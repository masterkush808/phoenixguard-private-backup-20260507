# PhoenixGuard Frontend V4 State Architecture

## Scope

This document describes the current realtime state architecture for the FastAPI plus static HTML window tracker dashboard. It is based on:

- `Backend/src/phoenixguard/mobile_api/app.py`
- `Backend/src/phoenixguard/mobile_api/window_tracker.py`
- `Frontend/dashboard/static/window_tracker_dashboard.html`

No SPA framework is present today. The dashboard is a server-rendered static HTML file with a large embedded JavaScript runtime and direct REST calls.

## Current Runtime Shape

FastAPI owns all authoritative state access. The dashboard page is served by:

- `GET /v1/mobile/window-tracker/dashboard`
- `GET /v1/mobile/window-tracker/dashboard/{session_id}`

The HTML template receives a concrete `SESSION_ID` by string replacement. The browser then derives the session API root as:

```text
/v1/mobile/window-tracker/sessions/{SESSION_ID}
```

The main session read path is:

```text
dashboard JS -> GET /v1/mobile/window-tracker/sessions/{session_id}
             -> app.py get_tracker_session()
             -> ContinuousWindowTrackerService.get_session()
             -> _public_session_payload()
```

The model council telemetry path is separate:

```text
dashboard JS -> GET /v1/mobile/model-council/health?session_id={session_id}
             -> app.py model_council_health()
             -> build_model_council_health_from_session()
```

The dashboard currently merges those two responses client-side through `enrichSessionTelemetry()`.

## State Sources

The session JSON persisted by `ContinuousWindowTrackerService` is the base source of truth. It is stored under:

```text
data/mobile_api/window_tracker/sessions/{session_id}/session.json
```

Frame and image artifacts are stored beside the session in the session directory. The browser never reads those paths directly; it requests stable artifact endpoints:

- `GET /v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-chart`
- `GET /v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window`
- `GET /v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{artifact_kind}`

The public session payload is not a raw file read. `_public_session_payload()` normalizes and enriches the stored payload before returning it to the dashboard.

## Public Session Projection

The frontend should treat the API response as a projection, not as storage schema. Important top-level fields currently consumed or suitable for SPA state are:

| Field | Meaning |
| --- | --- |
| `session_id` | Stable session key injected into the dashboard URL/template. |
| `status` | Derived user-visible state: `running`, `ready`, `awaiting_focus`, `error`, etc. |
| `tracking_enabled` | Whether the continuous worker should be active. |
| `study_in_progress` | Whether this session is inside an active capture/study. |
| `capture_count` | Number of completed captures. |
| `frame_index` | Monotonic frame counter. |
| `state_version` | Derived version from capture count, frame index, and publish epoch. |
| `decision_version` | Trade intent state version when an actionable intent exists; otherwise usually mirrors `state_version`. |
| `last_capture_at` / `last_capture_epoch` | Published capture timestamp. |
| `signal_age_sec` | Computed at request time from latest signal publish epoch. |
| `freshness_score` / `freshness_window_sec` | Computed freshness model for the latest signal. |
| `decision_valid_until_epoch` | Computed expiry of the current decision. |
| `next_capture_in_sec` | Runtime countdown to the next scheduled capture. |
| `capture_interval_sec` | Configured baseline capture interval. |
| `effective_capture_interval_sec` | Adaptive interval after runtime planning. |
| `adaptive_timer_reason` | Explanation for adaptive interval selection. |
| `manual_focus_region` | Normalized focus region contract. |
| `focus_selector` | Focus selector support/status/error state. |
| `locked_window` / `locked_title` | Current target window metadata. |
| `execution_controls` | Normalized execution and gating controls. |
| `broker_surface` | Broker-control detection and read-only amount policy. |
| `broker_execution_state` | Last known shadow/live execution state. |
| `tracking_summary` | Chart study summary used by most visual panels. |
| `latest_signal` | Current published decision/signal block. |
| `trade_intent` | Canonical executable trade intent when available. |
| `scenario_analysis` | A* scenario generation output. |
| `model_council_result` / `model_council` / `model_council_packet` | Model Council V3 decision state and packet. |
| `memory_projection_predict` / `memory_projection_future` / `memory_projection_current` | Memory projection state selected by active mode. |
| `recent_studies` | Compact history list for dashboard trend/history cards. |
| `last_*_path` | Server-side artifact paths used by the backend; frontend should prefer artifact URLs. |

## Client State Today

The static dashboard maintains a single mutable `state` object:

```text
state.session
state.mode
state.refreshTimer
state.busy
state.pendingMemoryAction
state.selected
state.layers
state.surface
```

The refresh loop calls `refreshSession()` every 1000 ms. That function:

1. Exits early if `state.busy` is true.
2. Fetches the session payload.
3. Fetches Model Council health telemetry.
4. Calls `renderSession()` with the merged payload.
5. On failure, renders a synthetic error session.

`postAction()` also sets `state.busy`, sends a mutation request, and immediately renders the returned payload with enriched telemetry.

## Architectural Risk

The current `state.busy` flag serializes all refreshes and mutations. This prevents duplicate overlapping requests, but it also means slow telemetry or mutation requests can block fresh session reads. Since `enrichSessionTelemetry()` performs a second HTTP request after the session fetch, a telemetry timeout delays the entire visible refresh.

Image rendering is now frame-cache-busted through `?v={artifact_frame_key}`, so the browser only gets a new artifact URL when the display, overlay, source capture, or artifact path changes. V4 should preserve that pattern and avoid render-time timestamps.

## Recommended V4 State Model

Keep the backend as the authority, but split frontend state into explicit domains:

| Domain | State |
| --- | --- |
| Session core | `session_id`, `status`, `tracking_enabled`, `capture_count`, `frame_index`, `state_version`, `decision_version`, freshness fields. |
| Decision | `latest_signal`, `trade_intent`, `tracking_summary`, `model_council`, `model_council_packet`. |
| Runtime | `study_in_progress`, `next_capture_in_sec`, `effective_capture_interval_sec`, `runtime_telemetry`. |
| Controls | `execution_controls`, `manual_focus_region`, `focus_selector`, `broker_execution_state`. |
| Media | Artifact URLs keyed by `state_version` and artifact kind. |
| Local UI | Selected inspector panel, zoom/pan, active layers, pending command state. |

The frontend should render from immutable snapshots where possible:

```text
incoming session event -> compare state_version/decision_version -> update store -> render changed panels only
```

The lowest-risk step inside the current static dashboard is to track `lastRenderedStateVersion` and only refresh image URLs when the version changes.

## Recommended Low-Latency Update Path

Add a Server-Sent Events endpoint as the primary low-latency path, with fast polling fallback for browsers or deployments where streaming is unavailable.

Recommended endpoint:

```text
GET /v1/mobile/window-tracker/sessions/{session_id}/events
```

Recommended event behavior:

- Emit an initial `session.snapshot` event immediately.
- Emit `session.update` when `state_version`, `decision_version`, `capture_count`, `status`, or `last_error` changes.
- Emit lightweight `session.heartbeat` every 10-15 seconds.
- Include `id: {state_version}` where possible so the browser can reconnect with `Last-Event-ID`.
- Keep payloads JSON and compatible with the current public session projection.

Fallback:

- Poll `GET /v1/mobile/window-tracker/sessions/{session_id}` every 250-500 ms while `tracking_enabled` or `study_in_progress` is true.
- Back off to 1000-2000 ms while idle or awaiting focus.
- Poll telemetry separately at 2000-5000 ms because telemetry should not block decision updates.

SSE is preferred over WebSocket here because the current architecture is one-way server-to-dashboard state publication plus REST mutations. WebSocket is useful later if the UI needs high-frequency bidirectional commands, but SSE fits the existing FastAPI/static HTML system with less protocol and lifecycle complexity.
