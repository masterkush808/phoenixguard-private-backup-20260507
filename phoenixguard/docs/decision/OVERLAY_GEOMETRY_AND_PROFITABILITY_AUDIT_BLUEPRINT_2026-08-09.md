# PhoenixGuard V3 Overlay Geometry and Profitability Audit Blueprint

Date: 2026-08-09

Status: Independent-review handoff

Scope: PhoenixGuard V3 only. This document does not create V4, a chart strategy, or automatic execution authority.

## 1. Purpose

This blueprint records the observed overlay failure, the correction contract, and the questions an independent analyst must use to find remaining errors. It also defines what PhoenixGuard may and may not claim about profitability.

The system is intended to discover hidden market state, state transitions, pair and timeframe behavior, complete swing paths, rests, opposing forces, and causal evidence. A directional description is not an entry permission and is not evidence of future profit by itself.

## 2. Incident evidence

The live `pocket-live-8788` packet exposed the following geometry on 2026-08-09:

| Field | Live value | Finding |
| --- | --- | --- |
| Accepted candle-domain bounds | `[226, 130, 1262, 906]` | Current geometry domain |
| Invalid resistance anchors | `[[203, 604], [713, 130]]` | First anchor was 23 pixels outside the accepted candle domain |
| Invalid resistance status | `ACTIVE` | Incorrectly publishable |
| Contract status | `VALIDATED` | False certification |
| Viewport stability evidence | `false` | Required additional lineage caution |
| Strict tracker trendline count | Changed between one and two | Candidate contamination across observations |
| Downstream playbook trendline count | Three | Parallel feed disagreed with the strict tracker feed |

The giant red diagonal was not a cosmetic renderer error. The backend certified stale or out-of-domain anchor geometry, and the frontend had no final strict-contract rejection boundary.

## 3. Root-cause chain

1. Coordinates were numeric, but anchors were not proven to belong to closed candles in the accepted frame.
2. Candidates outside the current candle domain were retained.
3. An offscreen projection became a viewport-border intersection that was never a wick or current-price projection.
4. The renderer checked the full bitmap instead of the accepted candle domain.
5. Parallel feeds could describe trendlines without a mandatory strict acceptance boundary.
6. Direction, local leg, reaction, empirical outcome mass, and execution permission were presented too closely.

## 4. Corrected strict trendline contract

A trendline is publishable only when every condition below is true:

1. The source frame has current symbol, timeframe, geometry epoch, and candle lineage.
2. Both canonical anchors are inside the accepted candle domain.
3. Each anchor maps to a distinct closed candle in that frame.
4. Support matches lower-wick extrema; resistance matches upper-wick extrema.
5. The second anchor occurs later than the first.
6. Existing no-body-obstruction and closed-body-breach checks pass.
7. A third touch is required for structural control; two touches remain developing evidence.
8. Projection reaches latest candle x only when its y remains inside the chart domain.
9. Offscreen projection is omitted, never clipped to a fabricated border endpoint.
10. The first two rendered points remain the exact canonical wick anchors.
11. The dashboard requires `geometry_contract_accepted=true` and an accepted status.

## 5. Required overlay lineage envelope

```json
{
  "schema_version": "PG_OVERLAY_GEOMETRY_V3",
  "frame_id": "accepted-frame-id",
  "closed_candle_key": "symbol|timeframe|close-time",
  "geometry_epoch": 0,
  "viewport_fingerprint": "stable-source-fingerprint",
  "coordinate_space": "chart",
  "coordinate_units": "pixels",
  "geometry_contract_accepted": true,
  "geometry_contract_reason": "machine-auditable proof",
  "anchor_candle_indices": [17, 76],
  "anchor_wick_points": [[413, 854], [1162, 600]],
  "line_points": [[413, 854], [1162, 600], [1262, 566.1]]
}
```

No renderer may infer missing lineage, repair invalid coordinates, substitute another feed, or reuse geometry across a viewport change.

## 6. Hidden-state decision separation

| Field | Question answered | Must not imply |
| --- | --- | --- |
| Dominant structure | Which side has persistent multi-scale evidence? | Immediate entry |
| Local leg | What shorter continuation or counter-leg is active? | Structural flip |
| Reaction evidence | Did closed candles react at valid geometry? | Guaranteed continuation |
| Transition distribution | What followed comparable latent states? | Certainty now |
| Profitability evidence | Did frozen forward decisions produce positive net out-of-sample EV? | Permanent profitability |
| Execution permission | Did every authority and risk contract pass? | Directional truth |

A two-candle local leg cannot reverse dominant structure. An opposite switch requires distinct closed-candle evidence and must survive hysteresis.

## 7. Profitability truth boundary

No nonstationary market system can guarantee future profit. PhoenixGuard must never display that claim. It can guarantee process invariants and refuse to promote an unproven model.

The enforceable guarantees are:

1. No geometry without accepted-frame and wick provenance.
2. No directional flip from a forming candle or duplicate observation.
3. No profitability claim from in-sample reconstruction, backfilled labels, or unmatured outcomes.
4. No promotion when the lower confidence bound of net expected value is non-positive.
5. No undeclared mixing of pair, timeframe, payout, session, or regime statistics.
6. No stale decision reuse after source, symbol, timeframe, geometry, or candle-lineage change.

## 8. Profitability certification design

Profitability evaluation observes frozen decisions forward. It must not generate a chart setup or alter hidden-state discovery labels.

For binary payout ratio `r` and win probability `p`:

```text
EV = p * r - (1 - p)
break_even_probability = 1 / (1 + r)
net_ev_lower = p_lower * realized_payout - (1 - p_lower) - measured_costs
```

Promotion is forbidden unless:

1. Decisions were frozen before outcomes.
2. Horizons cover the complete predicted swing and rest sequence, not a fixed three-to-five-candle shortcut.
3. Overlapping swing horizons are purged and embargoed.
4. Evaluation is walk-forward and strictly out of sample.
5. Pair, timeframe, session, latent regime, and payout are reported separately.
6. Conservative net EV lower bound is positive after costs.
7. Calibration error, Brier score, log loss, drawdown, tail loss, abstention, and support pass declared thresholds.
8. Multiple non-overlapping forward windows remain positive.
9. Drift has not invalidated the certified population.
10. Live-shadow results agree with offline walk-forward results before execution is considered.

Minimum output:

```json
{
  "status": "NOT_CERTIFIED",
  "pair": "NZD/USD OTC",
  "timeframe": "M5",
  "latent_regime": "DOWN_SWING_WITH_LOCAL_UP_PULLBACK",
  "matured_forward_decisions": 0,
  "win_probability_lower_bound": null,
  "net_ev_lower": null,
  "calibration_error": null,
  "maximum_drawdown": null,
  "drift_status": "UNKNOWN",
  "execution_authority": false
}
```

`NOT_CERTIFIED` does not block intelligence. Discovery continues while execution remains separate.

## 9. Independent analyst questions

Each answer requires packet evidence, file references, and a reproducible test:

1. Can any public trendline anchor fail to map to an accepted closed-candle wick?
2. Can geometry survive a viewport-fingerprint change?
3. Can retained study pair with a bitmap from different candle lineage?
4. Can council, playbook, or history reintroduce a rejected trendline?
5. Can offscreen projection become a visible border endpoint?
6. Can chart coordinates render against window dimensions?
7. Can support or resistance select the wrong wick edge?
8. Can touch counts include duplicates or forming candles?
9. Can a local counter-leg overwrite structure before hysteresis matures?
10. Can empirical BUY/SELL mass contradict the headline without explanation?
11. Can a profitability claim use zero matured outcomes?
12. Can overlapping swing outcomes leak across folds?
13. Are payout and costs captured at decision time?
14. Does every timeframe convert candle horizon into real duration correctly?
15. Is memory bounded by sufficient statistics and compact motifs instead of unlimited frames?

## 10. Acceptance criteria

Overlay correction requires:

1. `[[203,604],[713,130]]` is absent for the cited candle domain.
2. Valid lines retain exact first two wick anchors.
3. Offscreen projection yields anchor-only geometry and no border intersection.
4. Forming-candle and stale-domain anchors are rejected.
5. Legacy trendline objects without strict acceptance cannot render.
6. Symbol, timeframe, viewport, or geometry-epoch changes clear incompatible overlays.

Profitability claims require forward measurement, uncertainty bounds, leakage audit, pair/timeframe specificity, and separation from execution permission.

## 11. Implementation surfaces

- `Backend/src/phoenixguard/tracking/trendline_geometry_v3.py`
- `Backend/src/phoenixguard/tracking/market_object_tracker_v3.py`
- `Frontend/dashboard/static/window_tracker_dashboard.html`
- `Backend/tests/test_trendline_geometry_normalization_v3.py`
- `Backend/tests/test_dashboard_static_contract.py`
- `Backend/src/phoenixguard/study/directional_consensus_v3.py`

This is the V3 review baseline. Any relaxation requires evidence that it does not restore stale geometry, fabricated endpoints, directional contradiction, leakage, or false profitability claims.
