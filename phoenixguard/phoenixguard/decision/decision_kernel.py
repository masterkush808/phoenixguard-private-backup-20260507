from __future__ import annotations

from dataclasses import dataclass
from math import ceil, exp
from statistics import median
from typing import Any, Mapping, Sequence, cast


SIDES = {"BUY", "SELL"}
MEMORY_SHRINKAGE_K = 30.0


@dataclass(frozen=True, slots=True)
class BeliefState:
    buy: float
    sell: float
    hold: float
    uncertainty: float
    conflict: float
    directional_edge: float
    evidence_mass: float
    usable_bias: float
    family_totals: Mapping[str, float]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExpectedUtility:
    raw_ev_R: float
    adjusted_ev_R: float
    uncertainty_tax_R: float
    reward_R: float
    loss_R: float
    cost_R: float


@dataclass(frozen=True, slots=True)
class FirewallAdvisory:
    action: str
    reasons: tuple[str, ...]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number or number in {float("inf"), float("-inf")}:
        return float(default)
    return float(number)


def _clip01(value: Any, default: float = 0.0) -> float:
    number = _safe_float(value, default)
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return float(number)


def _direction(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized.startswith("BUY") or normalized in {"BULL", "BULLISH"}:
        return "BUY"
    if normalized.startswith("SELL") or normalized in {"BEAR", "BEARISH"}:
        return "SELL"
    return "HOLD"


def _public_side(side: str) -> str:
    if side == "BUY":
        return "buy"
    if side == "SELL":
        return "sell"
    return "hold"


def _opposite(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return {}


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for item in cast(Sequence[Any], value):
        if isinstance(item, Mapping):
            rows.append(dict(cast(Mapping[str, Any], item)))
    return rows


def _mean(values: Sequence[float], default: float = 0.0) -> float:
    if not values:
        return float(default)
    return float(sum(values) / max(1, len(values)))


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(float(value) for value in values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = max(0.0, min(1.0, float(q))) * float(len(sorted_values) - 1)
    lower = int(position)
    upper = min(len(sorted_values) - 1, lower + 1)
    fraction = position - float(lower)
    return float(sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction)


def _setup_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if "reversal" in text:
        return "reversal"
    if "impulse" in text:
        return "impulse"
    if "continuation" in text:
        return "continuation"
    if "consolidation" in text or "compression" in text:
        return "compression"
    if "current pressure" in text:
        return "current_pressure"
    return text.replace(" ", "_")[:48]


def _freshness(age_candles: float, ttl_candles: float) -> float:
    ttl = max(1.0, float(ttl_candles))
    age = max(0.0, float(age_candles))
    if age <= 2.0:
        return 1.0
    return _clip01(exp(-max(0.0, age - 2.0) / ttl), 1.0)


def _proximity_factor(distance_to_trigger: Any) -> float:
    distance = _clip01(distance_to_trigger, 1.0)
    if distance <= 0.03:
        return 1.0
    return _clip01(0.24 + 0.76 * (1.0 - distance), 0.24)


def _scope_weight(scope: Any) -> float:
    normalized = str(scope or "").strip().lower()
    if normalized in {"current", "micro", "latest", "trigger"}:
        return 1.16
    if normalized in {"local", "sniper"}:
        return 1.08
    if normalized in {"global", "structure"}:
        return 0.92
    if normalized in {"model", "ensemble", "vision"}:
        return 1.00
    return 1.00


def _evidence_family(scope: Any) -> str:
    normalized = str(scope or "").strip().lower()
    if normalized in {"global", "structure", "support", "resistance", "smart_money", "order_block", "fair_value_gap", "liquidity_sweep"}:
        return "structure"
    if normalized in {"local", "sniper", "trigger", "entry"}:
        return "timing"
    if normalized in {"current", "micro", "latest", "candle_count", "behavior"}:
        return "momentum"
    if normalized in {"model", "ensemble", "vision", "probability"}:
        return "model"
    if normalized in {"memory", "historical"}:
        return "memory"
    if normalized in {"opposition", "risk", "failure"}:
        return "risk"
    return "other"


def _normalize_probabilities(values: Mapping[str, float]) -> dict[str, float]:
    raw = {key: max(0.0, float(value)) for key, value in values.items()}
    total = sum(raw.values())
    if total <= 1e-9:
        fallback = 1.0 / max(1, len(raw))
        return {key: fallback for key in raw}
    return {key: float(value / total) for key, value in raw.items()}


def _sigmoid(value: float) -> float:
    clipped = max(-24.0, min(24.0, float(value)))
    return float(1.0 / (1.0 + exp(-clipped)))


def _belief_engine(
    signals: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, float | str],
    *,
    conflict_score: float,
    failure_risk: float,
    congestion: float,
) -> BeliefState:
    family_totals: dict[str, float] = {}
    side_family_totals: dict[tuple[str, str], float] = {}
    for row in signals:
        side = _direction(row.get("side", "HOLD"))
        family = str(row.get("family", _evidence_family(row.get("zone_type", ""))) or "other")
        amount = max(0.0, _safe_float(row.get("evidence", 0.0), 0.0))
        family_totals[family] = float(family_totals.get(family, 0.0) + amount)
        if side in SIDES:
            key = (side, family)
            side_family_totals[key] = float(side_family_totals.get(key, 0.0) + amount)

    capped_buy = sum(min(0.34, amount) for (side, _family), amount in side_family_totals.items() if side == "BUY")
    capped_sell = sum(min(0.34, amount) for (side, _family), amount in side_family_totals.items() if side == "SELL")
    total = max(1e-9, capped_buy + capped_sell)
    directional_edge = abs(capped_buy - capped_sell) / total
    evidence_mass = _clip01(total / 0.90, 0.0)
    usable_bias = _clip01(directional_edge * evidence_mass, 0.0)
    uncertainty = _clip01(
        0.36 * (1.0 - evidence_mass)
        + 0.28 * conflict_score
        + 0.20 * failure_risk
        + 0.10 * congestion
        + 0.06 * float(abs(capped_buy - capped_sell) < 0.04),
        0.0,
    )
    log_odds_buy_sell = 3.4 * (capped_buy - capped_sell) - 0.55 * conflict_score - 0.35 * failure_risk
    directional_prob = _sigmoid(log_odds_buy_sell)
    buy_raw = directional_prob * evidence_mass * (1.0 - 0.40 * uncertainty)
    sell_raw = (1.0 - directional_prob) * evidence_mass * (1.0 - 0.40 * uncertainty)
    hold_raw = 0.10 + 0.70 * uncertainty + 0.35 * (1.0 - evidence_mass) + 0.18 * conflict_score

    legacy_side = str(evidence.get("dominant_side", "HOLD"))
    if legacy_side == "BUY":
        buy_raw += 0.08 * usable_bias
    elif legacy_side == "SELL":
        sell_raw += 0.08 * usable_bias
    else:
        hold_raw += 0.12

    belief = _normalize_probabilities({"buy": buy_raw, "sell": sell_raw, "hold": hold_raw})
    reason_codes: list[str] = ["BELIEF_ENGINE_READY"]
    if evidence_mass < 0.35:
        reason_codes.append("EVIDENCE_MASS_LOW")
    if conflict_score >= 0.62:
        reason_codes.append("CONFLICT_ELEVATED")
    if uncertainty >= 0.58:
        reason_codes.append("UNCERTAINTY_HIGH")
    if usable_bias >= 0.40:
        reason_codes.append("DIRECTIONAL_EDGE_USABLE")
    return BeliefState(
        buy=_clip01(belief["buy"]),
        sell=_clip01(belief["sell"]),
        hold=_clip01(belief["hold"]),
        uncertainty=uncertainty,
        conflict=_clip01(conflict_score),
        directional_edge=_clip01(directional_edge),
        evidence_mass=evidence_mass,
        usable_bias=usable_bias,
        family_totals=dict(family_totals),
        reason_codes=tuple(reason_codes),
    )


def _alignment_for_side(side: str, context: Mapping[str, Any]) -> float:
    if side not in SIDES:
        return 0.35
    global_direction = _direction(context.get("global_direction", context.get("global_bias", "HOLD")))
    local_direction = _direction(context.get("local_direction", context.get("local_bias", "HOLD")))
    current_direction = _direction(context.get("current_direction", context.get("impulse_direction", "HOLD")))
    alignment = 0.24
    if global_direction == side:
        alignment += 0.26
    elif global_direction == _opposite(side):
        alignment -= 0.14
    if local_direction == side:
        alignment += 0.30
    elif local_direction == _opposite(side):
        alignment -= 0.18
    if current_direction == side:
        alignment += 0.20
    elif current_direction == _opposite(side):
        alignment -= 0.12
    return _clip01(alignment, 0.35)


def _ttl_from_context(context: Mapping[str, Any]) -> float:
    explicit = _safe_float(context.get("ttl_candles", context.get("max_valid_age", 0.0)), 0.0)
    if explicit > 0.0:
        return float(explicit)
    setup_family = _setup_family(context.get("setup", context.get("setup_family", "")))
    if setup_family == "compression":
        return 9.0
    if setup_family == "reversal":
        return 7.0
    if setup_family == "impulse":
        return 6.0
    return 8.0


def _context_age(context: Mapping[str, Any], signals: Sequence[Mapping[str, Any]]) -> float:
    clocks = _mapping(context.get("clocks", {}))
    direct = _safe_float(
        context.get(
            "setup_age_candles",
            clocks.get("candles_since_thesis", clocks.get("candles_since_last_strengthening", -1.0)),
        ),
        -1.0,
    )
    if direct >= 0.0:
        return float(direct)
    ages = [
        max(0.0, _safe_float(row.get("age_candles", 0.0), 0.0))
        for row in signals
        if _direction(row.get("side", row.get("direction", "HOLD"))) in SIDES
    ]
    return _mean(ages, 0.0)


def _normalized_signals(snapshot: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _mapping_rows(snapshot.get("signals", []))
    if not rows:
        probabilities = _mapping(snapshot.get("probabilities", context.get("probabilities", {})))
        for side in ("BUY", "SELL"):
            confidence = _clip01(probabilities.get(side, 0.0), 0.0)
            if confidence > 0.0:
                rows.append(
                    {
                        "side": side,
                        "confidence": confidence,
                        "quality": confidence,
                        "zone_type": "probability",
                        "age_candles": context.get("setup_age_candles", 0),
                    }
                )

    ttl = _ttl_from_context(context)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        side = _direction(row.get("side", row.get("direction", "HOLD")))
        if side not in SIDES:
            continue
        confidence = _clip01(row.get("confidence", row.get("score", 0.0)), 0.0)
        quality = _clip01(row.get("quality", confidence), confidence)
        if confidence <= 0.0 or quality <= 0.0:
            continue
        age = max(0.0, _safe_float(row.get("age_candles", context.get("setup_age_candles", 0.0)), 0.0))
        freshness = _clip01(row.get("freshness", _freshness(age, ttl)), _freshness(age, ttl))
        alignment = _clip01(row.get("alignment", _alignment_for_side(side, context)), _alignment_for_side(side, context))
        proximity = _clip01(
            row.get(
                "proximity_factor",
                _proximity_factor(row.get("distance_to_trigger", context.get("distance_to_trigger", 1.0))),
            ),
            0.5,
        )
        persistence = _clip01(row.get("persistence_factor", row.get("persistence", 0.58)), 0.58)
        model_weight = max(
            0.05,
            _safe_float(
                row.get("weight_model", row.get("model_weight", row.get("weight", _scope_weight(row.get("zone_type", ""))))),
                1.0,
            ),
        )
        evidence = model_weight * confidence * quality * freshness * alignment * proximity * persistence
        normalized.append(
            {
                "side": side,
                "confidence": confidence,
                "quality": quality,
                "zone_type": str(row.get("zone_type", row.get("scope", "signal")) or "signal").lower(),
                "family": _evidence_family(row.get("zone_type", row.get("scope", "signal"))),
                "age_candles": float(age),
                "freshness": freshness,
                "alignment": alignment,
                "proximity_factor": proximity,
                "persistence_factor": persistence,
                "model_weight": float(model_weight),
                "evidence": float(max(0.0, evidence)),
            }
        )
    return normalized


def _evidence_summary(signals: Sequence[Mapping[str, Any]]) -> dict[str, float | str]:
    buy = sum(_safe_float(row.get("evidence", 0.0), 0.0) for row in signals if row.get("side") == "BUY")
    sell = sum(_safe_float(row.get("evidence", 0.0), 0.0) for row in signals if row.get("side") == "SELL")
    total = max(1e-9, buy + sell)
    spread = abs(sell - buy)
    dominant_side = "BUY" if buy > sell + 1e-6 else "SELL" if sell > buy + 1e-6 else "HOLD"
    raw_strength = spread / max(total, 1e-9)
    volume_strength = max(buy, sell) / max(0.72, max(buy, sell) + 0.40)
    bias_strength = _clip01(0.72 * raw_strength + 0.28 * volume_strength, 0.0)
    if bias_strength < 0.10 or max(buy, sell) < 0.05:
        dominant_side = "HOLD"
    conflict = _clip01(1.0 - raw_strength, 0.0) if total > 1e-8 else 1.0
    return {
        "buy_evidence": float(buy),
        "sell_evidence": float(sell),
        "net_bias": float(sell - buy),
        "dominant_side": dominant_side,
        "bias_strength": bias_strength,
        "conflict_score": conflict,
    }


def _distance_payload(snapshot: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, float]:
    distances = _mapping(snapshot.get("distances", context.get("distances", {})))
    latest_token = _mapping(snapshot.get("latest_token", context.get("latest_token", {})))
    trigger = _clip01(
        distances.get(
            "trigger",
            context.get("distance_to_trigger", latest_token.get("distance_to_trigger", 1.0)),
        ),
        1.0,
    )
    target = _clip01(
        distances.get(
            "target",
            context.get("distance_to_target", latest_token.get("distance_to_target", 1.0)),
        ),
        1.0,
    )
    invalidation = _clip01(
        distances.get(
            "invalidation",
            context.get("distance_to_invalidation", latest_token.get("distance_to_invalidation", 1.0)),
        ),
        1.0,
    )
    return {
        "trigger": trigger,
        "target": target,
        "invalidation": invalidation,
    }


def _directional_speed(snapshot: Mapping[str, Any], context: Mapping[str, Any]) -> float:
    explicit = _safe_float(snapshot.get("directional_speed", context.get("directional_speed", 0.0)), 0.0)
    if explicit > 0.0:
        return float(max(0.006, min(1.0, explicit)))
    candle_stats = _mapping(snapshot.get("candle_statistics", context.get("candle_statistics", {})))
    average_step = _safe_float(candle_stats.get("average_step", context.get("average_step", 0.0)), 0.0)
    momentum = _clip01(candle_stats.get("momentum_consistency", context.get("momentum_consistency", 0.0)), 0.0)
    impulse = abs(_safe_float(context.get("impulse_delta", 0.0), 0.0))
    return float(max(0.006, min(1.0, max(average_step * (0.55 + momentum), impulse * 0.62))))


def _eta(distance: float, speed: float, penalty: float, *, high: int = 24) -> int:
    if distance <= 0.025:
        return 1
    raw = distance / max(0.006, speed)
    return int(max(1, min(high, ceil(raw * max(0.35, penalty)))))


def _memory_lookup(snapshot: Mapping[str, Any], context: Mapping[str, Any], side: str) -> dict[str, Any]:
    rows = _mapping_rows(snapshot.get("memory_rows", context.get("memory_rows", [])))
    setup = _setup_family(context.get("setup", context.get("setup_family", "")))
    pair_key = str(context.get("pair", context.get("market", snapshot.get("pair", ""))) or "").strip().upper()
    timeframe_key = str(context.get("timeframe", snapshot.get("timeframe", "")) or "").strip().upper()
    similar: list[dict[str, Any]] = []
    for row in rows:
        row_side = _direction(row.get("dominant_side", row.get("action", row.get("side", "HOLD"))))
        row_setup = _setup_family(row.get("setup", row.get("setup_family", "")))
        side_match = side in SIDES and row_side == side
        setup_match = setup != "unknown" and row_setup == setup
        if side_match and (setup_match or row_setup == "unknown"):
            similar.append(row)

    pair_specific = [
        row
        for row in similar
        if pair_key
        and str(row.get("market", row.get("pair", "")) or "").strip().upper() == pair_key
    ]
    timeframe_specific = [
        row
        for row in similar
        if timeframe_key
        and str(row.get("timeframe", "") or "").strip().upper() == timeframe_key
    ]
    duration_rows = pair_specific if len(pair_specific) >= 3 else timeframe_specific if len(timeframe_specific) >= 3 else similar
    medians: dict[str, float] = {}
    quantiles: dict[str, dict[str, float]] = {}
    for key in ("candles_to_trigger", "candles_to_target", "candles_to_invalidation", "candles_to_stale"):
        values = [
            _safe_float(row.get(key, 0.0), 0.0)
            for row in duration_rows
            if _safe_float(row.get(key, 0.0), 0.0) > 0.0
        ]
        if values:
            medians[key] = float(median(values))
            quantiles[key] = {"q20": _quantile(values, 0.20), "q80": _quantile(values, 0.80)}
    triggered = sum(1 for row in similar if bool(row.get("triggered", False)))
    target_first = sum(1 for row in similar if bool(row.get("target_before_invalidation", False)))
    similarity_values = [
        _clip01(row.get("similarity", row.get("memory_similarity", row.get("similarity_score", 1.0))), 1.0)
        for row in similar
    ]
    similarity_score = _mean(similarity_values, 1.0 if similar else 0.0)
    memory_weight = float(len(similar) / (float(len(similar)) + MEMORY_SHRINKAGE_K)) if similar else 0.0
    memory_confidence = _clip01(memory_weight * similarity_score, 0.0)
    return {
        "similar_setup_count": int(len(similar)),
        "duration_sample_count": int(len(duration_rows)),
        "pair_specific_count": int(len(pair_specific)),
        "timeframe_specific_count": int(len(timeframe_specific)),
        "setup_family": setup,
        "median_durations": medians,
        "duration_quantiles": quantiles,
        "trigger_rate": float(triggered / max(1, len(similar))) if similar else 0.0,
        "target_before_invalidation_rate": float(target_first / max(1, len(similar))) if similar else 0.0,
        "memory_weight": memory_weight,
        "memory_similarity_score": similarity_score,
        "memory_confidence": memory_confidence,
        "memory_enabled": bool(memory_confidence > 0.0),
        "memory_shrinkage_k": MEMORY_SHRINKAGE_K,
    }


def _state_machine(
    *,
    dominant_side: str,
    bias_strength: float,
    freshness: float,
    age_candles: float,
    ttl_candles: float,
    alignment: float,
    proximity: float,
    conflict_score: float,
    context: Mapping[str, Any],
) -> str:
    entry_state = str(context.get("entry_state", context.get("setup_state", "")) or "").strip().upper()
    execution_action = _direction(context.get("execution_action", "HOLD"))
    target_reached = bool(context.get("target_reached", False))
    active = bool(context.get("active", False))
    if target_reached or entry_state == "COMPLETE":
        return "COMPLETE"
    if entry_state == "INVALIDATED" or bool(context.get("invalidated", False)):
        return "INVALIDATED"
    if active or entry_state == "ACTIVE":
        return "ACTIVE"
    if dominant_side not in SIDES:
        return "IDLE"
    if freshness <= 0.18 or age_candles > ttl_candles:
        return "STALE"
    if entry_state in {"TRIGGERED", "TRIGGER_READY", "SNIPER_READY"} or execution_action == dominant_side:
        return "TRIGGERED"
    armed_score = _clip01(0.40 * bias_strength + 0.24 * proximity + 0.20 * alignment + 0.16 * freshness - 0.18 * conflict_score)
    if armed_score >= 0.54 and bias_strength >= 0.34 and conflict_score <= 0.68:
        return "ARMED"
    if bias_strength >= 0.18 or max(alignment, proximity) >= 0.45:
        return "WATCH"
    return "IDLE"


def _decision_for_state(state: str) -> str:
    if state == "TRIGGERED":
        return "TRIGGER_CONFIRMED"
    if state == "ACTIVE":
        return "MANAGE_ACTIVE"
    if state == "ARMED":
        return "WATCH_FOR_TRIGGER"
    if state == "WATCH":
        return "WAIT_FOR_SETUP_MATURITY"
    if state in {"STALE", "INVALIDATED"}:
        return "CANCEL_SETUP"
    if state == "COMPLETE":
        return "COMPLETE"
    return "STAND_ASIDE"


def _target_race_probabilities(
    *,
    p_target_before_invalidation: float,
    p_expire_before_trigger: float,
    state: str,
) -> dict[str, float]:
    if state in {"STALE", "INVALIDATED", "COMPLETE"}:
        return {"target": 0.0, "stop": 0.0 if state != "INVALIDATED" else 1.0, "expiry": 1.0 if state == "STALE" else 0.0, "no_event": 0.0}
    target_score = _clip01(p_target_before_invalidation, 0.0)
    stop_score = _clip01(1.0 - p_target_before_invalidation, 0.0)
    expiry_score = _clip01(0.35 * p_expire_before_trigger, 0.0)
    no_event_score = _clip01(0.12 + 0.20 * p_expire_before_trigger, 0.0)
    return _normalize_probabilities(
        {
            "target": target_score,
            "stop": stop_score,
            "expiry": expiry_score,
            "no_event": no_event_score,
        }
    )


def _expected_utility(
    *,
    target_race: Mapping[str, float],
    distances: Mapping[str, float],
    context: Mapping[str, Any],
    belief: BeliefState,
    failure_risk: float,
    congestion: float,
    conflict_score: float,
) -> ExpectedUtility:
    reward_R = _safe_float(context.get("reward_R", context.get("reward_r", 0.0)), 0.0)
    if reward_R <= 0.0:
        reward_R = max(0.25, min(5.0, float(distances.get("target", 1.0)) / max(0.05, float(distances.get("invalidation", 1.0)))))
    loss_R = _safe_float(context.get("loss_R", context.get("risk_R", 1.0)), 1.0)
    loss_R = max(0.25, min(3.0, loss_R))
    cost_R = _safe_float(context.get("cost_R", context.get("cost_r", context.get("spread_cost_R", 0.02))), 0.02)
    cost_R = max(0.0, min(1.0, cost_R))
    p_win = _clip01(target_race.get("target", 0.0), 0.0)
    p_loss = _clip01(target_race.get("stop", 0.0), 0.0)
    raw_ev = float(p_win * reward_R - p_loss * loss_R - cost_R)
    uncertainty_tax = _clip01(
        0.36 * belief.uncertainty
        + 0.18 * conflict_score
        + 0.16 * failure_risk
        + 0.08 * congestion
        + _safe_float(context.get("drawdown_penalty_R", 0.0), 0.0),
        0.0,
    )
    return ExpectedUtility(
        raw_ev_R=raw_ev,
        adjusted_ev_R=float(raw_ev - uncertainty_tax),
        uncertainty_tax_R=uncertainty_tax,
        reward_R=float(reward_R),
        loss_R=float(loss_R),
        cost_R=float(cost_R),
    )


def _confidence_tier(state: str, belief: BeliefState, utility: ExpectedUtility, conflict_score: float) -> str:
    if state in {"STALE", "INVALIDATED", "COMPLETE"}:
        return "X"
    if belief.uncertainty >= 0.70 or conflict_score >= 0.76:
        return "D"
    if utility.adjusted_ev_R >= 0.35 and belief.usable_bias >= 0.55 and belief.uncertainty <= 0.34:
        return "A+"
    if utility.adjusted_ev_R >= 0.12 and belief.usable_bias >= 0.38 and belief.uncertainty <= 0.50:
        return "A"
    if state in {"WATCH", "ARMED", "TRIGGERED", "ACTIVE"} and belief.usable_bias >= 0.20:
        return "B"
    return "C"


def _firewall_advisory(state: str, belief: BeliefState, utility: ExpectedUtility, next_event: str) -> FirewallAdvisory:
    reasons: list[str] = ["ADVISORY_FIREWALL_ONLY"]
    if state in {"STALE", "INVALIDATED", "COMPLETE"}:
        reasons.append(f"STATE_{state}")
        return FirewallAdvisory(action="WAIT", reasons=tuple(reasons))
    if belief.uncertainty >= 0.68:
        reasons.append("UNCERTAINTY_TOO_HIGH")
        return FirewallAdvisory(action="ABSTAIN", reasons=tuple(reasons))
    if utility.adjusted_ev_R < 0.0:
        reasons.append("EXPECTED_VALUE_NEGATIVE")
        return FirewallAdvisory(action="WAIT", reasons=tuple(reasons))
    if next_event in {"invalidation", "stale"} and state not in {"TRIGGERED", "ACTIVE"}:
        reasons.append(f"NEXT_EVENT_{next_event.upper()}")
        return FirewallAdvisory(action="WAIT", reasons=tuple(reasons))
    if state in {"TRIGGERED", "ACTIVE"} and utility.adjusted_ev_R >= 0.0:
        reasons.append("STATE_ALLOWS_ACTION")
        reasons.append("EXPECTED_VALUE_NONNEGATIVE")
        return FirewallAdvisory(action="ALLOW", reasons=tuple(reasons))
    if state == "ARMED":
        reasons.append("STATE_ARMED")
        return FirewallAdvisory(action="WAIT", reasons=tuple(reasons))
    reasons.append("STATE_NOT_EXECUTABLE")
    return FirewallAdvisory(action="WAIT", reasons=tuple(reasons))


def _reason_codes(
    *,
    state: str,
    dominant_side: str,
    belief: BeliefState,
    utility: ExpectedUtility,
    next_event: str,
    firewall: FirewallAdvisory,
) -> tuple[str, ...]:
    codes: list[str] = [f"STATE_{state}", *belief.reason_codes]
    if dominant_side in SIDES:
        codes.append(f"DIRECTION_{dominant_side}_VALID")
    else:
        codes.append("DIRECTION_HOLD")
    if utility.adjusted_ev_R >= 0.0:
        codes.append("EXPECTED_VALUE_NONNEGATIVE")
    else:
        codes.append("EXPECTED_VALUE_NEGATIVE")
    codes.append(f"NEXT_EVENT_{next_event.upper()}")
    codes.append(f"FIREWALL_{firewall.action}")
    for reason in firewall.reasons:
        if reason not in codes:
            codes.append(reason)
    return tuple(dict.fromkeys(codes))


def _recent_tokens(context: Mapping[str, Any], behavior: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _mapping_rows(context.get("candle_tokens", behavior.get("candle_tokens", [])))
    return rows[-12:]


def _age_since_event(tokens: Sequence[Mapping[str, Any]], patterns: Sequence[str], side: str = "") -> int:
    if not tokens:
        return 0
    normalized_side = _direction(side)
    lowered_patterns = [str(pattern).lower() for pattern in patterns if str(pattern).strip()]
    for offset, token in enumerate(reversed(tokens)):
        event = str(token.get("micro_structure_event", "") or "").lower()
        token_side = _direction(token.get("direction", "HOLD"))
        side_ok = normalized_side not in SIDES or token_side == normalized_side
        if side_ok and any(pattern in event for pattern in lowered_patterns):
            return int(offset)
    return int(len(tokens))


def _candle_clocks(tokens: Sequence[Mapping[str, Any]], side: str) -> dict[str, int]:
    opposite = _opposite(side)
    return {
        "candles_since_strengthening": _age_since_event(tokens, ("rejection", "impulse", "continuation"), side),
        "candles_since_rejection": _age_since_event(tokens, ("rejection",), side),
        "candles_since_trigger_touch": _age_since_event(tokens, ("trigger", "breakout"), side),
        "candles_since_momentum_peak": _age_since_event(tokens, ("impulse",), side),
        "candles_since_opposition": _age_since_event(tokens, ("reversal", "failed", "exhaustion", "pullback"), opposite),
    }


def _normalize_three(values: Mapping[str, float]) -> dict[str, float]:
    raw = {key: max(0.0, float(value)) for key, value in values.items()}
    total = sum(raw.values())
    if total <= 1e-9:
        return {"buy": 1.0 / 3.0, "sell": 1.0 / 3.0, "hold": 1.0 / 3.0}
    return {key: float(value / total) for key, value in raw.items()}


def _next_candle_forecast(
    *,
    dominant_side: str,
    state: str,
    bias_strength: float,
    freshness: float,
    conflict_score: float,
    failure_risk: float,
    congestion: float,
    opposing_ratio: float,
    persistence: float,
    distances: Mapping[str, float],
    candle_stats: Mapping[str, Any],
    behavior: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    tokens = _recent_tokens(context, behavior)
    recent = tokens[-5:]
    buy_count = sum(1 for token in recent if _direction(token.get("direction", "HOLD")) == "BUY")
    sell_count = sum(1 for token in recent if _direction(token.get("direction", "HOLD")) == "SELL")
    total_recent = max(1, buy_count + sell_count)
    buy_ratio = buy_count / total_recent
    sell_ratio = sell_count / total_recent
    latest_side = _direction(recent[-1].get("direction", "HOLD")) if recent else "HOLD"
    current_side = _direction(context.get("current_direction", context.get("impulse_direction", "HOLD")))
    local_side = _direction(context.get("local_direction", "HOLD"))
    global_side = _direction(context.get("global_direction", "HOLD"))
    major_side = _direction(context.get("major_trend_side", context.get("field_trend_side", global_side)))
    major_confidence = _clip01(context.get("major_trend_confidence", context.get("field_trend_confidence", 0.0)), 0.0)
    countertrend_enabled = bool(context.get("allow_countertrend_scalp", False))
    current_state = str(behavior.get("current_state", "") or "").lower()
    next_state = str(behavior.get("next_most_likely_state", "") or "").lower()
    events = [str(token.get("micro_structure_event", "") or "").lower() for token in recent]
    reversal_event = any("reversal" in event or "failed" in event for event in events) or "reversal" in current_state
    pullback_event = any("pullback" in event for event in events) or "pullback" in current_state or "pullback" in next_state
    exhaustion_event = any("exhaustion" in event for event in events) or "exhaustion" in current_state
    compression_event = any("compression" in event or "pause" in event for event in events) or "compression" in current_state
    near_trigger = _clip01(1.0 - float(distances.get("trigger", 1.0)), 0.0)
    near_invalidation = _clip01(1.0 - float(distances.get("invalidation", 1.0)), 0.0)
    candidate_ratio = _clip01(candle_stats.get("candidate_ratio", 0.0), 0.0)

    buy_score = 0.22 + 0.42 * buy_ratio
    sell_score = 0.22 + 0.42 * sell_ratio
    hold_score = 0.16 + 0.26 * congestion + 0.18 * conflict_score

    for side, amount in ((global_side, 0.07), (local_side, 0.10), (current_side, 0.13), (latest_side, 0.10)):
        if side == "BUY":
            buy_score += amount
        elif side == "SELL":
            sell_score += amount

    if dominant_side == "BUY":
        buy_score += 0.16 * bias_strength + 0.08 * persistence
        sell_score += 0.10 * opposing_ratio + 0.14 * failure_risk
    elif dominant_side == "SELL":
        sell_score += 0.16 * bias_strength + 0.08 * persistence
        buy_score += 0.10 * opposing_ratio + 0.14 * failure_risk

    if reversal_event or exhaustion_event:
        if dominant_side == "BUY":
            sell_score += 0.20 + 0.14 * near_invalidation
        elif dominant_side == "SELL":
            buy_score += 0.20 + 0.14 * near_invalidation
        hold_score += 0.07
    if pullback_event and dominant_side in SIDES:
        if dominant_side == "BUY":
            sell_score += 0.14
        else:
            buy_score += 0.14
    if compression_event:
        hold_score += 0.16
    if near_trigger >= 0.72 and dominant_side in SIDES and state in {"WATCH", "ARMED", "TRIGGERED"}:
        if dominant_side == "BUY":
            buy_score += 0.12
        else:
            sell_score += 0.12
    if state in {"STALE", "INVALIDATED", "COMPLETE"}:
        hold_score += 0.22
        if dominant_side in SIDES:
            if dominant_side == "BUY":
                buy_score *= 0.84
            else:
                sell_score *= 0.84

    probs = _normalize_three({"buy": buy_score, "sell": sell_score, "hold": hold_score})
    next_bias = max(probs.items(), key=lambda item: item[1])[0].upper()
    if next_bias == "HOLD":
        next_side = "HOLD"
    else:
        next_side = next_bias
    opposite = _opposite(dominant_side)
    countertrend_pressure = (
        next_side == opposite
        and dominant_side in SIDES
        and countertrend_enabled
        and (
            probs[next_side.lower()] >= 0.46
            or reversal_event
            or exhaustion_event
            or (pullback_event and opposing_ratio >= 0.24)
        )
    )
    micro_pullback_against_major = bool(
        not countertrend_enabled
        and next_side == opposite
        and dominant_side in SIDES
        and major_side in {dominant_side, "HOLD"}
    )
    trend_follow_pressure = (
        next_side == dominant_side
        and dominant_side in SIDES
        and probs[next_side.lower()] >= 0.44
        and state in {"WATCH", "ARMED", "TRIGGERED", "ACTIVE"}
        and freshness >= 0.24
    )

    if countertrend_pressure and probs[next_side.lower()] >= 0.42:
        trade_mode = "COUNTERTREND_SCALP"
        execution_side = next_side
        hold_for = 1 if failure_risk < 0.46 and not reversal_event else 2
        instruction = (
            f"{next_side} is a short countertrend read against the {dominant_side} thesis; "
            f"treat it as {hold_for} candle(s), not a full trend flip."
        )
    elif trend_follow_pressure:
        trade_mode = "TREND_FOLLOW"
        execution_side = dominant_side
        hold_for = 2 if state == "ARMED" else 3 if state in {"TRIGGERED", "ACTIVE"} else 1
        instruction = f"{dominant_side} remains the trend-follow side; hold the read for about {hold_for} candle(s) unless invalidation pressure rises."
    elif dominant_side in SIDES and next_side == opposite:
        trade_mode = "PULLBACK_WAIT"
        execution_side = "HOLD"
        hold_for = 1
        instruction = f"{opposite} pressure looks like a pullback inside the {dominant_side} thesis; wait for rejection or a cleaner countertrend break."
    elif state in {"STALE", "INVALIDATED", "COMPLETE"}:
        trade_mode = "RESET_WAIT"
        execution_side = "HOLD"
        hold_for = 0
        instruction = "The active setup is not fresh enough; wait for a new M5 candle sequence to reset the thesis."
    else:
        trade_mode = "STAND_ASIDE"
        execution_side = "HOLD"
        hold_for = 0
        instruction = "The next candle read is not clean enough for trend-follow or countertrend execution."

    clocks = _candle_clocks(tokens, dominant_side)
    return {
        "next_candle_bias": _public_side(next_side),
        "p_next_buy": probs["buy"],
        "p_next_sell": probs["sell"],
        "p_next_hold": probs["hold"],
        "trade_mode": trade_mode,
        "execution_side": _public_side(execution_side),
        "dominant_trend_side": _public_side(dominant_side),
        "countertrend_side": _public_side(opposite if countertrend_pressure else "HOLD"),
        "countertrend_window_candles": int(hold_for if trade_mode == "COUNTERTREND_SCALP" else 0),
        "trend_follow_window_candles": int(hold_for if trade_mode == "TREND_FOLLOW" else 0),
        "hold_for_candles": int(hold_for),
        "major_trend_side": _public_side(major_side),
        "major_trend_confidence": major_confidence,
        "countertrend_scalp_enabled": bool(countertrend_enabled),
        "micro_pullback_against_major": micro_pullback_against_major,
        "pullback_active": bool(pullback_event),
        "reversal_pressure": bool(reversal_event or exhaustion_event),
        "compression_active": bool(compression_event),
        "candidate_ratio": candidate_ratio,
        "instruction": instruction,
        "candle_clocks": clocks,
    }


def _market_conversation(
    *,
    dominant_side: str,
    state: str,
    next_candle: Mapping[str, Any],
    visible_candles: int,
    distances: Mapping[str, float],
    freshness: float,
) -> str:
    side = _public_side(dominant_side).upper()
    next_bias = str(next_candle.get("next_candle_bias", "hold") or "hold").upper()
    mode = str(next_candle.get("trade_mode", "STAND_ASIDE") or "STAND_ASIDE").replace("_", " ")
    hold_for = int(next_candle.get("hold_for_candles", 0) or 0)
    trigger_distance = float(distances.get("trigger", 1.0))
    if str(next_candle.get("trade_mode", "")) == "COUNTERTREND_SCALP":
        return (
            f"{visible_candles} M5 candles show {side} as the larger thesis, but the immediate candle conversation is {next_bias}. "
            f"That is a countertrend scalp window for about {hold_for} candle(s), not proof that the whole trend flipped."
        )
    if str(next_candle.get("trade_mode", "")) == "TREND_FOLLOW":
        return (
            f"{visible_candles} M5 candles keep {side} aligned with the next candle read. "
            f"Mode is {mode}; trigger distance is {trigger_distance:.2f}, freshness {freshness:.0%}."
        )
    if state in {"STALE", "INVALIDATED"}:
        return (
            f"{visible_candles} M5 candles still show a {side} thesis, but state is {state}. "
            "The old setup is expired; wait for a fresh candle sequence or a separate countertrend break."
        )
    return (
        f"{visible_candles} M5 candles are mixed at the next-candle level. "
        f"Mode is {mode}; next candle bias is {next_bias}, so execution should stay selective."
    )


def analyze_decision_kernel(input_snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = dict(input_snapshot or {})
    context = _mapping(snapshot.get("context", {}))
    if not context:
        context = dict(snapshot)
    signals = _normalized_signals(snapshot, context)
    evidence = _evidence_summary(signals)
    dominant_side = str(evidence["dominant_side"])
    bias_strength = float(evidence["bias_strength"])
    conflict_score = float(evidence["conflict_score"])
    ttl_candles = _ttl_from_context(context)
    setup_age_candles = _context_age(context, signals)
    freshness = _freshness(setup_age_candles, ttl_candles)
    alignment = _alignment_for_side(dominant_side, context) if dominant_side in SIDES else 0.0
    distances = _distance_payload(snapshot, context)
    proximity = _proximity_factor(distances["trigger"])

    candle_stats = _mapping(snapshot.get("candle_statistics", context.get("candle_statistics", {})))
    behavior = _mapping(snapshot.get("behavior", context.get("behavior", {})))
    box_context = _mapping(snapshot.get("box_context", context.get("box_context", behavior.get("box_context", {}))))
    probability = _mapping(snapshot.get("probability", context.get("probability", {})))
    congestion = _clip01(context.get("congestion_score", context.get("consolidation_score", 0.0)), 0.0)
    failure_risk = _clip01(box_context.get("failure_risk", context.get("failure_risk", 0.0)), 0.0)
    opposing_ratio = _clip01(candle_stats.get("opposing_ratio", context.get("opposing_ratio", 0.0)), 0.0)
    persistence = _clip01(candle_stats.get("momentum_consistency", context.get("persistence", 0.45)), 0.45)
    timing_score = _clip01(context.get("timing_score", context.get("entry_timing_score", 0.0)), 0.0)
    belief = _belief_engine(
        signals,
        evidence,
        conflict_score=conflict_score,
        failure_risk=failure_risk,
        congestion=congestion,
    )

    state = _state_machine(
        dominant_side=dominant_side,
        bias_strength=bias_strength,
        freshness=freshness,
        age_candles=setup_age_candles,
        ttl_candles=ttl_candles,
        alignment=alignment,
        proximity=proximity,
        conflict_score=conflict_score,
        context=context,
    )
    if state == "ARMED" and (belief.evidence_mass < 0.25 or belief.uncertainty >= 0.72):
        state = "WATCH"

    speed = _directional_speed(snapshot, context)
    penalty = (
        (1.0 + 0.70 * congestion)
        * (1.0 + 0.62 * conflict_score)
        * (1.0 + 0.58 * failure_risk)
    )
    eta_trigger = _eta(distances["trigger"], speed, penalty, high=24)
    eta_target = _eta(distances["target"], speed, penalty * 0.86, high=30)
    adverse_speed = max(0.006, speed * (0.42 + 0.85 * max(opposing_ratio, failure_risk, conflict_score * 0.45)))
    eta_invalidation = _eta(distances["invalidation"], adverse_speed, 1.0 + 0.35 * failure_risk, high=24)
    stale_after_candles = int(max(0, ceil(ttl_candles - setup_age_candles)))

    memory = _memory_lookup(snapshot, context, dominant_side)
    medians = cast(Mapping[str, Any], memory.get("median_durations", {}))
    memory_count = int(memory.get("similar_setup_count", 0) or 0)
    memory_weight = min(0.42, _clip01(memory.get("memory_weight", 0.0), 0.0))
    if memory_count > 0 and memory_weight > 0.0:
        trigger_median = _safe_float(medians.get("candles_to_trigger", 0.0), 0.0)
        target_median = _safe_float(medians.get("candles_to_target", 0.0), 0.0)
        invalidation_median = _safe_float(medians.get("candles_to_invalidation", 0.0), 0.0)
        stale_median = _safe_float(medians.get("candles_to_stale", 0.0), 0.0)
        if trigger_median > 0.0:
            eta_trigger = int(round((1.0 - memory_weight) * float(eta_trigger) + memory_weight * trigger_median))
        if target_median > 0.0:
            eta_target = int(round((1.0 - memory_weight) * float(eta_target) + memory_weight * target_median))
        if invalidation_median > 0.0:
            eta_invalidation = int(round((1.0 - memory_weight) * float(eta_invalidation) + memory_weight * invalidation_median))
        if stale_median > 0.0:
            stale_after_candles = int(round((1.0 - memory_weight) * float(stale_after_candles) + memory_weight * stale_median))

    target_first = _clip01(probability.get("target_first_probability", context.get("target_first_probability", 0.0)), 0.0)
    invalidation_first = _clip01(
        probability.get("invalidation_first_probability", context.get("invalidation_first_probability", 0.0)),
        0.0,
    )
    if target_first <= 0.0 and invalidation_first <= 0.0:
        target_first = _clip01(0.26 + 0.34 * bias_strength + 0.18 * alignment + 0.12 * persistence - 0.22 * failure_risk)
        invalidation_first = _clip01(0.18 + 0.30 * conflict_score + 0.24 * opposing_ratio + 0.22 * failure_risk)

    p_trigger_next_1 = _clip01(
        0.05
        + 0.34 * bias_strength
        + 0.25 * proximity
        + 0.14 * timing_score
        + 0.12 * persistence
        + (0.14 if state == "ARMED" else 0.30 if state == "TRIGGERED" else 0.0)
        - 0.22 * conflict_score
        - 0.12 * congestion
        - 0.18 * float(state in {"STALE", "INVALIDATED", "IDLE"}),
    )
    p_trigger_next_3 = _clip01(1.0 - ((1.0 - p_trigger_next_1) ** max(1, min(3, max(1, stale_after_candles)))))
    if state in {"INVALIDATED", "COMPLETE"}:
        p_trigger_next_1 = 0.0
        p_trigger_next_3 = 0.0
    elif state == "STALE":
        p_trigger_next_1 = min(p_trigger_next_1, 0.08)
        p_trigger_next_3 = min(p_trigger_next_3, 0.16)
    target_denominator = max(1e-6, target_first + invalidation_first)
    p_target_before_invalidation = _clip01(
        0.62 * (target_first / target_denominator)
        + 0.18 * bias_strength
        + 0.12 * alignment
        + 0.08 * persistence
        - 0.15 * failure_risk
        - 0.10 * conflict_score,
    )
    if memory_count > 0 and memory_weight > 0.0:
        observed_target_rate = _clip01(memory.get("target_before_invalidation_rate", 0.0), 0.0)
        if observed_target_rate > 0.0:
            target_memory_weight = min(0.28, memory_weight)
            p_target_before_invalidation = _clip01(
                (1.0 - target_memory_weight) * p_target_before_invalidation
                + target_memory_weight * observed_target_rate
            )

    age_ratio = _clip01(setup_age_candles / max(1.0, ttl_candles), 0.0)
    p_expire_before_trigger = _clip01(
        0.06
        + 0.38 * age_ratio
        + 0.18 * (1.0 - proximity)
        + 0.14 * conflict_score
        + 0.10 * congestion
        - 0.10 * bias_strength
        + 0.36 * float(state == "STALE"),
    )

    hazard_trigger = _clip01(p_trigger_next_1 * (1.18 if state == "ARMED" else 1.0))
    hazard_invalidation = _clip01(0.08 + 0.42 * invalidation_first + 0.22 * failure_risk + 0.18 * opposing_ratio + 0.10 * conflict_score)
    hazard_expiry = _clip01(0.05 + 0.44 * p_expire_before_trigger + 0.20 * age_ratio + 0.10 * (1.0 - freshness))
    hazard_total = max(1e-6, hazard_trigger + hazard_invalidation + hazard_expiry)
    next_event_scores = {
        "trigger": float(hazard_trigger / hazard_total),
        "invalidation": float(hazard_invalidation / hazard_total),
        "stale": float(hazard_expiry / hazard_total),
    }
    competing_event_probabilities = _normalize_probabilities(
        {
            "trigger": hazard_trigger,
            "invalidation": hazard_invalidation,
            "expiry": hazard_expiry,
            "no_event": _clip01(0.12 + 0.28 * (1.0 - max(hazard_trigger, hazard_invalidation, hazard_expiry))),
        }
    )
    next_event = max(next_event_scores, key=lambda key: next_event_scores[key])
    if state == "INVALIDATED":
        next_event = "invalidation"
        competing_event_probabilities = {"trigger": 0.0, "invalidation": 1.0, "expiry": 0.0, "no_event": 0.0}
    elif state == "STALE":
        next_event = "stale"
        competing_event_probabilities = {"trigger": 0.0, "invalidation": 0.0, "expiry": 1.0, "no_event": 0.0}
    elif state == "COMPLETE":
        next_event = "complete"
        competing_event_probabilities = {"trigger": 0.0, "invalidation": 0.0, "expiry": 0.0, "no_event": 1.0}
    elif state == "TRIGGERED":
        next_event = "target" if p_target_before_invalidation >= 0.50 else "invalidation"

    target_race = _target_race_probabilities(
        p_target_before_invalidation=p_target_before_invalidation,
        p_expire_before_trigger=p_expire_before_trigger,
        state=state,
    )
    utility = _expected_utility(
        target_race=target_race,
        distances=distances,
        context=context,
        belief=belief,
        failure_risk=failure_risk,
        congestion=congestion,
        conflict_score=conflict_score,
    )
    firewall = _firewall_advisory(state, belief, utility, next_event)
    confidence_tier = _confidence_tier(state, belief, utility, conflict_score)
    reason_codes = _reason_codes(
        state=state,
        dominant_side=dominant_side,
        belief=belief,
        utility=utility,
        next_event=next_event,
        firewall=firewall,
    )

    visible_candles = int(
        candle_stats.get(
            "sample_size",
            context.get("visible_candle_count", context.get("sample_size", 0)),
        )
        or 0
    )
    next_candle = _next_candle_forecast(
        dominant_side=dominant_side,
        state=state,
        bias_strength=bias_strength,
        freshness=freshness,
        conflict_score=conflict_score,
        failure_risk=failure_risk,
        congestion=congestion,
        opposing_ratio=opposing_ratio,
        persistence=persistence,
        distances=distances,
        candle_stats=candle_stats,
        behavior=behavior,
        context=context,
    )
    target_horizon_candles = int(
        max(
            1,
            eta_target if state in {"TRIGGERED", "ACTIVE"} else eta_trigger + eta_target,
        )
    )
    target_horizon_source = "kernel_eta"
    target_median = _safe_float(medians.get("candles_to_target", 0.0), 0.0)
    memory_duration_count = int(memory.get("duration_sample_count", memory_count) or 0)
    if target_median > 0.0 and memory_duration_count >= 3:
        target_horizon_candles = int(max(target_horizon_candles, round(target_median)))
        target_horizon_source = "study_memory"
    normal_target_min_candles = max(10, int(_safe_float(context.get("min_primary_target_candles", 10), 10.0)))
    long_target_max_candles = max(normal_target_min_candles, int(_safe_float(context.get("max_primary_target_candles", 36), 36.0)))
    target_horizon_candles = int(max(normal_target_min_candles, min(long_target_max_candles, target_horizon_candles)))
    news_event_candidate = bool(target_horizon_candles <= 4 and _clip01(candle_stats.get("normalized_volatility", 0.0), 0.0) >= 0.62)
    if str(next_candle.get("trade_mode", "")) == "TREND_FOLLOW":
        retimed_hold = int(max(normal_target_min_candles, target_horizon_candles))
        next_candle["hold_for_candles"] = retimed_hold
        next_candle["trend_follow_window_candles"] = retimed_hold
        next_candle["instruction"] = (
            f"{dominant_side} remains the trend-follow side; aim for the mapped target horizon "
            f"({retimed_hold} candle(s)) unless invalidation pressure rises."
        )
    next_candle["target_horizon_candles"] = int(target_horizon_candles)
    next_candle["target_horizon_source"] = target_horizon_source
    next_candle["news_event_candidate"] = news_event_candidate
    conversation = _market_conversation(
        dominant_side=dominant_side,
        state=state,
        next_candle=next_candle,
        visible_candles=visible_candles,
        distances=distances,
        freshness=freshness,
    )

    return {
        "pair": str(snapshot.get("pair", context.get("pair", context.get("market", ""))) or ""),
        "timeframe": str(snapshot.get("timeframe", context.get("timeframe", "")) or "").upper(),
        "dominant_side": _public_side(dominant_side),
        "major_trend_side": next_candle["major_trend_side"],
        "major_trend_confidence": next_candle["major_trend_confidence"],
        "bias_strength": bias_strength,
        "state": state,
        "setup_age_candles": int(round(setup_age_candles)),
        "freshness": freshness,
        "structure_alignment": alignment,
        "buy_evidence": float(evidence["buy_evidence"]),
        "sell_evidence": float(evidence["sell_evidence"]),
        "net_bias": float(evidence["net_bias"]),
        "conflict_score": conflict_score,
        "belief_buy": belief.buy,
        "belief_sell": belief.sell,
        "belief_hold": belief.hold,
        "belief_uncertainty": belief.uncertainty,
        "belief_conflict": belief.conflict,
        "directional_edge": belief.directional_edge,
        "evidence_mass": belief.evidence_mass,
        "usable_bias": belief.usable_bias,
        "evidence_family_totals": dict(belief.family_totals),
        "distance_to_trigger": distances["trigger"],
        "distance_to_target": distances["target"],
        "distance_to_invalidation": distances["invalidation"],
        "eta_trigger_candles": int(max(1, eta_trigger)),
        "eta_target_after_trigger_candles": int(max(1, eta_target)),
        "target_horizon_candles": int(target_horizon_candles),
        "target_horizon_source": target_horizon_source,
        "min_primary_target_candles": int(normal_target_min_candles),
        "max_primary_target_candles": int(long_target_max_candles),
        "news_event_candidate": news_event_candidate,
        "eta_invalidation_candles": int(max(1, eta_invalidation)),
        "stale_after_candles": int(max(0, stale_after_candles)),
        "p_trigger_next_1": p_trigger_next_1,
        "p_trigger_next_3": p_trigger_next_3,
        "p_target_before_invalidation": p_target_before_invalidation,
        "p_expire_before_trigger": p_expire_before_trigger,
        "hazard_trigger": hazard_trigger,
        "hazard_invalidation": hazard_invalidation,
        "hazard_expiry": hazard_expiry,
        "next_event_likelihoods": next_event_scores,
        "competing_event_probabilities": competing_event_probabilities,
        "target_race_probabilities": target_race,
        "next_most_likely_event": next_event,
        "raw_expected_value_R": utility.raw_ev_R,
        "expected_value_R": utility.adjusted_ev_R,
        "uncertainty_tax_R": utility.uncertainty_tax_R,
        "reward_R": utility.reward_R,
        "loss_R": utility.loss_R,
        "cost_R": utility.cost_R,
        "confidence_tier": confidence_tier,
        "reason_codes": list(reason_codes),
        "firewall_action": firewall.action,
        "firewall_reasons": list(firewall.reasons),
        "decision": _decision_for_state(state),
        "next_candle_bias": next_candle["next_candle_bias"],
        "p_next_buy": next_candle["p_next_buy"],
        "p_next_sell": next_candle["p_next_sell"],
        "p_next_hold": next_candle["p_next_hold"],
        "trade_mode": next_candle["trade_mode"],
        "candle_execution_side": next_candle["execution_side"],
        "countertrend_side": next_candle["countertrend_side"],
        "countertrend_window_candles": next_candle["countertrend_window_candles"],
        "trend_follow_window_candles": next_candle["trend_follow_window_candles"],
        "hold_for_candles": next_candle["hold_for_candles"],
        "micro_pullback_against_major": next_candle["micro_pullback_against_major"],
        "countertrend_scalp_enabled": next_candle["countertrend_scalp_enabled"],
        "candle_instruction": next_candle["instruction"],
        "market_conversation": conversation,
        "candle_clocks": dict(cast(Mapping[str, Any], next_candle["candle_clocks"])),
        "next_candle": next_candle,
        "memory": memory,
        "evidence_stream": [dict(row) for row in signals[:12]],
    }
