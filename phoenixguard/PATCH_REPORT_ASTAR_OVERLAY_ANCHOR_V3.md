# A* Overlay Anchor V3 Patch Report

## Scope
Patched the uploaded overlay precision GUI package to harden live overlay anchoring, display-state rendering, supply/demand tightness, replay label dominance, and frontend geometry visibility.

## Files changed
- Backend/src/phoenixguard/tracking/market_object_tracker_v3.py
- Backend/src/phoenixguard/vision/overlay_geometry.py
- Backend/src/phoenixguard/vision/box_refinement_v3.py
- Frontend/dashboard/static/window_tracker_dashboard.html

## Main implementation changes
1. Added candle/wick/sequence anchor refinement before V3 overlay objects are normalized.
2. Added anchor quality scoring and anchor evidence status to every generated overlay.
3. Tightened SUPPLY_ZONE, DEMAND_ZONE, OPPOSING_FORCE, SNIPER_ENTRY_BOX, RETEST_BOX, TARGET_ZONE_BOX, INVALIDATION_BOX, IMPULSE_BOX, PULLBACK_BOX, and CONTINUATION_BOX around real candle rows instead of broad loose boxes.
4. Prevented critical live overlays without usable candle anchor evidence from being promoted into live overlay truth.
5. Changed duplicate merging for supply/demand/trigger/target/invalidation overlays to keep the best anchored/tightest object instead of unioning boxes into oversized rectangles.
6. Added overlay_quality metrics: floating_boxes, unanchored_live_overlays, loose_zone_count, label_collision_risk_count, anchor_quality_avg, anchor_quality_min, render_grade.
7. Changed clean-live budgeting so valid overlay geometry is ghosted/compacted instead of silently hidden.
8. Reduced historical/replay label dominance by keeping geometry accessible while moving labels to inspector/ghosted states outside REPLAY/FULL_HISTORY_READ.
9. Changed frontend budgets to preserve all valid geometry while controlling only labels.
10. Updated frontend label/render logic to use backend display_label/short_label/display_state and hide labels on narrow or ghosted overlays.

## Verification run in sandbox
```text
python -m compileall -q Backend/src/phoenixguard/vision Backend/src/phoenixguard/tracking Backend/src/phoenixguard/mobile_api Backend/tools Backend/tests
PASS
```

## Required in your full repo after replacement
```powershell
python -m compileall -q Backend\src\phoenixguard\vision Backend\src\phoenixguard\tracking Backend\src\phoenixguard\mobile_api Backend\tools Backend\tests
python Backend\tools\audit_overlay_anchor_quality_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --mode CLEAN_LIVE
python Backend\tools\certify_overlay_anchor_precision_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788
python Backend\tools\capture_overlay_anchor_screenshots_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --out .codex_runtime\visual_evidence\anchor_v3
python -m pyright
```

## Important operational note
After replacing the files, clear old runtime overlay caches and restart the tracker. Otherwise the dashboard may still render old cached overlay geometry from before this patch.
