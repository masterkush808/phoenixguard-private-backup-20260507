# PhoenixGuard V3 Agent 1 Repo Dataflow Map

Scope: current cartography and schema integrity. This map records the active V3 behavior after local
shooter execution was retired.

## High-Level Flow

1. Capture and upload surfaces
   - Manual/mobile upload: `phoenixguard/mobile_api/service.py` stages quartet images under
     `data/mobile_api/jobs/<job_id>/uploads`.
   - Observer upload: `phoenixguard/mobile_api/observer.py` stages observer bundles under
     `data/mobile_api/observer/sessions/<session_id>/bundles`.
   - Continuous tracker: `Backend/src/phoenixguard/mobile_api/window_tracker.py` captures the broker window in
     `_capture_and_analyze_claimed` and writes session state, artifacts, previews, and event logs.

2. Perception and decision production
   - `main.py::run_inference` is the main analysis pipeline.
   - It combines CV detections, memory bank recall, skill gates, ensemble decision output,
     sequence/regression/RL context, and decision kernel output.
   - Current output still includes legacy signal-shaped diagnostic fields such as `action`,
     `execution_action`, `execution_permission`, `decision_state`, `decision_kernel`,
     `memory_similarity`, `skill_contributions`, and chart context. These fields are not execution
     authority.

3. Observer and tracker publication
   - `SignalObserverService._build_signal_payload` converts `run_inference` output into
     `latest_signal`.
   - `SignalObserverService.latest_signal` serves the current observer signal.
   - `ContinuousWindowTrackerService._capture_and_analyze_claimed` merges tracker state, broker
     state, timing fields, scenario analysis, Model Council V3 state, and observer/latest signal
     data into `session.json`.
   - `ContinuousWindowTrackerService._normalize_session_payload` computes fresh `state_version`,
     updates signal age/freshness, and exposes `latest_signal`.

4. Package handoff consumption
   - `shooter.py` is the local package reporter, not a click executor.
   - It probes `/v1/mobile/model-council/sessions/{session_id}/execution/latest`.
   - It writes `runtime/live/shooter_handshake.json` only when a validated
     `PG_EXECUTION_PACKET_V3` includes an explicit accepted, execution-ready
     `PG_ALLOWANCE_PACKAGE_V1`.
   - `Backend/tools/phoenixguard_mt4_file_bridge.py` compacts the package into an MT4 command and rejects
     inferred, missing, non-accepted, or non-ready allowance packages.
   - The API app exposes Model Council health, intelligence, latest execution packet endpoints, and
     package-reporter handshake status.

5. V3 schema support
   - `Backend/src/phoenixguard/execution/packet_v3.py` now defines `PG_EXECUTION_PACKET_V3` validation and
     publisher helpers.
   - It rejects raw `action` / `execution_action` payloads, `SNIPER_READY`, CALL/PUT aliases, stale
     packets, missing `model_council.final_side`, and side mismatch.
   - Runtime integrity failures are classified as `RUNTIME_INTEGRITY`, not `MARKET_BLOCKER`.

## Key Producer Functions

- `main.py::run_inference`: primary analysis producer.
- `phoenixguard/mobile_api/observer.py::SignalObserverService._build_signal_payload`: observer
  signal payload producer.
- `Backend/src/phoenixguard/mobile_api/window_tracker.py::ContinuousWindowTrackerService._capture_and_analyze_claimed`:
  live tracker session, latest signal, Model Council result, and optional V3 packet producer.
- `Backend/src/phoenixguard/runtime/model_council_daemon.py`: model ensemble inference daemon, currently
  `/status` and `/predict`, not a V3 execution packet publisher.
- `Backend/src/phoenixguard/decision/model_council_v3.py`: V3 council evaluator and packet publisher helper now
  consuming Agent 1 packet support.
- `Backend/src/phoenixguard/execution/packet_v3.py::build_execution_packet_v3`: reusable V3 packet publisher
  helper added for later Model Council integration.

## Key Consumer Functions

- `shooter.py::review_allowed_package`: validates a V3 packet and its allowance package.
- `shooter.py::publish_allowed_package_report`: writes the package-reporter handshake for accepted
  packages.
- `Backend/tools/phoenixguard_mt4_file_bridge.py::_validate_command`: revalidates allowance package source,
  type, authority, accepted state, and execution readiness before MT4 handoff.
- `Frontend/dashboard/static/window_tracker_dashboard.html`: dashboard consumer of tracker
  session `latest_signal` fields.
- `Backend/scripts_runtime/replay_signals.py`: dry-run consumer of legacy signal-shaped diagnostics.
- Tests under `Backend/tests/test_execution_packet_schema_v3.py`, `Backend/tests/test_mt4_file_bridge.py`, and
  `Backend/tests/test_entry_allowance_burn.py`.

## Agent 1 Integration Boundary

Legacy `action` or `execution_action` producers remain for dashboards, replay, and compatibility.
They are diagnostics only; executable authority is the V3 packet plus explicit allowance package.
