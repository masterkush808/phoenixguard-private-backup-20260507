from __future__ import annotations

from typing import Any

from phoenixguard.decision.market_reality_engine import ACCEPTABLE_ENTRY, analyze_market_reality
from phoenixguard.decision.model_council_v3 import ModelCouncilV3, validate_execution_packet_v3


NOW = 1_764_100_000.0


def _strong_snapshot(side: str = "BUY", *, frame_id: int = 200) -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY"
    return {
        "session_id": "market-reality-tests",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": frame_id,
        "capture_count": frame_id + 2,
        "state_version": frame_id + 1000,
        "input_frame_hash": f"market_reality_{side.lower()}_{frame_id}",
        "previous_frame_hash": f"market_reality_{side.lower()}_{frame_id - 1}",
        "confidence": 0.84,
        "runtime_model_health": {
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "max_model_latency_ms": 24,
            "queue_depth": 0,
        },
        "live_integrity": {
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": f"market_reality_{side.lower()}_{frame_id}",
            "previous_frame_hash": f"market_reality_{side.lower()}_{frame_id - 1}",
            "packet_age_ms": 100,
        },
        "global_structure": {
            "global_side": side,
            "global_state": "TRENDING",
            "major_swing_direction": side,
            "major_swing_strength": 0.86,
            "global_confidence": 0.84,
        },
        "local_micro_structure": {
            "local_side": side,
            "local_state": "PULLBACK_RECOVERY",
            "momentum_state": "STRENGTHENING",
            "confidence": 0.82,
        },
        "zone_liquidity": {
            "zone_type": "demand" if side == "BUY" else "supply",
            "side": side,
            "inside_valid_trigger_zone": True,
            "strength": 0.86,
        },
        "angle_dynamics": {
            "angle_class": "STRONG_BUT_SUSTAINABLE",
            "screen_space_angle": 34.0,
            "late_chase_risk": False,
            "parabolic_risk": False,
            "post_impulse_wait_required": False,
            "angle_break_probability": 0.24,
            "pullback_depth": 0.28,
        },
        "historical_pattern": {
            "similarity_state": "REPEATING_SUCCESSFUL_PATH",
            "best_match_setup": f"{side}_CONTINUATION_AFTER_PULLBACK",
            "best_match_outcome": "WIN",
            "historical_entry_quality": "GOOD",
            "historical_late_entry_risk": "LOW",
            "where_history_would_enter": "DEMAND_TRIGGER_ZONE" if side == "BUY" else "SUPPLY_TRIGGER_ZONE",
            "where_history_would_exit": f"BEFORE_{opposite}_ZONE",
            "similarity_to_winning_setups": 0.74,
            "similarity_to_losing_setups": 0.12,
        },
        "risk_opposing_force": {
            "side": side,
            "distance_to_opposing_force": 0.36,
            "minimum_required_distance": 0.22,
            "distance_ok": True,
            "risk_state": "ACCEPTABLE",
        },
        "market_context": {
            "global_side": side,
            "local_side": side,
            "dominant_side": side,
            "dominance_state": "STRENGTHENING",
            "current_location": "MIDDLE_SAFE",
            "inside_valid_trigger_zone": True,
            "opposing_force_distance_ok": True,
            "is_late_chase": False,
            "is_steep_angle_break_risk": False,
            "is_continuation_confirmed": True,
        },
        "timing": {
            "state": "READY",
            "side": side,
            "expiry_seconds": 300,
            "target_time_text": "00:05:00",
            "reason": "Pullback into conservative trigger zone confirmed.",
        },
        # Provide COMPLETE sequence context by default for tests that expect executable packets.
        "sequence_length": 50,
        "frames_used": 50,
        "frames_received": 50,
        "sequence_confidence": 0.92,
        "sequence_status": "COMPLETE",
        "historical_structure": [
            {
                "key": "history_1",
                "label": f"H1 {side}",
                "bbox": [10, 20, 150, 180],
                "direction": side,
                "candle_count": 18,
            }
        ],
        "progression": [{"stage": "context_confirmed", "direction": side, "confidence": 0.92}],
        "entry_progression": {
            "progression_stage": "SNIPER_READY",
            "maturity_score": 0.91,
            "progression_velocity": 0.32,
            "continuation_strength": 0.86,
            "exhaustion_risk": 0.12,
        },
        "entry_quality": ACCEPTABLE_ENTRY,
    }


def _second_council_read(snapshot: dict[str, Any]) -> dict[str, Any]:
    council = ModelCouncilV3()
    first = dict(snapshot)
    first["frame_id"] = int(snapshot["frame_id"]) - 1
    first["capture_count"] = int(snapshot["capture_count"]) - 1
    first["state_version"] = int(snapshot["state_version"]) - 1
    first["input_frame_hash"] = f"{snapshot['input_frame_hash']}_first"
    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is False
    return council.evaluate(snapshot, now_epoch=NOW + 0.5)


def test_strong_buy_direction_with_bad_now_entry_stays_watching() -> None:
    snapshot = _strong_snapshot("BUY")
    snapshot["entry_quality"] = "BAD_NOW"

    result = _second_council_read(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "WATCHING"
    assert result["model_council"]["final_side"] == "BUY"
    assert result["block_reason"] == "ENTRY_QUALITY_BELOW_ACCEPTABLE"
    assert result["entry_quality"]["state"] == "BAD_NOW"
    assert result["trade_permission"]["study_allowed"] is True


def test_acceptable_entry_grants_model_council_permission() -> None:
    packet = _second_council_read(_strong_snapshot("BUY"))

    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["state"] == "EXECUTABLE"
    assert packet["trade_permission"]["executable_allowed"] is True
    assert packet["trade_permission"]["permission_state"] == "GRANTED"
    assert packet["trade_permission"]["denied_at"] is None
    assert packet["entry_quality"]["state"] == ACCEPTABLE_ENTRY
    assert packet["entry_quality"]["entry_grade"] == ACCEPTABLE_ENTRY
    assert packet["entry_quality"]["direction_side"] == "BUY"
    assert packet["entry_quality"]["entry_timing"] == "READY"
    assert packet["regime_playbook"]["regime"] == "TREND_CONTINUATION"
    assert packet["current_candle_contract"]["state"] == "VALID"
    assert packet["market_reality"]["market_listening_stream"]["role"] == "MODEL_COUNCIL_INPUT"
    assert validate_execution_packet_v3(packet, now_epoch=NOW + 0.6).ok is True


def test_missing_legacy_entry_quality_infers_from_structural_evidence() -> None:
    snapshot = _strong_snapshot("SELL")
    snapshot["entry_quality"] = "NONE"

    packet = _second_council_read(snapshot)

    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "SELL"
    assert packet["entry_quality"]["state"] == "GOOD_ENTRY"
    assert packet["trade_permission"]["permission_state"] == "GRANTED"


def test_late_chase_trap_denies_permission() -> None:
    result = analyze_market_reality(
        _strong_snapshot("BUY"),
        side="BUY",
        market_inputs={
            "market_context": {"dominant_side": "BUY", "is_late_chase": True, "inside_valid_trigger_zone": True},
            "classifiers": {"late_chase_after_impulse": True},
            "angle_context": {"late_chase_risk": True},
        },
    )

    assert result["market_trap"]["detected"] is True
    assert result["market_trap"]["trap_type"] == "LATE_CHASE_TRAP"
    assert result["trade_permission"]["executable_allowed"] is False
    assert result["trade_permission"]["deny_reason"] == "LATE_CHASE_TRAP"
    assert result["trade_permission"]["permission_state"] == "DENIED"
    assert result["trade_permission"]["denied_at"] == "MARKET_TRAP"
    assert result["trade_permission"]["next_required_condition"] == "Wait for pullback/retest into a qualified trigger zone."
    assert result["market_phase"] == "LATE_CHASE_TRAP"
    assert result["allowed_action"] == "WATCH"
    assert result["forbidden_action"] == "CHASE_BUY"


def test_ideal_path_hold_or_protect_denies_execution() -> None:
    snapshot = _strong_snapshot("BUY")
    snapshot["ideal_trade_path"] = {
        "action": "PROTECT",
        "reason": "Historical path would protect here instead of opening a new entry.",
    }

    result = _second_council_read(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "WATCHING"
    assert result["block_reason"] == "IDEAL_PATH_PROTECT"
    assert result["ideal_trade_path"]["executable_allowed"] is False


def test_path_risk_weak_denies_execution() -> None:
    snapshot = _strong_snapshot("BUY")
    snapshot["path_risk"] = {"state": "WEAK", "score": 0.31}

    result = _second_council_read(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "WATCHING"
    assert result["block_reason"] == "PATH_RISK_WEAK"
    assert result["path_risk"]["state"] == "WEAK"


def test_candidate_queue_prevents_flip_flop_executable() -> None:
    council = ModelCouncilV3()
    assert council.evaluate(_strong_snapshot("BUY", frame_id=210), now_epoch=NOW)["execution"]["enabled"] is False
    assert council.evaluate(_strong_snapshot("SELL", frame_id=211), now_epoch=NOW + 0.5)["execution"]["enabled"] is False

    result = council.evaluate(_strong_snapshot("BUY", frame_id=212), now_epoch=NOW + 1.0)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "WATCHING"
    assert result["block_reason"] == "FLIP_FLOP_CONTAINED"
    assert result["trade_candidate_queue"]["flip_flop_risk"] is True


def test_timing_path_bad_denies_executable_but_allows_preparing_study() -> None:
    snapshot = _strong_snapshot("BUY")
    snapshot["time_to_reward_seconds"] = 240
    snapshot["time_to_invalidation_seconds"] = 45

    result = _second_council_read(snapshot)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "PREPARING"
    assert result["block_reason"] == "TIMING_PATH_BAD"
    assert result["trade_permission"]["prepare_allowed"] is True
    assert result["trade_permission"]["executable_allowed"] is False
    assert result["time_to_reward_invalidation"]["state"] == "BAD"
