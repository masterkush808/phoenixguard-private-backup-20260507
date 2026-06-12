# PhoenixGuard Frontend V4 Component Style Guide

## Visual North Star

PhoenixGuard V4 should feel like a futuristic command cockpit for live market interpretation: dark instrument glass, calibrated gold hierarchy, cyan telemetry, jade execution readiness, and restrained danger states. Keep the interface operational and dense. It should prioritize scan speed, current state, and precise action over marketing-style presentation.

Preserve the exact product title wherever the primary brand title appears:

```text
808Fx Standard Hybrid System
```

Do not rewrite it, abbreviate it, change casing, or add extra symbols inside the title text.

## Foundation Tokens

Use `assets/themes/phoenix_command_tokens.css` as the V4 token source. The file exposes `--pc-*` tokens for new cockpit surfaces and also maps key `--pg-*` variables so older PhoenixGuard components can adopt the same palette when the theme file is loaded.

Core theme roles:

| Role | Token | Usage |
| --- | --- | --- |
| App background | `--pc-bg` | Full viewport base behind every cockpit surface. |
| Console shell | `--pc-surface-console` | Main command surface, dashboard frame, live tracker shell. |
| Panels | `--pc-surface-panel` | Repeated modules, inspectors, metrics, report panels. |
| Elevated panels | `--pc-surface-raised` | Active inspector, selected prediction card, modal surfaces. |
| Primary text | `--pc-text` | Main readings, headings, strong labels. |
| Muted text | `--pc-text-muted` | Secondary copy, status notes, detail rows. |
| Dim text | `--pc-text-dim` | Overlines, inactive labels, low-priority metadata. |
| Gold command | `--pc-gold` | Brand title gradient, active mode, selected state, confidence fill. |
| Cyan telemetry | `--pc-cyan` | Motion traces, data overlays, telemetry emphasis. |
| Jade execution | `--pc-jade` | Buy/ready/connected/running states. |
| Red abort | `--pc-red` | Sell/error/stop/destructive states. |

Avoid one-note palettes. Gold should lead hierarchy, cyan should carry telemetry and spatial context, jade should communicate execution readiness or positive state, and red should be reserved for meaningful risk, sell, or failure.

## Layout System

Use cockpit density. The existing dashboard is a good baseline: top brand/status bar, signal deck, decision kernel, control ribbon, main surface, inspector, telemetry, history, and prediction modules.

Preferred layout rules:

- Use full-width command bands or grid regions for page sections.
- Use cards only for repeated modules, metric tiles, inspectors, modals, and framed tools.
- Do not nest cards inside cards.
- Keep radii tight: `--pc-radius-sm` to `--pc-radius-lg`; avoid pill-shaped containers except tiny live dots, progress tracks, or intentional badges.
- Preserve stable dimensions with grid tracks, `minmax(0, 1fr)`, fixed control heights, and explicit aspect ratios for image/canvas regions.
- Keep dense controls close to the surface they operate on.
- Let primary dashboard content appear immediately; do not introduce landing-page hero sections for V4 operational screens.

## Typography

Use the existing split between display and mono typography:

- Display: `--pc-font-display` for headings, high-value readings, and major dashboard labels.
- Mono: `--pc-font-mono` for overlines, metric labels, status chips, telemetry values, timestamps, session ids, and machine-state text.

Type scale guidance:

| Component | Size Token | Notes |
| --- | --- | --- |
| Brand title | `--pc-text-hero` | Use only for `808Fx Standard Hybrid System`. |
| Section headings | `--pc-text-xl` to `--pc-text-2xl` | Tight, readable, not oversized. |
| Metric values | `--pc-text-lg` to `--pc-text-2xl` | Use tabular or mono when numeric alignment matters. |
| Body copy | `--pc-text-sm` to `--pc-text-md` | Keep summaries scannable. |
| Overlines | `--pc-text-xs` | Uppercase mono with `--pc-letter-overline`. |

Do not use negative letter spacing. Keep `letter-spacing: 0` for normal text and use only the tokenized positive tracking for overlines and compact labels.

## Component Patterns

### Topbar and Brand Lockup

The topbar anchors the cockpit. It should include:

- A compact brand mark.
- The exact title text `808Fx Standard Hybrid System`.
- One operational subtitle, such as engine, tracker, or surface context.
- Session and live-status tokens aligned to the right on desktop and stacked naturally on mobile.

Use the brand title gradient from `--pc-gradient-brand`. Keep the title readable even when the background scene is active.

### Signal Primary Card

The signal card is the strongest decision read. It should:

- Use a large action value such as BUY, SELL, or HOLD.
- Tie action color to semantic state: jade for buy/ready, red for sell/error, hold neutral, gold for active analysis.
- Include confidence as both text and a stable progress track.
- Use hover lift no greater than `--pc-lift-sm`.

### Metric Tiles

Metric tiles are compact and repeatable. Keep them predictable:

- Label in mono uppercase.
- Value large enough for glance reading.
- Note in muted text.
- Same padding and min-height within a grid.
- Use dividers between tiles inside grouped metrics.

Do not allow changing values to resize surrounding layout. Use `overflow-wrap: anywhere` for long statuses.

### Decision Kernel

The decision kernel should feel like a dedicated command instrument. Use it for scenario state, model council state, timing, and high-value reasoning. Keep the headline strong and pair it with a small state chip. Use tile groups for supporting kernel values rather than prose-heavy blocks.

### Control Ribbon

Controls should be compact and direct:

- Primary command: jade filled button.
- Active mode: gold outline/fill.
- Warning or destructive command: red text and border.
- Passive command: translucent panel button.
- Disabled command: reduced opacity with no hover lift.

Prefer icon buttons when an icon exists in the active frontend stack. When text buttons are required, keep labels short and exact.

### Surface Stage and Overlays

The chart or broker surface is the operational center:

- Use grid texture lightly; it should support alignment, not dominate.
- Keep image/canvas regions unambiguous and non-cropped unless the user chooses zoom/pan.
- Hotspots should use semantic overlay colors:
  - Gold: chart bounds, supply/demand, active focus.
  - Cyan: swings, motion, projection paths.
  - Jade: buy triggers, ready execution zones.
  - Red: sell triggers, failure or abort zones.
  - Neutral steel: diagnostics and passive bounds.
- Labels must not obscure the chart more than needed. Use compact mono tags with ellipsis.

### Inspector Module

The inspector should explain the selected object or current thesis:

- Title with strong display weight.
- State chip below title.
- Summary in readable body text.
- Detail rows with fixed label column on desktop and stacked rows on narrow viewports.
- Data-heavy rows must wrap safely.

### Prediction and Scenario Cards

Prediction cards need a strong visual region first, then metadata:

- Keep image areas stable and uncropped by default.
- Use gold pulse only for active/running prediction.
- Use cyan-to-jade gradients for scenario heatmap cells, with opacity representing magnitude.
- Keep metadata labels terse.

### History and Reports

History items and reports should remain low-chrome:

- Use dark panel background, thin borders, and muted meta text.
- Emphasize action words with semantic color only when useful.
- Avoid decorative badges unless they convey a real state.

## Responsive Behavior

Desktop:

- Multi-column cockpit grids are appropriate when every region remains readable.
- Preserve the chart/surface as the largest area.
- Keep control ribbons on one line when possible.

Tablet:

- Collapse major decks to single-column before content starts compressing.
- Keep metrics in two-column groups.
- Stack topbar session tokens beneath brand if needed.

Mobile:

- Use edge-to-edge console framing when space is tight.
- Collapse metrics and kernel tiles to one column under 560px.
- Let controls wrap into equal-width buttons.
- Keep the exact title visible and readable; do not replace it with an abbreviation.

## Accessibility

- Use `color-scheme: dark`.
- Maintain visible focus rings with gold or cyan outlines.
- Do not rely on color alone for status. Pair color with text labels such as Running, Error, Buy, Sell, Hold, Locked, or Awaiting.
- Respect `prefers-reduced-motion`.
- Keep touch targets at least 30px for compact tool controls and 38px for primary controls.
- Use `aria-label` on cockpit sections and icon-only controls.

## Implementation Notes

- New V4 code should import `assets/themes/phoenix_command_tokens.css` before component CSS.
- Prefer token values over one-off colors. If a one-off color is unavoidable, add a token first.
- Do not alter existing title copy in `phoenixguard/mobile_api/static/window_tracker_dashboard.html`; the current visible title already uses `808Fx Standard Hybrid System`.
- Keep CSS scoped with `.phoenix-command` or token variables when possible so older Gradio/theme assets are not accidentally restyled.
