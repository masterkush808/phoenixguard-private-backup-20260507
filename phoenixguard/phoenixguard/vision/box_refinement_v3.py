from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

from phoenixguard.runtime.realtime_performance_v3 import OVERLAY_RENDER_BUDGETS
from phoenixguard.vision.v3_overlay_contract import (
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
ACTIONABLE_TYPES = {"SNIPER_ENTRY_BOX", "RETEST_BOX", "CONTINUATION_BOX", "TARGET_ZONE_BOX", "INVALIDATION_BOX"}
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
CURRENT_CANDLE_LIVE_MODES = {"CLEAN_LIVE", "CANDLES", "LOCAL", "ACTIVE_CONTEXT", "DIAGNOSTICS", "DEBUG", "INSPECTOR"}
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


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


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


def _bounds_for_overlay(row: Mapping[str, Any], scene: Mapping[str, Any]) -> tuple[list[float] | None, list[float] | None, str]:
    raw = normalize_bounds(row.get("bounds") or row.get("bbox"))
    if raw is None:
        return None, None, "invalid_bounds"
    coordinate_mode = str(row.get("coordinate_mode") or "").upper()
    chart_bounds = normalize_bounds(scene.get("chart_region_chart_bounds") or [0, 0, 1, 1]) or [0.0, 0.0, 1.0, 1.0]
    plot_chart = normalize_bounds(scene.get("plot_area_chart_bounds") or chart_bounds) or chart_bounds
    plot_full = normalize_bounds(scene.get("plot_area_bounds") or plot_chart) or plot_chart
    right_panel = normalize_bounds(scene.get("right_order_panel_bounds") or plot_full) or plot_full
    if max(abs(value) for value in raw) <= 1.0001:
        if coordinate_mode == "PLOT_AREA_NORMALIZED":
            pw = max(1.0, plot_chart[2] - plot_chart[0])
            ph = max(1.0, plot_chart[3] - plot_chart[1])
            raw = [plot_chart[0] + raw[0] * pw, plot_chart[1] + raw[1] * ph, plot_chart[0] + raw[2] * pw, plot_chart[1] + raw[3] * ph]
        else:
            cw = max(1.0, chart_bounds[2] - chart_bounds[0])
            ch = max(1.0, chart_bounds[3] - chart_bounds[1])
            raw = [chart_bounds[0] + raw[0] * cw, chart_bounds[1] + raw[1] * ch, chart_bounds[0] + raw[2] * cw, chart_bounds[1] + raw[3] * ch]
    if row.get("type") == "BROKER_CONTROL":
        return raw, right_panel, "broker_controls"
    if coordinate_mode in {"FULL_BROKER_SURFACE", "WINDOW_SPACE"}:
        return raw, plot_full, "full_broker_surface"
    return raw, plot_chart, "chart_image_space"


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
    elif overlay_type in {"SNIPER_ENTRY_BOX", "RETEST_BOX", "TARGET_ZONE_BOX"}:
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
        if overlay_type == "INVALIDATION_BOX":
            cap = max(3.0, min(cap, 6.0))
        box[1] = max(clip[1], center - cap * 0.5)
        box[3] = min(clip[3], box[1] + cap)
    box = _clamp_box(box, clip)
    if box is None:
        return None, ["outside_plot_area"]
    if _box_width(box) < 3.0 or _box_height(box) < 3.0:
        return None, ["too_small_after_refinement"]
    return [round(float(value), 3) for value in box], flags


def _priority(row: Mapping[str, Any]) -> tuple[int, float, float]:
    return (
        overlay_type_priority(row.get("type")),
        _clip01(row.get("truth_score", row.get("confidence", 0.0))),
        _float(row.get("z_index"), 0.0),
    )


def _mark_rejected(row: Mapping[str, Any], reason: str) -> dict[str, Any]:
    output = dict(row)
    output["visible_default"] = False
    output["visible_modes"] = ["DEBUG", "INSPECTOR"]
    output["precision_rejected"] = True
    output["precision_rejection_reason"] = reason
    output["lifecycle_state"] = "DEBUG"
    output["layer"] = "diagnostics"
    output["display_label"] = short_label_for_overlay(output.get("type"), output.get("side"), output.get("label"))
    output["short_label"] = output["display_label"]
    return output


def _without_modes(row: Mapping[str, Any], blocked_modes: set[str]) -> list[str]:
    modes = [str(item).upper() for item in _sequence(row.get("visible_modes"))]
    return [mode for mode in modes if mode not in blocked_modes]


def _only_modes(row: Mapping[str, Any], allowed_modes: set[str], default_modes: Sequence[str]) -> list[str]:
    modes: list[str] = []
    for item in _sequence(row.get("visible_modes")):
        normalized = normalize_view_mode(item)
        if normalized in allowed_modes and normalized not in modes:
            modes.append(normalized)
    return modes or list(default_modes)


def _historical_current_marker(row: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("layer", "role", "source_agent", "reason", "label", "raw_display_label", "lifecycle_state")
    ).lower()
    return any(token in haystack for token in ("history", "historical", "replay", "memory"))


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


def _apply_current_candle_policy(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    output = [dict(row) for row in rows]
    current_rows = [
        row
        for row in sorted(output, key=_priority, reverse=True)
        if str(row.get("type") or "") == "CURRENT_CANDLE" and not row.get("precision_rejected")
    ]
    historical_rows = [row for row in current_rows if _historical_current_marker(row)]
    live_rows = [row for row in current_rows if not _historical_current_marker(row)]
    duplicates_hidden = 0
    for row in historical_rows:
        duplicates_hidden += 1
        _map_current_marker_to_history(row)
    for index, row in enumerate(live_rows):
        if index == 0:
            row["visible_modes"] = _only_modes(
                row,
                CURRENT_CANDLE_LIVE_MODES,
                ["CLEAN_LIVE", "CANDLES", "LOCAL", "ACTIVE_CONTEXT", "INSPECTOR"],
            )
            row["display_label"] = "NOW"
            row["short_label"] = "NOW"
            continue
        duplicates_hidden += 1
        row["visible_default"] = False
        row["visible_modes"] = ["DIAGNOSTICS", "DEBUG", "INSPECTOR"]
        row["label_hidden"] = True
        row["label_anchor"] = "hidden"
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
            for existing in kept:
                existing_type = str(existing.get("type") or "")
                if existing_type != row_type and not (row_type in ZONE_TYPES and existing_type in ZONE_TYPES):
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

    by_parent: dict[str, list[list[float]]] = {}
    for row in output:
        parent_id = _text(row.get("parent_overlay_id"))
        bounds = normalize_bounds(row.get("bounds"))
        if parent_id and bounds is not None:
            by_parent.setdefault(parent_id, []).append(bounds)
    for siblings in by_parent.values():
        for index, first in enumerate(siblings):
            for second in siblings[index + 1 :]:
                if rectangles_overlap(first, second, padding=1.0) and _iou(first, second) > 0.72:
                    collisions += 1
    return output, {
        "nested_overlays": nested,
        "nested_children_tightened": tightened,
        "nesting_collisions": collisions,
    }


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
            row["visible_default"] = False
            row["visible_modes"] = [mode for mode in _sequence(row.get("visible_modes")) if str(mode).upper() not in {"CLEAN_LIVE", "ACTIVE_CONTEXT"}]
            row.setdefault("precision_flags", []).append("counter_side_hidden_from_live")
        if overlay_type in ZONE_TYPES:
            supply_demand_visible += 1
            if supply_demand_visible > 3:
                row["visible_default"] = False
                row["visible_modes"] = [mode for mode in _sequence(row.get("visible_modes")) if str(mode).upper() != "CLEAN_LIVE"]
                row.setdefault("precision_flags", []).append("clean_live_zone_budget_hidden")
        if overlay_type == "CURRENT_CANDLE":
            current_candle_visible += 1
            if current_candle_visible > 1:
                row["visible_default"] = False
                row["visible_modes"] = [mode for mode in _sequence(row.get("visible_modes")) if str(mode).upper() != "CLEAN_LIVE"]
                row.setdefault("precision_flags", []).append("clean_live_current_candle_budget_hidden")
        if overlay_type in ACTIONABLE_TYPES:
            actionable_visible += 1
            if actionable_visible > 6:
                row["visible_default"] = False
                row["visible_modes"] = [mode for mode in _sequence(row.get("visible_modes")) if str(mode).upper() != "CLEAN_LIVE"]
                row.setdefault("precision_flags", []).append("clean_live_actionable_budget_hidden")
        if overlay_type in SEQUENCE_TYPES:
            row["visible_default"] = False
            row["visible_modes"] = [mode for mode in _sequence(row.get("visible_modes")) if str(mode).upper() != "CLEAN_LIVE"]
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
        modes = [str(item).upper() for item in _sequence(row.get("visible_modes")) if str(item).upper() != normalized_mode]
        row["visible_modes"] = modes or ["DEBUG", "INSPECTOR"]
        if normalized_mode == "CLEAN_LIVE":
            row["visible_default"] = False
        row.setdefault("precision_flags", []).append(f"{normalized_mode.lower()}_render_budget_hidden")
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
    scene_frame = int(_float(scene.get("frame_id"), 0.0))
    if scene_frame <= 0 and frame_id is not None:
        scene_frame = int(_float(frame_id, 0.0))
    normalized: list[dict[str, Any]] = []
    missing_transform = 0
    stale_frame = 0
    unanchored = 0
    oversized = 0
    outside = 0
    for index, raw in enumerate(overlays):
        try:
            source_frame = raw.get("frame_id", raw.get("frame_index"))
            row = normalize_v3_overlay_object(
                raw,
                strict=False,
                fallback_index=index,
                frame_id=frame_id if source_frame in (None, "") else None,
            )
        except Exception:
            continue
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
        row["z_index"] = int(_float(row.get("z_index"), OVERLAY_TYPE_PRIORITY.get(str(row.get("type") or ""), 0)))
        row.setdefault("precision_flags", [])
        source_agent = str(row.get("source_agent") or "").lower()
        if normalized_mode == "CLEAN_LIVE" and ("legacy_v2" in source_agent or str(row.get("type") or "") == "DEBUG_RAW_DETECTION"):
            normalized.append(_mark_rejected(row, "legacy_or_raw_hidden_from_clean_live"))
            continue
        if not row.get("chart_transform_id"):
            missing_transform += 1
        if scene_frame and int(_float(row.get("frame_id"), scene_frame)) != scene_frame:
            stale_frame += 1
        if not row.get("track_id") or not row.get("object_id") or str(row.get("anchor_type") or "").upper() in {"", "NONE", "UNKNOWN"}:
            unanchored += 1
            row["anchor_type"] = "BOX"
            row.setdefault("precision_flags", []).append("anchor_defaulted")
        if row.get("type") in MARKET_OVERLAY_TYPES:
            raw_bounds, clip_bounds, _space = _bounds_for_overlay(row, scene)
            if raw_bounds is None or clip_bounds is None:
                normalized.append(_mark_rejected(row, "invalid_bounds"))
                continue
            refined_bounds, flags = _tighten_box(row, raw_bounds, clip_bounds)
            row["raw_bounds"] = [round(float(value), 3) for value in raw_bounds]
            row["precision_flags"] = list(row.get("precision_flags") or []) + flags
            if refined_bounds is None:
                outside += 1
                normalized.append(_mark_rejected(row, "outside_plot_area"))
                continue
            if "height_refined" in flags or "width_refined" in flags:
                oversized += 1
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
    current_policy, duplicate_now_hidden = _apply_current_candle_policy(normalized)
    suppressed, duplicate_count = _suppress_duplicates(current_policy)
    nested, nesting_report = _apply_overlay_nesting(suppressed)
    budgeted = (
        _apply_clean_live_budget(nested, str(current_side or "").upper())
        if normalized_mode == "CLEAN_LIVE"
        else [dict(row) for row in nested]
    )
    budgeted = _apply_render_budget(budgeted, normalized_mode)
    laid_out = layout_overlay_labels(budgeted, chart_bounds=plot_chart)
    for row in laid_out:
        if row.get("type") == "CURRENT_CANDLE" and normalized_mode == "CLEAN_LIVE":
            row["label_hidden"] = True
            row["label_anchor"] = "hidden"
    rendered = [row for row in laid_out if not row.get("precision_rejected") and (row.get("visible_default") is not False or normalized_mode != "CLEAN_LIVE")]
    label_collisions = _label_collision_count(rendered)
    rendered_outside = 0
    for row in rendered:
        if row.get("type") in MARKET_OVERLAY_TYPES and _intersection(normalize_bounds(row.get("bounds")) or [], plot_chart) is None:
            rendered_outside += 1
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
                if row.get("precision_rejected") or (normalized_mode == "CLEAN_LIVE" and row.get("visible_default") is False)
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
