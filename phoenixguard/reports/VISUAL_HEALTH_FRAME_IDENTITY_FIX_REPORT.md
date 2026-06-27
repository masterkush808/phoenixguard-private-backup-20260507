# Visual Health Frame Identity Fix Report

Date: 2026-06-27

## Clear Answer

The frontend was rendering overlays correctly, but `/v1/mobile/visual/health/v3/{session_id}` could still report `FAIL`.

Root cause:

```text
_apply_display_snapshot_to_projected_payload()
  wrote the current display frame_id
then _apply_compact_overlay_identity()
  overwrote top-level frame_id with the older overlay object frame_id
```

That made visual health compare:

```text
backend frame_id = old overlay object frame
frontend frame_id = current displayed broker frame
```

## Fix

Compact overlay identity no longer writes top-level `frame_id`.

Top-level `frame_id` remains the current display frame. Overlay authority remains available through overlay object fields and frame-specific overlay metadata.

## Validation

```text
compileall changed files: PASS
pyright changed files: PASS, 0 errors
compact/live-state tests: PASS, 11 passed
```

## Runtime Impact

This fix prevents false visual-health mismatches while preserving the overlay authority model. It does not change model council logic, packet authority, shooter reporter behavior, MT4 bridge behavior, broker calibration, or overlay geometry.
