from __future__ import annotations

from copy import deepcopy

from phoenixguard.execution.packet_v3 import (
    MODEL_COUNCIL,
    PG_EXECUTION_PACKET_SCHEMA_VERSION,
    RUNTIME_INTEGRITY,
    SCHEMA_INTEGRITY,
    build_execution_packet_v3,
    packet_age_ms,
    packet_identity,
    resolve_execution_side,
    resolve_expiry_seconds,
    validate_execution_packet_v3,
)
from tests.support.v3_packet_samples import complete_sequence_context_v3


NOW = 1_800_000_000.0


def _packet(**overrides):
    payload = build_execution_packet_v3(
        packet_id="pgpkt-test-001",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=5438,
        capture_count=5440,
        state_version=99182,
        created_epoch=NOW - 0.2,
        valid_until_epoch=NOW + 2.0,
        side="BUY",
        expiry_seconds=300,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "abc123",
            "previous_frame_hash": "def456",
            "packet_age_ms": 180,
        },
        model_council={
            "final_state": "EXECUTABLE",
            "final_side": "BUY",
            "decision_id": "mc-test-001",
            "maturity_stage": "EXECUTABLE_PACKET",
            "buy_score": 0.78,
            "sell_score": 0.14,
            "hold_score": 0.08,
            "dominance_margin": 0.64,
            "disagreement_score": 0.18,
            "flip_flop_state": "STABLE_EXECUTABLE",
        },
        runtime_model_health={
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "max_model_latency_ms": 78,
            "queue_depth": 0,
        },
        sequence_context=complete_sequence_context_v3(
            sequence_id="seq-pgpkt-test-001",
            session_id="pocket-live-8788",
            side="BUY",
        ),
    )
    _deep_update(payload, overrides)
    return payload


def _deep_update(target, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def test_execution_packet_schema_v3_valid() -> None:
    payload = _packet()
    result = validate_execution_packet_v3(
        payload,
        now_epoch=NOW,
        expected_session_id="pocket-live-8788",
        expected_symbol="EUR/GBP OTC",
        expected_timeframe="M5",
    )

    assert result.accepted is True
    assert result.reason_codes == ()
    assert result.runtime_integrity == "PASS"
    assert result.side == "BUY"
    assert result.expiry_seconds == 300
    assert payload["instrument_context"]["identity_state"] == "IDENTITY_CONFIRMED"
    assert payload["instrument_context"]["display_symbol"] == "EUR/GBP OTC"
    assert payload["instrument_context"]["ocr_symbol"] == ""
    assert payload["instrument_context"]["timeframe"] == "M5"
    assert payload["instrument_context"]["paper_safe"] is True
    assert payload["symbol_context"]["display_symbol"] == "EUR/GBP OTC"
    assert payload["provenance"]["sequence_id"] == "seq-pgpkt-test-001"
    assert payload["model_council"]["sequence_context"]["schema_version"] == "PG_SEQUENCE_CONTEXT_V3"


def test_execution_packet_builder_does_not_synthesize_sequence_context() -> None:
    payload = build_execution_packet_v3(
        packet_id="pgpkt-no-sequence",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=5438,
        capture_count=5440,
        state_version=99182,
        created_epoch=NOW - 0.2,
        valid_until_epoch=NOW + 2.0,
        side="BUY",
        expiry_seconds=300,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "abc123",
            "previous_frame_hash": "def456",
        },
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
    )

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert "sequence_context" not in payload["model_council"]
    assert result.rejected is True
    assert "MISSING_SEQUENCE_CONTEXT" in result.reason_codes
    assert "INCOMPLETE_PACKET_PROVENANCE" in result.reason_codes


def test_side_field_resolution_consistent() -> None:
    payload = _packet()
    assert resolve_execution_side(payload) == "BUY"

    mismatched = deepcopy(payload)
    mismatched["model_council"]["final_side"] = "SELL"
    result = validate_execution_packet_v3(mismatched, now_epoch=NOW)

    assert resolve_execution_side(mismatched) is None
    assert result.rejected is True
    assert "EXECUTION_SIDE_MODEL_COUNCIL_MISMATCH" in result.reason_codes
    assert MODEL_COUNCIL in result.categories


def test_expiry_field_resolution_consistent() -> None:
    payload = _packet()
    assert resolve_expiry_seconds(payload) == 300

    mismatched = deepcopy(payload)
    mismatched["execution"]["time_sequence"]["target_seconds"] = 60
    result = validate_execution_packet_v3(mismatched, now_epoch=NOW)

    assert resolve_expiry_seconds(mismatched) is None
    assert result.rejected is True
    assert "INVALID_OR_MISSING_EXPIRY_SECONDS" in result.reason_codes


def test_trade_permission_denied_rejects_executable_packet() -> None:
    payload = _packet()
    payload["trade_permission"] = {
        "executable_allowed": False,
        "deny_reason": "ENTRY_QUALITY_BELOW_ACCEPTABLE",
    }

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "TRADE_PERMISSION_DENIED" in result.reason_codes
    assert MODEL_COUNCIL in result.categories


def test_bad_entry_quality_rejects_executable_packet() -> None:
    payload = _packet()
    payload["entry_quality"] = {
        "state": "BAD_NOW",
        "score": 0.21,
        "passes_executable_threshold": False,
    }

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "ENTRY_QUALITY_BELOW_ACCEPTABLE" in result.reason_codes
    assert MODEL_COUNCIL in result.categories


def test_market_trap_rejects_executable_packet() -> None:
    payload = _packet()
    payload["market_trap"] = {
        "active_traps": [{"trap": "LATE_CHASE_AFTER_IMPULSE", "severity": 0.88}],
        "execution_allowed": False,
    }

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "MARKET_TRAP_EXECUTION_DENIED" in result.reason_codes
    assert MODEL_COUNCIL in result.categories


def test_overlay_truth_rejects_executable_packet() -> None:
    payload = _packet()
    payload["overlay_truth_audit"] = {"valid_for_execution": False}

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "OVERLAY_TRUTH_NOT_EXECUTION_SAFE" in result.reason_codes
    assert MODEL_COUNCIL in result.categories


def test_session_mismatch_rejected() -> None:
    result = validate_execution_packet_v3(
        _packet(),
        now_epoch=NOW,
        expected_session_id="different-session",
    )

    assert result.rejected is True
    assert "SESSION_ID_MISMATCH" in result.reason_codes
    assert RUNTIME_INTEGRITY in result.categories
    assert "MARKET_BLOCKER" not in result.categories


def test_frame_id_must_advance_for_live_packet() -> None:
    payload = _packet()
    previous = packet_identity(payload)

    result = validate_execution_packet_v3(payload, now_epoch=NOW, previous_identity=previous)

    assert result.rejected is True
    assert "FRAME_ID_NOT_ADVANCING" in result.reason_codes
    assert "CAPTURE_COUNT_NOT_ADVANCING" in result.reason_codes
    assert "STATE_VERSION_NOT_ADVANCING" in result.reason_codes
    assert RUNTIME_INTEGRITY in result.categories


def test_old_schema_packet_rejected() -> None:
    payload = _packet(schema_version="PG_EXECUTION_PACKET_V2")

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert result.reason_codes == ("INVALID_SCHEMA_VERSION",)
    assert result.categories == (SCHEMA_INTEGRITY,)


def test_raw_signal_not_executable_packet() -> None:
    raw_signal = {
        "signal_id": "legacy-001",
        "actionable": True,
        "execution_action": "BUY",
        "entry_state": "SNIPER_READY",
        "expiry_seconds": 300,
    }

    result = validate_execution_packet_v3(raw_signal, now_epoch=NOW)

    assert result.rejected is True
    assert "RAW_SIGNAL_NOT_EXECUTION_PACKET" in result.reason_codes
    assert result.categories == (SCHEMA_INTEGRITY,)


def test_call_put_side_aliases_are_rejected() -> None:
    payload = _packet(execution={"side": "CALL"}, model_council={"final_side": "CALL"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "INVALID_OR_MISSING_EXECUTION_SIDE" in result.reason_codes
    assert any("FINAL_SIDE" in code for code in result.reason_codes)


def test_missing_model_council_final_side_rejected() -> None:
    payload = _packet()
    payload["model_council"].pop("final_side")

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert any("FINAL_SIDE" in code for code in result.reason_codes)


def test_stale_valid_until_rejected_as_runtime_integrity() -> None:
    payload = _packet(valid_until_epoch=NOW - 0.1)

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "PACKET_EXPIRED" in result.reason_codes
    assert RUNTIME_INTEGRITY in result.categories
    assert "MARKET_BLOCKER" not in result.categories


def test_execution_packet_expires_after_valid_until_epoch_sec() -> None:
    payload = _packet(valid_until_epoch=NOW + 2.0, valid_until_epoch_sec=NOW - 0.001)

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "PACKET_EXPIRED" in result.reason_codes


def test_packet_age_ms_uses_seconds_epoch_correctly() -> None:
    payload = _packet(created_epoch_sec=NOW - 1.25, created_epoch=NOW - 1.25)

    assert packet_age_ms(payload, now_epoch=NOW) == 1250


def test_invalid_side_enum_rejected() -> None:
    payload = _packet(execution={"side": "CALL"}, model_council={"final_side": "CALL"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "INVALID_SIDE_ENUM" in result.reason_codes


def test_invalid_execution_state_rejected() -> None:
    payload = _packet(execution={"state": "READY_TO_FIRE"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "INVALID_EXECUTION_STATE_ENUM" in result.reason_codes


def test_invalid_instrument_context_state_rejected() -> None:
    payload = _packet(instrument_context={"instrument_context_state": "BROKER_READYISH"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "INVALID_INSTRUMENT_CONTEXT_STATE_ENUM" in result.reason_codes
