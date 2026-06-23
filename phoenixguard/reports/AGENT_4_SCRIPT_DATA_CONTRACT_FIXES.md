# Agent 4 Script Data Contract Fixes

CLEAR ANSWER

Script contracts were hardened around JSON payloads, ONNX export arguments, route function use, and replay/runtime contradiction payloads.

CONFIDENCE LEVEL

0.86

KEY CAVEATS

Not every script in `tools/` is Pyright clean. The requested target scripts compile and passed targeted Pyright checks earlier in the pass.

FILES STUDIED

`scripts/adaptive_expiry_harness.py`, `scripts/build_sequence_teacher_manifest.py`, `scripts/business_mock_api.py`, `scripts/export_inference_bundles.py`, `scripts/export_runtime_contradiction_queue.py`, `scripts/replay_signals.py`.

ERRORS FOUND

Untyped JSON mappings, loose replay row parsing, ONNX export called with a raw tensor argument, FastAPI route handlers flagged as unused, and broad `object`/`Unknown` propagation.

FIXES APPLIED

Added mapping helpers, narrowed JSON payloads, typed harness status/sample containers, changed ONNX export args to `(example,)`, added explicit route handler retention through `app.state.mock_business_route_handlers`, and added script `main() -> int` where useful.

TESTS RUN

`.venv\Scripts\python.exe -m compileall -q` over target scripts passed. Targeted Pyright over target scripts passed earlier in the run.

REMAINING RISKS

Some `tools/` scripts outside the requested list still have Pyright errors.
