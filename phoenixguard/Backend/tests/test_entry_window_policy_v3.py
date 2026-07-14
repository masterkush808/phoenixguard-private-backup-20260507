from __future__ import annotations

import pytest

from phoenixguard.decision.entry_window_policy_v3 import (
    MAXIMUM_ENTRY_WINDOW_SECONDS,
    MINIMUM_ENTRY_WINDOW_SECONDS,
    entry_location_guidance_v3,
    resolve_entry_window_policy_v3,
)


@pytest.mark.parametrize(
    ("timeframe_seconds", "remaining_seconds", "expected_duration_seconds"),
    (
        (60, 0, 600),
        (60, 60, 660),
        (180, 120, 720),
        (300, 240, 840),
        (300, 300, 900),
        (900, 1, 900),
    ),
)
def test_entry_window_is_chart_aware_inside_professional_ten_to_fifteen_minute_band(
    timeframe_seconds: int,
    remaining_seconds: int,
    expected_duration_seconds: int,
) -> None:
    policy = resolve_entry_window_policy_v3(
        timeframe_seconds=timeframe_seconds,
        opening_candle_remaining_seconds=remaining_seconds,
        trade_expiry_reference_seconds=30,
    )

    assert policy["duration_sec"] == expected_duration_seconds
    assert MINIMUM_ENTRY_WINDOW_SECONDS <= policy["duration_sec"] <= MAXIMUM_ENTRY_WINDOW_SECONDS
    assert policy["trade_expiry_reference_sec"] == 30
    assert policy["closes_early_on"]


def test_short_trade_expiry_does_not_collapse_the_setup_window_to_seconds() -> None:
    short_expiry = resolve_entry_window_policy_v3(
        timeframe_seconds=60,
        opening_candle_remaining_seconds=41,
        trade_expiry_reference_seconds=30,
    )
    long_expiry = resolve_entry_window_policy_v3(
        timeframe_seconds=60,
        opening_candle_remaining_seconds=41,
        trade_expiry_reference_seconds=1_080,
    )

    assert short_expiry["duration_sec"] == 641
    assert long_expiry["duration_sec"] == short_expiry["duration_sec"]


def test_entry_location_guidance_is_qualified_by_verified_zone() -> None:
    buy = entry_location_guidance_v3("buy")
    sell = entry_location_guidance_v3("SELL")

    assert buy["rule"] == "BUY_LOW"
    assert buy["preferred_price_location"] == "LOWER_PRICE"
    assert "verified demand or retest area" in buy["message"]
    assert "do not chase highs" in buy["message"]
    assert sell["rule"] == "SELL_HIGH"
    assert sell["preferred_price_location"] == "HIGHER_PRICE"
    assert "verified supply or retest area" in sell["message"]
    assert "do not chase lows" in sell["message"]
