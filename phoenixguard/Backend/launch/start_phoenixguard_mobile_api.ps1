param(
    [string]$Host = $(if ($env:PHOENIXGUARD_MOBILE_API_HOST) { $env:PHOENIXGUARD_MOBILE_API_HOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PHOENIXGUARD_MOBILE_API_PORT) { [int]$env:PHOENIXGUARD_MOBILE_API_PORT } else { 8793 }),
    [switch]$Bootstrap
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $ProjectRoot
$backendSrc = Join-Path -Path $ProjectRoot -ChildPath 'Backend\src'
$backendRoot = Join-Path -Path $ProjectRoot -ChildPath 'Backend'
$backendCompat = Join-Path -Path $ProjectRoot -ChildPath 'Backend\compat'
$backendLaunch = Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch'
$frontendDashboard = Join-Path -Path $ProjectRoot -ChildPath 'Frontend\dashboard'
$env:PYTHONPATH = (@($backendSrc, $backendRoot, $backendCompat, $backendLaunch, $frontendDashboard, $ProjectRoot, $env:PYTHONPATH) | Where-Object { $_ -and [string]$_ -ne '' }) -join [System.IO.Path]::PathSeparator
$env:PHOENIXGUARD_PROJECT_ROOT = $ProjectRoot

if (-not (Test-Path -LiteralPath '.venv')) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment at '$ProjectRoot\.venv'."
    }
}

$ActivateScriptPath = Join-Path -Path $ProjectRoot -ChildPath '.venv\Scripts\Activate.ps1'
if (-not (Test-Path -LiteralPath $ActivateScriptPath)) {
    throw "Virtual environment activation script not found at '$ActivateScriptPath'."
}
. $ActivateScriptPath

. (Join-Path -Path $PSScriptRoot -ChildPath 'Resolve-PhoenixGuardPython.ps1')
$pythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $ProjectRoot
$PythonPath = [string]$pythonRuntime.VenvPython
$PythonProcessPath = [string]$pythonRuntime.ProcessPython
$runtimeDir = Join-Path -Path $ProjectRoot -ChildPath '.codex_runtime'
$env:PHOENIXGUARD_RUNTIME_DIR = $runtimeDir
$env:PHOENIXGUARD_DATA_DIR = Join-Path -Path $runtimeDir -ChildPath 'data_live'
$env:PHOENIXGUARD_LOGS_DIR = Join-Path -Path $runtimeDir -ChildPath 'logs_live'
$env:PHOENIXGUARD_TRACKER_STATUS_FILE = Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'

if ($Bootstrap) {
    Write-Host "Installing Python dependencies for the mobile API..."
    & $PythonProcessPath -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }
    & $PythonProcessPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies from 'requirements.txt'."
    }
}

$env:PHOENIXGUARD_MOBILE_API_HOST = $Host
$env:PHOENIXGUARD_MOBILE_API_PORT = "$Port"

Write-Host "Launching PhoenixGuard Mobile API at http://$Host`:$Port"
& $PythonProcessPath Backend\launch\start_phoenixguard_mobile_api.py
if ($LASTEXITCODE -ne 0) {
    throw "PhoenixGuard Mobile API exited with code $LASTEXITCODE."
}
