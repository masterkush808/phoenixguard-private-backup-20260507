from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Any, Mapping

from phoenixguard.execution.packet_v3 import (
    EXECUTION_PACKET_SCHEMA_VERSION,
    PG_CACHE_SCHEMA_VERSION,
    STUDY_PACKET_SCHEMA_VERSION,
    STUDY_PACKET_TYPE,
    validate_execution_packet_v3,
)


CACHE_SCHEMA_VERSION = PG_CACHE_SCHEMA_VERSION
DEFAULT_CACHE_TTL_SEC = 8.0
DEFAULT_STUDY_PACKET_TTL_SEC = 8.0
MAX_REASONABLE_EPOCH_SECONDS = 10_000_000_000.0

REQUIRED_CACHE_FIELDS: tuple[str, ...] = (
    "schema_version",
    "cache_schema_version",
    "created_epoch_sec",
    "valid_until_epoch_sec",
    "session_id",
    "symbol",
    "timeframe",
    "frame_id",
    "capture_count",
    "state_version",
    "input_frame_hash",
    "viewport_hash",
    "model_version_hash",
    "preprocess_version_hash",
    "decision_schema_version",
    "calibration_profile_id",
)


class CacheValidationError(ValueError):
    """Raised when a cache object cannot be trusted for V3 runtime use."""


@dataclass(frozen=True)
class CacheValidationResult:
    ok: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return float(number)


def _epoch_seconds(payload: Mapping[str, Any], standard_key: str, alias_key: str) -> float:
    standard = _number(payload.get(standard_key), 0.0)
    if standard > 0.0:
        return standard
    return _number(payload.get(alias_key), 0.0)


def _expiry_seconds(payload: Mapping[str, Any], default: float = 0.0) -> float:
    expiry = _number(payload.get("expiry_seconds"), 0.0)
    if expiry > 0.0:
        return expiry
    ttl = _number(payload.get("ttl_sec") or payload.get("time_to_live_sec") or payload.get("valid_for_seconds"), 0.0)
    return ttl if ttl > 0.0 else float(default)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _nested_mapping(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _reason_from_packet_issue_code(code: str) -> str:
    normalized = str(code or "").strip().lower()
    return {
        "invalid_schema_version": "old_or_missing_execution_packet_schema",
        "raw_signal_not_execution_packet": "old_or_missing_execution_packet_schema",
        "packet_expired": "packet_expired",
        "not_live": "live_integrity_not_live",
        "cache_not_fresh": "cache_status_not_fresh",
        "source_not_model_council": "source_not_model_council",
        "frame_not_advancing": "frame_not_advancing",
        "capture_not_advancing": "capture_not_advancing",
        "state_not_advancing": "state_not_advancing",
        "execution_not_enabled": "execution_disabled",
        "execution_state_not_executable": "execution_state_not_executable",
        "council_state_not_executable": "council_state_not_executable",
        "invalid_or_missing_execution_side": "execution_side_invalid",
        "model_council_final_side_required": "model_council_final_side_missing",
        "execution_side_model_council_mismatch": "execution_side_model_council_mismatch",
        "required_models_not_awake": "required_models_not_awake",
        "missing_time_sequence": "missing_time_sequence",
        "missing_time_sequence_steps": "missing_time_sequence_steps",
        "amount_action_not_locked": "amount_action_not_locked",
        "invalid_or_missing_expiry_seconds": "invalid_or_missing_expiry_seconds",
        "missing_input_frame_hash": "missing_input_frame_hash",
    }.get(normalized, normalized or "packet_validation_failed")


def _append_unique(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _add_context_mismatch(
    reasons: list[str],
    record: Mapping[str, Any],
    expected: Mapping[str, Any],
    field_name: str,
) -> None:
    expected_value = expected.get(field_name)
    if expected_value is None:
        return
    actual_value = record.get(field_name)
    if _text(actual_value) != _text(expected_value):
        reasons.append(
            f"{field_name}_mismatch:{_text(actual_value) or '<missing>'}!={_text(expected_value)}"
        )


def validate_cache_record(
    record: Mapping[str, Any] | None,
    *,
    expected_context: Mapping[str, Any] | None = None,
    previous_record: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
) -> CacheValidationResult:
    """Validate a V3 cache object before any live runtime component trusts it."""

    if not _is_mapping(record):
        return CacheValidationResult(False, ("cache_record_not_mapping",))

    payload = dict(record or {})
    reasons: list[str] = []
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        reasons.append("missing_or_invalid_cache_entry_schema_version")
    if payload.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        reasons.append("old_or_missing_cache_schema")

    missing_fields = [field_name for field_name in REQUIRED_CACHE_FIELDS if field_name not in payload]
    if missing_fields:
        reasons.append("missing_cache_fields:" + ",".join(missing_fields))

    if _text(payload.get("decision_schema_version")) != EXECUTION_PACKET_SCHEMA_VERSION:
        reasons.append("decision_schema_version_mismatch")

    if "created_epoch_sec" not in payload:
        reasons.append("created_epoch_sec_missing_or_invalid")
    created_epoch = _epoch_seconds(payload, "created_epoch_sec", "created_epoch")
    if created_epoch <= 0.0:
        _append_unique(reasons, "created_epoch_sec_missing_or_invalid")
    elif created_epoch > MAX_REASONABLE_EPOCH_SECONDS:
        reasons.append("timestamp_unit_not_seconds")

    check_epoch = float(time.time()) if now_epoch is None else float(now_epoch)
    if "valid_until_epoch_sec" not in payload:
        reasons.append("valid_until_epoch_sec_missing_or_invalid")
    valid_until_epoch = _epoch_seconds(payload, "valid_until_epoch_sec", "valid_until_epoch")
    if valid_until_epoch <= 0.0:
        _append_unique(reasons, "valid_until_epoch_sec_missing_or_invalid")
    elif valid_until_epoch > MAX_REASONABLE_EPOCH_SECONDS:
        reasons.append("timestamp_unit_not_seconds")
    elif valid_until_epoch <= check_epoch:
        reasons.append("cache_entry_expired")
    ttl_seconds = _expiry_seconds(payload, 0.0)
    if created_epoch > 0.0 and ttl_seconds > 0.0 and created_epoch + ttl_seconds <= check_epoch:
        _append_unique(reasons, "cache_entry_expired")

    expected = dict(expected_context or {})
    for field_name in (
        "session_id",
        "symbol",
        "timeframe",
        "input_frame_hash",
        "viewport_hash",
        "model_version_hash",
        "preprocess_version_hash",
        "decision_schema_version",
        "calibration_profile_id",
    ):
        _add_context_mismatch(reasons, payload, expected, field_name)

    for field_name in ("frame_id", "capture_count", "state_version"):
        if field_name in expected:
            expected_value = _integer(expected.get(field_name), -1)
            actual_value = _integer(payload.get(field_name), -1)
            if actual_value != expected_value:
                reasons.append(f"{field_name}_mismatch:{actual_value}!={expected_value}")

    if previous_record is not None:
        previous = dict(previous_record or {})
        for field_name in ("frame_id", "capture_count", "state_version"):
            current_value = _integer(payload.get(field_name), 0)
            previous_value = _integer(previous.get(field_name), 0)
            if current_value <= previous_value:
                reasons.append(f"{field_name}_not_advancing:{current_value}<={previous_value}")

    return CacheValidationResult(not reasons, tuple(reasons))


def require_valid_cache_record(
    record: Mapping[str, Any] | None,
    *,
    expected_context: Mapping[str, Any] | None = None,
    previous_record: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    result = validate_cache_record(
        record,
        expected_context=expected_context,
        previous_record=previous_record,
        now_epoch=now_epoch,
    )
    if not result.ok:
        raise CacheValidationError(result.reason)
    return dict(record or {})


def attach_cache_v3_metadata(
    payload: Mapping[str, Any],
    *,
    session_id: str,
    symbol: str,
    timeframe: str,
    frame_id: int,
    capture_count: int,
    state_version: int,
    input_frame_hash: str,
    viewport_hash: str,
    model_version_hash: str,
    preprocess_version_hash: str,
    calibration_profile_id: str,
    created_epoch: float | None = None,
    valid_until_epoch: float | None = None,
    created_epoch_sec: float | None = None,
    valid_until_epoch_sec: float | None = None,
    ttl_seconds: float = DEFAULT_CACHE_TTL_SEC,
) -> dict[str, Any]:
    enriched = dict(payload)
    created = (
        float(time.time())
        if created_epoch_sec is None and created_epoch is None
        else float(created_epoch_sec if created_epoch_sec is not None else created_epoch)
    )
    valid_until = (
        float(valid_until_epoch_sec if valid_until_epoch_sec is not None else valid_until_epoch)
        if valid_until_epoch_sec is not None or valid_until_epoch is not None
        else created + max(0.1, float(ttl_seconds))
    )
    enriched.update(
        {
            "schema_version": CACHE_SCHEMA_VERSION,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "created_epoch_sec": created,
            "valid_until_epoch_sec": valid_until,
            "created_epoch": created,
            "valid_until_epoch": valid_until,
            "session_id": str(session_id),
            "symbol": str(symbol),
            "timeframe": str(timeframe),
            "frame_id": int(frame_id),
            "capture_count": int(capture_count),
            "state_version": int(state_version),
            "input_frame_hash": str(input_frame_hash),
            "viewport_hash": str(viewport_hash),
            "model_version_hash": str(model_version_hash),
            "preprocess_version_hash": str(preprocess_version_hash),
            "decision_schema_version": EXECUTION_PACKET_SCHEMA_VERSION,
            "calibration_profile_id": str(calibration_profile_id),
        }
    )
    return enriched


def validate_study_packet_for_current_state(
    packet: Mapping[str, Any] | None,
    *,
    now_epoch: float | None = None,
    ttl_seconds: float = DEFAULT_STUDY_PACKET_TTL_SEC,
) -> CacheValidationResult:
    if not _is_mapping(packet):
        return CacheValidationResult(False, ("study_packet_not_mapping",))

    payload = dict(packet or {})
    reasons: list[str] = []
    if payload.get("schema_version") != STUDY_PACKET_SCHEMA_VERSION:
        reasons.append("not_study_packet_schema")
    if _upper(payload.get("packet_type")) != STUDY_PACKET_TYPE:
        reasons.append("not_study_packet_type")

    created_epoch = _epoch_seconds(payload, "created_epoch_sec", "created_epoch")
    valid_until_epoch = _epoch_seconds(payload, "valid_until_epoch_sec", "valid_until_epoch")
    if created_epoch <= 0.0:
        reasons.append("created_epoch_sec_missing_or_invalid")
    elif created_epoch > MAX_REASONABLE_EPOCH_SECONDS:
        reasons.append("timestamp_unit_not_seconds")

    check_epoch = float(time.time()) if now_epoch is None else float(now_epoch)
    effective_valid_until = valid_until_epoch
    if effective_valid_until <= 0.0 and created_epoch > 0.0:
        effective_valid_until = created_epoch + max(0.1, float(ttl_seconds))
    if effective_valid_until <= 0.0:
        reasons.append("valid_until_epoch_sec_missing_or_invalid")
    elif effective_valid_until > MAX_REASONABLE_EPOCH_SECONDS:
        reasons.append("timestamp_unit_not_seconds")
    elif effective_valid_until <= check_epoch:
        reasons.append("study_packet_expired")

    return CacheValidationResult(not reasons, tuple(reasons))


def validate_execution_packet_for_live_execution(
    packet: Mapping[str, Any] | None,
    *,
    now_epoch: float | None = None,
) -> CacheValidationResult:
    """Validate only packet-level live-execution freshness and runtime authority."""

    if not _is_mapping(packet):
        return CacheValidationResult(False, ("packet_not_mapping",))

    payload = dict(packet or {})
    reasons: list[str] = []
    if payload.get("schema_version") != EXECUTION_PACKET_SCHEMA_VERSION:
        reasons.append("old_or_missing_execution_packet_schema")

    check_epoch = float(time.time()) if now_epoch is None else float(now_epoch)
    valid_until_epoch = _epoch_seconds(payload, "valid_until_epoch_sec", "valid_until_epoch")
    valid_until_alias = _number(payload.get("valid_until_epoch"), 0.0)
    if valid_until_epoch <= 0.0 or valid_until_epoch <= check_epoch or (valid_until_alias > 0.0 and valid_until_alias <= check_epoch):
        reasons.append("packet_expired")

    live_integrity = _nested_mapping(payload, "live_integrity")
    if live_integrity.get("is_live") is not True:
        reasons.append("live_integrity_not_live")
    if _text(live_integrity.get("cache_status")).lower() != "fresh":
        reasons.append("cache_status_not_fresh")

    execution = _nested_mapping(payload, "execution")
    side = _upper(execution.get("side"))
    if execution.get("enabled") is not True:
        reasons.append("execution_disabled")
    if _upper(execution.get("state")) != "EXECUTABLE":
        reasons.append("execution_state_not_executable")
    if side not in {"BUY", "SELL"}:
        reasons.append("execution_side_invalid")

    model_council = _nested_mapping(payload, "model_council")
    final_side = _upper(model_council.get("final_side"))
    if final_side not in {"BUY", "SELL"}:
        reasons.append("model_council_final_side_missing")
    elif side != final_side:
        reasons.append("execution_side_model_council_mismatch")

    runtime_health = _nested_mapping(payload, "runtime_model_health")
    if runtime_health.get("all_required_models_awake") is not True:
        reasons.append("required_models_not_awake")

    authoritative = validate_execution_packet_v3(
        payload,
        now_epoch=check_epoch,
        require_executable=True,
    )
    if not authoritative.ok:
        for code in authoritative.reason_codes:
            _append_unique(reasons, _reason_from_packet_issue_code(code))

    return CacheValidationResult(not reasons, tuple(reasons))


def packet_can_execute(packet: Mapping[str, Any] | None, *, now_epoch: float | None = None) -> bool:
    return validate_execution_packet_for_live_execution(packet, now_epoch=now_epoch).ok
