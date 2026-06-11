[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Operator launcher prints explicit live-runtime status.")]
param(
    [string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { 'The Most Innovative Trading Platform' }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 0.5 }),
    [int]$WarmupSeconds = 20,
    [switch]$NoBrowser,
    [switch]$SkipPreview,
    [switch]$DisableShooter
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path -LiteralPath '.venv')) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment at '$PSScriptRoot\.venv'."
    }
}

$pythonPath = Join-Path -Path $PSScriptRoot -ChildPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python executable not found at '$pythonPath'."
}

$baseUrl = 'http://127.0.0.1:8793'
$dashboardUrl = "$baseUrl/dashboard/live/$SessionId"

function Get-LiveReadinessSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [Parameter(Mandatory = $true)]
        [string]$SessionId
    )

    $result = [ordered]@{
        ready = $false
        reason = "unknown"
        tracker_status = ""
        capture_count = 0
        has_chart_endpoint = $false
        has_overlay_endpoint = $false
        has_study_packet = $false
    }

    try {
        $session = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$SessionId" -TimeoutSec 5
        $result.tracker_status = [string]($session.status)
        $result.capture_count = [int]($session.capture_count)
        $result.has_study_packet = $null -ne $session.model_council_study_packet
    } catch {
        $result.reason = "session_unavailable"
        return [pscustomobject]$result
    }

    try {
        $chartResp = Invoke-WebRequest -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$SessionId/artifacts/latest-chart" -TimeoutSec 5 -UseBasicParsing
        $result.has_chart_endpoint = ($chartResp.StatusCode -eq 200)
    } catch {
        $result.has_chart_endpoint = $false
    }

    try {
        $overlayResp = Invoke-WebRequest -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$SessionId/artifacts/latest-overlay" -TimeoutSec 5 -UseBasicParsing
        $result.has_overlay_endpoint = ($overlayResp.StatusCode -eq 200)
    } catch {
        $result.has_overlay_endpoint = $false
    }

    if ($result.tracker_status -eq "running" -and $result.capture_count -gt 0 -and ($result.has_chart_endpoint -or $result.has_overlay_endpoint)) {
        $result.ready = $true
        $result.reason = "ready"
    } else {
        $result.reason = "warming"
    }

    return [pscustomobject]$result
}

Write-Host "PhoenixGuard V3 live-ready launch"
Write-Host "  Session: $SessionId"
Write-Host "  Broker window query: $BrokerWindowQuery"
Write-Host "  Dashboard: $dashboardUrl"
if ($DisableShooter) {
    Write-Host "  Shooter: DISABLED for this launch"
} else {
    Write-Warning "LIVE broker clicks will be armed for this launched shooter process. The shooter still waits for PG_EXECUTION_PACKET_V3."
}

$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS = if ($DisableShooter) { '0' } else { '1' }
$env:PHOENIXGUARD_BROKER_WINDOW_QUERY = $BrokerWindowQuery
$env:PHOENIXGUARD_TRACKER_SESSION_ID = $SessionId
$env:PHOENIXGUARD_DASHBOARD_ROUTE = 'live'
$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'
$env:PHOENIXGUARD_EXECUTION_COOLDOWN_SEC = '600'
$runtimeDir = Join-Path -Path $PSScriptRoot -ChildPath '.codex_runtime'
if (-not (Test-Path -LiteralPath $runtimeDir)) {
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
}
$env:PHOENIXGUARD_DATA_DIR = Join-Path -Path $runtimeDir -ChildPath 'data_live'
$env:PHOENIXGUARD_LOGS_DIR = Join-Path -Path $runtimeDir -ChildPath 'logs_live'
$env:PHOENIXGUARD_TRACKER_STATUS_FILE = Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'

Write-Host ""
Write-Host "Preflight: stop existing live stack"
$currentPid = [int]$PID
$targetPatterns = @(
    '*start_phoenixguard_mobile_api.py*',
    '*start_phoenixguard_24_7_tracker.ps1*',
    '*start_phoenixguard_24_7_tracker.py*',
    '*shooter.py*',
    '*phoenixguard.runtime.model_council_daemon*'
)
$processRows = @(Get-CimInstance Win32_Process)
$targetProcessIds = New-Object 'System.Collections.Generic.HashSet[int]'
$processRows | Where-Object {
    $commandLine = [string]$_.CommandLine
    $matchesTarget = $false
    foreach ($pattern in $targetPatterns) {
        if ($commandLine -like $pattern) {
            $matchesTarget = $true
            break
        }
    }
    (-not [string]::IsNullOrWhiteSpace($commandLine)) -and ([int]$_.ProcessId) -ne $currentPid -and $matchesTarget
} | ForEach-Object {
    [void]$targetProcessIds.Add([int]$_.ProcessId)
}
try {
    Get-NetTCPConnection -LocalPort 8793 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $ownerPid = [int]$_.OwningProcess
        if ($ownerPid -ne $currentPid) {
            $owner = $processRows | Where-Object { [int]$_.ProcessId -eq $ownerPid } | Select-Object -First 1
            $ownerCommandLine = [string]$owner.CommandLine
            if ($ownerCommandLine -like '*phoenixguard*' -or $ownerCommandLine -like '*start_phoenixguard_mobile_api.py*') {
                [void]$targetProcessIds.Add($ownerPid)
            }
        }
    }
} catch {
    Write-Verbose "Port cleanup check skipped: $($_.Exception.Message)"
}
$queue = [System.Collections.Generic.Queue[int]]::new()
foreach ($processId in $targetProcessIds) {
    $queue.Enqueue([int]$processId)
}
while ($queue.Count -gt 0) {
    $parentId = $queue.Dequeue()
    $processRows | Where-Object { [int]$_.ParentProcessId -eq $parentId } | ForEach-Object {
        $childId = [int]$_.ProcessId
        if ($childId -ne $currentPid -and $targetProcessIds.Add($childId)) {
            $queue.Enqueue($childId)
        }
    }
}
foreach ($processId in $targetProcessIds) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}
if ($targetProcessIds.Count -gt 0) {
    Start-Sleep -Seconds 2
}
Write-Host "  stopped_processes=$($targetProcessIds.Count)"

Write-Host ""
Write-Host "Preflight: runtime cleanup"
if (Test-Path ".\tools\clean_v3_runtime_state.py") {
    & $pythonPath ".\tools\clean_v3_runtime_state.py" --apply
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime cleanup failed. Launch aborted."
    }
} else {
    Write-Warning "tools\clean_v3_runtime_state.py not found. Skipping runtime cleanup."
}

Write-Host ""
Write-Host "Preflight: V3 integrity"
& $pythonPath 'tools\verify_v3_integrity.py'
if ($LASTEXITCODE -ne 0) {
    throw "V3 integrity preflight failed."
}

Write-Host ""
Write-Host "Preflight: broker window"
& $pythonPath 'shooter.py' list-windows --contains $BrokerWindowQuery
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Broker window preflight found no visible match for '$BrokerWindowQuery'. Continuing so the tracker can warm up and attach when the broker becomes visible."
}

if (-not $SkipPreview) {
    Write-Host ""
    Write-Host "Preflight: calibration preview"
    & $pythonPath 'shooter.py' preview --window-query $BrokerWindowQuery
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Calibration preview could not run against '$BrokerWindowQuery'. Continuing with a warming launch instead of aborting."
    }
}

Write-Host ""
if ($DisableShooter) {
    Write-Host "Launching full V3 stack with Model Council and shooter disabled..."
} else {
    Write-Host "Launching full V3 stack with visible shooter HUD..."
}
$launchArgs = @{
    CaptureIntervalSec = $CaptureIntervalSec
    BrokerWindowQuery = $BrokerWindowQuery
    Profile = if ($DisableShooter) { 'TRACKER_PLUS_COUNCIL' } else { 'FULL' }
    ShooterMode = if ($DisableShooter) { 'LIVE_DISABLED' } else { 'LIVE_READY' }
    RecordActionEvidence = -not $DisableShooter
    NoStatusLoop = $true
}
if ($NoBrowser) {
    $launchArgs['NoBrowser'] = $true
}

& (Join-Path -Path $PSScriptRoot -ChildPath 'start_phoenixguard_full_local.ps1') @launchArgs

Start-Sleep -Seconds ([Math]::Max(1, $WarmupSeconds))

Write-Host ""
Write-Host "Readiness check: tracker artifacts and session state"
$readyDeadline = (Get-Date).AddSeconds(150)
$readySnapshot = $null
while ((Get-Date) -lt $readyDeadline) {
    $readySnapshot = Get-LiveReadinessSnapshot -BaseUrl $baseUrl -SessionId $SessionId
    if ($readySnapshot.ready) {
        break
    }
    Start-Sleep -Seconds 2
}
if ($null -eq $readySnapshot) {
    $readySnapshot = Get-LiveReadinessSnapshot -BaseUrl $baseUrl -SessionId $SessionId
}
if ($readySnapshot.ready) {
    Write-Host "Readiness: PASS"
    Write-Host "  tracker_status=$($readySnapshot.tracker_status) capture_count=$($readySnapshot.capture_count) chart_endpoint=$($readySnapshot.has_chart_endpoint) overlay_endpoint=$($readySnapshot.has_overlay_endpoint)"
} else {
    Write-Warning "Readiness: WARMING"
    Write-Warning "  tracker_status=$($readySnapshot.tracker_status) capture_count=$($readySnapshot.capture_count) chart_endpoint=$($readySnapshot.has_chart_endpoint) overlay_endpoint=$($readySnapshot.has_overlay_endpoint)"
    Write-Warning "  Live stack is running, but visual artifacts are not fully ready yet."
}

Write-Host ""
Write-Host "Runtime trace after warmup"
& $pythonPath 'tools\runtime_trace_v3.py' --base-url $baseUrl --session $SessionId --timeout 20
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Runtime trace reported a non-executable live state after launch. The tracker and armed shooter remain running; the shooter continues waiting for PG_EXECUTION_PACKET_V3."
}

Write-Host ""
Write-Host "Live launch complete."
Write-Host "  Dashboard: $dashboardUrl"
if ($DisableShooter) {
    Write-Host "  Shooter: disabled; no shooter process was launched."
} else {
    Write-Host "  Shooter logs: .codex_runtime\shooter_live_ready_stderr.log"
}
Write-Host "  Launch summary: .codex_runtime\live_launch_summary.json"
