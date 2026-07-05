# Final MT4 Bridge Contract Audit - 2026-07-05

## Summary

PhoenixGuard V3 playbook package production is certified at the runtime-contract level, not at the profit level. The tracker, API, live freshness guard, playbook final decider, and MT4 common-file writer produced fresh executable packages with professional trade-plan evidence.

The remaining certification gap was the MT4 consumer boundary: the common-file bridge wrote executable commands, but the EA audit/state files did not show a matching accepted packet during the noon certification window.

## Findings

- The active bridge file `mt4_execution_command.json` was fresh and contained `PG_MT4_EXECUTION_COMMAND_V1`.
- The latest observed command carried `PLAYBOOK_FINAL_DECIDER_V3`, `PG_EXECUTION_PACKET_V3`, `INTRADAY_ENTER_NOW`, a fresh live-integrity pass, and a professional expected move horizon.
- The EA audit/state files were stale compared with the bridge command file, indicating the EA side was not proven to be consuming the fresh commands.
- The full local launcher was letting the MT4 bridge inherit the shooter poll interval, slowing MT4 command publication.
- The EA did not normalize `SWING_ENTER_NOW`, even though the PhoenixGuard packet contract allows it.
- The EA intraday package management still defaulted to a 12-minute maximum hold, conflicting with the playbook's professional 60-90 minute expected move horizon.
- The EA duplicate guard marked a packet as seen before safety and order submission completed, which could suppress retry after temporary spread, margin, or trade-context blocks.

## Fixes Applied

- `Backend/tools/phoenixguard_mt4_file_bridge.py`
  - Added MT4 symbol/timeframe overrides via CLI and environment:
    - `--symbol-override` / `PHOENIXGUARD_MT4_SYMBOL`
    - `--timeframe-override` / `PHOENIXGUARD_MT4_TIMEFRAME`
  - Keeps bridge-fresh `created_epoch_sec` while preserving source packet time as `source_created_epoch_sec`.

- `Backend/launch/start_phoenixguard_full_local.ps1`
  - MT4 bridge now defaults to a 1-second poll and 20-second HTTP timeout.
  - MT4 bridge no longer inherits the slower shooter poll interval.
  - Passes optional symbol/timeframe overrides into the bridge.

- `Backend/launch/mt4/PhoenixGuard_MT4_Executioner.mq4`
  - Normalizes `SWING_ENTER_NOW` as a valid swing package.
  - Removes the old 12-minute forced intraday max hold by defaulting `InpIntradayMaxHoldMinutes` to `0`.
  - Stops suppressing packet retries after temporary safety/order blocks; only accepted packet IDs are treated as already processed.

- `Backend/tests/test_mt4_file_bridge.py`
  - Added coverage for symbol/timeframe override.
  - Added coverage for current EA package normalization, professional hold default, and retry-safe duplicate handling.

## Validation

- `python -m compileall -q Backend/tools/phoenixguard_mt4_file_bridge.py Backend/tests/test_mt4_file_bridge.py`
- `pyright Backend/tools/phoenixguard_mt4_file_bridge.py Backend/tests/test_mt4_file_bridge.py`
- `PHOENIXGUARD_STRICT_REPO_VENV=0 python -m pytest -q Backend/tests/test_mt4_file_bridge.py`

Results:

- compileall: PASS
- pyright: 0 errors, 0 warnings
- pytest: 19 passed

Note: `.venv-live` currently does not include pytest, so targeted tests were run with the test interpreter and strict runtime guard disabled. The live venv compile check passed.

## Current Certification Boundary

Certified:

- Live PhoenixGuard package production.
- Playbook adaptation/performance contract.
- Stale/freshness gate behavior.
- MT4 common-file command writing.
- MT4 bridge Python contract.

Not certified yet:

- EA accepted-packet confirmation.
- Actual MT4 order placement.
- Profitability or long-run strategy edge.

To certify the remaining boundary, recompile/reload `PhoenixGuard_MT4_Executioner.mq4` in MT4 with the updated source and confirm:

- `InpAllowLiveExecution=true`
- `InpDryRun=false` for real execution, or `true` for dry-run acceptance proof
- The EA chart symbol matches the intended MT4 symbol, or `PHOENIXGUARD_MT4_SYMBOL` is set before launch
- `mt4_executioner_audit.csv` receives `ACCEPT_TRADE`
- `mt4_executioner_state.txt` records the accepted packet identity
