# Runtime Endpoint Map

## Mobile API App

Defined in `Backend/src/phoenixguard/mobile_api/app.py`.

| Endpoint | Purpose | Current authority |
| --- | --- | --- |
| `GET /v1/mobile/health` | API health check | Runtime status only |
| `GET /v1/mobile/model-council/health` | Model Council uptime health from tracker/session state | Runtime integrity input |
| `GET /v1/mobile/model-council/intelligence` | Council alignment/intelligence health from tracker/session state | Diagnostic |
| `GET /v1/mobile/model-council/sessions/{session_id}/execution/latest` | Latest `PG_EXECUTION_PACKET_V3` for a tracker session | Model Council packet delivery |
| `GET /v1/mobile/model-council/execution/latest?session_id=...` | Alias for latest session packet; resolves latest session when omitted | Model Council packet delivery |
| `GET /v1/mobile/config` | Mobile API config | Diagnostic/config |
| `GET /v1/mobile/jobs` | List manual quartet jobs | Diagnostic |
| `POST /v1/mobile/jobs` | Submit manual quartet analysis | Analysis producer |
| `GET /v1/mobile/jobs/{job_id}` | Read manual job result | Diagnostic |
| `GET /v1/mobile/jobs/{job_id}/artifacts/{artifact_name}` | Read job artifact | Diagnostic |
| `GET /v1/mobile/observer/config` | Observer config | Diagnostic |
| `GET /v1/mobile/observer/sessions` | List observer sessions | Diagnostic |
| `POST /v1/mobile/observer/sessions` | Create observer session | State producer |
| `GET /v1/mobile/observer/sessions/{session_id}` | Read observer session | Diagnostic/current state |
| `GET /v1/mobile/observer/sessions/{session_id}/signals/latest` | Read latest legacy observer signal | Must not be live execution authority under V3 |
| `POST /v1/mobile/observer/sessions/{session_id}/bundles` | Submit observer bundle | Analysis producer |
| `GET /v1/mobile/window-tracker/windows` | List candidate broker windows | Diagnostic/setup |
| `GET /v1/mobile/window-tracker/sessions` | List tracker sessions | Diagnostic |
| `POST /v1/mobile/window-tracker/sessions` | Create tracker session | State producer |
| `GET /v1/mobile/window-tracker/sessions/{session_id}` | Read tracker session/latest signal | Diagnostic/current state |
| `PUT/DELETE /v1/mobile/window-tracker/sessions/{session_id}/focus-region` | Manage capture focus | Runtime setup |
| `GET /v1/mobile/operator/state/v1/{session_id}` | Privacy-safe exact-frame operator workspace | Public operator truth |
| `GET /v1/mobile/window-tracker/sessions/{session_id}/events` | Server-sent session updates | Public state delivery |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/start` | Start continuous tracker | Capture producer |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/stop` | Stop tracker | Runtime control |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/emergency-stop` | Stop tracker immediately | Runtime control |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/capture-once` | Single capture | Capture producer |
| `GET /v1/mobile/window-tracker/sessions/{session_id}/tracking-episodes/readiness` | Validate the fixed 12-event baseline inputs | Episode readiness |
| `GET /v1/mobile/window-tracker/sessions/{session_id}/tracking-episodes/current` | Read current/retained episode progress | Episode state |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/tracking-episodes/start` | Freeze and start one 12-closed-candle episode | Episode control |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/tracking-episodes/stop` | Stop only the episode and retain its record | Episode control |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/predict` | Tracker prediction action | Diagnostic/analysis |
| `POST /v1/mobile/window-tracker/sessions/{session_id}/show-future` | Future projection action | Diagnostic |
| `GET /v1/mobile/window-tracker/sessions/{session_id}/forecast-actions/{request_id}` | Poll an immutable forecast action | Diagnostic/analysis |
| `PATCH /v1/mobile/window-tracker/sessions/{session_id}/controls` | Update tracker controls | Runtime setup |
| `GET /v3/mobile/window-tracker/dashboard[/<session_id>]` | Canonical V3 HTML dashboard; `/v1/...` remains a compatibility alias | Diagnostic |

## Model Council Daemon

Defined in `Backend/src/phoenixguard/runtime/model_council_daemon.py`.

| Endpoint | Purpose | Current authority |
| --- | --- | --- |
| `GET /status` | Local ensemble/runtime status | Health input only |
| `POST /predict` | Local ensemble prediction | Evidence contributor only |

## V3 Execution Packet Endpoints

The FastAPI app now defines the two Model Council packet paths consumed by the V3 shooter:

- `GET /v1/mobile/model-council/sessions/{session_id}/execution/latest`
- `GET /v1/mobile/model-council/execution/latest?session_id=...`

These endpoints return a direct `PG_EXECUTION_PACKET_V3` object and return HTTP 404 when no executable Model Council packet is present.

## Legacy Execution Endpoint Gap

The shooter still lists this observer path for compatibility probing, but the API does not expose it and it must not become a live authority:

- `GET /v1/mobile/observer/sessions/{session_id}/execution/latest`

Observer latest-signal endpoints remain legacy diagnostics, not valid V3 execution authority.
