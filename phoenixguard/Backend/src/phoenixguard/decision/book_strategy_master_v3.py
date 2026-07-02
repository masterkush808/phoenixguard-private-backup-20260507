from __future__ import annotations

from math import isfinite
from typing import Any, Literal, Mapping, Sequence, cast


BOOK_STRATEGY_SCHEMA_VERSION = "PG_BOOK_STRATEGY_MASTER_V3"
BOOK_STRATEGY_EXECUTION_AUTHORITY = "PLAYBOOK_FINAL_DECIDER_V3"
MODEL_COUNCIL_CONTRIBUTOR_ROLE = "MODEL_COUNCIL_CONTRIBUTOR_GATE_V3"

BookMaturityState = Literal[
    "NO_OPPORTUNITY",
    "EARLY_FORMING",
    "VALID_WATCH",
    "PREPARE",
    "ENTER_NOW",
    "LATE_CHASE",
    "INVALIDATED",
    "MISSED",
]

BookEntryProfile = Literal[
    "AGGRESSIVE_SNIPER",
    "CONSERVATIVE_RETEST",
    "CONTINUATION_RETEST",
    "REVERSAL_RECLAIM",
    "MOMENTUM_ACCEPTANCE",
    "WATCH_ONLY",
    "NO_TRADE",
]

BookReactionType = Literal[
    "WICK_REJECTION",
    "BODY_ACCEPTANCE",
    "RETEST_HOLD",
    "RECLAIM_AFTER_SWEEP",
    "CONTINUATION_PRESSURE",
    "EXHAUSTION",
    "NO_REACTION",
]

BOOK_STRATEGY_MATURITY_STATES: tuple[BookMaturityState, ...] = (
    "NO_OPPORTUNITY",
    "EARLY_FORMING",
    "VALID_WATCH",
    "PREPARE",
    "ENTER_NOW",
    "LATE_CHASE",
    "INVALIDATED",
    "MISSED",
)

BOOK_ENTRY_PROFILES: tuple[BookEntryProfile, ...] = (
    "AGGRESSIVE_SNIPER",
    "CONSERVATIVE_RETEST",
    "CONTINUATION_RETEST",
    "REVERSAL_RECLAIM",
    "MOMENTUM_ACCEPTANCE",
    "WATCH_ONLY",
    "NO_TRADE",
)

BOOK_REACTION_TYPES: tuple[BookReactionType, ...] = (
    "WICK_REJECTION",
    "BODY_ACCEPTANCE",
    "RETEST_HOLD",
    "RECLAIM_AFTER_SWEEP",
    "CONTINUATION_PRESSURE",
    "EXHAUSTION",
    "NO_REACTION",
)

BOOK_STRATEGY_PLAYBOOKS: tuple[str, ...] = (
    "SMC_TURTLE_SOUP",
    "SMC_SH_BMS_RTO",
    "SMC_SMS_BMS_RTO",
    "AMD_REVERSAL",
    "SUPPLY_REJECTION",
    "DEMAND_REJECTION",
    "SUPPLY_BREAK_RETEST_CONTINUATION",
    "DEMAND_BREAK_RETEST_CONTINUATION",
    "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION",
    "FAILED_DEMAND_RECLAIM_SELL_CONTINUATION",
    "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL",
    "FAILED_BUY_INTO_SUPPLY_SELL_REVERSAL",
    "TRENDLINE_CONFLUENCE_BOUNCE",
    "TRENDLINE_BREAK_RETEST",
    "CHANNEL_EDGE_REACTION",
    "FIB_OTE_REACTION",
    "PIVOT_ROUND_NUMBER_REACTION",
    "SLINGSHOT_FALSE_BREAK",
    "CANDLE_CONFIRMATION_AT_ZONE",
    "COUNTERTREND_SCALP_ONLY",
    "CHOP_NO_TRADE",
)

ENTER_NOW_LANES: frozenset[str] = frozenset(
    {
        "SNIPER_ZONE_ENTRY",
        "FAILED_RETEST_ENTRY",
        "LOCAL_BREAKDOWN_CONTINUATION",
        "HISTORY_MATCHED_CONTINUATION",
        "WAVE_RIDING_CONTINUATION",
        "MOMENTUM_ACCEPTANCE_ENTRY",
    }
)
PLAYBOOK_HARD_BAD_ENTRY_CLASSES: frozenset[str] = frozenset(
    {
        "BUY_HIGH_AFTER_IMPULSE",
        "SELL_LOW_AFTER_DROP",
        "LATE_CHASE",
        "LATE_CHASE_AFTER_IMPULSE",
        "LATE_CHASE_STEEP_IMPULSE",
        "INTO_OPPOSING_FORCE",
        "NO_PATH_ROOM",
        "FAKE_BREAKOUT_RISK",
        "WICK_TRAP",
        "DRAWDOWN_FIRST",
        "DRAWDOWN_FIRST_EXPECTED",
    }
)
PLAYBOOK_LATE_CHASE_BAD_ENTRY_CLASSES: frozenset[str] = frozenset(
    {
        "BUY_HIGH_AFTER_IMPULSE",
        "SELL_LOW_AFTER_DROP",
        "LATE_CHASE",
        "LATE_CHASE_AFTER_IMPULSE",
        "LATE_CHASE_STEEP_IMPULSE",
    }
)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    source = cast(Mapping[Any, Any], value)
    return {str(key): item for key, item in source.items()}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not isfinite(parsed):
        return float(default)
    return float(parsed)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _clip01(value: Any, default: float = 0.0) -> float:
    parsed = _float(value, default)
    return max(0.0, min(1.0, parsed))


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _side(value: Any) -> str:
    text = _upper(value)
    if text in {"BUY", "BULL", "BULLISH", "UP", "UPTREND", "CALL", "DEMAND"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "DOWNTREND", "PUT", "SUPPLY"}:
        return "SELL"
    return "HOLD"


def _opposite_side(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"


def _first_side(*values: Any) -> str:
    for value in values:
        side = _side(value)
        if side in {"BUY", "SELL"}:
            return side
    return "HOLD"


def _contains_any(value: Any, tokens: Sequence[str]) -> bool:
    text = _upper(value)
    return any(token in text for token in tokens)


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        mapped = _mapping(value)
        if mapped:
            return mapped
    return {}


def _first_rows(*values: Any) -> list[dict[str, Any]]:
    for value in values:
        rows = _rows(value)
        if rows:
            return rows
    return []


def _zone_type(zone: Mapping[str, Any]) -> str:
    zone_type = _upper(zone.get("zone_type") or zone.get("type") or zone.get("kind") or zone.get("role"))
    if "DEMAND" in zone_type or zone_type == "SUPPORT":
        return "DEMAND"
    if "SUPPLY" in zone_type or zone_type == "RESISTANCE":
        return "SUPPLY"
    return zone_type


def _zone_side(zone: Mapping[str, Any]) -> str:
    resolved = _side(zone.get("side") or zone.get("direction"))
    if resolved in {"BUY", "SELL"}:
        return resolved
    zone_type = _zone_type(zone)
    if zone_type == "DEMAND":
        return "BUY"
    if zone_type == "SUPPLY":
        return "SELL"
    return "HOLD"


def _zone_current_price_inside(zone: Mapping[str, Any]) -> bool:
    return _bool(
        zone.get("current_price_inside")
        or zone.get("inside")
        or zone.get("touching_now")
        or zone.get("sniper_touching_now")
        or zone.get("trigger_touching_now")
    )


def _zone_role_flip_confirmed(zone: Mapping[str, Any]) -> bool:
    return bool(
        _bool(zone.get("role_flip_confirmed"))
        or _contains_any(zone.get("role_flip_state"), ("ROLE_FLIP_CONFIRMED", "BROKEN_SUPPORT_ROLE_FLIP", "BROKEN_RESISTANCE_ROLE_FLIP"))
        or _contains_any(zone.get("zone_authority_state"), ("ROLE_FLIP_CONFIRMED",))
    )


def _nearest_zone_for_side(zones: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for zone in zones:
        if _zone_side(zone) != side:
            continue
        distance = _float(zone.get("distance_from_current") or zone.get("distance_to_latest_norm") or zone.get("distance"), 1.0)
        inside_bonus = -0.35 if _zone_current_price_inside(zone) else 0.0
        candidates.append((max(0.0, distance + inside_bonus), _mapping(zone)))
    if not candidates:
        return {}
    return min(candidates, key=lambda item: item[0])[1]


def _opposing_zone_for_side(zones: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"
    return _nearest_zone_for_side(zones, opposite)


def _location_supports_side(side: str, location: Any, zone: Mapping[str, Any]) -> bool:
    text = _upper(location)
    zone_type = _zone_type(zone) if zone else ""
    if side == "BUY":
        return bool(zone_type == "DEMAND" or _contains_any(text, ("DEMAND", "SUPPORT", "LOCAL_LOW", "RANGE_LOW", "LOW")))
    if side == "SELL":
        return bool(zone_type == "SUPPLY" or _contains_any(text, ("SUPPLY", "RESISTANCE", "LOCAL_HIGH", "RANGE_HIGH", "HIGH")))
    return False


def _short_horizon_side(snapshot: Mapping[str, Any]) -> tuple[str, float]:
    study = _first_mapping(
        snapshot.get("two_candle_study"),
        _mapping(snapshot.get("decision_kernel")).get("two_candle_study"),
        _mapping(snapshot.get("tracking_summary")).get("two_candle_study"),
    )
    lstm = _first_mapping(
        snapshot.get("lstm_contribution"),
        study.get("lstm_contribution"),
        _mapping(snapshot.get("decision_kernel")).get("lstm_contribution"),
    )
    next_candle = _first_mapping(
        _mapping(snapshot.get("decision_kernel")).get("next_candle"),
        _mapping(snapshot.get("latest_signal")).get("next_candle"),
    )
    for source in (study, lstm, next_candle):
        side = _first_side(
            source.get("next_1_direction"),
            source.get("next_candle_bias"),
            source.get("primary_pressure"),
            source.get("side"),
        )
        if side in {"BUY", "SELL"}:
            probability = max(
                _clip01(source.get("next_1_probability"), 0.0),
                _clip01(source.get("probability"), 0.0),
                _clip01(source.get("confidence"), 0.0),
                _clip01(source.get(f"p_next_{side.lower()}"), 0.0),
            )
            return side, probability
    return "HOLD", 0.0


def _explicit_false(mapping: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in mapping and not _bool(mapping.get(key)):
            return True
    return False


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None


def _max_score(mapping: Mapping[str, Any], keys: Sequence[str], default: float = 0.0) -> float:
    values = [_clip01(mapping.get(key), default) for key in keys if mapping.get(key) is not None]
    return max(values) if values else float(default)


def _trendline_role(trendline: Mapping[str, Any]) -> str:
    role = _upper(trendline.get("trendline_role") or trendline.get("role") or trendline.get("type") or trendline.get("label"))
    if "SUPPORT" in role:
        return "SUPPORT_TRENDLINE"
    if "RESISTANCE" in role:
        return "RESISTANCE_TRENDLINE"
    if "INNER" in role:
        return "INNER_TRENDLINE"
    return role or "TRENDLINE"


def _trendline_side(trendline: Mapping[str, Any]) -> str:
    side = _side(trendline.get("side") or trendline.get("direction"))
    if side in {"BUY", "SELL"}:
        return side
    role = _trendline_role(trendline)
    if role == "SUPPORT_TRENDLINE":
        return "BUY"
    if role == "RESISTANCE_TRENDLINE":
        return "SELL"
    return "HOLD"


def _trendline_touching_now(trendline: Mapping[str, Any]) -> bool:
    return _bool(
        trendline.get("touching_now")
        or trendline.get("current_price_touching")
        or trendline.get("wick_probe_now")
        or trendline.get("sniper_touching_now")
        or trendline.get("near_line_now")
    )


def _best_trendline_for_side(trendlines: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for trendline in trendlines:
        if _trendline_side(trendline) not in {side, "HOLD"}:
            continue
        state = _upper(trendline.get("state") or trendline.get("breach_state") or trendline.get("validation_state"))
        if state in {"INVALIDATED", "BROKEN", "HISTORICAL"}:
            continue
        score = max(
            _clip01(trendline.get("significance_score"), 0.0),
            _clip01(trendline.get("touch_quality"), 0.0),
            _clip01(trendline.get("confidence"), 0.0),
        )
        if _trendline_touching_now(trendline):
            score += 0.35
        if _upper(trendline.get("trendline_scope") or trendline.get("scope")) in {"MAJOR", "OUTER", "GLOBAL"}:
            score += 0.12
        candidates.append((score, _mapping(trendline)))
    if not candidates:
        return {}
    return max(candidates, key=lambda item: item[0])[1]


def _zone_significance(zone: Mapping[str, Any]) -> float:
    return max(
        _clip01(zone.get("significance_score"), 0.0),
        _clip01(zone.get("historical_significance"), 0.0),
        _clip01(zone.get("confidence"), 0.0),
        min(1.0, _float(zone.get("touch_count"), 0.0) / 4.0),
        min(1.0, _float(zone.get("reaction_count"), 0.0) / 3.0),
        min(1.0, _float(zone.get("retest_count"), 0.0) / 3.0),
    )


def _trendline_significance(trendline: Mapping[str, Any]) -> float:
    return max(
        _clip01(trendline.get("significance_score"), 0.0),
        _clip01(trendline.get("touch_quality"), 0.0),
        _clip01(trendline.get("confidence"), 0.0),
        min(1.0, _float(trendline.get("touch_count"), 0.0) / 3.0),
        min(1.0, _float(trendline.get("wick_probe_count"), 0.0) / 3.0),
    )


def _select_significant_structure(
    *,
    side: str,
    active_zone: Mapping[str, Any],
    trendline: Mapping[str, Any],
    inside_trigger: bool,
    trendline_confluence: bool,
    role_flip_confirmed: bool,
    retest_confirmed: bool,
    continuation_confirmed: bool,
    break_of_structure_confirmed: bool,
    liquidity_sweep_detected: bool,
) -> dict[str, Any]:
    zone_score = _zone_significance(active_zone) if active_zone else 0.0
    trend_score = _trendline_significance(trendline) if trendline else 0.0
    zone_role_flip = _zone_role_flip_confirmed(active_zone) if active_zone else False
    if role_flip_confirmed and (retest_confirmed or zone_role_flip or break_of_structure_confirmed):
        return {
            "source": "role_flip",
            "structure_type": "ROLE_FLIP_RETEST",
            "source_id": str(active_zone.get("zone_id") or active_zone.get("id") or active_zone.get("key") or ""),
            "significance_score": 0.76,
            "current_touch": bool(retest_confirmed or _zone_current_price_inside(active_zone)),
            "reason": "Broken structure is acting as a role-flip retest.",
            "significant": True,
        }
    if active_zone and (inside_trigger or zone_score >= 0.55):
        structure_type = _zone_type(active_zone) or ("DEMAND" if side == "BUY" else "SUPPLY")
        current_touch = bool(inside_trigger or _zone_current_price_inside(active_zone))
        return {
            "source": "zone",
            "structure_type": f"{structure_type}_ZONE",
            "source_id": str(active_zone.get("zone_id") or active_zone.get("id") or active_zone.get("key") or ""),
            "significance_score": round(float(max(zone_score, 0.72 if current_touch else 0.0)), 4),
            "current_touch": current_touch,
            "reason": f"{structure_type.lower()} zone is active at the current price.",
            "significant": True,
        }
    if inside_trigger:
        structure_type = "DEMAND" if side == "BUY" else "SUPPLY" if side == "SELL" else "TRIGGER"
        return {
            "source": "trigger_zone",
            "structure_type": f"{structure_type}_ZONE",
            "source_id": "",
            "significance_score": 0.68,
            "current_touch": True,
            "reason": "Current price is inside a valid trigger zone.",
            "significant": True,
        }
    if trendline and (trendline_confluence or _trendline_touching_now(trendline) or trend_score >= 0.58):
        role = _trendline_role(trendline)
        current_touch = bool(trendline_confluence or _trendline_touching_now(trendline))
        return {
            "source": "trendline",
            "structure_type": role,
            "source_id": str(trendline.get("trendline_id") or trendline.get("id") or trendline.get("key") or ""),
            "significance_score": round(float(max(trend_score, 0.70 if current_touch else 0.0)), 4),
            "current_touch": current_touch,
            "reason": f"{role.lower()} is active for timing.",
            "significant": True,
        }
    if role_flip_confirmed:
        return {
            "source": "role_flip",
            "structure_type": "ROLE_FLIP_RETEST",
            "source_id": "",
            "significance_score": 0.74,
            "current_touch": bool(retest_confirmed),
            "reason": "Broken structure is acting as a role-flip retest.",
            "significant": True,
        }
    if liquidity_sweep_detected:
        return {
            "source": "liquidity",
            "structure_type": "LIQUIDITY_SWEEP_RECLAIM",
            "source_id": "",
            "significance_score": 0.72,
            "current_touch": True,
            "reason": "Stop-hunt/liquidity sweep context is active.",
            "significant": True,
        }
    if break_of_structure_confirmed and retest_confirmed:
        return {
            "source": "structure",
            "structure_type": "BMS_RETEST",
            "source_id": "",
            "significance_score": 0.70,
            "current_touch": True,
            "reason": "Break-of-structure retest is active.",
            "significant": True,
        }
    if continuation_confirmed:
        return {
            "source": "flow",
            "structure_type": "FLOW_CONTINUATION",
            "source_id": "",
            "significance_score": 0.62,
            "current_touch": False,
            "reason": "Directional flow continuation is present.",
            "significant": True,
        }
    return {
        "source": "none",
        "structure_type": "NO_SIGNIFICANT_STRUCTURE",
        "source_id": "",
        "significance_score": 0.0,
        "current_touch": False,
        "reason": "No significant reaction structure is active yet.",
        "significant": False,
    }


def _classify_candlestick_reaction(
    candle: Mapping[str, Any],
    *,
    side: str,
    current_candle_ok: bool,
    inside_trigger: bool,
    retest_confirmed: bool,
    continuation_confirmed: bool,
    liquidity_sweep_detected: bool,
) -> dict[str, Any]:
    pattern = _upper(candle.get("pattern_name") or candle.get("pattern") or candle.get("name"))
    role = _upper(candle.get("pattern_role") or candle.get("role") or candle.get("family"))
    lower_wick = _clip01(
        _first_present(candle, "lower_shadow_range_ratio", "lower_wick_range_ratio", "lower_wick_pct", "lower_shadow_pct"),
        0.0,
    )
    upper_wick = _clip01(
        _first_present(candle, "upper_shadow_range_ratio", "upper_wick_range_ratio", "upper_wick_pct", "upper_shadow_pct"),
        0.0,
    )
    close_location = _clip01(_first_present(candle, "close_location_value", "close_pos_in_range", "close_position"), 0.5)
    explicit_wick = _bool(
        candle.get("wick_rejection")
        or candle.get("rejection_wick")
        or candle.get("sniper_reaction")
        or candle.get("touch_rejection")
    )
    buy_wick = bool(
        side == "BUY"
        and (
            explicit_wick
            or _bool(candle.get("lower_wick_rejection"))
            or (lower_wick >= 0.34 and close_location >= 0.45)
            or pattern in {"HAMMER", "DRAGONFLY_DOJI", "BULLISH_PIN_BAR", "TWEEZER_BOTTOM"}
        )
    )
    sell_wick = bool(
        side == "SELL"
        and (
            explicit_wick
            or _bool(candle.get("upper_wick_rejection"))
            or (upper_wick >= 0.34 and close_location <= 0.55)
            or pattern in {"SHOOTING_STAR", "GRAVESTONE_DOJI", "BEARISH_PIN_BAR", "TWEEZER_TOP"}
        )
    )
    wick_rejection = bool(buy_wick or sell_wick)
    body_acceptance = bool(
        current_candle_ok
        or _bool(candle.get("body_acceptance"))
        or _bool(candle.get("close_acceptance"))
        or _bool(candle.get("accepted"))
        or _contains_any(pattern, ("ENGULFING", "MARUBOZU", "LONG_BODY", "MEETING_LINE", "KICKING"))
    )
    retest_hold = bool(
        retest_confirmed
        or _bool(candle.get("retest_hold"))
        or _bool(candle.get("failed_retest"))
        or _contains_any(pattern, ("HARAMI", "PIERCING", "DARK_CLOUD", "TWEEZER"))
    )
    continuation_pressure = bool(
        continuation_confirmed
        or role == "CONTINUATION"
        or _contains_any(pattern, ("THREE_METHODS", "SEPARATING_LINES", "TASUKI", "MAT_HOLD"))
    )
    exhaustion = bool(
        _bool(candle.get("too_late") or candle.get("exhaustion") or candle.get("wick_reversal_risk"))
        or role in {"EXHAUSTION", "INDECISION"}
        or _contains_any(pattern, ("HIGH_WAVE", "SPINNING_TOP", "DOJI"))
    )
    if liquidity_sweep_detected and (wick_rejection or body_acceptance or retest_hold):
        reaction_type: BookReactionType = "RECLAIM_AFTER_SWEEP"
    elif wick_rejection and inside_trigger:
        reaction_type = "WICK_REJECTION"
    elif retest_hold:
        reaction_type = "RETEST_HOLD"
    elif continuation_pressure and body_acceptance:
        reaction_type = "CONTINUATION_PRESSURE"
    elif body_acceptance:
        reaction_type = "BODY_ACCEPTANCE"
    elif exhaustion:
        reaction_type = "EXHAUSTION"
    else:
        reaction_type = "NO_REACTION"
    explicit_score = _max_score(
        candle,
        (
            "final_candle_evidence_score",
            "confirmation_score",
            "morphology_score",
            "reaction_score",
            "entry_score",
            "score",
        ),
        0.0,
    )
    derived_score = 0.0
    derived_score += 0.32 if wick_rejection else 0.0
    derived_score += 0.25 if body_acceptance else 0.0
    derived_score += 0.22 if retest_hold else 0.0
    derived_score += 0.18 if continuation_pressure else 0.0
    derived_score -= 0.20 if reaction_type == "EXHAUSTION" else 0.0
    quality = _clip01(max(explicit_score, derived_score))
    return {
        "reaction_type": reaction_type,
        "reaction_quality": round(float(quality), 4),
        "pattern_name": pattern,
        "pattern_role": role,
        "wick_rejection": wick_rejection,
        "body_acceptance": body_acceptance,
        "retest_hold": retest_hold,
        "continuation_pressure": continuation_pressure,
        "exhaustion": exhaustion,
        "lower_wick_ratio": round(float(lower_wick), 4),
        "upper_wick_ratio": round(float(upper_wick), 4),
        "close_location": round(float(close_location), 4),
    }


def _select_entry_profile(
    *,
    side: str,
    significant_structure: Mapping[str, Any],
    candle_reaction: Mapping[str, Any],
    inside_trigger: bool,
    trendline_confluence: bool,
    retest_confirmed: bool,
    role_flip_confirmed: bool,
    continuation_confirmed: bool,
    break_of_structure_confirmed: bool,
    structure_shift_confirmed: bool,
    liquidity_sweep_detected: bool,
    timing_supportive: bool,
    current_candle_ok: bool,
    late_chase: bool,
    opposing_force_ok: bool,
    conflict_market: bool,
    structural_extreme_reversal: bool,
) -> BookEntryProfile:
    if side not in {"BUY", "SELL"} or conflict_market:
        return "NO_TRADE"
    if (late_chase and not structural_extreme_reversal) or not opposing_force_ok:
        return "NO_TRADE"
    reaction_type = _upper(candle_reaction.get("reaction_type"))
    current_touch = _bool(significant_structure.get("current_touch"))
    significant = _bool(significant_structure.get("significant"))
    if (
        significant
        and current_touch
        and (inside_trigger or trendline_confluence or structural_extreme_reversal)
        and reaction_type in {"WICK_REJECTION", "RECLAIM_AFTER_SWEEP", "BODY_ACCEPTANCE"}
        and current_candle_ok
    ):
        return "AGGRESSIVE_SNIPER"
    if liquidity_sweep_detected and reaction_type in {"RECLAIM_AFTER_SWEEP", "WICK_REJECTION", "BODY_ACCEPTANCE"} and current_candle_ok:
        return "REVERSAL_RECLAIM"
    if (retest_confirmed or role_flip_confirmed) and current_candle_ok:
        return "CONSERVATIVE_RETEST"
    if continuation_confirmed and current_candle_ok and (retest_confirmed or break_of_structure_confirmed or structure_shift_confirmed):
        return "CONTINUATION_RETEST"
    if timing_supportive and current_candle_ok and significant:
        return "MOMENTUM_ACCEPTANCE"
    return "WATCH_ONLY"


def _strategy_combo(
    *,
    playbook: str,
    entry_profile: str,
    significant_structure: Mapping[str, Any],
    candle_reaction: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[str]:
    combo: list[str] = [playbook, entry_profile]
    structure_type = _upper(significant_structure.get("structure_type"))
    reaction_type = _upper(candle_reaction.get("reaction_type"))
    if structure_type and structure_type != "NO_SIGNIFICANT_STRUCTURE":
        combo.append(structure_type)
    if reaction_type and reaction_type != "NO_REACTION":
        combo.append(reaction_type)
    if _bool(evidence.get("liquidity_sweep_detected")):
        combo.append("LIQUIDITY_SWEEP")
    if _bool(evidence.get("break_of_structure_confirmed")):
        combo.append("BMS")
    if _bool(evidence.get("structure_shift_confirmed")):
        combo.append("SMS")
    if _bool(evidence.get("role_flip_confirmed")):
        combo.append("ROLE_FLIP")
    if _bool(evidence.get("trendline_confluence")):
        combo.append("TRENDLINE_CONFLUENCE")
    if _bool(evidence.get("retest_confirmed")):
        combo.append("RETEST_CONFIRMED")
    deduped: list[str] = []
    for item in combo:
        item_text = _upper(item)
        if item_text and item_text not in deduped:
            deduped.append(item_text)
    return deduped


def _build_blocker(field: str, received: Any, required: Any, reason: str, *, hard: bool) -> dict[str, Any]:
    return {
        "field": field,
        "received": received,
        "required": required,
        "reason": reason,
        "hard": bool(hard),
    }


def _select_playbook(
    *,
    side: str,
    lane_name: str,
    market_play: Mapping[str, Any],
    market_context: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> str:
    primary_play = _upper(market_play.get("primary_play") or market_play.get("play"))
    play_stage = _upper(market_play.get("play_stage") or market_context.get("play_stage"))
    if _bool(evidence.get("conflict_market")):
        return "CHOP_NO_TRADE"
    if _bool(evidence.get("failed_continuation_reversal")):
        return "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL" if side == "BUY" else "FAILED_BUY_INTO_SUPPLY_SELL_REVERSAL"
    if _bool(evidence.get("countertrend_scalp_only")):
        return "COUNTERTREND_SCALP_ONLY"
    if _bool(evidence.get("liquidity_sweep_detected")) and _bool(evidence.get("break_of_structure_confirmed")):
        return "SMC_SH_BMS_RTO" if _bool(evidence.get("retest_confirmed")) else "SMC_TURTLE_SOUP"
    if _bool(evidence.get("structure_shift_confirmed")) and _bool(evidence.get("break_of_structure_confirmed")):
        return "SMC_SMS_BMS_RTO"
    if _bool(evidence.get("liquidity_sweep_detected")) or _bool(evidence.get("false_breakout_risk")):
        return "SLINGSHOT_FALSE_BREAK"
    if _bool(evidence.get("role_flip_confirmed")):
        active_zone_type = _upper(evidence.get("active_zone_type"))
        if side == "BUY" and active_zone_type == "SUPPLY":
            return "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION"
        if side == "SELL" and active_zone_type == "DEMAND":
            return "FAILED_DEMAND_RECLAIM_SELL_CONTINUATION"
        return "SUPPLY_BREAK_RETEST_CONTINUATION" if side == "BUY" else "DEMAND_BREAK_RETEST_CONTINUATION"
    if lane_name == "FAILED_RETEST_ENTRY":
        return "SUPPLY_BREAK_RETEST_CONTINUATION" if side == "BUY" else "DEMAND_BREAK_RETEST_CONTINUATION"
    if lane_name in {"WAVE_RIDING_CONTINUATION", "LOCAL_BREAKDOWN_CONTINUATION", "MOMENTUM_ACCEPTANCE_ENTRY"}:
        if _bool(evidence.get("trendline_confluence")):
            return "TRENDLINE_CONFLUENCE_BOUNCE"
        return "DEMAND_BREAK_RETEST_CONTINUATION" if side == "BUY" else "SUPPLY_BREAK_RETEST_CONTINUATION"
    if _contains_any(primary_play, ("CHANNEL",)):
        return "CHANNEL_EDGE_REACTION"
    if _contains_any(primary_play, ("FIB", "OTE")):
        return "FIB_OTE_REACTION"
    if _contains_any(primary_play, ("PIVOT", "ROUND")):
        return "PIVOT_ROUND_NUMBER_REACTION"
    if _bool(evidence.get("inside_valid_trigger_zone")) or lane_name == "SNIPER_ZONE_ENTRY":
        return "DEMAND_REJECTION" if side == "BUY" else "SUPPLY_REJECTION"
    if _contains_any(play_stage, ("CANDLE", "CONFIRM")):
        return "CANDLE_CONFIRMATION_AT_ZONE"
    return "CANDLE_CONFIRMATION_AT_ZONE"


def _next_required_for_state(state: BookMaturityState, blockers: Sequence[Mapping[str, Any]], playbook: str) -> str:
    if state == "ENTER_NOW":
        return "publish validated PG_EXECUTION_PACKET_V3 after hard runtime gates pass"
    if blockers:
        first = blockers[0]
        reason = str(first.get("reason") or first.get("required") or "").strip()
        if reason:
            return reason
    if state == "EARLY_FORMING":
        return f"{playbook}: wait for price to reach a valid zone, trendline, or role-flip context"
    if state == "VALID_WATCH":
        return f"{playbook}: wait for retest/rejection/acceptance proof at the active area"
    if state == "PREPARE":
        return f"{playbook}: wait for current candle acceptance, timing readiness, and path room"
    if state == "LATE_CHASE":
        return f"{playbook}: skip chase; wait for pullback/retest or a new structure sequence"
    if state == "INVALIDATED":
        return f"{playbook}: candidate invalidated; wait for fresh structure"
    if state == "MISSED":
        return f"{playbook}: opportunity already moved; collect outcome evidence and wait"
    return "continue study"


def evaluate_book_strategy_master_v3(
    snapshot: Mapping[str, Any],
    *,
    market: Mapping[str, Any],
    candidate_side: str,
    execution_lane: Mapping[str, Any],
    timing_decision: Mapping[str, Any],
    current_candle: Mapping[str, Any],
    timing_mode: str,
    final_score_passed: bool,
    timing_enter_now: bool,
    lane_score: float,
    lane_required_score: float,
    bad_entry_filter: Mapping[str, Any] | None = None,
    bad_entry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply single-timeframe book-rule doctrine to already-detected V3 evidence.

    The playbook is the final decision authority. Model Council, timing, LSTM,
    memory, skills, and lane scoring are contributors; hard runtime freshness and
    packet validation still gate whether an ENTER_NOW decision may publish.
    """

    market_context = _first_mapping(market.get("market_context"), snapshot.get("market_context"))
    market_play = _first_mapping(market.get("market_play"), snapshot.get("market_play"))
    price_location = _first_mapping(market.get("price_location"), snapshot.get("price_location"))
    history_context = _first_mapping(market.get("history_context"), snapshot.get("historical_pattern"), snapshot.get("history_context"))
    zone_context = _first_mapping(snapshot.get("zone_liquidity"), market_context.get("zone_liquidity"))
    risk_context = _first_mapping(market.get("risk_context"), snapshot.get("risk_context"), snapshot.get("risk_opposing_force"))
    angle_context = _first_mapping(market.get("angle_context"), snapshot.get("angle_context"), snapshot.get("angle_features"))
    tracking_summary = _mapping(snapshot.get("tracking_summary"))
    candle_movement_context = _first_mapping(
        snapshot.get("candle_movement_context_v3"),
        snapshot.get("candle_movement_context"),
        market.get("candle_movement_context_v3"),
        market.get("candle_movement_context"),
        tracking_summary.get("candle_movement_context_v3"),
        tracking_summary.get("candle_movement_context"),
    )
    current_leg_context = _mapping(candle_movement_context.get("current_leg"))
    opposing_force_room_context = _mapping(
        candle_movement_context.get("opposing_force_room")
        or current_leg_context.get("opposing_force_room")
    )
    side = _side(candidate_side or market_context.get("dominant_side") or snapshot.get("candidate_side"))
    lane_name = _upper(execution_lane.get("name") or execution_lane.get("lane") or snapshot.get("selected_execution_lane"))
    lane_authority_ready = bool(_bool(execution_lane.get("accepted")) and lane_name in ENTER_NOW_LANES)
    zones = _first_rows(
        market.get("zones"),
        snapshot.get("zones"),
        snapshot.get("support_resistance_zones"),
        _mapping(snapshot.get("support_resistance_context")).get("zones"),
        _mapping(snapshot.get("smart_money_context")).get("zones"),
    )
    trendlines = _first_rows(
        market.get("trendlines"),
        snapshot.get("trendlines"),
        snapshot.get("trendlines_v3"),
        _mapping(snapshot.get("trendline_context")).get("trendlines"),
        _mapping(snapshot.get("overlay_geometry")).get("trendlines"),
    )
    active_zone = _nearest_zone_for_side(zones, side)
    opposing_zone = _opposing_zone_for_side(zones, side)
    active_trendline = _best_trendline_for_side(trendlines, side)
    timing_entry = _mapping(timing_decision.get("entry_timing"))
    live_trigger_reaction = _mapping(execution_lane.get("live_trigger_reaction"))
    execution_timing = _first_mapping(
        snapshot.get("execution_timing"),
        snapshot.get("timing_signal"),
        _mapping(snapshot.get("latest_signal")).get("execution_timing"),
        _mapping(snapshot.get("tracking_summary")).get("execution_timing"),
    )
    reasoning_bad_entry = _mapping(bad_entry_filter)
    market_bad_entry = _first_mapping(bad_entry, market.get("bad_entry"), snapshot.get("bad_entry"))
    bad_entry_class = _upper(
        reasoning_bad_entry.get("class")
        or reasoning_bad_entry.get("class_id")
        or market_bad_entry.get("class")
        or market_bad_entry.get("class_id")
        or market_bad_entry.get("bad_entry_class")
        or market_bad_entry.get("reason")
    )
    bad_entry_active = bool(
        _bool(reasoning_bad_entry.get("active"))
        or _bool(market_bad_entry.get("detected"))
        or _clip01(reasoning_bad_entry.get("severity"), 0.0) >= 0.72
    )
    raw_playbook_hard_bad_entry = bool(bad_entry_active and bad_entry_class in PLAYBOOK_HARD_BAD_ENTRY_CLASSES)
    raw_playbook_late_chase_bad_entry = bool(bad_entry_active and bad_entry_class in PLAYBOOK_LATE_CHASE_BAD_ENTRY_CLASSES)
    late_chase_bad_entry_class = bool(bad_entry_class in PLAYBOOK_LATE_CHASE_BAD_ENTRY_CLASSES)
    measured_reaction_accepted = bool(
        _bool(live_trigger_reaction.get("accepted"))
        or (
            _contains_any(execution_timing.get("timing_class") or execution_timing.get("class"), ("MEASURED_REACTION", "OPPOSING_FORCE_REACTION", "SNIPER_REACTION"))
            and _bool(execution_timing.get("entry_allowed") or execution_timing.get("timing_entry_allowed"))
        )
    )
    measured_reaction_overrides_stale_late_chase = bool(
        measured_reaction_accepted
        and bad_entry_class in {"LATE_CHASE", "LATE_CHASE_AFTER_IMPULSE", "LATE_CHASE_STEEP_IMPULSE"}
    )
    playbook_hard_bad_entry = bool(raw_playbook_hard_bad_entry and not measured_reaction_overrides_stale_late_chase)
    playbook_late_chase_bad_entry = bool(raw_playbook_late_chase_bad_entry and not measured_reaction_overrides_stale_late_chase)
    current_candle_ok = _bool(current_candle.get("entry_allowed") or current_candle.get("accepted"))
    inside_trigger = _bool(
        market_context.get("inside_valid_trigger_zone")
        or zone_context.get("inside_valid_trigger_zone")
        or snapshot.get("inside_valid_trigger_zone")
        or (active_zone and _zone_current_price_inside(active_zone))
    )
    continuation_confirmed = _bool(
        market_context.get("is_continuation_confirmed")
        or snapshot.get("continuation_confirmed")
        or snapshot.get("current_flow_continuation_ready")
        or execution_lane.get("structural_flow_ready")
        or execution_lane.get("mature_directional_flow_ready")
        or (_bool(execution_lane.get("accepted")) and lane_name in {"LOCAL_BREAKDOWN_CONTINUATION", "WAVE_RIDING_CONTINUATION", "HISTORY_MATCHED_CONTINUATION", "MOMENTUM_ACCEPTANCE_ENTRY"})
    )
    pullback_confirmed = _bool(snapshot.get("pullback_confirmed") or snapshot.get("pullback_reclaim_ready"))
    retest_confirmed = _bool(
        snapshot.get("retest_confirmed")
        or snapshot.get("failed_retest_confirmed")
        or execution_lane.get("reversal_capture_mature")
        or pullback_confirmed
    )
    break_of_structure_confirmed = _bool(
        snapshot.get("break_of_structure_confirmed")
        or snapshot.get("bms_confirmed")
        or _contains_any(market_play.get("primary_play"), ("BMS", "BREAK_OF_STRUCTURE", "BREAKOUT", "BREAK"))
    )
    structure_shift_confirmed = _bool(
        snapshot.get("structure_shift_confirmed")
        or snapshot.get("sms_confirmed")
        or _contains_any(market_play.get("primary_play"), ("SMS", "MARKET_STRUCTURE_SHIFT"))
    )
    liquidity_sweep_detected = _bool(
        snapshot.get("liquidity_sweep_detected")
        or zone_context.get("liquidity_sweep_detected")
        or _mapping(snapshot.get("smart_money_context")).get("liquidity_sweep_detected")
        or _contains_any(market_play.get("primary_play"), ("SWEEP", "TURTLE", "STOP_HUNT"))
    )
    role_flip_confirmed = _bool(
        snapshot.get("role_flip_confirmed")
        or market_context.get("role_flip_confirmed")
        or any(_zone_role_flip_confirmed(zone) for zone in zones)
    )
    false_breakout_risk = _bool(
        market_context.get("false_breakout_risk")
        or snapshot.get("false_breakout_risk")
        or _contains_any(market_play.get("primary_play"), ("FALSE_BREAK", "SLINGSHOT"))
    )
    trendline_confluence = _bool(
        snapshot.get("trendline_confluence")
        or snapshot.get("trendline_touch_confirmed")
        or _rows(snapshot.get("trendlines_v3"))
        or _trendline_touching_now(active_trendline)
        or lane_name in {"WAVE_RIDING_CONTINUATION", "LOCAL_BREAKDOWN_CONTINUATION"}
    )
    opposing_force_ok = _bool(
        market_context.get("opposing_force_distance_ok")
        or risk_context.get("distance_ok")
        or execution_lane.get("opposing_force_ok")
    )
    if "room_ok" in opposing_force_room_context:
        opposing_force_ok = bool(_bool(opposing_force_room_context.get("room_ok")))
    movement_stage = _upper(candle_movement_context.get("move_stage") or current_leg_context.get("move_stage"), "UNKNOWN")
    current_leg_side = _side(current_leg_context.get("side"))
    current_leg_candle_count = _int(current_leg_context.get("candle_count"), 0)
    visible_candle_count = _int(candle_movement_context.get("visible_candle_count"), 0)
    estimated_candles_to_force = _int(opposing_force_room_context.get("estimated_candles_to_force"), 0)
    same_side_current_leg = bool(side in {"BUY", "SELL"} and current_leg_side == side)
    movement_context_present = bool(candle_movement_context)
    movement_stage_late = movement_stage in {"LATE", "EXHAUSTED"}
    movement_stage_reclaiming = movement_stage in {"EARLY", "STILL_RECLAIMING"}
    movement_stage_mature_with_room = bool(movement_stage == "MATURE" and opposing_force_ok)
    current_leg_extended = bool(same_side_current_leg and current_leg_candle_count >= 14)
    current_leg_exhausted = bool(
        same_side_current_leg
        and (
            movement_stage_late
            or current_leg_candle_count >= 18
            or (estimated_candles_to_force > 0 and estimated_candles_to_force <= 2 and current_leg_candle_count >= 6)
        )
    )
    raw_late_chase = _bool(
        market_context.get("is_late_chase")
        or snapshot.get("late_chase")
        or angle_context.get("late_chase_risk")
        or current_candle.get("too_late")
    )
    preliminary_late_chase = bool((raw_late_chase and not measured_reaction_accepted) or playbook_late_chase_bad_entry)
    history_exit_here = _bool(
        market_context.get("history_would_exit_here")
        or history_context.get("would_have_exited_here")
        or history_context.get("history_would_exit_here")
    )
    conflict_market = _bool(market_context.get("conflict_market") or snapshot.get("conflict_market"))
    pullback_not_confirmed = _bool(market_context.get("pullback_not_confirmed") or snapshot.get("pullback_not_confirmed"))
    middle_safe = _bool(
        market_context.get("middle_safe")
        or price_location.get("middle_safe")
        or _upper(market_context.get("current_location")) == "MIDDLE_SAFE"
    )
    candidate_invalidated = _bool(
        snapshot.get("candidate_invalidated")
        or snapshot.get("previous_side_invalidated")
        or snapshot.get("confirmed_reversal")
        or market_context.get("candidate_invalidated")
    )
    global_structure = _mapping(snapshot.get("global_structure"))
    local_micro_structure = _mapping(snapshot.get("local_micro_structure"))
    wave_context = _first_mapping(
        snapshot.get("wave_context"),
        snapshot.get("wave_riding_context"),
        market_context.get("wave_context"),
        market.get("wave_context"),
    )
    global_side = _first_side(
        snapshot.get("global_side"),
        market_context.get("global_side"),
        global_structure.get("global_side"),
        global_structure.get("side"),
        market_play.get("global_context"),
    )
    local_side = _first_side(
        snapshot.get("local_side"),
        market_context.get("local_side"),
        local_micro_structure.get("local_side"),
        local_micro_structure.get("side"),
        wave_context.get("local_side"),
        wave_context.get("wave_side"),
        wave_context.get("current_wave_side"),
    )
    dominant_side = _first_side(
        market_context.get("dominant_side"),
        snapshot.get("dominant_side"),
        market_play.get("side_bias"),
        global_side,
        local_side,
    )
    primary_bias_side = global_side if global_side in {"BUY", "SELL"} else dominant_side if dominant_side in {"BUY", "SELL"} else local_side
    countertrend_against_global = bool(global_side in {"BUY", "SELL"} and side == _opposite_side(global_side))
    countertrend_against_local = bool(local_side in {"BUY", "SELL"} and side == _opposite_side(local_side))
    countertrend_against_primary = bool(primary_bias_side in {"BUY", "SELL"} and side == _opposite_side(primary_bias_side))
    aligned_with_primary_bias = bool(side in {"BUY", "SELL"} and primary_bias_side in {"BUY", "SELL"} and side == primary_bias_side)
    short_horizon_side, short_horizon_probability = _short_horizon_side(snapshot)
    price_location_label = _upper(price_location.get("relative_location") or market_context.get("current_location"))
    structural_extreme_for_side = _location_supports_side(side, price_location_label, active_zone)
    opposite_pressure_warning = bool(short_horizon_side == side and short_horizon_probability >= 0.52)
    explicit_reversal_play_hint = bool(
        _contains_any(market_play.get("primary_play"), ("FAILED_SELL", "FAILED_BUY", "REVERSAL", "SWEEP", "STOP_HUNT", "TURTLE"))
        or _contains_any(market_play.get("play_stage"), ("REVERSAL", "AGGRESSIVE_REVERSAL", "SWEEP"))
    )
    failed_continuation_reversal = bool(
        side in {"BUY", "SELL"}
        and structural_extreme_for_side
        and current_candle_ok
        and (inside_trigger or measured_reaction_accepted or liquidity_sweep_detected or opposite_pressure_warning)
        and (
            countertrend_against_global
            or countertrend_against_primary
            or explicit_reversal_play_hint
        )
        and not aligned_with_primary_bias
    )
    late_chase = preliminary_late_chase
    late_chase_softened_by_extreme_reversal = False
    late_chase_softened_by_book_reaction = False
    book_reaction_overrides_late_chase_bad_entry = False
    countertrend_reversal_override = bool(
        role_flip_confirmed
        or structure_shift_confirmed
        or (break_of_structure_confirmed and retest_confirmed)
        or (liquidity_sweep_detected and (retest_confirmed or current_candle_ok or measured_reaction_accepted))
        or failed_continuation_reversal
    )
    timing_waiting = bool(_upper(timing_mode) != "ENTER_NOW" or not timing_enter_now)
    local_counter_without_reclaim = bool(countertrend_against_local and not countertrend_reversal_override)
    primary_counter_without_reclaim = bool(countertrend_against_primary and not countertrend_reversal_override)
    weak_countertrend_conditions = bool(timing_waiting or not final_score_passed or not lane_authority_ready)
    countertrend_scalp_only = bool(
        side in {"BUY", "SELL"}
        and (primary_counter_without_reclaim or local_counter_without_reclaim)
        and weak_countertrend_conditions
    )
    large_move_bias_aligned = bool(
        side in {"BUY", "SELL"}
        and not countertrend_scalp_only
        and (aligned_with_primary_bias or countertrend_reversal_override)
    )
    if side not in {"BUY", "SELL"}:
        bias_alignment = "NO_DIRECTION"
    elif countertrend_scalp_only:
        bias_alignment = "COUNTERTREND_SCALP_ONLY"
    elif countertrend_reversal_override and (countertrend_against_global or countertrend_against_local or countertrend_against_primary):
        bias_alignment = "REVERSAL_OVERRIDE"
    elif aligned_with_primary_bias:
        bias_alignment = "PRIMARY_BIAS_ALIGNED"
    elif countertrend_against_global or countertrend_against_local or countertrend_against_primary:
        bias_alignment = "COUNTERTREND_WATCH"
    else:
        bias_alignment = "BIAS_UNRESOLVED"
    live_integrity = _mapping(snapshot.get("live_integrity") or market.get("live_integrity"))
    runtime_model_health = _mapping(snapshot.get("runtime_model_health") or market.get("runtime_model_health") or snapshot.get("model_health"))
    api_health = _mapping(snapshot.get("api_health") or market.get("api_health"))
    candlestick_context = _first_mapping(
        current_candle,
        snapshot.get("candlestick_context"),
        snapshot.get("latest_candle"),
        snapshot.get("current_candle"),
        snapshot.get("current_candle_contract"),
        _mapping(snapshot.get("latest_signal")).get("candlestick_context"),
        _mapping(snapshot.get("tracking_summary")).get("candlestick_context"),
    )
    significant_structure = _select_significant_structure(
        side=side,
        active_zone=active_zone,
        trendline=active_trendline,
        inside_trigger=inside_trigger,
        trendline_confluence=trendline_confluence,
        role_flip_confirmed=role_flip_confirmed,
        retest_confirmed=retest_confirmed,
        continuation_confirmed=continuation_confirmed,
        break_of_structure_confirmed=break_of_structure_confirmed,
        liquidity_sweep_detected=liquidity_sweep_detected,
    )
    candle_reaction = _classify_candlestick_reaction(
        candlestick_context,
        side=side,
        current_candle_ok=current_candle_ok,
        inside_trigger=inside_trigger,
        retest_confirmed=retest_confirmed,
        continuation_confirmed=continuation_confirmed,
        liquidity_sweep_detected=liquidity_sweep_detected,
    )
    reaction_type = _upper(candle_reaction.get("reaction_type"))
    significant_structure_touch = bool(
        _bool(significant_structure.get("significant"))
        and (
            _bool(significant_structure.get("current_touch"))
            or inside_trigger
            or trendline_confluence
            or retest_confirmed
            or role_flip_confirmed
            or liquidity_sweep_detected
        )
    )
    book_valid_reaction = bool(
        side in {"BUY", "SELL"}
        and current_candle_ok
        and opposing_force_ok
        and reaction_type not in {"", "NO_REACTION", "EXHAUSTION"}
        and (
            significant_structure_touch
            or continuation_confirmed
            or break_of_structure_confirmed
            or structure_shift_confirmed
            or measured_reaction_accepted
            or failed_continuation_reversal
            or lane_name in ENTER_NOW_LANES
        )
    )
    movement_supports_book_reaction = bool(
        failed_continuation_reversal
        or (
            movement_context_present
            and (
                movement_stage_reclaiming
                or movement_stage_mature_with_room
                or (not same_side_current_leg and current_leg_candle_count <= 6)
            )
        )
    )
    current_move_true_extension = bool(
        not movement_context_present
        or current_leg_exhausted
        or (same_side_current_leg and current_leg_extended and not movement_stage_reclaiming)
        or (movement_stage_late and not book_valid_reaction)
    )
    book_reaction_overrides_late_chase_bad_entry = bool(
        late_chase_bad_entry_class
        and book_valid_reaction
        and movement_supports_book_reaction
        and not current_leg_exhausted
    )
    late_chase_softened_by_extreme_reversal = bool(
        preliminary_late_chase
        and failed_continuation_reversal
        and opposing_force_ok
        and not current_leg_exhausted
        and not (raw_playbook_hard_bad_entry and not late_chase_bad_entry_class)
    )
    late_chase_softened_by_book_reaction = bool(
        preliminary_late_chase
        and book_valid_reaction
        and movement_supports_book_reaction
        and not current_leg_exhausted
        and not (raw_playbook_hard_bad_entry and not late_chase_bad_entry_class)
    )
    late_chase_reaction_override_allowed = bool(
        measured_reaction_overrides_stale_late_chase
        or late_chase_softened_by_extreme_reversal
        or late_chase_softened_by_book_reaction
        or book_reaction_overrides_late_chase_bad_entry
    )
    playbook_hard_bad_entry = bool(
        raw_playbook_hard_bad_entry
        and not (late_chase_bad_entry_class and late_chase_reaction_override_allowed)
    )
    playbook_late_chase_bad_entry = bool(
        raw_playbook_late_chase_bad_entry
        and not late_chase_reaction_override_allowed
    )
    late_chase = bool(
        preliminary_late_chase
        and current_move_true_extension
        and not late_chase_reaction_override_allowed
    )
    play_evidence: dict[str, Any] = {
        "side": side,
        "lane": lane_name,
        "single_timeframe_mode": True,
        "multiple_timeframe_required": False,
        "visible_major_local_scaling": True,
        "inside_valid_trigger_zone": inside_trigger,
        "current_candle_ok": current_candle_ok,
        "timing_mode": _upper(timing_mode),
        "timing_enter_now": bool(timing_enter_now),
        "lane_authority_ready": lane_authority_ready,
        "measured_reaction_accepted": measured_reaction_accepted,
        "final_score_passed": bool(final_score_passed),
        "lane_score": round(float(lane_score), 4),
        "lane_required_score": round(float(lane_required_score), 4),
        "continuation_confirmed": continuation_confirmed,
        "pullback_confirmed": pullback_confirmed,
        "retest_confirmed": retest_confirmed,
        "break_of_structure_confirmed": break_of_structure_confirmed,
        "structure_shift_confirmed": structure_shift_confirmed,
        "liquidity_sweep_detected": liquidity_sweep_detected,
        "role_flip_confirmed": role_flip_confirmed,
        "false_breakout_risk": false_breakout_risk,
        "trendline_confluence": trendline_confluence,
        "opposing_force_ok": opposing_force_ok,
        "late_chase": late_chase,
        "raw_late_chase": raw_late_chase,
        "preliminary_late_chase": preliminary_late_chase,
        "current_move_true_extension": current_move_true_extension,
        "late_chase_softened_by_extreme_reversal": late_chase_softened_by_extreme_reversal,
        "late_chase_softened_by_book_reaction": late_chase_softened_by_book_reaction,
        "book_valid_reaction": book_valid_reaction,
        "book_reaction_overrides_late_chase_bad_entry": book_reaction_overrides_late_chase_bad_entry,
        "movement_context_present": movement_context_present,
        "movement_stage": movement_stage,
        "movement_stage_reclaiming": movement_stage_reclaiming,
        "movement_stage_mature_with_room": movement_stage_mature_with_room,
        "current_leg_side": current_leg_side,
        "current_leg_candle_count": current_leg_candle_count,
        "current_leg_extended": current_leg_extended,
        "current_leg_exhausted": current_leg_exhausted,
        "visible_candle_count": visible_candle_count,
        "estimated_candles_to_force": estimated_candles_to_force,
        "candle_movement_summary": candle_movement_context.get("summary"),
        "candle_movement_context_v3": candle_movement_context,
        "history_exit_here": history_exit_here,
        "conflict_market": conflict_market,
        "pullback_not_confirmed": pullback_not_confirmed,
        "middle_safe": middle_safe,
        "short_horizon_side": short_horizon_side,
        "short_horizon_probability": round(float(short_horizon_probability), 4),
        "structural_extreme_for_side": structural_extreme_for_side,
        "opposite_pressure_warning": opposite_pressure_warning,
        "failed_continuation_reversal": failed_continuation_reversal,
        "global_side": global_side,
        "local_side": local_side,
        "dominant_side": dominant_side,
        "primary_bias_side": primary_bias_side,
        "bias_alignment": bias_alignment,
        "aligned_with_primary_bias": aligned_with_primary_bias,
        "countertrend_against_global": countertrend_against_global,
        "countertrend_against_local": countertrend_against_local,
        "countertrend_against_primary": countertrend_against_primary,
        "countertrend_reversal_override": countertrend_reversal_override,
        "countertrend_scalp_only": countertrend_scalp_only,
        "large_move_bias_aligned": large_move_bias_aligned,
        "active_zone_id": str(active_zone.get("zone_id") or active_zone.get("id") or active_zone.get("key") or ""),
        "active_zone_type": _zone_type(active_zone) if active_zone else "",
        "opposing_zone_id": str(opposing_zone.get("zone_id") or opposing_zone.get("id") or opposing_zone.get("key") or ""),
        "opposing_zone_type": _zone_type(opposing_zone) if opposing_zone else "",
        "active_trendline_id": str(active_trendline.get("trendline_id") or active_trendline.get("id") or active_trendline.get("key") or ""),
        "active_trendline_role": _trendline_role(active_trendline) if active_trendline else "",
        "significant_structure": significant_structure,
        "candlestick_reaction": candle_reaction,
        "primary_market_play": _upper(market_play.get("primary_play")),
        "play_stage": _upper(market_play.get("play_stage")),
        "price_location": _upper(price_location.get("relative_location") or market_context.get("current_location")),
        "live_integrity_present": bool(live_integrity),
        "runtime_model_health_present": bool(runtime_model_health),
        "api_health_present": bool(api_health),
    }
    playbook = _select_playbook(
        side=side,
        lane_name=lane_name,
        market_play=market_play,
        market_context=market_context,
        evidence=play_evidence,
    )
    blockers: list[dict[str, Any]] = []
    soft_warnings: list[dict[str, Any]] = []

    def add_blocker(field: str, received: Any, required: Any, reason: str, *, hard: bool = False) -> None:
        blockers.append(_build_blocker(field, received, required, reason, hard=hard))

    def add_warning(field: str, received: Any, effect: str) -> None:
        soft_warnings.append({"field": field, "received": received, "effect": effect})

    measured_reaction_can_override_timing = bool(
        measured_reaction_accepted
        and not countertrend_scalp_only
        and (large_move_bias_aligned or countertrend_reversal_override or lane_authority_ready)
    )
    timing_supportive = bool(
        (_upper(timing_mode) == "ENTER_NOW" and timing_enter_now)
        or measured_reaction_can_override_timing
        or (
            current_candle_ok
            and not countertrend_scalp_only
            and (inside_trigger or retest_confirmed or continuation_confirmed or failed_continuation_reversal)
        )
    )
    play_evidence["measured_reaction_can_override_timing"] = measured_reaction_can_override_timing
    play_evidence["timing_supportive"] = timing_supportive
    score_supportive = bool(final_score_passed or lane_score >= max(0.55, lane_required_score * 0.80))
    entry_profile = _select_entry_profile(
        side=side,
        significant_structure=significant_structure,
        candle_reaction=candle_reaction,
        inside_trigger=inside_trigger,
        trendline_confluence=trendline_confluence,
        retest_confirmed=retest_confirmed,
        role_flip_confirmed=role_flip_confirmed,
        continuation_confirmed=continuation_confirmed,
        break_of_structure_confirmed=break_of_structure_confirmed,
        structure_shift_confirmed=structure_shift_confirmed,
        liquidity_sweep_detected=liquidity_sweep_detected,
        timing_supportive=timing_supportive,
        current_candle_ok=current_candle_ok,
        late_chase=late_chase,
        opposing_force_ok=opposing_force_ok,
        conflict_market=conflict_market,
        structural_extreme_reversal=failed_continuation_reversal,
    )
    strategy_combo = _strategy_combo(
        playbook=playbook,
        entry_profile=entry_profile,
        significant_structure=significant_structure,
        candle_reaction=candle_reaction,
        evidence=play_evidence,
    )
    play_evidence["entry_profile"] = entry_profile
    play_evidence["strategy_combo"] = strategy_combo

    if live_integrity and _explicit_false(live_integrity, "is_live", "frame_advancing", "capture_advancing", "state_advancing"):
        add_blocker(
            "live_integrity",
            live_integrity,
            "fresh advancing live frame/capture/state",
            "Live source truth is not advancing cleanly.",
            hard=True,
        )
    if live_integrity and _upper(live_integrity.get("cache_status") or live_integrity.get("cache_state")) in {"STALE", "EXPIRED", "DIRTY"}:
        add_blocker(
            "cache_state",
            live_integrity.get("cache_status") or live_integrity.get("cache_state"),
            "fresh",
            "Live cache state is not acceptable for strategy authority.",
            hard=True,
        )
    if runtime_model_health and _explicit_false(runtime_model_health, "all_required_models_awake", "models_awake"):
        add_blocker(
            "runtime_model_health",
            runtime_model_health,
            "models awake",
            "Required model contributors are not awake.",
            hard=True,
        )
    if api_health and _upper(api_health.get("status") or api_health.get("state") or api_health.get("health")) in {"DOWN", "ERROR", "FAILED", "UNHEALTHY"}:
        add_blocker(
            "api_health",
            api_health.get("status") or api_health.get("state") or api_health.get("health"),
            "healthy",
            "API health is not acceptable for strategy authority.",
            hard=True,
        )
    if side not in {"BUY", "SELL"}:
        add_blocker("candidate_side", side, "BUY or SELL", "No directional opportunity exists.", hard=True)
    if candidate_invalidated:
        add_blocker("candidate_invalidated", True, False, "Candidate was invalidated by current structure.", hard=True)
    if conflict_market:
        add_blocker("conflict_market", True, False, "BUY and SELL evidence conflict; do not force a trade.")
    if late_chase:
        late_reason = (
            f"True late chase: current {current_leg_side or side} leg has {current_leg_candle_count} candle(s), "
            f"stage={movement_stage}, room_to_force={estimated_candles_to_force or 'unknown'} candle(s); "
            "wait for pullback/retest/reclaim or fresh structure reaction."
        )
        add_blocker("late_chase", True, False, late_reason, hard=True)
    elif late_chase_softened_by_extreme_reversal:
        add_warning(
            "late_chase",
            "FIRST_REJECTION_FROM_EXTREME",
            "late-chase warning softened because current price rejected a structural extreme in the opposite direction",
        )
    elif late_chase_softened_by_book_reaction:
        add_warning(
            "late_chase",
            "BOOK_VALID_REACTION",
            "late-chase warning softened because candle movement, structure touch, reaction, and path room support an active book entry",
        )
    if playbook_hard_bad_entry:
        add_blocker(
            "bad_entry_filter.class",
            bad_entry_class,
            "no buy-high/sell-low/late-chase/path-room violation",
            "The playbook rejects this entry location; wait for a cleaner pullback, retest, or path-room reset.",
            hard=True,
        )
    if countertrend_scalp_only:
        add_blocker(
            "bias_alignment",
            bias_alignment,
            "primary-bias aligned entry or confirmed reclaim/role-flip reversal",
            "Local reaction is only a minor countertrend/scalp read; wait for bias-aligned continuation or a confirmed reclaim/role flip.",
        )
    elif (countertrend_against_global or countertrend_against_local or countertrend_against_primary) and not countertrend_reversal_override:
        add_warning(
            "bias_alignment",
            bias_alignment,
            "countertrend read remains watch-only until reclaim, role flip, or major structure invalidation confirms adaptation",
        )
    if history_exit_here:
        add_blocker("history_exit_here", True, False, "Historical analog says this area is closer to exit/protection than entry.")
    if not opposing_force_ok:
        add_blocker("opposing_force_ok", False, True, "Nearest opposing force is too close for a clean path.", hard=True)
    if pullback_not_confirmed and playbook in {"SMC_SH_BMS_RTO", "SMC_SMS_BMS_RTO", "TRENDLINE_BREAK_RETEST"}:
        add_warning("pullback_or_retest", "missing", "conservative_entry_waits_for_retest_confirmation")
    if false_breakout_risk and playbook not in {"SMC_TURTLE_SOUP", "SLINGSHOT_FALSE_BREAK"}:
        add_warning("false_breakout_risk", True, "confidence_reduced_until_sweep_direction_resolves")
    if not inside_trigger and not (role_flip_confirmed or continuation_confirmed or trendline_confluence or lane_authority_ready):
        add_warning(
            "active_location",
            "outside_trigger",
            "watch_until_price_reaches_zone_role_flip_trendline_or_continuation_context",
        )
    if not current_candle_ok:
        add_warning("current_candle.entry_allowed", False, "watch_until_rejection_acceptance_retest_or_continuation_candle_appears")
    if not timing_supportive:
        add_warning(
            "timing_mode",
            _upper(timing_mode, "UNKNOWN"),
            str(timing_entry.get("next_condition") or "timing contributor has not reached immediate-entry mode"),
        )
    if not score_supportive:
        add_warning(
            "final_execution_score",
            round(float(lane_score), 4),
            f"confidence_reduced_until_score >= {max(0.55, lane_required_score * 0.80):.4f}",
        )

    has_context = bool(
        side in {"BUY", "SELL"}
        and (
            _bool(significant_structure.get("significant"))
            or inside_trigger
            or continuation_confirmed
            or role_flip_confirmed
            or trendline_confluence
            or break_of_structure_confirmed
            or middle_safe
            or failed_continuation_reversal
            or lane_name in ENTER_NOW_LANES
        )
    )
    profile_executable = entry_profile in {
        "AGGRESSIVE_SNIPER",
        "CONSERVATIVE_RETEST",
        "CONTINUATION_RETEST",
        "REVERSAL_RECLAIM",
        "MOMENTUM_ACCEPTANCE",
    }
    reaction_ready = bool(
        current_candle_ok
        and profile_executable
        and (
            inside_trigger
            or retest_confirmed
            or role_flip_confirmed
            or continuation_confirmed
            or trendline_confluence
            or break_of_structure_confirmed
            or structure_shift_confirmed
            or liquidity_sweep_detected
            or measured_reaction_accepted
            or failed_continuation_reversal
            or _upper(candle_reaction.get("reaction_type")) not in {"", "NO_REACTION", "EXHAUSTION"}
            or lane_name in ENTER_NOW_LANES
        )
    )
    hard_blockers = [row for row in blockers if _bool(row.get("hard"))]
    if side not in {"BUY", "SELL"}:
        state: BookMaturityState = "NO_OPPORTUNITY"
    elif candidate_invalidated:
        state = "INVALIDATED"
    elif late_chase or history_exit_here:
        state = "LATE_CHASE"
    elif conflict_market:
        state = "NO_OPPORTUNITY"
    elif hard_blockers:
        state = "PREPARE"
    elif not has_context:
        state = "EARLY_FORMING"
    elif entry_profile == "WATCH_ONLY" or not reaction_ready:
        state = "VALID_WATCH"
    elif blockers:
        state = "PREPARE"
    elif bool(reaction_ready and timing_supportive and not hard_blockers):
        state = "ENTER_NOW"
    else:
        state = "PREPARE"

    evidence_score = 0.0
    evidence_score += 0.12 if inside_trigger else 0.0
    evidence_score += 0.12 if current_candle_ok else 0.0
    evidence_score += 0.10 if retest_confirmed else 0.0
    evidence_score += 0.10 if continuation_confirmed else 0.0
    evidence_score += 0.08 if role_flip_confirmed else 0.0
    evidence_score += 0.08 if trendline_confluence else 0.0
    evidence_score += 0.08 if break_of_structure_confirmed or structure_shift_confirmed else 0.0
    evidence_score += 0.10 if failed_continuation_reversal else 0.0
    evidence_score += 0.08 if _bool(significant_structure.get("significant")) else 0.0
    evidence_score += 0.10 * _clip01(candle_reaction.get("reaction_quality"), 0.0)
    evidence_score += 0.06 if entry_profile in {"AGGRESSIVE_SNIPER", "CONSERVATIVE_RETEST", "CONTINUATION_RETEST", "REVERSAL_RECLAIM"} else 0.0
    evidence_score += 0.10 if large_move_bias_aligned else 0.0
    evidence_score += 0.10 if opposing_force_ok else -0.08
    evidence_score += 0.12 if final_score_passed else -0.08
    evidence_score += 0.10 if timing_enter_now and _upper(timing_mode) == "ENTER_NOW" else -0.05
    evidence_score -= 0.16 if countertrend_scalp_only else 0.0
    evidence_score -= 0.18 if late_chase else 0.0
    evidence_score -= 0.14 if history_exit_here else 0.0
    evidence_score -= 0.12 if conflict_market else 0.0
    confidence = _clip01(0.25 + evidence_score - (0.10 * len(hard_blockers)) - (0.025 * max(0, len(blockers) - len(hard_blockers))))
    if state == "ENTER_NOW" and not countertrend_scalp_only:
        confidence = max(confidence, min(0.99, _clip01(lane_score, 0.0)))
    next_required = _next_required_for_state(state, blockers, playbook)
    if state != "ENTER_NOW" and not blockers and soft_warnings:
        next_required = str(soft_warnings[0].get("effect") or next_required)
    denied_at = "NONE" if state == "ENTER_NOW" else str(blockers[0]["field"] if blockers else state).upper().replace(".", "_")
    playbook_signal = side if state == "ENTER_NOW" and side in {"BUY", "SELL"} else "HOLD"
    watch_conditions = [str(row.get("effect") or row.get("field") or "") for row in soft_warnings if str(row.get("effect") or row.get("field") or "").strip()]
    strategy_read = {
        "headline": f"{side} {playbook.replace('_', ' ').title()}" if side in {"BUY", "SELL"} else "No valid directional play",
        "maturity": state,
        "side": side,
        "signal": playbook_signal,
        "playbook": playbook,
        "entry_profile": entry_profile,
        "reaction_type": candle_reaction.get("reaction_type"),
        "structure_type": significant_structure.get("structure_type"),
        "strategy_combo": strategy_combo,
        "confidence": round(float(confidence), 4),
        "next_required": next_required,
        "watch_conditions": watch_conditions,
        "doctrine": "single_timeframe_visible_history_only",
        "execution_authority": BOOK_STRATEGY_EXECUTION_AUTHORITY,
        "model_council_role": MODEL_COUNCIL_CONTRIBUTOR_ROLE,
    }
    return {
        "schema_version": BOOK_STRATEGY_SCHEMA_VERSION,
        "source": "book_strategy_master_v3",
        "execution_authority": BOOK_STRATEGY_EXECUTION_AUTHORITY,
        "final_decider": True,
        "model_council_role": MODEL_COUNCIL_CONTRIBUTOR_ROLE,
        "playbook": playbook,
        "maturity_state": state,
        "state": state,
        "side": side,
        "playbook_signal": playbook_signal,
        "entry_profile": entry_profile,
        "reaction_type": candle_reaction.get("reaction_type"),
        "significant_structure": significant_structure,
        "candlestick_reaction": candle_reaction,
        "strategy_combo": strategy_combo,
        "watch_conditions": watch_conditions,
        "confidence": round(float(confidence), 4),
        "denied_at": denied_at,
        "next_required": next_required,
        "hard_blockers": hard_blockers,
        "blockers": blockers,
        "soft_warnings": soft_warnings,
        "evidence": play_evidence,
        "strategy_read": strategy_read,
        "single_timeframe_mode": True,
        "multiple_timeframe_required": False,
        "rules_applied": [
            "observation_is_not_execution",
            "only_fresh_v3_packet_executes",
            "zones_are_reaction_areas_not_entries",
            "after_bms_wait_for_retracement",
            "avoid_late_chase",
            "late_chase_requires_true_extension_no_room_or_no_fresh_reaction",
            "aggressive_entries_require_structure_touch_candle_reaction_and_path_room",
            "conservative_entries_require_retest_reclaim_or_role_flip",
            "avoid_opposing_force_without_path_room",
            "major_visible_bias_dominates_inner_reactions",
            "countertrend_scalps_require_reclaim_role_flip_or_major_invalidation",
            "do_not_require_multiple_timeframes",
        ],
    }


__all__ = [
    "BOOK_ENTRY_PROFILES",
    "BOOK_REACTION_TYPES",
    "BOOK_STRATEGY_MATURITY_STATES",
    "BOOK_STRATEGY_PLAYBOOKS",
    "BOOK_STRATEGY_SCHEMA_VERSION",
    "evaluate_book_strategy_master_v3",
]
