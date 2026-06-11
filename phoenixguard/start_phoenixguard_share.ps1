[CmdletBinding()]
param(
    [ValidateSet('FAST', 'BALANCED', 'FULL', 'HEAVY_LAZY')]
    [string]$LaunchMode = $(if ($env:PHOENIXGUARD_PROFILE) { $env:PHOENIXGUARD_PROFILE.ToUpperInvariant() } else { 'FAST' }),

    [string]$BindAddress = $(if ($env:PHOENIXGUARD_SHARE_HOST) { $env:PHOENIXGUARD_SHARE_HOST } else { '127.0.0.1' }),

    [int]$ListenPort = $(if ($env:PHOENIXGUARD_SHARE_PORT) { [int]$env:PHOENIXGUARD_SHARE_PORT } else { 7861 }),

    [ValidateSet('LAN', 'TUNNEL', 'PUBLIC')]
    [string]$AccessMode = $(
        if ($env:PHOENIXGUARD_SHARE_TUNNEL -and $env:PHOENIXGUARD_SHARE_TUNNEL.ToLowerInvariant() -in @('1', 'true', 'yes', 'on')) {
            'TUNNEL'
        } elseif ($env:PHOENIXGUARD_SHARE_HOST -and $env:PHOENIXGUARD_SHARE_HOST.ToLowerInvariant() -notin @('127.0.0.1', 'localhost', '::1')) {
            'PUBLIC'
        } else {
            'LAN'
        }
    ),

    [switch]$Public,
    [switch]$Tunnel,
    [switch]$Bootstrap
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath $PSScriptRoot

$ProjectRoot = $PSScriptRoot
$VirtualEnvPath = Join-Path -Path $ProjectRoot -ChildPath '.venv'
$ActivateScriptPath = Join-Path -Path $VirtualEnvPath -ChildPath 'Scripts\Activate.ps1'
$RequirementsFilePath = Join-Path -Path $ProjectRoot -ChildPath 'requirements.txt'
$ShareRunnerPath = Join-Path -Path $ProjectRoot -ChildPath 'share_phoenixguard.py'

if (-not (Test-Path -LiteralPath $VirtualEnvPath)) {
    py -3.11 -m venv $VirtualEnvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment at '$VirtualEnvPath'."
    }
}

if (-not (Test-Path -LiteralPath $ActivateScriptPath)) {
    throw "Virtual environment activation script not found at '$ActivateScriptPath'."
}

. $ActivateScriptPath

if ($Bootstrap) {
    if (-not (Test-Path -LiteralPath $RequirementsFilePath)) {
        throw "requirements.txt was not found at '$RequirementsFilePath'."
    }

    Write-Output "Installing Python dependencies..."
    python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }

    python -m pip install -r $RequirementsFilePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies from '$RequirementsFilePath'."
    }
}

if (-not $env:PHOENIXGUARD_SHARE_PASSWORD -and -not $env:PHOENIXGUARD_SHARE_CREDENTIALS) {
    Write-Output "Set PHOENIXGUARD_SHARE_PASSWORD and optionally PHOENIXGUARD_SHARE_USERNAME before launching share mode."
    Write-Output "Example:"
    Write-Output "  `$env:PHOENIXGUARD_SHARE_USERNAME='operator'"
    Write-Output "  `$env:PHOENIXGUARD_SHARE_PASSWORD='ChangeMe2026!'"
    exit 1
}

if ($Public -and $Tunnel) {
    throw "Choose either -Public or -Tunnel, not both."
}

if ($Public) {
    $AccessMode = 'PUBLIC'
} elseif ($Tunnel) {
    $AccessMode = 'TUNNEL'
}

$ResolvedAccessMode = $AccessMode.ToUpperInvariant()

switch ($ResolvedAccessMode) {
    'LAN' {
        if (-not $PSBoundParameters.ContainsKey('BindAddress')) {
            $BindAddress = '127.0.0.1'
        }
        $env:PHOENIXGUARD_SHARE_TUNNEL = '0'
    }
    'TUNNEL' {
        $BindAddress = '127.0.0.1'
        $env:PHOENIXGUARD_SHARE_TUNNEL = '1'
        $env:PHOENIXGUARD_SHARE_STRICT_PASSWORDS = '1'
    }
    'PUBLIC' {
        $BindAddress = '0.0.0.0'
        $env:PHOENIXGUARD_SHARE_TUNNEL = '0'
        $env:PHOENIXGUARD_SHARE_STRICT_PASSWORDS = '1'
    }
}

$env:PHOENIXGUARD_PROFILE = $LaunchMode.ToUpperInvariant()
$env:PHOENIXGUARD_SHARE_MODE = '1'
$env:PHOENIXGUARD_SHARE_HOST = $BindAddress
$env:PHOENIXGUARD_SHARE_PORT = [string]$ListenPort
$env:PHOENIXGUARD_SHARE_ACCESS_MODE = $ResolvedAccessMode

if (-not (Test-Path -LiteralPath $ShareRunnerPath)) {
    throw "share_phoenixguard.py was not found at '$ShareRunnerPath'."
}

switch ($ResolvedAccessMode) {
    'LAN' {
        Write-Output "Access mode: LAN. PhoenixGuard stays local unless you add your own reverse proxy or tunnel."
    }
    'TUNNEL' {
        Write-Output "Access mode: TUNNEL. PhoenixGuard stays on 127.0.0.1 and Gradio will generate a temporary public HTTPS link."
        Write-Output "Send the share URL plus the PhoenixGuard credentials only to people you trust."
    }
    'PUBLIC' {
        Write-Output "Access mode: PUBLIC. PhoenixGuard will listen on 0.0.0.0 for LAN or reverse-proxy use."
        Write-Output "This does not make the app worldwide by itself. You still need router port forwarding or a tunnel such as Cloudflare."
    }
}

Write-Output "Launching PhoenixGuard share mode on $($env:PHOENIXGUARD_SHARE_HOST):$($env:PHOENIXGUARD_SHARE_PORT) with access mode $ResolvedAccessMode"
python $ShareRunnerPath

if ($LASTEXITCODE -ne 0) {
    throw "PhoenixGuard share process exited with code $LASTEXITCODE."
}
