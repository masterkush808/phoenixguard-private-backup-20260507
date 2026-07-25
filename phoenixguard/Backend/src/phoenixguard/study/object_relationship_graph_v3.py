"""Bounded, observation-only candle/object relationship graph for V3.

This module records only relationships that the current study input proves:

* objects supplied in one call were observed with the latest studied candle;
* an object is anchored to a candle only when the object carries that explicit
  candle identity and the identity exists in ``studied_candles``; and
* rectangular overlap is emitted only for two valid normalized bounds; and
* retracement confluence is studied only from fully closed, identity-stable
  candles whose two-sided pivots were confirmed by a later closed candle.

The graph deliberately carries no order, entry, trade, or execution authority.
Unknown and pixel-space geometry is never guessed into normalized geometry.
Price, normalized-price-proxy, and pixel-price-proxy value axes remain
separate and are compared only when their explicit coordinate spaces match.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from typing import Any, cast


OBJECT_RELATIONSHIP_GRAPH_SCHEMA_VERSION = "PG_OBJECT_RELATIONSHIP_GRAPH_V3"
DEFAULT_MAX_OBJECT_NODES = 64
DEFAULT_MAX_CANDLE_NODES = 16
DEFAULT_MAX_GRAPH_EDGES = 512
DEFAULT_MAX_POINTS_PER_OBJECT = 32
DEFAULT_MAX_COMPLETED_SWINGS = 16
DEFAULT_MAX_RETRACEMENT_OBSERVATIONS = 128
DEFAULT_RETRACEMENT_TOLERANCE_RATIO = 0.015
MAX_OBJECT_INPUT_ROWS = 4_096
MAX_CANDLE_INPUT_ROWS = 4_096
MAX_OBJECT_NODES = 256
MAX_CANDLE_NODES = 64
MAX_GRAPH_EDGES = 4_096
MAX_POINTS_PER_OBJECT = 128
MAX_COMPLETED_SWINGS = 64
MAX_RETRACEMENT_OBSERVATIONS = 1_024

RETRACEMENT_CONFLUENCE_SCHEMA_VERSION = "PG_RETRACEMENT_CONFLUENCE_STUDY_V3"

_NORMALIZED_COORDINATE_SPACES = {
    "NORMALIZED",
    "NORMALIZED_FRAME",
    "NORMALIZED_IMAGE",
    "UNIT_INTERVAL",
}
_VALUE_COORDINATE_SPACES = {
    "PRICE",
    "NORMALIZED_PRICE_PROXY",
    "PIXEL_PRICE_PROXY",
}
_RETRACEMENT_OBJECT_FAMILIES = {
    "ORDER_BLOCK": "ORDER_BLOCK",
    "BULLISH_ORDER_BLOCK": "ORDER_BLOCK",
    "BEARISH_ORDER_BLOCK": "ORDER_BLOCK",
    "FVG": "FVG_IMBALANCE",
    "FAIR_VALUE_GAP": "FVG_IMBALANCE",
    "BULLISH_FVG": "FVG_IMBALANCE",
    "BEARISH_FVG": "FVG_IMBALANCE",
    "IMBALANCE": "FVG_IMBALANCE",
    "PRICE_IMBALANCE": "FVG_IMBALANCE",
    "CROWDED_AREA": "CROWDED_CONSOLIDATION",
    "CROWDED_PRICE_AREA": "CROWDED_CONSOLIDATION",
    "CROWDED_ZONE": "CROWDED_CONSOLIDATION",
    "CONSOLIDATION": "CROWDED_CONSOLIDATION",
    "CONSOLIDATION_AREA": "CROWDED_CONSOLIDATION",
    "CONSOLIDATION_ZONE": "CROWDED_CONSOLIDATION",
}
_UNSTABLE_OBJECT_IDENTITY_SCOPES = {
    "EPHEMERAL",
    "FRAME_LOCAL",
    "FRAME_POSITIONAL",
    "GENERIC",
    "OBSERVATION_ONLY",
    "POSITIONAL",
    "SYNTHETIC",
    "UNKNOWN",
    "UNRESOLVED",
}
_RETRACEMENT_LEVEL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "level_id": "FIB_61_8",
        "level_ratio": 0.618,
        "classification": "STANDARD_FIBONACCI_REFERENCE_BOUNDARY",
        "label": "61.8% Fibonacci reference boundary",
        "standard_fibonacci": True,
        "user_defined": False,
        "experimental": False,
        "evaluated_for_object_confluence": False,
    },
    {
        "level_id": "OTE_70_5",
        "level_ratio": 0.705,
        "classification": "ICT_STYLE_OTE_REFERENCE",
        "label": "70.5% ICT-style OTE reference",
        "standard_fibonacci": False,
        "user_defined": False,
        "experimental": False,
        "evaluated_for_object_confluence": True,
    },
    {
        "level_id": "CUSTOM_71_8",
        "level_ratio": 0.718,
        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
        "label": "71.8% user-defined experimental retracement",
        "standard_fibonacci": False,
        "user_defined": True,
        "experimental": True,
        "evaluated_for_object_confluence": True,
    },
    {
        "level_id": "FIB_78_6",
        "level_ratio": 0.786,
        "classification": "STANDARD_FIBONACCI_REFERENCE_BOUNDARY",
        "label": "78.6% Fibonacci reference boundary",
        "standard_fibonacci": True,
        "user_defined": False,
        "experimental": False,
        "evaluated_for_object_confluence": False,
    },
)
_EVALUATED_RETRACEMENT_LEVELS = tuple(
    row for row in _RETRACEMENT_LEVEL_CATALOG if row["evaluated_for_object_confluence"]
)
_TOKEN_SEPARATOR = re.compile(r"[^A-Z0-9]+")


class ObjectRelationshipGraphValidationError(ValueError):
    """Raised when graph evidence is unsafe, malformed, or ambiguous."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object, *, field: str, maximum: int) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ObjectRelationshipGraphValidationError(f"{field} must be a sequence of mappings")
    sequence = cast(Sequence[object], value)
    if len(sequence) > maximum:
        raise ObjectRelationshipGraphValidationError(
            f"{field} cannot exceed {maximum} input rows"
        )
    rows: list[dict[str, Any]] = []
    for raw in sequence:
        row = _mapping(raw)
        if not row:
            raise ObjectRelationshipGraphValidationError(
                f"every {field} row must be a non-empty mapping"
            )
        rows.append(row)
    return rows


def _bounded_text(
    value: object,
    *,
    field: str,
    maximum: int,
    required: bool = False,
) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ObjectRelationshipGraphValidationError(f"{field} is required")
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise ObjectRelationshipGraphValidationError(f"{field} is not a bounded public value")
    return text


def _token(
    value: object,
    *,
    field: str,
    maximum: int = 96,
    required: bool = False,
    default: str = "UNKNOWN",
) -> str:
    text = _bounded_text(value, field=field, maximum=maximum, required=required)
    if not text:
        return default
    canonical = _TOKEN_SEPARATOR.sub("_", text.upper()).strip("_")
    if not canonical:
        if required:
            raise ObjectRelationshipGraphValidationError(f"{field} is required")
        return default
    return canonical[:maximum]


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ObjectRelationshipGraphValidationError(f"{field} must be a finite number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ObjectRelationshipGraphValidationError(
            f"{field} must be a finite number"
        ) from exc
    if not math.isfinite(parsed):
        raise ObjectRelationshipGraphValidationError(f"{field} must be a finite number")
    return parsed


def _normalized(value: object, *, field: str) -> float:
    parsed = _finite(value, field=field)
    if not 0.0 <= parsed <= 1.0:
        raise ObjectRelationshipGraphValidationError(f"{field} must be in [0, 1]")
    return round(parsed, 8)


def _non_negative(value: object, *, field: str) -> int | float:
    parsed = _finite(value, field=field)
    if parsed < 0.0:
        raise ObjectRelationshipGraphValidationError(f"{field} must be non-negative")
    return int(parsed) if parsed.is_integer() else round(parsed, 8)


def _public_scalar(value: object, *, field: str) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ObjectRelationshipGraphValidationError(f"{field} cannot be boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ObjectRelationshipGraphValidationError(f"{field} must be finite")
        return value
    return _bounded_text(value, field=field, maximum=128)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object, *, length: int = 20) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[:length]


def _parse_bounds(value: object, *, field: str) -> dict[str, float]:
    if isinstance(value, Mapping):
        row = dict(cast(Mapping[str, Any], value))
        if all(name in row for name in ("left", "top", "right", "bottom")):
            left = _normalized(row.get("left"), field=f"{field}.left")
            top = _normalized(row.get("top"), field=f"{field}.top")
            right = _normalized(row.get("right"), field=f"{field}.right")
            bottom = _normalized(row.get("bottom"), field=f"{field}.bottom")
        elif all(name in row for name in ("x_min", "y_min", "x_max", "y_max")):
            left = _normalized(row.get("x_min"), field=f"{field}.x_min")
            top = _normalized(row.get("y_min"), field=f"{field}.y_min")
            right = _normalized(row.get("x_max"), field=f"{field}.x_max")
            bottom = _normalized(row.get("y_max"), field=f"{field}.y_max")
        elif all(name in row for name in ("x1", "y1", "x2", "y2")):
            left = _normalized(row.get("x1"), field=f"{field}.x1")
            top = _normalized(row.get("y1"), field=f"{field}.y1")
            right = _normalized(row.get("x2"), field=f"{field}.x2")
            bottom = _normalized(row.get("y2"), field=f"{field}.y2")
        elif all(name in row for name in ("x", "y", "width", "height")):
            left = _normalized(row.get("x"), field=f"{field}.x")
            top = _normalized(row.get("y"), field=f"{field}.y")
            width = _finite(row.get("width"), field=f"{field}.width")
            height = _finite(row.get("height"), field=f"{field}.height")
            if width <= 0.0 or height <= 0.0:
                raise ObjectRelationshipGraphValidationError(
                    f"{field} width and height must be positive"
                )
            right = _normalized(left + width, field=f"{field}.right")
            bottom = _normalized(top + height, field=f"{field}.bottom")
        else:
            raise ObjectRelationshipGraphValidationError(
                f"{field} must define left/top/right/bottom normalized bounds"
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast(Sequence[object], value)
        if len(sequence) != 4:
            raise ObjectRelationshipGraphValidationError(
                f"{field} must contain exactly four normalized coordinates"
            )
        left = _normalized(sequence[0], field=f"{field}[0]")
        top = _normalized(sequence[1], field=f"{field}[1]")
        right = _normalized(sequence[2], field=f"{field}[2]")
        bottom = _normalized(sequence[3], field=f"{field}[3]")
    else:
        raise ObjectRelationshipGraphValidationError(f"{field} must be normalized bounds")
    if right <= left or bottom <= top:
        raise ObjectRelationshipGraphValidationError(
            f"{field} must have positive width and height"
        )
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _parse_points(
    value: object,
    *,
    field: str,
    limit: int,
) -> tuple[list[dict[str, float]], int]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ObjectRelationshipGraphValidationError(f"{field} must be normalized points")
    sequence = cast(Sequence[object], value)
    if len(sequence) > 2_048:
        raise ObjectRelationshipGraphValidationError(f"{field} cannot exceed 2048 input points")
    points: list[dict[str, float]] = []
    for index, raw in enumerate(sequence):
        if isinstance(raw, Mapping):
            row = dict(cast(Mapping[str, Any], raw))
            if "x" not in row or "y" not in row:
                raise ObjectRelationshipGraphValidationError(
                    f"{field}[{index}] must define x and y"
                )
            x_value, y_value = row.get("x"), row.get("y")
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            point = cast(Sequence[object], raw)
            if len(point) != 2:
                raise ObjectRelationshipGraphValidationError(
                    f"{field}[{index}] must contain exactly x and y"
                )
            x_value, y_value = point[0], point[1]
        else:
            raise ObjectRelationshipGraphValidationError(
                f"{field}[{index}] must be a normalized point"
            )
        points.append(
            {
                "x": _normalized(x_value, field=f"{field}[{index}].x"),
                "y": _normalized(y_value, field=f"{field}[{index}].y"),
            }
        )
    return points[:limit], max(0, len(points) - limit)


def _geometry(
    row: Mapping[str, Any],
    *,
    max_points: int,
) -> dict[str, Any] | None:
    nested_raw = row.get("geometry")
    if nested_raw is not None and not isinstance(nested_raw, Mapping):
        raise ObjectRelationshipGraphValidationError("object.geometry must be a mapping")
    nested = (
        dict(cast(Mapping[str, Any], nested_raw))
        if isinstance(nested_raw, Mapping)
        else {}
    )
    coordinate_space = _token(
        nested.get("coordinate_space", row.get("coordinate_space")),
        field="object.geometry.coordinate_space",
        maximum=32,
        default="",
    )
    normalized_space = coordinate_space in _NORMALIZED_COORDINATE_SPACES

    bounds_candidates: list[tuple[str, object]] = []
    for field, source in (
        ("object.normalized_bounds", row),
        ("object.bounds_normalized", row),
        ("object.geometry.normalized_bounds", nested),
        ("object.geometry.bounds_normalized", nested),
    ):
        key = field.rsplit(".", 1)[-1]
        if key in source and source.get(key) is not None:
            bounds_candidates.append((field, source.get(key)))
    for field, source in (("object.bounds", row), ("object.geometry.bounds", nested)):
        key = field.rsplit(".", 1)[-1]
        if key not in source or source.get(key) is None:
            continue
        if not normalized_space:
            raise ObjectRelationshipGraphValidationError(
                f"{field} requires an explicit normalized coordinate space"
            )
        bounds_candidates.append((field, source.get(key)))

    parsed_bounds = [_parse_bounds(value, field=field) for field, value in bounds_candidates]
    if any(candidate != parsed_bounds[0] for candidate in parsed_bounds[1:]):
        raise ObjectRelationshipGraphValidationError("object normalized bounds disagree")
    bounds = parsed_bounds[0] if parsed_bounds else None

    point_candidates: list[tuple[str, object]] = []
    for field, source in (
        ("object.normalized_points", row),
        ("object.points_normalized", row),
        ("object.geometry.normalized_points", nested),
        ("object.geometry.points_normalized", nested),
    ):
        key = field.rsplit(".", 1)[-1]
        if key in source and source.get(key) is not None:
            point_candidates.append((field, source.get(key)))
    for field, source in (("object.points", row), ("object.geometry.points", nested)):
        key = field.rsplit(".", 1)[-1]
        if key not in source or source.get(key) is None:
            continue
        if not normalized_space:
            raise ObjectRelationshipGraphValidationError(
                f"{field} requires an explicit normalized coordinate space"
            )
        point_candidates.append((field, source.get(key)))

    parsed_points = [
        _parse_points(value, field=field, limit=max_points)
        for field, value in point_candidates
    ]
    if any(candidate != parsed_points[0] for candidate in parsed_points[1:]):
        raise ObjectRelationshipGraphValidationError("object normalized points disagree")
    points, points_truncated = parsed_points[0] if parsed_points else ([], 0)
    if bounds is None and not points:
        return None
    return {
        "coordinate_space": "NORMALIZED_FRAME",
        "bounds": bounds,
        "points": points,
        "points_truncated_count": points_truncated,
    }


def _parse_value_bounds(value: object, *, field: str) -> dict[str, float]:
    if isinstance(value, Mapping):
        row = dict(cast(Mapping[str, Any], value))
        if all(name in row for name in ("low", "high")):
            low_raw, high_raw = row.get("low"), row.get("high")
        elif all(name in row for name in ("minimum", "maximum")):
            low_raw, high_raw = row.get("minimum"), row.get("maximum")
        elif all(name in row for name in ("min", "max")):
            low_raw, high_raw = row.get("min"), row.get("max")
        else:
            raise ObjectRelationshipGraphValidationError(
                f"{field} must define low/high value-axis bounds"
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast(Sequence[object], value)
        if len(sequence) != 2:
            raise ObjectRelationshipGraphValidationError(
                f"{field} must contain exactly two value-axis bounds"
            )
        low_raw, high_raw = sequence[0], sequence[1]
    else:
        raise ObjectRelationshipGraphValidationError(
            f"{field} must contain explicit value-axis bounds"
        )
    low = _finite(low_raw, field=f"{field}.low")
    high = _finite(high_raw, field=f"{field}.high")
    if high < low:
        raise ObjectRelationshipGraphValidationError(
            f"{field} must be ordered from low to high"
        )
    return {"low": round(low, 8), "high": round(high, 8)}


def _value_geometry(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return an explicit one-dimensional market-value axis.

    Normalized frame bounds describe where an object was drawn.  They are not
    market values and are intentionally ignored here.  The caller must provide
    both ``value_bounds`` and ``value_coordinate_space``.
    """

    has_bounds = row.get("value_bounds") is not None
    has_space = row.get("value_coordinate_space") is not None
    if not has_bounds and not has_space:
        return None
    if not has_bounds or not has_space:
        raise ObjectRelationshipGraphValidationError(
            "object retracement geometry requires both value_bounds and "
            "value_coordinate_space"
        )
    coordinate_space = _token(
        row.get("value_coordinate_space"),
        field="object.value_coordinate_space",
        maximum=32,
        required=True,
    )
    if coordinate_space not in _VALUE_COORDINATE_SPACES:
        raise ObjectRelationshipGraphValidationError(
            "object.value_coordinate_space is not a supported market-value axis"
        )
    source = _token(
        row.get("value_axis_source"),
        field="object.value_axis_source",
        maximum=32,
        default="EXPLICIT",
    )
    return {
        "coordinate_space": coordinate_space,
        "bounds": _parse_value_bounds(row.get("value_bounds"), field="object.value_bounds"),
        "source": source,
    }


def _candle_value_geometry(row: Mapping[str, Any]) -> dict[str, float] | None:
    raw = row.get("ohlc")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ObjectRelationshipGraphValidationError("candle.ohlc must be a mapping")
    ohlc = dict(cast(Mapping[str, Any], raw))
    if not all(name in ohlc for name in ("open", "high", "low", "close")):
        raise ObjectRelationshipGraphValidationError(
            "candle.ohlc must define open/high/low/close"
        )
    open_value = _finite(ohlc.get("open"), field="candle.ohlc.open")
    high_value = _finite(ohlc.get("high"), field="candle.ohlc.high")
    low_value = _finite(ohlc.get("low"), field="candle.ohlc.low")
    close_value = _finite(ohlc.get("close"), field="candle.ohlc.close")
    scale = max(1.0, abs(open_value), abs(high_value), abs(low_value), abs(close_value))
    tolerance = scale * 1e-10
    if high_value + tolerance < low_value:
        raise ObjectRelationshipGraphValidationError("candle.ohlc high cannot be below low")
    if high_value + tolerance < max(open_value, close_value):
        raise ObjectRelationshipGraphValidationError(
            "candle.ohlc high cannot be below its body"
        )
    if low_value - tolerance > min(open_value, close_value):
        raise ObjectRelationshipGraphValidationError(
            "candle.ohlc low cannot be above its body"
        )
    if high_value - low_value <= tolerance:
        raise ObjectRelationshipGraphValidationError(
            "candle.ohlc must have a positive range"
        )
    return {
        "open": round(open_value, 8),
        "high": round(high_value, 8),
        "low": round(low_value, 8),
        "close": round(close_value, 8),
    }


def _first_value(sources: Sequence[Mapping[str, Any]], names: Sequence[str]) -> object:
    for source in sources:
        for name in names:
            if name in source and source.get(name) is not None:
                return source.get(name)
    return None


def _lifecycle(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("lifecycle")
    if raw is not None and not isinstance(raw, (Mapping, str)):
        raise ObjectRelationshipGraphValidationError(
            "object.lifecycle must be a token or mapping"
        )
    nested = dict(cast(Mapping[str, Any], raw)) if isinstance(raw, Mapping) else {}
    sources = [row, nested]
    state_raw = raw if isinstance(raw, str) else _first_value(
        sources, ("lifecycle_state", "state", "status")
    )
    state = _token(
        state_raw,
        field="object.lifecycle.state",
        maximum=64,
        default="UNKNOWN",
    )
    first_seen = _public_scalar(
        _first_value(sources, ("first_seen", "first_observed_at")),
        field="object.lifecycle.first_seen",
    )
    last_seen = _public_scalar(
        _first_value(sources, ("last_seen", "last_observed_at")),
        field="object.lifecycle.last_seen",
    )
    duration_raw = _first_value(sources, ("duration", "duration_candles", "duration_seconds"))
    age_raw = _first_value(sources, ("age", "age_candles", "age_seconds"))
    duration = (
        None
        if duration_raw is None
        else _non_negative(duration_raw, field="object.lifecycle.duration")
    )
    age = None if age_raw is None else _non_negative(age_raw, field="object.lifecycle.age")
    duration_unit = (
        "CANDLES"
        if _first_value(sources, ("duration_candles",)) is not None
        else "SECONDS"
        if _first_value(sources, ("duration_seconds",)) is not None
        else _token(
            _first_value(sources, ("duration_unit",)),
            field="object.lifecycle.duration_unit",
            maximum=24,
            default="UNSPECIFIED",
        )
    )
    age_unit = (
        "CANDLES"
        if _first_value(sources, ("age_candles",)) is not None
        else "SECONDS"
        if _first_value(sources, ("age_seconds",)) is not None
        else _token(
            _first_value(sources, ("age_unit",)),
            field="object.lifecycle.age_unit",
            maximum=24,
            default="UNSPECIFIED",
        )
    )
    return {
        "state": state,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "duration": duration,
        "duration_unit": duration_unit,
        "age": age,
        "age_unit": age_unit,
    }


def _association_values(row: Mapping[str, Any]) -> list[str]:
    values: list[object] = []
    for name in (
        "candle_id",
        "candle_identity",
        "anchor_candle_id",
        "anchor_candle_identity",
    ):
        if name in row and row.get(name) is not None:
            values.append(row.get(name))
    for name in ("candle_ids", "associated_candle_ids", "candle_associations"):
        raw = row.get(name)
        if raw is None:
            continue
        if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
            raise ObjectRelationshipGraphValidationError(f"object.{name} must be a sequence")
        sequence = cast(Sequence[object], raw)
        if len(sequence) > 64:
            raise ObjectRelationshipGraphValidationError(
                f"object.{name} cannot exceed 64 associations"
            )
        for item in sequence:
            if isinstance(item, Mapping):
                mapped = dict(cast(Mapping[str, Any], item))
                identity = _first_value(
                    [mapped], ("candle_id", "candle_identity", "id")
                )
                if identity is None:
                    raise ObjectRelationshipGraphValidationError(
                        f"object.{name} association must identify a candle"
                    )
                values.append(identity)
            else:
                values.append(item)
    result = {
        _bounded_text(value, field="object.candle_association", maximum=256, required=True)
        for value in values
    }
    return sorted(result)


def _canonical_object(row: Mapping[str, Any], *, max_points: int) -> dict[str, Any]:
    object_type = _token(
        _first_value([row], ("object_type", "kind", "role", "label", "type")),
        field="object.object_type",
        required=True,
    )
    object_id = _bounded_text(
        _first_value([row], ("object_id", "track_id", "zone_id", "id")),
        field="object.object_id",
        maximum=256,
    )
    confidence_raw = row.get("confidence", 0.0)
    confidence = _finite(confidence_raw, field="object.confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ObjectRelationshipGraphValidationError("object.confidence must be in [0, 1]")
    identity_scope_claim = _token(
        row.get("identity_scope"),
        field="object.identity_scope",
        maximum=32,
        default="",
    )
    identity_scope = identity_scope_claim or (
        "EXPLICIT" if object_id else "UNRESOLVED"
    )
    identity_stable = (
        bool(object_id)
        and row.get("identity_stable") is True
        and identity_scope not in _UNSTABLE_OBJECT_IDENTITY_SCOPES
    )
    canonical: dict[str, Any] = {
        "object_type": object_type,
        "object_id": object_id,
        "identity_scope": identity_scope,
        "identity_stable": identity_stable,
        "direction": _token(
            row.get("direction", row.get("side")),
            field="object.direction",
            maximum=32,
        ),
        "confidence": round(confidence, 6),
        "lifecycle": _lifecycle(row),
        "geometry": _geometry(row, max_points=max_points),
        "value_geometry": _value_geometry(row),
        "explicit_candle_associations": _association_values(row),
    }
    canonical["evidence_digest"] = _digest(canonical)
    canonical["node_id"] = f"object:{object_type.lower()}:{_digest(canonical, length=16)}"
    return canonical


def _sequence_index(row: Mapping[str, Any], fallback: int) -> int:
    position = _mapping(row.get("sequence_position"))
    raw = position.get("index", fallback)
    if isinstance(raw, bool):
        raise ObjectRelationshipGraphValidationError(
            "candle.sequence_position.index must be a non-negative integer"
        )
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise ObjectRelationshipGraphValidationError(
            "candle.sequence_position.index must be a non-negative integer"
        ) from exc
    if parsed < 0 or float(raw) != parsed:
        raise ObjectRelationshipGraphValidationError(
            "candle.sequence_position.index must be a non-negative integer"
        )
    return parsed


def _canonical_candle(row: Mapping[str, Any], *, source_index: int) -> dict[str, Any]:
    identity = _bounded_text(
        _first_value(
            [row],
            (
                "stable_candle_identity",
                "candle_identity",
                "closed_candle_identity",
                "candle_id",
                "id",
            ),
        ),
        field="candle.candle_id",
        maximum=256,
    )
    if not identity:
        identity = f"sequence-index:{source_index}"
    position = _mapping(row.get("sequence_position"))
    is_latest = position.get("is_latest") is True
    sequence_index = _sequence_index(row, source_index)
    canonical = {
        "candle_id": identity,
        "identity_stable": not identity.startswith("sequence-index:"),
        "retracement_identity_proven": (
            row.get("identity_stable") is True
            and not identity.startswith("sequence-index:")
        ),
        "closed_proven": row.get("closed") is True,
        "timestamp": _public_scalar(row.get("timestamp"), field="candle.timestamp"),
        "sequence_index": sequence_index,
        "explicit_latest": is_latest,
        "coordinate_space": _token(
            row.get("coordinate_space"),
            field="candle.coordinate_space",
            maximum=32,
        ),
        "direction": _token(
            row.get("direction"), field="candle.direction", maximum=32
        ),
        "type": _token(row.get("type"), field="candle.type"),
        "personality": _token(row.get("personality"), field="candle.personality"),
        "regime": _token(row.get("regime"), field="candle.regime"),
        "ohlc": _candle_value_geometry(row),
    }
    canonical["node_id"] = f"candle:{_digest({'candle_id': identity}, length=20)}"
    return canonical


def _overlap(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> dict[str, float] | None:
    first_geometry = _mapping(first.get("geometry"))
    second_geometry = _mapping(second.get("geometry"))
    first_bounds = _mapping(first_geometry.get("bounds"))
    second_bounds = _mapping(second_geometry.get("bounds"))
    if not first_bounds or not second_bounds:
        return None
    left = max(float(first_bounds["left"]), float(second_bounds["left"]))
    top = max(float(first_bounds["top"]), float(second_bounds["top"]))
    right = min(float(first_bounds["right"]), float(second_bounds["right"]))
    bottom = min(float(first_bounds["bottom"]), float(second_bounds["bottom"]))
    width = right - left
    height = bottom - top
    if width <= 0.0 or height <= 0.0:
        return None
    intersection = width * height
    first_area = (float(first_bounds["right"]) - float(first_bounds["left"])) * (
        float(first_bounds["bottom"]) - float(first_bounds["top"])
    )
    second_area = (float(second_bounds["right"]) - float(second_bounds["left"])) * (
        float(second_bounds["bottom"]) - float(second_bounds["top"])
    )
    union = first_area + second_area - intersection
    return {
        "intersection_area": round(intersection, 8),
        "intersection_over_union": round(intersection / union, 8),
    }


def _proven_candle_series(
    candles: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    ordered = sorted(
        (dict(row) for row in candles),
        key=lambda row: (int(row["sequence_index"]), str(row["candle_id"])),
    )
    audit: dict[str, Any] = {
        "input_candles": len(ordered),
        "selected_proven_candles": 0,
        "excluded_candles": len(ordered),
        "excluded_without_value_geometry": sum(
            row.get("ohlc") is None for row in ordered
        ),
        "excluded_without_closed_proof": sum(
            row.get("ohlc") is not None and row.get("closed_proven") is not True
            for row in ordered
        ),
        "excluded_without_identity_proof": sum(
            row.get("ohlc") is not None
            and row.get("retracement_identity_proven") is not True
            for row in ordered
        ),
        "excluded_unsupported_coordinate_space": sum(
            row.get("ohlc") is not None
            and str(row.get("coordinate_space") or "") not in _VALUE_COORDINATE_SPACES
            for row in ordered
        ),
        "excluded_outside_newest_contiguous_run": 0,
        "selected_coordinate_space": None,
        "selected_start_sequence_index": None,
        "selected_end_sequence_index": None,
    }
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in ordered:
        coordinate_space = str(row.get("coordinate_space") or "")
        eligible = (
            row.get("ohlc") is not None
            and row.get("closed_proven") is True
            and row.get("retracement_identity_proven") is True
            and coordinate_space in _VALUE_COORDINATE_SPACES
        )
        if not eligible:
            if current:
                runs.append(current)
                current = []
            continue
        if current:
            previous = current[-1]
            consecutive = int(row["sequence_index"]) == int(previous["sequence_index"]) + 1
            same_space = coordinate_space == str(previous["coordinate_space"])
            if not consecutive or not same_space:
                runs.append(current)
                current = []
        current.append(row)
    if current:
        runs.append(current)
    if not runs:
        if audit["excluded_without_value_geometry"] == len(ordered):
            status = "CANDLE_VALUE_GEOMETRY_NOT_SUPPLIED"
        elif audit["excluded_without_closed_proof"]:
            status = "NO_CONTIGUOUS_CANDLES_WITH_CLOSED_PROOF"
        elif audit["excluded_without_identity_proof"]:
            status = "NO_CONTIGUOUS_CANDLES_WITH_STABLE_IDENTITY_PROOF"
        else:
            status = "NO_SUPPORTED_CONTIGUOUS_VALUE_AXIS"
        return [], status, audit
    selected = max(
        runs,
        key=lambda run: (
            int(run[-1]["sequence_index"]),
            len(run),
            str(run[-1]["candle_id"]),
        ),
    )
    audit.update(
        {
            "selected_proven_candles": len(selected),
            "excluded_candles": len(ordered) - len(selected),
            "excluded_outside_newest_contiguous_run": sum(
                len(run) for run in runs if run is not selected
            ),
            "selected_coordinate_space": str(selected[0]["coordinate_space"]),
            "selected_start_sequence_index": int(selected[0]["sequence_index"]),
            "selected_end_sequence_index": int(selected[-1]["sequence_index"]),
        }
    )
    return selected, "PROVEN_NEWEST_CONTIGUOUS_RUN", audit


def _confirmed_pivots(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Find one-candle-left/one-closed-candle-right confirmed pivots.

    An outside candle can be both a local high and local low.  Intrabar order is
    unavailable in candle geometry, so those ambiguous pivots are excluded.
    Consecutive pivots of one kind retain only the more extreme observation.
    """

    candidates: list[dict[str, Any]] = []
    for index in range(1, len(candles) - 1):
        previous = _mapping(candles[index - 1].get("ohlc"))
        current = _mapping(candles[index].get("ohlc"))
        following = _mapping(candles[index + 1].get("ohlc"))
        pivot_low = (
            float(current["low"]) < float(previous["low"])
            and float(current["low"]) <= float(following["low"])
        )
        pivot_high = (
            float(current["high"]) > float(previous["high"])
            and float(current["high"]) >= float(following["high"])
        )
        if pivot_low == pivot_high:
            continue
        kind = "LOW" if pivot_low else "HIGH"
        value = float(current["low"] if pivot_low else current["high"])
        candidate = {
            "kind": kind,
            "value": round(value, 8),
            "candle": dict(candles[index]),
            "confirmation_candle": dict(candles[index + 1]),
        }
        if candidates and candidates[-1]["kind"] == kind:
            previous_value = _finite(
                candidates[-1].get("value"),
                field="confirmed_pivot.value",
            )
            more_extreme = value < previous_value if kind == "LOW" else value > previous_value
            if more_extreme:
                candidates[-1] = candidate
            continue
        candidates.append(candidate)
    return candidates


def _completed_swings(
    candles: Sequence[Mapping[str, Any]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int, str, dict[str, Any]]:
    proven, proof_status, proof_audit = _proven_candle_series(candles)
    if not proven:
        return [], 0, proof_status, proof_audit
    pivots = _confirmed_pivots(proven)
    completed: list[dict[str, Any]] = []
    for start, end in zip(pivots, pivots[1:]):
        if start["kind"] == end["kind"]:
            continue
        start_candle = cast(Mapping[str, Any], start["candle"])
        end_candle = cast(Mapping[str, Any], end["candle"])
        confirmation = cast(Mapping[str, Any], end["confirmation_candle"])
        direction = "UP" if start["kind"] == "LOW" else "DOWN"
        start_value = float(start["value"])
        end_value = float(end["value"])
        if (direction == "UP" and end_value <= start_value) or (
            direction == "DOWN" and end_value >= start_value
        ):
            continue
        swing_low = min(start_value, end_value)
        swing_high = max(start_value, end_value)
        swing_range = swing_high - swing_low
        scale = max(1.0, abs(swing_low), abs(swing_high))
        if swing_range <= scale * 1e-10:
            continue
        identity_source = {
            "start_candle_id": str(start_candle["candle_id"]),
            "end_candle_id": str(end_candle["candle_id"]),
            "confirmation_candle_id": str(confirmation["candle_id"]),
            "coordinate_space": str(start_candle["coordinate_space"]),
            "direction": direction,
            "start_value": round(start_value, 8),
            "end_value": round(end_value, 8),
        }
        completed.append(
            {
                "swing_id": f"swing:{_digest(identity_source, length=20)}",
                "identity_stable": True,
                "direction": direction,
                "side": "BULLISH" if direction == "UP" else "BEARISH",
                "coordinate_space": str(start_candle["coordinate_space"]),
                "start_candle_id": str(start_candle["candle_id"]),
                "end_candle_id": str(end_candle["candle_id"]),
                "confirmation_candle_id": str(confirmation["candle_id"]),
                "start_sequence_index": int(start_candle["sequence_index"]),
                "end_sequence_index": int(end_candle["sequence_index"]),
                "confirmation_sequence_index": int(confirmation["sequence_index"]),
                "start_value": round(start_value, 8),
                "end_value": round(end_value, 8),
                "swing_low": round(swing_low, 8),
                "swing_high": round(swing_high, 8),
                "swing_range": round(swing_range, 8),
                "completion_proof": {
                    "kind": "TWO_SIDED_CLOSED_CANDLE_PIVOT_CONFIRMATION",
                    "end_pivot_confirmed_by_candle_id": str(confirmation["candle_id"]),
                    "uses_forming_candle": False,
                },
            }
        )
    completed.sort(
        key=lambda row: (
            -int(row["end_sequence_index"]),
            -int(row["start_sequence_index"]),
            str(row["swing_id"]),
        )
    )
    selected = completed[:limit]
    return (
        selected,
        max(0, len(completed) - len(selected)),
        proof_status,
        proof_audit,
    )


def _retracement_value(swing: Mapping[str, Any], ratio: float) -> float:
    end_value = float(swing["end_value"])
    swing_range = float(swing["swing_range"])
    value = (
        end_value - ratio * swing_range
        if swing["direction"] == "UP"
        else end_value + ratio * swing_range
    )
    return round(value, 8)


def _retracement_study(
    candles: Sequence[Mapping[str, Any]],
    objects: Sequence[Mapping[str, Any]],
    *,
    max_completed_swings: int,
    max_observations: int,
    tolerance_ratio: float,
) -> dict[str, Any]:
    swings, omitted_swings, proof_status, proof_audit = _completed_swings(
        candles,
        limit=max_completed_swings,
    )
    observation_regime = "UNKNOWN"
    if candles:
        latest_observation_candle: Mapping[str, Any] = max(
            candles,
            key=lambda row: int(row.get("sequence_index", -1)),
        )
        observation_regime = str(
            latest_observation_candle.get("regime") or "UNKNOWN"
        )
    base: dict[str, Any] = {
        "schema_version": RETRACEMENT_CONFLUENCE_SCHEMA_VERSION,
        "status": "NO_PROVEN_COMPLETED_SWINGS",
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "safety": {
            "causal_claim": False,
            "grants_entry_permission": False,
            "grants_execution_permission": False,
            "may_issue_orders": False,
            "forming_candles_allowed": False,
            "mixed_coordinate_spaces_allowed": False,
        },
        "method": {
            "swing_completion": "TWO_SIDED_CLOSED_CANDLE_PIVOT_CONFIRMATION",
            "level_formula": (
                "UP: end_value - ratio*swing_range; "
                "DOWN: end_value + ratio*swing_range"
            ),
            "near_touch_distance_normalizer": "COMPLETED_SWING_RANGE",
            "tolerance_ratio": tolerance_ratio,
            "regime_partition_basis": "CURRENT_STUDY_FRAME_AT_CONFLUENCE_OBSERVATION",
            "experimental_level_notice": (
                "CUSTOM_71_8 is a nonstandard, user-defined experimental level; "
                "it is not represented as a standard Fibonacci ratio."
            ),
        },
        "level_catalog": [dict(row) for row in _RETRACEMENT_LEVEL_CATALOG],
        "reference_band": {
            "lower_level_id": "FIB_61_8",
            "lower_ratio": 0.618,
            "upper_level_id": "FIB_78_6",
            "upper_ratio": 0.786,
            "interpretation": "REFERENCE_BAND_ONLY",
        },
        "proof_status": proof_status,
        "proof_audit": proof_audit,
        "counts": {
            "proven_completed_swings": len(swings),
            "comparable_objects": 0,
            "evaluated_level_object_pairs": 0,
            "observations": 0,
            "relations": 0,
        },
        "caps": {
            "max_completed_swings": max_completed_swings,
            "max_observations": max_observations,
        },
        "truncated": omitted_swings > 0,
        "truncated_counts": {
            "completed_swings": omitted_swings,
            "observations": 0,
        },
        "observations": [],
        "relations": [],
    }
    if not swings:
        return base

    swing_spaces = {str(swing["coordinate_space"]) for swing in swings}
    comparable: list[dict[str, Any]] = []
    for object_row in objects:
        object_type = str(object_row["object_type"])
        family = _RETRACEMENT_OBJECT_FAMILIES.get(object_type)
        value_geometry = _mapping(object_row.get("value_geometry"))
        if family is None or not value_geometry or object_row.get("identity_stable") is not True:
            continue
        object_space = str(value_geometry.get("coordinate_space") or "")
        if object_space not in swing_spaces:
            raise ObjectRelationshipGraphValidationError(
                "retracement swing and object value coordinate spaces must match exactly"
            )
        candidate = dict(object_row)
        candidate["retracement_object_family"] = family
        comparable.append(candidate)
    base["counts"]["comparable_objects"] = len(comparable)
    if not comparable:
        base["status"] = "NO_COMPARABLE_OBJECTS"
        return base

    candidates: list[tuple[tuple[int, int, str, str], dict[str, Any], dict[str, Any]]] = []
    for swing_rank, swing in enumerate(swings):
        swing_range = float(swing["swing_range"])
        tolerance = swing_range * tolerance_ratio
        lower_reference_value = _retracement_value(swing, 0.618)
        upper_reference_value = _retracement_value(swing, 0.786)
        reference_low = min(lower_reference_value, upper_reference_value)
        reference_high = max(lower_reference_value, upper_reference_value)
        for level_rank, level in enumerate(_EVALUATED_RETRACEMENT_LEVELS):
            ratio = float(level["level_ratio"])
            level_value = _retracement_value(swing, ratio)
            for object_row in comparable:
                value_geometry = _mapping(object_row["value_geometry"])
                bounds = _mapping(value_geometry["bounds"])
                low = float(bounds["low"])
                high = float(bounds["high"])
                if low <= level_value <= high:
                    relation = "RETRACEMENT_LEVEL_OVERLAPS_OBJECT"
                    distance = 0.0
                else:
                    distance = min(abs(level_value - low), abs(level_value - high))
                    if distance > tolerance + max(1e-12, swing_range * 1e-12):
                        continue
                    relation = "RETRACEMENT_LEVEL_NEAR_TOUCHES_OBJECT"
                normalized_distance = distance / swing_range
                identity_source = {
                    "swing_id": swing["swing_id"],
                    "level_id": level["level_id"],
                    "object_type": object_row["object_type"],
                    "object_id": object_row["object_id"],
                }
                study_id = f"retracement-study:{_digest(identity_source, length=24)}"
                observation = {
                    "study_id": study_id,
                    "status": "COMPLETED",
                    "identity_stable": True,
                    "swing_id": str(swing["swing_id"]),
                    "swing_direction": str(swing["direction"]),
                    "side": str(swing["side"]),
                    "observation_regime": observation_regime,
                    "regime_basis": (
                        "CURRENT_STUDY_FRAME_AT_CONFLUENCE_OBSERVATION"
                    ),
                    "start_candle_id": str(swing["start_candle_id"]),
                    "end_candle_id": str(swing["end_candle_id"]),
                    "confirmation_candle_id": str(swing["confirmation_candle_id"]),
                    "coordinate_space": str(swing["coordinate_space"]),
                    "swing_start_value": float(swing["start_value"]),
                    "swing_end_value": float(swing["end_value"]),
                    "swing_low": float(swing["swing_low"]),
                    "swing_high": float(swing["swing_high"]),
                    "swing_range": swing_range,
                    "completion_proof": dict(
                        cast(Mapping[str, Any], swing["completion_proof"])
                    ),
                    "level_id": str(level["level_id"]),
                    "level_ratio": ratio,
                    "level_value": level_value,
                    "classification": str(level["classification"]),
                    "label": str(level["label"]),
                    "standard_fibonacci": bool(level["standard_fibonacci"]),
                    "user_defined": bool(level["user_defined"]),
                    "experimental": bool(level["experimental"]),
                    "ote_reference_band": {
                        "lower_ratio": 0.618,
                        "upper_ratio": 0.786,
                        "value_low": round(reference_low, 8),
                        "value_high": round(reference_high, 8),
                    },
                    "object_type": str(object_row["object_type"]),
                    "object_family": str(object_row["retracement_object_family"]),
                    "object_id": str(object_row["object_id"]),
                    "object_node_id": str(object_row["node_id"]),
                    "object_value_bounds": {
                        "low": round(low, 8),
                        "high": round(high, 8),
                    },
                    "object_value_axis_source": str(value_geometry.get("source") or "EXPLICIT"),
                    "relation": relation,
                    "distance": round(distance, 8),
                    "normalized_distance": round(normalized_distance, 8),
                    "tolerance": round(tolerance, 8),
                    "tolerance_ratio": tolerance_ratio,
                    "observational_confluence": True,
                    "causal": False,
                }
                relation_row = {
                    "relation_id": (
                        f"retracement-relation:{_digest({'study_id': study_id, 'relation': relation}, length=24)}"
                    ),
                    "study_id": study_id,
                    "relation": relation,
                    "swing_id": str(swing["swing_id"]),
                    "level_id": str(level["level_id"]),
                    "object_type": str(object_row["object_type"]),
                    "object_id": str(object_row["object_id"]),
                    "object_node_id": str(object_row["node_id"]),
                    "coordinate_space": str(swing["coordinate_space"]),
                    "observational": True,
                    "causal": False,
                }
                candidates.append(
                    (
                        (
                            swing_rank,
                            level_rank,
                            str(object_row["object_type"]),
                            str(object_row["object_id"]),
                        ),
                        observation,
                        relation_row,
                    )
                )

    candidates.sort(key=lambda item: item[0])
    selected = candidates[:max_observations]
    observations = [observation for _, observation, _ in selected]
    relations = [relation for _, _, relation in selected]
    omitted_observations = max(0, len(candidates) - len(selected))
    base["status"] = (
        "STUDIED_TRUNCATED"
        if omitted_swings > 0 or omitted_observations > 0
        else "STUDIED"
    )
    base["counts"].update(
        {
            "evaluated_level_object_pairs": (
                len(swings) * len(_EVALUATED_RETRACEMENT_LEVELS) * len(comparable)
            ),
            "observations": len(observations),
            "relations": len(relations),
        }
    )
    base["truncated"] = omitted_swings > 0 or omitted_observations > 0
    base["truncated_counts"]["observations"] = omitted_observations
    base["observations"] = observations
    base["relations"] = relations
    return base


def _edge(
    relation: str,
    source: str,
    target: str,
    *,
    directed: bool,
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    if not directed and target < source:
        source, target = target, source
    return {
        "edge_id": f"edge:{_digest({'relation': relation, 'source': source, 'target': target}, length=20)}",
        "relation": relation,
        "source": source,
        "target": target,
        "directed": directed,
        "observational": True,
        "causal": False,
        "proof": dict(proof),
    }


def _cap(value: object, *, field: str, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        raise ObjectRelationshipGraphValidationError(f"{field} must be an integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ObjectRelationshipGraphValidationError(f"{field} must be an integer") from exc
    if not lower <= parsed <= upper or float(cast(Any, value)) != parsed:
        raise ObjectRelationshipGraphValidationError(
            f"{field} must be in [{lower}, {upper}]"
        )
    return parsed


def build_object_relationship_graph_v3(
    studied_candles: Sequence[Mapping[str, Any]],
    objects: Sequence[Mapping[str, Any]],
    *,
    max_object_nodes: int = DEFAULT_MAX_OBJECT_NODES,
    max_candle_nodes: int = DEFAULT_MAX_CANDLE_NODES,
    max_edges: int = DEFAULT_MAX_GRAPH_EDGES,
    max_points_per_object: int = DEFAULT_MAX_POINTS_PER_OBJECT,
    max_completed_swings: int = DEFAULT_MAX_COMPLETED_SWINGS,
    max_retracement_observations: int = DEFAULT_MAX_RETRACEMENT_OBSERVATIONS,
    retracement_tolerance_ratio: float = DEFAULT_RETRACEMENT_TOLERANCE_RATIO,
) -> dict[str, Any]:
    """Build a deterministic, bounded graph from one observation scope.

    ``studied_candles`` must be ordered history or carry canonical
    ``sequence_position.index`` fields. All supplied objects are treated as
    co-present in this observation scope. That co-presence supports
    ``OBSERVED_WITH`` and ``CO_OCCURS`` only; it never creates an anchor or a
    causal relationship.
    """

    object_limit = _cap(
        max_object_nodes,
        field="max_object_nodes",
        lower=1,
        upper=MAX_OBJECT_NODES,
    )
    candle_limit = _cap(
        max_candle_nodes,
        field="max_candle_nodes",
        lower=1,
        upper=MAX_CANDLE_NODES,
    )
    edge_limit = _cap(max_edges, field="max_edges", lower=1, upper=MAX_GRAPH_EDGES)
    point_limit = _cap(
        max_points_per_object,
        field="max_points_per_object",
        lower=1,
        upper=MAX_POINTS_PER_OBJECT,
    )
    swing_limit = _cap(
        max_completed_swings,
        field="max_completed_swings",
        lower=1,
        upper=MAX_COMPLETED_SWINGS,
    )
    retracement_observation_limit = _cap(
        max_retracement_observations,
        field="max_retracement_observations",
        lower=1,
        upper=MAX_RETRACEMENT_OBSERVATIONS,
    )
    tolerance_ratio = _finite(
        retracement_tolerance_ratio,
        field="retracement_tolerance_ratio",
    )
    if not 0.0 <= tolerance_ratio <= 0.1:
        raise ObjectRelationshipGraphValidationError(
            "retracement_tolerance_ratio must be in [0, 0.1]"
        )
    tolerance_ratio = round(tolerance_ratio, 8)
    raw_candles = _rows(
        studied_candles, field="studied_candles", maximum=MAX_CANDLE_INPUT_ROWS
    )
    if not raw_candles:
        raise ObjectRelationshipGraphValidationError(
            "studied_candles must contain at least one studied candle"
        )
    raw_objects = _rows(objects, field="objects", maximum=MAX_OBJECT_INPUT_ROWS)

    candles = [
        _canonical_candle(row, source_index=index)
        for index, row in enumerate(raw_candles)
    ]
    candle_by_id: dict[str, dict[str, Any]] = {}
    for candle in candles:
        identity = str(candle["candle_id"])
        previous = candle_by_id.get(identity)
        if previous is not None and previous != candle:
            raise ObjectRelationshipGraphValidationError(
                f"conflicting studied candle identity: {identity}"
            )
        candle_by_id[identity] = candle
    candles = list(candle_by_id.values())
    explicit_latest = [row for row in candles if row["explicit_latest"]]
    if len(explicit_latest) > 1:
        raise ObjectRelationshipGraphValidationError(
            "studied_candles identifies more than one latest candle"
        )
    latest = (
        explicit_latest[0]
        if explicit_latest
        else max(
            candles,
            key=lambda row: (int(row["sequence_index"]), str(row["candle_id"])),
        )
    )

    canonical_objects = [_canonical_object(row, max_points=point_limit) for row in raw_objects]
    object_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in canonical_objects:
        object_id = str(row["object_id"])
        key = (
            str(row["object_type"]),
            object_id if object_id else str(row["evidence_digest"]),
        )
        previous = object_by_key.get(key)
        if previous is not None and previous != row:
            raise ObjectRelationshipGraphValidationError(
                f"conflicting object identity: {key[0]}:{key[1]}"
            )
        object_by_key[key] = row
    canonical_objects = sorted(
        object_by_key.values(),
        key=lambda row: (
            str(row["object_type"]),
            str(row["object_id"]),
            str(row["evidence_digest"]),
        ),
    )
    selected_objects = canonical_objects[:object_limit]
    retracement_study = _retracement_study(
        candles,
        selected_objects,
        max_completed_swings=swing_limit,
        max_observations=retracement_observation_limit,
        tolerance_ratio=tolerance_ratio,
    )

    anchored_ids = {
        association
        for row in selected_objects
        for association in cast(Sequence[str], row["explicit_candle_associations"])
        if association in candle_by_id
    }
    selected_candle_ids = {str(latest["candle_id"])}
    anchor_candidates = sorted(
        (candle_by_id[identity] for identity in anchored_ids if identity != latest["candle_id"]),
        key=lambda row: (-int(row["sequence_index"]), str(row["candle_id"])),
    )
    selected_candle_ids.update(
        str(row["candle_id"]) for row in anchor_candidates[: max(0, candle_limit - 1)]
    )
    selected_candles = sorted(
        (candle_by_id[identity] for identity in selected_candle_ids),
        key=lambda row: (int(row["sequence_index"]), str(row["candle_id"])),
    )
    selected_candle_by_id = {
        str(row["candle_id"]): row for row in selected_candles
    }

    candidates: list[tuple[int, dict[str, Any]]] = []
    for object_row in selected_objects:
        associations = cast(Sequence[str], object_row["explicit_candle_associations"])
        matched = [identity for identity in associations if identity in selected_candle_by_id]
        object_row["matched_candle_associations"] = matched
        object_row["unresolved_or_omitted_candle_associations"] = [
            identity for identity in associations if identity not in selected_candle_by_id
        ]
        for identity in matched:
            candle = selected_candle_by_id[identity]
            candidates.append(
                (
                    0,
                    _edge(
                        "ANCHORED_TO_CANDLE",
                        str(object_row["node_id"]),
                        str(candle["node_id"]),
                        directed=True,
                        proof={
                            "kind": "EXPLICIT_CANDLE_IDENTITY",
                            "candle_id": identity,
                        },
                    ),
                )
            )
        if str(latest["candle_id"]) not in matched:
            candidates.append(
                (
                    1,
                    _edge(
                        "OBSERVED_WITH",
                        str(object_row["node_id"]),
                        str(latest["node_id"]),
                        directed=False,
                        proof={
                            "kind": "SAME_GRAPH_OBSERVATION_SCOPE",
                            "latest_candle_id": str(latest["candle_id"]),
                            "anchor_inferred": False,
                        },
                    ),
                )
            )

    for index, first in enumerate(selected_objects):
        for second in selected_objects[index + 1 :]:
            overlap = _overlap(first, second)
            if overlap is not None:
                candidates.append(
                    (
                        2,
                        _edge(
                            "OVERLAPS",
                            str(first["node_id"]),
                            str(second["node_id"]),
                            directed=False,
                            proof={
                                "kind": "POSITIVE_NORMALIZED_RECTANGLE_INTERSECTION",
                                "coordinate_space": "NORMALIZED_FRAME",
                                **overlap,
                            },
                        ),
                    )
                )
            candidates.append(
                (
                    3,
                    _edge(
                        "CO_OCCURS",
                        str(first["node_id"]),
                        str(second["node_id"]),
                        directed=False,
                        proof={"kind": "SAME_GRAPH_OBSERVATION_SCOPE"},
                    ),
                )
            )

    candidates.sort(
        key=lambda item: (
            item[0],
            str(item[1]["relation"]),
            str(item[1]["source"]),
            str(item[1]["target"]),
        )
    )
    edges = [edge for _, edge in candidates[:edge_limit]]

    candle_nodes = [
        {
            "node_id": str(row["node_id"]),
            "node_type": "CANDLE",
            "candle_id": str(row["candle_id"]),
            "identity_stable": bool(row["identity_stable"]),
            "timestamp": row["timestamp"],
            "sequence_index": int(row["sequence_index"]),
            "is_latest": row["candle_id"] == latest["candle_id"],
            "coordinate_space": str(row["coordinate_space"]),
            "direction": str(row["direction"]),
            "type": str(row["type"]),
            "personality": str(row["personality"]),
            "regime": str(row["regime"]),
        }
        for row in selected_candles
    ]
    object_nodes = [
        {
            "node_id": str(row["node_id"]),
            "node_type": "MARKET_OBJECT",
            "object_type": str(row["object_type"]),
            "object_id": str(row["object_id"]),
            "identity_scope": str(row["identity_scope"]),
            "identity_stable": bool(row["identity_stable"]),
            "direction": str(row["direction"]),
            "confidence": float(row["confidence"]),
            "lifecycle": dict(cast(Mapping[str, Any], row["lifecycle"])),
            "geometry": (
                None
                if row["geometry"] is None
                else dict(cast(Mapping[str, Any], row["geometry"]))
            ),
            "value_geometry": (
                None
                if row["value_geometry"] is None
                else dict(cast(Mapping[str, Any], row["value_geometry"]))
            ),
            "explicit_candle_associations": list(
                cast(Sequence[str], row["explicit_candle_associations"])
            ),
            "matched_candle_associations": list(
                cast(Sequence[str], row["matched_candle_associations"])
            ),
            "unresolved_or_omitted_candle_associations": list(
                cast(
                    Sequence[str],
                    row["unresolved_or_omitted_candle_associations"],
                )
            ),
            "evidence_digest": str(row["evidence_digest"]),
        }
        for row in selected_objects
    ]
    relation_counts = {
        relation: sum(edge["relation"] == relation for edge in edges)
        for relation in (
            "ANCHORED_TO_CANDLE",
            "OBSERVED_WITH",
            "OVERLAPS",
            "CO_OCCURS",
        )
    }
    truncation = {
        "objects": max(0, len(canonical_objects) - len(selected_objects)),
        "candles": max(0, len(anchored_ids | {str(latest['candle_id'])}) - len(selected_candles)),
        "edges": max(0, len(candidates) - len(edges)),
    }
    truncated = any(count > 0 for count in truncation.values())
    return {
        "schema_version": OBJECT_RELATIONSHIP_GRAPH_SCHEMA_VERSION,
        "status": "READY_TRUNCATED" if truncated else "READY",
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "safety": {
            "causal_claim": False,
            "grants_entry_permission": False,
            "grants_execution_permission": False,
            "may_issue_orders": False,
        },
        "relationship_contract": {
            "observation_scope": "ONE_CURRENT_STUDY_CALL",
            "observed_with_is_anchor": False,
            "anchor_requires_explicit_matching_candle_identity": True,
            "overlap_requires_normalized_rectangle_intersection": True,
            "object_co_occurrence_is_causal": False,
            "retracement_requires_closed_identity_stable_confirmed_pivots": True,
            "retracement_requires_exact_value_coordinate_space_match": True,
            "retracement_requires_explicit_stable_object_identity": True,
            "retracement_confluence_is_trade_authority": False,
        },
        "latest_candle_id": str(latest["candle_id"]),
        "input_counts": {
            "candles": len(raw_candles),
            "objects": len(raw_objects),
        },
        "selected_counts": {
            "candle_nodes": len(candle_nodes),
            "object_nodes": len(object_nodes),
            "edges": len(edges),
        },
        "caps": {
            "max_candle_nodes": candle_limit,
            "max_object_nodes": object_limit,
            "max_edges": edge_limit,
            "max_points_per_object": point_limit,
            "max_completed_swings": swing_limit,
            "max_retracement_observations": retracement_observation_limit,
        },
        "truncated": truncated,
        "truncated_counts": truncation,
        "relation_counts": relation_counts,
        "nodes": candle_nodes + object_nodes,
        "edges": edges,
        "retracement_study": retracement_study,
    }


__all__ = [
    "DEFAULT_MAX_CANDLE_NODES",
    "DEFAULT_MAX_COMPLETED_SWINGS",
    "DEFAULT_MAX_GRAPH_EDGES",
    "DEFAULT_MAX_OBJECT_NODES",
    "DEFAULT_MAX_POINTS_PER_OBJECT",
    "DEFAULT_MAX_RETRACEMENT_OBSERVATIONS",
    "DEFAULT_RETRACEMENT_TOLERANCE_RATIO",
    "MAX_CANDLE_INPUT_ROWS",
    "MAX_CANDLE_NODES",
    "MAX_COMPLETED_SWINGS",
    "MAX_GRAPH_EDGES",
    "MAX_OBJECT_INPUT_ROWS",
    "MAX_OBJECT_NODES",
    "MAX_POINTS_PER_OBJECT",
    "MAX_RETRACEMENT_OBSERVATIONS",
    "OBJECT_RELATIONSHIP_GRAPH_SCHEMA_VERSION",
    "ObjectRelationshipGraphValidationError",
    "RETRACEMENT_CONFLUENCE_SCHEMA_VERSION",
    "build_object_relationship_graph_v3",
]
