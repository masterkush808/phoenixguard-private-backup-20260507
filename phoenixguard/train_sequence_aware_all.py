from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import torch
from phoenixguard.paths import PROJECT_ROOT
from phoenixguard.training.ensemble_cv_models import EnsembleCVModels, TRAIN_CONFIGS  # type: ignore[import]

from scripts.build_sequence_teacher_manifest import build_teacher_manifest, validate_teacher_manifest

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


def _preferred_label_dir(split_name: str, canonical_label: str) -> str:
    clean_split_root = PROJECT_ROOT / "data" / "clean_split" / split_name
    preferred = clean_split_root / canonical_label
    if preferred.exists():
        return str(preferred)

    legacy_name = f"{canonical_label}S"
    legacy = clean_split_root / legacy_name
    if legacy.exists():
        return str(legacy)

    raise FileNotFoundError(
        f"Could not find clean split directory for {split_name}/{canonical_label} "
        f"under {clean_split_root}"
    )


def resolve_split_dirs() -> tuple[list[str], list[str]]:
    train_dirs = [
        _preferred_label_dir("train", "BUY"),
        _preferred_label_dir("train", "SELL"),
    ]
    val_dirs = [
        _preferred_label_dir("val", "BUY"),
        _preferred_label_dir("val", "SELL"),
    ]
    return train_dirs, val_dirs


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
        "--only-models",
        nargs="+",
        choices=MODEL_ORDER,
        help="Train only the specified models instead of the full ensemble.",
    )
    return parser.parse_args()


def _read_saved_metrics(save_dir: Path, model_name: str) -> dict[str, float] | None:
    metadata_path = save_dir / f"{model_name}_metadata.json"
    if not metadata_path.exists():
        return None
    try:
        with metadata_path.open("r", encoding="utf-8") as metadata_file:
            payload = json.load(metadata_file)
        evaluation_metrics = payload.get("evaluation_metrics", {})
        if not isinstance(evaluation_metrics, dict):
            return None
        return {
            "validation_accuracy": float(evaluation_metrics.get("validation_accuracy", 0.0) or 0.0),
            "balanced_accuracy": float(evaluation_metrics.get("balanced_accuracy", 0.0) or 0.0),
        }
    except Exception:
        return None


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
    print(f"[DATA SPLIT] train dirs: {train_dirs}")
    print(f"[DATA SPLIT] val dirs: {val_dirs}")

    target_models = list(args.only_models) if args.only_models else list(MODEL_ORDER)

    for model_name in target_models:
        print("=" * 80)
        print(f"[SEQUENCE-AWARE TRAINING] STARTING MODEL: {model_name}")
        print("=" * 80)

        cleanup_memory()
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
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
                random_seed=seed,
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
