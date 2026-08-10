from __future__ import annotations

from typing import Any, Mapping, Sequence

from phoenixguard.decision.two_candle_study_v3 import build_two_candle_study_v3


def build_high_frequency_candle_forecast(
    *,
    candles: Sequence[Mapping[str, Any]],
    image_size: tuple[int, int] | Sequence[int] = (1, 1),
    timeframe: str = "",
    candidate_action: str = "HOLD",
    global_direction: str = "HOLD",
    local_direction: str = "HOLD",
    impulse_direction: str = "HOLD",
    decision_kernel: Mapping[str, Any] | None = None,
    candle_statistics: Mapping[str, Any] | None = None,
    behavior: Mapping[str, Any] | None = None,
    setup: str = "",
    frame_id: int | str = 0,
    sequence_id: str = "",
    scene_forecast_contribution: Mapping[str, Any] | None = None,
    model_council: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility entry point for the old micro-candle forecast name.

    The V3 implementation is intentionally study-only. It reports next-1 and
    next-2 candle bias, confidence, wick risk, and continuation/reversal risk,
    but it never returns invented future OHLC candles.
    """

    return build_two_candle_study_v3(
        candles=candles,
        image_size=image_size,
        timeframe=timeframe,
        candidate_action=candidate_action,
        global_direction=global_direction,
        local_direction=local_direction,
        impulse_direction=impulse_direction,
        decision_kernel=decision_kernel,
        candle_statistics=candle_statistics,
        behavior=behavior,
        setup=setup,
        frame_id=frame_id,
        sequence_id=sequence_id,
        scene_forecast_contribution=scene_forecast_contribution,
        model_council=model_council,
    )


__all__ = ["build_high_frequency_candle_forecast"]
