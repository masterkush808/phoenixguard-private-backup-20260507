# PhoenixGuard Frontend V4 Visual Regression Report

Date: 2026-05-20

Scope: dashboard visual QA from static source review and existing pytest assertions. No browser automation was run, no live server was started, and no broker-facing execution path was triggered.

## Current Visual Baseline

Primary surface:

- `phoenixguard/mobile_api/static/window_tracker_dashboard.html`

Dashboard assets served through the allowlisted asset route:

- `landing-transition-lifestyle-suite.png` - 2,455,204 bytes
- `landing-transition-lifestyle-travel.png` - 2,556,908 bytes
- `landing-transition-market-vision-alt.png` - 2,998,883 bytes
- `landing-transition-market-vision.png` - 2,998,883 bytes

The dashboard uses full-screen background scene cycling, a dark glass console, gold/jade/red signal tones, dense metric grids, and a zoomable broker/chart surface. Important visual states covered by source review:

- Overlay vs raw surface mode.
- Fit plane, actual size, zoom in/out, center pan.
- Layer toggles for chart bounds, candles, swings, supply/demand, trigger zones, council decision, historical replay, broker controls, and diagnostics.
- Prediction and future projection image panels.
- Live, error, warning, primary, BUY, SELL, and HOLD color classes.

## Automated Visual Assertions Present

Existing pytest checks assert visual/layout-critical source behavior:

```powershell
python -m pytest tests/test_window_tracker_service.py::test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout tests/test_window_tracker_service.py::test_tracker_dashboard_fits_selected_surface_without_width_only_crop -v --tb=short -ra
```

Result in this QA pass: passed as part of the 15-test safe subset.

These tests cover:

- Prediction images use uncropped full-width layout.
- Fit-plane math considers both stage width and height.
- The dashboard can prefer full selected plane/full broker window fitting.
- Overlay fallback ordering remains encoded in the dashboard source.

## Manual Visual Regression Checklist

Use only a non-live test service or fixture-backed API for this checklist. Do not run live broker launchers during QA.

- Open dashboard route against a test session.
- Verify first viewport shows the brand lockup, session chip, live state, signal deck, kernel deck, and primary controls without overlap.
- Toggle raw/overlay mode and verify the chart surface does not crop unexpectedly.
- Exercise zoom slider, zoom in/out, fit, actual, and center controls.
- Toggle every overlay layer and confirm no layer button wraps into overlapping text.
- Confirm BUY/SELL/HOLD states remain visually distinct.
- Confirm emergency stop remains visible and legible at desktop and mobile widths.
- Confirm prediction/reference/future images render contained, not cropped or stretched.
- Confirm fallback placeholders are readable when artifacts are missing.

## Recommended Future Visual Automation

No Playwright/Selenium/Lighthouse dependency was found in the repo. When adding a visual harness, keep it isolated from live execution:

```powershell
python -m pytest tests/test_window_tracker_service.py::test_tracker_http_surface_serves_session_artifacts_and_dashboard -v --tb=short -ra
```

Then add a fixture server that uses fake tracker services only, and capture screenshots at:

- 1440 x 1000 desktop
- 1024 x 768 tablet
- 390 x 844 mobile
- 320 x 740 narrow mobile

Hard rule: the visual harness must not call `/start`, `/capture-once`, `/demo-random-trade`, or live execution control patches.

## Visual Risks

- The background image payload is about 11 MB across four assets. This is acceptable for a local dashboard, but expensive for remote sharing or repeated cold loads.
- The dashboard is intentionally dense; long session IDs, market names, or diagnostic text should stay under regression watch.
- Animated scene cycling and live pulse should remain disabled under `prefers-reduced-motion: reduce`; the CSS includes a reduced-motion media query.
