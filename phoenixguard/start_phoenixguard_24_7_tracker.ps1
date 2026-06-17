[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Interactive launcher prints concise status lines.")]
param(
    [string]$ApiHost = $(if ($env:PHOENIXGUARD_MOBILE_API_HOST) { $env:PHOENIXGUARD_MOBILE_API_HOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PHOENIXGUARD_MOBILE_API_PORT) { [int]$env:PHOENIXGUARD_MOBILE_API_PORT } else { 8793 }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
    [string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { 'Pocket Option' }),
    [int]$BrokerWindowHwnd = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_HWND) { [int]$env:PHOENIXGUARD_BROKER_WINDOW_HWND } else { 0 }),
    [string]$FocusRegion = $(if ($env:PHOENIXGUARD_TRACKER_FOCUS_REGION) { $env:PHOENIXGUARD_TRACKER_FOCUS_REGION } else { '0.03,0.13,0.87,0.96' }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 1.0 }),
    [switch]$NoOpenDashboard,
    [switch]$NoWaitForLock
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

$env:PHOENIXGUARD_MOBILE_API_HOST = $ApiHost
$env:PHOENIXGUARD_MOBILE_API_PORT = "$Port"
$env:PHOENIXGUARD_TRACKER_SESSION_ID = $SessionId
$env:PHOENIXGUARD_BROKER_WINDOW_QUERY = $BrokerWindowQuery
$env:PHOENIXGUARD_BROKER_WINDOW_HWND = "$BrokerWindowHwnd"
$env:PHOENIXGUARD_TRACKER_FOCUS_REGION = $FocusRegion
$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC = "$CaptureIntervalSec"

$launcherArgs = @(
    (Join-Path $PSScriptRoot 'start_phoenixguard_24_7_tracker.py'),
    '--host', $ApiHost,
    '--port', "$Port",
    '--session-id', $SessionId,
    '--window-query', $BrokerWindowQuery,
    '--focus-region', $FocusRegion,
    '--capture-interval', "$CaptureIntervalSec"
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

python @launcherArgs
if ($LASTEXITCODE -ne 0) {
    throw "PhoenixGuard 24/7 tracker exited with code $LASTEXITCODE."
}
