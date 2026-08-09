# PhoenixGuard V3 Masked-Future Replay Blueprint

## Objective

Measure whether V3 can predict unseen candle behavior across every historical BUY/SELL image without seeing future candles, future overlays, annotations, trade outcomes, or folder labels.

This is hidden-state discovery, not a strategy backtest. The output is a distribution over BUY, SELL, and REST behavior plus a whole-swing candle horizon. It never grants entry or execution permission.

## Causal replay contract

1. Discover every image under the canonical BUY and SELL memory banks.
2. Run the same adaptive candle palette extractor used by the live tracker.
3. Resolve pair/timeframe from filenames first, then from a bounded RapidOCR title/header crop; OCR text never becomes a directional feature.
4. Convert pixels to canonical V3 candle geometry and discard all image annotations.
5. Select a historical cutoff and expose only candles left of that cutoff.
6. Run V3 candle intelligence, behavior segmentation, wick trendline geometry, and the masked-future posterior on that prefix.
7. Freeze the prediction and its feature digest.
8. Reveal the withheld suffix only to the scorer.
9. Score majority direction, endpoint direction, whole-swing direction, pullback cases, and horizon error.
10. Keep all cutoffs from one image or near-duplicate image family in one validation fold.
11. Train the final compact artifact only after grouped out-of-sample scoring is complete.

## Leakage exclusions

- BUY/SELL folder names are provenance, never labels.
- Future candle pixels are never feature inputs.
- Future trendlines, arrows, indicators, and text are never feature inputs.
- Before/after or near-duplicate screenshots cannot cross folds.
- A prediction row records a prefix feature digest before its target is revealed.
- Raw screenshots are not copied into a generated dataset.

## Targets

Fixed horizons are 3, 5, 8, 13, 21, and 34 candles. The primary target is the majority candle direction requested by the operator. Endpoint direction is reported separately.

The variable whole-swing target includes rests. It identifies the dominant future direction and the candle index of its observed path extreme. A two- or three-candle move opposite the dominant swing is recorded as a pullback case rather than being allowed to redefine the entire swing.

Two pullback measurements remain separate. `future_counter_move` asks the harder pre-pullback question before counter candles exist. `visible_pullback_resolution` asks the operator's actual question after a two-to-four-candle local leg is visible and conflicts with the longer-scale state. Live promotion requires the visible-pullback score to beat its baseline.

## Model

The model is a bounded hierarchical empirical-Bayes context model. Context includes current/previous hidden state, state age, major and inner trend, recent multi-scale momentum, expansion/compression, path efficiency, candle interaction, and strict three-touch wick trendline geometry. Pair/timeframe evidence backs off to global evidence when pair support is sparse.

The artifact is gzip-compressed JSON with pruned low-support contexts. It has no neural checkpoints and no duplicated images.

## Promotion

The artifact is always written as diagnostic evidence. It changes live hidden-state control only when grouped out-of-sample 13/21-candle accuracy exceeds both 52 percent and the existing local-state baseline over at least 500 scored cases. This is an evidence promotion boundary, not a trade blocker.

## Disk contract

The runner refuses to begin or write when the selected drive would fall below 45 GB free. It stores one resumable compact candle ledger, one compressed prediction ledger, one summary, and one compressed model. It never emits masked image copies.

## Command

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'Backend\src')
.\.venv-live\Scripts\python.exe Backend\tools\run_masked_future_v3.py
```

The command exits with `PG_DISK_RESERVE_BLOCKED` before training if the 45 GB floor is not available.
