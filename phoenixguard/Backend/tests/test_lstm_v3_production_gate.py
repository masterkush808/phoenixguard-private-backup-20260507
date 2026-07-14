from __future__ import annotations

import importlib
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _trainer() -> Any:
    tools = Path(__file__).resolve().parents[1] / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return importlib.import_module("train_lstm_candle_sequence_v3")


def _passing_evidence() -> tuple[dict[str, int], dict[str, Any], dict[str, Any]]:
    source_counts = {"train": 200, "validation": 40, "test": 40}
    test_metrics = {
        "balanced_accuracy": 0.60,
        "persistence_baseline_balanced_accuracy": 0.50,
        "confusion_matrix": [[600, 400], [400, 600]],
        "endpoint_path_direction_accuracy": 0.61,
        "interval_90_coverage": 0.90,
        "source_cluster_accuracy_95": {
            "accuracy": 0.60,
            "lower_95": 0.54,
            "upper_95": 0.66,
            "sources": 40,
        },
    }
    class_evidence = {
        "selected": 300,
        "correct": 276,
        "precision": 0.92,
        "wilson_lower_95": 0.88,
    }
    risk_control = {
        "test_selection": {
            "accuracy": 0.92,
            "macro_predicted_class_precision": 0.92,
            "wilson_lower_95": 0.89,
            "per_class": {
                "BUY": deepcopy(class_evidence),
                "SELL": deepcopy(class_evidence),
            },
        },
        "test_selected_source_cluster_accuracy_95": {
            "accuracy": 0.92,
            "lower_95": 0.86,
            "upper_95": 0.96,
            "sources": 20,
        },
    }
    return source_counts, test_metrics, risk_control


def test_production_gate_accepts_robust_locked_test_evidence() -> None:
    source_counts, test_metrics, risk_control = _passing_evidence()

    result = _trainer()._production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=0.85,
        minimum_predictions=20,
    )

    assert result["production_ready"] is True
    assert result["locked_test_selective_point_pass"] is True
    assert result["locked_test_selective_robust_pass"] is True
    assert result["failed_checks"] == []


def test_production_gate_rejects_one_sided_selective_predictions() -> None:
    source_counts, test_metrics, risk_control = _passing_evidence()
    risk_control["test_selection"]["per_class"]["BUY"] = {
        "selected": 0,
        "correct": 0,
        "precision": 0.0,
        "wilson_lower_95": 0.0,
    }

    result = _trainer()._production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=0.60,
        minimum_predictions=20,
    )

    assert result["production_ready"] is False
    assert result["required_selective_precision"] == 0.85
    assert "locked_selective_each_class_has_minimum_support" in result["failed_checks"]
    assert "locked_selective_each_class_precision_at_target" in result["failed_checks"]


def test_production_gate_rejects_direction_without_real_baseline_edge() -> None:
    source_counts, test_metrics, risk_control = _passing_evidence()
    test_metrics["balanced_accuracy"] = 0.519
    test_metrics["persistence_baseline_balanced_accuracy"] = 0.515

    result = _trainer()._production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=0.85,
        minimum_predictions=20,
    )

    assert result["production_ready"] is False
    assert "direction_balanced_accuracy_at_least_52" in result["failed_checks"]
    assert "direction_beats_persistence_by_one_point" in result["failed_checks"]
