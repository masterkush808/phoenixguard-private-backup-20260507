"""Current-scenario book-rule action signal for PhoenixGuard V3.

The complete book engine remains private and may calculate internal study
paths. This public projection reports only the action prescribed by the latest
closed-candle rules and exact chart geometry supporting that action.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from typing import Any

from phoenixguard.decision.book_strategy_context_v3 import (
    BOOK_STRATEGY_CONTEXT_SCHEMA_V3,
    select_current_book_action_v3,
)
from phoenixguard.decision.book_strategy_full_stack_v3 import (
    FULL_BOOK_STACK_SCHEMA_V3,
)
from phoenixguard.decision.candlestick_rule_catalog_v3 import (
    CANDLESTICK_CATALOG_SCHEMA_V3,
)


BOOK_RULE_ACTION_SIGNAL_SCHEMA_V3 = "PG_BOOK_RULE_ACTION_SIGNAL_V3"


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _side(*values: object) -> str:
    for value in values:
        text = "".join(
            character if character.isalnum() else "_"
            for character in str(value or "").strip().upper()
        )
        tokens = {token for token in text.split("_") if token}
        buy = bool(tokens & {
            "BUY", "BULL", "BULLISH", "UP", "UPTREND", "UPSIDE", "LONG",
            "SUPPORT", "DEMAND", "ASCENDING",
        })
        sell = bool(tokens & {
            "SELL", "BEAR", "BEARISH", "DOWN", "DOWNTREND", "DOWNSIDE", "SHORT",
            "RESIST", "RESISTANCE", "SUPPLY", "DESCENDING",
        })
        if buy and not sell:
            return "BUY"
        if sell and not buy:
            return "SELL"
    return "NEUTRAL"


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return str(value or "").strip().upper() in {
        "ACTIVE", "ALIGNED", "COMPLETE", "CONFIRMED", "QUALIFIED",
        "READY", "TRUE", "VALID", "YES",
    }


def _first_truth(payload: Mapping[str, Any], *keys: str) -> bool:
    return any(_truthy(payload.get(key)) for key in keys)


def _safe_text(value: object, default: str = "", *, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    return text[:limit] or default


def _stable_id(prefix: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, default=str, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def _point_pairs(value: object) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    output: list[list[float]] = []
    for raw in value:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or len(raw) < 2:
            continue
        x = _number(raw[0], math.nan)
        y = _number(raw[1], math.nan)
        if math.isfinite(x) and math.isfinite(y):
            output.append([round(x, 6), round(y, 6)])
    return output


def _bounds(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) < 4:
        return []
    values = [_number(item, math.nan) for item in value[:4]]
    if not all(math.isfinite(item) for item in values):
        return []
    x0, y0, x1, y1 = values
    return [
        round(min(x0, x1), 6), round(min(y0, y1), 6),
        round(max(x0, x1), 6), round(max(y0, y1), 6),
    ]


def _pattern_summary(control: Mapping[str, Any]) -> dict[str, Any]:
    catalog = _mapping(control.get("candlestick_catalog_v3") or control.get("candlestick_catalog"))
    scanner = _mapping(control.get("book_scanner_ranking_v3"))
    rankings = _rows(scanner.get("ranked_patterns") or scanner.get("rankings"))
    qualified = _rows(catalog.get("qualified_detections") or catalog.get("qualified_patterns"))
    selected = _mapping(scanner.get("selected_pattern"))
    if selected:
        selected_rule_id = _safe_text(selected.get("rule_id"))
        selected = next(
            (row for row in qualified if _safe_text(row.get("rule_id")) == selected_rule_id),
            selected,
        )
    elif qualified:
        selected = max(qualified, key=lambda row: _number(row.get("weight")))
    elif rankings:
        selected = rankings[0]
    recognized = list(
        catalog.get("recognized_pattern_ids")
        or control.get("candlestick_patterns")
        or []
    )
    pattern = _safe_text(
        selected.get("pattern")
        or selected.get("pattern_id")
        or selected.get("rule_id")
        or selected.get("name")
        or (recognized[0] if recognized else ""),
        "No location-valid candle pattern",
    )
    traceability = _mapping(control.get("rule_traceability_v3"))
    confirmation_index = int(
        _number(
            selected.get("confirmation_index", selected.get("pattern_end_index", selected.get("candle_index", selected.get("end_index", selected.get("index", -1))))),
            -1.0,
        )
    )
    pattern_end_index = int(_number(selected.get("pattern_end_index"), confirmation_index))
    pattern_start_index = int(_number(selected.get("pattern_start_index"), pattern_end_index))
    return {
        "selected_pattern": pattern,
        "side": _side(selected.get("side"), selected.get("direction")),
        "location_valid": bool(selected) and bool(selected.get("directional_authority", selected.get("location_valid"))) and not _truthy(selected.get("disqualified")),
        "recognized_count": len(recognized),
        "qualified_count": len(qualified),
        "catalog_pattern_count": int(_number(
            catalog.get("catalog_pattern_count"),
            _number(traceability.get("catalog_pattern_count")),
        )),
        "pattern_start_index": pattern_start_index,
        "pattern_end_index": pattern_end_index,
        "confirmation_index": confirmation_index,
        "selected_candle_index": confirmation_index,
    }


def _market_geometry(
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Latest close and median candle height in chart pixel space."""
    rows = [dict(row) for row in candles if isinstance(row, Mapping)]
    if not rows:
        return {}
    latest = rows[-1]
    close_y = latest.get("close_y", latest.get("close_y_px"))
    ranges: list[float] = []
    for row in rows[-12:]:
        top = row.get("top_y", row.get("wick_top_px", row.get("high_y")))
        bottom = row.get("bottom_y", row.get("wick_bottom_px", row.get("low_y")))
        top_value = _number(top, float("nan"))
        bottom_value = _number(bottom, float("nan"))
        if math.isfinite(top_value) and math.isfinite(bottom_value):
            ranges.append(abs(top_value - bottom_value))
    median_range = sorted(ranges)[len(ranges) // 2] if ranges else 0.0
    return {
        "latest_close_y_px": _number(close_y, float("nan")) if math.isfinite(_number(close_y, float("nan"))) else None,
        "median_candle_range_y_px": median_range,
    }


def _strategy_row(
    strategy_id: str,
    label: str,
    *,
    active: bool,
    side: str = "NEUTRAL",
    evidence: str,
    waiting: bool = False,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "label": label,
        "status": "ACTIVE" if active else "WATCHING" if waiting else "NOT_PRESENT",
        "active": active,
        "side": side if side in {"BUY", "SELL"} else "NEUTRAL",
        "evidence": _safe_text(evidence, "No completed-candle evidence"),
    }


def _strategy_report(control: Mapping[str, Any], candle: Mapping[str, Any]) -> list[dict[str, Any]]:
    line = _mapping(control.get("trendline_context"))
    full_stack = _mapping(control.get("full_non_indicator_stack_v3"))
    trendline_full = _mapping(
        control.get("trendline_contracts_full_v3")
        or control.get("trendline_contracts")
        or full_stack.get("trendline_contracts")
    )
    strict_line_reactions = _rows(trendline_full.get("current_reactions"))
    strict_line_flips = _rows(trendline_full.get("current_role_flip_retests"))
    hlz = _mapping(control.get("hlz_sequence_v3") or control.get("hlz_sequence"))
    role_flip = _mapping(control.get("role_flip_sequence_v3") or control.get("role_flip"))
    ote = _mapping(control.get("fibonacci_ote_v3") or control.get("fibonacci_ote"))
    structure = _mapping(control.get("market_structure_full_v3") or full_stack.get("market_structure"))
    order_blocks = _mapping(control.get("order_blocks_full_v3") or full_stack.get("order_blocks"))
    turtle = _mapping(control.get("liquidity_turtle_soup_v3") or full_stack.get("liquidity_turtle_soup"))
    amd = _mapping(control.get("amd_v3") or full_stack.get("amd"))
    news = _mapping(control.get("news_pivot_v3") or full_stack.get("news_pivot"))
    sakata = _mapping(control.get("sakata_v3") or full_stack.get("sakata"))
    zones = _mapping(
        trendline_full.get("support_resistance_contracts")
        or control.get("support_resistance_full_v3")
        or control.get("zone_context")
        or full_stack.get("support_resistance")
    )
    htf = _mapping(control.get("higher_timeframe_authority_v3") or control.get("higher_timeframe"))
    pair_dna = _mapping(control.get("pair_dna_forecast_context_v3") or control.get("pair_dna"))
    bms = _rows(structure.get("bms_events"))
    sms = _rows(structure.get("sms_events"))
    active_methods = list(sakata.get("active_methods") or [])
    line_side = _side(
        strict_line_flips[-1].get("current_action_side") if strict_line_flips else None,
        strict_line_reactions[-1].get("current_action_side") if strict_line_reactions else None,
        line.get("reaction_side"),
        line.get("break_side"),
    )
    hlz_side = _side(hlz.get("side"), hlz.get("direction"), control.get("initial_reaction_side"))
    structure_side = _side(
        structure.get("latest_side"), control.get("inner_structure_side"),
        control.get("major_structure_side"),
    )
    return [
        _strategy_row(
            "STRICT_WICK_TRENDLINE", "Strict wick trendline reaction",
            active=bool(
                strict_line_reactions or strict_line_flips
            ),
            waiting=bool(_number(trendline_full.get("valid_count")) > 0),
            side=line_side,
            evidence=(
                f"{int(_number(trendline_full.get('valid_count')))} valid; "
                f"{len(strict_line_reactions)} current rejections; "
                f"{len(strict_line_flips)} current role-flip retests"
            ),
        ),
        _strategy_row(
            "HLZ_EVENT_SEQUENCE", "HLZ stop-hunt, BMS and RTO sequence",
            active=_first_truth(hlz, "entry_sequence_ready", "complete", "confirmed"),
            side=hlz_side, waiting=bool(hlz),
            evidence=(
                f"stop_hunt={bool(hlz.get('stop_hunt'))}; "
                f"BMS={bool(hlz.get('bms'))}; RTO={bool(hlz.get('rto'))}"
            ),
        ),
        _strategy_row(
            "ROLE_FLIP_RETEST", "Support/resistance role-flip retest",
            active=_first_truth(role_flip, "complete", "confirmed", "ready"),
            side=_side(role_flip.get("side"), role_flip.get("direction")),
            waiting=bool(role_flip),
            evidence=_safe_text(role_flip.get("status") or role_flip.get("state"), "Sequence not complete"),
        ),
        _strategy_row(
            "FIBONACCI_OTE", "Fibonacci and OTE location",
            active=_first_truth(ote, "complete", "confirmed", "in_ote", "inside_ote", "ready"),
            side=_side(ote.get("side"), ote.get("direction"), hlz_side),
            waiting=bool(ote),
            evidence=_safe_text(ote.get("status") or ote.get("location"), "Outside a confirmed OTE event"),
        ),
        _strategy_row(
            "BMS_SMS_STRUCTURE", "BMS and SMS market structure",
            active=bool(bms or sms), side=structure_side,
            evidence=f"{len(bms)} BMS events; {len(sms)} SMS events",
        ),
        _strategy_row(
            "ORDER_BLOCK_RTO", "Order block and return-to-origin",
            active=bool(order_blocks.get("active_block")) or _first_truth(order_blocks, "complete", "confirmed"),
            side=_side(order_blocks.get("side"), order_blocks.get("direction"), structure_side),
            waiting=bool(order_blocks),
            evidence="Active order block is location-valid" if order_blocks.get("active_block") else "No active order-block return",
        ),
        _strategy_row(
            "TURTLE_SOUP", "Liquidity sweep and Turtle Soup",
            active=_first_truth(turtle, "complete", "confirmed", "ready"),
            side=_side(turtle.get("side"), turtle.get("direction")), waiting=bool(turtle),
            evidence=_safe_text(turtle.get("status") or turtle.get("event"), "No completed sweep-reclaim"),
        ),
        _strategy_row(
            "AMD_SEQUENCE", "Accumulation, manipulation and distribution",
            active=_first_truth(amd, "complete", "confirmed", "ready"),
            side=_side(amd.get("side"), amd.get("direction")), waiting=bool(amd),
            evidence=_safe_text(amd.get("phase") or amd.get("status"), "AMD sequence incomplete"),
        ),
        _strategy_row(
            "SAKATA_METHODS", "Sakata method catalogue",
            active=bool(active_methods), side=_side(sakata.get("side"), sakata.get("direction")),
            evidence=", ".join(str(item) for item in active_methods) if active_methods else "No active Sakata method",
        ),
        _strategy_row(
            "CANDLESTICK_CATALOGUE", "Full candlestick rule catalogue",
            active=int(candle.get("qualified_count", 0) or 0) > 0,
            side=str(candle.get("side") or "NEUTRAL"),
            waiting=int(candle.get("recognized_count", 0) or 0) > 0,
            evidence=(
                f"{candle.get('selected_pattern')}; {int(candle.get('qualified_count', 0) or 0)} "
                f"qualified of {int(candle.get('catalog_pattern_count', 0) or 0)} catalogued"
            ),
        ),
        _strategy_row(
            "SUPPLY_DEMAND_REACTION", "Supply, demand and opposing-force reaction",
            active=bool(_rows(zones.get("current_reactions")) or _rows(zones.get("current_role_flip_retests"))),
            waiting=bool(_rows(zones.get("active_contracts"))),
            side=_side(
                (_rows(zones.get("current_role_flip_retests"))[-1].get("current_action_side") if _rows(zones.get("current_role_flip_retests")) else None),
                (_rows(zones.get("current_reactions"))[-1].get("current_action_side") if _rows(zones.get("current_reactions")) else None),
            ),
            evidence=f"{len(_rows(zones.get('current_reactions')))} current rejections; {len(_rows(zones.get('current_role_flip_retests')))} current role-flip retests",
        ),
        _strategy_row(
            "HIGHER_TIMEFRAME_AUTHORITY", "Higher-timeframe structure authority",
            active=_first_truth(htf, "strictly_enforced", "aligned", "confirmed"),
            side=_side(htf.get("effective_side"), htf.get("side"), control.get("major_structure_side")),
            waiting=bool(htf),
            evidence=f"strict={bool(htf.get('strictly_enforced'))}; aligned={bool(htf.get('aligned', True))}",
        ),
        _strategy_row(
            "NEWS_PIVOT", "NewsPivot event rule",
            active=_first_truth(news, "confirmed", "complete", "ready"),
            side=_side(news.get("side"), news.get("direction")), waiting=bool(news),
            evidence=_safe_text(news.get("status") or news.get("event"), "No confirmed NewsPivot event"),
        ),
        _strategy_row(
            "PAIR_DNA", "Pair-specific behavior history",
            active=bool(pair_dna.get("profile_applied")),
            side=_side(pair_dna.get("side"), pair_dna.get("dominant_side")),
            evidence=(
                f"Pair history leans {pair_dna.get('side')} at {pair_dna.get('probability')}"
                if bool(pair_dna.get("profile_applied"))
                else _safe_text(pair_dna.get("current_regime"), "Pair history still collecting")
                + "; directionally neutral"
            ),
        ),
        _strategy_row(
            "SUNDAY_GAP_FADE", "Weekend gap fade (about 85 percent fill within 48 hours)",
            active=bool(_mapping(control.get("sunday_gap_fade_v3") or full_stack.get("sunday_gap_fade_v3")).get("detected")),
            side=_side((_mapping(control.get("sunday_gap_fade_v3") or full_stack.get("sunday_gap_fade_v3")).get("side"))),
            evidence=(
                f"Weekend {str((_mapping(control.get('sunday_gap_fade_v3') or full_stack.get('sunday_gap_fade_v3'))).get('gap_direction') or '').lower()} gap; "
                f"size {_number((_mapping(control.get('sunday_gap_fade_v3') or full_stack.get('sunday_gap_fade_v3'))).get('gap_size')):.4f}"
                if bool((_mapping(control.get('sunday_gap_fade_v3') or full_stack.get('sunday_gap_fade_v3'))).get("detected"))
                else str((_mapping(control.get('sunday_gap_fade_v3') or full_stack.get('sunday_gap_fade_v3'))).get("reason") or "No weekend gap context.")
            ),
        ),
    ]


def _selected_pattern_overlay(
    candles: Sequence[Mapping[str, Any]],
    pattern: Mapping[str, Any],
    *,
    frame_id: int,
    pair: str,
    timeframe: str,
    selector_fingerprint: str,
    closed_candle_key: str,
    playbook: str,
    confidence: float,
    rule_ids: Sequence[str],
    chart_bounds: Sequence[float],
) -> dict[str, Any] | None:
    if not candles or not pattern.get("location_valid"):
        return None
    index = int(_number(pattern.get("selected_candle_index"), -1.0))
    if index < 0 or index >= len(candles):
        index = len(candles) - 1
    start_index = max(0, int(_number(pattern.get("pattern_start_index"), float(index))))
    pattern_end_index = min(
        len(candles) - 1,
        max(start_index, int(_number(pattern.get("pattern_end_index"), float(index)))),
    )
    confirmation_index = min(
        len(candles) - 1,
        max(pattern_end_index, int(_number(pattern.get("confirmation_index"), float(index)))),
    )
    geometry_end_index = max(pattern_end_index, confirmation_index)
    geometry: list[tuple[float, float, float]] = []
    for candle_index in range(start_index, geometry_end_index + 1):
        candle = _mapping(candles[candle_index])
        x = _number(candle.get("x", candle.get("center_x")), math.nan)
        top = _number(candle.get("top_y", candle.get("high_y")), math.nan)
        bottom = _number(candle.get("bottom_y", candle.get("low_y")), math.nan)
        if all(math.isfinite(item) for item in (x, top, bottom)):
            geometry.append((x, min(top, bottom), max(top, bottom)))
    if not geometry:
        return None
    xs = sorted(
        value for value in (
            _number(row.get("x", row.get("center_x")), math.nan) for row in candles
        ) if math.isfinite(value)
    )
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b > a]
    half_width = max(3.0, (sorted(gaps)[len(gaps) // 2] if gaps else 10.0) * 0.42)
    pattern_name = _safe_text(pattern.get("selected_pattern"), "Candle rule")
    overlay_bounds = [
        round(min(row[0] for row in geometry) - half_width, 6),
        round(min(row[1] for row in geometry), 6),
        round(max(row[0] for row in geometry) + half_width, 6),
        round(max(row[2] for row in geometry), 6),
    ]
    declared_chart_bounds = _bounds(chart_bounds)
    if declared_chart_bounds and (
        overlay_bounds[0] < declared_chart_bounds[0]
        or overlay_bounds[1] < declared_chart_bounds[1]
        or overlay_bounds[2] > declared_chart_bounds[2]
        or overlay_bounds[3] > declared_chart_bounds[3]
    ):
        return None
    return {
        "id": _stable_id("book_candle", pair, timeframe, closed_candle_key, index, pattern_name),
        "type": "BOOK_RULE_CANDLE", "layer": "book_rules", "group": "plan",
        "label": f"BOOK CANDLE: {pattern_name}", "label_hidden": False,
        "bounds": overlay_bounds,
        "points": [], "line_points": [], "side": str(pattern.get("side") or "NEUTRAL"),
        "confidence": confidence, "frame_id": frame_id,
        "coordinate_space": "chart", "coordinate_units": "pixels",
        "symbol": pair, "timeframe": timeframe,
        "market_selector_visual_fingerprint": selector_fingerprint,
        "instrument_identity_status": "LOCKED", "book_rule_ids": list(rule_ids),
        "book_playbook": playbook, "closed_candle_key": closed_candle_key,
        "pattern_start_index": start_index, "pattern_end_index": pattern_end_index,
        "confirmation_index": confirmation_index,
        "geometry_contract_accepted": True,
        "chart_bounds": declared_chart_bounds,
    }


def _book_overlays(
    *,
    trendlines: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    pattern: Mapping[str, Any],
    frame_id: int,
    pair: str,
    timeframe: str,
    selector_fingerprint: str,
    closed_candle_key: str,
    playbook: str,
    action_side: str,
    confidence: float,
    chart_bounds: Sequence[float],
    rule_ids: Sequence[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    fallback_chart_bounds = _bounds(chart_bounds)
    for index, source in enumerate(trendlines):
        line = _mapping(source)
        points = _point_pairs(line.get("line_points") or line.get("points"))
        anchors = _point_pairs(line.get("anchor_wick_points"))
        declared_bounds = _bounds(line.get("chart_bounds")) or fallback_chart_bounds
        touch_count = int(_number(line.get("touch_count")))
        if (
            line.get("geometry_contract_accepted") is not True
            or len(points) < 2
            or len(anchors) < 2
            or anchors[0] == anchors[1]
            or touch_count < 3
            or not declared_bounds
        ):
            continue
        role = _safe_text(
            line.get("role") or line.get("trendline_role") or line.get("type"),
            "TRENDLINE",
        ).upper()
        line_bounds = _bounds(line.get("bounds")) or _bounds([
            min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points),
        ])
        output.append({
            "id": _stable_id("book_line", pair, timeframe, closed_candle_key, role, anchors[:2], index),
            "type": "BOOK_RULE_LINE", "layer": "book_rules", "group": "plan",
            "label": f"BOOK {role.replace('_TRENDLINE', '')} {touch_count}T - {playbook.replace('_', ' ')}",
            "label_hidden": False, "bounds": line_bounds,
            "points": _point_pairs(line.get("touch_points")), "line_points": points,
            "anchor_wick_points": anchors, "chart_bounds": declared_bounds,
            "geometry_contract_accepted": True,
            "geometry_status": _safe_text(line.get("geometry_status"), "ANCHORS_VALID_BOOK_RULE"),
            "touch_count": touch_count, "side": _side(line.get("direction"), role),
            "confidence": _number(line.get("confidence"), confidence), "frame_id": frame_id,
            "coordinate_space": "chart", "coordinate_units": "pixels",
            "symbol": pair, "timeframe": timeframe,
            "market_selector_visual_fingerprint": selector_fingerprint,
            "instrument_identity_status": "LOCKED", "book_rule_ids": list(rule_ids),
            "book_playbook": playbook, "book_action_side": action_side,
            "closed_candle_key": closed_candle_key,
        })
    for index, source in enumerate(zones):
        zone = _mapping(source)
        zone_bounds = _bounds(zone.get("bbox") or zone.get("bounds"))
        if not zone_bounds or zone.get("still_significant") is False:
            continue
        if fallback_chart_bounds and (
            zone_bounds[0] < fallback_chart_bounds[0]
            or zone_bounds[1] < fallback_chart_bounds[1]
            or zone_bounds[2] > fallback_chart_bounds[2]
            or zone_bounds[3] > fallback_chart_bounds[3]
        ):
            continue
        if not (
            zone.get("entry_authority_allowed") is True
            or zone.get("nearest") is True
            or zone.get("role_flip_confirmed") is True
        ):
            continue
        role = _safe_text(zone.get("role") or zone.get("zone_family"), "REACTION ZONE").upper()
        grade = _safe_text(zone.get("quality_grade"))
        touches = int(_number(zone.get("touch_count")))
        label = f"BOOK {role} - {touches}T" + (f" - {grade}" if grade else "")
        output.append({
            "id": _stable_id("book_zone", pair, timeframe, closed_candle_key, zone.get("key"), index),
            "type": "BOOK_RULE_ZONE", "layer": "book_rules", "group": "plan",
            "label": label, "label_hidden": False, "bounds": zone_bounds,
            "points": _point_pairs(zone.get("touch_points")), "line_points": [],
            "side": _side(zone.get("direction"), role),
            "confidence": _number(zone.get("confidence"), confidence), "frame_id": frame_id,
            "coordinate_space": "chart", "coordinate_units": "pixels",
            "symbol": pair, "timeframe": timeframe,
            "market_selector_visual_fingerprint": selector_fingerprint,
            "instrument_identity_status": "LOCKED", "book_rule_ids": list(rule_ids),
            "book_playbook": playbook, "book_action_side": action_side,
            "closed_candle_key": closed_candle_key,
            "geometry_contract_accepted": True,
            "chart_bounds": fallback_chart_bounds,
        })
    candle_overlay = _selected_pattern_overlay(
        candles, pattern, frame_id=frame_id, pair=pair, timeframe=timeframe,
        selector_fingerprint=selector_fingerprint, closed_candle_key=closed_candle_key,
        playbook=playbook, confidence=confidence, rule_ids=rule_ids,
        chart_bounds=chart_bounds,
    )
    if candle_overlay:
        output.append(candle_overlay)
    return output


def build_book_rule_action_signal_v3(
    *,
    control: Mapping[str, Any] | None,
    candles: Sequence[Mapping[str, Any]] = (),
    trendlines: Sequence[Mapping[str, Any]] = (),
    support_resistance_zones: Sequence[Mapping[str, Any]] = (),
    pair: str,
    timeframe: str,
    frame_id: int,
    closed_candle_key: str = "",
    closed_candle_sequence: int = 0,
    market_selector_visual_fingerprint: str = "",
    chart_bounds: Sequence[float] = (),
    identity_confirmed: bool = True,
) -> dict[str, Any]:
    """Build a high-priority, horizon-free book action and its overlays."""

    source = _mapping(control)
    normalized_pair = _safe_text(pair, "UNKNOWN")
    normalized_timeframe = _safe_text(timeframe, "UNKNOWN").upper()
    lineage = {
        "frame_id": int(frame_id),
        "closed_candle_key": _safe_text(closed_candle_key),
        "closed_candle_sequence": max(0, int(closed_candle_sequence)),
        "pair": normalized_pair,
        "timeframe": normalized_timeframe,
        "market_selector_visual_fingerprint": _safe_text(market_selector_visual_fingerprint),
    }
    if not source or not identity_confirmed:
        return {
            "schema_version": BOOK_RULE_ACTION_SIGNAL_SCHEMA_V3,
            "version": 1, "provider_role": "PRIMARY_STRATEGIST_SIGNAL_PROVIDER",
            "priority": "HIGH",
            "status": "SOURCE_IDENTITY_PENDING" if not identity_confirmed else "WAITING_FOR_CLOSED_CANDLE_RULE_STUDY",
            "action": "WAIT", "watch_side": "NEUTRAL", "actionable": False,
            "scenario": "Waiting for an identity-locked closed-candle book study.",
            "trigger": "Complete pair and timeframe identity, then evaluate the next closed candle.",
            "invalidation": "No rule action exists without closed-candle lineage.",
            "strategy_report": [], "overlays": [], "overlay_count": 0,
            "technical_indicators_used": False, "horizon_published": False,
            "execution_authority": False, **lineage,
        }

    current_rule = select_current_book_action_v3(
        source,
        market_geometry=_market_geometry(candles),
    )
    immediate_side = _side(current_rule.get("watch_side"))
    confidence = max(0.0, min(1.0, _number(current_rule.get("evidence_strength"))))
    alignment_margin = _number(
        _mapping(current_rule.get("directional_alignment")).get("margin"),
        0.0,
    )
    margin = min(1.0, alignment_margin / 6.0) if alignment_margin > 0.0 else 0.0
    playbook = _safe_text(current_rule.get("playbook"), "UNRESOLVED").upper()
    candle = _pattern_summary(source)
    strategies = _strategy_report(source, candle)
    active_strategies = [row for row in strategies if row["status"] == "ACTIVE"]
    watching_strategies = [row for row in strategies if row["status"] == "WATCHING"]
    resolution_by_family = {
        str(row.get("strategy_id")): dict(row)
        for row in current_rule.get("family_resolutions") or []
        if isinstance(row, Mapping) and row.get("strategy_id")
    }
    for row in strategies:
        resolved = resolution_by_family.get(str(row.get("strategy_id")))
        if resolved:
            row["resolved_playbook"] = _safe_text(resolved.get("playbook"))
            row["resolution"] = _safe_text(resolved.get("resolution"), "WATCHING")
            row["resolution_reason"] = _safe_text(resolved.get("reason"))
    action = _safe_text(current_rule.get("action"), "WAIT").upper()
    status = _safe_text(current_rule.get("status"), "WAITING_FOR_CURRENT_BOOK_TRIGGER").upper()
    if action not in {"BUY", "SELL"}:
        action = "WAIT"
    opposing_force = bool(current_rule.get("opposing_force_conflict"))
    conflicted = opposing_force

    trigger_by_playbook = {
        "ROLE_FLIP_RETEST": "Require the flipped level to hold on a completed retest and rejection close.",
        "STOP_HUNT_BMS_RTO": "Require the stop-hunt, BMS and return-to-origin sequence to finish on a completed candle.",
        "BMS_OTE_RTO": "Require BMS alignment and a completed OTE return-to-origin reaction.",
        "TRENDLINE_REJECTION": "Require a completed wick rejection at the mature three-touch line without a body breach.",
        "BREAK_RETEST": "Require the broken line or zone to hold its new role on the completed retest.",
        "LIQUIDITY_SWEEP_RECLAIM": "Require the sweep to reclaim the level on a completed candle.",
        "SUPPORT_RESISTANCE_REJECTION": "Require a completed rejection of the exact support/resistance zone.",
        "ORDER_BLOCK_RTO": "Require the return into the last opposing candle that caused BMS to hold.",
        "TURTLE_SOUP_SH_BMS_RTO": "Require the sweep, reclaim, confirming BMS, and return chain to complete in order.",
        "STRUCTURE_CONTINUATION": "Require the completed structure break to hold without an opposing close.",
        "AMD_DISTRIBUTION": "Require accumulation, opposite-side manipulation, and a distribution close.",
        "POST_NEWS_PIVOT": "Require the post-news pivot and midpoint confirmation close.",
        "SAKATA_METHOD": "Require the Sakata cycle formation to complete with a confirming close.",
        "CANDLE_REVERSAL_AT_STRUCTURE": "Require the location-valid candle pattern to close at confirmed structure.",
        "CANDLE_CONTINUATION_AT_STRUCTURE": "Require the location-valid continuation pattern to close at confirmed structure.",
        "SUNDAY_GAP_FADE": "Require weekend-gap context and fade the first-hour retrace toward the pre-weekend close.",
        "RANGE_REACTION": "Require completed rejection from the active range boundary.",
    }
    trigger = _safe_text(
        current_rule.get("trigger"),
        trigger_by_playbook.get(playbook, "Wait for one complete current book setup."),
    )
    hlz = _mapping(source.get("hlz_sequence_v3") or source.get("hlz_sequence"))
    role_flip = _mapping(source.get("role_flip_sequence_v3") or source.get("role_flip"))
    location = "active structure"
    if _first_truth(hlz, "entry_sequence_ready", "complete"):
        location = "the completed HLZ sequence"
    elif _first_truth(role_flip, "complete", "confirmed"):
        location = "the confirmed role-flip retest"
    elif _number(_mapping(source.get("zone_context")).get("active_zone_count")) > 0:
        location = "the active supply/demand reaction area"
    scenario = _safe_text(
        current_rule.get("scenario"),
        "No complete directional book setup is aligned on the latest closed candle.",
    )

    full_stack = _mapping(source.get("full_non_indicator_stack_v3"))
    structure_full = _mapping(source.get("market_structure_full_v3") or full_stack.get("market_structure"))
    trendline_full = _mapping(
        source.get("trendline_contracts_full_v3")
        or source.get("trendline_contracts")
        or full_stack.get("trendline_contracts")
    )
    bms_events = _rows(structure_full.get("bms_events"))
    sms_events = _rows(structure_full.get("sms_events"))
    trace_rows = _rows(source.get("rule_trace"))
    selected_rule_ids = {
        _safe_text(value) for value in list(current_rule.get("rule_ids") or []) if _safe_text(value)
    }
    active_trace = [
        row for row in trace_rows
        if _truthy(row.get("observed"))
        and _number(row.get("weight")) > 0.0
        and (
            not selected_rule_ids
            or _safe_text(row.get("rule_id") or row.get("rule") or row.get("name")) in selected_rule_ids
        )
    ]
    rule_ids = sorted(selected_rule_ids) or [
        _safe_text(row.get("rule_id") or row.get("rule") or row.get("name"), f"RULE_{index + 1}")
        for index, row in enumerate(active_trace)
    ]
    selected_provenance = _rows(current_rule.get("provenance"))
    traceability_rows = [*active_trace, *selected_provenance]
    book_traceability_rows = [
        row
        for row in traceability_rows
        if row.get("book_page_required") is not False
    ]
    internal_evidence_rows = [
        row
        for row in traceability_rows
        if row.get("book_page_required") is False
    ]
    selected_book_rule_ids = sorted(
        {
            _safe_text(row.get("rule_id") or row.get("rule") or row.get("name"))
            for row in book_traceability_rows
            if _safe_text(row.get("rule_id") or row.get("rule") or row.get("name"))
        }
    )
    selected_internal_evidence_ids = sorted(
        {
            _safe_text(row.get("rule_id") or row.get("rule") or row.get("name"))
            for row in internal_evidence_rows
            if _safe_text(row.get("rule_id") or row.get("rule") or row.get("name"))
        }
    )
    page_references = sorted({
        f"{_safe_text(row.get('source_file'), 'book')}:{page}"
        for row in book_traceability_rows
        for page in list(row.get("pdf_pages") or [])
    })
    strict_lines = _rows(trendline_full.get("contracts"))
    strict_zones = _rows(
        _mapping(trendline_full.get("support_resistance_contracts")).get("active_contracts")
    )
    overlay_lines = [
        row for row in strict_lines
        if row.get("strict_strategy_valid")
    ]
    overlay_zones = [
        row for row in strict_zones
        if row.get("entry_authority_allowed")
        or row.get("nearest")
        or row.get("role_flip_confirmed")
    ]
    overlays = _book_overlays(
        trendlines=overlay_lines, zones=overlay_zones, candles=candles,
        pattern=candle, frame_id=int(frame_id), pair=normalized_pair,
        timeframe=normalized_timeframe,
        selector_fingerprint=_safe_text(market_selector_visual_fingerprint),
        closed_candle_key=_safe_text(closed_candle_key), playbook=playbook,
        action_side=immediate_side, confidence=confidence,
        chart_bounds=chart_bounds, rule_ids=rule_ids,
    )
    opposing_target = _mapping(_mapping(
        source.get("opposing_force_targets_v3")
        or source.get("opposing_targets")
        or full_stack.get("opposing_targets")
    ).get(immediate_side))
    opposing_summary = (
        _safe_text(
            opposing_target.get("label") or opposing_target.get("role")
            or opposing_target.get("target_type"),
            "A current opposing book reaction conflicts with this side",
        )
        if opposing_force
        else "No stronger opposing book force is confirmed at the current rule location"
    )
    return {
        "schema_version": BOOK_RULE_ACTION_SIGNAL_SCHEMA_V3,
        "version": 1, "provider_role": "PRIMARY_STRATEGIST_SIGNAL_PROVIDER",
        "priority": "HIGH", "status": status, "action": action,
        "watch_side": immediate_side, "actionable": action in {"BUY", "SELL"},
        "confidence": round(confidence, 6),
        "confidence_percent": round(confidence * 100.0, 1),
        "score_margin": round(margin, 6), "playbook": playbook,
        "playbook_family": _safe_text(current_rule.get("playbook_family")),
        "resolution": _safe_text(current_rule.get("resolution"), "WATCHING"),
        "blocked_reasons": [
            _safe_text(row) for row in (current_rule.get("blocked_reasons") or []) if _safe_text(row)
        ],
        "advisories": [
            _safe_text(row) for row in (current_rule.get("advisories") or []) if _safe_text(row)
        ],
        "entry_window_candles": int(_number(current_rule.get("entry_window_candles"), 0)),
        "profit_room": _mapping(current_rule.get("profit_room")),
        "stop_plan": _mapping(current_rule.get("stop_plan")),
        "regime": _safe_text(current_rule.get("regime"), "UNCLASSIFIED"),
        "regime_notes": [
            _safe_text(row) for row in (current_rule.get("regime_notes") or []) if _safe_text(row)
        ],
        "directional_alignment": _mapping(current_rule.get("directional_alignment")),
        "family_resolutions": [
            dict(row) for row in current_rule.get("family_resolutions") or [] if isinstance(row, Mapping)
        ],
        "entry_profile": _safe_text(current_rule.get("profile"), "NONE").upper(),
        "entry_profiles": _mapping(current_rule.get("entry_profiles")),
        "confluence_count": int(_number(current_rule.get("confluence_count"))),
        "scenario": scenario, "trigger": trigger,
        "invalidation": _safe_text(
            current_rule.get("invalidation") or source.get("invalidation"),
            "A completed opposing structure break invalidates the current book scenario.",
        ),
        "opposing_force": {
            "present": opposing_force, "conflicted": conflicted,
            "summary": opposing_summary,
        },
        "structure": {
            "major_side": _side(source.get("major_structure_side")),
            "inner_side": _side(source.get("inner_structure_side")),
            "bms_event_count": len(bms_events), "sms_event_count": len(sms_events),
        },
        "strict_trendlines": {
            "valid_count": int(_number(trendline_full.get("valid_count"))),
            "outer_valid_count": int(_number(trendline_full.get("outer_valid_count"))),
            "false_breach_redraw_count": int(_number(trendline_full.get("false_breach_redraw_count"))),
            "current_touch": bool(
                _rows(trendline_full.get("current_reactions"))
                or _rows(trendline_full.get("current_role_flip_retests"))
            ),
            "geometry_contract": "TWO_WICK_ANCHORS_THIRD_TOUCH_NO_BODY_BREACH",
        },
        "hlz": {
            "entry_sequence_ready": bool(hlz.get("entry_sequence_ready")),
            "stop_hunt": bool(hlz.get("stop_hunt")), "bms": bool(hlz.get("bms")),
            "rto": bool(hlz.get("rto")),
        },
        "candlestick": candle, "strategy_report": strategies,
        "strategy_family_count": len(strategies),
        "active_strategy_count": len(active_strategies),
        "watching_strategy_count": len(watching_strategies),
        "active_strategy_ids": [row["strategy_id"] for row in active_strategies],
        "rule_traceability": {
            "complete": bool(selected_book_rule_ids) and all(
                bool(row.get("source_file")) and bool(row.get("pdf_pages"))
                for row in book_traceability_rows
            ),
            "evaluated_rule_count": len(trace_rows), "active_rule_count": len(active_trace),
            "book_trace_count": len(book_traceability_rows),
            "internal_evidence_count": len(internal_evidence_rows),
            "book_page_reference_count": len(page_references),
            "book_page_references": page_references,
            "selected_rule_ids": rule_ids,
            "selected_book_rule_ids": selected_book_rule_ids,
            "selected_internal_evidence_ids": selected_internal_evidence_ids,
            "selected_provenance": selected_provenance,
        },
        "overlays": overlays, "overlay_count": len(overlays),
        "overlay_contract": "EXACT_CURRENT_CHART_PIXELS_NO_FLOATING_GEOMETRY",
        "technical_indicators_used": False,
        "technical_indicator_scope": "EXCLUDED_BY_USER",
        "horizon_published": False, "execution_authority": False,
        "current_scenario_only": True,
        "contract_schemas": {
            "context": BOOK_STRATEGY_CONTEXT_SCHEMA_V3,
            "full_stack": FULL_BOOK_STACK_SCHEMA_V3,
            "candlestick_catalog": CANDLESTICK_CATALOG_SCHEMA_V3,
        },
        "source_engine_schema": _safe_text(source.get("schema")), **lineage,
    }


__all__ = [
    "BOOK_RULE_ACTION_SIGNAL_SCHEMA_V3",
    "build_book_rule_action_signal_v3",
]
