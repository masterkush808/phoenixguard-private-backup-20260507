"""Explainable historical sequence similarity and outcome correlation for V3.

Fingerprints combine volatility-normalized price shape, candle micro-geometry,
behavior-state distribution, trend scope, and detected object types.  Search is
deterministic and pair-scoped by default.  Historical continuation summaries
are published only with minimum labeled support and remain observation-only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, cast

from phoenixguard.study._persistence_v3 import (
    exclusive_store_lock,
    read_json_document,
    write_json_atomic,
)
from phoenixguard.study.behavioral_sequence_v3 import BEHAVIORAL_SEQUENCE_SCHEMA_VERSION
from phoenixguard.study.candle_intelligence_v3 import CANDLE_INTELLIGENCE_SCHEMA_VERSION


SEQUENCE_FINGERPRINT_SCHEMA_VERSION = "PG_SEQUENCE_FINGERPRINT_V3"
HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION = "PG_HISTORICAL_SEQUENCE_STORE_V3"
FINGERPRINT_VECTOR_SIZE = 60
DEFAULT_MAX_HISTORICAL_ENTRIES = 1_000_000
DEFAULT_MAX_ENTRIES_PER_PAIR = 1_000_000


class HistoricalSimilarityValidationError(ValueError):
    """Raised when fingerprint or historical-store evidence is invalid."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for row in cast(Sequence[object], value):
        if isinstance(row, Mapping):
            result.append(dict(cast(Mapping[str, Any], row)))
    return result


def _required_rows(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise HistoricalSimilarityValidationError(f"{field} must be a list of mappings")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(cast(list[object], value)):
        if not isinstance(item, Mapping):
            raise HistoricalSimilarityValidationError(f"{field}[{index}] must be a mapping")
        result.append(dict(cast(Mapping[str, Any], item)))
    return result


def _required_strings(value: object, *, field: str, maximum: int) -> list[str]:
    if not isinstance(value, list):
        raise HistoricalSimilarityValidationError(f"{field} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(cast(list[object], value)):
        if not isinstance(item, str) or not item.strip():
            raise HistoricalSimilarityValidationError(f"{field}[{index}] must be a non-empty string")
        if len(item) > maximum:
            raise HistoricalSimilarityValidationError(f"{field}[{index}] exceeds {maximum} characters")
        result.append(item)
    return result


def _finite(value: object, *, field: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise HistoricalSimilarityValidationError(f"{field} must be a finite number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise HistoricalSimilarityValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise HistoricalSimilarityValidationError(f"{field} must be a finite number")
    return parsed


def _integer(value: object, *, field: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise HistoricalSimilarityValidationError(f"{field} must be a non-negative integer")
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise HistoricalSimilarityValidationError(f"{field} must be a non-negative integer") from exc
    if not math.isfinite(numeric) or numeric < 0.0 or not numeric.is_integer():
        raise HistoricalSimilarityValidationError(f"{field} must be a non-negative integer")
    return int(numeric)


def _identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise HistoricalSimilarityValidationError(f"{field} is required")
    if len(text) > maximum:
        raise HistoricalSimilarityValidationError(f"{field} exceeds {maximum} characters")
    return text


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _resample(values: Sequence[float], size: int) -> list[float]:
    if size <= 0:
        raise HistoricalSimilarityValidationError("resample size must be positive")
    if not values:
        return [0.0] * size
    if len(values) == 1:
        return [round(float(values[0]), 8)] * size
    result: list[float] = []
    scale = (len(values) - 1) / max(1, size - 1)
    for target in range(size):
        position = target * scale
        left = int(math.floor(position))
        right = min(len(values) - 1, left + 1)
        fraction = position - left
        value = float(values[left]) * (1.0 - fraction) + float(values[right]) * fraction
        result.append(round(value, 8))
    return result


def _ohlc(row: Mapping[str, Any]) -> tuple[float, float, float, float]:
    source = _mapping(row.get("ohlc"))
    return (
        _finite(source.get("open"), field="ohlc.open"),
        _finite(source.get("high"), field="ohlc.high"),
        _finite(source.get("low"), field="ohlc.low"),
        _finite(source.get("close"), field="ohlc.close"),
    )


def _ratio(row: Mapping[str, Any], name: str) -> float:
    return _finite(_mapping(row.get("ratios")).get(name), field=f"ratios.{name}")


def _distribution(values: Sequence[str], classes: Sequence[str]) -> list[float]:
    counts = Counter(values)
    total = max(1, len(values))
    return [round(counts[name] / total, 8) for name in classes]


def _trend_one_hot(label: object) -> list[float]:
    canonical = str(label or "SIDEWAYS").strip().upper()
    return [float(canonical == name) for name in ("UP", "SIDEWAYS", "DOWN")]


def _outcome(outcome: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _mapping(outcome)
    direction = "UNKNOWN"
    for name in ("direction", "outcome_direction", "next_direction", "actual_direction"):
        text = str(source.get(name) or "").strip().upper()
        if text in {"BUY", "BULL", "BULLISH", "UP", "UP_SWING"}:
            direction = "UP"
            break
        if text in {"SELL", "BEAR", "BEARISH", "DOWN", "DOWN_SWING"}:
            direction = "DOWN"
            break
        if text in {"REST", "SIDEWAYS", "FLAT", "HOLD", "TIE"}:
            direction = "REST"
            break
    realized = _finite(source.get("realized_return"), field="outcome.realized_return", default=0.0)
    # Realized P&L cannot stand in for market direction because a profitable
    # SELL has positive P&L while the market path is DOWN.
    success_value = source.get("success")
    if isinstance(success_value, bool):
        success: bool | None = success_value
    else:
        result = str(source.get("result") or source.get("status") or "").strip().upper()
        success = True if result in {"WIN", "SUCCESS", "MATCHED", "CORRECT"} else False if result in {"LOSS", "FAILED", "INCORRECT"} else None
    coordinate_continuity = str(source.get("coordinate_continuity") or "").strip().upper()
    if coordinate_continuity not in {"CURRENT_FRAME_REOBSERVATION", "PRICE_SERIES"}:
        coordinate_continuity = ""
    return {
        "direction": direction,
        "realized_return": round(realized, 8),
        "success": success,
        "horizon_candles": _integer(source.get("horizon_candles"), field="outcome.horizon_candles"),
        "coordinate_continuity": coordinate_continuity,
    }


def _fingerprint_core(fingerprint: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SEQUENCE_FINGERPRINT_SCHEMA_VERSION,
        "sequence_id": fingerprint.get("sequence_id"),
        "symbol": fingerprint.get("symbol"),
        "timeframe": fingerprint.get("timeframe"),
        "coordinate_space": fingerprint.get("coordinate_space"),
        "regime": fingerprint.get("regime"),
        "candle_count": fingerprint.get("candle_count"),
        "feature_vector": fingerprint.get("feature_vector"),
        "components": fingerprint.get("components"),
        "tokens": fingerprint.get("tokens"),
        "object_types": fingerprint.get("object_types"),
        "latest": fingerprint.get("latest"),
    }


def _fingerprint_id(fingerprint: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _fingerprint_core(fingerprint),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_sequence_fingerprint_v3(
    candle_study: Mapping[str, Any],
    behavior_study: Mapping[str, Any],
    *,
    symbol: object,
    timeframe: object,
    sequence_id: object | None = None,
    objects: Sequence[Mapping[str, Any]] = (),
    outcome: Mapping[str, Any] | None = None,
    max_candles: int = 64,
) -> dict[str, Any]:
    """Build a fixed-size, explainable fingerprint from one completed study."""

    if candle_study.get("schema_version") != CANDLE_INTELLIGENCE_SCHEMA_VERSION:
        raise HistoricalSimilarityValidationError("candle study schema is not PhoenixGuard V3")
    if behavior_study.get("schema_version") != BEHAVIORAL_SEQUENCE_SCHEMA_VERSION:
        raise HistoricalSimilarityValidationError("behavior study schema is not PhoenixGuard V3")
    if candle_study.get("status") != "STUDIED" or behavior_study.get("status") != "STUDIED":
        raise HistoricalSimilarityValidationError("fingerprints require completed studies")
    if candle_study.get("execution_authority") is not False or behavior_study.get("execution_authority") is not False:
        raise HistoricalSimilarityValidationError("fingerprints require observation-only evidence")
    limit = int(max_candles)
    if limit < 4 or limit > 512:
        raise HistoricalSimilarityValidationError("max_candles must be in [4, 512]")
    candle_rows = _required_rows(candle_study.get("candles"), field="candle_study.candles")[-limit:]
    state_rows = _required_rows(behavior_study.get("states"), field="behavior_study.states")[-limit:]
    if len(candle_rows) < 2 or len(state_rows) < 2:
        raise HistoricalSimilarityValidationError("fingerprints require at least two studied candles")
    if len(candle_rows) != len(state_rows):
        raise HistoricalSimilarityValidationError("candle and behavior study lengths must match")
    canonical_symbol = _identity(symbol, field="symbol", maximum=64)
    canonical_timeframe = _identity(timeframe, field="timeframe", maximum=32)
    resolved_sequence_id = _identity(
        sequence_id or candle_study.get("sequence_signature"),
        field="sequence_id",
        maximum=256,
    )

    spaces = {str(row.get("coordinate_space") or "UNKNOWN") for row in candle_rows}
    if len(spaces) != 1:
        raise HistoricalSimilarityValidationError("one fingerprint cannot mix candle coordinate spaces")
    closes = [_ohlc(row)[3] for row in candle_rows]
    baseline = _finite(candle_study.get("baseline_range"), field="baseline_range")
    if baseline <= 0.0:
        raise HistoricalSimilarityValidationError("baseline_range must be positive")
    path = [_clip((close - closes[0]) / baseline, -8.0, 8.0) / 8.0 for close in closes]
    deltas = [0.0] + [_clip((closes[index] - closes[index - 1]) / baseline, -4.0, 4.0) / 4.0 for index in range(1, len(closes))]
    body = [_clip(_ratio(row, "body_to_range"), 0.0, 1.0) for row in candle_rows]
    upper = [_clip(_ratio(row, "upper_wick_to_range"), 0.0, 1.0) for row in candle_rows]
    lower = [_clip(_ratio(row, "lower_wick_to_range"), 0.0, 1.0) for row in candle_rows]
    path_shape = _resample(path, 16)
    delta_shape = _resample(deltas, 8)
    body_shape = _resample(body, 8)
    upper_shape = _resample(upper, 8)
    lower_shape = _resample(lower, 8)
    directions = [str(row.get("direction") or "NEUTRAL") for row in candle_rows]
    states = [str(row.get("state") or "REST") for row in state_rows]
    direction_distribution = _distribution(directions, ("BULLISH", "NEUTRAL", "BEARISH"))
    state_distribution = _distribution(states, ("UP_SWING", "REST", "DOWN_SWING"))
    major = str(_mapping(behavior_study.get("major_trend")).get("label") or "SIDEWAYS")
    inner = str(_mapping(behavior_study.get("inner_trend")).get("label") or "SIDEWAYS")
    major_one_hot = _trend_one_hot(major)
    inner_one_hot = _trend_one_hot(inner)
    feature_vector = (
        path_shape
        + delta_shape
        + body_shape
        + upper_shape
        + lower_shape
        + direction_distribution
        + state_distribution
        + major_one_hot
        + inner_one_hot
    )
    if len(feature_vector) != FINGERPRINT_VECTOR_SIZE:
        raise HistoricalSimilarityValidationError("internal fingerprint dimension mismatch")
    tokens = [
        "|".join(
            (
                str(row.get("direction") or "UNKNOWN"),
                str(row.get("type") or "UNKNOWN"),
                str(row.get("personality") or "UNKNOWN"),
                str(row.get("relation_to_previous") or "UNKNOWN"),
            )
        )
        for row in candle_rows[-32:]
    ]
    if isinstance(objects, (str, bytes, bytearray)) or len(objects) > 256:
        raise HistoricalSimilarityValidationError("objects must contain at most 256 mappings")
    object_rows: list[dict[str, Any]] = []
    for index, raw_row in enumerate(cast(Sequence[object], objects)):
        if not isinstance(raw_row, Mapping):
            raise HistoricalSimilarityValidationError(f"objects[{index}] must be a mapping")
        object_rows.append(dict(cast(Mapping[str, Any], raw_row)))
    object_types = sorted(
        {
            str(row.get("object_type") or row.get("type") or "").strip().upper()
            for row in object_rows
            if str(row.get("object_type") or row.get("type") or "").strip()
        }
    )
    latest = candle_rows[-1]
    regime = str(latest.get("regime") or "UNKNOWN").strip().upper()
    fingerprint: dict[str, Any] = {
        "schema_version": SEQUENCE_FINGERPRINT_SCHEMA_VERSION,
        "status": "READY",
        "study_only": True,
        "execution_authority": False,
        "fingerprint_id": "",
        "sequence_id": resolved_sequence_id,
        "symbol": canonical_symbol,
        "timeframe": canonical_timeframe,
        "coordinate_space": next(iter(spaces)),
        "regime": regime,
        "candle_count": len(candle_rows),
        "feature_vector": [round(value, 8) for value in feature_vector],
        "components": {
            "path_shape": path_shape,
            "delta_shape": delta_shape,
            "body_shape": body_shape,
            "upper_wick_shape": upper_shape,
            "lower_wick_shape": lower_shape,
            "direction_distribution": direction_distribution,
            "state_distribution": state_distribution,
            "major_trend": major,
            "inner_trend": inner,
        },
        "tokens": tokens,
        "object_types": object_types,
        "latest": {
            "direction": str(latest.get("direction") or "UNKNOWN"),
            "type": str(latest.get("type") or "UNKNOWN"),
            "personality": str(latest.get("personality") or "UNKNOWN"),
            "current_state": str(_mapping(behavior_study.get("current_state")).get("state") or "UNKNOWN"),
        },
        "outcome": _outcome(outcome),
    }
    fingerprint["fingerprint_id"] = _fingerprint_id(fingerprint)
    return validate_sequence_fingerprint_v3(fingerprint)


def _finite_vector(value: object, *, field: str, size: int) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise HistoricalSimilarityValidationError(f"{field} must be a numeric sequence")
    result = [_finite(item, field=f"{field}[{index}]") for index, item in enumerate(cast(Sequence[object], value))]
    if len(result) != size:
        raise HistoricalSimilarityValidationError(f"{field} must contain {size} values")
    return [round(item, 8) for item in result]


def validate_sequence_fingerprint_v3(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached canonical fingerprint and reject any tampering."""

    source = _mapping(value)
    if source.get("schema_version") != SEQUENCE_FINGERPRINT_SCHEMA_VERSION:
        raise HistoricalSimilarityValidationError(f"fingerprint schema must be {SEQUENCE_FINGERPRINT_SCHEMA_VERSION}")
    if source.get("study_only") is not True or source.get("execution_authority") is not False:
        raise HistoricalSimilarityValidationError("fingerprint must be study-only")
    components = _mapping(source.get("components"))
    canonical: dict[str, Any] = {
        "schema_version": SEQUENCE_FINGERPRINT_SCHEMA_VERSION,
        "status": "READY",
        "study_only": True,
        "execution_authority": False,
        "fingerprint_id": str(source.get("fingerprint_id") or ""),
        "sequence_id": _identity(source.get("sequence_id"), field="sequence_id", maximum=256),
        "symbol": _identity(source.get("symbol"), field="symbol", maximum=64),
        "timeframe": _identity(source.get("timeframe"), field="timeframe", maximum=32),
        "coordinate_space": _identity(source.get("coordinate_space"), field="coordinate_space", maximum=32),
        "regime": _identity(source.get("regime"), field="regime", maximum=64),
        "candle_count": _integer(source.get("candle_count"), field="candle_count"),
        "feature_vector": _finite_vector(source.get("feature_vector"), field="feature_vector", size=FINGERPRINT_VECTOR_SIZE),
        "components": {
            "path_shape": _finite_vector(components.get("path_shape"), field="path_shape", size=16),
            "delta_shape": _finite_vector(components.get("delta_shape"), field="delta_shape", size=8),
            "body_shape": _finite_vector(components.get("body_shape"), field="body_shape", size=8),
            "upper_wick_shape": _finite_vector(components.get("upper_wick_shape"), field="upper_wick_shape", size=8),
            "lower_wick_shape": _finite_vector(components.get("lower_wick_shape"), field="lower_wick_shape", size=8),
            "direction_distribution": _finite_vector(components.get("direction_distribution"), field="direction_distribution", size=3),
            "state_distribution": _finite_vector(components.get("state_distribution"), field="state_distribution", size=3),
            "major_trend": _identity(components.get("major_trend"), field="major_trend", maximum=32),
            "inner_trend": _identity(components.get("inner_trend"), field="inner_trend", maximum=32),
        },
        "tokens": _required_strings(source.get("tokens"), field="tokens", maximum=512),
        "object_types": sorted(
            {
                token.strip().upper()
                for token in _required_strings(
                    source.get("object_types"),
                    field="object_types",
                    maximum=128,
                )
            }
        ),
        "latest": {
            "direction": str(_mapping(source.get("latest")).get("direction") or "UNKNOWN"),
            "type": str(_mapping(source.get("latest")).get("type") or "UNKNOWN"),
            "personality": str(_mapping(source.get("latest")).get("personality") or "UNKNOWN"),
            "current_state": str(_mapping(source.get("latest")).get("current_state") or "UNKNOWN"),
        },
        "outcome": _outcome(_mapping(source.get("outcome"))),
    }
    if canonical["candle_count"] < 2:
        raise HistoricalSimilarityValidationError("fingerprint requires at least two candles")
    if not 1 <= len(cast(list[str], canonical["tokens"])) <= 32:
        raise HistoricalSimilarityValidationError("fingerprint tokens must contain [1, 32] rows")
    expected_id = _fingerprint_id(canonical)
    if canonical["fingerprint_id"] != expected_id:
        raise HistoricalSimilarityValidationError("fingerprint digest mismatch")
    return canonical


def _mean_absolute_similarity(left: Sequence[float], right: Sequence[float], *, scale: float = 1.0) -> float:
    distance = mean(abs(first - second) for first, second in zip(left, right, strict=True))
    return _clip(1.0 - distance / max(1e-12, scale), 0.0, 1.0)


def _token_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    length = min(len(left), len(right))
    positional = sum(left[-length + index] == right[-length + index] for index in range(length)) / max(1, length)
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    jaccard = len(left_set & right_set) / max(1, len(union))
    return 0.6 * positional + 0.4 * jaccard


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / max(1, len(left_set | right_set))


def sequence_similarity_v3(
    query: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two fingerprints using fixed, explainable component weights."""

    left = validate_sequence_fingerprint_v3(query)
    right = validate_sequence_fingerprint_v3(candidate)
    left_components = _mapping(left["components"])
    right_components = _mapping(right["components"])
    path_score = _mean_absolute_similarity(
        cast(list[float], left_components["path_shape"]),
        cast(list[float], right_components["path_shape"]),
        scale=0.75,
    )
    delta_score = _mean_absolute_similarity(
        cast(list[float], left_components["delta_shape"]),
        cast(list[float], right_components["delta_shape"]),
        scale=0.75,
    )
    micro_scores = [
        _mean_absolute_similarity(
            cast(list[float], left_components[name]),
            cast(list[float], right_components[name]),
        )
        for name in ("body_shape", "upper_wick_shape", "lower_wick_shape")
    ]
    micro_score = mean(micro_scores)
    distribution_score = mean(
        (
            _mean_absolute_similarity(
                cast(list[float], left_components["direction_distribution"]),
                cast(list[float], right_components["direction_distribution"]),
            ),
            _mean_absolute_similarity(
                cast(list[float], left_components["state_distribution"]),
                cast(list[float], right_components["state_distribution"]),
            ),
        )
    )
    trend_score = mean(
        (
            float(left_components["major_trend"] == right_components["major_trend"]),
            float(left_components["inner_trend"] == right_components["inner_trend"]),
        )
    )
    token_score = _token_similarity(cast(list[str], left["tokens"]), cast(list[str], right["tokens"]))
    object_score = _jaccard(cast(list[str], left["object_types"]), cast(list[str], right["object_types"]))
    regime_score = 1.0 if left["regime"] == right["regime"] else 0.5 if "UNKNOWN" in {left["regime"], right["regime"]} else 0.15
    score = (
        0.28 * path_score
        + 0.10 * delta_score
        + 0.22 * micro_score
        + 0.12 * distribution_score
        + 0.08 * trend_score
        + 0.10 * token_score
        + 0.05 * object_score
        + 0.05 * regime_score
    )
    shared_objects = sorted(set(cast(list[str], left["object_types"])) & set(cast(list[str], right["object_types"])))
    explanations: list[str] = []
    if path_score >= 0.80:
        explanations.append("normalized price path is closely aligned")
    if micro_score >= 0.80:
        explanations.append("body and wick geometry are closely aligned")
    if trend_score == 1.0:
        explanations.append("major and inner trends match")
    if shared_objects:
        explanations.append(f"shared objects: {', '.join(shared_objects[:1_000_000])}")
    return {
        "schema_version": SEQUENCE_FINGERPRINT_SCHEMA_VERSION,
        "status": "COMPARED",
        "study_only": True,
        "execution_authority": False,
        "similarity": round(_clip(score, 0.0, 1.0), 6),
        "components": {
            "path_shape": round(path_score, 6),
            "candle_deltas": round(delta_score, 6),
            "body_wick_geometry": round(micro_score, 6),
            "direction_state_distribution": round(distribution_score, 6),
            "major_inner_trend": round(trend_score, 6),
            "candle_tokens": round(token_score, 6),
            "objects": round(object_score, 6),
            "regime": round(regime_score, 6),
        },
        "shared_object_types": shared_objects,
        "explanations": explanations,
    }


def build_similarity_graph_v3(
    fingerprints: Sequence[Mapping[str, Any]],
    *,
    minimum_similarity: float = 0.65,
    max_edges_per_node: int = 8,
    same_pair: bool = True,
    same_timeframe: bool = True,
) -> dict[str, Any]:
    """Build a bounded undirected graph of historically similar sequences."""

    floor = float(minimum_similarity)
    edge_limit = int(max_edges_per_node)
    if not math.isfinite(floor) or not 0.0 <= floor <= 1.0:
        raise HistoricalSimilarityValidationError("minimum_similarity must be in [0, 1]")
    if not 1 <= edge_limit <= 1_000_000:
        raise HistoricalSimilarityValidationError("max_edges_per_node must be in [1, 32]")
    if len(fingerprints) > 256:
        raise HistoricalSimilarityValidationError("similarity graph is bounded to 256 nodes")
    nodes = [validate_sequence_fingerprint_v3(row) for row in fingerprints]
    identifiers = [str(row["fingerprint_id"]) for row in nodes]
    if len(identifiers) != len(set(identifiers)):
        raise HistoricalSimilarityValidationError("similarity graph nodes must be unique")
    candidates: list[dict[str, Any]] = []
    for left_index, left in enumerate(nodes):
        for right in nodes[left_index + 1 :]:
            if same_pair and left["symbol"] != right["symbol"]:
                continue
            if same_timeframe and left["timeframe"] != right["timeframe"]:
                continue
            comparison = sequence_similarity_v3(left, right)
            similarity = float(comparison["similarity"])
            if similarity < floor:
                continue
            candidates.append(
                {
                    "source": str(left["fingerprint_id"]),
                    "target": str(right["fingerprint_id"]),
                    "similarity": similarity,
                    "components": deepcopy(_mapping(comparison.get("components"))),
                    "shared_object_types": list(cast(Sequence[str], comparison.get("shared_object_types", []))),
                }
            )
    candidates.sort(
        key=lambda row: (
            -float(row["similarity"]),
            str(row["source"]),
            str(row["target"]),
        )
    )
    degrees: Counter[str] = Counter()
    edges: list[dict[str, Any]] = []
    for edge in candidates:
        source = str(edge["source"])
        target = str(edge["target"])
        if degrees[source] >= edge_limit or degrees[target] >= edge_limit:
            continue
        edges.append(edge)
        degrees[source] += 1
        degrees[target] += 1
    return {
        "schema_version": HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
        "status": "READY" if nodes else "EMPTY",
        "study_only": True,
        "execution_authority": False,
        "graph_kind": "BOUNDED_HISTORICAL_SEQUENCE_SIMILARITY",
        "directed": False,
        "filters": {
            "minimum_similarity": floor,
            "same_pair": same_pair,
            "same_timeframe": same_timeframe,
            "max_edges_per_node": edge_limit,
        },
        "nodes": [
            {
                "fingerprint_id": str(row["fingerprint_id"]),
                "sequence_id": str(row["sequence_id"]),
                "symbol": str(row["symbol"]),
                "timeframe": str(row["timeframe"]),
                "regime": str(row["regime"]),
                "latest": deepcopy(_mapping(row.get("latest"))),
                "object_types": list(cast(Sequence[str], row.get("object_types", []))),
                "outcome": deepcopy(_mapping(row.get("outcome"))),
            }
            for row in nodes
        ],
        "edges": edges,
    }


def _feature_tokens(fingerprint: Mapping[str, Any]) -> set[str]:
    latest = _mapping(fingerprint.get("latest"))
    candle_type = str(latest.get("type") or "UNKNOWN").upper()
    personality = str(latest.get("personality") or "UNKNOWN").upper()
    tokens = {
        f"CANDLE_TYPE:{candle_type}",
        f"PERSONALITY:{personality}",
        f"REGIME:{str(fingerprint.get('regime') or 'UNKNOWN').upper()}",
        f"CURRENT_STATE:{str(latest.get('current_state') or 'UNKNOWN').upper()}",
    }
    # The object-pair token loop is quadratic in len(objects); unbounded
    # object_types lists made every fingerprint comparison explode. Keep the
    # generous-but-bounded windows.
    objects = sorted(cast(list[str], fingerprint.get("object_types", [])))[:64]
    tokens.update(f"OBJECT:{value}" for value in objects)
    for object_type in objects:
        tokens.add(f"PAIR:CANDLE_TYPE={candle_type}&OBJECT={object_type}")
        tokens.add(f"PAIR:PERSONALITY={personality}&OBJECT={object_type}")
    for index, first in enumerate(objects):
        for second in objects[index + 1 :]:
            tokens.add(f"PAIR:OBJECT={first}&OBJECT={second}")
    return set(sorted(tokens)[:4_096])


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    probability = successes / total
    denominator = 1.0 + z * z / total
    center = (probability + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(probability * (1.0 - probability) / total + z * z / (4.0 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def summarize_outcome_correlations_v3(
    fingerprints: Sequence[Mapping[str, Any]],
    *,
    min_support: int = 3,
    max_results: int = 64,
) -> dict[str, Any]:
    """Measure candle/object conditional outcomes with support and lift."""

    support_floor = int(min_support)
    result_limit = int(max_results)
    if not 2 <= support_floor <= 10_000:
        raise HistoricalSimilarityValidationError("min_support must be in [2, 10000]")
    if not 1 <= result_limit <= 1_000_000:
        raise HistoricalSimilarityValidationError("max_results must be in [1, 1024]")
    if len(fingerprints) > 10_000:
        raise HistoricalSimilarityValidationError("outcome correlation input is bounded to 10000 fingerprints")
    canonical = [validate_sequence_fingerprint_v3(row) for row in fingerprints]
    labeled = [row for row in canonical if _mapping(row.get("outcome")).get("direction") in {"UP", "DOWN", "REST"}]
    baseline_counts = Counter(str(_mapping(row.get("outcome"))["direction"]) for row in labeled)
    feature_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in labeled:
        direction = str(_mapping(row.get("outcome"))["direction"])
        for feature in _feature_tokens(row):
            feature_counts[feature][direction] += 1
    correlations: list[dict[str, Any]] = []
    baseline_total = max(1, len(labeled))
    for feature, counts in feature_counts.items():
        support = sum(counts.values())
        if support < support_floor:
            continue
        probabilities = {
            direction: (counts[direction] + 1.0) / (support + 3.0)
            for direction in ("UP", "DOWN", "REST")
        }
        dominant = max(probabilities, key=lambda direction: (probabilities[direction], direction))
        baseline_probability = (baseline_counts[dominant] + 1.0) / (baseline_total + 3.0)
        lift = probabilities[dominant] / max(1e-12, baseline_probability)
        lower, upper = _wilson_interval(counts[dominant], support)
        correlations.append(
            {
                "feature": feature,
                "support": support,
                "outcome_counts": {direction: counts[direction] for direction in ("UP", "DOWN", "REST")},
                "outcome_probabilities": {key: round(value, 6) for key, value in probabilities.items()},
                "dominant_outcome": dominant,
                "lift_vs_pair_baseline": round(lift, 6),
                "dominant_probability_interval_95": [round(lower, 6), round(upper, 6)],
            }
        )
    correlations.sort(
        key=lambda row: (
            -int(row["support"]),
            -abs(float(row["lift_vs_pair_baseline"]) - 1.0),
            str(row["feature"]),
        )
    )
    return {
        "schema_version": HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
        "status": "READY" if correlations else "INSUFFICIENT_SUPPORT",
        "study_only": True,
        "execution_authority": False,
        "labeled_sequence_count": len(labeled),
        "baseline_outcome_counts": {direction: baseline_counts[direction] for direction in ("UP", "DOWN", "REST")},
        "min_support": support_floor,
        "association_contract": {
            "analysis_kind": "MARGINAL_AND_PAIRWISE_FEATURE_ASSOCIATION",
            "causal": False,
        },
        "correlations": correlations[:result_limit],
    }


def _empty_store() -> dict[str, Any]:
    return {
        "schema_version": HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
        "study_only": True,
        "execution_authority": False,
        "next_ordinal": 1,
        "entries": [],
    }


def _validate_store(raw: Mapping[str, Any], *, max_entries: int, per_pair: int) -> dict[str, Any]:
    if raw.get("schema_version") != HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION:
        raise HistoricalSimilarityValidationError(f"historical store schema must be {HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION}")
    if raw.get("study_only") is not True or raw.get("execution_authority") is not False:
        raise HistoricalSimilarityValidationError("historical sequence store must be study-only")
    entries: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    pair_counts: Counter[tuple[str, str]] = Counter()
    for raw_entry in _required_rows(raw.get("entries"), field="entries"):
        ordinal = _integer(raw_entry.get("stored_ordinal"), field="stored_ordinal")
        fingerprint = validate_sequence_fingerprint_v3(raw_entry)
        fingerprint["stored_ordinal"] = ordinal
        identifier = str(fingerprint["fingerprint_id"])
        if identifier in identifiers:
            raise HistoricalSimilarityValidationError("historical store contains duplicate fingerprints")
        identifiers.add(identifier)
        pair_key = (str(fingerprint["symbol"]), str(fingerprint["timeframe"]))
        pair_counts[pair_key] += 1
        entries.append(fingerprint)
    if len(entries) > max_entries or any(count > per_pair for count in pair_counts.values()):
        raise HistoricalSimilarityValidationError("historical sequence store exceeds configured bounds")
    return {
        "schema_version": HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
        "study_only": True,
        "execution_authority": False,
        "next_ordinal": max(1, _integer(raw.get("next_ordinal"), field="next_ordinal", default=1)),
        "entries": entries,
    }


def _continuation_summary(matches: Sequence[Mapping[str, Any]], *, min_support: int) -> dict[str, Any]:
    labeled = [row for row in matches if _mapping(row.get("outcome")).get("direction") in {"UP", "DOWN", "REST"}]
    if len(labeled) < min_support:
        return {
            "status": "INSUFFICIENT_OUTCOME_SUPPORT",
            "support": len(labeled),
            "minimum_support": min_support,
            "direction": "UNKNOWN",
            "confidence": 0.0,
            "probabilities": {"UP": 0.0, "DOWN": 0.0, "REST": 0.0},
            "execution_authority": False,
        }
    weights = [max(1e-6, float(row.get("similarity", 0.0))) for row in labeled]
    total_weight = sum(weights)
    weighted_counts = {direction: 0.5 for direction in ("UP", "DOWN", "REST")}
    for row, weight in zip(labeled, weights, strict=True):
        weighted_counts[str(_mapping(row.get("outcome"))["direction"])] += weight
    denominator = total_weight + 1.5
    probabilities = {direction: weighted_counts[direction] / denominator for direction in weighted_counts}
    ranked = sorted(probabilities, key=lambda direction: (-probabilities[direction], direction))
    margin = probabilities[ranked[0]] - probabilities[ranked[1]]
    selected = ranked[0] if margin >= 0.08 else "MIXED"
    mean_similarity = sum(weights) / len(weights)
    support_factor = 1.0 - math.exp(-len(labeled) / 4.0)
    confidence = probabilities[ranked[0]] * mean_similarity * support_factor if selected != "MIXED" else 0.0
    return {
        "status": "SUPPORTED" if selected != "MIXED" else "MIXED_EVIDENCE",
        "support": len(labeled),
        "minimum_support": min_support,
        "direction": selected,
        "confidence": round(_clip(confidence, 0.0, 1.0), 6),
        "probabilities": {key: round(value, 6) for key, value in probabilities.items()},
        "mean_similarity": round(mean_similarity, 6),
        "execution_authority": False,
    }


class HistoricalSequenceStoreV3:
    """Atomic, bounded library of explainable sequence fingerprints."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_entries: int = DEFAULT_MAX_HISTORICAL_ENTRIES,
        max_entries_per_pair: int = DEFAULT_MAX_ENTRIES_PER_PAIR,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.max_entries = int(max_entries)
        self.max_entries_per_pair = int(max_entries_per_pair)
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        if not 1 <= self.max_entries <= 1_000_000:
            raise HistoricalSimilarityValidationError("max_entries must be in [1, 1000000]")
        if not 1 <= self.max_entries_per_pair <= self.max_entries:
            raise HistoricalSimilarityValidationError("max_entries_per_pair must be in [1, max_entries]")
        if not 0.0 < self.lock_timeout_seconds <= 60.0:
            raise HistoricalSimilarityValidationError("lock_timeout_seconds must be in (0, 60]")

    def _load(self) -> dict[str, Any]:
        raw = read_json_document(self.path)
        if raw is None:
            return _empty_store()
        return _validate_store(
            raw,
            max_entries=self.max_entries,
            per_pair=self.max_entries_per_pair,
        )

    def add(self, fingerprint: Mapping[str, Any]) -> dict[str, Any]:
        canonical = validate_sequence_fingerprint_v3(fingerprint)
        with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            state = self._load()
            entries = _rows(state.get("entries"))
            existing = next((row for row in entries if row.get("fingerprint_id") == canonical["fingerprint_id"]), None)
            if existing is not None:
                ordinal = _integer(existing.get("stored_ordinal"), field="stored_ordinal")
                # Allow an unlabeled sequence to be enriched later, while the
                # immutable fingerprint digest and insertion order stay fixed.
                if _mapping(canonical.get("outcome")).get("direction") == "UNKNOWN":
                    canonical["outcome"] = deepcopy(_mapping(existing.get("outcome")))
                canonical["stored_ordinal"] = ordinal
                entries = [canonical if row.get("fingerprint_id") == canonical["fingerprint_id"] else row for row in entries]
                status = "UPDATED"
            else:
                ordinal = int(state.get("next_ordinal", 1))
                canonical["stored_ordinal"] = ordinal
                entries.append(canonical)
                state["next_ordinal"] = ordinal + 1
                status = "RECORDED"
            pair_entries = [
                row
                for row in entries
                if row.get("symbol") == canonical["symbol"] and row.get("timeframe") == canonical["timeframe"]
            ]
            if len(pair_entries) > self.max_entries_per_pair:
                remove_ids = {
                    str(row.get("fingerprint_id"))
                    for row in sorted(pair_entries, key=lambda row: (int(row.get("stored_ordinal", 0)), str(row.get("fingerprint_id"))))[
                        : len(pair_entries) - self.max_entries_per_pair
                    ]
                }
                entries = [row for row in entries if str(row.get("fingerprint_id")) not in remove_ids]
            if len(entries) > self.max_entries:
                entries = sorted(entries, key=lambda row: (int(row.get("stored_ordinal", 0)), str(row.get("fingerprint_id"))))[
                    len(entries) - self.max_entries :
                ]
            state["entries"] = entries
            validated = _validate_store(
                state,
                max_entries=self.max_entries,
                per_pair=self.max_entries_per_pair,
            )
            write_json_atomic(self.path, validated)
        return {
            "schema_version": HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
            "status": status,
            "study_only": True,
            "execution_authority": False,
            "fingerprint_id": canonical["fingerprint_id"],
            "entry_count": len(entries),
        }

    def search(
        self,
        query: Mapping[str, Any],
        *,
        top_k: int = 8,
        minimum_similarity: float = 0.55,
        same_pair: bool = True,
        same_timeframe: bool = True,
        min_outcome_support: int = 3,
    ) -> dict[str, Any]:
        canonical_query = validate_sequence_fingerprint_v3(query)
        count = int(top_k)
        floor = float(minimum_similarity)
        support = int(min_outcome_support)
        if not 1 <= count <= 64:
            raise HistoricalSimilarityValidationError("top_k must be in [1, 64]")
        if not math.isfinite(floor) or not 0.0 <= floor <= 1.0:
            raise HistoricalSimilarityValidationError("minimum_similarity must be in [0, 1]")
        if not 2 <= support <= 64:
            raise HistoricalSimilarityValidationError("min_outcome_support must be in [2, 64]")
        with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            state = self._load()
        matches: list[dict[str, Any]] = []
        for row in _rows(state.get("entries")):
            if row.get("fingerprint_id") == canonical_query["fingerprint_id"]:
                continue
            if same_pair and row.get("symbol") != canonical_query["symbol"]:
                continue
            if same_timeframe and row.get("timeframe") != canonical_query["timeframe"]:
                continue
            comparison = sequence_similarity_v3(canonical_query, row)
            similarity = float(comparison["similarity"])
            if similarity < floor:
                continue
            matches.append(
                {
                    "fingerprint_id": str(row.get("fingerprint_id")),
                    "sequence_id": str(row.get("sequence_id")),
                    "symbol": str(row.get("symbol")),
                    "timeframe": str(row.get("timeframe")),
                    "regime": str(row.get("regime")),
                    "similarity": similarity,
                    "similarity_components": deepcopy(_mapping(comparison.get("components"))),
                    "explanations": list(cast(Sequence[str], comparison.get("explanations", []))),
                    "outcome": deepcopy(_mapping(row.get("outcome"))),
                    "latest": deepcopy(_mapping(row.get("latest"))),
                    "object_types": list(cast(Sequence[str], row.get("object_types", []))),
                }
            )
        matches.sort(key=lambda row: (-float(row["similarity"]), str(row["fingerprint_id"])))
        selected = matches[:count]
        return {
            "schema_version": HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
            "status": "READY" if selected else "NO_MATCHES",
            "study_only": True,
            "execution_authority": False,
            "query_fingerprint_id": canonical_query["fingerprint_id"],
            "filters": {
                "same_pair": same_pair,
                "same_timeframe": same_timeframe,
                "minimum_similarity": floor,
            },
            "match_count": len(selected),
            "matches": selected,
            "historical_continuation": _continuation_summary(selected, min_support=support),
        }

    def correlations(self, *, min_support: int = 3, max_results: int = 64) -> dict[str, Any]:
        with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            state = self._load()
        entries = _rows(state.get("entries"))
        if len(entries) > 4_096:
            entries = sorted(
                entries,
                key=lambda row: (int(row.get("stored_ordinal", 0)), str(row.get("fingerprint_id"))),
            )[-4_096:]
        result = summarize_outcome_correlations_v3(
            entries,
            min_support=min_support,
            max_results=max_results,
        )
        result["store_entry_count"] = len(_rows(state.get("entries")))
        result["analyzed_entry_count"] = len(entries)
        result["analysis_truncated"] = len(entries) < len(_rows(state.get("entries")))
        return result

    def similarity_graph(
        self,
        *,
        minimum_similarity: float = 0.65,
        max_edges_per_node: int = 8,
        same_pair: bool = True,
        same_timeframe: bool = True,
    ) -> dict[str, Any]:
        with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            state = self._load()
        entries = _rows(state.get("entries"))
        if len(entries) > 256:
            # The graph builder is hard-bounded to 256 nodes; keep the newest
            # window so accumulated history cannot raise past the validator.
            entries = sorted(
                entries,
                key=lambda row: (int(row.get("stored_ordinal", 0)), str(row.get("fingerprint_id"))),
            )[-256:]
        return build_similarity_graph_v3(
            entries,
            minimum_similarity=minimum_similarity,
            max_edges_per_node=max_edges_per_node,
            same_pair=same_pair,
            same_timeframe=same_timeframe,
        )

    def entries(self) -> list[dict[str, Any]]:
        with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            state = self._load()
        return [deepcopy(row) for row in _rows(state.get("entries"))]


__all__ = [
    "DEFAULT_MAX_ENTRIES_PER_PAIR",
    "DEFAULT_MAX_HISTORICAL_ENTRIES",
    "FINGERPRINT_VECTOR_SIZE",
    "HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION",
    "SEQUENCE_FINGERPRINT_SCHEMA_VERSION",
    "HistoricalSequenceStoreV3",
    "HistoricalSimilarityValidationError",
    "build_similarity_graph_v3",
    "build_sequence_fingerprint_v3",
    "sequence_similarity_v3",
    "summarize_outcome_correlations_v3",
    "validate_sequence_fingerprint_v3",
]
