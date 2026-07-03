# Professional Playbook Root-Cause Review

Date: 2026-07-03

## Purpose

This review records why PhoenixGuard produced short local entries even after the
professional playbook became the intended final authority.

## Root Causes Found

1. Duplicate decision snapshot material was visible to repo search.

   `Developer/decision_making_architecture_pack_20260630_143838` contained copied
   source, copied docs, and runtime session JSON. It was not imported by active
   Python code, but it polluted search and review results. It has been moved to
   `.codex_runtime/forensic_archives/decision_making_architecture_pack_20260630_143838`.

2. High-frequency two-candle execution could still rewrite the live decision
   kernel.

   When ready and enabled, the tracker changed `decision_kernel.decision` to
   `EXECUTABLE`, rewrote major/dominant side to the two-candle side, and set
   timing to the short high-frequency window. That made a local candle reaction
   look like execution authority before the professional hierarchy resolved the
   big-picture thesis.

3. The default M5 profile still treated two-candle output as executable.

   `config/window_tracker_timeframe_profiles.json` described `M5_EXECUTION` with
   `two_candle_execution_allowed=true`. This conflicted with the current doctrine:
   short-horizon candle prediction is contributor evidence, not final authority.

4. The execution-path documentation still emphasizes Model Council promotion.

   The implementation doc correctly says the playbook is the final strategy
   decider, but older execution-path docs still describe Model Council as the
   central promotion node. That is documentation drift and should be reconciled
   before the next release commit.

## Corrected Behavior

- Two-candle prediction remains available as a study/contributor signal.
- Two-candle prediction no longer rewrites the decision kernel by default.
- Direct high-frequency kernel override now requires the explicit
  `allow_high_frequency_kernel_override` control.
- Missing `two_candle_execution_allowed` now defaults to `false` in Model Council.
- The M5 and M1 timeframe profiles keep two-candle execution disabled by default.
- MT4 packets still require a grade-ready professional trade plan and minimum
  professional candle horizon.

## Professional Trading Hierarchy

PhoenixGuard should treat market evidence in this order:

1. Big picture: visible swing path, dominant trend, previous impulse, current
   formed swing, and major supply/demand or trendline structure.
2. Local distribution: pullbacks, consolidation/range state, role flips, retests,
   stop hunts, BMS/SMS/RTO, and opposing force distance.
3. Granular timing: wick reaction, body acceptance, candle sequence, current-leg
   candle count, and current price interaction with structure.
4. Final decision: the playbook selects trend continuation, reversal reclaim,
   aggressive sniper, conservative retest, or no trade.
5. Execution contract: freshness, source identity, model health, API health, and
   MT4 command safety validate the already-decided playbook action.

## Remaining Watch Items

- Reconcile `docs/active_execution_paths.md` with the playbook-final architecture.
- Add burn metrics that compare two-candle direction accuracy separately from
  professional package outcomes.
- During burns, classify short counter-moves as `study`, `scalp-only`, or
  `pullback inside primary thesis`; do not let them become the main trade thesis.
- Treat consolidation as a market regime with special rules: wait for range
  extremes, sweep/reclaim, or breakout/retest instead of trading the middle.
