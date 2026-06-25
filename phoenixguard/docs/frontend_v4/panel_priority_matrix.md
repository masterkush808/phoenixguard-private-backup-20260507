# PhoenixGuard Frontend V4 Panel Priority Matrix

Source dashboard: `Frontend/dashboard/static/window_tracker_dashboard.html`
Protected title text: `808Fx Standard Hybrid System`

## Priority Definitions

| Priority | Meaning |
| --- | --- |
| P0 | Must be visible or one click away during live operation. |
| P1 | Important, but can live in a secondary column, tab, drawer, or expandable panel. |
| P2 | Contextual detail; promote only when relevant state changes. |
| P3 | Administrative or supporting content. |

## Matrix

| Panel / control group | Bucket | Priority | Current DOM IDs/classes | Promotion triggers |
| --- | --- | --- | --- | --- |
| Protected brand header | Live | P0 | `.topbar`, `.brand-lockup`, `.brand-title`, `#hero-copy`, `#session-chip`, `#live-dot`, `#metric-status-mini` | Always visible. Preserve `808Fx Standard Hybrid System`. |
| Signal overview | Live | P0 | `.signal-deck`, `#signal-card`, `#signal-action`, `#signal-confidence`, `#confidence-fill`, `#signal-summary`, `#signal-pills` | Always visible; highlight when action changes from HOLD. |
| Session metric tiles | Live | P0 | `.metric-grid`, `.metric-tile`, `#metric-status-tile`, `#metric-next-tile`, `#metric-focus-tile`, `#metric-captures-tile` | Always visible; focus tile promotes on unmatched window or focus error. |
| Decision kernel summary | Council | P0 | `.kernel-deck`, `#kernel-story-card`, `#kernel-side`, `#kernel-state`, `#kernel-story` | Always visible in compact form; expand on non-HOLD or blocked permission. |
| Decision clocks | Council | P1 | `.kernel-metrics`, `#kernel-bias-tile`, `#kernel-candle-tile`, `#kernel-mode-tile`, `#kernel-trigger-tile`, `#kernel-target-tile`, `#kernel-stale-tile`, `#kernel-event-tile` | Promote when trigger/target ETA is valid or setup life is stale. |
| Tracker command ribbon | Settings | P0/P1 | `.control-ribbon`, `.action-group`, `#tracker-toggle`, `#capture-now`, `#emergency-stop`, `#refresh-now` | Keep start/stop, capture, emergency visible; move lower-risk toggles to Settings. |
| Execution controls | Settings | P0/P1 | `#execution-toggle`, `#execution-mode-toggle`, `#demo-trade` | Promote when execution is live, blocked, or emergency-stopped. |
| Scenario and memory gates | Settings / Simulation | P1 | `#counter-scalp-toggle`, `#scenario-toggle`, `#memory-gate-toggle`, `#identity-gate-toggle`, `#auto-memory-toggle`, `#adaptive-timer-toggle` | Promote when simulation or permission panels are open. |
| View mode controls | Chart / Settings | P1 | `.mode-group`, `#mode-overlay`, `#mode-raw` | Keep near chart surface. |
| Broker Surface | Chart | P0 | `.surface-module`, `#surface-stage`, `#surface-canvas`, `#surface-overlay`, `#surface-raw`, `#hotspot-layer`, `#surface-placeholder` | Always visible; fallback placeholder if no image. |
| Surface tools | Chart | P1 | `.surface-tools`, `#zoom-out`, `#zoom-slider`, `#zoom-in`, `#zoom-fit`, `#zoom-actual`, `#pan-center`, `#zoom-readout` | Keep attached to Broker Surface. |
| Overlay layer toggles | Chart / Settings | P1 | `.layer-controls`, `.layer-toggle`, `#layer-chart-bounds`, `#layer-recent-candles`, `#layer-major-swings`, `#layer-local-swings`, `#layer-supply-demand`, `#layer-trigger-zones`, `#layer-council-decision`, `#layer-historical-replay`, `#layer-broker-controls`, `#layer-diagnostics` | Keep as chart toolbar or layer menu. |
| Adaptive Inspector | Live / Diagnostics | P0/P1 | `#inspector-panel`, `#inspector-updated`, `#inspector-eyebrow`, `#inspector-title`, `#inspector-state`, `#inspector-summary`, `#inspector-rows` | Always available; content follows selected tile, study, history row, or overlay box. |
| Study Map | Chart / Council | P1 | `.matrix-module`, `.study-grid`, `#study-global-cell`, `#study-local-cell`, `#study-impulse-cell`, `#study-smc-cell`, `#study-sr-cell`, `#study-candles-cell`, `#study-candles` | Promote when structure conflicts with signal. |
| Focus State | Calibration | P1 | `.focus-module`, `.focus-stack`, `#focus-window-cell`, `#focus-timeframe-cell`, `#focus-selector-cell`, `#focus-execution-cell`, `#focus-error-cell` | Promote when focus is armed, pending, unmatched, or erroring. |
| Runtime Telemetry | Diagnostics | P1/P2 | `.telemetry-module`, `#telemetry-grid`, `#telemetry-updated`, `#telemetry-compute`, `#telemetry-queue`, `#telemetry-packet`, `#telemetry-cache`, `#telemetry-paper`, `#telemetry-path`, `#telemetry-reality`, `#telemetry-entry-quality`, `#telemetry-permission`, `#telemetry-avoided` | Promote on API error, stale packet, high latency, cache rejection, or model health warning. |
| Recent Studies | Replay | P1/P2 | `.history-module`, `#history-caption`, `#history-list`, `.history-item`, `.history-action`, `.history-copy`, `.history-meta` | Promote after capture completes or when user selects replay layer. |
| PhoenixGuard Read | Council | P1/P2 | `.report-module`, `#report-caption`, `#report-list`, `.report-item`, `.report-title`, `.report-body`, `.report-meta` | Promote when council needs narrative explanation. |
| Prediction Images | Simulation | P1/P2 | `.prediction-module`, `#prediction-gallery`, `#prediction-input-img`, `#prediction-memory-img`, `#prediction-overlay-img`, `#prediction-metadata`, `#prediction-signal`, `#prediction-conf`, `#prediction-gates`, `#prediction-memory` | Promote after `#predict-now` or `#show-future`. |
| Scenario Heatmap | Simulation | P2 | `#scenario-heatmap`, `.scenario-heatmap-empty`, `.scenario-heatmap-grid`, `.scenario-heatmap-row`, `.scenario-heatmap-cell`, `.scenario-heatmap-meta` | Promote when `#scenario-toggle` is on or scenario analysis exists. |
| Background scene stack | Visual support | P3 | `.scene-stack`, `.scene-frame`, `.scene-market`, `.scene-suite`, `.scene-alt`, `.scene-travel`, `.scene-shade`, `.grain` | Keep decorative; do not let it drive IA. |

## V4 First View Composition

1. Header with protected title, session chip, and live status.
2. Signal card plus compact session metric row.
3. Broker Surface with attached chart controls and layer menu.
4. Compact Decision Kernel strip.
5. Adaptive Inspector as the right-side/context panel.

## Secondary View Composition

- Council view: Decision Kernel, PhoenixGuard Read, market reality, permission, entry quality.
- Replay view: Recent Studies plus historical replay layer controls.
- Simulation view: Prediction Images, Scenario Heatmap, demo timing, scenario controls.
- Calibration view: Focus State, focus arm/clear, capture cadence, window/timeframe identity.
- Diagnostics view: Runtime Telemetry, latency/model health, errors, diagnostic overlay.
- Settings view: execution mode, gates, memory projection, timer behavior, view mode defaults.
