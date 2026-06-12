# PhoenixGuard Frontend V4 Responsive Layout Report

Date: 2026-05-20

Scope: responsive review of the static dashboard CSS and existing layout tests. No live dashboard process, live tracker, shooter, or broker automation was launched.

## Breakpoints Found

The dashboard CSS includes these responsive branches:

- `@media (prefers-reduced-motion: reduce)`
- `@media (max-width: 1380px)`
- `@media (max-width: 860px)`
- `@media (max-width: 560px)`
- an additional `@media (max-width: 860px)` branch for later dashboard sections

Primary desktop grids:

- `.signal-deck`: three columns for primary signal, brief, and metrics.
- `.kernel-deck`: two columns for narrative and kernel metrics.
- `.metric-grid`: four metric tiles.
- `.kernel-metrics`: seven kernel tiles.
- `.lower-grid`: four study/history/detail modules.

Responsive behavior:

- At `max-width: 1380px`, major decks collapse to one column.
- At `max-width: 860px`, topbar, session cluster, control ribbon, and surface tools wrap into mobile-oriented stacks.
- At `max-width: 560px`, major sections reduce padding and control groups become single-column/compact stacks.
- Text-heavy values use `overflow-wrap: anywhere` in multiple areas to prevent long tokens from pushing layout.
- Session token uses overflow handling and ellipsis.

## Safe Responsive Test Commands

Static/source-level responsive checks currently live inside pytest rather than browser viewport automation:

```powershell
python -m pytest tests/test_window_tracker_service.py::test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout tests/test_window_tracker_service.py::test_tracker_dashboard_fits_selected_surface_without_width_only_crop -v --tb=short -ra
```

Broader safe dashboard command:

```powershell
python -m pytest tests/test_mobile_api_service.py tests/test_ui_copy_hardening.py tests/test_window_tracker_service.py::test_tracker_http_surface_serves_session_artifacts_and_dashboard tests/test_window_tracker_service.py::test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout tests/test_window_tracker_service.py::test_tracker_dashboard_fits_selected_surface_without_width_only_crop -v --tb=short -ra
```

Do not use live launchers for responsive QA. They can start runtime capture and live execution paths depending on saved focus and script parameters.

## Viewport Checklist

Recommended manual viewport matrix for a future fake-service browser harness:

- 1800 x 1100: verify wide desktop density and no excess empty bands.
- 1366 x 768: verify major grids collapse at the intended point.
- 1024 x 768: verify control ribbon and surface tools wrap without covering chart controls.
- 768 x 1024: verify lower-grid modules stack cleanly.
- 390 x 844: verify signal action, session chip, emergency stop, and layer controls remain tappable.
- 320 x 740: verify no horizontal page scroll except inside the intended surface stage when zoomed.

## Responsiveness Findings

- The CSS uses `minmax(0, 1fr)` in grid tracks, which is important for dense dashboard content.
- Fixed-format controls have stable minimum heights and compact padding.
- The chart surface has a dedicated scrollable stage and fit-plane mode to avoid width-only cropping.
- The dashboard uses `100svh`, which is better suited to mobile browser chrome than plain `100vh`.
- The current source is structured for responsive stacking, but the high control count still needs browser screenshot validation before release.

## Gaps

- No automated viewport screenshot suite exists in the repo.
- No automated tap-target or accessibility viewport pass was found.
- No mobile Android Compose frontend QA was executed in this pass; this report focuses on the Frontend V4 dashboard surface.
