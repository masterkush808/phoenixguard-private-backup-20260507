from __future__ import annotations
from pathlib import Path
from typing import Sequence, Mapping, Any
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
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for ov in overlays:
        try:
            bbox = ov.get("bbox") or [0, 0, 0, 0]
            x1, y1, x2, y2 = map(int, bbox)
            color = (255, 0, 0, 160) if float(ov.get("truth_score") or 0.0) >= 0.7 else (255, 200, 0, 140)
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
            label = str(ov.get("id") or ov.get("overlay_id") or "overlay")
            text = f"{label} {ov.get('truth_score', ''):.2f}" if isinstance(ov.get('truth_score'), (int, float)) else label
            if font:
                draw.text((x1 + 4, y1 + 4), text, fill=(255, 255, 255, 230), font=font)
            else:
                draw.text((x1 + 4, y1 + 4), text, fill=(255, 255, 255, 230))
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
