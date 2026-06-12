from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence


SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH = 50
SEQUENCE_CONTEXT_MIN_CONFIDENCE = 0.75
SEQUENCE_CONTEXT_MIN_BOX_HISTORY_LEN = 1
SEQUENCE_CONTEXT_MIN_PROGRESSION_LEN = 1
SEQUENCE_CONTEXT_SCHEMA_VERSION = "PG_SEQUENCE_CONTEXT_V3"
_SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH = SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH
_SEQUENCE_CONTEXT_MIN_CONFIDENCE = SEQUENCE_CONTEXT_MIN_CONFIDENCE
_TRACKED_CANDLE_HISTORY_LIMIT = 24


@dataclass(frozen=True, slots=True)
class SequenceContextV3:
    sequence_id: str
    session_id: str
    sequence_index: int
    frame_start: int
    frame_end: int
    sequence_length: int
    frames_received: int
    frames_used: int
    candle_count: int
    timeframe: str
    sequence_signature: str
    sequence_confidence: float
    global_direction: str
    local_direction: str
    current_phase: str
    progression_score: float
    progression: tuple[dict[str, Any], ...]
    motifs: tuple[Any, ...]
    box_history: tuple[dict[str, Any], ...]
    angle_vectors: tuple[Any, ...]
    sniper_zones: tuple[dict[str, Any], ...]
    target_zones: tuple[dict[str, Any], ...]
    invalidation_zones: tuple[dict[str, Any], ...]
    sequence_status: str
    frame_range: tuple[int, int]
    candle_range: tuple[int, int]
    frames_dropped: int
    sequence_age_ms: int
    packet_age_ms: int
    decision_age_ms: int
    model_vote_age_ms: int
    entry_progression: dict[str, Any]
    tracking_summary: dict[str, Any]
    sequence_history: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEQUENCE_CONTEXT_SCHEMA_VERSION,
            "sequence_id": self.sequence_id,
            "session_id": self.session_id,
            "sequence_index": self.sequence_index,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "sequence_length": self.sequence_length,
            "frames_received": self.frames_received,
            "frames_used": self.frames_used,
            "candle_count": self.candle_count,
            "timeframe": self.timeframe,
            "sequence_signature": self.sequence_signature,
            "sequence_confidence": round(float(self.sequence_confidence), 4),
            "global_direction": self.global_direction,
            "local_direction": self.local_direction,
            "current_phase": self.current_phase,
            "progression_score": round(float(self.progression_score), 4),
            "progression": [dict(item) for item in self.progression],
            "motifs": list(self.motifs),
            "box_history": [dict(item) for item in self.box_history],
            "angle_vectors": list(self.angle_vectors),
            "sniper_zones": [dict(item) for item in self.sniper_zones],
            "target_zones": [dict(item) for item in self.target_zones],
            "invalidation_zones": [dict(item) for item in self.invalidation_zones],
            "sequence_status": self.sequence_status,
            "status": self.sequence_status,
            "frame_range": [self.frame_range[0], self.frame_range[1]],
            "candle_range": [self.candle_range[0], self.candle_range[1]],
            "frames_dropped": self.frames_dropped,
            "sequence_age_ms": self.sequence_age_ms,
            "packet_age_ms": self.packet_age_ms,
            "decision_age_ms": self.decision_age_ms,
            "model_vote_age_ms": self.model_vote_age_ms,
            "entry_progression": dict(self.entry_progression),
            "tracking_summary": dict(self.tracking_summary),
            "sequence_history": [dict(item) for item in self.sequence_history],
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(value)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf") }:
        return float(default)
    return float(parsed)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _clip01(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf") }:
        parsed = float(default)
    return max(0.0, min(1.0, parsed))


def _upper(value: Any, default: str = "HOLD") -> str:
    text = str(value or default).strip().upper()
    return text or default


def _hash_payload(parts: Mapping[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_box_like(value: Any) -> dict[str, Any]:
    row = _mapping(value)
    if not row:
        return {}
    normalized = dict(row)
    if "bbox" in normalized:
        normalized["bbox"] = _sequence(normalized.get("bbox"))
    if "meta" in normalized and isinstance(normalized.get("meta"), Mapping):
        normalized["meta"] = dict(normalized["meta"])
    return normalized


def _candle_side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL", "GREEN", "LIME"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT", "RED", "MAGENTA", "PINK"}:
        return "SELL"
    return "HOLD"


def _tracked_candle_history_from_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    chart_state = _mapping(snapshot.get("chart_state"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    signal = _mapping(snapshot.get("latest_signal"))
    candles = _sequence(
        snapshot.get("tracked_candles")
        or chart_state.get("tracked_candles")
        or tracking.get("tracked_candles")
        or signal.get("tracked_candles")
    )
    rows: list[dict[str, Any]] = []
    start = max(0, len(candles) - _TRACKED_CANDLE_HISTORY_LIMIT)
    for index, candle in enumerate(candles[start:], start=start + 1):
        row = _mapping(candle)
        if not row:
            continue
        side = _candle_side(row.get("direction") or row.get("side") or row.get("color"))
        payload = {
            "key": str(row.get("key") or row.get("track_id") or f"tracked_candle_{index}"),
            "label": str(row.get("label") or f"C{index} {side}"),
            "source": "tracked_candles",
            "role": "observed_candle_sequence",
            "direction": side,
            "color": str(row.get("color") or "").strip(),
            "track_id": row.get("track_id", index),
            "price_proxy": row.get("price_proxy"),
            "body_height_pct": row.get("body_height_pct"),
            "normalized_x": row.get("normalized_x"),
            "normalized_y": row.get("normalized_y"),
            "story": f"Observed candle {index} voted {side}.",
        }
        bbox = _sequence(row.get("bbox"))
        if bbox:
            payload["bbox"] = bbox
        rows.append(payload)
    return rows


def _structure_boxes_from_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    chart_state = _mapping(snapshot.get("chart_state"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    signal = _mapping(snapshot.get("latest_signal"))
    historical = _sequence(
        snapshot.get("historical_structure")
        or chart_state.get("historical_structure")
        or tracking.get("historical_structure")
        or signal.get("historical_structure")
        or snapshot.get("structure_boxes")
        or chart_state.get("structure_boxes")
        or tracking.get("structure_boxes")
        or signal.get("structure_boxes")
    )
    if historical:
        boxes = [_normalize_box_like(item) for item in historical if _mapping(item)]
        if len(boxes) < _TRACKED_CANDLE_HISTORY_LIMIT:
            boxes.extend(_tracked_candle_history_from_snapshot(snapshot))
        return boxes
    boxes: list[dict[str, Any]] = []
    for key in ("global_box", "local_box", "current_box"):
        box = _normalize_box_like(snapshot.get(key) or chart_state.get(key) or tracking.get(key) or signal.get(key))
        if box:
            boxes.append(box)
    boxes.extend(_tracked_candle_history_from_snapshot(snapshot))
    return boxes


def _zone_list(snapshot: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    tracking = _mapping(snapshot.get("tracking_summary"))
    signal = _mapping(snapshot.get("latest_signal"))
    chart_state = _mapping(snapshot.get("chart_state"))
    payload = _sequence(snapshot.get(key)) or _sequence(chart_state.get(key)) or _sequence(tracking.get(key)) or _sequence(signal.get(key))
    return [_normalize_box_like(item) for item in payload if _mapping(item)]


def _entry_progression_fallback(
    *,
    current_phase: str,
    progression_score: float,
    progression_steps: Sequence[Any],
    sequence_length: int,
    sequence_state: Mapping[str, Any],
    chart_state: Mapping[str, Any],
    tracking: Mapping[str, Any],
    signal: Mapping[str, Any],
) -> dict[str, Any]:
    if not progression_steps:
        return {}
    stage = str(
        sequence_state.get("progression_stage")
        or chart_state.get("structure_setup")
        or tracking.get("entry_state")
        or signal.get("entry_state")
        or current_phase
        or "progression"
    ).strip()
    continuation = _clip01(
        chart_state.get("continuation_probability")
        or tracking.get("continuation_score")
        or signal.get("continuation_score")
        or sequence_state.get("continuation_probability")
        or progression_score,
        progression_score,
    )
    exhaustion = _clip01(
        chart_state.get("fakeout_probability")
        or chart_state.get("reversal_probability")
        or tracking.get("reversal_score")
        or signal.get("reversal_score")
        or sequence_state.get("fakeout_probability")
        or 0.0,
        0.0,
    )
    return {
        "progression_stage": stage or "progression",
        "maturity_score": _clip01(sequence_state.get("progression_maturity") or progression_score, progression_score),
        "progression_velocity": _clip01(
            sequence_state.get("progression_velocity")
            or (len(progression_steps) / max(1, int(sequence_length))),
            0.0,
        ),
        "continuation_strength": continuation,
        "exhaustion_risk": exhaustion,
        "source": "sequence_context_memory_compression",
    }


def _sequence_context_candidates(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in (
        ("model_council", "sequence_context"),
        ("model_council_result", "model_council", "sequence_context"),
        ("study_packet", "model_council", "sequence_context"),
        ("execution_packet", "model_council", "sequence_context"),
        ("latest_signal", "model_council", "sequence_context"),
    ):
        node: Any = packet
        for key in path:
            node = _mapping(node).get(key)
        if isinstance(node, Mapping) and node:
            candidates.append(dict(node))
    return candidates


def _normalize_sequence_context(row: Mapping[str, Any], *, synthesize_signature: bool = True) -> dict[str, Any]:
    if not row:
        return {}
    normalized = dict(row)
    normalized["sequence_index"] = max(0, _int(normalized.get("sequence_index"), 0))
    normalized["sequence_id"] = str(normalized.get("sequence_id") or "").strip()
    normalized["session_id"] = str(normalized.get("session_id") or "").strip()
    normalized["frame_start"] = _int(normalized.get("frame_start"), 0)
    normalized["frame_end"] = _int(normalized.get("frame_end"), 0)
    normalized["sequence_length"] = max(0, _int(normalized.get("sequence_length"), 0))
    normalized["frames_received"] = max(0, _int(normalized.get("frames_received"), 0))
    normalized["frames_used"] = max(0, _int(normalized.get("frames_used"), 0))
    normalized["candle_count"] = max(0, _int(normalized.get("candle_count"), 0))
    normalized["timeframe"] = str(normalized.get("timeframe") or "").strip().upper()
    normalized["sequence_signature"] = str(normalized.get("sequence_signature") or "").strip()
    normalized["sequence_confidence"] = _clip01(normalized.get("sequence_confidence", 0.0), 0.0)
    normalized["global_direction"] = _upper(normalized.get("global_direction"))
    normalized["local_direction"] = _upper(normalized.get("local_direction"))
    normalized["current_phase"] = str(normalized.get("current_phase") or normalized.get("sequence_phase") or "progression").strip()
    normalized["progression_score"] = _clip01(normalized.get("progression_score", 0.0), 0.0)
    normalized["progression"] = [dict(item) for item in _sequence(normalized.get("progression")) if isinstance(item, Mapping)]
    normalized["motifs"] = list(_sequence(normalized.get("motifs")))
    normalized["box_history"] = [dict(item) for item in _sequence(normalized.get("box_history")) if isinstance(item, Mapping)]
    normalized["angle_vectors"] = list(_sequence(normalized.get("angle_vectors")))
    normalized["sniper_zones"] = [dict(item) for item in _sequence(normalized.get("sniper_zones")) if isinstance(item, Mapping)]
    normalized["target_zones"] = [dict(item) for item in _sequence(normalized.get("target_zones")) if isinstance(item, Mapping)]
    normalized["invalidation_zones"] = [dict(item) for item in _sequence(normalized.get("invalidation_zones")) if isinstance(item, Mapping)]
    normalized["sequence_status"] = str(
        normalized.get("sequence_status") or normalized.get("status") or "PARTIAL_SEQUENCE"
    ).strip().upper()
    normalized["entry_progression"] = _mapping(normalized.get("entry_progression"))
    normalized["tracking_summary"] = _mapping(normalized.get("tracking_summary"))
    normalized["sequence_history"] = [dict(item) for item in _sequence(normalized.get("sequence_history")) if isinstance(item, Mapping)]
    normalized["frame_range"] = (
        _int(_sequence(normalized.get("frame_range"))[:1][0] if _sequence(normalized.get("frame_range")) else normalized.get("frame_start"), 0),
        _int(_sequence(normalized.get("frame_range"))[1] if len(_sequence(normalized.get("frame_range"))) > 1 else normalized.get("frame_end"), 0),
    )
    normalized["candle_range"] = (
        _int(_sequence(normalized.get("candle_range"))[:1][0] if _sequence(normalized.get("candle_range")) else 1, 1),
        _int(_sequence(normalized.get("candle_range"))[1] if len(_sequence(normalized.get("candle_range"))) > 1 else normalized.get("candle_count"), 0),
    )
    normalized["frames_dropped"] = max(0, _int(normalized.get("frames_dropped"), 0))
    normalized["sequence_age_ms"] = max(0, _int(normalized.get("sequence_age_ms"), 0))
    normalized["packet_age_ms"] = max(0, _int(normalized.get("packet_age_ms"), 0))
    normalized["decision_age_ms"] = max(0, _int(normalized.get("decision_age_ms"), 0))
    normalized["model_vote_age_ms"] = max(0, _int(normalized.get("model_vote_age_ms"), 0))
    if synthesize_signature and not normalized["sequence_signature"]:
        normalized["sequence_signature"] = _hash_payload(
            {
                "sequence_id": normalized["sequence_id"],
                "session_id": normalized["session_id"],
                "frame_range": normalized["frame_range"],
                "candle_range": normalized["candle_range"],
                "candle_count": normalized["candle_count"],
                "global_direction": normalized["global_direction"],
                "local_direction": normalized["local_direction"],
                "current_phase": normalized["current_phase"],
                "box_history": normalized["box_history"],
                "sniper_zones": normalized["sniper_zones"],
                "target_zones": normalized["target_zones"],
                "invalidation_zones": normalized["invalidation_zones"],
            }
        )
    if normalized["frame_end"] and normalized["frame_start"] and normalized["frame_end"] < normalized["frame_start"]:
        normalized["frame_start"], normalized["frame_end"] = normalized["frame_end"], normalized["frame_start"]
    if normalized["candle_range"][0] > normalized["candle_range"][1]:
        normalized["candle_range"] = (normalized["candle_range"][1], normalized["candle_range"][0])
    return normalized


def sequence_context_readiness_report(
    value: Mapping[str, Any] | SequenceContextV3 | None,
    *,
    source_module: str = "model_council_resolver",
) -> dict[str, Any]:
    if isinstance(value, SequenceContextV3):
        normalized = value.as_dict()
    else:
        normalized = _normalize_sequence_context(_mapping(value), synthesize_signature=False)
    source = str(source_module or "model_council_resolver").strip() or "model_council_resolver"
    entry_progression = _mapping(normalized.get("entry_progression"))
    progression = _sequence(normalized.get("progression"))
    box_history = _sequence(normalized.get("box_history"))
    motifs = _sequence(normalized.get("motifs"))
    sequence_length = max(0, _int(normalized.get("sequence_length"), 0))
    frames_received = max(0, _int(normalized.get("frames_received"), 0))
    frames_used = max(0, _int(normalized.get("frames_used"), 0))
    sequence_confidence = _clip01(normalized.get("sequence_confidence"), 0.0)
    sequence_status = str(normalized.get("sequence_status") or "").strip().upper()
    sequence_signature = str(normalized.get("sequence_signature") or "").strip()
    checks: list[dict[str, Any]] = []

    def add_check(
        field: str,
        received: Any,
        required: Any,
        ok: bool,
        *,
        failed_module: str = source,
        blocking: bool = True,
        reason: str = "",
    ) -> None:
        checks.append(
            {
                "field": field,
                "received": received,
                "required": required,
                "ok": bool(ok),
                "blocking": bool(blocking),
                "failed_module": failed_module,
                "reason": reason or f"{field} did not meet sequence readiness requirements",
            }
        )

    add_check("sequence_id", str(normalized.get("sequence_id") or ""), "non-empty", bool(normalized.get("sequence_id")))
    add_check("session_id", str(normalized.get("session_id") or ""), "non-empty", bool(normalized.get("session_id")))
    add_check("sequence_signature", sequence_signature, "non-empty", bool(sequence_signature), failed_module="packet_builder")
    add_check("sequence_status", sequence_status or "MISSING", "COMPLETE", sequence_status == "COMPLETE")
    add_check(
        "sequence_length",
        sequence_length,
        f">={SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH}",
        sequence_length >= SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH,
    )
    add_check("frames_used", frames_used, f">=sequence_length({sequence_length})", frames_used >= sequence_length)
    add_check("frames_received", frames_received, f">=frames_used({frames_used})", frames_received >= frames_used)
    add_check(
        "sequence_confidence",
        round(sequence_confidence, 4),
        f">={SEQUENCE_CONTEXT_MIN_CONFIDENCE}",
        sequence_confidence >= SEQUENCE_CONTEXT_MIN_CONFIDENCE,
    )
    add_check(
        "box_history_len",
        len(box_history),
        f">={SEQUENCE_CONTEXT_MIN_BOX_HISTORY_LEN}",
        len(box_history) >= SEQUENCE_CONTEXT_MIN_BOX_HISTORY_LEN,
        failed_module="memory_compression",
        blocking=False,
        reason="box_history is empty, so the visual play sequence cannot be audited deeply",
    )
    add_check(
        "progression_len",
        len(progression),
        f">={SEQUENCE_CONTEXT_MIN_PROGRESSION_LEN}",
        len(progression) >= SEQUENCE_CONTEXT_MIN_PROGRESSION_LEN,
        failed_module="memory_compression",
        blocking=False,
        reason="progression is empty, so entry progression context is weak",
    )
    add_check(
        "entry_progression_len",
        len(entry_progression),
        ">=1",
        bool(entry_progression),
        failed_module="memory_compression",
        blocking=False,
        reason="entry_progression is empty or was not attached",
    )

    blocking_failures = [dict(item) for item in checks if not item["ok"] and item["blocking"]]
    rejected_fields = [dict(item) for item in checks if not item["ok"]]

    def _is_missing_received(value: Any) -> bool:
        if value is None:
            return True
        if value == 0:
            return True
        if isinstance(value, str):
            return value in {"", "MISSING"}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return len(value) == 0
        return False

    missing_fields = [
        dict(item)
        for item in checks
        if not item["ok"] and _is_missing_received(item["received"])
    ]
    if not normalized:
        failed_module = "tracker"
    elif any(item["field"] == "sequence_signature" and not item["ok"] for item in checks):
        failed_module = "packet_builder"
    elif any(item["failed_module"] == "memory_compression" and not item["ok"] for item in checks):
        failed_module = "memory_compression"
    elif blocking_failures:
        failed_module = "model_council_resolver"
    else:
        failed_module = source
    blocking_summary = "; ".join(
        f"{item['field']}={item['received']} required {item['required']}"
        for item in blocking_failures[:4]
    )
    return {
        "schema_version": "PG_SEQUENCE_CONTEXT_READINESS_V3",
        "ready": not blocking_failures,
        "source_module": source,
        "failed_module": failed_module if rejected_fields else "",
        "sequence_id": str(normalized.get("sequence_id") or ""),
        "session_id": str(normalized.get("session_id") or ""),
        "sequence_status": sequence_status or "MISSING",
        "status": "COMPLETE" if not blocking_failures else "INCOMPLETE",
        "sequence_length": sequence_length,
        "frames_received": frames_received,
        "frames_used": frames_used,
        "frames_dropped": max(0, _int(normalized.get("frames_dropped"), frames_received - frames_used)),
        "box_history_len": len(box_history),
        "entry_progression_len": len(entry_progression),
        "progression_len": len(progression),
        "motif_count": len(motifs),
        "sequence_signature": sequence_signature,
        "sequence_confidence": round(sequence_confidence, 4),
        "minimum_required_sequence_length": SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH,
        "minimum_required_box_history_len": SEQUENCE_CONTEXT_MIN_BOX_HISTORY_LEN,
        "minimum_required_progression_len": SEQUENCE_CONTEXT_MIN_PROGRESSION_LEN,
        "minimum_required_sequence_confidence": SEQUENCE_CONTEXT_MIN_CONFIDENCE,
        "missing_fields": missing_fields,
        "rejected_fields": rejected_fields,
        "blocking_failures": blocking_failures,
        "checks": checks,
        "next_required": "none" if not blocking_failures else f"sequence context incomplete: {blocking_summary}",
    }


def resolve_sequence_context(packet: Mapping[str, Any]) -> SequenceContextV3:
    if not isinstance(packet, Mapping) or not packet:
        raise ValueError("sequence context missing")
    candidates = [_normalize_sequence_context(candidate, synthesize_signature=False) for candidate in _sequence_context_candidates(packet)]
    candidates = [candidate for candidate in candidates if candidate]
    if not candidates:
        raise ValueError("sequence context missing")
    canonical = candidates[0]
    for candidate in candidates[1:]:
        if candidate != canonical:
            raise ValueError("sequence context ambiguous")
    required = (
        "sequence_id",
        "session_id",
        "frame_start",
        "frame_end",
        "sequence_length",
        "frames_received",
        "frames_used",
        "candle_count",
        "timeframe",
        "sequence_confidence",
        "global_direction",
        "local_direction",
        "current_phase",
    )
    for field_name in required:
        value = canonical.get(field_name)
        if value in (None, "", []):
            raise ValueError(f"sequence context missing {field_name}")
    return SequenceContextV3(
        sequence_id=str(canonical["sequence_id"]),
        session_id=str(canonical["session_id"]),
        sequence_index=_int(canonical["sequence_index"]),
        frame_start=_int(canonical["frame_start"]),
        frame_end=_int(canonical["frame_end"]),
        sequence_length=max(1, _int(canonical["sequence_length"])),
        frames_received=max(0, _int(canonical["frames_received"])),
        frames_used=max(0, _int(canonical["frames_used"])),
        candle_count=max(0, _int(canonical["candle_count"])),
        timeframe=str(canonical["timeframe"]),
        sequence_signature=str(canonical["sequence_signature"]),
        sequence_confidence=_clip01(canonical["sequence_confidence"]),
        global_direction=str(canonical["global_direction"]),
        local_direction=str(canonical["local_direction"]),
        current_phase=str(canonical["current_phase"]),
        progression_score=_clip01(canonical["progression_score"]),
        progression=tuple(dict(item) for item in canonical.get("progression", [])),
        motifs=tuple(canonical.get("motifs", [])),
        box_history=tuple(dict(item) for item in canonical.get("box_history", [])),
        angle_vectors=tuple(canonical.get("angle_vectors", [])),
        sniper_zones=tuple(dict(item) for item in canonical.get("sniper_zones", [])),
        target_zones=tuple(dict(item) for item in canonical.get("target_zones", [])),
        invalidation_zones=tuple(dict(item) for item in canonical.get("invalidation_zones", [])),
        sequence_status=str(canonical.get("sequence_status") or "PARTIAL_SEQUENCE").strip().upper(),
        frame_range=(canonical["frame_start"], canonical["frame_end"]),
        candle_range=(1 if canonical["candle_count"] else 0, canonical["candle_count"]),
        frames_dropped=max(0, _int(canonical.get("frames_dropped"), 0)),
        sequence_age_ms=max(0, _int(canonical.get("sequence_age_ms"), 0)),
        packet_age_ms=max(0, _int(canonical.get("packet_age_ms"), 0)),
        decision_age_ms=max(0, _int(canonical.get("decision_age_ms"), 0)),
        model_vote_age_ms=max(0, _int(canonical.get("model_vote_age_ms"), 0)),
        entry_progression=_mapping(canonical.get("entry_progression")),
        tracking_summary=_mapping(canonical.get("tracking_summary")),
        sequence_history=tuple(dict(item) for item in canonical.get("sequence_history", [])),
    )


def build_sequence_context_v3(
    snapshot: Mapping[str, Any],
    *,
    packet: Mapping[str, Any] | None = None,
) -> SequenceContextV3:
    packet_data = _mapping(packet)
    tracking = _mapping(snapshot.get("tracking_summary") or packet_data.get("tracking_summary"))
    signal = _mapping(snapshot.get("latest_signal") or packet_data.get("latest_signal"))
    chart_state = _mapping(snapshot.get("chart_state"))
    sequence_state = _mapping(snapshot.get("sequence_state") or chart_state.get("sequence_state") or tracking.get("sequence_state"))
    sequence_model = _mapping(sequence_state.get("sequence_model"))
    entry_progression = _mapping(
        sequence_state.get("entry_progression")
        or chart_state.get("entry_progression")
        or tracking.get("entry_progression")
        or signal.get("entry_progression")
    )
    historical_structure = _structure_boxes_from_snapshot(snapshot)
    v3_execution_candidate = _mapping(snapshot.get("v3_execution_candidate"))
    if not historical_structure and bool(v3_execution_candidate.get("active")):
        candidate_side = _upper(
            v3_execution_candidate.get("side")
            or snapshot.get("candidate_side")
            or snapshot.get("side")
            or signal.get("candidate_side")
        )
        historical_structure = [
            {
                "object_id": str(v3_execution_candidate.get("candidate_id") or snapshot.get("candidate_id") or "v3_candidate_sequence_evidence"),
                "kind": "v3_execution_candidate_evidence",
                "side": candidate_side,
                "source": str(v3_execution_candidate.get("source") or "v3_execution_candidate"),
                "lane": str(v3_execution_candidate.get("lane") or snapshot.get("selected_lane") or ""),
                "confidence": _clip01(v3_execution_candidate.get("score") or snapshot.get("sequence_confidence") or snapshot.get("confidence"), 0.0),
                "story": "Explicit V3 execution candidate supplied sequence fallback evidence.",
            }
        ]
    projection = _mapping(snapshot.get("projection") or chart_state.get("projected_next_box") or signal.get("projected_next_box"))
    support_resistance = _mapping(
        snapshot.get("support_resistance_context")
        or tracking.get("support_resistance_context")
        or signal.get("support_resistance_context")
    )
    zones = _zone_list(snapshot, "support_resistance_zones")
    sniper_zones = [zone for zone in zones if str(zone.get("kind") or zone.get("label") or "").lower().find("sniper") >= 0]
    target_zones = [zone for zone in zones if str(zone.get("kind") or zone.get("label") or "").lower().find("target") >= 0]
    invalidation_zones = [zone for zone in zones if str(zone.get("kind") or zone.get("label") or "").lower().find("invalidation") >= 0]
    angle_vectors = _sequence(snapshot.get("angle_vectors") or chart_state.get("angle_vectors") or signal.get("angle_vectors") or sequence_model.get("angle_vectors"))
    motifs = _sequence(snapshot.get("motifs") or chart_state.get("motifs") or entry_progression.get("motifs"))
    if not motifs:
        motifs = [item.get("story", "") for item in historical_structure if isinstance(item, Mapping) and item.get("story")]
    progression_steps = _sequence(snapshot.get("progression") or chart_state.get("progression") or sequence_state.get("progression"))
    if not progression_steps:
        progression_steps = historical_structure
    sequence_history = _sequence(snapshot.get("sequence_history") or chart_state.get("sequence_history") or tracking.get("sequence_history"))
    candle_count = _int(snapshot.get("candle_count") or chart_state.get("recent_candle_count") or tracking.get("visible_candle_count") or len(_sequence(tracking.get("tracked_candles", []))), 0)
    sequence_index = _int(snapshot.get("sequence_index") or sequence_state.get("sequence_index") or packet_data.get("sequence_index") or len(historical_structure), 0)
    frames_used = _int(snapshot.get("frames_used") or packet_data.get("frames_used") or snapshot.get("capture_count") or packet_data.get("capture_count"), 0)
    frames_received = _int(snapshot.get("frames_received") or packet_data.get("frames_received") or snapshot.get("capture_count") or packet_data.get("capture_count"), 0)
    sequence_length = max(
        1,
        _int(snapshot.get("sequence_length") or sequence_state.get("sequence_length") or candle_count or frames_used or len(historical_structure), 1),
    )
    if frames_used <= 0:
        frames_used = sequence_length
    if frames_received <= 0:
        frames_received = max(frames_used, sequence_length)
    frame_end = _int(snapshot.get("frame_index") or snapshot.get("frame_end") or packet_data.get("frame_id") or packet_data.get("frame_end") or frames_received, 0)
    if frame_end <= 0:
        frame_end = max(frames_received, frames_used, sequence_length)
    frame_start = _int(snapshot.get("frame_start") or packet_data.get("frame_start") or max(1, frame_end - sequence_length + 1), 1)
    if frame_start <= 0:
        frame_start = max(1, frame_end - sequence_length + 1)
    if frame_end < frame_start:
        frame_start, frame_end = frame_end, frame_start
    if candle_count <= 0:
        candle_count = max(1, len(historical_structure) or len(zones) or frames_used)
    sequence_id = str(
        snapshot.get("sequence_id")
        or packet_data.get("sequence_id")
        or f"seq_{str(snapshot.get('session_id') or packet_data.get('session_id') or 'session').strip()}_{frame_end}"
    ).strip()
    session_id = str(snapshot.get("session_id") or packet_data.get("session_id") or tracking.get("session_id") or signal.get("session_id") or "").strip()
    timeframe = str(
        snapshot.get("timeframe")
        or chart_state.get("timeframe")
        or tracking.get("detected_timeframe")
        or signal.get("focus_timeframe")
        or packet_data.get("timeframe")
        or "M5"
    ).strip().upper()
    global_direction = _upper(
        snapshot.get("global_direction")
        or chart_state.get("global_direction")
        or tracking.get("global_direction")
        or signal.get("global_direction")
        or sequence_state.get("macro_swing_direction")
    )
    local_direction = _upper(
        snapshot.get("local_direction")
        or chart_state.get("local_direction")
        or tracking.get("local_direction")
        or signal.get("local_direction")
        or sequence_state.get("recent_swing_direction")
    )
    current_phase = str(
        snapshot.get("current_phase")
        or sequence_state.get("progression_stage")
        or chart_state.get("swing_state", {}).get("swing_phase")
        or chart_state.get("structure_setup")
        or "progression"
    ).strip()
    progression_score = _clip01(
        snapshot.get("sequence_confidence")
        or chart_state.get("history_coherence")
        or sequence_state.get("sequence_model", {}).get("history_coherence")
        or sequence_state.get("progression_maturity")
        or sequence_state.get("progression_velocity")
        or signal.get("confidence")
        or tracking.get("state_confidence")
        or 0.0
    )
    if not entry_progression:
        entry_progression = _entry_progression_fallback(
            current_phase=current_phase,
            progression_score=progression_score,
            progression_steps=progression_steps,
            sequence_length=sequence_length,
            sequence_state=sequence_state,
            chart_state=chart_state,
            tracking=tracking,
            signal=signal,
        )
    complete = (
        sequence_length >= _SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH
        and frames_used >= sequence_length
        and frames_received >= frames_used
        and progression_score >= _SEQUENCE_CONTEXT_MIN_CONFIDENCE
        and bool(historical_structure)
        and bool(progression_steps)
        and bool(entry_progression)
    )
    status = str(snapshot.get("sequence_status") or "").strip().upper()
    if complete and status in {"", "PARTIAL_SEQUENCE", "INCOMPLETE", "MISSING", "WARMING"}:
        status = "COMPLETE"
    elif not status:
        status = "PARTIAL_SEQUENCE"
    sequence_signature = _hash_payload(
        {
            "sequence_id": sequence_id,
            "session_id": session_id,
            "frame_range": [frame_start, frame_end],
            "candle_count": candle_count,
            "sequence_length": sequence_length,
            "frames_received": frames_received,
            "frames_used": frames_used,
            "timeframe": timeframe,
            "global_direction": global_direction,
            "local_direction": local_direction,
            "current_phase": current_phase,
            "progression": progression_steps,
            "box_history": historical_structure,
            "sniper_zones": sniper_zones,
            "target_zones": target_zones,
            "invalidation_zones": invalidation_zones,
        }
    )
    frames_dropped = max(0, frames_received - frames_used)
    sequence_age_ms = max(0, _int(snapshot.get("sequence_age_ms") or signal.get("sequence_age_ms") or 0, 0))
    packet_age_ms = max(0, _int(packet_data.get("packet_age_ms") or signal.get("packet_age_ms") or snapshot.get("packet_age_ms") or 0, 0))
    decision_age_ms = max(0, _int(snapshot.get("decision_age_ms") or signal.get("decision_age_ms") or 0, 0))
    model_vote_age_ms = max(0, _int(snapshot.get("model_vote_age_ms") or signal.get("model_vote_age_ms") or 0, 0))
    if status != "COMPLETE":
        sequence_age_ms = max(sequence_age_ms, packet_age_ms)
    return SequenceContextV3(
        sequence_id=sequence_id,
        session_id=session_id,
        sequence_index=sequence_index,
        frame_start=frame_start,
        frame_end=frame_end,
        sequence_length=sequence_length,
        frames_received=frames_received,
        frames_used=frames_used,
        candle_count=candle_count,
        timeframe=timeframe,
        sequence_signature=sequence_signature,
        sequence_confidence=progression_score,
        global_direction=global_direction,
        local_direction=local_direction,
        current_phase=current_phase,
        progression_score=progression_score,
        progression=tuple(dict(item) for item in progression_steps if isinstance(item, Mapping)),
        motifs=tuple(motifs),
        box_history=tuple(dict(item) for item in historical_structure),
        angle_vectors=tuple(angle_vectors),
        sniper_zones=tuple(sniper_zones),
        target_zones=tuple(target_zones),
        invalidation_zones=tuple(invalidation_zones),
        sequence_status=status,
        frame_range=(frame_start, frame_end),
        candle_range=(1, candle_count),
        frames_dropped=frames_dropped,
        sequence_age_ms=sequence_age_ms,
        packet_age_ms=packet_age_ms,
        decision_age_ms=decision_age_ms,
        model_vote_age_ms=model_vote_age_ms,
        entry_progression=dict(entry_progression),
        tracking_summary=dict(tracking),
        sequence_history=tuple(dict(item) for item in sequence_history if isinstance(item, Mapping)),
    )


__all__ = [
    "SEQUENCE_CONTEXT_COMPLETE_MIN_LENGTH",
    "SEQUENCE_CONTEXT_MIN_BOX_HISTORY_LEN",
    "SEQUENCE_CONTEXT_MIN_CONFIDENCE",
    "SEQUENCE_CONTEXT_MIN_PROGRESSION_LEN",
    "SEQUENCE_CONTEXT_SCHEMA_VERSION",
    "SequenceContextV3",
    "build_sequence_context_v3",
    "resolve_sequence_context",
    "sequence_context_readiness_report",
]
