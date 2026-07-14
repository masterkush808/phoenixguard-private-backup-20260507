from __future__ import annotations
from pathlib import Path
from typing import Callable, cast

from phoenixguard.decision.high_frequency_candle_predictor import build_high_frequency_candle_forecast
from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    FEATURE_SCHEMA,
    build_lstm_candle_sequence_contribution,
    causal_chart_context_tensor,
    create_lstm_candle_sequence_model,
    sequence_features_to_matrix,
)


def _set_torch_seed(torch_module: object, seed: int) -> None:
    manual_seed = cast(Callable[[int], object], getattr(torch_module, "manual_seed"))
    manual_seed(seed)


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

    assert contribution["schema_version"] == "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3"
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


def test_v3_lstm_predicts_a_probabilistic_multi_event_path() -> None:
    import torch

    model = create_lstm_candle_sequence_model(
        input_dim=len(FEATURE_SCHEMA),
        hidden_dim=16,
        num_layers=2,
        dropout=0.1,
        horizon_steps=6,
    )
    model.eval()
    with torch.inference_mode():
        outputs = model(torch.zeros((2, 24, len(FEATURE_SCHEMA)), dtype=torch.float32), horizon_steps=6)

    assert tuple(outputs["direction_logits"].shape) == (2, 6, 2)
    assert tuple(outputs["decision_logits"].shape) == (2, 6, 2)
    assert tuple(outputs["feature_mean"].shape) == (2, 6, 5)
    assert tuple(outputs["feature_scale"].shape) == (2, 6, 5)
    assert tuple(outputs["play_logits"].shape) == (2, 3)
    assert tuple(outputs["context_embedding"].shape) == (2, 16)
    assert bool(torch.all(outputs["feature_scale"] > 0.0))


def test_v3_feature_matrix_right_pads_after_real_observations() -> None:
    features = [
        {"direction_value": 1.0, "relative_price_location": 0.42},
        {"direction_value": -1.0, "relative_price_location": 0.39},
    ]

    matrix = sequence_features_to_matrix(features, sequence_length=5)

    assert matrix[0][FEATURE_SCHEMA.index("direction_value")] == 1.0
    assert matrix[1][FEATURE_SCHEMA.index("direction_value")] == -1.0
    assert matrix[2:] == [[0.0] * len(FEATURE_SCHEMA) for _ in range(3)]


def test_v3_packed_encoder_ignores_right_padding_and_teacher_forcing() -> None:
    import torch

    _set_torch_seed(torch, 17)
    model = create_lstm_candle_sequence_model(
        input_dim=len(FEATURE_SCHEMA),
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=4,
    )
    model.eval()
    observed = torch.randn((1, 3, len(FEATURE_SCHEMA)), dtype=torch.float32)
    padded = torch.cat((observed, torch.zeros((1, 5, len(FEATURE_SCHEMA)))), dim=1)
    mask = torch.tensor([[True, True, True, False, False, False, False, False]])
    targets = torch.randn((1, 4, len(FEATURE_SCHEMA)), dtype=torch.float32)

    with torch.inference_mode():
        compact = model(observed, lengths=torch.tensor([3]), horizon_steps=4)
        explicit = model(padded, mask=mask, horizon_steps=4)
        legacy_training_call = model(
            padded,
            targets=targets,
            teacher_forcing_ratio=1.0,
            lengths=torch.tensor([3]),
            horizon_steps=4,
        )

    assert not hasattr(model, "decoder_cell")
    assert torch.allclose(compact["direction_logits"], explicit["direction_logits"], atol=1e-6)
    assert torch.allclose(explicit["direction_logits"], legacy_training_call["direction_logits"], atol=1e-6)
    assert torch.allclose(explicit["feature_mean"], legacy_training_call["feature_mean"], atol=1e-6)


def test_v3_causal_chart_context_masks_future_pixels_and_fuses_optional_pixels() -> None:
    import torch

    source = torch.ones((20, 40, 3), dtype=torch.float32)
    pixels = causal_chart_context_tensor(source, cut_x=0.5, output_size=(16, 32))

    assert tuple(pixels.shape) == (3, 16, 32)
    assert bool(torch.all(pixels[:, :, 16:] == 0.0))
    assert bool(torch.all(pixels[:, :, :15] > 0.0))

    _set_torch_seed(torch, 23)
    model = create_lstm_candle_sequence_model(
        input_dim=len(FEATURE_SCHEMA),
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=3,
    )
    model.eval()
    sequence = torch.randn((1, 5, len(FEATURE_SCHEMA)), dtype=torch.float32)
    with torch.inference_mode():
        without_pixels = model(sequence, lengths=[5], horizon_steps=3)
        with_pixels = model(sequence, lengths=[5], chart_context=pixels.unsqueeze(0), horizon_steps=3)

    assert tuple(with_pixels["context_embedding"].shape) == (1, 16)
    context_embedding = with_pixels["context_embedding"]
    context_norm = torch.sqrt(torch.sum(context_embedding * context_embedding, dim=-1))
    assert torch.allclose(context_norm, torch.ones(1), atol=1e-5)
    assert not torch.allclose(without_pixels["direction_logits"], with_pixels["direction_logits"])
