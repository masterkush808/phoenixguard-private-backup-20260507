from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import DefaultDict, Iterator, Literal, Mapping, Sequence, TypeAlias

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR: Path = PROJECT_ROOT / "808 Memory"
DEFAULT_OUT_DIR: Path = PROJECT_ROOT / "data" / "clean_split"
DEFAULT_MANIFEST_PATH: Path = PROJECT_ROOT / "data_splits" / "split_manifest.csv"
DEFAULT_SUMMARY_PATH: Path = PROJECT_ROOT / "data_splits" / "split_summary.json"

IMAGE_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
LABEL_BUY_TOKENS: frozenset[str] = frozenset({"BUY", "BUYS"})
LABEL_SELL_TOKENS: frozenset[str] = frozenset({"SELL", "SELLS"})

Label: TypeAlias = Literal["BUY", "SELL"]
SplitName: TypeAlias = Literal["train", "val", "test"]
SPLITS: tuple[SplitName, SplitName, SplitName] = ("train", "val", "test")


@dataclass(frozen=True, slots=True)
class ImageEntry:
    source_path: Path
    label: Label
    sha256: str
    dhash: str
    capture_date: str | None = None


@dataclass(frozen=True, slots=True)
class SplitStats:
    train_count: int
    val_count: int
    test_count: int
    total_count: int


def sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def dhash_file(path: Path, hash_size: int = 8) -> str:
    if hash_size <= 0:
        raise ValueError("hash_size must be greater than 0.")

    with Image.open(path) as image:
        grayscale = image.convert("L")
        resized = grayscale.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)

    pixels: list[int] = [int(byte) for byte in resized.tobytes()]
    difference_bits: list[str] = []

    row_width: int = hash_size + 1
    for row_index in range(hash_size):
        row_start: int = row_index * row_width
        row_end: int = row_start + row_width
        row_pixels: list[int] = pixels[row_start:row_end]
        for col_index in range(hash_size):
            left_pixel: int = row_pixels[col_index]
            right_pixel: int = row_pixels[col_index + 1]
            difference_bits.append("1" if left_pixel > right_pixel else "0")

    bit_string: str = "".join(difference_bits)
    return f"{int(bit_string, 2):0{hash_size * hash_size // 4}x}"


def hamming_distance_hex(left_hex: str, right_hex: str) -> int:
    left_int: int = int(left_hex, 16)
    right_int: int = int(right_hex, 16)
    xor_value: int = left_int ^ right_int
    return xor_value.bit_count()


_CAPTURE_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?<!\d)(?P<year>20\d{2})[-_. ]+(?P<month>0?[1-9]|1[0-2])"
        r"[-_. ]+(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)"
    ),
    re.compile(
        r"(?<!\d)(?P<year>20\d{2})(?P<month>0[1-9]|1[0-2])"
        r"(?P<day>0[1-9]|[12]\d|3[01])(?!\d)"
    ),
)


def infer_capture_date_from_path(path: Path) -> str | None:
    """Return an explicit capture date from the filename, never filesystem metadata.

    File modification times and archive-directory timestamps commonly describe a copy or
    export operation rather than the chart capture. Restricting this to an unambiguous ISO-like
    date in the filename keeps date grouping conservative.
    """

    for pattern in _CAPTURE_DATE_PATTERNS:
        match = pattern.search(path.stem)
        if match is None:
            continue
        try:
            capture_day = date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            continue
        return capture_day.isoformat()
    return None


def infer_label_from_path(path: Path) -> Label:
    upper_parts: set[str] = {part.upper() for part in path.parts}
    if upper_parts & LABEL_BUY_TOKENS:
        return "BUY"
    if upper_parts & LABEL_SELL_TOKENS:
        return "SELL"

    upper_name: str = path.name.upper()
    if "BUY" in upper_name:
        return "BUY"
    if "SELL" in upper_name:
        return "SELL"

    raise ValueError(
        f"Could not infer label for image: {path}. "
        "Expected BUY/BUYS or SELL/SELLS somewhere in the path."
    )


def iter_image_files(source_dir: Path) -> Iterator[Path]:
    for path in source_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            yield path


def collect_image_entries(source_dir: Path) -> list[ImageEntry]:
    resolved_source_dir: Path = source_dir.resolve()
    if not resolved_source_dir.exists():
        raise FileNotFoundError(f"Source directory does not exist: {resolved_source_dir}")

    entries: list[ImageEntry] = []
    for full_path in iter_image_files(resolved_source_dir):
        label: Label = infer_label_from_path(full_path)
        entry = ImageEntry(
            source_path=full_path.resolve(),
            label=label,
            sha256=sha256_file(full_path),
            dhash=dhash_file(full_path),
            capture_date=infer_capture_date_from_path(full_path),
        )
        entries.append(entry)

    if not entries:
        raise RuntimeError(f"No images found under: {resolved_source_dir}")

    entries.sort(key=lambda item: (str(item.source_path).casefold(), item.label))
    return entries


class DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent: list[int] = list(range(size))
        self.rank: list[int] = [0] * size

    def find(self, index: int) -> int:
        if self.parent[index] != index:
            self.parent[index] = self.find(self.parent[index])
        return self.parent[index]

    def union(self, left: int, right: int) -> None:
        left_root: int = self.find(left)
        right_root: int = self.find(right)

        if left_root == right_root:
            return

        if self.rank[left_root] < self.rank[right_root]:
            self.parent[left_root] = right_root
        elif self.rank[left_root] > self.rank[right_root]:
            self.parent[right_root] = left_root
        else:
            self.parent[right_root] = left_root
            self.rank[left_root] += 1


def dedupe_exact_entries(entries: Sequence[ImageEntry]) -> list[ImageEntry]:
    """Remove only repeated records for the same source path.

    Distinct paths with identical bytes remain manifest members. This is deliberate: dropping
    them would leave raw-suite images absent from the manifest, allowing downstream code to
    assign those unknown images to a split independently. Hash-identical paths are instead
    joined into one global leakage group by :func:`group_entries`.
    """

    unique_by_source: dict[str, ImageEntry] = {}
    for entry in entries:
        key = str(entry.source_path.resolve()).casefold()
        existing = unique_by_source.get(key)
        if existing is None:
            unique_by_source[key] = entry
            continue
        if (
            existing.label != entry.label
            or existing.sha256 != entry.sha256
            or existing.dhash != entry.dhash
        ):
            raise ValueError(f"Conflicting metadata for source image: {entry.source_path}")

    deduped_entries: list[ImageEntry] = list(unique_by_source.values())
    deduped_entries.sort(key=lambda item: (str(item.source_path).casefold(), item.label))
    return deduped_entries


def group_entries(
    entries: Sequence[ImageEntry],
    use_dhash: bool = True,
    dhash_threshold: int = 2,
    use_capture_date: bool = True,
) -> list[list[ImageEntry]]:
    if dhash_threshold < 0:
        raise ValueError("dhash_threshold cannot be negative.")

    deduped_entries: list[ImageEntry] = dedupe_exact_entries(entries)
    if not deduped_entries:
        return []

    # One global graph is essential. Label-local graphs allow the same chart copied under BUY
    # and SELL (or a visually near-identical chart) to leak into different splits.
    disjoint_set = DisjointSet(len(deduped_entries))

    sha_buckets: DefaultDict[str, list[int]] = defaultdict(list)
    capture_date_buckets: DefaultDict[str, list[int]] = defaultdict(list)
    for item_index, entry in enumerate(deduped_entries):
        sha_buckets[entry.sha256].append(item_index)
        capture_date = entry.capture_date or infer_capture_date_from_path(entry.source_path)
        if use_capture_date and capture_date:
            capture_date_buckets[capture_date].append(item_index)

    for bucket in sha_buckets.values():
        representative = bucket[0]
        for item_index in bucket[1:]:
            disjoint_set.union(representative, item_index)

    if use_dhash:
        for left_index, left_entry in enumerate(deduped_entries):
            for right_index in range(left_index + 1, len(deduped_entries)):
                right_entry = deduped_entries[right_index]
                if hamming_distance_hex(left_entry.dhash, right_entry.dhash) <= dhash_threshold:
                    disjoint_set.union(left_index, right_index)

    # Same-day screenshots are often adjacent observations from one capture session. An explicit
    # filename date is therefore an indivisible leakage boundary even when the pixels differ.
    for bucket in capture_date_buckets.values():
        representative = bucket[0]
        for item_index in bucket[1:]:
            disjoint_set.union(representative, item_index)

    components: DefaultDict[int, list[ImageEntry]] = defaultdict(list)
    for item_index, entry in enumerate(deduped_entries):
        components[disjoint_set.find(item_index)].append(entry)

    grouped_entries = list(components.values())
    for group in grouped_entries:
        group.sort(key=lambda item: (str(item.source_path).casefold(), item.label))
    grouped_entries.sort(
        key=lambda group: (
            str(group[0].source_path).casefold(),
            group[0].sha256,
        )
    )
    return grouped_entries


def _group_size(group: Sequence[ImageEntry]) -> int:
    return len(group)


def _split_count_targets(
    total_items: int,
    val_pct: float,
    test_pct: float,
) -> dict[SplitName, int]:
    target_val: int = int(round(total_items * val_pct))
    target_test: int = int(round(total_items * test_pct))
    target_train: int = max(total_items - target_val - target_test, 0)
    return {
        "train": target_train,
        "val": target_val,
        "test": target_test,
    }


def _normalized_count_cost(current: int, target: int) -> float:
    denominator = float(max(target, 1))
    deviation = (float(current) - float(target)) / denominator
    overshoot = max(float(current - target), 0.0) / denominator
    return deviation * deviation + (2.0 * overshoot * overshoot)


def _candidate_assignment_cost(
    candidate: SplitName,
    group_label_counts: Mapping[Label, int],
    group_size: int,
    current_counts: Mapping[SplitName, int],
    current_label_counts: Mapping[SplitName, Mapping[Label, int]],
    target_counts: Mapping[SplitName, int],
    target_label_counts: Mapping[SplitName, Mapping[Label, int]],
) -> float:
    cost = 0.0
    for split_name in SPLITS:
        prospective_total = current_counts[split_name]
        if split_name == candidate:
            prospective_total += group_size
        cost += 0.5 * _normalized_count_cost(prospective_total, target_counts[split_name])

        for label in ("BUY", "SELL"):
            prospective_label_count = current_label_counts[split_name][label]
            if split_name == candidate:
                prospective_label_count += group_label_counts.get(label, 0)
            cost += _normalized_count_cost(
                prospective_label_count,
                target_label_counts[split_name][label],
            )
    return cost


def _validate_explicit_split_map(
    groups: Sequence[list[ImageEntry]],
    split_map: Mapping[int, SplitName],
) -> None:
    invalid_labels: list[tuple[int, str, object]] = []
    for group_index, group in enumerate(groups):
        if not group:
            raise ValueError(f"Group {group_index} is empty and cannot be assigned safely.")
        invalid_labels.extend(
            (group_index, str(entry.source_path), entry.label)
            for entry in group
            if entry.label not in {"BUY", "SELL"}
        )

    if invalid_labels:
        raise ValueError(
            "Unknown image labels cannot be assigned to train implicitly: "
            f"{invalid_labels}"
        )

    expected_indices = set(range(len(groups)))
    assigned_indices = set(split_map)
    missing_indices = sorted(expected_indices - assigned_indices)
    extra_indices = sorted(assigned_indices - expected_indices)
    invalid_assignments = sorted(
        (group_index, split_name)
        for group_index, split_name in split_map.items()
        if split_name not in SPLITS
    )
    if missing_indices or extra_indices or invalid_assignments:
        raise ValueError(
            "Every image group requires one explicit train/val/test assignment; refusing "
            "an implicit train fallback. "
            f"missing={missing_indices}, extra={extra_indices}, invalid={invalid_assignments}"
        )

    source_to_group: dict[str, int] = {}
    for group_index, group in enumerate(groups):
        for entry in group:
            source_key = str(entry.source_path.resolve()).casefold()
            previous_group = source_to_group.setdefault(source_key, group_index)
            if previous_group != group_index:
                raise ValueError(
                    f"Source image appears in multiple groups: {entry.source_path} "
                    f"({previous_group} and {group_index})."
                )


def split_groups(
    groups: Sequence[list[ImageEntry]],
    val_pct: float,
    test_pct: float,
    seed: int = 42,
) -> dict[int, SplitName]:
    if not 0.0 <= val_pct < 1.0:
        raise ValueError("val_pct must be in the range [0.0, 1.0).")
    if not 0.0 <= test_pct < 1.0:
        raise ValueError("test_pct must be in the range [0.0, 1.0).")
    if val_pct + test_pct >= 1.0:
        raise ValueError("val_pct + test_pct must be less than 1.0.")

    for group_index, group in enumerate(groups):
        if not group:
            raise ValueError(f"Group {group_index} is empty and cannot be split.")

    total_count = sum(_group_size(group) for group in groups)
    total_label_counts: Counter[Label] = Counter(
        entry.label for group in groups for entry in group
    )
    target_counts = _split_count_targets(total_count, val_pct, test_pct)
    target_label_counts: dict[SplitName, dict[Label, int]] = {
        split_name: {"BUY": 0, "SELL": 0} for split_name in SPLITS
    }
    for label in ("BUY", "SELL"):
        label_targets = _split_count_targets(total_label_counts[label], val_pct, test_pct)
        for split_name in SPLITS:
            target_label_counts[split_name][label] = label_targets[split_name]

    current_counts: dict[SplitName, int] = {split_name: 0 for split_name in SPLITS}
    current_label_counts: dict[SplitName, dict[Label, int]] = {
        split_name: {"BUY": 0, "SELL": 0} for split_name in SPLITS
    }
    group_indices = list(range(len(groups)))
    random.Random(seed).shuffle(group_indices)
    group_indices.sort(key=lambda index: _group_size(groups[index]), reverse=True)

    split_map: dict[int, SplitName] = {}
    for group_index in group_indices:
        group = groups[group_index]
        group_label_counts: Counter[Label] = Counter(entry.label for entry in group)
        candidate_costs = {
            split_name: _candidate_assignment_cost(
                candidate=split_name,
                group_label_counts=group_label_counts,
                group_size=len(group),
                current_counts=current_counts,
                current_label_counts=current_label_counts,
                target_counts=target_counts,
                target_label_counts=target_label_counts,
            )
            for split_name in SPLITS
        }
        selected_split = min(SPLITS, key=lambda split_name: candidate_costs[split_name])
        split_map[group_index] = selected_split
        current_counts[selected_split] += len(group)
        for label in ("BUY", "SELL"):
            current_label_counts[selected_split][label] += group_label_counts[label]

    _validate_explicit_split_map(groups, split_map)
    return split_map


def _reset_output_dirs(out_dir: Path) -> None:
    for split_name in SPLITS:
        split_path: Path = out_dir / split_name
        if split_path.exists():
            shutil.rmtree(split_path)
    out_dir.mkdir(parents=True, exist_ok=True)


def _build_destination_name(group_index: int, entry_index: int, entry: ImageEntry) -> str:
    safe_suffix: str = entry.source_path.suffix.lower()
    stem_hash: str = entry.sha256[:12]
    return f"{group_index:05d}_{entry_index:03d}_{stem_hash}{safe_suffix}"


def copy_and_manifest(
    groups: Sequence[list[ImageEntry]],
    split_map: Mapping[int, SplitName],
    out_dir: Path,
    manifest_path: Path,
) -> dict[str, int]:
    resolved_out_dir: Path = out_dir.resolve()
    resolved_manifest_path: Path = manifest_path.resolve()

    _validate_explicit_split_map(groups, split_map)
    _reset_output_dirs(resolved_out_dir)
    resolved_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    total_written: int = 0
    counts_by_split: dict[str, int] = {"train": 0, "val": 0, "test": 0}

    fieldnames: list[str] = [
        "split",
        "label",
        "group_index",
        "entry_index",
        "source_path",
        "destination_path",
        "sha256",
        "dhash",
        "capture_date",
        "is_group_representative",
    ]

    with resolved_manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.DictWriter(manifest_file, fieldnames=fieldnames)
        writer.writeheader()

        for group_index, group in enumerate(groups):
            split_name: SplitName = split_map[group_index]
            for entry_index, entry in enumerate(group):
                destination_dir: Path = resolved_out_dir / split_name / entry.label
                destination_dir.mkdir(parents=True, exist_ok=True)

                destination_name: str = _build_destination_name(group_index, entry_index, entry)
                destination_path: Path = destination_dir / destination_name

                shutil.copy2(entry.source_path, destination_path)

                writer.writerow(
                    {
                        "split": split_name,
                        "label": entry.label,
                        "group_index": str(group_index),
                        "entry_index": str(entry_index),
                        "source_path": str(entry.source_path),
                        "destination_path": str(destination_path),
                        "sha256": entry.sha256,
                        "dhash": entry.dhash,
                        "capture_date": entry.capture_date
                        or infer_capture_date_from_path(entry.source_path)
                        or "",
                        "is_group_representative": "1" if entry_index == 0 else "0",
                    }
                )

                counts_by_split[split_name] += 1
                total_written += 1

    counts_by_split["total"] = total_written
    return counts_by_split


def build_summary(
    entries: Sequence[ImageEntry],
    groups: Sequence[list[ImageEntry]],
    split_map: Mapping[int, SplitName],
    source_dir: Path,
    out_dir: Path,
    manifest_path: Path,
    use_dhash: bool,
    dhash_threshold: int,
    val_pct: float,
    test_pct: float,
    seed: int,
    use_capture_date: bool = True,
) -> dict[str, object]:
    _validate_explicit_split_map(groups, split_map)
    deduped_count: int = sum(len(group) for group in groups)
    duplicate_removed_count: int = len(entries) - deduped_count

    counts: dict[SplitName, int] = {"train": 0, "val": 0, "test": 0}
    group_counts: dict[SplitName, int] = {"train": 0, "val": 0, "test": 0}

    for group_index, group in enumerate(groups):
        split_name: SplitName = split_map[group_index]
        counts[split_name] += len(group)
        group_counts[split_name] += 1

    buy_count: int = sum(1 for entry in entries if entry.label == "BUY")
    sell_count: int = sum(1 for entry in entries if entry.label == "SELL")
    sha_counts = Counter(entry.sha256 for entry in entries)
    exact_duplicate_member_count = sum(count - 1 for count in sha_counts.values() if count > 1)
    labels_by_sha: DefaultDict[str, set[Label]] = defaultdict(set)
    capture_date_counts: Counter[str] = Counter()
    for entry in entries:
        labels_by_sha[entry.sha256].add(entry.label)
        capture_date = entry.capture_date or infer_capture_date_from_path(entry.source_path)
        if capture_date:
            capture_date_counts[capture_date] += 1
    conflicting_label_hash_count = sum(
        1 for labels in labels_by_sha.values() if len(labels) > 1
    )
    mixed_label_group_count = sum(
        1 for group in groups if len({entry.label for entry in group}) > 1
    )

    summary: dict[str, object] = {
        "project_root": str(PROJECT_ROOT),
        "source_dir": str(source_dir.resolve()),
        "out_dir": str(out_dir.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "raw_entry_count": len(entries),
        "raw_buy_count": buy_count,
        "raw_sell_count": sell_count,
        "deduped_entry_count": deduped_count,
        "duplicate_removed_count": duplicate_removed_count,
        "exact_duplicate_member_count": exact_duplicate_member_count,
        "conflicting_label_hash_count": conflicting_label_hash_count,
        "mixed_label_group_count": mixed_label_group_count,
        "multi_image_capture_date_count": sum(
            1 for count in capture_date_counts.values() if count > 1
        ),
        "group_count": len(groups),
        "train_count": counts["train"],
        "val_count": counts["val"],
        "test_count": counts["test"],
        "train_group_count": group_counts["train"],
        "val_group_count": group_counts["val"],
        "test_group_count": group_counts["test"],
        "use_dhash": use_dhash,
        "dhash_threshold": dhash_threshold,
        "use_capture_date": use_capture_date,
        "manifest_policy": "all_distinct_source_paths_explicitly_assigned",
        "val_pct": val_pct,
        "test_pct": test_pct,
        "seed": seed,
    }
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a leakage-safe clean train/val/test split for PhoenixGuard chart images."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Source directory containing the raw 808 Memory images.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Output directory where train/val/test folders will be created.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="CSV manifest path.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=DEFAULT_SUMMARY_PATH,
        help="JSON summary path.",
    )
    parser.add_argument(
        "--val-pct",
        type=float,
        default=0.15,
        help="Validation percentage.",
    )
    parser.add_argument(
        "--test-pct",
        type=float,
        default=0.15,
        help="Test percentage.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic grouped splitting.",
    )
    parser.add_argument(
        "--use-dhash",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable or disable dHash-based near-duplicate grouping.",
    )
    parser.add_argument(
        "--dhash-threshold",
        type=int,
        default=2,
        help="Maximum dHash Hamming distance for grouping near-duplicates.",
    )
    parser.add_argument(
        "--group-capture-date",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep images with the same explicit filename capture date in one split.",
    )
    return parser.parse_args()


def main() -> None:
    args: argparse.Namespace = parse_args()

    source_dir: Path = args.source_dir
    out_dir: Path = args.out_dir
    manifest_path: Path = args.manifest_path
    summary_path: Path = args.summary_path
    val_pct: float = args.val_pct
    test_pct: float = args.test_pct
    seed: int = args.seed
    use_dhash: bool = args.use_dhash
    dhash_threshold: int = args.dhash_threshold
    use_capture_date: bool = args.group_capture_date

    entries: list[ImageEntry] = collect_image_entries(source_dir)
    groups: list[list[ImageEntry]] = group_entries(
        entries=entries,
        use_dhash=use_dhash,
        dhash_threshold=dhash_threshold,
        use_capture_date=use_capture_date,
    )
    split_map: dict[int, SplitName] = split_groups(
        groups=groups,
        val_pct=val_pct,
        test_pct=test_pct,
        seed=seed,
    )

    copy_stats: dict[str, int] = copy_and_manifest(
        groups=groups,
        split_map=split_map,
        out_dir=out_dir,
        manifest_path=manifest_path,
    )

    summary: dict[str, object] = build_summary(
        entries=entries,
        groups=groups,
        split_map=split_map,
        source_dir=source_dir,
        out_dir=out_dir,
        manifest_path=manifest_path,
        use_dhash=use_dhash,
        dhash_threshold=dhash_threshold,
        val_pct=val_pct,
        test_pct=test_pct,
        seed=seed,
        use_capture_date=use_capture_date,
    )
    summary["written_train_count"] = copy_stats["train"]
    summary["written_val_count"] = copy_stats["val"]
    summary["written_test_count"] = copy_stats["test"]
    summary["written_total_count"] = copy_stats["total"]

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("=" * 80)
    print("[BUILD CLEAN SPLIT] COMPLETE")
    print("=" * 80)
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Source dir   : {source_dir.resolve()}")
    print(f"Output dir   : {out_dir.resolve()}")
    print(f"Manifest     : {manifest_path.resolve()}")
    print(f"Summary      : {summary_path.resolve()}")
    print(f"Raw images   : {summary['raw_entry_count']}")
    print(f"Manifest imgs: {summary['deduped_entry_count']}")
    print(f"Groups       : {summary['group_count']}")
    print(f"Train count  : {summary['train_count']}")
    print(f"Val count    : {summary['val_count']}")
    print(f"Test count   : {summary['test_count']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
