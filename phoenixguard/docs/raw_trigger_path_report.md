# Raw Trigger Path Report

## V3 Rule

Live execution authority is restricted to `PG_EXECUTION_PACKET_V3`.

Raw fields such as `action`, `execution_action`, `actionable`, `entry_state`, `SNIPER_READY`,
`TRIGGER_READY`, memory scores, skill-gate pass/fail, ensemble consensus, and tracker lane selection
are evidence or diagnostics only.

## Paths Reviewed

| Path                                                                    | Legacy behavior                                                          | V3 outcome                                                                                                                                   |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `shooter.py` package reporter mode                                      | Could parse legacy observer/tracker signal fields in older builds        | Current process reads only Model Council execution packets and reports accepted allowance packages                                           |
| Legacy shooter signal parsing                                           | Parsed raw signal-shaped payloads in older builds                        | Retired from the active local shooter package reporter                                                                                       |
| `Backend/src/phoenixguard/mobile_api/window_tracker.py::_evaluate_broker_execution` | Could call `execution_backend.prepare_and_click(...)` from tracker lanes | Live mode now refuses internal clicks; missing/invalid packets return `blocked_by_runtime`, valid packets return `external_shooter_required` |
| `Backend/src/phoenixguard/mobile_api/window_tracker.py::execute_demo_random_trade`  | Could send random/manual demo clicks                                     | Now returns `blocked_by_runtime` and does not call the execution backend                                                                     |
| `phoenixguard/mobile_api/observer.py` latest-signal endpoint            | Exposes legacy `latest_signal` fields                                    | Diagnostic only; no V3 execution endpoint is exposed for observer raw signals                                                                |
| `Backend/src/phoenixguard/decision/ensemble.py` support gates                       | Support gates previously expected to block consensus in legacy tests     | Support gate failures remain visible as diagnostics; Model Council decides executability                                                     |
| `Backend/src/phoenixguard/decision/model_council_v3.py`                             | New V3 authority                                                         | Only mature, stable council decisions can publish executable packets                                                                         |

## Remaining Legacy Producers

Legacy producers still emit raw side/action fields for dashboards and historical compatibility:

- `main.py::run_inference`
- `phoenixguard/mobile_api/observer.py`
- tracker `latest_signal`
- ensemble and decision kernel diagnostics

These are not live execution authority. The V3 validator rejects raw signal payloads, and the
package reporter refuses handoff without an explicit accepted allowance package.

## Tests Covering Lockdown

- `Backend/tests/test_shooter_v3_runtime.py`
- `Backend/tests/test_execution_packet_schema_v3.py`
- `Backend/tests/test_window_tracker_model_council_v3.py`
- `Backend/tests/test_window_tracker_service.py`
- `test_trade_triggering_complete.py`
