from __future__ import annotations
from pathlib import Path
from typing import Sequence, Mapping, Any, cast
from PIL import Image, ImageDraw, ImageFont
import io


def _ensure_image(path: Path | None, width: int = 800, height: int = 600) -> Image.Image:
    if path is None or not Path(path).exists():
        img = Image.new("RGBA", (width, height), (24, 24, 24, 255))
        return img
    try:
        img = Image.open(path).convert("RGBA")
        return img
    except Exception:
        return Image.new("RGBA", (width, height), (24, 24, 24, 255))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    parts = list(cast(Sequence[Any], value))
    if len(parts) < 4:
        return None
    values = [_float(item, float("nan")) for item in parts[:4]]
    if any(item != item for item in values):
        return None
    left, top, right, bottom = values
    return [min(left, right), min(top, bottom), max(left, right), max(top, bottom)]


def _scene_bbox(scene_graph: Mapping[str, Any] | None, key: str) -> list[float] | None:
    if not isinstance(scene_graph, Mapping):
        return None
    return _bbox(scene_graph.get(key))


def _scale_normalized(bounds: Sequence[float], target: Sequence[float]) -> list[float]:
    width = max(1.0, float(target[2]) - float(target[0]))
    height = max(1.0, float(target[3]) - float(target[1]))
    return [
        float(target[0]) + float(bounds[0]) * width,
        float(target[1]) + float(bounds[1]) * height,
        float(target[0]) + float(bounds[2]) * width,
        float(target[1]) + float(bounds[3]) * height,
    ]


def _translate_full_to_chart(bounds: Sequence[float], scene_graph: Mapping[str, Any] | None) -> list[float] | None:
    chart_full = _scene_bbox(scene_graph, "chart_region_bounds")
    if chart_full is None:
        return None
    return [
        float(bounds[0]) - chart_full[0],
        float(bounds[1]) - chart_full[1],
        float(bounds[2]) - chart_full[0],
        float(bounds[3]) - chart_full[1],
    ]


def _translate_chart_to_full(bounds: Sequence[float], scene_graph: Mapping[str, Any] | None) -> list[float] | None:
    chart_full = _scene_bbox(scene_graph, "chart_region_bounds")
    if chart_full is None:
        return None
    return [
        float(bounds[0]) + chart_full[0],
        float(bounds[1]) + chart_full[1],
        float(bounds[2]) + chart_full[0],
        float(bounds[3]) + chart_full[1],
    ]


def _bounds_for_target(
    overlay: Mapping[str, Any],
    bounds: Sequence[float],
    *,
    image_size: tuple[int, int],
    scene_graph: Mapping[str, Any] | None,
    target_space: str,
) -> list[float] | None:
    converted = [float(item) for item in bounds[:4]]
    coordinate_mode = str(overlay.get("coordinate_mode") or overlay.get("space") or "").upper()
    is_normalized = max(abs(item) for item in converted) <= 1.0001
    target = target_space.lower().strip()
    chart_bounds = _scene_bbox(scene_graph, "chart_region_chart_bounds") or [0.0, 0.0, float(image_size[0]), float(image_size[1])]
    plot_chart = _scene_bbox(scene_graph, "plot_area_chart_bounds") or chart_bounds
    broker_surface = _scene_bbox(scene_graph, "broker_surface_bounds") or [0.0, 0.0, float(image_size[0]), float(image_size[1])]
    plot_full = _scene_bbox(scene_graph, "plot_area_bounds") or broker_surface

    plot_scaled_to_full = False
    if coordinate_mode == "PLOT_AREA_NORMALIZED" and is_normalized:
        plot_scaled_to_full = target in {"full", "full_broker_surface", "window"}
        converted = _scale_normalized(converted, plot_full if target in {"full", "full_broker_surface", "window"} else plot_chart)
    elif coordinate_mode in {"CHART_NORMALIZED", "NORMALIZED"} and is_normalized:
        converted = _scale_normalized(converted, chart_bounds)
    elif coordinate_mode in {"FULL_BROKER_SURFACE", "WINDOW_SPACE"} and is_normalized:
        converted = _scale_normalized(converted, broker_surface)

    if target in {"chart", "chart_image_space"} and coordinate_mode in {"FULL_BROKER_SURFACE", "WINDOW_SPACE"}:
        converted = _translate_full_to_chart(converted, scene_graph) or []
        if not converted:
            return None
    if target in {"full", "full_broker_surface", "window"} and coordinate_mode not in {"FULL_BROKER_SURFACE", "WINDOW_SPACE"} and not plot_scaled_to_full:
        converted = _translate_chart_to_full(converted, scene_graph) or converted
    return _bbox(converted)


def _points_for_target(
    overlay: Mapping[str, Any],
    points: Sequence[Any],
    *,
    image_size: tuple[int, int],
    scene_graph: Mapping[str, Any] | None,
    target_space: str,
) -> list[tuple[int, int]]:
    converted: list[tuple[int, int]] = []
    for raw_point in points:
        if not isinstance(raw_point, Sequence) or isinstance(raw_point, (str, bytes, bytearray)):
            continue
        point_parts = list(cast(Sequence[object], raw_point))
        if len(point_parts) < 2:
            continue
        box = _bounds_for_target(
            overlay,
            [_float(point_parts[0]), _float(point_parts[1]), _float(point_parts[0]), _float(point_parts[1])],
            image_size=image_size,
            scene_graph=scene_graph,
            target_space=target_space,
        )
        if box is None:
            continue
        converted.append((int(round(box[0])), int(round(box[1]))))
    return converted


def render_overlays_on_chart(
    chart_path: Path | None,
    overlays: Sequence[Mapping[str, Any]],
    out_path: Path | None = None,
    *,
    scene_graph: Mapping[str, Any] | None = None,
    target_space: str = "chart",
) -> bytes:
    """Render overlays on top of a chart image and return PNG bytes. Optionally save to out_path."""
    img = _ensure_image(chart_path)
    image_size = (int(img.width), int(img.height))
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for ov in overlays:
        try:
            overlay_type = str(ov.get("type") or ov.get("overlay_type") or ov.get("kind") or "").upper()
            side = str(ov.get("side") or ov.get("direction") or "").upper()
            layer = str(ov.get("layer") or "").lower()
            display_state = str(ov.get("display_state") or "COMPACT").upper()
            label_hidden = (
                ov.get("label_hidden") is True
                or str(ov.get("label_hidden") or "").lower() == "true"
                or ov.get("label_visible") is False
                or display_state in {"GHOSTED", "ICON_ONLY", "INSPECTOR_LABEL", "INSPECTOR_ONLY_LABEL"}
            )
            if "DEMAND" in overlay_type or side == "BUY":
                color = (32, 212, 155, 150)
            elif "SUPPLY" in overlay_type or side == "SELL":
                color = (245, 139, 69, 150)
            elif "TRENDLINE" in overlay_type:
                color = (86, 211, 255, 150)
            elif layer == "historical_replay":
                color = (214, 169, 78, 115)
            else:
                color = (244, 201, 93, 135)
            line_points = ov.get("line_points") or ov.get("points")
            if isinstance(line_points, Sequence) and not isinstance(line_points, (str, bytes, bytearray)):
                points = _points_for_target(
                    ov,
                    cast(Sequence[Any], line_points),
                    image_size=image_size,
                    scene_graph=scene_graph,
                    target_space=target_space,
                )
                if len(points) >= 2:
                    draw.line(points, fill=color, width=2)
                    for point in points[:3]:
                        draw.ellipse([point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3], fill=color)
                    label_point = points[0]
                    label = str(ov.get("display_label") or ov.get("short_label") or ov.get("label") or overlay_type or "OVERLAY")
                    if font and label and not label_hidden:
                        draw.text((label_point[0] + 4, label_point[1] + 4), label, fill=(255, 255, 255, 210), font=font)
                    continue
            bbox = ov.get("bbox") or ov.get("bounds") or [0, 0, 0, 0]
            converted_bbox = _bounds_for_target(
                ov,
                cast(Sequence[Any], bbox),
                image_size=image_size,
                scene_graph=scene_graph,
                target_space=target_space,
            )
            if converted_bbox is None:
                continue
            x1, y1, x2, y2 = map(lambda value: int(round(float(value))), converted_bbox)
            if x2 <= x1 or y2 <= y1:
                continue
            draw.rectangle([x1, y1, x2, y2], outline=color, fill=None, width=2)
            label = str(ov.get("display_label") or ov.get("short_label") or ov.get("label") or ov.get("id") or ov.get("overlay_id") or "OVERLAY")
            text = label
            if label_hidden:
                continue
            if font:
                draw.text((x1 + 4, y1 + 4), text, fill=(255, 255, 255, 210), font=font)
            else:
                draw.text((x1 + 4, y1 + 4), text, fill=(255, 255, 255, 210))
        except Exception:
            continue
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    png = bio.getvalue()
    if out_path is not None:
        try:
            Path(out_path).write_bytes(png)
        except Exception:
            pass
    return png
