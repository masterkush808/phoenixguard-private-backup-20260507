# PhoenixGuard V3 Final Deployment Report

Date: 2026-05-19

## Status

V3 authority separation is implemented and regression-tested.

The tracker publishes Model Council state and optional `PG_EXECUTION_PACKET_V3` packets. The shooter is the only live click path and requires V3 schema validation, runtime integrity, second live read, trade discipline, side match, time sequence, and calibration. Tracker-internal live clicks and demo-random clicks are blocked.

## Agent Recheck Summary

| Agent | Recheck outcome |
| --- | --- |
| Agent 1: Cartographer/schema | Rechecked schema/docs; V3 packet validator and maps are present |
| Agent 2: Model Council | Rechecked maturity pipeline, stateful second-read maturity, mutual exclusion, and study-vs-executable schemas |
| Agent 3: Market intelligence | Rechecked zones, angles, history, middle-safe, and late-chase classifiers |
| Agent 4: Shooter | Rechecked V3-only shooter gates, amount preservation, duplicate prevention, and pre-click confirmation |
| Agent 5: Observability/cache | Rechecked cache schema, health/intelligence endpoints, forensic/paper-mode helpers |

## Implemented Authority Chain

```text
capture/tracker evidence
  -> ModelCouncilV3
  -> optional PG_EXECUTION_PACKET_V3
  -> /v1/mobile/model-council/.../execution/latest
  -> Shooter V3 runtime integrity
  -> second live read
  -> trade discipline
  -> calibrated time sequence
  -> calibrated BUY/SELL click
```

## Live Reactivation Gate

Live reactivation remains controlled. Before enabling live clicks, confirm:

- the shooter is pointed at `/v1/mobile/model-council/sessions/{session_id}/execution/latest`
- calibration profile exists and BUY/SELL/time controls are valid
- health endpoint reports all required models awake
- paper mode logs match expected V3 decisions
- the 5-trade / 20-minute discipline rule is enabled
- emergency stop behavior has been tested

## Verification

```text
python -m pytest -q
742 passed, 3 skipped

python -m pytest tests/test_execution_packet_schema_v3.py tests/test_model_council_v3.py tests/test_market_intelligence_v3.py tests/test_cache_observability_v3.py tests/test_shooter_v3_runtime.py tests/test_window_tracker_model_council_v3.py tests/test_shooter_runtime.py tests/test_shooter_parse.py tests/test_window_tracker_service.py -q
231 passed

python -m compileall -q phoenixguard/decision phoenixguard/execution phoenixguard/runtime phoenixguard/mobile_api shooter.py
passed
```
