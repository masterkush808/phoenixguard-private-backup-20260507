from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import time
from typing import Any, Iterable, Mapping


ENTER_NOW_MODE = "ENTER_NOW"
EXECUTION_PACKET_TYPE = "PG_EXECUTION_PACKET_V3"
STUDY_PACKET_TYPE = "STUDY_PACKET"

_PACKET_KEYS = (
    "model_council_packet",
    "execution_packet",
    "latest_model_council_packet",
    "latest_execution_packet",
    "model_council_study_packet",
    "study_packet",
    "latest_model_council_study_packet",
    "latest_study_packet",
)
_NESTED_PACKET_CONTAINERS = (
    "latest_signal",
    "tracking_summary",
    "model_council_result",
    "model_council",
    "signal_payload",
    "tracker_payload",
    "payload",
)
_BLOCKED_BROKER_STATUSES = {
    "blocked",
    "blocked_by_runtime",
    "cooldown",
    "external_shooter_required",
    "live_disabled",
    "runtime_blocked",
    "waiting_for_manual_execution",
    "watching",
    "wait",
    "waiting",
}


@dataclass(frozen=True)
class EnterNowPackage:
    key: str
    session_id: str
    packet_id: str
    packet_type: str
    source: str
    side: str
    lane: str
    final_score: float | None
    threshold: float | None
    timing_mode: str
    entry_now_allowed: bool
    blocked: bool
    blocker: str
    broker_status: str
    broker_message: str
    created_epoch: float | None
    valid_until_epoch: float | None
    raw: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_fresh(self, *, now_epoch: float | None = None, max_age_sec: float = 900.0) -> bool:
        now = float(now_epoch if now_epoch is not None else time.time())
        if self.valid_until_epoch is not None and self.valid_until_epoch + 2.0 < now:
            return False
        if self.created_epoch is not None and now - self.created_epoch > max(1.0, float(max_age_sec)):
            return False
        return True


def extract_enter_now_packages(
    session_payload: Mapping[str, Any] | None,
    *,
    now_epoch: float | None = None,
    max_age_sec: float = 900.0,
    fresh_only: bool = False,
) -> list[EnterNowPackage]:
    payload = _mapping(session_payload)
    if not payload:
        return []
    now = float(now_epoch if now_epoch is not None else time.time())
    broker_state = _resolve_broker_state(payload)
    packages: list[EnterNowPackage] = []
    seen: set[str] = set()
    for source, packet in _iter_packet_candidates(payload):
        package = build_enter_now_package(
            packet,
            session_payload=payload,
            source=source,
            broker_state=broker_state,
        )
        if package is None:
            continue
        if package.key in seen:
            continue
        if fresh_only and not package.is_fresh(now_epoch=now, max_age_sec=max_age_sec):
            continue
        seen.add(package.key)
        packages.append(package)
    packages.sort(
        key=lambda item: (
            0 if item.blocked else 1,
            -(item.created_epoch or 0.0),
            item.packet_id,
            item.source,
        )
    )
    return packages


def build_enter_now_package(
    packet_payload: Mapping[str, Any] | None,
    *,
    session_payload: Mapping[str, Any] | None = None,
    source: str = "packet",
    broker_state: Mapping[str, Any] | None = None,
) -> EnterNowPackage | None:
    packet = _mapping(packet_payload)
    if not packet:
        return None
    timing_mode, entry_now_allowed = _timing_mode_and_entry_now(packet)
    if not entry_now_allowed and _upper(timing_mode) != ENTER_NOW_MODE:
        return None

    session = _mapping(session_payload)
    broker = _mapping(broker_state) or _resolve_broker_state(session)
    execution = _mapping(packet.get("execution"))
    council = _mapping(packet.get("model_council"))
    promotion = _mapping(packet.get("promotion_trace"))
    lane_payload = _mapping(packet.get("execution_lane") or council.get("execution_lane") or promotion.get("execution_lane"))
    packet_type = _packet_type(packet)
    side = _first_side(
        execution.get("side"),
        council.get("final_side"),
        council.get("side"),
        promotion.get("candidate_side"),
        packet.get("side"),
        packet.get("action"),
        session.get("action"),
    )
    lane = _text(
        lane_payload.get("name")
        or packet.get("selected_execution_lane")
        or council.get("selected_execution_lane")
        or promotion.get("selected_lane")
        or "LANE_PENDING"
    )
    final_score = _first_number(
        council.get("final_execution_score"),
        packet.get("final_execution_score"),
        promotion.get("final_execution_score"),
        council.get("final_score"),
        packet.get("final_score"),
        promotion.get("final_score"),
    )
    threshold = _first_number(
        council.get("execution_threshold"),
        packet.get("execution_threshold"),
        promotion.get("execution_threshold"),
    )
    packet_id = _text(
        packet.get("packet_id")
        or promotion.get("packet_id")
        or packet.get("decision_id")
        or packet.get("candidate_id")
        or promotion.get("candidate_id")
    )
    created_epoch = _first_number(
        packet.get("created_epoch_sec"),
        packet.get("created_epoch"),
        packet.get("published_epoch_sec"),
        packet.get("published_epoch"),
        session.get("last_capture_epoch"),
    )
    valid_until_epoch = _first_number(packet.get("valid_until_epoch_sec"), packet.get("valid_until_epoch"))
    broker_status = _text(broker.get("status")).lower()
    broker_message = _text(broker.get("message") or broker.get("reason") or broker.get("last_message"))
    blocker = _text(
        broker_message
        or promotion.get("true_blocker")
        or promotion.get("blocked_by")
        or packet.get("true_blocker")
        or packet.get("block_reason")
        or promotion.get("next_required")
        or packet.get("next_required")
    )
    execution_enabled = execution.get("enabled")
    is_execution_packet = packet_type == EXECUTION_PACKET_TYPE
    blocked = (
        broker_status in _BLOCKED_BROKER_STATUSES
        or packet_type == STUDY_PACKET_TYPE
        or not is_execution_packet
        or execution_enabled is False
    )
    raw = _safe_json_dict(packet)
    key = _package_key(
        session_id=_text(packet.get("session_id") or session.get("session_id")),
        packet_id=packet_id,
        packet_type=packet_type,
        side=side,
        lane=lane,
        valid_until_epoch=valid_until_epoch,
        created_epoch=created_epoch,
        raw=raw,
    )
    return EnterNowPackage(
        key=key,
        session_id=_text(packet.get("session_id") or session.get("session_id")),
        packet_id=packet_id,
        packet_type=packet_type,
        source=source,
        side=side,
        lane=lane,
        final_score=final_score,
        threshold=threshold,
        timing_mode=_upper(timing_mode or ENTER_NOW_MODE),
        entry_now_allowed=bool(entry_now_allowed),
        blocked=bool(blocked),
        blocker=blocker or ("study packet is not executable" if packet_type == STUDY_PACKET_TYPE else ""),
        broker_status=broker_status,
        broker_message=broker_message,
        created_epoch=created_epoch,
        valid_until_epoch=valid_until_epoch,
        raw=raw,
    )


def format_enter_now_notification(package: EnterNowPackage) -> str:
    status = "BLOCKED" if package.blocked else "ENTER NOW"
    side = package.side or "HOLD"
    score = _score_text(package.final_score, package.threshold)
    lane = package.lane or "LANE_PENDING"
    packet_id = package.packet_id or "packet"
    reason = package.blocker or package.broker_status or "runtime state pending"
    return f"PhoenixGuard {status}: {side} {lane} {score} packet={packet_id} reason={reason}"


def _iter_packet_candidates(payload: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    visited: set[int] = set()

    def visit(container: Mapping[str, Any], path: str, depth: int) -> Iterable[tuple[str, Mapping[str, Any]]]:
        if depth > 4:
            return
        marker = id(container)
        if marker in visited:
            return
        visited.add(marker)
        if _looks_like_packet(container):
            yield path, container
        for key in _PACKET_KEYS:
            value = container.get(key)
            if isinstance(value, Mapping):
                yield f"{path}.{key}", value
        for key in _NESTED_PACKET_CONTAINERS:
            value = container.get(key)
            if isinstance(value, Mapping):
                yield from visit(value, f"{path}.{key}", depth + 1)

    yield from visit(payload, "session", 0)


def _looks_like_packet(value: Mapping[str, Any]) -> bool:
    if value.get("packet_type") or value.get("schema_version"):
        return bool(
            value.get("packet_id")
            or value.get("promotion_trace")
            or value.get("timing_decision")
            or value.get("execution")
        )
    return False


def _resolve_broker_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidates = [
        payload.get("broker_execution_state"),
        _mapping(payload.get("latest_signal")).get("broker_execution_state"),
        _mapping(payload.get("tracking_summary")).get("broker_execution_state"),
    ]
    for candidate in candidates:
        mapping = _mapping(candidate)
        if mapping:
            return mapping
    return {}


def _timing_mode_and_entry_now(packet: Mapping[str, Any]) -> tuple[str, bool]:
    council = _mapping(packet.get("model_council"))
    promotion = _mapping(packet.get("promotion_trace"))
    timing_decision = _first_mapping(
        packet.get("timing_decision"),
        council.get("timing_decision"),
        promotion.get("timing_decision"),
    )
    timing_forecast = _first_mapping(
        packet.get("timing_forecast"),
        council.get("timing_forecast"),
        timing_decision.get("timing_forecast"),
    )
    timing_entry = _first_mapping(timing_decision.get("entry_timing"), timing_forecast.get("entry_timing"))
    mode = _text(
        timing_decision.get("timing_mode")
        or timing_entry.get("mode")
        or timing_forecast.get("best_entry_mode")
        or promotion.get("timing_mode")
        or packet.get("timing_mode")
    )
    raw_allowed = _first_present(
        timing_decision.get("entry_now_allowed"),
        timing_entry.get("entry_now_allowed"),
        timing_forecast.get("entry_now_allowed"),
        promotion.get("entry_now_allowed"),
        packet.get("entry_now_allowed"),
    )
    allowed = _bool(raw_allowed)
    if allowed is None:
        allowed = _upper(mode) == ENTER_NOW_MODE or _upper(timing_entry.get("mode")) == ENTER_NOW_MODE
    return mode, bool(allowed)


def _packet_type(packet: Mapping[str, Any]) -> str:
    raw = _upper(packet.get("packet_type") or packet.get("schema_version"))
    if EXECUTION_PACKET_TYPE in raw:
        return EXECUTION_PACKET_TYPE
    if "STUDY" in raw:
        return STUDY_PACKET_TYPE
    return raw or "UNKNOWN_PACKET"


def _package_key(
    *,
    session_id: str,
    packet_id: str,
    packet_type: str,
    side: str,
    lane: str,
    valid_until_epoch: float | None,
    created_epoch: float | None,
    raw: Mapping[str, Any],
) -> str:
    identity = {
        "session_id": session_id,
        "packet_id": packet_id,
        "packet_type": packet_type,
        "side": side,
        "lane": lane,
        "valid_until_epoch": int(valid_until_epoch or 0.0),
        "created_epoch": int(created_epoch or 0.0),
    }
    if not packet_id:
        identity["raw_hash"] = hashlib.sha1(
            json.dumps(raw, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
    return "|".join(str(identity[key]) for key in sorted(identity))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        mapping = _mapping(value)
        if mapping:
            return mapping
    return {}


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value in (None, "", [], {}):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            return number
    return None


def _first_side(*values: Any) -> str:
    for value in values:
        text = _upper(value)
        if text in {"BUY", "SELL"}:
            return text
    return "HOLD"


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _text(value).lower()
    if text in {"1", "true", "yes", "y", "on", "allowed", "enter_now"}:
        return True
    if text in {"0", "false", "no", "n", "off", "blocked", "wait"}:
        return False
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper().replace("-", "_").replace(" ", "_")


def _score_text(score: float | None, threshold: float | None) -> str:
    if score is None:
        return "score=pending"
    if threshold is None:
        return f"score={score:.2f}"
    return f"score={score:.2f}/{threshold:.2f}"


def _safe_json_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return {str(key): str(item) for key, item in value.items()}
