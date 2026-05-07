"""Strict candle geometry snapping to integer pixel boundaries with precise JSON mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast
import numpy as np


@dataclass(slots=True)
class SnappedCandleGeometry:
    """Precise integer-snapped candle coordinates for overlay rendering."""
    
    center_x: int
    body_x1: int
    body_x2: int
    body_y1: int
    body_y2: int
    wick_top: int
    wick_bottom: int
    
    # Precision metadata for JSON serialization
    confidence: float
    direction: str
    body_class: str
    pattern_family: str
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dictionary with explicit integer types."""
        return {
            "center_x": int(self.center_x),
            "body_bbox": [int(self.body_x1), int(self.body_y1), int(self.body_x2), int(self.body_y2)],
            "wick_top": int(self.wick_top),
            "wick_bottom": int(self.wick_bottom),
            "confidence": float(self.confidence),
            "direction": str(self.direction),
            "body_class": str(self.body_class),
            "pattern_family": str(self.pattern_family),
        }


def _snap_to_pixel(value: float) -> int:
    """Snap floating-point coordinate to nearest integer pixel with banker's rounding."""
    return int(np.round(float(value)))


def _clamp_coordinate(
    value: int,
    min_val: int,
    max_val: int,
) -> int:
    """Clamp integer coordinate to valid range."""
    return int(np.clip(value, min_val, max_val))


def _ensure_minimum_span(
    coord1: int,
    coord2: int,
    minimum: int = 1,
) -> tuple[int, int]:
    """Ensure minimum span between coordinates."""
    if coord2 <= coord1:
        coord2 = coord1 + minimum
    return coord1, coord2


def snap_candle_geometry(
    candle_data: Mapping[str, Any],
    chart_width: int,
    chart_height: int,
    chart_x_offset: int = 0,
    chart_y_offset: int = 0,
) -> SnappedCandleGeometry | None:
    """
    Snap floating-point candle coordinates to strict integer pixel boundaries.
    
    Parameters:
    -----------
    candle_data : Mapping[str, Any]
        Candle data with floating-point coordinates:
        - center_x: float center X coordinate
        - body_bbox: [float x1, float y1, float x2, float y2]
        - wick_top: float top wick Y coordinate
        - wick_bottom: float bottom wick Y coordinate
        - confidence: float confidence score
        - direction: str candle direction (BUY/SELL)
        - body_class: str body class (small/medium/expansion)
        - pattern_family: str pattern family name
        
    chart_width : int
        Chart canvas width in pixels
    chart_height : int
        Chart canvas height in pixels
    chart_x_offset : int
        X offset of chart within larger canvas
    chart_y_offset : int
        Y offset of chart within larger canvas
        
    Returns:
    --------
    SnappedCandleGeometry | None
        Snapped geometry or None if invalid/out of bounds
    """
    
    # Extract raw floating-point values
    try:
        center_x_raw = float(candle_data.get("center_x", 0.0) or 0.0)
        body_bbox_raw = cast(Sequence[Any], candle_data.get("body_bbox", [0.0, 0.0, 0.0, 0.0]))
        wick_top_raw = float(candle_data.get("wick_top", 0.0) or 0.0)
        wick_bottom_raw = float(candle_data.get("wick_bottom", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    
    # Extract bounding box coordinates
    if len(body_bbox_raw) < 4:
        return None
    
    try:
        body_x1_raw = float(body_bbox_raw[0])
        body_y1_raw = float(body_bbox_raw[1])
        body_x2_raw = float(body_bbox_raw[2])
        body_y2_raw = float(body_bbox_raw[3])
    except (TypeError, ValueError):
        return None
    
    # Snap all coordinates to pixel grid
    center_x = _snap_to_pixel(center_x_raw)
    body_x1 = _snap_to_pixel(body_x1_raw)
    body_x2 = _snap_to_pixel(body_x2_raw)
    body_y1 = _snap_to_pixel(body_y1_raw)
    body_y2 = _snap_to_pixel(body_y2_raw)
    wick_top = _snap_to_pixel(wick_top_raw)
    wick_bottom = _snap_to_pixel(wick_bottom_raw)
    
    # Clamp to chart bounds
    min_x = chart_x_offset
    max_x = chart_x_offset + chart_width - 1
    min_y = chart_y_offset
    max_y = chart_y_offset + chart_height - 1
    
    center_x = _clamp_coordinate(center_x, min_x, max_x)
    body_x1 = _clamp_coordinate(body_x1, min_x, max_x - 3)
    body_x2 = _clamp_coordinate(body_x2, body_x1 + 3, max_x)
    body_y1 = _clamp_coordinate(body_y1, min_y, max_y - 3)
    body_y2 = _clamp_coordinate(body_y2, body_y1 + 3, max_y)
    wick_top = _clamp_coordinate(wick_top, min_y, body_y1)
    wick_bottom = _clamp_coordinate(wick_bottom, body_y2, max_y)
    
    # Ensure minimum spans
    body_x1, body_x2 = _ensure_minimum_span(body_x1, body_x2, minimum=3)
    body_y1, body_y2 = _ensure_minimum_span(body_y1, body_y2, minimum=3)
    
    # Validate center_x is within body bounds
    body_left = min(body_x1, body_x2)
    body_right = max(body_x1, body_x2)
    center_x = _clamp_coordinate(center_x, body_left, body_right)
    
    # Extract metadata
    try:
        confidence = float(np.clip(candle_data.get("confidence", 0.0) or 0.0, 0.0, 1.0))
        direction = str(candle_data.get("direction", "HOLD") or "HOLD").strip().upper()
        body_class = str(candle_data.get("body_class", "medium") or "medium").strip().lower()
        pattern_family = str(candle_data.get("pattern_family", "") or "").strip()
    except (TypeError, ValueError):
        return None
    
    return SnappedCandleGeometry(
        center_x=center_x,
        body_x1=int(body_x1),
        body_x2=int(body_x2),
        body_y1=int(body_y1),
        body_y2=int(body_y2),
        wick_top=int(wick_top),
        wick_bottom=int(wick_bottom),
        confidence=confidence,
        direction=direction,
        body_class=body_class,
        pattern_family=pattern_family,
    )


def snap_candle_collection(
    candles: Sequence[Mapping[str, Any]],
    chart_width: int,
    chart_height: int,
    chart_x_offset: int = 0,
    chart_y_offset: int = 0,
) -> list[SnappedCandleGeometry]:
    """Snap a collection of candles to integer pixel grid."""
    snapped = []
    for candle in candles:
        geometry = snap_candle_geometry(
            candle,
            chart_width=chart_width,
            chart_height=chart_height,
            chart_x_offset=chart_x_offset,
            chart_y_offset=chart_y_offset,
        )
        if geometry is not None:
            snapped.append(geometry)
    return snapped


def validate_candle_overlap(
    candle1: SnappedCandleGeometry,
    candle2: SnappedCandleGeometry,
    min_separation: int = 2,
) -> bool:
    """Check if two candles have acceptable separation (not overlapping)."""
    left1, right1 = candle1.body_x1, candle1.body_x2
    left2, right2 = candle2.body_x1, candle2.body_x2
    
    if right1 < left2:
        return (left2 - right1) >= min_separation
    elif right2 < left1:
        return (left1 - right2) >= min_separation
    else:
        return False


def compute_candle_metrics(geometry: SnappedCandleGeometry) -> dict[str, Any]:
    """Compute metrics from snapped geometry for validation."""
    body_width = geometry.body_x2 - geometry.body_x1
    body_height = geometry.body_y2 - geometry.body_y1
    wick_height_top = geometry.body_y1 - geometry.wick_top
    wick_height_bottom = geometry.wick_bottom - geometry.body_y2
    total_height = geometry.wick_bottom - geometry.wick_top
    
    return {
        "body_width": int(body_width),
        "body_height": int(body_height),
        "wick_top_height": int(wick_height_top),
        "wick_bottom_height": int(wick_height_bottom),
        "total_height": int(total_height),
        "aspect_ratio": float(body_width / max(body_height, 1)),
    }
