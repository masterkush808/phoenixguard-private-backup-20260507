from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping, cast

from PIL import Image

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.training.ensemble_cv_models import ChartImageDataset
from Developer.model_training.train_sequence_aware_all import audit_alias_split_dirs, resolve_split_dirs


def _as_int(value: object) -> int:
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"expected int-compatible value, got {type(value).__name__}")


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(128, 128, 128)).save(path)


def test_resolve_split_dirs_prefers_canonical_manifest_dirs(tmp_path: Path) -> None:
    clean_split_root = tmp_path / "clean_split"
    for split_name in ("train", "val"):
        for label in ("BUY", "BUYS", "SELL", "SELLS"):
            (clean_split_root / split_name / label).mkdir(parents=True, exist_ok=True)

    train_dirs, val_dirs = resolve_split_dirs(clean_split_root=clean_split_root)

    assert [Path(path).name for path in train_dirs] == ["BUY", "SELL"]
    assert [Path(path).name for path in val_dirs] == ["BUY", "SELL"]


def test_chart_image_dataset_normalizes_buys_and_sells_to_binary_labels(tmp_path: Path) -> None:
    buy_dir = tmp_path / "BUY"
    buys_dir = tmp_path / "BUYS"
    sell_dir = tmp_path / "SELL"
    sells_dir = tmp_path / "SELLS"
    _write_png(buy_dir / "buy.png")
    _write_png(buys_dir / "buys.png")
    _write_png(sell_dir / "sell.png")
    _write_png(sells_dir / "sells.png")

    dataset = ChartImageDataset(
        [str(buy_dir), str(buys_dir), str(sell_dir), str(sells_dir)],
        transform=None,
    )

    label_by_dir = {
        Path(sample).parent.name: int(label)
        for sample, label in zip(dataset.samples, dataset.labels)
    }

    assert label_by_dir["BUY"] == 0
    assert label_by_dir["BUYS"] == 0
    assert label_by_dir["SELL"] == 1
    assert label_by_dir["SELLS"] == 1


def test_alias_split_audit_detects_shadow_overlap(tmp_path: Path) -> None:
    clean_split_root = tmp_path / "clean_split"
    canonical_train_buy = clean_split_root / "train" / "BUY" / "canon.png"
    alias_val_buys = clean_split_root / "val" / "BUYS" / "alias.png"
    _write_png(canonical_train_buy)
    alias_val_buys.parent.mkdir(parents=True, exist_ok=True)
    alias_val_buys.write_bytes(canonical_train_buy.read_bytes())

    audit = audit_alias_split_dirs(clean_split_root=clean_split_root)
    alias_counts = cast(Mapping[str, object], audit["alias_counts"])
    cross_split_overlap = cast(Mapping[str, Mapping[str, object]], audit["cross_split_overlap"])

    assert bool(audit["has_shadow_aliases"]) is True
    assert _as_int(alias_counts["BUYS"]) == 1
    assert _as_int(cross_split_overlap["BUYS"]["train->val"]) == 1
