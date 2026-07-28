# PhoenixGuard Frontend V4 Realtime Event Schema

## Position

Despite the filename, the recommended first realtime transport for the current PhoenixGuard dashboard is Server-Sent Events, not WebSocket. The present UI sends commands with REST and only needs low-latency session updates from server to browser.

This schema can still be reused by a future WebSocket transport. Event names and payload bodies should remain transport-neutral.

## Transport Recommendation

Primary:

```text
GET /v1/mobile/window-tracker/sessions/{session_id}/events
Accept: text/event-stream
```

Fallback:

```text
GET /v1/mobile/window-tracker/sessions/{session_id}
```

The fallback should fast-poll at 250-500 ms during active tracking and back off to 1000-2000 ms when idle. Model Council health should be polled independently at a slower cadence.

## Event Envelope

Every event payload should use this envelope:

```json
{
  "event": "session.update",
  "session_id": "pocket-live-8788",
  "state_version": 12345,
  "decision_version": 12345,
  "capture_count": 17,
  "frame_index": 17,
  "published_epoch": 1779270000.123,
  "server_epoch": 1779270000.456,
  "payload": {}
}
```

Required envelope fields:

| Field | Type | Notes |
| --- | --- | --- |
| `event` | string | Event type. Should match the SSE `event:` field. |
| `session_id` | string | Window tracker session id. |
| `state_version` | integer | Current session projection version. Use `0` for pre-capture events. |
| `decision_version` | integer | Current decision/trade intent version. Use `0` when unavailable. |
| `capture_count` | integer | Completed capture count. |
| `frame_index` | integer | Current frame index. |
| `published_epoch` | number | Latest signal publish epoch when available. |
| `server_epoch` | number | Time the event was emitted. |
| `payload` | object | Event-specific body. |

Recommended SSE framing:

```text
id: 12345
event: session.update
data: {"event":"session.update","session_id":"pocket-live-8788","state_version":12345,...}
```

## Event Types

### `session.snapshot`

Emitted immediately after subscription.

Payload:

```json
{
  "session": {
    "session_id": "pocket-live-8788",
    "status": "running",
    "tracking_enabled": true,
    "capture_count": 17,
    "frame_index": 17,
    "state_version": 12345,
    "decision_version": 12345,
    "latest_signal": {},
    "tracking_summary": {},
    "execution_controls": {},
    "broker_execution_state": {},
    "manual_focus_region": {},
    "focus_selector": {},
    "scenario_analysis": {}
  }
}
```

The snapshot can be the same projection returned by `GET /v1/mobile/window-tracker/sessions/{session_id}`.

### `session.update`

Emitted when the public session projection changes materially.

Payload:

```json
{
  "patch_mode": "replace",
  "session": {
    "status": "running",
    "tracking_enabled": true,
    "study_in_progress": false,
    "capture_count": 18,
    "frame_index": 18,
    "state_version": 12388,
    "decision_version": 12388,
    "last_capture_at": "2026-05-20T09:31:45Z",
    "signal_age_sec": 0.0,
    "freshness_score": 1.0,
    "next_capture_in_sec": 0.8,
    "effective_capture_interval_sec": 1.0,
    "latest_signal": {},
    "tracking_summary": {},
    "trade_intent": {},
    "model_council": {},
    "model_council_packet": {},
    "recent_studies": []
  }
}
```

For the first implementation, use `patch_mode: "replace"` and include the normal public session projection. A later implementation can emit JSON Patch or narrow domain patches after the UI has a stable store.

### `session.status`

Emitted when lifecycle or control state changes without a new frame.

Payload:

```json
{
  "status": "awaiting_focus",
  "tracking_enabled": false,
  "study_in_progress": false,
  "last_error": "",
  "manual_focus_region": {},
  "focus_selector": {},
  "execution_controls": {}
}
```

Use for start/stop, focus arm/cancel, focus clear, emergency stop, and control patch responses.

### `session.media`

Emitted when artifacts advance.

Payload:

```json
{
  "state_version": 12388,
  "artifacts": {
    "chart": "/v1/mobile/window-tracker/sessions/pocket-live-8788/artifacts/latest-chart?v=12388",
    "window": "/v1/mobile/window-tracker/sessions/pocket-live-8788/artifacts/latest-window?v=12388",
    "overlay": "/v1/mobile/window-tracker/sessions/pocket-live-8788/artifacts/latest-overlay?v=12388",
    "full_overlay": "/v1/mobile/window-tracker/sessions/pocket-live-8788/artifacts/latest-full-overlay?v=12388"
  }
}
```

The current dashboard uses frame-aware `?v={artifact_frame_key}` URLs, including display frame, overlay frame, source capture id, and artifact path where available. V4 should keep that state-versioned pattern so unchanged images stay cacheable inside a frame version.

### `telemetry.update`

Emitted or polled on a slower cadence than session updates.

Payload:

```json
{
  "model_council_health": {},
  "runtime_telemetry": {
    "compute": {},
    "queue": {},
    "latency": {},
    "packet": {},
    "cache": {},
    "frames": {},
    "paper": {},
    "path_quality": {}
  }
}
```

This event should not be required to render the latest signal. If telemetry is slow or missing, decision panels should still update.

### `session.error`

Emitted when the stream can continue but a recoverable error occurred.

Payload:

```json
{
  "message": "The locked broker window is not visible right now.",
  "code": "WINDOW_NOT_VISIBLE",
  "status": "waiting_for_window",
  "retryable": true
}
```

### `session.heartbeat`

Emitted every 10-15 seconds.

Payload:

```json
{
  "status": "running",
  "tracking_enabled": true,
  "server_epoch": 1779270000.456
}
```

## Browser Subscription Contract

Recommended static dashboard implementation:

```javascript
function connectSessionEvents() {
  if (!window.EventSource) {
    startFastPolling();
    return;
  }

  const source = new EventSource(`${sessionUrl()}/events`);
  source.addEventListener("session.snapshot", (event) => {
    applySessionEnvelope(JSON.parse(event.data));
  });
  source.addEventListener("session.update", (event) => {
    applySessionEnvelope(JSON.parse(event.data));
  });
  source.addEventListener("session.status", (event) => {
    applySessionEnvelope(JSON.parse(event.data));
  });
  source.addEventListener("session.media", (event) => {
    applyMediaEnvelope(JSON.parse(event.data));
  });
  source.onerror = () => {
    source.close();
    startFastPolling();
  };
}
```

## Backend Publication Contract

The backend should emit an event when any of these values changes:

- `state_version`
- `decision_version`
- `capture_count`
- `frame_index`
- `status`
- `tracking_enabled`
- `study_in_progress`
- `last_error`
- `manual_focus_region.updated_at`
- `execution_controls`

The existing `_public_session_payload()` should remain the projection builder for event payloads. This avoids creating a second schema path.

## Compatibility Notes

The existing REST mutation endpoints should remain unchanged:

- `POST /start`
- `POST /stop`
- `POST /emergency-stop`
- `POST /capture-once`
- `POST /demo-random-trade`
- `PATCH /controls`
- focus-region endpoints

After each mutation, the client can trust either the REST response or the next SSE event. The safest V4 behavior is to render the REST response immediately and then reconcile with the next event by `state_version`.
