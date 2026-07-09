from __future__ import annotations
import pytest

from copy import deepcopy
from typing import Any, Callable, Mapping, cast

from phoenixguard.decision import model_council_v3 as model_council_module
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
    if first["execution"]["enabled"] is True:
        return first
    return council.evaluate(_strong_snapshot(side, frame_id=101, skill_pass=skill_pass), now_epoch=NOW + 0.5)


def test_model_council_carries_astar_authorization_survival_trace() -> None:
    result = _second_packet("BUY")

    allowance = cast(Mapping[str, Any], result["allowance_package"])
    maturity = cast(Mapping[str, Any], result["opportunity_maturity"])
    trace = cast(Mapping[str, Any], allowance["authorization_survival_trace_v3"])

    assert allowance["astar_decision_state_v3"]["schema_version"] == "PG_ASTAR_DECISION_STATE_V3"
    assert maturity["authorization_survival_trace_v3"] == trace
    assert trace["final_state"] in {"ENTER_NOW", "PREPARING", "BLOCKED_BY_RUNTIME", "WATCHING"}
    assert "trace_steps" in trace


def _high_frequency_snapshot(side: str = "BUY", *, frame_id: int = 201) -> dict[str, Any]:
    snapshot = _strong_snapshot(side, frame_id=frame_id)
    opposite = "SELL" if side == "BUY" else "BUY"
    snapshot[f"{side.lower()}_score"] = 0.86
    snapshot[f"{opposite.lower()}_score"] = 0.08
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"]["current_location"] = "MIDDLE_SAFE"
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
    snapshot["current_candle_contract"] = dict(cast(Mapping[str, Any], snapshot["current_candle_acceptance"]))
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
        "p_target_before_invalidation": 0.78,
        "p_trigger_next_1": 0.72,
        "p_trigger_next_3": 0.70,
    }
    return snapshot


def _wave_riding_snapshot(
    side: str = "BUY",
    *,
    frame_id: int = 230,
    strict_high_frequency: bool = False,
) -> dict[str, Any]:
    snapshot = _strong_snapshot(side, frame_id=frame_id)
    opposite = "SELL" if side == "BUY" else "BUY"
    snapshot[f"{side.lower()}_score"] = 0.88
    snapshot[f"{opposite.lower()}_score"] = 0.02
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"].update(
        {
            "inside_valid_trigger_zone": False,
            "current_location": "MIDDLE_SAFE",
            "opposing_force_distance_ok": True,
            "is_late_chase": False,
            "is_steep_angle_break_risk": False,
            "is_continuation_confirmed": True,
        }
    )
    snapshot["entry_quality"] = "EARLY_WATCH"
    snapshot["timing"] = {
        "state": "READY",
        "side": side,
        "expiry_seconds": 300,
        "target_time_text": "00:05:00",
        "reason": "Current flow has reclaimed with clear path.",
    }
    snapshot["current_candle_acceptance"] = {
        "state": "VALID",
        "phase": "VALID",
        "entry_allowed": True,
        "current_candle_closed": True,
        "close_progress": 1.0,
    }
    snapshot["current_candle_contract"] = dict(cast(Mapping[str, Any], snapshot["current_candle_acceptance"]))
    snapshot["execution_timing"] = {
        "state": "READY",
        "side": side,
        "lane": "HIGH_FREQUENCY_TWO_CANDLE" if strict_high_frequency else "WAVE_RIDING_CONTINUATION",
        "expiry_seconds": 300,
        "recommended_expiry_seconds": 300,
        "current_flow_continuation_ready": True,
        "current_flow_direction_confirmed": True,
        "clear_path_score": 0.84,
        "p_target_before_invalidation": 0.78,
        "p_trigger_next_1": 0.72,
        "current_flow_conflicts": [],
    }
    snapshot["decision_kernel"] = {
        "trade_mode": "LIVE_MARKET_FLOW",
        "state": "ACTIVE",
        "decision": "EXECUTABLE",
        "dominant_side": side.lower(),
        "major_trend_side": side.lower(),
        "candle_execution_side": side.lower(),
        "next_candle_bias": side.lower(),
        "p_target_before_invalidation": 0.78,
        "p_trigger_next_1": 0.72,
        "p_trigger_next_3": 0.70,
    }
    snapshot["latest_signal"] = {"entry_state": "WAIT_FOR_TRIGGER"}
    snapshot["tracking_summary"] = {"entry_state": "WAIT_FOR_TRIGGER", "local_direction": side}
    if strict_high_frequency:
        snapshot["high_frequency_candle_cycle"] = {
            "enabled": True,
            "ready": False,
            "lane": "HIGH_FREQUENCY_TWO_CANDLE",
            "side": side,
            "candidate_side": side,
            "confidence": 0.52,
            "current_candle_closed": False,
            "forecast_agreement": False,
            "targets_future_candle_window": True,
            "swing_fallback_enabled": False,
            "expiry_seconds": 600,
            "horizon_candles": 2,
            "reason": "Current M5 candle is still open.",
        }
    return snapshot


def test_high_frequency_two_candle_contributes_to_local_breakdown_without_lane_authority() -> None:
    council = ModelCouncilV3()
    first = council.evaluate(_high_frequency_snapshot("BUY", frame_id=210), now_epoch=NOW)
    result = first if first.get("packet_type") == "PG_EXECUTION_PACKET_V3" else council.evaluate(
        _high_frequency_snapshot("BUY", frame_id=211),
        now_epoch=NOW + 0.5,
    )

    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["execution"]["side"] == "BUY"
    assert result["execution"]["expiry_seconds"] >= 60 * 60
    assert result["allowance_package"]["entry_window"]["duration_sec"] == 600
    assert result["allowance_package"]["thesis_horizon"]["expected_candle_count"] >= 12
    assert result["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    lane = result["model_council"]["execution_lane"]
    assert "HIGH_FREQUENCY_TWO_CANDLE" not in lane["accepted_lanes"]
    contribution = lane["high_frequency_contribution"]
    assert contribution["execution_authority"] is False
    assert contribution["lane_authority"] is False
    assert contribution["status"] == "CONTRIBUTING"
    hf_cycle = contribution["high_frequency_candle_cycle"]
    assert hf_cycle["targets_future_candle_window"] is True
    assert hf_cycle["do_not_render_synthetic_candles"] is True
    assert hf_cycle["uses_unseen_future_candles"] is False


def test_high_frequency_two_candle_requires_local_reclaim_confirmation() -> None:
    snapshot = _high_frequency_snapshot("BUY", frame_id=215)
    snapshot["local_micro_structure"]["local_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "SELL"
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["execution_timing"]["current_flow_continuation_ready"] = False

    result = ModelCouncilV3().evaluate(snapshot, now_epoch=NOW)
    lane = result["model_council"]["execution_lane"]

    assert result["execution"]["enabled"] is False
    assert lane["accepted"] is False
    assert lane["high_frequency_contribution"]["lane_authority"] is False
    assert "LOCAL_RECLAIM_NOT_CONFIRMED" in lane["high_frequency_contribution"]["blockers"]


def test_reasoning_wait_for_pullback_is_contributor_when_playbook_authorizes_high_frequency_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def _wait_for_pullback_reasoning(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "arbitration": {
                "coherence_score": 0.82,
                "state": "WAIT_FOR_PULLBACK",
                "side": "BUY",
            },
            "final_reasoning_decision": {
                "side": "BUY",
                "decision": "WAIT_FOR_PULLBACK",
                "confidence": 0.82,
                "play": "TREND_CONTINUATION",
                "regime": "PULLBACK_PHASE_TRENDING_UP",
                "price_location": "LOCAL_HIGH",
                "timing_mode": "ENTER_NOW",
                "reason": "Macro BUY remains valid, but local pullback has not reclaimed.",
            },
            "bad_entry_filter": {},
            "model_role_outputs": [],
        }

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.analyze_reasoning_arbitration_v3",
        _wait_for_pullback_reasoning,
    )
    council = ModelCouncilV3()
    council.evaluate(_high_frequency_snapshot("BUY", frame_id=216), now_epoch=NOW)
    result = council.evaluate(_high_frequency_snapshot("BUY", frame_id=217), now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is True
    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["promotion_trace"]["execution_lane"]["accepted"] is True
    assert result["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    assert result["promotion_trace"]["execution_lane"]["high_frequency_contribution"]["lane_authority"] is False
    assert result["promotion_trace"]["true_blocker"] == "NONE"
    assert result["promotion_trace"]["reasoning_execution_blocked"] is True
    allowance = result["allowance_package"]
    assert allowance["execution_authority"] == "PLAYBOOK_FINAL_DECIDER_V3"
    assert allowance["packet_authority"] == "PG_EXECUTION_PACKET_V3"
    assert allowance["model_council_role"] == "MODEL_COUNCIL_CONTRIBUTOR_GATE_V3"
    assert allowance["playbook_authorized"] is True


def test_intraday_enter_now_package_overrides_soft_pullback_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    def _wait_for_pullback_reasoning(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "arbitration": {
                "coherence_score": 0.84,
                "state": "WAIT_FOR_PULLBACK",
                "side": "BUY",
            },
            "final_reasoning_decision": {
                "side": "BUY",
                "decision": "WAIT_FOR_PULLBACK",
                "confidence": 0.84,
                "play": "TREND_CONTINUATION",
                "regime": "PULLBACK_RECLAIM",
                "price_location": "MIDDLE_SAFE",
                "timing_mode": "ENTER_NOW",
                "reason": "Swing context is waiting, but the current sniper entry is ready.",
            },
            "bad_entry_filter": {
                "active": False,
                "class": "NONE",
                "severity": 0.0,
                "action": "NONE",
            },
            "model_role_outputs": [],
        }

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.analyze_reasoning_arbitration_v3",
        _wait_for_pullback_reasoning,
    )
    council = ModelCouncilV3()
    council.evaluate(_strong_snapshot("BUY", frame_id=222), now_epoch=NOW)
    packet = council.evaluate(_strong_snapshot("BUY", frame_id=223), now_epoch=NOW + 0.5)

    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["selected_execution_lane"] == "SNIPER_ZONE_ENTRY"
    assert packet["promotion_trace"]["intraday_enter_now_reasoning_override_allowed"] is True
    assert packet["promotion_trace"]["reasoning_execution_blocked"] is False
    allowance = packet["allowance_package"]
    assert allowance["schema_version"] == "PG_ALLOWANCE_PACKAGE_V1"
    assert allowance["package_type"] == "INTRADAY_ENTER_NOW"
    assert allowance["allowance_family"] == "INTRADAY"
    assert allowance["entry_now_allowed"] is True
    assert allowance["execution_ready"] is True


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

    contribution = lane["high_frequency_contribution"]
    assert contribution["lane_authority"] is False
    assert contribution["status"] == "FORMING"
    assert "CURRENT_M5_CANDLE_NOT_CLOSED" in contribution["blockers"]
    assert "TWO_CANDLE_SIDE_MISMATCH" not in contribution["blockers"]


def test_strict_high_frequency_failure_does_not_block_structural_lane_selection() -> None:
    council = ModelCouncilV3()
    council.evaluate(_wave_riding_snapshot("BUY", frame_id=230, strict_high_frequency=True), now_epoch=NOW)
    result = council.evaluate(_wave_riding_snapshot("BUY", frame_id=231, strict_high_frequency=True), now_epoch=NOW + 0.5)

    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["execution"]["enabled"] is True
    assert result["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    lane = result["model_council"]["execution_lane"]
    contribution = lane["high_frequency_contribution"]
    assert contribution["lane_authority"] is False
    assert contribution["status"] == "WAITING"
    assert "HIGH_FREQUENCY_TWO_CANDLE" not in lane["accepted_lanes"]
    assert "CURRENT_M5_CANDLE_NOT_CLOSED" in contribution["blockers"]


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


assert_non_executable_release_fields = _assert_non_executable_release_fields
broker_click_unsafe_result = _broker_click_unsafe_result
second_packet = _second_packet
strong_snapshot = _strong_snapshot


def test_ready_timing_without_explicit_expiry_uses_playbook_preferred_expiry() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot in (first, second):
        snapshot["timing"].pop("expiry_seconds", None)
        snapshot.pop("expiry_seconds", None)
        snapshot.pop("required_seconds", None)

    first_result = council.evaluate(first, now_epoch=NOW)
    assert first_result["execution"]["enabled"] is True
    assert first_result["timing_decision"]["entry_now_allowed"] is True
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is True
    assert result["execution"]["expiry_seconds"] > 0
    assert result["timing_decision"]["preferred_expiry_sec"] == result["execution"]["expiry_seconds"]
    assert result["model_council"]["final_state"] == "EXECUTABLE"
    assert result["block_reason"] is None
    assert result["schema_version"] == "PG_EXECUTION_PACKET_V3"


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


def test_blank_symbol_blocks_execution_without_marking_models_stale() -> None:
    snapshot = _strong_snapshot("BUY", frame_id=100)
    snapshot["symbol"] = ""
    snapshot["market"] = ""
    snapshot["ocr_symbol"] = ""

    result = ModelCouncilV3().evaluate(snapshot, now_epoch=NOW)

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "BLOCKED_BY_RUNTIME"
    assert result["block_reason"] == "INSTRUMENT_CONTEXT_NOT_PAPER_SAFE"
    assert result["instrument_context"]["display_symbol"] == ""
    assert result["instrument_context"]["ocr_symbol"] == ""
    assert result["runtime_model_health"]["all_required_models_awake"] is True
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
    assert council.evaluate(first, now_epoch=NOW)["execution"]["enabled"] is True
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


def test_blank_symbol_legacy_broker_click_mode_uses_paper_packet_when_user_locked_only() -> None:
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

    assert result["execution"]["enabled"] is True
    assert result["model_council"]["final_state"] == "EXECUTABLE"
    assert result["block_reason"] is None
    assert result["instrument_context"]["paper_safe"] is True
    assert result["instrument_context"]["broker_click_safe"] is False
    assert result["promotion_trace"]["release_condition"] == "none"
    assert result["promotion_trace"]["instrument_context_state"] in {"USER_PROFILE_LOCKED", "BROKER_SURFACE_LOCKED"}
    assert result["schema_version"] == "PG_EXECUTION_PACKET_V3"


def test_live_packet_publication_mode_is_not_broker_click_identity_mode() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    first.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "execution_controls": {"execution_mode": "live", "live_execution_enabled": True},
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
            "execution_controls": {"execution_mode": "live", "live_execution_enabled": True},
            "instrument_identity_lock": {"user_symbol": "EUR/GBP OTC"},
        }
    )

    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["execution"]["enabled"] is True
    assert packet["instrument_context"]["paper_safe"] is True
    assert packet["instrument_context"]["broker_click_safe"] is False


def test_broker_click_mode_executes_when_user_profile_lock_has_v2_evidence() -> None:
    council = ModelCouncilV3()
    evidence_lock: dict[str, Any] = {
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

    first_result = council.evaluate(first, now_epoch=NOW)
    assert first_result["execution"]["side"] == "BUY"
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
    snapshot = _strong_snapshot("BUY", frame_id=100)
    snapshot["current_candle"] = {
        "candle_phase": "LATE_CANDLE",
        "seconds_elapsed": 270,
        "seconds_remaining": 30,
        "too_late": True,
        "entry_allowed": False,
    }
    snapshot["latest_signal"] = {"entry_state": "WAIT_FOR_RETEST"}
    snapshot["tracking_summary"] = {"entry_state": "WAIT_FOR_RETEST"}

    result = ModelCouncilV3().evaluate(snapshot, now_epoch=NOW)

    assert result["execution"]["enabled"] is False
    assert result["schema_version"] == MODEL_COUNCIL_STUDY_SCHEMA_VERSION
    assert result["packet_id"].startswith("pgpkt_")
    assert result["packet_type"] == "STUDY_PACKET"
    assert result["study_packet"]["packet_id"] == result["packet_id"]
    assert result["study_packet"]["packet_type"] == "STUDY_PACKET"
    assert result["study_packet"]["execution"]["state"] in {"PREPARING", "WATCHING"}
    assert result["study_packet"]["model_council"]["final_side"] == "BUY"
    assert result["promotion_trace"]["promotion_result"] in {"PREPARING", "WATCHING"}
    assert result["promotion_trace"]["packet_result"] == "STUDY_PACKET_PUBLISHED"
    assert result["promotion_trace"]["late_chase_detected"] is False
    assert result["final_execution_score"] >= 0.0
    assert result["study_packet"]["true_blocker"] != "Late-chase class not detected."


def test_every_non_executable_state_has_denied_at() -> None:
    blank_source = _strong_snapshot("BUY", frame_id=100)
    blank_source["symbol"] = ""
    blank_source["market"] = ""
    blank_source["ocr_symbol"] = ""
    late_candle = _strong_snapshot("BUY", frame_id=101)
    late_candle["current_candle"] = {
        "candle_phase": "LATE_CANDLE",
        "seconds_elapsed": 270,
        "seconds_remaining": 30,
        "too_late": True,
        "entry_allowed": False,
    }
    late_candle["latest_signal"] = {"entry_state": "WAIT_FOR_RETEST"}
    late_candle["tracking_summary"] = {"entry_state": "WAIT_FOR_RETEST"}
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
        ModelCouncilV3().evaluate(blank_source, now_epoch=NOW),
        ModelCouncilV3().evaluate(late_candle, now_epoch=NOW),
        _permission_denied_result(),
    ]

    for result in results:
        assert result["execution"]["enabled"] is False
        assert result["promotion_trace"]["denied_at"] not in (None, "", "CONTEXT", "WATCHING", "N/A", "MISSING")


def test_every_non_executable_state_has_next_required() -> None:
    blank_source = _strong_snapshot("BUY", frame_id=100)
    blank_source["symbol"] = ""
    blank_source["market"] = ""
    blank_source["ocr_symbol"] = ""
    late_candle = _strong_snapshot("BUY", frame_id=101)
    late_candle["current_candle"] = {
        "candle_phase": "LATE_CANDLE",
        "seconds_elapsed": 270,
        "seconds_remaining": 30,
        "too_late": True,
        "entry_allowed": False,
    }
    late_candle["latest_signal"] = {"entry_state": "WAIT_FOR_RETEST"}
    late_candle["tracking_summary"] = {"entry_state": "WAIT_FOR_RETEST"}
    results = [
        ModelCouncilV3().evaluate(blank_source, now_epoch=NOW),
        ModelCouncilV3().evaluate(late_candle, now_epoch=NOW),
        _permission_denied_result(),
    ]

    for result in results:
        _assert_non_executable_release_fields(result)


def test_every_non_executable_study_packet_has_promotion_failure_audit() -> None:
    blank_source = _strong_snapshot("BUY", frame_id=100)
    blank_source["symbol"] = ""
    blank_source["market"] = ""
    blank_source["ocr_symbol"] = ""
    late_candle = _strong_snapshot("BUY", frame_id=101)
    late_candle["current_candle"] = {
        "candle_phase": "LATE_CANDLE",
        "seconds_elapsed": 270,
        "seconds_remaining": 30,
        "too_late": True,
        "entry_allowed": False,
    }
    late_candle["latest_signal"] = {"entry_state": "WAIT_FOR_RETEST"}
    late_candle["tracking_summary"] = {"entry_state": "WAIT_FOR_RETEST"}
    results = [
        ModelCouncilV3().evaluate(blank_source, now_epoch=NOW),
        ModelCouncilV3().evaluate(late_candle, now_epoch=NOW),
        _permission_denied_result(),
    ]

    for result in results:
        audit = result["study_packet"]["promotion_failure_audit_v3"]
        assert audit["schema_version"] == PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION
        assert audit["packet_result"] == "STUDY_PACKET_PUBLISHED"
        assert audit["denied_at"] == result["promotion_trace"]["denied_at"]
        assert audit["blocker_ranking"][0]["blocker"] == result["promotion_trace"]["denied_at"]


def test_partial_sequence_context_blocks_execution_and_reports_exact_rejected_fields() -> None:
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

    assert result["execution"]["enabled"] is False
    assert result["model_council"]["final_state"] == "BLOCKED_BY_RUNTIME"
    assert result["block_reason"] == "PARTIAL_SEQUENCE_NOT_EXECUTABLE"
    assert trace["denied_at"] == "PARTIAL_SEQUENCE_NOT_EXECUTABLE"
    assert trace["true_blocker"] == "PARTIAL_SEQUENCE_NOT_EXECUTABLE"
    assert trace["sequence_context_ready"] is False
    assert trace["sequence_context_advisory"] is True
    assert readiness["next_required"].startswith("sequence context incomplete:")
    assert trace["next_required"] != "full sequence context required"
    assert "sequence_length=12 required >=50" in readiness["next_required"]
    assert result["opportunity_maturity_state"] == "VALID_WATCH"
    assert result["opportunity_maturity"]["visual_integrity"] == "BLOCK"
    assert result["opportunity_maturity"]["sequence_context_role"] == "TRACE_ADVISORY_FOR_PLAYBOOK_AUTHORITY"
    assert result["allowance_package"]["visual_integrity"] == "BLOCK"
    assert result["allowance_package"]["sequence_context_role"] == "TRACE_ADVISORY_FOR_PLAYBOOK_AUTHORITY"
    assert result["packet_validation"]["first_reason"] == "PARTIAL_SEQUENCE_NOT_EXECUTABLE"
    assert readiness["sequence_length"] == 12
    assert readiness["frames_received"] == 12
    assert readiness["frames_used"] == 12
    assert readiness["minimum_required_sequence_length"] == 50
    assert readiness["minimum_required_box_history_len"] == 1
    assert readiness["minimum_required_progression_len"] == 1
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
    assert result["promotion_trace"]["true_blocker"] == "PLAYBOOK_MATURITY_VALID_WATCH"
    assert result["promotion_trace"]["promotion_failure_audit_v3"]["exact_field_preventing_execution_packet"] == "book_strategy_master"
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
    assert result["promotion_trace"]["true_blocker"] == "PLAYBOOK_MATURITY_VALID_WATCH"
    assert result["promotion_trace"]["denied_at"] == "PLAYBOOK_MATURITY_VALID_WATCH"


def test_instrument_context_reports_broker_click_false_without_blocking_paper_packet() -> None:
    result = _broker_click_unsafe_result()

    assert result["execution"]["enabled"] is True
    assert result["promotion_trace"]["release_state"] == "EXECUTION_PACKET_PUBLISHED"
    assert result["promotion_trace"]["instrument_context_broker_click_safe"] is False
    assert result["instrument_context"]["paper_safe"] is True
    assert result["instrument_context"]["broker_click_safe"] is False
    assert result["promotion_trace"]["next_required"] == "none"
    assert result["promotion_trace"]["release_condition"] == "none"
    assert result["promotion_trace"]["denied_at"] == "NONE"


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


def test_executable_ready_does_not_require_broker_click_safe() -> None:
    result = _broker_click_unsafe_result()

    assert result["execution"]["enabled"] is True
    assert result["promotion_trace"]["true_blocker"] == "NONE"
    assert result["opportunity_maturity_state"] == "ENTER_NOW"
    assert result["opportunity_maturity"]["visual_integrity"] == "PASS"
    assert result["allowance_package"]["visual_integrity"] == "PASS"
    assert result["schema_version"] == "PG_EXECUTION_PACKET_V3"


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
    assert packet["opportunity_maturity_state"] == "ENTER_NOW"
    assert packet["opportunity_maturity"]["visual_integrity"] == "PASS"
    assert packet["promotion_trace"]["opportunity_maturity_state"] == "ENTER_NOW"
    allowance = packet["allowance_package"]
    assert allowance["package_type"] == "INTRADAY_ENTER_NOW"
    assert allowance["execution_authority"] == "PLAYBOOK_FINAL_DECIDER_V3"
    assert allowance["packet_authority"] == "PG_EXECUTION_PACKET_V3"
    assert allowance["execution_ready"] is True
    assert allowance["opportunity_maturity"] == "ENTER_NOW"
    assert allowance["visual_integrity"] == "PASS"
    assert packet["model_council"]["allowance_package"]["package_type"] == "INTRADAY_ENTER_NOW"
    assert packet["promotion_trace"]["allowance_package"]["package_type"] == "INTRADAY_ENTER_NOW"


def test_strategy_package_uses_visible_swing_horizon_not_one_candle_scalp() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=100)
    second = _strong_snapshot("BUY", frame_id=101)
    for snapshot in (first, second):
        snapshot["candidate_side"] = "BUY"
        snapshot["tracking_summary"] = {
            "entry_state": "SNIPER_READY",
            "local_direction": "BUY",
            "global_direction": "BUY",
            "visible_candle_count": 52,
            "historical_structure": [
                {"label": "H1 SELL", "direction": "SELL", "candle_count": 7},
                {"label": "H2 BUY", "direction": "BUY", "candle_count": 10},
                {"label": "H3 SELL", "direction": "SELL", "candle_count": 6},
                {"label": "H4 BUY", "direction": "BUY", "candle_count": 9},
            ],
        }
        snapshot["risk_opposing_force"]["distance_to_opposing_force"] = 0.34
        snapshot["risk_opposing_force"]["distance_ok"] = True
        snapshot["timing"]["expiry_seconds"] = 300
        snapshot["timing"]["target_time_text"] = "00:05:00"

    council.evaluate(first, now_epoch=NOW)
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    expected = packet["allowance_package"]["expected_move_time"]
    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["expiry_seconds"] >= 60 * 60
    assert packet["allowance_package"]["entry_window"]["duration_sec"] == 300
    assert packet["allowance_package"]["entry_window"]["candle_count"] == 1
    assert packet["allowance_package"]["thesis_horizon"]["expected_candle_count"] >= 12
    assert packet["allowance_package"]["professional_trade_plan"]["professional_grade"] is True
    assert expected["expected_candle_count"] >= 12
    assert expected["basis"] == "professional_visible_history_memory_trend_plan"
    assert expected["projection_horizon"]["basis"] == "visible_swing_leg_room_projection"
    assert expected["projection_horizon"]["applied"] is True


def test_strategy_package_uses_full_overlay_suite_projection_horizon() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=120)
    second = _strong_snapshot("BUY", frame_id=121)
    for snapshot in (first, second):
        snapshot["candidate_side"] = "BUY"
        snapshot["tracking_summary"] = {
            "entry_state": "SNIPER_READY",
            "local_direction": "BUY",
            "global_direction": "BUY",
            "visible_candle_count": 54,
            "structure_boxes": [
                {
                    "id": "overlay_wave_buy",
                    "label": "IMPULSE",
                    "direction": "BUY",
                    "bbox": [180, 240, 420, 90],
                    "sniper_window": [184, 212, 224, 246],
                    "target_window": [430, 84, 506, 118],
                    "invalidation_y": 260,
                    "source_indices": list(range(24)),
                    "confidence": 0.9,
                }
            ],
            "support_resistance_zones": [
                {
                    "zone_id": "demand_overlay_suite",
                    "role": "DEMAND",
                    "side": "BUY",
                    "bbox": [180, 210, 250, 252],
                    "current_price_inside": True,
                    "distance_from_current": 0.03,
                    "confidence": 0.88,
                },
                {
                    "zone_id": "supply_overlay_suite",
                    "role": "SUPPLY",
                    "side": "SELL",
                    "bbox": [430, 84, 506, 118],
                    "distance_from_current": 0.44,
                    "confidence": 0.82,
                },
            ],
            "projection": {
                "direction": "BUY",
                "zones": [
                    {
                        "id": "projected_overlay_suite_buy",
                        "kind": "sniper",
                        "direction": "BUY",
                        "bbox": [184, 212, 224, 246],
                        "target_bbox": [430, 84, 506, 118],
                        "path": [[184, 236], [246, 202], [318, 158], [430, 96]],
                        "expected_move_candles": 24,
                        "confidence": 0.91,
                    }
                ],
            },
            "angle_vectors": [
                {
                    "id": "overlay_angle_buy",
                    "direction": "BUY",
                    "line_points": [[184, 236], [430, 96]],
                    "confidence": 0.8,
                }
            ],
        }

    council.evaluate(first, now_epoch=NOW)
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    plan = packet["opportunity_maturity"]["professional_trade_plan"]
    book = packet["opportunity_maturity"]["book_strategy"]
    assert plan["trend_alignment"]["overlay_suite_thesis"] is True
    assert plan["trade_hierarchy"]["local_distribution"]["overlay_suite_expected_candles"] >= 24
    assert plan["thesis_horizon"]["overlay_suite_expected_candles"] >= 24
    assert plan["thesis_horizon"]["expected_candle_count"] >= 24
    assert "OVERLAY_SUITE_FULL_READ" in book["strategy_combo"]
    assert "OVERLAY_ENTRY_TARGET_MAP" in book["strategy_combo"]
    assert "OVERLAY_PROJECTION_PATH" in book["strategy_combo"]
    ai_summary = packet["playbook_ai_summary_v3"]
    assert ai_summary["schema_version"] == "PG_PLAYBOOK_AI_SUMMARY_V3"
    assert ai_summary["full_suite_ready"] is True
    assert ai_summary["thesis_arbitration"]["winner"] == "BUY"
    assert ai_summary["meta_label"]["candidate_tradeable"] is True
    assert ai_summary["horizon"]["optimized_candle_count"] >= 8
    assert packet["allowance_package"]["playbook_ai_summary_v3"]["thesis_arbitration"]["winner"] == "BUY"
    assert packet["opportunity_maturity"]["playbook_ai_summary_v3"]["full_suite_ready"] is True
    assert packet["model_council"]["playbook_ai_summary_v3"]["horizon"]["optimized_duration_sec"] >= 300


def test_professional_plan_honors_book_full_suite_room_override() -> None:
    candle_context: dict[str, Any] = {
        "timeframe": "M5",
        "timeframe_seconds": 300,
        "visible_candle_count": 54,
        "current_leg": {"side": "BUY", "candle_count": 9, "move_stage": "MATURE"},
        "move_stage": "MATURE",
        "opposing_force_room": {
            "candidate_side": "BUY",
            "room_ok": True,
            "estimated_candles_to_force": 2,
        },
        "candles_per_leg": [
            {"side": "BUY", "candle_count": 6},
            {"side": "SELL", "candle_count": 7},
            {"side": "BUY", "candle_count": 9},
        ],
    }
    book_strategy: dict[str, Any] = {
        "maturity_state": "ENTER_NOW",
        "entry_profile": "AGGRESSIVE_SNIPER",
        "reaction_type": "CONTINUATION_PRESSURE",
        "evidence": {
            "aligned_with_primary_bias": True,
            "professional_profit_room_ok": True,
            "professional_profit_room_candles": 18,
            "professional_profit_room_source": "full_overlay_suite_projection_overrides_near_zone",
            "full_suite_room_override_ready": True,
            "overlay_suite_evidence_v3": {
                "entry_ready": True,
                "full_suite_ready": True,
                "target_ready": True,
                "expected_move_candles_from_projection": 18,
            },
        },
    }

    professional_trade_plan_v3 = cast(
        Callable[..., dict[str, Any]],
        getattr(model_council_module, "_professional_trade_plan_v3"),
    )
    plan = professional_trade_plan_v3(
        candle_context,
        book_strategy,
        candidate_side="BUY",
        entry_window_seconds=300,
        path_class="DIRECT_CONTINUATION",
        professional_thesis_resolution={
            "authority_side": "BUY",
            "directional_target_room_candles": 2,
            "directional_target_room_source": "directional_opposing_zone",
        },
    )

    assert plan["professional_grade"] is True
    assert plan["blocker"] == ""
    assert plan["trade_hierarchy"]["local_distribution"]["effective_room_candles"] == 18
    assert plan["trade_hierarchy"]["local_distribution"]["room_overridden_by_book_profit"] is True
    assert plan["thesis_horizon"]["expected_candle_count"] >= 8


def test_playbook_ai_wait_route_is_warning_when_playbook_strike_is_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _confirmed_wait_route_intelligence(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "PG_PLAYBOOK_AI_INTELLIGENCE_V3",
            "semantic_graph": {
                "coverage": {
                    "full_suite_ready": True,
                    "overlay_arsenal_score": 0.91,
                }
            },
            "thesis_arbitration": {
                "winner": "BUY",
                "winning_score": 0.74,
                "margin": 0.16,
                "conflict": True,
            },
            "meta_label": {
                "selected_side": "BUY",
                "candidate_tradeable": True,
                "selected": {
                    "target_before_invalidation_probability": 0.81,
                },
            },
            "horizon": {
                "selected_side": "BUY",
                "selected": {
                    "optimized_candle_count": 12,
                    "optimized_duration_sec": 3600,
                    "basis": "test_professional_projection",
                },
            },
            "regime_router": {
                "route": "WAIT_FOR_CLEARER_THESIS",
                "regime": "CONFLICT_OR_RANGE_ARBITRATION",
            },
        }

    def _confirmed_wait_route_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "PG_PLAYBOOK_AI_SUMMARY_V3",
            "full_suite_ready": True,
            "thesis_arbitration": {
                "winner": "BUY",
                "winning_score": 0.74,
                "margin": 0.16,
            },
            "meta_label": {
                "candidate_tradeable": True,
                "target_before_invalidation_probability": 0.81,
            },
            "horizon": {
                "optimized_candle_count": 12,
                "optimized_duration_sec": 3600,
            },
            "regime_router": {
                "route": "WAIT_FOR_CLEARER_THESIS",
            },
        }

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.build_playbook_ai_intelligence_v3",
        _confirmed_wait_route_intelligence,
    )
    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.compact_playbook_ai_intelligence_v3",
        _confirmed_wait_route_summary,
    )

    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=130)
    second = _strong_snapshot("BUY", frame_id=131)

    council.evaluate(first, now_epoch=NOW)
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    decision = packet["professional_trade_plan"]["playbook_ai_decision"]
    assert decision["block_reason"] == ""
    assert decision["warning_reason"] == "SOFT_WAIT_FOR_CLEARER_THESIS"
    assert decision["strike_override_ready"] is True
    assert packet["opportunity_maturity"]["denied_at"] == "NONE"


def test_full_suite_story_flip_requires_second_fresh_same_side_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def _story_lock_intelligence(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        side = "SELL" if call_count <= 2 else "BUY"
        return {
            "schema_version": "PG_PLAYBOOK_AI_INTELLIGENCE_V3",
            "semantic_graph": {"coverage": {"full_suite_ready": True, "rows_total": 30}},
            "thesis_arbitration": {
                "winner": side,
                "winning_score": 0.76,
                "margin": 0.18,
                "scores": {
                    "BUY": {"side": "BUY", "score": 0.76 if side == "BUY" else 0.44},
                    "SELL": {"side": "SELL", "score": 0.76 if side == "SELL" else 0.44},
                },
            },
            "meta_label": {
                "selected_side": side,
                "candidate_tradeable": True,
                "selected": {"target_before_invalidation_probability": 0.82},
            },
            "horizon": {
                "selected_side": side,
                "selected": {
                    "optimized_candle_count": 8,
                    "optimized_duration_sec": 2400,
                    "basis": "test_full_suite_story",
                },
            },
            "regime_router": {"route": "TRADEABLE_CURRENT_TRUTH", "regime": "TEST"},
            "full_suite_story_lock_v3": {
                "schema_version": "PG_FULL_SUITE_STORY_LOCK_V3",
                "active_side": side,
                "candidate_side": "HOLD",
                "state": "FULL_SUITE_STORY_CONFIRMED",
                "confirmed": True,
                "transition_confirmed": False,
                "story_confidence": 0.76,
                "story_margin": 0.18,
                "target_before_invalidation_probability": 0.82,
                "horizon_candles": 8,
                "rows_total": 30,
            },
        }

    def _story_lock_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
        ai = dict(payload)
        return {
            "schema_version": "PG_PLAYBOOK_AI_SUMMARY_V3",
            "full_suite_ready": True,
            "thesis_arbitration": dict(cast(Mapping[str, Any], ai["thesis_arbitration"])),
            "meta_label": {
                "candidate_tradeable": True,
                "target_before_invalidation_probability": 0.82,
            },
            "horizon": {
                "optimized_candle_count": 8,
                "optimized_duration_sec": 2400,
            },
            "regime_router": {"route": "TRADEABLE_CURRENT_TRUTH"},
            "full_suite_story_lock_v3": dict(cast(Mapping[str, Any], ai["full_suite_story_lock_v3"])),
        }

    def _story_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        paths = (
            ("playbook_ai_intelligence_v3", "full_suite_story_lock_v3"),
            ("playbook_ai_summary_v3", "full_suite_story_lock_v3"),
            ("dual_thesis_report_v3", "full_suite_story_lock_v3"),
            ("model_council", "playbook_ai_summary_v3", "full_suite_story_lock_v3"),
        )
        for path in paths:
            node: Any = payload
            for key in path:
                mapping_node: Mapping[str, Any] = cast(Mapping[str, Any], node) if isinstance(node, Mapping) else {}
                node = mapping_node.get(key)
            if isinstance(node, Mapping):
                return cast(Mapping[str, Any], node)
        return {}

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.build_playbook_ai_intelligence_v3",
        _story_lock_intelligence,
    )
    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.compact_playbook_ai_intelligence_v3",
        _story_lock_summary,
    )

    council = ModelCouncilV3()
    council.evaluate(_strong_snapshot("SELL", frame_id=160), now_epoch=NOW)
    sell_packet = council.evaluate(_strong_snapshot("SELL", frame_id=161), now_epoch=NOW + 0.5)
    assert _story_from_payload(sell_packet)["effective_side"] == "SELL"

    pending = council.evaluate(_strong_snapshot("SELL", frame_id=162), now_epoch=NOW + 1.0)
    pending_story = _story_from_payload(pending)
    assert pending_story["raw_active_side"] == "BUY"
    assert pending_story["effective_side"] == "SELL"
    assert pending_story["side_flip_pending"] is True

    confirmed = council.evaluate(_strong_snapshot("SELL", frame_id=163), now_epoch=NOW + 1.5)
    confirmed_story = _story_from_payload(confirmed)
    assert confirmed_story["effective_side"] == "BUY"
    assert confirmed_story["side_flip_pending"] is False


def test_unresolved_countertrend_cannot_publish_professional_package() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("BUY", frame_id=140)
    second = _strong_snapshot("BUY", frame_id=141)
    for snapshot in (first, second):
        snapshot["global_structure"]["global_side"] = "SELL"
        snapshot["candidate_side"] = "BUY"
        snapshot["action"] = "BUY"
        snapshot["buy_score"] = 0.90
        snapshot["sell_score"] = 0.02
        snapshot["market_context"]["global_side"] = "SELL"
        snapshot["market_context"]["dominant_side"] = "SELL"
        snapshot["local_micro_structure"]["local_side"] = "BUY"
        snapshot["market_context"]["local_side"] = "BUY"
        snapshot["role_flip_confirmed"] = False
        snapshot["break_of_structure_confirmed"] = False
        snapshot["retest_confirmed"] = False
        snapshot["liquidity_sweep_detected"] = False
        snapshot["tracking_summary"] = {
            "entry_state": "SNIPER_READY",
            "local_direction": "BUY",
            "global_direction": "SELL",
            "visible_candle_count": 50,
            "historical_structure": [
                {"label": "H1 SELL", "direction": "SELL", "candle_count": 15},
                {"label": "H2 BUY", "direction": "BUY", "candle_count": 4},
            ],
        }

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["packet_type"] != "PG_EXECUTION_PACKET_V3"
    assert result["opportunity_maturity"]["professional_trade_plan"]["professional_grade"] is False
    assert result["opportunity_maturity"]["professional_trade_plan"]["blocker"] == "PROFESSIONAL_COUNTERTREND_NOT_CONFIRMED"


def test_against_global_structure_blocks_playbook_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    def _against_structure_reasoning(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "arbitration": {"state": "BLOCKED"},
            "final_reasoning_decision": {
                "decision": "EXECUTE",
                "side": "SELL",
                "confidence": 0.82,
                "reason": "Forced test fixture.",
            },
            "bad_entry_filter": {
                "active": True,
                "class": "AGAINST_GLOBAL_STRUCTURE",
                "severity": 0.68,
                "action": "WAIT_FOR_REJECTION",
                "reason": "Candidate side is against global structure without reversal confirmation.",
            },
            "model_role_outputs": [],
        }

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.analyze_reasoning_arbitration_v3",
        _against_structure_reasoning,
    )
    council = ModelCouncilV3()
    first = _strong_snapshot("SELL", frame_id=100)
    second = _strong_snapshot("SELL", frame_id=101)
    for snapshot in (first, second):
        snapshot["candidate_side"] = "SELL"
        snapshot["global_structure"]["global_side"] = "BUY"
        snapshot["market_context"]["global_side"] = "BUY"
        snapshot["market_context"]["dominant_side"] = "SELL"

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)

    assert result["execution"]["enabled"] is False
    assert result["packet_type"] == "STUDY_PACKET"
    assert result["promotion_trace"]["hard_bad_entry_class_active"] is True
    assert result["promotion_trace"]["reasoning_bad_entry_class"] == "AGAINST_GLOBAL_STRUCTURE"
    assert result["promotion_trace"]["true_blocker"] in {
        "PLAYBOOK_MATURITY_NO_OPPORTUNITY",
        "PLAYBOOK_MATURITY_INVALIDATED",
        "BAD_ENTRY_FILTER",
        "PLAYBOOK_INVALIDATED",
    }


def test_true_blocker_reported_not_generic_late_chase_reason() -> None:
    snapshot = _strong_snapshot("SELL", frame_id=100)
    snapshot["timing"] = {
        "state": "READY",
        "expiry_seconds": 300,
        "target_time_seconds": 300,
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
    result = ModelCouncilV3().evaluate(snapshot, now_epoch=NOW)

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
    assert result["opportunity_maturity_state"] == "LATE_CHASE"
    assert result["opportunity_maturity"]["denied_at"] == "PLAYBOOK_MATURITY_LATE_CHASE"
    assert result["study_packet"]["timing_decision"]["entry_now_allowed"] is False


def test_local_breakdown_lane_against_global_needs_reversal_proof() -> None:
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

    assert packet["execution"]["enabled"] is False
    assert packet["packet_type"] == "STUDY_PACKET"
    assert packet["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    assert packet["promotion_trace"]["lane_accepted"] is True
    assert packet["promotion_trace"]["reasoning_execution_blocked"] is True
    assert packet["promotion_trace"]["true_blocker"] == "REASONING_WATCH"
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


def test_wave_riding_lane_publishes_when_current_flow_has_clear_path() -> None:
    council = ModelCouncilV3()
    first = _strong_snapshot("SELL", frame_id=100)
    second = _strong_snapshot("SELL", frame_id=101)
    for snapshot in (first, second):
        snapshot["sell_score"] = 0.88
        snapshot["buy_score"] = 0.02
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"].update(
            {
                "inside_valid_trigger_zone": False,
                "current_location": "MIDDLE_SAFE",
                "opposing_force_distance_ok": True,
            }
        )
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["execution_timing"] = {
            "state": "READY",
            "side": "SELL",
            "lane": "WAVE_RIDING_CONTINUATION",
            "expiry_seconds": 300,
            "recommended_expiry_seconds": 300,
            "current_flow_continuation_ready": True,
            "current_flow_direction_confirmed": True,
            "clear_path_score": 0.84,
            "p_target_before_invalidation": 0.78,
            "p_trigger_next_1": 0.72,
            "current_flow_conflicts": [],
        }
        snapshot["latest_signal"] = {"entry_state": "WAIT_FOR_TRIGGER"}
        snapshot["tracking_summary"] = {"entry_state": "WAIT_FOR_TRIGGER", "local_direction": "SELL"}

    council.evaluate(first, now_epoch=NOW)
    packet = council.evaluate(second, now_epoch=NOW + 0.5)

    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["execution"]["enabled"] is True
    assert packet["selected_execution_lane"] == "WAVE_RIDING_CONTINUATION"
    wave = packet["promotion_trace"]["wave_context"]
    assert wave["continuation_ready"] is True
    assert wave["clear_path_ready"] is True
    assert wave["phase"] == "CLEAR_PATH_CONTINUATION"


def test_clean_wave_riding_overrides_soft_reasoning_pullback_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    def _soft_pullback_reasoning(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "arbitration": {
                "coherence_score": 0.84,
                "state": "WAIT_FOR_PULLBACK",
                "side": "BUY",
            },
            "final_reasoning_decision": {
                "side": "BUY",
                "decision": "WAIT_FOR_PULLBACK",
                "confidence": 0.84,
                "play": "TREND_CONTINUATION",
                "regime": "PULLBACK_RECLAIM",
                "price_location": "MIDDLE_SAFE",
                "timing_mode": "ENTER_NOW",
                "reason": "Macro pullback wait is soft; current wave has reclaimed.",
            },
            "bad_entry_filter": {
                "active": False,
                "class": "NONE",
                "severity": 0.0,
                "action": "NONE",
            },
            "model_role_outputs": [],
        }

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.analyze_reasoning_arbitration_v3",
        _soft_pullback_reasoning,
    )
    council = ModelCouncilV3()
    council.evaluate(_wave_riding_snapshot("BUY", frame_id=240), now_epoch=NOW)
    packet = council.evaluate(_wave_riding_snapshot("BUY", frame_id=241), now_epoch=NOW + 0.5)

    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["selected_execution_lane"] == "WAVE_RIDING_CONTINUATION"
    assert packet["promotion_trace"]["wave_reasoning_override_allowed"] is True
    assert packet["promotion_trace"]["reasoning_execution_blocked"] is False


def test_wave_riding_does_not_override_hard_buy_high_bad_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def _buy_high_reasoning(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "arbitration": {
                "coherence_score": 0.83,
                "state": "WAIT_FOR_PULLBACK",
                "side": "BUY",
            },
            "final_reasoning_decision": {
                "side": "BUY",
                "decision": "WAIT_FOR_PULLBACK",
                "confidence": 0.83,
                "play": "TREND_CONTINUATION",
                "regime": "BULLISH_IMPULSE",
                "price_location": "LOCAL_HIGH",
                "timing_mode": "ENTER_NOW",
                "reason": "Price is high after expansion.",
            },
            "bad_entry_filter": {
                "active": True,
                "class": "BUY_HIGH_AFTER_IMPULSE",
                "severity": 0.82,
                "action": "WAIT_FOR_PULLBACK",
            },
            "model_role_outputs": [],
        }

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.analyze_reasoning_arbitration_v3",
        _buy_high_reasoning,
    )
    council = ModelCouncilV3()
    council.evaluate(_wave_riding_snapshot("BUY", frame_id=242), now_epoch=NOW)
    result = council.evaluate(_wave_riding_snapshot("BUY", frame_id=243), now_epoch=NOW + 0.5)

    assert result["packet_type"] == "STUDY_PACKET"
    assert result["execution"]["enabled"] is False
    assert result["promotion_trace"]["execution_lane"]["accepted"] is True
    assert result["promotion_trace"]["wave_reasoning_override_allowed"] is False
    assert result["promotion_trace"]["true_blocker"] == "PLAYBOOK_MATURITY_LATE_CHASE"
    assert result["promotion_trace"]["hard_bad_entry_class_active"] is True
    assert result["opportunity_maturity"]["book_strategy"]["maturity_state"] == "LATE_CHASE"
    assert any(
        blocker.get("field") == "bad_entry_filter.class"
        for blocker in result["opportunity_maturity"]["book_strategy"]["blockers"]
    )


def _wave_context_from_result(result: dict[str, Any]) -> dict[str, Any]:
    trace = cast(Mapping[str, Any], result.get("promotion_trace") or {})
    execution_lane = cast(Mapping[str, Any], trace.get("execution_lane") or {})
    return cast(dict[str, Any], trace.get("wave_context") or execution_lane.get("wave_context") or {})


def _apply_sell_low_support_risk(snapshot: dict[str, Any], *, role_flip: bool = False) -> None:
    snapshot["market_context"].update(
        {
            "current_location": "SUPPORT_LOW_DEMAND",
            "history_area_label": "studied_low_extreme",
            "history_area_risk": 0.91,
            "history_extension_against_side": not role_flip,
            "history_extension_stretched": not role_flip,
        }
    )
    snapshot["zone_liquidity"].update(
        {
            "zone_type": "support",
            "inside_valid_trigger_zone": False,
        }
    )
    snapshot["execution_timing"].update(
        {
            "history_area_label": "studied_low_extreme",
            "history_area_risk": 0.91,
            "history_extension_against_side": not role_flip,
            "history_extension_stretched": not role_flip,
            "favorable_history_rejection": False,
            "entry_area_relation": "above_price",
            "entry_area_near": True,
            "entry_area_score": 1.0,
            "breakout_confirmation": role_flip,
            "break_and_retest_confirmed": role_flip,
            "retest_confirmed": role_flip,
            "current_flow_direction_confirmed": True,
            "p_target_before_invalidation": 0.78,
            "p_trigger_next_1": 0.75,
        }
    )
    snapshot["smart_money_context"] = {
        "breakout_confirmation": role_flip,
        "role_flip_confirmed": role_flip,
        "break_and_retest_confirmed": role_flip,
    }


def _apply_buy_high_resistance_risk(snapshot: dict[str, Any]) -> None:
    snapshot["market_context"].update(
        {
            "current_location": "RESISTANCE_HIGH_SUPPLY",
            "history_area_label": "studied_high_extreme",
            "history_area_risk": 0.91,
            "history_extension_against_side": True,
            "history_extension_stretched": True,
        }
    )
    snapshot["zone_liquidity"].update(
        {
            "zone_type": "resistance",
            "inside_valid_trigger_zone": False,
        }
    )
    snapshot["execution_timing"].update(
        {
            "history_area_label": "studied_high_extreme",
            "history_area_risk": 0.91,
            "history_extension_against_side": True,
            "history_extension_stretched": True,
            "favorable_history_reclaim": False,
            "entry_area_relation": "below_price",
            "entry_area_near": True,
            "entry_area_score": 1.0,
            "p_target_before_invalidation": 0.78,
            "p_trigger_next_1": 0.75,
        }
    )


def test_model_council_blocks_sell_low_support_without_role_flip() -> None:
    council = ModelCouncilV3()
    first = _wave_riding_snapshot("SELL", frame_id=244)
    second = _wave_riding_snapshot("SELL", frame_id=245)
    for snapshot in (first, second):
        _apply_sell_low_support_risk(snapshot)

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)
    wave = _wave_context_from_result(result)

    assert result["packet_type"] == "STUDY_PACKET"
    assert result["execution"]["enabled"] is False
    assert wave["directional_location_ok"] is False
    assert wave["sell_low_history_risk"] is True
    assert "SELL_LOW_SUPPORT_LOCATION_GUARD" in wave["blockers"]


def test_model_council_blocks_buy_high_resistance_without_role_flip() -> None:
    council = ModelCouncilV3()
    first = _wave_riding_snapshot("BUY", frame_id=246)
    second = _wave_riding_snapshot("BUY", frame_id=247)
    for snapshot in (first, second):
        _apply_buy_high_resistance_risk(snapshot)

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)
    wave = _wave_context_from_result(result)

    assert result["packet_type"] == "STUDY_PACKET"
    assert result["execution"]["enabled"] is False
    assert wave["directional_location_ok"] is False
    assert wave["buy_high_history_risk"] is True
    assert "BUY_HIGH_RESISTANCE_LOCATION_GUARD" in wave["blockers"]


def test_model_council_allows_confirmed_role_flip_through_support(monkeypatch: pytest.MonkeyPatch) -> None:
    def _execute_reasoning(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "arbitration": {
                "coherence_score": 0.86,
                "state": "EXECUTE",
                "side": "SELL",
            },
            "final_reasoning_decision": {
                "side": "SELL",
                "decision": "EXECUTE",
                "confidence": 0.86,
                "play": "BEARISH_ROLE_FLIP_CONTINUATION",
                "regime": "SUPPORT_BROKEN_RETESTED_AS_RESISTANCE",
                "price_location": "ROLE_FLIP_RETEST",
                "timing_mode": "ENTER_NOW",
                "reason": "Former support has broken and retested as resistance.",
            },
            "bad_entry_filter": {
                "active": False,
                "class": "NONE",
                "severity": 0.0,
                "action": "NONE",
            },
            "model_role_outputs": [],
        }

    monkeypatch.setattr(
        "phoenixguard.decision.model_council_v3.analyze_reasoning_arbitration_v3",
        _execute_reasoning,
    )
    council = ModelCouncilV3()
    first = _wave_riding_snapshot("SELL", frame_id=248)
    second = _wave_riding_snapshot("SELL", frame_id=249)
    for snapshot in (first, second):
        _apply_sell_low_support_risk(snapshot, role_flip=True)

    council.evaluate(first, now_epoch=NOW)
    result = council.evaluate(second, now_epoch=NOW + 0.5)
    wave = _wave_context_from_result(result)

    assert wave["breakout_role_flip_ready"] is True
    assert wave["directional_location_ok"] is True
    assert "SELL_LOW_SUPPORT_LOCATION_GUARD" not in wave["blockers"]
    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["execution"]["enabled"] is True


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
    assert result["promotion_trace"]["true_blocker"] == "PLAYBOOK_MATURITY_VALID_WATCH"
    assert result["promotion_trace"]["execution_lane"]["accepted"] is False
    assert result["promotion_trace"]["missed_opportunity"]["side"] == "SELL"
    assert result["promotion_trace"]["missed_opportunity"]["lane_score"] >= result["promotion_trace"]["missed_opportunity"]["lane_threshold"]
    assert result["promotion_trace"]["missed_opportunity"]["future_move_confirmed"] is None


def test_aligned_buy_structure_needs_wave_proof_before_stale_sell_reload() -> None:
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

    assert packet["packet_type"] == "STUDY_PACKET"
    assert packet["execution"]["enabled"] is False
    assert packet["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    assert packet["promotion_trace"]["stale_dominant_overridden"] is True
    assert packet["promotion_trace"]["execution_lane"]["reversal_capture_mature"] is True
    assert packet["promotion_trace"]["execution_lane"]["accepted"] is False
    assert "WAVE_CONTEXT_NOT_READY" in packet["promotion_trace"]["execution_lane"]["blockers"]
    assert packet["promotion_trace"]["wave_context"]["phase"] == "MID_RANGE_TIMING_ONLY"


def test_mature_high_score_directional_flow_without_wave_proof_stays_study() -> None:
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

    assert packet["packet_type"] == "STUDY_PACKET"
    assert packet["execution"]["enabled"] is False
    lane = packet["promotion_trace"]["execution_lane"]
    momentum_lane = next(
        row for row in lane["evaluated_lanes"] if row["name"] == "MOMENTUM_ACCEPTANCE_ENTRY"
    )
    assert momentum_lane["accepted"] is False
    assert momentum_lane["momentum_context_ready"] is False
    assert "MID_RANGE_NEEDS_FLOW_PROOF" in momentum_lane["momentum_wave_blockers"]
    assert packet["book_strategy"]["maturity_state"] in {"VALID_WATCH", "PREPARE"}


def test_momentum_acceptance_requires_high_lane_score_and_clean_wave_context() -> None:
    council = ModelCouncilV3()
    first = _wave_riding_snapshot("BUY", frame_id=100)
    second = _wave_riding_snapshot("BUY", frame_id=101)
    for snapshot, score in ((first, 0.78), (second, 0.95)):
        snapshot["buy_score"] = score
        snapshot["sell_score"] = 0.01
        snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["current_location"] = "MIDDLE_SAFE"
        snapshot["entry_quality"] = "EARLY_WATCH"
        snapshot["timing"]["state"] = "READY"
        snapshot["execution_timing"]["lane"] = "MOMENTUM_ACCEPTANCE_ENTRY"
        snapshot["path_risk"] = {"state": "STRONG", "score": 0.84, "executable_allowed": True}
        snapshot["latest_signal"] = {"entry_state": "ACTIVE"}
        snapshot["tracking_summary"] = {"entry_state": "ACTIVE", "local_direction": "BUY"}

    first_result = council.evaluate(first, now_epoch=NOW)
    assert first_result["execution"]["enabled"] is False
    assert first_result["promotion_trace"]["execution_lane"]["accepted"] is False

    packet = council.evaluate(second, now_epoch=NOW + 0.5)
    assert packet["execution"]["enabled"] is True
    lane = packet["promotion_trace"]["execution_lane"]
    momentum_lane = next(
        row for row in lane["evaluated_lanes"] if row["name"] == "MOMENTUM_ACCEPTANCE_ENTRY"
    )
    assert "MOMENTUM_ACCEPTANCE_ENTRY" in lane["accepted_lanes"]
    assert momentum_lane["accepted"] is True
    assert momentum_lane["momentum_context_ready"] is True
    assert momentum_lane["momentum_wave_blockers"] == []
    assert packet["book_strategy"]["maturity_state"] == "ENTER_NOW"


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
        "allowance_package",
        "opportunity_maturity",
        "opportunity_maturity_state",
        "visual_integrity",
        "block_reason",
    ):
        assert field in packet
    assert packet["schema_version"] == PG_EXECUTION_PACKET_SCHEMA_VERSION
    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["execution"]["amount_action"] == "DO_NOT_CHANGE_AMOUNT"
    assert packet["execution"]["allowance_package_type"] == "INTRADAY_ENTER_NOW"
    assert packet["allowance_package"]["selected_lane"] == packet["selected_execution_lane"]
    assert packet["opportunity_maturity_state"] == "ENTER_NOW"
    assert packet["opportunity_maturity"]["state"] == "ENTER_NOW"
    assert packet["visual_integrity"] == "PASS"
    assert packet["execution"]["expiry_seconds"] >= 60 * 60
    assert packet["allowance_package"]["entry_window"]["duration_sec"] == 300
    assert packet["allowance_package"]["thesis_horizon"]["expected_candle_count"] >= 12
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
