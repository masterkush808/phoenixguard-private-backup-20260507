[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Deployment bootstrap prints operator actions.")]
param(
    [string]$RepoUrl = "https://github.com/masterkush808/phoenixguard-private-backup-20260507.git",
    [string]$InstallRoot = "C:\PhoenixGuard",
    [string]$Branch = "main",
    [string]$SessionId = "pocket-live-8788",
    [string]$BrokerWindowQuery = "The Most Innovative Trading Platform",
    [double]$CaptureIntervalSec = 15.0,
    [ValidateSet("chrome", "edge", "default")]
    [string]$DashboardBrowser = "edge",
    [string]$PublicBaseUrl = "",
    [switch]$RegisterScheduledTasks
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[PhoenixGuard Deploy] $Message"
}

function Require-Command {
    param([string]$Name, [string]$InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required. $InstallHint"
    }
}

Write-Step "Checking required tools."
Require-Command git "Install Git for Windows, then rerun this script."
Require-Command py "Install Python 3.11 x64 from python.org, then rerun this script."

$repoRoot = Join-Path -Path $InstallRoot -ChildPath "phoenixguard"
if (-not (Test-Path -LiteralPath $InstallRoot)) {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot ".git"))) {
    Write-Step "Cloning repo into $repoRoot."
    git clone --branch $Branch $RepoUrl $repoRoot
} else {
    Write-Step "Updating existing repo at $repoRoot."
    Push-Location $repoRoot
    git fetch origin
    git checkout $Branch
    git pull --ff-only origin $Branch
    Pop-Location
}

Set-Location $repoRoot

Write-Step "Installing live Python environment."
& ".\Backend\scripts_runtime\env\install_live.ps1"

$envFile = Join-Path -Path $repoRoot -ChildPath ".env.production.local"
$envLines = @(
    "PHOENIXGUARD_TRACKER_SESSION_ID=$SessionId",
    "PHOENIXGUARD_BROKER_WINDOW_QUERY=$BrokerWindowQuery",
    "PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC=$CaptureIntervalSec",
    "PHOENIXGUARD_DASHBOARD_BROWSER=$DashboardBrowser",
    "PHOENIXGUARD_MOBILE_API_HOST=127.0.0.1",
    "PHOENIXGUARD_MOBILE_API_PORT=8793",
    "PHOENIXGUARD_RUNTIME_DIR=$repoRoot\runtime\live",
    "PHOENIXGUARD_DATA_DIR=$repoRoot\runtime\live\data_live",
    "PHOENIXGUARD_LOGS_DIR=$repoRoot\runtime\live\logs_live"
)
if ($PublicBaseUrl.Trim()) {
    $envLines += "PHOENIXGUARD_PUBLIC_BASE_URL=$PublicBaseUrl"
}
$envLines | Set-Content -LiteralPath $envFile -Encoding ASCII
Write-Step "Wrote $envFile."

[Environment]::SetEnvironmentVariable("PHOENIXGUARD_PROJECT_ROOT", $repoRoot, "User")
[Environment]::SetEnvironmentVariable("PHOENIXGUARD_TRACKER_SESSION_ID", $SessionId, "User")
[Environment]::SetEnvironmentVariable("PHOENIXGUARD_BROKER_WINDOW_QUERY", $BrokerWindowQuery, "User")
[Environment]::SetEnvironmentVariable("PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC", [string]$CaptureIntervalSec, "User")
[Environment]::SetEnvironmentVariable("PHOENIXGUARD_DASHBOARD_BROWSER", $DashboardBrowser, "User")

$runtimeDir = Join-Path -Path $repoRoot -ChildPath "runtime\live"
$logsDir = Join-Path -Path $runtimeDir -ChildPath "logs_live"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

if ($RegisterScheduledTasks) {
    Write-Step "Registering interactive scheduled tasks."
    $launcher = Join-Path -Path $repoRoot -ChildPath "Backend\launch\launch_phoenixguard_live_ready.ps1"
    $watchdog = Join-Path -Path $repoRoot -ChildPath "Developer\deployment\windows_worker_watchdog.ps1"

    $launchArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -SessionId `"$SessionId`" -BrokerWindowQuery `"$BrokerWindowQuery`" -CaptureIntervalSec $CaptureIntervalSec -DashboardBrowser $DashboardBrowser -NoBrowser"
    $watchArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$watchdog`" -ProjectRoot `"$repoRoot`" -SessionId `"$SessionId`""

    $launchAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $launchArgs -WorkingDirectory $repoRoot
    $launchTrigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName "PhoenixGuard-Live-Stack" -Action $launchAction -Trigger $launchTrigger -Description "Starts PhoenixGuard live tracker/API/package reporter at worker logon." -Force | Out-Null

    $watchAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $watchArgs -WorkingDirectory $repoRoot
    $watchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName "PhoenixGuard-Live-Watchdog" -Action $watchAction -Trigger $watchTrigger -Description "Checks PhoenixGuard health and restarts the live stack if needed." -Force | Out-Null
}

Write-Step "Verifying live environment."
& ".\.venv-live\Scripts\python.exe" ".\Backend\tools\verify_single_venv_runtime.py"

Write-Step "Bootstrap complete."
Write-Step "Next: RDP into this worker, open the broker chart in Edge/Chrome, then run Backend\launch\launch_phoenixguard_live_ready.ps1 -NoBrowser or log off/on to trigger the task."
