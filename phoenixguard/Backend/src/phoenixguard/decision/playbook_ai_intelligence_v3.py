from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence, cast


PG_PLAYBOOK_AI_INTELLIGENCE_SCHEMA_VERSION = "PG_PLAYBOOK_AI_INTELLIGENCE_V3"
SIDES: tuple[str, str] = ("BUY", "SELL")


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


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
        parsed = int(_float(value, float(default)))
    except (TypeError, ValueError, OverflowError):
        return int(default)
    return int(parsed)


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("BUY") or text in {"BULL", "BULLISH", "GREEN", "UP", "CALL", "LONG"}:
        return "BUY"
    if text.startswith("SELL") or text in {"BEAR", "BEARISH", "RED", "MAGENTA", "DOWN", "PUT", "SHORT"}:
        return "SELL"
    return "HOLD"


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "ready", "passed"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "blocked", "failed"}:
            return False
    return bool(value)


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper()
    return text or default


def _round4(value: Any, default: float = 0.0) -> float:
    return round(float(_float(value, default)), 4)


def _opposite(side: str) -> str:
    if side == "BUY":
        return "SELL"
    if side == "SELL":
        return "BUY"
    return "HOLD"


def _ratio(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    bottom = _float(denominator, 0.0)
    if bottom <= 0.0:
        return float(default)
    return _clip01(_float(numerator, 0.0) / bottom)


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        mapped = _mapping(value)
        if mapped:
            return mapped
    return {}


def _first_side(*values: Any) -> str:
    for value in values:
        side = _side(value)
        if side in SIDES:
            return side
    return "HOLD"


def _duration_text(seconds: int) -> str:
    safe_seconds = max(0, int(seconds))
    if safe_seconds < 60:
        return f"{safe_seconds}s"
    minutes = safe_seconds // 60
    remainder = safe_seconds % 60
    if minutes < 60:
        return f"{minutes}m {remainder}s" if remainder else f"{minutes}m"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _horizon_class(candle_count: int) -> str:
    if candle_count <= 0:
        return "WAIT_FOR_CONFIRMATION"
    if candle_count <= 2:
        return "SNIPER_1_2_CANDLES"
    if candle_count <= 5:
        return "INTRAMOVE_3_5_CANDLES"
    if candle_count <= 12:
        return "STRUCTURE_LEG_6_12_CANDLES"
    return "EXTENDED_THESIS_13_PLUS_CANDLES"


def _contains_any(value: Any, tokens: Sequence[str]) -> bool:
    text = _upper(value)
    return any(token in text for token in tokens)


def _dedupe_rules(rules: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for rule in rules:
        text = str(rule or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _market_context(snapshot: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any]:
    intelligence = _first_mapping(
        market.get("market_intelligence_v3"),
        market.get("market_intelligence"),
        market.get("market_context_v3"),
        market.get("market_context"),
        snapshot.get("market_intelligence_v3"),
        snapshot.get("market_intelligence"),
        snapshot.get("market_context_v3"),
        snapshot.get("market_context"),
    )
    if not intelligence:
        intelligence = dict(market)

    return {
        "source_version": intelligence.get("version") or intelligence.get("schema_version") or "",
        "model_council": _first_mapping(intelligence.get("model_council"), market.get("model_council")),
        "regime": _first_mapping(intelligence.get("regime"), market.get("regime"), snapshot.get("regime")),
        "price_location": _first_mapping(
            intelligence.get("price_location"),
            market.get("price_location"),
            snapshot.get("price_location"),
        ),
        "reasoning_arbitration": _first_mapping(
            intelligence.get("reasoning_arbitration"),
            market.get("reasoning_arbitration"),
            snapshot.get("reasoning_arbitration"),
        ),
        "market_reality": _first_mapping(
            intelligence.get("market_reality"),
            market.get("market_reality"),
            snapshot.get("market_reality"),
        ),
        "classifiers": _first_mapping(
            intelligence.get("classifiers"),
            _mapping(intelligence.get("market_classifiers")).get("classifiers"),
            market.get("classifiers"),
            snapshot.get("classifiers"),
        ),
        "market_classifiers": _first_mapping(
            intelligence.get("market_classifiers"),
            market.get("market_classifiers"),
            snapshot.get("market_classifiers"),
        ),
        "entry_quality": _first_mapping(
            intelligence.get("entry_quality"),
            market.get("entry_quality"),
            snapshot.get("entry_quality"),
        ),
        "path_risk": _first_mapping(
            intelligence.get("path_risk"),
            market.get("path_risk"),
            snapshot.get("path_risk"),
        ),
        "trade_permission": _first_mapping(
            intelligence.get("trade_permission"),
            market.get("trade_permission"),
            snapshot.get("trade_permission"),
        ),
        "raw_keys": sorted(str(key) for key in intelligence.keys()),
    }


def _resolve_inputs(
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    book_strategy: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _mapping(book_strategy.get("evidence"))
    overlay_suite = _first_mapping(
        evidence.get("overlay_suite_evidence_v3"),
        book_strategy.get("overlay_suite_evidence_v3"),
        snapshot.get("overlay_suite_evidence_v3"),
        market.get("overlay_suite_evidence_v3"),
    )
    candle_context = _first_mapping(
        book_strategy.get("candle_movement_context_v3"),
        evidence.get("candle_movement_context_v3"),
        snapshot.get("candle_movement_context_v3"),
        market.get("candle_movement_context_v3"),
        book_strategy.get("candle_movement_context"),
        snapshot.get("candle_movement_context"),
        market.get("candle_movement_context"),
    )
    opportunity_maturity = _mapping(book_strategy.get("opportunity_maturity"))
    allowance = _mapping(book_strategy.get("allowance_package"))
    professional_plan = _first_mapping(
        book_strategy.get("professional_trade_plan"),
        evidence.get("professional_trade_plan"),
        opportunity_maturity.get("professional_trade_plan"),
        allowance.get("professional_trade_plan"),
        snapshot.get("professional_trade_plan"),
        market.get("professional_trade_plan"),
    )
    return {
        "evidence": evidence,
        "overlay_suite": overlay_suite,
        "candle_context": candle_context,
        "professional_plan": professional_plan,
        "market_context": _market_context(snapshot, market),
    }


def _bias_sides(evidence: Mapping[str, Any], professional_plan: Mapping[str, Any]) -> dict[str, str]:
    trend_alignment = _mapping(professional_plan.get("trend_alignment"))
    hierarchy = _mapping(professional_plan.get("trade_hierarchy"))
    big_picture = _mapping(hierarchy.get("big_picture"))
    local_distribution = _mapping(hierarchy.get("local_distribution"))
    return {
        "global_side": _first_side(evidence.get("global_side"), trend_alignment.get("global_side"), big_picture.get("global_side")),
        "local_side": _first_side(evidence.get("local_side"), trend_alignment.get("local_side"), local_distribution.get("local_side")),
        "dominant_side": _first_side(evidence.get("dominant_side"), trend_alignment.get("dominant_side"), big_picture.get("dominant_side")),
        "primary_bias_side": _first_side(
            evidence.get("primary_bias_side"),
            trend_alignment.get("primary_bias_side"),
            big_picture.get("side"),
        ),
        "professional_authority_side": _first_side(
            professional_plan.get("authority_side"),
            professional_plan.get("side"),
        ),
    }


def _overlay_layer_score(overlay_suite: Mapping[str, Any]) -> dict[str, Any]:
    rows_total = _int(overlay_suite.get("rows_total"), 0)
    actionable_count = _int(overlay_suite.get("actionable_count"), 0)
    same_side_actionable = _int(overlay_suite.get("same_side_actionable_count"), 0)
    entry_count = _int(overlay_suite.get("entry_window_count"), 0)
    same_side_entry_count = _int(overlay_suite.get("same_side_entry_window_count"), 0)
    target_count = _int(overlay_suite.get("target_window_count"), 0)
    invalidation_count = _int(overlay_suite.get("invalidation_count"), 0)
    projection_count = _int(overlay_suite.get("prediction_path_count"), 0)
    structure_count = _int(overlay_suite.get("structure_box_count"), 0)
    trendline_count = _int(overlay_suite.get("trendline_count"), 0)
    replay_count = _int(overlay_suite.get("replay_path_count"), 0)
    memory_count = _int(overlay_suite.get("memory_path_count"), 0)
    angle_count = _int(overlay_suite.get("angle_vector_count"), 0)
    opposing_force_count = _int(overlay_suite.get("opposing_force_count"), 0)
    overlay_score = _clip01(overlay_suite.get("overlay_arsenal_score"), 0.0)

    entry_ready = _bool(overlay_suite.get("entry_ready")) or same_side_entry_count > 0
    target_ready = _bool(overlay_suite.get("target_ready")) or target_count > 0 or opposing_force_count > 0
    invalidation_ready = _bool(overlay_suite.get("invalidation_ready")) or invalidation_count > 0
    projection_ready = _bool(overlay_suite.get("projection_ready")) or projection_count > 0
    structure_ready = _bool(overlay_suite.get("structure_ready")) or structure_count > 0
    trendline_ready = _bool(overlay_suite.get("trendline_ready")) or trendline_count > 0
    angle_ready = _bool(overlay_suite.get("angle_ready")) or angle_count > 0
    replay_ready = replay_count > 0 or memory_count > 0
    path_ready = bool(projection_ready or structure_ready or trendline_ready or replay_ready)
    full_suite_ready = _bool(overlay_suite.get("full_suite_ready")) or bool(entry_ready and target_ready and path_ready)
    current_entry_touch = _bool(overlay_suite.get("current_entry_touch")) or _int(overlay_suite.get("current_entry_touch_count"), 0) > 0

    return {
        "rows_total": rows_total,
        "actionable_count": actionable_count,
        "same_side_actionable_count": same_side_actionable,
        "opposite_actionable_count": max(0, actionable_count - same_side_actionable),
        "entry_window_count": entry_count,
        "same_side_entry_window_count": same_side_entry_count,
        "target_window_count": target_count,
        "invalidation_count": invalidation_count,
        "prediction_path_count": projection_count,
        "structure_box_count": structure_count,
        "trendline_count": trendline_count,
        "replay_path_count": replay_count,
        "memory_path_count": memory_count,
        "angle_vector_count": angle_count,
        "opposing_force_count": opposing_force_count,
        "overlay_arsenal_score": _round4(overlay_score),
        "entry_ready": entry_ready,
        "current_entry_touch": current_entry_touch,
        "target_ready": target_ready,
        "invalidation_ready": invalidation_ready,
        "projection_ready": projection_ready,
        "structure_ready": structure_ready,
        "trendline_ready": trendline_ready,
        "angle_ready": angle_ready,
        "replay_ready": replay_ready,
        "path_ready": path_ready,
        "full_suite_ready": full_suite_ready,
        "expected_move_candles": max(0, _int(overlay_suite.get("expected_move_candles_from_projection"), 0)),
    }


def _build_semantic_graph(
    overlay_suite: Mapping[str, Any],
    evidence: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
) -> dict[str, Any]:
    layers = _overlay_layer_score(overlay_suite)
    overlay_side = _side(overlay_suite.get("side"))
    current_leg = _mapping(candle_context.get("current_leg"))
    current_leg_side = _side(current_leg.get("side"))
    current_stage = _upper(candle_context.get("move_stage") or current_leg.get("move_stage"), "UNKNOWN")
    best_entry = _mapping(overlay_suite.get("best_entry"))
    best_target = _mapping(overlay_suite.get("best_target"))
    best_path = _mapping(overlay_suite.get("best_prediction_path"))
    best_force = _mapping(overlay_suite.get("best_opposing_force"))
    raw_overlay_rows_seen = len(
        _rows(
            overlay_suite.get("rows")
            or overlay_suite.get("normalized_rows")
            or overlay_suite.get("overlay_rows")
        )
    )
    if raw_overlay_rows_seen <= 0:
        raw_overlay_rows_seen = max(
            _int(overlay_suite.get("raw_overlay_rows_seen"), 0),
            _int(overlay_suite.get("rows_total"), 0),
        )
    missing_first_class_feeds_value = overlay_suite.get("missing_first_class_feeds")
    missing_first_class_feeds = (
        [str(item) for item in cast(Sequence[Any], missing_first_class_feeds_value)]
        if isinstance(missing_first_class_feeds_value, Sequence)
        and not isinstance(missing_first_class_feeds_value, (str, bytes, bytearray))
        else []
    )

    if layers["full_suite_ready"]:
        interpretation = "FULL_OVERLAY_SUITE_THESIS"
    elif layers["entry_ready"] and layers["target_ready"]:
        interpretation = "ENTRY_TARGET_STRUCTURE_PARTIAL"
    elif layers["path_ready"]:
        interpretation = "PATH_CONTEXT_WITHOUT_COMPLETE_ENTRY_TARGET"
    elif layers["rows_total"] > 0:
        interpretation = "RAW_OVERLAY_CONTEXT_ONLY"
    else:
        interpretation = "NO_OVERLAY_EVIDENCE"

    nodes = [
        {
            "id": "candidate_bias",
            "kind": "bias",
            "side": _first_side(evidence.get("primary_bias_side"), evidence.get("dominant_side"), overlay_side),
        },
        {
            "id": "entry_layer",
            "kind": "entry",
            "ready": layers["entry_ready"],
            "current_touch": layers["current_entry_touch"],
            "count": layers["entry_window_count"],
            "same_side_count": layers["same_side_entry_window_count"],
            "best": best_entry,
        },
        {
            "id": "path_layer",
            "kind": "path",
            "ready": layers["path_ready"],
            "projection_ready": layers["projection_ready"],
            "structure_ready": layers["structure_ready"],
            "trendline_ready": layers["trendline_ready"],
            "angle_ready": layers["angle_ready"],
            "replay_ready": layers["replay_ready"],
            "best": best_path,
        },
        {
            "id": "target_layer",
            "kind": "target",
            "ready": layers["target_ready"],
            "target_count": layers["target_window_count"],
            "opposing_force_count": layers["opposing_force_count"],
            "best_target": best_target,
            "best_opposing_force": best_force,
        },
        {
            "id": "invalidation_layer",
            "kind": "invalidation",
            "ready": layers["invalidation_ready"],
            "count": layers["invalidation_count"],
        },
        {
            "id": "candle_leg",
            "kind": "candle_movement",
            "side": current_leg_side,
            "stage": current_stage,
            "candle_count": _int(current_leg.get("candle_count"), 0),
        },
    ]

    edges = [
        {
            "from": "entry_layer",
            "to": "path_layer",
            "relation": "entry_needs_path_confirmation",
            "active": bool(layers["entry_ready"] and layers["path_ready"]),
        },
        {
            "from": "path_layer",
            "to": "target_layer",
            "relation": "path_projects_to_target_or_opposing_force",
            "active": bool(layers["path_ready"] and layers["target_ready"]),
        },
        {
            "from": "entry_layer",
            "to": "invalidation_layer",
            "relation": "entry_requires_invalidation_boundary",
            "active": bool(layers["entry_ready"] and layers["invalidation_ready"]),
        },
        {
            "from": "candle_leg",
            "to": "candidate_bias",
            "relation": "current_leg_tests_bias_alignment",
            "active": current_leg_side in SIDES,
        },
    ]

    return {
        "schema_version": "PG_PLAYBOOK_AI_SEMANTIC_GRAPH_V3",
        "interpretation": interpretation,
        "overlay_side": overlay_side,
        "overlay_semantics_are_rendering_independent": True,
        "coverage": layers,
        "raw_overlay_rows_seen": raw_overlay_rows_seen,
        "first_class_feeds": _mapping(overlay_suite.get("first_class_feeds")),
        "missing_first_class_feeds": missing_first_class_feeds,
        "professional_plan_present": bool(professional_plan),
        "professional_thesis_class": str(professional_plan.get("thesis_class") or ""),
        "nodes": nodes,
        "edges": edges,
    }


def _source_bias_score(side: str, sources: Mapping[str, str]) -> dict[str, Any]:
    weights = {
        "primary_bias_side": 0.20,
        "dominant_side": 0.14,
        "global_side": 0.10,
        "local_side": 0.10,
        "professional_authority_side": 0.18,
    }
    score = 0.50
    aligned: list[str] = []
    opposed: list[str] = []
    for key, weight in weights.items():
        source_side = sources.get(key, "HOLD")
        if source_side == side:
            score += weight
            aligned.append(key)
        elif source_side == _opposite(side):
            score -= weight
            opposed.append(key)
    return {
        "score": _round4(_clip01(score, 0.5)),
        "aligned_sources": aligned,
        "opposed_sources": opposed,
        "source_sides": dict(sources),
    }


def _professional_score(side: str, professional_plan: Mapping[str, Any]) -> dict[str, Any]:
    if not professional_plan:
        return {"score": 0.5, "present": False, "side_match": False, "blocker": "", "signals": []}

    plan_side = _first_side(professional_plan.get("authority_side"), professional_plan.get("side"))
    blocker = _upper(professional_plan.get("blocker"))
    thesis_state = _upper(professional_plan.get("professional_thesis_state") or professional_plan.get("thesis_class"))
    trend_alignment = _mapping(professional_plan.get("trend_alignment"))
    grade = _bool(professional_plan.get("professional_grade"))
    side_match = side == plan_side
    score = 0.46
    signals: list[str] = []
    if side_match:
        score += 0.18
        signals.append("authority_side_match")
    else:
        score -= 0.12
        signals.append("authority_side_opposes")
    if grade and side_match:
        score += 0.22
        signals.append("professional_grade")
    elif grade:
        score -= 0.08
    if blocker:
        score -= 0.14 if side_match else 0.04
        signals.append(f"blocker:{blocker}")
    if _bool(trend_alignment.get("overlay_suite_thesis")) and side_match:
        score += 0.08
        signals.append("overlay_suite_thesis")
    if _bool(trend_alignment.get("replay_template_thesis")) and side_match:
        score += 0.06
        signals.append("replay_template_thesis")
    if _contains_any(thesis_state, ("REVERSAL", "RECLAIM", "OPPOSING_FORCE", "REJECTION", "COUNTER_LEG")) and side_match:
        score += 0.05
        signals.append("professional_reaction_or_reversal")
    if _bool(trend_alignment.get("countertrend_unresolved")) and side_match:
        score -= 0.12
        signals.append("countertrend_unresolved")
    if _bool(trend_alignment.get("countertrend_scalp_only")) and side_match:
        score -= 0.10
        signals.append("countertrend_scalp_only")

    return {
        "score": _round4(_clip01(score, 0.5)),
        "present": True,
        "side_match": side_match,
        "professional_grade": grade,
        "plan_side": plan_side,
        "blocker": blocker,
        "thesis_class": str(professional_plan.get("thesis_class") or ""),
        "professional_thesis_state": thesis_state,
        "signals": signals,
    }


def _candle_score(side: str, candle_context: Mapping[str, Any], professional_plan: Mapping[str, Any]) -> dict[str, Any]:
    current_leg = _mapping(candle_context.get("current_leg"))
    current_side = _side(current_leg.get("side"))
    current_stage = _upper(candle_context.get("move_stage") or current_leg.get("move_stage"), "UNKNOWN")
    buy_count = _int(candle_context.get("buy_candle_count"), 0)
    sell_count = _int(candle_context.get("sell_candle_count"), 0)
    visible_count = max(0, _int(candle_context.get("visible_candle_count"), buy_count + sell_count))
    side_count = buy_count if side == "BUY" else sell_count
    imbalance_score = _ratio(side_count, max(1, buy_count + sell_count), 0.5)
    score = 0.40 + (imbalance_score * 0.20)
    signals: list[str] = []

    reaction_context = _contains_any(
        professional_plan.get("professional_thesis_state") or professional_plan.get("thesis_class"),
        ("REVERSAL", "RECLAIM", "OPPOSING_FORCE", "REJECTION", "COUNTER_LEG"),
    )
    if current_side == side:
        if current_stage in {"EARLY", "OPENING_PHASE", "MID_CANDLE", "STILL_RECLAIMING", "MATURE"}:
            score += 0.14
            signals.append("same_side_leg_not_exhausted")
        elif current_stage in {"LATE", "EXHAUSTED", "CLOSE_PRESSURE"}:
            score -= 0.16
            signals.append("same_side_late_or_exhausted_leg")
        else:
            score += 0.03
            signals.append("same_side_unclear_stage")
    elif current_side == _opposite(side):
        if current_stage in {"LATE", "EXHAUSTED", "CLOSE_PRESSURE"} and reaction_context:
            score += 0.12
            signals.append("opposite_leg_exhaustion_supports_reaction")
        elif current_stage in {"LATE", "EXHAUSTED"}:
            score += 0.02
            signals.append("opposite_leg_late_but_reaction_unconfirmed")
        else:
            score -= 0.08
            signals.append("opposite_leg_still_active")
    else:
        signals.append("current_leg_side_unknown")

    room = _mapping(candle_context.get("opposing_force_room") or current_leg.get("opposing_force_room"))
    room_side = _side(room.get("candidate_side"))
    room_ok = _bool(room.get("room_ok"), True)
    estimated_room = _int(room.get("estimated_room_candles") or room.get("estimated_candles_to_force"), 0)
    if room_side == side and room_ok:
        score += 0.06
        signals.append("opposing_force_room_ok")
    elif room_side == side and not room_ok:
        score -= 0.10
        signals.append("opposing_force_room_too_short")

    return {
        "score": _round4(_clip01(score, 0.5)),
        "current_leg_side": current_side,
        "current_leg_stage": current_stage,
        "current_leg_candle_count": _int(current_leg.get("candle_count"), 0),
        "visible_candle_count": visible_count,
        "side_candle_count": side_count,
        "side_candle_ratio": _round4(imbalance_score),
        "room_ok": room_ok,
        "estimated_room_candles": estimated_room,
        "signals": signals,
    }


def _overlay_side_score(side: str, overlay_suite: Mapping[str, Any], layers: Mapping[str, Any]) -> dict[str, Any]:
    overlay_side = _side(overlay_suite.get("side"))
    actionable_count = max(0, _int(layers.get("actionable_count"), 0))
    same_actionable = max(0, _int(layers.get("same_side_actionable_count"), 0))
    opposite_actionable = max(0, actionable_count - same_actionable)
    if overlay_side == side:
        side_actionable = same_actionable
        side_ratio = _ratio(same_actionable, max(1, actionable_count), 0.5)
    elif overlay_side in SIDES:
        side_actionable = opposite_actionable
        side_ratio = _ratio(opposite_actionable, max(1, actionable_count), 0.25)
    else:
        side_actionable = actionable_count // 2
        side_ratio = 0.5 if actionable_count else 0.0

    readiness = (
        (0.18 if _bool(layers.get("entry_ready")) and overlay_side in {side, "HOLD"} else 0.0)
        + (0.12 if _bool(layers.get("current_entry_touch")) and overlay_side in {side, "HOLD"} else 0.0)
        + (0.16 if _bool(layers.get("target_ready")) else 0.0)
        + (0.10 if _bool(layers.get("invalidation_ready")) else 0.0)
        + (0.16 if _bool(layers.get("projection_ready")) else 0.0)
        + (0.10 if _bool(layers.get("structure_ready")) else 0.0)
        + (0.08 if _bool(layers.get("trendline_ready")) else 0.0)
        + (0.06 if _bool(layers.get("angle_ready")) else 0.0)
        + (0.04 if _bool(layers.get("replay_ready")) else 0.0)
    )
    arsenal = _clip01(layers.get("overlay_arsenal_score"), 0.0)
    score = _clip01((readiness * 0.62) + (arsenal * 0.28) + (side_ratio * 0.10))
    return {
        "score": _round4(score),
        "overlay_side": overlay_side,
        "side_actionable_count": side_actionable,
        "side_actionable_ratio": _round4(side_ratio),
        "arsenal_score": _round4(arsenal),
        "readiness_score": _round4(readiness),
    }


def _market_side_score(side: str, market_context: Mapping[str, Any]) -> dict[str, Any]:
    model_council = _mapping(market_context.get("model_council"))
    price_location = _mapping(market_context.get("price_location"))
    reasoning = _mapping(market_context.get("reasoning_arbitration"))
    classifiers = _mapping(market_context.get("classifiers"))
    classifier_bundle = _mapping(market_context.get("market_classifiers"))
    entry_quality = _mapping(market_context.get("entry_quality"))
    path_risk = _mapping(market_context.get("path_risk"))

    explicit_score = model_council.get("buy_score") if side == "BUY" else model_council.get("sell_score")
    score = _clip01(explicit_score, 0.5) if explicit_score is not None else 0.5
    location_key = "buy_quality" if side == "BUY" else "sell_quality"
    location_quality = _clip01(price_location.get(location_key), 0.5)
    coherence = _clip01(reasoning.get("coherence_score"), 0.5)
    entry_score = _clip01(entry_quality.get("score") or entry_quality.get("quality"), 0.5)
    risk_score = _clip01(path_risk.get("score") or path_risk.get("risk_score"), 0.0)

    risk_flags = {
        "late_chase_after_impulse": _bool(classifiers.get("late_chase_after_impulse")),
        "near_opposing_force": _bool(classifiers.get("near_opposing_force")),
        "history_would_exit_here": _bool(classifiers.get("history_would_exit_here")),
        "false_breakout_risk": _bool(classifiers.get("false_breakout_risk")),
        "pullback_not_confirmed": _bool(classifiers.get("pullback_not_confirmed")),
        "dominance_weakening": _bool(classifiers.get("dominance_weakening")),
        "conflict_market": _bool(classifiers.get("conflict_market")),
        "angle_break_risk": _bool(classifiers.get("angle_break_risk")),
    }
    risk_penalty = (
        (0.12 if risk_flags["conflict_market"] else 0.0)
        + (0.10 if risk_flags["late_chase_after_impulse"] else 0.0)
        + (0.08 if risk_flags["history_would_exit_here"] else 0.0)
        + (0.07 if risk_flags["near_opposing_force"] else 0.0)
        + (0.06 if risk_flags["false_breakout_risk"] else 0.0)
        + (0.05 if risk_flags["pullback_not_confirmed"] else 0.0)
        + (0.05 if risk_flags["dominance_weakening"] else 0.0)
        + (0.05 if risk_flags["angle_break_risk"] else 0.0)
        + min(0.10, risk_score * 0.10)
    )
    blended = (score * 0.36) + (location_quality * 0.20) + (coherence * 0.18) + (entry_score * 0.14) + ((1.0 - risk_penalty) * 0.12)
    block_reason = str(classifier_bundle.get("block_reason") or "")
    return {
        "score": _round4(_clip01(blended)),
        "model_side_score": _round4(score),
        "location_quality": _round4(location_quality),
        "reasoning_coherence": _round4(coherence),
        "entry_quality": _round4(entry_score),
        "risk_penalty": _round4(risk_penalty),
        "risk_flags": risk_flags,
        "block_reason": block_reason,
    }


def _side_thesis_score(
    side: str,
    overlay_suite: Mapping[str, Any],
    layers: Mapping[str, Any],
    evidence: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
    market_context: Mapping[str, Any],
) -> dict[str, Any]:
    overlay_score = _overlay_side_score(side, overlay_suite, layers)
    bias_score = _source_bias_score(side, _bias_sides(evidence, professional_plan))
    professional = _professional_score(side, professional_plan)
    candle = _candle_score(side, candle_context, professional_plan)
    market_score = _market_side_score(side, market_context)

    path_quality = _clip01(
        (0.22 if _bool(layers.get("projection_ready")) else 0.0)
        + (0.18 if _bool(layers.get("target_ready")) else 0.0)
        + (0.14 if _bool(layers.get("invalidation_ready")) else 0.0)
        + (0.14 if _bool(layers.get("structure_ready")) else 0.0)
        + (0.10 if _bool(layers.get("trendline_ready")) else 0.0)
        + (0.08 if _bool(layers.get("angle_ready")) else 0.0)
        + (0.08 if _bool(layers.get("replay_ready")) else 0.0)
        + (0.06 if _bool(layers.get("current_entry_touch")) else 0.0),
    )
    safety = _clip01(1.0 - _float(market_score.get("risk_penalty"), 0.0), 0.5)
    score = _clip01(
        (_float(overlay_score.get("score"), 0.0) * 0.24)
        + (_float(bias_score.get("score"), 0.5) * 0.18)
        + (_float(professional.get("score"), 0.5) * 0.17)
        + (_float(candle.get("score"), 0.5) * 0.14)
        + (_float(market_score.get("score"), 0.5) * 0.12)
        + (path_quality * 0.10)
        + (safety * 0.05)
    )
    if _bool(layers.get("full_suite_ready")) and _side(overlay_suite.get("side")) == side:
        score = _clip01(score + 0.04)
    if _upper(professional.get("blocker")) and bool(professional.get("side_match")):
        score = _clip01(score - 0.04)

    return {
        "side": side,
        "score": _round4(score),
        "components": {
            "overlay": overlay_score,
            "bias": bias_score,
            "professional": professional,
            "candle_movement": candle,
            "market": market_score,
            "path_quality": _round4(path_quality),
            "safety": _round4(safety),
        },
    }


def _build_thesis_arbitration(
    overlay_suite: Mapping[str, Any],
    semantic_graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
    market_context: Mapping[str, Any],
    candidate_side: str,
) -> dict[str, Any]:
    layers = _mapping(semantic_graph.get("coverage"))
    scores = {
        side: _side_thesis_score(
            side,
            overlay_suite,
            layers,
            evidence,
            professional_plan,
            candle_context,
            market_context,
        )
        for side in SIDES
    }
    overlay_side = _side(overlay_suite.get("side"))
    story_controls_side = bool(
        _bool(layers.get("full_suite_ready"))
        and overlay_side in SIDES
        and _int(layers.get("rows_total"), 0) > 0
        and _ratio(layers.get("same_side_actionable_count"), max(1, _int(layers.get("actionable_count"), 0)), 0.0) >= 0.55
        and _bool(layers.get("entry_ready"))
        and _bool(layers.get("target_ready"))
        and _bool(layers.get("path_ready"))
    )
    if story_controls_side:
        for side in SIDES:
            side_score = _mapping(scores.get(side))
            raw_score = _float(side_score.get("score"), 0.0)
            story_adjustment = 0.18 if side == overlay_side else -0.12
            components = _mapping(side_score.get("components"))
            components["full_suite_story_weight"] = {
                "applied": True,
                "overlay_side": overlay_side,
                "adjustment": _round4(story_adjustment),
                "reason": "confirmed raw full-suite overlay story controls thesis arbitration",
            }
            scores[side] = {
                **side_score,
                "score": _round4(_clip01(raw_score + story_adjustment)),
                "components": components,
            }
    buy_score = _float(scores["BUY"].get("score"), 0.0)
    sell_score = _float(scores["SELL"].get("score"), 0.0)
    margin = abs(buy_score - sell_score)
    if margin < 0.035:
        winner = "HOLD"
    else:
        winner = "BUY" if buy_score > sell_score else "SELL"

    candidate_score = _float(scores.get(candidate_side, {}).get("score"), 0.0) if candidate_side in SIDES else 0.0
    conflict = bool(margin < 0.08 or _bool(_mapping(market_context.get("classifiers")).get("conflict_market")))
    if winner == "HOLD":
        state = "BALANCED_WAIT_FOR_CONFIRMATION"
    elif candidate_side in SIDES and winner != candidate_side and candidate_score > 0.52:
        state = "CANDIDATE_CONTESTED_BY_OPPOSITE_THESIS"
    elif winner == candidate_side:
        state = "CANDIDATE_THESIS_LEADS"
    else:
        state = "OPPOSITE_THESIS_LEADS"

    return {
        "schema_version": "PG_PLAYBOOK_THESIS_ARBITRATION_V3",
        "candidate_side": candidate_side,
        "scores": scores,
        "winner": winner,
        "winning_score": _round4(max(buy_score, sell_score)),
        "margin": _round4(margin),
        "conflict": conflict,
        "candidate_score": _round4(candidate_score),
        "candidate_supported": bool(candidate_side in SIDES and winner == candidate_side and candidate_score >= 0.56),
        "state": state,
        "buy_sell_scored_simultaneously": True,
        "full_suite_story_controls_side": story_controls_side,
        "full_suite_story_side": overlay_side if story_controls_side else "HOLD",
    }


def _risk_flags(market_context: Mapping[str, Any]) -> dict[str, bool]:
    classifiers = _mapping(market_context.get("classifiers"))
    return {
        "late_chase_after_impulse": _bool(classifiers.get("late_chase_after_impulse")),
        "near_opposing_force": _bool(classifiers.get("near_opposing_force")),
        "history_would_exit_here": _bool(classifiers.get("history_would_exit_here")),
        "false_breakout_risk": _bool(classifiers.get("false_breakout_risk")),
        "pullback_not_confirmed": _bool(classifiers.get("pullback_not_confirmed")),
        "dominance_weakening": _bool(classifiers.get("dominance_weakening")),
        "conflict_market": _bool(classifiers.get("conflict_market")),
        "angle_break_risk": _bool(classifiers.get("angle_break_risk")),
    }


def _build_regime_router(
    semantic_graph: Mapping[str, Any],
    thesis_arbitration: Mapping[str, Any],
    evidence: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
    market_context: Mapping[str, Any],
) -> dict[str, Any]:
    layers = _mapping(semantic_graph.get("coverage"))
    current_leg = _mapping(candle_context.get("current_leg"))
    current_leg_side = _side(current_leg.get("side"))
    current_stage = _upper(candle_context.get("move_stage") or current_leg.get("move_stage"), "UNKNOWN")
    winner = _side(thesis_arbitration.get("winner"))
    winning_score = _clip01(thesis_arbitration.get("winning_score"), 0.0)
    margin = _clip01(thesis_arbitration.get("margin"), 0.0)
    risks = _risk_flags(market_context)
    regime = _mapping(market_context.get("regime"))
    regime_primary = _upper(regime.get("primary") or regime.get("regime") or regime.get("label"))
    thesis_state = _upper(professional_plan.get("professional_thesis_state") or professional_plan.get("thesis_class"))
    aligned_primary = _bool(evidence.get("aligned_with_primary_bias")) or _bool(
        _mapping(professional_plan.get("trend_alignment")).get("aligned_with_primary_bias")
    )
    reversal_context = bool(
        _bool(evidence.get("countertrend_reversal_override"))
        or _contains_any(thesis_state, ("REVERSAL", "RECLAIM", "OPPOSING_FORCE", "REJECTION", "COUNTER_LEG"))
    )
    late_stage = bool(winner in SIDES and current_leg_side == winner and current_stage in {"LATE", "EXHAUSTED", "CLOSE_PRESSURE"})
    full_suite_reentry_projection = bool(
        late_stage
        and _bool(layers.get("full_suite_ready"))
        and _bool(layers.get("current_entry_touch"))
        and _bool(layers.get("target_ready"))
        and _bool(layers.get("invalidation_ready"))
        and _bool(layers.get("projection_ready"))
        and _int(layers.get("expected_move_candles"), 0) >= 6
        and winning_score >= 0.62
        and margin >= 0.08
        and not risks["near_opposing_force"]
        and not risks["history_would_exit_here"]
        and not risks["angle_break_risk"]
    )

    if not semantic_graph.get("professional_plan_present") and _int(layers.get("rows_total"), 0) <= 0:
        regime_label = "INSUFFICIENT_PLAYBOOK_CONTEXT"
        route = "WAIT_FOR_CONTEXT"
    elif risks["conflict_market"] or bool(thesis_arbitration.get("conflict")):
        regime_label = "CONFLICT_OR_RANGE_ARBITRATION"
        route = "WAIT_FOR_CLEARER_THESIS"
    elif full_suite_reentry_projection:
        regime_label = "FULL_SUITE_REENTRY_AFTER_EXTENDED_LEG"
        route = "PLAYBOOK_STRUCTURE_THESIS"
    elif late_stage:
        regime_label = "LATE_IMPULSE_RISK"
        route = "WAIT_FOR_PULLBACK_OR_NEW_STRUCTURE"
    elif reversal_context:
        regime_label = "OPPOSING_FORCE_REACTION_OR_RECLAIM"
        route = "REACTION_THESIS_ONLY"
    elif aligned_primary and _bool(layers.get("full_suite_ready")):
        regime_label = "STRUCTURE_CONFIRMED_TREND_CONTINUATION"
        route = "TREND_CONTINUATION_THESIS"
    elif _bool(layers.get("full_suite_ready")):
        regime_label = "OVERLAY_CONFIRMED_PLAYBOOK_STRUCTURE"
        route = "PLAYBOOK_STRUCTURE_THESIS"
    elif regime_primary:
        regime_label = regime_primary
        route = "MARKET_CONTEXT_ROUTE"
    else:
        regime_label = "MIXED_OR_DEVELOPING"
        route = "WAIT_OR_SMALL_CONFIRMATION"

    confidence = _clip01(
        (winning_score * 0.46)
        + (_clip01(layers.get("overlay_arsenal_score"), 0.0) * 0.24)
        + (min(1.0, margin * 4.0) * 0.16)
        + ((0.14) if not any(risks.values()) else 0.04)
    )
    if route.startswith("WAIT"):
        confidence = _clip01(confidence + 0.04)

    return {
        "schema_version": "PG_PLAYBOOK_REGIME_ROUTER_V3",
        "regime": regime_label,
        "route": route,
        "route_side": winner if winner in SIDES else "HOLD",
        "confidence": _round4(confidence),
        "current_leg_side": current_leg_side,
        "current_leg_stage": current_stage,
        "market_regime_primary": regime_primary,
        "aligned_with_primary_bias": aligned_primary,
        "reaction_or_reclaim_context": reversal_context,
        "risk_flags": risks,
        "rendering_independent": True,
    }


def _room_context(professional_plan: Mapping[str, Any], candle_context: Mapping[str, Any]) -> dict[str, Any]:
    thesis_horizon = _mapping(professional_plan.get("thesis_horizon"))
    profit_discipline = _mapping(professional_plan.get("profit_discipline"))
    hierarchy = _mapping(professional_plan.get("trade_hierarchy"))
    local_distribution = _mapping(hierarchy.get("local_distribution"))
    current_leg = _mapping(candle_context.get("current_leg"))
    room = _mapping(candle_context.get("opposing_force_room") or current_leg.get("opposing_force_room"))
    effective_room = _int(
        thesis_horizon.get("effective_room_candles")
        or profit_discipline.get("effective_room_candles")
        or local_distribution.get("effective_room_candles")
        or room.get("estimated_room_candles")
        or room.get("estimated_candles_to_force"),
        0,
    )
    directional_room = _int(
        thesis_horizon.get("directional_target_room_candles")
        or profit_discipline.get("directional_target_room_candles")
        or local_distribution.get("directional_target_room_candles"),
        0,
    )
    return {
        "room_ok": _bool(
            profit_discipline.get("room_ok")
            if profit_discipline
            else local_distribution.get("room_ok")
            if local_distribution
            else room.get("room_ok"),
            True,
        ),
        "effective_room_candles": max(0, effective_room),
        "directional_target_room_candles": max(0, directional_room),
        "estimated_candles_to_force": max(
            0,
            _int(
                thesis_horizon.get("estimated_candles_to_force")
                or profit_discipline.get("estimated_candles_to_force")
                or local_distribution.get("estimated_candles_to_force")
                or room.get("estimated_room_candles")
                or room.get("estimated_candles_to_force"),
                0,
            ),
        ),
        "source": str(
            thesis_horizon.get("effective_room_source")
            or profit_discipline.get("effective_room_source")
            or local_distribution.get("effective_room_source")
            or "candle_movement_context"
        ),
    }


def _side_meta_label(
    side: str,
    thesis: Mapping[str, Any],
    layers: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
    market_context: Mapping[str, Any],
) -> dict[str, Any]:
    professional = _mapping(_mapping(thesis.get("components")).get("professional"))
    candle = _mapping(_mapping(thesis.get("components")).get("candle_movement"))
    room = _room_context(professional_plan, candle_context)
    market = _market_side_score(side, market_context)
    target_ready = _bool(layers.get("target_ready"))
    invalidation_ready = _bool(layers.get("invalidation_ready"))
    projection_ready = _bool(layers.get("projection_ready"))
    entry_touch = _bool(layers.get("current_entry_touch"))
    full_suite_ready = _bool(layers.get("full_suite_ready"))
    thesis_score = _clip01(thesis.get("score"), 0.0)
    professional_grade = bool(professional.get("professional_grade")) and bool(professional.get("side_match"))
    blocker = _upper(professional.get("blocker"))
    current_stage = _upper(candle.get("current_leg_stage"))
    current_side = _side(candle.get("current_leg_side"))

    probability = (
        thesis_score * 0.30
        + (0.15 if target_ready else 0.03)
        + (0.10 if invalidation_ready else 0.04)
        + (0.11 if projection_ready else 0.02)
        + (0.10 if full_suite_ready else 0.03)
        + (0.10 if professional_grade else 0.04)
        + (0.09 if bool(room.get("room_ok")) else 0.01)
        + (0.07 if entry_touch else 0.03)
        + (_clip01(market.get("score"), 0.5) * 0.08)
    )
    if blocker:
        probability -= 0.08
    if current_side == side and current_stage in {"LATE", "EXHAUSTED", "CLOSE_PRESSURE"}:
        probability -= 0.11
    if _float(market.get("risk_penalty"), 0.0) >= 0.20:
        probability -= 0.07
    if not target_ready:
        probability -= 0.04
    if not invalidation_ready:
        probability -= 0.03
    probability = _clip01(probability)

    if probability >= 0.66:
        label = "TARGET_BEFORE_INVALIDATION_LIKELY"
    elif probability >= 0.54:
        label = "TARGET_BEFORE_INVALIDATION_FAVORED"
    elif probability >= 0.44:
        label = "TARGET_INVALIDATION_BALANCED"
    else:
        label = "INVALIDATION_FIRST_RISK"

    invalidation_first_risk = _clip01(1.0 - probability + (0.06 if not invalidation_ready else 0.0))
    return {
        "side": side,
        "target_before_invalidation_probability": _round4(probability),
        "invalidation_first_risk": _round4(invalidation_first_risk),
        "label": label,
        "target_ready": target_ready,
        "invalidation_ready": invalidation_ready,
        "room": room,
        "professional_grade": professional_grade,
        "professional_blocker": blocker,
        "components": {
            "thesis_score": _round4(thesis_score),
            "projection_ready": projection_ready,
            "full_suite_ready": full_suite_ready,
            "entry_touch": entry_touch,
            "market_risk_penalty": _round4(market.get("risk_penalty")),
        },
    }


def _build_meta_label(
    semantic_graph: Mapping[str, Any],
    thesis_arbitration: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
    market_context: Mapping[str, Any],
) -> dict[str, Any]:
    layers = _mapping(semantic_graph.get("coverage"))
    scores = _mapping(thesis_arbitration.get("scores"))
    by_side = {
        side: _side_meta_label(
            side,
            _mapping(scores.get(side)),
            layers,
            professional_plan,
            candle_context,
            market_context,
        )
        for side in SIDES
    }
    winner = _side(thesis_arbitration.get("winner"))
    selected_side = winner if winner in SIDES else "HOLD"
    selected = by_side.get(selected_side, {}) if selected_side in SIDES else {}
    return {
        "schema_version": "PG_PLAYBOOK_META_LABEL_TARGET_INVALIDATION_V3",
        "target": "target_before_invalidation",
        "by_side": by_side,
        "selected_side": selected_side,
        "selected": selected,
        "tradeable_probability_floor": 0.54,
        "candidate_tradeable": bool(
            selected_side in SIDES
            and _float(selected.get("target_before_invalidation_probability"), 0.0) >= 0.54
        ),
    }


def _horizon_candidates_for_side(
    side: str,
    thesis: Mapping[str, Any],
    meta_label: Mapping[str, Any],
    semantic_graph: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    layers = _mapping(semantic_graph.get("coverage"))
    thesis_horizon = _mapping(professional_plan.get("thesis_horizon"))
    entry_window = _mapping(professional_plan.get("entry_window"))
    plan_side = _first_side(professional_plan.get("authority_side"), professional_plan.get("side"))
    timeframe_seconds = max(1, _int(candle_context.get("timeframe_seconds"), 300) or 300)
    candidates: list[dict[str, Any]] = []
    meta_probability = _clip01(meta_label.get("target_before_invalidation_probability"), 0.0)
    thesis_score = _clip01(thesis.get("score"), 0.0)
    overlay_expected = _int(layers.get("expected_move_candles"), 0)
    room = _room_context(professional_plan, candle_context)
    effective_room = _int(room.get("effective_room_candles"), 0)

    professional_count = _int(thesis_horizon.get("expected_candle_count"), 0)
    if professional_count > 0 and plan_side == side:
        candidates.append(
            {
                "basis": "professional_trade_plan_thesis_horizon",
                "candle_count": professional_count,
                "score": _round4(0.45 + (0.25 if _bool(professional_plan.get("professional_grade")) else 0.0) + (meta_probability * 0.20) + (thesis_score * 0.10)),
            }
        )
    if overlay_expected > 0:
        candidates.append(
            {
                "basis": "overlay_projection_expected_move",
                "candle_count": overlay_expected,
                "score": _round4(0.35 + (_clip01(layers.get("overlay_arsenal_score"), 0.0) * 0.35) + (meta_probability * 0.20)),
            }
        )
    if effective_room > 0:
        candidates.append(
            {
                "basis": "opposing_force_room_cap",
                "candle_count": effective_room,
                "score": _round4(0.30 + (0.25 if _bool(room.get("room_ok"), True) else 0.0) + (meta_probability * 0.20)),
            }
        )
    entry_candles = _int(entry_window.get("candle_count"), 0)
    if entry_candles > 0 and plan_side == side:
        candidates.append(
            {
                "basis": "professional_entry_window",
                "candle_count": entry_candles,
                "score": _round4(0.25 + (meta_probability * 0.20)),
            }
        )

    current_leg = _mapping(candle_context.get("current_leg"))
    current_count = _int(current_leg.get("candle_count"), 0)
    if current_count > 0:
        continuation_count = max(1, min(6, current_count // 2 if current_count > 2 else 2))
        candidates.append(
            {
                "basis": "visible_current_leg_reference",
                "candle_count": continuation_count,
                "score": _round4(0.26 + (thesis_score * 0.22)),
            }
        )
    if not candidates:
        candidates.append({"basis": "fallback_single_candle_confirmation", "candle_count": 1, "score": _round4(0.20 + thesis_score * 0.20)})

    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        count = max(1, _int(candidate.get("candle_count"), 1))
        score = _clip01(candidate.get("score"), 0.0)
        normalized.append(
            {
                "basis": str(candidate.get("basis") or "unknown"),
                "candle_count": count,
                "duration_sec": int(count * timeframe_seconds),
                "duration_text": _duration_text(int(count * timeframe_seconds)),
                "score": _round4(score),
            }
        )
    return sorted(normalized, key=lambda item: (_float(item.get("score"), 0.0), _int(item.get("candle_count"), 0)), reverse=True)


def _build_horizon(
    semantic_graph: Mapping[str, Any],
    thesis_arbitration: Mapping[str, Any],
    meta_label: Mapping[str, Any],
    professional_plan: Mapping[str, Any],
    candle_context: Mapping[str, Any],
    regime_router: Mapping[str, Any],
) -> dict[str, Any]:
    scores = _mapping(thesis_arbitration.get("scores"))
    meta_by_side = _mapping(meta_label.get("by_side"))
    timeframe = str(candle_context.get("timeframe") or "M5").upper()
    timeframe_seconds = max(1, _int(candle_context.get("timeframe_seconds"), 300) or 300)
    selected_side = _side(meta_label.get("selected_side"))
    route = _upper(regime_router.get("route"))
    by_side: dict[str, Any] = {}
    for side in SIDES:
        side_meta = _mapping(meta_by_side.get(side))
        candidates = _horizon_candidates_for_side(
            side,
            _mapping(scores.get(side)),
            side_meta,
            semantic_graph,
            professional_plan,
            candle_context,
        )
        best = candidates[0] if candidates else {"basis": "none", "candle_count": 0, "score": 0.0}
        probability = _clip01(side_meta.get("target_before_invalidation_probability"), 0.0)
        optimized_count = _int(best.get("candle_count"), 0)
        room = _mapping(side_meta.get("room"))
        effective_room = _int(room.get("effective_room_candles"), 0)
        if effective_room > 0:
            optimized_count = min(optimized_count, effective_room)
        if probability < 0.44:
            optimized_count = 0
        elif probability < 0.54:
            optimized_count = min(optimized_count, 1)
        optimized_count = max(0, optimized_count)
        by_side[side] = {
            "optimized_candle_count": optimized_count,
            "optimized_duration_sec": int(optimized_count * timeframe_seconds),
            "optimized_duration_text": _duration_text(int(optimized_count * timeframe_seconds)),
            "horizon_class": _horizon_class(optimized_count),
            "basis": str(best.get("basis") or "none"),
            "target_before_invalidation_probability": _round4(probability),
            "candidates": candidates,
        }

    selected: dict[str, Any] = _mapping(by_side.get(selected_side)) if selected_side in SIDES else {}
    if route.startswith("WAIT") or selected_side not in SIDES:
        selected = {
            "optimized_candle_count": 0,
            "optimized_duration_sec": 0,
            "optimized_duration_text": "0s",
            "horizon_class": "WAIT_FOR_CONFIRMATION",
            "basis": "regime_router_wait_state",
            "target_before_invalidation_probability": 0.0,
            "candidates": list[dict[str, Any]](),
        }

    return {
        "schema_version": "PG_PLAYBOOK_HORIZON_OPTIMIZER_V3",
        "timeframe": timeframe,
        "timeframe_seconds": timeframe_seconds,
        "selected_side": selected_side if selected_side in SIDES else "HOLD",
        "selected": selected,
        "by_side": by_side,
        "optimization_rules": [
            "prefer_professional_trade_plan_horizon_when_grade_and_side_match",
            "use_overlay_projection_expected_move_when_professional_horizon_is_absent",
            "cap_horizon_by_opposing_force_room_when_available",
            "collapse_to_wait_when_meta_label_favors_invalidation_first",
        ],
    }


def _build_full_suite_story_lock(
    semantic_graph: Mapping[str, Any],
    thesis_arbitration: Mapping[str, Any],
    meta_label: Mapping[str, Any],
    horizon: Mapping[str, Any],
    candle_context: Mapping[str, Any],
    candidate_side: str,
) -> dict[str, Any]:
    layers = _mapping(semantic_graph.get("coverage"))
    selected_meta = _mapping(meta_label.get("selected"))
    selected_horizon = _mapping(horizon.get("selected"))
    current_leg = _mapping(candle_context.get("current_leg"))
    winner = _side(thesis_arbitration.get("winner"))
    selected_side = _side(meta_label.get("selected_side") or horizon.get("selected_side") or winner)
    target_probability = _clip01(selected_meta.get("target_before_invalidation_probability"), 0.0)
    winning_score = _clip01(thesis_arbitration.get("winning_score"), 0.0)
    margin = _clip01(thesis_arbitration.get("margin"), 0.0)
    horizon_candles = _int(selected_horizon.get("optimized_candle_count"), 0)
    rows_total = _int(layers.get("rows_total"), 0)
    current_leg_side = _side(current_leg.get("side"))
    current_leg_stage = _upper(candle_context.get("move_stage") or current_leg.get("move_stage"), "UNKNOWN")
    opposite_leg_warning = bool(
        selected_side in SIDES
        and current_leg_side == _opposite(selected_side)
        and _int(current_leg.get("candle_count"), 0) <= 1
    )
    story_confirmed = bool(
        _bool(layers.get("full_suite_ready"))
        and selected_side in SIDES
        and winner == selected_side
        and rows_total > 0
        and winning_score >= 0.60
        and margin >= 0.06
        and target_probability >= 0.54
        and horizon_candles > 0
    )
    if story_confirmed and selected_side != candidate_side:
        state = "FULL_SUITE_TRANSITION_CONFIRMED"
    elif story_confirmed:
        state = "FULL_SUITE_STORY_CONFIRMED"
    elif selected_side in SIDES:
        state = "FULL_SUITE_STORY_DEVELOPING"
    else:
        state = "FULL_SUITE_STORY_UNCLEAR"
    return {
        "schema_version": "PG_FULL_SUITE_STORY_LOCK_V3",
        "active_side": selected_side if selected_side in SIDES else "HOLD",
        "candidate_side": candidate_side if candidate_side in SIDES else "HOLD",
        "state": state,
        "confirmed": story_confirmed,
        "transition_confirmed": bool(story_confirmed and selected_side != candidate_side),
        "story_confidence": _round4(winning_score),
        "story_margin": _round4(margin),
        "target_before_invalidation_probability": _round4(target_probability),
        "horizon_candles": horizon_candles,
        "rows_total": rows_total,
        "current_leg_side": current_leg_side,
        "current_leg_stage": current_leg_stage,
        "opposite_single_candle_warning": opposite_leg_warning,
        "opposite_single_candle_policy": "warning_only_until_full_suite_transition",
        "raw_candle_cannot_flip_story": True,
        "package_side_source": "full_suite_story" if story_confirmed else "developing_full_suite_story",
        "reason": (
            "Full overlay suite has enough entry/target/projection evidence to control package side."
            if story_confirmed
            else "Full overlay suite is present but has not crossed the story lock threshold."
        ),
    }


def build_playbook_ai_intelligence_v3(
    snapshot: Mapping[str, Any] | None,
    market: Mapping[str, Any] | None,
    book_strategy: Mapping[str, Any] | None,
    candidate_side: Any,
) -> dict[str, Any]:
    snapshot_map = _mapping(snapshot)
    market_map = _mapping(market)
    book_strategy_map = _mapping(book_strategy)
    side = _side(
        candidate_side
        or book_strategy_map.get("side")
        or book_strategy_map.get("candidate_side")
        or book_strategy_map.get("action")
    )
    resolved = _resolve_inputs(snapshot_map, market_map, book_strategy_map)
    evidence = _mapping(resolved.get("evidence"))
    overlay_suite = _mapping(resolved.get("overlay_suite"))
    candle_context = _mapping(resolved.get("candle_context"))
    professional_plan = _mapping(resolved.get("professional_plan"))
    market_context = _mapping(resolved.get("market_context"))

    semantic_graph = _build_semantic_graph(overlay_suite, evidence, professional_plan, candle_context)
    thesis_arbitration = _build_thesis_arbitration(
        overlay_suite,
        semantic_graph,
        evidence,
        professional_plan,
        candle_context,
        market_context,
        side,
    )
    regime_router = _build_regime_router(
        semantic_graph,
        thesis_arbitration,
        evidence,
        professional_plan,
        candle_context,
        market_context,
    )
    meta_label = _build_meta_label(
        semantic_graph,
        thesis_arbitration,
        professional_plan,
        candle_context,
        market_context,
    )
    horizon = _build_horizon(
        semantic_graph,
        thesis_arbitration,
        meta_label,
        professional_plan,
        candle_context,
        regime_router,
    )
    full_suite_story_lock = _build_full_suite_story_lock(
        semantic_graph,
        thesis_arbitration,
        meta_label,
        horizon,
        candle_context,
        side,
    )

    rules = [
        "overlay_suite_evidence_v3_consumed_as_semantic_market_structure",
        "overlay_rendering_paths_not_called_or_modified",
        "buy_and_sell_theses_scored_simultaneously",
        "candle_movement_context_v3_used_for_leg_stage_room_and_timeframe",
        "professional_trade_plan_used_when_present",
        "market_context_used_for_regime_and_risk_routing",
        "target_before_invalidation_meta_label_estimated",
        "horizon_optimized_from_professional_plan_overlay_projection_and_room",
        "full_suite_story_lock_controls_package_side_when_confirmed",
        "single_opposite_candle_is_warning_not_story_flip",
    ]
    if not overlay_suite:
        rules.append("missing_overlay_suite_evidence_handled_as_empty_context")
    if not candle_context:
        rules.append("missing_candle_movement_context_handled_as_unknown_leg")
    if not professional_plan:
        rules.append("missing_professional_trade_plan_handled_without_blocking")
    if side not in SIDES:
        rules.append("missing_candidate_side_scored_with_hold_candidate")

    return {
        "schema_version": PG_PLAYBOOK_AI_INTELLIGENCE_SCHEMA_VERSION,
        "semantic_graph": semantic_graph,
        "regime_router": regime_router,
        "thesis_arbitration": thesis_arbitration,
        "meta_label": meta_label,
        "horizon": horizon,
        "full_suite_story_lock_v3": full_suite_story_lock,
        "rules_applied": _dedupe_rules(rules),
    }


def _compact_side_score(value: Any) -> dict[str, Any]:
    mapping = _mapping(value)
    components = _mapping(mapping.get("components"))
    overlay = _mapping(components.get("overlay"))
    professional = _mapping(components.get("professional"))
    candle = _mapping(components.get("candle_movement"))
    market = _mapping(components.get("market"))
    return {
        "side": _side(mapping.get("side")),
        "score": _round4(mapping.get("score")),
        "overlay_score": _round4(overlay.get("score")),
        "professional_score": _round4(professional.get("score")),
        "professional_grade": _bool(professional.get("professional_grade")),
        "current_leg_side": _side(candle.get("current_leg_side")),
        "current_leg_stage": _upper(candle.get("current_leg_stage")),
        "market_score": _round4(market.get("score"), 0.5),
        "market_risk_penalty": _round4(market.get("risk_penalty")),
    }


def compact_playbook_ai_intelligence_v3(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a bounded transport-safe summary for API, alert, and bridge payloads."""

    payload = _mapping(value)
    if not payload:
        return {}
    semantic = _mapping(payload.get("semantic_graph"))
    coverage = _mapping(semantic.get("coverage"))
    router = _mapping(payload.get("regime_router"))
    arbitration = _mapping(payload.get("thesis_arbitration"))
    scores = _mapping(arbitration.get("scores"))
    meta = _mapping(payload.get("meta_label"))
    selected_meta = _mapping(meta.get("selected"))
    horizon = _mapping(payload.get("horizon"))
    selected_horizon = _mapping(horizon.get("selected"))
    story_lock = _mapping(payload.get("full_suite_story_lock_v3"))
    rules_value = payload.get("rules_applied")
    rules = (
        [str(item) for item in cast(Sequence[Any], rules_value)[:12]]
        if isinstance(rules_value, Sequence) and not isinstance(rules_value, (str, bytes, bytearray))
        else []
    )
    missing_feeds_value = semantic.get("missing_first_class_feeds")
    missing_feeds = (
        [str(item) for item in cast(Sequence[Any], missing_feeds_value)[:12]]
        if isinstance(missing_feeds_value, Sequence) and not isinstance(missing_feeds_value, (str, bytes, bytearray))
        else []
    )
    return {
        "schema_version": "PG_PLAYBOOK_AI_SUMMARY_V3",
        "source_schema_version": str(payload.get("schema_version") or ""),
        "semantic_interpretation": str(semantic.get("interpretation") or ""),
        "full_suite_ready": _bool(coverage.get("full_suite_ready")),
        "coverage": {
            "rows_total": _int(coverage.get("rows_total"), 0),
            "actionable_count": _int(coverage.get("actionable_count"), 0),
            "same_side_actionable_count": _int(coverage.get("same_side_actionable_count"), 0),
            "entry_window_count": _int(coverage.get("entry_window_count"), 0),
            "same_side_entry_window_count": _int(coverage.get("same_side_entry_window_count"), 0),
            "target_window_count": _int(coverage.get("target_window_count"), 0),
            "opposing_force_count": _int(coverage.get("opposing_force_count"), 0),
            "invalidation_count": _int(coverage.get("invalidation_count"), 0),
            "prediction_path_count": _int(coverage.get("prediction_path_count"), 0),
            "structure_box_count": _int(coverage.get("structure_box_count"), 0),
            "trendline_count": _int(coverage.get("trendline_count"), 0),
            "overlay_arsenal_score": _round4(coverage.get("overlay_arsenal_score")),
            "expected_move_candles": _int(coverage.get("expected_move_candles"), 0),
        },
        "missing_first_class_feeds": missing_feeds,
        "regime_router": {
            "regime": str(router.get("regime") or ""),
            "route": str(router.get("route") or ""),
            "route_side": _side(router.get("route_side")),
            "confidence": _round4(router.get("confidence")),
            "current_leg_side": _side(router.get("current_leg_side")),
            "current_leg_stage": _upper(router.get("current_leg_stage")),
        },
        "thesis_arbitration": {
            "candidate_side": _side(arbitration.get("candidate_side")),
            "winner": _side(arbitration.get("winner")),
            "winning_score": _round4(arbitration.get("winning_score")),
            "margin": _round4(arbitration.get("margin")),
            "candidate_score": _round4(arbitration.get("candidate_score")),
            "candidate_supported": _bool(arbitration.get("candidate_supported")),
            "conflict": _bool(arbitration.get("conflict")),
            "state": str(arbitration.get("state") or ""),
            "scores": {side: _compact_side_score(scores.get(side)) for side in SIDES},
        },
        "meta_label": {
            "selected_side": _side(meta.get("selected_side")),
            "candidate_tradeable": _bool(meta.get("candidate_tradeable")),
            "target_before_invalidation_probability": _round4(
                selected_meta.get("target_before_invalidation_probability")
            ),
            "invalidation_first_risk": _round4(selected_meta.get("invalidation_first_risk")),
            "label": str(selected_meta.get("label") or ""),
        },
        "horizon": {
            "selected_side": _side(horizon.get("selected_side")),
            "optimized_candle_count": _int(selected_horizon.get("optimized_candle_count"), 0),
            "optimized_duration_sec": _int(selected_horizon.get("optimized_duration_sec"), 0),
            "optimized_duration_text": str(selected_horizon.get("optimized_duration_text") or ""),
            "horizon_class": str(selected_horizon.get("horizon_class") or ""),
            "basis": str(selected_horizon.get("basis") or ""),
            "target_before_invalidation_probability": _round4(
                selected_horizon.get("target_before_invalidation_probability")
            ),
        },
        "full_suite_story_lock_v3": {
            "active_side": _side(story_lock.get("active_side")),
            "raw_active_side": _side(story_lock.get("raw_active_side")),
            "effective_side": _side(story_lock.get("effective_side")),
            "display_side": _side(story_lock.get("display_side")),
            "candidate_side": _side(story_lock.get("candidate_side")),
            "state": str(story_lock.get("state") or ""),
            "confirmed": _bool(story_lock.get("confirmed")),
            "transition_confirmed": _bool(story_lock.get("transition_confirmed")),
            "side_flip_pending": _bool(story_lock.get("side_flip_pending")),
            "stability_state": str(story_lock.get("stability_state") or ""),
            "stability_reads": _int(story_lock.get("stability_reads"), 0),
            "required_stability_reads": _int(story_lock.get("required_stability_reads"), 0),
            "story_confidence": _round4(story_lock.get("story_confidence")),
            "story_margin": _round4(story_lock.get("story_margin")),
            "target_before_invalidation_probability": _round4(
                story_lock.get("target_before_invalidation_probability")
            ),
            "horizon_candles": _int(story_lock.get("horizon_candles"), 0),
            "opposite_single_candle_policy": str(story_lock.get("opposite_single_candle_policy") or ""),
            "raw_candle_cannot_flip_story": _bool(story_lock.get("raw_candle_cannot_flip_story")),
        },
        "rules_applied": rules,
    }


__all__ = [
    "PG_PLAYBOOK_AI_INTELLIGENCE_SCHEMA_VERSION",
    "build_playbook_ai_intelligence_v3",
    "compact_playbook_ai_intelligence_v3",
]
