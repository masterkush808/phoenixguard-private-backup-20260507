## Unreleased

### Fixed
- Normalize session artifact keys to `last_chart_path`, `last_overlay_path`, and `last_window_path` for dashboard compatibility.
- Canonicalize projection and memory artifact keys returned by tracker adapters into `projection_image_path` and `reference_image_path`.
- Ensure overlay registry entries include `bbox` and `truth_score` for renderer compatibility.

### Added
- `phoenixguard/vision/contracts.py` — overlay and chart state helpers.
- Unit tests for session payload normalization, projection artifact resolution, and API integration for `show-future` + `latest-projection`.
- Visual regression smoke test creating baseline PNG.

