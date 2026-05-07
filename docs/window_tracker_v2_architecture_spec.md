# Window Tracker V2 Architecture Specification

This document defines the target v2 design for the PhoenixGuard window tracker. It is the future-state blueprint that should replace the current tightly coupled live-tracker orchestration over time.

The current implementation reference remains [docs/window_tracker_deep_dive.md](</c:/Users/thaba/OneDrive/Documents/The 808 Vision 2026/phoenixguard/docs/window_tracker_deep_dive.md>).

## 1. Purpose

Window Tracker v2 must evolve the tracker from a smart monolith into a modular perception platform with explicit contracts, asynchronous processing, replay-driven evaluation, and production-grade runtime controls.

The v2 system must:

1. separate capture, perception, temporal state, decision, learning, and presentation concerns
2. treat stance, setup, and trigger as different layers with different flip speeds
3. replace static layout dependence with adaptive chart understanding
4. support asynchronous frame processing with bounded queues and backpressure
5. make every decision replayable, inspectable, and versioned
6. expose operator-grade diagnostics, especially "why not execute"
7. preserve compatibility with the current dashboard and session workflow during migration

## 2. Target Logical Architecture

The target architecture is:

Capture Engine
-> Perception Engine
-> Temporal State Engine
-> Decision Engine
-> Learning and Replay Engine
-> Operator Workstation and API

### 2.1. Capture Layer

Responsibilities:

- window discovery
- lock scoring
- raw frame capture
- frame fingerprinting
- immutable raw artifact persistence

Must not own:

- OCR
- candle parsing
- stance or execution decisions

### 2.2. Perception Layer

Responsibilities:

- layout classification
- chart surface segmentation
- UI chrome masking
- dynamic ROI refinement
- OCR for market and timeframe
- candle, box, axis, and structure extraction
- confidence fusion

### 2.3. Temporal State Layer

Responsibilities:

- persistent candle identity
- continuity scoring
- short-memory trend state
- regime lock
- reversal readiness

### 2.4. Decision Layer

Responsibilities:

- stance
- setup
- trigger
- veto logic
- confidence calibration
- final execution phrase

### 2.5. Learning Layer

Responsibilities:

- immutable replay logging
- future outcome resolution
- calibration reports
- shadow policy evaluation
- promotion gating

### 2.6. Presentation and API Layer

Responsibilities:

- dashboard payloads
- artifact serving
- operator actions
- session control
- diagnostics
- health and observability routes

## 3. Runtime Topology

V2 replaces the blocking observer wait path with an event-driven pipeline.

Core workers:

- `capture-worker`
- `perception-worker`
- `temporal-worker`
- `decision-worker`
- `learning-worker`
- `artifact-worker`
- `dashboard-publisher`

Recommended bounded queues:

- `capture.raw_frame`
- `perception.snapshot`
- `temporal.state`
- `decision.output`
- `learning.replay_candidate`
- `learning.outcome_resolution`
- `presentation.session_projection`

Backpressure rule:

- if downstream is saturated, the session drops stale frames before it drops liveness
- queue depth and overflow become first-class health metrics

## 4. Module Catalog

### 4.1. Capture Modules

| Module | Responsibility | Input | Output |
|---|---|---|---|
| `WindowRegistry` | enumerate visible windows | OS state | `WindowDescriptor[]` |
| `LockManager` | choose and maintain best target window | descriptors + session config | `WindowLockState` |
| `FrameGrabber` | capture current locked surface | lock state | `RawFrame` |
| `Fingerprinter` | detect duplicate frames | raw frame | `FrameFingerprint` |
| `FrameStore` | persist raw artifacts | raw frame + metadata | `ArtifactRef` |

### 4.2. Perception Modules

| Module | Responsibility | Input | Output |
|---|---|---|---|
| `LayoutClassifier` | classify layout family and confidence | raw frame | `LayoutDetection` |
| `ChromeMasker` | isolate non-chart UI chrome | raw frame + layout detection | `ChromeMask` |
| `ChartSegmenter` | detect chart canvas candidate | masked frame | `ChartSurfaceDetection` |
| `RoiRefiner` | refine analysis and display ROIs | chart surface detection | `ChartRoi` |
| `LabelReader` | read market and timeframe | ROI crops | `LabelReadout` |
| `CandleDetector` | detect visible candles and boxes | chart ROI | `CandleObservation[]` |
| `StructureParser` | build perception snapshot | detections + labels | `PerceptionSnapshot` |

### 4.3. Temporal Modules

| Module | Responsibility | Input | Output |
|---|---|---|---|
| `TrackManager` | assign candle identity across frames | perception snapshot + prior tracks | `CandleTrack[]` |
| `ContinuityEngine` | score continuity and sequencing quality | tracks | `ContinuityState` |
| `RegimeEngine` | estimate slow-moving local regime | tracks + continuity | `RegimeState` |
| `ReversalEngine` | estimate reversal maturity | regime + latest structure | `ReversalState` |
| `TemporalStore` | persist rolling temporal state | temporal outputs | `TemporalState` |

### 4.4. Decision Modules

| Module | Responsibility | Input | Output |
|---|---|---|---|
| `StanceEngine` | compute bullish, bearish, or neutral stance | temporal state | `StanceState` |
| `SetupEngine` | classify continuation, pullback, reversal attempt, consolidation, transition | temporal + perception | `SetupState` |
| `TriggerEngine` | classify waiting, armed, ready, invalidated | stance + setup + timing | `TriggerState` |
| `VetoEngine` | apply hard blockers | full decision context | `VetoState` |
| `Calibrator` | split and calibrate confidence | decision states | `ConfidenceBreakdown` |
| `DecisionOrchestrator` | produce final operator-facing decision | all above | `DecisionOutput` |

### 4.5. Learning and Presentation Modules

| Module | Responsibility | Input | Output |
|---|---|---|---|
| `ReplayStore` | immutable append-only decision logs | `DecisionOutput` + snapshots | `ReplayRecord` |
| `LabelResolver` | resolve later outcomes | replay record + future state | `OutcomeLabel` |
| `Evaluator` | compute performance and calibration by version | replay + labels | `EvaluationReport` |
| `ShadowRunner` | run non-live policies in parallel | decision context | `ShadowDecisionRecord` |
| `SessionProjection` | build latest session view | latest state across layers | `SessionProjection` |
| `DashboardPayloadBuilder` | build workstation payloads | session projection | `DashboardPayload` |

## 5. Canonical Data Contracts

All stage messages must be versioned and typed.

### 5.1. `EventEnvelope`

| Field | Type |
|---|---|
| `event_id` | `str` |
| `event_type` | `str` |
| `schema_version` | `str` |
| `session_id` | `str` |
| `frame_id` | `str` |
| `emitted_at` | `str` |
| `producer` | `str` |
| `payload` | `object` |

### 5.2. `SessionConfig`

| Field | Type |
|---|---|
| `session_id` | `str` |
| `name` | `str` |
| `market_hint` | `str` |
| `window_query` | `str` |
| `capture_interval_ms` | `int` |
| `layout_mode` | `str` |
| `profile_name` | `str` |
| `decision_policy_version` | `str` |
| `shadow_policy_versions` | `str[]` |

### 5.3. `WindowLockState`

| Field | Type |
|---|---|
| `status` | `idle|stable|waiting|guarded|lost` |
| `selected_hwnd` | `int` |
| `selected_title` | `str` |
| `score` | `float` |
| `reason_codes` | `str[]` |
| `warning` | `str` |

### 5.4. `FrameCaptured`

| Field | Type |
|---|---|
| `frame_id` | `str` |
| `capture_seq` | `int` |
| `captured_at` | `str` |
| `artifact_ref` | `ArtifactRef` |
| `fingerprint` | `str` |
| `capture_method` | `str` |
| `lock_state` | `WindowLockState` |

### 5.5. `PerceptionSnapshot`

| Field | Type |
|---|---|
| `frame_id` | `str` |
| `layout_detection` | `LayoutDetection` |
| `chart_roi` | `ChartRoi` |
| `labels` | `LabelReadout` |
| `candles` | `CandleObservation[]` |
| `boxes` | `StructureObservation[]` |
| `perception_confidence` | `PerceptionConfidence` |

### 5.6. `TemporalState`

| Field | Type |
|---|---|
| `frame_id` | `str` |
| `tracks` | `CandleTrack[]` |
| `continuity` | `ContinuityState` |
| `regime` | `RegimeState` |
| `reversal` | `ReversalState` |
| `temporal_confidence` | `float` |

### 5.7. `DecisionOutput`

| Field | Type |
|---|---|
| `decision_id` | `str` |
| `frame_id` | `str` |
| `stance` | `StanceState` |
| `setup` | `SetupState` |
| `trigger` | `TriggerState` |
| `veto` | `VetoState` |
| `confidence` | `ConfidenceBreakdown` |
| `final_action` | `BUY|SELL|HOLD` |
| `execution_phrase` | `str` |
| `why_not_execute` | `str[]` |
| `decision_summary` | `str` |

### 5.8. `ReplayRecord`

| Field | Type |
|---|---|
| `replay_id` | `str` |
| `session_id` | `str` |
| `decision_id` | `str` |
| `frame_id` | `str` |
| `captured_at` | `str` |
| `decision_output` | `DecisionOutput` |
| `model_versions` | `object` |
| `state_snapshot_ref` | `ArtifactRef` |

### 5.9. `OutcomeLabel`

| Field | Type |
|---|---|
| `replay_id` | `str` |
| `resolved_at` | `str` |
| `actual_outcome` | `BUY|SELL|HOLD|AMBIGUOUS` |
| `travel_proxy_pct` | `float` |
| `candles_elapsed` | `int` |
| `market_minutes_elapsed` | `float` |
| `pnl_proxy` | `float` |
| `resolution_reason` | `str` |

### 5.10. `DashboardPayload`

| Field | Type |
|---|---|
| `session_projection` | `SessionProjection` |
| `latest_decision` | `DecisionOutput` |
| `latest_perception` | `PerceptionSnapshot` |
| `latest_temporal_state` | `TemporalState` |
| `diagnostics` | `DiagnosticsPayload` |
| `artifact_manifest` | `ArtifactManifest` |
| `replay_summary` | `ReplaySummary` |

## 6. State Schemas

### 6.1. Session State

```json
{
  "session_id": "string",
  "status": "idle|starting|warming|waiting_for_window|running|degraded|stopped|error",
  "config": "SessionConfig",
  "lock_state": "WindowLockState",
  "runtime": {
    "worker_heartbeats": {},
    "queue_depths": {},
    "capture_age_ms": 0,
    "duplicate_frame_rate": 0.0,
    "runtime_stale": false
  },
  "latest_frame_id": "string",
  "latest_perception_frame_id": "string",
  "latest_temporal_frame_id": "string",
  "latest_decision_id": "string",
  "artifact_manifest": "ArtifactManifest"
}
```

### 6.2. Regime State

```json
{
  "direction": "bullish|bearish|neutral",
  "state": "building|locked|weakening|broken",
  "strength": 0.0,
  "opposing_strength": 0.0,
  "score_gap": 0.0,
  "up_step_ratio": 0.0,
  "down_step_ratio": 0.0,
  "travel": 0.0,
  "recent_travel": 0.0,
  "reversal_ready": false
}
```

### 6.3. Trigger State

```json
{
  "trigger": "waiting|armed|ready|invalidated",
  "action_side": "BUY|SELL|HOLD",
  "gate_ready": false,
  "timing_ready": false,
  "permission_ready": false,
  "cooldown_active": false,
  "confidence": 0.0,
  "invalidated_by": []
}
```

### 6.4. Decision Log

```json
{
  "decision_id": "string",
  "session_id": "string",
  "frame_id": "string",
  "model_versions": {
    "perception": "string",
    "temporal": "string",
    "decision": "string",
    "shadow": []
  },
  "stance": "StanceState",
  "setup": "SetupState",
  "trigger": "TriggerState",
  "veto": "VetoState",
  "confidence": "ConfidenceBreakdown",
  "final_action": "BUY|SELL|HOLD",
  "execution_phrase": "string",
  "why_not_execute": [],
  "latency_ms": {
    "capture": 0,
    "perception": 0,
    "temporal": 0,
    "decision": 0,
    "publish": 0
  }
}
```

## 7. Decision Hierarchy

V2 makes the slow, medium, and fast layers explicit.

### 7.1. Market Stance

Allowed values:

- `bullish`
- `bearish`
- `neutral`

Characteristics:

- slowest to flip
- highest directional authority
- can persist through pullbacks and noisy trigger changes

### 7.2. Setup State

Allowed values:

- `continuation`
- `pullback`
- `reversal_attempt`
- `consolidation`
- `transition`

Characteristics:

- medium speed
- describes structural context
- may change while stance remains locked

### 7.3. Trigger State

Allowed values:

- `waiting`
- `armed`
- `ready`
- `invalidated`

Characteristics:

- fastest-moving layer
- controls execution privilege
- must never flip stance by itself

### 7.4. Final Action Rule

The final action is derived from:

1. stance
2. setup alignment
3. trigger
4. veto state
5. confidence calibration

Trigger alone may not force a counter-stance execution. Countertrend execution requires explicit reversal maturity and veto clearance.

## 8. Adaptive Layout and Hybrid Perception

The default v2 localization path is:

1. classify layout family
2. detect major UI regions
3. mask non-chart chrome
4. segment chart canvas
5. refine analysis ROI and display ROI
6. score localization confidence
7. fall back to fixed profile only when adaptive localization is weak

The perception path should be hybrid:

- learned region detection for chart UI
- OCR for market and timeframe labels
- learned or semi-learned candle and box detection
- heuristic and template fallback rescue path
- confidence fusion across all sources

## 9. Multi-Horizon Reasoning

Even when only one chart is visible, v2 must reason across three horizons:

- `micro`
- `intraday`
- `swing`

For each horizon the system should produce:

- stance
- setup
- trigger suitability
- confidence

Required cross-horizon behavior:

- micro triggers may not emit `SELL NOW` against a locked bullish swing stance without reversal readiness
- intraday pullbacks should not break swing stance by default
- swing neutral state can cap micro execution confidence

## 10. Observability and Security

### 10.1. Required Metrics

- capture FPS
- capture latency
- duplicate-frame rate
- queue depth by stage
- queue overflow count
- layout confidence trend
- chart ROI failure rate
- OCR success rate
- perception latency
- temporal latency
- decision latency
- publish latency
- veto rate
- countertrend block rate
- reversal-ready rate
- actionable rate
- runtime stale rate
- accuracy by market and timeframe
- drift by version

### 10.2. Required Runtime Controls

- worker heartbeats
- bounded queues
- circuit breakers
- session TTLs
- artifact retention policies
- safe shutdown and restart
- auth on mutating routes
- role-based control
- rate limiting
- versioned response schemas

## 11. Testing and Replay Harness

Required validation surfaces:

- golden screenshot packs
- layout regression tests
- OCR regression tests
- chart ROI artifact diff tests
- temporal continuity tests
- stance/setup/trigger snapshot tests
- veto regression tests
- dashboard payload contract tests
- latency budget tests
- replay accuracy tests
- shadow policy comparison tests

Required replay capabilities:

- step through historical sessions frame by frame
- inspect perception output
- inspect temporal-state evolution
- inspect decision evolution
- compare live and shadow policies
- regenerate dashboard payloads

## 12. Migration Strategy

Migration must be incremental and backward-compatible.

### 12.1. Compatibility Rule

Current dashboard and current session routes remain usable while v2 is phased in.

### 12.2. V1 to V2 Ownership Mapping

| Current Concern | V2 Owner |
|---|---|
| `WindowsWindowCaptureBackend` | Capture Layer |
| `PhoenixGuardWindowTrackingAdapter` | Perception Layer |
| candle ID assignment and local tracking summary | Temporal Layer |
| `_stabilize_tracker_signal` | Decision Layer |
| RL context recording and feedback resolution | Learning Layer |
| dashboard payload assembly | Presentation Layer |

### 12.3. Bridge Plan

1. wrap current dict payloads in typed schemas
2. split the existing hot path into stage functions
3. put bounded queues between stages
4. replace internal implementations stage by stage

## 13. Implementation Order

### Phase 0: Baseline Freeze and Contracts

Deliverables:

- freeze current behavior with snapshot tests
- define core schemas
- add structured decision logs
- stabilize frame ids, replay ids, and model version fields

Exit criteria:

- current behavior is reproducible
- schemas validate all stage outputs

### Phase 1: Modular Extraction Without Behavioral Change

Deliverables:

- move current capture logic into `capture/`
- move current chart-reading logic into `perception/`
- move current continuity and trend state into `temporal/`
- move current stabilization into `decision/`
- add a dedicated session projection layer

Exit criteria:

- no material behavioral regression
- each layer has isolated tests

### Phase 2: Async Pipeline

Deliverables:

- bounded queues
- worker supervisor
- heartbeats
- stage latency metrics
- non-blocking dashboard publication

Exit criteria:

- capture no longer blocks on downstream inference
- stage failure no longer freezes the whole session

### Phase 3: Decision Decomposition

Deliverables:

- explicit `StanceState`
- explicit `SetupState`
- explicit `TriggerState`
- explicit `VetoState`
- explicit why-not-executing diagnostics

Exit criteria:

- every final action is derivable from those state objects
- countertrend blocks are first-class vetoes

### Phase 4: Adaptive Perception

Deliverables:

- layout classifier
- chrome masker
- chart segmenter
- ROI confidence scoring
- OCR fusion
- fallback chain integrating legacy heuristics

Exit criteria:

- fixed normalized profiles are fallback, not primary path
- resize and theme robustness improve measurably

### Phase 5: Replay and Evaluation Platform

Deliverables:

- immutable replay store
- outcome resolver
- calibration reports
- shadow policy runner
- promotion gate

Exit criteria:

- live policy changes require evaluation evidence
- online learning cannot directly mutate live execution policy

### Phase 6: Operator Workstation and Production Hardening

Deliverables:

- stance/setup/trigger workstation panels
- trend-lock and why-not-executing panels
- auth and role separation
- retention policies
- safe restart and shutdown
- hardened runtime controls

Exit criteria:

- the tracker behaves like a persistent platform, not just a local tool

## 14. Success Criteria

V2 is successful when:

1. stance flips are rarer and more justified than in v1
2. chart localization survives resizing and moderate layout shifts
3. capture remains stable during inference latency spikes
4. every operator-visible decision is replayable
5. why-not-executing explanations are trusted
6. policy promotion is evidence-driven
7. the dashboard behaves like a decision workstation rather than a passive viewer
