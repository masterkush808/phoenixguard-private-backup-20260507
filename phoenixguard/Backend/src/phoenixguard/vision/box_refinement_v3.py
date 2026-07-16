from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from phoenixguard.runtime.realtime_performance_v3 import OVERLAY_RENDER_BUDGETS
from phoenixguard.vision.v3_overlay_contract import (
    LIVE_VIEW_MODES,
    OVERLAY_TYPE_PRIORITY,
    layout_overlay_labels,
    normalize_bounds,
    normalize_overlay_display_label,
    normalize_v3_overlay_object,
    normalize_view_mode,
    overlay_layer_name,
    overlay_type_priority,
    rectangles_overlap,
    short_label_for_overlay,
)


OVERLAY_PRECISION_AUDIT_SCHEMA_VERSION = "PG_OVERLAY_PRECISION_AUDIT_V3"

MARKET_OVERLAY_TYPES = {
    "CURRENT_CANDLE",
    "IMPULSE_BOX",
    "PULLBACK_BOX",
    "RETEST_BOX",
    "CONTINUATION_BOX",
    "SNIPER_ENTRY_BOX",
    "TARGET_ZONE_BOX",
    "INVALIDATION_BOX",
    "SUPPLY_ZONE",
    "DEMAND_ZONE",
    "OPPOSING_FORCE",
    "ORDER_BLOCK",
    "FAIR_VALUE_GAP",
    "LIQUIDITY_POOL",
    "LIQUIDITY_SWEEP",
    "MARKET_STRUCTURE_SHIFT",
    "SUPPORT_TRENDLINE",
    "RESISTANCE_TRENDLINE",
    "INNER_TRENDLINE",
    "ANGLE_VECTOR",
    "PROGRESSION_PATH",
    "PREDICTION_PATH",
    "REPLAY_ENTRY",
    "REPLAY_EXIT",
}

ZONE_TYPES = {"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"}
SMART_MONEY_TYPES = {
    "ORDER_BLOCK",
    "FAIR_VALUE_GAP",
    "LIQUIDITY_POOL",
    "LIQUIDITY_SWEEP",
    "MARKET_STRUCTURE_SHIFT",
}
ACTIONABLE_TYPES = {"SNIPER_ENTRY_BOX", "RETEST_BOX", "CONTINUATION_BOX", "TARGET_ZONE_BOX", "INVALIDATION_BOX"}
FLOATING_REJECT_TYPES = ZONE_TYPES | ACTIONABLE_TYPES
DISPLAY_STATES = {
    "FULL",
    "COMPACT",
    "GHOSTED",
    "ICON_ONLY",
    "GROUPED",
    "NESTED",
    "INSPECTOR_LABEL",
    "FOCUS_EXPANDED",
}
TRENDLINE_TYPES = {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}
ALWAYS_LABEL_TYPES = ZONE_TYPES | TRENDLINE_TYPES
HISTORY_TYPES = {"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"}
STRUCTURE_TYPES = {"IMPULSE_BOX", "PULLBACK_BOX", "CONTINUATION_BOX"}
EXECUTION_FOCUS_TYPES = {"SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX"}
DIAGNOSTIC_TYPES = {
    "DEBUG_RAW_DETECTION",
    "REJECTED_OVERLAY",
    "STALE_OVERLAY",
    "TRANSFORM_DEBUG",
    "SCENE_GRAPH_DEBUG",
    "LABEL_COLLISION_DEBUG",
}
SEQUENCE_TYPES = {
    "IMPULSE_BOX",
    "PULLBACK_BOX",
    "SUPPORT_TRENDLINE",
    "RESISTANCE_TRENDLINE",
    "INNER_TRENDLINE",
    "PROGRESSION_PATH",
    "REPLAY_ENTRY",
    "REPLAY_EXIT",
}
CURRENT_CANDLE_LIVE_MODES = {
    "CLEAN_LIVE",
    "CANDLES",
    "LOCAL",
    "ACTIVE_CONTEXT",
    "DIAGNOSTICS",
    "DEBUG",
    "INSPECTOR",
}
NEST_PARENT_TYPES = {
    "IMPULSE_BOX",
    "PULLBACK_BOX",
    "SUPPLY_ZONE",
    "DEMAND_ZONE",
    "PROGRESSION_PATH",
    "REPLAY_ENTRY",
    "REPLAY_EXIT",
}
NEST_CHILD_TYPES = {
    "PULLBACK_BOX",
    "CONTINUATION_BOX",
    "RETEST_BOX",
    "SNIPER_ENTRY_BOX",
    "TARGET_ZONE_BOX",
    "INVALIDATION_BOX",
    "SUPPLY_ZONE",
    "DEMAND_ZONE",
    "OPPOSING_FORCE",
    "PREDICTION_PATH",
    "SUPPORT_TRENDLINE",
    "RESISTANCE_TRENDLINE",
    "INNER_TRENDLINE",
    "PROGRESSION_PATH",
    "REPLAY_ENTRY",
    "REPLAY_EXIT",
}


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[object], value))
    return []


def _string_list(value: object) -> list[str]:
    return [str(item) for item in _sequence(value) if str(item)]


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


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _scene_payload(scene_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    scene = _mapping(scene_graph)
    nested = _mapping(scene.get("scene_graph"))
    return nested if nested else scene


def _box_width(box: Sequence[Any]) -> float:
    bounds = normalize_bounds(box) or [0.0, 0.0, 0.0, 0.0]
    return max(0.0, bounds[2] - bounds[0])


def _box_height(box: Sequence[Any]) -> float:
    bounds = normalize_bounds(box) or [0.0, 0.0, 0.0, 0.0]
    return max(0.0, bounds[3] - bounds[1])


def _box_area(box: Sequence[Any]) -> float:
    return _box_width(box) * _box_height(box)


def _intersection(first: Sequence[Any], second: Sequence[Any]) -> list[float] | None:
    a = normalize_bounds(first)
    b = normalize_bounds(second)
    if a is None or b is None:
        return None
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _iou(first: Sequence[Any], second: Sequence[Any]) -> float:
    inter = _intersection(first, second)
    if inter is None:
        return 0.0
    union = _box_area(first) + _box_area(second) - _box_area(inter)
    if union <= 0.0:
        return 0.0
    return _box_area(inter) / union


def _containment_ratio(child: Sequence[Any], parent: Sequence[Any]) -> float:
    inter = _intersection(child, parent)
    if inter is None:
        return 0.0
    area = _box_area(child)
    if area <= 0.0:
        return 0.0
    return _box_area(inter) / area


def _vertical_overlap_ratio(first: Sequence[Any], second: Sequence[Any]) -> float:
    a = normalize_bounds(first)
    b = normalize_bounds(second)
    if a is None or b is None:
        return 0.0
    top = max(a[1], b[1])
    bottom = min(a[3], b[3])
    overlap = max(0.0, bottom - top)
    return overlap / max(1.0, min(_box_height(a), _box_height(b)))


def _horizontal_overlap_ratio(first: Sequence[Any], second: Sequence[Any]) -> float:
    a = normalize_bounds(first)
    b = normalize_bounds(second)
    if a is None or b is None:
        return 0.0
    left = max(a[0], b[0])
    right = min(a[2], b[2])
    overlap = max(0.0, right - left)
    return overlap / max(1.0, min(_box_width(a), _box_width(b)))


def _clamp_box(box: Sequence[Any], clip: Sequence[Any]) -> list[float] | None:
    source = normalize_bounds(box)
    target = normalize_bounds(clip)
    if source is None or target is None:
        return None
    left = min(max(source[0], target[0]), target[2])
    top = min(max(source[1], target[1]), target[3])
    right = min(max(source[2], target[0]), target[2])
    bottom = min(max(source[3], target[1]), target[3])
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _inset_box(box: Sequence[Any], amount: float = 4.0) -> list[float] | None:
    bounds = normalize_bounds(box)
    if bounds is None:
        return None
    inset = max(0.0, float(amount))
    if _box_width(bounds) <= inset * 2.0 + 3.0 or _box_height(bounds) <= inset * 2.0 + 3.0:
        return bounds
    return [bounds[0] + inset, bounds[1] + inset, bounds[2] - inset, bounds[3] - inset]


def _clip_or_snap_to_plot(box: Sequence[Any], plot: Sequence[Any], *, thin: bool = False) -> list[float] | None:
    clipped = _intersection(box, plot)
    if clipped is not None:
        return clipped
    return None


def _point_from_value(value: object) -> list[float] | None:
    mapping = _mapping(value)
    point = _sequence(value)
    if mapping:
        x = _float(
            mapping.get("x", mapping.get("center_x", mapping.get("left", mapping.get("x0", mapping.get("price_x"))))),
            float("nan"),
        )
        y = _float(
            mapping.get("y", mapping.get("center_y", mapping.get("top", mapping.get("y0", mapping.get("price_y"))))),
            float("nan"),
        )
    elif len(point) >= 2:
        x = _float(point[0], float("nan"))
        y = _float(point[1], float("nan"))
    else:
        return None
    if x != x or y != y:
        return None
    return [float(x), float(y)]


def _anchor_points_from_row(row: Mapping[str, Any]) -> list[list[float]]:
    points: list[list[float]] = []
    seen: set[tuple[float, float]] = set()
    for key in ("touch_points", "line_points", "points", "anchors", "path"):
        for item in _sequence(row.get(key)):
            point = _point_from_value(item)
            if point is None:
                continue
            rounded = (round(point[0], 3), round(point[1], 3))
            if rounded in seen:
                continue
            seen.add(rounded)
            points.append([rounded[0], rounded[1]])
    for key in ("start_point", "end_point", "nearest", "anchor_point"):
        point = _point_from_value(row.get(key))
        if point is None:
            continue
        rounded = (round(point[0], 3), round(point[1], 3))
        if rounded in seen:
            continue
        seen.add(rounded)
        points.append([rounded[0], rounded[1]])
    return points


def _points_inside_box(points: Sequence[Sequence[Any]], box: Sequence[Any], padding: float = 0.0) -> list[list[float]]:
    bounds = normalize_bounds(box)
    if bounds is None:
        return []
    pad = max(0.0, float(padding))
    inside: list[list[float]] = []
    for item in points:
        point = _point_from_value(item)
        if point is None:
            continue
        if bounds[0] - pad <= point[0] <= bounds[2] + pad and bounds[1] - pad <= point[1] <= bounds[3] + pad:
            inside.append(point)
    return inside


def _has_anchor_evidence_mapping(row: Mapping[str, Any]) -> bool:
    evidence = _mapping(row.get("anchor_evidence"))
    if not evidence or evidence.get("valid") is not True:
        return False
    if _sequence(evidence.get("candle_indices")) or _sequence(evidence.get("anchor_candle_indices")):
        return True
    return bool(_anchor_points_from_row({"touch_points": evidence.get("touch_points")}))


def _has_positive_anchor_count(row: Mapping[str, Any]) -> bool:
    for key in ("touch_count", "wick_probe_count", "reaction_count", "retest_count", "sweep_count", "candle_count"):
        if _float(row.get(key), 0.0) > 0.0:
            return True
    return False


def _has_hard_anchor_evidence(row: Mapping[str, Any]) -> bool:
    if _has_anchor_evidence_mapping(row):
        return True
    if _sequence(row.get("anchor_candle_indices")) or _sequence(row.get("anchor_candles")) or _sequence(row.get("source_indices")):
        return True
    if _anchor_points_from_row(row):
        return True
    if _has_positive_anchor_count(row):
        return True
    if row.get("line_y") is not None and (row.get("line_x0") is not None or row.get("line_x1") is not None):
        return _has_positive_anchor_count(row) or bool(_anchor_points_from_row(row))
    return False


def _anchor_rejection_reason(row: Mapping[str, Any]) -> str:
    for key in ("parent_overlay_id", "parent_label"):
        if row.get(key) not in (None, "", [], {}):
            return "parent_only_anchor"
    if row.get("line_y") is not None and (row.get("line_x0") is not None or row.get("line_x1") is not None):
        return "line_level_without_touch_evidence"
    for key in (
        "source_path",
        "structural_anchor",
        "anchored",
        "still_significant",
        "zone_family",
        "liquidity_pool_type",
        "liquidity_source",
        "role_flip_state",
        "zone_stack_id",
        "source_rule",
        "validation_reason",
        "replay_sequence",
        "replay_action",
        "story",
        "knowledge_tags",
    ):
        if row.get(key) not in (None, "", [], {}):
            return "metadata_only_anchor"
    return "floating_unanchored_overlay"


def _has_structural_anchor(row: Mapping[str, Any]) -> bool:
    return _has_hard_anchor_evidence(row)


def _snap_box_to_anchor_evidence(row: Mapping[str, Any], bounds: Sequence[Any], plot: Sequence[Any]) -> tuple[list[float], list[str]]:
    box = normalize_bounds(bounds)
    clip = normalize_bounds(plot)
    if box is None or clip is None:
        return list(normalize_bounds(bounds) or [0.0, 0.0, 0.0, 0.0]), []
    overlay_type = str(row.get("type") or "")
    if overlay_type not in FLOATING_REJECT_TYPES:
        return [round(float(value), 3) for value in box], []
    points = _anchor_points_from_row(row)
    if not points:
        return [round(float(value), 3) for value in box], []
    plot_w = max(1.0, clip[2] - clip[0])
    plot_h = max(1.0, clip[3] - clip[1])
    inside_plot = _points_inside_box(points, clip, padding=1.0)
    inside_current = _points_inside_box(inside_plot, box, padding=14.0)
    selected = inside_current or inside_plot
    if not selected:
        return [round(float(value), 3) for value in box], []

    xs = [point[0] for point in selected]
    ys = [point[1] for point in selected]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    flags: list[str] = []
    refined = list(box)

    if overlay_type in ZONE_TYPES:
        vertical_pad = max(8.0, min(plot_h * 0.055, max(_box_height(box) * 0.22, 10.0)))
        top = max(clip[1], min_y - vertical_pad)
        bottom = min(clip[3], max_y + vertical_pad)
        max_height = plot_h * 0.12
        if bottom - top > max_height:
            center = (min_y + max_y) * 0.5
            top = max(clip[1], center - max_height * 0.5)
            bottom = min(clip[3], top + max_height)
        if bottom > top and (abs(top - refined[1]) > 0.5 or abs(bottom - refined[3]) > 0.5):
            refined[1] = top
            refined[3] = bottom
            flags.append("anchor_snap_refined")
        if max_x > min_x and _box_width(box) > plot_w * 0.26:
            horizontal_pad = max(18.0, min(plot_w * 0.035, _box_width(box) * 0.12))
            left = max(clip[0], min_x - horizontal_pad)
            right = min(clip[2], max_x + horizontal_pad)
            if right - left >= 12.0 and (abs(left - refined[0]) > 0.5 or abs(right - refined[2]) > 0.5):
                refined[0] = left
                refined[2] = right
                if "anchor_snap_refined" not in flags:
                    flags.append("anchor_snap_refined")
    else:
        horizontal_pad = max(12.0, min(plot_w * 0.045, _box_width(box) * 0.35))
        vertical_pad = max(8.0, min(plot_h * 0.045, _box_height(box) * 0.40))
        left = max(clip[0], min_x - horizontal_pad)
        right = min(clip[2], max_x + horizontal_pad)
        top = max(clip[1], min_y - vertical_pad)
        bottom = min(clip[3], max_y + vertical_pad)
        if right - left >= 8.0 and bottom - top >= 6.0:
            candidate = _clamp_box([left, top, right, bottom], box)
            if candidate is not None and _box_area(candidate) > 0.0:
                refined = candidate
                flags.append("anchor_snap_refined")

    clamped = _clamp_box(refined, clip) or box
    return [round(float(value), 3) for value in clamped], flags


def _bounds_for_overlay(row: Mapping[str, Any], scene: Mapping[str, Any]) -> tuple[list[float] | None, list[float] | None, str]:
    raw = normalize_bounds(row.get("bounds") or row.get("bbox"))
    if raw is None:
        return None, None, "invalid_bounds"
    raw_bounds = raw
    coordinate_mode = str(row.get("coordinate_mode") or "").upper()
    chart_bounds = normalize_bounds(scene.get("chart_region_chart_bounds") or [0, 0, 1, 1]) or [0.0, 0.0, 1.0, 1.0]
    plot_chart = normalize_bounds(scene.get("plot_area_chart_bounds") or chart_bounds) or chart_bounds
    plot_full = normalize_bounds(scene.get("plot_area_bounds") or plot_chart) or plot_chart
    broker_surface = normalize_bounds(scene.get("broker_surface_bounds") or plot_full) or plot_full
    right_panel = normalize_bounds(scene.get("right_order_panel_bounds") or plot_full) or plot_full
    raw_is_normalized = max(abs(value) for value in raw_bounds) <= 1.0001

    def scale_to(bounds: Sequence[float]) -> list[float]:
        bw = max(1.0, float(bounds[2]) - float(bounds[0]))
        bh = max(1.0, float(bounds[3]) - float(bounds[1]))
        return [
            float(bounds[0]) + raw_bounds[0] * bw,
            float(bounds[1]) + raw_bounds[1] * bh,
            float(bounds[0]) + raw_bounds[2] * bw,
            float(bounds[1]) + raw_bounds[3] * bh,
        ]

    if row.get("type") == "BROKER_CONTROL":
        if raw_is_normalized:
            raw_bounds = scale_to(broker_surface)
        return raw_bounds, right_panel, "broker_controls"

    if coordinate_mode in {"FULL_BROKER_SURFACE", "WINDOW_SPACE"}:
        if raw_is_normalized:
            raw_bounds = scale_to(broker_surface)
        return raw_bounds, plot_full, "full_broker_surface"

    if max(abs(value) for value in raw_bounds) <= 1.0001:
        if coordinate_mode == "PLOT_AREA_NORMALIZED":
            pw = max(1.0, plot_chart[2] - plot_chart[0])
            ph = max(1.0, plot_chart[3] - plot_chart[1])
            raw_bounds = [plot_chart[0] + raw_bounds[0] * pw, plot_chart[1] + raw_bounds[1] * ph, plot_chart[0] + raw_bounds[2] * pw, plot_chart[1] + raw_bounds[3] * ph]
        else:
            cw = max(1.0, chart_bounds[2] - chart_bounds[0])
            ch = max(1.0, chart_bounds[3] - chart_bounds[1])
            raw_bounds = [chart_bounds[0] + raw_bounds[0] * cw, chart_bounds[1] + raw_bounds[1] * ch, chart_bounds[0] + raw_bounds[2] * cw, chart_bounds[1] + raw_bounds[3] * ch]
    return raw_bounds, plot_chart, "chart_image_space"


def _project_normalized_geometry(
    row: Mapping[str, Any],
    scene: Mapping[str, Any],
) -> dict[str, Any]:
    """Project every public geometry field through the same chart transform."""
    coordinate_mode = str(row.get("coordinate_mode") or "").upper()
    if coordinate_mode not in {"CHART_NORMALIZED", "PLOT_AREA_NORMALIZED"}:
        return dict(row)
    chart_bounds = normalize_bounds(
        scene.get("chart_region_chart_bounds") or [0, 0, 1, 1]
    ) or [0.0, 0.0, 1.0, 1.0]
    target = chart_bounds
    if coordinate_mode == "PLOT_AREA_NORMALIZED":
        target = normalize_bounds(scene.get("plot_area_chart_bounds") or chart_bounds) or chart_bounds
    width = max(1.0, target[2] - target[0])
    height = max(1.0, target[3] - target[1])

    def project_point(value: object) -> list[float] | None:
        point = _point_from_value(value)
        if point is None:
            return None
        if max(abs(point[0]), abs(point[1])) > 1.0001:
            return point
        return [target[0] + point[0] * width, target[1] + point[1] * height]

    projected = dict(row)
    for key in ("bounds", "bbox", "tight_bounds", "expanded_bounds", "raw_bounds"):
        bounds = normalize_bounds(row.get(key))
        if bounds is None or max(abs(value) for value in bounds) > 1.0001:
            continue
        projected[key] = [
            target[0] + bounds[0] * width,
            target[1] + bounds[1] * height,
            target[0] + bounds[2] * width,
            target[1] + bounds[3] * height,
        ]
    for key in (
        "line_points",
        "points",
        "path",
        "touch_points",
        "anchor_wick_points",
        "trendline_touch_points",
        "anchors",
    ):
        values = _sequence(row.get(key))
        if not values:
            continue
        points = [point for item in values if (point := project_point(item)) is not None]
        if points:
            projected[key] = points
    for key in ("start", "end", "start_point", "end_point", "label_position"):
        point = project_point(row.get(key))
        if point is not None:
            projected[key] = point
    anchor_evidence = _mapping(row.get("anchor_evidence"))
    if anchor_evidence:
        evidence_points = [
            point
            for item in _sequence(anchor_evidence.get("touch_points"))
            if (point := project_point(item)) is not None
        ]
        if evidence_points:
            projected["anchor_evidence"] = {
                **anchor_evidence,
                "touch_points": evidence_points,
            }
    projected["coordinate_mode"] = "CHART_IMAGE_SPACE"
    return projected


def _tighten_box(row: Mapping[str, Any], bounds: Sequence[Any], plot: Sequence[Any]) -> tuple[list[float] | None, list[str]]:
    box = normalize_bounds(bounds)
    clip = normalize_bounds(plot)
    if box is None or clip is None:
        return None, ["invalid_bounds"]
    overlay_type = str(row.get("type") or "")
    flags: list[str] = []
    plot_w = max(1.0, clip[2] - clip[0])
    plot_h = max(1.0, clip[3] - clip[1])
    if overlay_type == "INVALIDATION_BOX":
        box = _clip_or_snap_to_plot(box, clip, thin=True)
    else:
        box = _clip_or_snap_to_plot(box, clip)
    if box is None:
        return None, ["outside_plot_area"]

    max_width_ratio = 1.0
    max_height_ratio = 1.0
    if overlay_type in ZONE_TYPES:
        max_width_ratio = 0.42
        max_height_ratio = 0.135
    elif overlay_type == "LIQUIDITY_POOL":
        # Liquidity is a price level. A chart-height zone here is a semantic
        # error, even when an older producer still supplies the parent zone.
        max_width_ratio = 1.0
        max_height_ratio = 0.014
    elif overlay_type in {"SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX"}:
        max_width_ratio = 0.18
        max_height_ratio = 0.115
    elif overlay_type == "INVALIDATION_BOX":
        max_width_ratio = 0.18
        max_height_ratio = 0.020
    elif overlay_type == "CONTINUATION_BOX":
        max_width_ratio = 0.30
        max_height_ratio = 0.34
    elif overlay_type in {"IMPULSE_BOX", "PULLBACK_BOX", "PROGRESSION_PATH"}:
        max_width_ratio = 0.72
        max_height_ratio = 0.72
    elif overlay_type == "CURRENT_CANDLE":
        max_width_ratio = 0.035
        max_height_ratio = 0.24

    width = _box_width(box)
    height = _box_height(box)
    if width > plot_w * max_width_ratio:
        flags.append("width_refined")
        cap = plot_w * max_width_ratio
        if overlay_type in ZONE_TYPES:
            box[0] = max(clip[0], box[2] - cap)
        else:
            center = (box[0] + box[2]) * 0.5
            box[0] = max(clip[0], center - cap * 0.5)
            box[2] = min(clip[2], box[0] + cap)
    if height > plot_h * max_height_ratio:
        flags.append("height_refined")
        cap = plot_h * max_height_ratio
        center = (box[1] + box[3]) * 0.5
        if overlay_type == "LIQUIDITY_POOL":
            center = _float(row.get("price_level_y", row.get("line_y")), center)
            cap = max(4.0, min(10.0, cap))
        if overlay_type == "INVALIDATION_BOX":
            cap = max(3.0, min(cap, 6.0))
        box[1] = max(clip[1], center - cap * 0.5)
        box[3] = min(clip[3], box[1] + cap)
    box = _clamp_box(box, clip)
    if box is None:
        return None, ["outside_plot_area"]
    if overlay_type in ACTIONABLE_TYPES and _has_structural_anchor(row):
        min_size_by_type: dict[str, tuple[float, float]] = {
            "SNIPER_ENTRY_BOX": (14.0, 10.0),
            "RETEST_BOX": (14.0, 10.0),
            "TARGET_ZONE_BOX": (24.0, 8.0),
            "INVALIDATION_BOX": (24.0, 4.0),
            "CONTINUATION_BOX": (28.0, 14.0),
        }
        min_width, min_height = min_size_by_type.get(overlay_type, (12.0, 8.0))
        if _box_width(box) < min_width or _box_height(box) < min_height:
            center_x = (box[0] + box[2]) * 0.5
            center_y = (box[1] + box[3]) * 0.5
            expanded = _clamp_box(
                [
                    center_x - min_width * 0.5,
                    center_y - min_height * 0.5,
                    center_x + min_width * 0.5,
                    center_y + min_height * 0.5,
                ],
                clip,
            )
            if expanded is not None:
                box = expanded
                flags.append("min_anchor_box_expanded")
    if _box_width(box) < 3.0 or _box_height(box) < 3.0:
        return None, ["too_small_after_refinement"]
    return [round(float(value), 3) for value in box], flags


def _priority(row: Mapping[str, Any]) -> tuple[int, float, float]:
    return (
        overlay_type_priority(row.get("type")),
        _clip01(row.get("truth_score", row.get("confidence", 0.0))),
        _float(row.get("z_index"), 0.0),
    )


def _mode_emphasizes_type(mode: str, overlay_type: str, layer: str) -> bool:
    normalized_mode = normalize_view_mode(mode)
    if normalized_mode == "CLEAN_LIVE":
        return overlay_type in EXECUTION_FOCUS_TYPES | {"CURRENT_CANDLE", "CONTINUATION_BOX"}
    if normalized_mode == "SUPPLY_DEMAND":
        return overlay_type in ZONE_TYPES
    if normalized_mode == "TRENDLINES":
        return overlay_type in TRENDLINE_TYPES
    if normalized_mode == "SMART_MONEY":
        return overlay_type in SMART_MONEY_TYPES
    if normalized_mode == "TRIGGER":
        return overlay_type in {"SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX"}
    if normalized_mode == "TARGET":
        return overlay_type in {"TARGET_ZONE_BOX", "OPPOSING_FORCE"}
    if normalized_mode in {"BROKER", "CALIBRATION"}:
        return overlay_type == "BROKER_CONTROL"
    if normalized_mode == "GLOBAL":
        return overlay_type in {"IMPULSE_BOX", "PROGRESSION_PATH"} or layer == "major_swings"
    if normalized_mode == "LOCAL":
        return overlay_type in {"PULLBACK_BOX", "CONTINUATION_BOX", "SNIPER_ENTRY_BOX", "CURRENT_CANDLE"}
    if normalized_mode == "COUNCIL":
        return overlay_type in {
            "MODEL_COUNCIL_MARKER",
            "REGIME_MARKER",
            "MARKET_PLAY_MARKER",
            "PRICE_LOCATION_MARKER",
            "TWO_CANDLE_STUDY",
            "LSTM_STUDY",
            "SNIPER_ENTRY_BOX",
            "TARGET_ZONE_BOX",
            "SUPPLY_ZONE",
            "DEMAND_ZONE",
            "OPPOSING_FORCE",
        }
    if normalized_mode == "TWO_CANDLE_STUDY":
        return overlay_type == "TWO_CANDLE_STUDY"
    if normalized_mode == "LSTM_STUDY":
        return overlay_type == "LSTM_STUDY"
    if normalized_mode in {"FULL_HISTORY_READ", "REPLAY"}:
        return overlay_type in HISTORY_TYPES | STRUCTURE_TYPES | EXECUTION_FOCUS_TYPES | ZONE_TYPES | TRENDLINE_TYPES
    if normalized_mode == "PATH":
        return overlay_type in HISTORY_TYPES
    if normalized_mode == "ACTIVE_CONTEXT":
        return overlay_type in EXECUTION_FOCUS_TYPES | STRUCTURE_TYPES | ZONE_TYPES | TRENDLINE_TYPES
    if normalized_mode in {"DIAGNOSTICS", "DEBUG", "INSPECTOR"}:
        return True
    return False


def _semantic_style_family(row: Mapping[str, Any]) -> str:
    overlay_type = str(row.get("type") or "")
    side = str(row.get("side") or "").upper()
    if overlay_type == "DEMAND_ZONE":
        return "demand"
    if overlay_type == "SUPPLY_ZONE":
        return "supply"
    if overlay_type == "SUPPORT_TRENDLINE":
        return "support"
    if overlay_type == "RESISTANCE_TRENDLINE":
        return "resistance"
    if overlay_type == "INNER_TRENDLINE":
        return "inner"
    if overlay_type in SMART_MONEY_TYPES:
        return "smart_money"
    if side == "BUY":
        return "buy"
    if side == "SELL":
        return "sell"
    if overlay_type in {"SNIPER_ENTRY_BOX", "CONTINUATION_BOX"}:
        return "trigger"
    if overlay_type == "TARGET_ZONE_BOX":
        return "target"
    if overlay_type == "INVALIDATION_BOX":
        return "invalidation"
    if overlay_type in HISTORY_TYPES:
        return "history"
    if overlay_type in TRENDLINE_TYPES:
        return "trendline"
    if overlay_type in DIAGNOSTIC_TYPES or str(row.get("layer") or "") == "diagnostics":
        return "diagnostic"
    return "context"


def _style_for_display_state(row: Mapping[str, Any], display_state: str, visual_weight: float) -> dict[str, Any]:
    state = display_state if display_state in DISPLAY_STATES else "COMPACT"
    weight = max(0.05, min(1.0, float(visual_weight)))
    opacity_by_state = {
        "FULL": 0.92,
        "FOCUS_EXPANDED": 1.0,
        "COMPACT": 0.72,
        "GROUPED": 0.66,
        "NESTED": 0.62,
        "GHOSTED": 0.44,
        "ICON_ONLY": 0.38,
        "INSPECTOR_LABEL": 0.42,
    }
    fill_by_state = {
        "FULL": 0.018,
        "FOCUS_EXPANDED": 0.022,
        "COMPACT": 0.012,
        "GROUPED": 0.008,
        "NESTED": 0.006,
        "GHOSTED": 0.0,
        "ICON_ONLY": 0.0,
        "INSPECTOR_LABEL": 0.0,
    }
    border_by_state = {
        "FULL": 2.75,
        "FOCUS_EXPANDED": 2.95,
        "COMPACT": 2.35,
        "GROUPED": 2.15,
        "NESTED": 1.95,
        "GHOSTED": 1.35,
        "ICON_ONLY": 1.20,
        "INSPECTOR_LABEL": 1.20,
    }
    label_mode = {
        "FULL": "full",
        "FOCUS_EXPANDED": "full",
        "COMPACT": "compact",
        "GROUPED": "summary",
        "NESTED": "compact",
        "GHOSTED": "inspector",
        "ICON_ONLY": "icon",
        "INSPECTOR_LABEL": "inspector",
    }[state]
    return {
        "semantic_family": _semantic_style_family(row),
        "opacity": round(max(0.15, min(1.0, opacity_by_state[state] * (0.72 + weight * 0.32))), 3),
        "border_width": round(border_by_state[state], 3),
        "fill_opacity": round(fill_by_state[state], 3),
        "label_mode": label_mode,
    }


def _display_state_for_row(row: Mapping[str, Any], mode: str, current_side: str) -> tuple[str, float, str]:
    overlay_type = str(row.get("type") or "")
    layer = str(row.get("layer") or overlay_layer_name(overlay_type, row.get("layer")))
    truth = _clip01(row.get("truth_score", row.get("confidence", 0.0)))
    side = str(row.get("side") or "").upper()
    active_side = bool(current_side in {"BUY", "SELL"} and side == current_side)
    emphasized = _mode_emphasizes_type(mode, overlay_type, layer)
    if row.get("precision_rejected") or overlay_type in DIAGNOSTIC_TYPES:
        return "INSPECTOR_LABEL", 0.15, "removed from live truth; retained for diagnostics inspector"
    if _historical_overlay_context(row):
        return "GHOSTED", max(0.22, min(0.42, truth * 0.28 + 0.14)), "historical geometry remains available without competing with the current plan"
    if current_side in {"BUY", "SELL"} and side in {"BUY", "SELL"} and side != current_side and overlay_type in ACTIONABLE_TYPES:
        return "GHOSTED", max(0.34, min(0.52, truth * 0.30 + 0.22)), "counter-side execution geometry remains visible but subdued"
    if overlay_type in EXECUTION_FOCUS_TYPES:
        weight = 0.90 + (0.07 if active_side else 0.0) + min(0.03, truth * 0.03)
        return "FULL", min(1.0, weight), "execution-relevant overlay remains expanded"
    if overlay_type == "CURRENT_CANDLE":
        return "COMPACT", 0.72, "current candle geometry remains visible with adaptive label"
    if emphasized:
        weight = max(0.72, truth * 0.34 + 0.52)
        return "FULL", min(1.0, weight), "selected V3 mode expands this overlay family"
    if row.get("child_overlay_ids"):
        return "GROUPED", max(0.48, min(0.72, truth * 0.42 + 0.30)), "parent overlay groups nested market structure"
    if _text(row.get("parent_overlay_id")) or _float(row.get("nesting_depth"), 0.0) > 0.0:
        return "NESTED", max(0.42, min(0.68, truth * 0.38 + 0.28)), "nested child overlay remains visible inside parent context"
    if overlay_type in ZONE_TYPES:
        state = "COMPACT" if truth >= 0.35 else "GHOSTED"
        return state, max(0.46, min(0.76, truth * 0.42 + 0.34)), "supply-demand context stays readable with proportional visual weight"
    if overlay_type in TRENDLINE_TYPES:
        return "COMPACT", max(0.54, min(0.80, truth * 0.42 + 0.36)), "support-resistance trendline stays separate from zones"
    if overlay_type in STRUCTURE_TYPES:
        return "COMPACT", max(0.38, min(0.64, truth * 0.38 + 0.24)), "structure overlay remains readable without dominating active execution"
    if overlay_type in HISTORY_TYPES:
        state = "COMPACT" if normalize_view_mode(mode) in {"FULL_HISTORY_READ", "REPLAY"} else "GHOSTED"
        return state, max(0.24, min(0.58, truth * 0.36 + 0.20)), "historical overlay remains visible as replay context"
    return "COMPACT", max(0.30, min(0.58, truth * 0.34 + 0.24)), "valid overlay receives compact representation"


def _apply_display_metadata(rows: Sequence[Mapping[str, Any]], mode: str, current_side: str) -> list[dict[str, Any]]:
    normalized_mode = normalize_view_mode(mode)
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        display_state, visual_weight, representation_reason = _display_state_for_row(row, mode, current_side)
        row["display_state"] = display_state
        row["visual_weight"] = round(float(visual_weight), 3)
        row["geometry_visible"] = bool(not row.get("precision_rejected"))
        row["inspector_visible"] = True
        row["representation_reason"] = representation_reason
        row["style"] = _style_for_display_state(row, display_state, visual_weight)
        row["label_mode"] = row["style"]["label_mode"]
        row["label_visible"] = bool(
            row.get("geometry_visible")
            and row.get("label_hidden") is not True
            and display_state not in {"GHOSTED", "ICON_ONLY", "INSPECTOR_LABEL", "INSPECTOR_ONLY_LABEL"}
        )
        overlay_id = _text(row.get("overlay_id") or row.get("id"))
        parent_id = _text(row.get("parent_overlay_id"))
        if parent_id:
            row.setdefault("group_id", f"group_{parent_id}")
            row.setdefault("group_type", "LOCAL_STRUCTURE_GROUP")
        elif row.get("child_overlay_ids") and overlay_id:
            row.setdefault("group_id", f"group_{overlay_id}")
            row.setdefault("group_type", "LOCAL_STRUCTURE_GROUP")
            children = len(_sequence(row.get("child_overlay_ids")))
            row.setdefault("summary_label", f"{children} CHILD OVERLAYS")
        if normalized_mode == "CANDLES" and str(row.get("type") or "") == "CURRENT_CANDLE":
            row["label_hidden"] = True
            row["label_anchor"] = "hidden"
            row["label_visible"] = False
        output.append(row)
    return output


def _label_limit_for_mode(mode: str) -> int:
    normalized_mode = normalize_view_mode(mode)
    if normalized_mode in {"DIAGNOSTICS", "DEBUG", "INSPECTOR"}:
        return 60
    if normalized_mode in {"FULL_HISTORY_READ", "REPLAY"}:
        return 34
    if normalized_mode == "ACTIVE_CONTEXT":
        return 28
    if normalized_mode in {"SUPPLY_DEMAND", "TRENDLINES", "PATH"}:
        return 24
    return 18


def _apply_adaptive_label_policy(rows: Sequence[Mapping[str, Any]], mode: str) -> list[dict[str, Any]]:
    output = [dict(row) for row in rows]
    label_limit = _label_limit_for_mode(mode)
    candidates: list[tuple[float, int, int]] = []
    for index, row in enumerate(output):
        if row.get("precision_rejected") or row.get("label_hidden") is True:
            continue
        if str(row.get("display_state") or "") in {"GHOSTED", "ICON_ONLY", "INSPECTOR_LABEL", "INSPECTOR_ONLY_LABEL"}:
            continue
        candidates.append((_float(row.get("visual_weight"), 0.0), overlay_type_priority(row.get("type")), index))
    candidates.sort(reverse=True)
    keep_indexes = {index for _weight, _priority_value, index in candidates[:label_limit]}
    for index, row in enumerate(output):
        display_state = str(row.get("display_state") or "COMPACT")
        overlay_type = str(row.get("type") or "")
        label = _text(row.get("display_label") or row.get("short_label") or row.get("label"))
        hidden_by_state = display_state in {"GHOSTED", "ICON_ONLY", "INSPECTOR_LABEL", "INSPECTOR_ONLY_LABEL"}
        label_priority_type = overlay_type in ALWAYS_LABEL_TYPES
        hidden_by_budget = (
            not label_priority_type
            and index not in keep_indexes
            and index in {candidate[2] for candidate in candidates}
        )
        long_low_weight_label = (
            not label_priority_type
            and len(label) > 24
            and _float(row.get("visual_weight"), 0.0) < 0.75
        )
        if overlay_type == "CURRENT_CANDLE" and normalize_view_mode(mode) == "CLEAN_LIVE":
            row["label_hidden"] = True
            row["label_anchor"] = "hidden"
            row["label_visible"] = False
        elif hidden_by_state or hidden_by_budget or long_low_weight_label:
            row["label_hidden"] = True
            row["label_anchor"] = "inspector"
            row["label_visible"] = False
            if display_state in {"GHOSTED", "ICON_ONLY"}:
                row["label_mode"] = "inspector"
            elif display_state not in {"FULL", "FOCUS_EXPANDED"} or overlay_type not in EXECUTION_FOCUS_TYPES:
                row["display_state"] = "INSPECTOR_LABEL"
                row["label_mode"] = "inspector"
                row["style"] = _style_for_display_state(row, "INSPECTOR_LABEL", _float(row.get("visual_weight"), 0.15))
        else:
            row["label_visible"] = row.get("label_hidden") is not True
            row["label_lane"] = _text(row.get("label_anchor"), "inside")
        row["geometry_visible"] = bool(not row.get("precision_rejected"))
        row["inspector_visible"] = True
        if row.get("label_anchor") in {"top", "right", "left", "bottom", "inside"}:
            row["label_lane"] = str(row.get("label_anchor"))
        elif row.get("label_anchor") == "inspector":
            row["label_lane"] = "inspector"
    return output


PROFESSIONAL_SINGLE_LABEL_TYPES = {
    "ORDER_BLOCK",
    "FAIR_VALUE_GAP",
    "LIQUIDITY_POOL",
    "SNIPER_ENTRY_BOX",
    "RETEST_BOX",
    "TARGET_ZONE_BOX",
    "INVALIDATION_BOX",
}


def _hide_overlay_label(row: dict[str, Any], reason: str) -> None:
    row["label_hidden"] = True
    row["label_visible"] = False
    row["label_anchor"] = "inspector"
    row["label_lane"] = "inspector"
    row.setdefault("precision_flags", []).append(reason)


def _label_relevance(row: Mapping[str, Any], current_side: str) -> tuple[int, int, int, int, float, float, int]:
    source_path = _text(row.get("source_path")).lower()
    lifecycle = _text(row.get("lifecycle_state")).upper()
    side = _text(row.get("side")).upper()
    source_rank = 0
    if ".projection." in source_path:
        source_rank = 5
    elif ".structure_boxes" in source_path and "current" in _text(row.get("source_key")).lower():
        source_rank = 4
    elif ".structure_boxes" in source_path and "local" in _text(row.get("source_key")).lower():
        source_rank = 3
    elif ".structure_boxes" in source_path:
        source_rank = 2
    lifecycle_rank = {
        "FRESH_ACTIVE": 5,
        "ACTIVE": 4,
        "PREDICTED": 3,
        "MITIGATED_ACTIVE": 2,
        "HISTORICAL_ACTIVE": 1,
    }.get(lifecycle, 0)
    freshness_rank = {
        "FRESH": 3,
        "TESTED": 2,
        "MITIGATED": 1,
    }.get(_text(row.get("freshness_state")).upper(), 0)
    distance = _float(row.get("distance_to_latest_norm"), 9.0)
    anchors = [int(_float(item, -1.0)) for item in _sequence(row.get("anchor_candles"))]
    return (
        1 if current_side in {"BUY", "SELL"} and side == current_side else 0,
        1 if row.get("visible_default") is not False else 0,
        source_rank,
        lifecycle_rank + freshness_rank,
        -distance,
        _clip01(row.get("truth_score", row.get("confidence", 0.0))),
        max(anchors, default=-1),
    )


def _apply_professional_label_policy(
    rows: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    current_side: str,
) -> list[dict[str, Any]]:
    """Keep labels decision-oriented while preserving every valid geometry.

    The user can still toggle and inspect every overlay. Only repeated inline
    labels are collapsed; the strongest current representative remains labeled
    and every other object stays available to hover/inspection.
    """

    output = [dict(row) for row in rows]
    normalized_mode = normalize_view_mode(mode)
    current_rows = sorted(
        [row for row in output if _text(row.get("type")).upper() == "CURRENT_CANDLE" and not row.get("precision_rejected")],
        key=_current_candle_recency,
        reverse=True,
    )
    for index, row in enumerate(current_rows):
        row["is_latest_candle"] = index == 0
        if index == 0:
            row["display_label"] = "NOW"
            row["short_label"] = "NOW"
        else:
            row["display_label"] = "CANDLES"
            row["short_label"] = "CANDLES"
        if normalized_mode in {"CLEAN_LIVE", "CANDLES"} or index > 0:
            _hide_overlay_label(row, "historical_candle_label_suppressed")

    for row in output:
        if _historical_overlay_context(row):
            _hide_overlay_label(row, "historical_inline_label_suppressed")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in output:
        overlay_type = _text(row.get("type")).upper()
        if (
            overlay_type not in PROFESSIONAL_SINGLE_LABEL_TYPES
            or row.get("precision_rejected")
            or _historical_overlay_context(row)
            or row.get("label_hidden") is True
        ):
            continue
        side = _text(row.get("side")).upper()
        grouped.setdefault((overlay_type, side), []).append(row)
    for siblings in grouped.values():
        if len(siblings) <= 1:
            continue
        ranked = sorted(siblings, key=lambda row: _label_relevance(row, current_side), reverse=True)
        for row in ranked[1:]:
            _hide_overlay_label(row, "repeated_family_label_suppressed")
    return output


def _mark_rejected(row: Mapping[str, Any], reason: str) -> dict[str, Any]:
    output = dict(row)
    precision_flags = _string_list(output.get("precision_flags"))
    if reason not in precision_flags:
        precision_flags.append(reason)
    output["precision_flags"] = precision_flags
    output["visible_default"] = False
    output["visible_modes"] = ["DEBUG", "INSPECTOR"]
    output["precision_rejected"] = True
    output["precision_rejection_reason"] = reason
    output["lifecycle_state"] = "DEBUG"
    output["layer"] = "diagnostics"
    output["display_label"] = short_label_for_overlay(output.get("type"), output.get("side"), output.get("label"))
    output["short_label"] = output["display_label"]
    return output


def _only_modes(row: Mapping[str, Any], allowed_modes: set[str], default_modes: Sequence[str]) -> list[str]:
    modes: list[str] = []
    for item in _sequence(row.get("visible_modes")):
        normalized = normalize_view_mode(item)
        if normalized in allowed_modes and normalized not in modes:
            modes.append(normalized)
    return modes or list(default_modes)


def _with_modes(modes: Sequence[str], extra_modes: Sequence[str]) -> list[str]:
    output = [normalize_view_mode(mode) for mode in modes if str(mode or "").strip()]
    for mode in extra_modes:
        normalized = normalize_view_mode(mode)
        if normalized not in output:
            output.append(normalized)
    return output


def _historical_current_marker(row: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("layer", "role", "source_agent", "reason", "label", "raw_display_label", "lifecycle_state")
    ).lower()
    return any(token in haystack for token in ("history", "historical", "replay", "memory"))


def _historical_overlay_context(row: Mapping[str, Any]) -> bool:
    lifecycle = _text(row.get("lifecycle_state")).upper()
    layer = _text(row.get("layer")).lower()
    return bool(
        lifecycle in {"HISTORICAL", "REPLAY", "ARCHIVED", "HISTORICAL_ACTIVE", "BROKEN_REFERENCE", "CONSUMED_REFERENCE"}
        or layer in {"historical_replay", "replay"}
        or _text(row.get("role")).lower().startswith("replay_")
    )


def _current_candle_recency(row: Mapping[str, Any]) -> tuple[int, int, float, float]:
    explicit_latest = row.get("is_latest_candle") is True or _text(row.get("is_latest_candle")).lower() == "true"
    label = _text(row.get("display_label") or row.get("short_label") or row.get("label")).upper()
    anchors = [int(_float(item, -1.0)) for item in _sequence(row.get("anchor_candles"))]
    candle_index = int(_float(row.get("candle_index"), -1.0))
    bounds = normalize_bounds(row.get("bounds") or row.get("bbox"))
    right = bounds[2] if bounds is not None else -1.0
    return (
        1 if explicit_latest or label == "NOW" or _text(row.get("role")).lower() == "current_candle" else 0,
        max([candle_index, *anchors], default=-1),
        float(right),
        _float(row.get("z_index"), 0.0),
    )


def _map_current_marker_to_history(row: dict[str, Any]) -> None:
    row["type"] = "PROGRESSION_PATH"
    row["layer"] = "historical_replay"
    row["lifecycle_state"] = "HISTORICAL"
    row["visible_default"] = False
    row["visible_modes"] = ["FULL_HISTORY_READ", "REPLAY", "INSPECTOR"]
    row["display_label"] = "HISTORICAL PROGRESSION"
    row["short_label"] = "HISTORICAL PROGRESSION"
    row["role"] = "history"
    row.setdefault("precision_flags", []).append("duplicate_now_mapped_to_history")


def _apply_current_candle_policy(rows: Sequence[Mapping[str, Any]], mode: str = "CLEAN_LIVE") -> tuple[list[dict[str, Any]], int]:
    output = [dict(row) for row in rows]
    normalized_mode = normalize_view_mode(mode)
    current_rows = [
        row
        for row in sorted(output, key=_current_candle_recency, reverse=True)
        if str(row.get("type") or "") == "CURRENT_CANDLE" and not row.get("precision_rejected")
    ]
    if normalized_mode == "CANDLES":
        for index, row in enumerate(current_rows):
            row["visible_modes"] = _only_modes(row, {"CANDLES", "INSPECTOR", "DEBUG", "DIAGNOSTICS"}, ["CANDLES", "INSPECTOR"])
            row["display_label"] = "NOW" if index == 0 else "CANDLES"
            row["short_label"] = row["display_label"]
            row["label_hidden"] = True
            row["label_anchor"] = "hidden"
            row["label_visible"] = False
            row["geometry_visible"] = True
        return output, 0
    historical_rows = [row for row in current_rows if _historical_current_marker(row)]
    live_rows = [row for row in current_rows if not _historical_current_marker(row)]
    duplicates_hidden = 0
    for row in historical_rows:
        duplicates_hidden += 1
        _map_current_marker_to_history(row)
    for index, row in enumerate(live_rows):
        if index == 0:
            row["visible_modes"] = _with_modes(
                _only_modes(
                    row,
                    CURRENT_CANDLE_LIVE_MODES,
                    ["CLEAN_LIVE", "CANDLES", "LOCAL", "ACTIVE_CONTEXT", "INSPECTOR"],
                ),
                [],
            )
            row["display_label"] = "NOW"
            row["short_label"] = "NOW"
            row["is_latest_candle"] = True
            row["label_hidden"] = False
            row["label_anchor"] = "top"
            row["label_visible"] = True
            continue
        duplicates_hidden += 1
        row["visible_default"] = False
        row["visible_modes"] = _with_modes(
            _only_modes(
                row,
                {"CANDLES", "DIAGNOSTICS", "DEBUG", "INSPECTOR"},
                ["CANDLES", "INSPECTOR"],
            ),
            ["INSPECTOR"],
        )
        row["label_hidden"] = True
        row["label_anchor"] = "hidden"
        row["label_visible"] = False
        row["display_state"] = "GHOSTED"
        row["is_latest_candle"] = False
        row.setdefault("precision_flags", []).append("duplicate_now_hidden_from_live")
    return output, duplicates_hidden


def _suppress_duplicates(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    duplicate_count = 0
    candidates = sorted((dict(row) for row in rows), key=_priority, reverse=True)
    for row in candidates:
        row_type = str(row.get("type") or "")
        row_bounds = normalize_bounds(row.get("bounds"))
        duplicate = False
        if row_bounds is not None and row_type in ZONE_TYPES | ACTIONABLE_TYPES:
            row_family = _duplicate_suppression_family(row)
            for existing in kept:
                if _duplicate_suppression_family(existing) != row_family:
                    continue
                existing_bounds = normalize_bounds(existing.get("bounds"))
                if existing_bounds is None:
                    continue
                if _iou(row_bounds, existing_bounds) >= 0.60 or (
                    _vertical_overlap_ratio(row_bounds, existing_bounds) >= 0.72
                    and _horizontal_overlap_ratio(row_bounds, existing_bounds) >= 0.48
                ):
                    duplicate = True
                    duplicate_count += 1
                    break
        if duplicate:
            rejected.append(_mark_rejected(row, "duplicate_weaker_track"))
        else:
            kept.append(row)
    return kept + rejected, duplicate_count


def _duplicate_suppression_family(row: Mapping[str, Any]) -> str:
    overlay_type = str(row.get("type") or "")
    if overlay_type in ZONE_TYPES:
        role = _text(row.get("role") or row.get("zone_role") or row.get("zone_family")).lower()
        return f"{overlay_type}:{role}" if role else overlay_type
    return overlay_type


def _separate_nested_sibling_bounds(loser: Sequence[float], winner: Sequence[float]) -> list[float] | None:
    loser_box = normalize_bounds(loser)
    winner_box = normalize_bounds(winner)
    if loser_box is None or winner_box is None:
        return None
    gap = 2.0
    min_width = 10.0
    min_height = 8.0
    candidates = [
        [loser_box[0], loser_box[1], min(loser_box[2], winner_box[0] - gap), loser_box[3]],
        [max(loser_box[0], winner_box[2] + gap), loser_box[1], loser_box[2], loser_box[3]],
        [loser_box[0], loser_box[1], loser_box[2], min(loser_box[3], winner_box[1] - gap)],
        [loser_box[0], max(loser_box[1], winner_box[3] + gap), loser_box[2], loser_box[3]],
    ]
    valid = [
        box
        for box in candidates
        if _box_width(box) >= min_width
        and _box_height(box) >= min_height
        and _box_area(box) >= max(80.0, _box_area(loser_box) * 0.06)
    ]
    if not valid:
        return None
    valid.sort(key=_box_area, reverse=True)
    return [round(float(value), 3) for value in valid[0]]


def _both_trendline_siblings(first_row: Mapping[str, Any], second_row: Mapping[str, Any]) -> bool:
    return str(first_row.get("type") or "") in TRENDLINE_TYPES and str(second_row.get("type") or "") in TRENDLINE_TYPES


def _apply_overlay_nesting(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    output = [dict(row) for row in rows]
    by_overlay_id: dict[str, dict[str, Any]] = {
        _text(row.get("overlay_id") or row.get("id")): row for row in output if _text(row.get("overlay_id") or row.get("id"))
    }
    parents = [
        row
        for row in output
        if not row.get("precision_rejected")
        and str(row.get("type") or "") in NEST_PARENT_TYPES
        and normalize_bounds(row.get("bounds")) is not None
    ]
    nested = 0
    tightened = 0
    collisions = 0
    for child in output:
        if child.get("precision_rejected") or str(child.get("type") or "") not in NEST_CHILD_TYPES:
            continue
        child_bounds = normalize_bounds(child.get("bounds"))
        if child_bounds is None:
            continue
        candidates: list[tuple[float, float, dict[str, Any]]] = []
        for parent in parents:
            parent_id = _text(parent.get("overlay_id") or parent.get("id"))
            child_id = _text(child.get("overlay_id") or child.get("id"))
            if not parent_id or parent_id == child_id:
                continue
            parent_bounds = normalize_bounds(parent.get("bounds"))
            if parent_bounds is None or _box_area(parent_bounds) <= _box_area(child_bounds) * 1.08:
                continue
            containment = _containment_ratio(child_bounds, parent_bounds)
            iou = _iou(child_bounds, parent_bounds)
            if containment < 0.25 and iou < 0.05:
                continue
            candidates.append((containment, -_box_area(parent_bounds), parent))
        if not candidates:
            child["nesting_depth"] = int(child.get("nesting_depth", 0) or 0)
            continue
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        containment, _negative_area, parent = candidates[0]
        parent_id = _text(parent.get("overlay_id") or parent.get("id"))
        parent_bounds = normalize_bounds(parent.get("bounds"))
        clip = _inset_box(parent_bounds or [], amount=4.0)
        if clip is not None:
            tightened_bounds = _clamp_box(child_bounds, clip)
            if tightened_bounds is not None and tightened_bounds != child_bounds:
                child["bounds"] = [round(float(value), 3) for value in tightened_bounds]
                child["bbox"] = list(child["bounds"])
                child["tight_bounds"] = list(child["bounds"])
                child.setdefault("precision_flags", []).append("nested_child_tightened_to_parent")
                tightened += 1
        child["parent_overlay_id"] = parent_id
        child["parent_type"] = str(parent.get("type") or "")
        child["nesting_depth"] = int(parent.get("nesting_depth", 0) or 0) + 1
        child["nesting_role"] = "child"
        child["containment_ratio"] = round(float(containment), 4)
        child.setdefault("precision_flags", []).append("nested_in_parent")
        nested += 1
        parent_row = by_overlay_id.get(parent_id)
        if parent_row is not None:
            children = [str(item) for item in _sequence(parent_row.get("child_overlay_ids"))]
            child_id = _text(child.get("overlay_id") or child.get("id"))
            if child_id and child_id not in children:
                children.append(child_id)
            parent_row["child_overlay_ids"] = children
            parent_row["nesting_role"] = "parent"
            parent_row["contains_nested_overlays"] = True
            parent_row.setdefault("precision_flags", []).append("nest_parent")

    by_parent: dict[str, list[tuple[dict[str, Any], list[float]]]] = {}
    for row in output:
        parent_id = _text(row.get("parent_overlay_id"))
        bounds = normalize_bounds(row.get("bounds"))
        if parent_id and bounds is not None and row.get("visible_default") is not False:
            by_parent.setdefault(parent_id, []).append((row, bounds))
    for siblings in by_parent.values():
        for index, (first_row, first_bounds) in enumerate(siblings):
            for other_index in range(index + 1, len(siblings)):
                second_row, second_bounds = siblings[other_index]
                if not (rectangles_overlap(first_bounds, second_bounds, padding=1.0) and _iou(first_bounds, second_bounds) > 0.72):
                    continue
                if _both_trendline_siblings(first_row, second_row):
                    first_row.setdefault("precision_flags", []).append("trendline_sibling_overlap_kept")
                    second_row.setdefault("precision_flags", []).append("trendline_sibling_overlap_kept")
                    continue
                if _priority(first_row) >= _priority(second_row):
                    winner_row, winner_bounds = first_row, first_bounds
                    loser_row, loser_bounds = second_row, second_bounds
                    loser_index = other_index
                else:
                    winner_row, winner_bounds = second_row, second_bounds
                    loser_row, loser_bounds = first_row, first_bounds
                    loser_index = index
                separated = _separate_nested_sibling_bounds(loser_bounds, winner_bounds)
                if separated is not None:
                    loser_row["bounds"] = separated
                    loser_row["bbox"] = list(separated)
                    loser_row["tight_bounds"] = list(separated)
                    loser_row.setdefault("precision_flags", []).append(
                        f"nested_sibling_separated_from_{_text(winner_row.get('type'), 'overlay').lower()}"
                    )
                    siblings[loser_index] = (loser_row, separated)
                    tightened += 1
                    continue
                loser_row["visible_default"] = False
                loser_row["visible_modes"] = ["DIAGNOSTICS", "DEBUG", "INSPECTOR"]
                loser_row["display_state"] = "INSPECTOR_LABEL"
                loser_row["label_hidden"] = True
                loser_row["label_anchor"] = "hidden"
                loser_row.setdefault("precision_flags", []).append(
                    f"nested_sibling_demoted_under_{_text(winner_row.get('type'), 'overlay').lower()}"
                )
                siblings[loser_index] = (loser_row, loser_bounds)
    return output, {
        "nested_overlays": nested,
        "nested_children_tightened": tightened,
        "nesting_collisions": collisions,
    }


def _reject_unanchored_floating_boxes(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    rejected = 0
    for raw in rows:
        row = dict(raw)
        overlay_type = str(row.get("type") or "")
        if row.get("precision_rejected") or overlay_type not in FLOATING_REJECT_TYPES:
            output.append(row)
            continue
        if _has_structural_anchor(row):
            output.append(row)
            continue
        rejected += 1
        reason = _anchor_rejection_reason(row)
        rejected_row = _mark_rejected(row, reason)
        rejected_row.setdefault("precision_flags", []).append(reason)
        rejected_row.setdefault("anchor_evidence_status", "MISSING_ANCHOR_EVIDENCE")
        output.append(rejected_row)
    return output, rejected


def _apply_clean_live_budget(rows: Sequence[Mapping[str, Any]], current_side: str) -> list[dict[str, Any]]:
    output = [dict(row) for row in rows]
    current_candle_visible = 0
    supply_demand_visible = 0
    actionable_visible = 0
    for row in sorted(output, key=_priority, reverse=True):
        overlay_type = str(row.get("type") or "")
        side = str(row.get("side") or "").upper()
        truth = _clip01(row.get("truth_score", row.get("confidence", 0.0)))
        if row.get("precision_rejected"):
            continue
        if truth < 0.20 and overlay_type != "CURRENT_CANDLE":
            row.update(_mark_rejected(row, "truth_score_below_live_threshold"))
            continue
        if current_side in {"BUY", "SELL"} and side in {"BUY", "SELL"} and side != current_side and overlay_type in ACTIONABLE_TYPES:
            row["display_state"] = "GHOSTED"
            row["label_hidden"] = True
            row["label_visible"] = False
            row["geometry_visible"] = True
            row.setdefault("precision_flags", []).append("counter_side_ghosted_not_hidden")
        if overlay_type in ZONE_TYPES:
            supply_demand_visible += 1
            if supply_demand_visible > 6:
                row["display_state"] = "GHOSTED"
                row["label_hidden"] = True
                row["label_visible"] = False
                row["geometry_visible"] = True
                row.setdefault("precision_flags", []).append("clean_live_zone_budget_ghosted_not_hidden")
        if overlay_type == "CURRENT_CANDLE":
            current_candle_visible += 1
            if current_candle_visible > 1:
                row["visible_default"] = False
                row["visible_modes"] = [mode for mode in _sequence(row.get("visible_modes")) if str(mode).upper() != "CLEAN_LIVE"]
                row.setdefault("precision_flags", []).append("clean_live_current_candle_budget_hidden")
        if overlay_type in ACTIONABLE_TYPES:
            actionable_visible += 1
            if actionable_visible > 10:
                row["display_state"] = "GHOSTED"
                row["label_hidden"] = True
                row["label_visible"] = False
                row["geometry_visible"] = True
                row.setdefault("precision_flags", []).append("clean_live_actionable_budget_ghosted_not_hidden")
    return output


def _apply_render_budget(rows: Sequence[Mapping[str, Any]], mode: str) -> list[dict[str, Any]]:
    normalized_mode = str(mode or "CLEAN_LIVE").strip().upper()
    budget = int(OVERLAY_RENDER_BUDGETS.get(normalized_mode, 0) or 0)
    if budget <= 0:
        return [dict(row) for row in rows]
    output = [dict(row) for row in rows]
    visible = [
        row
        for row in sorted(output, key=_priority, reverse=True)
        if not row.get("precision_rejected") and normalized_mode in {str(item).upper() for item in _sequence(row.get("visible_modes"))}
    ]
    for row in visible[budget:]:
        row["display_state"] = "GHOSTED"
        row["label_hidden"] = True
        row["label_visible"] = False
        row["geometry_visible"] = True
        row.setdefault("precision_flags", []).append(f"{normalized_mode.lower()}_render_budget_ghosted_not_hidden")
    return output


def _label_collision_count(rows: Sequence[Mapping[str, Any]]) -> int:
    visible = [normalize_bounds(row.get("label_bounds")) for row in rows if not row.get("label_hidden")]
    boxes = [box for box in visible if box is not None]
    count = 0
    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            if rectangles_overlap(first, second, padding=2.0):
                count += 1
    return count


@dataclass(frozen=True)
class OverlayPrecisionAuditV3:
    frame_id: int
    overlay_count: int
    rendered_count: int
    rejected_count: int
    precision_report: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OVERLAY_PRECISION_AUDIT_SCHEMA_VERSION,
            "frame_id": self.frame_id,
            "overlay_count": self.overlay_count,
            "rendered_count": self.rendered_count,
            "rejected_count": self.rejected_count,
            "precision_report": dict(self.precision_report),
        }


def resolve_precision_overlays_v3(
    overlays: Sequence[Mapping[str, Any]],
    *,
    scene_graph: Mapping[str, Any] | None = None,
    mode: str = "CLEAN_LIVE",
    current_side: str = "",
    frame_id: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_mode = normalize_view_mode(mode)
    scene = _scene_payload(scene_graph)
    plot_chart = normalize_bounds(scene.get("plot_area_chart_bounds") or scene.get("chart_region_chart_bounds") or [0, 0, 1000, 700]) or [0.0, 0.0, 1000.0, 700.0]
    label_layout_bounds = plot_chart
    if normalized_mode in {"BROKER", "CALIBRATION"}:
        label_layout_bounds = (
            normalize_bounds(scene.get("broker_surface_bounds"))
            or normalize_bounds(scene.get("right_order_panel_bounds"))
            or plot_chart
        )
    scene_frame = int(_float(scene.get("frame_id"), 0.0))
    if scene_frame <= 0 and frame_id is not None:
        scene_frame = int(_float(frame_id, 0.0))
    normalized: list[dict[str, Any]] = []
    missing_transform = 0
    stale_frame = 0
    unanchored = 0
    oversized = 0
    outside = 0
    anchor_snap_refined = 0
    for index, raw in enumerate(overlays):
        source_frame = raw.get("frame_id", raw.get("frame_index"))
        source_frame_missing = source_frame in (None, "")
        try:
            row = normalize_v3_overlay_object(
                raw,
                strict=False,
                fallback_index=index,
                frame_id=frame_id if source_frame in (None, "") else None,
            )
        except Exception:
            continue
        # Forecast belief/revision fields are not part of the generic visual
        # object schema, but they are part of the scene forecaster's public
        # safety contract. Precision refinement may alter geometry; it must
        # never erase which side is committed or which side is merely pending.
        for key in (
            "forecast_engine",
            "forecast_provider",
            "forecast_provider_status",
            "forecast_id",
            "forecast_revision",
            "belief_revision",
            "belief_state",
            "committed_side",
            "candidate_side",
            "change_probability",
            "confirmation_events",
            "required_events",
            "closed_candle_key",
            "closed_candle_sequence",
            "forecast_computed_frame_id",
            "source_forecast_frame_id",
            "geometry_projected_frame_id",
            "geometry_frame_match_verified",
            "geometry_reprojected_from_cache",
            "geometry_projection_provenance",
            "detector_coverage_rebase_applied",
            "cache_replaced_for_detector_coverage_rebase",
            "scene_feature_audit",
        ):
            if key in raw:
                row[key] = raw[key]
        row["layer"] = overlay_layer_name(row.get("type"), row.get("layer"))
        raw_display_label = _text(row.get("raw_display_label") or row.get("display_label") or row.get("label"))
        display_label, display_label_status, unmapped_display_label = normalize_overlay_display_label(
            raw_display_label,
            row.get("type"),
            row.get("side"),
        )
        row["raw_display_label"] = raw_display_label
        row["display_label"] = display_label
        row["short_label"] = display_label
        row["display_label_status"] = display_label_status
        row["unmapped_display_label"] = unmapped_display_label
        if str(raw.get("forecast_engine") or "").strip().upper() == "SCENE_FORECASTER_V3":
            # The scene forecaster still travels through the historical
            # LSTM_STUDY visual type for wire compatibility.  Do not let the
            # generic type label rename the replacement engine back to LSTM.
            scene_label = _text(
                raw.get("display_label")
                or raw.get("short_label")
                or raw.get("label"),
                "SCENE FORECASTER E1-E12",
            )
            if "SCENE" not in scene_label.upper():
                scene_label = "SCENE FORECASTER E1-E12"
            row["raw_display_label"] = scene_label
            row["display_label"] = scene_label
            row["short_label"] = scene_label
            row["label"] = scene_label
            row["display_label_status"] = "CANONICAL"
            row["unmapped_display_label"] = ""
        row["z_index"] = int(_float(row.get("z_index"), OVERLAY_TYPE_PRIORITY.get(str(row.get("type") or ""), 0)))
        row.setdefault("precision_flags", [])
        live_mode = normalized_mode in LIVE_VIEW_MODES
        transform_id = _text(row.get("chart_transform_id"))
        transform_token = transform_id.upper()
        transform_missing = not transform_id or any(
            token in transform_token for token in ("PENDING", "UNKNOWN", "MISSING")
        )
        if transform_missing:
            missing_transform += 1
            if live_mode:
                normalized.append(_mark_rejected(row, "missing_or_pending_chart_transform"))
                continue
        row_frame = int(_float(row.get("frame_id"), 0.0))
        if source_frame_missing or row_frame <= 0:
            stale_frame += 1
            if live_mode:
                normalized.append(_mark_rejected(row, "missing_source_frame_id"))
                continue
        if scene_frame and row_frame != scene_frame:
            stale_frame += 1
            if live_mode:
                normalized.append(_mark_rejected(row, "stale_source_frame_id"))
                continue
        coordinate_mode = _text(row.get("coordinate_mode")).upper()
        if live_mode and coordinate_mode == "PLOT_AREA_NORMALIZED" and normalize_bounds(
            scene.get("plot_area_chart_bounds")
        ) is None:
            normalized.append(_mark_rejected(row, "missing_plot_area_transform"))
            continue
        if live_mode and coordinate_mode == "CHART_NORMALIZED" and normalize_bounds(
            scene.get("chart_region_chart_bounds")
        ) is None:
            normalized.append(_mark_rejected(row, "missing_chart_region_transform"))
            continue
        source_agent = str(row.get("source_agent") or "").lower()
        if normalized_mode == "CLEAN_LIVE" and (
            "legacy_v2" in source_agent
            or str(row.get("type") or "") == "DEBUG_RAW_DETECTION"
        ):
            normalized.append(_mark_rejected(row, "legacy_or_raw_hidden_from_clean_live"))
            continue
        if not row.get("track_id") or not row.get("object_id") or str(row.get("anchor_type") or "").upper() in {"", "NONE", "UNKNOWN"}:
            unanchored += 1
            row["anchor_type"] = "BOX"
            precision_flags = _string_list(row.get("precision_flags"))
            precision_flags.append("anchor_defaulted")
            row["precision_flags"] = precision_flags
        # Every normalized overlay field must cross the same transform.  Study
        # paths are intentionally not box-tightened, but their points still
        # need to become chart-image pixels alongside their bounds.
        row = _project_normalized_geometry(row, scene)
        if row.get("type") in MARKET_OVERLAY_TYPES:
            raw_bounds, clip_bounds, _space = _bounds_for_overlay(row, scene)
            if raw_bounds is None or clip_bounds is None:
                normalized.append(_mark_rejected(row, "invalid_bounds"))
                continue
            refined_bounds, flags = _tighten_box(row, raw_bounds, clip_bounds)
            row["raw_bounds"] = [round(float(value), 3) for value in raw_bounds]
            row["precision_flags"] = _string_list(row.get("precision_flags")) + flags
            if refined_bounds is None:
                outside += 1
                normalized.append(_mark_rejected(row, "outside_plot_area"))
                continue
            if "height_refined" in flags or "width_refined" in flags:
                oversized += 1
            snapped_bounds, snap_flags = _snap_box_to_anchor_evidence(row, refined_bounds, clip_bounds)
            if snap_flags:
                anchor_snap_refined += 1
                row["precision_flags"] = _string_list(row.get("precision_flags")) + snap_flags
                refined_bounds = snapped_bounds
            row["bounds"] = refined_bounds
            row["bbox"] = refined_bounds
            row["tight_bounds"] = refined_bounds
            row["expanded_bounds"] = refined_bounds
            row["coordinate_mode"] = "CHART_IMAGE_SPACE" if row.get("coordinate_mode") not in {"FULL_BROKER_SURFACE", "WINDOW_SPACE"} else row.get("coordinate_mode")
            row["precision_space"] = _space
        normalized.append(row)

    if scene_frame:
        stale_frame = sum(
            1
            for row in normalized
            if int(_float(row.get("frame_id"), scene_frame)) != scene_frame
        )
    current_policy, duplicate_now_hidden = _apply_current_candle_policy(normalized, normalized_mode)
    suppressed, duplicate_count = _suppress_duplicates(current_policy)
    pre_anchored, pre_nesting_floating_rejected = _reject_unanchored_floating_boxes(suppressed)
    nested, nesting_report = _apply_overlay_nesting(pre_anchored)
    anchored, post_nesting_floating_rejected = _reject_unanchored_floating_boxes(nested)
    floating_rejected = pre_nesting_floating_rejected + post_nesting_floating_rejected
    budgeted = (
        _apply_clean_live_budget(anchored, str(current_side or "").upper())
        if normalized_mode == "CLEAN_LIVE"
        else [dict(row) for row in anchored]
    )
    budgeted = _apply_render_budget(budgeted, normalized_mode)
    represented = _apply_display_metadata(budgeted, normalized_mode, str(current_side or "").upper())
    laid_out = layout_overlay_labels(represented, chart_bounds=label_layout_bounds)
    for row in laid_out:
        if row.get("type") == "CURRENT_CANDLE" and normalized_mode in {"CLEAN_LIVE", "CANDLES"}:
            row["label_hidden"] = True
            row["label_anchor"] = "hidden"
            row["label_visible"] = False
    laid_out = _apply_adaptive_label_policy(laid_out, normalized_mode)
    laid_out = _apply_professional_label_policy(
        laid_out,
        mode=normalized_mode,
        current_side=str(current_side or "").upper(),
    )
    rendered = [row for row in laid_out if not row.get("precision_rejected") and row.get("geometry_visible") is not False]
    label_collisions = _label_collision_count(rendered)
    rendered_outside = 0
    for row in rendered:
        if row.get("type") in MARKET_OVERLAY_TYPES and _intersection(normalize_bounds(row.get("bounds")) or [], plot_chart) is None:
            rendered_outside += 1
    display_state_counts: dict[str, int] = {}
    for row in laid_out:
        state = str(row.get("display_state") or "COMPACT")
        display_state_counts[state] = display_state_counts.get(state, 0) + 1
    precision_report = {
        "unanchored_boxes": 0,
        "oversized_boxes": 0,
        "duplicate_boxes": duplicate_count,
        "duplicate_now_hidden": duplicate_now_hidden,
        "label_collisions": label_collisions,
        "outside_plot_area": rendered_outside,
        "stale_frame_id": stale_frame,
        "missing_transform": missing_transform,
        "refined_oversized_inputs": oversized,
        "outside_rejected": outside,
        "unanchored_inputs_fixed": unanchored,
        "anchor_snap_refined": anchor_snap_refined,
        "floating_unanchored_rejected": floating_rejected,
        "chart_visible_geometry": len([row for row in rendered if row.get("geometry_visible") is not False]),
        "visible_label_count": len([row for row in rendered if row.get("label_hidden") is not True and row.get("label_visible") is not False]),
        "inspector_label_count": display_state_counts.get("INSPECTOR_LABEL", 0),
        "inspector_only_label_count": display_state_counts.get("INSPECTOR_LABEL", 0) + display_state_counts.get("INSPECTOR_ONLY_LABEL", 0),
        "ghosted_count": display_state_counts.get("GHOSTED", 0),
        "compact_count": display_state_counts.get("COMPACT", 0),
        "full_count": display_state_counts.get("FULL", 0),
        **nesting_report,
    }
    audit = OverlayPrecisionAuditV3(
        frame_id=scene_frame,
        overlay_count=len(laid_out),
        rendered_count=len(rendered),
        rejected_count=len(
            [
                row
                for row in laid_out
                if row.get("precision_rejected")
            ]
        ),
        precision_report=precision_report,
    )
    return laid_out, audit.as_dict()


__all__ = [
    "OVERLAY_PRECISION_AUDIT_SCHEMA_VERSION",
    "OverlayPrecisionAuditV3",
    "resolve_precision_overlays_v3",
]
