from __future__ import annotations

import pytest

from phoenixguard.mobile_api.window_tracker import PhoenixGuardWindowTrackingAdapter


def test_behavior_tokens_preserve_measured_body_and_wicks() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles = [
        {
            "track_id": 1,
            "bbox": [10, 30, 18, 70],
            "center_y": 50,
            "direction": "SELL",
            "body_height_pct": 0.55,
            "upper_wick_pct": 0.20,
            "lower_wick_pct": 0.25,
            "body_top_px": 38,
            "body_bottom_px": 60,
            "wick_top_px": 30,
            "wick_bottom_px": 70,
        },
        {
            "track_id": 2,
            "bbox": [20, 25, 28, 90],
            "center_y": 57.5,
            "direction": "BUY",
            "body_height_pct": 0.20,
            "upper_wick_pct": 0.10,
            "lower_wick_pct": 0.70,
            "body_top_px": 32,
            "body_bottom_px": 45,
            "wick_top_px": 25,
            "wick_bottom_px": 90,
        },
    ]

    build_tokens = getattr(adapter, "_build_candle_behavior_tokens")
    tokens = build_tokens(
        candles,
        {"fit_bounds": [0, 20, 100, 100], "zones": []},
        candidate_action="BUY",
    )

    latest = tokens[-1]
    assert latest["geometry_measured"] is True
    assert latest["body_pct"] == pytest.approx(0.20)
    assert latest["upper_wick_pct"] == pytest.approx(0.10)
    assert latest["lower_wick_pct"] == pytest.approx(0.70)
    assert latest["wick_to_body_ratio"] == pytest.approx(4.0)
    assert latest["wick_imbalance"] == pytest.approx(0.60)
    assert latest["micro_structure_event"] == "bullish_wick_sweep_reclaim"
