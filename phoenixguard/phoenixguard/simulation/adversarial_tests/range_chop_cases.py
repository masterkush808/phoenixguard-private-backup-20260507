from __future__ import annotations

from typing import Any

from .cases import DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, build_adversarial_case


def build_range_chop_trap_case(
    *,
    seed: int = 0,
    frame_count: int = 48,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> dict[str, Any]:
    return build_adversarial_case(
        "range_chop_trap",
        seed=seed,
        frame_count=frame_count,
        symbol=symbol,
        timeframe=timeframe,
    )


def build_mid_range_no_edge_case(
    *,
    seed: int = 0,
    frame_count: int = 48,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> dict[str, Any]:
    return build_adversarial_case(
        "mid_range_no_edge",
        seed=seed,
        frame_count=frame_count,
        symbol=symbol,
        timeframe=timeframe,
    )


__all__ = ["build_mid_range_no_edge_case", "build_range_chop_trap_case"]
