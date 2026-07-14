from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
import math
import sys
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Mapping, cast

PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_BOOTSTRAP))

from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_HORIZON_STEPS,
    DEFAULT_METRICS_PATH,
    DEFAULT_MODEL_PATH,
    DEFAULT_SEQUENCE_LENGTH,
    DIRECT_RAW_CV_ARCHITECTURE,
    FEATURE_SCHEMA,
    LEGACY_MULTISCALE_ARCHITECTURE,
    PLAY_LABELS,
    PREDICTION_SCHEMA,
    create_legacy_lstm_candle_sequence_model,
    create_lstm_candle_sequence_model,
)


EXPECTED_VISUAL_FRONTEND = "SHARED_ADAPTIVE_PALETTE_OHLC_PLUS_CAUSAL_RAW_CHART_PIXELS"
LEGACY_VISUAL_FRONTEND = "OPENCV_CANDLE_GEOMETRY_AND_MULTISCALE_PATTERN_CONVOLUTIONS"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(cast(Mapping[str, Any], payload)) if isinstance(payload, Mapping) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _locked_test_release_evidence(config: Mapping[str, Any], metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Independently verify the fail-closed claims required for promotion."""

    test = _mapping(metrics.get("test"))
    risk = _mapping(metrics.get("risk_control"))
    selection = _mapping(risk.get("test_selection"))
    per_class = _mapping(selection.get("per_class"))
    classes = [_mapping(per_class.get(side)) for side in ("BUY", "SELL")]
    direction_cluster = _mapping(test.get("source_cluster_accuracy_95"))
    selective_cluster = _mapping(risk.get("test_selected_source_cluster_accuracy_95"))
    source_counts = _mapping(metrics.get("source_counts") or config.get("source_counts"))
    target = max(
        0.85,
        min(
            1.0,
            _finite_float(
                metrics.get("selective_target_precision", config.get("target_selective_precision", 0.85)),
                0.85,
            ),
        ),
    )
    validation_selection = _mapping(risk.get("validation_selection"))
    minimum_predictions = max(1, int(validation_selection.get("minimum_predictions_per_class", 20) or 20))

    recalls: list[float] = []
    confusion = test.get("confusion_matrix")
    if isinstance(confusion, Sequence) and not isinstance(confusion, (str, bytes, bytearray)):
        matrix = cast(Sequence[Any], confusion)
        for index in range(2):
            if index >= len(matrix) or not isinstance(matrix[index], Sequence) or isinstance(
                matrix[index], (str, bytes, bytearray)
            ):
                recalls.append(0.0)
                continue
            row = cast(Sequence[Any], matrix[index])
            support = sum(max(0.0, _finite_float(value)) for value in row)
            correct = max(0.0, _finite_float(row[index])) if index < len(row) else 0.0
            recalls.append(correct / support if support > 0.0 else 0.0)
    while len(recalls) < 2:
        recalls.append(0.0)

    balanced = _finite_float(test.get("balanced_accuracy"))
    persistence = _finite_float(test.get("persistence_baseline_balanced_accuracy"))
    checks = {
        "independent_split_support": (
            int(source_counts.get("train", 0) or 0) >= 100
            and int(source_counts.get("validation", 0) or 0) >= 20
            and int(source_counts.get("test", 0) or 0) >= 20
        ),
        "direction_balanced_accuracy_at_least_52": balanced >= 0.52,
        "direction_beats_persistence_by_one_point": balanced >= persistence + 0.01,
        "direction_source_cluster_lower_95_above_chance": _finite_float(direction_cluster.get("lower_95")) > 0.50,
        "both_direction_class_recalls_at_least_chance": all(value >= 0.50 for value in recalls),
        "endpoint_path_direction_accuracy_at_least_55": _finite_float(
            test.get("endpoint_path_direction_accuracy")
        )
        >= 0.55,
        "interval_90_coverage_at_least_70": _finite_float(test.get("interval_90_coverage")) >= 0.70,
        "locked_selective_accuracy_at_target": _finite_float(selection.get("accuracy")) >= target,
        "locked_selective_macro_precision_at_target": _finite_float(
            selection.get("macro_predicted_class_precision")
        )
        >= target,
        "locked_selective_overall_wilson_lower_at_target": _finite_float(selection.get("wilson_lower_95"))
        >= target,
        "locked_selective_each_class_has_minimum_support": all(
            int(row.get("selected", 0) or 0) >= minimum_predictions for row in classes
        ),
        "locked_selective_each_class_precision_at_target": all(
            _finite_float(row.get("precision")) >= target for row in classes
        ),
        "locked_selective_each_class_wilson_lower_at_target": all(
            _finite_float(row.get("wilson_lower_95")) >= target for row in classes
        ),
        "locked_selective_spans_ten_source_clusters": int(selective_cluster.get("sources", 0) or 0) >= 10,
        "locked_selective_source_cluster_lower_95_at_target": _finite_float(selective_cluster.get("lower_95"))
        >= target,
    }
    return {
        "passes": all(checks.values()),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "required_selective_precision": target,
        "minimum_predictions_per_class": minimum_predictions,
        "test_class_recalls": {"BUY": round(recalls[0], 6), "SELL": round(recalls[1], 6)},
        "balanced_accuracy_margin_over_persistence": round(balanced - persistence, 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate staged LSTM candle-sequence V3 artifacts.")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--metrics-path", type=Path, default=DEFAULT_METRICS_PATH)
    args = parser.parse_args()

    config = _load(args.config_path)
    metrics = _load(args.metrics_path)
    architecture = str(config.get("architecture") or "")
    legacy_restored = architecture == LEGACY_MULTISCALE_ARCHITECTURE
    inference_ok = False
    inference_error = ""
    if args.model_path.exists() and config:
        try:
            import torch

            model_factory = (
                create_legacy_lstm_candle_sequence_model
                if legacy_restored
                else create_lstm_candle_sequence_model
            )
            model = model_factory(
                input_dim=int(config.get("input_dim", len(FEATURE_SCHEMA)) or len(FEATURE_SCHEMA)),
                hidden_dim=int(config.get("hidden_dim", 96) or 96),
                num_layers=int(config.get("num_layers", 2) or 2),
                dropout=float(config.get("dropout", 0.15) or 0.0),
                horizon_steps=int(config.get("horizon_steps", DEFAULT_HORIZON_STEPS) or DEFAULT_HORIZON_STEPS),
            )
            loaded: object = torch.load(args.model_path, map_location="cpu", weights_only=False)
            loaded_map = dict(cast(Mapping[str, Any], loaded)) if isinstance(loaded, Mapping) else {}
            state_dict = loaded_map.get("state_dict", loaded)
            model.load_state_dict(cast(Mapping[str, Any], state_dict))
            model.eval()
            sequence_length = int(config.get("sequence_length", DEFAULT_SEQUENCE_LENGTH) or DEFAULT_SEQUENCE_LENGTH)
            horizon_steps = int(config.get("horizon_steps", DEFAULT_HORIZON_STEPS) or DEFAULT_HORIZON_STEPS)
            with torch.inference_mode():
                inference_input = torch.zeros(
                    (1, sequence_length, len(FEATURE_SCHEMA)),
                    dtype=torch.float32,
                )
                if legacy_restored:
                    outputs = model(inference_input, horizon_steps=horizon_steps)
                else:
                    chart_size = config.get("chart_context_size", [96, 192])
                    if not isinstance(chart_size, Sequence) or isinstance(
                        chart_size, (str, bytes, bytearray)
                    ):
                        raise ValueError("chart_context_size must contain height and width")
                    chart_size_values = cast(Sequence[Any], chart_size)
                    if len(chart_size_values) < 2:
                        raise ValueError("chart_context_size must contain height and width")
                    outputs = model(
                        inference_input,
                        horizon_steps=horizon_steps,
                        lengths=torch.tensor([max(1, sequence_length // 2)], dtype=torch.long),
                        chart_context=torch.zeros(
                            (
                                1,
                                3,
                                max(8, int(chart_size_values[0])),
                                max(8, int(chart_size_values[1])),
                            ),
                            dtype=torch.float32,
                        ),
                    )
            common_outputs = (
                "direction_logits",
                "feature_mean",
                "feature_scale",
                "play_logits",
            )
            inference_ok = bool(
                tuple(outputs["direction_logits"].shape) == (1, horizon_steps, 2)
                and tuple(outputs["feature_mean"].shape) == (1, horizon_steps, len(PREDICTION_SCHEMA))
                and tuple(outputs["feature_scale"].shape) == (1, horizon_steps, len(PREDICTION_SCHEMA))
                and tuple(outputs["play_logits"].shape) == (1, len(PLAY_LABELS))
                and bool(torch.all(outputs["feature_scale"] > 0.0))
                and all(bool(torch.all(torch.isfinite(outputs[key]))) for key in common_outputs)
                and (
                    legacy_restored
                    or (
                        tuple(outputs["decision_logits"].shape) == (1, horizon_steps, 2)
                        and tuple(outputs["context_embedding"].shape)
                        == (1, int(config.get("hidden_dim", 96) or 96))
                        and bool(torch.all(torch.isfinite(outputs["decision_logits"])))
                        and bool(torch.all(torch.isfinite(outputs["context_embedding"])))
                    )
                )
            )
        except Exception as exc:
            inference_error = str(exc)
    reported_production_ready = bool(config.get("production_ready") and metrics.get("production_ready"))
    release_evidence = (
        {
            "passes": reported_production_ready,
            "protocol": "LEGACY_RESTORED_RECORDED_METRICS",
            "comparable_to_clean_grouped_test": False,
            "direction_accuracy": metrics.get("test_direction_accuracy"),
            "balanced_accuracy": metrics.get("test_balanced_accuracy"),
            "first_event_accuracy": _mapping(metrics.get("test_horizon_direction_accuracy")).get("1"),
            "twelfth_event_accuracy": _mapping(metrics.get("test_horizon_direction_accuracy")).get("12"),
        }
        if legacy_restored
        else _locked_test_release_evidence(config, metrics)
    )
    recorded_readiness = _mapping(metrics.get("production_readiness"))
    recorded_checks = _mapping(recorded_readiness.get("checks") or config.get("production_readiness_checks"))
    recorded_release_pass = (
        reported_production_ready
        if legacy_restored
        else bool(recorded_checks) and all(bool(value) for value in recorded_checks.values())
    )
    required_metric_keys = (
        (
            "test_balanced_accuracy",
            "test_path_delta_mae",
            "test_interval_90_coverage",
            "test_calibration_error",
            "test_persistence_baseline_accuracy",
            "test_persistence_baseline_balanced_accuracy",
            "test_play_balanced_accuracy",
            "test_direction_accuracy",
            "test_horizon_direction_accuracy",
        )
        if legacy_restored
        else (
            "test_balanced_accuracy",
            "test_path_delta_mae",
            "test_interval_90_coverage",
            "test_calibration_error",
            "test_persistence_baseline_accuracy",
            "test_persistence_baseline_balanced_accuracy",
            "test_endpoint_path_direction_accuracy",
            "test_source_cluster_accuracy_95",
            "test_play_balanced_accuracy",
            "test_selective_accuracy",
            "test_selective_buy_precision",
            "test_selective_sell_precision",
        )
    )
    checks: dict[str, bool] = {
        "model_exists": args.model_path.exists(),
        "config_exists": bool(config),
        "metrics_exists": bool(metrics),
        "feature_schema_exists": bool(config.get("feature_schema")),
        "model_version_exists": bool(config.get("model_version") or metrics.get("model_version")),
        "phoenixguard_v3_stack": str(config.get("stack_version")) == "PHOENIXGUARD_V3",
        "computer_vision_modality": str(config.get("modality")) == "COMPUTER_VISION"
        and str(config.get("training_source")) == "RAW_SCREENSHOT_SUITES",
        "supported_v3_architecture": architecture
        in {DIRECT_RAW_CV_ARCHITECTURE, LEGACY_MULTISCALE_ARCHITECTURE},
        "matching_visual_frontend": str(config.get("visual_frontend"))
        == (LEGACY_VISUAL_FRONTEND if legacy_restored else EXPECTED_VISUAL_FRONTEND),
        "inference_test_passes": inference_ok,
        "causal_candle_event_horizon": str(config.get("horizon_unit")) == "CANDLE_EVENTS"
        and str(config.get("clock_time_assumption")) == "NONE",
        "source_grouped_splits_exist": all(int(dict(config.get("source_counts") or {}).get(key, 0)) > 0 for key in ("train", "validation", "test")),
        "evaluation_metrics_exist": all(key in metrics for key in required_metric_keys),
        "evaluation_protocol_evidence_exists": bool(
            legacy_restored
            or (
                _mapping(_mapping(metrics.get("risk_control")).get("test_selection")).get("per_class")
                and _mapping(_mapping(metrics.get("risk_control")).get("test_selected_source_cluster_accuracy_95"))
            )
        ),
        "production_ready": bool(
            reported_production_ready
            and recorded_release_pass
            and release_evidence["passes"]
        ),
    }
    ok = all(value for key, value in checks.items() if key != "production_ready")
    payload: dict[str, object] = {
        "ok": ok,
        "production_ready": checks["production_ready"],
        "architecture": architecture,
        "evaluation_protocol": (
            "LEGACY_RESTORED_RECORDED_METRICS"
            if legacy_restored
            else "CLEAN_GROUPED_LOCKED_TEST"
        ),
        "metrics_comparable_to_clean_grouped_test": not legacy_restored,
        "reported_production_ready": reported_production_ready,
        "recorded_release_gate_passes": recorded_release_pass,
        "release_evidence": release_evidence,
        "checks": checks,
        "config_path": str(args.config_path),
        "inference_error": inference_error,
        "metrics_path": str(args.metrics_path),
        "model_path": str(args.model_path),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
