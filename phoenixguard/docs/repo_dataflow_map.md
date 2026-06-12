# PhoenixGuard V3 Agent 1 Repo Dataflow Map

Scope: Agent 1 cartography and schema integrity. This map records current repo behavior before later agents remove legacy execution authority.

## High-Level Flow

1. Capture and upload surfaces
   - Manual/mobile upload: `phoenixguard/mobile_api/service.py` stages quartet images under `data/mobile_api/jobs/<job_id>/uploads`.
   - Observer upload: `phoenixguard/mobile_api/observer.py` stages observer bundles under `data/mobile_api/observer/sessions/<session_id>/bundles`.
   - Continuous tracker: `phoenixguard/mobile_api/window_tracker.py` captures the broker window in `_capture_and_analyze_claimed` and writes session state, artifacts, previews, and event logs.

2. Perception and decision production
   - `main.py::run_inference` is the main analysis pipeline.
   - It combines CV detections, memory bank recall, skill gates, ensemble decision output, sequence/regression/RL context, and decision kernel output.
   - Current output is legacy signal-shaped data: `action`, `execution_action`, `execution_permission`, `decision_state`, `decision_kernel`, `memory_similarity`, `skill_contributions`, and chart context.

3. Observer and tracker publication
   - `SignalObserverService._build_signal_payload` converts `run_inference` output into `latest_signal`.
   - `SignalObserverService.latest_signal` serves the current observer signal.
   - `ContinuousWindowTrackerService._capture_and_analyze_claimed` merges tracker state, broker state, timing fields, scenario analysis, Model Council V3 state, and observer/latest signal data into `session.json`.
   - `ContinuousWindowTrackerService._normalize_session_payload` computes fresh `state_version`, updates signal age/freshness, and exposes `latest_signal`.

4. Existing execution consumption
   - `shooter.py` contains legacy parsing helpers but live signal mode now probes Model Council V3 packet endpoints first and executes only a valid `PG_EXECUTION_PACKET_V3`.
   - It probes `/v1/mobile/model-council/sessions/{session_id}/execution/latest` and `/v1/mobile/model-council/execution/latest`.
   - The API app exposes Model Council health, intelligence, and latest execution packet endpoints.
   - The tracker's internal broker backend no longer performs live clicks; in live mode it either reports `blocked_by_runtime` for missing/invalid V3 packets or `external_shooter_required` for valid packets.

5. V3 schema support added by Agent 1
   - `phoenixguard/execution/packet_v3.py` now defines `PG_EXECUTION_PACKET_V3` validation and publisher helpers.
   - It rejects raw `action` / `execution_action` payloads, `SNIPER_READY`, CALL/PUT aliases, stale packets, missing `model_council.final_side`, and side mismatch.
   - Runtime integrity failures are classified as `RUNTIME_INTEGRITY`, not `MARKET_BLOCKER`.

## Key Producer Functions

- `main.py::run_inference`: primary analysis producer.
- `phoenixguard/mobile_api/observer.py::SignalObserverService._build_signal_payload`: observer signal payload producer.
- `phoenixguard/mobile_api/window_tracker.py::ContinuousWindowTrackerService._capture_and_analyze_claimed`: live tracker session, latest signal, Model Council result, and optional V3 packet producer.
- `phoenixguard/runtime/model_council_daemon.py`: model ensemble inference daemon, currently `/status` and `/predict`, not a V3 execution packet publisher.
- `phoenixguard/decision/model_council_v3.py`: V3 council evaluator and packet publisher helper now consuming Agent 1 packet support.
- `phoenixguard/execution/packet_v3.py::build_execution_packet_v3`: reusable V3 packet publisher helper added for later Model Council integration.

## Key Consumer Functions

- `shooter.py::fetch_latest_model_council_packet`: probes V3 packet endpoints.
- `shooter.py::parse_trade_signal`: legacy raw signal parser still present for compatibility tests, but not a live signal-mode authority.
- `shooter.py` V3 gate helpers around Gate 1, Gate 2, Gate 3, pre-click confirmation, and amount-preserve behavior.
- `phoenixguard/mobile_api/static/window_tracker_dashboard.html`: dashboard consumer of tracker session `latest_signal` fields.
- `scripts/replay_signals.py`: dry-run consumer of legacy shooter `parse_trade_signal`.
- Tests under `tests/test_shooter_runtime.py`, `tests/test_shooter_parse.py`, and `tests/test_execution_packet_schema_v3.py`.

## Agent 1 Integration Boundary

Agent 1 did not remove legacy `action` or `execution_action` producers. That removal belongs to Agents 2 and 4. Agent 1 created the V3 schema authority and reports the legacy paths that must be migrated.
