from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Sequence, cast
import hashlib
import time


@dataclass
class V3ChartTransform:
    chart_transform_id: str
    frame_id: int
    screen_bounds: list[float]
    window_bounds: list[float]
    chart_image_bounds: list[float]
    plot_area_bounds: list[float]
    price_axis_bounds: list[float]
    time_axis_bounds: list[float]
    valid: bool
    reason: str

    @staticmethod
    def create(chart_size: Sequence[float], frame_id: int | None = None) -> "V3ChartTransform":
        width = float(chart_size[0]) if len(chart_size) >= 1 else 1.0
        height = float(chart_size[1]) if len(chart_size) >= 2 else 1.0
        w = max(1.0, width)
        h = max(1.0, height)
        chart_image_bounds = [0.0, 0.0, w, h]
        # For now window_bounds and screen_bounds equal chart_image_bounds (no DPI info)
        screen_bounds = list(chart_image_bounds)
        window_bounds = list(chart_image_bounds)
        # Assume plot area excludes small margins for price axis and time axis
        price_axis_w = min(80.0, max(32.0, w * 0.06))
        time_axis_h = min(36.0, max(12.0, h * 0.06))
        plot_area_bounds = [0.0, 0.0, max(1.0, w - price_axis_w), max(1.0, h - time_axis_h)]
        price_axis_bounds = [plot_area_bounds[2], 0.0, w, h - time_axis_h]
        time_axis_bounds = [0.0, plot_area_bounds[3], w, h]
        ts = int(time.time()) if frame_id is None else int(frame_id)
        # id deterministically from sizes and timestamp
        seed = f"{w}x{h}-{ts}"
        tid = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return V3ChartTransform(
            chart_transform_id=f"ct_{tid}",
            frame_id=ts,
            screen_bounds=screen_bounds,
            window_bounds=window_bounds,
            chart_image_bounds=chart_image_bounds,
            plot_area_bounds=plot_area_bounds,
            price_axis_bounds=price_axis_bounds,
            time_axis_bounds=time_axis_bounds,
            valid=True,
            reason="defaulted",
        )

    def normalized_to_chart_image(self, bbox_norm: Sequence[float]) -> list[int]:
        # bbox_norm assumed [x0,y0,x1,y1] in 0..1 relative to chart_image_bounds
        if not bbox_norm or len(bbox_norm) < 4:
            return [0, 0, 0, 0]
        w = max(1.0, float(self.chart_image_bounds[2] - self.chart_image_bounds[0]))
        h = max(1.0, float(self.chart_image_bounds[3] - self.chart_image_bounds[1]))
        x0 = int(round(float(bbox_norm[0]) * w + self.chart_image_bounds[0]))
        y0 = int(round(float(bbox_norm[1]) * h + self.chart_image_bounds[1]))
        x1 = int(round(float(bbox_norm[2]) * w + self.chart_image_bounds[0]))
        y1 = int(round(float(bbox_norm[3]) * h + self.chart_image_bounds[1]))
        return [x0, y0, x1, y1]

    def chart_image_to_screen(self, bbox_px: Sequence[float]) -> list[int]:
        # identity mapping currently
        if not bbox_px or len(bbox_px) < 4:
            return [0, 0, 0, 0]
        return [int(round(float(v))) for v in bbox_px]

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))
