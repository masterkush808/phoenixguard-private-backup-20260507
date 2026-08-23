"""HLZ, timeframe, role-flip, pair-DNA, and temporal rule context for V3."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from phoenixguard.decision.book_strategy_full_stack_v3 import (
    evaluate_full_non_indicator_book_stack_v3,
)
from phoenixguard.decision.candlestick_rule_catalog_v3 import (
    CANDLESTICK_SOURCE_FILE_V3,
)


BOOK_STRATEGY_CONTEXT_SCHEMA_V3 = "PG_BOOK_STRATEGY_CONTEXT_V3"
STRATEGIST_ENTRY_WINDOW_CANDLES_V3 = 3
CHOP_PLAYBOOK_V3 = "CHOP"
STRATEGIST_STRICT_GATES = True
STATUS_STRATEGIST_ACTION_CONFIRMED_V3 = "STRATEGIST_ACTION_CONFIRMED"
STATUS_STRATEGIC_CONFLICT_V3 = "STRATEGIC_CONFLICT"
STATUS_WAITING_FOR_TRIGGER_V3 = "WAITING_FOR_CURRENT_BOOK_TRIGGER"
STATUS_MARKET_CHOP_V3 = "MARKET_CHOP"

_HLZ_FILE = "HLZ - Market Structure And Powerful Setups.pdf"
_TRENDLINE_FILE = "secrets revealed $10 000 cost price-1-1.pdf"
_TIMEFRAME_SECONDS = {
    "M1": 60,
    "M2": 120,
    "M3": 180,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H2": 7200,
    "H4": 14400,
    "H6": 21600,
    "H8": 28800,
    "H12": 43200,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _side(*values: Any) -> str:
    for value in values:
        text = "".join(
            character if character.isalnum() else "_"
            for character in str(value or "").strip().upper()
        )
        tokens = {token for token in text.split("_") if token}
        buy = bool(tokens & {
            "BUY", "BULL", "BULLISH", "UP", "UPTREND", "UPSIDE", "LONG",
            "DEMAND", "SUPPORT", "ASCENDING",
        })
        sell = bool(tokens & {
            "SELL", "BEAR", "BEARISH", "DOWN", "DOWNTREND", "DOWNSIDE", "SHORT",
            "SUPPLY", "RESIST", "RESISTANCE", "DESCENDING",
        })
        if buy and not sell:
            return "BUY"
        if sell and not buy:
            return "SELL"
    return "NEUTRAL"


def _truthy(row: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if value is True or (isinstance(value, (int, float)) and value != 0):
            return True
        if str(value or "").strip().upper() in {"TRUE", "YES", "CONFIRMED", "COMPLETE", "ACTIVE", "HELD"}:
            return True
    return False


def _trace(
    rule_id: str,
    side: str,
    weight: float,
    reason: str,
    *,
    pdf_pages: Sequence[int],
    source_file: str = _HLZ_FILE,
    source_section: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "side": side,
        "weight": round(float(weight), 6),
        "observed": True,
        "reason": reason,
        "source_book": source_file.removesuffix(".pdf"),
        "source_file": source_file,
        "source_section": source_section,
        "printed_pages": list(pdf_pages),
        "pdf_pages": list(pdf_pages),
    }


def _latest_bms(candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {"side": "NEUTRAL", "index": -1, "confirmed": False}
    for index in range(4, len(candles)):
        history = candles[max(0, index - 8) : index]
        close = _number(candles[index].get("close"))
        prior_high = max(_number(row.get("high")) for row in history)
        prior_low = min(_number(row.get("low")) for row in history)
        if close > prior_high:
            latest = {"side": "BUY", "index": index, "confirmed": True, "broken_level": prior_high}
        elif close < prior_low:
            latest = {"side": "SELL", "index": index, "confirmed": True, "broken_level": prior_low}
    return latest


def _failure_swing(candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(candles) < 9:
        return {"confirmed": False, "side": "NEUTRAL"}
    rows = candles[-12:]
    midpoint = max(4, len(rows) // 2)
    first = rows[:midpoint]
    second = rows[midpoint:]
    first_high = max(_number(row.get("high")) for row in first)
    second_high = max(_number(row.get("high")) for row in second[:-1] or second)
    first_low = min(_number(row.get("low")) for row in first)
    second_low = min(_number(row.get("low")) for row in second[:-1] or second)
    close = _number(rows[-1].get("close"))
    event_index = len(candles) - 1
    if second_high <= first_high and close < second_low:
        return {"confirmed": True, "side": "SELL", "index": event_index, "failed_extreme": first_high, "broken_swing": second_low}
    if second_low >= first_low and close > second_high:
        return {"confirmed": True, "side": "BUY", "index": event_index, "failed_extreme": first_low, "broken_swing": second_high}
    return {"confirmed": False, "side": "NEUTRAL"}


def _fibonacci_ote(
    candles: Sequence[Mapping[str, Any]],
    bms: Mapping[str, Any],
) -> dict[str, Any]:
    side = str(bms.get("side") or "NEUTRAL")
    break_index = int(bms.get("index") or -1)
    if side not in {"BUY", "SELL"} or break_index < 0 or not candles:
        return {"available": False, "side": "NEUTRAL", "in_ote": False, "retracement_ratio": None}
    history = candles[max(0, break_index - 12) : break_index + 1]
    current = _number(candles[-1].get("close"))
    if side == "BUY":
        swing_start = min(_number(row.get("low")) for row in history)
        swing_end = max(_number(row.get("high")) for row in history)
        distance = swing_end - swing_start
        ratio = (swing_end - current) / distance if distance > 1e-9 else 0.0
        levels = {str(level): swing_end - distance * level for level in (0.5, 0.62, 0.705, 0.79)}
    else:
        swing_start = max(_number(row.get("high")) for row in history)
        swing_end = min(_number(row.get("low")) for row in history)
        distance = swing_start - swing_end
        ratio = (current - swing_end) / distance if distance > 1e-9 else 0.0
        levels = {str(level): swing_end + distance * level for level in (0.5, 0.62, 0.705, 0.79)}
    return {
        "available": distance > 1e-9,
        "side": side,
        "swing_start": swing_start,
        "swing_end": swing_end,
        "retracement_ratio": round(ratio, 6),
        "at_or_beyond_50": 0.5 <= ratio <= 0.79,
        "in_ote": 0.62 <= ratio <= 0.79,
        "levels": {key: round(value, 8) for key, value in levels.items()},
        "evaluation_index": len(candles) - 1,
        "after_bms": len(candles) - 1 > break_index,
        "source_file": _HLZ_FILE,
        "pdf_pages": [7, 12],
    }


def _higher_timeframe_authority(
    timeframe: str,
    trend_directions: Mapping[str, Any] | None,
    higher_timeframe_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current = str(timeframe or "M5").strip().upper()
    context = _mapping(higher_timeframe_context)
    directions = _mapping(trend_directions)
    side = _side(
        context.get("side"), context.get("direction"),
        context.get("major_trend_side"), context.get("major_trend_direction"),
        directions.get("higher_timeframe"), directions.get("major"),
        directions.get("global"),
    )
    authority_timeframe = str(
        context.get("timeframe")
        or context.get("authority_timeframe")
        or context.get("higher_timeframe")
        or current
    ).strip().upper()
    current_seconds = _TIMEFRAME_SECONDS.get(current, 0)
    authority_seconds = _TIMEFRAME_SECONDS.get(authority_timeframe, current_seconds)
    strict = side in {"BUY", "SELL"} and authority_seconds >= current_seconds
    return {
        "current_timeframe": current,
        "authority_timeframe": authority_timeframe,
        "side": side,
        "strictly_enforced": strict,
        "refinement_mode": "HTF_ANALYSIS_LTF_ENTRY" if authority_seconds > current_seconds else "CURRENT_TIMEFRAME_AUTHORITY",
        "source_file": _HLZ_FILE,
        "pdf_pages": [105, 105],
    }


def _role_flip_sequence(zones: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for zone in _rows(zones):
        aggregate = _truthy(zone, "role_flip_confirmed")
        break_confirmed = aggregate or _truthy(zone, "break_confirmed", "body_close_break", "structure_break_confirmed")
        reclaim_confirmed = aggregate or _truthy(zone, "reclaim_confirmed", "close_reclaimed", "acceptance_confirmed")
        retest_confirmed = aggregate or _truthy(zone, "retest_hold", "retest_confirmed", "role_flip_retest_confirmed")
        if not (break_confirmed or reclaim_confirmed or retest_confirmed):
            continue
        original = _side(
            zone.get("original_role"), zone.get("zone_type"),
            zone.get("type"), zone.get("role"),
        )
        side = _side(zone.get("role_flip_side"), zone.get("new_side"))
        if side == "NEUTRAL" and original in {"BUY", "SELL"}:
            side = "SELL" if original == "BUY" else "BUY"
        complete = break_confirmed and reclaim_confirmed and retest_confirmed and side in {"BUY", "SELL"}
        return {
            "detected": True,
            "complete": complete,
            "side": side,
            "break_confirmed": break_confirmed,
            "reclaim_confirmed": reclaim_confirmed,
            "retest_hold_confirmed": retest_confirmed,
            "zone_id": str(zone.get("zone_id") or zone.get("id") or ""),
        }
    return {"detected": False, "complete": False, "side": "NEUTRAL"}


def _strict_role_flip_sequence_v3(
    full_stack: Mapping[str, Any],
    fallback_zones: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    trendlines = _mapping(full_stack.get("trendline_contracts"))
    zones = _mapping(full_stack.get("support_resistance"))
    candidates: list[dict[str, Any]] = []
    for row in _rows(trendlines.get("role_flips")):
        candidates.append({
            "source": "TRENDLINE",
            "source_id": row.get("trendline_id"),
            "side": row.get("current_action_side") or row.get("break_side"),
            "break_index": row.get("break_index"),
            "retest_index": row.get("retest_index"),
            "current_retest": bool(row.get("current_role_flip_retest")),
        })
    for row in _rows(zones.get("role_flips")):
        candidates.append({
            "source": "SUPPORT_RESISTANCE_ZONE",
            "source_id": row.get("zone_id"),
            "side": row.get("current_action_side") or row.get("flipped_role_side"),
            "break_index": row.get("break_index"),
            "retest_index": row.get("retest_index"),
            "current_retest": bool(row.get("current_role_flip_retest")),
        })
    valid = [
        row for row in candidates
        if _side(row.get("side")) in {"BUY", "SELL"}
        and int(_number(row.get("break_index"), -1)) >= 0
        and int(_number(row.get("retest_index"), -1)) > int(_number(row.get("break_index"), -1))
    ]
    if not valid:
        legacy = _role_flip_sequence(fallback_zones)
        return {
            **legacy,
            "complete": False,
            "causal_order_confirmed": False,
            "status": "WAITING_FOR_DERIVED_BREAK_BEFORE_RETEST_INDICES",
        }
    selected = max(valid, key=lambda row: int(_number(row.get("retest_index"), -1)))
    return {
        "detected": True,
        "complete": True,
        "side": _side(selected.get("side")),
        "break_confirmed": True,
        "reclaim_confirmed": True,
        "retest_hold_confirmed": True,
        "break_index": int(_number(selected.get("break_index"), -1)),
        "retest_index": int(_number(selected.get("retest_index"), -1)),
        "current_retest": bool(selected.get("current_retest")),
        "source": selected.get("source"),
        "source_id": selected.get("source_id"),
        "causal_order_confirmed": True,
    }


def _pair_dna_context(
    pair_dna_context: Mapping[str, Any] | None,
    behavior_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    profile = _mapping(pair_dna_context)
    behavior = _mapping(behavior_payload)
    candle = _mapping(profile.get("candle"))
    direction_counts = _mapping(candle.get("direction_counts") or profile.get("direction_counts"))
    buy_count = int(_number(direction_counts.get("BUY") or direction_counts.get("BULLISH")))
    sell_count = int(_number(direction_counts.get("SELL") or direction_counts.get("BEARISH")))
    support = buy_count + sell_count
    side = "NEUTRAL"
    probability = 0.5
    if support >= 12:
        probability = max(buy_count, sell_count) / max(1, support)
        if probability >= 0.56:
            side = "BUY" if buy_count > sell_count else "SELL"
    transition_counts = _mapping(
        _mapping(profile.get("behavior")).get("transition_counts")
        or profile.get("transition_counts")
    )
    regime_counts = _mapping(profile.get("regime_counts"))
    current_regime = str(
        behavior.get("current_state")
        or behavior.get("regime")
        or profile.get("current_regime")
        or max(regime_counts, key=lambda key: _number(regime_counts.get(key)), default="UNKNOWN")
    ).upper()
    personality_counts = _mapping(candle.get("personality_counts"))
    dominant_personality = max(
        personality_counts,
        key=lambda key: _number(personality_counts.get(key)),
        default="UNKNOWN",
    )
    amplitude = 1.0
    text = f"{current_regime} {dominant_personality}".upper()
    if any(token in text for token in ("EXPANSION", "IMPULSE", "VOLATILE", "LONG_BODY")):
        amplitude = 1.18
    elif any(token in text for token in ("COMPRESSION", "RANGE", "DOJI", "SMALL_BODY")):
        amplitude = 0.78
    return {
        "profile_applied": support >= 12 or bool(transition_counts),
        "independent_pair_only": True,
        "side": side,
        "probability": round(probability, 6),
        "directional_support": support,
        "current_regime": current_regime,
        "dominant_personality": str(dominant_personality).upper(),
        "transition_state_count": len(transition_counts),
        "path_amplitude": amplitude,
        "source_schema": str(profile.get("schema_version") or profile.get("schema") or "PAIR_DNA_V3"),
    }


def _temporal_context(
    session_context: Mapping[str, Any] | None,
    news_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    session = _mapping(session_context)
    news = _mapping(news_context)
    text = " ".join(
        [
            *(f"{key}={value}" for key, value in session.items()),
            *(f"{key}={value}" for key, value in news.items()),
        ]
    ).upper()
    explicit_session = str(
        session.get("session")
        or session.get("active_session")
        or session.get("session_name")
        or ""
    ).upper()
    active_session = (
        "LONDON_NEW_YORK_OVERLAP"
        if "LONDON" in text and "NEW_YORK" in text
        else "LONDON"
        if "LONDON" in explicit_session or "LONDON_SESSION" in text
        else "NEW_YORK"
        if "NEW_YORK" in explicit_session or "NEW YORK" in text
        else "ASIAN"
        if "ASIAN" in explicit_session or "ASIAN_SESSION" in text
        else "UNOBSERVED"
    )
    amplitude = {
        "ASIAN": 0.78,
        "LONDON": 1.12,
        "NEW_YORK": 1.08,
        "LONDON_NEW_YORK_OVERLAP": 1.22,
    }.get(active_session, 1.0)
    impact = str(news.get("impact") or news.get("event_impact") or "").upper()
    negative_news = any(token in text for token in ("NO_HIGH_IMPACT", "NO HIGH IMPACT", "NEWS_CLEAR"))
    high_impact = not negative_news and (
        impact in {"HIGH", "RED", "HIGH_IMPACT"}
        or _truthy(news, "high_impact", "event_active", "news_active")
        or "HIGH IMPACT NEWS" in text
    )
    pivot_confirmed = _truthy(
        news,
        "news_pivot_confirmed",
        "post_news_pivot_confirmed",
        "post_news_reclaim_confirmed",
    ) or "NEWS_PIVOT_CONFIRMED" in text
    event_phase = str(news.get("event_phase") or news.get("phase") or "").upper()
    entry_suspended = bool(high_impact and not pivot_confirmed)
    if high_impact:
        amplitude *= 1.32
    return {
        "active_session": active_session,
        "session_observed": active_session != "UNOBSERVED",
        "high_impact_news_observed": high_impact,
        "news_event_phase": event_phase or "UNOBSERVED",
        "news_pivot_confirmed": pivot_confirmed,
        "entry_suspended_until_news_pivot": entry_suspended,
        "path_amplitude": round(amplitude, 6),
        "direction_inferred_from_news": False,
        "session_provenance": {"source_file": _HLZ_FILE, "pdf_pages": [61, 72]},
        "news_provenance": {"source_file": _HLZ_FILE, "pdf_pages": [46, 49]},
    }


def evaluate_book_strategy_context_v3(
    *,
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    trend_directions: Mapping[str, Any] | None = None,
    higher_timeframe_context: Mapping[str, Any] | None = None,
    support_resistance_zones: Sequence[Mapping[str, Any]] | None = None,
    smart_money_context: Mapping[str, Any] | None = None,
    pair_dna_context: Mapping[str, Any] | None = None,
    behavior_payload: Mapping[str, Any] | None = None,
    session_context: Mapping[str, Any] | None = None,
    news_context: Mapping[str, Any] | None = None,
    trendlines: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = _rows(candles)
    full_stack = evaluate_full_non_indicator_book_stack_v3(
        candles=rows,
        timeframe=timeframe,
        trendlines=trendlines,
        support_resistance_zones=support_resistance_zones,
        session_context=session_context,
        news_context=news_context,
        pair_dna_context=pair_dna_context,
    )
    scores = {"BUY": 0.0, "SELL": 0.0}
    traces: list[dict[str, Any]] = []
    for direction in scores:
        scores[direction] += _number(
            _mapping(full_stack.get("score_adjustments")).get(direction)
        )
    traces.extend(_rows(full_stack.get("rule_trace")))
    htf = _higher_timeframe_authority(timeframe, trend_directions, higher_timeframe_context)
    if htf["strictly_enforced"]:
        side = str(htf["side"])
        scores[side] += 2.6
        traces.append(_trace("HTF_DIRECTIONAL_AUTHORITY", side, 2.6, "Higher-timeframe structure owns terminal direction; the lower timeframe only refines the reaction and entry.", pdf_pages=[105, 105], source_section="HTF analysis and LTF entries"))

    full_structure = _mapping(full_stack.get("market_structure"))
    derived_bms = _mapping(full_structure.get("latest_bms"))
    bms = (
        {**derived_bms, "confirmed": bool(derived_bms.get("completed_close_confirmed"))}
        if derived_bms
        else _latest_bms(rows)
    )
    if bms["confirmed"]:
        side = str(bms["side"])
        scores[side] += 1.8
        traces.append(_trace("HLZ_BMS_COMPLETED_CLOSE", side, 1.8, "A completed candle closed beyond the prior swing boundary.", pdf_pages=[9, 12], source_section="Break in market structure"))
    derived_sms = _mapping(full_structure.get("latest_sms"))
    sms = (
        {**derived_sms, "confirmed": True}
        if derived_sms
        else _failure_swing(rows)
    )
    if sms["confirmed"]:
        side = str(sms["side"])
        scores[side] += 2.1
        traces.append(_trace("HLZ_SMS_FAILURE_SWING", side, 2.1, "Price failed to extend the prior extreme and then broke the opposing swing.", pdf_pages=[14, 15], source_section="Failure swing SMS"))
    fibonacci = _fibonacci_ote(rows, bms)
    if fibonacci.get("in_ote"):
        side = str(fibonacci["side"])
        scores[side] += 2.25
        traces.append(_trace("HLZ_BMS_OTE_RETRACEMENT", side, 2.25, "After BMS, price retraced into the 0.62-0.79 optimal trade entry interval.", pdf_pages=[7, 12], source_section="Fibonacci retracement and OTE"))
    elif fibonacci.get("at_or_beyond_50"):
        side = str(fibonacci["side"])
        scores[side] += 1.15
        traces.append(_trace("HLZ_BMS_FIFTY_PERCENT_RETRACEMENT", side, 1.15, "After BMS, price reached the book's minimum 50 percent retracement area.", pdf_pages=[7, 12], source_section="Fibonacci retracement"))

    role_flip = _strict_role_flip_sequence_v3(full_stack, support_resistance_zones or [])
    if role_flip["complete"]:
        side = str(role_flip["side"])
        scores[side] += 2.35
        traces.append(_trace("ROLE_FLIP_BREAK_RECLAIM_RETEST", side, 2.35, "The former level completed break, reclaim, and retest-hold ordering before changing role.", pdf_pages=[88, 95], source_section="BMS and return to order block"))

    smc_text = " ".join(f"{key}={value}" for key, value in _mapping(smart_money_context).items()).upper()
    order_block_side = _side(
        _mapping(smart_money_context).get("order_block_side"),
        _mapping(smart_money_context).get("dominant_side"),
    )
    full_order_blocks = _mapping(full_stack.get("order_blocks"))
    active_order_block = _mapping(full_order_blocks.get("active_block"))
    full_liquidity = _mapping(full_stack.get("liquidity_turtle_soup"))
    latest_sweep = _mapping(full_liquidity.get("latest_sweep"))
    return_to_order_block = bool(active_order_block.get("return_to_order_block")) or any(token in smc_text for token in ("RETURN_TO_ORDER_BLOCK", "RTO", "ORDER_BLOCK_RETEST"))
    stop_hunt = bool(latest_sweep.get("reclaim_close_confirmed")) or any(token in smc_text for token in ("STOP_HUNT", "LIQUIDITY_SWEEP", "TURTLE_SOUP"))
    ordered_side = str(bms.get("side") or "NEUTRAL")
    if return_to_order_block and order_block_side in {"BUY", "SELL"} and order_block_side == ordered_side:
        scores[order_block_side] += 1.65
        traces.append(_trace("HLZ_BMS_RETURN_TO_ORDER_BLOCK", order_block_side, 1.65, "BMS was followed by a return to the originating order block.", pdf_pages=[51, 55, 88, 95], source_section="Order blocks and RTO"))
    if stop_hunt and ordered_side in {"BUY", "SELL"}:
        scores[ordered_side] += 1.25
        traces.append(_trace("HLZ_STOP_HUNT_BMS_SEQUENCE", ordered_side, 1.25, "A visible stop hunt/liquidity sweep precedes the structure break.", pdf_pages=[46, 49, 80, 89], source_section="Liquidity manipulation and Turtle Soup"))

    pair_dna = _pair_dna_context(pair_dna_context, behavior_payload)
    if pair_dna["profile_applied"] and pair_dna["side"] in {"BUY", "SELL"}:
        side = str(pair_dna["side"])
        weight = min(1.0, max(0.0, (float(pair_dna["probability"]) - 0.5) * 4.0))
        scores[side] += weight
        traces.append(_trace("PAIR_DNA_EMPIRICAL_DIRECTION", side, weight, "Only this pair and timeframe's bounded historical direction counts contribute empirical confluence.", pdf_pages=[240, 264], source_file=CANDLESTICK_SOURCE_FILE_V3, source_section="Multiple-technique filtering and pair behavior"))

    temporal = _temporal_context(session_context, news_context)
    if temporal["session_observed"]:
        traces.append(_trace("HLZ_SESSION_PHASE", "NEUTRAL", 0.0, f"Observed session phase: {temporal['active_session']}; it changes expected amplitude, not direction.", pdf_pages=[61, 72], source_section="Asian, London, New York, and AMD sessions"))
    if temporal["high_impact_news_observed"]:
        traces.append(_trace("NEWS_PIVOT_SEQUENCE", "NEUTRAL", 0.0, "High-impact news may hunt liquidity; direction remains unassigned until a post-event pivot/reclaim closes.", pdf_pages=[46, 49], source_section="High-impact news and liquidity"))
    full_news = _mapping(full_stack.get("news_pivot"))
    if full_news.get("active"):
        temporal["entry_suspended_until_news_pivot"] = not bool(full_news.get("confirmed"))
        temporal["news_pivot_confirmed"] = bool(full_news.get("confirmed"))
        temporal["news_pivot_state"] = str(full_news.get("state") or "UNOBSERVED")

    bms_side = str(bms.get("side") or "NEUTRAL")
    effective_htf_side = str(htf["side"])
    ltf_counter_trend_evidence = bool(
        sms.get("confirmed")
        and bms.get("confirmed")
        and str(sms.get("side")) == bms_side
        and bms_side in {"BUY", "SELL"}
        and htf.get("strictly_enforced")
        and bms_side != effective_htf_side
    )
    if ltf_counter_trend_evidence:
        traces.append(_trace(
            "LTF_REVERSAL_EVIDENCE_NO_AUTHORITY",
            "NEUTRAL",
            0.0,
            (
                "Lower-timeframe SMS and BMS stand against the higher-timeframe side; per HLZ p.105 "
                "the HTF owns direction and p.152 a market hardly reverses without taking HTF liquidity, "
                "so this evidence is reported and never grants counter-HTF permission."
            ),
            pdf_pages=[105, 152],
            source_section="HTF analysis and LTF entries; liquidity before reversal",
        ))
    bms_index = int(_number(bms.get("index"), -1))
    sweep_index = int(_number(latest_sweep.get("index"), -1))
    sms_index = int(_number(sms.get("index"), -1))
    rto_index = int(_number(active_order_block.get("latest_retest_index"), -1))
    role_retest_index = int(_number(role_flip.get("retest_index"), -1))
    retracement_index = int(_number(fibonacci.get("evaluation_index"), -1))
    precursor_index = max(sweep_index, sms_index)
    precursor_ordered = precursor_index < 0 or precursor_index < bms_index
    terminal_indices = [
        index for index, active in (
            (retracement_index, bool(fibonacci.get("in_ote"))),
            (rto_index, return_to_order_block),
            (role_retest_index, bool(role_flip.get("complete"))),
        ) if active and index > bms_index
    ]
    terminal_index = max(terminal_indices, default=-1)
    htf_sequence_aligned = bool(
        not htf.get("strictly_enforced")
        or effective_htf_side == bms_side
    )
    sequence_ready = bool(
        bms.get("confirmed")
        and bms_side in {"BUY", "SELL"}
        and precursor_ordered
        and terminal_index > bms_index
        and htf_sequence_aligned
    )
    events = [
        {"ordinal": 1, "event": "LIQUIDITY_OR_FAILURE_SWING", "satisfied": precursor_index >= 0, "index": precursor_index, "order_valid": precursor_ordered},
        {"ordinal": 2, "event": "BMS_COMPLETED_CLOSE", "satisfied": bool(bms.get("confirmed")), "index": bms_index, "order_valid": bms_index >= 0 and precursor_ordered},
        {"ordinal": 3, "event": "RETRACEMENT", "satisfied": bool(fibonacci.get("at_or_beyond_50")), "index": retracement_index, "order_valid": retracement_index > bms_index},
        {"ordinal": 4, "event": "OTE_OR_ORDER_BLOCK", "satisfied": bool(fibonacci.get("in_ote") or return_to_order_block), "index": max(retracement_index if fibonacci.get("in_ote") else -1, rto_index), "order_valid": max(retracement_index if fibonacci.get("in_ote") else -1, rto_index) > bms_index},
        {"ordinal": 5, "event": "RTO_OR_RETEST_HOLD", "satisfied": bool(return_to_order_block or role_flip.get("complete")), "index": max(rto_index, role_retest_index), "order_valid": max(rto_index, role_retest_index) > bms_index},
    ]
    full_trendline = _mapping(full_stack.get("trendline_contracts"))
    full_zones = _mapping(full_stack.get("support_resistance"))
    opposing_reactions = _mapping(full_stack.get("opposing_force_reactions"))
    current_line_reactions = _rows(full_trendline.get("current_reactions"))
    current_zone_reactions = _rows(full_zones.get("current_reactions"))
    aggressive_side = _side(
        current_line_reactions[-1].get("current_action_side") if current_line_reactions else None,
        current_zone_reactions[-1].get("current_action_side") if current_zone_reactions else None,
        latest_sweep.get("side"),
    )
    aggressive_current = bool(
        current_line_reactions
        or current_zone_reactions
        or int(_number(latest_sweep.get("index"), -1)) == len(rows) - 1
    )
    aggressive_htf_ok = bool(
        aggressive_side in {"BUY", "SELL"}
        and (
            not htf.get("strictly_enforced")
            or aggressive_side == effective_htf_side
        )
    )
    conservative_side = _side(bms_side, role_flip.get("side")) if sequence_ready else _side(role_flip.get("side"))
    conservative_current = bool(
        sequence_ready and terminal_index == len(rows) - 1
        or role_flip.get("current_retest")
    )
    entry_profiles = {
        "aggressive": {
            "ready": aggressive_current and aggressive_htf_ok,
            "side": aggressive_side,
            "current_closed_candle_evidence": aggressive_current,
            "requires": ["STRICT_THREE_TOUCH_OR_ZONE_OR_SWEEP", "COMPLETED_REJECTION", "HTF_NOT_OPPOSING"],
        },
        "conservative": {
            "ready": conservative_current and htf_sequence_aligned,
            "side": conservative_side,
            "current_closed_candle_evidence": conservative_current,
            "requires": ["CAUSAL_EVENT_ORDER", "COMPLETED_RETEST_OR_RTO", "HTF_ALIGNMENT"],
        },
    }
    return {
        "schema": BOOK_STRATEGY_CONTEXT_SCHEMA_V3,
        "future_blind": True,
        "observed_candle_count": len(rows),
        "technical_indicators_used": False,
        "technical_indicator_scope": "EXCLUDED_BY_USER",
        "horizon_published": False,
        "execution_authority": False,
        "action_authority_scope": "CURRENT_CLOSED_CANDLE_ONLY",
        "score_adjustments": scores,
        "higher_timeframe": {
            **htf,
            "effective_side": effective_htf_side,
            "counter_trend_evidence_on_ltf": ltf_counter_trend_evidence,
        },
        "hlz_sequence": {
            "events": events,
            "ordered": sequence_ready,
            "entry_sequence_ready": sequence_ready,
            "terminal_event_index": terminal_index,
            "current_terminal_event": terminal_index == len(rows) - 1,
            "precursor_ordered_before_bms": precursor_ordered,
            "timeframe_refinement": {
                "mode": htf.get("refinement_mode"),
                "authority_timeframe": htf.get("authority_timeframe"),
                "entry_timeframe": htf.get("current_timeframe"),
                "aligned": htf_sequence_aligned,
            },
            "entry_profiles": entry_profiles,
            "bms": bms,
            "sms": sms,
            "return_to_order_block": return_to_order_block,
            "stop_hunt": stop_hunt,
        },
        "fibonacci_ote": fibonacci,
        "role_flip": role_flip,
        "pair_dna": pair_dna,
        "temporal": temporal,
        "path_amplitude": round(float(pair_dna["path_amplitude"]) * float(temporal["path_amplitude"]), 6),
        "full_non_indicator_stack_v3": full_stack,
        "market_structure_full_v3": _mapping(full_stack.get("market_structure")),
        "trendline_contracts_full_v3": _mapping(full_stack.get("trendline_contracts")),
        "support_resistance_full_v3": full_zones,
        "order_blocks_full_v3": _mapping(full_stack.get("order_blocks")),
        "liquidity_turtle_soup_v3": _mapping(full_stack.get("liquidity_turtle_soup")),
        "amd_v3": _mapping(full_stack.get("amd")),
        "news_pivot_v3": full_news,
        "sakata_v3": _mapping(full_stack.get("sakata")),
        "sunday_gap_fade_v3": _mapping(full_stack.get("sunday_gap_fade_v3")),
        "rule_calibration_v3": _mapping(full_stack.get("rule_calibration")),
        "candle_location_history": _mapping(full_stack.get("candle_location_history")),
        "opposing_targets": {
            **_mapping(full_stack.get("opposing_targets")),
            "_current_reactions_v3": opposing_reactions,
        },
        "rule_trace": traces,
    }


def _clip_text(value: object, default: str = "", *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit] or default


_PLAYBOOK_FAMILY_V3: dict[str, str] = {
    "BREAK_RETEST": "STRICT_WICK_TRENDLINE",
    "TRENDLINE_REJECTION": "STRICT_WICK_TRENDLINE",
    "ROLE_FLIP_RETEST": "ROLE_FLIP_RETEST",
    "SUPPORT_RESISTANCE_REJECTION": "SUPPLY_DEMAND_REACTION",
    "STOP_HUNT_BMS_RTO": "HLZ_EVENT_SEQUENCE",
    "BMS_OTE_RTO": "HLZ_EVENT_SEQUENCE",
    "ORDER_BLOCK_RTO": "ORDER_BLOCK_RTO",
    "TURTLE_SOUP_SH_BMS_RTO": "TURTLE_SOUP",
    "LIQUIDITY_SWEEP_RECLAIM": "TURTLE_SOUP",
    "STRUCTURE_CONTINUATION": "BMS_SMS_STRUCTURE",
    "AMD_DISTRIBUTION": "AMD_SEQUENCE",
    "POST_NEWS_PIVOT": "NEWS_PIVOT",
    "SAKATA_METHOD": "SAKATA_METHODS",
    "CANDLE_REVERSAL_AT_STRUCTURE": "CANDLESTICK_CATALOGUE",
    "CANDLE_CONTINUATION_AT_STRUCTURE": "CANDLESTICK_CATALOGUE",
    "SUNDAY_GAP_FADE": "SUNDAY_GAP_FADE",
}

_BOOK_STRATEGY_FAMILIES_V3: tuple[str, ...] = (
    "STRICT_WICK_TRENDLINE",
    "HLZ_EVENT_SEQUENCE",
    "ROLE_FLIP_RETEST",
    "FIBONACCI_OTE",
    "BMS_SMS_STRUCTURE",
    "ORDER_BLOCK_RTO",
    "TURTLE_SOUP",
    "AMD_SEQUENCE",
    "SAKATA_METHODS",
    "CANDLESTICK_CATALOGUE",
    "SUPPLY_DEMAND_REACTION",
    "HIGHER_TIMEFRAME_AUTHORITY",
    "NEWS_PIVOT",
    "PAIR_DNA",
    "SUNDAY_GAP_FADE",
)

_PLAYBOOK_PROVENANCE_V3: dict[str, dict[str, Any]] = {
    "BREAK_RETEST": {
        "source_file": _TRENDLINE_FILE,
        "pdf_pages": [26, 27, 33, 58],
        "source_section": "Support, resistance, break, retest, and reaction",
    },
    "TRENDLINE_REJECTION": {
        "source_file": _TRENDLINE_FILE,
        "pdf_pages": [13, 20, 23, 58],
        "source_section": "Valid trendlines, wick contact, and reaction",
    },
    "ROLE_FLIP_RETEST": {
        "source_file": _TRENDLINE_FILE,
        "pdf_pages": [26, 27, 33, 58],
        "source_section": "Role flip: break, reclaim, and retest hold",
    },
    "SUPPORT_RESISTANCE_REJECTION": {
        "source_file": _TRENDLINE_FILE,
        "pdf_pages": [26, 27, 33],
        "source_section": "Zone rejection behavior",
    },
    "STOP_HUNT_BMS_RTO": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [46, 49, 80, 87, 93, 103, 105],
        "source_section": "Stop hunt, BMS, retracement, RTO, and HTF refinement",
    },
    "BMS_OTE_RTO": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [7, 9, 11, 12, 103, 105],
        "source_section": "BMS, OTE retracement, and return-to-origin",
    },
    "ORDER_BLOCK_RTO": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [51, 55, 88, 95],
        "source_section": "Order blocks and return to order block",
    },
    "TURTLE_SOUP_SH_BMS_RTO": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [80, 89, 95],
        "source_section": "Turtle Soup, SH+BMS+RTO, SMS+BMS+RTO",
    },
    "LIQUIDITY_SWEEP_RECLAIM": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [80, 89],
        "source_section": "Liquidity sweep and reclaim behavior",
    },
    "STRUCTURE_CONTINUATION": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [9, 12, 14, 15],
        "source_section": "BMS, SMS, failure swing, and continuation",
    },
    "AMD_DISTRIBUTION": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [61, 72, 99],
        "source_section": "Accumulation, manipulation, and distribution",
    },
    "POST_NEWS_PIVOT": {
        "source_file": _HLZ_FILE,
        "pdf_pages": [46, 49],
        "source_section": "High-impact news liquidity pivot",
    },
    "SAKATA_METHOD": {
        "source_file": CANDLESTICK_SOURCE_FILE_V3,
        "pdf_pages": [277, 291],
        "source_section": "Sakata five methods",
    },
    "CANDLE_REVERSAL_AT_STRUCTURE": {
        "source_file": CANDLESTICK_SOURCE_FILE_V3,
        "pdf_pages": [67, 69],
        "source_section": "Reversal formations with location filtering",
    },
    "CANDLE_CONTINUATION_AT_STRUCTURE": {
        "source_file": CANDLESTICK_SOURCE_FILE_V3,
        "pdf_pages": [70, 204],
        "source_section": "Continuation formations with location filtering",
    },
    "SUNDAY_GAP_FADE": {
        "source_file": "zlib.pub_the-art-of-currency-trading-a-professionals-guide-to-the-foreign-exchange-market.pdf",
        "pdf_pages": [190, 192],
        "source_section": "Weekend gap fade: about 85 percent fill within 48 hours",
    },
}


def _profit_room_assessment(
    source: Mapping[str, Any],
    side: str,
    geometry: Mapping[str, Any],
) -> dict[str, Any]:
    """Measure remaining room toward the opposing book target for a side."""
    full_stack = _mapping(source.get("full_non_indicator_stack_v3"))
    targets = _mapping(
        source.get("opposing_force_targets_v3")
        or source.get("opposing_targets")
        or full_stack.get("opposing_targets")
    )
    target = _mapping(targets.get(side))
    raw_close_y = geometry.get("latest_close_y_px")
    room: float | None = None
    raw_target_y = target.get("target_y_px")
    if raw_target_y is not None and raw_close_y is not None:
        room = abs(_number(raw_target_y) - _number(raw_close_y))
    else:
        raw_bounds = target.get("bounds")
        bound_values: list[float] = []
        if isinstance(raw_bounds, Sequence) and not isinstance(raw_bounds, (str, bytes, bytearray)):
            bound_values = [_number(item) for item in list(raw_bounds)[:4]]
        if len(bound_values) >= 4 and raw_close_y is not None:
            zone_low, zone_high = sorted((bound_values[1], bound_values[3]))
            close_value = _number(raw_close_y)
            if close_value < zone_low:
                room = zone_low - close_value
            elif close_value > zone_high:
                room = close_value - zone_high
            else:
                room = 0.0
        elif target.get("distance_px") is not None:
            room = abs(_number(target.get("distance_px")))
    if room is None:
        return {
            "measured": False,
            "sufficient": None,
            "room_px": None,
            "minimum_room_px": None,
            "target_source": "",
            "reason": (
                "No measurable opposing structure target; profit room is reported, never guessed."
            ),
        }
    median_range = _number(geometry.get("median_candle_range_y_px"), 0.0)
    minimum_room = max(1.5 * median_range, 8.0) if median_range > 0.0 else 12.0
    sufficient = bool(room >= minimum_room)
    label = str(target.get("source") or "opposing book structure").replace("_", " ").lower()
    if room <= 0.0:
        reason = (
            f"Price already trades inside the {label} target; the move to target is spent and "
            f"the book forbids a fresh entry without room."
        )
    elif sufficient:
        reason = f"{room:.1f}px of room remains to the {label} target against a {minimum_room:.1f}px minimum."
    else:
        reason = (
            f"Only {room:.1f}px of profit room remains to the {label} target; the book requires "
            f"at least {minimum_room:.1f}px before an entry may publish."
        )
    return {
        "measured": True,
        "sufficient": sufficient,
        "room_px": round(room, 6),
        "minimum_room_px": round(minimum_room, 6),
        "target_source": str(target.get("source") or ""),
        "reason": reason,
    }


def select_current_book_action_v3(
    control: Mapping[str, Any] | None,
    *,
    market_geometry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve every book family by name with confluence, reasons, and profit room."""

    source = _mapping(control)
    geometry = _mapping(market_geometry)
    full_stack = _mapping(source.get("full_non_indicator_stack_v3"))
    trendline = _mapping(
        source.get("trendline_contracts_full_v3")
        or source.get("trendline_contracts")
        or full_stack.get("trendline_contracts")
    )
    zones = _mapping(
        trendline.get("support_resistance_contracts")
        or source.get("support_resistance_full_v3")
        or source.get("support_resistance")
        or full_stack.get("support_resistance")
    )
    hlz = _mapping(source.get("hlz_sequence_v3") or source.get("hlz_sequence"))
    role_flip = _mapping(source.get("role_flip_sequence_v3") or source.get("role_flip"))
    catalog = _mapping(source.get("candlestick_catalog_v3") or source.get("candlestick_catalog"))
    htf = _mapping(source.get("higher_timeframe_authority_v3") or source.get("higher_timeframe"))
    temporal = _mapping(source.get("session_news_context_v3") or source.get("temporal"))
    structure_full = _mapping(source.get("market_structure_full_v3") or full_stack.get("market_structure"))
    order_blocks = _mapping(source.get("order_blocks_full_v3") or full_stack.get("order_blocks"))
    liquidity = _mapping(source.get("liquidity_turtle_soup_v3") or full_stack.get("liquidity_turtle_soup"))
    amd = _mapping(source.get("amd_v3") or full_stack.get("amd"))
    news_pivot = _mapping(source.get("news_pivot_v3") or full_stack.get("news_pivot"))
    sakata = _mapping(source.get("sakata_v3") or full_stack.get("sakata"))
    sunday_gap = _mapping(source.get("sunday_gap_fade_v3") or full_stack.get("sunday_gap_fade_v3"))
    fibonacci = _mapping(source.get("fibonacci_ote_v3") or source.get("fibonacci_ote"))
    pair_dna = _mapping(source.get("pair_dna_forecast_context_v3") or source.get("pair_dna"))
    observed_count = int(_number(source.get("observed_candle_count"), 0))
    latest_index = observed_count - 1
    major_side = _side(source.get("major_structure_side"))
    inner_side = _side(source.get("inner_structure_side"))

    candidates: list[dict[str, Any]] = []
    family_notes: dict[str, dict[str, Any]] = {}

    def _note(strategy_id: str, *, state: str, playbook: str = "", side: str = "NEUTRAL", reason: str = "") -> None:
        family_notes[strategy_id] = {
            "strategy_id": strategy_id,
            "resolution": state,
            "playbook": playbook,
            "side": _side(side),
            "reason": _clip_text(reason, "No completed-candle evidence on the latest closed candle."),
        }

    def _add_candidate(
        *,
        side: object,
        playbook: str,
        profile: str,
        priority: int,
        rule_ids: Sequence[str],
        evidence: Mapping[str, Any],
        trigger: str,
        invalidation: str,
        reason: str,
        event_index: object = None,
    ) -> None:
        normalized = _side(side)
        if normalized not in {"BUY", "SELL"}:
            return
        candidates.append({
            "side": normalized,
            "playbook": playbook,
            "family": _PLAYBOOK_FAMILY_V3.get(playbook, "UNFAMILIED"),
            "profile": profile,
            "priority": int(priority),
            "rule_ids": sorted({str(value) for value in rule_ids if str(value)}),
            "evidence": dict(evidence),
            "trigger": _clip_text(trigger, "Wait for the completed book trigger."),
            "invalidation": _clip_text(invalidation, "An opposing completed close invalidates the setup."),
            "reason": _clip_text(reason, "Completed book evidence."),
            "event_index": int(_number(event_index, latest_index)),
        })

    def _recent(index: object, window: int = STRATEGIST_ENTRY_WINDOW_CANDLES_V3) -> bool:
        value = int(_number(index, -1.0))
        return value >= 0 and latest_index - value <= window

    htf_effective_side = _side(htf.get("effective_side") or htf.get("side"))

    for strategy_id in _BOOK_STRATEGY_FAMILIES_V3:
        _note(strategy_id, state="WATCHING")

    line_reactions = _rows(trendline.get("current_reactions"))
    line_flips = _rows(trendline.get("current_role_flip_retests"))
    zone_reactions = _rows(zones.get("current_reactions"))
    zone_flips = _rows(zones.get("current_role_flip_retests"))

    for row in line_flips:
        _add_candidate(
            side=row.get("current_action_side"), playbook="BREAK_RETEST",
            profile="CONSERVATIVE", priority=100,
            rule_ids=["STRICT_TRENDLINE_ROLE_FLIP"], evidence=row,
            trigger="The completed retest held the broken trendline in its new role.",
            invalidation="A completed close back through the flipped trendline invalidates the hold.",
            reason="Current trendline role-flip retest held on the latest closed candle.",
            event_index=row.get("retest_index"),
        )
    if line_flips:
        _note("STRICT_WICK_TRENDLINE", state="CANDIDATE", playbook="BREAK_RETEST",
              side=line_flips[-1].get("current_action_side"),
              reason=f"{len(line_flips)} current role-flip retest(s) on strict wick lines.")
    elif line_reactions:
        _note("STRICT_WICK_TRENDLINE", state="CANDIDATE", playbook="TRENDLINE_REJECTION",
              side=line_reactions[-1].get("current_action_side"),
              reason=f"{len(line_reactions)} current wick-line reaction(s).")
    elif _number(trendline.get("valid_count")) > 0:
        _note("STRICT_WICK_TRENDLINE", state="WATCHING",
              reason=f"{int(_number(trendline.get('valid_count')))} valid line(s); no current touch.")
    else:
        _note("STRICT_WICK_TRENDLINE", state="ABSENT", reason="No strict three-touch wick line on the chart.")

    for row in zone_flips:
        _add_candidate(
            side=row.get("current_action_side"), playbook="ROLE_FLIP_RETEST",
            profile="CONSERVATIVE", priority=98,
            rule_ids=["SUPPORT_RESISTANCE_ROLE_FLIP"], evidence=row,
            trigger="The completed retest held the support/resistance zone in its new role.",
            invalidation="A completed close through the far side of the flipped zone invalidates the hold.",
            reason="Current support/resistance role-flip retest held.",
            event_index=row.get("retest_index"),
        )
    if zone_flips:
        _note("ROLE_FLIP_RETEST", state="CANDIDATE", playbook="ROLE_FLIP_RETEST",
              side=zone_flips[-1].get("current_action_side"),
              reason=f"{len(zone_flips)} current zone role-flip retest(s).")
    elif _rows(zones.get("active_contracts")):
        _note("ROLE_FLIP_RETEST", state="WATCHING",
              reason="Active zones present; no ordered break-reclaim-retest yet.")

    for row in line_reactions:
        _add_candidate(
            side=row.get("current_action_side") or row.get("role_side"), playbook="TRENDLINE_REJECTION",
            profile="AGGRESSIVE", priority=92,
            rule_ids=["STRICT_TRENDLINE_REJECTION"], evidence=row,
            trigger="The latest completed candle rejected a strict three-touch wick line.",
            invalidation="A completed body close through the defending line invalidates the reaction.",
            reason="Latest closed candle rejected a mature wick line.",
            event_index=row.get("index"),
        )

    for row in zone_reactions:
        _add_candidate(
            side=row.get("current_action_side") or row.get("role_side"), playbook="SUPPORT_RESISTANCE_REJECTION",
            profile="AGGRESSIVE", priority=88,
            rule_ids=["SUPPORT_RESISTANCE_REJECTION"], evidence=row,
            trigger="The latest completed candle rejected the exact support/resistance zone.",
            invalidation="A completed close beyond the far edge of the zone invalidates the reaction.",
            reason="Latest closed candle rejected the exact zone.",
            event_index=row.get("index"),
        )
    if zone_reactions:
        _note("SUPPLY_DEMAND_REACTION", state="CANDIDATE", playbook="SUPPORT_RESISTANCE_REJECTION",
              side=zone_reactions[-1].get("current_action_side") or zone_reactions[-1].get("role_side"),
              reason=f"{len(zone_reactions)} current zone rejection(s).")
    elif _rows(zones.get("active_contracts")):
        _note("SUPPLY_DEMAND_REACTION", state="WATCHING",
              reason="Active zones present; no current-candle rejection.")

    hlz_bms = _mapping(hlz.get("bms"))
    sequence_side = _side(hlz_bms.get("side"))
    fresh_hlz = bool(hlz.get("entry_sequence_ready")) and bool(hlz.get("current_terminal_event"))
    active_block = _mapping(order_blocks.get("active_block"))
    latest_sweep = _mapping(liquidity.get("latest_sweep"))
    if sequence_side in {"BUY", "SELL"} and _truthy(hlz_bms, "confirmed"):
        terminal_indices: list[int] = []
        if _truthy(fibonacci, "at_or_beyond_50") and _side(fibonacci.get("side")) == sequence_side:
            index_value = int(_number(fibonacci.get("evaluation_index"), -1))
            if index_value >= 0:
                terminal_indices.append(index_value)
        if _truthy(active_block, "return_to_order_block") and _side(active_block.get("side")) == sequence_side:
            index_value = int(_number(active_block.get("latest_retest_index"), -1))
            if index_value >= 0:
                terminal_indices.append(index_value)
        if _truthy(role_flip, "complete") and _side(role_flip.get("side")) == sequence_side:
            index_value = int(_number(role_flip.get("retest_index"), -1))
            if index_value >= 0:
                terminal_indices.append(index_value)
        terminal_index = max(terminal_indices, default=-1)
        bms_index = int(_number(hlz_bms.get("index"), -1))
        windowed_hlz_ready = (
            not fresh_hlz
            and terminal_index > bms_index
            and _recent(terminal_index)
        )
        if fresh_hlz or windowed_hlz_ready:
            playbook_name = "STOP_HUNT_BMS_RTO" if _truthy(hlz, "stop_hunt") else "BMS_OTE_RTO"
            window_note = (
                "on the current closed candle"
                if fresh_hlz
                else f"within the {STRATEGIST_ENTRY_WINDOW_CANDLES_V3}-candle entry window"
            )
            _add_candidate(
                side=sequence_side, playbook=playbook_name,
                profile="CONSERVATIVE", priority=96 if fresh_hlz else 90,
                rule_ids=["HLZ_CAUSAL_SEQUENCE_COMPLETE"],
                evidence={"hlz_summary": {key: hlz.get(key) for key in (
                    "entry_sequence_ready", "stop_hunt", "bms", "rto",
                    "terminal_event_index", "current_terminal_event",
                )}},
                trigger=(
                    "Enter while the completed HLZ stop-hunt/BMS/retracement-or-RTO sequence holds "
                    "and price has not invalidated the origin."
                    if windowed_hlz_ready
                    else "The current candle completed the ordered HLZ retracement or return sequence."
                ),
                invalidation="An opposing completed structure break invalidates the sequence.",
                reason=f"Ordered HLZ sequence completed {window_note}.",
                event_index=terminal_index,
            )
            _note("HLZ_EVENT_SEQUENCE", state="CANDIDATE", playbook=playbook_name,
                  side=sequence_side,
                  reason=f"Stop-hunt={bool(_truthy(hlz, 'stop_hunt'))}; BMS confirmed; sequence {window_note}.")
        else:
            _note("HLZ_EVENT_SEQUENCE", state="WATCHING", side=sequence_side,
                  reason=(
                      "Stop-hunt and BMS confirmed; waiting for the retracement or RTO leg to complete."
                      if _truthy(hlz, "stop_hunt")
                      else "BMS confirmed; waiting for the ordered retracement or RTO leg."
                  ))
    else:
        _note(
            "HLZ_EVENT_SEQUENCE",
            state="WATCHING" if hlz.get("events") else "ABSENT",
            reason="No confirmed BMS inside an ordered HLZ sequence.",
        )

    ob_side = _side(active_block.get("side"))
    if ob_side in {"BUY", "SELL"} and _truthy(active_block, "return_to_order_block"):
        retest_index = active_block.get("latest_retest_index")
        if _recent(retest_index):
            _add_candidate(
                side=ob_side, playbook="ORDER_BLOCK_RTO",
                profile="CONSERVATIVE", priority=87,
                rule_ids=["INDEPENDENT_BMS_ORDER_BLOCK_RTO"], evidence=active_block,
                trigger="Price returned into the last opposing candle that caused BMS and respected it.",
                invalidation="A completed close through the far edge of the order block invalidates it.",
                reason=f"Active {ob_side} order block retested within the entry window.",
                event_index=retest_index,
            )
            _note("ORDER_BLOCK_RTO", state="CANDIDATE", playbook="ORDER_BLOCK_RTO",
                  side=ob_side, reason="Active order block retested inside the entry window.")
        else:
            _note("ORDER_BLOCK_RTO", state="CONFLUENCE", side=ob_side,
                  reason="Active order block exists; latest retest aged out of the entry window.")
    elif ob_side in {"BUY", "SELL"}:
        _note("ORDER_BLOCK_RTO", state="WATCHING", side=ob_side,
              reason="Active order block derived; no return-to-origin yet.")
    else:
        _note("ORDER_BLOCK_RTO", state="ABSENT", reason="No qualifying order block.")

    liquidity_state = str(liquidity.get("state") or "").upper()
    sweep_side = _side(latest_sweep.get("side"))
    if _truthy(liquidity, "complete") and sweep_side in {"BUY", "SELL"}:
        _add_candidate(
            side=sweep_side, playbook="TURTLE_SOUP_SH_BMS_RTO",
            profile="CONSERVATIVE", priority=86,
            rule_ids=["TURTLE_SOUP_SH_BMS_RTO_COMPLETE"], evidence=liquidity,
            trigger="Equal-level liquidity swept, reclaimed, confirmed BMS, and returned in causal order.",
            invalidation="A completed close beyond the swept pool invalidates the Turtle Soup chain.",
            reason="Full sweep, reclaim, BMS, and RTO chain completed.",
            event_index=latest_sweep.get("index"),
        )
        _note("TURTLE_SOUP", state="CANDIDATE", playbook="TURTLE_SOUP_SH_BMS_RTO",
              side=sweep_side, reason="Complete SH+BMS+RTO Turtle Soup chain.")
    elif sweep_side in {"BUY", "SELL"} and liquidity_state in {
        "SWEEP_RECLAIMED", "SWEEP_RECLAIM_BMS_CONFIRMED",
    } and _recent(latest_sweep.get("index")):
        _add_candidate(
            side=sweep_side, playbook="LIQUIDITY_SWEEP_RECLAIM",
            profile="AGGRESSIVE", priority=85,
            rule_ids=["LIQUIDITY_SWEEP_RECLAIM_CONFIRMED"], evidence=latest_sweep,
            trigger="A completed candle swept equal-level liquidity and closed back beyond the level.",
            invalidation="A completed close back through the swept level invalidates the reclaim.",
            reason=f"Sweep-reclaim state {liquidity_state} within the entry window.",
            event_index=latest_sweep.get("index"),
        )
        _note("TURTLE_SOUP", state="CANDIDATE", playbook="LIQUIDITY_SWEEP_RECLAIM",
              side=sweep_side, reason=f"Liquidity state {liquidity_state}.")
    elif sweep_side in {"BUY", "SELL"}:
        _note("TURTLE_SOUP", state="WATCHING", side=sweep_side,
              reason=f"Liquidity state {liquidity_state or 'UNPROVEN'}; sweep outside the entry window.")
    else:
        _note("TURTLE_SOUP", state="ABSENT", reason="No equal-level liquidity pools swept.")

    structure_side = _side(structure_full.get("structure_side"))
    latest_structured_bms = _mapping(structure_full.get("latest_bms"))
    sms_events = _rows(structure_full.get("sms_events"))
    bms_events = _rows(structure_full.get("bms_events"))
    if (
        structure_side in {"BUY", "SELL"}
        and _truthy(latest_structured_bms, "completed_close_confirmed")
        and _side(latest_structured_bms.get("side")) == structure_side
        and _recent(latest_structured_bms.get("index"))
    ):
        protected = _mapping(structure_full.get("protected_swing"))
        _add_candidate(
            side=structure_side, playbook="STRUCTURE_CONTINUATION",
            profile="CONSERVATIVE", priority=82,
            rule_ids=["HLZ_BMS_COMPLETED_CLOSE"] + (
                ["HLZ_SMS_FAILURE_SWING"] if sms_events and sms_events[-1].get("side") == structure_side else []
            ),
            evidence={
                **latest_structured_bms,
                "protected_swing": protected,
                "sms_event_count": len(sms_events),
            },
            trigger="Structure broke with a completed close and the protected swing continues to hold.",
            invalidation="A completed close beyond the protected swing invalidates the continuation.",
            reason=(
                f"{len(bms_events)} BMS and {len(sms_events)} SMS events align "
                f"{structure_side}; the latest break is inside the entry window."
            ),
            event_index=latest_structured_bms.get("index"),
        )
        _note("BMS_SMS_STRUCTURE", state="CANDIDATE", playbook="STRUCTURE_CONTINUATION",
              side=structure_side,
              reason=f"{len(bms_events)} BMS; {len(sms_events)} SMS aligned {structure_side}.")
    elif structure_side in {"BUY", "SELL"}:
        _note("BMS_SMS_STRUCTURE", state="CONFLUENCE", side=structure_side,
              reason=f"{len(bms_events)} BMS; {len(sms_events)} SMS align {structure_side}, latest outside window.")
    else:
        _note("BMS_SMS_STRUCTURE", state="ABSENT", reason="No completed structural break.")

    ote_side = _side(fibonacci.get("side"))
    if _truthy(fibonacci, "in_ote"):
        _note("FIBONACCI_OTE", state="CONFLUENCE", side=ote_side,
              reason=f"Retracement {fibonacci.get('retracement_ratio')} sits inside the OTE band.")
    elif _truthy(fibonacci, "at_or_beyond_50"):
        _note("FIBONACCI_OTE", state="CONFLUENCE", side=ote_side,
              reason=f"Retracement {fibonacci.get('retracement_ratio')} reached the 50 percent area.")
    else:
        _note("FIBONACCI_OTE", state="ABSENT",
              reason="Outside a confirmed retracement event.")

    amd_side = _side(amd.get("side"))
    amd_event_recent = (
        amd.get("event_index") is None or _recent(amd.get("event_index"))
    )
    if _truthy(amd, "complete") and amd_side in {"BUY", "SELL"} and amd_event_recent:
        _add_candidate(
            side=amd_side, playbook="AMD_DISTRIBUTION",
            profile="CONSERVATIVE", priority=80,
            rule_ids=["AMD_SEQUENCE_COMPLETE"], evidence=amd,
            trigger="Accumulation range, opposite-side manipulation reclaim, and distribution close all completed.",
            invalidation="A completed close back inside the accumulation range invalidates distribution.",
            reason=f"AMD distribution confirmed {amd_side}: {amd.get('state')}.",
            event_index=amd.get("event_index"),
        )
        _note("AMD_SEQUENCE", state="CANDIDATE", playbook="AMD_DISTRIBUTION",
              side=amd_side, reason=f"Distribution confirmed: {amd.get('state')}.")
    elif _truthy(amd, "complete") and amd_side in {"BUY", "SELL"}:
        _note("AMD_SEQUENCE", state="CONFLUENCE", side=amd_side,
              reason="Distribution confirmed but its confirming close aged out of the entry window.")
    elif amd_side in {"BUY", "SELL"}:
        _note("AMD_SEQUENCE", state="WATCHING", side=amd_side,
              reason=f"AMD state {amd.get('state')}; distribution not confirmed.")
    else:
        _note("AMD_SEQUENCE", state="ABSENT",
              reason=f"AMD state {amd.get('state') or 'INSUFFICIENT_VISIBLE_SEQUENCE'}.")

    news_side = _side(news_pivot.get("side"))
    good_news_bad_price = bool(news_pivot.get("good_news_bad_price_confirmed"))
    bad_news_good_price = bool(news_pivot.get("bad_news_good_price_confirmed"))
    if _truthy(news_pivot, "confirmed") and news_side in {"BUY", "SELL"}:
        persistence_note = (
            " pre-release pivot persists for the session"
            if news_pivot.get("pivot_persists_for_session")
            else ""
        )
        _add_candidate(
            side=news_side, playbook="POST_NEWS_PIVOT",
            profile="CONSERVATIVE", priority=78,
            rule_ids=["POST_NEWS_PIVOT_CONFIRMED"], evidence=news_pivot,
            trigger="Post-news displacement pivoted and the midpoint confirmation close completed.",
            invalidation=(
                "A completed close back through the pre-release NewsPivot is the hard exit."
                if news_pivot.get("pivot_persists_for_session")
                else "A completed close beyond the news extreme invalidates the pivot."
            ),
            reason=(
                f"Confirmed post-news pivot with midpoint reclaim;{persistence_note}"
                f" closes back through pivot so far: {int(_number(news_pivot.get('closes_back_through_pivot')))}."
            ),
        )
        _note("NEWS_PIVOT", state="CANDIDATE", playbook="POST_NEWS_PIVOT",
              side=news_side, reason=str(news_pivot.get("state") or "News pivot confirmed."))
    elif good_news_bad_price or bad_news_good_price:
        persistent_side = "SELL" if good_news_bad_price else "BUY"
        _note("NEWS_PIVOT", state="CONFLUENCE", playbook="POST_NEWS_PIVOT",
              side=persistent_side,
              reason=(
                  "Good-news/bad-price: rally folded back through the session's pre-release pivot."
                  if good_news_bad_price
                  else "Bad-news/good-price: sell-off reclaimed the session's pre-release pivot."
              ))
    elif _truthy(temporal, "high_impact_news_observed"):
        _note("NEWS_PIVOT", state="WATCHING",
              reason="High-impact news observed; entries suspended until a pivot closes.")
    else:
        _note("NEWS_PIVOT", state="ABSENT", reason="No confirmed NewsPivot event.")

    gap_detected = bool(sunday_gap.get("detected"))
    gap_side = _side(sunday_gap.get("side"))
    if gap_detected and gap_side in {"BUY", "SELL"}:
        _add_candidate(
            side=gap_side, playbook="SUNDAY_GAP_FADE",
            profile="CONSERVATIVE", priority=75,
            rule_ids=["SUNDAY_GAP_FADE_85PCT_FILL_WITHIN_48H"], evidence=sunday_gap,
            trigger="Fade the weekend gap during its first hour of the Monday session.",
            invalidation="A completed close making a new gap extreme invalidates the fade.",
            reason=(
                f"Weekend {str(sunday_gap.get('gap_direction') or '').lower()} gap of "
                f"{_number(sunday_gap.get('gap_size')):.4f} detected; about 85 percent fill within 48 hours."
            ),
            event_index=latest_index,
        )
        _note("SUNDAY_GAP_FADE", state="CANDIDATE", playbook="SUNDAY_GAP_FADE",
              side=gap_side, reason=f"Weekend gap {sunday_gap.get('gap_direction')} detected.")
    else:
        _note("SUNDAY_GAP_FADE", state="ABSENT",
              reason=str(sunday_gap.get("reason") or "No weekend gap context."))

    methods = [
        row for row in _rows(sakata.get("active_methods"))
        if _side(row.get("side")) in {"BUY", "SELL"}
    ]
    if methods:
        chosen = methods[0]
        method_names = ", ".join(str(row.get("method") or "METHOD") for row in methods[:4])
        _add_candidate(
            side=chosen.get("side"), playbook="SAKATA_METHOD",
            profile="AGGRESSIVE", priority=76,
            rule_ids=[f"SAKATA_{str(row.get('method') or 'METHOD').upper()}" for row in methods[:4]],
            evidence=sakata,
            trigger="The Sakata cycle formation completed with visible pivots and a confirming close.",
            invalidation="A completed close against the formation's cycle side cancels it.",
            reason=f"Active Sakata method(s): {method_names}.",
        )
        _note("SAKATA_METHODS", state="CANDIDATE", playbook="SAKATA_METHOD",
              side=chosen.get("side"), reason=f"Active method(s): {method_names}.")
    else:
        _note("SAKATA_METHODS", state="ABSENT", reason="No active Sakata method.")

    qualified = _rows(catalog.get("qualified_detections"))
    for row in qualified:
        confirmation_index = int(_number(row.get("confirmation_index"), -1))
        if confirmation_index != latest_index:
            continue
        reversal = str(row.get("family") or "").startswith("REVERSAL")
        _add_candidate(
            side=row.get("side"), playbook="CANDLE_REVERSAL_AT_STRUCTURE" if reversal else "CANDLE_CONTINUATION_AT_STRUCTURE",
            profile="CONSERVATIVE", priority=84,
            rule_ids=[str(row.get("rule_id") or "CANDLE_RULE")], evidence=row,
            trigger="The current candle completed pattern, prior-trend, price-location, confirmation, and timeframe requirements.",
            invalidation="A completed close through the pattern's structural invalidation side cancels it.",
            reason=f"Location-valid {row.get('rule_id')} confirmed on the latest closed candle.",
            event_index=confirmation_index,
        )
    has_recognized = bool(catalog.get("recognized_pattern_ids")) or bool(_rows(catalog.get("detections")))
    if qualified:
        _note("CANDLESTICK_CATALOGUE", state="CANDIDATE",
              playbook="CANDLE_REVERSAL_AT_STRUCTURE",
              side=qualified[-1].get("side"),
              reason=f"{len(qualified)} qualified detection(s) at the latest close.")
    elif has_recognized:
        _note("CANDLESTICK_CATALOGUE", state="WATCHING",
              reason="Patterns recognized but none satisfied location, trend, and confirmation filters.")
    else:
        _note("CANDLESTICK_CATALOGUE", state="ABSENT",
              reason="No catalogue pattern recognized.")

    if htf.get("strictly_enforced"):
        _note("HIGHER_TIMEFRAME_AUTHORITY", state="CONFLUENCE", side=htf_effective_side,
              reason=f"Strict HTF authority {str(htf.get('authority_timeframe') or '')} owns {htf_effective_side}.")
    elif htf:
        _note("HIGHER_TIMEFRAME_AUTHORITY", state="WATCHING", side=htf_effective_side,
              reason="HTF context observed without strict enforcement.")
    else:
        _note("HIGHER_TIMEFRAME_AUTHORITY", state="ABSENT", reason="No higher-timeframe context.")

    pair_dna_side = _side(pair_dna.get("side"))
    if _truthy(pair_dna, "profile_applied") and pair_dna_side in {"BUY", "SELL"}:
        _note("PAIR_DNA", state="CONFLUENCE", side=pair_dna_side,
              reason=f"Pair history leans {pair_dna_side} at {pair_dna.get('probability')}.")
    elif _truthy(pair_dna, "profile_applied"):
        _note("PAIR_DNA", state="WATCHING",
              reason="Pair history collected but directionally neutral.")
    else:
        _note("PAIR_DNA", state="ABSENT", reason="Pair history still collecting.")


    alignment_votes = {"BUY": 0.0, "SELL": 0.0}
    alignment_rows: list[dict[str, Any]] = []

    def _vote(family: str, side: object, weight: float, reason: str) -> None:
        normalized = _side(side)
        if normalized not in {"BUY", "SELL"} or weight <= 0.0:
            return
        alignment_votes[normalized] += float(weight)
        alignment_rows.append({
            "family": family,
            "side": normalized,
            "weight": round(float(weight), 6),
            "reason": _clip_text(reason),
        })

    if htf.get("strictly_enforced") and htf_effective_side in {"BUY", "SELL"}:
        _vote(
            "HIGHER_TIMEFRAME_AUTHORITY", htf_effective_side, 2.6,
            "Rule of forex: price continues in its direction until an opposing force; the HTF owns that direction.",
        )
    if major_side in {"BUY", "SELL"}:
        _vote("MAJOR_STRUCTURE_DIRECTION", major_side, 1.35, "Big-picture structure continues until an opposing force breaks it.")
    if inner_side in {"BUY", "SELL"}:
        _vote("INNER_STRUCTURE_DIRECTION", inner_side, 0.65, "Inner swing continues within the larger direction.")
    structured_latest_bms_pre = _mapping(structure_full.get("latest_bms"))
    if structure_side in {"BUY", "SELL"} and _truthy(structured_latest_bms_pre, "completed_close_confirmed"):
        _vote("BMS_SMS_STRUCTURE", structure_side, 1.8, "Completed structural break extends the move toward the next opposing force.")
    if line_flips:
        _vote("STRICT_WICK_TRENDLINE", line_flips[-1].get("current_action_side"), 2.25,
              "Trendline role flip marks a change of controlling direction at the big-picture level.")
    elif line_reactions:
        _vote("STRICT_WICK_TRENDLINE", line_reactions[-1].get("current_action_side"), 3.0,
              "Mature wick-line rejection is the sniper entry with the big picture.")
    if zone_flips:
        _vote("ROLE_FLIP_RETEST", zone_flips[-1].get("current_action_side"), 2.15,
              "Zone role flip re-hands control to the new side.")
    elif zone_reactions:
        _vote("SUPPLY_DEMAND_REACTION", zone_reactions[-1].get("current_action_side") or zone_reactions[-1].get("role_side"), 1.55,
              "Rejection AT supply or demand: candles only speak at significant areas.")
    if ob_side in {"BUY", "SELL"} and _truthy(active_block, "return_to_order_block"):
        _vote("ORDER_BLOCK_RTO", ob_side, 1.65, "Return-to-origin at the order block retests the institutional leg.")
    if sweep_side in {"BUY", "SELL"} and liquidity_state in {"SWEEP_RECLAIMED", "SWEEP_RECLAIM_BMS_CONFIRMED"}:
        _vote("TURTLE_SOUP", sweep_side, 2.1, "Smart-money sweep-reclaim catches the reversal of the manipulation leg.")
    amd_complete_vote = _truthy(amd, "complete")
    if amd_complete_vote and amd_side in {"BUY", "SELL"}:
        _vote("AMD_SEQUENCE", amd_side, 1.55, "Distribution confirms the continuation leg after manipulation.")
    ote_weight = 2.25 if _truthy(fibonacci, "in_ote") else (1.15 if _truthy(fibonacci, "at_or_beyond_50") else 0.0)
    if ote_weight > 0.0:
        _vote("FIBONACCI_OTE", fibonacci.get("side"), ote_weight, "Retracement location before continuation into the next opposing force.")
    for row in qualified:
        confirmation_index_value = int(_number(row.get("confirmation_index"), -1))
        if confirmation_index_value == latest_index and row.get("directional_authority"):
            _vote(
                "CANDLESTICK_CATALOGUE", row.get("side"),
                max(0.5, min(1.55, _number(row.get("weight"), 1.0))),
                f"{row.get('rule_id')} confirmed AT structural location.",
            )
    if _truthy(pair_dna, "profile_applied") and pair_dna_side in {"BUY", "SELL"}:
        _vote("PAIR_DNA", pair_dna_side, min(1.0, max(0.0, (_number(pair_dna.get("probability"), 0.5) - 0.5) * 4.0)),
              "Pair behavior history leans this side.")
    methods_vote = [row for row in _rows(sakata.get("active_methods")) if _side(row.get("side")) in {"BUY", "SELL"}]
    for row in methods_vote:
        _vote("SAKATA_METHODS", row.get("side"), 0.85, f"Sakata cycle formation {row.get('method')}.")
    if good_news_bad_price or bad_news_good_price:
        _vote(
            "NEWS_PIVOT",
            "SELL" if good_news_bad_price else "BUY",
            0.9,
            (
                "Good-news/bad-price: rally folded back through the session's pre-release pivot."
                if good_news_bad_price
                else "Bad-news/good-price: sell-off reclaimed the session's pre-release pivot."
            ),
        )
    if gap_detected and gap_side in {"BUY", "SELL"}:
        _vote(
            "SUNDAY_GAP_FADE", gap_side, 1.2,
            f"Weekend {str(sunday_gap.get('gap_direction') or '').lower()} gap fades; about 85 percent fill within 48 hours.",
        )

    leader = (
        "BUY" if alignment_votes["BUY"] > alignment_votes["SELL"]
        else "SELL" if alignment_votes["SELL"] > alignment_votes["BUY"]
        else "NEUTRAL"
    )
    directional_alignment = {
        "BUY": round(alignment_votes["BUY"], 6),
        "SELL": round(alignment_votes["SELL"], 6),
        "leader": leader,
        "margin": round(abs(alignment_votes["BUY"] - alignment_votes["SELL"]), 6),
        "contributions": alignment_rows,
        "principle": (
            "All buy teachings merge into one BUY case and all sell teachings into one SELL case; "
            "price continues until an opposing force (support, resistance, supply, demand)."
        ),
    }

    trending_regime = bool(
        (major_side in {"BUY", "SELL"} and major_side == inner_side)
        or (
            structure_side in {"BUY", "SELL"}
            and _truthy(structured_latest_bms_pre, "completed_close_confirmed")
            and _recent(structured_latest_bms_pre.get("index"))
        )
    )
    ranging_regime = bool(
        not trending_regime
        and (_truthy(amd, "accumulation_confirmed") or bool(line_reactions or zone_reactions))
    )
    regime_state = "TRENDING" if trending_regime else "RANGING" if ranging_regime else "UNCLASSIFIED"
    regime_notes: list[str] = []



    if not candidates:
        directional_sides = [
            major_side, inner_side, structure_side, htf_effective_side,
            pair_dna_side, sweep_side, ob_side, amd_side, ote_side,
        ]
        sides_present = {side_value for side_value in directional_sides if side_value in {"BUY", "SELL"}}
        if len(sides_present) == 1:
            watch_side = next(iter(sides_present))
        elif leader in {"BUY", "SELL"}:
            watch_side = leader
        else:
            watch_side = (
                structure_side if structure_side in {"BUY", "SELL"}
                else htf_effective_side if htf_effective_side in {"BUY", "SELL"}
                else major_side if major_side in {"BUY", "SELL"}
                else "NEUTRAL"
            )
        chop = not sides_present and leader == "NEUTRAL"
        base = {
            "status": STATUS_MARKET_CHOP_V3 if chop else STATUS_WAITING_FOR_TRIGGER_V3,
            "action": "WAIT",
            "watch_side": watch_side,
            "playbook": CHOP_PLAYBOOK_V3 if chop else "AWAITING_BOOK_TRIGGER",
            "playbook_family": "",
            "profile": "NONE",
            "scenario": (
                "No book family carries directional evidence; the market is genuinely directionless chop."
                if chop
                else "Book families carry directional context but no completed setup has triggered; most moments are no-trade."
            ),
            "trigger": (
                "Wait until any family completes its setup on a closed candle."
                if chop
                else "Wait for a strict rejection, ordered retest, sweep reclaim, or location-qualified candle on the latest closed candle."
            ),
            "invalidation": "No active rule action exists to invalidate.",
            "evidence_strength": 0.0,
            "confluence_count": 0,
            "rule_ids": [],
            "provenance": [],
            "opposing_force_conflict": False,
            "entry_profiles": _mapping(hlz.get("entry_profiles")),
            "selected_evidence": {},
            "resolution": "CHOP" if chop else "WATCHING",
            "blocked_reasons": [],
            "profit_room": _profit_room_assessment(source, watch_side if watch_side in {"BUY", "SELL"} else "SELL", geometry) if watch_side in {"BUY", "SELL"} else {},
            "stop_plan": {
                "side": watch_side if watch_side in {"BUY", "SELL"} else "",
                "minimum_distance_px": round(1.2 * _number(geometry.get("median_candle_range_y_px"), 0.0), 6) if _number(geometry.get("median_candle_range_y_px"), 0.0) > 0.0 else None,
                "structure_buffer_px": round(0.2 * _number(geometry.get("median_candle_range_y_px"), 0.0), 6) if _number(geometry.get("median_candle_range_y_px"), 0.0) > 0.0 else None,
                "basis": "DONNELLY_PP338_342_STRUCTURE_PLUS_ADR_BUFFER",
                "adr_proxy_basis": "median_closed_candle_range_y_px",
                "immovable_at_publish": True,
                "reentry_cooldown_after_hit": "4h (execution-side enforcement)",
                "note": "No active setup; distances are the standing minimums for the next entry.",
            },
            "regime": regime_state,
            "regime_notes": sorted(set(regime_notes)),
            "directional_alignment": directional_alignment,
            "family_resolutions": [family_notes[key] for key in _BOOK_STRATEGY_FAMILIES_V3 if key in family_notes],
            "entry_window_candles": STRATEGIST_ENTRY_WINDOW_CANDLES_V3,
            "horizon_published": False,
            "execution_authority": False,
        }
        return base


    fade_playbooks_v3 = {"TRENDLINE_REJECTION", "SUPPORT_RESISTANCE_REJECTION", "ROLE_FLIP_RETEST"}
    continuation_playbooks_v3 = {
        "BREAK_RETEST", "STRUCTURE_CONTINUATION", "AMD_DISTRIBUTION",
        "ORDER_BLOCK_RTO", "STOP_HUNT_BMS_RTO", "BMS_OTE_RTO",
    }
    if regime_state == "RANGING":
        for row in candidates:
            if row["playbook"] in fade_playbooks_v3:
                row["priority"] += 2
            elif row["playbook"] in continuation_playbooks_v3:
                row["priority"] -= 2
                regime_notes.append(
                    "Range regime expects false breaks; breakout-continuation entries are demoted per Donnelly pp.141-144."
                )
    elif regime_state == "TRENDING":
        for row in candidates:
            if row["playbook"] in fade_playbooks_v3:
                row["priority"] -= 1
            elif row["playbook"] in continuation_playbooks_v3:
                row["priority"] += 1

    candidates.sort(key=lambda row: int(row["priority"]), reverse=True)
    selected = candidates[0]
    selected_side = str(selected["side"])
    selected_playbook = str(selected["playbook"])
    selected_family = str(selected["family"])

    same_priority_conflict = any(
        row["side"] != selected_side and int(row["priority"]) >= int(selected["priority"]) - 2
        for row in candidates[1:]
    )
    aligned_rule_ids = {
        rule_id
        for row in candidates
        if row["side"] == selected_side
        for rule_id in row["rule_ids"]
    }
    if major_side == selected_side:
        aligned_rule_ids.add("MAJOR_STRUCTURE_DIRECTION")
    if inner_side == selected_side:
        aligned_rule_ids.add("INNER_STRUCTURE_DIRECTION")
    effective_htf = _side(htf.get("effective_side") or htf.get("side"))
    htf_ok = bool(
        not htf.get("strictly_enforced")
        or effective_htf == selected_side
    )
    if htf_ok and effective_htf == selected_side:
        aligned_rule_ids.add("HTF_DIRECTIONAL_AUTHORITY")
    if _side(fibonacci.get("side")) == selected_side and (
        _truthy(fibonacci, "in_ote") or _truthy(fibonacci, "at_or_beyond_50")
    ):
        aligned_rule_ids.add("OTE_LOCATION_CONFLUENCE")
    if _truthy(pair_dna, "profile_applied") and pair_dna_side == selected_side:
        aligned_rule_ids.add("PAIR_DNA_EMPIRICAL_DIRECTION")
    if ob_side == selected_side:
        aligned_rule_ids.add("ORDER_BLOCK_ALIGNMENT")
    if sweep_side == selected_side:
        aligned_rule_ids.add("LIQUIDITY_SWEEP_SIDE")
    if amd_side == selected_side and _truthy(amd, "complete"):
        aligned_rule_ids.add("AMD_PHASE_ALIGNMENT")
    if sequence_side == selected_side and _truthy(hlz_bms, "confirmed"):
        aligned_rule_ids.add("BMS_ALIGNMENT")
    structured_latest_bms = _mapping(structure_full.get("latest_bms"))
    if _side(structured_latest_bms.get("side")) == selected_side and _truthy(
        structured_latest_bms, "completed_close_confirmed"
    ):
        aligned_rule_ids.add("BMS_ALIGNMENT")

    opposing_targets = _mapping(
        source.get("opposing_force_targets_v3")
        or source.get("opposing_targets")
        or full_stack.get("opposing_targets")
    )
    opposing = _mapping(
        _mapping(opposing_targets.get("_current_reactions_v3")).get("by_action_side")
        or _mapping(source.get("opposing_force_reactions")).get("by_action_side")
        or _mapping(full_stack.get("opposing_force_reactions")).get("by_action_side")
    )
    opposing_conflict = bool(_mapping(opposing.get(selected_side)).get("at_current_force"))
    suspended = bool(temporal.get("entry_suspended_until_news_pivot"))
    confluence_count = len(aligned_rule_ids)
    profit_room = _profit_room_assessment(source, selected_side, geometry)
    range_unit = _number(geometry.get("median_candle_range_y_px"), 0.0)
    stop_plan = {
        "side": selected_side,
        "minimum_distance_px": round(1.2 * range_unit, 6) if range_unit > 0.0 else None,
        "structure_buffer_px": round(0.2 * range_unit, 6) if range_unit > 0.0 else None,
        "basis": "DONNELLY_PP338_342_STRUCTURE_PLUS_ADR_BUFFER",
        "adr_proxy_basis": "median_closed_candle_range_y_px",
        "immovable_at_publish": True,
        "reentry_cooldown_after_hit": "4h (execution-side enforcement)",
        "note": (
            "Place the stop beyond the setup's defending structure plus a 20 percent volatility buffer; "
            "never closer than 1.2x the volatility unit. The level is fixed at publish time."
            if range_unit > 0.0
            else "Volatility unit unmeasured; distances publish as null rather than guessed."
        ),
    }
    advisories: list[str] = []
    if confluence_count < 2:
        advisories.append("single confluence")
    if not htf_ok:
        advisories.append("higher-timeframe conflict noted")
    if opposing_conflict:
        advisories.append("current opposing-force reaction noted")
    if suspended:
        advisories.append("high-impact news context unresolved")
    if profit_room.get("sufficient") is False:
        advisories.append(profit_room.get("reason") or "thin profit room")

    # The strategist is the authority: a named candidate with a valid side is
    # actionable. Strict gate mode is opt-in only.
    if same_priority_conflict:
        advisories.append("equal-priority directional conflict resolved by priority order")
    ready = bool(
        selected_side in {"BUY", "SELL"}
        and (
            not STRATEGIST_STRICT_GATES
            or (
                confluence_count >= 2
                and htf_ok
                and not opposing_conflict
                and not same_priority_conflict
                and not suspended
                and profit_room.get("sufficient") is not False
            )
        )
    )
    profile = str(selected["profile"])
    status = STATUS_STRATEGIST_ACTION_CONFIRMED_V3 if ready else STATUS_STRATEGIC_CONFLICT_V3
    action = selected_side if ready else "WAIT"

    blocked_reasons: list[str] = []
    if confluence_count < 2:
        blocked_reasons.append("fewer than two independent confluences")
    if not htf_ok:
        blocked_reasons.append("higher-timeframe conflict")
    if opposing_conflict:
        blocked_reasons.append("current opposing-force reaction")
    if same_priority_conflict:
        blocked_reasons.append("equal-priority directional conflict")
    if suspended:
        blocked_reasons.append("unresolved high-impact news context")
    if profit_room.get("sufficient") is False:
        blocked_reasons.append(profit_room.get("reason") or "insufficient profit room")
    provenance: list[dict[str, Any]] = []
    evidence = _mapping(selected.get("evidence"))
    direct_provenance = _mapping(evidence.get("rule_provenance"))
    if direct_provenance:
        provenance.append(dict(direct_provenance))
    elif evidence.get("hlz_summary"):
        provenance.append(dict(_PLAYBOOK_PROVENANCE_V3.get(selected_playbook) or {
            "source_file": _HLZ_FILE, "pdf_pages": [], "source_section": "HLZ sequences",
        }))
    elif evidence.get("source_file"):
        provenance.append({
            "source_file": evidence.get("source_file"),
            "pdf_pages": list(evidence.get("pdf_pages") or []),
            "source_section": evidence.get("source_section") or evidence.get("family"),
        })
    else:
        fallback = _PLAYBOOK_PROVENANCE_V3.get(selected_playbook)
        if fallback:
            provenance.append(dict(fallback))
    for row in provenance:
        row.setdefault("rule_id", next(iter(selected.get("rule_ids") or []), selected_playbook))

    strength = (
        min(1.0, 0.35 + 0.12 * confluence_count + (0.12 if profile == "CONSERVATIVE" else 0.06))
        if ready
        else min(0.5, 0.15 + 0.08 * confluence_count)
    )
    scenario = (
        f"{selected_side} {selected_playbook.replace('_', ' ').lower()} is live: {selected['reason']} "
        f"Confluence {confluence_count}: {', '.join(sorted(aligned_rule_ids))}."
        if ready
        else f"{selected_side} {selected_playbook} held by directional conflict: "
        + (", ".join(blocked_reasons) if blocked_reasons else "gates unmet")
        + "."
    )

    for key, note in family_notes.items():
        if key == selected_family and ready:
            note["resolution"] = "ACTIONABLE"
            note["playbook"] = selected_playbook
            note["side"] = selected_side
        elif key == selected_family:
            note["resolution"] = "CONFLICT_HELD"
            note["playbook"] = selected_playbook
            note["side"] = selected_side

    return {
        "status": status,
        "action": action,
        "watch_side": selected_side,
        "playbook": selected_playbook,
        "playbook_family": selected_family,
        "profile": profile,
        "scenario": scenario,
        "trigger": selected["trigger"],
        "invalidation": selected["invalidation"],
        "evidence_strength": round(strength, 6),
        "confluence_count": confluence_count,
        "rule_ids": sorted(aligned_rule_ids),
        "provenance": provenance,
        "opposing_force_conflict": opposing_conflict,
        "entry_profiles": _mapping(hlz.get("entry_profiles")),
        "selected_evidence": evidence,
        "resolution": "ACTIONABLE" if ready else "CONFLICT_HELD",
        "blocked_reasons": blocked_reasons,
        "advisories": advisories,
        "profit_room": profit_room,
        "stop_plan": stop_plan,
        "regime": regime_state,
        "regime_notes": sorted(set(regime_notes)),
        "directional_alignment": directional_alignment,
        "family_resolutions": [
            family_notes[key] for key in _BOOK_STRATEGY_FAMILIES_V3 if key in family_notes
        ],
        "entry_window_candles": STRATEGIST_ENTRY_WINDOW_CANDLES_V3,
        "horizon_published": False,
        "execution_authority": False,
    }


__all__ = [
    "BOOK_STRATEGY_CONTEXT_SCHEMA_V3",
    "CHOP_PLAYBOOK_V3",
    "STATUS_STRATEGIST_ACTION_CONFIRMED_V3",
    "STATUS_STRATEGIC_CONFLICT_V3",
    "STATUS_WAITING_FOR_TRIGGER_V3",
    "STRATEGIST_STRICT_GATES",
    "STATUS_MARKET_CHOP_V3",
    "STRATEGIST_ENTRY_WINDOW_CANDLES_V3",
    "_BOOK_STRATEGY_FAMILIES_V3",
    "_PLAYBOOK_FAMILY_V3",
    "evaluate_book_strategy_context_v3",
    "select_current_book_action_v3",
]
