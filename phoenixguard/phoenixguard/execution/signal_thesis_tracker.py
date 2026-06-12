from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


SIGNAL_THESIS_SCHEMA_VERSION = "PG_SIGNAL_THESIS_V3"
_ACTIVE_STATES = {"TRACKING", "ALLOW_PULLBACK", "PROTECT_WIN", "TARGET_REACHED"}
_TERMINAL_STATES = {"INVALIDATED", "PAIR_SWITCH_RESET", "NO_ACTIVE_THESIS"}
_GENERIC_SYMBOL_KEYS = {
    "",
    "ACTIVECHART",
    "LOCKEDACTIVECHART",
    "USERLOCKED",
    "USERLOCKEDACTIVECHART",
    "UNKNOWN",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(value)


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


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _opposite(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"


def _normalized_symbol(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().replace("/", "").split())


def _symbol_is_generic(value: Any) -> bool:
    return _normalized_symbol(value).replace(" ", "").replace("_", "") in _GENERIC_SYMBOL_KEYS


def _stable_hash(payload: Mapping[str, Any], *, prefix: str) -> str:
    blob = json.dumps(dict(payload), sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return prefix + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:18]


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        row = _mapping(value)
        if row:
            return row
    return {}


def _first_rows(*values: Any) -> list[dict[str, Any]]:
    for value in values:
        rows = _rows(value)
        if rows:
            return rows
    return []


def _bbox(value: Any) -> list[float]:
    row = _mapping(value)
    raw = (
        row.get("bbox")
        or row.get("pixel_bbox")
        or row.get("bounds")
        or row.get("normalized_bbox")
        or value
    )
    values = _sequence(raw)
    if len(values) < 4:
        return []
    try:
        x0, y0, x1, y1 = [float(values[index]) for index in range(4)]
    except (TypeError, ValueError):
        return []
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _center_y(value: Any) -> float | None:
    row = _mapping(value)
    for key in ("center_y", "price_y", "latest_y", "current_y", "y"):
        if key in row:
            parsed = _float(row.get(key), float("nan"))
            if parsed == parsed:
                return parsed
    box = _bbox(value)
    if len(box) >= 4:
        return (box[1] + box[3]) * 0.5
    return None


def _right_edge(value: Mapping[str, Any]) -> float:
    box = _bbox(value)
    return float(box[2]) if len(box) >= 4 else _float(value.get("x"), 0.0)


def _chart_height(snapshot: Mapping[str, Any]) -> float:
    tracking = _mapping(snapshot.get("tracking_summary"))
    for source in (
        tracking.get("chart_region"),
        tracking.get("display_region"),
        snapshot.get("chart_region"),
        snapshot.get("plot_area"),
    ):
        row = _mapping(source)
        bounds = _bbox(row.get("pixel_bbox") or row.get("bbox") or row.get("normalized_bbox") or row)
        if len(bounds) >= 4 and bounds[3] > bounds[1]:
            return max(1.0, bounds[3] - bounds[1])
        if _float(row.get("height"), 0.0) > 0:
            return max(1.0, _float(row.get("height"), 1.0))
    return 1.0


def _latest_price_y(snapshot: Mapping[str, Any]) -> tuple[float | None, str]:
    tracking = _mapping(snapshot.get("tracking_summary"))
    signal = _mapping(snapshot.get("latest_signal"))
    timing = _mapping(snapshot.get("execution_timing") or snapshot.get("timing") or signal.get("execution_timing") or tracking.get("execution_timing"))
    price_position = _mapping(timing.get("price_position"))
    for key in ("latest_price_y", "current_price_y", "latest_y", "current_y", "price_y"):
        if key in price_position:
            parsed = _float(price_position.get(key), float("nan"))
            if parsed == parsed:
                return parsed, f"execution_timing.price_position.{key}"
    for source_name, source in (
        ("snapshot.current_box", snapshot.get("current_box")),
        ("tracking.current_box", tracking.get("current_box")),
        ("signal.current_box", signal.get("current_box")),
    ):
        y = _center_y(source)
        if y is not None:
            return y, source_name
    candles = _first_rows(
        tracking.get("tracked_candles"),
        signal.get("tracked_candles"),
        tracking.get("visible_candles"),
        signal.get("visible_candles"),
    )
    if candles:
        latest = max(candles, key=_right_edge)
        y = _center_y(latest)
        if y is not None:
            return y, "tracked_candles.latest"
    boxes = _first_rows(
        snapshot.get("structure_boxes"),
        snapshot.get("historical_structure"),
        tracking.get("structure_boxes"),
        tracking.get("historical_structure"),
    )
    if boxes:
        latest_box = max(boxes, key=_right_edge)
        y = _center_y(latest_box)
        if y is not None:
            return y, "structure_boxes.latest"
    return None, "unavailable"


def _sequence_context_from_payloads(
    snapshot: Mapping[str, Any],
    result: Mapping[str, Any],
    study_packet: Mapping[str, Any],
    execution_packet: Mapping[str, Any],
) -> dict[str, Any]:
    for candidate in (
        _mapping(_mapping(result.get("model_council")).get("sequence_context")),
        _mapping(_mapping(study_packet.get("model_council")).get("sequence_context")),
        _mapping(_mapping(execution_packet.get("model_council")).get("sequence_context")),
        _mapping(snapshot.get("sequence_context")),
        _mapping(snapshot.get("sequence_context_v3")),
    ):
        if candidate:
            return candidate
    return {}


def _zone_rows(
    name: str,
    snapshot: Mapping[str, Any],
    result: Mapping[str, Any],
    sequence_context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    tracking = _mapping(snapshot.get("tracking_summary"))
    signal = _mapping(snapshot.get("latest_signal"))
    if name == "entry":
        timing = _mapping(snapshot.get("execution_timing") or snapshot.get("timing") or signal.get("execution_timing") or tracking.get("execution_timing"))
        rows = _first_rows(
            sequence_context.get("sniper_zones"),
            sequence_context.get("entry_zones"),
            timing.get("entry_area_zone") if isinstance(timing.get("entry_area_zone"), Sequence) else [],
        )
        entry_area_zone = _mapping(timing.get("entry_area_zone"))
        if rows:
            return rows
        return [entry_area_zone] if entry_area_zone else []
    if name == "target":
        return _first_rows(
            sequence_context.get("target_zones"),
            _mapping(result.get("ideal_trade_path")).get("target_zones"),
            tracking.get("target_zones"),
            signal.get("target_zones"),
        )
    if name == "invalidation":
        return _first_rows(
            sequence_context.get("invalidation_zones"),
            _mapping(result.get("ideal_trade_path")).get("invalidation_zones"),
            tracking.get("invalidation_zones"),
            signal.get("invalidation_zones"),
        )
    return []


def _zone_payload(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    first = dict(rows[0])
    box = _bbox(first)
    if box:
        first["bbox"] = [round(value, 3) for value in box]
        first["center_y"] = round((box[1] + box[3]) * 0.5, 3)
    return first


def _distance_norm(side: str, start_y: float | None, end_y: float | None, height: float) -> float:
    if start_y is None or end_y is None:
        return 0.0
    raw = (start_y - end_y) if side == "BUY" else (end_y - start_y)
    return round(raw / max(1.0, height), 4)


def _candidate_from_payloads(
    snapshot: Mapping[str, Any],
    result: Mapping[str, Any],
    study_packet: Mapping[str, Any],
    execution_packet: Mapping[str, Any],
) -> dict[str, Any]:
    council = _mapping(result.get("model_council"))
    execution = _mapping(result.get("execution"))
    trace = _mapping(result.get("promotion_trace") or council.get("promotion_trace"))
    market_context = _mapping(result.get("market_context") or snapshot.get("market_context"))
    signal = _mapping(snapshot.get("latest_signal"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    timing = _first_mapping(
        result.get("timing_decision"),
        result.get("execution_timing"),
        snapshot.get("execution_timing"),
        snapshot.get("timing"),
        signal.get("execution_timing"),
        tracking.get("execution_timing"),
    )
    side = _side(
        execution.get("side")
        or council.get("final_side")
        or trace.get("candidate_side")
        or study_packet.get("side")
        or _mapping(study_packet.get("execution")).get("side")
        or execution_packet.get("side")
        or snapshot.get("candidate_side")
        or snapshot.get("side")
        or signal.get("execution_action")
        or signal.get("side")
    )
    state = _first_text(
        council.get("final_state"),
        execution.get("state"),
        result.get("release_state"),
        trace.get("promotion_result"),
        "WATCHING",
    ).upper()
    side_score = max(
        _clip01(council.get("buy_score") if side == "BUY" else council.get("sell_score")),
        _clip01(result.get("final_score")),
        _clip01(result.get("final_execution_score")),
        _clip01(council.get("final_score")),
        _clip01(snapshot.get("confidence")),
        _clip01(signal.get("effective_confidence")),
        _clip01(signal.get("confidence")),
    )
    symbol = _first_text(
        result.get("symbol"),
        study_packet.get("symbol"),
        execution_packet.get("symbol"),
        snapshot.get("symbol"),
        snapshot.get("market"),
        signal.get("market"),
        tracking.get("detected_market"),
    )
    timeframe = _first_text(
        result.get("timeframe"),
        study_packet.get("timeframe"),
        execution_packet.get("timeframe"),
        snapshot.get("timeframe"),
        snapshot.get("focus_timeframe"),
        signal.get("focus_timeframe"),
        tracking.get("detected_timeframe"),
    ).upper()
    frame_id = _int(snapshot.get("frame_id") or snapshot.get("tracker_frame_id") or study_packet.get("frame_id") or execution_packet.get("frame_id"), 0)
    candidate_id = _first_text(
        council.get("candidate_id"),
        trace.get("candidate_id"),
        study_packet.get("candidate_id"),
        execution_packet.get("candidate_id"),
    )
    if not candidate_id:
        candidate_id = _stable_hash(
            {
                "session_id": snapshot.get("session_id"),
                "symbol": symbol,
                "timeframe": timeframe,
                "side": side,
                "lane": council.get("selected_lane") or result.get("selected_execution_lane"),
            },
            prefix="pgcand_",
        )
    return {
        "side": side,
        "state": state,
        "score": round(float(side_score), 4),
        "symbol": symbol,
        "generic_symbol": _symbol_is_generic(symbol),
        "timeframe": timeframe,
        "frame_id": frame_id,
        "candidate_id": candidate_id,
        "packet_id": _first_text(execution_packet.get("packet_id"), result.get("packet_id"), study_packet.get("packet_id")),
        "lane": _first_text(
            council.get("selected_lane"),
            council.get("selected_execution_lane"),
            result.get("selected_lane"),
            result.get("selected_execution_lane"),
            study_packet.get("selected_lane"),
        ),
        "candidate_stage": _first_text(council.get("candidate_stage"), trace.get("candidate_stage"), study_packet.get("candidate_stage")),
        "next_required": _first_text(council.get("next_required"), trace.get("next_required"), study_packet.get("next_required")),
        "reason": _first_text(council.get("arbitration_reason"), result.get("reason"), study_packet.get("reason")),
        "confirmed_reversal": bool(
            snapshot.get("confirmed_reversal")
            or snapshot.get("previous_side_invalidated")
            or snapshot.get("candidate_invalidated")
            or trace.get("candidate_invalidated")
        ),
        "entry_now_allowed": bool(timing.get("entry_now_allowed") or timing.get("entry_allowed")),
        "execution_enabled": bool(execution.get("enabled") or execution_packet),
    }


def _start_allowed(candidate: Mapping[str, Any]) -> bool:
    side = _side(candidate.get("side"))
    if side not in {"BUY", "SELL"}:
        return False
    score = _clip01(candidate.get("score"))
    state = str(candidate.get("state") or "").upper()
    stage = str(candidate.get("candidate_stage") or "").upper()
    if bool(candidate.get("execution_enabled")):
        return True
    if state in {"EXECUTABLE", "PREPARING", "BUY_PREPARING", "SELL_PREPARING"} and score >= 0.52:
        return True
    if stage not in {"", "OBSERVATION", "CANDIDATE_CREATED"} and score >= 0.58:
        return True
    return state in {"WATCHING", "BLOCKED_BY_RUNTIME"} and score >= 0.68 and bool(candidate.get("lane"))


def _append_event(previous: Mapping[str, Any], event: Mapping[str, Any]) -> list[dict[str, Any]]:
    history = _rows(previous.get("history"))
    row = {key: value for key, value in event.items() if value not in (None, "", [], {})}
    if row:
        history.append(row)
    return history[-16:]


def _base_no_active(
    *,
    snapshot: Mapping[str, Any],
    candidate: Mapping[str, Any],
    now_epoch: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": SIGNAL_THESIS_SCHEMA_VERSION,
        "active": False,
        "status": "NO_ACTIVE_THESIS",
        "room_state": "NO_ACTIVE_THESIS",
        "session_id": str(snapshot.get("session_id") or ""),
        "symbol": str(candidate.get("symbol") or snapshot.get("symbol") or snapshot.get("market") or ""),
        "timeframe": str(candidate.get("timeframe") or snapshot.get("timeframe") or ""),
        "side": "HOLD",
        "effective_side": "HOLD",
        "raw_read_side": _side(candidate.get("side")),
        "countertrend_blocked": False,
        "reason": reason,
        "updated_epoch": float(now_epoch),
        "source_module": "signal_thesis_tracker",
    }


def _new_thesis(
    *,
    snapshot: Mapping[str, Any],
    result: Mapping[str, Any],
    study_packet: Mapping[str, Any],
    execution_packet: Mapping[str, Any],
    candidate: Mapping[str, Any],
    now_epoch: float,
) -> dict[str, Any]:
    sequence_context = _sequence_context_from_payloads(snapshot, result, study_packet, execution_packet)
    current_y, current_source = _latest_price_y(snapshot)
    height = _chart_height(snapshot)
    entry_zone = _zone_payload(_zone_rows("entry", snapshot, result, sequence_context))
    target_zone = _zone_payload(_zone_rows("target", snapshot, result, sequence_context))
    invalidation_zone = _zone_payload(_zone_rows("invalidation", snapshot, result, sequence_context))
    entry_y = _center_y(entry_zone) if entry_zone else current_y
    if entry_y is None:
        entry_y = current_y
    target_y = _center_y(target_zone)
    invalidation_y = _center_y(invalidation_zone)
    side = _side(candidate.get("side"))
    target_distance = _distance_norm(side, entry_y, target_y, height)
    invalidation_distance = -_distance_norm(side, entry_y, invalidation_y, height)
    thesis_id = _stable_hash(
        {
            "session_id": snapshot.get("session_id"),
            "symbol": candidate.get("symbol"),
            "timeframe": candidate.get("timeframe"),
            "side": side,
            "candidate_id": candidate.get("candidate_id"),
        },
        prefix="pgthesis_",
    )
    created_frame = _int(candidate.get("frame_id"), 0)
    status = "TRACKING"
    return {
        "schema_version": SIGNAL_THESIS_SCHEMA_VERSION,
        "active": True,
        "status": status,
        "room_state": "ALLOW_PULLBACK",
        "thesis_id": thesis_id,
        "session_id": str(snapshot.get("session_id") or ""),
        "symbol": str(candidate.get("symbol") or ""),
        "symbol_key": _normalized_symbol(candidate.get("symbol")),
        "timeframe": str(candidate.get("timeframe") or ""),
        "side": side,
        "effective_side": side,
        "raw_read_side": side,
        "current_signal_side": side,
        "opposite_side": _opposite(side),
        "confidence": _clip01(candidate.get("score")),
        "source_candidate_id": str(candidate.get("candidate_id") or ""),
        "source_packet_id": str(candidate.get("packet_id") or ""),
        "selected_lane": str(candidate.get("lane") or ""),
        "created_epoch": float(now_epoch),
        "updated_epoch": float(now_epoch),
        "age_sec": 0.0,
        "entry_frame_id": created_frame,
        "last_frame_id": created_frame,
        "entry_price_proxy": round(float(entry_y), 4) if entry_y is not None else None,
        "current_price_proxy": round(float(current_y), 4) if current_y is not None else None,
        "price_proxy_source": current_source,
        "chart_height_proxy": round(float(height), 4),
        "entry_zone": entry_zone,
        "target_zone": target_zone,
        "invalidation_zone": invalidation_zone,
        "target_distance_norm": round(max(0.0, target_distance), 4) if target_y is not None else 0.0,
        "invalidation_distance_norm": round(max(0.0, invalidation_distance), 4) if invalidation_y is not None else 0.0,
        "move_progress_norm": 0.0,
        "unrealized_progress_norm": 0.0,
        "max_favorable_excursion_norm": 0.0,
        "max_adverse_excursion_norm": 0.0,
        "target_reached": False,
        "invalidated": False,
        "invalidation_reason": "",
        "countertrend_blocked": True,
        "countertrend_attempt_blocked": False,
        "blocked_countertrend_side": _opposite(side),
        "countertrend_policy": "BLOCK_OPPOSITE_EXECUTION_UNTIL_INVALIDATION",
        "release_condition": "pair switch, target completion, or invalidation/reversal confirmation",
        "plain_language": (
            f"Tracking the active {side} idea from frame {created_frame}. "
            f"The opposite {_opposite(side)} side is blocked until invalidation is confirmed."
        ),
        "history": [
            {
                "event": "THESIS_STARTED",
                "epoch": float(now_epoch),
                "frame_id": created_frame,
                "side": side,
                "score": round(_clip01(candidate.get("score")), 4),
                "state": str(candidate.get("state") or ""),
            }
        ],
        "source_module": "signal_thesis_tracker",
    }


def _pair_switched(previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    previous_symbol = _normalized_symbol(previous.get("symbol") or previous.get("symbol_key"))
    current_symbol = _normalized_symbol(candidate.get("symbol"))
    if _symbol_is_generic(previous_symbol) or _symbol_is_generic(current_symbol):
        return False
    return previous_symbol != current_symbol


def _timeframe_switched(previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    previous_timeframe = str(previous.get("timeframe") or "").strip().upper()
    current_timeframe = str(candidate.get("timeframe") or "").strip().upper()
    if not previous_timeframe or not current_timeframe:
        return False
    return previous_timeframe != current_timeframe


def _is_active(previous: Mapping[str, Any]) -> bool:
    return bool(previous.get("active")) and str(previous.get("status") or "").upper() not in _TERMINAL_STATES


def _update_active_thesis(
    previous: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    result: Mapping[str, Any],
    study_packet: Mapping[str, Any],
    execution_packet: Mapping[str, Any],
    candidate: Mapping[str, Any],
    now_epoch: float,
) -> dict[str, Any]:
    side = _side(previous.get("side"))
    current_side = _side(candidate.get("side"))
    current_y, current_source = _latest_price_y(snapshot)
    height = max(1.0, _float(previous.get("chart_height_proxy"), _chart_height(snapshot)))
    entry_y = _float(previous.get("entry_price_proxy"), float("nan"))
    if entry_y != entry_y:
        entry_y = current_y if current_y is not None else 0.0
    progress_norm = _distance_norm(side, entry_y, current_y, height)
    favorable = max(0.0, progress_norm)
    adverse = max(0.0, -progress_norm)
    target_distance = _float(previous.get("target_distance_norm"), 0.0)
    invalidation_distance = _float(previous.get("invalidation_distance_norm"), 0.0)
    previous_mfe = _float(previous.get("max_favorable_excursion_norm"), 0.0)
    previous_mae = _float(previous.get("max_adverse_excursion_norm"), 0.0)
    max_favorable = max(previous_mfe, favorable)
    max_adverse = max(previous_mae, adverse)
    opposite_read = current_side == _opposite(side)
    same_read = current_side == side
    score = _clip01(candidate.get("score"))
    explicit_invalidated = bool(candidate.get("confirmed_reversal"))
    invalidation_threshold = max(0.075, invalidation_distance * 1.05 if invalidation_distance > 0 else 0.12)
    zone_breached = bool(max_adverse >= invalidation_threshold and invalidation_distance > 0)
    candidate_state = str(candidate.get("state") or "").upper()
    candidate_stage = str(candidate.get("candidate_stage") or "").upper()
    executable_like = bool(
        candidate.get("execution_enabled")
        or candidate.get("entry_now_allowed")
        or candidate_state in {"PREPARING", "EXECUTABLE", "BUY_PREPARING", "SELL_PREPARING"}
        or candidate_stage not in {"", "OBSERVATION", "CANDIDATE_CREATED"}
    )
    reversal_confirmed = bool(opposite_read and score >= 0.72 and executable_like)
    previous_symbol_generic = _symbol_is_generic(previous.get("symbol") or previous.get("symbol_key"))
    current_symbol_generic = bool(candidate.get("generic_symbol"))
    generic_locked_flip = bool(
        (previous_symbol_generic or current_symbol_generic)
        and opposite_read
        and score >= 0.82
        and executable_like
        and max_adverse >= 0.12
    )
    fallback_breach = bool(
        invalidation_distance <= 0
        and opposite_read
        and score >= 0.82
        and executable_like
        and max_adverse >= 0.16
    )
    invalidated = bool(
        explicit_invalidated
        or (zone_breached and reversal_confirmed)
        or generic_locked_flip
        or fallback_breach
    )
    target_reached = bool(target_distance > 0 and max_favorable >= max(0.04, target_distance * 0.92))
    opposite_attempt_blocked = bool(opposite_read and not invalidated)
    if invalidated:
        status = "INVALIDATED"
        room_state = "INVALIDATED"
        if generic_locked_flip:
            reason = (
                "Active thesis reset because the chart identity was generic and a strong opposite executable "
                "read appeared. This usually means the pair changed or the old thesis is invalidated."
            )
        elif fallback_breach:
            reason = "Active thesis invalidated by a strong opposite executable read after price moved beyond the allowed room."
        elif zone_breached and reversal_confirmed:
            reason = "Active thesis invalidated by confirmed opposite read and invalidation-zone breach."
        else:
            reason = "Active thesis invalidated by explicit reversal/invalidation evidence."
    elif target_reached:
        status = "TARGET_REACHED"
        room_state = "PROTECT_WIN"
        reason = "Target distance has been reached; protect the winning idea and wait for a new valid setup."
    elif max_favorable >= max(0.08, target_distance * 0.40 if target_distance > 0 else 0.12):
        status = "PROTECT_WIN"
        room_state = "PROTECT_WIN"
        reason = "Move is materially in profit; opposite reads are watch-only until real reversal evidence appears."
    elif adverse > 0.0:
        status = "ALLOW_PULLBACK"
        room_state = "ALLOW_PULLBACK"
        reason = "Pullback against the active thesis is inside the allowed room."
    else:
        status = "TRACKING"
        room_state = "TRACKING" if same_read else "ALLOW_PULLBACK"
        reason = "Active thesis remains aligned with the live read." if same_read else "Active thesis remains valid while the current read is not confirmed."
    countertrend_blocked = bool(status in _ACTIVE_STATES and side in {"BUY", "SELL"})
    updated = dict(previous)
    updated.update(
        {
            "schema_version": SIGNAL_THESIS_SCHEMA_VERSION,
            "active": status in _ACTIVE_STATES,
            "status": status,
            "room_state": room_state,
            "effective_side": side if status != "INVALIDATED" else "HOLD",
            "raw_read_side": current_side,
            "current_signal_side": current_side,
            "confidence": max(_clip01(previous.get("confidence")), score if same_read else 0.0),
            "updated_epoch": float(now_epoch),
            "age_sec": round(max(0.0, float(now_epoch) - _float(previous.get("created_epoch"), now_epoch)), 3),
            "last_frame_id": _int(candidate.get("frame_id"), _int(previous.get("last_frame_id"), 0)),
            "current_price_proxy": round(float(current_y), 4) if current_y is not None else previous.get("current_price_proxy"),
            "price_proxy_source": current_source,
            "move_progress_norm": round(float(progress_norm), 4),
            "unrealized_progress_norm": round(float(progress_norm), 4),
            "max_favorable_excursion_norm": round(float(max_favorable), 4),
            "max_adverse_excursion_norm": round(float(max_adverse), 4),
            "target_reached": target_reached,
            "invalidated": invalidated,
            "invalidation_reason": reason if invalidated else "",
            "countertrend_blocked": countertrend_blocked,
            "countertrend_attempt_blocked": opposite_attempt_blocked,
            "blocked_countertrend_side": _opposite(side) if countertrend_blocked else "HOLD",
            "countertrend_policy": (
                "BLOCK_OPPOSITE_EXECUTION_UNTIL_INVALIDATION"
                if countertrend_blocked
                else "TRACK_SAME_SIDE_OR_WAIT"
            ),
            "release_condition": (
                "confirmed invalidation or pair/timeframe switch"
                if countertrend_blocked
                else "pair switch, target completion, or invalidation/reversal confirmation"
            ),
            "plain_language": (
                f"The original {side} thesis is still active; current {current_side} is countertrend/watch-only until invalidation."
                if opposite_attempt_blocked
                else f"Active {side} thesis remains valid; opposite {_opposite(side)} execution is blocked until invalidation."
                if countertrend_blocked
                else reason
            ),
            "source_module": "signal_thesis_tracker",
        }
    )
    if generic_locked_flip:
        updated["pair_switch_suspected"] = True
        updated["reset_reason"] = "generic_locked_chart_opposite_executable"
    if invalidated and opposite_read and _start_allowed(candidate):
        updated["replaced_by"] = _new_thesis(
            snapshot=snapshot,
            result=result,
            study_packet=study_packet,
            execution_packet=execution_packet,
            candidate=candidate,
            now_epoch=now_epoch,
        )
    updated["history"] = _append_event(
        previous,
        {
            "event": "THESIS_INVALIDATED" if invalidated else "COUNTERTREND_BLOCKED" if opposite_attempt_blocked else "THESIS_UPDATED",
            "epoch": float(now_epoch),
            "frame_id": updated["last_frame_id"],
            "side": side,
            "raw_read_side": current_side,
            "progress_norm": updated["move_progress_norm"],
            "mfe": updated["max_favorable_excursion_norm"],
            "mae": updated["max_adverse_excursion_norm"],
            "reason": reason,
        },
    )
    return updated


def update_signal_thesis_v3(
    previous: Mapping[str, Any] | None,
    *,
    snapshot: Mapping[str, Any],
    model_council_result: Mapping[str, Any],
    execution_packet: Mapping[str, Any] | None = None,
    study_packet: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Track the active trade idea separately from the latest frame read.

    The council is allowed to produce a fresh BUY/SELL read every frame. This
    reducer keeps the currently tracked trade thesis alive through normal
    pullbacks and blocks opposite-side execution until the old thesis is
    genuinely invalidated or the instrument changes.
    """

    now_value = float(now_epoch if now_epoch is not None else _float(snapshot.get("now_epoch"), 0.0))
    result = _mapping(model_council_result)
    packet = _mapping(execution_packet)
    study = _mapping(study_packet)
    candidate = _candidate_from_payloads(snapshot, result, study, packet)
    previous_payload = _mapping(previous)
    if _is_active(previous_payload) and (_pair_switched(previous_payload, candidate) or _timeframe_switched(previous_payload, candidate)):
        reset_reason = "pair switched" if _pair_switched(previous_payload, candidate) else "timeframe switched"
        reset = dict(previous_payload)
        reset.update(
            {
                "active": False,
                "status": "PAIR_SWITCH_RESET",
                "room_state": "PAIR_SWITCH_RESET",
                "effective_side": "HOLD",
                "raw_read_side": _side(candidate.get("side")),
                "countertrend_blocked": False,
                "invalidated": True,
                "invalidation_reason": reset_reason,
                "updated_epoch": now_value,
                "plain_language": f"Previous thesis was reset because the {reset_reason}.",
                "source_module": "signal_thesis_tracker",
            }
        )
        reset["history"] = _append_event(
            previous_payload,
            {
                "event": "PAIR_SWITCH_RESET",
                "epoch": now_value,
                "from_symbol": previous_payload.get("symbol"),
                "to_symbol": candidate.get("symbol"),
                "from_timeframe": previous_payload.get("timeframe"),
                "to_timeframe": candidate.get("timeframe"),
            },
        )
        if _start_allowed(candidate):
            reset["replaced_by"] = _new_thesis(
                snapshot=snapshot,
                result=result,
                study_packet=study,
                execution_packet=packet,
                candidate=candidate,
                now_epoch=now_value,
            )
        return reset

    if _is_active(previous_payload):
        updated = _update_active_thesis(
            previous_payload,
            snapshot=snapshot,
            result=result,
            study_packet=study,
            execution_packet=packet,
            candidate=candidate,
            now_epoch=now_value,
        )
        if updated.get("active"):
            return updated
        return updated

    if _start_allowed(candidate):
        return _new_thesis(
            snapshot=snapshot,
            result=result,
            study_packet=study,
            execution_packet=packet,
            candidate=candidate,
            now_epoch=now_value,
        )
    return _base_no_active(
        snapshot=snapshot,
        candidate=candidate,
        now_epoch=now_value,
        reason="No council side has enough maturity to start a durable thesis yet.",
    )


def thesis_blocks_countertrend(thesis: Mapping[str, Any], packet_or_result: Mapping[str, Any]) -> bool:
    """Return true when a packet/result is trying to execute against an active thesis."""

    row = _mapping(packet_or_result)
    if not bool(thesis.get("countertrend_blocked")):
        return False
    thesis_side = _side(thesis.get("side"))
    attempted_side = _side(
        row.get("side")
        or _mapping(row.get("execution")).get("side")
        or _mapping(row.get("model_council")).get("final_side")
        or row.get("final_side")
    )
    return bool(thesis_side in {"BUY", "SELL"} and attempted_side == _opposite(thesis_side))
