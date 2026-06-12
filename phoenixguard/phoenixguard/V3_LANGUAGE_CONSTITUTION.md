# PhoenixGuard V3 Language Constitution

Version: `PG_V3_LANGUAGE_CONSTITUTION_2026_05_25`

This file is the canonical vocabulary for PhoenixGuard V3. Runtime code, packets, endpoints,
tests, dashboards, and the shooter must use these terms with the meanings below.

## Core Doctrine

Observation is not execution. Score is not execution. Candidate is not execution. Study
packet is not execution.

Only a validated `PG_EXECUTION_PACKET_V3` can authorize the shooter. Only
`ShooterActionSequencerV2` can perform calibrated broker actions. Only `FloatingStateV2`
reports operator truth.

## Canonical Runtime Chain

```text
V3 Launcher
-> Tracker / Frame Capture
-> Vision Models
-> Model Council V3
-> Market Reality Engine
-> Execution Lane Resolver
-> Timing / Path-Aware Timing Engine
-> Study Packet Publisher
-> Execution Packet Publisher
-> PG_EXECUTION_PACKET_V3 Validator
-> ShooterActionSequencerV2
-> Calibrated Time / Side Action
-> FloatingStateV2
-> Observability / Runtime Trace
```

Everything else is support, test, diagnostic, legacy, or quarantine.

## Field Meanings

`raw_side`

Observation-level direction inferred from tracker, raw signal, overlay, or market pressure.
It is never execution authority.

`candidate_side`

The side being evaluated by the Model Council. It may become stable or be rejected. It is
never enough to click.

`final_side`

The Model Council's arbitration side. It must equal `execution.side` inside an executable
packet. It cannot directly reach the shooter.

`execution.side`

The only action side. It must be `BUY` or `SELL` inside a validated
`PG_EXECUTION_PACKET_V3`.

`side`

Generic display shorthand only. In execution-critical code, the qualified field name must
be used: `raw_side`, `candidate_side`, `final_side`, or `execution.side`.

`state`

A scoped state. `execution.state`, `model_council.final_state`, `candidate_stage`, and
`shooter_action_state` are not interchangeable.

`packet_id`

Required for every published packet. `packet_id = null` is not a published packet.

`STUDY_PACKET`

A non-executable Model Council packet. It must include `packet_id`, council state,
candidate or side context when available, score/threshold, `denied_at` or `blocked_by`,
`next_required`, and `promotion_trace`. It must not enable execution.

`PG_EXECUTION_PACKET_V3`

The only execution-authority packet. It must include `schema_version`,
`packet_id`, `execution.enabled=true`, `execution.state=EXECUTABLE`, `execution.side`,
`execution.expiry_seconds`, `execution.time_sequence`, live integrity, model health,
instrument context, and Model Council final state/side.

`EXECUTABLE`

A state meaning the Model Council has granted permission and the execution packet validator
has accepted the packet. It does not mean the click has already happened.

`WATCHING`

A non-executable study state. It must always include a traceable `blocked_by`, `denied_at`,
`next_required`, or `release_condition`.

`PREPARING`

A candidate is close enough to track actively but not ready to execute. It must explain the
missing condition.

`TRADE_CANDIDATE`

A structured candidate with `candidate_id`, `candidate_side`, `candidate_stage`,
stability, score, and required next condition.

`EXECUTION_LANE`

The context path the market is offering. Canonical lanes are `SNIPER_ZONE_ENTRY`,
`LOCAL_BREAKDOWN_CONTINUATION`, `FAILED_RETEST_ENTRY`, `MOMENTUM_ACCEPTANCE_ENTRY`, and
`HISTORY_MATCHED_CONTINUATION`.

`entry_quality`

The quality classification of the entry now: `PERFECT_ENTRY`, `GOOD_ENTRY`,
`VALID_ENTRY`, `AGGRESSIVE_VALID_ENTRY`, `WATCH_ONLY`, or `BAD_ENTRY`.

`timing_decision`

The path-aware timing object. Execution is allowed only when its mode allows entering now
and the selected expiry is present.

`expiry_seconds`

Duration in seconds for the broker time setting. It must match
`time_sequence.target_seconds`.

`time_sequence`

The explicit calibrated time-setting instruction for the shooter. It must include
`target_seconds`, `target_text`, and ordered steps.

`calibration_valid`

The shooter has loaded the required calibrated targets and verified they are compatible
with the current broker window before action.

`runtime_integrity`

Freshness and identity proof: live frame/capture/state advancement, packet TTL, cache
freshness, model health, instrument identity, and endpoint agreement.

`shooter_action_state`

The current action phase of `ShooterActionSequencerV2`, such as `WINDOW_LOCKED`,
`TIME_PANEL_READY`, `TIME_TYPED_OR_SELECTED`, `FINAL_PRE_CLICK_RECHECK`, or
`ABORT_BEFORE_SIDE_CLICK`.

## Non-Negotiable Rules

1. `side` is never enough for execution.
2. `raw_side` is observation only.
3. `candidate_side` is Model Council reasoning only.
4. `final_side` is arbitration only.
5. `execution.side` is the only action side.
6. `final_side` must equal `execution.side` in every executable packet.
7. `STUDY_PACKET` can never enter the shooter action sequencer.
8. `packet_id` cannot be empty or null in a published packet.
9. `schema_version` mismatch rejects execution.
10. Expired or stale packets reject execution.
11. Raw `action`, `execution_action`, `SNIPER_READY`, memory confidence, skill gates, and
    old decision-kernel output cannot authorize action.
12. Amount controls remain unreachable.
13. Floating Window compact mode must not display raw `n/a` debug fields.
14. Runtime trace must show tracker, council, study, execution, shooter, floating state,
    health, cache, and calibration together.
