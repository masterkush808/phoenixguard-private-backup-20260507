from __future__ import annotations

import hashlib
from typing import Any, Mapping, cast


MARKET_PLAY_ENGINE_VERSION = "PG_MARKET_PLAY_ENGINE_V3"

MARKET_PLAY_CLASSES = {
    "BULLISH_IMPULSE",
    "BEARISH_IMPULSE",
    "BULLISH_PULLBACK_CONTINUATION",
    "BEARISH_PULLBACK_CONTINUATION",
    "BULLISH_REVERSAL_FORMING",
    "BEARISH_REVERSAL_FORMING",
    "RANGE_LOW_BUY_REACTION",
    "RANGE_HIGH_SELL_REACTION",
    "LIQUIDITY_SWEEP_REVERSAL",
    "FAKE_BREAKOUT",
    "FAKE_BREAKDOWN",
    "LATE_CHASE_AFTER_IMPULSE",
    "MID_RANGE_NO_EDGE",
    "SUPPLY_REJECTION",
    "DEMAND_REJECTION",
    "TREND_CONTINUATION",
    "TREND_EXHAUSTION",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


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


def _play_id(seed: str) -> str:
    return "play_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def analyze_market_play_v3(
    snapshot: Mapping[str, Any] | None,
    *,
    side: str | None = None,
    regime: Mapping[str, Any] | None = None,
    price_location: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = dict(snapshot or {})
    market_context = _mapping(source.get("market_context"))
    angle = _mapping(source.get("angle_features") or source.get("angle_context") or source.get("angle_dynamics"))
    local_micro = _mapping(source.get("local_micro_structure"))
    resolved_side = _side(side or source.get("candidate_side") or market_context.get("dominant_side"))
    global_side = _side(source.get("global_side") or market_context.get("global_side") or _mapping(source.get("global_structure")).get("global_side"))
    local_side = _side(source.get("local_side") or market_context.get("local_side") or local_micro.get("local_side"))
    regime_primary = _upper((regime or {}).get("primary"))
    relative_location = _upper((price_location or {}).get("relative_location") or market_context.get("current_location"))
    local_range_position = _clip01((price_location or {}).get("local_range_position"), 0.5)
    pullback_confirmed = _bool(source.get("pullback_confirmed") or source.get("retest_confirmed"))
    continuation = _bool(
        source.get("continuation_confirmed")
        or source.get("retest_confirmed")
        or source.get("pullback_confirmed")
        or _clip01(source.get("continuation_probability"), 0.0) >= 0.56
        or market_context.get("is_continuation_confirmed")
    )
    rejection_score = max(
        _clip01(source.get("rejection_score"), 0.0),
        _clip01(angle.get("wick_rejection_score"), 0.0),
        _clip01(local_micro.get("rejection_score"), 0.0),
    )
    impulse_length = max(_clip01(angle.get("impulse_length"), 0.0), _clip01(source.get("impulse_length"), 0.0))
    late_chase = _bool(angle.get("late_chase_risk") or angle.get("post_impulse_wait_required") or market_context.get("is_late_chase"))
    liquidity_sweep = _bool(source.get("liquidity_sweep_detected") or market_context.get("liquidity_sweep_detected"))
    false_breakout = _bool(source.get("false_breakout_risk") or source.get("fake_breakout_risk") or market_context.get("false_breakout_risk"))
    breakout_confirmed = _bool(source.get("breakout_confirmed") or source.get("breakout_phase"))
    reversal_forming = _bool(source.get("reversal_confirmed") or source.get("reversal_forming") or market_context.get("is_reversal_confirmed"))

    primary_play = "MID_RANGE_NO_EDGE"
    secondary_play = "NONE"
    play_stage = "STUDYING"
    local_context = "FORMING"
    entry_logic = "WAIT_FOR_CLEAR_PLAY"
    confidence = 0.42

    if false_breakout and resolved_side == "BUY":
        primary_play = "FAKE_BREAKOUT"
        play_stage = "RECLAIM_NOT_CONFIRMED"
        local_context = "BREAKOUT_UNSTABLE"
        entry_logic = "WAIT_FOR_BREAK_CONFIRMATION"
        confidence = 0.76
    elif false_breakout and resolved_side == "SELL":
        primary_play = "FAKE_BREAKDOWN"
        play_stage = "RECLAIM_NOT_CONFIRMED"
        local_context = "BREAKDOWN_UNSTABLE"
        entry_logic = "WAIT_FOR_BREAK_CONFIRMATION"
        confidence = 0.76
    elif liquidity_sweep and reversal_forming:
        primary_play = "LIQUIDITY_SWEEP_REVERSAL"
        secondary_play = "BEARISH_REVERSAL_FORMING" if resolved_side == "SELL" else "BULLISH_REVERSAL_FORMING" if resolved_side == "BUY" else "NONE"
        play_stage = "REVERSAL_FORMING"
        local_context = "SWEEP_REJECTING"
        entry_logic = "ENTER_AFTER_REJECTION" if rejection_score >= 0.45 else "WAIT_FOR_REJECTION"
        confidence = 0.70 + 0.10 * rejection_score
    elif late_chase or (impulse_length >= 0.72 and not pullback_confirmed):
        primary_play = "LATE_CHASE_AFTER_IMPULSE"
        secondary_play = "TREND_EXHAUSTION" if impulse_length >= 0.78 else "NONE"
        play_stage = "MATURE_OR_LATE"
        local_context = "POST_IMPULSE"
        entry_logic = "WAIT_FOR_PULLBACK"
        confidence = max(0.68, impulse_length)
    elif regime_primary in {"RANGING", "CHOPPY", "COMPRESSION"} and relative_location == "LOCAL_LOW":
        primary_play = "RANGE_LOW_BUY_REACTION"
        secondary_play = "DEMAND_REJECTION"
        play_stage = "REACTION_FORMING" if rejection_score < 0.45 else "REJECTION_CONFIRMED"
        local_context = "RANGE_LOW"
        entry_logic = "BUY_LOW_AFTER_DEMAND_REACTION"
        resolved_side = "BUY" if resolved_side == "HOLD" else resolved_side
        confidence = 0.62 + 0.16 * rejection_score
    elif regime_primary in {"RANGING", "CHOPPY", "COMPRESSION"} and relative_location == "LOCAL_HIGH":
        primary_play = "RANGE_HIGH_SELL_REACTION"
        secondary_play = "SUPPLY_REJECTION"
        play_stage = "REACTION_FORMING" if rejection_score < 0.45 else "REJECTION_CONFIRMED"
        local_context = "RANGE_HIGH"
        entry_logic = "SELL_HIGH_AFTER_SUPPLY_REACTION"
        resolved_side = "SELL" if resolved_side == "HOLD" else resolved_side
        confidence = 0.62 + 0.16 * rejection_score
    elif resolved_side == "SELL" and continuation and (global_side in {"SELL", "HOLD"} or local_side == "SELL") and local_range_position >= 0.55:
        primary_play = "BEARISH_PULLBACK_CONTINUATION"
        secondary_play = "SUPPLY_REJECTION" if relative_location == "LOCAL_HIGH" or rejection_score >= 0.40 else "TREND_CONTINUATION"
        play_stage = "REJECTION_CONFIRMED" if rejection_score >= 0.45 else "PULLBACK_FAILING"
        local_context = "PULLBACK_FAILING"
        entry_logic = "SELL_HIGH_AFTER_PULLBACK_FAILURE"
        confidence = 0.66 + 0.08 * float(global_side == "SELL") + 0.10 * rejection_score
    elif resolved_side == "BUY" and continuation and (global_side in {"BUY", "HOLD"} or local_side == "BUY") and local_range_position <= 0.45:
        primary_play = "BULLISH_PULLBACK_CONTINUATION"
        secondary_play = "DEMAND_REJECTION" if relative_location == "LOCAL_LOW" or rejection_score >= 0.40 else "TREND_CONTINUATION"
        play_stage = "REJECTION_CONFIRMED" if rejection_score >= 0.45 else "PULLBACK_HOLDING"
        local_context = "PULLBACK_HOLDING"
        entry_logic = "BUY_LOW_AFTER_PULLBACK_HOLD"
        confidence = 0.66 + 0.08 * float(global_side == "BUY") + 0.10 * rejection_score
    elif reversal_forming and resolved_side == "BUY":
        primary_play = "BULLISH_REVERSAL_FORMING"
        secondary_play = "DEMAND_REJECTION" if relative_location == "LOCAL_LOW" else "NONE"
        play_stage = "REVERSAL_FORMING"
        local_context = "OPPOSING_FORCE_BUILDING"
        entry_logic = "WAIT_FOR_REJECTION"
        confidence = 0.62
    elif reversal_forming and resolved_side == "SELL":
        primary_play = "BEARISH_REVERSAL_FORMING"
        secondary_play = "SUPPLY_REJECTION" if relative_location == "LOCAL_HIGH" else "NONE"
        play_stage = "REVERSAL_FORMING"
        local_context = "OPPOSING_FORCE_BUILDING"
        entry_logic = "WAIT_FOR_REJECTION"
        confidence = 0.62
    elif breakout_confirmed and resolved_side == "BUY":
        primary_play = "BULLISH_IMPULSE"
        secondary_play = "TREND_CONTINUATION"
        play_stage = "IMPULSE_ACTIVE"
        local_context = "BREAKOUT_ACCEPTING"
        entry_logic = "WAIT_FOR_RETEST" if relative_location == "LOCAL_HIGH" else "BUY_WITH_ACCEPTANCE"
        confidence = 0.60 + 0.14 * impulse_length
    elif breakout_confirmed and resolved_side == "SELL":
        primary_play = "BEARISH_IMPULSE"
        secondary_play = "TREND_CONTINUATION"
        play_stage = "IMPULSE_ACTIVE"
        local_context = "BREAKDOWN_ACCEPTING"
        entry_logic = "WAIT_FOR_RETEST" if relative_location == "LOCAL_LOW" else "SELL_WITH_ACCEPTANCE"
        confidence = 0.60 + 0.14 * impulse_length
    elif resolved_side in {"BUY", "SELL"} and global_side == local_side == resolved_side:
        primary_play = "TREND_CONTINUATION"
        secondary_play = "DEMAND_REJECTION" if resolved_side == "BUY" and relative_location == "LOCAL_LOW" else "SUPPLY_REJECTION" if resolved_side == "SELL" and relative_location == "LOCAL_HIGH" else "NONE"
        play_stage = "CONTEXT_ALIGNED"
        local_context = "TREND_ALIGNED"
        entry_logic = "BUY_LOW_SELL_HIGH_CONFIRMATION"
        confidence = 0.60

    side_bias = resolved_side
    if primary_play == "RANGE_LOW_BUY_REACTION":
        side_bias = "BUY"
    elif primary_play == "RANGE_HIGH_SELL_REACTION":
        side_bias = "SELL"
    if primary_play in {"FAKE_BREAKOUT", "FAKE_BREAKDOWN"} and side_bias == "HOLD":
        side_bias = _opposite(global_side)

    global_context = (
        "UPTREND"
        if global_side == "BUY" or regime_primary == "TRENDING_UP"
        else "DOWNTREND"
        if global_side == "SELL" or regime_primary == "TRENDING_DOWN"
        else regime_primary or "UNKNOWN"
    )
    price_location_label = (
        "HIGH_RELATIVE_TO_LOCAL_RANGE"
        if relative_location == "LOCAL_HIGH"
        else "LOW_RELATIVE_TO_LOCAL_RANGE"
        if relative_location == "LOCAL_LOW"
        else "MIDDLE_RELATIVE_TO_LOCAL_RANGE"
    )
    reason = {
        "BULLISH_PULLBACK_CONTINUATION": "Price pulled back into a low/value area, held demand, and BUY continuation remains aligned.",
        "BEARISH_PULLBACK_CONTINUATION": "Price pulled back into a high/value area, failed upward continuation, and SELL control remains aligned.",
        "RANGE_LOW_BUY_REACTION": "Price is reacting from the low side of a range where BUY has better location.",
        "RANGE_HIGH_SELL_REACTION": "Price is reacting from the high side of a range where SELL has better location.",
        "LATE_CHASE_AFTER_IMPULSE": "The move is already expanded and needs a pullback or retest before execution.",
        "FAKE_BREAKOUT": "Breakout evidence exists, but reclaim/retest confirmation is missing.",
        "FAKE_BREAKDOWN": "Breakdown evidence exists, but reclaim/retest confirmation is missing.",
        "LIQUIDITY_SWEEP_REVERSAL": "A liquidity sweep is rejecting and reversal evidence is forming.",
        "MID_RANGE_NO_EDGE": "Price is in the middle of the structure with no clear buy-low/sell-high edge.",
    }.get(primary_play, "The visible evidence is being classified as a structured market play.")

    return {
        "version": MARKET_PLAY_ENGINE_VERSION,
        "market_play": {
            "play_id": _play_id("|".join([str(source.get("session_id", "")), str(source.get("frame_id", "")), primary_play, side_bias])),
            "primary_play": primary_play,
            "secondary_play": secondary_play,
            "side_bias": side_bias,
            "play_stage": play_stage,
            "global_context": global_context,
            "local_context": local_context,
            "price_location": price_location_label,
            "entry_logic": entry_logic,
            "confidence": round(_clip01(confidence), 4),
            "reason": reason,
        },
    }


__all__ = [
    "MARKET_PLAY_CLASSES",
    "MARKET_PLAY_ENGINE_VERSION",
    "analyze_market_play_v3",
]
