from __future__ import annotations

from typing import Any

from phoenixguard.decision.book_strategy_master_v3 import (
    BOOK_STRATEGY_SCHEMA_VERSION,
    BOOK_STRATEGY_EXECUTION_AUTHORITY,
    MODEL_COUNCIL_CONTRIBUTOR_ROLE,
    evaluate_book_strategy_master_v3,
)
from phoenixguard.decision.candle_movement_context_v3 import build_candle_movement_context_v3
from phoenixguard.decision.market_play_engine_v3 import analyze_market_play_v3
from phoenixguard.decision.model_council_v3 import ModelCouncilV3


NOW = 1_800_010_000.0


def _zone(zone_id: str, zone_type: str, *, side: str, inside: bool = False, distance: float = 0.08) -> dict[str, Any]:
    return {
        "zone_id": zone_id,
        "zone_type": zone_type,
        "side": side,
        "current_price_inside": inside,
        "distance_from_current": distance,
        "role_flip_confirmed": False,
        "confidence": 0.82,
    }


def _strategy_snapshot(side: str = "BUY") -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY"
    return {
        "session_id": "pocket-live-8788",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": 901,
        "capture_count": 903,
        "state_version": 1901,
        "input_frame_hash": "frame_901",
        "previous_frame_hash": "frame_900",
        "live_integrity": {
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "cache_status": "fresh",
        },
        "runtime_model_health": {
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "max_model_latency_ms": 32,
            "queue_depth": 0,
        },
        "candidate_side": side,
        "global_structure": {"global_side": side, "global_confidence": 0.86},
        "local_micro_structure": {"local_side": side, "confidence": 0.84},
        "market_context": {
            "global_side": side,
            "local_side": side,
            "dominant_side": side,
            "current_location": "DEMAND_ZONE" if side == "BUY" else "SUPPLY_ZONE",
            "inside_valid_trigger_zone": True,
            "opposing_force_distance_ok": True,
            "is_continuation_confirmed": True,
            "is_late_chase": False,
            "middle_safe": True,
        },
        "zone_liquidity": {
            "side": side,
            "zone_type": "DEMAND" if side == "BUY" else "SUPPLY",
            "inside_valid_trigger_zone": True,
        },
        "zones": [
            _zone("active_001", "DEMAND" if side == "BUY" else "SUPPLY", side=side, inside=True),
            _zone("opposing_001", "SUPPLY" if side == "BUY" else "DEMAND", side=opposite, distance=0.42),
        ],
        "risk_context": {
            "distance_to_opposing_force": 0.42,
            "minimum_required_distance": 0.22,
            "distance_ok": True,
        },
        "angle_features": {
            "angle_class": "HEALTHY_TREND",
            "late_chase_risk": False,
            "post_impulse_wait_required": False,
        },
        "historical_pattern": {
            "similarity_state": "REPEATING_SUCCESSFUL_PATH",
            "would_have_exited_here": False,
            "historical_entry_quality": "GOOD",
        },
        "timing": {"state": "READY", "side": side, "expiry_seconds": 300},
        "current_candle_acceptance": {
            "state": "VALID",
            "phase": "VALID",
            "entry_allowed": True,
            "current_candle_closed": True,
        },
        "continuation_confirmed": True,
        "pullback_confirmed": True,
        "retest_confirmed": True,
        "sequence_length": 50,
        "frames_used": 50,
        "frames_received": 50,
        "sequence_confidence": 0.94,
        "sequence_status": "COMPLETE",
        "historical_structure": [
            {"key": "history_1", "label": f"H1 {side}", "bbox": [10, 20, 150, 180], "direction": side}
        ],
        "progression": [{"stage": "context_confirmed", "direction": side, "confidence": 0.92}],
        "entry_progression": {
            "progression_stage": "SNIPER_READY",
            "maturity_score": 0.91,
            "progression_velocity": 0.34,
            "continuation_strength": 0.86,
            "exhaustion_risk": 0.12,
        },
    }


def _attach_candle_movement_fixture(snapshot: dict[str, Any], side: str) -> None:
    opposite = "SELL" if side == "BUY" else "BUY"
    candles = [
        {
            "index": index,
            "bbox": [20 + index * 10, 100 + index * 2, 28 + index * 10, 126 + index * 2],
            "center_x": 24 + index * 10,
            "center_y": 113 + index * 2,
            "direction": opposite,
            "price_proxy": 1.0 - index * 0.01,
        }
        for index in range(4)
    ] + [
        {
            "index": index,
            "bbox": [20 + index * 10, 180 - index * 3, 28 + index * 10, 206 - index * 3],
            "center_x": 24 + index * 10,
            "center_y": 193 - index * 3,
            "direction": side,
            "price_proxy": 0.96 + (index - 4) * 0.012,
        }
        for index in range(4, 12)
    ]
    snapshot["tracking_summary"] = {
        "detected_timeframe": "M5",
        "visible_candle_count": len(candles),
        "tracked_candles": candles,
        "historical_structure": [
            {"label": f"H1 {opposite}", "direction": opposite, "source_indices": list(range(4)), "candle_count": 4},
            {"label": f"H2 {side}", "direction": side, "source_indices": list(range(4, 12)), "candle_count": 8},
        ],
        "support_resistance_zones": [
            {
                "label": "DEMAND" if side == "BUY" else "SUPPLY",
                "role": "DEMAND" if side == "BUY" else "SUPPLY",
                "bbox": [58, 120, 132, 190],
                "anchor_candles": [4, 5, 6],
            }
        ],
    }
    snapshot["risk_opposing_force"] = {"distance_to_opposing_force": 0.38, "distance_ok": True}
    snapshot["candle_movement_context_v3"] = build_candle_movement_context_v3(snapshot)


def test_book_strategy_master_signs_valid_single_timeframe_reaction() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.44
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.72
    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "LIVE_TOUCH_REJECTION"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.83,
        lane_required_score=0.70,
    )

    assert result["schema_version"] == BOOK_STRATEGY_SCHEMA_VERSION
    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook"] == "DEMAND_REJECTION"
    assert result["execution_authority"] == BOOK_STRATEGY_EXECUTION_AUTHORITY
    assert result["model_council_role"] == MODEL_COUNCIL_CONTRIBUTOR_ROLE
    assert result["single_timeframe_mode"] is True
    assert result["multiple_timeframe_required"] is False
    assert result["playbook_signal"] == "BUY"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert result["reaction_type"] in {"BODY_ACCEPTANCE", "RETEST_HOLD", "WICK_REJECTION", "RECLAIM_AFTER_SWEEP"}
    assert "DEMAND_REJECTION" in result["strategy_combo"]


def test_book_strategy_uses_conservative_retest_profile_after_role_flip() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["role_flip_confirmed"] = True
    snapshot["retest_confirmed"] = True
    snapshot["break_of_structure_confirmed"] = True
    snapshot["zones"][0]["current_price_inside"] = False
    snapshot["zones"][0]["role_flip_confirmed"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "BMS_ROLE_FLIP", "play_stage": "RETEST_ACCEPTED"},
            "price_location": {"relative_location": "ROLE_FLIP_RETEST"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={"name": "FAILED_RETEST_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "legacy lane waiting"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_RETEST",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.72,
        lane_required_score=0.90,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "SELL"
    assert result["entry_profile"] == "CONSERVATIVE_RETEST"
    assert "ROLE_FLIP" in result["strategy_combo"]
    assert "RETEST_CONFIRMED" in result["strategy_combo"]


def test_book_strategy_keeps_watching_without_candle_reaction() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["current_candle_acceptance"]["entry_allowed"] = False
    snapshot["current_candle_acceptance"]["accepted"] = False
    snapshot["current_candle_acceptance"]["state"] = "FORMING"

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "AT_DEMAND"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "wait for candle reaction"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=True,
        timing_enter_now=False,
        lane_score=0.80,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "VALID_WATCH"
    assert result["playbook_signal"] == "HOLD"
    assert result["entry_profile"] == "WATCH_ONLY"
    assert not result["hard_blockers"]
    assert any(row["field"] == "current_candle.entry_allowed" for row in result["soft_warnings"])


def test_book_strategy_blocks_when_models_not_awake() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["runtime_model_health"]["all_required_models_awake"] = False

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={"market_context": snapshot["market_context"], "zones": snapshot["zones"]},
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.84,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "PREPARE"
    assert result["playbook_signal"] == "HOLD"
    assert any(row["field"] == "runtime_model_health" for row in result["hard_blockers"])


def test_book_strategy_can_authorize_without_legacy_lane_acceptance() -> None:
    snapshot = _strategy_snapshot("BUY")
    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "RETEST_ACCEPTED"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False, "reason": "LANE_SCORE_BELOW_THRESHOLD"},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "legacy timing contributor waiting"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.79,
        lane_required_score=0.95,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["final_decider"] is True
    assert result["evidence"]["lane_authority_ready"] is False
    assert result["evidence"]["final_score_passed"] is False
    assert not result["blockers"]
    assert result["evidence"]["inside_valid_trigger_zone"] is True


def test_book_strategy_downgrades_local_countertrend_reaction_to_scalp_watch() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["global_structure"]["global_side"] = "SELL"
    snapshot["local_micro_structure"]["local_side"] = "BUY"
    snapshot["market_context"]["global_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "BUY"
    snapshot["market_context"]["dominant_side"] = "SELL"

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "BEARISH_PULLBACK_CONTINUATION", "play_stage": "PULLBACK_FAILING"},
            "price_location": {"relative_location": "SUPPLY_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={
            "name": "SNIPER_ZONE_ENTRY",
            "accepted": False,
            "live_trigger_reaction": {"accepted": True},
        },
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "wait for larger bias alignment"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.56,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "PREPARE"
    assert result["playbook_signal"] == "HOLD"
    assert result["playbook"] == "COUNTERTREND_SCALP_ONLY"
    assert result["evidence"]["countertrend_scalp_only"] is True
    assert result["evidence"]["countertrend_against_local"] is True
    assert result["evidence"]["measured_reaction_can_override_timing"] is False
    assert any(row["field"] == "bias_alignment" for row in result["blockers"])


def test_book_strategy_promotes_failed_supply_reclaim_buy_continuation() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["role_flip_confirmed"] = True
    snapshot["break_of_structure_confirmed"] = True
    snapshot["retest_confirmed"] = True
    snapshot["zones"] = [
        _zone("failed_supply_001", "SUPPLY", side="BUY", inside=False),
        _zone("target_supply_001", "SUPPLY", side="SELL", distance=0.42),
    ]
    snapshot["zones"][0]["role_flip_confirmed"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "BMS_ROLE_FLIP", "play_stage": "RETEST_ACCEPTED"},
            "price_location": {"relative_location": "ROLE_FLIP_RETEST"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "FAILED_RETEST_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "legacy timing contributor waiting"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_RETEST",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.70,
        lane_required_score=0.90,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["playbook"] == "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION"
    assert result["entry_profile"] == "CONSERVATIVE_RETEST"
    assert result["evidence"]["countertrend_reversal_override"] is True
    assert result["evidence"]["large_move_bias_aligned"] is True


def test_book_strategy_arms_failed_sell_into_demand_aggressive_buy_reversal() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["global_structure"]["global_side"] = "SELL"
    snapshot["local_micro_structure"]["local_side"] = "SELL"
    snapshot["market_context"]["global_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "SELL"
    snapshot["market_context"]["dominant_side"] = "SELL"
    snapshot["market_context"]["current_location"] = "DEMAND_ZONE"
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["angle_features"]["late_chase_risk"] = True
    snapshot["continuation_confirmed"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.46
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.74
    snapshot["two_candle_study"] = {"next_1_direction": "BUY", "next_1_probability": 0.64}

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL", "play_stage": "AGGRESSIVE_REVERSAL_ARMED"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "aggressive reversal reaction"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.57,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["playbook"] == "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert result["evidence"]["failed_continuation_reversal"] is True
    assert result["evidence"]["countertrend_reversal_override"] is True
    assert result["evidence"]["late_chase"] is False
    assert result["evidence"]["late_chase_softened_by_extreme_reversal"] is True


def test_market_play_classifies_failed_sell_into_demand_before_countertrend_scalp() -> None:
    result = analyze_market_play_v3(
        {
            "candidate_side": "BUY",
            "global_side": "SELL",
            "local_side": "SELL",
            "market_context": {
                "global_side": "SELL",
                "local_side": "SELL",
                "current_location": "DEMAND_ZONE",
                "active_zone_type": "DEMAND",
            },
            "local_micro_structure": {"rejection_score": 0.52},
        },
        side="BUY",
        price_location={"relative_location": "LOCAL_LOW", "local_range_position": 0.18},
    )

    play = result["market_play"]
    assert play["primary_play"] == "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL"
    assert play["side_bias"] == "BUY"
    assert play["entry_logic"] == "AGGRESSIVE_BUY_ON_DEMAND_REJECTION_OR_CONSERVATIVE_RETEST"


def test_book_strategy_master_blocks_late_chase() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["angle_features"]["late_chase_risk"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={"market_context": snapshot["market_context"], "zones": snapshot["zones"]},
        candidate_side="SELL",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.84,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "LATE_CHASE"
    assert result["denied_at"] == "LATE_CHASE"
    assert any(row["field"] == "late_chase" for row in result["hard_blockers"])


def test_book_strategy_softens_late_chase_for_book_valid_aggressive_reclaim() -> None:
    snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(snapshot, "BUY")
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["angle_features"]["late_chase_risk"] = True
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.48
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.76

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "DEMAND_REJECTION", "play_stage": "LIVE_TOUCH_REJECTION"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
        bad_entry_filter={"active": True, "class": "LATE_CHASE", "severity": 0.86},
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert result["evidence"]["raw_late_chase"] is True
    assert result["evidence"]["late_chase"] is False
    assert result["evidence"]["book_valid_reaction"] is True
    assert result["evidence"]["late_chase_softened_by_book_reaction"] is True
    assert result["evidence"]["current_leg_candle_count"] == 8
    assert result["evidence"]["movement_stage"] == "MATURE"
    assert not result["hard_blockers"]


def test_book_strategy_keeps_true_late_chase_when_extension_has_no_room() -> None:
    snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(snapshot, "BUY")
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["market_context"]["opposing_force_distance_ok"] = False
    snapshot["angle_features"]["late_chase_risk"] = True
    snapshot["risk_opposing_force"] = {"distance_to_opposing_force": 0.06, "distance_ok": False}
    late_room: dict[str, Any] = {
        "candidate_side": "BUY",
        "room_ok": False,
        "distance_norm": 0.06,
        "risk_state": "NEAR_OPPOSING_FORCE",
        "estimated_candles_to_force": 1,
        "zone": {},
        "reason": "Opposing force is too close.",
    }
    snapshot["candle_movement_context_v3"] = {
        **snapshot["candle_movement_context_v3"],
        "move_stage": "LATE",
        "opposing_force_room": late_room,
        "current_leg": {
            **snapshot["candle_movement_context_v3"]["current_leg"],
            "move_stage": "LATE",
            "candle_count": 16,
            "opposing_force_room": late_room,
        },
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "LATE_CHASE_AFTER_IMPULSE", "play_stage": "MATURE_OR_LATE"},
            "price_location": {"relative_location": "LOCAL_HIGH"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
        bad_entry_filter={"active": True, "class": "LATE_CHASE_STEEP_IMPULSE", "severity": 0.88},
    )

    assert result["maturity_state"] == "LATE_CHASE"
    assert result["playbook_signal"] == "HOLD"
    assert result["evidence"]["late_chase"] is True
    assert result["evidence"]["current_leg_exhausted"] is True
    assert any(row["field"] == "late_chase" for row in result["hard_blockers"])


def test_model_council_promotes_short_horizon_buy_warning_at_demand_to_candidate_side() -> None:
    council = ModelCouncilV3()
    snapshot = _strategy_snapshot("SELL")
    snapshot["candidate_side"] = "SELL"
    snapshot["global_structure"]["global_side"] = "SELL"
    snapshot["local_micro_structure"]["local_side"] = "SELL"
    snapshot["market_context"]["global_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "SELL"
    snapshot["market_context"]["dominant_side"] = "SELL"
    snapshot["market_context"]["current_location"] = "DEMAND_ZONE"
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["zone_liquidity"] = {"side": "BUY", "zone_type": "DEMAND", "inside_valid_trigger_zone": True}
    snapshot["zones"] = [
        _zone("demand_001", "DEMAND", side="BUY", inside=True),
        _zone("supply_001", "SUPPLY", side="SELL", distance=0.42),
    ]
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.44
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.72
    snapshot["two_candle_study"] = {"next_1_direction": "BUY", "next_1_probability": 0.64}
    snapshot["continuation_confirmed"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False

    result = council.evaluate(snapshot, now_epoch=NOW)

    assert result["model_council"]["final_side"] == "BUY"
    assert result["book_strategy"]["playbook"] == "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL"
    assert result["book_strategy"]["evidence"]["failed_continuation_reversal"] is True


def test_model_council_carries_book_strategy_in_study_and_packet() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(first_snapshot, "BUY")
    first = council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(second_snapshot, "BUY")
    second_snapshot["frame_id"] = 902
    second_snapshot["capture_count"] = 904
    second_snapshot["state_version"] = 1902
    second_snapshot["input_frame_hash"] = "frame_902"
    second_snapshot["previous_frame_hash"] = "frame_901"
    second = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)

    assert first["book_strategy"]["single_timeframe_mode"] is True
    assert second["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert second["book_strategy"]["maturity_state"] == "ENTER_NOW"
    assert second["allowance_package"]["book_strategy_maturity"] == "ENTER_NOW"
    assert second["allowance_package"]["execution_authority"] == BOOK_STRATEGY_EXECUTION_AUTHORITY
    assert second["allowance_package"]["candle_movement"]["visible_candle_count"] == 12
    assert second["allowance_package"]["candle_movement"]["current_leg_candle_count"] == 8
    assert second["allowance_package"]["expected_move_time"]["expected_duration_sec"] == 300
    assert second["allowance_package"]["expected_move_time"]["expected_candle_count"] == 1
    assert second["allowance_package"]["expected_move_time"]["projected_total_current_leg_candles"] == 9
    assert second["candle_movement_context_v3"]["move_stage"] == "MATURE"
    assert second["model_council"]["strategy_read"]["doctrine"] == "single_timeframe_visible_history_only"


def test_model_council_playbook_publishes_when_legacy_lane_rejects_score() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("SELL")
    second_snapshot = _strategy_snapshot("SELL")
    for snapshot in (first_snapshot, second_snapshot):
        snapshot["execution_threshold"] = 0.98
        snapshot["lane_thresholds"] = {
            "SNIPER_ZONE_ENTRY": 0.98,
            "LOCAL_BREAKDOWN_CONTINUATION": 0.98,
            "FAILED_RETEST_ENTRY": 0.98,
            "WAVE_RIDING_CONTINUATION": 0.98,
            "MOMENTUM_ACCEPTANCE_ENTRY": 0.98,
            "HISTORY_MATCHED_CONTINUATION": 0.98,
        }
    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 912
    second_snapshot["capture_count"] = 914
    second_snapshot["state_version"] = 1912
    second_snapshot["input_frame_hash"] = "frame_912"
    second_snapshot["previous_frame_hash"] = "frame_911"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)

    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["book_strategy_state"] == "ENTER_NOW"
    assert result["model_council"]["execution_lane"]["accepted"] is False
    assert result["allowance_package"]["playbook_authorized"] is True
    assert result["allowance_package"]["lane_is_contributor"] is True
    assert result["allowance_package"]["score_passed"] is False
    assert result["model_council"]["contributors_are_diagnostic"] is True
