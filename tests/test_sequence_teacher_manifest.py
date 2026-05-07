from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.build_sequence_teacher_manifest import (
    build_teacher_task_labels,
    export_teacher_review_queue,
    normalize_teacher_manifest_file,
    normalize_teacher_manifest_record,
    teacher_manifest_metadata_path,
    validate_directional_teacher_consistency,
    validate_teacher_manifest,
)


def _write_split_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as manifest_file:
        writer = csv.DictWriter(
            manifest_file,
            fieldnames=["source_path", "destination_path", "label", "split"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_teacher_manifest(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as manifest_file:
        for record in records:
            manifest_file.write(json.dumps(record, ensure_ascii=True) + "\n")


def test_validate_teacher_manifest_accepts_matching_metadata(tmp_path: Path) -> None:
    split_manifest = tmp_path / "split_manifest.csv"
    teacher_manifest = tmp_path / "sequence_teacher_manifest.jsonl"

    rows = [
        {
            "source_path": "src_a.png",
            "destination_path": "train/BUY/a.png",
            "label": "BUY",
            "split": "train",
        },
        {
            "source_path": "src_b.png",
            "destination_path": "train/SELL/b.png",
            "label": "SELL",
            "split": "train",
        },
    ]
    _write_split_manifest(split_manifest, rows)
    _write_teacher_manifest(
        teacher_manifest,
        [
            {"image_path": "train/BUY/a.png", "sequence_targets": {"projection_direction": "BUY", "next_box_type": "impulse"}},
            {"image_path": "train/SELL/b.png", "sequence_targets": {"projection_direction": "SELL", "next_box_type": "pullback"}},
        ],
    )

    split_stat = split_manifest.stat()
    metadata = {
        "split_manifest_path": str(split_manifest.resolve()),
        "split_manifest_size": int(split_stat.st_size),
        "split_manifest_mtime_ns": int(split_stat.st_mtime_ns),
        "expected_rows": 2,
        "written_rows": 2,
        "error_count": 0,
    }
    teacher_manifest_metadata_path(teacher_manifest).write_text(
        json.dumps(metadata, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    summary = validate_teacher_manifest(
        split_manifest_path=split_manifest,
        manifest_path=teacher_manifest,
    )
    assert summary["record_count"] == 2
    assert summary["metadata_present"] is True


def test_validate_teacher_manifest_rejects_partial_manifest(tmp_path: Path) -> None:
    split_manifest = tmp_path / "split_manifest.csv"
    teacher_manifest = tmp_path / "sequence_teacher_manifest.jsonl"

    _write_split_manifest(
        split_manifest,
        [
            {
                "source_path": "src_a.png",
                "destination_path": "train/BUY/a.png",
                "label": "BUY",
                "split": "train",
            },
            {
                "source_path": "src_b.png",
                "destination_path": "train/SELL/b.png",
                "label": "SELL",
                "split": "train",
            },
        ],
    )
    _write_teacher_manifest(
        teacher_manifest,
        [
            {"image_path": "train/BUY/a.png", "sequence_targets": {"projection_direction": "BUY"}},
        ],
    )

    with pytest.raises(RuntimeError, match="row count mismatch"):
        validate_teacher_manifest(
            split_manifest_path=split_manifest,
            manifest_path=teacher_manifest,
        )


def test_validate_teacher_manifest_rejects_error_rows(tmp_path: Path) -> None:
    split_manifest = tmp_path / "split_manifest.csv"
    teacher_manifest = tmp_path / "sequence_teacher_manifest.jsonl"

    _write_split_manifest(
        split_manifest,
        [
            {
                "source_path": "src_a.png",
                "destination_path": "train/BUY/a.png",
                "label": "BUY",
                "split": "train",
            },
        ],
    )
    _write_teacher_manifest(
        teacher_manifest,
        [
            {
                "image_path": "train/BUY/a.png",
                "sequence_targets": {},
                "error": "synthetic failure",
            },
        ],
    )

    with pytest.raises(RuntimeError, match="contains 1 error rows"):
        validate_teacher_manifest(
            split_manifest_path=split_manifest,
            manifest_path=teacher_manifest,
        )


def test_validate_directional_teacher_consistency_rejects_skewed_buy_targets() -> None:
    records = []
    for _ in range(12):
        records.append(
            {
                "label": "BUY",
                "sequence_targets": {
                    "projection_direction": "SELL",
                    "next_box_direction": "SELL",
                },
            }
        )
    for _ in range(12):
        records.append(
            {
                "label": "SELL",
                "sequence_targets": {
                    "projection_direction": "SELL",
                    "next_box_direction": "SELL",
                },
            }
        )

    with pytest.raises(RuntimeError, match="contradict the label distribution too often"):
        validate_directional_teacher_consistency(records)


def test_normalize_teacher_manifest_record_aligns_directional_targets_to_label() -> None:
    record = {
        "label": "BUY",
        "projection": {"direction": "SELL", "confidence": 0.72, "dominance": 0.14},
        "next_box": {"direction": "SELL", "confidence": 0.77},
        "action": "HOLD",
        "confidence": 0.22,
        "sequence_targets": {
            "projection_direction": "SELL",
            "next_box_direction": "SELL",
            "current_box_direction": "SELL",
            "macro_trend": "BUY",
        },
    }

    normalized = normalize_teacher_manifest_record(record)

    assert normalized["sequence_targets"]["projection_direction"] == "BUY"
    assert normalized["sequence_targets"]["next_box_direction"] == "BUY"
    assert normalized["sequence_targets"]["current_box_direction"] == "SELL"
    assert normalized["raw_sequence_targets"]["projection_direction"] == "SELL"
    assert normalized["raw_sequence_targets"]["next_box_direction"] == "SELL"
    assert normalized["teacher_target_adjustments"] == {
        "projection_direction": {
            "from": "SELL",
            "to": "BUY",
            "reason": "align_with_trade_label",
        },
        "next_box_direction": {
            "from": "SELL",
            "to": "BUY",
            "reason": "align_with_trade_label",
        },
    }
    assert normalized["teacher_task_labels"]["review_required"] is True
    assert normalized["teacher_task_labels"]["review_bucket"] == "projection_conflict"
    assert normalized["teacher_task_labels"]["label_quality"] in {"contradictory", "review_required"}

    renormalized = normalize_teacher_manifest_record(normalized)
    assert renormalized["teacher_target_adjustments"] == normalized["teacher_target_adjustments"]
    assert renormalized["raw_sequence_targets"] == normalized["raw_sequence_targets"]


def test_normalize_teacher_manifest_file_rewrites_rows_and_metadata(tmp_path: Path) -> None:
    teacher_manifest = tmp_path / "sequence_teacher_manifest.jsonl"
    _write_teacher_manifest(
        teacher_manifest,
        [
            {
                "image_path": "train/BUY/a.png",
                "label": "BUY",
                "sequence_targets": {
                    "projection_direction": "SELL",
                    "next_box_direction": "SELL",
                    "current_box_direction": "SELL",
                },
            },
            {
                "image_path": "train/SELL/b.png",
                "label": "SELL",
                "sequence_targets": {
                    "projection_direction": "SELL",
                    "next_box_direction": "SELL",
                },
            },
        ],
    )
    teacher_manifest_metadata_path(teacher_manifest).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "expected_rows": 2,
                "written_rows": 2,
                "error_count": 0,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    stats = normalize_teacher_manifest_file(teacher_manifest)

    assert stats == {
        "record_count": 2,
        "adjusted_record_count": 1,
        "adjusted_target_count": 2,
        "metadata_updated": True,
    }

    repaired_records = []
    with teacher_manifest.open("r", encoding="utf-8") as manifest_file:
        for raw_line in manifest_file:
            repaired_records.append(json.loads(raw_line))

    assert repaired_records[0]["sequence_targets"]["projection_direction"] == "BUY"
    assert repaired_records[0]["sequence_targets"]["next_box_direction"] == "BUY"
    assert repaired_records[0]["sequence_targets"]["current_box_direction"] == "SELL"
    assert repaired_records[0]["raw_sequence_targets"]["projection_direction"] == "SELL"
    assert repaired_records[1]["sequence_targets"]["projection_direction"] == "SELL"
    metadata = json.loads(teacher_manifest_metadata_path(teacher_manifest).read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 3
    assert metadata["normalized_directional_targets"] is True
    assert metadata["normalized_adjusted_record_count"] == 1
    assert metadata["normalized_adjusted_target_count"] == 2
    assert metadata["teacher_task_label_summary"]["review_required"]["true"] >= 1


def test_build_teacher_task_labels_marks_hard_negative_rows() -> None:
    task_labels = build_teacher_task_labels(
        {
            "label": "BUY",
            "action": "SELL",
            "confidence": 0.66,
            "decision_state": "PROJECTED",
            "execution_permission": "WAIT_FOR_CONFIRMATION",
            "projection": {"direction": "SELL", "confidence": 0.81, "dominance": 0.24},
            "next_box": {"direction": "SELL", "confidence": 0.84},
            "chart_state": {"structure_setup": "impulse_chain"},
            "sequence_targets": {"projection_direction": "BUY", "next_box_direction": "BUY"},
            "raw_sequence_targets": {"projection_direction": "SELL", "next_box_direction": "SELL"},
        }
    )

    assert task_labels["review_required"] is True
    assert task_labels["review_bucket"] == "hard_negative"
    assert task_labels["label_quality"] == "contradictory"
    assert task_labels["timing_label"] == "projected"
    assert task_labels["review_priority"] > 0.5


def test_export_teacher_review_queue_writes_prioritized_rows(tmp_path: Path) -> None:
    teacher_manifest = tmp_path / "sequence_teacher_manifest.jsonl"
    review_output = tmp_path / "teacher_review.csv"
    _write_teacher_manifest(
        teacher_manifest,
        [
            normalize_teacher_manifest_record(
                {
                    "image_path": "train/BUY/a.png",
                    "source_path": "src_a.png",
                    "label": "BUY",
                    "split": "train",
                    "action": "SELL",
                    "confidence": 0.64,
                    "decision_state": "PROJECTED",
                    "execution_permission": "WAIT_FOR_CONFIRMATION",
                    "projection": {"direction": "SELL", "confidence": 0.79, "dominance": 0.20},
                    "next_box": {"direction": "SELL", "confidence": 0.82},
                    "chart_state": {"structure_setup": "impulse_chain"},
                    "sequence_targets": {"projection_direction": "SELL", "next_box_direction": "SELL"},
                }
            ),
            normalize_teacher_manifest_record(
                {
                    "image_path": "train/SELL/b.png",
                    "source_path": "src_b.png",
                    "label": "SELL",
                    "split": "train",
                    "action": "HOLD",
                    "confidence": 0.22,
                    "decision_state": "UNCERTAIN",
                    "execution_permission": "WAIT_FOR_CONFIRMATION",
                    "projection": {"direction": "SELL", "confidence": 0.58, "dominance": 0.05},
                    "next_box": {"direction": "SELL", "confidence": 0.62},
                    "chart_state": {"structure_setup": "none"},
                    "sequence_targets": {"projection_direction": "SELL", "next_box_direction": "SELL"},
                }
            ),
        ],
    )

    stats = export_teacher_review_queue(
        manifest_path=teacher_manifest,
        output_path=review_output,
        min_priority=0.0,
    )

    assert stats["review_row_count"] == 2
    with review_output.open("r", encoding="utf-8", newline="") as review_file:
        rows = list(csv.DictReader(review_file))

    assert rows[0]["trade_label"] == "BUY"
    assert rows[0]["review_bucket"] == "hard_negative"
    assert rows[0]["raw_projection_direction"] == "SELL"
    assert float(rows[0]["review_priority"]) >= float(rows[1]["review_priority"])
