from __future__ import annotations

from copy import deepcopy
from typing import Any

from phoenixguard.execution.execution_constitution import (
    CONSTITUTION_RULES,
    evaluate_execution_constitution,
)


NOW = 1000.0


def _packet() -> dict[str, Any]:
    return {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_id": "pgpkt_constitution",
        "created_epoch": NOW - 0.1,
        "valid_until_epoch": NOW + 2.0,
        "live_integrity": {"packet_age_ms": 100},
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
        "trade_permission": {"permission_state": "GRANTED", "executable_allowed": True},
        "entry_quality": {"state": "ACCEPTABLE_ENTRY", "passes_executable_threshold": True},
        "market_trap": {"detected": False, "executable_allowed": True},
        "overlay_truth_audit": {"valid_for_execution": True, "execution_safe": True},
    }


def test_execution_constitution_lists_hard_rules() -> None:
    assert "NO_RAW_SIGNAL_CAN_EXECUTE" in CONSTITUTION_RULES
    assert "NO_AMOUNT_CHANGE_ALLOWED" in CONSTITUTION_RULES
    assert "NO_UNVERIFIED_OVERLAY_GEOMETRY_CAN_EXECUTE" in CONSTITUTION_RULES
    assert "NO_PERMISSION_DENIED_PACKET_CAN_EXECUTE" in CONSTITUTION_RULES
    assert "NO_BAD_ENTRY_QUALITY_CAN_EXECUTE" in CONSTITUTION_RULES
    assert "NO_MISSING_TRADE_PERMISSION_CAN_EXECUTE" in CONSTITUTION_RULES
    assert "NO_MISSING_ENTRY_QUALITY_CAN_EXECUTE" in CONSTITUTION_RULES
    assert "NO_MISSING_MARKET_TRAP_TRUTH_CAN_EXECUTE" in CONSTITUTION_RULES


def test_execution_constitution_accepts_clean_packet() -> None:
    result = evaluate_execution_constitution(
        _packet(),
        {"gate_1_second_read": "PASS"},
        now_epoch=NOW,
    )

    assert result.ok is True
    assert result.violations == ()


def test_execution_constitution_blocks_raw_signal() -> None:
    result = evaluate_execution_constitution({"action": "BUY"}, {"gate_1_second_read": "PASS"}, now_epoch=NOW)

    assert result.ok is False
    assert "NO_RAW_SIGNAL_CAN_EXECUTE" in result.violations


def test_execution_constitution_blocks_side_mismatch_and_amount_change() -> None:
    packet = _packet()
    packet["model_council"]["final_side"] = "SELL"
    packet["execution"]["amount_action"] = "SET_AMOUNT"

    result = evaluate_execution_constitution(packet, {"gate_1_second_read": "PASS"}, now_epoch=NOW)

    assert "NO_SIDE_MISMATCH_CAN_EXECUTE" in result.violations
    assert "NO_AMOUNT_CHANGE_ALLOWED" in result.violations


def test_execution_constitution_blocks_trap_and_bad_overlay() -> None:
    packet = _packet()
    packet["trap_assessment"] = {
        "active_traps": [{"trap": "LATE_CHASE_AFTER_IMPULSE", "severity": 0.88}],
        "execution_allowed": False,
    }
    packet["overlay_truth_audit"] = {"valid_for_execution": False}

    result = evaluate_execution_constitution(packet, {"gate_1_second_read": "PASS"}, now_epoch=NOW)

    assert "NO_LATE_CHASE_TRAP_CAN_EXECUTE" in result.violations
    assert "NO_UNVERIFIED_OVERLAY_GEOMETRY_CAN_EXECUTE" in result.violations


def test_execution_constitution_blocks_permission_denial_and_bad_entry_quality() -> None:
    packet = _packet()
    packet["trade_permission"] = {"executable_allowed": False, "deny_reason": "LATE_CHASE_TRAP"}
    packet["entry_quality"] = {"state": "BAD_NOW", "passes_executable_threshold": False}

    result = evaluate_execution_constitution(packet, {"gate_1_second_read": "PASS"}, now_epoch=NOW)

    assert "NO_PERMISSION_DENIED_PACKET_CAN_EXECUTE" in result.violations
    assert "NO_BAD_ENTRY_QUALITY_CAN_EXECUTE" in result.violations


def test_execution_constitution_fails_closed_when_execution_truth_is_missing() -> None:
    packet = _packet()
    packet.pop("trade_permission")
    packet.pop("entry_quality")
    packet.pop("market_trap")
    packet.pop("overlay_truth_audit")

    result = evaluate_execution_constitution(packet, {"gate_1_second_read": "PASS"}, now_epoch=NOW)

    assert "NO_MISSING_TRADE_PERMISSION_CAN_EXECUTE" in result.violations
    assert "NO_MISSING_ENTRY_QUALITY_CAN_EXECUTE" in result.violations
    assert "NO_MISSING_MARKET_TRAP_TRUTH_CAN_EXECUTE" in result.violations
    assert "NO_UNVERIFIED_OVERLAY_GEOMETRY_CAN_EXECUTE" in result.violations


def test_execution_constitution_rejects_denied_council_copy() -> None:
    packet = _packet()
    packet["model_council"]["trade_permission"] = {
        "permission_state": "DENIED",
        "executable_allowed": False,
        "deny_reason": "COUNCIL_VETO",
    }
    packet["model_council"]["entry_quality"] = {
        "state": "BAD_NOW",
        "passes_executable_threshold": False,
    }

    result = evaluate_execution_constitution(packet, {"gate_1_second_read": "PASS"}, now_epoch=NOW)

    assert "NO_PERMISSION_DENIED_PACKET_CAN_EXECUTE" in result.violations
    assert "NO_BAD_ENTRY_QUALITY_CAN_EXECUTE" in result.violations


def test_execution_constitution_blocks_stale_and_first_read() -> None:
    packet = deepcopy(_packet())
    packet["valid_until_epoch"] = NOW - 0.1

    result = evaluate_execution_constitution(packet, {"gate_1_second_read": "WAIT"}, now_epoch=NOW)

    assert "NO_STALE_PACKET_CAN_EXECUTE" in result.violations
    assert "NO_FIRST_READ_PACKET_CAN_EXECUTE" in result.violations
