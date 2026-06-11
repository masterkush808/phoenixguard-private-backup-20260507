param(
    [ValidateSet('FAST', 'BALANCED', 'FULL', 'HEAVY_LAZY')]
    [string]$Profile = $(if ($env:PHOENIXGUARD_PROFILE) { $env:PHOENIXGUARD_PROFILE.ToUpperInvariant() } else { 'FAST' }),
    [switch]$Bootstrap,
    [switch]$RunTests,
    [switch]$CheckHF
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    py -3.11 -m venv .venv
}

& .\.venv\Scripts\Activate.ps1

if ($Bootstrap) {
    Write-Host "Installing Python dependencies..."
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
}

if (-not $env:PHOENIXGUARD_PASSPHRASE) {
    Write-Host "Set PHOENIXGUARD_PASSPHRASE in your environment for production security."
}

if ($RunTests) {
    Write-Host "Running test suite..."
    python -m pytest -q
}

if ($CheckHF) {
    if ($env:HF_TOKEN) {
        Write-Host "Validating Hugging Face model access..."
        python hf_model_check.py --token $env:HF_TOKEN
    } else {
        Write-Host "HF_TOKEN not set, skipping remote HF access validation."
    }
}

$env:PHOENIXGUARD_PROFILE = $Profile.ToUpperInvariant()
Write-Host "Launching PhoenixGuard with profile: $($env:PHOENIXGUARD_PROFILE)"

python main.py
