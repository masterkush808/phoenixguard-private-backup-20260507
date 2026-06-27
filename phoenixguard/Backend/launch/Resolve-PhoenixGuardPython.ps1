function Resolve-PhoenixGuardPythonRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $venvPath = Join-Path -Path $ProjectRoot -ChildPath '.venv'
    $venvPython = Join-Path -Path $venvPath -ChildPath 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "PhoenixGuard repo Python not found at '$venvPython'. Create the repo .venv before launching."
    }

    $scriptsPath = Join-Path -Path $venvPath -ChildPath 'Scripts'
    $env:PHOENIXGUARD_PYTHON_EXE = $venvPython
    $env:PHOENIXGUARD_PYVENV_LAUNCHER = $venvPython
    $env:VIRTUAL_ENV = $venvPath
    $env:PHOENIXGUARD_STRICT_REPO_VENV = if ($env:PHOENIXGUARD_STRICT_REPO_VENV) { $env:PHOENIXGUARD_STRICT_REPO_VENV } else { '1' }
    $env:PATH = (@($scriptsPath) + (($env:PATH -split [System.IO.Path]::PathSeparator) | Where-Object { $_ -and $_ -ne $scriptsPath })) -join [System.IO.Path]::PathSeparator

    [pscustomobject]@{
        VenvPath = $venvPath
        VenvPython = $venvPython
        ScriptsPath = $scriptsPath
    }
}
