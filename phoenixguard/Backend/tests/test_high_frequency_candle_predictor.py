from __future__ import annotations
from pathlib import Path

from phoenixguard.decision.high_frequency_candle_predictor import build_high_frequency_candle_forecast
from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import build_lstm_candle_sequence_contribution


def _candle(index: int, *, side: str, top: int, bottom: int) -> dict[str, object]:
    return {
        "track_id": index,
        "bbox": [index * 8, top, index * 8 + 5, bottom],
        "direction": side,
        "color": "green" if side == "BUY" else "magenta",
        "price_proxy": 1.0 - ((top + bottom) / 2.0 / 1000.0),
        "body_height_pct": (bottom - top) / 1000.0,
    }


def test_high_frequency_forecast_reports_two_candle_buy_structure() -> None:
    candles = [
        _candle(1, side="BUY", top=720, bottom=760),
        _candle(2, side="BUY", top=700, bottom=742),
        _candle(3, side="SELL", top=706, bottom=748),
        _candle(4, side="BUY", top=675, bottom=724),
        _candle(5, side="BUY", top=650, bottom=704),
        _candle(6, side="BUY", top=625, bottom=680),
        _candle(7, side="BUY", top=610, bottom=665),
    ]

    forecast = build_high_frequency_candle_forecast(
        candles=candles,
        image_size=(900, 1000),
        timeframe="M5",
        candidate_action="BUY",
        global_direction="BUY",
        local_direction="BUY",
        impulse_direction="BUY",
        decision_kernel={"p_next_buy": 0.69, "p_next_sell": 0.18, "p_next_hold": 0.13, "next_candle_bias": "buy"},
        candle_statistics={
            "sample_weight": 0.82,
            "recent_buy_ratio": 0.83,
            "recent_sell_ratio": 0.17,
            "momentum_consistency": 0.76,
            "direction_run": 4,
            "opposing_ratio": 0.17,
            "average_step": 0.018,
        },
        behavior={"continuation_score": 0.74, "reversal_score": 0.18, "consolidation_score": 0.12},
        setup="CONTINUATION BUY",
    )

    assert forecast["status"] == "READY"
    assert forecast["primary_pressure"] == "BUY"
    assert forecast["horizon_candles"] == 2
    assert len(forecast["candle_forecasts"]) == 2
    assert forecast["candle_forecasts"][0]["direction"] in {"BUY", "READING"}
    assert forecast["candle_forecasts"][1]["direction"] == "BUY"
    assert forecast["do_not_render_synthetic_candles"] is True
    assert forecast["two_candle_study"]["display_as"] == "TEXT_AND_BANDS_ONLY"
    assert forecast["two_candle_study"]["do_not_render_synthetic_candles"] is True
    for row in forecast["candle_forecasts"]:
        assert row["display_as"] == "TEXT_AND_BANDS_ONLY"
        assert row["do_not_render_synthetic_candles"] is True
        assert "expected_high_norm" not in row
        assert "expected_low_norm" not in row
        assert "expected_close_norm" not in row
    assert "no synthetic candles" in forecast["summary"]
    assert forecast["confidence"] > 0.45
    assert forecast["signals"]


def test_high_frequency_forecast_reports_warming_without_enough_candles() -> None:
    forecast = build_high_frequency_candle_forecast(
        candles=[_candle(1, side="SELL", top=300, bottom=350)],
        image_size=(900, 1000),
        impulse_direction="SELL",
    )

    assert forecast["status"] == "WARMING"
    assert forecast["primary_pressure"] == "SELL"
    assert forecast["candle_forecasts"] == []
    assert forecast["do_not_render_synthetic_candles"] is True
    assert forecast["two_candle_study"]["do_not_render_synthetic_candles"] is True
    assert "at least five visible candles" in forecast["summary"]


def test_lstm_candle_contributor_is_observed_only_and_non_blocking_without_artifact(tmp_path: Path) -> None:
    candles = [
        _candle(1, side="SELL", top=300, bottom=350),
        _candle(2, side="SELL", top=320, bottom=370),
        _candle(3, side="BUY", top=315, bottom=365),
    ]

    contribution = build_lstm_candle_sequence_contribution(
        candles=candles,
        image_size=(900, 1000),
        timeframe="M5",
        sequence_phase="PULLBACK_RELOAD_SELL",
        model_path=tmp_path / "missing.pt",
        config_path=tmp_path / "missing_config.json",
        metrics_path=tmp_path / "missing_metrics.json",
    )

    assert contribution["schema_version"] == "PG_LSTM_CANDLE_SEQUENCE_CONTRIBUTION_V3"
    assert contribution["fresh"] is False
    assert contribution["blocker"] is False
    assert contribution["contribution"] == 0.0
    assert contribution["sequence_length"] == len(candles)
    assert len(contribution["features"]) == len(candles)
    for row in contribution["features"]:
        assert "body_norm" in row
        assert "relative_price_location" in row
        assert "expected_high_norm" not in row
        assert "expected_low_norm" not in row
