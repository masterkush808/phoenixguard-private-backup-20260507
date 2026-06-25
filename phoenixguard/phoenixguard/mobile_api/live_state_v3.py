from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence, cast

from PIL import Image

from phoenixguard.mobile_api.realtime_sync_v3 import build_visual_health_v3
from phoenixguard.runtime.realtime_performance_v3 import (
    build_frame_timing_trace_v3,
    build_performance_trace_v3,
    model_warm_states_from_health,
)
from phoenixguard.tracking.market_object_tracker_v3 import (
    MarketObjectRegistryV3,
    build_market_object_registry_v3,
)
from phoenixguard.vision.broker_scene_graph_v3 import build_broker_scene_graph_v3
from phoenixguard.vision.box_refinement_v3 import OVERLAY_PRECISION_AUDIT_SCHEMA_VERSION, resolve_precision_overlays_v3
from phoenixguard.vision.overlay_layer_manager_v3 import OverlayLayerManagerV3
from phoenixguard.vision.v3_overlay_contract import (
    REQUIRED_FIELDS,
    VIEW_MODES,
    approved_overlay_display_labels,
    is_approved_overlay_display_label,
    normalize_v3_overlay_object,
    normalize_view_mode,
    overlay_is_visible,
    prediction_overlay_config,
    view_mode_profile,
)
from phoenixguard.vlm.context_skeleton_v3 import build_vlm_context_skeleton_v3


LIVE_STATE_SCHEMA_VERSION = "PG_LIVE_STATE_V3"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return cast(Sequence[Any], value)
    return ()


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        row = _mapping(value)
        if row:
            return row
    return {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    return [dict(cast(Mapping[str, Any], item)) for item in _sequence(value) if isinstance(item, Mapping)]


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


def _int(value: Any, default: int = 0) -> int:
    return int(_float(value, float(default)))


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on", "ok", "ready", "valid", "pass", "passed", "locked"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off", "invalid", "fail", "failed", "missing", "mismatch", "wrong_surface"}:
        return False
    return default


def _artifact_frame_id_from_path(path: Path | str | None) -> int:
    if not path:
        return 0
    try:
        prefix = Path(path).stem.split("_", 1)[0]
    except Exception:
        return 0
    try:
        frame_id = int(prefix)
    except (TypeError, ValueError):
        return 0
    return frame_id if frame_id > 0 else 0


def _image_size(path: Path | None) -> tuple[int, int]:
    if path is None or not path.exists():
        return (0, 0)
    try:
        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception:
        return (0, 0)


def _artifact_from_path(kind: str, path: Path | str | None, *, session_id: str = "") -> dict[str, Any]:
    resolved = Path(path) if path else None
    exists = bool(resolved and resolved.exists())
    width, height = _image_size(resolved)
    mtime = 0.0
    size = 0
    if exists and resolved is not None:
        try:
            stat = resolved.stat()
            mtime = float(stat.st_mtime)
            size = int(stat.st_size)
        except OSError:
            exists = False
    version = f"{int(mtime * 1000.0)}-{int(size)}" if exists else "missing"
    return {
        "kind": kind,
        "path": str(resolved) if resolved else "",
        "exists": exists,
        "width": width,
        "height": height,
        "frame_id": _artifact_frame_id_from_path(resolved),
        "mtime": mtime,
        "size": size,
        "url": f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{kind}?v={version}" if session_id else "",
    }


def _artifact_refs(
    session: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Path | str] | None = None,
    artifact_probe: Callable[[str], Path | str] | None = None,
) -> dict[str, dict[str, Any]]:
    session_id = _text(session.get("session_id"))
    keys = {
        "window": "last_window_path",
        "chart": "last_chart_path",
        "overlay": "last_overlay_path",
        "full-overlay": "last_full_overlay_path",
        "projection": "last_projection_path",
        "memory-reference": "last_memory_reference_path",
    }
    refs: dict[str, dict[str, Any]] = {}
    for kind, key in keys.items():
        value: Path | str | None = None
        if artifacts and kind in artifacts:
            value = artifacts[kind]
        elif artifact_probe is not None:
            try:
                value = artifact_probe(kind)
            except Exception:
                value = None
        if value is None and kind == "window" and _text(session.get("last_display_window_path")):
            value = _text(session.get("last_display_window_path"))
        if value is None:
            value = _text(session.get(key))
        refs[kind] = _artifact_from_path(kind, value, session_id=session_id)
    return refs


def _bounds_payload(values: Sequence[Any] | None) -> dict[str, Any]:
    if not values or len(values) < 4:
        return {"exists": False, "x": 0, "y": 0, "width": 0, "height": 0, "bbox": []}
    x0 = _float(values[0])
    y0 = _float(values[1])
    x1 = _float(values[2])
    y1 = _float(values[3])
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    return {
        "exists": right > left and bottom > top,
        "x": left,
        "y": top,
        "width": max(0.0, right - left),
        "height": max(0.0, bottom - top),
        "bbox": [left, top, right, bottom],
    }


def _focus_chart_space_bounds(
    session: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> list[float]:
    tracking = _mapping(session.get("tracking_summary"))
    focus = _mapping(tracking.get("focus_region")) or _mapping(session.get("manual_focus_region"))
    focus_box = _bounds_list(focus.get("pixel_bbox"))
    chart_width = _float(_mapping(artifacts.get("chart")).get("width"), 0.0)
    chart_height = _float(_mapping(artifacts.get("chart")).get("height"), 0.0)
    if chart_width <= 0.0 and focus_box:
        chart_width = max(1.0, focus_box[2] - focus_box[0])
    if chart_height <= 0.0 and focus_box:
        chart_height = max(1.0, focus_box[3] - focus_box[1])
    if chart_width <= 1.0 or chart_height <= 1.0:
        return []
    return [0.0, 0.0, float(chart_width), float(chart_height)]


def _chart_region_is_too_thin_for_focus(
    pixel_bbox: object,
    session: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> bool:
    bbox = _bounds_list(pixel_bbox)
    focus_bounds = _focus_chart_space_bounds(session, artifacts)
    if not bbox or not focus_bounds:
        return False
    focus_width = max(1.0, focus_bounds[2] - focus_bounds[0])
    focus_height = max(1.0, focus_bounds[3] - focus_bounds[1])
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    top_ratio = bbox[1] / focus_height if focus_height > 0.0 else 0.0
    return bool(width < focus_width * 0.70 or height < focus_height * 0.42 or top_ratio > 0.30)


def _plot_area(session: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    tracking = _mapping(session.get("tracking_summary"))
    chart_region = _mapping(tracking.get("chart_region") or tracking.get("display_region"))
    pixel_bbox: object = chart_region.get("pixel_bbox") or chart_region.get("bbox")
    if not isinstance(pixel_bbox, Sequence) or isinstance(pixel_bbox, (str, bytes, bytearray)):
        width = _float(artifacts.get("chart", {}).get("width"), 0.0)
        height = _float(artifacts.get("chart", {}).get("height"), 0.0)
        pixel_bbox = [0.0, 0.0, width, height] if width and height else []
    elif _chart_region_is_too_thin_for_focus(cast(object, pixel_bbox), session, artifacts):
        focus_bounds = _focus_chart_space_bounds(session, artifacts)
        if focus_bounds:
            pixel_bbox = focus_bounds
    payload = _bounds_payload(cast(Sequence[Any], pixel_bbox) if pixel_bbox else None)
    bounds = dict(payload)
    payload.update(
        {
            "bounds": bounds,
            "source": _text(chart_region.get("source"), "tracker_chart_region"),
            "confidence": _float(chart_region.get("confidence"), 0.0),
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "manual_focus_region": _mapping(session.get("manual_focus_region")),
        }
    )
    return payload


def _chart_transform(session: Mapping[str, Any], plot: Mapping[str, Any]) -> dict[str, Any]:
    tracking = _mapping(session.get("tracking_summary"))
    raw = _mapping(tracking.get("chart_transform"))
    frame_id = _int(session.get("frame_index"))
    transform_id = _text(raw.get("chart_transform_id") or raw.get("id"), f"ct_{_text(session.get('session_id'), 'session')}_{frame_id}")
    return {
        "chart_transform_id": transform_id,
        "frame_id": frame_id,
        "plot_area": dict(plot),
        "coordinate_space": "FULL_BROKER_SURFACE_WITH_PLOT_AREA",
    }


def _study_packet_summary(packet: Mapping[str, Any] | None, *, now_epoch: float) -> dict[str, Any]:
    payload = _mapping(packet)
    valid_until = _float(payload.get("valid_until_epoch") or payload.get("valid_until_epoch_sec"), 0.0)
    created = _float(payload.get("created_epoch") or payload.get("created_epoch_sec") or payload.get("published_epoch"), 0.0)
    age_ms = max(0.0, (float(now_epoch) - created) * 1000.0) if created > 0.0 else 0.0
    return {
        "exists": bool(payload),
        "packet_id": _text(payload.get("packet_id")),
        "schema_version": _text(payload.get("schema_version")),
        "created_epoch": created,
        "valid_until_epoch": valid_until,
        "age_ms": round(age_ms, 3),
        "fresh": bool(payload) and (valid_until <= 0.0 or valid_until >= now_epoch),
    }


def _execution_packet_valid_until(packet: Mapping[str, Any]) -> float:
    direct = _float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"), 0.0)
    if direct > 0.0:
        return direct
    created = _float(packet.get("created_epoch_sec") or packet.get("created_epoch"), 0.0)
    ttl = _float(packet.get("ttl_sec") or packet.get("time_to_live_sec") or packet.get("freshness_window_sec"), 0.0)
    return created + max(0.1, ttl) if created > 0.0 and ttl > 0.0 else 0.0


def _current_execution_packet(packet: Any, *, now_epoch: float) -> dict[str, Any]:
    payload = _mapping(packet)
    if not payload:
        return {}
    packet_type = _text(payload.get("packet_type")).upper()
    schema_version = _text(payload.get("schema_version")).upper()
    if packet_type != "PG_EXECUTION_PACKET_V3" or schema_version != "PG_EXECUTION_PACKET_V3":
        return {}
    valid_until = _execution_packet_valid_until(payload)
    if valid_until <= float(now_epoch):
        return {}
    execution = _mapping(payload.get("execution"))
    council = _mapping(payload.get("model_council"))
    execution_side = _text(execution.get("side")).upper()
    final_side = _text(council.get("final_side")).upper()
    if execution_side not in {"BUY", "SELL"} or final_side not in {"BUY", "SELL"}:
        return {}
    if execution_side != final_side:
        return {}
    if execution.get("enabled") is not True:
        return {}
    if _text(execution.get("state")).upper() != "EXECUTABLE":
        return {}
    if _text(council.get("final_state")).upper() != "EXECUTABLE":
        return {}
    if _int(execution.get("expiry_seconds"), 0) <= 0:
        return {}
    return payload


def _model_council_summary(session: Mapping[str, Any], study_packet: Mapping[str, Any] | None) -> dict[str, Any]:
    signal = _mapping(session.get("latest_signal"))
    result = _mapping(session.get("model_council_result"))
    packet = _mapping(study_packet)
    council = _mapping(result.get("model_council") or packet.get("model_council"))
    execution = _mapping(result.get("execution") or packet.get("execution"))
    promotion = _mapping(result.get("promotion_trace") or packet.get("promotion_trace"))
    return {
        "state": _text(execution.get("state") or council.get("final_state") or signal.get("entry_state"), "WATCHING").upper(),
        "side": _text(execution.get("side") or council.get("final_side") or signal.get("execution_action") or signal.get("action"), "HOLD").upper(),
        "summary": _text(signal.get("summary"), "Awaiting live chart study."),
        "next_required": _text(promotion.get("next_required") or promotion.get("denied_at") or signal.get("execution_block_reason")),
        "result_present": bool(result),
    }


def _instrument(session: Mapping[str, Any]) -> dict[str, Any]:
    tracking = _mapping(session.get("tracking_summary"))
    signal = _mapping(session.get("latest_signal"))
    market = _text(signal.get("market") or tracking.get("detected_market") or session.get("market"))
    timeframe = _text(signal.get("focus_timeframe") or tracking.get("detected_timeframe"))
    return {
        "market": market,
        "timeframe": timeframe,
        "market_confidence": _float(signal.get("market_confidence", tracking.get("market_confidence", 0.0)), 0.0),
        "timeframe_confidence": _float(signal.get("timeframe_confidence", tracking.get("timeframe_confidence", 0.0)), 0.0),
        "identity_locked": bool(market and timeframe),
        "instrument_context": _mapping(tracking.get("instrument_context") or signal.get("instrument_context")),
        "symbol_context": _mapping(tracking.get("symbol_context") or signal.get("symbol_context")),
    }


def _overlay_from_active_object(
    row: Mapping[str, Any],
    *,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    scene_graph: Mapping[str, Any],
    index: int,
) -> dict[str, Any] | None:
    overlay = _mapping(row.get("overlay"))
    if not overlay:
        overlay = dict(row)
    for source_key, target_key in (("pixel_bbox", "bounds"), ("bbox", "bounds"), ("bounds", "bounds")):
        if source_key in overlay and target_key not in overlay:
            overlay[target_key] = overlay[source_key]
    overlay.setdefault("overlay_id", row.get("overlay_id") or overlay.get("id") or f"registry_{index}")
    overlay.setdefault("object_id", row.get("object_id") or overlay.get("overlay_id"))
    overlay.setdefault("track_id", row.get("track_id") or row.get("object_id") or overlay.get("object_id"))
    overlay.setdefault("truth_score", row.get("truth_score", overlay.get("confidence", 0.0)))
    overlay.setdefault("lifecycle_state", row.get("lifecycle_state", "ACTIVE"))
    overlay["frame_id"] = frame_id
    overlay["sequence_id"] = sequence_id
    overlay["chart_transform_id"] = chart_transform_id
    overlay.setdefault("source_agent", row.get("source_agent", "market_registry"))
    overlay.setdefault("reason", row.get("reason", "registry active object"))
    overlay.setdefault("source_rule", row.get("source_rule", "active_object_registry"))
    overlay.setdefault("structural_anchor", row.get("structural_anchor", True))
    visible_modes = [str(item).upper() for item in _sequence(overlay.get("visible_modes"))]
    if visible_modes and "REPLAY" in visible_modes and "FULL_HISTORY_READ" not in visible_modes:
        overlay["visible_modes"] = [*visible_modes, "FULL_HISTORY_READ"]
    _rescale_registry_overlay_to_current_chart(overlay, row, scene_graph=scene_graph)
    try:
        return normalize_v3_overlay_object(
            overlay,
            strict=False,
            frame_id=frame_id,
            sequence_id=sequence_id,
            chart_transform_id=chart_transform_id,
            fallback_index=index,
        )
    except Exception:
        return None


def _scale_number(value: Any, *, source_min: float, source_size: float, target_min: float, target_size: float) -> float:
    return target_min + ((_float(value, source_min) - source_min) * target_size / max(1.0, source_size))


def _scale_bounds_between_chart_spaces(bounds: Sequence[Any], source: Sequence[float], target: Sequence[float]) -> list[float]:
    source_width = max(1.0, float(source[2] - source[0]))
    source_height = max(1.0, float(source[3] - source[1]))
    target_width = max(1.0, float(target[2] - target[0]))
    target_height = max(1.0, float(target[3] - target[1]))
    return [
        _scale_number(bounds[0], source_min=source[0], source_size=source_width, target_min=target[0], target_size=target_width),
        _scale_number(bounds[1], source_min=source[1], source_size=source_height, target_min=target[1], target_size=target_height),
        _scale_number(bounds[2], source_min=source[0], source_size=source_width, target_min=target[0], target_size=target_width),
        _scale_number(bounds[3], source_min=source[1], source_size=source_height, target_min=target[1], target_size=target_height),
    ]


def _scale_point_between_chart_spaces(point: Sequence[Any], source: Sequence[float], target: Sequence[float]) -> list[float]:
    source_width = max(1.0, float(source[2] - source[0]))
    source_height = max(1.0, float(source[3] - source[1]))
    target_width = max(1.0, float(target[2] - target[0]))
    target_height = max(1.0, float(target[3] - target[1]))
    return [
        _scale_number(point[0], source_min=source[0], source_size=source_width, target_min=target[0], target_size=target_width),
        _scale_number(point[1], source_min=source[1], source_size=source_height, target_min=target[1], target_size=target_height),
    ]


def _scale_points_between_chart_spaces(value: Any, source: Sequence[float], target: Sequence[float]) -> list[list[float]]:
    points: list[list[float]] = []
    for item in _sequence(value):
        point = _sequence(item)
        if len(point) >= 2:
            points.append(_scale_point_between_chart_spaces(point, source, target))
    return points


def _rescale_registry_overlay_to_current_chart(
    overlay: dict[str, Any],
    row: Mapping[str, Any],
    *,
    scene_graph: Mapping[str, Any],
) -> None:
    source_transform = _mapping(row.get("chart_transform")) or _mapping(overlay.get("chart_transform"))
    source_bounds = (
        _bounds_list(source_transform.get("chart_image_bounds"))
        or _bounds_list(source_transform.get("window_bounds"))
        or _bounds_list(source_transform.get("screen_bounds"))
    )
    target_bounds = _bounds_list(scene_graph.get("chart_region_chart_bounds"))
    if not source_bounds or not target_bounds:
        return
    source_width = max(1.0, source_bounds[2] - source_bounds[0])
    source_height = max(1.0, source_bounds[3] - source_bounds[1])
    target_width = max(1.0, target_bounds[2] - target_bounds[0])
    target_height = max(1.0, target_bounds[3] - target_bounds[1])
    if abs(source_width - target_width) < 1.0 and abs(source_height - target_height) < 1.0:
        return
    for key in ("bbox", "bounds", "pixel_bbox"):
        raw_bounds = _sequence(overlay.get(key))
        if len(raw_bounds) >= 4:
            overlay[key] = _scale_bounds_between_chart_spaces(raw_bounds[:4], source_bounds, target_bounds)
    for key in ("points", "line_points", "touch_points", "path"):
        scaled_points = _scale_points_between_chart_spaces(overlay.get(key), source_bounds, target_bounds)
        if scaled_points:
            overlay[key] = scaled_points
    overlay["coordinate_mode"] = "CHART_IMAGE_SPACE"
    overlay["registry_chart_space_scaled_v3"] = True


def _combine_overlays(
    registry: MarketObjectRegistryV3,
    *,
    active_objects: Sequence[Mapping[str, Any]] | None,
    chart_transform_id: str,
    scene_graph: Mapping[str, Any],
) -> list[dict[str, Any]]:
    overlays = [dict(overlay) for overlay in registry.overlays]
    seen = {str(overlay.get("overlay_id")) for overlay in overlays}
    registry_has_historical_progression = any(
        _text(overlay.get("type")).upper() == "PROGRESSION_PATH"
        and _text(overlay.get("source_path")).startswith("tracking_summary.historical_structure")
        for overlay in overlays
    )
    for index, row in enumerate(active_objects or []):
        if registry_has_historical_progression and _source_less_progression_rectangle(row):
            continue
        overlay = _overlay_from_active_object(
            row,
            frame_id=registry.frame_id,
            sequence_id=registry.sequence_context.sequence_id,
            chart_transform_id=chart_transform_id,
            scene_graph=scene_graph,
            index=index,
        )
        if overlay is not None and str(overlay.get("overlay_id")) not in seen:
            overlays.append(overlay)
            seen.add(str(overlay.get("overlay_id")))
    return overlays


def _source_less_progression_rectangle(row: Mapping[str, Any]) -> bool:
    overlay = _mapping(row.get("overlay")) or dict(row)
    overlay_type = _text(overlay.get("type") or row.get("type")).upper()
    layer = _text(overlay.get("layer") or row.get("layer")).lower()
    source_path = _text(overlay.get("source_path") or row.get("source_path"))
    has_path_points = bool(
        _sequence(overlay.get("line_points"))
        or _sequence(overlay.get("points"))
        or _sequence(overlay.get("path"))
        or _sequence(overlay.get("anchors"))
    )
    is_progression = overlay_type in {"PROGRESSION_PATH", "HISTORICAL_REPLAY", "HISTORICAL_PROGRESSION", "PROGRESSION"}
    return bool(is_progression and layer in {"", "historical_replay", "replay"} and not source_path and not has_path_points)


def _dashboard_overlay_object(overlay: Mapping[str, Any], *, compact: bool = False) -> dict[str, Any]:
    row = dict(overlay)
    bbox = _sequence(row.get("bbox"))
    if len(bbox) < 4:
        bounds_value = row.get("bounds")
        if _sequence(bounds_value):
            bbox = list(_sequence(bounds_value))[:4]
        elif isinstance(bounds_value, Mapping):
            maybe_bbox = _mapping(bounds_value).get("bbox")
            if _sequence(maybe_bbox):
                bbox = list(_sequence(maybe_bbox))[:4]
    if len(bbox) >= 4:
        x0 = _float(bbox[0])
        y0 = _float(bbox[1])
        x1 = _float(bbox[2])
        y1 = _float(bbox[3])
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        box = [left, top, right, bottom]
        row["bbox"] = box
        row["bounds"] = box
        row["bounds_rect"] = {
            "exists": right > left and bottom > top,
            "x": left,
            "y": top,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": max(0.0, right - left),
            "height": max(0.0, bottom - top),
            "bbox": box,
        }
    label_bounds = row.get("label_bounds")
    label_bounds_seq = _sequence(label_bounds)
    if len(label_bounds_seq) >= 4:
        lx0 = _float(label_bounds_seq[0])
        ly0 = _float(label_bounds_seq[1])
        lx1 = _float(label_bounds_seq[2])
        ly1 = _float(label_bounds_seq[3])
        left, right = sorted((lx0, lx1))
        top, bottom = sorted((ly0, ly1))
        label_box = [left, top, right, bottom]
        row["label_bounds"] = {
            "exists": right > left and bottom > top,
            "x": left,
            "y": top,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": max(0.0, right - left),
            "height": max(0.0, bottom - top),
            "bbox": label_box,
        }
    canonical_label = _text(row.get("display_label") or row.get("short_label"))
    if canonical_label:
        original_label = _text(row.get("label"))
        if original_label and original_label != canonical_label:
            row.setdefault("raw_label", original_label)
        row["label"] = canonical_label
        row["short_label"] = canonical_label
    if not compact:
        return row
    keep_keys = (
        "schema_version",
        "overlay_id",
        "id",
        "object_id",
        "track_id",
        "type",
        "overlay_type",
        "kind",
        "side",
        "direction",
        "action",
        "source_agent",
        "source_version",
        "source_path",
        "source_key",
        "broker_source_lock_id",
        "frame_id",
        "sequence_id",
        "chart_transform_id",
        "bbox",
        "bounds",
        "bounds_rect",
        "truth_score",
        "confidence",
        "label",
        "raw_label",
        "raw_display_label",
        "display_label",
        "display_label_status",
        "unmapped_display_label",
        "short_label",
        "layer",
        "role",
        "visible_modes",
        "visible_default",
        "label_hidden",
        "label_anchor",
        "label_bounds",
        "display_state",
        "visual_weight",
        "geometry_visible",
        "label_visible",
        "inspector_visible",
        "label_mode",
        "label_lane",
        "representation_reason",
        "style",
        "group_id",
        "group_type",
        "group_bounds",
        "summary_label",
        "expand_on_hover",
        "expand_on_click",
        "points",
        "line_points",
        "touch_points",
        "touch_count",
        "trendline_role",
        "trendline_scope",
        "trendline_validation",
        "wick_probe_count",
        "line_obstruction_count",
        "body_cross_fraction",
        "close_distance_norm",
        "significant_close",
        "touch_quality",
        "breach_state",
        "validation_reason",
        "skill_gate",
        "zone_family",
        "liquidity_pool_type",
        "liquidity_source",
        "role_flip_state",
        "zone_stack_id",
        "source_rule",
        "knowledge_tags",
        "replay_sequence",
        "replay_action",
        "story",
        "parent_overlay_id",
        "parent_type",
        "nesting_depth",
        "nesting_role",
        "child_overlay_ids",
        "containment_ratio",
        "z_index",
        "coordinate_mode",
        "anchor_type",
        "anchor_candles",
        "lifecycle_state",
        "ttl_ms",
        "reason",
        "precision_rejected",
        "precision_rejection_reason",
    )
    required_keep = set(REQUIRED_FIELDS)
    return {
        key: row[key]
        for key in keep_keys
        if key in row and (key in required_keep or row.get(key) not in (None, "", [], {}))
    }


def _zone_bbox(zone: Mapping[str, Any]) -> list[float]:
    raw = zone.get("bbox") or zone.get("pixel_bbox") or zone.get("bounds") or zone.get("normalized_bbox")
    if isinstance(raw, Mapping):
        raw = _mapping(raw).get("bbox")
    raw_seq = _sequence(raw)
    if len(raw_seq) < 4:
        return []
    x0, y0, x1, y1 = [_float(raw_seq[index]) for index in range(4)]
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right <= left or bottom <= top:
        return []
    return [left, top, right, bottom]


def _bounds_list(value: Any) -> list[float]:
    raw = value
    if isinstance(value, Mapping):
        value_map = _mapping(value)
        raw = value_map.get("bbox") or value_map.get("pixel_bbox") or value_map.get("bounds") or value_map.get("normalized_bbox")
        if isinstance(raw, Mapping):
            raw = _mapping(raw).get("bbox")
    raw_seq = _sequence(raw)
    if len(raw_seq) < 4:
        return []
    x0, y0, x1, y1 = [_float(raw_seq[index]) for index in range(4)]
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right <= left or bottom <= top:
        return []
    return [left, top, right, bottom]


def _broker_control_source(
    session: Mapping[str, Any],
    aliases: Sequence[str],
) -> tuple[list[float], dict[str, Any]]:
    tracking = _mapping(session.get("tracking_summary"))
    surfaces = [
        _mapping(session.get("broker_surface")),
        _mapping(tracking.get("broker_surface")),
    ]
    for surface in surfaces:
        execution_boxes = _mapping(surface.get("execution_boxes"))
        for alias in aliases:
            for source in (_mapping(execution_boxes.get(alias)), _mapping(surface.get(alias))):
                if not source:
                    continue
                if "visible" in source and not _bool(source.get("visible"), True):
                    continue
                if "locked" in source and not _bool(source.get("locked"), True):
                    continue
                bounds = _bounds_list(source)
                if bounds:
                    return bounds, source
    return [], {}


def _broker_control_overlay_objects(
    session: Mapping[str, Any],
    *,
    scene_graph: Mapping[str, Any],
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    broker_source_lock_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    surface_bounds = _bounds_list(scene_graph.get("broker_surface_bounds"))
    if not surface_bounds or surface_bounds[2] - surface_bounds[0] < 100.0 or surface_bounds[3] - surface_bounds[1] < 100.0:
        return []
    definitions: tuple[dict[str, Any], ...] = (
        {
            "source_key": "broker_screen",
            "label": "BROKER SURFACE",
            "role": "broker_screen",
            "scene_key": "broker_surface_bounds",
            "confidence": 0.92,
        },
        {
            "source_key": "right_order_panel",
            "label": "RIGHT ORDER PANEL",
            "role": "right_order_panel",
            "scene_key": "right_order_panel_bounds",
            "aliases": ("order_panel", "right_order_panel"),
            "confidence": 0.92,
        },
        {
            "source_key": "time_button",
            "label": "TIME BUTTON",
            "role": "time_button",
            "aliases": ("time_button", "time_input", "time_box", "time_field", "expiry_time_field"),
            "confidence": 0.86,
        },
        {
            "source_key": "amount_field",
            "label": "AMOUNT FIELD",
            "role": "amount_field",
            "aliases": ("amount_field", "amount_input", "amount_box", "stake_amount"),
            "confidence": 0.84,
        },
        {
            "source_key": "buy_icon",
            "label": "BUY BUTTON",
            "role": "buy_icon",
            "aliases": ("buy_icon", "buy_button"),
            "confidence": 0.90,
        },
        {
            "source_key": "sell_icon",
            "label": "SELL BUTTON",
            "role": "sell_icon",
            "aliases": ("sell_icon", "sell_button"),
            "confidence": 0.90,
        },
    )
    overlays: list[dict[str, Any]] = []
    for index, definition in enumerate(definitions):
        bounds = _bounds_list(scene_graph.get(str(definition.get("scene_key")))) if definition.get("scene_key") else []
        source_row: dict[str, Any] = {}
        if not bounds:
            bounds, source_row = _broker_control_source(
                session,
                tuple(str(item) for item in definition.get("aliases", ())),
            )
        elif definition.get("aliases"):
            source_bounds, source_row = _broker_control_source(
                session,
                tuple(str(item) for item in definition.get("aliases", ())),
            )
            if source_bounds:
                bounds = source_bounds
        if not bounds:
            continue
        confidence = max(0.01, min(1.0, _float(source_row.get("confidence"), _float(definition.get("confidence"), 0.85))))
        source_key = _text(definition.get("source_key"), f"broker_control_{index}")
        label = _text(definition.get("label"), "BROKER CONTROL")
        raw: dict[str, Any] = {
            "overlay_id": f"broker_control_{source_key}_{frame_id}",
            "object_id": f"broker_control_{source_key}",
            "track_id": f"broker_control_{source_key}",
            "type": "BROKER_CONTROL",
            "side": "HOLD",
            "source_agent": "broker_scene_graph_v3",
            "source_key": source_key,
            "broker_source_lock_id": broker_source_lock_id,
            "frame_id": frame_id,
            "sequence_id": sequence_id,
            "chart_transform_id": chart_transform_id,
            "coordinate_mode": "FULL_BROKER_SURFACE",
            "anchor_type": "BROKER_SURFACE",
            "bounds": bounds,
            "truth_score": confidence,
            "confidence": confidence,
            "lifecycle_state": "CONFIRMED",
            "visible_modes": ["BROKER", "CALIBRATION", "INSPECTOR"],
            "visible_default": False,
            "ttl_ms": 30000,
            "created_at_ms": now_ms,
            "reason": f"{label.lower()} locked on broker source",
            "label": label,
            "display_label": label,
            "short_label": label,
            "layer": "broker_controls",
            "role": _text(definition.get("role"), "broker_control"),
            "z_index": 30 + index,
        }
        try:
            overlays.append(
                normalize_v3_overlay_object(
                    raw,
                    strict=False,
                    frame_id=frame_id,
                    sequence_id=sequence_id,
                    chart_transform_id=chart_transform_id,
                    fallback_index=index,
                )
            )
        except Exception:
            continue
    return overlays


def _signal_thesis_overlay_objects(
    thesis: Mapping[str, Any],
    *,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
) -> list[dict[str, Any]]:
    if not bool(thesis.get("active")):
        return []
    side = _text(thesis.get("effective_side") or thesis.get("side")).upper()
    if side not in {"BUY", "SELL"}:
        return []
    opposite = "SELL" if side == "BUY" else "BUY"
    thesis_id = _text(thesis.get("thesis_id"), "active-thesis")
    confidence = max(0.5, min(1.0, _float(thesis.get("confidence"), 0.75)))
    visible_modes = ["CLEAN_LIVE", "COUNCIL", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"]

    def make_overlay(kind: str, overlay_type: str, zone: Mapping[str, Any], *, overlay_side: str, label: str, layer: str, role: str) -> dict[str, Any] | None:
        bbox = _zone_bbox(zone)
        if not bbox:
            return None
        raw: dict[str, Any] = {
            "overlay_id": f"thesis_{thesis_id}_{kind}",
            "object_id": f"thesis_{thesis_id}_{kind}",
            "track_id": f"thesis_{thesis_id}",
            "type": overlay_type,
            "side": overlay_side,
            "bbox": bbox,
            "truth_score": confidence,
            "confidence": confidence,
            "label": label,
            "display_label": label,
            "short_label": label,
            "layer": layer,
            "role": role,
            "visible_modes": visible_modes,
            "visible_default": True,
            "label_hidden": False,
            "label_anchor": "top" if kind == "countertrend_block" else "bottom",
            "z_index": 96 if kind == "countertrend_block" else 88,
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "source_agent": "signal_thesis_tracker",
            "reason": _text(thesis.get("plain_language"), "Active thesis context"),
            "lifecycle_state": "ACTIVE",
            "frame_id": frame_id,
            "sequence_id": sequence_id,
            "chart_transform_id": chart_transform_id,
        }
        try:
            return normalize_v3_overlay_object(
                raw,
                strict=False,
                frame_id=frame_id,
                sequence_id=sequence_id,
                chart_transform_id=chart_transform_id,
                fallback_index=0,
            )
        except Exception:
            return raw

    entry_zone = _mapping(thesis.get("entry_zone"))
    target_zone = _mapping(thesis.get("target_zone"))
    invalidation_zone = _mapping(thesis.get("invalidation_zone"))
    overlays: list[dict[str, Any]] = []
    for candidate in (
        make_overlay("active_entry", "SNIPER_ENTRY_BOX", entry_zone, overlay_side=side, label=f"ACTIVE {side}", layer="trigger_zones", role="active_thesis_entry"),
        make_overlay("active_target", "TARGET_ZONE_BOX", target_zone, overlay_side=side, label=f"{side} TARGET", layer="target_zones", role="active_thesis_target"),
        make_overlay("active_invalidation", "INVALIDATION_BOX", invalidation_zone, overlay_side=side, label=f"{side} INVALID", layer="invalidation", role="active_thesis_invalidation"),
        make_overlay(
            "countertrend_block",
            "OPPOSING_FORCE",
            invalidation_zone or entry_zone,
            overlay_side=opposite,
            label="OPPOSING FORCE",
            layer="supply_demand",
            role="countertrend_block",
        ),
    ):
        if candidate is not None:
            overlays.append(candidate)
    return overlays


def _tracked_candle_rows(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    tracking = _mapping(session.get("tracking_summary"))
    signal = _mapping(session.get("latest_signal"))
    rows = _sequence_of_mappings(tracking.get("tracked_candles"))
    if rows:
        return rows
    return _sequence_of_mappings(signal.get("tracked_candles"))


def _union_box(boxes: Sequence[Sequence[Any]]) -> list[float]:
    normalized = [_bounds_list(box) for box in boxes]
    usable = [box for box in normalized if box]
    if not usable:
        return []
    return [
        min(box[0] for box in usable),
        min(box[1] for box in usable),
        max(box[2] for box in usable),
        max(box[3] for box in usable),
    ]


def _pad_box(bounds: Sequence[Any], pad_x: float = 4.0, pad_y: float = 6.0) -> list[float]:
    box = _bounds_list(bounds)
    if not box:
        return []
    return [box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y]


def _study_anchor_box(session: Mapping[str, Any], *, candle_count: int = 2) -> tuple[list[float], list[int]]:
    candles = _tracked_candle_rows(session)
    if candles:
        count = max(1, min(int(candle_count), len(candles)))
        selected = candles[-count:]
        bounds = _union_box([_bounds_list(row) for row in selected])
        if bounds:
            first_index = max(0, len(candles) - count)
            return _pad_box(bounds), list(range(first_index, len(candles)))
    thesis = _first_mapping(
        session.get("signal_thesis_v3"),
        _mapping(session.get("latest_signal")).get("signal_thesis_v3"),
        _mapping(session.get("tracking_summary")).get("signal_thesis_v3"),
    )
    for key in ("entry_zone", "target_zone", "invalidation_zone"):
        bounds = _zone_bbox(_mapping(thesis.get(key)))
        if bounds:
            return _pad_box(bounds), []
    tracking = _mapping(session.get("tracking_summary"))
    for source in (
        _mapping(tracking.get("current_box")),
        *(_sequence_of_mappings(tracking.get("structure_boxes"))[-1:]),
    ):
        bounds = _bounds_list(source)
        if bounds:
            return _pad_box(bounds), []
    return [], []


def _study_overlay_objects(
    session: Mapping[str, Any],
    two_candle: Mapping[str, Any],
    lstm: Mapping[str, Any],
    *,
    active_mode: str,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    signal = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    fallback_side = _text(signal.get("execution_action") or signal.get("action") or tracking.get("local_direction"), "HOLD").upper()
    overlays: list[dict[str, Any]] = []

    def add_study(
        *,
        overlay_type: str,
        payload: Mapping[str, Any],
        label: str,
        mode: str,
        candle_count: int,
        fallback_confidence: float,
        fallback_reason: str,
    ) -> None:
        if not payload and active_mode != mode:
            return
        bounds, anchor_candles = _study_anchor_box(session, candle_count=candle_count)
        if not bounds:
            return
        confidence = max(0.05, min(1.0, _float(payload.get("confidence") or payload.get("contribution"), fallback_confidence)))
        side = _text(payload.get("side") or payload.get("direction") or payload.get("direction_bias") or fallback_side, "HOLD").upper()
        if side not in {"BUY", "SELL", "HOLD"}:
            side = "HOLD"
        raw: dict[str, Any] = {
            "overlay_id": f"{overlay_type.lower()}_{frame_id}",
            "object_id": f"{overlay_type.lower()}_{frame_id}",
            "track_id": f"{overlay_type.lower()}_study",
            "type": overlay_type,
            "side": side,
            "source_agent": "live_state_v3_study_overlay",
            "source_key": _text(payload.get("schema_version") or payload.get("skill") or mode, mode),
            "frame_id": frame_id,
            "sequence_id": sequence_id,
            "chart_transform_id": chart_transform_id,
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "CANDLES" if anchor_candles else "BOX",
            "anchor_candles": anchor_candles,
            "bounds": bounds,
            "truth_score": confidence,
            "confidence": confidence,
            "lifecycle_state": "ACTIVE",
            "visible_modes": [mode, "COUNCIL", "INSPECTOR"],
            "visible_default": False,
            "ttl_ms": 12000,
            "created_at_ms": now_ms,
            "reason": _text(payload.get("summary") or payload.get("reason"), fallback_reason),
            "label": label,
            "display_label": label,
            "short_label": label,
            "layer": "active_council_decision",
            "role": mode.lower(),
            "label_anchor": "top",
            "label_hidden": False,
            "z_index": 74 if overlay_type == "TWO_CANDLE_STUDY" else 72,
            "structural_anchor": True,
            "source_rule": "study_overlay_anchored_to_visible_candles",
        }
        try:
            overlays.append(
                normalize_v3_overlay_object(
                    raw,
                    strict=False,
                    frame_id=frame_id,
                    sequence_id=sequence_id,
                    chart_transform_id=chart_transform_id,
                    fallback_index=len(overlays),
                )
            )
        except Exception:
            overlays.append(raw)

    add_study(
        overlay_type="TWO_CANDLE_STUDY",
        payload=two_candle,
        label="TWO CANDLE STUDY",
        mode="TWO_CANDLE_STUDY",
        candle_count=2,
        fallback_confidence=0.58,
        fallback_reason="Two-candle study anchored to the latest visible candles.",
    )
    add_study(
        overlay_type="LSTM_STUDY",
        payload=lstm,
        label="LSTM STUDY",
        mode="LSTM_STUDY",
        candle_count=8,
        fallback_confidence=0.50,
        fallback_reason="LSTM sequence contribution anchored to the latest visible candle window.",
    )
    return overlays


def _council_overlay_objects(
    session: Mapping[str, Any],
    study_packet: Mapping[str, Any] | None,
    *,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    bounds, anchor_candles = _study_anchor_box(session, candle_count=1)
    if not bounds:
        return []
    council = _model_council_summary(session, study_packet)
    side = _text(council.get("side"), "HOLD").upper()
    if side not in {"BUY", "SELL", "HOLD"}:
        side = "HOLD"
    confidence = max(0.35, min(1.0, _float(council.get("confidence"), _float(_mapping(session.get("latest_signal")).get("effective_confidence"), 0.62))))
    raw: dict[str, Any] = {
        "overlay_id": f"model_council_marker_{frame_id}",
        "object_id": f"model_council_marker_{frame_id}",
        "track_id": "model_council_marker",
        "type": "MODEL_COUNCIL_MARKER",
        "side": side,
        "source_agent": "live_state_v3_council_overlay",
        "source_key": _text(council.get("state"), "COUNCIL"),
        "frame_id": frame_id,
        "sequence_id": sequence_id,
        "chart_transform_id": chart_transform_id,
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "anchor_type": "CANDLES" if anchor_candles else "BOX",
        "anchor_candles": anchor_candles,
        "bounds": bounds,
        "truth_score": confidence,
        "confidence": confidence,
        "lifecycle_state": "ACTIVE",
        "visible_modes": ["COUNCIL", "ACTIVE_CONTEXT", "INSPECTOR"],
        "visible_default": False,
        "ttl_ms": 12000,
        "created_at_ms": now_ms,
        "reason": _text(council.get("summary"), "Model council marker anchored to the active chart read."),
        "label": "MODEL COUNCIL MARKER",
        "display_label": "MODEL COUNCIL MARKER",
        "short_label": "MODEL COUNCIL MARKER",
        "layer": "active_council_decision",
        "role": "model_council",
        "label_anchor": "right",
        "label_hidden": False,
        "z_index": 76,
        "structural_anchor": True,
        "source_rule": "council_overlay_anchored_to_current_candle",
    }
    try:
        return [
            normalize_v3_overlay_object(
                raw,
                strict=False,
                frame_id=frame_id,
                sequence_id=sequence_id,
                chart_transform_id=chart_transform_id,
                fallback_index=0,
            )
        ]
    except Exception:
        return [raw]


def _overlay_visible_for_mode(overlay: Mapping[str, Any], mode: str, *, now_ms: int | float | None = None) -> bool:
    if bool(overlay.get("precision_rejected", False)):
        return False
    normalized_mode = normalize_view_mode(mode)
    if normalized_mode == "CLEAN_LIVE" and overlay.get("visible_default") is False:
        return False
    created_at_ms = _float(overlay.get("created_at_ms"), 0.0)
    effective_now_ms = now_ms if created_at_ms > 0.0 else None
    return overlay_is_visible(overlay, normalized_mode, now_ms=effective_now_ms)


def _overlay_is_frame_aligned(overlay: Mapping[str, Any], frame_id: int) -> bool:
    if frame_id <= 0:
        return True
    layer = _text(overlay.get("layer")).lower()
    if layer in {"historical_replay", "replay"}:
        return True
    overlay_frame = _int(overlay.get("frame_id") or overlay.get("frame_index"))
    return overlay_frame <= 0 or overlay_frame == frame_id


def _overlay_artifact_alignment(
    *,
    overlay_artifact: Mapping[str, Any],
    overlay_object_frame_id: int,
) -> tuple[bool, str, int]:
    artifact_frame_id = _int(overlay_artifact.get("frame_id"))
    if artifact_frame_id <= 0 or overlay_object_frame_id <= 0:
        return True, "", artifact_frame_id
    if artifact_frame_id == overlay_object_frame_id:
        return True, "", artifact_frame_id
    return (
        False,
        f"overlay artifact frame {artifact_frame_id} does not match overlay object frame {overlay_object_frame_id}",
        artifact_frame_id,
    )


def _surface_signatures_match(session: Mapping[str, Any]) -> bool:
    display_signature = _text(
        session.get("last_display_surface_signature")
        or session.get("last_window_surface_signature")
        or session.get("display_surface_signature")
    )
    overlay_signature = _text(
        session.get("overlay_source_window_signature")
        or session.get("last_window_surface_signature")
        or session.get("last_study_surface_signature")
    )
    return bool(display_signature and overlay_signature and display_signature == overlay_signature)


def _display_only_overlay_authority_locked(
    session: Mapping[str, Any],
    *,
    overlay_object_frame_id: int = 0,
    source_block_reason: str = "",
) -> bool:
    if source_block_reason:
        return False
    display_frame = _int(session.get("frame_index") or session.get("chart_frame_id") or session.get("display_frame_id"))
    has_display_artifact = bool(session.get("last_display_window_path") or session.get("last_window_path") or session.get("last_frame_path"))
    if display_frame > 0 and overlay_object_frame_id > 0 and display_frame == overlay_object_frame_id and has_display_artifact:
        return True
    if not (bool(session.get("display_snapshot_only_v3")) or bool(session.get("display_fast_path_v3"))):
        return False
    if _surface_signatures_match(session):
        return True
    overlay_frame = max(_int(session.get("overlay_frame_id")), _int(session.get("full_overlay_frame_id")))
    has_overlay_artifact = bool(session.get("last_overlay_path") or session.get("last_full_overlay_path"))
    return bool(overlay_frame > 0 and display_frame > 0 and has_overlay_artifact and has_display_artifact)


def _mode_visible_layers(mode: str) -> list[str]:
    profile = view_mode_profile(mode)
    layer_visibility = _mapping(profile.get("layer_visibility"))
    ordered_layers = OverlayLayerManagerV3(mode).layer_order()
    return [layer for layer in ordered_layers if _bool(layer_visibility.get(layer), False)]


def _first_present(sources: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> tuple[bool, Any]:
    for source in sources:
        for key in keys:
            if key in source:
                return True, source.get(key)
    return False, None


def _source_text_status_wrong(sources: Sequence[Mapping[str, Any]]) -> bool:
    for source in sources:
        for key in ("status", "source_status", "lock_status", "validation_status", "reason"):
            value = source.get(key)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if "wrong_surface" in lowered or "wrong surface" in lowered or "surface_mismatch" in lowered:
                    return True
    return False


def _broker_source_summary(session: Mapping[str, Any]) -> dict[str, Any]:
    broker_surface = _mapping(session.get("broker_surface"))
    locked_window = _mapping(session.get("locked_window"))
    descriptor = _mapping(session.get("descriptor"))
    tracking = _mapping(session.get("tracking_summary"))
    tracking_surface = _mapping(tracking.get("broker_surface"))
    sources: list[dict[str, Any]] = [
        _mapping(session.get("broker_source")),
        _mapping(session.get("broker_source_lock")),
        _mapping(session.get("broker_source_summary")),
        _mapping(session.get("source_validation")),
        _mapping(broker_surface.get("broker_source")),
        _mapping(broker_surface.get("broker_source_lock")),
        _mapping(broker_surface.get("source")),
        _mapping(broker_surface.get("source_validation")),
        _mapping(broker_surface.get("lock_validation")),
        tracking_surface,
        _mapping(tracking_surface.get("broker_source")),
        _mapping(tracking_surface.get("broker_source_lock")),
    ]
    sources = [source for source in sources if source]

    def bool_field(keys: Sequence[str], default: bool) -> bool:
        found, value = _first_present(sources, keys)
        return _bool(value, default) if found else default

    lock_found, lock_value = _first_present(
        sources,
        ("lock_id", "source_lock_id", "surface_lock_id", "broker_lock_id", "window_lock_id", "locked_hwnd", "hwnd"),
    )
    lock_id = _text(lock_value) if lock_found else ""
    if not lock_id:
        lock_id = _text(
            session.get("lock_id")
            or session.get("source_capture_id")
            or session.get("display_capture_id")
            or locked_window.get("hwnd")
            or descriptor.get("hwnd")
            or locked_window.get("title")
            or session.get("locked_title")
            or descriptor.get("title")
        )

    wrong_surface = bool_field(
        (
            "wrong_surface",
            "surface_mismatch",
            "window_mismatch",
            "lock_mismatch",
            "wrong_window",
            "wrong_broker_surface",
        ),
        False,
    ) or _source_text_status_wrong(sources)
    url_valid = bool_field(("url_valid", "frame_url_valid", "window_url_valid", "source_url_valid"), True)
    pixel_fingerprint_valid = bool_field(
        (
            "pixel_fingerprint_valid",
            "fingerprint_valid",
            "visual_fingerprint_valid",
            "broker_surface_hash_stable",
            "surface_hash_stable",
        ),
        True,
    )

    title_found, title_value = _first_present(sources, ("title_valid", "window_title_valid", "source_title_valid"))
    if title_found:
        title_valid = _bool(title_value, True)
    else:
        title_valid = True
    if not title_valid:
        wrong_surface = True

    valid_found, valid_value = _first_present(
        sources,
        ("valid", "source_valid", "lock_valid", "surface_valid", "broker_source_valid", "is_valid"),
    )
    base_valid = _bool(valid_value, True) if valid_found else True
    valid = bool(base_valid and not wrong_surface and url_valid and title_valid and pixel_fingerprint_valid)
    status_found, status_value = _first_present(sources, ("status", "lock_status"))
    status_text = _text(status_value).upper() if status_found else ""
    if status_text and status_text not in {"VALID", "OK", "PASS", "LOCKED"}:
        valid = False
        if status_text in {"WRONG_SURFACE", "TITLE_MATCH_PIXEL_MISMATCH"}:
            wrong_surface = True
    return {
        "lock_id": lock_id,
        "valid": valid,
        "status": status_text or ("VALID" if valid else "UNKNOWN"),
        "wrong_surface": bool(wrong_surface),
        "url_valid": bool(url_valid),
        "title_valid": bool(title_valid),
        "pixel_fingerprint_valid": bool(pixel_fingerprint_valid),
    }


def _broker_source_block_reason(broker_source: Mapping[str, Any]) -> str:
    if not broker_source:
        return ""
    if _bool(broker_source.get("wrong_surface"), False):
        return "broker source rejected: wrong surface"
    if _bool(broker_source.get("valid"), True):
        return ""
    invalid_fields = [
        label
        for label, key in (
            ("url", "url_valid"),
            ("title", "title_valid"),
            ("pixel_fingerprint", "pixel_fingerprint_valid"),
        )
        if not _bool(broker_source.get(key), True)
    ]
    suffix = ", ".join(invalid_fields) if invalid_fields else "source lock"
    return f"broker source rejected: invalid {suffix}"


def _empty_overlay_reason(
    *,
    source_block_reason: str,
    total_count: int,
    renderable_count: int,
    active_mode: str,
) -> str:
    if renderable_count > 0:
        return ""
    if source_block_reason:
        return source_block_reason
    if total_count <= 0:
        return "no market overlays available for the current broker surface"
    return f"no renderable overlays for mode {active_mode}"


def _broker_surface_summary(
    session: Mapping[str, Any],
    surface_frame: Mapping[str, Any],
    frame_timing: Mapping[str, Any],
    *,
    now_epoch: float,
) -> dict[str, Any]:
    frame_url = _text(surface_frame.get("url"))
    frame_age = _float(frame_timing.get("frame_age_ms"), 0.0)
    if frame_age <= 0.0:
        published = _float(session.get("display_published_epoch") or session.get("last_capture_epoch"), 0.0)
        if published > 0.0:
            frame_age = max(0.0, (float(now_epoch) - published) * 1000.0)
    return {
        "frame_id": _int(session.get("display_frame_id") or session.get("frame_index") or session.get("capture_count")),
        "frame_url": frame_url,
        "url": frame_url,
        "image_url": frame_url,
        "latest_window_url": frame_url,
        "width": _int(surface_frame.get("width")),
        "height": _int(surface_frame.get("height")),
        "age_ms": round(frame_age, 3),
        "exists": bool(surface_frame.get("exists")),
        "frame": dict(surface_frame),
    }


def _shooter_summary(session_id: str, shooter_state: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = _mapping(shooter_state)
    explicit_available = payload.get("available")
    available = bool(payload) if explicit_available is None else _bool(explicit_available, bool(payload))
    return {
        "available": available,
        "session_id": _text(payload.get("session_id")),
        "session_match": bool(payload and _text(payload.get("session_id")) in {"", session_id}),
        "state": _text(payload.get("state") or payload.get("status")),
        "mode": _text(payload.get("mode") or payload.get("shooter_mode")),
        "side": _text(payload.get("side") or payload.get("action")),
        "raw": payload,
    }


def _two_candle_and_lstm_payloads(session: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    tracking = _mapping(session.get("tracking_summary"))
    signal = _mapping(session.get("latest_signal"))
    kernel = _mapping(signal.get("decision_kernel") or tracking.get("decision_kernel"))
    forecast = _mapping(
        signal.get("high_frequency_forecast")
        or tracking.get("high_frequency_forecast")
        or signal.get("micro_candle_forecast")
        or tracking.get("micro_candle_forecast")
        or kernel.get("high_frequency_forecast")
    )
    two_candle = _first_mapping(
        signal.get("two_candle_study"),
        tracking.get("two_candle_study"),
        forecast.get("two_candle_study"),
        kernel.get("two_candle_study"),
    )
    lstm = _first_mapping(
        signal.get("lstm_contribution"),
        tracking.get("lstm_contribution"),
        forecast.get("lstm_contribution"),
        kernel.get("lstm_contribution"),
        two_candle.get("lstm_contribution"),
    )
    return two_candle, lstm


def _visual_plane_state(frontend_heartbeat: Mapping[str, Any] | None) -> dict[str, Any]:
    heartbeat = _mapping(frontend_heartbeat)
    plane = _mapping(heartbeat.get("visual_plane"))
    zoom = _float(plane.get("zoom", heartbeat.get("zoom", 1.0)), 1.0)
    pan_x = _float(plane.get("pan_x", heartbeat.get("pan_x", 0.0)), 0.0)
    pan_y = _float(plane.get("pan_y", heartbeat.get("pan_y", 0.0)), 0.0)
    return {
        "schema_version": "PG_VISUAL_PLANE_STATE_V3",
        "zoom": max(0.25, min(2.5, zoom)),
        "pan_x": pan_x,
        "pan_y": pan_y,
        "locked": True,
        "last_user_change_epoch_ms": _int(plane.get("last_user_change_epoch_ms", heartbeat.get("last_user_change_epoch_ms", 0))),
        "auto_zoom_enabled": False,
        "reason": "Live mode preserves the user-selected visual plane; overlays do not auto-zoom the dashboard.",
    }


def _overlay_layout_payload(
    precision_audit: Mapping[str, Any],
    *,
    rendered_count: int,
    hidden_count: int,
    active_mode: str,
) -> dict[str, Any]:
    report = _mapping(precision_audit.get("precision_report"))
    return {
        "schema_version": "PG_OVERLAY_LAYOUT_V3",
        "mode": active_mode,
        "collision_count": _int(report.get("label_collisions")),
        "hidden_for_budget": hidden_count,
        "rendered_count": rendered_count,
        "duplicate_count": _int(report.get("duplicate_boxes")),
        "duplicate_now_hidden": _int(report.get("duplicate_now_hidden")),
        "current_candle_label_hidden": active_mode == "CLEAN_LIVE",
        "diagnostics_overlap_allowed": active_mode in {"DIAGNOSTICS", "DEBUG", "INSPECTOR"},
    }


def _overlay_vocabulary_payload(
    all_overlays: Sequence[Mapping[str, Any]],
    rendered_overlays: Sequence[Mapping[str, Any]],
    *,
    active_mode: str,
) -> dict[str, Any]:
    approved_labels = list(approved_overlay_display_labels())
    approved_tokens = {str(label).strip().upper().replace(" ", "_") for label in approved_labels}
    unknown_terms: list[str] = []
    remapped_labels: list[dict[str, str]] = []
    visible_unapproved: list[str] = []
    for row in all_overlays:
        raw_label = _text(row.get("unmapped_display_label") or row.get("raw_display_label"))
        display_label = _text(row.get("display_label") or row.get("short_label"))
        status = _text(row.get("display_label_status"))
        if status == "unmapped" and raw_label and raw_label not in unknown_terms:
            unknown_terms.append(raw_label)
        if status == "remapped" and raw_label and display_label and raw_label != display_label:
            item = {"raw": raw_label, "display": display_label}
            if item not in remapped_labels:
                remapped_labels.append(item)
    visible_label_rows = [row for row in rendered_overlays if row.get("label_hidden") is not True and row.get("label_hidden") != "true"]
    for row in visible_label_rows:
        display_label = _text(row.get("display_label") or row.get("label"))
        if display_label and not is_approved_overlay_display_label(display_label) and display_label not in visible_unapproved:
            visible_unapproved.append(display_label)
    return {
        "schema_version": "PG_OVERLAY_VOCABULARY_AUDIT_V3",
        "active_mode": active_mode,
        "approved_label_count": len(approved_labels),
        "approved_labels": approved_labels,
        "approved_label_tokens": sorted(approved_tokens),
        "visible_labels": [_text(row.get("display_label") or row.get("label")) for row in visible_label_rows if _text(row.get("display_label") or row.get("label"))],
        "visible_unapproved_labels": visible_unapproved,
        "dictionary_coverage_ok": not visible_unapproved,
        "unknown_or_unmapped_terms": unknown_terms,
        "labels_remapped": remapped_labels,
    }


def _overlay_ledger_payload(
    all_overlays: Sequence[Mapping[str, Any]],
    rendered_overlays: Sequence[Mapping[str, Any]],
    *,
    active_mode: str,
) -> dict[str, Any]:
    rendered_ids = {
        _text(row.get("overlay_id") or row.get("id") or row.get("object_id"))
        for row in rendered_overlays
        if _text(row.get("overlay_id") or row.get("id") or row.get("object_id"))
    }
    rows: list[dict[str, Any]] = []
    display_counts: dict[str, int] = {}
    rejected_count = 0
    for index, row in enumerate(all_overlays):
        overlay_id = _text(row.get("overlay_id") or row.get("id") or row.get("object_id"), f"overlay-{index}")
        if row.get("precision_rejected"):
            rejected_count += 1
            continue
        display_state = _text(row.get("display_state"), "COMPACT").upper()
        display_counts[display_state] = display_counts.get(display_state, 0) + 1
        chart_visible = overlay_id in rendered_ids
        label_visible = bool(chart_visible and row.get("label_hidden") is not True and row.get("label_visible") is not False)
        rows.append(
            {
                "overlay_id": overlay_id,
                "object_id": _text(row.get("object_id")),
                "track_id": _text(row.get("track_id")),
                "type": _text(row.get("type")),
                "layer": _text(row.get("layer")),
                "display_label": _text(row.get("display_label") or row.get("label")),
                "display_state": display_state,
                "visual_weight": round(_float(row.get("visual_weight"), 0.0), 3),
                "semantic_family": _text(_mapping(row.get("style")).get("semantic_family")),
                "source_agent": _text(row.get("source_agent")),
                "truth_score": row.get("truth_score"),
                "chart_visible": chart_visible,
                "geometry_visible": bool(chart_visible and row.get("geometry_visible") is not False),
                "label_visible": label_visible,
                "inspector_visible": bool(row.get("inspector_visible") is not False),
                "label_lane": _text(row.get("label_lane") or row.get("label_anchor")),
                "parent_overlay_id": _text(row.get("parent_overlay_id")),
                "group_id": _text(row.get("group_id")),
                "reason": _text(row.get("representation_reason") or row.get("reason")),
            }
        )
    rows.sort(key=lambda item: (bool(item["chart_visible"]), _float(item.get("visual_weight"), 0.0)), reverse=True)
    return {
        "schema_version": "PG_OVERLAY_LEDGER_V3",
        "active_mode": active_mode,
        "valid_count": len(rows),
        "ledger_count": len(rows),
        "chart_visible_geometry_count": len([row for row in rows if row["geometry_visible"]]),
        "label_visible_count": len([row for row in rows if row["label_visible"]]),
        "inspector_visible_count": len([row for row in rows if row["inspector_visible"]]),
        "rejected_count": rejected_count,
        "display_state_counts": display_counts,
        "objects": rows,
    }


def _compact_scalar_and_selected(payload: Mapping[str, Any], selected_keys: set[str]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            compact[str(key)] = value
        elif key in selected_keys:
            compact[str(key)] = value
    return compact


def _compact_recent_studies(rows: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    studies = _sequence_of_mappings(rows)
    compact_rows: list[dict[str, Any]] = []
    for row in studies[-limit:]:
        compact_rows.append(
            {
                key: row.get(key)
                for key in (
                    "timestamp",
                    "created_at",
                    "side",
                    "action",
                    "confidence",
                    "summary",
                    "setup",
                    "execution_action",
                    "execution_block_reason",
                    "state",
                    "packet_id",
                )
                if row.get(key) not in (None, "", [], {})
            }
        )
    return compact_rows


def _compact_tracking_summary(tracking: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "artifact_integrity",
        "box_context",
        "broker_execution_state",
        "broker_identity",
        "broker_source",
        "broker_source_lock",
        "broker_surface",
        "candle_statistics",
        "chart_region",
        "current_box",
        "decision_kernel",
        "display_region",
        "execution_timing",
        "focus_region",
        "global_local_control",
        "high_frequency_forecast",
        "instrument_context",
        "lstm_contribution",
        "major_trend_context",
        "map_timing",
        "micro_candle_forecast",
        "overlay_truth_audit",
        "phoenixguard_report",
        "pipeline_timing",
        "projection",
        "historical_structure",
        "angle_vectors",
        "signal_thesis_v3",
        "smart_money_context",
        "structure_boxes",
        "study_stage_timings",
        "support_resistance_context",
        "support_resistance_zones",
        "symbol_context",
        "timing_signal",
        "two_candle_study",
        "tracked_candles",
    }
    compact = _compact_scalar_and_selected(tracking, selected)
    compact["model_council_result_present"] = bool(tracking.get("model_council_result"))
    compact["model_council_packet_present"] = bool(tracking.get("model_council") or tracking.get("model_council_study_packet"))
    return compact


def _compact_latest_signal(signal: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "broker_execution_state",
        "broker_source",
        "broker_source_lock",
        "countertrend_lane",
        "decision_kernel",
        "entry_distance",
        "execution_timing",
        "global_local_control",
        "high_frequency_forecast",
        "instrument_context",
        "lstm_contribution",
        "major_trend_context",
        "map_timing",
        "micro_candle_forecast",
        "overlay_instructions",
        "overlay_truth_audit",
        "pipeline_timing",
        "projection",
        "historical_structure",
        "angle_vectors",
        "probability",
        "signal_thesis_v3",
        "smart_money_context",
        "study_stage_timings",
        "support_resistance_context",
        "support_resistance_zones",
        "symbol_context",
        "timing_signal",
        "two_candle_study",
    }
    compact = _compact_scalar_and_selected(signal, selected)
    compact["model_council_result_present"] = bool(signal.get("model_council_result"))
    compact["model_council_packet_present"] = bool(signal.get("model_council") or signal.get("model_council_study_packet"))
    return compact


def _compact_model_council_result(result: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "angle_context",
        "current_candle_contract",
        "entry_quality",
        "execution_lane",
        "final_reasoning_decision",
        "instrument_context",
        "market_play",
        "market_reality",
        "pair_profile",
        "price_location",
        "reality_adjustments",
        "reasoning_arbitration",
        "regime",
        "promotion_trace",
        "sequence_context",
        "sequence_context_readiness",
        "signal_thesis_v3",
        "symbol_context",
        "time_to_reward_invalidation",
        "timing_decision",
        "timing_forecast",
        "trade_permission",
        "two_candle_study",
        "lstm_contribution",
    }
    compact = _compact_scalar_and_selected(result, selected)
    council = _mapping(result.get("model_council"))
    if council:
        compact["model_council"] = _compact_scalar_and_selected(
            council,
            {
                "execution",
                "trade_permission",
                "entry_quality",
                "market_reality",
                "promotion_trace",
                "sequence_context",
                "sequence_context_readiness",
            },
        )
    compact["study_packet_present"] = bool(result.get("study_packet") or result.get("model_council_study_packet"))
    return compact


def _compact_performance_trace_v3(trace: Mapping[str, Any]) -> dict[str, Any]:
    timing = _mapping(trace.get("timing_trace"))
    visual_health = _mapping(trace.get("visual_health"))
    keep_timing_keys = {
        "schema_version",
        "frame_id",
        "frame_age_ms",
        "overlay_age_ms",
        "model_vote_age_ms",
        "packet_age_ms",
        "frontend_render_age_ms",
        "state_publish_age_ms",
        "overlay_state_version",
        "overlay_frame_state_version",
        "model_state_version",
        "display_frame_id",
        "overlay_frame_id",
        "overlay_frame_gap",
        "raw_overlay_frame_gap",
        "surface_signature_aligned",
        "display_only_authority_locked",
        "frame_gap_status",
        "stale_status",
        "stale_flags",
        "freshness_score",
        "source",
    }
    compact: dict[str, Any] = {
        "schema_version": trace.get("schema_version"),
        "session_id": trace.get("session_id"),
        "generated_epoch": trace.get("generated_epoch"),
        "frame_id": trace.get("frame_id"),
        "state_version": trace.get("state_version"),
        "overlay_state_version": trace.get("overlay_state_version"),
        "overlay_frame_state_version": trace.get("overlay_frame_state_version"),
        "display_frame": _compact_scalar_and_selected(
            _mapping(trace.get("display_frame")),
            {"frame_id", "age_ms", "url"},
        ),
        "overlay_state": _compact_scalar_and_selected(
            _mapping(trace.get("overlay_state")),
            {"frame_id", "age_ms", "fresh", "overlay_state_version", "overlay_frame_state_version"},
        ),
        "model_state": _compact_scalar_and_selected(
            _mapping(trace.get("model_state")),
            {"frame_id", "age_ms", "fresh", "models_awake", "models_total", "queue_depth"},
        ),
        "frontend_state": _compact_scalar_and_selected(
            _mapping(trace.get("frontend_state")),
            {"age_ms", "fresh"},
        ),
        "metrics": dict(_mapping(trace.get("metrics"))),
        "timing_trace": _compact_scalar_and_selected(timing, keep_timing_keys),
        "visual_health": _compact_scalar_and_selected(
            visual_health,
            {"status", "frame_age_ms", "overlay_age_ms", "model_vote_age_ms", "packet_age_ms", "frontend_render_age_ms", "stale_flags"},
        ),
        "adaptive_performance": _compact_scalar_and_selected(
            _mapping(trace.get("adaptive_performance")),
            {"schema_version", "profile", "reasons", "rule"},
        ),
    }
    stale_status = timing.get("stale_status") or visual_health.get("status")
    if stale_status:
        compact["stale_status"] = stale_status
    return compact


def _compact_session_payload(session: Mapping[str, Any]) -> dict[str, Any]:
    keep_keys = (
        "session_id",
        "name",
        "market",
        "window_query",
        "layout_profile",
        "effective_layout_profile",
        "capture_interval_sec",
        "rl_track_interval_sec",
        "status",
        "tracking_enabled",
        "created_at",
        "updated_at",
        "last_capture_at",
        "last_capture_started_epoch",
        "last_capture_epoch",
        "display_frame_id",
        "display_capture_epoch",
        "display_published_epoch",
        "chart_frame_id",
        "overlay_frame_id",
        "full_overlay_frame_id",
        "model_vote_frame_id",
        "model_capture_epoch",
        "model_council_update_pending",
        "model_council_pending_frame_id",
        "model_council_pending_capture_epoch",
        "source_capture_id",
        "last_window_surface_signature",
        "last_display_surface_signature",
        "last_study_surface_signature",
        "overlay_source_window_signature",
        "overlay_source_study_signature",
        "capture_count",
        "frame_index",
        "state_version",
        "decision_version",
        "decision_valid_until_epoch",
        "locked_window",
        "locked_title",
        "manual_focus_region",
        "focus_selector",
        "execution_controls",
        "broker_source",
        "broker_source_lock",
        "broker_surface",
        "broker_execution_state",
        "last_frame_path",
        "last_window_path",
        "last_display_window_path",
        "last_chart_path",
        "last_display_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
        "last_decision_path",
        "memory_projection_predict",
        "memory_projection_future",
        "memory_projection_active_mode",
        "execution_debug",
        "execution_debug_log_path",
        "signal_thesis_v3",
        "model_council_study_packet",
        "model_council_packet",
        "execution_packet",
    )
    compact = {key: session.get(key) for key in keep_keys if key in session}
    now_epoch = time.time()
    for packet_key in ("model_council_packet", "execution_packet"):
        if packet_key in compact and not _current_execution_packet(compact.get(packet_key), now_epoch=now_epoch):
            compact.pop(packet_key, None)
    if not compact.get("model_council_packet") and not compact.get("execution_packet"):
        broker_execution_state = _mapping(compact.get("broker_execution_state"))
        if _text(broker_execution_state.get("status")).lower() == "external_shooter_required":
            broker_execution_state.update(
                {
                    "status": "blocked_by_runtime",
                    "message": "Model Council V3 executable packet expired or is missing; waiting for a fresh PG_EXECUTION_PACKET_V3.",
                    "side": "HOLD",
                    "lane": "LIVE_MARKET_FLOW_WAIT",
                    "actionable": False,
                }
            )
            compact["broker_execution_state"] = broker_execution_state
    if bool(compact.get("tracking_enabled", False)):
        current_status = str(compact.get("status", "") or "").strip().lower()
        compact["status"] = current_status if current_status in {"running", "tracking"} else "running"
    compact["tracking_summary"] = _compact_tracking_summary(_mapping(session.get("tracking_summary")))
    compact["latest_signal"] = _compact_latest_signal(_mapping(session.get("latest_signal")))
    compact["model_council_result"] = _compact_model_council_result(_mapping(session.get("model_council_result")))
    compact["recent_studies"] = _compact_recent_studies(session.get("recent_studies"))
    return compact


def compact_session_payload(session: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_session_payload(session)


def _compact_live_poll_session_payload(session: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "session_id",
        "name",
        "market",
        "window_query",
        "layout_profile",
        "effective_layout_profile",
        "capture_interval_sec",
        "rl_track_interval_sec",
        "status",
        "tracking_enabled",
        "created_at",
        "updated_at",
        "last_capture_at",
        "last_capture_started_epoch",
        "last_capture_epoch",
        "display_frame_id",
        "display_capture_epoch",
        "display_published_epoch",
        "chart_frame_id",
        "overlay_frame_id",
        "full_overlay_frame_id",
        "model_vote_frame_id",
        "model_capture_epoch",
        "source_capture_id",
        "last_window_surface_signature",
        "last_display_surface_signature",
        "last_study_surface_signature",
        "overlay_source_window_signature",
        "overlay_source_study_signature",
        "capture_count",
        "frame_index",
        "state_version",
        "decision_version",
        "decision_valid_until_epoch",
        "locked_window",
        "locked_title",
        "manual_focus_region",
        "focus_selector",
        "execution_controls",
        "broker_source",
        "broker_source_lock",
        "broker_surface",
        "broker_execution_state",
        "last_frame_path",
        "last_window_path",
        "last_display_window_path",
        "last_chart_path",
        "last_display_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
    }
    compact = {key: session.get(key) for key in selected if key in session}
    latest_signal = _mapping(session.get("latest_signal"))
    compact["latest_signal"] = _compact_scalar_and_selected(
        latest_signal,
        {
            "session_id",
            "signal_id",
            "side",
            "action",
            "execution_action",
            "confidence",
            "status",
            "final_state",
            "lane",
            "execution_lane",
            "symbol",
            "pair",
            "timeframe",
            "published_epoch",
            "signal_age_sec",
            "broker_source",
            "broker_source_lock",
            "promotion_failure_audit_v3",
        },
    )
    tracking = _mapping(session.get("tracking_summary"))
    compact["tracking_summary"] = _compact_scalar_and_selected(
        tracking,
        {
            "session_id",
            "detected_market",
            "detected_timeframe",
            "status",
            "frame_index",
            "capture_count",
            "display_frame_id",
            "last_capture_epoch",
            "broker_source",
            "broker_source_lock",
            "broker_surface",
            "pipeline_timing",
        },
    )
    model_result = _mapping(session.get("model_council_result"))
    if model_result:
        compact["model_council_result"] = _compact_scalar_and_selected(
            model_result,
            {
                "packet_id",
                "state",
                "final_state",
                "side",
                "final_side",
                "lane",
                "execution_lane",
                "score",
                "confidence",
                "actionable",
                "denied_at",
                "next_required",
                "promotion_failure_audit_v3",
            },
        )
    return compact


def build_live_state_v3(
    session_payload: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Path | str] | None = None,
    active_objects: Sequence[Mapping[str, Any]] | None = None,
    registry_entries: Sequence[Mapping[str, Any]] | None = None,
    study_packet: Mapping[str, Any] | None = None,
    execution_packet: Mapping[str, Any] | None = None,
    model_health: Mapping[str, Any] | None = None,
    shooter_state: Mapping[str, Any] | None = None,
    frontend_heartbeat: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
    artifact_probe: Callable[[str], Path | str] | None = None,
    overlay_mode: str = "CLEAN_LIVE",
    compact_public: bool = False,
) -> dict[str, Any]:
    now_value = float(now_epoch if now_epoch is not None else time.time())
    now_ms = int(now_value * 1000.0)
    requested_overlay_mode = _text(overlay_mode, "CLEAN_LIVE")
    active_overlay_mode = normalize_view_mode(requested_overlay_mode)
    visible_layers = _mode_visible_layers(active_overlay_mode)
    overlay_mode_payload: dict[str, Any] = {
        "requested": requested_overlay_mode,
        "active": active_overlay_mode,
        "available_modes": list(VIEW_MODES),
        "visible_layers": visible_layers,
        "reason_if_empty": "",
    }
    session = dict(session_payload)
    session_id = _text(session.get("session_id"), "session")
    model_health_payload = dict(model_health or {})
    broker_source = _broker_source_summary(session)
    source_block_reason = _broker_source_block_reason(broker_source)
    artifact_refs = _artifact_refs(session, artifacts=artifacts, artifact_probe=artifact_probe)
    plot = _plot_area(session, artifact_refs)
    chart_transform = _chart_transform(session, plot)
    registry = build_market_object_registry_v3(session)
    scene_graph = build_broker_scene_graph_v3(session, artifacts=artifact_refs).as_dict()["scene_graph"]
    display_overlay_artifact = artifact_refs["full-overlay"] if artifact_refs["full-overlay"]["exists"] else artifact_refs["overlay"]
    overlay_artifact_aligned, overlay_artifact_mismatch_reason, overlay_artifact_frame_id = _overlay_artifact_alignment(
        overlay_artifact=display_overlay_artifact,
        overlay_object_frame_id=registry.frame_id,
    )
    overlay_authority_locked = bool(
        not overlay_artifact_aligned
        and _display_only_overlay_authority_locked(
            session,
            overlay_object_frame_id=registry.frame_id,
            source_block_reason=source_block_reason,
        )
    )
    overlay_render_alignment_ok = bool(overlay_artifact_aligned or overlay_authority_locked)
    current_side = _text(
        _mapping(session.get("latest_signal")).get("action")
        or _mapping(session.get("latest_signal")).get("side")
        or _mapping(session.get("latest_signal")).get("execution_action")
    ).upper()
    two_candle_study, lstm_contribution = _two_candle_and_lstm_payloads(session)
    raw_overlays = _combine_overlays(
        registry,
        active_objects=active_objects,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
        scene_graph=scene_graph,
    )
    signal_thesis = _first_mapping(
        session.get("signal_thesis_v3"),
        _mapping(session.get("latest_signal")).get("signal_thesis_v3"),
        _mapping(session.get("tracking_summary")).get("signal_thesis_v3"),
        _mapping(session.get("model_council_result")).get("signal_thesis_v3"),
    )
    thesis_overlays = [] if source_block_reason else _signal_thesis_overlay_objects(
        signal_thesis,
        frame_id=registry.frame_id,
        sequence_id=registry.sequence_context.sequence_id,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
    )
    study_overlays = [] if source_block_reason else _study_overlay_objects(
        session,
        two_candle_study,
        lstm_contribution,
        active_mode=active_overlay_mode,
        frame_id=registry.frame_id,
        sequence_id=registry.sequence_context.sequence_id,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
        now_ms=now_ms,
    )
    council_overlays = [] if source_block_reason else _council_overlay_objects(
        session,
        study_packet or _mapping(session.get("model_council_study_packet")),
        frame_id=registry.frame_id,
        sequence_id=registry.sequence_context.sequence_id,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
        now_ms=now_ms,
    )
    broker_control_modes = {"BROKER", "CALIBRATION"}
    broker_control_overlays = [] if source_block_reason or active_overlay_mode not in broker_control_modes else _broker_control_overlay_objects(
        session,
        scene_graph=scene_graph,
        frame_id=registry.frame_id,
        sequence_id=registry.sequence_context.sequence_id,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
        broker_source_lock_id=_text(broker_source.get("lock_id")),
        now_ms=now_ms,
    )
    precision_input_overlays = raw_overlays + thesis_overlays + study_overlays + council_overlays + broker_control_overlays
    if source_block_reason:
        precision_overlays = []
        precision_audit: dict[str, Any] = {
            "schema_version": OVERLAY_PRECISION_AUDIT_SCHEMA_VERSION,
            "frame_id": registry.frame_id,
            "overlay_count": len(precision_input_overlays),
            "rendered_count": 0,
            "rejected_count": len(precision_input_overlays),
            "precision_report": {
                "unanchored_boxes": 0,
                "oversized_boxes": 0,
                "duplicate_boxes": 0,
                "label_collisions": 0,
                "outside_plot_area": 0,
                "stale_frame_id": 0,
                "missing_transform": 0,
                "refined_oversized_inputs": 0,
                "outside_rejected": 0,
                "unanchored_inputs_fixed": 0,
                "broker_source_rejected": len(precision_input_overlays),
            },
            "source_block_reason": source_block_reason,
        }
    else:
        precision_overlays, precision_audit = resolve_precision_overlays_v3(
            precision_input_overlays,
            scene_graph=scene_graph,
            mode=active_overlay_mode,
            current_side=current_side,
            frame_id=registry.frame_id,
        )
    layer_manager = OverlayLayerManagerV3(active_overlay_mode, now_ms=now_ms)
    clean_overlays_only = str(os.getenv("PHOENIXGUARD_LIVE_STATE_CLEAN_OVERLAYS_ONLY", "0") or "0").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    clean_mode_prefilter = bool(clean_overlays_only and active_overlay_mode == "CLEAN_LIVE")
    if overlay_render_alignment_ok:
        overlay_source = [
            overlay
            for overlay in precision_overlays
            if (not clean_mode_prefilter or _overlay_visible_for_mode(overlay, "CLEAN_LIVE", now_ms=now_ms))
            and _overlay_visible_for_mode(overlay, active_overlay_mode, now_ms=now_ms)
            and _overlay_is_frame_aligned(overlay, registry.frame_id)
        ]
    else:
        overlay_source = []
    overlay_source = sorted(overlay_source, key=layer_manager.overlay_sort_key)[: int(layer_manager.as_dict()["active_budget"])]
    overlays = [
        _dashboard_overlay_object(overlay, compact=clean_overlays_only)
        for overlay in overlay_source
    ]
    total_overlay_count = len(precision_input_overlays) if source_block_reason else len(precision_overlays)
    rejected_overlay_count = len(precision_input_overlays) if source_block_reason else len(
        [overlay for overlay in precision_overlays if overlay.get("precision_rejected")]
    )
    renderable_overlay_count = len(overlays)
    hidden_overlay_count = max(0, total_overlay_count - renderable_overlay_count - rejected_overlay_count)
    reason_if_empty = _empty_overlay_reason(
        source_block_reason=source_block_reason,
        total_count=total_overlay_count,
        renderable_count=renderable_overlay_count,
        active_mode=active_overlay_mode,
    )
    if overlay_artifact_mismatch_reason and renderable_overlay_count == 0 and not overlay_authority_locked:
        reason_if_empty = overlay_artifact_mismatch_reason
    overlay_mode_payload["reason_if_empty"] = reason_if_empty
    overlay_mode_payload["artifact_frame_id"] = overlay_artifact_frame_id
    overlay_mode_payload["overlay_object_frame_id"] = registry.frame_id
    overlay_mode_payload["artifact_frame_aligned"] = overlay_render_alignment_ok
    overlay_mode_payload["artifact_authority_locked"] = overlay_authority_locked
    overlays_payload: dict[str, Any] = {
        "count": renderable_overlay_count,
        "total_count": total_overlay_count,
        "renderable_count": renderable_overlay_count,
        "hidden_count": hidden_overlay_count,
        "rejected_count": rejected_overlay_count,
        "artifact_frame_id": overlay_artifact_frame_id,
        "overlay_object_frame_id": registry.frame_id,
        "artifact_frame_aligned": overlay_render_alignment_ok,
        "artifact_authority_locked": overlay_authority_locked,
        "artifact_mismatch_reason": overlay_artifact_mismatch_reason,
        "unknown_or_unmapped_terms": [],
        "vocabulary": {},
        "objects": overlays,
    }
    sequence_context = registry.sequence_context.as_dict()
    visual_health = build_visual_health_v3(
        session_id=session_id,
        artifacts=artifact_refs,
        overlay_objects=cast(list[Mapping[str, Any]], overlays),
        sequence_context={"source_status": registry.source_status, **sequence_context},
        model_health=model_health_payload,
        frontend_heartbeat=frontend_heartbeat,
    )
    frame_timing = build_frame_timing_trace_v3(
        session,
        overlays=overlays,
        model_health=model_health_payload,
        frontend_heartbeat=frontend_heartbeat,
        now_epoch=now_value,
    )
    model_warm_state = model_warm_states_from_health(
        model_health_payload,
        frame_id=registry.frame_id,
        now_epoch=now_value,
    )
    surface_frame = artifact_refs["window"]
    chart_frame = artifact_refs["chart"]
    broker_surface_payload = _broker_surface_summary(session, surface_frame, frame_timing, now_epoch=now_value)
    execution_packet_payload = (
        _current_execution_packet(execution_packet, now_epoch=now_value)
        or _current_execution_packet(session.get("model_council_packet"), now_epoch=now_value)
        or _current_execution_packet(session.get("execution_packet"), now_epoch=now_value)
    )
    visual_plane = _visual_plane_state(frontend_heartbeat)
    overlay_layout = _overlay_layout_payload(
        precision_audit,
        rendered_count=renderable_overlay_count,
        hidden_count=hidden_overlay_count,
        active_mode=active_overlay_mode,
    )
    overlay_vocabulary = _overlay_vocabulary_payload(
        [*precision_overlays, *thesis_overlays, *study_overlays, *council_overlays],
        overlays,
        active_mode=active_overlay_mode,
    )
    overlay_ledger = _overlay_ledger_payload(
        precision_overlays,
        overlays,
        active_mode=active_overlay_mode,
    )
    overlays_payload["unknown_or_unmapped_terms"] = overlay_vocabulary["unknown_or_unmapped_terms"]
    overlays_payload["vocabulary"] = overlay_vocabulary
    overlays_payload["ledger"] = overlay_ledger
    prediction_overlay = prediction_overlay_config()
    live_visual_state: dict[str, Any] = {
        "schema_version": LIVE_STATE_SCHEMA_VERSION,
        "session_id": session_id,
        "frame_id": _int(session.get("frame_index")),
        "state_version": _int(session.get("state_version")),
        "chart_transform_id": chart_transform["chart_transform_id"],
        "requested_mode": requested_overlay_mode,
        "active_mode": active_overlay_mode,
        "visible_layers": visible_layers,
        "overlay_count": total_overlay_count,
        "renderable_count": renderable_overlay_count,
        "hidden_count": hidden_overlay_count,
        "rejected_count": rejected_overlay_count,
        "overlay_artifact_frame_id": overlay_artifact_frame_id,
        "overlay_object_frame_id": registry.frame_id,
        "overlay_artifact_frame_aligned": overlay_render_alignment_ok,
        "overlay_artifact_authority_locked": overlay_authority_locked,
        "reason_if_empty": reason_if_empty,
        "unknown_or_unmapped_terms": overlay_vocabulary["unknown_or_unmapped_terms"],
        "visible_mode": active_overlay_mode,
        "overlay_mode": dict(overlay_mode_payload),
        "overlay_mode_name": active_overlay_mode,
        "tracking_summary": _mapping(session.get("tracking_summary")),
        "latest_signal": _mapping(session.get("latest_signal")),
        "broker_source": broker_source,
        "broker_surface": broker_surface_payload,
        "manual_focus_region": _mapping(session.get("manual_focus_region")),
        "focus_selector": _mapping(session.get("focus_selector")),
        "session": _compact_live_poll_session_payload(session),
        "broker_surface_frame": {
            "artifact": surface_frame,
            "frame_id": _int(session.get("display_frame_id") or session.get("frame_index")),
            "capture_epoch": _float(session.get("display_capture_epoch") or session.get("last_capture_started_epoch")),
            "published_epoch": _float(session.get("display_published_epoch") or session.get("last_capture_epoch")),
            "stream": "display",
            "capture_plane": _mapping(_mapping(session.get("broker_surface")).get("capture_plane")),
            "locked_title": _text(session.get("locked_title") or _mapping(session.get("descriptor")).get("title")),
            "window_query": _text(session.get("window_query"), "Pocket Option"),
        },
        "surface": {
            "selected_plane": "full_broker_surface",
            "frame": surface_frame,
            "overlay_frame": artifact_refs["full-overlay"] if artifact_refs["full-overlay"]["exists"] else artifact_refs["overlay"],
            "mode": "full_broker_surface",
        },
        "chart": {
            "frame": chart_frame,
            "plot_area": plot,
            "chart_transform": chart_transform,
            "scene_graph": scene_graph,
        },
        "chart_frame": {
            "artifact": chart_frame,
            "url": chart_frame["url"],
            "image_url": chart_frame["url"],
            "frame_url": chart_frame["url"],
            "overlay_url": artifact_refs["overlay"]["url"],
            "display_artifact": artifact_refs["full-overlay"] if artifact_refs["full-overlay"]["exists"] else chart_frame,
        },
        "chart_transform": chart_transform,
        "plot_area": plot,
        "scene_graph": scene_graph,
        "broker_scene_graph_v3": scene_graph,
        "overlay_layer_manager_v3": layer_manager.as_dict(),
        "overlay_precision_audit": precision_audit,
        "overlay_layout": overlay_layout,
        "overlay_vocabulary": overlay_vocabulary,
        "overlay_ledger_v3": overlay_ledger,
        "prediction_overlay": prediction_overlay,
        "two_candle_study": two_candle_study,
        "lstm_contribution": lstm_contribution,
        "visual_plane": visual_plane,
        "frame_timing_trace_v3": frame_timing,
        "frame_timing": frame_timing,
        "overlay_state_version": frame_timing["overlay_state_version"],
        "overlay_frame_state_version": frame_timing.get("overlay_frame_state_version", ""),
        "model_state_version": frame_timing["model_state_version"],
        "frame_age_ms": frame_timing["frame_age_ms"],
        "overlay_age_ms": frame_timing["overlay_age_ms"],
        "model_vote_age_ms": frame_timing["model_vote_age_ms"],
        "packet_age_ms": frame_timing["packet_age_ms"],
        "frontend_render_age_ms": frame_timing["frontend_render_age_ms"],
        "stale_status": frame_timing["stale_status"],
        "stale_flags": frame_timing["stale_flags"],
        "artifacts": artifact_refs,
        "overlays": overlays_payload,
        "overlay_objects": overlays,
        "market_objects": {
            "active_count": len(active_objects or registry_entries or registry.objects),
            "registry_count": len(registry_entries or registry.objects),
            "objects": [obj.as_dict() for obj in registry.objects],
            "source_status": dict(registry.source_status),
        },
        "market_object_registry": registry.as_dict(),
        "sequence_context": sequence_context,
        "sequence_context_v3": sequence_context,
        "signal_thesis_v3": signal_thesis,
        "two_candle_study_v3": two_candle_study,
        "lstm_candle_sequence_contribution_v3": lstm_contribution,
        "model_council": _model_council_summary(session, study_packet or session.get("model_council_study_packet")),
        "packets": {
            "study": _study_packet_summary(study_packet or _mapping(session.get("model_council_study_packet")), now_epoch=now_value),
            "execution": _study_packet_summary(execution_packet_payload, now_epoch=now_value),
        },
        "study_packet_status": _study_packet_summary(study_packet or _mapping(session.get("model_council_study_packet")), now_epoch=now_value),
        "execution_packet_status": _study_packet_summary(execution_packet_payload, now_epoch=now_value),
        "model_health": model_health_payload,
        "model_warm_state_v3": model_warm_state,
        "instrument": _instrument(session),
        "instrument_context": _instrument(session),
        "visual_health": {
            **visual_health,
            "full_broker_surface_visible": bool(surface_frame["exists"]),
            "overlay_contract_ok": bool(_mapping(visual_health.get("overlay")).get("contract_ok", False)),
        },
        "provider_status": dict(_mapping(session.get("live_state_provider_status"))),
        "shooter": _shooter_summary(session_id, shooter_state),
        "shooter_state": dict(_mapping(shooter_state)),
        "frontend_heartbeat": dict(_mapping(frontend_heartbeat)),
    }
    live_visual_state["performance_trace_v3"] = build_performance_trace_v3(live_visual_state, now_epoch=now_value)
    live_visual_state["vlm_context_skeleton_v3"] = build_vlm_context_skeleton_v3(live_visual_state)
    market_objects = _mapping(live_visual_state.get("market_objects"))
    compact_live_visual_state: dict[str, Any] = {
        "schema_version": live_visual_state["schema_version"],
        "session_id": live_visual_state["session_id"],
        "frame_id": live_visual_state["frame_id"],
        "state_version": live_visual_state["state_version"],
        "chart_transform_id": live_visual_state["chart_transform_id"],
        "requested_mode": live_visual_state["requested_mode"],
        "active_mode": live_visual_state["active_mode"],
        "visible_layers": live_visual_state["visible_layers"],
        "overlay_count": live_visual_state["overlay_count"],
        "renderable_count": live_visual_state["renderable_count"],
        "reason_if_empty": live_visual_state["reason_if_empty"],
        "overlay_mode": live_visual_state["overlay_mode"],
        "broker_source": live_visual_state["broker_source"],
        "broker_surface": live_visual_state["broker_surface"],
        "manual_focus_region": live_visual_state["manual_focus_region"],
        "focus_selector": live_visual_state["focus_selector"],
        "session": live_visual_state["session"],
        "surface": live_visual_state["surface"],
        "chart_frame": live_visual_state["chart_frame"],
        "plot_area": live_visual_state["plot_area"],
        "scene_graph": live_visual_state["scene_graph"],
        "overlay_layer_manager_v3": live_visual_state["overlay_layer_manager_v3"],
        "overlay_precision_audit": live_visual_state["overlay_precision_audit"],
        "overlay_layout": live_visual_state["overlay_layout"],
        "overlay_vocabulary": live_visual_state["overlay_vocabulary"],
        "overlay_ledger_v3": live_visual_state["overlay_ledger_v3"],
        "prediction_overlay": live_visual_state["prediction_overlay"],
        "two_candle_study": live_visual_state["two_candle_study"],
        "lstm_contribution": live_visual_state["lstm_contribution"],
        "visual_plane": live_visual_state["visual_plane"],
        "frame_timing_trace_v3": live_visual_state["frame_timing_trace_v3"],
        "overlay_state_version": live_visual_state["overlay_state_version"],
        "overlay_frame_state_version": live_visual_state["overlay_frame_state_version"],
        "performance_trace_v3": _compact_performance_trace_v3(_mapping(live_visual_state.get("performance_trace_v3"))),
        "vlm_context_skeleton_v3": live_visual_state["vlm_context_skeleton_v3"],
        "overlays": live_visual_state["overlays"],
        "market_objects": {
            "active_count": market_objects.get("active_count"),
            "registry_count": market_objects.get("registry_count"),
            "source_status": market_objects.get("source_status"),
        },
        "model_council": live_visual_state["model_council"],
        "signal_thesis_v3": live_visual_state["signal_thesis_v3"],
        "packets": live_visual_state["packets"],
        "visual_health": live_visual_state["visual_health"],
        "provider_status": live_visual_state["provider_status"],
        "shooter": live_visual_state["shooter"],
    }
    if compact_public:
        public_session = _compact_live_poll_session_payload(session)
        public_session.update(compact_live_visual_state)
        public_session["live_visual_state"] = compact_live_visual_state
        public_session.setdefault("tracking_summary", _mapping(public_session.get("tracking_summary")))
        public_session.setdefault("latest_signal", _mapping(public_session.get("latest_signal")))
    else:
        public_session = _compact_session_payload(session)
        public_session.update(live_visual_state)
        public_session["tracking_summary"] = _compact_tracking_summary(_mapping(live_visual_state.get("tracking_summary")))
        public_session["latest_signal"] = _compact_latest_signal(_mapping(live_visual_state.get("latest_signal")))
        public_session["model_council_result"] = _compact_model_council_result(_mapping(session.get("model_council_result")))
        public_session["live_visual_state"] = compact_live_visual_state
        public_session.setdefault("tracking_summary", _compact_tracking_summary(_mapping(session.get("tracking_summary"))))
        public_session.setdefault("latest_signal", _compact_latest_signal(_mapping(session.get("latest_signal"))))
    return public_session


def build_live_state_v3_from_tracker_service(
    tracker_service: Any,
    session_id: str,
    *,
    model_health_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    shooter_state_loader: Callable[[str], Mapping[str, Any]] | None = None,
    active_object_loader: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
    registry_loader: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
    frontend_heartbeat_loader: Callable[[str], Mapping[str, Any] | None] | None = None,
    now_epoch: float | None = None,
    overlay_mode: str = "CLEAN_LIVE",
    compact_public: bool = False,
) -> dict[str, Any]:
    degraded_sources: list[dict[str, str]] = []

    def mark_degraded(source: str, exc: Exception) -> None:
        degraded_sources.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})

    snapshot_getter = getattr(tracker_service, "get_session_snapshot", None)
    if callable(snapshot_getter):
        session = _mapping(snapshot_getter(session_id))
    else:
        session = _mapping(tracker_service.get_session(session_id))
    artifacts: dict[str, Path | str] = {}
    session_artifact_keys = {
        "window": "last_window_path",
        "chart": "last_chart_path",
        "overlay": "last_overlay_path",
        "full-overlay": "last_full_overlay_path",
        "projection": "last_projection_path",
        "memory-reference": "last_memory_reference_path",
    }
    for kind, key in session_artifact_keys.items():
        direct_path = _text(session.get(key))
        if direct_path:
            artifacts[kind] = direct_path
            continue
        try:
            artifacts[kind] = tracker_service.latest_artifact_path(session_id, kind)
        except Exception as exc:
            if kind in {"window", "chart", "overlay", "full-overlay"}:
                mark_degraded(f"artifact:{kind}", exc)
            pass
    try:
        study_packet = _mapping(tracker_service.latest_model_council_study_packet(session_id))
    except Exception as exc:
        mark_degraded("model_council_study_packet", exc)
        study_packet = _mapping(session.get("model_council_study_packet"))
    try:
        execution_packet = _mapping(tracker_service.latest_model_council_packet(session_id))
    except Exception as exc:
        mark_degraded("model_council_execution_packet", exc)
        fallback_execution_packet = _mapping(session.get("model_council_packet") or session.get("execution_packet"))
        execution_packet = _mapping(_current_execution_packet(
            fallback_execution_packet,
            now_epoch=float(now_epoch if now_epoch is not None else time.time()),
        ))
    model_health = _mapping(model_health_builder(session)) if model_health_builder else {}
    try:
        shooter_state = _mapping(shooter_state_loader(session_id)) if shooter_state_loader else {}
    except Exception as exc:
        mark_degraded("shooter_state", exc)
        shooter_state = {}
    try:
        active_objects = list(active_object_loader(session_id)) if active_object_loader else []
    except Exception as exc:
        mark_degraded("active_objects", exc)
        active_objects = []
    try:
        registry_entries = list(registry_loader(session_id)) if registry_loader else []
    except Exception as exc:
        mark_degraded("registry_entries", exc)
        registry_entries = []
    try:
        frontend_heartbeat = _mapping(frontend_heartbeat_loader(session_id)) if frontend_heartbeat_loader else {}
    except Exception as exc:
        mark_degraded("frontend_heartbeat", exc)
        frontend_heartbeat = {}
    session["live_state_provider_status"] = {
        "ok": not degraded_sources,
        "degraded": bool(degraded_sources),
        "degraded_sources": degraded_sources,
    }
    return build_live_state_v3(
        session,
        artifacts=artifacts,
        active_objects=active_objects,
        registry_entries=registry_entries,
        study_packet=study_packet,
        execution_packet=execution_packet,
        model_health=model_health,
        shooter_state=shooter_state,
        frontend_heartbeat=frontend_heartbeat,
        now_epoch=now_epoch,
        overlay_mode=overlay_mode,
        compact_public=compact_public,
    )


__all__ = [
    "LIVE_STATE_SCHEMA_VERSION",
    "build_live_state_v3",
    "build_live_state_v3_from_tracker_service",
    "compact_session_payload",
]


compact_session_payload = _compact_session_payload
