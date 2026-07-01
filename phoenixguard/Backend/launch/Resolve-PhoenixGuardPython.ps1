function Get-PhoenixGuardPythonEnvironmentName {
    $explicitName = [string]$env:PHOENIXGUARD_PYTHON_ENV_NAME
    if (-not [string]::IsNullOrWhiteSpace($explicitName)) {
        return $explicitName.Trim()
    }

    $profile = [string]$env:PHOENIXGUARD_PYTHON_PROFILE
    if ([string]::IsNullOrWhiteSpace($profile)) {
        $profile = 'live'
    }

    switch ($profile.Trim().ToLowerInvariant()) {
        'live' { return '.venv-live' }
        'final_live' { return '.venv-live' }
        'final-live' { return '.venv-live' }
        'dev' { return '.venv-dev' }
        'test' { return '.venv-dev' }
        'testing' { return '.venv-dev' }
        'training' { return '.venv-training' }
        'train' { return '.venv-training' }
        'business' { return '.venv-business' }
        'share' { return '.venv-business' }
        'docs' { return '.venv-docs' }
        'docs-pdf' { return '.venv-docs' }
        default {
            throw "Unknown PhoenixGuard Python profile '$profile'. Set PHOENIXGUARD_PYTHON_ENV_NAME to one of .venv-live, .venv-dev, .venv-training, .venv-business, or .venv-docs."
        }
    }
}

function Resolve-PhoenixGuardPythonRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $environmentName = Get-PhoenixGuardPythonEnvironmentName
    if ($environmentName -notmatch '^\.venv(-[A-Za-z0-9]+)*$') {
        throw "Unsafe PhoenixGuard Python environment name '$environmentName'. Use a top-level .venv-* directory name."
    }

    $venvPath = Join-Path -Path $ProjectRoot -ChildPath $environmentName
    $venvPython = Join-Path -Path $venvPath -ChildPath 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "PhoenixGuard Python for environment '$environmentName' not found at '$venvPython'. Create it from the matching requirements lock before launching."
    }

    $scriptsPath = Join-Path -Path $venvPath -ChildPath 'Scripts'
    $env:PHOENIXGUARD_PYTHON_ENV_NAME = $environmentName
    $env:PHOENIXGUARD_PYTHON_EXE = $venvPython
    $env:PHOENIXGUARD_PYVENV_LAUNCHER = $venvPython
    $env:VIRTUAL_ENV = $venvPath
    $env:PHOENIXGUARD_STRICT_REPO_VENV = if ($env:PHOENIXGUARD_STRICT_REPO_VENV) { $env:PHOENIXGUARD_STRICT_REPO_VENV } else { '1' }
    $env:PATH = (@($scriptsPath) + (($env:PATH -split [System.IO.Path]::PathSeparator) | Where-Object { $_ -and $_ -ne $scriptsPath })) -join [System.IO.Path]::PathSeparator

    [pscustomobject]@{
        VenvPath = $venvPath
        VenvPython = $venvPython
        ScriptsPath = $scriptsPath
        EnvironmentName = $environmentName
    }
}
