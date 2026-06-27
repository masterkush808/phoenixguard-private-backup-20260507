# Realtime Sync Frame Skew Tolerance Report

Date: 2026-06-27

## Clear Answer

Frontend visual health could report `FAIL` during the 15-second live cadence even when overlays rendered correctly.

Observed condition:

```text
backend frame_id: 26
frontend frame_id: 24
overlay_count: 11 on both sides
overlay_state_version: matching
full broker surface visible: true
```

## Fix

`build_frontend_sync_status()` now allows a small frame skew only when:

```text
overlay count matches
overlay state version matches or one side has no version
frame skew <= 3
```

It still fails on:

```text
large frame skew
overlay count mismatch
overlay state version mismatch
chart transform mismatch
stale heartbeat
broker surface not visible
```

## Compact Artifact Fallback

The compact public live-state response does not always include the full `visual_health.artifacts`
block. Visual health now derives artifact health from compact live-state artifact paths or from
the frontend heartbeat proving a visible full broker surface with positive render dimensions.

## Validation

```text
compileall realtime sync files: PASS
pyright realtime sync files: PASS, 0 errors
test_realtime_sync_v3.py: PASS, 12 passed
```

## Runtime Impact

This does not loosen execution authority. It only prevents a false visual-health failure when the frontend is one or two display frames behind/ahead while rendering the same overlay truth.
