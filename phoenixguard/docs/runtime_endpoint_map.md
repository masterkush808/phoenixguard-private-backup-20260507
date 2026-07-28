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
| `PATCH /v1/mobile/window-tracker/sessions/{session_id}/controls` | Update tracker controls | Runtime setup |
| `GET /v3/mobile/window-tracker/dashboard[/<session_id>]` | Canonical V3 HTML dashboard; `/v1/...` remains a compatibility alias | Diagnostic |

Continuous market study has no manual baseline-control API. Starting the tracker starts capture;
each new pair/timeframe-scoped closed-candle identity advances the study exactly once, while repeated
frames remain idempotent. The current bounded study is delivered through the tracker session,
operator workspace, SSE, compact live state, and V3 dashboard surfaces.

`MarketStudyServiceV3` integrates the bounded research services into that continuous pipeline. Its
study object carries `motif_lattice`, `survival_network`, `path_reconstruction`,
`adaptive_feature_ontology`, `concept_drift`, `regime_partition`, `cross_pair_association`, and
`claim_proofs`. The pair-scoped ontology persists its bounded audit state, while concept-drift
snapshots and append-stable regime partitions persist inside Pair DNA and replay idempotently.
The atomic cross-pair coordinator retains only bounded normalized returns and compares distinct pairs
only at exact shared closed timestamps; without a compatible peer or sufficient support it publishes
`INSUFFICIENT_SYNCHRONIZED_PAIR` or `INSUFFICIENT_SUPPORT`, never a synthetic edge.

These are study-only fields inside the existing market-study object, not new action endpoints. Raw
identities, full feature windows, persistence details, and private evidence still require an explicit
allowlist before reaching an operator surface. All Granger-style and mutual-information results are
explicitly non-causal associations; they do not establish influence, predict a direction, or
authorize entry.

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
