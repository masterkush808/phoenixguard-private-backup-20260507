# PhoenixGuard Frontend V4 Overlay Renderer

## Scope

This document defines the frontend V4 renderer contract for chart overlays. It is based on the current dashboard renderer in `phoenixguard/mobile_api/static/window_tracker_dashboard.html` and the backend geometry producer in `phoenixguard/vision/overlay_geometry.py`. The static dashboard is not changed by this document.

The renderer must treat `tracking_summary.overlay_geometry` as the primary overlay source and `latest_signal.overlay_geometry` as the fallback. Legacy structures may still be rendered when `overlay_geometry.boxes` is absent, but V4 should prefer the normalized geometry payload.

## Coordinate Spaces

Overlay objects use one of two spaces:

| Space | Source | Meaning | Renderer mapping |
| --- | --- | --- | --- |
| `chart` | `overlay_geometry.boxes` | Box coordinates are relative to the chart capture plane. | Offset by `tracking.chart_region.pixel_bbox` or `tracking.display_region.pixel_bbox` when drawn on a full-window overlay. |
| `window` | broker execution controls | Box coordinates are relative to the locked broker window. | Draw only when the active image dimensions match `broker_surface.capture_plane`. |

Boxes with absolute pixel coordinates are mapped by image natural width/height. Boxes whose max absolute coordinate is `<= 1.0001` are treated as normalized `[x0, y0, x1, y1]`.

## Layer Stack

The canonical layer order is:

1. `chart_bounds`
2. `recent_candles`
3. `major_swings`
4. `local_swings`
5. `supply_demand`
6. `trigger_zones`
7. `active_council_decision`
8. `historical_replay`
9. `broker_controls`
10. `diagnostics`

Frontend V4 should render these as layered object groups, not as unrelated DOM boxes. Each object must carry:

- `key` or `id`
- `label`
- `layer`
- `bbox`
- optional `direction`, `role`, `kind`, `source`, `confidence`
- `visible_default`
- `structural_anchor` where available

`phoenixguard/vision/overlay_object_schema.py` provides a small typed helper for this payload shape.

## Render Modes

### `active-context`

Default V4 mode. Shows the objects needed to understand the current actionable chart context:

- `chart_bounds`
- `recent_candles`
- `active_council_decision`
- nearest support and nearest resistance selected by distance/confidence
- up to three trigger/target objects matching the active BUY/SELL side
- broker controls only when the active image is window-space compatible

This mode should use each object's `visible_default` as the first decision, then apply user layer toggles.

### `full-structure`

Shows all current chart structure except historical replay and diagnostics by default. This mode is for analysis, not execution.

### `historical-replay`

Adds `historical_replay` with reduced visual priority. Historical boxes must not be treated as live execution evidence unless the truth audit explicitly marks them decision-valid and the active kernel state refers to that history.

### `diagnostics`

Adds diagnostic objects and truth-audit failure hints. This mode is gated by `overlay_geometry.debug_enabled` or a local developer override.

### `raw`

Shows the raw capture, but may retain `broker_controls` for execution lock verification. Chart overlays should not render in raw mode.

## Static Layer Reuse

The current dashboard reuses static DOM nodes when `overlay_geometry.static_layer_hash`, static layer visibility, surface mode, and full-overlay state have not changed. V4 should keep that optimization.

Static layers:

- `chart_bounds`
- `major_swings`
- `supply_demand`
- `historical_replay`
- `broker_controls`

Dynamic layers:

- `recent_candles`
- `local_swings`
- `trigger_zones`
- `active_council_decision`
- `diagnostics`

The renderer budget from the backend is `overlay_geometry.render_budget_ms`; current tests expect it to stay at or below 16 ms.

## Clipping Requirements

The backend already clips object boxes to chart bounds and broker exclusion boxes. The frontend must still defensively clip final CSS percentages:

- `left` and `top`: clamp to `[0, 99.5]`
- `width` and `height`: clamp to `[1, 100]` for visible hotspots
- normalized boxes inside a focus region must be remapped to the focus region before percentage conversion
- broker controls must be skipped when the rendered image is a chart crop and not a full broker-window capture

Renderer clipping is display safety only. It must not override backend truth or execution validity.

## Label Collision Policy

Current labels are rendered inside `.surface-hotspot span`, so crowded zones can overlap. V4 should introduce a label placement pass before DOM insertion:

1. Build a candidate label box for each visible overlay object.
2. Prefer inside-top-left placement for boxes larger than the label.
3. For narrow candle/trigger boxes, place the label outside using anchors in this priority: `top`, `right`, `bottom`, `left`.
4. Clip candidate label boxes to chart bounds.
5. If the label overlaps a higher-priority label, abbreviate it.
6. If abbreviation still overlaps, hide the lower-priority label but keep the hotspot and `aria-label`.

Priority order:

1. `broker_controls`
2. `active_council_decision`
3. active-side `trigger_zones`
4. nearest `supply_demand`
5. `recent_candles`
6. `major_swings`
7. `local_swings`
8. `historical_replay`
9. `chart_bounds`
10. `diagnostics`

The simulation helper `phoenixguard/simulation/overlay_eval/label_clutter_metrics.py` should be used to measure label overlap before promoting the new renderer.
