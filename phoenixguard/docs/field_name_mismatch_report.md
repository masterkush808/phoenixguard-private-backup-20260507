# Field Name Mismatch Report

## Critical Mismatches

| Legacy field | V3 field | Current risk | Required migration |
| --- | --- | --- | --- |
| `action` | `model_council.final_side` plus `execution.side` | Too broad; used for display, bias, and possible execution | Treat as contributor/display only |
| `execution_action` | `execution.side` | Existing live paths can parse it as authority | Must not execute without full V3 packet |
| `actionable` | `execution.enabled` | Boolean gate is not enough for V3 | Replace with Model Council executable packet |
| `execution_permission == EXECUTE` | `execution.state == EXECUTABLE` | Permission can come from ensemble/skill gates | Use only council final state |
| `entry_state == SNIPER_READY` | `model_council.maturity_stage == EXECUTABLE_PACKET` | Legacy armed state still appears in decision kernel/governor/shooter tests | Remove live authority |
| `side`, `candidate_action`, `model_action`, `best_play_action` | `execution.side` | Multiple possible side sources can disagree | V3 validator requires `execution.side == model_council.final_side` |
| `CALL` / `PUT` | `BUY` / `SELL` only | Legacy shooter aliases options terms | V3 rejects CALL/PUT |
| `market` | `symbol` | Observer uses market strings | Packet publisher must normalize to `symbol` |
| `focus_timeframe`, `detected_timeframe` | `timeframe` | Tracker/observer may expose several timeframes | Packet publisher must select one authoritative timeframe |
| `frame_index` | `frame_id` | Tracker increments `frame_index` | Packet publisher must map to `frame_id` |
| `published_epoch`, `completed_epoch` | `created_epoch` | Legacy timestamps vary by surface | Publisher must set one packet creation epoch |
| `signal_age_sec`, `freshness_score` | `live_integrity.packet_age_ms`, `valid_until_epoch` | Legacy freshness is advisory | Runtime integrity must reject stale packets |
| `expiry_seconds`, `required_seconds`, broker expiry | `execution.expiry_seconds` and `time_sequence.target_seconds` | Multiple expiry sources can disagree | V3 requires matching execution/time sequence |
| `source=tracker` | `live_integrity.source=model_council` | Shooter must not trust tracker source | V3 rejects non-council source |

## Echo Effects To Check In Later Agents

- `shooter.py` still contains legacy side resolution and raw signal parsing for compatibility helpers, but the live signal-mode authority is the V3 Model Council packet path.
- `phoenixguard/decision/decision_kernel.py` still treats `SNIPER_READY` and `execution_action` as state-machine input. Agent 2 must downgrade these to evidence.
- `phoenixguard/decision/ensemble.py` emits `execution_permission: EXECUTE`. Agent 2 must make this diagnostic only.
- `phoenixguard/mobile_api/observer.py` emits `actionable`, `execution_action`, and `status=ready/armed/watch`. Agent 2/5 must publish V3 separately.
- `phoenixguard/mobile_api/window_tracker.py` mutates timing and execution lane fields inside `latest_signal` for diagnostics. Final live timing now belongs to the Model Council packet, and tracker live mode refuses internal clicks.

## Agent 1 Guardrail Added

`validate_execution_packet_v3` refuses to resolve side from legacy aliases or raw payload fields. The side resolver is strict:

- Accepted: `BUY`, `SELL`
- Rejected: `CALL`, `PUT`, `UP`, `DOWN`, raw `action`, raw `execution_action`
