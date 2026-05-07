param(
    [ValidateSet('FAST', 'BALANCED', 'FULL', 'HEAVY_LAZY')]
    [Alias('Profile')]
    [string]$LaunchMode = $(if ($env:PHOENIXGUARD_PROFILE) { $env:PHOENIXGUARD_PROFILE.ToUpperInvariant() } else { 'HEAVY_LAZY' }),
    [switch]$Bootstrap,
    [switch]$RunTests,
    [switch]$CheckHF,
    [switch]$VoiceControl
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

$ShareRunnerPath = Join-Path -Path $PSScriptRoot -ChildPath 'share_phoenixguard.py'

if ($Bootstrap) {
    Write-Host "Installing Python dependencies..."
    python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies from 'requirements.txt'."
    }
}

if ($RunTests) {
    Write-Host "Running test suite..."
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "808Fx Standard System test suite failed."
    }
}

if ($CheckHF) {
    if ($env:HF_TOKEN) {
        Write-Host "Validating Hugging Face model access..."
        python hf_model_check.py --token $env:HF_TOKEN
        if ($LASTEXITCODE -ne 0) {
            throw "Hugging Face model access validation failed."
        }
    } else {
        Write-Host "HF_TOKEN not set, skipping remote HF access validation."
    }
}

$env:PHOENIXGUARD_PROFILE = $LaunchMode.ToUpperInvariant()

$env:PHOENIXGUARD_SHARE_MODE = '0'
if (-not $env:PHOENIXGUARD_SHARE_HOST) {
    $env:PHOENIXGUARD_SHARE_HOST = '127.0.0.1'
}
if (-not $env:PHOENIXGUARD_SHARE_PORT) {
    $env:PHOENIXGUARD_SHARE_PORT = '7860'
}
$env:PHOENIXGUARD_SHARE_TUNNEL = '0'
$env:PHOENIXGUARD_SHARE_STRICT_PASSWORDS = '0'
if (-not $env:PHOENIXGUARD_UI_OPEN_BROWSER) {
    $env:PHOENIXGUARD_UI_OPEN_BROWSER = '1'
}
if (-not $env:PHOENIXGUARD_MOBILE_API_HOST) {
    $env:PHOENIXGUARD_MOBILE_API_HOST = '127.0.0.1'
}
if (-not $env:PHOENIXGUARD_MOBILE_API_PORT) {
    $env:PHOENIXGUARD_MOBILE_API_PORT = '8787'
}

if ($VoiceControl) {
    $VoiceScriptPath = Join-Path -Path $PSScriptRoot -ChildPath 'start_phoenixguard_voice.ps1'
    if (-not (Test-Path -LiteralPath $VoiceScriptPath)) {
        throw "Voice launcher script not found at '$VoiceScriptPath'."
    }
    $WindowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Write-Host "Starting 808 voice control bridge..."
    Start-Process -FilePath $WindowsPowerShell -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $VoiceScriptPath,
        '-Host', $env:PHOENIXGUARD_MOBILE_API_HOST,
        '-Port', $env:PHOENIXGUARD_MOBILE_API_PORT,
        '-StartMobileApi'
    ) | Out-Null
}

Write-Host "Launching 808Fx Standard System premium preview with profile: $($env:PHOENIXGUARD_PROFILE)"
Write-Host "808Fx Standard System premium UI: http://$($env:PHOENIXGUARD_SHARE_HOST):$($env:PHOENIXGUARD_SHARE_PORT)"

python $ShareRunnerPath
if ($LASTEXITCODE -ne 0) {
    throw "808Fx Standard System premium preview exited with code $LASTEXITCODE."
}
