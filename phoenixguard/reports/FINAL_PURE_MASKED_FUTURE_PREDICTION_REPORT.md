# Final Pure Masked-Future Prediction Report

## 1. Images processed

- Discovered screenshots: 332
- Successfully extracted screenshots: 332
- Screenshots with frozen predictions: 330

## 2. Independent families

- Near-duplicate families: 55
- Grouped folds: 5

## 3. Masked cutoffs

- Prepared causal cutoffs: 1310
- Preparation failures: 9

## 4. Frozen predictions

- Frozen before reveal: 1310
- Every test-family prediction was flushed before its suffix was scored.

## 5. Leakage audit

- Result: **PASS**
- Future pixels were physically obscured in predictor input.
- BUY/SELL folder provenance was never a target or feature.

## 6. Accuracy by horizon

| Horizon | Scored | Majority | Endpoint | Exact step | Candle token |
|---:|---:|---:|---:|---:|---:|
| 1 | 1310 | 51.60% | 42.06% | 51.60% | 37.10% |
| 2 | 1310 | 22.52% | 41.45% | 50.15% | 37.65% |
| 3 | 1310 | 50.15% | 43.59% | 49.08% | 38.56% |
| 5 | 1310 | 51.83% | 44.81% | 53.13% | 37.92% |
| 8 | 1310 | 33.44% | 44.81% | 48.78% | 37.05% |
| 13 | 1286 | 49.07% | 46.03% | 49.92% | 37.43% |
| 21 | 1188 | 45.12% | 46.21% | 48.32% | 35.51% |
| 34 | 918 | 43.25% | 44.77% | 48.58% | 38.06% |

## 7. Accuracy by pair

```json
{
  "AUDCAD": {
    "cases": 56,
    "horizon_predictions": 431,
    "majority_accuracy": 0.410673
  },
  "AUDCHF": {
    "cases": 28,
    "horizon_predictions": 214,
    "majority_accuracy": 0.588785
  },
  "AUDJPY": {
    "cases": 16,
    "horizon_predictions": 122,
    "majority_accuracy": 0.491803
  },
  "AUDNZD": {
    "cases": 16,
    "horizon_predictions": 124,
    "majority_accuracy": 0.395161
  },
  "AUDUSD": {
    "cases": 80,
    "horizon_predictions": 612,
    "majority_accuracy": 0.431373
  },
  "CADJPY": {
    "cases": 63,
    "horizon_predictions": 481,
    "majority_accuracy": 0.417879
  },
  "EURAUD": {
    "cases": 56,
    "horizon_predictions": 436,
    "majority_accuracy": 0.465596
  },
  "EURCAD": {
    "cases": 145,
    "horizon_predictions": 1085,
    "majority_accuracy": 0.446083
  },
  "EURCHF": {
    "cases": 24,
    "horizon_predictions": 186,
    "majority_accuracy": 0.365591
  },
  "EURGBP": {
    "cases": 20,
    "horizon_predictions": 156,
    "majority_accuracy": 0.50641
  },
  "EURJPY": {
    "cases": 64,
    "horizon_predictions": 478,
    "majority_accuracy": 0.443515
  },
  "EURNZD": {
    "cases": 44,
    "horizon_predictions": 341,
    "majority_accuracy": 0.434018
  },
  "EURUSD": {
    "cases": 16,
    "horizon_predictions": 118,
    "majority_accuracy": 0.440678
  },
  "GBPAUD": {
    "cases": 36,
    "horizon_predictions": 275,
    "majority_accuracy": 0.476364
  },
  "GBPCAD": {
    "cases": 76,
    "horizon_predictions": 588,
    "majority_accuracy": 0.440476
  },
  "GBPCHF": {
    "cases": 12,
    "horizon_predictions": 93,
    "majority_accuracy": 0.301075
  },
  "GBPJPY": {
    "cases": 60,
    "horizon_predictions": 459,
    "majority_accuracy": 0.396514
  },
  "GBPNZD": {
    "cases": 48,
    "horizon_predictions": 374,
    "majority_accuracy": 0.451872
  },
  "GBPUSD": {
    "cases": 63,
    "horizon_predictions": 453,
    "majority_accuracy": 0.397351
  },
  "NZDCAD": {
    "cases": 4,
    "horizon_predictions": 32,
    "majority_accuracy": 0.40625
  },
  "NZDJPY": {
    "cases": 64,
    "horizon_predictions": 482,
    "majority_accuracy": 0.414938
  },
  "NZDUSD": {
    "cases": 40,
    "horizon_predictions": 309,
    "majority_accuracy": 0.365696
  },
  "UNKNOWN": {
    "cases": 143,
    "horizon_predictions": 1061,
    "majority_accuracy": 0.468426
  },
  "USDCAD": {
    "cases": 28,
    "horizon_predictions": 212,
    "majority_accuracy": 0.410377
  },
  "USDCHF": {
    "cases": 4,
    "horizon_predictions": 30,
    "majority_accuracy": 0.4
  },
  "USDJPY": {
    "cases": 16,
    "horizon_predictions": 120,
    "majority_accuracy": 0.333333
  },
  "USDZAR": {
    "cases": 12,
    "horizon_predictions": 93,
    "majority_accuracy": 0.451613
  },
  "XAUUSD": {
    "cases": 76,
    "horizon_predictions": 577,
    "majority_accuracy": 0.403813
  }
}
```

## 8. Accuracy by timeframe

```json
{
  "D1": {
    "cases": 8,
    "horizon_predictions": 64,
    "majority_accuracy": 0.390625
  },
  "H1": {
    "cases": 297,
    "horizon_predictions": 2254,
    "majority_accuracy": 0.433895
  },
  "H4": {
    "cases": 91,
    "horizon_predictions": 705,
    "majority_accuracy": 0.441135
  },
  "M1": {
    "cases": 228,
    "horizon_predictions": 1712,
    "majority_accuracy": 0.412967
  },
  "M15": {
    "cases": 144,
    "horizon_predictions": 1106,
    "majority_accuracy": 0.442134
  },
  "M30": {
    "cases": 331,
    "horizon_predictions": 2522,
    "majority_accuracy": 0.445678
  },
  "M5": {
    "cases": 120,
    "horizon_predictions": 922,
    "majority_accuracy": 0.394794
  },
  "UNKNOWN": {
    "cases": 91,
    "horizon_predictions": 657,
    "majority_accuracy": 0.473364
  }
}
```

## 9. Accuracy by market phase

```json
{
  "DOWN_SWING|CHOP_OR_TRANSITION": {
    "cases": 119,
    "horizon_predictions": 905,
    "majority_accuracy": 0.387845
  },
  "DOWN_SWING|CONTINUATION": {
    "cases": 232,
    "horizon_predictions": 1754,
    "majority_accuracy": 0.415051
  },
  "DOWN_SWING|PULLBACK": {
    "cases": 129,
    "horizon_predictions": 971,
    "majority_accuracy": 0.406797
  },
  "REST|CHOP_OR_TRANSITION": {
    "cases": 119,
    "horizon_predictions": 901,
    "majority_accuracy": 0.425083
  },
  "REST|CONTINUATION": {
    "cases": 131,
    "horizon_predictions": 983,
    "majority_accuracy": 0.412004
  },
  "REST|PULLBACK": {
    "cases": 106,
    "horizon_predictions": 815,
    "majority_accuracy": 0.415951
  },
  "UP_SWING|CHOP_OR_TRANSITION": {
    "cases": 102,
    "horizon_predictions": 776,
    "majority_accuracy": 0.475515
  },
  "UP_SWING|CONTINUATION": {
    "cases": 210,
    "horizon_predictions": 1600,
    "majority_accuracy": 0.475625
  },
  "UP_SWING|PULLBACK": {
    "cases": 162,
    "horizon_predictions": 1237,
    "majority_accuracy": 0.467259
  }
}
```

## 10. Pullback, retest, and continuation conditions

Market-phase rows combine the visible hidden state with prefix-only movement relationship.

## 11. SMC and supply-demand context

Each frozen prediction contains prefix-only trendline, supply/demand, SMC, liquidity,
pullback, continuation, candle-intelligence, and hidden-state evidence.

## 12. Confidence calibration

- Expected calibration error: 0.087674

## 13. Best examples

```json
[
  {
    "cutoff_id": "image-0007-9b5672f4e347-cutoff-0064",
    "score": 0.923077
  },
  {
    "cutoff_id": "image-0048-c07cb1852568-cutoff-0086",
    "score": 0.9
  },
  {
    "cutoff_id": "image-0086-34636f54f40b-cutoff-0067",
    "score": 0.861538
  },
  {
    "cutoff_id": "image-0102-54e503a3b618-cutoff-0076",
    "score": 0.853846
  },
  {
    "cutoff_id": "image-0173-e0fbd54433fd-cutoff-0072",
    "score": 0.84
  },
  {
    "cutoff_id": "image-0174-2ff84101edb9-cutoff-0072",
    "score": 0.84
  },
  {
    "cutoff_id": "image-0175-2ca2f5e8717d-cutoff-0072",
    "score": 0.84
  },
  {
    "cutoff_id": "image-0104-92a26b6eb229-cutoff-0075",
    "score": 0.830769
  },
  {
    "cutoff_id": "image-0101-48cd4fe10a3f-cutoff-0124",
    "score": 0.826087
  },
  {
    "cutoff_id": "image-0147-f0753325f6f2-cutoff-0142",
    "score": 0.826087
  },
  {
    "cutoff_id": "image-0216-21b532c1d060-cutoff-0032",
    "score": 0.823529
  },
  {
    "cutoff_id": "image-0140-578f7f706cfb-cutoff-0058",
    "score": 0.823077
  }
]
```

## 14. Worst examples

```json
[
  {
    "cutoff_id": "image-0318-f0b716d40eea-cutoff-0144",
    "score": 0.043478
  },
  {
    "cutoff_id": "image-0207-dc208f6ecf43-cutoff-0095",
    "score": 0.046154
  },
  {
    "cutoff_id": "image-0304-e33f60fc2177-cutoff-0042",
    "score": 0.058824
  },
  {
    "cutoff_id": "image-0306-c9f1a7008012-cutoff-0042",
    "score": 0.058824
  },
  {
    "cutoff_id": "image-0254-302c7fb5c29f-cutoff-0113",
    "score": 0.06087
  },
  {
    "cutoff_id": "image-0061-b6b06dcdc1e5-cutoff-0096",
    "score": 0.076923
  },
  {
    "cutoff_id": "image-0331-094e5a640135-cutoff-0042",
    "score": 0.078261
  },
  {
    "cutoff_id": "image-0108-84d88524dafa-cutoff-0032",
    "score": 0.084615
  },
  {
    "cutoff_id": "image-0109-113220bb0471-cutoff-0032",
    "score": 0.084615
  },
  {
    "cutoff_id": "image-0123-85eb427f1ab6-cutoff-0113",
    "score": 0.084615
  },
  {
    "cutoff_id": "image-0124-7d3460e37838-cutoff-0113",
    "score": 0.084615
  },
  {
    "cutoff_id": "image-0125-9e1004b3c975-cutoff-0113",
    "score": 0.084615
  }
]
```

## 15. Visual gallery

- Gallery: C:\Users\thaba\OneDrive\Documents\The 808 Vision 2026\phoenixguard\.codex_runtime\pure_masked_future\gallery\index.html

## 16. Disk usage and cleanup

- Run bytes: 1111783771
- Free disk after run: 46.499 GB
- Revealed screenshots are hard-linked per cutoff when supported.

## 17. Scope confirmation

- Screenshot pixels were the sole market input.
- No broker price-history import was used.
- No live transaction, authorization, or broker bridge artifact was created.
- Whole-path dominant accuracy: 49.54%
- Path-class accuracy: 57.86%
- Swing-length MAE: 10.969 candles
