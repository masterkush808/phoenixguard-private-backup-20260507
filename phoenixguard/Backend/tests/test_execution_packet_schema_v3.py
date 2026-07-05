from __future__ import annotations
import pytest

from copy import deepcopy
from typing import Any, cast

from phoenixguard.execution.packet_v3 import (
    MODEL_COUNCIL,
    RUNTIME_INTEGRITY,
    SCHEMA_INTEGRITY,
    build_execution_packet_v3,
    packet_age_ms,
    packet_identity,
    resolve_execution_side,
    resolve_expiry_seconds,
    validate_execution_packet_v3,
)
import phoenixguard.execution.packet_v3 as packet_v3
from tests.support.v3_packet_samples import complete_sequence_context_v3


NOW = 1_800_000_000.0


Payload = dict[str, Any]


def _allowance_package(
    *,
    package_type: str = "INTRADAY_ENTER_NOW",
    side: str = "BUY",
    accepted: bool = True,
    execution_ready: bool = True,
) -> Payload:
    thesis_horizon: Payload = {
        "expected_duration_sec": 1800,
        "expected_duration_text": "30m 00s",
        "expected_candle_count": 6,
        "timeframe": "M5",
        "timeframe_seconds": 300,
        "minimum_professional_candles": 4,
        "basis": "test_professional_visible_history_memory_trend_plan",
    }
    professional_trade_plan: Payload = {
        "schema_version": "PG_PROFESSIONAL_TRADE_PLAN_V3",
        "side": side,
        "authority_side": side,
        "professional_grade": True,
        "blocker": "",
        "professional_thesis_state": "TREND_ALIGNED_CONTINUATION",
        "thesis_horizon": thesis_horizon,
    }
    expected_move_time: Payload = {
        **thesis_horizon,
        "professional_trade_plan": professional_trade_plan,
    }
    return {
        "schema_version": "PG_ALLOWANCE_PACKAGE_V1",
        "package_type": package_type,
        "allowance_family": "INTRADAY" if package_type == "INTRADAY_ENTER_NOW" else "SWING",
        "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3",
        "packet_authority": "PG_EXECUTION_PACKET_V3",
        "side": side,
        "accepted": accepted,
        "decision_accepted": accepted,
        "execution_ready": execution_ready,
        "entry_now_allowed": package_type == "INTRADAY_ENTER_NOW",
        "timing_mode": "ENTER_NOW" if package_type == "INTRADAY_ENTER_NOW" else "WAIT_FOR_PULLBACK",
        "selected_lane": "SNIPER_ZONE_ENTRY",
        "score": 0.83,
        "threshold": 0.70,
        "professional_trade_plan": professional_trade_plan,
        "thesis_horizon": thesis_horizon,
        "expected_move_time": expected_move_time,
    }


def _packet(**overrides: Any) -> Payload:
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
        allowance_package=_allowance_package(),
    )
    _deep_update(payload, overrides)
    return payload


def _deep_update(target: Payload, updates: Payload) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(cast(Payload, target[key]), cast(Payload, value))
        else:
            target[key] = value


packet = _packet


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


def test_execution_packet_exports_playbook_horizon_and_score() -> None:
    payload = _packet()

    assert payload["allowance_package"]["packet_id"] == payload["packet_id"]
    assert payload["expected_move_time"]["expected_duration_sec"] == 1800
    assert payload["expected_move_time"]["expected_candle_count"] == 6
    assert payload["expected_duration_sec"] == 1800
    assert payload["expected_candle_count"] == 6
    assert payload["score"] == 0.83
    assert payload["final_score"] == 0.83
    assert payload["model_council"]["expected_move_time"]["expected_candle_count"] == 6
    assert payload["model_council"]["professional_trade_plan"]["professional_grade"] is True


def test_executable_packet_requires_explicit_allowance_package() -> None:
    payload = _packet()
    payload.pop("allowance_package")
    council = cast(Payload, payload["model_council"])
    council.pop("allowance_package")

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "MISSING_ALLOWANCE_PACKAGE" in result.reason_codes
    assert MODEL_COUNCIL in result.categories


def test_executable_packet_rejects_non_ready_allowance_package() -> None:
    payload = _packet(allowance_package=_allowance_package(execution_ready=False))

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "ALLOWANCE_PACKAGE_NOT_EXECUTION_READY" in result.reason_codes
    assert MODEL_COUNCIL in result.categories


def test_execution_packet_publication_age_overrides_source_frame_age(monkeypatch: pytest.MonkeyPatch) -> None:
    def _now_epoch() -> float:
        return NOW

    monkeypatch.setattr(packet_v3, "now_epoch", _now_epoch)

    payload = build_execution_packet_v3(
        packet_id="pgpkt-age-contract",
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
            "packet_age_ms": 45_000,
        },
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        sequence_context=complete_sequence_context_v3(
            sequence_id="seq-pgpkt-age-contract",
            session_id="pocket-live-8788",
            side="BUY",
        ),
        allowance_package=_allowance_package(),
    )

    assert payload["live_integrity"]["packet_age_ms"] == 200
    assert payload["live_integrity"]["source_frame_age_ms"] == 45_000
    result = validate_execution_packet_v3(payload, now_epoch=NOW, expected_session_id="pocket-live-8788")
    assert result.accepted is True
    assert payload["instrument_context"]["timeframe"] == "M5"
    assert payload["instrument_context"]["paper_safe"] is True
    assert payload["symbol_context"]["display_symbol"] == "EUR/GBP OTC"
    assert payload["provenance"]["sequence_id"] == "seq-pgpkt-age-contract"
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


def test_fallback_expiry_source_rejected_for_execution_packet() -> None:
    payload = _packet(expiry_source="timeframe_fallback")

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "FALLBACK_EXPIRY_SOURCE" in result.reason_codes


def test_nested_fallback_expiry_source_rejected_for_execution_packet() -> None:
    payload = _packet(execution={"expiry_source": "operator_fallback(--expiry)"})

    result = validate_execution_packet_v3(payload, now_epoch=NOW)

    assert result.rejected is True
    assert "FALLBACK_EXPIRY_SOURCE" in result.reason_codes


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
    raw_signal: dict[str, Any] = {
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


def test_execution_packet_exposes_explicit_ttl_seconds() -> None:
    payload = _packet()

    assert round(float(payload["ttl_sec"]), 3) == 2.2
    assert round(float(payload["valid_for_seconds"]), 3) == 2.2


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
