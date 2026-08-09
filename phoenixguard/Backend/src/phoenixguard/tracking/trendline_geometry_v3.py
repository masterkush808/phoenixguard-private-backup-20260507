from __future__ import annotations

import math
from statistics import median
from typing import Any, Mapping, Sequence


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _points(value: Any) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    rows: list[list[float]] = []
    for raw in value:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or len(raw) < 2:
            return []
        x = _number(raw[0])
        y = _number(raw[1])
        if x is None or y is None:
            return []
        rows.append([x, y])
    return rows


def _bounds(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) < 4:
        return None
    parsed = [_number(item) for item in value[:4]]
    if any(item is None for item in parsed):
        return None
    left, top, right, bottom = (float(item) for item in parsed if item is not None)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _first_number(row: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "yes", "1", "closed", "complete", "completed"}:
            return True
        if token in {"false", "no", "0", "forming", "open", "in_progress"}:
            return False
    return None


def _candle_is_closed(candle: Mapping[str, Any], index: int, count: int) -> bool:
    for key in ("is_closed", "closed", "is_complete", "complete", "completed"):
        if key in candle:
            parsed = _flag(candle.get(key))
            if parsed is not None:
                return parsed
    for key in ("is_forming", "forming", "in_progress"):
        if key in candle:
            parsed = _flag(candle.get(key))
            if parsed is not None:
                return not parsed
    return index < count - 1


def _candle_geometry(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    count = len(candles)
    for index, candle in enumerate(candles):
        if not isinstance(candle, Mapping):
            continue
        box = _bounds(candle.get("bbox") or candle.get("bounds") or candle.get("box"))
        if box is not None:
            left, top, right, bottom = box
            center_x = _first_number(candle, ("center_x", "x_center", "x"))
            if center_x is None:
                center_x = (left + right) / 2.0
        else:
            center_x = _first_number(candle, ("center_x", "x_center", "x"))
            top = _first_number(candle, ("wick_top_px", "wick_top_y", "top"))
            bottom = _first_number(candle, ("wick_bottom_px", "wick_bottom_y", "bottom"))
            width = _first_number(candle, ("width", "body_width")) or 2.0
            if center_x is None or top is None or bottom is None or bottom <= top:
                continue
            left = center_x - width / 2.0
            right = center_x + width / 2.0
        if not (left <= center_x <= right) or bottom <= top:
            continue
        rows.append(
            {
                "index": index,
                "center_x": float(center_x),
                "top": float(top),
                "bottom": float(bottom),
                "left": float(left),
                "right": float(right),
                "width": float(right - left),
                "closed": _candle_is_closed(candle, index, count),
            }
        )
    return rows


def _geometry_bounds(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float] | None:
    if not rows:
        return None
    return (
        min(float(row["left"]) for row in rows),
        min(float(row["top"]) for row in rows),
        max(float(row["right"]) for row in rows),
        max(float(row["bottom"]) for row in rows),
    )


def _wick_edge(trendline: Mapping[str, Any]) -> str | None:
    token = " ".join(
        str(trendline.get(key) or "")
        for key in ("direction", "role", "trendline_role", "type", "label")
    ).upper()
    if "RESISTANCE" in token or "SELL" in token:
        return "top"
    if "SUPPORT" in token or "BUY" in token:
        return "bottom"
    return None


def _anchor_match(
    point: Sequence[float],
    *,
    wick_edge: str,
    candles: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if not candles:
        return None
    widths = [float(row["width"]) for row in candles if float(row["width"]) > 0.0]
    ordered_centers = sorted({float(row["center_x"]) for row in candles})
    gaps = [
        ordered_centers[index] - ordered_centers[index - 1]
        for index in range(1, len(ordered_centers))
        if ordered_centers[index] > ordered_centers[index - 1]
    ]
    median_width = median(widths) if widths else 2.0
    median_gap = median(gaps) if gaps else median_width * 2.0
    x_tolerance = max(1.5, min(8.0, max(median_width * 0.8, median_gap * 0.4)))
    y_tolerance = max(1.5, min(6.0, median_width * 0.75))
    nearest = min(candles, key=lambda row: abs(float(row["center_x"]) - float(point[0])))
    if abs(float(nearest["center_x"]) - float(point[0])) > x_tolerance:
        return None
    if not bool(nearest["closed"]):
        return None
    expected_y = float(nearest[wick_edge])
    if abs(expected_y - float(point[1])) > y_tolerance:
        return None
    return nearest


def _rejected(
    trendline: Mapping[str, Any],
    *,
    status: str,
    reason: str,
    chart_bounds: tuple[float, float, float, float] | None,
) -> dict[str, Any]:
    row = dict(trendline)
    row.update(
        {
            "geometry_status": status,
            "geometry_contract_accepted": False,
            "geometry_contract_reason": reason,
            "breach_state": "GEOMETRY_INVALID",
            "lifecycle_state": "REJECTED",
            "visible_modes": [],
        }
    )
    if chart_bounds is not None:
        row["chart_bounds"] = [round(value, 6) for value in chart_bounds]
    return row


def normalize_trendline_geometry_v3(
    trendline: Mapping[str, Any],
    *,
    chart_bounds: tuple[float, float, float, float] | None,
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    row = dict(trendline)
    anchors = _points(row.get("anchor_wick_points"))
    if chart_bounds is None:
        return _rejected(
            row,
            status="REJECTED_UNRESOLVED_CHART_BOUNDS",
            reason="accepted candle geometry did not establish a chart domain",
            chart_bounds=None,
        )
    if len(anchors) < 2:
        return _rejected(
            row,
            status="REJECTED_MISSING_WICK_ANCHORS",
            reason="two canonical wick anchors are required",
            chart_bounds=chart_bounds,
        )
    first = anchors[0]
    second = anchors[1]
    left, top, right, bottom = chart_bounds
    tolerance = 0.75
    if any(
        point[0] < left - tolerance
        or point[0] > right + tolerance
        or point[1] < top - tolerance
        or point[1] > bottom + tolerance
        for point in (first, second)
    ):
        return _rejected(
            row,
            status="REJECTED_ANCHOR_OUTSIDE_ACCEPTED_CHART",
            reason="a canonical anchor is outside the current accepted candle domain",
            chart_bounds=chart_bounds,
        )
    wick_edge = _wick_edge(row)
    if wick_edge is None:
        return _rejected(
            row,
            status="REJECTED_UNRESOLVED_WICK_SIDE",
            reason="trendline does not declare support or resistance wick semantics",
            chart_bounds=chart_bounds,
        )
    first_match = _anchor_match(first, wick_edge=wick_edge, candles=candles)
    second_match = _anchor_match(second, wick_edge=wick_edge, candles=candles)
    if first_match is None or second_match is None:
        return _rejected(
            row,
            status="REJECTED_ANCHOR_WITHOUT_CLOSED_WICK_PROOF",
            reason="each anchor must match the declared wick edge of a closed candle in this frame",
            chart_bounds=chart_bounds,
        )
    first_index = int(first_match["index"])
    second_index = int(second_match["index"])
    if first_index == second_index or second[0] <= first[0]:
        return _rejected(
            row,
            status="REJECTED_NON_DISTINCT_ANCHORS",
            reason="anchors must belong to two distinct forward-ordered closed candles",
            chart_bounds=chart_bounds,
        )
    dx = second[0] - first[0]
    if abs(dx) <= 1e-9:
        return _rejected(
            row,
            status="REJECTED_VERTICAL_TRENDLINE",
            reason="vertical geometry is not a time-progressing trendline",
            chart_bounds=chart_bounds,
        )
    touch_points = _points(row.get("touch_points"))
    matched_touch_points = [first, second]
    matched_touch_indices = [first_index, second_index]
    for point in touch_points:
        match = _anchor_match(point, wick_edge=wick_edge, candles=candles)
        if match is None:
            continue
        match_index = int(match["index"])
        if match_index in matched_touch_indices:
            continue
        matched_touch_indices.append(match_index)
        matched_touch_points.append(point)
    slope = (second[1] - first[1]) / dx
    raw_projection = [right, first[1] + slope * (right - first[0])]
    current_projection_visible = top <= raw_projection[1] <= bottom
    normalized_points = [first, second]
    extension_omitted = True
    geometry_status = "ANCHORS_VALID_EXTENSION_OUTSIDE_CHART"
    if right <= second[0] + tolerance:
        geometry_status = "ANCHORS_VALID_AT_LATEST_X"
    elif current_projection_visible:
        normalized_points.append(raw_projection)
        extension_omitted = False
        geometry_status = "VISIBLE_TO_LATEST_X"
    xs = [point[0] for point in normalized_points]
    ys = [point[1] for point in normalized_points]
    extension_end = normalized_points[-1]
    row.update(
        {
            "points": [[round(x, 6), round(y, 6)] for x, y in normalized_points],
            "line_points": [[round(x, 6), round(y, 6)] for x, y in normalized_points],
            "anchor_wick_points": [first, second],
            "anchor_candle_indices": [first_index, second_index],
            "touch_points": matched_touch_points,
            "touch_candle_indices": matched_touch_indices,
            "touch_count": len(matched_touch_indices),
            "bounds": [
                round(min(xs), 6),
                round(min(ys), 6),
                round(max(xs), 6),
                round(max(ys), 6),
            ],
            "geometry_status": geometry_status,
            "geometry_contract_accepted": True,
            "geometry_contract_reason": "two closed-candle wick anchors verified in the accepted frame",
            "extension_clipped": False,
            "extension_omitted": extension_omitted,
            "current_projection_visible": current_projection_visible,
            "current_projection_x": round(right, 6),
            "visible_extension_end": [round(extension_end[0], 6), round(extension_end[1], 6)],
            "raw_projection_end": [round(raw_projection[0], 6), round(raw_projection[1], 6)],
            "chart_bounds": [round(left, 6), round(top, 6), round(right, 6), round(bottom, 6)],
            "coordinate_space": "chart",
            "coordinate_units": "pixels",
        }
    )
    return row


def normalize_trendline_overlays_v3(
    trendlines: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candle_rows = _candle_geometry(candles)
    chart_bounds = _geometry_bounds(candle_rows)
    normalized = [
        normalize_trendline_geometry_v3(
            row,
            chart_bounds=chart_bounds,
            candles=candle_rows,
        )
        for row in trendlines
    ]
    return [row for row in normalized if row.get("geometry_contract_accepted") is True]


__all__ = [
    "normalize_trendline_geometry_v3",
    "normalize_trendline_overlays_v3",
]