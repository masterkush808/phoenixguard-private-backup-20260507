# PhoenixGuard Frontend V4 Information Architecture

Source surface: `Frontend/dashboard/static/window_tracker_dashboard.html`
Route source: `Backend/src/phoenixguard/mobile_api/app.py`
Protected title text: `808Fx Standard Hybrid System`

## IA Goal

Frontend V4 should keep the current dashboard as a live operations console, but group the existing panels by operator intent instead of raw implementation order. The current static dashboard already exposes the required domains; V4 should classify, label, and prioritize them without renaming the protected title text `808Fx Standard Hybrid System`.

## Primary Buckets

| Bucket | Operator intent | Current DOM mapping |
| --- | --- | --- |
| Live | See current session status, signal, countdown, focus lock, and capture count. | `.topbar`, `.signal-deck`, `#signal-card`, `#signal-action`, `#signal-confidence`, `#signal-summary`, `#signal-pills`, `.metric-grid`, `#metric-status-tile`, `#metric-next-tile`, `#metric-focus-tile`, `#metric-captures-tile`, `#live-dot`, `#metric-status-mini`, `#session-chip` |
| Council | Understand the decision kernel, bias, model council state, permission, and gate reasoning. | `.kernel-deck`, `#kernel-story-card`, `#kernel-side`, `#kernel-state`, `#kernel-story`, `.kernel-metrics`, `#kernel-bias-tile`, `#kernel-candle-tile`, `#kernel-mode-tile`, `#kernel-trigger-tile`, `#kernel-target-tile`, `#kernel-stale-tile`, `#kernel-event-tile`, `#layer-council-decision`, `#telemetry-reality`, `#telemetry-entry-quality`, `#telemetry-permission` |
| Chart | Inspect the broker/chart surface, overlay image, raw locked window, overlay layers, zoom, pan, and hotspots. | `.operation-grid`, `.surface-module`, `#surface-stage`, `#surface-canvas`, `#surface-overlay`, `#surface-raw`, `#hotspot-layer`, `#surface-placeholder`, `.surface-tools`, `#zoom-slider`, `#zoom-in`, `#zoom-out`, `#zoom-fit`, `#zoom-actual`, `#pan-center`, `#surface-caption`, `#surface-perf`, `#layer-chart-bounds`, `#layer-recent-candles`, `#layer-major-swings`, `#layer-local-swings`, `#layer-supply-demand`, `#layer-trigger-zones`, `#layer-broker-controls` |
| Replay | Review recent studies and historical overlays. | `.history-module`, `#history-list`, `#history-caption`, `.history-item`, `.history-action`, `.history-copy`, `.history-meta`, `#layer-historical-replay` |
| Simulation | Generate or inspect forward path, prediction memory, scenario heatmap, demo timing, and counter-scalp/scenario controls. | `.prediction-module`, `#prediction-gallery`, `#prediction-input-img`, `#prediction-memory-img`, `#prediction-overlay-img`, `#prediction-metadata`, `#prediction-signal`, `#prediction-conf`, `#prediction-gates`, `#prediction-memory`, `#scenario-heatmap`, `#predict-now`, `#show-future`, `#demo-trade`, `#scenario-toggle`, `#counter-scalp-toggle` |
| Calibration | Manage focus region, locked window state, timeframe, selector state, and capture cadence. | `.focus-module`, `.focus-stack`, `#focus-window-cell`, `#focus-timeframe-cell`, `#focus-selector-cell`, `#focus-execution-cell`, `#focus-error-cell`, `#focus-arm`, `#focus-clear`, `#capture-now`, `#adaptive-timer-toggle`, `#metric-next`, `#tracking-mode-label` |
| Diagnostics | Surface runtime telemetry, errors, latency, model health, cache, packet age, and diagnostic overlay. | `.telemetry-module`, `#telemetry-grid`, `#telemetry-updated`, `#telemetry-compute`, `#telemetry-queue`, `#telemetry-packet`, `#telemetry-cache`, `#telemetry-paper`, `#telemetry-path`, `#telemetry-reality`, `#telemetry-entry-quality`, `#telemetry-permission`, `#telemetry-avoided`, `#latency-pipeline`, `#latency-overlay`, `#latency-budget`, `#model-health-panel`, `#focus-error`, `#layer-diagnostics` |
| Settings | Operate tracker mode, execution mode, gates, memory projection, layer visibility, and emergency controls. | `.control-ribbon`, `.action-group`, `.mode-group`, `#tracker-toggle`, `#execution-toggle`, `#execution-mode-toggle`, `#emergency-stop`, `#memory-gate-toggle`, `#identity-gate-toggle`, `#auto-memory-toggle`, `#refresh-now`, `#mode-overlay`, `#mode-raw`, `.layer-controls`, `.layer-toggle` |

## Recommended V4 Navigation Model

Use these top-level tabs or segmented views:

1. Live
2. Council
3. Chart
4. Replay
5. Simulation
6. Calibration
7. Diagnostics
8. Settings

The first viewport should keep Live, Council summary, and Chart visible together because they answer the operator's immediate question: what is happening, why, and where on the broker surface. Replay, Simulation, Calibration, Diagnostics, and Settings can be secondary panels or drawer-level views.

## Panel Ownership

| Current panel | V4 bucket | Keep visible in first viewport? | Notes |
| --- | --- | --- | --- |
| Brand/topbar | Live | Yes | Must preserve `808Fx Standard Hybrid System`. |
| Signal overview | Live | Yes | Primary answer surface for current action and confidence. |
| Session metrics | Live | Yes | Status, next study, focus, and captures are live state. |
| Decision kernel | Council | Yes, compact | Keep headline and strongest next-event clocks in first viewport. |
| Control ribbon | Settings | Condensed | Move high-risk actions behind deliberate controls; keep start/stop and emergency visible. |
| Broker Surface | Chart | Yes | Primary visual workspace. |
| Adaptive Inspector | Live / Diagnostics | Yes, context panel | It is cross-bucket detail; it should follow selected item context. |
| Study Map | Chart / Council | Secondary | It summarizes structural reads that feed the council. |
| Focus State | Calibration | Secondary | Promote only while focus is pending or broken. |
| Runtime Telemetry | Diagnostics | Secondary | Promote on warning/error states. |
| Recent Studies | Replay | Secondary | Useful after live state is stable. |
| PhoenixGuard Read | Council | Secondary | Narrative reasoning; should be expandable from Council. |
| Prediction Images | Simulation | Secondary | Promote after `Predict` or `Show Future`. |
| Scenario Heatmap | Simulation | Secondary | Promote when `#scenario-toggle` is on. |

## IA Constraints

- Do not change the title text `808Fx Standard Hybrid System`.
- Keep DOM mappings stable for current JavaScript selectors.
- Treat `#inspector-panel` as a shared detail target, not a separate primary bucket.
- Use route-backed state as the source of truth; the dashboard polls the session route every second.
- Keep emergency and execution state visible even if most Settings controls move to a secondary area.
