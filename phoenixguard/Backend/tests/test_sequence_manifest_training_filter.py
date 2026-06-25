from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from PIL import Image
import torch

from phoenixguard.training.ensemble_cv_models import ChartImageDataset, EnsembleCVModels


def _write_image(path: Path, *, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=color).save(path)


def _write_manifest(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def test_sequence_manifest_quality_filter_skips_contradictory_aux_rows(tmp_path: Path) -> None:
    buy_dir = tmp_path / "train" / "BUY"
    sell_dir = tmp_path / "train" / "SELL"
    clean_buy = buy_dir / "clean_buy.png"
    contradictory_buy = buy_dir / "contradictory_buy.png"
    clean_sell = sell_dir / "clean_sell.png"
    _write_image(clean_buy, color=(0, 255, 0))
    _write_image(contradictory_buy, color=(0, 200, 0))
    _write_image(clean_sell, color=(255, 0, 0))

    manifest = tmp_path / "sequence_teacher_manifest.jsonl"
    _write_manifest(
        manifest,
        [
            {
                "image_path": str(clean_buy),
                "label": "BUY",
                "sequence_targets": {
                    "projection_direction": "BUY",
                    "next_box_direction": "BUY",
                    "structure_setup": "impulse_chain",
                },
                "teacher_task_labels": {
                    "label_quality": "clean",
                    "review_bucket": "clean_alignment",
                    "review_required": False,
                },
            },
            {
                "image_path": str(contradictory_buy),
                "label": "BUY",
                "sequence_targets": {
                    "projection_direction": "BUY",
                    "next_box_direction": "BUY",
                    "structure_setup": "reversal_release",
                },
                "teacher_task_labels": {
                    "label_quality": "contradictory",
                    "review_bucket": "hard_negative",
                    "review_required": True,
                },
            },
            {
                "image_path": str(clean_sell),
                "label": "SELL",
                "sequence_targets": {
                    "projection_direction": "SELL",
                    "next_box_direction": "SELL",
                    "structure_setup": "impulse_chain",
                },
                "teacher_task_labels": {
                    "label_quality": "clean",
                    "review_bucket": "clean_alignment",
                    "review_required": False,
                },
            },
        ],
    )

    ensemble = EnsembleCVModels(
        image_dirs=[str(buy_dir), str(sell_dir)],
        device=torch.device("cpu"),
        target_models=["mobilenetv3"],
        sequence_manifest_path=str(manifest),
        sequence_manifest_quality="exclude_contradictory",
        enable_continual_learning=False,
    )

    assert ensemble.sequence_manifest_filter_stats["mode"] == "exclude_contradictory"
    assert ensemble.sequence_manifest_filter_stats["record_count"] == 3
    assert ensemble.sequence_manifest_filter_stats["retained_records"] == 2
    assert ensemble.sequence_manifest_filter_stats["skipped_records"] == 1
    assert ensemble.sequence_manifest_filter_stats["skip_reasons"]["contradictory"] == 1

    dataset = cast(ChartImageDataset, ensemble.build_dataset(
        model_name="mobilenetv3",
        image_dirs=[str(buy_dir), str(sell_dir)],
        is_training=False,
    ))
    sample_index = {Path(path).name: idx for idx, path in enumerate(dataset.samples)}

    contradictory_targets = dataset.sequence_label_indices[sample_index["contradictory_buy.png"]]
    clean_buy_targets = dataset.sequence_label_indices[sample_index["clean_buy.png"]]
    clean_sell_targets = dataset.sequence_label_indices[sample_index["clean_sell.png"]]

    assert all(int(value) == -1 for value in contradictory_targets.values())
    assert all(int(value) >= 0 for value in clean_buy_targets.values())
    assert all(int(value) >= 0 for value in clean_sell_targets.values())


def test_sequence_manifest_quality_filter_clean_only_keeps_clean_rows(tmp_path: Path) -> None:
    buy_dir = tmp_path / "train" / "BUY"
    sell_dir = tmp_path / "train" / "SELL"
    clean_buy = buy_dir / "clean_buy.png"
    review_sell = sell_dir / "review_sell.png"
    _write_image(clean_buy, color=(0, 255, 0))
    _write_image(review_sell, color=(255, 128, 0))

    manifest = tmp_path / "sequence_teacher_manifest.jsonl"
    _write_manifest(
        manifest,
        [
            {
                "image_path": str(clean_buy),
                "label": "BUY",
                "sequence_targets": {
                    "projection_direction": "BUY",
                    "next_box_direction": "BUY",
                },
                "teacher_task_labels": {
                    "label_quality": "clean",
                    "review_bucket": "clean_alignment",
                    "review_required": False,
                },
            },
            {
                "image_path": str(review_sell),
                "label": "SELL",
                "sequence_targets": {
                    "projection_direction": "SELL",
                    "next_box_direction": "SELL",
                },
                "teacher_task_labels": {
                    "label_quality": "review_required",
                    "review_bucket": "projection_conflict",
                    "review_required": True,
                },
            },
        ],
    )

    ensemble = EnsembleCVModels(
        image_dirs=[str(buy_dir), str(sell_dir)],
        device=torch.device("cpu"),
        target_models=["mobilenetv3"],
        sequence_manifest_path=str(manifest),
        sequence_manifest_quality="clean_only",
        enable_continual_learning=False,
    )

    assert ensemble.sequence_manifest_filter_stats["retained_records"] == 1
    assert ensemble.sequence_manifest_filter_stats["skipped_records"] == 1
    assert ensemble.sequence_manifest_filter_stats["skip_reasons"]["review_required"] == 1
