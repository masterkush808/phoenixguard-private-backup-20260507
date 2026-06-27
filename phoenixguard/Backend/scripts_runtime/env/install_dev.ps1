param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path,
    [switch]$Recreate
)

Set-Location $ProjectRoot
$venvPath = Join-Path $ProjectRoot '.venv'
$lockPath = Join-Path $ProjectRoot 'requirements\locks\dev-win-py311.txt'
if (-not (Test-Path $lockPath)) { throw "Missing lock file: $lockPath" }
if ($Recreate -and (Test-Path $venvPath)) { Remove-Item -LiteralPath $venvPath -Recurse -Force }
if (-not (Test-Path $venvPath)) { py -3.11 -m venv $venvPath }
$python = Join-Path $venvPath 'Scripts\python.exe'
& $python -m pip install -U pip setuptools wheel pip-tools
& $python -m pip install -r $lockPath
& $python -m pip check
& $python Backend\tools\verify_dependency_profile.py --profile dev
