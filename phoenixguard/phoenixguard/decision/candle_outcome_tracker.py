from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence


CANDLE_OUTCOME_TRACKER_V1 = "CANDLE_OUTCOME_TRACKER_V1"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not isfinite(parsed):
        return float(default)
    return float(parsed)


def _clip01(value: Any, default: float = 0.0) -> float:
    parsed = _float(value, default)
    return max(0.0, min(1.0, parsed))


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _candle_value(row: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in row:
            return _float(row.get(name), default)
    return float(default)


def _extract_candles(entry: Mapping[str, Any], candles: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if candles is not None:
        return _rows(candles)
    for key in ("outcome_candles", "future_candles", "candles_after_entry", "tracked_candles", "candles", "ohlc"):
        rows = _rows(entry.get(key))
        if rows:
            return rows
    tracking = _mapping(entry.get("tracking_summary"))
    for key in ("outcome_candles", "future_candles", "candles_after_entry", "tracked_candles", "candles", "ohlc"):
        rows = _rows(tracking.get(key))
        if rows:
            return rows
    return []


def _nested_float(entry: Mapping[str, Any], *paths: Sequence[str], default: float = 0.0) -> float:
    for path in paths:
        current: Any = entry
        for key in path:
            current = _mapping(current).get(key)
        if current is not None:
            return _float(current, default)
    return float(default)


def _entry_price(entry: Mapping[str, Any], candles: Sequence[Mapping[str, Any]]) -> float:
    price = _nested_float(
        entry,
        ("entry_price",),
        ("price",),
        ("execution", "entry_price"),
        ("paper_entry", "entry_price"),
        ("paper_entry", "price"),
        ("signal", "entry_price"),
        default=float("nan"),
    )
    if isfinite(price):
        return float(price)
    if candles:
        first = candles[0]
        return _candle_value(first, "entry_price", "open", "o", "close", "c", "price_proxy", default=0.0)
    return 0.0


def _level_from_entry(entry: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        if name in entry:
            parsed = _float(entry.get(name), float("nan"))
            if isfinite(parsed):
                return parsed
    for container_name in ("execution", "paper_entry", "risk_context", "market_context", "target_context"):
        container = _mapping(entry.get(container_name))
        for name in names:
            if name in container:
                parsed = _float(container.get(name), float("nan"))
                if isfinite(parsed):
                    return parsed
    return None


def _opposing_force_price(entry: Mapping[str, Any], side: str, entry_price: float) -> float | None:
    direct = _level_from_entry(
        entry,
        "opposing_force_price",
        "opposing_force",
        "nearest_opposing_force_price",
        "opposing_force_level",
        "nearest_supply_price" if side == "BUY" else "nearest_demand_price",
    )
    if direct is not None:
        return direct

    zone = _mapping(entry.get("opposing_force_zone") or _mapping(entry.get("risk_context")).get("opposing_force_zone"))
    bounds = zone.get("price_bounds") or zone.get("bounds")
    if isinstance(bounds, Sequence) and not isinstance(bounds, (str, bytes, bytearray)):
        values = [_float(item, float("nan")) for item in bounds]
        values = [item for item in values if isfinite(item)]
        if values:
            if side == "BUY":
                above = [item for item in values if item >= entry_price]
                return min(above) if above else max(values)
            if side == "SELL":
                below = [item for item in values if item <= entry_price]
                return max(below) if below else min(values)
    return None


def _dominance_value(row: Mapping[str, Any]) -> float | None:
    for key in (
        "dominance_score",
        "dominance_margin",
        "projection_dominance",
        "dominance_gap",
        "side_dominance",
    ):
        if key in row:
            parsed = _float(row.get(key), float("nan"))
            if isfinite(parsed):
                return _clip01(parsed)
    return None


def _angle_value(row: Mapping[str, Any]) -> float | None:
    for key in (
        "angle",
        "active_trend_angle_degrees",
        "screen_space_angle",
        "multi_candle_regression_angle",
        "candle_body_angle",
    ):
        if key in row:
            parsed = _float(row.get(key), float("nan"))
            if isfinite(parsed):
                return parsed
    angle_context = _mapping(row.get("angle_context") or row.get("angle_features") or row.get("angle_dynamics"))
    for key in ("active_trend_angle_degrees", "screen_space_angle", "multi_candle_regression_angle"):
        if key in angle_context:
            parsed = _float(angle_context.get(key), float("nan"))
            if isfinite(parsed):
                return parsed
    return None


def _target_hit(row: Mapping[str, Any], side: str, target_price: float | None) -> bool:
    if target_price is None:
        return False
    high = _candle_value(row, "high", "h", "close", "c", "price_proxy")
    low = _candle_value(row, "low", "l", "close", "c", "price_proxy")
    return bool(high >= target_price) if side == "BUY" else bool(low <= target_price)


def _stop_hit(row: Mapping[str, Any], side: str, stop_price: float | None) -> bool:
    if stop_price is None:
        return False
    high = _candle_value(row, "high", "h", "close", "c", "price_proxy")
    low = _candle_value(row, "low", "l", "close", "c", "price_proxy")
    return bool(low <= stop_price) if side == "BUY" else bool(high >= stop_price)


def _risk_unit(entry_price: float, side: str, target_price: float | None, stop_price: float | None, opposing_force_price: float | None) -> float:
    candidates: list[float] = []
    if stop_price is not None:
        candidates.append(abs(entry_price - stop_price))
    if target_price is not None:
        candidates.append(abs(target_price - entry_price))
    if opposing_force_price is not None:
        ahead = opposing_force_price >= entry_price if side == "BUY" else opposing_force_price <= entry_price
        if ahead:
            candidates.append(abs(opposing_force_price - entry_price))
    candidates = [item for item in candidates if item > 1e-9]
    return min(candidates) if candidates else 1.0


def _final_outcome_proxy(
    *,
    target_hit: bool,
    stop_hit: bool,
    target_index: int,
    stop_index: int,
    mfe_r: float,
    mae_r: float,
    final_progress: float,
    tolerance: float,
) -> str:
    if target_hit and not stop_hit:
        return "WIN"
    if stop_hit and not target_hit:
        return "LOSS"
    if target_hit and stop_hit:
        if target_index < stop_index:
            return "WIN"
        if stop_index < target_index:
            return "LOSS"
        return "AMBIGUOUS"
    if mfe_r >= 1.0 and final_progress > -tolerance:
        return "WIN"
    if mae_r >= 1.0 and final_progress <= tolerance:
        return "LOSS"
    if final_progress > tolerance and mfe_r >= max(0.35, mae_r * 1.25):
        return "FAVORABLE"
    if final_progress < -tolerance or mae_r >= max(0.35, mfe_r * 1.25):
        return "ADVERSE"
    return "SCRATCH"


def track_candle_outcome_v1(
    entry: Mapping[str, Any] | None,
    candles: Sequence[Mapping[str, Any]] | None = None,
    *,
    side: str | None = None,
    entry_price: float | None = None,
    target_price: float | None = None,
    stop_price: float | None = None,
    opposing_force_price: float | None = None,
) -> dict[str, Any]:
    """Score paper-entry path quality from post-entry candles.

    The tracker is intentionally execution-neutral: it records what would have
    happened after a paper idea and surfaces trap markers for later review.
    """

    entry_context = _mapping(entry)
    rows = _extract_candles(entry_context, candles)
    resolved_side = _side(
        side
        or entry_context.get("side")
        or _mapping(entry_context.get("execution")).get("side")
        or _mapping(entry_context.get("paper_entry")).get("side")
        or _mapping(entry_context.get("market_context")).get("dominant_side")
    )
    if resolved_side not in {"BUY", "SELL"}:
        resolved_side = "BUY"

    resolved_entry_price = _float(entry_price, float("nan")) if entry_price is not None else _entry_price(entry_context, rows)
    if not isfinite(resolved_entry_price):
        resolved_entry_price = 0.0
    resolved_target = target_price if target_price is not None else _level_from_entry(
        entry_context,
        "target_price",
        "target",
        "take_profit_price",
        "take_profit",
    )
    resolved_stop = stop_price if stop_price is not None else _level_from_entry(
        entry_context,
        "stop_price",
        "stop",
        "stop_loss_price",
        "stop_loss",
        "invalidation_price",
        "invalidation",
    )
    resolved_opposing = (
        opposing_force_price
        if opposing_force_price is not None
        else _opposing_force_price(entry_context, resolved_side, resolved_entry_price)
    )

    risk_unit = _risk_unit(resolved_entry_price, resolved_side, resolved_target, resolved_stop, resolved_opposing)
    tolerance = max(1e-9, risk_unit * 0.02)

    mfe = 0.0
    mae = 0.0
    best_index = 0
    worst_index = 0
    target_index = 0
    stop_index = 0
    target_was_hit = False
    stop_was_hit = False
    touched_opposing_force = bool(entry_context.get("touched_opposing_force"))
    returned_to_entry = bool(entry_context.get("returned_to_entry"))
    dominance_weakened = bool(entry_context.get("dominance_weakened"))
    angle_broke = bool(entry_context.get("angle_broke"))

    entry_dominance = _dominance_value(entry_context)
    if entry_dominance is None:
        entry_dominance = _dominance_value(_mapping(entry_context.get("market_context")))
    dominance_drop_threshold = _float(entry_context.get("dominance_drop_threshold"), 0.12)
    entry_angle = _angle_value(entry_context)
    if entry_angle is None:
        entry_angle = _angle_value(_mapping(entry_context.get("angle_context")))

    side_closes = [0.0]
    moved_away_index = 0
    previous_progress = 0.0
    max_drawdown = 0.0
    running_best = 0.0

    for index, row in enumerate(rows, start=1):
        open_price = _candle_value(row, "open", "o", "close", "c", "price_proxy", default=resolved_entry_price)
        high = _candle_value(row, "high", "h", "close", "c", "price_proxy", default=open_price)
        low = _candle_value(row, "low", "l", "close", "c", "price_proxy", default=open_price)
        close = _candle_value(row, "close", "c", "price_proxy", default=open_price)

        favorable = high - resolved_entry_price if resolved_side == "BUY" else resolved_entry_price - low
        adverse = resolved_entry_price - low if resolved_side == "BUY" else high - resolved_entry_price
        favorable = max(0.0, favorable)
        adverse = max(0.0, adverse)
        if favorable > mfe:
            mfe = favorable
            best_index = index
        if adverse > mae:
            mae = adverse
            worst_index = index

        if _target_hit(row, resolved_side, resolved_target) and not target_was_hit:
            target_was_hit = True
            target_index = index
        if _stop_hit(row, resolved_side, resolved_stop) and not stop_was_hit:
            stop_was_hit = True
            stop_index = index

        if resolved_opposing is not None:
            if resolved_side == "BUY":
                touched_opposing_force = touched_opposing_force or high >= resolved_opposing
            else:
                touched_opposing_force = touched_opposing_force or low <= resolved_opposing
        touched_opposing_force = touched_opposing_force or _bool(row.get("touched_opposing_force"))

        progress = close - resolved_entry_price if resolved_side == "BUY" else resolved_entry_price - close
        side_closes.append(progress)
        if moved_away_index == 0 and favorable > tolerance:
            moved_away_index = index
        if moved_away_index and index > moved_away_index and low <= resolved_entry_price <= high:
            returned_to_entry = True
        previous_progress = progress

        running_best = max(running_best, progress)
        max_drawdown = max(max_drawdown, running_best - progress)

        row_dominance = _dominance_value(row)
        if entry_dominance is not None and row_dominance is not None:
            dominance_weakened = dominance_weakened or row_dominance <= max(0.0, entry_dominance - dominance_drop_threshold)
        dominance_state = str(row.get("dominance_state") or "").upper()
        dominance_weakened = dominance_weakened or any(
            token in dominance_state for token in ("WEAKEN", "FADING", "FADE", "ROLLING", "CONFLICT")
        )

        angle_context = _mapping(row.get("angle_context") or row.get("angle_features") or row.get("angle_dynamics"))
        angle_class = str(row.get("angle_class") or angle_context.get("angle_class") or "").upper()
        angle_broke = angle_broke or _bool(row.get("angle_broke"))
        angle_broke = angle_broke or angle_class in {"BROKEN_ANGLE", "ANGLE_BROKE"}
        angle_broke = angle_broke or _clip01(row.get("angle_break_probability"), _clip01(angle_context.get("angle_break_probability"), 0.0)) >= 0.55
        row_angle = _angle_value(row)
        if entry_angle is not None and row_angle is not None and abs(entry_angle) >= 12.0:
            angle_broke = angle_broke or abs(row_angle) <= abs(entry_angle) * 0.45

    total_travel = sum(abs(side_closes[index] - side_closes[index - 1]) for index in range(1, len(side_closes)))
    final_progress = previous_progress
    path_smoothness = _clip01(max(0.0, final_progress) / max(total_travel, 1e-9), 1.0 if not rows else 0.0)
    mfe_r = mfe / max(risk_unit, 1e-9)
    mae_r = mae / max(risk_unit, 1e-9)
    max_drawdown_r = max_drawdown / max(risk_unit, 1e-9)
    outcome = _final_outcome_proxy(
        target_hit=target_was_hit,
        stop_hit=stop_was_hit,
        target_index=target_index,
        stop_index=stop_index,
        mfe_r=mfe_r,
        mae_r=mae_r,
        final_progress=final_progress,
        tolerance=tolerance,
    )

    return {
        "version": CANDLE_OUTCOME_TRACKER_V1,
        "side": resolved_side,
        "entry_price": round(float(resolved_entry_price), 8),
        "target_price": None if resolved_target is None else round(float(resolved_target), 8),
        "stop_price": None if resolved_stop is None else round(float(resolved_stop), 8),
        "opposing_force_price": None if resolved_opposing is None else round(float(resolved_opposing), 8),
        "sample_count": len(rows),
        "mfe": round(float(mfe), 8),
        "mae": round(float(mae), 8),
        "mfe_r": round(float(mfe_r), 4),
        "mae_r": round(float(mae_r), 4),
        "path_smoothness": round(float(path_smoothness), 4),
        "max_drawdown": round(float(max_drawdown), 8),
        "max_drawdown_r": round(float(max_drawdown_r), 4),
        "time_to_best_candles": int(best_index),
        "time_to_worst_candles": int(worst_index),
        "target_hit": target_was_hit,
        "stop_hit": stop_was_hit,
        "target_hit_candle": int(target_index),
        "stop_hit_candle": int(stop_index),
        "touched_opposing_force": bool(touched_opposing_force),
        "returned_to_entry": bool(returned_to_entry),
        "dominance_weakened": bool(dominance_weakened),
        "angle_broke": bool(angle_broke),
        "final_progress": round(float(final_progress), 8),
        "final_progress_r": round(float(final_progress / max(risk_unit, 1e-9)), 4),
        "final_outcome_proxy": outcome,
    }


def track_candle_outcome(
    entry: Mapping[str, Any] | None,
    candles: Sequence[Mapping[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return track_candle_outcome_v1(entry, candles, **kwargs)


__all__ = [
    "CANDLE_OUTCOME_TRACKER_V1",
    "track_candle_outcome",
    "track_candle_outcome_v1",
]
