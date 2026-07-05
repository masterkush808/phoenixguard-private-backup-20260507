[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Interactive burn orchestrator prints concise operator status.")]
param(
    [string]$ProjectRoot = $(if ($env:PHOENIXGUARD_PROJECT_ROOT) { $env:PHOENIXGUARD_PROJECT_ROOT } else { (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path }),
    [string]$BaseUrl = $(if ($env:PHOENIXGUARD_BURN_BASE_URL) { $env:PHOENIXGUARD_BURN_BASE_URL } else { "http://127.0.0.1:8793" }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { "pocket-live-8788" }),
    [string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { "The Most Innovative Trading Platform" }),
    [int]$BrokerWindowHwnd = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_HWND) { [int]$env:PHOENIXGUARD_BROKER_WINDOW_HWND } else { 0 }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 15.0 }),
    [double]$ShooterPollSec = $(if ($env:PHOENIXGUARD_SHOOTER_POLL_SEC) { [double]$env:PHOENIXGUARD_SHOOTER_POLL_SEC } else { 15.0 }),
    [int]$WarmupSeconds = 20,
    [int]$DurationSec = $(if ($env:PHOENIXGUARD_BURN_DURATION_SEC) { [int]$env:PHOENIXGUARD_BURN_DURATION_SEC } else { 28800 }),
    [double]$BurnIntervalSec = $(if ($env:PHOENIXGUARD_BURN_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_BURN_INTERVAL_SEC } else { 5.0 }),
    [double]$BurnTimeoutSec = $(if ($env:PHOENIXGUARD_BURN_TIMEOUT_SEC) { [double]$env:PHOENIXGUARD_BURN_TIMEOUT_SEC } else { 45.0 }),
    [double]$RawEverySec = $(if ($env:PHOENIXGUARD_BURN_RAW_EVERY_SEC) { [double]$env:PHOENIXGUARD_BURN_RAW_EVERY_SEC } else { 60.0 }),
    [double]$StorageGuardIntervalSec = $(if ($env:PHOENIXGUARD_BURN_STORAGE_GUARD_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_BURN_STORAGE_GUARD_INTERVAL_SEC } else { 60.0 }),
    [double]$PeriodicScreenshotSec = $(if ($env:PHOENIXGUARD_BURN_PERIODIC_SCREENSHOT_SEC) { [double]$env:PHOENIXGUARD_BURN_PERIODIC_SCREENSHOT_SEC } else { 600.0 }),
    [double]$EntryEvidenceMinSec = $(if ($env:PHOENIXGUARD_BURN_ENTRY_EVIDENCE_MIN_SEC) { [double]$env:PHOENIXGUARD_BURN_ENTRY_EVIDENCE_MIN_SEC } else { 60.0 }),
    [int]$ApiHealthTimeoutSec = 240,
    [int]$FreshLiveStateTimeoutSec = 300,
    [double]$FreshLiveStateMaxAgeMs = 45000.0,
    [int]$MonitorPollSeconds = 30,
    [int]$NotifyEveryMinutes = 30,
    [string]$OutRoot = "",
    [switch]$OpenBrowser,
    [switch]$DisableShooter,
    [switch]$UseExistingStack,
    [switch]$NoMonitor,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"

function Get-TextValue {
    param($Value, [string]$Default = "")
    if ($null -eq $Value) {
        return $Default
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $Default
    }
    return $text.Trim()
}

function Get-ObjectProperty {
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object -or [string]::IsNullOrWhiteSpace($Name)) {
        return $Default
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $Default
    }
    return $property.Value
}

function Get-NestedValue {
    param($Object, [string[]]$Path, $Default = $null)
    $current = $Object
    foreach ($name in $Path) {
        $current = Get-ObjectProperty -Object $current -Name $name -Default $null
        if ($null -eq $current) {
            return $Default
        }
    }
    return $current
}

function Get-DoubleValue {
    param($Value, [double]$Default = [double]::NaN)
    if ($null -eq $Value) {
        return $Default
    }
    try {
        $number = [double]$Value
        if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
            return $Default
        }
        return $number
    } catch {
        return $Default
    }
}

function Get-IntValue {
    param($Value, [int]$Default = 0)
    $number = Get-DoubleValue -Value $Value -Default ([double]$Default)
    return [int][Math]::Round($number)
}

function Get-BoolValue {
    param($Value)
    if ($Value -is [bool]) {
        return [bool]$Value
    }
    $text = (Get-TextValue -Value $Value).ToLowerInvariant()
    return @("1", "true", "yes", "on", "running") -contains $text
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $json = $Payload | ConvertTo-Json -Depth 30
    $tmp = "$Path.$PID.$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()).tmp"
    $json | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Add-JsonLine {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    ($Payload | ConvertTo-Json -Depth 30 -Compress) | Add-Content -LiteralPath $Path -Encoding UTF8
}

function ConvertTo-ProcessArgumentString {
    param([string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        $argument = [string]$_
        if ($argument.Length -eq 0) {
            '""'
        } elseif ($argument -match '[\s"]') {
            '"' + $argument.Replace('"', '\"') + '"'
        } else {
            $argument
        }
    }) -join ' ')
}

function Get-JsonEndpoint {
    param([string]$Uri, [int]$TimeoutSec = 10)
    try {
        $payload = Invoke-RestMethod -Uri $Uri -TimeoutSec $TimeoutSec
        return [ordered]@{ ok = $true; uri = $Uri; payload = $payload; error = "" }
    } catch {
        return [ordered]@{ ok = $false; uri = $Uri; payload = $null; error = $_.Exception.Message }
    }
}

function Get-LiveFreshnessSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$SessionId,
        [Parameter(Mandatory = $true)][double]$MaxFrameAgeMs
    )
    $base = $BaseUrl.TrimEnd("/")
    $sessionQuery = [Uri]::EscapeDataString($SessionId)
    $healthResult = Get-JsonEndpoint -Uri "$base/v1/mobile/health" -TimeoutSec 10
    $liveResult = Get-JsonEndpoint -Uri "$base/v1/mobile/live/state/v3/$sessionQuery`?compact=1&monitor=1" -TimeoutSec 20
    $perfResult = Get-JsonEndpoint -Uri "$base/v1/mobile/performance/trace/v3/$sessionQuery" -TimeoutSec 20
    $live = $liveResult.payload
    $perf = $perfResult.payload
    $healthStatus = Get-TextValue -Value (Get-ObjectProperty -Object $healthResult.payload -Name "status")
    $liveStatus = Get-TextValue -Value (Get-ObjectProperty -Object $live -Name "status")
    $trackingEnabled = Get-BoolValue (Get-ObjectProperty -Object $live -Name "tracking_enabled")
    $captureCount = Get-IntValue -Value (Get-ObjectProperty -Object $live -Name "capture_count") -Default 0
    if ($captureCount -le 0) {
        $captureCount = Get-IntValue -Value (Get-NestedValue -Object $live -Path @("latest", "capture_count")) -Default 0
    }
    if ($captureCount -le 0) {
        $captureCount = Get-IntValue -Value (Get-ObjectProperty -Object $perf -Name "capture_count") -Default 0
    }
    if ($captureCount -le 0) {
        $captureCount = Get-IntValue -Value (Get-NestedValue -Object $perf -Path @("timing_trace", "capture_count")) -Default 0
    }
    $frameId = Get-IntValue -Value (Get-ObjectProperty -Object $live -Name "chart_frame_id") -Default 0
    if ($frameId -le 0) {
        $frameId = Get-IntValue -Value (Get-ObjectProperty -Object $perf -Name "frame_id") -Default 0
    }
    if ($frameId -le 0) {
        $frameId = Get-IntValue -Value (Get-NestedValue -Object $perf -Path @("timing_trace", "frame_id")) -Default 0
    }
    $frameAgeMs = Get-DoubleValue -Value (Get-NestedValue -Object $perf -Path @("timing_trace", "frame_age_ms")) -Default ([double]::NaN)
    if ([double]::IsNaN($frameAgeMs)) {
        $frameAgeMs = Get-DoubleValue -Value (Get-ObjectProperty -Object $perf -Name "frame_age_ms") -Default ([double]::NaN)
    }
    if ([double]::IsNaN($frameAgeMs)) {
        $frameAgeMs = Get-DoubleValue -Value (Get-NestedValue -Object $live -Path @("visual_health_v3", "frame_age_ms")) -Default ([double]::NaN)
    }
    if ([double]::IsNaN($frameAgeMs)) {
        $displayPublishedMs = Get-DoubleValue -Value (Get-NestedValue -Object $perf -Path @("timing_trace", "display_published_epoch_ms")) -Default ([double]::NaN)
        if (-not [double]::IsNaN($displayPublishedMs) -and $displayPublishedMs -gt 0) {
            $frameAgeMs = [Math]::Max(0.0, ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $displayPublishedMs))
        }
    }
    if ([double]::IsNaN($frameAgeMs)) {
        $lastCaptureEpoch = Get-DoubleValue -Value (Get-ObjectProperty -Object $live -Name "last_capture_epoch") -Default ([double]::NaN)
        if (-not [double]::IsNaN($lastCaptureEpoch) -and $lastCaptureEpoch -gt 0) {
            $frameAgeMs = [Math]::Max(0.0, ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - ($lastCaptureEpoch * 1000.0)))
        }
    }
    $staleStatus = Get-TextValue -Value (Get-NestedValue -Object $perf -Path @("timing_trace", "stale_status"))
    if (-not $staleStatus) {
        $staleStatus = Get-TextValue -Value (Get-ObjectProperty -Object $perf -Name "stale_status")
    }
    if (-not $staleStatus) {
        $staleStatus = Get-TextValue -Value (Get-NestedValue -Object $live -Path @("visual_health_v3", "status"))
    }
    $liveReady = [bool]($liveResult.ok -and (($liveStatus.ToUpperInvariant() -eq "RUNNING") -or $trackingEnabled -or $captureCount -gt 0 -or $frameId -gt 0))
    $performanceReady = [bool]($perfResult.ok -and $frameId -gt 0)
    $staleBlocked = @("STALE", "FAIL", "FROZEN", "REJECT") -contains $staleStatus.ToUpperInvariant()
    $ageReady = (-not [double]::IsNaN($frameAgeMs)) -and $frameAgeMs -le $MaxFrameAgeMs
    $ready = [bool]($healthResult.ok -and $healthStatus -eq "ok" -and ($liveReady -or $performanceReady) -and $ageReady -and -not $staleBlocked)
    $reason = if ($ready) {
        if ($liveReady) { "fresh_live_state" } else { "fresh_performance_trace" }
    } elseif (-not $healthResult.ok -or $healthStatus -ne "ok") {
        "health_not_ok"
    } elseif (-not ($liveReady -or $performanceReady)) {
        "live_or_performance_state_not_running"
    } elseif (-not $ageReady) {
        "frame_age_ms=$frameAgeMs"
    } elseif ($staleBlocked) {
        "stale_status=$staleStatus"
    } else {
        "unknown"
    }
    return [ordered]@{
        ready = $ready
        reason = $reason
        health_ok = [bool]$healthResult.ok
        health_status = $healthStatus
        live_ok = [bool]$liveResult.ok
        performance_ok = [bool]$perfResult.ok
        live_status = $liveStatus
        tracking_enabled = $trackingEnabled
        capture_count = $captureCount
        frame_id = $frameId
        frame_age_ms = if ([double]::IsNaN($frameAgeMs)) { $null } else { [Math]::Round($frameAgeMs, 3) }
        stale_status = $staleStatus
        health_error = $healthResult.error
        live_error = $liveResult.error
        performance_error = $perfResult.error
    }
}

function Wait-ApiHealth {
    param([string]$BaseUrl, [int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $base = $BaseUrl.TrimEnd("/")
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $result = Get-JsonEndpoint -Uri "$base/v1/mobile/health" -TimeoutSec 10
        $status = Get-TextValue -Value (Get-ObjectProperty -Object $result.payload -Name "status")
        $last = [ordered]@{ ok = [bool]($result.ok -and $status -eq "ok"); status = $status; error = $result.error }
        if ($last.ok) {
            return $last
        }
        Start-Sleep -Seconds 2
    }
    if ($null -eq $last) {
        $last = [ordered]@{ ok = $false; status = ""; error = "timeout" }
    }
    return $last
}

function Wait-FreshLiveState {
    param([string]$BaseUrl, [string]$SessionId, [int]$TimeoutSec, [double]$MaxFrameAgeMs)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Get-LiveFreshnessSnapshot -BaseUrl $BaseUrl -SessionId $SessionId -MaxFrameAgeMs $MaxFrameAgeMs
        if ($last.ready) {
            return $last
        }
        Start-Sleep -Seconds 3
    }
    if ($null -eq $last) {
        $last = [ordered]@{ ready = $false; reason = "timeout" }
    }
    return $last
}

function Test-InteractiveScreenCapture {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )
    $probe = @'
from PIL import ImageGrab
try:
    image = ImageGrab.grab(bbox=(0, 0, 8, 8), all_screens=True)
    print(f"SCREEN_CAPTURE_OK {image.size[0]}x{image.size[1]}")
except Exception as exc:
    print(f"SCREEN_CAPTURE_FAIL {type(exc).__name__}: {exc}")
    raise SystemExit(7)
'@
    $completed = $probe | & $PythonPath - 2>&1
    $text = ($completed | Out-String).Trim()
    return [ordered]@{
        ok = [bool]($LASTEXITCODE -eq 0)
        exit_code = [int]$LASTEXITCODE
        output = $text
        project_root = $ProjectRoot
    }
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Wait-BurnRunning {
    param([string]$StatusPath, [int]$TimeoutSec = 180)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Read-JsonFile -Path $StatusPath
        if ($last -and [bool](Get-ObjectProperty -Object $last -Name "running")) {
            return [ordered]@{ ok = $true; status = $last; error = "" }
        }
        Start-Sleep -Seconds 2
    }
    return [ordered]@{ ok = $false; status = $last; error = "burn status did not report running before timeout" }
}

function Start-OrchestratorProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$LogDir,
        [ValidateSet("Hidden", "Normal", "Minimized")][string]$WindowStyle = "Hidden"
    )
    $stdout = Join-Path $LogDir "$Name.stdout.log"
    $stderr = Join-Path $LogDir "$Name.stderr.log"
    $argumentString = ConvertTo-ProcessArgumentString -Arguments $Arguments
    $process = Start-Process -FilePath $FilePath -ArgumentList $argumentString -WorkingDirectory $WorkingDirectory -WindowStyle $WindowStyle -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    return [ordered]@{
        name = $Name
        pid = $process.Id
        file = $FilePath
        arguments = $Arguments
        stdout = $stdout
        stderr = $stderr
        window_style = $WindowStyle
        started_at = (Get-Date).ToString("o")
    }
}

function Get-ProcessSnapshot {
    param([object[]]$Children)
    return @($Children | ForEach-Object {
        $pidValue = [int]$_.pid
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        [ordered]@{
            name = $_.name
            pid = $pidValue
            running = [bool]($null -ne $process)
            file = $_.file
            stdout = $_.stdout
            stderr = $_.stderr
            window_style = $_.window_style
        }
    })
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
Set-Location $ProjectRoot

if (-not $OutRoot) {
    $OutRoot = Join-Path -Path $ProjectRoot -ChildPath ".codex_runtime\burn_orchestration"
}
$OutRoot = (New-Item -ItemType Directory -Force -Path $OutRoot).FullName
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path -Path $OutRoot -ChildPath "entry_allowance_orchestrator_$stamp"
$LogDir = Join-Path -Path $RunDir -ChildPath "agent_logs"
$EntryDir = Join-Path -Path $RunDir -ChildPath "entry_allowance_burn"
$AlertsDir = Join-Path -Path $RunDir -ChildPath "alerts"
$StatusPath = Join-Path -Path $RunDir -ChildPath "orchestrator_status.json"
$EventsPath = Join-Path -Path $RunDir -ChildPath "orchestrator_events.jsonl"
$ManifestPath = Join-Path -Path $RunDir -ChildPath "launch_manifest.json"
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir, $EntryDir, $AlertsDir | Out-Null

$launcherPath = Join-Path -Path $ProjectRoot -ChildPath "Backend\launch\launch_phoenixguard_live_ready.ps1"
$resolvePythonPath = Join-Path -Path $ProjectRoot -ChildPath "Backend\launch\Resolve-PhoenixGuardPython.ps1"
$burnScript = Join-Path -Path $ProjectRoot -ChildPath "Backend\tools\run_entry_allowance_burn.py"
$stackHealthScript = Join-Path -Path $ProjectRoot -ChildPath "Backend\tools\watch_stack_health.py"
$mt4BridgeHealthScript = Join-Path -Path $ProjectRoot -ChildPath "Backend\tools\watch_mt4_bridge_health.py"
$mt4ExecutionScript = Join-Path -Path $ProjectRoot -ChildPath "Backend\tools\watch_mt4_execution_confirmation.py"
$tradeAlertScript = Join-Path -Path $ProjectRoot -ChildPath "Backend\tools\watch_trade_package_ack_alerts.ps1"
$statusNotifyScript = Join-Path -Path $ProjectRoot -ChildPath "Backend\tools\watch_burn_status_notify.ps1"

foreach ($requiredPath in @($launcherPath, $resolvePythonPath, $burnScript, $stackHealthScript, $mt4BridgeHealthScript, $mt4ExecutionScript, $tradeAlertScript, $statusNotifyScript)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required PhoenixGuard burn helper not found: $requiredPath"
    }
}

. $resolvePythonPath
$pythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $ProjectRoot
$python = [string]$pythonRuntime.VenvPython
$powershell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

$base = $BaseUrl.TrimEnd("/")
$entryEventsPath = Join-Path -Path $EntryDir -ChildPath "entry_events.jsonl"
$entryStatusPath = Join-Path -Path $EntryDir -ChildPath "status.json"
$mt4Dir = if ($env:APPDATA) { Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\Files\PhoenixGuard" } else { "" }
$mt4CommandPath = if ($mt4Dir) { Join-Path $mt4Dir "mt4_execution_command.json" } else { "" }
$mt4StatusPath = if ($mt4Dir) { Join-Path $mt4Dir "mt4_bridge_status.json" } else { "" }
$mt4AuditPath = if ($mt4Dir) { Join-Path $mt4Dir "mt4_executioner_audit.csv" } else { "" }
$children = @()
$transcriptStarted = $false

function Write-OrchestratorEvent {
    param([string]$Event, $Payload = $null)
    $record = [ordered]@{
        at = (Get-Date).ToString("o")
        event = $Event
        payload = $Payload
    }
    Add-JsonLine -Path $EventsPath -Payload $record
}

function Write-OrchestratorStatus {
    param([string]$Phase, $Extra = $null)
    $burnStatus = Read-JsonFile -Path $entryStatusPath
    $snapshot = [ordered]@{
        schema_version = "PG_ENTRY_ALLOWANCE_BURN_ORCHESTRATOR_V1"
        updated_at = (Get-Date).ToString("o")
        phase = $Phase
        project_root = $ProjectRoot
        run_dir = $RunDir
        base_url = $base
        session_id = $SessionId
        duration_sec = $DurationSec
        burn_dir = $EntryDir
        burn_status_path = $entryStatusPath
        entry_events_path = $entryEventsPath
        trade_alert_log = Join-Path $AlertsDir "trade_package_ack_alerts.jsonl"
        no_direct_cleanup = $true
        burn_cleanup_flags = [ordered]@{
            clear_existing = $false
            prune_hardening_studies = $false
            run_entry_allowance_argument = "--keep-existing"
        }
        burn_status = $burnStatus
        processes = Get-ProcessSnapshot -Children $children
        extra = $Extra
    }
    Write-JsonFile -Path $StatusPath -Payload $snapshot
    Add-JsonLine -Path (Join-Path $RunDir "orchestrator_status.jsonl") -Payload $snapshot
}

try {
    try {
        Start-Transcript -Path (Join-Path $RunDir "orchestrator_transcript.log") -Append | Out-Null
        $transcriptStarted = $true
    } catch {
        Write-Warning "Transcript was not started: $($_.Exception.Message)"
    }

    Write-OrchestratorEvent -Event "orchestrator_started" -Payload ([ordered]@{
        run_dir = $RunDir
        base_url = $base
        session_id = $SessionId
        duration_sec = $DurationSec
        plan_only = [bool]$PlanOnly
    })
    Write-OrchestratorStatus -Phase "starting"

    $launcherParams = @{
        SessionId = $SessionId
        BrokerWindowQuery = $BrokerWindowQuery
        CaptureIntervalSec = [double]$CaptureIntervalSec
        WarmupSeconds = [int]$WarmupSeconds
        ShooterPollSec = [double]$ShooterPollSec
    }
    if ($BrokerWindowHwnd -gt 0) {
        $launcherParams["BrokerWindowHwnd"] = [int]$BrokerWindowHwnd
    }
    if (-not $OpenBrowser) {
        $launcherParams["NoBrowser"] = $true
    }
    if ($DisableShooter) {
        $launcherParams["DisableShooter"] = $true
    }

    $launcherArgs = @(
        "-SessionId", $SessionId,
        "-BrokerWindowQuery", $BrokerWindowQuery,
        "-CaptureIntervalSec", ([string][double]$CaptureIntervalSec),
        "-WarmupSeconds", ([string][int]$WarmupSeconds),
        "-ShooterPollSec", ([string][double]$ShooterPollSec)
    )
    if ($BrokerWindowHwnd -gt 0) {
        $launcherArgs += @("-BrokerWindowHwnd", ([string][int]$BrokerWindowHwnd))
    }
    if (-not $OpenBrowser) {
        $launcherArgs += "-NoBrowser"
    }
    if ($DisableShooter) {
        $launcherArgs += "-DisableShooter"
    }

    $watcherDurationSec = [Math]::Max(60, $DurationSec + 300)
    $burnArgs = @(
        "Backend\tools\run_entry_allowance_burn.py",
        "--base-url", $base,
        "--session-id", $SessionId,
        "--duration-sec", ([string][int]$DurationSec),
        "--interval-sec", ([string][double]$BurnIntervalSec),
        "--timeout-sec", ([string][double]$BurnTimeoutSec),
        "--raw-every-sec", ([string][double]$RawEverySec),
        "--storage-guard-interval-sec", ([string][double]$StorageGuardIntervalSec),
        "--periodic-screenshot-sec", ([string][double]$PeriodicScreenshotSec),
        "--entry-evidence-min-sec", ([string][double]$EntryEvidenceMinSec),
        "--out-dir", $EntryDir,
        "--keep-existing",
        "--no-operator-alert"
    )
    $tradeAlertArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $tradeAlertScript,
        "-EntryEventsPath", $entryEventsPath,
        "-AlertLog", (Join-Path $AlertsDir "trade_package_ack_alerts.jsonl"),
        "-PollSeconds", "2",
        "-IgnoreExisting"
    )
    if ($mt4StatusPath) {
        $tradeAlertArgs += @("-Mt4StatusPath", $mt4StatusPath)
    }
    if ($mt4CommandPath) {
        $tradeAlertArgs += @("-Mt4CommandPath", $mt4CommandPath)
    }
    if ($mt4AuditPath) {
        $tradeAlertArgs += @("-Mt4AuditPath", $mt4AuditPath)
    }

    $manifest = [ordered]@{
        schema_version = "PG_ENTRY_ALLOWANCE_BURN_ORCHESTRATOR_MANIFEST_V1"
        created_at = (Get-Date).ToString("o")
        project_root = $ProjectRoot
        run_dir = $RunDir
        python = $python
        powershell = $powershell
        launcher = $launcherPath
        launcher_args = $launcherArgs
        burn_script = $burnScript
        burn_args = $burnArgs
        trade_alert_script = $tradeAlertScript
        trade_alert_args = $tradeAlertArgs
        screen_capture_preflight_required = $true
        no_direct_cleanup = $true
        destructive_cleanup_notes = "This orchestrator does not remove files or stop processes directly. It delegates stack preparation only to the canonical launcher and starts the burn with --keep-existing plus PHOENIXGUARD_BURN_PRUNE_HARDENING_STUDIES=0."
    }
    Write-JsonFile -Path $ManifestPath -Payload $manifest

    if ($PlanOnly) {
        Write-OrchestratorEvent -Event "plan_only_complete" -Payload $manifest
        Write-OrchestratorStatus -Phase "plan_only" -Extra $manifest
        Write-Host "Plan only. Manifest: $ManifestPath"
        exit 0
    }

    Write-Host "PhoenixGuard burn orchestrator"
    Write-Host "  Run dir: $RunDir"
    Write-Host "  Launcher: Backend\launch\launch_phoenixguard_live_ready.ps1"
    Write-Host "  Burn dir: $EntryDir"
    Write-Host "  No direct cleanup: true"

    Write-Host "Checking interactive screen-capture access"
    $screenPreflight = Test-InteractiveScreenCapture -PythonPath $python -ProjectRoot $ProjectRoot
    Write-OrchestratorEvent -Event "screen_capture_preflight_complete" -Payload $screenPreflight
    if (-not [bool]$screenPreflight.ok) {
        Write-OrchestratorStatus -Phase "failed_screen_capture_preflight" -Extra $screenPreflight
        throw "Interactive screen capture is unavailable in this process. PhoenixGuard cannot publish fresh broker frames from this context. Details=$($screenPreflight.output)"
    }

    if ($UseExistingStack) {
        Write-OrchestratorEvent -Event "canonical_launcher_skipped_existing_stack" -Payload ([ordered]@{ base_url = $base; session_id = $SessionId })
        Write-OrchestratorStatus -Phase "existing_stack"
    } else {
        Write-OrchestratorEvent -Event "canonical_launcher_starting" -Payload ([ordered]@{ launcher = $launcherPath; args = $launcherArgs })
        Write-OrchestratorStatus -Phase "canonical_launcher"
        & $launcherPath @launcherParams
        Write-OrchestratorEvent -Event "canonical_launcher_completed"
    }

    Write-Host ""
    Write-Host "Waiting for API health at $base"
    $apiHealth = Wait-ApiHealth -BaseUrl $base -TimeoutSec $ApiHealthTimeoutSec
    Write-OrchestratorEvent -Event "api_health_wait_complete" -Payload $apiHealth
    if (-not [bool]$apiHealth.ok) {
        Write-OrchestratorStatus -Phase "failed_api_health" -Extra $apiHealth
        throw "PhoenixGuard API did not become healthy at $base. Status=$($apiHealth.status) Error=$($apiHealth.error)"
    }

    Write-Host "Waiting for fresh live state for session $SessionId"
    $freshState = Wait-FreshLiveState -BaseUrl $base -SessionId $SessionId -TimeoutSec $FreshLiveStateTimeoutSec -MaxFrameAgeMs $FreshLiveStateMaxAgeMs
    Write-OrchestratorEvent -Event "fresh_live_state_wait_complete" -Payload $freshState
    if (-not [bool]$freshState.ready) {
        Write-OrchestratorStatus -Phase "failed_fresh_live_state" -Extra $freshState
        throw "Fresh live state was not proven for $SessionId. Reason=$($freshState.reason)"
    }

    $env:PHOENIXGUARD_BURN_CLEAR_EXISTING = "0"
    $env:PHOENIXGUARD_BURN_PRUNE_HARDENING_STUDIES = "0"
    $env:PHOENIXGUARD_BURN_CAPTURE_BLOCKED_ENTER_NOW = "1"
    $env:PHOENIXGUARD_BURN_OPERATOR_ALERT = "0"
    $env:PHOENIXGUARD_BURN_BASE_URL = $base
    $env:PHOENIXGUARD_BURN_SESSION_ID = $SessionId

    Write-OrchestratorEvent -Event "trade_alert_watcher_starting" -Payload ([ordered]@{ args = $tradeAlertArgs })
    $children += Start-OrchestratorProcess -Name "trade_package_ack_alerts" -FilePath $powershell -Arguments $tradeAlertArgs -WorkingDirectory $ProjectRoot -LogDir $LogDir -WindowStyle "Normal"
    Start-Sleep -Seconds 3

    Write-OrchestratorEvent -Event "burn_runner_starting" -Payload ([ordered]@{ args = $burnArgs })
    $children += Start-OrchestratorProcess -Name "entry_allowance_burn" -FilePath $python -Arguments $burnArgs -WorkingDirectory $ProjectRoot -LogDir $LogDir -WindowStyle "Hidden"

    $children += Start-OrchestratorProcess -Name "stack_health" -FilePath $python -Arguments @(
        "Backend\tools\watch_stack_health.py",
        "--base-url", $base,
        "--session-id", $SessionId,
        "--out-dir", (Join-Path $RunDir "stack_health"),
        "--duration-sec", ([string][int]$watcherDurationSec),
        "--poll-sec", "5",
        "--timeout-sec", "20",
        "--print-every", "60"
    ) -WorkingDirectory $ProjectRoot -LogDir $LogDir -WindowStyle "Hidden"

    $children += Start-OrchestratorProcess -Name "mt4_bridge_health" -FilePath $python -Arguments @(
        "Backend\tools\watch_mt4_bridge_health.py",
        "--out-dir", (Join-Path $RunDir "mt4_bridge_health"),
        "--duration-sec", ([string][int]$watcherDurationSec),
        "--poll-sec", "1",
        "--stale-sec", "90",
        "--status-stale-sec", "90",
        "--print-every", "60"
    ) -WorkingDirectory $ProjectRoot -LogDir $LogDir -WindowStyle "Hidden"

    $children += Start-OrchestratorProcess -Name "mt4_execution_confirmation" -FilePath $python -Arguments @(
        "Backend\tools\watch_mt4_execution_confirmation.py",
        "--out-dir", (Join-Path $RunDir "mt4_execution_confirmation"),
        "--duration-sec", ([string][int]$watcherDurationSec),
        "--poll-sec", "1",
        "--print-every", "60"
    ) -WorkingDirectory $ProjectRoot -LogDir $LogDir -WindowStyle "Hidden"

    $children += Start-OrchestratorProcess -Name "burn_status_notify" -FilePath $powershell -Arguments @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $statusNotifyScript,
        "-BurnDir", $RunDir,
        "-BaseUrl", $base,
        "-SessionId", $SessionId,
        "-DurationSec", ([string][int]$watcherDurationSec),
        "-PollSeconds", "60",
        "-NotifyEveryMinutes", ([string][int]$NotifyEveryMinutes),
        "-NotifyNow"
    ) -WorkingDirectory $ProjectRoot -LogDir $LogDir -WindowStyle "Hidden"

    $manifest["children"] = $children
    Write-JsonFile -Path $ManifestPath -Payload $manifest
    Write-OrchestratorStatus -Phase "children_started" -Extra ([ordered]@{ fresh_state = $freshState; api_health = $apiHealth })

    $burnReady = Wait-BurnRunning -StatusPath $entryStatusPath -TimeoutSec 180
    Write-OrchestratorEvent -Event "burn_status_wait_complete" -Payload $burnReady
    if (-not [bool]$burnReady.ok) {
        Write-OrchestratorStatus -Phase "failed_burn_start" -Extra $burnReady
        throw $burnReady.error
    }

    Write-Host "Burn started."
    Write-Host "  Status: $entryStatusPath"
    Write-Host "  Alerts: $(Join-Path $AlertsDir "trade_package_ack_alerts.jsonl")"
    Write-Host "  Manifest: $ManifestPath"

    if ($NoMonitor) {
        Write-OrchestratorStatus -Phase "startup_complete_no_monitor"
        Write-OrchestratorEvent -Event "startup_complete_no_monitor"
        exit 0
    }

    $monitorDeadline = (Get-Date).AddSeconds([Math]::Max(60, $DurationSec + 600))
    while ((Get-Date) -lt $monitorDeadline) {
        $burnStatus = Read-JsonFile -Path $entryStatusPath
        $liveSnapshot = Get-LiveFreshnessSnapshot -BaseUrl $base -SessionId $SessionId -MaxFrameAgeMs $FreshLiveStateMaxAgeMs
        Write-OrchestratorStatus -Phase "monitoring" -Extra ([ordered]@{ live_state = $liveSnapshot })
        $running = [bool](Get-ObjectProperty -Object $burnStatus -Name "running")
        $samples = Get-ObjectProperty -Object $burnStatus -Name "sample_count" -Default $null
        $remaining = Get-ObjectProperty -Object $burnStatus -Name "remaining_sec" -Default $null
        Write-Host ("{0} burn_running={1} samples={2} remaining_sec={3} live_ready={4} frame_age_ms={5}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $running, $samples, $remaining, $liveSnapshot.ready, $liveSnapshot.frame_age_ms)
        if ($burnStatus -and -not $running) {
            break
        }
        Start-Sleep -Seconds ([Math]::Max(5, $MonitorPollSeconds))
    }

    Write-OrchestratorStatus -Phase "complete"
    Write-OrchestratorEvent -Event "orchestrator_complete"
} catch {
    Write-OrchestratorEvent -Event "orchestrator_failed" -Payload ([ordered]@{ error = $_.Exception.Message })
    Write-OrchestratorStatus -Phase "failed" -Extra ([ordered]@{ error = $_.Exception.Message })
    throw
} finally {
    if ($transcriptStarted) {
        try {
            Stop-Transcript | Out-Null
        } catch {
        }
    }
}
