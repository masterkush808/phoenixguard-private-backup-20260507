# PhoenixGuard Frontend V4 Overlay Modes

## Mode Matrix

| Mode | Purpose | Default layers | Hidden until requested |
| --- | --- | --- | --- |
| `active-context` | Live trading read. Minimal, current, side-aware context. | `chart_bounds`, `recent_candles`, `supply_demand`, `trigger_zones`, `active_council_decision`, compatible `broker_controls` | `major_swings`, `local_swings`, `historical_replay`, `diagnostics` |
| `full-structure` | Analyst view of current structure. | `chart_bounds`, `recent_candles`, `major_swings`, `local_swings`, `supply_demand`, `trigger_zones`, `active_council_decision` | `historical_replay`, `broker_controls`, `diagnostics` |
| `historical-replay` | Compare active setup against past structure. | full-structure plus `historical_replay` | `diagnostics`; broker controls unless execution panel is armed |
| `execution-lock` | Verify click targets and broker ownership. | `broker_controls`, `active_council_decision`, selected active-side `trigger_zones` | all nonessential structure |
| `diagnostics` | Geometry/truth-audit inspection. | all layers whose payload exists | none, but requires debug enablement |
| `raw` | Inspect raw capture. | compatible `broker_controls` only | chart overlays |

## Active-Context Default

`active-context` is the V4 default. The backend already applies a live default visibility pass:

- all objects start hidden except `chart_bounds`, `recent_candles`, and `active_council_decision`
- one nearest support/demand and one nearest resistance/supply are shown
- up to three active-side trigger/target objects are shown
- historical replay remains hidden

The frontend should honor `box.visible_default` in this mode. Manual layer toggles may reveal additional layers, but they should not change execution trust.

## Layer Semantics

### `chart_bounds`

Defines the chart plane. It may cover the full chart and is allowed to have a large area ratio. It is a reference frame, not a signal.

### `recent_candles`

Latest tracked candles. These are dynamic and should be cheap to re-render every refresh.

### `major_swings`

Global structure and long swing context. Hidden in active-context unless the user asks for full structure.

### `local_swings`

Current local impulse or micro structure. Useful for study, but too noisy for the default live view when trigger zones already summarize action.

### `supply_demand`

Support, resistance, demand, and supply bands. Backend refinement narrows these to reaction clusters and rejects oversized zones. In active-context, show only the nearest support-side and resistance-side rows selected by distance and confidence.

### `trigger_zones`

Sniper, trigger, target, and invalidation windows from projection or memory fit. Active-context should show only rows aligned with the active BUY/SELL side, plus neutral rows when direction is absent.

### `active_council_decision`

The current model council decision box. This is high priority and should appear above structural context. It is only created when active side is BUY or SELL and a current anchored structure exists.

### `historical_replay`

Past structure used for comparison. It must stay off by default so the live chart does not look more certain than it is.

### `broker_controls`

Execution controls and broker click targets. These are window-space objects. They may render in overlay or raw mode only when image dimensions match the locked broker surface.

### `diagnostics`

Debug-only geometry and truth-audit objects. Diagnostics should not render unless `overlay_geometry.debug_enabled` is true or a local developer toggle is active.

## Mode Switching Rules

Mode state and layer toggle state are separate:

- Changing mode changes the default visibility profile.
- Manual toggles apply after the mode profile.
- Returning to `active-context` should restore `visible_default` unless the user has explicitly pinned custom visibility.
- Diagnostics must not be silently carried into active-context.

Recommended state shape:

```json
{
  "mode": "active-context",
  "manual_layer_overrides": {
    "historical_replay": true
  },
  "pinned_custom_visibility": false
}
```

## Execution Safety

Overlay visibility is not execution permission. Execution decisions must read `overlay_geometry.truth_audit.valid_for_execution` or the execution packet's overlay truth state.

Frontend labels should reflect this distinction:

- live execution-read objects use solid/high-priority styling
- historical replay uses muted styling
- diagnostics uses warning/failure styling
- hidden labels must keep accessible names through `aria-label`
