[CmdletBinding()]
param(
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

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $ProjectRoot
$RequirementsFilePath = Join-Path -Path $ProjectRoot -ChildPath 'requirements.txt'
$backendSrc = Join-Path -Path $ProjectRoot -ChildPath 'Backend\src'
$backendRoot = Join-Path -Path $ProjectRoot -ChildPath 'Backend'
$backendCompat = Join-Path -Path $ProjectRoot -ChildPath 'Backend\compat'
$frontendDashboard = Join-Path -Path $ProjectRoot -ChildPath 'Frontend\dashboard'
$env:PYTHONPATH = (@($backendSrc, $backendRoot, $backendCompat, $frontendDashboard, $ProjectRoot, $env:PYTHONPATH) | Where-Object { $_ -and [string]$_ -ne '' }) -join [System.IO.Path]::PathSeparator
$env:PHOENIXGUARD_PROJECT_ROOT = $ProjectRoot
$ShareRunnerPath = Join-Path -Path $ProjectRoot -ChildPath 'Frontend\dashboard\share_phoenixguard.py'

. (Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\Resolve-PhoenixGuardPython.ps1')
$pythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $ProjectRoot
$PythonPath = [string]$pythonRuntime.VenvPython
$PythonProcessPath = [string]$pythonRuntime.ProcessPython

if ($Bootstrap) {
    if (-not (Test-Path -LiteralPath $RequirementsFilePath)) {
        throw "requirements.txt was not found at '$RequirementsFilePath'."
    }

    Write-Output "Installing Python dependencies..."
    & $PythonProcessPath -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }

    & $PythonProcessPath -m pip install -r $RequirementsFilePath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies from '$RequirementsFilePath'."
    }
}

if (-not $env:PHOENIXGUARD_SHARE_PASSWORD -and -not $env:PHOENIXGUARD_SHARE_CREDENTIALS) {
    Write-Output "Set PHOENIXGUARD_SHARE_PASSWORD and optionally PHOENIXGUARD_SHARE_USERNAME before launching 808Fx Standard System share mode."
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

$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'
$env:PHOENIXGUARD_SHARE_MODE = '1'
$env:PHOENIXGUARD_SHARE_HOST = $BindAddress
$env:PHOENIXGUARD_SHARE_PORT = [string]$ListenPort
$env:PHOENIXGUARD_SHARE_ACCESS_MODE = $ResolvedAccessMode
$env:PHOENIXGUARD_UI_HOST = $BindAddress
$env:PHOENIXGUARD_UI_PORT = [string]$ListenPort
$env:PHOENIXGUARD_UI_SHARE = $(if ($ResolvedAccessMode -eq 'TUNNEL') { '1' } else { '0' })
$env:PHOENIXGUARD_UI_REQUIRE_AUTH = '1'
$env:PHOENIXGUARD_UI_STRICT_PASSWORDS = $(if ($ResolvedAccessMode -in @('TUNNEL', 'PUBLIC')) { '1' } else { '0' })
$env:PHOENIXGUARD_UI_OPEN_BROWSER = '0'
$env:PHOENIXGUARD_UI_SHOW_ERROR = '0'

if (-not (Test-Path -LiteralPath $ShareRunnerPath)) {
    throw "Frontend\dashboard\share_phoenixguard.py was not found at '$ShareRunnerPath'."
}

switch ($ResolvedAccessMode) {
    'LAN' {
        Write-Output "Access mode: LAN. The canonical 808Fx Standard System stays on the host unless you add your own reverse proxy or tunnel."
    }
    'TUNNEL' {
        Write-Output "Access mode: TUNNEL. The canonical 808Fx Standard System stays on 127.0.0.1 and Gradio will generate a temporary public HTTPS link."
        Write-Output "Send the share URL plus the 808Fx Standard System credentials only to people you trust."
    }
    'PUBLIC' {
        Write-Output "Access mode: PUBLIC. The canonical 808Fx Standard System will listen on 0.0.0.0 for LAN or reverse-proxy use."
        Write-Output "This does not make the app worldwide by itself. You still need router port forwarding or a tunnel such as Cloudflare."
    }
}

Write-Output "Launching 808Fx Standard System premium share surface on $($env:PHOENIXGUARD_UI_HOST):$($env:PHOENIXGUARD_UI_PORT) with access mode $ResolvedAccessMode"
& $PythonProcessPath $ShareRunnerPath

if ($LASTEXITCODE -ne 0) {
    throw "808Fx Standard System exited with code $LASTEXITCODE."
}
