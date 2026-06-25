# PhoenixGuard Frontend V4 Performance Budget Report

Date: 2026-05-20

Scope: performance budget recommendations for the local dashboard and safe test commands. This pass did not start live capture, live broker execution, shooter signal mode, or a persistent dashboard server.

## Current Payload Observations

Static dashboard HTML:

- `Frontend/dashboard/static/window_tracker_dashboard.html` - 187,212 bytes

Dashboard scene assets:

- four PNG background assets totaling about 11.0 MB

Runtime behavior visible in source:

- Dashboard refreshes session state every 1000 ms.
- The surface supports image loading, overlay hotspots, zoom/pan, fit-plane math, and multiple rendered telemetry modules.
- CSS includes reduced-motion handling.
- The dashboard is local-first, so the large scene asset budget is less risky than it would be on a public web app.

## Proposed Budgets

Local dashboard budget:

- Initial HTML: keep under 250 KB.
- Dashboard background/brand assets: keep under 12 MB total until a real web deployment needs stricter delivery.
- API polling: keep default dashboard polling at 1000 ms or slower unless profiling proves subsecond UI polling is necessary.
- Main-thread render update: target under 50 ms per session payload on a mid-range laptop.
- Image fit/overlay re-render after resize or image load: target under 100 ms.
- No layout shift for topbar, signal deck, control ribbon, and emergency-stop controls after first session render.

Remote/share budget if this dashboard is ever exposed outside localhost:

- Convert large PNG scene assets to compressed WebP/AVIF variants.
- Keep first-view critical assets under 2 MB.
- Lazy-load non-critical scene images.
- Cache immutable assets with long-lived cache headers.

## Safe Performance Commands

Safe regression subset executed in this pass:

```powershell
python -m pytest Backend/tests/test_mobile_api_service.py Backend/tests/test_ui_copy_hardening.py Backend/tests/test_window_tracker_service.py::test_tracker_http_surface_serves_session_artifacts_and_dashboard Backend/tests/test_window_tracker_service.py::test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout Backend/tests/test_window_tracker_service.py::test_tracker_dashboard_fits_selected_surface_without_width_only_crop Backend/tests/test_window_tracker_service.py::test_live_dashboard_launcher_keeps_voice_bridge_opt_in Backend/tests/test_window_tracker_service.py::test_full_local_launcher_arms_live_shooter_without_broker_auto_open Backend/tests/test_window_tracker_service.py::test_tracker_http_emergency_stop_disables_live_execution -v --tb=short -ra
```

Result: 15 passed in 35.25s.

Safe broader command:

```powershell
python -m pytest Backend/tests/test_mobile_api_service.py Backend/tests/test_mobile_observer_service.py Backend/tests/test_window_tracker_service.py Backend/tests/test_ui_copy_hardening.py -v --tb=short -ra
```

Static size check command:

```powershell
Get-Item Frontend\dashboard\static\window_tracker_dashboard.html, Frontend\assets\share\css-control\*.png | ForEach-Object { "{0}`t{1}" -f $_.Name, $_.Length }
```

## Commands Not Allowed During QA

Do not run these for performance QA because they can trigger live runtime or broker-facing flows:

```powershell
.\start_live_dashboard.ps1
.\start_phoenixguard_full_local.ps1
python shooter.py signal --session-id pocket-live-8788 --base-url http://127.0.0.1:8793 --poll 0.05 --max-signal-age 8 --preferred-source tracker --require-preferred-source --shooter-mode LIVE_READY --no-auto-open
```

Also do not use performance probes that call:

- `POST /v1/mobile/window-tracker/sessions/{sessionId}/start`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/capture-once`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/demo-random-trade`
- `PATCH /v1/mobile/window-tracker/sessions/{sessionId}/controls` with live execution enabled

## Performance Risks

- The scene image payload is the largest frontend cost.
- A 1-second polling loop can become expensive if payloads grow large or if render work expands.
- Re-rendering hotspot layers after every session update should be profiled once browser automation exists.
- Dense text panels and long diagnostic values should be tested for layout thrash.

## Next Instrumentation Step

Add a fake-service dashboard harness with browser performance marks around:

- `refreshSession()`
- `renderSession()`
- `renderSurface()`
- `renderHotspots()`
- image load to first usable surface

The harness must use fixture data only and must not call live capture, live broker, or shooter execution paths.
