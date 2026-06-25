# PhoenixGuard Signal Producer Consumer Map

## Producers

| Producer | Current output | Main path | V3 status |
| --- | --- | --- | --- |
| `main.py::run_inference` | Legacy analysis dict with `action`, `execution_action`, `execution_permission`, `decision_kernel`, `memory_similarity` | Used by mobile API pipeline and observer | Contributor only under V3 |
| `SignalObserverService._build_signal_payload` | `latest_signal` with `action`, `base_action`, `candidate_action`, `execution_action`, `actionable`, freshness fields | `data/mobile_api/observer/sessions/<id>/session.json` | Must stop being execution authority |
| `ContinuousWindowTrackerService._capture_and_analyze_claimed` | Tracker `session.json` with `capture_count`, `frame_index`, `state_version`, `latest_signal`, artifacts, broker state, `model_council_result`, and optional `model_council_packet` | `data/mobile_api/window_tracker/sessions/<id>/session.json` | Feeds Model Council V3 packet publisher |
| `Backend/src/phoenixguard/runtime/model_council_daemon.py` | Prediction response from local ensemble runtime | `/status`, `/predict` | Needs V3 arbitration/publisher layer |
| `Backend/src/phoenixguard/decision/model_council_v3.py` | Council result and optional `execution_packet` / `model_council_packet` | In-process evaluator/publisher | Uses Agent 1 packet helpers |
| `Backend/src/phoenixguard/execution/packet_v3.py::build_execution_packet_v3` | `PG_EXECUTION_PACKET_V3` packet | New reusable support module | Ready for integration |

## Consumers

| Consumer | Input read today | Execution risk |
| --- | --- | --- |
| `shooter.py::fetch_latest_model_council_packet` | Model Council V3 packet endpoints | Live packet consumer |
| `shooter.py::parse_trade_signal` | Raw `execution_action`, `action`, `entry_state`, `actionable`, legacy tracker fields | Legacy helper/parser only; not the live signal-mode authority |
| `Backend/scripts_runtime/replay_signals.py` | Raw shooter parse payloads | Should be migrated to V3 packet replay |
| `window_tracker_dashboard.html` | Tracker session `latest_signal` | Diagnostic only under V3 |
| `Backend/tests/test_shooter_runtime.py` and `Backend/tests/test_shooter_parse.py` | Legacy shooter behavior | Must be updated by Agent 4 |
| `Backend/tests/test_execution_packet_schema_v3.py` | New V3 schema module | Agent 1 coverage |

## Legacy Execution Paths Found

- `shooter.py::parse_trade_signal` still accepts explicit `execution_action` and `actionable` payloads for legacy helper tests, but the live signal loop uses `fetch_latest_model_council_packet`.
- `Backend/src/phoenixguard/execution/governor.py::validate_fire_command` still treats `SNIPER_READY`, `TRIGGER_READY`, `READY_TO_FIRE`, and similar states as armed.
- `Backend/src/phoenixguard/decision/decision_kernel.py` promotes state when `entry_state` is `TRIGGERED`, `TRIGGER_READY`, or `SNIPER_READY`, or when `execution_action` equals dominant side.
- `phoenixguard/mobile_api/observer.py` renders stale or insufficient observer signals to `HOLD`, but still exposes `execution_action` and `actionable`.
- `Backend/src/phoenixguard/mobile_api/window_tracker.py` still builds diagnostic execution lanes and mutates `latest_signal["expiry_seconds"]`, `latest_signal["execution_lane"]`, and `latest_signal["execution_timing"]`; in `execution_mode=live` it now refuses internal clicks and defers valid V3 packets to the standalone shooter.

## V3 Required Consumer Rule

Live execution consumers must call `validate_execution_packet_v3` and accept only:

- `schema_version == PG_EXECUTION_PACKET_V3`
- `execution.enabled is True`
- `execution.state == EXECUTABLE`
- `execution.side == model_council.final_side`
- `runtime_model_health.all_required_models_awake is True`
- runtime integrity category passes
- second live read and discipline gate pass outside schema validation
