[CmdletBinding()]
param(
    [string]$TaskName = 'PhoenixGuard Share',
    [string]$ConfigPath = '',
    [switch]$RunAsCurrentUser,
    [switch]$BootstrapOnFirstRun,
    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $ConfigPath) {
    $ConfigPath = Join-Path -Path $script:ScriptRoot -ChildPath 'phoenixguard.vm-share.env.ps1'
}

$principalIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($principalIdentity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $RunAsCurrentUser -and -not $isAdministrator) {
    throw 'Run this script from an elevated PowerShell session, or pass -RunAsCurrentUser for a per-user task.'
}

$StartScriptPath = (Resolve-Path -LiteralPath (Join-Path -Path $script:ScriptRoot -ChildPath 'Start-PhoenixGuardVmShare.ps1')).Path
$ResolvedConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source

$argumentParts = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', "`"$StartScriptPath`"",
    '-ConfigPath', "`"$ResolvedConfigPath`""
)

if ($BootstrapOnFirstRun) {
    $argumentParts += '-Bootstrap'
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
    -Description $(if ($RunAsCurrentUser) { 'Starts the PhoenixGuard protected share desk for the current user at logon.' } else { 'Starts the PhoenixGuard protected share desk at VM boot.' }) `
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
