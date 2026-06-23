# Agent 2 Pyright Pylance Language Fixes

CLEAR ANSWER

The hardening pass removed the old broad Pyright suppression configuration and fixed large clusters of real diagnostics in mobile API, shooter, tests, scripts, training, ReportLab PDF generation, and runtime helpers. Final Pyright result is not clean yet: 204 errors remain.

CONFIDENCE LEVEL

0.78

KEY CAVEATS

This is a partial but material cleanup. No global ignores were added. Remaining diagnostics are documented in `reports/pyright_latest.json`.

FILES STUDIED

`pyrightconfig.json`, `phoenixguard/mobile_api/app.py`, `shooter.py`, `tests/test_sequence_projection.py`, `tests/test_gate_stability.py`, `tests/test_manual_multi_timeframe_upload.py`, compatibility shims, `phoenixguard/training/ensemble_cv_models.py`, `phoenixguard/mobile_api/live_state_v3.py`.

ERRORS FOUND

Initial full Pyright under tightened `basic` config reported 600 errors. Largest clusters were `mobile_api/app.py`, `tests/test_sequence_projection.py`, `shooter.py`, optional GUI imports, fuzzy tests, and dynamic JSON contracts.

FIXES APPLIED

Added typed wrappers and mapping helpers, exposed public test seams, replaced dynamic shim exports with explicit re-exports, narrowed fuzz-test casts, fixed optional dependency typing, and converted many unsafe `object` numeric conversions to typed helper paths.

TESTS RUN

Focused Pyright checks passed for all touched hotspots. Final full Pyright: 204 errors, 0 warnings.

REMAINING RISKS

Top remaining Pyright clusters include `tests/test_training_split_resolution.py`, `tools/certify_overlay_visual_truth_v3.py`, `phoenixguard/business/billing.py`, `phoenixguard/decision/scenario_integration.py`, `phoenixguard/runtime/local_ensemble_runtime.py`, and `phoenixguard/execution/packet_v3.py`.
