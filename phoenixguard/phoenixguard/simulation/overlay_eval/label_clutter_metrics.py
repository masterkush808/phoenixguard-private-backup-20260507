from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

from phoenixguard.vision.overlay_geometry import (
    bbox_area,
    bbox_iou,
    bbox_overlap_ratio,
    clip_bbox_to_bounds,
    normalize_bbox,
)

from .box_metrics import BBox, DEFAULT_ID_KEYS


@dataclass(frozen=True, slots=True)
class LabelBox:
    label_id: str
    bbox: BBox
    evaluated_bbox: BBox | None
    clipped_area_ratio: float
    clipped: bool


@dataclass(frozen=True, slots=True)
class LabelOverlap:
    first_id: str
    second_id: str
    iou: float
    overlap_ratio: float
    intersection_area: float


@dataclass(frozen=True, slots=True)
class LabelClutterMetrics:
    label_count: int
    valid_label_count: int
    visible_label_count: int
    invalid_label_count: int
    clipped_count: int
    overlap_count: int
    mean_overlap_ratio: float
    max_overlap_ratio: float
    mean_clipped_area_ratio: float
    labels: tuple[LabelBox, ...]
    overlaps: tuple[LabelOverlap, ...]


def _round(value: float) -> float:
    return round(float(value), 6)


def _mean(values: Sequence[float]) -> float:
    return _round(sum(values) / len(values)) if values else 0.0


def _bbox_tuple(value: Sequence[Any]) -> BBox | None:
    bbox = normalize_bbox(value)
    if bbox is None:
        return None
    return cast(BBox, tuple(float(part) for part in bbox[:4]))


def _bbox_from_label(label: Any) -> Sequence[Any]:
    if isinstance(label, Mapping):
        value = label.get("bbox", label.get("label_bbox", ()))
        return cast(Sequence[Any], value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()
    return cast(Sequence[Any], label) if isinstance(label, Sequence) and not isinstance(label, (str, bytes, bytearray)) else ()


def _identity(item: Any, index: int, id_keys: Sequence[str]) -> str:
    if isinstance(item, Mapping):
        row = cast(Mapping[str, Any], item)
        for key in id_keys:
            value = row.get(key)
            if str(value or "").strip():
                return str(value).strip()
        text = row.get("text")
        if str(text or "").strip():
            return str(text).strip()
    return f"label_{index}"


def clip_label_box(label: Any, chart_bounds: Sequence[Any]) -> BBox | None:
    bbox = _bbox_tuple(_bbox_from_label(label))
    if bbox is None:
        return None
    clipped = clip_bbox_to_bounds(bbox, chart_bounds)
    if clipped is None:
        return None
    return cast(BBox, tuple(float(part) for part in clipped[:4]))


def _label_box(
    label: Any,
    index: int,
    *,
    chart_bounds: Sequence[Any] | None,
    id_keys: Sequence[str],
) -> LabelBox | None:
    bbox = _bbox_tuple(_bbox_from_label(label))
    if bbox is None:
        return None
    evaluated_bbox = bbox
    clipped_area_ratio = 0.0
    if chart_bounds is not None:
        clipped = clip_bbox_to_bounds(bbox, chart_bounds)
        raw_area = max(1.0, bbox_area(bbox))
        if clipped is None:
            return LabelBox(
                label_id=_identity(label, index, id_keys),
                bbox=bbox,
                evaluated_bbox=None,
                clipped_area_ratio=1.0,
                clipped=True,
            )
        evaluated_bbox = cast(BBox, tuple(float(part) for part in clipped[:4]))
        clipped_area_ratio = _round(max(0.0, min(1.0, 1.0 - (bbox_area(evaluated_bbox) / raw_area))))
    return LabelBox(
        label_id=_identity(label, index, id_keys),
        bbox=bbox,
        evaluated_bbox=evaluated_bbox,
        clipped_area_ratio=clipped_area_ratio,
        clipped=clipped_area_ratio > 0.0,
    )


def _intersection_area(first: BBox, second: BBox) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return 0.0
    return float((right - left) * (bottom - top))


def label_overlap_count(
    labels: Sequence[Any],
    *,
    chart_bounds: Sequence[Any] | None = None,
    overlap_threshold: float = 0.0,
    id_keys: Sequence[str] = DEFAULT_ID_KEYS,
) -> int:
    return evaluate_label_clutter_metrics(
        labels,
        chart_bounds=chart_bounds,
        overlap_threshold=overlap_threshold,
        id_keys=id_keys,
    ).overlap_count


def evaluate_label_clutter_metrics(
    labels: Sequence[Any],
    *,
    chart_bounds: Sequence[Any] | None = None,
    overlap_threshold: float = 0.0,
    id_keys: Sequence[str] = DEFAULT_ID_KEYS,
) -> LabelClutterMetrics:
    label_items = tuple(labels)
    valid_labels: list[LabelBox] = []
    invalid_count = 0
    for index, label in enumerate(label_items):
        label_box = _label_box(label, index, chart_bounds=chart_bounds, id_keys=id_keys)
        if label_box is None:
            invalid_count += 1
        else:
            valid_labels.append(label_box)

    visible_labels = [label for label in valid_labels if label.evaluated_bbox is not None]
    overlaps: list[LabelOverlap] = []
    threshold = max(0.0, min(1.0, float(overlap_threshold)))
    for first_index, first in enumerate(visible_labels):
        first_bbox = cast(BBox, first.evaluated_bbox)
        for second in visible_labels[first_index + 1 :]:
            second_bbox = cast(BBox, second.evaluated_bbox)
            overlap_ratio = bbox_overlap_ratio(first_bbox, second_bbox)
            if overlap_ratio <= threshold:
                continue
            overlaps.append(
                LabelOverlap(
                    first_id=first.label_id,
                    second_id=second.label_id,
                    iou=_round(bbox_iou(first_bbox, second_bbox)),
                    overlap_ratio=_round(overlap_ratio),
                    intersection_area=_round(_intersection_area(first_bbox, second_bbox)),
                )
            )

    clipped_ratios = [label.clipped_area_ratio for label in valid_labels]
    overlap_ratios = [overlap.overlap_ratio for overlap in overlaps]
    return LabelClutterMetrics(
        label_count=len(label_items),
        valid_label_count=len(valid_labels),
        visible_label_count=len(visible_labels),
        invalid_label_count=invalid_count,
        clipped_count=sum(1 for label in valid_labels if label.clipped),
        overlap_count=len(overlaps),
        mean_overlap_ratio=_mean(overlap_ratios),
        max_overlap_ratio=_round(max(overlap_ratios)) if overlap_ratios else 0.0,
        mean_clipped_area_ratio=_mean(clipped_ratios),
        labels=tuple(valid_labels),
        overlaps=tuple(overlaps),
    )
