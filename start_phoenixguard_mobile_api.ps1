param(
    [string]$Host = $(if ($env:PHOENIXGUARD_MOBILE_API_HOST) { $env:PHOENIXGUARD_MOBILE_API_HOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PHOENIXGUARD_MOBILE_API_PORT) { [int]$env:PHOENIXGUARD_MOBILE_API_PORT } else { 8787 }),
    [switch]$Bootstrap
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path -LiteralPath '.venv')) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment at '$PSScriptRoot\.venv'."
    }
}

$ActivateScriptPath = Join-Path -Path $PSScriptRoot -ChildPath '.venv\Scripts\Activate.ps1'
if (-not (Test-Path -LiteralPath $ActivateScriptPath)) {
    throw "Virtual environment activation script not found at '$ActivateScriptPath'."
}

. $ActivateScriptPath

if ($Bootstrap) {
    Write-Host "Installing Python dependencies for the mobile API..."
    python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies from 'requirements.txt'."
    }
}

$env:PHOENIXGUARD_MOBILE_API_HOST = $Host
$env:PHOENIXGUARD_MOBILE_API_PORT = "$Port"

Write-Host "Launching PhoenixGuard Mobile API at http://$Host`:$Port"
python start_phoenixguard_mobile_api.py
if ($LASTEXITCODE -ne 0) {
    throw "PhoenixGuard Mobile API exited with code $LASTEXITCODE."
}
