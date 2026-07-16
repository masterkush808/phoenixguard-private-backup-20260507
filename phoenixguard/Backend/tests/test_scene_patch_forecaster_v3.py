from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, cast

import pytest
import torch


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "Backend" / "src"
TOOLS = ROOT / "Backend" / "tools"
for path in (SOURCE, TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import train_scene_forecaster_v3 as trainer  # noqa: E402
from phoenixguard.decision import scene_patch_forecaster_v3 as forecaster  # noqa: E402
from phoenixguard.decision.scene_forecast_features_v3 import (  # noqa: E402
    CANDLE_NUMERIC_SCHEMA,
    CONTEXT_NUMERIC_SCHEMA,
)
from phoenixguard.decision.scene_patch_forecaster_v3 import (  # noqa: E402
    ScenePatchForecasterConfig,
    ScenePatchForecasterV3,
    scene_forecast_loss,
)


_manual_seed = cast(Callable[[int], object], getattr(torch, "manual_seed"))
_turning_point_loss = cast(
    Callable[[torch.Tensor, torch.Tensor, torch.Tensor], torch.Tensor],
    getattr(forecaster, "_turning_point_loss"),
)


def _small_model(*, static_features: int = 7) -> ScenePatchForecasterV3:
    return ScenePatchForecasterV3(
        ScenePatchForecasterConfig(
            candle_features=len(CANDLE_NUMERIC_SCHEMA),
            static_features=static_features,
            horizon=12,
            patch_size=4,
            d_model=32,
            attention_heads=4,
            encoder_layers=1,
            feedforward_width=48,
            dropout=0.0,
        )
    )


def test_output_shapes_quantile_order_and_valid_ohlc() -> None:
    _manual_seed(4)
    model = _small_model()
    candles = torch.randn(2, 19, len(CANDLE_NUMERIC_SCHEMA))
    candle_mask = torch.ones(2, 19, dtype=torch.bool)
    candle_mask[0, :3] = False
    static_values = torch.randn(2, 7)
    static_missing = torch.zeros(2, 7, dtype=torch.bool)

    outputs = model(candles, candle_mask, static_values, static_missing)

    assert outputs["close_quantiles"].shape == (2, 12, 3)
    assert outputs["upper_spans"].shape == (2, 12, 3)
    assert outputs["lower_spans"].shape == (2, 12, 3)
    assert outputs["ohlc_quantiles"].shape == (2, 12, 3, 4)
    assert outputs["movement_logits"].shape == (2, 12, 3)
    assert outputs["scenario_trajectories"].shape == (2, 3, 12)
    assert outputs["scenario_probabilities"].shape == (2, 3)

    quantiles = outputs["close_quantiles"]
    assert torch.all(quantiles[..., 0] < quantiles[..., 1])
    assert torch.all(quantiles[..., 1] < quantiles[..., 2])
    assert torch.all(outputs["upper_spans"] > 0.0)
    assert torch.all(outputs["lower_spans"] > 0.0)

    ohlc = outputs["ohlc_quantiles"]
    predicted_open, high, low, close = ohlc.unbind(dim=-1)
    assert torch.all(high >= torch.maximum(predicted_open, close))
    assert torch.all(low <= torch.minimum(predicted_open, close))
    scenarios = outputs["scenario_trajectories"]
    assert torch.all(scenarios[:, 0] <= scenarios[:, 1])
    assert torch.all(scenarios[:, 1] <= scenarios[:, 2])
    assert torch.allclose(
        outputs["movement_probabilities"].sum(dim=-1), torch.ones(2, 12), atol=1.0e-6
    )


def test_every_horizon_receives_direct_path_gradient() -> None:
    _manual_seed(8)
    model = _small_model()
    candles = torch.randn(2, 20, len(CANDLE_NUMERIC_SCHEMA))
    outputs = model(candles)
    outputs["close_quantiles"].retain_grad()
    target = torch.tensor(
        [
            [0.2, 0.6, 0.1, -0.3, -0.7, -0.2, 0.4, 0.9, 0.5, 0.0, -0.4, 0.3],
            [-0.1, -0.5, -0.2, 0.2, 0.7, 0.4, -0.1, -0.6, -0.3, 0.3, 0.8, 0.2],
        ],
        dtype=torch.float32,
    )
    increments = torch.cat((target[:, :1], target[:, 1:] - target[:, :-1]), dim=1)
    movements = torch.where(
        increments < -0.02,
        torch.zeros_like(increments, dtype=torch.long),
        torch.where(
            increments > 0.02,
            torch.full_like(increments, 2, dtype=torch.long),
            torch.ones_like(increments, dtype=torch.long),
        ),
    )
    total, components = scene_forecast_loss(
        outputs,
        target,
        movements,
        target_upper_spans=torch.full_like(target, 0.25),
        target_lower_spans=torch.full_like(target, 0.20),
    )

    cast(Callable[[], object], getattr(total, "backward"))()

    assert torch.isfinite(total)
    assert components["quantile"].item() > 0.0
    gradient = outputs["close_quantiles"].grad
    assert gradient is not None
    assert torch.all(gradient.abs().sum(dim=(0, 2)) > 0.0)


def test_non_monotonic_target_retains_turning_supervision() -> None:
    target = torch.tensor(
        [[0.0, 1.0, 0.0, 1.0, 0.0, -1.0, 0.0, -1.0, 0.0, 1.0, 0.0, -1.0]]
    )
    mask = torch.ones_like(target, dtype=torch.bool)
    matching = target.clone().requires_grad_(True)
    flat = torch.zeros_like(target, requires_grad=True)

    matching_loss = _turning_point_loss(matching, target, mask)
    flat_loss = _turning_point_loss(flat, target, mask)
    cast(Callable[[], object], getattr(matching_loss, "backward"))()

    assert flat_loss.item() > matching_loss.item()
    assert matching.grad is not None
    assert matching.grad.abs().sum().item() > 0.0


def _raw_feature(index: int, *, delta: float | None = None) -> dict[str, object]:
    movement = float(index + 1) / 20.0 if delta is None else delta
    return {
        "index": index,
        "bbox": [float(index * 5), 10.0, float(index * 5 + 3), 30.0],
        "center_x_px": float(index * 5 + 1.5),
        "direction": "BUY" if movement >= 0.0 else "SELL",
        "direction_value": 1.0 if movement >= 0.0 else -1.0,
        "body_norm": 0.5,
        "upper_wick_norm": 0.25,
        "lower_wick_norm": 0.25,
        "range_norm": 0.04,
        "relative_price_location": 0.4 + index * 0.01,
        "parse_confidence": 1.0,
        "relative_price_delta_scaled": movement,
        "range_vs_recent": 1.0,
        "body_vs_recent": 0.5,
    }


def _context(as_of_index: int) -> dict[str, object]:
    values = [0.0] * len(CONTEXT_NUMERIC_SCHEMA)
    values[0] = float(as_of_index)
    return {
        "as_of_index": as_of_index,
        "numeric_schema": list(CONTEXT_NUMERIC_SCHEMA),
        "numeric_values": values,
    }


def test_group_separation_rejects_cross_split_perceptual_group() -> None:
    sources = [
        {
            "split": "train",
            "independent_group": "train:perceptual-group:17",
            "source": "train.png",
        },
        {
            "split": "test",
            "independent_group": "test:perceptual-group:17",
            "source": "test.png",
        },
    ]

    with pytest.raises(ValueError, match="leakage"):
        trainer.validate_group_separation(sources)


def test_window_uses_only_pre_cut_rows_and_latest_causal_suite_context() -> None:
    features = [_raw_feature(index) for index in range(8)]
    features[3]["relative_price_delta_scaled"] = 91.0
    features[4]["relative_price_delta_scaled"] = 92.0
    features[5]["relative_price_delta_scaled"] = 93.0
    source = {
        "split": "train",
        "independent_group": "train:perceptual-group:2",
        "source": "scene.png",
        "features": features,
        "scene_context_history": [_context(2), _context(4)],
        "future_suite_payload": {"should_never_be_loaded": 999_999.0},
    }
    windows = trainer.build_causal_windows(
        [source], sequence_length=4, horizon=3, minimum_history=3
    )
    window = next(row for row in windows if row["cut_point"] == 3)

    materialized = cast(dict[str, Any], trainer.materialize_window(window))
    static_values = cast(torch.Tensor, materialized["static_values"])
    candles = cast(torch.Tensor, materialized["candles"])
    candle_mask = cast(torch.Tensor, materialized["candle_mask"])
    target_close_path = cast(torch.Tensor, materialized["target_close_path"])

    assert window["input_indices"] == (0, 1, 2)
    assert window["target_indices"] == (3, 4, 5)
    assert max(window["input_indices"]) < min(window["target_indices"])
    assert window["suite_context_as_of_index"] == 2
    assert static_values[0].item() == 2.0
    close_delta_index = trainer.BASE_CANDLE_SCHEMA.index("close_delta")
    observed_deltas = candles[candle_mask, close_delta_index]
    assert observed_deltas.max().item() < 1.0
    assert target_close_path[0].item() == 91.0


def test_future_anchored_teacher_prediction_is_rejected() -> None:
    source = {
        "split": "train",
        "independent_group": "train:perceptual-group:3",
        "source": "teacher-scene.png",
        "features": [_raw_feature(index) for index in range(16)],
    }
    cut_point = 4
    window_id_builder = cast(
        Callable[[str, int, int], str],
        getattr(trainer, "_window_id"),
    )
    window_id = window_id_builder("teacher-scene.png", cut_point, 12)
    teacher = {
        window_id: {
            "window_id": window_id,
            "source": "teacher-scene.png",
            "cut_point": cut_point,
            "as_of_index": cut_point,
            "close_quantiles": [[-0.2, 0.0, 0.2] for _ in range(12)],
        }
    }

    with pytest.raises(ValueError, match="not anchored"):
        trainer.build_causal_windows(
            [source],
            sequence_length=4,
            horizon=12,
            minimum_history=4,
            teacher_rows=teacher,
        )
