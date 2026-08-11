from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from phoenixguard.vision.candle_palette_v3 import (
    build_candle_palette_masks,
    extract_candle_tracks_adaptive_v3,
    extract_candle_tracks_v3,
)


COLORS: dict[str, tuple[int, int, int]] = {
    "green": (42, 190, 72),
    "blue": (45, 100, 230),
    "red": (224, 58, 42),
    "orange": (235, 115, 30),
    "magenta": (225, 45, 180),
}


def _synthetic_chart(
    *,
    buy_palette: str,
    sell_palette: str,
    count: int = 24,
) -> tuple[NDArray[np.uint8], list[str], list[float]]:
    image = np.full((260, 360, 3), 18, dtype=np.uint8)
    previous_close = 150
    directions: list[str] = []
    centers: list[float] = []
    for index in range(count):
        side = "BUY" if index % 3 != 1 else "SELL"
        open_y = previous_close
        close_y = open_y - 8 if side == "BUY" else open_y + 10
        center_x = 30 + index * 11
        palette = buy_palette if side == "BUY" else sell_palette
        color = COLORS[palette]
        body_top, body_bottom = sorted((open_y, close_y))
        image[max(0, body_top - 4) : min(image.shape[0], body_bottom + 5), center_x] = color
        image[body_top : body_bottom + 1, center_x - 3 : center_x + 4] = color
        directions.append(side)
        centers.append(float(center_x))
        previous_close = close_y

    # Same-color chart annotations must not turn into candles. These one-pixel
    # dashes are intentionally positioned at a different horizontal cadence.
    for start in range(4, image.shape[1] - 4, 14):
        image[80, start : start + 7] = COLORS["red"]
    return image, directions, centers


def test_palette_masks_keep_all_supported_themes_separate() -> None:
    pixels = np.asarray([[COLORS[name] for name in COLORS]], dtype=np.uint8)

    masks = build_candle_palette_masks(pixels)

    assert set(masks) == set(COLORS)
    for index, palette in enumerate(COLORS):
        assert bool(masks[palette][0, index]) is True
        assert sum(bool(mask[0, index]) for mask in masks.values()) == 1


@pytest.mark.parametrize(
    ("buy_palette", "sell_palette"),
    [
        ("green", "red"),
        ("blue", "red"),
        ("blue", "green"),
        ("orange", "magenta"),
    ],
)
def test_extractor_recovers_palette_semantics_from_continuity(
    buy_palette: str,
    sell_palette: str,
) -> None:
    image, expected_directions, expected_centers = _synthetic_chart(
        buy_palette=buy_palette,
        sell_palette=sell_palette,
    )

    tracks = extract_candle_tracks_v3(
        image,
        roi_bounds=(5, 10, 350, 250),
        minimum_track_length=6,
    )

    assert [row["direction"] for row in tracks] == expected_directions
    assert [row["center_x_px"] for row in tracks] == expected_centers
    assert {row["palette"] for row in tracks} == {buy_palette, sell_palette}
    assert all(float(row["wick_top_px"]) < float(row["wick_bottom_px"]) for row in tracks)
    assert all(float(row["body_top_px"]) < float(row["body_bottom_px"]) for row in tracks)
    assert all(float(row["open_y_px"]) != float(row["close_y_px"]) for row in tracks)
    assert all(0.0 <= float(row["price_proxy"]) <= 1.0 for row in tracks)
    assert all(float(row["parse_confidence"]) >= 0.70 for row in tracks)
    assert all(not (float(row["bbox"][1]) <= 80 <= float(row["bbox"][3])) for row in tracks)


def test_extractor_returns_global_coordinates_for_a_bounded_roi() -> None:
    chart, expected_directions, expected_centers = _synthetic_chart(
        buy_palette="green",
        sell_palette="red",
        count=12,
    )
    canvas = np.zeros((320, 460, 3), dtype=np.uint8)
    canvas[30:290, 50:410] = chart

    tracks = extract_candle_tracks_v3(
        canvas,
        roi_bounds=(50, 30, 410, 290),
        minimum_track_length=6,
    )

    assert [row["direction"] for row in tracks] == expected_directions
    assert [row["center_x_px"] for row in tracks] == [value + 50.0 for value in expected_centers]
    assert all(float(row["bbox"][1]) >= 30.0 for row in tracks)


@pytest.mark.parametrize(
    "invalid",
    [
        np.zeros((10, 10), dtype=np.uint8),
        np.zeros((2, 10, 3), dtype=np.uint8),
        np.zeros((10, 2, 3), dtype=np.uint8),
    ],
)
def test_extractor_fails_closed_for_non_image_arrays(invalid: NDArray[np.uint8]) -> None:
    assert extract_candle_tracks_v3(invalid) == []


def _all_increasing(values: Sequence[float]) -> bool:
    return all(left < right for left, right in zip(values, values[1:]))


def test_track_ids_and_x_coordinates_are_strictly_ordered() -> None:
    image, _directions, _centers = _synthetic_chart(buy_palette="blue", sell_palette="red")

    tracks = extract_candle_tracks_v3(image)

    assert [row["track_id"] for row in tracks] == list(range(len(tracks)))
    assert _all_increasing([float(row["center_x_px"]) for row in tracks])


def test_causal_track_bridges_five_temporarily_hidden_candles() -> None:
    image, _directions, expected_centers = _synthetic_chart(
        buy_palette="green",
        sell_palette="magenta",
        count=30,
    )
    # Simulate an expiry marker or chart annotation hiding five complete candle
    # bodies between a long historical run and the still-visible current edge.
    for center in expected_centers[20:25]:
        x = int(center)
        image[:, x - 4 : x + 5] = 18

    tracks = extract_candle_tracks_v3(
        image,
        roi_bounds=(5, 10, 355, 250),
        minimum_track_length=6,
    )
    centers = [float(row["center_x_px"]) for row in tracks]

    assert centers[:20] == expected_centers[:20]
    assert centers[-5:] == expected_centers[25:]
    assert len(centers) == 25


def test_palette_hypotheses_reject_a_larger_incoherent_blue_ui_lattice() -> None:
    chart, expected_directions, expected_centers = _synthetic_chart(
        buy_palette="green",
        sell_palette="magenta",
    )
    # Broker chrome/grid strokes are much more numerous and more regularly
    # spaced than the candles, but their y sequence is not OHLC-continuous.
    ui_y_positions = (15, 35, 55, 75)
    for index, x in enumerate(range(8, 352, 4)):
        y = ui_y_positions[index % len(ui_y_positions)]
        chart[y : y + 7, x] = COLORS["blue"]

    tracks = extract_candle_tracks_v3(
        chart,
        roi_bounds=(0, 0, 360, 250),
        minimum_track_length=6,
    )

    assert [row["direction"] for row in tracks] == expected_directions
    assert [row["center_x_px"] for row in tracks] == expected_centers
    assert {row["palette"] for row in tracks} == {"green", "magenta"}


@pytest.mark.parametrize("sell_palette", ["red", "green"])
def test_coherent_one_pixel_blue_candle_suites_remain_valid(sell_palette: str) -> None:
    image = np.full((180, 300, 3), 18, dtype=np.uint8)
    previous_close = 100
    expected_directions: list[str] = []
    for index in range(20):
        side = "BUY" if index % 3 != 1 else "SELL"
        close_y = previous_close - 5 if side == "BUY" else previous_close + 7
        x = 25 + index * 11
        palette = "blue" if side == "BUY" else sell_palette
        image[min(previous_close, close_y) : max(previous_close, close_y) + 1, x] = COLORS[palette]
        expected_directions.append(side)
        previous_close = close_y

    tracks = extract_candle_tracks_v3(image, minimum_track_length=6)

    assert [row["direction"] for row in tracks] == expected_directions
    assert {row["palette"] for row in tracks} == {"blue", sell_palette}
    assert all(float(row["bbox"][2]) - float(row["bbox"][0]) == 0.0 for row in tracks)


def test_adaptive_extractor_rejects_regular_colored_footer_controls() -> None:
    chart, expected_directions, expected_centers = _synthetic_chart(
        buy_palette="blue",
        sell_palette="green",
    )
    canvas = np.full((420, 460, 3), 18, dtype=np.uint8)
    canvas[:260, :360] = chart
    # Dense blue/orange footer glyphs deliberately form a longer two-pixel
    # lattice than the real candles, but they have no coherent OHLC path.
    for x in range(18, 430, 3):
        palette = "blue" if x % 2 else "orange"
        y = 320 + (x % 5) * 10
        canvas[y : y + 8, x] = COLORS[palette]

    tracks = extract_candle_tracks_adaptive_v3(
        canvas,
        x_bounds=(0.0, 1.0),
        top_ratio=0.0,
        minimum_track_length=6,
    )

    assert [row["direction"] for row in tracks] == expected_directions
    assert [row["center_x_px"] for row in tracks] == expected_centers
