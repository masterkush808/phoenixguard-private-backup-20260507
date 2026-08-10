"""Post-freeze reveal targets and screenshot prediction scoring."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Any, Mapping, Sequence, cast

from phoenixguard.simulation.masked_future_v3 import build_masked_future_target_v3
from phoenixguard.study.masked_future_behavior_v3 import candle_ohlc_v3
from phoenixguard.study.prefix_vision_prediction_v3 import (
    TOKEN_FIELDS,
    candle_geometry_token_v3,
)


MASKED_FUTURE_SCORE_SCHEMA_VERSION = "PG_PURE_MASKED_FUTURE_SCORE_V3"


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _baseline_range(candles: Sequence[Mapping[str, Any]]) -> float:
    rows = [row for candle in candles[-64:] if (row := candle_ohlc_v3(candle)) is not None]
    return float(median(max(1e-9, row[1] - row[2]) for row in rows)) if rows else 1.0


def _path_class(
    target: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    whole = _mapping(target.get("whole_swing"))
    whole_side = str(whole.get("side") or "REST")
    features = _mapping(context.get("features"))
    local_side = str(features.get("state_side") or "REST")
    if whole_side == "REST":
        return "CHOP"
    if bool(target.get("pullback")):
        return "PULLBACK"
    if local_side in {"BUY", "SELL"} and local_side == whole_side:
        return "CONTINUATION"
    if local_side in {"BUY", "SELL"} and local_side != whole_side:
        return "REVERSAL"
    return "CONTINUATION"


def build_revealed_target_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    horizons: Sequence[int],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    target = build_masked_future_target_v3(
        candles,
        cutoff=int(cutoff),
        horizons=horizons,
    )
    prefix = list(candles[: int(cutoff)])
    future = list(candles[int(cutoff) :])
    scale = _baseline_range(prefix)
    tokens: dict[str, Any] = {}
    step_directions: dict[str, str] = {}
    actual_path: list[float] = []
    anchor = candle_ohlc_v3(prefix[-1]) if prefix else None
    anchor_close = anchor[3] if anchor is not None else 0.0
    maximum = min(max((int(value) for value in horizons), default=1), len(future))
    for index, candle in enumerate(future[:maximum], start=1):
        row = candle_ohlc_v3(candle)
        actual_path.append(
            round((row[3] - anchor_close) / max(scale, 1e-9), 6)
            if row is not None
            else actual_path[-1]
            if actual_path
            else 0.0
        )
        if index in horizons:
            token = candle_geometry_token_v3(candle, baseline_range=scale)
            tokens[str(index)] = token
            step_directions[str(index)] = str(token.get("direction") or "REST")
    target["candle_tokens"] = tokens
    target["step_directions"] = step_directions
    target["path_class"] = _path_class(target, context)
    target["actual_normalized_path"] = actual_path
    target["future_suffix_used_by_scorer_only"] = True
    return target


def _token_similarity(predicted: Mapping[str, Any], actual: Mapping[str, Any]) -> float:
    if not predicted or not actual:
        return 0.0
    return sum(
        str(predicted.get(field)) == str(actual.get(field))
        for field in TOKEN_FIELDS
    ) / len(TOKEN_FIELDS)


def score_frozen_prediction_v3(
    prediction: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    reveal_started_epoch_ms: int,
    fold: int,
    source_path: str,
    market_phase: str,
) -> dict[str, Any]:
    frozen_epoch = int(prediction.get("prediction_frozen_epoch_ms", 0) or 0)
    if frozen_epoch <= 0 or frozen_epoch > int(reveal_started_epoch_ms):
        raise ValueError("PG_PREDICTION_WAS_NOT_FROZEN_BEFORE_REVEAL")
    predicted_horizons = _mapping(prediction.get("horizons"))
    actual_majority = _mapping(target.get("horizons"))
    actual_endpoint = _mapping(target.get("endpoint_horizons"))
    actual_steps = _mapping(target.get("step_directions"))
    actual_tokens = _mapping(target.get("candle_tokens"))
    horizon_scores: dict[str, Any] = {}
    correctness: list[float] = []
    for key, raw_prediction in sorted(
        predicted_horizons.items(),
        key=lambda item: int(item[0]),
    ):
        if key not in actual_majority:
            continue
        row = _mapping(raw_prediction)
        predicted_side = str(row.get("predicted_side") or "REST")
        majority = str(actual_majority.get(key) or "REST")
        endpoint = str(actual_endpoint.get(key) or "REST")
        step = str(actual_steps.get(key) or "REST")
        predicted_token = _mapping(row.get("expected_candle_token"))
        actual_token = _mapping(actual_tokens.get(key))
        majority_correct = predicted_side == majority
        endpoint_correct = predicted_side == endpoint
        step_correct = predicted_side == step
        token_similarity = _token_similarity(predicted_token, actual_token)
        correctness.extend(
            (float(majority_correct), float(endpoint_correct), token_similarity)
        )
        horizon_scores[key] = {
            "predicted_side": predicted_side,
            "confidence": round(_number(row.get("confidence")), 6),
            "probabilities": deepcopy_mapping(row.get("probabilities")),
            "actual_majority_side": majority,
            "actual_endpoint_side": endpoint,
            "actual_step_side": step,
            "majority_correct": majority_correct,
            "endpoint_correct": endpoint_correct,
            "step_correct": step_correct,
            "predicted_candle_token": predicted_token,
            "actual_candle_token": actual_token,
            "candle_token_similarity": round(token_similarity, 6),
        }
    path_prediction = _mapping(prediction.get("path_prediction"))
    whole = _mapping(target.get("whole_swing"))
    predicted_path_class = str(path_prediction.get("predicted_path_class") or "CHOP")
    actual_path_class = str(target.get("path_class") or "CHOP")
    predicted_dominant = str(path_prediction.get("dominant_side") or "REST")
    actual_dominant = str(whole.get("side") or "REST")
    path_class_correct = predicted_path_class == actual_path_class
    dominant_correct = predicted_dominant == actual_dominant
    expected_swing = _number(path_prediction.get("expected_swing_candles"))
    actual_swing = _number(whole.get("candles"))
    swing_error = abs(expected_swing - actual_swing)
    correctness.extend((float(path_class_correct), float(dominant_correct)))
    overall = mean(correctness) if correctness else 0.0
    return {
        "schema_version": MASKED_FUTURE_SCORE_SCHEMA_VERSION,
        "image_id": str(prediction.get("image_id") or ""),
        "family_id": str(prediction.get("family_id") or ""),
        "cutoff_id": str(prediction.get("cutoff_id") or ""),
        "fold": int(fold),
        "symbol": str(prediction.get("symbol") or "UNKNOWN"),
        "timeframe": str(prediction.get("timeframe") or "UNKNOWN"),
        "source_path": str(source_path),
        "market_phase": str(market_phase or "UNKNOWN"),
        "prediction_frozen_epoch_ms": frozen_epoch,
        "reveal_started_epoch_ms": int(reveal_started_epoch_ms),
        "prediction_preceded_reveal": True,
        "horizons": horizon_scores,
        "path_score": {
            "predicted_dominant_side": predicted_dominant,
            "actual_dominant_side": actual_dominant,
            "dominant_side_correct": dominant_correct,
            "predicted_path_class": predicted_path_class,
            "actual_path_class": actual_path_class,
            "path_class_correct": path_class_correct,
            "expected_swing_candles": round(expected_swing, 3),
            "actual_swing_candles": int(actual_swing),
            "swing_length_absolute_error": round(swing_error, 6),
            "actual_normalized_path": target.get("actual_normalized_path", []),
        },
        "overall_score": round(overall, 6),
        "future_suffix_used_by_scorer_only": True,
        "folder_label_used_as_target": False,
    }


def deepcopy_mapping(value: object) -> dict[str, Any]:
    return {str(key): item for key, item in _mapping(value).items()}


def _group_accuracy(
    scorecards: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for score in scorecards:
        grouped[str(score.get(field) or "UNKNOWN")].append(score)
    output: dict[str, Any] = {}
    for key, rows in sorted(grouped.items()):
        values: list[float] = []
        for score in rows:
            for raw_horizon in _mapping(score.get("horizons")).values():
                horizon = _mapping(raw_horizon)
                if horizon:
                    values.append(float(bool(horizon.get("majority_correct"))))
        output[key] = {
            "cases": len(rows),
            "horizon_predictions": len(values),
            "majority_accuracy": round(mean(values), 6) if values else None,
        }
    return output


def aggregate_scorecards_v3(
    scorecards: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int],
) -> dict[str, Any]:
    by_horizon: dict[str, Any] = {}
    calibration_rows: list[tuple[float, bool]] = []
    for horizon in sorted({int(value) for value in horizons}):
        key = str(horizon)
        rows = [
            _mapping(_mapping(score.get("horizons")).get(key))
            for score in scorecards
            if _mapping(_mapping(score.get("horizons")).get(key))
        ]
        majority = [float(bool(row.get("majority_correct"))) for row in rows]
        endpoint = [float(bool(row.get("endpoint_correct"))) for row in rows]
        step = [float(bool(row.get("step_correct"))) for row in rows]
        tokens = [_number(row.get("candle_token_similarity")) for row in rows]
        for row in rows:
            calibration_rows.append(
                (_number(row.get("confidence")), bool(row.get("majority_correct")))
            )
        by_horizon[key] = {
            "scored": len(rows),
            "majority_direction_accuracy": round(mean(majority), 6) if majority else None,
            "endpoint_direction_accuracy": round(mean(endpoint), 6) if endpoint else None,
            "step_direction_accuracy": round(mean(step), 6) if step else None,
            "candle_token_similarity": round(mean(tokens), 6) if tokens else None,
        }
    reliability: list[dict[str, Any]] = []
    ece = 0.0
    for index in range(10):
        low, high = index / 10.0, (index + 1) / 10.0
        bucket = [
            row for row in calibration_rows
            if row[0] >= low and (row[0] < high or index == 9 and row[0] <= high)
        ]
        if not bucket:
            continue
        confidence = mean(row[0] for row in bucket)
        accuracy = mean(float(row[1]) for row in bucket)
        ece += len(bucket) / max(1, len(calibration_rows)) * abs(confidence - accuracy)
        reliability.append(
            {
                "low": low,
                "high": high,
                "rows": len(bucket),
                "confidence": round(confidence, 6),
                "accuracy": round(accuracy, 6),
            }
        )
    path_rows = [_mapping(score.get("path_score")) for score in scorecards]
    best = sorted(scorecards, key=lambda row: _number(row.get("overall_score")), reverse=True)[:12]
    worst = sorted(scorecards, key=lambda row: _number(row.get("overall_score")))[:12]
    return {
        "scorecard_count": len(scorecards),
        "by_horizon": by_horizon,
        "whole_path": {
            "dominant_direction_accuracy": round(
                mean(float(bool(row.get("dominant_side_correct"))) for row in path_rows),
                6,
            ) if path_rows else None,
            "path_class_accuracy": round(
                mean(float(bool(row.get("path_class_correct"))) for row in path_rows),
                6,
            ) if path_rows else None,
            "swing_length_mae_candles": round(
                mean(_number(row.get("swing_length_absolute_error")) for row in path_rows),
                6,
            ) if path_rows else None,
        },
        "by_pair": _group_accuracy(scorecards, "symbol"),
        "by_timeframe": _group_accuracy(scorecards, "timeframe"),
        "by_market_phase": _group_accuracy(scorecards, "market_phase"),
        "calibration": {
            "expected_calibration_error": round(ece, 6),
            "reliability": reliability,
        },
        "best_examples": [
            {"cutoff_id": row.get("cutoff_id"), "score": row.get("overall_score")}
            for row in best
        ],
        "worst_examples": [
            {"cutoff_id": row.get("cutoff_id"), "score": row.get("overall_score")}
            for row in worst
        ],
    }


__all__ = [
    "MASKED_FUTURE_SCORE_SCHEMA_VERSION",
    "aggregate_scorecards_v3",
    "build_revealed_target_v3",
    "score_frozen_prediction_v3",
]
