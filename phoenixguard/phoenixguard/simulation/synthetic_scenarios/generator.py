from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SYNTHETIC_SCENARIO_VERSION = "PHOENIX_SYNTHETIC_MARKET_SCENARIOS_V1"

DEFAULT_SYMBOL = "PHOENIX/SYNTH"
DEFAULT_TIMEFRAME = "M1"

PLAN_SYNTHETIC_MARKET_CATEGORIES: tuple[str, ...] = (
    "steep_bullish_impulse_then_reversal",
    "steep_bearish_impulse_then_reversal",
    "buy_into_supply",
    "sell_into_demand",
    "fake_breakout",
    "fake_breakdown",
    "range_chop",
    "liquidity_sweep_then_snapback",
    "healthy_pullback_continuation",
    "trend_continuation_after_retest",
    "middle_safe_continuation",
    "middle_danger_entry",
    "angle_break_after_vertical_move",
)

LEGACY_SYNTHETIC_MARKET_CATEGORIES: tuple[str, ...] = (
    "trend_up",
    "trend_down",
    "range_chop",
    "breakout_continuation",
    "breakout_failure",
    "liquidity_sweep",
    "volatility_expansion",
    "volatility_compression",
    "gap_reprice",
    "mean_reversion",
    "slow_grind",
    "impulse_reversal",
)

SYNTHETIC_MARKET_CATEGORIES: tuple[str, ...] = tuple(
    dict.fromkeys((*PLAN_SYNTHETIC_MARKET_CATEGORIES, *LEGACY_SYNTHETIC_MARKET_CATEGORIES))
)

_SYNTHETIC_BASE_CATEGORY: dict[str, str] = {
    "steep_bullish_impulse_then_reversal": "impulse_reversal",
    "steep_bearish_impulse_then_reversal": "bearish_impulse_reversal",
    "buy_into_supply": "breakout_failure",
    "sell_into_demand": "fake_breakdown_base",
    "fake_breakout": "breakout_failure",
    "fake_breakdown": "fake_breakdown_base",
    "liquidity_sweep_then_snapback": "liquidity_sweep",
    "healthy_pullback_continuation": "slow_grind",
    "trend_continuation_after_retest": "breakout_continuation",
    "middle_safe_continuation": "trend_up",
    "middle_danger_entry": "mean_reversion",
    "angle_break_after_vertical_move": "impulse_reversal",
}

_TRADE_ACTIONS = ("HOLD", "BUY", "SELL")


def normalize_synthetic_category(category: str) -> str:
    normalized = str(category or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in SYNTHETIC_MARKET_CATEGORIES:
        allowed = ", ".join(SYNTHETIC_MARKET_CATEGORIES)
        raise ValueError(f"unknown synthetic category {category!r}; expected one of: {allowed}")
    return normalized


def _base_category(category: str) -> str:
    return _SYNTHETIC_BASE_CATEGORY.get(category, category)


def generate_synthetic_market_scenario(
    category: str = "trend_up",
    *,
    seed: int = 0,
    frame_count: int = 48,
    start_price: float = 100.0,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    horizon: int = 3,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic offline market scenario as plain Python dictionaries."""
    normalized = normalize_synthetic_category(category)
    frame_total = _coerce_frame_count(frame_count)
    label_horizon = max(1, int(horizon))
    base_price = max(float(start_price), 0.0001)
    stable_seed = _stable_seed(seed, normalized, frame_total)
    rng = random.Random(stable_seed)
    scenario_key = scenario_id or f"synthetic:{normalized}:{int(seed)}:{frame_total}"

    candles = _generate_candles(
        normalized,
        rng=rng,
        frame_count=frame_total,
        start_price=base_price,
    )
    labels = _build_labels(
        scenario_id=scenario_key,
        category=normalized,
        candles=candles,
        horizon=label_horizon,
    )
    frames = _build_frames(
        scenario_id=scenario_key,
        category=normalized,
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
    )

    return {
        "version": SYNTHETIC_SCENARIO_VERSION,
        "scenario_id": scenario_key,
        "category": normalized,
        "symbol": str(symbol),
        "timeframe": str(timeframe),
        "frames": frames,
        "labels": labels,
        "expected": _build_expected_metadata(normalized, labels),
        "metadata": {
            "seed": int(seed),
            "stable_seed": stable_seed,
            "frame_count": frame_total,
            "horizon": label_horizon,
            "start_price": base_price,
            "deterministic": True,
            "offline_only": True,
            "broker_actions_allowed": False,
        },
    }


def generate_synthetic_market_suite(
    categories: Iterable[str] | None = None,
    *,
    seed: int = 0,
    frame_count: int = 48,
    start_price: float = 100.0,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    horizon: int = 3,
) -> list[dict[str, Any]]:
    """Generate one deterministic scenario per requested category."""
    selected = tuple(categories) if categories is not None else SYNTHETIC_MARKET_CATEGORIES
    scenarios: list[dict[str, Any]] = []
    for index, category in enumerate(selected):
        normalized = normalize_synthetic_category(category)
        scenarios.append(
            generate_synthetic_market_scenario(
                normalized,
                seed=int(seed) + index,
                frame_count=frame_count,
                start_price=start_price,
                symbol=symbol,
                timeframe=timeframe,
                horizon=horizon,
            )
        )
    return scenarios


def export_scenarios_json(
    scenarios: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    path: str | Path,
    *,
    indent: int = 2,
) -> Path:
    """Export one or more scenario dictionaries to a JSON file."""
    if isinstance(scenarios, Mapping):
        scenario_list = [copy.deepcopy(dict(scenarios))]
    else:
        scenario_list = [copy.deepcopy(dict(scenario)) for scenario in scenarios]

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SYNTHETIC_SCENARIO_VERSION,
        "scenario_count": len(scenario_list),
        "offline_only": True,
        "broker_actions_allowed": False,
        "scenarios": scenario_list,
    }
    destination.write_text(
        json.dumps(payload, indent=indent, sort_keys=True),
        encoding="utf-8",
    )
    return destination


def _coerce_frame_count(frame_count: int) -> int:
    total = int(frame_count)
    if total < 8:
        raise ValueError("frame_count must be at least 8 for scenario labels")
    return total


def _stable_seed(seed: int, category: str, frame_count: int) -> int:
    material = f"{int(seed)}:{category}:{int(frame_count)}".encode("ascii")
    return int(hashlib.sha256(material).hexdigest()[:16], 16)


def _generate_candles(
    category: str,
    *,
    rng: random.Random,
    frame_count: int,
    start_price: float,
) -> list[dict[str, float]]:
    price = start_price
    candles: list[dict[str, float]] = []
    for index in range(frame_count):
        open_price = price
        base_category = _base_category(category)
        delta = _category_delta(base_category, index, frame_count, price, start_price, rng)
        close_price = max(0.0001, open_price + delta)
        wick = _wick_size(base_category, index, frame_count, delta, rng)
        upper_wick = wick * (0.65 + rng.random() * 0.55)
        lower_wick = wick * (0.65 + rng.random() * 0.55)
        high = max(open_price, close_price) + upper_wick
        low = max(0.0001, min(open_price, close_price) - lower_wick)
        volume = _volume_for(category, index, frame_count, abs(delta), rng)
        candles.append(
            {
                "open": _round_price(open_price),
                "high": _round_price(high),
                "low": _round_price(low),
                "close": _round_price(close_price),
                "volume": round(volume, 4),
            }
        )
        price = close_price
    return candles


def _category_delta(
    category: str,
    index: int,
    frame_count: int,
    price: float,
    start_price: float,
    rng: random.Random,
) -> float:
    midpoint = frame_count // 2
    progress = index / max(frame_count - 1, 1)
    wave = math.sin(index * 0.73) * 0.012
    noise = (rng.random() - 0.5) * 0.018

    if category == "trend_up":
        return 0.062 + wave + noise
    if category == "trend_down":
        return -0.062 + wave - noise
    if category == "range_chop":
        return ((-1.0) ** index) * (0.052 + rng.random() * 0.021) + (start_price - price) * 0.08
    if category == "breakout_continuation":
        if index < int(frame_count * 0.55):
            return ((-1.0) ** index) * 0.018 + noise
        return 0.118 if index == int(frame_count * 0.55) else 0.073 + wave + noise
    if category == "breakout_failure":
        if index < int(frame_count * 0.45):
            return ((-1.0) ** index) * 0.019 + noise
        if index == int(frame_count * 0.45):
            return 0.162
        return -0.083 + wave - noise
    if category == "liquidity_sweep":
        if index == midpoint - 1:
            return -0.168
        if index == midpoint:
            return 0.194
        return 0.019 + math.sin(index * 0.61) * 0.027 + noise
    if category == "volatility_expansion":
        amplitude = 0.018 + progress * 0.126
        return math.sin(index * 1.18) * amplitude + noise
    if category == "volatility_compression":
        amplitude = 0.124 - progress * 0.098
        return math.sin(index * 1.05) * amplitude + noise * 0.5
    if category == "gap_reprice":
        if index == midpoint:
            return 0.238
        post_gap_bias = 0.021 if index > midpoint else 0.0
        return post_gap_bias + ((-1.0) ** index) * 0.018 + noise
    if category == "mean_reversion":
        return (start_price - price) * 0.14 + math.sin(index * 0.91) * 0.038 + noise
    if category == "slow_grind":
        return 0.027 + math.sin(index * 0.35) * 0.006 + noise * 0.35
    if category == "impulse_reversal":
        if index < int(frame_count * 0.28):
            return 0.124 + wave + noise
        if index < int(frame_count * 0.36):
            return 0.034 + noise
        return -0.091 + wave - noise
    if category == "bearish_impulse_reversal":
        if index < int(frame_count * 0.28):
            return -0.124 + wave - noise
        if index < int(frame_count * 0.36):
            return -0.034 + noise
        return 0.091 + wave + noise
    if category == "fake_breakdown_base":
        if index < int(frame_count * 0.45):
            return ((-1.0) ** index) * 0.019 + noise
        if index == int(frame_count * 0.45):
            return -0.162
        return 0.083 + wave + noise

    raise ValueError(f"unhandled synthetic category {category!r}")


def _wick_size(
    category: str,
    index: int,
    frame_count: int,
    delta: float,
    rng: random.Random,
) -> float:
    progress = index / max(frame_count - 1, 1)
    base = 0.018 + abs(delta) * 0.33 + rng.random() * 0.012
    if category in {"volatility_expansion", "news_like_spike"}:
        return base * (1.0 + progress)
    if category in {"liquidity_sweep", "breakout_failure", "impulse_reversal", "bearish_impulse_reversal", "fake_breakdown_base"}:
        return base * 1.55
    if category == "volatility_compression":
        return base * max(0.45, 1.3 - progress)
    return base


def _volume_for(
    category: str,
    index: int,
    frame_count: int,
    move_size: float,
    rng: random.Random,
) -> float:
    midpoint = frame_count // 2
    base = 1.0 + move_size * 5.0 + rng.random() * 0.18
    if category in {"breakout_continuation", "breakout_failure", "gap_reprice"} and abs(index - midpoint) <= 2:
        return base * 2.2
    if category in {"liquidity_sweep", "impulse_reversal", "bearish_impulse_reversal", "fake_breakdown_base"} and index >= midpoint - 2:
        return base * 1.7
    if category == "volatility_expansion":
        return base * (1.0 + index / max(frame_count - 1, 1))
    return base


def _build_frames(
    *,
    scenario_id: str,
    category: str,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, float]],
) -> list[dict[str, Any]]:
    closes = [float(candle["close"]) for candle in candles]
    frames: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        body = close_price - open_price
        candle_range = max(high - low, 0.0)
        frames.append(
            {
                "frame_id": f"{scenario_id}:frame:{index:04d}",
                "label_id": f"{scenario_id}:label:{index:04d}",
                "index": index,
                "timestamp": _timestamp_for_index(index),
                "symbol": str(symbol),
                "timeframe": str(timeframe),
                "ohlc": {
                    "open": _round_price(open_price),
                    "high": _round_price(high),
                    "low": _round_price(low),
                    "close": _round_price(close_price),
                    "volume": round(float(candle["volume"]), 4),
                },
                "features": {
                    "synthetic_category": category,
                    "body": _round_price(body),
                    "body_abs": _round_price(abs(body)),
                    "range": _round_price(candle_range),
                    "direction": _action_from_delta(body),
                    "rolling_return_3": _round_price(_rolling_return(closes, index, 3)),
                    "rolling_return_5": _round_price(_rolling_return(closes, index, 5)),
                    "rolling_volatility_5": _round_price(_rolling_volatility(closes, index, 5)),
                    "upper_wick": _round_price(high - max(open_price, close_price)),
                    "lower_wick": _round_price(min(open_price, close_price) - low),
                },
            }
        )
    return frames


def _build_labels(
    *,
    scenario_id: str,
    category: str,
    candles: Sequence[Mapping[str, float]],
    horizon: int,
) -> list[dict[str, Any]]:
    closes = [float(candle["close"]) for candle in candles]
    labels: list[dict[str, Any]] = []
    for index, close in enumerate(closes):
        future_index = min(index + horizon, len(closes) - 1)
        future_close = closes[future_index]
        future_return = (future_close - close) / max(close, 0.0001)
        target_action = _action_from_return(future_return)
        risk_state = _risk_state_for(category, index, len(candles))
        labels.append(
            {
                "label_id": f"{scenario_id}:label:{index:04d}",
                "frame_id": f"{scenario_id}:frame:{index:04d}",
                "frame_index": index,
                "horizon": int(horizon),
                "future_index": future_index,
                "future_return": round(float(future_return), 7),
                "target_action": target_action,
                "target_direction": target_action,
                "confidence": _label_confidence(future_return, target_action),
                "risk_state": risk_state,
                "trade_allowed": target_action != "HOLD" and risk_state not in {"TRAP", "UNSTABLE"},
            }
        )
    return labels


def _build_expected_metadata(category: str, labels: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {action: 0 for action in _TRADE_ACTIONS}
    for label in labels:
        action = str(label.get("target_action", "HOLD")).upper()
        if action in counts:
            counts[action] += 1

    dominant_action = max(counts, key=lambda action: counts[action])
    tradeable_labels = sum(1 for label in labels if bool(label.get("trade_allowed")))
    return {
        "primary_behavior": category,
        "dominant_action": dominant_action,
        "action_distribution": counts,
        "tradeable_frame_count": tradeable_labels,
        "safe_actions": [action for action, count in counts.items() if count > 0],
        "offline_only": True,
        "broker_actions_allowed": False,
        "should_trigger_broker_action": False,
    }


def _risk_state_for(category: str, index: int, frame_count: int) -> str:
    category = _base_category(category)
    progress = index / max(frame_count - 1, 1)
    if category in {"breakout_failure", "liquidity_sweep", "impulse_reversal", "bearish_impulse_reversal", "fake_breakdown_base"} and progress >= 0.35:
        return "TRAP"
    if category in {"range_chop", "volatility_expansion"}:
        return "UNSTABLE"
    if category == "gap_reprice" and abs(index - frame_count // 2) <= 1:
        return "UNSTABLE"
    return "NORMAL"


def _label_confidence(future_return: float, target_action: str) -> float:
    if target_action == "HOLD":
        return 0.55
    scaled = 0.56 + min(abs(future_return) / 0.0035, 1.0) * 0.36
    return round(float(min(0.94, scaled)), 4)


def _action_from_delta(delta: float) -> str:
    if delta > 0.00001:
        return "BUY"
    if delta < -0.00001:
        return "SELL"
    return "HOLD"


def _action_from_return(future_return: float) -> str:
    if future_return > 0.00035:
        return "BUY"
    if future_return < -0.00035:
        return "SELL"
    return "HOLD"


def _rolling_return(closes: Sequence[float], index: int, window: int) -> float:
    start = max(0, index - int(window))
    if start == index:
        return 0.0
    return (closes[index] - closes[start]) / max(closes[start], 0.0001)


def _rolling_volatility(closes: Sequence[float], index: int, window: int) -> float:
    start = max(1, index - int(window) + 1)
    returns = [
        abs((closes[i] - closes[i - 1]) / max(closes[i - 1], 0.0001))
        for i in range(start, index + 1)
    ]
    return sum(returns) / len(returns) if returns else 0.0


def _timestamp_for_index(index: int) -> str:
    minute = int(index)
    hour = minute // 60
    minute_in_hour = minute % 60
    return f"2026-01-01T{hour:02d}:{minute_in_hour:02d}:00Z"


def _round_price(value: float) -> float:
    return round(float(value), 5)


__all__ = [
    "DEFAULT_SYMBOL",
    "DEFAULT_TIMEFRAME",
    "SYNTHETIC_MARKET_CATEGORIES",
    "SYNTHETIC_SCENARIO_VERSION",
    "export_scenarios_json",
    "generate_synthetic_market_scenario",
    "generate_synthetic_market_suite",
    "normalize_synthetic_category",
]
