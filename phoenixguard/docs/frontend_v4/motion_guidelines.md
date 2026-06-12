# PhoenixGuard Frontend V4 Motion Guidelines

## Motion Principle

Motion in PhoenixGuard V4 should feel like cockpit instrumentation: fast, precise, subtle, and state-driven. Animation should confirm system changes, reveal hierarchy, or indicate live processing. Avoid decorative loops that compete with chart reading or decision review.

## Motion Tokens

Use the motion tokens from `assets/themes/phoenix_command_tokens.css`:

| Token | Value | Usage |
| --- | --- | --- |
| `--pc-duration-instant` | 90ms | Pressed states, quick color changes. |
| `--pc-duration-fast` | 150ms | Hover lift, icon/control feedback. |
| `--pc-duration-base` | 220ms | Panel state changes, selection changes. |
| `--pc-duration-slow` | 360ms | Console or module entrance. |
| `--pc-duration-scene` | 36000ms | Slow background scene cycle only. |
| `--pc-ease-standard` | cubic-bezier(0.2, 0, 0, 1) | Default UI transition. |
| `--pc-ease-emphasized` | cubic-bezier(0.16, 1, 0.3, 1) | Entrance and large surface transitions. |
| `--pc-ease-linear` | linear | Continuous loops and progress motion. |

Keep hover translations to `--pc-lift-sm` or `--pc-lift-md`. Larger movement should be reserved for modal entrance or major view changes.

## Approved Motion Patterns

### Console Entrance

Use a single entrance on initial load:

- Duration: `--pc-duration-slow`.
- Transform: translateY(8px to 10px) into position.
- Opacity: 0 to 1.
- Easing: `--pc-ease-emphasized`.

Do not stagger every dashboard tile on live pages. The user should be able to scan immediately.

### Hover Lift

Use on clickable tiles, metric cells, prediction cards, and compact controls:

- Duration: `--pc-duration-fast`.
- Transform: `translateY(var(--pc-lift-sm))`.
- Border should brighten before the background becomes visually heavy.
- Avoid scaling whole cards except tiny hotspots.

### Active Live Pulse

Use only for live dots, running prediction markers, and critical processing states:

- Duration: 900ms to 1400ms.
- Opacity pulse only.
- Optional glow should be low alpha and tokenized.
- Never pulse large panels, long labels, or the brand title.

### Confidence and Progress Fill

Use width transitions for confidence bars, progress tracks, and scenario strength:

- Duration: `--pc-duration-base` to 280ms.
- Easing: `--pc-ease-standard`.
- Track must have a stable height and no layout shift.

### Background Scene Cycle

Background scene motion may be slow and atmospheric if it stays behind the cockpit:

- Cycle duration: `--pc-duration-scene`.
- Low opacity.
- Minimal pan/scale drift.
- No flicker or high-contrast flashes.
- Foreground readability wins over scene visibility.

### Hotspot Feedback

Hotspots on chart or broker surfaces may use:

- Border-color transition.
- Background alpha increase.
- Scale up to 1.01.
- Compact label reveal or emphasis.

Hotspot motion must not shift the underlying image or alter geometry.

## Restricted Motion

Avoid these patterns in V4 operational screens:

- Bouncy easing for decision-critical controls.
- Large card scaling.
- Infinite shimmer on content that is not loading.
- Global transitions on every property.
- Looping animation on large text, panels, or metric values.
- Decorative orbiting elements, bokeh blobs, or background objects.
- Motion that changes chart coordinate perception.

## State-Specific Motion

| State | Motion Guidance |
| --- | --- |
| Awaiting | Static or very low-contrast idle state. |
| Running | Live dot pulse, optional running marker pulse. |
| Locked | Border emphasis, no loop required. |
| Buy/Ready | Jade color change with fast transition. |
| Sell/Risk | Red color change; avoid playful movement. |
| Error | Static red state or one brief shake on direct user input failure. |
| Loading | Progress bar or skeleton shimmer only while waiting. |

## Reduced Motion

Always include a reduced-motion path:

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 1ms !important;
    scroll-behavior: auto !important;
  }
}
```

For long-running live indicators, reduced motion should keep the final state visible without repeating animation. A running status can remain jade or gold without pulsing.

## Timing Recommendations

- Hover: 120ms to 170ms.
- Pressed: 80ms to 100ms.
- Focus ring: immediate or 90ms.
- Selection changes: 160ms to 220ms.
- Metric value updates: color/opacity only, 150ms to 220ms.
- Panel entrance: 300ms to 380ms.
- Toast entrance: 220ms to 300ms.
- Background scene cycle: 30s to 45s.

## Motion QA Checklist

- No animation blocks chart inspection.
- No text overlaps or shifts during state changes.
- Controls do not resize on hover, active, disabled, or loading states.
- Reduced motion disables loops and long transitions.
- Motion communicates state instead of decoration.
- Live cockpit motion remains readable on desktop, tablet, and mobile.
