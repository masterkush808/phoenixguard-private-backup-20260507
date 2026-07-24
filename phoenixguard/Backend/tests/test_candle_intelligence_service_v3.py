from __future__ import annotations

import math
from typing import Any

import pytest

from phoenixguard.study.candle_intelligence_v3 import (
    CANDLE_INTELLIGENCE_SCHEMA_VERSION,
    CandleStudyValidationError,
    adapt_tracker_candle_v3,
    analyze_candle_sequence_v3,
    analyze_candle_v3,
)


def _price_candle(
    candle_id: str,
    *,
    open_value: float,
    high: float,
    low: float,
    close: float,
    timestamp: int,
    closed: bool = True,
) -> dict[str, Any]:
    return {
        "candle_id": candle_id,
        "timestamp": timestamp,
        "open": open_value,
        "high": high,
        "low": low,
        "close": close,
        "is_closed": closed,
    }


def test_candle_sequence_measures_exact_wicks_rejection_and_acceptance() -> None:
    rows = [
        _price_candle("c1", open_value=100.0, high=105.0, low=95.0, close=104.0, timestamp=1_000),
        _price_candle("c2", open_value=104.0, high=108.0, low=101.0, close=103.0, timestamp=1_300),
        _price_candle("c3", open_value=103.0, high=110.0, low=102.0, close=109.0, timestamp=1_600),
    ]

    study = analyze_candle_sequence_v3(rows, regime="TRENDING_UP")

    assert study["schema_version"] == CANDLE_INTELLIGENCE_SCHEMA_VERSION
    assert study["status"] == "STUDIED"
    assert study["execution_authority"] is False
    rejection = study["candles"][1]
    assert rejection["exact_geometry"] == {
        "range_size": 7.0,
        "body_size": 1.0,
        "upper_wick_size": 4.0,
        "lower_wick_size": 2.0,
    }
    assert math.isclose(float(rejection["ratios"]["upper_wick_to_range"]), 4.0 / 7.0, abs_tol=1e-6)
    assert rejection["interaction"]["rejection"]["upper_wick_swept_previous_high"] is True
    assert rejection["personality"] == "LIQUIDITY_REJECTION_HIGH"
    acceptance = study["candles"][2]
    assert acceptance["interaction"]["acceptance"]["closed_beyond_previous_high"] is True
    assert acceptance["personality"] == "BREAKOUT_ACCEPTANCE_UP"
    assert acceptance["sequence_position"]["is_latest"] is True
    assert len(study["sequence_signature"]) == 64


def test_active_tracker_adapter_requires_exact_closed_candle_proof() -> None:
    tracker_token = {
        "track_id": 22,
        "direction": "BUY",
        "open_proxy": 0.40,
        "high_proxy": 0.62,
        "low_proxy": 0.34,
        "close_proxy": 0.58,
        "body_top_px": 32.0,
        "body_bottom_px": 45.0,
        "wick_top_px": 25.0,
        "wick_bottom_px": 90.0,
    }
    with pytest.raises(CandleStudyValidationError, match="proven_closed"):
        adapt_tracker_candle_v3(
            tracker_token,
            closure_proof={"event_key": "event-22", "candle_id": "22"},
        )
    with pytest.raises(CandleStudyValidationError, match="does not identify"):
        adapt_tracker_candle_v3(
            tracker_token,
            closure_proof={"event_key": "event-22", "candle_id": "21", "proven_closed": True},
        )

    adapted = adapt_tracker_candle_v3(
        tracker_token,
        closure_proof={"event_key": "event-22", "candle_id": "22", "proven_closed": True},
    )
    result = analyze_candle_v3(adapted)
    assert adapted["closed_candle_identity"] == "event-22"
    assert result["coordinate_space"] == "NORMALIZED_PRICE_PROXY"
    assert result["direction"] == "BULLISH"
    assert math.isclose(float(result["exact_geometry"]["upper_wick_size"]), 0.04, abs_tol=1e-8)
    assert result["closed"] is True
    assert result["candle_id"] == "22"
    assert result["identity_stable"] is False
    assert result["stable_candle_identity"] == ""
    assert result["identity_source"] == "track_id"


def test_body_and_wick_tracker_geometry_is_adapted_without_inventing_ohlc() -> None:
    tracker_row = {
        "track_id": 7,
        "direction": "BUY",
        "body_top_px": 32.0,
        "body_bottom_px": 45.0,
        "wick_top_px": 25.0,
        "wick_bottom_px": 90.0,
    }
    adapted = adapt_tracker_candle_v3(
        tracker_row,
        closure_proof={"event_key": "closed-7", "track_id": "7", "proven_closed": True},
    )
    result = analyze_candle_v3(adapted)

    assert result["coordinate_space"] == "PIXEL_PRICE_PROXY"
    assert result["source_values"] == {
        "open_y_px": 45.0,
        "wick_top_px": 25.0,
        "wick_bottom_px": 90.0,
        "close_y_px": 32.0,
    }
    assert result["exact_geometry"]["body_size"] == 13.0
    assert result["exact_geometry"]["lower_wick_size"] == 45.0
    assert result["direction"] == "BULLISH"


def test_source_bar_timestamp_is_normalized_as_stable_identity_evidence() -> None:
    result = analyze_candle_v3(
        {
            "bar_open_time": 1_783_755_200,
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.8,
            "closed": True,
        }
    )

    assert result["candle_id"] == "1783755200"
    assert result["timestamp"] == 1_783_755_200
    assert result["identity_stable"] is True
    assert result["stable_candle_identity"] == "BAR_OPEN_TIME:1783755200"
    assert result["identity_source"] == "bar_open_time"


def test_forming_incomplete_and_contradictory_candles_fail_closed() -> None:
    forming = _price_candle(
        "forming",
        open_value=1.0,
        high=1.2,
        low=0.9,
        close=1.1,
        timestamp=1_000,
        closed=False,
    )
    with pytest.raises(CandleStudyValidationError, match="forming"):
        analyze_candle_v3(forming)
    incomplete = {"open": 1.0, "high": 1.2, "low": 0.9, "is_closed": True}
    with pytest.raises(CandleStudyValidationError, match="wick_top_px"):
        analyze_candle_v3(incomplete)
    contradictory = _price_candle(
        "bad",
        open_value=1.0,
        high=1.05,
        low=0.9,
        close=1.1,
        timestamp=1_000,
    )
    with pytest.raises(CandleStudyValidationError, match="below the candle body"):
        analyze_candle_v3(contradictory)


def test_sequence_rejects_mixed_price_and_pixel_coordinate_spaces() -> None:
    price = _price_candle(
        "price",
        open_value=1.0,
        high=1.2,
        low=0.9,
        close=1.1,
        timestamp=1_000,
    )
    pixel = adapt_tracker_candle_v3(
        {
            "track_id": 2,
            "direction": "BUY",
            "body_top_px": 30.0,
            "body_bottom_px": 40.0,
            "wick_top_px": 20.0,
            "wick_bottom_px": 50.0,
        },
        closure_proof={"event_key": "closed-2", "track_id": "2", "proven_closed": True},
    )

    with pytest.raises(CandleStudyValidationError, match="mix coordinate"):
        analyze_candle_sequence_v3([price, pixel])
