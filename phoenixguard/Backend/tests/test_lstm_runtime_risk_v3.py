from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    DIRECT_RAW_CV_ARCHITECTURE,
    FEATURE_SCHEMA,
    LEGACY_MULTISCALE_ARCHITECTURE,
    build_lstm_candle_sequence_contribution,
    candle_sequence_features,
    create_legacy_lstm_candle_sequence_model,
    create_lstm_candle_sequence_model,
    legacy_sequence_features_to_matrix,
    sequence_features_to_matrix,
)
from phoenixguard.decision.retrieval_forecast_v3 import build_retrieval_bank_v3


def _candles() -> list[dict[str, Any]]:
    return [
        {
            "track_id": index,
            "bbox": [index * 9, 460 - index * 3, index * 9 + 5, 490 - index * 3],
            "direction": "BUY" if index % 3 else "SELL",
            "price_proxy": 0.40 + index * 0.012,
            "body_height_pct": 0.03,
            "upper_wick_pct": 0.01,
            "lower_wick_pct": 0.01,
            "parse_confidence": 0.95,
        }
        for index in range(1, 9)
    ]


def _write_artifact(
    root: Path,
    *,
    production_ready: bool,
    include_risk: bool,
) -> tuple[Path, Path, Path]:
    torch = pytest.importorskip("torch")
    horizon = 3
    sequence_length = 12
    model = create_lstm_candle_sequence_model(
        input_dim=len(FEATURE_SCHEMA),
        hidden_dim=16,
        num_layers=1,
        dropout=0.0,
        horizon_steps=horizon,
    )
    with torch.no_grad():
        model.direction_head.weight.zero_()
        model.direction_head.bias.copy_(torch.tensor([1.0, -1.0]))
        model.decision_head.weight.zero_()
        model.decision_head.bias.copy_(torch.tensor([1.0, -1.0]))
        model.feature_mean_head.weight.zero_()
        model.feature_mean_head.bias.copy_(torch.tensor([0.0, 0.0, 0.0, 0.0, 1.0]))
    model.eval()

    features = candle_sequence_features(_candles(), image_size=(640, 520))
    matrix = sequence_features_to_matrix(features, sequence_length=sequence_length)
    with torch.inference_mode():
        outputs = model(
            torch.tensor([matrix], dtype=torch.float32),
            lengths=torch.tensor([len(features)]),
            horizon_steps=horizon,
        )
    embedding = outputs["context_embedding"].squeeze(0).tolist()
    payload: dict[str, Any] = {"state_dict": model.state_dict()}
    if include_risk:
        payload["retrieval_bank"] = build_retrieval_bank_v3(
            [embedding],
            ["train-source-1"],
            [["SELL"] * horizon],
            [[-0.05] * horizon],
            split_labels=["train"],
            entry_ids=["train-window-1"],
        )
        payload["risk_control"] = {
            "temperature": 0.5,
            "retrieval": {"top_k": 1, "alpha": 0.45},
            "thresholds": {"BUY": 0.8, "SELL": 0.8},
            "validation_selection": {"target_precision": 0.85},
        }

    model_path = root / "model.pt"
    config_path = root / "config.json"
    metrics_path = root / "metrics.json"
    torch.save(payload, model_path)
    config = {
        "model_version": "lstm_candle_sequence_v3",
        "architecture": DIRECT_RAW_CV_ARCHITECTURE,
        "input_dim": len(FEATURE_SCHEMA),
        "hidden_dim": 16,
        "num_layers": 1,
        "dropout": 0.0,
        "horizon_steps": horizon,
        "sequence_length": sequence_length,
        "production_ready": production_ready,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    metrics_path.write_text(
        json.dumps({"production_ready": production_ready, "test_balanced_accuracy": 0.61}),
        encoding="utf-8",
    )
    return model_path, config_path, metrics_path


def _contribution(paths: tuple[Path, Path, Path]) -> dict[str, Any]:
    model_path, config_path, metrics_path = paths
    return build_lstm_candle_sequence_contribution(
        candles=_candles(),
        image_size=(640, 520),
        model_path=model_path,
        config_path=config_path,
        metrics_path=metrics_path,
    )


def test_runtime_applies_temperature_retrieval_and_selective_thresholds(tmp_path: Path) -> None:
    contribution = _contribution(
        _write_artifact(tmp_path, production_ready=True, include_risk=True)
    )

    first = contribution["forecast_path"][0]
    assert contribution["artifact_loaded"] is True
    assert contribution["production_authorized"] is True
    assert contribution["risk_control_applied"] is True
    assert contribution["retrieval"]["status"] == "ok"
    assert contribution["retrieval"]["neighbor_count"] == 1
    assert first["raw_model_buy_probability"] < first["calibrated_model_buy_probability"]
    assert first["buy_probability"] < first["calibrated_model_buy_probability"]
    assert first["retrieval_effective_alpha"] > 0.0
    assert first["selective_authorized"] is True
    assert contribution["selective_status"] == "AUTHORIZED"
    assert contribution["selective_prediction"]["accuracy_guarantee"] is False
    assert contribution["contribution"] > 0.0


def test_non_production_challenger_emits_diagnostic_path_but_never_an_edge(tmp_path: Path) -> None:
    contribution = _contribution(
        _write_artifact(tmp_path, production_ready=False, include_risk=True)
    )

    assert contribution["artifact_loaded"] is True
    assert contribution["production_authorized"] is False
    assert contribution["fresh"] is True
    assert contribution["forecast_available"] is True
    assert contribution["selective_status"] == "NO_EDGE"
    assert contribution["selective_side"] == "NO_EDGE"
    assert contribution["contribution"] == 0.0
    assert all(row["selective_status"] == "NO_EDGE" for row in contribution["forecast_path"])
    assert "diagnostic" in contribution["reason"].lower()


def test_legacy_v3_artifact_without_risk_metadata_fails_closed(tmp_path: Path) -> None:
    contribution = _contribution(
        _write_artifact(tmp_path, production_ready=True, include_risk=False)
    )

    assert contribution["artifact_loaded"] is True
    assert contribution["forecast_available"] is True
    assert contribution["risk_control_applied"] is False
    assert contribution["risk_control_status"] == "CALIBRATION_UNAVAILABLE"
    assert contribution["selective_status"] == "NO_EDGE"
    assert contribution["contribution"] == 0.0


def test_restored_legacy_v3_padding_and_state_dict_contract() -> None:
    torch = pytest.importorskip("torch")
    project_root = Path(__file__).resolve().parents[2]
    export_root = project_root / "models" / "exports" / "lstm_candle_sequence_v3"
    model_path = export_root / "lstm_candle_sequence_v3.pt"
    config = json.loads((export_root / "lstm_candle_sequence_v3_config.json").read_text(encoding="utf-8"))
    payload = torch.load(model_path, map_location="cpu", weights_only=False)
    state_dict = payload["state_dict"]

    model = create_legacy_lstm_candle_sequence_model(
        input_dim=int(config["input_dim"]),
        hidden_dim=int(config["hidden_dim"]),
        num_layers=int(config["num_layers"]),
        dropout=float(config["dropout"]),
        horizon_steps=int(config["horizon_steps"]),
    )
    model.load_state_dict(state_dict, strict=True)
    model.eval()

    assert config["architecture"] == LEGACY_MULTISCALE_ARCHITECTURE
    assert len(state_dict) == 37
    assert hasattr(model, "history_attention")
    assert hasattr(model, "decoder_cell")

    features = candle_sequence_features(_candles(), image_size=(640, 520))
    legacy_matrix = legacy_sequence_features_to_matrix(features, sequence_length=12)
    direct_matrix = sequence_features_to_matrix(features, sequence_length=12)
    assert legacy_matrix[:4] == [[0.0] * len(FEATURE_SCHEMA)] * 4
    assert legacy_matrix[-1] != [0.0] * len(FEATURE_SCHEMA)
    assert direct_matrix[:8] != [[0.0] * len(FEATURE_SCHEMA)] * 8
    assert direct_matrix[-4:] == [[0.0] * len(FEATURE_SCHEMA)] * 4

    with torch.inference_mode():
        outputs = model(
            torch.tensor([legacy_matrix], dtype=torch.float32),
            horizon_steps=int(config["horizon_steps"]),
        )
    assert tuple(outputs["direction_logits"].shape) == (1, 12, 2)
    assert tuple(outputs["feature_mean"].shape) == (1, 12, 5)
    assert tuple(outputs["feature_scale"].shape) == (1, 12, 5)
    assert tuple(outputs["play_logits"].shape) == (1, 3)
    assert all(torch.isfinite(value).all() for value in outputs.values())


def test_exact_export_restores_legacy_v3_progression_and_recorded_metrics() -> None:
    pytest.importorskip("torch")
    project_root = Path(__file__).resolve().parents[2]
    export_root = project_root / "models" / "exports" / "lstm_candle_sequence_v3"
    contribution = build_lstm_candle_sequence_contribution(
        candles=_candles(),
        image_size=(640, 520),
        model_path=export_root / "lstm_candle_sequence_v3.pt",
        config_path=export_root / "lstm_candle_sequence_v3_config.json",
        metrics_path=export_root / "lstm_candle_sequence_v3_metrics.json",
    )

    assert contribution["artifact_loaded"] is True
    assert contribution["architecture"] == LEGACY_MULTISCALE_ARCHITECTURE
    assert contribution["legacy_restored"] is True
    assert contribution["production_authorized"] is True
    assert contribution["forecast_available"] is True
    assert len(contribution["forecast_path"]) == 12
    assert contribution["chart_context_used"] is False
    assert contribution["selective_authorized"] is False
    assert contribution["selective_status"] == "NO_EDGE"
    assert contribution["selective_side"] == "NO_EDGE"
    assert contribution["selective_prediction"]["policy"] == "LEGACY_EXPORTED_V3_DIAGNOSTIC_ONLY"
    assert contribution["selective_prediction"]["accuracy_guarantee"] is False
    assert contribution["risk_control_status"] == "CALIBRATION_UNAVAILABLE"
    assert contribution["contribution"] == 0.0
    assert contribution["forecast_path"][0]["selective_authorized"] is False
    assert "diagnostic" in contribution["reason"].lower()
    assert math.isclose(
        float(contribution["metrics"]["test_balanced_accuracy"]),
        0.7144,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert math.isclose(
        float(contribution["metrics"]["test_interval_90_coverage"]),
        0.8924,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert math.isclose(
        float(contribution["metrics"]["test_play_accuracy"]),
        0.8803,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
