[CmdletBinding()]
param(
    [string]$TaskName = 'PhoenixGuard Watchdog',

    [ValidateRange(0, 3600)]
    [int]$RestartDelaySeconds = 10,

    [ValidateRange(0, 100000)]
    [int]$MaxRestartCount = 0,

    [string]$LogPath = '',

    [switch]$RunAsCurrentUser,

    [switch]$BootstrapOnFirstRun,

    [switch]$RunTests,

    [switch]$CheckHF,

    [switch]$StopOnCleanExit,

    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WatchdogPath = (Resolve-Path -LiteralPath (Join-Path -Path $script:ScriptRoot -ChildPath 'Start-PhoenixGuardWatchdog.ps1')).Path
$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source

$principalIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($principalIdentity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $RunAsCurrentUser -and -not $isAdministrator) {
    throw 'Run this script from an elevated PowerShell session, or pass -RunAsCurrentUser for a per-user task.'
}

$argumentParts = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', "`"$WatchdogPath`"",
    '-RestartDelaySeconds', [string]$RestartDelaySeconds,
    '-MaxRestartCount', [string]$MaxRestartCount
)

if ($LogPath) {
    $argumentParts += @('-LogPath', "`"$LogPath`"")
}

if ($BootstrapOnFirstRun) {
    $argumentParts += '-Bootstrap'
}

if ($RunTests) {
    $argumentParts += '-RunTests'
}

if ($CheckHF) {
    $argumentParts += '-CheckHF'
}

if ($StopOnCleanExit) {
    $argumentParts += '-StopOnCleanExit'
}

$taskAction = New-ScheduledTaskAction -Execute $PowerShellExe -Argument ($argumentParts -join ' ')
$taskTrigger = if ($RunAsCurrentUser) { New-ScheduledTaskTrigger -AtLogOn -User $principalIdentity.Name } else { New-ScheduledTaskTrigger -AtStartup }
$taskPrincipal = if ($RunAsCurrentUser) {
    New-ScheduledTaskPrincipal -UserId $principalIdentity.Name -LogonType Interactive -RunLevel Limited
} else {
    New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
}
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description $(if ($RunAsCurrentUser) { 'Keeps the PhoenixGuard desk running for the current user by relaunching it after exits.' } else { 'Keeps the PhoenixGuard desk running at system startup by relaunching it after exits.' }) `
    -Action $taskAction `
    -Trigger $taskTrigger `
    -Principal $taskPrincipal `
    -Settings $taskSettings `
    -Force | Out-Null

Write-Output "Scheduled task '$TaskName' is registered."

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Scheduled task '$TaskName' was started."
}
