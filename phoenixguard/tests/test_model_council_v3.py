from __future__ import annotations

from copy import deepcopy
from typing import Any

from phoenixguard.decision.model_council_v3 import (
    MODEL_COUNCIL_STUDY_SCHEMA_VERSION,
    PG_EXECUTION_PACKET_SCHEMA_VERSION,
    PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION,
    ModelCouncilV3,
    validate_execution_packet_v3,
)
from phoenixguard.execution.sequence_context import build_sequence_context_v3, sequence_context_readiness_report


NOW = 1_764_000_000.0


def _strong_snapshot(side: str = "BUY", *, frame_id: int = 101, skill_pass: bool = True) -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY"
    return {
        "session_id": "pocket-live-8788",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": frame_id,
        "capture_count": frame_id + 2,
        "state_version": frame_id + 1000,
        "input_frame_hash": f"hash_{side.lower()}_{frame_id}",
        "previous_frame_hash": f"hash_{side.lower()}_{frame_id - 1}",
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
            "max_model_latency_ms": 41,
            "queue_depth": 0,
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
            "late_chase_risk": False,
            "parabolic_risk": False,
            "post_impulse_wait_required": False,
        },
        "historical_pattern": {
            "similarity_state": "REPEATING_SUCCESSFUL_PATH",
            "best_match_setup": f"{side}_CONTINUATION_AFTER_PULLBACK",
            "historical_entry_quality": "GOOD",
            "historical_late_entry_risk": "LOW",
            "where_history_would_enter": "DEMAND_TRIGGER_ZONE" if side == "BUY" else "SUPPLY_TRIGGER_ZONE",
            "where_history_would_exit": f"BEFORE_{opposite}_ZONE",
        },
        "risk_opposing_force": {
            "side": side,
            "distance_to_opposing_force": 0.34,
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
        # Provide COMPLETE sequence context by default for tests that expect executable packets
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
        "progression": [
            {
                "stage": "context_confirmed",
                "direction": side,
                "confidence": 0.92,
            }
        ],
        "entry_progression": {
            "progression_stage": "SNIPER_READY",
            "maturity_score": 0.92,
            "progression_velocity": 0.34,
            "continuation_strength": 0.86,
            "exhaustion_risk": 0.12,
        },
        "skill_gates": [
            {"name": "continuation_strength", "score": 0.91 if skill_pass else 0.12, "pass_fail": skill_pass},
            {"name": "memory_similarity", "score": 0.88 if skill_pass else 0.20, "pass_fail": skill_pass},
        ],
    }


def _second_packet(side: str = "BUY", *, skill_pass: bool = True) -> dict[str, Any]:
    council = ModelCouncilV3()
    first = council.evaluate(_strong_snapshot(side, frame_id=100, skill_pass=skill_pass), now_epoch=NOW)
    assert first["execution"]["enabled"] is False
    return council.evaluate(_strong_snapshot(side, frame_id=101, skill_pass=skill_pass), now_epoch=NOW + 0.5)


def _high_frequency_snapshot(side: str = "BUY", *, frame_id: int = 201) -> dict[str, Any]:
    snapshot = _strong_snapshot(side, frame_id=frame_id)
    opposite = "SELL" if side == "BUY" else "BUY"
    snapshot[f"{side.lower()}_score"] = 0.64
    snapshot[f"{opposite.lower()}_score"] = 0.08
    snapshot["timing"] = {
        "state": "READY",
        "side": side,
        "expiry_seconds": 600,
        "target_time_text": "00:10:00",
        "reason": "M5 candle closed; next two-candle study window is ready.",
    }
    snapshot["execution_timing"] = {
        "state": "READY",
        "side": side,
        "lane": "HIGH_FREQUENCY_TWO_CANDLE",
        "expiry_seconds": 600,
        "recommended_expiry_seconds": 600,
        "current_flow_continuation_ready": True,
    }
    snapshot["current_candle_acceptance"] = {
        "state": "VALID",
        "phase": "VALID",
        "entry_allowed": True,
        "current_candle_closed": True,
        "close_progress": 1.0,
    }
    snapshot["current_candle_contract"] = dict(snapshot["current_candle_acceptance"])
    snapshot["high_frequency_candle_cycle"] = {
        "enabled": True,
        "ready": True,
        "lane": "HIGH_FREQUENCY_TWO_CANDLE",
        "side": side,
        "candidate_side": side,
        "confidence": 0.64,
        "current_candle_closed": True,
        "forecast_agreement": True,
        "targets_future_candle_window": True,
        "do_not_render_synthetic_candles": True,
        "uses_unseen_future_candles": False,
        "does_not_trade_seen_last_two_candles": True,
        "swing_fallback_enabled": True,
        "expiry_seconds": 600,
        "horizon_candles": 2,
        "reason": "closed M5 candle boundary accepted",
    }
    snapshot["decision_kernel"] = {
        "trade_mode": "HIGH_FREQUENCY",
        "state": "ACTIVE",
        "decision": "EXECUTABLE",
        "dominant_side": side.lower(),
        "major_trend_side": side.lower(),
        "candle_execution_side": side.lower(),
        "next_candle_bias": side.lower(),
        "target_horizon_candles": 2,
        "p_target_before_invalidation": 0.64,
        "p_trigger_next_1": 0.64,
        "p_trigger_next_3": 0.64,
    }
    return snapshot


def test_high_frequency_two_candle_lane_publishes_fixed_600s_packet() -> None:
    council = ModelCouncilV3()
    first = council.evaluate(_high_frequency_snapshot("BUY", frame_id=210), now_epoch=NOW)
    result = first if first.get("packet_type") == "PG_EXECUTION_PACKET_V3" else council.evaluate(
        _high_frequency_snapshot("BUY", frame_id=211),
        now_epoch=NOW + 0.5,
    )

    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["execution"]["side"] == "BUY"
    assert result["execution"]["expiry_seconds"] == 600
    assert result["selected_execution_lane"] == "HIGH_FREQUENCY_TWO_CANDLE"
    hf_cycle = result["model_council"]["execution_lane"]["high_frequency_candle_cycle"]
    assert hf_cycle["targets_future_candle_window"] is True
    assert hf_cycle["do_not_render_synthetic_candles"] is True
    assert hf_cycle["uses_unseen_future_candles"] is False


def test_high_frequency_open_candle_does_not_report_false_side_mismatch() -> None:
    council = ModelCouncilV3()
    snapshot = _high_frequency_snapshot("SELL", frame_id=220)
    snapshot["high_frequency_candle_cycle"].update(
        {
            "ready": False,
            "side": "HOLD",
            "candidate_side": "SELL",
            "active_candidate_side": "SELL",
            "forecast_side": "SELL",
            "current_candle_closed": False,
            "forecast_agreement": True,
            "swing_fallback_enabled": False,
            "reason": "Current M5 candle is still open.",
        }
    )

    result = council.evaluate(snapshot, now_epoch=NOW)
    lane = result["model_council"]["execution_lane"]

    assert lane["accepted"] is False
    assert "CURRENT_M5_CANDLE_NOT_CLOSED" in lane["blockers"]
    assert "TWO_CANDLE_SIDE_MISMATCH" not in lane["blockers"]


def test_model_council_resets_stability_on_symbol_switch() -> None:
    council = ModelCouncilV3()
    council.evaluate(_strong_snapshot("BUY", frame_id=300), now_epoch=NOW)
    assert council.evaluate(_strong_snapshot("BUY", frame_id=301), now_epoch=NOW + 0.5)["packet_type"] == "PG_EXECUTION_PACKET_V3"

    switched = _strong_snapshot("BUY", frame_id=302)
    switched["symbol"] = "USD/JPY OTC"
    switched["market"] = "USD/JPY OTC"
    switched["instrument_context"] = {
        "display_symbol": "USD/JPY OTC",
        "timeframe": "M5",
        "viewport_hash": "viewport-b",
        "broker_surface_hash": "broker-b",
        "paper_safe": True,
        "broker_click_safe": False,
        "session_id": "pocket-live-8788",
    }
    result = council.evaluate(switched, now_epoch=NOW + 1.0)

    assert result["execution"]["enabled"] is False
    assert result["promotion_trace"]["candidate_stable_reads"] == 1


REQUIRED_NON_EXECUTABLE_RELEASE_FIELDS = (
    "denied_at",
    "next_required",
    "release_condition",
    "candidate_id",
    "candidate_stage",
    "final_score",
    "threshold",
    "selected_lane",
    "timing_mode",
    "instrument_context_state",
)


def _assert_non_executable_release_fields(result: dict[str, Any]) -> None:
    assert result["execution"]["enabled"] is False
    trace = result["promotion_trace"]
    for field in REQUIRED_NON_EXECUTABLE_RELEASE_FIELDS:
        assert field in trace
        assert trace[field] not in (None, "", "N/A", "MISSING")
    assert trace["denied_at"] not in {"CONTEXT", "WATCHING", "N/A", "MISSING"}
    assert trace["next_required"] not in {"CONTEXT", "WATCHING", "N/A", "MISSING"}
    audit = result["study_packet"]["promotion_failure_audit_v3"]
    assert audit["schema_version"] == PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION
    assert audit == result["promotion_trace"]["promotion_failure_audit_v3"]
    assert audit == result["model_council"]["promotion_failure_audit_v3"]
    assert audit["denied_at"] == trace["denied_at"]
    assert audit["top_blocker"] == trace["denied_at"]
    assert audit["exact_field_preventing_execution_packet"]
    assert audit["next_required"] == trace["next_required"]
    assert audit["blocker_ranking"][0]["blocker"] == trace["denied_at"]


def _permission_denied_result() -> dict[str, Any]:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot in (first, second):
        snapshot["trade_permission"] = {
            "permission_state": "DENIED",
            "executable_allowed": False,
            "prepare_allowed": False,
            "deny_reason": "TRADE_PERMISSION_DENIED",
        }
    council.evaluate(first, now_epoch=NOW)
    return council.evaluate(second, now_epoch=NOW + 0.5)


def _broker_click_unsafe_result() -> dict[str, Any]:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot in (first, second):
        snapshot.update(
            {
                "symbol": "",
                "market": "",
                "ocr_symbol": "",
                "viewport_hash": "chart-viewport-a",
                "execution_mode": "broker_click",
                "instrument_identity_lock": {"user_symbol": "EUR/GBP OTC"},
            }
        )
    council.evaluate(first, now_epoch=NOW)
    return council.evaluate(second, now_epoch=NOW + 0.5)


def test_ready_timing_without_explicit_expiry_does_not_fallback_to_300() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot in (first, second):
        snapshot["timing"].pop("expiry_seconds", None)
        snapshot.pop("expiry_seconds", None)
        snapshot.pop("required_seconds", None)

    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is False
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is False
    assert result["execution"]["expiry_seconds"] == 0
    assert result["model_council"]["final_state"] == "BLOCKED_BY_RUNTIME"
    assert result["block_reason"] == "MODEL_COUNCIL_EXPLICIT_EXPIRY_MISSING"
    assert "execution_packet" not in result


def test_raw_buy_does_not_execute() -> None:
    packet = ModelCouncilV3().evaluate(
        {
            "session_id": "raw",
            "symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "frame_id": 1,
            "capture_count": 2,
            "state_version": 3,
            "input_frame_hash": "raw-buy",
            "action": "BUY",
            "execution_action": "BUY",
        },
        now_epoch=NOW,
    )

    assert packet["execution"]["enabled"] is False
    assert packet["schema_version"] == MODEL_COUNCIL_STUDY_SCHEMA_VERSION
    assert packet["model_council"]["final_state"] != "EXECUTABLE"


def test_blank_symbol_does_not_block_study_or_mark_models_stale() -> None:
    snapshot = _strong_snapshot("BUY", frame_id=100)
    snapshot["symbol"] = ""
    snapshot["market"] = ""
    snapshot["ocr_symbol"] = ""

    result = ModelCouncilV3().evaluate(snapshot, now_epoch=NOW)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] != "BLOCKED_BY_RUNTIME"
    assert result["block_reason"] is None
    assert result["instrument_context"]["display_symbol"] == ""
    assert result["instrument_context"]["ocr_symbol"] == ""
    assert result["runtime_model_health"]["all_required_models_awake"] is True


def test_blank_symbol_allows_paper_packet_when_user_locked() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    first.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "instrument_identity_lock": {"user_symbol": "EUR/GBP OTC"},
        }
    )
    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is False
    second = _strong_snapshot("BUY", frame_id=101)
    second.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "instrument_identity_lock": {"user_symbol": "EUR/GBP OTC"},
        }
    )

    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["execution"]["enabled"] is True
    assert packet["symbol"] == "EUR/GBP OTC"
    assert packet["instrument_context"]["display_symbol"] == "EUR/GBP OTC"
    assert packet["instrument_context"]["ocr_symbol"] == ""
    assert packet["instrument_context"]["paper_safe"] is True
    assert packet["instrument_context"]["broker_click_safe"] is False
    assert validate_execution_packet_v3(packet, now_epoch=NOW + 0.6).ok is True


def test_blank_symbol_blocks_broker_click_mode_when_user_locked_only() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    first.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "execution_mode": "broker_click",
            "instrument_identity_lock": {"user_symbol": "EUR/GBP OTC"},
        }
    )
    council.evaluate(first, now_epoch=NOW)
    second = _strong_snapshot("BUY", frame_id=101)
    second.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "execution_mode": "broker_click",
            "instrument_identity_lock": {"user_symbol": "EUR/GBP OTC"},
        }
    )

    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "BLOCKED_BY_RUNTIME"
    assert result["block_reason"] == "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE"
    assert result["promotion_trace"]["denied_at"] == "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE"
    assert "instrument_context.broker_click_safe=true" in result["promotion_trace"]["next_required"]
    assert result["promotion_trace"]["release_condition"] == result["promotion_trace"]["next_required"]
    assert result["promotion_trace"]["instrument_context_state"] in {"USER_PROFILE_LOCKED", "BROKER_SURFACE_LOCKED"}
    assert "execution_packet" not in result


def test_broker_click_mode_executes_when_user_profile_lock_has_v2_evidence() -> None:
    council = ModelCouncilV3()
    evidence_lock = {
        "user_symbol": "EUR/GBP OTC",
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "viewport_hash": "chart-viewport-a",
        "broker_surface_hash": "broker-a",
        "window_handle": "hwnd-1",
        "window_rect": [0, 0, 640, 420],
        "calibration_layout_id": "layout-a",
        "expected_calibration_layout_id": "layout-a",
        "window_handle_stable": True,
        "window_rect_stable": True,
        "viewport_hash_stable": True,
        "broker_surface_hash_stable": True,
        "calibration_layout_match": True,
        "session_active": True,
        "packet_fresh": True,
        "models_awake": True,
        "profile_mismatch": False,
    }
    first = _strong_snapshot("BUY", frame_id=100)
    first.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "broker_surface_hash": "broker-a",
            "execution_mode": "broker_click",
            "instrument_identity_lock": dict(evidence_lock),
        }
    )
    council.evaluate(first, now_epoch=NOW)
    second = _strong_snapshot("BUY", frame_id=101)
    second.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "broker_surface_hash": "broker-a",
            "execution_mode": "broker_click",
            "instrument_identity_lock": dict(evidence_lock),
        }
    )

    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is True
    assert result["instrument_context"]["broker_click_safe"] is True
    assert result["instrument_context"]["instrument_context_state"] == "BROKER_CLICK_SAFE"
    assert result["promotion_trace"]["release_condition"] == "none"


def test_timeframe_required_for_study() -> None:
    snapshot = _strong_snapshot("BUY", frame_id=100)
    snapshot["timeframe"] = ""

    result = ModelCouncilV3().evaluate(snapshot, now_epoch=NOW)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "BLOCKED_BY_RUNTIME"
    assert result["block_reason"] == "MISSING_TIMEFRAME"


def test_raw_sell_does_not_execute() -> None:
    packet = ModelCouncilV3().evaluate(
        {
            "session_id": "raw",
            "symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "frame_id": 1,
            "capture_count": 2,
            "state_version": 3,
            "input_frame_hash": "raw-sell",
            "action": "SELL",
            "execution_action": "SELL",
        },
        now_epoch=NOW,
    )

    assert packet["execution"]["enabled"] is False
    assert packet["execution"]["side"] is None


def test_missing_runtime_model_health_cannot_publish_executable_packet() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    first.pop("runtime_model_health")
    second.pop("runtime_model_health")

    council.evaluate(first, now_epoch=NOW)
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["schema_version"] == MODEL_COUNCIL_STUDY_SCHEMA_VERSION
    assert packet["execution"]["enabled"] is False
    assert packet["model_council"]["final_state"] == "BLOCKED_BY_RUNTIME"
    assert packet["block_reason"] == "REQUIRED_MODELS_NOT_AWAKE"
    assert "execution_packet" not in packet


def test_missing_live_integrity_cannot_publish_executable_packet() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    first.pop("live_integrity")
    second.pop("live_integrity")

    council.evaluate(first, now_epoch=NOW)
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["schema_version"] == MODEL_COUNCIL_STUDY_SCHEMA_VERSION
    assert packet["execution"]["enabled"] is False
    assert packet["model_council"]["final_state"] == "BLOCKED_BY_RUNTIME"
    assert packet["block_reason"] in {"NOT_LIVE", "CACHE_NOT_FRESH", "FRAME_NOT_ADVANCING"}
    assert "execution_packet" not in packet


def test_skill_gate_pass_does_not_execute() -> None:
    packet = ModelCouncilV3().evaluate(
        {
            "session_id": "skill-only",
            "symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "frame_id": 10,
            "capture_count": 11,
            "state_version": 12,
            "input_frame_hash": "skill-pass",
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
            },
            "action": "BUY",
            "skill_gates": [
                {"name": "all_green", "score": 0.99, "pass_fail": True},
                {"name": "memory_high", "score": 0.98, "pass_fail": True},
            ],
        },
        now_epoch=NOW,
    )

    assert packet["execution"]["enabled"] is False
    assert packet["contributors"]["contributors_are_diagnostic"] is True
    assert packet["block_reason"] is None


def test_skill_gate_fail_does_not_directly_block() -> None:
    packet = _second_packet("BUY", skill_pass=False)

    assert packet["execution"]["enabled"] is True
    assert packet["block_reason"] != "SKILL_GATE_FAILED"
    assert all(row["role"] == "DIAGNOSTIC_CONTRIBUTOR_ONLY" for row in packet["contributors"]["skill_gates"])


def test_buy_and_sell_cannot_both_be_executable() -> None:
    council = ModelCouncilV3()
    packet = council.evaluate(
        {
            **_strong_snapshot("BUY"),
            "buy_score": 0.91,
            "sell_score": 0.90,
            "conflict_score": 0.80,
            "buy_executable": True,
            "sell_executable": True,
        },
        now_epoch=NOW,
    )

    assert packet["execution"]["enabled"] is False
    assert packet["model_council"]["final_state"] == "CONFLICT"
    assert packet["model_council"]["final_side"] is None
    assert packet["block_reason"] == "BUY_AND_SELL_EXECUTABLE_CONFLICT"


def test_flip_flop_stays_watching() -> None:
    council = ModelCouncilV3()
    council.evaluate(_strong_snapshot("BUY", frame_id=100), now_epoch=NOW)
    council.evaluate(_strong_snapshot("SELL", frame_id=101), now_epoch=NOW + 0.5)
    packet = council.evaluate(_strong_snapshot("BUY", frame_id=102), now_epoch=NOW + 1.0)

    assert packet["execution"]["enabled"] is False
    assert packet["block_reason"] == "FLIP_FLOP_CONTAINED"
    assert packet["model_council"]["final_state"] == "WATCHING"
    assert packet["promotion_trace"]["candidate_flip_count_10s"] >= 2


def test_raw_side_flip_does_not_reset_stable_candidate() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    first.update({"execution_action": "SELL", "action": "SELL", "buy_score": 0.88, "sell_score": 0.04})
    second = _strong_snapshot("BUY", frame_id=101)
    second.update({"execution_action": "BUY", "action": "BUY", "buy_score": 0.89, "sell_score": 0.03})

    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is False
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "BUY"
    assert packet["promotion_trace"]["raw_flip_count_10s"] == 1
    assert packet["promotion_trace"]["candidate_flip_count_10s"] == 0
    assert packet["promotion_trace"]["candidate_stable_reads"] >= 2


def test_stable_candidate_releases_flip_flop_containment() -> None:
    council = ModelCouncilV3()
    council.evaluate(_strong_snapshot("BUY", frame_id=100), now_epoch=NOW)
    council.evaluate(_strong_snapshot("SELL", frame_id=101), now_epoch=NOW + 0.5)
    contained = council.evaluate(_strong_snapshot("BUY", frame_id=102), now_epoch=NOW + 1.0)
    packet = council.evaluate(_strong_snapshot("BUY", frame_id=103), now_epoch=NOW + 1.5)

    assert contained["block_reason"] == "FLIP_FLOP_CONTAINED"
    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "BUY"
    assert packet["promotion_trace"]["release_allowed"] is True
    assert packet["model_council"]["flip_flop_state"] == "FLIP_FLOP_RELEASED"


def test_model_council_publishes_study_packet_for_watching_state() -> None:
    result = ModelCouncilV3().evaluate(_strong_snapshot("BUY", frame_id=100), now_epoch=NOW)

    assert result["execution"]["enabled"] is False
    assert result["schema_version"] == MODEL_COUNCIL_STUDY_SCHEMA_VERSION
    assert result["packet_id"].startswith("pgpkt_")
    assert result["packet_type"] == "STUDY_PACKET"
    assert result["study_packet"]["packet_id"] == result["packet_id"]
    assert result["study_packet"]["packet_type"] == "STUDY_PACKET"
    assert result["study_packet"]["execution"]["state"] in {"PREPARING", "WATCHING"}
    assert result["study_packet"]["execution"]["side"] == "BUY"
    assert result["promotion_trace"]["promotion_result"] in {"PREPARING", "WATCHING"}
    assert result["promotion_trace"]["packet_result"] == "STUDY_PACKET_PUBLISHED"
    assert result["promotion_trace"]["late_chase_detected"] is False
    assert result["final_execution_score"] >= 0.0
    assert result["study_packet"]["true_blocker"] != "Late-chase class not detected."


def test_every_non_executable_state_has_denied_at() -> None:
    results = [
        ModelCouncilV3().evaluate(
            {
                "session_id": "raw-watch",
                "symbol": "EUR/GBP OTC",
                "timeframe": "M5",
                "frame_id": 1,
                "capture_count": 2,
                "state_version": 3,
                "input_frame_hash": "raw-watch",
            },
            now_epoch=NOW,
        ),
        ModelCouncilV3().evaluate(_strong_snapshot("BUY", frame_id=100), now_epoch=NOW),
        _permission_denied_result(),
        _broker_click_unsafe_result(),
    ]

    for result in results:
        assert result["execution"]["enabled"] is False
        assert result["promotion_trace"]["denied_at"] not in (None, "", "CONTEXT", "WATCHING", "N/A", "MISSING")


def test_every_non_executable_state_has_next_required() -> None:
    results = [
        ModelCouncilV3().evaluate(_strong_snapshot("BUY", frame_id=100), now_epoch=NOW),
        _permission_denied_result(),
        _broker_click_unsafe_result(),
    ]

    for result in results:
        _assert_non_executable_release_fields(result)


def test_every_non_executable_study_packet_has_promotion_failure_audit() -> None:
    results = [
        ModelCouncilV3().evaluate(_strong_snapshot("BUY", frame_id=100), now_epoch=NOW),
        _permission_denied_result(),
        _broker_click_unsafe_result(),
    ]

    for result in results:
        audit = result["study_packet"]["promotion_failure_audit_v3"]
        assert audit["schema_version"] == PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION
        assert audit["packet_result"] == "STUDY_PACKET_PUBLISHED"
        assert audit["denied_at"] == result["promotion_trace"]["denied_at"]
        assert audit["blocker_ranking"][0]["blocker"] == result["promotion_trace"]["denied_at"]


def test_sequence_context_blocker_reports_exact_rejected_fields() -> None:
    snapshot = _strong_snapshot("SELL", frame_id=100)
    snapshot.update(
        {
            "sequence_length": 12,
            "frames_used": 12,
            "frames_received": 12,
            "sequence_confidence": 0.41,
            "sequence_status": "PARTIAL_SEQUENCE",
        }
    )

    result = ModelCouncilV3().evaluate(snapshot, now_epoch=NOW)
    trace = result["promotion_trace"]
    readiness = trace["sequence_context_readiness"]

    assert trace["denied_at"] == "SEQUENCE_CONTEXT"
    assert trace["next_required"].startswith("sequence context incomplete:")
    assert trace["next_required"] != "full sequence context required"
    assert "sequence_length=12 required >=50" in trace["next_required"]
    assert readiness["sequence_length"] == 12
    assert readiness["frames_received"] == 12
    assert readiness["frames_used"] == 12
    assert readiness["minimum_required_sequence_length"] == 50
    assert readiness["minimum_required_box_history_len"] == 1
    assert readiness["minimum_required_progression_len"] == 1
    audit = result["study_packet"]["promotion_failure_audit_v3"]
    assert audit["top_blocker"] == "SEQUENCE_CONTEXT"
    assert audit["exact_field_preventing_execution_packet"] == "model_council_resolver"
    assert {row["field"] for row in readiness["blocking_failures"]} >= {
        "sequence_status",
        "sequence_length",
        "sequence_confidence",
    }


def test_sequence_context_reads_nested_tracker_history() -> None:
    snapshot = _strong_snapshot("SELL", frame_id=120)
    snapshot.pop("historical_structure")
    snapshot.pop("progression")
    snapshot.pop("entry_progression")
    snapshot["tracking_summary"] = {
        "visible_candle_count": 50,
        "historical_structure": [
            {
                "key": "history_1",
                "label": "H1 SELL",
                "bbox": [0, 74, 219, 434],
                "direction": "SELL",
                "candle_count": 6,
            }
        ],
        "current_box": {
            "key": "current",
            "label": "CURRENT",
            "bbox": [1089, 640, 1241, 830],
            "direction": "BUY",
            "candle_count": 4,
        },
        "entry_state": "SNIPER_READY",
        "continuation_score": 0.82,
        "reversal_score": 0.11,
    }

    context = build_sequence_context_v3(snapshot)
    readiness = sequence_context_readiness_report(context)

    assert context.sequence_status == "COMPLETE"
    assert len(context.box_history) == 1
    assert len(context.progression) == 1
    assert context.entry_progression["source"] == "sequence_context_memory_compression"
    assert readiness["ready"] is True


def test_sequence_context_promotes_tracked_candle_history_when_live_snapshot_marks_partial() -> None:
    snapshot = _strong_snapshot("SELL", frame_id=140)
    snapshot.pop("historical_structure")
    snapshot.pop("progression")
    snapshot.pop("entry_progression")
    snapshot["sequence_status"] = "PARTIAL_SEQUENCE"
    snapshot["tracking_summary"] = {
        "visible_candle_count": 20,
        "tracked_candles": [
            {
                "track_id": index,
                "bbox": [index * 4, 80 + index, index * 4 + 3, 160 + index],
                "direction": "SELL" if index % 3 else "BUY",
                "color": "magenta" if index % 3 else "green",
                "price_proxy": 0.48 + index * 0.002,
                "body_height_pct": 0.54,
                "normalized_x": index / 20,
                "normalized_y": 0.58,
            }
            for index in range(1, 21)
        ],
    }

    context = build_sequence_context_v3(snapshot)
    readiness = sequence_context_readiness_report(context)

    assert context.sequence_status == "COMPLETE"
    assert len(context.box_history) == 20
    assert len(context.progression) == 20
    assert context.entry_progression["source"] == "sequence_context_memory_compression"
    assert readiness["ready"] is True


def test_sequence_context_derives_confidence_from_tracked_structure_when_score_missing() -> None:
    snapshot = _strong_snapshot("BUY", frame_id=150)
    snapshot.pop("sequence_confidence")
    snapshot.pop("confidence", None)
    snapshot["sequence_status"] = "PARTIAL_SEQUENCE"
    snapshot["historical_structure"] = [
        {"key": "demand_1", "label": "DEMAND", "direction": "BUY", "bbox": [20, 240, 220, 320]},
        {"key": "pullback_1", "label": "PULLBACK", "direction": "BUY", "bbox": [240, 220, 410, 300]},
    ]
    snapshot["progression"] = [
        {"stage": "impulse", "direction": "BUY"},
        {"stage": "pullback", "direction": "BUY"},
    ]

    context = build_sequence_context_v3(snapshot)
    readiness = sequence_context_readiness_report(context)

    assert context.sequence_status == "COMPLETE"
    assert context.sequence_confidence >= 0.75
    assert readiness["ready"] is True


def test_flip_flop_contained_has_release_condition() -> None:
    council = ModelCouncilV3()
    council.evaluate(_strong_snapshot("BUY", frame_id=100), now_epoch=NOW)
    council.evaluate(_strong_snapshot("SELL", frame_id=101), now_epoch=NOW + 0.5)
    result = council.evaluate(_strong_snapshot("BUY", frame_id=102), now_epoch=NOW + 1.0)

    assert result["promotion_trace"]["release_state"] == "FLIP_FLOP_CONTAINED"
    assert result["promotion_trace"]["denied_at"] == "FLIP_FLOP_CONTAINED"
    assert "candidate_stage=CANDIDATE_STABLE" in result["promotion_trace"]["release_condition"]
    _assert_non_executable_release_fields(result)


def test_context_block_has_specific_context_field() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("SELL", frame_id=100)
    second = _strong_snapshot("SELL", frame_id=101)
    for snapshot in (first, second):
        snapshot["sell_score"] = 0.91
        snapshot["buy_score"] = 0.01
        snapshot["execution_threshold"] = 0.55
        snapshot["lane_thresholds"] = {"SNIPER_ZONE_ENTRY": 0.55}
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["current_location"] = "MIDDLE_DANGER"
        snapshot["entry_quality"] = "ACCEPTABLE_ENTRY"
        snapshot["timing"]["state"] = "READY"
        snapshot["latest_signal"] = {"entry_state": "WAIT_FOR_TRIGGER"}
        snapshot["tracking_summary"] = {"entry_state": "WAIT_FOR_TRIGGER"}

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["promotion_trace"]["release_state"] == "CONTEXT_BLOCKED"
    assert "selected_lane=" in result["promotion_trace"]["next_required"]
    assert ".structure_ok=true" in result["promotion_trace"]["next_required"]
    _assert_non_executable_release_fields(result)


def test_score_pass_without_packet_reports_true_blocker() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("SELL", frame_id=100)
    second = _strong_snapshot("SELL", frame_id=101)
    for snapshot in (first, second):
        snapshot["sell_score"] = 0.91
        snapshot["buy_score"] = 0.01
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["current_location"] = "MIDDLE_DANGER"
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["timing"]["state"] = "WAIT"
        snapshot["latest_signal"] = {"entry_state": "WAIT_FOR_TRIGGER"}
        snapshot["tracking_summary"] = {"entry_state": "WAIT_FOR_TRIGGER"}

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is False
    assert "execution_packet" not in result
    assert result["promotion_trace"]["execution_lane"]["actual_score"] >= result["promotion_trace"]["execution_lane"]["required_score"]
    assert result["promotion_trace"]["true_blocker"] == "NO_EXECUTION_LANE_ACCEPTED"
    assert result["promotion_trace"]["denied_at"] == "NO_EXECUTION_LANE_ACCEPTED"


def test_instrument_context_wait_reports_broker_click_safe_false() -> None:
    result = _broker_click_unsafe_result()

    assert result["promotion_trace"]["release_state"] == "INSTRUMENT_CONTEXT_WAIT"
    assert result["promotion_trace"]["instrument_context_broker_click_safe"] is False
    assert "instrument_context.broker_click_safe=false" in result["promotion_trace"]["next_required"]
    assert "instrument_context.broker_click_safe=true" in result["promotion_trace"]["release_condition"]
    _assert_non_executable_release_fields(result)


def test_executable_ready_requires_enter_now_timing() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot in (first, second):
        snapshot["current_candle"] = {
            "candle_phase": "LATE_CANDLE",
            "seconds_elapsed": 270,
            "seconds_remaining": 30,
            "too_late": True,
            "entry_allowed": False,
        }
        snapshot["latest_signal"] = {"entry_state": "SNIPER_READY"}
        snapshot["tracking_summary"] = {"entry_state": "SNIPER_READY"}

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is False
    assert result["promotion_trace"]["timing_mode"] != "ENTER_NOW"
    assert result["promotion_trace"]["release_state"] == "TIMING_WAIT"
    assert "execution_packet" not in result


def test_executable_ready_requires_broker_click_safe() -> None:
    result = _broker_click_unsafe_result()

    assert result["execution"]["enabled"] is False
    assert result["promotion_trace"]["release_state"] == "INSTRUMENT_CONTEXT_WAIT"
    assert result["promotion_trace"]["true_blocker"] == "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE"
    assert "execution_packet" not in result


def test_execution_packet_publishes_after_all_release_conditions_pass() -> None:
    packet = _second_packet("BUY")

    assert packet["execution"]["enabled"] is True
    assert packet["packet_id"]
    assert packet["promotion_trace"]["packet_result"] == "PG_EXECUTION_PACKET_V3_PUBLISHED"
    assert packet["promotion_trace"]["candidate_stage"] == "EXECUTION_PACKET_PUBLISHED"
    assert packet["promotion_trace"]["timing_mode"] == "ENTER_NOW"
    assert packet["promotion_trace"]["final_score"] >= packet["promotion_trace"]["threshold"]
    assert packet["promotion_trace"]["lane_accepted"] is True
    assert packet["promotion_trace"]["release_condition"] == "none"


def test_true_blocker_reported_not_generic_late_chase_reason() -> None:
    result = ModelCouncilV3().evaluate(_strong_snapshot("SELL", frame_id=100), now_epoch=NOW)

    assert result["execution"]["enabled"] is False
    assert result["promotion_trace"]["true_blocker"] != "LATE-CHASE CLASS NOT DETECTED."
    assert result["model_council"]["arbitration_reason"] != "Late-chase class not detected."
    assert result["study_packet"]["true_blocker"] == result["promotion_trace"]["true_blocker"]


def test_stable_council_decision_becomes_executable() -> None:
    packet = _second_packet("BUY")

    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["state"] == "EXECUTABLE"
    assert packet["execution"]["side"] == "BUY"
    assert packet["model_council"]["final_state"] == "EXECUTABLE"
    assert packet["model_council"]["final_side"] == "BUY"
    assert packet["timing_decision"]["timing_mode"] == "ENTER_NOW"
    assert packet["timing_decision"]["entry_now_allowed"] is True
    assert packet["timing_decision"]["path_class"] in {"DIRECT_CONTINUATION", "PULLBACK_THEN_CONTINUATION"}
    assert packet["timing_decision"]["timing_forecast"]["entry_now_quality"] == "GOOD"
    assert packet["council_debate"]["protocol_version"] == "COUNCIL_DEBATE_PROTOCOL_V1"
    assert packet["council_debate"]["arbitration"] == "EXECUTABLE"
    assert validate_execution_packet_v3(packet, now_epoch=NOW + 0.6).ok is True


def test_timing_wait_blocks_execution_packet() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot in (first, second):
        snapshot["timing"] = {
            "state": "WAIT",
            "side": "BUY",
            "expiry_seconds": 300,
            "target_time_text": "00:05:00",
            "current_candle_phase": "LATE_CANDLE",
            "seconds_elapsed": 270,
            "seconds_remaining": 30,
        }
        snapshot["current_candle"] = {
            "candle_phase": "LATE_CANDLE",
            "seconds_elapsed": 270,
            "seconds_remaining": 30,
            "too_late": True,
            "entry_allowed": False,
        }
        snapshot["latest_signal"] = {"entry_state": "SNIPER_READY"}
        snapshot["tracking_summary"] = {"entry_state": "SNIPER_READY"}

    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is False
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is False
    assert result["timing_decision"]["entry_now_allowed"] is False
    assert result["timing_decision"]["timing_mode"] in {"SKIP_LATE_ENTRY", "WAIT_FOR_CANDLE_CLOSE_BEHAVIOUR"}
    assert result["timing_decision"]["path_class"] in {"LATE_CHASE_REVERSAL_RISK", "ADVERSE_FIRST_THEN_TARGET"}
    assert result["model_council"]["final_state"] in {"WATCHING", "PREPARING"}
    assert result["study_packet"]["timing_decision"]["entry_now_allowed"] is False


def test_local_breakdown_lane_can_execute_without_sniper_zone() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("SELL", frame_id=100)
    second = _strong_snapshot("SELL", frame_id=101)
    for snapshot in (first, second):
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["current_location"] = "MIDDLE_DANGER"
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["timing"]["state"] = "WAIT"
        snapshot["latest_signal"] = {
            "entry_state": "SNIPER_READY",
            "microstructure_break": True,
            "execution_lane": "LOCAL_BREAKDOWN_CONTINUATION",
        }
        snapshot["tracking_summary"] = {
            "entry_state": "SNIPER_READY",
            "local_direction": "SELL",
            "global_direction": "BUY",
        }
        snapshot["global_structure"]["global_side"] = "BUY"
        snapshot["local_micro_structure"]["local_side"] = "SELL"

    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is False
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "SELL"
    assert packet["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    assert packet["promotion_trace"]["lane_accepted"] is True
    assert packet["promotion_trace"]["permission_override_allowed"] is True
    assert packet["promotion_trace"]["raw_timing_ready"] is False
    assert packet["promotion_trace"]["timing_ready"] is True


def test_execution_timing_current_flow_feeds_local_breakdown_lane() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("SELL", frame_id=100)
    second = _strong_snapshot("SELL", frame_id=101)
    for snapshot in (first, second):
        snapshot["sell_score"] = 0.94
        snapshot["buy_score"] = 0.02
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["current_location"] = "MIDDLE_SAFE"
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["timing"]["state"] = "WAIT"
        snapshot["execution_timing"] = {
            "state": "WAIT",
            "side": "SELL",
            "lane": "LIVE_MARKET_FLOW",
            "expiry_seconds": 300,
            "current_flow_continuation_ready": True,
            "breakout_confirmation": True,
        }
        snapshot["latest_signal"] = {"entry_state": "WAIT_FOR_TRIGGER"}
        snapshot["tracking_summary"] = {"entry_state": "WAIT_FOR_TRIGGER", "local_direction": "SELL"}

    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is False
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["execution"]["enabled"] is True
    assert packet["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    assert packet["promotion_trace"]["execution_lane"]["accepted"] is True
    assert packet["promotion_trace"]["current_candle_acceptance"]["entry_allowed"] is True


def test_score_above_threshold_without_lane_stays_study_packet() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("SELL", frame_id=100)
    second = _strong_snapshot("SELL", frame_id=101)
    for snapshot in (first, second):
        snapshot["sell_score"] = 0.91
        snapshot["buy_score"] = 0.01
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["current_location"] = "MIDDLE_DANGER"
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["timing"]["state"] = "WAIT"
        snapshot["latest_signal"] = {"entry_state": "WAIT_FOR_TRIGGER"}
        snapshot["tracking_summary"] = {"entry_state": "WAIT_FOR_TRIGGER"}

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is False
    assert result["packet_type"] == "STUDY_PACKET"
    assert result["promotion_trace"]["true_blocker"] == "NO_EXECUTION_LANE_ACCEPTED"
    assert result["promotion_trace"]["execution_lane"]["accepted"] is False
    assert result["promotion_trace"]["missed_opportunity"]["side"] == "SELL"
    assert result["promotion_trace"]["missed_opportunity"]["lane_score"] >= result["promotion_trace"]["missed_opportunity"]["lane_threshold"]
    assert result["promotion_trace"]["missed_opportunity"]["future_move_confirmed"] is None


def test_aligned_buy_structure_overrides_stale_sell_pullback_reload() -> None:
    council = ModelCouncilV3()
    snapshot = _strong_snapshot("BUY", frame_id=101)
    snapshot["buy_score"] = 0.0
    snapshot["sell_score"] = 1.0
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"].update(
        {
            "global_side": "BUY",
            "local_side": "BUY",
            "dominant_side": "SELL",
            "inside_valid_trigger_zone": False,
            "current_location": "MIDDLE_DANGER",
            "is_late_chase": True,
            "is_steep_angle_break_risk": True,
            "pullback_not_confirmed": True,
            "entry_quality_state": "BAD_NOW",
            "trade_permission_deny_reason": "LATE_CHASE_TRAP",
        }
    )
    snapshot["timing"] = {
        "state": "READY",
        "side": "BUY",
        "expiry_seconds": 300,
        "target_time_text": "00:05:00",
    }

    packet = council.evaluate(snapshot, now_epoch=NOW)

    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "BUY"
    assert packet["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    assert packet["promotion_trace"]["stale_dominant_overridden"] is True
    assert packet["promotion_trace"]["execution_lane"]["reversal_capture_mature"] is True


def test_mature_high_score_directional_flow_publishes_momentum_packet() -> None:
    council = ModelCouncilV3()
    snapshots = [_strong_snapshot("BUY", frame_id=100 + index) for index in range(3)]
    for snapshot in snapshots:
        snapshot["buy_score"] = 0.91
        snapshot["sell_score"] = 0.0
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"].update(
            {
                "global_side": "BUY",
                "local_side": "BUY",
                "dominant_side": "BUY",
                "inside_valid_trigger_zone": False,
                "current_location": "MIDDLE_DANGER",
                "opposing_force_distance_ok": True,
                "is_late_chase": False,
                "is_steep_angle_break_risk": False,
                "is_continuation_confirmed": False,
            }
        )
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["timing"] = {
            "state": "WAIT",
            "side": "BUY",
            "expiry_seconds": 300,
            "target_time_text": "00:05:00",
        }

    council.evaluate(snapshots[0], now_epoch=NOW)
    council.evaluate(snapshots[1], now_epoch=NOW + 0.5)
    packet = council.evaluate(snapshots[2], now_epoch=NOW + 1.0)

    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "BUY"
    assert packet["selected_execution_lane"] == "MOMENTUM_ACCEPTANCE_ENTRY"
    assert packet["promotion_trace"]["mature_directional_flow_ready"] is True
    assert packet["promotion_trace"]["permission_override_allowed"] is True


def test_momentum_acceptance_requires_high_lane_score() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot, score in ((first, 0.78), (second, 0.95)):
        snapshot["buy_score"] = score
        snapshot["sell_score"] = 0.01
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["current_location"] = "MIDDLE_SAFE"
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["timing"]["state"] = "WAIT"
        snapshot["path_risk"] = {"state": "STRONG", "score": 0.84, "executable_allowed": True}
        snapshot["latest_signal"] = {"entry_state": "ACTIVE"}
        snapshot["tracking_summary"] = {"entry_state": "ACTIVE", "local_direction": "BUY"}

    first_result = council.evaluate(first, now_epoch=NOW)
    assert first_result["execution"]["enabled"] is False
    assert first_result["promotion_trace"]["execution_lane"]["accepted"] is False

    packet = council.evaluate(second, now_epoch=NOW + 0.5)
    assert packet["execution"]["enabled"] is True
    assert packet["selected_execution_lane"] == "MOMENTUM_ACCEPTANCE_ENTRY"


def test_execution_packet_v3_contains_required_fields() -> None:
    packet = _second_packet("SELL")

    for field in (
        "schema_version",
        "packet_id",
        "session_id",
        "symbol",
        "timeframe",
        "frame_id",
        "capture_count",
        "state_version",
        "created_epoch",
        "valid_until_epoch",
        "live_integrity",
        "execution",
        "model_council",
        "market_context",
        "angle_context",
        "history_context",
        "runtime_model_health",
        "instrument_context",
        "symbol_context",
        "market_reality",
        "entry_quality",
        "trade_permission",
        "market_trap",
        "ideal_trade_path",
        "path_risk",
        "regime_playbook",
        "current_candle_contract",
        "market_listening_stream",
        "trade_candidate_queue",
        "council_debate",
        "promotion_trace",
        "block_reason",
    ):
        assert field in packet
    assert packet["schema_version"] == PG_EXECUTION_PACKET_SCHEMA_VERSION
    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["execution"]["amount_action"] == "DO_NOT_CHANGE_AMOUNT"
    assert packet["execution"]["time_sequence"]["target_text"] == "00:05:00"
    assert packet["model_council"]["contributors_are_diagnostic"] is True
    assert packet["promotion_trace"]["promotion_result"] == "EXECUTABLE_PACKET_CREATED"
    assert packet["instrument_context"]["identity_state"] == "IDENTITY_CONFIRMED"
    assert packet["instrument_context"]["display_symbol"] == "EUR/GBP OTC"
    assert packet["instrument_context"]["timeframe"] == "M5"


def test_valid_until_epoch_enforced() -> None:
    packet = _second_packet("BUY")
    expired = validate_execution_packet_v3(packet, now_epoch=float(packet["valid_until_epoch"]) + 0.001)

    assert expired.ok is False
    assert "PACKET_EXPIRED" in expired.reason_codes


def test_model_council_final_side_required() -> None:
    packet = deepcopy(_second_packet("BUY"))
    packet["model_council"]["final_side"] = None

    result = validate_execution_packet_v3(packet, now_epoch=NOW + 0.6)

    assert result.ok is False
    assert "MODEL_COUNCIL_FINAL_SIDE_REQUIRED" in result.reason_codes
