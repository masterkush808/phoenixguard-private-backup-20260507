[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Interactive operator launcher prints concise status lines.")]
param(
    [string]$ApiHost = $(if ($env:PHOENIXGUARD_MOBILE_API_HOST) { $env:PHOENIXGUARD_MOBILE_API_HOST } else { '127.0.0.1' }),
    [int]$ApiPort = $(if ($env:PHOENIXGUARD_MOBILE_API_PORT) { [int]$env:PHOENIXGUARD_MOBILE_API_PORT } else { 8793 }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 15.0 }),
    [ValidateSet('FULL', 'TRACKER_ONLY', 'TRACKER_PLUS_COUNCIL', 'FULL_V3_VALIDATION', 'FULL_V3_SHOOTER_ATTACHED')]
    [string]$Profile = $(if ($env:PHOENIXGUARD_LOCAL_PROFILE) { $env:PHOENIXGUARD_LOCAL_PROFILE } else { 'FULL' }),
    [double]$ShooterPollSec = $(if ($env:PHOENIXGUARD_SHOOTER_POLL_SEC) { [double]$env:PHOENIXGUARD_SHOOTER_POLL_SEC } else { 15.0 }),
    [double]$ShooterMinConfidence = 0.2,
    [ValidateSet('PACKAGE_REPORTER')]
    [string]$ShooterMode = $(if ($env:PHOENIXGUARD_SHOOTER_MODE) { $env:PHOENIXGUARD_SHOOTER_MODE } else { 'PACKAGE_REPORTER' }),
    [string]$BrokerSpeedProfile = $(if ($env:PHOENIXGUARD_BROKER_SPEED_PROFILE) { $env:PHOENIXGUARD_BROKER_SPEED_PROFILE } else { '' }),
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
    [ValidateSet('chrome', 'default', 'edge')]
    [string]$DashboardBrowser = $(if ($env:PHOENIXGUARD_DASHBOARD_BROWSER) { $env:PHOENIXGUARD_DASHBOARD_BROWSER } else { 'chrome' }),
    [switch]$NoBrowser,
    [switch]$NoStatusLoop,
    [switch]$NoKillExisting
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $ProjectRoot
$backendSrc = Join-Path -Path $ProjectRoot -ChildPath 'Backend\src'
$backendRoot = Join-Path -Path $ProjectRoot -ChildPath 'Backend'
$backendCompat = Join-Path -Path $ProjectRoot -ChildPath 'Backend\compat'
$backendLaunch = Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch'
$frontendDashboard = Join-Path -Path $ProjectRoot -ChildPath 'Frontend\dashboard'
$env:PYTHONPATH = (@($backendSrc, $backendRoot, $backendCompat, $backendLaunch, $frontendDashboard, $ProjectRoot, $env:PYTHONPATH) | Where-Object { $_ -and [string]$_ -ne '' }) -join [System.IO.Path]::PathSeparator
$env:PHOENIXGUARD_PROJECT_ROOT = $ProjectRoot
$env:PHOENIXGUARD_DASHBOARD_BROWSER = $DashboardBrowser

. (Join-Path -Path $PSScriptRoot -ChildPath 'Resolve-PhoenixGuardPython.ps1')
$pythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $ProjectRoot
$pythonPath = [string]$pythonRuntime.VenvPython
$pythonProcessPath = [string]$pythonRuntime.ProcessPython

$defaultRuntimeDir = Join-Path -Path $ProjectRoot -ChildPath '.codex_runtime'
$runtimeDir = $defaultRuntimeDir
$statusPath = Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'
$trackerStdoutPath = Join-Path -Path $runtimeDir -ChildPath 'tracker_launcher_stdout.log'
$trackerStderrPath = Join-Path -Path $runtimeDir -ChildPath 'tracker_launcher_stderr.log'
$baseUrl = "http://$ApiHost`:$ApiPort"
$dashboardUrl = "$baseUrl/dashboard/live/$SessionId"
$finalLaunchProfile = 'FINAL_LIVE'
$env:PHOENIXGUARD_PROFILE = $finalLaunchProfile
$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS = '0'
$env:PHOENIXGUARD_LIVE_EXECUTION_ENABLED = if ($env:PHOENIXGUARD_LIVE_EXECUTION_ENABLED) { $env:PHOENIXGUARD_LIVE_EXECUTION_ENABLED } else { '0' }
$env:PHOENIXGUARD_BROKER_WINDOW_HWND = "$BrokerWindowHwnd"
$env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL = if ($env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL) { $env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL } else { '0' }
$env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS = if ($env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS) { $env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS } else { '1' }
$env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N = if ($env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N) { $env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N } else { '300' }
$env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH = if ($env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH) { $env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH } else { '960' }
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT = if ($env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT) { $env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT } else { '1' }
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC = if ($env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC) { $env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC } else { '15.0' }
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_POLL_SEC = if ($env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_POLL_SEC) { $env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_POLL_SEC } else { '15.0' }
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_TIMEOUT_SEC = if ($env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_TIMEOUT_SEC) { $env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_TIMEOUT_SEC } else { '1.0' }
$env:PHOENIXGUARD_COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC = if ($env:PHOENIXGUARD_COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC) { $env:PHOENIXGUARD_COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC } else { '20.0' }
$env:PHOENIXGUARD_TRACKER_ARTIFACT_PRUNE_INTERVAL_SEC = if ($env:PHOENIXGUARD_TRACKER_ARTIFACT_PRUNE_INTERVAL_SEC) { $env:PHOENIXGUARD_TRACKER_ARTIFACT_PRUNE_INTERVAL_SEC } else { '300.0' }
$env:PHOENIXGUARD_FAST_FOCUS_PREVIEW = if ($env:PHOENIXGUARD_FAST_FOCUS_PREVIEW) { $env:PHOENIXGUARD_FAST_FOCUS_PREVIEW } else { '1' }
$env:PHOENIXGUARD_RUNTIME_DIR = $runtimeDir
$env:PHOENIXGUARD_DATA_DIR = Join-Path -Path $runtimeDir -ChildPath 'data_live'
$env:PHOENIXGUARD_LOGS_DIR = Join-Path -Path $runtimeDir -ChildPath 'logs_live'
$env:PHOENIXGUARD_TRACKER_STATUS_FILE = Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'

$launchShooter = @('FULL', 'FULL_V3_VALIDATION', 'FULL_V3_SHOOTER_ATTACHED') -contains $Profile
$launchMt4Bridge = -not ($env:PHOENIXGUARD_MT4_BRIDGE_ENABLED -and $env:PHOENIXGUARD_MT4_BRIDGE_ENABLED.Trim().ToLowerInvariant() -in @('0', 'false', 'off', 'no'))
$startupTestSignal = $false
$brokerClickPath = 'RETIRED_PACKAGE_REPORTER_ONLY'

function Get-PhoenixGuardBrowserExecutable {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('chrome', 'edge')]
        [string]$BrowserName
    )

    $candidatePaths = @()
    $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($BrowserName -eq 'chrome') {
        if ($env:ProgramFiles) {
            $candidatePaths += Join-Path -Path $env:ProgramFiles -ChildPath 'Google\Chrome\Application\chrome.exe'
        }
        if ($programFilesX86) {
            $candidatePaths += Join-Path -Path $programFilesX86 -ChildPath 'Google\Chrome\Application\chrome.exe'
        }
        if ($env:LOCALAPPDATA) {
            $candidatePaths += Join-Path -Path $env:LOCALAPPDATA -ChildPath 'Google\Chrome\Application\chrome.exe'
        }
    } elseif ($BrowserName -eq 'edge') {
        if ($env:ProgramFiles) {
            $candidatePaths += Join-Path -Path $env:ProgramFiles -ChildPath 'Microsoft\Edge\Application\msedge.exe'
        }
        if ($programFilesX86) {
            $candidatePaths += Join-Path -Path $programFilesX86 -ChildPath 'Microsoft\Edge\Application\msedge.exe'
        }
        if ($env:LOCALAPPDATA) {
            $candidatePaths += Join-Path -Path $env:LOCALAPPDATA -ChildPath 'Microsoft\Edge\Application\msedge.exe'
        }
    }

    foreach ($candidatePath in $candidatePaths) {
        if ($candidatePath -and (Test-Path -LiteralPath $candidatePath)) {
            return [string]$candidatePath
        }
    }
    return ''
}

function Get-PhoenixGuardDashboardBrowserArguments {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('chrome', 'edge')]
        [string]$BrowserName,
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    $arguments = New-Object 'System.Collections.Generic.List[string]'
    if ($BrowserName -eq 'chrome') {
        $profileDir = if ($env:PHOENIXGUARD_DASHBOARD_CHROME_PROFILE_DIR) {
            [string]$env:PHOENIXGUARD_DASHBOARD_CHROME_PROFILE_DIR
        } else {
            Join-Path -Path $ProjectRoot -ChildPath '.codex_runtime\chrome_dashboard_profile'
        }
        New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
        $arguments.Add("--user-data-dir=$profileDir")
        $arguments.Add('--disable-background-timer-throttling')
        $arguments.Add('--disable-renderer-backgrounding')
        $arguments.Add('--disable-backgrounding-occluded-windows')
        $arguments.Add('--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,BackForwardCache')
        $arguments.Add('--new-window')
    }
    $arguments.Add($Url)
    return [string[]]$arguments.ToArray()
}

function Start-PhoenixGuardDashboardBrowser {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [ValidateSet('chrome', 'default', 'edge')]
        [string]$BrowserName
    )

    if ($BrowserName -eq 'default') {
        Start-Process $Url
        return
    }

    $browserPath = Get-PhoenixGuardBrowserExecutable -BrowserName $BrowserName
    if ($browserPath) {
        Start-Process -FilePath $browserPath -ArgumentList (Get-PhoenixGuardDashboardBrowserArguments -BrowserName $BrowserName -Url $Url)
        return
    }

    Write-Warning "Configured dashboard browser '$BrowserName' was not found. Falling back to the Windows default browser."
    Start-Process $Url
}

function ConvertTo-PhoenixGuardProcessArgumentString {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

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

Write-Host "PhoenixGuard launch profile: $finalLaunchProfile"
Write-Host "  Compatibility profile: $Profile"
Write-Host "  Tracker: ON"
Write-Host "  Model Council V3: ON"
Write-Host "  Market Reality: ON"
Write-Host "  Legacy V1/V2: OFF"
Write-Host "  Execution Packet Publisher: ON (STUDY_PACKET every council cycle; PG_EXECUTION_PACKET_V3 only when executable)"
Write-Host "  Shooter: $(if ($launchShooter) { 'ON' } else { 'OFF' })"
Write-Host "  Startup Test Signal: REMOVED"
Write-Host "  Broker Click Path: $brokerClickPath"

if (-not (Test-Path -LiteralPath $runtimeDir)) {
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
}

function Start-TrackerChildProcess {
    $trackerArgs = @(
        'Backend\launch\start_phoenixguard_24_7_tracker.py',
        '--host',
        $ApiHost,
        '--port',
        "$ApiPort",
        '--session-id',
        $SessionId,
        '--window-query',
        $BrokerWindowQuery,
        '--focus-region',
        $TrackerFocusRegion,
        '--capture-interval',
        "$CaptureIntervalSec",
        '--dashboard-browser',
        $DashboardBrowser,
        '--no-open-dashboard'
    )
    if ($BrokerWindowHwnd -gt 0) {
        $trackerArgs += @('--window-hwnd', "$BrokerWindowHwnd")
    }

    Start-Process -FilePath $pythonProcessPath -ArgumentList (ConvertTo-PhoenixGuardProcessArgumentString -Arguments $trackerArgs) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $trackerStdoutPath -RedirectStandardError $trackerStderrPath
}

if (-not $NoKillExisting) {
    $currentPid = [int]$PID
    $targetPatterns = @(
        '*start_phoenixguard_mobile_api.py*',
        '*start_phoenixguard_24_7_tracker.ps1*',
        '*start_phoenixguard_24_7_tracker.py*',
        '*shooter.py*',
        '*phoenixguard.runtime.model_council_daemon*',
        '*uvicorn phoenixguard.mobile_api.app*',
        '*phoenixguard_mt4_file_bridge.py*',
        '*run_entry_allowance_burn.py*',
        '*manual_entry_alert*',
        '*business_mock*',
        '*next dev --hostname 127.0.0.1 --port 3210*',
        '*next start --hostname 127.0.0.1 --port 3310*',
        '*node_modules\next\dist\server\lib\start-server.js*'
    )
    for ($cleanupAttempt = 0; $cleanupAttempt -lt 3; $cleanupAttempt++) {
        $processRows = @()
        $processRowsAvailable = $false
        try {
            $processRows = @(Get-CimInstance Win32_Process)
            $processRowsAvailable = $true
        } catch {
            Write-Warning "Process command-line scan unavailable: $($_.Exception.Message). Falling back to PhoenixGuard port cleanup only."
        }
        $targetProcessIds = New-Object 'System.Collections.Generic.HashSet[int]'

        if ($processRowsAvailable) {
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
        }

        try {
            $cleanupPorts = @($ApiPort, 8793, 18181, 18180, 8787, 3210, 3310) | Select-Object -Unique
            foreach ($cleanupPort in $cleanupPorts) {
                Get-NetTCPConnection -LocalPort $cleanupPort -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
                    $ownerPid = [int]$_.OwningProcess
                    if ($ownerPid -ne $currentPid) {
                        if (-not $processRowsAvailable) {
                            [void]$targetProcessIds.Add($ownerPid)
                        } else {
                            $owner = $processRows | Where-Object { [int]$_.ProcessId -eq $ownerPid } | Select-Object -First 1
                            $ownerCommandLine = [string]$owner.CommandLine
                            if ($ownerCommandLine -like '*phoenixguard*' -or $ownerCommandLine -like '*start_phoenixguard_mobile_api.py*' -or $ownerCommandLine -like '*next*') {
                                [void]$targetProcessIds.Add($ownerPid)
                            }
                        }
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
        Write-Host "Starting shooter package reporter against $baseUrl"
        $effectiveShooterPollSec = [double]$ShooterPollSec
        $pollText = ([string]$effectiveShooterPollSec).Replace(',', '.')
        $logDir = Join-Path -Path $ProjectRoot -ChildPath '.codex_runtime\logs'
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $outPath = Join-Path -Path $logDir -ChildPath "shooter-full-local-$stamp.out.log"
        $errPath = Join-Path -Path $logDir -ChildPath "shooter-full-local-$stamp.err.log"
        $shooterArgs = @(
            'Backend\launch\shooter.py',
            'signal',
            '--session-id',
            $SessionId,
            '--base-url',
            $baseUrl,
            '--poll',
            $pollText,
            '--heartbeat',
            '4.0'
        )
        Start-Process -FilePath $pythonProcessPath -ArgumentList $shooterArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath | Out-Null
        Write-Host "Shooter package reporter log: $errPath"
    } elseif ($session) {
        Write-Host "Profile $Profile selected; tracker started without shooter."
    }
    if ($session -and $launchMt4Bridge) {
        Write-Host "Starting MT4 file bridge against $baseUrl"
        $bridgePollText = ([string][double]$ShooterPollSec).Replace(',', '.')
        $bridgeTimeoutSec = if ($env:PHOENIXGUARD_MT4_BRIDGE_TIMEOUT_SEC) { [double]$env:PHOENIXGUARD_MT4_BRIDGE_TIMEOUT_SEC } else { 30.0 }
        $bridgeTimeoutText = ([string]$bridgeTimeoutSec).Replace(',', '.')
        $bridgeLogDir = Join-Path -Path $ProjectRoot -ChildPath '.codex_runtime\logs'
        New-Item -ItemType Directory -Force -Path $bridgeLogDir | Out-Null
        $bridgeStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $bridgeOutPath = Join-Path -Path $bridgeLogDir -ChildPath "mt4-bridge-full-local-$bridgeStamp.out.log"
        $bridgeErrPath = Join-Path -Path $bridgeLogDir -ChildPath "mt4-bridge-full-local-$bridgeStamp.err.log"
        $bridgeArgs = @(
            'Backend\tools\phoenixguard_mt4_file_bridge.py',
            '--session-id',
            $SessionId,
            '--base-url',
            $baseUrl,
            '--poll-sec',
            $bridgePollText,
            '--timeout-sec',
            $bridgeTimeoutText,
            '--print-every',
            '30.0',
            '--metrics-every',
            '15.0'
        )
        Start-Process -FilePath $pythonProcessPath -ArgumentList $bridgeArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $bridgeOutPath -RedirectStandardError $bridgeErrPath | Out-Null
        Write-Host "MT4 file bridge log: $bridgeOutPath"
    } elseif ($session) {
        Write-Host "MT4 file bridge disabled by PHOENIXGUARD_MT4_BRIDGE_ENABLED=$env:PHOENIXGUARD_MT4_BRIDGE_ENABLED"
    }
} catch {
    throw "Tracker API did not become healthy at $baseUrl. Start output: $($_.Exception.Message)"
}

if (-not $NoBrowser) {
    Start-PhoenixGuardDashboardBrowser -Url $dashboardUrl -BrowserName $DashboardBrowser
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
