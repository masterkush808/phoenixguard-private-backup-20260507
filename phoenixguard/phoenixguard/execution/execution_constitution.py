from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping, Sequence


EXECUTION_CONSTITUTION_VERSION = "PG_EXECUTION_CONSTITUTION_V1"

CONSTITUTION_RULES: tuple[str, ...] = (
    "NO_RAW_SIGNAL_CAN_EXECUTE",
    "NO_FIRST_READ_PACKET_CAN_EXECUTE",
    "NO_STALE_PACKET_CAN_EXECUTE",
    "NO_SIDE_MISMATCH_CAN_EXECUTE",
    "NO_MISSING_TIME_SEQUENCE_CAN_EXECUTE",
    "NO_AMOUNT_CHANGE_ALLOWED",
    "NO_LATE_CHASE_TRAP_CAN_EXECUTE",
    "NO_SIMULTANEOUS_BUY_SELL_CAN_EXECUTE",
    "NO_MODEL_HEALTH_FAILURE_CAN_EXECUTE",
    "NO_UNVERIFIED_OVERLAY_GEOMETRY_CAN_EXECUTE",
    "NO_PERMISSION_DENIED_PACKET_CAN_EXECUTE",
    "NO_BAD_ENTRY_QUALITY_CAN_EXECUTE",
)


@dataclass(frozen=True)
class ConstitutionResult:
    ok: bool
    violations: tuple[str, ...] = field(default_factory=tuple)
    reason: str = "OK"

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": EXECUTION_CONSTITUTION_VERSION,
            "ok": self.ok,
            "violations": list(self.violations),
            "reason": self.reason,
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(value)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _side(value: Any) -> str:
    side = _upper(value)
    if side in {"BUY", "CALL"}:
        return "BUY"
    if side in {"SELL", "PUT"}:
        return "SELL"
    return ""


def _trap_active(packet: Mapping[str, Any]) -> bool:
    for container_name in ("market_reality", "trap_assessment", "market_context"):
        container = _mapping(packet.get(container_name))
        if not container:
            continue
        if _bool(container.get("late_chase_risk")) or _bool(container.get("is_late_chase")):
            return True
        active_traps = _sequence(container.get("active_traps"))
        for raw in active_traps:
            trap = _mapping(raw)
            name = _upper(trap.get("trap") or trap.get("name") or raw)
            if name in {
                "LATE_CHASE_AFTER_IMPULSE",
                "NO_PULLBACK_AFTER_VERTICAL_MOVE",
                "HISTORY_SAYS_EXIT_NOT_ENTRY",
                "OPPOSITE_FORCE_TOO_CLOSE",
                "TREND_ANGLE_BREAK_RISK",
            } and _float(trap.get("severity"), 1.0) >= 0.5:
                return True
        if container.get("execution_allowed") is False:
            return True
    return False


def _overlay_verified(packet: Mapping[str, Any]) -> bool:
    overlay = _mapping(packet.get("overlay_truth_audit") or packet.get("overlay_geometry") or packet.get("overlay_context"))
    if not overlay:
        return True
    if overlay.get("valid_for_execution") is False:
        return False
    if overlay.get("execution_safe") is False:
        return False
    objects = _sequence(overlay.get("objects") or overlay.get("overlay_objects"))
    decision_objects = [obj for obj in objects if _mapping(obj).get("used_for_decision") or _mapping(obj).get("valid_for_decision")]
    if not decision_objects:
        return True
    return all(_bool(_mapping(obj).get("valid_for_decision")) for obj in decision_objects)


def _permission_allows_execution(packet: Mapping[str, Any]) -> bool:
    council = _mapping(packet.get("model_council"))
    permission = _mapping(packet.get("trade_permission") or council.get("trade_permission"))
    if not permission:
        return True
    return permission.get("executable_allowed") is True


def _entry_quality_allows_execution(packet: Mapping[str, Any]) -> bool:
    council = _mapping(packet.get("model_council"))
    entry_quality = _mapping(packet.get("entry_quality") or council.get("entry_quality"))
    if not entry_quality:
        return True
    state = _upper(
        entry_quality.get("state")
        or entry_quality.get("entry_grade")
        or entry_quality.get("grade")
        or entry_quality.get("quality")
    )
    if not state:
        return True
    if state in {"A_PLUS_ENTRY", "GOOD_ENTRY", "ACCEPTABLE_ENTRY"}:
        return True
    if entry_quality.get("passes_executable_threshold") is True:
        return True
    return False


def evaluate_execution_constitution(
    packet: Mapping[str, Any] | None,
    decision: Mapping[str, Any] | None = None,
    *,
    now_epoch: float | None = None,
    first_read_confirmed: bool = True,
    max_packet_age_seconds: float = 2.0,
) -> ConstitutionResult:
    violations: list[str] = []
    now = time.time() if now_epoch is None else float(now_epoch)
    decision = _mapping(decision)
    if not isinstance(packet, Mapping):
        return ConstitutionResult(False, ("NO_RAW_SIGNAL_CAN_EXECUTE",), "NO_RAW_SIGNAL_CAN_EXECUTE")

    if packet.get("schema_version") != "PG_EXECUTION_PACKET_V3":
        violations.append("NO_RAW_SIGNAL_CAN_EXECUTE")
    if not first_read_confirmed or str(decision.get("gate_1_second_read") or "").upper() not in {"PASS", ""}:
        violations.append("NO_FIRST_READ_PACKET_CAN_EXECUTE")

    created = _float(packet.get("created_epoch"), 0.0)
    valid_until = _float(packet.get("valid_until_epoch"), 0.0)
    live = _mapping(packet.get("live_integrity"))
    packet_age_ms = _float(live.get("packet_age_ms"), -1.0)
    if created > 0.0:
        packet_age = max(0.0, now - created)
    elif packet_age_ms >= 0.0:
        packet_age = packet_age_ms / 1000.0
    else:
        packet_age = max_packet_age_seconds + 1.0
    if created <= 0.0 or valid_until <= now or packet_age > max(0.05, float(max_packet_age_seconds)):
        violations.append("NO_STALE_PACKET_CAN_EXECUTE")

    execution = _mapping(packet.get("execution"))
    council = _mapping(packet.get("model_council"))
    execution_side = _side(execution.get("side"))
    final_side = _side(council.get("final_side"))
    if execution_side not in {"BUY", "SELL"} or final_side not in {"BUY", "SELL"} or execution_side != final_side:
        violations.append("NO_SIDE_MISMATCH_CAN_EXECUTE")

    time_sequence = _mapping(execution.get("time_sequence"))
    if not time_sequence or not time_sequence.get("target_seconds") or not time_sequence.get("target_text") or not _sequence(time_sequence.get("steps")):
        violations.append("NO_MISSING_TIME_SEQUENCE_CAN_EXECUTE")

    if _upper(execution.get("amount_action") or "DO_NOT_CHANGE_AMOUNT") != "DO_NOT_CHANGE_AMOUNT":
        violations.append("NO_AMOUNT_CHANGE_ALLOWED")

    if _trap_active(packet):
        violations.append("NO_LATE_CHASE_TRAP_CAN_EXECUTE")

    if _bool(execution.get("buy_executable") and execution.get("sell_executable")):
        violations.append("NO_SIMULTANEOUS_BUY_SELL_CAN_EXECUTE")
    for key in ("sides", "executable_sides", "final_sides"):
        sides = {_side(item) for item in _sequence(execution.get(key) or council.get(key))}
        if {"BUY", "SELL"}.issubset(sides):
            violations.append("NO_SIMULTANEOUS_BUY_SELL_CAN_EXECUTE")
            break

    health = _mapping(packet.get("runtime_model_health"))
    if health.get("all_required_models_awake") is not True:
        violations.append("NO_MODEL_HEALTH_FAILURE_CAN_EXECUTE")

    if not _overlay_verified(packet):
        violations.append("NO_UNVERIFIED_OVERLAY_GEOMETRY_CAN_EXECUTE")

    if not _permission_allows_execution(packet):
        violations.append("NO_PERMISSION_DENIED_PACKET_CAN_EXECUTE")

    if not _entry_quality_allows_execution(packet):
        violations.append("NO_BAD_ENTRY_QUALITY_CAN_EXECUTE")

    unique = tuple(dict.fromkeys(violations))
    return ConstitutionResult(not unique, unique, unique[0] if unique else "OK")


__all__ = [
    "CONSTITUTION_RULES",
    "EXECUTION_CONSTITUTION_VERSION",
    "ConstitutionResult",
    "evaluate_execution_constitution",
]
