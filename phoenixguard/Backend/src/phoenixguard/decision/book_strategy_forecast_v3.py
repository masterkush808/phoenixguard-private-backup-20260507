"""Future-blind book-rule control for Phoenix Guard V3 forecasts.

The existing V3 stack already extracts candles, structure, SMC, zones, and
trendlines.  This module makes those observations causal forecast inputs.  It
does not inspect hidden candles and it never grants execution authority.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from phoenixguard.decision.book_strategy_context_v3 import (
    evaluate_book_strategy_context_v3,
)
from phoenixguard.decision.book_strategy_full_stack_v3 import (
    build_pair_conditioned_horizon_v3,
    rank_book_scanner_v3,
)
from phoenixguard.decision.candlestick_rule_catalog_v3 import (
    CANDLESTICK_RULE_CATALOG_V3,
    evaluate_candlestick_catalog_v3,
)


BOOK_STRATEGY_FORECAST_SCHEMA_V3 = "PG_BOOK_STRATEGY_FORECAST_CONTROL_V3"
FORECAST_HORIZON_CANDLES_V3 = 72

_BOOK_SOURCES: dict[str, tuple[str, str]] = {
    "LOCATION": (
        "The power of Japanese candlestick charts",
        "Prior trend, structural location, and confirmation",
    ),
    "CANDLE": (
        "The power of Japanese candlestick charts",
        "Reversal and continuation candlestick formations",
    ),
    "TRENDLINE": (
        "FOREX BLACK BOOK",
        "Wick trendline touches, rejection, break, and retest",
    ),
    "STRUCTURE": (
        "HLZ - Market Structure And Powerful Setups",
        "Market structure, opposing force, and role reversal",
    ),
    "SMC": (
        "secrets revealed $10 000 cost price-1-1",
        "BMS, SMS, liquidity sweep, and order-block behavior",
    ),
    "CONFLUENCE": (
        "The Art of Currency Trading",
        "Context, confluence, pair behavior, and invalidation",
    ),
}

_BOOK_PROVENANCE: dict[str, dict[str, Any]] = {
    "LOCATION": {
        "source_file": "The power of Japanese candlestick charts _ advanced filtering techniques for trading stocks, futures and Forex ( PDFDrive ).pdf",
        "printed_pages": [67, 69],
        "pdf_pages": [91, 93],
    },
    "CANDLE": {
        "source_file": "The power of Japanese candlestick charts _ advanced filtering techniques for trading stocks, futures and Forex ( PDFDrive ).pdf",
        "printed_pages": [70, 204],
        "pdf_pages": [94, 228],
    },
    "TRENDLINE": {
        "source_file": "secrets revealed $10 000 cost price-1-1.pdf",
        "printed_pages": [7, 22],
        "pdf_pages": [7, 22],
    },
    "STRUCTURE": {
        "source_file": "HLZ - Market Structure And Powerful Setups.pdf",
        "printed_pages": [4, 105],
        "pdf_pages": [4, 105],
    },
    "SMC": {
        "source_file": "HLZ - Market Structure And Powerful Setups.pdf",
        "printed_pages": [14, 105],
        "pdf_pages": [14, 105],
    },
    "CONFLUENCE": {
        "source_file": "zlib.pub_the-art-of-currency-trading-a-professionals-guide-to-the-foreign-exchange-market.pdf",
        "printed_pages": [],
        "pdf_pages": [],
    },
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _pick(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row:
            parsed = _number(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _side(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if any(token in text for token in ("BUY", "BULL", "UP", "LONG", "DEMAND", "SUPPORT")):
        return "BUY"
    if any(token in text for token in ("SELL", "BEAR", "DOWN", "SHORT", "SUPPLY", "RESIST")):
        return "SELL"
    return "NEUTRAL"


def _truthy(row: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if value is True or (isinstance(value, (int, float)) and value != 0):
            return True
        if str(value or "").strip().lower() in {
            "true",
            "yes",
            "active",
            "confirmed",
            "accepted",
        }:
            return True
    return False


def _normalise_candles(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, source in enumerate(_rows(candles)):
        open_price = _pick(source, "open", "open_price", "o")
        high_price = _pick(source, "high", "high_price", "h")
        low_price = _pick(source, "low", "low_price", "l")
        close_price = _pick(source, "close", "close_price", "c")
        pixel_mode = None in (open_price, high_price, low_price, close_price)
        open_y = _pick(source, "open_y_px", "open_y", "body_open_y_px", "body_open_y")
        close_y = _pick(source, "close_y_px", "close_y", "body_close_y_px", "body_close_y")
        top_y = _pick(
            source,
            "wick_top_y_px",
            "wick_top_px",
            "wick_top_y",
            "high_y_px",
            "top_y_px",
            "top",
        )
        bottom_y = _pick(
            source,
            "wick_bottom_y_px",
            "wick_bottom_px",
            "wick_bottom_y",
            "low_y_px",
            "bottom_y_px",
            "bottom",
        )
        if pixel_mode and (open_y is None or close_y is None):
            body_top = _pick(source, "body_top_y_px", "body_top", "body_y_min")
            body_bottom = _pick(source, "body_bottom_y_px", "body_bottom", "body_y_max")
            direction = _side(source.get("direction") or source.get("side"))
            if body_top is not None and body_bottom is not None:
                if direction == "BUY":
                    open_y, close_y = body_bottom, body_top
                elif direction == "SELL":
                    open_y, close_y = body_top, body_bottom
        if pixel_mode:
            if None in (open_y, close_y, top_y, bottom_y):
                continue
            open_price = -float(open_y)
            close_price = -float(close_y)
            high_price = -float(top_y)
            low_price = -float(bottom_y)
        if None in (open_price, high_price, low_price, close_price):
            continue
        open_value = float(open_price)
        close_value = float(close_price)
        high_value = max(float(high_price), open_value, close_value)
        low_value = min(float(low_price), open_value, close_value)
        x_value = _pick(source, "x_center_px", "center_x_px", "x_px", "x", "index")
        result.append(
            {
                "index": index,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "range": max(1e-9, high_value - low_value),
                "body": abs(close_value - open_value),
                "direction": (
                    "BUY"
                    if close_value > open_value
                    else "SELL"
                    if close_value < open_value
                    else "NEUTRAL"
                ),
                "x": x_value if x_value is not None else float(index),
                "open_y": open_y,
                "close_y": close_y,
                "top_y": top_y,
                "bottom_y": bottom_y,
                "pixel_mode": pixel_mode,
                "timestamp": source.get("timestamp") or source.get("closed_at") or source.get("time"),
            }
        )
    return result


def _record(
    traces: list[dict[str, Any]],
    *,
    rule_id: str,
    source_kind: str,
    side: str,
    weight: float,
    reason: str,
    observed: bool = True,
    provenance: Mapping[str, Any] | None = None,
) -> None:
    source_book, source_section = _BOOK_SOURCES[source_kind]
    source_provenance = dict(_BOOK_PROVENANCE[source_kind])
    if isinstance(provenance, Mapping):
        # Detector-specific provenance is more precise when present, but an
        # omitted field must not erase the canonical book/page fallback.
        source_provenance.update(
            {
                key: value
                for key, value in provenance.items()
                if value not in (None, "", [], ())
            }
        )
    book_page_required = source_kind != "CONFLUENCE"
    traces.append(
        {
            "rule_id": rule_id,
            "side": side,
            "weight": round(float(weight), 6),
            "observed": bool(observed),
            "reason": reason,
            "source_book": source_book,
            "source_section": source_section,
            "provenance_scope": (
                "BOOK_RULE" if book_page_required else "INTERNAL_V3_EVIDENCE"
            ),
            "book_page_required": book_page_required,
            **source_provenance,
        }
    )


def _explicit_trends(payload: Mapping[str, Any] | None) -> tuple[str, str]:
    data = _mapping(payload)
    major = _side(
        data.get("major")
        or data.get("major_trend")
        or data.get("primary")
        or data.get("outer")
        or data.get("higher_timeframe")
        or data.get("global")
    )
    inner = _side(
        data.get("inner")
        or data.get("inner_trend")
        or data.get("local")
        or data.get("minor")
        or data.get("current")
        or data.get("impulse")
    )
    return major, inner


def _derived_trends(candles: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    if len(candles) < 3:
        return "NEUTRAL", "NEUTRAL"

    def direction(window: int) -> str:
        sample = candles[-min(window, len(candles)) :]
        delta = float(sample[-1]["close"]) - float(sample[0]["close"])
        noise = statistics.median(float(row["range"]) for row in sample)
        if abs(delta) <= noise * 0.35:
            return "NEUTRAL"
        return "BUY" if delta > 0.0 else "SELL"

    return direction(24), direction(8)


def _point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        x_value = _pick(value, "x_px", "x", "x_center_px")
        y_value = _pick(value, "y_px", "y", "wick_y_px")
        if x_value is not None and y_value is not None:
            return x_value, y_value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) >= 2:
        x_value = _number(value[0])
        y_value = _number(value[1])
        if x_value is not None and y_value is not None:
            return x_value, y_value
    return None


def _line_y_at_x(line: Mapping[str, Any], x_value: float) -> float | None:
    direct = _pick(line, "projected_y_px", "current_y_px", "line_y_px", "y_at_latest_px")
    if direct is not None:
        return direct
    raw_projection = _point(line.get("raw_projection_end"))
    projection_x = _number(line.get("current_projection_x"))
    if raw_projection is not None and projection_x is not None and abs(projection_x - x_value) <= 1.5:
        return raw_projection[1]
    candidates = (
        _rows(line.get("line_points"))
        or _rows(line.get("anchor_wick_points"))
        or _rows(line.get("points"))
    )
    points: list[tuple[float, float]] = []
    raw_points = line.get("line_points") or line.get("anchor_wick_points") or line.get("points")
    if isinstance(raw_points, Sequence) and not isinstance(raw_points, (str, bytes, bytearray)):
        points = [parsed for value in raw_points if (parsed := _point(value)) is not None]
    if len(points) < 2:
        first = _point(line.get("anchor_1") or line.get("first_anchor") or line.get("start"))
        second = _point(line.get("anchor_2") or line.get("second_anchor") or line.get("end"))
        points = [value for value in (first, second) if value is not None]
    if len(points) >= 2 and abs(points[1][0] - points[0][0]) > 1e-9:
        x1, y1 = points[0]
        x2, y2 = points[1]
        return y1 + ((y2 - y1) / (x2 - x1)) * (x_value - x1)
    slope = _pick(line, "slope_px_per_x", "slope", "gradient")
    intercept = _pick(line, "intercept_px", "intercept")
    if slope is not None and intercept is not None:
        return slope * x_value + intercept
    return None


def _trendline_rules(
    candles: Sequence[Mapping[str, Any]],
    trendlines: Sequence[Mapping[str, Any]],
    traces: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, Any]]:
    scores = {"BUY": 0.0, "SELL": 0.0}
    context: dict[str, Any] = {
        "mature_line_count": 0,
        "current_touch": False,
        "reaction_side": "NEUTRAL",
        "break_side": "NEUTRAL",
        "best_line": None,
    }
    if not candles:
        return scores, context
    latest = candles[-1]
    pixel_ranges = [
        abs(float(row["bottom_y"]) - float(row["top_y"]))
        for row in candles[-12:]
        if row.get("bottom_y") is not None and row.get("top_y") is not None
    ]
    tolerance = max(3.0, statistics.median(pixel_ranges) * 0.28) if pixel_ranges else 4.0
    for line in _rows(trendlines):
        if line.get("geometry_contract_accepted") is False or line.get("accepted") is False:
            continue
        if str(line.get("geometry_status") or line.get("status") or "").upper().startswith("REJECTED"):
            continue
        role = _side(line.get("role") or line.get("kind") or line.get("type") or line.get("label"))
        if role not in scores:
            continue
        touch_points = line.get("touch_points")
        default_touches = len(touch_points) if isinstance(touch_points, Sequence) else 0
        touch_count = int(_number(line.get("touch_count"), float(default_touches)) or 0)
        mature = bool(line.get("strategy_touch_confirmed")) or touch_count >= 3
        if not mature:
            _record(
                traces,
                rule_id="TRENDLINE_TWO_ANCHOR_CANDIDATE",
                source_kind="TRENDLINE",
                side=role,
                weight=0.0,
                reason=f"The line has {touch_count} verified wick touches; strategy authority requires at least three.",
                observed=False,
            )
            continue
        context["mature_line_count"] += 1
        line_y = _line_y_at_x(line, float(latest["x"]))
        current_touch = _truthy(
            line,
            "current_touch",
            "touching_now",
            "latest_touch",
            "reaction_active",
        )
        close_through = False
        rejection = False
        if line_y is not None and latest.get("close_y") is not None:
            close_y = float(latest["close_y"])
            top_y = float(latest["top_y"] if latest.get("top_y") is not None else close_y)
            bottom_y = float(latest["bottom_y"] if latest.get("bottom_y") is not None else close_y)
            current_touch = current_touch or top_y - tolerance <= line_y <= bottom_y + tolerance
            if role == "SELL":
                close_through = close_y < line_y - tolerance
                rejection = current_touch and close_y > line_y + tolerance * 0.25
            else:
                close_through = close_y > line_y + tolerance
                rejection = current_touch and close_y < line_y - tolerance * 0.25
        else:
            close_through = _truthy(line, "close_through", "broken", "body_break_confirmed")
            rejection = current_touch and not close_through
        if close_through:
            breakout_side = "BUY" if role == "SELL" else "SELL"
            scores[breakout_side] += 2.75
            context.update({"break_side": breakout_side, "best_line": dict(line)})
            _record(
                traces,
                rule_id="TRENDLINE_BODY_CLOSE_BREAK",
                source_kind="TRENDLINE",
                side=breakout_side,
                weight=2.75,
                reason="A completed body closed through a mature wick line, activating break-and-retest behavior.",
            )
        elif rejection:
            scores[role] += 3.0
            context.update(
                {"current_touch": True, "reaction_side": role, "best_line": dict(line)}
            )
            _record(
                traces,
                rule_id="TRENDLINE_THIRD_TOUCH_REJECTION",
                source_kind="TRENDLINE",
                side=role,
                weight=3.0,
                reason="A mature wick line was touched and the completed candle body remained on its defending side.",
            )
        elif current_touch:
            scores[role] += 1.15
            context.update(
                {"current_touch": True, "reaction_side": role, "best_line": dict(line)}
            )
            _record(
                traces,
                rule_id="TRENDLINE_TOUCH_UNCONFIRMED",
                source_kind="TRENDLINE",
                side=role,
                weight=1.15,
                reason="Price is touching a mature line, but completed rejection confirmation is not visible yet.",
            )
    return scores, context


def _zone_rules(
    candles: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
    context_payload: Mapping[str, Any] | None,
    traces: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, Any]]:
    scores = {"BUY": 0.0, "SELL": 0.0}
    candidates = list(_rows(zones))
    context = _mapping(context_payload)
    for key in (
        "zones",
        "active_zones",
        "support_zones",
        "resistance_zones",
        "supply_zones",
        "demand_zones",
    ):
        candidates.extend(_rows(context.get(key)))
    active_sides: list[str] = []
    for zone in candidates:
        role = _side(zone.get("role") or zone.get("kind") or zone.get("type") or zone.get("label"))
        if role not in scores:
            continue
        active = _truthy(
            zone,
            "contains_latest",
            "price_inside",
            "active",
            "touching",
            "reaction_active",
            "is_current",
        )
        if not active and candles and not candles[-1].get("pixel_mode"):
            lower = _pick(zone, "low", "lower", "price_low", "bottom_price")
            upper = _pick(zone, "high", "upper", "price_high", "top_price")
            if lower is not None and upper is not None:
                low_value, high_value = sorted((lower, upper))
                active = low_value <= float(candles[-1]["close"]) <= high_value
        if not active:
            continue
        strength = _clip01(_number(zone.get("confidence") or zone.get("strength"), 0.65) or 0.65)
        weight = 1.0 + 0.8 * strength
        scores[role] += weight
        active_sides.append(role)
        _record(
            traces,
            rule_id="ACTIVE_SUPPORT_DEMAND" if role == "BUY" else "ACTIVE_RESISTANCE_SUPPLY",
            source_kind="STRUCTURE",
            side=role,
            weight=weight,
            reason="The anchor candle is inside or touching an observable reaction zone.",
        )
    return scores, {
        "active_sides": active_sides,
        "active_zone_count": len(active_sides),
    }


def _candle_patterns(
    candles: Sequence[Mapping[str, Any]],
    prior_trend: str,
) -> list[tuple[str, str, float]]:
    if not candles:
        return []
    current = candles[-1]
    body = float(current["body"])
    spread = float(current["range"])
    upper_wick = float(current["high"]) - max(float(current["open"]), float(current["close"]))
    lower_wick = min(float(current["open"]), float(current["close"])) - float(current["low"])
    patterns: list[tuple[str, str, float]] = []
    if body <= spread * 0.1:
        patterns.append(("DOJI_INDECISION", "NEUTRAL", 0.0))
    if lower_wick >= max(body * 2.0, spread * 0.45) and upper_wick <= spread * 0.2:
        patterns.append(
            (
                "HAMMER" if prior_trend == "SELL" else "HANGING_MAN",
                "BUY" if prior_trend == "SELL" else "SELL",
                1.0,
            )
        )
    if upper_wick >= max(body * 2.0, spread * 0.45) and lower_wick <= spread * 0.2:
        patterns.append(
            (
                "SHOOTING_STAR" if prior_trend == "BUY" else "INVERTED_HAMMER",
                "SELL" if prior_trend == "BUY" else "BUY",
                1.0,
            )
        )
    if body >= spread * 0.82 and current["direction"] in {"BUY", "SELL"}:
        patterns.append(
            (
                "BULLISH_MARUBOZU" if current["direction"] == "BUY" else "BEARISH_MARUBOZU",
                str(current["direction"]),
                0.85,
            )
        )
    if len(candles) >= 2:
        previous = candles[-2]
        current_body = sorted((float(current["open"]), float(current["close"])))
        previous_body = sorted((float(previous["open"]), float(previous["close"])))
        if (
            current["direction"] == "BUY"
            and previous["direction"] == "SELL"
            and current_body[0] <= previous_body[0]
            and current_body[1] >= previous_body[1]
        ):
            patterns.append(("BULLISH_ENGULFING", "BUY", 1.35))
        if (
            current["direction"] == "SELL"
            and previous["direction"] == "BUY"
            and current_body[0] <= previous_body[0]
            and current_body[1] >= previous_body[1]
        ):
            patterns.append(("BEARISH_ENGULFING", "SELL", 1.35))
        if current_body[0] >= previous_body[0] and current_body[1] <= previous_body[1]:
            patterns.append(
                (
                    "BULLISH_HARAMI" if current["direction"] == "BUY" else "BEARISH_HARAMI",
                    str(current["direction"]),
                    0.65,
                )
            )
        tolerance = statistics.median((float(current["range"]), float(previous["range"]))) * 0.12
        if abs(float(current["low"]) - float(previous["low"])) <= tolerance:
            patterns.append(("TWEEZER_BOTTOM", "BUY", 0.75))
        if abs(float(current["high"]) - float(previous["high"])) <= tolerance:
            patterns.append(("TWEEZER_TOP", "SELL", 0.75))
    if len(candles) >= 3:
        first, middle, last = candles[-3:]
        directions = [str(row["direction"]) for row in (first, middle, last)]
        if directions == ["BUY", "BUY", "BUY"] and middle["close"] > first["close"] and last["close"] > middle["close"]:
            patterns.append(("THREE_WHITE_SOLDIERS", "BUY", 1.25))
        if directions == ["SELL", "SELL", "SELL"] and middle["close"] < first["close"] and last["close"] < middle["close"]:
            patterns.append(("THREE_BLACK_CROWS", "SELL", 1.25))
        midpoint = (float(first["open"]) + float(first["close"])) / 2.0
        if first["direction"] == "SELL" and float(middle["body"]) <= float(first["body"]) * 0.45 and last["direction"] == "BUY" and float(last["close"]) > midpoint:
            patterns.append(("MORNING_STAR", "BUY", 1.55))
        if first["direction"] == "BUY" and float(middle["body"]) <= float(first["body"]) * 0.45 and last["direction"] == "SELL" and float(last["close"]) < midpoint:
            patterns.append(("EVENING_STAR", "SELL", 1.55))
    return patterns


def _packet_side(payload: Mapping[str, Any] | None) -> tuple[str, float]:
    data = _mapping(payload)
    side = _side(
        data.get("forecast_side")
        or data.get("dominant_side")
        or data.get("direction")
        or data.get("side")
        or data.get("decision")
        or data.get("action")
    )
    confidence = _clip01(
        _number(
            data.get("confidence")
            or data.get("probability")
            or data.get("strength")
            or data.get("bias_strength"),
            0.0,
        )
        or 0.0
    )
    return side, confidence


def _smart_money_rule(payload: Mapping[str, Any] | None) -> tuple[str, float, str]:
    data = _mapping(payload)
    side, confidence = _packet_side(data)
    text = " ".join(f"{key}={value}" for key, value in data.items()).upper()
    if side == "NEUTRAL":
        side = _side(text)
    event = (
        "LIQUIDITY_SWEEP_RECLAIM"
        if any(token in text for token in ("SWEEP", "STOP_HUNT", "RECLAIM"))
        else "STRUCTURE_SHIFT"
    )
    return side, confidence or 0.55, event


def _phase_path(
    primary_side: str,
    major_side: str,
    playbook: str,
    confidence: float,
) -> tuple[list[float], list[str]]:
    sign = 1.0 if primary_side == "BUY" else -1.0
    major_sign = 1.0 if major_side == "BUY" else -1.0 if major_side == "SELL" else sign
    if playbook in {
        "TRENDLINE_REJECTION",
        "CANDLE_REVERSAL_AT_STRUCTURE",
        "LIQUIDITY_SWEEP_RECLAIM",
    }:
        phases = [(10, sign, 1.0), (4, sign, 0.18), (7, -sign, 0.48), (14, sign, 0.78)]
    elif playbook in {"BREAK_RETEST", "ROLE_FLIP_RETEST"}:
        phases = [(8, sign, 0.9), (6, -sign, 0.55), (18, sign, 0.95), (5, sign, 0.2)]
    else:
        phases = [(14, sign, 0.82), (5, sign, 0.15), (8, -sign, 0.42), (18, sign, 0.86)]
    used = sum(length for length, _, _ in phases)
    phases.append((FORECAST_HORIZON_CANDLES_V3 - used, major_sign, 0.68 if major_sign == sign else 0.58))
    amplitude = 0.72 + 0.28 * confidence
    multipliers: list[float] = []
    directions: list[str] = []
    for length, phase_sign, strength in phases:
        for step in range(max(0, length)):
            wave = (0.86, 1.08, 0.72, 1.16, 0.91)[step % 5]
            value = float(phase_sign) * float(strength) * amplitude * wave
            multipliers.append(round(value, 6))
            directions.append("BUY" if value > 0.08 else "SELL" if value < -0.08 else "REST")
    return multipliers[:FORECAST_HORIZON_CANDLES_V3], directions[:FORECAST_HORIZON_CANDLES_V3]


def build_book_strategy_forecast_control_v3(
    *,
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    trendlines: Sequence[Mapping[str, Any]] | None = None,
    support_resistance_context: Mapping[str, Any] | None = None,
    support_resistance_zones: Sequence[Mapping[str, Any]] | None = None,
    smart_money_context: Mapping[str, Any] | None = None,
    behavior_payload: Mapping[str, Any] | None = None,
    decision_kernel: Mapping[str, Any] | None = None,
    trend_directions: Mapping[str, Any] | None = None,
    book_strategy: Mapping[str, Any] | None = None,
    playbook_ai_intelligence: Mapping[str, Any] | None = None,
    session_context: Mapping[str, Any] | None = None,
    news_context: Mapping[str, Any] | None = None,
    pair_dna_context: Mapping[str, Any] | None = None,
    higher_timeframe_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic strategy control using visible history only."""

    rows = _normalise_candles(candles)
    traces: list[dict[str, Any]] = []
    scores = {"BUY": 0.0, "SELL": 0.0}
    explicit_major, explicit_inner = _explicit_trends(trend_directions)
    derived_major, derived_inner = _derived_trends(rows)
    major_side = explicit_major if explicit_major != "NEUTRAL" else derived_major
    inner_side = explicit_inner if explicit_inner != "NEUTRAL" else derived_inner
    context_suite = evaluate_book_strategy_context_v3(
        candles=rows,
        timeframe=timeframe,
        trend_directions=trend_directions,
        higher_timeframe_context=higher_timeframe_context,
        support_resistance_zones=support_resistance_zones,
        smart_money_context=smart_money_context,
        pair_dna_context=pair_dna_context,
        behavior_payload=behavior_payload,
        session_context=session_context,
        news_context=news_context,
        trendlines=trendlines,
    )
    higher_timeframe = _mapping(context_suite.get("higher_timeframe"))
    effective_htf_side = _side(higher_timeframe.get("effective_side"))
    if higher_timeframe.get("strictly_enforced") and effective_htf_side in scores:
        major_side = effective_htf_side
    for direction in scores:
        scores[direction] += _number(
            _mapping(context_suite.get("score_adjustments")).get(direction),
            0.0,
        ) or 0.0
    traces.extend(_rows(context_suite.get("rule_trace")))
    if major_side in scores:
        scores[major_side] += 1.35
        _record(
            traces,
            rule_id="MAJOR_STRUCTURE_DIRECTION",
            source_kind="STRUCTURE",
            side=major_side,
            weight=1.35,
            reason="Higher-order visible structure establishes the controlling context.",
        )
    if inner_side in scores:
        scores[inner_side] += 0.65
        _record(
            traces,
            rule_id="INNER_STRUCTURE_DIRECTION",
            source_kind="STRUCTURE",
            side=inner_side,
            weight=0.65,
            reason="The inner swing controls the near-term phase without overruling major structure.",
        )

    structure_break_side = "NEUTRAL"
    if len(rows) >= 5:
        lookback = rows[-9:-1]
        prior_high = max(float(row["high"]) for row in lookback)
        prior_low = min(float(row["low"]) for row in lookback)
        close = float(rows[-1]["close"])
        structure_break_side = "BUY" if close > prior_high else "SELL" if close < prior_low else "NEUTRAL"
        if structure_break_side in scores:
            scores[structure_break_side] += 1.8
            _record(
                traces,
                rule_id="COMPLETED_CLOSE_STRUCTURE_BREAK",
                source_kind="STRUCTURE",
                side=structure_break_side,
                weight=1.8,
                reason="The anchor candle completed beyond the prior visible swing boundary.",
            )

    line_scores, line_context = _trendline_rules(rows, trendlines or [], traces)
    zone_scores, zone_context = _zone_rules(
        rows,
        support_resistance_zones or [],
        support_resistance_context,
        traces,
    )
    for direction in scores:
        scores[direction] += line_scores[direction] + zone_scores[direction]

    location_present = bool(line_context["current_touch"] or zone_context["active_zone_count"])
    reaction_location_side = str(line_context.get("reaction_side") or "NEUTRAL")
    active_zone_sides = list(zone_context.get("active_sides") or [])
    location_side = (
        reaction_location_side
        if reaction_location_side in scores
        else active_zone_sides[0]
        if len(set(active_zone_sides)) == 1
        else "NEUTRAL"
    )
    candlestick_catalog = evaluate_candlestick_catalog_v3(
        candles=rows,
        prior_trend=inner_side if inner_side != "NEUTRAL" else major_side,
        location_side=location_side,
        higher_timeframe_side=effective_htf_side,
        location_history=_mapping(context_suite.get("candle_location_history")),
    )
    book_scanner = rank_book_scanner_v3(
        candlestick_catalog,
        _mapping(context_suite.get("full_non_indicator_stack_v3")),
        effective_htf_side,
    )
    calibration_multipliers = _mapping(
        _mapping(context_suite.get("rule_calibration_v3")).get("multipliers")
    )
    confirmed_pattern_sides: list[str] = []
    for detection in _rows(candlestick_catalog.get("detections")):
        pattern = str(detection.get("rule_id") or "UNKNOWN_PATTERN")
        pattern_side = _side(detection.get("side"))
        authority = bool(detection.get("directional_authority"))
        calibration_multiplier = _number(calibration_multipliers.get(pattern), 1.0) or 1.0
        weight = (_number(detection.get("weight"), 0.0) or 0.0) * calibration_multiplier
        if pattern_side in scores and authority:
            scores[pattern_side] += weight
            confirmed_pattern_sides.append(pattern_side)
        _record(
            traces,
            rule_id=pattern,
            source_kind="CANDLE",
            side=pattern_side,
            weight=weight,
            reason=(
                "Pattern geometry, prior trend, structural location, confirmation close, and timeframe context all passed."
                if authority
                else "Pattern geometry was recognized but failed: "
                + ", ".join(str(value) for value in detection.get("failed_requirements", []))
            ),
            observed=authority,
            provenance={
                "source_file": detection.get("source_file"),
                "printed_pages": detection.get("printed_pages"),
                "pdf_pages": detection.get("pdf_pages"),
            },
        )

    smc_side, smc_confidence, smc_event = _smart_money_rule(smart_money_context)
    if smc_side in scores:
        weight = 0.8 + 0.9 * smc_confidence
        scores[smc_side] += weight
        _record(
            traces,
            rule_id=smc_event,
            source_kind="SMC",
            side=smc_side,
            weight=weight,
            reason="Visible liquidity or structure-shift behavior supplies directional confluence.",
        )

    for packet, rule_id, maximum_weight in (
        (book_strategy, "EXISTING_BOOK_ENGINE", 0.9),
        (playbook_ai_intelligence, "PLAYBOOK_AI_EVIDENCE", 0.65),
        (behavior_payload, "PAIR_BEHAVIOR_STATE", 0.55),
        (decision_kernel, "HIDDEN_STATE_BELIEF", 0.45),
    ):
        packet_side, packet_confidence = _packet_side(packet)
        if packet_side in scores and packet_confidence > 0.0:
            weight = maximum_weight * packet_confidence
            scores[packet_side] += weight
            _record(
                traces,
                rule_id=rule_id,
                source_kind="CONFLUENCE",
                side=packet_side,
                weight=weight,
                reason="Existing V3 intelligence contributes as confluence and cannot overrule completed geometry alone.",
            )

    reaction_side = str(line_context.get("reaction_side") or "NEUTRAL")
    break_side = str(line_context.get("break_side") or "NEUTRAL")
    immediate_side = (
        reaction_side
        if reaction_side in scores
        else break_side
        if break_side in scores
        else structure_break_side
    )
    if immediate_side not in scores and confirmed_pattern_sides:
        immediate_side = confirmed_pattern_sides[-1]
    winner = "BUY" if scores["BUY"] >= scores["SELL"] else "SELL"
    if immediate_side not in scores:
        immediate_side = winner

    total = scores["BUY"] + scores["SELL"]
    margin = abs(scores["BUY"] - scores["SELL"]) / max(1e-9, total)
    active_rules = sum(
        1 for row in traces if bool(row["observed"]) and float(row["weight"]) > 0.0
    )
    evidence_quality = min(1.0, active_rules / 7.0)
    geometry_quality = 1.0 if location_present else 0.35 if line_context["mature_line_count"] else 0.15
    confidence = _clip01(
        0.48 * margin + 0.32 * evidence_quality + 0.20 * geometry_quality
    )

    hlz_sequence = _mapping(context_suite.get("hlz_sequence"))
    role_flip = _mapping(context_suite.get("role_flip"))
    if role_flip.get("complete"):
        playbook = "ROLE_FLIP_RETEST"
    elif hlz_sequence.get("entry_sequence_ready") and hlz_sequence.get("stop_hunt"):
        playbook = "STOP_HUNT_BMS_RTO"
    elif hlz_sequence.get("entry_sequence_ready"):
        playbook = "BMS_OTE_RTO"
    elif reaction_side in scores:
        playbook = "TRENDLINE_REJECTION"
    elif break_side in scores:
        playbook = "BREAK_RETEST"
    elif smc_event == "LIQUIDITY_SWEEP_RECLAIM" and smc_side in scores:
        playbook = "LIQUIDITY_SWEEP_RECLAIM"
    elif confirmed_pattern_sides and location_present:
        playbook = "CANDLE_REVERSAL_AT_STRUCTURE"
    elif structure_break_side in scores:
        playbook = "STRUCTURE_CONTINUATION"
    elif zone_context["active_zone_count"]:
        playbook = "RANGE_REACTION"
    else:
        playbook = "UNRESOLVED"

    temporal = _mapping(context_suite.get("temporal"))
    htf_entry_aligned = bool(
        not higher_timeframe.get("strictly_enforced")
        or immediate_side == effective_htf_side
    )
    aggressive_ready = bool(
        line_context["current_touch"]
        or hlz_sequence.get("stop_hunt")
        or role_flip.get("complete")
    )
    conservative_ready = bool(
        hlz_sequence.get("entry_sequence_ready")
        or (confirmed_pattern_sides and location_present)
        or break_side in scores
    )
    entry_actionable = bool(
        confidence >= 0.55
        and htf_entry_aligned
        and not temporal.get("entry_suspended_until_news_pivot")
        and (
            conservative_ready
            or (aggressive_ready and reaction_side in confirmed_pattern_sides)
            or break_side in scores
            or (structure_break_side == winner and smc_side == winner)
        )
    )
    entry_profile = (
        "CONSERVATIVE_CLOSE_CONFIRMED"
        if entry_actionable
        else "AGGRESSIVE_TOUCH_OBSERVATION"
        if line_context["current_touch"]
        else "NONE"
    )
    pair_horizon = build_pair_conditioned_horizon_v3(
        context_suite,
        primary_side=immediate_side,
        confidence=confidence,
    )
    phase_multipliers = list(pair_horizon.get("phase_multipliers") or [])
    horizon_directions = list(pair_horizon.get("horizon_directions") or [])
    path_amplitude = max(0.55, min(1.65, _number(context_suite.get("path_amplitude"), 1.0) or 1.0))
    phase_multipliers = [round(value * path_amplitude, 6) for value in phase_multipliers]
    cumulative = sum(phase_multipliers)
    terminal_side = (
        effective_htf_side
        if higher_timeframe.get("strictly_enforced") and effective_htf_side in scores
        else "BUY"
        if cumulative > 0.0
        else "SELL"
        if cumulative < 0.0
        else winner
    )
    invalidation = (
        "Completed body close through the mature defending trendline."
        if reaction_side in scores
        else "Completed close back through the broken line before its retest holds."
        if break_side in scores
        else "Completed close beyond the opposite boundary of the active reaction zone."
        if zone_context["active_zone_count"]
        else "A completed opposing structure break invalidates the directional sequence."
    )
    return {
        "schema": BOOK_STRATEGY_FORECAST_SCHEMA_V3,
        "version": 1,
        "future_blind": True,
        "timeframe": str(timeframe or "UNKNOWN").strip().upper(),
        "observed_candle_count": len(rows),
        "forecast_horizon_candles": FORECAST_HORIZON_CANDLES_V3,
        "forecast_side": terminal_side,
        "initial_reaction_side": immediate_side,
        "major_structure_side": major_side,
        "inner_structure_side": inner_side,
        "confidence": round(confidence, 6),
        "buy_score": round(scores["BUY"], 6),
        "sell_score": round(scores["SELL"], 6),
        "score_margin": round(margin, 6),
        "playbook": playbook,
        "entry_actionable": entry_actionable,
        "entry_profile": entry_profile,
        "entry_profiles": {
            "aggressive": {
                "ready": aggressive_ready and htf_entry_aligned and not temporal.get("entry_suspended_until_news_pivot"),
                "requires": ["MATURE_STRUCTURE_TOUCH_OR_SWEEP", "VISIBLE_REJECTION", "HTF_NOT_OPPOSING"],
            },
            "conservative": {
                "ready": conservative_ready and htf_entry_aligned and not temporal.get("entry_suspended_until_news_pivot"),
                "requires": ["COMPLETED_CONFIRMATION_CLOSE", "BMS_RETRACEMENT_OR_RETEST", "HTF_ALIGNMENT"],
            },
        },
        "invalidation": invalidation,
        "opposing_force_present": major_side in scores and major_side != immediate_side,
        "phase_multipliers": phase_multipliers,
        "horizon_directions": horizon_directions,
        "trendline_context": line_context,
        "zone_context": zone_context,
        "candlestick_patterns": list(candlestick_catalog.get("recognized_pattern_ids", [])),
        "candlestick_catalog_v3": candlestick_catalog,
        "book_scanner_ranking_v3": book_scanner,
        "pair_conditioned_horizon_v3": pair_horizon,
        "hlz_sequence_v3": hlz_sequence,
        "fibonacci_ote_v3": _mapping(context_suite.get("fibonacci_ote")),
        "higher_timeframe_authority_v3": higher_timeframe,
        "role_flip_sequence_v3": role_flip,
        "pair_dna_forecast_context_v3": _mapping(context_suite.get("pair_dna")),
        "session_news_context_v3": temporal,
        "market_structure_full_v3": _mapping(context_suite.get("market_structure_full_v3")),
        "trendline_contracts_full_v3": _mapping(context_suite.get("trendline_contracts_full_v3")),
        "order_blocks_full_v3": _mapping(context_suite.get("order_blocks_full_v3")),
        "liquidity_turtle_soup_v3": _mapping(context_suite.get("liquidity_turtle_soup_v3")),
        "amd_v3": _mapping(context_suite.get("amd_v3")),
        "news_pivot_v3": _mapping(context_suite.get("news_pivot_v3")),
        "sakata_v3": _mapping(context_suite.get("sakata_v3")),
        "opposing_force_targets_v3": _mapping(context_suite.get("opposing_targets")),
        "technical_indicators_used": False,
        "technical_indicator_scope": "EXCLUDED_BY_USER",
        "rule_traceability_v3": {
            "complete": bool(
                [row for row in traces if row.get("book_page_required") is not False]
            ) and all(
                bool(row.get("source_file")) and bool(row.get("pdf_pages"))
                for row in traces
                if row.get("book_page_required") is not False
            ),
            "catalog_pattern_count": len(CANDLESTICK_RULE_CATALOG_V3),
            "trace_count": len(traces),
            "book_trace_count": sum(
                1 for row in traces if row.get("book_page_required") is not False
            ),
            "internal_evidence_count": sum(
                1 for row in traces if row.get("book_page_required") is False
            ),
        },
        "rule_trace": traces,
        "unobserved_context": [
            "broker_order_flow",
            "news_release_state",
            "session_liquidity_if_not_visible",
            "spread_and_slippage",
        ],
        "execution_authority": False,
    }


__all__ = [
    "BOOK_STRATEGY_FORECAST_SCHEMA_V3",
    "FORECAST_HORIZON_CANDLES_V3",
    "build_book_strategy_forecast_control_v3",
]
