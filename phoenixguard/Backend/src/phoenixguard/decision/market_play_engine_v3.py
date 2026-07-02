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
    "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION",
    "FAILED_DEMAND_RECLAIM_SELL_CONTINUATION",
    "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL",
    "FAILED_BUY_INTO_SUPPLY_SELL_REVERSAL",
    "COUNTERTREND_SCALP_ONLY",
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
    role_flip_confirmed = _bool(source.get("role_flip_confirmed") or market_context.get("role_flip_confirmed"))
    zone_liquidity = _mapping(source.get("zone_liquidity") or market_context.get("zone_liquidity"))
    active_zone_type = _upper(
        source.get("active_zone_type")
        or market_context.get("active_zone_type")
        or zone_liquidity.get("zone_type")
        or zone_liquidity.get("type")
    )
    current_location = _upper(market_context.get("current_location") or (price_location or {}).get("relative_location"))
    at_demand_extreme = bool(
        active_zone_type in {"DEMAND", "SUPPORT", "DEMAND_ZONE", "SUPPORT_ZONE"}
        or relative_location == "LOCAL_LOW"
        or current_location in {"DEMAND", "DEMAND_ZONE", "SUPPORT", "SUPPORT_ZONE", "LOCAL_LOW", "RANGE_LOW"}
    )
    at_supply_extreme = bool(
        active_zone_type in {"SUPPLY", "RESISTANCE", "SUPPLY_ZONE", "RESISTANCE_ZONE"}
        or relative_location == "LOCAL_HIGH"
        or current_location in {"SUPPLY", "SUPPLY_ZONE", "RESISTANCE", "RESISTANCE_ZONE", "LOCAL_HIGH", "RANGE_HIGH"}
    )
    reversal_forming = _bool(source.get("reversal_confirmed") or source.get("reversal_forming") or market_context.get("is_reversal_confirmed"))
    countertrend_without_reclaim = bool(
        resolved_side in {"BUY", "SELL"}
        and (
            (global_side in {"BUY", "SELL"} and resolved_side == _opposite(global_side))
            or (local_side in {"BUY", "SELL"} and resolved_side == _opposite(local_side))
        )
        and not (role_flip_confirmed or liquidity_sweep or reversal_forming or breakout_confirmed)
    )

    primary_play = "MID_RANGE_NO_EDGE"
    secondary_play = "NONE"
    play_stage = "STUDYING"
    local_context = "FORMING"
    entry_logic = "WAIT_FOR_CLEAR_PLAY"
    confidence = 0.42
    bearish_pullback_continuation = bool(
        resolved_side == "SELL"
        and continuation
        and (global_side in {"SELL", "HOLD"} or local_side == "SELL")
        and local_range_position >= 0.55
    )
    bullish_pullback_continuation = bool(
        resolved_side == "BUY"
        and continuation
        and (global_side in {"BUY", "HOLD"} or local_side == "BUY")
        and local_range_position <= 0.45
    )

    if (
        resolved_side == "BUY"
        and at_demand_extreme
        and not bullish_pullback_continuation
        and (rejection_score >= 0.38 or liquidity_sweep or reversal_forming)
    ):
        primary_play = "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL" if countertrend_without_reclaim else "DEMAND_REJECTION"
        secondary_play = "LIQUIDITY_SWEEP_REVERSAL" if liquidity_sweep else "BULLISH_REVERSAL_FORMING"
        play_stage = "AGGRESSIVE_REVERSAL_ARMED" if rejection_score >= 0.45 else "REACTION_FORMING"
        local_context = "SELL_CONTINUATION_FAILING_AT_DEMAND" if countertrend_without_reclaim else "DEMAND_REJECTING"
        entry_logic = "AGGRESSIVE_BUY_ON_DEMAND_REJECTION_OR_CONSERVATIVE_RETEST"
        confidence = 0.66 + 0.16 * rejection_score + 0.06 * float(liquidity_sweep or reversal_forming)
    elif (
        resolved_side == "SELL"
        and at_supply_extreme
        and not bearish_pullback_continuation
        and (rejection_score >= 0.38 or liquidity_sweep or reversal_forming)
    ):
        primary_play = "FAILED_BUY_INTO_SUPPLY_SELL_REVERSAL" if countertrend_without_reclaim else "SUPPLY_REJECTION"
        secondary_play = "LIQUIDITY_SWEEP_REVERSAL" if liquidity_sweep else "BEARISH_REVERSAL_FORMING"
        play_stage = "AGGRESSIVE_REVERSAL_ARMED" if rejection_score >= 0.45 else "REACTION_FORMING"
        local_context = "BUY_CONTINUATION_FAILING_AT_SUPPLY" if countertrend_without_reclaim else "SUPPLY_REJECTING"
        entry_logic = "AGGRESSIVE_SELL_ON_SUPPLY_REJECTION_OR_CONSERVATIVE_RETEST"
        confidence = 0.66 + 0.16 * rejection_score + 0.06 * float(liquidity_sweep or reversal_forming)
    elif countertrend_without_reclaim:
        primary_play = "COUNTERTREND_SCALP_ONLY"
        secondary_play = "TREND_EXHAUSTION" if impulse_length >= 0.62 else "NONE"
        play_stage = "MINOR_COUNTERTREND_REACTION"
        local_context = "INNER_REACTION_AGAINST_VISIBLE_BIAS"
        entry_logic = "WAIT_FOR_RECLAIM_ROLE_FLIP_OR_BIAS_ALIGNMENT"
        confidence = 0.68
    elif resolved_side == "BUY" and role_flip_confirmed and active_zone_type in {"SUPPLY", "RESISTANCE", "SUPPLY_ZONE"}:
        primary_play = "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION"
        secondary_play = "TREND_CONTINUATION"
        play_stage = "ROLE_FLIP_RECLAIM_CONFIRMED"
        local_context = "FAILED_SUPPLY_ACCEPTING_BUY"
        entry_logic = "BUY_AFTER_SUPPLY_RECLAIM_RETEST_HOLD"
        confidence = 0.74 + 0.08 * float(global_side in {"BUY", "HOLD"})
    elif resolved_side == "SELL" and role_flip_confirmed and active_zone_type in {"DEMAND", "SUPPORT", "DEMAND_ZONE"}:
        primary_play = "FAILED_DEMAND_RECLAIM_SELL_CONTINUATION"
        secondary_play = "TREND_CONTINUATION"
        play_stage = "ROLE_FLIP_RECLAIM_CONFIRMED"
        local_context = "FAILED_DEMAND_ACCEPTING_SELL"
        entry_logic = "SELL_AFTER_DEMAND_RECLAIM_RETEST_HOLD"
        confidence = 0.74 + 0.08 * float(global_side in {"SELL", "HOLD"})
    elif false_breakout and resolved_side == "BUY":
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
    elif bearish_pullback_continuation:
        primary_play = "BEARISH_PULLBACK_CONTINUATION"
        secondary_play = "SUPPLY_REJECTION" if relative_location == "LOCAL_HIGH" or rejection_score >= 0.40 else "TREND_CONTINUATION"
        play_stage = "REJECTION_CONFIRMED" if rejection_score >= 0.45 else "PULLBACK_FAILING"
        local_context = "PULLBACK_FAILING"
        entry_logic = "SELL_HIGH_AFTER_PULLBACK_FAILURE"
        confidence = 0.66 + 0.08 * float(global_side == "SELL") + 0.10 * rejection_score
    elif bullish_pullback_continuation:
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
        "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION": "Former supply has failed as resistance and is accepting BUY continuation after reclaim/role flip.",
        "FAILED_DEMAND_RECLAIM_SELL_CONTINUATION": "Former demand has failed as support and is accepting SELL continuation after reclaim/role flip.",
        "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL": "SELL continuation is failing at demand; aggressive BUY reaction is armed before conservative reclaim.",
        "FAILED_BUY_INTO_SUPPLY_SELL_REVERSAL": "BUY continuation is failing at supply; aggressive SELL reaction is armed before conservative reclaim.",
        "COUNTERTREND_SCALP_ONLY": "The reaction is against visible bias without reclaim or role-flip proof, so it is minor/watch-only.",
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
