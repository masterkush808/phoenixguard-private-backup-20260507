[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Operator launcher prints explicit live-runtime status.")]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSUseShouldProcessForStateChangingFunctions", "", Justification = "This non-interactive launcher intentionally performs explicit startup actions.")]
param(
    [string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { 'The Most Innovative Trading Platform' }),
    [int]$BrokerWindowHwnd = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_HWND) { [int]$env:PHOENIXGUARD_BROKER_WINDOW_HWND } else { 0 }),
    [string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
    [double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 30.0 }),
    [int]$WarmupSeconds = 20,
    [double]$ShooterPollSec = $(if ($env:PHOENIXGUARD_SHOOTER_POLL_SEC) { [double]$env:PHOENIXGUARD_SHOOTER_POLL_SEC } else { 30.0 }),
    [ValidateSet('chrome', 'default', 'edge')]
    [string]$DashboardBrowser = $(if ($env:PHOENIXGUARD_DASHBOARD_BROWSER) { $env:PHOENIXGUARD_DASHBOARD_BROWSER } else { 'chrome' }),
    [switch]$NoBrowser,
    [switch]$SkipPreview,
    [switch]$DisableShooter
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
# Compatibility wrappers still pass this retired switch. Keep it bindable while
# the canonical launcher always performs its bounded readiness preview.
Write-Verbose "Retired compatibility input SkipPreview=$([bool]$SkipPreview) is ignored."
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $ProjectRoot

function Set-PhoenixGuardDefaultProcessEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $currentValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace([string]$currentValue)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    }
}

# Bound native math/tokenizer pools before the first Python process imports
# PyTorch, NumPy, or Transformers. Explicit operator overrides remain intact.
Set-PhoenixGuardDefaultProcessEnvironment -Name 'OMP_NUM_THREADS' -Value '2'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'MKL_NUM_THREADS' -Value '2'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'OPENBLAS_NUM_THREADS' -Value '2'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'NUMEXPR_NUM_THREADS' -Value '2'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'TOKENIZERS_PARALLELISM' -Value 'false'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_CHRONOS_CPU_THREADS' -Value '2'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_BACKGROUND_WARMUP_ON_LAUNCH' -Value '1'
# Continuous observation is intentionally CPU-small on the supported local
# profile. The V3 study lane is only awakened for material keyframes or its
# bounded watchdog heartbeat.
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_CPU_STREAM_ENABLED' -Value '1'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_CPU_STREAM_FPS' -Value '0.25'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_CPU_STREAM_SNAPSHOT_FALLBACK_SEC' -Value '15.0'
$normalizedBrokerQuery = $BrokerWindowQuery.Trim().ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace([string]$env:PHOENIXGUARD_MT4_BRIDGE_ENABLED)) {
    $pocketOptionOnly = $normalizedBrokerQuery.Contains('pocket option') -or $normalizedBrokerQuery.Contains('innovative trading platform')
    $env:PHOENIXGUARD_MT4_BRIDGE_ENABLED = if ($pocketOptionOnly) { '0' } else { '1' }
}
$launchMt4Bridge = $env:PHOENIXGUARD_MT4_BRIDGE_ENABLED.Trim().ToLowerInvariant() -notin @('0', 'false', 'off', 'no')

# The canonical launcher is FINAL_LIVE. Choose the live Python profile before
# resolving the interpreter, while still respecting an explicit caller profile
# or PHOENIXGUARD_PYTHON_ENV_NAME override handled by the resolver.
$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_PYTHON_PROFILE' -Value 'live'

$backendSrc = Join-Path -Path $ProjectRoot -ChildPath 'Backend\src'
$backendRoot = Join-Path -Path $ProjectRoot -ChildPath 'Backend'
$backendCompat = Join-Path -Path $ProjectRoot -ChildPath 'Backend\compat'
$backendLaunch = Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch'
$frontendDashboard = Join-Path -Path $ProjectRoot -ChildPath 'Frontend\dashboard'
$env:PYTHONPATH = (@($backendSrc, $backendRoot, $backendCompat, $backendLaunch, $frontendDashboard, $ProjectRoot, $env:PYTHONPATH) | Where-Object { $_ -and [string]$_ -ne '' }) -join [System.IO.Path]::PathSeparator
$env:PHOENIXGUARD_PROJECT_ROOT = $ProjectRoot

. (Join-Path -Path $PSScriptRoot -ChildPath 'Resolve-PhoenixGuardPython.ps1')
$pythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $ProjectRoot
$pythonPath = [string]$pythonRuntime.VenvPython
$env:PHOENIXGUARD_PYTHON_EXE = $pythonPath
$env:PHOENIXGUARD_PYVENV_LAUNCHER = $pythonPath
$env:VIRTUAL_ENV = Split-Path -Parent (Split-Path -Parent $pythonPath)
$pythonScriptsDir = Split-Path -Parent $pythonPath
if ($pythonScriptsDir -and -not ([string]$env:PATH).ToLowerInvariant().StartsWith($pythonScriptsDir.ToLowerInvariant() + [System.IO.Path]::PathSeparator)) {
    $env:PATH = $pythonScriptsDir + [System.IO.Path]::PathSeparator + $env:PATH
}

$baseUrl = 'http://127.0.0.1:8793'
$dashboardUrl = "$baseUrl/v3/mobile/window-tracker/dashboard/$SessionId"

function ConvertTo-ProcessArgumentString {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    return (($Arguments | ForEach-Object {
        $value = [string]$_
        if ($value -match '[\s"]') {
            '"' + ($value -replace '"', '\"') + '"'
        } else {
            $value
        }
    }) -join ' ')
}

function Test-PhoenixGuardOwnedCommandLine {
    param(
        [AllowEmptyString()]
        [string]$CommandLine,
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string[]]$TargetPattern
    )

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    if ($CommandLine.IndexOf($RepositoryRoot, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $false
    }
    foreach ($pattern in $TargetPattern) {
        if ($CommandLine -like $pattern) {
            return $true
        }
    }
    return $false
}

function Test-LocalTcpPortOpen {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    try {
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        return [bool](@($listeners | Where-Object { [int]$_.Port -eq $Port }).Count -gt 0)
    } catch {
        throw "Local TCP listener inspection failed; refusing destructive preflight cleanup."
    }
}

function Get-ValidatedPhoenixGuardRuntimeLock {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LockPath,
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedSessionId,
        [Parameter(Mandatory = $true)]
        [int]$ExpectedApiPort,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedDataDir
    )

    if (-not (Test-Path -LiteralPath $LockPath -PathType Leaf)) {
        throw "Runtime lock is missing; refusing unattributed process cleanup."
    }
    $lock = $null
    for ($attempt = 0; $attempt -lt 5 -and $null -eq $lock; $attempt += 1) {
        try {
            $lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            if ($attempt -lt 4) {
                Start-Sleep -Milliseconds 50
            }
        }
    }
    if ($null -eq $lock) {
        throw "Runtime lock is unreadable; refusing unattributed process cleanup."
    }

    $expectedRoot = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
    $expectedLockPath = [System.IO.Path]::GetFullPath($LockPath)
    $lockRoot = [System.IO.Path]::GetFullPath([string]$lock.repo_root).TrimEnd('\')
    $lockPathValue = [System.IO.Path]::GetFullPath([string]$lock.lock_path)
    $ownerToken = [string]$lock.owner_token
    $expectedOwnerId = if ($ownerToken.Length -ge 10) {
        "phoenixguard-$ExpectedSessionId-$($ownerToken.Substring(0, 10))"
    } else {
        ''
    }
    $runtimeDataDir = [System.IO.Path]::GetFullPath([string]$lock.data_dir)
    $expectedRuntimeDataDir = [System.IO.Path]::GetFullPath($ExpectedDataDir).TrimEnd('\')

    $isValid = (
        ([string]$lock.schema_version -ceq 'PG_RUNTIME_SINGLETON_GUARD_V3') -and
        $lockRoot.Equals($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        $lockPathValue.Equals($expectedLockPath, [System.StringComparison]::OrdinalIgnoreCase) -and
        ([string]$lock.session_id -ceq $ExpectedSessionId) -and
        ([int]$lock.api_port -eq $ExpectedApiPort) -and
        ([string]$lock.base_url -ceq "http://127.0.0.1:$ExpectedApiPort") -and
        ($ownerToken -cmatch '^[0-9a-f]{32}$') -and
        ([string]$lock.state_version_owner -ceq $ownerToken) -and
        ([string]$lock.runtime_owner_id -ceq $expectedOwnerId) -and
        $runtimeDataDir.TrimEnd('\').Equals($expectedRuntimeDataDir, [System.StringComparison]::OrdinalIgnoreCase)
    )
    if (-not $isValid) {
        throw "Runtime lock ownership contract failed validation; refusing process cleanup."
    }

    $heartbeatEpochMs = [int64]$lock.heartbeat_epoch_ms
    $nowEpochMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $heartbeatAgeMs = $nowEpochMs - $heartbeatEpochMs
    if ($heartbeatEpochMs -le 0 -or $heartbeatAgeMs -lt -30000 -or $heartbeatAgeMs -gt 120000) {
        throw "Runtime lock heartbeat is not fresh; refusing PID cleanup because PID reuse cannot be excluded."
    }

    $ownedPids = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($key in @('launcher_pid', 'tracker_pid', 'api_pid', 'shooter_pid')) {
        $candidate = [int]$lock.$key
        if ($candidate -gt 0) {
            [void]$ownedPids.Add($candidate)
        }
    }
    if ($ownedPids.Count -eq 0) {
        throw "Runtime lock contains no owned process identifiers."
    }

    $pidEpochContracts = @(
        [pscustomobject]@{ PidKey = 'tracker_pid'; EpochKey = 'created_epoch_ms' },
        [pscustomobject]@{ PidKey = 'api_pid'; EpochKey = 'api_registered_epoch_ms' }
    )
    $verifiedCorePidCount = 0
    foreach ($contract in $pidEpochContracts) {
        $candidatePid = [int]$lock.($contract.PidKey)
        if ($candidatePid -le 0) {
            continue
        }
        $candidateProcess = Get-Process -Id $candidatePid -ErrorAction SilentlyContinue
        if ($null -eq $candidateProcess) {
            continue
        }
        $registeredEpochMs = [int64]$lock.($contract.EpochKey)
        if ($registeredEpochMs -le 0) {
            $registeredEpochMs = [int64]$lock.created_epoch_ms
        }
        try {
            $processStartEpochMs = [DateTimeOffset]::new(
                $candidateProcess.StartTime.ToUniversalTime()
            ).ToUnixTimeMilliseconds()
        } catch {
            throw "Owned PID start time could not be verified; refusing cleanup because PID reuse cannot be excluded."
        }
        if ([Math]::Abs([double]($processStartEpochMs - $registeredEpochMs)) -gt 120000.0) {
            throw "Owned PID start time does not match its runtime-lock registration; refusing cleanup."
        }
        $verifiedCorePidCount += 1
    }
    if ($verifiedCorePidCount -eq 0) {
        throw "No live core PID matched the active runtime lock; refusing process cleanup."
    }
    return [pscustomobject]@{
        BaseUrl = [string]$lock.base_url
        SessionId = [string]$lock.session_id
        Pids = @($ownedPids)
        HeartbeatAgeMs = [int64]$heartbeatAgeMs
    }
}

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
        source_state = ""
        source_fresh = $false
    }

    try {
        $session = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$SessionId" -TimeoutSec 5
        $result.tracker_status = [string]($session.status)
        $result.capture_count = [int]($session.capture_count)
        $result.has_study_packet = $null -ne $session.model_council_study_packet
        if ($null -ne $session.capture_source_v3) {
            $result.source_state = [string]($session.capture_source_v3.state)
            $result.source_fresh = [bool]($session.capture_source_v3.fresh)
        }
    } catch {
        $result.reason = "session_unavailable"
        return [pscustomobject]$result
    }

    # Universal capture intentionally starts without opening or activating a
    # broker window. These states prove the API/session/controller contract is
    # operational while honestly waiting for a user-selected chart; visual
    # artifact readiness begins only after the first leased frame arrives.
    $sourceOperationalWithoutFrame = $result.source_state -in @(
        'NO_SOURCE',
        'READY',
        'SELECTING',
        'CLAIMING',
        'VALIDATING',
        'PAUSED',
        'STALE',
        'SWITCHING',
        'STOPPED',
        'KILLED',
        'WAITING_FOR_SOURCE'
    )
    if ($sourceOperationalWithoutFrame) {
        $result.ready = $true
        $result.reason = "universal_source_$($result.source_state.ToLowerInvariant())"
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
    Write-Host "  Shooter: PACKAGE_REPORTER only; broker clicks are retired."
}

$env:PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS = '0'
$env:PHOENIXGUARD_LIVE_EXECUTION_ENABLED = '1'
$env:PHOENIXGUARD_BROKER_WINDOW_QUERY = $BrokerWindowQuery
$env:PHOENIXGUARD_TRACKER_SESSION_ID = $SessionId
$env:PHOENIXGUARD_DASHBOARD_ROUTE = 'live'
$env:PHOENIXGUARD_EXECUTION_COOLDOWN_SEC = '900'
$env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL = '0'
$env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS = '1'
$env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N = '300'
$env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH = '960'
# A display-only heartbeat advances the visible frame without a matching model
# result.  Keep the operator surface on the last atomic chart + study bundle;
# the full capture worker publishes the next bundle when inference completes.
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT = '0'
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC = '15.0'
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_POLL_SEC = '15.0'
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_TIMEOUT_SEC = '4.0'
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_FILE_THREAD = '0'
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_FILE_HEARTBEAT = '0'
$env:PHOENIXGUARD_DISPLAY_REUSE_IDENTICAL_SURFACE = '0'
$env:PHOENIXGUARD_DISPLAY_REUSE_ONLY_HEARTBEAT = '0'
$env:PHOENIXGUARD_DISPLAY_BUSY_REUSE_HEARTBEAT = '0'
$env:PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY = '1'
$env:PHOENIXGUARD_POCKET_FAST_FOREGROUND_IMAGEGRAB = '0'
$env:PHOENIXGUARD_CAPTURE_ACTIVATE_WINDOW_FALLBACK = '0'
$env:PHOENIXGUARD_TRACKER_RESTORE_LOCKED_WINDOW = '0'
$env:PHOENIXGUARD_DISPLAY_FAST_VISIBLE_CAPTURE = '0'
$env:PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK = '1'
$env:PHOENIXGUARD_SCAN_BROKER_SURFACE_WHEN_NOT_EXECUTABLE = '0'
$env:PHOENIXGUARD_COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC = '20.0'
$env:PHOENIXGUARD_TRACKER_ARTIFACT_PRUNE_INTERVAL_SEC = '300.0'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_TRACKER_ARTIFACT_RETENTION_FRAMES' -Value '144'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_TRACKER_ARTIFACT_MAX_AGE_SEC' -Value '5400'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_TRACKER_ARTIFACT_MAX_MB' -Value '128'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_MARKET_REGISTRY_MAX_BYTES' -Value '16777216'
Set-PhoenixGuardDefaultProcessEnvironment -Name 'PHOENIXGUARD_MARKET_REGISTRY_RETAIN_LINES' -Value '4000'
$env:PHOENIXGUARD_OVERLAY_PERSIST_DEBUG = '0'
$env:PHOENIXGUARD_OVERLAY_GEOMETRY_DUMPS = '0'
$env:PHOENIXGUARD_UVICORN_ACCESS_LOG = '0'
$env:PHOENIXGUARD_PERSIST_CHILD_STDIO = '0'
$env:PHOENIXGUARD_SHOOTER_POLL_SEC = ([string][double]$ShooterPollSec).Replace(',', '.')
$env:PHOENIXGUARD_FAST_FOCUS_PREVIEW = '0'
$legacyRuntimeDir = Join-Path -Path $ProjectRoot -ChildPath 'runtime\live'
$configuredRuntimeDir = [string]$env:PHOENIXGUARD_RUNTIME_DIR
if (-not [string]::IsNullOrWhiteSpace($configuredRuntimeDir)) {
    $runtimeDir = [System.IO.Path]::GetFullPath($configuredRuntimeDir)
} elseif (-not [string]::IsNullOrWhiteSpace([string]$env:LOCALAPPDATA)) {
    $runtimeDir = Join-Path -Path $env:LOCALAPPDATA -ChildPath 'PhoenixGuard\runtime\live'
} else {
    $runtimeDir = $legacyRuntimeDir
}
$dataDir = if (-not [string]::IsNullOrWhiteSpace([string]$env:PHOENIXGUARD_DATA_DIR)) {
    [System.IO.Path]::GetFullPath([string]$env:PHOENIXGUARD_DATA_DIR)
} else {
    Join-Path -Path $runtimeDir -ChildPath 'data_live'
}
$logsDir = if (-not [string]::IsNullOrWhiteSpace([string]$env:PHOENIXGUARD_LOGS_DIR)) {
    [System.IO.Path]::GetFullPath([string]$env:PHOENIXGUARD_LOGS_DIR)
} else {
    Join-Path -Path $runtimeDir -ChildPath 'logs_live'
}
$trackerStatusFile = if (-not [string]::IsNullOrWhiteSpace([string]$env:PHOENIXGUARD_TRACKER_STATUS_FILE)) {
    [System.IO.Path]::GetFullPath([string]$env:PHOENIXGUARD_TRACKER_STATUS_FILE)
} else {
    Join-Path -Path $runtimeDir -ChildPath 'tracker_status.json'
}
if (-not (Test-Path -LiteralPath $runtimeDir)) {
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
}
$env:PHOENIXGUARD_RUNTIME_DIR = $runtimeDir
$env:PHOENIXGUARD_DATA_DIR = $dataDir
$env:PHOENIXGUARD_LOGS_DIR = $logsDir
$env:PHOENIXGUARD_TRACKER_STATUS_FILE = $trackerStatusFile
$windowsRegionCaptureStatusPath = Join-Path -Path $runtimeDir -ChildPath 'windows_region_capture_status.json'
$env:PHOENIXGUARD_WINDOWS_REGION_CAPTURE_STATUS_FILE = $windowsRegionCaptureStatusPath
if ($runtimeDir -ne $legacyRuntimeDir) {
    $legacyTokenPath = Join-Path -Path $legacyRuntimeDir -ChildPath 'edge_tab_capture.token'
    $currentTokenPath = Join-Path -Path $runtimeDir -ChildPath 'edge_tab_capture.token'
    if (
        -not (Test-Path -LiteralPath $currentTokenPath -PathType Leaf) -and
        (Test-Path -LiteralPath $legacyTokenPath -PathType Leaf)
    ) {
        Copy-Item -LiteralPath $legacyTokenPath -Destination $currentTokenPath
    }
}
. (Join-Path -Path $PSScriptRoot -ChildPath 'Initialize-PhoenixGuardEdgeTabCapture.ps1')
$edgeTabCapture = Initialize-PhoenixGuardEdgeTabCaptureEnvironment `
    -RuntimeDir $runtimeDir `
    -MinIntervalSec 4.0
if (-not $env:PHOENIXGUARD_DISK_GUARD_ENABLED) {
    $env:PHOENIXGUARD_DISK_GUARD_ENABLED = '1'
}
if (-not $env:PHOENIXGUARD_DISK_GUARD_MAX_BYTES) {
    $env:PHOENIXGUARD_DISK_GUARD_MAX_BYTES = '512MB'
}
if (-not $env:PHOENIXGUARD_DISK_GUARD_LOW_WATER_BYTES) {
    $env:PHOENIXGUARD_DISK_GUARD_LOW_WATER_BYTES = '384MB'
}
if (-not $env:PHOENIXGUARD_DISK_GUARD_INTERVAL_SEC) {
    $env:PHOENIXGUARD_DISK_GUARD_INTERVAL_SEC = '300'
}
$env:PHOENIXGUARD_DISK_GUARD_INCLUDE_CODEX_SESSIONS = '0'

Write-Host ""
Write-Host "Preflight: stop existing live stack"
$currentPid = [int]$PID
$targetPatterns = @(
    '*start_phoenixguard_mobile_api.py*',
    '*start_phoenixguard_24_7_tracker.ps1*',
    '*start_phoenixguard_24_7_tracker.py*',
    '*start_phoenixguard_windows_region_capture.py*',
    '*shooter.py*',
    '*phoenixguard.runtime.model_council_daemon*',
    '*phoenixguard_disk_growth_guard.py*',
    '*uvicorn phoenixguard.mobile_api.app*',
    '*phoenixguard_mt4_file_bridge.py*',
    '*run_entry_allowance_burn.py*',
    '*manual_entry_alert*',
    '*business_mock*',
    '*next dev --hostname 127.0.0.1 --port 3210*',
    '*next start --hostname 127.0.0.1 --port 3310*',
    '*node_modules\next\dist\server\lib\start-server.js*'
)
$processRows = @()
$processRowsAvailable = $false
try {
    $processRows = @(Get-CimInstance Win32_Process)
    $processRowsAvailable = $true
} catch {
    Write-Warning "Process command-line scan unavailable: $($_.Exception.Message). Cleanup will not stop unattributed port owners."
}
$targetProcessIds = New-Object 'System.Collections.Generic.HashSet[int]'
if ($processRowsAvailable) {
    $processRows | Where-Object {
        $commandLine = [string]$_.CommandLine
        ([int]$_.ProcessId) -ne $currentPid -and
            (Test-PhoenixGuardOwnedCommandLine -CommandLine $commandLine -RepositoryRoot $ProjectRoot -TargetPattern $targetPatterns)
    } | ForEach-Object {
        [void]$targetProcessIds.Add([int]$_.ProcessId)
    }
}
$lockFallbackStopped = 0
if (-not $processRowsAvailable) {
    $runtimeLockPath = Join-Path -Path $runtimeDir -ChildPath 'phoenixguard_stack.lock.json'
    $validatedRuntimeLock = $null
    if (Test-LocalTcpPortOpen -Port 8793) {
        $validatedRuntimeLock = Get-ValidatedPhoenixGuardRuntimeLock `
            -LockPath $runtimeLockPath `
            -RepositoryRoot $ProjectRoot `
            -ExpectedSessionId $SessionId `
            -ExpectedApiPort 8793 `
            -ExpectedDataDir $env:PHOENIXGUARD_DATA_DIR
    } elseif (Test-Path -LiteralPath $runtimeLockPath -PathType Leaf) {
        try {
            $validatedRuntimeLock = Get-ValidatedPhoenixGuardRuntimeLock `
                -LockPath $runtimeLockPath `
                -RepositoryRoot $ProjectRoot `
                -ExpectedSessionId $SessionId `
                -ExpectedApiPort 8793 `
                -ExpectedDataDir $env:PHOENIXGUARD_DATA_DIR
        } catch {
            Write-Warning "Stale or invalid closed-port runtime lock will not be used as PID authority."
        }
    }
    Write-Warning "Using repository-scoped psutil cleanup because process command-line inspection is unavailable."
    & $pythonPath `
        '.\Developer\developer_tools\phoenixguard_kill_switch.py' `
        --kill-only `
        --session-id $SessionId `
        --base-url 'http://127.0.0.1:8793' `
        --ports '8793,8767,8787,18180,18181,3210,3310,7861'
    if ($LASTEXITCODE -ne 0) {
        throw "Repository-scoped PhoenixGuard cleanup failed; runtime deletion is blocked."
    }
    $lockFallbackStopped = 1
    Start-Sleep -Seconds 3
}
try {
    foreach ($cleanupPort in @(8793, 18181, 18180, 8787, 3210, 3310)) {
        Get-NetTCPConnection -LocalPort $cleanupPort -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
            $ownerPid = [int]$_.OwningProcess
            if ($ownerPid -ne $currentPid) {
                if ($processRowsAvailable) {
                    $owner = $processRows | Where-Object { [int]$_.ProcessId -eq $ownerPid } | Select-Object -First 1
                    $ownerCommandLine = [string]$owner.CommandLine
                    if (Test-PhoenixGuardOwnedCommandLine -CommandLine $ownerCommandLine -RepositoryRoot $ProjectRoot -TargetPattern $targetPatterns) {
                        [void]$targetProcessIds.Add($ownerPid)
                    }
                }
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
$portCloseDeadline = (Get-Date).AddSeconds(12)
while ((Get-Date) -lt $portCloseDeadline -and (Test-LocalTcpPortOpen -Port 8793)) {
    Start-Sleep -Milliseconds 250
}
if (Test-LocalTcpPortOpen -Port 8793) {
    throw "Port 8793 is still owned after scoped PhoenixGuard cleanup; refusing runtime deletion and relaunch."
}
Write-Host "  stopped_processes=$($targetProcessIds.Count) lock_fallback_roots=$lockFallbackStopped"

Write-Host ""
Write-Host "Preflight: configured PhoenixGuard live Python environment"
& $pythonPath ".\Backend\tools\verify_single_venv_runtime.py"
if ($LASTEXITCODE -ne 0) {
    throw "Configured PhoenixGuard Python environment verification failed. Launch aborted."
}

Write-Host ""
Write-Host "Preflight: runtime cleanup"
if (Test-Path ".\Backend\tools\clean_v3_runtime_state.py") {
    & $pythonPath ".\Backend\tools\clean_v3_runtime_state.py" --apply --delete
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime cleanup failed. Launch aborted."
    }
} else {
    Write-Warning "Backend\tools\clean_v3_runtime_state.py not found. Skipping runtime cleanup."
}

Write-Host ""
Write-Host "Preflight: V3 integrity"
& $pythonPath 'Backend\tools\verify_v3_integrity.py'
if ($LASTEXITCODE -ne 0) {
    throw "V3 integrity preflight failed."
}

Write-Host ""
Write-Host "Preflight: disk growth guard"
if ($env:PHOENIXGUARD_DISK_GUARD_ENABLED -eq '0') {
    Write-Warning "Disk growth guard is disabled by PHOENIXGUARD_DISK_GUARD_ENABLED=0"
} elseif (Test-Path ".\Backend\tools\phoenixguard_disk_growth_guard.py") {
    $guardLimit = [string]$env:PHOENIXGUARD_DISK_GUARD_MAX_BYTES
    $guardLowWater = [string]$env:PHOENIXGUARD_DISK_GUARD_LOW_WATER_BYTES
    $guardInterval = [string]$env:PHOENIXGUARD_DISK_GUARD_INTERVAL_SEC
    $guardArgs = @(
        (Join-Path -Path $ProjectRoot -ChildPath 'Backend\tools\phoenixguard_disk_growth_guard.py'),
        '--apply',
        '--limit',
        $guardLimit,
        '--low-water',
        $guardLowWater,
        '--interval-sec',
        $guardInterval,
        '--report-path',
        (Join-Path -Path $runtimeDir -ChildPath 'disk_growth_guard_report.json'),
        '--jsonl-log',
        (Join-Path -Path $env:PHOENIXGUARD_LOGS_DIR -ChildPath 'disk_growth_guard.jsonl')
    )
    if ($env:PHOENIXGUARD_DISK_GUARD_INCLUDE_CODEX_SESSIONS -ne '0') {
        $guardArgs += '--include-codex-sessions'
    }
    $guardOutPath = 'NUL'
    $guardErrPath = '\\.\NUL'
    Start-Process -FilePath $pythonPath -ArgumentList (ConvertTo-ProcessArgumentString -Arguments $guardArgs) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $guardOutPath -RedirectStandardError $guardErrPath | Out-Null
    Write-Host "  enabled=true cap=$guardLimit low_water=$guardLowWater interval_sec=$guardInterval"
    Write-Host "  guard_stdio=discarded report=$(Join-Path -Path $runtimeDir -ChildPath 'disk_growth_guard_report.json')"
} else {
    Write-Warning "Backend\tools\phoenixguard_disk_growth_guard.py not found. Disk cap worker not started."
}

Write-Host ""
Write-Host "Preflight: shooter broker-window and calibration checks retired"

Write-Host ""
Write-Host "Launching single FINAL_LIVE tracker, Model Council, package reporter, and broker-relevant bridge stack..."
$childLaunchProfile = if ($DisableShooter) { 'TRACKER_PLUS_COUNCIL' } else { 'FULL' }
$launchArgs = @{
    ApiHost = '127.0.0.1'
    ApiPort = 8793
    SessionId = $SessionId
    CaptureIntervalSec = $CaptureIntervalSec
    BrokerWindowQuery = $BrokerWindowQuery
    BrokerWindowHwnd = $BrokerWindowHwnd
    Profile = $childLaunchProfile
    NoStatusLoop = $true
    DashboardBrowser = $DashboardBrowser
}
if ($NoBrowser) {
    $launchArgs['NoBrowser'] = $true
}

& (Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\start_phoenixguard_full_local.ps1') @launchArgs

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
& $pythonPath 'Backend\tools\runtime_trace_v3.py' --base-url $baseUrl --session $SessionId --timeout 20
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Runtime trace reported a non-executable live state after launch. The tracker remains running; shooter arming still requires fresh frame readiness."
}

Write-Host ""
Write-Host "Postflight: single logical process topology"
$windowsRegionCaptureStatus = $null
if (Test-Path -LiteralPath $windowsRegionCaptureStatusPath -PathType Leaf) {
    try {
        $windowsRegionCaptureStatus = Get-Content -LiteralPath $windowsRegionCaptureStatusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Warning "Universal chart selector status is unreadable: $($_.Exception.Message)"
    }
}
$sourceControllerStatus = if ($null -ne $windowsRegionCaptureStatus) {
    [string]$windowsRegionCaptureStatus.status
} else {
    'unavailable'
}
$sourceControllerReady = [bool](
    $null -ne $windowsRegionCaptureStatus -and
    [bool]$windowsRegionCaptureStatus.hotkey_registered -and
    $sourceControllerStatus -notin @('', 'error', 'failed', 'unavailable')
)
$topologyArgs = @(
    'Backend\tools\certify_process_topology_v3.py',
    '--base-url',
    $baseUrl,
    '--session',
    $SessionId,
    '--port',
    '8793',
    '--data-dir',
    $env:PHOENIXGUARD_DATA_DIR
)
if ($DisableShooter) {
    $topologyArgs += '--allow-missing-shooter'
}
if ($launchMt4Bridge) {
    $topologyArgs += '--require-bridge'
}
if ($sourceControllerReady) {
    $topologyArgs += '--require-source-controller'
}
& $pythonPath @topologyArgs
if ($LASTEXITCODE -ne 0) {
    throw "Process topology certification failed after launch. Clean stack was not proven."
}

if (-not $DisableShooter) {
    Write-Host ""
    Write-Host "Shooter reporter: PACKAGE_REPORTER is attached by FINAL_LIVE; separate shooter execution arming is retired."
}

$summaryPath = Join-Path -Path $runtimeDir -ChildPath 'live_launch_summary.json'
$summaryPayload = [ordered]@{
    schema_version = 'PG_LIVE_READY_LAUNCH_SUMMARY_V1'
    created_at = (Get-Date).ToString('o')
    project_root = $ProjectRoot
    python_exe = $pythonPath
    runtime_dir = $runtimeDir
    data_dir = $env:PHOENIXGUARD_DATA_DIR
    logs_dir = $env:PHOENIXGUARD_LOGS_DIR
    tracker_status_file = $env:PHOENIXGUARD_TRACKER_STATUS_FILE
    base_url = $baseUrl
    dashboard_url = $dashboardUrl
    dashboard_browser = $DashboardBrowser
    broker_window_query = $BrokerWindowQuery
    broker_window_hwnd = $BrokerWindowHwnd
    session_id = $SessionId
    capture_interval_sec = $CaptureIntervalSec
    shooter_disabled = [bool]$DisableShooter
    shooter_mode = if ($DisableShooter) { 'DISABLED' } else { 'PACKAGE_REPORTER' }
    shooter_execution_path = 'retired_reporter_only'
    shooter_poll_sec = $ShooterPollSec
    mt4_bridge_enabled = [bool]$launchMt4Bridge
    cpu_stream_fps = $env:PHOENIXGUARD_CPU_STREAM_FPS
    live_execution_enabled = $env:PHOENIXGUARD_LIVE_EXECUTION_ENABLED
    background_capture_only = $env:PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY
    foreground_capture_fallback_enabled = $env:PHOENIXGUARD_CAPTURE_ACTIVATE_WINDOW_FALLBACK
    locked_window_restore_enabled = $env:PHOENIXGUARD_TRACKER_RESTORE_LOCKED_WINDOW
    display_native_capture_fallback_enabled = $env:PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK
    edge_tab_capture_ingest_armed = [bool]$edgeTabCapture.Armed
    edge_tab_capture_token_path = [string]$edgeTabCapture.TokenPath
    edge_tab_capture_min_interval_sec = [double]$edgeTabCapture.MinIntervalSec
    source_controller_ready = $sourceControllerReady
    source_controller_status = $sourceControllerStatus
    source_controller_hotkey_registered = if ($null -ne $windowsRegionCaptureStatus) { [bool]$windowsRegionCaptureStatus.hotkey_registered } else { $false }
    source_controller_status_file = $windowsRegionCaptureStatusPath
    disk_growth_guard_enabled = $env:PHOENIXGUARD_DISK_GUARD_ENABLED
    disk_growth_guard_max_bytes = $env:PHOENIXGUARD_DISK_GUARD_MAX_BYTES
    disk_growth_guard_low_water_bytes = $env:PHOENIXGUARD_DISK_GUARD_LOW_WATER_BYTES
    disk_growth_guard_interval_sec = $env:PHOENIXGUARD_DISK_GUARD_INTERVAL_SEC
}
$summaryPayload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host ""
Write-Host "Live launch complete."
Write-Host "  Dashboard: $dashboardUrl"
Write-Host "  Runtime: $runtimeDir"
Write-Host "  Data: $env:PHOENIXGUARD_DATA_DIR"
Write-Host "  Logs: $env:PHOENIXGUARD_LOGS_DIR"
if ($DisableShooter) {
    Write-Host "  Shooter: disabled; no shooter process was launched."
} else {
    Write-Host "  Shooter reporter: background package reporter with logs in $env:PHOENIXGUARD_LOGS_DIR"
}
Write-Host "  Launch summary: $summaryPath"
