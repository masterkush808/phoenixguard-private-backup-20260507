from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast


ORDER_POSITIONING_CANDIDATE_SCHEMA_VERSION = "PG_ORDER_POSITIONING_CANDIDATES_V3"
ORDER_POSITIONING_MAX_WINDOW_STEPS = 32
ORDER_POSITIONING_REPROJECTION_SCHEMA_VERSION = "PG_ORDER_POSITIONING_REPROJECTION_V1"

OrderIntentV3 = Literal["ENTRY_LIMIT", "ENTRY_STOP", "PROTECTIVE_STOP"]
OrderKindV3 = Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"]

_CANONICAL_OVERLAY_SCHEMA = "PG_V3_OVERLAY_OBJECT_V1"
_NORMALIZED_MODE = "CHART_NORMALIZED"
_ALLOWED_LIFECYCLES = {
    "ACTIVE",
    "CONFIRMED",
    "FRESH",
    "FRESH_ACTIVE",
    "MITIGATED_ACTIVE",
    "ROLE_FLIP_CONFIRMED",
}
_LIMIT_SOURCE_TYPES = {
    "BUY": {"DEMAND_ZONE", "ORDER_BLOCK", "RETEST_BOX", "SUPPORT_TRENDLINE"},
    "SELL": {"SUPPLY_ZONE", "ORDER_BLOCK", "RETEST_BOX", "RESISTANCE_TRENDLINE"},
}
_STOP_SOURCE_TYPES = {
    "BUY": {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"},
    "SELL": {"DEMAND_ZONE", "SUPPORT_TRENDLINE"},
}
_TRENDLINE_TYPES = {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE"}
_ENTRY_INTENTS = {"ENTRY_LIMIT", "ENTRY_STOP"}
_MIN_SOURCE_CONFIDENCE = 0.70
_MIN_SOURCE_TRUTH = 0.70
_MIN_ANCHOR_QUALITY = 0.65
_APPROACH_DISTANCE_NORM = 0.025
_MAX_FAVORABLE_CANDLES_BEFORE_ENTRY = 4
_MIN_REPROJECTION_ANCHORS = 3
_MAX_REPROJECTION_RMSE = 0.0125
_MAX_REPROJECTION_RESIDUAL = 0.03
_MAX_DISPLAY_BAND_NORM = 0.05
_REACTION_WINDOW_GEOMETRY_ROLE = "FORWARD_REACTION_WINDOW"
_REACTION_WINDOW_ANCHOR = "LATEST_COMPLETED_CANDLE"
_MAX_REACTION_STEP_NORM = 0.05
_CONFIRMED_STATES = {
    "CONFIRMED",
    "CLOSED_CONFIRMED",
    "CONFIRMED_CLOSED",
    "VALID",
}
_STOP_CONFIRMATION_EVENTS = {
    "BREAK_OF_STRUCTURE",
    "MARKET_STRUCTURE_SHIFT",
    "RECLAIM_AFTER_SWEEP",
    "RESISTANCE_RECLAIM",
    "SUPPORT_BREAK",
}


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(cast(Sequence[Any], value))


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for item in _sequence(value) if (row := _mapping(item))]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "pass", "passed"}
    return bool(value)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _box(value: Any) -> list[float]:
    values = _sequence(value)
    if len(values) < 4:
        return []
    parsed = [_finite(values[index]) for index in range(4)]
    if any(number is None for number in parsed):
        return []
    x0, y0, x1, y1 = cast(list[float], parsed)
    if x0 == x1 or y0 == y1:
        return []
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _inside(inner: Sequence[float], outer: Sequence[float], *, epsilon: float = 1e-9) -> bool:
    return (
        len(inner) == 4
        and len(outer) == 4
        and inner[0] >= outer[0] - epsilon
        and inner[1] >= outer[1] - epsilon
        and inner[2] <= outer[2] + epsilon
        and inner[3] <= outer[3] + epsilon
    )


def _normalize_box(box: Sequence[float], chart: Sequence[float]) -> list[float]:
    width = chart[2] - chart[0]
    height = chart[3] - chart[1]
    return [
        round((box[0] - chart[0]) / width, 6),
        round((box[1] - chart[1]) / height, 6),
        round((box[2] - chart[0]) / width, 6),
        round((box[3] - chart[1]) / height, 6),
    ]


def _normalize_y(value: float, chart: Sequence[float]) -> float:
    return round((value - chart[1]) / (chart[3] - chart[1]), 6)


def _stable_id(prefix: str, values: Sequence[str]) -> str:
    digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _overlay_rows(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("v3_overlay_objects", "overlay_objects"):
        rows = _rows(session.get(key))
        if rows:
            return rows
    overlays = session.get("overlays")
    rows = _rows(overlays)
    if rows:
        return rows
    overlay_map = _mapping(overlays)
    return _rows(overlay_map.get("objects") or overlay_map.get("all_objects"))


def _source_key(overlay: Mapping[str, Any]) -> str:
    return _text(
        overlay.get("track_id")
        or overlay.get("object_id")
    )


def _quality_score(overlay: Mapping[str, Any]) -> float:
    quality = _mapping(overlay.get("anchor_quality"))
    score = _finite(quality.get("score"))
    return score if score is not None else -1.0


def _overlay_bounds(overlay: Mapping[str, Any]) -> tuple[list[float], str]:
    overlay_type = _upper(overlay.get("type"))
    if overlay_type not in _TRENDLINE_TYPES:
        bounds = _box(overlay.get("bounds") or overlay.get("bbox"))
        return bounds, "" if bounds else "MISSING_SOURCE_BOUNDS"

    projected_band = _mapping(
        overlay.get("projected_entry_band") or overlay.get("projected_price_band")
    )
    if not projected_band or not _bool(projected_band.get("verified")):
        return [], "TRENDLINE_PROJECTED_BAND_UNVERIFIED"
    if _upper(projected_band.get("coordinate_mode")) != _upper(
        overlay.get("coordinate_mode")
    ):
        return [], "TRENDLINE_PROJECTED_BAND_COORDINATE_MISMATCH"
    bounds = _box(projected_band.get("bounds") or projected_band.get("bbox"))
    return bounds, "" if bounds else "TRENDLINE_PROJECTED_BAND_MISSING_BOUNDS"


def _verified_source_reason(
    overlay: Mapping[str, Any],
    *,
    frame_id: str,
    sequence_id: str,
    chart_transform_id: str,
    broker_source_lock_id: str,
    coordinate_mode: str,
    chart_bounds: Sequence[float],
) -> tuple[str, list[float]]:
    if _text(overlay.get("schema_version")) != _CANONICAL_OVERLAY_SCHEMA:
        return "NON_CANONICAL_OVERLAY", []
    if not _source_key(overlay):
        return "MISSING_STABLE_SOURCE_ID", []
    if _text(overlay.get("frame_id")) != frame_id:
        return "STALE_SOURCE_FRAME", []
    if _text(overlay.get("sequence_id")) != sequence_id:
        return "SOURCE_SEQUENCE_MISMATCH", []
    if _text(overlay.get("chart_transform_id")) != chart_transform_id:
        return "SOURCE_TRANSFORM_MISMATCH", []
    if _text(overlay.get("broker_source_lock_id")) != broker_source_lock_id:
        return "SOURCE_LOCK_MISMATCH", []
    if _upper(overlay.get("coordinate_mode")) != coordinate_mode:
        return "SOURCE_COORDINATE_MISMATCH", []
    if _upper(overlay.get("lifecycle_state")) not in _ALLOWED_LIFECYCLES:
        return "SOURCE_NOT_LIVE", []
    if _upper(overlay.get("anchor_evidence_status")) != "VALID":
        return "ANCHOR_EVIDENCE_INVALID", []
    evidence = _mapping(overlay.get("anchor_evidence"))
    if evidence.get("valid") is not True:
        return "ANCHOR_EVIDENCE_UNPROVEN", []
    quality = _mapping(overlay.get("anchor_quality"))
    if _quality_score(overlay) < _MIN_ANCHOR_QUALITY:
        return "ANCHOR_QUALITY_TOO_LOW", []
    for key in (
        "has_candle_anchor",
        "has_sequence_anchor",
        "inside_plot_area",
        "matches_symbol_timeframe",
        "chart_transform_valid",
    ):
        if quality.get(key) is not True:
            return f"ANCHOR_QUALITY_{key.upper()}_FAILED", []
    confidence = _finite(overlay.get("confidence"))
    truth = _finite(overlay.get("truth_score"))
    if confidence is None or confidence < _MIN_SOURCE_CONFIDENCE:
        return "SOURCE_CONFIDENCE_TOO_LOW", []
    if truth is None or truth < _MIN_SOURCE_TRUTH:
        return "SOURCE_TRUTH_TOO_LOW", []
    bounds, bounds_reason = _overlay_bounds(overlay)
    if bounds_reason:
        return bounds_reason, []
    if not _inside(bounds, chart_bounds):
        return "SOURCE_OUTSIDE_CHART", []
    return "", bounds


def _source_side_matches(overlay: Mapping[str, Any], expected: str) -> bool:
    source_type = _upper(overlay.get("type"))
    source_side = _upper(overlay.get("side"))
    if source_type in {"DEMAND_ZONE", "SUPPORT_TRENDLINE"}:
        return expected == "BUY" and source_side == "BUY"
    if source_type in {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"}:
        return expected == "SELL" and source_side == "SELL"
    return source_side == expected


def _confirmation_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in cast(Mapping[Any, Any], value).items():
            if _upper(key) in {
                "EVENT",
                "EVENT_TYPE",
                "TYPE",
                "REACTION_TYPE",
                "STRUCTURE_TYPE",
                "TAG",
                "TAGS",
                "KNOWLEDGE_TAGS",
            }:
                tokens.update(_confirmation_tokens(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in cast(Sequence[Any], value):
            tokens.update(_confirmation_tokens(nested))
    else:
        text = _upper(value).replace("-", "_").replace(" ", "_")
        if text:
            tokens.add(text)
    return tokens


def _stop_confirmation_reason(
    overlay: Mapping[str, Any],
    *,
    thesis_side: str,
) -> str:
    """Require one explicit, closed-candle structural break before a stop entry."""

    evidence = _mapping(overlay.get("confirmation_evidence"))
    state = _upper(
        evidence.get("state")
        or overlay.get("confirmation_state")
        or overlay.get("breakout_confirmation_state")
    )
    confirmation_valid = bool(
        evidence.get("valid") is True
        or overlay.get("stop_entry_confirmation_valid") is True
    )
    if state not in _CONFIRMED_STATES or not confirmation_valid:
        return "STOP_ENTRY_CLOSED_CONFIRMATION_UNPROVEN"
    if not (
        evidence.get("is_closed") is True
        or overlay.get("confirmation_is_closed") is True
        or overlay.get("stop_entry_confirmation_valid") is True
    ):
        return "STOP_ENTRY_CONFIRMATION_CANDLE_OPEN_OR_UNKNOWN"
    closed_key = _text(
        evidence.get("closed_candle_key")
        or overlay.get("confirmation_closed_candle_key")
        or overlay.get("confirmed_candle_key")
    )
    if not closed_key:
        return "STOP_ENTRY_CONFIRMATION_CANDLE_ID_MISSING"
    confirmation_side = _upper(
        evidence.get("side")
        or overlay.get("confirmation_side")
        or overlay.get("confirmation_direction")
        or overlay.get("breakout_side")
    )
    if confirmation_side != thesis_side:
        return "STOP_ENTRY_CONFIRMATION_SIDE_MISMATCH"
    tokens: set[str] = set()
    for value in (
        evidence,
        overlay.get("confirmation_event"),
        overlay.get("confirmation_type"),
        overlay.get("reaction_type"),
        overlay.get("knowledge_tags"),
    ):
        tokens.update(_confirmation_tokens(value))
    if not tokens.intersection(_STOP_CONFIRMATION_EVENTS):
        return "STOP_ENTRY_STRUCTURE_EVENT_MISSING"
    return ""


def order_positioning_stop_confirmation_reason_v3(
    overlay: Mapping[str, Any],
    *,
    thesis_side: str,
) -> str:
    """Return the fail-closed reason for an observational stop-entry source."""

    return _stop_confirmation_reason(overlay, thesis_side=_upper(thesis_side))


def _positive_area_overlap(left: Any, right: Any) -> bool:
    first = _box(left)
    second = _box(right)
    if not first or not second:
        return False
    overlap_width = min(first[2], second[2]) - max(first[0], second[0])
    overlap_height = min(first[3], second[3]) - max(first[1], second[1])
    return overlap_width > 1e-9 and overlap_height > 1e-9


def _distance_to_box(y: float, bounds: Sequence[float]) -> float:
    if y < bounds[1]:
        return bounds[1] - y
    if y > bounds[3]:
        return y - bounds[3]
    return 0.0


def _timing_state(y: float, bounds: Sequence[float]) -> str:
    distance = _distance_to_box(y, bounds)
    if distance <= 1e-9:
        return "AT_AREA"
    if distance <= _APPROACH_DISTANCE_NORM:
        return "NEAR_AREA"
    return "WAITING_EARLY"


def _reaction_window_contract(
    session: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    geometry_role = _upper(session.get("geometry_role"))
    reaction_window_anchor = _upper(session.get("reaction_window_anchor"))
    anchor_id = _text(session.get("reaction_window_anchor_id"))
    origin_x = _finite(session.get("reaction_window_origin_x_norm"))
    step_x = _finite(session.get("reaction_window_step_x_norm"))
    horizon_steps = _integer(session.get("reaction_window_horizon_steps"))
    if session.get("reaction_window_verified") is not True:
        return {}, "REACTION_WINDOW_UNVERIFIED"
    if geometry_role != _REACTION_WINDOW_GEOMETRY_ROLE:
        return {}, "REACTION_WINDOW_GEOMETRY_ROLE_INVALID"
    if reaction_window_anchor != _REACTION_WINDOW_ANCHOR or not anchor_id:
        return {}, "REACTION_WINDOW_ANCHOR_UNPROVEN"
    if (
        origin_x is None
        or step_x is None
        or horizon_steps is None
        or not 1 <= horizon_steps <= ORDER_POSITIONING_MAX_WINDOW_STEPS
        or not 0.0 <= origin_x < 1.0
        or not 0.0 < step_x <= _MAX_REACTION_STEP_NORM
    ):
        return {}, "REACTION_WINDOW_GEOMETRY_INVALID"
    end_x = origin_x + step_x * horizon_steps
    if end_x > 1.0 + 1e-9:
        return {}, "REACTION_WINDOW_HORIZON_TRUNCATED"
    end_x = min(1.0, end_x)
    if end_x - origin_x <= 1e-6:
        return {}, "REACTION_WINDOW_HAS_NO_FORWARD_ROOM"
    return (
        {
            "reaction_window_verified": True,
            "geometry_role": _REACTION_WINDOW_GEOMETRY_ROLE,
            "reaction_window_anchor": _REACTION_WINDOW_ANCHOR,
            "anchor_id": anchor_id,
            "reaction_window_anchor_id": anchor_id,
            "origin_x_norm": round(origin_x, 6),
            "reaction_window_origin_x_norm": round(origin_x, 6),
            "step_x_norm": round(step_x, 6),
            "reaction_window_step_x_norm": round(step_x, 6),
            "horizon_steps": horizon_steps,
            "reaction_window_horizon_steps": horizon_steps,
            "x_bounds": [round(origin_x, 6), round(end_x, 6)],
        },
        "",
    )


def _entry_is_late(
    *,
    intent: OrderIntentV3,
    thesis_side: str,
    current_y: float,
    source_bounds: Sequence[float],
    display_band_norm: float,
) -> bool:
    if intent == "ENTRY_LIMIT":
        return (
            current_y > source_bounds[3] + display_band_norm
            if thesis_side == "BUY"
            else current_y < source_bounds[1] - display_band_norm
        )
    return (
        current_y < source_bounds[1] - display_band_norm
        if thesis_side == "BUY"
        else current_y > source_bounds[3] + display_band_norm
    )


def _entry_bounds(
    intent: OrderIntentV3,
    thesis_side: str,
    source_bounds: Sequence[float],
    display_band_norm: float,
    reaction_x_bounds: Sequence[float],
) -> list[float]:
    left, right = float(reaction_x_bounds[0]), float(reaction_x_bounds[1])
    if intent == "ENTRY_LIMIT":
        return [left, float(source_bounds[1]), right, float(source_bounds[3])]
    if thesis_side == "BUY":
        top = max(0.0, source_bounds[1] - display_band_norm)
        return [left, top, right, source_bounds[1]]
    bottom = min(1.0, source_bounds[3] + display_band_norm)
    return [left, source_bounds[3], right, bottom]


def _protective_bounds(
    thesis_side: str,
    source_bounds: Sequence[float],
    display_band_norm: float,
    reaction_x_bounds: Sequence[float],
) -> list[float]:
    left, right = float(reaction_x_bounds[0]), float(reaction_x_bounds[1])
    if thesis_side == "BUY":
        bottom = min(1.0, source_bounds[3] + display_band_norm)
        return [left, source_bounds[3], right, bottom]
    top = max(0.0, source_bounds[1] - display_band_norm)
    return [left, top, right, source_bounds[1]]


def _order_kind(intent: OrderIntentV3, thesis_side: str) -> OrderKindV3:
    if intent == "ENTRY_LIMIT":
        return "BUY_LIMIT" if thesis_side == "BUY" else "SELL_LIMIT"
    if intent == "ENTRY_STOP":
        return "BUY_STOP" if thesis_side == "BUY" else "SELL_STOP"
    return "SELL_STOP" if thesis_side == "BUY" else "BUY_STOP"


def _zone_id(
    *,
    sequence_id: str,
    thesis_side: str,
    intent: OrderIntentV3,
    order_kind: OrderKindV3,
    source_key: str,
) -> str:
    return _stable_id(
        "order-zone",
        [sequence_id, thesis_side, intent, order_kind, source_key],
    )


def _reprojection_anchors(value: Any) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _rows(value):
        anchor_id = _text(row.get("anchor_id") or row.get("track_id"))
        x_norm = _finite(row.get("x_norm"))
        y_norm = _finite(row.get("y_norm"))
        if (
            not anchor_id
            or anchor_id in seen
            or x_norm is None
            or y_norm is None
            or not 0.0 <= x_norm <= 1.0
            or not 0.0 <= y_norm <= 1.0
        ):
            continue
        seen.add(anchor_id)
        anchors.append(
            {
                "anchor_id": anchor_id,
                "x_norm": round(x_norm, 6),
                "y_norm": round(y_norm, 6),
            }
        )
    return anchors[:24]


def fit_order_positioning_reprojection_v3(
    baseline_anchors: Any,
    current_anchors: Any,
) -> dict[str, Any]:
    """Fit one bounded global chart transform from stable candle anchors."""

    baseline = {row["anchor_id"]: row for row in _reprojection_anchors(baseline_anchors)}
    current = {row["anchor_id"]: row for row in _reprojection_anchors(current_anchors)}
    matched = [(baseline[key], current[key]) for key in sorted(baseline.keys() & current.keys())]
    if len(matched) < _MIN_REPROJECTION_ANCHORS:
        return {
            "schema_version": ORDER_POSITIONING_REPROJECTION_SCHEMA_VERSION,
            "status": "UNPROVEN",
            "reason": "THREE_STABLE_CANDLE_ANCHORS_REQUIRED",
            "matched_anchor_count": len(matched),
        }

    def fit_axis(axis: str) -> tuple[float, float] | None:
        source = [float(left[f"{axis}_norm"]) for left, _right in matched]
        target = [float(right[f"{axis}_norm"]) for _left, right in matched]
        source_mean = sum(source) / len(source)
        target_mean = sum(target) / len(target)
        variance = sum((value - source_mean) ** 2 for value in source)
        if variance <= 1e-6:
            return None
        scale = sum(
            (source_value - source_mean) * (target_value - target_mean)
            for source_value, target_value in zip(source, target)
        ) / variance
        return scale, target_mean - scale * source_mean

    x_fit = fit_axis("x")
    y_fit = fit_axis("y")
    if x_fit is None or y_fit is None:
        return {
            "schema_version": ORDER_POSITIONING_REPROJECTION_SCHEMA_VERSION,
            "status": "UNPROVEN",
            "reason": "ANCHOR_SPREAD_INSUFFICIENT",
            "matched_anchor_count": len(matched),
        }
    scale_x, offset_x = x_fit
    scale_y, offset_y = y_fit
    if not 0.5 <= scale_x <= 2.0 or not 0.5 <= scale_y <= 2.0:
        return {
            "schema_version": ORDER_POSITIONING_REPROJECTION_SCHEMA_VERSION,
            "status": "UNPROVEN",
            "reason": "GLOBAL_TRANSFORM_SCALE_OUT_OF_RANGE",
            "matched_anchor_count": len(matched),
        }
    residuals = [
        math.hypot(
            scale_x * float(left["x_norm"]) + offset_x - float(right["x_norm"]),
            scale_y * float(left["y_norm"]) + offset_y - float(right["y_norm"]),
        )
        for left, right in matched
    ]
    rmse = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    max_residual = max(residuals)
    if rmse > _MAX_REPROJECTION_RMSE or max_residual > _MAX_REPROJECTION_RESIDUAL:
        return {
            "schema_version": ORDER_POSITIONING_REPROJECTION_SCHEMA_VERSION,
            "status": "UNPROVEN",
            "reason": "GLOBAL_TRANSFORM_RESIDUAL_TOO_HIGH",
            "matched_anchor_count": len(matched),
            "rmse": round(rmse, 6),
            "max_residual": round(max_residual, 6),
        }
    return {
        "schema_version": ORDER_POSITIONING_REPROJECTION_SCHEMA_VERSION,
        "status": "PROVEN",
        "reason": "GLOBAL_CANDLE_ANCHOR_AFFINE_FIT",
        "matched_anchor_count": len(matched),
        "scale_x": round(scale_x, 9),
        "offset_x": round(offset_x, 9),
        "scale_y": round(scale_y, 9),
        "offset_y": round(offset_y, 9),
        "rmse": round(rmse, 6),
        "max_residual": round(max_residual, 6),
    }


def reproject_order_positioning_bounds_v3(
    bounds: Any,
    transform: Mapping[str, Any],
) -> list[float]:
    source = _box(bounds)
    if not source or _upper(transform.get("status")) != "PROVEN":
        return []
    scale_x = _finite(transform.get("scale_x"))
    offset_x = _finite(transform.get("offset_x"))
    scale_y = _finite(transform.get("scale_y"))
    offset_y = _finite(transform.get("offset_y"))
    if None in {scale_x, offset_x, scale_y, offset_y}:
        return []
    projected = [
        cast(float, scale_x) * source[0] + cast(float, offset_x),
        cast(float, scale_y) * source[1] + cast(float, offset_y),
        cast(float, scale_x) * source[2] + cast(float, offset_x),
        cast(float, scale_y) * source[3] + cast(float, offset_y),
    ]
    if any(not math.isfinite(value) or value < -0.03 or value > 1.03 for value in projected):
        return []
    normalized = [round(max(0.0, min(1.0, value)), 6) for value in projected]
    return normalized if normalized[2] > normalized[0] and normalized[3] > normalized[1] else []


def inverse_reproject_order_positioning_y_v3(
    value: Any,
    transform: Mapping[str, Any],
) -> float | None:
    current = _finite(value)
    scale_y = _finite(transform.get("scale_y"))
    offset_y = _finite(transform.get("offset_y"))
    if (
        current is None
        or scale_y is None
        or offset_y is None
        or _upper(transform.get("status")) != "PROVEN"
        or abs(scale_y) <= 1e-9
    ):
        return None
    baseline = (current - offset_y) / scale_y
    return round(baseline, 6) if 0.0 <= baseline <= 1.0 else None


def _zone_payload(
    *,
    overlay: Mapping[str, Any],
    normalized_source: Sequence[float],
    current_y: float,
    sequence_id: str,
    thesis_side: str,
    intent: OrderIntentV3,
    display_band_norm: float,
    reaction_window: Mapping[str, Any],
) -> dict[str, Any]:
    order_kind = _order_kind(intent, thesis_side)
    source_key = _source_key(overlay)
    bounds = _entry_bounds(
        intent,
        thesis_side,
        normalized_source,
        display_band_norm,
        cast(Sequence[float], reaction_window["x_bounds"]),
    )
    zone_id = _zone_id(
        sequence_id=sequence_id,
        thesis_side=thesis_side,
        intent=intent,
        order_kind=order_kind,
        source_key=source_key,
    )
    route = "PULLBACK_LIMIT" if intent == "ENTRY_LIMIT" else "BREAKOUT_STOP"
    return {
        "zone_id": zone_id,
        "intent": intent,
        "order_kind": order_kind,
        "side": thesis_side,
        "thesis_side": thesis_side,
        "route": route,
        "bounds": bounds,
        "source_bounds": list(normalized_source),
        "geometry_role": _REACTION_WINDOW_GEOMETRY_ROLE,
        "reaction_window_anchor": _REACTION_WINDOW_ANCHOR,
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "coordinate_mode": _NORMALIZED_MODE,
        "boundary_y_norm": bounds[1] if thesis_side == "BUY" else bounds[3],
        "display_band_norm": display_band_norm,
        "timing_state": _timing_state(current_y, bounds),
        "late_chase": False,
        "source_overlay_id": _text(overlay.get("overlay_id") or overlay.get("id")),
        "source_object_id": _text(overlay.get("object_id")),
        "source_track_id": _text(overlay.get("track_id")),
        "source_key": source_key,
        "source_type": _upper(overlay.get("type")),
        "source_anchor_candle_indices": _sequence(
            overlay.get("anchor_candle_indices") or overlay.get("anchor_candles")
        ),
        "source_truth_score": _finite(overlay.get("truth_score")),
        "source_confidence": _finite(overlay.get("confidence")),
        "source_anchor_quality": _quality_score(overlay),
        "source_latest_anchor_index": max(
            (
                index
                for value in _sequence(
                    overlay.get("anchor_candle_indices")
                    or overlay.get("anchor_candles")
                )
                if (index := _integer(value)) is not None and index >= 0
            ),
            default=-1,
        ),
        "status": "CANDIDATE",
        "status_reason": "verified structural positioning area",
        "last_updated_step": 0,
    }


def _protective_zone(
    *,
    entry_zone: Mapping[str, Any],
    overlay: Mapping[str, Any],
    normalized_source: Sequence[float],
    sequence_id: str,
    thesis_side: str,
    display_band_norm: float,
    reaction_window: Mapping[str, Any],
) -> dict[str, Any]:
    intent: OrderIntentV3 = "PROTECTIVE_STOP"
    order_kind = _order_kind(intent, thesis_side)
    entry_id = _text(entry_zone.get("zone_id"))
    source_key = _source_key(overlay)
    zone_id = _stable_id(
        "order-zone",
        [sequence_id, thesis_side, intent, order_kind, source_key, entry_id],
    )
    bounds = _protective_bounds(
        thesis_side,
        normalized_source,
        display_band_norm,
        cast(Sequence[float], reaction_window["x_bounds"]),
    )
    return {
        "zone_id": zone_id,
        "intent": intent,
        "order_kind": order_kind,
        "side": "SELL" if thesis_side == "BUY" else "BUY",
        "thesis_side": thesis_side,
        "route": "PROTECTION",
        "protected_entry_zone_id": entry_id,
        "bounds": bounds,
        "source_bounds": list(normalized_source),
        "geometry_role": _REACTION_WINDOW_GEOMETRY_ROLE,
        "reaction_window_anchor": _REACTION_WINDOW_ANCHOR,
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "coordinate_mode": _NORMALIZED_MODE,
        "boundary_y_norm": bounds[3] if thesis_side == "BUY" else bounds[1],
        "display_band_norm": display_band_norm,
        "timing_state": "STANDBY",
        "late_chase": False,
        "source_overlay_id": _text(overlay.get("overlay_id") or overlay.get("id")),
        "source_object_id": _text(overlay.get("object_id")),
        "source_track_id": _text(overlay.get("track_id")),
        "source_key": source_key,
        "source_type": _upper(overlay.get("type")),
        "source_anchor_candle_indices": _sequence(
            overlay.get("anchor_candle_indices") or overlay.get("anchor_candles")
        ),
        "source_truth_score": _finite(overlay.get("truth_score")),
        "source_confidence": _finite(overlay.get("confidence")),
        "source_anchor_quality": _quality_score(overlay),
        "source_latest_anchor_index": max(
            (
                index
                for value in _sequence(
                    overlay.get("anchor_candle_indices")
                    or overlay.get("anchor_candles")
                )
                if (index := _integer(value)) is not None and index >= 0
            ),
            default=-1,
        ),
        "status": "CANDIDATE",
        "status_reason": "protective stop beyond verified structural boundary",
        "last_updated_step": 0,
    }


def _candidate_blocked(
    blockers: Sequence[str],
    *,
    side: str = "HOLD",
    frame_id: str = "",
    sequence_id: str = "",
    chart_transform_id: str = "",
    broker_source_lock_id: str = "",
    market: str = "",
    timeframe: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": ORDER_POSITIONING_CANDIDATE_SCHEMA_VERSION,
        "status": "BLOCKED",
        "side": side,
        "frame_id": frame_id,
        "sequence_id": sequence_id,
        "chart_transform_id": chart_transform_id,
        "broker_source_lock_id": broker_source_lock_id,
        "market": market,
        "timeframe": timeframe,
        "coordinate_mode": _NORMALIZED_MODE,
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "current_price_y_norm": None,
        "horizon_steps": 0,
        "candidate_zones": [],
        "rejected_sources": [],
        "blockers": list(dict.fromkeys(blockers)),
        "contract_reason": "positioning is hidden until every geometry proof is valid",
    }


def build_order_positioning_candidates_v3(session: Mapping[str, Any]) -> dict[str, Any]:
    """Build limit, stop-entry, and protective areas from verified V3 geometry.

    Required session fields are ``side``, ``thesis_verified``, ``frame_id``,
    ``sequence_id``, ``chart_transform_id``, ``broker_source_lock_id``,
    ``coordinate_mode``, ``chart_bounds``, ``current_price_y``,
    ``current_price_verified``, ``timing_verified``,
    ``favorable_candles_since_origin``, a named latest-completed-candle
    reaction window, and canonical V3 overlay objects.

    The function deliberately does not consume trigger, target, sniper, model
    path, or raw-detection geometry. Missing proof returns ``BLOCKED`` with no
    zones; it never guesses a price area.
    """

    side = _upper(session.get("side"))
    frame_id = _text(session.get("frame_id"))
    sequence_id = _text(session.get("sequence_id"))
    chart_transform_id = _text(session.get("chart_transform_id"))
    broker_source_lock_id = _text(session.get("broker_source_lock_id"))
    coordinate_mode = _upper(session.get("coordinate_mode"))
    market = _upper(session.get("market") or session.get("pair") or session.get("symbol"))
    timeframe = _upper(session.get("timeframe"))
    blockers: list[str] = []

    if side not in {"BUY", "SELL"}:
        blockers.append("THESIS_SIDE_UNPROVEN")
    if not _bool(session.get("thesis_verified")):
        blockers.append("THESIS_UNVERIFIED")
    if not frame_id:
        blockers.append("FRAME_ID_MISSING")
    if not sequence_id:
        blockers.append("SEQUENCE_ID_MISSING")
    if not chart_transform_id:
        blockers.append("CHART_TRANSFORM_ID_MISSING")
    if not broker_source_lock_id:
        blockers.append("BROKER_SOURCE_LOCK_ID_MISSING")
    if not coordinate_mode:
        blockers.append("COORDINATE_MODE_MISSING")
    if not market:
        blockers.append("MARKET_IDENTITY_MISSING")
    if not timeframe:
        blockers.append("TIMEFRAME_IDENTITY_MISSING")
    if _upper(session.get("price_axis_orientation")) != "SCREEN_Y_INCREASES_DOWN":
        blockers.append("PRICE_AXIS_ORIENTATION_UNPROVEN")
    if _upper(session.get("current_price_basis")) != _REACTION_WINDOW_ANCHOR:
        blockers.append("BASELINE_PRICE_ANCHOR_UNPROVEN")

    reaction_window, reaction_window_reason = _reaction_window_contract(session)
    if reaction_window_reason:
        blockers.append(reaction_window_reason)

    display_band_norm = _finite(session.get("display_band_norm"))
    if (
        session.get("display_band_verified") is not True
        or display_band_norm is None
        or not 0.0 < display_band_norm <= _MAX_DISPLAY_BAND_NORM
    ):
        blockers.append("DISPLAY_BAND_GEOMETRY_UNVERIFIED")

    chart_bounds = _box(session.get("chart_bounds"))
    if not chart_bounds:
        blockers.append("CHART_BOUNDS_UNPROVEN")
    current_price_y = _finite(session.get("current_price_y"))
    if not _bool(session.get("current_price_verified")) or current_price_y is None:
        blockers.append("CURRENT_PRICE_GEOMETRY_UNVERIFIED")
    elif chart_bounds and not chart_bounds[1] <= current_price_y <= chart_bounds[3]:
        blockers.append("CURRENT_PRICE_OUTSIDE_CHART")

    favorable_candles = _integer(session.get("favorable_candles_since_origin"))
    if not _bool(session.get("timing_verified")) or favorable_candles is None:
        blockers.append("ENTRY_TIMING_UNVERIFIED")
    elif favorable_candles < 0:
        blockers.append("ENTRY_TIMING_INVALID")
    elif favorable_candles > _MAX_FAVORABLE_CANDLES_BEFORE_ENTRY:
        blockers.append("LATE_CHASE_FIVE_OR_MORE_FAVORABLE_CANDLES")

    if blockers:
        return _candidate_blocked(
            blockers,
            side=side,
            frame_id=frame_id,
            sequence_id=sequence_id,
            chart_transform_id=chart_transform_id,
            broker_source_lock_id=broker_source_lock_id,
            market=market,
            timeframe=timeframe,
        )

    if current_price_y is None or not chart_bounds or display_band_norm is None:
        return _candidate_blocked(["GEOMETRY_UNAVAILABLE"], side=side)
    current_y_norm = _normalize_y(current_price_y, chart_bounds)
    route_candidates: list[
        tuple[dict[str, Any], Mapping[str, Any], list[float]]
    ] = []
    rejected: list[dict[str, str]] = []

    source_rows = sorted(
        _overlay_rows(session),
        key=lambda overlay: (
            _source_key(overlay),
            -_quality_score(overlay),
            -(_finite(overlay.get("truth_score")) or 0.0),
            -(_finite(overlay.get("confidence")) or 0.0),
            _text(overlay.get("overlay_id") or overlay.get("id")),
        ),
    )
    for overlay in source_rows:
        source_type = _upper(overlay.get("type"))
        relevant = (
            source_type in _LIMIT_SOURCE_TYPES[side]
            or source_type in _STOP_SOURCE_TYPES[side]
        )
        if not relevant:
            continue
        reason, source_bounds = _verified_source_reason(
            overlay,
            frame_id=frame_id,
            sequence_id=sequence_id,
            chart_transform_id=chart_transform_id,
            broker_source_lock_id=broker_source_lock_id,
            coordinate_mode=coordinate_mode,
            chart_bounds=chart_bounds,
        )
        if reason:
            rejected.append({"source_key": _source_key(overlay), "reason": reason})
            continue
        normalized_source = _normalize_box(source_bounds, chart_bounds)

        intents: list[OrderIntentV3] = []
        source_role_considered = False
        if source_type in _LIMIT_SOURCE_TYPES[side] and _source_side_matches(overlay, side):
            source_role_considered = True
            intents.append("ENTRY_LIMIT")
        opposite = "SELL" if side == "BUY" else "BUY"
        if source_type in _STOP_SOURCE_TYPES[side] and _source_side_matches(overlay, opposite):
            source_role_considered = True
            confirmation_reason = _stop_confirmation_reason(
                overlay,
                thesis_side=side,
            )
            if confirmation_reason:
                rejected.append(
                    {
                        "source_key": _source_key(overlay),
                        "reason": confirmation_reason,
                    }
                )
            else:
                intents.append("ENTRY_STOP")
        if not intents and not source_role_considered:
            rejected.append(
                {"source_key": _source_key(overlay), "reason": "SOURCE_SIDE_ROLE_MISMATCH"}
            )
            continue

        for intent in intents:
            if _entry_is_late(
                intent=intent,
                thesis_side=side,
                current_y=current_y_norm,
                source_bounds=normalized_source,
                display_band_norm=display_band_norm,
            ):
                rejected.append(
                    {
                        "source_key": _source_key(overlay),
                        "reason": f"LATE_CHASE_{intent}",
                    }
                )
                continue
            entry = _zone_payload(
                overlay=overlay,
                normalized_source=normalized_source,
                current_y=current_y_norm,
                sequence_id=sequence_id,
                thesis_side=side,
                intent=intent,
                display_band_norm=display_band_norm,
                reaction_window=reaction_window,
            )
            if _box(entry.get("bounds")):
                route_candidates.append((entry, overlay, normalized_source))

    def route_rank(
        route: tuple[dict[str, Any], Mapping[str, Any], list[float]],
    ) -> tuple[float, int, float, float, float, str, str]:
        entry = route[0]
        latest_anchor_index = _finite(entry.get("source_latest_anchor_index"))
        return (
            _distance_to_box(current_y_norm, _box(entry.get("bounds"))),
            -int(latest_anchor_index if latest_anchor_index is not None else -1),
            -(_finite(entry.get("source_anchor_quality")) or 0.0),
            -(_finite(entry.get("source_truth_score")) or 0.0),
            -(_finite(entry.get("source_confidence")) or 0.0),
            _text(entry.get("source_key")),
            _text(entry.get("zone_id")),
        )

    selected_routes: list[
        tuple[dict[str, Any], Mapping[str, Any], list[float]]
    ] = []
    for intent in ("ENTRY_LIMIT", "ENTRY_STOP"):
        matching = sorted(
            (
                route
                for route in route_candidates
                if _upper(route[0].get("intent")) == intent
            ),
            key=route_rank,
        )
        if not matching:
            continue
        selected_routes.append(matching[0])
        for entry, _, _ in matching[1:]:
            rejected.append(
                {
                    "source_key": _text(entry.get("source_key")),
                    "reason": f"VALID_CONTEXT_NOT_NEAREST_{intent}",
                }
            )

    zones: list[dict[str, Any]] = []
    for entry, overlay, normalized_source in selected_routes:
        protective = _protective_zone(
            entry_zone=entry,
            overlay=overlay,
            normalized_source=normalized_source,
            sequence_id=sequence_id,
            thesis_side=side,
            display_band_norm=display_band_norm,
            reaction_window=reaction_window,
        )
        if not _box(protective.get("bounds")):
            continue
        if any(
            _positive_area_overlap(candidate.get("bounds"), accepted.get("bounds"))
            for candidate in (entry, protective)
            for accepted in zones
        ):
            rejected.append(
                {
                    "source_key": _text(entry.get("source_key")),
                    "reason": "CONFLICTING_ORDER_AREA_OVERLAP",
                }
            )
            continue
        zones.extend((entry, protective))

    intent_order = {"ENTRY_LIMIT": 0, "ENTRY_STOP": 1, "PROTECTIVE_STOP": 2}
    zones.sort(
        key=lambda zone: (
            intent_order.get(_upper(zone.get("intent")), 99),
            _upper(zone.get("order_kind")),
            _text(zone.get("zone_id")),
        )
    )
    if not zones:
        blocked = _candidate_blocked(
            ["NO_VERIFIED_POSITIONING_SOURCE"],
            side=side,
            frame_id=frame_id,
            sequence_id=sequence_id,
            chart_transform_id=chart_transform_id,
            broker_source_lock_id=broker_source_lock_id,
            market=market,
            timeframe=timeframe,
        )
        blocked["rejected_sources"] = rejected
        return blocked

    return {
        "schema_version": ORDER_POSITIONING_CANDIDATE_SCHEMA_VERSION,
        "status": "READY",
        "side": side,
        "frame_id": frame_id,
        "sequence_id": sequence_id,
        "chart_transform_id": chart_transform_id,
        "broker_source_lock_id": broker_source_lock_id,
        "market": market,
        "timeframe": timeframe,
        "coordinate_mode": _NORMALIZED_MODE,
        "price_axis_orientation": "SCREEN_Y_INCREASES_DOWN",
        "display_band_norm": round(display_band_norm, 6),
        "display_band_basis": _text(session.get("display_band_basis")),
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "current_price_y_norm": current_y_norm,
        "baseline_price_y_norm": current_y_norm,
        "current_price_basis": _REACTION_WINDOW_ANCHOR,
        "geometry_role": _REACTION_WINDOW_GEOMETRY_ROLE,
        "reaction_window_anchor": _REACTION_WINDOW_ANCHOR,
        "reaction_window": reaction_window,
        "horizon_steps": _integer(reaction_window.get("horizon_steps")) or 0,
        "timing": {
            "verified": True,
            "favorable_candles_since_origin": favorable_candles,
            "late_after_favorable_candles": _MAX_FAVORABLE_CANDLES_BEFORE_ENTRY,
        },
        "candidate_zones": zones,
        "reprojection_anchors": _reprojection_anchors(
            session.get("reprojection_anchors")
        ),
        "rejected_sources": rejected,
        "blockers": [],
        "contract_reason": (
            "verified structural areas define positioning; overlays do not authorize a trade"
        ),
    }


__all__ = [
    "ORDER_POSITIONING_CANDIDATE_SCHEMA_VERSION",
    "ORDER_POSITIONING_MAX_WINDOW_STEPS",
    "ORDER_POSITIONING_REPROJECTION_SCHEMA_VERSION",
    "build_order_positioning_candidates_v3",
    "fit_order_positioning_reprojection_v3",
    "inverse_reproject_order_positioning_y_v3",
    "order_positioning_stop_confirmation_reason_v3",
    "reproject_order_positioning_bounds_v3",
]
