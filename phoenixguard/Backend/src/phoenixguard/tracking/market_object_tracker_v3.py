from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping, Sequence, cast

from phoenixguard.vision.overlay_geometry import normalize_bbox

try:
    from phoenixguard.vision.v3_overlay_contract import (
        TYPE_LAYER_MAP as _contract_type_layer_map,
        TYPE_ROLE_MAP as _contract_type_role_map,
        V3_OVERLAY_SCHEMA_VERSION as _contract_overlay_schema_version,
        normalize_bounds,
        normalize_overlay_type,
        normalize_v3_overlay_object,
        stable_overlay_id,
        validate_overlay_payload,
    )
except Exception:
    _contract_overlay_schema_version = "PG_V3_OVERLAY_OBJECT"
    _contract_type_layer_map = {
        "CURRENT_CANDLE": "recent_candles",
        "IMPULSE_BOX": "major_swings",
        "PULLBACK_BOX": "local_swings",
        "RETEST_BOX": "trigger_zones",
        "CONTINUATION_BOX": "trigger_zones",
        "SNIPER_ENTRY_BOX": "trigger_zones",
        "TARGET_ZONE_BOX": "target_zones",
        "INVALIDATION_BOX": "invalidation",
        "SUPPLY_ZONE": "supply_demand",
        "DEMAND_ZONE": "supply_demand",
        "OPPOSING_FORCE": "supply_demand",
        "SUPPORT_TRENDLINE": "trendlines",
        "RESISTANCE_TRENDLINE": "trendlines",
        "INNER_TRENDLINE": "trendlines",
        "ANGLE_VECTOR": "active_council_decision",
        "PREDICTION_PATH": "active_council_decision",
        "PROGRESSION_PATH": "historical_replay",
        "BROKER_CONTROL": "broker_controls",
    }
    _contract_type_role_map = {
        "CURRENT_CANDLE": "current_candle",
        "SNIPER_ENTRY_BOX": "sniper",
        "RETEST_BOX": "trigger",
        "CONTINUATION_BOX": "continuation",
        "TARGET_ZONE_BOX": "target",
        "INVALIDATION_BOX": "invalidation",
        "SUPPLY_ZONE": "supply",
        "DEMAND_ZONE": "demand",
        "OPPOSING_FORCE": "opposing_force",
        "SUPPORT_TRENDLINE": "support_trendline",
        "RESISTANCE_TRENDLINE": "resistance_trendline",
        "INNER_TRENDLINE": "inner_trendline",
        "ANGLE_VECTOR": "angle",
        "PREDICTION_PATH": "prediction",
    }

    def _sequence_values(value: Any) -> list[Any]:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return list(cast(Sequence[Any], value))
        return []

    def normalize_bounds(value: Any) -> list[float] | None:
        values = _sequence_values(value)
        bbox = normalize_bbox(values) if values else None
        if bbox is not None:
            return [float(item) for item in bbox]
        points: list[tuple[float, float]] = []
        for item in values:
            point = _sequence_values(item)
            if len(point) >= 2:
                points.append((_float(point[0]), _float(point[1])))
        if not points:
            return None
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        pad = 3.0 if max(xs) <= min(xs) or max(ys) <= min(ys) else 0.0
        return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]

    def stable_overlay_id(*parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return f"v3ov_{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:16]}"

    def normalize_overlay_type(raw: Any, *, layer: Any = "", role: Any = "", side: Any = "") -> str:
        value = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
        if value in _contract_type_layer_map:
            return value
        role_value = str(role or "").strip().lower()
        if role_value in {"sniper", "aggressive_sniper"}:
            return "SNIPER_ENTRY_BOX"
        if role_value in {"target"}:
            return "TARGET_ZONE_BOX"
        if role_value in {"invalidation", "cancel"}:
            return "INVALIDATION_BOX"
        if role_value in {"trigger", "retest", "primary"}:
            return "RETEST_BOX"
        if role_value in {"pullback", "reclaim"}:
            return "PULLBACK_BOX"
        if role_value in {"support_trend", "support_trendline", "support_line"}:
            return "SUPPORT_TRENDLINE"
        if role_value in {"resistance_trend", "resistance_trendline", "resistance_line"}:
            return "RESISTANCE_TRENDLINE"
        if role_value in {"inner_trend", "inner_trendline", "inner_line"}:
            return "INNER_TRENDLINE"
        if role_value in {"support", "demand"}:
            return "DEMAND_ZONE"
        if role_value in {"resistance", "supply"}:
            return "SUPPLY_ZONE"
        layer_value = str(layer or "").strip().lower()
        if layer_value == "recent_candles":
            return "CURRENT_CANDLE"
        if layer_value == "major_swings":
            return "IMPULSE_BOX"
        if layer_value == "local_swings":
            return "PULLBACK_BOX"
        if layer_value == "supply_demand":
            return "SUPPLY_ZONE" if str(side or "").upper() == "SELL" else "DEMAND_ZONE"
        if layer_value == "historical_replay":
            return "PROGRESSION_PATH"
        if layer_value == "broker_controls":
            return "BROKER_CONTROL"
        return "CONTINUATION_BOX"

    def normalize_v3_overlay_object(
        raw: Mapping[str, Any],
        *,
        strict: bool = True,
        image_size: Sequence[Any] | None = None,
        fallback_index: int = 0,
        frame_id: int | str | None = None,
        sequence_id: str = "",
        chart_transform_id: str = "",
        source_agent: str = "market_object_tracker_v3",
    ) -> dict[str, Any]:
        bounds = normalize_bounds(raw.get("bounds", raw.get("bbox", raw.get("box", raw.get("rect")))))
        if bounds is None:
            if strict:
                raise ValueError("invalid overlay bounds")
            return {}
        overlay_type = normalize_overlay_type(raw.get("type"), layer=raw.get("layer"), role=raw.get("role"), side=raw.get("side"))
        confidence = _clip01(raw.get("confidence", raw.get("truth_score", 0.0)))
        truth_score = _clip01(raw.get("truth_score", confidence))
        side = _upper_side(raw.get("side", raw.get("direction", "HOLD")))
        label = _text(raw.get("label") or raw.get("key") or overlay_type.replace("_", " "))
        return {
            "schema_version": _contract_overlay_schema_version,
            "overlay_id": _text(raw.get("overlay_id") or raw.get("id") or raw.get("key") or stable_overlay_id(sequence_id, frame_id, overlay_type, label)),
            "object_id": _text(raw.get("object_id") or raw.get("overlay_id") or stable_overlay_id(sequence_id, overlay_type, label)),
            "track_id": _text(raw.get("track_id") or raw.get("object_id") or stable_overlay_id(sequence_id, overlay_type, label)),
            "type": overlay_type,
            "side": side,
            "source_agent": _text(raw.get("source_agent") or source_agent),
            "frame_id": frame_id if frame_id is not None else raw.get("frame_id", 0),
            "sequence_id": _text(sequence_id or raw.get("sequence_id") or "sequence_pending"),
            "chart_transform_id": _text(chart_transform_id or raw.get("chart_transform_id")),
            "coordinate_mode": _text(raw.get("coordinate_mode") or "CHART_IMAGE_SPACE"),
            "anchor_type": _text(raw.get("anchor_type") or "BOX"),
            "bounds": [round(float(value), 4) for value in bounds],
            "bbox": [round(float(value), 4) for value in bounds],
            "truth_score": truth_score,
            "confidence": confidence,
            "lifecycle_state": _text(raw.get("lifecycle_state") or "ACTIVE").upper(),
            "visible_modes": list(raw.get("visible_modes") or ["CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "PREDICTION", "DEBUG", "INSPECTOR"]),
            "ttl_ms": int(_float(raw.get("ttl_ms"), 30000.0)),
            "reason": _text(raw.get("reason") or raw.get("message") or f"{overlay_type} from tracked market object"),
            "label": label,
            "layer": _text(raw.get("layer") or _contract_type_layer_map.get(overlay_type, "diagnostics")),
            "role": _text(raw.get("role") or _contract_type_role_map.get(overlay_type, "")),
            "visible_default": bool(raw.get("visible_default", True)),
        }

    def validate_overlay_payload(overlays: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        rows = [_mapping(row) for row in overlays]
        required = {"overlay_id", "object_id", "track_id", "type", "bounds", "confidence", "truth_score"}
        errors: list[dict[str, Any]] = [
            {"object_id": str(row.get("object_id") or ""), "errors": sorted(required - set(row.keys()))}
            for row in rows
            if required - set(row.keys())
        ]
        return {"schema_version": "PG_V3_OVERLAY_CONTRACT_AUDIT", "ok": not errors, "count": len(rows), "errors": errors, "fallback_contract": True}


V3_OVERLAY_SCHEMA_VERSION = _contract_overlay_schema_version
TYPE_LAYER_MAP = _contract_type_layer_map
TYPE_ROLE_MAP = _contract_type_role_map
TRACKER_SCHEMA_VERSION = "PG_MARKET_OBJECT_TRACKER_V3"
OVERLAY_SCHEMA_VERSION = V3_OVERLAY_SCHEMA_VERSION
SEQUENCE_CONTEXT_SCHEMA_VERSION = "PG_SEQUENCE_CONTEXT_V3"
MARKET_OBJECT_REGISTRY_SCHEMA_VERSION = TRACKER_SCHEMA_VERSION


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def _point_rows(value: Any) -> list[list[float]]:
    points: list[list[float]] = []
    for item in _sequence(value):
        point = _sequence(item)
        if len(point) < 2:
            continue
        x_value = _float(point[0], float("nan"))
        y_value = _float(point[1], float("nan"))
        if x_value == x_value and y_value == y_value:
            points.append([round(float(x_value), 6), round(float(y_value), 6)])
    return points


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _float(value, 0.0)))


def _upper_side(value: Any, default: str = "HOLD") -> str:
    side = str(value or default).strip().upper()
    return side if side in {"BUY", "SELL", "HOLD"} else default


def _frame_id(payload: Mapping[str, Any]) -> int:
    return int(_float(payload.get("frame_index", payload.get("capture_count", 0)), 0.0))


def _session_id(payload: Mapping[str, Any]) -> str:
    return _text(payload.get("session_id"), "session")


def _stable_id(session_id: str, object_type: str, source_path: str, source_key: Any = "") -> str:
    digest = hashlib.sha1(f"{session_id}|{object_type}|{source_path}|{source_key}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"mobj_{digest}"


def _sequence_id(session_id: str, frame_id: int, tracking: Mapping[str, Any], signal: Mapping[str, Any]) -> str:
    digest = hashlib.sha1(
        "|".join(
            str(value or "")
            for value in (
                session_id,
                tracking.get("global_direction"),
                tracking.get("local_direction"),
                tracking.get("impulse_direction"),
                signal.get("entry_state"),
                signal.get("setup"),
            )
        ).encode("utf-8", errors="ignore")
    ).hexdigest()[:14]
    return f"seq_{digest}"


def _chart_transform_id(payload: Mapping[str, Any], tracking: Mapping[str, Any]) -> str:
    for source in (tracking.get("chart_transform"), tracking.get("overlay_geometry"), payload.get("chart_transform")):
        item = _mapping(source)
        text = _text(item.get("chart_transform_id") or item.get("id"))
        if text:
            return text
    return f"ct_{_session_id(payload)}_{_frame_id(payload)}"


def _raw_bbox(raw: Mapping[str, Any]) -> list[float] | None:
    # Rows derived from a parent structure often carry parent ``bounds`` plus a
    # specific child ``bbox``. The child crop must win or replay/trigger boxes
    # expand back into the full parent leg.
    for key in ("bbox", "bounds", "pixel_bbox", "box", "rect"):
        bbox = normalize_bounds(raw.get(key))
        if bbox is not None:
            return bbox
    for key in ("target_bbox", "sniper_window", "trigger_window", "target_window"):
        bbox = normalize_bounds(raw.get(key))
        if bbox is not None:
            return bbox
    for key in ("line_points", "points", "anchors", "path"):
        bbox = normalize_bounds(raw.get(key))
        if bbox is not None:
            return bbox
    return None


def _anchor_indices_from_raw(raw: Mapping[str, Any]) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for key in ("anchor_candle_indices", "anchor_candles", "source_indices", "candle_indices", "indices"):
        for item in _sequence(raw.get(key)):
            index = int(_float(item, -1.0))
            if index < 0 or index in seen:
                continue
            output.append(index)
            seen.add(index)
    return output


def _box_intersects(first: Sequence[float], second: Sequence[float], *, padding: float = 0.0) -> bool:
    pad = max(0.0, float(padding))
    return not (
        first[2] < second[0] - pad
        or first[0] > second[2] + pad
        or first[3] < second[1] - pad
        or first[1] > second[3] + pad
    )


def _bbox_anchor_candidates(raw: Mapping[str, Any], primary_bbox: Sequence[float]) -> list[list[float]]:
    candidates: list[list[float]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for value in (
        list(primary_bbox[:4]),
        raw.get("source_bbox"),
        raw.get("parent_bbox"),
        raw.get("anchor_bbox"),
        raw.get("sequence_bbox"),
        raw.get("trigger_window"),
        raw.get("sniper_window"),
        raw.get("bbox"),
        raw.get("bounds"),
    ):
        bounds = normalize_bounds(value)
        if bounds is None:
            continue
        key: tuple[float, float, float, float] = (
            round(float(bounds[0]), 3),
            round(float(bounds[1]), 3),
            round(float(bounds[2]), 3),
            round(float(bounds[3]), 3),
        )
        if key in seen:
            continue
        candidates.append([float(item) for item in bounds[:4]])
        seen.add(key)
    return candidates


def _candle_anchor_evidence(
    raw: Mapping[str, Any],
    primary_bbox: Sequence[float],
    candles: Sequence[Mapping[str, Any]],
) -> tuple[list[int], list[list[float]]]:
    explicit = _anchor_indices_from_raw(raw)
    candidates = _bbox_anchor_candidates(raw, primary_bbox)
    points: list[list[float]] = []
    seen_indexes: set[int] = set(explicit)
    indexes = list(explicit)
    for candle_index, candle in enumerate(candles):
        candle_box = _raw_bbox(candle)
        if candle_box is None:
            continue
        candle_center_x = _float(candle.get("center_x"), (candle_box[0] + candle_box[2]) * 0.5)
        candle_center_y = _float(candle.get("center_y"), (candle_box[1] + candle_box[3]) * 0.5)
        touches = any(_box_intersects(candidate, candle_box, padding=8.0) for candidate in candidates)
        if not touches:
            continue
        if candle_index not in seen_indexes:
            indexes.append(candle_index)
            seen_indexes.add(candle_index)
        point = [round(float(candle_center_x), 3), round(float(candle_center_y), 3)]
        if point not in points:
            points.append(point)
    return indexes, points


CRITICAL_ANCHORED_OVERLAY_TYPES: frozenset[str] = frozenset(
    {
        "SUPPLY_ZONE",
        "DEMAND_ZONE",
        "OPPOSING_FORCE",
        "SNIPER_ENTRY_BOX",
        "RETEST_BOX",
        "CONTINUATION_BOX",
        "TARGET_ZONE_BOX",
        "INVALIDATION_BOX",
    }
)
ZONE_OVERLAY_TYPES: frozenset[str] = frozenset({"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"})
ACTIONABLE_OVERLAY_TYPES: frozenset[str] = frozenset(
    {"SNIPER_ENTRY_BOX", "RETEST_BOX", "CONTINUATION_BOX", "TARGET_ZONE_BOX", "INVALIDATION_BOX"}
)
HISTORICAL_OVERLAY_TYPES: frozenset[str] = frozenset({"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"})
TRENDLINE_OVERLAY_TYPES: frozenset[str] = frozenset({"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"})


def _candle_anchor_row(candle: Mapping[str, Any], index: int) -> dict[str, float | int]:
    box = _raw_bbox(candle) or [0.0, 0.0, 1.0, 1.0]
    left, top, right, bottom = [float(value) for value in box[:4]]
    open_y = _float(candle.get("open_y", candle.get("open_px", candle.get("open_price_y"))), float("nan"))
    close_y = _float(candle.get("close_y", candle.get("close_px", candle.get("close_price_y"))), float("nan"))
    if open_y == open_y and close_y == close_y:
        body_top = min(open_y, close_y)
        body_bottom = max(open_y, close_y)
    else:
        body_top = _float(candle.get("body_top", candle.get("body_y0")), top + (bottom - top) * 0.24)
        body_bottom = _float(candle.get("body_bottom", candle.get("body_y1")), bottom - (bottom - top) * 0.24)
        if body_bottom < body_top:
            body_top, body_bottom = body_bottom, body_top
    wick_high = _float(
        candle.get("wick_high_y", candle.get("high_y", candle.get("high_px", candle.get("top_y")))),
        top,
    )
    wick_low = _float(
        candle.get("wick_low_y", candle.get("low_y", candle.get("low_px", candle.get("bottom_y")))),
        bottom,
    )
    center_x = _float(candle.get("center_x", candle.get("x_center")), (left + right) * 0.5)
    center_y = _float(candle.get("center_y", candle.get("y_center")), (top + bottom) * 0.5)
    return {
        "index": int(index),
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "center_x": center_x,
        "center_y": center_y,
        "body_top": float(max(top, min(bottom, body_top))),
        "body_bottom": float(max(top, min(bottom, body_bottom))),
        "wick_high": float(max(top, min(bottom, wick_high))),
        "wick_low": float(max(top, min(bottom, wick_low))),
        "width": max(1.0, right - left),
        "height": max(1.0, bottom - top),
    }


def _candle_rows_for_anchor(
    candles: Sequence[Mapping[str, Any]],
    anchor_indices: Sequence[int],
    fallback_bbox: Sequence[float],
) -> list[dict[str, float | int]]:
    selected: list[dict[str, float | int]] = []
    seen: set[int] = set()
    for raw_index in anchor_indices:
        index = int(raw_index)
        if index < 0 or index >= len(candles) or index in seen:
            continue
        selected.append(_candle_anchor_row(candles[index], index))
        seen.add(index)
    if selected:
        fallback = normalize_bounds(fallback_bbox)
        if fallback is None:
            return selected
        aligned = [
            row
            for row in selected
            if float(row.get("right", 0.0)) >= fallback[0] - 12.0
            and float(row.get("left", 0.0)) <= fallback[2] + 12.0
        ]
        if aligned:
            return aligned
        selected = []
    fallback = normalize_bounds(fallback_bbox)
    if fallback is None:
        return []
    for index, candle in enumerate(candles):
        candle_box = _raw_bbox(candle)
        if candle_box is None:
            continue
        if _box_intersects(fallback, candle_box, padding=5.0):
            selected.append(_candle_anchor_row(candle, index))
    return selected


def _point_from_raw(value: Any) -> list[float] | None:
    point = _sequence(value)
    if len(point) < 2:
        return None
    x_value = _float(point[0], float("nan"))
    y_value = _float(point[1], float("nan"))
    if x_value != x_value or y_value != y_value:
        return None
    return [float(x_value), float(y_value)]


def _explicit_anchor_points(raw: Mapping[str, Any], *, overlay_type: str) -> list[list[float]]:
    points: list[list[float]] = []
    seen: set[tuple[float, float]] = set()
    for key in ("touch_points", "anchor_wick_points", "line_points", "points", "path"):
        for point in _point_rows(raw.get(key)):
            rounded = (round(point[0], 3), round(point[1], 3))
            if rounded in seen:
                continue
            seen.add(rounded)
            points.append([rounded[0], rounded[1]])
    type_name = str(overlay_type or "").upper()
    if type_name in {"REPLAY_ENTRY", "REPLAY_EXIT"}:
        preferred_keys = ("end_point", "target_point") if type_name == "REPLAY_EXIT" else ("start_point", "entry_point", "sniper_point")
        for key in preferred_keys:
            point = _point_from_raw(raw.get(key))
            if point is None:
                continue
            rounded = (round(point[0], 3), round(point[1], 3))
            if rounded in seen:
                continue
            seen.add(rounded)
            points.insert(0, [rounded[0], rounded[1]])
            break
    return points


def _nearest_row_for_point(rows: Sequence[Mapping[str, float | int]], point: Sequence[float]) -> dict[str, float | int] | None:
    if not rows or len(point) < 2:
        return None
    px = float(point[0])
    py = float(point[1])

    def score(row: Mapping[str, float | int]) -> tuple[float, float]:
        left = float(row.get("left", 0.0))
        right = float(row.get("right", left))
        top = float(row.get("top", 0.0))
        bottom = float(row.get("bottom", top))
        center_x = float(row.get("center_x", (left + right) * 0.5))
        center_y = float(row.get("center_y", (top + bottom) * 0.5))
        x_distance = 0.0 if left - 8.0 <= px <= right + 8.0 else abs(center_x - px)
        y_distance = 0.0 if top - 10.0 <= py <= bottom + 10.0 else min(abs(top - py), abs(bottom - py), abs(center_y - py))
        return (x_distance + y_distance * 0.45, x_distance)

    return dict(min(rows, key=score))


def _rows_around_indices(
    all_rows: Sequence[Mapping[str, float | int]],
    selected_indices: Sequence[int],
    *,
    radius: int,
    max_rows: int,
) -> list[dict[str, float | int]]:
    if not all_rows:
        return []
    selected: dict[int, dict[str, float | int]] = {}
    available = {int(row.get("index", index)): dict(row) for index, row in enumerate(all_rows)}
    for raw_index in selected_indices:
        for index in range(int(raw_index) - radius, int(raw_index) + radius + 1):
            row = available.get(index)
            if row is not None:
                selected[index] = row
    rows = [selected[index] for index in sorted(selected)]
    if len(rows) <= max_rows:
        return rows
    center = sum(selected_indices) / max(1, len(selected_indices))
    return sorted(rows, key=lambda row: abs(float(row.get("index", 0)) - center))[:max_rows]


def _rows_from_anchor_points(
    all_rows: Sequence[Mapping[str, float | int]],
    points: Sequence[Sequence[float]],
    source_box: Sequence[float],
    *,
    prefer_recent: bool,
    max_points: int,
    neighbor_radius: int,
    max_rows: int,
) -> list[dict[str, float | int]]:
    if not all_rows or not points:
        return []
    left = min(float(row.get("left", 0.0)) for row in all_rows)
    right = max(float(row.get("right", 0.0)) for row in all_rows)
    visible_points = [
        [float(point[0]), float(point[1])]
        for point in points
        if len(point) >= 2 and left - 12.0 <= float(point[0]) <= right + 12.0
    ]
    if not visible_points:
        return []
    scoped = [
        point
        for point in visible_points
        if float(source_box[0]) - 18.0 <= point[0] <= float(source_box[2]) + 18.0
    ] or visible_points
    ordered = sorted(scoped, key=lambda point: point[0], reverse=prefer_recent)
    selected_points = ordered[: max(1, int(max_points))]
    selected_indexes: list[int] = []
    seen_indexes: set[int] = set()
    for point in selected_points:
        row = _nearest_row_for_point(all_rows, point)
        if row is None:
            continue
        index = int(row.get("index", -1))
        if index < 0 or index in seen_indexes:
            continue
        selected_indexes.append(index)
        seen_indexes.add(index)
    return _rows_around_indices(
        all_rows,
        selected_indexes,
        radius=max(0, int(neighbor_radius)),
        max_rows=max(1, int(max_rows)),
    )


def _recent_row_cluster(
    rows: Sequence[Mapping[str, float | int]],
    source_box: Sequence[float],
    *,
    max_rows: int,
) -> list[dict[str, float | int]]:
    if len(rows) <= max_rows:
        return [dict(row) for row in rows]
    anchor_x = float(source_box[2])
    ranked = sorted(rows, key=lambda row: abs(float(row.get("center_x", 0.0)) - anchor_x))[:max_rows]
    return sorted((dict(row) for row in ranked), key=lambda row: float(row.get("center_x", 0.0)))


def _select_anchor_rows_for_overlay(
    *,
    overlay_type: str,
    raw: Mapping[str, Any],
    source_box: Sequence[float],
    candles: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, float | int]],
) -> list[dict[str, float | int]]:
    type_name = str(overlay_type or "").upper()
    all_rows = [_candle_anchor_row(candle, index) for index, candle in enumerate(candles)]
    if not all_rows:
        return [dict(row) for row in rows]
    explicit_points = _explicit_anchor_points(raw, overlay_type=type_name)
    if type_name in ZONE_OVERLAY_TYPES and explicit_points:
        point_rows = _rows_from_anchor_points(
            all_rows,
            explicit_points,
            source_box,
            prefer_recent=True,
            max_points=5,
            neighbor_radius=1,
            max_rows=7,
        )
        if point_rows:
            return point_rows
    if type_name in {"REPLAY_ENTRY", "REPLAY_EXIT"}:
        preferred = _explicit_anchor_points(raw, overlay_type=type_name)
        point_rows = _rows_from_anchor_points(
            all_rows,
            preferred,
            source_box,
            prefer_recent=type_name == "REPLAY_EXIT",
            max_points=1,
            neighbor_radius=1,
            max_rows=3,
        )
        if point_rows:
            return point_rows
    if type_name in ACTIONABLE_OVERLAY_TYPES:
        return _recent_row_cluster(rows, source_box, max_rows=6)
    if type_name in ZONE_OVERLAY_TYPES:
        return _recent_row_cluster(rows, source_box, max_rows=7)
    return [dict(row) for row in rows]


def _median(values: Sequence[float], default: float = 0.0) -> float:
    rows = sorted(float(value) for value in values if value == value)
    if not rows:
        return float(default)
    mid = len(rows) // 2
    if len(rows) % 2:
        return rows[mid]
    return (rows[mid - 1] + rows[mid]) * 0.5


def _raw_chart_extent(candles: Sequence[Mapping[str, Any]], fallback_bbox: Sequence[float]) -> tuple[float, float, float, float]:
    boxes = [_raw_bbox(candle) for candle in candles]
    valid = [box for box in boxes if box is not None]
    fallback = normalize_bounds(fallback_bbox) or [0.0, 0.0, 1.0, 1.0]
    if not valid:
        return (fallback[0], fallback[1], fallback[2], fallback[3])
    left = min([fallback[0], *[float(box[0]) for box in valid]])
    top = min([fallback[1], *[float(box[1]) for box in valid]])
    right = max([fallback[2], *[float(box[2]) for box in valid]])
    bottom = max([fallback[3], *[float(box[3]) for box in valid]])
    return (left, top, right, bottom)


def _tighten_bbox_to_anchor_rows(
    *,
    object_type: str,
    raw: Mapping[str, Any],
    bbox: Sequence[float],
    candles: Sequence[Mapping[str, Any]],
    anchor_indices: Sequence[int],
) -> tuple[list[float], dict[str, Any]]:
    source_box = normalize_bounds(bbox) or [float(value) for value in bbox[:4]]
    if len(source_box) < 4:
        return list(source_box), {"score": 0.0, "status": "INVALID", "reason": "invalid_source_bbox"}
    rows = _candle_rows_for_anchor(candles, anchor_indices, source_box)
    chart_left, chart_top, chart_right, chart_bottom = _raw_chart_extent(candles, source_box)
    chart_width = max(1.0, chart_right - chart_left)
    chart_height = max(1.0, chart_bottom - chart_top)
    type_name = str(object_type or "").upper()
    raw_anchor_count = len(rows)
    rows = _select_anchor_rows_for_overlay(
        overlay_type=type_name,
        raw=raw,
        source_box=source_box,
        candles=candles,
        rows=rows,
    )
    anchor_count = len(rows)
    if not rows:
        reason = "missing_candle_anchor_rows"
        score = 0.20 if type_name in CRITICAL_ANCHORED_OVERLAY_TYPES else 0.48
        return [round(float(value), 3) for value in source_box], {
            "score": score,
            "status": "WEAK" if score >= 0.40 else "REJECT",
            "reason": reason,
            "anchor_count": 0,
            "tightened": False,
        }
    widths = [float(row["width"]) for row in rows]
    heights = [float(row["height"]) for row in rows]
    avg_width = max(3.0, _median(widths, 6.0))
    avg_height = max(6.0, _median(heights, 18.0))
    xs_left = [float(row["left"]) for row in rows]
    xs_right = [float(row["right"]) for row in rows]
    wick_highs = [float(row["wick_high"]) for row in rows]
    wick_lows = [float(row["wick_low"]) for row in rows]
    body_tops = [float(row["body_top"]) for row in rows]
    body_bottoms = [float(row["body_bottom"]) for row in rows]
    centers_x = [float(row["center_x"]) for row in rows]
    centers_y = [float(row["center_y"]) for row in rows]
    pad_x = max(4.0, min(24.0, avg_width * 0.75))
    pad_y = max(2.0, min(12.0, avg_height * 0.22))
    selected_anchor_indices = [int(row["index"]) for row in rows]
    selected_anchor_points = [[round(float(row["center_x"]), 3), round(float(row["center_y"]), 3)] for row in rows]
    explicit_point_anchor = bool(_explicit_anchor_points(raw, overlay_type=type_name))
    snapped_to_local_cluster = anchor_count > 0 and (
        raw_anchor_count > anchor_count
        or (type_name in ZONE_OVERLAY_TYPES and explicit_point_anchor)
        or (type_name in {"REPLAY_ENTRY", "REPLAY_EXIT"} and explicit_point_anchor)
    )
    left = min(xs_left) - pad_x
    right = max(xs_right) + pad_x
    top = min(wick_highs) - pad_y
    bottom = max(wick_lows) + pad_y
    if type_name in {"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"}:
        if type_name == "SUPPLY_ZONE":
            top = min(wick_highs) - pad_y
            bottom = max(body_tops) + pad_y
        elif type_name == "DEMAND_ZONE":
            top = min(body_bottoms) - pad_y
            bottom = max(wick_lows) + pad_y
        else:
            raw_center_y = (source_box[1] + source_box[3]) * 0.5
            nearest_center = min(centers_y, key=lambda value: abs(value - raw_center_y))
            top = nearest_center - max(6.0, avg_height * 0.42)
            bottom = nearest_center + max(6.0, avg_height * 0.42)
        raw_left, _raw_top, raw_right, _raw_bottom = source_box
        if not snapped_to_local_cluster:
            # Preserve a reasonable validity horizon without turning the zone into a chart-wide slab.
            left = min(left, max(chart_left, raw_left))
            right = max(right, min(raw_right, left + max(48.0, avg_width * 8.0)))
        max_zone_width = max(44.0, chart_width * (0.18 if snapped_to_local_cluster else 0.34))
        if right - left > max_zone_width:
            anchor_x = max(centers_x)
            right = min(chart_right, anchor_x + max_zone_width * 0.40)
            left = max(chart_left, right - max_zone_width)
        min_zone_height = max(8.0, avg_height * 0.35)
        max_zone_height = max(min_zone_height, chart_height * 0.090)
        center_y = (top + bottom) * 0.5
        zone_height = min(max(min_zone_height, bottom - top), max_zone_height)
        top = center_y - zone_height * 0.5
        bottom = center_y + zone_height * 0.5
    elif type_name in {"SNIPER_ENTRY_BOX", "RETEST_BOX"}:
        anchor_x = max(centers_x)
        anchor_y = _median(centers_y, (source_box[1] + source_box[3]) * 0.5)
        width = min(max(36.0, avg_width * 6.0), chart_width * 0.22)
        height = min(max(12.0, avg_height * 1.20), chart_height * 0.075)
        left = anchor_x - width * 0.45
        right = anchor_x + width * 0.55
        top = anchor_y - height * 0.5
        bottom = anchor_y + height * 0.5
    elif type_name in {"TARGET_ZONE_BOX", "INVALIDATION_BOX"}:
        raw_center_y = (source_box[1] + source_box[3]) * 0.5
        anchor_y = _median(centers_y, raw_center_y)
        line_y = _float(raw.get("line_y"), raw_center_y)
        if abs(line_y - raw_center_y) < chart_height * 0.35:
            anchor_y = line_y
        width = min(max(44.0, avg_width * 8.0), chart_width * 0.30)
        height = min(max(7.0, avg_height * 0.58), chart_height * 0.042)
        anchor_x = max(centers_x)
        left = max(chart_left, anchor_x - width * 0.72)
        right = min(chart_right, left + width)
        top = anchor_y - height * 0.5
        bottom = anchor_y + height * 0.5
    elif type_name in {"IMPULSE_BOX", "PULLBACK_BOX", "CONTINUATION_BOX"}:
        min_width = max(42.0, avg_width * max(3.0, min(9.0, anchor_count + 1.0)))
        max_width = max(min_width, chart_width * (0.48 if type_name == "IMPULSE_BOX" else 0.34))
        min_height = max(18.0, avg_height * 1.6)
        max_height = max(min_height, chart_height * (0.34 if type_name == "IMPULSE_BOX" else 0.24))
        if right - left < min_width:
            center_x = (left + right) * 0.5
            left = center_x - min_width * 0.5
            right = center_x + min_width * 0.5
        if right - left > max_width:
            anchor_x = max(centers_x)
            right = min(chart_right, anchor_x + max_width * 0.25)
            left = right - max_width
        if bottom - top < min_height:
            center_y = (top + bottom) * 0.5
            top = center_y - min_height * 0.5
            bottom = center_y + min_height * 0.5
        if bottom - top > max_height:
            center_y = (top + bottom) * 0.5
            top = center_y - max_height * 0.5
            bottom = center_y + max_height * 0.5
    elif type_name in {"REPLAY_ENTRY", "REPLAY_EXIT"}:
        source_width = max(1.0, source_box[2] - source_box[0])
        source_height = max(1.0, source_box[3] - source_box[1])
        anchor_x = _median(centers_x, (source_box[0] + source_box[2]) * 0.5)
        source_center_y = (source_box[1] + source_box[3]) * 0.5
        anchor_y = source_center_y
        width = min(max(30.0, avg_width * 4.0), max(30.0, source_width), chart_width * 0.14)
        height = min(max(10.0, min(source_height, avg_height * 0.62)), chart_height * 0.055)
        left = anchor_x - width * 0.5
        right = anchor_x + width * 0.5
        top = anchor_y - height * 0.5
        bottom = anchor_y + height * 0.5
    else:
        # General overlays: tighten only if the source box is much larger than the actual candle cluster.
        cluster_left = min(xs_left) - pad_x
        cluster_right = max(xs_right) + pad_x
        cluster_top = min(wick_highs) - pad_y
        cluster_bottom = max(wick_lows) + pad_y
        source_area = max(1.0, (source_box[2] - source_box[0]) * (source_box[3] - source_box[1]))
        cluster_area = max(1.0, (cluster_right - cluster_left) * (cluster_bottom - cluster_top))
        if cluster_area < source_area * 0.68:
            left, top, right, bottom = cluster_left, cluster_top, cluster_right, cluster_bottom
    left = max(chart_left, min(chart_right, left))
    right = max(chart_left, min(chart_right, right))
    top = max(chart_top, min(chart_bottom, top))
    bottom = max(chart_top, min(chart_bottom, bottom))
    if right <= left or bottom <= top:
        return [round(float(value), 3) for value in source_box], {
            "score": 0.35,
            "status": "WEAK",
            "reason": "anchor_refinement_collapsed",
            "anchor_count": anchor_count,
            "tightened": False,
        }
    refined = [round(float(left), 3), round(float(top), 3), round(float(right), 3), round(float(bottom), 3)]
    source_area = max(1.0, (source_box[2] - source_box[0]) * (source_box[3] - source_box[1]))
    refined_area = max(1.0, (refined[2] - refined[0]) * (refined[3] - refined[1]))
    floating_risk = max(0.0, min(1.0, 1.0 - min(1.0, anchor_count / 3.0)))
    if refined_area > source_area * 1.40 and type_name in CRITICAL_ANCHORED_OVERLAY_TYPES:
        floating_risk = min(1.0, floating_risk + 0.20)
    score = 0.50 + min(0.30, anchor_count * 0.08) + (0.10 if refined_area <= source_area * 1.05 else 0.0) - floating_risk * 0.18
    score = max(0.0, min(1.0, score))
    return refined, {
        "score": round(float(score), 4),
        "status": "VALID" if score >= 0.65 else "WEAK",
        "reason": "candle_wick_sequence_anchor",
        "anchor_count": anchor_count,
        "tightened": refined != [round(float(value), 3) for value in source_box],
        "selected_anchor_indices": selected_anchor_indices,
        "selected_anchor_points": selected_anchor_points,
        "local_cluster_snap": snapped_to_local_cluster,
        "source_area": round(float(source_area), 3),
        "refined_area": round(float(refined_area), 3),
        "floating_risk": round(float(floating_risk), 4),
    }




def _short_display_label_for_overlay(object_type: str, side: str, fallback: str) -> str:
    type_name = str(object_type or "").upper()
    side_name = str(side or "").upper()
    if type_name == "SNIPER_ENTRY_BOX":
        return f"SNIPER {side_name}" if side_name in {"BUY", "SELL"} else "SNIPER"
    if type_name == "RETEST_BOX":
        return "TRIGGER"
    if type_name == "TARGET_ZONE_BOX":
        return "TARGET"
    if type_name == "INVALIDATION_BOX":
        return "INVALID"
    if type_name == "SUPPLY_ZONE":
        return "SUPPLY"
    if type_name == "DEMAND_ZONE":
        return "DEMAND"
    if type_name == "OPPOSING_FORCE":
        return "OPPOSING"
    if type_name == "SUPPORT_TRENDLINE":
        return "SUPPORT TREND"
    if type_name == "RESISTANCE_TRENDLINE":
        return "RESISTANCE TREND"
    if type_name == "INNER_TRENDLINE":
        return "INNER TREND"
    if type_name == "IMPULSE_BOX":
        return "IMPULSE"
    if type_name == "PULLBACK_BOX":
        return "PULLBACK"
    if type_name == "CONTINUATION_BOX":
        return "CONT"
    if type_name == "CURRENT_CANDLE":
        return "NOW"
    if type_name == "REPLAY_ENTRY":
        return "ENTRY"
    if type_name == "REPLAY_EXIT":
        return "EXIT"
    if type_name == "PROGRESSION_PATH":
        return "HISTORY"
    return str(fallback or type_name.replace("_", " ")).strip()

def _display_profile_for_overlay(
    *,
    object_type: str,
    layer: str,
    lifecycle_state: str,
    anchor_quality: Mapping[str, Any],
    confidence: float,
    truth_score: float,
) -> dict[str, Any]:
    type_name = str(object_type or "").upper()
    layer_name = str(layer or "").lower()
    lifecycle = str(lifecycle_state or "").upper()
    score = _clip01(anchor_quality.get("score", 0.0))
    historical = type_name in HISTORICAL_OVERLAY_TYPES or lifecycle in {"HISTORICAL", "REPLAY", "ARCHIVED"} or layer_name == "historical_replay"
    rejected = bool(anchor_quality.get("status") == "REJECT")
    if rejected:
        display_state = "GHOSTED"
        label_hidden = True
        visual_weight = 0.18
        visible_modes = ["DIAGNOSTICS", "INSPECTOR"]
    elif historical:
        display_state = "GHOSTED"
        label_hidden = True
        visual_weight = 0.28
        visible_modes = ["CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"]
    elif type_name in {"SNIPER_ENTRY_BOX", "RETEST_BOX", "TARGET_ZONE_BOX", "INVALIDATION_BOX", "CURRENT_CANDLE"}:
        display_state = "FULL" if score >= 0.62 else "COMPACT"
        label_hidden = False if type_name in {"SNIPER_ENTRY_BOX", "RETEST_BOX", "TARGET_ZONE_BOX", "INVALIDATION_BOX"} else True
        visual_weight = max(0.62, min(1.0, 0.58 + score * 0.28 + max(confidence, truth_score) * 0.14))
        visible_modes = ["CLEAN_LIVE", "ACTIVE_CONTEXT", "LOCAL", "TRIGGERS", "TARGETS", "FULL_HISTORY_READ", "INSPECTOR"]
    elif type_name in {"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"}:
        display_state = "COMPACT" if score >= 0.62 else "GHOSTED"
        label_hidden = score < 0.72
        visual_weight = max(0.42, min(0.76, 0.36 + score * 0.28 + max(confidence, truth_score) * 0.10))
        visible_modes = ["CLEAN_LIVE", "ACTIVE_CONTEXT", "SUPPLY_DEMAND", "SMC_COUNCIL", "FULL_HISTORY_READ", "INSPECTOR"]
    elif type_name in TRENDLINE_OVERLAY_TYPES:
        display_state = "COMPACT"
        label_hidden = True
        visual_weight = max(0.34, min(0.72, 0.30 + score * 0.28 + max(confidence, truth_score) * 0.10))
        visible_modes = ["CLEAN_LIVE", "ACTIVE_CONTEXT", "TRENDLINES", "FULL_HISTORY_READ", "INSPECTOR"]
    else:
        display_state = "NESTED" if score >= 0.58 else "GHOSTED"
        label_hidden = True
        visual_weight = max(0.30, min(0.68, 0.28 + score * 0.25 + max(confidence, truth_score) * 0.08))
        visible_modes = ["ACTIVE_CONTEXT", "LOCAL", "MAJOR", "FULL_HISTORY_READ", "INSPECTOR"]
    semantic_family = "context"
    if type_name in {"DEMAND_ZONE", "SUPPORT_TRENDLINE"}:
        semantic_family = "demand"
    elif type_name in {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"}:
        semantic_family = "supply"
    elif type_name == "SNIPER_ENTRY_BOX":
        semantic_family = "buy" if truth_score >= 0.0 else "entry"
    elif type_name == "TARGET_ZONE_BOX":
        semantic_family = "target"
    elif type_name == "INVALIDATION_BOX":
        semantic_family = "invalidation"
    elif type_name == "OPPOSING_FORCE":
        semantic_family = "opposing"
    elif type_name in HISTORICAL_OVERLAY_TYPES:
        semantic_family = "history"
    style = {
        "visual_weight": round(float(visual_weight), 4),
        "semantic_family": semantic_family,
        "anchor_evidence_status": str(anchor_quality.get("status", "UNKNOWN")),
        "opacity": round(float(max(0.14, min(0.96, visual_weight))), 4),
        "fill_opacity": round(float(max(0.0, min(0.10, visual_weight * 0.045))), 4),
        "border_width": round(float(max(0.75, min(2.5, 0.75 + visual_weight * 1.1))), 3),
    }
    return {
        "display_state": display_state,
        "label_hidden": label_hidden,
        "label_visible": not label_hidden,
        "label_anchor": "inspector" if label_hidden else "overlay",
        "geometry_visible": True,
        "inspector_visible": True,
        "visual_weight": round(float(visual_weight), 4),
        "semantic_family": semantic_family,
        "style": style,
        "visible_modes": visible_modes,
        "precision_rejected": rejected,
    }


def _bool_false(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "off"}
    return False


def _zone_role(zone: Mapping[str, Any]) -> str:
    role = str(
        zone.get("role")
        or zone.get("zone_role")
        or zone.get("zone_family")
        or zone.get("type")
        or zone.get("kind")
        or zone.get("label")
        or ""
    ).strip().lower()
    if "support" in role or "demand" in role:
        return "support"
    if "resistance" in role or "supply" in role:
        return "resistance"
    return ""


def _zone_lifecycle_state(zone: Mapping[str, Any]) -> str:
    explicit = _text(zone.get("lifecycle_state") or zone.get("state")).upper()
    if explicit:
        if explicit in {"FRESH", "TESTED", "ACTIVE", "FRESH_ACTIVE"}:
            return "FRESH_ACTIVE"
        if explicit in {"MITIGATED", "MITIGATED_ACTIVE"}:
            return "MITIGATED_ACTIVE"
        if explicit in {"HISTORICAL", "HISTORICAL_ACTIVE"}:
            return "HISTORICAL_ACTIVE"
        if explicit in {"BROKEN", "BROKEN_REFERENCE"}:
            return "BROKEN_REFERENCE"
        if explicit in {"CONSUMED", "CONSUMED_REFERENCE", "MITIGATED_REFERENCE"}:
            return "CONSUMED_REFERENCE"
        if explicit in {"ROLE_FLIP", "ROLE_FLIP_CONFIRMED"}:
            return "ROLE_FLIP_CONFIRMED"
        if explicit in {"CONTEXT", "REFERENCE", "STALE", "CONTEXT_REFERENCE"}:
            return "CONTEXT_REFERENCE"
    authority = _text(zone.get("zone_authority_state") or zone.get("freshness_state") or zone.get("authority_state")).upper()
    if "ROLE_FLIP" in authority:
        return "ROLE_FLIP_CONFIRMED"
    if "BROKEN" in authority:
        return "BROKEN_REFERENCE"
    if "CONSUMED" in authority:
        return "CONSUMED_REFERENCE"
    if "MITIGATED" in authority:
        return "MITIGATED_ACTIVE" if not _bool_false(zone.get("entry_authority_allowed")) else "CONTEXT_REFERENCE"
    if _bool_false(zone.get("entry_authority_allowed")):
        return "CONTEXT_REFERENCE"
    if "HISTORICAL" in authority:
        return "HISTORICAL_ACTIVE"
    return "FRESH_ACTIVE"


def _active_zone_lifecycle(lifecycle_state: str) -> bool:
    return lifecycle_state in {"FRESH_ACTIVE", "MITIGATED_ACTIVE", "HISTORICAL_ACTIVE", "ACTIVE", "FRESH", "TESTED", "MITIGATED"}


def _point_pair_bounds(points: Sequence[Any]) -> list[float] | None:
    return normalize_bounds(points)


def _first_finite_number(raw: Mapping[str, Any], keys: Sequence[str], default: float) -> float:
    for key in keys:
        if key not in raw:
            continue
        value = _float(raw.get(key), float("nan"))
        if value == value and value not in (float("inf"), -float("inf")):
            return value
    return float(default)


def _bounds_from_first_key(raw: Mapping[str, Any], keys: Sequence[str]) -> list[float] | None:
    for key in keys:
        bounds = normalize_bounds(raw.get(key))
        if bounds is not None:
            return bounds
    return None


def _point_box(point: Any, *, pad: float = 5.0) -> list[float] | None:
    point_values = _sequence(point)
    if len(point_values) < 2:
        return None
    x = _float(point_values[0], float("nan"))
    y = _float(point_values[1], float("nan"))
    if x != x or y != y:
        return None
    return [x - pad, y - pad, x + pad, y + pad]


def _line_y_at(first: Sequence[float], second: Sequence[float], x: float) -> float:
    x0 = float(first[0])
    y0 = float(first[1])
    x1 = float(second[0])
    y1 = float(second[1])
    if abs(x1 - x0) <= 1e-6:
        return (y0 + y1) * 0.5
    ratio = (float(x) - x0) / (x1 - x0)
    return y0 + ratio * (y1 - y0)


def _candle_line_rows(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for index, candle in enumerate(candles):
        bbox = _raw_bbox(candle)
        if bbox is None:
            continue
        left, box_top, right, box_bottom = [float(value) for value in bbox[:4]]
        body_box = _bounds_from_first_key(candle, ("body_bbox", "body_bounds", "body_pixel_bbox", "body_box"))
        body_top = float(body_box[1]) if body_box else box_top
        body_bottom = float(body_box[3]) if body_box else box_bottom
        wick_box = _bounds_from_first_key(candle, ("wick_bbox", "wick_bounds", "wick_pixel_bbox", "wick_box"))
        wick_top_default = float(wick_box[1]) if wick_box else box_top
        wick_bottom_default = float(wick_box[3]) if wick_box else box_bottom
        wick_top = _first_finite_number(
            candle,
            (
                "wick_top",
                "wick_y1",
                "high_y",
                "top_wick_y",
                "upper_wick_y",
                "upper_wick_top",
                "high_price_y",
                "wick_high",
            ),
            wick_top_default,
        )
        wick_bottom = _first_finite_number(
            candle,
            (
                "wick_bottom",
                "wick_y2",
                "low_y",
                "bottom_wick_y",
                "lower_wick_y",
                "lower_wick_bottom",
                "low_price_y",
                "wick_low",
            ),
            wick_bottom_default,
        )
        wick_top = min(wick_top, box_top, body_top)
        wick_bottom = max(wick_bottom, box_bottom, body_bottom)
        center_x = _float(candle.get("center_x"), (left + right) * 0.5)
        center_y = _float(candle.get("center_y"), (body_top + body_bottom) * 0.5)
        rows.append(
            {
                "index": float(index),
                "left": left,
                "top": wick_top,
                "right": right,
                "bottom": wick_bottom,
                "body_top": body_top,
                "body_bottom": body_bottom,
                "center_x": center_x,
                "center_y": center_y,
            }
        )
    rows.sort(key=lambda item: item["center_x"])
    return rows


def _pivot_rows(rows: Sequence[Mapping[str, float]], *, role: str, window: int = 2) -> list[dict[str, float]]:
    pivots: list[dict[str, float]] = []
    if len(rows) < 2:
        return pivots
    for index, row in enumerate(rows):
        left = max(0, index - window)
        right = min(len(rows), index + window + 1)
        neighbors = rows[left:right]
        if role == "support":
            value = float(row.get("bottom", 0.0))
            if value >= max(float(item.get("bottom", 0.0)) for item in neighbors) - 1e-6:
                pivots.append(dict(row))
        elif role == "resistance":
            value = float(row.get("top", 0.0))
            if value <= min(float(item.get("top", 0.0)) for item in neighbors) + 1e-6:
                pivots.append(dict(row))
    if len(pivots) >= 2:
        return pivots
    ranked = sorted(
        rows,
        key=lambda item: float(item.get("bottom" if role == "support" else "top", 0.0)),
        reverse=role == "support",
    )
    for row in ranked:
        if not pivots or abs(float(row.get("center_x", 0.0)) - float(pivots[0].get("center_x", 0.0))) > 8.0:
            pivots.append(dict(row))
        if len(pivots) >= 2:
            break
    return sorted(pivots, key=lambda item: item["center_x"])


def _validated_trendline(
    rows: Sequence[Mapping[str, float]],
    *,
    role: str,
    local_only: bool = False,
) -> dict[str, Any] | None:
    scoped = list(rows[-min(12, len(rows)) :]) if local_only else list(rows)
    if len(scoped) < (5 if local_only else 4):
        return None
    pivots = _pivot_rows(scoped, role=role, window=1 if local_only else 2)
    if len(pivots) < 2:
        return None
    average_range = (
        sum(max(1.0, float(row.get("bottom", 0.0)) - float(row.get("top", 0.0))) for row in scoped)
        / max(1, len(scoped))
    )
    touch_tolerance = max(1.5, min(5.0 if local_only else 6.0, average_range * (0.16 if local_only else 0.20)))
    break_tolerance = max(touch_tolerance * 1.65, average_range * 0.34)
    min_anchor_dx = max(14.0, average_range * (0.55 if local_only else 0.75))
    min_anchor_dy = max(2.0, average_range * (0.12 if local_only else 0.10))
    latest_index = int(scoped[-1]["index"])
    best: dict[str, Any] | None = None
    for first_index in range(0, len(pivots) - 1):
        for second_index in range(first_index + 1, len(pivots)):
            first = pivots[first_index]
            second = pivots[second_index]
            anchor_dx = abs(float(second["center_x"]) - float(first["center_x"]))
            if anchor_dx < min_anchor_dx:
                continue
            first_point = [
                float(first["center_x"]),
                float(first["bottom" if role == "support" else "top"]),
            ]
            second_point = [
                float(second["center_x"]),
                float(second["bottom" if role == "support" else "top"]),
            ]
            anchor_dy = float(second_point[1]) - float(first_point[1])
            if role == "support" and anchor_dy >= -min_anchor_dy:
                continue
            if role == "resistance" and anchor_dy <= min_anchor_dy:
                continue
            anchor_dx_signed = float(second_point[0]) - float(first_point[0])
            slope = 0.0 if abs(anchor_dx_signed) <= 1e-6 else anchor_dy / anchor_dx_signed
            intercept = float(first_point[1]) - slope * float(first_point[0])
            anchor_start = int(min(first["index"], second["index"]))
            anchor_end = int(max(first["index"], second["index"]))
            if anchor_end <= anchor_start:
                continue
            line_obstruction_count = 0
            wick_probe_count = 0
            body_cross_count = 0
            evaluated_after_anchor = 0
            touch_indices = {anchor_start, anchor_end}
            significant_close = False
            for row in scoped:
                row_index = int(row["index"])
                if row_index < anchor_start or row_index > latest_index:
                    continue
                line_y = _line_y_at(first_point, second_point, float(row["center_x"]))
                top = float(row["top"])
                bottom = float(row["bottom"])
                body_top = float(row.get("body_top", top))
                body_bottom = float(row.get("body_bottom", bottom))
                center_y = float(row.get("center_y", (top + bottom) * 0.5))
                if role == "support":
                    wick_distance = bottom - line_y
                    body_break_distance = max(center_y - line_y, body_bottom - line_y)
                else:
                    wick_distance = line_y - top
                    body_break_distance = max(line_y - center_y, line_y - body_top)
                if anchor_start < row_index < anchor_end and wick_distance > touch_tolerance:
                    line_obstruction_count += 1
                    break
                if row_index > anchor_end:
                    evaluated_after_anchor += 1
                    if body_break_distance > break_tolerance:
                        significant_close = True
                        body_cross_count += 1
                        break
                    if wick_distance > touch_tolerance:
                        wick_probe_count += 1
                    if body_break_distance > touch_tolerance:
                        body_cross_count += 1
                if abs(wick_distance) <= touch_tolerance * 1.2:
                    touch_indices.add(row_index)
            if line_obstruction_count or significant_close:
                continue
            last_x = float(scoped[-1]["center_x"])
            latest_row = scoped[-1]
            latest_line_y = _line_y_at(first_point, second_point, last_x)
            latest_center_y = float(latest_row.get("center_y", latest_line_y))
            close_distance_norm = min(9.999, abs(latest_center_y - latest_line_y) / max(1.0, average_range))
            if local_only and close_distance_norm > 2.25:
                continue
            end_point = [last_x, latest_line_y]
            line_points = [first_point, second_point]
            if abs(last_x - float(second_point[0])) > 1e-6:
                line_points.append(end_point)
            touch_rows = [
                row
                for row in scoped
                if int(row["index"]) in touch_indices
            ]
            touch_points = [
                [
                    float(row["center_x"]),
                    float(row["bottom" if role == "support" else "top"]),
                ]
                for row in sorted(touch_rows, key=lambda item: float(item["center_x"]))
            ]
            if len(touch_points) < 2:
                touch_points = [first_point, second_point]
            touches = max(2, len(touch_points))
            anchor_candles = sorted({int(row["index"]) for row in touch_rows} | {anchor_start, anchor_end})
            body_cross_fraction = body_cross_count / max(1, evaluated_after_anchor)
            score = (anchor_dx * 0.012) + touches + (0.75 / max(0.35, close_distance_norm + 0.35) if local_only else 0.0)
            candidate: dict[str, Any] = {
                "role": role,
                "points": line_points,
                "line_points": line_points,
                "touch_points": touch_points,
                "anchor_wick_points": touch_points,
                "anchor_candles": anchor_candles,
                "touch_count": int(touches),
                "slope": round(float(slope), 6),
                "intercept": round(float(intercept), 6),
                "wick_probe_count": int(wick_probe_count),
                "line_obstruction_count": int(line_obstruction_count),
                "body_cross_fraction": round(float(body_cross_fraction), 4),
                "close_distance_norm": round(float(close_distance_norm), 4),
                "significant_close": bool(significant_close),
                "trendline_scope": "LOCAL" if local_only else "MAJOR",
                "touch_quality": "VALIDATED",
                "breach_state": "ACTIVE",
                "confidence": _clip01(0.60 + min(0.28, touches * 0.055) - min(0.08, wick_probe_count * 0.015)),
                "trendline_validation": "wick_anchor_no_obstruction_no_significant_close",
                "validation_reason": (
                    "diagonal_wick_anchors_touch_no_price_obstruction_between_points_and_no_significant_close"
                ),
                "skill_gate": "TRENDLINE_WICK_ANCHOR_NO_OBSTRUCTION_V2",
                "_score": score,
            }
            if best is None or float(candidate["_score"]) > float(best.get("_score", 0.0)):
                best = candidate
    if best is None:
        return None
    best.pop("_score", None)
    return best


def _derive_trendline_overlays(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = _candle_line_rows(candles)
    if len(rows) < 4:
        return []
    overlays: list[dict[str, Any]] = []
    for role, overlay_type, label in (
        ("support", "SUPPORT_TRENDLINE", "SUPPORT TRENDLINE"),
        ("resistance", "RESISTANCE_TRENDLINE", "RESISTANCE TRENDLINE"),
    ):
        candidate = _validated_trendline(rows, role=role, local_only=False)
        if candidate:
            overlays.append(
                {
                    **candidate,
                    "type": overlay_type,
                    "label": label,
                    "display_label": label,
                    "direction": "BUY" if role == "support" else "SELL",
                    "role": f"{role}_trendline",
                    "trendline_role": role,
                    "anchor_type": "LINE",
                    "bounds": _point_pair_bounds(candidate["points"]),
                    "visible_modes": ["CLEAN_LIVE", "TRENDLINES", "PATH", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
                    "lifecycle_state": "ACTIVE",
                }
            )
    latest_direction = "support" if float(rows[-1]["center_y"]) <= float(rows[max(0, len(rows) - 4)]["center_y"]) else "resistance"
    inner_candidates: list[dict[str, Any]] = []
    for role in ("support", "resistance"):
        candidate = _validated_trendline(rows, role=role, local_only=True)
        if candidate:
            candidate = dict(candidate)
            candidate["_inner_score"] = (
                float(candidate.get("touch_count", 0.0) or 0.0)
                - float(candidate.get("close_distance_norm", 9.0) or 9.0)
                + (0.75 if role == latest_direction else 0.0)
            )
            inner_candidates.append(candidate)
    inner = max(inner_candidates, key=lambda item: float(item.get("_inner_score", 0.0))) if inner_candidates else None
    if inner:
        inner.pop("_inner_score", None)
        latest_direction = str(inner.get("role") or latest_direction)
        overlays.append(
            {
                **inner,
                "type": "INNER_TRENDLINE",
                "label": "INNER TRENDLINE",
                "display_label": "INNER TRENDLINE",
                "direction": "BUY" if latest_direction == "support" else "SELL",
                "role": "inner_trendline",
                "trendline_role": latest_direction,
                "anchor_type": "LINE",
                "bounds": _point_pair_bounds(inner["points"]),
                "visible_modes": ["CLEAN_LIVE", "TRENDLINES", "PATH", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
                "lifecycle_state": "ACTIVE",
            }
        )
    return [row for row in overlays if normalize_bounds(row.get("bounds")) is not None]


@dataclass(frozen=True)
class MarketObjectV3:
    object_id: str
    object_type: str
    source_path: str
    side: str
    bounds: tuple[float, float, float, float]
    confidence: float
    truth_score: float
    first_seen_frame: int
    last_seen_frame: int
    track_id: str
    label: str
    reason: str
    state: str = "ACTIVE"
    anchor_type: str = "BOX"
    anchor_candles: tuple[int, ...] = ()
    tight_bounds: tuple[float, float, float, float] | None = None
    expanded_bounds: tuple[float, float, float, float] | None = None

    def as_dict(self) -> dict[str, Any]:
        tight_bounds = self.tight_bounds or self.bounds
        expanded_bounds = self.expanded_bounds or self.bounds
        return {
            "object_id": self.object_id,
            "type": self.object_type,
            "object_type": self.object_type,
            "source_path": self.source_path,
            "side": self.side,
            "state": self.state,
            "lifecycle_state": self.state,
            "anchor_type": self.anchor_type,
            "anchor_candles": list(self.anchor_candles),
            "bounds": list(self.bounds),
            "bbox": list(self.bounds),
            "tight_bounds": list(tight_bounds),
            "expanded_bounds": list(expanded_bounds),
            "confidence": self.confidence,
            "truth_score": self.truth_score,
            "first_seen_frame": self.first_seen_frame,
            "last_seen_frame": self.last_seen_frame,
            "track_id": self.track_id,
            "label": self.label,
            "reason": self.reason,
        }


def _empty_source_status() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class SequenceContextV3:
    sequence_id: str
    frame_start: int
    frame_end: int
    sequence_length: int
    frames_received: int
    frames_used: int
    sequence_signature: str
    confidence: float
    directions: Mapping[str, str]
    phase: str
    tracked_objects: tuple[str, ...]
    memory_matches: tuple[Mapping[str, Any], ...]
    status: str
    placeholder: bool = True
    impulse_tracks: tuple[str, ...] = ()
    pullback_tracks: tuple[str, ...] = ()
    retest_tracks: tuple[str, ...] = ()
    continuation_tracks: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()
    angle_vectors: tuple[str, ...] = ()
    sniper_entries: tuple[str, ...] = ()
    target_zones: tuple[str, ...] = ()
    invalidation_zones: tuple[str, ...] = ()
    prediction_paths: tuple[str, ...] = ()
    source_status: dict[str, str] = field(default_factory=_empty_source_status)
    missing_sources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEQUENCE_CONTEXT_SCHEMA_VERSION,
            "sequence_id": self.sequence_id,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "sequence_length": self.sequence_length,
            "frames_received": self.frames_received,
            "frames_used": self.frames_used,
            "sequence_signature": self.sequence_signature,
            "confidence": self.confidence,
            "directions": dict(self.directions),
            "phase": self.phase,
            "tracked_objects": list(self.tracked_objects),
            "impulse_tracks": list(self.impulse_tracks),
            "pullback_tracks": list(self.pullback_tracks),
            "retest_tracks": list(self.retest_tracks),
            "continuation_tracks": list(self.continuation_tracks),
            "zones": list(self.zones),
            "angle_vectors": list(self.angle_vectors),
            "sniper_entries": list(self.sniper_entries),
            "target_zones": list(self.target_zones),
            "invalidation_zones": list(self.invalidation_zones),
            "prediction_paths": list(self.prediction_paths),
            "memory_matches": [dict(item) for item in self.memory_matches],
            "source_status": dict(self.source_status),
            "missing_sources": list(self.missing_sources),
            "status": self.status,
            "sequence_status": self.status,
            "placeholder": self.placeholder,
        }


@dataclass(frozen=True)
class MarketObjectRegistryV3:
    session_id: str
    frame_id: int
    status: str
    degraded: bool
    missing_sources: tuple[str, ...]
    source_status: Mapping[str, str]
    objects: tuple[MarketObjectV3, ...]
    overlays: tuple[dict[str, Any], ...]
    sequence_context: SequenceContextV3

    def counts_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for obj in self.objects:
            counts[obj.object_type] = counts.get(obj.object_type, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        objects = [obj.as_dict() for obj in self.objects]
        overlays = [dict(overlay) for overlay in self.overlays]
        return {
            "schema_version": TRACKER_SCHEMA_VERSION,
            "registry_schema_version": MARKET_OBJECT_REGISTRY_SCHEMA_VERSION,
            "session_id": self.session_id,
            "frame_id": self.frame_id,
            "status": self.status,
            "degraded": self.degraded,
            "missing_sources": list(self.missing_sources),
            "invalid_sources": [],
            "source_status": dict(self.source_status),
            "counts_by_type": self.counts_by_type(),
            "object_count": len(self.objects),
            "overlay_count": len(self.overlays),
            "objects": objects,
            "object_registry": objects,
            "tracked_objects": objects,
            "overlays": overlays,
            "overlay_objects": overlays,
            "sequence_context": self.sequence_context.as_dict(),
            "overlay_contract": validate_overlay_payload(self.overlays),
        }


class _RegistryBuilder:
    def __init__(self, first_seen_by_id: Mapping[str, int] | None = None) -> None:
        self.first_seen_by_id = dict(first_seen_by_id or {})

    def build(self, payload: Mapping[str, Any]) -> MarketObjectRegistryV3:
        session_id = _session_id(payload)
        frame_id = _frame_id(payload)
        tracking = _mapping(payload.get("tracking_summary"))
        signal = _mapping(payload.get("latest_signal"))
        candles = _sequence_of_mappings(tracking.get("tracked_candles"))
        missing: list[str] = []
        if not tracking:
            missing.append("tracking_summary")
        if not candles:
            missing.append("tracking_summary.tracked_candles")
        source_status = {
            "tracking_summary": "READY" if tracking else "MISSING",
            "tracking_summary.tracked_candles": "READY" if candles else "MISSING",
            "tracking_summary.structure_boxes": "READY" if _sequence_of_mappings(tracking.get("structure_boxes")) else "MISSING",
            "tracking_summary.projection": "READY" if _mapping(tracking.get("projection")) else "MISSING",
            "memory_projection": "READY" if _mapping(payload.get("memory_projection_predict") or payload.get("memory_projection_current")) else "MISSING",
        }
        if missing:
            sequence = self._sequence_context(
                payload,
                tracking,
                signal,
                (),
                status="MISSING_CRITICAL_SOURCE",
                missing_sources=tuple(missing),
                source_status=source_status,
            )
            return MarketObjectRegistryV3(
                session_id=session_id,
                frame_id=frame_id,
                status="MISSING_CRITICAL_SOURCE",
                degraded=True,
                missing_sources=tuple(missing),
                source_status=source_status,
                objects=(),
                overlays=(),
                sequence_context=sequence,
            )

        objects: list[MarketObjectV3] = []
        overlays: list[dict[str, Any]] = []
        chart_transform_id = _chart_transform_id(payload, tracking)
        sequence_id = _sequence_id(session_id, frame_id, tracking, signal)

        def add_object(
            raw: Mapping[str, Any],
            *,
            object_type: str,
            source_path: str,
            source_key: Any = "",
            label: str = "",
            role: str = "",
            layer: str = "",
            side: Any = None,
            reason: str = "",
            lifecycle_state: str = "ACTIVE",
        ) -> None:
            bbox = _raw_bbox(raw)
            if bbox is None or normalize_bbox(bbox) is None:
                return
            anchor_indices, anchor_touch_points = _candle_anchor_evidence(raw, bbox, candles)
            explicit_anchor_indices = _anchor_indices_from_raw(raw)
            explicit_touch_points = _point_rows(raw.get("touch_points"))
            if explicit_anchor_indices:
                anchor_indices = explicit_anchor_indices
            if explicit_touch_points:
                anchor_touch_points = explicit_touch_points
            bbox, anchor_quality = _tighten_bbox_to_anchor_rows(
                object_type=object_type,
                raw=raw,
                bbox=bbox,
                candles=candles,
                anchor_indices=anchor_indices,
            )
            selected_anchor_indices = [
                int(_float(item, -1.0))
                for item in _sequence(anchor_quality.get("selected_anchor_indices"))
                if int(_float(item, -1.0)) >= 0
            ]
            if selected_anchor_indices and object_type not in TRENDLINE_OVERLAY_TYPES:
                anchor_indices = selected_anchor_indices
            selected_anchor_points = _point_rows(anchor_quality.get("selected_anchor_points"))
            if selected_anchor_points and object_type not in TRENDLINE_OVERLAY_TYPES:
                anchor_touch_points = selected_anchor_points
            if bool(anchor_quality.get("status") == "REJECT") and object_type in CRITICAL_ANCHORED_OVERLAY_TYPES:
                # Do not promote forced or floating boxes into the live overlay truth layer.
                # They remain inspectable as rejected overlays through diagnostics if the caller emits them there.
                return
            side_value = _upper_side(side if side is not None else raw.get("side", raw.get("direction", signal.get("action"))))
            object_id = _stable_id(session_id, object_type, source_path, source_key)
            track_id = _text(raw.get("track_id") or raw.get("persistent_id"), object_id)
            first_seen = int(self.first_seen_by_id.get(object_id, frame_id))
            self.first_seen_by_id.setdefault(object_id, first_seen)
            confidence = _clip01(raw.get("confidence", raw.get("truth_score", signal.get("effective_confidence", signal.get("confidence", 0.0)))))
            truth = _clip01(raw.get("truth_score", confidence))
            label_value = _text(label or raw.get("label") or raw.get("key") or object_type.replace("_", " "))
            reason_value = _text(reason or raw.get("reason") or raw.get("story") or f"{object_type} from {source_path}")
            obj = MarketObjectV3(
                object_id=object_id,
                object_type=object_type,
                source_path=source_path,
                side=side_value,
                bounds=tuple(float(value) for value in bbox[:4]),  # type: ignore[arg-type]
                confidence=confidence,
                truth_score=truth,
                first_seen_frame=first_seen,
                last_seen_frame=frame_id,
                track_id=track_id,
                label=label_value,
                reason=reason_value,
                state=lifecycle_state,
                anchor_type=_text(raw.get("anchor_type"), "BOX").upper(),
                anchor_candles=tuple(anchor_indices),
            )
            objects.append(obj)
            overlay_raw = dict(raw)
            overlay_raw.update(
                {
                    "overlay_id": stable_overlay_id(session_id, frame_id, object_id),
                    "object_id": object_id,
                    "track_id": track_id,
                    "type": object_type,
                    "side": side_value,
                    "source_agent": "market_object_tracker_v3",
                    "source_path": source_path,
                    "source_key": source_key,
                    "frame_id": frame_id,
                    "sequence_id": sequence_id,
                    "chart_transform_id": chart_transform_id,
                    "coordinate_mode": overlay_raw.get("coordinate_mode", "CHART_IMAGE_SPACE"),
                    "anchor_type": _text(raw.get("anchor_type"), "BOX").upper(),
                    "anchor_candles": list(obj.anchor_candles),
                    "anchor_candle_indices": list(obj.anchor_candles),
                    "anchor_quality": dict(anchor_quality),
                    "anchor_evidence_status": _text(anchor_quality.get("status"), "UNKNOWN"),
                    "bounds": bbox,
                    "bbox": bbox,
                    "truth_score": truth,
                    "confidence": confidence,
                    "lifecycle_state": lifecycle_state,
                    "reason": reason_value,
                    "label": label_value,
                    "display_label": _short_display_label_for_overlay(object_type, side_value, label_value),
                    "layer": layer or TYPE_LAYER_MAP.get(object_type, "diagnostics"),
                    "role": role or TYPE_ROLE_MAP.get(object_type, ""),
                    "visible_default": bool(raw.get("visible_default", object_type not in {"DEBUG_RAW_DETECTION", "PROGRESSION_PATH"})),
                }
            )
            overlay_raw.update(
                _display_profile_for_overlay(
                    object_type=object_type,
                    layer=str(overlay_raw.get("layer") or layer or TYPE_LAYER_MAP.get(object_type, "diagnostics")),
                    lifecycle_state=lifecycle_state,
                    anchor_quality=anchor_quality,
                    confidence=confidence,
                    truth_score=truth,
                )
            )
            if anchor_touch_points:
                overlay_raw["touch_points"] = anchor_touch_points
                overlay_raw["anchor_wick_points"] = anchor_touch_points
            normalized_overlay = normalize_v3_overlay_object(
                overlay_raw,
                strict=False,
                frame_id=frame_id,
                sequence_id=sequence_id,
                chart_transform_id=chart_transform_id,
                source_agent="market_object_tracker_v3",
            )
            normalized_overlay.update(
                {
                    "anchor_quality": dict(anchor_quality),
                    "anchor_evidence_status": _text(anchor_quality.get("status"), "UNKNOWN"),
                    "display_state": overlay_raw.get("display_state", normalized_overlay.get("display_state", "COMPACT")),
                    "visual_weight": overlay_raw.get("visual_weight", normalized_overlay.get("visual_weight", 0.55)),
                    "style": overlay_raw.get("style", normalized_overlay.get("style", {})),
                    "label_hidden": overlay_raw.get("label_hidden", normalized_overlay.get("label_hidden", False)),
                    "label_visible": overlay_raw.get("label_visible", not bool(overlay_raw.get("label_hidden", False))),
                    "label_anchor": overlay_raw.get("label_anchor", normalized_overlay.get("label_anchor", "overlay")),
                    "geometry_visible": True,
                    "inspector_visible": True,
                    "visible_modes": overlay_raw.get("visible_modes", normalized_overlay.get("visible_modes", [])),
                    "bbox": bbox,
                    "bounds": bbox,
                }
            )
            if object_type in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}:
                line_points = _point_rows(overlay_raw.get("line_points") or overlay_raw.get("points") or overlay_raw.get("path"))
                touch_points = _point_rows(overlay_raw.get("touch_points")) or line_points[:2]
                if len(line_points) >= 2 and len(touch_points) >= 2:
                    normalized_overlay.update(
                        {
                            "anchor_type": "TRENDLINE_TOUCH_POINTS",
                            "line_points": line_points,
                            "points": line_points,
                            "path": line_points,
                            "touch_points": touch_points,
                            "trendline_touch_points": touch_points,
                            "anchor_wick_points": touch_points,
                            "anchor_evidence": {
                                "valid": True,
                                "anchor_type": "TRENDLINE_TOUCH_POINTS",
                                "touch_points": touch_points,
                                "candle_indices": list(obj.anchor_candles),
                                "touch_count": int(_float(overlay_raw.get("touch_count"), float(len(touch_points)))),
                                "validation": _text(
                                    overlay_raw.get("trendline_validation"),
                                    "wick_anchor_no_obstruction_no_significant_close",
                                ),
                            },
                            "anchor_evidence_status": "VALID",
                        }
                    )
            overlays.append(normalized_overlay)

        latest_index = len(candles) - 1
        for candle_index, candle in enumerate(candles):
            latest = candle_index == latest_index
            candle_modes = ["CANDLES", "INSPECTOR"]
            if latest:
                candle_modes = ["CLEAN_LIVE", "CANDLES", "LOCAL", "ACTIVE_CONTEXT", "INSPECTOR"]
            candle_row: dict[str, Any] = {
                **candle,
                "visible_modes": candle_modes,
                "label": "CURRENT CANDLE" if latest else "CANDLES",
                "display_label": "NOW" if latest else "CANDLES",
                "label_hidden": not latest,
                "label_anchor": "hidden" if not latest else "top",
                "visible_default": latest,
                "z_index": 120 if latest else 48 + candle_index,
                "anchor_type": "CANDLE",
                "anchor_candles": [candle_index],
            }
            add_object(
                candle_row,
                object_type="CURRENT_CANDLE",
                source_path=f"tracking_summary.tracked_candles[{candle_index}]",
                source_key=candle.get("track_id", candle_index),
                label="CURRENT CANDLE" if latest else "CANDLES",
                role="current_candle" if latest else "visible_candle",
                layer="recent_candles",
                side=candle.get("direction"),
            )

        for index, trendline in enumerate(_derive_trendline_overlays(candles)):
            object_type = normalize_overlay_type(
                trendline.get("type"),
                layer=trendline.get("layer"),
                role=trendline.get("role"),
                side=trendline.get("direction"),
            )
            add_object(
                trendline,
                object_type=object_type,
                source_path=f"tracking_summary.trendlines_v3[{index}]",
                source_key=trendline.get("trendline_role", index),
                label=_text(trendline.get("label"), object_type.replace("_", " ")),
                role=_text(trendline.get("role"), TYPE_ROLE_MAP.get(object_type, "")),
                layer=TYPE_LAYER_MAP.get(object_type, "diagnostics"),
                side=trendline.get("direction"),
                reason="Validated wick trendline from tracked candles.",
                lifecycle_state=_text(trendline.get("lifecycle_state"), "ACTIVE"),
            )

        for index, box in enumerate(_sequence_of_mappings(tracking.get("structure_boxes"))):
            key = str(box.get("key") or box.get("role") or "").lower()
            label_lower = str(box.get("label") or "").lower()
            if "global" in key or index == 0:
                object_type = "IMPULSE_BOX"
            elif "local" in key or "pullback" in label_lower:
                object_type = "PULLBACK_BOX"
            elif "current" in key or "continuation" in label_lower:
                object_type = "CONTINUATION_BOX"
            else:
                object_type = "CONTINUATION_BOX"
            add_object(
                box,
                object_type=object_type,
                source_path=f"tracking_summary.structure_boxes[{index}]",
                source_key=box.get("key", index),
                label=_text(box.get("label"), object_type.replace("_", " ")),
                role=str(box.get("role") or box.get("key") or ""),
                layer=TYPE_LAYER_MAP[object_type],
                side=box.get("direction"),
            )
            structure_key = box.get("key", index)
            structure_label = _text(box.get("label") or box.get("key"), object_type.replace("_", " "))
            structure_side = _upper_side(box.get("direction", signal.get("action")))
            micro_specs = (
                ("sniper_window", "SNIPER_ENTRY_BOX", "sniper", f"SNIPER {structure_side}"),
                ("trigger_window", "RETEST_BOX", "trigger", f"TRIGGER {structure_side}"),
                ("target_window", "TARGET_ZONE_BOX", "target", f"{structure_side} TARGET"),
                ("target_bbox", "TARGET_ZONE_BOX", "target", f"{structure_side} TARGET"),
            )
            for field_name, micro_type, micro_role, micro_label in micro_specs:
                if normalize_bounds(box.get(field_name)) is None:
                    continue
                add_object(
                    {
                        **box,
                        "bbox": box.get(field_name),
                        "source_bbox": box.get("bbox") or box.get("bounds"),
                        "label": micro_label,
                        "role": micro_role,
                        "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"],
                        "parent_label": structure_label,
                    },
                    object_type=micro_type,
                    source_path=f"tracking_summary.structure_boxes[{index}].{field_name}",
                    source_key=structure_key,
                    label=micro_label,
                    role=micro_role,
                    layer=TYPE_LAYER_MAP[micro_type],
                    side=box.get("direction"),
                )
            if box.get("invalidation_y") is not None:
                base_bbox = _raw_bbox(box)
                if base_bbox is not None:
                    invalidation_y = _float(box.get("invalidation_y"), base_bbox[3])
                    add_object(
                        {
                            **box,
                            "bbox": [base_bbox[0], invalidation_y - 2.0, base_bbox[2], invalidation_y + 2.0],
                            "source_bbox": base_bbox,
                            "label": f"{structure_side} INVALIDATION",
                            "role": "invalidation",
                            "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"],
                            "parent_label": structure_label,
                        },
                        object_type="INVALIDATION_BOX",
                        source_path=f"tracking_summary.structure_boxes[{index}].invalidation_y",
                        source_key=structure_key,
                        label=f"{structure_side} INVALIDATION",
                        role="invalidation",
                        layer=TYPE_LAYER_MAP["INVALIDATION_BOX"],
                        side=box.get("direction"),
                    )

        for index, box in enumerate(_sequence_of_mappings(tracking.get("historical_structure"))):
            label = _text(box.get("label"), f"history {index + 1}")
            lower = label.lower()
            object_type = "PULLBACK_BOX" if "pullback" in lower else "PROGRESSION_PATH"
            history_overlay = dict(box)
            if object_type == "PROGRESSION_PATH":
                line_points = _sequence(box.get("line_points") or box.get("points") or box.get("path"))
                if len(line_points) < 2:
                    start_point = _sequence(box.get("start_point"))
                    end_point = _sequence(box.get("end_point"))
                    if len(start_point) >= 2 and len(end_point) >= 2:
                        line_points = [start_point[:2], end_point[:2]]
                path_bounds = normalize_bounds(box.get("path_bounds") or line_points)
                if len(line_points) >= 2 and path_bounds is not None:
                    history_overlay.update(
                        {
                            "bbox": path_bounds,
                            "bounds": path_bounds,
                            "line_points": line_points,
                            "points": line_points,
                            "path": line_points,
                            "anchor_type": "POLYGON",
                            "visible_default": True,
                            "visible_modes": ["FULL_HISTORY_READ", "REPLAY", "PATH", "ACTIVE_CONTEXT", "INSPECTOR"],
                        }
                    )
            add_object(
                {**history_overlay, "visible_modes": history_overlay.get("visible_modes", ["FULL_HISTORY_READ", "REPLAY", "INSPECTOR"])},
                object_type=object_type,
                source_path=f"tracking_summary.historical_structure[{index}]",
                source_key=box.get("key", index),
                label=label,
                role="history",
                layer="historical_replay" if object_type == "PROGRESSION_PATH" else "local_swings",
                side=box.get("direction"),
                lifecycle_state="HISTORICAL",
            )
            history_key = box.get("key", index)
            history_side = _upper_side(box.get("direction", signal.get("action")))
            replay_specs = (
                ("sniper_window", "REPLAY_ENTRY", "replay_entry", "WOULD HAVE ENTERED"),
                ("trigger_window", "RETEST_BOX", "replay_trigger", "TRIGGER"),
                ("target_window", "REPLAY_EXIT", "replay_exit", "WOULD HAVE EXITED"),
                ("target_bbox", "REPLAY_EXIT", "replay_exit", "WOULD HAVE EXITED"),
            )
            emitted_replay_fields: set[str] = set()
            for field_name, replay_type, replay_role, replay_label in replay_specs:
                if field_name in emitted_replay_fields:
                    continue
                replay_bounds = normalize_bounds(box.get(field_name))
                if replay_bounds is None:
                    if field_name == "sniper_window":
                        replay_bounds = _point_box(box.get("start_point"))
                    elif field_name in {"target_window", "target_bbox"}:
                        replay_bounds = _point_box(box.get("end_point"))
                if replay_bounds is None:
                    continue
                emitted_replay_fields.add(field_name)
                add_object(
                    {
                        **box,
                        "bbox": replay_bounds,
                        "source_bbox": box.get("bbox") or box.get("bounds"),
                        "label": replay_label,
                        "display_label": replay_label,
                        "role": replay_role,
                        "visible_modes": ["FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
                        "parent_label": label,
                        "replay_sequence": int(_float(box.get("sequence_index"), index + 1)),
                        "replay_action": replay_role,
                    },
                    object_type=replay_type,
                    source_path=f"tracking_summary.historical_structure[{index}].{field_name}",
                    source_key=history_key,
                    label=replay_label,
                    role=replay_role,
                    layer="historical_replay",
                    side=history_side,
                    lifecycle_state="HISTORICAL",
                )
                if field_name == "target_window":
                    emitted_replay_fields.add("target_bbox")
            if box.get("invalidation_y") is not None:
                base_bbox = _raw_bbox(box)
                if base_bbox is not None:
                    invalidation_y = _float(box.get("invalidation_y"), base_bbox[3])
                    add_object(
                        {
                            **box,
                            "bbox": [base_bbox[0], invalidation_y - 2.0, base_bbox[2], invalidation_y + 2.0],
                            "source_bbox": base_bbox,
                            "label": "INVALID",
                            "display_label": "INVALID",
                            "role": "replay_invalidation",
                            "visible_modes": ["FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
                            "parent_label": label,
                            "replay_sequence": int(_float(box.get("sequence_index"), index + 1)),
                            "replay_action": "invalidation",
                        },
                        object_type="INVALIDATION_BOX",
                        source_path=f"tracking_summary.historical_structure[{index}].invalidation_y",
                        source_key=history_key,
                        label="INVALID",
                        role="replay_invalidation",
                        layer="historical_replay",
                        side=history_side,
                        lifecycle_state="HISTORICAL",
                    )

        for index, zone in enumerate(_sequence_of_mappings(tracking.get("support_resistance_zones"))):
            role = _zone_role(zone)
            if role not in {"support", "resistance"}:
                continue
            object_type = "DEMAND_ZONE" if role == "support" else "SUPPLY_ZONE"
            lifecycle_state = _zone_lifecycle_state(zone)
            add_object(
                {
                    **zone,
                    "zone_family": object_type,
                    "lifecycle_state": lifecycle_state,
                    "entry_authority_active": _active_zone_lifecycle(lifecycle_state),
                },
                object_type=object_type,
                source_path=f"tracking_summary.support_resistance_zones[{index}]",
                source_key=zone.get("key", index),
                label=_text(zone.get("label"), object_type.replace("_", " ")),
                role=role or TYPE_ROLE_MAP.get(object_type, ""),
                layer="supply_demand",
                side=zone.get("direction"),
                lifecycle_state=lifecycle_state,
            )

        projection = _mapping(tracking.get("projection"))
        for index, zone in enumerate(_sequence_of_mappings(projection.get("zones"))):
            kind = str(zone.get("kind") or zone.get("role") or "").lower()
            if "sniper" in kind:
                object_type = "SNIPER_ENTRY_BOX"
            elif "trigger" in kind or "primary" in kind or index > 0:
                object_type = "RETEST_BOX"
            else:
                object_type = "CONTINUATION_BOX"
            add_object(
                zone,
                object_type=object_type,
                source_path=f"tracking_summary.projection.zones[{index}]",
                source_key=zone.get("key", kind or index),
                label=_text(zone.get("label"), object_type.replace("_", " ")),
                role=kind or TYPE_ROLE_MAP.get(object_type, ""),
                layer="trigger_zones",
                side=zone.get("direction", projection.get("direction")),
                lifecycle_state="PREDICTED",
            )
            if normalize_bounds(zone.get("target_bbox")) is not None:
                add_object(
                    {
                        **zone,
                        "bbox": zone.get("target_bbox"),
                        "source_bbox": zone.get("bbox") or zone.get("bounds"),
                        "label": f"{_upper_side(zone.get('direction', projection.get('direction')))} TARGET",
                    },
                    object_type="TARGET_ZONE_BOX",
                    source_path=f"tracking_summary.projection.zones[{index}].target_bbox",
                    source_key=zone.get("key", kind or index),
                    label=f"{_upper_side(zone.get('direction', projection.get('direction')))} TARGET",
                    role="target",
                    layer="trigger_zones",
                    side=zone.get("direction", projection.get("direction")),
                    lifecycle_state="PREDICTED",
                )
            if zone.get("invalidation_y") is not None and _raw_bbox(zone) is not None:
                bbox = _raw_bbox(zone) or [0, 0, 1, 1]
                y = _float(zone.get("invalidation_y"), bbox[3])
                add_object(
                    {
                        **zone,
                        "bbox": [bbox[0], y - 2.0, bbox[2], y + 2.0],
                        "source_bbox": bbox,
                        "label": f"{_upper_side(zone.get('direction', projection.get('direction')))} INVALIDATION",
                    },
                    object_type="INVALIDATION_BOX",
                    source_path=f"tracking_summary.projection.zones[{index}].invalidation_y",
                    source_key=zone.get("key", kind or index),
                    label=f"{_upper_side(zone.get('direction', projection.get('direction')))} INVALIDATION",
                    role="invalidation",
                    layer="trigger_zones",
                    side=zone.get("direction", projection.get("direction")),
                    lifecycle_state="PREDICTED",
                )
            if normalize_bounds(zone.get("path")) is not None:
                add_object(
                    {**zone, "bounds": zone.get("path"), "label": f"{_upper_side(zone.get('direction', projection.get('direction')))} PREDICTION PATH"},
                    object_type="PREDICTION_PATH",
                    source_path=f"tracking_summary.projection.zones[{index}].path",
                    source_key=zone.get("key", kind or index),
                    label=f"{_upper_side(zone.get('direction', projection.get('direction')))} PREDICTION PATH",
                    role="prediction",
                    layer="active_council_decision",
                    side=zone.get("direction", projection.get("direction")),
                    lifecycle_state="PREDICTED",
                )

        for index, vector in enumerate(_sequence_of_mappings(tracking.get("angle_vectors"))):
            add_object(
                vector,
                object_type="ANGLE_VECTOR",
                source_path=f"tracking_summary.angle_vectors[{index}]",
                source_key=vector.get("id", index),
                label=_text(vector.get("label"), "ANGLE VECTOR"),
                role="angle",
                layer="active_council_decision",
                side=vector.get("direction"),
            )

        execution_timing = _mapping(tracking.get("execution_timing"))
        for key, fallback_type in (("entry_area_zone", "DEMAND_ZONE"), ("opposing_force_zone", "OPPOSING_FORCE")):
            zone = _mapping(execution_timing.get(key))
            if zone:
                role_value = _zone_role(zone)
                if fallback_type == "OPPOSING_FORCE":
                    object_type = "OPPOSING_FORCE"
                elif role_value == "resistance":
                    object_type = "SUPPLY_ZONE"
                elif role_value == "support":
                    object_type = "DEMAND_ZONE"
                else:
                    continue
                lifecycle_state = _zone_lifecycle_state(zone)
                if object_type == "OPPOSING_FORCE" and not _active_zone_lifecycle(lifecycle_state):
                    continue
                add_object(
                    {
                        **zone,
                        "zone_family": object_type,
                        "source_zone_id": zone.get("source_zone_id") or zone.get("zone_id") or zone.get("key") or zone.get("label"),
                        "side_blocked": _upper_side(zone.get("side_blocked") or signal.get("action")),
                        "force_type": zone.get("force_type") or role_value or "opposing_force",
                        "force_strength": zone.get("force_strength", zone.get("confidence", 0.0)),
                        "anchor_level": zone.get("anchor_level", zone.get("line_y")),
                        "lifecycle_state": lifecycle_state,
                        "entry_authority_active": _active_zone_lifecycle(lifecycle_state),
                    },
                    object_type=object_type,
                    source_path=f"tracking_summary.execution_timing.{key}",
                    source_key=zone.get("label", key),
                    label=_text(zone.get("label"), object_type.replace("_", " ")),
                    role=key,
                    layer="supply_demand",
                    side=zone.get("direction"),
                    lifecycle_state=lifecycle_state,
                )

        memory = _mapping(payload.get("memory_projection_current")) or _mapping(payload.get("memory_projection_predict")) or _mapping(payload.get("memory_projection_future"))
        forward = _mapping(memory.get("forward_projection"))
        projected = _sequence_of_mappings(forward.get("projected_candles"))
        if projected:
            points: list[list[float]] = []
            for candle in projected:
                bbox = _raw_bbox(candle)
                if bbox:
                    points.append([(bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5])
            if points:
                add_object(
                    {"path": points, "confidence": memory.get("memory_precision_score", memory.get("memory_similarity", 0.0)), "direction": memory.get("dominant_side")},
                    object_type="PREDICTION_PATH",
                    source_path="memory_projection.forward_projection.projected_candles",
                    source_key=_text(memory.get("primary_fit", {})),
                    label=f"{_upper_side(memory.get('dominant_side'))} MEMORY PATH",
                    role="prediction",
                    layer="active_council_decision",
                    side=memory.get("dominant_side"),
                    lifecycle_state="PREDICTED",
                )

        unique_objects: dict[str, MarketObjectV3] = {}
        unique_overlays: dict[str, dict[str, Any]] = {}
        for obj, overlay in zip(objects, overlays):
            unique_objects[obj.object_id] = obj
            unique_overlays[obj.object_id] = overlay
        ordered_objects = tuple(unique_objects.values())
        ordered_overlays = tuple(unique_overlays[obj.object_id] for obj in ordered_objects)
        sequence = self._sequence_context(payload, tracking, signal, ordered_objects, status="READY", missing_sources=(), source_status=source_status)
        return MarketObjectRegistryV3(
            session_id=session_id,
            frame_id=frame_id,
            status="READY",
            degraded=False,
            missing_sources=(),
            source_status=source_status,
            objects=ordered_objects,
            overlays=ordered_overlays,
            sequence_context=sequence,
        )

    def _sequence_context(
        self,
        payload: Mapping[str, Any],
        tracking: Mapping[str, Any],
        signal: Mapping[str, Any],
        objects: Sequence[MarketObjectV3],
        *,
        status: str,
        missing_sources: tuple[str, ...],
        source_status: Mapping[str, str] | None = None,
    ) -> SequenceContextV3:
        session_id = _session_id(payload)
        frame_id = _frame_id(payload)
        candles = _sequence_of_mappings(tracking.get("tracked_candles"))
        visible = int(_float(tracking.get("visible_candle_count", len(candles)), len(candles)))
        memory = _mapping(payload.get("memory_projection_predict") or payload.get("memory_projection_current") or payload.get("memory_projection_future"))
        top_matches = _sequence_of_mappings(_mapping(memory.get("primary_fit")).get("top_matches"))
        memory_matches: list[dict[str, Any]] = []
        for item in top_matches[:3]:
            memory_matches.append(
                {
                    "entry_id": _text(item.get("entry_id") or item.get("image_name") or item.get("label")),
                    "label": _text(item.get("label")),
                    "similarity": _clip01(item.get("similarity", item.get("score", 0.0))),
                    "summary": _text(item.get("summary")),
                }
            )
        signature = hashlib.sha1(
            "|".join(
                [
                    str(frame_id),
                    str([(obj.object_id, obj.last_seen_frame, list(obj.bounds)) for obj in objects]),
                    str(memory_matches),
                    str(missing_sources),
                ]
            ).encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        phase = _text(tracking.get("overlay_kind") or signal.get("setup") or signal.get("setup_type"), "UNKNOWN").upper().replace(" ", "_")
        def ids_for(*types: str) -> tuple[str, ...]:
            wanted = set(types)
            return tuple(obj.object_id for obj in objects if obj.object_type in wanted)

        return SequenceContextV3(
            sequence_id=_sequence_id(session_id, frame_id, tracking, signal),
            frame_start=max(0, frame_id - max(1, visible)),
            frame_end=frame_id,
            sequence_length=visible,
            frames_received=int(_float(payload.get("capture_count", frame_id), frame_id)),
            frames_used=visible,
            sequence_signature=signature,
            confidence=max(_clip01(signal.get("effective_confidence", signal.get("confidence", 0.0))), _clip01(memory.get("memory_precision_score", 0.0))),
            directions={
                "global": _upper_side(tracking.get("global_direction")),
                "local": _upper_side(tracking.get("local_direction")),
                "impulse": _upper_side(tracking.get("impulse_direction")),
                "council": _upper_side(signal.get("action")),
                "execution": _upper_side(signal.get("execution_action")),
            },
            phase=phase,
            tracked_objects=tuple(obj.object_id for obj in objects),
            memory_matches=tuple(memory_matches),
            status=status,
            placeholder=True,
            impulse_tracks=ids_for("IMPULSE_BOX"),
            pullback_tracks=ids_for("PULLBACK_BOX"),
            retest_tracks=ids_for("RETEST_BOX"),
            continuation_tracks=ids_for("CONTINUATION_BOX"),
            zones=ids_for("SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"),
            angle_vectors=ids_for("ANGLE_VECTOR"),
            sniper_entries=ids_for("SNIPER_ENTRY_BOX"),
            target_zones=ids_for("TARGET_ZONE_BOX"),
            invalidation_zones=ids_for("INVALIDATION_BOX"),
            prediction_paths=ids_for("PREDICTION_PATH"),
            source_status=dict(source_status or {}),
            missing_sources=missing_sources,
        )


class MarketObjectTrackerV3:
    def __init__(self) -> None:
        self._first_seen_by_id: dict[str, int] = {}

    def build_registry(self, session_payload: Mapping[str, Any]) -> MarketObjectRegistryV3:
        builder = _RegistryBuilder(self._first_seen_by_id)
        registry = builder.build(session_payload)
        self._first_seen_by_id.update(builder.first_seen_by_id)
        return registry


def build_market_object_registry_v3(session_payload: Mapping[str, Any]) -> MarketObjectRegistryV3:
    return _RegistryBuilder().build(session_payload)


def build_v3_overlays_from_session(session_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(build_market_object_registry_v3(session_payload).overlays)


def build_sequence_context_v3(session_payload: Mapping[str, Any]) -> dict[str, Any]:
    return build_market_object_registry_v3(session_payload).sequence_context.as_dict()


derive_trendline_overlays = _derive_trendline_overlays


__all__ = [
    "MARKET_OBJECT_REGISTRY_SCHEMA_VERSION",
    "OVERLAY_SCHEMA_VERSION",
    "SEQUENCE_CONTEXT_SCHEMA_VERSION",
    "TRACKER_SCHEMA_VERSION",
    "MarketObjectRegistryV3",
    "MarketObjectTrackerV3",
    "MarketObjectV3",
    "SequenceContextV3",
    "build_market_object_registry_v3",
    "build_sequence_context_v3",
    "build_v3_overlays_from_session",
    "derive_trendline_overlays",
]
