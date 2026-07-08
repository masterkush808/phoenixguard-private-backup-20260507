from __future__ import annotations

from math import isfinite
from typing import Any, Literal, Mapping, Sequence, cast

from phoenixguard.decision.astar_decision_state_v3 import build_candidate_decision_ledger_v3


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

MarketPhaseV3 = Literal[
    "BUY_TREND",
    "BUY_IN_BUY_CONTINUATION",
    "SELL_IN_BUY_PULLBACK",
    "SELL_IN_BUY_DISTRIBUTION",
    "SELL_IN_BUY_REVERSAL_ATTEMPT",
    "BUY_PAUSE_IN_BUY",
    "SELL_TREND",
    "SELL_IN_SELL_CONTINUATION",
    "BUY_IN_SELL_PULLBACK",
    "BUY_IN_SELL_ACCUMULATION",
    "BUY_IN_SELL_REVERSAL_ATTEMPT",
    "SELL_PAUSE_IN_SELL",
    "RANGE_ACCUMULATION",
    "RANGE_DISTRIBUTION",
    "CHOP_NO_TRADE",
    "UNKNOWN",
]

MomentumInterpretationV3 = Literal[
    "NO_MOMENTUM_DRIVER",
    "TREND_REENTRY_SUPPORT",
    "COUNTER_LEG_REACTION_SUPPORT",
    "STRUCTURE_BREAK_ACCEPTANCE_SUPPORT",
    "RAW_MOMENTUM_DIAGNOSTIC_ONLY",
    "COUNTERTREND_TRAP_RISK",
    "LATE_IMPULSE_RISK",
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
    "SELL_IN_BUY_PROFESSIONAL_COUNTER_LEG",
    "BUY_IN_SELL_PROFESSIONAL_COUNTER_LEG",
    "SELL_IN_BUY_OPPOSING_FORCE_REACTION",
    "BUY_IN_SELL_OPPOSING_FORCE_REACTION",
    "SELL_TREND_RESUMPTION_FROM_SUPPLY",
    "BUY_TREND_RESUMPTION_FROM_DEMAND",
    "BUY_CURRENT_PRESSURE_CONTINUATION",
    "SELL_CURRENT_PRESSURE_CONTINUATION",
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

MARKET_PHASES_V3: tuple[MarketPhaseV3, ...] = (
    "BUY_TREND",
    "BUY_IN_BUY_CONTINUATION",
    "SELL_IN_BUY_PULLBACK",
    "SELL_IN_BUY_DISTRIBUTION",
    "SELL_IN_BUY_REVERSAL_ATTEMPT",
    "BUY_PAUSE_IN_BUY",
    "SELL_TREND",
    "SELL_IN_SELL_CONTINUATION",
    "BUY_IN_SELL_PULLBACK",
    "BUY_IN_SELL_ACCUMULATION",
    "BUY_IN_SELL_REVERSAL_ATTEMPT",
    "SELL_PAUSE_IN_SELL",
    "RANGE_ACCUMULATION",
    "RANGE_DISTRIBUTION",
    "CHOP_NO_TRADE",
    "UNKNOWN",
)

MOMENTUM_INTERPRETATIONS_V3: tuple[MomentumInterpretationV3, ...] = (
    "NO_MOMENTUM_DRIVER",
    "TREND_REENTRY_SUPPORT",
    "COUNTER_LEG_REACTION_SUPPORT",
    "STRUCTURE_BREAK_ACCEPTANCE_SUPPORT",
    "RAW_MOMENTUM_DIAGNOSTIC_ONLY",
    "COUNTERTREND_TRAP_RISK",
    "LATE_IMPULSE_RISK",
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
        "AGAINST_GLOBAL_STRUCTURE",
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
PROFESSIONAL_MIN_PROFIT_ROOM_CANDLES = 8
PROFESSIONAL_LOW_CONTEXT_MIN_PROFIT_ROOM_CANDLES = 4


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


def _market_phase_v3(
    *,
    side: str,
    primary_bias_side: str,
    current_leg_side: str,
    current_leg_candle_count: int,
    movement_stage: str,
    professional_counter_leg: bool,
    countertrend_reversal_override: bool,
    conflict_market: bool,
) -> MarketPhaseV3:
    if conflict_market:
        return "CHOP_NO_TRADE"
    if primary_bias_side == "BUY":
        if side == "BUY" and current_leg_side == "BUY":
            return "BUY_IN_BUY_CONTINUATION"
        if current_leg_side == "SELL" or side == "SELL":
            if professional_counter_leg:
                return "SELL_IN_BUY_DISTRIBUTION"
            if countertrend_reversal_override:
                return "SELL_IN_BUY_REVERSAL_ATTEMPT"
            return "SELL_IN_BUY_PULLBACK"
        if current_leg_candle_count <= 1 or movement_stage in {"PAUSE", "RANGE", "UNKNOWN"}:
            return "BUY_PAUSE_IN_BUY"
        return "BUY_TREND"
    if primary_bias_side == "SELL":
        if side == "SELL" and current_leg_side == "SELL":
            return "SELL_IN_SELL_CONTINUATION"
        if current_leg_side == "BUY" or side == "BUY":
            if professional_counter_leg:
                return "BUY_IN_SELL_ACCUMULATION"
            if countertrend_reversal_override:
                return "BUY_IN_SELL_REVERSAL_ATTEMPT"
            return "BUY_IN_SELL_PULLBACK"
        if current_leg_candle_count <= 1 or movement_stage in {"PAUSE", "RANGE", "UNKNOWN"}:
            return "SELL_PAUSE_IN_SELL"
        return "SELL_TREND"
    if movement_stage in {"RANGE", "CHOP", "CONSOLIDATION"}:
        return "RANGE_ACCUMULATION" if side == "BUY" else "RANGE_DISTRIBUTION" if side == "SELL" else "CHOP_NO_TRADE"
    return "UNKNOWN"


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


def _merged_rows(*values: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        for row in _rows(value):
            signature = "|".join(
                str(
                    _first_present(row, "id", "key", "zone_id", "trendline_id", "object_id", "source_path")
                    or row.get("bbox")
                    or row.get("bounds")
                    or row
                )
                .strip()
                .split()
            )
            if signature in seen:
                continue
            seen.add(signature)
            rows.append(row)
    return rows


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
        distance = _zone_distance_norm(zone, default=1.0)
        inside_bonus = -0.35 if _zone_current_price_inside(zone) else 0.0
        candidates.append((max(0.0, distance + inside_bonus), _mapping(zone)))
    if not candidates:
        return {}
    return min(candidates, key=lambda item: item[0])[1]


def _opposing_zone_for_side(zones: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"
    return _nearest_zone_for_side(zones, opposite)


def _zone_distance_norm(zone: Mapping[str, Any], *, default: float = 0.0) -> float:
    return _float(
        _first_present(
            zone,
            "distance_from_current",
            "distance_to_latest_norm",
            "distance_norm",
            "opposing_force_distance_norm",
            "distance",
        ),
        default,
    )


def _distance_to_candle_room(distance_norm: float, visible_candle_count: int) -> int:
    if distance_norm <= 0.0 or visible_candle_count <= 0:
        return 0
    return max(1, int(round(distance_norm * float(max(1, visible_candle_count)))))


def _location_supports_side(side: str, location: Any, zone: Mapping[str, Any]) -> bool:
    text = _upper(location)
    zone_type = _zone_type(zone) if zone else ""
    if side == "BUY":
        return bool(zone_type == "DEMAND" or _contains_any(text, ("DEMAND", "SUPPORT", "LOCAL_LOW", "RANGE_LOW", "LOW")))
    if side == "SELL":
        return bool(zone_type == "SUPPLY" or _contains_any(text, ("SUPPLY", "RESISTANCE", "LOCAL_HIGH", "RANGE_HIGH", "HIGH")))
    return False


def _zone_is_current_area(zone: Mapping[str, Any]) -> bool:
    if not zone:
        return False
    if _zone_current_price_inside(zone):
        return True
    distance = _zone_distance_norm(zone, default=1.0)
    if distance <= 0.12:
        return True
    relevance = _upper(zone.get("entry_relevance") or zone.get("relevance") or zone.get("current_relevance"))
    return relevance in {"ENTRY_SUPPORT", "ENTRY_RESISTANCE", "ACTIVE_SUPPORT", "ACTIVE_RESISTANCE"}


def _wrong_side_entry_location(
    *,
    side: str,
    price_location_label: str,
    active_zone: Mapping[str, Any],
    opposing_zone: Mapping[str, Any],
) -> dict[str, Any]:
    if side not in {"BUY", "SELL"}:
        return {}
    wrong_zone_type = "SUPPLY" if side == "BUY" else "DEMAND"
    wrong_label_terms = ("SUPPLY", "RESISTANCE") if side == "BUY" else ("DEMAND", "SUPPORT")
    if _contains_any(price_location_label, wrong_label_terms):
        return {
            "source": "price_location",
            "side": side,
            "location": price_location_label,
            "wrong_zone_type": wrong_zone_type,
        }
    for source, zone in (("active_zone", active_zone), ("opposing_zone", opposing_zone)):
        if not zone or _zone_type(zone) != wrong_zone_type or not _zone_is_current_area(zone):
            continue
        return {
            "source": source,
            "side": side,
            "zone_id": str(zone.get("zone_id") or zone.get("id") or zone.get("key") or ""),
            "zone_type": _zone_type(zone),
            "distance_norm": round(float(_zone_distance_norm(zone, default=1.0)), 4),
            "current_price_inside": _zone_current_price_inside(zone),
            "wrong_zone_type": wrong_zone_type,
        }
    return {}


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


def _bounds_from_value(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, Mapping):
        mapped = _mapping(value)
        for key in ("bbox", "bounds", "window", "rect"):
            bounds = _bounds_from_value(mapped.get(key))
            if bounds is not None:
                return bounds
        x1 = _first_present(mapped, "x1", "left", "min_x")
        y1 = _first_present(mapped, "y1", "top", "min_y")
        x2 = _first_present(mapped, "x2", "right", "max_x")
        y2 = _first_present(mapped, "y2", "bottom", "max_y")
        if None not in (x1, y1, x2, y2):
            return (_float(x1), _float(y1), _float(x2), _float(y2))
        cx = _first_present(mapped, "center_x", "x")
        cy = _first_present(mapped, "center_y", "y")
        if cx is not None and cy is not None:
            x = _float(cx)
            y = _float(cy)
            return (x - 4.0, y - 4.0, x + 4.0, y + 4.0)
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    items = list(cast(Sequence[Any], value))
    if len(items) >= 4:
        x1, y1, x2, y2 = (_float(items[0]), _float(items[1]), _float(items[2]), _float(items[3]))
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if len(items) >= 2:
        x = _float(items[0])
        y = _float(items[1])
        return (x - 4.0, y - 4.0, x + 4.0, y + 4.0)
    return None


def _bounds_from_row(row: Mapping[str, Any], *keys: str) -> tuple[float, float, float, float] | None:
    for key in keys:
        bounds = _bounds_from_value(row.get(key))
        if bounds is not None:
            return bounds
    return None


def _bounds_center(bounds: tuple[float, float, float, float] | None) -> tuple[float, float] | None:
    if bounds is None:
        return None
    return ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)


def _sequence_count(value: Any) -> int:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return 0
    return len(cast(Sequence[Any], value))


def _history_row_side(row: Mapping[str, Any]) -> str:
    side = _first_side(
        row.get("side"),
        row.get("direction"),
        row.get("expected_side"),
        row.get("action"),
        row.get("bias"),
    )
    if side in {"BUY", "SELL"}:
        return side
    label = _upper(row.get("label") or row.get("display_label") or row.get("name") or row.get("key"))
    if "BUY" in label or "DEMAND" in label or "SUPPORT" in label:
        return "BUY"
    if "SELL" in label or "SUPPLY" in label or "RESISTANCE" in label:
        return "SELL"
    return "HOLD"


def _history_rows_for_replay_template(
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    history_context: Mapping[str, Any],
    tracking_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    market_tracking = _mapping(market.get("tracking_summary"))
    sources = (
        tracking_summary.get("historical_structure"),
        snapshot.get("historical_structure"),
        market_tracking.get("historical_structure"),
        market.get("historical_structure"),
        history_context.get("historical_structure"),
        history_context.get("best_matches"),
        history_context.get("matches"),
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        for row in _rows(source):
            signature = "|".join(
                str(_first_present(row, "id", "key", "label", "display_label", "source_path") or row.get("bbox") or row)
                .strip()
                .split()
            )
            if signature in seen:
                continue
            seen.add(signature)
            rows.append(row)
    return rows


def _history_expected_move_candles(
    row: Mapping[str, Any],
    entry_bounds: tuple[float, float, float, float] | None,
    exit_bounds: tuple[float, float, float, float] | None,
) -> int:
    explicit = max(
        _int(_first_present(row, "expected_move_candles", "move_candles", "leg_candles", "candle_count"), 0),
        _sequence_count(row.get("source_indices")),
        _sequence_count(row.get("anchor_candle_indices")),
        _sequence_count(row.get("anchor_candles")),
        _sequence_count(row.get("path")),
        _sequence_count(row.get("line_points")),
        _sequence_count(row.get("points")),
    )
    if explicit > 0:
        return explicit
    entry_center = _bounds_center(entry_bounds)
    exit_center = _bounds_center(exit_bounds)
    if entry_center is None or exit_center is None:
        return 0
    return max(1, int(round(abs(exit_center[0] - entry_center[0]) / 24.0)))


def _replay_wave_template_v3(
    *,
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    history_context: Mapping[str, Any],
    tracking_summary: Mapping[str, Any],
    side: str,
    history_enter_here: bool,
    history_exit_here: bool,
    inside_trigger: bool,
    retest_confirmed: bool,
    role_flip_confirmed: bool,
    trendline_confluence: bool,
    continuation_confirmed: bool,
    break_of_structure_confirmed: bool,
    structure_shift_confirmed: bool,
    liquidity_sweep_detected: bool,
    measured_reaction_accepted: bool,
    current_candle_ok: bool,
    movement_stage: str,
    same_side_current_leg: bool,
    current_leg_candle_count: int,
    professional_min_profit_room_candles: int,
    professional_reaction_is_current_truth: bool,
    counter_leg_is_current_truth: bool,
    opposing_force_ok: bool,
) -> dict[str, Any]:
    rows = _history_rows_for_replay_template(snapshot, market, history_context, tracking_summary)
    if side not in {"BUY", "SELL"}:
        return {
            "schema_version": "PG_REPLAY_WAVE_TEMPLATE_V3",
            "side": side,
            "rows_total": len(rows),
            "same_side_templates": 0,
            "entry_exit_templates": 0,
            "template_profitable": False,
            "entry_alignment_ready": False,
            "late_template_chase_risk": False,
            "best_expected_move_candles": 0,
            "rules": ["replay_template_requires_buy_or_sell_side"],
        }

    same_side_templates = 0
    entry_exit_templates = 0
    best_template: dict[str, Any] = {}
    best_rank: tuple[int, int, float, int] = (-1, -1, 0.0, 0)
    for index, row in enumerate(rows):
        row_side = _history_row_side(row)
        if row_side != side:
            continue
        same_side_templates += 1
        entry_bounds = _bounds_from_row(
            row,
            "sniper_window",
            "trigger_window",
            "entry_window",
            "entry_bbox",
            "start_bbox",
            "start_point",
            "entry_point",
            "sniper_point",
        )
        exit_bounds = _bounds_from_row(
            row,
            "target_window",
            "target_bbox",
            "exit_window",
            "exit_bbox",
            "end_bbox",
            "end_point",
            "target_point",
        )
        entry_exit_pair = bool(entry_bounds is not None and exit_bounds is not None)
        if entry_exit_pair:
            entry_exit_templates += 1
        quality = max(
            _clip01(row.get("truth_score"), 0.0),
            _clip01(row.get("confidence"), 0.0),
            _clip01(row.get("quality"), 0.0),
            _clip01(row.get("score"), 0.0),
            0.65 if entry_exit_pair else 0.0,
        )
        expected_candles = _history_expected_move_candles(row, entry_bounds, exit_bounds)
        entry_center = _bounds_center(entry_bounds)
        exit_center = _bounds_center(exit_bounds)
        if entry_center is not None and exit_center is not None:
            target_direction_valid = bool(exit_center[1] < entry_center[1]) if side == "BUY" else bool(exit_center[1] > entry_center[1])
        else:
            target_direction_valid = entry_exit_pair
        profitable = bool(
            entry_exit_pair
            and target_direction_valid
            and quality >= 0.72
            and expected_candles >= professional_min_profit_room_candles
        )
        rank = (1 if profitable else 0, 1 if entry_exit_pair else 0, quality, expected_candles)
        if rank > best_rank:
            best_rank = rank
            best_template = {
                "index": index,
                "id": str(_first_present(row, "id", "key", "source_path") or f"historical_structure[{index}]"),
                "label": str(_first_present(row, "label", "display_label", "name") or ""),
                "row_side": row_side,
                "entry_window_present": entry_bounds is not None,
                "exit_window_present": exit_bounds is not None,
                "target_direction_valid": target_direction_valid,
                "template_profitable": profitable,
                "quality": round(float(quality), 4),
                "expected_move_candles": expected_candles,
                "entry_bounds": list(entry_bounds) if entry_bounds is not None else [],
                "exit_bounds": list(exit_bounds) if exit_bounds is not None else [],
            }

    best_expected = _int(best_template.get("expected_move_candles"), 0)
    best_profitable = _bool(best_template.get("template_profitable"))
    fresh_entry_proof = bool(
        history_enter_here
        or inside_trigger
        or retest_confirmed
        or role_flip_confirmed
        or trendline_confluence
        or continuation_confirmed
        or break_of_structure_confirmed
        or structure_shift_confirmed
        or liquidity_sweep_detected
        or measured_reaction_accepted
        or professional_reaction_is_current_truth
        or counter_leg_is_current_truth
    )
    current_entry_phase = bool(
        best_profitable
        and current_candle_ok
        and fresh_entry_proof
        and not history_exit_here
        and opposing_force_ok
    )
    late_threshold = max(6, int(round(float(best_expected) * 0.55))) if best_expected > 0 else 0
    current_mid_or_late_phase = bool(
        best_profitable
        and same_side_current_leg
        and current_leg_candle_count >= late_threshold > 0
        and movement_stage in {"MATURE", "LATE", "EXHAUSTED"}
        and not current_entry_phase
        and not professional_reaction_is_current_truth
        and not counter_leg_is_current_truth
    )
    entry_alignment_ready = bool(best_profitable and current_entry_phase)
    late_template_chase_risk = bool(best_profitable and current_mid_or_late_phase)
    return {
        "schema_version": "PG_REPLAY_WAVE_TEMPLATE_V3",
        "side": side,
        "rows_total": len(rows),
        "same_side_templates": same_side_templates,
        "entry_exit_templates": entry_exit_templates,
        "best_template_id": best_template.get("id", ""),
        "best_template_label": best_template.get("label", ""),
        "best_quality": best_template.get("quality", 0.0),
        "best_expected_move_candles": best_expected,
        "best_template": best_template,
        "entry_window_present": _bool(best_template.get("entry_window_present")),
        "exit_window_present": _bool(best_template.get("exit_window_present")),
        "target_direction_valid": _bool(best_template.get("target_direction_valid")),
        "history_enter_here": history_enter_here,
        "history_exit_here": history_exit_here,
        "fresh_entry_proof": fresh_entry_proof,
        "current_entry_phase": current_entry_phase,
        "current_mid_or_late_phase": current_mid_or_late_phase,
        "template_profitable": best_profitable,
        "entry_alignment_ready": entry_alignment_ready,
        "late_template_chase_risk": late_template_chase_risk,
        "late_threshold_candles": late_threshold,
        "rules": [
            "replay_entry_requires_sniper_or_trigger_window",
            "replay_exit_requires_target_or_exit_window",
            "template_must_match_candidate_side",
            "template_expected_move_must_clear_professional_room",
            "current_price_must_be_back_at_entry_phase_not_mid_leg",
        ],
    }


OVERLAY_ENTRY_TYPES: frozenset[str] = frozenset(
    {"SNIPER_ENTRY_BOX", "RETEST_BOX", "REPLAY_ENTRY", "DEMAND_ZONE", "SUPPLY_ZONE"}
)
OVERLAY_TARGET_TYPES: frozenset[str] = frozenset({"TARGET_ZONE_BOX", "REPLAY_EXIT"})
OVERLAY_STRUCTURE_TYPES: frozenset[str] = frozenset({"IMPULSE_BOX", "PULLBACK_BOX", "CONTINUATION_BOX"})
OVERLAY_CURRENT_TYPES: frozenset[str] = frozenset({"CURRENT_BOX", "CURRENT"})
OVERLAY_TRENDLINE_TYPES: frozenset[str] = frozenset(
    {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}
)
OVERLAY_DIAGNOSTIC_TYPES: frozenset[str] = frozenset(
    {
        "BROKER_CONTROL",
        "CHART_BOUNDS",
        "DEBUG_RAW_DETECTION",
        "LABEL_COLLISION_DEBUG",
        "REJECTED_OVERLAY",
        "SCENE_GRAPH_DEBUG",
        "STALE_OVERLAY",
        "TRANSFORM_DEBUG",
    }
)


def _overlay_text(row: Mapping[str, Any], source_path: str = "") -> str:
    parts = [
        source_path,
        str(row.get("overlay_type") or ""),
        str(row.get("object_type") or ""),
        str(row.get("type") or ""),
        str(row.get("kind") or ""),
        str(row.get("role") or ""),
        str(row.get("label") or ""),
        str(row.get("display_label") or ""),
        str(row.get("name") or ""),
        str(row.get("source_path") or ""),
    ]
    return _upper(" ".join(part for part in parts if part))


def _overlay_type_from_row(row: Mapping[str, Any], source_path: str = "") -> str:
    explicit = _upper(
        _first_present(row, "overlay_type", "object_type", "type", "box_type", "kind", "role")
    )
    text = _overlay_text(row, source_path)
    if explicit in OVERLAY_DIAGNOSTIC_TYPES:
        return explicit
    if "REPLAY" in text and ("ENTRY" in text or "WOULD HAVE ENTERED" in text):
        return "REPLAY_ENTRY"
    if "REPLAY" in text and ("EXIT" in text or "WOULD HAVE EXITED" in text):
        return "REPLAY_EXIT"
    if "WOULD HAVE ENTERED" in text:
        return "REPLAY_ENTRY"
    if "WOULD HAVE EXITED" in text:
        return "REPLAY_EXIT"
    if "SNIPER" in text or ("ENTRY" in text and "EXIT" not in text and "TARGET" not in text):
        return "SNIPER_ENTRY_BOX"
    if "TRIGGER" in text or "RETEST" in text:
        return "RETEST_BOX"
    if "TARGET" in text or "TAKE_PROFIT" in text:
        return "TARGET_ZONE_BOX"
    if "INVALID" in text or "STOP" in text:
        return "INVALIDATION_BOX"
    if "OPPOSING" in text or "OPPOSING_FORCE" in text:
        return "OPPOSING_FORCE"
    if "DEMAND" in text or "SUPPORT_ZONE" in text:
        return "DEMAND_ZONE"
    if "SUPPLY" in text or "RESISTANCE_ZONE" in text:
        return "SUPPLY_ZONE"
    if "SUPPORT" in text and "TRENDLINE" in text:
        return "SUPPORT_TRENDLINE"
    if "RESISTANCE" in text and "TRENDLINE" in text:
        return "RESISTANCE_TRENDLINE"
    if "INNER" in text and "TRENDLINE" in text:
        return "INNER_TRENDLINE"
    if "ANGLE" in text or "VECTOR" in text:
        return "ANGLE_VECTOR"
    if "PREDICTION" in text or "PROJECT" in text:
        return "PREDICTION_PATH"
    if "PROGRESSION" in text or ("PATH" in text and "HISTORICAL" in text):
        return "PROGRESSION_PATH"
    if "IMPULSE" in text:
        return "IMPULSE_BOX"
    if "PULLBACK" in text:
        return "PULLBACK_BOX"
    if "CONTINUATION" in text:
        return "CONTINUATION_BOX"
    if explicit:
        return explicit
    if row.get("path") or row.get("line_points") or row.get("points"):
        return "PREDICTION_PATH"
    return "UNKNOWN_OVERLAY"


def _overlay_side_from_row(row: Mapping[str, Any], overlay_type: str) -> str:
    side = _history_row_side(row)
    if side in {"BUY", "SELL"}:
        return side
    if overlay_type in {"DEMAND_ZONE", "SUPPORT_TRENDLINE"}:
        return "BUY"
    if overlay_type in {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"}:
        return "SELL"
    return "HOLD"


def _overlay_quality(row: Mapping[str, Any]) -> float:
    return max(
        _clip01(row.get("anchor_quality"), 0.0),
        _clip01(row.get("truth_score"), 0.0),
        _clip01(row.get("quality"), 0.0),
        _clip01(row.get("confidence"), 0.0),
        _clip01(row.get("score"), 0.0),
        0.62 if _bounds_from_row(row, "bbox", "bounds", "window", "rect") is not None else 0.0,
    )


def _overlay_current_touch(row: Mapping[str, Any], source_path: str) -> bool:
    text = _overlay_text(row, source_path)
    return bool(
        _bool(
            row.get("current_price_inside")
            or row.get("inside")
            or row.get("touching_now")
            or row.get("current_touch")
            or row.get("active")
            or row.get("live")
            or row.get("entry_allowed")
            or row.get("timing_entry_allowed")
        )
        or "EXECUTION_TIMING.ENTRY_AREA_ZONE" in text
        or "ACTIVE_THESIS_ENTRY" in text
    )


def _overlay_expected_move_candles(row: Mapping[str, Any]) -> int:
    explicit = max(
        _int(
            _first_present(
                row,
                "expected_move_candles",
                "expected_candles",
                "move_candles",
                "leg_candles",
                "candle_count",
                "projected_candle_count",
                "expected_thesis_candles",
            ),
            0,
        ),
        _sequence_count(row.get("source_indices")),
        _sequence_count(row.get("anchor_candle_indices")),
        _sequence_count(row.get("anchor_candles")),
        _sequence_count(row.get("path")),
        _sequence_count(row.get("line_points")),
        _sequence_count(row.get("points")),
        _sequence_count(row.get("projected_candles")),
    )
    if explicit > 0:
        return explicit
    entry_bounds = _bounds_from_row(row, "sniper_window", "trigger_window", "entry_window", "entry_bbox")
    target_bounds = _bounds_from_row(row, "target_window", "target_bbox", "exit_window", "exit_bbox")
    return _history_expected_move_candles(row, entry_bounds, target_bounds)


def _overlay_contained_candle_count(row: Mapping[str, Any]) -> int:
    return max(
        _int(_first_present(row, "contained_count", "contained_candle_count", "candle_count"), 0),
        _sequence_count(row.get("contained_candles")),
        _sequence_count(row.get("contained_candle_indices")),
        _sequence_count(row.get("anchor_candle_indices")),
        _sequence_count(row.get("anchor_candles")),
        _sequence_count(row.get("source_indices")),
    )


def _overlay_summary_row(row: Mapping[str, Any], normalized: Mapping[str, Any]) -> dict[str, Any]:
    bounds = normalized.get("bounds")
    return {
        "source_path": str(normalized.get("source_path") or ""),
        "type": str(normalized.get("type") or ""),
        "side": str(normalized.get("side") or "HOLD"),
        "id": str(_first_present(row, "id", "key", "zone_id", "trendline_id", "object_id", "source_path") or ""),
        "label": str(_first_present(row, "label", "display_label", "name") or ""),
        "quality": normalized.get("quality", 0.0),
        "current_touch": bool(normalized.get("current_touch")),
        "expected_move_candles": int(normalized.get("expected_move_candles") or 0),
        "contained_candle_count": int(normalized.get("contained_candle_count") or 0),
        "bounds": bounds if isinstance(bounds, list) else [],
    }


def _looks_like_overlay_row(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("object_type")
        or row.get("overlay_type")
        or row.get("type")
        or row.get("kind")
        or row.get("role")
        or row.get("bbox")
        or row.get("bounds")
        or row.get("line_points")
        or row.get("points")
        or row.get("path")
    )


def _overlay_suite_evidence_v3(
    *,
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    tracking_summary: Mapping[str, Any],
    side: str,
) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_row(
        row: Mapping[str, Any],
        source_path: str,
        *,
        overlay_type: str | None = None,
        side_override: str | None = None,
    ) -> None:
        mapped = _mapping(row)
        if not mapped:
            return
        resolved_type = _upper(overlay_type) if overlay_type else _overlay_type_from_row(mapped, source_path)
        if resolved_type in OVERLAY_DIAGNOSTIC_TYPES:
            return
        resolved_side = side_override if side_override in {"BUY", "SELL"} else _overlay_side_from_row(mapped, resolved_type)
        bounds_tuple = _bounds_from_row(
            mapped,
            "bbox",
            "bounds",
            "window",
            "rect",
            "zone_bounds",
            "entry_window",
            "trigger_window",
            "sniper_window",
            "target_window",
            "target_bbox",
        )
        bounds = list(bounds_tuple) if bounds_tuple is not None else []
        object_identity = _first_present(
            mapped,
            "id",
            "key",
            "zone_id",
            "trendline_id",
            "object_id",
            "track_id",
            "source_key",
        )
        original_source_path = str(mapped.get("source_path") or "")
        signature = "|".join(
            str(
                (
                    resolved_type,
                    resolved_side,
                    object_identity,
                    original_source_path,
                    bounds,
                    "" if object_identity or original_source_path or bounds else source_path,
                )
            )
            .strip()
            .split()
        )
        if signature in seen:
            return
        seen.add(signature)
        normalized_rows.append(
            {
                "source_path": source_path,
                "type": resolved_type,
                "side": resolved_side,
                "quality": round(float(_overlay_quality(mapped)), 4),
                "current_touch": _overlay_current_touch(mapped, source_path),
                "expected_move_candles": _overlay_expected_move_candles(mapped),
                "contained_candle_count": _overlay_contained_candle_count(mapped),
                "bounds": bounds,
                "row": mapped,
            }
        )

    def add_rows(value: Any, source_prefix: str, *, overlay_type: str | None = None, side_override: str | None = None) -> None:
        for index, row in enumerate(_rows(value)):
            add_row(row, f"{source_prefix}[{index}]", overlay_type=overlay_type, side_override=side_override)

    def add_registry_rows(value: Any, source_prefix: str, *, depth: int = 0) -> None:
        if depth > 4 or value in (None, "", [], {}, ()):
            return
        mapping = _mapping(value)
        if mapping:
            if _looks_like_overlay_row(mapping):
                add_row(mapping, source_prefix)
            for key in (
                "objects",
                "object_registry",
                "tracked_objects",
                "overlays",
                "overlay_objects",
                "rows",
                "overlay_rows",
                "normalized_rows",
                "active_objects",
                "items",
            ):
                child = mapping.get(key)
                if child in (None, "", [], {}, ()):
                    continue
                for index, row in enumerate(_rows(child)):
                    add_row(row, f"{source_prefix}.{key}[{index}]")
            for key in (
                "market_objects",
                "market_object_registry",
                "registry",
                "overlay_geometry",
                "truth_audit",
                "overlay_truth_audit",
            ):
                child = mapping.get(key)
                if child not in (None, "", [], {}, ()):
                    add_registry_rows(child, f"{source_prefix}.{key}", depth=depth + 1)
            return
        for index, row in enumerate(_rows(value)):
            add_row(row, f"{source_prefix}[{index}]")

    add_registry_rows(snapshot.get("market_objects"), "snapshot.market_objects")
    add_registry_rows(snapshot.get("market_object_registry"), "snapshot.market_object_registry")
    add_registry_rows(snapshot.get("overlay_geometry"), "snapshot.overlay_geometry")
    add_registry_rows(snapshot.get("overlay_truth_audit"), "snapshot.overlay_truth_audit")
    add_registry_rows(market.get("market_objects"), "market.market_objects")
    add_registry_rows(market.get("market_object_registry"), "market.market_object_registry")
    add_registry_rows(market.get("overlay_geometry"), "market.overlay_geometry")
    add_registry_rows(market.get("overlay_truth_audit"), "market.overlay_truth_audit")
    add_registry_rows(tracking_summary.get("market_objects"), "tracking_summary.market_objects")
    add_registry_rows(tracking_summary.get("market_object_registry"), "tracking_summary.market_object_registry")
    add_registry_rows(tracking_summary.get("overlay_geometry"), "tracking_summary.overlay_geometry")
    add_registry_rows(tracking_summary.get("overlay_truth_audit"), "tracking_summary.overlay_truth_audit")
    add_rows(tracking_summary.get("tracked_candles"), "tracking_summary.tracked_candles", overlay_type="CURRENT_CANDLE")
    current_box = _mapping(tracking_summary.get("current_box"))
    if current_box:
        current_box_side = _first_side(current_box.get("side"), current_box.get("direction"))
        add_row(
            current_box,
            "tracking_summary.current_box",
            overlay_type="CURRENT_BOX",
            side_override=current_box_side if current_box_side in {"BUY", "SELL"} else None,
        )
    add_rows(tracking_summary.get("trendlines_v3"), "tracking_summary.trendlines_v3")
    add_rows(tracking_summary.get("support_resistance_zones"), "tracking_summary.support_resistance_zones")

    for index, box in enumerate(_rows(tracking_summary.get("structure_boxes"))):
        add_row(box, f"tracking_summary.structure_boxes[{index}]")
        box_side = _history_row_side(box)
        for field_name, overlay_type in (
            ("sniper_window", "SNIPER_ENTRY_BOX"),
            ("trigger_window", "RETEST_BOX"),
            ("entry_window", "SNIPER_ENTRY_BOX"),
            ("target_window", "TARGET_ZONE_BOX"),
            ("target_bbox", "TARGET_ZONE_BOX"),
            ("invalidation_window", "INVALIDATION_BOX"),
            ("pullback_window", "PULLBACK_BOX"),
            ("continuation_window", "CONTINUATION_BOX"),
        ):
            value = box.get(field_name)
            if value in (None, "", [], {}, ()):
                continue
            add_row(
                {**box, "bounds": value, "role": field_name, "overlay_type": overlay_type},
                f"tracking_summary.structure_boxes[{index}].{field_name}",
                overlay_type=overlay_type,
                side_override=box_side if box_side in {"BUY", "SELL"} else None,
            )
        if box.get("invalidation_y") is not None:
            add_row(
                {**box, "role": "invalidation_y", "overlay_type": "INVALIDATION_BOX"},
                f"tracking_summary.structure_boxes[{index}].invalidation_y",
                overlay_type="INVALIDATION_BOX",
                side_override=box_side if box_side in {"BUY", "SELL"} else None,
            )

    for index, row in enumerate(_rows(tracking_summary.get("historical_structure"))):
        add_row(row, f"tracking_summary.historical_structure[{index}]", overlay_type="PROGRESSION_PATH")
        row_side = _history_row_side(row)
        for field_name, overlay_type in (
            ("sniper_window", "REPLAY_ENTRY"),
            ("trigger_window", "RETEST_BOX"),
            ("entry_window", "REPLAY_ENTRY"),
            ("entry_bbox", "REPLAY_ENTRY"),
            ("target_window", "REPLAY_EXIT"),
            ("target_bbox", "REPLAY_EXIT"),
            ("exit_window", "REPLAY_EXIT"),
            ("exit_bbox", "REPLAY_EXIT"),
        ):
            value = row.get(field_name)
            if value in (None, "", [], {}, ()):
                continue
            add_row(
                {**row, "bounds": value, "role": field_name, "overlay_type": overlay_type},
                f"tracking_summary.historical_structure[{index}].{field_name}",
                overlay_type=overlay_type,
                side_override=row_side if row_side in {"BUY", "SELL"} else None,
            )

    projection = _mapping(tracking_summary.get("projection") or snapshot.get("projection") or market.get("projection"))
    add_rows(projection.get("zones"), "tracking_summary.projection.zones")
    for index, zone in enumerate(_rows(projection.get("zones"))):
        zone_side = _history_row_side({**zone, "direction": zone.get("direction") or projection.get("direction")})
        for field_name, overlay_type in (
            ("target_bbox", "TARGET_ZONE_BOX"),
            ("target_window", "TARGET_ZONE_BOX"),
            ("invalidation_window", "INVALIDATION_BOX"),
            ("path", "PREDICTION_PATH"),
            ("line_points", "PREDICTION_PATH"),
        ):
            value = zone.get(field_name)
            if value in (None, "", [], {}, ()):
                continue
            add_row(
                {**zone, "bounds": value, "role": field_name, "overlay_type": overlay_type},
                f"tracking_summary.projection.zones[{index}].{field_name}",
                overlay_type=overlay_type,
                side_override=zone_side if zone_side in {"BUY", "SELL"} else None,
            )
        if zone.get("invalidation_y") is not None:
            add_row(
                {**zone, "role": "invalidation_y", "overlay_type": "INVALIDATION_BOX"},
                f"tracking_summary.projection.zones[{index}].invalidation_y",
                overlay_type="INVALIDATION_BOX",
                side_override=zone_side if zone_side in {"BUY", "SELL"} else None,
            )

    add_rows(tracking_summary.get("angle_vectors"), "tracking_summary.angle_vectors", overlay_type="ANGLE_VECTOR")
    execution_timing = _mapping(tracking_summary.get("execution_timing") or snapshot.get("execution_timing") or market.get("execution_timing"))
    entry_zone = _mapping(execution_timing.get("entry_area_zone"))
    if entry_zone:
        entry_type = "DEMAND_ZONE" if side == "BUY" else "SUPPLY_ZONE" if side == "SELL" else None
        add_row(entry_zone, "tracking_summary.execution_timing.entry_area_zone", overlay_type=entry_type, side_override=side)
    opposing_zone = _mapping(execution_timing.get("opposing_force_zone"))
    if opposing_zone:
        add_row(opposing_zone, "tracking_summary.execution_timing.opposing_force_zone", overlay_type="OPPOSING_FORCE")

    for root_name, root in (("snapshot", snapshot), ("market", market)):
        root_current_box = _mapping(root.get("current_box"))
        if root_current_box:
            root_current_side = _first_side(root_current_box.get("side"), root_current_box.get("direction"))
            add_row(
                root_current_box,
                f"{root_name}.current_box",
                overlay_type="CURRENT_BOX",
                side_override=root_current_side if root_current_side in {"BUY", "SELL"} else None,
            )
        overlay_geometry = _mapping(root.get("overlay_geometry"))
        add_rows(overlay_geometry.get("boxes"), f"{root_name}.overlay_geometry.boxes")
        add_rows(overlay_geometry.get("zones"), f"{root_name}.overlay_geometry.zones")
        add_rows(overlay_geometry.get("trendlines"), f"{root_name}.overlay_geometry.trendlines")
        for key in ("v3_overlay_objects", "overlay_objects", "renderables"):
            add_rows(root.get(key), f"{root_name}.{key}")
        overlays = root.get("overlays")
        overlay_rows = _mapping(overlays).get("objects") if isinstance(overlays, Mapping) else overlays
        add_rows(overlay_rows, f"{root_name}.overlays")
        memory = _mapping(
            root.get("memory_projection_current")
            or root.get("memory_projection_predict")
            or root.get("memory_projection_future")
        )
        forward = _mapping(memory.get("forward_projection"))
        projected_candles = forward.get("projected_candles")
        if projected_candles:
            memory_side = _first_side(
                forward.get("direction"),
                memory.get("direction"),
                memory.get("projection_bias_direction"),
            )
            add_row(
                {
                    "overlay_type": "PREDICTION_PATH",
                    "role": "memory_forward_projection",
                    "direction": memory_side,
                    "projected_candles": projected_candles,
                    "expected_move_candles": _sequence_count(projected_candles),
                    "confidence": forward.get("confidence") or memory.get("confidence"),
                },
                f"{root_name}.memory_projection.forward_projection.projected_candles",
                overlay_type="PREDICTION_PATH",
                side_override=memory_side if memory_side in {"BUY", "SELL"} else None,
            )

    counts_by_type: dict[str, int] = {}
    counts_by_layer: dict[str, int] = {}
    best_entry: dict[str, Any] = {}
    best_target: dict[str, Any] = {}
    best_prediction_path: dict[str, Any] = {}
    best_opposing_force: dict[str, Any] = {}
    entry_window_count = 0
    same_side_entry_window_count = 0
    current_entry_touch_count = 0
    target_window_count = 0
    invalidation_count = 0
    prediction_path_count = 0
    angle_vector_count = 0
    supply_demand_count = 0
    structure_box_count = 0
    trendline_count = 0
    replay_path_count = 0
    memory_path_count = 0
    opposing_force_count = 0
    actionable_count = 0
    same_side_actionable_count = 0
    current_box_count = 0
    same_side_current_box_count = 0
    same_side_current_box_candle_count = 0
    same_side_projection_entry_count = 0
    same_side_reclaim_projection_count = 0
    best_expected_move_candles = 0
    best_entry_rank = (-1, -1.0, -1)
    best_target_rank = (-1, -1.0, -1)
    best_path_rank = (-1, -1.0, -1)
    best_force_rank = (-1, -1.0, -1)
    for normalized in normalized_rows:
        row = _mapping(normalized.get("row"))
        overlay_type = str(normalized.get("type") or "")
        source_path = str(normalized.get("source_path") or "")
        overlay_text = _overlay_text(row, source_path)
        row_side = str(normalized.get("side") or "HOLD")
        same_side = bool(side in {"BUY", "SELL"} and row_side in {side, "HOLD"})
        quality = _float(normalized.get("quality"), 0.0)
        expected_candles = _int(normalized.get("expected_move_candles"), 0)
        contained_candles = _int(normalized.get("contained_candle_count"), 0)
        best_expected_move_candles = max(best_expected_move_candles, expected_candles)
        counts_by_type[overlay_type] = counts_by_type.get(overlay_type, 0) + 1
        layer = source_path.split("[", 1)[0]
        counts_by_layer[layer] = counts_by_layer.get(layer, 0) + 1
        current_box_layer = bool(overlay_type in OVERLAY_CURRENT_TYPES or source_path.endswith("current_box"))
        projection_layer = "PROJECTION.ZONES" in overlay_text
        reclaim_projection = bool(
            projection_layer
            and any(token in overlay_text for token in ("RECLAIM", "SNIPER", "TRIGGER", "ENTRY"))
        )
        if current_box_layer:
            current_box_count += 1
            if same_side:
                same_side_current_box_count += 1
                same_side_current_box_candle_count = max(same_side_current_box_candle_count, contained_candles)
        if same_side and projection_layer and overlay_type in OVERLAY_ENTRY_TYPES:
            same_side_projection_entry_count += 1
        if same_side and reclaim_projection:
            same_side_reclaim_projection_count += 1
        if overlay_type in OVERLAY_ENTRY_TYPES:
            entry_window_count += 1
            same_side_entry_window_count += 1 if same_side else 0
            current_entry_touch_count += 1 if same_side and bool(normalized.get("current_touch")) else 0
            rank = (1 if same_side else 0, quality, expected_candles)
            if rank > best_entry_rank:
                best_entry_rank = rank
                best_entry = _overlay_summary_row(row, normalized)
        if overlay_type in OVERLAY_TARGET_TYPES:
            target_window_count += 1
            rank = (1 if same_side else 0, quality, expected_candles)
            if rank > best_target_rank:
                best_target_rank = rank
                best_target = _overlay_summary_row(row, normalized)
        if overlay_type == "INVALIDATION_BOX":
            invalidation_count += 1
        if overlay_type == "PREDICTION_PATH":
            prediction_path_count += 1
            if "memory_projection" in str(normalized.get("source_path") or ""):
                memory_path_count += 1
            rank = (1 if same_side else 0, quality, expected_candles)
            if rank > best_path_rank:
                best_path_rank = rank
                best_prediction_path = _overlay_summary_row(row, normalized)
        if overlay_type == "ANGLE_VECTOR":
            angle_vector_count += 1
        if overlay_type in {"SUPPLY_ZONE", "DEMAND_ZONE"}:
            supply_demand_count += 1
        if overlay_type in OVERLAY_STRUCTURE_TYPES:
            structure_box_count += 1
        if overlay_type in OVERLAY_TRENDLINE_TYPES:
            trendline_count += 1
        if overlay_type in {"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"}:
            replay_path_count += 1
        if overlay_type == "OPPOSING_FORCE":
            opposing_force_count += 1
            rank = (1 if same_side else 0, quality, expected_candles)
            if rank > best_force_rank:
                best_force_rank = rank
                best_opposing_force = _overlay_summary_row(row, normalized)
        if overlay_type not in {"UNKNOWN_OVERLAY", "CURRENT_CANDLE"}:
            actionable_count += 1
            same_side_actionable_count += 1 if same_side else 0

    entry_ready = same_side_entry_window_count > 0
    current_entry_touch = current_entry_touch_count > 0
    target_ready = target_window_count > 0 or opposing_force_count > 0
    invalidation_ready = invalidation_count > 0
    projection_ready = prediction_path_count > 0 or best_expected_move_candles > 0
    trendline_ready = trendline_count > 0
    structure_ready = structure_box_count > 0
    angle_evidence_count = angle_vector_count + trendline_count + prediction_path_count
    angle_ready = angle_evidence_count > 0
    opposing_force_ready = opposing_force_count > 0
    full_suite_ready = bool(
        entry_ready
        and target_ready
        and (projection_ready or replay_path_count > 0 or structure_ready)
        and (structure_ready or trendline_ready or supply_demand_count > 0)
    )
    live_reclaim_breakout_ready = bool(
        side in {"BUY", "SELL"}
        and same_side_current_box_count > 0
        and (same_side_projection_entry_count > 0 or same_side_reclaim_projection_count > 0 or current_entry_touch)
        and target_ready
        and projection_ready
        and max(same_side_current_box_candle_count, best_expected_move_candles) >= 3
    )
    overlay_arsenal_score = _clip01(
        (0.18 if entry_ready else 0.0)
        + (0.14 if current_entry_touch else 0.0)
        + (0.16 if target_ready else 0.0)
        + (0.08 if invalidation_ready else 0.0)
        + (0.14 if projection_ready else 0.0)
        + (0.10 if structure_ready else 0.0)
        + (0.08 if trendline_ready else 0.0)
        + (0.06 if angle_ready else 0.0)
        + (0.06 if opposing_force_ready else 0.0)
        + min(0.10, same_side_actionable_count * 0.01)
    )
    first_class_feeds = {
        "tracking_summary.structure_boxes": structure_box_count > 0,
        "tracking_summary.support_resistance_zones": supply_demand_count > 0,
        "tracking_summary.trendlines_v3": trendline_count > 0,
        "tracking_summary.projection": projection_ready,
        "tracking_summary.angle_vectors": angle_ready,
        "tracking_summary.historical_structure": replay_path_count > 0,
    }
    return {
        "schema_version": "PG_PLAYBOOK_OVERLAY_SUITE_EVIDENCE_V3",
        "side": side,
        "rows_total": len(normalized_rows),
        "raw_overlay_rows_seen": len(normalized_rows),
        "rows": normalized_rows,
        "normalized_rows": normalized_rows,
        "counts_by_type": counts_by_type,
        "counts_by_layer": counts_by_layer,
        "actionable_count": actionable_count,
        "same_side_actionable_count": same_side_actionable_count,
        "entry_window_count": entry_window_count,
        "same_side_entry_window_count": same_side_entry_window_count,
        "current_entry_touch_count": current_entry_touch_count,
        "target_window_count": target_window_count,
        "invalidation_count": invalidation_count,
        "prediction_path_count": prediction_path_count,
        "angle_vector_count": angle_vector_count,
        "angle_evidence_count": angle_evidence_count,
        "supply_demand_count": supply_demand_count,
        "structure_box_count": structure_box_count,
        "trendline_count": trendline_count,
        "replay_path_count": replay_path_count,
        "memory_path_count": memory_path_count,
        "opposing_force_count": opposing_force_count,
        "entry_ready": entry_ready,
        "current_entry_touch": current_entry_touch,
        "target_ready": target_ready,
        "invalidation_ready": invalidation_ready,
        "projection_ready": projection_ready,
        "trendline_ready": trendline_ready,
        "structure_ready": structure_ready,
        "angle_ready": angle_ready,
        "opposing_force_ready": opposing_force_ready,
        "full_suite_ready": full_suite_ready,
        "current_box_count": current_box_count,
        "same_side_current_box_count": same_side_current_box_count,
        "same_side_current_box_candle_count": same_side_current_box_candle_count,
        "same_side_projection_entry_count": same_side_projection_entry_count,
        "same_side_reclaim_projection_count": same_side_reclaim_projection_count,
        "live_reclaim_breakout_ready": live_reclaim_breakout_ready,
        "best_entry": best_entry,
        "best_target": best_target,
        "best_prediction_path": best_prediction_path,
        "best_opposing_force": best_opposing_force,
        "expected_move_candles_from_projection": best_expected_move_candles,
        "overlay_arsenal_score": round(float(overlay_arsenal_score), 4),
        "first_class_feeds": first_class_feeds,
        "missing_first_class_feeds": [name for name, present in first_class_feeds.items() if not present],
        "rules": [
            "full_overlay_suite_is_playbook_evidence_not_gui_decoration",
            "lazy_loaded_gui_layers_do_not_limit_playbook_intelligence",
            "structure_projection_target_invalidation_angle_replay_and_memory_paths_are_first_class",
        ],
    }


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
    wave_context_ready: bool,
    timing_supportive: bool,
    current_candle_ok: bool,
    momentum_context_ready: bool,
    replay_template_entry_ready: bool,
    overlay_suite_entry_ready: bool,
    overlay_suite_full_ready: bool,
    late_chase: bool,
    opposing_force_ok: bool,
    conflict_market: bool,
    structural_extreme_reversal: bool,
) -> BookEntryProfile:
    if side not in {"BUY", "SELL"}:
        return "NO_TRADE"
    reaction_type = _upper(candle_reaction.get("reaction_type"))
    if (
        structural_extreme_reversal
        and reaction_type in {"WICK_REJECTION", "RECLAIM_AFTER_SWEEP", "BODY_ACCEPTANCE", "RETEST_HOLD"}
        and current_candle_ok
    ):
        return "AGGRESSIVE_SNIPER"
    if (
        replay_template_entry_ready
        and current_candle_ok
        and (
            inside_trigger
            or trendline_confluence
            or retest_confirmed
            or role_flip_confirmed
            or structural_extreme_reversal
        )
    ):
        return "AGGRESSIVE_SNIPER"
    if replay_template_entry_ready and current_candle_ok:
        return "CONTINUATION_RETEST"
    if (
        overlay_suite_entry_ready
        and current_candle_ok
        and (
            inside_trigger
            or trendline_confluence
            or retest_confirmed
            or role_flip_confirmed
            or structural_extreme_reversal
            or overlay_suite_full_ready
        )
    ):
        return "AGGRESSIVE_SNIPER"
    if overlay_suite_entry_ready and overlay_suite_full_ready and current_candle_ok:
        return "CONTINUATION_RETEST"
    current_touch = _bool(significant_structure.get("current_touch"))
    significant = _bool(significant_structure.get("significant"))
    if (
        significant
        and current_touch
        and (inside_trigger or trendline_confluence or structural_extreme_reversal)
        and reaction_type in {"WICK_REJECTION", "RECLAIM_AFTER_SWEEP", "BODY_ACCEPTANCE", "RETEST_HOLD"}
        and current_candle_ok
    ):
        return "AGGRESSIVE_SNIPER"
    if liquidity_sweep_detected and reaction_type in {"RECLAIM_AFTER_SWEEP", "WICK_REJECTION", "BODY_ACCEPTANCE"} and current_candle_ok:
        return "REVERSAL_RECLAIM"
    if (retest_confirmed or role_flip_confirmed) and current_candle_ok:
        return "CONSERVATIVE_RETEST"
    if continuation_confirmed and current_candle_ok and (retest_confirmed or break_of_structure_confirmed or structure_shift_confirmed or wave_context_ready):
        return "CONTINUATION_RETEST"
    if timing_supportive and current_candle_ok and significant and momentum_context_ready:
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
    if _bool(evidence.get("professional_opposing_force_reaction")):
        combo.append("OPPOSING_FORCE_REACTION")
    if _bool(evidence.get("professional_bias_resumption_reaction")):
        combo.append("TREND_RESUMPTION_REJECTION")
    if _bool(evidence.get("current_pressure_is_current_truth")):
        combo.append("CURRENT_PRESSURE_CONTINUATION")
    if _bool(evidence.get("replay_template_entry_ready")):
        combo.append("REPLAY_WAVE_TEMPLATE_ENTRY")
    if _bool(evidence.get("overlay_suite_full_ready")):
        combo.append("OVERLAY_SUITE_FULL_READ")
    if _bool(evidence.get("overlay_suite_entry_ready")) and _bool(evidence.get("overlay_suite_target_ready")):
        combo.append("OVERLAY_ENTRY_TARGET_MAP")
    if _bool(evidence.get("overlay_suite_projection_ready")):
        combo.append("OVERLAY_PROJECTION_PATH")
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
    if _bool(evidence.get("professional_opposing_force_reaction")):
        return "SELL_IN_BUY_OPPOSING_FORCE_REACTION" if side == "SELL" else "BUY_IN_SELL_OPPOSING_FORCE_REACTION"
    if _bool(evidence.get("professional_bias_resumption_reaction")):
        return "SELL_TREND_RESUMPTION_FROM_SUPPLY" if side == "SELL" else "BUY_TREND_RESUMPTION_FROM_DEMAND"
    if _bool(evidence.get("current_pressure_is_current_truth")):
        return "SELL_CURRENT_PRESSURE_CONTINUATION" if side == "SELL" else "BUY_CURRENT_PRESSURE_CONTINUATION"
    if _bool(evidence.get("counter_leg_is_current_truth")):
        return "SELL_IN_BUY_PROFESSIONAL_COUNTER_LEG" if side == "SELL" else "BUY_IN_SELL_PROFESSIONAL_COUNTER_LEG"
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
    overlay_suite_evidence = _overlay_suite_evidence_v3(
        snapshot=snapshot,
        market=market,
        tracking_summary=tracking_summary,
        side=side,
    )
    overlay_suite_entry_ready = _bool(overlay_suite_evidence.get("entry_ready"))
    overlay_suite_current_entry_touch = _bool(overlay_suite_evidence.get("current_entry_touch"))
    overlay_suite_projection_ready = _bool(overlay_suite_evidence.get("projection_ready"))
    overlay_suite_target_ready = _bool(overlay_suite_evidence.get("target_ready"))
    overlay_suite_full_ready = _bool(overlay_suite_evidence.get("full_suite_ready"))
    overlay_suite_expected_move_candles = _int(overlay_suite_evidence.get("expected_move_candles_from_projection"), 0)
    overlay_suite_live_reclaim_breakout_ready = _bool(overlay_suite_evidence.get("live_reclaim_breakout_ready"))
    overlay_suite_current_box_candle_count = _int(
        overlay_suite_evidence.get("same_side_current_box_candle_count"),
        0,
    )
    overlay_suite_score = _clip01(overlay_suite_evidence.get("overlay_arsenal_score"), 0.0)
    zones = _merged_rows(
        market.get("zones"),
        snapshot.get("zones"),
        tracking_summary.get("support_resistance_zones"),
        snapshot.get("support_resistance_zones"),
        _mapping(snapshot.get("support_resistance_context")).get("zones"),
        _mapping(snapshot.get("smart_money_context")).get("zones"),
    )
    trendlines = _merged_rows(
        market.get("trendlines"),
        snapshot.get("trendlines"),
        snapshot.get("trendlines_v3"),
        tracking_summary.get("trendlines_v3"),
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
        or overlay_suite_current_entry_touch
        or (active_zone and _zone_current_price_inside(active_zone))
    )
    continuation_confirmed = _bool(
        market_context.get("is_continuation_confirmed")
        or snapshot.get("continuation_confirmed")
        or snapshot.get("current_flow_continuation_ready")
        or (overlay_suite_full_ready and overlay_suite_projection_ready)
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
        or overlay_suite_evidence.get("trendline_ready")
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
    professional_thesis_resolution = _mapping(snapshot.get("professional_thesis_resolution_v3"))
    professional_thesis_state = _upper(professional_thesis_resolution.get("thesis_state"))
    professional_authority_side = _side(professional_thesis_resolution.get("authority_side"))
    professional_opposing_force_reaction = bool(
        professional_thesis_state
        in {
            "SELL_IN_BUY_OPPOSING_FORCE_REACTION",
            "BUY_IN_SELL_OPPOSING_FORCE_REACTION",
            "OPPOSING_FORCE_REACTION",
        }
        and professional_authority_side == side
    )
    professional_bias_resumption_reaction = bool(
        professional_thesis_state
        in {
            "SELL_TREND_RESUMPTION_FROM_SUPPLY",
            "BUY_TREND_RESUMPTION_FROM_DEMAND",
        }
        and professional_authority_side == side
    )
    professional_current_pressure_continuation = bool(
        professional_thesis_state
        in {
            "BUY_CURRENT_PRESSURE_CONTINUATION",
            "SELL_CURRENT_PRESSURE_CONTINUATION",
        }
        and professional_authority_side == side
    )
    current_pressure_continuation_ready = bool(
        _bool(professional_thesis_resolution.get("current_pressure_continuation_ready"))
        and professional_authority_side == side
    )
    current_pressure_defends_against_opposing_force = bool(
        _bool(professional_thesis_resolution.get("current_pressure_defends_against_opposing_force"))
        and professional_authority_side == side
    )
    opposing_force_rejection_confirmed = _bool(
        professional_thesis_resolution.get("opposing_force_rejection_confirmed")
    )
    opposing_force_ok_overridden_by_professional_reaction = False
    if (professional_opposing_force_reaction or professional_bias_resumption_reaction) and not opposing_force_ok:
        opposing_force_ok = True
        opposing_force_ok_overridden_by_professional_reaction = True
    opposing_force_ok_overridden_by_current_pressure = False
    bad_entry_overridden_by_professional_reaction = False
    if (professional_opposing_force_reaction or professional_bias_resumption_reaction) and bad_entry_class in {"AGAINST_GLOBAL_STRUCTURE", "INTO_OPPOSING_FORCE"}:
        playbook_hard_bad_entry = False
        bad_entry_overridden_by_professional_reaction = True
    bad_entry_overridden_by_current_pressure = False
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
    current_pressure_is_current_truth = bool(
        side in {"BUY", "SELL"}
        and same_side_current_leg
        and not current_leg_exhausted
        and (
            professional_current_pressure_continuation
            or current_pressure_defends_against_opposing_force
        )
    )
    if current_pressure_is_current_truth and not opposing_force_ok:
        opposing_force_ok = True
        opposing_force_ok_overridden_by_current_pressure = True
    if current_pressure_is_current_truth and bad_entry_class in {
        "AGAINST_GLOBAL_STRUCTURE",
        "INTO_OPPOSING_FORCE",
        "BUY_HIGH_AFTER_IMPULSE",
        "SELL_LOW_AFTER_DROP",
    }:
        playbook_hard_bad_entry = False
        bad_entry_overridden_by_current_pressure = True
    opposite_current_leg_active = bool(
        side in {"BUY", "SELL"}
        and current_leg_side == _opposite_side(side)
        and current_leg_candle_count >= 3
        and not movement_stage_late
    )
    raw_late_chase = _bool(
        market_context.get("is_late_chase")
        or snapshot.get("late_chase")
        or angle_context.get("late_chase_risk")
        or current_candle.get("too_late")
    )
    preliminary_late_chase = bool((raw_late_chase and not measured_reaction_accepted) or playbook_late_chase_bad_entry)
    history_enter_here = _bool(
        market_context.get("history_would_enter_here")
        or market_context.get("history_would_enter_now")
        or history_context.get("would_have_entered_here")
        or history_context.get("history_would_enter_now")
        or history_context.get("history_would_enter_here")
    )
    history_exit_here = _bool(
        market_context.get("history_would_exit_here")
        or history_context.get("would_have_exited_here")
        or history_context.get("history_would_exit_here")
    )
    raw_conflict_market = _bool(market_context.get("conflict_market") or snapshot.get("conflict_market"))
    conflict_market = bool(
        raw_conflict_market
        and not professional_opposing_force_reaction
        and not professional_bias_resumption_reaction
        and not current_pressure_is_current_truth
    )
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
        execution_lane.get("wave_context"),
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
    live_overlay_reclaim_is_current_truth = bool(
        side in {"BUY", "SELL"}
        and overlay_suite_live_reclaim_breakout_ready
        and not current_leg_exhausted
        and opposing_force_ok
        and (
            aligned_with_primary_bias
            or countertrend_against_global
            or countertrend_against_local
            or countertrend_against_primary
            or primary_bias_side not in {"BUY", "SELL"}
        )
        and max(overlay_suite_current_box_candle_count, overlay_suite_expected_move_candles) >= 3
    )
    short_horizon_side, short_horizon_probability = _short_horizon_side(snapshot)
    price_location_label = _upper(price_location.get("relative_location") or market_context.get("current_location"))
    wrong_side_location_evidence = _wrong_side_entry_location(
        side=side,
        price_location_label=price_location_label,
        active_zone=active_zone,
        opposing_zone=opposing_zone,
    )
    wrong_side_location_role_flip_exception = bool(
        wrong_side_location_evidence
        and (
            role_flip_confirmed
            or (break_of_structure_confirmed and retest_confirmed)
            or live_overlay_reclaim_is_current_truth
        )
    )
    wrong_side_location_blocked = bool(wrong_side_location_evidence and not wrong_side_location_role_flip_exception)
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
            or live_overlay_reclaim_is_current_truth
        )
        and not aligned_with_primary_bias
    )
    counter_reaction_rejection_confirmed = bool(
        side in {"BUY", "SELL"}
        and (
            role_flip_confirmed
            or structure_shift_confirmed
            or (break_of_structure_confirmed and retest_confirmed)
            or (liquidity_sweep_detected and (retest_confirmed or measured_reaction_accepted))
            or (short_horizon_side == side and short_horizon_probability >= 0.58 and measured_reaction_accepted)
            or live_overlay_reclaim_is_current_truth
        )
    )
    professional_counter_reaction_needs_confirmation = bool(
        professional_opposing_force_reaction
        and opposite_current_leg_active
        and not counter_reaction_rejection_confirmed
        and not live_overlay_reclaim_is_current_truth
    )
    late_chase = preliminary_late_chase
    late_chase_softened_by_extreme_reversal = False
    late_chase_softened_by_book_reaction = False
    book_reaction_overrides_late_chase_bad_entry = False
    professional_reaction_is_current_truth = bool(
        (
            professional_opposing_force_reaction
            and side in {"BUY", "SELL"}
            and primary_bias_side == _opposite_side(side)
            and not current_leg_exhausted
            and not professional_counter_reaction_needs_confirmation
        )
        or (
            professional_bias_resumption_reaction
            and side in {"BUY", "SELL"}
            and primary_bias_side == side
            and current_leg_side == _opposite_side(side)
            and not professional_counter_reaction_needs_confirmation
        )
    )
    countertrend_reversal_override = bool(
        role_flip_confirmed
        or structure_shift_confirmed
        or (break_of_structure_confirmed and retest_confirmed)
        or (liquidity_sweep_detected and (retest_confirmed or current_candle_ok or measured_reaction_accepted))
        or failed_continuation_reversal
        or professional_reaction_is_current_truth
        or live_overlay_reclaim_is_current_truth
        or current_pressure_is_current_truth
    )
    professional_counter_leg = bool(
        professional_thesis_state
        in {
            "SELL_IN_BUY_TRADEABLE_COUNTER_LEG",
            "BUY_IN_SELL_TRADEABLE_COUNTER_LEG",
        }
    )
    counter_leg_is_current_truth = bool(
        (
            professional_counter_leg
            and current_leg_side == side
            and side in {"BUY", "SELL"}
            and primary_bias_side == _opposite_side(side)
            and not current_leg_exhausted
            and opposing_force_ok
        )
        or (
            live_overlay_reclaim_is_current_truth
            and side in {"BUY", "SELL"}
            and (
                primary_bias_side == _opposite_side(side)
                or countertrend_against_global
                or countertrend_against_local
                or countertrend_against_primary
            )
        )
    )
    timing_waiting = bool(_upper(timing_mode) != "ENTER_NOW" or not timing_enter_now)
    local_counter_without_reclaim = bool(countertrend_against_local and not countertrend_reversal_override)
    primary_counter_without_reclaim = bool(countertrend_against_primary and not countertrend_reversal_override)
    weak_countertrend_conditions = bool(timing_waiting or not final_score_passed or not lane_authority_ready)
    countertrend_scalp_only = bool(
        side in {"BUY", "SELL"}
        and (primary_counter_without_reclaim or local_counter_without_reclaim)
        and weak_countertrend_conditions
        and not counter_leg_is_current_truth
        and not professional_reaction_is_current_truth
        and not live_overlay_reclaim_is_current_truth
        and not current_pressure_is_current_truth
    )
    large_move_bias_aligned = bool(
        side in {"BUY", "SELL"}
        and not countertrend_scalp_only
        and (
            aligned_with_primary_bias
            or countertrend_reversal_override
            or counter_leg_is_current_truth
            or professional_reaction_is_current_truth
            or live_overlay_reclaim_is_current_truth
            or current_pressure_is_current_truth
        )
    )
    if side not in {"BUY", "SELL"}:
        bias_alignment = "NO_DIRECTION"
    elif countertrend_scalp_only:
        bias_alignment = "COUNTERTREND_SCALP_ONLY"
    elif live_overlay_reclaim_is_current_truth:
        bias_alignment = "LIVE_OVERLAY_RECLAIM_CURRENT_TRUTH"
    elif current_pressure_is_current_truth:
        bias_alignment = professional_thesis_state or "CURRENT_PRESSURE_CONTINUATION"
    elif professional_reaction_is_current_truth:
        bias_alignment = professional_thesis_state
    elif counter_leg_is_current_truth:
        bias_alignment = professional_thesis_state
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
            or counter_leg_is_current_truth
            or professional_reaction_is_current_truth
            or live_overlay_reclaim_is_current_truth
            or current_pressure_is_current_truth
            or overlay_suite_entry_ready
            or overlay_suite_full_ready
            or lane_name in ENTER_NOW_LANES
        )
    )
    movement_supports_book_reaction = bool(
        failed_continuation_reversal
        or counter_leg_is_current_truth
        or professional_reaction_is_current_truth
        or live_overlay_reclaim_is_current_truth
        or current_pressure_is_current_truth
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
    active_zone_distance_norm = _zone_distance_norm(active_zone)
    opposing_zone_distance_norm = _zone_distance_norm(opposing_zone)
    directional_target_room_candles = _distance_to_candle_room(opposing_zone_distance_norm, visible_candle_count)
    force_room_candles = max(0, estimated_candles_to_force)
    overlay_room_candles = max(0, overlay_suite_expected_move_candles)
    professional_min_profit_room_candles = (
        PROFESSIONAL_MIN_PROFIT_ROOM_CANDLES
        if visible_candle_count >= 20
        else PROFESSIONAL_LOW_CONTEXT_MIN_PROFIT_ROOM_CANDLES
    )
    full_suite_room_override_ready = bool(
        live_overlay_reclaim_is_current_truth
        and directional_target_room_candles > 0
        and directional_target_room_candles < professional_min_profit_room_candles
        and max(force_room_candles, overlay_room_candles) >= professional_min_profit_room_candles
    )
    late_chase_bad_entry_full_suite_override = bool(
        late_chase_bad_entry_class
        and live_overlay_reclaim_is_current_truth
        and not current_leg_exhausted
        and (
            full_suite_room_override_ready
            or overlay_room_candles >= professional_min_profit_room_candles
            or force_room_candles >= professional_min_profit_room_candles
        )
    )
    late_chase_reaction_override_allowed = bool(
        measured_reaction_overrides_stale_late_chase
        or late_chase_softened_by_extreme_reversal
        or late_chase_softened_by_book_reaction
        or book_reaction_overrides_late_chase_bad_entry
        or late_chase_bad_entry_full_suite_override
    )
    playbook_hard_bad_entry = bool(
        raw_playbook_hard_bad_entry
        and not bad_entry_overridden_by_professional_reaction
        and not bad_entry_overridden_by_current_pressure
        and not (late_chase_bad_entry_class and late_chase_reaction_override_allowed)
    )
    playbook_late_chase_bad_entry = bool(
        raw_playbook_late_chase_bad_entry
        and not late_chase_reaction_override_allowed
        and not (
            professional_reaction_is_current_truth
            and book_valid_reaction
            and movement_supports_book_reaction
        )
        and not current_pressure_is_current_truth
    )
    late_chase = bool(
        preliminary_late_chase
        and current_move_true_extension
        and not late_chase_reaction_override_allowed
    )
    if full_suite_room_override_ready and overlay_room_candles >= professional_min_profit_room_candles:
        professional_profit_room_candles = overlay_room_candles
        professional_profit_room_source = "full_overlay_suite_projection_overrides_near_zone"
    elif full_suite_room_override_ready:
        professional_profit_room_candles = force_room_candles
        professional_profit_room_source = "opposing_force_room_overrides_near_zone"
    elif directional_target_room_candles > 0:
        professional_profit_room_candles = directional_target_room_candles
        professional_profit_room_source = "directional_opposing_zone"
    elif force_room_candles > 0:
        professional_profit_room_candles = force_room_candles
        professional_profit_room_source = "opposing_force_room"
    elif overlay_room_candles > 0:
        professional_profit_room_candles = overlay_room_candles
        professional_profit_room_source = "full_overlay_suite_projection"
    else:
        professional_profit_room_candles = 0
        professional_profit_room_source = "unknown"
    professional_profit_room_known = professional_profit_room_candles > 0
    professional_profit_room_ok = bool(
        not professional_profit_room_known
        or professional_profit_room_candles >= professional_min_profit_room_candles
    )
    professional_profit_discipline = {
        "schema_version": "PG_PLAYBOOK_PROFIT_DISCIPLINE_V3",
        "passed": professional_profit_room_ok,
        "side": side,
        "minimum_room_candles": professional_min_profit_room_candles,
        "effective_room_candles": professional_profit_room_candles,
        "room_source": professional_profit_room_source,
        "directional_target_room_candles": directional_target_room_candles,
        "opposing_force_room_candles": force_room_candles,
        "overlay_projection_room_candles": overlay_room_candles,
        "near_zone_room_overridden_by_full_suite": full_suite_room_override_ready,
        "active_zone_distance_norm": round(float(active_zone_distance_norm), 4),
        "opposing_zone_distance_norm": round(float(opposing_zone_distance_norm), 4),
        "active_zone_type": _zone_type(active_zone) if active_zone else "",
        "opposing_zone_type": _zone_type(opposing_zone) if opposing_zone else "",
        "rule": "entry needs enough room to the trade-side target; micro-horizon reads are diagnostic only",
    }
    live_overlay_entry_contract_ready = bool(
        live_overlay_reclaim_is_current_truth
        and professional_profit_room_ok
        and not current_leg_exhausted
        and overlay_suite_current_box_candle_count >= 3
        and (overlay_suite_entry_ready or overlay_suite_projection_ready or overlay_suite_full_ready)
    )
    entry_contract_ready = bool(current_candle_ok or live_overlay_entry_contract_ready)
    momentum_request_present = bool(
        lane_name == "MOMENTUM_ACCEPTANCE_ENTRY"
        or _contains_any(market_play.get("primary_play"), ("MOMENTUM", "IMPULSE", "PRESSURE"))
        or _contains_any(market_play.get("play_stage"), ("MOMENTUM", "IMPULSE", "PRESSURE"))
        or _bool(market_context.get("momentum_acceptance") or market_context.get("momentum_acceptance_entry"))
        or _bool(snapshot.get("momentum_acceptance") or snapshot.get("momentum_acceptance_entry"))
    )
    wave_momentum_blockers_raw = wave_context.get("blockers")
    wave_momentum_blockers = (
        [
            str(blocker)
            for blocker in cast(Sequence[Any], wave_momentum_blockers_raw)
            if str(blocker or "").strip()
        ]
        if isinstance(wave_momentum_blockers_raw, Sequence) and not isinstance(wave_momentum_blockers_raw, (str, bytes, bytearray))
        else []
    )
    wave_momentum_context_ready = bool(
        not wave_momentum_blockers
        and (
            wave_context.get("wave_entry_ok")
            or wave_context.get("professional_reaction_ready")
            or wave_context.get("granular_entry_ok")
            or wave_context.get("pullback_reclaim_ready")
            or wave_context.get("breakout_role_flip_ready")
            or wave_context.get("force_reaction_ready")
            or wave_context.get("strong_confluence_override")
            or (
                wave_context.get("continuation_ready")
                and wave_context.get("clear_path_ready")
                and wave_context.get("buy_low_sell_high_ok")
            )
        )
    )
    momentum_structure_context_ready = bool(
        significant_structure_touch
        or inside_trigger
        or retest_confirmed
        or role_flip_confirmed
        or trendline_confluence
        or break_of_structure_confirmed
        or structure_shift_confirmed
        or liquidity_sweep_detected
        or measured_reaction_accepted
        or professional_reaction_is_current_truth
        or counter_leg_is_current_truth
        or live_overlay_reclaim_is_current_truth
        or current_pressure_is_current_truth
        or wave_momentum_context_ready
        or overlay_suite_full_ready
        or overlay_suite_projection_ready
    )
    if not momentum_request_present:
        momentum_interpretation: MomentumInterpretationV3 = "NO_MOMENTUM_DRIVER"
    elif current_leg_exhausted or (same_side_current_leg and current_leg_candle_count >= 14 and not momentum_structure_context_ready):
        momentum_interpretation = "LATE_IMPULSE_RISK"
    elif (countertrend_against_primary or countertrend_against_local or countertrend_against_global) and not (
        countertrend_reversal_override
        or professional_reaction_is_current_truth
        or counter_leg_is_current_truth
        or live_overlay_reclaim_is_current_truth
        or current_pressure_is_current_truth
    ):
        momentum_interpretation = "COUNTERTREND_TRAP_RISK"
    elif (
        professional_reaction_is_current_truth
        or counter_leg_is_current_truth
        or live_overlay_reclaim_is_current_truth
        or current_pressure_is_current_truth
    ):
        momentum_interpretation = "COUNTER_LEG_REACTION_SUPPORT"
    elif role_flip_confirmed or break_of_structure_confirmed or structure_shift_confirmed:
        momentum_interpretation = "STRUCTURE_BREAK_ACCEPTANCE_SUPPORT"
    elif aligned_with_primary_bias and momentum_structure_context_ready and professional_profit_room_ok:
        momentum_interpretation = "TREND_REENTRY_SUPPORT"
    else:
        momentum_interpretation = "RAW_MOMENTUM_DIAGNOSTIC_ONLY"
    momentum_context_ready = bool(
        momentum_request_present
        and professional_profit_room_ok
        and not current_leg_exhausted
        and momentum_interpretation
        in {
            "TREND_REENTRY_SUPPORT",
            "COUNTER_LEG_REACTION_SUPPORT",
            "STRUCTURE_BREAK_ACCEPTANCE_SUPPORT",
        }
    )
    replay_wave_template = _replay_wave_template_v3(
        snapshot=snapshot,
        market=market,
        history_context=history_context,
        tracking_summary=tracking_summary,
        side=side,
        history_enter_here=history_enter_here,
        history_exit_here=history_exit_here,
        inside_trigger=inside_trigger,
        retest_confirmed=retest_confirmed,
        role_flip_confirmed=role_flip_confirmed,
        trendline_confluence=trendline_confluence,
        continuation_confirmed=continuation_confirmed,
        break_of_structure_confirmed=break_of_structure_confirmed,
        structure_shift_confirmed=structure_shift_confirmed,
        liquidity_sweep_detected=liquidity_sweep_detected,
        measured_reaction_accepted=measured_reaction_accepted,
        current_candle_ok=current_candle_ok,
        movement_stage=movement_stage,
        same_side_current_leg=same_side_current_leg,
        current_leg_candle_count=current_leg_candle_count,
        professional_min_profit_room_candles=professional_min_profit_room_candles,
        professional_reaction_is_current_truth=professional_reaction_is_current_truth,
        counter_leg_is_current_truth=counter_leg_is_current_truth,
        opposing_force_ok=opposing_force_ok,
    )
    replay_template_entry_ready = _bool(replay_wave_template.get("entry_alignment_ready"))
    replay_template_late_chase_risk = _bool(replay_wave_template.get("late_template_chase_risk"))
    play_evidence: dict[str, Any] = {
        "side": side,
        "lane": lane_name,
        "single_timeframe_mode": True,
        "multiple_timeframe_required": False,
        "visible_major_local_scaling": True,
        "inside_valid_trigger_zone": inside_trigger,
        "current_candle_ok": current_candle_ok,
        "entry_contract_ready": entry_contract_ready,
        "live_overlay_entry_contract_ready": live_overlay_entry_contract_ready,
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
        "opposing_force_ok_overridden_by_professional_reaction": opposing_force_ok_overridden_by_professional_reaction,
        "opposing_force_ok_overridden_by_current_pressure": opposing_force_ok_overridden_by_current_pressure,
        "bad_entry_overridden_by_professional_reaction": bad_entry_overridden_by_professional_reaction,
        "bad_entry_overridden_by_current_pressure": bad_entry_overridden_by_current_pressure,
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
        "opposite_current_leg_active": opposite_current_leg_active,
        "counter_reaction_rejection_confirmed": counter_reaction_rejection_confirmed,
        "professional_counter_reaction_needs_confirmation": professional_counter_reaction_needs_confirmation,
        "visible_candle_count": visible_candle_count,
        "estimated_candles_to_force": estimated_candles_to_force,
        "professional_profit_discipline": professional_profit_discipline,
        "professional_profit_room_ok": professional_profit_room_ok,
        "professional_profit_room_candles": professional_profit_room_candles,
        "professional_min_profit_room_candles": professional_min_profit_room_candles,
        "professional_profit_room_source": professional_profit_room_source,
        "full_suite_room_override_ready": full_suite_room_override_ready,
        "late_chase_bad_entry_full_suite_override": late_chase_bad_entry_full_suite_override,
        "momentum_request_present": momentum_request_present,
        "momentum_interpretation_v3": momentum_interpretation,
        "momentum_context_ready": momentum_context_ready,
        "momentum_structure_context_ready": momentum_structure_context_ready,
        "wave_momentum_context_ready": wave_momentum_context_ready,
        "wave_momentum_blockers": wave_momentum_blockers,
        "directional_target_room_candles": directional_target_room_candles,
        "opposing_force_room_candles": force_room_candles,
        "active_zone_distance_norm": round(float(active_zone_distance_norm), 4),
        "opposing_zone_distance_norm": round(float(opposing_zone_distance_norm), 4),
        "candle_movement_summary": candle_movement_context.get("summary"),
        "candle_movement_context_v3": candle_movement_context,
        "history_enter_here": history_enter_here,
        "history_exit_here": history_exit_here,
        "replay_wave_template_v3": replay_wave_template,
        "replay_template_entry_ready": replay_template_entry_ready,
        "replay_template_late_chase_risk": replay_template_late_chase_risk,
        "overlay_suite_evidence_v3": overlay_suite_evidence,
        "overlay_suite_entry_ready": overlay_suite_entry_ready,
        "overlay_suite_current_entry_touch": overlay_suite_current_entry_touch,
        "overlay_suite_projection_ready": overlay_suite_projection_ready,
        "overlay_suite_target_ready": overlay_suite_target_ready,
        "overlay_suite_full_ready": overlay_suite_full_ready,
        "overlay_suite_expected_move_candles": overlay_suite_expected_move_candles,
        "overlay_suite_score": round(float(overlay_suite_score), 4),
        "conflict_market": conflict_market,
        "raw_conflict_market": raw_conflict_market,
        "pullback_not_confirmed": pullback_not_confirmed,
        "middle_safe": middle_safe,
        "short_horizon_side": short_horizon_side,
        "short_horizon_probability": round(float(short_horizon_probability), 4),
        "wrong_side_location_evidence": wrong_side_location_evidence,
        "wrong_side_location_role_flip_exception": wrong_side_location_role_flip_exception,
        "wrong_side_location_blocked": wrong_side_location_blocked,
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
        "professional_thesis_state": professional_thesis_state,
        "professional_counter_leg": professional_counter_leg,
        "professional_opposing_force_reaction": professional_opposing_force_reaction,
        "professional_bias_resumption_reaction": professional_bias_resumption_reaction,
        "professional_current_pressure_continuation": professional_current_pressure_continuation,
        "current_pressure_continuation_ready": current_pressure_continuation_ready,
        "current_pressure_defends_against_opposing_force": current_pressure_defends_against_opposing_force,
        "opposing_force_rejection_confirmed": opposing_force_rejection_confirmed,
        "current_pressure_is_current_truth": current_pressure_is_current_truth,
        "professional_reaction_is_current_truth": professional_reaction_is_current_truth,
        "counter_leg_is_current_truth": counter_leg_is_current_truth,
        "live_overlay_reclaim_is_current_truth": live_overlay_reclaim_is_current_truth,
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
    market_phase = _market_phase_v3(
        side=side,
        primary_bias_side=primary_bias_side,
        current_leg_side=current_leg_side,
        current_leg_candle_count=current_leg_candle_count,
        movement_stage=movement_stage,
        professional_counter_leg=bool(
            counter_leg_is_current_truth
            or professional_reaction_is_current_truth
            or live_overlay_reclaim_is_current_truth
            or current_pressure_is_current_truth
        ),
        countertrend_reversal_override=countertrend_reversal_override,
        conflict_market=conflict_market,
    )
    play_evidence["market_phase_v3"] = market_phase
    playbook = _select_playbook(
        side=side,
        lane_name=lane_name,
        market_play=market_play,
        market_context=market_context,
        evidence=play_evidence,
    )
    blockers: list[dict[str, Any]] = []
    soft_warnings: list[dict[str, Any]] = []
    non_negotiable_blocker_fields = {
        "api_health",
        "cache_state",
        "candidate_invalidated",
        "candidate_side",
        "live_integrity",
        "runtime",
        "runtime_model_health",
        "wrong_side_location",
    }

    def add_blocker(field: str, received: Any, required: Any, reason: str, *, hard: bool = False) -> None:
        field_key = str(field or "").strip().lower()
        if field_key not in non_negotiable_blocker_fields:
            soft_warnings.append(
                {
                    "field": field,
                    "received": received,
                    "required": required,
                    "effect": "overlay_truth_authority_kept; strategy caution downgraded from blocker",
                    "reason": reason,
                    "former_hard": bool(hard),
                }
            )
            return
        blockers.append(_build_blocker(field, received, required, reason, hard=hard))

    def add_warning(field: str, received: Any, effect: str) -> None:
        soft_warnings.append({"field": field, "received": received, "effect": effect})

    measured_reaction_can_override_timing = bool(
        measured_reaction_accepted
        and (
            not countertrend_scalp_only
            or counter_leg_is_current_truth
            or professional_reaction_is_current_truth
            or live_overlay_reclaim_is_current_truth
            or current_pressure_is_current_truth
        )
        and (large_move_bias_aligned or countertrend_reversal_override or lane_authority_ready)
    )
    timing_supportive = bool(
        (_upper(timing_mode) == "ENTER_NOW" and timing_enter_now)
        or measured_reaction_can_override_timing
        or professional_reaction_is_current_truth
        or live_overlay_reclaim_is_current_truth
        or current_pressure_is_current_truth
        or (
            entry_contract_ready
            and (
                not countertrend_scalp_only
                or counter_leg_is_current_truth
                or professional_reaction_is_current_truth
                or live_overlay_reclaim_is_current_truth
                or current_pressure_is_current_truth
            )
            and (
                inside_trigger
                or retest_confirmed
                or continuation_confirmed
                or failed_continuation_reversal
                or counter_leg_is_current_truth
                or professional_reaction_is_current_truth
                or live_overlay_reclaim_is_current_truth
                or current_pressure_is_current_truth
                or replay_template_entry_ready
                or overlay_suite_entry_ready
                or (overlay_suite_full_ready and overlay_suite_target_ready)
            )
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
        wave_context_ready=wave_momentum_context_ready,
        timing_supportive=timing_supportive,
        current_candle_ok=entry_contract_ready,
        momentum_context_ready=momentum_context_ready,
        replay_template_entry_ready=replay_template_entry_ready,
        overlay_suite_entry_ready=overlay_suite_entry_ready,
        overlay_suite_full_ready=overlay_suite_full_ready,
        late_chase=late_chase,
        opposing_force_ok=opposing_force_ok,
        conflict_market=conflict_market,
        structural_extreme_reversal=bool(
            failed_continuation_reversal
            or professional_reaction_is_current_truth
            or live_overlay_reclaim_is_current_truth
            or current_pressure_is_current_truth
        ),
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
    if wrong_side_location_blocked:
        required_area = "demand/support or accepted role-flip support" if side == "BUY" else "supply/resistance or accepted role-flip resistance"
        add_blocker(
            "wrong_side_location",
            wrong_side_location_evidence,
            required_area,
            (
                "Location safety blocked the package: PhoenixGuard does not buy into active resistance/supply "
                "or sell into active support/demand unless the area has already been accepted as a role-flip/reclaim."
            ),
            hard=True,
        )
    if professional_counter_reaction_needs_confirmation:
        add_blocker(
            "professional_counter_reaction_confirmation",
            {
                "side": side,
                "current_leg_side": current_leg_side,
                "current_leg_candle_count": current_leg_candle_count,
                "movement_stage": movement_stage,
                "professional_thesis_state": professional_thesis_state,
            },
            "live rejection, role flip, structure shift, break/retest, sweep reaction, or current leg turning with the entry",
            (
                "The book read found an opposing-force reaction, but the live candle leg is still driving against "
                "the proposed entry. Keep this as PREPARE until the rejection is confirmed by structure or the "
                "current leg turns with the trade side."
            ),
            hard=True,
        )
    if conflict_market:
        add_blocker("conflict_market", True, False, "BUY and SELL evidence conflict; do not force a trade.")
    elif raw_conflict_market and current_pressure_is_current_truth:
        add_warning(
            "conflict_market",
            "SOFTENED_BY_CURRENT_PRESSURE",
            "both sides have evidence, but current pressure is defended while the opposite reaction lacks rejection proof",
        )
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
    elif bad_entry_overridden_by_professional_reaction:
        add_warning(
            "bad_entry_filter.class",
            bad_entry_class,
            "against-global warning softened because tested opposing-force reaction is the active sell/buy opportunity",
        )
    elif bad_entry_overridden_by_current_pressure:
        add_warning(
            "bad_entry_filter.class",
            bad_entry_class,
            "location warning softened because current pressure is defended and the opposite reaction has no rejection proof",
        )
    elif late_chase_bad_entry_full_suite_override:
        add_warning(
            "bad_entry_filter.class",
            bad_entry_class,
            "late-chase warning softened because current-box/reclaim/projection evidence shows a live path with professional room",
        )
    if momentum_request_present and not momentum_context_ready:
        momentum_reason = (
            "Momentum is diagnostic only until it is tied to a current zone, wick rejection, retest/reclaim, "
            "role flip, breakout acceptance, or professional opposing-force reaction with enough room."
        )
        if lane_name == "MOMENTUM_ACCEPTANCE_ENTRY" or momentum_interpretation in {"COUNTERTREND_TRAP_RISK", "LATE_IMPULSE_RISK"}:
            add_blocker(
                "momentum_context_ready",
                momentum_interpretation,
                "TREND_REENTRY_SUPPORT or COUNTER_LEG_REACTION_SUPPORT or STRUCTURE_BREAK_ACCEPTANCE_SUPPORT",
                momentum_reason,
                hard=True,
            )
        else:
            add_warning("momentum_context_ready", momentum_interpretation, momentum_reason)
    if (
        countertrend_scalp_only
        and not counter_leg_is_current_truth
        and not professional_reaction_is_current_truth
        and not current_pressure_is_current_truth
    ):
        add_blocker(
            "bias_alignment",
            bias_alignment,
            "primary-bias aligned entry or confirmed reclaim/role-flip reversal",
            "Local reaction is only a minor countertrend/scalp read; wait for bias-aligned continuation or a confirmed reclaim/role flip.",
            hard=True,
        )
    elif (
        countertrend_against_global
        or countertrend_against_local
        or countertrend_against_primary
    ) and not countertrend_reversal_override and not counter_leg_is_current_truth and not professional_reaction_is_current_truth and not current_pressure_is_current_truth:
        add_blocker(
            "bias_alignment",
            bias_alignment,
            "primary-bias aligned entry or confirmed reclaim/role-flip reversal",
            "Countertrend read remains watch-only until reclaim, role flip, or major structure invalidation confirms adaptation.",
            hard=True,
        )
    elif counter_leg_is_current_truth:
        add_warning(
            "bias_alignment",
            bias_alignment,
            "professional_counter_leg_is_allowed_because_current_leg_truth_overrides_bias_marriage",
        )
    elif professional_reaction_is_current_truth:
        add_warning(
            "bias_alignment",
            bias_alignment,
            "professional_opposing_force_reaction_is_allowed_because_tested_resistance_or_support_overrides_bias_marriage",
        )
    elif current_pressure_is_current_truth:
        add_warning(
            "bias_alignment",
            bias_alignment,
            "current_pressure_continuation_is_allowed_because_the_live_leg_is_defended_and_opposite_rejection_is_unproven",
        )
    if history_exit_here:
        add_blocker("history_exit_here", True, False, "Historical analog says this area is closer to exit/protection than entry.")
    replay_template_transition_has_structure = bool(
        retest_confirmed
        or role_flip_confirmed
        or live_overlay_reclaim_is_current_truth
        or professional_reaction_is_current_truth
        or counter_leg_is_current_truth
        or current_pressure_is_current_truth
        or (book_valid_reaction and (inside_trigger or trendline_confluence or significant_structure_touch))
    )
    replay_template_late_chase_softened_by_current_transition = bool(
        replay_template_late_chase_risk
        and not current_leg_exhausted
        and professional_profit_room_ok
        and replay_template_transition_has_structure
    )
    if replay_template_late_chase_risk and not replay_template_late_chase_softened_by_current_transition:
        add_blocker(
            "replay_wave_template.phase",
            {
                "current_leg_candle_count": current_leg_candle_count,
                "movement_stage": movement_stage,
                "best_expected_move_candles": replay_wave_template.get("best_expected_move_candles"),
                "late_threshold_candles": replay_wave_template.get("late_threshold_candles"),
            },
            "fresh replay-quality entry phase",
            "Replay would-have-entered/exited template says this is already mid-leg or late; wait for the next clean entry window.",
            hard=True,
        )
    elif replay_template_late_chase_softened_by_current_transition:
        add_warning(
            "replay_wave_template.phase",
            {
                "current_leg_candle_count": current_leg_candle_count,
                "movement_stage": movement_stage,
                "best_expected_move_candles": replay_wave_template.get("best_expected_move_candles"),
            },
            (
                "replay template is mid-leg, but current source-truth reclaim/retest/reaction and path room "
                "are active; treat it as strategy caution instead of a hard veto"
            ),
        )
    elif _int(replay_wave_template.get("entry_exit_templates"), 0) > 0 and not replay_template_entry_ready:
        add_warning(
            "replay_wave_template",
            "NOT_AT_ENTRY_WINDOW",
            "wait for a replay-quality would-have-entered style entry window, not the middle of a leg",
        )
    if side in {"BUY", "SELL"} and professional_profit_room_known and not professional_profit_room_ok:
        add_blocker(
            "professional_profit_room",
            professional_profit_room_candles,
            f">= {professional_min_profit_room_candles} candle(s)",
            (
                "Projected path is too short for a professional thesis; keep the short-horizon read as study "
                "and wait for a larger target path, pullback, or new structure reaction."
            ),
            hard=True,
        )
    opposing_force_soft_warning = bool(
        not opposing_force_ok
        and professional_profit_room_ok
        and (
            professional_profit_room_known
            or live_overlay_reclaim_is_current_truth
            or professional_reaction_is_current_truth
            or counter_leg_is_current_truth
            or current_pressure_is_current_truth
        )
    )
    if not opposing_force_ok and not opposing_force_soft_warning:
        add_blocker("opposing_force_ok", False, True, "Nearest opposing force is too close for a clean path.", hard=True)
    elif opposing_force_soft_warning:
        add_warning(
            "opposing_force_ok",
            False,
            "opposing-force warning softened because full-suite projection/professional reaction still shows usable path room",
        )
    play_evidence["replay_template_late_chase_softened_by_current_transition"] = (
        replay_template_late_chase_softened_by_current_transition
    )
    play_evidence["opposing_force_soft_warning"] = opposing_force_soft_warning
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
    if not current_candle_ok and live_overlay_entry_contract_ready:
        add_warning(
            "current_candle.entry_allowed",
            "LIVE_OVERLAY_ENTRY_CONTRACT",
            "current candle contract was not explicit, but current-box/reclaim/projection room supplied a live aggressive-entry contract",
        )
    elif not current_candle_ok:
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
            or professional_reaction_is_current_truth
            or live_overlay_reclaim_is_current_truth
            or current_pressure_is_current_truth
            or replay_template_entry_ready
            or overlay_suite_full_ready
            or overlay_suite_entry_ready
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
        entry_contract_ready
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
            or professional_reaction_is_current_truth
            or live_overlay_reclaim_is_current_truth
            or current_pressure_is_current_truth
            or replay_template_entry_ready
            or overlay_suite_entry_ready
            or overlay_suite_full_ready
            or _upper(candle_reaction.get("reaction_type")) not in {"", "NO_REACTION", "EXHAUSTION"}
            or lane_name in ENTER_NOW_LANES
        )
    )
    hard_blockers = [row for row in blockers if _bool(row.get("hard"))]
    if side not in {"BUY", "SELL"}:
        state: BookMaturityState = "NO_OPPORTUNITY"
    elif candidate_invalidated:
        state = "INVALIDATED"
    elif conflict_market and not (current_pressure_is_current_truth or professional_reaction_is_current_truth or live_overlay_reclaim_is_current_truth):
        state = "VALID_WATCH"
    elif hard_blockers:
        state = "PREPARE"
    elif not has_context:
        state = "EARLY_FORMING"
    elif entry_profile == "WATCH_ONLY" or not reaction_ready:
        state = "VALID_WATCH"
    elif blockers:
        state = "PREPARE"
    elif bool(reaction_ready and not hard_blockers):
        state = "ENTER_NOW"
    else:
        state = "PREPARE"

    astar_confirmation_score = max(
        _clip01(lane_score, 0.0),
        overlay_suite_score,
        _clip01(candle_reaction.get("reaction_quality"), 0.0),
        0.76
        if (
            (retest_confirmed or role_flip_confirmed or live_overlay_reclaim_is_current_truth)
            and entry_contract_ready
        )
        else 0.0,
    )
    active_zone_type = _zone_type(active_zone).upper() if active_zone else ""
    opposing_zone_type = _zone_type(opposing_zone).upper() if opposing_zone else ""
    astar_zone_role = ""
    if side == "BUY" and (
        "RESISTANCE" in price_location_label
        or "SUPPLY" in price_location_label
        or (opposing_zone_type in {"SUPPLY", "RESISTANCE"} and (role_flip_confirmed or break_of_structure_confirmed))
    ):
        astar_zone_role = "resistance"
    elif side == "SELL" and (
        "SUPPORT" in price_location_label
        or "DEMAND" in price_location_label
        or (opposing_zone_type in {"DEMAND", "SUPPORT"} and (role_flip_confirmed or break_of_structure_confirmed))
    ):
        astar_zone_role = "support"
    elif active_zone_type in {"DEMAND", "SUPPORT"}:
        astar_zone_role = "support"
    elif active_zone_type in {"SUPPLY", "RESISTANCE"}:
        astar_zone_role = "resistance"
    astar_decision_input: dict[str, Any] = {
        "candidate_side": side,
        "requested_state": "ENTER_NOW" if reaction_ready else state,
        "book_strategy_state": state,
        "timing_mode": "ENTER_NOW" if timing_supportive else timing_mode,
        "entry_now_allowed": timing_supportive,
        "current_candle_accepted": entry_contract_ready,
        "current_candle_entry_allowed": entry_contract_ready,
        "pullback_held": bool(pullback_confirmed or retest_confirmed),
        "pullback_reclaimed": bool(role_flip_confirmed or live_overlay_reclaim_is_current_truth),
        "pullback_failed": bool(candidate_invalidated),
        "market_location": price_location_label or ("MID_RANGE" if middle_safe else ""),
        "range_position": price_location.get("range_position") or price_location.get("location_percentile"),
        "confirmation_score": astar_confirmation_score,
        "zone_role": astar_zone_role,
        "accepted_above": bool(side == "BUY" and (role_flip_confirmed or break_of_structure_confirmed or live_overlay_reclaim_is_current_truth)),
        "support_rejected": bool(side == "SELL" and (role_flip_confirmed or break_of_structure_confirmed or live_overlay_reclaim_is_current_truth)),
        "retest_held": retest_confirmed,
        "breakout_confirmation": bool(side == "BUY" and break_of_structure_confirmed),
        "breakdown_confirmation": bool(side == "SELL" and break_of_structure_confirmed),
        "strong_confirmation": bool(
            book_valid_reaction
            or professional_reaction_is_current_truth
            or counter_leg_is_current_truth
            or current_pressure_is_current_truth
        ),
        "hard_blockers": hard_blockers,
        "blockers": blockers,
        "soft_warnings": soft_warnings,
    }
    astar_decision_ledger = build_candidate_decision_ledger_v3(astar_decision_input)
    astar_final_state = _upper(astar_decision_ledger.get("final_state"))
    astar_promoted_from = state
    astar_transition_promoted_enter_now = bool(
        astar_final_state == "ENTER_NOW"
        and state in {"VALID_WATCH", "PREPARE", "EARLY_FORMING"}
        and not hard_blockers
        and has_context
        and reaction_ready
        and entry_contract_ready
        and score_supportive
        and professional_profit_room_ok
        and not conflict_market
        and not candidate_invalidated
        and not current_leg_exhausted
    )
    if astar_transition_promoted_enter_now:
        state = "ENTER_NOW"
        add_warning(
            "authorization_survival_trace_v3",
            astar_promoted_from,
            "A* state-transition controller promoted the setup because pullback/reclaim/current-candle/path evidence survived without true hard blockers",
        )
    play_evidence["astar_decision_state_v3"] = astar_decision_ledger
    play_evidence["astar_transition_promoted_enter_now"] = astar_transition_promoted_enter_now
    play_evidence["astar_transition_promoted_from"] = astar_promoted_from

    evidence_score = 0.0
    evidence_score += 0.12 if inside_trigger else 0.0
    evidence_score += 0.12 if current_candle_ok else 0.0
    evidence_score += 0.10 if retest_confirmed else 0.0
    evidence_score += 0.10 if continuation_confirmed else 0.0
    evidence_score += 0.08 if role_flip_confirmed else 0.0
    evidence_score += 0.08 if trendline_confluence else 0.0
    evidence_score += 0.08 if break_of_structure_confirmed or structure_shift_confirmed else 0.0
    evidence_score += 0.10 if failed_continuation_reversal else 0.0
    evidence_score += 0.10 if counter_leg_is_current_truth else 0.0
    evidence_score += 0.10 if professional_reaction_is_current_truth else 0.0
    evidence_score += 0.10 if live_overlay_reclaim_is_current_truth else 0.0
    evidence_score += 0.10 if current_pressure_is_current_truth else 0.0
    evidence_score += 0.04 if momentum_context_ready else 0.0
    evidence_score += 0.08 if replay_template_entry_ready else 0.0
    evidence_score += 0.08 if overlay_suite_full_ready else 0.0
    evidence_score += 0.06 if overlay_suite_entry_ready and overlay_suite_target_ready else 0.0
    evidence_score += 0.08 * overlay_suite_score
    evidence_score += 0.08 if _bool(significant_structure.get("significant")) else 0.0
    evidence_score += 0.10 * _clip01(candle_reaction.get("reaction_quality"), 0.0)
    evidence_score += 0.06 if entry_profile in {"AGGRESSIVE_SNIPER", "CONSERVATIVE_RETEST", "CONTINUATION_RETEST", "REVERSAL_RECLAIM"} else 0.0
    evidence_score += 0.10 if large_move_bias_aligned else 0.0
    evidence_score += 0.10 if opposing_force_ok else -0.08
    evidence_score += 0.12 if final_score_passed else -0.08
    evidence_score += 0.10 if timing_enter_now and _upper(timing_mode) == "ENTER_NOW" else -0.05
    evidence_score -= 0.16 if countertrend_scalp_only else 0.0
    evidence_score -= 0.18 if late_chase else 0.0
    evidence_score -= 0.12 if replay_template_late_chase_risk else 0.0
    evidence_score -= 0.14 if history_exit_here else 0.0
    evidence_score -= 0.12 if conflict_market else 0.0
    evidence_score -= 0.10 if momentum_interpretation in {"COUNTERTREND_TRAP_RISK", "LATE_IMPULSE_RISK"} else 0.0
    evidence_score -= 0.04 if momentum_interpretation == "RAW_MOMENTUM_DIAGNOSTIC_ONLY" else 0.0
    confidence = _clip01(0.25 + evidence_score - (0.10 * len(hard_blockers)) - (0.025 * max(0, len(blockers) - len(hard_blockers))))
    if state == "ENTER_NOW" and (
        not countertrend_scalp_only
        or counter_leg_is_current_truth
        or professional_reaction_is_current_truth
        or live_overlay_reclaim_is_current_truth
    ):
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
        "market_phase_v3": market_phase,
        "reaction_type": candle_reaction.get("reaction_type"),
        "structure_type": significant_structure.get("structure_type"),
        "strategy_combo": strategy_combo,
        "professional_profit_discipline": professional_profit_discipline,
        "astar_decision_state_v3": astar_decision_ledger,
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
        "market_phase_v3": market_phase,
        "reaction_type": candle_reaction.get("reaction_type"),
        "significant_structure": significant_structure,
        "candlestick_reaction": candle_reaction,
        "strategy_combo": strategy_combo,
        "astar_decision_state_v3": astar_decision_ledger,
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
            "micro_horizon_reads_are_diagnostic_not_trade_thesis",
            "profitability_requires_room_to_directional_target_zone",
            "momentum_is_context_confirmation_not_standalone_authority",
            "raw_impulse_against_structure_is_trap_risk_until_reclaim_or_rejection_confirms",
            "replay_would_have_entered_templates_are_live_entry_quality_maps",
            "do_not_enter_mid_leg_when_replay_template_says_entry_window_was_earlier",
            "full_overlay_suite_feeds_the_playbook_even_when_gui_layers_are_lazy_loaded",
            "entry_target_invalidation_projection_angle_and_memory_paths_are_not_optional_for_strategy_reasoning",
            "major_visible_bias_dominates_inner_reactions",
            "buy_and_sell_books_run_in_parallel",
            "professional_counter_legs_are_not_scalps_when_current_leg_truth_is_visible",
            "opposing_force_reactions_need_live_rejection_confirmation_before_manual_alert",
            "defended_current_pressure_can_soften_buy_sell_conflict_until_opposite_rejection_is_proven",
            "countertrend_scalps_require_reclaim_role_flip_major_invalidation_or_professional_counter_leg",
            "do_not_require_multiple_timeframes",
        ],
    }


__all__ = [
    "BOOK_ENTRY_PROFILES",
    "BOOK_REACTION_TYPES",
    "MARKET_PHASES_V3",
    "BOOK_STRATEGY_MATURITY_STATES",
    "BOOK_STRATEGY_PLAYBOOKS",
    "BOOK_STRATEGY_SCHEMA_VERSION",
    "evaluate_book_strategy_master_v3",
]
