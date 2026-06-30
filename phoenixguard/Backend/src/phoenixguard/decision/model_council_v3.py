from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping, Sequence, cast

from phoenixguard.decision.reasoning_arbitrator_v3 import analyze_reasoning_arbitration_v3
from phoenixguard.decision.market_intelligence_v3 import analyze_market_intelligence
from phoenixguard.execution.packet_v3 import (
    PG_EXECUTION_PACKET_SCHEMA_VERSION,
    build_execution_packet_v3,
    validate_execution_packet_v3,
)
from phoenixguard.execution.sequence_context import build_sequence_context_v3, sequence_context_readiness_report
from phoenixguard.runtime.instrument_context import (
    build_instrument_context,
    symbol_context_from_instrument_context,
    validate_instrument_context,
)


MATURITY_STAGES = (
    "OBSERVATION",
    "HYPOTHESIS",
    "CONTEXT_CONFIRMATION",
    "ZONE_QUALIFICATION",
    "TIMING_READINESS",
    "EXECUTION_MATURITY",
    "EXECUTABLE_PACKET",
)
MODEL_COUNCIL_STUDY_SCHEMA_VERSION = "PG_MODEL_COUNCIL_STUDY_V3"
PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION = "PG_PROMOTION_FAILURE_AUDIT_V3"
ALLOWANCE_PACKAGE_SCHEMA_VERSION = "PG_ALLOWANCE_PACKAGE_V1"
ALLOWANCE_PACKAGE_SWING = "SWING"
ALLOWANCE_PACKAGE_INTRADAY_ENTER_NOW = "INTRADAY_ENTER_NOW"
OPPORTUNITY_MATURITY_SCHEMA_VERSION = "PG_OPPORTUNITY_MATURITY_V3"
OPPORTUNITY_MATURITY_STATES = {
    "NO_OPPORTUNITY",
    "EARLY_FORMING",
    "VALID_WATCH",
    "PREPARE",
    "ENTER_NOW",
    "LATE_CHASE",
    "INVALIDATED",
    "MISSED",
}
COUNCIL_STATES = {
    "NO_SETUP",
    "BUY_OBSERVATION",
    "SELL_OBSERVATION",
    "BUY_HYPOTHESIS",
    "SELL_HYPOTHESIS",
    "BUY_CONTEXT_CONFIRMED",
    "SELL_CONTEXT_CONFIRMED",
    "BUY_ZONE_QUALIFIED",
    "SELL_ZONE_QUALIFIED",
    "BUY_TIMING_READY",
    "SELL_TIMING_READY",
    "BUY_PREPARING",
    "SELL_PREPARING",
    "BUY_EXECUTABLE",
    "SELL_EXECUTABLE",
    "CONFLICT",
    "WATCHING",
    "OBSERVING",
    "COOLDOWN",
    "BLOCKED_BY_MARKET",
    "BLOCKED_BY_RUNTIME",
}
DEFAULT_EXECUTION_LANE_THRESHOLDS = {
    "SNIPER_ZONE_ENTRY": 0.70,
    "FAILED_RETEST_ENTRY": 0.72,
    "LOCAL_BREAKDOWN_CONTINUATION": 0.74,
    "HISTORY_MATCHED_CONTINUATION": 0.76,
    "WAVE_RIDING_CONTINUATION": 0.78,
    "MOMENTUM_ACCEPTANCE_ENTRY": 0.82,
}
DEFAULT_HIGH_FREQUENCY_CONTRIBUTION_THRESHOLD = 0.50
DEFAULT_AI_CONTRIBUTION_STRENGTHS = {
    "market_intelligence": 1.0,
    "decision_kernel": 1.0,
    "smart_money": 1.0,
    "memory_projection": 1.0,
    "lstm_sequence": 1.0,
    "scenario_engine": 1.0,
    "high_frequency": 1.0,
}
LANE_SOFT_PERMISSION_REASONS = {
    "ENTRY_QUALITY_BELOW_ACCEPTABLE",
}
LANE_SOFT_MARKET_BLOCK_REASONS = {
    "CONFLICT_MARKET",
    "PULLBACK_NOT_CONFIRMED",
    "DOMINANCE_WEAKENING",
}
LATE_CHASE_BLOCK_REASONS = {
    "LATE_CHASE",
    "LATE_CHASE_AFTER_IMPULSE",
    "LATE_CHASE_STEEP_IMPULSE",
}
ENTRY_QUALITY_SOFT_STATES = {
    "EARLY_WATCH",
    "WATCH_ONLY",
    "FORMING",
    "",
}
WAVE_REASONING_SOFT_WAIT_STATES = {
    "WAIT_FOR_PULLBACK",
    "WAIT_FOR_BREAK_CONFIRMATION",
    "PREPARE",
    "TRACK_CANDIDATE",
}
INTRADAY_ENTER_NOW_REASONING_SOFT_WAIT_STATES = WAVE_REASONING_SOFT_WAIT_STATES
INTRADAY_ENTER_NOW_LANES = {
    "SNIPER_ZONE_ENTRY",
    "FAILED_RETEST_ENTRY",
    "LOCAL_BREAKDOWN_CONTINUATION",
    "WAVE_RIDING_CONTINUATION",
    "MOMENTUM_ACCEPTANCE_ENTRY",
}
WAVE_REASONING_HARD_BAD_CLASSES = {
    "BUY_HIGH_AFTER_IMPULSE",
    "SELL_LOW_AFTER_DROP",
    "LATE_CHASE",
    "LATE_CHASE_AFTER_IMPULSE",
    "INTO_OPPOSING_FORCE",
    "NO_PATH_ROOM",
    "FAKE_BREAKOUT_RISK",
    "WICK_TRAP",
    "DRAWDOWN_FIRST",
    "DRAWDOWN_FIRST_EXPECTED",
}


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _first_visible_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}, ()):
            return value
    return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _first_trade_side(*values: Any) -> str:
    for value in values:
        side = _side(value)
        if side in {"BUY", "SELL"}:
            return side
    return "HOLD"


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _stable_json_hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(dict(payload), sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _council_debate(
    *,
    candidate_side: str,
    buy_score: float,
    sell_score: float,
    final_state: str,
    market: Mapping[str, Any],
    market_context: Mapping[str, Any],
    entry_quality: Mapping[str, Any],
    trade_permission: Mapping[str, Any],
    block_reason: str | None,
) -> dict[str, Any]:
    trap = _mapping(market.get("market_trap") or _mapping(market.get("market_reality")).get("market_trap"))
    path_risk = _mapping(market.get("path_risk") or _mapping(market.get("market_reality")).get("path_risk"))
    history = _mapping(market.get("history_context"))
    side = candidate_side if candidate_side in {"BUY", "SELL"} else "HOLD"
    buy_case = "BUY evidence is active." if buy_score > 0.0 else "No material BUY evidence."
    sell_case = "SELL evidence is active." if sell_score > 0.0 else "No material SELL evidence."
    if side == "BUY":
        buy_case = f"BUY leads with score {buy_score:.2f}; dominant side is {_side(market_context.get('dominant_side'))}."
        sell_case = f"SELL counter-case score {sell_score:.2f}; opposing force must remain far enough."
    elif side == "SELL":
        sell_case = f"SELL leads with score {sell_score:.2f}; dominant side is {_side(market_context.get('dominant_side'))}."
        buy_case = f"BUY counter-case score {buy_score:.2f}; opposing force must remain far enough."
    risk_reason = str(path_risk.get("reason") or market_context.get("opposing_force_reason") or "Path risk is being monitored.")
    history_reason = str(history.get("reason") or history.get("similarity_state") or "History has no blocking analog.")
    execution_reason = str(
        trade_permission.get("reason")
        or trade_permission.get("deny_reason")
        or entry_quality.get("reason")
        or block_reason
        or "Execution timing is governed by maturity and freshness."
    )
    return {
        "protocol_version": "COUNCIL_DEBATE_PROTOCOL_V1",
        "buy_case": buy_case,
        "sell_case": sell_case,
        "risk_case": risk_reason,
        "history_case": history_reason,
        "execution_case": execution_reason,
        "active_traps": trap.get("active_traps", []),
        "arbitration": final_state,
        "reason": str(block_reason or execution_reason or "Council arbitration complete."),
    }


def _now() -> float:
    return float(time.time())


def _score_from_snapshot(snapshot: Mapping[str, Any], side: str) -> float:
    key = "buy_score" if side == "BUY" else "sell_score"
    explicit_score: float | None = None
    if key in snapshot:
        explicit_score = _clip01(snapshot.get(key))
    probs = _mapping(snapshot.get("probabilities"))
    if side in probs:
        explicit_score = _clip01(probs.get(side))
    market_context = _mapping(snapshot.get("market_context"))
    global_structure = _mapping(snapshot.get("global_structure"))
    local_micro = _mapping(snapshot.get("local_micro_structure"))
    context_global_side = _side(market_context.get("global_side"))
    context_local_side = _side(market_context.get("local_side"))
    dominant_side = _side(market_context.get("dominant_side") or snapshot.get("dominant_side"))
    context_structural_side = (
        context_global_side
        if context_global_side in {"BUY", "SELL"} and context_global_side == context_local_side
        else "HOLD"
    )
    dominant_is_stale = bool(
        dominant_side in {"BUY", "SELL"}
        and context_structural_side in {"BUY", "SELL"}
        and dominant_side != context_structural_side
        and (
            _bool(market_context.get("is_late_chase"))
            or _bool(market_context.get("is_steep_angle_break_risk"))
            or _bool(market_context.get("pullback_not_confirmed"))
            or _upper(market_context.get("current_location")) in {"MIDDLE_DANGER", "UNKNOWN", ""}
            or _upper(market_context.get("entry_quality_state")) in {"BAD_NOW", "LATE_ENTRY", "CHASE_ENTRY"}
            or _upper(market_context.get("trade_permission_deny_reason")) in {"LATE_CHASE_TRAP", "PATH_RISK_WEAK", "IDEAL_PATH_HOLD"}
        )
    )
    if context_structural_side == side:
        structural_confidence = max(
            _clip01(snapshot.get("confidence"), 0.0),
            _clip01(market_context.get("global_confidence"), 0.0),
            _clip01(market_context.get("local_confidence"), 0.0),
            _clip01(global_structure.get("global_confidence"), 0.0),
            _clip01(local_micro.get("confidence"), 0.0),
        )
        structural_floor = 0.76 if dominant_is_stale else 0.68
        structural_score = max(structural_floor, structural_confidence)
        return max(explicit_score if explicit_score is not None else 0.0, structural_score)
    if dominant_side == side:
        if dominant_is_stale:
            stale_score = min(explicit_score if explicit_score is not None else 0.45, 0.45)
            return max(0.18, stale_score)
        if explicit_score is not None:
            return explicit_score
        return max(0.72, _clip01(snapshot.get("confidence"), 0.72))
    if _side(global_structure.get("global_side")) == side and _side(local_micro.get("local_side")) == side:
        structural_score = max(
            0.68,
            0.5 * _clip01(global_structure.get("global_confidence"), 0.68)
            + 0.5 * _clip01(local_micro.get("confidence"), 0.68),
        )
        return max(explicit_score if explicit_score is not None else 0.0, structural_score)
    if explicit_score is not None:
        return explicit_score
    kernel = _mapping(snapshot.get("decision_kernel") or _mapping(snapshot.get("tracking_summary")).get("decision_kernel"))
    dominant = _side(kernel.get("dominant_side") or snapshot.get("dominant_side"))
    confidence = _clip01(kernel.get("p_target_before_invalidation"), _clip01(snapshot.get("confidence"), 0.0))
    if dominant == side:
        return max(0.52, confidence)
    return max(0.0, 0.22 * confidence)


def _runtime_health(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    health = _mapping(snapshot.get("runtime_model_health") or snapshot.get("model_health"))
    if not health:
        return {
            "all_required_models_awake": False,
            "council_status": "MISSING",
            "max_model_latency_ms": 0.0,
            "queue_depth": 0,
            "models": [],
            "missing_runtime_model_health": True,
        }
    models = _rows(health.get("models"))
    now = _float(snapshot.get("now_epoch"), _now())
    missing_heartbeat = False
    max_latency = _float(health.get("max_model_latency_ms"), 0.0)
    for model in models:
        status = str(model.get("status", "") or "").upper()
        heartbeat_age = now - _float(model.get("last_heartbeat_epoch"), now)
        max_latency = max(max_latency, _float(model.get("latency_ms"), 0.0))
        required = model.get("required", True) is not False
        if required and (status in {"STALE", "FAILED", "DISABLED"} or heartbeat_age > _float(health.get("heartbeat_stale_seconds"), 10.0)):
            missing_heartbeat = True
    all_awake = bool(health.get("all_required_models_awake", False)) and not missing_heartbeat
    return {
        "all_required_models_awake": all_awake,
        "council_status": "AWAKE" if all_awake else "STALE",
        "max_model_latency_ms": max_latency,
        "queue_depth": _int(health.get("queue_depth"), 0),
        "models": models,
    }


def _previous_instrument_context(previous_state: Mapping[str, Any] | None) -> dict[str, Any]:
    previous = _mapping(previous_state)
    for key in ("instrument_context", "model_council_packet", "execution_packet"):
        candidate = _mapping(previous.get(key))
        if key == "instrument_context" and candidate:
            return candidate
        nested = _mapping(candidate.get("instrument_context"))
        if nested:
            return nested
    result = _mapping(previous.get("model_council_result"))
    if result:
        return _previous_instrument_context(result)
    return {}


def _instrument_packet_mode(snapshot: Mapping[str, Any]) -> str:
    controls = _mapping(snapshot.get("execution_controls"))
    raw_mode = str(
        snapshot.get("instrument_context_mode")
        or snapshot.get("identity_mode")
        or snapshot.get("execution_mode")
        or controls.get("execution_mode")
        or ""
    ).strip().lower()
    broker_click_enabled = _bool(
        snapshot.get("allow_live_broker_clicks")
        or snapshot.get("broker_click_enabled")
        or controls.get("allow_live_broker_clicks")
        or controls.get("broker_click_enabled")
    )
    if raw_mode in {"broker_click", "broker", "live_click"}:
        return "broker_click"
    if raw_mode == "live" and broker_click_enabled:
        return "broker_click"
    return "paper"


def _timing_context(snapshot: Mapping[str, Any], side: str) -> dict[str, Any]:
    timing = _mapping(snapshot.get("timing") or snapshot.get("execution_timing") or snapshot.get("timing_signal"))
    state = str(timing.get("state") or timing.get("timing_state") or timing.get("entry_state") or "").upper()
    if state in {"READY", "TRIGGER_READY", "SNIPER_READY"}:
        state = "READY"
    elif state in {"WAIT", "WATCH", "SNIPER_WATCH", "PREMATURE", ""}:
        state = "WAIT"
    expiry = _int(
        timing.get("expiry_seconds")
        or timing.get("recommended_expiry_seconds")
        or snapshot.get("expiry_seconds")
        or snapshot.get("required_seconds"),
        0,
    )
    return {
        "state": state,
        "expiry_seconds": expiry,
        "target_time_text": str(timing.get("target_time_text") or ""),
        "reason": str(timing.get("reason") or timing.get("instruction") or f"{side} timing is {state or 'UNKNOWN'}."),
    }


def _candidate_side_from_snapshot(snapshot: Mapping[str, Any]) -> str:
    direct = _side(snapshot.get("candidate_side"))
    if direct in {"BUY", "SELL"}:
        return direct
    for container_key, side_key in (
        ("market_context", "dominant_side"),
        ("global_structure", "global_side"),
        ("local_micro_structure", "local_side"),
        ("risk_opposing_force", "side"),
        ("zone_liquidity", "side"),
    ):
        nested_side = _side(_mapping(snapshot.get(container_key)).get(side_key))
        if nested_side in {"BUY", "SELL"}:
            return nested_side
    return "HOLD"


def _raw_observed_side_from_snapshot(snapshot: Mapping[str, Any]) -> str:
    direct = _side(
        snapshot.get("raw_observed_side")
        or snapshot.get("execution_action")
        or snapshot.get("action")
        or snapshot.get("side")
    )
    if direct in {"BUY", "SELL"}:
        return direct
    direct = _side(snapshot.get("candidate_side"))
    return direct if direct in {"BUY", "SELL"} else "HOLD"


def _scored_candidate_side(
    snapshot: Mapping[str, Any],
    *,
    raw_side: str,
    buy_score: float,
    sell_score: float,
) -> str:
    if buy_score > sell_score:
        return "BUY"
    if sell_score > buy_score:
        return "SELL"
    hint = _side(snapshot.get("candidate_side"))
    if hint in {"BUY", "SELL"}:
        return hint
    return _candidate_side_from_snapshot(snapshot)


def _side_sequence(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in (_side(item) for item in cast(Sequence[Any], value)) if item in {"BUY", "SELL"}]


def _side_flip_count(sides: Sequence[str]) -> int:
    flips = 0
    previous = ""
    for side in sides:
        current = _side(side)
        if current not in {"BUY", "SELL"}:
            continue
        if previous in {"BUY", "SELL"} and current != previous:
            flips += 1
        previous = current
    return flips


def _entry_quality_label(value: Any) -> str:
    if isinstance(value, Mapping):
        quality = cast(Mapping[str, Any], value)
        return str(
            quality.get("state")
            or quality.get("entry_grade")
            or quality.get("grade")
            or quality.get("quality")
            or ""
        ).strip().upper()
    return str(value or "").strip().upper()


def _entry_quality_acceptable(value: Any) -> bool:
    label = _entry_quality_label(value)
    if not label:
        return True
    if label in {"A_PLUS_ENTRY", "GOOD_ENTRY", "ACCEPTABLE_ENTRY"}:
        return True
    if isinstance(value, Mapping) and cast(Mapping[str, Any], value).get("passes_executable_threshold") is True:
        return True
    return False


def _candidate_id(
    snapshot: Mapping[str, Any],
    *,
    side: str,
    market_context: Mapping[str, Any],
    entry_quality: Any,
) -> str:
    setup = ""
    if isinstance(entry_quality, Mapping):
        entry_quality_map = cast(Mapping[str, Any], entry_quality)
        setup = str(entry_quality_map.get("setup") or entry_quality_map.get("pattern") or entry_quality_map.get("entry_model") or "")
    if not setup:
        setup = str(snapshot.get("setup") or snapshot.get("setup_name") or snapshot.get("strategy") or "")
    zone = str(
        snapshot.get("sniper_zone_id")
        or snapshot.get("conservative_trigger_zone_id")
        or market_context.get("sniper_zone_id")
        or market_context.get("conservative_trigger_zone_id")
        or market_context.get("current_location")
        or _mapping(snapshot.get("zone_liquidity")).get("zone_type")
        or ""
    )
    seed = "|".join(
        [
            str(snapshot.get("session_id") or ""),
            str(snapshot.get("symbol") or snapshot.get("market") or ""),
            str(snapshot.get("timeframe") or snapshot.get("focus_timeframe") or ""),
            side,
            setup,
            zone,
        ]
    )
    return "pgcand_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _diagnostic_skill_gates(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw = snapshot.get("skill_gates", [])
    if isinstance(raw, Mapping):
        iterable = list(cast(Mapping[str, Any], raw).values())
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        iterable = list(cast(Sequence[Any], raw))
    else:
        iterable = []
    for item in iterable:
        if isinstance(item, Mapping):
            row = dict(cast(Mapping[str, Any], item))
        else:
            row = {"name": str(item)}
        row["role"] = "DIAGNOSTIC_CONTRIBUTOR_ONLY"
        rows.append(row)
    return rows


def _recent_flip_flop(snapshot: Mapping[str, Any], candidate_side: str, previous_state: Mapping[str, Any] | None) -> bool:
    recent = _side_sequence(snapshot.get("recent_candidate_sides") or snapshot.get("recent_sides"))
    if len(recent) >= 3 and len(set(recent[-3:])) > 1:
        return True
    prev = _mapping(previous_state)
    prev_side = _side(prev.get("final_side") or _mapping(prev.get("model_council")).get("final_side"))
    prev_state = str(prev.get("final_state") or _mapping(prev.get("model_council")).get("final_state") or "").upper()
    if prev_state == "EXECUTABLE" and prev_side in {"BUY", "SELL"} and candidate_side in {"BUY", "SELL"} and prev_side != candidate_side:
        invalidated = _bool(snapshot.get("previous_side_invalidated") or snapshot.get("confirmed_reversal"))
        return not invalidated
    return False


def _maturity_stage(snapshot: Mapping[str, Any], side: str, market: Mapping[str, Any], timing: Mapping[str, Any]) -> str:
    if side not in {"BUY", "SELL"}:
        return "OBSERVATION"
    market_context = _mapping(market.get("market_context"))
    context_confirmed = _bool(snapshot.get("context_confirmed")) or (
        _side(market_context.get("dominant_side")) == side
        and _side(market_context.get("global_side")) in {side, "HOLD"}
        and _side(market_context.get("local_side")) in {side, "HOLD"}
        and _bool(market_context.get("opposing_force_distance_ok"))
    )
    if not context_confirmed:
        return "HYPOTHESIS"
    if not _bool(market_context.get("inside_valid_trigger_zone")):
        return "CONTEXT_CONFIRMATION"
    if str(timing.get("state", "")).upper() != "READY":
        return "ZONE_QUALIFICATION"
    if _int(snapshot.get("stability_frames"), 0) < 2 and not _bool(snapshot.get("execution_mature")):
        return "TIMING_READINESS"
    return "EXECUTION_MATURITY"


def _opportunity_maturity_v3(
    *,
    candidate_side: str,
    runtime_blocked: bool,
    candidate_invalidated: bool,
    side_ok: bool,
    context_ok: bool,
    lane_effective_timing_ready: bool,
    lane_effective_mature: bool,
    stable: bool,
    final_score_passed: bool,
    timing_has_explicit_expiry: bool,
    timing_mode: str,
    entry_now_allowed: bool,
    current_candle_ok: bool,
    trap_active: bool,
    late_chase: bool,
    opposing_force_ok: bool,
    path_class: str,
    reasoning_execution_blocked: bool,
    reasoning_block_reason: str,
    hard_bad_entry_class_active: bool,
    bad_entry_filter_hard_active: bool,
    bad_entry_detected_effective: bool,
    history_exit_active: bool,
    permission_denied_effective: bool,
    permission_prepare_allowed: bool,
    final_execution_score: float,
    lane_required_score: float,
    execution_lane: Mapping[str, Any],
    release_condition: str = "",
    next_required: str = "",
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    soft_pressure: list[dict[str, Any]] = []

    def add_blocker(field: str, received: Any, required: Any, reason: str, *, hard: bool = False) -> None:
        blockers.append(
            {
                "field": field,
                "received": received,
                "required": required,
                "reason": reason,
                "hard": bool(hard),
            }
        )

    def add_soft(name: str, value: Any, effect: str) -> None:
        soft_pressure.append({"name": name, "value": value, "effect": effect})

    if runtime_blocked:
        add_blocker("runtime", "blocked", "runtime_pass", "Hard runtime integrity is not clear.", hard=True)
    if candidate_side not in {"BUY", "SELL"}:
        add_blocker("candidate_side", candidate_side or "HOLD", "BUY or SELL", "No directional opportunity is mature enough.")
    if candidate_invalidated:
        add_blocker("candidate_invalidated", True, False, "The current candidate was invalidated.", hard=True)
    if not context_ok:
        add_blocker("execution_lane.accepted", False, True, str(execution_lane.get("reason") or "No accepted execution lane."))
    if not lane_effective_mature:
        add_blocker("candidate_maturity", False, True, "Candidate still needs mature/repeated confirmation.")
    if not stable:
        add_blocker("candidate_stability", False, True, "Candidate side/dominance is not stable enough.")
    if not lane_effective_timing_ready or timing_mode != "ENTER_NOW":
        add_blocker("timing_mode", timing_mode or "UNKNOWN", "ENTER_NOW", "Entry timing is not at the immediate-entry phase.")
    if not timing_has_explicit_expiry:
        add_blocker("timing.expiry_seconds", "missing", "explicit positive expiry", "Execution contract needs explicit expiry.", hard=True)
    if not current_candle_ok:
        add_blocker("current_candle.entry_allowed", False, True, "Current candle is not accepted for entry.")
    if not final_score_passed:
        add_blocker(
            "final_execution_score",
            round(float(final_execution_score), 4),
            f">= {float(lane_required_score):.4f}",
            "Lane score is below threshold.",
        )
    if trap_active:
        add_blocker("market_trap", True, False, "Active trap blocks promotion.", hard=True)
    if late_chase or path_class == "LATE_CHASE_REVERSAL_RISK":
        add_blocker("late_chase", True, False, "Price is late/extended; do not chase.")
    if not opposing_force_ok:
        add_blocker("opposing_force_ok", False, True, "Opposing force is too close.")
    if reasoning_execution_blocked:
        add_blocker("reasoning_decision", reasoning_block_reason or "BLOCKED", "EXECUTE or soft override", "Reasoning layer is not released.")
    if hard_bad_entry_class_active or bad_entry_filter_hard_active or bad_entry_detected_effective:
        add_blocker(
            "bad_entry_filter",
            True,
            False,
            "Hard bad-entry class is active.",
            hard=bool(hard_bad_entry_class_active or bad_entry_filter_hard_active),
        )
    if history_exit_active:
        add_blocker("historical_pattern.would_have_exited_here", True, False, "History suggests this is an exit zone, not an entry.")
    if permission_denied_effective and not permission_prepare_allowed:
        add_blocker("trade_permission", "denied", "granted or prepare_allowed", "Trade permission hard-blocked.", hard=True)
    elif permission_denied_effective:
        add_soft("trade_permission", "prepare_only", "classification_reduced_to_prepare")

    hard_blockers = [row for row in blockers if row.get("hard")]
    score_gap = round(max(0.0, float(lane_required_score) - float(final_execution_score)), 4)
    base_confidence = _clip01(float(final_execution_score) / max(0.01, float(lane_required_score)), 0.0)
    confidence = _clip01(
        base_confidence
        - 0.10 * len(hard_blockers)
        - 0.035 * max(0, len(blockers) - len(hard_blockers)),
        0.0,
    )

    if candidate_invalidated or trap_active or hard_bad_entry_class_active or bad_entry_filter_hard_active or bad_entry_detected_effective:
        state = "INVALIDATED"
    elif late_chase or path_class == "LATE_CHASE_REVERSAL_RISK":
        state = "LATE_CHASE"
    elif not side_ok:
        state = "NO_OPPORTUNITY"
    elif runtime_blocked:
        state = "VALID_WATCH" if context_ok else "EARLY_FORMING"
    elif not context_ok:
        state = "EARLY_FORMING"
    elif not lane_effective_mature or not stable:
        state = "VALID_WATCH"
    elif (
        entry_now_allowed
        and timing_mode == "ENTER_NOW"
        and current_candle_ok
        and final_score_passed
        and not reasoning_execution_blocked
        and opposing_force_ok
        and not history_exit_active
        and not permission_denied_effective
        and timing_has_explicit_expiry
    ):
        state = "ENTER_NOW"
    else:
        state = "PREPARE"

    if state == "ENTER_NOW":
        denied_at = "NONE"
        next_step = "publish validated PG_EXECUTION_PACKET_V3"
    else:
        first_blocker = blockers[0] if blockers else {}
        denied_at = str(first_blocker.get("field") or state).strip().upper().replace(".", "_")
        next_step = str(next_required or release_condition or first_blocker.get("required") or "continue study").strip()

    return {
        "schema_version": OPPORTUNITY_MATURITY_SCHEMA_VERSION,
        "state": state,
        "confidence": round(float(confidence), 4),
        "candidate_side": candidate_side if candidate_side in {"BUY", "SELL"} else "HOLD",
        "visual_integrity": "BLOCK" if any(row.get("hard") and row["field"] in {"runtime", "timing.expiry_seconds"} for row in blockers) else "PASS",
        "hard_blockers": hard_blockers,
        "blockers": blockers,
        "soft_contributors": soft_pressure,
        "entry_quality_factors": {
            "context_ok": bool(context_ok),
            "lane_timing_ready": bool(lane_effective_timing_ready),
            "lane_mature": bool(lane_effective_mature),
            "stable": bool(stable),
            "final_score_passed": bool(final_score_passed),
            "score_gap": score_gap,
            "timing_mode": timing_mode,
            "entry_now_allowed": bool(entry_now_allowed),
            "current_candle_ok": bool(current_candle_ok),
            "opposing_force_ok": bool(opposing_force_ok),
            "path_class": path_class,
        },
        "denied_at": denied_at,
        "next_required": next_step,
    }


def _mark_opportunity_maturity_blocked(
    maturity: dict[str, Any],
    *,
    state: str,
    denied_at: str,
    next_required: str,
    field: str,
    received: Any,
    required: Any,
    reason: str,
    hard: bool = True,
) -> dict[str, Any]:
    normalized_state = _upper(state, "VALID_WATCH")
    if normalized_state not in OPPORTUNITY_MATURITY_STATES:
        normalized_state = "VALID_WATCH"
    blocker = {
        "field": field,
        "received": received,
        "required": required,
        "reason": reason,
        "hard": bool(hard),
    }
    blockers = _rows(maturity.get("blockers"))
    blockers.append(blocker)
    hard_blockers = _rows(maturity.get("hard_blockers"))
    if hard:
        hard_blockers.append(blocker)
    maturity.update(
        {
            "state": normalized_state,
            "confidence": round(max(0.0, _clip01(maturity.get("confidence"), 0.0) - (0.16 if hard else 0.06)), 4),
            "visual_integrity": "BLOCK" if hard else maturity.get("visual_integrity", "PASS"),
            "blockers": blockers,
            "hard_blockers": hard_blockers,
            "denied_at": _upper(denied_at),
            "next_required": str(next_required or reason or required or "").strip(),
        }
    )
    return maturity


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text or default


def _build_allowance_package_v1(
    *,
    candidate_side: str,
    timing_mode: str,
    timing_decision: Mapping[str, Any],
    execution_lane: Mapping[str, Any],
    final_execution_score: float,
    lane_required_score: float,
    executable: bool,
    final_state: str,
    true_blocker: str,
    next_required: str,
    release_state: str,
    promotion_result: str,
    path_class: str,
    preferred_expiry_seconds: int,
    final_score_passed: bool,
    intraday_reasoning_override_allowed: bool,
    wave_reasoning_override_allowed: bool,
    trap_active: bool,
    late_chase: bool,
    opposing_force_ok: bool,
    hard_bad_entry_class_active: bool,
    opportunity_maturity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entry_now_allowed = bool(_mapping(timing_decision).get("entry_now_allowed"))
    maturity = _mapping(opportunity_maturity)
    opportunity_state = _upper(maturity.get("state"), "NO_OPPORTUNITY")
    lane_name = _upper(execution_lane.get("name"))
    timing_mode_normalized = _upper(timing_mode or timing_decision.get("timing_mode"))
    package_type = (
        ALLOWANCE_PACKAGE_INTRADAY_ENTER_NOW
        if entry_now_allowed and timing_mode_normalized == "ENTER_NOW"
        else ALLOWANCE_PACKAGE_SWING
    )
    decision_accepted = bool(
        candidate_side in {"BUY", "SELL"}
        and bool(execution_lane.get("accepted"))
        and final_score_passed
        and (entry_now_allowed if package_type == ALLOWANCE_PACKAGE_INTRADAY_ENTER_NOW else True)
    )
    blocker = _upper(true_blocker)
    accepted_lanes_raw = execution_lane.get("accepted_lanes", [])
    accepted_lanes = (
        list(cast(Sequence[Any], accepted_lanes_raw))
        if isinstance(accepted_lanes_raw, Sequence) and not isinstance(accepted_lanes_raw, (str, bytes, bytearray))
        else []
    )
    package: dict[str, Any] = {
        "schema_version": ALLOWANCE_PACKAGE_SCHEMA_VERSION,
        "package_type": package_type,
        "allowance_family": "INTRADAY" if package_type == ALLOWANCE_PACKAGE_INTRADAY_ENTER_NOW else "SWING",
        "execution_authority": PG_EXECUTION_PACKET_SCHEMA_VERSION,
        "side": candidate_side if candidate_side in {"BUY", "SELL"} else None,
        "accepted": decision_accepted,
        "decision_accepted": decision_accepted,
        "execution_ready": bool(executable),
        "executable": bool(executable),
        "opportunity_maturity": opportunity_state,
        "opportunity_maturity_confidence": round(_clip01(maturity.get("confidence"), 0.0), 4),
        "visual_integrity": str(maturity.get("visual_integrity") or "UNKNOWN").upper(),
        "tracking_active": bool(package_type == ALLOWANCE_PACKAGE_SWING and decision_accepted and not executable),
        "intraday_capture_active": bool(package_type == ALLOWANCE_PACKAGE_INTRADAY_ENTER_NOW and decision_accepted),
        "entry_now_allowed": entry_now_allowed,
        "timing_mode": timing_mode_normalized,
        "path_class": _upper(path_class),
        "selected_lane": lane_name,
        "lane_accepted": bool(execution_lane.get("accepted")),
        "accepted_lanes": accepted_lanes,
        "score": round(float(final_execution_score), 4),
        "threshold": round(float(lane_required_score), 4),
        "score_passed": bool(final_score_passed),
        "preferred_expiry_sec": int(max(0, preferred_expiry_seconds)),
        "final_state": _upper(final_state),
        "promotion_result": promotion_result,
        "release_state": release_state,
        "true_blocker": None if blocker in {"", "NONE"} else blocker,
        "next_required": "" if str(next_required or "").lower() == "none" else str(next_required or ""),
        "reasoning_override_allowed": bool(intraday_reasoning_override_allowed or wave_reasoning_override_allowed),
        "intraday_reasoning_override_allowed": bool(intraday_reasoning_override_allowed),
        "wave_reasoning_override_allowed": bool(wave_reasoning_override_allowed),
        "safety": {
            "trap_clear": not trap_active,
            "late_chase_clear": not late_chase,
            "opposing_force_ok": bool(opposing_force_ok),
            "hard_bad_entry_class_active": bool(hard_bad_entry_class_active),
        },
    }
    return package


def _mark_allowance_package_blocked(
    package: dict[str, Any],
    *,
    block_reason: str,
    next_required: str,
    release_state: str,
    final_state: str = "WATCHING",
    promotion_result: str = "STUDY_PACKET_PUBLISHED",
) -> dict[str, Any]:
    package.update(
        {
            "execution_ready": False,
            "executable": False,
            "tracking_active": bool(package.get("package_type") == ALLOWANCE_PACKAGE_SWING and package.get("accepted")),
            "final_state": _upper(final_state),
            "promotion_result": promotion_result,
            "release_state": release_state,
            "true_blocker": _upper(block_reason),
            "next_required": str(next_required or ""),
        }
    )
    return package


def _lane_thresholds(snapshot: Mapping[str, Any]) -> dict[str, float]:
    thresholds = dict(DEFAULT_EXECUTION_LANE_THRESHOLDS)
    supplied = _mapping(snapshot.get("lane_thresholds") or snapshot.get("execution_lane_thresholds"))
    for lane, value in supplied.items():
        key = _upper(lane)
        if key in thresholds:
            thresholds[key] = _clip01(value, thresholds[key])
    thresholds.pop("HIGH_FREQUENCY_TWO_CANDLE", None)
    return thresholds


def _ai_contribution_strengths(snapshot: Mapping[str, Any]) -> dict[str, float]:
    controls = _mapping(snapshot.get("execution_controls"))
    supplied = _mapping(snapshot.get("ai_contribution_strengths") or controls.get("ai_contribution_strengths"))
    strengths = dict(DEFAULT_AI_CONTRIBUTION_STRENGTHS)
    for name, value in supplied.items():
        key = str(name or "").strip().lower()
        if key in strengths:
            strengths[key] = max(0.0, min(2.0, _float(value, strengths[key])))
    return strengths


def _ai_strength_multiplier(strengths: Mapping[str, float]) -> float:
    values = [_float(strengths.get(key), 1.0) for key in DEFAULT_AI_CONTRIBUTION_STRENGTHS]
    if not values:
        return 1.0
    return max(0.0, min(2.0, sum(values) / len(values)))


def _nested_bool(*containers: Mapping[str, Any], names: Sequence[str]) -> bool:
    for container in containers:
        for name in names:
            if name in container and _bool(container.get(name)):
                return True
    return False


def _nested_text(*containers: Mapping[str, Any], names: Sequence[str]) -> str:
    for container in containers:
        for name in names:
            text = str(container.get(name) or "").strip()
            if text:
                return text
    return ""


LIVE_TRIGGER_ENTRY_STATES = {"SNIPER_READY", "TRIGGER_READY", "TRIGGERED", "ACTIVE", "EXECUTE"}
LIVE_REACTION_TIMING_CLASSES = {
    "MEASURED_REACTION_WINDOW",
    "OPPOSING_FORCE_REACTION",
    "FAILED_RETEST_REACTION",
    "SNIPER_REACTION",
    "HIGH_FREQUENCY_TWO_CANDLE_CYCLE",
}


def _live_trigger_reaction_evidence(
    snapshot: Mapping[str, Any],
    latest_signal: Mapping[str, Any],
    tracking: Mapping[str, Any],
    execution_timing: Mapping[str, Any],
    current_candle: Mapping[str, Any],
    side: str,
) -> dict[str, Any]:
    entry_state = _upper(
        _nested_text(
            snapshot,
            latest_signal,
            tracking,
            execution_timing,
            names=("entry_state", "setup_state", "trigger_state", "trigger", "decision_state", "state"),
        )
    )
    timing_class = _upper(
        execution_timing.get("timing_class")
        or execution_timing.get("class")
        or latest_signal.get("timing_class")
        or tracking.get("timing_class")
    )
    trigger_side = _first_trade_side(
        latest_signal.get("execution_action"),
        latest_signal.get("action"),
        latest_signal.get("candidate_action"),
        tracking.get("execution_action"),
        tracking.get("action"),
        execution_timing.get("side"),
        execution_timing.get("candidate_side"),
        snapshot.get("execution_action"),
        snapshot.get("candidate_side"),
    )
    expiry_seconds = _int(
        execution_timing.get("expiry_seconds")
        or execution_timing.get("recommended_expiry_seconds")
        or execution_timing.get("target_seconds")
        or latest_signal.get("expiry_seconds")
        or tracking.get("expiry_seconds")
        or snapshot.get("expiry_seconds"),
        0,
    )
    candle_entry_allowed = bool(
        current_candle.get("entry_allowed")
        and not bool(current_candle.get("too_late"))
        and not bool(current_candle.get("wick_reversal_risk"))
    )
    timing_entry_allowed = _bool(
        execution_timing.get("entry_allowed")
        or execution_timing.get("actionable")
        or latest_signal.get("actionable")
        or tracking.get("actionable")
    )
    trigger_state_ready = entry_state in LIVE_TRIGGER_ENTRY_STATES
    timing_reaction_ready = bool(
        timing_class in LIVE_REACTION_TIMING_CLASSES
        or _upper(execution_timing.get("state") or execution_timing.get("entry_state")) in {"READY", "TRIGGER_READY", "SNIPER_READY"}
    )
    accepted = bool(
        side in {"BUY", "SELL"}
        and trigger_side == side
        and trigger_state_ready
        and timing_reaction_ready
        and (timing_entry_allowed or timing_class in LIVE_REACTION_TIMING_CLASSES)
        and candle_entry_allowed
        and expiry_seconds > 0
    )
    blockers: list[str] = []
    if side not in {"BUY", "SELL"}:
        blockers.append("NO_DIRECTION_CANDIDATE")
    if trigger_side != side:
        blockers.append("TRIGGER_SIDE_MISMATCH")
    if not trigger_state_ready:
        blockers.append("TRIGGER_STATE_NOT_READY")
    if not timing_reaction_ready:
        blockers.append("REACTION_TIMING_NOT_READY")
    if not timing_entry_allowed and timing_class not in LIVE_REACTION_TIMING_CLASSES:
        blockers.append("TIMING_ENTRY_NOT_ALLOWED")
    if not candle_entry_allowed:
        blockers.append("CURRENT_CANDLE_NOT_ACCEPTED")
    if expiry_seconds <= 0:
        blockers.append("EXPLICIT_EXPIRY_MISSING")
    return {
        "accepted": accepted,
        "side": side if side in {"BUY", "SELL"} else "HOLD",
        "trigger_side": trigger_side,
        "entry_state": entry_state,
        "timing_class": timing_class,
        "expiry_seconds": expiry_seconds,
        "candle_entry_allowed": candle_entry_allowed,
        "timing_entry_allowed": timing_entry_allowed,
        "blockers": blockers,
    }


def _current_candle_acceptance(snapshot: Mapping[str, Any], market: Mapping[str, Any], side: str) -> dict[str, Any]:
    market_reality = _mapping(market.get("market_reality"))
    candle = _mapping(
        snapshot.get("current_candle_acceptance")
        or snapshot.get("current_candle")
        or snapshot.get("current_candle_contract")
        or market.get("current_candle_acceptance")
        or market.get("current_candle_contract")
        or market_reality.get("current_candle_contract")
    )
    latest_signal = _mapping(snapshot.get("latest_signal"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    execution_timing = _mapping(snapshot.get("execution_timing") or snapshot.get("timing_signal"))
    entry_state = _upper(
        _nested_text(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=("entry_state", "setup_state", "trigger_state", "trigger", "decision_state", "state"),
        )
    )
    explicit_phase = _upper(candle.get("candle_phase") or candle.get("phase") or candle.get("state"))
    micro_break = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=(
            "microstructure_break",
            "local_breakdown_confirmed",
            "local_breakout_confirmed",
            "structure_break",
            "current_flow_continuation_ready",
        ),
    )
    if explicit_phase in {"UNSAFE", "FAIL", "INVALID", "REJECT"}:
        phase = explicit_phase
    elif explicit_phase in {"ACTIVE_BREAKDOWN", "ACTIVE_BREAKOUT", "REJECTION", "RETEST_FAILURE", "VALID"}:
        phase = explicit_phase
    elif micro_break and side == "SELL":
        phase = "ACTIVE_BREAKDOWN"
    elif micro_break and side == "BUY":
        phase = "ACTIVE_BREAKOUT"
    elif entry_state in {"SNIPER_READY", "TRIGGER_READY", "TRIGGERED", "ACTIVE", "EXECUTE"}:
        phase = "REJECTION"
    else:
        phase = "FORMING"

    angle = _mapping(market.get("angle_context") or snapshot.get("angle_context") or snapshot.get("angle_features"))
    angle_class = _upper(angle.get("angle_class"))
    raw_progress = candle.get("close_progress")
    if raw_progress is None:
        raw_progress = candle.get("progress", snapshot.get("current_candle_progress"))
    close_progress = _clip01(raw_progress, 0.5)
    wick_risk = _bool(candle.get("wick_reversal_risk") or snapshot.get("wick_reversal_risk"))
    too_late = bool(
        _bool(candle.get("too_late") or snapshot.get("current_candle_late"))
        or angle_class in {"PARABOLIC_RISK", "VERTICAL_EXHAUSTION"}
        or ("max_entry_progress" in candle and raw_progress is not None and close_progress > _clip01(candle.get("max_entry_progress"), 0.96))
    )
    executable_allowed = candle.get("executable_allowed")
    entry_allowed = bool(
        side in {"BUY", "SELL"}
        and executable_allowed is not False
        and not too_late
        and not wick_risk
        and phase not in {"UNSAFE", "FAIL", "INVALID", "REJECT"}
    )
    if entry_allowed:
        reason = f"{side} current candle is acceptable for {phase.lower()}."
    elif too_late:
        reason = "Current candle is too late or exhausted for a fresh entry."
    elif wick_risk:
        reason = "Current candle has wick/reversal risk."
    else:
        reason = str(candle.get("reason") or "Current candle has not accepted an entry phase.")
    return {
        "side": side if side in {"BUY", "SELL"} else "HOLD",
        "candle_phase": phase,
        "entry_allowed": entry_allowed,
        "too_late": too_late,
        "too_early": phase == "FORMING",
        "wick_reversal_risk": wick_risk,
        "close_progress": round(float(close_progress), 4),
        "reason": reason,
    }


def _timeframe_seconds(timeframe: Any, default: int = 300) -> int:
    label = _upper(timeframe)
    mapping = {
        "M1": 60,
        "M3": 180,
        "M5": 300,
        "M15": 900,
        "M30": 1800,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400,
    }
    return int(mapping.get(label, default))


def _timing_expiry_band(preferred_seconds: int) -> dict[str, Any]:
    preferred = max(1, int(preferred_seconds or 1))
    minimum = max(30, int(preferred * 0.6))
    maximum = max(preferred, int(preferred * 2.0))
    avoid = sorted({max(30, int(preferred * 0.2)), max(preferred + 1, int(preferred * 3.0))})
    return {
        "minimum_safe_expiry_sec": minimum,
        "preferred_expiry_sec": preferred,
        "maximum_useful_expiry_sec": maximum,
        "avoid_expiry_sec": avoid,
    }


def _timing_stage_label(seconds_elapsed: int, seconds_remaining: int, timeframe_seconds: int) -> str:
    if seconds_remaining <= 0:
        return "NEW_CANDLE_CONFIRMATION"
    if seconds_remaining <= max(15, int(timeframe_seconds * 0.1)):
        return "CLOSE_PRESSURE"
    if seconds_elapsed <= 0:
        return "OPENING_PHASE"
    if seconds_elapsed <= max(1, int(timeframe_seconds * 0.2)):
        return "EARLY_CANDLE"
    if seconds_elapsed <= max(1, int(timeframe_seconds * 0.75)):
        return "MID_CANDLE"
    return "LATE_CANDLE"


def _timing_entry_quality(allowed: bool) -> str:
    return "GOOD" if allowed else "POOR"


def _timing_entry_mode(path_class: str, *, entry_allowed: bool, lane_name: str, current_candle_phase: str, current_candle_ok: bool, too_late: bool) -> str:
    if entry_allowed:
        return "ENTER_NOW"
    if too_late or current_candle_phase in {"LATE_CANDLE", "CLOSE_PRESSURE", "REJECTION"}:
        return "SKIP_LATE_ENTRY"
    if lane_name == "FAILED_RETEST_ENTRY" or path_class == "PULLBACK_THEN_CONTINUATION":
        return "WAIT_FOR_RETEST"
    if lane_name == "LOCAL_BREAKDOWN_CONTINUATION":
        return "WAIT_FOR_BREAK_CONFIRMATION"
    if lane_name == "WAVE_RIDING_CONTINUATION":
        return "WAIT_FOR_BREAK_CONFIRMATION"
    if lane_name == "SNIPER_ZONE_ENTRY":
        return "WAIT_FOR_PULLBACK"
    if not current_candle_ok:
        return "WAIT_FOR_CANDLE_CLOSE_BEHAVIOUR"
    return "WAIT_FOR_PULLBACK"


def _timing_path_class(
    *,
    lane_name: str,
    current_candle_ok: bool,
    current_candle_phase: str,
    opposing_force_ok: bool,
    late_chase: bool,
    path_score: float,
) -> str:
    if late_chase or current_candle_phase in {"LATE_CANDLE", "CLOSE_PRESSURE"}:
        return "LATE_CHASE_REVERSAL_RISK"
    if not opposing_force_ok or path_score < 0.45:
        return "OPPOSING_FORCE_FIRST"
    if lane_name == "FAILED_RETEST_ENTRY":
        return "PULLBACK_THEN_CONTINUATION"
    if lane_name == "SNIPER_ZONE_ENTRY":
        return "PULLBACK_THEN_CONTINUATION" if not current_candle_ok else "DIRECT_CONTINUATION"
    if lane_name in {"LOCAL_BREAKDOWN_CONTINUATION", "HISTORY_MATCHED_CONTINUATION", "WAVE_RIDING_CONTINUATION"}:
        return "DIRECT_CONTINUATION"
    if lane_name == "MOMENTUM_ACCEPTANCE_ENTRY":
        return "FAKEOUT_THEN_DIRECTION" if not current_candle_ok else "DIRECT_CONTINUATION"
    if not current_candle_ok:
        return "ADVERSE_FIRST_THEN_TARGET"
    return "DIRECT_CONTINUATION"


def _wave_riding_context(
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    side: str,
    market_context: Mapping[str, Any],
    source_market_context: Mapping[str, Any],
    latest_signal: Mapping[str, Any],
    tracking: Mapping[str, Any],
    execution_timing: Mapping[str, Any],
    current_candle: Mapping[str, Any],
    path_score: float,
    opposing_force_ok: bool,
    side_aligned: bool,
    local_aligned: bool,
    global_side: str,
    local_side: str,
    lane_score: float,
    dominance_strengthening: bool,
    continuation_confirmed: bool,
    micro_break: bool,
    retest_detected: bool,
    failed_retest: bool,
    history_exit_here: bool,
    angle_ok: bool,
    late_chase: bool,
) -> dict[str, Any]:
    zone_liquidity = _mapping(snapshot.get("zone_liquidity") or market.get("zone_liquidity"))
    decision_kernel = _mapping(snapshot.get("decision_kernel") or market.get("decision_kernel"))
    smc = _mapping(snapshot.get("smart_money_context") or tracking.get("smart_money_context") or latest_signal.get("smart_money_context"))
    candle_phase = _upper(current_candle.get("candle_phase") or current_candle.get("phase") or current_candle.get("state"))
    current_location = _upper(
        market_context.get("current_location")
        or source_market_context.get("current_location")
        or zone_liquidity.get("current_location")
    )
    zone_type = _upper(zone_liquidity.get("zone_type") or zone_liquidity.get("type") or zone_liquidity.get("family"))
    preferred_entry_area = _upper(
        execution_timing.get("preferred_entry_area")
        or market_context.get("preferred_entry_area")
        or source_market_context.get("preferred_entry_area")
        or zone_liquidity.get("preferred_entry_area")
    )
    entry_relation = _upper(
        execution_timing.get("entry_area_relation")
        or market_context.get("entry_area_relation")
        or source_market_context.get("entry_area_relation")
        or zone_liquidity.get("entry_area_relation")
    )
    significant_context = _upper(
        execution_timing.get("significant_entry_context")
        or execution_timing.get("significant_zone_entry_context")
        or market_context.get("significant_entry_context")
        or zone_liquidity.get("significant_entry_context")
    )
    inside_valid_trigger = _bool(market_context.get("inside_valid_trigger_zone") or zone_liquidity.get("inside_valid_trigger_zone"))
    entry_area_near = _bool(
        execution_timing.get("entry_area_near")
        or market_context.get("entry_area_near")
        or source_market_context.get("entry_area_near")
        or inside_valid_trigger
    )
    entry_area_score = max(
        _clip01(execution_timing.get("entry_area_score"), 0.0),
        _clip01(market_context.get("entry_area_score"), 0.0),
        _clip01(source_market_context.get("entry_area_score"), 0.0),
        _clip01(zone_liquidity.get("strength"), 0.0) if entry_area_near else 0.0,
        0.72 if inside_valid_trigger else 0.0,
    )
    clear_path_score = max(
        _clip01(execution_timing.get("clear_path_score"), 0.0),
        _clip01(market_context.get("clear_path_score"), 0.0),
        _clip01(source_market_context.get("clear_path_score"), 0.0),
        _clip01(decision_kernel.get("p_target_before_invalidation"), 0.0),
        _clip01(path_score, 0.0),
    )
    p_target_before_invalidation = max(
        _clip01(execution_timing.get("p_target_before_invalidation"), 0.0),
        _clip01(decision_kernel.get("p_target_before_invalidation"), 0.0),
        _clip01(snapshot.get("p_target_before_invalidation"), 0.0),
    )
    if p_target_before_invalidation <= 0.0:
        p_target_before_invalidation = clear_path_score
    p_trigger = max(
        _clip01(execution_timing.get("p_trigger_next_1"), 0.0),
        _clip01(execution_timing.get("p_trigger_next_3"), 0.0),
        _clip01(decision_kernel.get("p_trigger_next_1"), 0.0),
        _clip01(decision_kernel.get("p_trigger_next_3"), 0.0),
    )
    p_next_side = _side(
        execution_timing.get("p_next_side")
        or decision_kernel.get("p_next_side")
        or latest_signal.get("next_side")
        or latest_signal.get("side")
    )
    current_flow_continuation_ready = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=("current_flow_continuation_ready", "current_flow_ready", "flow_continuation_ready"),
    )
    current_flow_direction_confirmed = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=("current_flow_direction_confirmed", "flow_direction_confirmed", "direction_confirmed"),
    )
    breakout_confirmation = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        smc,
        names=("breakout_confirmation", "role_flip_confirmed", "break_and_retest_confirmed", "bms_confirmed", "bos_confirmed"),
    )
    liquidity_sweep = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        smc,
        names=("liquidity_sweep", "stop_hunt", "liquidity_grab", "ssl_sweep", "bsl_sweep"),
    )
    flow_conflicts_raw: Any = execution_timing.get("current_flow_conflicts") or market_context.get("current_flow_conflicts") or []
    flow_conflict_count = (
        len(cast(Sequence[Any], flow_conflicts_raw))
        if isinstance(flow_conflicts_raw, Sequence) and not isinstance(flow_conflicts_raw, (str, bytes, bytearray))
        else 0
    )
    compression_pressure = _clip01(execution_timing.get("compression_pressure"), 0.0)
    history_area_label = _upper(execution_timing.get("history_area_label") or market_context.get("history_area_label"))
    history_area_risk = max(
        _clip01(execution_timing.get("history_area_risk"), 0.0),
        _clip01(market_context.get("history_area_risk"), 0.0),
    )
    history_extension_against_side = _bool(
        execution_timing.get("history_extension_against_side")
        or market_context.get("history_extension_against_side")
    )
    history_extension_stretched = _bool(
        execution_timing.get("history_extension_stretched")
        or market_context.get("history_extension_stretched")
    )
    favorable_history_reclaim = _bool(
        execution_timing.get("favorable_history_reclaim")
        or market_context.get("favorable_history_reclaim")
    )
    favorable_history_rejection = _bool(
        execution_timing.get("favorable_history_rejection")
        or market_context.get("favorable_history_rejection")
    )

    entry_keywords = {
        "BUY": {"DEMAND", "SUPPORT", "LOW", "LOWER", "DISCOUNT", "PULLBACK", "RETEST", "SSL", "BUY_ZONE"},
        "SELL": {"SUPPLY", "RESISTANCE", "HIGH", "UPPER", "PREMIUM", "PULLBACK", "RETEST", "BSL", "SELL_ZONE"},
    }
    opposing_keywords = {
        "BUY": {"SUPPLY", "RESISTANCE", "HIGH", "UPPER", "PREMIUM", "BSL", "SELL_ZONE", "OPPOSING"},
        "SELL": {"DEMAND", "SUPPORT", "LOW", "LOWER", "DISCOUNT", "SSL", "BUY_ZONE", "OPPOSING"},
    }
    text_context = " ".join(
        item
        for item in (current_location, zone_type, preferred_entry_area, entry_relation, significant_context)
        if item
    )
    active_entry_context = " ".join(
        item
        for item in (current_location, preferred_entry_area, entry_relation, significant_context)
        if item
    )
    entry_location_match = any(keyword in active_entry_context for keyword in entry_keywords.get(side, set())) or bool(
        entry_area_near and any(keyword in zone_type for keyword in entry_keywords.get(side, set()))
    )
    opposing_location_match = any(keyword in text_context for keyword in opposing_keywords.get(side, set()))
    if "MIDDLE_SAFE" in text_context:
        opposing_location_match = False
    if "NO_OPPOSING" in text_context or "CLEAR_PATH" in text_context:
        opposing_location_match = False
    if "MIDDLE_DANGER" in text_context and clear_path_score < 0.68:
        opposing_location_match = True
    adverse_entry_location = bool(opposing_location_match)
    sell_low_history_risk = bool(
        side == "SELL"
        and not favorable_history_rejection
        and (
            history_area_label in {"LOWER_STUDIED_HISTORY", "STUDIED_LOW_EXTREME", "LOW_EXTREME", "LOWER_HISTORY"}
            or history_area_risk >= 0.58
            or history_extension_against_side
            or history_extension_stretched
        )
    )
    buy_high_history_risk = bool(
        side == "BUY"
        and not favorable_history_reclaim
        and (
            history_area_label in {"UPPER_STUDIED_HISTORY", "STUDIED_HIGH_EXTREME", "HIGH_EXTREME", "UPPER_HISTORY"}
            or history_area_risk >= 0.58
            or history_extension_against_side
            or history_extension_stretched
        )
    )
    entry_area_behind_price = bool(
        (side == "SELL" and entry_relation in {"ABOVE_PRICE", "ABOVE", "MAPPED_HISTORY"} and not entry_area_near)
        or (side == "BUY" and entry_relation in {"BELOW_PRICE", "BELOW", "MAPPED_HISTORY"} and not entry_area_near)
    )
    directional_location_chase_risk = bool(
        sell_low_history_risk
        or buy_high_history_risk
        or adverse_entry_location
        or entry_area_behind_price
    )

    entry_area_valid = bool(entry_area_near or entry_area_score >= 0.58 or entry_location_match)
    near_opposing_force = bool((not opposing_force_ok) or opposing_location_match)
    zone_timing_confirmed = bool(entry_area_near and (continuation_confirmed or dominance_strengthening))
    reaction_confirmed = bool(
        failed_retest
        or retest_detected
        or liquidity_sweep
        or zone_timing_confirmed
        or candle_phase in {"REJECTION", "RETEST_FAILURE", "ACTIVE_BREAKDOWN", "ACTIVE_BREAKOUT", "VALID"}
        or _nested_bool(
            snapshot,
            latest_signal,
            tracking,
            execution_timing,
            names=("rejection_confirmed", "reaction_confirmed", "pullback_reclaimed", "reclaim_confirmed"),
        )
    )
    local_reclaim_confirmed = bool(
        local_aligned
        and (
            dominance_strengthening
            or continuation_confirmed
            or current_flow_continuation_ready
            or current_flow_direction_confirmed
            or breakout_confirmation
            or micro_break
            or retest_detected
        )
    )
    clear_path_ready = bool(
        opposing_force_ok
        and clear_path_score >= 0.58
        and p_target_before_invalidation >= 0.55
        and flow_conflict_count <= 1
    )
    continuation_ready = bool(
        local_reclaim_confirmed
        and side_aligned
        and angle_ok
        and not late_chase
        and not history_exit_here
        and not (directional_location_chase_risk and not breakout_confirmation)
        and (
            current_flow_continuation_ready
            or current_flow_direction_confirmed
            or micro_break
            or (dominance_strengthening and entry_area_valid)
            or (continuation_confirmed and (entry_area_valid or p_trigger >= 0.62))
        )
        and (
            clear_path_ready
            or breakout_confirmation
            or p_trigger >= 0.62
            or lane_score >= 0.86
        )
    )
    breakout_role_flip_ready = bool(
        breakout_confirmation
        and local_reclaim_confirmed
        and angle_ok
        and not late_chase
        and p_target_before_invalidation >= 0.52
        and flow_conflict_count <= 1
    )
    directional_location_ok = bool(not directional_location_chase_risk or breakout_role_flip_ready)
    pullback_reclaim_ready = bool(
        entry_area_valid
        and directional_location_ok
        and local_reclaim_confirmed
        and reaction_confirmed
        and angle_ok
        and not late_chase
        and (clear_path_ready or p_trigger >= 0.58 or lane_score >= 0.84)
    )
    force_reaction_ready = bool(
        near_opposing_force
        and not adverse_entry_location
        and directional_location_ok
        and reaction_confirmed
        and local_reclaim_confirmed
        and (clear_path_ready or breakout_role_flip_ready)
    )
    strong_confluence_override = bool(
        lane_score >= 0.88
        and local_reclaim_confirmed
        and angle_ok
        and not late_chase
        and p_target_before_invalidation >= 0.62
        and clear_path_score >= 0.68
        and flow_conflict_count == 0
        and not history_exit_here
        and directional_location_ok
    )
    buy_low_sell_high_ok = bool(
        directional_location_ok
        and (
            entry_area_valid
            or pullback_reclaim_ready
            or breakout_role_flip_ready
            or strong_confluence_override
            or (continuation_ready and clear_path_ready and not near_opposing_force)
        )
    )
    granular_entry_ok = bool(
        local_reclaim_confirmed
        and current_flow_continuation_ready
        and buy_low_sell_high_ok
        and not near_opposing_force
        and clear_path_score >= 0.58
    )

    blockers: list[str] = []
    if side not in {"BUY", "SELL"}:
        blockers.append("NO_DIRECTION_CANDIDATE")
    if local_side in {"BUY", "SELL"} and side in {"BUY", "SELL"} and local_side != side and not local_reclaim_confirmed:
        blockers.append("LOCAL_WAVE_AGAINST_ENTRY")
    if near_opposing_force and not (force_reaction_ready or breakout_role_flip_ready):
        blockers.append("OPPOSING_FORCE_DECISION_UNRESOLVED")
    if not directional_location_ok:
        blockers.append("SELL_LOW_SUPPORT_LOCATION_GUARD" if side == "SELL" else "BUY_HIGH_RESISTANCE_LOCATION_GUARD" if side == "BUY" else "DIRECTIONAL_LOCATION_GUARD")
    if not buy_low_sell_high_ok:
        blockers.append("BUY_LOW_SELL_HIGH_LOCATION_NOT_READY")
    if not clear_path_ready and not (breakout_role_flip_ready or pullback_reclaim_ready or strong_confluence_override):
        blockers.append("WAVE_PATH_NOT_CLEAR")
    if history_exit_here:
        blockers.append("HISTORY_EXIT_ZONE")
    if not angle_ok or late_chase:
        blockers.append("ANGLE_OR_LATE_CHASE_RISK")
    if not bool(current_candle.get("entry_allowed")):
        blockers.append("CURRENT_CANDLE_NOT_ACCEPTED")
    if not entry_area_valid and not continuation_ready and not breakout_role_flip_ready:
        blockers.append("MID_RANGE_NEEDS_FLOW_PROOF")

    if near_opposing_force and not (force_reaction_ready or breakout_role_flip_ready):
        phase = "WAIT_AT_OPPOSING_FORCE"
    elif breakout_role_flip_ready:
        phase = "BREAKOUT_ROLE_FLIP"
    elif pullback_reclaim_ready:
        phase = "PULLBACK_RECLAIM"
    elif force_reaction_ready:
        phase = "OPPOSING_FORCE_REACTION"
    elif continuation_ready and clear_path_ready:
        phase = "CLEAR_PATH_CONTINUATION"
    elif compression_pressure >= 0.55:
        phase = "COMPRESSION_WAIT"
    else:
        phase = "MID_RANGE_TIMING_ONLY"

    wave_entry_ok = bool(
        side in {"BUY", "SELL"}
        and bool(current_candle.get("entry_allowed"))
        and not blockers
        and (
            pullback_reclaim_ready
            or force_reaction_ready
            or breakout_role_flip_ready
            or (continuation_ready and clear_path_ready)
            or strong_confluence_override
        )
    )
    if wave_entry_ok:
        next_required = "none"
    elif "LOCAL_WAVE_AGAINST_ENTRY" in blockers:
        next_required = "wait for local wave reclaim or confirmed role-flip before entry"
    elif "OPPOSING_FORCE_DECISION_UNRESOLVED" in blockers:
        next_required = "wait for rejection, break-and-retest, or clean continuation through opposing force"
    elif "BUY_LOW_SELL_HIGH_LOCATION_NOT_READY" in blockers:
        next_required = "wait for price to return to the correct buy-low/sell-high entry area"
    elif "MID_RANGE_NEEDS_FLOW_PROOF" in blockers:
        next_required = "wait for current-flow continuation proof before entering mid-range"
    else:
        next_required = "; ".join(blockers) or "wait for wave maturity"

    wave_score = max(
        0.0,
        min(
            1.0,
            0.24 * clear_path_score
            + 0.22 * entry_area_score
            + 0.18 * (1.0 if local_reclaim_confirmed else 0.0)
            + 0.14 * (1.0 if reaction_confirmed else 0.0)
            + 0.12 * (1.0 if continuation_ready else 0.0)
            + 0.10 * (1.0 if breakout_role_flip_ready else 0.0),
        ),
    )
    return {
        "contract_version": "PG_WAVE_RIDING_CONTEXT_V1",
        "side": side if side in {"BUY", "SELL"} else "HOLD",
        "phase": phase,
        "wave_entry_ok": wave_entry_ok,
        "granular_entry_ok": granular_entry_ok,
        "wave_score": round(float(wave_score), 4),
        "clear_path_ready": clear_path_ready,
        "clear_path_score": round(float(clear_path_score), 4),
        "entry_area_valid": entry_area_valid,
        "entry_area_near": entry_area_near,
        "entry_area_score": round(float(entry_area_score), 4),
        "buy_low_sell_high_ok": buy_low_sell_high_ok,
        "directional_location_ok": directional_location_ok,
        "directional_location_chase_risk": directional_location_chase_risk,
        "adverse_entry_location": adverse_entry_location,
        "entry_area_behind_price": entry_area_behind_price,
        "sell_low_history_risk": sell_low_history_risk,
        "buy_high_history_risk": buy_high_history_risk,
        "history_area_label": history_area_label,
        "history_area_risk": round(float(history_area_risk), 4),
        "history_extension_against_side": history_extension_against_side,
        "history_extension_stretched": history_extension_stretched,
        "near_opposing_force": near_opposing_force,
        "opposing_force_ok": bool(opposing_force_ok),
        "reaction_confirmed": reaction_confirmed,
        "pullback_reclaim_ready": pullback_reclaim_ready,
        "breakout_role_flip_ready": breakout_role_flip_ready,
        "force_reaction_ready": force_reaction_ready,
        "continuation_ready": continuation_ready,
        "strong_confluence_override": strong_confluence_override,
        "local_reclaim_confirmed": local_reclaim_confirmed,
        "current_flow_continuation_ready": current_flow_continuation_ready,
        "current_flow_direction_confirmed": current_flow_direction_confirmed,
        "breakout_confirmation": breakout_confirmation,
        "liquidity_sweep": liquidity_sweep,
        "p_target_before_invalidation": round(float(p_target_before_invalidation), 4),
        "p_trigger": round(float(p_trigger), 4),
        "p_next_side": p_next_side,
        "flow_conflict_count": flow_conflict_count,
        "current_location": current_location,
        "zone_type": zone_type,
        "preferred_entry_area": preferred_entry_area,
        "entry_area_relation": entry_relation,
        "significant_entry_context": significant_context,
        "local_side": local_side,
        "global_side": global_side,
        "side_aligned": side_aligned,
        "local_aligned": local_aligned,
        "blockers": blockers,
        "next_required": next_required,
    }


def _permission_failed_reasons(trade_permission: Mapping[str, Any]) -> set[str]:
    raw = trade_permission.get("failed_reasons")
    reasons: set[str] = set()
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        reasons.update(_upper(item) for item in cast(Sequence[Any], raw) if str(item or "").strip())
    deny = _upper(trade_permission.get("deny_reason"))
    if deny:
        reasons.add(deny)
    return reasons


def _resolve_execution_lane(
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    candidate_side: str,
    *,
    lane_score: float,
    execution_threshold: float,
    entry_quality_ok: bool,
    entry_quality_label: str,
    trap_active: bool,
    opposing_force_ok: bool,
    timing_ready: bool,
    timing_has_explicit_expiry: bool,
    stable: bool,
    candidate_stable_reads: int,
    dominance_margin: float,
) -> dict[str, Any]:
    side = candidate_side if candidate_side in {"BUY", "SELL"} else "HOLD"
    thresholds = _lane_thresholds(snapshot)
    market_context = _mapping(market.get("market_context"))
    source_market_context = _mapping(snapshot.get("market_context"))
    market_reality = _mapping(market.get("market_reality"))
    latest_signal = _mapping(snapshot.get("latest_signal"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    v3_candidate = _mapping(snapshot.get("v3_execution_candidate"))
    execution_timing = _mapping(snapshot.get("execution_timing") or snapshot.get("timing_signal"))
    angle = _mapping(market.get("angle_context") or snapshot.get("angle_context") or snapshot.get("angle_features"))
    history = _mapping(market.get("history_context") or snapshot.get("history_context") or snapshot.get("historical_pattern"))
    path_risk = _mapping(market.get("path_risk") or market_reality.get("path_risk"))
    ideal_path = _mapping(market.get("ideal_trade_path") or market_reality.get("ideal_trade_path"))

    dominant_side = _side(market_context.get("dominant_side"))
    source_dominant_side = _side(source_market_context.get("dominant_side"))
    source_global_side = _side(source_market_context.get("global_side"))
    source_local_side = _side(source_market_context.get("local_side"))
    source_structural_side = (
        source_global_side
        if source_global_side in {"BUY", "SELL"} and source_global_side == source_local_side
        else "HOLD"
    )
    local_side = _side(
        market_context.get("local_side")
        or _mapping(snapshot.get("local_micro_structure")).get("local_side")
        or tracking.get("local_direction")
    )
    global_side = _side(
        market_context.get("global_side")
        or _mapping(snapshot.get("global_structure")).get("global_side")
        or tracking.get("global_direction")
    )
    entry_state = _upper(
        _nested_text(
        snapshot,
        latest_signal,
        tracking,
        names=("entry_state", "setup_state", "trigger_state", "trigger", "decision_state", "state"),
        )
    )
    lane_hint = _upper(
        v3_candidate.get("lane")
        or latest_signal.get("execution_lane")
        or latest_signal.get("lane")
        or tracking.get("execution_lane")
        or execution_timing.get("lane")
    )
    micro_break = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=(
            "microstructure_break",
            "local_breakdown_confirmed",
            "local_breakout_confirmed",
            "structure_break",
            "current_flow_continuation_ready",
        ),
    )
    failed_retest = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=("failed_retest", "retest_failed", "retest_failed_against_opposite_side", "failed_retest_entry"),
    )
    retest_detected = failed_retest or _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=("retest_detected", "retest_confirmed", "pullback_confirmed"),
    )
    dominance_strengthening = _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        market_context,
        names=("dominance_strengthening", "pressure_strengthening", "momentum_strengthening", "is_continuation_confirmed"),
    ) or dominance_margin >= 0.32
    continuation_confirmed = _bool(market_context.get("is_continuation_confirmed")) or _nested_bool(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        names=("continuation_confirmed", "pullback_confirmed", "retest_confirmed", "current_flow_continuation_ready"),
    )
    late_chase_raw = bool(
        market_context.get("is_late_chase")
        or _bool(angle.get("late_chase_risk"))
        or _bool(angle.get("post_impulse_wait_required"))
    )
    angle_class = _upper(angle.get("angle_class"))
    path_score = _clip01(path_risk.get("score"), 0.72)
    path_ok = bool(path_risk.get("executable_allowed", True)) and opposing_force_ok and path_score >= 0.45
    current_candle = _current_candle_acceptance(snapshot, market, side)
    current_candle_ok = bool(current_candle.get("entry_allowed"))
    live_trigger_reaction = _live_trigger_reaction_evidence(
        snapshot,
        latest_signal,
        tracking,
        execution_timing,
        current_candle,
        side,
    )
    stale_late_chase_overridden = bool(late_chase_raw and live_trigger_reaction.get("accepted"))
    late_chase = bool(late_chase_raw and not stale_late_chase_overridden)
    angle_ok = not late_chase and angle_class not in {"PARABOLIC_RISK", "VERTICAL_EXHAUSTION", "BROKEN_ANGLE"}
    history_success = (
        _upper(history.get("similarity_state")) in {"REPEATING_SUCCESSFUL_PATH", "SUCCESS_MATCH", "WINNING_ANALOG"}
        or _clip01(history.get("similarity_to_winning_setups"), 0.0) >= 0.72
    )
    history_enter_now = _bool(history.get("would_have_entered_here") or history.get("history_would_enter_now"))
    history_exit_here = _bool(history.get("would_have_exited_here") or market_context.get("history_would_exit_here"))
    ideal_action = _upper(ideal_path.get("action"))
    ideal_allows = bool(ideal_path.get("executable_allowed", True)) and ideal_action not in {"PROTECT", "EXIT", "DEFEND", "HOLD", "NO_TRADE"}
    side_aligned = side in {"BUY", "SELL"} and dominant_side == side
    local_aligned = side in {"BUY", "SELL"} and local_side == side
    global_not_opposed_for_strict = global_side in {side, "HOLD"}
    soft_entry_state = entry_quality_label in ENTRY_QUALITY_SOFT_STATES
    timing_can_be_lane_ready = timing_ready or (timing_has_explicit_expiry and current_candle_ok)
    stale_dominant_overridden = bool(
        side in {"BUY", "SELL"}
        and source_structural_side == side
        and source_dominant_side in {"BUY", "SELL"}
        and source_dominant_side != side
        and (
            _bool(source_market_context.get("is_late_chase"))
            or _bool(source_market_context.get("is_steep_angle_break_risk"))
            or _bool(source_market_context.get("pullback_not_confirmed"))
            or _upper(source_market_context.get("current_location")) in {"MIDDLE_DANGER", "UNKNOWN", ""}
            or _upper(source_market_context.get("entry_quality_state")) in {"BAD_NOW", "LATE_ENTRY", "CHASE_ENTRY"}
            or _upper(source_market_context.get("trade_permission_deny_reason")) in {"LATE_CHASE_TRAP", "PATH_RISK_WEAK", "IDEAL_PATH_HOLD"}
        )
    )
    lane_reversal_capture_mature = bool(
        stale_dominant_overridden
        and candidate_stable_reads >= 1
        and dominance_margin >= max(0.12, _float(snapshot.get("reversal_capture_min_dominance"), 0.18))
    )
    stable_for_lane = _bool(snapshot.get("execution_mature")) or candidate_stable_reads >= 2 or lane_reversal_capture_mature
    structural_flow_ready = bool(
        side in {"BUY", "SELL"}
        and global_side == side
        and local_side == side
        and dominance_strengthening
        and opposing_force_ok
        and angle_ok
        and stable_for_lane
        and lane_reversal_capture_mature
    )
    mature_directional_flow_ready = bool(
        side in {"BUY", "SELL"}
        and global_side == side
        and local_side == side
        and dominance_strengthening
        and angle_ok
        and opposing_force_ok
        and path_score >= 0.62
        and not history_exit_here
        and timing_has_explicit_expiry
        and candidate_stable_reads >= _int(snapshot.get("opportunity_capture_stable_reads"), 3)
        and lane_score >= _float(snapshot.get("opportunity_capture_min_score"), 0.90)
    )
    wave_context = _wave_riding_context(
        snapshot,
        market,
        side=side,
        market_context=market_context,
        source_market_context=source_market_context,
        latest_signal=latest_signal,
        tracking=tracking,
        execution_timing=execution_timing,
        current_candle=current_candle,
        path_score=path_score,
        opposing_force_ok=opposing_force_ok,
        side_aligned=side_aligned,
        local_aligned=local_aligned,
        global_side=global_side,
        local_side=local_side,
        lane_score=lane_score,
        dominance_strengthening=dominance_strengthening,
        continuation_confirmed=continuation_confirmed,
        micro_break=micro_break,
        retest_detected=retest_detected,
        failed_retest=failed_retest,
        history_exit_here=history_exit_here,
        angle_ok=angle_ok,
        late_chase=late_chase,
    )

    lane_rows: list[dict[str, Any]] = []
    hf_candle_wave_confirmed = False
    high_frequency_contribution: dict[str, Any] = {
        "name": "HIGH_FREQUENCY_CANDLE_CONTRIBUTION",
        "active": False,
        "status": "NOT_PRESENT",
        "execution_authority": False,
        "lane_authority": False,
        "contributes_to": ["LOCAL_BREAKDOWN_CONTINUATION", "WAVE_RIDING_CONTINUATION"],
        "blockers": ["HIGH_FREQUENCY_CONTEXT_NOT_PRESENT"],
    }

    def add_lane(
        name: str,
        structure_ok: bool,
        reason: str,
        *,
        strictness: str,
        lane_entry_quality_ok: bool,
        lane_timing_ready: bool,
        lane_maturity_ok: bool,
        wave_ok: bool,
    ) -> None:
        required = thresholds.get(name, execution_threshold)
        accepted = bool(
            structure_ok
            and side in {"BUY", "SELL"}
            and path_ok
            and wave_ok
            and not trap_active
            and current_candle_ok
            and lane_score >= required
        )
        blockers: list[str] = []
        if side not in {"BUY", "SELL"}:
            blockers.append("NO_DIRECTION_CANDIDATE")
        if not structure_ok:
            blockers.append(f"{name}_STRUCTURE_NOT_READY")
        if not path_ok:
            blockers.append("PATH_RISK_OR_OPPOSING_FORCE")
        if not wave_ok:
            blockers.append("WAVE_CONTEXT_NOT_READY")
        if trap_active:
            blockers.append("TRAP_ACTIVE")
        if not current_candle_ok:
            blockers.append("CURRENT_CANDLE_NOT_ACCEPTED")
        if lane_score < required:
            blockers.append("LANE_SCORE_BELOW_THRESHOLD")
        lane_rows.append(
            {
                "name": name,
                "accepted": accepted,
                "side": side if side in {"BUY", "SELL"} else "HOLD",
                "reason": reason if accepted else "; ".join(blockers) or reason,
                "strictness": strictness,
                "required_score": round(float(required), 4),
                "actual_score": round(float(lane_score), 4),
                "structure_ok": bool(structure_ok),
                "path_ok": bool(path_ok),
                "wave_ok": bool(wave_ok),
                "wave_phase": wave_context.get("phase"),
                "wave_score": wave_context.get("wave_score"),
                "wave_context": wave_context,
                "live_trigger_reaction": live_trigger_reaction,
                "stale_late_chase_overridden": stale_late_chase_overridden,
                "trap_ok": not trap_active,
                "current_candle_ok": current_candle_ok,
                "entry_quality_ok": bool(lane_entry_quality_ok),
                "timing_ready": bool(lane_timing_ready),
                "maturity_ok": bool(lane_maturity_ok),
                "blockers": blockers,
            }
        )

    hf_cycle = _mapping(snapshot.get("high_frequency_candle_cycle"))
    hf_requested = bool(_bool(hf_cycle.get("enabled")) or lane_hint in {"HIGH_FREQUENCY_TWO_CANDLE", "HIGH_FREQUENCY", "HFT"})
    if hf_requested:
        controls = _mapping(snapshot.get("execution_controls"))
        model_strength_profile = _mapping(snapshot.get("model_strength_profile") or controls.get("model_strength_profile"))
        two_candle_execution_allowed_raw = _first_visible_value(
            snapshot.get("two_candle_execution_allowed"),
            controls.get("two_candle_execution_allowed"),
            model_strength_profile.get("two_candle_execution_allowed"),
        )
        two_candle_execution_allowed = True if two_candle_execution_allowed_raw is None else _bool(two_candle_execution_allowed_raw)
        hf_swing_fallback_raw = hf_cycle.get("swing_fallback_enabled")
        hf_swing_fallback_enabled = True if hf_swing_fallback_raw is None else _bool(hf_swing_fallback_raw)
        hf_side = _first_trade_side(
            hf_cycle.get("side"),
            hf_cycle.get("candidate_side"),
            hf_cycle.get("active_candidate_side"),
            execution_timing.get("side"),
            side,
        )
        hf_score = max(lane_score, _clip01(hf_cycle.get("confidence"), 0.0))
        hf_required = _clip01(
            _first_visible_value(
                snapshot.get("high_frequency_contribution_threshold"),
                controls.get("high_frequency_contribution_threshold"),
                model_strength_profile.get("high_frequency_contribution_threshold"),
                DEFAULT_HIGH_FREQUENCY_CONTRIBUTION_THRESHOLD,
            ),
            DEFAULT_HIGH_FREQUENCY_CONTRIBUTION_THRESHOLD,
        )
        hf_ready = bool(_bool(hf_cycle.get("ready")) and _bool(hf_cycle.get("current_candle_closed")))
        hf_structure_ok = bool(
            hf_ready
            and side in {"BUY", "SELL"}
            and hf_side == side
            and _upper(snapshot.get("timeframe")) == "M5"
            and _bool(hf_cycle.get("forecast_agreement"))
            and _bool(hf_cycle.get("targets_future_candle_window"))
        )
        hf_blockers: list[str] = []
        if side not in {"BUY", "SELL"}:
            hf_blockers.append("NO_DIRECTION_CANDIDATE")
        if not hf_ready:
            hf_blockers.append("CURRENT_M5_CANDLE_NOT_CLOSED")
        if hf_side != side:
            hf_blockers.append("TWO_CANDLE_SIDE_MISMATCH")
        if _upper(snapshot.get("timeframe")) != "M5":
            hf_blockers.append("TIMEFRAME_NOT_M5")
        if not _bool(hf_cycle.get("forecast_agreement")):
            hf_blockers.append("TWO_CANDLE_FORECAST_NOT_ALIGNED")
        if not path_ok:
            hf_blockers.append("PATH_RISK_OR_OPPOSING_FORCE")
        if trap_active:
            hf_blockers.append("TRAP_ACTIVE")
        if not current_candle_ok:
            hf_blockers.append("CURRENT_CANDLE_NOT_ACCEPTED")
        if hf_score < hf_required:
            hf_blockers.append("LANE_SCORE_BELOW_THRESHOLD")
        if not two_candle_execution_allowed:
            hf_blockers.append("TWO_CANDLE_STUDY_ONLY")
        hf_local_reclaim_ok = bool(
            side in {"BUY", "SELL"}
            and local_aligned
            and (
                dominance_strengthening
                or continuation_confirmed
                or micro_break
                or retest_detected
                or structural_flow_ready
                or mature_directional_flow_ready
            )
        )
        if not hf_local_reclaim_ok:
            hf_blockers.append("LOCAL_RECLAIM_NOT_CONFIRMED")
        hf_wave_ok = bool(wave_context.get("granular_entry_ok") or (wave_context.get("wave_entry_ok") and hf_local_reclaim_ok))
        if not hf_wave_ok:
            hf_blockers.append("WAVE_CONTEXT_NOT_READY")
        hf_wave_forming = bool(
            two_candle_execution_allowed
            and side in {"BUY", "SELL"}
            and hf_side == side
            and _upper(snapshot.get("timeframe")) == "M5"
            and _bool(hf_cycle.get("forecast_agreement"))
            and _bool(hf_cycle.get("targets_future_candle_window"))
            and hf_local_reclaim_ok
            and hf_wave_ok
            and path_ok
            and not trap_active
            and current_candle_ok
            and hf_score >= hf_required
        )
        hf_candle_wave_confirmed = bool(hf_wave_forming and hf_structure_ok)
        high_frequency_contribution = {
            "name": "HIGH_FREQUENCY_CANDLE_CONTRIBUTION",
            "active": True,
            "status": "CONTRIBUTING" if hf_candle_wave_confirmed else "FORMING" if hf_wave_forming else "WAITING",
            "execution_authority": False,
            "lane_authority": False,
            "side": side if side in {"BUY", "SELL"} else "HOLD",
            "contributes_to": ["LOCAL_BREAKDOWN_CONTINUATION", "WAVE_RIDING_CONTINUATION"],
            "reason": (
                f"{side} high-frequency candle context confirms the current wave; structural lane authority is still required."
                if hf_candle_wave_confirmed
                else str(hf_cycle.get("reason") or "; ".join(hf_blockers) or "High-frequency candle context is waiting.")
            ),
            "required_score": round(float(hf_required), 4),
            "actual_score": round(float(hf_score), 4),
            "structure_ok": bool(hf_structure_ok),
            "path_ok": bool(path_ok),
            "trap_ok": not trap_active,
            "current_candle_ok": current_candle_ok,
            "blockers": hf_blockers,
            "local_reclaim_ok": hf_local_reclaim_ok,
            "wave_ok": hf_wave_ok,
            "wave_forming": hf_wave_forming,
            "wave_confirmed": hf_candle_wave_confirmed,
            "wave_phase": wave_context.get("phase"),
            "wave_score": wave_context.get("wave_score"),
            "wave_context": wave_context,
            "high_frequency_candle_cycle": hf_cycle,
            "swing_fallback_enabled": hf_swing_fallback_enabled,
        }

    sniper_structure = bool(
        side_aligned
        and global_not_opposed_for_strict
        and _bool(market_context.get("inside_valid_trigger_zone"))
        and opposing_force_ok
        and entry_quality_ok
        and angle_ok
    )
    add_lane(
        "SNIPER_ZONE_ENTRY",
        sniper_structure,
        f"{side} accepted from sniper/conservative trigger zone with path distance.",
        strictness="strict",
        lane_entry_quality_ok=entry_quality_ok,
        lane_timing_ready=timing_ready,
        lane_maturity_ok=stable_for_lane,
        wave_ok=bool(wave_context.get("wave_entry_ok") or wave_context.get("pullback_reclaim_ready")),
    )

    local_break_ready = bool(
        local_aligned
        and side_aligned
        and dominance_strengthening
        and angle_ok
        and stable_for_lane
        and (
            micro_break
            or structural_flow_ready
            or hf_candle_wave_confirmed
            or lane_hint in {"LOCAL_BREAKDOWN_CONTINUATION", "LIVE_MARKET_FLOW", "TREND_FOLLOW"}
        )
    )
    add_lane(
        "LOCAL_BREAKDOWN_CONTINUATION",
        local_break_ready,
        f"{side} pressure accepted by local rejection/breakdown continuation with enough path.",
        strictness="adaptive",
        lane_entry_quality_ok=entry_quality_ok or soft_entry_state,
        lane_timing_ready=timing_can_be_lane_ready,
        lane_maturity_ok=stable_for_lane,
        wave_ok=bool(wave_context.get("wave_entry_ok") or wave_context.get("continuation_ready") or wave_context.get("breakout_role_flip_ready")),
    )

    failed_retest_ready = bool(
        side_aligned
        and local_aligned
        and retest_detected
        and failed_retest
        and angle_ok
        and stable_for_lane
    )
    add_lane(
        "FAILED_RETEST_ENTRY",
        failed_retest_ready,
        f"{side} accepted after failed retest and restored dominance.",
        strictness="adaptive",
        lane_entry_quality_ok=entry_quality_ok or soft_entry_state,
        lane_timing_ready=timing_can_be_lane_ready,
        lane_maturity_ok=stable_for_lane,
        wave_ok=bool(wave_context.get("wave_entry_ok") or wave_context.get("force_reaction_ready") or wave_context.get("pullback_reclaim_ready")),
    )

    wave_ride_ready = bool(
        side_aligned
        and local_aligned
        and angle_ok
        and stable_for_lane
        and not history_exit_here
        and (
            wave_context.get("current_flow_continuation_ready")
            or wave_context.get("current_flow_direction_confirmed")
            or wave_context.get("breakout_confirmation")
            or micro_break
            or hf_candle_wave_confirmed
            or lane_hint in {"WAVE_RIDING_CONTINUATION", "WAVE_RIDING", "RIDE_WAVE"}
        )
        and (
            wave_context.get("continuation_ready")
            or wave_context.get("breakout_role_flip_ready")
            or wave_context.get("pullback_reclaim_ready")
        )
    )
    add_lane(
        "WAVE_RIDING_CONTINUATION",
        wave_ride_ready,
        f"{side} wave accepted because the current flow has reclaimed and path remains open until opposing force.",
        strictness="wave_riding",
        lane_entry_quality_ok=entry_quality_ok or soft_entry_state,
        lane_timing_ready=timing_can_be_lane_ready,
        lane_maturity_ok=stable_for_lane,
        wave_ok=bool(wave_context.get("wave_entry_ok")),
    )

    momentum_ready = bool(
        side_aligned
        and local_aligned
        and dominance_strengthening
        and angle_ok
        and path_score >= 0.62
        and not history_exit_here
        and stable_for_lane
        and (
            structural_flow_ready
            or mature_directional_flow_ready
            or lane_hint in {"MOMENTUM_ACCEPTANCE_ENTRY", "LIVE_MARKET_FLOW", "TREND_FOLLOW"}
            or entry_state in {"SNIPER_READY", "TRIGGER_READY", "TRIGGERED", "ACTIVE", "EXECUTE"}
            or _nested_bool(
                snapshot,
                latest_signal,
                tracking,
                names=("momentum_acceptance", "momentum_acceptance_entry", "current_flow_continuation_ready"),
            )
        )
    )
    add_lane(
        "MOMENTUM_ACCEPTANCE_ENTRY",
        momentum_ready,
        f"{side} momentum is accepted with clean path and no late-chase classification.",
        strictness="aggressive_high_score",
        lane_entry_quality_ok=entry_quality_ok or soft_entry_state,
        lane_timing_ready=timing_can_be_lane_ready,
        lane_maturity_ok=stable_for_lane,
        wave_ok=bool(wave_context.get("wave_entry_ok") or wave_context.get("strong_confluence_override")),
    )

    history_ready = bool(
        side_aligned
        and local_aligned
        and history_success
        and history_enter_now
        and not history_exit_here
        and ideal_allows
        and angle_ok
        and stable_for_lane
    )
    add_lane(
        "HISTORY_MATCHED_CONTINUATION",
        history_ready,
        f"{side} accepted because current structure matches successful historical continuation.",
        strictness="memory_confirmed",
        lane_entry_quality_ok=entry_quality_ok or soft_entry_state,
        lane_timing_ready=timing_can_be_lane_ready,
        lane_maturity_ok=stable_for_lane,
        wave_ok=bool(wave_context.get("wave_entry_ok") or wave_context.get("continuation_ready")),
    )

    accepted = [lane for lane in lane_rows if lane["accepted"]]
    explicit_wave_lane_requested = lane_hint in {"WAVE_RIDING_CONTINUATION", "WAVE_RIDING", "RIDE_WAVE"}
    selected = (
        next((lane for lane in accepted if explicit_wave_lane_requested and lane.get("name") == "WAVE_RIDING_CONTINUATION"), None)
        or (
            max(accepted, key=lambda row: float(row["actual_score"]) - float(row["required_score"]))
            if accepted
            else max(
                lane_rows,
                key=lambda row: (
                    int(bool(row.get("structure_ok"))),
                    float(row.get("actual_score", 0.0)) - float(row.get("required_score", 1.0)),
                ),
            )
        )
    )
    opportunity_capture = bool(
        selected["accepted"]
        and selected["name"] in {"LOCAL_BREAKDOWN_CONTINUATION", "FAILED_RETEST_ENTRY", "MOMENTUM_ACCEPTANCE_ENTRY", "WAVE_RIDING_CONTINUATION"}
        and lane_score >= float(selected["required_score"])
        and timing_has_explicit_expiry
        and current_candle_ok
    )
    return {
        "name": selected["name"],
        "accepted": bool(selected["accepted"]),
        "side": selected["side"],
        "reason": selected["reason"],
        "strictness": selected["strictness"],
        "required_score": selected["required_score"],
        "actual_score": selected["actual_score"],
        "accepted_lanes": [lane["name"] for lane in accepted],
        "evaluated_lanes": lane_rows,
        "blockers": selected.get("blockers", []),
        "lane_entry_quality_ok": bool(selected.get("entry_quality_ok")),
        "lane_timing_ready": bool(selected.get("timing_ready")),
        "lane_maturity_ok": bool(selected.get("maturity_ok")),
        "reversal_capture_mature": bool(lane_reversal_capture_mature),
        "stale_dominant_overridden": bool(stale_dominant_overridden),
        "structural_flow_ready": bool(structural_flow_ready),
        "mature_directional_flow_ready": bool(mature_directional_flow_ready),
        "permission_override_allowed": bool(selected["accepted"] and selected.get("entry_quality_ok")),
        "opportunity_capture_mode": opportunity_capture,
        "current_candle_acceptance": current_candle,
        "live_trigger_reaction": live_trigger_reaction,
        "stale_late_chase_overridden": stale_late_chase_overridden,
        "raw_late_chase": late_chase_raw,
        "effective_late_chase": late_chase,
        "wave_context": wave_context,
        "high_frequency_candle_cycle": hf_cycle,
        "high_frequency_contribution": high_frequency_contribution,
        "next_required": (
            "none"
            if selected["accepted"]
            else selected["reason"]
            or "accepted execution lane"
        ),
    }


def _missed_opportunity_probe(
    *,
    candidate_side: str,
    execution_lane: Mapping[str, Any],
    raw_council_score: float,
    final_execution_score: float,
    true_blocker: str,
) -> dict[str, Any]:
    if candidate_side not in {"BUY", "SELL"} or bool(execution_lane.get("accepted")):
        return {}
    evaluated = _rows(execution_lane.get("evaluated_lanes"))
    priority = {
        "LOCAL_BREAKDOWN_CONTINUATION": 0,
        "FAILED_RETEST_ENTRY": 1,
        "WAVE_RIDING_CONTINUATION": 2,
        "MOMENTUM_ACCEPTANCE_ENTRY": 3,
        "HISTORY_MATCHED_CONTINUATION": 4,
        "SNIPER_ZONE_ENTRY": 5,
    }
    score_passed_lanes = [
        lane
        for lane in evaluated
        if not bool(lane.get("accepted"))
        and _float(lane.get("actual_score"), 0.0) >= _float(lane.get("required_score"), 1.0)
        and bool(lane.get("path_ok", False))
        and bool(lane.get("current_candle_ok", False))
    ]
    if not score_passed_lanes:
        return {}
    selected = min(
        score_passed_lanes,
        key=lambda lane: (
            priority.get(str(lane.get("name") or "").upper(), 99),
            -(_float(lane.get("actual_score"), 0.0) - _float(lane.get("required_score"), 1.0)),
        ),
    )
    lane_name = str(selected.get("name") or "").upper()
    blockers = [
        str(blocker)
        for blocker in selected.get("blockers", [])
        if str(blocker or "").strip()
    ] if isinstance(selected.get("blockers"), Sequence) and not isinstance(selected.get("blockers"), (str, bytes, bytearray)) else []
    case_name = "MISSED_VALID_MOVE_LOCAL_BREAKDOWN" if lane_name == "LOCAL_BREAKDOWN_CONTINUATION" else f"MISSED_VALID_MOVE_{lane_name or 'UNKNOWN'}"
    return {
        "case": case_name,
        "side": candidate_side,
        "setup": lane_name,
        "reason_blocked": true_blocker,
        "final_score": round(float(final_execution_score), 4),
        "raw_council_score": round(float(raw_council_score), 4),
        "lane_score": selected.get("actual_score"),
        "lane_threshold": selected.get("required_score"),
        "blocked_condition": selected.get("reason"),
        "lane_blockers": blockers,
        "future_move_confirmed": None,
        "should_have_lane": lane_name,
        "recommended_patch": (
            "Collect post-block movement for this lane; if future move confirms repeatedly, "
            "tighten structure detection instead of weakening runtime or trap checks."
        ),
    }


def _lane_release_requirements(execution_lane: Mapping[str, Any], *, final_score: float, lane_required_score: float) -> str:
    lane_name = str(execution_lane.get("name") or "execution lane").strip().upper() or "EXECUTION_LANE"
    blockers = [
        _upper(blocker)
        for blocker in execution_lane.get("blockers", [])
        if str(blocker or "").strip()
    ] if isinstance(execution_lane.get("blockers"), Sequence) and not isinstance(execution_lane.get("blockers"), (str, bytes, bytearray)) else []
    requirements: list[str] = []
    if "NO_DIRECTION_CANDIDATE" in blockers:
        requirements.append("candidate_side in BUY/SELL")
    if any(blocker.endswith("_STRUCTURE_NOT_READY") for blocker in blockers):
        requirements.append(f"selected_lane={lane_name}.structure_ok=true")
    if "PATH_RISK_OR_OPPOSING_FORCE" in blockers:
        requirements.append("path_risk.executable_allowed=true + market_context.opposing_force_distance_ok=true")
    if "TRAP_ACTIVE" in blockers:
        requirements.append("market_trap.trap_active=false")
    if "CURRENT_CANDLE_NOT_ACCEPTED" in blockers:
        requirements.append("current_candle.entry_allowed=true")
    if "LANE_SCORE_BELOW_THRESHOLD" in blockers:
        requirements.append(f"final_score={final_score:.4f} >= threshold={lane_required_score:.4f}")
    if not requirements:
        raw = str(execution_lane.get("next_required") or execution_lane.get("reason") or "").strip()
        if raw and raw.upper() not in {"CONTEXT", "WATCHING", "N/A", "MISSING"}:
            requirements.append(raw)
        else:
            requirements.append(f"selected_lane={lane_name}.accepted=true")
    return "; ".join(requirements)


def _instrument_release_requirement(instrument_context: Mapping[str, Any], fallback: str) -> str:
    evidence = _mapping(instrument_context.get("evidence"))
    missing = [
        key
        for key in (
            "window_handle_stable",
            "window_rect_stable",
            "viewport_hash_stable",
            "broker_surface_hash_stable",
            "calibration_layout_match",
            "timeframe_known",
            "session_active",
            "packet_fresh",
            "models_awake",
        )
        if evidence and not bool(evidence.get(key))
    ]
    if bool(evidence.get("profile_mismatch")):
        missing.append("profile_mismatch=false")
    if missing:
        return f"instrument_context.broker_click_safe=false; next_required {' + '.join(missing)}; release requires instrument_context.broker_click_safe=true"
    if fallback and fallback.lower() != "none":
        return f"instrument_context.broker_click_safe=false; next_required {fallback}; release requires instrument_context.broker_click_safe=true"
    return "instrument_context.broker_click_safe=false; next_required stable viewport + broker surface lock; release requires instrument_context.broker_click_safe=true"


def _non_executable_release_state(
    *,
    executable: bool,
    true_blocker: str,
    final_state: str,
    flip_flop_contained: bool,
    permission_denied_effective: bool,
    context_ok: bool,
    lane_effective_timing_ready: bool,
    timing_mode: str,
    final_score_passed: bool,
    lane_score_blocked: bool,
    lane_timing_blocked: bool,
    packet_identity_mode: str,
    instrument_context: Mapping[str, Any],
) -> str:
    if executable:
        return "EXECUTION_PACKET_PUBLISHED"
    blocker = _upper(true_blocker)
    if blocker.startswith("INSTRUMENT_CONTEXT") or (
        packet_identity_mode == "broker_click" and not bool(instrument_context.get("broker_click_safe"))
    ):
        return "INSTRUMENT_CONTEXT_WAIT"
    if flip_flop_contained or blocker == "FLIP_FLOP_CONTAINED":
        return "FLIP_FLOP_CONTAINED"
    if final_state == "PREPARING" and blocker in {"CANDIDATE_MATURITY", "NONE"}:
        return "PREPARING"
    if blocker == "CANDIDATE_SIDE":
        return "WATCHING"
    if lane_timing_blocked:
        return "TIMING_WAIT"
    if lane_score_blocked and not final_score_passed:
        return "LANE_WAIT"
    if not context_ok or blocker == "NO_EXECUTION_LANE_ACCEPTED":
        return "CONTEXT_BLOCKED"
    if permission_denied_effective or blocker in {"TRADE_PERMISSION_DENIED", "PERMISSION_DENIED"}:
        return "PERMISSION_DENIED"
    if blocker == "CANDIDATE_MATURITY":
        return "PREPARING"
    if not lane_effective_timing_ready or timing_mode != "ENTER_NOW":
        return "TIMING_WAIT"
    if not final_score_passed or blocker == "LANE_SCORE_BELOW_THRESHOLD":
        return "LANE_WAIT"
    if final_state == "PREPARING":
        return "PREPARING"
    return "WATCHING"


def _promotion_exact_field(blocker: str, sequence: Mapping[str, Any], instrument_context: Mapping[str, Any]) -> str:
    blocker_upper = _upper(blocker)
    if blocker_upper == "SEQUENCE_CONTEXT":
        failed_module = str(sequence.get("failed_module") or "").strip()
        return failed_module or "sequence_context"
    if blocker_upper.startswith("INSTRUMENT_CONTEXT"):
        if "BROKER_CLICK_SAFE" in blocker_upper or not bool(instrument_context.get("broker_click_safe")):
            return "instrument_context.broker_click_safe"
        return "instrument_context"
    if blocker_upper in {"MODEL_COUNCIL_EXPLICIT_EXPIRY_MISSING", "MISSING_TIME_SEQUENCE"}:
        return "timing.expiry_seconds"
    if "TIMING" in blocker_upper or blocker_upper.startswith("CURRENT_CANDLE"):
        return "timing_mode"
    if blocker_upper in {"NO_EXECUTION_LANE_ACCEPTED", "LANE_SCORE_BELOW_THRESHOLD"}:
        return "execution_lane"
    if blocker_upper.startswith("PG_EXECUTION_PACKET_V3") or blocker_upper == "EXECUTION_PACKET_NOT_CURRENT_AFTER_PUBLICATION":
        return "current_execution_packet"
    if blocker_upper in {"FLIP_FLOP_CONTAINED", "CANDIDATE_MATURITY", "CANDIDATE_SIDE"}:
        return "trade_candidate_queue.active_candidate"
    if "PERMISSION" in blocker_upper:
        return "trade_permission"
    if "TRAP" in blocker_upper or "BAD_ENTRY" in blocker_upper:
        return "market_trap"
    return blocker_upper.lower() or "promotion_trace.denied_at"


def build_promotion_failure_audit_v3(
    *,
    packet_id: str = "",
    candidate_id: str = "",
    promotion_trace: Mapping[str, Any],
    sequence_context_readiness: Mapping[str, Any] | None = None,
    execution_lane: Mapping[str, Any] | None = None,
    final_score: float = 0.0,
    threshold: float = 0.0,
    timing_mode: str = "",
    instrument_context: Mapping[str, Any] | None = None,
    packet_result: str = "",
    extra_source_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    promotion = _mapping(promotion_trace)
    sequence = _mapping(sequence_context_readiness or promotion.get("sequence_context_readiness"))
    lane = _mapping(execution_lane or promotion.get("execution_lane"))
    instrument = _mapping(instrument_context)
    denied_at = _upper(
        promotion.get("denied_at")
        or promotion.get("true_blocker")
        or promotion.get("blocked_by")
        or promotion.get("promotion_result")
        or "EXECUTION_PACKET_NOT_PUBLISHED"
    )
    next_required = str(
        promotion.get("next_required")
        or promotion.get("release_condition")
        or sequence.get("next_required")
        or lane.get("next_required")
        or "publish fresh validated PG_EXECUTION_PACKET_V3 when all gates pass"
    ).strip()
    release_condition = str(promotion.get("release_condition") or next_required).strip()
    opportunity = _mapping(promotion.get("opportunity_maturity"))
    source_fields = dict(extra_source_fields or {})
    opportunity_state = _upper(
        promotion.get("opportunity_maturity_state")
        or opportunity.get("state")
        or source_fields.get("opportunity_maturity_state")
    )
    visual_integrity = _upper(
        promotion.get("visual_integrity")
        or opportunity.get("visual_integrity")
        or source_fields.get("visual_integrity")
    )
    score = float(final_score)
    score_threshold = float(threshold)
    mode = _upper(timing_mode or promotion.get("timing_mode"))
    blockers: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(blocker: str, *, field: str = "", reason: str = "", weight: float = 1.0) -> None:
        label = _upper(blocker or "EXECUTION_PACKET_NOT_PUBLISHED")
        if not label or label == "NONE" or label in seen:
            return
        seen.add(label)
        blockers.append(
            {
                "rank": len(blockers) + 1,
                "blocker": label,
                "field": field or _promotion_exact_field(label, sequence, instrument),
                "reason": str(reason or next_required or label),
                "weight": round(float(weight), 4),
                "next_required": next_required,
            }
        )

    add(denied_at, reason=next_required, weight=1.0)
    if sequence and not bool(sequence.get("ready")):
        add("SEQUENCE_CONTEXT", field=str(sequence.get("failed_module") or "sequence_context"), reason=str(sequence.get("next_required") or next_required), weight=0.92)
    if mode and mode != "ENTER_NOW":
        add(f"TIMING_MODE_{mode}", field="timing_mode", reason=next_required, weight=0.88)
    if score_threshold > 0.0 and score < score_threshold:
        add("LANE_SCORE_BELOW_THRESHOLD", field="final_execution_score", reason=f"final_score={score:.4f} < threshold={score_threshold:.4f}", weight=0.82)
    if lane and not bool(lane.get("accepted")):
        add("NO_EXECUTION_LANE_ACCEPTED", field="execution_lane.accepted", reason=str(lane.get("reason") or lane.get("next_required") or next_required), weight=0.8)
    if instrument and not bool(instrument.get("broker_click_safe")) and denied_at.startswith("INSTRUMENT_CONTEXT"):
        add("INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE", field="instrument_context.broker_click_safe", reason=next_required, weight=0.95)

    if not blockers:
        add("EXECUTION_PACKET_NOT_PUBLISHED", field="promotion_trace.packet_result", reason=next_required, weight=0.5)
    for index, row in enumerate(blockers, start=1):
        row["rank"] = index

    top = blockers[0]
    return {
        "schema_version": PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION,
        "packet_id": str(packet_id or promotion.get("packet_id") or ""),
        "candidate_id": str(candidate_id or promotion.get("candidate_id") or ""),
        "packet_result": _upper(packet_result or promotion.get("packet_result") or "STUDY_PACKET_PUBLISHED"),
        "promotion_result": str(promotion.get("promotion_result") or ""),
        "denied_at": denied_at,
        "top_blocker": top["blocker"],
        "exact_field_preventing_execution_packet": top["field"],
        "next_required": next_required,
        "release_condition": release_condition,
        "final_score": round(score, 4),
        "threshold": round(score_threshold, 4),
        "score_passed": bool(score_threshold > 0.0 and score >= score_threshold),
        "timing_mode": mode,
        "sequence_ready": bool(sequence.get("ready")) if sequence else None,
        "sequence_status": str(sequence.get("sequence_status") or sequence.get("status") or ""),
        "sequence_length": _int(sequence.get("sequence_length"), 0),
        "selected_lane": str(lane.get("name") or promotion.get("selected_lane") or ""),
        "lane_accepted": bool(lane.get("accepted")) if lane else None,
        "instrument_context_state": str(instrument.get("instrument_context_state") or instrument.get("identity_state_v2") or ""),
        "instrument_context_broker_click_safe": bool(instrument.get("broker_click_safe")) if instrument else None,
        "opportunity_maturity_state": opportunity_state,
        "visual_integrity": visual_integrity,
        "blocker_ranking": blockers,
        "source_fields": source_fields,
    }


def _packet_base(snapshot: Mapping[str, Any], now: float) -> dict[str, Any]:
    session_id = str(snapshot.get("session_id") or "pocket-live-8788")
    instrument_context = _mapping(snapshot.get("instrument_context"))
    symbol = str(instrument_context.get("display_symbol") or snapshot.get("symbol") or snapshot.get("market") or "")
    timeframe = str(instrument_context.get("timeframe") or snapshot.get("timeframe") or snapshot.get("focus_timeframe") or "").upper()
    frame_id = _int(snapshot.get("frame_id") or snapshot.get("tracker_frame_id") or snapshot.get("frame_index"), 0)
    capture_count = _int(snapshot.get("capture_count"), frame_id)
    state_version = _int(snapshot.get("state_version") or snapshot.get("decision_version"), max(frame_id, capture_count))
    input_hash = str(snapshot.get("input_frame_hash") or snapshot.get("frame_hash") or _stable_json_hash({
        "session_id": session_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "frame_id": frame_id,
        "capture_count": capture_count,
        "state_version": state_version,
    })[:16])
    packet_seed = f"{session_id}|{symbol}|{timeframe}|{frame_id}|{capture_count}|{state_version}|{input_hash}|{now:.3f}"
    packet_id = "pgpkt_" + hashlib.sha1(packet_seed.encode("utf-8")).hexdigest()[:18]
    return {
        "packet_id": packet_id,
        "session_id": session_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "frame_id": frame_id,
        "capture_count": capture_count,
        "state_version": state_version,
        "input_frame_hash": input_hash,
        "previous_frame_hash": str(snapshot.get("previous_frame_hash") or ""),
    }


def evaluate_model_council_v3(
    snapshot: Mapping[str, Any],
    *,
    previous_state: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    current_now = _now() if now is None else float(now)
    raw_side = _raw_observed_side_from_snapshot(snapshot)
    buy_score = _score_from_snapshot(snapshot, "BUY")
    sell_score = _score_from_snapshot(snapshot, "SELL")
    candidate_side = _scored_candidate_side(
        snapshot,
        raw_side=raw_side,
        buy_score=buy_score,
        sell_score=sell_score,
    )

    market = analyze_market_intelligence(snapshot, candidate_side=candidate_side)
    timing = _timing_context(snapshot, candidate_side)
    health = _runtime_health(snapshot)
    previous_instrument_context = _previous_instrument_context(previous_state)
    has_explicit_instrument_lock = any(
        key in snapshot
        for key in (
            "instrument_context",
            "instrument_identity_lock",
            "viewport_hash",
            "broker_surface_hash",
            "window_handle",
            "window_rect",
            "broker_surface",
            "manual_focus_region",
        )
    )
    if not has_explicit_instrument_lock:
        previous_instrument_context = {}
    instrument_context = build_instrument_context(snapshot, previous_context=previous_instrument_context)
    symbol_context = symbol_context_from_instrument_context(instrument_context)
    study_identity_validation = validate_instrument_context(instrument_context, mode="study")
    packet_identity_mode = _instrument_packet_mode(snapshot)
    packet_identity_validation = validate_instrument_context(instrument_context, mode=packet_identity_mode)
    market_context = _mapping(market.get("market_context"))
    two_candle_study = _mapping(snapshot.get("two_candle_study") or _mapping(snapshot.get("decision_kernel")).get("two_candle_study"))
    ai_contribution_strengths = _ai_contribution_strengths(snapshot)
    ai_strength_multiplier = _ai_strength_multiplier(ai_contribution_strengths)
    model_strength_profile = _mapping(
        snapshot.get("model_strength_profile")
        or _mapping(snapshot.get("execution_controls")).get("model_strength_profile")
    )
    lstm_contribution: dict[str, Any] = _mapping(
        snapshot.get("lstm_contribution")
        or two_candle_study.get("lstm_contribution")
        or _mapping(snapshot.get("decision_kernel")).get("lstm_contribution")
    )
    skill_contributions: list[dict[str, Any]] = []
    if lstm_contribution:
        lstm_strength = ai_contribution_strengths.get("lstm_sequence", 1.0)
        lstm_raw_contribution = _clip01(lstm_contribution.get("contribution"), 0.0)
        lstm_effective_contribution = _clip01(lstm_raw_contribution * lstm_strength, 0.0)
        lstm_contribution = {
            **lstm_contribution,
            "raw_contribution": round(lstm_raw_contribution, 4),
            "effective_contribution": round(lstm_effective_contribution, 4),
            "strength": round(float(lstm_strength), 4),
        }
        skill_contributions.append(
            {
                "skill": "LSTM_CANDLE_SEQUENCE",
                "side": _side(lstm_contribution.get("side")),
                "contribution": round(lstm_effective_contribution, 4),
                "raw_contribution": round(lstm_raw_contribution, 4),
                "strength": round(float(lstm_strength), 4),
                "confidence": round(_clip01(lstm_contribution.get("confidence"), 0.0), 4),
                "fresh": bool(lstm_contribution.get("fresh", False)),
                "blocker": False,
                "interpretation": str(
                    lstm_contribution.get("interpretation")
                    or lstm_contribution.get("reason")
                    or "LSTM candle sequence contribution is diagnostic only."
                ),
            }
        )
    bad_entry = _mapping(market.get("bad_entry"))
    market_reality = _mapping(market.get("market_reality"))
    trade_permission = _mapping(market.get("trade_permission") or market_reality.get("trade_permission"))
    snapshot_trade_permission = _mapping(snapshot.get("trade_permission"))
    if snapshot_trade_permission:
        trade_permission = {**trade_permission, **snapshot_trade_permission}
    permission_executable_allowed = (
        _bool(trade_permission.get("executable_allowed"))
        if "executable_allowed" in trade_permission
        else True
    )
    permission_prepare_allowed = (
        _bool(trade_permission.get("prepare_allowed"))
        if "prepare_allowed" in trade_permission
        else True
    )
    permission_denied = bool(trade_permission and not permission_executable_allowed)
    permission_block_reason = str(trade_permission.get("deny_reason") or "TRADE_PERMISSION_DENIED")
    dominance_margin = abs(buy_score - sell_score)
    disagreement_score = min(1.0, 1.0 - dominance_margin)
    hold_score = max(0.0, min(1.0, 1.0 - max(buy_score, sell_score)))
    entry_quality_surface = market.get("entry_quality", _mapping(market_reality.get("entry_quality")))
    entry_quality_label = _entry_quality_label(entry_quality_surface) or "UNKNOWN"
    market_trap = _mapping(market.get("market_trap", _mapping(market_reality.get("market_trap"))))
    raw_recent_sides = _side_sequence(snapshot.get("recent_raw_sides"))
    if not raw_recent_sides:
        raw_recent_sides = _side_sequence(snapshot.get("recent_sides"))
    candidate_recent_sides = _side_sequence(snapshot.get("recent_candidate_sides"))
    if not candidate_recent_sides:
        candidate_recent_sides = _side_sequence(snapshot.get("recent_sides"))
    raw_flip_count = _side_flip_count(raw_recent_sides)
    candidate_flip_count = _side_flip_count(candidate_recent_sides)
    candidate_stable_reads = _int(snapshot.get("candidate_stable_reads"), _int(snapshot.get("stability_frames"), 0))
    entry_quality_ok = _entry_quality_acceptable(entry_quality_surface)
    trap_active = bool(
        market_trap.get("detected")
        or market_trap.get("trap_active")
        or market_trap.get("trap_free") is False
        or market_trap.get("execution_allowed") is False
        or market_trap.get("executable_allowed") is False
        or bool(market_trap.get("active_traps"))
    )
    opposing_force_ok = _bool(
        market_context.get("opposing_force_distance_ok")
        or snapshot.get("opposing_force_distance_ok")
        or _mapping(snapshot.get("risk_opposing_force")).get("distance_ok")
    )

    both_executable_requested = bool(snapshot.get("buy_executable") and snapshot.get("sell_executable"))
    flip_flop = _recent_flip_flop(snapshot, candidate_side, previous_state)
    maturity_stage = _maturity_stage(snapshot, candidate_side, market, timing)
    models_not_awake = not bool(health.get("all_required_models_awake", False))
    runtime_blocked = models_not_awake or not study_identity_validation.ok
    market_block_reason = _upper(market.get("block_reason"))
    market_blocked = bool(market_block_reason or bad_entry.get("detected"))
    timing_ready = str(timing.get("state", "")).upper() == "READY"
    timing_expiry = _int(timing.get("expiry_seconds"), 0)
    timing_has_explicit_expiry = timing_expiry > 0
    mature = maturity_stage == "EXECUTION_MATURITY" or _bool(snapshot.get("execution_mature"))
    min_dominance_margin = _float(snapshot.get("min_dominance_margin"), 0.18)
    flip_flop_release_allowed = bool(
        flip_flop
        and candidate_stable_reads >= _int(snapshot.get("flip_flop_release_stable_reads"), 2)
        and candidate_flip_count <= _int(snapshot.get("flip_flop_release_candidate_flips"), 2)
        and dominance_margin >= min_dominance_margin
        and entry_quality_ok
        and timing_ready
        and not trap_active
        and opposing_force_ok
    )
    flip_flop_contained = bool(flip_flop and not flip_flop_release_allowed)
    stable = (not flip_flop_contained) and dominance_margin >= min_dominance_margin
    side_ok = candidate_side in {"BUY", "SELL"}
    base_council_score = max(buy_score, sell_score) if side_ok else 0.0
    raw_council_score = _clip01(base_council_score * ai_strength_multiplier)
    trap_penalty = -0.12 if trap_active else 0.0
    path_risk_adjustment = 0.03 if opposing_force_ok else -0.08
    flip_flop_penalty = -0.05 if flip_flop_contained else (-0.02 if flip_flop else 0.0)
    execution_threshold = _float(snapshot.get("execution_threshold"), 0.70)
    lane_score = _clip01(raw_council_score + trap_penalty + path_risk_adjustment + flip_flop_penalty)
    preliminary_stable = (not flip_flop_contained) and dominance_margin >= min_dominance_margin
    execution_lane = _resolve_execution_lane(
        snapshot,
        market,
        candidate_side,
        lane_score=lane_score,
        execution_threshold=execution_threshold,
        entry_quality_ok=entry_quality_ok,
        entry_quality_label=entry_quality_label,
        trap_active=trap_active,
        opposing_force_ok=opposing_force_ok,
        timing_ready=timing_ready,
        timing_has_explicit_expiry=timing_has_explicit_expiry,
        stable=preliminary_stable,
        candidate_stable_reads=candidate_stable_reads,
        dominance_margin=dominance_margin,
    )
    context_ok = bool(execution_lane.get("accepted"))
    lane_effective_entry_quality_ok = bool(entry_quality_ok or execution_lane.get("lane_entry_quality_ok"))
    lane_effective_timing_ready = bool(timing_ready or execution_lane.get("lane_timing_ready"))
    lane_effective_mature = bool(mature or execution_lane.get("lane_maturity_ok"))
    lane_required_score = _float(execution_lane.get("required_score"), execution_threshold)
    final_score_passed = lane_score >= lane_required_score
    angle = _mapping(market.get("angle_context") or snapshot.get("angle_context") or snapshot.get("angle_features"))
    current_candle = _current_candle_acceptance(snapshot, market, candidate_side)
    current_candle_ok = bool(current_candle.get("entry_allowed"))
    live_trigger_reaction = _mapping(execution_lane.get("live_trigger_reaction"))
    stale_late_chase_overridden = bool(execution_lane.get("stale_late_chase_overridden"))
    lane_name = str(execution_lane.get("name") or "").strip().upper()
    current_candle_phase = _upper(
        current_candle.get("candle_phase")
        or current_candle.get("phase")
        or current_candle.get("state")
        or timing.get("current_candle_phase")
    )
    timeframe_seconds = _timeframe_seconds(snapshot.get("timeframe"), 300)
    seconds_elapsed = _int(
        _first_visible_value(
            current_candle.get("seconds_elapsed"),
            current_candle.get("elapsed_seconds"),
            timing.get("seconds_elapsed"),
            timing.get("seconds_elapsed_in_candle"),
            snapshot.get("current_candle_seconds_elapsed"),
        ),
        0,
    )
    seconds_remaining = _int(
        _first_visible_value(
            current_candle.get("seconds_remaining"),
            current_candle.get("remaining_seconds"),
            timing.get("seconds_remaining"),
            timing.get("remaining_seconds"),
            snapshot.get("current_candle_seconds_remaining"),
        ),
        0,
    )
    if not current_candle_phase:
        current_candle_phase = _timing_stage_label(seconds_elapsed, seconds_remaining, timeframe_seconds)
    current_candle_phase = current_candle_phase or "MID_CANDLE"
    if seconds_elapsed <= 0 and seconds_remaining > 0:
        seconds_elapsed = max(0, timeframe_seconds - seconds_remaining)
    if seconds_remaining <= 0 and seconds_elapsed > 0:
        seconds_remaining = max(0, timeframe_seconds - seconds_elapsed)
    late_chase_raw = bool(
        market_context.get("is_late_chase")
        or _bool(angle.get("late_chase_risk"))
        or _bool(angle.get("post_impulse_wait_required"))
        or bool(current_candle.get("too_late"))
        or current_candle_phase in {"LATE_CANDLE", "CLOSE_PRESSURE"}
    )
    late_chase = bool(late_chase_raw and not stale_late_chase_overridden)
    path_class = _timing_path_class(
        lane_name=lane_name,
        current_candle_ok=current_candle_ok,
        current_candle_phase=current_candle_phase,
        opposing_force_ok=opposing_force_ok,
        late_chase=late_chase,
        path_score=lane_score,
    )
    reward_invalidation = _mapping(
        snapshot.get("time_to_reward_invalidation")
        or market.get("time_to_reward_invalidation")
        or market_reality.get("time_to_reward_invalidation")
    )
    preferred_expiry_seconds = max(
        1,
        _int(
            _first_visible_value(
                timing_expiry,
                timing.get("preferred_expiry_seconds"),
                timing.get("recommended_expiry_seconds"),
                execution_lane.get("recommended_expiry_seconds"),
                reward_invalidation.get("recommended_expiry_seconds"),
                reward_invalidation.get("preferred_expiry_seconds"),
                timeframe_seconds,
            ),
            timeframe_seconds,
        ),
    )
    reward_seconds = _int(
        _first_visible_value(
            reward_invalidation.get("expected_time_to_reward_sec"),
            reward_invalidation.get("time_to_reward_sec"),
            reward_invalidation.get("reward_seconds"),
            timing.get("expected_time_to_reward_sec"),
            timing.get("reward_eta_sec"),
            current_candle.get("expected_time_to_reward_sec"),
        ),
        0,
    )
    if reward_seconds <= 0:
        reward_seconds = preferred_expiry_seconds if path_class == "DIRECT_CONTINUATION" else max(preferred_expiry_seconds, int(preferred_expiry_seconds * 1.4))
    invalidation_seconds = _int(
        _first_visible_value(
            reward_invalidation.get("expected_time_to_invalidation_sec"),
            reward_invalidation.get("time_to_invalidation_sec"),
            reward_invalidation.get("invalidation_seconds"),
            timing.get("expected_time_to_invalidation_sec"),
            current_candle.get("expected_time_to_invalidation_sec"),
        ),
        0,
    )
    if invalidation_seconds <= 0:
        invalidation_seconds = max(30, int(preferred_expiry_seconds * (0.3 if path_class in {"DIRECT_CONTINUATION", "PULLBACK_THEN_CONTINUATION"} else 0.2)))
    entry_now_allowed = bool(
        side_ok
        and context_ok
        and lane_effective_timing_ready
        and lane_effective_mature
        and stable
        and final_score_passed
        and timing_has_explicit_expiry
        and current_candle_ok
        and not trap_active
        and opposing_force_ok
        and path_class not in {"ADVERSE_FIRST_THEN_TARGET", "OPPOSING_FORCE_FIRST", "LATE_CHASE_REVERSAL_RISK"}
        and not late_chase
    )
    timing_mode = _timing_entry_mode(
        path_class,
        entry_allowed=entry_now_allowed,
        lane_name=lane_name,
        current_candle_phase=current_candle_phase,
        current_candle_ok=current_candle_ok,
        too_late=bool(current_candle.get("too_late")),
    )
    expiry_band = _timing_expiry_band(preferred_expiry_seconds)
    drawdown_first_warning_active = bool(not entry_now_allowed or path_class in {"ADVERSE_FIRST_THEN_TARGET", "OPPOSING_FORCE_FIRST", "LATE_CHASE_REVERSAL_RISK"})
    timing_forecast: dict[str, Any] = {
        "side": candidate_side if candidate_side in {"BUY", "SELL"} else None,
        "best_entry_mode": timing_mode,
        "expected_time_to_favourable_move_sec": reward_seconds,
        "expected_time_to_adverse_move_sec": invalidation_seconds if drawdown_first_warning_active else max(15, int(preferred_expiry_seconds * 0.25)),
        "expected_time_to_target_sec": reward_seconds,
        "expected_time_to_invalidation_sec": invalidation_seconds,
        "recommended_expiry_sec": preferred_expiry_seconds,
        "entry_now_quality": _timing_entry_quality(entry_now_allowed),
        "entry_after_pullback_quality": "GOOD" if path_class in {"PULLBACK_THEN_CONTINUATION", "DIRECT_CONTINUATION"} else "POOR",
    }
    timing_decision: dict[str, Any] = {
        "direction_side": candidate_side if candidate_side in {"BUY", "SELL"} else None,
        "direction_confidence": round(float(raw_council_score), 4),
        "entry_now_allowed": entry_now_allowed,
        "timing_mode": timing_mode,
        "path_class": path_class,
        "preferred_expiry_sec": preferred_expiry_seconds,
        "expiry_band": {
            "min": expiry_band["minimum_safe_expiry_sec"],
            "preferred": expiry_band["preferred_expiry_sec"],
            "max": expiry_band["maximum_useful_expiry_sec"],
        },
        "time_to_reward_sec": reward_seconds,
        "time_to_invalidation_sec": invalidation_seconds,
        "mfe_mae_expected_ratio": round(float(1.35 if path_class in {"DIRECT_CONTINUATION", "PULLBACK_THEN_CONTINUATION"} else 0.82 if drawdown_first_warning_active else 1.0), 4),
        "reason": (
            "BUY direction likely, but adverse movement is expected before reward."
            if drawdown_first_warning_active and candidate_side == "BUY"
            else "SELL direction likely, but adverse movement is expected before reward."
            if drawdown_first_warning_active and candidate_side == "SELL"
            else "Timing is aligned for immediate execution."
        ),
        "timing_forecast": timing_forecast,
        "entry_timing": {
            "mode": timing_mode,
            "side": candidate_side if candidate_side in {"BUY", "SELL"} else None,
            "reason": (
                "BUY likely later, but current entry usually draws down first."
                if drawdown_first_warning_active and candidate_side == "BUY"
                else "SELL likely later, but current entry usually draws down first."
                if drawdown_first_warning_active and candidate_side == "SELL"
                else "Current entry is executable now."
            ),
            "next_condition": (
                "wait for pullback to local demand or retest hold"
                if timing_mode == "WAIT_FOR_PULLBACK"
                else "enter only if retest fails and SELL dominance remains"
                if timing_mode == "WAIT_FOR_RETEST" and candidate_side == "SELL"
                else "enter only if retest fails and BUY dominance remains"
                if timing_mode == "WAIT_FOR_RETEST"
                else "enter only if breakout confirmation holds"
                if timing_mode == "WAIT_FOR_BREAK_CONFIRMATION"
                else "avoid late entry unless candle closes cleanly"
                if timing_mode == "WAIT_FOR_CANDLE_CLOSE_BEHAVIOUR"
                else "skip this setup until the path clears"
            ),
        },
        "timing_risk": {
            "expected_time_to_reward_sec": reward_seconds,
            "expected_time_to_invalidation_sec": invalidation_seconds,
            "expiry_sec": preferred_expiry_seconds,
            "timing_quality": "GOOD" if entry_now_allowed else "BAD",
            "reason": (
                "reward likely before invalidation"
                if entry_now_allowed and reward_seconds <= invalidation_seconds
                else "market may move against entry before reaching reward"
            ),
        },
        "expiry_band": expiry_band,
        "current_candle_phase": {
            "timeframe": str(snapshot.get("timeframe") or "M5").upper(),
            "seconds_elapsed": seconds_elapsed,
            "seconds_remaining": seconds_remaining,
            "phase": current_candle_phase,
            "entry_risk": "LOW" if entry_now_allowed else "HIGH",
            "reason": (
                "Candle is acceptable for immediate execution."
                if entry_now_allowed
                else "Candle timing is not yet suitable for entry."
            ),
        },
        "drawdown_first_warning": {
            "active": drawdown_first_warning_active,
            "side": candidate_side if candidate_side in {"BUY", "SELL"} else None,
            "expected_initial_adverse_move": "MEDIUM" if drawdown_first_warning_active else "LOW",
            "entry_now": "NOT_RECOMMENDED" if drawdown_first_warning_active else "RECOMMENDED",
            "better_entry_condition": (
                "wait for pullback to local demand or retest hold"
                if candidate_side == "BUY"
                else "wait for pullback to local supply or retest fail"
                if candidate_side == "SELL"
                else "wait for better timing confirmation"
            ),
        },
        "timing_memory": _mapping(
            snapshot.get("timing_memory")
            or market.get("timing_memory")
            or market_reality.get("timing_memory")
        ),
    }
    timed_reasoning = analyze_reasoning_arbitration_v3(
        snapshot,
        side=candidate_side,
        market_play=_mapping(market.get("market_play")),
        regime=_mapping(market.get("regime")),
        price_location=_mapping(market.get("price_location")),
        memory_confirmation=_mapping(market.get("memory_confirmation")),
        pair_profile=_mapping(market.get("pair_profile")),
        model_role_votes=_rows(market.get("model_role_outputs")),
        timing_decision=timing_decision,
        market_context=market_context,
        existing_block_reason=_upper(market.get("block_reason")),
    )
    reasoning_arbitration = _mapping(timed_reasoning.get("arbitration"))
    final_reasoning_decision = _mapping(timed_reasoning.get("final_reasoning_decision"))
    bad_entry_filter = _mapping(timed_reasoning.get("bad_entry_filter"))
    model_role_outputs = _rows(timed_reasoning.get("model_role_outputs"))
    reasoning_decision_state = _upper(final_reasoning_decision.get("decision"))
    reasoning_side = _side(final_reasoning_decision.get("side"))
    reasoning_side_mismatch = bool(
        candidate_side in {"BUY", "SELL"}
        and reasoning_side in {"BUY", "SELL"}
        and reasoning_side != candidate_side
    )
    reasoning_execution_blocked = bool(
        reasoning_decision_state in {
            "WATCH",
            "WAIT_FOR_PULLBACK",
            "WAIT_FOR_RETEST",
            "WAIT_FOR_REJECTION",
            "WAIT_FOR_BREAK_CONFIRMATION",
            "ABORT",
        }
        or reasoning_side_mismatch
    )
    reasoning_block_reason = (
        "REASONING_SIDE_MISMATCH"
        if reasoning_side_mismatch
        else f"REASONING_{reasoning_decision_state}"
        if reasoning_execution_blocked
        else ""
    )
    selected_wave_context = _mapping(execution_lane.get("wave_context"))
    reasoning_bad_entry_class = _upper(bad_entry_filter.get("class"))
    market_bad_entry_class = _upper(
        bad_entry.get("class")
        or bad_entry.get("class_id")
        or bad_entry.get("bad_entry_class")
        or bad_entry.get("reason")
    )
    hard_bad_entry_classes = {
        item
        for item in (reasoning_bad_entry_class, market_bad_entry_class)
        if item in WAVE_REASONING_HARD_BAD_CLASSES
    }
    late_chase_block_overridden = bool(
        stale_late_chase_overridden
        and live_trigger_reaction.get("accepted")
        and market_block_reason in LATE_CHASE_BLOCK_REASONS
        and current_candle_ok
        and not trap_active
        and opposing_force_ok
    )
    late_chase_bad_entry_override_allowed = bool(
        stale_late_chase_overridden
        and live_trigger_reaction.get("accepted")
        and hard_bad_entry_classes
        and hard_bad_entry_classes.issubset(LATE_CHASE_BLOCK_REASONS)
        and current_candle_ok
        and not trap_active
        and opposing_force_ok
    )
    hard_bad_entry_class_active = bool(
        hard_bad_entry_classes
        and not late_chase_bad_entry_override_allowed
    )
    bad_entry_filter_hard_active = bool(
        bad_entry_filter.get("active")
        and _clip01(bad_entry_filter.get("severity"), 0.0) >= 0.72
        and not late_chase_bad_entry_override_allowed
    )
    bad_entry_detected_effective = bool(
        bad_entry.get("detected")
        and not (
            stale_late_chase_overridden
            and live_trigger_reaction.get("accepted")
            and market_bad_entry_class in LATE_CHASE_BLOCK_REASONS
            and current_candle_ok
            and not trap_active
            and opposing_force_ok
        )
    )
    history_exit_active = _bool(
        _mapping(snapshot.get("historical_pattern")).get("would_have_exited_here")
        or market_context.get("history_would_exit_here")
    )
    wave_reasoning_override_allowed = bool(
        lane_name == "WAVE_RIDING_CONTINUATION"
        and bool(execution_lane.get("accepted"))
        and reasoning_decision_state in WAVE_REASONING_SOFT_WAIT_STATES
        and not reasoning_side_mismatch
        and not hard_bad_entry_class_active
        and not bad_entry_filter_hard_active
        and not bad_entry_detected_effective
        and not trap_active
        and not late_chase
        and not history_exit_active
        and current_candle_ok
        and opposing_force_ok
        and path_class == "DIRECT_CONTINUATION"
        and timing_mode == "ENTER_NOW"
        and bool(selected_wave_context.get("wave_entry_ok"))
        and bool(selected_wave_context.get("buy_low_sell_high_ok"))
        and bool(selected_wave_context.get("opposing_force_ok"))
        and (
            bool(selected_wave_context.get("pullback_reclaim_ready"))
            or bool(selected_wave_context.get("breakout_role_flip_ready"))
            or bool(selected_wave_context.get("strong_confluence_override"))
            or bool(
                selected_wave_context.get("continuation_ready")
                and selected_wave_context.get("clear_path_ready")
            )
        )
        and (
            bool(selected_wave_context.get("clear_path_ready"))
            or _clip01(selected_wave_context.get("clear_path_score"), 0.0) >= 0.70
        )
    )
    high_frequency_contribution_for_override = _mapping(execution_lane.get("high_frequency_contribution"))
    high_frequency_cycle_for_override = _mapping(high_frequency_contribution_for_override.get("high_frequency_candle_cycle"))
    high_frequency_soft_wait_only = bool(
        high_frequency_contribution_for_override.get("active")
        and not bool(high_frequency_contribution_for_override.get("lane_authority"))
        and _upper(high_frequency_cycle_for_override.get("lane")) == "HIGH_FREQUENCY_TWO_CANDLE"
    )
    high_frequency_wait_blocks_intraday = bool(
        high_frequency_soft_wait_only
        and not (
            bool(live_trigger_reaction.get("accepted"))
            and lane_name in INTRADAY_ENTER_NOW_LANES
            and timing_mode == "ENTER_NOW"
        )
    )
    intraday_enter_now_reasoning_override_allowed = bool(
        entry_now_allowed
        and timing_mode == "ENTER_NOW"
        and lane_name in INTRADAY_ENTER_NOW_LANES
        and bool(execution_lane.get("accepted"))
        and reasoning_decision_state in INTRADAY_ENTER_NOW_REASONING_SOFT_WAIT_STATES
        and not high_frequency_wait_blocks_intraday
        and not reasoning_side_mismatch
        and not hard_bad_entry_class_active
        and not bad_entry_filter_hard_active
        and not bad_entry_detected_effective
        and not trap_active
        and not late_chase
        and not history_exit_active
        and current_candle_ok
        and opposing_force_ok
        and path_class in {"DIRECT_CONTINUATION", "PULLBACK_THEN_CONTINUATION"}
    )
    if wave_reasoning_override_allowed or intraday_enter_now_reasoning_override_allowed:
        reasoning_execution_blocked = False
        reasoning_block_reason = ""
    permission_failed_reasons = _permission_failed_reasons(trade_permission)
    lane_permission_override = bool(
        execution_lane.get("permission_override_allowed")
        and permission_failed_reasons
        and permission_failed_reasons.issubset(LANE_SOFT_PERMISSION_REASONS)
    )
    lane_market_override = bool(
        execution_lane.get("accepted")
        and market_block_reason
        and (market_block_reason in LANE_SOFT_MARKET_BLOCK_REASONS or late_chase_block_overridden)
        and not bad_entry_detected_effective
        and not trap_active
    )
    market_blocked_effective = bool(market_blocked and not lane_market_override)
    permission_denied_effective = bool(permission_denied and not lane_permission_override)
    if lane_permission_override:
        effective_entry_quality = _mapping(entry_quality_surface)
        effective_entry_quality.setdefault("raw_state", entry_quality_label)
        effective_entry_quality.update(
            {
                "state": "AGGRESSIVE_VALID_ENTRY",
                "entry_grade": "AGGRESSIVE_VALID_ENTRY",
                "quality": "AGGRESSIVE_VALID_ENTRY",
                "score": max(_clip01(effective_entry_quality.get("score"), 0.0), _clip01(execution_lane.get("actual_score"), 0.0)),
                "entry_score": max(_clip01(effective_entry_quality.get("entry_score"), 0.0), _clip01(execution_lane.get("actual_score"), 0.0)),
                "rank": max(_int(effective_entry_quality.get("rank"), 0), 2),
                "passes_executable_threshold": True,
                "lane_override": True,
                "lane": execution_lane.get("name"),
                "reason": f"{execution_lane.get('name')} accepted a valid lane entry; raw entry quality was {entry_quality_label}.",
            }
        )
        entry_quality_surface = effective_entry_quality
        entry_quality_label = _entry_quality_label(entry_quality_surface) or entry_quality_label
        effective_trade_permission = _mapping(trade_permission)
        failed_reasons: list[str] = []
        raw_failed = trade_permission.get("failed_reasons")
        if isinstance(raw_failed, Sequence) and not isinstance(raw_failed, (str, bytes, bytearray)):
            failed_reasons = [
                str(reason)
                for reason in cast(Sequence[Any], raw_failed)
                if _upper(reason) not in LANE_SOFT_PERMISSION_REASONS
            ]
        effective_trade_permission.update(
            {
                "permission_state": "GRANTED",
                "side": candidate_side if candidate_side in {"BUY", "SELL"} else None,
                "executable_allowed": True,
                "prepare_allowed": True,
                "deny_reason": None,
                "failed_reasons": failed_reasons,
                "lane_override": True,
                "lane": execution_lane.get("name"),
                "reason": f"Execution permission granted by accepted {execution_lane.get('name')} lane.",
            }
        )
        trade_permission = effective_trade_permission
    entry_quality_adjustment = 0.05 if lane_effective_entry_quality_ok else -0.08
    context_adjustment = 0.0 if context_ok else -0.30
    timing_adjustment = 0.0 if lane_effective_timing_ready else -0.08
    maturity_adjustment = 0.0 if lane_effective_mature else -0.04
    permission_adjustment = -0.18 if permission_denied_effective else 0.0
    market_reality_adjustment = (
        entry_quality_adjustment
        + trap_penalty
        + path_risk_adjustment
        + flip_flop_penalty
        + context_adjustment
        + timing_adjustment
        + maturity_adjustment
        + permission_adjustment
    )
    final_execution_score = _clip01(raw_council_score + market_reality_adjustment)
    final_score_passed = final_execution_score >= lane_required_score
    stable = preliminary_stable
    permission_hard_block = bool(permission_denied_effective and not permission_prepare_allowed)
    candidate_invalidated = _bool(
        snapshot.get("candidate_invalidated")
        or snapshot.get("previous_side_invalidated")
        or snapshot.get("confirmed_reversal")
    )
    opportunity_maturity = _opportunity_maturity_v3(
        candidate_side=candidate_side,
        runtime_blocked=runtime_blocked,
        candidate_invalidated=candidate_invalidated,
        side_ok=side_ok,
        context_ok=context_ok,
        lane_effective_timing_ready=lane_effective_timing_ready,
        lane_effective_mature=lane_effective_mature,
        stable=stable,
        final_score_passed=final_score_passed,
        timing_has_explicit_expiry=timing_has_explicit_expiry,
        timing_mode=timing_mode,
        entry_now_allowed=entry_now_allowed,
        current_candle_ok=current_candle_ok,
        trap_active=trap_active,
        late_chase=late_chase,
        opposing_force_ok=opposing_force_ok,
        path_class=path_class,
        reasoning_execution_blocked=reasoning_execution_blocked,
        reasoning_block_reason=reasoning_block_reason,
        hard_bad_entry_class_active=hard_bad_entry_class_active,
        bad_entry_filter_hard_active=bad_entry_filter_hard_active,
        bad_entry_detected_effective=bad_entry_detected_effective,
        history_exit_active=history_exit_active,
        permission_denied_effective=permission_denied_effective,
        permission_prepare_allowed=permission_prepare_allowed,
        final_execution_score=final_execution_score,
        lane_required_score=lane_required_score,
        execution_lane=execution_lane,
    )
    opportunity_maturity_state = _upper(opportunity_maturity.get("state"), "NO_OPPORTUNITY")

    final_state = "WATCHING"
    block_reason: str | None = None
    executable = False
    if both_executable_requested or (buy_score >= 0.62 and sell_score >= 0.62):
        final_state = "CONFLICT"
        candidate_side = "HOLD"
        block_reason = "BUY_AND_SELL_EXECUTABLE_CONFLICT"
    elif runtime_blocked:
        final_state = "BLOCKED_BY_RUNTIME"
        block_reason = "REQUIRED_MODELS_NOT_AWAKE" if models_not_awake else study_identity_validation.first_reason
    elif flip_flop_contained:
        final_state = "WATCHING"
        block_reason = "FLIP_FLOP_CONTAINED"
    elif market_blocked_effective or permission_hard_block:
        final_state = "WATCHING"
        block_reason = str(market_block_reason or permission_block_reason or "BLOCKED_BY_MARKET")
    elif permission_denied_effective and context_ok:
        final_state = "PREPARING" if side_ok and context_ok and permission_prepare_allowed else "WATCHING"
        block_reason = permission_block_reason
    elif side_ok and context_ok and lane_effective_timing_ready and lane_effective_mature and stable and final_score_passed:
        if not timing_has_explicit_expiry:
            final_state = "BLOCKED_BY_RUNTIME"
            block_reason = "MODEL_COUNCIL_EXPLICIT_EXPIRY_MISSING"
        elif reasoning_execution_blocked:
            final_state = "PREPARING"
            block_reason = reasoning_block_reason
        elif opportunity_maturity_state != "ENTER_NOW":
            final_state = "PREPARING" if opportunity_maturity_state in {"PREPARE", "VALID_WATCH", "EARLY_FORMING"} else "WATCHING"
            block_reason = f"OPPORTUNITY_MATURITY_{opportunity_maturity_state}"
        elif not timing_decision["entry_now_allowed"] or timing_mode != "ENTER_NOW":
            final_state = "PREPARING"
            block_reason = f"TIMING_MODE_{timing_mode}"
        elif packet_identity_validation.ok:
            final_state = "EXECUTABLE"
            executable = True
        else:
            final_state = "BLOCKED_BY_RUNTIME"
            block_reason = packet_identity_validation.first_reason
    elif side_ok and context_ok and lane_effective_timing_ready and lane_effective_mature and stable:
        final_state = "PREPARING"
        block_reason = "LANE_SCORE_BELOW_THRESHOLD"
    elif side_ok and context_ok:
        final_state = "PREPARING"
    elif side_ok:
        final_state = "WATCHING"

    base_snapshot = dict(snapshot)
    base_snapshot["instrument_context"] = instrument_context
    base = _packet_base(base_snapshot, current_now)
    active_candidate_id = _candidate_id(
        snapshot,
        side=candidate_side,
        market_context=market_context,
        entry_quality=entry_quality_surface,
    )
    entry_quality_label = _entry_quality_label(entry_quality_surface) or "UNKNOWN"
    effective_permission_state = "GRANTED" if executable or lane_permission_override else "DENIED" if permission_denied_effective else "PENDING"
    permission_state = str(
        effective_permission_state
        if lane_permission_override or executable or permission_denied_effective
        else trade_permission.get("permission_state")
        or effective_permission_state
    ).strip().upper()
    if executable:
        promotion_result = "EXECUTABLE_PACKET_CREATED"
    elif final_state == "PREPARING":
        promotion_result = "PREPARING"
    elif flip_flop_contained:
        promotion_result = "WAITING"
    else:
        promotion_result = final_state
    if block_reason:
        blocked_by = block_reason
    elif flip_flop_contained:
        blocked_by = "candidate_flip_count"
    elif not side_ok:
        blocked_by = "candidate_side"
    elif not context_ok:
        blocked_by = "NO_EXECUTION_LANE_ACCEPTED"
    elif not lane_effective_timing_ready:
        blocked_by = "timing"
    elif not lane_effective_mature:
        blocked_by = "candidate_maturity"
    elif not stable:
        blocked_by = "dominance_margin"
    elif not final_score_passed:
        blocked_by = "LANE_SCORE_BELOW_THRESHOLD"
    else:
        blocked_by = None
    true_blocker = str(block_reason or blocked_by or "NONE").strip().upper()
    candidate_stage = (
        "EXECUTION_PACKET_PUBLISHED"
        if executable
        else "PREPARING"
        if final_state == "PREPARING"
        else "CANDIDATE_STABLE"
        if side_ok and stable
        else "CANDIDATE_CREATED"
        if side_ok
        else "OBSERVATION"
    )
    lane_release_requirements = _lane_release_requirements(
        execution_lane,
        final_score=final_execution_score,
        lane_required_score=lane_required_score,
    )
    if executable:
        next_required = "none"
    elif flip_flop_contained:
        next_required = (
            f"candidate_stage=CANDIDATE_STABLE; same candidate side for {max(0, _int(snapshot.get('flip_flop_release_stable_reads'), 2) - candidate_stable_reads)} more read(s); dominance_margin >= {min_dominance_margin:.2f}; entry_quality_ok=true; timing_mode=ENTER_NOW"
        )
    elif not context_ok:
        next_required = lane_release_requirements
    elif not lane_effective_mature:
        next_required = f"candidate_stage={candidate_stage}; next_required one more stable candidate read"
    elif not lane_effective_timing_ready or timing_mode != "ENTER_NOW":
        next_required = f"timing_mode={timing_mode}; next_required {timing_decision['entry_timing']['next_condition']}"
    elif not timing_has_explicit_expiry:
        next_required = "timing.expiry_seconds explicit and execution.time_sequence target exists"
    elif not final_score_passed:
        next_required = f"final_score={final_execution_score:.4f} >= threshold={lane_required_score:.4f} for selected_lane={execution_lane.get('name', 'execution lane')}"
    else:
        next_required = str(block_reason or "continue study")
    instrument_context_state = str(
        instrument_context.get("instrument_context_state")
        or instrument_context.get("identity_state_v2")
        or instrument_context.get("identity_state")
        or "UNKNOWN"
    ).strip().upper()
    instrument_release_condition = str(
        instrument_context.get("release_condition")
        or "stable locked broker surface evidence"
    ).strip()
    if true_blocker.startswith("INSTRUMENT_CONTEXT") or true_blocker in {
        "MISSING_TIMEFRAME",
        "INSTRUMENT_CONTEXT_INVALIDATED",
        "INSTRUMENT_CONTEXT_NOT_PAPER_SAFE",
        "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE",
    }:
        next_required = _instrument_release_requirement(instrument_context, instrument_release_condition)
    if final_state == "BLOCKED_BY_RUNTIME" or true_blocker.startswith("INSTRUMENT_CONTEXT"):
        runtime_field = "instrument_context" if true_blocker.startswith("INSTRUMENT_CONTEXT") else "runtime"
        _mark_opportunity_maturity_blocked(
            opportunity_maturity,
            state="VALID_WATCH",
            denied_at=true_blocker,
            next_required=next_required,
            field=runtime_field,
            received=true_blocker,
            required="hard runtime gates pass",
            reason=next_required,
            hard=True,
        )
        opportunity_maturity_state = _upper(opportunity_maturity.get("state"), "VALID_WATCH")
    opportunity_maturity["next_required"] = "publish validated PG_EXECUTION_PACKET_V3" if executable else next_required
    lane_blockers = [
        _upper(blocker)
        for blocker in execution_lane.get("blockers", [])
        if str(blocker or "").strip()
    ] if isinstance(execution_lane.get("blockers"), Sequence) and not isinstance(execution_lane.get("blockers"), (str, bytes, bytearray)) else []
    release_state = _non_executable_release_state(
        executable=executable,
        true_blocker=true_blocker,
        final_state=final_state,
        flip_flop_contained=flip_flop_contained,
        permission_denied_effective=permission_denied_effective,
        context_ok=context_ok,
        lane_effective_timing_ready=lane_effective_timing_ready,
        timing_mode=timing_mode,
        final_score_passed=final_score_passed,
        lane_score_blocked="LANE_SCORE_BELOW_THRESHOLD" in lane_blockers,
        lane_timing_blocked="CURRENT_CANDLE_NOT_ACCEPTED" in lane_blockers,
        packet_identity_mode=packet_identity_mode,
        instrument_context=instrument_context,
    )
    if executable:
        release_condition = "none"
    elif true_blocker.startswith("INSTRUMENT_CONTEXT") or final_state == "BLOCKED_BY_RUNTIME":
        release_condition = next_required
    elif flip_flop_contained:
        release_condition = "candidate_stage=CANDIDATE_STABLE + same candidate side + stable dominance + acceptable entry + timing_mode=ENTER_NOW"
    elif not context_ok:
        release_condition = lane_release_requirements
    elif not lane_effective_mature:
        release_condition = "candidate_stage=CANDIDATE_STABLE/PREPARING"
    elif not lane_effective_timing_ready or timing_mode != "ENTER_NOW":
        release_condition = f"timing_mode=ENTER_NOW; {timing_decision['entry_timing']['next_condition']}"
    elif not stable:
        release_condition = f"dominance_margin >= {min_dominance_margin:.2f}"
    elif not final_score_passed:
        release_condition = f"final_score >= threshold ({final_execution_score:.4f}/{lane_required_score:.4f})"
    else:
        release_condition = next_required
    missed_opportunity = _missed_opportunity_probe(
        candidate_side=candidate_side,
        execution_lane=execution_lane,
        raw_council_score=raw_council_score,
        final_execution_score=final_execution_score,
        true_blocker=true_blocker,
    )
    trade_candidate_queue_raw = market.get("trade_candidate_queue", _mapping(market_reality.get("trade_candidate_queue")))
    trade_candidate_queue: dict[str, Any]
    if isinstance(trade_candidate_queue_raw, Mapping):
        trade_candidate_queue = _mapping(trade_candidate_queue_raw)
    elif isinstance(trade_candidate_queue_raw, Sequence) and not isinstance(trade_candidate_queue_raw, (str, bytes, bytearray)):
        trade_candidate_queue = {"candidates": _rows(trade_candidate_queue_raw)}
    else:
        trade_candidate_queue = {}
    active_candidate: dict[str, Any] = {
        "candidate_id": active_candidate_id,
        "side": candidate_side if candidate_side in {"BUY", "SELL"} else "HOLD",
        "stage": candidate_stage,
        "maturity_stage": "EXECUTABLE_PACKET" if executable else maturity_stage,
        "stable_reads": candidate_stable_reads,
        "raw_side_flips": raw_flip_count,
        "candidate_flip_count_10s": candidate_flip_count,
        "candidate_invalidated": candidate_invalidated,
        "entry_quality": entry_quality_label,
        "permission": permission_state,
        "opportunity_maturity": opportunity_maturity_state,
        "opportunity_maturity_confidence": opportunity_maturity["confidence"],
        "flip_flop_risk": flip_flop_contained,
        "release_allowed": flip_flop_release_allowed,
        "raw_recent_sides": raw_recent_sides,
        "candidate_recent_sides": candidate_recent_sides,
    }
    trade_candidate_queue.update(
        {
            "active_candidate": active_candidate,
            "candidate_id": active_candidate_id,
            "candidate_side": candidate_side if candidate_side in {"BUY", "SELL"} else "HOLD",
            "candidate_stage": active_candidate["stage"],
            "candidate_stable_reads": candidate_stable_reads,
            "raw_side_flips": raw_flip_count,
            "candidate_flip_count_10s": candidate_flip_count,
            "candidate_invalidated": candidate_invalidated,
            "opportunity_maturity": opportunity_maturity,
            "opportunity_maturity_state": opportunity_maturity_state,
            "flip_flop_risk": flip_flop_contained,
            "flip_flop_release_allowed": flip_flop_release_allowed,
        }
    )
    allowance_package = _build_allowance_package_v1(
        candidate_side=candidate_side,
        timing_mode=timing_mode,
        timing_decision=timing_decision,
        execution_lane=execution_lane,
        final_execution_score=final_execution_score,
        lane_required_score=lane_required_score,
        executable=executable,
        final_state=final_state,
        true_blocker=true_blocker,
        next_required=next_required,
        release_state=release_state,
        promotion_result=promotion_result,
        path_class=path_class,
        preferred_expiry_seconds=preferred_expiry_seconds,
        final_score_passed=final_score_passed,
        intraday_reasoning_override_allowed=intraday_enter_now_reasoning_override_allowed,
        wave_reasoning_override_allowed=wave_reasoning_override_allowed,
        trap_active=trap_active,
        late_chase=late_chase,
        opposing_force_ok=opposing_force_ok,
        hard_bad_entry_class_active=hard_bad_entry_class_active,
        opportunity_maturity=opportunity_maturity,
    )
    promotion_trace: dict[str, Any] = {
        "packet_id": base["packet_id"],
        "release_state": release_state,
        "non_executable_state": None if executable else release_state,
        "raw_side": raw_side,
        "previous_raw_side": raw_recent_sides[-2] if len(raw_recent_sides) >= 2 else None,
        "candidate_side": candidate_side if candidate_side in {"BUY", "SELL"} else "HOLD",
        "previous_candidate_side": candidate_recent_sides[-2] if len(candidate_recent_sides) >= 2 else None,
        "candidate_id": active_candidate_id,
        "candidate_stage": candidate_stage,
        "opportunity_maturity": opportunity_maturity,
        "opportunity_maturity_state": opportunity_maturity_state,
        "visual_integrity": opportunity_maturity.get("visual_integrity"),
        "candidate_stable_reads": candidate_stable_reads,
        "raw_flip_count_10s": raw_flip_count,
        "candidate_flip_count_10s": candidate_flip_count,
        "dominance_margin": round(float(dominance_margin), 4),
        "entry_quality": entry_quality_label,
        "entry_quality_ok": lane_effective_entry_quality_ok,
        "raw_entry_quality_ok": entry_quality_ok,
        "timing_ready": lane_effective_timing_ready,
        "raw_timing_ready": timing_ready,
        "timing_mode": timing_mode,
        "timing_has_explicit_expiry": timing_has_explicit_expiry,
        "timing_decision": timing_decision,
        "late_chase_detected": bool(
            bad_entry.get("detected")
            or market_trap.get("late_chase_detected")
            or str(market_trap.get("trap_type") or market_trap.get("primary_trap") or "").upper().startswith("LATE_CHASE")
        ),
        "trap_active": trap_active,
        "path_quality": "ACCEPTABLE" if opposing_force_ok else "OPPOSING_FORCE_TOO_CLOSE",
        "opposing_force_ok": opposing_force_ok,
        "instrument_context_state": instrument_context_state,
        "instrument_context_broker_click_safe": bool(instrument_context.get("broker_click_safe")),
        "instrument_context_release_condition": instrument_release_condition,
        "instrument_context_evidence": _mapping(instrument_context.get("evidence")),
        "permission": permission_state,
        "permission_override_allowed": lane_permission_override,
        "market_block_override_allowed": lane_market_override,
        "reasoning_decision_state": reasoning_decision_state,
        "reasoning_execution_blocked": reasoning_execution_blocked,
        "reasoning_block_reason": reasoning_block_reason,
        "intraday_enter_now_reasoning_override_allowed": intraday_enter_now_reasoning_override_allowed,
        "high_frequency_soft_wait_only": high_frequency_soft_wait_only,
        "high_frequency_wait_blocks_intraday": high_frequency_wait_blocks_intraday,
        "wave_reasoning_override_allowed": wave_reasoning_override_allowed,
        "reasoning_bad_entry_class": reasoning_bad_entry_class,
        "market_bad_entry_class": market_bad_entry_class,
        "bad_entry_detected_effective": bad_entry_detected_effective,
        "bad_entry_filter_hard_active": bad_entry_filter_hard_active,
        "hard_bad_entry_class_active": hard_bad_entry_class_active,
        "late_chase_block_overridden": late_chase_block_overridden,
        "late_chase_bad_entry_override_allowed": late_chase_bad_entry_override_allowed,
        "denied_at": (
            true_blocker
            if true_blocker != "NONE"
            else blocked_by
            if blocked_by != "NONE"
            else promotion_result
        ),
        "base_council_score": round(float(base_council_score), 4),
        "ai_strength_multiplier": round(float(ai_strength_multiplier), 4),
        "raw_council_score": round(float(raw_council_score), 4),
        "market_reality_adjustment": round(float(market_reality_adjustment), 4),
        "final_execution_score": round(float(final_execution_score), 4),
        "final_score": round(float(final_execution_score), 4),
        "execution_threshold": round(float(execution_threshold), 4),
        "lane_threshold": round(float(lane_required_score), 4),
        "threshold": round(float(lane_required_score), 4),
        "execution_lane": execution_lane,
        "selected_lane": execution_lane.get("name"),
        "lane_accepted": bool(execution_lane.get("accepted")),
        "accepted_lanes": execution_lane.get("accepted_lanes", []),
        "stale_dominant_overridden": bool(execution_lane.get("stale_dominant_overridden")),
        "raw_late_chase": bool(execution_lane.get("raw_late_chase", late_chase_raw)),
        "effective_late_chase": bool(execution_lane.get("effective_late_chase", late_chase)),
        "stale_late_chase_overridden": stale_late_chase_overridden,
        "live_trigger_reaction": live_trigger_reaction,
        "structural_flow_ready": bool(execution_lane.get("structural_flow_ready")),
        "reversal_capture_mature": bool(execution_lane.get("reversal_capture_mature")),
        "mature_directional_flow_ready": bool(execution_lane.get("mature_directional_flow_ready")),
        "opportunity_capture_mode": bool(execution_lane.get("opportunity_capture_mode")),
        "current_candle_acceptance": execution_lane.get("current_candle_acceptance", {}),
        "wave_context": execution_lane.get("wave_context", {}),
        "release_allowed": flip_flop_release_allowed,
        "blocked_by": blocked_by,
        "true_blocker": true_blocker,
        "next_required": next_required,
        "release_condition": release_condition,
        "promotion_result": promotion_result,
        "packet_result": "PG_EXECUTION_PACKET_V3_PUBLISHED" if executable else "STUDY_PACKET_PUBLISHED",
        "missed_opportunity": missed_opportunity,
        "reasoning_state": final_reasoning_decision.get("decision"),
        "reasoning_play": final_reasoning_decision.get("play"),
        "reasoning_regime": final_reasoning_decision.get("regime"),
        "reasoning_price_location": final_reasoning_decision.get("price_location"),
        "reasoning_coherence_score": reasoning_arbitration.get("coherence_score"),
        "bad_entry_filter": bad_entry_filter,
        "ai_contribution_strengths": ai_contribution_strengths,
        "model_strength_profile": model_strength_profile,
        "lane_thresholds": _lane_thresholds(snapshot),
        "allowance_package": allowance_package,
    }
    council_scores = {
        "global": round(float(_clip01(market.get("global_score"), raw_council_score)), 4),
        "local": round(float(_clip01(market.get("local_score"), raw_council_score)), 4),
        "zone": round(float(_clip01(market.get("zone_score"), raw_council_score)), 4),
        "angle": round(float(_clip01(market.get("angle_score"), raw_council_score)), 4),
        "history": round(float(_clip01(market.get("history_score"), raw_council_score)), 4),
        "risk": round(float(_clip01(market.get("risk_score"), raw_council_score)), 4),
        "arbitration": round(float(_clip01(market.get("arbitration_score"), raw_council_score)), 4),
        "raw_council_score": round(float(raw_council_score), 4),
    }
    reality_adjustments = {
        "entry_quality": round(float(entry_quality_adjustment), 4),
        "trap_penalty": round(float(trap_penalty), 4),
        "path_risk": round(float(path_risk_adjustment), 4),
        "flip_flop_penalty": round(float(flip_flop_penalty), 4),
        "context": round(float(context_adjustment), 4),
        "timing": round(float(timing_adjustment), 4),
        "maturity": round(float(maturity_adjustment), 4),
        "permission": round(float(permission_adjustment), 4),
        "market_reality_adjustment": round(float(market_reality_adjustment), 4),
    }
    council: dict[str, Any] = {
        "final_state": final_state,
        "final_side": candidate_side if side_ok and final_state != "CONFLICT" else None,
        "decision_id": "mc_" + hashlib.sha1(f"{current_now}|{candidate_side}|{buy_score}|{sell_score}".encode("utf-8")).hexdigest()[:18],
        "maturity_stage": "EXECUTABLE_PACKET" if executable else maturity_stage,
        "opportunity_maturity": opportunity_maturity,
        "opportunity_maturity_state": opportunity_maturity_state,
        "arbitration_reason": (
            f"{candidate_side} executable via {execution_lane.get('name')}: {execution_lane.get('reason')}"
            if executable
            else str(
                block_reason
                or (
                    f"{promotion_result}: blocked_by={true_blocker}; next_required={next_required}"
                    if true_blocker != "NONE"
                    else f"{promotion_result}: {next_required}"
                )
            )
        ),
        "buy_score": round(float(buy_score), 4),
        "sell_score": round(float(sell_score), 4),
        "hold_score": round(float(hold_score), 4),
        "dominance_margin": round(float(dominance_margin), 4),
        "disagreement_score": round(float(disagreement_score), 4),
        "council_scores": council_scores,
        "reality_adjustments": reality_adjustments,
        "base_council_score": round(float(base_council_score), 4),
        "ai_strength_multiplier": round(float(ai_strength_multiplier), 4),
        "raw_council_score": round(float(raw_council_score), 4),
        "final_execution_score": round(float(final_execution_score), 4),
        "execution_threshold": round(float(execution_threshold), 4),
        "lane_threshold": round(float(lane_required_score), 4),
        "execution_lane": execution_lane,
        "selected_execution_lane": execution_lane.get("name"),
        "true_blocker": true_blocker,
        "release_state": release_state,
        "non_executable_state": None if executable else release_state,
        "denied_at": promotion_trace["denied_at"],
        "next_required": next_required,
        "release_condition": release_condition,
        "candidate_id": active_candidate_id,
        "candidate_stage": candidate_stage,
        "final_score": round(float(final_execution_score), 4),
        "threshold": round(float(lane_required_score), 4),
        "selected_lane": execution_lane.get("name"),
        "timing_mode": timing_mode,
        "instrument_context_state": instrument_context_state,
        "instrument_context_broker_click_safe": bool(instrument_context.get("broker_click_safe")),
        "flip_flop_state": (
            "FLIP_FLOP_RELEASED"
            if flip_flop_release_allowed
            else "FLIP_FLOP_CONTAINED"
            if flip_flop_contained
            else ("STABLE_EXECUTABLE" if executable else "STUDYING")
        ),
        "contributors_are_diagnostic": True,
        "ai_contribution_strengths": ai_contribution_strengths,
        "model_strength_profile": model_strength_profile,
        "lane_thresholds": _lane_thresholds(snapshot),
        "skill_contributions": skill_contributions,
        "two_candle_study": two_candle_study,
        "lstm_contribution": lstm_contribution,
        "entry_quality": entry_quality_surface,
        "trade_permission": trade_permission,
        "promotion_trace": promotion_trace,
        "trade_candidate_queue": trade_candidate_queue,
        "timing_decision": timing_decision,
        "timing_forecast": timing_forecast,
        "regime": market.get("regime", {}),
        "market_play": market.get("market_play", {}),
        "price_location": market.get("price_location", {}),
        "memory_confirmation": market.get("memory_confirmation", {}),
        "pair_profile": market.get("pair_profile", {}),
        "model_role_outputs": model_role_outputs,
        "reasoning_arbitration": reasoning_arbitration,
        "bad_entry_filter": bad_entry_filter,
        "final_reasoning_decision": final_reasoning_decision,
        "primary_play": final_reasoning_decision.get("play"),
        "regime_primary": _mapping(market.get("regime")).get("primary"),
        "reasoning_decision": final_reasoning_decision.get("decision"),
        "reasoning_coherence_score": reasoning_arbitration.get("coherence_score"),
        "reasoning_execution_blocked": reasoning_execution_blocked,
        "reasoning_block_reason": reasoning_block_reason,
        "intraday_enter_now_reasoning_override_allowed": intraday_enter_now_reasoning_override_allowed,
        "wave_reasoning_override_allowed": wave_reasoning_override_allowed,
        "allowance_package": allowance_package,
    }
    council_debate = _council_debate(
        candidate_side=candidate_side,
        buy_score=buy_score,
        sell_score=sell_score,
        final_state=final_state,
        market=market,
        market_context=market_context,
        entry_quality=_mapping(entry_quality_surface),
        trade_permission=_mapping(trade_permission),
        block_reason=block_reason,
    )
    study_side = candidate_side if side_ok and (executable or context_ok or final_state == "PREPARING") else None
    execution: dict[str, Any] = {
        "enabled": executable,
        "state": "EXECUTABLE" if executable else final_state,
        "side": study_side,
        "expiry_seconds": timing_expiry if executable else 0,
        "amount_action": "DO_NOT_CHANGE_AMOUNT",
        "allowance_package_type": allowance_package["package_type"],
    }
    result: dict[str, Any] = {
        "schema_version": MODEL_COUNCIL_STUDY_SCHEMA_VERSION,
        "packet_id": base["packet_id"],
        "packet_type": "STUDY_PACKET",
        "execution": execution,
        "allowance_package": allowance_package,
        "model_council": council,
        "promotion_trace": promotion_trace,
        "council_scores": council_scores,
        "reality_adjustments": reality_adjustments,
        "execution_lane": execution_lane,
        "selected_execution_lane": execution_lane.get("name"),
        "release_state": release_state,
        "non_executable_state": None if executable else release_state,
        "missed_opportunity": missed_opportunity,
        "opportunity_maturity": opportunity_maturity,
        "opportunity_maturity_state": opportunity_maturity_state,
        "final_execution_score": round(float(final_execution_score), 4),
        "final_score": round(float(final_execution_score), 4),
        "execution_threshold": round(float(execution_threshold), 4),
        "threshold": round(float(lane_required_score), 4),
        "base_council_score": round(float(base_council_score), 4),
        "ai_strength_multiplier": round(float(ai_strength_multiplier), 4),
        "lane_thresholds": _lane_thresholds(snapshot),
        "ai_contribution_strengths": ai_contribution_strengths,
        "model_strength_profile": model_strength_profile,
        "market_context": market_context,
        "two_candle_study": two_candle_study,
        "lstm_contribution": lstm_contribution,
        "skill_contributions": skill_contributions,
        "angle_context": market.get("angle_context", {}),
        "history_context": market.get("history_context", {}),
        "market_reality": market_reality,
        "entry_quality": entry_quality_surface,
        "trade_permission": trade_permission,
        "market_trap": market_trap,
        "ideal_trade_path": market.get("ideal_trade_path", _mapping(market_reality.get("ideal_trade_path"))),
        "path_risk": market.get("path_risk", _mapping(market_reality.get("path_risk"))),
        "regime_playbook": market.get("regime_playbook", _mapping(market_reality.get("regime_playbook"))),
        "time_to_reward_invalidation": market.get(
            "time_to_reward_invalidation",
            _mapping(market_reality.get("time_to_reward_invalidation")),
        ),
        "current_candle_contract": market.get(
            "current_candle_contract",
            _mapping(market_reality.get("current_candle_contract")),
        ),
        "market_listening_stream": market.get(
            "market_listening_stream",
            _mapping(market_reality.get("market_listening_stream")),
        ),
        "trade_candidate_queue": trade_candidate_queue,
        "council_debate": council_debate,
        "timing_decision": timing_decision,
        "timing_forecast": timing_forecast,
        "regime": market.get("regime", {}),
        "market_play": market.get("market_play", {}),
        "price_location": market.get("price_location", {}),
        "memory_confirmation": market.get("memory_confirmation", {}),
        "pair_profile": market.get("pair_profile", {}),
        "model_role_outputs": model_role_outputs,
        "reasoning_arbitration": reasoning_arbitration,
        "bad_entry_filter": bad_entry_filter,
        "final_reasoning_decision": final_reasoning_decision,
        "runtime_model_health": health,
        "instrument_context": instrument_context,
        "symbol_context": symbol_context,
        "instrument_context_validation": packet_identity_validation.as_dict(),
        "block_reason": block_reason,
        "contributors": {
            "contributors_are_diagnostic": True,
            "ai_contribution_strengths": ai_contribution_strengths,
            "model_strength_profile": model_strength_profile,
            "skill_gates": _diagnostic_skill_gates(snapshot),
            "skill_contributions": skill_contributions,
            "lstm_candle_sequence": lstm_contribution,
            "two_candle_study": two_candle_study,
            "memory": snapshot.get("memory", snapshot.get("memory_similarity", {})),
            "decision_kernel": snapshot.get("decision_kernel", {}),
            "market_agents": market.get("agents", []),
            "market_reality": market_reality,
            "model_role_outputs": model_role_outputs,
            "play_reasoning": final_reasoning_decision,
        },
    }
    study_packet_valid_for_seconds = _float(snapshot.get("study_packet_valid_for_seconds"), 20.0)
    study_packet: dict[str, Any] = {
        "schema_version": MODEL_COUNCIL_STUDY_SCHEMA_VERSION,
        "packet_id": base["packet_id"],
        "packet_type": "STUDY_PACKET",
        "session_id": base["session_id"],
        "symbol": base["symbol"],
        "timeframe": base["timeframe"],
        "frame_id": base["frame_id"],
        "capture_count": base["capture_count"],
        "state_version": base["state_version"],
        "created_epoch": current_now,
        "created_epoch_sec": current_now,
        "valid_until_epoch": current_now + study_packet_valid_for_seconds,
        "valid_until_epoch_sec": current_now + study_packet_valid_for_seconds,
        "execution": execution,
        "model_council": council,
        "allowance_package": allowance_package,
        "block_reason": block_reason,
        "promotion_trace": promotion_trace,
        "reason": council["arbitration_reason"],
        "true_blocker": true_blocker,
        "release_state": release_state,
        "non_executable_state": None if executable else release_state,
        "denied_at": promotion_trace["denied_at"],
        "next_required": next_required,
        "release_condition": release_condition,
        "candidate_id": active_candidate_id,
        "candidate_stage": candidate_stage,
        "final_score": round(float(final_execution_score), 4),
        "threshold": round(float(lane_required_score), 4),
        "selected_lane": execution_lane.get("name"),
        "timing_mode": timing_mode,
        "instrument_context_state": instrument_context_state,
        "execution_lane": execution_lane,
        "selected_execution_lane": execution_lane.get("name"),
        "lane_thresholds": _lane_thresholds(snapshot),
        "ai_contribution_strengths": ai_contribution_strengths,
        "model_strength_profile": model_strength_profile,
        "missed_opportunity": missed_opportunity,
        "opportunity_maturity": opportunity_maturity,
        "opportunity_maturity_state": opportunity_maturity_state,
        "trade_candidate_queue": trade_candidate_queue,
        "council_scores": council_scores,
        "reality_adjustments": reality_adjustments,
        "base_council_score": round(float(base_council_score), 4),
        "ai_strength_multiplier": round(float(ai_strength_multiplier), 4),
        "two_candle_study": two_candle_study,
        "lstm_contribution": lstm_contribution,
        "skill_contributions": skill_contributions,
        "final_execution_score": round(float(final_execution_score), 4),
        "execution_threshold": round(float(execution_threshold), 4),
        "timing_decision": timing_decision,
        "timing_forecast": timing_forecast,
        "regime": market.get("regime", {}),
        "market_play": market.get("market_play", {}),
        "price_location": market.get("price_location", {}),
        "memory_confirmation": market.get("memory_confirmation", {}),
        "pair_profile": market.get("pair_profile", {}),
        "model_role_outputs": model_role_outputs,
        "reasoning_arbitration": reasoning_arbitration,
        "bad_entry_filter": bad_entry_filter,
        "final_reasoning_decision": final_reasoning_decision,
    }
    result["study_packet"] = study_packet
    result["model_council_study_packet"] = study_packet
    sequence_context = build_sequence_context_v3(
        snapshot,
        packet=base_snapshot,
    )
    sequence_context_payload = sequence_context.as_dict()
    sequence_readiness = sequence_context_readiness_report(
        sequence_context,
        source_module="model_council_resolver",
    )
    council["sequence_context"] = sequence_context_payload
    council["sequence_context_readiness"] = sequence_readiness
    council["sequence_id"] = sequence_context_payload["sequence_id"]
    council["sequence_signature"] = sequence_context_payload["sequence_signature"]
    council["sequence_length"] = sequence_context_payload["sequence_length"]
    council["frames_used"] = sequence_context_payload["frames_used"]
    council["sequence_status"] = sequence_context_payload["sequence_status"]
    council["sequence_confidence"] = sequence_context_payload["sequence_confidence"]

    def _refresh_promotion_failure_audit() -> dict[str, Any]:
        audit = build_promotion_failure_audit_v3(
            packet_id=base["packet_id"],
            candidate_id=active_candidate_id,
            promotion_trace=promotion_trace,
            sequence_context_readiness=sequence_readiness,
            execution_lane=execution_lane,
            final_score=final_execution_score,
            threshold=lane_required_score,
            timing_mode=timing_mode,
            instrument_context=instrument_context,
            packet_result=str(promotion_trace.get("packet_result") or study_packet.get("packet_result") or "STUDY_PACKET_PUBLISHED"),
            extra_source_fields={
                "release_state": promotion_trace.get("release_state"),
                "non_executable_state": promotion_trace.get("non_executable_state"),
                "blocked_by": promotion_trace.get("blocked_by"),
                "true_blocker": promotion_trace.get("true_blocker"),
                "opportunity_maturity_state": opportunity_maturity_state,
                "visual_integrity": opportunity_maturity.get("visual_integrity"),
                "opportunity_maturity_denied_at": opportunity_maturity.get("denied_at"),
            },
        )
        promotion_trace["promotion_failure_audit_v3"] = audit
        promotion_trace["allowance_package"] = allowance_package
        council["promotion_failure_audit_v3"] = audit
        council["promotion_trace"] = promotion_trace
        council["allowance_package"] = allowance_package
        study_packet["promotion_failure_audit_v3"] = audit
        study_packet["promotion_trace"] = promotion_trace
        study_packet["allowance_package"] = allowance_package
        result["promotion_failure_audit_v3"] = audit
        result["promotion_trace"] = promotion_trace
        result["model_council"] = council
        result["allowance_package"] = allowance_package
        result["study_packet"] = study_packet
        result["model_council_study_packet"] = study_packet
        return audit

    if not bool(sequence_readiness.get("ready")):
        executable = False
        block_reason = "SEQUENCE_CONTEXT"
        blocked_by = block_reason
        true_blocker = block_reason
        next_required = str(sequence_readiness.get("next_required") or "sequence context incomplete")
        release_condition = next_required
        _mark_opportunity_maturity_blocked(
            opportunity_maturity,
            state="VALID_WATCH",
            denied_at=block_reason,
            next_required=next_required,
            field=str(sequence_readiness.get("failed_module") or "sequence_context"),
            received=sequence_readiness.get("status") or "not_ready",
            required="COMPLETE sequence context",
            reason=next_required,
            hard=True,
        )
        opportunity_maturity_state = _upper(opportunity_maturity.get("state"), "VALID_WATCH")
        allowance_package["opportunity_maturity"] = opportunity_maturity_state
        allowance_package["opportunity_maturity_confidence"] = opportunity_maturity["confidence"]
        allowance_package["visual_integrity"] = opportunity_maturity.get("visual_integrity")
        promotion_trace["denied_at"] = block_reason
        promotion_trace["blocked_by"] = block_reason
        promotion_trace["true_blocker"] = block_reason
        promotion_trace["next_required"] = next_required
        promotion_trace["release_condition"] = next_required
        promotion_trace["sequence_context_readiness"] = sequence_readiness
        promotion_trace["opportunity_maturity"] = opportunity_maturity
        promotion_trace["opportunity_maturity_state"] = opportunity_maturity_state
        promotion_trace["visual_integrity"] = opportunity_maturity.get("visual_integrity")
        promotion_trace["promotion_result"] = "STUDY_PACKET_PUBLISHED"
        promotion_trace["packet_result"] = "STUDY_PACKET_PUBLISHED"
        _mark_allowance_package_blocked(
            allowance_package,
            block_reason=block_reason,
            next_required=next_required,
            release_state=release_state,
            final_state="WATCHING",
            promotion_result="STUDY_PACKET_PUBLISHED",
        )
        promotion_trace["allowance_package"] = allowance_package
        study_packet["denied_at"] = block_reason
        study_packet["next_required"] = next_required
        study_packet["release_condition"] = next_required
        study_packet["sequence_context_readiness"] = sequence_readiness
        study_packet["non_executable_state"] = release_state
        study_packet["block_reason"] = block_reason
        study_packet["allowance_package"] = allowance_package
        study_packet["opportunity_maturity"] = opportunity_maturity
        study_packet["opportunity_maturity_state"] = opportunity_maturity_state
        council["final_state"] = "WATCHING"
        council["allowance_package"] = allowance_package
        council["opportunity_maturity"] = opportunity_maturity
        council["opportunity_maturity_state"] = opportunity_maturity_state
        council["arbitration_reason"] = (
            f"BLOCKED_BY_SEQUENCE_CONTEXT: blocked_by={block_reason}; "
            f"failed_module={sequence_readiness.get('failed_module')}; next_required={next_required}"
        )
        if executable:
            result["block_reason"] = block_reason
        result["packet_result"] = "STUDY_PACKET_PUBLISHED"
        result["execution"] = {**execution, "enabled": False, "state": "WATCHING"}
        result["allowance_package"] = allowance_package
        result["model_council"] = council
        result["promotion_trace"] = promotion_trace
        result["opportunity_maturity"] = opportunity_maturity
        result["opportunity_maturity_state"] = opportunity_maturity_state
        result["study_packet"] = study_packet
        result["model_council_study_packet"] = study_packet
    if not executable:
        _refresh_promotion_failure_audit()
    if executable:
        packet = build_execution_packet_v3(
            packet_id=base["packet_id"],
            session_id=base["session_id"],
            symbol=base["symbol"],
            timeframe=base["timeframe"],
            frame_id=base["frame_id"],
            capture_count=base["capture_count"],
            state_version=base["state_version"],
            side=candidate_side,
            expiry_seconds=timing_expiry,
            input_frame_hash=base["input_frame_hash"],
            previous_frame_hash=base["previous_frame_hash"],
            created_epoch=current_now,
            valid_for_seconds=_float(snapshot.get("packet_valid_for_seconds"), 8.0),
            live_integrity=_mapping(snapshot.get("live_integrity")),
            model_council=council,
            market_context=market_context,
            angle_context=_mapping(market.get("angle_context")),
            history_context=_mapping(market.get("history_context")),
            runtime_model_health=health,
            instrument_context=instrument_context,
            symbol_context=symbol_context,
            sequence_context=sequence_context_payload,
            allowance_package=allowance_package,
        )
        packet["allowance_package"] = allowance_package
        packet["execution"]["allowance_package_type"] = allowance_package["package_type"]
        packet["market_reality"] = market_reality
        packet["packet_type"] = "PG_EXECUTION_PACKET_V3"
        packet["entry_quality"] = result["entry_quality"]
        packet["trade_permission"] = trade_permission
        packet["market_trap"] = result["market_trap"]
        packet["ideal_trade_path"] = result["ideal_trade_path"]
        packet["path_risk"] = result["path_risk"]
        packet["regime_playbook"] = result["regime_playbook"]
        packet["time_to_reward_invalidation"] = result["time_to_reward_invalidation"]
        packet["timing_decision"] = timing_decision
        packet["timing_forecast"] = timing_forecast
        packet["regime"] = result["regime"]
        packet["market_play"] = result["market_play"]
        packet["price_location"] = result["price_location"]
        packet["memory_confirmation"] = result["memory_confirmation"]
        packet["pair_profile"] = result["pair_profile"]
        packet["two_candle_study"] = two_candle_study
        packet["lstm_contribution"] = lstm_contribution
        packet["skill_contributions"] = skill_contributions
        packet["ai_contribution_strengths"] = ai_contribution_strengths
        packet["model_strength_profile"] = model_strength_profile
        packet["lane_thresholds"] = _lane_thresholds(snapshot)
        packet["model_role_outputs"] = model_role_outputs
        packet["reasoning_arbitration"] = reasoning_arbitration
        packet["bad_entry_filter"] = bad_entry_filter
        packet["final_reasoning_decision"] = final_reasoning_decision
        packet["current_candle_contract"] = result["current_candle_contract"]
        packet["execution_lane"] = execution_lane
        packet["selected_execution_lane"] = execution_lane.get("name")
        packet["trade_candidate_queue"] = result["trade_candidate_queue"]
        packet["market_listening_stream"] = result["market_listening_stream"]
        packet["council_debate"] = council_debate
        packet["promotion_trace"] = promotion_trace
        packet["opportunity_maturity"] = opportunity_maturity
        packet["opportunity_maturity_state"] = opportunity_maturity_state
        packet["visual_integrity"] = opportunity_maturity.get("visual_integrity")
        validation = validate_execution_packet_v3(
            packet,
            now=current_now,
            require_executable=True,
            require_broker_click_safe_identity=packet_identity_mode == "broker_click",
        )
        if not validation.ok:
            runtime_release_condition = (
                _instrument_release_requirement(instrument_context, instrument_release_condition)
                if validation.first_reason.startswith("INSTRUMENT_CONTEXT")
                else f"runtime validation clears: {validation.first_reason}"
            )
            runtime_release_state = "INSTRUMENT_CONTEXT_WAIT" if validation.first_reason.startswith("INSTRUMENT_CONTEXT") else "WATCHING"
            _mark_opportunity_maturity_blocked(
                opportunity_maturity,
                state="VALID_WATCH",
                denied_at=validation.first_reason,
                next_required=runtime_release_condition,
                field="current_execution_packet",
                received=validation.first_reason,
                required="validate_execution_packet_v3 pass",
                reason=runtime_release_condition,
                hard=True,
            )
            opportunity_maturity_state = _upper(opportunity_maturity.get("state"), "VALID_WATCH")
            allowance_package["opportunity_maturity"] = opportunity_maturity_state
            allowance_package["opportunity_maturity_confidence"] = opportunity_maturity["confidence"]
            allowance_package["visual_integrity"] = opportunity_maturity.get("visual_integrity")
            _mark_allowance_package_blocked(
                allowance_package,
                block_reason=validation.first_reason,
                next_required=runtime_release_condition,
                release_state=runtime_release_state,
                final_state="BLOCKED_BY_RUNTIME",
                promotion_result="BLOCKED_BY_RUNTIME",
            )
            promotion_trace.update(
                {
                    "release_state": runtime_release_state,
                    "non_executable_state": runtime_release_state,
                    "denied_at": validation.first_reason,
                    "blocked_by": validation.first_reason,
                    "true_blocker": validation.first_reason,
                    "next_required": runtime_release_condition,
                    "release_condition": runtime_release_condition,
                    "promotion_result": "BLOCKED_BY_RUNTIME",
                    "packet_result": "STUDY_PACKET_PUBLISHED",
                    "allowance_package": allowance_package,
                    "opportunity_maturity": opportunity_maturity,
                    "opportunity_maturity_state": opportunity_maturity_state,
                    "visual_integrity": opportunity_maturity.get("visual_integrity"),
                }
            )
            council.update(
                {
                    "final_state": "BLOCKED_BY_RUNTIME",
                    "release_state": runtime_release_state,
                    "non_executable_state": runtime_release_state,
                    "true_blocker": validation.first_reason,
                    "denied_at": validation.first_reason,
                    "next_required": runtime_release_condition,
                    "release_condition": runtime_release_condition,
                    "arbitration_reason": f"BLOCKED_BY_RUNTIME: blocked_by={validation.first_reason}; next_required={runtime_release_condition}",
                    "promotion_trace": promotion_trace,
                    "allowance_package": allowance_package,
                    "opportunity_maturity": opportunity_maturity,
                    "opportunity_maturity_state": opportunity_maturity_state,
                }
            )
            study_packet["promotion_trace"] = promotion_trace
            study_packet["model_council"] = council
            study_packet["allowance_package"] = allowance_package
            study_packet["opportunity_maturity"] = opportunity_maturity
            study_packet["opportunity_maturity_state"] = opportunity_maturity_state
            study_packet["true_blocker"] = validation.first_reason
            study_packet["reason"] = council["arbitration_reason"]
            result["execution"] = {**execution, "enabled": False, "state": "BLOCKED_BY_RUNTIME"}
            result["allowance_package"] = allowance_package
            result["model_council"] = council
            result["promotion_trace"] = promotion_trace
            result["opportunity_maturity"] = opportunity_maturity
            result["opportunity_maturity_state"] = opportunity_maturity_state
            result["study_packet"] = study_packet
            result["model_council_study_packet"] = study_packet
            result["block_reason"] = validation.first_reason
            result["packet_validation"] = validation.as_dict()
            _refresh_promotion_failure_audit()
        else:
            packet["contributors"] = result["contributors"]
            result["execution_packet"] = packet
            result["model_council_packet"] = packet
            result["packet_validation"] = validation.as_dict()
    if not _mapping(result.get("execution_packet") or result.get("model_council_packet")):
        no_packet_reason = str(
            promotion_trace.get("true_blocker")
            or promotion_trace.get("denied_at")
            or block_reason
            or "EXECUTION_PACKET_NOT_PUBLISHED"
        ).strip().upper()
        if not no_packet_reason or no_packet_reason == "NONE":
            no_packet_reason = "EXECUTION_PACKET_NOT_PUBLISHED"
        derived_no_packet_next_required = ""
        late_chase_packet_block = bool(
            opportunity_maturity_state == "LATE_CHASE"
            or late_chase
            or path_class == "LATE_CHASE_REVERSAL_RISK"
        )
        if late_chase_packet_block:
            no_packet_reason = f"TIMING_MODE_{timing_mode or 'LATE_CHASE'}"
            derived_no_packet_next_required = f"skip late chase; {timing_decision['entry_timing']['next_condition']}"
        elif no_packet_reason in {"EXECUTION_PACKET_NOT_PUBLISHED", "WATCHING", "STUDY_PACKET_PUBLISHED"}:
            if not context_ok or not bool(execution_lane.get("accepted")):
                no_packet_reason = "NO_EXECUTION_LANE_ACCEPTED"
                derived_no_packet_next_required = lane_release_requirements
            elif not lane_effective_mature:
                no_packet_reason = "CANDIDATE_MATURITY"
                derived_no_packet_next_required = "candidate_stage=CANDIDATE_STABLE/PREPARING"
            elif not stable:
                no_packet_reason = "CANDIDATE_STABILITY"
                derived_no_packet_next_required = f"dominance_margin >= {min_dominance_margin:.2f}"
            elif not lane_effective_timing_ready or timing_mode != "ENTER_NOW":
                no_packet_reason = f"TIMING_MODE_{timing_mode or 'NOT_READY'}"
                derived_no_packet_next_required = f"timing_mode=ENTER_NOW; {timing_decision['entry_timing']['next_condition']}"
            elif not timing_has_explicit_expiry:
                no_packet_reason = "MODEL_COUNCIL_EXPLICIT_EXPIRY_MISSING"
                derived_no_packet_next_required = "timing.expiry_seconds explicit and execution.time_sequence target exists"
            elif not final_score_passed:
                no_packet_reason = "LANE_SCORE_BELOW_THRESHOLD"
                derived_no_packet_next_required = (
                    f"final_score={final_execution_score:.4f} >= threshold={lane_required_score:.4f} "
                    f"for selected_lane={execution_lane.get('name', 'execution lane')}"
                )
            elif final_state and final_state != "EXECUTABLE":
                no_packet_reason = str(final_state).strip().upper()
                derived_no_packet_next_required = str(next_required or release_condition or "continue study")
        raw_no_packet_next_required = str(promotion_trace.get("next_required") or next_required or "").strip()
        if late_chase_packet_block:
            no_packet_next_required = str(
                derived_no_packet_next_required
                or "skip late chase; wait for a fresh trigger/retest"
            ).strip()
        elif (
            not raw_no_packet_next_required
            or raw_no_packet_next_required.lower() == "none"
            or raw_no_packet_next_required == "publish fresh validated PG_EXECUTION_PACKET_V3 when all gates pass"
        ):
            no_packet_next_required = str(
                derived_no_packet_next_required
                or "publish fresh validated PG_EXECUTION_PACKET_V3 when all gates pass"
            ).strip()
        else:
            no_packet_next_required = raw_no_packet_next_required
        if not no_packet_next_required or no_packet_next_required.lower() == "none":
            no_packet_next_required = "publish fresh validated PG_EXECUTION_PACKET_V3 when all gates pass"
        no_packet_reason_upper = _upper(no_packet_reason, "EXECUTION_PACKET_NOT_PUBLISHED")
        if opportunity_maturity_state in {"LATE_CHASE", "INVALIDATED", "MISSED"}:
            no_packet_maturity_state = opportunity_maturity_state
        elif no_packet_reason_upper == "NO_EXECUTION_LANE_ACCEPTED":
            no_packet_maturity_state = "EARLY_FORMING"
        elif no_packet_reason_upper in {"CANDIDATE_MATURITY", "CANDIDATE_STABILITY"}:
            no_packet_maturity_state = "VALID_WATCH"
        elif "LATE_CHASE" in no_packet_reason_upper or no_packet_reason_upper.startswith("TIMING_MODE_SKIP_LATE"):
            no_packet_maturity_state = "LATE_CHASE"
        elif "INVALID" in no_packet_reason_upper or "TRAP" in no_packet_reason_upper or "BAD_ENTRY" in no_packet_reason_upper:
            no_packet_maturity_state = "INVALIDATED"
        elif no_packet_reason_upper == "LANE_SCORE_BELOW_THRESHOLD" or no_packet_reason_upper.startswith("TIMING_MODE_"):
            no_packet_maturity_state = "PREPARE"
        elif no_packet_reason_upper in OPPORTUNITY_MATURITY_STATES:
            no_packet_maturity_state = no_packet_reason_upper
        else:
            no_packet_maturity_state = opportunity_maturity_state if opportunity_maturity_state in OPPORTUNITY_MATURITY_STATES else "VALID_WATCH"
        no_packet_hard_block = no_packet_reason_upper.startswith(
            (
                "INSTRUMENT_CONTEXT",
                "SEQUENCE_CONTEXT",
                "REQUIRED_MODELS",
                "NOT_LIVE",
                "CACHE",
                "FRAME",
                "CAPTURE",
                "STATE",
                "MISSING",
                "MODEL_COUNCIL_EXPLICIT_EXPIRY",
                "EXECUTION_PACKET_NOT_CURRENT",
            )
        )
        _mark_opportunity_maturity_blocked(
            opportunity_maturity,
            state=no_packet_maturity_state,
            denied_at=no_packet_reason_upper,
            next_required=no_packet_next_required,
            field=_promotion_exact_field(no_packet_reason_upper, sequence_readiness, instrument_context),
            received=no_packet_reason_upper,
            required=no_packet_next_required,
            reason=no_packet_next_required,
            hard=no_packet_hard_block,
        )
        opportunity_maturity_state = _upper(opportunity_maturity.get("state"), no_packet_maturity_state)
        allowance_package["opportunity_maturity"] = opportunity_maturity_state
        allowance_package["opportunity_maturity_confidence"] = opportunity_maturity["confidence"]
        allowance_package["visual_integrity"] = opportunity_maturity.get("visual_integrity")
        _mark_allowance_package_blocked(
            allowance_package,
            block_reason=no_packet_reason,
            next_required=no_packet_next_required,
            release_state=str(council.get("release_state") or release_state or "WATCHING"),
            final_state=str(council.get("final_state") or final_state or "WATCHING"),
            promotion_result="STUDY_PACKET_PUBLISHED",
        )
        result_execution = _mapping(result.get("execution") or execution)
        result_execution.update(
            {
                "enabled": False,
                "state": "WATCHING"
                if str(result_execution.get("state") or "").strip().upper() == "EXECUTABLE"
                else str(result_execution.get("state") or final_state or "WATCHING").strip().upper(),
            }
        )
        if result_execution["state"] == "EXECUTABLE":
            result_execution["state"] = "WATCHING"
        result["execution"] = result_execution
        council["final_state"] = (
            "WATCHING" if str(council.get("final_state") or "").strip().upper() == "EXECUTABLE" else council.get("final_state", "WATCHING")
        )
        council["true_blocker"] = no_packet_reason
        council["denied_at"] = no_packet_reason
        council["next_required"] = no_packet_next_required
        council["release_condition"] = str(promotion_trace.get("release_condition") or release_condition or no_packet_next_required)
        council["allowance_package"] = allowance_package
        council["opportunity_maturity"] = opportunity_maturity
        council["opportunity_maturity_state"] = opportunity_maturity_state
        promotion_trace.update(
            {
                "denied_at": no_packet_reason,
                "blocked_by": no_packet_reason,
                "true_blocker": no_packet_reason,
                "next_required": no_packet_next_required,
                "release_condition": council["release_condition"],
                "packet_result": "STUDY_PACKET_PUBLISHED",
                "allowance_package": allowance_package,
                "opportunity_maturity": opportunity_maturity,
                "opportunity_maturity_state": opportunity_maturity_state,
                "visual_integrity": opportunity_maturity.get("visual_integrity"),
            }
        )
        if str(promotion_trace.get("promotion_result") or "").strip().upper() == "EXECUTABLE_PACKET_CREATED":
            promotion_trace["promotion_result"] = str(council.get("final_state") or "WATCHING").strip().upper()
        council["promotion_trace"] = promotion_trace
        study_packet["execution"] = dict(result_execution)
        study_packet["model_council"] = council
        study_packet["promotion_trace"] = promotion_trace
        study_packet["allowance_package"] = allowance_package
        study_packet["opportunity_maturity"] = opportunity_maturity
        study_packet["opportunity_maturity_state"] = opportunity_maturity_state
        study_packet["true_blocker"] = no_packet_reason
        study_packet["denied_at"] = no_packet_reason
        study_packet["next_required"] = no_packet_next_required
        study_packet["release_condition"] = council["release_condition"]
        study_packet["packet_result"] = "STUDY_PACKET_PUBLISHED"
        result["model_council"] = council
        result["promotion_trace"] = promotion_trace
        result["allowance_package"] = allowance_package
        result["opportunity_maturity"] = opportunity_maturity
        result["opportunity_maturity_state"] = opportunity_maturity_state
        result["study_packet"] = study_packet
        result["model_council_study_packet"] = study_packet
        result["packet_result"] = "STUDY_PACKET_PUBLISHED"
        result["execution_packet_present"] = False
    return result


def publish_model_council_packet_v3(
    snapshot: Mapping[str, Any],
    *,
    previous_state: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    result = evaluate_model_council_v3(snapshot, previous_state=previous_state, now=now)
    packet = result.get("execution_packet")
    return dict(cast(Mapping[str, Any], packet)) if isinstance(packet, Mapping) else None


class ModelCouncilV3:
    """Small stateful facade that enforces stable second-read maturity.

    The pure evaluator can be used in batch/replay mode. Live callers should
    keep an instance of this facade so an idea must survive at least two
    same-side reads before it can publish an executable packet.
    """

    def __init__(self) -> None:
        self._previous_result: dict[str, Any] | None = None
        self._stable_candidate_side: str | None = None
        self._stable_candidate_count = 0
        self._recent_candidate_sides: list[str] = []
        self._recent_raw_sides: list[str] = []
        self._stable_context_key: str | None = None

    def evaluate(self, snapshot: Mapping[str, Any], *, now_epoch: float | None = None) -> dict[str, Any]:
        working = dict(snapshot)
        working.pop("recent_sides", None)
        instrument = _mapping(working.get("instrument_context"))
        context_symbol = str(
            instrument.get("display_symbol")
            or instrument.get("canonical_symbol")
            or working.get("symbol")
            or working.get("market")
            or ""
        ).strip().upper()
        context_timeframe = str(instrument.get("timeframe") or working.get("timeframe") or working.get("focus_timeframe") or "").strip().upper()
        unknown_symbols = {"UNKNOWN", "N/A", "NA", "NONE", "NULL", "USER_LOCKED_ACTIVE_CHART"}
        context_symbol_for_switch = "" if context_symbol in unknown_symbols else context_symbol
        previous_symbol = ""
        previous_timeframe = ""
        if self._stable_context_key:
            previous_symbol, _, previous_timeframe = self._stable_context_key.partition("|")
        symbol_switched = bool(
            context_symbol_for_switch
            and previous_symbol
            and context_symbol_for_switch != previous_symbol
        )
        timeframe_switched = bool(
            context_timeframe
            and previous_timeframe
            and context_timeframe != previous_timeframe
        )
        context_switched = bool(symbol_switched or timeframe_switched)
        if context_switched:
            self._previous_result = None
            self._stable_candidate_side = None
            self._stable_candidate_count = 0
            self._recent_candidate_sides = []
            self._recent_raw_sides = []
            working["execution_mature"] = False
            working["candidate_stable_reads"] = 0
            working["stability_frames"] = 0
        if context_symbol_for_switch or context_timeframe:
            stored_symbol = context_symbol_for_switch
            if not stored_symbol and previous_symbol and not context_switched:
                stored_symbol = previous_symbol
            stored_timeframe = context_timeframe or previous_timeframe
            self._stable_context_key = f"{stored_symbol}|{stored_timeframe}"
        locked_surface_maturity = bool(_bool(working.get("locked_surface_identity_fallback_active")) and not context_switched)
        raw_side = _raw_observed_side_from_snapshot(working)
        buy_score = _score_from_snapshot(working, "BUY")
        sell_score = _score_from_snapshot(working, "SELL")
        candidate = _scored_candidate_side(
            working,
            raw_side=raw_side,
            buy_score=buy_score,
            sell_score=sell_score,
        )
        working["raw_observed_side"] = raw_side
        if raw_side in {"BUY", "SELL"}:
            self._recent_raw_sides.append(raw_side)
            self._recent_raw_sides = self._recent_raw_sides[-5:]
            working["recent_raw_sides"] = list(self._recent_raw_sides)
        if candidate in {"BUY", "SELL"}:
            if self._stable_candidate_side == candidate:
                self._stable_candidate_count += 1
            else:
                self._stable_candidate_side = candidate
                self._stable_candidate_count = 1
            self._recent_candidate_sides.append(candidate)
            self._recent_candidate_sides = self._recent_candidate_sides[-5:]
            working["candidate_side"] = candidate
            working["recent_candidate_sides"] = list(self._recent_candidate_sides)
            effective_stable_count = max(self._stable_candidate_count, 2 if locked_surface_maturity else 0)
            working["candidate_stable_reads"] = effective_stable_count
            working["stability_frames"] = max(_int(working.get("stability_frames"), 0), effective_stable_count)
            if effective_stable_count >= 2:
                working["execution_mature"] = True
        result = evaluate_model_council_v3(
            working,
            previous_state=self._previous_result,
            now=now_epoch,
        )
        self._previous_result = result
        packet = result.get("execution_packet")
        if isinstance(packet, Mapping):
            return dict(cast(Mapping[str, Any], packet))
        return result
