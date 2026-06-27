[CmdletBinding()]
param(
    [string]$ConfigPath = '',
    [switch]$Bootstrap,
    [switch]$NoShooter,
    [switch]$NoStopExisting
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path -Path $script:ScriptRoot -ChildPath '..\..\..\..')).Path
$TrackerLauncherPath = Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\start_phoenixguard_24_7_tracker.ps1'
$TrackerScriptPath = Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\start_phoenixguard_24_7_tracker.py'
$ShooterPath = Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\shooter.py'
$RequirementsPath = Join-Path -Path $ProjectRoot -ChildPath 'requirements.txt'

if (-not $ConfigPath) {
    $ConfigPath = Join-Path -Path $script:ScriptRoot -ChildPath 'phoenixguard.vm-monitor.env.ps1'
}

if (-not (Test-Path -LiteralPath $TrackerLauncherPath)) {
    throw "Tracker launcher not found at '$TrackerLauncherPath'."
}
if (-not (Test-Path -LiteralPath $TrackerScriptPath)) {
    throw "Tracker script not found at '$TrackerScriptPath'."
}
if (-not (Test-Path -LiteralPath $ShooterPath)) {
    throw "Shooter entrypoint not found at '$ShooterPath'."
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "VM monitor config not found at '$ConfigPath'. Copy phoenixguard.vm-monitor.env.example.ps1 first."
}

. (Resolve-Path -LiteralPath $ConfigPath).Path

$RuntimeRoot = Join-Path -Path $ProjectRoot -ChildPath '.codex_runtime'
$env:PHOENIXGUARD_RUNTIME_DIR = $RuntimeRoot
$LogRoot = Join-Path -Path $RuntimeRoot -ChildPath 'vm_monitor_logs'
if (-not (Test-Path -LiteralPath $LogRoot)) {
    New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
}

if (-not $env:PHOENIXGUARD_DATA_DIR) {
    $env:PHOENIXGUARD_DATA_DIR = Join-Path -Path $RuntimeRoot -ChildPath 'data_live'
}
if (-not $env:PHOENIXGUARD_LOGS_DIR) {
    $env:PHOENIXGUARD_LOGS_DIR = Join-Path -Path $RuntimeRoot -ChildPath 'logs_live'
}
if (-not $env:PHOENIXGUARD_TRACKER_STATUS_FILE) {
    $env:PHOENIXGUARD_TRACKER_STATUS_FILE = Join-Path -Path $RuntimeRoot -ChildPath 'tracker_status.json'
}
if (-not $env:PHOENIXGUARD_MONITOR_STATUS_FILE) {
    $env:PHOENIXGUARD_MONITOR_STATUS_FILE = Join-Path -Path $RuntimeRoot -ChildPath 'vm_monitor_status.json'
}

foreach ($path in @($env:PHOENIXGUARD_DATA_DIR, $env:PHOENIXGUARD_LOGS_DIR, (Split-Path -Parent $env:PHOENIXGUARD_TRACKER_STATUS_FILE), (Split-Path -Parent $env:PHOENIXGUARD_MONITOR_STATUS_FILE))) {
    if ($path -and -not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

function Get-EnvOrDefault {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$DefaultValue
    )

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }
    return $value
}

function ConvertTo-BoolFlag {
    param(
        [string]$Value,
        [bool]$DefaultValue = $false
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $DefaultValue
    }
    return $Value.Trim().ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
}

function ConvertTo-ProcessArgumentString {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $escaped = foreach ($argument in $Arguments) {
        if ($argument -match '[\s"]') {
            '"' + ($argument -replace '"', '\"') + '"'
        } else {
            $argument
        }
    }
    return ($escaped -join ' ')
}

function Get-ObjectPropertyValue {
    param(
        $InputObject,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        $DefaultValue = $null
    )

    if ($null -eq $InputObject) {
        return $DefaultValue
    }
    if ($InputObject -is [System.Collections.IDictionary]) {
        if ($InputObject.Contains($Name)) {
            return $InputObject[$Name]
        }
        return $DefaultValue
    }

    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $DefaultValue
    }
    return $property.Value
}

function ConvertTo-NullableDouble {
    param(
        $Value
    )

    if ($null -eq $Value) {
        return $null
    }
    try {
        $text = ([string]$Value).Trim()
        if ([string]::IsNullOrWhiteSpace($text)) {
            return $null
        }
        return [double]$text
    } catch {
        return $null
    }
}

function Write-MonitorLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $line = '[{0}] {1}' -f (Get-Date -Format o), $Message
    Write-Host $line
    Add-Content -LiteralPath (Join-Path -Path $LogRoot -ChildPath 'vm-monitor.log') -Value $line
}

function Stop-PhoenixGuardMonitorChildren {
    $patterns = @(
        'start_phoenixguard_24_7_tracker.py',
        'start_phoenixguard_mobile_api.py',
        'shooter.py'
    )
    Get-CimInstance Win32_Process | Where-Object {
        $commandLine = [string]$_.CommandLine
        -not [string]::IsNullOrWhiteSpace($commandLine) -and ($patterns | Where-Object { $commandLine -like "*$_*" })
    } | ForEach-Object {
        Write-MonitorLog "Stopping existing PhoenixGuard process $($_.ProcessId)."
        Stop-Process -Id ([int]$_.ProcessId) -Force -ErrorAction SilentlyContinue
    }
}

function Test-ApiHealth {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl
    )

    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/health" -TimeoutSec 5
        return [string]$health.status -eq 'ok'
    } catch {
        return $false
    }
}

function Wait-ApiHealth {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [int]$TimeoutSec
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-ApiHealth -BaseUrl $BaseUrl) {
            return $true
        }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

function Get-TrackerStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Write-MonitorStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [hashtable]$Payload
    )

    $Payload['timestamp_epoch'] = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $Payload['timestamp_utc'] = (Get-Date).ToUniversalTime().ToString('o')
    $json = $Payload | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
}

$BindHost = Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_BIND_HOST' -DefaultValue (Get-EnvOrDefault -Name 'PHOENIXGUARD_MOBILE_API_HOST' -DefaultValue '127.0.0.1')
$BaseHost = Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_BASE_HOST' -DefaultValue $(if ($BindHost -eq '0.0.0.0') { '127.0.0.1' } else { $BindHost })
$Port = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_MOBILE_API_PORT' -DefaultValue '8793')
$SessionId = Get-EnvOrDefault -Name 'PHOENIXGUARD_TRACKER_SESSION_ID' -DefaultValue 'pocket-live-8788'
$CaptureIntervalSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC' -DefaultValue '15.0')
$HealthTimeoutSec = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_HEALTH_TIMEOUT_SEC' -DefaultValue '90')
$HealthIntervalSec = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_HEALTH_INTERVAL_SEC' -DefaultValue '10')
$UnhealthyRestartsAfter = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_UNHEALTHY_RESTARTS_AFTER' -DefaultValue '6')
$RestartDelaySec = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_RESTART_DELAY_SEC' -DefaultValue '5')
$ShooterPollSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_SHOOTER_POLL_SEC' -DefaultValue '15.0')
$ShooterMinConfidence = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_SHOOTER_MIN_CONFIDENCE' -DefaultValue '0.2')
$ShooterMaxSignalAge = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_SHOOTER_MAX_SIGNAL_AGE_SEC' -DefaultValue '8')
$ShooterCooldownSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_SHOOTER_COOLDOWN_SEC' -DefaultValue '1200')
$ShooterEnabled = (ConvertTo-BoolFlag -Value (Get-EnvOrDefault -Name 'PHOENIXGUARD_SHOOTER_ENABLED' -DefaultValue '1') -DefaultValue $true) -and -not $NoShooter
$StartupTestTradeEnabled = ConvertTo-BoolFlag -Value (Get-EnvOrDefault -Name 'PHOENIXGUARD_STARTUP_TEST_TRADE_ENABLED' -DefaultValue '0') -DefaultValue $false
$StartupTestTradeSide = (Get-EnvOrDefault -Name 'PHOENIXGUARD_STARTUP_TEST_TRADE_SIDE' -DefaultValue 'AUTO').Trim().ToUpperInvariant()
$StartupTestTradeExpirySec = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_STARTUP_TEST_TRADE_EXPIRY_SEC' -DefaultValue '180')
$StartupTestTradeReadyTimeoutSec = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_STARTUP_TEST_TRADE_READY_TIMEOUT_SEC' -DefaultValue '120')
$StartupTestTradeMaxSignalAgeSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_STARTUP_TEST_TRADE_MAX_SIGNAL_AGE_SEC' -DefaultValue ([string]$ShooterMaxSignalAge))
$StartupTestTradeMinRepeatSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_STARTUP_TEST_TRADE_MIN_REPEAT_SEC' -DefaultValue '3600')
$VisibilityRecoveryEnabled = ConvertTo-BoolFlag -Value (Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_VISIBILITY_RECOVERY_ENABLED' -DefaultValue '1') -DefaultValue $true
$VisibilityRecoveryStaleSignalSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_VISIBILITY_STALE_SIGNAL_SEC' -DefaultValue ([string]([Math]::Max(60.0, $ShooterMaxSignalAge * 8.0))))
$VisibilityRecoveryCooldownSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_VISIBILITY_RECOVERY_COOLDOWN_SEC' -DefaultValue '90')
$CaptureProgressStallSec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_CAPTURE_STALL_SEC' -DefaultValue '45')
$LowStructureRecoverySec = [double](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_LOW_STRUCTURE_SEC' -DefaultValue '35')
$LowStructureMinCandles = [int](Get-EnvOrDefault -Name 'PHOENIXGUARD_MONITOR_LOW_STRUCTURE_MIN_CANDLES' -DefaultValue '6')
$BrokerUrl = Get-EnvOrDefault -Name 'PHOENIXGUARD_BROKER_URL' -DefaultValue 'https://pocketoption.com/en/cabinet/demo-quick-high-low/'
$WaitForLock = ConvertTo-BoolFlag -Value (Get-EnvOrDefault -Name 'PHOENIXGUARD_TRACKER_WAIT_FOR_LOCK' -DefaultValue '1') -DefaultValue $true
$OpenDashboard = ConvertTo-BoolFlag -Value (Get-EnvOrDefault -Name 'PHOENIXGUARD_TRACKER_OPEN_DASHBOARD' -DefaultValue '0') -DefaultValue $false
$BaseUrl = "http://$BaseHost`:$Port"
$DashboardUrl = "$BaseUrl/v1/mobile/window-tracker/dashboard/$SessionId"
$SessionRouteId = [uri]::EscapeDataString($SessionId)

$env:PHOENIXGUARD_MOBILE_API_HOST = $BindHost
$env:PHOENIXGUARD_MOBILE_API_PORT = [string]$Port
$env:PHOENIXGUARD_TRACKER_SESSION_ID = $SessionId
$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC = [string]$CaptureIntervalSec

. (Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\Resolve-PhoenixGuardPython.ps1')
$pythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $ProjectRoot
$PythonPath = [string]$pythonRuntime.VenvPython

if ($Bootstrap) {
    Write-MonitorLog 'Installing Python dependencies for VM monitor.'
    & $PythonPath -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to upgrade pip.'
    }
    & $PythonPath -m pip install -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies from '$RequirementsPath'."
    }
}

if (-not $NoStopExisting) {
    Stop-PhoenixGuardMonitorChildren
}

$trackerProcess = $null
$shooterProcess = $null
$trackerRestartCount = 0
$shooterRestartCount = 0
$unhealthyCount = 0
$startupTestTradeAttempted = $false
$startupTestTradeResult = $null
$lastVisibilityRecoveryEpoch = 0.0
$lastObservedFrameIndex = -1.0
$lastFrameProgressEpoch = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$lowStructureSinceEpoch = 0.0

function Get-TrackerSessionSnapshot {
    try {
        return Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$SessionRouteId" -TimeoutSec 10
    } catch {
        return $null
    }
}

function Test-TrackerReadyForExecution {
    param(
        [Parameter(Mandatory = $true)]
        $SessionPayload
    )

    if ($null -eq $SessionPayload) {
        return $false
    }
    $focusLocked = $false
    $manualFocusRegion = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'manual_focus_region'
    if ($null -ne $manualFocusRegion) {
        $bbox = Get-ObjectPropertyValue -InputObject $manualFocusRegion -Name 'normalized_bbox'
        $focusLocked = [bool](Get-ObjectPropertyValue -InputObject $manualFocusRegion -Name 'enabled' -DefaultValue $false) -and $null -ne $bbox -and $bbox.Count -eq 4
    }
    $trackingEnabled = [bool](Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'tracking_enabled' -DefaultValue $false)
    $status = ([string](Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'status' -DefaultValue '')).ToLowerInvariant()
    $running = $trackingEnabled -and $status -in @('tracking', 'running')
    if (-not ($focusLocked -and $running)) {
        return $false
    }

    $latestSignal = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'latest_signal'
    if ($null -eq $latestSignal) {
        return $false
    }
    $stateVersion = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'state_version')
    $decisionVersion = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'decision_version')
    if ($null -eq $stateVersion -or $null -eq $decisionVersion -or $stateVersion -le 0 -or $decisionVersion -le 0) {
        return $false
    }

    $signalAge = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $latestSignal -Name 'signal_age_sec')
    if ($null -eq $signalAge) {
        $signalAge = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'signal_age_sec')
    }
    $freshnessWindow = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $latestSignal -Name 'freshness_window_sec')
    if ($null -eq $freshnessWindow) {
        $freshnessWindow = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'freshness_window_sec')
    }
    $pipelineLatency = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $latestSignal -Name 'pipeline_latency_sec')
    if ($null -eq $pipelineLatency) {
        $pipelineLatency = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'pipeline_latency_sec')
    }
    $allowedSignalAge = [Math]::Max(1.0, $StartupTestTradeMaxSignalAgeSec)
    if ($null -ne $freshnessWindow) {
        $allowedSignalAge = [Math]::Max($allowedSignalAge, $freshnessWindow)
    }
    if ($null -ne $pipelineLatency) {
        $allowedSignalAge = [Math]::Max($allowedSignalAge, $pipelineLatency * 3.0)
    }
    if ($null -eq $signalAge -or $signalAge -gt $allowedSignalAge) {
        return $false
    }

    $validUntil = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'decision_valid_until_epoch')
    $nowEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($null -ne $validUntil -and $validUntil -gt 0 -and $nowEpoch -gt $validUntil) {
        return $false
    }
    return $true
}

function Wait-TrackerReadyForExecution {
    param(
        [int]$TimeoutSec
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSec))
    while ((Get-Date) -lt $deadline) {
        $snapshot = Get-TrackerSessionSnapshot
        if ($null -ne $snapshot -and (Test-TrackerReadyForExecution -SessionPayload $snapshot)) {
            return $snapshot
        }
        Start-Sleep -Milliseconds 800
    }
    return $null
}

function Resolve-StartupTestTradeSide {
    param(
        $SessionPayload
    )

    if ($StartupTestTradeSide -in @('BUY', 'SELL')) {
        return $StartupTestTradeSide
    }

    $timingCandidates = @()
    $latestSignal = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'latest_signal'
    $trackingSummary = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'tracking_summary'
    $latestSignalTiming = Get-ObjectPropertyValue -InputObject $latestSignal -Name 'execution_timing'
    $trackingSummaryTiming = Get-ObjectPropertyValue -InputObject $trackingSummary -Name 'execution_timing'
    if ($null -ne $latestSignalTiming) {
        $timingCandidates += $latestSignalTiming
    }
    if ($null -ne $trackingSummaryTiming) {
        $timingCandidates += $trackingSummaryTiming
    }
    foreach ($timing in $timingCandidates) {
        $entryAllowed = $true
        $entryAllowedValue = Get-ObjectPropertyValue -InputObject $timing -Name 'entry_allowed'
        if ($null -ne $entryAllowedValue) {
            $entryAllowed = [System.Convert]::ToBoolean($entryAllowedValue)
        }
        $side = ([string](Get-ObjectPropertyValue -InputObject $timing -Name 'side' -DefaultValue '')).Trim().ToUpperInvariant()
        if ($entryAllowed -and $side -in @('BUY', 'SELL')) {
            return $side
        }
        if ($side -eq 'HOLD' -or -not $entryAllowed) {
            return ''
        }
    }

    $candidates = @()
    if ($null -ne $latestSignal) {
        $candidates += Get-ObjectPropertyValue -InputObject $latestSignal -Name 'execution_action'
        $candidates += Get-ObjectPropertyValue -InputObject $latestSignal -Name 'action'
        $candidates += Get-ObjectPropertyValue -InputObject $latestSignal -Name 'major_bias'
        $candidates += Get-ObjectPropertyValue -InputObject $latestSignal -Name 'candidate_action'
    }
    $smartMoneyContext = Get-ObjectPropertyValue -InputObject $trackingSummary -Name 'smart_money_context'
    if ($null -ne $smartMoneyContext) {
        $candidates += Get-ObjectPropertyValue -InputObject $smartMoneyContext -Name 'dominant_side'
    }

    foreach ($candidate in $candidates) {
        $side = ([string]$candidate).Trim().ToUpperInvariant()
        if ($side -in @('BUY', 'SELL')) {
            return $side
        }
    }
    return ''
}

function Get-RecentStartupTestTradeSkipResult {
    param(
        $SessionPayload
    )

    if ($null -eq $SessionPayload) {
        return $null
    }

    $brokerState = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'broker_execution_state'
    $lastTradeEpoch = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $brokerState -Name 'last_trade_epoch')
    $lastResult = Get-ObjectPropertyValue -InputObject $brokerState -Name 'last_result'
    $lastResultTrade = Get-ObjectPropertyValue -InputObject $lastResult -Name 'trade'
    if ($null -eq $lastTradeEpoch) {
        $lastTradeEpoch = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $lastResultTrade -Name 'opened_epoch')
    }
    $lastLane = ([string](Get-ObjectPropertyValue -InputObject $lastResultTrade -Name 'lane' -DefaultValue (Get-ObjectPropertyValue -InputObject $brokerState -Name 'lane' -DefaultValue ''))).Trim().ToUpperInvariant()
    $nowEpoch = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($lastLane -eq 'DEMO_RANDOM_TEST' -and $null -ne $lastTradeEpoch -and ($nowEpoch - $lastTradeEpoch) -lt $StartupTestTradeMinRepeatSec) {
        return @{
            status = 'skipped'
            message = "Startup test trade already ran $([Math]::Round($nowEpoch - $lastTradeEpoch, 1))s ago; not repeating within $StartupTestTradeMinRepeatSec second(s)."
        }
    }
    return $null
}

function Get-TrackerSignalAgeSec {
    param(
        $SessionPayload
    )

    $latestSignal = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'latest_signal'
    $signalAge = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $latestSignal -Name 'signal_age_sec')
    if ($null -eq $signalAge) {
        $signalAge = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'signal_age_sec')
    }
    return $signalAge
}

function Get-TrackerFrameIndex {
    param(
        $SessionPayload
    )

    $frameIndex = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'frame_index')
    if ($null -eq $frameIndex) {
        $frameIndex = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'capture_count')
    }
    return $frameIndex
}

function Get-TrackerVisibleCandleCount {
    param(
        $SessionPayload
    )

    $trackingSummary = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'tracking_summary'
    $visible = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $trackingSummary -Name 'visible_candle_count')
    if ($null -ne $visible) {
        return [int][Math]::Max(0, [Math]::Round($visible))
    }
    $tracked = Get-ObjectPropertyValue -InputObject $trackingSummary -Name 'tracked_candles'
    if ($null -ne $tracked) {
        try {
            return [int]$tracked.Count
        } catch {
            return 0
        }
    }
    return 0
}

function Get-TrackerCaptureAgeSec {
    param(
        $SessionPayload
    )

    $lastCaptureEpoch = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'last_capture_epoch')
    if ($null -eq $lastCaptureEpoch -or $lastCaptureEpoch -le 0) {
        return $null
    }
    $nowEpoch = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    return [Math]::Max(0.0, $nowEpoch - $lastCaptureEpoch)
}

function Test-TrackerNeedsVisibilityRecovery {
    param(
        $SessionPayload
    )

    if (-not $VisibilityRecoveryEnabled -or $null -eq $SessionPayload) {
        return @{
            needed = $false
            reason = ''
            signal_age_sec = $null
        }
    }

    $latestSignal = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'latest_signal'
    $trackingSummary = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'tracking_summary'
    $lastError = [string](Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'last_error' -DefaultValue '')
    $summaryMessage = [string](Get-ObjectPropertyValue -InputObject $trackingSummary -Name 'message' -DefaultValue '')
    $signalMessage = [string](Get-ObjectPropertyValue -InputObject $latestSignal -Name 'message' -DefaultValue '')
    $combined = ($lastError + ' ' + $summaryMessage + ' ' + $signalMessage).ToLowerInvariant()
    $brokerState = Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'broker_execution_state'
    $activeTrade = Get-ObjectPropertyValue -InputObject $brokerState -Name 'active_trade'
    $activeExpires = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $activeTrade -Name 'expires_epoch')
    $nowEpoch = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($null -ne $activeTrade -and $null -ne $activeExpires -and $activeExpires -gt $nowEpoch) {
        return @{
            needed = $false
            reason = 'active trade is being monitored'
            signal_age_sec = Get-TrackerSignalAgeSec -SessionPayload $SessionPayload
        }
    }
    $signalAge = Get-TrackerSignalAgeSec -SessionPayload $SessionPayload
    $trackingEnabled = [bool](Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'tracking_enabled' -DefaultValue $false)
    $sessionStatus = ([string](Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'status' -DefaultValue '')).Trim().ToLowerInvariant()
    $running = $trackingEnabled -and $sessionStatus -in @('tracking', 'running')
    $frameIndex = Get-TrackerFrameIndex -SessionPayload $SessionPayload
    if ($running -and $null -ne $frameIndex) {
        if ($script:lastObservedFrameIndex -lt 0 -or $frameIndex -ne $script:lastObservedFrameIndex) {
            $script:lastObservedFrameIndex = $frameIndex
            $script:lastFrameProgressEpoch = $nowEpoch
        } elseif (($nowEpoch - $script:lastFrameProgressEpoch) -gt $CaptureProgressStallSec) {
            return @{
                needed = $true
                reason = "capture frame stalled for $([Math]::Round($nowEpoch - $script:lastFrameProgressEpoch, 1))s at frame $frameIndex"
                signal_age_sec = $signalAge
            }
        }
    }
    $captureAge = Get-TrackerCaptureAgeSec -SessionPayload $SessionPayload
    if ($running -and $null -ne $captureAge -and $captureAge -gt $CaptureProgressStallSec) {
        return @{
            needed = $true
            reason = "last capture is stale for $([Math]::Round($captureAge, 1))s"
            signal_age_sec = $signalAge
        }
    }
    $visibleCandles = Get-TrackerVisibleCandleCount -SessionPayload $SessionPayload
    $signalStatus = ([string](Get-ObjectPropertyValue -InputObject $latestSignal -Name 'status' -DefaultValue '')).Trim().ToLowerInvariant()
    $lowStructure = $running -and $visibleCandles -lt $LowStructureMinCandles -and (
        $signalStatus -in @('warming', 'awaiting_focus', 'empty') -or
        $combined.Contains('waiting for more visible candle') -or
        $combined.Contains('locked focus region')
    )
    if ($lowStructure) {
        if ($script:lowStructureSinceEpoch -le 0) {
            $script:lowStructureSinceEpoch = $nowEpoch
        }
        if (($nowEpoch - $script:lowStructureSinceEpoch) -gt $LowStructureRecoverySec) {
            return @{
                needed = $true
                reason = "low chart structure for $([Math]::Round($nowEpoch - $script:lowStructureSinceEpoch, 1))s ($visibleCandles visible candle(s), status=$signalStatus)"
                signal_age_sec = $signalAge
            }
        }
    } else {
        $script:lowStructureSinceEpoch = 0.0
    }
    $studyInProgress = [bool](Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'study_in_progress' -DefaultValue $false)
    if ($studyInProgress -and $null -ne $signalAge -and $signalAge -lt [Math]::Max(300.0, $VisibilityRecoveryStaleSignalSec * 4.0)) {
        return @{
            needed = $false
            reason = 'tracker study is still in progress'
            signal_age_sec = $signalAge
        }
    }
    $stateVersion = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'state_version')
    $decisionVersion = ConvertTo-NullableDouble -Value (Get-ObjectPropertyValue -InputObject $SessionPayload -Name 'decision_version')
    $hasPublishedSignal = $null -ne $stateVersion -and $null -ne $decisionVersion -and $stateVersion -gt 0 -and $decisionVersion -gt 0

    if ($combined.Contains('locked broker window is not visible') -or $combined.Contains('broker window is not visible')) {
        return @{
            needed = $true
            reason = 'locked broker window is not visible'
            signal_age_sec = $signalAge
        }
    }
    if ($hasPublishedSignal -and $null -ne $signalAge -and $signalAge -gt $VisibilityRecoveryStaleSignalSec) {
        return @{
            needed = $true
            reason = "signal stale for $([Math]::Round($signalAge, 1))s"
            signal_age_sec = $signalAge
        }
    }

    return @{
        needed = $false
        reason = ''
        signal_age_sec = $signalAge
    }
}

function Invoke-BrokerVisibilityRecovery {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Reason
    )

    if (-not $VisibilityRecoveryEnabled) {
        return $false
    }
    $nowEpoch = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($script:lastVisibilityRecoveryEpoch -gt 0 -and ($nowEpoch - $script:lastVisibilityRecoveryEpoch) -lt $VisibilityRecoveryCooldownSec) {
        return $false
    }
    $script:lastVisibilityRecoveryEpoch = $nowEpoch
    Write-MonitorLog "Recovering broker visibility because $Reason."
    if (-not [string]::IsNullOrWhiteSpace($BrokerUrl)) {
        try {
            Start-Process -FilePath $BrokerUrl | Out-Null
            Write-MonitorLog "Requested broker window open: $BrokerUrl"
        } catch {
            Write-MonitorLog "Broker window open request failed: $($_.Exception.Message)"
        }
    }
    return $true
}

function Restart-PhoenixGuardStack {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Reason
    )

    Write-MonitorLog "Restarting PhoenixGuard stack: $Reason"
    Stop-PhoenixGuardMonitorChildren
    if ($RestartDelaySec -gt 0) {
        Start-Sleep -Seconds $RestartDelaySec
    }
    $script:unhealthyCount = 0
    $script:trackerRestartCount += 1
    $script:trackerProcess = Start-TrackerProcess
    Wait-ApiHealth -BaseUrl $BaseUrl -TimeoutSec $HealthTimeoutSec | Out-Null
    $script:shooterProcess = $null
    if ($ShooterEnabled -and (Test-ApiHealth -BaseUrl $BaseUrl)) {
        $script:shooterRestartCount += 1
        $script:shooterProcess = Start-ShooterProcess
    }
}

function Invoke-StartupTestTrade {
    if (-not $StartupTestTradeEnabled) {
        return $null
    }
    if ($startupTestTradeAttempted) {
        return $startupTestTradeResult
    }

    $quickSnapshot = Get-TrackerSessionSnapshot
    $recentSkip = Get-RecentStartupTestTradeSkipResult -SessionPayload $quickSnapshot
    if ($null -ne $recentSkip) {
        $script:startupTestTradeAttempted = $true
        $script:startupTestTradeResult = $recentSkip
        Write-MonitorLog "Startup test trade skipped: $($recentSkip.message)"
        return $recentSkip
    }

    Write-MonitorLog "Startup test trade enabled; waiting for locked tracker session before demo execution test."
    $snapshot = Wait-TrackerReadyForExecution -TimeoutSec $StartupTestTradeReadyTimeoutSec
    if ($null -eq $snapshot) {
        $result = @{
            status = 'skipped'
            message = "Tracker was not focus-locked and running within $StartupTestTradeReadyTimeoutSec second(s)."
        }
        $script:startupTestTradeAttempted = $true
        $script:startupTestTradeResult = $result
        Write-MonitorLog "Startup test trade skipped: $($result.message)"
        return $result
    }

    $script:startupTestTradeAttempted = $true
    $recentSkip = Get-RecentStartupTestTradeSkipResult -SessionPayload $snapshot
    if ($null -ne $recentSkip) {
        $script:startupTestTradeResult = $recentSkip
        Write-MonitorLog "Startup test trade skipped: $($recentSkip.message)"
        return $recentSkip
    }

    $side = Resolve-StartupTestTradeSide -SessionPayload $snapshot
    $body = @{
        expiry_seconds = [Math]::Max(1, $StartupTestTradeExpirySec)
        force = $true
    }
    if ($side -in @('BUY', 'SELL')) {
        $body['side'] = $side
    }

    try {
        $tradeTimeoutSec = [Math]::Min(300, [Math]::Max(90, $StartupTestTradeReadyTimeoutSec))
        $response = Invoke-RestMethod `
            -Method Post `
            -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$SessionRouteId/demo-random-trade" `
            -ContentType 'application/json' `
            -Body ($body | ConvertTo-Json -Depth 5) `
            -TimeoutSec $tradeTimeoutSec
        $state = $response.broker_execution_state
        $result = @{
            status = [string](Get-ObjectPropertyValue -InputObject $state -Name 'status' -DefaultValue '')
            side = [string](Get-ObjectPropertyValue -InputObject $state -Name 'side' -DefaultValue '')
            lane = [string](Get-ObjectPropertyValue -InputObject $state -Name 'lane' -DefaultValue '')
            expiry_seconds = [int](Get-ObjectPropertyValue -InputObject $state -Name 'expiry_seconds' -DefaultValue 0)
            message = [string](Get-ObjectPropertyValue -InputObject $state -Name 'message' -DefaultValue '')
        }
        $script:startupTestTradeResult = $result
        Write-MonitorLog "Startup test trade result: status=$($result.status) side=$($result.side) lane=$($result.lane) expiry=$($result.expiry_seconds)s message=$($result.message)"
        return $result
    } catch {
        $result = @{
            status = 'error'
            message = $_.Exception.Message
        }
        $script:startupTestTradeResult = $result
        Write-MonitorLog "Startup test trade failed: $($result.message)"
        return $result
    }
}

function Start-TrackerProcess {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $args = @(
        $TrackerScriptPath,
        '--host', $BindHost,
        '--port', [string]$Port,
        '--session-id', $SessionId,
        '--capture-interval', [string]$CaptureIntervalSec
    )
    if (-not $OpenDashboard) {
        $args += '--no-open-dashboard'
    }
    if (-not $WaitForLock) {
        $args += '--no-wait-for-lock'
    }

    $outPath = Join-Path -Path $LogRoot -ChildPath "tracker-$stamp.out.log"
    $errPath = Join-Path -Path $LogRoot -ChildPath "tracker-$stamp.err.log"
    Write-MonitorLog "Starting tracker on $BaseUrl with session '$SessionId'."
    return Start-Process -FilePath $PythonPath -ArgumentList (ConvertTo-ProcessArgumentString -Arguments $args) -WorkingDirectory $ProjectRoot -RedirectStandardOutput $outPath -RedirectStandardError $errPath -WindowStyle Hidden -PassThru
}

function Start-ShooterProcess {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $args = @(
        $ShooterPath,
        'signal',
        '--session-id', $SessionId,
        '--base-url', $BaseUrl,
        '--poll', [string]$ShooterPollSec,
        '--preferred-source', 'tracker',
        '--require-preferred-source',
        '--min-confidence', [string]$ShooterMinConfidence,
        '--max-signal-age', [string]$ShooterMaxSignalAge,
        '--cooldown', [string]$ShooterCooldownSec
    )

    $outPath = Join-Path -Path $LogRoot -ChildPath "shooter-$stamp.out.log"
    $errPath = Join-Path -Path $LogRoot -ChildPath "shooter-$stamp.err.log"
    Write-MonitorLog "Starting shooter against $BaseUrl with $ShooterPollSec second polling."
    return Start-Process -FilePath $PythonPath -ArgumentList (ConvertTo-ProcessArgumentString -Arguments $args) -WorkingDirectory $ProjectRoot -RedirectStandardOutput $outPath -RedirectStandardError $errPath -WindowStyle Hidden -PassThru
}

try {
    $trackerProcess = Start-TrackerProcess
    if (-not (Wait-ApiHealth -BaseUrl $BaseUrl -TimeoutSec $HealthTimeoutSec)) {
        Write-MonitorLog "Tracker API did not become healthy at $BaseUrl within $HealthTimeoutSec second(s)."
    }

    if ($ShooterEnabled -and (Test-ApiHealth -BaseUrl $BaseUrl)) {
        $shooterProcess = Start-ShooterProcess
    }

    if ($StartupTestTradeEnabled -and (Test-ApiHealth -BaseUrl $BaseUrl)) {
        Invoke-StartupTestTrade | Out-Null
    }

    Write-MonitorStatus -Path $env:PHOENIXGUARD_MONITOR_STATUS_FILE -Payload @{
        status = 'running'
        base_url = $BaseUrl
        dashboard_url = $DashboardUrl
        session_id = $SessionId
        api_healthy = (Test-ApiHealth -BaseUrl $BaseUrl)
        bind_host = $BindHost
        port = $Port
        capture_interval_sec = $CaptureIntervalSec
        shooter_enabled = $ShooterEnabled
        shooter_poll_sec = $ShooterPollSec
        shooter_cooldown_sec = $ShooterCooldownSec
        startup_test_trade_enabled = $StartupTestTradeEnabled
        startup_test_trade_max_signal_age_sec = $StartupTestTradeMaxSignalAgeSec
        startup_test_trade_result = $startupTestTradeResult
        visibility_recovery_enabled = $VisibilityRecoveryEnabled
        visibility_recovery_stale_signal_sec = $VisibilityRecoveryStaleSignalSec
        visibility_recovery_cooldown_sec = $VisibilityRecoveryCooldownSec
        capture_stall_sec = $CaptureProgressStallSec
        low_structure_recovery_sec = $LowStructureRecoverySec
        low_structure_min_candles = $LowStructureMinCandles
        broker_url = $BrokerUrl
        last_visibility_recovery_epoch = $lastVisibilityRecoveryEpoch
        tracker_pid = $(if ($null -ne $trackerProcess -and -not $trackerProcess.HasExited) { $trackerProcess.Id } else { $null })
        shooter_pid = $(if ($null -ne $shooterProcess -and -not $shooterProcess.HasExited) { $shooterProcess.Id } else { $null })
        tracker_restart_count = $trackerRestartCount
        shooter_restart_count = $shooterRestartCount
        tracker_status_file = $env:PHOENIXGUARD_TRACKER_STATUS_FILE
        latest_tracker_status = (Get-TrackerStatus -Path $env:PHOENIXGUARD_TRACKER_STATUS_FILE)
    }

    while ($true) {
        Start-Sleep -Seconds $HealthIntervalSec

        $apiHealthy = Test-ApiHealth -BaseUrl $BaseUrl
        if ($apiHealthy) {
            $unhealthyCount = 0
        } else {
            $unhealthyCount += 1
            Write-MonitorLog "Tracker API health failed ($unhealthyCount/$UnhealthyRestartsAfter)."
        }

        if ($null -eq $trackerProcess -or $trackerProcess.HasExited -or ($UnhealthyRestartsAfter -gt 0 -and $unhealthyCount -ge $UnhealthyRestartsAfter)) {
            $trackerRestartCount += 1
            if ($null -ne $trackerProcess -and -not $trackerProcess.HasExited) {
                Write-MonitorLog 'Restarting tracker because the API stayed unhealthy.'
                Stop-Process -Id $trackerProcess.Id -Force -ErrorAction SilentlyContinue
            } else {
                Write-MonitorLog 'Restarting tracker because the tracker process exited.'
            }
            Stop-PhoenixGuardMonitorChildren
            if ($RestartDelaySec -gt 0) {
                Start-Sleep -Seconds $RestartDelaySec
            }
            $unhealthyCount = 0
            $trackerProcess = Start-TrackerProcess
            Wait-ApiHealth -BaseUrl $BaseUrl -TimeoutSec $HealthTimeoutSec | Out-Null
        }

        if ($ShooterEnabled -and (Test-ApiHealth -BaseUrl $BaseUrl) -and ($null -eq $shooterProcess -or $shooterProcess.HasExited)) {
            $shooterRestartCount += 1
            if ($RestartDelaySec -gt 0) {
                Start-Sleep -Seconds $RestartDelaySec
            }
            $shooterProcess = Start-ShooterProcess
        }

        $sessionSnapshot = $null
        if (Test-ApiHealth -BaseUrl $BaseUrl) {
            $sessionSnapshot = Get-TrackerSessionSnapshot
        }
        $visibilityRecovery = Test-TrackerNeedsVisibilityRecovery -SessionPayload $sessionSnapshot
        if ([bool](Get-ObjectPropertyValue -InputObject $visibilityRecovery -Name 'needed' -DefaultValue $false)) {
            $reason = [string](Get-ObjectPropertyValue -InputObject $visibilityRecovery -Name 'reason' -DefaultValue 'tracker signal is stale')
            if (Invoke-BrokerVisibilityRecovery -Reason $reason) {
                Restart-PhoenixGuardStack -Reason "broker visibility recovery: $reason"
                if ($StartupTestTradeEnabled -and (Test-ApiHealth -BaseUrl $BaseUrl)) {
                    $completedStatuses = @('clicked', 'click_sent_unverified', 'monitoring')
                    $previousStartupStatus = [string](Get-ObjectPropertyValue -InputObject $startupTestTradeResult -Name 'status' -DefaultValue '')
                    if ($previousStartupStatus -notin $completedStatuses) {
                        $script:startupTestTradeAttempted = $false
                        Invoke-StartupTestTrade | Out-Null
                    }
                }
                continue
            }
        }

        $trackerStatus = Get-TrackerStatus -Path $env:PHOENIXGUARD_TRACKER_STATUS_FILE
        Write-MonitorStatus -Path $env:PHOENIXGUARD_MONITOR_STATUS_FILE -Payload @{
            status = 'running'
            base_url = $BaseUrl
            dashboard_url = $DashboardUrl
            session_id = $SessionId
            api_healthy = $apiHealthy
            bind_host = $BindHost
            port = $Port
            capture_interval_sec = $CaptureIntervalSec
            shooter_enabled = $ShooterEnabled
            shooter_poll_sec = $ShooterPollSec
            shooter_cooldown_sec = $ShooterCooldownSec
            startup_test_trade_enabled = $StartupTestTradeEnabled
            startup_test_trade_max_signal_age_sec = $StartupTestTradeMaxSignalAgeSec
            startup_test_trade_result = $startupTestTradeResult
            visibility_recovery_enabled = $VisibilityRecoveryEnabled
            visibility_recovery_stale_signal_sec = $VisibilityRecoveryStaleSignalSec
            visibility_recovery_cooldown_sec = $VisibilityRecoveryCooldownSec
            capture_stall_sec = $CaptureProgressStallSec
            low_structure_recovery_sec = $LowStructureRecoverySec
            low_structure_min_candles = $LowStructureMinCandles
            broker_url = $BrokerUrl
            last_visibility_recovery_epoch = $lastVisibilityRecoveryEpoch
            latest_signal_age_sec = $(if ($null -ne $sessionSnapshot) { Get-TrackerSignalAgeSec -SessionPayload $sessionSnapshot } else { $null })
            latest_capture_age_sec = $(if ($null -ne $sessionSnapshot) { Get-TrackerCaptureAgeSec -SessionPayload $sessionSnapshot } else { $null })
            visible_candle_count = $(if ($null -ne $sessionSnapshot) { Get-TrackerVisibleCandleCount -SessionPayload $sessionSnapshot } else { $null })
            last_observed_frame_index = $lastObservedFrameIndex
            last_frame_progress_epoch = $lastFrameProgressEpoch
            tracker_pid = $(if ($null -ne $trackerProcess -and -not $trackerProcess.HasExited) { $trackerProcess.Id } else { $null })
            shooter_pid = $(if ($null -ne $shooterProcess -and -not $shooterProcess.HasExited) { $shooterProcess.Id } else { $null })
            tracker_restart_count = $trackerRestartCount
            shooter_restart_count = $shooterRestartCount
            tracker_status_file = $env:PHOENIXGUARD_TRACKER_STATUS_FILE
            latest_tracker_status = $trackerStatus
        }
    }
}
catch {
    Write-MonitorLog "VM monitor stopped after error: $($_.Exception.Message)"
    if ($_.ScriptStackTrace) {
        Write-MonitorLog $_.ScriptStackTrace
    }
    throw
}
finally {
    Write-MonitorStatus -Path $env:PHOENIXGUARD_MONITOR_STATUS_FILE -Payload @{
        status = 'stopped'
        base_url = $BaseUrl
        dashboard_url = $DashboardUrl
        session_id = $SessionId
        tracker_status_file = $env:PHOENIXGUARD_TRACKER_STATUS_FILE
    }
}
