[CmdletBinding()]
param(
    [string]$ApiHost = '127.0.0.1',
    [int]$ApiPort = 8793,
    [string]$SessionId = 'pocket-live-8788',
    [string]$BrokerWindowQuery = 'The Most Innovative Trading Platform',
    [string]$FocusRegion = '0.03,0.13,0.87,0.96',
    [double]$CaptureIntervalSec = 30.0,
    [int]$ReadyTimeoutSec = 150
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$currentPid = [int]$PID
$edgeNames = @('msedge.exe', 'msedgewebview2.exe')
$ownedMarkers = @(
    'start_phoenixguard_mobile_api.py',
    'start_phoenixguard_24_7_tracker.py',
    'start_phoenixguard_windows_region_capture.py',
    'shooter.py',
    'phoenixguard.runtime.model_council_daemon',
    'phoenixguard_disk_growth_guard.py',
    'uvicorn phoenixguard.mobile_api.app'
)

$processRows = @(Get-CimInstance Win32_Process)
$owned = [System.Collections.Generic.HashSet[int]]::new()
foreach ($row in $processRows) {
    $processId = [int]$row.ProcessId
    $name = [string]$row.Name
    $commandLine = [string]$row.CommandLine
    if ($processId -eq $currentPid -or $name -in $edgeNames -or -not $commandLine) {
        continue
    }
    $isRepositoryProcess = $commandLine.IndexOf($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    $isOwnedCommand = $false
    foreach ($marker in $ownedMarkers) {
        if ($commandLine.IndexOf($marker, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $isOwnedCommand = $true
            break
        }
    }
    if ($isRepositoryProcess -and $isOwnedCommand) {
        [void]$owned.Add($processId)
    }
}

$queue = [System.Collections.Generic.Queue[int]]::new()
foreach ($processId in @($owned)) {
    $queue.Enqueue($processId)
}
while ($queue.Count -gt 0) {
    $parentId = $queue.Dequeue()
    foreach ($child in $processRows | Where-Object { [int]$_.ParentProcessId -eq $parentId }) {
        $childId = [int]$child.ProcessId
        if ($childId -eq $currentPid -or [string]$child.Name -in $edgeNames) {
            continue
        }
        if ($owned.Add($childId)) {
            $queue.Enqueue($childId)
        }
    }
}

foreach ($processId in @($owned)) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -notin @('msedge', 'msedgewebview2')) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

$portDeadline = (Get-Date).AddSeconds(15)
do {
    $listener = Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { break }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $portDeadline)
if ($listener) {
    throw "PhoenixGuard port $ApiPort remained active after repository-scoped cleanup."
}

$runtimeDir = Join-Path $env:LOCALAPPDATA 'PhoenixGuard\runtime\live'
$tokenPath = Join-Path $runtimeDir 'edge_tab_capture.token'
$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
if ($token.Length -lt 32) {
    throw 'The persisted PhoenixGuard frame-ingest token is missing or invalid.'
}

$env:PHOENIXGUARD_FRAME_INGEST_TOKEN = $token
$env:PHOENIXGUARD_FRAME_INGEST_LOCAL_MANAGED = '1'
$env:PHOENIXGUARD_RUNTIME_DIR = $runtimeDir
$env:PHOENIXGUARD_DATA_DIR = Join-Path $runtimeDir 'data_live'
$env:PHOENIXGUARD_LOGS_DIR = Join-Path $runtimeDir 'logs_live'
# Full book-rule/CV studies exceed the legacy lightweight default on this
# machine. Retain the engine's existing hard maximum and bounded single-worker
# mailbox instead of discarding valid work at 45 seconds.
$env:PHOENIXGUARD_LIVE_TRACKER_STUDY_BUDGET_SEC = '120'

$pythonPath = (Resolve-Path (Join-Path $projectRoot '.venv-live\Scripts\python.exe')).Path
$trackerScript = Join-Path $projectRoot 'Backend\launch\start_phoenixguard_24_7_tracker.py'
$trackerArguments = @(
    ('"{0}"' -f $trackerScript),
    '--host', $ApiHost,
    '--port', [string]$ApiPort,
    '--session-id', $SessionId,
    '--window-query', ('"{0}"' -f $BrokerWindowQuery),
    '--focus-region', $FocusRegion,
    '--capture-interval', [string]$CaptureIntervalSec,
    '--dashboard-browser', 'chrome',
    '--no-open-dashboard'
) -join ' '

$tracker = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList $trackerArguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru

$baseUrl = "http://${ApiHost}:$ApiPort"
$readyDeadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
$readiness = $null
do {
    Start-Sleep -Seconds 2
    if ($tracker.HasExited) {
        throw "PhoenixGuard tracker exited during startup with code $($tracker.ExitCode)."
    }
    try {
        $readiness = Invoke-RestMethod "$baseUrl/v1/mobile/frame-ingest/config" -TimeoutSec 10
    } catch {
        $readiness = $null
    }
} while ((-not $readiness -or -not $readiness.readiness.armed) -and (Get-Date) -lt $readyDeadline)
if (-not $readiness -or -not $readiness.readiness.armed) {
    throw "PhoenixGuard API did not become token-armed within $ReadyTimeoutSec seconds."
}

$sessionUrl = "$baseUrl/v1/mobile/window-tracker/sessions/$SessionId"
$focusBody = @{ normalized_bbox = @(0.03, 0.13, 0.87, 0.96); source = 'phoenixguard_fast_restart_v3' } | ConvertTo-Json -Compress
$focusRestored = $false
for ($attempt = 1; $attempt -le 4 -and -not $focusRestored; $attempt++) {
    try {
        Invoke-RestMethod -Method Put -Uri "$sessionUrl/focus-region" -ContentType 'application/json' -Body $focusBody -TimeoutSec 20 | Out-Null
        $focusRestored = $true
    } catch {
        if ($attempt -eq 4) { throw }
        Start-Sleep -Seconds 2
    }
}
$session = $null
for ($attempt = 1; $attempt -le 4 -and -not $session; $attempt++) {
    try {
        $session = Invoke-RestMethod -Method Post -Uri "$sessionUrl/start" -ContentType 'application/json' -Body '{}' -TimeoutSec 20
    } catch {
        if ($attempt -eq 4) { throw }
        Start-Sleep -Seconds 2
    }
}

[ordered]@{
    schema_version = 'PG_FAST_RESTART_V3'
    stopped_processes = $owned.Count
    tracker_pid = $tracker.Id
    api = $baseUrl
    ingest_armed = [bool]$readiness.readiness.armed
    tracking = [bool]$session.tracking_enabled
    edge_processes_touched = 0
} | ConvertTo-Json -Compress
