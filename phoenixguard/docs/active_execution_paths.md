# Active Execution Paths

## BFS Execution Graph

| Node                                 | File                                        | Function                                                   | Output                                          | Consumer                      | Failure Observed                                            | Patch                                                                           |
| ------------------------------------ | ------------------------------------------- | ---------------------------------------------------------- | ----------------------------------------------- | ----------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Frame Capture                        | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | worker session capture loop                                | session payload, frame/capture counters         | Tracker Study                 | API crash made all downstream endpoints refuse connection   | launcher health guard already restarts tracker child                            |
| Tracker Study                        | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | `_publish_model_council_v3_state`                          | `latest_signal`, `tracking_summary`             | Model Council state endpoints | study data existed but shooter only saw `packet_id=null`    | study packet stored on session payload                                          |
| Model Council Contributor Gate        | `Backend/src/phoenixguard/decision/model_council_v3.py` | `ModelCouncilV3.evaluate`                                  | freshness, model health, source lock, contributor evidence, promotion trace | Playbook / packet publisher | WATCHING/PREPARING was invisible to shooter endpoint        | `STUDY_PACKET` now includes packet id/type/score/next-required                  |
| Market Reality / Entry / Trap / Path | `Backend/src/phoenixguard/decision/model_council_v3.py` | evaluation scoring block                                   | `final_execution_score`, `reality_adjustments`, professional thesis inputs  | Playbook final decider        | trigger readiness was not summarized as one execution score | final score and threshold added to result/study/execution packet                |
| Playbook Final Decider                | `Backend/src/phoenixguard/decision/book_strategy_master_v3.py` | `evaluate_book_strategy_master_v3`                         | `ENTER_NOW`, `PREPARE`, `LATE_CHASE`, professional strategy read | packet builder                | raw/local candidate side could overrule big-picture thesis  | professional thesis resolution and grade-ready trade plan gate packet promotion |
| Study Packet Publisher               | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | `latest_model_council_study_packet`                        | latest `STUDY_PACKET`                           | diagnostics/UI                | no dedicated endpoint for non-executable packet visibility  | `/study/latest` endpoints added                                                 |
| Execution Packet Publisher           | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | `latest_model_council_packet`                              | `PG_EXECUTION_PACKET_V3`                        | shooter/MT4 packet readers    | endpoint correctly returns only executable packets          | execution/latest remains executable-only and requires playbook authority         |
| Shooter Package Reporter             | `shooter.py`                                | `review_allowed_package`, `publish_allowed_package_report` | accepted allowance-package handshake            | MT4/external bridge handoff   | legacy shooter could imply local click authority            | local click path retired; reporter writes only accepted package handshakes      |
| MT4 File Bridge                      | `Backend/tools/phoenixguard_mt4_file_bridge.py`     | `_compact_allowance_package`, `_validate_command`          | `PG_MT4_EXECUTION_COMMAND_V1`                   | MT4 EA                        | inferred packages could look executable                     | bridge rejects inferred, missing, non-accepted, non-ready, or non-professional allowance packages |
| Floating State                       | `Backend/src/phoenixguard/mobile_api/app.py`            | `_latest_shooter_handshake_or_waiting`                     | operator package-reporter status                | user                          | package absence could look like an error                    | waiting state now reports package reporter status without implying a click      |

## Diagnostic Command

```powershell
.\.venv\Scripts\python.exe Backend\tools\diagnose_v3_execution_path.py --session pocket-live-8788 --base-url http://127.0.0.1:8793
```

Use this while both processes are running. The deciding comparison is:

| Tracker        | Playbook/Council State | Study Packet | Execution Packet | Meaning |
| -------------- | ---------------------- | ------------ | ---------------- | ------- |
| current        | WATCHING/PREPARING     | present      | missing          | playbook has not authorized `ENTER_NOW` or runtime contract is still waiting |
| current        | ENTER_NOW/EXECUTABLE   | present      | missing          | publisher/endpoint wiring fault or packet validator rejected current truth |
| current        | ENTER_NOW/EXECUTABLE   | present      | present          | package reporter or MT4 bridge should validate the allowance package |
| endpoint error | endpoint error         | endpoint error | endpoint error | PhoenixGuard API process is down |
