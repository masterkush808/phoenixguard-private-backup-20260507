from __future__ import annotations

from phoenixguard.simulation.event_backtesting import (
    CandleBacktestConfig,
    run_event_candle_backtest,
    run_parameter_sweep,
)
from phoenixguard.simulation.synthetic_scenarios import generate_synthetic_market_scenario


def test_event_candle_backtester_records_path_metrics() -> None:
    scenario = generate_synthetic_market_scenario("trend_up", seed=3, frame_count=18)
    candles = [frame["ohlc"] for frame in scenario["frames"]]

    result = run_event_candle_backtest(
        candles,
        CandleBacktestConfig(angle_threshold=0.005, dominance_margin=0.05, entry_quality_score=0.1, expiry_candles=3, name="loose"),
    )
    payload = result.as_dict()

    assert payload["candles_processed"] == 18
    assert payload["trade_count"] > 0
    assert payload["trades"][0]["outcome_metrics"]["sample_count"] <= 3
    assert "MFE/MAE ratio" in payload


def test_parameter_sweep_ranks_runs() -> None:
    scenario = generate_synthetic_market_scenario("trend_up", seed=4, frame_count=18)
    candles = [frame["ohlc"] for frame in scenario["frames"]]

    sweep = run_parameter_sweep(
        candles,
        {"dominance_margin": [0.01, 0.8], "entry_quality_score": [0.1]},
        base_config={"angle_threshold": 0.005, "expiry_candles": 2},
    )
    payload = sweep.as_dict()

    assert payload["run_count"] == 2
    assert payload["best"] is not None
    assert payload["results"][0]["trade_count"] >= payload["results"][1]["trade_count"]
