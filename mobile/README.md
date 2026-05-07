# PhoenixGuard Mobile

## Architecture

The mobile integration leaves the existing PhoenixGuard inference pipeline untouched.

- `phoenixguard/mobile_api/`: FastAPI adapter that stages 4 ordered screenshots, submits them into the current quartet analysis flow, and exposes job/result/artifact endpoints for Android.
- `start_phoenixguard_mobile_api.py` and `start_phoenixguard_mobile_api.ps1`: launchers for the mobile API.
- `mobile/android/`: Jetpack Compose Android client with a premium quartet-capture surface, job polling, and overlay dossier rendering.

The Android app is intentionally a client, not a local model runner. The heavy multi-timeframe inference still runs through the current server-side PhoenixGuard stack.

## Mobile API

Run the API from the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
.\start_phoenixguard_mobile_api.ps1
```

Default API base URL:

```text
http://127.0.0.1:8787
```

Primary endpoints:

- `GET /v1/mobile/health`
- `GET /v1/mobile/config`
- `POST /v1/mobile/jobs`
- `GET /v1/mobile/jobs/{jobId}`
- `GET /v1/mobile/jobs/{jobId}/artifacts/{artifactName}`
- `GET /v1/mobile/observer/config`
- `POST /v1/mobile/observer/sessions`
- `GET /v1/mobile/observer/sessions`
- `GET /v1/mobile/observer/sessions/{sessionId}`
- `POST /v1/mobile/observer/sessions/{sessionId}/bundles`
- `GET /v1/mobile/observer/sessions/{sessionId}/signals/latest`
- `GET /v1/mobile/window-tracker/windows`
- `POST /v1/mobile/window-tracker/sessions`
- `GET /v1/mobile/window-tracker/sessions`
- `GET /v1/mobile/window-tracker/sessions/{sessionId}`
- `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-chart`
- `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-window`
- `GET /v1/mobile/window-tracker/dashboard/{sessionId}`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/start`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/capture-once`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/stop`

The observer endpoints are for continuous market watching rather than one-off bundle review. Each session keeps its own settings, applies freshness decay to old signals, and raises the confidence bar automatically when recent bundles keep reversing direction.

The window-tracker endpoints are for desktop automation. A tracker session locks onto a visible app window by title, captures it on a timer, auto-crops or profile-crops the chart region, synthesizes adaptive views from the same live chart, and keeps object-level candle IDs across frames.

For a Pocket Option browser-style tab, pass `layout_profile: "pocket_option_browser"` when creating the session. Tracker responses include `layout_profile`, `effective_layout_profile`, and `last_chart_region` so the locked chart area can be verified.

The dashboard route renders a small local chart-and-signal surface on top of the live session, while the latest artifact routes expose the current chart crop and full locked window as PNG images.

`POST /v1/mobile/jobs` expects exactly four multipart file parts named `screenshots` in this order:

1. `Higher TF / Zoomed Out`
2. `Higher TF / Zoomed In`
3. `Lower TF / Zoomed Out`
4. `Lower TF / Zoomed In`

## Android App

The Android project lives in `mobile/android/`.

Key design characteristics:

- obsidian background with restrained bronze signal accents
- image-led 4-slot capture matrix instead of generic upload cards
- compact control deck for higher timeframe, lower timeframe, overlay mode, and council scope
- job-driven result dossier with action, confidence, frame-by-frame readout, and overlay gallery

Default emulator base URL is already wired to:

```text
http://10.0.2.2:8787/
```

If you need a different API target, change `MOBILE_API_BASE_URL` in `mobile/android/app/build.gradle.kts`.

## Validation

Targeted Python verification for the new API seam:

```powershell
python -m pytest tests/test_mobile_api_service.py -q
```
