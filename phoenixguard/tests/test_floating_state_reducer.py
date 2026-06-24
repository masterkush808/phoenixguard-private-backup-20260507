from __future__ import annotations
from typing import Any

from phoenixguard.execution.floating_state_reducer import FloatingStateV2, build_floating_state


def test_floating_state_hides_na_fields_for_study_packet() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": "pgpkt_abcdef123456",
            "packet_type": "STUDY_PACKET",
            "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
            "model_council": {"final_execution_score": 0.32, "execution_threshold": 0.70},
            "execution_lane": {"name": "SNIPER_ZONE_ENTRY", "accepted": False, "reason": "SNIPER_ZONE_ENTRY_STRUCTURE_NOT_READY"},
        },
    )

    rendered = str(state)
    assert state["packet"]["type"] == "STUDY"
    assert state["schema_version"] == "FloatingStateV2"
    assert state["council"]["lane_short"] == "SNIPER"
    assert state["council"]["reason_short"] == "Sniper structure not ready"
    assert "n/a" not in rendered.lower()


def test_floating_state_hides_na_fields() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": None,
            "packet_type": "STUDY_PACKET",
            "side": "n/a",
            "raw_side": "n/a",
            "execution": {"enabled": False, "state": "WATCHING", "side": None},
            "model_council": {"final_state": "WATCHING", "final_side": None},
            "promotion_trace": {"next_required": "stable candidate"},
        },
    )

    compact = {key: value for key, value in state.items() if key != "inspector"}
    rendered = str(compact).lower()
    assert "n/a" not in rendered
    assert state["packet"]["id_short"] == ""
    assert state["packet"]["side"] == ""


def test_floating_state_maps_execution_packet() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": "exec_1779374240",
            "packet_type": "PG_EXECUTION_PACKET_V3",
            "execution": {"enabled": True, "state": "EXECUTABLE", "side": "SELL", "expiry_seconds": 300},
            "model_council": {"final_execution_score": 0.78, "execution_threshold": 0.70},
            "execution_lane": {"name": "LOCAL_BREAKDOWN_CONTINUATION", "accepted": True},
            "timing_decision": {
                "timing_mode": "ENTER_NOW",
                "path_class": "DIRECT_CONTINUATION",
                "preferred_expiry_sec": 300,
                "time_to_reward_sec": 420,
                "time_to_invalidation_sec": 90,
                "entry_now_allowed": True,
                "timing_forecast": {
                    "best_entry_mode": "ENTER_NOW",
                    "recommended_expiry_sec": 300,
                    "expected_time_to_target_sec": 420,
                    "expected_time_to_invalidation_sec": 90,
                    "entry_now_quality": "GOOD",
                },
                "current_candle_phase": {
                    "timeframe": "M5",
                    "seconds_elapsed": 120,
                    "seconds_remaining": 180,
                    "phase": "MID_CANDLE",
                },
            },
        },
        action_payload={"phase": "TIME_PANEL_READY", "step": "minute_input"},
    )

    assert state["state_chip"] == "ACTION"
    assert state["packet"]["type"] == "EXECUTABLE"
    assert state["council"]["side"] == "SELL"
    assert state["council"]["lane_short"] == "BREAKDOWN"
    assert state["council"]["score_gap"] < 0
    assert state["timing"]["mode"] == "ENTER_NOW"
    assert state["timing"]["path_class"] == "DIRECT_CONTINUATION"
    assert state["timing"]["summary"].startswith("Timing: Enter now")
    assert state["timing"]["drawdown_first_warning"] == {}


def test_compact_mode_shows_packet_score_lane_timing() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": "exec_compact_truth",
            "packet_type": "PG_EXECUTION_PACKET_V3",
            "execution": {"enabled": True, "state": "EXECUTABLE", "side": "BUY", "expiry_seconds": 300},
            "model_council": {"final_state": "EXECUTABLE", "final_side": "BUY", "final_execution_score": 0.82, "execution_threshold": 0.70},
            "execution_lane": {"name": "SNIPER_ZONE_ENTRY", "accepted": True},
            "timing_decision": {"timing_mode": "ENTER_NOW", "entry_now_allowed": True},
        },
    )

    assert state["packet"]["type"] == "EXECUTABLE"
    assert state["council"]["final_score"] == 0.82
    assert state["council"]["threshold"] == 0.70
    assert state["council"]["lane_short"] == "SNIPER"
    assert state["timing"]["mode"] == "ENTER_NOW"


def test_inspector_mode_shows_raw_packet() -> None:
    raw_packet: dict[str, Any] = {
        "packet_id": "exec_raw_packet",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "execution": {"enabled": True, "state": "EXECUTABLE", "side": "SELL", "expiry_seconds": 300},
    }

    state = build_floating_state(session_id="pocket-live-8788", signal_payload=raw_packet)

    assert state["inspector"]["packet_raw"]["packet_id"] == "exec_raw_packet"
    assert "packet_raw" not in state["packet"]


def test_floating_state_score_gap_and_cooldown_chip() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_type": "STUDY_PACKET",
            "model_council": {"final_execution_score": 0.64, "execution_threshold": 0.70},
        },
        cooldown_remaining_seconds=50,
    )

    assert state["state_chip"] == "COOLDOWN"
    assert round(state["council"]["score_gap"], 2) == 0.06
    assert state["shooter"]["cooldown_remaining_sec"] == 50


def test_floating_state_model_health_summary() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        tracker_payload={
            "status": "running",
            "model_health": {"models_awake": 7, "models_total": 7},
            "cache_status": "fresh",
            "_fetch_latency_sec": 0.2,
        },
    )

    assert state["health"]["tracker"] == "RUNNING"
    assert state["health"]["models_awake"] == 7
    assert state["health"]["models_total"] == 7
    assert state["health"]["latency_sec"] == 0.2


def test_floating_state_is_typed() -> None:
    typed = FloatingStateV2.from_dict(
        {
            "session_id": "pocket-live-8788",
            "mode": "live",
            "timestamp": 1.0,
            "state_chip": "study",
            "packet": {"type": "STUDY"},
        }
    )

    assert typed.session_id == "pocket-live-8788"
    assert typed.as_dict()["schema_version"] == "FloatingStateV2"


def test_floating_state_shows_instrument_context_wait() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": "study_context_wait",
            "packet_type": "STUDY_PACKET",
            "execution": {"enabled": False, "state": "BLOCKED_BY_RUNTIME", "side": "BUY"},
            "model_council": {
                "final_state": "BLOCKED_BY_RUNTIME",
                "final_side": "BUY",
                "final_execution_score": 0.74,
                "execution_threshold": 0.70,
                "true_blocker": "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE",
                "next_required": "instrument_context.broker_click_safe=true",
            },
            "instrument_context": {
                "instrument_context_state": "USER_PROFILE_LOCKED",
                "broker_click_safe": False,
                "timeframe": "M5",
                "release_condition": "stable viewport + broker surface lock",
                "reason": "User profile locked; waiting for broker surface evidence.",
            },
        },
    )

    assert state["instrument"]["state"] == "USER_PROFILE_LOCKED"
    assert state["instrument"]["broker_click_safe"] is False
    assert state["instrument"]["next_required"] == "stable viewport + broker surface lock"


def test_floating_state_shows_broker_click_safe() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": "exec_safe",
            "packet_type": "PG_EXECUTION_PACKET_V3",
            "execution": {"enabled": True, "state": "EXECUTABLE", "side": "SELL", "expiry_seconds": 300},
            "model_council": {"final_state": "EXECUTABLE", "final_side": "SELL", "final_execution_score": 0.81},
            "instrument_context": {
                "instrument_context_state": "BROKER_CLICK_SAFE",
                "broker_click_safe": True,
                "timeframe": "M5",
                "release_condition": "none",
                "reason": "Stable broker surface.",
                "evidence": {"viewport_hash_stable": True},
            },
        },
    )

    assert state["instrument"]["state"] == "BROKER_CLICK_SAFE"
    assert state["instrument"]["broker_click_safe"] is True
    assert state["instrument"]["evidence"]["viewport_hash_stable"] is True


def test_floating_state_matches_runtime_trace() -> None:
    trace_packet: dict[str, Any] = {
        "packet_id": "pgpkt_trace_truth",
        "packet_type": "STUDY_PACKET",
        "execution": {"enabled": False, "state": "PREPARING", "side": "BUY"},
        "model_council": {"final_state": "PREPARING", "final_side": "BUY", "final_execution_score": 0.69, "execution_threshold": 0.70},
        "promotion_trace": {
            "timing_mode": "WAIT_FOR_RETEST",
            "selected_lane": "FAILED_RETEST_ENTRY",
            "next_required": "failed retest confirmation",
        },
        "instrument_context": {"instrument_context_state": "USER_PROFILE_LOCKED", "broker_click_safe": False},
    }

    state = build_floating_state(session_id="pocket-live-8788", signal_payload=trace_packet)

    assert state["packet"]["type"] == "STUDY"
    assert state["council"]["state"] == "PREPARING"
    assert state["council"]["side"] == "BUY"
    assert state["timing"]["mode"] == "WAIT_FOR_RETEST"
    assert state["instrument"]["broker_click_safe"] is False


def test_floating_window_reports_next_required() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_type": "STUDY_PACKET",
            "promotion_trace": {"next_required": "instrument_context.broker_click_safe=true"},
        },
    )

    assert state["council"]["next_required"] == "instrument context.broker click safe=true"


def test_floating_state_uses_packet_runtime_model_health_when_tracker_summary_missing() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": "study_runtime_health",
            "packet_type": "STUDY_PACKET",
            "runtime_model_health": {
                "all_required_models_awake": True,
                "required_roles": [
                    "global_structure",
                    "local_micro_structure",
                    "zone_liquidity",
                    "angle_dynamics",
                    "historical_pattern",
                    "risk_opposing_force",
                    "arbitration_synthesis",
                ],
            },
        },
        tracker_payload={"status": "running", "cache_status": "fresh"},
    )

    assert state["health"]["models_awake"] == 7
    assert state["health"]["models_total"] == 7
