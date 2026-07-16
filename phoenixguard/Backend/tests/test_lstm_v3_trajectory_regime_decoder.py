from __future__ import annotations

import importlib
import json
import math
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import pytest
import torch

from phoenixguard.decision import lstm_candle_sequence_contributor_v3 as contributor


_load_artifact_bundle = cast(
    Callable[[Path, Path, Path], dict[str, Any]],
    getattr(contributor, "_load_artifact_bundle"),
)
_model_forecast = cast(
    Callable[..., dict[str, Any]],
    getattr(contributor, "_model_forecast"),
)


def _approx(expected: object, **kwargs: float) -> object:
    return cast(Callable[..., object], getattr(pytest, "approx"))(expected, **kwargs)


@lru_cache(maxsize=1)
def _trainer() -> Any:
    tools = Path(__file__).resolve().parents[1] / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return importlib.import_module("train_lstm_candle_sequence_v3")


def _features(count: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "body_norm": 0.18,
            "upper_wick_norm": 0.04,
            "lower_wick_norm": 0.05,
            "direction": "BUY" if index % 2 == 0 else "SELL",
            "direction_value": 1.0 if index % 2 == 0 else -1.0,
            "range_norm": 0.04,
            "relative_price_location": 0.5,
            "relative_price_delta_scaled": 0.0,
            "range_vs_recent": 1.0,
            "body_vs_recent": 1.0,
            "momentum_5": 0.0,
            "direction_run_norm": 0.0,
            "parse_confidence": 0.9,
            "phase_value": 0.5,
        }
        for index in range(count)
    ]


def _configured_regime_model(horizon_steps: int = 3) -> Any:
    model = contributor.create_lstm_candle_sequence_model(
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=horizon_steps,
        trajectory_modes=3,
    )
    with torch.no_grad():
        model.trajectory_mode_head.weight.zero_()
        model.trajectory_mode_head.bias.copy_(torch.tensor([0.0, 4.0, 0.0]))
        model.trajectory_mode_mean_head.weight.zero_()
        model.trajectory_mode_mean_head.bias.copy_(
            torch.atanh(torch.tensor([0.4, -0.4, 0.0]))
        )
        model.trajectory_mode_scale_head.weight.zero_()
        model.trajectory_mode_scale_head.bias.zero_()
    model.eval()
    return model


def test_factory_keeps_old_direct_state_dict_exactly_backward_compatible() -> None:
    old_model = contributor.create_lstm_candle_sequence_model(
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=3,
    )
    restored_model = contributor.create_lstm_candle_sequence_model(
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=3,
        trajectory_modes=0,
    )

    restored_model.load_state_dict(old_model.state_dict(), strict=True)
    assert not any("trajectory_mode" in key for key in old_model.state_dict())
    outputs = old_model(
        torch.zeros((2, 5, len(contributor.FEATURE_SCHEMA))),
        lengths=torch.tensor([5, 5]),
        horizon_steps=3,
    )
    assert "trajectory_mode_logits" not in outputs
    assert "trajectory_mode_mean" not in outputs


def test_artifact_without_trajectory_flag_still_loads_unchanged(
    tmp_path: Path,
) -> None:
    model = contributor.create_lstm_candle_sequence_model(
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=3,
    )
    model_path = tmp_path / "old-direct.pt"
    config_path = tmp_path / "old-direct.json"
    metrics_path = tmp_path / "old-direct-metrics.json"
    target_schema = "TEST_DIRECT_TARGET_V3"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "training_target_schema_version": target_schema,
        },
        model_path,
    )
    config_path.write_text(
        json.dumps(
            {
                "model_version": contributor.LSTM_CANDLE_SEQUENCE_VERSION,
                "architecture": contributor.DIRECT_RAW_CV_ARCHITECTURE,
                "training_target_schema_version": target_schema,
                "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
                "input_dim": len(contributor.FEATURE_SCHEMA),
                "hidden_dim": 16,
                "num_layers": 1,
                "dropout": 0.0,
                "horizon_steps": 3,
            }
        ),
        encoding="utf-8",
    )
    metrics_path.write_text(
        json.dumps(
            {
                "training_target_schema_version": target_schema,
                "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
                "production_ready": False,
            }
        ),
        encoding="utf-8",
    )

    bundle = _load_artifact_bundle(
        model_path,
        config_path,
        metrics_path,
    )

    assert bundle["model_loaded"] is True
    assert bundle["legacy_restored"] is False
    assert (
        bundle["error"]
        == "V3 artifact loaded but held-out evaluation did not pass the production gate."
    )


def test_target_endpoint_regime_selects_the_matching_path_branch() -> None:
    trainer = _trainer()
    path_targets = torch.tensor(
        [[0.05, 0.20], [-0.05, -0.20], [0.01, -0.01]],
        dtype=torch.float32,
    )

    mode_targets = trainer._trajectory_mode_targets(path_targets)

    assert mode_targets.tolist() == [
        trainer.SIDE_TO_INDEX["BUY"],
        trainer.SIDE_TO_INDEX["SELL"],
        trainer.PATH_DIRECTION_LABELS.index("HOLD"),
    ]


def test_training_path_loss_routes_each_sample_through_its_target_regime() -> None:
    trainer = _trainer()
    batch, horizon = 2, 2
    targets = torch.zeros(
        (batch, horizon, len(trainer.FEATURE_SCHEMA)),
        dtype=torch.float32,
    )
    targets[0, :, trainer.PATH_TARGET_FEATURE_INDEX] = torch.tensor([0.1, 0.2])
    targets[1, :, trainer.PATH_TARGET_FEATURE_INDEX] = torch.tensor([-0.1, -0.2])
    good_modes = torch.tensor(
        [
            [[0.1, -0.1, 0.0], [0.2, -0.2, 0.0]],
            [[0.1, -0.1, 0.0], [0.2, -0.2, 0.0]],
        ],
        dtype=torch.float32,
    )
    bad_modes = good_modes.clone()
    bad_modes[0, :, trainer.SIDE_TO_INDEX["BUY"]] = 0.0
    bad_modes[1, :, trainer.SIDE_TO_INDEX["SELL"]] = 0.0

    def loss_for(mode_means: torch.Tensor) -> torch.Tensor:
        return trainer._loss(
            {
                "direction_logits": torch.zeros((batch, horizon, 2)),
                "decision_logits": torch.zeros((batch, horizon, 2)),
                "feature_mean": torch.zeros(
                    (batch, horizon, len(trainer.PREDICTION_SCHEMA))
                ),
                "feature_scale": torch.full(
                    (batch, horizon, len(trainer.PREDICTION_SCHEMA)),
                    0.2,
                ),
                "play_logits": torch.zeros((batch, len(trainer.PLAY_LABELS))),
                "trajectory_mode_logits": torch.zeros(
                    (batch, len(trainer.PATH_DIRECTION_LABELS))
                ),
                "trajectory_mode_mean": mode_means,
                "trajectory_mode_scale": torch.full_like(mode_means, 0.2),
            },
            targets,
            torch.zeros((batch, horizon), dtype=torch.long),
            torch.zeros(batch, dtype=torch.long),
            class_weights=torch.ones(2),
            play_class_weights=torch.ones(len(trainer.PLAY_LABELS)),
            target_quality=torch.ones((batch, horizon)),
            trajectory_mode_class_weights=torch.ones(
                len(trainer.PATH_DIRECTION_LABELS)
            ),
        )

    assert float(loss_for(good_modes)) < float(loss_for(bad_modes))


def test_multimodal_direction_hinges_do_not_exaggerate_correct_paths() -> None:
    trainer = _trainer()
    margin = trainer.PATH_DIRECTION_HOLD_THRESHOLD_SCALED
    probe = torch.tensor(
        [margin, margin + 0.01, -margin, -margin - 0.01, 0.0],
        requires_grad=True,
    )
    probe_targets = torch.tensor([0.2, 0.2, -0.2, -0.2, 0.0])

    hinge, active = trainer._dead_zone_direction_hinge(probe, probe_targets)

    assert active.tolist() == [True, True, True, True, False]
    assert hinge.tolist() == _approx([0.0, 0.0, 0.0, 0.0, 0.0])

    batch, horizon = 1, 3
    target_path = torch.tensor([0.03, 0.06, 0.09])
    targets = torch.zeros(
        (batch, horizon, len(trainer.FEATURE_SCHEMA)),
        dtype=torch.float32,
    )
    targets[0, :, trainer.PATH_TARGET_FEATURE_INDEX] = target_path
    mode_means = torch.zeros(
        (batch, horizon, len(trainer.PATH_DIRECTION_LABELS)),
        dtype=torch.float32,
    )
    buy_index = trainer.SIDE_TO_INDEX["BUY"]
    sell_index = trainer.SIDE_TO_INDEX["SELL"]
    hold_index = trainer.PATH_DIRECTION_LABELS.index("HOLD")
    mode_means[0, :, buy_index] = target_path + 0.001
    mode_means[0, :, sell_index] = -target_path
    mode_means[0, :, hold_index] = 0.0
    mode_means.requires_grad_()
    loss = trainer._loss(
        {
            "direction_logits": torch.tensor([[[8.0, -8.0]] * horizon]),
            "decision_logits": torch.tensor([[[8.0, -8.0]] * horizon]),
            "feature_mean": torch.zeros(
                (batch, horizon, len(trainer.PREDICTION_SCHEMA))
            ),
            "feature_scale": torch.full(
                (batch, horizon, len(trainer.PREDICTION_SCHEMA)), 0.2
            ),
            "play_logits": torch.tensor([[8.0, -8.0, -8.0]]),
            "trajectory_mode_logits": torch.tensor([[8.0, -8.0, -8.0]]),
            "trajectory_mode_mean": mode_means,
            "trajectory_mode_scale": torch.full_like(mode_means, 0.2),
        },
        targets,
        torch.zeros((batch, horizon), dtype=torch.long),
        torch.zeros(batch, dtype=torch.long),
        class_weights=torch.ones(2),
        play_class_weights=torch.ones(len(trainer.PLAY_LABELS)),
        target_quality=torch.ones((batch, horizon)),
        trajectory_mode_class_weights=torch.ones(len(trainer.PATH_DIRECTION_LABELS)),
    )
    loss.backward()

    assert mode_means.grad is not None
    # The near-exact BUY branch sits slightly above its target. Its gradient
    # must pull it back down, never push it farther toward the tanh boundary.
    assert torch.all(mode_means.grad[0, :, buy_index] > 0.0)
    # Alternative branches already satisfy their labelled dead zones and
    # therefore receive no magnitude-forcing gradient.
    assert torch.allclose(
        mode_means.grad[0, :, sell_index],
        torch.zeros(horizon),
        atol=1e-8,
    )
    assert torch.allclose(
        mode_means.grad[0, :, hold_index],
        torch.zeros(horizon),
        atol=1e-8,
    )


def test_runtime_map_path_and_all_learned_scenarios_are_exported() -> None:
    model = _configured_regime_model()

    forecast = _model_forecast(
        model,
        _features(),
        sequence_length=8,
        horizon_steps=3,
        path_target_semantics="DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
    )

    assert forecast["trajectory_decoder_status"] == "AVAILABLE"
    assert forecast["trajectory_mode"] == "SELL"
    assert forecast["trajectory_mode_probability_calibrated"] is False
    probabilities = forecast["trajectory_mode_probabilities"]
    assert set(probabilities) == {"BUY", "SELL", "HOLD"}
    assert math.isclose(sum(probabilities.values()), 1.0, abs_tol=2e-6)
    scenarios = forecast["trajectory_scenarios"]
    assert [row["side"] for row in scenarios] == ["BUY", "SELL", "HOLD"]
    assert sum(bool(row["selected"]) for row in scenarios) == 1
    endpoints = {
        row["side"]: row["forecast_path"][-1]["expected_close_norm"]
        for row in scenarios
    }
    assert endpoints["BUY"] > 0.5
    assert endpoints["SELL"] < 0.5
    assert endpoints["HOLD"] == _approx(0.5)
    assert forecast["forecast_path"][-1]["expected_close_norm"] == _approx(
        endpoints["SELL"]
    )


def test_existing_unconditional_model_never_fabricates_scenarios() -> None:
    model = contributor.create_lstm_candle_sequence_model(
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=3,
    )
    model.eval()

    forecast = _model_forecast(
        model,
        _features(),
        sequence_length=8,
        horizon_steps=3,
        path_target_semantics="DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
    )

    assert forecast["trajectory_decoder_status"] == "UNAVAILABLE"
    assert forecast["trajectory_mode"] is None
    assert forecast["trajectory_mode_probabilities"] == {}
    assert forecast["trajectory_scenarios"] == []


def test_evaluation_reports_target_branch_and_map_end_to_end_metrics() -> None:
    trainer = _trainer()
    model = _configured_regime_model()
    paths = ([0.1, 0.2, 0.3], [-0.1, -0.2, -0.3], [0.0, 0.01, 0.0])
    rows: list[dict[str, Any]] = []
    for index, path in enumerate(paths):
        target_rows = [
            [0.0 for _name in trainer.FEATURE_SCHEMA] for _step in range(len(path))
        ]
        for step, displacement in enumerate(path):
            target_rows[step][trainer.PATH_TARGET_FEATURE_INDEX] = displacement
        rows.append(
            {
                "sequence": [
                    [0.0 for _name in trainer.FEATURE_SCHEMA] for _step in range(8)
                ],
                "length": 8,
                "targets": target_rows,
                "directions": [0, 1, 0],
                "target_quality": [1.0, 1.0, 1.0],
                "play": 0,
                "source": f"missing-source-{index}.png",
                "independent_group": f"test:{index}",
            }
        )

    metrics = trainer.evaluate(model, rows, batch_size=3)

    assert metrics["trajectory_mode_accuracy"] is not None
    assert metrics["trajectory_mode_balanced_accuracy"] is not None
    assert metrics["trajectory_target_mode_path_delta_mae"] is not None
    assert metrics["trajectory_map_path_delta_mae"] == metrics["path_delta_mae"]
    support = metrics["trajectory_mode_predicted_support"]
    assert sum(support.values()) == len(rows)
    assert len(metrics["trajectory_mode_confusion_matrix"]) == 3


def test_contribution_exposes_scenarios_at_the_top_level(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = _configured_regime_model()
    config = {
        "model_version": contributor.LSTM_CANDLE_SEQUENCE_VERSION,
        "architecture": contributor.DIRECT_RAW_CV_ARCHITECTURE,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "trajectory_modes": 3,
        "sequence_length": 8,
        "horizon_steps": 3,
        "minimum_history": 6,
        "chart_context_size": [32, 64],
    }
    artifact = {
        "config": config,
        "metrics": {"test_balanced_accuracy": 0.5},
        "model": model,
        "model_loaded": True,
        "legacy_restored": False,
        "ready": False,
        "retrieval_bank": None,
        "risk_control": {},
        "risk_error": "",
        "error": "diagnostic only",
    }
    model_path = tmp_path / "regime.pt"
    config_path = tmp_path / "regime.json"
    metrics_path = tmp_path / "regime-metrics.json"

    def fake_select(*_args: Any) -> tuple[Any, Path, Path, Path, dict[str, Any]]:
        return (
            artifact,
            model_path,
            config_path,
            metrics_path,
            {"source": "TEST_REGIME_V3"},
        )

    monkeypatch.setattr(
        contributor,
        "_select_runtime_artifact_bundle",
        fake_select,
    )
    candles = [
        {
            "bbox": [8.0 + 10.0 * index, 20.0, 12.0 + 10.0 * index, 55.0],
            "center_x_px": 10.0 + 10.0 * index,
            "body_height_pct": 0.18,
            "upper_wick_pct": 0.04,
            "lower_wick_pct": 0.05,
            "direction": "BUY" if index % 2 == 0 else "SELL",
            "price_proxy": 0.5,
            "confidence": 0.9,
        }
        for index in range(8)
    ]

    result = contributor.build_lstm_candle_sequence_contribution(
        candles=candles,
        image_size=(100, 100),
        model_path=model_path,
        config_path=config_path,
        metrics_path=metrics_path,
    )

    assert result["trajectory_modes"] == 3
    assert result["trajectory_decoder_status"] == "AVAILABLE"
    assert result["trajectory_mode"] == "SELL"
    assert len(result["trajectory_scenarios"]) == 3
    assert result["forecast_path"][-1]["expected_close_norm"] < 0.5
