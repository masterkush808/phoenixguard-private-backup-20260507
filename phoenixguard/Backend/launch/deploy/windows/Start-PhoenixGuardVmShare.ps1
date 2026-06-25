[CmdletBinding()]
param(
    [string]$ConfigPath = '',
    [switch]$Bootstrap
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $ConfigPath) {
    $ConfigPath = Join-Path -Path $script:ScriptRoot -ChildPath 'phoenixguard.vm-share.env.ps1'
}

$ProjectRoot = (Resolve-Path (Join-Path -Path $script:ScriptRoot -ChildPath '..\..')).Path
$ShareLauncherPath = Join-Path -Path $ProjectRoot -ChildPath 'start_phoenixguard_share.ps1'

if (-not (Test-Path -LiteralPath $ShareLauncherPath)) {
    throw "PhoenixGuard share launcher not found at '$ShareLauncherPath'."
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "VM share config not found at '$ConfigPath'. Copy phoenixguard.vm-share.env.example.ps1 first."
}

$ResolvedConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
. $ResolvedConfigPath

if (-not $env:PHOENIXGUARD_SHARE_PASSWORD -and -not $env:PHOENIXGUARD_SHARE_CREDENTIALS) {
    throw "Set PHOENIXGUARD_SHARE_PASSWORD or PHOENIXGUARD_SHARE_CREDENTIALS in '$ResolvedConfigPath'."
}

if (-not $env:PHOENIXGUARD_SHARE_PORT) {
    $env:PHOENIXGUARD_SHARE_PORT = '7861'
}

$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'

Push-Location -LiteralPath $ProjectRoot
try {
    & $ShareLauncherPath `
        -BindAddress '127.0.0.1' `
        -ListenPort ([int]$env:PHOENIXGUARD_SHARE_PORT) `
        -AccessMode LAN `
        -Bootstrap:$Bootstrap.IsPresent
}
finally {
    Pop-Location
}
