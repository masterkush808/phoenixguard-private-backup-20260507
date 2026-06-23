from __future__ import annotations

from typing import Any

from .cases import DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, build_adversarial_case


def build_buy_into_supply_case(
    *,
    seed: int = 0,
    frame_count: int = 48,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> dict[str, Any]:
    return build_adversarial_case(
        "buy_into_supply",
        seed=seed,
        frame_count=frame_count,
        symbol=symbol,
        timeframe=timeframe,
    )


def build_sell_into_demand_case(
    *,
    seed: int = 0,
    frame_count: int = 48,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> dict[str, Any]:
    return build_adversarial_case(
        "sell_into_demand",
        seed=seed,
        frame_count=frame_count,
        symbol=symbol,
        timeframe=timeframe,
    )


__all__ = ["build_buy_into_supply_case", "build_sell_into_demand_case"]
