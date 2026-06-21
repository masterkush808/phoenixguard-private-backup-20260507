from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import time
from typing import Any, Mapping, Sequence, cast
from phoenixguard.core.config import RUNTIME
from phoenixguard.vision.v3_chart_transform import V3ChartTransform


OVERLAY_LAYERS: tuple[str, ...] = (
    "chart_bounds",
    "recent_candles",
    "major_swings",
    "local_swings",
    "supply_demand",
    "target_zones",
    "invalidation",
    "prediction_path",
    "historical_replay",
    "trigger_zones",
    "active_council_decision",  # Council predictions RENDER ON TOP of candles and zones
    "broker_controls",
    "diagnostics",
)

DEFAULT_LAYER_VISIBILITY: dict[str, bool] = {
    "chart_bounds": True,
    "recent_candles": True,
    "major_swings": False,
    "local_swings": False,
    "supply_demand": True,
    "trigger_zones": True,
    "target_zones": True,
    "invalidation": True,
    "prediction_path": False,
    "active_council_decision": True,
    "historical_replay": True,
    "broker_controls": False,
    "diagnostics": False,
}

STATIC_OVERLAY_LAYERS: frozenset[str] = frozenset(
    {
        "chart_bounds",
            "major_swings",
            "supply_demand",
            "target_zones",
            "invalidation",
            "prediction_path",
            "historical_replay",
        "broker_controls",
    }
)


@dataclass(frozen=True)
class OverlayGeometryPolicy:
    max_area_ratio: float = 0.38
    max_zone_area_ratio: float = 0.08
    max_trigger_area_ratio: float = 0.10
    max_structure_area_ratio: float = 0.28
    max_aspect_ratio: float = 36.0
    merge_iou_threshold: float = 0.52
    merge_overlap_threshold: float = 0.72
    broker_overlap_drop_threshold: float = 0.45
    broker_clip_padding_px: int = 4
    min_width_px: int = 3
    min_height_px: int = 3
    temporal_smoothing_alpha: float = 0.68
    render_budget_ms: int = 12


DEFAULT_GEOMETRY_POLICY = OverlayGeometryPolicy()


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:
        return fallback
    return number


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _float(value, 0.0)))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        value = int(float(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        value = default
    return max(int(minimum), int(value))


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(float(minimum), float(value))


def _prune_overlay_geometry_dumps(debug_dir: Any) -> None:
    try:
        paths = [path for path in debug_dir.glob("overlay_geometry_*.json") if path.is_file()]
        if not paths:
            return
        max_files = _env_int("PHOENIXGUARD_OVERLAY_GEOMETRY_DUMP_MAX_FILES", 80, 4)
        max_mb = _env_float("PHOENIXGUARD_OVERLAY_GEOMETRY_DUMP_MAX_MB", 48.0, 1.0)
        max_age_sec = _env_float("PHOENIXGUARD_OVERLAY_GEOMETRY_DUMP_MAX_AGE_SEC", 7200.0, 60.0)
        now = time.time()
        ordered = sorted(paths, key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)
        removable: set[Any] = set()
        for index, path in enumerate(ordered):
            try:
                age_sec = now - float(path.stat().st_mtime)
            except OSError:
                age_sec = 0.0
            if index >= max_files or age_sec > max_age_sec:
                removable.add(path)
        kept = [path for path in ordered if path not in removable]
        total_bytes = sum(path.stat().st_size for path in kept if path.exists())
        max_bytes = int(max_mb * 1024.0 * 1024.0)
        for path in sorted(kept, key=lambda item: item.stat().st_mtime if item.exists() else 0.0):
            if total_bytes <= max_bytes:
                break
            try:
                total_bytes -= int(path.stat().st_size)
            except OSError:
                pass
            removable.add(path)
        for path in removable:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:
        pass


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, Any], item)) for item in value if isinstance(item, Mapping)]


def normalize_bbox(bbox: Sequence[Any]) -> list[float] | None:
    if len(bbox) < 4:
        return None
    x0 = _float(bbox[0])
    y0 = _float(bbox[1])
    x1 = _float(bbox[2])
    y1 = _float(bbox[3])
    left = min(x0, x1)
    top = min(y0, y1)
    right = max(x0, x1)
    bottom = max(y0, y1)
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def bbox_area(bbox: Sequence[Any]) -> float:
    normalized = normalize_bbox(bbox)
    if normalized is None:
        return 0.0
    return max(0.0, normalized[2] - normalized[0]) * max(0.0, normalized[3] - normalized[1])


def bbox_aspect_ratio(bbox: Sequence[Any]) -> float:
    normalized = normalize_bbox(bbox)
    if normalized is None:
        return 0.0
    width = max(1.0, normalized[2] - normalized[0])
    height = max(1.0, normalized[3] - normalized[1])
    return max(width / height, height / width)


def clip_bbox_to_bounds(bbox: Sequence[Any], bounds: Sequence[Any]) -> list[float] | None:
    box = normalize_bbox(bbox)
    bound = normalize_bbox(bounds)
    if box is None or bound is None:
        return None
    left = max(bound[0], min(bound[2], box[0]))
    top = max(bound[1], min(bound[3], box[1]))
    right = max(bound[0], min(bound[2], box[2]))
    bottom = max(bound[1], min(bound[3], box[3]))
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def bbox_intersection(first: Sequence[Any], second: Sequence[Any]) -> list[float] | None:
    a = normalize_bbox(first)
    b = normalize_bbox(second)
    if a is None or b is None:
        return None
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def bbox_iou(first: Sequence[Any], second: Sequence[Any]) -> float:
    intersection = bbox_intersection(first, second)
    if intersection is None:
        return 0.0
    overlap = bbox_area(intersection)
    union = bbox_area(first) + bbox_area(second) - overlap
    return 0.0 if union <= 0.0 else max(0.0, min(1.0, overlap / union))


def bbox_overlap_ratio(first: Sequence[Any], second: Sequence[Any]) -> float:
    intersection = bbox_intersection(first, second)
    if intersection is None:
        return 0.0
    overlap = bbox_area(intersection)
    min_area = max(1.0, min(bbox_area(first), bbox_area(second)))
    return max(0.0, min(1.0, overlap / min_area))


def _layer_max_area_ratio(layer: str, policy: OverlayGeometryPolicy) -> float:
    normalized = str(layer or "").strip().lower()
    if normalized == "supply_demand":
        return policy.max_zone_area_ratio
    if normalized == "trigger_zones":
        return policy.max_trigger_area_ratio
    if normalized in {"major_swings", "local_swings", "historical_replay", "active_council_decision"}:
        return policy.max_structure_area_ratio
    return policy.max_area_ratio


def _refine_supply_demand_box_to_reaction_cluster(
    box: Mapping[str, Any],
    bbox: Sequence[Any],
    chart_bounds: Sequence[Any],
) -> list[float] | None:
    normalized = normalize_bbox(bbox)
    bounds = normalize_bbox(chart_bounds)
    if normalized is None or bounds is None:
        return normalized
    touch_points = []
    raw_points = box.get("touch_points", [])
    if isinstance(raw_points, Sequence) and not isinstance(raw_points, (str, bytes, bytearray)):
        for item in raw_points:
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) and len(item) >= 2:
                touch_points.append((_float(item[0]), _float(item[1])))
    chart_width = max(1.0, bounds[2] - bounds[0])
    chart_height = max(1.0, bounds[3] - bounds[1])
    horizontal_pad = max(20.0, min(52.0, chart_width * 0.045))
    vertical_pad = max(8.0, min(24.0, chart_height * 0.026))
    minimum_width = min(chart_width * 0.16, 140.0)
    maximum_width = max(minimum_width, chart_width * 0.30)
    if touch_points:
        recent_points = touch_points[-min(8, len(touch_points)) :]
        xs = [point[0] for point in recent_points]
        ys = [point[1] for point in recent_points]
        left = max(bounds[0], min(xs) - horizontal_pad)
        right = min(bounds[2], max(xs) + horizontal_pad)
        anchor_x = xs[-1]
        reaction_height = (max(ys) - min(ys)) + vertical_pad * 2.0
    else:
        raw_width = normalized[2] - normalized[0]
        raw_height = normalized[3] - normalized[1]
        if raw_width >= chart_width * 0.82 or raw_height >= chart_height * 0.40:
            return None
        minimum_width = min(minimum_width, max(3.0, raw_width))
        line_x0 = _float(box.get("line_x0"), normalized[0])
        line_x1 = _float(box.get("line_x1"), normalized[2])
        line_left = max(bounds[0], min(bounds[2], min(line_x0, line_x1)))
        line_right = max(bounds[0], min(bounds[2], max(line_x0, line_x1)))
        left = max(bounds[0], normalized[0], line_left)
        right = min(bounds[2], normalized[2], line_right)
        if right <= left:
            left, right = normalized[0], normalized[2]
        anchor_x = (max(bounds[0], min(bounds[2], left)) + max(bounds[0], min(bounds[2], right))) * 0.5
        reaction_height = normalized[3] - normalized[1]
    if right - left < minimum_width:
        center_x = (left + right) * 0.5
        left = max(bounds[0], center_x - minimum_width * 0.5)
        right = min(bounds[2], center_x + minimum_width * 0.5)
    if right - left > maximum_width:
        left = max(bounds[0], anchor_x - maximum_width * 0.5)
        right = min(bounds[2], left + maximum_width)
        if right - left < maximum_width:
            left = max(bounds[0], right - maximum_width)
    line_y = _float(box.get("line_y"), (normalized[1] + normalized[3]) * 0.5)
    zone_height = max(reaction_height, 14.0)
    zone_height = min(zone_height, chart_height * 0.11)
    top = max(bounds[1], line_y - zone_height * 0.5)
    bottom = min(bounds[3], line_y + zone_height * 0.5)
    if right <= left or bottom <= top:
        return normalized
    return [left, top, right, bottom]


def has_structural_anchor(box: Mapping[str, Any]) -> bool:
    if bool(box.get("structural_anchor") or box.get("anchored") or box.get("nearest") or box.get("still_significant")):
        return True
    for key in ("touch_points", "source_indices", "path", "start_point", "end_point"):
        value = box.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) > 0:
            return True
    if _float(box.get("candle_count"), 0.0) > 0.0:
        return True
    if "line_y" in box and ("line_x0" in box or "line_x1" in box):
        return True
    source = str(box.get("source", "") or "").lower()
    return source in {"tracked_candle", "tracked_candles", "chart_segmentation", "candle_track", "decision_kernel"}


def _box_kind(box: Mapping[str, Any]) -> str:
    for key in ("layer", "role", "kind", "key", "box_type", "type"):
        text = str(box.get(key, "") or "").strip().lower()
        if text:
            return text
    return "box"


def _same_merge_family(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    first_layer = str(first.get("layer", "") or "").strip().lower()
    second_layer = str(second.get("layer", "") or "").strip().lower()
    if first_layer != second_layer:
        return False
    first_role = str(first.get("role", first.get("kind", "")) or "").strip().lower()
    second_role = str(second.get("role", second.get("kind", "")) or "").strip().lower()
    if first_role and second_role and first_role != second_role:
        return False
    first_direction = str(first.get("direction", "") or "").strip().upper()
    second_direction = str(second.get("direction", "") or "").strip().upper()
    if first_direction and second_direction and first_direction != second_direction:
        return False
    return True


def _merge_two_boxes(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    a = normalize_bbox(cast(Sequence[Any], first.get("bbox", [])))
    b = normalize_bbox(cast(Sequence[Any], second.get("bbox", [])))
    if a is None:
        return dict(second)
    if b is None:
        return dict(first)
    confidence = max(_clip01(first.get("confidence", 0.0)), _clip01(second.get("confidence", 0.0)))
    merged = dict(first if _clip01(first.get("confidence", 0.0)) >= _clip01(second.get("confidence", 0.0)) else second)
    merged["bbox"] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
    merged["confidence"] = round(confidence, 4)
    merged["merged_count"] = int(first.get("merged_count", 1) or 1) + int(second.get("merged_count", 1) or 1)
    merged_keys: list[Any] = []
    for source in (first, second):
        raw_keys = source.get("merged_keys", [])
        if isinstance(raw_keys, Sequence) and not isinstance(raw_keys, (str, bytes, bytearray)):
            merged_keys.extend(list(raw_keys))
        else:
            merged_keys.append(source.get("key", source.get("label", "")))
    merged["merged_keys"] = [item for item in merged_keys if str(item or "").strip()]
    if first.get("line_y") is not None or second.get("line_y") is not None:
        merged["line_y"] = round((_float(first.get("line_y"), (a[1] + a[3]) * 0.5) + _float(second.get("line_y"), (b[1] + b[3]) * 0.5)) * 0.5, 3)
    if first.get("line_x0") is not None or second.get("line_x0") is not None:
        merged["line_x0"] = min(_float(first.get("line_x0"), a[0]), _float(second.get("line_x0"), b[0]))
    if first.get("line_x1") is not None or second.get("line_x1") is not None:
        merged["line_x1"] = max(_float(first.get("line_x1"), a[2]), _float(second.get("line_x1"), b[2]))
    for key in ("touch_points", "source_indices"):
        combined: list[Any] = []
        for source in (first.get(key), second.get(key)):
            if isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray)):
                combined.extend(list(source))
        if combined:
            merged[key] = combined
    merged["structural_anchor"] = bool(has_structural_anchor(first) or has_structural_anchor(second))
    return merged


def merge_same_type_boxes(
    boxes: Sequence[Mapping[str, Any]],
    *,
    policy: OverlayGeometryPolicy = DEFAULT_GEOMETRY_POLICY,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for raw_box in boxes:
        box = dict(raw_box)
        bbox = cast(Sequence[Any], box.get("bbox", []))
        if normalize_bbox(bbox) is None:
            continue
        merge_index: int | None = None
        for index, existing in enumerate(merged):
            if not _same_merge_family(existing, box):
                continue
            existing_bbox = cast(Sequence[Any], existing.get("bbox", []))
            if (
                bbox_iou(existing_bbox, bbox) >= policy.merge_iou_threshold
                or bbox_overlap_ratio(existing_bbox, bbox) >= policy.merge_overlap_threshold
            ):
                merge_index = index
                break
        if merge_index is None:
            merged.append(box)
        else:
            merged[merge_index] = _merge_two_boxes(merged[merge_index], box)
    return merged


def _clip_against_broker_exclusions(
    bbox: Sequence[Any],
    chart_bounds: Sequence[Any],
    exclusions: Sequence[Sequence[Any]],
    *,
    policy: OverlayGeometryPolicy,
) -> list[float] | None:
    clipped = clip_bbox_to_bounds(bbox, chart_bounds)
    bounds = normalize_bbox(chart_bounds)
    if clipped is None or bounds is None:
        return None
    for raw_exclusion in exclusions:
        exclusion = clip_bbox_to_bounds(raw_exclusion, bounds)
        if exclusion is None:
            continue
        overlap = bbox_overlap_ratio(clipped, exclusion)
        if overlap <= 0.0:
            continue
        if overlap >= policy.broker_overlap_drop_threshold:
            return None
        pad = float(max(0, int(policy.broker_clip_padding_px)))
        exclusion_width = max(1.0, exclusion[2] - exclusion[0])
        exclusion_height = max(1.0, exclusion[3] - exclusion[1])
        right_panel = exclusion[2] >= bounds[2] - 1.0 and exclusion[0] > bounds[0] + 1.0
        left_panel = exclusion[0] <= bounds[0] + 1.0 and exclusion[2] < bounds[2] - 1.0
        bottom_panel = exclusion[3] >= bounds[3] - 1.0 and exclusion[1] > bounds[1] + 1.0
        top_panel = exclusion[1] <= bounds[1] + 1.0 and exclusion[3] < bounds[3] - 1.0
        if right_panel and exclusion_height >= (bounds[3] - bounds[1]) * 0.45:
            clipped[2] = min(clipped[2], exclusion[0] - pad)
        elif left_panel and exclusion_height >= (bounds[3] - bounds[1]) * 0.45:
            clipped[0] = max(clipped[0], exclusion[2] + pad)
        elif bottom_panel and exclusion_width >= (bounds[2] - bounds[0]) * 0.45:
            clipped[3] = min(clipped[3], exclusion[1] - pad)
        elif top_panel and exclusion_width >= (bounds[2] - bounds[0]) * 0.45:
            clipped[1] = max(clipped[1], exclusion[3] + pad)
        else:
            return None
        clipped = clip_bbox_to_bounds(clipped, bounds)
        if clipped is None:
            return None
    return clipped


def sanitize_overlay_box(
    box: Mapping[str, Any],
    *,
    chart_bounds: Sequence[Any],
    layer: str,
    broker_exclusion_boxes: Sequence[Sequence[Any]] = (),
    require_anchor: bool = False,
    policy: OverlayGeometryPolicy = DEFAULT_GEOMETRY_POLICY,
) -> dict[str, Any] | None:
    row = dict(box)
    raw_bbox = cast(Sequence[Any], row.get("bbox", []))
    clipped = _clip_against_broker_exclusions(
        raw_bbox,
        chart_bounds,
        broker_exclusion_boxes,
        policy=policy,
    )
    if clipped is None:
        return None
    if str(layer or "").strip().lower() == "supply_demand":
        refined = _refine_supply_demand_box_to_reaction_cluster(row, clipped, chart_bounds)
        if refined is None:
            return None
        clipped = refined
    width = clipped[2] - clipped[0]
    height = clipped[3] - clipped[1]
    if width < policy.min_width_px or height < policy.min_height_px:
        return None
    bounds_area = max(1.0, bbox_area(chart_bounds))
    area_ratio = (width * height) / bounds_area
    if area_ratio > _layer_max_area_ratio(layer, policy):
        return None
    if bbox_aspect_ratio(clipped) > policy.max_aspect_ratio:
        return None
    if require_anchor and not has_structural_anchor(row):
        return None
    row["bbox"] = [round(float(value), 3) for value in clipped]
    row["layer"] = layer
    if str(layer or "").strip().lower() == "supply_demand":
        row["line_x0"] = round(float(clipped[0]), 3)
        row["line_x1"] = round(float(clipped[2]), 3)
        line_y = _float(row.get("line_y"), (clipped[1] + clipped[3]) * 0.5)
        row["line_y"] = round(max(float(clipped[1]), min(float(clipped[3]), line_y)), 3)
    row["visible_default"] = bool(DEFAULT_LAYER_VISIBILITY.get(layer, False))
    row["geometry_kind"] = _box_kind(row)
    row["area_ratio"] = round(float(area_ratio), 6)
    row["aspect_ratio"] = round(float(bbox_aspect_ratio(clipped)), 4)
    row["structural_anchor"] = bool(has_structural_anchor(row))
    return row


def _normalized_exclusion_boxes(
    chart_bounds: Sequence[Any],
    broker_exclusion_boxes: Sequence[Sequence[Any]],
) -> list[list[float]]:
    rows: list[list[float]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for raw_box in broker_exclusion_boxes:
        clipped = clip_bbox_to_bounds(raw_box, chart_bounds)
        if clipped is None:
            continue
        key = tuple(round(float(value), 2) for value in clipped)
        if key in seen:
            continue
        seen.add(key)
        rows.append([float(value) for value in clipped])
    return rows


def _default_axis_exclusion_boxes(chart_bounds: Sequence[Any]) -> list[list[float]]:
    bounds = normalize_bbox(chart_bounds)
    if bounds is None:
        return []
    width = max(1.0, bounds[2] - bounds[0])
    height = max(1.0, bounds[3] - bounds[1])
    transform = V3ChartTransform.create((width, height), frame_id=0)
    rows: list[list[float]] = []
    for raw_box in (transform.price_axis_bounds, transform.time_axis_bounds):
        translated = [
            float(raw_box[0]) + bounds[0],
            float(raw_box[1]) + bounds[1],
            float(raw_box[2]) + bounds[0],
            float(raw_box[3]) + bounds[1],
        ]
        clipped = clip_bbox_to_bounds(translated, bounds)
        if clipped is not None:
            rows.append([float(value) for value in clipped])
    return rows


def _market_exclusion_boxes(
    chart_bounds: Sequence[Any],
    broker_exclusion_boxes: Sequence[Sequence[Any]],
) -> list[list[float]]:
    return _normalized_exclusion_boxes(chart_bounds, broker_exclusion_boxes) + _default_axis_exclusion_boxes(chart_bounds)


def _point_x(value: Any) -> float | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) < 2:
        return None
    return _float(value[0], float("nan"))


def _tighten_micro_plan_bbox(
    row: Mapping[str, Any],
    key: str,
    bbox: Sequence[Any],
    chart_bounds: Sequence[Any],
) -> list[float] | None:
    box = normalize_bbox(bbox)
    bounds = normalize_bbox(chart_bounds)
    if box is None or bounds is None:
        return box
    chart_width = max(1.0, bounds[2] - bounds[0])
    chart_height = max(1.0, bounds[3] - bounds[1])
    normalized_key = str(key or "").strip().lower()
    is_entry_window = "sniper" in normalized_key or "trigger" in normalized_key
    max_width = min(chart_width, max(48.0, chart_width * (0.22 if is_entry_window else 0.26)))
    max_height = min(chart_height, max(12.0, chart_height * (0.085 if is_entry_window else 0.10)))
    width = max(1.0, box[2] - box[0])
    height = max(1.0, box[3] - box[1])
    if width <= max_width and height <= max_height:
        return box

    anchor_candidates: list[float] = []
    base_box = normalize_bbox(cast(Sequence[Any], row.get("bbox", [])))
    if base_box is not None:
        anchor_candidates.extend([base_box[2], (base_box[0] + base_box[2]) * 0.5])
    for point_key in ("end_point", "entry_point", "trigger_point", "sniper_point", "target_point"):
        point_x = _point_x(row.get(point_key))
        if point_x is not None and point_x == point_x:
            anchor_candidates.append(point_x)
    anchor_candidates.append(box[2])
    anchor_x = max(bounds[0], min(box[2], max(anchor_candidates)))

    left, right = box[0], box[2]
    if width > max_width:
        right = min(bounds[2], max(bounds[0] + max_width, anchor_x))
        left = right - max_width
        if left < bounds[0]:
            left = bounds[0]
            right = min(bounds[2], left + max_width)

    top, bottom = box[1], box[3]
    if height > max_height:
        center_y = (box[1] + box[3]) * 0.5
        top = max(bounds[1], center_y - max_height * 0.5)
        bottom = min(bounds[3], top + max_height)
        if bottom - top < max_height:
            top = max(bounds[1], bottom - max_height)

    return clip_bbox_to_bounds([left, top, right, bottom], bounds)


def _sanitize_sequence(
    items: Sequence[Mapping[str, Any]],
    *,
    chart_bounds: Sequence[Any],
    layer: str,
    broker_exclusion_boxes: Sequence[Sequence[Any]],
    require_anchor: bool,
    policy: OverlayGeometryPolicy,
    merge: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        sanitized = sanitize_overlay_box(
            item,
            chart_bounds=chart_bounds,
            layer=layer,
            broker_exclusion_boxes=broker_exclusion_boxes,
            require_anchor=require_anchor,
            policy=policy,
        )
        if sanitized is not None:
            rows.append(sanitized)
    return merge_same_type_boxes(rows, policy=policy) if merge else rows


def _clip_micro_plan_fields(
    box: dict[str, Any],
    *,
    chart_bounds: Sequence[Any],
    broker_exclusion_boxes: Sequence[Sequence[Any]],
    policy: OverlayGeometryPolicy,
) -> dict[str, Any]:
    row = dict(box)
    for key in ("sniper_window", "trigger_window", "target_window", "target_bbox"):
        bbox = cast(Sequence[Any], row.get(key, []))
        if len(bbox) < 4:
            continue
        clipped = _clip_against_broker_exclusions(
            bbox,
            chart_bounds,
            broker_exclusion_boxes,
            policy=policy,
        )
        if clipped is None:
            row.pop(key, None)
        else:
            clipped = _tighten_micro_plan_bbox(row, key, clipped, chart_bounds) or clipped
            row[key] = [round(float(value), 3) for value in clipped]
    plan = _mapping(row.get("sniper_target_plan", {}))
    for plan_key in ("sniper", "trigger", "target"):
        bbox = cast(Sequence[Any], plan.get(plan_key, []))
        if len(bbox) < 4:
            continue
        clipped = _clip_against_broker_exclusions(
            bbox,
            chart_bounds,
            broker_exclusion_boxes,
            policy=policy,
        )
        if clipped is None:
            plan.pop(plan_key, None)
        else:
            clipped = _tighten_micro_plan_bbox(row, plan_key, clipped, chart_bounds) or clipped
            plan[plan_key] = [round(float(value), 3) for value in clipped]
    if plan:
        row["sniper_target_plan"] = plan
    bounds = normalize_bbox(chart_bounds)
    if bounds is not None and row.get("invalidation_y") is not None:
        row["invalidation_y"] = round(max(bounds[1], min(bounds[3], _float(row.get("invalidation_y"), bounds[1]))), 3)
    return row


def _box_key(box: Mapping[str, Any]) -> str:
    for key in ("key", "label", "role", "kind"):
        text = str(box.get(key, "") or "").strip()
        if text:
            return text
    return _box_kind(box)


def _smooth_bbox(previous: Sequence[Any], current: Sequence[Any], alpha: float) -> list[float] | None:
    previous_box = normalize_bbox(previous)
    current_box = normalize_bbox(current)
    if previous_box is None or current_box is None:
        return current_box
    return [
        round(previous_box[index] * alpha + current_box[index] * (1.0 - alpha), 3)
        for index in range(4)
    ]


def smooth_overlay_boxes(
    previous_boxes: Sequence[Mapping[str, Any]],
    current_boxes: Sequence[Mapping[str, Any]],
    *,
    alpha: float = DEFAULT_GEOMETRY_POLICY.temporal_smoothing_alpha,
) -> list[dict[str, Any]]:
    previous_by_key = {
        f"{str(box.get('layer', ''))}:{_box_key(box)}": box
        for box in previous_boxes
        if isinstance(box, Mapping)
    }
    smoothed: list[dict[str, Any]] = []
    for current in current_boxes:
        row = dict(current)
        previous = previous_by_key.get(f"{str(row.get('layer', ''))}:{_box_key(row)}")
        if previous:
            bbox = _smooth_bbox(
                cast(Sequence[Any], previous.get("bbox", [])),
                cast(Sequence[Any], row.get("bbox", [])),
                max(0.0, min(0.98, float(alpha))),
            )
            if bbox is not None:
                row["bbox"] = bbox
                row["smoothed"] = True
        smoothed.append(row)
    return smoothed


def _static_layer_hash(boxes: Sequence[Mapping[str, Any]]) -> str:
    static_rows = [
        {
            "layer": box.get("layer", ""),
            "key": _box_key(box),
            "bbox": box.get("bbox", []),
            "direction": box.get("direction", ""),
        }
        for box in boxes
        if str(box.get("layer", "") or "") in STATIC_OVERLAY_LAYERS
    ]
    encoded = json.dumps(static_rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _box_distance_sort_key(box: Mapping[str, Any]) -> tuple[float, float]:
    distance = _float(box.get("distance_to_latest_norm"), _float(box.get("entry_area_distance_norm"), 1.0))
    confidence = _clip01(box.get("confidence", box.get("significance_score", 0.0)))
    return (distance, -confidence)


def _apply_live_default_visibility(boxes: Sequence[Mapping[str, Any]], active_side: str) -> list[dict[str, Any]]:
    rows = [dict(box) for box in boxes]
    for row in rows:
        layer = str(row.get("layer", "") or "")
        key = str(row.get("key", "") or "").strip().lower()
        row["visible_default"] = (
            layer in {"chart_bounds", "recent_candles", "active_council_decision", "historical_replay"}
            or key == "current"
        )

    supply_rows = [row for row in rows if str(row.get("layer", "") or "") == "supply_demand"]
    supports = [row for row in supply_rows if str(row.get("role", row.get("kind", "")) or "").lower() in {"support", "demand"}]
    resistances = [row for row in supply_rows if str(row.get("role", row.get("kind", "")) or "").lower() in {"resistance", "supply"}]
    for group in (supports, resistances):
        for row in sorted(group, key=_box_distance_sort_key)[:1]:
            row["visible_default"] = True

    trigger_rows = [
        row
        for row in rows
        if str(row.get("layer", "") or "") == "trigger_zones"
        and str(row.get("kind", row.get("role", "")) or "").lower() in {"sniper", "primary", "trigger", "target"}
        and (not active_side or active_side == "HOLD" or str(row.get("direction", "") or "").upper() in {active_side, ""})
    ]
    for row in sorted(trigger_rows, key=_box_distance_sort_key)[:3]:
        row["visible_default"] = True

    return rows


LAYER_VISIBILITY_BY_MODE: dict[str, dict[str, bool]] = {
    "live": dict(DEFAULT_LAYER_VISIBILITY),
    "replay": {**dict(DEFAULT_LAYER_VISIBILITY), "historical_replay": True, "recent_candles": False},
    "inspect": {**dict(DEFAULT_LAYER_VISIBILITY), "diagnostics": True, "broker_controls": True, "recent_candles": True},
    "prediction": {**dict(DEFAULT_LAYER_VISIBILITY), "active_council_decision": True, "historical_replay": True},
}


def apply_visibility_by_mode(
    boxes: Sequence[Mapping[str, Any]],
    *,
    active_side: str = "",
    view_mode: str = "live",
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    """Apply default visibility rules for the given view_mode and return (boxes, layer_visibility).

    - `view_mode` supports: 'live', 'replay', 'inspect', 'prediction'.
    - Returns modified boxes list (with `visible_default` flags) and computed `layer_visibility` map.
    """
    normalized_mode = str(view_mode or "live").strip().lower()
    base_visibility = dict(DEFAULT_LAYER_VISIBILITY)
    overrides = LAYER_VISIBILITY_BY_MODE.get(normalized_mode, {})
    base_visibility.update(overrides)

    # Start from existing live logic for per-box defaults, then adjust by mode
    rows = _apply_live_default_visibility(boxes, active_side)

    # Mode-specific adjustments to `visible_default` per box
    if normalized_mode == "replay":
        for row in rows:
            layer = str(row.get("layer", "") or "")
            if layer == "historical_replay":
                row["visible_default"] = True
            if layer == "recent_candles":
                row["visible_default"] = False
    elif normalized_mode == "inspect":
        for row in rows:
            layer = str(row.get("layer", "") or "")
            if layer in {"diagnostics", "broker_controls"}:
                row["visible_default"] = True
    elif normalized_mode == "prediction":
        for row in rows:
            layer = str(row.get("layer", "") or "")
            if layer in {"active_council_decision", "historical_replay"}:
                row["visible_default"] = True

    # Ensure layer_visibility reflects any boxes explicitly visible by default
    for row in rows:
        layer = str(row.get("layer", "") or "")
        if row.get("visible_default"):
            base_visibility[layer] = True

    return rows, base_visibility


def _anchor_type(box: Mapping[str, Any]) -> str:
    if box.get("line_y") is not None:
        return "support_resistance_band"
    if box.get("start_point") is not None or box.get("end_point") is not None:
        return "swing_leg"
    if box.get("path") is not None:
        return "projected_path"
    if str(box.get("source", "") or "").lower() in {"tracked_candle", "tracked_candles", "candle_track"}:
        return "tracked_candle"
    if has_structural_anchor(box):
        return "structural_anchor"
    return "unanchored"


def build_overlay_truth_audit(
    boxes: Sequence[Mapping[str, Any]],
    *,
    minimum_truth_score: float = 0.55,
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    invalid_count = 0
    decision_invalid_count = 0
    for index, box in enumerate(boxes):
        layer = str(box.get("layer", "") or "")
        confidence = _clip01(box.get("confidence", 0.72 if layer == "chart_bounds" else 0.0))
        anchored = has_structural_anchor(box) or layer == "chart_bounds"
        area_ratio = _float(box.get("area_ratio"), 0.0)
        aspect_ratio = _float(box.get("aspect_ratio"), 1.0)
        penalties = 0.0
        if not anchored:
            penalties += 0.35
        if area_ratio > 0.18 and layer in {"supply_demand", "trigger_zones"}:
            penalties += 0.22
        if aspect_ratio > 64.0:
            penalties += 0.22
        if box.get("visible_default") is False and layer != "historical_replay":
            penalties += 0.04
        truth_score = max(0.0, min(1.0, 0.52 + 0.34 * confidence + 0.22 * float(anchored) - penalties))
        valid_for_decision = truth_score >= float(minimum_truth_score) and anchored
        if not valid_for_decision:
            invalid_count += 1
            if layer in {"supply_demand", "trigger_zones", "active_council_decision"}:
                decision_invalid_count += 1
        evidence = []
        if anchored:
            evidence.append(_anchor_type(box))
        if box.get("merged_count"):
            evidence.append(f"merged_count:{box.get('merged_count')}")
        if confidence > 0.0:
            evidence.append(f"confidence:{confidence:.2f}")
        objects.append(
            {
                "id": str(box.get("key") or box.get("id") or f"overlay_{index}"),
                "type": str(box.get("role") or box.get("kind") or layer or "OVERLAY_OBJECT").upper(),
                "layer": layer,
                "bbox": list(box.get("bbox", [])),
                "truth_score": round(truth_score, 4),
                "anchor_type": _anchor_type(box),
                "evidence": evidence,
                "valid_for_decision": valid_for_decision,
                "visible_default": bool(box.get("visible_default", False)),
            }
        )
    return {
        "version": "OVERLAY_TRUTH_AUDIT_V1",
        "objects": objects,
        "valid_for_execution": decision_invalid_count == 0,
        "execution_safe": decision_invalid_count == 0,
        "invalid_object_count": invalid_count,
        "decision_invalid_object_count": decision_invalid_count,
        "minimum_truth_score": float(minimum_truth_score),
    }


def _chart_bounds_from_size(chart_size: Sequence[Any]) -> list[float]:
    width = max(1.0, _float(chart_size[0], 1.0) if len(chart_size) >= 1 else 1.0)
    height = max(1.0, _float(chart_size[1], 1.0) if len(chart_size) >= 2 else 1.0)
    return [0.0, 0.0, width, height]


def prepare_overlay_geometry(
    tracking_summary: Mapping[str, Any],
    latest_signal: Mapping[str, Any],
    *,
    chart_size: Sequence[Any],
    broker_exclusion_boxes: Sequence[Sequence[Any]] = (),
    previous_geometry: Mapping[str, Any] | None = None,
    debug_enabled: bool = False,
    policy: OverlayGeometryPolicy = DEFAULT_GEOMETRY_POLICY,
) -> dict[str, Any]:
    chart_bounds = _chart_bounds_from_size(chart_size)
    broker_exclusions = _normalized_exclusion_boxes(chart_bounds, broker_exclusion_boxes)
    market_exclusions = _market_exclusion_boxes(chart_bounds, broker_exclusions)
    tracking = dict(tracking_summary)
    signal = dict(latest_signal)
    boxes: list[dict[str, Any]] = []

    chart_box = sanitize_overlay_box(
        {"key": "chart_bounds", "label": "CHART BOUNDS", "bbox": chart_bounds, "source": "chart_plane"},
        chart_bounds=chart_bounds,
        layer="chart_bounds",
        policy=OverlayGeometryPolicy(
            **{
                **policy.__dict__,
                "max_area_ratio": 1.01,
                "max_structure_area_ratio": 1.01,
            }
        ),
    )
    if chart_box is not None:
        boxes.append(chart_box)

    recent_candles = []
    tracked = _sequence_of_mappings(tracking.get("tracked_candles", []))
    for index, candle in enumerate(tracked[-6:], start=max(1, len(tracked) - 5)):
        row = dict(candle)
        row.setdefault("key", f"recent_candle_{index}")
        row.setdefault("label", f"C{index}")
        row.setdefault("source", "tracked_candle")
        row.setdefault("role", "recent_candle")
        row["structural_anchor"] = True
        recent_candles.append(row)
    boxes.extend(
        _sanitize_sequence(
            recent_candles,
            chart_bounds=chart_bounds,
            layer="recent_candles",
            broker_exclusion_boxes=broker_exclusions,
            require_anchor=True,
            policy=policy,
            merge=False,
        )
    )

    raw_structure = _sequence_of_mappings(tracking.get("structure_boxes", []))
    sanitized_structure: list[dict[str, Any]] = []
    for raw_box in raw_structure:
        key = str(raw_box.get("key", "") or "").lower()
        layer = "major_swings" if key == "global" else "local_swings"
        row = _clip_micro_plan_fields(
            raw_box,
            chart_bounds=chart_bounds,
            broker_exclusion_boxes=market_exclusions,
            policy=policy,
        )
        sanitized = sanitize_overlay_box(
            row,
            chart_bounds=chart_bounds,
            layer=layer,
            broker_exclusion_boxes=market_exclusions,
            require_anchor=True,
            policy=policy,
        )
        if sanitized is not None:
            sanitized_structure.append(sanitized)
    sanitized_structure = merge_same_type_boxes(sanitized_structure, policy=policy)
    tracking["structure_boxes"] = sanitized_structure
    current = next((box for box in sanitized_structure if str(box.get("key", "") or "").lower() == "current"), None)
    if current is not None:
        tracking["current_box"] = dict(current)
    boxes.extend(sanitized_structure)

    historical = _sanitize_sequence(
        [
            _clip_micro_plan_fields(
                row,
                chart_bounds=chart_bounds,
                broker_exclusion_boxes=market_exclusions,
                policy=policy,
            )
            for row in _sequence_of_mappings(tracking.get("historical_structure", []))
        ],
        chart_bounds=chart_bounds,
        layer="historical_replay",
        broker_exclusion_boxes=market_exclusions,
        require_anchor=True,
        policy=policy,
    )
    tracking["historical_structure"] = historical
    boxes.extend(historical)

    supply_demand = _sanitize_sequence(
        _sequence_of_mappings(tracking.get("support_resistance_zones", [])),
        chart_bounds=chart_bounds,
        layer="supply_demand",
        broker_exclusion_boxes=market_exclusions,
        require_anchor=True,
        policy=policy,
    )
    tracking["support_resistance_zones"] = supply_demand
    boxes.extend(supply_demand)

    projection = _mapping(tracking.get("projection", {}))
    sanitized_projection_zones: list[dict[str, Any]] = []
    for raw_zone in _sequence_of_mappings(projection.get("zones", [])):
        row = _clip_micro_plan_fields(
            raw_zone,
            chart_bounds=chart_bounds,
            broker_exclusion_boxes=market_exclusions,
            policy=policy,
        )
        row["structural_anchor"] = bool(row.get("path") or row.get("invalidation_y") is not None)
        sanitized = sanitize_overlay_box(
            row,
            chart_bounds=chart_bounds,
            layer="trigger_zones",
            broker_exclusion_boxes=market_exclusions,
            require_anchor=True,
            policy=policy,
        )
        if sanitized is None:
            continue
        sanitized_projection_zones.append(sanitized)
    projection["zones"] = sanitized_projection_zones
    projection["visual_overlay_disabled"] = True
    projection["visual_overlay_reason"] = "prediction_path_disabled_for_live_chart"
    tracking["projection"] = projection

    active_side = str(
        signal.get("execution_action")
        or signal.get("action")
        or signal.get("candidate_action")
        or tracking.get("control_direction")
        or "HOLD"
    ).upper()
    if active_side in {"BUY", "SELL"} and current is not None:
        council = dict(current)
        council["key"] = "active_council_decision"
        council["label"] = f"COUNCIL {active_side}"
        council["direction"] = active_side
        council["source"] = "model_council"
        council["role"] = "active_decision"
        council["structural_anchor"] = True
        sanitized = sanitize_overlay_box(
            council,
            chart_bounds=chart_bounds,
            layer="active_council_decision",
            broker_exclusion_boxes=market_exclusions,
            require_anchor=True,
            policy=policy,
        )
        if sanitized is not None:
            boxes.append(sanitized)

    previous_boxes = _sequence_of_mappings(_mapping(previous_geometry).get("boxes", []))
    if previous_boxes:
        boxes = smooth_overlay_boxes(previous_boxes, boxes, alpha=policy.temporal_smoothing_alpha)
    # Default to 'live' view_mode unless caller specifies otherwise via tracking_summary
    view_mode = str(tracking.get("view_mode") or tracking.get("overlay_view_mode") or "live").strip().lower()
    boxes, layer_visibility = apply_visibility_by_mode(boxes, active_side=active_side, view_mode=view_mode)

    layer_counts = {layer: 0 for layer in OVERLAY_LAYERS}
    visible_default_count = 0
    for box in boxes:
        layer = str(box.get("layer", "") or "")
        if layer in layer_counts:
            layer_counts[layer] += 1
        if bool(box.get("visible_default", False)):
            visible_default_count += 1

    geometry = {
        "version": 3,
        "chart_bounds": chart_bounds,
        "layers": list(OVERLAY_LAYERS),
        "layer_visibility": dict(layer_visibility),
        "boxes": boxes,
        "layer_counts": layer_counts,
        "visible_default_count": int(visible_default_count),
        "hidden_default_count": int(max(0, len(boxes) - visible_default_count)),
        "debug_enabled": bool(debug_enabled),
        "diagnostics_enabled": bool(debug_enabled),
        "render_budget_ms": int(policy.render_budget_ms),
        "static_layers": sorted(STATIC_OVERLAY_LAYERS),
        "static_layer_hash": _static_layer_hash(boxes),
        "static_layer_count": int(sum(1 for box in boxes if str(box.get("layer", "") or "") in STATIC_OVERLAY_LAYERS)),
        "dynamic_layer_count": int(sum(1 for box in boxes if str(box.get("layer", "") or "") not in STATIC_OVERLAY_LAYERS)),
        "temporal_smoothing": {
            "enabled": True,
            "alpha": float(policy.temporal_smoothing_alpha),
            "previous_frame_available": bool(previous_boxes),
        },
        "broker_exclusion_count": int(len(market_exclusions)),
    }
    # Attach a chart transform for this frame
    try:
        frame_ts = int(tracking.get("frame_timestamp") or tracking.get("frame_id") or 0)
        transform = V3ChartTransform.create(chart_size, frame_id=frame_ts if frame_ts > 0 else None)
        geometry["chart_transform"] = transform.as_dict()
        # annotate boxes with transform id and frame id
        for b in boxes:
            try:
                b["chart_transform_id"] = transform.chart_transform_id
                b["frame_id"] = transform.frame_id
            except Exception:
                pass
    except Exception:
        pass
    geometry["truth_audit"] = build_overlay_truth_audit(boxes)
    tracking["overlay_geometry"] = geometry
    tracking["overlay_truth_audit"] = geometry["truth_audit"]
    signal["overlay_truth_audit"] = geometry["truth_audit"]
    if _env_flag("PHOENIXGUARD_OVERLAY_GEOMETRY_DUMPS", False):
        debug_dir = RUNTIME.project_root / ".codex_runtime" / "overlay_geometry_dumps"
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            frame_id = int(geometry.get("chart_transform", {}).get("frame_id") or tracking.get("frame_id") or 0)
            dump_path = debug_dir / f"overlay_geometry_{frame_id}_{int(time.time())}.json"
            dump = {
                "tracking_summary": tracking,
                "latest_signal": signal,
                "overlay_geometry": geometry,
            }
            dump_path.write_text(json.dumps(dump, default=str), encoding="utf-8")
            _prune_overlay_geometry_dumps(debug_dir)
        except Exception:
            pass
    return {
        "tracking_summary": tracking,
        "latest_signal": signal,
        "overlay_geometry": geometry,
    }
