[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "One-shot scheduled resume script writes operator logs.")]
param()

$ErrorActionPreference = "Stop"

$Root = "C:\Users\thaba\OneDrive\Documents\The 808 Vision 2026\phoenixguard"
$BurnDir = "C:\Users\thaba\OneDrive\Documents\The 808 Vision 2026\phoenixguard\reports\eight_hour_burn_20260622_233105"
$BaseUrl = "http://127.0.0.1:8793"
$SessionId = "pocket-live-8788"
$ResumeAt = [DateTime]"2026-06-23T05:00:00"
$CheckpointPath = Join-Path $BurnDir "pause_resume\pause_checkpoint.json"
$EntryDir = Join-Path $BurnDir "entry_allowance_burn"
$LogDir = Join-Path $BurnDir "agent_logs"
$ResumeDir = Join-Path $BurnDir "pause_resume"

New-Item -ItemType Directory -Force -Path $LogDir, $ResumeDir | Out-Null
Set-Location $Root

function Add-JsonLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    ($Payload | ConvertTo-Json -Depth 12 -Compress) | Add-Content -LiteralPath $Path -Encoding UTF8
}

function Quote-Arg {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Start-HiddenProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    $stdout = Join-Path $LogDir "$Name.stdout.log"
    $stderr = Join-Path $LogDir "$Name.stderr.log"
    $argLine = ($Arguments | ForEach-Object { Quote-Arg -Value $_ }) -join " "
    $process = Start-Process -FilePath $FilePath -ArgumentList $argLine -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    return [ordered]@{
        name = $Name
        pid = $process.Id
        file = $FilePath
        arguments = $Arguments
        stdout = $stdout
        stderr = $stderr
    }
}

function Get-CommandLineProcess {
    param([Parameter(Mandatory = $true)][string]$Needle)
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -like "*$Needle*"
    })
}

function Wait-TrackerReady {
    param([int]$TimeoutSec = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastError = ""
    while ((Get-Date) -lt $deadline) {
        try {
            $payload = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/live/state/v3/$SessionId`?compact=1" -TimeoutSec 10
            if ($payload -and ($payload.status -eq "running" -or $payload.tracking_enabled)) {
                return [ordered]@{ ok = $true; payload = $payload; error = "" }
            }
            $lastError = "tracker endpoint returned non-running status"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 5
    }
    return [ordered]@{ ok = $false; payload = $null; error = $lastError }
}

$eventLog = Join-Path $ResumeDir "resume_events.jsonl"
$checkpoint = Get-Content -Raw -LiteralPath $CheckpointPath | ConvertFrom-Json
$remainingSec = [Math]::Max(60.0, [double]$checkpoint.remaining_sec_at_pause)
$remainingText = [TimeSpan]::FromSeconds($remainingSec)

$env:PHOENIXGUARD_LIVE_EXECUTION_ENABLED = "0"
$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS = "0"
$env:PHOENIXGUARD_TRACKER_SESSION_ID = $SessionId
$env:PHOENIXGUARD_BURN_CAPTURE_BLOCKED_ENTER_NOW = "1"

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python.exe"
}
$powershell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

Add-JsonLine -Path $eventLog -Payload ([ordered]@{
    at = (Get-Date).ToString("o")
    event = "resume_started"
    scheduled_for = $ResumeAt.ToString("o")
    remaining_sec = $remainingSec
    remaining_text = "{0:00}:{1:00}:{2:00}" -f [int][Math]::Floor($remainingText.TotalHours), $remainingText.Minutes, $remainingText.Seconds
})

$trackerReady = Wait-TrackerReady -TimeoutSec 60
if (-not $trackerReady.ok) {
    $failure = [ordered]@{
        at = (Get-Date).ToString("o")
        event = "resume_failed_tracker_not_ready"
        error = $trackerReady.error
    }
    Add-JsonLine -Path $eventLog -Payload $failure
    $failure | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ResumeDir "resume_failed.json") -Encoding UTF8
    exit 2
}

$started = @()

if (-not (Get-CommandLineProcess -Needle "phoenixguard_mt4_file_bridge.py")) {
    $started += Start-HiddenProcess -Name "resume_mt4_file_bridge" -FilePath $python -Arguments @(
        "tools\phoenixguard_mt4_file_bridge.py",
        "--base-url", $BaseUrl,
        "--session-id", $SessionId,
        "--poll-sec", "0.25",
        "--timeout-sec", "15",
        "--metrics-every", "1.0",
        "--print-every", "30"
    )
}

$started += Start-HiddenProcess -Name "resume_entry_allowance_burn" -FilePath $python -Arguments @(
    "tools\run_entry_allowance_burn.py",
    "--base-url", $BaseUrl,
    "--session-id", $SessionId,
    "--duration-sec", ([string][Math]::Round($remainingSec, 3)),
    "--interval-sec", "5",
    "--timeout-sec", "45",
    "--raw-every-sec", "60",
    "--storage-guard-interval-sec", "60",
    "--out-dir", $EntryDir,
    "--keep-existing"
)

$started += Start-HiddenProcess -Name "resume_stack_health" -FilePath $python -Arguments @(
    "tools\watch_stack_health.py",
    "--base-url", $BaseUrl,
    "--session-id", $SessionId,
    "--out-dir", (Join-Path $BurnDir "stack_health"),
    "--duration-sec", ([string][Math]::Round($remainingSec, 3)),
    "--poll-sec", "1",
    "--timeout-sec", "20",
    "--print-every", "60"
)

$started += Start-HiddenProcess -Name "resume_mt4_bridge_health" -FilePath $python -Arguments @(
    "tools\watch_mt4_bridge_health.py",
    "--out-dir", (Join-Path $BurnDir "mt4_bridge_health"),
    "--duration-sec", ([string][Math]::Round($remainingSec, 3)),
    "--poll-sec", "0.5",
    "--print-every", "60"
)

$started += Start-HiddenProcess -Name "resume_mt4_execution_confirmation" -FilePath $python -Arguments @(
    "tools\watch_mt4_execution_confirmation.py",
    "--out-dir", (Join-Path $BurnDir "mt4_execution_confirmation"),
    "--duration-sec", ([string][Math]::Round($remainingSec, 3)),
    "--poll-sec", "1",
    "--print-every", "60"
)

$started += Start-HiddenProcess -Name "resume_entry_allowed_alerts" -FilePath $powershell -Arguments @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $Root "tools\watch_entry_allowed_alerts.ps1"),
    "-StatusPath", (Join-Path $EntryDir "status.json"),
    "-EntryEventsPath", (Join-Path $EntryDir "entry_events.jsonl"),
    "-AlertLog", (Join-Path $BurnDir "alerts\entry_allowed_alerts.jsonl"),
    "-PollSeconds", "2",
    "-IgnoreExisting"
)

$started += Start-HiddenProcess -Name "resume_burn_status_notify" -FilePath $powershell -Arguments @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $Root "tools\watch_burn_status_notify.ps1"),
    "-BurnDir", $BurnDir,
    "-BaseUrl", $BaseUrl,
    "-SessionId", $SessionId,
    "-DurationSec", ([string][Math]::Ceiling($remainingSec)),
    "-PollSeconds", "60",
    "-NotifyEveryMinutes", "30",
    "-NotifyNow"
)

$statusPath = Join-Path $EntryDir "status.json"
if (Test-Path -LiteralPath $statusPath) {
    try {
        $status = Get-Content -Raw -LiteralPath $statusPath | ConvertFrom-Json
        $status | Add-Member -NotePropertyName paused -NotePropertyValue $false -Force
        $status | Add-Member -NotePropertyName resumed_at -NotePropertyValue (Get-Date).ToString("o") -Force
        $status | Add-Member -NotePropertyName resume_remaining_sec -NotePropertyValue $remainingSec -Force
        $status | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $statusPath -Encoding UTF8
    } catch {
        Add-JsonLine -Path $eventLog -Payload ([ordered]@{ at = (Get-Date).ToString("o"); event = "status_resume_stamp_failed"; error = $_.Exception.Message })
    }
}

$result = [ordered]@{
    at = (Get-Date).ToString("o")
    event = "resume_agents_started"
    burn_dir = $BurnDir
    remaining_sec = $remainingSec
    started = $started
}
Add-JsonLine -Path $eventLog -Payload $result
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $ResumeDir "resume_started.json") -Encoding UTF8
