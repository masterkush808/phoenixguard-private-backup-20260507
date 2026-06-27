param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
)

Set-Location $ProjectRoot
$venvPath = Join-Path $ProjectRoot '.venv'
$lockPath = Join-Path $ProjectRoot 'requirements\locks\training-win-py311.txt'
if (-not (Test-Path $lockPath)) { throw "Missing lock file: $lockPath" }
$python = Join-Path $venvPath 'Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Repo .venv Python not found at $python. Create .venv once, then rerun this installer." }
$sitePackages = Join-Path $venvPath 'Lib\site-packages'
$repoPathFile = Join-Path $sitePackages 'phoenixguard_repo_paths.pth'
@(
    $ProjectRoot,
    (Join-Path $ProjectRoot 'Backend\src'),
    (Join-Path $ProjectRoot 'Backend'),
    (Join-Path $ProjectRoot 'Backend\compat'),
    (Join-Path $ProjectRoot 'Backend\launch'),
    (Join-Path $ProjectRoot 'Backend\tools'),
    (Join-Path $ProjectRoot 'Frontend\dashboard')
) | Set-Content -LiteralPath $repoPathFile -Encoding ASCII
& $python -m pip install -U pip setuptools wheel pip-tools
& $python -m pip install -r $lockPath
& $python -m pip check
& $python Backend\tools\verify_dependency_profile.py --profile training
