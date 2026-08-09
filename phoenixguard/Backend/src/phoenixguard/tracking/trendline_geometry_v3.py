from __future__ import annotations

import math
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


def _candle_bounds(candles: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float] | None:
    boxes: list[tuple[float, float, float, float]] = []
    for candle in candles:
        box = _bounds(candle.get("bbox") or candle.get("bounds") or candle.get("box"))
        if box is not None:
            boxes.append(box)
            continue
        center_x = _number(candle.get("center_x") or candle.get("x_center") or candle.get("x"))
        top = _number(
            candle.get("wick_top_px")
            or candle.get("wick_top_y")
            or candle.get("top")
        )
        bottom = _number(
            candle.get("wick_bottom_px")
            or candle.get("wick_bottom_y")
            or candle.get("bottom")
        )
        width = _number(candle.get("width") or candle.get("body_width")) or 2.0
        if center_x is not None and top is not None and bottom is not None and bottom > top:
            boxes.append((center_x - width / 2.0, top, center_x + width / 2.0, bottom))
    if not boxes:
        return None
    return (
        min(row[0] for row in boxes),
        min(row[1] for row in boxes),
        max(row[2] for row in boxes),
        max(row[3] for row in boxes),
    )


def _clip_segment(
    start: Sequence[float],
    end: Sequence[float],
    rectangle: tuple[float, float, float, float],
) -> tuple[list[float], list[float]] | None:
    left, top, right, bottom = rectangle
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    dx = x1 - x0
    dy = y1 - y0
    lower = 0.0
    upper = 1.0
    for coefficient, distance in (
        (-dx, x0 - left),
        (dx, right - x0),
        (-dy, y0 - top),
        (dy, bottom - y0),
    ):
        if abs(coefficient) <= 1e-12:
            if distance < 0.0:
                return None
            continue
        ratio = distance / coefficient
        if coefficient < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return None
    return (
        [x0 + lower * dx, y0 + lower * dy],
        [x0 + upper * dx, y0 + upper * dy],
    )


def normalize_trendline_geometry_v3(
    trendline: Mapping[str, Any],
    *,
    chart_bounds: tuple[float, float, float, float] | None,
) -> dict[str, Any]:
    row = dict(trendline)
    anchors = _points(row.get("anchor_wick_points"))
    line_points = _points(row.get("line_points") or row.get("points"))
    if len(anchors) < 2 or len(line_points) < 2 or chart_bounds is None:
        row["geometry_status"] = "UNRESOLVED_CHART_BOUNDS"
        return row
    first = anchors[0]
    second = anchors[1]
    left, top, right, bottom = chart_bounds
    dx = second[0] - first[0]
    if abs(dx) <= 1e-9:
        row["geometry_status"] = "REJECTED_VERTICAL_TRENDLINE"
        row["breach_state"] = "GEOMETRY_INVALID"
        return row
    slope = (second[1] - first[1]) / dx
    raw_projection = [right, first[1] + slope * (right - first[0])]
    current_projection_visible = top <= raw_projection[1] <= bottom
    clipped = _clip_segment(second, raw_projection, chart_bounds)
    extension_end = second
    if clipped is not None:
        extension_end = clipped[1]
    normalized_points = [first, second]
    if math.dist(second, extension_end) > 0.75:
        normalized_points.append(extension_end)
    xs = [point[0] for point in normalized_points]
    ys = [point[1] for point in normalized_points]
    row.update(
        {
            "points": [[round(x, 6), round(y, 6)] for x, y in normalized_points],
            "line_points": [[round(x, 6), round(y, 6)] for x, y in normalized_points],
            "anchor_wick_points": [first, second],
            "bounds": [
                round(max(left, min(xs)), 6),
                round(max(top, min(ys)), 6),
                round(min(right, max(xs)), 6),
                round(min(bottom, max(ys)), 6),
            ],
            "geometry_status": "CLIPPED_TO_VISIBLE_CHART" if not current_projection_visible else "VISIBLE_TO_LATEST_X",
            "extension_clipped": not current_projection_visible,
            "current_projection_visible": current_projection_visible,
            "current_projection_x": round(right, 6),
            "visible_extension_end": [round(extension_end[0], 6), round(extension_end[1], 6)],
            "chart_bounds": [round(left, 6), round(top, 6), round(right, 6), round(bottom, 6)],
        }
    )
    return row


def normalize_trendline_overlays_v3(
    trendlines: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    chart_bounds = _candle_bounds(candles)
    return [
        normalize_trendline_geometry_v3(row, chart_bounds=chart_bounds)
        for row in trendlines
    ]


__all__ = [
    "normalize_trendline_geometry_v3",
    "normalize_trendline_overlays_v3",
]
