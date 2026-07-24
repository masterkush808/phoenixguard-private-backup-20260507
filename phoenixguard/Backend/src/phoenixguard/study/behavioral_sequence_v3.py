"""Measured swing, rest, and trend behavior for PhoenixGuard V3 studies.

The service consumes validated candle-intelligence rows and describes what the
market actually did: directional swings, resting/compression periods, their
durations, and observed transitions.  Major and inner trends are deliberately
reported separately and never become execution authority.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
import math
from statistics import mean, median
from typing import Any, cast

from phoenixguard.study.candle_intelligence_v3 import (
    CANDLE_INTELLIGENCE_SCHEMA_VERSION,
    MAX_STUDY_CANDLES,
    CandleStudyValidationError,
    analyze_candle_sequence_v3,
)


BEHAVIORAL_SEQUENCE_SCHEMA_VERSION = "PG_BEHAVIORAL_SEQUENCE_V3"
BEHAVIOR_STATES = ("UP_SWING", "REST", "DOWN_SWING")


class BehaviorStudyValidationError(ValueError):
    """Raised when the behavioral study input violates the V3 study contract."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for item in cast(Sequence[object], value):
        if isinstance(item, Mapping):
            result.append(dict(cast(Mapping[str, Any], item)))
    return result


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise BehaviorStudyValidationError(f"{field} must be a finite number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise BehaviorStudyValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise BehaviorStudyValidationError(f"{field} must be a finite number")
    return parsed


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _timestamp_seconds(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _canonical_study(
    source: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    max_candles: int,
) -> dict[str, Any]:
    if isinstance(source, Mapping):
        candidate = _mapping(source)
        if candidate.get("schema_version") != CANDLE_INTELLIGENCE_SCHEMA_VERSION:
            raise BehaviorStudyValidationError("candle study schema is not PhoenixGuard V3")
        if candidate.get("status") not in {"STUDIED", "INSUFFICIENT_HISTORY"}:
            raise BehaviorStudyValidationError("candle study status is not usable")
        if candidate.get("execution_authority") is not False:
            raise BehaviorStudyValidationError("candle study must be observation-only")
        return candidate
    try:
        return analyze_candle_sequence_v3(source, max_candles=max_candles)
    except CandleStudyValidationError as exc:
        raise BehaviorStudyValidationError(str(exc)) from exc


def _ohlc(row: Mapping[str, Any]) -> tuple[float, float, float, float]:
    ohlc = _mapping(row.get("ohlc"))
    return (
        _finite(ohlc.get("open"), field="ohlc.open"),
        _finite(ohlc.get("high"), field="ohlc.high"),
        _finite(ohlc.get("low"), field="ohlc.low"),
        _finite(ohlc.get("close"), field="ohlc.close"),
    )


def _ratio(row: Mapping[str, Any], name: str) -> float:
    ratios = _mapping(row.get("ratios"))
    return _finite(ratios.get(name, 0.0), field=f"ratios.{name}")


def _state_rows(candles: Sequence[Mapping[str, Any]], baseline_range: float) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    previous_close: float | None = None
    for index, candle in enumerate(candles):
        open_value, high_value, low_value, close_value = _ohlc(candle)
        body_ratio = _ratio(candle, "body_to_range")
        range_multiple = _ratio(candle, "range_vs_sequence_median")
        candle_type = str(candle.get("type") or "UNKNOWN")
        delta = close_value - previous_close if previous_close is not None else close_value - open_value
        movement_ratio = abs(delta) / max(1e-12, baseline_range)
        indecision = candle_type in {
            "DOJI",
            "LONG_LEGGED_DOJI",
            "SPINNING_TOP",
            "BALANCED_INDECISION",
        }
        resting = bool(
            movement_ratio <= 0.30
            and (body_ratio <= 0.36 or range_multiple <= 0.68 or indecision)
        )
        if abs(delta) <= baseline_range * 1e-9 or resting:
            state = "REST"
        elif delta > 0.0:
            state = "UP_SWING"
        else:
            state = "DOWN_SWING"
        states.append(
            {
                "index": index,
                "candle_id": str(candle.get("candle_id") or f"candle-{index:06d}"),
                "timestamp": candle.get("timestamp"),
                "state": state,
                "delta": delta,
                "movement_vs_median_range": movement_ratio,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "body_ratio": body_ratio,
                "upper_wick_ratio": _ratio(candle, "upper_wick_to_range"),
                "lower_wick_ratio": _ratio(candle, "lower_wick_to_range"),
                "range_multiple": range_multiple,
                "personality": str(candle.get("personality") or "UNKNOWN"),
            }
        )
        previous_close = close_value
    return states


def _duration_seconds(
    states: Sequence[Mapping[str, Any]],
    start: int,
    end: int,
    timeframe_seconds: int,
) -> int:
    first = _timestamp_seconds(states[start].get("timestamp"))
    last = _timestamp_seconds(states[end].get("timestamp"))
    if first is not None and last is not None and last >= first:
        observed = last - first + timeframe_seconds
        return max(timeframe_seconds, int(round(observed)))
    return (end - start + 1) * timeframe_seconds


def _segment(
    states: Sequence[Mapping[str, Any]],
    start: int,
    end: int,
    timeframe_seconds: int,
    baseline_range: float,
) -> dict[str, Any]:
    selected = states[start : end + 1]
    state = str(selected[0]["state"])
    price_change = float(selected[-1]["close"]) - float(selected[0]["open"])
    closes = [float(row["close"]) for row in selected]
    path_distance = abs(closes[0] - float(selected[0]["open"]))
    path_distance += sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
    efficiency = abs(price_change) / max(1e-12, path_distance)
    return {
        "segment_index": 0,
        "state": state,
        "direction": "UP" if state == "UP_SWING" else "DOWN" if state == "DOWN_SWING" else "SIDEWAYS",
        "start_index": start,
        "end_index": end,
        "start_candle_id": str(selected[0]["candle_id"]),
        "end_candle_id": str(selected[-1]["candle_id"]),
        "candle_count": len(selected),
        "duration_seconds": _duration_seconds(states, start, end, timeframe_seconds),
        "price_change": round(price_change, 8),
        "absolute_price_change": round(abs(price_change), 8),
        "absolute_change_in_median_ranges": round(abs(price_change) / max(1e-12, baseline_range), 8),
        "high": round(max(float(row["high"]) for row in selected), 8),
        "low": round(min(float(row["low"]) for row in selected), 8),
        "path_efficiency": round(_clip01(efficiency), 6),
        "average_body_ratio": round(mean(float(row["body_ratio"]) for row in selected), 6),
        "average_upper_wick_ratio": round(mean(float(row["upper_wick_ratio"]) for row in selected), 6),
        "average_lower_wick_ratio": round(mean(float(row["lower_wick_ratio"]) for row in selected), 6),
        "average_range_multiple": round(mean(float(row["range_multiple"]) for row in selected), 6),
    }


def _segments(
    states: Sequence[Mapping[str, Any]],
    timeframe_seconds: int,
    baseline_range: float,
) -> list[dict[str, Any]]:
    if not states:
        return []
    result: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(states) + 1):
        boundary = index == len(states) or states[index].get("state") != states[start].get("state")
        if not boundary:
            continue
        row = _segment(states, start, index - 1, timeframe_seconds, baseline_range)
        row["segment_index"] = len(result)
        result.append(row)
        start = index
    for index, row in enumerate(result):
        row["previous_state"] = str(result[index - 1]["state"]) if index else "NONE"
        row["next_state"] = str(result[index + 1]["state"]) if index + 1 < len(result) else "NONE"
    return result


def summarize_regime_transitions_v3(states: Sequence[str]) -> dict[str, Any]:
    """Return deterministic transition counts and conditional probabilities."""

    canonical = [str(state).strip().upper() for state in states]
    if any(state not in BEHAVIOR_STATES for state in canonical):
        raise BehaviorStudyValidationError("behavior state is outside the V3 taxonomy")
    counts: Counter[tuple[str, str]] = Counter(zip(canonical, canonical[1:]))
    outgoing: Counter[str] = Counter()
    for (source, _target), count in counts.items():
        outgoing[source] += count
    rows = [
        {
            "from": source,
            "to": target,
            "count": count,
            "probability": round(count / outgoing[source], 6),
        }
        for (source, target), count in sorted(counts.items())
    ]
    matrix = {
        source: {
            target: round(counts[(source, target)] / outgoing[source], 6) if outgoing[source] else 0.0
            for target in BEHAVIOR_STATES
        }
        for source in BEHAVIOR_STATES
    }
    return {
        "observation_count": max(0, len(canonical) - 1),
        "rows": rows,
        "matrix": matrix,
    }


def _trend(states: Sequence[Mapping[str, Any]], *, scope: str) -> dict[str, Any]:
    count = len(states)
    if count < 2:
        return {
            "scope": scope,
            "label": "INSUFFICIENT_HISTORY",
            "direction": "UNKNOWN",
            "strength": 0.0,
            "candle_count": count,
            "normalized_slope": 0.0,
            "directional_efficiency": 0.0,
        }
    closes = [float(row["close"]) for row in states]
    ranges = [float(row["high"]) - float(row["low"]) for row in states]
    baseline = max(1e-12, median(ranges))
    x_mean = (count - 1) / 2.0
    y_mean = mean(closes)
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    slope = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(closes)) / max(1e-12, denominator)
    normalized_slope = slope / baseline
    path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, count))
    efficiency = abs(closes[-1] - closes[0]) / max(1e-12, path)
    if normalized_slope >= 0.08 and efficiency >= 0.22:
        label = "UP"
        direction = "BULLISH"
    elif normalized_slope <= -0.08 and efficiency >= 0.22:
        label = "DOWN"
        direction = "BEARISH"
    else:
        label = "SIDEWAYS"
        direction = "SIDEWAYS"
    strength = _clip01(abs(normalized_slope) * min(1.0, 0.35 + efficiency))
    return {
        "scope": scope,
        "label": label,
        "direction": direction,
        "strength": round(strength, 6),
        "candle_count": count,
        "normalized_slope": round(normalized_slope, 6),
        "directional_efficiency": round(_clip01(efficiency), 6),
        "net_change": round(closes[-1] - closes[0], 8),
    }


def _metric_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "segment_count": 0,
            "average_candles": 0.0,
            "maximum_candles": 0,
            "average_duration_seconds": 0.0,
            "average_absolute_price_change": 0.0,
        }
    return {
        "segment_count": len(rows),
        "average_candles": round(mean(float(row["candle_count"]) for row in rows), 4),
        "maximum_candles": max(int(row["candle_count"]) for row in rows),
        "average_duration_seconds": round(mean(float(row["duration_seconds"]) for row in rows), 2),
        "average_absolute_price_change": round(mean(float(row["absolute_price_change"]) for row in rows), 8),
    }


def measure_market_behavior_v3(
    source: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    timeframe_seconds: int = 300,
    max_candles: int = MAX_STUDY_CANDLES,
    inner_window: int = 8,
) -> dict[str, Any]:
    """Measure swing/rest durations and the separate major and inner trends."""

    timeframe = int(timeframe_seconds)
    if timeframe <= 0 or timeframe > 2_592_000:
        raise BehaviorStudyValidationError("timeframe_seconds must be in [1, 2592000]")
    window = int(inner_window)
    if window < 2 or window > 128:
        raise BehaviorStudyValidationError("inner_window must be in [2, 128]")
    study = _canonical_study(source, max_candles=max_candles)
    candles = _rows(study.get("candles"))
    if len(candles) < 2:
        return {
            "schema_version": BEHAVIORAL_SEQUENCE_SCHEMA_VERSION,
            "status": "INSUFFICIENT_HISTORY",
            "study_only": True,
            "execution_authority": False,
            "candle_count": len(candles),
            "major_trend": _trend([], scope="MAJOR"),
            "inner_trend": _trend([], scope="INNER"),
            "states": [],
            "segments": [],
            "transition_summary": summarize_regime_transitions_v3([]),
        }

    baseline = _finite(study.get("baseline_range"), field="baseline_range")
    if baseline <= 0.0:
        raise BehaviorStudyValidationError("baseline_range must be positive")
    states = _state_rows(candles, baseline)
    segments = _segments(states, timeframe, baseline)
    transition_summary = summarize_regime_transitions_v3([str(row["state"]) for row in states])
    segment_transitions = summarize_regime_transitions_v3([str(row["state"]) for row in segments])
    by_state: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in segments:
        by_state[str(row["state"])].append(row)
    up_summary = _metric_summary(by_state["UP_SWING"])
    down_summary = _metric_summary(by_state["DOWN_SWING"])
    rest_summary = _metric_summary(by_state["REST"])
    rests_with_breakout = [row for row in by_state["REST"] if row.get("next_state") in {"UP_SWING", "DOWN_SWING"}]
    rest_summary["breakout_up_count"] = sum(row.get("next_state") == "UP_SWING" for row in rests_with_breakout)
    rest_summary["breakout_down_count"] = sum(row.get("next_state") == "DOWN_SWING" for row in rests_with_breakout)
    rest_summary["unresolved_count"] = sum(row.get("next_state") == "NONE" for row in by_state["REST"])

    major = _trend(states, scope="MAJOR")
    inner_states = states[-min(window, len(states)) :]
    inner = _trend(inner_states, scope="INNER")
    current = segments[-1]
    current_state = str(current["state"])
    if current_state == "UP_SWING":
        current_story = f"Price is in an up swing lasting {current['candle_count']} candles."
    elif current_state == "DOWN_SWING":
        current_story = f"Price is in a down swing lasting {current['candle_count']} candles."
    else:
        current_story = f"Price is resting sideways for {current['candle_count']} candles."
    story = (
        f"Major trend: {major['label']}. Inner trend: {inner['label']}. "
        f"{current_story}"
    )
    return {
        "schema_version": BEHAVIORAL_SEQUENCE_SCHEMA_VERSION,
        "status": "STUDIED",
        "study_only": True,
        "execution_authority": False,
        "candle_sequence_signature": str(study.get("sequence_signature") or ""),
        "candle_count": len(candles),
        "timeframe_seconds": timeframe,
        "major_trend": major,
        "inner_trend": inner,
        "current_state": {
            "state": current_state,
            "direction": current["direction"],
            "candle_count": current["candle_count"],
            "duration_seconds": current["duration_seconds"],
            "started_at_index": current["start_index"],
        },
        "states": [
            {
                "index": int(row["index"]),
                "candle_id": str(row["candle_id"]),
                "state": str(row["state"]),
                "movement_vs_median_range": round(float(row["movement_vs_median_range"]), 6),
            }
            for row in states
        ],
        "segments": segments,
        "swing_summary": {
            "up": up_summary,
            "down": down_summary,
        },
        "rest_summary": rest_summary,
        "state_counts": dict(sorted(Counter(str(row["state"]) for row in states).items())),
        "transition_summary": transition_summary,
        "segment_transition_summary": segment_transitions,
        "market_story": story,
    }


__all__ = [
    "BEHAVIORAL_SEQUENCE_SCHEMA_VERSION",
    "BEHAVIOR_STATES",
    "BehaviorStudyValidationError",
    "measure_market_behavior_v3",
    "summarize_regime_transitions_v3",
]
