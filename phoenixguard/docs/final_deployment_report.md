# PhoenixGuard V3 Final Deployment Report

Date: 2026-05-19

Superseded by the current package-reporter architecture. This report is kept as historical
deployment evidence, but `shooter.py` is no longer a live click path.

## Status

V3 authority separation is implemented and regression-tested.

The tracker publishes Model Council state and optional `PG_EXECUTION_PACKET_V3` packets. Current
builds use `shooter.py` only as an accepted allowance-package reporter. Downstream MT4/external
execution must consume an explicit, accepted, execution-ready `PG_ALLOWANCE_PACKAGE_V1`.
Tracker-internal live clicks and demo-random clicks are blocked.

## Agent Recheck Summary

| Agent                             | Recheck outcome                                                                                               |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Agent 1: Cartographer/schema      | Rechecked schema/docs; V3 packet validator and maps are present                                               |
| Agent 2: Model Council            | Rechecked maturity pipeline, stateful second-read maturity, mutual exclusion, and study-vs-executable schemas |
| Agent 3: Market intelligence      | Rechecked zones, angles, history, middle-safe, and late-chase classifiers                                     |
| Agent 4: Shooter/package reporter | Rechecked V3 packet and allowance-package handoff boundaries                                                  |
| Agent 5: Observability/cache      | Rechecked cache schema, health/intelligence endpoints, forensic/paper-mode helpers                            |

## Implemented Authority Chain

```text
capture/tracker evidence
  -> ModelCouncilV3
  -> optional PG_EXECUTION_PACKET_V3
  -> PG_ALLOWANCE_PACKAGE_V1
  -> /v1/mobile/model-council/.../execution/latest
  -> shooter package reporter
  -> MT4 bridge / external execution path
```

## Package Handoff Reactivation Gate

External package handoff remains controlled. Before enabling any downstream external execution
consumer, confirm:

- the package reporter is pointed at
  `/v1/mobile/model-council/sessions/{session_id}/execution/latest`
- the packet includes explicit `PG_ALLOWANCE_PACKAGE_V1`
- the package is accepted and execution-ready
- the package type is `INTRADAY_ENTER_NOW` or `SWING`
- the MT4 bridge rejects inferred or missing packages
- health endpoint reports all required models awake
- paper mode logs match expected V3 decisions
- emergency stop behavior for the external consumer has been tested

## Verification

```text
.\.venv\Scripts\python.exe -m pytest -q
742 passed, 3 skipped

.\.venv\Scripts\python.exe -m pytest Backend/tests/test_execution_packet_schema_v3.py Backend/tests/test_model_council_v3.py Backend/tests/test_market_intelligence_v3.py Backend/tests/test_cache_observability_v3.py Backend/tests/test_shooter_v3_runtime.py Backend/tests/test_window_tracker_model_council_v3.py Backend/tests/test_shooter_runtime.py Backend/tests/test_shooter_parse.py Backend/tests/test_window_tracker_service.py -q
231 passed

python -m compileall -q phoenixguard/decision phoenixguard/execution phoenixguard/runtime phoenixguard/mobile_api shooter.py
passed
```
