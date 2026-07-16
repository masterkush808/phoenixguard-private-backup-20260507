from __future__ import annotations

import importlib
import hashlib
import json
import sys
from collections.abc import Callable, MutableMapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import pytest

from phoenixguard.decision import lstm_candle_sequence_contributor_v3 as contributor


_ARTIFACT_CACHE = cast(
    MutableMapping[object, object],
    getattr(contributor, "_ARTIFACT_CACHE"),
)
_MANIFEST_INTEGRITY_CACHE = cast(
    MutableMapping[object, object],
    getattr(contributor, "_MANIFEST_INTEGRITY_CACHE"),
)
_load_artifact_bundle = cast(
    Callable[[Path, Path, Path], dict[str, Any]],
    getattr(contributor, "_load_artifact_bundle"),
)
_resolve_artifact_bundle_paths = cast(
    Callable[[Path, Path, Path], dict[str, Any]],
    getattr(contributor, "_resolve_artifact_bundle_paths"),
)


@lru_cache(maxsize=1)
def _trainer() -> Any:
    tools = Path(__file__).resolve().parents[1] / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return importlib.import_module("train_lstm_candle_sequence_v3")


def _publish_loadable_generation(
    tmp_path: Path,
    generation: str,
    *,
    before_switch: Any = None,
) -> tuple[Path, Path, Path]:
    trainer = _trainer()
    model_path = tmp_path / "model.pt"
    config_path = tmp_path / "config.json"
    metrics_path = tmp_path / "metrics.json"
    input_dim = len(contributor.FEATURE_SCHEMA)
    hidden_dim = 8
    target_schema = "PG_TEST_DIRECT_CUMULATIVE_TARGET_V3"
    model = contributor.create_lstm_candle_sequence_model(
        input_dim=input_dim,
        hidden_dim=hidden_dim,
        num_layers=1,
        dropout=0.0,
        horizon_steps=contributor.DEFAULT_HORIZON_STEPS,
        trajectory_modes=0,
    )
    trainer._publish_artifact_bundle(
        artifact={
            "artifact_generation_id": generation,
            "training_target_schema_version": target_schema,
            "state_dict": model.state_dict(),
        },
        config={
            "artifact_generation_id": generation,
            "model_version": contributor.LSTM_CANDLE_SEQUENCE_VERSION,
            "architecture": contributor.DIRECT_RAW_CV_ARCHITECTURE,
            "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
            "training_target_schema_version": target_schema,
            "input_dim": input_dim,
            "hidden_dim": hidden_dim,
            "num_layers": 1,
            "dropout": 0.0,
            "horizon_steps": contributor.DEFAULT_HORIZON_STEPS,
            "trajectory_modes": 0,
            "production_ready": False,
        },
        metrics={
            "artifact_generation_id": generation,
            "training_target_schema_version": target_schema,
            "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
            "production_ready": False,
        },
        model_path=model_path,
        config_path=config_path,
        metrics_path=metrics_path,
        _before_manifest_switch=before_switch,
    )
    return model_path, config_path, metrics_path


def _passing_evidence() -> tuple[dict[str, int], dict[str, Any], dict[str, Any]]:
    source_counts = {"train": 200, "validation": 40, "test": 40}
    test_metrics = {
        "balanced_accuracy": 0.60,
        "persistence_baseline_balanced_accuracy": 0.50,
        "confusion_matrix": [[600, 400], [400, 600]],
        "endpoint_path_direction_accuracy": 0.61,
        "endpoint_path_balanced_accuracy": 0.61,
        "endpoint_path_persistence_accuracy": 0.50,
        "path_movement_balanced_accuracy": 0.60,
        "horizon_position_balanced_accuracy": 0.61,
        "endpoint_source_cluster_accuracy_95": {
            "accuracy": 0.61,
            "lower_95": 0.54,
            "upper_95": 0.68,
            "sources": 40,
        },
        "endpoint_predicted_support": {"BUY": 450, "SELL": 450, "HOLD": 100},
        "path_delta_mae": 0.04,
        "pathwise_conformal": {
            "source_simultaneous_coverage": 0.90,
            "mean_full_band_width_relative_price": 0.12,
        },
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


@pytest.mark.parametrize("missing_side", ["BUY", "SELL"])
def test_production_gate_rejects_one_sided_path_predictions(missing_side: str) -> None:
    source_counts, test_metrics, risk_control = _passing_evidence()
    test_metrics["endpoint_predicted_support"][missing_side] = 0

    result = _trainer()._production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=0.60,
        minimum_predictions=20,
    )

    assert result["production_ready"] is False
    assert result["required_selective_precision"] == 0.85
    assert "endpoint_predictions_include_buy_and_sell" in result["failed_checks"]


@pytest.mark.parametrize(
    "confusion",
    [
        [[1000, 0], [1000, 0]],
        [[0, 1000], [0, 1000]],
    ],
    ids=["buy_only", "sell_only"],
)
def test_one_sided_body_head_is_auxiliary_when_direct_path_is_healthy(
    confusion: list[list[int]],
) -> None:
    source_counts, test_metrics, risk_control = _passing_evidence()
    test_metrics["confusion_matrix"] = confusion

    result = _trainer()._production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=0.85,
        minimum_predictions=20,
    )

    assert result["production_ready"] is True
    assert result["checks"]["both_body_direction_class_recalls_at_least_chance"] is False
    assert "both_body_direction_class_recalls_at_least_chance" not in result["failed_checks"]


def test_production_gate_rejects_strong_body_metrics_with_chance_level_path() -> None:
    source_counts, test_metrics, risk_control = _passing_evidence()
    test_metrics["balanced_accuracy"] = 0.90
    test_metrics["path_movement_balanced_accuracy"] = 0.3333
    test_metrics["endpoint_path_direction_accuracy"] = 0.61

    result = _trainer()._production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=0.85,
        minimum_predictions=20,
    )

    assert result["production_ready"] is False
    assert "path_movement_balanced_accuracy_at_least_40" in result["failed_checks"]


def test_production_gate_rejects_non_informative_path_band() -> None:
    source_counts, test_metrics, risk_control = _passing_evidence()
    test_metrics["pathwise_conformal"]["mean_full_band_width_relative_price"] = 0.31

    result = _trainer()._production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=0.85,
        minimum_predictions=20,
    )

    assert result["production_ready"] is False
    assert "pathwise_mean_full_band_width_at_most_30" in result["failed_checks"]


def test_body_direction_baseline_edge_is_auxiliary_when_direct_path_is_healthy() -> None:
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

    assert result["production_ready"] is True
    assert result["checks"]["body_direction_balanced_accuracy_at_least_52"] is False
    assert result["checks"]["body_direction_beats_persistence_by_one_point"] is False


def test_artifact_bundle_publication_stages_one_generation(
    tmp_path: Path,
) -> None:
    trainer = _trainer()
    generation = "generation-atomic-v3"
    model_path = tmp_path / "model.pt"
    config_path = tmp_path / "config.json"
    metrics_path = tmp_path / "metrics.json"

    trainer._publish_artifact_bundle(
        artifact={
            "artifact_generation_id": generation,
            "state_dict": {"weight": trainer.torch.tensor([1.0])},
        },
        config={"artifact_generation_id": generation},
        metrics={"artifact_generation_id": generation},
        model_path=model_path,
        config_path=config_path,
        metrics_path=metrics_path,
    )

    artifact = trainer.torch.load(
        model_path,
        map_location="cpu",
        weights_only=False,
    )
    assert artifact["artifact_generation_id"] == generation
    assert json.loads(config_path.read_text(encoding="utf-8"))[
        "artifact_generation_id"
    ] == generation
    assert json.loads(metrics_path.read_text(encoding="utf-8"))[
        "artifact_generation_id"
    ] == generation
    assert not list(tmp_path.glob("*.tmp"))


def test_artifact_bundle_publication_rejects_mixed_generations(
    tmp_path: Path,
) -> None:
    trainer = _trainer()

    with pytest.raises(RuntimeError, match="generation is inconsistent"):
        trainer._publish_artifact_bundle(
            artifact={"artifact_generation_id": "weights-a"},
            config={"artifact_generation_id": "config-b"},
            metrics={"artifact_generation_id": "metrics-c"},
            model_path=tmp_path / "model.pt",
            config_path=tmp_path / "config.json",
            metrics_path=tmp_path / "metrics.json",
        )

    assert not list(tmp_path.iterdir())


def test_interruption_before_manifest_switch_keeps_old_generation_loadable(
    tmp_path: Path,
) -> None:
    _ARTIFACT_CACHE.clear()
    _MANIFEST_INTEGRITY_CACHE.clear()
    paths = _publish_loadable_generation(tmp_path, "generation-old")
    manifest_path = contributor.artifact_bundle_manifest_path(paths[0])
    old_manifest_bytes = manifest_path.read_bytes()
    old_bundle = _load_artifact_bundle(*paths)

    assert old_bundle["model_loaded"] is True
    assert old_bundle["artifact_generation_id"] == "generation-old"

    def interrupt_before_pointer() -> None:
        raise InterruptedError("simulated stop before pointer switch")

    with pytest.raises(InterruptedError, match="before pointer switch"):
        _publish_loadable_generation(
            tmp_path,
            "generation-unpublished",
            before_switch=interrupt_before_pointer,
        )

    assert manifest_path.read_bytes() == old_manifest_bytes
    resolution = _resolve_artifact_bundle_paths(*paths)
    assert resolution["error"] == ""
    assert resolution["generation_id"] == "generation-old"
    _ARTIFACT_CACHE.clear()
    still_live = _load_artifact_bundle(*paths)
    assert still_live["model_loaded"] is True
    assert still_live["artifact_generation_id"] == "generation-old"


def test_manifest_switch_loads_one_complete_new_generation(tmp_path: Path) -> None:
    _ARTIFACT_CACHE.clear()
    _MANIFEST_INTEGRITY_CACHE.clear()
    paths = _publish_loadable_generation(tmp_path, "generation-one")
    old_manifest = contributor.artifact_bundle_manifest_path(paths[0]).read_bytes()

    _publish_loadable_generation(tmp_path, "generation-two")

    manifest_path = contributor.artifact_bundle_manifest_path(paths[0])
    assert manifest_path.read_bytes() != old_manifest
    resolution = _resolve_artifact_bundle_paths(*paths)
    assert resolution["error"] == ""
    assert resolution["generation_id"] == "generation-two"
    selected_paths = resolution["paths"]
    assert all("generation-two" in str(path) for path in selected_paths)
    _ARTIFACT_CACHE.clear()
    selected = _load_artifact_bundle(*paths)
    assert selected["model_loaded"] is True
    assert selected["artifact_generation_id"] == "generation-two"


def test_manifest_rejects_hash_invalid_referenced_file(tmp_path: Path) -> None:
    _ARTIFACT_CACHE.clear()
    _MANIFEST_INTEGRITY_CACHE.clear()
    paths = _publish_loadable_generation(tmp_path, "generation-hash")
    resolution = _resolve_artifact_bundle_paths(*paths)
    model_path = resolution["paths"][0]
    model_bytes = bytearray(model_path.read_bytes())
    model_bytes[-1] ^= 0x01
    model_path.write_bytes(model_bytes)
    _MANIFEST_INTEGRITY_CACHE.clear()

    rejected = _resolve_artifact_bundle_paths(*paths)

    assert "model hash does not match" in rejected["error"]
    runtime = _load_artifact_bundle(*paths)
    assert runtime["model_loaded"] is False
    assert "model hash does not match" in runtime["error"]


def test_manifest_rejects_referenced_internal_generation_mismatch(
    tmp_path: Path,
) -> None:
    _ARTIFACT_CACHE.clear()
    _MANIFEST_INTEGRITY_CACHE.clear()
    paths = _publish_loadable_generation(tmp_path, "generation-pointer")
    manifest_path = contributor.artifact_bundle_manifest_path(paths[0])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_reference = manifest_path.parent / manifest["files"]["config"]["path"]
    config = json.loads(config_reference.read_text(encoding="utf-8"))
    config["artifact_generation_id"] = "generation-other"
    config_reference.write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest["files"]["config"]["sha256"] = hashlib.sha256(
        config_reference.read_bytes()
    ).hexdigest()
    manifest["files"]["config"]["size_bytes"] = config_reference.stat().st_size
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    rejected = _resolve_artifact_bundle_paths(*paths)

    assert "config generation does not match pointer" in rejected["error"]
    runtime = _load_artifact_bundle(*paths)
    assert runtime["model_loaded"] is False
    assert "config generation does not match pointer" in runtime["error"]


def test_manifest_rejects_missing_referenced_file(tmp_path: Path) -> None:
    _ARTIFACT_CACHE.clear()
    _MANIFEST_INTEGRITY_CACHE.clear()
    paths = _publish_loadable_generation(tmp_path, "generation-missing")
    selected = _resolve_artifact_bundle_paths(*paths)
    selected["paths"][2].unlink()
    _MANIFEST_INTEGRITY_CACHE.clear()

    runtime = _load_artifact_bundle(*paths)

    assert runtime["model_loaded"] is False
    assert "artifact manifest rejected" in runtime["error"]


def test_runtime_preserves_legacy_no_manifest_bundle(tmp_path: Path) -> None:
    _ARTIFACT_CACHE.clear()
    _MANIFEST_INTEGRITY_CACHE.clear()
    paths = _publish_loadable_generation(tmp_path, "generation-legacy-compatible")
    contributor.artifact_bundle_manifest_path(paths[0]).unlink()

    legacy = _load_artifact_bundle(*paths)

    assert legacy["model_loaded"] is True
    assert legacy["artifact_generation_id"] == "generation-legacy-compatible"
    assert legacy["artifact_manifest_path"] == ""
