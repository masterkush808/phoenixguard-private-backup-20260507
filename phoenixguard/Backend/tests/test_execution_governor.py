from __future__ import annotations

from typing import Any

from phoenixguard.execution import ExecutionGovernor, validate_fire_command


NOW = 1_800_000_000.0
Payload = dict[str, Any]


def _context(**overrides: Any) -> Payload:
    base: Payload = {
        "now": NOW,
        "broker_layout_id": "broker-layout-v1",
        "calibration_profile_id": "calibration-v1",
        "max_latency_seconds": 3.0,
    }
    base.update(overrides)
    return base


def _command(**overrides: Any) -> Payload:
    base: Payload = {
        "signal_id": "sig-001",
        "session_id": "session-a",
        "symbol": "EURUSD_OTC",
        "timeframe": "M1",
        "side": "BUY",
        "entry_state": "ARMED",
        "expiry_seconds": 60,
        "expiry_source": "signal_contract",
        "created_at": NOW - 1.0,
        "published_at": NOW - 0.5,
        "valid_until": NOW + 30.0,
        "max_latency_seconds": 3.0,
        "decision_kernel": {"state": "ARMED", "side": "BUY"},
        "tracker": {"side": "BUY"},
        "hypotheses": {"resolved": True, "selected_side": "BUY"},
        "timing": {"window_open": True, "late_candle": False, "candle_closed": False},
        "broker_layout_id": "broker-layout-v1",
        "calibration_profile_id": "calibration-v1",
        "post_click_verification_required": True,
    }
    base.update(overrides)
    return base


def _assert_blocked(command: Payload, reason_code: str, context: Payload | None = None) -> Any:
    decision = validate_fire_command(command, context=context or _context())
    assert decision.blocked
    assert reason_code in decision.reason_codes
    return decision


def test_blocks_missing_decision_kernel() -> None:
    command = _command()
    command.pop("decision_kernel")
    _assert_blocked(command, "MISSING_DECISION_KERNEL")


def test_blocks_fallback_expiry_source() -> None:
    _assert_blocked(_command(expiry_source="fallback_derived"), "FALLBACK_EXPIRY_SOURCE")


def test_blocks_tracker_fire_side_mismatch() -> None:
    _assert_blocked(_command(tracker={"side": "SELL"}), "TRACKER_SIDE_MISMATCH")


def test_blocks_stale_signal() -> None:
    _assert_blocked(_command(valid_until=NOW - 0.1), "STALE_SIGNAL")


def test_blocks_duplicate_signal_id() -> None:
    governor = ExecutionGovernor()
    first = governor.validate(_command(), _context())
    assert first.approved

    duplicate = governor.validate(_command(), _context())
    assert duplicate.blocked
    assert "DUPLICATE_SIGNAL_ID" in duplicate.reason_codes


def test_blocks_unresolved_dual_hypotheses() -> None:
    _assert_blocked(_command(hypotheses={"resolved": False}), "DUAL_HYPOTHESIS_UNRESOLVED")


def test_blocks_late_candle() -> None:
    _assert_blocked(
        _command(timing={"window_open": True, "late_candle": True, "candle_closed": False}),
        "LATE_CANDLE",
    )


def test_blocks_broker_layout_mismatch() -> None:
    _assert_blocked(_command(broker_layout_id="wrong-layout"), "BROKER_LAYOUT_MISMATCH")


def test_blocks_latency_violation() -> None:
    _assert_blocked(_command(published_at=NOW - 5.0), "LATENCY_VIOLATION")


def test_blocks_cooldown() -> None:
    _assert_blocked(_command(cooldown_until=NOW + 5.0), "COOLDOWN_ACTIVE")


def test_blocks_missing_post_click_verification_requirement() -> None:
    _assert_blocked(
        _command(post_click_verification_required=False),
        "POST_CLICK_VERIFICATION_REQUIRED",
    )


def test_approves_complete_fire_command() -> None:
    decision = validate_fire_command(_command(signal_id="sig-complete"), context=_context())

    assert decision.approved
    assert not decision.blocked
    assert decision.reason_codes == ("APPROVED",)
    assert decision.signal_id == "sig-complete"
    assert decision.side == "BUY"
