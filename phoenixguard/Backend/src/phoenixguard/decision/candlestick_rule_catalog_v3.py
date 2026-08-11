"""Complete Phoenix Guard V3 catalogue for the supplied candlestick book.

The catalogue is deliberately data-first: every pattern has stable identity,
printed-page and PDF-page provenance, prior-trend requirements, structural
location requirements, and confirmation semantics. Detection and directional
authority are separate so a shape cannot become a forecast merely because its
geometry resembles a named candle.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any


CANDLESTICK_CATALOG_SCHEMA_V3 = "PG_CANDLESTICK_RULE_CATALOG_V3"
CANDLESTICK_CATALOG_VERSION_V3 = 1
CANDLESTICK_SOURCE_FILE_V3 = (
    "The power of Japanese candlestick charts _ advanced filtering techniques "
    "for trading stocks, futures and Forex ( PDFDrive ).pdf"
)


def _spec(
    rule_id: str,
    side: str,
    family: str,
    bars: int,
    prior_trend: str,
    printed_start: int,
    printed_end: int,
    *,
    confirmation: str = "SELF_CONFIRMED",
    location_required: bool = True,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "side": side,
        "family": family,
        "bars": bars,
        "required_prior_trend": prior_trend,
        "required_location_side": side if location_required else "ANY",
        "confirmation": confirmation,
        "printed_pages": [printed_start, printed_end],
        "pdf_pages": [printed_start + 24, printed_end + 24],
        "source_file": CANDLESTICK_SOURCE_FILE_V3,
        "source_section": family,
        "technical_indicator": False,
        "price_location_mode": (
            "REVERSAL_EXTREME"
            if family.startswith("REVERSAL")
            else "TREND_CONTINUATION"
        ),
    }


CANDLESTICK_RULE_CATALOG_V3: tuple[dict[str, Any], ...] = (
    _spec("SPINNING_TOP_BOTTOM", "BUY", "REVERSAL_SINGLE", 1, "SELL", 70, 72, confirmation="NEXT_CLOSE"),
    _spec("SPINNING_TOP_TOP", "SELL", "REVERSAL_SINGLE", 1, "BUY", 70, 72, confirmation="NEXT_CLOSE"),
    _spec("HAMMER", "BUY", "REVERSAL_SINGLE", 1, "SELL", 72, 73, confirmation="NEXT_CLOSE"),
    _spec("HANGING_MAN", "SELL", "REVERSAL_SINGLE", 1, "BUY", 72, 73, confirmation="NEXT_CLOSE"),
    _spec("INVERTED_HAMMER", "BUY", "REVERSAL_SINGLE", 1, "SELL", 75, 78, confirmation="NEXT_CLOSE"),
    _spec("SHOOTING_STAR", "SELL", "REVERSAL_SINGLE", 1, "BUY", 75, 78, confirmation="NEXT_CLOSE"),
    _spec("DOJI_BOTTOM", "BUY", "REVERSAL_SINGLE", 1, "SELL", 78, 81, confirmation="NEXT_CLOSE"),
    _spec("DOJI_TOP", "SELL", "REVERSAL_SINGLE", 1, "BUY", 78, 81, confirmation="NEXT_CLOSE"),
    _spec("BULLISH_MEETING_LINE", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 81, 84),
    _spec("BEARISH_MEETING_LINE", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 81, 84),
    _spec("BULLISH_BELT_HOLD", "BUY", "REVERSAL_SINGLE", 1, "SELL", 84, 87, confirmation="NEXT_CLOSE"),
    _spec("BEARISH_BELT_HOLD", "SELL", "REVERSAL_SINGLE", 1, "BUY", 84, 87, confirmation="NEXT_CLOSE"),
    _spec("BULLISH_ENGULFING", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 90, 91),
    _spec("BEARISH_ENGULFING", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 90, 91),
    _spec("WHITE_INSIDE_OUT_UP", "BUY", "REVERSAL_TRIPLE", 3, "SELL", 95, 96),
    _spec("BLACK_INSIDE_OUT_DOWN", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 95, 96),
    _spec("PIERCING_LINE", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 99, 100),
    _spec("DARK_CLOUD_COVER", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 99, 100),
    _spec("BULLISH_THRUSTING_LINE", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 100, 104, confirmation="NEXT_CLOSE"),
    _spec("INCOMPLETE_DARK_CLOUD_COVER", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 100, 104, confirmation="NEXT_CLOSE"),
    _spec("BULLISH_HARAMI", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 104, 107, confirmation="NEXT_CLOSE"),
    _spec("BEARISH_HARAMI", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 104, 107, confirmation="NEXT_CLOSE"),
    _spec("BULLISH_HARAMI_CROSS", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 110, 111, confirmation="NEXT_CLOSE"),
    _spec("BEARISH_HARAMI_CROSS", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 110, 111, confirmation="NEXT_CLOSE"),
    _spec("HOMING_PIGEON", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 114, 115, confirmation="NEXT_CLOSE"),
    _spec("BEARISH_HOMING_PIGEON", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 114, 115, confirmation="NEXT_CLOSE"),
    _spec("TWEEZERS_BOTTOM", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 115, 119, confirmation="NEXT_CLOSE"),
    _spec("TWEEZERS_TOP", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 115, 119, confirmation="NEXT_CLOSE"),
    _spec("DOJI_STAR_BOTTOM", "BUY", "REVERSAL_DOUBLE", 2, "SELL", 119, 123, confirmation="NEXT_CLOSE"),
    _spec("DOJI_STAR_TOP", "SELL", "REVERSAL_DOUBLE", 2, "BUY", 119, 123, confirmation="NEXT_CLOSE"),
    _spec("THREE_RIVER_MORNING_DOJI_STAR", "BUY", "REVERSAL_TRIPLE", 3, "SELL", 123, 126),
    _spec("THREE_RIVER_EVENING_DOJI_STAR", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 123, 126),
    _spec("ABANDONED_BABY_BOTTOM", "BUY", "REVERSAL_TRIPLE", 3, "SELL", 126, 131),
    _spec("ABANDONED_BABY_TOP", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 126, 131),
    _spec("THREE_RIVER_MORNING_STAR", "BUY", "REVERSAL_TRIPLE", 3, "SELL", 131, 134),
    _spec("THREE_RIVER_EVENING_STAR", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 131, 134),
    _spec("TRI_STAR_BOTTOM", "BUY", "REVERSAL_TRIPLE", 3, "SELL", 134, 137),
    _spec("TRI_STAR_TOP", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 134, 137),
    _spec("BREAKAWAY_THREE_NEW_PRICE_BOTTOM", "BUY", "REVERSAL_MULTIPLE", 5, "SELL", 137, 140),
    _spec("BREAKAWAY_THREE_NEW_PRICE_TOP", "SELL", "REVERSAL_MULTIPLE", 5, "BUY", 137, 140),
    _spec("BULLISH_BLACK_THREE_GAPS", "BUY", "REVERSAL_MULTIPLE", 4, "SELL", 143, 144),
    _spec("BEARISH_WHITE_THREE_GAPS", "SELL", "REVERSAL_MULTIPLE", 4, "BUY", 143, 144),
    _spec("THREE_WHITE_SOLDIERS", "BUY", "REVERSAL_TRIPLE", 3, "SELL", 147, 148),
    _spec("THREE_BLACK_CROWS", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 147, 148),
    _spec("ADVANCE_BLOCK", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 151, 153, confirmation="NEXT_CLOSE"),
    _spec("DELIBERATION", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 153, 154, confirmation="NEXT_CLOSE"),
    _spec("UPSIDE_GAP_TWO_CROWS", "SELL", "REVERSAL_TRIPLE", 3, "BUY", 154, 156),
    _spec("CONCEALING_BABY_SWALLOW", "BUY", "REVERSAL_MULTIPLE", 4, "SELL", 156, 158),
    _spec("LADDER_BOTTOM", "BUY", "REVERSAL_MULTIPLE", 5, "SELL", 158, 160),
    _spec("TOWER_BOTTOM", "BUY", "REVERSAL_MULTIPLE", 5, "SELL", 160, 163),
    _spec("TOWER_TOP", "SELL", "REVERSAL_MULTIPLE", 5, "BUY", 160, 163),
    _spec("EIGHT_TO_TEN_NEW_RECORD_LOWS", "BUY", "REVERSAL_MULTIPLE", 8, "SELL", 163, 167, confirmation="NEXT_CLOSE"),
    _spec("EIGHT_TO_TEN_NEW_RECORD_HIGHS", "SELL", "REVERSAL_MULTIPLE", 8, "BUY", 163, 167, confirmation="NEXT_CLOSE"),
    _spec("BULLISH_SEPARATING_LINES", "BUY", "CONTINUATION_DOUBLE", 2, "BUY", 172, 176, location_required=False),
    _spec("BEARISH_SEPARATING_LINES", "SELL", "CONTINUATION_DOUBLE", 2, "SELL", 172, 176, location_required=False),
    _spec("BULLISH_KICKING", "BUY", "REVERSAL_OR_CONTINUATION", 2, "NEUTRAL", 176, 180, location_required=False),
    _spec("BEARISH_KICKING", "SELL", "REVERSAL_OR_CONTINUATION", 2, "NEUTRAL", 176, 180, location_required=False),
    _spec("ON_NECK", "SELL", "CONTINUATION_DOUBLE", 2, "SELL", 180, 181, location_required=False),
    _spec("IN_NECK", "SELL", "CONTINUATION_DOUBLE", 2, "SELL", 181, 183, location_required=False),
    _spec("BEARISH_THRUSTING_LINE", "SELL", "CONTINUATION_DOUBLE", 2, "SELL", 183, 185, location_required=False),
    _spec("RISING_THREE_METHODS", "BUY", "CONTINUATION_MULTIPLE", 5, "BUY", 185, 187, location_required=False),
    _spec("FALLING_THREE_METHODS", "SELL", "CONTINUATION_MULTIPLE", 5, "SELL", 187, 189, location_required=False),
    _spec("BULLISH_MAT_HOLD", "BUY", "CONTINUATION_MULTIPLE", 5, "BUY", 190, 192, location_required=False),
    _spec("BEARISH_MAT_HOLD", "SELL", "CONTINUATION_MULTIPLE", 5, "SELL", 190, 192, location_required=False),
    _spec("RISING_WINDOW", "BUY", "CONTINUATION_GAP", 2, "BUY", 192, 195, location_required=False),
    _spec("FALLING_WINDOW", "SELL", "CONTINUATION_GAP", 2, "SELL", 192, 195, location_required=False),
    _spec("TASUKI_UPSIDE_GAP", "BUY", "CONTINUATION_TRIPLE", 3, "BUY", 195, 195, location_required=False),
    _spec("TASUKI_DOWNSIDE_GAP", "SELL", "CONTINUATION_TRIPLE", 3, "SELL", 195, 196, location_required=False),
    _spec("UP_GAP_SIDE_BY_SIDE_WHITE_LINES", "BUY", "CONTINUATION_TRIPLE", 3, "BUY", 199, 199, location_required=False),
    _spec("DOWN_GAP_SIDE_BY_SIDE_WHITE_LINES", "SELL", "CONTINUATION_TRIPLE", 3, "SELL", 199, 200, location_required=False),
    _spec("HIGH_PRICE_GAPPING_PLAY", "BUY", "CONTINUATION_MULTIPLE", 5, "BUY", 203, 203, location_required=False),
    _spec("LOW_PRICE_GAPPING_PLAY", "SELL", "CONTINUATION_MULTIPLE", 5, "SELL", 203, 204, location_required=False),
)


ADVANCED_CANDLE_FILTER_CATALOG_V3: tuple[dict[str, Any], ...] = tuple(
    {
        "rule_id": rule_id,
        "printed_pages": [start, end],
        "pdf_pages": [start + 24, end + 24],
        "source_file": CANDLESTICK_SOURCE_FILE_V3,
        "requires_observable_input": required,
    }
    for rule_id, start, end, required in (
        ("MULTIPLE_TECHNIQUE_FILTER", 216, 216, "PRICE_STRUCTURE"),
        ("SAKATA_FIVE_METHODS", 253, 267, "CLOSED_CANDLES_AND_SWINGS"),
        ("COMPUTERIZED_CANDLE_SCANNING", 273, 284, "CLOSED_CANDLE_GEOMETRY"),
    )
)

EXCLUDED_CONVENTIONAL_INDICATOR_RULES_V3: tuple[str, ...] = (
    "MOVING_AVERAGE_FILTER",
    "MACD_FILTER",
    "RSI_FILTER",
    "STOCHASTIC_FILTER",
    "MOMENTUM_FILTER",
    "WILLIAMS_PERCENT_RETRACEMENT_FILTER",
    "DIRECTIONAL_MOVEMENT_FILTER",
    "COMMODITY_CHANNEL_FILTER",
    "VOLUME_FILTER",
    "BOLLINGER_FILTER",
)


def _value(row: Mapping[str, Any], key: str) -> float:
    try:
        value = float(row.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _up(row: Mapping[str, Any]) -> bool:
    return _value(row, "close") > _value(row, "open")


def _down(row: Mapping[str, Any]) -> bool:
    return _value(row, "close") < _value(row, "open")


def _body_low(row: Mapping[str, Any]) -> float:
    return min(_value(row, "open"), _value(row, "close"))


def _body_high(row: Mapping[str, Any]) -> float:
    return max(_value(row, "open"), _value(row, "close"))


def _spread(row: Mapping[str, Any]) -> float:
    return max(1e-9, _value(row, "high") - _value(row, "low"))


def _body(row: Mapping[str, Any]) -> float:
    return abs(_value(row, "close") - _value(row, "open"))


def _upper_wick(row: Mapping[str, Any]) -> float:
    return max(0.0, _value(row, "high") - _body_high(row))


def _lower_wick(row: Mapping[str, Any]) -> float:
    return max(0.0, _body_low(row) - _value(row, "low"))


def _doji(row: Mapping[str, Any]) -> bool:
    return _body(row) <= 0.1 * _spread(row)


def _small(row: Mapping[str, Any]) -> bool:
    return _body(row) <= 0.35 * _spread(row)


def _long(row: Mapping[str, Any]) -> bool:
    return _body(row) >= 0.6 * _spread(row)


def _marubozu(row: Mapping[str, Any]) -> bool:
    return _long(row) and _upper_wick(row) <= 0.08 * _spread(row) and _lower_wick(row) <= 0.08 * _spread(row)


def _near(left: float, right: float, scale: float, fraction: float = 0.12) -> bool:
    return abs(left - right) <= max(1e-9, scale * fraction)


def _gap_up(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _value(right, "low") > _value(left, "high")


def _gap_down(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _value(right, "high") < _value(left, "low")


def _inside(inner: Mapping[str, Any], outer: Mapping[str, Any]) -> bool:
    return _body_low(inner) >= _body_low(outer) and _body_high(inner) <= _body_high(outer)


def _spinning(row: Mapping[str, Any]) -> bool:
    return _small(row) and _upper_wick(row) >= _body(row) * 0.6 and _lower_wick(row) >= _body(row) * 0.6


def _umbrella(row: Mapping[str, Any]) -> bool:
    return _lower_wick(row) >= max(_body(row) * 2.0, _spread(row) * 0.45) and _upper_wick(row) <= _spread(row) * 0.2


def _inverted_umbrella(row: Mapping[str, Any]) -> bool:
    return _upper_wick(row) >= max(_body(row) * 2.0, _spread(row) * 0.45) and _lower_wick(row) <= _spread(row) * 0.2


def _three_progressive(rows: Sequence[Mapping[str, Any]], side: str) -> bool:
    if len(rows) < 3:
        return False
    a, b, c = rows[-3:]
    if side == "BUY":
        return all(_up(row) and _long(row) for row in (a, b, c)) and _value(a, "close") < _value(b, "close") < _value(c, "close")
    return all(_down(row) and _long(row) for row in (a, b, c)) and _value(a, "close") > _value(b, "close") > _value(c, "close")


def _matches(rule_id: str, candles: Sequence[Mapping[str, Any]]) -> bool:
    if not candles:
        return False
    c = candles[-1]
    p = candles[-2] if len(candles) >= 2 else {}
    a = candles[-3] if len(candles) >= 3 else {}
    if rule_id.startswith("SPINNING_TOP"):
        return _spinning(c)
    if rule_id in {"HAMMER", "HANGING_MAN"}:
        return _umbrella(c)
    if rule_id in {"INVERTED_HAMMER", "SHOOTING_STAR"}:
        return _inverted_umbrella(c)
    if rule_id.startswith("DOJI_") and "STAR" not in rule_id:
        return _doji(c)
    if rule_id == "BULLISH_MEETING_LINE":
        return _down(p) and _up(c) and _near(_value(c, "close"), _value(p, "close"), max(_spread(c), _spread(p)))
    if rule_id == "BEARISH_MEETING_LINE":
        return _up(p) and _down(c) and _near(_value(c, "close"), _value(p, "close"), max(_spread(c), _spread(p)))
    if rule_id == "BULLISH_BELT_HOLD":
        return _up(c) and _long(c) and _lower_wick(c) <= 0.06 * _spread(c)
    if rule_id == "BEARISH_BELT_HOLD":
        return _down(c) and _long(c) and _upper_wick(c) <= 0.06 * _spread(c)
    if rule_id == "BULLISH_ENGULFING":
        return _down(p) and _up(c) and _body_low(c) <= _body_low(p) and _body_high(c) >= _body_high(p)
    if rule_id == "BEARISH_ENGULFING":
        return _up(p) and _down(c) and _body_low(c) <= _body_low(p) and _body_high(c) >= _body_high(p)
    if rule_id == "WHITE_INSIDE_OUT_UP":
        return len(candles) >= 3 and _inside(p, a) and _up(c) and _value(c, "close") > _value(a, "high")
    if rule_id == "BLACK_INSIDE_OUT_DOWN":
        return len(candles) >= 3 and _inside(p, a) and _down(c) and _value(c, "close") < _value(a, "low")
    if rule_id == "PIERCING_LINE":
        midpoint = (_value(p, "open") + _value(p, "close")) / 2.0
        return _down(p) and _up(c) and _value(c, "open") <= _value(p, "close") and midpoint < _value(c, "close") < _value(p, "open")
    if rule_id == "DARK_CLOUD_COVER":
        midpoint = (_value(p, "open") + _value(p, "close")) / 2.0
        return _up(p) and _down(c) and _value(c, "open") >= _value(p, "close") and _value(p, "open") < _value(c, "close") < midpoint
    if rule_id == "BULLISH_THRUSTING_LINE":
        midpoint = (_value(p, "open") + _value(p, "close")) / 2.0
        return _down(p) and _up(c) and _value(p, "close") < _value(c, "close") <= midpoint
    if rule_id == "INCOMPLETE_DARK_CLOUD_COVER":
        midpoint = (_value(p, "open") + _value(p, "close")) / 2.0
        return _up(p) and _down(c) and midpoint <= _value(c, "close") < _value(p, "close")
    if rule_id in {"BULLISH_HARAMI", "BEARISH_HARAMI"}:
        desired = _up(c) if rule_id.startswith("BULLISH") else _down(c)
        return _long(p) and desired and _inside(c, p)
    if rule_id in {"BULLISH_HARAMI_CROSS", "BEARISH_HARAMI_CROSS"}:
        return _long(p) and _doji(c) and _inside(c, p)
    if rule_id == "HOMING_PIGEON":
        return _down(p) and _down(c) and _inside(c, p)
    if rule_id == "BEARISH_HOMING_PIGEON":
        return _up(p) and _up(c) and _inside(c, p)
    if rule_id == "TWEEZERS_BOTTOM":
        return _near(_value(c, "low"), _value(p, "low"), max(_spread(c), _spread(p)))
    if rule_id == "TWEEZERS_TOP":
        return _near(_value(c, "high"), _value(p, "high"), max(_spread(c), _spread(p)))
    if rule_id in {"DOJI_STAR_BOTTOM", "DOJI_STAR_TOP"}:
        return _long(p) and _doji(c) and (_gap_down(p, c) if rule_id.endswith("BOTTOM") else _gap_up(p, c))
    if rule_id in {"THREE_RIVER_MORNING_DOJI_STAR", "THREE_RIVER_EVENING_DOJI_STAR"}:
        bullish = rule_id.startswith("THREE_RIVER_MORNING")
        return len(candles) >= 3 and (_down(a) if bullish else _up(a)) and _doji(p) and (_up(c) if bullish else _down(c)) and (_value(c, "close") > (_value(a, "open") + _value(a, "close")) / 2.0 if bullish else _value(c, "close") < (_value(a, "open") + _value(a, "close")) / 2.0)
    if rule_id in {"ABANDONED_BABY_BOTTOM", "ABANDONED_BABY_TOP"}:
        bullish = rule_id.endswith("BOTTOM")
        return len(candles) >= 3 and _doji(p) and ((_gap_down(a, p) and _gap_up(p, c) and _up(c)) if bullish else (_gap_up(a, p) and _gap_down(p, c) and _down(c)))
    if rule_id in {"THREE_RIVER_MORNING_STAR", "THREE_RIVER_EVENING_STAR"}:
        bullish = "MORNING" in rule_id
        midpoint = (_value(a, "open") + _value(a, "close")) / 2.0
        return len(candles) >= 3 and (_down(a) if bullish else _up(a)) and _small(p) and (_up(c) if bullish else _down(c)) and (_value(c, "close") > midpoint if bullish else _value(c, "close") < midpoint)
    if rule_id in {"TRI_STAR_BOTTOM", "TRI_STAR_TOP"}:
        return len(candles) >= 3 and all(_doji(row) for row in (a, p, c)) and ((_value(p, "low") < _value(a, "low") and _value(p, "low") < _value(c, "low")) if rule_id.endswith("BOTTOM") else (_value(p, "high") > _value(a, "high") and _value(p, "high") > _value(c, "high")))
    if rule_id.startswith("BREAKAWAY_THREE_NEW_PRICE") and len(candles) >= 5:
        rows = candles[-5:]
        bullish = rule_id.endswith("BOTTOM")
        return ((_down(rows[0]) and all(_down(row) for row in rows[1:4]) and _up(rows[4]) and _value(rows[4], "close") > _value(rows[1], "open")) if bullish else (_up(rows[0]) and all(_up(row) for row in rows[1:4]) and _down(rows[4]) and _value(rows[4], "close") < _value(rows[1], "open")))
    if rule_id == "BULLISH_BLACK_THREE_GAPS" and len(candles) >= 4:
        rows = candles[-4:]
        return all(_down(row) for row in rows) and all(_gap_down(rows[i], rows[i + 1]) for i in range(3))
    if rule_id == "BEARISH_WHITE_THREE_GAPS" and len(candles) >= 4:
        rows = candles[-4:]
        return all(_up(row) for row in rows) and all(_gap_up(rows[i], rows[i + 1]) for i in range(3))
    if rule_id == "THREE_WHITE_SOLDIERS":
        return _three_progressive(candles, "BUY")
    if rule_id == "THREE_BLACK_CROWS":
        return _three_progressive(candles, "SELL")
    if rule_id == "ADVANCE_BLOCK" and len(candles) >= 3:
        rows = candles[-3:]
        bodies = [_body(row) for row in rows]
        return all(_up(row) for row in rows) and bodies[0] > bodies[1] > bodies[2] and _upper_wick(rows[2]) > _upper_wick(rows[0])
    if rule_id == "DELIBERATION" and len(candles) >= 3:
        rows = candles[-3:]
        return _up(rows[0]) and _up(rows[1]) and _up(rows[2]) and _long(rows[0]) and _long(rows[1]) and _small(rows[2]) and _value(rows[2], "open") >= _value(rows[1], "close")
    if rule_id == "UPSIDE_GAP_TWO_CROWS" and len(candles) >= 3:
        return _up(a) and _down(p) and _down(c) and _gap_up(a, p) and _body_low(c) <= _body_low(p) and _body_high(c) >= _body_high(p) and _value(c, "close") > _value(a, "close")
    if rule_id == "CONCEALING_BABY_SWALLOW" and len(candles) >= 4:
        w, x, y, z = candles[-4:]
        return _down(w) and _marubozu(w) and _down(x) and _marubozu(x) and _down(y) and _upper_wick(y) > _body(y) and _down(z) and _value(z, "high") > _value(y, "high") and _value(z, "low") < _value(y, "low")
    if rule_id == "LADDER_BOTTOM" and len(candles) >= 5:
        rows = candles[-5:]
        return all(_down(row) for row in rows[:3]) and _down(rows[3]) and _upper_wick(rows[3]) > _body(rows[3]) and _up(rows[4]) and _value(rows[4], "close") > _value(rows[3], "high")
    if rule_id in {"TOWER_BOTTOM", "TOWER_TOP"} and len(candles) >= 5:
        rows = candles[-5:]
        bullish = rule_id.endswith("BOTTOM")
        return ((_down(rows[0]) and _long(rows[0]) and all(_small(row) for row in rows[1:4]) and _up(rows[4]) and _long(rows[4])) if bullish else (_up(rows[0]) and _long(rows[0]) and all(_small(row) for row in rows[1:4]) and _down(rows[4]) and _long(rows[4])))
    if rule_id == "EIGHT_TO_TEN_NEW_RECORD_LOWS" and len(candles) >= 8:
        lows = [_value(row, "low") for row in candles[-8:]]
        return all(right < left for left, right in zip(lows, lows[1:]))
    if rule_id == "EIGHT_TO_TEN_NEW_RECORD_HIGHS" and len(candles) >= 8:
        highs = [_value(row, "high") for row in candles[-8:]]
        return all(right > left for left, right in zip(highs, highs[1:]))
    if rule_id == "BULLISH_SEPARATING_LINES":
        return _down(p) and _up(c) and _near(_value(c, "open"), _value(p, "open"), max(_spread(c), _spread(p)))
    if rule_id == "BEARISH_SEPARATING_LINES":
        return _up(p) and _down(c) and _near(_value(c, "open"), _value(p, "open"), max(_spread(c), _spread(p)))
    if rule_id == "BULLISH_KICKING":
        return _down(p) and _marubozu(p) and _up(c) and _marubozu(c) and _gap_up(p, c)
    if rule_id == "BEARISH_KICKING":
        return _up(p) and _marubozu(p) and _down(c) and _marubozu(c) and _gap_down(p, c)
    if rule_id in {"ON_NECK", "IN_NECK", "BEARISH_THRUSTING_LINE"}:
        if not (_down(p) and _long(p) and _up(c)):
            return False
        close = _value(c, "close")
        if rule_id == "ON_NECK":
            return _near(close, _value(p, "low"), _spread(p), 0.08)
        if rule_id == "IN_NECK":
            return _value(p, "low") < close <= _value(p, "close") + 0.2 * _body(p)
        return _value(p, "close") < close < (_value(p, "open") + _value(p, "close")) / 2.0
    if rule_id in {"RISING_THREE_METHODS", "FALLING_THREE_METHODS"} and len(candles) >= 5:
        rows = candles[-5:]
        bullish = rule_id.startswith("RISING")
        inside_rows = rows[1:4]
        if bullish:
            return (
                _up(rows[0])
                and _long(rows[0])
                and all(
                    _down(row)
                    and _value(row, "high") <= _value(rows[0], "high")
                    and _value(row, "low") >= _value(rows[0], "low")
                    for row in inside_rows
                )
                and _up(rows[4])
                and _value(rows[4], "close") > _value(rows[0], "high")
            )
        return (
            _down(rows[0])
            and _long(rows[0])
            and all(
                _up(row)
                and _value(row, "high") <= _value(rows[0], "high")
                and _value(row, "low") >= _value(rows[0], "low")
                for row in inside_rows
            )
            and _down(rows[4])
            and _value(rows[4], "close") < _value(rows[0], "low")
        )
    if rule_id in {"BULLISH_MAT_HOLD", "BEARISH_MAT_HOLD"} and len(candles) >= 5:
        rows = candles[-5:]
        bullish = rule_id.startswith("BULLISH")
        return ((_up(rows[0]) and _long(rows[0]) and _gap_up(rows[0], rows[1]) and all(_small(row) for row in rows[1:4]) and _up(rows[4]) and _value(rows[4], "close") > _value(rows[0], "high")) if bullish else (_down(rows[0]) and _long(rows[0]) and _gap_down(rows[0], rows[1]) and all(_small(row) for row in rows[1:4]) and _down(rows[4]) and _value(rows[4], "close") < _value(rows[0], "low")))
    if rule_id == "RISING_WINDOW":
        return _gap_up(p, c)
    if rule_id == "FALLING_WINDOW":
        return _gap_down(p, c)
    if rule_id in {"TASUKI_UPSIDE_GAP", "TASUKI_DOWNSIDE_GAP"} and len(candles) >= 3:
        bullish = rule_id.startswith("TASUKI_UP")
        return ((_up(a) and _up(p) and _gap_up(a, p) and _down(c) and _value(a, "high") < _value(c, "close") < _value(p, "low")) if bullish else (_down(a) and _down(p) and _gap_down(a, p) and _up(c) and _value(p, "high") < _value(c, "close") < _value(a, "low")))
    if rule_id in {"UP_GAP_SIDE_BY_SIDE_WHITE_LINES", "DOWN_GAP_SIDE_BY_SIDE_WHITE_LINES"} and len(candles) >= 3:
        bullish = rule_id.startswith("UP_GAP")
        return ((_up(a) and _up(p) and _up(c) and _gap_up(a, p) and _near(_value(p, "open"), _value(c, "open"), max(_spread(p), _spread(c)))) if bullish else (_down(a) and _up(p) and _up(c) and _gap_down(a, p) and _near(_value(p, "open"), _value(c, "open"), max(_spread(p), _spread(c)))))
    if rule_id in {"HIGH_PRICE_GAPPING_PLAY", "LOW_PRICE_GAPPING_PLAY"} and len(candles) >= 5:
        rows = candles[-5:]
        bullish = rule_id.startswith("HIGH")
        consolidation_high = max(_value(row, "high") for row in rows[1:4])
        consolidation_low = min(_value(row, "low") for row in rows[1:4])
        return ((_up(rows[0]) and all(_small(row) for row in rows[1:4]) and _value(rows[4], "low") > consolidation_high) if bullish else (_down(rows[0]) and all(_small(row) for row in rows[1:4]) and _value(rows[4], "high") < consolidation_low))
    return False


def _derived_prior_trend(
    candles: Sequence[Mapping[str, Any]],
    *,
    pattern_end: int,
    bars: int,
    fallback: str,
) -> str:
    start = pattern_end - bars + 1
    history = candles[max(0, start - 12) : max(0, start)]
    if len(history) < 5:
        return "NEUTRAL"
    split = max(2, len(history) // 2)
    left = history[:split]
    right = history[split:]
    left_high = max(_value(row, "high") for row in left)
    left_low = min(_value(row, "low") for row in left)
    right_high = max(_value(row, "high") for row in right)
    right_low = min(_value(row, "low") for row in right)
    delta = _value(history[-1], "close") - _value(history[0], "close")
    noise = statistics.median(_spread(row) for row in history)
    if right_high > left_high and right_low > left_low and delta > noise * 0.35:
        return "BUY"
    if right_high < left_high and right_low < left_low and delta < -noise * 0.35:
        return "SELL"
    if abs(delta) > noise * 1.25:
        return "BUY" if delta > 0.0 else "SELL"
    return "NEUTRAL"


def _confirmation_close(
    side: str,
    pattern: Mapping[str, Any],
    confirmation: Mapping[str, Any],
) -> bool:
    return (
        _value(confirmation, "close") > _value(pattern, "high")
        if side == "BUY"
        else _value(confirmation, "close") < _value(pattern, "low")
    )


def _derived_filter_state(
    candles: Sequence[Mapping[str, Any]],
    prior_trend: str,
    location_side: str,
) -> dict[str, Any]:
    rows = list(candles[-20:])
    if not rows:
        return {"observable_filter_count": 0, "filters": {}}
    ranges = [_spread(row) for row in rows]
    latest = rows[-1]
    close_location = (
        (_value(latest, "close") - min(_value(row, "low") for row in rows))
        / max(
            1e-9,
            max(_value(row, "high") for row in rows)
            - min(_value(row, "low") for row in rows),
        )
    )
    return {
        "observable_filter_count": 3,
        "technical_indicators_used": False,
        "technical_indicator_scope": "EXCLUDED_BY_USER",
        "filters": {
            "RAW_PRICE_STRUCTURE_TREND": prior_trend,
            "STRUCTURAL_PRICE_LOCATION": location_side,
            "VISIBLE_RANGE_POSITION": (
                "UPPER_EXTREME"
                if close_location >= 0.8
                else "LOWER_EXTREME"
                if close_location <= 0.2
                else "MID_RANGE"
            ),
        },
    }


def _semantic_side(value: object) -> str:
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


def _location_sides(value: object) -> set[str]:
    sides: set[str] = set()
    values = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else [value]
    for item in values:
        if isinstance(item, Mapping):
            candidates = (item.get("role_side"), item.get("side"), item.get("role"))
        else:
            candidates = (item,)
        for candidate in candidates:
            side = _semantic_side(candidate)
            if side in {"BUY", "SELL"}:
                sides.add(side)
    return sides


def evaluate_candlestick_catalog_v3(
    *,
    candles: Sequence[Mapping[str, Any]],
    prior_trend: str,
    location_side: str,
    higher_timeframe_side: str = "NEUTRAL",
    location_history: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [row for row in candles if isinstance(row, Mapping)]
    fallback_trend = _semantic_side(prior_trend)
    location = _semantic_side(location_side)
    htf_side = _semantic_side(higher_timeframe_side)
    detections: list[dict[str, Any]] = []
    for spec in CANDLESTICK_RULE_CATALOG_V3:
        bars = int(spec["bars"])
        current_match = len(rows) >= bars and _matches(str(spec["rule_id"]), rows)
        confirmation_mode = str(spec["confirmation"])
        matched = current_match
        confirmation_satisfied = confirmation_mode == "SELF_CONFIRMED" and current_match
        pattern_end = len(rows) - 1
        if confirmation_mode == "NEXT_CLOSE" and len(rows) >= bars + 1:
            prior_match = _matches(str(spec["rule_id"]), rows[:-1])
            if prior_match:
                matched = True
                pattern_end = len(rows) - 2
                confirmation_satisfied = _confirmation_close(
                    str(spec["side"]),
                    rows[-2],
                    rows[-1],
                )
        if not matched:
            continue
        observed_prior = _derived_prior_trend(
            rows,
            pattern_end=pattern_end,
            bars=bars,
            fallback=fallback_trend,
        )
        required_prior = str(spec["required_prior_trend"])
        prior_trend_valid = required_prior == "NEUTRAL" or observed_prior == required_prior
        required_location = str(spec["required_location_side"])
        location_bindings = location_history if isinstance(location_history, Mapping) else {}
        location_sides = _location_sides(location_bindings.get(str(pattern_end)))
        if pattern_end == len(rows) - 1 and not isinstance(location_history, Mapping):
            location_sides.update(_location_sides(location))
        family = str(spec["family"])
        side = str(spec["side"])
        if family.startswith("REVERSAL"):
            location_valid = side in location_sides
            location_mode = "REVERSAL_AT_MATCHING_SUPPORT_RESISTANCE"
        else:
            opposing = "SELL" if side == "BUY" else "BUY"
            location_valid = observed_prior == side and opposing not in location_sides
            location_mode = "CONTINUATION_WITH_TREND_AND_NO_OPPOSING_LOCATION"
        bound_location = ",".join(sorted(location_sides)) or "UNBOUND"
        htf_valid = htf_side not in {"BUY", "SELL"} or htf_side == side
        directional_authority = bool(
            prior_trend_valid
            and location_valid
            and confirmation_satisfied
            and htf_valid
        )
        failures = []
        if not prior_trend_valid:
            failures.append("PRIOR_TREND_MISMATCH")
        if not location_valid:
            failures.append("STRUCTURAL_LOCATION_MISSING")
        if not confirmation_satisfied:
            failures.append("CONFIRMATION_CLOSE_PENDING")
        if not htf_valid:
            failures.append("HIGHER_TIMEFRAME_CONFLICT")
        base_weight = 1.55 if str(spec["family"]).startswith("REVERSAL") else 1.15
        detections.append(
            {
                **spec,
                "geometry_matched": True,
                "pattern_end_index": pattern_end,
                "pattern_start_index": pattern_end - bars + 1,
                "confirmation_index": (
                    len(rows) - 1
                    if confirmation_mode == "NEXT_CLOSE" and pattern_end == len(rows) - 2
                    else pattern_end
                ),
                "observed_prior_trend": observed_prior,
                "prior_trend_valid": prior_trend_valid,
                "location_valid": location_valid,
                "bound_structural_location": bound_location,
                "location_validation_mode": location_mode,
                "historical_location_binding": pattern_end != len(rows) - 1,
                "confirmation_satisfied": confirmation_satisfied,
                "higher_timeframe_valid": htf_valid,
                "directional_authority": directional_authority,
                "failed_requirements": failures,
                "weight": base_weight if directional_authority else 0.0,
            }
        )
    qualified = [row for row in detections if row["directional_authority"]]
    return {
        "schema": CANDLESTICK_CATALOG_SCHEMA_V3,
        "version": CANDLESTICK_CATALOG_VERSION_V3,
        "catalog_pattern_count": len(CANDLESTICK_RULE_CATALOG_V3),
        "advanced_filter_count": len(ADVANCED_CANDLE_FILTER_CATALOG_V3),
        "catalog_complete": True,
        "technical_price_action_catalog_complete": True,
        "provenance_complete": all(
            bool(row.get("source_file")) and bool(row.get("pdf_pages"))
            for row in CANDLESTICK_RULE_CATALOG_V3
        ),
        "future_blind": True,
        "horizon_published": False,
        "execution_authority": False,
        "detections": detections,
        "qualified_detections": qualified,
        "recognized_pattern_ids": [str(row["rule_id"]) for row in detections],
        "directional_pattern_ids": [str(row["rule_id"]) for row in qualified],
        "derived_filters": _derived_filter_state(rows, fallback_trend, location),
        "advanced_filter_catalog": [dict(row) for row in ADVANCED_CANDLE_FILTER_CATALOG_V3],
        "excluded_conventional_indicator_rules": list(EXCLUDED_CONVENTIONAL_INDICATOR_RULES_V3),
        "technical_indicators_used": False,
        "technical_indicator_scope": "EXCLUDED_BY_USER",
    }


__all__ = [
    "ADVANCED_CANDLE_FILTER_CATALOG_V3",
    "CANDLESTICK_CATALOG_SCHEMA_V3",
    "CANDLESTICK_CATALOG_VERSION_V3",
    "CANDLESTICK_RULE_CATALOG_V3",
    "EXCLUDED_CONVENTIONAL_INDICATOR_RULES_V3",
    "evaluate_candlestick_catalog_v3",
]
