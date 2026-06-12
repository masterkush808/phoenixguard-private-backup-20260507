from __future__ import annotations

from statistics import mean
from typing import Any, Mapping, Sequence


PAIR_BEHAVIOR_PROFILE_VERSION = "PG_PAIR_BEHAVIOR_PROFILE_V3"


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
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _candle_value(row: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in row:
            return _float(row.get(key), default)
    return float(default)


def _ohlc(row: Mapping[str, Any]) -> tuple[float, float, float, float]:
    fallback = _candle_value(row, "close", "c", "price_proxy", "y", default=0.0)
    open_value = _candle_value(row, "open", "o", default=fallback)
    close_value = _candle_value(row, "close", "c", "price_proxy", "y", default=fallback)
    high_value = _candle_value(row, "high", "h", default=max(open_value, close_value))
    low_value = _candle_value(row, "low", "l", default=min(open_value, close_value))
    if high_value < low_value:
        high_value, low_value = low_value, high_value
    return open_value, high_value, low_value, close_value


def _candidate_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "tracked_candles", "candle_map", "ohlc"):
        rows = _rows(snapshot.get(key))
        if rows:
            return rows
    tracking = _mapping(snapshot.get("tracking_summary"))
    for key in ("candles", "tracked_candles", "candle_map", "ohlc"):
        rows = _rows(tracking.get(key))
        if rows:
            return rows
    return []


def _timeframe_seconds(timeframe: Any, default: int = 300) -> int:
    text = str(timeframe or "").strip().upper()
    if not text:
        return int(default)
    if text.startswith("S"):
        return max(1, _int(text[1:], default))
    if text.startswith("M"):
        return max(1, _int(text[1:], default // 60) * 60)
    if text.endswith("M"):
        return max(1, _int(text[:-1], default // 60) * 60)
    if text.startswith("H"):
        return max(1, _int(text[1:], 1) * 3600)
    return int(default)


def _volatility_class(wick_to_body_ratio: float, average_range: float, average_body: float) -> str:
    if wick_to_body_ratio >= 1.45:
        return "WICKY_HIGH"
    if wick_to_body_ratio >= 0.92:
        return "WICKY_MEDIUM"
    if average_body <= average_range * 0.22:
        return "LOW_BODY_CHOP"
    if average_range <= 1e-9:
        return "LOW_VOLATILITY"
    return "NORMAL"


def analyze_pair_behavior_profile_v3(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(snapshot or {})
    symbol = str(source.get("symbol") or source.get("market") or source.get("pair") or "UNKNOWN").strip().upper()
    timeframe = str(source.get("timeframe") or source.get("focus_timeframe") or "M5").strip().upper()
    explicit = _mapping(source.get("pair_profile") or source.get("pair_behavior_profile"))
    candles = [_ohlc(row) for row in _candidate_rows(source)]
    bodies = [abs(row[3] - row[0]) for row in candles]
    ranges = [max(0.0, row[1] - row[2]) for row in candles]
    wicks = [max(0.0, (row[1] - row[2]) - abs(row[3] - row[0])) for row in candles]
    average_body_size = _float(explicit.get("average_candle_body_size"), mean(bodies) if bodies else 0.0)
    average_wick_size = _float(explicit.get("average_wick_size"), mean(wicks) if wicks else 0.0)
    average_range = _float(explicit.get("average_candle_range"), mean(ranges) if ranges else average_body_size + average_wick_size)
    wick_to_body_ratio = _float(explicit.get("wick_to_body_ratio"), average_wick_size / max(average_body_size, 1e-9))
    volatility_class = str(explicit.get("volatility_class") or _volatility_class(wick_to_body_ratio, average_range, average_body_size))

    fakeout_frequency = _clip01(
        explicit.get(
            "fakeout_frequency",
            max(
                _clip01(source.get("fakeout_probability"), 0.0),
                _clip01(source.get("false_breakout_probability"), 0.0),
                min(0.78, wick_to_body_ratio / 2.4),
            ),
        ),
        0.0,
    )
    drawdown_first_frequency = _clip01(
        explicit.get(
            "drawdown_first_frequency",
            max(0.18, min(0.72, 0.18 + 0.22 * fakeout_frequency + 0.20 * min(1.0, wick_to_body_ratio / 1.8))),
        ),
        0.37,
    )
    typical_pullback_depth = _clip01(
        explicit.get("typical_pullback_depth", source.get("typical_pullback_depth", source.get("pullback_depth", 0.38))),
        0.38,
    )
    typical_continuation_time = _int(
        explicit.get("typical_continuation_time_sec", source.get("typical_continuation_time_sec")),
        _timeframe_seconds(timeframe),
    )
    typical_reversal_time = _int(
        explicit.get("typical_reversal_time_sec", source.get("typical_reversal_time_sec")),
        max(_timeframe_seconds(timeframe), int(_timeframe_seconds(timeframe) * 1.4)),
    )
    preferred_expiry_sec = _int(
        explicit.get("preferred_expiry_sec", source.get("preferred_expiry_sec", source.get("expiry_seconds"))),
        _timeframe_seconds(timeframe),
    )
    late_chase_failure_rate = _clip01(
        explicit.get("late_chase_failure_rate", min(0.84, 0.28 + 0.36 * drawdown_first_frequency + 0.18 * fakeout_frequency)),
        0.48,
    )
    warning = ""
    if wick_to_body_ratio >= 0.92 or drawdown_first_frequency >= 0.42:
        warning = "This pair often wicks or pulls back before continuation."
    elif late_chase_failure_rate >= 0.58:
        warning = "Late chase entries have elevated failure risk on this pair/timeframe."

    return {
        "version": PAIR_BEHAVIOR_PROFILE_VERSION,
        "pair_profile": {
            "symbol": symbol,
            "timeframe": timeframe,
            "average_candle_body_size": round(float(average_body_size), 8),
            "average_wick_size": round(float(average_wick_size), 8),
            "average_candle_range": round(float(average_range), 8),
            "wick_to_body_ratio": round(float(wick_to_body_ratio), 4),
            "fakeout_frequency": round(fakeout_frequency, 4),
            "typical_pullback_depth": round(typical_pullback_depth, 4),
            "typical_continuation_time_sec": int(typical_continuation_time),
            "typical_reversal_time_sec": int(typical_reversal_time),
            "volatility_class": volatility_class,
            "drawdown_first_frequency": round(drawdown_first_frequency, 4),
            "preferred_expiry_sec": int(preferred_expiry_sec),
            "best_expiry_bands": {
                "min": max(15, int(preferred_expiry_sec * 0.70)),
                "preferred": int(preferred_expiry_sec),
                "max": max(int(preferred_expiry_sec), int(preferred_expiry_sec * 1.60)),
            },
            "late_chase_failure_rate": round(late_chase_failure_rate, 4),
            "warning": warning,
        },
    }


def update_pair_profile_from_outcome(profile: Mapping[str, Any], outcome: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(profile)
    result = dict(outcome)
    alpha = _clip01(result.get("learning_rate"), 0.08)
    drawdown_first = 1.0 if str(result.get("max_adverse_excursion") or "").strip() and _float(result.get("max_adverse_excursion"), 0.0) > 0.0 else 0.0
    late_failure = 1.0 if str(result.get("lesson") or "").upper().find("LATE") >= 0 or str(result.get("result") or "").upper() in {"LOSS", "FAILED"} else 0.0
    current["drawdown_first_frequency"] = round(
        (1.0 - alpha) * _clip01(current.get("drawdown_first_frequency"), 0.37) + alpha * drawdown_first,
        4,
    )
    current["late_chase_failure_rate"] = round(
        (1.0 - alpha) * _clip01(current.get("late_chase_failure_rate"), 0.48) + alpha * late_failure,
        4,
    )
    current["updated_from_outcome"] = True
    current["last_candidate_id"] = str(result.get("candidate_id") or "")
    return current


__all__ = [
    "PAIR_BEHAVIOR_PROFILE_VERSION",
    "analyze_pair_behavior_profile_v3",
    "update_pair_profile_from_outcome",
]
