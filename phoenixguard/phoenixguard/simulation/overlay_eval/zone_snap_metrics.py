from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any, Mapping, Sequence, cast

from phoenixguard.vision.overlay_geometry import bbox_area, clip_bbox_to_bounds, normalize_bbox

from .box_metrics import BBox, DEFAULT_ID_KEYS


Anchor = tuple[float | None, float]


@dataclass(frozen=True, slots=True)
class ZoneAnchorMetric:
    zone_id: str
    anchoring_score: float
    anchor_count: int
    best_anchor_x: float | None
    best_anchor_y: float | None
    best_anchor_distance_px: float
    clipped_area_ratio: float
    clipped: bool
    bbox: BBox | None
    evaluated_bbox: BBox | None


@dataclass(frozen=True, slots=True)
class ZoneSnapMetrics:
    zone_count: int
    valid_zone_count: int
    invalid_zone_count: int
    anchored_count: int
    unanchored_count: int
    clipped_count: int
    mean_anchoring_score: float
    min_anchoring_score: float
    metrics: tuple[ZoneAnchorMetric, ...]


def _round(value: float) -> float:
    return round(float(value), 6)


def _mean(values: Sequence[float]) -> float:
    return _round(sum(values) / len(values)) if values else 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _bbox_tuple(value: Sequence[Any]) -> BBox | None:
    bbox = normalize_bbox(value)
    if bbox is None:
        return None
    return cast(BBox, tuple(float(part) for part in bbox[:4]))


def _bbox_from_zone(zone: Any) -> Sequence[Any]:
    if isinstance(zone, Mapping):
        row = cast(Mapping[str, object], zone)
        value = row.get("bbox", ())
        return cast(Sequence[Any], value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()
    return cast(Sequence[Any], zone) if isinstance(zone, Sequence) and not isinstance(zone, (str, bytes, bytearray)) else ()


def _identity(item: Any, index: int, id_keys: Sequence[str]) -> str:
    if isinstance(item, Mapping):
        row = cast(Mapping[str, Any], item)
        for key in id_keys:
            value = row.get(key)
            if str(value or "").strip():
                return str(value).strip()
    return f"zone_{index}"


def _point_anchor(value: Any) -> Anchor | None:
    if isinstance(value, Mapping):
        row = cast(Mapping[str, Any], value)
        y_value = row.get("y", row.get("line_y", row.get("anchor_y", row.get("price_y"))))
        if y_value is None:
            return None
        x_value = row.get("x", row.get("center_x", row.get("anchor_x")))
        return (_safe_float(x_value) if x_value is not None else None, _safe_float(y_value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        point = cast(Sequence[object], value)
        if len(point) >= 2:
            return (_safe_float(point[0]), _safe_float(point[1]))
    if isinstance(value, (int, float)):
        return (None, _safe_float(value))
    return None


def extract_zone_anchors(
    zone: Mapping[str, Any],
    *,
    anchor_points: Sequence[Any] = (),
) -> tuple[Anchor, ...]:
    anchors: list[Anchor] = []
    for key in ("line_y", "anchor_y", "price_y", "entry_y", "trigger_y"):
        if key in zone:
            anchors.append((None, _safe_float(zone.get(key))))
    for key in ("anchor_point", "start_point", "end_point", "latest_point"):
        anchor = _point_anchor(zone.get(key))
        if anchor is not None:
            anchors.append(anchor)
    for key in ("touch_points", "anchor_points", "path"):
        values = zone.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            continue
        for item in cast(Sequence[object], values):
            anchor = _point_anchor(item)
            if anchor is not None:
                anchors.append(anchor)
    for item in anchor_points:
        anchor = _point_anchor(item)
        if anchor is not None:
            anchors.append(anchor)
    return tuple(anchors)


def _axis_distance(value: float, low: float, high: float) -> float:
    if low <= value <= high:
        return 0.0
    return float(low - value if value < low else value - high)


def _axis_score(value: float, low: float, high: float) -> float:
    span = max(1.0, high - low)
    distance = _axis_distance(value, low, high)
    return _round(max(0.0, min(1.0, 1.0 - distance / span)))


def _score_anchor(bbox: BBox, anchor: Anchor) -> tuple[float, float]:
    x_value, y_value = anchor
    y_score = _axis_score(y_value, bbox[1], bbox[3])
    y_distance = _axis_distance(y_value, bbox[1], bbox[3])
    if x_value is None:
        return y_score, _round(y_distance)
    x_score = _axis_score(x_value, bbox[0], bbox[2])
    x_distance = _axis_distance(x_value, bbox[0], bbox[2])
    return _round(x_score * y_score), _round(hypot(x_distance, y_distance))


def score_zone_anchor(
    zone: Mapping[str, Any],
    *,
    chart_bounds: Sequence[Any] | None = None,
    anchor_points: Sequence[Any] = (),
    zone_id: str | None = None,
) -> ZoneAnchorMetric:
    raw_bbox = _bbox_tuple(_bbox_from_zone(zone))
    anchors = extract_zone_anchors(zone, anchor_points=anchor_points)
    if raw_bbox is None:
        return ZoneAnchorMetric(
            zone_id=zone_id or str(zone.get("id") or zone.get("key") or "zone"),
            anchoring_score=0.0,
            anchor_count=len(anchors),
            best_anchor_x=None,
            best_anchor_y=None,
            best_anchor_distance_px=0.0,
            clipped_area_ratio=0.0,
            clipped=False,
            bbox=None,
            evaluated_bbox=None,
        )

    evaluated_bbox = raw_bbox
    clipped_area_ratio = 0.0
    if chart_bounds is not None:
        clipped = clip_bbox_to_bounds(raw_bbox, chart_bounds)
        raw_area = max(1.0, bbox_area(raw_bbox))
        if clipped is None:
            return ZoneAnchorMetric(
                zone_id=zone_id or str(zone.get("id") or zone.get("key") or "zone"),
                anchoring_score=0.0,
                anchor_count=len(anchors),
                best_anchor_x=None,
                best_anchor_y=None,
                best_anchor_distance_px=0.0,
                clipped_area_ratio=1.0,
                clipped=True,
                bbox=raw_bbox,
                evaluated_bbox=None,
            )
        evaluated_bbox = cast(BBox, tuple(float(part) for part in clipped[:4]))
        clipped_area_ratio = _round(max(0.0, min(1.0, 1.0 - (bbox_area(evaluated_bbox) / raw_area))))

    best_anchor: Anchor | None = None
    best_score = 0.0
    best_distance = 0.0
    for anchor in anchors:
        score, distance = _score_anchor(evaluated_bbox, anchor)
        if score > best_score or (score == best_score and (best_anchor is None or distance < best_distance)):
            best_score = score
            best_distance = distance
            best_anchor = anchor

    return ZoneAnchorMetric(
        zone_id=zone_id or str(zone.get("id") or zone.get("key") or "zone"),
        anchoring_score=_round(best_score),
        anchor_count=len(anchors),
        best_anchor_x=None if best_anchor is None else best_anchor[0],
        best_anchor_y=None if best_anchor is None else _round(best_anchor[1]),
        best_anchor_distance_px=_round(best_distance),
        clipped_area_ratio=clipped_area_ratio,
        clipped=clipped_area_ratio > 0.0,
        bbox=raw_bbox,
        evaluated_bbox=evaluated_bbox,
    )


def zone_anchoring_score(
    zone: Mapping[str, Any],
    *,
    chart_bounds: Sequence[Any] | None = None,
    anchor_points: Sequence[Any] = (),
) -> float:
    return score_zone_anchor(zone, chart_bounds=chart_bounds, anchor_points=anchor_points).anchoring_score


def evaluate_zone_snap_metrics(
    zones: Sequence[Mapping[str, Any]],
    *,
    chart_bounds: Sequence[Any] | None = None,
    minimum_score: float = 0.72,
    anchor_points: Sequence[Any] = (),
    id_keys: Sequence[str] = DEFAULT_ID_KEYS,
) -> ZoneSnapMetrics:
    zone_items = tuple(zones)
    metrics = tuple(
        score_zone_anchor(
            zone,
            chart_bounds=chart_bounds,
            anchor_points=anchor_points,
            zone_id=_identity(zone, index, id_keys),
        )
        for index, zone in enumerate(zone_items)
    )
    valid_metrics = tuple(metric for metric in metrics if metric.evaluated_bbox is not None)
    anchored_count = sum(1 for metric in metrics if metric.anchoring_score >= max(0.0, min(1.0, float(minimum_score))))
    scores = [metric.anchoring_score for metric in valid_metrics]
    return ZoneSnapMetrics(
        zone_count=len(zone_items),
        valid_zone_count=len(valid_metrics),
        invalid_zone_count=len(zone_items) - len(valid_metrics),
        anchored_count=anchored_count,
        unanchored_count=len(zone_items) - anchored_count,
        clipped_count=sum(1 for metric in metrics if metric.clipped),
        mean_anchoring_score=_mean(scores),
        min_anchoring_score=_round(min(scores)) if scores else 0.0,
        metrics=metrics,
    )
