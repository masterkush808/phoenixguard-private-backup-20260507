from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from phoenixguard.decision import lstm_candle_sequence_contributor_v3 as contributor


_coherent_ohlc_from_close = cast(
    Callable[..., tuple[float, float, float]],
    getattr(contributor, "_coherent_ohlc_from_close"),
)


def _approx(expected: object, **kwargs: float) -> object:
    return cast(Callable[..., object], getattr(pytest, "approx"))(expected, **kwargs)


@pytest.mark.parametrize(
    ("body_direction", "expected_open"),
    [
        ("BUY", 0.54),
        ("SELL", 0.66),
    ],
)
def test_coherent_ohlc_allocates_total_range_across_predicted_shape(
    body_direction: str,
    expected_open: float,
) -> None:
    open_location, high_location, low_location = _coherent_ohlc_from_close(
        close_location=0.60,
        candle_range=0.12,
        body_ratio=0.50,
        upper_wick_ratio=0.20,
        lower_wick_ratio=0.30,
        body_direction=body_direction,
    )

    assert open_location == _approx(expected_open)
    assert abs(0.60 - open_location) == _approx(0.06)
    assert high_location - low_location == _approx(0.12)
    assert high_location >= max(open_location, 0.60)
    assert low_location <= min(open_location, 0.60)


def test_coherent_ohlc_never_moves_authoritative_close_at_chart_boundary() -> None:
    open_location, high_location, low_location = _coherent_ohlc_from_close(
        close_location=0.98,
        candle_range=0.20,
        body_ratio=0.60,
        upper_wick_ratio=0.20,
        lower_wick_ratio=0.20,
        body_direction="BUY",
    )

    assert open_location == _approx(0.86)
    assert high_location == 1.0
    assert low_location == _approx(0.82)
    assert low_location <= open_location <= 0.98 <= high_location


def test_coherent_ohlc_zero_shape_falls_back_to_directional_body() -> None:
    open_location, high_location, low_location = _coherent_ohlc_from_close(
        close_location=0.50,
        candle_range=0.08,
        body_ratio=0.0,
        upper_wick_ratio=0.0,
        lower_wick_ratio=0.0,
        body_direction="SELL",
    )

    assert open_location == _approx(0.58)
    assert high_location == _approx(0.58)
    assert low_location == _approx(0.50)
