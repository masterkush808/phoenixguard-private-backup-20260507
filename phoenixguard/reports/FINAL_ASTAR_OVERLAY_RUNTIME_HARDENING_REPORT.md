# PhoenixGuard V3 A* Overlay Runtime Hardening Report

Generated: 2026-06-25

## Clear Answer

PhoenixGuard V3 overlay and runtime hardening is implemented without creating V4, without changing shooter calibration, without changing broker coordinates, and without bypassing `PG_EXECUTION_PACKET_V3`.

The overlay path now enforces stricter backend-owned truth: canonical labels, strict anchor metadata, source/frame/transform identity, hard-anchor rejection for floating objects, supply/demand lifecycle rules, marker-only council mode, compact API contract completeness, frontend stale-frame filtering, and singleton stack ownership for one tracker, one API, and one shooter reporter.

## Files Studied

- `phoenixguard/vision/v3_overlay_contract.py`
- `phoenixguard/vision/box_refinement_v3.py`
- `phoenixguard/tracking/market_object_tracker_v3.py`
- `phoenixguard/mobile_api/live_state_v3.py`
- `phoenixguard/mobile_api/app.py`
- `phoenixguard/mobile_api/static/window_tracker_dashboard.html`
- `assets/js/overlay_placement.js`
- `assets/js/overlay_placement.esm.js`
- `assets/js/overlay_skeleton.js`
- `phoenixguard/vision/market_registry.py`
- `phoenixguard/vision/overlay_layer_manager_v3.py`
- `start_phoenixguard_24_7_tracker.py`
- `start_phoenixguard_mobile_api.py`
- `shooter.py`
- `tools/validate_overlay_contract_v3.py`
- `tools/certify_process_topology_v3.py`
- `tools/capture_overlay_mode_screenshots_v3.py`
- Overlay, live-state, runtime guard, frontend, and shooter tests under `tests/`.

## Files Changed

- Added `phoenixguard/runtime/singleton_guard_v3.py`.
- Added `tests/test_runtime_singleton_guard_v3.py`.
- Hardened overlay contract, anchor evidence, display states, style metadata, and council mode policy.
- Hardened precision resolver floating-box rejection and parent-only anchor rejection.
- Hardened market object tracker S/D, S/R, opposing-force, lifecycle, and candle-anchor derivation.
- Hardened live-state compact payload and FastAPI compact response so strict anchor fields survive transport.
- Hardened frontend overlay placement, stale context filtering, CSS/DOM diagnostics option, and label/style propagation.
- Hardened tracker/API/shooter singleton lock registration and Windows lock-write retry behavior.
- Hardened certification tools for topology, contract validation, and screenshot capture.

## Canonical Vocabulary And Alias Mapping

PASS. Canonical overlay vocabulary remains V3 and includes:

`CHART_BOUNDS`, `CURRENT_CANDLE`, `IMPULSE_BOX`, `PULLBACK_BOX`, `RETEST_BOX`, `CONTINUATION_BOX`, `SNIPER_ENTRY_BOX`, `TARGET_ZONE_BOX`, `INVALIDATION_BOX`, `SUPPLY_ZONE`, `DEMAND_ZONE`, `OPPOSING_FORCE`, `SUPPORT_TRENDLINE`, `RESISTANCE_TRENDLINE`, `INNER_TRENDLINE`, `ANGLE_VECTOR`, `PROGRESSION_PATH`, `PREDICTION_PATH`, `REPLAY_ENTRY`, `REPLAY_EXIT`, council/study markers, broker/debug diagnostics.

Alias normalization is verified through `tests/test_v3_overlay_contract.py` and live contract validation.

## Anchor And Precision Validation

PASS.

- Live overlay contract: 54 overlays, 0 invalid.
- Precision audit: 68 input overlays, 29 rendered, 39 rejected.
- Floating unanchored rejected: 2.
- Unanchored live boxes: 0.
- Stale frame overlays: 0.
- Missing transform overlays: 0.
- Label collisions: 0.
- Outside plot-area rendered: 0.

Important behavior: unanchored or outside candidates are rejected instead of being drawn as live truth.

## Supply/Demand And Opposing Force

PASS.

- `SUPPLY_ZONE` now requires resistance/supply-side evidence and no longer defaults ambiguous roles to demand.
- `DEMAND_ZONE` now requires support/demand-side evidence.
- Broken or consumed zones are lifecycle-marked and visually reduced instead of staying active.
- `OPPOSING_FORCE` is only published from meaningful active zones and carries side, force, source zone, distance, and anchor details.

## Trendline And Structure

PASS.

- Trendlines require touch/line evidence.
- Line-level overlays without touch evidence are rejected.
- Historical progression is path-based when path points exist and no longer relies on source-less broad rectangles.
- Impulse/pullback/retest/target objects now carry anchor evidence through compact API transport.

## Pair Switch And Stale Overlay Protection

PASS by contract and frontend/backend filtering.

- Frontend skeleton rejects mismatched frame, transform, symbol, and timeframe where comparable values exist.
- Backend live-state requires frame-aligned overlays before rendering.
- Singleton guard prevents old tracker/API/shooter writers from running beside the current stack.

## Single Process Guard

PASS.

`PhoenixRuntimeSingletonGuardV3` owns `.codex_runtime/phoenixguard_stack.lock.json` and tracks:

- session id
- API PID
- tracker PID
- shooter PID
- base URL
- data directory
- heartbeat
- owner token

Final live topology:

- tracker: one process
- API: one process
- shooter reporter: one process
- process topology gate: PASS

## Frontend And Screenshots

PASS.

Screenshot evidence directory:

`.codex_runtime/visual_evidence/overlay_modes`

Captured nonblank screenshots:

- `clean_live_pocket-live-8788.png`
- `global_pocket-live-8788.png`
- `local_pocket-live-8788.png`
- `supply_demand_pocket-live-8788.png`
- `trendlines_pocket-live-8788.png`
- `trigger_pocket-live-8788.png`
- `target_pocket-live-8788.png`
- `path_pocket-live-8788.png`
- `council_pocket-live-8788.png`
- `two_candle_study_pocket-live-8788.png`
- `active_context_pocket-live-8788.png`
- `full_history_read_pocket-live-8788.png`
- `replay_pocket-live-8788.png`
- `broker_pocket-live-8788.png`
- `diagnostics_pocket-live-8788.png`

Screenshot certification report: `reports/certification/gate_overlay_mode_screenshots_v3.json`

## Live Certification Results

- `python tools\certify_process_topology_v3.py --session pocket-live-8788 --base-url http://127.0.0.1:8793`: PASS
- `python tools\validate_overlay_contract_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --mode DIAGNOSTICS`: PASS, 54 overlays, 0 invalid
- `python tools\audit_overlay_precision_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --mode CLEAN_LIVE`: PASS metrics, 0 unanchored live boxes
- `python tools\certify_overlay_modes_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --all-modes`: PASS
- `python tools\certify_overlay_visual_truth_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788`: PASS
- `python tools\runtime_trace_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --timeout 20`: PASS alignment
- `python tools\verify_v3_integrity.py`: PASS

## Static And Test Results

- `python -m compileall -q .`: PASS
- `node --check assets/js/overlay_placement.js`: PASS
- `node --check assets/js/overlay_placement.esm.js`: PASS
- `node --check assets/js/overlay_skeleton.js`: PASS
- `python -m pyright`: 0 errors, 0 warnings, 0 informations
- Focused overlay/runtime/frontend/shooter tests: 121 passed
- Full pytest: 1250 passed, 7 skipped

## Dependencies

`pip check` reports broad global Python environment conflicts involving TensorFlow, LangChain, mitmproxy, Streamlit, PyCaret, protobuf, Pillow, packaging, transformers, scikit-learn, and related non-isolated packages. PhoenixGuard compile, Pyright, integrity, runtime trace, live overlay gates, frontend screenshot checks, and full pytest pass in the current environment. The dependency conflicts should be handled by isolating PhoenixGuard in its own virtual environment rather than mutating the global workstation package set during overlay hardening.

## Remaining Risks

- Live screenshot capture for every mode is slow because the dashboard and tracker continue doing heavy live capture work. The screenshot artifacts are present and nonblank; the tool was improved to reuse a browser, and Diagnostics now has a frontend option.
- `pip check` is not clean because the workstation Python environment is shared and contains conflicting ML/web packages. Project verification passes, but a dedicated PhoenixGuard environment remains the correct permanent dependency solution.

