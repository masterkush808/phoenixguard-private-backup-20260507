from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence, cast


SCHEMA_VERSION = "PG_CANDLE_MOVEMENT_CONTEXT_V3"
REVERSAL_CONFIRMATION_CANDLES = 3

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M3": 180,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}

BOX_SOURCES: tuple[tuple[str, str], ...] = (
    ("historical_structure", "tracking_summary.historical_structure"),
    ("structure_boxes", "tracking_summary.structure_boxes"),
    ("support_resistance_zones", "tracking_summary.support_resistance_zones"),
    ("zones", "zones"),
)

SINGLE_BOX_KEYS: tuple[tuple[str, str], ...] = (
    ("current_box", "tracking_summary.current_box"),
    ("entry_zone", "entry_zone"),
    ("active_entry_zone", "active_entry_zone"),
    ("opposing_force_zone", "opposing_force_zone"),
)

PROJECTION_BOX_KEYS: tuple[str, ...] = (
    "sniper_window",
    "trigger_window",
    "target_window",
    "invalidation_window",
    "pullback_window",
    "continuation_window",
)

RELEVANT_BOX_TOKENS = (
    "SUPPORT",
    "RESISTANCE",
    "SUPPLY",
    "DEMAND",
    "TRENDLINE",
    "SNIPER",
    "TRIGGER",
    "TARGET",
    "IMPULSE",
    "PULLBACK",
    "CONTINUATION",
    "OPPOSING",
    "HISTORY",
    "REPLAY",
    "ENTRY",
    "EXIT",
)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _text(value: Any, default: str = "") -> str:
    raw = str(value if value is not None else default).strip()
    return raw or default


def _upper(value: Any, default: str = "") -> str:
    raw = _text(value, default).upper().replace(" ", "_").replace("-", "_")
    return raw or default


def _side(value: Any, default: str = "HOLD") -> str:
    normalized = _upper(value, default)
    if normalized in {"BUY", "BULL", "BULLISH", "GREEN", "UP", "DEMAND", "SUPPORT"}:
        return "BUY"
    if normalized in {"SELL", "BEAR", "BEARISH", "RED", "MAGENTA", "PINK", "DOWN", "SUPPLY", "RESISTANCE"}:
        return "SELL"
    return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed):
        return float(default)
    return float(parsed)


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "pass", "ok", "ready"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "fail", "blocked"}:
        return False
    return default


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _bounds(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items = list(cast(Sequence[Any], value))[:4]
    if len(items) < 4:
        return []
    values = [_float(item, float("nan")) for item in items]
    if any(not math.isfinite(item) for item in values):
        return []
    x0, y0, x1, y1 = values
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right <= left or bottom <= top:
        return []
    return [left, top, right, bottom]


def _first_bounds(row: Mapping[str, Any]) -> list[float]:
    for key in ("bbox", "bounds", "box", "rect", "range_bbox", "context_bbox", "pixel_bbox"):
        parsed = _bounds(row.get(key))
        if parsed:
            return parsed
    return []


def _center(bounds: Sequence[float]) -> tuple[float, float] | None:
    if len(bounds) < 4:
        return None
    return (float(bounds[0]) + float(bounds[2])) * 0.5, (float(bounds[1]) + float(bounds[3])) * 0.5


def _index_from_row(row: Mapping[str, Any], default: int) -> int:
    for key in ("index", "source_index", "candle_index", "track_id", "sequence_index"):
        raw = row.get(key)
        if raw in (None, "", [], {}):
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue
    source_path = _text(row.get("source_path"))
    if source_path:
        matches = re.findall(r"\[(\d+)\]", source_path)
        if matches:
            return int(matches[-1])
    return int(default)


def _int_list(value: Any) -> list[int]:
    values: list[int] = []
    if isinstance(value, Mapping):
        for nested_key in ("anchor_candle_indices", "anchor_candles", "source_indices", "candle_indices", "indices"):
            values.extend(_int_list(_mapping(value).get(nested_key)))
        return sorted(set(values))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    for item in cast(Sequence[Any], value):
        try:
            values.append(int(float(item)))
        except (TypeError, ValueError):
            continue
    return sorted(set(values))


def _anchor_indices(row: Mapping[str, Any]) -> list[int]:
    candidates: list[int] = []
    for key in ("anchor_candle_indices", "anchor_candles", "source_indices", "candle_indices", "indices"):
        candidates.extend(_int_list(row.get(key)))
    source_path = _text(row.get("source_path"))
    if source_path:
        candidates.extend(int(match) for match in re.findall(r"\[(\d+)\]", source_path))
    return sorted(set(index for index in candidates if index >= 0))


def _infer_direction(row: Mapping[str, Any], previous_price: float | None, price: float) -> str:
    explicit = _side(
        row.get("direction")
        or row.get("side")
        or row.get("candle_side")
        or row.get("color")
        or row.get("candle_color"),
        "HOLD",
    )
    if explicit in {"BUY", "SELL"}:
        return explicit
    if previous_price is None:
        return "HOLD"
    delta = price - previous_price
    if delta > 0.000001:
        return "BUY"
    if delta < -0.000001:
        return "SELL"
    return "HOLD"


def _price_proxy(row: Mapping[str, Any], bounds: Sequence[float]) -> float:
    for key in ("price_proxy", "close", "c", "close_price", "current_price_proxy"):
        raw = row.get(key)
        if raw not in (None, "", [], {}):
            return _float(raw)
    center = _center(bounds)
    if center is not None:
        return -float(center[1])
    return 0.0


def _candle_rows(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    tracking = _mapping(source.get("tracking_summary")) or _mapping(source)
    raw_rows = (
        _rows(source.get("tracked_candles"))
        or _rows(tracking.get("tracked_candles"))
        or _rows(source.get("candles"))
        or _rows(tracking.get("candles"))
    )
    output: list[dict[str, Any]] = []
    previous_price: float | None = None
    for position, row in enumerate(raw_rows):
        bounds = _first_bounds(row)
        index = _index_from_row(row, position)
        center = _center(bounds) if bounds else None
        price = _price_proxy(row, bounds)
        output.append(
            {
                "index": index,
                "position": position,
                "bbox": bounds,
                "center_x": center[0] if center else _float(row.get("center_x"), float(position)),
                "center_y": center[1] if center else _float(row.get("center_y"), 0.0),
                "price_proxy": price,
                "direction": _infer_direction(row, previous_price, price),
                "body_height_pct": _clip01(row.get("body_height_pct"), 0.0),
                "source_key": _text(row.get("source_key") or row.get("track_id") or index),
            }
        )
        previous_price = price
    return sorted(output, key=lambda item: (_float(item.get("center_x")), _float(item.get("position"))))


def _count_candle_color(candles: Sequence[Mapping[str, Any]], side: str) -> int:
    return sum(1 for candle in candles if _side(candle.get("direction"), "HOLD") == side)


def _duration_payload(candle_count: int, timeframe_seconds: int) -> dict[str, Any]:
    seconds = int(max(0, candle_count) * max(0, timeframe_seconds))
    return {
        "seconds": seconds,
        "minutes": round(seconds / 60.0, 3),
        "text": f"{round(seconds / 60.0, 1)}m" if seconds else "unknown",
    }


def _leg_stage(
    *,
    side: str,
    candle_count: int,
    previous_side: str,
    previous_candle_count: int,
    room_ok: bool | None,
    exhaustion_risk: float,
) -> tuple[str, str]:
    if side not in {"BUY", "SELL"}:
        return "CHOP", "Leg has no clean buy/sell direction."
    if exhaustion_risk >= 0.72 or candle_count >= 18:
        return "EXHAUSTED", "Move is extended or exhaustion risk is elevated."
    if room_ok is False and candle_count >= 4:
        return "LATE", "Opposing force is too close for fresh continuation."
    if previous_side in {"BUY", "SELL"} and previous_side != side and candle_count <= max(5, int(round(previous_candle_count * 0.45))):
        return "STILL_RECLAIMING", "Current leg is still reclaiming against the prior leg."
    if candle_count <= 3:
        return "EARLY", "Move has only a few candles of proof."
    if candle_count >= 14 and room_ok is False:
        return "LATE", "Move has matured and is close to opposing force."
    if candle_count >= 16:
        return "LATE", "Move is extended in candle count."
    return "MATURE", "Move has enough candles to matter without being exhausted."


def _box_contains_candle(box: Sequence[float], candle: Mapping[str, Any]) -> bool:
    if len(box) < 4:
        return False
    cx = _float(candle.get("center_x"), float("nan"))
    cy = _float(candle.get("center_y"), float("nan"))
    if math.isfinite(cx) and math.isfinite(cy) and box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
        return True
    candle_box = _bounds(candle.get("bbox"))
    if len(candle_box) < 4:
        return False
    overlap_x = max(0.0, min(box[2], candle_box[2]) - max(box[0], candle_box[0]))
    overlap_y = max(0.0, min(box[3], candle_box[3]) - max(box[1], candle_box[1]))
    return overlap_x > 0.0 and overlap_y > 0.0


def _box_is_relevant(row: Mapping[str, Any]) -> bool:
    label = _upper(row.get("label") or row.get("display_label") or row.get("short_label") or row.get("type") or row.get("role"))
    if any(token in label for token in RELEVANT_BOX_TOKENS):
        return True
    return bool(_anchor_indices(row) or _first_bounds(row))


def _box_payload(
    row: Mapping[str, Any],
    *,
    source_path: str,
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    bounds = _first_bounds(row)
    anchors = _anchor_indices(row)
    contained = [
        int(candle.get("index", candle.get("position", 0)) or 0)
        for candle in candles
        if bounds and _box_contains_candle(bounds, candle)
    ]
    label = _text(row.get("label") or row.get("display_label") or row.get("short_label") or row.get("type") or source_path)
    box_type = _upper(row.get("type") or row.get("role") or label, "BOX")
    return {
        "source_path": source_path,
        "type": box_type,
        "label": label,
        "side": _side(row.get("side") or row.get("direction") or row.get("role"), "HOLD"),
        "bbox": bounds,
        "anchor_candle_indices": anchors,
        "anchor_candle_count": len(anchors),
        "contained_candle_indices": sorted(set(contained)),
        "contained_candle_count": len(set(contained)),
        "touch_count": int(_float(row.get("touch_count") or row.get("wick_probe_count"), 0.0)),
        "anchor_quality": _clip01(row.get("anchor_quality") or row.get("truth_score") or row.get("confidence"), 0.0),
    }


def _collect_box_rows(source: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    tracking = _mapping(source.get("tracking_summary")) or _mapping(source)
    rows: list[tuple[str, dict[str, Any]]] = []
    for key, source_path in BOX_SOURCES:
        for index, row in enumerate(_rows(tracking.get(key) or source.get(key))):
            rows.append((f"{source_path}[{index}]", row))
    for key, source_path in SINGLE_BOX_KEYS:
        row = _mapping(tracking.get(key) or source.get(key))
        if row:
            rows.append((source_path, row))
    projection = _mapping(tracking.get("projection") or source.get("projection"))
    for index, row in enumerate(_rows(projection.get("zones"))):
        rows.append((f"tracking_summary.projection.zones[{index}]", row))
    for key in PROJECTION_BOX_KEYS:
        row = _mapping(projection.get(key))
        if row:
            rows.append((f"tracking_summary.projection.{key}", row))
    execution_timing = _mapping(tracking.get("execution_timing") or source.get("execution_timing"))
    for key in ("opposing_force_zone", "entry_area_zone", "preferred_entry_area"):
        row = _mapping(execution_timing.get(key))
        if row:
            rows.append((f"execution_timing.{key}", row))
    for index, row in enumerate(_rows(source.get("overlay_objects") or source.get("overlays"))):
        if _upper(row.get("type")) == "CURRENT_CANDLE":
            continue
        rows.append((f"overlay_objects[{index}]", row))
    return rows


def _dedupe_boxes(rows: Sequence[tuple[str, Mapping[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    output: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for source_path, row in rows:
        parsed = dict(row)
        label = _upper(parsed.get("label") or parsed.get("display_label") or parsed.get("type"))
        bounds = ",".join(str(round(value, 2)) for value in _first_bounds(parsed))
        key = f"{source_path}|{label}|{bounds}"
        if key in seen:
            continue
        seen.add(key)
        output.append((source_path, parsed))
    return output


def _opposing_force_room(source: Mapping[str, Any], candidate_side: str, visible_candle_count: int) -> dict[str, Any]:
    tracking = _mapping(source.get("tracking_summary")) or _mapping(source)
    market = _mapping(source.get("market_context"))
    risk = _mapping(source.get("risk_opposing_force"))
    timing = _mapping(source.get("execution_timing") or tracking.get("execution_timing"))
    zone = (
        _mapping(risk.get("zone"))
        or _mapping(timing.get("opposing_force_zone"))
        or _mapping(market.get("opposing_force_zone"))
        or _mapping(market.get("nearest_opposing_force"))
    )
    distance = None
    for container in (risk, timing, market, zone):
        for key in (
            "distance_to_opposing_force",
            "opposing_force_distance_norm",
            "distance_norm",
            "entry_area_distance_norm",
            "nearest_opposing_force_distance",
        ):
            raw = container.get(key)
            if raw not in (None, "", [], {}):
                distance = _clip01(raw, 0.0)
                break
        if distance is not None:
            break
    explicit_ok = None
    for container in (risk, timing, market):
        for key in ("distance_ok", "opposing_force_ok", "opposing_force_distance_ok", "clear_path_ready"):
            if key in container:
                explicit_ok = _bool(container.get(key))
                break
        if explicit_ok is not None:
            break
    room_ok = explicit_ok if explicit_ok is not None else (distance is None or distance >= 0.18)
    risk_state = _upper(risk.get("risk_state") or market.get("opposing_force_risk") or zone.get("risk_state"))
    if not risk_state:
        risk_state = "CLEAR" if room_ok and (distance is None or distance >= 0.28) else "TIGHT" if room_ok else "NEAR_OPPOSING_FORCE"
    estimated_candles = None if distance is None else int(round(distance * max(1, visible_candle_count)))
    return {
        "candidate_side": candidate_side if candidate_side in {"BUY", "SELL"} else "HOLD",
        "room_ok": bool(room_ok),
        "distance_norm": distance,
        "risk_state": risk_state,
        "estimated_candles_to_force": estimated_candles,
        "zone": zone,
        "reason": "Opposing force has room." if room_ok else "Opposing force is too close.",
    }


def _historical_legs(
    source: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    timeframe_seconds: int,
    room_ok: bool | None,
) -> list[dict[str, Any]]:
    tracking = _mapping(source.get("tracking_summary")) or _mapping(source)
    rows = _rows(tracking.get("historical_structure")) or _rows(source.get("historical_structure"))
    legs: list[dict[str, Any]] = []
    previous_side = "HOLD"
    previous_count = 0
    for position, row in enumerate(rows):
        anchors = _anchor_indices(row)
        candle_count = int(_float(row.get("candle_count"), float(len(anchors) or 0)))
        bounds = _first_bounds(row)
        if candle_count <= 0 and bounds:
            candle_count = sum(1 for candle in candles if _box_contains_candle(bounds, candle))
        if candle_count <= 0:
            candle_count = len(anchors)
        side = _side(row.get("direction") or row.get("side"), "HOLD")
        stage, stage_reason = _leg_stage(
            side=side,
            candle_count=candle_count,
            previous_side=previous_side,
            previous_candle_count=previous_count,
            room_ok=room_ok,
            exhaustion_risk=_clip01(row.get("exhaustion_risk"), 0.0),
        )
        start_index = min(anchors) if anchors else None
        end_index = max(anchors) if anchors else None
        legs.append(
            {
                "source": "historical_structure",
                "source_path": f"tracking_summary.historical_structure[{position}]",
                "label": _text(row.get("label") or f"L{position + 1} {side}"),
                "side": side,
                "candle_count": candle_count,
                "duration": _duration_payload(candle_count, timeframe_seconds),
                "start_index": start_index,
                "end_index": end_index,
                "source_indices": anchors,
                "net_move": _float(row.get("net_move"), 0.0),
                "slope": _float(row.get("slope"), 0.0),
                "move_stage": stage,
                "stage_reason": stage_reason,
            }
        )
        if side in {"BUY", "SELL"}:
            previous_side = side
            previous_count = candle_count
    if legs:
        return _reconcile_historical_tail(
            legs,
            candles,
            timeframe_seconds=timeframe_seconds,
            room_ok=room_ok,
        )
    return _derive_legs_from_candles(candles, timeframe_seconds, room_ok)


def _derive_legs_from_candles(
    candles: Sequence[Mapping[str, Any]],
    timeframe_seconds: int,
    room_ok: bool | None,
) -> list[dict[str, Any]]:
    if not candles:
        return []
    groups: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    active_side = "HOLD"
    for candle in candles:
        candle_side = _side(candle.get("direction"), "HOLD")
        if candle_side == "HOLD":
            candle_side = active_side
        if not current:
            current = [candle]
            active_side = candle_side
            continue
        if candle_side != active_side and candle_side in {"BUY", "SELL"} and len(current) >= 2:
            groups.append(current)
            current = [candle]
            active_side = candle_side
        else:
            current.append(candle)
            if active_side == "HOLD" and candle_side in {"BUY", "SELL"}:
                active_side = candle_side
    if current:
        groups.append(current)

    legs: list[dict[str, Any]] = []
    previous_side = "HOLD"
    previous_count = 0
    for position, group in enumerate(groups):
        first = group[0]
        last = group[-1]
        side = _side(last.get("direction"), _side(first.get("direction"), "HOLD"))
        count = len(group)
        stage, stage_reason = _leg_stage(
            side=side,
            candle_count=count,
            previous_side=previous_side,
            previous_candle_count=previous_count,
            room_ok=room_ok,
            exhaustion_risk=0.0,
        )
        indices = [int(row.get("index", index) or index) for index, row in enumerate(group)]
        legs.append(
            {
                "source": "tracked_candles",
                "source_path": f"tracking_summary.tracked_candles.derived_leg[{position}]",
                "label": f"L{position + 1} {side}",
                "side": side,
                "candle_count": count,
                "duration": _duration_payload(count, timeframe_seconds),
                "start_index": min(indices) if indices else None,
                "end_index": max(indices) if indices else None,
                "source_indices": sorted(set(indices)),
                "net_move": round(_float(last.get("price_proxy"), 0.0) - _float(first.get("price_proxy"), 0.0), 6),
                "slope": round((_float(last.get("price_proxy"), 0.0) - _float(first.get("price_proxy"), 0.0)) / max(1, count - 1), 6),
                "move_stage": stage,
                "stage_reason": stage_reason,
            }
        )
        if side in {"BUY", "SELL"}:
            previous_side = side
            previous_count = count
    return legs


def _leg_side(candles: Sequence[Mapping[str, Any]], default: str = "HOLD") -> str:
    for candle in reversed(candles):
        side = _side(candle.get("direction"), "HOLD")
        if side in {"BUY", "SELL"}:
            return side
    return default


def _leg_indices(candles: Sequence[Mapping[str, Any]]) -> list[int]:
    return sorted(
        {
            int(candle.get("index", candle.get("position", position)) or 0)
            for position, candle in enumerate(candles)
        }
    )


def _refresh_leg_with_tail(
    leg: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    previous_leg: Mapping[str, Any],
    timeframe_seconds: int,
    room_ok: bool | None,
) -> dict[str, Any]:
    if not candles:
        return dict(leg)
    updated = dict(leg)
    source = _text(updated.get("source"), "historical_structure")
    if "tracked_candles_tail" not in source:
        source = f"{source}+tracked_candles_tail"
    existing_indices = _int_list(updated.get("source_indices"))
    tail_indices = _leg_indices(candles)
    existing_index_set = set(existing_indices)
    added_count = sum(1 for index in tail_indices if index not in existing_index_set)
    candle_count = int(_float(updated.get("candle_count"), float(len(existing_indices)))) + added_count
    combined_indices = sorted(existing_index_set | set(tail_indices))
    side = _side(updated.get("side"), "HOLD")
    stage, stage_reason = _leg_stage(
        side=side,
        candle_count=candle_count,
        previous_side=_side(previous_leg.get("side"), "HOLD"),
        previous_candle_count=int(_float(previous_leg.get("candle_count"), 0.0)),
        room_ok=room_ok,
        exhaustion_risk=0.0,
    )
    updated.update(
        {
            "source": source,
            "candle_count": candle_count,
            "duration": _duration_payload(candle_count, timeframe_seconds),
            "start_index": min(combined_indices) if combined_indices else updated.get("start_index"),
            "end_index": max(combined_indices) if combined_indices else updated.get("end_index"),
            "source_indices": combined_indices,
            "move_stage": stage,
            "stage_reason": stage_reason,
        }
    )
    return updated


def _tracked_tail_leg(
    candles: Sequence[Mapping[str, Any]],
    *,
    position: int,
    previous_leg: Mapping[str, Any],
    timeframe_seconds: int,
    room_ok: bool | None,
) -> dict[str, Any]:
    side = _leg_side(candles)
    indices = _leg_indices(candles)
    candle_count = len(candles)
    stage, stage_reason = _leg_stage(
        side=side,
        candle_count=candle_count,
        previous_side=_side(previous_leg.get("side"), "HOLD"),
        previous_candle_count=int(_float(previous_leg.get("candle_count"), 0.0)),
        room_ok=room_ok,
        exhaustion_risk=0.0,
    )
    first = candles[0]
    last = candles[-1]
    confirmation_count = sum(1 for candle in candles if _side(candle.get("direction"), "HOLD") == side)
    return {
        "source": "tracked_candles_tail",
        "source_path": f"tracking_summary.tracked_candles.reconciled_leg[{position}]",
        "label": f"L{position + 1} {side}",
        "side": side,
        "candle_count": candle_count,
        "duration": _duration_payload(candle_count, timeframe_seconds),
        "start_index": min(indices) if indices else None,
        "end_index": max(indices) if indices else None,
        "source_indices": indices,
        "net_move": round(_float(last.get("price_proxy"), 0.0) - _float(first.get("price_proxy"), 0.0), 6),
        "slope": round(
            (_float(last.get("price_proxy"), 0.0) - _float(first.get("price_proxy"), 0.0))
            / max(1, candle_count - 1),
            6,
        ),
        "move_stage": stage,
        "stage_reason": stage_reason,
        "confirmation_count": confirmation_count,
        "confirmation_required": REVERSAL_CONFIRMATION_CANDLES,
        "transition_state": "CONFIRMED",
    }


def _transition_leg(
    candles: Sequence[Mapping[str, Any]],
    *,
    position: int,
    previous_leg: Mapping[str, Any],
    timeframe_seconds: int,
) -> dict[str, Any]:
    candidate_side = _leg_side(candles)
    indices = _leg_indices(candles)
    confirmation_count = sum(
        1 for candle in candles if _side(candle.get("direction"), "HOLD") == candidate_side
    )
    return {
        "source": "tracked_candles_tail",
        "source_path": f"tracking_summary.tracked_candles.transition[{position}]",
        "label": f"L{position + 1} {candidate_side} TRANSITION",
        "side": "HOLD",
        "candidate_side": candidate_side,
        "previous_side": _side(previous_leg.get("side"), "HOLD"),
        "candle_count": len(candles),
        "duration": _duration_payload(len(candles), timeframe_seconds),
        "start_index": min(indices) if indices else None,
        "end_index": max(indices) if indices else None,
        "source_indices": indices,
        "net_move": round(
            _float(candles[-1].get("price_proxy"), 0.0) - _float(candles[0].get("price_proxy"), 0.0),
            6,
        ),
        "slope": round(
            (_float(candles[-1].get("price_proxy"), 0.0) - _float(candles[0].get("price_proxy"), 0.0))
            / max(1, len(candles) - 1),
            6,
        ),
        "move_stage": "TRANSITION",
        "stage_reason": (
            f"Fresh {candidate_side} reversal has {confirmation_count}/"
            f"{REVERSAL_CONFIRMATION_CANDLES} confirming candles; current pressure is held neutral."
        ),
        "confirmation_count": confirmation_count,
        "confirmation_required": REVERSAL_CONFIRMATION_CANDLES,
        "transition_state": "FORMING",
    }


def _reconcile_historical_tail(
    historical_legs: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    *,
    timeframe_seconds: int,
    room_ok: bool | None,
) -> list[dict[str, Any]]:
    """Reconcile lagging historical segments with fresh tracked-candle evidence.

    Historical structure uses a three-candle minimum before accepting a new
    market leg. Until that threshold is met, an opposite tail is represented as
    TRANSITION/HOLD instead of leaving the completed historical side current.
    """
    reconciled = [dict(leg) for leg in historical_legs]
    if not reconciled or not candles:
        return reconciled
    historical_end = reconciled[-1].get("end_index")
    if historical_end is None:
        return reconciled
    historical_end_index = int(_float(historical_end, -1.0))
    tail = [
        candle
        for candle in candles
        if int(_float(candle.get("index", candle.get("position")), -1.0)) > historical_end_index
    ]
    if not tail:
        return reconciled

    pending_reversal: list[Mapping[str, Any]] = []
    for candle in tail:
        active_leg = reconciled[-1]
        active_side = _side(active_leg.get("side"), "HOLD")
        candle_side = _side(candle.get("direction"), "HOLD")
        previous_leg = reconciled[-2] if len(reconciled) > 1 else {}
        if candle_side == "HOLD":
            if pending_reversal:
                pending_reversal.append(candle)
            else:
                reconciled[-1] = _refresh_leg_with_tail(
                    active_leg,
                    [candle],
                    previous_leg=previous_leg,
                    timeframe_seconds=timeframe_seconds,
                    room_ok=room_ok,
                )
            continue
        if active_side not in {"BUY", "SELL"} or candle_side == active_side:
            absorbed = [*pending_reversal, candle]
            pending_reversal = []
            reconciled[-1] = _refresh_leg_with_tail(
                active_leg,
                absorbed,
                previous_leg=previous_leg,
                timeframe_seconds=timeframe_seconds,
                room_ok=room_ok,
            )
            continue

        pending_reversal.append(candle)
        confirmation_count = sum(
            1
            for pending in pending_reversal
            if _side(pending.get("direction"), "HOLD") == candle_side
        )
        if confirmation_count >= REVERSAL_CONFIRMATION_CANDLES:
            reconciled.append(
                _tracked_tail_leg(
                    pending_reversal,
                    position=len(reconciled),
                    previous_leg=active_leg,
                    timeframe_seconds=timeframe_seconds,
                    room_ok=room_ok,
                )
            )
            pending_reversal = []

    if pending_reversal:
        reconciled.append(
            _transition_leg(
                pending_reversal,
                position=len(reconciled),
                previous_leg=reconciled[-1],
                timeframe_seconds=timeframe_seconds,
            )
        )
    return reconciled


def _selected_timeframe(source: Mapping[str, Any]) -> tuple[str, int]:
    tracking = _mapping(source.get("tracking_summary")) or _mapping(source)
    timeframe = _upper(
        tracking.get("detected_timeframe")
        or tracking.get("timeframe")
        or source.get("timeframe")
        or source.get("focus_timeframe")
        or _mapping(source.get("instrument_context")).get("timeframe"),
        "M5",
    )
    return timeframe, TIMEFRAME_SECONDS.get(timeframe, 0)


def build_candle_movement_context_v3(source: Mapping[str, Any]) -> dict[str, Any]:
    """Build the candle-count evidence used by decision packages and burns."""
    existing = _mapping(source.get("candle_movement_context_v3") or source.get("candle_movement_context"))
    if existing.get("schema_version") == SCHEMA_VERSION:
        return existing

    tracking = _mapping(source.get("tracking_summary")) or _mapping(source)
    timeframe, timeframe_seconds = _selected_timeframe(source)
    candles = _candle_rows(source)
    visible_count = int(
        _float(
            tracking.get("visible_candle_count")
            or source.get("visible_candle_count")
            or len(candles),
            float(len(candles)),
        )
    )
    candidate_side = _side(
        source.get("candidate_side")
        or tracking.get("execution_action")
        or tracking.get("candidate_action")
        or tracking.get("global_direction")
        or tracking.get("local_direction"),
        "HOLD",
    )
    opposing_room = _opposing_force_room(source, candidate_side, visible_count)
    legs = _historical_legs(source, candles, timeframe_seconds, bool(opposing_room.get("room_ok")))
    current_leg = legs[-1] if legs else {}
    box_counts = [
        _box_payload(row, source_path=source_path, candles=candles)
        for source_path, row in _dedupe_boxes(_collect_box_rows(source))
        if _box_is_relevant(row)
    ]
    current_stage = _upper(current_leg.get("move_stage"), "CHOP")
    current_count = int(_float(current_leg.get("candle_count"), 0.0))
    previous_leg = legs[-2] if len(legs) > 1 else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "timeframe": timeframe,
        "timeframe_seconds": timeframe_seconds,
        "visible_candle_count": visible_count,
        "tracked_candle_count": len(candles),
        "buy_candle_count": _count_candle_color(candles, "BUY"),
        "sell_candle_count": _count_candle_color(candles, "SELL"),
        "hold_candle_count": _count_candle_color(candles, "HOLD"),
        "current_leg": {
            **current_leg,
            "candle_count": current_count,
            "duration": _duration_payload(current_count, timeframe_seconds),
            "move_stage": current_stage,
            "opposing_force_room": opposing_room,
        },
        "previous_leg": previous_leg,
        "move_stage": current_stage,
        "move_stage_reason": _text(current_leg.get("stage_reason"), "No current leg available."),
        "move_duration": _duration_payload(current_count, timeframe_seconds),
        "opposing_force_room": opposing_room,
        "legs": legs,
        "candles_per_leg": [
            {
                "label": leg.get("label"),
                "side": leg.get("side"),
                "candle_count": leg.get("candle_count"),
                "duration": leg.get("duration"),
                "move_stage": leg.get("move_stage"),
            }
            for leg in legs
        ],
        "box_candle_counts": box_counts,
        "boxes_with_candles": sum(1 for row in box_counts if int(row.get("contained_candle_count", 0) or 0) > 0),
        "boxes_with_anchors": sum(1 for row in box_counts if int(row.get("anchor_candle_count", 0) or 0) > 0),
        "summary": (
            f"{visible_count} visible candles; current {current_leg.get('side', 'HOLD')} leg has "
            f"{current_count} candles over {_duration_payload(current_count, timeframe_seconds)['text']}; "
            f"stage={current_stage}; opposing_room={'YES' if opposing_room.get('room_ok') else 'NO'}."
        ),
    }
