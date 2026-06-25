param(
    [string]$ApiHost = "127.0.0.1",
    [int]$Port = 8788,
    [string]$SessionId = "pocket-live-8788",
    [double]$IntervalSec = 5.0,
    [switch]$Follow,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

function Get-TrackerSession {
    param(
        [string]$BaseUrl,
        [string]$TargetSessionId
    )

    return Invoke-RestMethod -Uri "$BaseUrl/v1/mobile/window-tracker/sessions/$TargetSessionId" -TimeoutSec 30
}

function Write-TrackerSummary {
    param(
        [object]$Payload
    )

    $signal = $Payload.latest_signal
    $tracking = $Payload.tracking_summary
    $reasons = @()
    if ($signal -and $signal.reasons) {
        $reasons = @($signal.reasons | Select-Object -First 5)
    }

    $summary = [ordered]@{
        fetched_at = (Get-Date).ToString("s")
        session_id = $Payload.session_id
        market = $Payload.market
        tracker_status = $Payload.status
        locked_title = $Payload.locked_title
        capture_count = $Payload.capture_count
        frame_index = $Payload.frame_index
        last_capture_at = $Payload.last_capture_at
        signal_action = $signal.action
        candidate_action = $signal.candidate_action
        model_action = $signal.model_action
        execution_action = $signal.execution_action
        signal_status = $signal.status
        effective_confidence = $signal.effective_confidence
        candidate_confidence = $signal.candidate_confidence
        raw_confidence = $signal.raw_confidence
        freshness_score = $signal.freshness_score
        gate_state = $signal.gate_state
        gate_strength = $signal.gate_strength
        timing_state = $signal.timing_state
        timing_score = $signal.timing_score
        execution_permission = $signal.execution_permission
        actionable = $signal.actionable
        transition = $signal.transition
        summary = $signal.summary
        visible_candle_count = $tracking.visible_candle_count
        active_track_count = $tracking.active_track_count
        latest_candle_color = $tracking.latest_candle_color
        chart_path = $Payload.last_chart_path
        frame_path = $Payload.last_frame_path
        reasons = $reasons
    }

    $summary.GetEnumerator() | ForEach-Object {
        $value = $_.Value
        if ($value -is [System.Collections.IEnumerable] -and $value -isnot [string]) {
            $value = ($value -join " | ")
        }
        "{0,-22} {1}" -f "$($_.Key):", $value
    }
}

$baseUrl = "http://$ApiHost`:$Port"

if ($Json) {
    $payload = Get-TrackerSession -BaseUrl $baseUrl -TargetSessionId $SessionId
    $payload | ConvertTo-Json -Depth 10
    exit 0
}

do {
    try {
        $payload = Get-TrackerSession -BaseUrl $baseUrl -TargetSessionId $SessionId
        Clear-Host
        Write-TrackerSummary -Payload $payload
    } catch {
        Clear-Host
        "tracker_error: $($_.Exception.Message)"
    }

    if (-not $Follow) {
        break
    }
    Start-Sleep -Milliseconds ([int]([Math]::Max(500.0, $IntervalSec * 1000.0)))
} while ($true)
