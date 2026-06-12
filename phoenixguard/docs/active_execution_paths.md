# Active Execution Paths

## BFS Execution Graph

| Node | File | Function | Output | Consumer | Failure Observed | Patch |
| --- | --- | --- | --- | --- | --- | --- |
| Frame Capture | `phoenixguard/mobile_api/window_tracker.py` | worker session capture loop | session payload, frame/capture counters | Tracker Study | API crash made all downstream endpoints refuse connection | launcher health guard already restarts tracker child |
| Tracker Study | `phoenixguard/mobile_api/window_tracker.py` | `_publish_model_council_v3_state` | `latest_signal`, `tracking_summary` | Model Council state endpoints | study data existed but shooter only saw `packet_id=null` | study packet stored on session payload |
| Model Council | `phoenixguard/decision/model_council_v3.py` | `ModelCouncilV3.evaluate` | council state, promotion trace, candidate queue | packet publisher | WATCHING/PREPARING was invisible to shooter endpoint | `STUDY_PACKET` now includes packet id/type/score/next-required |
| Market Reality / Entry / Trap / Path | `phoenixguard/decision/model_council_v3.py` | evaluation scoring block | `final_execution_score`, `reality_adjustments` | promotion logic | trigger readiness was not summarized as one execution score | final score and threshold added to result/study/execution packet |
| Candidate Promotion | `phoenixguard/decision/model_council_v3.py` | promotion ladder | WATCHING, PREPARING, EXECUTABLE | packet builder | raw-side noise could hide why promotion stalled | promotion trace exposes candidate side/stage/flips/blocker |
| Study Packet Publisher | `phoenixguard/mobile_api/window_tracker.py` | `latest_model_council_study_packet` | latest `STUDY_PACKET` | diagnostics/UI | no dedicated endpoint for non-executable packet visibility | `/study/latest` endpoints added |
| Execution Packet Publisher | `phoenixguard/mobile_api/window_tracker.py` | `latest_model_council_packet` | `PG_EXECUTION_PACKET_V3` | shooter packet reader | endpoint correctly returns only executable packets | unchanged; execution/latest remains executable-only |
| Shooter Packet Reader | `shooter.py` | `fetch_latest_model_council_packet`, `fetch_latest_model_council_study_packet` | executable packet or study visibility packet | V3 gate evaluator / status window | legacy startup test could bypass packet authority; wait logs showed `packet_id=null` | `--test-signal` isolated to `CALIBRATION_TEST`; study packets now produce explicit wait decisions |
| Shooter Gates | `shooter.py` | `_evaluate_v3_shooter_decision` | gate states | broker action mode | gates stayed `NOT_CHECKED` without executable packet | expected behavior preserved; study packets never enter gates |
| Floating Window | `shooter.py` | `FloatingStatusBox._build_signal_text` | operator status text | user | showed `n/a` while council was studying | now displays STUDY/EXECUTABLE packet state, score, next required |

## Diagnostic Command

```powershell
python tools\diagnose_v3_execution_path.py --session pocket-live-8788 --base-url http://127.0.0.1:8793
```

Use this while both processes are running. The deciding comparison is:

| Tracker | Council | Study Packet | Execution Packet | Meaning |
| --- | --- | --- | --- | --- |
| current | WATCHING/PREPARING | present | missing | council has not promoted yet |
| current | EXECUTABLE | present | missing | publisher/endpoint wiring fault |
| current | EXECUTABLE | present | present | shooter should reach gate 1 |
| endpoint error | endpoint error | endpoint error | endpoint error | PhoenixGuard API process is down |
