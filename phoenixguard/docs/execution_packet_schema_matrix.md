# PG_EXECUTION_PACKET_V3 Schema Matrix

Agent 1 implementation: `Backend/src/phoenixguard/execution/packet_v3.py`.

| Field | Required | Validator behavior | Legacy mismatch |
| --- | --- | --- | --- |
| `schema_version` | Yes | Must equal `PG_EXECUTION_PACKET_V3` | Raw `action` or `execution_action` payloads rejected |
| `packet_id` | Yes | Non-empty string | Legacy uses `signal_id` |
| `session_id` | Yes | Non-empty; optional expected-session match | Legacy observer and tracker both use this but not always packet-scoped |
| `symbol` | Yes | Non-empty; optional expected-symbol match | Legacy often uses `market` or detected market |
| `timeframe` | Yes | Non-empty; optional expected-timeframe match | Legacy often uses `focus_timeframe` or `detected_timeframe` |
| `frame_id` | Yes | Numeric; must advance when previous identity supplied | Legacy tracker uses `frame_index` |
| `capture_count` | Yes | Numeric; must advance when previous identity supplied | Present in tracker session |
| `state_version` | Yes | Numeric; must advance when previous identity supplied | Derived in tracker normalization |
| `created_epoch` | Yes | Positive epoch | Legacy uses `published_epoch`, `completed_epoch`, `timestamp` |
| `valid_until_epoch` | Yes | Must be in future | Legacy latest signal computes it opportunistically |
| `live_integrity.is_live` | Yes | Must be true | Must remain `RUNTIME_INTEGRITY` |
| `live_integrity.frame_advancing` | Yes | Must be true | Not a market blocker |
| `live_integrity.capture_advancing` | Yes | Must be true | Not a market blocker |
| `live_integrity.state_advancing` | Yes | Must be true | Not a market blocker |
| `live_integrity.source` | Yes | Must be `model_council` | Legacy uses `tracker` or observer surface |
| `live_integrity.cache_status` | Yes | Must be `fresh` | Old cache states rejected |
| `live_integrity.input_frame_hash` | Yes | Non-empty | Legacy screenshots may use artifact signatures |
| `execution.enabled` | Yes | Must be true for executable validation | Legacy uses `actionable` |
| `execution.state` | Yes | Must be `EXECUTABLE` | Legacy uses `ready`, `armed`, `SNIPER_READY`, `EXECUTE` |
| `execution.side` | Yes | Strict `BUY` or `SELL`; CALL/PUT rejected | Shooter legacy accepted aliases |
| `execution.expiry_seconds` | Yes | Must match `time_sequence.target_seconds` | Legacy uses top-level `expiry_seconds`, `required_seconds`, broker state |
| `execution.amount_action` | Yes | Must be `DO_NOT_CHANGE_AMOUNT` | Shooter already logs amount preserve |
| `execution.time_sequence` | Yes | Must include mode, target text/seconds, and steps | Legacy shooter derives time |
| `model_council.final_state` | Yes | Must be `EXECUTABLE` for live validation | Legacy has `decision_state`, `entry_state` |
| `model_council.final_side` | Yes | Must match execution side | Legacy has `dominant_side`, `candidate_action` |
| `runtime_model_health.all_required_models_awake` | Yes | Must be true | Also surfaced by `/v1/mobile/model-council/health` |
| `block_reason` | Optional | May be null for executable packet | Legacy stores many block strings |

## Validator Result Contract

- `PacketValidationResult.accepted`: no schema or runtime issues.
- `PacketValidationResult.runtime_integrity`: `PASS` or `FAIL`.
- Runtime freshness, identity, cache, live-state, and model-awake failures are categorized as `RUNTIME_INTEGRITY`.
- Schema and raw-signal failures are not categorized as `MARKET_BLOCKER`.

## Tests Added

- `test_execution_packet_schema_v3_valid`
- `test_side_field_resolution_consistent`
- `test_expiry_field_resolution_consistent`
- `test_session_mismatch_rejected`
- `test_frame_id_must_advance_for_live_packet`
- `test_old_schema_packet_rejected`
- `test_raw_signal_not_executable_packet`
- Extra coverage for CALL/PUT rejection, missing final side, and stale valid-until.
