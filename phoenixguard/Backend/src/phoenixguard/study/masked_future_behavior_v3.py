"""Leak-fenced, variable-horizon behavior evidence for PhoenixGuard V3.

The model in this module is deliberately small and non-executing. It learns
empirical state/path distributions from candle prefixes, never from trade
folder labels, and exposes BUY/SELL components plus a whole-swing horizon.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import median
from typing import Any, cast


MASKED_FUTURE_MODEL_SCHEMA_VERSION = "PG_MASKED_FUTURE_BEHAVIOR_MODEL_V3"
MASKED_FUTURE_EVIDENCE_SCHEMA_VERSION = "PG_MASKED_FUTURE_BEHAVIOR_EVIDENCE_V3"
DEFAULT_MASKED_FUTURE_MODEL_NAME = "V3_MASKED_FUTURE_BEHAVIOR_MODEL.json.gz"
DEFAULT_HORIZONS = (3, 5, 8, 13, 21, 34)
DIRECTION_SIDES = ("BUY", "SELL", "REST")


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _number(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _clip01(value: object) -> float:
    return max(0.0, min(1.0, _number(value)))


def _token(value: object, fallback: str = "UNKNOWN") -> str:
    text = re.sub(r"[^A-Z0-9_*>.-]+", "_", str(value or "").strip().upper())
    return text[:96] or fallback


def _state_side(value: object) -> str:
    text = _token(value)
    if text in {"UP", "UP_SWING", "BUY", "BULL", "BULLISH"}:
        return "BUY"
    if text in {"DOWN", "DOWN_SWING", "SELL", "BEAR", "BEARISH"}:
        return "SELL"
    return "REST"


def _timeframe_seconds(value: object) -> int:
    text = _token(value, "")
    aliases = {"DAILY": "D1", "WEEKLY": "W1", "MONTHLY": "MN1"}
    text = aliases.get(text, text)
    match = re.fullmatch(r"(M|H|D|W|MN)(\d+)", text)
    if not match:
        return 0
    unit, amount_text = match.groups()
    amount = max(1, int(amount_text))
    multiplier = {"M": 60, "H": 3600, "D": 86400, "W": 604800, "MN": 2592000}[unit]
    return amount * multiplier


def candle_ohlc_v3(row: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    """Return price-axis OHLC from either price values or pixel geometry."""

    payload = _mapping(row.get("ohlc"))
    if all(name in payload for name in ("open", "high", "low", "close")):
        values = tuple(_number(payload.get(name), float("nan")) for name in ("open", "high", "low", "close"))
        if all(math.isfinite(value) for value in values):
            return cast(tuple[float, float, float, float], values)
    if all(name in row for name in ("open", "high", "low", "close")):
        values = tuple(_number(row.get(name), float("nan")) for name in ("open", "high", "low", "close"))
        if all(math.isfinite(value) for value in values):
            return cast(tuple[float, float, float, float], values)
    source = _mapping(row.get("source_values"))
    merged = {**row, **source}
    names = ("open_y_px", "wick_top_px", "wick_bottom_px", "close_y_px")
    if all(name in merged for name in names):
        open_y, high_y, low_y, close_y = (_number(merged.get(name), float("nan")) for name in names)
        if all(math.isfinite(value) for value in (open_y, high_y, low_y, close_y)):
            return -open_y, -high_y, -low_y, -close_y
    return None


def _ratio(row: Mapping[str, Any], name: str) -> float:
    return _number(_mapping(row.get("ratios")).get(name), 0.0)


def _bucket(value: float, edges: Sequence[float], labels: Sequence[str]) -> str:
    for edge, label in zip(edges, labels):
        if value <= edge:
            return label
    return labels[-1]


def _direction_bucket(delta_in_ranges: float) -> str:
    if delta_in_ranges >= 0.35:
        return "BUY"
    if delta_in_ranges <= -0.35:
        return "SELL"
    return "REST"


def _fit_wick_line(points: Sequence[tuple[int, float]], *, current_index: int, scale: float) -> dict[str, Any]:
    selected = list(points)[-24:]
    if len(selected) < 3:
        return {"valid": False, "touches": len(selected), "span_bars": 0}
    slopes: list[float] = []
    for left_index, (left_x, left_y) in enumerate(selected):
        for right_x, right_y in selected[left_index + 1 :]:
            if right_x != left_x:
                slopes.append((right_y - left_y) / float(right_x - left_x))
    if not slopes:
        return {"valid": False, "touches": len(selected), "span_bars": 0}
    slope = float(median(slopes))
    intercept = float(median([y - slope * x for x, y in selected]))
    tolerance = max(1e-9, float(scale) * 0.22)
    touches = [(x, y) for x, y in selected if abs(y - (slope * x + intercept)) <= tolerance]
    span = max((x for x, _ in touches), default=0) - min((x for x, _ in touches), default=0)
    valid = len(touches) >= 3 and span >= 5
    normalized_slope = slope / max(float(scale), 1e-9)
    slope_class = "UP" if normalized_slope > 0.035 else "DOWN" if normalized_slope < -0.035 else "FLAT"
    return {
        "valid": valid,
        "touches": len(touches),
        "span_bars": int(span),
        "slope_in_ranges": round(normalized_slope, 6),
        "slope_class": slope_class,
        "value_now": slope * current_index + intercept,
        "anchor_source": "WICK_EXTREMA_ONLY",
    }


def build_wick_trendline_context_v3(candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build causal trendline context from three-touch wick extrema."""

    rows = [item for item in (candle_ohlc_v3(row) for row in candles[-96:]) if item is not None]
    if len(rows) < 8:
        return {"status": "INSUFFICIENT_HISTORY", "class": "UNPROVEN"}
    highs = [row[1] for row in rows]
    lows = [row[2] for row in rows]
    closes = [row[3] for row in rows]
    ranges = [max(1e-9, row[1] - row[2]) for row in rows]
    scale = float(median(ranges))
    high_pivots: list[tuple[int, float]] = []
    low_pivots: list[tuple[int, float]] = []
    for index in range(2, len(rows) - 2):
        if highs[index] >= max(highs[index - 2 : index + 3]):
            high_pivots.append((index, highs[index]))
        if lows[index] <= min(lows[index - 2 : index + 3]):
            low_pivots.append((index, lows[index]))
    upper = _fit_wick_line(high_pivots, current_index=len(rows) - 1, scale=scale)
    lower = _fit_wick_line(low_pivots, current_index=len(rows) - 1, scale=scale)
    current = closes[-1]
    upper_distance = (
        (_number(upper.get("value_now")) - current) / scale if upper.get("valid") else None
    )
    lower_distance = (
        (current - _number(lower.get("value_now"))) / scale if lower.get("valid") else None
    )
    if upper.get("valid") and lower.get("valid"):
        upper_slope = str(upper.get("slope_class"))
        lower_slope = str(lower.get("slope_class"))
        line_class = upper_slope if upper_slope == lower_slope else "CONVERGING_OR_DIVERGING"
    elif upper.get("valid"):
        line_class = f"UPPER_{upper.get('slope_class')}"
    elif lower.get("valid"):
        line_class = f"LOWER_{lower.get('slope_class')}"
    else:
        line_class = "UNPROVEN"
    relation = "MID_CHANNEL"
    if upper_distance is not None and abs(upper_distance) <= 0.30:
        relation = "AT_UPPER_WICK_LINE"
    elif lower_distance is not None and abs(lower_distance) <= 0.30:
        relation = "AT_LOWER_WICK_LINE"
    elif upper_distance is not None and upper_distance < -0.30:
        relation = "ABOVE_UPPER_WICK_LINE"
    elif lower_distance is not None and lower_distance < -0.30:
        relation = "BELOW_LOWER_WICK_LINE"
    return {
        "status": "PROVEN" if upper.get("valid") or lower.get("valid") else "UNPROVEN",
        "class": line_class,
        "relation": relation,
        "upper": upper,
        "lower": lower,
        "upper_distance_ranges": round(upper_distance, 6) if upper_distance is not None else None,
        "lower_distance_ranges": round(lower_distance, 6) if lower_distance is not None else None,
        "minimum_touches": 3,
        "minimum_span_bars": 5,
    }


def build_masked_future_context_v3(
    candles: Sequence[Mapping[str, Any]],
    behavior: Mapping[str, Any],
    *,
    symbol: object,
    timeframe: object,
) -> dict[str, Any]:
    """Create a bounded context using only the supplied visible prefix."""

    rows = list(candles)[-128:]
    ohlc = [item for item in (candle_ohlc_v3(row) for row in rows) if item is not None]
    current = _mapping(behavior.get("current_state"))
    segments = _rows(behavior.get("segments"))
    latest_segment = segments[-1] if segments else {}
    state = _token(current.get("state"), "REST")
    previous_state = _token(latest_segment.get("previous_state"), "NONE")
    age = max(0, int(_number(current.get("candle_count"), 0.0)))
    age_bucket = _bucket(float(age), (1, 2, 4, 8, float("inf")), ("1", "2", "3_4", "5_8", "9_PLUS"))
    major = _mapping(behavior.get("major_trend"))
    inner = _mapping(behavior.get("inner_trend"))
    state_ngram = ">".join(_token(row.get("state"), "REST") for row in segments[-4:]) or state
    ranges = [max(1e-9, row[1] - row[2]) for row in ohlc]
    scale = float(median(ranges)) if ranges else 1.0
    closes = [row[3] for row in ohlc]

    def momentum(period: int) -> str:
        if len(closes) <= period:
            return "REST"
        return _direction_bucket((closes[-1] - closes[-period - 1]) / max(scale, 1e-9))

    momentum_3 = momentum(3)
    momentum_8 = momentum(8)
    momentum_21 = momentum(21)
    state_side = _state_side(state)
    major_side = _state_side(major.get("direction") or major.get("label"))
    inner_side = _state_side(inner.get("direction") or inner.get("label"))
    long_votes = [major_side, major_side, momentum_21, momentum_8, inner_side]
    buy_votes = sum(value == "BUY" for value in long_votes)
    sell_votes = sum(value == "SELL" for value in long_votes)
    long_side = "BUY" if buy_votes > sell_votes else "SELL" if sell_votes > buy_votes else "REST"
    scale_conflict = (
        f"LOCAL_{state_side}_COUNTER_TO_{long_side}"
        if state_side in {"BUY", "SELL"} and long_side in {"BUY", "SELL"} and state_side != long_side
        else "ALIGNED_OR_UNRESOLVED"
    )

    recent_range = float(median(ranges[-8:])) if ranges else scale
    prior_range = float(median(ranges[:-8])) if len(ranges) > 8 else scale
    volatility_ratio = recent_range / max(prior_range, 1e-9)
    volatility = _bucket(volatility_ratio, (0.72, 0.95, 1.20, 1.60, float("inf")), ("COMPRESSED", "QUIET", "NORMAL", "EXPANDING", "EXTREME"))
    efficiency = _number(latest_segment.get("path_efficiency"), 0.0)
    efficiency_bucket = _bucket(efficiency, (0.25, 0.55, 0.78, float("inf")), ("LOW", "MIXED", "ORDERED", "DIRECT"))
    latest = rows[-1] if rows else {}
    interaction = _mapping(latest.get("interaction"))
    rejection = _mapping(interaction.get("rejection"))
    acceptance = _mapping(interaction.get("acceptance"))
    latest_signal = "REJECT_" + _token(rejection.get("side"), "NONE") if rejection.get("detected") else "ACCEPT_" + _token(acceptance.get("side"), "NONE") if acceptance.get("detected") else "NEUTRAL"
    trendline = build_wick_trendline_context_v3(rows)
    features = {
        "state": state,
        "state_side": state_side,
        "previous_state": previous_state,
        "age_bucket": age_bucket,
        "major": _token(major.get("direction") or major.get("label"), "SIDEWAYS"),
        "inner": _token(inner.get("direction") or inner.get("label"), "SIDEWAYS"),
        "state_ngram": state_ngram,
        "momentum_3": momentum_3,
        "momentum_8": momentum_8,
        "momentum_21": momentum_21,
        "long_side": long_side,
        "scale_conflict": scale_conflict,
        "volatility": volatility,
        "efficiency": efficiency_bucket,
        "latest_type": _token(latest.get("type"), "UNKNOWN"),
        "latest_signal": latest_signal,
        "trendline_class": _token(trendline.get("class"), "UNPROVEN"),
        "trendline_relation": _token(trendline.get("relation"), "UNPROVEN"),
    }
    pair = _token(symbol)
    frame = _token(timeframe)
    full = "|".join(f"{key}={features[key]}" for key in sorted(features))
    reduced = "|".join(
        f"{key}={features[key]}"
        for key in ("state", "previous_state", "age_bucket", "major", "inner", "momentum_8", "volatility", "trendline_relation")
    )
    state_only = "|".join(
        f"{key}={features[key]}" for key in ("state", "previous_state", "age_bucket", "inner")
    )
    context_keys = [
        "SCALE_RESOLUTION|{pair}|{frame}|state={state}|age={age}|long={long}|major={major}|inner={inner}|m21={momentum}|line={line}".format(
            pair=pair,
            frame=frame,
            state=features["state"],
            age=features["age_bucket"],
            long=features["long_side"],
            major=features["major"],
            inner=features["inner"],
            momentum=features["momentum_21"],
            line=features["trendline_relation"],
        ),
        f"PAIR_FRAME_FULL|{pair}|{frame}|{full}",
        f"PAIR_FULL|{pair}|*|{full}",
        f"GLOBAL_FULL|*|*|{full}",
        f"PAIR_REDUCED|{pair}|{frame}|{reduced}",
        f"GLOBAL_REDUCED|*|*|{reduced}",
        f"GLOBAL_STATE|*|*|{state_only}",
    ]
    canonical = json.dumps(features, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        "schema_version": "PG_MASKED_FUTURE_CONTEXT_V3",
        "prefix_candle_count": len(rows),
        "symbol": pair,
        "timeframe": frame,
        "features": features,
        "trendline_geometry": trendline,
        "context_keys": context_keys,
        "feature_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "future_fields_present": False,
    }


def new_masked_future_model_artifact_v3(horizons: Sequence[int] = DEFAULT_HORIZONS) -> dict[str, Any]:
    return {
        "schema_version": MASKED_FUTURE_MODEL_SCHEMA_VERSION,
        "created_at_utc": "",
        "horizons": sorted({max(1, int(value)) for value in horizons}),
        "contexts": {},
        "training": {},
        "calibration": {},
        "promotion": {"eligible": False, "reason": "NOT_EVALUATED"},
    }


def _counter(container: dict[str, Any], section: str, key: str = "") -> dict[str, int]:
    section_map = container.setdefault(section, {})
    if key:
        section_map = section_map.setdefault(str(key), {})
    for side in DIRECTION_SIDES:
        section_map.setdefault(side, 0)
    return cast(dict[str, int], section_map)


def update_masked_future_model_v3(
    artifact: dict[str, Any],
    context: Mapping[str, Any],
    target: Mapping[str, Any],
) -> None:
    contexts = cast(dict[str, Any], artifact.setdefault("contexts", {}))
    horizon_targets = _mapping(target.get("horizons"))
    endpoint_targets = _mapping(target.get("endpoint_horizons"))
    whole = _mapping(target.get("whole_swing"))
    whole_side = _state_side(whole.get("side"))
    whole_length = max(0, int(_number(whole.get("candles"), 0.0)))
    pullback = bool(target.get("pullback"))
    features = _mapping(context.get("features"))
    local_side = _state_side(features.get("state_side"))
    local_age = str(features.get("age_bucket") or "")
    mature_local_leg = local_side in {"BUY", "SELL"} and local_age in {"2", "3_4"}
    for context_key in cast(Sequence[Any], context.get("context_keys", [])):
        key = str(context_key)
        row = cast(dict[str, Any], contexts.setdefault(key, {"support": 0}))
        row["support"] = int(row.get("support", 0)) + 1
        for horizon, side_value in horizon_targets.items():
            side = _state_side(side_value)
            counts = _counter(row, "horizons", str(horizon))
            counts[side] = int(counts.get(side, 0)) + 1
        for horizon, side_value in endpoint_targets.items():
            side = _state_side(side_value)
            counts = _counter(row, "endpoint_horizons", str(horizon))
            counts[side] = int(counts.get(side, 0)) + 1
        if whole_length > 0:
            swing_counts = _counter(row, "whole_swing")
            swing_counts[whole_side] = int(swing_counts.get(whole_side, 0)) + 1
            lengths = cast(dict[str, Any], row.setdefault("swing_lengths", {}))
            length_row = cast(dict[str, Any], lengths.setdefault(whole_side, {"count": 0, "sum": 0, "sum_sq": 0, "histogram": {}}))
            length_row["count"] = int(length_row.get("count", 0)) + 1
            length_row["sum"] = int(length_row.get("sum", 0)) + whole_length
            length_row["sum_sq"] = int(length_row.get("sum_sq", 0)) + whole_length * whole_length
            histogram = cast(dict[str, int], length_row.setdefault("histogram", {}))
            bucket = str(min(64, whole_length))
            histogram[bucket] = int(histogram.get(bucket, 0)) + 1
            pullbacks = cast(dict[str, Any], row.setdefault("pullback_by_swing", {}))
            pullback_row = cast(dict[str, int], pullbacks.setdefault(whole_side, {"YES": 0, "NO": 0}))
            pullback_row["YES" if pullback else "NO"] = int(pullback_row.get("YES" if pullback else "NO", 0)) + 1
            if mature_local_leg:
                resolution_counts = _counter(row, "mature_local_resolution")
                resolution_counts[whole_side] = int(resolution_counts.get(whole_side, 0)) + 1


def finalize_masked_future_model_v3(
    artifact: dict[str, Any],
    *,
    minimum_context_support: int = 4,
    maximum_contexts: int = 25000,
) -> dict[str, Any]:
    contexts = _mapping(artifact.get("contexts"))
    ranked = sorted(contexts.items(), key=lambda item: (-int(_mapping(item[1]).get("support", 0)), item[0]))
    retained: dict[str, Any] = {}
    for key, value in ranked:
        support = int(_mapping(value).get("support", 0))
        mandatory = key.startswith("GLOBAL_STATE|") or key.startswith("GLOBAL_REDUCED|")
        if support >= max(1, int(minimum_context_support)) or mandatory:
            retained[key] = value
        if len(retained) >= max(128, int(maximum_contexts)):
            break
    artifact["contexts"] = retained
    artifact["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    training = _mapping(artifact.get("training"))
    training.update(
        {
            "retained_context_count": len(retained),
            "pruned_context_count": max(0, len(contexts) - len(retained)),
            "minimum_context_support": max(1, int(minimum_context_support)),
            "maximum_contexts": max(128, int(maximum_contexts)),
        }
    )
    artifact["training"] = training
    return artifact


def _posterior(
    artifact: Mapping[str, Any],
    context_keys: Sequence[Any],
    section: str,
    key: str = "",
) -> tuple[dict[str, float], int, str]:
    contexts = _mapping(artifact.get("contexts"))
    probabilities = {side: 1.0 / 3.0 for side in DIRECTION_SIDES}
    support = 0
    matched_key = ""
    for context_key in reversed([str(value) for value in context_keys]):
        row = _mapping(contexts.get(context_key))
        section_map = _mapping(row.get(section))
        counts = _mapping(section_map.get(key)) if key else section_map
        total = sum(max(0, int(_number(counts.get(side), 0.0))) for side in DIRECTION_SIDES)
        if total <= 0:
            continue
        prior_strength = min(16.0, max(3.0, math.sqrt(float(total))))
        denominator = float(total) + prior_strength
        probabilities = {
            side: (max(0, int(_number(counts.get(side), 0.0))) + prior_strength * probabilities[side]) / denominator
            for side in DIRECTION_SIDES
        }
        support = total
        matched_key = context_key
    return probabilities, support, matched_key


def _entropy(probabilities: Mapping[str, float]) -> float:
    value = 0.0
    for probability in probabilities.values():
        if probability > 0.0:
            value -= probability * math.log2(probability)
    return value


def _length_distribution(
    artifact: Mapping[str, Any], context_keys: Sequence[Any], side: str
) -> tuple[float, int, int, int]:
    contexts = _mapping(artifact.get("contexts"))
    for context_key in [str(value) for value in context_keys]:
        row = _mapping(contexts.get(context_key))
        length_row = _mapping(_mapping(row.get("swing_lengths")).get(side))
        count = int(_number(length_row.get("count"), 0.0))
        if count < 3:
            continue
        histogram = {int(key): int(_number(value, 0.0)) for key, value in _mapping(length_row.get("histogram")).items()}
        ordered = sorted(histogram.items())
        low_target = max(1, int(math.ceil(count * 0.10)))
        high_target = max(1, int(math.ceil(count * 0.90)))
        cumulative = 0
        low = ordered[0][0] if ordered else 0
        high = ordered[-1][0] if ordered else 0
        for length, frequency in ordered:
            cumulative += frequency
            if cumulative >= low_target and low == (ordered[0][0] if ordered else 0):
                low = length
            if cumulative >= high_target:
                high = length
                break
        return _number(length_row.get("sum"), 0.0) / max(1, count), low, high, count
    return 0.0, 0, 0, 0


def _pullback_probability(
    artifact: Mapping[str, Any], context_keys: Sequence[Any], side: str
) -> tuple[float | None, int]:
    contexts = _mapping(artifact.get("contexts"))
    for context_key in [str(value) for value in context_keys]:
        row = _mapping(contexts.get(context_key))
        counts = _mapping(_mapping(row.get("pullback_by_swing")).get(side))
        yes = int(_number(counts.get("YES"), 0.0))
        no = int(_number(counts.get("NO"), 0.0))
        if yes + no >= 3:
            return (yes + 0.5) / (yes + no + 1.0), yes + no
    return None, 0


def pending_masked_future_evidence_v3(reason: object) -> dict[str, Any]:
    return {
        "schema_version": MASKED_FUTURE_EVIDENCE_SCHEMA_VERSION,
        "status": "MODEL_UNAVAILABLE",
        "reason": str(reason or "Masked-future model is unavailable."),
        "study_only": True,
        "execution_authority": False,
        "strategy_authority": False,
        "grants_entry_permission": False,
        "horizons": [],
        "whole_swing": {},
    }


class MaskedFutureBehaviorModelV3:
    def __init__(self, artifact: Mapping[str, Any], *, source_path: str | Path | None = None) -> None:
        payload = _mapping(artifact)
        if payload.get("schema_version") != MASKED_FUTURE_MODEL_SCHEMA_VERSION:
            raise ValueError("masked-future model schema mismatch")
        self.artifact = payload
        self.source_path = Path(source_path) if source_path else None

    @classmethod
    def load(cls, path: str | Path) -> "MaskedFutureBehaviorModelV3":
        model_path = Path(path)
        raw = gzip.decompress(model_path.read_bytes()) if model_path.suffix.lower() == ".gz" else model_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("masked-future artifact must be a JSON object")
        return cls(cast(Mapping[str, Any], payload), source_path=model_path)

    def predict_context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        context_keys = cast(Sequence[Any], context.get("context_keys", []))
        horizon_rows: list[dict[str, Any]] = []
        for horizon in cast(Sequence[Any], self.artifact.get("horizons", DEFAULT_HORIZONS)):
            key = str(max(1, int(_number(horizon, 1.0))))
            probabilities, support, matched = _posterior(self.artifact, context_keys, "horizons", key)
            direction = max(("BUY", "SELL"), key=lambda side: probabilities[side])
            horizon_rows.append(
                {
                    "candles": int(key),
                    "predicted_side": direction,
                    "probabilities": {side: round(probabilities[side], 6) for side in DIRECTION_SIDES},
                    "support": support,
                    "entropy_bits": round(_entropy(probabilities), 6),
                    "matched_context": matched,
                    "target_definition": "MAJORITY_CANDLE_DIRECTION",
                }
            )
        swing_probabilities, swing_support, swing_context = _posterior(
            self.artifact, context_keys, "whole_swing"
        )
        features = _mapping(context.get("features"))
        local_side = _state_side(features.get("state_side"))
        mature_local_leg = local_side in {"BUY", "SELL"} and str(features.get("age_bucket")) in {"2", "3_4"}
        resolution_support = 0
        resolution_context = ""
        if mature_local_leg:
            resolution_probabilities, resolution_support, resolution_context = _posterior(
                self.artifact, context_keys, "mature_local_resolution"
            )
            if resolution_support >= 3:
                conflict = str(features.get("scale_conflict")) != "ALIGNED_OR_UNRESOLVED"
                weight = 0.65 if conflict else 0.40
                swing_probabilities = {
                    side: (1.0 - weight) * swing_probabilities[side] + weight * resolution_probabilities[side]
                    for side in DIRECTION_SIDES
                }
                swing_support = max(swing_support, resolution_support)
        if swing_support:
            swing_side = max(("BUY", "SELL"), key=lambda side: swing_probabilities[side])
        elif horizon_rows:
            swing_side = str(horizon_rows[-1]["predicted_side"])
            swing_probabilities = dict(cast(Mapping[str, float], horizon_rows[-1]["probabilities"]))
            swing_support = int(horizon_rows[-1]["support"])
            swing_context = str(horizon_rows[-1]["matched_context"])
        else:
            swing_side = "UNRESOLVED"
        expected, low, high, length_support = _length_distribution(self.artifact, context_keys, swing_side)
        if expected <= 0.0 and horizon_rows:
            expected = float(horizon_rows[-1]["candles"])
            low = max(1, int(round(expected * 0.60)))
            high = max(low, int(round(expected * 1.40)))
        pullback_probability, pullback_support = _pullback_probability(self.artifact, context_keys, swing_side)
        promotion = _mapping(self.artifact.get("promotion"))
        active = bool(swing_support or any(int(row["support"]) > 0 for row in horizon_rows))
        return {
            "schema_version": MASKED_FUTURE_EVIDENCE_SCHEMA_VERSION,
            "status": "ACTIVE" if active else "INSUFFICIENT_EMPIRICAL_SUPPORT",
            "study_only": True,
            "execution_authority": False,
            "strategy_authority": False,
            "grants_entry_permission": False,
            "model_schema_version": self.artifact.get("schema_version"),
            "model_source": str(self.source_path or "IN_MEMORY"),
            "feature_digest": context.get("feature_digest"),
            "visible_prefix_candles": context.get("prefix_candle_count"),
            "horizons": horizon_rows,
            "whole_swing": {
                "predicted_side": swing_side,
                "probabilities": {side: round(_number(swing_probabilities.get(side)), 6) for side in DIRECTION_SIDES},
                "support": swing_support,
                "matched_context": swing_context,
                "expected_candles": round(expected, 3),
                "candle_interval_80": [low, high],
                "length_support": length_support,
                "rests_included": True,
                "pullback_before_swing_probability": round(pullback_probability, 6) if pullback_probability is not None else None,
                "pullback_support": pullback_support,
                "visible_local_leg_side": local_side,
                "visible_local_leg_age_bucket": features.get("age_bucket"),
                "visible_scale_conflict": features.get("scale_conflict"),
                "local_leg_relationship": (
                    "PULLBACK_AGAINST_PREDICTED_WHOLE_SWING"
                    if local_side in {"BUY", "SELL"} and swing_side in {"BUY", "SELL"} and local_side != swing_side
                    else "ALIGNED_WITH_PREDICTED_WHOLE_SWING"
                    if local_side == swing_side
                    else "UNRESOLVED"
                ),
                "mature_local_resolution_support": resolution_support,
                "mature_local_resolution_context": resolution_context,
            },
            "promotion_eligible": promotion.get("eligible") is True,
            "promotion": promotion,
            "calibration": deepcopy(_mapping(self.artifact.get("calibration"))),
            "leakage_contract": {
                "future_candles_in_features": False,
                "folder_buy_sell_label_in_features": False,
                "prediction_frozen_before_reveal": True,
            },
            "trendline_geometry": deepcopy(_mapping(context.get("trendline_geometry"))),
        }

    def predict(
        self,
        *,
        candles: Sequence[Mapping[str, Any]],
        behavior: Mapping[str, Any],
        symbol: object,
        timeframe: object,
    ) -> dict[str, Any]:
        return self.predict_context(
            build_masked_future_context_v3(candles, behavior, symbol=symbol, timeframe=timeframe)
        )


def resolve_masked_future_model_path_v3(root_dir: str | Path | None = None) -> Path | None:
    configured = str(os.getenv("PHOENIXGUARD_MASKED_FUTURE_MODEL", "")).strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    if root_dir is not None:
        candidates.append(Path(root_dir) / DEFAULT_MASKED_FUTURE_MODEL_NAME)
    candidates.append(Path(__file__).resolve().parents[1] / DEFAULT_MASKED_FUTURE_MODEL_NAME)
    return next((path for path in candidates if path.is_file()), None)


def load_default_masked_future_model_v3(
    root_dir: str | Path | None = None,
) -> MaskedFutureBehaviorModelV3 | None:
    path = resolve_masked_future_model_path_v3(root_dir)
    if path is None:
        return None
    try:
        return MaskedFutureBehaviorModelV3.load(path)
    except (OSError, ValueError, json.JSONDecodeError, gzip.BadGzipFile):
        return None


def save_masked_future_model_v3(artifact: Mapping[str, Any], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    data = gzip.compress(payload, compresslevel=9, mtime=0) if destination.suffix.lower() == ".gz" else payload
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return destination


def apply_masked_future_evidence_v3(
    hidden_state: Mapping[str, Any], evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge promoted posterior evidence without creating trade authority."""

    result = deepcopy(_mapping(hidden_state))
    model = deepcopy(_mapping(evidence))
    result["masked_future_behavior_v3"] = model
    if model.get("status") != "ACTIVE" or model.get("promotion_eligible") is not True:
        return result
    whole = _mapping(model.get("whole_swing"))
    side = _state_side(whole.get("predicted_side"))
    if side not in {"BUY", "SELL"}:
        return result
    probabilities = _mapping(whole.get("probabilities"))
    probability = _clip01(probabilities.get(side))
    opposite = "SELL" if side == "BUY" else "BUY"
    control = _mapping(result.get("control"))
    control.update(
        {
            "side": side,
            "candidate_side": side,
            "status": "EMPIRICAL_WHOLE_SWING_POSTERIOR",
            "basis": "grouped_out_of_sample_masked_future_behavior",
            "empirical_probability": round(probability, 6),
            "opposing_probability": round(_clip01(probabilities.get(opposite)), 6),
            "empirical_support": int(_number(whole.get("support"), 0.0)),
            "expected_swing_candles": _number(whole.get("expected_candles"), 0.0),
            "pullback_before_swing_probability": whole.get("pullback_before_swing_probability"),
            "authority": "STATE_EVIDENCE_NOT_ENTRY_INSTRUCTION",
            "execution_authority": False,
        }
    )
    result["control"] = control
    components = _mapping(result.get("directional_components"))
    for component_side in ("BUY", "SELL"):
        component = _mapping(components.get(component_side))
        component.update(
            {
                "whole_swing_probability": round(_clip01(probabilities.get(component_side)), 6),
                "empirical_support": int(_number(whole.get("support"), 0.0)),
                "currently_in_control": component_side == side,
                "evidence_status": "GROUPED_OUT_OF_SAMPLE_CALIBRATED",
                "authority": "STATE_EVIDENCE_NOT_TRADE_COMMAND",
            }
        )
        components[component_side] = component
    result["directional_components"] = components
    expected_candles = max(0.0, _number(whole.get("expected_candles"), 0.0))
    seconds_per_candle = _timeframe_seconds(result.get("timeframe"))
    duration_seconds = int(round(expected_candles * seconds_per_candle)) if seconds_per_candle else 0
    result["state_cycle_horizon"] = {
        "status": "EMPIRICAL_WHOLE_SWING_HORIZON",
        "mode": "MASKED_FUTURE_WHOLE_SWING_INCLUDING_RESTS",
        "fixed_candle_horizon": False,
        "rests_included": True,
        "predicted_side": side,
        "expected_candles": round(expected_candles, 3),
        "candle_interval_80": whole.get("candle_interval_80", [0, 0]),
        "duration": {
            "status": "CALCULATED_FROM_VERIFIED_TIMEFRAME" if seconds_per_candle else "TIMEFRAME_UNAVAILABLE",
            "seconds": duration_seconds,
            "minutes": round(duration_seconds / 60.0, 3),
            "hours": round(duration_seconds / 3600.0, 3),
        },
        "path_probability": round(probability, 6),
        "support": int(_number(whole.get("support"), 0.0)),
    }
    result["directional_outcome_distribution"] = {
        "status": "ACTIVE",
        "support": int(_number(whole.get("support"), 0.0)),
        "probabilities": {component_side: round(_clip01(probabilities.get(component_side)), 6) for component_side in DIRECTION_SIDES},
        "target_definition": "WHOLE_SWING_MAJORITY_DIRECTION_INCLUDING_RESTS",
    }
    result["study_only"] = True
    result["execution_authority"] = False
    result["strategy_authority"] = False
    result["grants_entry_permission"] = False
    return result


__all__ = [
    "DEFAULT_HORIZONS",
    "DEFAULT_MASKED_FUTURE_MODEL_NAME",
    "MASKED_FUTURE_EVIDENCE_SCHEMA_VERSION",
    "MASKED_FUTURE_MODEL_SCHEMA_VERSION",
    "MaskedFutureBehaviorModelV3",
    "apply_masked_future_evidence_v3",
    "build_masked_future_context_v3",
    "build_wick_trendline_context_v3",
    "candle_ohlc_v3",
    "finalize_masked_future_model_v3",
    "load_default_masked_future_model_v3",
    "new_masked_future_model_artifact_v3",
    "pending_masked_future_evidence_v3",
    "resolve_masked_future_model_path_v3",
    "save_masked_future_model_v3",
    "update_masked_future_model_v3",
]
