from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping, Optional


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class FloatingStateV2:
    session_id: str
    mode: str
    timestamp: float
    state_chip: str
    packet: dict[str, Any] = field(default_factory=dict)
    council: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    instrument: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, Any] = field(default_factory=dict)
    shooter: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    inspector: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, state: Mapping[str, Any]) -> "FloatingStateV2":
        return cls(
            session_id=_text(state.get("session_id"), "session"),
            mode=_text(state.get("mode"), "LIVE").upper(),
            timestamp=float(_number(state.get("timestamp")) or time.time()),
            state_chip=_text(state.get("state_chip"), "WAITING").upper(),
            packet=dict(_mapping(state.get("packet"))),
            council=dict(_mapping(state.get("council"))),
            timing=dict(_mapping(state.get("timing"))),
            instrument=dict(_mapping(state.get("instrument"))),
            scores=dict(_mapping(state.get("scores"))),
            shooter=dict(_mapping(state.get("shooter"))),
            health=dict(_mapping(state.get("health"))),
            inspector=dict(_mapping(state.get("inspector"))),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "FloatingStateV2",
            "session_id": self.session_id,
            "mode": self.mode,
            "timestamp": self.timestamp,
            "state_chip": self.state_chip,
            "packet": self.packet,
            "council": self.council,
            "timing": self.timing,
            "instrument": self.instrument,
            "scores": self.scores,
            "shooter": self.shooter,
            "health": self.health,
            "inspector": self.inspector,
        }


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text and text.lower() not in {"n/a", "none", "null"} else fallback


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _side(value: Any) -> str:
    text = _text(value).upper()
    if text.startswith("BUY") or text.startswith("BULL"):
        return "BUY"
    if text.startswith("SELL") or text.startswith("BEAR"):
        return "SELL"
    return ""


def _short_id(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return text[-8:] if len(text) > 8 else text


def _display_council_state(state: Any) -> str:
    """Shorten council state names for UI display to prevent overflow."""
    text = _text(state).upper()
    
    # Map long state names to short display names
    display_map = {
        "BUY_OBSERVATION": "BUY.OBS",
        "SELL_OBSERVATION": "SELL.OBS",
        "BUY_HYPOTHESIS": "BUY.HYP",
        "SELL_HYPOTHESIS": "SELL.HYP",
        "BUY_CONTEXT_CONFIRMED": "BUY.CTX",
        "SELL_CONTEXT_CONFIRMED": "SELL.CTX",
        "BUY_ZONE_QUALIFIED": "BUY.ZONE",
        "SELL_ZONE_QUALIFIED": "SELL.ZONE",
        "BUY_TIMING_READY": "BUY.TIME",
        "SELL_TIMING_READY": "SELL.TIME",
        "BUY_PREPARING": "BUY.PREP",
        "SELL_PREPARING": "SELL.PREP",
        "BUY_EXECUTABLE": "BUY.EXEC",
        "SELL_EXECUTABLE": "SELL.EXEC",
        "CONFLICT": "CONFLICT",
        "WATCHING": "WATCH",
        "OBSERVING": "OBS",
        "COOLDOWN": "COOL",
        "BLOCKED_BY_MARKET": "BLK.MKT",
        "BLOCKED_BY_RUNTIME": "BLK.RUN",
        "NO_SETUP": "NONE",
    }
    
    return display_map.get(text, text[:12])  # Fallback to first 12 chars


def _latency(payload: Mapping[str, Any]) -> Optional[float]:
    for key in (
        "_fetch_latency_sec",
        "fetch_latency_sec",
        "age_sec",
        "age_seconds",
        "latency_sec",
        "latency_seconds",
        "signal_age_sec",
        "signal_age_seconds",
    ):
        value = _number(payload.get(key))
        if value is not None:
            return max(0.0, value)
    timestamp = _number(payload.get("timestamp") or payload.get("timestamp_epoch") or payload.get("created_at_epoch"))
    if timestamp and timestamp > 1_000_000_000:
        return max(0.0, time.time() - timestamp)
    return None


def _short_reason(value: Any) -> str:
    text = _text(value)
    if not text:
        return "Waiting for packet"
    upper = text.upper()
    replacements = (
        ("WAITING_FOR_EXECUTABLE_MODEL_COUNCIL_PACKET", "Waiting for executable packet"),
        ("SNIPER_ZONE_ENTRY_STRUCTURE_NOT_READY", "Sniper structure not ready"),
        ("SNIPER_ZONE_ENTRY_STRUCTURE_NOT_ACCEPTED", "Structure not accepted"),
        ("LOCAL_BREAKDOWN_CONTINUATION_STRUCTURE_NOT_READY", "Breakdown not ready"),
        ("FAILED_RETEST_ENTRY_STRUCTURE_NOT_READY", "Retest not ready"),
        ("WAVE_RIDING_CONTINUATION_STRUCTURE_NOT_READY", "Wave not ready"),
        ("MOMENTUM_ACCEPTANCE_ENTRY_STRUCTURE_NOT_READY", "Momentum not ready"),
        ("HISTORY_MATCHED_CONTINUATION_STRUCTURE_NOT_READY", "History match not ready"),
        ("WAVE_CONTEXT_NOT_READY", "Wave not ready"),
        ("OPPOSING_FORCE_DECISION_UNRESOLVED", "Opposing force unresolved"),
        ("LOCAL_WAVE_AGAINST_ENTRY", "Local wave against entry"),
        ("BUY_LOW_SELL_HIGH_LOCATION_NOT_READY", "Entry location not ready"),
        ("WAIT_FOR_PULLBACK", "Wait for pullback"),
        ("WAIT_FOR_RETEST", "Wait for retest"),
        ("WAIT_FOR_BREAK_CONFIRMATION", "Wait for break confirmation"),
        ("WAIT_FOR_CANDLE_CLOSE_BEHAVIOUR", "Wait for candle close"),
        ("SKIP_LATE_ENTRY", "Skip late entry"),
        ("ENTER_NOW", "Enter now"),
        ("DIRECT_CONTINUATION", "Direct continuation"),
        ("PULLBACK_THEN_CONTINUATION", "Pullback then continuation"),
        ("ADVERSE_FIRST_THEN_TARGET", "Adverse first"),
        ("FAKEOUT_THEN_DIRECTION", "Fakeout then direction"),
        ("LATE_CHASE_REVERSAL_RISK", "Late chase risk"),
        ("OPPOSING_FORCE_FIRST", "Opposing force first"),
        ("PATH_RISK_OR_OPPOSING_FORCE", "Path risk active"),
        ("TRAP_ACTIVE", "Trap active"),
        ("LATE_CHASE_STEEP_IMPULSE", "Late chase risk"),
        ("TRADE_DISCIPLINE_LOCKED_5_TRADES_20_MINUTES", "Trade discipline cooldown"),
        ("NO_EXECUTION_LANE_ACCEPTED", "No execution lane accepted"),
        ("FLIP_FLOP_CONTAINED", "Candidate containment"),
        ("TIMING_READY", "Timing not ready"),
        ("SCORE_BELOW_THRESHOLD", "Score below threshold"),
        ("CANDIDATE_NOT_STABLE", "Candidate not stable"),
        ("SAME CANDIDATE SIDE FOR", "Need stable candidate"),
    )
    for needle, replacement in replacements:
        if needle in upper:
            return replacement
    cleaned = text.replace("_", " ").replace(";", " | ")
    cleaned = " ".join(cleaned.split())
    return cleaned[:58]


def _lane_short(value: Any) -> str:
    text = _text(value).upper()
    if not text:
        return "LANE PENDING"
    mapping = {
        "SNIPER_ZONE_ENTRY": "SNIPER",
        "LOCAL_BREAKDOWN_CONTINUATION": "BREAKDOWN",
        "FAILED_RETEST_ENTRY": "RETEST",
        "WAVE_RIDING_CONTINUATION": "WAVE",
        "MOMENTUM_ACCEPTANCE_ENTRY": "MOMENTUM",
        "HISTORY_MATCHED_CONTINUATION": "HISTORY",
    }
    for key, short in mapping.items():
        if key in text:
            return short
    return text.replace("_", " ")[:18]


def _packet_type(payload: Mapping[str, Any]) -> str:
    raw = _text(payload.get("packet_type") or payload.get("schema_version")).upper()
    if "PG_EXECUTION_PACKET_V3" in raw:
        return "EXECUTABLE"
    if "STUDY" in raw:
        return "STUDY"
    if payload.get("execution") and _mapping(payload.get("execution")).get("enabled") is True:
        return "EXECUTABLE"
    if payload:
        return "STUDY"
    return "WAITING"


def _extract_packet_payload(signal_payload: Optional[Mapping[str, Any]], tracker_payload: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if isinstance(signal_payload, Mapping) and signal_payload:
        return signal_payload
    tracker = _mapping(tracker_payload)
    packet = tracker.get("model_council_study_packet")
    if isinstance(packet, Mapping):
        return packet
    result = _mapping(tracker.get("model_council_result"))
    packet = result.get("study_packet")
    if isinstance(packet, Mapping):
        return packet
    return {}


def _extract_scores(payload: Mapping[str, Any], council: Mapping[str, Any], promotion: Mapping[str, Any]) -> dict[str, Optional[float]]:
    scores_source = _mapping(
        payload.get("scores")
        or council.get("scores")
        or council.get("council_scores")
        or promotion.get("scores")
        or promotion.get("council_scores")
    )

    def pick(*keys: str) -> Optional[float]:
        for key in keys:
            value = _number(scores_source.get(key))
            if value is not None:
                return value
            value = _number(council.get(key))
            if value is not None:
                return value
            value = _number(promotion.get(key))
            if value is not None:
                return value
            value = _number(payload.get(key))
            if value is not None:
                return value
        return None

    return {
        "global": pick("global", "global_score"),
        "local": pick("local", "local_score"),
        "zone": pick("zone", "zone_score"),
        "angle": pick("angle", "angle_score"),
        "history": pick("history", "history_score"),
        "risk": pick("risk", "risk_score"),
        "arbitration": pick("arbitration", "arbitration_score"),
    }


def _action_label(action_payload: Optional[Mapping[str, Any]], packet_type: str) -> tuple[str, str]:
    action = _mapping(action_payload)
    if action:
        phase = _text(action.get("phase") or action.get("state"))
        step = _text(action.get("step") or action.get("target"))
        result = _text(action.get("result") or action.get("overall"))
        if result and result not in {"PASS"}:
            return "ACTION", _short_reason(result)
        if phase or step:
            words = (phase or step).replace("_", " ").title()
            if "TIME" in (phase or step).upper():
                return "ACTION", words[:44]
            if "SIDE_CLICK" in (phase or step).upper():
                return "ACTION", "Ready for side click"
            return "ACTION", words[:44]
    if packet_type == "EXECUTABLE":
        return "READY", "Waiting for action sequence"
    return "WAITING", "Waiting for executable packet"


def _models_awake(tracker: Mapping[str, Any]) -> tuple[int, int]:
    health = _mapping(tracker.get("model_health") or tracker.get("model_uptime") or tracker.get("health"))
    awake = _number(health.get("models_awake") or health.get("awake"))
    total = _number(health.get("models_total") or health.get("total"))
    if awake is not None and total is not None:
        return int(awake), int(total)
    roles = _sequence(tracker.get("model_council_roles") or tracker.get("roles"))
    if roles:
        awake_count = sum(1 for role in roles if _text(_mapping(role).get("state")).upper() in {"AWAKE", "RUNNING", "READY"})
        return awake_count, len(roles)
    return 0, 0


def _models_awake_from_runtime_health(health_payload: Mapping[str, Any]) -> tuple[int, int]:
    health = _mapping(health_payload)
    models = _sequence(health.get("models"))
    if models:
        awake_count = sum(1 for model in models if _text(_mapping(model).get("status")).upper() in {"AWAKE", "RUNNING", "READY"})
        return awake_count, len(models)
    required_roles = _sequence(health.get("required_roles"))
    if health.get("all_required_models_awake") is True and required_roles:
        return len(required_roles), len(required_roles)
    if health.get("all_required_models_awake") is True:
        return 7, 7
    return 0, 0


def build_floating_state(
    *,
    session_id: str,
    mode: str = "LIVE",
    signal_payload: Optional[Mapping[str, Any]] = None,
    tracker_payload: Optional[Mapping[str, Any]] = None,
    action_payload: Optional[Mapping[str, Any]] = None,
    cooldown_remaining_seconds: int = 0,
) -> dict[str, Any]:
    payload = _extract_packet_payload(signal_payload, tracker_payload)
    tracker = _mapping(tracker_payload)
    execution = _mapping(payload.get("execution"))
    council = _mapping(payload.get("model_council"))
    promotion = _mapping(payload.get("promotion_trace"))
    lane_payload = _mapping(payload.get("execution_lane") or council.get("execution_lane") or promotion.get("execution_lane"))
    timing_decision = _mapping(payload.get("timing_decision") or council.get("timing_decision") or promotion.get("timing_decision"))
    timing_forecast = _mapping(payload.get("timing_forecast") or council.get("timing_forecast") or timing_decision.get("timing_forecast"))
    timing_entry = _mapping(timing_decision.get("entry_timing") or timing_forecast.get("entry_timing"))
    instrument_context = _mapping(
        payload.get("instrument_context")
        or council.get("instrument_context")
        or promotion.get("instrument_context")
        or tracker.get("instrument_context")
    )
    current_candle_phase = _mapping(payload.get("current_candle_phase") or timing_decision.get("current_candle_phase") or council.get("current_candle_phase"))
    drawdown_warning = _mapping(timing_decision.get("drawdown_first_warning") or council.get("drawdown_first_warning"))
    packet_type = _packet_type(payload)
    packet_state = _text(execution.get("state") or council.get("final_state") or council.get("state") or ("WAITING" if not payload else "WATCHING")).upper()
    side = _side(execution.get("side") or council.get("final_side") or council.get("side") or promotion.get("candidate_side") or payload.get("side"))
    lane = _text(lane_payload.get("name") or payload.get("selected_execution_lane") or council.get("selected_execution_lane") or promotion.get("selected_lane"))
    lane_accepted = lane_payload.get("accepted")
    if lane_accepted is None:
        lane_accepted = promotion.get("lane_accepted")
    lane_status = "ACCEPTED" if lane_accepted is True else "WAITING" if lane else "PENDING"
    final_score = (
        _number(council.get("final_execution_score"))
        or _number(payload.get("final_execution_score"))
        or _number(promotion.get("final_execution_score"))
        or _number(council.get("final_score"))
        or _number(payload.get("final_score"))
    )
    threshold = _number(council.get("execution_threshold") or payload.get("execution_threshold") or promotion.get("execution_threshold")) or 0.70
    score_gap = None if final_score is None else round(threshold - final_score, 4)
    next_required = _text(promotion.get("next_required") or lane_payload.get("next_required") or council.get("next_required") or payload.get("next_required"))
    reason = _text(
        promotion.get("true_blocker")
        or promotion.get("blocked_by")
        or lane_payload.get("reason")
        or council.get("reason")
        or payload.get("reason")
        or next_required
    )
    timing_mode = _text(
        timing_decision.get("timing_mode")
        or timing_entry.get("mode")
        or timing_forecast.get("best_entry_mode")
        or promotion.get("timing_mode")
    )
    path_class = _text(timing_decision.get("path_class"))
    preferred_expiry = _number(timing_decision.get("preferred_expiry_sec") or timing_forecast.get("recommended_expiry_sec") or execution.get("expiry_seconds") or payload.get("expiry_seconds"))
    reward_eta = _number(timing_decision.get("time_to_reward_sec") or timing_forecast.get("expected_time_to_target_sec") or timing_forecast.get("expected_time_to_favourable_move_sec"))
    invalidation_eta = _number(timing_decision.get("time_to_invalidation_sec") or timing_forecast.get("expected_time_to_invalidation_sec"))
    entry_now_allowed = timing_decision.get("entry_now_allowed")
    if entry_now_allowed is None:
        entry_now_allowed = timing_entry.get("mode") == "ENTER_NOW"
    timing_summary = ""
    if timing_mode or path_class:
        timing_bits = []
        if timing_mode:
            timing_bits.append(f"Timing: {_short_reason(timing_mode)}")
        if path_class:
            timing_bits.append(f"Path: {_short_reason(path_class)}")
        if reward_eta is not None:
            timing_bits.append(f"Reward ETA: {int(round(float(reward_eta))) // 60}m")
        if invalidation_eta is not None:
            timing_bits.append(f"Invalidation ETA: {max(0, int(round(float(invalidation_eta))) // 60)}m")
        timing_bits.append(f"Entry now: {'good' if bool(entry_now_allowed) else 'poor'}")
        next_hint = _text(timing_entry.get("next_condition") or timing_decision.get("reason"))
        if next_hint:
            timing_bits.append(f"Next: {_short_reason(next_hint)}")
        timing_summary = " | ".join(timing_bits)
    action_state, action_text = _action_label(action_payload, packet_type)
    latency = _latency(payload) if payload else None
    if latency is None:
        latency = _latency(tracker)
    models_awake, models_total = _models_awake(tracker)
    if models_total <= 0:
        models_awake, models_total = _models_awake_from_runtime_health(
            _mapping(payload.get("runtime_model_health") or council.get("runtime_model_health") or tracker.get("runtime_model_health"))
        )
    cache_status = _text(tracker.get("cache") or tracker.get("cache_status"), "FRESH" if tracker else "PENDING").upper()
    tracker_status = _text(tracker.get("status"), "WAITING").upper()
    instrument_state = _text(
        instrument_context.get("instrument_context_state")
        or instrument_context.get("identity_state_v2")
        or council.get("instrument_context_state")
        or promotion.get("instrument_context_state")
        or instrument_context.get("identity_state"),
        "UNKNOWN",
    ).upper()
    instrument_safe = bool(
        instrument_context.get("broker_click_safe") is True
        or council.get("instrument_context_broker_click_safe") is True
        or promotion.get("instrument_context_broker_click_safe") is True
    )
    instrument_release = _text(
        instrument_context.get("release_condition")
        or council.get("release_condition")
        or promotion.get("instrument_context_release_condition")
        or promotion.get("release_condition")
        or next_required
    )
    instrument_reason = _short_reason(
        instrument_context.get("reason")
        or instrument_release
        or ("Broker click safe" if instrument_safe else "Instrument context waiting")
    )
    if "INSTRUMENT_CONTEXT" in (reason or next_required).upper() and not instrument_safe:
        instrument_state = instrument_state if instrument_state != "UNKNOWN" else "WAITING"

    state_chip = packet_type
    if cooldown_remaining_seconds > 0:
        state_chip = "COOLDOWN"
    elif action_state == "ACTION":
        state_chip = "ACTION"
    elif packet_type == "EXECUTABLE":
        state_chip = "EXECUTABLE"
    elif packet_state in {"PREPARING", "EXECUTABLE_READY"}:
        state_chip = "PREPARING"

    state = {
        "session_id": _text(session_id, "session"),
        "mode": _text(mode, "LIVE").upper(),
        "timestamp": time.time(),
        "state_chip": state_chip,
        "packet": {
            "type": packet_type,
            "id_short": _short_id(payload.get("packet_id") or payload.get("decision_id")),
            "age_sec": latency,
            "state": packet_state,
            "side": side,
        },
        "council": {
            "state": packet_state,
            "side": side,
            "lane": lane or "LANE_PENDING",
            "lane_short": _lane_short(lane),
            "lane_status": lane_status,
            "final_score": final_score,
            "threshold": threshold,
            "score_gap": score_gap,
            "next_required": _short_reason(next_required or reason),
            "reason_short": _short_reason(reason or next_required),
        },
        "timing": {
            "mode": timing_mode,
            "path_class": path_class,
            "preferred_expiry_sec": preferred_expiry,
            "reward_eta_sec": reward_eta,
            "invalidation_eta_sec": invalidation_eta,
            "entry_now_allowed": bool(entry_now_allowed) if entry_now_allowed is not None else None,
            "summary": timing_summary,
            "current_candle_phase": dict(current_candle_phase) if isinstance(current_candle_phase, Mapping) else {},
            "drawdown_first_warning": dict(drawdown_warning) if isinstance(drawdown_warning, Mapping) else {},
        },
        "instrument": {
            "state": instrument_state,
            "broker_click_safe": instrument_safe,
            "symbol_source": _text(instrument_context.get("symbol_source") or instrument_context.get("source")),
            "timeframe": _text(instrument_context.get("timeframe")),
            "reason_short": instrument_reason,
            "next_required": _short_reason(instrument_release),
            "evidence": dict(_mapping(instrument_context.get("evidence"))),
        },
        "scores": _extract_scores(payload, council, promotion),
        "shooter": {
            "state": action_state,
            "action": action_text,
            "calibration": _text(_mapping(action_payload).get("calibration"), "Pending"),
            "time_sequence": _text(_mapping(action_payload).get("time_sequence"), "Pending" if packet_type == "EXECUTABLE" else "Study only"),
            "cooldown_remaining_sec": max(0, int(cooldown_remaining_seconds)),
        },
        "health": {
            "tracker": tracker_status,
            "models_awake": models_awake,
            "models_total": models_total,
            "cache": cache_status,
            "latency_sec": latency,
        },
        "inspector": {
            "packet_raw": dict(payload) if isinstance(payload, Mapping) else {},
            "tracker_raw": dict(tracker) if isinstance(tracker, Mapping) else {},
            "action_raw": dict(action_payload) if isinstance(action_payload, Mapping) else {},
        },
    }
    return FloatingStateV2.from_dict(state).as_dict()
