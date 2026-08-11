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
    htf_reversal_confirmed = bool(
        sms.get("confirmed")
        and bms.get("confirmed")
        and str(sms.get("side")) == bms_side
        and bms_side in {"BUY", "SELL"}
        and bms_side != effective_htf_side
    )
    if htf_reversal_confirmed:
        effective_htf_side = bms_side
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
        or htf_reversal_confirmed
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
            or htf_reversal_confirmed
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
            "requires": ["CAUSAL_EVENT_ORDER", "COMPLETED_RETEST_OR_RTO", "HTF_ALIGNMENT_OR_CONFIRMED_REVERSAL"],
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
        "higher_timeframe": {**htf, "effective_side": effective_htf_side, "reversal_confirmed": htf_reversal_confirmed},
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
        "rule_calibration_v3": _mapping(full_stack.get("rule_calibration")),
        "candle_location_history": _mapping(full_stack.get("candle_location_history")),
        "opposing_targets": {
            **_mapping(full_stack.get("opposing_targets")),
            "_current_reactions_v3": opposing_reactions,
        },
        "rule_trace": traces,
    }


def select_current_book_action_v3(control: Mapping[str, Any] | None) -> dict[str, Any]:
    """Select a current closed-candle book action without score fallback."""

    source = _mapping(control)
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
    observed_count = int(_number(source.get("observed_candle_count"), 0))
    latest_index = observed_count - 1
    candidates: list[dict[str, Any]] = []

    def add_candidate(
        *, side: object, playbook: str, profile: str, priority: int,
        rule_id: str, evidence: Mapping[str, Any], current: bool,
        trigger: str, invalidation: str,
    ) -> None:
        normalized = _side(side)
        if normalized not in {"BUY", "SELL"} or not current:
            return
        candidates.append({
            "side": normalized,
            "playbook": playbook,
            "profile": profile,
            "priority": priority,
            "rule_id": rule_id,
            "evidence": dict(evidence),
            "trigger": trigger,
            "invalidation": invalidation,
        })

    for row in _rows(trendline.get("current_role_flip_retests")):
        add_candidate(side=row.get("current_action_side"), playbook="BREAK_RETEST", profile="CONSERVATIVE", priority=100, rule_id="STRICT_TRENDLINE_ROLE_FLIP", evidence=row, current=True, trigger="The completed retest held the broken trendline in its new role.", invalidation="A completed close back through the flipped trendline invalidates the hold.")
    for row in _rows(zones.get("current_role_flip_retests")):
        add_candidate(side=row.get("current_action_side"), playbook="ROLE_FLIP_RETEST", profile="CONSERVATIVE", priority=98, rule_id="SUPPORT_RESISTANCE_ROLE_FLIP", evidence=row, current=True, trigger="The completed retest held the support/resistance zone in its new role.", invalidation="A completed close through the far side of the flipped zone invalidates the hold.")
    for row in _rows(trendline.get("current_reactions")):
        add_candidate(side=row.get("current_action_side") or row.get("role_side"), playbook="TRENDLINE_REJECTION", profile="AGGRESSIVE", priority=92, rule_id="STRICT_TRENDLINE_REJECTION", evidence=row, current=True, trigger="The latest completed candle rejected a strict three-touch wick line.", invalidation="A completed body close through the defending line invalidates the reaction.")
    for row in _rows(zones.get("current_reactions")):
        add_candidate(side=row.get("current_action_side") or row.get("role_side"), playbook="SUPPORT_RESISTANCE_REJECTION", profile="AGGRESSIVE", priority=88, rule_id="SUPPORT_RESISTANCE_REJECTION", evidence=row, current=True, trigger="The latest completed candle rejected the exact support/resistance zone.", invalidation="A completed close beyond the far edge of the zone invalidates the reaction.")
    if hlz.get("entry_sequence_ready") and hlz.get("current_terminal_event"):
        bms = _mapping(hlz.get("bms"))
        playbook = "STOP_HUNT_BMS_RTO" if hlz.get("stop_hunt") else "BMS_OTE_RTO"
        add_candidate(side=bms.get("side"), playbook=playbook, profile="CONSERVATIVE", priority=96, rule_id="HLZ_CAUSAL_SEQUENCE_COMPLETE", evidence=hlz, current=True, trigger="The current candle completed the ordered HLZ retracement or return sequence.", invalidation="An opposing completed structure break invalidates the sequence.")
    qualified = _rows(catalog.get("qualified_detections"))
    for row in qualified:
        confirmation_index = int(_number(row.get("confirmation_index"), -1))
        add_candidate(side=row.get("side"), playbook="CANDLE_REVERSAL_AT_STRUCTURE" if str(row.get("family") or "").startswith("REVERSAL") else "CANDLE_CONTINUATION_AT_STRUCTURE", profile="CONSERVATIVE", priority=84, rule_id=str(row.get("rule_id") or "CANDLE_RULE"), evidence=row, current=confirmation_index == latest_index, trigger="The current candle completed pattern, prior-trend, price-location, confirmation, and timeframe requirements.", invalidation="A completed close through the pattern's structural invalidation side cancels it.")

    if not candidates:
        bms = _mapping(hlz.get("bms"))
        watch_side = _side(bms.get("side")) if bms.get("confirmed") else "NEUTRAL"
        return {
            "status": "WAITING_FOR_CURRENT_BOOK_TRIGGER",
            "action": "WAIT",
            "watch_side": watch_side,
            "playbook": "UNRESOLVED",
            "profile": "NONE",
            "scenario": "No current completed-candle book reaction or ordered retest is confirmed.",
            "trigger": "Wait for a strict rejection, ordered retest, or location-qualified candle confirmation on the latest closed candle.",
            "invalidation": "No active rule action exists to invalidate.",
            "evidence_strength": 0.0,
            "confluence_count": 0,
            "rule_ids": [],
            "provenance": [],
            "opposing_force_conflict": False,
            "entry_profiles": _mapping(hlz.get("entry_profiles")),
            "horizon_published": False,
            "execution_authority": False,
        }

    candidates.sort(key=lambda row: int(row["priority"]), reverse=True)
    selected = candidates[0]
    selected_side = str(selected["side"])
    same_priority_conflict = any(
        row["side"] != selected_side and int(row["priority"]) >= int(selected["priority"]) - 2
        for row in candidates[1:]
    )
    aligned_rule_ids = {str(row["rule_id"]) for row in candidates if row["side"] == selected_side}
    major = _side(source.get("major_structure_side"))
    inner = _side(source.get("inner_structure_side"))
    if major == selected_side:
        aligned_rule_ids.add("MAJOR_STRUCTURE_DIRECTION")
    if inner == selected_side:
        aligned_rule_ids.add("INNER_STRUCTURE_DIRECTION")
    effective_htf = _side(htf.get("effective_side") or htf.get("side"))
    htf_ok = bool(
        not htf.get("strictly_enforced")
        or effective_htf == selected_side
        or htf.get("reversal_confirmed")
    )
    if htf_ok and effective_htf == selected_side:
        aligned_rule_ids.add("HTF_DIRECTIONAL_AUTHORITY")
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
    ready = bool(
        confluence_count >= 2
        and htf_ok
        and not opposing_conflict
        and not same_priority_conflict
        and not suspended
    )
    profile = str(selected["profile"])
    status = "BOOK_ACTION_CONFIRMED" if ready else "BOOK_EVIDENCE_CONFLICT"
    action = selected_side if ready else "WAIT"
    provenance: list[dict[str, Any]] = []
    evidence = _mapping(selected.get("evidence"))
    direct_provenance = _mapping(evidence.get("rule_provenance"))
    if direct_provenance:
        provenance.append(dict(direct_provenance))
    elif evidence.get("source_file"):
        provenance.append({
            "source_file": evidence.get("source_file"),
            "pdf_pages": list(evidence.get("pdf_pages") or []),
            "source_section": evidence.get("source_section") or evidence.get("family"),
        })
    elif str(selected.get("playbook") or "").startswith(("STOP_HUNT", "BMS_OTE")):
        provenance.append({
            "source_file": _HLZ_FILE,
            "pdf_pages": [9, 11, 87, 88, 93, 94, 103, 105],
            "source_section": "BMS, retracement, RTO, confluence, and HTF refinement",
        })
    elif "ROLE_FLIP" in str(selected.get("playbook") or ""):
        provenance.append({
            "source_file": _TRENDLINE_FILE,
            "pdf_pages": [26, 27, 33, 58],
            "source_section": "Support, resistance, break, retest, and reaction",
        })
    strength = min(1.0, 0.35 + 0.12 * confluence_count + (0.12 if profile == "CONSERVATIVE" else 0.06)) if ready else 0.0
    scenario = f"{selected_side} {str(selected['playbook']).replace('_', ' ').lower()} is current on the latest completed candle."
    if not ready:
        reasons = []
        if confluence_count < 2:
            reasons.append("fewer than two independent confluences")
        if not htf_ok:
            reasons.append("higher-timeframe conflict")
        if opposing_conflict:
            reasons.append("current opposing-force reaction")
        if same_priority_conflict:
            reasons.append("equal-priority directional conflict")
        if suspended:
            reasons.append("unresolved event context")
        scenario = f"{selected_side} evidence is present but cannot become an action: {', '.join(reasons)}."
    return {
        "status": status,
        "action": action,
        "watch_side": selected_side,
        "playbook": selected["playbook"],
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
        "horizon_published": False,
        "execution_authority": False,
    }


__all__ = [
    "BOOK_STRATEGY_CONTEXT_SCHEMA_V3",
    "evaluate_book_strategy_context_v3",
    "select_current_book_action_v3",
]
