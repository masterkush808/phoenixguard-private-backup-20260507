from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any, Mapping, Sequence, cast

from phoenixguard.vision.overlay_geometry import bbox_area, bbox_iou, clip_bbox_to_bounds, normalize_bbox


BBox = tuple[float, float, float, float]
DEFAULT_ID_KEYS: tuple[str, ...] = ("id", "key", "label", "role", "kind")


@dataclass(frozen=True, slots=True)
class BoxMetric:
    prediction_id: str
    truth_id: str
    iou: float
    center_distance_px: float
    center_distance_norm: float
    area_error_ratio: float
    clipped_area_ratio: float
    prediction_clipped: bool
    prediction_bbox: BBox
    evaluated_prediction_bbox: BBox
    truth_bbox: BBox


@dataclass(frozen=True, slots=True)
class TemporalJitterMetric:
    object_count: int
    mean_center_shift_px: float
    max_center_shift_px: float
    mean_center_shift_norm: float
    max_center_shift_norm: float


@dataclass(frozen=True, slots=True)
class BoxMetrics:
    prediction_count: int
    truth_count: int
    valid_prediction_count: int
    valid_truth_count: int
    invalid_prediction_count: int
    invalid_truth_count: int
    matched_count: int
    false_positive_count: int
    missed_count: int
    mean_iou: float
    mean_center_distance_px: float
    mean_center_distance_norm: float
    mean_area_error_ratio: float
    mean_clipped_area_ratio: float
    temporal_jitter: TemporalJitterMetric
    matches: tuple[BoxMetric, ...]


@dataclass(frozen=True, slots=True)
class _BoxRecord:
    item_id: str
    raw_bbox: BBox
    evaluated_bbox: BBox
    clipped_area_ratio: float
    clipped: bool


def _round(value: float) -> float:
    return round(float(value), 6)


def _mean(values: Sequence[float]) -> float:
    return _round(sum(values) / len(values)) if values else 0.0


def _bbox_tuple(value: Sequence[Any]) -> BBox | None:
    bbox = normalize_bbox(value)
    if bbox is None:
        return None
    return cast(BBox, tuple(float(part) for part in bbox[:4]))


def _bbox_from_item(item: Any) -> Sequence[Any]:
    if isinstance(item, Mapping):
        row = cast(Mapping[str, object], item)
        value = row.get("bbox", ())
        return cast(Sequence[Any], value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()
    return cast(Sequence[Any], item) if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) else ()


def _identity(item: Any, index: int, prefix: str, id_keys: Sequence[str]) -> str:
    if isinstance(item, Mapping):
        row = cast(Mapping[str, Any], item)
        for key in id_keys:
            value = row.get(key)
            if str(value or "").strip():
                return str(value).strip()
    return f"{prefix}_{index}"


def _bounds_diagonal(chart_bounds: Sequence[Any] | None, fallback_box: BBox | None = None) -> float:
    bounds = _bbox_tuple(chart_bounds or ()) if chart_bounds is not None else None
    source = bounds or fallback_box
    if source is None:
        return 1.0
    return max(1.0, hypot(source[2] - source[0], source[3] - source[1]))


def _center(bbox: BBox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)


def _center_distance(first: BBox, second: BBox) -> float:
    first_center = _center(first)
    second_center = _center(second)
    return float(hypot(first_center[0] - second_center[0], first_center[1] - second_center[1]))


def _record_from_item(
    item: Any,
    index: int,
    *,
    prefix: str,
    chart_bounds: Sequence[Any] | None,
    id_keys: Sequence[str],
) -> _BoxRecord | None:
    raw_bbox = _bbox_tuple(_bbox_from_item(item))
    if raw_bbox is None:
        return None
    clipped_bbox = None
    clipped_area_ratio = 0.0
    if chart_bounds is not None:
        clipped = clip_bbox_to_bounds(raw_bbox, chart_bounds)
        raw_area = max(1.0, bbox_area(raw_bbox))
        if clipped is None:
            return None
        clipped_bbox = cast(BBox, tuple(float(part) for part in clipped[:4]))
        clipped_area_ratio = _round(max(0.0, min(1.0, 1.0 - (bbox_area(clipped_bbox) / raw_area))))
    evaluated_bbox = clipped_bbox or raw_bbox
    return _BoxRecord(
        item_id=_identity(item, index, prefix, id_keys),
        raw_bbox=raw_bbox,
        evaluated_bbox=evaluated_bbox,
        clipped_area_ratio=clipped_area_ratio,
        clipped=clipped_area_ratio > 0.0,
    )


def _records(
    items: Sequence[Any],
    *,
    prefix: str,
    chart_bounds: Sequence[Any] | None,
    id_keys: Sequence[str],
) -> tuple[tuple[_BoxRecord, ...], int]:
    records: list[_BoxRecord] = []
    invalid_count = 0
    for index, item in enumerate(items):
        record = _record_from_item(
            item,
            index,
            prefix=prefix,
            chart_bounds=chart_bounds,
            id_keys=id_keys,
        )
        if record is None:
            invalid_count += 1
        else:
            records.append(record)
    return tuple(records), invalid_count


def box_metric(
    prediction_bbox: Sequence[Any],
    truth_bbox: Sequence[Any],
    *,
    chart_bounds: Sequence[Any] | None = None,
    prediction_id: str = "prediction",
    truth_id: str = "truth",
) -> BoxMetric | None:
    prediction = _record_from_item(
        {"id": prediction_id, "bbox": prediction_bbox},
        0,
        prefix="prediction",
        chart_bounds=chart_bounds,
        id_keys=DEFAULT_ID_KEYS,
    )
    truth = _record_from_item(
        {"id": truth_id, "bbox": truth_bbox},
        0,
        prefix="truth",
        chart_bounds=chart_bounds,
        id_keys=DEFAULT_ID_KEYS,
    )
    if prediction is None or truth is None:
        return None
    return _pair_metric(prediction, truth, chart_bounds=chart_bounds)


def temporal_jitter(
    previous_boxes: Sequence[Any],
    current_boxes: Sequence[Any],
    *,
    chart_bounds: Sequence[Any] | None = None,
    id_keys: Sequence[str] = DEFAULT_ID_KEYS,
) -> TemporalJitterMetric:
    previous_records, _invalid_previous = _records(
        previous_boxes,
        prefix="previous",
        chart_bounds=chart_bounds,
        id_keys=id_keys,
    )
    current_records, _invalid_current = _records(
        current_boxes,
        prefix="current",
        chart_bounds=chart_bounds,
        id_keys=id_keys,
    )
    previous_by_id = {record.item_id: record.evaluated_bbox for record in previous_records}
    shifts: list[float] = []
    norm_shifts: list[float] = []
    for current in current_records:
        previous = previous_by_id.get(current.item_id)
        if previous is None:
            continue
        shift = _center_distance(previous, current.evaluated_bbox)
        diagonal = _bounds_diagonal(chart_bounds, current.evaluated_bbox)
        shifts.append(shift)
        norm_shifts.append(shift / diagonal)
    return TemporalJitterMetric(
        object_count=len(shifts),
        mean_center_shift_px=_mean(shifts),
        max_center_shift_px=_round(max(shifts)) if shifts else 0.0,
        mean_center_shift_norm=_mean(norm_shifts),
        max_center_shift_norm=_round(max(norm_shifts)) if norm_shifts else 0.0,
    )


def evaluate_box_metrics(
    predictions: Sequence[Any],
    truths: Sequence[Any],
    *,
    chart_bounds: Sequence[Any] | None = None,
    iou_threshold: float = 0.5,
    previous_predictions: Sequence[Any] = (),
    id_keys: Sequence[str] = DEFAULT_ID_KEYS,
) -> BoxMetrics:
    prediction_items = tuple(predictions)
    truth_items = tuple(truths)
    prediction_records, invalid_prediction_count = _records(
        prediction_items,
        prefix="prediction",
        chart_bounds=chart_bounds,
        id_keys=id_keys,
    )
    truth_records, invalid_truth_count = _records(
        truth_items,
        prefix="truth",
        chart_bounds=chart_bounds,
        id_keys=id_keys,
    )

    candidates: list[tuple[float, str, str, int, int]] = []
    for prediction_index, prediction in enumerate(prediction_records):
        for truth_index, truth in enumerate(truth_records):
            iou = bbox_iou(prediction.evaluated_bbox, truth.evaluated_bbox)
            if iou >= max(0.0, min(1.0, float(iou_threshold))):
                candidates.append((iou, prediction.item_id, truth.item_id, prediction_index, truth_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4]))

    used_predictions: set[int] = set()
    used_truths: set[int] = set()
    matches: list[BoxMetric] = []
    for _iou, _prediction_id, _truth_id, prediction_index, truth_index in candidates:
        if prediction_index in used_predictions or truth_index in used_truths:
            continue
        used_predictions.add(prediction_index)
        used_truths.add(truth_index)
        matches.append(
            _pair_metric(
                prediction_records[prediction_index],
                truth_records[truth_index],
                chart_bounds=chart_bounds,
            )
        )

    jitter = temporal_jitter(
        previous_predictions,
        prediction_items,
        chart_bounds=chart_bounds,
        id_keys=id_keys,
    )
    false_positive_count = len(prediction_records) - len(used_predictions) + invalid_prediction_count
    missed_count = len(truth_records) - len(used_truths)
    return BoxMetrics(
        prediction_count=len(prediction_items),
        truth_count=len(truth_items),
        valid_prediction_count=len(prediction_records),
        valid_truth_count=len(truth_records),
        invalid_prediction_count=invalid_prediction_count,
        invalid_truth_count=invalid_truth_count,
        matched_count=len(matches),
        false_positive_count=false_positive_count,
        missed_count=missed_count,
        mean_iou=_mean([match.iou for match in matches]),
        mean_center_distance_px=_mean([match.center_distance_px for match in matches]),
        mean_center_distance_norm=_mean([match.center_distance_norm for match in matches]),
        mean_area_error_ratio=_mean([match.area_error_ratio for match in matches]),
        mean_clipped_area_ratio=_mean([match.clipped_area_ratio for match in matches]),
        temporal_jitter=jitter,
        matches=tuple(matches),
    )


def _pair_metric(
    prediction: _BoxRecord,
    truth: _BoxRecord,
    *,
    chart_bounds: Sequence[Any] | None,
) -> BoxMetric:
    center_distance_px = _center_distance(prediction.evaluated_bbox, truth.evaluated_bbox)
    diagonal = _bounds_diagonal(chart_bounds, truth.evaluated_bbox)
    truth_area = max(1.0, bbox_area(truth.evaluated_bbox))
    area_error_ratio = abs(bbox_area(prediction.evaluated_bbox) - truth_area) / truth_area
    return BoxMetric(
        prediction_id=prediction.item_id,
        truth_id=truth.item_id,
        iou=_round(bbox_iou(prediction.evaluated_bbox, truth.evaluated_bbox)),
        center_distance_px=_round(center_distance_px),
        center_distance_norm=_round(center_distance_px / diagonal),
        area_error_ratio=_round(area_error_ratio),
        clipped_area_ratio=prediction.clipped_area_ratio,
        prediction_clipped=prediction.clipped,
        prediction_bbox=prediction.raw_bbox,
        evaluated_prediction_bbox=prediction.evaluated_bbox,
        truth_bbox=truth.evaluated_bbox,
    )
