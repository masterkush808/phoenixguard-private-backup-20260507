from __future__ import annotations

from tests.test_execution_packet_schema_v3 import NOW, _packet

from phoenixguard.execution.packet_v3 import packet_age_ms, validate_execution_packet_v3


def test_s2_execution_packet_expires_after_valid_until_epoch_sec() -> None:
    payload = _packet(valid_until_epoch=NOW + 2.0, valid_until_epoch_sec=NOW - 0.001)

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "PACKET_EXPIRED" in result.reason_codes


def test_s2_packet_age_ms_uses_seconds_epoch_correctly() -> None:
    payload = _packet(created_epoch_sec=NOW - 1.25, created_epoch=NOW - 1.25)

    assert packet_age_ms(payload, now_epoch=NOW) == 1250


def test_s2_millisecond_epoch_is_rejected() -> None:
    payload = _packet(created_epoch_sec=NOW * 1000.0, created_epoch=NOW * 1000.0)

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "TIMESTAMP_UNIT_NOT_SECONDS" in result.reason_codes


def test_s2_ambiguous_top_level_timestamp_field_is_rejected() -> None:
    payload = _packet(timestamp=NOW)

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "AMBIGUOUS_TOP_LEVEL_TIME_FIELD" in result.reason_codes
