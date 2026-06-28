[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Interactive launcher prints concise status lines.")]
param(
    [string]$ApiHost = $(if ($env:PHOENIXGUARD_MOBILE_API_HOST) { $env:PHOENIXGUARD_MOBILE_API_HOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PHOENIXGUARD_MOBILE_API_PORT) { [int]$env:PHOENIXGUARD_MOBILE_API_PORT } else { 8793 }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
    [string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { 'Pocket Option' }),
    [int]$BrokerWindowHwnd = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_HWND) { [int]$env:PHOENIXGUARD_BROKER_WINDOW_HWND } else { 0 }),
    [string]$FocusRegion = $(if ($env:PHOENIXGUARD_TRACKER_FOCUS_REGION) { $env:PHOENIXGUARD_TRACKER_FOCUS_REGION } else { '0.03,0.13,0.87,0.96' }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 15.0 }),
    [ValidateSet('chrome', 'default', 'edge')]
    [string]$DashboardBrowser = $(if ($env:PHOENIXGUARD_DASHBOARD_BROWSER) { $env:PHOENIXGUARD_DASHBOARD_BROWSER } else { 'chrome' }),
    [switch]$NoOpenDashboard,
    [switch]$NoWaitForLock,
    [switch]$InternalTrackerOnly
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

. (Join-Path -Path $PSScriptRoot -ChildPath 'Resolve-PhoenixGuardPython.ps1')
$pythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $ProjectRoot
$PythonPath = [string]$pythonRuntime.VenvPython
$runtimeDir = Join-Path -Path $ProjectRoot -ChildPath 'runtime\live'
$env:PHOENIXGUARD_RUNTIME_DIR = $runtimeDir
$env:PHOENIXGUARD_DATA_DIR = Join-Path -Path $runtimeDir -ChildPath 'data_live'
$env:PHOENIXGUARD_LOGS_DIR = Join-Path -Path $runtimeDir -ChildPath 'logs_live'
$env:PHOENIXGUARD_TRACKER_STATUS_FILE = Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'

$env:PHOENIXGUARD_MOBILE_API_HOST = $ApiHost
$env:PHOENIXGUARD_MOBILE_API_PORT = "$Port"
$env:PHOENIXGUARD_TRACKER_SESSION_ID = $SessionId
$env:PHOENIXGUARD_BROKER_WINDOW_QUERY = $BrokerWindowQuery
$env:PHOENIXGUARD_BROKER_WINDOW_HWND = "$BrokerWindowHwnd"
$env:PHOENIXGUARD_TRACKER_FOCUS_REGION = $FocusRegion
$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC = "$CaptureIntervalSec"
$env:PHOENIXGUARD_DASHBOARD_BROWSER = $DashboardBrowser

if (-not $InternalTrackerOnly) {
    Write-Warning "start_phoenixguard_24_7_tracker.ps1 is now an internal tracker worker wrapper. Delegating to launch_phoenixguard_live_ready.ps1 so the live dashboard and shooter package reporter start together."
    $launchArgs = @{
        BrokerWindowQuery = $BrokerWindowQuery
        BrokerWindowHwnd = $BrokerWindowHwnd
        SessionId = $SessionId
        CaptureIntervalSec = $CaptureIntervalSec
        DashboardBrowser = $DashboardBrowser
    }
    if ($NoOpenDashboard) {
        $launchArgs['NoBrowser'] = $true
    }
    & (Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\launch_phoenixguard_live_ready.ps1') @launchArgs
    exit $LASTEXITCODE
}

$launcherArgs = @(
    (Join-Path $ProjectRoot 'Backend\launch\start_phoenixguard_24_7_tracker.py'),
    '--host', $ApiHost,
    '--port', "$Port",
    '--session-id', $SessionId,
    '--window-query', $BrokerWindowQuery,
    '--focus-region', $FocusRegion,
    '--capture-interval', "$CaptureIntervalSec",
    '--dashboard-browser', $DashboardBrowser
)

if ($BrokerWindowHwnd -gt 0) {
    $launcherArgs += @('--window-hwnd', "$BrokerWindowHwnd")
}

if ($NoOpenDashboard) {
    $launcherArgs += '--no-open-dashboard'
}

if ($NoWaitForLock) {
    $launcherArgs += '--no-wait-for-lock'
}

& $PythonPath @launcherArgs
if ($LASTEXITCODE -ne 0) {
    throw "PhoenixGuard 24/7 tracker exited with code $LASTEXITCODE."
}
