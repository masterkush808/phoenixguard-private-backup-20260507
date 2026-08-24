from __future__ import annotations

import math
from typing import Any

from phoenixguard.mobile_api import window_tracker
from phoenixguard.study.latent_state_discovery_v3 import (
    build_latent_state_discovery_v3,
)

_candles_to_seconds = getattr(window_tracker, "_candles_to_seconds")
_canonical_timeframe_label_v3 = getattr(
    window_tracker, "_canonical_timeframe_label_v3"
)
_identity_text_timeframe_candidates_v3 = getattr(
    window_tracker, "_identity_text_timeframe_candidates_v3"
)
_reconcile_latent_state_control_v3 = getattr(
    window_tracker, "_reconcile_latent_state_control_v3"
)
_strict_live_trendline_geometry_v3 = getattr(
    window_tracker, "_strict_live_trendline_geometry_v3"
)
_timeframe_seconds = getattr(window_tracker, "_timeframe_seconds")


def test_timeframe_identity_and_math_are_not_m5_specific() -> None:
    assert _identity_text_timeframe_candidates_v3("EUR/JPY OTC 4H") == ["H4"]
    assert _identity_text_timeframe_candidates_v3("chart H04") == ["H4"]
    assert _identity_text_timeframe_candidates_v3("240 minutes") == ["H4"]
    assert _identity_text_timeframe_candidates_v3("1 week") == ["W1"]
    assert _identity_text_timeframe_candidates_v3("1 month") == ["MN1"]
    assert _canonical_timeframe_label_v3("6 hour") == "H6"
    assert _timeframe_seconds("H4") == 14_400
    assert _candles_to_seconds(23, "H4") == 331_200


def test_latent_state_binds_nested_ohlc_and_scales_h4_cycle_duration() -> None:
    candles = [
        {
            "ohlc": {
                "open": 100.0 + index,
                "high": 102.0 + index,
                "low": 99.0 + index,
                "close": 101.0 + index,
            }
        }
        for index in range(8)
    ]
    pair_profile = {
        "transition_counts": {
            "PIXEL_PRICE_PROXY|REST->UP_SWING": 8,
            "PIXEL_PRICE_PROXY|REST->DOWN_SWING": 2,
        },
        "segment_averages": {
            "PIXEL_PRICE_PROXY|REST": {
                "count": 5,
                "candle_count": 1.0,
                "path_efficiency": 0.2,
            },
            "PIXEL_PRICE_PROXY|UP_SWING": {
                "count": 8,
                "candle_count": 23.0,
                "path_efficiency": 0.82,
            },
            "PIXEL_PRICE_PROXY|DOWN_SWING": {
                "count": 6,
                "candle_count": 17.0,
                "path_efficiency": 0.74,
            },
        },
    }
    result = build_latent_state_discovery_v3(
        candles=candles,
        behavior={
            "current_state": "REST",
            "coordinate_space": "PIXEL_PRICE_PROXY",
            "segments": [
                {
                    "state": "DOWN_SWING",
                    "candle_count": 4,
                    "path_efficiency": 0.7,
                },
                {
                    "state": "REST",
                    "candle_count": 1,
                    "path_efficiency": 0.2,
                },
            ],
        },
        pair_profile=pair_profile,
        advanced_studies={},
        research_studies={},
        symbol="EUR/JPY OTC",
        timeframe="H4",
        timeframe_seconds=14_400,
    )

    horizon = result["state_cycle_horizon"]
    assert horizon["path"][-1]["state"] == "UP_SWING"
    assert horizon["expected_candles"] == 23.0
    assert horizon["duration"]["seconds"] == 331_200
    assert horizon["duration"]["hours"] == 92.0
    assert horizon["duration"]["display"] == "3d 20h"
    assert (
        result["learning_objectives"]["masked_price_reconstruction"]["status"]
        == "ACTIVE_DIAGNOSTIC"
    )
    posterior = result["next_state_distribution"]["posterior"]
    assert math.isclose(
        sum(item["mean"] for item in posterior.values()),
        1.0,
        abs_tol=2e-6,
    )


def test_live_trendline_geometry_requires_exact_two_wick_contract() -> None:
    valid = {
        "type": "SUPPORT_TRENDLINE",
        "trendline_role": "support",
        "anchor_type": "TRENDLINE_TOUCH_POINTS",
        "trendline_validation": "wick_anchor_no_obstruction_closed_body_validation",
        "line_points": [[10.0, 100.0], [50.0, 88.0], [90.0, 76.0]],
        "anchor_wick_points": [[10.0, 100.0], [50.0, 88.0]],
        "touch_count": 3,
        "anchor_span_bars": 8,
        "line_obstruction_count": 0,
        "significant_close": False,
        "breach_state": "ACTIVE",
        "confidence": 0.84,
    }
    assert _strict_live_trendline_geometry_v3(valid)["anchor_wick_points"] == [
        [10.0, 100.0],
        [50.0, 88.0],
    ]

    body_breached = {**valid, "significant_close": True}
    body_anchored = {**valid, "anchor_type": "CANDLE_BODY"}
    shifted_anchor = {
        **valid,
        "anchor_wick_points": [[11.0, 100.0], [50.0, 88.0]],
    }
    assert _strict_live_trendline_geometry_v3(body_breached) == {}
    assert _strict_live_trendline_geometry_v3(body_anchored) == {}
    assert _strict_live_trendline_geometry_v3(shifted_anchor) == {}


def test_cycle_duration_falls_back_to_observed_closed_candle_segments() -> None:
    behavior = {
        "current_state": {"state": "UP_SWING", "candle_count": 5},
        "segments": [
            {"state": "UP_SWING", "candle_count": 4, "duration_seconds": 1200},
            {"state": "REST", "candle_count": 2, "duration_seconds": 600},
            {"state": "DOWN_SWING", "candle_count": 3, "duration_seconds": 900},
            {"state": "UP_SWING", "candle_count": 5, "duration_seconds": 1500},
        ],
    }
    result = build_latent_state_discovery_v3(
        candles=[
            {"ohlc": {"open": 100 + index, "high": 102 + index, "low": 99 + index, "close": 101 + index}}
            for index in range(8)
        ],
        behavior=behavior,
        pair_profile={
            "transition_counts": {
                "PIXEL_PRICE_PROXY": {
                    "UP_SWING->REST": 4,
                    "REST->DOWN_SWING": 4,
                    "DOWN_SWING->UP_SWING": 4,
                }
            },
            "segment_averages": {
                "PIXEL_PRICE_PROXY": {
                    "UP_SWING": {"candle_count": 0, "support": 0},
                    "REST": {"candle_count": 0, "support": 0},
                    "DOWN_SWING": {"candle_count": 0, "support": 0},
                }
            },
        },
        advanced_studies={},
        research_studies={},
        symbol="EUR/JPY OTC",
        timeframe="M5",
        timeframe_seconds=300,
    )

    horizon = result["state_cycle_horizon"]
    assert horizon["status"] == "EMPIRICAL_STATE_CYCLE"
    assert [row["state"] for row in horizon["path"]] == [
        "UP_SWING",
        "REST",
        "DOWN_SWING",
    ]
    assert horizon["expected_candles"] == 5.0
    assert horizon["duration"]["seconds"] == 1500
    assert result["control"]["side"] == "UNRESOLVED"
    assert result["control"]["candidate_side"] == "BUY"
    assert result["control"]["status"] == "AWAITING_STRUCTURAL_CONFIRMATION"


def test_structural_control_requires_stable_confirmation_before_overriding_local_pullback() -> None:
    study: dict[str, Any] = {
        "hidden_state_discovery_v3": {
            "hidden_state": {
                "state": "UP_SWING",
                "direction": "BUY",
                "age_candles": 2,
            },
            "control": {
                "side": "UNRESOLVED",
                "candidate_side": "BUY",
                "status": "DEVELOPING_LOCAL_STATE",
            },
            "next_state_distribution": {"status": "SUPPORTED", "support": 8},
            "directional_components": {"BUY": {}, "SELL": {}, "REST": {}},
        }
    }
    resistance = {
        "type": "INNER_TRENDLINE",
        "trendline_role": "resistance",
        "trendline_scope": "LOCAL",
        "direction": "SELL",
        "anchor_type": "TRENDLINE_TOUCH_POINTS",
        "trendline_validation": "wick_anchor_no_obstruction_closed_body_validation",
        "line_points": [[10.0, 100.0], [50.0, 88.0]],
        "anchor_wick_points": [[10.0, 100.0], [50.0, 88.0]],
        "touch_candle_indices": [1, 4, 7],
        "touch_count": 3,
        "anchor_span_bars": 8,
        "confirmation_state": "CONFIRMED",
        "line_obstruction_count": 0,
        "significant_close": False,
        "breach_state": "ACTIVE",
        "forming_touch": False,
        "close_distance_norm": 0.2,
        "confidence": 0.88,
    }
    candles = [{"source_index": index} for index in range(8)]

    reconciled = _reconcile_latent_state_control_v3(
        study,
        trendlines=[resistance],
        candles=candles,
        major_trend_side="SELL",
    )
    control = reconciled["hidden_state_discovery_v3"]["control"]
    assert control["side"] == "UNRESOLVED"
    assert control["local_leg_side"] == "BUY"
    assert control["status"] == "DIRECTION_CONFLICT"
    consensus = control["directional_consensus_v3"]
    assert consensus["raw_candidate_side"] == "SELL"
    assert consensus["sell_score"] > consensus["buy_score"]
    assert consensus["pending_switch_side"] == "SELL"
    assert consensus["pending_switch_confirmations"] == 1
    assert consensus["switch_confirmations_required"] == 3
    opposing = control["structural_evidence"]["opposing_line"]
    assert opposing["direction"] == "SELL"
    assert opposing["confirmed"] is True
    assert opposing["current_touch"] is True

    developing = _reconcile_latent_state_control_v3(
        study,
        trendlines=[
            {
                **resistance,
                "touch_candle_indices": [1, 7],
                "touch_count": 2,
                "confirmation_state": "DEVELOPING",
            }
        ],
        candles=candles,
        major_trend_side="SELL",
    )["hidden_state_discovery_v3"]["control"]
    assert developing["side"] == "UNRESOLVED"
    assert developing["directional_consensus_v3"]["raw_candidate_side"] == "SELL"
    assert developing["structural_evidence"]["confirmed_trendline_count"] == 0


def test_strict_wick_role_is_independent_of_slope_direction() -> None:
    common = {
        "anchor_type": "TRENDLINE_TOUCH_POINTS",
        "trendline_validation": "wick_anchor_no_obstruction_closed_body_validation",
        "significant_close": False,
        "line_obstruction_count": 0,
        "breach_state": "ACTIVE",
        "touch_count": 2,
        "anchor_span_bars": 4,
        "confidence": 0.8,
    }
    falling_support = {
        **common,
        "type": "SUPPORT_TRENDLINE",
        "line_points": [[10.0, 200.0], [90.0, 240.0]],
        "anchor_wick_points": [[10.0, 200.0], [90.0, 240.0]],
    }
    rising_resistance = {
        **common,
        "type": "RESISTANCE_TRENDLINE",
        "line_points": [[10.0, 200.0], [90.0, 160.0]],
        "anchor_wick_points": [[10.0, 200.0], [90.0, 160.0]],
    }

    assert _strict_live_trendline_geometry_v3(falling_support)
    assert _strict_live_trendline_geometry_v3(rising_resistance)
