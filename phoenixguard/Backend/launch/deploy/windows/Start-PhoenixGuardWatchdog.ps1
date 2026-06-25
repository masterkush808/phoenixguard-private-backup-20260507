[CmdletBinding()]
param(
    [switch]$Bootstrap,

    [switch]$RunTests,

    [switch]$CheckHF,

    [ValidateRange(0, 3600)]
    [int]$RestartDelaySeconds = $(if ($env:PHOENIXGUARD_WATCHDOG_RESTART_DELAY_SEC) { [int]$env:PHOENIXGUARD_WATCHDOG_RESTART_DELAY_SEC } else { 10 }),

    [ValidateRange(0, 100000)]
    [int]$MaxRestartCount = $(if ($env:PHOENIXGUARD_WATCHDOG_MAX_RESTARTS) { [int]$env:PHOENIXGUARD_WATCHDOG_MAX_RESTARTS } else { 0 }),

    [string]$LogPath = '',

    [switch]$StopOnCleanExit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path -Path $script:ScriptRoot -ChildPath '..\..')).Path
$LauncherPath = Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\start_phoenixguard.ps1'
$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $LauncherPath)) {
    throw "PhoenixGuard launcher not found at '$LauncherPath'."
}

if (-not $LogPath) {
    $LogPath = Join-Path -Path $script:ScriptRoot -ChildPath 'logs\phoenixguard-watchdog.log'
}

$ResolvedLogDir = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $ResolvedLogDir)) {
    New-Item -ItemType Directory -Path $ResolvedLogDir -Force | Out-Null
}

function Write-WatchdogLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $line = "[{0}] {1}" -f (Get-Date -Format o), $Message
    Write-Output $line
    Add-Content -LiteralPath $LogPath -Value $line
}

$argumentParts = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', "`"$LauncherPath`""
)

if ($Bootstrap) {
    $argumentParts += '-Bootstrap'
}

if ($RunTests) {
    $argumentParts += '-RunTests'
}

if ($CheckHF) {
    $argumentParts += '-CheckHF'
}

$restartCount = 0

while ($true) {
    $attemptNumber = $restartCount + 1
    Write-WatchdogLog "Starting PhoenixGuard attempt $attemptNumber with FINAL_LIVE."

    & $PowerShellExe @argumentParts *>> $LogPath
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0 -and $StopOnCleanExit) {
        Write-WatchdogLog 'PhoenixGuard exited cleanly. Watchdog is stopping because StopOnCleanExit was requested.'
        exit 0
    }

    if ($exitCode -eq 0) {
        Write-WatchdogLog 'PhoenixGuard exited cleanly. Watchdog will relaunch it to keep the desk available.'
    } else {
        Write-WatchdogLog "PhoenixGuard exited with code $exitCode. Watchdog will attempt a restart."
    }

    if ($MaxRestartCount -gt 0 -and $restartCount -ge $MaxRestartCount) {
        Write-WatchdogLog "Reached the configured restart limit ($MaxRestartCount). Watchdog is stopping."
        exit $exitCode
    }

    $restartCount += 1
    if ($RestartDelaySeconds -gt 0) {
        Write-WatchdogLog "Waiting $RestartDelaySeconds second(s) before restart."
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}
