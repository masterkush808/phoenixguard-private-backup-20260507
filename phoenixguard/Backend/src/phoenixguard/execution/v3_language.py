from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Mapping, Sequence, cast

from phoenixguard.core.timing_policy_v3 import (
    MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS,
)


LANGUAGE_CONSTITUTION_VERSION = "PG_V3_LANGUAGE_CONSTITUTION_2026_05_25"
EXECUTION_PACKET_SCHEMA_VERSION = "PG_EXECUTION_PACKET_V3"
STUDY_PACKET_SCHEMA_VERSION = "PG_MODEL_COUNCIL_STUDY_V3"
STUDY_PACKET_TYPE = "STUDY_PACKET"
EXECUTION_PACKET_TYPE = "PG_EXECUTION_PACKET_V3"
VALID_EXECUTION_SIDES = {"BUY", "SELL"}
VALID_PACKET_TYPES = {STUDY_PACKET_TYPE, EXECUTION_PACKET_TYPE}
CANONICAL_EPOCH_FIELDS = {"created_epoch", "valid_until_epoch", "created_epoch_sec", "valid_until_epoch_sec"}


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class PacketType(str, Enum):
    STUDY_PACKET = STUDY_PACKET_TYPE
    PG_EXECUTION_PACKET_V3 = EXECUTION_PACKET_TYPE


class CouncilState(str, Enum):
    WATCHING = "WATCHING"
    PREPARING = "PREPARING"
    EXECUTABLE = "EXECUTABLE"
    BLOCKED_BY_RUNTIME = "BLOCKED_BY_RUNTIME"
    CONFLICT = "CONFLICT"
    COOLDOWN = "COOLDOWN"


class ExecutionState(str, Enum):
    WATCHING = "WATCHING"
    PREPARING = "PREPARING"
    EXECUTABLE = "EXECUTABLE"
    BLOCKED_BY_RUNTIME = "BLOCKED_BY_RUNTIME"


class TimingMode(str, Enum):
    ENTER_NOW = "ENTER_NOW"
    WAIT_FOR_PULLBACK = "WAIT_FOR_PULLBACK"
    WAIT_FOR_RETEST = "WAIT_FOR_RETEST"
    WAIT_FOR_BREAK_CONFIRMATION = "WAIT_FOR_BREAK_CONFIRMATION"
    WAIT_FOR_CANDLE_CLOSE_BEHAVIOUR = "WAIT_FOR_CANDLE_CLOSE_BEHAVIOUR"
    SKIP_LATE_ENTRY = "SKIP_LATE_ENTRY"


class InstrumentContextState(str, Enum):
    UNKNOWN = "UNKNOWN"
    VISUAL_CONTEXT_LOCKED = "VISUAL_CONTEXT_LOCKED"
    USER_PROFILE_LOCKED = "USER_PROFILE_LOCKED"
    BROKER_SURFACE_LOCKED = "BROKER_SURFACE_LOCKED"
    BROKER_CLICK_SAFE = "BROKER_CLICK_SAFE"
    INVALIDATED = "INVALIDATED"


class ShooterActionState(str, Enum):
    WAITING = "WAITING"
    READY = "READY"
    SETTING_TIME = "SETTING_TIME"
    ACTION = "ACTION"
    ACTION_SENT = "ACTION_SENT"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


class CalibrationState(str, Enum):
    PENDING = "PENDING"
    NOT_CHECKED = "NOT_CHECKED"
    PASS_ = "PASS"
    FAIL = "FAIL"
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True)
class LanguageIssue:
    code: str
    field: str
    message: str
    severity: str = "ERROR"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class LanguageValidationResult:
    ok: bool
    issues: tuple[LanguageIssue, ...] = field(default_factory=tuple)
    packet_type: str = ""
    packet_id: str = ""

    @property
    def rejected(self) -> bool:
        return not self.ok

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    @property
    def first_reason(self) -> str:
        return self.reason_codes[0] if self.reason_codes else "OK"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "packet_type": self.packet_type,
            "packet_id": self.packet_id,
            "first_reason": self.first_reason,
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class TimingDecision:
    entry_now_allowed: bool
    timing_mode: str
    path_class: str = ""
    selected_expiry: int | None = None
    expiry_band: str = ""
    time_to_reward_sec: float | None = None
    time_to_invalidation_sec: float | None = None
    drawdown_first_risk: str = ""
    reason: str = ""
    next_required: str = ""


@dataclass(frozen=True)
class TradeCandidate:
    candidate_id: str
    candidate_side: str
    candidate_stage: str
    stable_reads: int = 0
    final_score: float | None = None
    threshold: float | None = None
    denied_at: str = ""
    next_required: str = ""

    def __post_init__(self) -> None:
        if self.candidate_side and self.candidate_side not in VALID_EXECUTION_SIDES:
            raise ValueError("candidate_side must be BUY or SELL when present")


@dataclass(frozen=True)
class ExecutionLaneResult:
    name: str
    accepted: bool
    side: str
    reason: str = ""
    required_score: float | None = None
    actual_score: float | None = None
    denied_at: str = ""
    next_required: str = ""

    def __post_init__(self) -> None:
        if self.side and self.side not in VALID_EXECUTION_SIDES:
            raise ValueError("ExecutionLaneResult.side must be BUY or SELL when present")


@dataclass(frozen=True)
class ShooterPackageReportContract:
    schema_version: str = "PG_SHOOTER_PACKAGE_REPORT_V1"
    allowed_package_types: tuple[str, ...] = ("INTRADAY_ENTER_NOW", "SWING")
    execution_removed: bool = True
    broker_click_allowed: bool = False


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(cast(Sequence[Any], value))


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"none", "null", "n/a"} else text


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def normalize_canonical_side(value: Any) -> str | None:
    text = _text(value).upper()
    if text in VALID_EXECUTION_SIDES:
        return text
    return None


def normalize_enum_value(enum_cls: type[Enum], value: Any) -> str | None:
    text = _text(value).upper()
    for member in enum_cls:
        if str(member.value).upper() == text:
            return str(member.value)
    return None


def validate_enum_value(enum_cls: type[Enum], value: Any, field: str) -> LanguageIssue | None:
    if normalize_enum_value(enum_cls, value) is None:
        valid = ", ".join(str(member.value) for member in enum_cls)
        return LanguageIssue(
            code=f"INVALID_{field.upper().replace('.', '_')}",
            field=field,
            message=f"{field} must be one of: {valid}.",
        )
    return None


def packet_type_of(packet: Mapping[str, Any]) -> str:
    packet_type = _text(packet.get("packet_type")).upper()
    schema_version = _text(packet.get("schema_version")).upper()
    if packet_type in VALID_PACKET_TYPES:
        return packet_type
    if schema_version == EXECUTION_PACKET_SCHEMA_VERSION:
        return EXECUTION_PACKET_TYPE
    if schema_version == STUDY_PACKET_SCHEMA_VERSION:
        return STUDY_PACKET_TYPE
    return packet_type or schema_version


def packet_id_of(packet: Mapping[str, Any]) -> str:
    return _text(packet.get("packet_id"))


def created_epoch_sec(packet: Mapping[str, Any]) -> float:
    return _float(packet.get("created_epoch_sec") or packet.get("created_epoch"), 0.0)


def valid_until_epoch_sec(packet: Mapping[str, Any]) -> float:
    return _float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"), 0.0)


def packet_age_ms(packet: Mapping[str, Any], *, now_epoch: float | None = None) -> int:
    created = created_epoch_sec(packet)
    if created <= 0.0:
        return 0
    now_value = time.time() if now_epoch is None else float(now_epoch)
    return max(0, int(round((now_value - created) * 1000.0)))


def is_packet_current(packet: Mapping[str, Any], *, now_epoch: float | None = None) -> bool:
    valid_until = valid_until_epoch_sec(packet)
    if valid_until <= 0.0:
        return False
    now_value = time.time() if now_epoch is None else float(now_epoch)
    return valid_until > now_value


def validate_cache_entry_language(entry: Mapping[str, Any] | None, *, now_epoch: float | None = None) -> LanguageValidationResult:
    issues: list[LanguageIssue] = []

    def add(code: str, field: str, message: str) -> None:
        issues.append(LanguageIssue(code=code, field=field, message=message))

    if not isinstance(entry, Mapping):
        add("MISSING_CACHE_ENTRY", "$", "Cache entry must be a mapping.")
        return LanguageValidationResult(False, tuple(issues))

    schema = _text(entry.get("schema_version"))
    if not schema:
        add("MISSING_CACHE_SCHEMA_VERSION", "schema_version", "Cache entries require schema_version.")
    created = created_epoch_sec(entry)
    valid_until = valid_until_epoch_sec(entry)
    if created <= 0.0:
        add("MISSING_CACHE_CREATED_EPOCH_SEC", "created_epoch_sec", "Cache entries require created_epoch_sec epoch seconds.")
    if valid_until <= 0.0:
        add("MISSING_CACHE_VALID_UNTIL_EPOCH_SEC", "valid_until_epoch_sec", "Cache entries require valid_until_epoch_sec epoch seconds.")
    elif now_epoch is not None and valid_until <= float(now_epoch):
        add("CACHE_ENTRY_EXPIRED", "valid_until_epoch_sec", "Expired cache entries cannot override live state.")
    return LanguageValidationResult(not issues, tuple(issues), packet_type=_text(entry.get("packet_type")), packet_id=packet_id_of(entry))


def validate_study_packet_language(packet: Mapping[str, Any] | None, *, now_epoch: float | None = None) -> LanguageValidationResult:
    issues: list[LanguageIssue] = []

    def add(code: str, field: str, message: str) -> None:
        issues.append(LanguageIssue(code=code, field=field, message=message))

    if not isinstance(packet, Mapping):
        add("MISSING_PACKET", "$", "Study packet must be a mapping.")
        return LanguageValidationResult(False, tuple(issues))

    packet_type = packet_type_of(packet)
    packet_id = packet_id_of(packet)
    enum_issue = validate_enum_value(PacketType, packet_type, "packet_type")
    if enum_issue:
        issues.append(enum_issue)
    if packet_type != STUDY_PACKET_TYPE:
        add("NOT_STUDY_PACKET", "packet_type", "Study packet must declare STUDY_PACKET.")
    if not packet_id:
        add("MISSING_PACKET_ID", "packet_id", "Published study packets require a non-empty packet_id.")

    execution = _mapping(packet.get("execution"))
    execution_state = _text(execution.get("state")).upper()
    if execution_state:
        enum_issue = validate_enum_value(ExecutionState, execution_state, "execution.state")
        if enum_issue:
            issues.append(enum_issue)
    execution_side = _text(execution.get("side")).upper()
    if execution_side:
        enum_issue = validate_enum_value(Side, execution_side, "execution.side")
        if enum_issue:
            issues.append(enum_issue)
    if execution.get("enabled") is True:
        add("STUDY_PACKET_EXECUTION_ENABLED", "execution.enabled", "STUDY_PACKET can never enable execution.")
    if execution_state == "EXECUTABLE":
        add("STUDY_PACKET_EXECUTABLE_STATE", "execution.state", "STUDY_PACKET cannot advertise EXECUTABLE execution state.")
    if _mapping(execution.get("time_sequence")):
        add("STUDY_PACKET_HAS_ACTION_TIME_SEQUENCE", "execution.time_sequence", "Study packets may display timing context but must not carry action time_sequence.")

    council = _mapping(packet.get("model_council"))
    promotion = _mapping(packet.get("promotion_trace"))
    if not council:
        add("MISSING_MODEL_COUNCIL", "model_council", "Study packet must include model_council state.")
    if not promotion:
        add("MISSING_PROMOTION_TRACE", "promotion_trace", "Study packet must include promotion_trace.")
    for field_name in ("blocked_by", "denied_at", "next_required"):
        if not _text(promotion.get(field_name) or council.get(field_name)):
            add(f"MISSING_{field_name.upper()}", field_name, f"Non-executable study state must explain {field_name}.")

    created = created_epoch_sec(packet)
    valid_until = valid_until_epoch_sec(packet)
    if created <= 0.0:
        add("MISSING_CREATED_EPOCH_SEC", "created_epoch", "Study packet requires created_epoch epoch seconds.")
    if valid_until <= 0.0:
        add("MISSING_VALID_UNTIL_EPOCH_SEC", "valid_until_epoch", "Study packet requires valid_until_epoch epoch seconds.")
    elif now_epoch is not None and valid_until <= float(now_epoch):
        add("STUDY_PACKET_EXPIRED", "valid_until_epoch", "Expired study packets are stale display state.")

    return LanguageValidationResult(not issues, tuple(issues), packet_type=packet_type, packet_id=packet_id)


def validate_execution_packet_language(packet: Mapping[str, Any] | None, *, now_epoch: float | None = None) -> LanguageValidationResult:
    issues: list[LanguageIssue] = []

    def add(code: str, field: str, message: str) -> None:
        issues.append(LanguageIssue(code=code, field=field, message=message))

    if not isinstance(packet, Mapping):
        add("MISSING_PACKET", "$", "Execution packet must be a mapping.")
        return LanguageValidationResult(False, tuple(issues))

    packet_type = packet_type_of(packet)
    packet_id = packet_id_of(packet)
    enum_issue = validate_enum_value(PacketType, packet_type, "packet_type")
    if enum_issue:
        issues.append(enum_issue)
    if packet.get("schema_version") != EXECUTION_PACKET_SCHEMA_VERSION:
        add("UNKNOWN_PACKET_SCHEMA_REJECTED", "schema_version", "Only PG_EXECUTION_PACKET_V3 can authorise execution.")
    if packet_type != EXECUTION_PACKET_TYPE:
        add("NOT_EXECUTION_PACKET_TYPE", "packet_type", "Executable authority must declare PG_EXECUTION_PACKET_V3.")
    if not packet_id:
        add("MISSING_PACKET_ID", "packet_id", "PG_EXECUTION_PACKET_V3 requires a non-empty packet_id.")

    execution = _mapping(packet.get("execution"))
    council = _mapping(packet.get("model_council"))
    execution_state = _text(execution.get("state")).upper()
    if execution_state:
        enum_issue = validate_enum_value(ExecutionState, execution_state, "execution.state")
        if enum_issue:
            issues.append(enum_issue)
    council_state = _text(council.get("final_state")).upper()
    if council_state:
        enum_issue = validate_enum_value(CouncilState, council_state, "model_council.final_state")
        if enum_issue:
            issues.append(enum_issue)
    instrument_context = _mapping(packet.get("instrument_context"))
    instrument_state = _text(instrument_context.get("instrument_context_state") or instrument_context.get("identity_state_v2")).upper()
    if instrument_state:
        enum_issue = validate_enum_value(InstrumentContextState, instrument_state, "instrument_context.instrument_context_state")
        if enum_issue:
            issues.append(enum_issue)
    execution_side = normalize_canonical_side(execution.get("side"))
    final_side = normalize_canonical_side(council.get("final_side"))
    if execution_side is None:
        add("MISSING_EXECUTION_SIDE", "execution.side", "Only execution.side can drive shooter action and it must be BUY or SELL.")
    if final_side is None:
        add("MISSING_FINAL_SIDE", "model_council.final_side", "Model Council final_side must be BUY or SELL.")
    if execution_side and final_side and execution_side != final_side:
        add("FINAL_SIDE_MUST_EQUAL_EXECUTION_SIDE", "model_council.final_side", "final_side must equal execution.side.")

    for alias in ("raw_side", "side", "action", "execution_action", "candidate_action"):
        if alias in packet and normalize_canonical_side(packet.get(alias)) and not execution_side:
            add("RAW_SIDE_ALIAS_CANNOT_EXECUTE", alias, f"{alias} is observation/debug only and cannot replace execution.side.")

    expiry_seconds = _float(execution.get("expiry_seconds"), 0.0)
    if expiry_seconds <= 0.0:
        add("MISSING_EXPIRY_SECONDS", "execution.expiry_seconds", "Executable packet requires positive expiry_seconds.")
    elif expiry_seconds < MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS:
        add(
            "EXPIRY_BELOW_MINIMUM_STUDIED_DURATION",
            "execution.expiry_seconds",
            (
                "Executable V3 OTC packets require at least 900 seconds. "
                "Moves under 15 minutes are excluded from timing consideration."
            ),
        )
    time_sequence = _mapping(execution.get("time_sequence"))
    target_seconds = _float(time_sequence.get("target_seconds"), 0.0)
    target_text = _text(time_sequence.get("target_text"))
    if not time_sequence:
        add("MISSING_TIME_SEQUENCE", "execution.time_sequence", "Executable packet requires time_sequence.")
    if target_seconds <= 0.0:
        add("MISSING_TIME_SEQUENCE_TARGET_SECONDS", "execution.time_sequence.target_seconds", "time_sequence.target_seconds is required.")
    if not target_text:
        add("MISSING_TIME_SEQUENCE_TARGET_TEXT", "execution.time_sequence.target_text", "time_sequence.target_text is required.")
    if expiry_seconds > 0.0 and target_seconds > 0.0 and int(expiry_seconds) != int(target_seconds):
        add("TIME_SEQUENCE_EXPIRY_MISMATCH", "execution.time_sequence.target_seconds", "time_sequence target must match expiry_seconds.")
    if not _sequence(time_sequence.get("steps")):
        add("MISSING_TIME_SEQUENCE_STEPS", "execution.time_sequence.steps", "time_sequence.steps is required.")

    if execution.get("enabled") is not True:
        add("EXECUTION_NOT_ENABLED", "execution.enabled", "Executable packet must set execution.enabled=true.")
    if _text(execution.get("state")).upper() != "EXECUTABLE":
        add("EXECUTION_STATE_NOT_EXECUTABLE", "execution.state", "Executable packet must set execution.state=EXECUTABLE.")
    if _text(council.get("final_state")).upper() != "EXECUTABLE":
        add("COUNCIL_STATE_NOT_EXECUTABLE", "model_council.final_state", "Model Council final_state must be EXECUTABLE.")
    if execution.get("amount_action") != "DO_NOT_CHANGE_AMOUNT":
        add("AMOUNT_ACTION_NOT_LOCKED", "execution.amount_action", "Amount controls must remain unreachable.")

    valid_until = valid_until_epoch_sec(packet)
    created = created_epoch_sec(packet)
    if created <= 0.0:
        add("MISSING_CREATED_EPOCH_SEC", "created_epoch", "created_epoch must be epoch seconds.")
    if valid_until <= 0.0:
        add("MISSING_VALID_UNTIL_EPOCH_SEC", "valid_until_epoch", "valid_until_epoch must be epoch seconds.")
    elif now_epoch is not None and valid_until <= float(now_epoch):
        add("EXECUTION_PACKET_EXPIRED", "valid_until_epoch", "Expired packets cannot execute.")

    return LanguageValidationResult(not issues, tuple(issues), packet_type=packet_type, packet_id=packet_id)


def assert_language_ok(result: LanguageValidationResult) -> None:
    if result.rejected:
        details = ", ".join(result.reason_codes)
        raise ValueError(f"V3 language validation failed: {details}")


def public_language_scorecard() -> dict[str, Any]:
    return {
        "version": LANGUAGE_CONSTITUTION_VERSION,
        "active_packet_contracts": [STUDY_PACKET_TYPE, EXECUTION_PACKET_TYPE],
        "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3 strategy authority with PG_EXECUTION_PACKET_V3 packet validation",
        "model_council_role": "MODEL_COUNCIL_CONTRIBUTOR_GATE_V3",
        "action_authority": "MT4 bridge or external executor only; shooter reports allowed packages",
        "operator_truth": "FloatingStateV2 reducer only",
        "side_hierarchy": ["raw_side", "candidate_side", "final_side", "execution.side"],
        "required_runtime_trace_nodes": [
            "tracker_latest",
            "model_council_latest",
            "study_latest",
            "execution_latest",
            "floating_state",
            "shooter_handshake",
            "model_health",
            "package_reporter_status",
            "cache_status",
        ],
    }
