[CmdletBinding()]
param(
    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 18180,
    [string]$WebHost = "127.0.0.1",
    [int]$WebPort = 3210,
    [switch]$InstallWebDeps,
    [switch]$SkipApi,
    [switch]$SkipWeb,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WebDir = Join-Path $RepoRoot "Business\web"
$BackendSrc = Join-Path $RepoRoot "Backend\src"
$BackendRoot = Join-Path $RepoRoot "Backend"
. (Join-Path -Path $RepoRoot -ChildPath "Backend\launch\Resolve-PhoenixGuardPython.ps1")
$PythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $RepoRoot
$PythonPath = [string]$PythonRuntime.VenvPython
$env:PYTHONPATH = (@($BackendSrc, $BackendRoot, $RepoRoot, $env:PYTHONPATH) | Where-Object { $_ -and [string]$_ -ne "" }) -join [System.IO.Path]::PathSeparator
$RuntimeDir = Join-Path $RepoRoot ".codex_runtime\business_mock"
$PidFile = Join-Path $RuntimeDir "pids.json"
$ApiBaseUrl = "http://${ApiHost}:${ApiPort}"
$WebBaseUrl = "http://${WebHost}:${WebPort}"

function Stop-BusinessMockLocal {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Host "No mock business PID file found at $PidFile"
        return
    }

    $payload = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    foreach ($name in @("api_pid", "web_pid")) {
        $pidValue = $payload.$name
        if (-not $pidValue) {
            continue
        }
        Stop-ProcessTree -RootPid ([int]$pidValue)
        Write-Host "Stopped $name process tree rooted at $pidValue"
    }
    Remove-Item -LiteralPath $PidFile -Force
}

function Stop-ProcessTree {
    param(
        [int]$RootPid
    )
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$RootPid" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -RootPid ([int]$child.ProcessId)
    }
    $process = Get-Process -Id $RootPid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $RootPid -Force
    }
}

function Test-PortInUse {
    param(
        [string]$HostName,
        [int]$Port
    )
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(250)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSec = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 500) {
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Timed out waiting for $Url"
}

function Quote-PowerShellLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Start-HiddenPowerShell {
    param(
        [string]$Name,
        [string]$WorkingDirectory,
        [string]$CommandLine
    )
    $stdout = Join-Path $RuntimeDir "$Name.out.log"
    $stderr = Join-Path $RuntimeDir "$Name.err.log"
    $wrapped = "`$ErrorActionPreference = 'Stop'; Set-Location -LiteralPath '$WorkingDirectory'; $CommandLine"
    return Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $wrapped) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
}

if ($Stop) {
    Stop-BusinessMockLocal
    return
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

if (Test-Path -LiteralPath $PidFile) {
    throw "Mock business stack already has a PID file at $PidFile. Run .\Business\api\start_business_mock_local.ps1 -Stop first."
}

if (-not $SkipApi -and (Test-PortInUse -HostName $ApiHost -Port $ApiPort)) {
    throw "API port $ApiHost`:$ApiPort is already in use. Choose another -ApiPort or stop the existing service."
}
if (-not $SkipWeb -and (Test-PortInUse -HostName $WebHost -Port $WebPort)) {
    throw "Web port $WebHost`:$WebPort is already in use. Choose another -WebPort or stop the existing service."
}

$apiProcess = $null
$webProcess = $null

if (-not $SkipApi) {
    $apiCommand = "& $(Quote-PowerShellLiteral -Value $PythonPath) -m uvicorn Business.api.business_mock_api:app --host $ApiHost --port $ApiPort --log-level info"
    $apiProcess = Start-HiddenPowerShell -Name "api" -WorkingDirectory $RepoRoot -CommandLine $apiCommand
    Wait-HttpOk -Url "$ApiBaseUrl/healthz" -TimeoutSec 60
    Write-Host "Mock FastAPI ready at $ApiBaseUrl"
}

if (-not $SkipWeb) {
    if (-not (Test-Path -LiteralPath (Join-Path $WebDir "package.json"))) {
        throw "Business/web/package.json was not found. Create or fetch the mock Next.js app before starting the web process."
    }
    if ($InstallWebDeps -or -not (Test-Path -LiteralPath (Join-Path $WebDir "node_modules"))) {
        Push-Location $WebDir
        try {
            npm install
        }
        finally {
            Pop-Location
        }
    }
    $webCommand = "`$env:NEXT_PUBLIC_API_BASE_URL = '$ApiBaseUrl'; `$env:BUSINESS_MOCK_API_BASE_URL = '$ApiBaseUrl'; npm run dev -- --hostname $WebHost --port $WebPort"
    $webProcess = Start-HiddenPowerShell -Name "web" -WorkingDirectory $WebDir -CommandLine $webCommand
    Wait-HttpOk -Url $WebBaseUrl -TimeoutSec 90
    Write-Host "Mock Next.js ready at $WebBaseUrl"
}

$pidPayload = [ordered]@{
    api_pid = if ($apiProcess) { $apiProcess.Id } else { $null }
    web_pid = if ($webProcess) { $webProcess.Id } else { $null }
    api_base_url = $ApiBaseUrl
    web_base_url = $WebBaseUrl
    runtime_dir = $RuntimeDir
    live_bridge_touched = $false
}
$pidPayload | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8

Write-Host ""
Write-Host "Business mock stack started without launching shooter.py or the MT4 bridge."
Write-Host "Run E2E:"
Write-Host "Push-Location Business\web; `$env:BUSINESS_E2E='1'; `$env:BUSINESS_WEB_BASE_URL='$WebBaseUrl'; `$env:BUSINESS_API_BASE_URL='$ApiBaseUrl'; npx playwright test tests/e2e/business-mock-flow.spec.ts --reporter=line; Pop-Location"
Write-Host ""
Write-Host "Stop:"
Write-Host ".\Business\api\start_business_mock_local.ps1 -Stop"
