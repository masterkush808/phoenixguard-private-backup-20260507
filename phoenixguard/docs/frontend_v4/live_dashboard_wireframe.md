# PhoenixGuard Frontend V4 Live Dashboard Wireframe

Protected title text: `808Fx Standard Hybrid System`

This wireframe maps V4 layout zones to the current static DOM. It is documentation only; no dashboard code is changed.

## Desktop Wireframe

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 808 | Locked Broker Surface Tracker | PhoenixGuard engine                    │
│ 808Fx Standard Hybrid System                         Session | Live status   │
│ DOM: .topbar .brand-title #session-chip #live-dot #metric-status-mini        │
├──────────────────────────────────────────────────────────────────────────────┤
│ LIVE SIGNAL + SESSION METRICS                                                │
│ ┌─────────────────────┐ ┌───────────────────────────────┐ ┌───────────────┐ │
│ │ Signal Read          │ │ Current Thesis + pills        │ │ Status tiles  │ │
│ │ #signal-card         │ │ #signal-summary #signal-pills │ │ #metric-*     │ │
│ └─────────────────────┘ └───────────────────────────────┘ └───────────────┘ │
├──────────────────────────────────────────────────────────────────────────────┤
│ COUNCIL STRIP                                                                │
│ #kernel-story-card | #kernel-bias | #kernel-candle | #kernel-trigger | ...   │
├──────────────────────────────────────────────┬───────────────────────────────┤
│ CHART WORKSPACE                              │ INSPECTOR                     │
│ Broker Surface                               │ Adaptive Inspector            │
│ .surface-module                              │ #inspector-panel              │
│ #surface-stage                               │ #inspector-title              │
│ #surface-canvas                              │ #inspector-summary            │
│ #surface-overlay / #surface-raw              │ #inspector-rows               │
│ #hotspot-layer                               │                               │
│                                              │                               │
│ Toolbar: #zoom-* #pan-center #mode-*         │ Context follows selected      │
│ Layers: #layer-chart-bounds ...              │ metric, kernel, study,        │
│                                              │ history, or hotspot.          │
├──────────────────────────────────────────────┴───────────────────────────────┤
│ SECONDARY PANELS                                                             │
│ Study Map | Focus State | Runtime Telemetry | Recent Studies                 │
│ .matrix-module .focus-module .telemetry-module .history-module               │
├──────────────────────────────────────────────────────────────────────────────┤
│ EXPANDABLE WORKBENCH                                                         │
│ PhoenixGuard Read | Prediction Images | Scenario Heatmap | Settings          │
│ .report-module .prediction-module #scenario-heatmap .control-ribbon          │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Mobile / Narrow Wireframe

```text
┌────────────────────────────┐
│ 808Fx Standard Hybrid System│
│ Session chip | Live status  │
├────────────────────────────┤
│ Signal Read                 │
│ #signal-card                │
├────────────────────────────┤
│ Status / Next / Focus / Cap │
│ .metric-grid                │
├────────────────────────────┤
│ Broker Surface              │
│ #surface-stage              │
│ Layer menu + zoom controls  │
├────────────────────────────┤
│ Decision Kernel             │
│ #kernel-story-card          │
│ horizontal kernel tiles     │
├────────────────────────────┤
│ Inspector                   │
│ #inspector-panel            │
├────────────────────────────┤
│ Tabs: Replay | Simulation   │
│ Calibration | Diagnostics   │
│ Settings                    │
└────────────────────────────┘
```

## Interaction Notes

| Interaction | Current trigger | V4 placement |
| --- | --- | --- |
| Start/stop live tracking | `#tracker-toggle` -> `/start` or `/stop` | Sticky live control near header or bottom action bar. |
| Single capture | `#capture-now` -> `/capture-once` | Live action bar and Calibration view. |
| Arm focus | `#focus-arm` -> `/focus-region/arm` | Calibration view; promote when focus missing. |
| Clear focus | `#focus-clear` -> DELETE `/focus-region` | Calibration view. |
| Emergency stop | `#emergency-stop` -> `/emergency-stop` | Always visible, visually distinct. |
| Predict | `#predict-now` -> `/predict` | Simulation view. |
| Show future | `#show-future` -> `/show-future` | Simulation view. |
| Demo timing test | `#demo-trade` -> `/demo-random-trade` | Simulation or execution testing drawer. |
| Execution toggle | `#execution-toggle` -> PATCH `/controls` | Settings with live execution status mirrored in Live. |
| Layer visibility | `.layer-toggle` / `#layer-*` | Chart layer menu. |
| Overlay/raw mode | `#mode-overlay`, `#mode-raw` | Chart toolbar. |

## Bucket-To-Viewport Rules

| Bucket | Default visibility | Collapse behavior |
| --- | --- | --- |
| Live | Always visible. | Never fully hidden. |
| Council | Compact strip visible; full panel expandable. | Collapse clocks first, keep side/state. |
| Chart | Always visible. | On mobile, chart follows Live metrics before Council. |
| Replay | Hidden behind tab unless a new study completes. | Badge with latest count/status. |
| Simulation | Hidden behind tab unless prediction/future is requested. | Promote `#prediction-gallery` after action. |
| Calibration | Hidden unless focus is armed/missing/erroring. | Promote `#focus-error` and `#focus-window`. |
| Diagnostics | Hidden unless warning/error/stale telemetry occurs. | Promote `#telemetry-updated` and failing cell. |
| Settings | Hidden behind controls drawer. | Keep emergency and tracker toggle outside drawer. |

## Data Hydration

The dashboard should continue using the current session payload route as the primary state source:

- `GET /v1/mobile/window-tracker/sessions/{session_id}` hydrates Live, Council, Chart metadata, Replay, Simulation metadata, Calibration, Diagnostics, and Settings state.
- `GET /v1/mobile/model-council/health?session_id={session_id}` enriches Diagnostics and Council telemetry.
- Artifact URLs under `/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{artifact_kind}` hydrate Chart and Simulation images.
