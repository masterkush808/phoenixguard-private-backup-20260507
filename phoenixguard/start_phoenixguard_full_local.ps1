[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Interactive operator launcher prints concise status lines.")]
param(
    [string]$ApiHost = $(if ($env:PHOENIXGUARD_MOBILE_API_HOST) { $env:PHOENIXGUARD_MOBILE_API_HOST } else { '127.0.0.1' }),
    [int]$ApiPort = $(if ($env:PHOENIXGUARD_MOBILE_API_PORT) { [int]$env:PHOENIXGUARD_MOBILE_API_PORT } else { 8793 }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 1.0 }),
    [ValidateSet('FULL', 'TRACKER_ONLY', 'TRACKER_PLUS_COUNCIL', 'FULL_V3_VALIDATION', 'FULL_V3_SHOOTER_ATTACHED')]
    [string]$Profile = $(if ($env:PHOENIXGUARD_LOCAL_PROFILE) { $env:PHOENIXGUARD_LOCAL_PROFILE } else { 'FULL' }),
    [double]$ShooterPollSec = 0.05,
    [double]$ShooterMinConfidence = 0.2,
    [ValidateSet('STUDY_ONLY', 'DRY_RUN_CLICK', 'CALIBRATION_TEST', 'LIVE_DISABLED', 'LIVE_READY', 'LIVE_BEHAVIOR_VALIDATION')]
    [string]$ShooterMode = $(if ($env:PHOENIXGUARD_SHOOTER_MODE) { $env:PHOENIXGUARD_SHOOTER_MODE } else { 'LIVE_READY' }),
    [string]$BrokerSpeedProfile = $(if ($env:PHOENIXGUARD_BROKER_SPEED_PROFILE) { $env:PHOENIXGUARD_BROKER_SPEED_PROFILE } else { 'config/shooter_broker_timing_profile.json' }),
    [ValidateSet('conservative', 'balanced', 'fast-ui')]
    [string]$ActionSpeed = $(if ($env:PHOENIXGUARD_ACTION_SPEED) { $env:PHOENIXGUARD_ACTION_SPEED } else { 'balanced' }),
    [switch]$RecordActionEvidence,
    [int]$CalibrationTestExpirySeconds = $(if ($env:PHOENIXGUARD_CALIBRATION_TEST_EXPIRY_SECONDS) { [int]$env:PHOENIXGUARD_CALIBRATION_TEST_EXPIRY_SECONDS } else { 0 }),
    [ValidateSet('', 'BUY', 'SELL')]
    [string]$CalibrationTestSide = $(if ($env:PHOENIXGUARD_CALIBRATION_TEST_SIDE) { $env:PHOENIXGUARD_CALIBRATION_TEST_SIDE } else { '' }),
    [double]$CalibrationTestTimeFillWaitSeconds = $(if ($env:PHOENIXGUARD_CALIBRATION_TEST_TIME_FILL_WAIT_SECONDS) { [double]$env:PHOENIXGUARD_CALIBRATION_TEST_TIME_FILL_WAIT_SECONDS } else { 0.0 }),
    [switch]$CalibrationTestTimeOnly,
    [string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { 'Pocket Option' }),
    [int]$BrokerWindowHwnd = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_HWND) { [int]$env:PHOENIXGUARD_BROKER_WINDOW_HWND } else { 0 }),
    [string]$TrackerFocusRegion = $(if ($env:PHOENIXGUARD_TRACKER_FOCUS_REGION) { $env:PHOENIXGUARD_TRACKER_FOCUS_REGION } else { '0.03,0.13,0.87,0.96' }),
    [switch]$NoBrowser,
    [switch]$NoStatusLoop,
    [switch]$NoKillExisting
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path -LiteralPath '.venv')) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment at '$PSScriptRoot\.venv'."
    }
}

$ActivateScriptPath = Join-Path -Path $PSScriptRoot -ChildPath '.venv\Scripts\Activate.ps1'
if (-not (Test-Path -LiteralPath $ActivateScriptPath)) {
    throw "Virtual environment activation script not found at '$ActivateScriptPath'."
}

. $ActivateScriptPath

$pythonPath = Join-Path -Path $PSScriptRoot -ChildPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python executable not found at '$pythonPath'."
}

$defaultRuntimeDir = if ($env:PHOENIXGUARD_RUNTIME_DIR) {
    $env:PHOENIXGUARD_RUNTIME_DIR
} elseif ($env:LOCALAPPDATA) {
    Join-Path -Path $env:LOCALAPPDATA -ChildPath 'PhoenixGuard\codex_runtime'
} else {
    Join-Path -Path $PSScriptRoot -ChildPath '.codex_runtime'
}
$runtimeDir = $defaultRuntimeDir
$statusPath = Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'
$trackerStdoutPath = Join-Path -Path $runtimeDir -ChildPath 'tracker_launcher_stdout.log'
$trackerStderrPath = Join-Path -Path $runtimeDir -ChildPath 'tracker_launcher_stderr.log'
$baseUrl = "http://$ApiHost`:$ApiPort"
$dashboardUrl = "$baseUrl/dashboard/live/$SessionId"
$finalLaunchProfile = 'FINAL_LIVE'
$env:PHOENIXGUARD_PROFILE = $finalLaunchProfile
$env:PHOENIXGUARD_BROKER_WINDOW_HWND = "$BrokerWindowHwnd"
$env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL = if ($env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL) { $env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL } else { '0' }
$env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS = if ($env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS) { $env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS } else { '1' }
$env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N = if ($env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N) { $env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N } else { '300' }
$env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH = if ($env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH) { $env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH } else { '320' }
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT = if ($env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT) { $env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT } else { '1' }
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC = if ($env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC) { $env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC } else { '0.5' }
$env:PHOENIXGUARD_FAST_FOCUS_PREVIEW = if ($env:PHOENIXGUARD_FAST_FOCUS_PREVIEW) { $env:PHOENIXGUARD_FAST_FOCUS_PREVIEW } else { '1' }
if (-not $env:PHOENIXGUARD_DATA_DIR) {
    $env:PHOENIXGUARD_DATA_DIR = Join-Path -Path $runtimeDir -ChildPath 'data_live'
}
if (-not $env:PHOENIXGUARD_LOGS_DIR) {
    $env:PHOENIXGUARD_LOGS_DIR = Join-Path -Path $runtimeDir -ChildPath 'logs_live'
}
if (-not $env:PHOENIXGUARD_TRACKER_STATUS_FILE) {
    $env:PHOENIXGUARD_TRACKER_STATUS_FILE = Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'
}

$launchShooter = @('FULL', 'FULL_V3_VALIDATION', 'FULL_V3_SHOOTER_ATTACHED') -contains $Profile
$startupTestSignal = $false
$brokerClickPath = if ($ShooterMode -eq 'LIVE_READY') { 'EXPLICIT_LIVE_READY' } elseif ($ShooterMode -eq 'LIVE_BEHAVIOR_VALIDATION') { 'EXPLICIT_LIVE_BEHAVIOR_VALIDATION' } elseif ($ShooterMode -eq 'CALIBRATION_TEST') { 'CALIBRATION_TEST' } else { 'DISABLED' }

Write-Host "PhoenixGuard launch profile: $finalLaunchProfile"
Write-Host "  Compatibility profile: $Profile"
Write-Host "  Tracker: ON"
Write-Host "  Model Council V3: ON"
Write-Host "  Market Reality: ON"
Write-Host "  Legacy V1/V2: OFF"
Write-Host "  Execution Packet Publisher: ON (STUDY_PACKET every council cycle; PG_EXECUTION_PACKET_V3 only when executable)"
Write-Host "  Shooter: $(if ($launchShooter) { 'ON' } else { 'OFF' })"
Write-Host "  Startup Test Signal: REMOVED"
if ($startupTestSignal) {
    Write-Host "  Calibration Test Signal: ON (isolated calibration only)"
}
Write-Host "  Broker Click Path: $brokerClickPath"

if (-not (Test-Path -LiteralPath $runtimeDir)) {
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
}

function Start-TrackerChildProcess {
    $escapedRoot = $PSScriptRoot.Replace("'", "''")
    $escapedApiHost = $ApiHost.Replace("'", "''")
    $escapedSessionId = $SessionId.Replace("'", "''")
    $escapedBrokerWindowQuery = $BrokerWindowQuery.Replace("'", "''")
    $escapedTrackerFocusRegion = $TrackerFocusRegion.Replace("'", "''")
    $trackerWindowHwndArg = if ($BrokerWindowHwnd -gt 0) { " -BrokerWindowHwnd $BrokerWindowHwnd" } else { "" }
    $trackerCommand = @(
        'Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned',
        "cd '$escapedRoot'",
        ".\start_phoenixguard_24_7_tracker.ps1 -ApiHost '$escapedApiHost' -Port $ApiPort -SessionId '$escapedSessionId' -BrokerWindowQuery '$escapedBrokerWindowQuery'$trackerWindowHwndArg -FocusRegion '$escapedTrackerFocusRegion' -CaptureIntervalSec $CaptureIntervalSec -NoOpenDashboard"
    ) -join '; '

    Start-Process powershell -ArgumentList @(
        '-NoProfile',
        '-Command',
        $trackerCommand
    ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $trackerStdoutPath -RedirectStandardError $trackerStderrPath
}

if (-not $NoKillExisting) {
    $currentPid = [int]$PID
    $targetPatterns = @(
        '*start_phoenixguard_mobile_api.py*',
        '*start_phoenixguard_24_7_tracker.ps1*',
        '*start_phoenixguard_24_7_tracker.py*',
        '*shooter.py*',
        '*phoenixguard.runtime.model_council_daemon*'
    )
    for ($cleanupAttempt = 0; $cleanupAttempt -lt 3; $cleanupAttempt++) {
        $processRows = @(Get-CimInstance Win32_Process)
        $targetProcessIds = New-Object 'System.Collections.Generic.HashSet[int]'

        $processRows | Where-Object {
            $commandLine = [string]$_.CommandLine
            $matchesTarget = $false
            foreach ($pattern in $targetPatterns) {
                if ($commandLine -like $pattern) {
                    $matchesTarget = $true
                    break
                }
            }
            (-not [string]::IsNullOrWhiteSpace($commandLine)) -and ([int]$_.ProcessId) -ne $currentPid -and $matchesTarget
        } | ForEach-Object {
            [void]$targetProcessIds.Add([int]$_.ProcessId)
        }

        try {
            Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
                $ownerPid = [int]$_.OwningProcess
                if ($ownerPid -ne $currentPid) {
                    $owner = $processRows | Where-Object { [int]$_.ProcessId -eq $ownerPid } | Select-Object -First 1
                    $ownerCommandLine = [string]$owner.CommandLine
                    if ($ownerCommandLine -like '*phoenixguard*' -or $ownerCommandLine -like '*start_phoenixguard_mobile_api.py*') {
                        [void]$targetProcessIds.Add($ownerPid)
                    }
                }
            }
        } catch {
            Write-Verbose "Port cleanup check skipped: $($_.Exception.Message)"
        }

        $queue = [System.Collections.Generic.Queue[int]]::new()
        foreach ($processId in $targetProcessIds) {
            $queue.Enqueue([int]$processId)
        }
        while ($queue.Count -gt 0) {
            $parentId = $queue.Dequeue()
            $processRows | Where-Object { [int]$_.ParentProcessId -eq $parentId } | ForEach-Object {
                $childId = [int]$_.ProcessId
                if ($childId -ne $currentPid -and $targetProcessIds.Add($childId)) {
                    $queue.Enqueue($childId)
                }
            }
        }

        foreach ($processId in $targetProcessIds) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
        if ($targetProcessIds.Count -eq 0) {
            break
        }
        Start-Sleep -Seconds 1
    }
}

Write-Host "Starting PhoenixGuard tracker on $baseUrl"
$trackerProcess = Start-TrackerChildProcess

$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if ($trackerProcess.HasExited) {
        Write-Warning "Tracker launcher exited during startup with code $($trackerProcess.ExitCode). Restarting. Logs: $trackerStdoutPath / $trackerStderrPath"
        $trackerProcess = Start-TrackerChildProcess
        Start-Sleep -Seconds 1
    }
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/v1/mobile/health" -TimeoutSec 5
        if ($health.status -eq 'ok') {
            break
        }
    } catch {
        Start-Sleep -Milliseconds 800
    }
}

try {
    $session = $null
    $sessionDeadline = (Get-Date).AddSeconds(90)
    $lastSessionError = $null
    while ((Get-Date) -lt $sessionDeadline) {
        try {
            $session = Invoke-RestMethod -Uri "$baseUrl/v1/mobile/window-tracker/sessions/$SessionId" -TimeoutSec 10
            if ($session) {
                break
            }
        } catch {
            $lastSessionError = $_.Exception.Message
            Start-Sleep -Milliseconds 800
        }
    }
    if (-not $session) {
        throw "Window tracker session '$SessionId' was not available after startup wait. Last error: $lastSessionError"
    }
    if ($session -and $launchShooter) {
        Write-Host "Starting shooter against $baseUrl in $ShooterMode mode"
        $explicitLiveArm = [string]$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS
        $liveClickArm = if ($ShooterMode -eq 'LIVE_READY' -or $ShooterMode -eq 'LIVE_BEHAVIOR_VALIDATION') { '1' } else { '0' }
        $disabledShooterPollFloor = if ($env:PHOENIXGUARD_LIVE_DISABLED_SHOOTER_MIN_POLL_SEC) { [double]$env:PHOENIXGUARD_LIVE_DISABLED_SHOOTER_MIN_POLL_SEC } else { 0.50 }
        $effectiveShooterPollSec = if ($ShooterMode -eq 'LIVE_DISABLED') { [Math]::Max([double]$ShooterPollSec, [double]$disabledShooterPollFloor) } else { [double]$ShooterPollSec }
        $escapedRoot = $PSScriptRoot.Replace("'", "''")
        $escapedSessionId = $SessionId.Replace("'", "''")
        $escapedBaseUrl = $baseUrl.Replace("'", "''")
        $escapedBrokerWindowQuery = $BrokerWindowQuery.Replace("'", "''")
        $escapedBrokerSpeedProfile = $BrokerSpeedProfile.Replace("'", "''")
        $startupTestArg = if ($startupTestSignal) { ' --test-signal' } else { '' }
        $calibrationExpiryArg = if ($startupTestSignal -and $CalibrationTestExpirySeconds -gt 0) { " --calibration-test-expiry $CalibrationTestExpirySeconds" } else { '' }
        $calibrationSideArg = if ($startupTestSignal -and -not [string]::IsNullOrWhiteSpace($CalibrationTestSide)) { " --calibration-test-side $CalibrationTestSide" } else { '' }
        $calibrationWaitArg = if ($startupTestSignal -and $CalibrationTestTimeFillWaitSeconds -gt 0) { " --calibration-test-time-fill-wait $CalibrationTestTimeFillWaitSeconds" } else { '' }
        $calibrationTimeOnlyArg = if ($startupTestSignal -and $CalibrationTestTimeOnly) { ' --calibration-test-time-only' } else { '' }
        $actionEvidenceArg = if ($RecordActionEvidence -or $ShooterMode -eq 'LIVE_BEHAVIOR_VALIDATION') { ' --record-action-evidence' } else { '' }
        $windowHwndArg = if ($BrokerWindowHwnd -gt 0) { " --window-hwnd $BrokerWindowHwnd" } else { '' }
        $shooterCommand = @(
            'Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned',
            "cd '$escapedRoot'",
            "`$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS='$liveClickArm'",
            ".\.venv\Scripts\python.exe 'shooter.py' signal --session-id '$escapedSessionId' --base-url '$escapedBaseUrl' --poll $effectiveShooterPollSec --max-signal-age 30 --preferred-source tracker --require-preferred-source --min-confidence $ShooterMinConfidence --window-query '$escapedBrokerWindowQuery'$windowHwndArg --shooter-mode $ShooterMode --broker-speed-profile '$escapedBrokerSpeedProfile' --action-speed $ActionSpeed --no-auto-open$actionEvidenceArg$startupTestArg$calibrationExpiryArg$calibrationSideArg$calibrationWaitArg$calibrationTimeOnlyArg"
        ) -join '; '
        Start-Process powershell -ArgumentList @(
            '-NoExit',
            '-Command',
            $shooterCommand
        ) -WindowStyle Hidden | Out-Null
    } elseif ($session) {
        Write-Host "Profile $Profile selected; tracker started without shooter."
    }
} catch {
    throw "Tracker API did not become healthy at $baseUrl. Start output: $($_.Exception.Message)"
}

if (-not $NoBrowser) {
    Start-Process $dashboardUrl
}

if (-not (Test-Path -LiteralPath $statusPath)) {
    New-Item -ItemType File -Force -Path $statusPath | Out-Null
}

Write-Host "Dashboard: $dashboardUrl"
Write-Host "Status: $statusPath"
Write-Host "Tracker launcher logs: $trackerStdoutPath"
Write-Host "Tracker launcher errors: $trackerStderrPath"
if ($NoStatusLoop) {
    return
}
$lastStatusJson = ''
$healthFailureCount = 0
while ($true) {
    $apiHealthy = $false
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/v1/mobile/health" -TimeoutSec 2
        $apiHealthy = ($health.status -eq 'ok')
    } catch {
        $apiHealthy = $false
    }

    if ($apiHealthy) {
        $healthFailureCount = 0
    } else {
        $healthFailureCount += 1
    }

    if ($trackerProcess.HasExited -or $healthFailureCount -ge 6) {
        $restartReason = if ($trackerProcess.HasExited) {
            "tracker launcher exited with code $($trackerProcess.ExitCode)"
        } else {
            "API health failed $healthFailureCount consecutive checks"
        }
        Write-Warning "PhoenixGuard tracker child unhealthy ($restartReason). Restarting. Logs: $trackerStdoutPath / $trackerStderrPath"
        if (-not $trackerProcess.HasExited) {
            Stop-Process -Id $trackerProcess.Id -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
        $trackerProcess = Start-TrackerChildProcess
        $healthFailureCount = 0
        Start-Sleep -Seconds 2
    }

    if (Test-Path -LiteralPath $statusPath) {
        try {
            $statusJson = Get-Content -LiteralPath $statusPath -Raw -ErrorAction Stop
            if (-not [string]::IsNullOrWhiteSpace($statusJson) -and $statusJson -ne $lastStatusJson) {
                try {
                    ($statusJson | ConvertFrom-Json) | ConvertTo-Json -Depth 8
                } catch {
                    Write-Host $statusJson
                }
                $lastStatusJson = $statusJson
            }
        } catch {
            Write-Verbose "Status read skipped: $($_.Exception.Message)"
        }
    }
    Start-Sleep -Seconds 2
}
