from __future__ import annotations

from typing import Any

from phoenixguard.decision.book_strategy_master_v3 import (
    BOOK_STRATEGY_SCHEMA_VERSION,
    BOOK_STRATEGY_EXECUTION_AUTHORITY,
    MODEL_COUNCIL_CONTRIBUTOR_ROLE,
    evaluate_book_strategy_master_v3,
)
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


def test_model_council_carries_book_strategy_in_study_and_packet() -> None:
    council = ModelCouncilV3()
    first = council.evaluate(_strategy_snapshot("BUY"), now_epoch=NOW)
    second_snapshot = _strategy_snapshot("BUY")
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
