param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,
    [switch]$Recreate
)

Set-Location $ProjectRoot
$venvPath = Join-Path $ProjectRoot '.venv-business'
$lockPath = Join-Path $ProjectRoot 'requirements\locks\business-win-py311.txt'
if (-not (Test-Path $lockPath)) { throw "Missing lock file: $lockPath" }
if ($Recreate -and (Test-Path $venvPath)) { Remove-Item -LiteralPath $venvPath -Recurse -Force }
if (-not (Test-Path $venvPath)) { py -3.11 -m venv $venvPath }
$python = Join-Path $venvPath 'Scripts\python.exe'
$sync = Join-Path $venvPath 'Scripts\pip-sync.exe'
& $python -m pip install -U pip setuptools wheel pip-tools
& $sync $lockPath
& $python -m pip check
& $python Backend\tools\verify_dependency_profile.py --profile business
