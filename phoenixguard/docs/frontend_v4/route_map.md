# PhoenixGuard Frontend V4 Route Map

Source: `Backend/src/phoenixguard/mobile_api/app.py`

## Dashboard Entry Routes

| Method | Route | Dashboard role | Current consumer |
| --- | --- | --- | --- |
| GET | `/v1/mobile/window-tracker/dashboard` | Default dashboard HTML for resolved session. | Browser entrypoint. |
| GET | `/v1/mobile/window-tracker/dashboard/{session_id}` | Dashboard HTML for explicit session. | Browser entrypoint with session binding. |
| GET | `/v1/mobile/window-tracker/assets/{asset_name}` | Static brand/background assets used by the dashboard. | CSS background images in `window_tracker_dashboard.html`. |

## Live Session Routes

| Method | Route | Bucket | Current DOM consumers |
| --- | --- | --- | --- |
| GET | `/v1/mobile/window-tracker/windows?query=Pocket Option` | Calibration | Future window picker / focus setup. |
| GET | `/v1/mobile/window-tracker/sessions` | Live | Session list source; not directly used by current static dashboard. |
| POST | `/v1/mobile/window-tracker/sessions` | Live / Calibration | Session creation source; not directly used by current static dashboard. |
| GET | `/v1/mobile/window-tracker/sessions/{session_id}` | Live / Council / Chart / Replay / Simulation / Calibration / Diagnostics / Settings | Primary polling route used by `refreshSession()`. Feeds `#signal-*`, `#metric-*`, `#kernel-*`, `#surface-*`, `#history-list`, `#prediction-*`, `#scenario-heatmap`, `#focus-*`, `#telemetry-*`, and control button states. |

## Tracker Action Routes

| Method | Route | Bucket | Current DOM trigger |
| --- | --- | --- | --- |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/start` | Live / Settings | `#tracker-toggle` when stopped. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/stop` | Live / Settings | `#tracker-toggle` when running. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/capture-once` | Live / Calibration | `#capture-now`. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/emergency-stop` | Settings / Diagnostics | `#emergency-stop` and Ctrl+Alt+End handler. |
| PATCH | `/v1/mobile/window-tracker/sessions/{session_id}/controls` | Settings / Simulation / Calibration | `#execution-toggle`, `#execution-mode-toggle`, `#counter-scalp-toggle`, `#scenario-toggle`, `#memory-gate-toggle`, `#identity-gate-toggle`, `#auto-memory-toggle`, `#adaptive-timer-toggle`. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/demo-random-trade` | Simulation | `#demo-trade`. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/predict` | Simulation | `#predict-now`. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/show-future` | Simulation | `#show-future`. |

## Focus And Calibration Routes

| Method | Route | Bucket | Current DOM trigger |
| --- | --- | --- | --- |
| PUT | `/v1/mobile/window-tracker/sessions/{session_id}/focus-region` | Calibration | Programmatic focus region set; future selector flow. |
| DELETE | `/v1/mobile/window-tracker/sessions/{session_id}/focus-region` | Calibration | `#focus-clear`. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/focus-region/arm` | Calibration | `#focus-arm`. |
| POST | `/v1/mobile/window-tracker/sessions/{session_id}/focus-region/cancel` | Calibration | Not directly wired in current static controls; belongs with focus selector cancellation. |

## Artifact Routes

| Method | Route | Bucket | Current DOM consumers |
| --- | --- | --- | --- |
| GET | `/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-chart` | Chart | Direct chart artifact route. |
| GET | `/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window` | Chart | Direct window artifact route. |
| GET | `/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{artifact_kind}` | Chart / Simulation / Replay | `artifactUrl(kind)` for overlay, raw, prediction, memory, and future image families. |

## Council And Diagnostics Routes

| Method | Route | Bucket | Current DOM consumers |
| --- | --- | --- | --- |
| GET | `/v1/mobile/model-council/health?session_id={session_id}` | Council / Diagnostics | `runtimeTelemetryUrl()` enrichment for `#telemetry-*` and `#model-health-panel`. |
| GET | `/v1/mobile/model-council/intelligence?session_id={session_id}` | Council | Available council intelligence route; not directly called by current dashboard. |
| GET | `/v1/mobile/model-council/sessions/{session_id}/execution/latest` | Council / Diagnostics | Latest execution packet for session; route available for V4 detail panel. |
| GET | `/v1/mobile/model-council/execution/latest?session_id={session_id}` | Council / Diagnostics | Latest execution packet with query session; route available for V4 detail panel. |

## Adjacent Mobile And Voice Routes

These are relevant to V4 IA but are not direct dependencies of the current static dashboard:

| Method | Route | IA relevance |
| --- | --- | --- |
| GET | `/v1/mobile/health` | Diagnostics API health. |
| GET | `/v1/mobile/config` | Settings/configuration metadata. |
| GET/POST | `/v1/mobile/jobs` | Manual mobile screenshot jobs. |
| GET | `/v1/mobile/jobs/{job_id}` | Manual job detail. |
| GET | `/v1/mobile/jobs/{job_id}/artifacts/{artifact_name}` | Manual job artifact. |
| GET/POST | `/v1/mobile/observer/sessions` | Observer session management. |
| GET | `/v1/mobile/observer/sessions/{session_id}` | Observer session detail. |
| GET | `/v1/mobile/observer/sessions/{session_id}/signals/latest` | Observer latest signal. |
| GET/POST | `/v1/mobile/observer/sessions/{session_id}/bundles` | Observer bundle submission/detail family. |
| GET | `/v1/voice/status` | Voice status overlay candidate. |
| GET | `/v1/voice/commands` | Voice command catalog. |
| POST | `/v1/voice/preferences` | Voice settings. |
| POST | `/v1/voice/command` | Voice action execution. |

## V4 Routing Recommendation

Keep the static HTML entry routes stable, and let V4 route internally by panel bucket. The backend route contract already supports this: a single session payload hydrates the majority of panels, while action routes mutate controls and focus state.
