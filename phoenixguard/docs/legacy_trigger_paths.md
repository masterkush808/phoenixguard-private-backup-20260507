# Legacy Trigger Paths

## Current Classification

| Path | File | Status | Reason |
| --- | --- | --- | --- |
| Raw tracker action / `execution_action` | `shooter.py` | Disabled for V3 execution | shooter only executes `PG_EXECUTION_PACKET_V3` |
| Decision kernel trigger text | `shooter.py` | Diagnostic only | logged but not an execution authority |
| `SNIPER_READY` / skill gate readiness | `shooter.py` | Diagnostic only | not a broker-click authority |
| `--test-signal` startup entry | `shooter.py` | Calibration-only | rejected unless `--shooter-mode CALIBRATION_TEST` |
| Model Council `STUDY_PACKET` | `phoenixguard/decision/model_council_v3.py` | Visibility only | never clicked by shooter |
| Model Council `PG_EXECUTION_PACKET_V3` | `phoenixguard/decision/model_council_v3.py` | Execution authority | only packet type that can reach shooter gates |

## Production Rule

Production V3 flow is:

```text
Model Council final score and promotion trace
-> PG_EXECUTION_PACKET_V3
-> /v1/mobile/model-council/.../execution/latest
-> shooter gate 1
-> gate 2
-> gate 3
-> broker action mode
```

The calibration startup test is intentionally outside that chain. It does not create packet state and does not update V3 cooldown or five-trade discipline.
