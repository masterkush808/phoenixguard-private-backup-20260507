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

    $processPython = $venvPython
    $pyvenvCfg = Join-Path -Path $venvPath -ChildPath 'pyvenv.cfg'
    if (Test-Path -LiteralPath $pyvenvCfg) {
        $cfgLines = Get-Content -LiteralPath $pyvenvCfg
        $executableLine = $cfgLines | Where-Object { $_ -match '^\s*executable\s*=' } | Select-Object -First 1
        if ($executableLine) {
            $candidate = (($executableLine -split '=', 2)[1]).Trim()
            if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                $processPython = $candidate
            }
        }
        if ($processPython -eq $venvPython) {
            $homeLine = $cfgLines | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
            if ($homeLine) {
                $home = (($homeLine -split '=', 2)[1]).Trim()
                $candidate = Join-Path -Path $home -ChildPath 'python.exe'
                if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                    $processPython = $candidate
                }
            }
        }
    }

    $scriptsPath = Join-Path -Path $venvPath -ChildPath 'Scripts'
    $env:PHOENIXGUARD_PYTHON_EXE = $venvPython
    $env:PHOENIXGUARD_PYTHON_PROCESS_EXE = $processPython
    $env:PHOENIXGUARD_PYVENV_LAUNCHER = $venvPython
    $env:VIRTUAL_ENV = $venvPath
    $env:PHOENIXGUARD_STRICT_REPO_VENV = if ($env:PHOENIXGUARD_STRICT_REPO_VENV) { $env:PHOENIXGUARD_STRICT_REPO_VENV } else { '1' }
    $env:__PYVENV_LAUNCHER__ = $venvPython
    $env:PATH = (@($scriptsPath) + (($env:PATH -split [System.IO.Path]::PathSeparator) | Where-Object { $_ -and $_ -ne $scriptsPath })) -join [System.IO.Path]::PathSeparator

    [pscustomobject]@{
        VenvPath = $venvPath
        VenvPython = $venvPython
        ProcessPython = $processPython
        ScriptsPath = $scriptsPath
    }
}
