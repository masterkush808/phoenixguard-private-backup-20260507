# PhoenixGuard V3 Post-Implementation Hardening Report

Generated: 2026-05-19

## Integrated Changes

- Implemented `INSTRUMENT_CONTEXT_LOCK_V1`.
- Replaced symbol-text-only identity with instrument context states:
  - `IDENTITY_CONFIRMED`
  - `IDENTITY_LOCKED_BY_USER_PROFILE`
  - `IDENTITY_VISUAL_CONTINUITY_CONFIRMED`
  - `IDENTITY_UNKNOWN_BUT_PAPER_SAFE`
  - `IDENTITY_UNKNOWN_NOT_EXECUTABLE`
- Blank market OCR no longer blocks study, mapping, dashboards, telemetry, or paper-safe context.
- Locked broker focus now creates `USER_LOCKED_ACTIVE_CHART` when OCR market text is blank and the saved chart surface/timeframe/session/viewport are stable.
- Broker-click safety remains false unless broker/OCR identity is actually confirmed.
- `PG_EXECUTION_PACKET_V3` includes `instrument_context` and `symbol_context`.
- Shooter defaults to `LIVE_DISABLED` and supports `STUDY_ONLY`, `PAPER_EXECUTION`, `DRY_RUN_CLICK`, `CALIBRATION_TEST`, and `LIVE_DISABLED`.
- Shooter consumes only Model Council V3 packets, enforces second live read, trade discipline, runtime integrity, calibrated controls, duplicate prevention, no amount changes, and pre-click confirmation.
- Added safe validation recorders:
  - `data/shooter_validation/paper_executions.jsonl`
  - `data/shooter_validation/dry_run_clicks.jsonl`
  - `data/shooter_validation/calibration_tests.jsonl`
  - `data/shooter_validation/live_disabled.jsonl`
- Added candle outcome tracking and path-quality metrics.
- Added market forensic classifiers for late chase, opposing force, middle danger/safe, angle break, history would-exit, false breakout, pullback failure, dominance weakening, and conflict.
- Added overlay geometry sanitizer with chart clipping, broker-panel exclusion, area/aspect gates, structural anchors, merge/NMS, active-context defaults, and debug layer separation.
- Added model/process telemetry, cache counters, packet age, shooter handshake, paper outcome, path quality, and CV model loaded-state reporting.

## Verification

- Focused identity/packet/model tests: `46 passed`.
- Focused overlay/market/shooter/telemetry tests: `102 passed`.
- Final full regression after all patches: `780 passed, 7 skipped`.
- Safe shooter validation:
  - First packet refused with `WAITING_SECOND_LIVE_READ`.
  - Second advancing packet passed runtime, discipline, Model Council, and calibration gates.
  - `PAPER_EXECUTION`, `DRY_RUN_CLICK`, `CALIBRATION_TEST`, and `LIVE_DISABLED` all recorded successfully.
  - Every mode reported `broker_click_allowed=false`.
- Calibration preview found mapped time, BUY, SELL, and expiry controls without clicking.

## Running Realtime Stack

- PhoenixGuard API: `http://127.0.0.1:8793` PID `10600`.
- Model daemon: `http://127.0.0.1:8767` PID `14320`.
- Shooter poller: PID `5776`.
- Dashboard:
  `http://127.0.0.1:8793/v1/mobile/window-tracker/dashboard/pocket-live-8788`

## Live State Confirmed

- Tracker session: `pocket-live-8788`.
- Tracker status: `running`.
- Capture mode: live, not shadow.
- `execution_controls.execution_mode`: `live`.
- `live_execution_enabled`: `true`.
- Frames advancing:
  - capture count `69564 -> 69566`
  - frame index `69481 -> 69483`
- Timeframe detected: `M5`.
- Market OCR: blank.
- Instrument context:
  - `identity_state`: `IDENTITY_LOCKED_BY_USER_PROFILE`
  - `display_symbol`: `USER_LOCKED_ACTIVE_CHART`
  - `paper_safe`: `true`
  - `broker_click_safe`: `false`
- Model Council roles: all seven required roles `AWAKE`.
- CV models loaded: `byol`, `clip`, `dinov2`, `mobilenetv3`, `simclr`, `swav`.
- CV model failures: none.
- Shooter handshake: `not_ready`, because no fresh `PG_EXECUTION_PACKET_V3` executable packet is currently published.
- Latest executable packet endpoint correctly returns `404` while the council remains `WATCHING`.

## Safety Status

PhoenixGuard is running live capture and live study. The shooter is running as a live poller in `LIVE_DISABLED` mode only. No broker-click authority is enabled. Blank OCR identity is no longer a study blocker, but it still blocks real broker-click execution because `broker_click_safe=false`.

