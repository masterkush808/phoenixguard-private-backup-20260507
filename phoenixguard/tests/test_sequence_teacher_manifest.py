from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.build_sequence_teacher_manifest import (
    teacher_manifest_metadata_path,
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
