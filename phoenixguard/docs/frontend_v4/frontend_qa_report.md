# PhoenixGuard Frontend V4 QA Report

Date: 2026-05-20

Scope: local dashboard QA for `Frontend/dashboard/static/window_tracker_dashboard.html`, the FastAPI dashboard routes in `Backend/src/phoenixguard/mobile_api/app.py`, the launch scripts, and the existing pytest coverage. This QA pass did not start the dashboard runtime, tracker loop, shooter, voice bridge, or any broker-facing process.

## Safety Boundary

Live broker execution must not be triggered during QA. Treat these as inspect-only unless an operator explicitly chooses a live validation session outside QA:

- `.\Backend\launch\start_live_dashboard.ps1`
- `.\Backend\launch\start_phoenixguard_full_local.ps1`
- `.\.venv\Scripts\python.exe Backend\launch\shooter.py signal ...`
- any request to `/v1/mobile/window-tracker/sessions/{sessionId}/start`
- any request to `/v1/mobile/window-tracker/sessions/{sessionId}/capture-once`
- any request to `/v1/mobile/window-tracker/sessions/{sessionId}/demo-random-trade`
- any controls patch that enables `live_execution_enabled` or `execution_mode = "live"`

Observed launcher risk:

- `Backend/launch/start_live_dashboard.ps1` can patch controls to live mode and can auto-start tracking when broker focus already exists.
- `Backend/launch/start_phoenixguard_full_local.ps1` starts tracker tooling and then launches `Backend/launch/shooter.py` in `LIVE_READY` mode with `PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS` set inside the shooter process.

## Dashboard Surface Reviewed

- Static dashboard shell: `Frontend/dashboard/static/window_tracker_dashboard.html`
- Dashboard route renderer: `_render_window_tracker_dashboard()` in `Backend/src/phoenixguard/mobile_api/app.py`
- Dashboard route: `GET /v1/mobile/window-tracker/dashboard/{session_id}`
- Asset route: `GET /v1/mobile/window-tracker/assets/{asset_name}`
- Session and control routes are present, but live/state-changing routes were not executed during QA.

The dashboard is a single static HTML/CSS/JS surface of 187,212 bytes. It renders a high-density operator console with signal summary, kernel metrics, tracker controls, zoom/pan chart surface, overlay layer controls, diagnostics, prediction panes, and history/detail panels.

## Tests Executed

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_mobile_api_service.py Backend/tests/test_ui_copy_hardening.py Backend/tests/test_window_tracker_service.py::test_tracker_http_surface_serves_session_artifacts_and_dashboard Backend/tests/test_window_tracker_service.py::test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout Backend/tests/test_window_tracker_service.py::test_tracker_dashboard_fits_selected_surface_without_width_only_crop Backend/tests/test_window_tracker_service.py::test_live_dashboard_launcher_keeps_voice_bridge_opt_in Backend/tests/test_window_tracker_service.py::test_full_local_launcher_arms_live_shooter_without_broker_auto_open Backend/tests/test_window_tracker_service.py::test_tracker_http_emergency_stop_disables_live_execution -v --tb=short -ra
```

Result: 15 passed in 35.25s.

Coverage from this safe subset:

- Mobile API job flow and lazy health route.
- UI copy hardening around operator-facing language and hidden backend terms.
- Dashboard serving and latest artifact route behavior using test services.
- Dashboard image layout expectations for uncropped, full-width prediction surfaces.
- Fit-plane logic expectations for selected/full broker surfaces.
- Launch script static checks for optional voice bridge and broker auto-open behavior.
- Emergency stop route disables live execution in a test service.

## Recommended QA Commands

Activate the repo environment first:

```powershell
.\.venv\Scripts\Activate.ps1
```

Safe frontend/dashboard regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_mobile_api_service.py Backend/tests/test_ui_copy_hardening.py Backend/tests/test_window_tracker_service.py::test_tracker_http_surface_serves_session_artifacts_and_dashboard Backend/tests/test_window_tracker_service.py::test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout Backend/tests/test_window_tracker_service.py::test_tracker_dashboard_fits_selected_surface_without_width_only_crop Backend/tests/test_window_tracker_service.py::test_live_dashboard_launcher_keeps_voice_bridge_opt_in Backend/tests/test_window_tracker_service.py::test_full_local_launcher_arms_live_shooter_without_broker_auto_open Backend/tests/test_window_tracker_service.py::test_tracker_http_emergency_stop_disables_live_execution -v --tb=short -ra
```

Broader but still unit/integration-oriented dashboard and tracker command:

```powershell
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_mobile_api_service.py Backend/tests/test_mobile_observer_service.py Backend/tests/test_window_tracker_service.py Backend/tests/test_ui_copy_hardening.py -v --tb=short -ra
```

Do not run live launchers as part of QA. If static validation of launch scripts is needed, use read-only checks:

```powershell
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_window_tracker_service.py::test_live_dashboard_launcher_keeps_voice_bridge_opt_in Backend/tests/test_window_tracker_service.py::test_full_local_launcher_arms_live_shooter_without_broker_auto_open -v --tb=short -ra
```

## Product Polish Findings

- The dashboard has a complete operator workflow surface: refresh, capture, prediction, future projection, emergency stop, memory gates, identity gates, adaptive timer, layer toggles, zoom controls, raw/overlay modes, and keyboard emergency stop.
- The emergency-stop affordance is present in both UI and API coverage.
- The UI is dense and operationally appropriate for repeated scanning, but QA should keep watching for mobile text crowding because the interface has many same-row controls.
- The copy hardening tests reduce accidental exposure of internal model/backend terminology.

## Gaps

- No browser-driven visual regression harness was found in repo dependencies.
- No automated accessibility scan command was found.
- No Lighthouse/performance automation was found.
- This QA pass did not start a local server, because doing so through the current launchers risks tracker/shooter side effects.
