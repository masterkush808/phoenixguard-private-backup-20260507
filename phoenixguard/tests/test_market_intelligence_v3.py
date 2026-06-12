from __future__ import annotations

from typing import Any

from phoenixguard.decision.market_intelligence_v3 import (
    BAD_ENTRY_CLASS_001,
    MARKET_CLASSIFIER_NAMES,
    adaptive_angle_threshold,
    analyze_market_intelligence,
    analyze_market_intelligence_v3,
    angle_dynamics,
    market_classifiers,
)
from phoenixguard.decision.reasoning_arbitrator_v3 import build_model_role_votes_v3


def _zone(
    zone_id: str,
    zone_type: str,
    *,
    side: str,
    distance: float,
    inside: bool = False,
    broken: bool = False,
) -> dict[str, Any]:
    return {
        "zone_id": zone_id,
        "zone_type": zone_type,
        "side": side,
        "screen_bounds": [100, 100, 300, 140],
        "price_bounds": [1.0, 1.1],
        "strength": 0.82,
        "freshness": 0.74,
        "touch_count": 1,
        "reaction_score": 0.69,
        "broken": broken,
        "current_price_inside": inside,
        "distance_from_current": distance,
    }


def _base_snapshot(side: str = "BUY") -> dict[str, Any]:
    return {
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "candidate_side": side,
        "global_side": side,
        "local_side": side,
        "global_confidence": 0.74,
        "local_confidence": 0.68,
        "continuation_probability": 0.68,
        "continuation_confirmed": True,
        "zones": [
            _zone("demand_001", "DEMAND", side="BUY", distance=0.18, inside=side == "BUY"),
            _zone("supply_003", "SUPPLY", side="SELL", distance=0.34, inside=side == "SELL"),
        ],
        "risk_context": {
            "distance_to_opposing_force": 0.34,
            "minimum_required_distance": 0.22,
        },
        "history_context": {
            "similarity_state": "REPEATING_SUCCESSFUL_PATH",
            "best_match_setup": f"{side}_CONTINUATION_AFTER_PULLBACK",
            "best_match_outcome": "WIN",
            "historical_entry_quality": "GOOD",
            "historical_late_entry_risk": "LOW",
            "where_history_would_enter": "DEMAND_TRIGGER_ZONE" if side == "BUY" else "SUPPLY_TRIGGER_ZONE",
            "where_history_would_exit": "BEFORE_OPPOSING_ZONE",
            "similarity_to_winning_setups": 0.72,
            "similarity_to_losing_setups": 0.18,
        },
        "angle_features": {
            "screen_space_angle": 38.0,
            "price_normalised_angle": 35.0,
            "time_normalised_angle": 34.0,
            "volatility_normalised_angle": 36.0,
            "multi_candle_regression_angle": 37.0,
            "swing_leg_angle": 39.0,
            "candle_body_angle": 41.0,
            "acceleration": 0.18,
            "curvature": 0.10,
            "impulse_length": 0.58,
            "pullback_depth": 0.26,
            "wick_rejection_score": 0.48,
            "body_to_wick_ratio": 0.62,
            "angle_persistence": 0.56,
            "angle_decay": 0.20,
            "angle_break_probability": 0.24,
            "steepness_z_score": 0.86,
            "parabolic_risk": False,
            "late_chase_risk": False,
        },
    }


def _late_chase_snapshot(side: str = "BUY") -> dict[str, Any]:
    snapshot = _base_snapshot(side)
    snapshot["continuation_confirmed"] = True
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["zones"] = [
        _zone("demand_001", "DEMAND", side="BUY", distance=0.44, inside=False),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.26, inside=False),
    ]
    if side == "SELL":
        snapshot["zones"] = [
            _zone("supply_001", "SUPPLY", side="SELL", distance=0.44, inside=False),
            _zone("demand_003", "DEMAND", side="BUY", distance=0.26, inside=False),
        ]
    snapshot["risk_context"] = {
        "distance_to_opposing_force": 0.26,
        "minimum_required_distance": 0.22,
    }
    snapshot["angle_features"] = {
        "screen_space_angle": 74.0,
        "price_normalised_angle": 70.0,
        "time_normalised_angle": 72.0,
        "volatility_normalised_angle": 76.0,
        "multi_candle_regression_angle": 73.0,
        "swing_leg_angle": 75.0,
        "candle_body_angle": 82.0,
        "acceleration": 0.72,
        "curvature": 0.66,
        "impulse_length": 0.90,
        "pullback_depth": 0.04,
        "wick_rejection_score": 0.14,
        "body_to_wick_ratio": 0.88,
        "angle_persistence": 0.86,
        "angle_decay": 0.05,
        "angle_break_probability": 0.72,
        "steepness_z_score": 1.82,
        "parabolic_risk": True,
        "late_chase_risk": True,
    }
    snapshot["history_context"] = {
        "similarity_state": "RESEMBLES_LATE_LOSS",
        "best_matches": [
            {
                "match_id": "hist_0142",
                "setup": f"{side}_AFTER_STEEP_IMPULSE",
                "outcome": "LOSS",
                "entry_quality": "late",
                "would_enter_at": "earlier demand reaction" if side == "BUY" else "earlier supply reaction",
                "would_exit_at": "current_area",
                "similarity": 0.84,
            }
        ],
        "best_match_setup": f"{side}_AFTER_STEEP_IMPULSE",
        "best_match_outcome": "LOSS",
        "historical_entry_quality": "LATE",
        "historical_late_entry_risk": "HIGH",
        "where_history_would_enter": "EARLIER_TRIGGER_ZONE",
        "where_history_would_exit": "CURRENT_AREA",
        "would_have_exited_here": True,
        "similarity_to_winning_setups": 0.16,
        "similarity_to_losing_setups": 0.84,
    }
    return snapshot


def test_steep_vertical_buy_impulse_not_executable() -> None:
    result = analyze_market_intelligence_v3(_late_chase_snapshot("BUY"))

    assert result["execution"]["enabled"] is False
    assert result["execution"]["state"] == "WATCHING"
    assert result["model_council"]["final_state"] == "WATCHING"
    assert result["model_council"]["final_side"] == "BUY"
    assert result["block_reason"] == BAD_ENTRY_CLASS_001


def test_steep_vertical_sell_impulse_not_executable() -> None:
    result = analyze_market_intelligence_v3(_late_chase_snapshot("SELL"))

    assert result["execution"]["enabled"] is False
    assert result["execution"]["state"] == "WATCHING"
    assert result["model_council"]["final_side"] == "SELL"
    assert result["block_reason"] == BAD_ENTRY_CLASS_001


def test_pullback_after_steep_impulse_can_prepare() -> None:
    snapshot = _late_chase_snapshot("BUY")
    snapshot["pullback_confirmed"] = True
    snapshot["retest_confirmed"] = True
    snapshot["continuation_confirmed"] = True
    snapshot["angle_features"]["pullback_depth"] = 0.34
    snapshot["angle_features"]["late_chase_risk"] = False
    snapshot["angle_features"]["parabolic_risk"] = False
    snapshot["angle_features"]["angle_break_probability"] = 0.38
    snapshot["zones"] = [
        _zone("ctz_buy_002", "CONSERVATIVE_BUY_TRIGGER", side="BUY", distance=0.02, inside=True),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.36, inside=False),
    ]
    snapshot["history_context"] = {
        "similarity_state": "REPEATING_SUCCESSFUL_PATH",
        "best_match_setup": "BUY_CONTINUATION_AFTER_PULLBACK",
        "best_match_outcome": "WIN",
        "historical_entry_quality": "GOOD",
        "historical_late_entry_risk": "LOW",
        "where_history_would_enter": "DEMAND_TRIGGER_ZONE",
        "where_history_would_exit": "BEFORE_SUPPLY_ZONE",
        "similarity_to_winning_setups": 0.78,
        "similarity_to_losing_setups": 0.12,
    }
    snapshot["risk_context"] = {
        "distance_to_opposing_force": 0.36,
        "minimum_required_distance": 0.22,
    }

    result = analyze_market_intelligence_v3(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "PREPARING"
    assert result["block_reason"] is None
    assert result["market_context"]["inside_valid_trigger_zone"] is True


def test_buy_middle_safe_can_prepare() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["current_location"] = "MIDDLE_SAFE"
    snapshot["zones"] = [
        _zone("demand_001", "DEMAND", side="BUY", distance=0.27, inside=False),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.41, inside=False),
    ]
    snapshot["risk_context"] = {
        "distance_to_opposing_force": 0.41,
        "minimum_required_distance": 0.22,
    }

    result = analyze_market_intelligence_v3(snapshot)

    assert result["model_council"]["final_state"] == "PREPARING"
    assert result["market_context"]["middle_safe"] is True
    assert result["market_context"]["current_location"] == "MIDDLE_SAFE"
    assert result["classifiers"]["middle_safe"] is True
    assert result["market_context"]["paper_prepare_allowed"] is True
    assert result["market_context"]["paper_entry_allowed"] is True


def test_middle_danger_does_not_prepare_from_distance_alone() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["current_location"] = "MIDDLE_DANGER"
    snapshot["zones"] = [
        _zone("demand_001", "DEMAND", side="BUY", distance=0.27, inside=False),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.41, inside=False),
    ]
    snapshot["risk_context"] = {
        "distance_to_opposing_force": 0.41,
        "minimum_required_distance": 0.22,
    }

    result = analyze_market_intelligence_v3(snapshot)

    assert result["execution"]["state"] == "WATCHING"
    assert result["market_context"]["middle_safe"] is False
    assert result["market_context"]["middle_danger"] is True
    assert result["market_context"]["inside_valid_trigger_zone"] is False


def test_buy_near_supply_blocks_council_execution() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["zones"] = [
        _zone("ctz_buy_002", "CONSERVATIVE_BUY_TRIGGER", side="BUY", distance=0.01, inside=True),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.09, inside=False),
    ]
    snapshot["risk_context"] = {
        "distance_to_opposing_force": 0.09,
        "minimum_required_distance": 0.22,
    }

    result = analyze_market_intelligence_v3(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["market_state"] == "BLOCKED_BY_MARKET"
    assert result["block_reason"] == "OPPOSING_FORCE_TOO_CLOSE"


def test_sell_middle_safe_can_prepare() -> None:
    snapshot = _base_snapshot("SELL")
    snapshot["current_location"] = "MIDDLE_SAFE"
    snapshot["zones"] = [
        _zone("supply_001", "SUPPLY", side="SELL", distance=0.27, inside=False),
        _zone("demand_003", "DEMAND", side="BUY", distance=0.39, inside=False),
    ]
    snapshot["risk_context"] = {
        "distance_to_opposing_force": 0.39,
        "minimum_required_distance": 0.22,
    }

    result = analyze_market_intelligence_v3(snapshot)

    assert result["model_council"]["final_state"] == "PREPARING"
    assert result["model_council"]["final_side"] == "SELL"
    assert result["market_context"]["middle_safe"] is True


def test_sell_near_demand_blocks_council_execution() -> None:
    snapshot = _base_snapshot("SELL")
    snapshot["zones"] = [
        _zone("ctz_sell_002", "CONSERVATIVE_SELL_TRIGGER", side="SELL", distance=0.01, inside=True),
        _zone("demand_003", "DEMAND", side="BUY", distance=0.08, inside=False),
    ]
    snapshot["risk_context"] = {
        "distance_to_opposing_force": 0.08,
        "minimum_required_distance": 0.22,
    }

    result = analyze_market_intelligence_v3(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["market_state"] == "BLOCKED_BY_MARKET"
    assert result["block_reason"] == "OPPOSING_FORCE_TOO_CLOSE"


def test_history_would_exit_here_blocks_new_entry() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["history_context"] = {
        "similarity_state": "RESEMBLES_LATE_LOSS",
        "best_match_setup": "BUY_AFTER_STEEP_IMPULSE",
        "best_match_outcome": "LOSS",
        "historical_entry_quality": "LATE",
        "historical_late_entry_risk": "HIGH",
        "where_history_would_enter": "EARLIER_DEMAND_REACTION",
        "where_history_would_exit": "CURRENT_AREA",
        "would_have_exited_here": True,
        "similarity_to_winning_setups": 0.20,
        "similarity_to_losing_setups": 0.82,
    }

    result = analyze_market_intelligence_v3(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["market_state"] == "BLOCKED_BY_MARKET"
    assert result["block_reason"] == "HISTORY_WOULD_EXIT_HERE"


def test_late_chase_bad_entry_class_detected() -> None:
    result = analyze_market_intelligence_v3(_late_chase_snapshot("BUY"))

    assert result["block_reason"] == BAD_ENTRY_CLASS_001
    assert set(MARKET_CLASSIFIER_NAMES).issubset(result["classifiers"])
    assert result["classifiers"]["late_chase_after_impulse"] is True
    assert result["angle_context"]["late_chase_risk"] is True
    assert result["angle_context"]["post_impulse_wait_required"] is True
    assert "pullback/retest" in str(result["instruction"])


def test_false_breakout_risk_blocks_paper_entry() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["zones"] = [
        _zone("ctz_buy_002", "CONSERVATIVE_BUY_TRIGGER", side="BUY", distance=0.01, inside=True),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.36, inside=False),
    ]
    snapshot["liquidity_sweep_detected"] = True
    snapshot["breakout_confirmed"] = True
    snapshot["retest_confirmed"] = False
    snapshot["breakout_reclaimed"] = False
    snapshot["false_breakout_probability"] = 0.71

    result = analyze_market_intelligence_v3(snapshot)

    assert result["block_reason"] == "FALSE_BREAKOUT_RISK"
    assert result["classifiers"]["false_breakout_risk"] is True
    assert result["market_context"]["paper_entry_allowed"] is False


def test_model_role_outputs_include_model_and_freshness_contract() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["frames_used"] = 96
    snapshot["model_vote_age_ms"] = 420

    roles = build_model_role_votes_v3(
        snapshot,
        side="BUY",
        market_play={"primary_play": "BULLISH_PULLBACK_CONTINUATION", "confidence": 0.74},
        regime={"primary": "TRENDING_UP"},
        price_location={"relative_location": "LOCAL_LOW", "buy_quality": "GOOD"},
        memory_confirmation={"confirmed": True, "similarity": 0.78, "memory_vote": "BUY"},
    )

    assert len(roles) == 7
    for role in roles:
        assert role["model"]
        assert role["role"]
        assert role["side_vote"] in {"BUY", "SELL", "HOLD"}
        assert role["play_vote"]
        assert role["regime_vote"]
        assert role["frames_used"] == 96
        assert role["freshness_ms"] == 420
        assert role["evidence"]
        assert "risk_warning" in role


def test_pullback_not_confirmed_blocks_reload_setup() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["continuation_confirmed"] = False
    snapshot["continuation_probability"] = 0.34
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["requires_pullback_confirmation"] = True
    snapshot["setup_type"] = "pullback_reload"
    snapshot["zones"] = [
        _zone("ctz_buy_002", "CONSERVATIVE_BUY_TRIGGER", side="BUY", distance=0.01, inside=True),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.40, inside=False),
    ]

    result = analyze_market_intelligence_v3(snapshot)

    assert result["block_reason"] == "PULLBACK_NOT_CONFIRMED"
    assert result["classifiers"]["pullback_not_confirmed"] is True
    assert result["model_council"]["market_state"] == "BLOCKED_BY_MARKET"


def test_angle_break_risk_blocks_unstable_angle() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["angle_features"]["angle_class"] = "BROKEN_ANGLE"
    snapshot["angle_features"]["angle_break_probability"] = 0.66
    snapshot["zones"] = [
        _zone("ctz_buy_002", "CONSERVATIVE_BUY_TRIGGER", side="BUY", distance=0.01, inside=True),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.40, inside=False),
    ]

    result = analyze_market_intelligence_v3(snapshot)

    assert result["block_reason"] == "ANGLE_BREAK_RISK"
    assert result["classifiers"]["angle_break_risk"] is True


def test_dominance_weakening_blocks_bad_entry() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["current_location"] = "MIDDLE_SAFE"
    snapshot["previous_dominance_score"] = 0.74
    snapshot["dominance_score"] = 0.48
    snapshot["dominance_state"] = "WEAKENING"
    snapshot["zones"] = [
        _zone("demand_001", "DEMAND", side="BUY", distance=0.27, inside=False),
        _zone("supply_003", "SUPPLY", side="SELL", distance=0.41, inside=False),
    ]

    result = analyze_market_intelligence_v3(snapshot)

    assert result["block_reason"] == "DOMINANCE_WEAKENING"
    assert result["classifiers"]["dominance_weakening"] is True
    assert result["market_context"]["paper_prepare_allowed"] is False


def test_conflict_market_blocks_flip_flop_leakage() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot["buy_score"] = 0.66
    snapshot["sell_score"] = 0.64
    snapshot["conflict_score"] = 0.80
    snapshot["recent_sides"] = ["BUY", "SELL", "BUY"]

    result = analyze_market_intelligence_v3(snapshot)
    classifier_result = market_classifiers(snapshot, "BUY")

    assert result["block_reason"] == "CONFLICT_MARKET"
    assert result["classifiers"]["conflict_market"] is True
    assert classifier_result["blocking_reasons"][0] == "CONFLICT_MARKET"


def test_legacy_public_api_resolves_snapshot_side_for_model_council() -> None:
    snapshot = _base_snapshot("BUY")
    snapshot.pop("current_location", None)
    result = analyze_market_intelligence(snapshot)

    assert result["market_context"]["dominant_side"] == "BUY"
    assert result["market_context"]["global_side"] == "BUY"
    assert result["market_context"]["local_side"] == "BUY"
    assert result["execution_hint"]["state"] == "PREPARING"
    assert result["block_reason"] is None


def test_legacy_public_api_surfaces_market_block_reason() -> None:
    result = analyze_market_intelligence(_late_chase_snapshot("BUY"))

    assert result["market_context"]["dominant_side"] == "BUY"
    assert result["block_reason"] == BAD_ENTRY_CLASS_001
    assert result["execution_hint"]["block_reason"] == BAD_ENTRY_CLASS_001


def test_angle_adaptive_threshold_by_symbol_timeframe() -> None:
    m1_threshold = adaptive_angle_threshold("EUR/GBP OTC", "M1")
    m15_threshold = adaptive_angle_threshold("EUR/GBP OTC", "M15")
    baseline_threshold = adaptive_angle_threshold(
        "EUR/GBP OTC",
        "M5",
        {"steepness_z_p90": 1.8},
    )

    assert m1_threshold < m15_threshold
    assert baseline_threshold > adaptive_angle_threshold("EUR/GBP OTC", "M5")

    low_tf_angle = angle_dynamics(
        {
            "symbol": "EUR/GBP OTC",
            "timeframe": "M1",
            "candidate_side": "BUY",
            "angle_features": {
                "screen_space_angle": 50.0,
                "multi_candle_regression_angle": 50.0,
                "impulse_length": 0.60,
                "pullback_depth": 0.03,
                "angle_persistence": 0.78,
                "angle_break_probability": 0.60,
                "steepness_z_score": 1.14,
            },
        }
    )
    higher_tf_angle = angle_dynamics(
        {
            "symbol": "EUR/GBP OTC",
            "timeframe": "M15",
            "candidate_side": "BUY",
            "angle_features": {
                "screen_space_angle": 50.0,
                "multi_candle_regression_angle": 50.0,
                "impulse_length": 0.60,
                "pullback_depth": 0.03,
                "angle_persistence": 0.78,
                "angle_break_probability": 0.60,
                "steepness_z_score": 1.14,
            },
        }
    )

    assert low_tf_angle["late_chase_risk"] is True
    assert higher_tf_angle["late_chase_risk"] is False
