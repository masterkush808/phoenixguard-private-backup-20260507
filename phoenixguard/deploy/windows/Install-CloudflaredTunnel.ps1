[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TunnelToken,
    [switch]$ReinstallService
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$principalIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($principalIdentity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) {
        throw 'Run this script from an elevated PowerShell session.'
    }

    $powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
    $argumentParts = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', "`"$scriptPath`"",
        '-TunnelToken', "`"$TunnelToken`""
    )
    if ($ReinstallService) {
        $argumentParts += '-ReinstallService'
    }

    $elevated = Start-Process -FilePath $powerShellExe -Verb RunAs -ArgumentList ($argumentParts -join ' ') -Wait -PassThru
    if ($null -eq $elevated) {
        throw 'The elevated cloudflared installer did not start.'
    }
    if ($elevated.ExitCode -ne 0) {
        throw "The elevated cloudflared installer exited with code $($elevated.ExitCode)."
    }
    return
}

$cloudflaredCommand = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
if (-not $cloudflaredCommand) {
    $wingetCommand = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $wingetCommand) {
        throw 'cloudflared.exe is not installed and winget is unavailable. Install cloudflared first.'
    }

    Write-Output 'Installing cloudflared with winget...'
    & $wingetCommand.Source install --id Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw 'winget failed to install cloudflared.'
    }

    $cloudflaredCommand = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if (-not $cloudflaredCommand) {
        throw 'cloudflared.exe is still unavailable after winget install.'
    }
}

$cloudflaredExe = $cloudflaredCommand.Source

if ($ReinstallService) {
    Write-Output 'Removing the existing cloudflared service if present...'
    try {
        & $cloudflaredExe service uninstall | Out-Null
    }
    catch {
        Write-Output 'No existing cloudflared service was removed.'
    }
}

Write-Output 'Installing the cloudflared Windows service for the remotely-managed tunnel...'
& $cloudflaredExe service install $TunnelToken
if ($LASTEXITCODE -ne 0) {
    throw 'cloudflared service install failed.'
}

Write-Output 'cloudflared service installation completed.'
