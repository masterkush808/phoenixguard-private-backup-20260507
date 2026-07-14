from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from Developer.datasets.build_clean_split import (
    ImageEntry,
    collect_image_entries,
    copy_and_manifest,
    group_entries,
    infer_capture_date_from_path,
    split_groups,
)


def _write_pattern(path: Path, variant: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (48, 32), color=(15 + variant, 20, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((4 + variant, 5, 18 + variant, 27), fill=(20, 210, 90))
    draw.line((25, 2 + variant, 42, 29 - variant), fill=(220, 40, 70), width=3)
    image.save(path)


def test_identical_cross_label_images_share_one_group_and_split(tmp_path: Path) -> None:
    source_dir = tmp_path / "raw"
    buy_path = source_dir / "BUY" / "same-chart.png"
    sell_path = source_dir / "SELL" / "same-chart-copy.png"
    _write_pattern(buy_path, variant=0)
    sell_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(buy_path, sell_path)

    entries = collect_image_entries(source_dir)
    groups = group_entries(
        entries,
        use_dhash=False,
        use_capture_date=False,
    )

    assert len(entries) == 2
    assert len(groups) == 1
    assert {entry.label for entry in groups[0]} == {"BUY", "SELL"}

    split_map = split_groups(groups, val_pct=0.2, test_pct=0.2, seed=73)
    out_dir = tmp_path / "split"
    manifest_path = out_dir / "split_manifest.csv"
    copy_and_manifest(groups, split_map, out_dir, manifest_path)

    with manifest_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert {row["split"] for row in rows} == {split_map[0]}
    assert {row["group_index"] for row in rows} == {"0"}
    assert {row["label"] for row in rows} == {"BUY", "SELL"}
    assert all(Path(row["destination_path"]).is_file() for row in rows)


def test_capture_date_group_is_indivisible_and_assignment_is_deterministic(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "raw"
    first = source_dir / "BUY" / "Screenshot 2024-08-08 141048.png"
    second = source_dir / "SELL" / "Screenshot_20240808_181500.png"
    other = source_dir / "BUY" / "Screenshot 2024-08-09 090000.png"
    _write_pattern(first, variant=0)
    _write_pattern(second, variant=3)
    _write_pattern(other, variant=6)

    entries = collect_image_entries(source_dir)
    groups = group_entries(entries, use_dhash=False, use_capture_date=True)
    source_group = {
        entry.source_path.name: group_index
        for group_index, group in enumerate(groups)
        for entry in group
    }

    assert infer_capture_date_from_path(first) == "2024-08-08"
    assert infer_capture_date_from_path(second) == "2024-08-08"
    assert source_group[first.name] == source_group[second.name]
    assert source_group[first.name] != source_group[other.name]

    first_assignment = split_groups(groups, val_pct=0.2, test_pct=0.2, seed=91)
    second_assignment = split_groups(groups, val_pct=0.2, test_pct=0.2, seed=91)
    assert first_assignment == second_assignment
    assert first_assignment[source_group[first.name]] == first_assignment[source_group[second.name]]


def test_perceptual_grouping_is_global_across_labels(tmp_path: Path) -> None:
    entries = [
        ImageEntry(tmp_path / "BUY" / "a.png", "BUY", "a" * 64, "0000000000000000"),
        ImageEntry(tmp_path / "SELL" / "b.png", "SELL", "b" * 64, "0000000000000001"),
    ]

    groups = group_entries(
        entries,
        use_dhash=True,
        dhash_threshold=1,
        use_capture_date=False,
    )

    assert len(groups) == 1
    assert {entry.label for entry in groups[0]} == {"BUY", "SELL"}


def test_unassigned_group_fails_before_existing_outputs_are_removed(tmp_path: Path) -> None:
    source = tmp_path / "BUY" / "chart.png"
    _write_pattern(source, variant=1)
    entries = collect_image_entries(tmp_path)
    groups = group_entries(entries, use_dhash=False, use_capture_date=False)
    out_dir = tmp_path / "split"
    sentinel = out_dir / "train" / "keep.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing an implicit train fallback"):
        copy_and_manifest(groups, {}, out_dir, out_dir / "split_manifest.csv")

    assert sentinel.read_text(encoding="utf-8") == "preserve"
