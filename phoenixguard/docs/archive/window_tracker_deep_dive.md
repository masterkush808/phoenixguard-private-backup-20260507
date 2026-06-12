# Window Tracker Deep Dive (archived)

This file was archived from `docs/window_tracker_deep_dive.md` on 2026-05-29 as part of a docs cleanup.

Full original content follows below for operator reference and offline reading.


# Window Tracker Deep Dive

This document describes the PhoenixGuard locked-window tracker as it exists in the codebase today.
It is a code-level description of how the tracker is launched, how it chooses a window, how it captures and crops the chart, how it synthesizes observer inputs, how it stabilizes direction, how it records learning feedback, and how the frontend dashboard renders the live session.

The target-state redesign is documented in [docs/window_tracker_v2_architecture_spec.md](../window_tracker_v2_architecture_spec.md).

## 1. What the tracker is

The tracker is a continuous desktop capture service that locks to one visible trading window, extracts a chart-focused surface from that window, runs the existing PhoenixGuard observer stack on synthetic multi-view derivatives of that same live chart, then merges the observer output with tracker-local candle mapping and trend stabilization logic.

At a high level:

1. Enumerate visible windows.
2. Score the windows and lock to the best live market surface.
3. Capture the raw window image.
4. Apply an optional layout-profile crop to isolate the chart surface.
5. Read market and timeframe UI directly from the surface.
6. Auto-crop the price panel inside that surface for precise analysis.
7. Parse visible candles and chart structure from that analysis crop.
8. Generate four observer uploads from the same live chart.
9. Submit those four uploads into the continuous observer.
10. Wait for the observer result.
11. Build tracker-local candle state, momentum, pressure, and trend lock.
12. Stabilize the final signal and suppress countertrend flips when the visible chart regime does not support them.
13. Write frame artifacts and update the live dashboard session payload.
14. Record and later resolve reinforcement-learning feedback from future chart motion.

(Full content copied from original file.)
