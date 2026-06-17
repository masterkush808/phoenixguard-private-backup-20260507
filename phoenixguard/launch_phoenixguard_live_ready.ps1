[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Operator launcher prints explicit live-runtime status.")]
param(
    [string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { 'The Most Innovative Trading Platform' }),
    [int]$BrokerWindowHwnd = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_HWND) { [int]$env:PHOENIXGUARD_BROKER_WINDOW_HWND } else { 0 }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 1.0 }),
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

function Get-LivePerformanceSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [Parameter(Mandatory = $true)]
        [string]$SessionId
    )

    $result = [ordered]@{
        ready = $false
        reason = "unknown"
        frame_age_ms = 0.0
        stale_status = ""
    }

    try {
        $perf = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/performance/trace/v3/$SessionId" -TimeoutSec 20
        $timing = $perf.timing_trace
        $result.frame_age_ms = [double]($timing.frame_age_ms)
        $result.stale_status = [string]($timing.stale_status)
        if ($result.frame_age_ms -le 2500.0) {
            $result.ready = $true
            $result.reason = "fresh_frame"
        } else {
            $result.reason = "frame_age_ms=$($result.frame_age_ms)"
        }
    } catch {
        $result.reason = "performance_trace_unavailable: $($_.Exception.Message)"
    }

    return [pscustomobject]$result
}

function Get-LiveRuntimeAuthoritySnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [Parameter(Mandatory = $true)]
        [string]$SessionId
    )

    $result = [ordered]@{
        ready = $false
        reason = "unknown"
        source_lock = ""
        latest_frame = ""
        sequence_context = ""
        model_council = ""
        study_packet = ""
        execution_packet = ""
        packet_validator = ""
        model_warm = ""
        packet_contract = ""
        instrument_state = ""
        broker_click_safe = $false
    }

    try {
        $sessionQuery = [System.Uri]::EscapeDataString($SessionId)
        $trace = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/runtime/trace/v3?session_id=$sessionQuery" -TimeoutSec 25
        $nodes = $trace.dataflow_contract_trace.nodes
        $gates = $trace.certification_gates
        $promotion = $gates.model_council_trace.evidence

        $result.source_lock = [string]($nodes.BrokerSourceLockV3)
        $result.latest_frame = [string]($nodes.LatestFrameBufferV3)
        $result.sequence_context = [string]($nodes.SequenceContextV3)
        $result.model_council = [string]($nodes.ModelCouncilV3)
        $result.study_packet = [string]($nodes.STUDY_PACKET)
        $result.execution_packet = [string]($nodes.PG_EXECUTION_PACKET_V3)
        $result.packet_validator = [string]($nodes.PacketValidatorV3)
        $result.model_warm = [string]($nodes.MultiModelRoleOutputsV3)
        $result.packet_contract = [string]($gates.packet_contract.status)
        $result.instrument_state = [string]($promotion.instrument_context_state)
        $result.broker_click_safe = [bool]($promotion.instrument_context_broker_click_safe)

        $coreReady = (
            $result.source_lock -eq "PASS" -and
            $result.latest_frame -eq "PASS" -and
            $result.sequence_context -eq "PASS" -and
            $result.model_council -eq "PASS" -and
            $result.study_packet -eq "PASS" -and
            $result.packet_validator -eq "PASS" -and
            $result.model_warm -eq "PASS" -and
            [bool]($gates.source_lock.passed) -and
            [bool]($gates.sequence_context.passed) -and
            [bool]($gates.model_warm_state.passed) -and
            [bool]($gates.model_council_trace.passed) -and
            [bool]($gates.packet_contract.passed)
        )
        $packetSafe = ($result.execution_packet -ne "PASS" -or $result.broker_click_safe)

        if ($coreReady -and $packetSafe) {
            $result.ready = $true
            $result.reason = "runtime_authority_ready"
        } elseif (-not $coreReady) {
            $result.reason = "runtime_chain_not_ready source=$($result.source_lock) frame=$($result.latest_frame) sequence=$($result.sequence_context) council=$($result.model_council) study=$($result.study_packet) validator=$($result.packet_validator) models=$($result.model_warm) packet_contract=$($result.packet_contract)"
        } else {
            $result.reason = "execution_packet_present_but_instrument_not_broker_click_safe state=$($result.instrument_state)"
        }
    } catch {
        $result.reason = "runtime_trace_unavailable: $($_.Exception.Message)"
    }

    return [pscustomobject]$result
}

function Start-LiveReadyShooter {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl,
        [Parameter(Mandatory = $true)]
        [string]$SessionId,
        [Parameter(Mandatory = $true)]
        [string]$BrokerWindowQuery,
        [int]$BrokerWindowHwnd = 0
    )

    $escapedRoot = $PSScriptRoot.Replace("'", "''")
    $escapedSessionId = $SessionId.Replace("'", "''")
    $escapedBaseUrl = $BaseUrl.Replace("'", "''")
    $escapedBrokerWindowQuery = $BrokerWindowQuery.Replace("'", "''")
    $windowHwndArg = ""
    if ($BrokerWindowHwnd -gt 0) {
        $windowHwndArg = " --window-hwnd $BrokerWindowHwnd"
    }
    $shooterCommand = @(
        'Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned',
        "cd '$escapedRoot'",
        "`$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS='1'",
        ".\.venv\Scripts\python.exe 'shooter.py' signal --session-id '$escapedSessionId' --base-url '$escapedBaseUrl' --poll 0.05 --max-signal-age 30 --preferred-source tracker --require-preferred-source --min-confidence 0.2 --window-query '$escapedBrokerWindowQuery'$windowHwndArg --shooter-mode LIVE_READY --broker-speed-profile 'config/shooter_broker_timing_profile.json' --action-speed balanced --no-auto-open --record-action-evidence"
    ) -join '; '

    Start-Process powershell -ArgumentList @(
        '-NoExit',
        '-Command',
        $shooterCommand
    ) -WindowStyle Hidden | Out-Null
}

Write-Host "PhoenixGuard V3 live-ready launch"
Write-Host "  Session: $SessionId"
Write-Host "  Broker window query: $BrokerWindowQuery"
if ($BrokerWindowHwnd -gt 0) {
    Write-Host "  Broker window HWND: $BrokerWindowHwnd"
}
Write-Host "  Dashboard: $dashboardUrl"
if ($DisableShooter) {
    Write-Host "  Shooter: DISABLED for this launch"
} else {
    Write-Warning "LIVE broker clicks will be armed for this launched shooter process. The shooter still waits for PG_EXECUTION_PACKET_V3."
}

$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS = '0'
$env:PHOENIXGUARD_BROKER_WINDOW_QUERY = $BrokerWindowQuery
$env:PHOENIXGUARD_TRACKER_SESSION_ID = $SessionId
$env:PHOENIXGUARD_DASHBOARD_ROUTE = 'live'
$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'
$env:PHOENIXGUARD_EXECUTION_COOLDOWN_SEC = '600'
$env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL = '0'
$env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS = '1'
$env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N = '300'
$env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH = '320'
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT = '1'
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC = '0.5'
$env:PHOENIXGUARD_FAST_FOCUS_PREVIEW = '1'
$runtimeDir = if ($env:PHOENIXGUARD_RUNTIME_DIR) {
    $env:PHOENIXGUARD_RUNTIME_DIR
} elseif ($env:LOCALAPPDATA) {
    Join-Path -Path $env:LOCALAPPDATA -ChildPath 'PhoenixGuard\codex_runtime'
} else {
    Join-Path -Path $PSScriptRoot -ChildPath '.codex_runtime'
}
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
Write-Host "Launching full V3 tracker and Model Council stack; shooter arms only after readiness..."
$launchArgs = @{
    CaptureIntervalSec = $CaptureIntervalSec
    BrokerWindowQuery = $BrokerWindowQuery
    BrokerWindowHwnd = $BrokerWindowHwnd
    Profile = 'TRACKER_PLUS_COUNCIL'
    ShooterMode = 'LIVE_DISABLED'
    RecordActionEvidence = $false
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
    Write-Warning "Runtime trace reported a non-executable live state after launch. The tracker remains running; shooter arming still requires fresh frame readiness."
}

if (-not $DisableShooter) {
    Write-Host ""
    Write-Host "Shooter arming gate: runtime authority"
    $authorityDeadline = (Get-Date).AddSeconds(180)
    $authoritySnapshot = $null
    while ((Get-Date) -lt $authorityDeadline) {
        $authoritySnapshot = Get-LiveRuntimeAuthoritySnapshot -BaseUrl $baseUrl -SessionId $SessionId
        if ($authoritySnapshot.ready) {
            break
        }
        Start-Sleep -Seconds 2
    }
    if ($null -eq $authoritySnapshot) {
        $authoritySnapshot = Get-LiveRuntimeAuthoritySnapshot -BaseUrl $baseUrl -SessionId $SessionId
    }
    if (-not $authoritySnapshot.ready) {
        throw "Shooter arming refused: runtime authority gate failed ($($authoritySnapshot.reason)). Tracker remains running without shooter."
    }
    Write-Host "Runtime authority: PASS sequence=$($authoritySnapshot.sequence_context) study=$($authoritySnapshot.study_packet) execution=$($authoritySnapshot.execution_packet) instrument=$($authoritySnapshot.instrument_state)"

    Write-Host ""
    Write-Host "Shooter arming gate: broker source lock"
    & $pythonPath 'tools\certify_broker_source_lock_v3.py' --base-url $baseUrl --session $SessionId
    if ($LASTEXITCODE -ne 0) {
        throw "Shooter arming refused: broker source lock gate failed. Tracker remains running without shooter."
    }

    Write-Host ""
    Write-Host "Shooter arming gate: fresh tracker frame"
    $perfDeadline = (Get-Date).AddSeconds(90)
    $perfSnapshot = $null
    while ((Get-Date) -lt $perfDeadline) {
        $perfSnapshot = Get-LivePerformanceSnapshot -BaseUrl $baseUrl -SessionId $SessionId
        if ($perfSnapshot.ready) {
            break
        }
        Start-Sleep -Seconds 1
    }
    if ($null -eq $perfSnapshot) {
        $perfSnapshot = Get-LivePerformanceSnapshot -BaseUrl $baseUrl -SessionId $SessionId
    }
    if (-not $perfSnapshot.ready) {
        throw "Shooter arming refused: tracker frame is not fresh ($($perfSnapshot.reason)). Tracker remains running without shooter."
    }
    Write-Host "Shooter arming gate: PASS frame_age_ms=$([Math]::Round([double]$perfSnapshot.frame_age_ms, 0)) stale_status=$($perfSnapshot.stale_status)"

    $authoritySnapshot = Get-LiveRuntimeAuthoritySnapshot -BaseUrl $baseUrl -SessionId $SessionId
    if (-not $authoritySnapshot.ready) {
        throw "Shooter arming refused: runtime authority changed after freshness gate ($($authoritySnapshot.reason)). Tracker remains running without shooter."
    }
    if ($BrokerWindowHwnd -le 0) {
        try {
            $liveState = Invoke-RestMethod -Uri "$baseUrl/v1/mobile/live/state/v3/$SessionId`?mode=CLEAN_LIVE" -TimeoutSec 20
            $lockedHwndText = [string]($liveState.broker_source_lock.selected_target.window_handle)
            $lockedHwnd = 0
            if ([int]::TryParse($lockedHwndText, [ref]$lockedHwnd) -and $lockedHwnd -gt 0) {
                $BrokerWindowHwnd = $lockedHwnd
                Write-Host "Shooter locked HWND auto-detected from BrokerSourceLockV3: $BrokerWindowHwnd"
            }
        } catch {
            Write-Warning "Could not auto-detect BrokerSourceLockV3 HWND before shooter start: $($_.Exception.Message)"
        }
    }
    Write-Host "Starting shooter against $baseUrl in LIVE_READY mode"
    Start-LiveReadyShooter -BaseUrl $baseUrl -SessionId $SessionId -BrokerWindowQuery $BrokerWindowQuery -BrokerWindowHwnd $BrokerWindowHwnd
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
