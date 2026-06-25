from __future__ import annotations

from collections.abc import Callable
from typing import cast

import numpy as np
from numpy.typing import NDArray

from _pg_bootstrap import ensure_project_paths

ensure_project_paths()

from phoenixguard.decision import regression_module as _impl
from phoenixguard.decision.regression_module import (
    ChronosRegressor,
    Forecast3MCore,
    Forecast3MOutput,
    ForecastRouter,
    ImageFusionRegressor,
    conformal_interval,
)


def _accumulation_distribution(ohlc: list[list[float]], body_sizes: NDArray[np.float32]) -> float:
    impl = cast(Callable[[list[list[float]], NDArray[np.float32]], float], getattr(_impl, "_accumulation_distribution"))
    return impl(ohlc, body_sizes)


def _conformal_interval(returns: NDArray[np.float32], alpha: float = 0.05) -> tuple[float, float]:
    impl = cast(Callable[[NDArray[np.float32], float], tuple[float, float]], getattr(_impl, "_conformal_interval"))
    return impl(returns, alpha)


def _poly_trend(closes: NDArray[np.float32], degree: int = 2) -> dict[str, float]:
    impl = cast(Callable[[NDArray[np.float32], int], dict[str, float]], getattr(_impl, "_poly_trend"))
    return impl(closes, degree)

__all__ = [
    "ChronosRegressor",
    "Forecast3MCore",
    "Forecast3MOutput",
    "ForecastRouter",
    "ImageFusionRegressor",
    "_accumulation_distribution",
    "_conformal_interval",
    "_poly_trend",
    "conformal_interval",
]
