"""Prefix-only PhoenixGuard skill extraction and future prediction."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence, cast

from phoenixguard.simulation.masked_future_v3 import extract_image_sequence_v3
from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.latent_state_discovery_v3 import build_latent_state_discovery_v3
from phoenixguard.study.masked_future_behavior_v3 import (
    MaskedFutureBehaviorModelV3,
    build_masked_future_context_v3,
    candle_ohlc_v3,
    finalize_masked_future_model_v3,
    new_masked_future_model_artifact_v3,
    update_masked_future_model_v3,
)
from phoenixguard.study.masked_image_region_v3 import MaskRectangleV3


PREFIX_VISION_STUDY_SCHEMA_VERSION = "PG_PREFIX_VISION_STUDY_V3"
PURE_PREDICTION_SCHEMA_VERSION = "PG_PURE_MASKED_FUTURE_PREDICTION_V3"
TOKEN_FIELDS: tuple[str, ...] = (
    "direction",
    "body_bucket",
    "upper_wick_bucket",
    "lower_wick_bucket",
    "range_bucket",
)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [
        _mapping(cast(Mapping[str, Any], row))
        for row in cast(Sequence[object], value)
        if isinstance(row, Mapping)
    ]


def _number(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _timeframe_seconds(value: object) -> int:
    text = str(value or "").strip().upper()
    units = {"M": 60, "H": 3600, "D": 86400, "W": 604800, "MN": 2592000}
    for unit in ("MN", "M", "H", "D", "W"):
        if text.startswith(unit) and text[len(unit) :].isdigit():
            return int(text[len(unit) :]) * units[unit]
    return 1


def _price_rows(candles: Sequence[Mapping[str, Any]]) -> list[tuple[float, float, float, float]]:
    return [row for candle in candles if (row := candle_ohlc_v3(candle)) is not None]


def _scale(candles: Sequence[Mapping[str, Any]]) -> float:
    ranges = [max(1e-9, high - low) for _, high, low, _ in _price_rows(candles[-64:])]
    return float(median(ranges)) if ranges else 1.0


def _bucket(value: float, edges: tuple[float, float, float]) -> str:
    if value < edges[0]:
        return "TINY"
    if value < edges[1]:
        return "SMALL"
    if value < edges[2]:
        return "MEDIUM"
    return "LARGE"


def candle_geometry_token_v3(
    candle: Mapping[str, Any],
    *,
    baseline_range: float,
) -> dict[str, str]:
    row = candle_ohlc_v3(candle)
    if row is None:
        return {field: "UNKNOWN" for field in TOKEN_FIELDS}
    open_value, high, low, close = row
    span = max(1e-9, high - low)
    body = abs(close - open_value)
    upper = max(0.0, high - max(open_value, close))
    lower = max(0.0, min(open_value, close) - low)
    direction = "BUY" if close > open_value else "SELL" if close < open_value else "REST"
    relative_range = span / max(1e-9, baseline_range)
    range_bucket = (
        "COMPRESSED"
        if relative_range < 0.70
        else "NORMAL"
        if relative_range < 1.35
        else "EXPANDED"
    )
    return {
        "direction": direction,
        "body_bucket": _bucket(body / span, (0.12, 0.32, 0.62)),
        "upper_wick_bucket": _bucket(upper / span, (0.08, 0.22, 0.42)),
        "lower_wick_bucket": _bucket(lower / span, (0.08, 0.22, 0.42)),
        "range_bucket": range_bucket,
    }


def _pivot_context(candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = _price_rows(candles[-96:])
    if len(rows) < 8:
        return {"status": "INSUFFICIENT_HISTORY", "demand_zones": [], "supply_zones": []}
    scale = float(median(max(1e-9, high - low) for _, high, low, _ in rows))
    highs = [row[1] for row in rows]
    lows = [row[2] for row in rows]
    closes = [row[3] for row in rows]
    demand: list[dict[str, Any]] = []
    supply: list[dict[str, Any]] = []
    for index in range(2, len(rows) - 2):
        if lows[index] <= min(lows[index - 2 : index + 3]):
            touches = sum(abs(value - lows[index]) <= 0.35 * scale for value in lows)
            demand.append(
                {
                    "index": index,
                    "distance_ranges": round((closes[-1] - lows[index]) / scale, 6),
                    "touches": touches,
                    "wick_anchor": True,
                }
            )
        if highs[index] >= max(highs[index - 2 : index + 3]):
            touches = sum(abs(value - highs[index]) <= 0.35 * scale for value in highs)
            supply.append(
                {
                    "index": index,
                    "distance_ranges": round((highs[index] - closes[-1]) / scale, 6),
                    "touches": touches,
                    "wick_anchor": True,
                }
            )
    demand.sort(key=lambda row: abs(_number(row.get("distance_ranges"))))
    supply.sort(key=lambda row: abs(_number(row.get("distance_ranges"))))
    return {
        "status": "ACTIVE",
        "demand_zones": demand[:4],
        "supply_zones": supply[:4],
        "nearest_demand_ranges": demand[0]["distance_ranges"] if demand else None,
        "nearest_supply_ranges": supply[0]["distance_ranges"] if supply else None,
        "prefix_only": True,
    }


def _smc_context(candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = _price_rows(candles[-64:])
    if len(rows) < 10:
        return {"status": "INSUFFICIENT_HISTORY"}
    scale = float(median(max(1e-9, high - low) for _, high, low, _ in rows))
    prior = rows[-13:-1]
    prior_high = max(row[1] for row in prior)
    prior_low = min(row[2] for row in prior)
    open_value, high, low, close = rows[-1]
    sweep_high = high > prior_high and close < prior_high
    sweep_low = low < prior_low and close > prior_low
    break_high = close > prior_high
    break_low = close < prior_low
    body_ranges = abs(close - open_value) / max(scale, 1e-9)
    equal_highs = sum(abs(row[1] - prior_high) <= 0.20 * scale for row in prior)
    equal_lows = sum(abs(row[2] - prior_low) <= 0.20 * scale for row in prior)
    return {
        "status": "ACTIVE",
        "liquidity_sweep": "HIGH" if sweep_high else "LOW" if sweep_low else "NONE",
        "structure_break": "BUY" if break_high else "SELL" if break_low else "NONE",
        "displacement": body_ranges >= 0.85,
        "latest_body_ranges": round(body_ranges, 6),
        "equal_high_liquidity_count": equal_highs,
        "equal_low_liquidity_count": equal_lows,
        "prefix_only": True,
    }


def _movement_context(
    candles: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    rows = _price_rows(candles[-32:])
    features = _mapping(context.get("features"))
    if len(rows) < 3:
        return {"status": "INSUFFICIENT_HISTORY"}
    scale = _scale(candles)
    closes = [row[3] for row in rows]
    local_delta = (closes[-1] - closes[max(0, len(closes) - 4)]) / scale
    major_delta = (closes[-1] - closes[0]) / scale
    local_side = "BUY" if local_delta > 0.20 else "SELL" if local_delta < -0.20 else "REST"
    major_side = "BUY" if major_delta > 0.50 else "SELL" if major_delta < -0.50 else "REST"
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
    efficiency = abs(closes[-1] - closes[0]) / path if path > 1e-9 else 0.0
    return {
        "status": "ACTIVE",
        "major_side": major_side,
        "local_side": local_side,
        "relationship": (
            "PULLBACK"
            if major_side in {"BUY", "SELL"} and local_side in {"BUY", "SELL"} and major_side != local_side
            else "CONTINUATION"
            if major_side == local_side and major_side != "REST"
            else "CHOP_OR_TRANSITION"
        ),
        "efficiency": round(efficiency, 6),
        "state": features.get("state"),
        "state_side": features.get("state_side"),
        "scale_conflict": features.get("scale_conflict"),
        "prefix_only": True,
    }


def build_prefix_vision_study_v3(
    masked_image_path: str | Path,
    *,
    rectangle: MaskRectangleV3,
    image_id: str,
    symbol: object,
    timeframe: object,
    minimum_prefix_candles: int = 16,
) -> dict[str, Any]:
    record = extract_image_sequence_v3(
        masked_image_path,
        source_bucket="UNLABELED",
        maximum_width=0,
        symbol_hint=symbol,
        timeframe_hint=timeframe,
        skip_ocr=True,
    )
    visible = [
        candle
        for candle in record.candles
        if _number(candle.get("center_x_px"), -1.0) < float(rectangle.x1)
    ]
    if len(visible) < int(minimum_prefix_candles):
        raise ValueError(
            f"PG_MASKED_PREFIX_INSUFFICIENT_CANDLES: {len(visible)} < {minimum_prefix_candles}"
        )
    candle_study = analyze_candle_sequence_v3(
        visible[-128:],
        regime="UNKNOWN",
        require_closed=True,
        max_candles=128,
    )
    if str(candle_study.get("status")) != "STUDIED":
        raise ValueError("PG_MASKED_PREFIX_CANDLE_STUDY_FAILED")
    studied = _rows(candle_study.get("candles"))
    behavior = measure_market_behavior_v3(
        candle_study,
        timeframe_seconds=_timeframe_seconds(timeframe),
        max_candles=128,
        inner_window=min(8, len(studied)),
    )
    context = build_masked_future_context_v3(
        studied,
        behavior,
        symbol=symbol,
        timeframe=timeframe,
    )
    latent = build_latent_state_discovery_v3(
        candles=studied,
        behavior=behavior,
        pair_profile={},
        advanced_studies={},
        research_studies={},
        symbol=str(symbol or "UNKNOWN").upper(),
        timeframe=str(timeframe or "UNKNOWN").upper(),
        timeframe_seconds=_timeframe_seconds(timeframe),
    )
    supply_demand = _pivot_context(studied)
    smc = _smc_context(studied)
    movement = _movement_context(studied, context)
    feature_digest = hashlib.sha256(
        json.dumps(
            {
                "masked_image_hash": record.image_hash,
                "context_digest": context.get("feature_digest"),
                "prefix_ids": [str(row.get("candle_id")) for row in visible],
                "trendline": context.get("trendline_geometry"),
                "supply_demand": supply_demand,
                "smc": smc,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    last_raw = visible[-1]
    return {
        "schema_version": PREFIX_VISION_STUDY_SCHEMA_VERSION,
        "image_id": str(image_id),
        "masked_image_hash": record.image_hash,
        "visible_prefix_candle_count": len(visible),
        "symbol": str(symbol or record.symbol or "UNKNOWN").upper(),
        "timeframe": str(timeframe or record.timeframe or "UNKNOWN").upper(),
        "feature_digest": feature_digest,
        "context": context,
        "studied_candles": studied,
        "anchor_y_px": _number(last_raw.get("close_y_px")),
        "baseline_range_px": _scale(studied),
        "skill_evidence": {
            "candle_intelligence": deepcopy(_mapping(candle_study.get("summary"))),
            "trendline_context": deepcopy(_mapping(context.get("trendline_geometry"))),
            "supply_demand_context": supply_demand,
            "smc_context": smc,
            "pullback_context": movement,
            "continuation_context": movement,
            "hidden_state_context": deepcopy(_mapping(latent.get("hidden_state"))),
            "hidden_state_distribution": deepcopy(_mapping(latent.get("next_state_distribution"))),
            "memory_context": {"status": "FILLED_BY_GROUPED_MODEL_AFTER_FIT"},
        },
        "input_contract": {
            "masked_image_only": True,
            "future_pixels_available": False,
            "folder_label_available": False,
            "broker_price_data_available": False,
        },
    }


def _context_keys(context: Mapping[str, Any]) -> list[str]:
    values = context.get("context_keys")
    keys = [str(value) for value in cast(Sequence[Any], values or [])]
    keys.append("__GLOBAL__")
    return list(dict.fromkeys(keys))


def _token_key(token: Mapping[str, Any]) -> str:
    return json.dumps(
        {field: str(token.get(field) or "UNKNOWN") for field in TOKEN_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_token(value: str) -> dict[str, str]:
    payload = json.loads(value)
    row = _mapping(payload)
    return {field: str(row.get(field) or "UNKNOWN") for field in TOKEN_FIELDS}


@dataclass
class PrefixVisionPredictionModelV3:
    behavior_model: MaskedFutureBehaviorModelV3
    horizons: tuple[int, ...]
    token_counts: dict[str, dict[str, dict[str, int]]]
    path_counts: dict[str, dict[str, int]]

    @classmethod
    def fit(
        cls,
        training_rows: Sequence[Mapping[str, Any]],
        *,
        horizons: Sequence[int],
    ) -> "PrefixVisionPredictionModelV3":
        canonical_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
        behavior_artifact = new_masked_future_model_artifact_v3(canonical_horizons)
        token_counters: dict[str, dict[str, Counter[str]]] = {}
        path_counters: dict[str, Counter[str]] = {}
        for row in training_rows:
            context = _mapping(row.get("context"))
            target = _mapping(row.get("target"))
            update_masked_future_model_v3(behavior_artifact, context, target)
            tokens = _mapping(target.get("candle_tokens"))
            path_class = str(target.get("path_class") or "CHOP")
            for context_key in _context_keys(context):
                path_counters.setdefault(context_key, Counter())[path_class] += 1
                for horizon in canonical_horizons:
                    token = _mapping(tokens.get(str(horizon)))
                    if not token:
                        continue
                    token_counters.setdefault(str(horizon), {}).setdefault(
                        context_key,
                        Counter(),
                    )[_token_key(token)] += 1
        behavior_artifact["training"] = {
            "example_count": len(training_rows),
            "screenshot_prefix_only": True,
        }
        finalized = finalize_masked_future_model_v3(behavior_artifact)
        return cls(
            behavior_model=MaskedFutureBehaviorModelV3(finalized),
            horizons=canonical_horizons,
            token_counts={
                horizon: {
                    context_key: dict(counter)
                    for context_key, counter in contexts.items()
                }
                for horizon, contexts in token_counters.items()
            },
            path_counts={key: dict(counter) for key, counter in path_counters.items()},
        )

    @classmethod
    def from_behavior_model(
        cls,
        model: MaskedFutureBehaviorModelV3,
        *,
        horizons: Sequence[int],
    ) -> "PrefixVisionPredictionModelV3":
        return cls(model, tuple(sorted({int(value) for value in horizons})), {}, {})

    def _token_prediction(
        self,
        context: Mapping[str, Any],
        horizon: int,
        direction: str,
    ) -> tuple[dict[str, str], int, str]:
        contexts = self.token_counts.get(str(horizon), {})
        for context_key in _context_keys(context):
            counts = contexts.get(context_key, {})
            support = sum(int(value) for value in counts.values())
            if support >= 3 or context_key == "__GLOBAL__" and support:
                winner = max(counts, key=lambda key: (int(counts[key]), key))
                token = _decode_token(winner)
                token["direction"] = direction
                return token, support, context_key
        return {
            "direction": direction,
            "body_bucket": "MEDIUM",
            "upper_wick_bucket": "SMALL",
            "lower_wick_bucket": "SMALL",
            "range_bucket": "NORMAL",
        }, 0, "NO_TOKEN_SUPPORT"

    def _path_prediction(self, context: Mapping[str, Any]) -> tuple[str, int, str]:
        for context_key in _context_keys(context):
            counts = self.path_counts.get(context_key, {})
            support = sum(int(value) for value in counts.values())
            if support >= 3 or context_key == "__GLOBAL__" and support:
                return max(counts, key=lambda key: (int(counts[key]), key)), support, context_key
        return "CHOP", 0, "NO_PATH_SUPPORT"

    def predict(
        self,
        study: Mapping[str, Any],
        *,
        image_id: str,
        family_id: str,
        cutoff_id: str,
        anchor_index: int,
        hidden_future_candles: int,
    ) -> dict[str, Any]:
        context = _mapping(study.get("context"))
        behavior = self.behavior_model.predict_context(context)
        behavior_horizons = {
            int(_number(row.get("candles"), 0.0)): row
            for row in _rows(behavior.get("horizons"))
        }
        horizon_payload: dict[str, Any] = {}
        sequence_tokens: list[dict[str, Any]] = []
        for horizon in self.horizons:
            row = behavior_horizons.get(horizon, {})
            direction = str(row.get("predicted_side") or "REST")
            probabilities = _mapping(row.get("probabilities"))
            confidence = _number(probabilities.get(direction), 0.0)
            token, token_support, token_context = self._token_prediction(
                context,
                horizon,
                direction,
            )
            horizon_payload[str(horizon)] = {
                "predicted_side": direction,
                "confidence": round(confidence, 6),
                "probabilities": {
                    side: round(_number(probabilities.get(side), 0.0), 6)
                    for side in ("BUY", "SELL", "REST")
                },
                "support": int(_number(row.get("support"), 0.0)),
                "expected_candle_token": token,
                "token_support": token_support,
                "token_context": token_context,
            }
            sequence_tokens.append({"horizon": horizon, **token})
        whole = _mapping(behavior.get("whole_swing"))
        path_class, path_support, path_context = self._path_prediction(context)
        skill_evidence = deepcopy(_mapping(study.get("skill_evidence")))
        skill_evidence["memory_context"] = {
            "matched_context": whole.get("matched_context"),
            "support": whole.get("support", 0),
            "path_context": path_context,
            "path_support": path_support,
            "grouped_training_only": True,
        }
        dominant_side = str(whole.get("predicted_side") or "REST")
        return {
            "schema_version": PURE_PREDICTION_SCHEMA_VERSION,
            "image_id": str(image_id),
            "family_id": str(family_id),
            "cutoff_id": str(cutoff_id),
            "prediction_anchor_candle_index": int(anchor_index),
            "visible_prefix_candle_count": int(
                _number(study.get("visible_prefix_candle_count"), 0.0)
            ),
            "hidden_future_candle_count": int(hidden_future_candles),
            "symbol": str(study.get("symbol") or "UNKNOWN"),
            "timeframe": str(study.get("timeframe") or "UNKNOWN"),
            "feature_digest": str(study.get("feature_digest") or ""),
            "prediction_frozen_epoch_ms": 0,
            "horizons": horizon_payload,
            "path_prediction": {
                "dominant_side": dominant_side,
                "expected_swing_candles": round(
                    _number(whole.get("expected_candles"), 0.0),
                    3,
                ),
                "expected_pullback": path_class == "PULLBACK",
                "expected_reversal": path_class == "REVERSAL",
                "expected_continuation": path_class == "CONTINUATION",
                "expected_chop": path_class == "CHOP",
                "predicted_path_class": path_class,
                "path_class_support": path_support,
                "sequence_tokens": sequence_tokens,
            },
            "skill_evidence": skill_evidence,
            "causal_contract": {
                "masked_image_only": True,
                "prediction_precedes_reveal": True,
                "future_pixels_in_features": False,
                "folder_label_in_features": False,
            },
        }


__all__ = [
    "PREFIX_VISION_STUDY_SCHEMA_VERSION",
    "PURE_PREDICTION_SCHEMA_VERSION",
    "PrefixVisionPredictionModelV3",
    "build_prefix_vision_study_v3",
    "candle_geometry_token_v3",
]
