from __future__ import annotations

from math import isfinite
from statistics import median
from typing import Any, Mapping, Sequence


OPTIMIZED_TARGETS_SCHEMA_VERSION = "PG_OPTIMIZED_TARGETS_V3"
DEFAULT_OPTIMIZED_HORIZONS: tuple[int, ...] = (3, 5, 8, 13, 21, 34)
SIDES: tuple[str, str] = ("BUY", "SELL")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if isfinite(parsed) else float(default)


def candle_ohlc_price_v3(row: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    required = ("open_y_px", "close_y_px", "wick_top_px", "wick_bottom_px")
    if any(row.get(key) is None for key in required):
        return None
    open_price = -_number(row.get("open_y_px"))
    close_price = -_number(row.get("close_y_px"))
    high_price = -_number(row.get("wick_top_px"))
    low_price = -_number(row.get("wick_bottom_px"))
    high_price = max(high_price, open_price, close_price)
    low_price = min(low_price, open_price, close_price)
    return open_price, high_price, low_price, close_price


def visible_range_scale_v3(candles: Sequence[Mapping[str, Any]], cutoff: int) -> float:
    rows = [
        candle_ohlc_price_v3(row)
        for row in candles[max(0, int(cutoff) - 20) : int(cutoff)]
    ]
    ranges = [high - low for item in rows if item for _, high, low, _ in (item,)]
    valid = [value for value in ranges if value > 1e-9]
    return max(1e-6, float(median(valid))) if valid else 1.0


def _candle_side(row: Mapping[str, Any], *, rest_threshold: float) -> str:
    item = candle_ohlc_price_v3(row)
    if item is None:
        return "REST"
    open_price, _, _, close_price = item
    delta = close_price - open_price
    if abs(delta) <= max(1e-9, rest_threshold):
        return "REST"
    return "BUY" if delta > 0.0 else "SELL"


def _majority_side(rows: Sequence[Mapping[str, Any]], *, rest_threshold: float) -> str:
    counts = {"BUY": 0, "SELL": 0, "REST": 0}
    for row in rows:
        counts[_candle_side(row, rest_threshold=rest_threshold)] += 1
    if counts["BUY"] == counts["SELL"]:
        return "REST"
    return "BUY" if counts["BUY"] > counts["SELL"] else "SELL"


def _endpoint_side(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    horizon: int,
    scale: float,
) -> str:
    before = candle_ohlc_price_v3(candles[cutoff - 1])
    after = candle_ohlc_price_v3(candles[cutoff + horizon - 1])
    if before is None or after is None:
        return "REST"
    delta = after[3] - before[3]
    if abs(delta) <= 0.12 * scale:
        return "REST"
    return "BUY" if delta > 0.0 else "SELL"


def build_direction_targets_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    horizons: Sequence[int] = DEFAULT_OPTIMIZED_HORIZONS,
) -> dict[str, Any]:
    cutoff = int(cutoff)
    scale = visible_range_scale_v3(candles, cutoff)
    result: dict[str, Any] = {}
    for raw_horizon in horizons:
        horizon = int(raw_horizon)
        if cutoff <= 0 or horizon <= 0 or cutoff + horizon > len(candles):
            continue
        suffix = candles[cutoff : cutoff + horizon]
        result[str(horizon)] = {
            "majority": _majority_side(suffix, rest_threshold=0.06 * scale),
            "endpoint": _endpoint_side(
                candles,
                cutoff=cutoff,
                horizon=horizon,
                scale=scale,
            ),
            "future_candle_count": horizon,
        }
    return result


def build_trade_path_target_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    side_candidate: str,
    horizon: int = 21,
    target_multiple: float = 1.35,
    invalidation_multiple: float = 0.90,
) -> dict[str, Any]:
    cutoff = int(cutoff)
    side = str(side_candidate or "").strip().upper()
    if side not in SIDES or cutoff <= 0 or cutoff >= len(candles):
        return {
            "outcome": "NO_OPPORTUNITY",
            "target_before_invalidation": False,
            "invalidation_before_target": False,
            "time_barrier_expired": False,
            "mfe_ranges": 0.0,
            "mae_ranges": 0.0,
            "drawdown_first": False,
            "horizon": int(horizon),
        }
    entry = candle_ohlc_price_v3(candles[cutoff - 1])
    if entry is None:
        return {
            "outcome": "INVALID_GEOMETRY",
            "target_before_invalidation": False,
            "invalidation_before_target": False,
            "time_barrier_expired": False,
            "mfe_ranges": 0.0,
            "mae_ranges": 0.0,
            "drawdown_first": False,
            "horizon": int(horizon),
        }
    scale = visible_range_scale_v3(candles, cutoff)
    entry_price = entry[3]
    target_distance = max(1e-6, float(target_multiple) * scale)
    invalidation_distance = max(1e-6, float(invalidation_multiple) * scale)
    target_price = entry_price + target_distance if side == "BUY" else entry_price - target_distance
    invalidation_price = (
        entry_price - invalidation_distance
        if side == "BUY"
        else entry_price + invalidation_distance
    )
    suffix = candles[cutoff : min(len(candles), cutoff + max(1, int(horizon)))]
    mfe = 0.0
    mae = 0.0
    target_step = 0
    invalidation_step = 0
    first_excursion = ""
    for step, row in enumerate(suffix, start=1):
        item = candle_ohlc_price_v3(row)
        if item is None:
            continue
        _, high_price, low_price, _ = item
        favorable = (
            high_price - entry_price if side == "BUY" else entry_price - low_price
        )
        adverse = (
            entry_price - low_price if side == "BUY" else high_price - entry_price
        )
        mfe = max(mfe, favorable / scale)
        mae = max(mae, adverse / scale)
        hit_target = high_price >= target_price if side == "BUY" else low_price <= target_price
        hit_invalidation = (
            low_price <= invalidation_price
            if side == "BUY"
            else high_price >= invalidation_price
        )
        if not first_excursion:
            if adverse >= 0.35 * invalidation_distance:
                first_excursion = "DRAWDOWN"
            elif favorable >= 0.35 * target_distance:
                first_excursion = "FAVORABLE"
        if hit_target and hit_invalidation:
            invalidation_step = step
            break
        if hit_invalidation:
            invalidation_step = step
            break
        if hit_target:
            target_step = step
            break
    target_first = bool(target_step and not invalidation_step)
    invalidation_first = bool(invalidation_step and not target_step)
    expired = not target_first and not invalidation_first
    outcome = (
        "TARGET_BEFORE_INVALIDATION"
        if target_first
        else "INVALIDATION_BEFORE_TARGET"
        if invalidation_first
        else "TIME_BARRIER_EXPIRED"
    )
    return {
        "outcome": outcome,
        "target_before_invalidation": target_first,
        "invalidation_before_target": invalidation_first,
        "time_barrier_expired": expired,
        "mfe_ranges": round(max(0.0, mfe), 6),
        "mae_ranges": round(max(0.0, mae), 6),
        "drawdown_first": first_excursion == "DRAWDOWN",
        "target_step": target_step,
        "invalidation_step": invalidation_step,
        "horizon": int(horizon),
        "visible_range_scale": round(scale, 6),
        "target_multiple": float(target_multiple),
        "invalidation_multiple": float(invalidation_multiple),
    }


def build_optimized_targets_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    side_candidate: str,
    visible_maturity: str,
    horizons: Sequence[int] = DEFAULT_OPTIMIZED_HORIZONS,
    trade_horizon: int = 21,
) -> dict[str, Any]:
    directions = build_direction_targets_v3(
        candles,
        cutoff=cutoff,
        horizons=horizons,
    )
    trade_path = build_trade_path_target_v3(
        candles,
        cutoff=cutoff,
        side_candidate=side_candidate,
        horizon=trade_horizon,
    )
    if trade_path["outcome"] == "NO_OPPORTUNITY":
        maturity = "NO_OPPORTUNITY"
    elif str(visible_maturity).upper() == "LATE_CHASE":
        maturity = "LATE_CHASE"
    elif trade_path["target_before_invalidation"]:
        maturity = "ENTER_NOW" if not trade_path["drawdown_first"] else "PREPARE"
    elif trade_path["invalidation_before_target"]:
        maturity = "INVALIDATED"
    else:
        maturity = "MISSED" if trade_path["mfe_ranges"] >= 1.0 else "VALID_WATCH"
    horizon_key = str(int(trade_horizon))
    majority = dict(directions.get(horizon_key, {})).get("majority", "REST")
    return {
        "schema_version": OPTIMIZED_TARGETS_SCHEMA_VERSION,
        "directions": directions,
        "trade_path": trade_path,
        "opportunity_maturity": maturity,
        "candidate_direction_correct": majority == str(side_candidate).upper(),
        "pullback_held": bool(trade_path["target_before_invalidation"]),
        "pullback_failed": bool(trade_path["invalidation_before_target"]),
        "future_suffix_used_by_scorer_only": True,
    }
