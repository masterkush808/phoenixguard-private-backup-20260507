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
    $processPython = Join-Path -Path $scriptsPath -ChildPath 'phoenixguard-python.exe'
    $pyvenvCfg = Join-Path -Path $venvPath -ChildPath 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $processPython)) {
        $basePython = $null
        if (Test-Path -LiteralPath $pyvenvCfg) {
            $cfgLines = Get-Content -LiteralPath $pyvenvCfg
            $executableLine = $cfgLines | Where-Object { $_ -match '^\s*executable\s*=' } | Select-Object -First 1
            if ($executableLine) {
                $candidate = (($executableLine -split '=', 2)[1]).Trim()
                if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                    $basePython = $candidate
                }
            }
        }
        if ($basePython) {
            Copy-Item -LiteralPath $basePython -Destination $processPython -Force
            $basePythonDir = Split-Path -Parent $basePython
            foreach ($dllName in @('python311.dll', 'python3.dll')) {
                $dllSource = Join-Path -Path $basePythonDir -ChildPath $dllName
                if (Test-Path -LiteralPath $dllSource) {
                    Copy-Item -LiteralPath $dllSource -Destination (Join-Path -Path $scriptsPath -ChildPath $dllName) -Force
                }
            }
        }
    }
    if (-not (Test-Path -LiteralPath $processPython)) {
        $processPython = $venvPython
    }
    $env:PHOENIXGUARD_PYTHON_EXE = $venvPython
    $env:PHOENIXGUARD_PYTHON_PROCESS_EXE = $processPython
    $env:PHOENIXGUARD_PYVENV_LAUNCHER = $venvPython
    $env:VIRTUAL_ENV = $venvPath
    $env:PHOENIXGUARD_STRICT_REPO_VENV = if ($env:PHOENIXGUARD_STRICT_REPO_VENV) { $env:PHOENIXGUARD_STRICT_REPO_VENV } else { '1' }
    if ($processPython -ne $venvPython) {
        $env:__PYVENV_LAUNCHER__ = $venvPython
    }
    $env:PATH = (@($scriptsPath) + (($env:PATH -split [System.IO.Path]::PathSeparator) | Where-Object { $_ -and $_ -ne $scriptsPath })) -join [System.IO.Path]::PathSeparator

    [pscustomobject]@{
        VenvPath = $venvPath
        VenvPython = $venvPython
        ProcessPython = $processPython
        ScriptsPath = $scriptsPath
    }
}
