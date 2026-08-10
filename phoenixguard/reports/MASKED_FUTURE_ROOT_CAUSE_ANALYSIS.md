# Masked-Future V3 Root-Cause Analysis

## Gate

This report was generated before optimized training. It audits causal data, labels,
feature coverage, calibration evidence, and independent-family support.

## Dataset independence

| Measure | Value | Detail |
|---|---:|---|
| Source images | 332 | No image files duplicated |
| Near-duplicate families | 53 | All related images remain in one outer fold |
| All causal cutoffs | 37930 | Visible prefixes only |
| Eligible event windows | 19365 | Event-conditioned model denominator |
| Unresolved symbols | 37 | Uses cross-pair backoff |
| Unresolved timeframes | 24 | Uses cross-timeframe backoff |

## Candle extraction quality

| Measure | Value | Detail |
|---|---:|---|
| Failed extractions | 0 | Must remain zero |
| Candles per image | 38/170.0/414 | min/median/max |
| Mean parse confidence | 0.9293 | Candle geometry |
| Mean spacing confidence | 0.8654 | Track coherence |

## Label quality

| Measure | Value | Detail |
|---|---:|---|
| 3 | 19365 | REST 4.39%; majority/endpoint disagree 35.73% |
| 5 | 19365 | REST 5.12%; majority/endpoint disagree 38.84% |
| 8 | 19365 | REST 29.98%; majority/endpoint disagree 52.56% |
| 13 | 19365 | REST 5.61%; majority/endpoint disagree 41.29% |
| 21 | 19365 | REST 6.47%; majority/endpoint disagree 43.20% |
| 34 | 19365 | REST 12.59%; majority/endpoint disagree 47.74% |

Trade-path outcomes: {"INVALIDATION_BEFORE_TARGET": 11468, "TARGET_BEFORE_INVALIDATION": 7196, "TIME_BARRIER_EXPIRED": 701}.

Opportunity maturity labels: {"ENTER_NOW": 4696, "INVALIDATED": 11243, "LATE_CHASE": 330, "MISSED": 659, "PREPARE": 2397, "VALID_WATCH": 40}.

## Feature coverage

| Measure | Value | Detail |
|---|---:|---|
| Strict 3-touch trendline | 43.03% | Visible prefix only |
| Scale conflict | 25.90% | Long/local disagreement |
| Latest flip | 47.50% | Closed visible candle |
| Sparse contexts | 595 | of 1316 event/side/pair/timeframe contexts |

## Existing model calibration and horizon performance

| Measure | Value | Detail |
|---|---:|---|
| 3 | 56.21% | baseline 38.91%; Brier 0.2518 |
| 5 | 55.01% | baseline 40.16%; Brier 0.2536 |
| 8 | 52.82% | baseline 40.88%; Brier 0.2605 |
| 13 | 52.93% | baseline 41.74%; Brier 0.2577 |
| 21 | 51.80% | baseline 42.53%; Brier 0.2600 |
| 34 | 51.12% | baseline 43.46%; Brier 0.2633 |

## Leakage audit

Status: **PASS**

{
  "checks": {
    "all_cutoffs_from_one_family_in_one_fold": true,
    "all_cutoffs_from_one_image_in_one_fold": true,
    "calibration_and_test_events_disjoint": true,
    "folder_buy_sell_label_absent_from_features": true,
    "future_suffix_revealed_to_scorer_only": true,
    "future_targets_absent_from_feature_keys": true,
    "near_duplicate_family_grouping_required": true,
    "visible_prefix_matches_cutoff": true
  },
  "failures": [],
  "family_count": 51,
  "image_count": 324,
  "row_count": 37930,
  "schema_version": "PG_OPTIMIZED_LEAKAGE_AUDIT_V3",
  "status": "PASS"
}

## Root causes

1. Only 53 independent near-duplicate families exist despite tens of thousands of causal cutoffs.
2. Broad majority-direction labels disagree with endpoint direction in a material share of windows.
3. The old model has no target-before-invalidation, MFE, MAE, drawdown-first, or maturity label.
4. Pair/timeframe metadata remains unresolved for some images and therefore falls back to cross-pair priors.
5. Sparse event/pair/timeframe contexts require calibrated model backoff rather than raw count confidence.
6. Full-frame CV embeddings are unsafe for masked replay because their pixels include the withheld suffix; only prefix-safe geometry is admissible.

## Training decision

Proceed only with grouped outer folds, disjoint fit/meta/calibration families,
prefix-safe features, calibrated probabilities, and precision plus coverage reporting.
The optimized contributor remains non-executing.
