from __future__ import annotations

import importlib
import csv
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _trainer() -> Any:
    tools = Path(__file__).resolve().parents[1] / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return importlib.import_module("train_lstm_candle_sequence_v3")


def test_direct_path_target_is_cumulative_from_one_observed_anchor() -> None:
    trainer = _trainer()
    targets, movement = trainer._direct_cumulative_path_targets(
        [
            {"relative_price_delta_scaled": 0.10, "direction_value": -1.0},
            {"relative_price_delta_scaled": -0.15, "direction_value": 1.0},
            {"relative_price_delta_scaled": 0.045, "direction_value": 1.0},
        ]
    )

    path_index = trainer.PATH_TARGET_FEATURE_INDEX
    assert [round(row[path_index], 6) for row in targets] == [0.10, -0.05, -0.005]
    assert movement == [
        trainer.SIDE_TO_INDEX["BUY"],
        trainer.SIDE_TO_INDEX["SELL"],
        trainer.SIDE_TO_INDEX["BUY"],
    ]


def test_direct_path_target_is_clipped_to_supported_regression_range() -> None:
    trainer = _trainer()
    targets, _movement = trainer._direct_cumulative_path_targets(
        [
            {"relative_price_delta_scaled": 0.8},
            {"relative_price_delta_scaled": 0.7},
            {"relative_price_delta_scaled": -3.0},
        ]
    )

    path_index = trainer.PATH_TARGET_FEATURE_INDEX
    assert [row[path_index] for row in targets] == [0.8, 1.0, -1.0]


def test_pathwise_conformal_uses_one_worst_score_per_validation_source() -> None:
    trainer = _trainer()
    details = {
        "window_path_means": [[0.0, 0.0]] * 4,
        "window_path_scales": [[0.1, 0.1]] * 4,
        "window_path_targets": [
            [0.1, 0.0],   # source A score 1
            [0.0, 0.3],   # source A score 3; only this score represents A
            [0.2, 0.0],   # source B score 2
            [0.0, 0.4],   # source C score 4
        ],
        "window_sources": ["A", "A", "B", "C"],
    }

    calibration = trainer._fit_source_grouped_pathwise_conformal(details, alpha=0.5)
    evaluation = trainer._evaluate_pathwise_conformal(details, calibration)

    assert calibration["calibration_windows"] == 4
    assert calibration["calibration_sources"] == 3
    assert calibration["finite_sample_rank"] == 2
    assert calibration["quantile"] == 3.0
    assert evaluation["trajectory_simultaneous_coverage"] == 0.75
    assert evaluation["source_simultaneous_coverage"] == 0.6667


def test_manifest_perceptual_groups_are_the_independent_evaluation_unit(tmp_path: Path) -> None:
    trainer = _trainer()
    manifest = tmp_path / "split_manifest.csv"
    source_a = tmp_path / "a.png"
    source_a_copy = tmp_path / "a-copy.png"
    source_b = tmp_path / "b.png"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["split", "group_index", "source_path", "destination_path"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {"split": "val", "group_index": "17", "source_path": source_a, "destination_path": ""},
                {"split": "val", "group_index": "17", "source_path": source_a_copy, "destination_path": ""},
                {"split": "val", "group_index": "18", "source_path": source_b, "destination_path": ""},
            ]
        )

    mapping = trainer._split_manifest_source_map(manifest)
    group_a = mapping[trainer._resolved_key(source_a)]["independent_group"]
    group_a_copy = mapping[trainer._resolved_key(source_a_copy)]["independent_group"]
    group_b = mapping[trainer._resolved_key(source_b)]["independent_group"]

    assert group_a == group_a_copy == "val:perceptual-group:17"
    assert group_b == "val:perceptual-group:18"

    details = {
        "window_path_means": [[0.0], [0.0], [0.0]],
        "window_path_scales": [[0.1], [0.1], [0.1]],
        "window_path_targets": [[0.1], [0.3], [0.2]],
        "window_sources": [str(source_a), str(source_a_copy), str(source_b)],
        "window_independent_groups": [group_a, group_a_copy, group_b],
    }
    calibration = trainer._fit_source_grouped_pathwise_conformal(details, alpha=0.5)

    assert calibration["calibration_windows"] == 3
    assert calibration["calibration_independent_groups"] == 2


def test_batched_augmentation_preserves_padding_direction_and_causal_pixels() -> None:
    trainer = _trainer()
    torch = trainer.torch
    torch.manual_seed(7)
    sequence = torch.ones((2, 6, len(trainer.FEATURE_SCHEMA)), dtype=torch.float32)
    direction_index = trainer.FEATURE_SCHEMA.index("direction_value")
    sequence[:, :, direction_index] = torch.tensor([[1.0], [-1.0]])
    sequence[0, 4:, :] = 0.0
    context = torch.ones((2, 3, 8, 12), dtype=torch.float32)
    context[:, :, :, 7:] = 0.0

    augmented_sequence, augmented_context = trainer._augment_training_batch(
        sequence,
        context,
        torch.tensor([4, 6]),
    )

    assert torch.equal(augmented_sequence[:, :, direction_index], sequence[:, :, direction_index])
    assert torch.equal(augmented_sequence[0, 4:, :], sequence[0, 4:, :])
    assert torch.count_nonzero(augmented_context[:, :, :, 7:]) == 0


def test_checkpoint_selection_penalizes_one_sided_endpoint_collapse() -> None:
    trainer = _trainer()
    healthy = {
        "balanced_accuracy": 0.50,
        "path_movement_balanced_accuracy": 0.42,
        "horizon_position_balanced_accuracy": 0.46,
        "endpoint_path_balanced_accuracy": 0.50,
        "endpoint_path_direction_accuracy": 0.54,
        "endpoint_path_persistence_accuracy": 0.50,
        "endpoint_predicted_support": {"BUY": 160, "SELL": 160, "HOLD": 80},
        "horizon_path_movement_accuracy": {"12": 0.46},
        "path_delta_mae": 0.05,
    }
    collapsed = {
        **healthy,
        "endpoint_path_direction_accuracy": 0.56,
        "endpoint_predicted_support": {"BUY": 0, "SELL": 400, "HOLD": 0},
    }

    healthy_score, healthy_evidence = trainer._path_model_selection_score(
        healthy,
        horizon_steps=12,
    )
    collapsed_score, collapsed_evidence = trainer._path_model_selection_score(
        collapsed,
        horizon_steps=12,
    )

    assert healthy_score > collapsed_score
    assert healthy_evidence["collapse_penalty"] == 0.0
    assert collapsed_evidence["collapse_penalty"] == 0.20


def test_event_lattice_quality_rejects_overlapping_texture_fragments() -> None:
    trainer = _trainer()
    contributor = importlib.import_module(
        "phoenixguard.decision.lstm_candle_sequence_contributor_v3"
    )
    regular = [
        {"bbox": [10.0 + 8.0 * index, 20.0, 14.0 + 8.0 * index, 50.0]}
        for index in range(20)
    ]
    overlapping = [
        {"bbox": [10.0 + 2.0 * index, 20.0, 16.0 + 2.0 * index, 50.0]}
        for index in range(20)
    ]

    accepted = contributor.candle_sequence_geometry_quality(
        regular,
        image_size=(200, 100),
    )
    rejected = contributor.candle_sequence_geometry_quality(
        overlapping,
        image_size=(200, 100),
    )

    assert accepted["status"] == "READY"
    assert accepted["overlap_rate"] == 0.0
    assert rejected["status"] == "REJECTED"
    assert rejected["overlap_rate"] == 1.0
    assert "overlapping_component_events" in rejected["reasons"]
    assert trainer.candle_sequence_geometry_quality is contributor.candle_sequence_geometry_quality
