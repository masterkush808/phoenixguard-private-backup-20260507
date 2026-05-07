from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Iterator, Literal, Mapping, Sequence, TypeAlias

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR: Path = PROJECT_ROOT / "808 Memory"
DEFAULT_OUT_DIR: Path = PROJECT_ROOT / "data_splits"
DEFAULT_MANIFEST_PATH: Path = DEFAULT_OUT_DIR / "split_manifest.csv"
DEFAULT_SUMMARY_PATH: Path = DEFAULT_OUT_DIR / "split_summary.json"

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
        )
        entries.append(entry)

    if not entries:
        raise RuntimeError(f"No images found under: {resolved_source_dir}")

    entries.sort(key=lambda item: (item.label, str(item.source_path)))
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
    unique_by_hash: dict[tuple[Label, str], ImageEntry] = {}
    for entry in entries:
        key: tuple[Label, str] = (entry.label, entry.sha256)
        if key not in unique_by_hash:
            unique_by_hash[key] = entry

    deduped_entries: list[ImageEntry] = list(unique_by_hash.values())
    deduped_entries.sort(key=lambda item: (item.label, str(item.source_path)))
    return deduped_entries


def group_entries(
    entries: Sequence[ImageEntry],
    use_dhash: bool = True,
    dhash_threshold: int = 2,
) -> list[list[ImageEntry]]:
    if dhash_threshold < 0:
        raise ValueError("dhash_threshold cannot be negative.")

    deduped_entries: list[ImageEntry] = dedupe_exact_entries(entries)
    if not use_dhash:
        return [[entry] for entry in deduped_entries]

    label_buckets: DefaultDict[Label, list[ImageEntry]] = defaultdict(list)
    for entry in deduped_entries:
        label_buckets[entry.label].append(entry)

    grouped_entries: list[list[ImageEntry]] = []

    for label in ("BUY", "SELL"):
        bucket: list[ImageEntry] = label_buckets.get(label, [])
        if not bucket:
            continue

        disjoint_set = DisjointSet(len(bucket))
        for left_index in range(len(bucket)):
            left_entry: ImageEntry = bucket[left_index]
            for right_index in range(left_index + 1, len(bucket)):
                right_entry: ImageEntry = bucket[right_index]
                if hamming_distance_hex(left_entry.dhash, right_entry.dhash) <= dhash_threshold:
                    disjoint_set.union(left_index, right_index)

        components: DefaultDict[int, list[ImageEntry]] = defaultdict(list)
        for item_index, entry in enumerate(bucket):
            root_index: int = disjoint_set.find(item_index)
            components[root_index].append(entry)

        label_groups: list[list[ImageEntry]] = list(components.values())
        for group in label_groups:
            group.sort(key=lambda item: str(item.source_path))

        label_groups.sort(key=lambda group: str(group[0].source_path))
        grouped_entries.extend(label_groups)

    return grouped_entries


def _empty_split_map() -> dict[int, SplitName]:
    return {}


def _label_group_indices(
    groups: Sequence[list[ImageEntry]],
) -> dict[Label, list[int]]:
    mapping: dict[Label, list[int]] = {"BUY": [], "SELL": []}
    for index, group in enumerate(groups):
        if not group:
            continue
        mapping[group[0].label].append(index)
    return mapping


def _group_size(group: Sequence[ImageEntry]) -> int:
    return len(group)


def _assign_groups_for_label(
    group_indices: Sequence[int],
    groups: Sequence[list[ImageEntry]],
    val_pct: float,
    test_pct: float,
    seed: int,
) -> dict[int, SplitName]:
    split_map: dict[int, SplitName] = {}

    local_indices: list[int] = list(group_indices)
    rng = random.Random(seed)
    rng.shuffle(local_indices)

    local_indices.sort(key=lambda idx: len(groups[idx]), reverse=True)

    total_items: int = sum(_group_size(groups[idx]) for idx in local_indices)
    target_val: int = int(round(total_items * val_pct))
    target_test: int = int(round(total_items * test_pct))
    target_train: int = max(total_items - target_val - target_test, 0)

    target_counts: dict[SplitName, int] = {
        "train": target_train,
        "val": target_val,
        "test": target_test,
    }
    current_counts: dict[SplitName, int] = {
        "train": 0,
        "val": 0,
        "test": 0,
    }

    for group_index in local_indices:
        group_len: int = _group_size(groups[group_index])

        best_split: SplitName = "train"
        best_score: float | None = None

        for split_name in SPLITS:
            target: int = target_counts[split_name]
            current: int = current_counts[split_name]

            if target <= 0:
                score: float = float("-inf")
            else:
                remaining_before: int = target - current
                remaining_after: int = target - (current + group_len)
                score = float(remaining_before) - abs(float(remaining_after))

            if best_score is None or score > best_score:
                best_score = score
                best_split = split_name

        if best_score == float("-inf"):
            best_split = "train"

        split_map[group_index] = best_split
        current_counts[best_split] += group_len

    return split_map


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

    split_map: dict[int, SplitName] = _empty_split_map()
    grouped_indices: dict[Label, list[int]] = _label_group_indices(groups)

    for label in ("BUY", "SELL"):
        label_split_map: dict[int, SplitName] = _assign_groups_for_label(
            group_indices=grouped_indices[label],
            groups=groups,
            val_pct=val_pct,
            test_pct=test_pct,
            seed=seed + (0 if label == "BUY" else 100_000),
        )
        split_map.update(label_split_map)

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
) -> dict[str, object]:
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
        "group_count": len(groups),
        "train_count": counts["train"],
        "val_count": counts["val"],
        "test_count": counts["test"],
        "train_group_count": group_counts["train"],
        "val_group_count": group_counts["val"],
        "test_group_count": group_counts["test"],
        "use_dhash": use_dhash,
        "dhash_threshold": dhash_threshold,
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

    entries: list[ImageEntry] = collect_image_entries(source_dir)
    groups: list[list[ImageEntry]] = group_entries(
        entries=entries,
        use_dhash=use_dhash,
        dhash_threshold=dhash_threshold,
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
    print(f"Deduped imgs : {summary['deduped_entry_count']}")
    print(f"Groups       : {summary['group_count']}")
    print(f"Train count  : {summary['train_count']}")
    print(f"Val count    : {summary['val_count']}")
    print(f"Test count   : {summary['test_count']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
