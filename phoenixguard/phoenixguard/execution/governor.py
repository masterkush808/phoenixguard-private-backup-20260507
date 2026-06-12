from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


REASON_APPROVED = "APPROVED"
_SIDES = {"BUY", "SELL"}
_ARMED_STATES = {
    "ARMED",
    "READY",
    "READY_TO_FIRE",
    "FIRE_ARMED",
    "SNIPER_READY",
    "TRIGGER_READY",
    "TRIGGERED",
    "ACTIVE",
    "EXECUTE",
}
_FALLBACK_EXPIRY_SOURCES = {"fallback", "fallback_derived", "default", "derived_fallback"}


@dataclass(frozen=True)
class ExecutionDecision:
    approved: bool
    reason_codes: tuple[str, ...]
    signal_id: str | None = None
    side: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return not self.approved

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "blocked": self.blocked,
            "reason_codes": list(self.reason_codes),
            "signal_id": self.signal_id,
            "side": self.side,
            "details": dict(self.details),
        }


class ExecutionGovernor:
    """Stateful guard that prevents replaying approved signal ids."""

    def __init__(self, consumed_signal_ids: Iterable[str] | None = None) -> None:
        self._consumed_signal_ids = set(consumed_signal_ids or ())

    @property
    def consumed_signal_ids(self) -> frozenset[str]:
        return frozenset(self._consumed_signal_ids)

    def validate(self, fire_command: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> ExecutionDecision:
        decision = validate_fire_command(
            fire_command,
            context=context,
            consumed_signal_ids=self._consumed_signal_ids,
        )
        if decision.approved and decision.signal_id:
            self._consumed_signal_ids.add(decision.signal_id)
        return decision


def validate_fire_command(
    fire_command: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
    consumed_signal_ids: Iterable[str] | None = None,
) -> ExecutionDecision:
    context = context or {}
    now = _timestamp(context.get("now"))
    if now is None:
        now = datetime.now(timezone.utc).timestamp()

    reasons: list[str] = []
    details: dict[str, Any] = {}

    signal_id = _clean_str(fire_command.get("signal_id"))
    if not signal_id:
        reasons.append("MISSING_SIGNAL_ID")
    elif signal_id in set(consumed_signal_ids or ()):
        reasons.append("DUPLICATE_SIGNAL_ID")

    _validate_identity_fields(fire_command, reasons)

    side = _normalize_side(fire_command.get("side"))
    if side is None:
        reasons.append("INVALID_SIDE")

    entry_state = _clean_str(fire_command.get("entry_state")).upper()
    if entry_state not in _ARMED_STATES:
        reasons.append("ENTRY_STATE_NOT_ARMED")

    _validate_expiry(fire_command, reasons)
    _validate_freshness(fire_command, context, now, reasons, details)
    _validate_decision_kernel(fire_command.get("decision_kernel"), side, reasons)
    _validate_tracker(fire_command.get("tracker"), side, reasons)
    _validate_hypotheses(fire_command, side, reasons)
    _validate_timing(fire_command.get("timing"), reasons)
    _validate_cooldown(fire_command, context, now, reasons)
    _validate_broker_layout(fire_command, context, reasons)
    _validate_calibration_profile(fire_command, context, reasons)

    if fire_command.get("post_click_verification_required") is not True:
        reasons.append("POST_CLICK_VERIFICATION_REQUIRED")

    if reasons:
        return ExecutionDecision(
            approved=False,
            reason_codes=tuple(dict.fromkeys(reasons)),
            signal_id=signal_id,
            side=side,
            details=details,
        )

    return ExecutionDecision(
        approved=True,
        reason_codes=(REASON_APPROVED,),
        signal_id=signal_id,
        side=side,
        details=details,
    )


def _validate_identity_fields(fire_command: Mapping[str, Any], reasons: list[str]) -> None:
    for field_name in ("session_id", "symbol", "timeframe"):
        if field_name in fire_command and not _clean_str(fire_command.get(field_name)):
            reasons.append(f"MISSING_{field_name.upper()}")


def _validate_expiry(fire_command: Mapping[str, Any], reasons: list[str]) -> None:
    expiry = _positive_float(fire_command.get("expiry_seconds"))
    if expiry is None:
        reasons.append("MISSING_EXPLICIT_EXPIRY")

    expiry_source = _clean_str(fire_command.get("expiry_source")).lower()
    if not expiry_source:
        reasons.append("MISSING_EXPIRY_SOURCE")
    elif expiry_source in _FALLBACK_EXPIRY_SOURCES or "fallback" in expiry_source:
        reasons.append("FALLBACK_EXPIRY_SOURCE")


def _validate_freshness(
    fire_command: Mapping[str, Any],
    context: Mapping[str, Any],
    now: float,
    reasons: list[str],
    details: dict[str, Any],
) -> None:
    max_latency = _positive_float(fire_command.get("max_latency_seconds"))
    if max_latency is None:
        max_latency = _positive_float(context.get("max_latency_seconds"))
    if max_latency is None:
        reasons.append("MISSING_MAX_LATENCY")
        return

    for field_name in ("created_at", "published_at"):
        stamp = _timestamp(fire_command.get(field_name))
        if stamp is None:
            reasons.append(f"MISSING_{field_name.upper()}")
            continue
        age = now - stamp
        details[f"{field_name}_age_seconds"] = age
        if age < 0:
            reasons.append(f"{field_name.upper()}_IN_FUTURE")
        elif age > max_latency:
            reasons.append("LATENCY_VIOLATION")

    valid_until = _timestamp(fire_command.get("valid_until"))
    if valid_until is None:
        reasons.append("MISSING_VALID_UNTIL")
    elif valid_until < now:
        reasons.append("STALE_SIGNAL")


def _validate_decision_kernel(kernel: Any, side: str | None, reasons: list[str]) -> None:
    if not isinstance(kernel, Mapping):
        reasons.append("MISSING_DECISION_KERNEL")
        return

    kernel_state = _clean_str(kernel.get("state")).upper()
    if kernel_state and kernel_state not in _ARMED_STATES:
        reasons.append("DECISION_KERNEL_NOT_ARMED")

    kernel_side = _normalize_side(
        kernel.get("side")
        or kernel.get("dominant_side")
        or kernel.get("fire_side")
        or kernel.get("approved_side")
    )
    if kernel_side is None:
        reasons.append("DECISION_KERNEL_MISSING_SIDE")
    elif side is not None and kernel_side != side:
        reasons.append("DECISION_KERNEL_SIDE_MISMATCH")


def _validate_tracker(tracker: Any, side: str | None, reasons: list[str]) -> None:
    if not isinstance(tracker, Mapping):
        reasons.append("MISSING_TRACKER")
        return
    tracker_side = _normalize_side(tracker.get("side") or tracker.get("fire_side") or tracker.get("direction"))
    if tracker_side is None:
        reasons.append("TRACKER_MISSING_SIDE")
    elif side is not None and tracker_side != side:
        reasons.append("TRACKER_SIDE_MISMATCH")


def _validate_hypotheses(fire_command: Mapping[str, Any], side: str | None, reasons: list[str]) -> None:
    hypotheses = fire_command.get("hypotheses")
    if isinstance(hypotheses, Mapping):
        resolved = hypotheses.get("resolved") is True
        selected_side = _normalize_side(hypotheses.get("selected_side") or hypotheses.get("resolved_side"))
    else:
        resolved = fire_command.get("dual_hypothesis_resolved") is True
        selected_side = _normalize_side(fire_command.get("resolved_hypothesis_side"))

    if not resolved:
        reasons.append("DUAL_HYPOTHESIS_UNRESOLVED")
    elif selected_side is not None and side is not None and selected_side != side:
        reasons.append("DUAL_HYPOTHESIS_SIDE_MISMATCH")


def _validate_timing(timing: Any, reasons: list[str]) -> None:
    if not isinstance(timing, Mapping):
        reasons.append("MISSING_TIMING_FLAGS")
        return
    if timing.get("late_candle") is True:
        reasons.append("LATE_CANDLE")
    if timing.get("window_open") is not True:
        reasons.append("TIMING_WINDOW_CLOSED")
    if timing.get("candle_closed") is True:
        reasons.append("CANDLE_ALREADY_CLOSED")


def _validate_cooldown(
    fire_command: Mapping[str, Any],
    context: Mapping[str, Any],
    now: float,
    reasons: list[str],
) -> None:
    cooldown_until = _timestamp(fire_command.get("cooldown_until"))
    if cooldown_until is None:
        cooldown_until = _timestamp(context.get("cooldown_until"))
    if cooldown_until is not None and now < cooldown_until:
        reasons.append("COOLDOWN_ACTIVE")


def _validate_broker_layout(
    fire_command: Mapping[str, Any],
    context: Mapping[str, Any],
    reasons: list[str],
) -> None:
    layout_id = _clean_str(fire_command.get("broker_layout_id"))
    expected_layout_id = _clean_str(context.get("broker_layout_id") or context.get("expected_broker_layout_id"))
    if not layout_id:
        reasons.append("MISSING_BROKER_LAYOUT_ID")
    elif expected_layout_id and layout_id != expected_layout_id:
        reasons.append("BROKER_LAYOUT_MISMATCH")


def _validate_calibration_profile(
    fire_command: Mapping[str, Any],
    context: Mapping[str, Any],
    reasons: list[str],
) -> None:
    profile_id = _clean_str(fire_command.get("calibration_profile_id"))
    expected_profile_id = _clean_str(context.get("calibration_profile_id") or context.get("expected_calibration_profile_id"))
    if not profile_id:
        reasons.append("MISSING_CALIBRATION_PROFILE_ID")
    elif expected_profile_id and profile_id != expected_profile_id:
        reasons.append("CALIBRATION_PROFILE_MISMATCH")


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_side(value: Any) -> str | None:
    side = _clean_str(value).upper()
    return side if side in _SIDES else None


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _timestamp(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = _clean_str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
