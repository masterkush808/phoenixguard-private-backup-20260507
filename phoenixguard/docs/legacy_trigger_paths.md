# Legacy Trigger Paths

## Current Classification

| Path                                                               | File                                        | Status                    | Reason                                                                        |
| ------------------------------------------------------------------ | ------------------------------------------- | ------------------------- | ----------------------------------------------------------------------------- |
| Raw tracker action / `execution_action`                            | `shooter.py`                                | Disabled for V3 execution | local shooter reports only accepted allowance packages from validated packets |
| Decision kernel trigger text                                       | `shooter.py`                                | Diagnostic only           | not a package handoff authority                                               |
| `SNIPER_READY` / skill gate readiness                              | `shooter.py`                                | Diagnostic only           | not a broker-click or package-handoff authority                               |
| Manual/test-signal startup entry                                   | `shooter.py`                                | Retired                   | local shooter no longer supports calibrated/manual broker actions             |
| Model Council `STUDY_PACKET`                                       | `phoenixguard/decision/model_council_v3.py` | Visibility only           | never clicked by shooter                                                      |
| Model Council `PG_EXECUTION_PACKET_V3` + `PG_ALLOWANCE_PACKAGE_V1` | `phoenixguard/decision/model_council_v3.py` | Package handoff authority | only this pair can reach the local package reporter and MT4 bridge            |

## Production Rule

Production V3 flow is:

```text
Model Council final score and promotion trace
-> PG_EXECUTION_PACKET_V3
-> PG_ALLOWANCE_PACKAGE_V1
-> /v1/mobile/model-council/.../execution/latest
-> shooter package reporter
-> MT4 bridge / external execution path
```

The retired calibration startup path is intentionally outside that chain. It does not create package
state and must not be treated as V3 execution authority.
