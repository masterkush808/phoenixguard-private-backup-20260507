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
    for key in ("bounds", "bbox", "pixel_bbox", "box", "rect"):
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
                "anchor_candles": anchor_candles,
                "touch_count": int(touches),
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
                    "bounds": bbox,
                    "truth_score": truth,
                    "confidence": confidence,
                    "lifecycle_state": lifecycle_state,
                    "reason": reason_value,
                    "label": label_value,
                    "layer": layer or TYPE_LAYER_MAP.get(object_type, "diagnostics"),
                    "role": role or TYPE_ROLE_MAP.get(object_type, ""),
                    "visible_default": bool(raw.get("visible_default", object_type not in {"DEBUG_RAW_DETECTION", "PROGRESSION_PATH"})),
                }
            )
            if anchor_touch_points:
                overlay_raw["touch_points"] = anchor_touch_points
            normalized_overlay = normalize_v3_overlay_object(
                overlay_raw,
                strict=False,
                frame_id=frame_id,
                sequence_id=sequence_id,
                chart_transform_id=chart_transform_id,
                source_agent="market_object_tracker_v3",
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
                            "visible_modes": ["CLEAN_LIVE", "FULL_HISTORY_READ", "REPLAY", "PATH", "ACTIVE_CONTEXT", "INSPECTOR"],
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
