# PhoenixGuard V3 Overlay Anchor Correction Report

Generated: 2026-06-27

## 1. Problem summary

The overlay system was detecting market objects, but live rendering did not carry enough explicit candle, wick, sequence, and transform evidence for every object. This allowed some overlays to appear visually plausible while still being weakly anchored or hard to audit.

This pass focused only on overlay anchoring precision. Shooter logic, Model Council strategy, execution packet authority, broker calibration, and live package logic were not changed.

## 2. Files studied

- `Backend/src/phoenixguard/vision/v3_overlay_contract.py`
- `Backend/src/phoenixguard/tracking/market_object_tracker_v3.py`
- `Backend/src/phoenixguard/vision/v3_chart_transform.py`
- `Backend/src/phoenixguard/vision/candle_snap.py`
- `Backend/src/phoenixguard/vision/overlay_geometry.py`
- `Backend/src/phoenixguard/vision/overlay_layer_manager_v3.py`
- `Backend/src/phoenixguard/vision/renderer.py`
- `Backend/src/phoenixguard/vision/chart_segmentation.py`
- `Backend/src/phoenixguard/mobile_api/window_tracker.py`
- `Frontend/dashboard/static/window_tracker_dashboard.html`

## 3. Files changed

- `Backend/src/phoenixguard/vision/v3_overlay_contract.py`
- `Backend/src/phoenixguard/tracking/market_object_tracker_v3.py`
- `Backend/src/phoenixguard/mobile_api/window_tracker.py`
- `Backend/src/phoenixguard/mobile_api/realtime_sync_v3.py`
- `Backend/tools/run_final_10h_production_certification.py`
- `Backend/tools/capture_overlay_mode_screenshots_v3.py`
- `Backend/tools/audit_overlay_anchor_quality_v3.py`
- `Backend/tools/certify_overlay_anchor_precision_v3.py`
- `Backend/tools/capture_overlay_anchor_screenshots_v3.py`
- `Backend/tests/test_overlay_anchor_quality_v3.py`

## 4. Anchor pipeline confirmed

Confirmed active path:

`broker frame -> chart bounds -> plot area -> candle extraction -> candle objects -> swing/wick anchors -> market object registry -> V3 overlay object -> chart transform -> backend live-state -> frontend renderer`

The correction strengthens the backend truth contract. The frontend still renders backend-resolved overlays only.

## 5. Candle anchor map result

Every normalized overlay now carries:

- `anchor_wick_points`
- `anchor_sequence_id`
- `anchor_confidence`
- `anchor_quality`

`anchor_quality` reports:

- candle anchor present
- wick anchor present
- sequence anchor present
- chart transform valid
- bounds valid
- symbol/timeframe mismatch state
- floating risk

## 6. Supply and demand anchor result

Supply/demand zones now export explicit `anchor_wick_points` from the same wick-touch cluster used for the zone band. This preserves the difference between a true wick/rejection zone and a generic rectangle.

## 7. Trendline anchor result

Trendlines now export:

- wick touch points
- anchor candles
- slope
- intercept
- touch count
- obstruction validation metadata

This makes support, resistance, and inner trendlines auditable as wick-derived lines.

## 8. Structure overlay anchor result

The V3 overlay contract now scores impulse, pullback, continuation, sniper, target, invalidation, current candle, supply/demand, opposing-force, and trendline overlays using a common anchor-quality contract.

Live hard-anchor overlays below score `0.65` are rejected from live truth.

## 9. Target, invalidation, and opposing-force result

These overlays now receive anchor-quality scoring through the same V3 contract. They must carry candle/wick/sequence/transform evidence to remain live-renderable.

## 10. Pair-switch anchor reset result

The live contract rejects wrong-frame and wrong-pair overlays during audit. Current live certification found:

- wrong-frame overlays: `0`
- wrong-pair overlays: `0`

## 11. Floating boxes before and after

Live evidence after correction:

| Mode | Floating | Unanchored | Wrong frame | Wrong pair | Current candle | Avg quality | Min quality |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CLEAN_LIVE | 0 | 0 | 0 | 0 | 1 | 0.9875 | 0.85 |
| SUPPLY_DEMAND | 0 | 0 | 0 | 0 | 0 | 1.0 | 1.0 |
| TRENDLINES | 0 | 0 | 0 | 0 | 0 | 1.0 | 1.0 |
| FULL_HISTORY_READ | 0 | 0 | 0 | 0 | 0 | 0.9905 | 0.81 |
| DIAGNOSTICS | 0 | 0 | 0 | 0 | 36 | 0.9938 | 0.81 |

The soft `0.81` rows are historical/diagnostic context, not rejected live truth.

## 12. Screenshots

Evidence folder:

`.codex_runtime/visual_evidence/anchor_fix`

Required captures present:

- `before_problem_case.png`
- `after_clean_live.png`
- `after_supply_demand.png`
- `after_trendlines.png`
- `after_full_history_read.png`
- `after_diagnostics_rejected.png`

Screenshot gate:

- `reports/certification/gate_overlay_anchor_screenshots_v3.json`
- Verdict: `PASS`

## 13. Validation

Commands run:

- `python -m compileall -q Backend/src/phoenixguard/vision Backend/src/phoenixguard/tracking Backend/src/phoenixguard/mobile_api Backend/tools Backend/tests/test_overlay_anchor_quality_v3.py`
- `python -m pyright` on all changed Python files
- `python -m pytest -q Backend/tests/test_overlay_anchor_quality_v3.py Backend/tests/test_v3_overlay_contract.py Backend/tests/test_market_object_tracker_v3.py Backend/tests/test_overlay_precision_v3.py Backend/tests/test_frontend_heartbeat_api_v3.py Backend/tests/test_realtime_sync_v3.py Backend/tests/test_final_10h_certification_monitor.py`
- `python Backend/tools/audit_overlay_anchor_quality_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --mode CLEAN_LIVE --timeout 45`
- `python Backend/tools/certify_overlay_anchor_precision_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --timeout 45`
- `python Backend/tools/capture_overlay_anchor_screenshots_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --out .codex_runtime/visual_evidence/anchor_fix --timeout 90`

Results:

- compileall: PASS
- changed-file pyright: PASS, `0 errors, 0 warnings`
- focused tests: PASS, `84 passed`
- clean-live anchor audit: PASS
- cross-mode anchor precision: PASS
- screenshot evidence capture: PASS

## 14. Runtime status

The stack is running on:

`http://127.0.0.1:8793/dashboard/live/pocket-live-8788`

Runtime status after restart:

- focus locked: true
- capture interval: 15 seconds
- tracker state: fresh/running
- health endpoint: HTTP 200

Windows venv note:

The launcher is invoked through `.venv/Scripts/python.exe`. Windows may show a child process command line using the base Python executable because the venv executable redirects to the base interpreter, but the launched process inherits the repo `.venv` environment and project paths.

## 15. Remaining caveats

- Full-history and diagnostics can show softer historical context overlays. They are still above the live rejection threshold and are not floating/unanchored.
- No shooter, Model Council, execution packet, broker calibration, or package-promotion logic was changed in this pass.

## 16. Resume point

The next broader production-hardening task can resume from the paused point after this anchor correction. The overlay anchor correction itself is complete and has screenshot plus audit evidence.
