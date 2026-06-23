from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping, Sequence, cast

from phoenixguard.execution.v3_language import (
    CalibrationState,
    CouncilState,
    ExecutionState,
    InstrumentContextState,
    PacketType,
    ShooterActionState,
    Side,
    TimingMode,
)
from phoenixguard.runtime.instrument_context import (
    INSTRUMENT_CONTEXT_LOCK_SCHEMA_VERSION,
    build_instrument_context,
    symbol_context_from_instrument_context,
    validate_instrument_context,
)
from phoenixguard.execution.sequence_context import (
    SEQUENCE_CONTEXT_MIN_BOX_HISTORY_LEN,
    SEQUENCE_CONTEXT_MIN_CONFIDENCE,
    SEQUENCE_CONTEXT_MIN_PROGRESSION_LEN,
    resolve_sequence_context,
)


EXECUTION_PACKET_SCHEMA_VERSION = "PG_EXECUTION_PACKET_V3"
PG_EXECUTION_PACKET_SCHEMA_VERSION = EXECUTION_PACKET_SCHEMA_VERSION
ALLOWANCE_PACKAGE_SCHEMA_VERSION = "PG_ALLOWANCE_PACKAGE_V1"
STUDY_PACKET_SCHEMA_VERSION = "PG_MODEL_COUNCIL_STUDY_V3"
STUDY_PACKET_TYPE = "STUDY_PACKET"
EXECUTION_PACKET_TYPE = EXECUTION_PACKET_SCHEMA_VERSION
PG_CACHE_SCHEMA_VERSION = "PG_CACHE_V3_MODEL_COUNCIL_EXECUTION"
EXECUTABLE_STATE = "EXECUTABLE"
FRESH_CACHE_STATUS = "fresh"
VALID_SIDE_VALUES = {member.value for member in Side}
VALID_EXECUTION_SIDES = {"BUY", "SELL"}
VALID_PACKET_TYPES = {member.value for member in PacketType}
VALID_ALLOWANCE_PACKAGE_TYPES = {"SWING", "INTRADAY_ENTER_NOW"}
MAX_REASONABLE_EPOCH_SECONDS = 10_000_000_000.0
RUNTIME_INTEGRITY_CATEGORY = "RUNTIME_INTEGRITY"
RUNTIME_INTEGRITY = RUNTIME_INTEGRITY_CATEGORY
SCHEMA_INTEGRITY = "SCHEMA"
MODEL_COUNCIL = "MODEL_COUNCIL"
AMBIGUOUS_TOP_LEVEL_TIME_FIELDS = frozenset(("timestamp", "valid_until", "age", "latency", "time"))
FALLBACK_EXPIRY_SOURCES = frozenset(
    (
        "fallback",
        "fallback_derived",
        "derived_fallback",
        "timeframe_fallback",
        "default",
        "derived_default",
        "operator_fallback",
    )
)


@dataclass(frozen=True)
class PacketValidationIssue:
    code: str
    category: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


ValidationIssue = PacketValidationIssue


@dataclass(frozen=True)
class PacketValidationResult:
    ok: bool
    executable: bool
    issues: tuple[PacketValidationIssue, ...] = field(default_factory=tuple)
    side: str | None = None
    expiry_seconds: int | None = None
    packet_id: str | None = None

    @property
    def accepted(self) -> bool:
        return self.ok

    @property
    def rejected(self) -> bool:
        return not self.ok

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(issue.category for issue in self.issues))

    @property
    def runtime_integrity(self) -> str:
        return "PASS" if self.runtime_integrity_ok else "FAIL"

    @property
    def first_reason(self) -> str:
        if not self.issues:
            return "OK"
        return self.issues[0].code

    @property
    def runtime_integrity_ok(self) -> bool:
        return not any(issue.category == RUNTIME_INTEGRITY_CATEGORY for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "executable": self.executable,
            "side": self.side,
            "expiry_seconds": self.expiry_seconds,
            "packet_id": self.packet_id,
            "runtime_integrity": self.runtime_integrity,
            "issues": [issue.as_dict() for issue in self.issues],
            "first_reason": self.first_reason,
        }


def now_epoch() -> float:
    return float(time.time())


def normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text in VALID_EXECUTION_SIDES:
        return text
    return None


def _enum_values(enum_cls: Any) -> set[str]:
    return {str(member.value).upper() for member in enum_cls}


def _enum_text(value: Any) -> str:
    return _clean_str(value).upper()


def _epoch_seconds(payload: Mapping[str, Any], standard_key: str, alias_key: str) -> float:
    standard = _float(payload.get(standard_key), 0.0)
    if standard > 0.0:
        return standard
    return _float(payload.get(alias_key), 0.0)


def _epoch_alias_mismatch(payload: Mapping[str, Any], standard_key: str, alias_key: str) -> bool:
    if standard_key not in payload or alias_key not in payload:
        return False
    standard = _float(payload.get(standard_key), 0.0)
    alias = _float(payload.get(alias_key), 0.0)
    return standard > 0.0 and alias > 0.0 and abs(standard - alias) > 0.001


def packet_age_ms(packet: Mapping[str, Any], *, now_epoch: float | None = None) -> int:
    created = _epoch_seconds(packet, "created_epoch_sec", "created_epoch")
    if created <= 0.0:
        return 0
    current = globals()["now_epoch"]() if now_epoch is None else float(now_epoch)
    return max(0, int(round((current - created) * 1000.0)))


def is_packet_current(packet: Mapping[str, Any], *, now_epoch: float | None = None) -> bool:
    valid_until = _epoch_seconds(packet, "valid_until_epoch_sec", "valid_until_epoch")
    if valid_until <= 0.0:
        return False
    current = globals()["now_epoch"]() if now_epoch is None else float(now_epoch)
    return valid_until > current


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(cast(Sequence[Any], value))


def _is_fallback_expiry_source(value: Any) -> bool:
    text = _clean_str(value).lower()
    if not text:
        return False
    return text in FALLBACK_EXPIRY_SOURCES or "fallback" in text


def _first_clean_text(*values: Any) -> str:
    for value in values:
        text = _clean_str(value)
        if text:
            return text
    return ""


def _entry_quality_allows_execution(entry_quality: Mapping[str, Any]) -> bool:
    state = _clean_str(
        entry_quality.get("state")
        or entry_quality.get("entry_grade")
        or entry_quality.get("grade")
        or entry_quality.get("quality")
    ).upper()
    if not state:
        return True
    if state in {"PERFECT_ENTRY", "IDEAL_ENTRY", "A_PLUS_ENTRY", "GOOD_ENTRY", "VALID_ENTRY", "AGGRESSIVE_VALID_ENTRY", "ACCEPTABLE_ENTRY"}:
        return True
    if entry_quality.get("passes_executable_threshold") is True:
        return True
    return False


def _market_trap_blocks_execution(trap: Mapping[str, Any]) -> bool:
    if not trap:
        return False
    if trap.get("execution_allowed") is False or trap.get("executable_allowed") is False:
        return True
    if trap.get("trap_free") is False:
        return True
    active_traps = _sequence(trap.get("active_traps") or trap.get("traps"))
    for raw in active_traps:
        item = _mapping(raw)
        severity = _float(item.get("severity"), 1.0)
        if severity >= 0.5:
            return True
    return False


def _overlay_truth_blocks_execution(overlay: Mapping[str, Any]) -> bool:
    if not overlay:
        return False
    return overlay.get("valid_for_execution") is False or overlay.get("execution_safe") is False


def parse_expiry_seconds(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if parsed > 0 else None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        parsed = int(text)
        return parsed if parsed > 0 else None
    parts = text.split(":")
    if len(parts) in {2, 3} and all(part.strip().isdigit() for part in parts):
        nums = [int(part) for part in parts]
        if len(nums) == 2:
            minutes, seconds = nums
            total = minutes * 60 + seconds
        else:
            hours, minutes, seconds = nums
            total = hours * 3600 + minutes * 60 + seconds
        return total if total > 0 else None
    return None


def format_expiry_text(expiry_seconds: int) -> str:
    seconds = max(1, int(expiry_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_time_sequence(expiry_seconds: int, *, mode: str = "TYPE_OR_ADJUST") -> dict[str, Any]:
    seconds = max(1, int(expiry_seconds))
    return {
        "mode": str(mode or "TYPE_OR_ADJUST"),
        "target_seconds": seconds,
        "target_text": format_expiry_text(seconds),
        "steps": [
            {"action": "focus_time_field"},
            {"action": "select_existing_time"},
            {"action": "type_time", "value": format_expiry_text(seconds)},
            {"action": "confirm_time"},
            {"action": "verify_time_if_possible"},
        ],
    }


def resolve_packet_side(packet: Mapping[str, Any]) -> str | None:
    execution = _mapping(packet.get("execution"))
    model_council = _mapping(packet.get("model_council"))
    execution_side = normalize_side(execution.get("side"))
    final_side = normalize_side(model_council.get("final_side"))
    if execution_side and final_side and execution_side == final_side:
        return execution_side
    return execution_side or final_side


def resolve_packet_expiry_seconds(packet: Mapping[str, Any]) -> int | None:
    execution = _mapping(packet.get("execution"))
    parsed = parse_expiry_seconds(execution.get("expiry_seconds"))
    if parsed:
        return parsed
    time_sequence = _mapping(execution.get("time_sequence"))
    return parse_expiry_seconds(time_sequence.get("target_seconds") or time_sequence.get("target_text"))


def resolve_execution_side(packet: Mapping[str, Any]) -> str | None:
    execution = _mapping(packet.get("execution"))
    model_council = _mapping(packet.get("model_council"))
    execution_side = normalize_side(execution.get("side"))
    final_side = normalize_side(model_council.get("final_side"))
    if execution_side in VALID_EXECUTION_SIDES and final_side in VALID_EXECUTION_SIDES and execution_side == final_side:
        return execution_side
    return None


def resolve_expiry_seconds(packet: Mapping[str, Any]) -> int | None:
    execution = _mapping(packet.get("execution"))
    time_sequence = _mapping(execution.get("time_sequence"))
    execution_expiry = parse_expiry_seconds(execution.get("expiry_seconds"))
    sequence_expiry = parse_expiry_seconds(time_sequence.get("target_seconds") or time_sequence.get("target_text"))
    if execution_expiry is not None and sequence_expiry is not None and execution_expiry == sequence_expiry:
        return execution_expiry
    return None


def packet_identity(packet: Mapping[str, Any]) -> dict[str, Any]:
    execution = _mapping(packet.get("execution"))
    live_integrity = _mapping(packet.get("live_integrity"))
    instrument_context = _mapping(packet.get("instrument_context"))
    return {
        "session_id": _clean_str(packet.get("session_id")),
        "symbol": _clean_str(packet.get("symbol") or instrument_context.get("display_symbol")),
        "timeframe": _clean_str(packet.get("timeframe") or instrument_context.get("timeframe")).upper(),
        "frame_id": _int(packet.get("frame_id")),
        "capture_count": _int(packet.get("capture_count")),
        "state_version": _int(packet.get("state_version")),
        "packet_id": _clean_str(packet.get("packet_id")),
        "side": normalize_side(execution.get("side")),
        "input_frame_hash": _clean_str(live_integrity.get("input_frame_hash")),
        "instrument_identity_state": _clean_str(instrument_context.get("identity_state")),
        "viewport_hash": _clean_str(instrument_context.get("viewport_hash")),
    }


def packet_identity_key(packet: Mapping[str, Any]) -> str:
    ident = packet_identity(packet)
    return "|".join(str(ident[key]) for key in (
        "session_id",
        "symbol",
        "timeframe",
        "frame_id",
        "capture_count",
        "state_version",
        "packet_id",
        "side",
        "input_frame_hash",
    ))


def build_execution_packet_v3(
    *,
    packet_id: str,
    session_id: str,
    symbol: str,
    timeframe: str,
    frame_id: int,
    capture_count: int,
    state_version: int,
    side: str,
    expiry_seconds: int,
    input_frame_hash: str = "",
    previous_frame_hash: str = "",
    created_epoch: float | None = None,
    valid_until_epoch: float | None = None,
    created_epoch_sec: float | None = None,
    valid_until_epoch_sec: float | None = None,
    valid_for_seconds: float = 2.0,
    live_integrity: Mapping[str, Any] | None = None,
    model_council: Mapping[str, Any] | None = None,
    market_context: Mapping[str, Any] | None = None,
    angle_context: Mapping[str, Any] | None = None,
    history_context: Mapping[str, Any] | None = None,
    runtime_model_health: Mapping[str, Any] | None = None,
    cache_status: str = FRESH_CACHE_STATUS,
    instrument_context: Mapping[str, Any] | None = None,
    symbol_context: Mapping[str, Any] | None = None,
    sequence_context: Mapping[str, Any] | None = None,
    allowance_package: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_side = normalize_side(side)
    if normalized_side not in VALID_EXECUTION_SIDES:
        raise ValueError("side must be BUY or SELL")
    expiry = max(1, int(expiry_seconds))
    if created_epoch_sec is not None:
        created = float(created_epoch_sec)
    elif created_epoch is not None:
        created = float(created_epoch)
    else:
        created = now_epoch()
    if valid_until_epoch_sec is not None:
        valid_until = float(valid_until_epoch_sec)
    elif valid_until_epoch is not None:
        valid_until = float(valid_until_epoch)
    else:
        valid_until = created + max(0.1, float(valid_for_seconds))
    ttl_seconds = max(0.1, float(valid_until) - float(created))
    live = dict(live_integrity or {})
    live_proof_present = bool(live)
    source_frame_age_ms = _int(live.get("packet_age_ms"), 0)
    publication_packet_age_ms = max(0, int(round((now_epoch() - created) * 1000.0)))
    resolved_input_frame_hash = str(input_frame_hash or live.get("input_frame_hash", ""))
    resolved_previous_frame_hash = str(previous_frame_hash or live.get("previous_frame_hash", ""))
    resolved_instrument_context = build_instrument_context(
        {
            "session_id": session_id,
            "symbol": symbol,
            "market": symbol,
            "timeframe": timeframe,
            "focus_timeframe": timeframe,
            "viewport_hash": _mapping(instrument_context).get("viewport_hash") or resolved_input_frame_hash,
            "input_frame_hash": resolved_input_frame_hash,
            "broker_surface_hash": _mapping(instrument_context).get("broker_surface_hash", ""),
            "instrument_context": dict(instrument_context or {}),
            "symbol_context": dict(symbol_context or {}),
        }
    )
    resolved_symbol_context = symbol_context_from_instrument_context(resolved_instrument_context)
    resolved_symbol_context.update(dict(symbol_context or {}))
    resolved_symbol = _clean_str(symbol or resolved_instrument_context.get("display_symbol"))
    resolved_timeframe = _clean_str(timeframe or resolved_instrument_context.get("timeframe")).upper()
    council = dict(model_council or {})
    resolved_allowance_package = dict(allowance_package or council.get("allowance_package") or {})
    if resolved_allowance_package:
        resolved_allowance_package.setdefault("schema_version", ALLOWANCE_PACKAGE_SCHEMA_VERSION)
        resolved_allowance_package.setdefault("execution_authority", EXECUTION_PACKET_SCHEMA_VERSION)
        council.setdefault("allowance_package", resolved_allowance_package)
    resolved_sequence_context = _mapping(sequence_context)
    if resolved_sequence_context:
        council["sequence_context"] = resolved_sequence_context
        council.setdefault("sequence_id", _clean_str(resolved_sequence_context.get("sequence_id")))
        council.setdefault("sequence_signature", _clean_str(resolved_sequence_context.get("sequence_signature")))
        council.setdefault("sequence_length", _int(resolved_sequence_context.get("sequence_length"), 0))
        council.setdefault("frames_used", _int(resolved_sequence_context.get("frames_used"), 0))
        council.setdefault("sequence_status", _clean_str(resolved_sequence_context.get("sequence_status")))
    council.setdefault("final_state", EXECUTABLE_STATE)
    council.setdefault("final_side", normalized_side)
    council.setdefault("decision_id", str(packet_id).replace("pgpkt", "mc", 1))
    council.setdefault("maturity_stage", "EXECUTABLE_PACKET")
    council.setdefault("arbitration_reason", "")
    council.setdefault("contributors_are_diagnostic", True)
    health = dict(runtime_model_health or {})
    health.setdefault("all_required_models_awake", False)
    health.setdefault(
        "council_status",
        "AWAKE" if health.get("all_required_models_awake") is True else "MISSING",
    )
    health.setdefault("max_model_latency_ms", 0)
    health.setdefault("queue_depth", 0)
    chart_transform = _mapping(
        council.get("chart_transform")
        or _mapping(council.get("overlay_geometry")).get("chart_transform")
        or _mapping(instrument_context).get("chart_transform")
    )
    broker_source_lock = _mapping(
        council.get("broker_source_lock")
        or _mapping(instrument_context).get("broker_source_lock")
        or live.get("broker_source_lock")
    )
    model_health_id = _first_clean_text(
        health.get("model_health_id"),
        health.get("health_id"),
        health.get("snapshot_id"),
        f"mh_{int(frame_id)}",
    )
    chart_transform_id = _first_clean_text(
        chart_transform.get("chart_transform_id"),
        council.get("chart_transform_id"),
        _mapping(instrument_context).get("chart_transform_id"),
        f"ct_{int(frame_id)}",
    )
    source_lock_id = _first_clean_text(
        broker_source_lock.get("lock_id"),
        broker_source_lock.get("source_lock_id"),
        broker_source_lock.get("viewport_fingerprint"),
        broker_source_lock.get("broker_pixel_fingerprint"),
        live.get("source_lock_id"),
        _mapping(instrument_context).get("source_lock_id"),
        f"source_lock_{int(frame_id)}",
    )
    provenance: dict[str, Any] = {
        "frame_id": int(frame_id),
        "capture_count": int(capture_count),
        "state_version": int(state_version),
        "sequence_id": _clean_str(resolved_sequence_context.get("sequence_id")),
        "source_lock_id": source_lock_id,
        "model_health_id": model_health_id,
        "chart_transform_id": chart_transform_id,
        "created_epoch_ms": int(round(created * 1000.0)),
        "valid_until_epoch_ms": int(round(valid_until * 1000.0)),
    }
    packet: dict[str, Any] = {
        "schema_version": EXECUTION_PACKET_SCHEMA_VERSION,
        "packet_type": EXECUTION_PACKET_TYPE,
        "packet_id": str(packet_id),
        "session_id": str(session_id),
        "symbol": resolved_symbol,
        "timeframe": resolved_timeframe,
        "frame_id": int(frame_id),
        "capture_count": int(capture_count),
        "state_version": int(state_version),
        "created_epoch_sec": created,
        "valid_until_epoch_sec": valid_until,
        "created_epoch": created,
        "valid_until_epoch": valid_until,
        "ttl_sec": ttl_seconds,
        "valid_for_seconds": ttl_seconds,
        "provenance": provenance,
        "instrument_context": resolved_instrument_context,
        "symbol_context": resolved_symbol_context,
        "sequence_id": _clean_str(resolved_sequence_context.get("sequence_id")),
        "sequence_signature": _clean_str(resolved_sequence_context.get("sequence_signature")),
        "sequence_length": _int(resolved_sequence_context.get("sequence_length"), 0),
        "frames_used": _int(resolved_sequence_context.get("frames_used"), 0),
        "sequence_status": _clean_str(resolved_sequence_context.get("sequence_status")),
        "sequence_confidence": _float(resolved_sequence_context.get("sequence_confidence"), 0.0),
        "frame_range": list(resolved_sequence_context.get("frame_range", [])) if isinstance(resolved_sequence_context.get("frame_range"), Sequence) else [],
        "candle_range": list(resolved_sequence_context.get("candle_range", [])) if isinstance(resolved_sequence_context.get("candle_range"), Sequence) else [],
        "live_integrity": {
            "is_live": live.get("is_live") is True,
            "frame_advancing": live.get("frame_advancing") is True,
            "capture_advancing": live.get("capture_advancing") is True,
            "state_advancing": live.get("state_advancing") is True,
            "source": str(live.get("source", "model_council") or "model_council"),
            "cache_status": str(
                live.get("cache_status")
                or ((cache_status or FRESH_CACHE_STATUS) if live_proof_present else "missing")
            ),
            "input_frame_hash": resolved_input_frame_hash,
            "previous_frame_hash": resolved_previous_frame_hash,
            "packet_age_ms": publication_packet_age_ms,
            "source_frame_age_ms": source_frame_age_ms,
        },
        "execution": {
            "enabled": True,
            "state": EXECUTABLE_STATE,
            "side": normalized_side,
            "expiry_seconds": expiry,
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
            "time_sequence": build_time_sequence(expiry),
        },
        "model_council": council,
        "market_context": dict(market_context or {}),
        "angle_context": dict(angle_context or {}),
        "history_context": dict(history_context or {}),
        "runtime_model_health": health,
        "block_reason": None,
    }
    if resolved_allowance_package:
        packet["allowance_package"] = resolved_allowance_package
        packet["execution"]["allowance_package_type"] = _clean_str(
            resolved_allowance_package.get("package_type")
        )
    return packet


def validate_execution_packet_v3(
    packet: Mapping[str, Any] | None,
    *,
    expected_session_id: str | None = None,
    expected_symbol: str | None = None,
    expected_timeframe: str | None = None,
    now: float | None = None,
    now_epoch: float | None = None,
    previous_packet: Mapping[str, Any] | None = None,
    previous_identity: Mapping[str, Any] | None = None,
    require_executable: bool = True,
    require_broker_click_safe_identity: bool = False,
) -> PacketValidationResult:
    issues: list[PacketValidationIssue] = []
    if now is not None:
        current_now = float(now)
    elif now_epoch is not None:
        current_now = float(now_epoch)
    else:
        current_now = globals()["now_epoch"]()

    def add(code: str, category: str, message: str) -> None:
        issues.append(PacketValidationIssue(code=code, category=category, message=message))

    if not isinstance(packet, Mapping):
        add("MISSING_PACKET", "SCHEMA", "Payload is not a mapping.")
        return PacketValidationResult(False, False, tuple(issues))

    if packet.get("schema_version") != EXECUTION_PACKET_SCHEMA_VERSION:
        rejection = raw_signal_rejection_reason(packet)
        if rejection == "RAW_SIGNAL_NOT_EXECUTABLE_PACKET":
            add("RAW_SIGNAL_NOT_EXECUTION_PACKET", "SCHEMA", "Raw action/execution_action payload is not a PG_EXECUTION_PACKET_V3 packet.")
        else:
            add("INVALID_SCHEMA_VERSION", "SCHEMA", "Payload is not a PG_EXECUTION_PACKET_V3 packet.")
        return PacketValidationResult(False, False, tuple(issues), packet_id=_clean_str(packet.get("packet_id")) or None)
    packet_type = _clean_str(packet.get("packet_type"))
    if packet_type and packet_type not in VALID_PACKET_TYPES:
        add(
            "INVALID_PACKET_TYPE_ENUM",
            "SCHEMA",
            "packet_type must be STUDY_PACKET or PG_EXECUTION_PACKET_V3.",
        )
    elif packet_type and packet_type != EXECUTION_PACKET_TYPE:
        add(
            "PACKET_TYPE_NOT_EXECUTION_PACKET",
            "SCHEMA",
            "packet_type must be PG_EXECUTION_PACKET_V3 for executable packets.",
        )

    for field_name in AMBIGUOUS_TOP_LEVEL_TIME_FIELDS:
        if field_name in packet:
            add(
                "AMBIGUOUS_TOP_LEVEL_TIME_FIELD",
                "SCHEMA",
                f"{field_name} is ambiguous; use created_epoch_sec, valid_until_epoch_sec, packet_age_ms, latency_ms, expiry_seconds, or cooldown_remaining_sec.",
            )

    packet_id = _clean_str(packet.get("packet_id"))
    if not packet_id:
        add("MISSING_PACKET_ID", "SCHEMA", "packet_id is required.")

    session_id = _clean_str(packet.get("session_id"))
    instrument_context = _mapping(packet.get("instrument_context"))
    symbol_context = _mapping(packet.get("symbol_context"))
    symbol = _clean_str(packet.get("symbol") or instrument_context.get("display_symbol") or symbol_context.get("display_symbol"))
    timeframe = _clean_str(packet.get("timeframe") or instrument_context.get("timeframe") or symbol_context.get("timeframe")).upper()
    if not session_id:
        add("MISSING_SESSION_ID", "SCHEMA", "session_id is required.")
    if expected_session_id and session_id != _clean_str(expected_session_id):
        add("SESSION_ID_MISMATCH", RUNTIME_INTEGRITY_CATEGORY, "Packet session_id does not match shooter session.")
    if not symbol:
        add("MISSING_SYMBOL", "SCHEMA", "symbol is required.")
    if expected_symbol and symbol and symbol != _clean_str(expected_symbol):
        add("SYMBOL_MISMATCH", RUNTIME_INTEGRITY_CATEGORY, "Packet symbol does not match expected symbol.")
    if not timeframe:
        add("MISSING_TIMEFRAME", "SCHEMA", "timeframe is required.")
    if expected_timeframe and timeframe and timeframe != _clean_str(expected_timeframe).upper():
        add("TIMEFRAME_MISMATCH", RUNTIME_INTEGRITY_CATEGORY, "Packet timeframe does not match expected timeframe.")

    if not instrument_context:
        add("MISSING_INSTRUMENT_CONTEXT", "SCHEMA", "instrument_context is required.")
    else:
        for field_name in (
            "identity_state",
            "display_symbol",
            "ocr_symbol",
            "timeframe",
            "viewport_hash",
            "broker_surface_hash",
            "confidence",
            "paper_safe",
            "broker_click_safe",
        ):
            if field_name not in instrument_context:
                add(
                    f"MISSING_INSTRUMENT_CONTEXT_{field_name.upper()}",
                    "SCHEMA",
                    f"instrument_context.{field_name} is required.",
                )
        if instrument_context.get("schema_version") not in {None, "", INSTRUMENT_CONTEXT_LOCK_SCHEMA_VERSION}:
            add("INVALID_INSTRUMENT_CONTEXT_SCHEMA", "SCHEMA", "instrument_context schema_version is invalid.")
        instrument_validation = validate_instrument_context(
            instrument_context,
            mode="broker_click" if require_broker_click_safe_identity else "paper",
        )
        if require_executable and not instrument_validation.ok:
            category = RUNTIME_INTEGRITY_CATEGORY
            for issue in instrument_validation.issues:
                add(issue, category, f"Instrument context is not valid for {instrument_validation.mode}.")

    if not symbol_context:
        add("MISSING_SYMBOL_CONTEXT", "SCHEMA", "symbol_context is required.")

    frame_id = _int(packet.get("frame_id"), -1)
    capture_count = _int(packet.get("capture_count"), -1)
    state_version = _int(packet.get("state_version"), -1)
    if frame_id < 0:
        add("MISSING_FRAME_ID", "SCHEMA", "frame_id must be present and non-negative.")
    if capture_count < 0:
        add("MISSING_CAPTURE_COUNT", "SCHEMA", "capture_count must be present and non-negative.")
    if state_version < 0:
        add("MISSING_STATE_VERSION", "SCHEMA", "state_version must be present and non-negative.")

    if previous_packet is not None or previous_identity is not None:
        resolved_previous_identity = dict(previous_identity or packet_identity(previous_packet or {}))
        if frame_id <= int(resolved_previous_identity.get("frame_id", -1)):
            add("FRAME_ID_NOT_ADVANCING", RUNTIME_INTEGRITY_CATEGORY, "frame_id must advance between live reads.")
        if capture_count <= int(resolved_previous_identity.get("capture_count", -1)):
            add("CAPTURE_COUNT_NOT_ADVANCING", RUNTIME_INTEGRITY_CATEGORY, "capture_count must advance between live reads.")
        if state_version <= int(resolved_previous_identity.get("state_version", -1)):
            add("STATE_VERSION_NOT_ADVANCING", RUNTIME_INTEGRITY_CATEGORY, "state_version must advance between live reads.")

    created_epoch = _epoch_seconds(packet, "created_epoch_sec", "created_epoch")
    valid_until_epoch = _epoch_seconds(packet, "valid_until_epoch_sec", "valid_until_epoch")
    valid_until_alias = _float(packet.get("valid_until_epoch"), 0.0)
    if created_epoch <= 0.0:
        add("MISSING_CREATED_EPOCH", "SCHEMA", "created_epoch_sec is required.")
    elif created_epoch > MAX_REASONABLE_EPOCH_SECONDS:
        add("TIMESTAMP_UNIT_NOT_SECONDS", "SCHEMA", "created_epoch_sec must be Unix epoch seconds, not milliseconds.")
    if _epoch_alias_mismatch(packet, "created_epoch_sec", "created_epoch"):
        add("CREATED_EPOCH_ALIAS_MISMATCH", "SCHEMA", "created_epoch_sec and created_epoch aliases must match.")
    if valid_until_epoch <= 0.0:
        add("MISSING_VALID_UNTIL_EPOCH", "SCHEMA", "valid_until_epoch_sec is required.")
    elif valid_until_epoch > MAX_REASONABLE_EPOCH_SECONDS:
        add("TIMESTAMP_UNIT_NOT_SECONDS", "SCHEMA", "valid_until_epoch_sec must be Unix epoch seconds, not milliseconds.")
    elif valid_until_epoch <= current_now or (valid_until_alias > 0.0 and valid_until_alias <= current_now):
        add("PACKET_EXPIRED", RUNTIME_INTEGRITY_CATEGORY, "Packet valid_until_epoch_sec has passed.")
    if _epoch_alias_mismatch(packet, "valid_until_epoch_sec", "valid_until_epoch"):
        add("VALID_UNTIL_EPOCH_ALIAS_MISMATCH", "SCHEMA", "valid_until_epoch_sec and valid_until_epoch aliases must match.")

    live_integrity = _mapping(packet.get("live_integrity"))
    provenance = _mapping(packet.get("provenance"))
    if not live_integrity:
        add("MISSING_LIVE_INTEGRITY", RUNTIME_INTEGRITY_CATEGORY, "live_integrity is required.")
    else:
        if live_integrity.get("is_live") is not True:
            add("NOT_LIVE", RUNTIME_INTEGRITY_CATEGORY, "live_integrity.is_live must be true.")
        if live_integrity.get("cache_status") != FRESH_CACHE_STATUS:
            add("CACHE_NOT_FRESH", RUNTIME_INTEGRITY_CATEGORY, "live_integrity.cache_status must be fresh.")
        if not _clean_str(live_integrity.get("input_frame_hash")):
            add("MISSING_INPUT_FRAME_HASH", RUNTIME_INTEGRITY_CATEGORY, "input_frame_hash is required.")
        for field_name, code in (
            ("frame_advancing", "FRAME_NOT_ADVANCING"),
            ("capture_advancing", "CAPTURE_NOT_ADVANCING"),
            ("state_advancing", "STATE_NOT_ADVANCING"),
        ):
            if live_integrity.get(field_name) is not True:
                add(code, RUNTIME_INTEGRITY_CATEGORY, f"live_integrity.{field_name} must be true.")
        if str(live_integrity.get("source", "") or "").strip() != "model_council":
            add("SOURCE_NOT_MODEL_COUNCIL", RUNTIME_INTEGRITY_CATEGORY, "Packet source must be model_council.")

    if not provenance:
        add("MISSING_PACKET_PROVENANCE", RUNTIME_INTEGRITY_CATEGORY, "provenance is required on PG_EXECUTION_PACKET_V3.")
    else:
        for field_name in (
            "frame_id",
            "capture_count",
            "state_version",
            "sequence_id",
            "source_lock_id",
            "model_health_id",
            "chart_transform_id",
            "created_epoch_ms",
            "valid_until_epoch_ms",
        ):
            if provenance.get(field_name) in (None, "", 0):
                add(
                    "INCOMPLETE_PACKET_PROVENANCE",
                    RUNTIME_INTEGRITY_CATEGORY,
                    f"provenance.{field_name} is required.",
                )
        for field_name in ("frame_id", "capture_count", "state_version"):
            if _int(provenance.get(field_name), -1) != _int(packet.get(field_name), -2):
                add(
                    "PROVENANCE_IDENTITY_MISMATCH",
                    RUNTIME_INTEGRITY_CATEGORY,
                    f"provenance.{field_name} must match packet.{field_name}.",
                )
        if _int(provenance.get("created_epoch_ms"), 0) <= 0 or _int(provenance.get("valid_until_epoch_ms"), 0) <= 0:
            add("PROVENANCE_TIME_INVALID", RUNTIME_INTEGRITY_CATEGORY, "provenance created/valid-until times must be epoch milliseconds.")

    execution = _mapping(packet.get("execution"))
    council = _mapping(packet.get("model_council"))
    allowance_package = _mapping(packet.get("allowance_package") or council.get("allowance_package"))
    health = _mapping(packet.get("runtime_model_health"))
    sequence_context = _mapping(council.get("sequence_context"))
    trade_permission = _mapping(packet.get("trade_permission") or council.get("trade_permission"))
    entry_quality = _mapping(packet.get("entry_quality") or council.get("entry_quality"))
    market_trap = _mapping(
        packet.get("market_trap")
        or packet.get("trap_assessment")
        or _mapping(packet.get("market_reality")).get("market_trap")
    )
    overlay_truth = _mapping(packet.get("overlay_truth_audit") or packet.get("overlay_geometry") or packet.get("overlay_context"))
    execution_side = normalize_side(execution.get("side"))
    final_side = normalize_side(council.get("final_side"))
    expiry = resolve_expiry_seconds(packet)
    raw_execution_side = _enum_text(execution.get("side"))
    raw_final_side = _enum_text(council.get("final_side"))
    raw_execution_state = _enum_text(execution.get("state"))
    raw_council_state = _enum_text(council.get("final_state"))

    if not execution:
        add("MISSING_EXECUTION", "SCHEMA", "execution object is required.")
    if not council:
        add("MISSING_MODEL_COUNCIL", "SCHEMA", "model_council object is required.")
    if allowance_package:
        allowance_schema = _clean_str(allowance_package.get("schema_version"))
        allowance_type = _enum_text(allowance_package.get("package_type"))
        allowance_authority = _clean_str(allowance_package.get("execution_authority"))
        if allowance_schema != ALLOWANCE_PACKAGE_SCHEMA_VERSION:
            add("INVALID_ALLOWANCE_PACKAGE_SCHEMA", MODEL_COUNCIL, "allowance_package.schema_version is invalid.")
        if allowance_type not in VALID_ALLOWANCE_PACKAGE_TYPES:
            add("INVALID_ALLOWANCE_PACKAGE_TYPE", MODEL_COUNCIL, "allowance_package.package_type must be SWING or INTRADAY_ENTER_NOW.")
        if allowance_authority and allowance_authority != EXECUTION_PACKET_SCHEMA_VERSION:
            add("INVALID_ALLOWANCE_EXECUTION_AUTHORITY", MODEL_COUNCIL, "allowance_package.execution_authority must be PG_EXECUTION_PACKET_V3.")
    if not sequence_context:
        add("MISSING_SEQUENCE_CONTEXT", MODEL_COUNCIL, "model_council.sequence_context is required.")
    else:
        try:
            resolved_sequence_context = resolve_sequence_context(packet)
        except ValueError as exc:
            reason = str(exc).strip().lower()
            if "ambiguous" in reason:
                add("AMBIGUOUS_SEQUENCE_CONTEXT", MODEL_COUNCIL, "sequence context must appear in one canonical location.")
            else:
                add("MISSING_SEQUENCE_CONTEXT", MODEL_COUNCIL, "model_council.sequence_context is required.")
            resolved_sequence_context = None
        if resolved_sequence_context is not None:
            sequence_context_fields = resolved_sequence_context.as_dict()
            for field_name in ("sequence_id", "sequence_signature", "sequence_status"):
                packet_value = packet.get(field_name)
                if _clean_str(packet_value) and _clean_str(packet_value) != _clean_str(sequence_context_fields.get(field_name)):
                    add("AMBIGUOUS_SEQUENCE_CONTEXT", MODEL_COUNCIL, f"{field_name} must match model_council.sequence_context.")
            for field_name in ("sequence_length", "frames_used"):
                packet_value = packet.get(field_name)
                if _int(packet_value, -1) >= 0 and _int(packet_value, -1) != _int(sequence_context_fields.get(field_name), -1):
                    add("AMBIGUOUS_SEQUENCE_CONTEXT", MODEL_COUNCIL, f"{field_name} must match model_council.sequence_context.")
            if _clean_str(sequence_context_fields.get("sequence_signature")) == "":
                add("MISSING_SEQUENCE_SIGNATURE", MODEL_COUNCIL, "sequence_signature is required in model_council.sequence_context.")
            if _clean_str(sequence_context_fields.get("sequence_status")) != "COMPLETE":
                add("PARTIAL_SEQUENCE_NOT_EXECUTABLE", MODEL_COUNCIL, "Only COMPLETE sequence context may execute.")
            if _int(sequence_context_fields.get("sequence_length"), 0) < 50:
                add("PARTIAL_SEQUENCE_NOT_EXECUTABLE", MODEL_COUNCIL, "Sequence length is below the live execution minimum.")
            if _float(sequence_context_fields.get("sequence_confidence"), 0.0) < SEQUENCE_CONTEXT_MIN_CONFIDENCE:
                add("SEQUENCE_CONFIDENCE_TOO_LOW", MODEL_COUNCIL, "sequence_confidence is below the live execution minimum.")
            if len(_sequence(sequence_context_fields.get("box_history"))) < SEQUENCE_CONTEXT_MIN_BOX_HISTORY_LEN:
                add("SEQUENCE_BOX_HISTORY_INSUFFICIENT", MODEL_COUNCIL, "box_history is required for executable sequence context.")
            if len(_sequence(sequence_context_fields.get("progression"))) < SEQUENCE_CONTEXT_MIN_PROGRESSION_LEN:
                add("SEQUENCE_PROGRESSION_INSUFFICIENT", MODEL_COUNCIL, "progression is required for executable sequence context.")
            if not _mapping(sequence_context_fields.get("entry_progression")):
                add("SEQUENCE_ENTRY_PROGRESSION_MISSING", MODEL_COUNCIL, "entry_progression is required for executable sequence context.")
            if provenance and _clean_str(provenance.get("sequence_id")) != _clean_str(sequence_context_fields.get("sequence_id")):
                add(
                    "PROVENANCE_SEQUENCE_MISMATCH",
                    MODEL_COUNCIL,
                    "provenance.sequence_id must match model_council.sequence_context.sequence_id.",
                )
    if not health:
        add("MISSING_RUNTIME_MODEL_HEALTH", RUNTIME_INTEGRITY_CATEGORY, "runtime_model_health object is required.")
    if raw_execution_side and raw_execution_side not in _enum_values(Side):
        add("INVALID_SIDE_ENUM", "EXECUTION", "execution.side must be BUY, SELL, or NONE.")
    if raw_final_side and raw_final_side not in _enum_values(Side):
        add("INVALID_MODEL_COUNCIL_FINAL_SIDE_ENUM", MODEL_COUNCIL, "model_council.final_side must be BUY, SELL, or NONE.")
    if raw_execution_state and raw_execution_state not in _enum_values(ExecutionState):
        add("INVALID_EXECUTION_STATE_ENUM", "EXECUTION", "execution.state is not a valid ExecutionState.")
    if raw_council_state and raw_council_state not in _enum_values(CouncilState):
        add("INVALID_COUNCIL_STATE_ENUM", MODEL_COUNCIL, "model_council.final_state is not a valid CouncilState.")
    instrument_state = _enum_text(
        instrument_context.get("instrument_context_state")
        or instrument_context.get("identity_state_v2")
    )
    if instrument_state and instrument_state not in _enum_values(InstrumentContextState):
        add(
            "INVALID_INSTRUMENT_CONTEXT_STATE_ENUM",
            "SCHEMA",
            "instrument_context.instrument_context_state is not a valid InstrumentContextState.",
        )
    timing_mode = _enum_text(
        execution.get("timing_mode")
        or _mapping(packet.get("timing")).get("timing_mode")
        or _mapping(packet.get("execution_timing")).get("timing_mode")
    )
    if timing_mode and timing_mode not in _enum_values(TimingMode):
        add("INVALID_TIMING_MODE_ENUM", "EXECUTION", "timing_mode is not a valid TimingMode.")
    shooter_state = _enum_text(packet.get("shooter_action_state") or _mapping(packet.get("shooter_action")).get("state"))
    if shooter_state and shooter_state not in _enum_values(ShooterActionState):
        add("INVALID_SHOOTER_ACTION_STATE_ENUM", "EXECUTION", "shooter_action_state is not a valid ShooterActionState.")
    calibration_state = _enum_text(packet.get("calibration_state") or _mapping(packet.get("calibration")).get("state"))
    if calibration_state and calibration_state not in _enum_values(CalibrationState):
        add("INVALID_CALIBRATION_STATE_ENUM", "SCHEMA", "calibration_state is not a valid CalibrationState.")
    if execution_side not in VALID_EXECUTION_SIDES and require_executable:
        add("INVALID_OR_MISSING_EXECUTION_SIDE", "EXECUTION", "execution.side must be BUY or SELL.")
    if final_side not in VALID_EXECUTION_SIDES and require_executable:
        add("MODEL_COUNCIL_FINAL_SIDE_REQUIRED", "MODEL_COUNCIL", "model_council.final_side must be BUY or SELL.")
    if execution_side and final_side and execution_side != final_side:
        add("EXECUTION_SIDE_MODEL_COUNCIL_MISMATCH", MODEL_COUNCIL, "execution.side must match model_council.final_side.")
    if expiry is None and require_executable:
        add("INVALID_OR_MISSING_EXPIRY_SECONDS", "EXECUTION", "execution.expiry_seconds and time_sequence target must match.")
    for source in (
        packet.get("expiry_source"),
        execution.get("expiry_source"),
        council.get("expiry_source"),
        _mapping(packet.get("timing")).get("expiry_source"),
        _mapping(packet.get("execution_timing")).get("expiry_source"),
    ):
        if _is_fallback_expiry_source(source):
            add("FALLBACK_EXPIRY_SOURCE", "EXECUTION", "Executable packets must not use fallback-derived expiry.")
            break
    if execution.get("amount_action") != "DO_NOT_CHANGE_AMOUNT":
        add("AMOUNT_ACTION_NOT_LOCKED", "EXECUTION", "execution.amount_action must be DO_NOT_CHANGE_AMOUNT.")
    time_sequence = _mapping(execution.get("time_sequence"))
    if require_executable and not time_sequence:
        add("MISSING_TIME_SEQUENCE", "EXECUTION", "execution.time_sequence is required.")
    elif require_executable:
        if parse_expiry_seconds(time_sequence.get("target_seconds")) is None:
            add(
                "MISSING_TIME_SEQUENCE_TARGET_SECONDS",
                "EXECUTION",
                "execution.time_sequence.target_seconds is required.",
            )
        if not _clean_str(time_sequence.get("target_text")):
            add(
                "MISSING_TIME_SEQUENCE_TARGET_TEXT",
                "EXECUTION",
                "execution.time_sequence.target_text is required.",
            )
        steps = time_sequence.get("steps")
        if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes, bytearray)) or not steps:
            add("MISSING_TIME_SEQUENCE_STEPS", "EXECUTION", "execution.time_sequence.steps is required.")

    execution_enabled = execution.get("enabled") is True
    execution_state = str(execution.get("state", "") or "").upper()
    council_state = str(council.get("final_state", "") or "").upper()
    if require_executable:
        if not execution_enabled:
            add("EXECUTION_NOT_ENABLED", "EXECUTION", "execution.enabled must be true.")
        if execution_state != EXECUTABLE_STATE:
            add("EXECUTION_STATE_NOT_EXECUTABLE", "EXECUTION", "execution.state must be EXECUTABLE.")
        if council_state != EXECUTABLE_STATE:
            add("COUNCIL_STATE_NOT_EXECUTABLE", "MODEL_COUNCIL", "model_council.final_state must be EXECUTABLE.")
    if health.get("all_required_models_awake") is not True:
        add("REQUIRED_MODELS_NOT_AWAKE", RUNTIME_INTEGRITY_CATEGORY, "All required runtime models must be awake.")
    if require_executable and trade_permission and trade_permission.get("executable_allowed") is not True:
        add(
            "TRADE_PERMISSION_DENIED",
            MODEL_COUNCIL,
            "trade_permission.executable_allowed must be true for executable packets.",
        )
    if require_executable and entry_quality and not _entry_quality_allows_execution(entry_quality):
        add(
            "ENTRY_QUALITY_BELOW_ACCEPTABLE",
            MODEL_COUNCIL,
            "entry_quality must be ACCEPTABLE_ENTRY or better for executable packets.",
        )
    if require_executable and _market_trap_blocks_execution(market_trap):
        add(
            "MARKET_TRAP_EXECUTION_DENIED",
            MODEL_COUNCIL,
            "Active market trap assessment prevents executable packets.",
        )
    if require_executable and _overlay_truth_blocks_execution(overlay_truth):
        add(
            "OVERLAY_TRUTH_NOT_EXECUTION_SAFE",
            MODEL_COUNCIL,
            "Overlay truth audit is not execution safe.",
        )

    executable = not issues and execution_enabled and execution_state == EXECUTABLE_STATE and council_state == EXECUTABLE_STATE
    return PacketValidationResult(
        ok=not issues,
        executable=executable,
        issues=tuple(issues),
        side=execution_side,
        expiry_seconds=expiry,
        packet_id=packet_id or None,
    )


def raw_signal_rejection_reason(payload: Mapping[str, Any]) -> str | None:
    if payload.get("schema_version") == EXECUTION_PACKET_SCHEMA_VERSION:
        return None
    for key in ("execution_action", "action", "side", "entry_state", "decision_kernel"):
        if key in payload:
            return "RAW_SIGNAL_NOT_EXECUTABLE_PACKET"
    return "OLD_OR_MISSING_SCHEMA_VERSION"
