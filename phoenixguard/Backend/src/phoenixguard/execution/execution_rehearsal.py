from __future__ import annotations

import time
from typing import Any, Mapping, cast

from phoenixguard.execution.execution_constitution import evaluate_execution_constitution


EXECUTION_REHEARSAL_VERSION = "PG_EXECUTION_REHEARSAL_V1"


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _side(value: Any) -> str:
    side = _text(value).upper()
    if side in {"BUY", "SELL"}:
        return side
    return ""


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _retired_coordinate_report() -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "BROKER_COORDINATE_EXECUTION_RETIRED",
        "execution_removed": True,
        "broker_click_allowed": False,
    }


def rehearse_execution(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    boxes: Mapping[str, Any],
    window_bounds: tuple[int, int, int, int] | list[int],
    *,
    latest_packet: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
    estimated_execution_latency_ms: float = 230.0,
    require_broker_click_safe: bool = True,
    max_packet_age_seconds: float = 2.0,
) -> dict[str, Any]:
    now = time.time() if now_epoch is None else float(now_epoch)
    execution = _mapping(packet.get("execution"))
    instrument_context = _mapping(packet.get("instrument_context"))
    time_sequence = _mapping(execution.get("time_sequence"))
    side = _side(execution.get("side"))
    expiry_seconds = int(_float(execution.get("expiry_seconds"), _float(time_sequence.get("target_seconds"), 0.0)))
    _ = (boxes, window_bounds, side, expiry_seconds)
    coordinate_report = _retired_coordinate_report()
    constitution = evaluate_execution_constitution(
        packet,
        decision,
        now_epoch=now,
        first_read_confirmed=True,
        max_packet_age_seconds=max_packet_age_seconds,
    )
    latest = _mapping(latest_packet)
    latest_side = _side(_mapping(latest.get("execution")).get("side")) if latest else side
    latest_council_side = _side(_mapping(latest.get("model_council")).get("final_side")) if latest else side
    valid_until = _float(packet.get("valid_until_epoch"), 0.0)
    expected_click_epoch = now + max(0.0, float(estimated_execution_latency_ms)) / 1000.0

    issues: list[str] = []
    if not constitution.ok:
        issues.extend(constitution.violations)
    if not time_sequence:
        issues.append("TIME_SEQUENCE_MISSING")
    if not time_sequence.get("target_seconds") or not time_sequence.get("target_text"):
        issues.append("TIME_SEQUENCE_TARGET_MISSING")
    if not coordinate_report.get("ok"):
        issues.append("BROKER_COORDINATE_EXECUTION_RETIRED")
    if require_broker_click_safe and instrument_context.get("broker_click_safe") is not True:
        issues.append("INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE")
    if latest_packet is not None and (latest_side != side or latest_council_side != side):
        issues.append("LATEST_COUNCIL_SIDE_MISMATCH")
    if valid_until <= expected_click_epoch:
        issues.append("PACKET_WOULD_EXPIRE_BEFORE_CLICK")
    candle = _mapping(packet.get("current_candle_contract"))
    if str(candle.get("entry_allowed_phase") or "").upper() in {"WAIT", "NO_ENTRY", "BLOCK"}:
        issues.append("CURRENT_CANDLE_PHASE_NOT_EXECUTABLE")

    unique = list(dict.fromkeys(issues))
    return {
        "version": EXECUTION_REHEARSAL_VERSION,
        "ready": not unique,
        "reason": unique[0] if unique else "EXECUTION_REHEARSAL_READY",
        "issues": unique,
        "would_click": "",
        "would_type_time": _text(time_sequence.get("target_text")),
        "estimated_execution_latency_ms": round(float(estimated_execution_latency_ms), 3),
        "packet_still_valid_after_latency": valid_until > expected_click_epoch,
        "coordinate_report": coordinate_report,
        "constitution": constitution.as_dict(),
    }


__all__ = ["EXECUTION_REHEARSAL_VERSION", "rehearse_execution"]
