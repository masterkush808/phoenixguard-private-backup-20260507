# PhoenixGuard Frontend V4 Performance Report

## Summary

The current dashboard is operational but latency is bounded by a 1000 ms polling loop plus two sequential JSON requests per refresh. The browser fetches the session payload, then fetches Model Council health telemetry, then renders the full dashboard. This is simple and robust, but it is not optimal for low-latency session updates.

Best next step: add an SSE session event endpoint and keep REST commands. Use fast polling only as fallback.

## Current Refresh Path

The static dashboard starts with:

```text
refreshSession()
setInterval(refreshSession, 1000)
```

Each refresh:

1. Checks `state.busy`.
2. Fetches `GET /v1/mobile/window-tracker/sessions/{session_id}`.
3. Fetches `GET /v1/mobile/model-council/health?session_id={session_id}`.
4. Merges telemetry into the session payload.
5. Calls `renderSession()`.

`renderSession()` updates many DOM regions every cycle:

- headline status metrics
- signal card
- study cells
- focus and execution controls
- kernel deck
- signal pills
- history list
- PhoenixGuard report cards
- telemetry panels
- surface caption
- image surface
- performance fields
- regression history
- inspector panel

## Performance Constraints

### Polling Adds Latency

With a 1000 ms interval, a newly published capture can wait almost a full second before the browser asks for it. Actual visible latency is:

```text
capture publish time
+ polling wait, 0-1000 ms
+ session fetch time
+ model council health fetch time
+ render time
+ image fetch/decode time when artifacts refresh
```

### Telemetry Blocks Session Rendering

`enrichSessionTelemetry()` waits for Model Council health after the session fetch. If telemetry is slow, the visible decision update is delayed even though the core session payload already arrived.

### `state.busy` Can Drop Refresh Opportunities

The dashboard avoids overlapping requests with a global busy flag. This is good for request control, but it couples unrelated work:

- a slow telemetry request can skip a session refresh
- a mutation request can pause polling
- private council computation must not delay regular public state reads

### Image Cache Busting Is Frame-Aware

Artifact URLs now append frame-aware `?v={artifact_frame_key}` values. This keeps freshness tied to display/overlay frame changes instead of forcing a new image request every render.

### Full Render Every Tick

The dashboard updates most panels every refresh. At one refresh per second this may be acceptable, but fast polling at 250 ms would make full rerendering expensive and visually noisy unless renders are gated by version changes.

## Recommended Low-Latency Architecture

Use SSE for low-latency state publication:

```text
GET /v1/mobile/window-tracker/sessions/{session_id}/events
```

Keep REST for commands and mutations. This matches the current request model while removing polling latency for session updates.

Recommended browser behavior:

- Subscribe with `EventSource`.
- Render `session.snapshot` immediately.
- Render `session.update` only when `state_version` or `decision_version` advances.
- Fetch image artifacts with frame/state versioned `?v={artifact_frame_key}` values.
- Poll Model Council health separately every 2-5 seconds, or emit it as a slower `telemetry.update` event.
- Fall back to fast polling if `EventSource` fails.

Recommended fallback polling:

| Mode | Interval |
| --- | --- |
| `tracking_enabled` true | 250-500 ms |
| `study_in_progress` true | 250 ms |
| idle but focused | 1000 ms |
| awaiting focus or error | 1500-2000 ms |
| telemetry health | 2000-5000 ms |

## Backend Fit

SSE fits the current FastAPI/static HTML architecture because:

- the UI currently needs one-way server-to-browser updates
- REST mutation endpoints already exist and can remain authoritative
- the public payload builder already exists in `_public_session_payload()`
- the server can emit update events after `_save_session()` or from a lightweight watcher loop
- no SPA framework or WebSocket command protocol is required

The first SSE implementation can be conservative:

1. Add an async generator endpoint in `app.py`.
2. Every 250 ms, call `get_window_tracker_service().get_session(session_id)`.
3. Compare a compact signature: `state_version`, `decision_version`, `capture_count`, `status`, `tracking_enabled`, `last_error`.
4. Emit only when the signature changes.
5. Emit heartbeat every 10-15 seconds.

This still polls internally, but it moves request pressure off every browser tick and lets the browser react immediately when the backend detects a change. A later version can publish directly from `_save_session()` through an in-memory session event bus.

## Concrete Implementation Recommendation

Phase 1 should avoid broad refactors:

- Add `GET /v1/mobile/window-tracker/sessions/{session_id}/events`.
- Use `StreamingResponse` with `text/event-stream`.
- Reuse `_public_session_payload()` by calling `get_session(session_id)`.
- Emit `session.snapshot`, `session.update`, and `session.heartbeat`.
- Keep all existing dashboard REST actions.
- Update the static dashboard to prefer `EventSource`, with the current `refreshSession()` loop retained as fallback.

Phase 2 can improve efficiency:

- Add an in-memory subscriber registry inside `ContinuousWindowTrackerService`.
- Notify subscribers after `_save_session()`.
- Split telemetry into an independent event or slower poll.
- Gate image URL changes by `state_version`.
- Track `lastRenderedStateVersion` and skip expensive render paths when only countdown/freshness fields changed.

## Expected Impact

| Change | Expected Result |
| --- | --- |
| SSE session events | Session updates visible without waiting for the next 1000 ms poll tick. |
| Telemetry decoupling | Signal/action panels render even if health telemetry is slow. |
| Version-keyed artifact URLs | Browser can cache unchanged images within a state version. |
| Render gating by `state_version` | Lower DOM work during fast updates and countdown-only changes. |
| Fast polling fallback | Low-latency behavior remains available where SSE is blocked. |

## Risk Notes

- `get_session()` may start or ensure workers for running sessions and may capture a preview for locked non-running sessions. An SSE loop should avoid accidentally increasing capture work. If that becomes a concern, add a read-only projection method that does not trigger preview capture.
- Multiple open dashboard tabs would each create an SSE stream. The stream loop must be lightweight and disconnect-aware.
- If event payloads include the full session projection, large fields such as history, scenario analysis, and model council payloads may grow. Start with full projection for compatibility, then introduce domain-specific patches if needed.

## Acceptance Criteria

- A running session update reaches the dashboard in under 500 ms after backend publication on local loopback under normal load.
- A slow telemetry endpoint does not delay `latest_signal` rendering.
- Artifact images do not reload unless `state_version` changes.
- REST commands still work without WebSocket-specific command handling.
- The current 1000 ms polling path remains as fallback and recovery mode.
