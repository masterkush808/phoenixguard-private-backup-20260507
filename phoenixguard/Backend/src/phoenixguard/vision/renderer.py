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


def render_overlays_on_chart(chart_path: Path | None, overlays: Sequence[Mapping[str, Any]], out_path: Path | None = None) -> bytes:
    """Render overlays on top of a chart image and return PNG bytes. Optionally save to out_path."""
    img = _ensure_image(chart_path)
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
                or display_state in {"GHOSTED", "ICON_ONLY", "INSPECTOR_ONLY_LABEL"}
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
                points: list[tuple[int, int]] = []
                for raw_point in cast(Sequence[object], line_points):
                    if not isinstance(raw_point, Sequence) or isinstance(raw_point, (str, bytes, bytearray)):
                        continue
                    point_parts = list(cast(Sequence[object], raw_point))
                    if len(point_parts) < 2:
                        continue
                    points.append((int(round(float(str(point_parts[0])))), int(round(float(str(point_parts[1]))))))
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
            x1, y1, x2, y2 = map(lambda value: int(round(float(value))), bbox)
            if x2 <= x1 or y2 <= y1:
                continue
            fill = (color[0], color[1], color[2], 8)
            draw.rectangle([x1, y1, x2, y2], outline=color, fill=fill, width=2)
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
