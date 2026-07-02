from __future__ import annotations

from tests.test_model_council_v3 import (
    NOW,
    assert_non_executable_release_fields,
    broker_click_unsafe_result,
    second_packet,
    strong_snapshot,
)

from phoenixguard.decision.model_council_v3 import ModelCouncilV3


def test_s1_watching_state_has_traceable_release_fields() -> None:
    snapshot = strong_snapshot("BUY", frame_id=100)
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
    assert_non_executable_release_fields(result)


def test_s1_legacy_broker_click_mode_publishes_paper_source_packet() -> None:
    result = broker_click_unsafe_result()

    assert result["execution"]["enabled"] is True
    assert result["promotion_trace"]["release_state"] == "EXECUTION_PACKET_PUBLISHED"
    assert result["promotion_trace"]["instrument_context_broker_click_safe"] is False
    assert result["instrument_context"]["paper_safe"] is True
    assert result["instrument_context"]["broker_click_safe"] is False
    assert result["promotion_trace"]["next_required"] == "none"
    assert result["promotion_trace"]["release_condition"] == "none"


def test_s1_execution_packet_publishes_after_release_conditions_pass() -> None:
    packet = second_packet("BUY")

    assert packet["execution"]["enabled"] is True
    assert packet["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert packet["packet_id"]
    assert packet["promotion_trace"]["candidate_stage"] == "EXECUTION_PACKET_PUBLISHED"
    assert packet["promotion_trace"]["timing_mode"] == "ENTER_NOW"
    assert packet["promotion_trace"]["release_condition"] == "none"
