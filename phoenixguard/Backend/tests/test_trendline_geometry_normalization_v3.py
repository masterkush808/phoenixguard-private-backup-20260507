from __future__ import annotations

from typing import Any

from phoenixguard.tracking.trendline_geometry_v3 import (
    normalize_trendline_overlays_v3,
)


def _candles(*, bottoms: dict[int, float] | None = None) -> list[dict[str, Any]]:
    selected_bottoms = bottoms or {}
    rows: list[dict[str, Any]] = []
    for center in range(10, 111, 10):
        bottom = selected_bottoms.get(center, 90.0)
        top = min(20.0, bottom - 5.0)
        rows.append({"bbox": [center - 2.0, top, center + 2.0, bottom]})
    return rows


def test_off_canvas_projection_is_omitted_without_fabricating_a_border_intersection() -> None:
    normalized = normalize_trendline_overlays_v3(
        [
            {
                "direction": "BUY",
                "anchor_wick_points": [[10.0, 80.0], [50.0, 20.0]],
                "touch_points": [[10.0, 80.0], [50.0, 20.0]],
                "line_points": [[10.0, 80.0], [50.0, 20.0], [108.0, -67.0]],
                "points": [[10.0, 80.0], [50.0, 20.0], [108.0, -67.0]],
                "bounds": [10.0, -67.0, 108.0, 80.0],
            }
        ],
        _candles(bottoms={10: 80.0, 50: 20.0}),
    )[0]

    assert normalized["anchor_wick_points"] == [[10.0, 80.0], [50.0, 20.0]]
    assert normalized["line_points"] == [[10.0, 80.0], [50.0, 20.0]]
    assert normalized["geometry_status"] == "ANCHORS_VALID_EXTENSION_OUTSIDE_CHART"
    assert normalized["current_projection_visible"] is False
    assert normalized["extension_clipped"] is False
    assert normalized["extension_omitted"] is True
    assert normalized["geometry_contract_accepted"] is True


def test_visible_projection_reaches_latest_chart_x() -> None:
    normalized = normalize_trendline_overlays_v3(
        [
            {
                "direction": "BUY",
                "anchor_wick_points": [[10.0, 70.0], [50.0, 60.0]],
                "touch_points": [[10.0, 70.0], [50.0, 60.0]],
                "line_points": [[10.0, 70.0], [50.0, 60.0], [108.0, 45.5]],
                "bounds": [10.0, 45.5, 108.0, 70.0],
            }
        ],
        _candles(bottoms={10: 70.0, 50: 60.0}),
    )[0]

    assert normalized["current_projection_visible"] is True
    assert normalized["extension_clipped"] is False
    assert normalized["extension_omitted"] is False
    assert normalized["line_points"][-1][0] == 112.0


def test_anchor_outside_the_current_candle_domain_is_not_published() -> None:
    normalized = normalize_trendline_overlays_v3(
        [
            {
                "direction": "SELL",
                "anchor_wick_points": [[-10.0, 20.0], [50.0, 20.0]],
                "touch_points": [[-10.0, 20.0], [50.0, 20.0]],
                "line_points": [[-10.0, 20.0], [50.0, 20.0]],
            }
        ],
        _candles(),
    )

    assert normalized == []


def test_anchor_without_exact_closed_candle_wick_proof_is_not_published() -> None:
    normalized = normalize_trendline_overlays_v3(
        [
            {
                "direction": "BUY",
                "anchor_wick_points": [[10.0, 75.0], [50.0, 60.0]],
                "touch_points": [[10.0, 75.0], [50.0, 60.0]],
                "line_points": [[10.0, 75.0], [50.0, 60.0]],
            }
        ],
        _candles(bottoms={10: 80.0, 50: 60.0}),
    )

    assert normalized == []


def test_forming_candle_cannot_be_a_canonical_anchor() -> None:
    normalized = normalize_trendline_overlays_v3(
        [
            {
                "direction": "BUY",
                "anchor_wick_points": [[50.0, 60.0], [110.0, 70.0]],
                "touch_points": [[50.0, 60.0], [110.0, 70.0]],
                "line_points": [[50.0, 60.0], [110.0, 70.0]],
            }
        ],
        _candles(bottoms={50: 60.0, 110: 70.0}),
    )

    assert normalized == []
