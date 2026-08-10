from __future__ import annotations

import hashlib
import json
from math import isfinite, log1p
from statistics import mean, median
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from phoenixguard.study.optimized_targets_v3 import candle_ohlc_price_v3


EVENT_WINDOWS_SCHEMA_VERSION = "PG_EVENT_WINDOWS_V3"
EVENT_TYPES: tuple[str, ...] = (
    "PULLBACK_VISIBLE",
    "RECLAIM_AFTER_SWEEP",
    "BREAK_AND_HOLD",
    "FAILED_BREAKOUT",
    "SUPPORT_REACTION",
    "RESISTANCE_REACTION",
    "CONTINUATION_PRESSURE",
    "CROWDED_MID_RANGE",
    "OPPOSING_FORCE_TOUCH",
    "NO_OPPORTUNITY",
)
TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "UNKNOWN")
CURRENCIES: tuple[str, ...] = (
    "AUD",
    "CAD",
    "CHF",
    "EUR",
    "GBP",
    "JPY",
    "NZD",
    "USD",
    "XAU",
    "ZAR",
)
NUMERIC_FEATURE_NAMES: tuple[str, ...] = (
    "visible_count",
    "range_scale",
    "body_ratio_mean_8",
    "wick_ratio_mean_8",
    "buy_ratio_8",
    "buy_ratio_21",
    "slope_3",
    "slope_5",
    "slope_8",
    "slope_13",
    "slope_21",
    "efficiency_8",
    "efficiency_21",
    "volatility_ratio",
    "compression_ratio",
    "range_location",
    "distance_high_ranges",
    "distance_low_ranges",
    "latest_body_ranges",
    "latest_upper_wick_ranges",
    "latest_lower_wick_ranges",
    "opposing_run",
    "same_side_run",
    "pullback_depth_ranges",
    "trendline_touches",
    "trendline_residual_ranges",
    "trendline_slope",
    "breakout_strength",
    "rejection_strength",
    "parse_confidence",
    "spacing_confidence",
    "scale_conflict",
    "latest_flip",
    "late_chase_risk",
    "symbol_known",
    "timeframe_known",
    "timeframe_log_seconds",
)
FEATURE_NAMES: tuple[str, ...] = (
    NUMERIC_FEATURE_NAMES
    + tuple(f"event_{name}" for name in EVENT_TYPES)
    + ("side_BUY", "side_SELL")
    + tuple(f"timeframe_{name}" for name in TIMEFRAMES)
    + tuple(f"base_{name}" for name in CURRENCIES)
    + tuple(f"quote_{name}" for name in CURRENCIES)
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if isfinite(parsed) else float(default)


def _timeframe_seconds(value: object) -> int:
    text = str(value or "").strip().upper()
    table = {
        "M1": 60,
        "M5": 300,
        "M15": 900,
        "M30": 1800,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400,
    }
    return int(table.get(text, 0))


def _price_rows(candles: Sequence[Mapping[str, Any]]) -> list[tuple[float, float, float, float]]:
    return [item for row in candles if (item := candle_ohlc_price_v3(row)) is not None]


def _range_scale(rows: Sequence[tuple[float, float, float, float]]) -> float:
    ranges = [high - low for _, high, low, _ in rows[-20:] if high > low]
    return max(1e-6, float(median(ranges))) if ranges else 1.0


def _slope(closes: Sequence[float], window: int, scale: float) -> float:
    if len(closes) < 2:
        return 0.0
    view = closes[-min(len(closes), max(2, int(window))) :]
    return float((view[-1] - view[0]) / max(scale, 1e-6))


def _efficiency(closes: Sequence[float], window: int) -> float:
    view = closes[-min(len(closes), max(2, int(window))) :]
    if len(view) < 2:
        return 0.0
    path = sum(abs(view[index] - view[index - 1]) for index in range(1, len(view)))
    return abs(view[-1] - view[0]) / path if path > 1e-9 else 0.0


def _side(delta: float, threshold: float = 0.0) -> str:
    if abs(delta) <= threshold:
        return "REST"
    return "BUY" if delta > 0.0 else "SELL"


def _run_length(sides: Sequence[str], side: str) -> int:
    count = 0
    for item in reversed(sides):
        if item != side:
            break
        count += 1
    return count


def _prefix_hash(candles: Sequence[Mapping[str, Any]]) -> str:
    keys = (
        "open_y_px",
        "close_y_px",
        "wick_top_px",
        "wick_bottom_px",
        "direction",
        "parse_confidence",
        "spacing_confidence",
    )
    payload = [[row.get(key) for key in keys] for row in candles]
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _trendline_features(
    rows: Sequence[tuple[float, float, float, float]],
    *,
    side: str,
    scale: float,
) -> tuple[float, float, float]:
    if len(rows) < 12 or side not in {"BUY", "SELL"}:
        return 0.0, 9.0, 0.0
    values = [item[2] if side == "BUY" else item[1] for item in rows[-32:]]
    pivots: list[tuple[int, float]] = []
    for index in range(1, len(values) - 1):
        center = values[index]
        is_pivot = (
            center <= values[index - 1] and center <= values[index + 1]
            if side == "BUY"
            else center >= values[index - 1] and center >= values[index + 1]
        )
        if is_pivot:
            pivots.append((index, center))
    if len(pivots) < 2:
        return float(len(pivots)), 9.0, 0.0
    xs = np.asarray([row[0] for row in pivots], dtype=np.float64)
    ys = np.asarray([row[1] for row in pivots], dtype=np.float64)
    slope, intercept = np.polyfit(xs, ys, 1)
    residuals = np.abs(ys - ((slope * xs) + intercept)) / max(scale, 1e-6)
    touches = int(np.sum(residuals <= 0.35))
    latest_residual = abs(values[-1] - ((slope * (len(values) - 1)) + intercept)) / max(scale, 1e-6)
    return float(touches), float(latest_residual), float(slope / max(scale, 1e-6))


def _symbol_parts(symbol: object) -> tuple[str, str]:
    text = "".join(ch for ch in str(symbol or "").upper() if ch.isalpha())
    if len(text) >= 6:
        return text[:3], text[3:6]
    return "UNKNOWN", "UNKNOWN"


def build_event_window_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    image_hash: str,
    family_id: str,
    symbol: object,
    timeframe: object,
    path: str = "",
) -> dict[str, Any]:
    cutoff = int(cutoff)
    prefix = list(candles[:cutoff])
    rows = _price_rows(prefix)
    if len(rows) < 8:
        raise ValueError("PG_EVENT_PREFIX_TOO_SHORT")
    scale = _range_scale(rows)
    opens = [item[0] for item in rows]
    highs = [item[1] for item in rows]
    lows = [item[2] for item in rows]
    closes = [item[3] for item in rows]
    bodies = [abs(close - open_price) for open_price, _, _, close in rows]
    ranges = [max(1e-6, high - low) for _, high, low, _ in rows]
    candle_sides = [_side(close - open_price, 0.05 * scale) for open_price, _, _, close in rows]
    long_side = _side(_slope(closes, 21, scale), 0.30)
    local_side = _side(_slope(closes, 3, scale), 0.18)
    same_run = _run_length(candle_sides, local_side) if local_side in {"BUY", "SELL"} else 0
    opposing_run = same_run if long_side in {"BUY", "SELL"} and local_side not in {long_side, "REST"} else 0
    lookback_high = max(highs[-13:-1]) if len(highs) > 1 else highs[-1]
    lookback_low = min(lows[-13:-1]) if len(lows) > 1 else lows[-1]
    range_high = max(highs[-21:])
    range_low = min(lows[-21:])
    range_span = max(1e-6, range_high - range_low)
    location = (closes[-1] - range_low) / range_span
    breakout_buy = max(0.0, (closes[-1] - lookback_high) / scale)
    breakout_sell = max(0.0, (lookback_low - closes[-1]) / scale)
    rejected_high = max(0.0, (highs[-1] - max(closes[-1], lookback_high)) / scale)
    rejected_low = max(0.0, (min(closes[-1], lookback_low) - lows[-1]) / scale)
    latest_flip = bool(
        len(candle_sides) >= 2
        and candle_sides[-1] in {"BUY", "SELL"}
        and candle_sides[-2] in {"BUY", "SELL"}
        and candle_sides[-1] != candle_sides[-2]
    )
    scale_conflict = bool(
        long_side in {"BUY", "SELL"}
        and local_side in {"BUY", "SELL"}
        and long_side != local_side
    )
    event_type = "NO_OPPORTUNITY"
    side_candidate = "HOLD"
    if scale_conflict and 2 <= opposing_run <= 4:
        event_type = "PULLBACK_VISIBLE"
        side_candidate = long_side
    elif lows[-1] < lookback_low and closes[-1] > lookback_low:
        event_type = "RECLAIM_AFTER_SWEEP"
        side_candidate = "BUY"
    elif highs[-1] > lookback_high and closes[-1] < lookback_high:
        event_type = "RECLAIM_AFTER_SWEEP"
        side_candidate = "SELL"
    elif breakout_buy >= 0.10 or breakout_sell >= 0.10:
        event_type = "BREAK_AND_HOLD"
        side_candidate = "BUY" if breakout_buy >= breakout_sell else "SELL"
    elif rejected_high >= 0.25 or rejected_low >= 0.25:
        event_type = "FAILED_BREAKOUT"
        side_candidate = "SELL" if rejected_high >= rejected_low else "BUY"
    elif location <= 0.22 and candle_sides[-1] == "BUY":
        event_type = "SUPPORT_REACTION"
        side_candidate = "BUY"
    elif location >= 0.78 and candle_sides[-1] == "SELL":
        event_type = "RESISTANCE_REACTION"
        side_candidate = "SELL"
    elif long_side in {"BUY", "SELL"} and local_side == long_side and _efficiency(closes, 8) >= 0.48:
        event_type = "CONTINUATION_PRESSURE"
        side_candidate = long_side
    elif 0.32 <= location <= 0.68 and _efficiency(closes, 8) <= 0.24:
        event_type = "CROWDED_MID_RANGE"
    elif min((closes[-1] - range_low) / scale, (range_high - closes[-1]) / scale) <= 0.35:
        event_type = "OPPOSING_FORCE_TOUCH"
        side_candidate = "BUY" if location <= 0.5 else "SELL"
    late_chase = bool(
        side_candidate in {"BUY", "SELL"}
        and local_side == side_candidate
        and same_run >= 5
        and abs(_slope(closes, 8, scale)) >= 2.8
    )
    if event_type == "NO_OPPORTUNITY" or side_candidate == "HOLD":
        maturity = "NO_OPPORTUNITY"
    elif late_chase:
        maturity = "LATE_CHASE"
    elif event_type == "PULLBACK_VISIBLE" and latest_flip:
        maturity = "PREPARE"
    elif event_type in {"RECLAIM_AFTER_SWEEP", "SUPPORT_REACTION", "RESISTANCE_REACTION"}:
        maturity = "PREPARE"
    else:
        maturity = "VALID_WATCH"
    touches, trendline_residual, trendline_slope = _trendline_features(
        rows,
        side=side_candidate,
        scale=scale,
    )
    recent = rows[-8:]
    parse_confidence = mean(_number(row.get("parse_confidence"), 0.0) for row in prefix[-16:])
    spacing_confidence = mean(_number(row.get("spacing_confidence"), 0.0) for row in prefix[-16:])
    features = {
        "visible_count": float(len(rows)),
        "range_scale": float(scale),
        "body_ratio_mean_8": mean(bodies[-8:]) / scale,
        "wick_ratio_mean_8": mean(
            max(0.0, (high - low) - abs(close - open_price))
            for open_price, high, low, close in recent
        )
        / scale,
        "buy_ratio_8": sum(side == "BUY" for side in candle_sides[-8:]) / min(8, len(candle_sides)),
        "buy_ratio_21": sum(side == "BUY" for side in candle_sides[-21:]) / min(21, len(candle_sides)),
        "slope_3": _slope(closes, 3, scale),
        "slope_5": _slope(closes, 5, scale),
        "slope_8": _slope(closes, 8, scale),
        "slope_13": _slope(closes, 13, scale),
        "slope_21": _slope(closes, 21, scale),
        "efficiency_8": _efficiency(closes, 8),
        "efficiency_21": _efficiency(closes, 21),
        "volatility_ratio": mean(ranges[-5:]) / max(1e-6, mean(ranges[-20:])),
        "compression_ratio": min(ranges[-5:]) / max(1e-6, max(ranges[-13:])),
        "range_location": location,
        "distance_high_ranges": (range_high - closes[-1]) / scale,
        "distance_low_ranges": (closes[-1] - range_low) / scale,
        "latest_body_ranges": bodies[-1] / scale,
        "latest_upper_wick_ranges": (highs[-1] - max(opens[-1], closes[-1])) / scale,
        "latest_lower_wick_ranges": (min(opens[-1], closes[-1]) - lows[-1]) / scale,
        "opposing_run": float(opposing_run),
        "same_side_run": float(same_run),
        "pullback_depth_ranges": abs(closes[-1] - closes[-min(len(closes), max(2, opposing_run + 1))]) / scale,
        "trendline_touches": touches,
        "trendline_residual_ranges": trendline_residual,
        "trendline_slope": trendline_slope,
        "breakout_strength": max(breakout_buy, breakout_sell),
        "rejection_strength": max(rejected_high, rejected_low),
        "parse_confidence": parse_confidence,
        "spacing_confidence": spacing_confidence,
        "scale_conflict": float(scale_conflict),
        "latest_flip": float(latest_flip),
        "late_chase_risk": float(late_chase),
        "symbol_known": float(str(symbol or "").upper() != "UNKNOWN"),
        "timeframe_known": float(str(timeframe or "").upper() != "UNKNOWN"),
        "timeframe_log_seconds": log1p(_timeframe_seconds(timeframe)),
    }
    event_id = hashlib.sha256(
        f"{image_hash}:{cutoff}:{event_type}:{side_candidate}".encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": EVENT_WINDOWS_SCHEMA_VERSION,
        "event_id": event_id,
        "image_hash": str(image_hash),
        "family_id": str(family_id),
        "cutoff": cutoff,
        "symbol": str(symbol or "UNKNOWN").upper(),
        "timeframe": str(timeframe or "UNKNOWN").upper(),
        "visible_prefix_hash": _prefix_hash(prefix),
        "visible_prefix_candles": len(prefix),
        "event_type": event_type,
        "side_candidate": side_candidate,
        "visible_maturity": maturity,
        "eligible": bool(side_candidate in {"BUY", "SELL"} and event_type not in {"NO_OPPORTUNITY", "CROWDED_MID_RANGE"}),
        "market_location": "LOW" if location <= 0.33 else "HIGH" if location >= 0.67 else "MID",
        "anchor_candles": [str(row.get("candle_id") or "") for row in prefix[-4:]],
        "features": features,
        "path": str(path),
        "causal_prefix_only": True,
    }


def feature_vector_v3(event: Mapping[str, Any]) -> NDArray[np.float32]:
    features = dict(event.get("features") or {})
    event_type = str(event.get("event_type") or "NO_OPPORTUNITY").upper()
    side = str(event.get("side_candidate") or "HOLD").upper()
    timeframe = str(event.get("timeframe") or "UNKNOWN").upper()
    if timeframe not in TIMEFRAMES:
        timeframe = "UNKNOWN"
    base, quote = _symbol_parts(event.get("symbol"))
    values = [_number(features.get(name), 0.0) for name in NUMERIC_FEATURE_NAMES]
    values.extend(float(event_type == name) for name in EVENT_TYPES)
    values.extend((float(side == "BUY"), float(side == "SELL")))
    values.extend(float(timeframe == name) for name in TIMEFRAMES)
    values.extend(float(base == name) for name in CURRENCIES)
    values.extend(float(quote == name) for name in CURRENCIES)
    return np.asarray(values, dtype=np.float32)


def sequence_tensor_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    length: int = 64,
) -> NDArray[np.float32]:
    prefix = list(candles[: int(cutoff)])
    rows = _price_rows(prefix)
    output = np.zeros((max(1, int(length)), 6), dtype=np.float32)
    if not rows:
        return output
    scale = _range_scale(rows)
    selected = rows[-len(output) :]
    offset = len(output) - len(selected)
    previous_close = selected[0][0]
    for index, (open_price, high, low, close) in enumerate(selected, start=offset):
        body = (close - open_price) / scale
        output[index] = np.asarray(
            [
                (open_price - previous_close) / scale,
                body,
                (high - low) / scale,
                (high - max(open_price, close)) / scale,
                (min(open_price, close) - low) / scale,
                1.0 if body > 0.05 else -1.0 if body < -0.05 else 0.0,
            ],
            dtype=np.float32,
        )
        previous_close = close
    return output
