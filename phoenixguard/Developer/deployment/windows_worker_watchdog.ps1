[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Watchdog prints concise operator status.")]
param(
    [string]$ProjectRoot = $(if ($env:PHOENIXGUARD_PROJECT_ROOT) { $env:PHOENIXGUARD_PROJECT_ROOT } else { "C:\PhoenixGuard\phoenixguard" }),
    [string]$BaseUrl = "http://127.0.0.1:8793",
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { "pocket-live-8788" }),
    [int]$HealthTimeoutSec = 10,
    [int]$MaxCompactLiveMs = 15000
)

$ErrorActionPreference = "Stop"

function Write-WatchdogLog {
    param([string]$Message)
    $logDir = Join-Path -Path $ProjectRoot -ChildPath "runtime\live\logs_live"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -LiteralPath (Join-Path $logDir "deployment_watchdog.log") -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-Endpoint {
    param([string]$Path, [int]$TimeoutSec)
    try {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $response = Invoke-RestMethod -Uri "$BaseUrl$Path" -TimeoutSec $TimeoutSec
        $sw.Stop()
        return [pscustomobject]@{ ok = $true; ms = [int]$sw.ElapsedMilliseconds; response = $response; error = "" }
    } catch {
        return [pscustomobject]@{ ok = $false; ms = 0; response = $null; error = $_.Exception.Message }
    }
}

Set-Location $ProjectRoot

$health = Test-Endpoint -Path "/v1/mobile/health" -TimeoutSec $HealthTimeoutSec
$live = Test-Endpoint -Path "/v1/mobile/live/state/v3/$SessionId?compact=1&monitor=1" -TimeoutSec $HealthTimeoutSec

$healthy = $health.ok -and $live.ok -and $live.ms -le $MaxCompactLiveMs
if ($healthy) {
    Write-WatchdogLog "PASS health_ms=$($health.ms) compact_live_ms=$($live.ms)"
    exit 0
}

Write-WatchdogLog "WARN unhealthy health_ok=$($health.ok) live_ok=$($live.ok) live_ms=$($live.ms) health_error=$($health.error) live_error=$($live.error)"

$python = Join-Path -Path $ProjectRoot -ChildPath ".venv-live\Scripts\python.exe"
$killSwitch = Join-Path -Path $ProjectRoot -ChildPath "Developer\developer_tools\phoenixguard_kill_switch.py"
$launcher = Join-Path -Path $ProjectRoot -ChildPath "Backend\launch\launch_phoenixguard_live_ready.ps1"

if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $killSwitch)) {
    Write-WatchdogLog "Restarting through kill switch."
    & $python $killSwitch --session-id $SessionId --base-url $BaseUrl --capture-interval-sec 15 --warmup-seconds 15 --verify-timeout-sec 120
    exit $LASTEXITCODE
}

Write-WatchdogLog "Kill switch unavailable; starting launcher fallback."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $launcher -SessionId $SessionId -NoBrowser
