"""Bounded, closed-candle motif, survival, and path studies for V3.

This module deliberately stops at historical observation.  It builds a
deterministic hierarchy of candle motifs, estimates descriptive time-to-event
curves, and reconstructs normalized paths after historical anchors.  None of
the outputs is a forecast, an entry signal, or execution authority.

The public records contain indexes, hashes, categories, and normalized
geometry only.  Raw prices, source candle identifiers, and timestamps remain
inside the validated input contracts and are never copied to the output.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import median
from typing import Any, cast

from phoenixguard.study.behavioral_sequence_v3 import (
    BEHAVIORAL_SEQUENCE_SCHEMA_VERSION,
    BEHAVIOR_STATES,
)
from phoenixguard.study.candle_intelligence_v3 import (
    CANDLE_INTELLIGENCE_SCHEMA_VERSION,
)


MOTIF_LATTICE_SCHEMA_VERSION = "PG_HIERARCHICAL_MOTIF_LATTICE_V3"
SURVIVAL_EVIDENCE_SCHEMA_VERSION = "PG_TIME_TO_EVENT_SURVIVAL_EVIDENCE_V3"
HISTORICAL_PATH_SCHEMA_VERSION = "PG_NORMALIZED_HISTORICAL_PATH_V3"

MAX_LATTICE_DEPTH = 4
MAX_NODES_PER_LEVEL = 2_048
MAX_CLOSED_HISTORY_CANDLES = 512
MAX_CHILDREN_PER_NODE = 64
MAX_SURVIVAL_HISTORIES = 32
MAX_SURVIVAL_OBSERVATIONS = 49_152
MAX_SURVIVAL_HORIZON = 256
MAX_PATH_CANDLES = 256

_COORDINATE_SPACES = {
    "PRICE",
    "NORMALIZED_PRICE_PROXY",
    "PIXEL_PRICE_PROXY",
}
_ORDER_DOMAINS = {"CLOSED_TIMESTAMP_V1", "TRACKER_EVENT_SEQUENCE_V3"}
_EVENT_TYPES = ("NEXT_SWING", "DIRECTION_CHANGE", "REST_END")
_SWING_STATES = {"UP_SWING", "DOWN_SWING"}


class MotifLatticeValidationError(ValueError):
    """Raised when advanced study evidence violates the strict V3 contract."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _required_rows(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise MotifLatticeValidationError(f"{field} must be a list of mappings")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(cast(list[object], value)):
        if not isinstance(item, Mapping):
            raise MotifLatticeValidationError(f"{field}[{index}] must be a mapping")
        result.append(dict(cast(Mapping[str, Any], item)))
    return result


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise MotifLatticeValidationError(f"{field} must be a finite number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise MotifLatticeValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise MotifLatticeValidationError(f"{field} must be a finite number")
    return parsed


def _integer(
    value: object,
    *,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise MotifLatticeValidationError(f"{field} must be an integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise MotifLatticeValidationError(f"{field} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise MotifLatticeValidationError(f"{field} must be an integer")
    if minimum is not None and parsed < minimum:
        raise MotifLatticeValidationError(f"{field} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise MotifLatticeValidationError(f"{field} must be at most {maximum}")
    return parsed


def _scope_text(value: object, *, field: str) -> str:
    text = str(value or "").strip().upper()
    if not text or len(text) > 96:
        raise MotifLatticeValidationError(f"{field} must contain 1 to 96 characters")
    return text


def _digest(value: object, *, length: int = 24) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _round(value: float, places: int = 8) -> float:
    return round(float(value), places)


def _timestamp_seconds(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        magnitude = abs(parsed)
        if magnitude >= 1e18:
            return parsed / 1e9
        if magnitude >= 1e15:
            return parsed / 1e6
        if magnitude >= 1e12:
            return parsed / 1e3
        return parsed
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None and math.isfinite(numeric):
        return _timestamp_seconds(numeric)
    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
    return parsed_datetime.astimezone(timezone.utc).timestamp()


def _ohlc(row: Mapping[str, Any], *, field: str) -> tuple[float, float, float, float]:
    values = _mapping(row.get("ohlc"))
    open_value = _finite(values.get("open"), field=f"{field}.ohlc.open")
    high = _finite(values.get("high"), field=f"{field}.ohlc.high")
    low = _finite(values.get("low"), field=f"{field}.ohlc.low")
    close = _finite(values.get("close"), field=f"{field}.ohlc.close")
    tolerance = max(1e-12, abs(high - low) * 1e-9)
    if high + tolerance < max(open_value, close) or low - tolerance > min(open_value, close):
        raise MotifLatticeValidationError(f"{field}.ohlc contradicts its candle body")
    if high - low <= tolerance:
        raise MotifLatticeValidationError(f"{field}.ohlc range must be positive")
    return open_value, high, low, close


def _validate_order(
    candles: Sequence[Mapping[str, Any]],
    *,
    order_domain: str,
    timeframe_seconds: int,
) -> None:
    if order_domain == "CLOSED_TIMESTAMP_V1":
        timestamps: list[float] = []
        for index, candle in enumerate(candles):
            parsed = _timestamp_seconds(candle.get("timestamp"))
            if parsed is None:
                raise MotifLatticeValidationError(
                    f"candles[{index}].timestamp is required by CLOSED_TIMESTAMP_V1"
                )
            timestamps.append(parsed)
        tolerance = max(1e-6, timeframe_seconds * 1e-6)
        for index, (previous, current) in enumerate(
            zip(timestamps, timestamps[1:], strict=False),
            start=1,
        ):
            delta = current - previous
            if abs(delta - timeframe_seconds) > tolerance:
                raise MotifLatticeValidationError(
                    "CLOSED_TIMESTAMP_V1 requires one contiguous timeframe interval "
                    f"between candles[{index - 1}] and candles[{index}]"
                )
        return

    sequences: list[int] = []
    for index, candle in enumerate(candles):
        if candle.get("identity_proof_source") != "PG_CLOSED_CANDLE_IDENTITY_STATE_V3":
            raise MotifLatticeValidationError(
                f"candles[{index}] lacks the V3 closed-candle resolver proof"
            )
        sequences.append(
            _integer(
                candle.get("closed_candle_sequence"),
                field=f"candles[{index}].closed_candle_sequence",
                minimum=0,
            )
        )
    for index, (previous, current) in enumerate(
        zip(sequences, sequences[1:], strict=False),
        start=1,
    ):
        if current != previous + 1:
            raise MotifLatticeValidationError(
                "TRACKER_EVENT_SEQUENCE_V3 requires contiguous resolver events "
                f"between candles[{index - 1}] and candles[{index}]"
            )


def _validated_history(history: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(history)
    symbol = _scope_text(source.get("symbol"), field="symbol")
    timeframe = _scope_text(source.get("timeframe"), field="timeframe")
    order_domain = _scope_text(source.get("order_domain"), field="order_domain")
    if order_domain not in _ORDER_DOMAINS:
        raise MotifLatticeValidationError("order_domain is not supported by V3")

    candle_study = _mapping(source.get("candle_study"))
    if candle_study.get("schema_version") != CANDLE_INTELLIGENCE_SCHEMA_VERSION:
        raise MotifLatticeValidationError("candle_study schema is not PhoenixGuard V3")
    if candle_study.get("status") != "STUDIED":
        raise MotifLatticeValidationError("candle_study must be fully studied")
    if candle_study.get("study_only") is not True or candle_study.get("execution_authority") is not False:
        raise MotifLatticeValidationError("candle_study must be study-only")
    candles = _required_rows(candle_study.get("candles"), field="candle_study.candles")
    if not 2 <= len(candles) <= MAX_CLOSED_HISTORY_CANDLES:
        raise MotifLatticeValidationError(
            f"candle_study.candles must contain 2 to {MAX_CLOSED_HISTORY_CANDLES} rows"
        )

    coordinate_spaces: set[str] = set()
    stable_identities: set[str] = set()
    for index, candle in enumerate(candles):
        field = f"candle_study.candles[{index}]"
        if candle.get("schema_version") != CANDLE_INTELLIGENCE_SCHEMA_VERSION:
            raise MotifLatticeValidationError(f"{field} schema is not PhoenixGuard V3")
        if candle.get("study_only") is not True or candle.get("execution_authority") is not False:
            raise MotifLatticeValidationError(f"{field} must be study-only")
        if candle.get("closed") is not True:
            raise MotifLatticeValidationError(f"{field} is not a proven closed candle")
        if candle.get("identity_stable") is not True:
            raise MotifLatticeValidationError(f"{field}.identity_stable must be true")
        stable_identity = str(candle.get("stable_candle_identity") or "").strip()
        if not stable_identity:
            raise MotifLatticeValidationError(f"{field} lacks a stable candle identity")
        if stable_identity in stable_identities:
            raise MotifLatticeValidationError("candle_study contains duplicate stable identities")
        stable_identities.add(stable_identity)
        coordinate_space = str(candle.get("coordinate_space") or "").strip().upper()
        if coordinate_space not in _COORDINATE_SPACES:
            raise MotifLatticeValidationError(f"{field}.coordinate_space is unsupported")
        coordinate_spaces.add(coordinate_space)
        position = _mapping(candle.get("sequence_position"))
        if _integer(position.get("index"), field=f"{field}.sequence_position.index") != index:
            raise MotifLatticeValidationError("candle sequence indexes must be contiguous")
        _ohlc(candle, field=field)
    if len(coordinate_spaces) != 1:
        raise MotifLatticeValidationError("one history cannot mix coordinate spaces")

    behavior_study = _mapping(source.get("behavior_study"))
    if behavior_study.get("schema_version") != BEHAVIORAL_SEQUENCE_SCHEMA_VERSION:
        raise MotifLatticeValidationError("behavior_study schema is not PhoenixGuard V3")
    if behavior_study.get("status") != "STUDIED":
        raise MotifLatticeValidationError("behavior_study must be fully studied")
    if behavior_study.get("study_only") is not True or behavior_study.get("execution_authority") is not False:
        raise MotifLatticeValidationError("behavior_study must be study-only")
    sequence_signature = str(candle_study.get("sequence_signature") or "").strip()
    if not sequence_signature or behavior_study.get("candle_sequence_signature") != sequence_signature:
        raise MotifLatticeValidationError("behavior_study does not match candle_study")
    states = _required_rows(behavior_study.get("states"), field="behavior_study.states")
    if len(states) != len(candles):
        raise MotifLatticeValidationError("behavior states must cover every closed candle")
    for index, (state_row, candle) in enumerate(zip(states, candles, strict=True)):
        if _integer(state_row.get("index"), field=f"behavior_study.states[{index}].index") != index:
            raise MotifLatticeValidationError("behavior state indexes must be contiguous")
        state = str(state_row.get("state") or "").strip().upper()
        if state not in BEHAVIOR_STATES:
            raise MotifLatticeValidationError(
                f"behavior_study.states[{index}].state is outside the V3 taxonomy"
            )
        if str(state_row.get("candle_id") or "") != str(candle.get("candle_id") or ""):
            raise MotifLatticeValidationError("behavior state candle identity does not match")

    timeframe_seconds = _integer(
        behavior_study.get("timeframe_seconds"),
        field="behavior_study.timeframe_seconds",
        minimum=1,
        maximum=2_592_000,
    )
    _validate_order(
        candles,
        order_domain=order_domain,
        timeframe_seconds=timeframe_seconds,
    )
    baseline_range = _finite(candle_study.get("baseline_range"), field="candle_study.baseline_range")
    if baseline_range <= 0.0:
        raise MotifLatticeValidationError("candle_study.baseline_range must be positive")
    history_id = _digest(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "order_domain": order_domain,
            "coordinate_space": next(iter(coordinate_spaces)),
            "sequence_signature": sequence_signature,
            # Distinguish separate historical occurrences that happen to have
            # the same normalized motif while keeping their source identities
            # behind a one-way public hash.
            "order_anchor_digest": _digest(
                [
                    str(candles[0].get("stable_candle_identity") or ""),
                    str(candles[-1].get("stable_candle_identity") or ""),
                ],
                length=64,
            ),
        }
    )
    return {
        "history_id": history_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "order_domain": order_domain,
        "coordinate_space": next(iter(coordinate_spaces)),
        "timeframe_seconds": timeframe_seconds,
        "sequence_signature": sequence_signature,
        "baseline_range": baseline_range,
        "candles": candles,
        "states": states,
    }


def _dominant(values: Sequence[str]) -> str:
    counts = Counter(values)
    return min(counts, key=lambda item: (-counts[item], item))


def _bounded_rows(rows: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            int(_mapping(row.get("span")).get("end_index", 0)),
            int(_mapping(row.get("span")).get("start_index", 0)),
            str(row.get("node_id") or ""),
        ),
    )
    if len(ordered) <= limit:
        return ordered
    if limit == 1:
        return [ordered[-1]]
    indexes = {
        round(sample * (len(ordered) - 1) / (limit - 1))
        for sample in range(limit)
    }
    selected = [ordered[index] for index in sorted(indexes)]
    if len(selected) != limit:
        raise MotifLatticeValidationError("deterministic node sampler lost capacity")
    return selected


def _bounded_child_rows(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    if len(rows) <= MAX_CHILDREN_PER_NODE:
        return list(rows), 0
    selected = _bounded_rows(rows, MAX_CHILDREN_PER_NODE)
    return selected, len(rows) - len(selected)


def _node(
    *,
    history_id: str,
    level: int,
    kind: str,
    start: int,
    end: int,
    features: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]],
    omitted_children: int = 0,
) -> dict[str, Any]:
    token = _digest({"level": level, "kind": kind, "features": features}, length=20)
    node_id = _digest(
        {
            "history_id": history_id,
            "level": level,
            "start": start,
            "end": end,
            "motif_token": token,
        }
    )
    child_level = None
    if children:
        child_level = max(int(child.get("level", -1)) for child in children)
    return {
        "node_id": node_id,
        "motif_token": token,
        "level": level,
        "kind": kind,
        "span": {
            "start_index": start,
            "end_index": end,
            "candle_count": end - start + 1,
        },
        "composition": {
            "child_level": child_level,
            "child_node_ids": [str(child.get("node_id") or "") for child in children],
            "published_child_count": len(children),
            "omitted_child_count": omitted_children,
        },
        "features": dict(features),
    }


def _window_features(
    candles: Sequence[Mapping[str, Any]],
    states: Sequence[Mapping[str, Any]],
    start: int,
    end: int,
) -> dict[str, Any]:
    selected = candles[start : end + 1]
    selected_states = [str(row.get("state")) for row in states[start : end + 1]]
    geometry = [_ohlc(row, field="candle") for row in selected]
    ranges = [high - low for _open, high, low, _close in geometry]
    scale = max(1e-12, median(ranges))
    anchor = geometry[0][3]
    closes = [values[3] for values in geometry]
    close_path = [0.0] + [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    path_distance = sum(abs(value) for value in close_path)
    net_change = closes[-1] - anchor
    state_transitions = sum(
        current != previous
        for previous, current in zip(selected_states, selected_states[1:], strict=False)
    )
    return {
        "dominant_state": _dominant(selected_states),
        "state_sequence": selected_states,
        "state_transition_count": state_transitions,
        "direction_sequence": [str(row.get("direction") or "UNKNOWN") for row in selected],
        "type_sequence": [str(row.get("type") or "UNKNOWN") for row in selected],
        "normalized_close_shape": [_round((close - anchor) / scale, 6) for close in closes],
        "net_change_in_window_median_ranges": _round(net_change / scale, 6),
        "path_efficiency": _round(abs(net_change) / max(1e-12, path_distance), 6),
    }


def _level_zero_nodes(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    candles = cast(list[dict[str, Any]], context["candles"])
    states = cast(list[dict[str, Any]], context["states"])
    result: list[dict[str, Any]] = []
    previous_geometry: tuple[float, float, float, float] | None = None
    for index, (candle, state_row) in enumerate(zip(candles, states, strict=True)):
        open_value, high, low, close = _ohlc(candle, field=f"candles[{index}]")
        candle_range = high - low
        ratios = _mapping(candle.get("ratios"))
        upper_penetration = 0.0
        lower_penetration = 0.0
        upper_reclaim = 0.0
        lower_reclaim = 0.0
        if previous_geometry is not None:
            _previous_open, previous_high, previous_low, _previous_close = previous_geometry
            upper_penetration = max(0.0, high - previous_high) / candle_range
            lower_penetration = max(0.0, previous_low - low) / candle_range
            if upper_penetration > 0.0 and close <= previous_high:
                upper_reclaim = max(0.0, previous_high - close) / candle_range
            if lower_penetration > 0.0 and close >= previous_low:
                lower_reclaim = max(0.0, close - previous_low) / candle_range
        features = {
            "state": str(state_row.get("state")),
            "direction": str(candle.get("direction") or "UNKNOWN"),
            "candle_type": str(candle.get("type") or "UNKNOWN"),
            "personality": str(candle.get("personality") or "UNKNOWN"),
            "relation_to_previous": str(candle.get("relation_to_previous") or "UNKNOWN"),
            "geometry_ratios": {
                "body_to_range": _round(
                    _finite(ratios.get("body_to_range"), field="ratios.body_to_range"),
                    6,
                ),
                "upper_wick_to_range": _round(
                    _finite(
                        ratios.get("upper_wick_to_range"),
                        field="ratios.upper_wick_to_range",
                    ),
                    6,
                ),
                "lower_wick_to_range": _round(
                    _finite(
                        ratios.get("lower_wick_to_range"),
                        field="ratios.lower_wick_to_range",
                    ),
                    6,
                ),
                "close_location_in_range": _round(
                    _finite(
                        ratios.get("close_location_in_range"),
                        field="ratios.close_location_in_range",
                    ),
                    6,
                ),
            },
            "wick_penetration": {
                "above_previous_high_in_current_ranges": _round(upper_penetration),
                "below_previous_low_in_current_ranges": _round(lower_penetration),
                "upper_reclaim_depth_in_current_ranges": _round(upper_reclaim),
                "lower_reclaim_depth_in_current_ranges": _round(lower_reclaim),
            },
        }
        result.append(
            _node(
                history_id=str(context["history_id"]),
                level=0,
                kind="SINGLE_CANDLE_MICRO_EVENT",
                start=index,
                end=index,
                features=features,
                children=[],
            )
        )
        previous_geometry = (open_value, high, low, close)
    return result


def _level_one_nodes(
    context: Mapping[str, Any],
    lower_nodes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    candles = cast(list[dict[str, Any]], context["candles"])
    states = cast(list[dict[str, Any]], context["states"])
    by_index = {
        int(_mapping(node.get("span"))["start_index"]): node
        for node in lower_nodes
    }
    result: list[dict[str, Any]] = []
    for span in range(3, 6):
        for start in range(0, len(candles) - span + 1):
            end = start + span - 1
            children = [by_index[index] for index in range(start, end + 1) if index in by_index]
            if len(children) != span:
                continue
            features = _window_features(candles, states, start, end)
            features["child_motif_tokens"] = [str(child["motif_token"]) for child in children]
            result.append(
                _node(
                    history_id=str(context["history_id"]),
                    level=1,
                    kind="THREE_TO_FIVE_CANDLE_ATOM",
                    start=start,
                    end=end,
                    features=features,
                    children=children,
                )
            )
    return result


def _atom_partition(span: int) -> tuple[int, ...]:
    cache: dict[int, tuple[int, ...] | None] = {0: ()}

    def solve(remaining: int) -> tuple[int, ...] | None:
        if remaining in cache:
            return cache[remaining]
        for size in (5, 4, 3):
            if remaining < size:
                continue
            suffix = solve(remaining - size)
            if suffix is not None:
                cache[remaining] = (size, *suffix)
                return cache[remaining]
        cache[remaining] = None
        return None

    return solve(span) or ()


def _level_two_nodes(
    context: Mapping[str, Any],
    lower_nodes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    candles = cast(list[dict[str, Any]], context["candles"])
    states = cast(list[dict[str, Any]], context["states"])
    by_span = {
        (
            int(_mapping(node.get("span"))["start_index"]),
            int(_mapping(node.get("span"))["end_index"]),
        ): node
        for node in lower_nodes
    }
    result: list[dict[str, Any]] = []
    for span in range(7, 13):
        pieces = _atom_partition(span)
        if not pieces:
            continue
        for start in range(0, len(candles) - span + 1):
            cursor = start
            children: list[dict[str, Any]] = []
            for piece in pieces:
                child = by_span.get((cursor, cursor + piece - 1))
                if child is None:
                    children = []
                    break
                children.append(child)
                cursor += piece
            if not children:
                continue
            end = start + span - 1
            features = _window_features(candles, states, start, end)
            features["atom_span_sequence"] = list(pieces)
            features["child_motif_tokens"] = [str(child["motif_token"]) for child in children]
            result.append(
                _node(
                    history_id=str(context["history_id"]),
                    level=2,
                    kind="SEVEN_TO_TWELVE_CANDLE_COMPOUND",
                    start=start,
                    end=end,
                    features=features,
                    children=children,
                )
            )
    return result


def _state_segments(states: Sequence[Mapping[str, Any]]) -> list[tuple[int, int, str]]:
    result: list[tuple[int, int, str]] = []
    start = 0
    for index in range(1, len(states) + 1):
        boundary = index == len(states) or states[index].get("state") != states[start].get("state")
        if boundary:
            result.append((start, index - 1, str(states[start].get("state"))))
            start = index
    return result


def _level_three_nodes(
    context: Mapping[str, Any],
    lower_levels: Sequence[Sequence[dict[str, Any]]],
) -> list[dict[str, Any]]:
    candles = cast(list[dict[str, Any]], context["candles"])
    states = cast(list[dict[str, Any]], context["states"])
    result: list[dict[str, Any]] = []
    for start, end, state in _state_segments(states):
        eligible: list[dict[str, Any]] = []
        for level_nodes in reversed(lower_levels):
            eligible = [
                node
                for node in level_nodes
                if int(_mapping(node.get("span"))["start_index"]) >= start
                and int(_mapping(node.get("span"))["end_index"]) <= end
            ]
            if eligible:
                break
        children, omitted = _bounded_child_rows(eligible)
        features = _window_features(candles, states, start, end)
        features.update(
            {
                "regime_state": state,
                "duration_seconds": (end - start + 1) * int(context["timeframe_seconds"]),
                "child_motif_tokens": [str(child["motif_token"]) for child in children],
            }
        )
        result.append(
            _node(
                history_id=str(context["history_id"]),
                level=3,
                kind="FULL_SWING_OR_REST_REGIME",
                start=start,
                end=end,
                features=features,
                children=children,
                omitted_children=omitted,
            )
        )
    return result


def build_hierarchical_motif_lattice_v3(
    history: Mapping[str, Any],
    *,
    max_depth: int = MAX_LATTICE_DEPTH,
    max_nodes_per_level: int = MAX_NODES_PER_LEVEL,
) -> dict[str, Any]:
    """Compose deterministic motif occurrences through levels zero to three."""

    depth = _integer(max_depth, field="max_depth", minimum=1, maximum=MAX_LATTICE_DEPTH)
    node_limit = _integer(
        max_nodes_per_level,
        field="max_nodes_per_level",
        minimum=1,
        maximum=MAX_NODES_PER_LEVEL,
    )
    context = _validated_history(history)
    candidates_by_level: list[list[dict[str, Any]]] = []
    published_by_level: list[list[dict[str, Any]]] = []

    level_zero = _level_zero_nodes(context)
    candidates_by_level.append(level_zero)
    published_by_level.append(_bounded_rows(level_zero, node_limit))
    if depth >= 2:
        level_one = _level_one_nodes(context, published_by_level[0])
        candidates_by_level.append(level_one)
        published_by_level.append(_bounded_rows(level_one, node_limit))
    if depth >= 3:
        level_two = _level_two_nodes(context, published_by_level[1])
        candidates_by_level.append(level_two)
        published_by_level.append(_bounded_rows(level_two, node_limit))
    if depth >= 4:
        level_three = _level_three_nodes(context, published_by_level[:3])
        candidates_by_level.append(level_three)
        published_by_level.append(_bounded_rows(level_three, node_limit))

    level_kinds = (
        "SINGLE_CANDLE_MICRO_EVENT",
        "THREE_TO_FIVE_CANDLE_ATOM",
        "SEVEN_TO_TWELVE_CANDLE_COMPOUND",
        "FULL_SWING_OR_REST_REGIME",
    )
    levels: list[dict[str, Any]] = []
    for level, nodes in enumerate(published_by_level):
        candidates = candidates_by_level[level]
        levels.append(
            {
                "level": level,
                "kind": level_kinds[level],
                "candidate_count": len(candidates),
                "published_count": len(nodes),
                "truncated_count": len(candidates) - len(nodes),
                "nodes": nodes,
            }
        )
    return {
        "schema_version": MOTIF_LATTICE_SCHEMA_VERSION,
        "status": "STUDIED",
        "study_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "symbol": context["symbol"],
        "timeframe": context["timeframe"],
        "coordinate_space": context["coordinate_space"],
        "order_domain": context["order_domain"],
        "history_id": context["history_id"],
        "closed_candle_count": len(cast(list[object], context["candles"])),
        "depth": depth,
        "max_depth": MAX_LATTICE_DEPTH,
        "max_nodes_per_level": node_limit,
        "max_children_per_node": MAX_CHILDREN_PER_NODE,
        "levels": levels,
        "summary": {
            "published_node_count": sum(len(nodes) for nodes in published_by_level),
            "published_by_level": {
                str(index): len(nodes) for index, nodes in enumerate(published_by_level)
            },
            "truncated_by_level": {
                str(index): len(candidates_by_level[index]) - len(nodes)
                for index, nodes in enumerate(published_by_level)
            },
        },
        "interpretation_contract": {
            "analysis_kind": "HISTORICAL_HIERARCHICAL_MOTIF_OBSERVATION",
            "closed_candles_only": True,
            "contiguous_order_proof_required": True,
            "raw_prices_published": False,
            "raw_candle_identities_published": False,
            "predictive_probability": False,
            "entry_signal": False,
            "note": (
                "Motif nodes describe closed historical geometry. Similar-looking "
                "motifs do not prove causation or a future outcome."
            ),
        },
    }


def _event_duration(
    states: Sequence[str],
    *,
    origin: int,
    event_type: str,
    horizon: int,
) -> tuple[int, bool] | None:
    origin_state = states[origin]
    if event_type == "DIRECTION_CHANGE" and origin_state not in _SWING_STATES:
        return None
    if event_type == "REST_END" and origin_state != "REST":
        return None
    available = min(horizon, len(states) - origin - 1)
    if available <= 0:
        return None
    for distance in range(1, available + 1):
        index = origin + distance
        current = states[index]
        if event_type == "NEXT_SWING":
            previous = states[index - 1]
            occurred = current in _SWING_STATES and current != previous
        elif event_type == "DIRECTION_CHANGE":
            occurred = (
                origin_state == "UP_SWING" and current == "DOWN_SWING"
            ) or (
                origin_state == "DOWN_SWING" and current == "UP_SWING"
            )
        else:
            occurred = current != "REST"
        if occurred:
            return distance, True
    return available, False


def _survival_interval(
    survival: float,
    greenwood_sum: float,
) -> list[float]:
    if survival <= 0.0:
        return [0.0, 0.0]
    if survival >= 1.0 or greenwood_sum <= 0.0:
        return [_round(survival, 6), _round(survival, 6)]
    log_survival = math.log(survival)
    standard_error = math.sqrt(greenwood_sum) / abs(log_survival)
    center = math.log(-log_survival)
    lower = math.exp(-math.exp(center + 1.959963984540054 * standard_error))
    upper = math.exp(-math.exp(center - 1.959963984540054 * standard_error))
    return [_round(lower, 6), _round(upper, 6)]


def _kaplan_meier_curve(
    observations: Sequence[Mapping[str, Any]],
    *,
    event_type: str,
    origin_state: str,
    timeframe_seconds: int,
    minimum_support: int,
) -> dict[str, Any]:
    maximum_duration = max(int(row["duration"]) for row in observations)
    survival = 1.0
    greenwood_sum = 0.0
    restricted_mean = 0.0
    points: list[dict[str, Any]] = []
    for duration in range(1, maximum_duration + 1):
        # Discrete restricted mean event-free time integrates S(t-) across
        # each one-candle interval.  Adding S(t) after the event update would
        # incorrectly report zero time when every event occurs at candle one.
        restricted_mean += survival
        at_risk = sum(int(row["duration"]) >= duration for row in observations)
        events = sum(
            int(row["duration"]) == duration and row.get("event_observed") is True
            for row in observations
        )
        censored = sum(
            int(row["duration"]) == duration and row.get("event_observed") is False
            for row in observations
        )
        if events:
            survival *= 1.0 - events / at_risk
            if at_risk > events:
                greenwood_sum += events / (at_risk * (at_risk - events))
        points.append(
            {
                "closed_candles": duration,
                "elapsed_seconds": duration * timeframe_seconds,
                "at_risk": at_risk,
                "events": events,
                "censored": censored,
                "survival_probability": _round(survival, 6),
                "cumulative_event_probability": _round(1.0 - survival, 6),
                "survival_confidence_interval_95": _survival_interval(
                    survival,
                    greenwood_sum,
                ),
            }
        )
    median_duration = next(
        (int(point["closed_candles"]) for point in points if float(point["survival_probability"]) <= 0.5),
        None,
    )
    event_count = sum(row.get("event_observed") is True for row in observations)
    return {
        "event_type": event_type,
        "origin_state": origin_state,
        "status": "SUPPORTED" if len(observations) >= minimum_support else "INSUFFICIENT_SUPPORT",
        "support": len(observations),
        "minimum_support": minimum_support,
        "event_count": event_count,
        "right_censored_count": len(observations) - event_count,
        "median_event_time_closed_candles": median_duration,
        "median_event_time_seconds": (
            None if median_duration is None else median_duration * timeframe_seconds
        ),
        "restricted_mean_event_free_closed_candles": _round(
            restricted_mean,
            6,
        ),
        "curve": points,
    }


def build_time_to_event_survival_evidence_v3(
    histories: Sequence[Mapping[str, Any]],
    *,
    max_horizon: int = 128,
    min_support: int = 3,
    max_histories: int = MAX_SURVIVAL_HISTORIES,
    max_observations: int = MAX_SURVIVAL_OBSERVATIONS,
) -> dict[str, Any]:
    """Estimate bounded descriptive survival curves from closed histories.

    Each input item carries ``symbol``, ``timeframe``, ``order_domain``, a V3
    ``candle_study``, and its matching V3 ``behavior_study``.  All histories
    must share scope, coordinate space, timeframe duration, and order domain.
    """

    if isinstance(histories, (str, bytes, bytearray)):
        raise MotifLatticeValidationError("histories must be a sequence of mappings")
    history_limit = _integer(
        max_histories,
        field="max_histories",
        minimum=1,
        maximum=MAX_SURVIVAL_HISTORIES,
    )
    observation_limit = _integer(
        max_observations,
        field="max_observations",
        minimum=1,
        maximum=MAX_SURVIVAL_OBSERVATIONS,
    )
    horizon = _integer(
        max_horizon,
        field="max_horizon",
        minimum=1,
        maximum=MAX_SURVIVAL_HORIZON,
    )
    support_floor = _integer(min_support, field="min_support", minimum=2, maximum=1_024)
    raw_histories = list(histories)
    if not raw_histories:
        raise MotifLatticeValidationError("at least one closed history is required")
    if len(raw_histories) > history_limit:
        raise MotifLatticeValidationError("histories exceed the configured bounded capacity")
    canonical = [_validated_history(row) for row in raw_histories]
    scope_keys = {
        (
            str(row["symbol"]),
            str(row["timeframe"]),
            str(row["coordinate_space"]),
            str(row["order_domain"]),
            int(row["timeframe_seconds"]),
        )
        for row in canonical
    }
    if len(scope_keys) != 1:
        raise MotifLatticeValidationError(
            "survival histories cannot mix pair, timeframe, coordinate, or order domains"
        )
    unique: dict[str, dict[str, Any]] = {}
    for row in canonical:
        unique.setdefault(str(row["history_id"]), row)
    duplicate_count = len(canonical) - len(unique)

    observations: list[dict[str, Any]] = []
    for history_id in sorted(unique):
        row = unique[history_id]
        states = [str(state.get("state")) for state in cast(list[dict[str, Any]], row["states"])]
        for origin in range(len(states) - 1):
            for event_type in _EVENT_TYPES:
                measured = _event_duration(
                    states,
                    origin=origin,
                    event_type=event_type,
                    horizon=horizon,
                )
                if measured is None:
                    continue
                duration, event_observed = measured
                observations.append(
                    {
                        "event_type": event_type,
                        "origin_state": states[origin],
                        "duration": duration,
                        "event_observed": event_observed,
                    }
                )
                if len(observations) > observation_limit:
                    raise MotifLatticeValidationError(
                        "derived survival observations exceed the configured bounded capacity"
                    )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for observation in observations:
        key = (str(observation["event_type"]), str(observation["origin_state"]))
        grouped.setdefault(key, []).append(observation)
    timeframe_seconds = int(next(iter(unique.values()))["timeframe_seconds"])
    curves = [
        _kaplan_meier_curve(
            grouped[(event_type, origin_state)],
            event_type=event_type,
            origin_state=origin_state,
            timeframe_seconds=timeframe_seconds,
            minimum_support=support_floor,
        )
        for event_type in _EVENT_TYPES
        for origin_state in BEHAVIOR_STATES
        if (event_type, origin_state) in grouped
    ]
    reference = next(iter(unique.values()))
    return {
        "schema_version": SURVIVAL_EVIDENCE_SCHEMA_VERSION,
        "status": "STUDIED",
        "study_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "symbol": reference["symbol"],
        "timeframe": reference["timeframe"],
        "coordinate_space": reference["coordinate_space"],
        "order_domain": reference["order_domain"],
        "history_count": len(unique),
        "duplicate_history_count": duplicate_count,
        "derived_observation_count": len(observations),
        "max_horizon_closed_candles": horizon,
        "curves": curves,
        "event_definitions": {
            "NEXT_SWING": (
                "First later closed candle that begins an UP_SWING or DOWN_SWING state."
            ),
            "DIRECTION_CHANGE": (
                "First later closed candle in the swing state opposite the origin swing."
            ),
            "REST_END": "First later closed candle whose state is not REST.",
        },
        "interpretation_contract": {
            "analysis_kind": "KAPLAN_MEIER_STYLE_HISTORICAL_TIME_TO_EVENT",
            "association_is_causal": False,
            "right_censoring_explicit": True,
            "confidence_interval_method": "GREENWOOD_LOG_LOG",
            "overlapping_origins_may_be_dependent": True,
            "predictive_probability": False,
            "entry_signal": False,
            "note": (
                "Curves summarize historical closed-candle event timing. They do "
                "not establish influence, independence, or a future deadline."
            ),
        },
    }


def reconstruct_normalized_historical_path_v3(
    history: Mapping[str, Any],
    *,
    anchor_index: int,
    reference_direction: str,
    end_index: int | None = None,
    max_path_candles: int = 128,
    normalization_lookback: int = 64,
) -> dict[str, Any]:
    """Reconstruct one exact historical path in anchor-known median ranges."""

    context = _validated_history(history)
    candles = cast(list[dict[str, Any]], context["candles"])
    states = cast(list[dict[str, Any]], context["states"])
    anchor = _integer(
        anchor_index,
        field="anchor_index",
        minimum=0,
        maximum=len(candles) - 2,
    )
    limit = _integer(
        max_path_candles,
        field="max_path_candles",
        minimum=2,
        maximum=MAX_PATH_CANDLES,
    )
    lookback = _integer(
        normalization_lookback,
        field="normalization_lookback",
        minimum=1,
        maximum=MAX_CLOSED_HISTORY_CANDLES,
    )
    direction = str(reference_direction or "").strip().upper()
    if direction not in {"UP", "DOWN"}:
        raise MotifLatticeValidationError("reference_direction must be UP or DOWN")
    selected_end = min(len(candles) - 1, anchor + limit - 1)
    if end_index is not None:
        selected_end = _integer(
            end_index,
            field="end_index",
            minimum=anchor + 1,
            maximum=len(candles) - 1,
        )
        if selected_end - anchor + 1 > limit:
            raise MotifLatticeValidationError("requested path exceeds max_path_candles")

    normalization_start = max(0, anchor - lookback + 1)
    known_geometry = [
        _ohlc(candle, field="normalization_candle")
        for candle in candles[normalization_start : anchor + 1]
    ]
    scale = median(high - low for _open, high, low, _close in known_geometry)
    if scale <= 0.0:
        raise MotifLatticeValidationError("normalization median range must be positive")
    anchor_close = _ohlc(candles[anchor], field="anchor_candle")[3]

    points: list[dict[str, Any]] = []
    cumulative_distance = 0.0
    maximum_favorable = 0.0
    maximum_adverse = 0.0
    maximum_favorable_offset = 0
    maximum_adverse_offset = 0
    previous_close = anchor_close
    state_counts: Counter[str] = Counter()
    for source_index in range(anchor, selected_end + 1):
        offset = source_index - anchor
        open_value, high, low, close = _ohlc(candles[source_index], field="path_candle")
        state = str(states[source_index].get("state"))
        state_counts[state] += 1
        if offset:
            cumulative_distance += abs(close - previous_close) / scale
            if direction == "UP":
                favorable = max(0.0, high - anchor_close) / scale
                adverse = max(0.0, anchor_close - low) / scale
            else:
                favorable = max(0.0, anchor_close - low) / scale
                adverse = max(0.0, high - anchor_close) / scale
            if favorable > maximum_favorable:
                maximum_favorable = favorable
                maximum_favorable_offset = offset
            if adverse > maximum_adverse:
                maximum_adverse = adverse
                maximum_adverse_offset = offset
        net = (close - anchor_close) / scale
        efficiency = abs(net) / cumulative_distance if cumulative_distance > 1e-12 else 0.0
        points.append(
            {
                "offset_closed_candles": offset,
                "source_index": source_index,
                "state": state,
                "normalized_ohlc_from_anchor_close": {
                    "open": _round((open_value - anchor_close) / scale),
                    "high": _round((high - anchor_close) / scale),
                    "low": _round((low - anchor_close) / scale),
                    "close": _round(net),
                },
                "close_delta_from_previous_in_median_ranges": (
                    0.0 if not offset else _round((close - previous_close) / scale)
                ),
                "cumulative_close_path_distance_in_median_ranges": _round(
                    cumulative_distance
                ),
                "path_efficiency": _round(min(1.0, efficiency), 6),
                "cumulative_favorable_excursion_in_median_ranges": _round(
                    maximum_favorable
                ),
                "cumulative_adverse_excursion_in_median_ranges": _round(maximum_adverse),
            }
        )
        previous_close = close

    transition_count = sum(
        current.get("state") != previous.get("state")
        for previous, current in zip(points, points[1:], strict=False)
    )
    final_close = float(
        _mapping(points[-1].get("normalized_ohlc_from_anchor_close"))["close"]
    )
    point_count = len(points)
    path_id = _digest(
        {
            "history_id": context["history_id"],
            "anchor_index": anchor,
            "end_index": selected_end,
            "reference_direction": direction,
            "normalization_start": normalization_start,
            "scale": format(scale, ".17g"),
        }
    )
    return {
        "schema_version": HISTORICAL_PATH_SCHEMA_VERSION,
        "status": "RECONSTRUCTED",
        "study_only": True,
        "causal": False,
        "historical_only": True,
        "execution_authority": False,
        "grants_entry_permission": False,
        "symbol": context["symbol"],
        "timeframe": context["timeframe"],
        "coordinate_space": context["coordinate_space"],
        "order_domain": context["order_domain"],
        "history_id": context["history_id"],
        "path_id": path_id,
        "anchor_index": anchor,
        "end_index": selected_end,
        "reference_direction": direction,
        "reference_direction_is_trade_instruction": False,
        "point_count": point_count,
        "normalization": {
            "unit": "MEDIAN_CANDLE_RANGE",
            "lookback_start_index": normalization_start,
            "lookback_end_index": anchor,
            "lookback_candle_count": len(known_geometry),
            "uses_only_candles_known_at_anchor": True,
            "future_path_influences_scale": False,
            "raw_scale_published": False,
        },
        "excursion_window": {
            "anchor_candle_is_reference_only": True,
            "excursions_begin_after_anchor": True,
        },
        "points": points,
        "path_summary": {
            "maximum_favorable_excursion_in_median_ranges": _round(maximum_favorable),
            "maximum_adverse_excursion_in_median_ranges": _round(maximum_adverse),
            "maximum_favorable_excursion_offset": maximum_favorable_offset,
            "maximum_adverse_excursion_offset": maximum_adverse_offset,
            "final_displacement_in_median_ranges": _round(final_close),
            "final_path_efficiency": points[-1]["path_efficiency"],
            "state_transition_count": transition_count,
            "time_in_states": {
                state: {
                    "closed_candles": state_counts[state],
                    "seconds": state_counts[state] * int(context["timeframe_seconds"]),
                    "fraction": _round(state_counts[state] / point_count, 6),
                }
                for state in BEHAVIOR_STATES
            },
        },
        "proof_certificate": {
            "derivation_digest": _digest(
                {
                    "path_id": path_id,
                    "point_digest": _digest(points, length=64),
                    "coordinate_space": context["coordinate_space"],
                    "order_domain": context["order_domain"],
                },
                length=64,
            ),
            "closed_candles_only": True,
            "contiguous_order_proven": True,
            "normalization_is_anchor_known": True,
            "raw_market_geometry_disclosed": False,
        },
        "interpretation_contract": {
            "analysis_kind": "EXACT_NORMALIZED_HISTORICAL_PATH_RECONSTRUCTION",
            "non_causal_label": True,
            "predictive_probability": False,
            "entry_signal": False,
            "note": (
                "Excursions are measured relative to a study reference direction. "
                "They are historical labels, not expected returns or trade advice."
            ),
        },
    }


__all__ = [
    "HISTORICAL_PATH_SCHEMA_VERSION",
    "MAX_CLOSED_HISTORY_CANDLES",
    "MAX_LATTICE_DEPTH",
    "MAX_NODES_PER_LEVEL",
    "MAX_PATH_CANDLES",
    "MAX_SURVIVAL_HISTORIES",
    "MAX_SURVIVAL_HORIZON",
    "MAX_SURVIVAL_OBSERVATIONS",
    "MOTIF_LATTICE_SCHEMA_VERSION",
    "SURVIVAL_EVIDENCE_SCHEMA_VERSION",
    "MotifLatticeValidationError",
    "build_hierarchical_motif_lattice_v3",
    "build_time_to_event_survival_evidence_v3",
    "reconstruct_normalized_historical_path_v3",
]
