# Final Language Fluency Hardening Report

CLEAR ANSWER

This pass completed checkpointing, backups, major language-hardening fixes, ReportLab PDF repair, CSS compatibility cleanup, focused tests, dependency checks, and V3 integrity verification. It did not achieve a fully clean Pyright or full pytest result.

CONFIDENCE LEVEL

0.80

KEY CAVEATS

Final Pyright remains at 204 errors, 0 warnings. Full pytest timed out after 15 minutes. Runtime trace failed because the local API at `127.0.0.1:8793` was not running. Web smoke failed on `/app` assertions.

FILES STUDIED

Core: `main.py`, `shooter.py`, `start_phoenixguard_24_7_tracker.py`, `phoenixguard/mobile_api/app.py`, `phoenixguard/mobile_api/live_state_v3.py`, `phoenixguard/execution/*`, `phoenixguard/decision/*`, `phoenixguard/runtime/*`, `phoenixguard/vision/*`.

Scripts/tests/frontend: `scripts/*`, `tools/*`, `tests/*`, `web/package.json`, `phoenixguard/mobile_api/static/window_tracker_dashboard.html`.

ERRORS FOUND

Initial full Pyright under the tightened config reported 600 errors. Uploaded diagnostics reported 3,489 Pylance/Pyright-style issues. The old `pyrightconfig.json` hid many diagnostics with global `none` settings.

FIXES APPLIED

Removed broad Pyright suppressions, fixed ReportLab typing, hardened script JSON contracts, replaced compatibility shim dynamic exports with explicit re-exports, added public test aliases, fixed large Pyright clusters in `mobile_api/app.py`, `shooter.py`, `tests/test_sequence_projection.py`, `tools/enter_now_floating_gui.py`, `tests/test_gate_stability.py`, `phoenixguard/training/ensemble_cv_models.py`, `phoenixguard/mobile_api/live_state_v3.py`, `tools/verify_v3_integrity.py`, `phoenixguard/business/store.py`, `phoenixguard/vision/broker_scene_graph_v3.py`, `phoenixguard/vision/chart_segmentation.py`, and `phoenixguard/execution/calibration_manifest.py`.

TESTS RUN

Passed:

- `.venv\Scripts\python.exe generate_phoenixguard_pdf.py`
- `.venv\Scripts\python.exe -m compileall -q main.py shooter.py start_phoenixguard_24_7_tracker.py start_phoenixguard_mobile_api.py phoenixguard scripts tools tests`
- `.venv\Scripts\python.exe tools\verify_v3_integrity.py`
- `.venv\Scripts\python.exe -m pip check`
- Focused pytest: tracker/bootstrap + overlay contract, runtime recovery, enhanced vision phase1, gate stability, manual multi-timeframe upload
- `npm --prefix web run typecheck`
- `npm --prefix web run build`

Failed or incomplete:

- `python -m pyright`: 204 errors remain
- `.venv\Scripts\python.exe -m compileall -q .`: fails in `.venv\Lib\site-packages\aenum\_py2.py`
- `.venv\Scripts\python.exe tools\runtime_trace_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --timeout 20`: connection refused
- `.venv\Scripts\python.exe -m pytest -q`: timed out after 15 minutes
- `npm --prefix web run test:smoke`: `/app` missing onboarding/status markers

REMAINING RISKS

The repo is materially cleaner but not fully language-fluent yet. Remaining work should continue from `reports/pyright_latest.json`, starting with `tests/test_training_split_resolution.py`, `tools/certify_overlay_visual_truth_v3.py`, `phoenixguard/business/billing.py`, `phoenixguard/decision/scenario_integration.py`, `phoenixguard/runtime/local_ensemble_runtime.py`, and `phoenixguard/execution/packet_v3.py`.
