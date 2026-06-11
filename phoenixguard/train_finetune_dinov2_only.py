from __future__ import annotations

import gc
import random
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
import torch

from phoenixguard.paths import PROJECT_ROOT
from phoenixguard.training.ensemble_cv_models import EnsembleCVModels  # type: ignore[import]


TRAIN_DIRS = [
    str(PROJECT_ROOT / "data" / "clean_split" / "train" / "BUY"),
    str(PROJECT_ROOT / "data" / "clean_split" / "train" / "SELL"),
]

VAL_DIRS = [
    str(PROJECT_ROOT / "data" / "clean_split" / "val" / "BUY"),
    str(PROJECT_ROOT / "data" / "clean_split" / "val" / "SELL"),
]

SAVE_DIR = str(PROJECT_ROOT / "models")
SEQUENCE_MANIFEST = str(PROJECT_ROOT / "data_splits" / "sequence_teacher_manifest.jsonl")

MODEL_ORDER = [
    "dinov2",
]

EPOCHS_PER_MODEL = {
    "dinov2": 18,
}


def cleanup_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def seed_everything(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)

    torch.manual_seed(seed)  # type: ignore[attr-defined]

    if torch.cuda.is_available():
        cuda_manual_seed_all_fn = cast(Callable[[int], None], torch.cuda.manual_seed_all)
        cuda_manual_seed_all_fn(seed)

    try:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    except Exception:
        pass


def main() -> None:
    try:
        torch.set_float32_matmul_precision("high")
    except Exception:
        pass

    seed_everything(42)

    save_path = Path(SAVE_DIR)
    save_path.mkdir(parents=True, exist_ok=True)
    sequence_manifest_path = str(Path(SEQUENCE_MANIFEST)) if Path(SEQUENCE_MANIFEST).exists() else None
    if sequence_manifest_path is not None:
        print(f"[SEQUENCE TEACHER] using {sequence_manifest_path}")
    else:
        print("[SEQUENCE TEACHER] not found, falling back to plain BUY/SELL training.")

    for model_name in MODEL_ORDER:
        print("=" * 80)
        print(f"[SEQUENTIAL TRAINING] STARTING MODEL: {model_name}")
        print("=" * 80)

        cleanup_memory()

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        ensemble: EnsembleCVModels = EnsembleCVModels(  # type: ignore[assignment]
            TRAIN_DIRS,
            device,
            [model_name],
            sequence_manifest_path=sequence_manifest_path,
        )

        ensemble.fine_tune_all(  # type: ignore[attr-defined]
            epochs=EPOCHS_PER_MODEL.get(model_name, 18),
            save_dir=SAVE_DIR,
            val_dirs=VAL_DIRS,
            only_models=[model_name],
            verbose=1,
        )

        del ensemble
        cleanup_memory()

        print("=" * 80)
        print(f"[SEQUENTIAL TRAINING] FINISHED MODEL: {model_name}")
        print("=" * 80)

    print("[DONE] DINOv2 trained and saved.")


if __name__ == "__main__":
    main()
