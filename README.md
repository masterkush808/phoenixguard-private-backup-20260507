# 808Fx Standard System

## Overview

808Fx Standard System is a premium chart-analysis workstation for financial
signal review, memory-augmented reasoning,
and multi-module decision support. It combines layered visual intelligence,
historical recall, and consensus logic for
disciplined signal generation.

## Features

- MemoryBank (HNSW few-shot recall + logit boost)
- 12-gate CurriculumGates (formal automata, ontology, regression, predictive)
- Live support checks for continuation strength, trend alignment, memory

alignment, counterforce, execution permission,

forecast calibration, interval efficiency, regime stability, and transition
alignment

- 3-condition ensemble consensus
- Per-run Plotly skill-contribution dashboard
- Online RL update every 50 memory-bank recalls
- Reactive Gradio workstation with live overlay tuning
- Confidence heatmap, compare desk, and scenario lab
- Zone Studio with persistent support/resistance/reaction teaching memory
- Feed tab with visual labeling, saved result evidence, and persisted RL/replay

learning

- Session timeline and pattern browser for in-session review
- Multi-timeframe hotkey capture workflow with floating HUD
- Continuous observer API with freshness decay, session-level regime adaptation,

and transition-only alerting

- Automatic locked-window tracker for Pocket Option-style desktop charts with

object-level candle IDs across frames

- Live tracking dashboard with dual study tracking, pending and resolved

learning samples, and timeframe-aware outcome

study

## Project Layout

- `phoenixguard/core/`: shared config and utility helpers
- `phoenixguard/vision/`: preprocessing, CV reasoning, grounded parsing, and

detector logic

- `phoenixguard/runtime/`: security, adaptation, local ensemble runtime, and

continual adapters

- `phoenixguard/memory/`: memory bank ingestion and retrieval features
- `phoenixguard/decision/`: ensemble, regression, RL, gates, and personalization
- `phoenixguard/training/`: reusable training implementation
- Root `main.py`, `train_*.py`, and `hf_model_check.py`: entry scripts that now

point into the organized package layout

## Setup Instructions

### 1. Clone the repository and enter the project directory

```powershell

cd "c:/Users/thaba/OneDrive/Documents/The 808 Vision 2026/phoenixguard"

```

### 2. Create and activate the virtual environment

```powershell

python -m venv .venv
.\.venv\Scripts\Activate.ps1

```

### 3. Install dependencies once

```powershell

pip install -r requirements.txt

```

### 4. Fast launch

If you opened a new PowerShell session, activate the environment again first:

```powershell

.\.venv\Scripts\Activate.ps1
.\start_live_dashboard.ps1

```

Use that when you want the local live-tracking dashboard, locked-window tracker,
and dashboard API to come up together.
The voice command bridge stays off by default to keep the workstation lighter.

If you want the main workstation instead:

```powershell

.\.venv\Scripts\Activate.ps1
.\start_phoenixguard.ps1

```

The 808Fx Standard System launcher defaults to the `FAST` runtime profile so
startup avoids the heaviest warmups and
optional CPU ensemble loading.

### 5. Optional runtime profiles

Choose one of these after activation:

```powershell

.\.venv\Scripts\Activate.ps1
.\start_phoenixguard.ps1 -Profile FAST
.\start_phoenixguard.ps1 -Profile BALANCED
.\start_phoenixguard.ps1 -Profile FULL
.\start_phoenixguard.ps1 -Profile HEAVY_LAZY

```

- `FAST`: quickest startup and lighter first inference. Disables test-time

adaptation, automatic replay continual

learning, recall-paced RL updates, foundation grounded backends, and local
ensemble auto-load, while still allowing
explicit learning submissions through the Feed tab.

- `BALANCED`: keeps a lighter runtime while leaving an upgrade path to the full

stack.

- `FULL`: restores the heavier experience, including launch-time preloading and

CUDA local-ensemble enablement when

available.

- `HEAVY_LAZY`: keeps startup light, but automatically runs the heavyweight

council on inference through the persistent

worker. On CPU, it requests the full council lazily, keeps only a small resident
model cache, and reuses cached results
for static images.

- `Model Council` is now lazy-loaded from its tab. Opening that tab starts a

persistent local worker, loads heavyweight

council models on demand, and reuses the refined result for the current static
image instead of front-loading that cost
at app launch.

### 6. Optional bootstrap / validation path

```powershell

.\.venv\Scripts\Activate.ps1
.\start_phoenixguard.ps1 -Bootstrap -RunTests -CheckHF -Profile FULL

```

Use this when you intentionally want dependency refresh, tests, and Hugging Face
validation. It is no longer part of the
default launch path.

### Tracing

PhoenixGuard now initializes OpenTelemetry tracing automatically when the mobile
API or workstation launches.

- By default, traces export to `http://localhost:4318/v1/traces` for AI Toolkit

or any local OTLP collector.

- Set `PHOENIXGUARD_TRACING_DISABLED=1` to turn tracing off.
- Override the exporter with `PHOENIXGUARD_OTLP_ENDPOINT` or the standard

`OTEL_EXPORTER_OTLP_ENDPOINT`.

- Override the service labels with `PHOENIXGUARD_TRACE_SERVICE_NAME` /

`OTEL_SERVICE_NAME` and

`PHOENIXGUARD_TRACE_SERVICE_VERSION` / `OTEL_SERVICE_VERSION`.

- Optional headers can be provided through `PHOENIXGUARD_OTLP_HEADERS` or

`OTEL_EXPORTER_OTLP_HEADERS`.

### Testing

Activate the virtual environment first:

```powershell

.\.venv\Scripts\Activate.ps1

```

Targeted regression subset used for the council, feedback, grounded-chart, and
manual multi-timeframe fixes:

```powershell

python -m pytest `
  tests/test_adaptive_runtime.py::test_grounded_chart_merges_optional_backend_regions `
  tests/test_adaptive_runtime.py::test_grounded_chart_structure_summary_tracks_directional_bias `
  tests/test_feedback_learning_flow.py::test_on_feedback_saves_result_image_and_routes_it_into_learning `
  tests/test_manual_multi_timeframe_upload.py::test_run_signal_workstation_requires_exactly_four_uploaded_images `
  tests/test_manual_multi_timeframe_upload.py::test_run_signal_workstation_combines_higher_and_lower_timeframes `
  tests/test_multi_timeframe_fusion.py::test_multi_timeframe_fusion_combines_four_frame_groups `
  -v --tb=short -ra

```

Focused end-to-end coverage:

```powershell

python -m pytest tests/test_end_to_end.py -v --tb=short -ra

```

Live tracker and dashboard regression subset:

```powershell

python -m pytest `
  tests/test_mobile_api_service.py `
  tests/test_mobile_observer_service.py `
  tests/test_window_tracker_service.py `
  tests/test_rl_runtime_integration.py `
  -v --tb=short -ra

```

Broad project suite:

```powershell

python -m pytest tests/test_full_suite.py -v --tb=short -ra

```

Full repository sweep:

```powershell

python -m pytest tests -v --tb=short -ra

```

- `tests/test_full_suite.py` is the broad project-wide validation pass.
- `tests/` is the full repo sweep, including adaptive runtime, overlays, share

surface, real-model checks, and

integration coverage.

- Some `tests/test_real_models.py` checks may be skipped when Hugging

Face-backed CV assets or `sentence-transformers`

are unavailable in the current environment.

### 6a. 808 Shooter Manual Testing

The 808 Shooter is a real-click Pocket Option executor with adaptive expiry selection driven by Phoenix Guard signals.

Validate calibration and adaptive expiry by running these manual tests:

Preview calibration points (all 15 interactive points):

```powershell

python "808 Shooter.py" preview

```

Test manual trade execution with 2-hour expiry (7200 seconds):

```powershell

python "808 Shooter.py" manual buy 7200 --window-query "Pocket Option"

```

Test manual trade execution with 2.5-hour expiry (9000 seconds):

```powershell

python "808 Shooter.py" manual buy 9000 --window-query "Pocket Option"

```

Full integration test with live Phoenix Guard signals and adaptive verbose diagnostics:

```powershell

python "808 Shooter.py" signal --session-id pocket-live-8788 --base-url http://127.0.0.1:8000 --adaptive-verbose

```

- `preview`: Shows all 15 calibration points with coordinates (broker_screen, time_button, hourly/minute controls, buy_icon, sell_icon, presets, final_screen)
- `manual buy/sell <seconds>`: Simulates trade execution with specified expiry, validates hourly+minute typing or preset selection
- `signal`: Runs continuous polling of Phoenix Guard signals, applies adaptive expiry selection, and executes trades via real clicks
- `--adaptive-verbose`: Enables DEBUG-level logging with structured JSON diagnostics showing expiry source, raw values, and all candidates

### 7. Optional hotkey capture workflow

- Press `Ctrl+V` to open a drag-select capture overlay on Windows.
- Drag the chart region, then press `Enter` to confirm or `Esc` to cancel.
- If `Ctrl+V` is unavailable, the app falls back to `Ctrl+Shift+4`.
- The first two confirmed captures are staged as the higher timeframe pair.
- Switch to the lower timeframe views and press the hotkey again until all four

captures are staged.

- After the fourth confirmation, the app runs multi-timeframe inference

automatically and refreshes the open desk.

### 7b. Continuous observer workflow

- Start the mobile API as usual with `.\start_phoenixguard_mobile_api.ps1`.
- Create an observer session through `POST /v1/mobile/observer/sessions`.
- Push repeated four-image bundles into `POST

/v1/mobile/observer/sessions/{sessionId}/bundles`.

- Read the live signal from `GET

/v1/mobile/observer/sessions/{sessionId}/signals/latest`.

- Observer signals decay toward `HOLD` as they age, and the session

automatically tightens its threshold when recent

bundles keep flipping direction.

### 7c. Locked window tracker workflow

- Start the mobile API with `.\start_phoenixguard_mobile_api.ps1`.
- Create a tracker session through `POST /v1/mobile/window-tracker/sessions`

with `window_query` set to your desktop app

title, for example `Pocket Option`.

- For the Pocket Option browser-style tab shown in the desk screenshot, prefer

`layout_profile = "auto"`. The tracker

now runs adaptive chart-surface detection first and only falls back to the
legacy Pocket Option profile crop if the
adaptive surface confidence is weak.

- The tracker locks the first matching visible window, runs a `capture ->

perception -> decision` pipeline, saves the

raw window frame, adapts the chart surface crop, derives single-chart observer
views from that chart surface, and pushes
them into the observer session automatically.

- The tracker also keeps object-level candle IDs across frames using the

existing candle parser and exposes the current

tracked-candle snapshot in the tracker session payload.

- The tracker reads the active pair label and the visible timeframe selector

directly from the locked window when those

UI elements are visible.

- Session payloads now include `layout_profile`, `effective_layout_profile`,

`last_chart_region`, and

`next_capture_in_sec` so you can verify the exact area being tracked and how
long remains until the next automatic
capture.

- The live dashboard shows that countdown in real time, and the capture worker

now uses a monotonic schedule so refresh

timing stays precise.

- Execution controls default to `shadow` mode. In shadow mode PhoenixGuard reads

the full broker window, verifies the

`$5` amount lock, calculates the expiry from timeframe plus the decision kernel
hold window, and reports the click it
would make without touching the broker UI.

- The execution listener always reads BUY/SELL, amount, and expiry controls from

the `full_window_gui` broker capture.

The locked chart focus region is only used for candle study, so a chart-only
crop cannot hide the far-right Pocket
Option order panel from the execution agent.

- When the saved focus covers the full broker window, the tracker now derives a

tighter candle-study plane from that

full capture before drawing overlays, so structure, support/resistance, and
future zones stay aligned to the candles
while execution still reads the whole order panel.

- Live clicking is Windows-only and requires an armed focus region. When

enabled, it sets the broker expiry/time field,

sets the amount field back to `$5`, and clicks only after the memory, market
identity, amount, expiry, cooldown, and
active-trade gates pass.

- If expiry verification fails or the broker controls become unstable after a

timer adjustment, execution records the

blocked stage and waits `60s` before retrying the same side/lane/expiry so it
does not keep changing the broker timer.

- `Demo M3 Trade` on the dashboard runs one broker-control test: it randomly

chooses BUY or SELL, sets the expiry to

`00:03:00` through the Pocket Option `M3` popup shortcut, resets amount to `$5`,
and clicks only if the BUY/SELL,
amount, and expiry fields are visible and detected.

- The live launcher defaults the tracker to a `3s` base cadence, with the

adaptive timer tightening to `0.5s`

around `WAIT_FOR_SNIPER`, `WAIT_FOR_TRIGGER`, and executable ready states,

then relaxing up to the `10s` idle cap when the setup is only being watched.

- If a broker focus is already saved, `start_live_dashboard.ps1` now starts the

tracker automatically in shadow

execution mode with scenario generation, memory projection, market identity
checks, and countertrend scalp reading
enabled. Broker timeframe OCR is advisory by default, so a tiny or unreadable
timeframe label no longer blocks execution
after the PhoenixGuard expiry planner has selected a valid minute/hour duration.

- Press `Ctrl+Alt+End` while the API is running to emergency-stop the tracker.

This stops capture and disables live

execution for the active session.

- Useful routes:
  - `GET /v1/mobile/window-tracker/windows`
  - `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-chart`
  - `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-window`
  - `GET /v1/mobile/window-tracker/dashboard/{sessionId}`
  - `POST /v1/mobile/window-tracker/sessions/{sessionId}/start`
  - `POST /v1/mobile/window-tracker/sessions/{sessionId}/capture-once`
  - `POST /v1/mobile/window-tracker/sessions/{sessionId}/demo-random-trade`
  - `POST /v1/mobile/window-tracker/sessions/{sessionId}/stop`

Quick one-command live launch:

```powershell

.\.venv\Scripts\Activate.ps1
.\start_live_dashboard.ps1

```

```powershell

.\.venv\Scripts\Activate.ps1
.\start_live_dashboard.ps1 -ApiPort 8793 -ForceRestart -NoBrowser


.\.venv\Scripts\Activate.ps1
python "808 Shooter.py" signal --session-id pocket-live-8788 --base-url http://127.0.0.1:8793

```

Useful variants:

```powershell

.\start_live_dashboard.ps1 -NoBrowser
.\start_live_dashboard.ps1 -ApiPort 8792
.\start_live_dashboard.ps1 -SessionId pocket-live-gbpjpy
.\start_live_dashboard.ps1 -EnableVoiceControl

```

- `start_live_dashboard.ps1` starts the local API, waits for health, warms the

tracker runtime, creates the default

locked session when needed, starts capture, and opens the local dashboard URL
automatically unless `-NoBrowser` is used.
It does not start the optional voice command bridge unless `-EnableVoiceControl`
is passed.

- The first live-dashboard start can spend up to a couple of minutes warming the

tracker runtime and model stack before

the session is ready.

- Runtime data and logs default to `.codex_runtime\data_live` and

`.codex_runtime\logs_live`.

### 7d. Reading the live dashboard

- `Locked Window` shows the full captured application surface, while `Chart

Crop` shows the tracker-focused trading

ground.

- Dashboard raw surface mode shows the latest full broker GUI capture. Overlay

mode stays on the selected chart plane

because the candle overlays are drawn in chart coordinates.

- The dashboard updates the detected pair name from the locked Pocket Option

window and uses the detected timeframe

selector as the market-time basis for live outcome study.

- `Live Signal State` shows the current actionable layer. `HOLD` there means the

execution gate is still blocked, not

that the tracker has no directional opinion.

- `Learning Runtime` shows the current policy study direction, blend weight,

pending learning samples, resolved sample

count, and recent learning accuracy.

- `Outcome Study` summarizes what happened after earlier BUY or SELL calls

resolved, including direction, proxy travel,

candles elapsed, and market-time elapsed.

- The tracker now logs one RL context per bundle, then resolves that context

after enough future frames arrive.

- Market-time reporting is timeframe aware. Example: on `M5`, a resolved sample

that moved for `4` candles is reported

as `20m`.

- The dashboard uses the detected timeframe first. If the selector cannot be

read, it falls back to the current signal

timeframe.

- Full tracker architecture and setup reference:

[docs/window_tracker_deep_dive.md](</c:/Users/thaba/OneDrive/Documents/The 808 Vision
2026/phoenixguard/docs/window_tracker_deep_dive.md>)

### 8. Visual Lab workflow

- `Compare Desk` shows raw, focused, annotated, and heatmap views with

client-side zoom and pan controls.

- `Scenario Lab` clones the current chart into a threshold sandbox without

overwriting the live desk.

- `Zone Studio` lets you paint support, resistance, and reaction zones that are

saved into persistent teaching memory.

### 9. Feed workflow

- Open `Feed` after an analysis run finishes.
- Upload the real result image, mark it up inside the visual labeling canvas,

then click `Save Visual Label`.

- Choose the signal direction, execution result, market state, setup state,

optional failure mode, and label-confidence

percentage before clicking `Submit To Learning`.

- The Feed now saves both the flattened result image and a structured

visual-label sidecar so replay memory, RL, and

recovery can use machine-readable labels instead of relying on free-text alone.

- Feedback submissions are journaled to disk first, then applied into

personalization, replay, and RL so restarts can

resume unfinished learning work instead of losing it.

### 10. Session review workflow

- `Session Timeline` keeps the analyzed captures from the current session in

order.

- `Pattern Browser` surfaces visually similar session cases based on action,

projection, confidence, and memory profile.

### 11. Worldwide protected sharing

- Use `share_phoenixguard.py` or `.\start_phoenixguard_share.ps1` to launch the

808Fx Standard System protected share

desk instead of exposing `main.py`.

Quickest worldwide path:

```powershell

.\.venv\Scripts\Activate.ps1
$env:PHOENIXGUARD_SHARE_CREDENTIALS='you:StrongPass2026!,brother:BrotherPass2026!'
.\start_phoenixguard_share.ps1 -LaunchMode FAST -AccessMode TUNNEL

```

- `TUNNEL` keeps the app on `127.0.0.1` and lets Gradio generate a temporary

public HTTPS link protected by login.

- `PUBLIC` binds to `0.0.0.0`, but that alone is still not worldwide. You also

need port forwarding or a reverse

proxy/tunnel.

Free Cloudflare no-domain path for a running local VM/app:

```powershell

.\deploy\windows\Start-PhoenixGuardVmShare.ps1
.\deploy\windows\Start-PhoenixGuardQuickTunnel.ps1 -StopExisting

```

- `Start-PhoenixGuardQuickTunnel.ps1` opens a temporary `trycloudflare.com` URL,

writes the discovered link to

`deploy\windows\logs\quick-tunnel.url.txt`, and keeps the Quick Tunnel process
running until you stop it.

Stop the free Quick Tunnel with:

```powershell

.\deploy\windows\Stop-PhoenixGuardQuickTunnel.ps1

```

- See `docs/share/WORLDWIDE_SHARE.md` for the secure quick-share path and the Cloudflare

Tunnel setup.

### 12. Windows VM + Cloudflare Tunnel

- For an always-on deployment that does not depend on your own PC, run

PhoenixGuard on a Windows cloud VM and keep the

app bound to `127.0.0.1`.

- Use `deploy\windows\Start-PhoenixGuardVmShare.ps1` to launch the protected

share desk from a VM-specific env script.

- Use `deploy\windows\Register-PhoenixGuardShareTask.ps1` to start PhoenixGuard

automatically on every VM boot.

- Use `deploy\windows\Install-CloudflaredTunnel.ps1` to install the

`cloudflared` Windows service for a remotely-managed

Cloudflare Tunnel.

- Use `deploy\windows\Start-PhoenixGuardQuickTunnel.ps1` if you want a free

temporary Cloudflare URL without attaching

your own domain yet.

Example launch command:

```powershell

.\deploy\windows\Start-PhoenixGuardVmShare.ps1

```

- Share mode now supports bounded queue size, request rate limits, upload safety

checks, and mutation-guarded inference

so multi-user traffic is less likely to churn VM state.

- The full operator runbook lives in

`deploy\windows\WINDOWS_VM_CLOUDFLARE_TUNNEL.md`.

### 13. Windows watchdog for the local desk

- Use `deploy\windows\Start-PhoenixGuardWatchdog.ps1` when you want the standard

desk to auto-restart after crashes or

unexpected exits.

- Use `deploy\windows\Register-PhoenixGuardWatchdogTask.ps1` to register that

watchdog as a Windows startup or logon

task.

Example launch command:

```powershell

.\deploy\windows\Start-PhoenixGuardWatchdog.ps1

```

- The watchdog writes its restart history to

`deploy\windows\logs\phoenixguard-watchdog.log`.

## Notes

- The pipeline uses a layered proprietary vision ensemble tuned locally on your

chart images from `808 Memory/BUYS` and

`808 Memory/SELLS`.

- Fine-tuning and model saving are fully automated; model assets are stored

locally for fast, private inference.

- Runtime behavior can be tuned with `PHOENIXGUARD_PROFILE` and the

`PHOENIXGUARD_*` overrides in

`phoenixguard/core/config.py`.

- If you encounter missing module errors, install them with `pip install

<module>`.

- For Ultralytics settings, see: [Ultralytics settings](https://docs.ultralytics.com/quickstart/#ultralytics-settings)

## License

Proprietary. All rights reserved.
