param(
    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 8791,
    [string]$SessionId = "pocket-live-8788",
    [string]$DataDir = ".codex_runtime\data_live",
    [string]$LogsDir = ".codex_runtime\logs_live",
    [double]$CaptureIntervalSec = 3.0,
    [int]$HealthTimeoutSec = 45,
    [int]$WarmTimeoutSec = 180,
    [switch]$ForceRestart,
    [switch]$NoBrowser,
    [switch]$EnableVoiceControl,
    [switch]$NoVoiceControl
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-ApiHealth {
    param(
        [string]$BaseUrl
    )

    try {
        $response = Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/health" -TimeoutSec 5
        return ($response.status -eq "ok")
    } catch {
        return $false
    }
}

function Wait-ApiHealth {
    param(
        [string]$BaseUrl,
        [int]$TimeoutSec = 45
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-ApiHealth -BaseUrl $BaseUrl) {
            return $true
        }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

function Invoke-ApiRequest {
    param(
        [string]$Uri,
        [string]$Method = "Get",
        [object]$Body = $null,
        [string]$ContentType = "application/json",
        [int]$TimeoutSec = 30
    )

    $invokeParams = @{
        Uri = $Uri
        Method = $Method
        TimeoutSec = $TimeoutSec
    }
    if ($null -ne $Body) {
        $invokeParams["Body"] = $Body
        $invokeParams["ContentType"] = $ContentType
    }
    return Invoke-RestMethod @invokeParams
}

function Get-ListeningProcessId {
    param(
        [int]$Port
    )

    $match = netstat -ano | Select-String "LISTENING" | Select-String ":$Port\s"
    foreach ($line in $match) {
        $text = [string]$line.Line
        if ($text -match "LISTENING\s+(\d+)\s*$") {
            return [int]$matches[1]
        }
    }
    return $null
}

function Restart-ApiListener {
    param(
        [int]$Port
    )

    $listenerPid = Get-ListeningProcessId -Port $Port
    if ($null -eq $listenerPid) {
        return $false
    }
    try {
        Stop-Process -Id $listenerPid -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 900
        return $true
    } catch {
        throw "Failed to stop the existing PhoenixGuard API listener on port $Port (PID $listenerPid): $($_.Exception.Message)"
    }
}

function Get-VoiceBridgeProcesses {
    param(
        [int]$Port
    )

    $scriptName = "start_phoenixguard_voice.ps1"
    $portToken = "-Port $Port"
    try {
        return @(Get-CimInstance Win32_Process | Where-Object {
            $commandLine = [string]$_.CommandLine
            -not [string]::IsNullOrWhiteSpace($commandLine) -and
            $commandLine -like "*$scriptName*" -and
            $commandLine -like "*$portToken*"
        })
    } catch {
        Write-Warning "Voice bridge process discovery failed: $($_.Exception.Message)"
        return @()
    }
}

function Restart-VoiceBridge {
    param(
        [int]$Port
    )

    $processes = @(Get-VoiceBridgeProcesses -Port $Port)
    foreach ($process in $processes) {
        try {
            Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
        } catch {
            throw "Failed to stop the existing PhoenixGuard voice bridge (PID $($process.ProcessId)): $($_.Exception.Message)"
        }
    }
    return $processes.Count
}

function Start-VoiceBridge {
    param(
        [string]$BindHost,
        [int]$Port,
        [string]$ResolvedSessionId
    )

    $voiceScriptPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "start_phoenixguard_voice.ps1"))
    if (-not (Test-Path -LiteralPath $voiceScriptPath)) {
        throw "Voice launcher script not found at '$voiceScriptPath'."
    }

    $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $windowsPowerShell)) {
        throw "Windows PowerShell was not found at '$windowsPowerShell'."
    }

    return Start-Process `
        -FilePath $windowsPowerShell `
        -ArgumentList @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', $voiceScriptPath,
            '-Host', $BindHost,
            '-Port', "$Port",
            '-SessionId', $ResolvedSessionId
        ) `
        -WorkingDirectory $PSScriptRoot `
        -PassThru
}

function Sync-VoiceRuntimeSession {
    param(
        [string]$BaseUrl,
        [string]$ResolvedSessionId,
        [int]$TimeoutSec = 30
    )

    $encodedSessionId = [Uri]::EscapeDataString([string]$ResolvedSessionId)
    return Invoke-ApiRequest -Uri "$BaseUrl/v1/voice/status?tracker_session_id=$encodedSessionId" -TimeoutSec $TimeoutSec
}

function Enable-VoiceRuntime {
    param(
        [string]$BaseUrl,
        [string]$ResolvedSessionId,
        [double]$CaptureIntervalSec,
        [bool]$AutomaticTimerEnabled,
        [int]$TimeoutSec = 30
    )

    $voiceStatus = Sync-VoiceRuntimeSession -BaseUrl $BaseUrl -ResolvedSessionId $ResolvedSessionId -TimeoutSec $TimeoutSec
    $snapshot = $voiceStatus.snapshot
    if ($null -eq $snapshot) {
        throw "Voice status payload did not include a snapshot for session '$ResolvedSessionId'."
    }

    $body = @{
        voice_enabled = $true
        listening_enabled = $true
        automatic_timer_enabled = $AutomaticTimerEnabled
        tracker_capture_interval_sec = $CaptureIntervalSec
        timezone_name = [string]$snapshot.timezone_name
        tracker_session_id = $ResolvedSessionId
    } | ConvertTo-Json -Compress

    return Invoke-ApiRequest `
        -Method Post `
        -Uri "$BaseUrl/v1/voice/preferences" `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec $TimeoutSec
}

function Start-DashboardApi {
    param(
        [string]$ResolvedDataDir,
        [string]$ResolvedLogsDir,
        [int]$Port,
        [string]$BindHost
    )

    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        throw "Python virtual environment not found at '.venv\\Scripts\\python.exe'."
    }

    New-Item -ItemType Directory -Force -Path $ResolvedDataDir, $ResolvedLogsDir | Out-Null

    $pythonPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".venv\Scripts\python.exe"))
    $entryPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "start_phoenixguard_mobile_api.py"))
    $stdoutLog = Join-Path $ResolvedLogsDir "dashboard_api_stdout.log"
    $stderrLog = Join-Path $ResolvedLogsDir "dashboard_api_stderr.log"

    Remove-Item -LiteralPath $stdoutLog, $stderrLog -ErrorAction SilentlyContinue
    $env:PHOENIXGUARD_DATA_DIR = $ResolvedDataDir
    $env:PHOENIXGUARD_LOGS_DIR = $ResolvedLogsDir
    $env:PHOENIXGUARD_MOBILE_API_HOST = $BindHost
    $env:PHOENIXGUARD_MOBILE_API_PORT = "$Port"
    $env:PHOENIXGUARD_TRACING_DISABLED = "1"

    $proc = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList "`"$entryPath`"" `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    return $proc
}

$resolvedDataDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $DataDir))
$resolvedLogsDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $LogsDir))
$baseUrl = "http://$ApiHost`:$ApiPort"
$dashboardUrl = "$baseUrl/v1/mobile/window-tracker/dashboard/$SessionId"
$sessionUrl = "$baseUrl/v1/mobile/window-tracker/sessions/$SessionId"
$voiceControlEnabled = [bool]$EnableVoiceControl -and -not [bool]$NoVoiceControl
$normalizedCaptureIntervalSec = [Math]::Min(10.0, [Math]::Max(0.5, [double]$CaptureIntervalSec))

if ($ForceRestart) {
    $restarted = Restart-ApiListener -Port $ApiPort
    if ($restarted) {
        Write-Host "Restarted the existing PhoenixGuard dashboard API listener on port $ApiPort"
    } else {
        Write-Host "No existing PhoenixGuard dashboard API listener was found on port $ApiPort"
    }
    if ($voiceControlEnabled) {
        $voiceRestarts = Restart-VoiceBridge -Port $ApiPort
        if ($voiceRestarts -gt 0) {
            Write-Host "Restarted $voiceRestarts existing PhoenixGuard voice bridge process(es) on port $ApiPort"
        } else {
            Write-Host "No existing PhoenixGuard voice bridge process was found for port $ApiPort"
        }
    }
}

if (-not (Test-ApiHealth -BaseUrl $baseUrl)) {
    Write-Host "Starting PhoenixGuard dashboard API on $baseUrl"
    $proc = Start-DashboardApi -ResolvedDataDir $resolvedDataDir -ResolvedLogsDir $resolvedLogsDir -Port $ApiPort -BindHost $ApiHost
    if (-not (Wait-ApiHealth -BaseUrl $baseUrl -TimeoutSec $HealthTimeoutSec)) {
        $stdoutLog = Join-Path $resolvedLogsDir "dashboard_api_stdout.log"
        $stderrLog = Join-Path $resolvedLogsDir "dashboard_api_stderr.log"
        throw "PhoenixGuard dashboard API did not become healthy on $baseUrl. Inspect $stdoutLog and $stderrLog."
    }
    Write-Host "PhoenixGuard dashboard API started with PID $($proc.Id)"
} else {
    Write-Host "PhoenixGuard dashboard API already healthy on $baseUrl"
}

Write-Host "Warming tracker runtime on $baseUrl (first start can take around 2 minutes)..."
try {
    $null = Invoke-ApiRequest -Uri "$baseUrl/v1/mobile/window-tracker/sessions?limit=1" -TimeoutSec $WarmTimeoutSec
} catch {
    Write-Warning "Tracker runtime warm-up request failed: $($_.Exception.Message)"
    throw "PhoenixGuard tracker runtime did not warm successfully on $baseUrl."
}

try {
    $session = Invoke-ApiRequest -Uri $sessionUrl -TimeoutSec $WarmTimeoutSec
} catch {
    Write-Host "Tracker session '$SessionId' was not found. Creating a locked Pocket Option live session."
    $createBody = @{
        session_id = $SessionId
        name = $SessionId
        window_query = "Pocket Option"
        layout_profile = "auto"
        capture_interval_sec = $normalizedCaptureIntervalSec
        auto_start = $false
        observer_policy = @{
            single_surface_mode = $true
            min_actionable_confidence = 0.58
            min_thesis_confidence = 0.46
            signal_cooldown_sec = 8.0
        }
    } | ConvertTo-Json -Depth 6

    $session = Invoke-ApiRequest `
        -Method Post `
        -Uri "$baseUrl/v1/mobile/window-tracker/sessions" `
        -ContentType "application/json" `
        -Body $createBody `
        -TimeoutSec $WarmTimeoutSec
}

try {
    $controlBody = @{
        capture_interval_sec = $normalizedCaptureIntervalSec
        live_execution_enabled = $true
        execution_mode = "shadow"
        allow_countertrend_scalp = $false
        scenario_generation_enabled = $true
        auto_memory_projection = $true
        require_market_identity = $true
        require_timeframe_identity = $false
        min_capture_interval_sec = 0.5
        max_capture_interval_sec = 10.0
        max_executions_per_window = 3
        execution_window_sec = 300.0
        cooldown_sec = 45.0
    } | ConvertTo-Json -Depth 4
    $session = Invoke-ApiRequest `
        -Method Patch `
        -Uri "$sessionUrl/controls" `
        -ContentType "application/json" `
        -Body $controlBody `
        -TimeoutSec $WarmTimeoutSec
    Write-Host "Tracker timing hardened: base=$($session.capture_interval_sec)s, adaptive min=$($session.execution_controls.min_capture_interval_sec)s, max=$($session.execution_controls.max_capture_interval_sec)s."
} catch {
    Write-Warning "Tracker timing controls could not be synced automatically: $($_.Exception.Message)"
}

$manualFocusEnabled = $false
if ($null -ne $session.manual_focus_region) {
    $manualFocusEnabled = [bool]$session.manual_focus_region.enabled
}

if (($session.status -eq "running" -or [bool]$session.tracking_enabled) -and -not $manualFocusEnabled) {
    Write-Host "Tracker session '$SessionId' is running without a saved broker focus. Pausing it so you can lock the exact Pocket Option region first."
    try {
        $session = Invoke-ApiRequest -Method Post -Uri "$sessionUrl/stop" -TimeoutSec $WarmTimeoutSec
    } catch {
        Write-Warning "Tracker session '$SessionId' could not be paused automatically: $($_.Exception.Message)"
    }
}

if (-not $manualFocusEnabled) {
    Write-Host "Broker focus is not set yet. In the dashboard, arm broker focus, go to Pocket Option, press Ctrl+V, drag the exact chart area, press Enter, then start the tracker."
} elseif ($session.status -ne "running") {
    Write-Host "Broker focus is already saved. Starting shadow tracker automatically."
    try {
        $session = Invoke-ApiRequest -Method Post -Uri "$sessionUrl/start" -TimeoutSec $WarmTimeoutSec
    } catch {
        Write-Warning "Tracker session '$SessionId' could not be started automatically: $($_.Exception.Message)"
    }
}

Write-Host "Live tracker session: $($session.session_id) | status=$($session.status) | focus_locked=$manualFocusEnabled | capture_count=$($session.capture_count) | frame_index=$($session.frame_index)"
Write-Host "API logs: $(Join-Path $resolvedLogsDir 'mobile_api.log')"
if ($null -ne $session.event_log_path -and [string]$session.event_log_path) {
    Write-Host "Tracker event log: $($session.event_log_path)"
}

if ($voiceControlEnabled) {
    try {
        $voiceState = Enable-VoiceRuntime `
            -BaseUrl $baseUrl `
            -ResolvedSessionId $SessionId `
            -CaptureIntervalSec ([double]$session.capture_interval_sec) `
            -AutomaticTimerEnabled ([bool]$session.tracking_enabled) `
            -TimeoutSec $WarmTimeoutSec
        if ($null -ne $voiceState.snapshot) {
            Write-Host "Voice runtime synced to session '$SessionId' | voice_enabled=$($voiceState.snapshot.voice_enabled) | listening_enabled=$($voiceState.snapshot.listening_enabled)"
        }
    } catch {
        Write-Warning "Voice runtime sync failed: $($_.Exception.Message)"
    }

    $existingVoiceBridge = @(Get-VoiceBridgeProcesses -Port $ApiPort)
    if ($existingVoiceBridge.Count -eq 0) {
        $voiceProc = Start-VoiceBridge -BindHost $ApiHost -Port $ApiPort -ResolvedSessionId $SessionId
        Write-Host "PhoenixGuard voice bridge started with PID $($voiceProc.Id). It will ask permission for microphone and audio output in its own window."
    } else {
        Write-Host "PhoenixGuard voice bridge is already running for port $ApiPort."
    }
} else {
    try {
        $stoppedVoiceBridges = Restart-VoiceBridge -Port $ApiPort
        if ($stoppedVoiceBridges -gt 0) {
            Write-Host "Stopped $stoppedVoiceBridges existing PhoenixGuard voice bridge process(es) because voice control is disabled."
        }
    } catch {
        Write-Warning "Voice bridge cleanup failed: $($_.Exception.Message)"
    }
    Write-Host "Voice control is disabled by default. Use -EnableVoiceControl only when you want the optional microphone command bridge."
}

Write-Host "Dashboard URL: $dashboardUrl"
if (-not $NoBrowser) {
    Start-Process $dashboardUrl | Out-Null
}
