from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import torch
from phoenixguard.paths import PROJECT_ROOT
from phoenixguard.training.ensemble_cv_models import EnsembleCVModels, TRAIN_CONFIGS  # type: ignore[import]

from Developer.sequence_teacher.build_sequence_teacher_manifest import build_teacher_manifest, validate_teacher_manifest

DEFAULT_SAVE_DIR = PROJECT_ROOT / "models"
DEFAULT_SPLIT_MANIFEST = PROJECT_ROOT / "data_splits" / "split_manifest.csv"
DEFAULT_SEQUENCE_MANIFEST = PROJECT_ROOT / "data_splits" / "sequence_teacher_manifest.jsonl"

MODEL_ORDER = [
    "mobilenetv3",
    "dinov2",
    "simclr",
    "byol",
    "swav",
    "clip",
]

EPOCHS_PER_MODEL = {
    "mobilenetv3": 20,
    "dinov2": 12,
    "simclr": 14,
    "byol": 14,
    "swav": 14,
    "clip": 10,
}


def cleanup_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def _canonical_label_dir(split_name: str, canonical_label: str, *, clean_split_root: Path | None = None) -> str:
    root = clean_split_root or (PROJECT_ROOT / "data" / "clean_split")
    split_root = root / split_name
    preferred = split_root / canonical_label
    if preferred.exists():
        return str(preferred)
    legacy = split_root / f"{canonical_label}S"
    if legacy.exists():
        return str(legacy)
    raise FileNotFoundError(
        f"Could not find clean split directory for {split_name}/{canonical_label} "
        f"under {split_root}"
    )


def resolve_split_dirs(*, clean_split_root: Path | None = None) -> tuple[list[str], list[str]]:
    train_dirs = [
        _canonical_label_dir("train", "BUY", clean_split_root=clean_split_root),
        _canonical_label_dir("train", "SELL", clean_split_root=clean_split_root),
    ]
    val_dirs = [
        _canonical_label_dir("val", "BUY", clean_split_root=clean_split_root),
        _canonical_label_dir("val", "SELL", clean_split_root=clean_split_root),
    ]
    return train_dirs, val_dirs


def audit_alias_split_dirs(*, clean_split_root: Path | None = None) -> dict[str, object]:
    root = clean_split_root or (PROJECT_ROOT / "data" / "clean_split")
    alias_map = {"BUY": "BUYS", "SELL": "SELLS"}
    split_names = ("train", "val", "test")
    alias_counts: dict[str, int] = {}
    cross_split_overlap: dict[str, dict[str, int]] = {}

    for canonical_label, alias_label in alias_map.items():
        canonical_hashes: dict[str, set[str]] = {}
        alias_hashes: dict[str, set[str]] = {}
        alias_total = 0
        for split_name in split_names:
            canonical_dir = root / split_name / canonical_label
            alias_dir = root / split_name / alias_label
            canonical_hashes[split_name] = set()
            alias_hashes[split_name] = set()
            if canonical_dir.exists():
                for path in canonical_dir.glob("*"):
                    if path.is_file():
                        canonical_hashes[split_name].add(hashlib.sha256(path.read_bytes()).hexdigest())
            if alias_dir.exists():
                alias_files = [path for path in alias_dir.glob("*") if path.is_file()]
                alias_total += len(alias_files)
                for path in alias_files:
                    alias_hashes[split_name].add(hashlib.sha256(path.read_bytes()).hexdigest())
        alias_counts[alias_label] = int(alias_total)
        overlaps: dict[str, int] = {}
        for canonical_split in split_names:
            for alias_split in split_names:
                overlap_count = len(canonical_hashes[canonical_split] & alias_hashes[alias_split])
                if overlap_count > 0:
                    overlaps[f"{canonical_split}->{alias_split}"] = int(overlap_count)
        if overlaps:
            cross_split_overlap[alias_label] = overlaps

    return {
        "root": str(root),
        "alias_counts": alias_counts,
        "cross_split_overlap": cross_split_overlap,
        "has_shadow_aliases": bool(any(count > 0 for count in alias_counts.values())),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train all PhoenixGuard ensemble models with sequence-aware teacher supervision.",
    )
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=DEFAULT_SAVE_DIR,
        help="Directory for saved model bundles",
    )
    parser.add_argument(
        "--split-manifest",
        type=Path,
        default=DEFAULT_SPLIT_MANIFEST,
        help="CSV split manifest used to build sequence teacher labels",
    )
    parser.add_argument(
        "--teacher-manifest",
        type=Path,
        default=DEFAULT_SEQUENCE_MANIFEST,
        help="Sequence teacher manifest JSONL path",
    )
    parser.add_argument(
        "--rebuild-teacher-manifest",
        action="store_true",
        help="Rebuild the sequence teacher manifest from the live CV pipeline before training.",
    )
    parser.add_argument(
        "--skip-teacher-manifest",
        action="store_true",
        help="Disable sequence-aware auxiliary supervision and run plain BUY/SELL training.",
    )
    parser.add_argument(
        "--sequence-aux-loss-weight",
        type=float,
        default=0.30,
        help="Weight for sequence-aware auxiliary supervision during fine-tuning.",
    )
    parser.add_argument(
        "--sequence-manifest-quality",
        choices=["all", "exclude_contradictory", "exclude_review_required", "clean_only"],
        default="exclude_contradictory",
        help=(
            "How strictly to filter sequence teacher rows before auxiliary supervision. "
            "'exclude_contradictory' is the default safe mode."
        ),
    )
    parser.add_argument(
        "--only-models",
        nargs="+",
        choices=MODEL_ORDER,
        help="Train only the specified models instead of the full ensemble.",
    )
    parser.add_argument(
        "--disable-continual-learning",
        action="store_true",
        help="Train from a fresh model state without loading the existing bundle, replay buffer, or LoRA adapter.",
    )
    return parser.parse_args()


def _read_saved_metrics(save_dir: Path, model_name: str) -> dict[str, float] | None:
    metadata_path = save_dir / f"{model_name}_metadata.json"
    if not metadata_path.exists():
        return None
    try:
        with metadata_path.open("r", encoding="utf-8") as metadata_file:
            payload_obj = json.load(metadata_file)
        if not isinstance(payload_obj, dict):
            return None
        payload = cast(dict[str, object], payload_obj)
        evaluation_metrics_obj = payload.get("evaluation_metrics", {})
        if not isinstance(evaluation_metrics_obj, dict):
            return None
        evaluation_metrics = cast(dict[str, object], evaluation_metrics_obj)
        return {
            "validation_accuracy": _saved_metric_float(evaluation_metrics, "validation_accuracy"),
            "balanced_accuracy": _saved_metric_float(evaluation_metrics, "balanced_accuracy"),
        }
    except Exception:
        return None


def _saved_metric_float(metrics: dict[str, object], key: str) -> float:
    value = metrics.get(key, 0.0)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def resolve_teacher_manifest(args: argparse.Namespace) -> str | None:
    if args.skip_teacher_manifest:
        print("[SEQUENCE TEACHER] disabled by --skip-teacher-manifest")
        return None

    teacher_manifest = Path(args.teacher_manifest)
    split_manifest = Path(args.split_manifest)

    needs_rebuild = bool(args.rebuild_teacher_manifest) or not teacher_manifest.exists()
    if not needs_rebuild:
        try:
            validation = validate_teacher_manifest(
                split_manifest_path=split_manifest,
                manifest_path=teacher_manifest,
            )
            if not bool(validation.get("metadata_present", False)):
                print(
                    "[SEQUENCE TEACHER] existing manifest has no metadata sidecar; "
                    "rebuilding to enforce atomic freshness validation."
                )
                needs_rebuild = True
            else:
                print(
                    f"[SEQUENCE TEACHER] validated {teacher_manifest} "
                    f"({validation['record_count']} rows)"
                )
        except Exception as exc:
            print(f"[SEQUENCE TEACHER] existing manifest invalid: {exc}")
            needs_rebuild = True

    if needs_rebuild:
        build_teacher_manifest(
            split_manifest_path=split_manifest,
            output_path=teacher_manifest,
        )
        validation = validate_teacher_manifest(
            split_manifest_path=split_manifest,
            manifest_path=teacher_manifest,
        )
        print(
            f"[SEQUENCE TEACHER] validated {teacher_manifest} "
            f"({validation['record_count']} rows)"
        )

    if not teacher_manifest.exists():
        raise FileNotFoundError(f"Sequence teacher manifest not found: {teacher_manifest}")

    print(f"[SEQUENCE TEACHER] using {teacher_manifest}")
    return str(teacher_manifest)


def main() -> None:
    args = parse_args()

    try:
        torch.set_float32_matmul_precision("high")
    except Exception:
        pass

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    teacher_manifest_path = resolve_teacher_manifest(args)
    train_dirs, val_dirs = resolve_split_dirs()
    alias_audit = audit_alias_split_dirs()
    print(f"[DATA SPLIT] train dirs: {train_dirs}")
    print(f"[DATA SPLIT] val dirs: {val_dirs}")
    if bool(alias_audit.get("has_shadow_aliases", False)):
        print(
            "[DATA HYGIENE WARNING] Unmanaged BUYS/SELLS shadow split detected under "
            f"{alias_audit.get('root', '')}. Excluding alias folders from training."
        )
        print(
            "[DATA HYGIENE WARNING] Alias file counts: "
            f"{json.dumps(alias_audit.get('alias_counts', {}), ensure_ascii=True, sort_keys=True)}"
        )
        overlap_view = cast(dict[str, object], alias_audit.get("cross_split_overlap", {}))
        if overlap_view:
            print(
                "[DATA HYGIENE WARNING] Alias/canonical hash overlap across splits: "
                f"{json.dumps(overlap_view, ensure_ascii=True, sort_keys=True)}"
            )

    target_models = list(args.only_models) if args.only_models else list(MODEL_ORDER)

    for model_name in target_models:
        print("=" * 80)
        print(f"[SEQUENCE-AWARE TRAINING] STARTING MODEL: {model_name}")
        print("=" * 80)

        cleanup_memory()
        torch_device: Any = getattr(torch, "device")
        device = torch_device("cuda" if torch.cuda.is_available() else "cpu")
        cfg = TRAIN_CONFIGS.get(model_name, TRAIN_CONFIGS["mobilenetv3"])
        seed_candidates = tuple(int(seed) for seed in cfg.seed_candidates) or (1337,)

        for attempt_idx, seed in enumerate(seed_candidates, start=1):
            if len(seed_candidates) > 1:
                print(
                    f"[SEED SEARCH] {model_name} attempt {attempt_idx}/{len(seed_candidates)} "
                    f"with seed {seed}"
                )

            ensemble = EnsembleCVModels(
                train_dirs,
                device,
                [model_name],
                sequence_manifest_path=teacher_manifest_path,
                sequence_aux_loss_weight=float(args.sequence_aux_loss_weight),
                sequence_manifest_quality=str(args.sequence_manifest_quality),
                random_seed=seed,
                enable_continual_learning=not bool(args.disable_continual_learning),
            )

            ensemble.fine_tune_all(
                epochs=EPOCHS_PER_MODEL.get(model_name, 12),
                save_dir=str(save_dir),
                val_dirs=val_dirs,
                only_models=[model_name],
                verbose=1,
            )

            del ensemble
            cleanup_memory()

            saved_metrics = _read_saved_metrics(save_dir, model_name)
            if (
                saved_metrics is not None
                and saved_metrics["validation_accuracy"] >= 70.0
                and saved_metrics["balanced_accuracy"] >= 70.0
            ):
                print(
                    f"[SEED SEARCH] {model_name} reached target with saved metrics "
                    f"acc={saved_metrics['validation_accuracy']:.2f}% | "
                    f"bal_acc={saved_metrics['balanced_accuracy']:.2f}%"
                )
                break

        print("=" * 80)
        print(f"[SEQUENCE-AWARE TRAINING] FINISHED MODEL: {model_name}")
        print("=" * 80)

    print("[DONE] All sequence-aware ensemble models trained and saved.")


if __name__ == "__main__":
    main()
