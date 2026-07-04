[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Background operator watcher writes concise status lines to stdout logs.")]
param(
    [Parameter(Mandatory = $true)]
    [string]$BurnDir,
    [string]$BaseUrl = "http://127.0.0.1:8793",
    [string]$SessionId = "pocket-live-8788",
    [int]$DurationSec = 28800,
    [int]$PollSeconds = 60,
    [int]$NotifyEveryMinutes = 30,
    [switch]$NotifyNow
)

$ErrorActionPreference = "Continue"

function Write-JsonFile {
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
    $json = $Payload | ConvertTo-Json -Depth 12
    $tmp = "$Path.$PID.$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()).tmp"
    $json | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

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

function Get-JsonEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [int]$TimeoutSec = 10
    )
    try {
        $payload = Invoke-RestMethod -Uri $Uri -TimeoutSec $TimeoutSec
        return [ordered]@{ ok = $true; payload = $payload; error = "" }
    } catch {
        return [ordered]@{ ok = $false; payload = $null; error = $_.Exception.Message }
    }
}

function Format-Duration {
    param([double]$Seconds)
    if ($Seconds -lt 0) {
        $Seconds = 0
    }
    $span = [TimeSpan]::FromSeconds($Seconds)
    if ($span.TotalHours -ge 1) {
        return "{0:00}h {1:00}m {2:00}s" -f [int][Math]::Floor($span.TotalHours), $span.Minutes, $span.Seconds
    }
    return "{0:00}m {1:00}s" -f $span.Minutes, $span.Seconds
}

function Send-BurnNotification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [Parameter(Mandatory = $true)]
        [string]$OutDir
    )

    $event = [ordered]@{
        at = (Get-Date).ToString("o")
        title = $Title
        message = $Message
        method = ""
        success = $false
        error = ""
    }

    try {
        if (Get-Command -Name New-BurntToastNotification -ErrorAction SilentlyContinue) {
            New-BurntToastNotification -Text $Title, $Message | Out-Null
            $event.method = "BurntToast"
            $event.success = $true
            Add-JsonLine -Path (Join-Path $OutDir "notification_events.jsonl") -Payload $event
            return
        }
    } catch {
        $event.error = $_.Exception.Message
    }

    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.Visible = $true
        $notify.BalloonTipTitle = $Title
        $notify.BalloonTipText = $Message
        $notify.ShowBalloonTip(10000)
        Start-Sleep -Seconds 11
        $notify.Dispose()
        $event.method = "NotifyIcon"
        $event.success = $true
    } catch {
        $event.error = $_.Exception.Message
        try {
            $safeMessage = ($Title + " - " + $Message).Replace("`r", " ").Replace("`n", " ")
            msg $env:USERNAME $safeMessage 2>$null
            $event.method = "msg"
            $event.success = $true
        } catch {
            if ($event.error) {
                $event.error = "$($event.error); $($_.Exception.Message)"
            } else {
                $event.error = $_.Exception.Message
            }
        }
    }

    Add-JsonLine -Path (Join-Path $OutDir "notification_events.jsonl") -Payload $event
}

function Get-BurnSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BurnDir,
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [Parameter(Mandatory = $true)]
        [string]$SessionId
    )

    $statusPath = Join-Path $BurnDir "entry_allowance_burn\status.json"
    if (-not (Test-Path -LiteralPath $statusPath)) {
        $rootStatusPath = Join-Path $BurnDir "status.json"
        if (Test-Path -LiteralPath $rootStatusPath) {
            $statusPath = $rootStatusPath
        }
    }
    $status = $null
    if (Test-Path -LiteralPath $statusPath) {
        try {
            $status = Get-Content -Raw -LiteralPath $statusPath | ConvertFrom-Json
        } catch {
            $status = $null
        }
    }

    $sessionResult = Get-JsonEndpoint -Uri "$BaseUrl/v1/mobile/live/state/v3/$SessionId`?compact=1&monitor=1" -TimeoutSec 10
    $perfResult = Get-JsonEndpoint -Uri "$BaseUrl/v1/mobile/performance/trace/v3/$SessionId" -TimeoutSec 20

    $mt4Dir = Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\Files\PhoenixGuard"
    $mt4Path = Join-Path $mt4Dir "mt4_bridge_status.json"
    $mt4 = $null
    if (Test-Path -LiteralPath $mt4Path) {
        try {
            $mt4 = Get-Content -Raw -LiteralPath $mt4Path | ConvertFrom-Json
        } catch {
            $mt4 = $null
        }
    }

    $patterns = @(
        "start_phoenixguard_24_7_tracker.py",
        "start_phoenixguard_mobile_api.py",
        "phoenixguard_mt4_file_bridge.py",
        "watch_stack_health.py",
        "watch_mt4_bridge_health.py",
        "watch_mt4_execution_confirmation.py",
        "run_entry_allowance_burn.py",
        "watch_entry_allowed_alerts.ps1",
        "watch_burn_status_notify.ps1"
    )
    $processes = @(Get-CimInstance Win32_Process | Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) {
            return $false
        }
        foreach ($pattern in $patterns) {
            if ($cmd -like "*$pattern*") {
                return $true
            }
        }
        return $false
    } | Select-Object ProcessId, ParentProcessId, Name, CommandLine)

    $stderrFiles = @()
    $logDir = Join-Path $BurnDir "agent_logs"
    if (Test-Path -LiteralPath $logDir) {
        $stderrFiles = @(Get-ChildItem -LiteralPath $logDir -Filter "*.stderr.log" -ErrorAction SilentlyContinue | ForEach-Object {
            [ordered]@{ name = $_.Name; size = $_.Length }
        })
    }

    $session = $sessionResult.payload
    $perf = $perfResult.payload
    return [ordered]@{
        at = (Get-Date).ToString("o")
        burn_dir = $BurnDir
        burn_running = if ($status) { $status.running } else { $false }
        sample_count = if ($status) { $status.sample_count } else { $null }
        elapsed_sec = if ($status) { $status.elapsed_sec } else { $null }
        remaining_sec = if ($status) { $status.remaining_sec } else { $null }
        entry_event_count = if ($status) { $status.entry_event_count } else { $null }
        entry_allowed_observation_count = if ($status) { $status.entry_allowed_observation_count } else { $null }
        last_blocked_by = if ($status -and $status.last_entry) { $status.last_entry.blocked_by } else { $null }
        tracker = [ordered]@{
            ok = $sessionResult.ok
            error = $sessionResult.error
            status = if ($session) { $session.status } else { $null }
            capture_count = if ($session) { $session.capture_count } else { $null }
            tracking_enabled = if ($session) { $session.tracking_enabled } else { $null }
            live_execution_enabled = if ($session -and $session.execution_controls) { $session.execution_controls.live_execution_enabled } elseif ($session) { $session.live_execution_enabled } else { $null }
            execution_mode = if ($session -and $session.execution_controls) { $session.execution_controls.execution_mode } elseif ($session) { $session.execution_mode } else { $null }
            last_capture_at = if ($session) { $session.last_capture_at } else { $null }
        }
        performance = [ordered]@{
            ok = $perfResult.ok
            error = $perfResult.error
            frame_age_ms = if ($perf -and $perf.timing_trace) { $perf.timing_trace.frame_age_ms } else { $null }
            stale_status = if ($perf -and $perf.timing_trace) { $perf.timing_trace.stale_status } else { $null }
            models_awake = if ($perf -and $perf.model_performance) { $perf.model_performance.models_awake } else { $null }
            models_total = if ($perf -and $perf.model_performance) { $perf.model_performance.models_total } else { $null }
            queue_depth = if ($perf -and $perf.model_performance) { $perf.model_performance.queue_depth } else { $null }
        }
        mt4 = [ordered]@{
            exists = [bool]$mt4
            bridge_status = if ($mt4) { $mt4.bridge_status } else { $null }
            bridge_sequence = if ($mt4) { $mt4.bridge_sequence } else { $null }
            heartbeat_alive = if ($mt4 -and $mt4.heartbeat) { $mt4.heartbeat.alive } else { $null }
            error = if ($mt4) { $mt4.error } else { $null }
            detail = if ($mt4) { $mt4.detail } else { $null }
        }
        process_count = $processes.Count
        processes = $processes
        stderr_files = $stderrFiles
        nonzero_stderr_count = @($stderrFiles | Where-Object { $_.size -gt 0 }).Count
    }
}

$outDir = Join-Path $BurnDir "operator_updates"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$updatesPath = Join-Path $outDir "status_updates.jsonl"
$latestPath = Join-Path $outDir "latest_status.json"

$started = Get-Date
$deadline = $started.AddSeconds($DurationSec)
$notifyEvery = [TimeSpan]::FromMinutes([Math]::Max(1, $NotifyEveryMinutes))
$nextNotify = if ($NotifyNow) { $started } else { $started.Add($notifyEvery) }

while ((Get-Date) -lt $deadline) {
    $snapshot = Get-BurnSnapshot -BurnDir $BurnDir -BaseUrl $BaseUrl -SessionId $SessionId
    Write-JsonFile -Path $latestPath -Payload $snapshot
    Add-JsonLine -Path $updatesPath -Payload $snapshot

    $now = Get-Date
    if ($now -ge $nextNotify) {
        $remainingText = if ($null -ne $snapshot.remaining_sec) { Format-Duration -Seconds ([double]$snapshot.remaining_sec) } else { "unknown" }
        $title = "PhoenixGuard 8h burn update"
        $message = "running=$($snapshot.burn_running) samples=$($snapshot.sample_count) remaining=$remainingText tracker=$($snapshot.tracker.status) frame=$($snapshot.performance.frame_age_ms)ms mt4=$($snapshot.mt4.bridge_status) stderr_nonzero=$($snapshot.nonzero_stderr_count)"
        Send-BurnNotification -Title $title -Message $message -OutDir $outDir
        $nextNotify = $now.Add($notifyEvery)
    }

    $sleepFor = [Math]::Max(5, $PollSeconds)
    Start-Sleep -Seconds $sleepFor
}

$final = Get-BurnSnapshot -BurnDir $BurnDir -BaseUrl $BaseUrl -SessionId $SessionId
$final.completed_watcher_at = (Get-Date).ToString("o")
Write-JsonFile -Path (Join-Path $outDir "final_status.json") -Payload $final
Add-JsonLine -Path $updatesPath -Payload $final
Send-BurnNotification -Title "PhoenixGuard 8h burn complete" -Message "samples=$($final.sample_count) tracker=$($final.tracker.status) mt4=$($final.mt4.bridge_status) stderr_nonzero=$($final.nonzero_stderr_count)" -OutDir $outDir
