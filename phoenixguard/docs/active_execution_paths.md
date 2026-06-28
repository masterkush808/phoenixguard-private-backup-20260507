# Active Execution Paths

## BFS Execution Graph

| Node                                 | File                                        | Function                                                   | Output                                          | Consumer                      | Failure Observed                                            | Patch                                                                           |
| ------------------------------------ | ------------------------------------------- | ---------------------------------------------------------- | ----------------------------------------------- | ----------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Frame Capture                        | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | worker session capture loop                                | session payload, frame/capture counters         | Tracker Study                 | API crash made all downstream endpoints refuse connection   | launcher health guard already restarts tracker child                            |
| Tracker Study                        | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | `_publish_model_council_v3_state`                          | `latest_signal`, `tracking_summary`             | Model Council state endpoints | study data existed but shooter only saw `packet_id=null`    | study packet stored on session payload                                          |
| Model Council                        | `Backend/src/phoenixguard/decision/model_council_v3.py` | `ModelCouncilV3.evaluate`                                  | council state, promotion trace, candidate queue | packet publisher              | WATCHING/PREPARING was invisible to shooter endpoint        | `STUDY_PACKET` now includes packet id/type/score/next-required                  |
| Market Reality / Entry / Trap / Path | `Backend/src/phoenixguard/decision/model_council_v3.py` | evaluation scoring block                                   | `final_execution_score`, `reality_adjustments`  | promotion logic               | trigger readiness was not summarized as one execution score | final score and threshold added to result/study/execution packet                |
| Candidate Promotion                  | `Backend/src/phoenixguard/decision/model_council_v3.py` | promotion ladder                                           | WATCHING, PREPARING, EXECUTABLE                 | packet builder                | raw-side noise could hide why promotion stalled             | promotion trace exposes candidate side/stage/flips/blocker                      |
| Study Packet Publisher               | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | `latest_model_council_study_packet`                        | latest `STUDY_PACKET`                           | diagnostics/UI                | no dedicated endpoint for non-executable packet visibility  | `/study/latest` endpoints added                                                 |
| Execution Packet Publisher           | `Backend/src/phoenixguard/mobile_api/window_tracker.py` | `latest_model_council_packet`                              | `PG_EXECUTION_PACKET_V3`                        | shooter packet reader         | endpoint correctly returns only executable packets          | unchanged; execution/latest remains executable-only                             |
| Shooter Package Reporter             | `shooter.py`                                | `review_allowed_package`, `publish_allowed_package_report` | accepted allowance-package handshake            | MT4/external bridge handoff   | legacy shooter could imply local click authority            | local click path retired; reporter writes only accepted package handshakes      |
| MT4 File Bridge                      | `Backend/tools/phoenixguard_mt4_file_bridge.py`     | `_compact_allowance_package`, `_validate_command`          | `PG_MT4_EXECUTION_COMMAND_V1`                   | MT4 EA                        | inferred packages could look executable                     | bridge rejects inferred, missing, non-accepted, or non-ready allowance packages |
| Floating State                       | `Backend/src/phoenixguard/mobile_api/app.py`            | `_latest_shooter_handshake_or_waiting`                     | operator package-reporter status                | user                          | package absence could look like an error                    | waiting state now reports package reporter status without implying a click      |

## Diagnostic Command

```powershell
.\.venv\Scripts\python.exe Backend\tools\diagnose_v3_execution_path.py --session pocket-live-8788 --base-url http://127.0.0.1:8793
```

Use this while both processes are running. The deciding comparison is:

| Tracker        | Council            | Study Packet   | Execution Packet | Meaning                          |
| -------------- | ------------------ | -------------- | ---------------- | -------------------------------- |
| current        | WATCHING/PREPARING | present        | missing          | council has not promoted yet     |
| current        | EXECUTABLE         | present        | missing          | publisher/endpoint wiring fault  |
| current        | EXECUTABLE         | present        | present          | package reporter should validate the allowance package |
| endpoint error | endpoint error     | endpoint error | endpoint error   | PhoenixGuard API process is down |
