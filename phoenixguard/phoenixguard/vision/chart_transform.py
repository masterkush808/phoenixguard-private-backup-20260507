from __future__ import annotations
from typing import Sequence


def normalized_to_pixel(bbox_norm: Sequence[float], chart_size: Sequence[int]) -> list[int]:
    if not bbox_norm or len(bbox_norm) < 4:
        return [0, 0, 0, 0]
    w = max(1, int(chart_size[0]))
    h = max(1, int(chart_size[1]))
    x0 = int(round(float(bbox_norm[0]) * w))
    y0 = int(round(float(bbox_norm[1]) * h))
    x1 = int(round(float(bbox_norm[2]) * w))
    y1 = int(round(float(bbox_norm[3]) * h))
    return [x0, y0, x1, y1]


def pixel_to_normalized(bbox_px: Sequence[float], chart_size: Sequence[int]) -> list[float]:
    if not bbox_px or len(bbox_px) < 4:
        return [0.0, 0.0, 0.0, 0.0]
    w = max(1, float(chart_size[0]))
    h = max(1, float(chart_size[1]))
    x0 = float(bbox_px[0]) / w
    y0 = float(bbox_px[1]) / h
    x1 = float(bbox_px[2]) / w
    y1 = float(bbox_px[3]) / h
    return [x0, y0, x1, y1]
