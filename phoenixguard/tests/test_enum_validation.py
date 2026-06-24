from __future__ import annotations

from tests.test_execution_packet_schema_v3 import NOW, packet

from phoenixguard.execution.packet_v3 import validate_execution_packet_v3


def test_s2_invalid_side_enum_rejected() -> None:
    payload = packet(execution={"side": "CALL"}, model_council={"final_side": "CALL"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "INVALID_SIDE_ENUM" in result.reason_codes


def test_s2_invalid_execution_state_rejected() -> None:
    payload = packet(execution={"state": "READY_TO_FIRE"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "INVALID_EXECUTION_STATE_ENUM" in result.reason_codes


def test_s2_invalid_instrument_context_state_rejected() -> None:
    payload = packet(instrument_context={"instrument_context_state": "BROKER_READYISH"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "INVALID_INSTRUMENT_CONTEXT_STATE_ENUM" in result.reason_codes
