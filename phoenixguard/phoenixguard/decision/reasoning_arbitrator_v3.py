from __future__ import annotations

from typing import Any, Mapping, Sequence


REASONING_ARBITRATOR_VERSION = "PG_REASONING_ARBITRATOR_V3"

DECISION_STATES = {
    "WATCH",
    "TRACK_CANDIDATE",
    "WAIT_FOR_PULLBACK",
    "WAIT_FOR_RETEST",
    "WAIT_FOR_REJECTION",
    "WAIT_FOR_BREAK_CONFIRMATION",
    "PREPARE",
    "ENTER_NOW",
    "ABORT",
}

BAD_ENTRY_CLASSES = {
    "BUY_HIGH_AFTER_IMPULSE",
    "SELL_LOW_AFTER_DROP",
    "MIDDLE_RANGE_NO_EDGE",
    "LATE_CHASE",
    "AGAINST_GLOBAL_STRUCTURE",
    "INTO_OPPOSING_FORCE",
    "WICK_TRAP",
    "FAKE_BREAKOUT_RISK",
    "NO_PATH_ROOM",
    "DRAWDOWN_FIRST_EXPECTED",
    "MEMORY_SAYS_FAILED_BEFORE",
}

MODEL_ROLE_NAMES = (
    "Global Structure Expert",
    "Local Candle Play Expert",
    "Supply/Demand and Zone Expert",
    "Angle and Momentum Expert",
    "Memory Similarity Expert",
    "Timing and Path Expert",
    "Risk and Trap Expert",
)

ROLE_MODEL_MAP = {
    "Global Structure Expert": "dinov2",
    "Local Candle Play Expert": "mobilenetv3",
    "Supply/Demand and Zone Expert": "clip",
    "Angle and Momentum Expert": "simclr",
    "Memory Similarity Expert": "swav",
    "Timing and Path Expert": "lstm_candle_sequence",
    "Risk and Trap Expert": "byol",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


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


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "pass", "passed"}
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


def _opposite(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"


def _confidence_label(value: float) -> str:
    if value >= 0.72:
        return "HIGH"
    if value >= 0.58:
        return "MEDIUM"
    return "LOW"


def _side_quality(price_location: Mapping[str, Any], side: str) -> str:
    if side == "BUY":
        return _upper(price_location.get("buy_quality"), "NEEDS_CONFIRMATION")
    if side == "SELL":
        return _upper(price_location.get("sell_quality"), "NEEDS_CONFIRMATION")
    return "NEEDS_CONFIRMATION"


def _quality_score(label: str) -> float:
    normalized = _upper(label)
    if normalized in {"GOOD", "IDEAL", "A_PLUS"}:
        return 0.86
    if normalized in {"ACCEPTABLE", "NEEDS_CONFIRMATION", "MIDDLE_OK"}:
        return 0.58
    if normalized in {"POOR", "BAD", "UNACCEPTABLE"}:
        return 0.25
    return 0.50


def _role_vote(
    role: str,
    *,
    model: str = "",
    side_vote: str,
    play_vote: str,
    regime_vote: str,
    confidence: float,
    evidence: str,
    frames_used: int,
    freshness_ms: int = 0,
    risk_warning: str = "",
) -> dict[str, Any]:
    return {
        "model": model or ROLE_MODEL_MAP.get(role, role.lower().replace(" ", "_")),
        "role": role,
        "side_vote": _side(side_vote),
        "play_vote": _upper(play_vote, "UNKNOWN"),
        "regime_vote": _upper(regime_vote, "UNKNOWN"),
        "confidence": round(_clip01(confidence), 4),
        "frames_used": int(max(0, frames_used)),
        "freshness_ms": int(max(0, freshness_ms)),
        "evidence": evidence,
        "risk_warning": risk_warning,
    }


def _normalize_role_vote(
    row: Mapping[str, Any],
    *,
    fallback_role: str,
    fallback_play: str,
    fallback_regime: str,
    fallback_side: str,
    fallback_frames_used: int,
    fallback_freshness_ms: int,
) -> dict[str, Any]:
    role = str(row.get("role") or fallback_role).strip() or fallback_role
    evidence = str(row.get("evidence") or row.get("reason") or "Model role evidence was supplied without narrative.").strip()
    return _role_vote(
        role,
        model=str(row.get("model") or row.get("model_name") or ROLE_MODEL_MAP.get(role, "")),
        side_vote=str(row.get("side_vote") or row.get("vote") or fallback_side),
        play_vote=str(row.get("play_vote") or fallback_play),
        regime_vote=str(row.get("regime_vote") or fallback_regime),
        confidence=_clip01(row.get("confidence"), 0.0),
        evidence=evidence,
        frames_used=int(_float(row.get("frames_used"), fallback_frames_used)),
        freshness_ms=int(_float(row.get("freshness_ms"), fallback_freshness_ms)),
        risk_warning=str(row.get("risk_warning") or ""),
    )


def build_model_role_votes_v3(
    snapshot: Mapping[str, Any] | None,
    *,
    side: str,
    market_play: Mapping[str, Any],
    regime: Mapping[str, Any],
    price_location: Mapping[str, Any],
    memory_confirmation: Mapping[str, Any],
    pair_profile: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    source = dict(snapshot or {})
    supplied = _rows(source.get("model_role_outputs") or source.get("role_outputs") or source.get("model_roles"))
    if supplied and all({"role", "side_vote", "play_vote", "confidence", "evidence", "frames_used"}.issubset(row.keys()) for row in supplied):
        fallback_frames = int(_float(source.get("frames_used", source.get("sequence_length", source.get("visible_candle_count", 0))), 0.0))
        fallback_freshness = int(_float(source.get("model_vote_age_ms", source.get("packet_age_ms", 0)), 0.0))
        fallback_play = _upper(market_play.get("primary_play"), "UNKNOWN")
        fallback_regime = _upper(regime.get("primary"), "UNKNOWN")
        return [
            _normalize_role_vote(
                row,
                fallback_role=MODEL_ROLE_NAMES[index] if index < len(MODEL_ROLE_NAMES) else f"Role {index + 1}",
                fallback_play=fallback_play,
                fallback_regime=fallback_regime,
                fallback_side=side,
                fallback_frames_used=fallback_frames,
                fallback_freshness_ms=fallback_freshness,
            )
            for index, row in enumerate(supplied[:7])
        ]

    resolved_side = _side(side or market_play.get("side_bias"))
    play = _upper(market_play.get("primary_play"), "UNKNOWN")
    regime_primary = _upper(regime.get("primary"), "UNKNOWN")
    frames_used = int(_float(source.get("frames_used", source.get("sequence_length", source.get("visible_candle_count", 0))), 0.0))
    freshness_ms = int(_float(source.get("model_vote_age_ms", source.get("packet_age_ms", 0)), 0.0))
    market_context = _mapping(source.get("market_context"))
    global_side = _side(source.get("global_side") or market_context.get("global_side") or _mapping(source.get("global_structure")).get("global_side"))
    local_side = _side(source.get("local_side") or market_context.get("local_side") or _mapping(source.get("local_micro_structure")).get("local_side"))
    location = _upper(price_location.get("relative_location"), "MIDDLE")
    memory_vote = _side(memory_confirmation.get("memory_vote"))
    side_quality = _side_quality(price_location, resolved_side)
    pair = pair_profile or {}
    drawdown_first = _clip01(pair.get("drawdown_first_frequency"), 0.0)
    wick_risk = _clip01(regime.get("wick_risk"), 0.0)

    zone_vote = "BUY" if location == "LOCAL_LOW" else "SELL" if location == "LOCAL_HIGH" else resolved_side
    timing_vote = resolved_side if side_quality in {"GOOD", "ACCEPTABLE", "NEEDS_CONFIRMATION"} else "HOLD"
    risk_vote = "HOLD" if side_quality == "POOR" or drawdown_first >= 0.58 or wick_risk >= 0.72 else resolved_side

    return [
        _role_vote(
            "Global Structure Expert",
            side_vote=global_side if global_side in {"BUY", "SELL"} else resolved_side,
            play_vote=play,
            regime_vote=regime_primary,
            confidence=max(_clip01(source.get("global_confidence"), 0.0), 0.62 if global_side == resolved_side else 0.42),
            evidence=f"Global context votes {global_side or 'HOLD'} inside {regime_primary}.",
            frames_used=frames_used,
            freshness_ms=freshness_ms,
        ),
        _role_vote(
            "Local Candle Play Expert",
            side_vote=local_side if local_side in {"BUY", "SELL"} else resolved_side,
            play_vote=play,
            regime_vote=regime_primary,
            confidence=max(_clip01(source.get("local_confidence"), 0.0), _clip01(market_play.get("confidence"), 0.0)),
            evidence=str(market_play.get("reason") or "Local candle play is classified by the market play engine."),
            frames_used=frames_used,
            freshness_ms=freshness_ms,
        ),
        _role_vote(
            "Supply/Demand and Zone Expert",
            side_vote=zone_vote,
            play_vote=play,
            regime_vote=regime_primary,
            confidence=_quality_score(side_quality),
            evidence=f"Price location is {location}; {resolved_side} location quality is {side_quality}.",
            frames_used=frames_used,
            freshness_ms=freshness_ms,
        ),
        _role_vote(
            "Angle and Momentum Expert",
            side_vote=resolved_side,
            play_vote=play,
            regime_vote=regime_primary,
            confidence=0.52 if play in {"LATE_CHASE_AFTER_IMPULSE", "TREND_EXHAUSTION"} else 0.68,
            evidence=f"Momentum context votes {play}.",
            frames_used=frames_used,
            freshness_ms=freshness_ms,
            risk_warning="Late chase or exhaustion risk is active." if play in {"LATE_CHASE_AFTER_IMPULSE", "TREND_EXHAUSTION"} else "",
        ),
        _role_vote(
            "Memory Similarity Expert",
            side_vote=memory_vote if memory_vote in {"BUY", "SELL"} else resolved_side,
            play_vote=play,
            regime_vote=regime_primary,
            confidence=max(0.35, _clip01(memory_confirmation.get("similarity"), 0.35)),
            evidence="Memory confirms similar structure." if memory_confirmation.get("confirmed") else "Memory is diagnostic and does not force execution.",
            frames_used=frames_used,
            freshness_ms=freshness_ms,
            risk_warning=str(memory_confirmation.get("warning") or ""),
        ),
        _role_vote(
            "Timing and Path Expert",
            side_vote=timing_vote,
            play_vote=play,
            regime_vote=regime_primary,
            confidence=0.72 if timing_vote == resolved_side and side_quality != "POOR" else 0.44,
            evidence=f"Timing prefers {timing_vote}; path quality follows {side_quality} location.",
            frames_used=frames_used,
            freshness_ms=freshness_ms,
            risk_warning="Better entry may come after pullback/retest." if side_quality == "POOR" else "",
        ),
        _role_vote(
            "Risk and Trap Expert",
            side_vote=risk_vote,
            play_vote=play,
            regime_vote=regime_primary,
            confidence=0.42 if risk_vote == "HOLD" else 0.64,
            evidence="Risk is acceptable for continued study." if risk_vote != "HOLD" else "Risk requires waiting before execution.",
            frames_used=frames_used,
            freshness_ms=freshness_ms,
            risk_warning="Wick or drawdown-first risk is elevated." if risk_vote == "HOLD" else "",
        ),
    ]


def detect_bad_entry_filter_v3(
    *,
    side: str,
    market_play: Mapping[str, Any],
    regime: Mapping[str, Any],
    price_location: Mapping[str, Any],
    memory_confirmation: Mapping[str, Any],
    pair_profile: Mapping[str, Any],
    market_context: Mapping[str, Any] | None = None,
    existing_block_reason: str | None = None,
) -> dict[str, Any]:
    resolved_side = _side(side or market_play.get("side_bias"))
    context = dict(market_context or {})
    global_side = _side(context.get("global_side") or regime.get("global_side"))
    play = _upper(market_play.get("primary_play"))
    location = _upper(price_location.get("relative_location"), "MIDDLE")
    side_quality = _side_quality(price_location, resolved_side)
    path_room = _clip01(price_location.get("path_room"), 1.0)
    wick_risk = _clip01(regime.get("wick_risk"), 0.0)
    fakeout_risk = _clip01(regime.get("fakeout_risk"), 0.0)
    drawdown_first = _clip01(pair_profile.get("drawdown_first_frequency"), 0.0)
    memory_adjustment = _float(memory_confirmation.get("confidence_adjustment"), 0.0)
    opposing_force_ok = context.get("opposing_force_distance_ok")
    candidates: list[tuple[float, str, str, str]] = []

    def add(severity: float, class_name: str, action: str, reason: str) -> None:
        if class_name in BAD_ENTRY_CLASSES:
            candidates.append((_clip01(severity), class_name, action, reason))

    if resolved_side == "BUY" and location == "LOCAL_HIGH" and (play in {"BULLISH_IMPULSE", "LATE_CHASE_AFTER_IMPULSE", "TREND_CONTINUATION"} or side_quality == "POOR"):
        add(0.82, "BUY_HIGH_AFTER_IMPULSE", "WAIT_FOR_PULLBACK", "BUY direction is visible, but price is high after expansion.")
    if resolved_side == "SELL" and location == "LOCAL_LOW" and (play in {"BEARISH_IMPULSE", "LATE_CHASE_AFTER_IMPULSE", "TREND_CONTINUATION"} or side_quality == "POOR"):
        add(0.82, "SELL_LOW_AFTER_DROP", "WAIT_FOR_PULLBACK", "SELL direction is visible, but price is already low after a drop.")
    if location == "MIDDLE" and play in {"MID_RANGE_NO_EDGE", "TREND_CONTINUATION"}:
        add(0.55, "MIDDLE_RANGE_NO_EDGE", "TRACK_CANDIDATE", "Middle trades need stronger confirmation and path room.")
    if play == "LATE_CHASE_AFTER_IMPULSE":
        add(0.86, "LATE_CHASE", "WAIT_FOR_PULLBACK", "The move is mature; wait for pullback or retest instead of chasing.")
    if resolved_side in {"BUY", "SELL"} and global_side == _opposite(resolved_side) and play not in {"LIQUIDITY_SWEEP_REVERSAL", "BULLISH_REVERSAL_FORMING", "BEARISH_REVERSAL_FORMING"}:
        add(0.68, "AGAINST_GLOBAL_STRUCTURE", "WAIT_FOR_REJECTION", "Candidate side is against global structure without reversal confirmation.")
    if opposing_force_ok is False:
        add(0.76, "INTO_OPPOSING_FORCE", "WAIT_FOR_RETEST", "Opposing force is too close for a clean path.")
    if wick_risk >= 0.68:
        add(0.58 + 0.30 * wick_risk, "WICK_TRAP", "WAIT_FOR_REJECTION", "Wick risk is elevated; wait for rejection confirmation.")
    if fakeout_risk >= 0.58 or play in {"FAKE_BREAKOUT", "FAKE_BREAKDOWN"}:
        add(0.74, "FAKE_BREAKOUT_RISK", "WAIT_FOR_BREAK_CONFIRMATION", "Fakeout risk is active; wait for reclaim/retest confirmation.")
    if path_room <= 0.16 or opposing_force_ok is False:
        add(0.70, "NO_PATH_ROOM", "WAIT_FOR_RETEST", "There is not enough path room before opposing force or invalidation.")
    if drawdown_first >= 0.52:
        add(0.50 + 0.28 * drawdown_first, "DRAWDOWN_FIRST_EXPECTED", "WAIT_FOR_PULLBACK", "This pair often moves against the entry before continuation.")
    if memory_adjustment < -0.025 or "failed" in str(memory_confirmation.get("warning") or "").lower():
        add(0.66, "MEMORY_SAYS_FAILED_BEFORE", "WATCH", "Similar memory plays include failed or late entries.")

    if existing_block_reason and _upper(existing_block_reason) in {"OPPOSING_FORCE_TOO_CLOSE"}:
        add(0.76, "INTO_OPPOSING_FORCE", "WAIT_FOR_RETEST", "Existing market evidence says opposing force is too close.")
    if existing_block_reason and _upper(existing_block_reason) in {"FALSE_BREAKOUT_RISK"}:
        add(0.74, "FAKE_BREAKOUT_RISK", "WAIT_FOR_BREAK_CONFIRMATION", "Existing market evidence says fakeout risk is active.")

    if not candidates:
        return {
            "active": False,
            "class": "NONE",
            "severity": 0.0,
            "action": "NONE",
            "reason": "No bad-entry prevention class is active.",
        }
    severity, class_name, action, reason = max(candidates, key=lambda item: item[0])
    return {
        "active": True,
        "class": class_name,
        "severity": round(severity, 4),
        "action": action,
        "reason": reason,
    }


def analyze_reasoning_arbitration_v3(
    snapshot: Mapping[str, Any] | None,
    *,
    side: str,
    market_play: Mapping[str, Any],
    regime: Mapping[str, Any],
    price_location: Mapping[str, Any],
    memory_confirmation: Mapping[str, Any],
    pair_profile: Mapping[str, Any],
    model_role_votes: Sequence[Mapping[str, Any]] | None = None,
    timing_decision: Mapping[str, Any] | None = None,
    market_context: Mapping[str, Any] | None = None,
    existing_block_reason: str | None = None,
) -> dict[str, Any]:
    source = dict(snapshot or {})
    resolved_side = _side(side or market_play.get("side_bias"))
    roles = [dict(row) for row in (model_role_votes or build_model_role_votes_v3(
        source,
        side=resolved_side,
        market_play=market_play,
        regime=regime,
        price_location=price_location,
        memory_confirmation=memory_confirmation,
        pair_profile=pair_profile,
    ))]
    if len(roles) < 7:
        roles = build_model_role_votes_v3(
            source,
            side=resolved_side,
            market_play=market_play,
            regime=regime,
            price_location=price_location,
            memory_confirmation=memory_confirmation,
            pair_profile=pair_profile,
        )
    side_votes = [row for row in roles if _side(row.get("side_vote")) in {"BUY", "SELL"}]
    aligned_votes = [
        _clip01(row.get("confidence"), 0.0)
        for row in side_votes
        if _side(row.get("side_vote")) == resolved_side
    ]
    conflict_votes = [
        _clip01(row.get("confidence"), 0.0)
        for row in side_votes
        if _side(row.get("side_vote")) not in {resolved_side, "HOLD"}
    ]
    total_vote_conf = sum(_clip01(row.get("confidence"), 0.0) for row in side_votes) or 1.0
    role_alignment = _clip01((sum(aligned_votes) - 0.55 * sum(conflict_votes)) / total_vote_conf, 0.0)
    play_confidence = _clip01(market_play.get("confidence"), 0.0)
    location_score = _quality_score(_side_quality(price_location, resolved_side))
    memory_score = _clip01(0.50 + 3.0 * _float(memory_confirmation.get("confidence_adjustment"), 0.0), 0.50)
    drawdown_first = _clip01(pair_profile.get("drawdown_first_frequency"), 0.0)
    pair_score = _clip01(0.88 - 0.34 * drawdown_first - 0.18 * _clip01(pair_profile.get("fakeout_frequency"), 0.0), 0.60)
    regime_primary = _upper(regime.get("primary"))
    regime_score = 0.76
    if regime_primary in {"FAKEOUT_RISK", "CHOPPY", "VOLATILE_WICKY"}:
        regime_score = 0.48
    elif regime_primary in {"RANGING", "COMPRESSION"}:
        regime_score = 0.58
    elif regime_primary in {"PULLBACK_PHASE", "TRENDING_UP", "TRENDING_DOWN"}:
        regime_score = 0.80
    bad_entry = detect_bad_entry_filter_v3(
        side=resolved_side,
        market_play=market_play,
        regime=regime,
        price_location=price_location,
        memory_confirmation=memory_confirmation,
        pair_profile=pair_profile,
        market_context=market_context,
        existing_block_reason=existing_block_reason,
    )
    bad_penalty = 0.28 * _clip01(bad_entry.get("severity"), 0.0) if bad_entry.get("active") else 0.0
    coherence_score = _clip01(
        0.26 * role_alignment
        + 0.22 * play_confidence
        + 0.18 * location_score
        + 0.12 * memory_score
        + 0.12 * regime_score
        + 0.10 * pair_score
        - bad_penalty,
        0.0,
    )
    conflict = _clip01(sum(conflict_votes) / total_vote_conf, 0.0)
    timing = dict(timing_decision or {})
    timing_mode = _upper(timing.get("timing_mode"))
    entry_now_allowed = _bool(timing.get("entry_now_allowed")) or timing_mode == "ENTER_NOW"
    path_class = _upper(timing.get("path_class"), "UNKNOWN")

    if bad_entry.get("active") and _clip01(bad_entry.get("severity"), 0.0) >= 0.76:
        state = str(bad_entry.get("action") or "WATCH")
    elif entry_now_allowed and coherence_score >= 0.70 and conflict <= 0.34:
        state = "ENTER_NOW"
    elif coherence_score >= 0.66 and resolved_side in {"BUY", "SELL"}:
        state = "PREPARE"
    elif coherence_score >= 0.54 and resolved_side in {"BUY", "SELL"}:
        state = "TRACK_CANDIDATE"
    else:
        state = "WATCH"
    if state not in DECISION_STATES:
        state = "WATCH"

    next_required = "none"
    if state != "ENTER_NOW":
        if bad_entry.get("active"):
            next_required = str(bad_entry.get("reason") or "Wait for bad-entry risk to clear.")
        elif timing_mode and timing_mode != "ENTER_NOW":
            next_required = f"timing_mode=ENTER_NOW required; current timing_mode={timing_mode}"
        elif conflict > 0.34:
            next_required = "Reduce model-role conflict before execution."
        else:
            next_required = "Wait for clearer play, location, memory, or timing alignment."

    reasoning_consistency = _confidence_label(coherence_score)
    conflict_label = _confidence_label(conflict)
    risk = "HIGH" if bad_entry.get("active") and _clip01(bad_entry.get("severity"), 0.0) >= 0.72 else "ELEVATED" if bad_entry.get("active") else "ACCEPTABLE"
    memory_state = "CONFIRMED" if memory_confirmation.get("confirmed") else "DIAGNOSTIC"
    final_reason = (
        f"{resolved_side} is executable because play, regime, location, memory, timing, and role evidence are coherent."
        if state == "ENTER_NOW"
        else str(bad_entry.get("reason") or next_required)
    )
    return {
        "version": REASONING_ARBITRATOR_VERSION,
        "model_role_outputs": roles,
        "bad_entry_filter": bad_entry,
        "arbitration": {
            "coherence_score": round(coherence_score, 4),
            "side": resolved_side if resolved_side in {"BUY", "SELL"} else "HOLD",
            "state": state,
            "play": _upper(market_play.get("primary_play"), "UNKNOWN"),
            "reasoning_consistency": reasoning_consistency,
            "conflict": conflict_label,
            "conflict_score": round(conflict, 4),
            "next_required": next_required,
        },
        "final_reasoning_decision": {
            "side": resolved_side if resolved_side in {"BUY", "SELL"} else None,
            "decision": state,
            "confidence": round(coherence_score, 4),
            "play": _upper(market_play.get("primary_play"), "UNKNOWN"),
            "regime": f"{regime_primary}_{_upper(regime.get('secondary'))}" if regime.get("secondary") not in {None, "", "NONE"} else regime_primary,
            "price_location": _upper(price_location.get("relative_location"), "UNKNOWN"),
            "memory_confirmation": memory_state,
            "timing_mode": timing_mode or ("ENTER_NOW" if state == "ENTER_NOW" else "WAIT"),
            "path_class": path_class,
            "risk": risk,
            "reason": final_reason,
        },
    }


__all__ = [
    "BAD_ENTRY_CLASSES",
    "DECISION_STATES",
    "MODEL_ROLE_NAMES",
    "REASONING_ARBITRATOR_VERSION",
    "analyze_reasoning_arbitration_v3",
    "build_model_role_votes_v3",
    "detect_bad_entry_filter_v3",
]
