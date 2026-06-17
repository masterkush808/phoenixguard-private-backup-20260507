[CmdletBinding()]
param(
    [string]$TaskName = 'PhoenixGuard VM Monitor',
    [string]$ConfigPath = '',
    [switch]$BootstrapOnFirstRun,
    [switch]$StartNow,
    [switch]$RunAtStartupAsSystem
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScriptPath = (Resolve-Path -LiteralPath (Join-Path -Path $script:ScriptRoot -ChildPath 'Start-PhoenixGuardVmMonitor.ps1')).Path
$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source

if (-not $ConfigPath) {
    $ConfigPath = Join-Path -Path $script:ScriptRoot -ChildPath 'phoenixguard.vm-monitor.env.ps1'
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "VM monitor config not found at '$ConfigPath'. Copy phoenixguard.vm-monitor.env.example.ps1 first."
}
$ResolvedConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path

$principalIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($principalIdentity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($RunAtStartupAsSystem -and -not $isAdministrator) {
    throw 'Run this script from an elevated PowerShell session to register a SYSTEM startup task.'
}

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
$taskTrigger = if ($RunAtStartupAsSystem) {
    New-ScheduledTaskTrigger -AtStartup
} else {
    New-ScheduledTaskTrigger -AtLogOn -User $principalIdentity.Name
}
$taskPrincipal = if ($RunAtStartupAsSystem) {
    New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
} else {
    New-ScheduledTaskPrincipal -UserId $principalIdentity.Name -LogonType Interactive -RunLevel Limited
}
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

$description = if ($RunAtStartupAsSystem) {
    'Starts PhoenixGuard tracker/shooter monitor at Windows startup. Use only for non-desktop API checks because SYSTEM cannot see the broker desktop.'
} else {
    'Starts PhoenixGuard tracker/shooter monitor when the VM operator logs on, preserving desktop capture and broker-click access.'
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description $description `
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
