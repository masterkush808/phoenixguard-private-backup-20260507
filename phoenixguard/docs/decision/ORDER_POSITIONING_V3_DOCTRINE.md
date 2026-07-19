# Order Positioning V3 Doctrine

Date: 2026-07-19
Status: binding design and annotation doctrine for PhoenixGuard V3 order-position overlays

## Purpose

PhoenixGuard must identify a defensible price area before a move, freeze that area for the tracking episode, and observe whether price approaches, respects, rejects, or invalidates it. It must not drag an entry, target, or invalidation box after every new candle and then present the moved box as if it had been predicted earlier.

This doctrine governs visualization, weak-supervision candidate generation, human annotation, and future training data. It does not place broker orders and it does not authorize a trade. A box is evidence about positioning; only the existing validated playbook and execution-package contract can grant permission to act.

## Binding order vocabulary

Modern broker semantics are binding. Local teaching material contributes chart context, but cannot redefine an order type.

| Canonical overlay | Binding meaning at the episode anchor | Required price relationship |
| --- | --- | --- |
| `BUY_LIMIT_ZONE` | Passive buy opportunity on a pullback into a validated area | Below current price, or explicitly at price within a documented tolerance |
| `SELL_LIMIT_ZONE` | Passive sell opportunity on a rally into a validated area | Above current price, or explicitly at price within a documented tolerance |
| `BUY_STOP_ENTRY_ZONE` | Momentum entry evidence that becomes relevant only if price rises through a confirmation boundary | Above current price and not already crossed |
| `SELL_STOP_ENTRY_ZONE` | Momentum entry evidence that becomes relevant only if price falls through a confirmation boundary | Below current price and not already crossed |
| `PROTECTIVE_STOP_ZONE` with `side=SELL`, `thesis_side=BUY`, `order_kind=SELL_STOP` | Invalidation area for a long thesis | Below the protected long structure |
| `PROTECTIVE_STOP_ZONE` with `side=BUY`, `thesis_side=SELL`, `order_kind=BUY_STOP` | Invalidation area for a short thesis | Above the protected short structure |
| `NO_VALID_ZONE` | Explicit negative label: the visible evidence does not support a causal, anchored area | No order-position box may be invented for the scoped side |

An entry stop and a protective stop are different roles. A buy stop can describe a breakout entry above price or protection for a short position; a sell stop can describe a breakdown entry below price or protection for a long position. PhoenixGuard must encode the role explicitly and must never infer it from the word `stop` alone.

`PROTECTIVE_STOP_ZONE` is the only canonical protective annotation label. In every canonical zone, `side` means the actual broker order side and `thesis_side` means the direction being entered or protected. The legacy names `BUY_PROTECTIVE_STOP_ZONE` and `SELL_PROTECTIVE_STOP_ZONE` described the thesis, not the order. An ingestion adapter may therefore map legacy `BUY_PROTECTIVE_STOP_ZONE` to canonical `side=SELL`, `thesis_side=BUY`, `order_kind=SELL_STOP`, and map the legacy sell form inversely. It must reject contradictory explicit thesis or order-kind metadata instead of silently repairing it.

The binding external references are:

- [FINRA order types](https://www.finra.org/investors/investing/investment-products/stocks/order-types): a buy limit executes only at its limit or lower, a sell limit only at its limit or higher; a stop normally becomes a market order after activation; and a stop-limit can remain unfilled.
- [CME Group glossary](https://www.cmegroup.com/education/glossary): a buy stop is placed above the market and a sell stop below it; stop-limit activation direction follows the same relationship.
- [CME futures order types](https://www.cmegroup.com/education/courses/futures-trading-mechanics-and-regulation/futures-order-types): order behavior, activation, and fill mechanics remain distinct from chart analysis.
- [SEC Trading 101](https://www.investor.gov/sites/default/files/trading101basics.pdf) and [Investor.gov order types](https://www.investor.gov/introduction-investing/investing-basics/glossary/order-types): limit price is a constraint, not a guarantee of execution.

Broker support, spread treatment, slippage, trigger conventions, and fill rules vary. The dashboard therefore describes opportunity and invalidation areas, not executable broker instructions or guaranteed fills.

## Local-book synthesis

The page references below use the PDF file-page index. It can differ from a book's printed page number. The lessons are paraphrased; none is a promise that a setup will work.

| Local source | PDF pages | Codable lesson | What must not be encoded |
| --- | ---: | --- | --- |
| *FOREX BLACK BOOK* | 60-62, 121-122, 136, 157 | Treat support and resistance as areas; draw trendlines from real structure; channel edges can locate passive opportunity; define and test entry, stop, and target rules. | A line touch is not automatic permission. Page 20 uses legacy limit-order wording that conflicts with modern FINRA/CME semantics and is not canonical. |
| *HLZ - Market Structure And Powerful Setups* | 26, 28-29, 46, 51-53, 78, 80-81, 105, 115 | Liquidity often clusters around stops; structure breaks, order-block returns, sweeps, and lower-timeframe confirmation can locate candidate areas; protective invalidation belongs beyond the defended structure. | A liquidity sweep or named order block does not guarantee reversal. A session time is context, not execution authority. |
| *secrets revealed $10 000 cost price-1-1* | 14, 17, 20, 26-27, 36, 41, 43-45, 51, 53, 94 | Validate trendline geometry first; use horizontal and diagonal confluence; stop-entry evidence belongs beyond a closed confirmation candle's high or low; protection belongs beyond a nearby swing with room. | Do not force a trendline to fit, chase a trigger far from the line, or continuously move an episode's original area. |
| *The Power of Japanese Candlestick Charts* | 41, 43-44, 65, 108, 218, 235, 240-241 | Candle patterns require trend and location context; confirmation can be represented beyond pattern or confirmation extremes; windows can behave as support/resistance areas. | A candle name alone is not a zone and is not sufficient for an entry. Countertrend patterns must not override primary structure without confirmation. |
| *The Art of Currency Trading* | 43-44, 126, 129, 133, 152, 162, 246, 254, 307, 354 | Use technical structure to position entry and invalidation; prefer convergence and pullback positioning to mid-range chasing; leave structural room beyond obvious levels; keep a major-level inventory. | Do not redraw or rescale evidence until it agrees with a desired view. Do not place protection exactly on an obvious line or round number without a justified buffer. |

## Positioning rules by evidence family

### Supply and demand

- A passive buy area may be proposed only on a return toward validated demand or reclaimed support below the anchor price.
- A passive sell area may be proposed only on a return toward validated supply or rejected resistance above the anchor price.
- A momentum buy area belongs beyond a confirmed upper boundary; a momentum sell area belongs beyond a confirmed lower boundary.
- A stop-entry proposal requires a named confirmation event, its confirmed `BUY` or `SELL` direction, and a completed-candle key or index. The confirmation direction must match the proposed thesis side. Raw supply, demand, liquidity, order-block, or trendline geometry is landscape evidence only and cannot independently promote a stop-entry zone.
- Protection belongs beyond the structural invalidation edge with a documented volatility, spread, or candle-range buffer. The edge itself is not automatically the stop.
- Zone freshness, historical reactions, significance, distance, and opposing-force room are evidence fields, not reasons to slide the box toward current price.

### Trendlines and channels

- Trendlines require real swing anchors, a stable chart transform, and enough touches or structural support to justify the line.
- A passive area can exist where a validated trendline or channel edge intersects compatible horizontal structure.
- A stop-entry area can exist beyond a closed confirmation candle at a validated break or rejection boundary.
- The proposed zone must remain close to the original structural interaction. If price has already travelled materially away, the correct output is `NO_VALID_ZONE` or `LATE_AFTER_MOVE`, not a relocated box.
- Opposing trendlines and horizontal levels must be recorded before the candidate is accepted.

### Candlesticks

- A candlestick pattern is timing evidence only when it occurs at a validated structure location.
- Confirmation must use closed candles. A buy-stop entry area may begin above the relevant confirmed high; a sell-stop entry area may begin below the relevant confirmed low.
- A passive limit area must come from the underlying support, demand, resistance, or supply geometry. It cannot be invented from a candle label in the middle of a range.
- Pattern extremes and the nearby swing can anchor protective invalidation, with a documented buffer.

### Market structure

- Pullback and retest geometry can support passive limit areas when the current price has not already crossed or left the area.
- A confirmed break of structure can support a stop-entry area beyond the boundary, provided the trigger is still prospective at publication time.
- Impulse, pullback, retest, continuation, and current-candle objects are evidence for one episode plan; they must not become a new plan on every candle.
- Mid-range positioning, conflicting higher-timeframe structure, or inadequate room to opposing force produces `NO_VALID_ZONE`.

### Liquidity and stop clusters

- Buy-side liquidity above highs and sell-side liquidity below lows are hazard and path-context areas.
- A visible liquidity pool is not automatically a reversal entry. A reversal candidate needs causal evidence such as a sweep and closed reclaim/rejection at compatible structure.
- Breakout candidates must distinguish a genuine prospective stop-entry boundary from an already completed sweep.
- Stop clusters should not be exposed under proprietary jargon in beginner UI. The public label can say `Buyers may react here`, `Sellers may react here`, `Upside confirmation`, `Downside confirmation`, or `Plan fails here`; the canonical contract name remains internal.

## Geometry contract

Every accepted zone must be reconstructable from the anchor frame. It requires:

1. Pair, timeframe, source lock, capture timestamp, frame identity, closed-candle key, and episode identity.
2. Source-image hash, image size, plot bounds, chart-transform identity, pixel bounding box, and normalized bounding box.
3. The anchor price proxy and the zone's price relationship at creation.
4. Hard evidence anchors: candle IDs or indices and candle boxes, swing anchors, source zone IDs, or validated trendline IDs.
5. Evidence-family tags and a short human-readable rationale.
6. Separate geometry confidence and doctrine confidence. Model confidence alone cannot promote a floating box.

The semantic validator must enforce `x2 > x1`, `y2 > y1`, normalized coordinates within `[0, 1]`, containment within plot bounds, and consistency between pixel and normalized geometry. Normalized annotation boxes are plot-relative: subtract the plot origin and divide by the plot width or height. The validator must also verify the vertical price relationship using the recorded `price_axis_direction`; pixel direction must never be assumed when an axis can be inverted or cropped.

A box must be clipped to the plot, never to broker controls, asset tabs, or decorative UI. Thin line evidence may be expanded to a visible band only by a deterministic, recorded tolerance. Expansion cannot cross the current-price relationship required by the order type without an explicit `AT_CURRENT` tolerance reason.

## Episode freeze and live observation

The tracking episode is the unit of prediction and evaluation.

- At `Start tracking`, the first complete, source-locked closed candle establishes the episode anchor.
- Candidate geometry is emitted once from information visible at that anchor. The original bounding boxes, order roles, evidence anchors, and rationale are immutable.
- A later broker scroll or rescale may reproject that immutable geometry only when at least three stable closed-candle IDs prove one global baseline-to-current transform and the fit stays inside bounded scale and residual tolerances. One mutable source box can never move the plan. If the global fit is unproven, the frozen areas are hidden for that frame instead of guessed.
- Pair, timeframe, and broker-source continuity are mandatory. Sequence and chart-transform identifiers may legitimately advance between frames; they are current-frame lineage evidence, not a reason to force the old pixel coordinates onto a changed chart.
- The default horizon is 12 completed candle observations. A new screenshot poll is not automatically a new episode.
- Later frames update observation state only: `UNTOUCHED`, `APPROACHING`, `TOUCHED`, `RESPECTED`, `REJECTED`, `BROKEN`, `INVALIDATED`, or `EXPIRED`.
- Later frames may record which forecast branch is being favoured, maximum favourable excursion, maximum adverse excursion, and time to touch. They may not rewrite the starting geometry.
- A trade moving five candles in the expected direction remains a tracked outcome of the original plan. PhoenixGuard must not issue a fresh late prediction on the fifth candle merely because current-price geometry changed.
- A candidate can be superseded only by an explicit episode reset, pair/timeframe/source-lock change, unusable chart transform, or hard invalidation. Supersession must preserve the original record and name the reason.
- `Stop and save` ends observation and persists the episode; it must not erase the baseline. `Reset` starts a new episode identity after the prior episode is complete or explicitly abandoned.

## No-chase rules

- A limit area is invalid at publication if price already passed through it and travelled away before the plan was frozen.
- A stop-entry area is invalid at publication if its confirmation boundary was already crossed on a completed candle.
- A candidate too far from its structural anchor, formed in the middle of a range, or lacking room to opposing structure is marked `NO_VALID_ZONE`.
- Do not move an old limit area to the latest pullback, move a stop-entry area to the newest high or low, or relabel a missed move as a current opportunity.
- A missed opportunity is evidence for evaluation. It is not a defect to hide by redrawing the overlay.

## Current implementation truth

PhoenixGuard does not currently contain a human-labelled supervised detector for these order-position boxes. The existing BUY/SELL pools are whole-image direction classes, and current feedback labels do not supply a reviewed population of order-zone bounding boxes. Grounding and existing box candidates can propose weak evidence, but they are not precision truth.

The honest implementation sequence is:

1. Generate deterministic or weak-supervision candidates from candle geometry, validated supply/demand structure, swing anchors, trendlines, and chart transforms.
2. Freeze candidates per episode and record their live observation history.
3. Have humans accept, adjust, reject, or mark `NO_VALID_ZONE` using the V3 annotation schema.
4. Adjudicate disagreements and quarantine ambiguous or non-causal records.
5. Split by episode and related-source group before any training.
6. Train a detector only after the reviewed dataset is sufficiently representative; report localization metrics by zone class and market regime, not image-direction accuracy.

Until that sequence is complete, UI and documentation must say `rule-derived`, `candidate`, or `human-reviewed`. They must not say `trained detector`, `learned precision zone`, or an equivalent unsupported claim.

## Human annotation and leakage prevention

- Geometry annotations used for causal training are made from the anchor frame without future candles visible.
- Annotators label every defensible order-position area in scope, or add `NO_VALID_ZONE` with a reason. Absence of a box is not silently treated as a negative.
- Outcome review is a separate phase. It may add touch, respect, break, path, and excursion labels, but cannot silently alter pre-outcome geometry.
- Each record preserves annotator, reviewer, timestamps, doctrine version, evidence anchors, confidence, and adjudication state.
- Related frames from one episode, perceptual duplicates, crops of the same capture, and records sharing a source sequence belong to one split group.
- Train, validation, and test assignment occurs at the episode-group level. No related frame may cross splits.
- Weak-supervision proposals are `REVIEW_REQUIRED` until a human accepts their geometry and semantics.
- Ambiguous, contradictory, non-causal, stale, or transform-uncertain records are excluded from detector training.
- `ELIGIBLE` is fail-closed: it requires causal pre-outcome annotation, a locked double review or resolved adjudication, no active disagreement or exclusion reason, an assigned episode-group split, no future-frame visibility, and a clean semantic-validation result.

The machine-readable contract is [phoenixguard_order_positioning_annotation_v3.schema.json](../schemas/phoenixguard_order_positioning_annotation_v3.schema.json).

## Acceptance gates

An order-position overlay is publishable only when all applicable gates pass:

- source lock and pair/timeframe identity are certain;
- the anchor uses a complete closed candle;
- geometry is inside the chart plot and tied to hard anchors;
- order role and price relationship are semantically consistent;
- the candidate is prospective rather than already crossed or chased;
- the episode baseline is frozen and recoverable;
- conflicting and opposing structure is recorded;
- the public label is plain language while the internal contract remains canonical;
- the overlay is clearly non-executing evidence;
- rejected and `NO_VALID_ZONE` cases are retained for evaluation.

## Prohibited claims and behaviours

- No overlay guarantees that price will react, an order will fill, or a trade will profit.
- No backfilled box may be presented as a live prediction.
- No future candle may leak into pre-outcome geometry or training labels.
- No whole-image BUY/SELL classifier output may be described as localized order-zone detection.
- No liquidity, candlestick, trendline, model, book strategy, or memory artifact may authorize execution by itself.
- No diagnostic or proprietary internal term is required in the beginner-facing label.
