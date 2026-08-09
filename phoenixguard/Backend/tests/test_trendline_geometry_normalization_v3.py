from __future__ import annotations

from phoenixguard.tracking.trendline_geometry_v3 import (
    normalize_trendline_overlays_v3,
)


def _candles() -> list[dict]:
    return [
        {"bbox": [float(index * 10), 0.0, float(index * 10 + 8), 100.0]}
        for index in range(11)
    ]


def test_off_canvas_trendline_extension_is_clipped_without_moving_wick_anchors() -> None:
    normalized = normalize_trendline_overlays_v3(
        [
            {
                "anchor_wick_points": [[10.0, 80.0], [50.0, 20.0]],
                "line_points": [[10.0, 80.0], [50.0, 20.0], [108.0, -67.0]],
                "points": [[10.0, 80.0], [50.0, 20.0], [108.0, -67.0]],
                "bounds": [10.0, -67.0, 108.0, 80.0],
            }
        ],
        _candles(),
    )[0]

    assert normalized["anchor_wick_points"] == [[10.0, 80.0], [50.0, 20.0]]
    assert normalized["line_points"][:2] == [[10.0, 80.0], [50.0, 20.0]]
    assert normalized["line_points"][-1][1] == 0.0
    assert normalized["bounds"][1] >= 0.0
    assert normalized["current_projection_visible"] is False
    assert normalized["extension_clipped"] is True


def test_visible_projection_reaches_latest_chart_x() -> None:
    normalized = normalize_trendline_overlays_v3(
        [
            {
                "anchor_wick_points": [[10.0, 70.0], [50.0, 60.0]],
                "line_points": [[10.0, 70.0], [50.0, 60.0], [108.0, 45.5]],
                "bounds": [10.0, 45.5, 108.0, 70.0],
            }
        ],
        _candles(),
    )[0]

    assert normalized["current_projection_visible"] is True
    assert normalized["extension_clipped"] is False
    assert normalized["line_points"][-1][0] == 108.0
