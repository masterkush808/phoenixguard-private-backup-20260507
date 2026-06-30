from __future__ import annotations

from typing import Any, Mapping, Sequence, cast


PG_MARKET_REALITY_ENGINE_VERSION = "PG_MARKET_REALITY_ENGINE_V1"
ACCEPTABLE_ENTRY = "ACCEPTABLE_ENTRY"
LIVE_TRIGGER_ENTRY_STATES = {"SNIPER_READY", "TRIGGER_READY", "TRIGGERED", "ACTIVE", "EXECUTE"}
LIVE_REACTION_TIMING_CLASSES = {
    "MEASURED_REACTION_WINDOW",
    "OPPOSING_FORCE_REACTION",
    "FAILED_RETEST_REACTION",
    "SNIPER_REACTION",
    "HIGH_FREQUENCY_TWO_CANDLE_CYCLE",
}

ENTRY_QUALITY_RANKS = {
    "UNACCEPTABLE_ENTRY": 0,
    "NO_ENTRY": 0,
    "BAD_ENTRY": 0,
    "BAD_NOW": 1,
    "LATE_ENTRY": 1,
    "CHASE_ENTRY": 1,
    "EARLY_WATCH": 1,
    "WATCH_ONLY": 1,
    "FORMING": 1,
    "KERNEL_TRIGGER": 2,
    "TRIGGER": 2,
    "CONFIRMATION": 2,
    "SNIPER": 3,
    "COMPLETE": 3,
    "ACCEPTABLE_ENTRY": 2,
    "ACCEPTABLE": 2,
    "GOOD_ENTRY": 3,
    "GOOD": 3,
    "IDEAL_ENTRY": 4,
    "A_PLUS_ENTRY": 4,
    "A+": 4,
}
ENTRY_QUALITY_CANONICAL = {
    "UNACCEPTABLE_ENTRY": "UNACCEPTABLE_ENTRY",
    "NO_ENTRY": "UNACCEPTABLE_ENTRY",
    "BAD_ENTRY": "UNACCEPTABLE_ENTRY",
    "BAD_NOW": "BAD_NOW",
    "LATE_ENTRY": "BAD_NOW",
    "CHASE_ENTRY": "BAD_NOW",
    "EARLY_WATCH": "EARLY_WATCH",
    "WATCH_ONLY": "EARLY_WATCH",
    "FORMING": "EARLY_WATCH",
    "KERNEL_TRIGGER": ACCEPTABLE_ENTRY,
    "TRIGGER": ACCEPTABLE_ENTRY,
    "CONFIRMATION": ACCEPTABLE_ENTRY,
    "SNIPER": "GOOD_ENTRY",
    "COMPLETE": "GOOD_ENTRY",
    "ACCEPTABLE": ACCEPTABLE_ENTRY,
    "ACCEPTABLE_ENTRY": ACCEPTABLE_ENTRY,
    "GOOD": "GOOD_ENTRY",
    "GOOD_ENTRY": "GOOD_ENTRY",
    "IDEAL_ENTRY": "IDEAL_ENTRY",
    "A_PLUS_ENTRY": "IDEAL_ENTRY",
    "A+": "IDEAL_ENTRY",
}

PERMISSION_DENY_PRIORITY = (
    "CANDIDATE_QUEUE_UNSTABLE",
    "LATE_CHASE_TRAP",
    "MARKET_TRAP_DETECTED",
    "IDEAL_PATH_PROTECT",
    "IDEAL_PATH_HOLD",
    "IDEAL_PATH_WAIT",
    "PATH_RISK_WEAK",
    "ENTRY_QUALITY_BELOW_ACCEPTABLE",
    "TIMING_PATH_BAD",
    "CURRENT_CANDLE_CONTRACT_UNSAFE",
    "REGIME_PLAYBOOK_DENIES_ENTRY",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, Any], item)) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


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


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "pass", "passed", "allowed"}
    return bool(value)


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text or default


def _nested(snapshot: Mapping[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        candidate = _mapping(snapshot.get(name))
        if candidate:
            return candidate
    return {}


def _first_text_from(*containers: Mapping[str, Any], names: Sequence[str]) -> str:
    for container in containers:
        for name in names:
            text = str(container.get(name) or "").strip()
            if text:
                return text
    return ""


def _measured_trigger_reaction_confirmed(snapshot: Mapping[str, Any], side: str) -> bool:
    resolved_side = _side(side)
    if resolved_side not in {"BUY", "SELL"}:
        return False
    latest_signal = _mapping(snapshot.get("latest_signal"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    timing = _mapping(snapshot.get("execution_timing") or snapshot.get("timing_signal") or snapshot.get("timing"))
    candle = _mapping(
        snapshot.get("current_candle_acceptance")
        or snapshot.get("current_candle_contract")
        or snapshot.get("current_candle")
        or latest_signal.get("current_candle_acceptance")
        or tracking.get("current_candle_acceptance")
    )
    trigger_side = _side(
        _first_text_from(
            latest_signal,
            tracking,
            timing,
            snapshot,
            names=("execution_action", "action", "candidate_action", "side", "candidate_side"),
        )
    )
    entry_state = _upper(
        _first_text_from(
            latest_signal,
            tracking,
            timing,
            snapshot,
            names=("entry_state", "setup_state", "trigger_state", "trigger", "decision_state", "state"),
        )
    )
    timing_class = _upper(_first_text_from(timing, latest_signal, tracking, names=("timing_class", "class")))
    timing_state = _upper(_first_text_from(timing, names=("state", "entry_state", "timing_state")))
    candle_allowed = bool(
        _bool(candle.get("entry_allowed"))
        and not _bool(candle.get("too_late"))
        and not _bool(candle.get("wick_reversal_risk"))
    )
    timing_allowed = _bool(timing.get("entry_allowed") or latest_signal.get("actionable") or tracking.get("actionable"))
    return bool(
        trigger_side == resolved_side
        and entry_state in LIVE_TRIGGER_ENTRY_STATES
        and (timing_class in LIVE_REACTION_TIMING_CLASSES or timing_state in {"READY", "TRIGGER_READY", "SNIPER_READY"})
        and (timing_allowed or timing_class in LIVE_REACTION_TIMING_CLASSES)
        and candle_allowed
    )


def _extract_side(snapshot: Mapping[str, Any], side: str | None, market_inputs: Mapping[str, Any]) -> str:
    resolved = _side(side)
    if resolved in {"BUY", "SELL"}:
        return resolved
    for key in ("candidate_side", "side", "execution_side", "execution_action", "action", "direction"):
        candidate = _side(snapshot.get(key))
        if candidate in {"BUY", "SELL"}:
            return candidate
    market_context = _mapping(market_inputs.get("market_context") or snapshot.get("market_context"))
    for key in ("dominant_side", "local_side", "global_side"):
        candidate = _side(market_context.get(key))
        if candidate in {"BUY", "SELL"}:
            return candidate
    return "HOLD"


def _quality_state_from_raw(raw: Any) -> tuple[str | None, float | None, str]:
    if isinstance(raw, Mapping):
        raw_map = cast(Mapping[str, Any], raw)
        state = _upper(
            raw_map.get("state")
            or raw_map.get("quality")
            or raw_map.get("entry_quality")
            or raw_map.get("class")
            or raw_map.get("classification")
            or raw_map.get("label")
        )
        score = raw_map.get("score", raw_map.get("entry_quality_score"))
        return (state or None, _clip01(score) if score is not None else None, str(raw_map.get("reason") or ""))
    if raw is None:
        return None, None, ""
    return _upper(raw) or None, None, ""


def _entry_quality(
    snapshot: Mapping[str, Any],
    side: str,
    market_inputs: Mapping[str, Any],
    *,
    trap_detected: bool,
) -> dict[str, Any]:
    reality = _mapping(snapshot.get("market_reality"))
    explicit = (
        snapshot.get("entry_quality")
        if "entry_quality" in snapshot
        else reality.get("entry_quality")
        if "entry_quality" in reality
        else snapshot.get("entry_quality_context")
    )
    explicit_state, explicit_score, explicit_reason = _quality_state_from_raw(explicit)
    if explicit_state in {"NONE", "UNKNOWN", "UNSET", "MISSING", "N/A", "NA"}:
        explicit_state = None
        explicit_score = None
        explicit_reason = ""

    market_context = _mapping(market_inputs.get("market_context") or snapshot.get("market_context"))
    source_market_context = _mapping(snapshot.get("market_context"))
    classifiers = _mapping(market_inputs.get("classifiers") or snapshot.get("classifiers"))
    angle = _mapping(market_inputs.get("angle_context") or snapshot.get("angle_context") or snapshot.get("angle_features") or snapshot.get("angle_dynamics"))
    history = _mapping(market_inputs.get("history_context") or snapshot.get("history_context") or snapshot.get("historical_pattern"))
    risk = _mapping(market_inputs.get("risk_context") or snapshot.get("risk_context") or snapshot.get("risk_opposing_force"))
    bad_entry = _mapping(market_inputs.get("bad_entry") or snapshot.get("bad_entry"))
    measured_trigger_reaction = _measured_trigger_reaction_confirmed(snapshot, side)

    if explicit_state:
        canonical = ENTRY_QUALITY_CANONICAL.get(explicit_state, explicit_state)
        rank = ENTRY_QUALITY_RANKS.get(canonical, ENTRY_QUALITY_RANKS.get(explicit_state, 1))
        score = explicit_score if explicit_score is not None else min(1.0, max(0.0, rank / 4.0))
        reason = explicit_reason or f"Entry quality supplied as {canonical}."
    else:
        inside_zone = _bool(market_context.get("inside_valid_trigger_zone") or source_market_context.get("inside_valid_trigger_zone"))
        distance_ok = _bool(
            market_context.get("opposing_force_distance_ok")
            or source_market_context.get("opposing_force_distance_ok")
            or risk.get("distance_ok")
        )
        continuation = _bool(
            market_context.get("is_continuation_confirmed")
            or source_market_context.get("is_continuation_confirmed")
            or snapshot.get("continuation_confirmed")
            or snapshot.get("pullback_confirmed")
            or snapshot.get("retest_confirmed")
            or measured_trigger_reaction
        )
        history_good = _upper(history.get("historical_entry_quality")) in {"GOOD", "GOOD_ENTRY", "ACCEPTABLE", "ACCEPTABLE_ENTRY"}
        late_or_bad = bool(
            trap_detected
            or (_bool(bad_entry.get("detected")) and not measured_trigger_reaction)
            or (_bool(classifiers.get("late_chase_after_impulse")) and not measured_trigger_reaction)
            or (_bool(market_context.get("is_late_chase")) and not measured_trigger_reaction)
            or (_bool(angle.get("late_chase_risk")) and not measured_trigger_reaction)
            or _bool(classifiers.get("history_would_exit_here"))
            or _bool(market_context.get("history_would_exit_here"))
        )
        if late_or_bad:
            canonical = "BAD_NOW"
            rank = ENTRY_QUALITY_RANKS[canonical]
            score = 0.25
            reason = "Direction can be strong while the current entry is late, trapped, or historically exit-like."
        elif side in {"BUY", "SELL"} and inside_zone and distance_ok and continuation:
            canonical = "GOOD_ENTRY" if history_good else ACCEPTABLE_ENTRY
            rank = ENTRY_QUALITY_RANKS[canonical]
            score = 0.74 if canonical == "GOOD_ENTRY" else 0.58
            reason = "Entry is in a qualified trigger area with distance and continuation evidence."
        elif side in {"BUY", "SELL"}:
            canonical = "EARLY_WATCH"
            rank = ENTRY_QUALITY_RANKS[canonical]
            score = 0.36
            reason = "Direction is visible but the entry location has not matured."
        else:
            canonical = "UNACCEPTABLE_ENTRY"
            rank = ENTRY_QUALITY_RANKS[canonical]
            score = 0.0
            reason = "No directional candidate is mature enough to qualify an entry."

    return {
        "state": canonical,
        "entry_grade": canonical,
        "direction_side": side if side in {"BUY", "SELL"} else "HOLD",
        "direction_confidence": round(
            max(
                _clip01(snapshot.get("confidence"), 0.0),
                _clip01(snapshot.get("buy_score"), 0.0) if side == "BUY" else 0.0,
                _clip01(snapshot.get("sell_score"), 0.0) if side == "SELL" else 0.0,
            ),
            4,
        ),
        "score": round(_clip01(score), 4),
        "entry_score": round(_clip01(score), 4),
        "rank": int(rank),
        "entry_timing": "READY" if int(rank) >= ENTRY_QUALITY_RANKS[ACCEPTABLE_ENTRY] else ("LATE" if canonical in {"BAD_NOW", "TRAP_RISK"} else "WAIT"),
        "entry_reason": reason,
        "recommended_wait_condition": "Wait for pullback/retest into a qualified trigger zone." if int(rank) < ENTRY_QUALITY_RANKS[ACCEPTABLE_ENTRY] else "Entry quality threshold met.",
        "minimum_executable_state": ACCEPTABLE_ENTRY,
        "minimum_executable_rank": ENTRY_QUALITY_RANKS[ACCEPTABLE_ENTRY],
        "passes_executable_threshold": int(rank) >= ENTRY_QUALITY_RANKS[ACCEPTABLE_ENTRY],
        "separate_from_direction": True,
        "reason": reason,
    }


def _market_trap(snapshot: Mapping[str, Any], market_inputs: Mapping[str, Any]) -> dict[str, Any]:
    explicit = _nested(snapshot, "market_trap", "trap_context")
    classifiers = _mapping(market_inputs.get("classifiers") or snapshot.get("classifiers"))
    market_context = _mapping(market_inputs.get("market_context") or snapshot.get("market_context"))
    angle = _mapping(market_inputs.get("angle_context") or snapshot.get("angle_context") or snapshot.get("angle_features") or snapshot.get("angle_dynamics"))
    history = _mapping(market_inputs.get("history_context") or snapshot.get("history_context") or snapshot.get("historical_pattern"))
    measured_trigger_reaction = _measured_trigger_reaction_confirmed(
        snapshot,
        _extract_side(snapshot, None, market_inputs),
    )

    explicit_type = _upper(explicit.get("trap_type") or explicit.get("type") or explicit.get("state"))
    explicit_detected = _bool(explicit.get("detected") or explicit.get("trap_detected"))
    if explicit_type in {"NONE", "CLEAR", "NO_TRAP"}:
        explicit_detected = False
    late_chase = bool(
        not measured_trigger_reaction
        and (
            _bool(classifiers.get("late_chase_after_impulse"))
            or _bool(market_context.get("is_late_chase"))
            or _bool(angle.get("late_chase_risk"))
        )
    )
    false_breakout = bool(_bool(classifiers.get("false_breakout_risk")) or _bool(market_context.get("false_breakout_risk")))
    history_exit = bool(_bool(classifiers.get("history_would_exit_here")) or _bool(history.get("would_have_exited_here")))
    angle_break = bool(_bool(classifiers.get("angle_break_risk")) or _bool(snapshot.get("angle_break_risk")))

    trap_type = explicit_type if explicit_detected and explicit_type else ""
    if not trap_type and late_chase:
        trap_type = "LATE_CHASE_TRAP"
    elif not trap_type and false_breakout:
        trap_type = "FALSE_BREAKOUT_TRAP"
    elif not trap_type and history_exit:
        trap_type = "HISTORY_EXIT_TRAP"
    elif not trap_type and angle_break:
        trap_type = "ANGLE_BREAK_TRAP"

    detected = bool(explicit_detected or trap_type)
    severity = _upper(explicit.get("severity"), "HIGH" if detected else "NONE")
    deny = bool(
        _bool(explicit.get("deny_execution") or explicit.get("block") or explicit.get("blocks_entry"))
        or (detected and severity not in {"LOW", "INFO", "NONE"})
    )
    reason = str(explicit.get("reason") or "")
    if not reason:
        reason = "Late chase trap detected after an expanded impulse." if trap_type == "LATE_CHASE_TRAP" else "Market trap classifier is clear."
        if detected and trap_type != "LATE_CHASE_TRAP":
            reason = f"{trap_type} detected; executable permission is denied."

    return {
        "detected": detected,
        "trap_type": trap_type or "NONE",
        "severity": severity,
        "executable_allowed": not deny,
        "deny_reason": "LATE_CHASE_TRAP" if trap_type == "LATE_CHASE_TRAP" and deny else "MARKET_TRAP_DETECTED" if deny else None,
        "reason": reason,
    }


def _time_to_reward_invalidation(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    timing_path = _nested(snapshot, "time_to_reward", "timing_path", "reward_invalidation_path")
    timing = _mapping(snapshot.get("timing") or snapshot.get("execution_timing") or snapshot.get("timing_signal"))
    explicit_state = _upper(
        snapshot.get("timing_path_state")
        or timing_path.get("state")
        or timing_path.get("timing_path_state")
        or timing.get("path_state")
    )
    reward_seconds = _int(
        snapshot.get("time_to_reward_seconds")
        or timing_path.get("time_to_reward_seconds")
        or timing_path.get("reward_seconds")
        or timing.get("time_to_reward_seconds"),
        0,
    )
    invalidation_seconds = _int(
        snapshot.get("time_to_invalidation_seconds")
        or timing_path.get("time_to_invalidation_seconds")
        or timing_path.get("invalidation_seconds")
        or timing.get("time_to_invalidation_seconds"),
        0,
    )
    explicit_bad = explicit_state in {"BAD", "BAD_PATH", "UNFAVORABLE", "DENY", "INVALIDATION_FIRST"}
    invalidation_first = bool(reward_seconds > 0 and invalidation_seconds > 0 and invalidation_seconds < reward_seconds * 0.55)
    executable_allowed = not (explicit_bad or invalidation_first)
    if explicit_state in {"GOOD", "FAVORABLE", "OK", "PASS"}:
        executable_allowed = True
    return {
        "state": "BAD" if not executable_allowed else "FAVORABLE" if reward_seconds or invalidation_seconds else "UNKNOWN",
        "time_to_reward_seconds": reward_seconds,
        "time_to_invalidation_seconds": invalidation_seconds,
        "reward_before_invalidation": bool(reward_seconds > 0 and (invalidation_seconds <= 0 or reward_seconds <= invalidation_seconds)),
        "executable_allowed": executable_allowed,
        "deny_reason": None if executable_allowed else "TIMING_PATH_BAD",
        "reason": (
            "Invalidation is likely before reward; do not execute this candle."
            if not executable_allowed
            else "Timing path does not put invalidation before reward."
        ),
    }


def _current_candle_contract(snapshot: Mapping[str, Any], side: str) -> dict[str, Any]:
    candle = _nested(snapshot, "current_candle_contract", "current_candle")
    state = _upper(candle.get("state") or candle.get("contract_state"))
    direction = _side(candle.get("direction") or candle.get("side"))
    progress = _clip01(candle.get("progress", candle.get("close_progress")), 1.0)
    body_against = bool(
        _bool(candle.get("closes_against_side"))
        or _bool(candle.get("body_against_side"))
        or (direction in {"BUY", "SELL"} and side in {"BUY", "SELL"} and direction != side and progress >= 0.72)
    )
    requires_closed = _bool(candle.get("requires_closed_candle") or snapshot.get("requires_closed_candle"))
    unsafe = bool(
        state in {"UNSAFE", "FAIL", "INVALID", "REJECT"}
        or body_against
        or (requires_closed and progress < _clip01(candle.get("minimum_close_progress"), 0.85))
    )
    return {
        "state": "UNSAFE" if unsafe else "VALID",
        "side": side,
        "candle_direction": direction if direction in {"BUY", "SELL"} else None,
        "close_progress": progress,
        "requires_closed_candle": requires_closed,
        "executable_allowed": not unsafe,
        "deny_reason": "CURRENT_CANDLE_CONTRACT_UNSAFE" if unsafe else None,
        "reason": "Current candle violates the entry contract." if unsafe else "Current candle contract is acceptable.",
    }


def _path_risk(
    snapshot: Mapping[str, Any],
    market_inputs: Mapping[str, Any],
    timing_path: Mapping[str, Any],
    trap: Mapping[str, Any],
) -> dict[str, Any]:
    raw = _nested(snapshot, "path_risk", "path_risk_context")
    risk_context = _mapping(market_inputs.get("risk_context") or snapshot.get("risk_context") or snapshot.get("risk_opposing_force"))
    market_context = _mapping(market_inputs.get("market_context") or snapshot.get("market_context"))
    state = _upper(raw.get("state") or raw.get("risk_state") or raw.get("strength"))
    score = raw.get("score", raw.get("path_score", raw.get("strength_score")))
    if score is None:
        score = 0.72
        if not _bool(risk_context.get("distance_ok") or market_context.get("opposing_force_distance_ok")):
            score = min(float(score), 0.38)
        if trap.get("detected"):
            score = min(float(score), 0.32)
    parsed_score = _clip01(score, 0.72)
    weak_by_state = state in {"WEAK", "BAD", "HIGH_RISK", "DENY", "FAIL", "UNFAVORABLE"}
    executable_allowed = not (weak_by_state or parsed_score < _clip01(raw.get("minimum_path_score"), 0.50))
    if state in {"STRONG", "GOOD", "ACCEPTABLE", "PASS"} and parsed_score >= 0.45:
        executable_allowed = True
    return {
        "state": "WEAK" if not executable_allowed else state or "ACCEPTABLE",
        "score": round(parsed_score, 4),
        "minimum_path_score": _clip01(raw.get("minimum_path_score"), 0.50),
        "executable_allowed": executable_allowed,
        "deny_reason": "PATH_RISK_WEAK" if not executable_allowed else None,
        "reason": str(raw.get("reason") or ("Path risk is too weak for execution." if not executable_allowed else "Path risk is acceptable.")),
    }


def _ideal_trade_path(
    snapshot: Mapping[str, Any],
    side: str,
    market_inputs: Mapping[str, Any],
    entry_quality: Mapping[str, Any],
    trap: Mapping[str, Any],
    path_risk: Mapping[str, Any],
) -> dict[str, Any]:
    raw = _nested(snapshot, "ideal_trade_path", "path_reasoner", "ideal_path")
    market_context = _mapping(market_inputs.get("market_context") or snapshot.get("market_context"))
    history = _mapping(market_inputs.get("history_context") or snapshot.get("history_context") or snapshot.get("historical_pattern"))
    action = _upper(raw.get("action") or raw.get("state") or raw.get("recommendation"))
    if not action:
        if _bool(history.get("would_have_exited_here") or market_context.get("history_would_exit_here")):
            action = "PROTECT"
        elif trap.get("detected"):
            action = "HOLD"
        elif entry_quality.get("passes_executable_threshold") and path_risk.get("executable_allowed", True):
            action = "ENTER"
        else:
            action = "STUDY"
    deny_reason = None
    if action in {"PROTECT", "EXIT", "DEFEND"}:
        deny_reason = "IDEAL_PATH_PROTECT"
    elif action in {"HOLD", "NO_TRADE"}:
        deny_reason = "IDEAL_PATH_HOLD"
    elif action in {"WAIT", "WAIT_FOR_PULLBACK", "WAIT_FOR_RETEST"}:
        deny_reason = "IDEAL_PATH_WAIT"
    return {
        "side": side,
        "action": action,
        "target_path": str(raw.get("target_path") or raw.get("path") or ""),
        "invalidation_path": str(raw.get("invalidation_path") or ""),
        "executable_allowed": deny_reason is None,
        "deny_reason": deny_reason,
        "reason": str(raw.get("reason") or ("Ideal path allows entry." if deny_reason is None else f"Ideal path says {action.lower()}.")),
    }


def _regime_playbook(snapshot: Mapping[str, Any], side: str, market_inputs: Mapping[str, Any]) -> dict[str, Any]:
    raw = _nested(snapshot, "regime_playbook", "playbook")
    market_context = _mapping(market_inputs.get("market_context") or snapshot.get("market_context"))
    regime = _upper(snapshot.get("market_regime") or snapshot.get("regime") or raw.get("regime"))
    if not regime:
        global_side = _side(market_context.get("global_side"))
        local_side = _side(market_context.get("local_side"))
        if side in {"BUY", "SELL"} and global_side == local_side == side:
            regime = "TREND_CONTINUATION"
        elif _upper(market_context.get("current_location")) in {"MIDDLE", "MID", "MIDDLE_DANGER"}:
            regime = "RANGE_OR_CHOP"
        else:
            regime = "TRANSITION"
    entry_rule = str(raw.get("entry_rule") or "Require entry quality, clear traps, path risk, and timing path to agree.")
    denies_entry = _bool(raw.get("deny_entry") or raw.get("blocks_entry"))
    return {
        "regime": regime,
        "playbook": str(raw.get("name") or f"{regime}_PLAYBOOK"),
        "entry_rule": entry_rule,
        "prepare_bias": str(raw.get("prepare_bias") or "STUDY_ALLOWED"),
        "executable_allowed": not denies_entry,
        "deny_reason": "REGIME_PLAYBOOK_DENIES_ENTRY" if denies_entry else None,
        "reason": str(raw.get("reason") or "Regime playbook is diagnostic and allows permission stack evaluation."),
    }


def _market_listening_stream(
    snapshot: Mapping[str, Any],
    entry_quality: Mapping[str, Any],
    trap: Mapping[str, Any],
    path_risk: Mapping[str, Any],
    timing_path: Mapping[str, Any],
) -> dict[str, Any]:
    rows = _rows(
        snapshot.get("market_listening_stream")
        or snapshot.get("listening_stream")
        or snapshot.get("listening_events")
        or snapshot.get("events")
    )
    synthetic: list[dict[str, Any]] = [
        {"event": "ENTRY_QUALITY", "state": entry_quality.get("state"), "score": entry_quality.get("score")},
        {"event": "TRAP_CLASSIFIER", "state": trap.get("trap_type"), "detected": trap.get("detected")},
        {"event": "PATH_RISK", "state": path_risk.get("state"), "score": path_risk.get("score")},
        {"event": "TIMING_PATH", "state": timing_path.get("state")},
    ]
    return {
        "events": rows + synthetic,
        "latest_event": (rows + synthetic)[-1] if rows or synthetic else {},
        "role": "MODEL_COUNCIL_INPUT",
    }


def _trade_candidate_queue(snapshot: Mapping[str, Any], side: str) -> dict[str, Any]:
    raw = snapshot.get("trade_candidate_queue") or snapshot.get("candidate_queue")
    candidates = _rows(raw)
    if isinstance(raw, Mapping):
        raw_map = cast(Mapping[str, Any], raw)
        candidates = _rows(raw_map.get("candidates")) or candidates
    recent: list[str] = []
    recent_raw = snapshot.get("recent_sides")
    if isinstance(recent_raw, Sequence) and not isinstance(recent_raw, (str, bytes, bytearray)):
        recent = [_side(item) for item in cast(Sequence[Any], recent_raw)]
    if not recent and candidates:
        recent = [_side(row.get("side") or row.get("candidate_side")) for row in candidates]
    recent = [item for item in recent if item in {"BUY", "SELL"}]
    last_window = recent[-3:]
    flip_flop = len(last_window) >= 3 and len(set(last_window)) > 1
    invalidated = _bool(snapshot.get("previous_side_invalidated") or snapshot.get("confirmed_reversal"))
    stable_reads = _int(snapshot.get("stability_frames") or snapshot.get("stable_reads"), 0)
    if side in {"BUY", "SELL"} and recent:
        stable_reads = max(stable_reads, len([item for item in reversed(recent) if item == side]))
    unstable = bool(flip_flop and not invalidated)
    return {
        "active_side": side if side in {"BUY", "SELL"} else None,
        "recent_sides": recent,
        "stable_reads": stable_reads,
        "flip_flop_risk": unstable,
        "executable_allowed": not unstable,
        "deny_reason": "CANDIDATE_QUEUE_UNSTABLE" if unstable else None,
        "reason": "Candidate queue is flip-flopping; executable permission is contained." if unstable else "Candidate queue is stable enough for study.",
    }


def _permission_layer(name: str, passed: bool, deny_reason: str | None, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "deny_reason": deny_reason if not passed else None,
        "reason": reason,
    }


def _permission_stack(
    side: str,
    entry_quality: Mapping[str, Any],
    trap: Mapping[str, Any],
    ideal_path: Mapping[str, Any],
    path_risk: Mapping[str, Any],
    timing_path: Mapping[str, Any],
    candle_contract: Mapping[str, Any],
    queue: Mapping[str, Any],
    playbook: Mapping[str, Any],
) -> dict[str, Any]:
    layers = [
        _permission_layer("direction_candidate", side in {"BUY", "SELL"}, "NO_DIRECTION_CANDIDATE", "A BUY or SELL candidate must exist."),
        _permission_layer(
            "entry_quality",
            bool(entry_quality.get("passes_executable_threshold")),
            "ENTRY_QUALITY_BELOW_ACCEPTABLE",
            str(entry_quality.get("reason") or ""),
        ),
        _permission_layer("market_trap", bool(trap.get("executable_allowed", True)), trap.get("deny_reason"), str(trap.get("reason") or "")),
        _permission_layer("ideal_trade_path", bool(ideal_path.get("executable_allowed", True)), ideal_path.get("deny_reason"), str(ideal_path.get("reason") or "")),
        _permission_layer("path_risk", bool(path_risk.get("executable_allowed", True)), path_risk.get("deny_reason"), str(path_risk.get("reason") or "")),
        _permission_layer("time_to_reward_invalidation", bool(timing_path.get("executable_allowed", True)), timing_path.get("deny_reason"), str(timing_path.get("reason") or "")),
        _permission_layer("current_candle_contract", bool(candle_contract.get("executable_allowed", True)), candle_contract.get("deny_reason"), str(candle_contract.get("reason") or "")),
        _permission_layer("trade_candidate_queue", bool(queue.get("executable_allowed", True)), queue.get("deny_reason"), str(queue.get("reason") or "")),
        _permission_layer("regime_playbook", bool(playbook.get("executable_allowed", True)), playbook.get("deny_reason"), str(playbook.get("reason") or "")),
    ]
    failed_reasons = [str(layer["deny_reason"]) for layer in layers if not layer["passed"] and layer.get("deny_reason")]
    first_reason = None
    for reason in PERMISSION_DENY_PRIORITY:
        if reason in failed_reasons:
            first_reason = reason
            break
    if first_reason is None and failed_reasons:
        first_reason = failed_reasons[0]

    hard_prepare_reasons = {
        "NO_DIRECTION_CANDIDATE",
        "LATE_CHASE_TRAP",
        "MARKET_TRAP_DETECTED",
        "IDEAL_PATH_PROTECT",
        "IDEAL_PATH_HOLD",
        "IDEAL_PATH_WAIT",
        "PATH_RISK_WEAK",
        "CANDIDATE_QUEUE_UNSTABLE",
        "REGIME_PLAYBOOK_DENIES_ENTRY",
    }
    hard_entry_quality = (
        "ENTRY_QUALITY_BELOW_ACCEPTABLE" in failed_reasons
        and str(entry_quality.get("state") or "").upper() in {"BAD_NOW", "UNACCEPTABLE_ENTRY"}
    )
    prepare_allowed = bool(not hard_entry_quality and not any(reason in hard_prepare_reasons for reason in failed_reasons))
    executable_allowed = not failed_reasons
    denied_layer = next((layer for layer in layers if not layer["passed"]), None)
    next_condition_by_reason = {
        "NO_DIRECTION_CANDIDATE": "Wait for BUY or SELL dominance to become measurable.",
        "ENTRY_QUALITY_BELOW_ACCEPTABLE": str(entry_quality.get("recommended_wait_condition") or "Wait for entry quality to reach ACCEPTABLE_ENTRY."),
        "LATE_CHASE_TRAP": "Wait for pullback/retest into a qualified trigger zone.",
        "MARKET_TRAP_DETECTED": "Wait until the active trap class clears.",
        "IDEAL_PATH_PROTECT": "Do not open a new entry where the ideal path says protect or exit.",
        "IDEAL_PATH_HOLD": "Wait until current price returns to a fresh entry area.",
        "IDEAL_PATH_WAIT": "Wait for the ideal path to offer a new entry.",
        "PATH_RISK_WEAK": "Wait for cleaner reward path before invalidation or opposing force.",
        "CANDIDATE_QUEUE_UNSTABLE": "Wait for the candidate side to stabilize across live reads.",
        "REGIME_PLAYBOOK_DENIES_ENTRY": "Wait for the current regime playbook confirmation.",
    }
    return {
        "permission_state": "GRANTED" if executable_allowed else "DENIED",
        "side": side if side in {"BUY", "SELL"} and executable_allowed else None,
        "denied_at": str(denied_layer.get("name", "")).upper() if denied_layer else None,
        "study_allowed": True,
        "prepare_allowed": prepare_allowed,
        "executable_allowed": executable_allowed,
        "deny_reason": first_reason,
        "next_required_condition": next_condition_by_reason.get(str(first_reason), "Wait for all permission layers to pass.") if not executable_allowed else "All permission layers passed.",
        "failed_reasons": failed_reasons,
        "layers": layers,
        "reason": "Execution permission granted." if executable_allowed else f"Execution permission denied: {first_reason}.",
    }


def analyze_market_reality(
    snapshot: Mapping[str, Any] | None,
    *,
    side: str | None = None,
    market_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Model Council permission evidence without owning shooter execution."""

    source = dict(snapshot or {})
    inputs = dict(market_inputs or {})
    resolved_side = _extract_side(source, side, inputs)
    trap = _market_trap(source, inputs)
    entry_quality = _entry_quality(source, resolved_side, inputs, trap_detected=bool(trap["detected"]))
    timing_path = _time_to_reward_invalidation(source)
    path_risk = _path_risk(source, inputs, timing_path, trap)
    ideal_path = _ideal_trade_path(source, resolved_side, inputs, entry_quality, trap, path_risk)
    candle_contract = _current_candle_contract(source, resolved_side)
    playbook = _regime_playbook(source, resolved_side, inputs)
    queue = _trade_candidate_queue(source, resolved_side)
    listening_stream = _market_listening_stream(source, entry_quality, trap, path_risk, timing_path)
    permission = _permission_stack(
        resolved_side,
        entry_quality,
        trap,
        ideal_path,
        path_risk,
        timing_path,
        candle_contract,
        queue,
        playbook,
    )
    frame_id = _int(source.get("frame_id"), 0)
    capture_count = _int(source.get("capture_count"), 0)
    market_context = _mapping(inputs.get("market_context") or source.get("market_context"))
    direction_strength = max(_clip01(source.get("confidence"), 0.0), entry_quality.get("direction_confidence", 0.0))
    market_phase = str(playbook.get("regime") or "UNKNOWN")
    if trap.get("detected"):
        market_phase = str(trap.get("trap_type") or "TRAP_RISK")
    elif not entry_quality.get("passes_executable_threshold"):
        market_phase = "ENTRY_QUALITY_WAIT"
    elif permission.get("executable_allowed"):
        market_phase = "EXECUTABLE_QUALITY"
    allowed_action = "EXECUTE" if permission.get("executable_allowed") else ("PREPARE" if permission.get("prepare_allowed") else "WATCH")
    forbidden_action = "CHASE_" + resolved_side if resolved_side in {"BUY", "SELL"} and not permission.get("executable_allowed") else "NONE"
    return {
        "version": PG_MARKET_REALITY_ENGINE_VERSION,
        "role": "MODEL_COUNCIL_INPUT",
        "state_id": f"mr_{frame_id}_{capture_count}_{resolved_side.lower()}",
        "symbol_context": str(source.get("symbol") or source.get("market") or "LOCKED_ACTIVE_CHART"),
        "timeframe": str(source.get("timeframe") or source.get("focus_timeframe") or ""),
        "market_phase": market_phase,
        "dominant_force": resolved_side,
        "dominant_force_strength": round(_clip01(direction_strength), 4),
        "opposing_force": str(market_context.get("nearest_opposing_force") or market_context.get("nearest_supply_zone_id") or market_context.get("nearest_demand_zone_id") or ""),
        "opposing_force_pressure": round(1.0 - _clip01(path_risk.get("score"), 0.0), 4),
        "price_location": str(market_context.get("current_location") or ""),
        "entry_quality_now": str(entry_quality.get("state") or ""),
        "allowed_action": allowed_action,
        "forbidden_action": forbidden_action,
        "reason": str(permission.get("reason") or entry_quality.get("reason") or ""),
        "side": resolved_side,
        "direction_context": {
            "side": resolved_side,
            "direction_is_separate_from_entry_quality": True,
        },
        "entry_quality": entry_quality,
        "trade_permission": permission,
        "permission": permission,
        "market_trap": trap,
        "ideal_trade_path": ideal_path,
        "path_risk": path_risk,
        "regime_playbook": playbook,
        "time_to_reward_invalidation": timing_path,
        "current_candle_contract": candle_contract,
        "market_listening_stream": listening_stream,
        "trade_candidate_queue": queue,
        "block_reason": permission["deny_reason"],
    }


__all__ = [
    "ACCEPTABLE_ENTRY",
    "ENTRY_QUALITY_RANKS",
    "PG_MARKET_REALITY_ENGINE_VERSION",
    "analyze_market_reality",
]
