from __future__ import annotations

from statistics import mean, pstdev
from typing import Any, Mapping, Sequence, cast


REGIME_ENGINE_VERSION = "PG_REGIME_ENGINE_V3"

REGIME_CLASSES = {
    "TRENDING_UP",
    "TRENDING_DOWN",
    "RANGING",
    "CHOPPY",
    "VOLATILE_WICKY",
    "LOW_VOLATILITY_GRIND",
    "POST_IMPULSE",
    "PULLBACK_PHASE",
    "REVERSAL_FORMING",
    "BREAKOUT_PHASE",
    "FAKEOUT_RISK",
    "COMPRESSION",
    "EXPANSION",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, Any], item)) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


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


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "pass", "passed"}
    return bool(value)


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text or default


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


def _trend_side_from_candles(candles: Sequence[tuple[float, float, float, float]]) -> str:
    if len(candles) < 2:
        return "HOLD"
    closes = [row[3] for row in candles]
    delta = closes[-1] - closes[0]
    price_span = max(1e-9, max(closes) - min(closes))
    if abs(delta) / price_span < 0.18:
        return "HOLD"
    return "BUY" if delta > 0 else "SELL"


def _volatility_features(candles: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float]:
    if not candles:
        return 0.0, 0.0, 0.0
    ranges = [max(0.0, row[1] - row[2]) for row in candles]
    bodies = [abs(row[3] - row[0]) for row in candles]
    wick_sizes = [max(0.0, (row[1] - row[2]) - abs(row[3] - row[0])) for row in candles]
    avg_range = mean(ranges) if ranges else 0.0
    range_std = pstdev(ranges) if len(ranges) >= 2 else 0.0
    avg_body = mean(bodies) if bodies else 0.0
    avg_wick = mean(wick_sizes) if wick_sizes else 0.0
    wick_to_body = avg_wick / max(avg_body, 1e-9)
    normalized_volatility = _clip01(range_std / max(avg_range, 1e-9), 0.0)
    range_to_body = avg_range / max(avg_body, 1e-9)
    return normalized_volatility, _clip01(wick_to_body / 2.8, 0.0), _clip01(range_to_body / 4.0, 0.0)


def _primary_from_explicit(snapshot: Mapping[str, Any]) -> str:
    for key in ("market_regime", "regime", "primary_regime"):
        value = _upper(snapshot.get(key))
        if value in REGIME_CLASSES:
            return value
    raw = _mapping(snapshot.get("regime"))
    value = _upper(raw.get("primary"))
    return value if value in REGIME_CLASSES else ""


def analyze_regime_v3(
    snapshot: Mapping[str, Any] | None,
    *,
    side: str | None = None,
    price_location: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = dict(snapshot or {})
    market_context = _mapping(source.get("market_context"))
    angle = _mapping(source.get("angle_features") or source.get("angle_context") or source.get("angle_dynamics"))
    candles = [_ohlc(row) for row in _candidate_rows(source)]
    recent = candles[-min(20, len(candles)) :]
    trend_side = _trend_side_from_candles(recent)
    global_side = _side(source.get("global_side") or market_context.get("global_side") or _mapping(source.get("global_structure")).get("global_side"))
    local_side = _side(source.get("local_side") or market_context.get("local_side") or _mapping(source.get("local_micro_structure")).get("local_side"))
    resolved_side = _side(side or source.get("candidate_side") or market_context.get("dominant_side"))
    volatility, wick_risk, range_to_body = _volatility_features(recent)
    explicit = _primary_from_explicit(source)

    pullback_active = _bool(source.get("pullback_confirmed") or source.get("retest_confirmed") or market_context.get("pullback_active"))
    reversal_forming = _bool(source.get("reversal_confirmed") or source.get("reversal_forming") or market_context.get("is_reversal_confirmed"))
    breakout_phase = _bool(source.get("breakout_confirmed") or source.get("breakout_phase"))
    fakeout_risk = max(
        _clip01(source.get("false_breakout_probability"), 0.0),
        _clip01(source.get("fakeout_probability"), 0.0),
        0.70 if _bool(source.get("liquidity_sweep_detected") and not source.get("breakout_reclaimed")) else 0.0,
    )
    compression_score = max(_clip01(source.get("compression_score"), 0.0), _clip01(source.get("consolidation_score"), 0.0))
    impulse_length = max(_clip01(angle.get("impulse_length"), 0.0), _clip01(source.get("impulse_length"), 0.0))
    late_chase = _bool(angle.get("late_chase_risk") or angle.get("post_impulse_wait_required") or market_context.get("is_late_chase"))

    if explicit:
        primary = explicit
    elif fakeout_risk >= 0.64:
        primary = "FAKEOUT_RISK"
    elif reversal_forming:
        primary = "REVERSAL_FORMING"
    elif pullback_active:
        primary = "PULLBACK_PHASE"
    elif late_chase or impulse_length >= 0.72:
        primary = "POST_IMPULSE"
    elif breakout_phase:
        primary = "BREAKOUT_PHASE"
    elif wick_risk >= 0.62 or range_to_body >= 0.72:
        primary = "VOLATILE_WICKY"
    elif compression_score >= 0.60:
        primary = "COMPRESSION"
    elif volatility >= 0.62:
        primary = "EXPANSION"
    elif global_side == local_side == "BUY" or trend_side == "BUY":
        primary = "TRENDING_UP"
    elif global_side == local_side == "SELL" or trend_side == "SELL":
        primary = "TRENDING_DOWN"
    elif global_side in {"BUY", "SELL"} and local_side in {"BUY", "SELL"} and global_side != local_side:
        primary = "CHOPPY"
    elif volatility <= 0.12 and len(recent) >= 8:
        primary = "LOW_VOLATILITY_GRIND"
    else:
        primary = "RANGING"

    secondary = "NONE"
    if primary not in {"TRENDING_UP", "TRENDING_DOWN"}:
        if global_side == "BUY":
            secondary = "TRENDING_UP"
        elif global_side == "SELL":
            secondary = "TRENDING_DOWN"
    if primary != "PULLBACK_PHASE" and pullback_active:
        secondary = "PULLBACK_PHASE"
    if primary != "FAKEOUT_RISK" and fakeout_risk >= 0.45:
        secondary = "FAKEOUT_RISK"

    if wick_risk >= 0.62:
        volatility_profile = "WICKY"
    elif volatility >= 0.58:
        volatility_profile = "EXPANDING"
    elif volatility <= 0.14:
        volatility_profile = "LOW"
    else:
        volatility_profile = "NORMAL"

    preferred_lanes = ["FAILED_RETEST_ENTRY", "PULLBACK_CONTINUATION_ENTRY"]
    forbidden_lanes = ["LATE_MOMENTUM_CHASE"]
    if primary in {"TRENDING_UP", "TRENDING_DOWN", "PULLBACK_PHASE"}:
        preferred_lanes = ["PULLBACK_CONTINUATION_ENTRY", "FAILED_RETEST_ENTRY", "SNIPER_ZONE_ENTRY"]
    elif primary in {"RANGING", "CHOPPY", "COMPRESSION"}:
        preferred_lanes = ["RANGE_REACTION_ENTRY", "FAILED_RETEST_ENTRY"]
        forbidden_lanes = ["LATE_MOMENTUM_CHASE", "MIDDLE_RANGE_CHASE"]
    elif primary in {"POST_IMPULSE", "FAKEOUT_RISK", "REVERSAL_FORMING"}:
        preferred_lanes = ["WAIT_FOR_RETEST", "REVERSAL_CONFIRMATION_ENTRY"]
        forbidden_lanes = ["LATE_MOMENTUM_CHASE", "BREAKOUT_CHASE"]

    location = _upper((price_location or {}).get("relative_location"))
    if location == "MIDDLE" and "MIDDLE_RANGE_CHASE" not in forbidden_lanes:
        forbidden_lanes.append("MIDDLE_RANGE_CHASE")

    reason = (
        f"{primary} is active; volatility={volatility_profile}, wick_risk={wick_risk:.2f}, fakeout_risk={fakeout_risk:.2f}."
    )
    return {
        "version": REGIME_ENGINE_VERSION,
        "regime": {
            "primary": primary,
            "secondary": secondary,
            "side": resolved_side,
            "trend_side": trend_side,
            "global_side": global_side,
            "local_side": local_side,
            "volatility_profile": volatility_profile,
            "wick_risk": round(wick_risk, 4),
            "fakeout_risk": round(fakeout_risk, 4),
            "compression_score": round(compression_score, 4),
            "normalized_volatility": round(volatility, 4),
            "preferred_lanes": preferred_lanes,
            "forbidden_lanes": forbidden_lanes,
            "reason": reason,
        },
    }


__all__ = [
    "REGIME_CLASSES",
    "REGIME_ENGINE_VERSION",
    "analyze_regime_v3",
]
