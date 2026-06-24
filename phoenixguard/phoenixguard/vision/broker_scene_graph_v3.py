from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from PIL import Image

from phoenixguard.vision.overlay_geometry import normalize_bbox


BROKER_SCENE_GRAPH_SCHEMA_VERSION = "PG_BROKER_SCENE_GRAPH_V3"


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[object], value))
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _int(value: Any, default: int = 0) -> int:
    return int(round(_float(value, default)))


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _bbox(value: object) -> list[float] | None:
    bbox_input = _sequence(value)
    bbox = normalize_bbox(bbox_input) if bbox_input else None
    return [float(item) for item in bbox] if bbox is not None else None


def _clip_box(box: Sequence[Any], clip: Sequence[Any]) -> list[float] | None:
    source = _bbox(box)
    target = _bbox(clip)
    if source is None or target is None:
        return None
    left = max(source[0], target[0])
    top = max(source[1], target[1])
    right = min(source[2], target[2])
    bottom = min(source[3], target[3])
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _image_size(path: Any) -> tuple[int, int]:
    text = _text(path)
    if not text:
        return (0, 0)
    try:
        with Image.open(Path(text)) as img:
            return int(img.width), int(img.height)
    except Exception:
        return (0, 0)


def _artifact_size(artifacts: Mapping[str, Any], kind: str) -> tuple[int, int]:
    artifact = _mapping(artifacts.get(kind))
    width = _int(artifact.get("width"))
    height = _int(artifact.get("height"))
    if width > 0 and height > 0:
        return width, height
    return _image_size(artifact.get("path"))


def _normalized_to_pixels(bbox: Sequence[Any], width: float, height: float) -> list[float] | None:
    values = _bbox(bbox)
    if values is None or width <= 0.0 or height <= 0.0:
        return None
    if max(abs(item) for item in values) <= 1.0001:
        return [values[0] * width, values[1] * height, values[2] * width, values[3] * height]
    return values


def _surface_size(session: Mapping[str, Any], artifacts: Mapping[str, Any]) -> tuple[int, int]:
    broker_surface = _mapping(session.get("broker_surface"))
    capture_plane = _mapping(broker_surface.get("capture_plane"))
    width = _int(capture_plane.get("width") or _mapping(broker_surface.get("control_visibility")).get("image_width"))
    height = _int(capture_plane.get("height") or _mapping(broker_surface.get("control_visibility")).get("image_height"))
    if width > 0 and height > 0:
        return width, height
    artifact_width, artifact_height = _artifact_size(artifacts, "window")
    if artifact_width > 0 and artifact_height > 0:
        return artifact_width, artifact_height
    tracking = _mapping(session.get("tracking_summary"))
    focus = _mapping(tracking.get("focus_region") or session.get("manual_focus_region"))
    focus_box = _bbox(focus.get("pixel_bbox"))
    if focus_box:
        return max(1, int(focus_box[2])), max(1, int(focus_box[3]))
    chart_width, chart_height = _artifact_size(artifacts, "chart")
    return max(1, chart_width), max(1, chart_height)


def _focus_bounds(session: Mapping[str, Any], surface_size: tuple[int, int]) -> list[float] | None:
    tracking = _mapping(session.get("tracking_summary"))
    focus = _mapping(tracking.get("focus_region")) or _mapping(session.get("manual_focus_region"))
    pixel = _bbox(focus.get("pixel_bbox"))
    if pixel:
        return pixel
    normalized = _sequence(focus.get("normalized_bbox"))
    if not normalized:
        normalized = _sequence(_mapping(session.get("manual_focus_region")).get("normalized_bbox"))
    return _normalized_to_pixels(normalized, float(surface_size[0]), float(surface_size[1]))


def _execution_panel_bounds(session: Mapping[str, Any], surface: Sequence[float], chart_full: Sequence[float]) -> list[float]:
    broker_surface = _mapping(session.get("broker_surface"))
    execution_boxes = _mapping(broker_surface.get("execution_boxes"))
    boxes: list[list[float]] = []
    for value in execution_boxes.values():
        box = _bbox(_mapping(value).get("bbox"))
        if box:
            boxes.append(box)
    for key in ("buy_button", "sell_button", "amount_field", "time_field"):
        box = _bbox(_mapping(broker_surface.get(key)).get("bbox"))
        if box:
            boxes.append(box)
    if boxes:
        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[2] for box in boxes)
        bottom = max(box[3] for box in boxes)
        width = max(160.0, right - left)
        return [
            max(surface[0], left - width * 0.18),
            max(chart_full[1], top - 90.0),
            min(surface[2], right + width * 0.24),
            min(chart_full[3], bottom + 120.0),
        ]
    panel_width = max(180.0, (surface[2] - surface[0]) * 0.18)
    return [max(chart_full[2], surface[2] - panel_width), chart_full[1], surface[2], chart_full[3]]


def _chart_region_bounds(
    session: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    surface_size: tuple[int, int],
) -> tuple[list[float], list[float], list[float]]:
    tracking = _mapping(session.get("tracking_summary"))
    chart_region = _mapping(tracking.get("chart_region") or tracking.get("display_region"))
    chart_raw = _bbox(chart_region.get("pixel_bbox") or chart_region.get("bbox"))
    chart_width = _int(chart_region.get("width"))
    chart_height = _int(chart_region.get("height"))
    artifact_chart_width, artifact_chart_height = _artifact_size(artifacts, "chart")
    if chart_width <= 0:
        chart_width = artifact_chart_width
    if chart_height <= 0:
        chart_height = artifact_chart_height
    if chart_raw is None:
        chart_raw = [0.0, 0.0, float(max(1, chart_width)), float(max(1, chart_height))]

    focus = _focus_bounds(session, surface_size)
    if focus and chart_raw[2] <= (focus[2] - focus[0]) + 4 and chart_raw[3] <= (focus[3] - focus[1]) + 4:
        chart_full = [focus[0] + chart_raw[0], focus[1] + chart_raw[1], focus[0] + chart_raw[2], focus[1] + chart_raw[3]]
    elif max(abs(item) for item in chart_raw) <= 1.0001:
        chart_full = [
            chart_raw[0] * surface_size[0],
            chart_raw[1] * surface_size[1],
            chart_raw[2] * surface_size[0],
            chart_raw[3] * surface_size[1],
        ]
    else:
        chart_full = chart_raw

    if chart_width <= 0:
        chart_width = max(1, int(chart_full[2] - chart_full[0]))
    if chart_height <= 0:
        chart_height = max(1, int(chart_full[3] - chart_full[1]))
    chart_space = [0.0, 0.0, float(chart_width), float(chart_height)]
    return chart_raw, chart_full, chart_space


def _plot_area_chart_bounds(chart_space: Sequence[float]) -> list[float]:
    width = max(1.0, float(chart_space[2] - chart_space[0]))
    height = max(1.0, float(chart_space[3] - chart_space[1]))
    left_pad = max(58.0, width * 0.060)
    top_pad = max(64.0, height * 0.115)
    right_pad = max(22.0, width * 0.020)
    bottom_pad = max(32.0, height * 0.045)
    return [
        chart_space[0] + left_pad,
        chart_space[1] + top_pad,
        chart_space[2] - right_pad,
        chart_space[3] - bottom_pad,
    ]


def _translate(box: Sequence[float], dx: float, dy: float) -> list[float]:
    return [float(box[0]) + dx, float(box[1]) + dy, float(box[2]) + dx, float(box[3]) + dy]


def _box_tuple(box: Sequence[Any]) -> tuple[float, float, float, float]:
    values = _bbox(box) or [0.0, 0.0, 0.0, 0.0]
    return float(values[0]), float(values[1]), float(values[2]), float(values[3])


@dataclass(frozen=True)
class BrokerSceneGraphV3:
    frame_id: int
    broker_surface_bounds: tuple[float, float, float, float]
    chart_region_bounds: tuple[float, float, float, float]
    chart_region_chart_bounds: tuple[float, float, float, float]
    plot_area_bounds: tuple[float, float, float, float]
    plot_area_chart_bounds: tuple[float, float, float, float]
    right_order_panel_bounds: tuple[float, float, float, float]
    top_asset_tabs_bounds: tuple[float, float, float, float]
    left_menu_bounds: tuple[float, float, float, float]
    price_axis_bounds: tuple[float, float, float, float]
    time_axis_bounds: tuple[float, float, float, float]
    valid: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BROKER_SCENE_GRAPH_SCHEMA_VERSION,
            "scene_graph": {
                "frame_id": self.frame_id,
                "broker_surface_bounds": list(self.broker_surface_bounds),
                "chart_region_bounds": list(self.chart_region_bounds),
                "chart_region_chart_bounds": list(self.chart_region_chart_bounds),
                "plot_area_bounds": list(self.plot_area_bounds),
                "plot_area_chart_bounds": list(self.plot_area_chart_bounds),
                "right_order_panel_bounds": list(self.right_order_panel_bounds),
                "top_asset_tabs_bounds": list(self.top_asset_tabs_bounds),
                "left_menu_bounds": list(self.left_menu_bounds),
                "price_axis_bounds": list(self.price_axis_bounds),
                "time_axis_bounds": list(self.time_axis_bounds),
                "valid": self.valid,
                "reason": self.reason,
                "rules": {
                    "market_overlays": "PLOT_AREA",
                    "broker_control_overlays": "RIGHT_ORDER_PANEL",
                    "asset_overlays": "TOP_ASSET_TABS",
                },
            },
        }


def build_broker_scene_graph_v3(
    session_payload: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Any] | None = None,
) -> BrokerSceneGraphV3:
    session = _mapping(session_payload)
    artifact_rows = _mapping(artifacts)
    frame_id = _int(session.get("frame_index") or session.get("capture_count"))
    surface_width, surface_height = _surface_size(session, artifact_rows)
    surface = [0.0, 0.0, float(surface_width), float(surface_height)]
    _chart_raw, chart_full, chart_space = _chart_region_bounds(session, artifact_rows, (surface_width, surface_height))
    chart_full = _clip_box(chart_full, surface) or [0.0, 0.0, float(surface_width), float(surface_height)]
    plot_chart = _plot_area_chart_bounds(chart_space)
    plot_chart = _clip_box(plot_chart, chart_space) or list(chart_space)
    plot_full = _translate(plot_chart, chart_full[0], chart_full[1])
    plot_full = _clip_box(plot_full, chart_full) or list(chart_full)
    right_panel = _execution_panel_bounds(session, surface, chart_full)
    top_height = max(42.0, plot_full[1] - chart_full[1])
    left_width = max(40.0, plot_full[0] - chart_full[0])
    top_tabs = [chart_full[0], chart_full[1], min(right_panel[0], chart_full[2]), min(chart_full[3], chart_full[1] + top_height)]
    left_menu = [chart_full[0], chart_full[1], min(chart_full[2], chart_full[0] + left_width), chart_full[3]]
    price_axis = [plot_full[2], plot_full[1], min(chart_full[2], plot_full[2] + max(12.0, chart_full[2] - plot_full[2])), plot_full[3]]
    time_axis = [plot_full[0], plot_full[3], plot_full[2], chart_full[3]]
    valid = bool(surface_width > 1 and surface_height > 1 and plot_full[2] > plot_full[0] and plot_full[3] > plot_full[1])
    return BrokerSceneGraphV3(
        frame_id=frame_id,
        broker_surface_bounds=_box_tuple(surface),
        chart_region_bounds=_box_tuple(chart_full),
        chart_region_chart_bounds=_box_tuple(chart_space),
        plot_area_bounds=_box_tuple(plot_full),
        plot_area_chart_bounds=_box_tuple(plot_chart),
        right_order_panel_bounds=_box_tuple(right_panel),
        top_asset_tabs_bounds=_box_tuple(top_tabs),
        left_menu_bounds=_box_tuple(left_menu),
        price_axis_bounds=_box_tuple(price_axis),
        time_axis_bounds=_box_tuple(time_axis),
        valid=valid,
        reason="broker surface and chart plot area locked" if valid else "scene graph could not lock broker surface and chart plot area",
    )


__all__ = [
    "BROKER_SCENE_GRAPH_SCHEMA_VERSION",
    "BrokerSceneGraphV3",
    "build_broker_scene_graph_v3",
]
