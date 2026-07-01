# Book Strategy Master V3

Date: 2026-06-30

## Purpose

`PG_BOOK_STRATEGY_MASTER_V3` is the single-timeframe action-principles layer for PhoenixGuard V3.

It does not replace computer vision, overlays, regression, LSTM, skill gates, memory, Model Council, or the V3 execution packet contract. It consumes those outputs as evidence and is the final strategy decider. Model Council is a contributor gate for freshness, model health, source lock, sequence context, timing diagnostics, and packet-readiness evidence; the packet validator still decides whether the playbook decision is current and safe to publish.

## Doctrine

- Observation is not execution.
- Study is not execution.
- Raw side is not execution.
- Dashboard state is not execution.
- Memory confidence is not execution.
- Model Council score/lane acceptance is not execution authority.
- The playbook is the final decision authority.
- Only a fresh validated V3 execution or allowance package can authorize action.

## Maturity Ladder

- `NO_OPPORTUNITY`
- `EARLY_FORMING`
- `VALID_WATCH`
- `PREPARE`
- `ENTER_NOW`
- `LATE_CHASE`
- `INVALIDATED`
- `MISSED`

Hard runtime integrity remains strict. Soft intelligence contributors affect confidence and next-required explanation. Entry-quality logic decides whether the opportunity is early, mature, late, invalid, or missed. `ENTER_NOW` comes from the playbook, not from raw lane score or Model Council final side.

## Playbook Families

- SMC turtle soup
- SMC SH/BMS/RTO
- SMC SMS/BMS/RTO
- AMD reversal
- Supply rejection
- Demand rejection
- Supply break/retest continuation
- Demand break/retest continuation
- Trendline confluence bounce
- Trendline break/retest
- Channel edge reaction
- Fibonacci OTE reaction
- Pivot or round-number reaction
- Slingshot false break
- Candle confirmation at zone
- Chop/no-trade

## Reaction Matrix

The master playbook now evaluates a reaction matrix before promotion:

- Significant structure selection: demand/supply zones, outer or inner trendline touches, role-flip retests, BMS retests, liquidity sweeps, and continuation flow.
- Candlestick reaction grammar: wick rejection, body acceptance, retest hold, reclaim after sweep, continuation pressure, exhaustion, or no reaction.
- Entry profile selection: `AGGRESSIVE_SNIPER`, `CONSERVATIVE_RETEST`, `CONTINUATION_RETEST`, `REVERSAL_RECLAIM`, `MOMENTUM_ACCEPTANCE`, `WATCH_ONLY`, or `NO_TRADE`.
- Strategy combo construction: playbook family plus structure, candle reaction, SMC/liquidity, trendline, retest, and role-flip evidence.

Aggressive sniper entries require current-price interaction with significant structure plus a live wick/reclaim/body-acceptance reaction. Conservative entries wait for retest, reclaim, or role-flip proof. Missing candle proof is a watch condition, not a hard runtime failure.

Hard playbook blockers stay intentionally narrow:

- stale or non-advancing live truth when reported by the runtime payload;
- dirty/stale cache state;
- required models not awake;
- unhealthy API state;
- late chase;
- invalidated candidate;
- opposing force too close or no clean path room;
- explicit buy-high/sell-low/path-room bad-entry classifications.

## Integration

- `phoenixguard.decision.book_strategy_master_v3` evaluates the already-detected evidence.
- `phoenixguard.decision.model_council_v3` contributes hard freshness/runtime gates and publishes study or packet state based on the playbook decision.
- `phoenixguard.execution.packet_v3` carries `execution_authority=PLAYBOOK_FINAL_DECIDER_V3` and `packet_authority=PG_EXECUTION_PACKET_V3` into execution packets.
- `phoenixguard.execution.floating_state_reducer` exposes a compact strategy summary.
- `phoenixguard.mobile_api.live_state_v3` keeps the strategy fields in compact live payloads.
- `window_tracker_dashboard.html` displays the strategy read in the inspector only.

## Overlay Boundary

This feature does not change overlay geometry, chart transforms, supply/demand placement, candle snapping, trendline rendering, or chart-plane drawing rules.

Historical replay structure was tightened so a clean one-leg trend can be split into two anchored history segments instead of becoming one oversized replay object. This supports the decision read and keeps the visual sanitizer policy intact.

## Verification

Last local verification:

- `python -m pytest -q Backend/tests --maxfail=1`
  - `1325 passed in 699.74s`
- `python -m pyright`
  - `0 errors, 0 warnings, 0 informations`
- `python -m compileall -q Backend/src Backend/tools Backend/tests`
  - pass
- `python -m pip check`
  - `No broken requirements found.`
- `git diff --check`
  - pass with CRLF normalization warnings only

## Verdict

`PASS_WORKING_PRODUCT_LOCAL_VERIFICATION`
