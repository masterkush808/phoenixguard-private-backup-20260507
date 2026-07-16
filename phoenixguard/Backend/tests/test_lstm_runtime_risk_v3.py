from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

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


def _approx(expected: object, **kwargs: float) -> object:
    return cast(Callable[..., object], getattr(pytest, "approx"))(expected, **kwargs)


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
    direct_path: bool = False,
    interval_groups: int | None = None,
    interval_quantile: float = 1.0,
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
        model.feature_scale_head.weight.zero_()
        model.feature_scale_head.bias.zero_()
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
    target_schema = "PG_DIRECT_CUMULATIVE_CLOSE_DISPLACEMENT_TARGET_V3"
    payload: dict[str, Any] = {"state_dict": model.state_dict()}
    if direct_path:
        payload["training_target_schema_version"] = target_schema
    if include_risk:
        payload["retrieval_bank"] = build_retrieval_bank_v3(
            [embedding],
            ["train-source-1"],
            [["SELL"] * horizon],
            [[-0.05] * horizon],
            split_labels=["train"],
            entry_ids=["train-window-1"],
        )
        risk_control: dict[str, Any] = {
            "temperature": 0.5,
            "retrieval": {"top_k": 1, "alpha": 0.45},
            "thresholds": {"BUY": 0.8, "SELL": 0.8},
            "validation_selection": {"target_precision": 0.85},
        }
        if direct_path:
            risk_control["trajectory"] = {
                "probability_calibrated": True,
                "horizons": {
                    str(step): {
                        "probability_calibrated": True,
                        "temperature": 1.0,
                        "thresholds": {"BUY": 0.8, "SELL": 0.8},
                    }
                    for step in range(1, horizon + 1)
                },
            }
            if interval_groups is not None:
                risk_control["pathwise_conformal"] = {
                    "schema_version": "PG_PERCEPTUAL_GROUP_PATHWISE_CONFORMAL_V3",
                    "method": "PERCEPTUAL_GROUP_PATHWISE_CONFORMAL",
                    "quantile": interval_quantile,
                    "calibration_independent_groups": interval_groups,
                    "calibration_sources": interval_groups,
                    "coverage": 0.9,
                }
        payload["risk_control"] = risk_control

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
        "minimum_history": 8,
        "production_ready": production_ready,
    }
    if direct_path:
        config.update(
            {
                "training_target_schema_version": target_schema,
                "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
                "path_output_is_incremental": False,
                "direction_target_semantics": "CANDLE_BODY_COLOR_BUY_SELL",
            }
        )
    config_path.write_text(json.dumps(config), encoding="utf-8")
    metrics: dict[str, object] = {
        "production_ready": production_ready,
        "test_balanced_accuracy": 0.61,
    }
    if direct_path:
        metrics.update(
            {
                "training_target_schema_version": target_schema,
                "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
                "test_path_movement_balanced_accuracy": 0.61,
            }
        )
    metrics_path.write_text(
        json.dumps(metrics),
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


def test_runtime_applies_temperature_retrieval_and_selective_thresholds(
    tmp_path: Path,
) -> None:
    contribution = _contribution(
        _write_artifact(
            tmp_path,
            production_ready=True,
            include_risk=True,
            direct_path=True,
        )
    )

    first = contribution["forecast_path"][0]
    assert contribution["artifact_loaded"] is True
    assert contribution["production_authorized"] is True
    assert contribution["artifact_production_gate_passed"] is True
    assert contribution["risk_control_applied"] is True
    assert contribution["retrieval"]["status"] == "ok"
    assert contribution["retrieval"]["neighbor_count"] == 1
    assert (
        first["raw_model_buy_probability"] < first["calibrated_model_buy_probability"]
    )
    assert first["body_buy_probability"] < first["calibrated_model_buy_probability"]
    assert first["retrieval_effective_alpha"] > 0.0
    assert first["selective_authorized"] is True
    assert contribution["selective_status"] == "AUTHORIZED"
    assert contribution["selective_prediction"]["accuracy_guarantee"] is False
    assert contribution["contribution"] > 0.0


def test_non_production_challenger_emits_diagnostic_path_but_never_an_edge(
    tmp_path: Path,
) -> None:
    contribution = _contribution(
        _write_artifact(
            tmp_path,
            production_ready=False,
            include_risk=True,
            direct_path=True,
        )
    )

    assert contribution["artifact_loaded"] is True
    assert contribution["production_authorized"] is False
    assert contribution["fresh"] is True
    assert contribution["forecast_available"] is True
    assert contribution["forecast_suppressed"] is False
    assert contribution["forecast_quality_status"] == "LOW_CONFIDENCE"
    assert contribution["trade_authorization_status"] == "NO_EDGE"
    assert contribution["selective_status"] == "NO_EDGE"
    assert contribution["selective_side"] == "NO_EDGE"
    assert contribution["contribution"] == 0.0
    assert all(
        row["selective_status"] == "NO_EDGE" for row in contribution["forecast_path"]
    )
    assert "diagnostic" in contribution["reason"].lower()


def test_direct_path_with_imperfect_event_lattice_stays_visible_but_cannot_authorize(
    tmp_path: Path,
) -> None:
    paths = _write_artifact(
        tmp_path,
        production_ready=True,
        include_risk=True,
        direct_path=True,
    )
    candles = _candles()
    centers = [9, 18, 27, 90, 99, 162, 171, 180]
    for candle, center in zip(candles, centers):
        candle["bbox"] = [center - 2, candle["bbox"][1], center + 2, candle["bbox"][3]]
        candle["center_x_px"] = center
    model_path, config_path, metrics_path = paths

    contribution = build_lstm_candle_sequence_contribution(
        candles=candles,
        image_size=(640, 520),
        model_path=model_path,
        config_path=config_path,
        metrics_path=metrics_path,
    )

    assert contribution["artifact_production_gate_passed"] is True
    assert contribution["production_authorized"] is False
    assert contribution["forecast_available"] is True
    assert contribution["forecast_suppressed"] is False
    assert len(contribution["forecast_path"]) == 3
    assert contribution["forecast_quality_status"] == "LOW_CONFIDENCE"
    assert contribution["trade_authorization_status"] == "NO_EDGE"
    assert contribution["selective_status"] == "NO_EDGE"
    assert (
        "missing_or_duplicate_candle_slots" in contribution["forecast_quality_warnings"]
    )
    assert all(not row["selective_authorized"] for row in contribution["forecast_path"])


def test_legacy_v3_artifact_without_risk_metadata_fails_closed(tmp_path: Path) -> None:
    contribution = _contribution(
        _write_artifact(tmp_path, production_ready=True, include_risk=False)
    )

    assert contribution["artifact_loaded"] is True
    assert contribution["forecast_available"] is False
    assert contribution["forecast_suppressed"] is True
    assert contribution["risk_control_applied"] is False
    assert contribution["risk_control_status"] == "CALIBRATION_UNAVAILABLE"
    assert contribution["selective_status"] == "NO_EDGE"
    assert contribution["contribution"] == 0.0


def test_direct_path_outputs_are_anchor_relative_not_recursively_accumulated(
    tmp_path: Path,
) -> None:
    contribution = _contribution(
        _write_artifact(
            tmp_path,
            production_ready=False,
            include_risk=True,
            direct_path=True,
        )
    )

    path = contribution["forecast_path"]
    closes = [float(row["expected_close_norm"]) for row in path]
    cumulative = [float(row["expected_cumulative_delta_norm"]) for row in path]
    assert contribution["artifact_loaded"] is True
    assert (
        contribution["path_target_semantics"] == "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR"
    )
    assert len(path) == 3
    assert closes[0] > float(path[0]["expected_open_norm"])
    assert closes == _approx([closes[0]] * 3, abs=1e-6)
    assert cumulative == _approx([cumulative[0]] * 3, abs=1e-6)
    previous_close = float(contribution["features"][-1]["relative_price_location"])
    for row in path:
        open_location = float(row["expected_open_norm"])
        high_location = float(row["expected_high_norm"])
        low_location = float(row["expected_low_norm"])
        close_location = float(row["expected_close_norm"])
        candle_range = float(row["expected_range_norm"])
        shape_total = sum(
            float(row[key])
            for key in (
                "expected_body_ratio",
                "expected_upper_wick_ratio",
                "expected_lower_wick_ratio",
            )
        )
        expected_body_span = (
            candle_range * float(row["expected_body_ratio"]) / shape_total
        )

        assert abs(close_location - open_location) == _approx(
            expected_body_span, abs=3e-6
        )
        assert high_location - low_location == _approx(candle_range, abs=3e-6)
        assert float(row["expected_delta_norm"]) == _approx(
            close_location - previous_close,
            abs=3e-6,
        )
        previous_close = close_location


def test_uncalibrated_direct_artifact_exposes_no_90_percent_path_band(
    tmp_path: Path,
) -> None:
    contribution = _contribution(
        _write_artifact(
            tmp_path,
            production_ready=False,
            include_risk=True,
            direct_path=True,
        )
    )

    assert contribution["trajectory_interval_status"] == "UNAVAILABLE"
    assert contribution["trajectory_interval"]["calibrated"] is False
    assert all(
        "close_lower_90_norm" not in row for row in contribution["forecast_path"]
    )
    assert all(
        "close_upper_90_norm" not in row for row in contribution["forecast_path"]
    )


def test_pathwise_interval_requires_twenty_independent_calibration_groups(
    tmp_path: Path,
) -> None:
    contribution = _contribution(
        _write_artifact(
            tmp_path,
            production_ready=True,
            include_risk=True,
            direct_path=True,
            interval_groups=19,
            interval_quantile=1.0,
        )
    )

    interval = contribution["trajectory_interval"]
    assert contribution["production_authorized"] is True
    assert contribution["trajectory_interval_status"] == "UNAVAILABLE"
    assert interval["calibrated"] is False
    assert interval["source_count"] == 19
    assert interval["safety_reasons"] == ["INSUFFICIENT_INDEPENDENT_CALIBRATION_GROUPS"]
    assert all(
        "close_lower_90_norm" not in row for row in contribution["forecast_path"]
    )
    assert all(
        "close_upper_90_norm" not in row for row in contribution["forecast_path"]
    )


def test_pathwise_interval_rejects_any_raw_full_width_above_thirty_percent(
    tmp_path: Path,
) -> None:
    contribution = _contribution(
        _write_artifact(
            tmp_path,
            production_ready=True,
            include_risk=True,
            direct_path=True,
            interval_groups=20,
            interval_quantile=24.73972467,
        )
    )

    interval = contribution["trajectory_interval"]
    assert contribution["production_authorized"] is True
    assert contribution["trajectory_interval_status"] == "UNAVAILABLE"
    assert interval["calibrated"] is False
    assert float(interval["maximum_raw_full_width_norm"]) > 0.30
    assert interval["safety_reasons"] == ["RAW_FULL_INTERVAL_WIDTH_EXCEEDS_0_30"]
    assert all(
        "close_lower_90_norm" not in row for row in contribution["forecast_path"]
    )
    assert all(
        "close_upper_90_norm" not in row for row in contribution["forecast_path"]
    )


def test_pathwise_interval_preserves_valid_narrow_calibrated_band(
    tmp_path: Path,
) -> None:
    contribution = _contribution(
        _write_artifact(
            tmp_path,
            production_ready=True,
            include_risk=True,
            direct_path=True,
            interval_groups=20,
            interval_quantile=1.0,
        )
    )

    interval = contribution["trajectory_interval"]
    assert contribution["trajectory_interval_status"] == "READY"
    assert interval["calibrated"] is True
    assert interval["source_count"] == 20
    assert 0.0 < float(interval["maximum_raw_full_width_norm"]) <= 0.30
    assert interval["safety_reasons"] == []
    assert all("close_lower_90_norm" in row for row in contribution["forecast_path"])
    assert all("close_upper_90_norm" in row for row in contribution["forecast_path"])


def test_restored_legacy_v3_padding_and_state_dict_contract() -> None:
    torch = pytest.importorskip("torch")
    project_root = Path(__file__).resolve().parents[2]
    export_root = project_root / "models" / "exports" / "lstm_candle_sequence_v3"
    model_path = export_root / "lstm_candle_sequence_v3.pt"
    config = json.loads(
        (export_root / "lstm_candle_sequence_v3_config.json").read_text(
            encoding="utf-8"
        )
    )
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
    assert contribution["production_authorized"] is False
    assert contribution["artifact_production_gate_passed"] is False
    assert contribution["forecast_available"] is False
    assert contribution["forecast_suppressed"] is True
    assert contribution["forecast_path"] == []
    assert contribution["chart_context_used"] is False
    assert contribution["selective_authorized"] is False
    assert contribution["selective_status"] == "NO_EDGE"
    assert contribution["selective_side"] == "NO_EDGE"
    assert (
        contribution["selective_prediction"]["policy"]
        == "LEGACY_EXPORTED_V3_DIAGNOSTIC_ONLY"
    )
    assert contribution["selective_prediction"]["accuracy_guarantee"] is False
    assert contribution["risk_control_status"] == "CALIBRATION_UNAVAILABLE"
    assert contribution["contribution"] == 0.0
    assert "legacy body colour" in contribution["reason"].lower()
    assert "withheld" in contribution["reason"].lower()
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
