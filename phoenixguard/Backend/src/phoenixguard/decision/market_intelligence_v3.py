from __future__ import annotations

from math import atan2, degrees, isfinite
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence, cast

from phoenixguard.decision.market_play_engine_v3 import analyze_market_play_v3
from phoenixguard.decision.market_reality_engine import analyze_market_reality
from phoenixguard.decision.pair_behavior_profile_v3 import analyze_pair_behavior_profile_v3
from phoenixguard.decision.price_location_engine_v3 import analyze_price_location_v3
from phoenixguard.decision.reasoning_arbitrator_v3 import (
    analyze_reasoning_arbitration_v3,
    build_model_role_votes_v3,
)
from phoenixguard.decision.regime_engine_v3 import analyze_regime_v3
from phoenixguard.memory.visual_play_memory_bank import analyze_visual_play_memory_confirmation


BAD_ENTRY_CLASS_001 = "LATE_CHASE_STEEP_IMPULSE"
PG_MARKET_INTELLIGENCE_VERSION = "PG_MARKET_INTELLIGENCE_V3"
MARKET_CLASSIFIERS_VERSION = "PG_MARKET_CLASSIFIERS_V1"
MARKET_CLASSIFIER_NAMES = (
    "late_chase_after_impulse",
    "near_opposing_force",
    "middle_safe",
    "middle_danger",
    "angle_break_risk",
    "history_would_exit_here",
    "false_breakout_risk",
    "pullback_not_confirmed",
    "dominance_weakening",
    "conflict_market",
)
CLASSIFIER_BLOCK_REASONS = {
    "late_chase_after_impulse": BAD_ENTRY_CLASS_001,
    "near_opposing_force": "OPPOSING_FORCE_TOO_CLOSE",
    "angle_break_risk": "ANGLE_BREAK_RISK",
    "history_would_exit_here": "HISTORY_WOULD_EXIT_HERE",
    "false_breakout_risk": "FALSE_BREAKOUT_RISK",
    "pullback_not_confirmed": "PULLBACK_NOT_CONFIRMED",
    "dominance_weakening": "DOMINANCE_WEAKENING",
    "conflict_market": "CONFLICT_MARKET",
}
CLASSIFIER_BLOCK_PRIORITY = (
    "conflict_market",
    "late_chase_after_impulse",
    "history_would_exit_here",
    "near_opposing_force",
    "false_breakout_risk",
    "pullback_not_confirmed",
    "dominance_weakening",
    "angle_break_risk",
)
ANGLE_CLASSES = {
    "FLAT_NOISE",
    "WEAK_DRIFT",
    "HEALTHY_TREND",
    "STRONG_BUT_SUSTAINABLE",
    "STEEP_IMPULSE",
    "PARABOLIC_RISK",
    "VERTICAL_EXHAUSTION",
    "BROKEN_ANGLE",
}


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
    if not isfinite(parsed):
        return float(default)
    return float(parsed)


def _clip01(value: Any, default: float = 0.0) -> float:
    parsed = _float(value, default)
    return max(0.0, min(1.0, parsed))


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _upper_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _contains_token(value: Any, tokens: Sequence[str]) -> bool:
    text = _upper_text(value)
    return any(token.upper() in text for token in tokens)


def _nested_mapping(snapshot: Mapping[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        candidate = _mapping(snapshot.get(name))
        if candidate:
            return candidate
    return {}


def _candle_value(row: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in row:
            return _float(row.get(name), default)
    return float(default)


def _price_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "tracked_candles", "candle_map", "ohlc"):
        rows = _rows(snapshot.get(key))
        if rows:
            return rows
    tracking = _mapping(snapshot.get("tracking_summary"))
    for key in ("candles", "tracked_candles", "candle_map", "ohlc"):
        rows = _rows(tracking.get(key))
        if rows:
            return rows
    return []


def _price_range(candles: Sequence[Mapping[str, Any]]) -> float:
    values: list[float] = []
    for row in candles:
        for key in ("open", "o", "high", "h", "low", "l", "close", "c", "price_proxy"):
            if key in row:
                values.append(_float(row.get(key)))
    if not values:
        return 1.0
    return max(1e-9, max(values) - min(values))


def _close_values(candles: Sequence[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in candles:
        values.append(_candle_value(row, "close", "c", "price_proxy", default=_candle_value(row, "y", default=0.0)))
    return values


def adaptive_angle_threshold(symbol: Any, timeframe: Any, snapshot: Mapping[str, Any] | None = None) -> float:
    snapshot = snapshot or {}
    thresholds = _mapping(snapshot.get("angle_thresholds"))
    key = f"{str(symbol or '*').upper()}|{str(timeframe or '*').upper()}"
    for candidate in (key, f"{str(symbol or '*').upper()}|*", f"*|{str(timeframe or '*').upper()}", "*|*"):
        if candidate in thresholds:
            return max(0.5, _float(thresholds[candidate], 1.35))
    tf = str(timeframe or "").upper()
    base_by_timeframe = {
        "S5": 1.00,
        "S15": 1.05,
        "S30": 1.10,
        "M1": 1.16,
        "1M": 1.16,
        "M2": 1.22,
        "M3": 1.28,
        "M5": 1.35,
        "5M": 1.35,
        "M10": 1.44,
        "M15": 1.52,
        "15M": 1.52,
        "M30": 1.62,
        "H1": 1.72,
    }
    threshold = base_by_timeframe.get(tf, 1.35)
    if "OTC" in str(symbol or "").upper():
        threshold -= 0.05
    percentile = _float(snapshot.get("steepness_z_p90", snapshot.get("zscore_p90", 0.0)), 0.0)
    if percentile > 0.0:
        threshold = 0.55 * threshold + 0.45 * max(1.0, percentile)
    return round(max(0.85, min(2.25, threshold)), 4)


def angle_dynamics_agent(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    supplied = _mapping(snapshot.get("angle_features") or snapshot.get("angle_context") or snapshot.get("angle_dynamics"))
    candles = _price_rows(snapshot)
    closes = _close_values(candles)
    price_range = _price_range(candles)
    if supplied:
        screen_angle = _float(
            supplied.get(
                "screen_space_angle",
                supplied.get("active_trend_angle_degrees", supplied.get("multi_candle_regression_angle", 0.0)),
            ),
            0.0,
        )
        steepness_z = _float(supplied.get("steepness_z_score"), 0.0)
        impulse_length = _float(supplied.get("impulse_length"), 0.0)
    elif len(closes) >= 2:
        delta = closes[-1] - closes[0]
        screen_angle = degrees(atan2(delta / price_range, max(1, len(closes) - 1) / max(1, len(closes))))
        candle_changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
        avg_change = mean(candle_changes) if candle_changes else 0.0
        change_std = pstdev(candle_changes) if len(candle_changes) >= 2 else max(abs(avg_change), 1e-9)
        latest_change = candle_changes[-1] if candle_changes else 0.0
        steepness_z = abs((latest_change - avg_change) / max(change_std, 1e-9))
        impulse_length = abs(delta) / price_range
    else:
        screen_angle = _float(supplied.get("active_trend_angle_degrees"), 0.0)
        steepness_z = _float(supplied.get("steepness_z_score"), 0.0)
        impulse_length = _float(supplied.get("impulse_length"), 0.0)

    symbol = snapshot.get("symbol", snapshot.get("market", ""))
    timeframe = snapshot.get("timeframe", snapshot.get("focus_timeframe", ""))
    threshold = adaptive_angle_threshold(symbol, timeframe, snapshot)
    provided_class = str(supplied.get("angle_class", "") or "").strip().upper()
    if provided_class in ANGLE_CLASSES:
        angle_class = provided_class
    elif steepness_z >= threshold * 2.0 or abs(screen_angle) >= 72.0:
        angle_class = "VERTICAL_EXHAUSTION"
    elif steepness_z >= threshold * 1.45 or abs(screen_angle) >= 58.0:
        angle_class = "PARABOLIC_RISK"
    elif steepness_z >= threshold or abs(screen_angle) >= 44.0 or impulse_length >= 0.72:
        angle_class = "STEEP_IMPULSE"
    elif abs(screen_angle) >= 28.0:
        angle_class = "STRONG_BUT_SUSTAINABLE"
    elif abs(screen_angle) >= 12.0:
        angle_class = "HEALTHY_TREND"
    elif abs(screen_angle) >= 4.0:
        angle_class = "WEAK_DRIFT"
    else:
        angle_class = "FLAT_NOISE"

    pullback_depth = _float(supplied.get("pullback_depth"), _float(snapshot.get("pullback_depth"), 0.0))
    pullback_confirmed = _bool(snapshot.get("pullback_confirmed") or snapshot.get("retest_confirmed")) or pullback_depth >= 0.22
    if "late_chase_risk" in supplied:
        late_chase_risk = _bool(supplied.get("late_chase_risk"))
    else:
        late_chase_risk = bool(
            angle_class in {"STEEP_IMPULSE", "PARABOLIC_RISK", "VERTICAL_EXHAUSTION"}
            and not pullback_confirmed
            and (impulse_length >= 0.62 or steepness_z >= threshold or abs(screen_angle) >= 54.0)
        )
    post_impulse_wait_required = _bool(supplied.get("post_impulse_wait_required")) or (
        late_chase_risk and not pullback_confirmed
    )
    angle_break_probability = _clip01(
        supplied.get("angle_break_probability"),
        0.18 + 0.18 * float(angle_class in {"STEEP_IMPULSE", "PARABOLIC_RISK"}) + 0.22 * float(angle_class == "VERTICAL_EXHAUSTION"),
    )
    return {
        "agent": "angle_dynamics",
        "screen_space_angle": screen_angle,
        "price_normalised_angle": _float(supplied.get("price_normalised_angle"), screen_angle),
        "time_normalised_angle": _float(supplied.get("time_normalised_angle"), screen_angle / max(1, len(closes) or 1)),
        "volatility_normalised_angle": _float(supplied.get("volatility_normalised_angle"), steepness_z),
        "multi_candle_regression_angle": _float(supplied.get("multi_candle_regression_angle"), screen_angle),
        "swing_leg_angle": _float(supplied.get("swing_leg_angle"), screen_angle),
        "candle_body_angle": _float(supplied.get("candle_body_angle"), screen_angle),
        "acceleration": _float(supplied.get("acceleration"), steepness_z / max(1.0, threshold)),
        "curvature": _float(supplied.get("curvature"), 0.0),
        "impulse_length": _float(supplied.get("impulse_length"), impulse_length),
        "pullback_depth": pullback_depth,
        "wick_rejection_score": _clip01(supplied.get("wick_rejection_score"), 0.0),
        "body_to_wick_ratio": _float(supplied.get("body_to_wick_ratio"), 0.0),
        "angle_persistence": _clip01(supplied.get("angle_persistence"), 0.0),
        "angle_decay": _clip01(supplied.get("angle_decay"), 0.0),
        "angle_class": angle_class,
        "steepness_z_score": steepness_z,
        "adaptive_threshold": threshold,
        "parabolic_risk": _bool(supplied.get("parabolic_risk")) or angle_class in {"PARABOLIC_RISK", "VERTICAL_EXHAUSTION"},
        "late_chase_risk": late_chase_risk,
        "angle_break_probability": angle_break_probability,
        "post_impulse_wait_required": post_impulse_wait_required,
        "executable_vote": not post_impulse_wait_required and not late_chase_risk,
        "reason": (
            "Move is already vertically expanded; wait for pullback/retest into valid trigger zone."
            if post_impulse_wait_required
            else "Angle is acceptable for continued council study."
        ),
    }


def zone_liquidity_agent(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    supplied_zone = _mapping(snapshot.get("zone_liquidity"))
    zones = _rows(snapshot.get("zones") or snapshot.get("support_resistance_zones") or supplied_zone.get("zones"))
    market_context = _mapping(snapshot.get("market_context"))
    side = _side(snapshot.get("candidate_side") or snapshot.get("side") or supplied_zone.get("side") or market_context.get("dominant_side"))
    current_location = str(
        snapshot.get("current_location")
        or market_context.get("current_location")
        or supplied_zone.get("current_location")
        or supplied_zone.get("zone_type")
        or "MIDDLE_DANGER"
    ).strip().upper()
    current_location = current_location.replace(" ", "_").replace("-", "_")
    inside_valid = _bool(
        snapshot.get("inside_valid_trigger_zone")
        or market_context.get("inside_valid_trigger_zone")
        or supplied_zone.get("inside_valid_trigger_zone")
    )
    sniper_zone_id = str(snapshot.get("sniper_zone_id") or market_context.get("sniper_zone_id") or supplied_zone.get("sniper_zone_id") or "")
    conservative_trigger_zone_id = str(
        snapshot.get("conservative_trigger_zone_id")
        or market_context.get("conservative_trigger_zone_id")
        or supplied_zone.get("conservative_trigger_zone_id")
        or ""
    )
    valid_types: set[str] = (
        {"DEMAND", "DEMAND_ZONE", "SNIPER_BUY", "SNIPER_BUY_ZONE", "CONSERVATIVE_BUY_TRIGGER", "CONSERVATIVE_BUY_TRIGGER_ZONE"}
        if side == "BUY"
        else {"SUPPLY", "SUPPLY_ZONE", "SNIPER_SELL", "SNIPER_SELL_ZONE", "CONSERVATIVE_SELL_TRIGGER", "CONSERVATIVE_SELL_TRIGGER_ZONE"}
        if side == "SELL"
        else set()
    )
    for zone in zones:
        zone_type = str(zone.get("zone_type") or zone.get("type") or zone.get("kind") or "").strip().upper()
        zone_type = zone_type.replace(" ", "_").replace("-", "_")
        if _bool(zone.get("broken") or zone.get("is_broken")):
            continue
        if zone_type in valid_types and _bool(zone.get("current_price_inside") or zone.get("inside")):
            inside_valid = True
            current_location = zone_type
            zone_id = str(zone.get("zone_id") or zone.get("id") or "")
            if "SNIPER" in zone_type:
                sniper_zone_id = zone_id
            if "CONSERVATIVE" in zone_type:
                conservative_trigger_zone_id = zone_id
            break
    if not inside_valid:
        inside_valid = current_location in {
            "SNIPER_BUY_ZONE",
            "SNIPER_SELL_ZONE",
            "CONSERVATIVE_BUY_TRIGGER_ZONE",
            "CONSERVATIVE_SELL_TRIGGER_ZONE",
            "DEMAND_ZONE",
            "SUPPLY_ZONE",
        }
    return {
        "agent": "zone_liquidity",
        "zones": zones,
        "current_location": current_location,
        "inside_valid_trigger_zone": inside_valid,
        "sniper_zone_id": sniper_zone_id,
        "conservative_trigger_zone_id": conservative_trigger_zone_id,
        "liquidity_sweep_detected": _bool(snapshot.get("liquidity_sweep_detected")),
        "reason": f"Current location classified as {current_location}.",
    }


def historical_pattern_agent(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    supplied = _mapping(snapshot.get("history_context") or snapshot.get("historical_pattern"))
    best_matches = _rows(supplied.get("best_matches") or snapshot.get("history_matches"))
    similarity_state = str(
        supplied.get("similarity_state")
        or snapshot.get("similarity_state")
        or "UNKNOWN"
    ).strip().upper()
    would_exit = _bool(
        supplied.get("would_have_exited_here")
        or supplied.get("history_would_exit_here")
        or snapshot.get("history_would_exit_here")
    )
    entry_quality = str(supplied.get("historical_entry_quality") or "UNKNOWN").strip().upper()
    late_entry_risk = str(supplied.get("historical_late_entry_risk") or "").strip().upper()
    losing_similarity = _clip01(supplied.get("similarity_to_losing_setups"), 0.0)
    winning_similarity = _clip01(supplied.get("similarity_to_winning_setups"), 0.0)
    for match in best_matches:
        outcome = str(match.get("outcome") or match.get("best_match_outcome") or "").strip().upper()
        setup = str(match.get("setup") or "").strip().upper()
        quality = str(match.get("entry_quality") or "").strip().upper()
        if outcome == "LOSS" or "LOSS" in setup or quality == "LATE":
            losing_similarity = max(losing_similarity, _clip01(match.get("similarity"), 0.82))
        if str(match.get("would_exit_at") or "").strip().lower() in {"current", "current_area", "near_current_area", "here"}:
            would_exit = True
    resembles_loss = (
        "LOSS" in similarity_state
        or "LATE" in similarity_state
        or would_exit
        or entry_quality == "LATE"
        or late_entry_risk in {"HIGH", "ELEVATED"}
        or losing_similarity > winning_similarity + 0.12
    )
    return {
        "agent": "historical_pattern",
        "similarity_state": similarity_state,
        "best_match_setup": str(supplied.get("best_match_setup") or ""),
        "best_match_outcome": str(supplied.get("best_match_outcome") or ""),
        "would_have_entered_here": _bool(supplied.get("would_have_entered_here")),
        "would_have_exited_here": would_exit,
        "historical_entry_quality": entry_quality,
        "historical_late_entry_risk": late_entry_risk or ("HIGH" if resembles_loss else "LOW"),
        "similarity_to_winning_setups": winning_similarity,
        "similarity_to_losing_setups": losing_similarity,
        "best_matches": best_matches,
        "executable_vote": not resembles_loss,
        "reason": (
            "Historical analogs suggest current area is closer to exit/protection than entry."
            if resembles_loss
            else "Historical analogs do not classify the current area as a late bad entry."
        ),
    }


def risk_opposing_force_agent(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    supplied = _mapping(snapshot.get("risk_context") or snapshot.get("risk_opposing_force"))
    market_context = _mapping(snapshot.get("market_context"))
    resolved_side = _side(side or supplied.get("side") or market_context.get("dominant_side"))
    distance = _float(
        supplied.get("distance_to_opposing_force", market_context.get("distance_to_opposing_force", snapshot.get("opposing_force_distance", 1.0))),
        1.0,
    )
    minimum = _float(supplied.get("minimum_required_distance", snapshot.get("minimum_required_distance", 0.22)), 0.22)
    explicit_ok = supplied.get("distance_ok", market_context.get("opposing_force_distance_ok"))
    distance_ok = _bool(explicit_ok) if explicit_ok is not None else distance >= minimum
    return {
        "agent": "risk_opposing_force",
        "side": resolved_side,
        "nearest_opposing_force": str(supplied.get("nearest_opposing_force") or market_context.get("nearest_opposing_force") or ""),
        "distance_to_opposing_force": distance,
        "minimum_required_distance": minimum,
        "distance_ok": distance_ok,
        "risk_state": "ACCEPTABLE" if distance_ok else "OPPOSING_FORCE_CLOSE",
        "reason": (
            f"{resolved_side} has enough distance before nearest opposing force."
            if distance_ok
            else f"{resolved_side} is too close to opposing force."
        ),
    }


def global_structure_agent(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    supplied = _mapping(snapshot.get("global_structure"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    side = _side(
        snapshot.get("global_side")
        or supplied.get("global_side")
        or supplied.get("major_swing_direction")
        or tracking.get("global_direction")
        or tracking.get("major_trend_side")
    )
    return {
        "agent": "global_structure",
        "global_side": side,
        "global_state": str(snapshot.get("global_state") or supplied.get("global_state") or tracking.get("global_state") or "UNKNOWN").upper(),
        "major_swing_direction": side,
        "major_swing_strength": _clip01(
            snapshot.get("major_swing_strength"),
            _clip01(supplied.get("major_swing_strength"), _clip01(tracking.get("major_trend_confidence"), 0.0)),
        ),
        "global_confidence": _clip01(
            snapshot.get("global_confidence"),
            _clip01(supplied.get("global_confidence"), _clip01(tracking.get("confidence"), 0.0)),
        ),
        "reason": f"Global structure reads {side}.",
    }


def local_micro_structure_agent(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    supplied = _mapping(snapshot.get("local_micro_structure"))
    tracking = _mapping(snapshot.get("tracking_summary"))
    side = _side(snapshot.get("local_side") or supplied.get("local_side") or tracking.get("local_direction") or snapshot.get("candidate_side"))
    return {
        "agent": "local_micro_structure",
        "local_side": side,
        "local_state": str(snapshot.get("local_state") or supplied.get("local_state") or tracking.get("local_state") or "UNKNOWN").upper(),
        "momentum_state": str(snapshot.get("momentum_state") or supplied.get("momentum_state") or tracking.get("momentum_state") or "UNKNOWN").upper(),
        "confidence": _clip01(
            snapshot.get("local_confidence"),
            _clip01(supplied.get("confidence"), _clip01(tracking.get("local_confidence"), 0.0)),
        ),
        "reason": f"Local structure reads {side}.",
    }


def classify_middle_safe(snapshot: Mapping[str, Any], side: str) -> dict[str, Any]:
    zone = zone_liquidity_agent(snapshot)
    risk = risk_opposing_force_agent(snapshot, side)
    angle = angle_dynamics_agent(snapshot)
    continuation = _bool(snapshot.get("continuation_confirmed") or snapshot.get("pullback_confirmed") or snapshot.get("retest_confirmed"))
    location = str(zone.get("current_location", "")).upper()
    middle = location in {"MIDDLE", "MID", "MIDDLE_SAFE"}
    safe = bool(
        middle
        and risk["distance_ok"]
        and not angle["late_chase_risk"]
        and continuation
    )
    return {
        "current_location": "MIDDLE_SAFE" if safe else zone["current_location"],
        "middle_safe": safe,
        "opposing_force_distance_ok": bool(risk["distance_ok"]),
        "late_chase_risk": bool(angle["late_chase_risk"]),
        "continuation_or_pullback_confirmed": continuation,
    }


def _classifier_detail(*, detected: bool, block: bool, score: float, reason: str) -> dict[str, Any]:
    return {
        "detected": bool(detected),
        "block": bool(block),
        "score": round(_clip01(score), 4),
        "reason": reason,
    }


def _score_pair(snapshot: Mapping[str, Any]) -> tuple[float, float]:
    probabilities = _mapping(snapshot.get("probabilities"))
    buy_score = _clip01(snapshot.get("buy_score"), _clip01(probabilities.get("BUY", probabilities.get("buy")), 0.0))
    sell_score = _clip01(snapshot.get("sell_score"), _clip01(probabilities.get("SELL", probabilities.get("sell")), 0.0))
    return buy_score, sell_score


def _dominance_value(snapshot: Mapping[str, Any]) -> float:
    market_context = _mapping(snapshot.get("market_context"))
    for key in (
        "dominance_score",
        "dominance_margin",
        "projection_dominance",
        "dominance_gap",
        "side_dominance",
    ):
        if key in snapshot:
            return _clip01(snapshot.get(key), 0.0)
        if key in market_context:
            return _clip01(market_context.get(key), 0.0)
    projected = _mapping(snapshot.get("projected_next_box"))
    for key in ("dominance", "dominance_gap"):
        if key in projected:
            return _clip01(projected.get(key), 0.0)
    return 0.0


def _previous_dominance_value(snapshot: Mapping[str, Any]) -> float:
    for key in ("previous_dominance_score", "previous_dominance_margin", "prior_dominance_score", "prior_dominance_margin"):
        if key in snapshot:
            return _clip01(snapshot.get(key), 0.0)
    previous = _mapping(snapshot.get("previous_market_context") or snapshot.get("previous_state"))
    for key in ("dominance_score", "dominance_margin", "projection_dominance", "dominance_gap"):
        if key in previous:
            return _clip01(previous.get(key), 0.0)
    return 0.0


def classify_market_conditions(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    resolved_side = _side(side) if _side(side) in {"BUY", "SELL"} else _candidate_side_v3(snapshot)
    angle = angle_dynamics_agent(snapshot)
    history = historical_pattern_agent(snapshot)
    risk = risk_opposing_force_agent(snapshot, resolved_side)
    zone = zone_liquidity_agent(snapshot)
    global_agent = global_structure_agent(snapshot)
    local_agent = local_micro_structure_agent(snapshot)
    middle = classify_middle_safe(snapshot, resolved_side)
    market_context = _mapping(snapshot.get("market_context"))
    breakout = _nested_mapping(snapshot, "breakout_context", "false_breakout_context", "liquidity_context")
    current_location = str(middle["current_location"] if middle["middle_safe"] else zone["current_location"]).upper()
    pullback_confirmed = _bool(
        snapshot.get("pullback_confirmed")
        or snapshot.get("retest_confirmed")
        or snapshot.get("continuation_confirmed")
        or market_context.get("is_continuation_confirmed")
    )
    continuation_probability = _clip01(snapshot.get("continuation_probability"), _clip01(market_context.get("continuation_probability"), 0.0))
    if continuation_probability >= 0.56:
        pullback_confirmed = True

    late_chase = bool(
        angle["late_chase_risk"]
        or (
            angle["angle_class"] in {"STEEP_IMPULSE", "PARABOLIC_RISK", "VERTICAL_EXHAUSTION"}
            and not pullback_confirmed
            and (_float(angle.get("impulse_length"), 0.0) >= 0.62 or _float(angle.get("steepness_z_score"), 0.0) >= _float(angle.get("adaptive_threshold"), 1.35))
        )
    )
    near_opposing = not bool(risk["distance_ok"])
    middle_safe = bool(middle["middle_safe"])
    middle_danger = bool(
        not middle_safe
        and current_location in {"MIDDLE", "MID", "MIDDLE_DANGER", "UNKNOWN", ""}
        and not bool(zone["inside_valid_trigger_zone"])
    )
    source_dominant_side = _side(market_context.get("dominant_side"))
    source_global_side = _side(market_context.get("global_side"))
    source_local_side = _side(market_context.get("local_side"))
    source_structural_side = (
        source_global_side
        if source_global_side in {"BUY", "SELL"} and source_global_side == source_local_side
        else "HOLD"
    )
    stale_dominant_overridden = bool(
        resolved_side in {"BUY", "SELL"}
        and source_structural_side == resolved_side
        and source_dominant_side in {"BUY", "SELL"}
        and source_dominant_side != resolved_side
        and (
            _bool(market_context.get("is_late_chase"))
            or _bool(market_context.get("is_steep_angle_break_risk"))
            or _bool(market_context.get("pullback_not_confirmed"))
            or str(market_context.get("entry_quality_state") or "").strip().upper() in {"BAD_NOW", "LATE_ENTRY", "CHASE_ENTRY"}
        )
    )
    angle_break_risk = bool(
        _bool(
            snapshot.get("angle_break_risk")
            or snapshot.get("angle_broke")
            or (market_context.get("is_steep_angle_break_risk") if not stale_dominant_overridden else False)
        )
        or angle["angle_class"] in {"BROKEN_ANGLE"}
        or _clip01(angle.get("angle_break_probability"), 0.0) >= 0.58
        or (bool(angle["parabolic_risk"]) and not pullback_confirmed)
    )
    history_would_exit = bool(not history["executable_vote"] or history["would_have_exited_here"])

    sweep_detected = _bool(
        snapshot.get("liquidity_sweep_detected")
        or zone.get("liquidity_sweep_detected")
        or breakout.get("liquidity_sweep_detected")
    )
    breakout_confirmed = _bool(snapshot.get("breakout_confirmed") or breakout.get("breakout_confirmed"))
    breakout_failed = _bool(
        snapshot.get("false_breakout_risk")
        or snapshot.get("failed_breakout")
        or breakout.get("false_breakout_risk")
        or breakout.get("failed_breakout")
        or breakout.get("breakout_failed")
    )
    breakout_reclaimed = _bool(
        snapshot.get("breakout_reclaimed")
        or snapshot.get("reclaimed_breakout_level")
        or snapshot.get("retest_confirmed")
        or breakout.get("reclaimed_breakout_level")
        or breakout.get("breakout_reclaimed")
    )
    false_breakout_score = max(
        _clip01(snapshot.get("false_breakout_probability"), 0.0),
        _clip01(breakout.get("false_breakout_probability"), 0.0),
        _clip01(angle.get("wick_rejection_score"), 0.0),
    )
    false_breakout_risk = bool(
        breakout_failed
        or (sweep_detected and not breakout_reclaimed)
        or (breakout_confirmed and not breakout_reclaimed and false_breakout_score >= 0.55)
    )

    projected_box = _mapping(snapshot.get("projected_next_box"))
    local_state = str(snapshot.get("local_state") or _mapping(snapshot.get("local_micro_structure")).get("local_state") or "").upper()
    setup_text = " ".join(
        str(value or "")
        for value in (
            snapshot.get("setup_type"),
            snapshot.get("structure_setup"),
            snapshot.get("entry_setup"),
            local_state,
            projected_box.get("box_type"),
        )
    )
    requires_pullback = bool(
        _bool(snapshot.get("requires_pullback_confirmation"))
        or _contains_token(setup_text, ("PULLBACK", "RETEST", "RELOAD"))
        or bool(angle["post_impulse_wait_required"])
    )
    pullback_not_confirmed = bool(requires_pullback and not pullback_confirmed)

    dominance_state = str(snapshot.get("dominance_state") or market_context.get("dominance_state") or "").upper()
    v3_execution_candidate = _mapping(snapshot.get("v3_execution_candidate"))
    council_timing_entry_accepted = bool(
        v3_execution_candidate.get("active")
        and _side(v3_execution_candidate.get("side")) == resolved_side
        and _bool(v3_execution_candidate.get("entry_allowed"))
    )
    previous_dominance = _previous_dominance_value(snapshot)
    current_dominance = _dominance_value(snapshot)
    dominance_drop = previous_dominance > 0.0 and current_dominance < max(0.0, previous_dominance - _float(snapshot.get("dominance_drop_threshold"), 0.12))
    global_side = _side(global_agent.get("global_side"))
    local_side = _side(local_agent.get("local_side"))
    opposing_pressure = _clip01(snapshot.get("opposing_pressure"), _clip01(market_context.get("opposing_pressure"), 0.0))
    dominant_pressure = _clip01(snapshot.get("dominant_pressure"), _clip01(market_context.get("dominant_pressure"), 0.0))
    dominance_weakening = bool(
        not council_timing_entry_accepted
        and (
            _bool(snapshot.get("dominance_weakening") or market_context.get("dominance_weakening"))
            or _contains_token(dominance_state, ("WEAKEN", "FADING", "FADE", "ROLLING", "DECAY", "EXHAUST"))
            or dominance_drop
            or (opposing_pressure > 0.0 and opposing_pressure >= dominant_pressure + 0.10)
            or (
                resolved_side in {"BUY", "SELL"}
                and global_side in {"BUY", "SELL"}
                and local_side in {"BUY", "SELL"}
                and global_side != local_side
                and _clip01(global_agent.get("global_confidence"), 0.0) >= 0.54
                and _clip01(local_agent.get("confidence"), 0.0) >= 0.54
            )
        )
    )

    buy_score, sell_score = _score_pair(snapshot)
    recent_sides = [str(item or "").upper() for item in snapshot.get("recent_sides", [])] if isinstance(snapshot.get("recent_sides"), Sequence) else []
    recent_sides = [item for item in recent_sides if item in {"BUY", "SELL"}]
    conflict_market = bool(
        not council_timing_entry_accepted
        and (
            _bool(snapshot.get("conflict_market") or market_context.get("conflict_market"))
            or _clip01(snapshot.get("conflict_score"), 0.0) >= 0.55
            or (buy_score >= 0.52 and sell_score >= 0.52 and abs(buy_score - sell_score) <= 0.16)
            or (len(recent_sides) >= 3 and len(set(recent_sides[-3:])) > 1)
            or (
                global_side in {"BUY", "SELL"}
                and local_side in {"BUY", "SELL"}
                and global_side != local_side
                and _clip01(global_agent.get("global_confidence"), 0.0) >= 0.60
                and _clip01(local_agent.get("confidence"), 0.0) >= 0.60
            )
        )
    )

    details = {
        "late_chase_after_impulse": _classifier_detail(
            detected=late_chase,
            block=late_chase,
            score=max(_clip01(angle.get("angle_break_probability"), 0.0), _clip01(angle.get("impulse_length"), 0.0)),
            reason="Steep impulse has not pulled back into a valid re-entry zone." if late_chase else "No late-chase impulse trap detected.",
        ),
        "near_opposing_force": _classifier_detail(
            detected=near_opposing,
            block=near_opposing,
            score=1.0 - _clip01(_float(risk.get("distance_to_opposing_force"), 1.0) / max(_float(risk.get("minimum_required_distance"), 0.22), 1e-9), 0.0),
            reason=str(risk["reason"]),
        ),
        "middle_safe": _classifier_detail(
            detected=middle_safe,
            block=False,
            score=1.0 if middle_safe else 0.0,
            reason="Middle location is acceptable because trend, distance, and continuation agree." if middle_safe else "Middle-safe conditions are not complete.",
        ),
        "middle_danger": _classifier_detail(
            detected=middle_danger,
            block=False,
            score=0.62 if middle_danger else 0.0,
            reason="Middle location is unqualified; continue observing." if middle_danger else "Current location is not a middle-danger area.",
        ),
        "angle_break_risk": _classifier_detail(
            detected=angle_break_risk,
            block=angle_break_risk,
            score=_clip01(angle.get("angle_break_probability"), 0.0),
            reason="Angle is breaking or exhaustion risk is elevated." if angle_break_risk else "Angle break risk is below block threshold.",
        ),
        "history_would_exit_here": _classifier_detail(
            detected=history_would_exit,
            block=history_would_exit,
            score=max(_clip01(history.get("similarity_to_losing_setups"), 0.0), 0.72 if history_would_exit else 0.0),
            reason=str(history["reason"]),
        ),
        "false_breakout_risk": _classifier_detail(
            detected=false_breakout_risk,
            block=false_breakout_risk,
            score=max(false_breakout_score, 0.70 if false_breakout_risk else 0.0),
            reason="Breakout/sweep has not reclaimed; fakeout risk blocks the entry." if false_breakout_risk else "No unreclaimed breakout or sweep failure detected.",
        ),
        "pullback_not_confirmed": _classifier_detail(
            detected=pullback_not_confirmed,
            block=pullback_not_confirmed,
            score=0.70 if pullback_not_confirmed else 0.0,
            reason="Setup requires a pullback/retest confirmation before paper entry." if pullback_not_confirmed else "Required pullback or retest is confirmed or not required.",
        ),
        "dominance_weakening": _classifier_detail(
            detected=dominance_weakening,
            block=dominance_weakening,
            score=max(current_dominance, opposing_pressure, 0.70 if dominance_weakening else 0.0),
            reason="Dominance is weakening or local/global force disagrees." if dominance_weakening else "Dominance is not weakening enough to block.",
        ),
        "conflict_market": _classifier_detail(
            detected=conflict_market,
            block=conflict_market,
            score=max(_clip01(snapshot.get("conflict_score"), 0.0), min(buy_score, sell_score), 0.72 if conflict_market else 0.0),
            reason="BUY and SELL evidence conflict; contain flip-flop leakage." if conflict_market else "No market conflict classifier triggered.",
        ),
    }
    classifiers = {name: bool(details[name]["detected"]) for name in MARKET_CLASSIFIER_NAMES}
    blocking_reasons = [
        CLASSIFIER_BLOCK_REASONS[name]
        for name in CLASSIFIER_BLOCK_PRIORITY
        if bool(details[name]["detected"]) and bool(details[name]["block"])
    ]
    return {
        "version": MARKET_CLASSIFIERS_VERSION,
        "side": resolved_side,
        "classifiers": classifiers,
        "details": details,
        "blocking_reasons": blocking_reasons,
        "block_reason": blocking_reasons[0] if blocking_reasons else None,
        "paper_entry_allowed": not blocking_reasons,
        "paper_prepare_allowed": bool(not blocking_reasons and (middle_safe or zone["inside_valid_trigger_zone"]) and (pullback_confirmed or resolved_side in {"BUY", "SELL"})),
    }


def detect_bad_entry_class(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    angle = angle_dynamics_agent(snapshot)
    history = historical_pattern_agent(snapshot)
    risk = risk_opposing_force_agent(snapshot, side)
    zone = zone_liquidity_agent(snapshot)
    market_classifiers = classify_market_conditions(snapshot, side)
    pullback_confirmed = _bool(snapshot.get("pullback_confirmed") or snapshot.get("retest_confirmed"))
    valid_reentry = bool(zone["inside_valid_trigger_zone"]) and pullback_confirmed
    steep_no_pullback = bool(angle["late_chase_risk"] or angle["post_impulse_wait_required"]) and not valid_reentry
    history_blocks = not bool(history["executable_vote"])
    risk_unknown_or_close = not bool(risk["distance_ok"]) or _bool(snapshot.get("opposing_force_unknown"))
    detected = bool(
        market_classifiers["classifiers"]["late_chase_after_impulse"]
        or (steep_no_pullback and (history_blocks or risk_unknown_or_close or not bool(zone["inside_valid_trigger_zone"])))
    )
    return {
        "class_id": BAD_ENTRY_CLASS_001,
        "detected": detected,
        "side": _side(side),
        "angle": angle,
        "history": history,
        "risk": risk,
        "zone": zone,
        "market_classifiers": market_classifiers["classifiers"],
        "reason": (
            "BUY/SELL dominance visible but current location is late chase after steep impulse."
            if detected
            else "Late-chase class not detected."
        ),
        "instruction": "Wait for pullback/retest into conservative trigger zone." if detected else "",
    }


def _build_play_reasoning_stack(
    snapshot: Mapping[str, Any],
    *,
    side: str,
    market_context: Mapping[str, Any],
    block_reason: str | None,
) -> dict[str, Any]:
    enriched = dict(snapshot)
    enriched["market_context"] = dict(market_context)
    price_location = analyze_price_location_v3(enriched, side=side)["price_location"]
    regime = analyze_regime_v3(enriched, side=side, price_location=price_location)["regime"]
    market_play = analyze_market_play_v3(
        enriched,
        side=side,
        regime=regime,
        price_location=price_location,
    )["market_play"]
    memory_confirmation = analyze_visual_play_memory_confirmation(
        enriched,
        side=side,
        market_play=market_play,
        regime=regime,
        price_location=price_location,
    )["memory_confirmation"]
    pair_profile = analyze_pair_behavior_profile_v3(enriched)["pair_profile"]
    model_role_outputs = build_model_role_votes_v3(
        enriched,
        side=side,
        market_play=market_play,
        regime=regime,
        price_location=price_location,
        memory_confirmation=memory_confirmation,
        pair_profile=pair_profile,
    )
    reasoning = analyze_reasoning_arbitration_v3(
        enriched,
        side=side,
        market_play=market_play,
        regime=regime,
        price_location=price_location,
        memory_confirmation=memory_confirmation,
        pair_profile=pair_profile,
        model_role_votes=model_role_outputs,
        market_context=market_context,
        existing_block_reason=block_reason,
    )
    context_fields: dict[str, Any] = {
        "regime_primary": regime["primary"],
        "regime_secondary": regime["secondary"],
        "market_play": market_play["primary_play"],
        "market_play_secondary": market_play["secondary_play"],
        "play_stage": market_play["play_stage"],
        "play_side_bias": market_play["side_bias"],
        "relative_price_location": price_location["relative_location"],
        "buy_location_quality": price_location["buy_quality"],
        "sell_location_quality": price_location["sell_quality"],
        "side_location_quality": price_location["side_quality"],
        "memory_vote": memory_confirmation["memory_vote"],
        "memory_confidence_adjustment": memory_confirmation["confidence_adjustment"],
        "pair_volatility_class": pair_profile["volatility_class"],
        "pair_drawdown_first_frequency": pair_profile["drawdown_first_frequency"],
        "bad_entry_filter_active": bool(reasoning["bad_entry_filter"]["active"]),
        "bad_entry_filter_class": reasoning["bad_entry_filter"]["class"],
        "reasoning_decision_state": reasoning["final_reasoning_decision"]["decision"],
        "reasoning_coherence_score": reasoning["arbitration"]["coherence_score"],
    }
    return {
        "regime": regime,
        "market_play": market_play,
        "price_location": price_location,
        "memory_confirmation": memory_confirmation,
        "pair_profile": pair_profile,
        "model_role_outputs": model_role_outputs,
        "reasoning_arbitration": reasoning["arbitration"],
        "bad_entry_filter": reasoning["bad_entry_filter"],
        "final_reasoning_decision": reasoning["final_reasoning_decision"],
        "context_fields": context_fields,
    }


def analyze_market_intelligence(snapshot: Mapping[str, Any], *, candidate_side: str = "HOLD") -> dict[str, Any]:
    supplied_side = _side(candidate_side)
    side = supplied_side if supplied_side in {"BUY", "SELL"} else _candidate_side_v3(snapshot)
    global_agent = global_structure_agent(snapshot)
    local_agent = local_micro_structure_agent(snapshot)
    zone_agent = zone_liquidity_agent(snapshot)
    angle_agent = angle_dynamics_agent(snapshot)
    history_agent = historical_pattern_agent(snapshot)
    risk_agent = risk_opposing_force_agent(snapshot, side)
    bad_entry = detect_bad_entry_class(snapshot, side)
    middle = classify_middle_safe(snapshot, side)
    market_classifiers = classify_market_conditions(snapshot, side)

    inside_valid_trigger = bool(zone_agent["inside_valid_trigger_zone"] or middle["middle_safe"])
    continuation = _bool(snapshot.get("continuation_confirmed") or snapshot.get("pullback_confirmed") or snapshot.get("retest_confirmed"))
    block_reason: str | None = None
    classifier_block = market_classifiers.get("block_reason")
    if classifier_block:
        block_reason = str(classifier_block)
    elif bool(bad_entry["detected"]):
        block_reason = BAD_ENTRY_CLASS_001
    elif not bool(history_agent["executable_vote"]):
        block_reason = "HISTORY_WOULD_EXIT_HERE"
    elif not bool(risk_agent["distance_ok"]):
        block_reason = "OPPOSING_FORCE_TOO_CLOSE"
    elif bool(angle_agent["post_impulse_wait_required"]):
        block_reason = BAD_ENTRY_CLASS_001
    blocked = block_reason is not None
    can_prepare = bool(not blocked and inside_valid_trigger and (continuation or side in {"BUY", "SELL"}))
    execution_state = "WATCHING"
    if can_prepare:
        execution_state = "PREPARING"

    market_context: dict[str, Any] = {
        "global_side": global_agent["global_side"],
        "local_side": local_agent["local_side"],
        "dominant_side": side if side in {"BUY", "SELL"} else global_agent["global_side"],
        "dominance_state": str(snapshot.get("dominance_state") or "UNKNOWN").upper(),
        "current_location": middle["current_location"] if middle["middle_safe"] else zone_agent["current_location"],
        "nearest_supply_zone_id": str(snapshot.get("nearest_supply_zone_id") or ""),
        "nearest_demand_zone_id": str(snapshot.get("nearest_demand_zone_id") or ""),
        "conservative_trigger_zone_id": zone_agent["conservative_trigger_zone_id"],
        "sniper_zone_id": zone_agent["sniper_zone_id"],
        "inside_valid_trigger_zone": inside_valid_trigger,
        "opposing_force_distance_ok": bool(risk_agent["distance_ok"]),
        "is_late_chase": bool(market_classifiers["classifiers"]["late_chase_after_impulse"] or bad_entry["detected"] or angle_agent["late_chase_risk"]),
        "is_steep_angle_break_risk": bool(market_classifiers["classifiers"]["angle_break_risk"] or angle_agent["parabolic_risk"] or angle_agent["angle_break_probability"] >= 0.55),
        "is_reversal_confirmed": _bool(snapshot.get("reversal_confirmed")),
        "is_continuation_confirmed": continuation,
        "middle_safe": bool(middle["middle_safe"]),
        "middle_danger": bool(market_classifiers["classifiers"]["middle_danger"]),
        "near_opposing_force": bool(market_classifiers["classifiers"]["near_opposing_force"]),
        "history_would_exit_here": bool(market_classifiers["classifiers"]["history_would_exit_here"]),
        "false_breakout_risk": bool(market_classifiers["classifiers"]["false_breakout_risk"]),
        "pullback_not_confirmed": bool(market_classifiers["classifiers"]["pullback_not_confirmed"]),
        "dominance_weakening": bool(market_classifiers["classifiers"]["dominance_weakening"]),
        "conflict_market": bool(market_classifiers["classifiers"]["conflict_market"]),
        "paper_entry_allowed": bool(market_classifiers["paper_entry_allowed"]),
        "paper_prepare_allowed": bool(market_classifiers["paper_prepare_allowed"]),
        "classifiers": market_classifiers["classifiers"],
    }
    play_reasoning = _build_play_reasoning_stack(
        snapshot,
        side=side,
        market_context=market_context,
        block_reason=block_reason,
    )
    market_context.update(_mapping(play_reasoning.get("context_fields")))
    market_reality = analyze_market_reality(
        snapshot,
        side=side,
        market_inputs={
            "market_context": market_context,
            "classifiers": market_classifiers["classifiers"],
            "market_classifiers": market_classifiers,
            "angle_context": angle_agent,
            "history_context": history_agent,
            "risk_context": risk_agent,
            "bad_entry": bad_entry,
        },
    )
    trade_permission = _mapping(market_reality.get("trade_permission"))
    permission_prepare_allowed = (
        _bool(trade_permission.get("prepare_allowed"))
        if "prepare_allowed" in trade_permission
        else True
    )
    permission_executable_allowed = (
        _bool(trade_permission.get("executable_allowed"))
        if "executable_allowed" in trade_permission
        else True
    )
    if block_reason is None and not permission_prepare_allowed:
        block_reason = str(trade_permission.get("deny_reason") or "TRADE_PERMISSION_DENIED")
        execution_state = "WATCHING"
    market_context.update(
        {
            "entry_quality_state": _mapping(market_reality.get("entry_quality")).get("state"),
            "entry_quality_score": _mapping(market_reality.get("entry_quality")).get("score"),
            "trade_permission_prepare_allowed": permission_prepare_allowed,
            "trade_permission_executable_allowed": permission_executable_allowed,
            "trade_permission_deny_reason": trade_permission.get("deny_reason"),
            "paper_prepare_allowed": bool(market_context["paper_prepare_allowed"] and permission_prepare_allowed),
            "paper_entry_allowed": bool(market_context["paper_entry_allowed"] and permission_executable_allowed),
        }
    )
    return {
        "agents": [global_agent, local_agent, zone_agent, angle_agent, history_agent, risk_agent],
        "market_context": market_context,
        "regime": play_reasoning["regime"],
        "market_play": play_reasoning["market_play"],
        "price_location": play_reasoning["price_location"],
        "memory_confirmation": play_reasoning["memory_confirmation"],
        "pair_profile": play_reasoning["pair_profile"],
        "model_role_outputs": play_reasoning["model_role_outputs"],
        "reasoning_arbitration": play_reasoning["reasoning_arbitration"],
        "bad_entry_filter": play_reasoning["bad_entry_filter"],
        "final_reasoning_decision": play_reasoning["final_reasoning_decision"],
        "classifiers": market_classifiers["classifiers"],
        "classifier_details": market_classifiers["details"],
        "market_classifiers": market_classifiers,
        "angle_context": {
            "active_trend_angle_degrees": angle_agent["screen_space_angle"],
            "angle_class": angle_agent["angle_class"],
            "steepness_z_score": angle_agent["steepness_z_score"],
            "parabolic_risk": angle_agent["parabolic_risk"],
            "late_chase_risk": angle_agent["late_chase_risk"],
            "angle_break_probability": angle_agent["angle_break_probability"],
            "post_impulse_wait_required": angle_agent["post_impulse_wait_required"],
        },
        "history_context": {
            "similarity_state": history_agent["similarity_state"],
            "best_match_setup": history_agent["best_match_setup"],
            "best_match_outcome": history_agent["best_match_outcome"],
            "historical_entry_quality": history_agent["historical_entry_quality"],
            "historical_late_entry_risk": history_agent["historical_late_entry_risk"],
            "where_history_would_enter": str(_mapping(snapshot.get("history_context")).get("where_history_would_enter", "")),
            "where_history_would_exit": str(_mapping(snapshot.get("history_context")).get("where_history_would_exit", "")),
        },
        "risk_context": risk_agent,
        "market_reality": market_reality,
        "entry_quality": market_reality["entry_quality"],
        "trade_permission": trade_permission,
        "market_trap": market_reality["market_trap"],
        "ideal_trade_path": market_reality["ideal_trade_path"],
        "path_risk": market_reality["path_risk"],
        "regime_playbook": market_reality["regime_playbook"],
        "time_to_reward_invalidation": market_reality["time_to_reward_invalidation"],
        "current_candle_contract": market_reality["current_candle_contract"],
        "market_listening_stream": market_reality["market_listening_stream"],
        "trade_candidate_queue": market_reality["trade_candidate_queue"],
        "bad_entry": bad_entry,
        "execution_hint": {
            "enabled": False,
            "state": execution_state,
            "block_reason": block_reason,
            "instruction": bad_entry["instruction"],
        },
        "block_reason": block_reason,
    }


def _candidate_side_v3(snapshot: Mapping[str, Any]) -> str:
    market_context = _mapping(snapshot.get("market_context"))
    for key in ("candidate_side", "side", "execution_side", "execution_action", "action", "direction", "dominant_side"):
        side = _side(snapshot.get(key))
        if side in {"BUY", "SELL"}:
            return side
    for key in ("dominant_side", "local_side", "global_side"):
        side = _side(market_context.get(key))
        if side in {"BUY", "SELL"}:
            return side
    return "HOLD"


def _nearest_zone_id(zones: Sequence[Mapping[str, Any]], zone_type: str) -> str:
    normalized_type = zone_type.upper()
    candidates: list[tuple[float, str]] = []
    for row in zones:
        row_type = str(row.get("zone_type") or row.get("type") or row.get("kind") or "").strip().upper()
        row_type = row_type.replace(" ", "_").replace("-", "_")
        if row_type != normalized_type:
            continue
        candidates.append((_float(row.get("distance_from_current", row.get("distance", 1.0)), 1.0), str(row.get("zone_id") or row.get("id") or "")))
    if not candidates:
        return ""
    return min(candidates, key=lambda item: item[0])[1]


def analyze_market_intelligence_v3(input_snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return Model Council-ready V3 market evidence without execution authority."""

    snapshot = dict(input_snapshot or {})
    side = _candidate_side_v3(snapshot)
    if side in {"BUY", "SELL"}:
        snapshot.setdefault("candidate_side", side)

    global_agent = global_structure_agent(snapshot)
    local_agent = local_micro_structure_agent(snapshot)
    zone_agent = zone_liquidity_agent(snapshot)
    angle_agent = angle_dynamics_agent(snapshot)
    history_agent = historical_pattern_agent(snapshot)
    risk_agent = risk_opposing_force_agent(snapshot, side)
    bad_entry = detect_bad_entry_class(snapshot, side)
    middle = classify_middle_safe(snapshot, side)
    market_classifiers = classify_market_conditions(snapshot, side)
    zones = _rows(snapshot.get("zones") or snapshot.get("support_resistance_zones"))

    continuation = _bool(
        snapshot.get("continuation_confirmed")
        or snapshot.get("pullback_confirmed")
        or snapshot.get("retest_confirmed")
        or _clip01(snapshot.get("continuation_probability"), 0.0) >= 0.56
    )
    inside_valid_trigger = bool(zone_agent["inside_valid_trigger_zone"] or middle["middle_safe"])
    block_reason: str | None = None
    market_state = "WATCHING"
    final_state = "WATCHING"
    instruction = "Continue observing until context, zone, risk, angle, and history align."

    classifier_block = market_classifiers.get("block_reason")
    if classifier_block:
        block_reason = str(classifier_block)
        market_state = "BLOCKED_BY_MARKET"
        if block_reason == BAD_ENTRY_CLASS_001:
            instruction = "Wait for pullback/retest into conservative trigger zone."
        elif block_reason == "OPPOSING_FORCE_TOO_CLOSE":
            instruction = "Wait until there is enough room before opposing force."
        elif block_reason == "HISTORY_WOULD_EXIT_HERE":
            instruction = "Do not enter where historical analogs would protect or exit."
        elif block_reason == "FALSE_BREAKOUT_RISK":
            instruction = "Wait for breakout reclaim and retest confirmation."
        elif block_reason == "PULLBACK_NOT_CONFIRMED":
            instruction = "Wait for pullback/retest confirmation before preparing."
        elif block_reason == "DOMINANCE_WEAKENING":
            instruction = "Wait until dominance strengthens again."
        elif block_reason == "CONFLICT_MARKET":
            instruction = "Do not enter while BUY and SELL evidence conflict."
        else:
            instruction = "Wait for market risk to clear."
    elif bool(bad_entry["detected"]):
        block_reason = BAD_ENTRY_CLASS_001
        market_state = "BLOCKED_BY_MARKET"
        instruction = "Wait for pullback/retest into conservative trigger zone."
    elif not bool(history_agent["executable_vote"]):
        block_reason = "HISTORY_WOULD_EXIT_HERE"
        market_state = "BLOCKED_BY_MARKET"
        instruction = "Do not enter where historical analogs would protect or exit."
    elif not bool(risk_agent["distance_ok"]):
        block_reason = "OPPOSING_FORCE_TOO_CLOSE"
        market_state = "BLOCKED_BY_MARKET"
        instruction = "Wait until there is enough room before opposing force."
    elif bool(angle_agent["post_impulse_wait_required"]):
        block_reason = BAD_ENTRY_CLASS_001
        market_state = "BLOCKED_BY_MARKET"
        instruction = "Wait for pullback/retest into conservative trigger zone."
    elif side in {"BUY", "SELL"} and inside_valid_trigger and continuation:
        final_state = "PREPARING"
        market_state = "PREPARING"
        instruction = "Market context can prepare; Model Council timing must mature before executable packet."

    directional_score = _clip01(
        0.24 * _clip01(global_agent.get("global_confidence"), 0.0)
        + 0.20 * _clip01(local_agent.get("confidence"), 0.0)
        + 0.18 * float(inside_valid_trigger)
        + 0.18 * float(risk_agent["distance_ok"])
        + 0.12 * float(history_agent["executable_vote"])
        + 0.08 * (1.0 - _clip01(angle_agent.get("angle_break_probability"), 0.0)),
        0.0,
    )
    buy_raw = directional_score if side == "BUY" else 0.08 if side == "SELL" else 0.0
    sell_raw = directional_score if side == "SELL" else 0.08 if side == "BUY" else 0.0
    hold_raw = _clip01(
        0.24
        + 0.22 * float(block_reason is not None)
        + 0.14 * float(not inside_valid_trigger)
        + 0.10 * float(not continuation)
        + 0.10 * float(bool(angle_agent["late_chase_risk"])),
        0.0,
    )
    total = max(1e-9, buy_raw + sell_raw + hold_raw)
    buy_score = round(float(buy_raw / total), 4)
    sell_score = round(float(sell_raw / total), 4)
    hold_score = round(float(hold_raw / total), 4)

    current_location = str(middle["current_location"] if middle["middle_safe"] else zone_agent["current_location"])
    dominance_state = str(
        snapshot.get("dominance_state")
        or ("STRENGTHENING" if global_agent["global_side"] == local_agent["local_side"] == side else "FORMING")
    ).upper()
    market_context: dict[str, Any] = {
        "global_side": global_agent["global_side"],
        "local_side": local_agent["local_side"],
        "dominant_side": side if side in {"BUY", "SELL"} else global_agent["global_side"],
        "dominance_state": dominance_state,
        "current_location": current_location,
        "nearest_supply_zone_id": _nearest_zone_id(zones, "SUPPLY"),
        "nearest_demand_zone_id": _nearest_zone_id(zones, "DEMAND"),
        "conservative_trigger_zone_id": zone_agent["conservative_trigger_zone_id"],
        "sniper_zone_id": zone_agent["sniper_zone_id"],
        "inside_valid_trigger_zone": inside_valid_trigger,
        "opposing_force_distance_ok": bool(risk_agent["distance_ok"]),
        "is_late_chase": bool(market_classifiers["classifiers"]["late_chase_after_impulse"] or bad_entry["detected"] or angle_agent["late_chase_risk"]),
        "is_steep_angle_break_risk": bool(market_classifiers["classifiers"]["angle_break_risk"] or angle_agent["parabolic_risk"] or angle_agent["angle_break_probability"] >= 0.55),
        "is_reversal_confirmed": _bool(snapshot.get("reversal_confirmed")),
        "is_continuation_confirmed": continuation,
        "middle_safe": bool(middle["middle_safe"]),
        "middle_danger": bool(market_classifiers["classifiers"]["middle_danger"]),
        "near_opposing_force": bool(market_classifiers["classifiers"]["near_opposing_force"]),
        "history_would_exit_here": bool(market_classifiers["classifiers"]["history_would_exit_here"]),
        "false_breakout_risk": bool(market_classifiers["classifiers"]["false_breakout_risk"]),
        "pullback_not_confirmed": bool(market_classifiers["classifiers"]["pullback_not_confirmed"]),
        "dominance_weakening": bool(market_classifiers["classifiers"]["dominance_weakening"]),
        "conflict_market": bool(market_classifiers["classifiers"]["conflict_market"]),
        "paper_entry_allowed": bool(market_classifiers["paper_entry_allowed"]),
        "paper_prepare_allowed": bool(market_classifiers["paper_prepare_allowed"]),
        "classifiers": market_classifiers["classifiers"],
    }
    play_reasoning = _build_play_reasoning_stack(
        snapshot,
        side=side,
        market_context=market_context,
        block_reason=block_reason,
    )
    market_context.update(_mapping(play_reasoning.get("context_fields")))
    market_reality = analyze_market_reality(
        snapshot,
        side=side,
        market_inputs={
            "market_context": market_context,
            "classifiers": market_classifiers["classifiers"],
            "market_classifiers": market_classifiers,
            "angle_context": angle_agent,
            "history_context": history_agent,
            "risk_context": risk_agent,
            "bad_entry": bad_entry,
        },
    )
    trade_permission = _mapping(market_reality.get("trade_permission"))
    permission_prepare_allowed = (
        _bool(trade_permission.get("prepare_allowed"))
        if "prepare_allowed" in trade_permission
        else True
    )
    permission_executable_allowed = (
        _bool(trade_permission.get("executable_allowed"))
        if "executable_allowed" in trade_permission
        else True
    )
    if block_reason is None and not permission_prepare_allowed:
        block_reason = str(trade_permission.get("deny_reason") or "TRADE_PERMISSION_DENIED")
        market_state = "BLOCKED_BY_MARKET"
        final_state = "WATCHING"
        instruction = "Trade permission stack denied the current entry; continue studying until permission resets."
    market_context.update(
        {
            "entry_quality_state": _mapping(market_reality.get("entry_quality")).get("state"),
            "entry_quality_score": _mapping(market_reality.get("entry_quality")).get("score"),
            "trade_permission_prepare_allowed": permission_prepare_allowed,
            "trade_permission_executable_allowed": permission_executable_allowed,
            "trade_permission_deny_reason": trade_permission.get("deny_reason"),
            "paper_prepare_allowed": bool(market_context["paper_prepare_allowed"] and permission_prepare_allowed),
            "paper_entry_allowed": bool(market_context["paper_entry_allowed"] and permission_executable_allowed),
        }
    )
    arbitration_reason = (
        f"{side} dominance visible but current location is late chase after steep impulse."
        if block_reason == BAD_ENTRY_CLASS_001
        else str(history_agent["reason"])
        if block_reason == "HISTORY_WOULD_EXIT_HERE"
        else str(risk_agent["reason"])
        if block_reason == "OPPOSING_FORCE_TOO_CLOSE"
        else str(market_classifiers["details"]["false_breakout_risk"]["reason"])
        if block_reason == "FALSE_BREAKOUT_RISK"
        else str(market_classifiers["details"]["pullback_not_confirmed"]["reason"])
        if block_reason == "PULLBACK_NOT_CONFIRMED"
        else str(market_classifiers["details"]["dominance_weakening"]["reason"])
        if block_reason == "DOMINANCE_WEAKENING"
        else str(market_classifiers["details"]["conflict_market"]["reason"])
        if block_reason == "CONFLICT_MARKET"
        else str(market_classifiers["details"]["angle_break_risk"]["reason"])
        if block_reason == "ANGLE_BREAK_RISK"
        else str(trade_permission.get("reason"))
        if block_reason is not None and block_reason == trade_permission.get("deny_reason")
        else f"{side} can prepare: zone/risk/angle/history evidence is acceptable."
        if final_state == "PREPARING"
        else "Market evidence is still forming."
    )
    return {
        "version": PG_MARKET_INTELLIGENCE_VERSION,
        "execution": {"enabled": False, "state": final_state, "side": side if side in {"BUY", "SELL"} else None},
        "model_council": {
            "final_state": final_state,
            "market_state": market_state,
            "final_side": side if side in {"BUY", "SELL"} else "HOLD",
            "maturity_stage": "ZONE_QUALIFICATION" if final_state == "PREPARING" else "OBSERVATION",
            "arbitration_reason": arbitration_reason,
            "reason": arbitration_reason,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "hold_score": hold_score,
            "dominance_margin": round(abs(buy_score - sell_score), 4),
            "disagreement_score": round(1.0 - max(buy_score, sell_score, hold_score), 4),
            "contributors_are_diagnostic": True,
            "primary_play": play_reasoning["market_play"]["primary_play"],
            "play_stage": play_reasoning["market_play"]["play_stage"],
            "regime_primary": play_reasoning["regime"]["primary"],
            "price_location": play_reasoning["price_location"]["relative_location"],
            "reasoning_decision": play_reasoning["final_reasoning_decision"]["decision"],
            "reasoning_coherence_score": play_reasoning["reasoning_arbitration"]["coherence_score"],
            "model_role_outputs": play_reasoning["model_role_outputs"],
        },
        "market_context": market_context,
        "regime": play_reasoning["regime"],
        "market_play": play_reasoning["market_play"],
        "price_location": play_reasoning["price_location"],
        "memory_confirmation": play_reasoning["memory_confirmation"],
        "pair_profile": play_reasoning["pair_profile"],
        "model_role_outputs": play_reasoning["model_role_outputs"],
        "reasoning_arbitration": play_reasoning["reasoning_arbitration"],
        "bad_entry_filter": play_reasoning["bad_entry_filter"],
        "final_reasoning_decision": play_reasoning["final_reasoning_decision"],
        "classifiers": market_classifiers["classifiers"],
        "classifier_details": market_classifiers["details"],
        "market_classifiers": market_classifiers,
        "angle_context": dict(angle_agent),
        "history_context": dict(history_agent),
        "risk_context": dict(risk_agent),
        "market_reality": market_reality,
        "entry_quality": market_reality["entry_quality"],
        "trade_permission": trade_permission,
        "market_trap": market_reality["market_trap"],
        "ideal_trade_path": market_reality["ideal_trade_path"],
        "path_risk": market_reality["path_risk"],
        "regime_playbook": market_reality["regime_playbook"],
        "time_to_reward_invalidation": market_reality["time_to_reward_invalidation"],
        "current_candle_contract": market_reality["current_candle_contract"],
        "market_listening_stream": market_reality["market_listening_stream"],
        "trade_candidate_queue": market_reality["trade_candidate_queue"],
        "agents": {
            "global_structure": global_agent,
            "local_micro_structure": local_agent,
            "zone_liquidity": zone_agent,
            "angle_dynamics": angle_agent,
            "historical_pattern": history_agent,
            "risk_opposing_force": risk_agent,
        },
        "zones": zones,
        "bad_entry": bad_entry,
        "block_reason": block_reason,
        "instruction": instruction,
    }


def global_structure(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return global_structure_agent(snapshot)


def local_micro_structure(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return local_micro_structure_agent(snapshot)


def zone_liquidity(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return zone_liquidity_agent(snapshot)


def angle_dynamics(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    enriched = dict(snapshot)
    if side in {"BUY", "SELL"}:
        enriched.setdefault("candidate_side", side)
    return angle_dynamics_agent(enriched)


def historical_pattern(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return historical_pattern_agent(snapshot)


def risk_opposing_force(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return risk_opposing_force_agent(snapshot, side)


def market_classifiers(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_conditions(snapshot, side)


def classify_market_classifier(snapshot: Mapping[str, Any], name: str, side: str = "HOLD") -> dict[str, Any]:
    resolved = str(name or "").strip().lower()
    result = classify_market_conditions(snapshot, side)
    if resolved not in result["details"]:
        return _classifier_detail(detected=False, block=False, score=0.0, reason=f"Unknown classifier {resolved}.")
    return dict(result["details"][resolved])


def late_chase_after_impulse(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "late_chase_after_impulse", side)


def near_opposing_force(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "near_opposing_force", side)


def middle_safe(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "middle_safe", side)


def middle_danger(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "middle_danger", side)


def angle_break_risk(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "angle_break_risk", side)


def history_would_exit_here(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "history_would_exit_here", side)


def false_breakout_risk(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "false_breakout_risk", side)


def pullback_not_confirmed(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "pullback_not_confirmed", side)


def dominance_weakening(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "dominance_weakening", side)


def conflict_market(snapshot: Mapping[str, Any], side: str = "HOLD") -> dict[str, Any]:
    return classify_market_classifier(snapshot, "conflict_market", side)


__all__ = [
    "BAD_ENTRY_CLASS_001",
    "CLASSIFIER_BLOCK_REASONS",
    "MARKET_CLASSIFIER_NAMES",
    "MARKET_CLASSIFIERS_VERSION",
    "PG_MARKET_INTELLIGENCE_VERSION",
    "adaptive_angle_threshold",
    "analyze_market_intelligence",
    "analyze_market_intelligence_v3",
    "angle_break_risk",
    "angle_dynamics",
    "angle_dynamics_agent",
    "classify_market_classifier",
    "classify_market_conditions",
    "classify_middle_safe",
    "conflict_market",
    "detect_bad_entry_class",
    "dominance_weakening",
    "false_breakout_risk",
    "global_structure",
    "global_structure_agent",
    "history_would_exit_here",
    "historical_pattern",
    "historical_pattern_agent",
    "late_chase_after_impulse",
    "local_micro_structure",
    "local_micro_structure_agent",
    "market_classifiers",
    "middle_danger",
    "middle_safe",
    "near_opposing_force",
    "pullback_not_confirmed",
    "risk_opposing_force",
    "risk_opposing_force_agent",
    "zone_liquidity",
    "zone_liquidity_agent",
]
