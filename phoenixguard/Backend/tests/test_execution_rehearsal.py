from __future__ import annotations

from typing import Any

from phoenixguard.execution.execution_rehearsal import rehearse_execution


NOW = 1000.0


def _packet(*, broker_click_safe: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_id": "pgpkt_rehearsal",
        "created_epoch": NOW - 0.1,
        "valid_until_epoch": NOW + 2.0,
        "live_integrity": {"packet_age_ms": 100},
        "instrument_context": {
            "identity_state": "IDENTITY_CONFIRMED",
            "display_symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "broker_click_safe": broker_click_safe,
        },
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": "BUY",
            "expiry_seconds": 300,
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
            "time_sequence": {
                "target_seconds": 300,
                "target_text": "00:05:00",
                "steps": [{"action": "focus_time_field"}, {"action": "type_time", "value": "00:05:00"}],
            },
        },
        "model_council": {"final_state": "EXECUTABLE", "final_side": "BUY"},
        "runtime_model_health": {"all_required_models_awake": True},
        "overlay_truth_audit": {"valid_for_execution": True},
    }


def _boxes() -> dict[str, dict[str, float]]:
    return {
        "buy_icon": {"x": 0.8, "y": 0.4},
        "sell_icon": {"x": 0.8, "y": 0.5},
        "time_button": {"x": 0.8, "y": 0.2},
        "time_300": {"x": 0.7, "y": 0.25},
        "hourly_input": {"x": 0.75, "y": 0.2},
        "minute_input": {"x": 0.78, "y": 0.2},
    }


def test_execution_rehearsal_reports_retired_broker_coordinates() -> None:
    result = rehearse_execution(
        _packet(),
        {"gate_1_second_read": "PASS"},
        _boxes(),
        (0, 0, 1000, 800),
        now_epoch=NOW,
    )

    assert result["ready"] is False
    assert result["would_click"] == ""
    assert result["would_type_time"] == "00:05:00"
    assert result["packet_still_valid_after_latency"] is True
    assert "BROKER_COORDINATE_EXECUTION_RETIRED" in result["issues"]
    assert result["coordinate_report"]["execution_removed"] is True


def test_execution_rehearsal_blocks_unconfirmed_broker_identity() -> None:
    result = rehearse_execution(
        _packet(broker_click_safe=False),
        {"gate_1_second_read": "PASS"},
        _boxes(),
        (0, 0, 1000, 800),
        now_epoch=NOW,
    )

    assert result["ready"] is False
    assert "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE" in result["issues"]


def test_execution_rehearsal_blocks_bad_coordinates_and_candle_wait() -> None:
    packet = _packet()
    packet["current_candle_contract"] = {"entry_allowed_phase": "WAIT"}
    result = rehearse_execution(
        packet,
        {"gate_1_second_read": "PASS"},
        {"buy_icon": {"x": 1.2, "y": 0.4}},
        (0, 0, 1000, 800),
        now_epoch=NOW,
    )

    assert result["ready"] is False
    assert "BROKER_COORDINATE_EXECUTION_RETIRED" in result["issues"]
    assert "CURRENT_CANDLE_PHASE_NOT_EXECUTABLE" in result["issues"]
