"""Complete non-indicator book-rule authority for Phoenix Guard V3.

Only visible closed candles, screenshot geometry, pair-specific memory, and
explicit session/news context are accepted. No technical indicator is derived
or consulted here.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any


FULL_BOOK_STACK_SCHEMA_V3 = "PG_FULL_NON_INDICATOR_BOOK_STACK_V3"
_HLZ_FILE = "HLZ - Market Structure And Powerful Setups.pdf"
_TRENDLINE_FILE = "secrets revealed $10 000 cost price-1-1.pdf"
_CANDLE_FILE = (
    "The power of Japanese candlestick charts _ advanced filtering techniques "
    "for trading stocks, futures and Forex ( PDFDrive ).pdf"
)
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
        if str(value or "").strip().upper() in {
            "TRUE",
            "YES",
            "ACTIVE",
            "CONFIRMED",
            "COMPLETE",
            "HELD",
            "VALID",
        }:
            return True
    return False


def _stable_id(kind: str, *parts: Any) -> str:
    payload = "|".join([kind, *(str(part) for part in parts)])
    return f"{kind}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def _trace(
    rule_id: str,
    side: str,
    weight: float,
    reason: str,
    *,
    source_file: str,
    pdf_pages: Sequence[int],
    section: str,
    observed: bool = True,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "side": side,
        "weight": round(float(weight), 6),
        "observed": observed,
        "reason": reason,
        "source_book": source_file.removesuffix(".pdf"),
        "source_file": source_file,
        "source_section": section,
        "printed_pages": list(pdf_pages),
        "pdf_pages": list(pdf_pages),
    }


def _body(row: Mapping[str, Any]) -> float:
    return abs(_number(row.get("close")) - _number(row.get("open")))


def _spread(row: Mapping[str, Any]) -> float:
    return max(1e-9, _number(row.get("high")) - _number(row.get("low")))


def _direction(row: Mapping[str, Any]) -> str:
    return "BUY" if _number(row.get("close")) > _number(row.get("open")) else "SELL" if _number(row.get("close")) < _number(row.get("open")) else "NEUTRAL"


def _point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        x_value = _number(value.get("x_px", value.get("x", value.get("x_center_px"))), math.nan)
        y_value = _number(value.get("y_px", value.get("y", value.get("wick_y_px"))), math.nan)
        return (x_value, y_value) if math.isfinite(x_value) and math.isfinite(y_value) else None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) >= 2:
        x_value = _number(value[0], math.nan)
        y_value = _number(value[1], math.nan)
        return (x_value, y_value) if math.isfinite(x_value) and math.isfinite(y_value) else None
    return None


def _line_points(line: Mapping[str, Any]) -> list[tuple[float, float]]:
    raw = line.get("anchor_wick_points") or line.get("line_points") or line.get("points") or []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [parsed for value in raw if (parsed := _point(value)) is not None]


def _line_y(points: Sequence[tuple[float, float]], x_value: float) -> float | None:
    if len(points) < 2 or abs(points[1][0] - points[0][0]) <= 1e-9:
        return None
    x1, y1 = points[0]
    x2, y2 = points[1]
    return y1 + ((y2 - y1) / (x2 - x1)) * (x_value - x1)


def _pivots(
    candles: Sequence[Mapping[str, Any]],
    radius: int,
    tier: str,
) -> list[dict[str, Any]]:
    pivots: list[dict[str, Any]] = []
    for index in range(radius, len(candles) - radius):
        row = candles[index]
        neighbors = candles[index - radius : index] + candles[index + 1 : index + radius + 1]
        high = _number(row.get("high"))
        low = _number(row.get("low"))
        if all(high > _number(other.get("high")) for other in neighbors):
            pivots.append(
                {
                    "pivot_id": _stable_id("PH", tier, index, round(high, 8)),
                    "index": index,
                    "kind": "HIGH",
                    "price": high,
                    "x": _number(row.get("x"), float(index)),
                    "tier": tier,
                    "radius": radius,
                    "confirmed_at_index": index + radius,
                }
            )
        if all(low < _number(other.get("low")) for other in neighbors):
            pivots.append(
                {
                    "pivot_id": _stable_id("PL", tier, index, round(low, 8)),
                    "index": index,
                    "kind": "LOW",
                    "price": low,
                    "x": _number(row.get("x"), float(index)),
                    "tier": tier,
                    "radius": radius,
                    "confirmed_at_index": index + radius,
                }
            )
    return pivots


def _structure_hierarchy(candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    minor = _pivots(candles, 2, "INTERNAL") if len(candles) >= 5 else []
    intermediate = _pivots(candles, 3, "INTERMEDIATE") if len(candles) >= 7 else []
    external = _pivots(candles, 5, "EXTERNAL") if len(candles) >= 11 else []
    all_pivots = [*minor, *intermediate, *external]
    events: list[dict[str, Any]] = []
    for index in range(4, len(candles)):
        eligible = [pivot for pivot in all_pivots if int(pivot["confirmed_at_index"]) < index]
        prior_highs = [pivot for pivot in eligible if pivot["kind"] == "HIGH"]
        prior_lows = [pivot for pivot in eligible if pivot["kind"] == "LOW"]
        close = _number(candles[index].get("close"))
        high_pivot = max(prior_highs, key=lambda row: int(row["index"]), default=None)
        low_pivot = max(prior_lows, key=lambda row: int(row["index"]), default=None)
        broken = high_pivot if high_pivot and close > float(high_pivot["price"]) else low_pivot if low_pivot and close < float(low_pivot["price"]) else None
        if broken is None:
            continue
        side = "BUY" if broken["kind"] == "HIGH" else "SELL"
        event_id = _stable_id("BMS", side, index, broken["pivot_id"])
        if events and events[-1]["side"] == side and events[-1]["broken_pivot_id"] == broken["pivot_id"]:
            continue
        events.append(
            {
                "event_id": event_id,
                "event": "EXTERNAL_BMS" if broken["tier"] == "EXTERNAL" else "INTERNAL_BMS",
                "side": side,
                "index": index,
                "close": close,
                "broken_level": broken["price"],
                "broken_pivot_id": broken["pivot_id"],
                "structure_tier": broken["tier"],
                "completed_close_confirmed": True,
            }
        )
    latest_side = str(events[-1]["side"]) if events else "NEUTRAL"
    protected = None
    if events:
        event = events[-1]
        desired_kind = "LOW" if latest_side == "BUY" else "HIGH"
        protected = max(
            [pivot for pivot in all_pivots if pivot["kind"] == desired_kind and int(pivot["index"]) < int(event["index"])],
            key=lambda row: int(row["index"]),
            default=None,
        )
    sms_events: list[dict[str, Any]] = []
    for previous, current in zip(events, events[1:]):
        if previous["side"] != current["side"]:
            sms_events.append(
                {
                    "event_id": _stable_id("SMS", previous["event_id"], current["event_id"]),
                    "event": "SMS_CONFIRMED_BY_OPPOSING_BMS",
                    "side": current["side"],
                    "failed_side": previous["side"],
                    "confirming_bms_id": current["event_id"],
                    "index": current["index"],
                }
            )
    return {
        "internal_pivots": minor,
        "intermediate_pivots": intermediate,
        "external_pivots": external,
        "bms_events": events,
        "sms_events": sms_events,
        "latest_bms": events[-1] if events else {},
        "latest_sms": sms_events[-1] if sms_events else {},
        "protected_swing": protected or {},
        "structure_side": latest_side,
        "stable_lineage": True,
    }


def _anchor_is_significant(
    anchor: tuple[float, float],
    role: str,
    candles: Sequence[Mapping[str, Any]],
    pivots: Sequence[Mapping[str, Any]],
) -> bool:
    if not candles:
        return False
    x_values = [_number(row.get("x"), float(index)) for index, row in enumerate(candles)]
    index = min(range(len(x_values)), key=lambda candidate: abs(x_values[candidate] - anchor[0]))
    required_kind = "LOW" if role == "BUY" else "HIGH"
    return any(
        pivot.get("kind") == required_kind
        and int(pivot.get("index", -999)) == index
        and str(pivot.get("tier")) in {"INTERNAL", "INTERMEDIATE", "EXTERNAL"}
        for pivot in pivots
    )


def _zone_location_history(
    candles: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Attach visible support/resistance geometry to each historical candle."""
    history: dict[str, list[dict[str, Any]]] = {str(index): [] for index in range(len(candles))}
    for zone_index, raw_zone in enumerate(_rows(zones)):
        zone = dict(raw_zone)
        top_value = zone.get("top_y", zone.get("y1", zone.get("price_high_y_px")))
        bottom_value = zone.get("bottom_y", zone.get("y2", zone.get("price_low_y_px")))
        if top_value is None or bottom_value is None:
            continue
        zone_top, zone_bottom = sorted((_number(top_value), _number(bottom_value)))
        left_value = zone.get("left_x", zone.get("x1", zone.get("start_x")))
        right_value = zone.get("right_x", zone.get("x2", zone.get("end_x")))
        left_x = _number(left_value, float("-inf")) if left_value is not None else float("-inf")
        right_x = _number(right_value, float("inf")) if right_value is not None else float("inf")
        left_x, right_x = min(left_x, right_x), max(left_x, right_x)
        role_side = _side(zone.get("role") or zone.get("zone_role") or zone.get("type") or zone.get("label"))
        zone_id = str(zone.get("zone_id") or zone.get("id") or f"zone-{zone_index}")
        for candle_index, candle in enumerate(candles):
            x_value = _number(candle.get("x"), float(candle_index))
            top = candle.get("top_y")
            bottom = candle.get("bottom_y")
            if top is None or bottom is None or not left_x <= x_value <= right_x:
                continue
            candle_top, candle_bottom = sorted((_number(top), _number(bottom)))
            if candle_bottom < zone_top or candle_top > zone_bottom:
                continue
            history[str(candle_index)].append(
                {
                    "zone_id": zone_id,
                    "role_side": role_side,
                    "zone_top_y_px": zone_top,
                    "zone_bottom_y_px": zone_bottom,
                }
            )
    return history


def _trendline_contracts(
    candles: Sequence[Mapping[str, Any]],
    trendlines: Sequence[Mapping[str, Any]],
    support_resistance_zones: Sequence[Mapping[str, Any]],
    structure: Mapping[str, Any],
    timeframe: str,
) -> dict[str, Any]:
    pivots = [
        *_rows(structure.get("internal_pivots")),
        *_rows(structure.get("intermediate_pivots")),
        *_rows(structure.get("external_pivots")),
    ]
    current_seconds = _TIMEFRAME_SECONDS.get(str(timeframe or "M5").upper(), 300)
    contracts: list[dict[str, Any]] = []
    location_history: dict[str, str] = {}
    for raw in _rows(trendlines):
        line = dict(raw)
        points = _line_points(line)
        role = _side(line.get("role") or line.get("trendline_role") or line.get("type") or line.get("label"))
        touch_count = int(_number(line.get("touch_count"), float(len(_rows(line.get("touch_points"))))))
        accepted = line.get("geometry_contract_accepted") is True or line.get("accepted") is True
        anchors_significant = len(points) >= 2 and all(
            _anchor_is_significant(anchor, role, candles, pivots) for anchor in points[:2]
        )
        touch_indices = {int(_number(value, -1)) for value in line.get("touch_candle_indices", []) if not isinstance(value, bool)} if isinstance(line.get("touch_candle_indices"), Sequence) else set()
        obstruction_indices: list[int] = []
        if len(points) >= 2:
            left_x, right_x = sorted((points[0][0], points[1][0]))
            for index, candle in enumerate(candles):
                x_value = _number(candle.get("x"), float(index))
                open_y = candle.get("open_y")
                close_y = candle.get("close_y")
                if not (left_x < x_value < right_x) or index in touch_indices or open_y is None or close_y is None:
                    continue
                projected_y = _line_y(points, x_value)
                if projected_y is None:
                    continue
                body_top, body_bottom = sorted((_number(open_y), _number(close_y)))
                if body_top + 0.75 < projected_y < body_bottom - 0.75:
                    obstruction_indices.append(index)
        line_timeframe = str(line.get("timeframe") or line.get("source_timeframe") or timeframe).upper()
        line_seconds = _TIMEFRAME_SECONDS.get(line_timeframe, current_seconds)
        outer = "OUTER" in str(line.get("role") or line.get("kind") or "").upper() or line_seconds > current_seconds
        authority_multiplier = 1.6 if outer else 1.0 if line_seconds == current_seconds else 0.65
        mature = accepted and touch_count >= 3 and anchors_significant and not obstruction_indices
        latest_relation = "UNOBSERVED"
        close_through = False
        reclaimed = False
        if mature and points and candles and candles[-1].get("close_y") is not None:
            latest = candles[-1]
            projected_y = _line_y(points, _number(latest.get("x"), float(len(candles) - 1)))
            if projected_y is not None:
                close_y = _number(latest.get("close_y"))
                close_through = (role == "SELL" and close_y < projected_y - 2.0) or (role == "BUY" and close_y > projected_y + 2.0)
                latest_relation = "CLOSED_THROUGH" if close_through else "DEFENDING_SIDE"
                if len(candles) >= 2 and candles[-2].get("close_y") is not None:
                    previous_y = _line_y(points, _number(candles[-2].get("x"), float(len(candles) - 2)))
                    previous_close = _number(candles[-2].get("close_y"))
                    previous_through = previous_y is not None and ((role == "SELL" and previous_close < previous_y - 2.0) or (role == "BUY" and previous_close > previous_y + 2.0))
                    reclaimed = previous_through and not close_through
        lifecycle = "ACTIVE" if mature else "CANDIDATE_REJECTED"
        if mature and close_through:
            lifecycle = "INVALIDATED_BY_COMPLETED_CLOSE"
        elif mature and reclaimed:
            lifecycle = "FALSE_BREACH_RECLAIMED_KEEP_AND_REDRAW"
        replacement_trendline = None
        if lifecycle == "FALSE_BREACH_RECLAIMED_KEEP_AND_REDRAW":
            required_kind = "HIGH" if role == "SELL" else "LOW"
            replacement_pivots = sorted(
                [pivot for pivot in pivots if str(pivot.get("kind") or "").upper() == required_kind],
                key=lambda pivot: int(_number(pivot.get("index"), -1)),
            )
            if len(replacement_pivots) >= 2:
                replacement_anchors = replacement_pivots[-2:]
                replacement_points = [
                    [
                        _number(pivot.get("x"), float(_number(pivot.get("index"), 0.0))),
                        _number(pivot.get("y")),
                    ]
                    for pivot in replacement_anchors
                ]
                source_id = str(line.get("trendline_id") or line.get("id") or f"trendline-{len(contracts)}")
                replacement_trendline = {
                    "trendline_id": f"{source_id}:false-breach-redraw",
                    "replaces_trendline_id": source_id,
                    "role_side": role,
                    "line_points_v3": replacement_points,
                    "anchor_pivot_ids": [
                        str(pivot.get("pivot_id") or f"pivot-{int(_number(pivot.get('index'), -1))}")
                        for pivot in replacement_anchors
                    ],
                    "lifecycle_state": "REDRAW_CANDIDATE_AFTER_FALSE_BREACH",
                    "strict_strategy_valid": False,
                    "future_blind": True,
                }
        contract = {
            **line,
            "strict_strategy_valid": mature,
            "three_touch_confirmed": touch_count >= 3,
            "significant_anchor_pair_confirmed": anchors_significant,
            "body_obstruction_count": len(obstruction_indices),
            "body_obstruction_indices": obstruction_indices,
            "line_timeframe": line_timeframe,
            "outer_trendline": outer,
            "authority_multiplier": authority_multiplier,
            "lifecycle_state": lifecycle,
            "latest_relation": latest_relation,
            "false_breach_reclaimed": reclaimed,
            "keep_original_after_false_breach": reclaimed,
            "replacement_trendline": replacement_trendline,
            "role_side": role,
            "line_points_v3": [[x, y] for x, y in points],
        }
        contracts.append(contract)
        if mature and points:
            for index, candle in enumerate(candles):
                if candle.get("top_y") is None or candle.get("bottom_y") is None:
                    continue
                projected_y = _line_y(points, _number(candle.get("x"), float(index)))
                if projected_y is None:
                    continue
                top = _number(candle.get("top_y"))
                bottom = _number(candle.get("bottom_y"))
                if top - 2.0 <= projected_y <= bottom + 2.0:
                    location_history[str(index)] = role
    zone_location_history = _zone_location_history(candles, support_resistance_zones)
    for candle_index, zone_touches in zone_location_history.items():
        sides = {str(row.get("role_side") or "NEUTRAL") for row in zone_touches}
        sides.discard("NEUTRAL")
        trendline_side = location_history.get(candle_index)
        if trendline_side and sides and trendline_side not in sides:
            location_history[candle_index] = "NEUTRAL"
        elif not trendline_side and len(sides) == 1:
            location_history[candle_index] = next(iter(sides))
    valid = [row for row in contracts if row["strict_strategy_valid"]]
    target_by_forecast_side: dict[str, Any] = {"BUY": None, "SELL": None}
    if candles:
        latest_x = _number(candles[-1].get("x"), float(len(candles) - 1))
        x_values = [_number(candle.get("x"), float(index)) for index, candle in enumerate(candles)]
        x_steps = sorted(
            step for step in (x_values[index] - x_values[index - 1] for index in range(1, len(x_values))) if step > 0
        )
        candle_spacing = x_steps[len(x_steps) // 2] if x_steps else 1.0
        horizon_72_x = latest_x + (72.0 * candle_spacing)
        for forecast_side, opposing_role in (("BUY", "SELL"), ("SELL", "BUY")):
            candidates = []
            for line in valid:
                if line["role_side"] != opposing_role:
                    continue
                projected_y = _line_y([tuple(point) for point in line["line_points_v3"]], latest_x)
                if projected_y is not None:
                    candidates.append((abs(projected_y - _number(candles[-1].get("close_y"))), line, projected_y))
            if candidates:
                _, selected, projected_y = min(candidates, key=lambda row: row[0])
                target_by_forecast_side[forecast_side] = {
                    "source": "OPPOSING_TRENDLINE",
                    "line_id": str(selected.get("trendline_id") or selected.get("id") or ""),
                    "target_y_px": round(projected_y, 6),
                    "target_y_px_at_horizon_72": round(
                        _line_y([tuple(point) for point in selected["line_points_v3"]], horizon_72_x),
                        6,
                    ),
                    "horizon_72_x_px": round(horizon_72_x, 6),
                    "intersection_semantics": "PROJECTED_LINE_INTERSECTION_NOT_GUARANTEED_PRICE",
                    "authority_multiplier": selected["authority_multiplier"],
                }
    return {
        "contracts": contracts,
        "valid_contracts": valid,
        "valid_count": len(valid),
        "outer_valid_count": sum(bool(row["outer_trendline"]) for row in valid),
        "false_breach_redraw_count": sum(row.get("replacement_trendline") is not None for row in contracts),
        "candle_location_history": location_history,
        "candle_zone_location_history": zone_location_history,
        "historical_zone_binding_complete": len(zone_location_history) == len(candles),
        "opposing_targets": target_by_forecast_side,
    }


def _order_blocks(
    candles: Sequence[Mapping[str, Any]],
    structure: Mapping[str, Any],
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for event in _rows(structure.get("bms_events")):
        event_index = int(_number(event.get("index"), -1))
        side = str(event.get("side") or "NEUTRAL")
        if event_index <= 0 or side not in {"BUY", "SELL"}:
            continue
        desired_origin = "SELL" if side == "BUY" else "BUY"
        origin_index = next(
            (
                index
                for index in range(event_index - 1, max(-1, event_index - 9), -1)
                if _direction(candles[index]) == desired_origin
            ),
            -1,
        )
        if origin_index < 0:
            continue
        origin = candles[origin_index]
        low = _number(origin.get("low"))
        high = _number(origin.get("high"))
        later = candles[event_index + 1 :]
        retest_indices = [
            event_index + 1 + offset
            for offset, row in enumerate(later)
            if _number(row.get("low")) <= high and _number(row.get("high")) >= low
        ]
        block_id = _stable_id("OB", side, origin_index, round(low, 8), round(high, 8))
        blocks.append(
            {
                "order_block_id": block_id,
                "side": side,
                "origin_index": origin_index,
                "causing_bms_id": event.get("event_id"),
                "low": low,
                "high": high,
                "last_opposing_candle_confirmed": True,
                "retest_indices": retest_indices,
                "return_to_order_block": bool(retest_indices),
                "latest_retest_index": retest_indices[-1] if retest_indices else None,
            }
        )
    active = blocks[-1] if blocks else {}
    return {"blocks": blocks, "active_block": active, "independently_derived": True}


def _liquidity_and_turtle_soup(
    candles: Sequence[Mapping[str, Any]],
    structure: Mapping[str, Any],
    order_blocks: Mapping[str, Any],
) -> dict[str, Any]:
    pivots = [
        *_rows(structure.get("internal_pivots")),
        *_rows(structure.get("intermediate_pivots")),
    ]
    median_range = statistics.median([_spread(row) for row in candles]) if candles else 0.0
    tolerance = max(1e-9, median_range * 0.16)
    pools: list[dict[str, Any]] = []
    for kind, liquidity_type, sweep_side in (("HIGH", "BUY_STOPS_LIQUIDITY", "SELL"), ("LOW", "SELL_STOPS_LIQUIDITY", "BUY")):
        selected = [pivot for pivot in pivots if pivot["kind"] == kind]
        for left, right in zip(selected, selected[1:]):
            if abs(float(left["price"]) - float(right["price"])) <= tolerance:
                level = (float(left["price"]) + float(right["price"])) / 2.0
                pools.append(
                    {
                        "pool_id": _stable_id("LIQ", liquidity_type, left["pivot_id"], right["pivot_id"]),
                        "type": liquidity_type,
                        "level": level,
                        "side_after_sweep": sweep_side,
                        "pivot_ids": [left["pivot_id"], right["pivot_id"]],
                        "confirmed_at_index": max(int(left["confirmed_at_index"]), int(right["confirmed_at_index"])),
                    }
                )
    sweeps: list[dict[str, Any]] = []
    for pool in pools:
        for index in range(int(pool["confirmed_at_index"]) + 1, len(candles)):
            row = candles[index]
            level = float(pool["level"])
            if pool["type"] == "BUY_STOPS_LIQUIDITY" and _number(row.get("high")) > level and _number(row.get("close")) < level:
                sweeps.append({"sweep_id": _stable_id("SWEEP", pool["pool_id"], index), "pool_id": pool["pool_id"], "index": index, "side": "SELL", "reclaim_close_confirmed": True})
                break
            if pool["type"] == "SELL_STOPS_LIQUIDITY" and _number(row.get("low")) < level and _number(row.get("close")) > level:
                sweeps.append({"sweep_id": _stable_id("SWEEP", pool["pool_id"], index), "pool_id": pool["pool_id"], "index": index, "side": "BUY", "reclaim_close_confirmed": True})
                break
    latest_sweep = sweeps[-1] if sweeps else {}
    bms_after = next(
        (
            event
            for event in _rows(structure.get("bms_events"))
            if latest_sweep
            and int(event.get("index", -1)) > int(latest_sweep.get("index", -1))
            and event.get("side") == latest_sweep.get("side")
        ),
        None,
    )
    active_block = _mapping(order_blocks.get("active_block"))
    rto = bool(active_block.get("return_to_order_block") and bms_after and active_block.get("side") == bms_after.get("side"))
    state = "UNPROVEN"
    if latest_sweep:
        state = "SWEEP_RECLAIMED"
    if bms_after:
        state = "SWEEP_RECLAIM_BMS_CONFIRMED"
    if rto:
        state = "TURTLE_SOUP_SH_BMS_RTO_COMPLETE"
    return {
        "liquidity_pools": pools,
        "sweep_events": sweeps,
        "latest_sweep": latest_sweep,
        "confirming_bms": bms_after or {},
        "return_to_order_block": rto,
        "state": state,
        "complete": state == "TURTLE_SOUP_SH_BMS_RTO_COMPLETE",
        "side": str(latest_sweep.get("side") or "NEUTRAL"),
    }


def _amd_state(
    candles: Sequence[Mapping[str, Any]],
    session_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if len(candles) < 12:
        return {"state": "INSUFFICIENT_VISIBLE_SEQUENCE", "complete": False, "side": "NEUTRAL"}
    rows = candles[-18:]
    third = max(4, len(rows) // 3)
    accumulation = rows[:third]
    manipulation = rows[third : 2 * third]
    distribution = rows[2 * third :]
    range_high = max(_number(row.get("high")) for row in accumulation)
    range_low = min(_number(row.get("low")) for row in accumulation)
    range_size = range_high - range_low
    median_range = statistics.median(_spread(row) for row in rows)
    accumulation_confirmed = range_size <= median_range * max(2.0, third * 0.75)
    swept_high = any(_number(row.get("high")) > range_high and _number(row.get("close")) < range_high for row in manipulation)
    swept_low = any(_number(row.get("low")) < range_low and _number(row.get("close")) > range_low for row in manipulation)
    side = "SELL" if swept_high else "BUY" if swept_low else "NEUTRAL"
    distribution_confirmed = bool(
        distribution
        and side in {"BUY", "SELL"}
        and ((_number(distribution[-1].get("close")) > range_high) if side == "BUY" else (_number(distribution[-1].get("close")) < range_low))
    )
    session_text = " ".join(f"{key}={value}" for key, value in _mapping(session_context).items()).upper()
    session_sequence_observed = any(token in session_text for token in ("ASIAN", "LONDON", "NEW_YORK", "NEW YORK"))
    state = "ACCUMULATION" if accumulation_confirmed else "UNPROVEN"
    if side in {"BUY", "SELL"}:
        state = "MANIPULATION_RECLAIMED"
    if distribution_confirmed:
        state = "AMD_DISTRIBUTION_CONFIRMED"
    return {
        "state": state,
        "complete": distribution_confirmed,
        "side": side,
        "accumulation_confirmed": accumulation_confirmed,
        "manipulation_swept_high": swept_high,
        "manipulation_swept_low": swept_low,
        "distribution_confirmed": distribution_confirmed,
        "session_sequence_observed": session_sequence_observed,
        "accumulation_range": [range_low, range_high],
    }


def _news_pivot(
    candles: Sequence[Mapping[str, Any]],
    news_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    news = _mapping(news_context)
    text = " ".join(f"{key}={value}" for key, value in news.items()).upper()
    high_impact = (
        str(news.get("impact") or news.get("event_impact") or "").upper() in {"HIGH", "RED", "HIGH_IMPACT"}
        or _truthy(news, "high_impact", "event_active", "news_active")
        or "HIGH IMPACT" in text
    ) and "NO_HIGH_IMPACT" not in text
    if not high_impact or len(candles) < 5:
        return {"active": high_impact, "state": "UNOBSERVED" if not high_impact else "WAITING_FOR_DISPLACEMENT", "confirmed": False, "side": "NEUTRAL"}
    recent = candles[-8:]
    baseline = statistics.median(_spread(row) for row in recent[:-2]) if len(recent) > 2 else _spread(recent[0])
    displacement_offset = next(
        (index for index, row in enumerate(recent) if _spread(row) >= baseline * 1.7 and _direction(row) in {"BUY", "SELL"}),
        -1,
    )
    if displacement_offset < 0:
        return {"active": True, "state": "WAITING_FOR_DISPLACEMENT", "confirmed": False, "side": "NEUTRAL"}
    displacement = recent[displacement_offset]
    displacement_side = _direction(displacement)
    after = recent[displacement_offset + 1 :]
    pivot_index = next((index for index, row in enumerate(after) if _direction(row) != displacement_side and _direction(row) != "NEUTRAL"), -1)
    if pivot_index < 0:
        return {"active": True, "state": "DISPLACEMENT_OBSERVED_WAITING_FOR_PIVOT", "confirmed": False, "side": "NEUTRAL", "displacement_side": displacement_side}
    pivot = after[pivot_index]
    side = _direction(pivot)
    midpoint = (_number(displacement.get("high")) + _number(displacement.get("low"))) / 2.0
    confirmed = (_number(pivot.get("close")) < midpoint) if side == "SELL" else (_number(pivot.get("close")) > midpoint)
    return {
        "active": True,
        "state": "NEWS_PIVOT_CONFIRMED" if confirmed else "PIVOT_FORMING_WAITING_FOR_CLOSE",
        "confirmed": confirmed,
        "side": side if confirmed else "NEUTRAL",
        "displacement_side": displacement_side,
        "event_phase": str(news.get("event_phase") or news.get("phase") or "ACTIVE").upper(),
        "direction_inferred_before_pivot": False,
    }


def _sakata_state(
    candles: Sequence[Mapping[str, Any]],
    structure: Mapping[str, Any],
) -> dict[str, Any]:
    highs = _rows(structure.get("intermediate_pivots"))
    pivot_highs = [row for row in highs if row.get("kind") == "HIGH"][-3:]
    pivot_lows = [row for row in highs if row.get("kind") == "LOW"][-3:]
    median_range = statistics.median([_spread(row) for row in candles]) if candles else 0.0
    tolerance = median_range * 0.35
    methods: list[dict[str, Any]] = []
    if len(pivot_highs) == 3 and max(float(row["price"]) for row in pivot_highs) - min(float(row["price"]) for row in pivot_highs) <= tolerance:
        methods.append({"method": "THREE_MOUNTAINS", "side": "SELL", "confirmed": True})
    if len(pivot_lows) == 3 and max(float(row["price"]) for row in pivot_lows) - min(float(row["price"]) for row in pivot_lows) <= tolerance:
        methods.append({"method": "THREE_RIVERS", "side": "BUY", "confirmed": True})
    if len(candles) >= 4:
        up_gaps = sum(_number(right.get("low")) > _number(left.get("high")) for left, right in zip(candles[-4:], candles[-3:]))
        down_gaps = sum(_number(right.get("high")) < _number(left.get("low")) for left, right in zip(candles[-4:], candles[-3:]))
        if up_gaps >= 3:
            methods.append({"method": "THREE_GAPS_EXHAUSTION", "side": "SELL", "confirmed": True})
        if down_gaps >= 3:
            methods.append({"method": "THREE_GAPS_EXHAUSTION", "side": "BUY", "confirmed": True})
    if len(candles) >= 3:
        directions = [_direction(row) for row in candles[-3:]]
        if directions == ["BUY", "BUY", "BUY"]:
            methods.append({"method": "THREE_SOLDIERS", "side": "BUY", "confirmed": True})
        if directions == ["SELL", "SELL", "SELL"]:
            methods.append({"method": "THREE_CROWS", "side": "SELL", "confirmed": True})
    return {
        "active_methods": methods,
        "complete": True,
        "source_file": _CANDLE_FILE,
        "printed_pages": [253, 267],
        "pdf_pages": [277, 291],
    }


def _rule_calibration(pair_dna_context: Mapping[str, Any] | None) -> dict[str, Any]:
    profile = _mapping(pair_dna_context)
    correlations = _mapping(profile.get("outcome_correlations") or profile.get("rule_outcomes"))
    multipliers: dict[str, float] = {}
    evidence: dict[str, Any] = {}
    for rule_id, raw in correlations.items():
        row = _mapping(raw)
        wins = _number(row.get("wins") or row.get("correct") or row.get("positive"))
        losses = _number(row.get("losses") or row.get("incorrect") or row.get("negative"))
        support = int(_number(row.get("support"), wins + losses))
        if support < 8:
            continue
        rate = wins / max(1.0, wins + losses)
        multiplier = max(0.65, min(1.35, 0.7 + 0.6 * rate))
        multipliers[str(rule_id)] = round(multiplier, 6)
        evidence[str(rule_id)] = {"support": support, "observed_rate": round(rate, 6)}
    return {
        "pair_specific_only": True,
        "minimum_support": 8,
        "multipliers": multipliers,
        "evidence": evidence,
        "default_multiplier": 1.0,
    }


def _first_finite(row: Mapping[str, Any], *keys: str, default: float = math.nan) -> float:
    for key in keys:
        if key not in row or row.get(key) is None:
            continue
        value = _number(row.get(key), math.nan)
        if math.isfinite(value):
            return value
    return default


def _pixel_candle(row: Mapping[str, Any], index: int) -> dict[str, float]:
    x_value = _first_finite(row, "x", "center_x", "x_center_px", default=float(index))
    top = _first_finite(row, "top_y", "high_y", "wick_high_y_px")
    bottom = _first_finite(row, "bottom_y", "low_y", "wick_low_y_px")
    open_y = _first_finite(row, "open_y", "open_y_px")
    close_y = _first_finite(row, "close_y", "close_y_px")
    if not all(math.isfinite(value) for value in (x_value, top, bottom, open_y, close_y)):
        return {}
    return {
        "index": float(index),
        "x": x_value,
        "top": min(top, bottom),
        "bottom": max(top, bottom),
        "open_y": open_y,
        "close_y": close_y,
        "body_top": min(open_y, close_y),
        "body_bottom": max(open_y, close_y),
    }


def _pixel_spacing(candles: Sequence[Mapping[str, Any]]) -> float:
    xs = sorted(
        _first_finite(row, "x", "center_x", "x_center_px", default=float(index))
        for index, row in enumerate(candles)
    )
    steps = [right - left for left, right in zip(xs, xs[1:]) if right > left]
    return statistics.median(steps) if steps else 8.0


def _group_touch_indices(indices: Sequence[int]) -> list[int]:
    groups: list[list[int]] = []
    for index in sorted(set(indices)):
        if not groups or index > groups[-1][-1] + 1:
            groups.append([index])
        else:
            groups[-1].append(index)
    return [group[-1] for group in groups]


def _zone_pixel_bounds(zone: Mapping[str, Any]) -> list[float]:
    raw = zone.get("bbox") or zone.get("bounds")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) and len(raw) >= 4:
        values = [_number(value, math.nan) for value in raw[:4]]
        if all(math.isfinite(value) for value in values):
            x0, y0, x1, y1 = values
            return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    x0 = _first_finite(zone, "left_x", "x1", "start_x")
    x1 = _first_finite(zone, "right_x", "x2", "end_x")
    y0 = _first_finite(zone, "top_y", "y1", "price_high_y_px")
    y1 = _first_finite(zone, "bottom_y", "y2", "price_low_y_px")
    if all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    return []


def _strict_zone_contracts_v3(
    candles: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pixels = [_pixel_candle(row, index) for index, row in enumerate(candles)]
    spacing = _pixel_spacing(candles)
    heights = [row["bottom"] - row["top"] for row in pixels if row]
    tolerance = max(1.5, (statistics.median(heights) if heights else 12.0) * 0.1)
    contracts: list[dict[str, Any]] = []
    location_history: dict[str, list[dict[str, Any]]] = {
        str(index): [] for index in range(len(candles))
    }
    for zone_index, raw in enumerate(_rows(zones)):
        zone = dict(raw)
        bounds = _zone_pixel_bounds(zone)
        role = _side(
            zone.get("role_side"), zone.get("direction"), zone.get("side"),
            zone.get("role"), zone.get("zone_role"), zone.get("zone_family"),
            zone.get("type"), zone.get("label"),
        )
        if not bounds or role not in {"BUY", "SELL"} or zone.get("still_significant") is False:
            continue
        x0, y0, x1, y1 = bounds
        contacts: list[int] = []
        for index, pixel in enumerate(pixels):
            if not pixel or pixel["x"] < x0 - spacing or pixel["x"] > x1 + spacing:
                continue
            if pixel["bottom"] < y0 - tolerance or pixel["top"] > y1 + tolerance:
                continue
            contacts.append(index)
            location_history[str(index)].append(
                {
                    "zone_id": str(zone.get("zone_id") or zone.get("id") or f"zone-{zone_index}"),
                    "role_side": role,
                    "zone_top_y_px": y0,
                    "zone_bottom_y_px": y1,
                }
            )
        touch_indices = _group_touch_indices(contacts)
        break_index: int | None = None
        for index, pixel in enumerate(pixels):
            if not pixel or pixel["x"] < x0:
                continue
            broke = (
                pixel["close_y"] > y1 + tolerance
                if role == "BUY"
                else pixel["close_y"] < y0 - tolerance
            )
            if broke:
                break_index = index
                break
        original_rejections: list[int] = []
        for index in touch_indices:
            if break_index is not None and index >= break_index:
                continue
            pixel = pixels[index]
            closes_away = (
                pixel["close_y"] < y0 - tolerance * 0.25
                if role == "BUY"
                else pixel["close_y"] > y1 + tolerance * 0.25
            )
            candle_side = _direction(candles[index])
            if closes_away and candle_side in {role, "NEUTRAL"}:
                original_rejections.append(index)
        flipped_side = "SELL" if role == "BUY" else "BUY"
        retest_indices: list[int] = []
        if break_index is not None:
            for index in contacts:
                if index <= break_index:
                    continue
                pixel = pixels[index]
                holds_new_role = (
                    pixel["close_y"] <= y0 + tolerance
                    if flipped_side == "BUY"
                    else pixel["close_y"] >= y1 - tolerance
                )
                if holds_new_role:
                    retest_indices.append(index)
        latest_index = len(candles) - 1
        current_rejection = bool(original_rejections and original_rejections[-1] == latest_index)
        current_role_flip = bool(retest_indices and retest_indices[-1] == latest_index)
        role_flip_confirmed = bool(break_index is not None and retest_indices and break_index < retest_indices[-1])
        latest_close_y = pixels[-1]["close_y"] if pixels and pixels[-1] else math.nan
        distance = (
            min(abs(latest_close_y - y0), abs(latest_close_y - y1))
            if math.isfinite(latest_close_y)
            else math.inf
        )
        contract = {
            **zone,
            "zone_id": str(zone.get("zone_id") or zone.get("id") or f"zone-{zone_index}"),
            "bounds": bounds,
            "bbox": bounds,
            "role": "SUPPORT" if role == "BUY" else "RESISTANCE",
            "role_side": role,
            "original_role_side": role,
            "touch_count": len(touch_indices),
            "touch_candle_indices": touch_indices,
            "break_index": break_index,
            "break_confirmed": break_index is not None,
            "flipped_role_side": flipped_side if break_index is not None else "NEUTRAL",
            "retest_indices": retest_indices,
            "retest_index": retest_indices[-1] if retest_indices else None,
            "role_flip_confirmed": role_flip_confirmed,
            "current_rejection": current_rejection,
            "rejection_confirmed": current_rejection,
            "current_role_flip_retest": current_role_flip,
            "entry_authority_allowed": current_rejection or current_role_flip,
            "current_action_side": flipped_side if current_role_flip else role if current_rejection else "NEUTRAL",
            "distance_to_latest_close_px": round(distance, 6) if math.isfinite(distance) else None,
            "still_significant": True,
            "quality_grade": "A" if len(touch_indices) >= 3 else "B" if len(touch_indices) >= 2 else "C",
            "rule_provenance": {
                "source_file": _TRENDLINE_FILE,
                "pdf_pages": [26, 27, 33],
                "source_section": "Support, resistance, breaks, and reactions",
            },
        }
        contracts.append(contract)
    for role in ("BUY", "SELL"):
        eligible = [row for row in contracts if row["role_side"] == role and row.get("distance_to_latest_close_px") is not None]
        if eligible:
            nearest = min(eligible, key=lambda row: float(row["distance_to_latest_close_px"]))
            nearest["nearest"] = True
    return {
        "contracts": contracts,
        "active_contracts": [row for row in contracts if row.get("still_significant")],
        "current_reactions": [row for row in contracts if row.get("current_rejection")],
        "role_flips": [row for row in contracts if row.get("role_flip_confirmed")],
        "current_role_flip_retests": [row for row in contracts if row.get("current_role_flip_retest")],
        "location_history": location_history,
        "technical_indicators_used": False,
    }


def _strict_anchor_is_significant_v3(
    anchor: tuple[float, float],
    role: str,
    candles: Sequence[Mapping[str, Any]],
    pixels: Sequence[Mapping[str, float]],
    pivots: Sequence[Mapping[str, Any]],
    *,
    x_tolerance: float,
    y_tolerance: float,
) -> bool:
    if not pixels:
        return False
    index = min(
        range(len(pixels)),
        key=lambda candidate: abs(
            float((pixels[candidate] or {"x": float(candidate)})["x"]) - anchor[0]
        ),
    )
    pixel = pixels[index]
    if not pixel or abs(float(pixel["x"]) - anchor[0]) > x_tolerance:
        return False
    expected_wick_y = float(pixel["bottom"] if role == "BUY" else pixel["top"])
    if abs(expected_wick_y - anchor[1]) > y_tolerance:
        return False
    required_kind = "LOW" if role == "BUY" else "HIGH"
    return any(
        str(pivot.get("kind") or "").upper() == required_kind
        and int(_number(pivot.get("index"), -999)) == index
        and str(pivot.get("tier") or "").upper() in {"INTERNAL", "INTERMEDIATE", "EXTERNAL"}
        for pivot in pivots
    )


def _strict_trendline_contracts_v3(
    candles: Sequence[Mapping[str, Any]],
    trendlines: Sequence[Mapping[str, Any]],
    zone_contracts: Mapping[str, Any],
    structure: Mapping[str, Any],
    timeframe: str,
) -> dict[str, Any]:
    pivots = [
        *_rows(structure.get("internal_pivots")),
        *_rows(structure.get("intermediate_pivots")),
        *_rows(structure.get("external_pivots")),
    ]
    pixels = [_pixel_candle(row, index) for index, row in enumerate(candles)]
    spacing = _pixel_spacing(candles)
    heights = [row["bottom"] - row["top"] for row in pixels if row]
    tolerance = max(1.5, (statistics.median(heights) if heights else 12.0) * 0.1)
    current_seconds = _TIMEFRAME_SECONDS.get(str(timeframe or "M5").upper(), 300)
    contracts: list[dict[str, Any]] = []
    location_history: dict[str, str] = {}
    for line_index, raw in enumerate(_rows(trendlines)):
        line = dict(raw)
        points = sorted(_line_points(line)[:2], key=lambda point: point[0])
        role = _side(
            line.get("role_side"), line.get("direction"), line.get("side"),
            line.get("role"), line.get("trendline_role"), line.get("type"),
            line.get("label"),
        )
        accepted = line.get("geometry_contract_accepted") is True or line.get("accepted") is True
        if len(points) < 2 or role not in {"BUY", "SELL"}:
            continue
        anchor_indices = [
            min(range(len(pixels)), key=lambda index: abs((pixels[index] or {"x": float(index)})["x"] - point[0]))
            for point in points
        ] if pixels else []
        anchors_distinct = bool(
            len(anchor_indices) == 2
            and anchor_indices[0] != anchor_indices[1]
        )
        anchors_significant = bool(
            anchors_distinct
            and all(
                _strict_anchor_is_significant_v3(
                    points[offset], role, candles, pixels, pivots,
                    x_tolerance=max(2.0, spacing * 0.55),
                    y_tolerance=tolerance,
                )
                for offset in range(2)
            )
        )
        obstruction_indices: list[int] = []
        if len(anchor_indices) == 2:
            left_index, right_index = sorted(anchor_indices)
            for index in range(left_index + 1, right_index):
                pixel = pixels[index]
                if not pixel:
                    continue
                projected = _line_y(points, pixel["x"])
                if projected is None:
                    continue
                obstructed = (
                    pixel["body_bottom"] > projected + tolerance
                    if role == "BUY"
                    else pixel["body_top"] < projected - tolerance
                )
                if obstructed:
                    obstruction_indices.append(index)
        contact_indices: list[int] = []
        contact_points: list[list[float]] = []
        rejection_indices: list[int] = []
        close_through_indices: list[int] = []
        second_anchor = max(anchor_indices) if anchor_indices else -1
        for index, pixel in enumerate(pixels):
            if not pixel or pixel["x"] < points[0][0] - spacing:
                continue
            projected = _line_y(points, pixel["x"])
            if projected is None:
                continue
            contact = pixel["top"] - tolerance <= projected <= pixel["bottom"] + tolerance
            body_defends = (
                pixel["body_bottom"] <= projected + tolerance
                if role == "BUY"
                else pixel["body_top"] >= projected - tolerance
            )
            closes_through = (
                pixel["close_y"] > projected + tolerance
                if role == "BUY"
                else pixel["close_y"] < projected - tolerance
            )
            if closes_through and index > second_anchor:
                close_through_indices.append(index)
            if contact and body_defends:
                contact_indices.append(index)
                contact_points.append([round(pixel["x"], 6), round(projected, 6)])
                closes_away = (
                    pixel["close_y"] < projected - tolerance * 0.25
                    if role == "BUY"
                    else pixel["close_y"] > projected + tolerance * 0.25
                )
                if index > second_anchor and closes_away and _direction(candles[index]) in {role, "NEUTRAL"}:
                    rejection_indices.append(index)
        touch_indices = _group_touch_indices(contact_indices)
        third_touch_indices = [index for index in touch_indices if index > second_anchor]
        line_defined = bool(accepted and anchors_significant and not obstruction_indices)
        mature = bool(
            line_defined
            and len(touch_indices) >= 3
            and third_touch_indices
        )
        break_index = close_through_indices[0] if close_through_indices else None
        flipped_side = "SELL" if role == "BUY" else "BUY"
        retest_indices: list[int] = []
        if break_index is not None:
            for index in range(break_index + 1, len(pixels)):
                pixel = pixels[index]
                if not pixel:
                    continue
                projected = _line_y(points, pixel["x"])
                if projected is None:
                    continue
                contact = pixel["top"] - tolerance <= projected <= pixel["bottom"] + tolerance
                holds_new_role = (
                    pixel["close_y"] <= projected + tolerance
                    if flipped_side == "BUY"
                    else pixel["close_y"] >= projected - tolerance
                )
                if contact and holds_new_role:
                    retest_indices.append(index)
        latest_index = len(candles) - 1
        current_touch = bool(touch_indices and touch_indices[-1] == latest_index)
        current_rejection = bool(mature and rejection_indices and rejection_indices[-1] == latest_index and (break_index is None or latest_index < break_index))
        current_role_flip = bool(mature and retest_indices and retest_indices[-1] == latest_index)
        role_flip_confirmed = bool(mature and break_index is not None and retest_indices and break_index < retest_indices[-1])
        line_timeframe = str(line.get("timeframe") or line.get("source_timeframe") or timeframe).upper()
        line_seconds = _TIMEFRAME_SECONDS.get(line_timeframe, current_seconds)
        outer = "OUTER" in str(line.get("role") or line.get("kind") or "").upper() or line_seconds > current_seconds
        authority_multiplier = 1.6 if outer else 1.0 if line_seconds == current_seconds else 0.65
        lifecycle = "CANDIDATE_REJECTED"
        if line_defined:
            lifecycle = "TWO_ANCHORS_WAITING_FOR_THIRD_TOUCH"
        if mature:
            lifecycle = "ACTIVE_THREE_TOUCH"
        if break_index is not None:
            lifecycle = "BROKEN_WAITING_FOR_ROLE_FLIP_RETEST"
        if role_flip_confirmed:
            lifecycle = "ROLE_FLIP_RETEST_CONFIRMED"
        line_id = str(line.get("trendline_id") or line.get("id") or f"trendline-{line_index}")
        contract = {
            **line,
            "trendline_id": line_id,
            "role_side": role,
            "line_points": [[x, y] for x, y in points],
            "line_points_v3": [[x, y] for x, y in points],
            "anchor_wick_points": [[x, y] for x, y in points],
            "anchor_candle_indices": anchor_indices,
            "distinct_anchor_candles_confirmed": anchors_distinct,
            "geometry_contract_accepted": line_defined,
            "geometry_status": "STRICT_WICK_ANCHORS_ACCEPTED" if line_defined else "STRICT_GEOMETRY_REJECTED",
            "significant_anchor_pair_confirmed": anchors_significant,
            "body_obstruction_count": len(obstruction_indices),
            "body_obstruction_indices": obstruction_indices,
            "touch_count": len(touch_indices),
            "touch_candle_indices": touch_indices,
            "touch_points": contact_points,
            "third_touch_indices": third_touch_indices,
            "three_touch_confirmed": mature,
            "strict_strategy_valid": mature,
            "current_touch": current_touch,
            "current_rejection": current_rejection,
            "reaction_confirmed": current_rejection,
            "break_index": break_index,
            "break_side": flipped_side if break_index is not None else "NEUTRAL",
            "retest_indices": retest_indices,
            "retest_index": retest_indices[-1] if retest_indices else None,
            "role_flip_confirmed": role_flip_confirmed,
            "current_role_flip_retest": current_role_flip,
            "break_retest_confirmed": current_role_flip,
            "current_action_side": flipped_side if current_role_flip else role if current_rejection else "NEUTRAL",
            "line_timeframe": line_timeframe,
            "outer_trendline": outer,
            "authority_multiplier": authority_multiplier,
            "lifecycle_state": lifecycle,
            "rule_provenance": {
                "source_file": _TRENDLINE_FILE,
                "pdf_pages": [13, 20, 23, 58],
                "source_section": "Valid lines, obstruction, wick anchors, and reaction",
            },
        }
        contracts.append(contract)
        if mature:
            for index in touch_indices:
                location_history[str(index)] = role
    zone_history = _mapping(zone_contracts.get("location_history"))
    for candle_index, raw_locations in zone_history.items():
        zone_sides = {
            str(row.get("role_side") or "NEUTRAL")
            for row in _rows(raw_locations)
        }
        zone_sides.discard("NEUTRAL")
        line_side = location_history.get(str(candle_index))
        if line_side and zone_sides and line_side not in zone_sides:
            location_history[str(candle_index)] = "NEUTRAL"
        elif not line_side and len(zone_sides) == 1:
            location_history[str(candle_index)] = next(iter(zone_sides))
    valid = [row for row in contracts if row.get("strict_strategy_valid")]
    latest_close_y = pixels[-1]["close_y"] if pixels and pixels[-1] else math.nan
    targets: dict[str, Any] = {"BUY": None, "SELL": None}
    for action_side, opposing_side in (("BUY", "SELL"), ("SELL", "BUY")):
        candidates: list[tuple[float, dict[str, Any]]] = []
        for line in valid:
            if line.get("role_side") != opposing_side:
                continue
            projected = _line_y([tuple(point) for point in line["line_points_v3"]], pixels[-1]["x"] if pixels and pixels[-1] else 0.0)
            if projected is not None and math.isfinite(latest_close_y):
                candidates.append((abs(projected - latest_close_y), {"source": "OPPOSING_TRENDLINE", "line_id": line["trendline_id"], "target_y_px": round(projected, 6), "distance_px": round(abs(projected - latest_close_y), 6)}))
        for zone in _rows(zone_contracts.get("active_contracts")):
            if zone.get("role_side") != opposing_side or zone.get("distance_to_latest_close_px") is None:
                continue
            candidates.append((float(zone["distance_to_latest_close_px"]), {"source": "OPPOSING_ZONE", "zone_id": zone.get("zone_id"), "bounds": list(zone.get("bounds") or []), "distance_px": zone.get("distance_to_latest_close_px")}))
        if candidates:
            targets[action_side] = min(candidates, key=lambda item: item[0])[1]
    return {
        "contracts": contracts,
        "valid_contracts": valid,
        "valid_count": len(valid),
        "outer_valid_count": sum(bool(row.get("outer_trendline")) for row in valid),
        "current_reactions": [row for row in valid if row.get("current_rejection")],
        "role_flips": [row for row in valid if row.get("role_flip_confirmed")],
        "current_role_flip_retests": [row for row in valid if row.get("current_role_flip_retest")],
        "false_breach_redraw_count": 0,
        "candle_location_history": location_history,
        "candle_zone_location_history": zone_history,
        "historical_zone_binding_complete": len(zone_history) == len(candles),
        "opposing_targets": targets,
        "support_resistance_contracts": dict(zone_contracts),
    }


def _opposing_force_reactions_v3(
    trendline: Mapping[str, Any],
    zones: Mapping[str, Any],
) -> dict[str, Any]:
    current: list[dict[str, Any]] = []
    for row in _rows(trendline.get("current_reactions")):
        current.append({"kind": "TRENDLINE", "side": row.get("role_side"), "id": row.get("trendline_id"), "state": "CURRENT_REJECTION"})
    for row in _rows(trendline.get("current_role_flip_retests")):
        current.append({"kind": "TRENDLINE_ROLE_FLIP", "side": row.get("current_action_side"), "id": row.get("trendline_id"), "state": "CURRENT_RETEST_HOLD"})
    for row in _rows(zones.get("current_reactions")):
        current.append({"kind": "ZONE", "side": row.get("role_side"), "id": row.get("zone_id"), "state": "CURRENT_REJECTION"})
    for row in _rows(zones.get("current_role_flip_retests")):
        current.append({"kind": "ZONE_ROLE_FLIP", "side": row.get("current_action_side"), "id": row.get("zone_id"), "state": "CURRENT_RETEST_HOLD"})
    by_action_side: dict[str, Any] = {}
    for side in ("BUY", "SELL"):
        opposing = "SELL" if side == "BUY" else "BUY"
        conflicts = [row for row in current if row.get("side") == opposing]
        by_action_side[side] = {
            "at_current_force": bool(conflicts),
            "opposing_side": opposing,
            "current_conflicts": conflicts,
            "nearest_target": _mapping(trendline.get("opposing_targets")).get(side),
        }
    return {"current_reactions": current, "by_action_side": by_action_side}


def evaluate_full_non_indicator_book_stack_v3(
    *,
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    trendlines: Sequence[Mapping[str, Any]] | None = None,
    support_resistance_zones: Sequence[Mapping[str, Any]] | None = None,
    session_context: Mapping[str, Any] | None = None,
    news_context: Mapping[str, Any] | None = None,
    pair_dna_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _rows(candles)
    structure = _structure_hierarchy(rows)
    zones = _strict_zone_contracts_v3(rows, support_resistance_zones or [])
    trendline = _strict_trendline_contracts_v3(
        rows,
        trendlines or [],
        zones,
        structure,
        timeframe,
    )
    order_blocks = _order_blocks(rows, structure)
    liquidity = _liquidity_and_turtle_soup(rows, structure, order_blocks)
    amd = _amd_state(rows, session_context)
    news_pivot = _news_pivot(rows, news_context)
    sakata = _sakata_state(rows, structure)
    calibration = _rule_calibration(pair_dna_context)
    scores = {"BUY": 0.0, "SELL": 0.0}
    traces: list[dict[str, Any]] = []
    for line in _rows(trendline.get("valid_contracts")):
        side = str(line.get("current_action_side") or "NEUTRAL")
        if side not in scores:
            continue
        if not (line.get("current_rejection") or line.get("current_role_flip_retest")):
            continue
        weight = (2.25 if line.get("current_role_flip_retest") else 1.85) * _number(line.get("authority_multiplier"), 1.0)
        scores[side] += weight
        traces.append(_trace("STRICT_TRENDLINE_ROLE_FLIP" if line.get("current_role_flip_retest") else "STRICT_TRENDLINE_REJECTION", side, weight, "Two significant wick anchors, no intervening obstruction, a distinct third touch, and the current completed rejection or ordered role-flip retest all passed.", source_file=_TRENDLINE_FILE, pdf_pages=[13, 20, 23, 58], section="Valid trendlines, wick contact, and reaction"))
    for zone in _rows(zones.get("active_contracts")):
        side = str(zone.get("current_action_side") or "NEUTRAL")
        if side not in scores or not (zone.get("current_rejection") or zone.get("current_role_flip_retest")):
            continue
        weight = 2.15 if zone.get("current_role_flip_retest") else 1.55
        scores[side] += weight
        traces.append(_trace("SUPPORT_RESISTANCE_ROLE_FLIP" if zone.get("current_role_flip_retest") else "SUPPORT_RESISTANCE_REJECTION", side, weight, "The current closed candle respected the exact zone or completed break-before-retest role ordering.", source_file=_TRENDLINE_FILE, pdf_pages=[26, 27, 33], section="Support, resistance, and role reactions"))
    active_block = _mapping(order_blocks.get("active_block"))
    if active_block.get("return_to_order_block") and active_block.get("side") in scores:
        side = str(active_block["side"])
        scores[side] += 1.65
        traces.append(_trace("INDEPENDENT_BMS_ORDER_BLOCK_RTO", side, 1.65, "The last opposing candle causing BMS was independently derived and later retested.", source_file=_HLZ_FILE, pdf_pages=[51, 55, 88, 95], section="Order blocks and return to order block"))
    if liquidity["complete"] and liquidity["side"] in scores:
        side = str(liquidity["side"])
        scores[side] += 2.1
        traces.append(_trace("TURTLE_SOUP_SH_BMS_RTO_COMPLETE", side, 2.1, "Equal-level liquidity, sweep/reclaim, confirming BMS, and RTO completed in causal order.", source_file=_HLZ_FILE, pdf_pages=[80, 95], section="Turtle Soup, SH+BMS+RTO, SMS+BMS+RTO"))
    if amd["complete"] and amd["side"] in scores:
        side = str(amd["side"])
        scores[side] += 1.55
        traces.append(_trace("AMD_SEQUENCE_COMPLETE", side, 1.55, "Accumulation range, opposite-side manipulation/reclaim, and distribution completed.", source_file=_HLZ_FILE, pdf_pages=[61, 72, 99], section="Accumulation, manipulation, and distribution"))
    if news_pivot["confirmed"] and news_pivot["side"] in scores:
        side = str(news_pivot["side"])
        scores[side] += 1.35
        traces.append(_trace("POST_NEWS_PIVOT_CONFIRMED", side, 1.35, "High-impact displacement was followed by an opposing pivot and midpoint confirmation close.", source_file=_HLZ_FILE, pdf_pages=[46, 49], section="High-impact news liquidity behavior"))
    for method in _rows(sakata.get("active_methods")):
        side = str(method.get("side") or "NEUTRAL")
        if side in scores:
            scores[side] += 0.85
            traces.append(_trace(f"SAKATA_{method['method']}", side, 0.85, "A Sakata price-cycle formation was derived from visible candles and confirmed pivots.", source_file=_CANDLE_FILE, pdf_pages=[277, 291], section="Sakata Five Methods"))
    opposing_reactions = _opposing_force_reactions_v3(trendline, zones)
    return {
        "schema": FULL_BOOK_STACK_SCHEMA_V3,
        "future_blind": True,
        "technical_indicators_used": False,
        "technical_indicator_scope": "EXCLUDED_BY_USER",
        "horizon_published": False,
        "execution_authority": False,
        "action_authority_scope": "CURRENT_CLOSED_CANDLE_ONLY",
        "score_adjustments": scores,
        "market_structure": structure,
        "trendline_contracts": trendline,
        "support_resistance": zones,
        "order_blocks": order_blocks,
        "liquidity_turtle_soup": liquidity,
        "amd": amd,
        "news_pivot": news_pivot,
        "sakata": sakata,
        "rule_calibration": calibration,
        "candle_location_history": trendline["candle_location_history"],
        "candle_zone_location_history": trendline["candle_zone_location_history"],
        "opposing_targets": trendline["opposing_targets"],
        "opposing_force_reactions": opposing_reactions,
        "rule_trace": traces,
    }


def rank_book_scanner_v3(
    candlestick_catalog: Mapping[str, Any],
    full_stack: Mapping[str, Any],
    higher_timeframe_side: str,
) -> dict[str, Any]:
    calibration = _mapping(_mapping(full_stack.get("rule_calibration")).get("multipliers"))
    structure_side = _side(_mapping(full_stack.get("market_structure")).get("structure_side"))
    ranked: list[dict[str, Any]] = []
    for detection in _rows(candlestick_catalog.get("detections")):
        rule_id = str(detection.get("rule_id") or "")
        side = _side(detection.get("side"))
        base = _number(detection.get("weight"))
        multiplier = _number(calibration.get(rule_id), 1.0)
        htf_bonus = 0.35 if side == _side(higher_timeframe_side) else 0.0
        structure_bonus = 0.25 if side == structure_side else 0.0
        score = base * multiplier + htf_bonus + structure_bonus
        ranked.append(
            {
                "rule_id": rule_id,
                "side": side,
                "rank_score": round(score, 6),
                "directional_authority": bool(detection.get("directional_authority")),
                "pair_calibration_multiplier": multiplier,
                "failed_requirements": list(detection.get("failed_requirements") or []),
                "source_file": detection.get("source_file"),
                "pdf_pages": detection.get("pdf_pages"),
            }
        )
    ranked.sort(key=lambda row: (bool(row["directional_authority"]), float(row["rank_score"])), reverse=True)
    return {
        "schema": "PG_BOOK_CANDLE_SCANNER_RANKING_V3",
        "technical_indicators_used": False,
        "technical_indicator_scope": "EXCLUDED_BY_USER",
        "horizon_published": False,
        "execution_authority": False,
        "ranked_patterns": ranked,
        "selected_pattern": ranked[0] if ranked and ranked[0]["directional_authority"] else None,
    }


def build_pair_conditioned_horizon_v3(
    context_suite: Mapping[str, Any],
    *,
    primary_side: str,
    confidence: float,
) -> dict[str, Any]:
    side = _side(primary_side)
    sign = 1.0 if side == "BUY" else -1.0
    pair = _mapping(context_suite.get("pair_dna"))
    full = _mapping(context_suite.get("full_non_indicator_stack_v3"))
    regime = str(pair.get("current_regime") or "UNKNOWN").upper()
    personality = str(pair.get("dominant_personality") or "UNKNOWN").upper()
    expansion = any(token in f"{regime} {personality}" for token in ("EXPANSION", "IMPULSE", "VOLATILE", "LONG_BODY"))
    compression = any(token in f"{regime} {personality}" for token in ("COMPRESSION", "RANGE", "DOJI", "SMALL_BODY"))
    impulse_length = 18 if expansion else 9 if compression else 13
    rest_length = 3 if expansion else 7 if compression else 5
    pullback_length = 7 if expansion else 10 if compression else 8
    continuation_length = 20 if expansion else 14 if compression else 17
    used = impulse_length + rest_length + pullback_length + continuation_length
    effective_htf = _side(_mapping(context_suite.get("higher_timeframe")).get("effective_side"))
    terminal_sign = 1.0 if effective_htf == "BUY" else -1.0 if effective_htf == "SELL" else sign
    phases = [
        ("IMPULSE", impulse_length, sign, 1.0),
        ("REST", rest_length, sign, 0.14),
        ("PULLBACK", pullback_length, -sign, 0.48),
        ("CONTINUATION", continuation_length, sign, 0.82),
        ("TERMINAL_STRUCTURE", 72 - used, terminal_sign, 0.66),
    ]
    if _mapping(full.get("liquidity_turtle_soup")).get("complete"):
        phases[0] = ("SWEEP_RECLAIM_IMPULSE", impulse_length, sign, 1.12)
    if _mapping(full.get("amd")).get("complete"):
        phases[3] = ("DISTRIBUTION", continuation_length, sign, 0.94)
    amplitude = 0.7 + 0.3 * max(0.0, min(1.0, float(confidence)))
    wave = (0.78, 1.12, 0.91, 1.19, 0.73, 1.04, 0.88)
    multipliers: list[float] = []
    directions: list[str] = []
    phase_rows: list[dict[str, Any]] = []
    cursor = 0
    for name, length, phase_sign, strength in phases:
        phase_rows.append({"phase": name, "start_horizon": cursor + 1, "end_horizon": cursor + length, "side": "BUY" if phase_sign > 0 else "SELL", "length": length})
        for step in range(length):
            value = phase_sign * strength * amplitude * wave[step % len(wave)]
            multipliers.append(round(value, 6))
            directions.append("BUY" if value > 0.08 else "SELL" if value < -0.08 else "REST")
        cursor += length
    return {
        "schema": "PG_PAIR_CONDITIONED_72_HORIZON_V3",
        "horizon_count": 72,
        "pair_specific": bool(pair.get("profile_applied")),
        "regime": regime,
        "personality": personality,
        "phases": phase_rows,
        "phase_multipliers": multipliers[:72],
        "horizon_directions": directions[:72],
    }


__all__ = [
    "FULL_BOOK_STACK_SCHEMA_V3",
    "build_pair_conditioned_horizon_v3",
    "evaluate_full_non_indicator_book_stack_v3",
    "rank_book_scanner_v3",
]
