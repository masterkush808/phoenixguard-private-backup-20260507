# pyright: reportMissingTypeStubs=false
from __future__ import annotations

"""
PhoenixGuard Ensemble CV Models
==============================
Handles loading, fine-tuning, and inference for DINOv2, SimCLR, BYOL, SwAV,
MobileNetV3, and CLIP on chart images.

This rewrite preserves the existing public workflow:
- EnsembleCVModels(image_dirs=..., target_models=[...])
- fine_tune_all(...)
- evaluate(...)
- predict_ensemble(...)

Upgrades wired in without changing the outer training flow:
- sequential one-model-at-a-time training remains intact
- stable feature extraction for every model
- correct classifier-head based validation and prediction
- safer optimization for small datasets
- warmup + cosine learning-rate scheduling
- gradient clipping
- early stopping
- BatchNorm running-stat freezing for CNN-style backbones
- DINOv2 memory-safe staged fine-tuning
- optional staged fine-tuning for other backbones
- safe embedding export using backbone features only
"""

import copy
import gc
import json
import os
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from PIL import Image
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim


def _patch_torchvision_register_fake() -> None:
    register_fake = getattr(torch.library, "register_fake", None)
    if register_fake is None or bool(getattr(register_fake, "_phoenixguard_safe", False)):
        return

    def _safe_register_fake(op_name: str, *args: Any, **kwargs: Any) -> Any:
        decorator = register_fake(op_name, *args, **kwargs)

        def _wrapper(fn: Any) -> Any:
            try:
                return decorator(fn)
            except RuntimeError as exc:
                if "torchvision::nms" in str(exc):
                    return fn
                raise

        return _wrapper

    setattr(_safe_register_fake, "_phoenixguard_safe", True)
    torch.library.register_fake = _safe_register_fake


_patch_torchvision_register_fake()

try:
    import clip  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    clip = None  # type: ignore[assignment]
import timm
import torchvision.transforms as T
try:
    from lightly.models.modules import (  # type: ignore[import-not-found]
        BYOLPredictionHead as _LightlyBYOLPredictionHead,
        BYOLProjectionHead as _LightlyBYOLProjectionHead,
        SimCLRProjectionHead as _LightlySimCLRProjectionHead,
        SwaVProjectionHead as _LightlySwaVProjectionHead,
        SwaVPrototypes as _LightlySwaVPrototypes,
    )
    BYOLPredictionHead = cast(Any, _LightlyBYOLPredictionHead)
    BYOLProjectionHead = cast(Any, _LightlyBYOLProjectionHead)
    SimCLRProjectionHead = cast(Any, _LightlySimCLRProjectionHead)
    SwaVProjectionHead = cast(Any, _LightlySwaVProjectionHead)
    SwaVPrototypes = cast(Any, _LightlySwaVPrototypes)
except Exception:  # pragma: no cover - optional dependency fallback
    class SimCLRProjectionHead(nn.Module):
        def __init__(self, in_features: int, hidden_features: int, out_features: int) -> None:
            super().__init__()
            self.layers = nn.Sequential(
                nn.Linear(in_features, hidden_features, bias=False),
                nn.BatchNorm1d(hidden_features),
                nn.ReLU(inplace=True),
                nn.Linear(hidden_features, out_features, bias=False),
                nn.BatchNorm1d(out_features),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.layers(x)


    class BYOLProjectionHead(nn.Module):
        def __init__(self, in_features: int, hidden_features: int, out_features: int) -> None:
            super().__init__()
            self.layers = nn.Sequential(
                nn.Linear(in_features, hidden_features, bias=False),
                nn.BatchNorm1d(hidden_features),
                nn.ReLU(inplace=True),
                nn.Linear(hidden_features, out_features, bias=True),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.layers(x)


    class BYOLPredictionHead(BYOLProjectionHead):
        pass


    class SwaVProjectionHead(BYOLProjectionHead):
        pass


    class SwaVPrototypes(nn.Module):
        def __init__(self, in_features: int, n_prototypes: int = 512) -> None:
            super().__init__()
            self.heads = nn.ModuleList([nn.Linear(in_features, n_prototypes)])

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.heads[0](x)

from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from phoenixguard.core.config import RUNTIME, TRAIN
from phoenixguard.runtime.continual_adapters import (
    AdapterConfig,
    apply_lora_adapters,
    available_adapters,
    collect_adaptable_module_paths,
    collect_lora_summary,
    sanitize_adapter_name,
    set_active_adapter,
    set_adapter_trainable,
)
from scripts.build_sequence_teacher_manifest import validate_directional_teacher_consistency


IMAGENET_MEAN: Final[tuple[float, float, float]] = (0.485, 0.456, 0.406)
IMAGENET_STD: Final[tuple[float, float, float]] = (0.229, 0.224, 0.225)
CLIP_MEAN: Final[tuple[float, float, float]] = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD: Final[tuple[float, float, float]] = (0.26862954, 0.26130258, 0.27577711)

DEFAULT_SAVE_DIR: Final[str] = str(RUNTIME.models_dir)
_VALID_IMAGE_SUFFIXES: Final[tuple[str, ...]] = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
_BATCHNORM_TYPES: Final[tuple[type[nn.Module], ...]] = (
    nn.BatchNorm1d,
    nn.BatchNorm2d,
    nn.BatchNorm3d,
    nn.SyncBatchNorm,
)

TransformFn = Callable[[Image.Image], torch.Tensor]
_SEQUENCE_TASK_PRIORITY: Final[tuple[str, ...]] = (
    "projection_direction",
    "current_box_direction",
    "current_box_type",
    "next_box_direction",
    "next_box_type",
    "trigger",
    "projected_role",
    "entry_type",
    "macro_trend",
    "local_phase",
    "swing_phase",
    "structure_setup",
)
_UPPERCASE_SEQUENCE_TASKS: Final[frozenset[str]] = frozenset(
    {
        "projection_direction",
        "current_box_direction",
        "next_box_direction",
        "macro_trend",
    }
)
_SEQUENCE_MANIFEST_QUALITY_MODES: Final[frozenset[str]] = frozenset(
    {
        "all",
        "exclude_contradictory",
        "exclude_review_required",
        "clean_only",
    }
)


def _normalize_path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _path_key_aliases(path: str) -> set[str]:
    normalized = _normalize_path_key(path)
    aliases: set[str] = {normalized}

    path_obj = Path(normalized)
    parts = list(path_obj.parts)
    replacement_pairs = (
        ("BUY", "BUYS"),
        ("BUYS", "BUY"),
        ("SELL", "SELLS"),
        ("SELLS", "SELL"),
    )

    for idx, part in enumerate(parts):
        upper_part = part.upper()
        for source, target in replacement_pairs:
            if upper_part == source:
                alt_parts = list(parts)
                alt_parts[idx] = target
                aliases.add(_normalize_path_key(str(Path(*alt_parts))))

    return aliases


def _binary_label_from_dir_name(dir_name: str, fallback: int) -> int:
    normalized = str(dir_name).strip().upper()
    if normalized in {"BUY", "BUYS"}:
        return 0
    if normalized in {"SELL", "SELLS"}:
        return 1
    return int(fallback)


def _normalize_sequence_value(task_name: str, value: Any) -> str | None:
    text = str(value).strip()
    if not text:
        return None
    if task_name in _UPPERCASE_SEQUENCE_TASKS:
        return text.upper()
    return text.lower()


def _normalize_sequence_manifest_quality(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized not in _SEQUENCE_MANIFEST_QUALITY_MODES:
        raise ValueError(
            "Unsupported sequence manifest quality mode "
            f"{value!r}. Expected one of {sorted(_SEQUENCE_MANIFEST_QUALITY_MODES)}."
        )
    return normalized


def _sequence_record_filter_decision(
    record: Mapping[str, Any],
    *,
    quality_mode: str,
) -> tuple[bool, str]:
    normalized_mode = _normalize_sequence_manifest_quality(quality_mode)
    if normalized_mode == "all":
        return True, "included"

    task_labels_obj = record.get("teacher_task_labels", {})
    task_labels = task_labels_obj if isinstance(task_labels_obj, Mapping) else {}
    label_quality = str(task_labels.get("label_quality", "")).strip().lower()
    review_bucket = str(task_labels.get("review_bucket", "")).strip().lower()
    review_required = bool(task_labels.get("review_required", False))

    if normalized_mode == "exclude_contradictory":
        if label_quality == "contradictory" or review_bucket == "hard_negative":
            return False, "contradictory"
        return True, "included"

    if normalized_mode == "exclude_review_required":
        if review_required:
            return False, "review_required"
        return True, "included"

    if normalized_mode == "clean_only":
        if label_quality == "clean":
            return True, "included"
        return False, label_quality or "non_clean"

    return True, "included"


def cleanup_torch_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


@dataclass(frozen=True)
class ModelTrainConfig:
    input_size: int
    batch_size: int
    grad_accum_steps: int
    head_lr: float
    backbone_lr: float
    weight_decay: float
    metric_loss_weight: float
    label_smoothing: float
    max_grad_norm: float
    head_only_epochs: int
    unfreeze_threshold: float
    early_stop_patience: int
    warmup_epochs: int
    min_lr: float
    sampler_power: float
    class_weight_blend: float
    buy_weight_scale: float
    sell_weight_scale: float
    use_balanced_sampler: bool
    use_class_weights: bool
    use_focal_loss: bool = False
    focal_gamma: float = 1.5
    buy_recall_tiebreak: bool = False
    sell_recall_tiebreak: bool = False
    target_min_recall: float = 0.0
    backbone_layer_decay: float = 1.0
    prob_gap_target: float = 0.24
    prob_gap_penalty_weight: float = 0.06
    temperature_gap_weight: float = 0.18
    min_temperature: float = 1.20
    stage2_mode: str = "last_block"
    aux_label_smoothing: float = 0.03
    head_only_aux_loss_scale: float = 1.0
    stage2_aux_loss_scale: float = 0.60
    full_aux_loss_scale: float = 0.75
    stage2_aux_decay_floor: float = 0.60
    full_aux_decay_floor: float = 0.75
    aux_overfit_tolerance: float = 4.0
    freeze_aux_head_after_head_only: bool = True
    transform_profile: str = "basic"
    seed_candidates: tuple[int, ...] = (1337,)


@dataclass(frozen=True)
class ReplaySample:
    image_path: str
    label: int
    context_key: str = ""
    adapter_name: str = ""


@dataclass
class ContinualTrainingState:
    enabled: bool = False
    adapter_name: str = ""
    dominant_context_key: str = ""
    previous_bundle_path: str = ""
    replay_samples: list[ReplaySample] = field(default_factory=list)
    used_lora: bool = False
    lora_target_paths: list[str] = field(default_factory=list)
    teacher_model: nn.Module | None = None
    teacher_head: nn.Module | None = None
    teacher_aux_head: nn.Module | None = None
    reference_params: dict[str, torch.Tensor] = field(default_factory=dict)
    fisher_diagonal: dict[str, torch.Tensor] = field(default_factory=dict)


class FocalCrossEntropyLoss(nn.Module):
    """
    Class-aware focal loss for small imbalanced datasets.
    Uses standard CE targets, optional per-class weighting, and a mild gamma
    so DINO stays stable while paying more attention to hard BUY examples.
    """

    def __init__(
        self,
        *,
        weight: torch.Tensor | None = None,
        gamma: float = 1.5,
    ) -> None:
        super().__init__()
        if weight is not None:
            self.register_buffer("weight", weight.detach().clone().to(dtype=torch.float32), persistent=False)
        else:
            self.weight = None
        self.gamma = float(max(gamma, 0.0))

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        log_probs = F.log_softmax(logits, dim=-1)
        probs = log_probs.exp()
        targets_view = targets.view(-1, 1)
        target_log_probs = log_probs.gather(1, targets_view).squeeze(1)
        target_probs = probs.gather(1, targets_view).squeeze(1)
        focal_factor = torch.pow((1.0 - target_probs).clamp(min=1e-6), self.gamma)
        loss = -focal_factor * target_log_probs

        if self.weight is not None:
            class_weights = self.weight.to(device=logits.device, dtype=logits.dtype)
            sample_weights = class_weights.gather(0, targets.view(-1))
            denom = sample_weights.sum().clamp_min(1e-6)
            return torch.sum(loss * sample_weights) / denom

        return loss.mean()


class SequenceAuxiliaryHead(nn.Module):
    def __init__(
        self,
        in_features: int,
        task_values: Mapping[str, Sequence[str]],
        *,
        dropout: float = 0.14,
    ) -> None:
        super().__init__()
        hidden_features = max(128, min(1024, in_features // 2 if in_features >= 256 else in_features))
        self.shared = nn.Sequential(
            nn.LayerNorm(in_features),
            nn.Linear(in_features, hidden_features),
            nn.GELU(),
            nn.Dropout(p=float(dropout)),
        )
        self.task_heads = nn.ModuleDict()
        self.task_names: list[str] = []

        for task_name in task_values:
            num_classes = len(task_values[task_name])
            if num_classes < 2:
                continue
            self.task_heads[task_name] = nn.Linear(hidden_features, num_classes)
            self.task_names.append(task_name)

    def forward(self, features: torch.Tensor) -> dict[str, torch.Tensor]:
        hidden = self.shared(features)
        return {
            task_name: head(hidden)
            for task_name, head in self.task_heads.items()
        }


TRAIN_CONFIGS: Final[dict[str, ModelTrainConfig]] = {
    "mobilenetv3": ModelTrainConfig(
        input_size=224,
        batch_size=16,
        grad_accum_steps=1,
        head_lr=5e-4,
        backbone_lr=3.0e-6,
        weight_decay=1.0e-2,
        metric_loss_weight=0.0,
        label_smoothing=0.06,
        max_grad_norm=0.5,
        head_only_epochs=2,
        unfreeze_threshold=0.0,
        early_stop_patience=4,
        warmup_epochs=1,
        min_lr=5e-7,
        sampler_power=1.0,
        class_weight_blend=1.0,
        buy_weight_scale=1.0,
        sell_weight_scale=1.0,
        use_balanced_sampler=True,
        use_class_weights=True,
        prob_gap_target=0.20,
        prob_gap_penalty_weight=0.10,
        temperature_gap_weight=0.24,
        min_temperature=1.35,
        stage2_mode="last_block",
        aux_label_smoothing=0.04,
        head_only_aux_loss_scale=1.00,
        stage2_aux_loss_scale=0.52,
        full_aux_loss_scale=0.65,
        stage2_aux_decay_floor=0.45,
        full_aux_decay_floor=0.60,
        aux_overfit_tolerance=3.5,
        freeze_aux_head_after_head_only=True,
        transform_profile="basic",
    ),
    "dinov2": ModelTrainConfig(
        input_size=392,
        batch_size=4,
        grad_accum_steps=4,
        head_lr=2.6e-4,
        backbone_lr=2.2e-6,
        weight_decay=8.5e-3,
        metric_loss_weight=0.010,
        label_smoothing=0.0,
        max_grad_norm=0.5,
        head_only_epochs=8,
        unfreeze_threshold=69.0,
        early_stop_patience=8,
        warmup_epochs=2,
        min_lr=1e-7,
        sampler_power=0.65,
        class_weight_blend=0.65,
        buy_weight_scale=1.08,
        sell_weight_scale=1.00,
        use_balanced_sampler=True,
        use_class_weights=True,
        use_focal_loss=True,
        focal_gamma=1.3,
        buy_recall_tiebreak=False,
        backbone_layer_decay=0.55,
        prob_gap_target=0.20,
        prob_gap_penalty_weight=0.07,
        temperature_gap_weight=0.22,
        min_temperature=1.35,
        stage2_mode="last_block",
        aux_label_smoothing=0.02,
        head_only_aux_loss_scale=1.05,
        stage2_aux_loss_scale=0.58,
        full_aux_loss_scale=0.82,
        stage2_aux_decay_floor=0.48,
        full_aux_decay_floor=0.78,
        aux_overfit_tolerance=4.5,
        freeze_aux_head_after_head_only=True,
        transform_profile="basic",
    ),
    "simclr": ModelTrainConfig(
        input_size=224,
        batch_size=8,
        grad_accum_steps=2,
        head_lr=2.6e-4,
        backbone_lr=3.0e-6,
        weight_decay=8e-3,
        metric_loss_weight=0.008,
        label_smoothing=0.05,
        max_grad_norm=0.5,
        head_only_epochs=3,
        unfreeze_threshold=0.0,
        early_stop_patience=5,
        warmup_epochs=1,
        min_lr=5e-7,
        sampler_power=0.35,
        class_weight_blend=0.55,
        buy_weight_scale=1.18,
        sell_weight_scale=0.94,
        use_balanced_sampler=True,
        use_class_weights=True,
        use_focal_loss=True,
        focal_gamma=1.2,
        buy_recall_tiebreak=True,
        target_min_recall=0.0,
        prob_gap_target=0.21,
        prob_gap_penalty_weight=0.09,
        temperature_gap_weight=0.22,
        min_temperature=1.30,
        stage2_mode="last_block",
        aux_label_smoothing=0.03,
        head_only_aux_loss_scale=1.00,
        stage2_aux_loss_scale=0.46,
        full_aux_loss_scale=0.62,
        stage2_aux_decay_floor=0.38,
        full_aux_decay_floor=0.58,
        aux_overfit_tolerance=3.5,
        freeze_aux_head_after_head_only=True,
        transform_profile="ssl_boost",
        seed_candidates=(1337, 2027, 3037),
    ),
    "byol": ModelTrainConfig(
        input_size=224,
        batch_size=8,
        grad_accum_steps=2,
        head_lr=2.2e-4,
        backbone_lr=2.5e-6,
        weight_decay=8e-3,
        metric_loss_weight=0.0,
        label_smoothing=0.06,
        max_grad_norm=0.5,
        head_only_epochs=3,
        unfreeze_threshold=0.0,
        early_stop_patience=5,
        warmup_epochs=1,
        min_lr=5e-7,
        sampler_power=0.35,
        class_weight_blend=0.55,
        buy_weight_scale=0.90,
        sell_weight_scale=1.24,
        use_balanced_sampler=True,
        use_class_weights=True,
        use_focal_loss=True,
        focal_gamma=1.4,
        sell_recall_tiebreak=True,
        target_min_recall=0.0,
        prob_gap_target=0.20,
        prob_gap_penalty_weight=0.10,
        temperature_gap_weight=0.24,
        min_temperature=1.35,
        stage2_mode="last_block",
        aux_label_smoothing=0.04,
        head_only_aux_loss_scale=1.00,
        stage2_aux_loss_scale=0.44,
        full_aux_loss_scale=0.60,
        stage2_aux_decay_floor=0.36,
        full_aux_decay_floor=0.56,
        aux_overfit_tolerance=3.5,
        freeze_aux_head_after_head_only=True,
        transform_profile="ssl_boost",
        seed_candidates=(1337, 2027, 3037),
    ),
    "swav": ModelTrainConfig(
        input_size=224,
        batch_size=8,
        grad_accum_steps=2,
        head_lr=3.0e-4,
        backbone_lr=3.5e-6,
        weight_decay=8e-3,
        metric_loss_weight=0.012,
        label_smoothing=0.06,
        max_grad_norm=0.5,
        head_only_epochs=2,
        unfreeze_threshold=0.0,
        early_stop_patience=4,
        warmup_epochs=1,
        min_lr=5e-7,
        sampler_power=0.35,
        class_weight_blend=0.40,
        buy_weight_scale=1.06,
        sell_weight_scale=0.99,
        use_balanced_sampler=True,
        use_class_weights=True,
        prob_gap_target=0.21,
        prob_gap_penalty_weight=0.09,
        temperature_gap_weight=0.22,
        min_temperature=1.30,
        stage2_mode="last_block",
        aux_label_smoothing=0.03,
        head_only_aux_loss_scale=1.00,
        stage2_aux_loss_scale=0.50,
        full_aux_loss_scale=0.62,
        stage2_aux_decay_floor=0.42,
        full_aux_decay_floor=0.58,
        aux_overfit_tolerance=3.5,
        freeze_aux_head_after_head_only=True,
        transform_profile="basic",
    ),
    "clip": ModelTrainConfig(
        input_size=224,
        batch_size=4,
        grad_accum_steps=2,
        head_lr=2e-4,
        backbone_lr=3e-6,
        weight_decay=1e-2,
        metric_loss_weight=0.0,
        label_smoothing=0.05,
        max_grad_norm=0.5,
        head_only_epochs=3,
        unfreeze_threshold=0.0,
        early_stop_patience=4,
        warmup_epochs=2,
        min_lr=5e-7,
        sampler_power=1.0,
        class_weight_blend=1.0,
        buy_weight_scale=1.0,
        sell_weight_scale=1.0,
        use_balanced_sampler=True,
        use_class_weights=True,
        prob_gap_target=0.22,
        prob_gap_penalty_weight=0.08,
        temperature_gap_weight=0.20,
        min_temperature=1.25,
        stage2_mode="last_block",
        aux_label_smoothing=0.03,
        head_only_aux_loss_scale=1.00,
        stage2_aux_loss_scale=0.55,
        full_aux_loss_scale=0.68,
        stage2_aux_decay_floor=0.48,
        full_aux_decay_floor=0.62,
        aux_overfit_tolerance=4.0,
        freeze_aux_head_after_head_only=True,
        transform_profile="basic",
    ),
}


class ChartImageDataset(Dataset[tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]]):
    def __init__(
        self,
        image_dirs: Sequence[str],
        transform: TransformFn | None = None,
        *,
        sequence_targets_by_path: Mapping[str, Mapping[str, int]] | None = None,
        sequence_task_spaces: Mapping[str, Mapping[str, int]] | None = None,
    ) -> None:
        self.samples: list[str] = []
        self.labels: list[int] = []
        self.transform: TransformFn | None = transform
        self.sequence_task_names: list[str] = list(sequence_task_spaces.keys()) if sequence_task_spaces else []
        self.sequence_label_indices: list[dict[str, int]] = []

        print(f"[DATASET INIT] Initializing ChartImageDataset with dirs: {image_dirs}")
        for fallback_label, dir_path in enumerate(image_dirs):
            if not os.path.isdir(dir_path):
                print(f"[DATASET WARNING] Directory not found: {dir_path}")
                continue
            label = _binary_label_from_dir_name(Path(dir_path).name, fallback_label)
            found = 0
            for fname in sorted(os.listdir(dir_path)):
                if fname.lower().endswith(_VALID_IMAGE_SUFFIXES):
                    img_path = os.path.join(dir_path, fname)
                    self.samples.append(img_path)
                    self.labels.append(label)
                    normalized_path = _normalize_path_key(img_path)
                    sample_targets = (
                        dict(sequence_targets_by_path.get(normalized_path, {}))
                        if sequence_targets_by_path is not None
                        else {}
                    )
                    self.sequence_label_indices.append(
                        {
                            task_name: int(sample_targets.get(task_name, -1))
                            for task_name in self.sequence_task_names
                        }
                    )
                    found += 1
            print(f"[DATASET] Found {found} images in {dir_path}")

        print(f"[DATASET] Total images loaded: {len(self.samples)}")
        if not self.samples:
            joined = "\n".join(image_dirs)
            raise FileNotFoundError(f"No training images found in:\n{joined}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
        img_path = self.samples[idx]
        label = int(self.labels[idx])
        with Image.open(img_path) as image_obj:
            img = image_obj.convert("RGB")
        if self.transform is None:
            tensor = build_basic_transform(224, is_training=False)(img)
        else:
            tensor = self.transform(img)
        aux_targets = {
            task_name: torch.tensor(int(self.sequence_label_indices[idx].get(task_name, -1)), dtype=torch.long)
            for task_name in self.sequence_task_names
        }
        return tensor, torch.tensor(label, dtype=torch.long), aux_targets


class ReplayChartDataset(Dataset[tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]]):
    def __init__(
        self,
        samples: Sequence[ReplaySample],
        transform: TransformFn | None = None,
        *,
        sequence_task_spaces: Mapping[str, Mapping[str, int]] | None = None,
    ) -> None:
        self.records = [sample for sample in samples if os.path.isfile(sample.image_path)]
        self.samples = [sample.image_path for sample in self.records]
        self.labels = [int(sample.label) for sample in self.records]
        self.transform = transform
        self.sequence_task_names = list(sequence_task_spaces.keys()) if sequence_task_spaces else []
        self.sequence_label_indices = [
            {task_name: -1 for task_name in self.sequence_task_names}
            for _ in self.records
        ]

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
        img_path = self.records[idx].image_path
        label = int(self.records[idx].label)
        with Image.open(img_path) as image_obj:
            img = image_obj.convert("RGB")
        if self.transform is None:
            tensor = build_basic_transform(224, is_training=False)(img)
        else:
            tensor = self.transform(img)
        aux_targets = {
            task_name: torch.tensor(-1, dtype=torch.long)
            for task_name in self.sequence_task_names
        }
        return tensor, torch.tensor(label, dtype=torch.long), aux_targets


class MergedChartDataset(Dataset[tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]]):
    def __init__(self, datasets: Sequence[Dataset[tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]]]) -> None:
        self.datasets = [dataset for dataset in datasets if len(cast(Any, dataset)) > 0]
        self._offsets: list[int] = []
        running = 0
        for dataset in self.datasets:
            self._offsets.append(running)
            running += len(cast(Any, dataset))
        self.samples: list[str] = []
        self.labels: list[int] = []
        self.sequence_task_names: list[str] = []
        self.sequence_label_indices: list[dict[str, int]] = []
        for dataset in self.datasets:
            self.samples.extend(list(getattr(dataset, "samples", [])))
            self.labels.extend([int(label) for label in getattr(dataset, "labels", [])])
            for task_name in getattr(dataset, "sequence_task_names", []):
                if task_name not in self.sequence_task_names:
                    self.sequence_task_names.append(str(task_name))
            self.sequence_label_indices.extend(list(getattr(dataset, "sequence_label_indices", [])))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
        for dataset, offset in zip(self.datasets, self._offsets):
            upper = offset + len(cast(Any, dataset))
            if idx < upper:
                return dataset[idx - offset]
        raise IndexError(idx)


class TimmBackbone(nn.Module):
    def __init__(self, model_name: str, *, pretrained: bool = True) -> None:
        super().__init__()
        self.model = timm.create_model(model_name, pretrained=bool(pretrained), num_classes=0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return get_pooled_features(self.model, x)


class LightlySimCLR(nn.Module):
    def __init__(self, backbone: nn.Module, feature_dim: int = 2048, proj_dim: int = 128) -> None:
        super().__init__()
        self.backbone = backbone
        self.projection_head = SimCLRProjectionHead(feature_dim, feature_dim, proj_dim)
        self.num_features = feature_dim

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        return get_pooled_features(self.backbone, x)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.forward_features(x)
        return self.projection_head(features)


class LightlyBYOL(nn.Module):
    def __init__(self, backbone: nn.Module, feature_dim: int = 2048, proj_dim: int = 256) -> None:
        super().__init__()
        self.backbone = backbone
        self.projection_head = BYOLProjectionHead(feature_dim, 1024, proj_dim)
        self.prediction_head = BYOLPredictionHead(proj_dim, 1024, proj_dim)
        self.target_backbone = copy.deepcopy(backbone)
        self.target_projection_head = copy.deepcopy(self.projection_head)
        self.num_features = feature_dim

        for param in self.target_backbone.parameters():
            param.requires_grad = False
        for param in self.target_projection_head.parameters():
            param.requires_grad = False

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        return get_pooled_features(self.backbone, x)

    def forward(
        self,
        x0: torch.Tensor,
        x1: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        h0 = self.forward_features(x0)
        h1 = self.forward_features(x1)
        z0 = self.projection_head(h0)
        z1 = self.projection_head(h1)
        p0 = self.prediction_head(z0)
        p1 = self.prediction_head(z1)
        with torch.no_grad():
            t0 = self.target_projection_head(get_pooled_features(self.target_backbone, x0))
            t1 = self.target_projection_head(get_pooled_features(self.target_backbone, x1))
        return p0, p1, t0.detach(), t1.detach()

    @torch.no_grad()
    def update_momentum(self, m: float = 0.99) -> None:
        for online, target in zip(self.backbone.parameters(), self.target_backbone.parameters()):
            target.data = m * target.data + (1.0 - m) * online.data
        for online, target in zip(self.projection_head.parameters(), self.target_projection_head.parameters()):
            target.data = m * target.data + (1.0 - m) * online.data


class LightlySwaV(nn.Module):
    def __init__(
        self,
        backbone: nn.Module,
        feature_dim: int = 2048,
        proj_dim: int = 128,
        n_prototypes: int = 512,
    ) -> None:
        super().__init__()
        self.backbone = backbone
        self.projection_head = SwaVProjectionHead(feature_dim, feature_dim, proj_dim)
        self.prototypes = SwaVPrototypes(proj_dim, n_prototypes=n_prototypes)
        self.num_features = feature_dim

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        return get_pooled_features(self.backbone, x)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        features = self.forward_features(x)
        z = self.projection_head(features)
        p = self.prototypes(z)
        return features, p


class ClipImageEncoder(nn.Module):
    def __init__(self, clip_model: Any) -> None:
        super().__init__()
        self.clip_model = clip_model
        self.num_features = 512

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        image_features = self.clip_model.encode_image(x)
        if not isinstance(image_features, torch.Tensor):
            raise TypeError(f"Expected Tensor from CLIP image encoder, got {type(image_features)!r}")
        return image_features.float()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.forward_features(x)


class ClassificationBundle(nn.Module):
    def __init__(self, backbone: nn.Module, head: nn.Module) -> None:
        super().__init__()
        self.backbone = backbone
        self.head = head

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = forward_features(self.backbone, x)
        return self.head(features)


class DinoV2FeatureAdapter(nn.Module):
    def __init__(self, backbone: nn.Module, use_patch_mean: bool = True) -> None:
        super().__init__()
        self.backbone = backbone
        self.use_patch_mean = use_patch_mean

        base_dim_attr = cast(object, getattr(backbone, "num_features", None))
        if isinstance(base_dim_attr, int):
            base_dim = base_dim_attr
        else:
            embed_dim_attr = cast(object, getattr(backbone, "embed_dim", 384))
            base_dim = embed_dim_attr if isinstance(embed_dim_attr, int) else 384

        self.base_feature_dim = base_dim
        self.num_features = base_dim * 2 if use_patch_mean else base_dim

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        output_obj = cast(object, self.backbone.forward_features(x))  # type: ignore[misc]
        first_tensor = _first_tensor_from_output(output_obj)
        if first_tensor is not None:
            output_obj = cast(object, first_tensor)

        if not isinstance(output_obj, torch.Tensor):
            raise RuntimeError(f"Unexpected DINOv2 output type: {type(output_obj)!r}")

        if output_obj.ndim == 2:
            return output_obj

        if output_obj.ndim != 3:
            raise RuntimeError(f"Unexpected DINOv2 output ndim: {output_obj.ndim}")

        cls_token = output_obj[:, 0]

        num_prefix_tokens_attr = cast(object, getattr(self.backbone, "num_prefix_tokens", 1))
        num_prefix_tokens = num_prefix_tokens_attr if isinstance(num_prefix_tokens_attr, int) else 1

        patch_tokens = output_obj[:, num_prefix_tokens:]
        if patch_tokens.numel() == 0 or not self.use_patch_mean:
            return cls_token

        patch_mean = patch_tokens.mean(dim=1)
        return torch.cat([cls_token, patch_mean], dim=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.forward_features(x)


_has_clip = clip is not None


def _first_tensor_from_output(value: object) -> torch.Tensor | None:
    if not isinstance(value, (tuple, list)):
        return None

    seq: Sequence[Any] = cast(Sequence[Any], value)
    if len(seq) == 0:
        return None

    first_item = seq[0]
    if isinstance(first_item, torch.Tensor):
        return first_item
    return None


def get_pooled_features(model: nn.Module, x: torch.Tensor) -> torch.Tensor:
    """
    Returns a pooled feature vector for a given model and input.
    - For ResNet/MobileNet: global average pool to (batch, features)
    - For ViT/DINOv2: use class token if token output is returned
    - For CLIP: use encode_image (already pooled)
    """
    if hasattr(model, "clip_model") and callable(getattr(model, "forward_features", None)):
        clip_features: object = model.forward_features(x)  # type: ignore[misc]
        if not isinstance(clip_features, torch.Tensor):  # type: ignore[arg-type]
            raise RuntimeError(f"CLIP feature output has unexpected type: {type(clip_features)!r}")  # type: ignore[arg-type]
        return clip_features

    if hasattr(model, "blocks") and callable(getattr(model, "forward_features", None)):
        vit_output: object = model.forward_features(x)  # type: ignore[misc]
        first_tensor = _first_tensor_from_output(vit_output)  # type: ignore[arg-type]
        if first_tensor is not None:
            vit_output = first_tensor

        if isinstance(vit_output, torch.Tensor):
            if vit_output.ndim == 3:
                return vit_output[:, 0]
            if vit_output.ndim == 2:
                return vit_output

    forward_features_fn = getattr(model, "forward_features", None)
    if callable(forward_features_fn):
        output_obj = forward_features_fn(x)
    else:
        output_obj = model(x)

    first_tensor = _first_tensor_from_output(output_obj)
    if first_tensor is not None:
        output_obj = first_tensor

    if isinstance(output_obj, torch.Tensor):
        if output_obj.ndim == 4:
            pooled = torch.nn.functional.adaptive_avg_pool2d(output_obj, (1, 1))
            return pooled.view(pooled.size(0), -1)
        if output_obj.ndim == 2:
            return output_obj
        raise RuntimeError(f"Tensor output has unexpected ndim: {output_obj.ndim}")

    raise RuntimeError(f"Unexpected feature shape: {type(output_obj)!r}")


def _coerce_rgb_triplet(
    value: object,
    default: tuple[float, float, float],
) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return default

    typed_seq: Sequence[Any] = cast(Sequence[Any], value)
    try:
        items: list[float] = [float(x) for x in typed_seq]
    except Exception:
        return default

    if len(items) != 3:
        return default
    return (items[0], items[1], items[2])


def infer_feature_dim(model: nn.Module, input_hw: tuple[int, int], device: torch.device) -> int:
    model.eval()
    with torch.no_grad():
        dummy = torch.zeros((1, 3, input_hw[0], input_hw[1]), device=device)
        output = forward_features(model, dummy)
        return int(output.shape[1])


def build_basic_transform(
    input_size: int,
    is_training: bool,
    mean: Sequence[float] = IMAGENET_MEAN,
    std: Sequence[float] = IMAGENET_STD,
) -> TransformFn:
    interpolation = T.InterpolationMode.BICUBIC
    if is_training:
        return T.Compose(
            [
                T.Resize((input_size, input_size), interpolation=interpolation),
                T.RandomAffine(
                    degrees=0,
                    translate=(0.03, 0.03),
                    scale=(0.97, 1.03),
                    interpolation=interpolation,
                ),
                T.ColorJitter(
                    brightness=0.08,
                    contrast=0.08,
                    saturation=0.03,
                    hue=0.00,
                ),
                T.ToTensor(),
                T.Normalize(mean=tuple(mean), std=tuple(std)),
                T.RandomErasing(
                    p=0.05,
                    scale=(0.01, 0.04),
                    ratio=(0.80, 1.25),
                ),
            ]
        )

    return T.Compose(
        [
            T.Resize((input_size, input_size), interpolation=interpolation),
            T.ToTensor(),
            T.Normalize(mean=tuple(mean), std=tuple(std)),
        ]
    )


def build_dinov2_transform(
    input_size: int,
    is_training: bool,
    mean: Sequence[float] = IMAGENET_MEAN,
    std: Sequence[float] = IMAGENET_STD,
) -> TransformFn:
    interpolation = T.InterpolationMode.BICUBIC
    if is_training:
        return T.Compose(
            [
                T.Resize((input_size, input_size), interpolation=interpolation),
                T.RandomAffine(
                    degrees=0,
                    translate=(0.015, 0.015),
                    scale=(0.985, 1.015),
                    interpolation=interpolation,
                ),
                T.ColorJitter(
                    brightness=0.030,
                    contrast=0.035,
                    saturation=0.010,
                    hue=0.00,
                ),
                T.RandomAutocontrast(p=0.12),
                T.RandomAdjustSharpness(sharpness_factor=1.12, p=0.16),
                T.ToTensor(),
                T.Normalize(mean=tuple(mean), std=tuple(std)),
            ]
        )

    return T.Compose(
        [
            T.Resize((input_size, input_size), interpolation=interpolation),
            T.ToTensor(),
            T.Normalize(mean=tuple(mean), std=tuple(std)),
        ]
    )


def build_ssl_boost_transform(
    input_size: int,
    is_training: bool,
    mean: Sequence[float] = IMAGENET_MEAN,
    std: Sequence[float] = IMAGENET_STD,
) -> TransformFn:
    interpolation = T.InterpolationMode.BICUBIC
    if is_training:
        return T.Compose(
            [
                T.Resize((input_size, input_size), interpolation=interpolation),
                T.RandomAffine(
                    degrees=0,
                    translate=(0.025, 0.025),
                    scale=(0.975, 1.025),
                    interpolation=interpolation,
                ),
                T.ColorJitter(
                    brightness=0.05,
                    contrast=0.06,
                    saturation=0.02,
                    hue=0.00,
                ),
                T.RandomAutocontrast(p=0.12),
                T.RandomApply(
                    [T.GaussianBlur(kernel_size=3, sigma=(0.05, 0.35))],
                    p=0.10,
                ),
                T.ToTensor(),
                T.Normalize(mean=tuple(mean), std=tuple(std)),
                T.RandomErasing(
                    p=0.04,
                    scale=(0.008, 0.03),
                    ratio=(0.85, 1.20),
                ),
            ]
        )

    return T.Compose(
        [
            T.Resize((input_size, input_size), interpolation=interpolation),
            T.ToTensor(),
            T.Normalize(mean=tuple(mean), std=tuple(std)),
        ]
    )


def safe_pretrained_stats(
    model: nn.Module,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    cfg_obj = getattr(model, "pretrained_cfg", None)

    cfg_map: dict[str, object] = {}
    if isinstance(cfg_obj, Mapping):
        raw_items: Sequence[tuple[Any, Any]] = tuple(cfg_obj.items())  # type: ignore
        cfg_map = {str(key): value for key, value in raw_items}  # type: ignore

    mean_obj: object = cfg_map.get("mean", IMAGENET_MEAN)
    std_obj: object = cfg_map.get("std", IMAGENET_STD)

    mean = _coerce_rgb_triplet(mean_obj, IMAGENET_MEAN)
    std = _coerce_rgb_triplet(std_obj, IMAGENET_STD)
    return mean, std


def _tensor_label_collate(
    batch: Sequence[tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]],
) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
    images = torch.stack([sample[0] for sample in batch], dim=0)
    labels = torch.stack([sample[1] for sample in batch], dim=0)
    task_names = sorted({task_name for sample in batch for task_name in sample[2]})
    aux_targets = {
        task_name: torch.stack([sample[2][task_name] for sample in batch], dim=0)
        for task_name in task_names
    }
    return images, labels, aux_targets


def forward_features(model: nn.Module, x: torch.Tensor) -> torch.Tensor:
    return get_pooled_features(model, x)


def _freeze_batchnorm_stats(module: nn.Module) -> None:
    for submodule in module.modules():
        if isinstance(submodule, _BATCHNORM_TYPES):
            submodule.eval()
            for param in submodule.parameters():
                param.requires_grad = False


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if float(denominator) > 0.0 else 0.0


def _set_global_seed(seed: int) -> None:
    normalized_seed = int(seed)
    random.seed(normalized_seed)
    torch.manual_seed(normalized_seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(normalized_seed)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def batch_hard_triplet_loss(
    features: torch.Tensor,
    labels: torch.Tensor,
    margin: float = 0.4,
) -> torch.Tensor:
    if features.ndim != 2 or labels.ndim != 1 or int(features.shape[0]) < 3:
        return torch.zeros((), device=features.device, dtype=features.dtype)

    distances = torch.cdist(features, features, p=2)
    batch_size = int(features.shape[0])
    losses: list[torch.Tensor] = []

    for i in range(batch_size):
        label_i = labels[i]
        positive_mask = labels == label_i
        positive_mask[i] = False
        negative_mask = labels != label_i
        if not bool(torch.any(positive_mask)) or not bool(torch.any(negative_mask)):
            continue
        hardest_positive = torch.max(distances[i][positive_mask])
        hardest_negative = torch.min(distances[i][negative_mask])
        losses.append(F.relu(hardest_positive - hardest_negative + margin))

    if not losses:
        return torch.zeros((), device=features.device, dtype=features.dtype)
    return torch.stack(losses).mean()


def probability_gap_penalty(
    logits: torch.Tensor,
    *,
    target_gap: float = 0.24,
) -> torch.Tensor:
    if logits.ndim != 2 or int(logits.shape[0]) == 0 or int(logits.shape[1]) != 2:
        return torch.zeros((), device=logits.device, dtype=logits.dtype)
    probabilities = torch.softmax(logits, dim=-1)
    gaps = torch.abs(probabilities[:, 0] - probabilities[:, 1])
    excess_gap = F.relu(gaps - float(max(target_gap, 0.0)))
    return torch.mean(excess_gap.square())


def mean_probability_gap(logits: torch.Tensor) -> float:
    if logits.ndim != 2 or int(logits.shape[0]) == 0 or int(logits.shape[1]) != 2:
        return 0.0
    probabilities = torch.softmax(logits.detach(), dim=-1)
    gaps = torch.abs(probabilities[:, 0] - probabilities[:, 1])
    return float(gaps.mean().item())


class EnsembleCVModels:
    MODEL_SPECS: Final[dict[str, dict[str, str]]] = {
        "dinov2": {"mode": "supervised_metric", "save_name": "dinov2_finetuned.pkl"},
        "mobilenetv3": {"mode": "supervised_metric", "save_name": "mobilenetv3_finetuned.pkl"},
        "simclr": {"mode": "ssl_then_supervised", "save_name": "simclr_finetuned.pkl"},
        "byol": {"mode": "ssl_then_supervised", "save_name": "byol_finetuned.pkl"},
        "swav": {"mode": "ssl_then_supervised", "save_name": "swav_finetuned.pkl"},
        "clip": {"mode": "clip_image_classifier", "save_name": "clip_finetuned.pkl"},
    }

    def __init__(
        self,
        image_dirs: list[str],
        device: torch.device,
        target_models: list[str],
        sequence_manifest_path: str | None = None,
        sequence_aux_loss_weight: float = 0.30,
        sequence_manifest_quality: str = "exclude_contradictory",
        random_seed: int = 1337,
        pretrained_backbones: bool = True,
        enable_continual_learning: bool = True,
    ):
        self.image_dirs: list[str] = image_dirs
        self.device: torch.device = device
        self.target_models: list[str] = target_models
        self.random_seed: int = int(random_seed)
        self.pretrained_backbones: bool = bool(pretrained_backbones)
        self.enable_continual_learning: bool = bool(enable_continual_learning)
        self.feature_dims: dict[str, int] = {}
        self.best_val_accuracy: dict[str, float] = {}
        self.train_transforms: dict[str, TransformFn] = {}
        self.eval_transforms: dict[str, TransformFn] = {}
        self.models: dict[str, nn.Module] = {}
        self.heads: dict[str, nn.Module] = {}
        self.training_history: dict[str, list[dict[str, float]]] = {}
        self.evaluation_metrics: dict[str, dict[str, Any]] = {}
        self.class_weights_by_model: dict[str, list[float]] = {}
        self.temperature_scalers: dict[str, float] = {}
        self.decision_thresholds: dict[str, float] = {}
        self.sequence_manifest_path: str | None = None
        self.sequence_aux_loss_weight: float = float(max(sequence_aux_loss_weight, 0.0))
        self.sequence_manifest_quality: str = _normalize_sequence_manifest_quality(sequence_manifest_quality)
        self.sequence_targets_by_path: dict[str, dict[str, int]] = {}
        self.sequence_task_values: dict[str, list[str]] = {}
        self.sequence_task_spaces: dict[str, dict[str, int]] = {}
        self.sequence_aux_heads: dict[str, SequenceAuxiliaryHead] = {}
        self.sequence_aux_metrics: dict[str, dict[str, Any]] = {}
        self.sequence_manifest_record_count: int = 0
        self.sequence_manifest_alias_key_count: int = 0
        self.sequence_manifest_filter_stats: dict[str, Any] = {}
        self.clip_preprocess: TransformFn | None = None
        self.continual_states: dict[str, ContinualTrainingState] = {}
        _set_global_seed(self.random_seed)
        if sequence_manifest_path:
            self._load_sequence_manifest(sequence_manifest_path)

    @staticmethod
    def _artifact_score(
        metrics: Mapping[str, Any] | None,
    ) -> tuple[float, float, float, float]:
        if not isinstance(metrics, Mapping):
            return (0.0, 0.0, 0.0, 0.0)
        validation_accuracy = float(metrics.get("validation_accuracy", metrics.get("accuracy", 0.0)) or 0.0)
        balanced_accuracy = float(metrics.get("balanced_accuracy", 0.0) or 0.0)
        macro_f1 = float(metrics.get("macro_f1", 0.0) or 0.0)
        buy_recall = float(metrics.get("buy_recall", 0.0) or 0.0)
        sell_recall = float(metrics.get("sell_recall", 0.0) or 0.0)
        return (
            balanced_accuracy,
            validation_accuracy,
            macro_f1,
            min(buy_recall, sell_recall),
        )

    def _load_existing_metadata(self, model_dir: Path, name: str) -> dict[str, Any] | None:
        metadata_path = model_dir / f"{name}_metadata.json"
        if not metadata_path.exists():
            return None
        try:
            with metadata_path.open("r", encoding="utf-8") as metadata_file:
                payload = json.load(metadata_file)
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _ordered_sequence_task_names(self, task_names: Sequence[str]) -> list[str]:
        priority_index = {name: idx for idx, name in enumerate(_SEQUENCE_TASK_PRIORITY)}
        return sorted(
            {str(task_name) for task_name in task_names},
            key=lambda name: (priority_index.get(name, len(priority_index)), name),
        )

    def _read_sequence_manifest_records(self, manifest_path: Path) -> list[dict[str, Any]]:
        suffix = manifest_path.suffix.lower()
        if suffix == ".jsonl":
            records: list[dict[str, Any]] = []
            with manifest_path.open("r", encoding="utf-8") as manifest_file:
                for line in manifest_file:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        records.append(payload)
            return records

        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            payload = json.load(manifest_file)

        if isinstance(payload, list):
            return [record for record in payload if isinstance(record, dict)]
        if isinstance(payload, dict):
            records_obj = payload.get("records", payload.get("items", []))
            if isinstance(records_obj, list):
                return [record for record in records_obj if isinstance(record, dict)]
        raise ValueError(f"Unsupported sequence manifest format: {manifest_path}")

    def _load_sequence_manifest(self, manifest_path: str) -> None:
        manifest = Path(manifest_path).expanduser()
        if not manifest.is_absolute():
            manifest = Path.cwd() / manifest
        manifest = manifest.resolve()
        if not manifest.exists():
            raise FileNotFoundError(f"Sequence teacher manifest not found: {manifest}")

        raw_targets_by_path: dict[str, dict[str, str]] = {}
        task_values: dict[str, set[str]] = defaultdict(set)

        manifest_records = self._read_sequence_manifest_records(manifest)
        validate_directional_teacher_consistency(manifest_records)
        self.sequence_manifest_record_count = len(manifest_records)
        filter_stats: dict[str, Any] = {
            "mode": self.sequence_manifest_quality,
            "record_count": int(self.sequence_manifest_record_count),
            "retained_records": 0,
            "skipped_records": 0,
            "skip_reasons": {},
        }

        for record in manifest_records:
            image_path_obj = record.get("image_path", record.get("destination_path", record.get("path")))
            if not isinstance(image_path_obj, str) or not image_path_obj.strip():
                continue
            include_record, filter_reason = _sequence_record_filter_decision(
                record,
                quality_mode=self.sequence_manifest_quality,
            )
            if not include_record:
                filter_stats["skipped_records"] = int(filter_stats["skipped_records"]) + 1
                skip_reasons = cast(dict[str, int], filter_stats["skip_reasons"])
                skip_reasons[str(filter_reason)] = int(skip_reasons.get(str(filter_reason), 0)) + 1
                continue

            sequence_targets_obj = record.get("sequence_targets", {})
            if not isinstance(sequence_targets_obj, Mapping):
                continue

            normalized_targets: dict[str, str] = {}
            for task_name_raw, task_value_raw in sequence_targets_obj.items():
                task_name = str(task_name_raw).strip()
                if not task_name:
                    continue
                normalized_value = _normalize_sequence_value(task_name, task_value_raw)
                if normalized_value is None:
                    continue
                normalized_targets[task_name] = normalized_value
                task_values[task_name].add(normalized_value)

            if normalized_targets:
                filter_stats["retained_records"] = int(filter_stats["retained_records"]) + 1
                for normalized_path in _path_key_aliases(image_path_obj):
                    raw_targets_by_path[normalized_path] = dict(normalized_targets)

        self.sequence_task_values = {}
        self.sequence_task_spaces = {}
        for task_name in self._ordered_sequence_task_names(list(task_values.keys())):
            values = sorted(task_values[task_name])
            if len(values) < 2:
                continue
            self.sequence_task_values[task_name] = values
            self.sequence_task_spaces[task_name] = {
                value: idx
                for idx, value in enumerate(values)
            }

        self.sequence_targets_by_path = {}
        for normalized_path, target_map in raw_targets_by_path.items():
            encoded_targets = {
                task_name: int(self.sequence_task_spaces[task_name][target_value])
                for task_name, target_value in target_map.items()
                if task_name in self.sequence_task_spaces and target_value in self.sequence_task_spaces[task_name]
            }
            if encoded_targets:
                self.sequence_targets_by_path[normalized_path] = encoded_targets
        self.sequence_manifest_alias_key_count = len(self.sequence_targets_by_path)

        self.sequence_manifest_path = str(manifest)
        self.sequence_manifest_filter_stats = filter_stats
        if self.sequence_task_values:
            filter_suffix = ""
            skipped_records = int(filter_stats.get("skipped_records", 0))
            if skipped_records > 0:
                filter_suffix = (
                    f" | retained={int(filter_stats.get('retained_records', 0))}"
                    f" skipped={skipped_records}"
                    f" mode={self.sequence_manifest_quality}"
                )
            print(
                "[SEQUENCE TEACHER] Loaded "
                f"{self.sequence_manifest_record_count} manifest rows mapped to "
                f"{self.sequence_manifest_alias_key_count} path keys across "
                f"{len(self.sequence_task_values)} tasks from {manifest}"
                f"{filter_suffix}"
            )
        else:
            print(
                "[SEQUENCE TEACHER] Manifest loaded but no multi-class auxiliary tasks were found "
                f"in {manifest}"
            )

    def _choose_dinov2_name(self) -> str:
        preferred = [
            "vit_small_patch14_reg4_dinov2.lvd142m",
            "vit_base_patch14_reg4_dinov2.lvd142m",
            "vit_small_patch14_dinov2.lvd142m",
            "vit_base_patch14_dinov2.lvd142m",
        ]
        available = set(timm.list_models(pretrained=True))
        for name in preferred:
            if name in available:
                return name
        return "vit_small_patch14_dinov2.lvd142m"

    def _create_timm_backbone(self, model_name: str, *, dynamic_img_size: bool = False) -> nn.Module:
        try:
            backbone = timm.create_model(
                model_name,
                pretrained=self.pretrained_backbones,
                num_classes=0,
                dynamic_img_size=dynamic_img_size,
            )
        except TypeError:
            backbone = timm.create_model(model_name, pretrained=self.pretrained_backbones, num_classes=0)
        return backbone.to(self.device)

    def _init_models(self) -> None:
        failed_models: list[str] = []

        def _make_transforms(
            cfg: ModelTrainConfig,
            mean: Sequence[float],
            std: Sequence[float],
            clip_preprocess: TransformFn | None = None,
        ) -> tuple[TransformFn, TransformFn]:
            profile = str(cfg.transform_profile).strip().lower()
            if profile == "ssl_boost":
                train_tf = build_ssl_boost_transform(
                    cfg.input_size,
                    is_training=True,
                    mean=mean,
                    std=std,
                )
                eval_tf = build_ssl_boost_transform(
                    cfg.input_size,
                    is_training=False,
                    mean=mean,
                    std=std,
                )
            else:
                train_tf = build_basic_transform(
                    cfg.input_size,
                    is_training=True,
                    mean=mean,
                    std=std,
                )
                eval_tf = build_basic_transform(
                    cfg.input_size,
                    is_training=False,
                    mean=mean,
                    std=std,
                )
            if clip_preprocess is not None:
                return train_tf, clip_preprocess
            return train_tf, eval_tf

        if "dinov2" in self.target_models:
            dino_name = self._choose_dinov2_name()
            dino_backbone = self._create_timm_backbone(dino_name, dynamic_img_size=True)
            dino_backbone.eval()
            dino_model = DinoV2FeatureAdapter(dino_backbone, use_patch_mean=True).to(self.device)
            dino_model.eval()
            dino_mean, dino_std = safe_pretrained_stats(dino_backbone)
            dino_cfg = TRAIN_CONFIGS["dinov2"]
            train_tf = build_dinov2_transform(
                dino_cfg.input_size,
                is_training=True,
                mean=dino_mean,
                std=dino_std,
            )
            eval_tf = build_dinov2_transform(
                dino_cfg.input_size,
                is_training=False,
                mean=dino_mean,
                std=dino_std,
            )
            self.train_transforms["dinov2"] = train_tf
            self.eval_transforms["dinov2"] = eval_tf
            self.models["dinov2"] = dino_model
            self.feature_dims["dinov2"] = infer_feature_dim(
                dino_model,
                (dino_cfg.input_size, dino_cfg.input_size),
                self.device,
            )

        if "mobilenetv3" in self.target_models:
            mobilenet_model = self._create_timm_backbone("mobilenetv3_large_100", dynamic_img_size=False)
            mobilenet_model.eval()
            mnet_mean, mnet_std = safe_pretrained_stats(mobilenet_model)
            mnet_cfg = TRAIN_CONFIGS["mobilenetv3"]
            train_tf, eval_tf = _make_transforms(mnet_cfg, mnet_mean, mnet_std)
            self.train_transforms["mobilenetv3"] = train_tf
            self.eval_transforms["mobilenetv3"] = eval_tf
            self.models["mobilenetv3"] = mobilenet_model
            self.feature_dims["mobilenetv3"] = infer_feature_dim(
                mobilenet_model,
                (mnet_cfg.input_size, mnet_cfg.input_size),
                self.device,
            )

        if "simclr" in self.target_models:
            try:
                print("[INIT] simclr")
                simclr_backbone = self._create_timm_backbone("resnet50", dynamic_img_size=False)
                simclr_backbone.eval()
                simclr_cfg = TRAIN_CONFIGS["simclr"]
                simclr_mean, simclr_std = safe_pretrained_stats(simclr_backbone)
                train_tf, eval_tf = _make_transforms(simclr_cfg, simclr_mean, simclr_std)
                self.train_transforms["simclr"] = train_tf
                self.eval_transforms["simclr"] = eval_tf
                simclr_feature_dim = 2048
                simclr_model = LightlySimCLR(
                    simclr_backbone,
                    feature_dim=simclr_feature_dim,
                    proj_dim=128,
                ).to(self.device)
                simclr_model.eval()
                self.models["simclr"] = simclr_model
                self.feature_dims["simclr"] = simclr_feature_dim
                print("[OK] simclr")
            except Exception as exc:
                print(f"[FAILED] simclr: {exc}")
                failed_models.append("simclr")

        if "byol" in self.target_models:
            try:
                print("[INIT] byol")
                byol_backbone = TimmBackbone(
                    "resnet50",
                    pretrained=self.pretrained_backbones,
                ).to(self.device)
                byol_backbone.eval()
                byol_cfg = TRAIN_CONFIGS["byol"]
                byol_mean, byol_std = safe_pretrained_stats(byol_backbone.model)
                train_tf, eval_tf = _make_transforms(byol_cfg, byol_mean, byol_std)
                self.train_transforms["byol"] = train_tf
                self.eval_transforms["byol"] = eval_tf
                byol_feature_dim = 2048
                byol_model = LightlyBYOL(
                    byol_backbone,
                    feature_dim=byol_feature_dim,
                    proj_dim=256,
                ).to(self.device)
                byol_model.eval()
                self.models["byol"] = byol_model
                self.feature_dims["byol"] = byol_feature_dim
                print("[OK] byol")
            except Exception as exc:
                print(f"[FAILED] byol: {exc}")
                failed_models.append("byol")

        if "swav" in self.target_models:
            try:
                print("[INIT] swav")
                swav_backbone = TimmBackbone(
                    "resnet50",
                    pretrained=self.pretrained_backbones,
                ).to(self.device)
                swav_backbone.eval()
                swav_cfg = TRAIN_CONFIGS["swav"]
                swav_mean, swav_std = safe_pretrained_stats(swav_backbone.model)
                train_tf, eval_tf = _make_transforms(swav_cfg, swav_mean, swav_std)
                self.train_transforms["swav"] = train_tf
                self.eval_transforms["swav"] = eval_tf
                swav_feature_dim = 2048
                swav_model = LightlySwaV(
                    swav_backbone,
                    feature_dim=swav_feature_dim,
                    proj_dim=128,
                    n_prototypes=512,
                ).to(self.device)
                swav_model.eval()
                self.models["swav"] = swav_model
                self.feature_dims["swav"] = swav_feature_dim
                print("[OK] swav")
            except Exception as exc:
                print(f"[FAILED] swav: {exc}")
                failed_models.append("swav")

        if "clip" in self.target_models:
            try:
                clip_module = clip
                if not _has_clip or clip_module is None:
                    raise ImportError("clip not available")
                clip_loaded = cast(tuple[Any, TransformFn], clip_module.load("ViT-B/32", device=self.device))
                clip_model_any, clip_preprocess = clip_loaded
                clip_model = clip_model_any
                clip_model.eval()
                clip_wrapper = ClipImageEncoder(clip_model).to(self.device)
                self.models["clip"] = clip_wrapper
                self.feature_dims["clip"] = int(clip_wrapper.num_features)
                train_tf, eval_tf = _make_transforms(
                    TRAIN_CONFIGS["clip"],
                    CLIP_MEAN,
                    CLIP_STD,
                    clip_preprocess,
                )
                self.train_transforms["clip"] = train_tf
                self.eval_transforms["clip"] = eval_tf
                self.clip_preprocess = clip_preprocess
            except Exception as exc:
                print(f"[FAILED] clip: {exc}")
                failed_models.append("clip")

        if failed_models:
            print(f"[WARNING] Some models failed to initialize: {failed_models}")

    @staticmethod
    def _label_to_index(label: str) -> int | None:
        normalized = str(label).strip().upper()
        if normalized == "BUY":
            return 0
        if normalized == "SELL":
            return 1
        return None

    def _load_replay_samples(self) -> list[ReplaySample]:
        replay_path = Path(RUNTIME.replay_buffer_path)
        if not replay_path.exists():
            return []
        samples: list[ReplaySample] = []
        try:
            with replay_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = cast(dict[str, Any], json.loads(line))
                    except Exception:
                        continue
                    label = self._label_to_index(str(row.get("verdict", "")))
                    if label is None:
                        continue
                    snapshot_path = str(row.get("snapshot_path", "")).strip()
                    source_path = str(row.get("source_path", "")).strip()
                    image_path = snapshot_path if snapshot_path and os.path.isfile(snapshot_path) else source_path
                    if not image_path or not os.path.isfile(image_path):
                        continue
                    samples.append(
                        ReplaySample(
                            image_path=image_path,
                            label=label,
                            context_key=str(row.get("context_key", "")),
                            adapter_name=sanitize_adapter_name(
                                str(row.get("lora_adapter_name", row.get("context_key", ""))),
                                default="continual_default",
                            ),
                        )
                    )
        except Exception as exc:
            print(f"[CONTINUAL] Replay buffer load failed: {exc}")
            return []
        return samples[-int(max(TRAIN.replay_buffer_size, 1)) :]

    @staticmethod
    def _dominant_replay_context(samples: Sequence[ReplaySample]) -> str:
        counts: dict[str, int] = defaultdict(int)
        for sample in samples:
            if sample.context_key:
                counts[str(sample.context_key)] += 1
        if not counts:
            return ""
        return max(counts.items(), key=lambda item: item[1])[0]

    def _tail_root_paths(self, model: nn.Module, name: str) -> list[str]:
        module = self._resolve_backbone_module(model)
        if name == "dinov2":
            blocks_obj = getattr(module, "blocks", None)
            roots: list[str] = []
            if isinstance(blocks_obj, nn.ModuleList) and len(blocks_obj) > 0:
                start_idx = max(len(blocks_obj) - 2, 0)
                for idx in range(start_idx, len(blocks_obj)):
                    roots.append(f"blocks.{idx}")
            for norm_name in ("norm", "fc_norm"):
                if isinstance(getattr(module, norm_name, None), nn.Module):
                    roots.append(norm_name)
            return roots

        roots = []
        for attr_name in ("layer4",):
            if isinstance(getattr(module, attr_name, None), nn.Module):
                roots.append(attr_name)
        for attr_name in ("stages", "features", "blocks"):
            attr_value = getattr(module, attr_name, None)
            if isinstance(attr_value, (nn.ModuleList, nn.Sequential)) and len(attr_value) > 0:
                start_idx = max(len(attr_value) - 2, 0)
                for idx in range(start_idx, len(attr_value)):
                    roots.append(f"{attr_name}.{idx}")
        if not roots:
            roots.append("")
        return roots

    def _prepare_model_from_payload(
        self,
        model: nn.Module,
        head: nn.Module,
        aux_head: SequenceAuxiliaryHead | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        lora_payload = cast(dict[str, Any], payload.get("lora", {}))
        backbone_module = self._resolve_backbone_module(model)
        if bool(lora_payload.get("enabled", False)):
            target_paths = cast(list[str], lora_payload.get("target_paths", []))
            adapter_specs = cast(dict[str, dict[str, Any]], lora_payload.get("adapter_specs", {}))
            fallback_adapter = sanitize_adapter_name(str(lora_payload.get("active_adapter", "continual_default")))
            spec_info = cast(dict[str, Any], adapter_specs.get(fallback_adapter, {}))
            config = AdapterConfig(
                rank=int(spec_info.get("rank", TRAIN.lora_rank) or TRAIN.lora_rank),
                alpha=float(spec_info.get("alpha", TRAIN.lora_alpha) or TRAIN.lora_alpha),
                dropout=float(spec_info.get("dropout", TRAIN.lora_dropout) or TRAIN.lora_dropout),
            )
            applied = apply_lora_adapters(
                backbone_module,
                adapter_name=fallback_adapter,
                target_paths=target_paths,
                config=config,
            )
            for adapter_name, adapter_spec in adapter_specs.items():
                if sanitize_adapter_name(adapter_name) == fallback_adapter:
                    continue
                apply_lora_adapters(
                    backbone_module,
                    adapter_name=adapter_name,
                    target_paths=target_paths,
                    config=AdapterConfig(
                        rank=int(adapter_spec.get("rank", TRAIN.lora_rank) or TRAIN.lora_rank),
                        alpha=float(adapter_spec.get("alpha", TRAIN.lora_alpha) or TRAIN.lora_alpha),
                        dropout=float(adapter_spec.get("dropout", TRAIN.lora_dropout) or TRAIN.lora_dropout),
                    ),
                )
            set_active_adapter(backbone_module, str(lora_payload.get("active_adapter", fallback_adapter)))
            if not applied["target_paths"]:
                print("[CONTINUAL] LoRA payload present but no target paths could be wrapped before loading.")

        model.load_state_dict(cast(dict[str, Any], payload["backbone_state_dict"]))
        head.load_state_dict(cast(dict[str, Any], payload["head_state_dict"]))
        if aux_head is not None and "aux_head_state_dict" in payload:
            aux_head.load_state_dict(cast(dict[str, Any], payload["aux_head_state_dict"]))
        return lora_payload

    def _named_continual_params(
        self,
        model: nn.Module,
        head: nn.Module,
        aux_head: SequenceAuxiliaryHead | None,
    ) -> list[tuple[str, nn.Parameter]]:
        params: list[tuple[str, nn.Parameter]] = []
        for prefix, module in (("backbone", model), ("head", head), ("aux_head", aux_head)):
            if module is None:
                continue
            for name, param in module.named_parameters():
                params.append((f"{prefix}.{name}", param))
        return params

    def _build_fisher_loader(self, model_name: str, replay_samples: Sequence[ReplaySample]) -> DataLoader[Any]:
        include_replay = bool(replay_samples)
        return self._get_dataloader(
            batch_size=max(1, min(TRAIN_CONFIGS.get(model_name, TRAIN_CONFIGS["mobilenetv3"]).batch_size, 8)),
            model_name=model_name,
            image_dirs=self.image_dirs,
            shuffle=False,
            balanced=False,
            is_training=False,
            include_replay=include_replay,
            replay_samples=replay_samples,
        )

    def _estimate_fisher_diagonal(
        self,
        model: nn.Module,
        head: nn.Module,
        aux_head: SequenceAuxiliaryHead | None,
        name: str,
        replay_samples: Sequence[ReplaySample],
    ) -> tuple[dict[str, torch.Tensor], dict[str, torch.Tensor]]:
        named_params = self._named_continual_params(model, head, aux_head)
        reference_params = {
            param_name: param.detach().clone().to(self.device)
            for param_name, param in named_params
        }
        fisher = {
            param_name: torch.zeros_like(param.detach(), device=self.device)
            for param_name, param in named_params
        }
        loader = self._build_fisher_loader(name, replay_samples)
        max_batches = int(max(TRAIN.ewc_fisher_batches, 1))
        model.eval()
        head.eval()
        if aux_head is not None:
            aux_head.eval()

        for batch_idx, (imgs, labels, aux_targets) in enumerate(loader):
            if batch_idx >= max_batches:
                break
            imgs = imgs.to(self.device, non_blocking=True)
            labels = labels.to(self.device, non_blocking=True)
            aux_targets = {
                task_name: targets.to(self.device, non_blocking=True)
                for task_name, targets in aux_targets.items()
            }
            model.zero_grad(set_to_none=True)
            head.zero_grad(set_to_none=True)
            if aux_head is not None:
                aux_head.zero_grad(set_to_none=True)
            features = forward_features(model, imgs)
            features_for_head = F.normalize(features, p=2, dim=1) if name == "dinov2" else features
            logits = head(features_for_head)
            loss = F.cross_entropy(logits, labels)
            aux_loss, _, aux_count = self._compute_auxiliary_loss(
                aux_head,
                features_for_head,
                aux_targets,
                label_smoothing=float(TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"]).aux_label_smoothing),
            )
            if aux_count > 0:
                loss = loss + 0.10 * aux_loss
            loss.backward()
            for param_name, param in named_params:
                if param.grad is None:
                    continue
                fisher[param_name] = fisher[param_name] + param.grad.detach().pow(2)

        denom = float(max(min(len(loader), max_batches), 1))
        fisher = {
            param_name: (value / denom).detach().clone()
            for param_name, value in fisher.items()
        }
        model.zero_grad(set_to_none=True)
        head.zero_grad(set_to_none=True)
        if aux_head is not None:
            aux_head.zero_grad(set_to_none=True)
        return reference_params, fisher

    def _ewc_penalty(
        self,
        state: ContinualTrainingState,
        model: nn.Module,
        head: nn.Module,
        aux_head: SequenceAuxiliaryHead | None,
    ) -> torch.Tensor:
        penalty = torch.zeros((), device=self.device, dtype=torch.float32)
        if not state.reference_params or not state.fisher_diagonal:
            return penalty
        for param_name, param in self._named_continual_params(model, head, aux_head):
            reference = state.reference_params.get(param_name)
            fisher = state.fisher_diagonal.get(param_name)
            if reference is None or fisher is None:
                continue
            penalty = penalty + torch.sum(fisher.to(param.device) * (param - reference.to(param.device)).pow(2))
        return penalty

    @staticmethod
    def _distillation_loss(
        student_logits: torch.Tensor,
        teacher_logits: torch.Tensor,
        *,
        temperature: float,
    ) -> torch.Tensor:
        temp = float(max(temperature, 1.0))
        student_log_probs = F.log_softmax(student_logits / temp, dim=-1)
        teacher_probs = F.softmax(teacher_logits / temp, dim=-1)
        return F.kl_div(student_log_probs, teacher_probs, reduction="batchmean") * (temp ** 2)

    def _prepare_continual_state(
        self,
        *,
        name: str,
        model: nn.Module,
        head: nn.Module,
        aux_head: SequenceAuxiliaryHead | None,
        model_dir: Path,
    ) -> ContinualTrainingState:
        state = ContinualTrainingState()
        if not self.enable_continual_learning:
            self.continual_states[name] = state
            return state
        bundle_path = model_dir / self.MODEL_SPECS[name]["save_name"]
        replay_samples = self._load_replay_samples()
        state.replay_samples = replay_samples
        state.dominant_context_key = self._dominant_replay_context(replay_samples)
        if not bundle_path.exists():
            self.continual_states[name] = state
            return state

        try:
            try:
                payload = torch.load(bundle_path, map_location=self.device, weights_only=False)
            except TypeError:
                payload = torch.load(bundle_path, map_location=self.device)
        except Exception as exc:
            print(f"[CONTINUAL] Failed to load previous bundle for {name}: {exc}")
            self.continual_states[name] = state
            return state

        lora_payload = self._prepare_model_from_payload(model, head, aux_head, cast(dict[str, Any], payload))
        adapter_name = sanitize_adapter_name(state.dominant_context_key, default="continual_default")
        backbone_module = self._resolve_backbone_module(model)
        target_paths = cast(list[str], lora_payload.get("target_paths", []))
        if not target_paths:
            target_paths = collect_adaptable_module_paths(
                backbone_module,
                root_paths=self._tail_root_paths(model, name),
                include_conv2d=True,
            )
        if target_paths:
            apply_lora_adapters(
                backbone_module,
                adapter_name=adapter_name,
                target_paths=target_paths,
                config=AdapterConfig(
                    rank=int(TRAIN.lora_rank),
                    alpha=float(TRAIN.lora_alpha),
                    dropout=float(TRAIN.lora_dropout),
                ),
            )
            set_active_adapter(backbone_module, adapter_name)
            state.used_lora = True
            state.lora_target_paths = list(target_paths)
            state.adapter_name = adapter_name
        else:
            state.adapter_name = sanitize_adapter_name(str(lora_payload.get("active_adapter", "continual_default")))

        state.enabled = True
        state.previous_bundle_path = str(bundle_path)
        teacher_model = copy.deepcopy(model).to(self.device)
        teacher_head = copy.deepcopy(head).to(self.device)
        teacher_aux_head = copy.deepcopy(aux_head).to(self.device) if aux_head is not None else None
        teacher_model.eval()
        teacher_head.eval()
        if teacher_aux_head is not None:
            teacher_aux_head.eval()
        for module in (teacher_model, teacher_head, teacher_aux_head):
            if module is None:
                continue
            for param in module.parameters():
                param.requires_grad = False
        state.teacher_model = teacher_model
        state.teacher_head = teacher_head
        state.teacher_aux_head = teacher_aux_head
        state.reference_params, state.fisher_diagonal = self._estimate_fisher_diagonal(
            model,
            head,
            aux_head,
            name,
            replay_samples,
        )
        self.continual_states[name] = state
        return state

    def _save_adapter_sidecar(
        self,
        *,
        model: nn.Module,
        name: str,
        adapter_name: str,
        target_paths: Sequence[str],
    ) -> str:
        if not adapter_name:
            return ""
        adapter_state = {
            key: value.detach().cpu()
            for key, value in model.state_dict().items()
            if f".adapters.{adapter_name}." in key
        }
        if not adapter_state:
            return ""
        adapter_path = Path(RUNTIME.adapters_dir) / f"{name}_{adapter_name}.pt"
        torch.save(
            {
                "model_name": name,
                "adapter_name": adapter_name,
                "target_paths": list(target_paths),
                "state_dict": adapter_state,
            },
            adapter_path,
        )
        return str(adapter_path)

    def _register_context_adapter_mapping(
        self,
        *,
        context_key: str,
        adapter_name: str,
        adapter_file: str,
    ) -> None:
        if not context_key or not adapter_name:
            return
        payload: dict[str, Any] = {}
        try:
            if Path(RUNTIME.adapter_bank_path).exists():
                with Path(RUNTIME.adapter_bank_path).open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                    if isinstance(raw, dict):
                        payload = cast(dict[str, Any], raw)
        except Exception:
            payload = {}
        profile = dict(cast(dict[str, Any], payload.get(context_key, {})))
        profile["lora_adapter_name"] = str(adapter_name)
        if adapter_file:
            profile["lora_adapter_file"] = str(adapter_file)
        payload[str(context_key)] = profile
        with Path(RUNTIME.adapter_bank_path).open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)

    def _build_dataset(
        self,
        model_name: str,
        image_dirs: Sequence[str] | None = None,
        is_training: bool = True,
        include_replay: bool = False,
        replay_samples: Sequence[ReplaySample] | None = None,
    ) -> Dataset[tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]]:
        cfg = TRAIN_CONFIGS.get(model_name, TRAIN_CONFIGS["mobilenetv3"])
        transform = self.train_transforms.get(model_name) if is_training else self.eval_transforms.get(model_name)
        if transform is None:
            transform = build_basic_transform(cfg.input_size, is_training=is_training)
        base_dataset = ChartImageDataset(
            list(image_dirs) if image_dirs is not None else self.image_dirs,
            transform=transform,
            sequence_targets_by_path=self.sequence_targets_by_path,
            sequence_task_spaces=self.sequence_task_spaces,
        )
        if not include_replay:
            return base_dataset
        replay_dataset = ReplayChartDataset(
            list(replay_samples or []),
            transform=transform,
            sequence_task_spaces=self.sequence_task_spaces,
        )
        if len(replay_dataset) == 0:
            return base_dataset
        return MergedChartDataset([base_dataset, replay_dataset])

    def _make_sampler(self, dataset: ChartImageDataset, model_name: str) -> WeightedRandomSampler:
        cfg = TRAIN_CONFIGS.get(model_name, TRAIN_CONFIGS["mobilenetv3"])
        power = float(max(cfg.sampler_power, 0.0))

        class_counts: dict[int, int] = {}
        for label in dataset.labels:
            class_counts[int(label)] = class_counts.get(int(label), 0) + 1

        if power <= 0.0:
            weights = [1.0 for _ in dataset.labels]
        else:
            weights = [
                (1.0 / float(max(class_counts[int(label)], 1))) ** power
                for label in dataset.labels
            ]

        return WeightedRandomSampler(weights, num_samples=len(weights), replacement=True)

    def _get_dataloader(
        self,
        batch_size: int = 16,
        model_name: str = "dinov2",
        image_dirs: Sequence[str] | None = None,
        shuffle: bool = True,
        balanced: bool = False,
        is_training: bool | None = None,
        include_replay: bool = False,
        replay_samples: Sequence[ReplaySample] | None = None,
    ) -> DataLoader[Any]:
        dirs = list(image_dirs) if image_dirs is not None else list(self.image_dirs)

        if len(dirs) == 1 and os.path.isdir(dirs[0]):
            subdirs = [
                os.path.join(dirs[0], entry)
                for entry in os.listdir(dirs[0])
                if os.path.isdir(os.path.join(dirs[0], entry))
            ]
            if len(subdirs) > 0:
                dirs = subdirs

        if is_training is None:
            is_training = shuffle

        dataset = self._build_dataset(
            model_name=model_name,
            image_dirs=dirs,
            is_training=is_training,
            include_replay=include_replay,
            replay_samples=replay_samples,
        )

        common_kwargs: dict[str, Any] = {
            "batch_size": batch_size,
            "num_workers": 0,
            "pin_memory": torch.cuda.is_available(),
            "collate_fn": _tensor_label_collate,
        }

        if balanced and is_training:
            sampler = self._make_sampler(cast(ChartImageDataset, dataset), model_name=model_name)
            return DataLoader(dataset, sampler=sampler, **common_kwargs)

        return DataLoader(dataset, shuffle=is_training, **common_kwargs)

    def _ensure_head(self, name: str) -> nn.Module:
        if name not in self.heads:
            in_features = int(self.feature_dims.get(name, 768))
            if name == "dinov2":
                hidden_features = max(512, in_features // 2)
                self.heads[name] = nn.Sequential(
                    nn.LayerNorm(in_features),
                    nn.Dropout(p=0.08),
                    nn.Linear(in_features, hidden_features),
                    nn.GELU(),
                    nn.Dropout(p=0.12),
                    nn.Linear(hidden_features, 2),
                ).to(self.device)
            else:
                self.heads[name] = nn.Sequential(
                    nn.LayerNorm(in_features),
                    nn.Dropout(p=0.25),
                    nn.Linear(in_features, 2),
                ).to(self.device)
        return self.heads[name]

    def _ensure_aux_head(self, name: str) -> SequenceAuxiliaryHead | None:
        if not self.sequence_task_values or self.sequence_aux_loss_weight <= 0.0:
            return None
        if name not in self.sequence_aux_heads:
            in_features = int(self.feature_dims.get(name, 768))
            dropout = 0.10 if name == "dinov2" else 0.14
            self.sequence_aux_heads[name] = SequenceAuxiliaryHead(
                in_features,
                self.sequence_task_values,
                dropout=dropout,
            ).to(self.device)
        return self.sequence_aux_heads[name]

    def _effective_aux_loss_weight(self, name: str, stage_label: str) -> float:
        cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
        normalized_stage = str(stage_label).strip().lower()

        if normalized_stage == "head_only":
            scale = float(cfg.head_only_aux_loss_scale)
        elif normalized_stage == "full":
            scale = float(cfg.full_aux_loss_scale)
        else:
            scale = float(cfg.stage2_aux_loss_scale)

        return float(max(self.sequence_aux_loss_weight, 0.0)) * max(scale, 0.0)

    def _epoch_aux_loss_weight(
        self,
        name: str,
        stage_label: str,
        epoch_index: int,
        total_epochs: int,
    ) -> float:
        base_weight = self._effective_aux_loss_weight(name, stage_label)
        if base_weight <= 0.0 or total_epochs <= 1:
            return base_weight

        cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
        normalized_stage = str(stage_label).strip().lower()

        if normalized_stage == "head_only":
            return base_weight
        if normalized_stage == "full":
            floor = float(cfg.full_aux_decay_floor)
        else:
            floor = float(cfg.stage2_aux_decay_floor)

        floor = min(max(floor, 0.0), 1.0)
        progress = float(epoch_index) / float(max(total_epochs - 1, 1))
        decay_scale = 1.0 - (1.0 - floor) * progress
        return float(base_weight) * float(decay_scale)

    def _set_aux_head_trainable(self, name: str, trainable: bool) -> SequenceAuxiliaryHead | None:
        aux_head = self._ensure_aux_head(name)
        if aux_head is None:
            return None

        for param in aux_head.parameters():
            param.requires_grad = bool(trainable)
        if bool(trainable):
            aux_head.train()
        else:
            aux_head.eval()
        return aux_head

    def _compute_auxiliary_loss(
        self,
        aux_head: SequenceAuxiliaryHead | None,
        features: torch.Tensor,
        aux_targets: Mapping[str, torch.Tensor],
        *,
        label_smoothing: float = 0.0,
    ) -> tuple[torch.Tensor, float, int]:
        zero = torch.zeros((), device=features.device, dtype=features.dtype)
        if aux_head is None or not aux_targets:
            return zero, 0.0, 0

        aux_logits = aux_head(features)
        losses: list[torch.Tensor] = []
        total_correct = 0
        total_count = 0

        for task_name, logits in aux_logits.items():
            targets = aux_targets.get(task_name)
            if targets is None:
                continue
            valid_mask = targets >= 0
            if not bool(torch.any(valid_mask)):
                continue

            valid_logits = logits[valid_mask]
            valid_targets = targets[valid_mask]
            losses.append(
                F.cross_entropy(
                    valid_logits,
                    valid_targets,
                    label_smoothing=float(max(label_smoothing, 0.0)),
                )
            )

            predictions = torch.argmax(valid_logits, dim=1)
            total_correct += int((predictions == valid_targets).sum().item())
            total_count += int(valid_targets.numel())

        if not losses:
            return zero, 0.0, 0

        accuracy = 100.0 * float(total_correct) / float(total_count) if total_count > 0 else 0.0
        return torch.stack(losses).mean(), accuracy, total_count

    def _sequence_coverage_stats(self, dataset: ChartImageDataset) -> dict[str, Any]:
        total_samples = len(dataset.samples)
        task_names = list(dataset.sequence_task_names)
        matched_samples = 0
        fully_labeled_samples = 0
        per_task_valid: dict[str, int] = {task_name: 0 for task_name in task_names}

        for sample_targets in dataset.sequence_label_indices:
            valid_count = 0
            for task_name in task_names:
                if int(sample_targets.get(task_name, -1)) >= 0:
                    per_task_valid[task_name] += 1
                    valid_count += 1
            if valid_count > 0:
                matched_samples += 1
            if task_names and valid_count == len(task_names):
                fully_labeled_samples += 1

        matched_ratio = float(matched_samples) / float(total_samples) if total_samples > 0 else 0.0
        full_ratio = float(fully_labeled_samples) / float(total_samples) if total_samples > 0 else 0.0
        return {
            "total_samples": total_samples,
            "matched_samples": matched_samples,
            "fully_labeled_samples": fully_labeled_samples,
            "matched_ratio": matched_ratio,
            "full_ratio": full_ratio,
            "per_task_valid": per_task_valid,
        }

    def _validate_sequence_supervision(
        self,
        *,
        model_name: str,
        train_dirs: Sequence[str],
        val_dirs: Sequence[str] | None,
    ) -> None:
        if not self.sequence_task_values or self.sequence_aux_loss_weight <= 0.0:
            return

        train_dataset = self._build_dataset(
            model_name=model_name,
            image_dirs=train_dirs,
            is_training=True,
        )
        train_stats = self._sequence_coverage_stats(cast(ChartImageDataset, train_dataset))

        val_stats: dict[str, Any] | None = None
        if val_dirs:
            val_dataset = self._build_dataset(
                model_name=model_name,
                image_dirs=val_dirs,
                is_training=False,
            )
            val_stats = self._sequence_coverage_stats(cast(ChartImageDataset, val_dataset))

        print(
            f"[SEQUENCE COVERAGE] {model_name} train matched "
            f"{train_stats['matched_samples']}/{train_stats['total_samples']} "
            f"({100.0 * float(train_stats['matched_ratio']):.1f}%)"
        )
        print(
            f"[SEQUENCE COVERAGE] {model_name} train fully-labeled "
            f"{train_stats['fully_labeled_samples']}/{train_stats['total_samples']} "
            f"({100.0 * float(train_stats['full_ratio']):.1f}%)"
        )
        if val_stats is not None:
            print(
                f"[SEQUENCE COVERAGE] {model_name} val matched "
                f"{val_stats['matched_samples']}/{val_stats['total_samples']} "
                f"({100.0 * float(val_stats['matched_ratio']):.1f}%)"
            )
            print(
                f"[SEQUENCE COVERAGE] {model_name} val fully-labeled "
                f"{val_stats['fully_labeled_samples']}/{val_stats['total_samples']} "
                f"({100.0 * float(val_stats['full_ratio']):.1f}%)"
            )

        strict_full_coverage = self.sequence_manifest_quality == "all"

        if int(train_stats["matched_samples"]) == 0:
            raise RuntimeError(
                "Sequence teacher labels did not attach to any training samples. "
                "This usually means the manifest paths and training directories do not match."
            )
        if strict_full_coverage and int(train_stats["matched_samples"]) != int(train_stats["total_samples"]):
            raise RuntimeError(
                "Sequence teacher labels attached to only part of the training set. "
                "Rebuild the teacher manifest before training."
            )
        if strict_full_coverage and int(train_stats["fully_labeled_samples"]) != int(train_stats["total_samples"]):
            raise RuntimeError(
                "Sequence teacher manifest is missing one or more auxiliary targets on training samples. "
                "Rebuild the teacher manifest before training."
            )
        if (not strict_full_coverage) and int(train_stats["fully_labeled_samples"]) != int(train_stats["matched_samples"]):
            raise RuntimeError(
                "Filtered sequence teacher supervision attached partial auxiliary targets on retained "
                "training samples. Rebuild the teacher manifest or relax the filter mode."
            )
        if val_stats is not None and int(val_stats["matched_samples"]) == 0:
            raise RuntimeError(
                "Sequence teacher labels did not attach to any validation samples. "
                "This usually means the manifest paths and validation directories do not match."
            )
        if strict_full_coverage and val_stats is not None and int(val_stats["matched_samples"]) != int(val_stats["total_samples"]):
            raise RuntimeError(
                "Sequence teacher labels attached to only part of the validation set. "
                "Rebuild the teacher manifest before training."
            )
        if strict_full_coverage and val_stats is not None and int(val_stats["fully_labeled_samples"]) != int(val_stats["total_samples"]):
            raise RuntimeError(
                "Sequence teacher manifest is missing one or more auxiliary targets on validation samples. "
                "Rebuild the teacher manifest before training."
            )
        if (
            (not strict_full_coverage)
            and val_stats is not None
            and int(val_stats["fully_labeled_samples"]) != int(val_stats["matched_samples"])
        ):
            raise RuntimeError(
                "Filtered sequence teacher supervision attached partial auxiliary targets on retained "
                "validation samples. Rebuild the teacher manifest or relax the filter mode."
            )

    def _backbone_param_list(self, model: nn.Module) -> list[nn.Parameter]:
        module = self._resolve_backbone_module(model)
        return [param for param in module.parameters() if param.requires_grad]

    def _set_backbone_trainable(self, model: nn.Module, trainable: bool) -> list[nn.Parameter]:
        module = self._resolve_backbone_module(model)

        params: list[nn.Parameter] = []
        for param in module.parameters():
            param.requires_grad = trainable
            if trainable:
                params.append(param)
        return params

    def _freeze_all_backbone_params(self, model: nn.Module) -> None:
        self._set_backbone_trainable(model, False)

    def _resolve_backbone_module(self, model: nn.Module) -> nn.Module:
        backbone_attr = getattr(model, "backbone", None)
        module = backbone_attr if isinstance(backbone_attr, nn.Module) else model

        clip_model = getattr(module, "clip_model", None)
        if isinstance(clip_model, nn.Module):
            visual = getattr(clip_model, "visual", None)
            if isinstance(visual, nn.Module):
                return visual

        inner_model = getattr(module, "model", None)
        if isinstance(inner_model, nn.Module):
            return inner_model

        return module

    def _select_tail_modules(self, module: nn.Module) -> list[nn.Module]:
        for attr_name in ("blocks", "stages", "features"):
            attr_value = getattr(module, attr_name, None)
            if isinstance(attr_value, (nn.ModuleList, nn.Sequential)) and len(attr_value) > 0:
                modules = list(cast(Sequence[nn.Module], attr_value))
                tail = modules[-2:] if len(modules) >= 2 else modules[-1:]
                return [tail_module for tail_module in tail if isinstance(tail_module, nn.Module)]

        layer4_obj = getattr(module, "layer4", None)
        if isinstance(layer4_obj, nn.Module):
            return [layer4_obj]

        trunk_obj = getattr(module, "trunk", None)
        if isinstance(trunk_obj, nn.Module):
            tail = self._select_tail_modules(trunk_obj)
            if tail:
                return tail

        return []

    def _set_trainable_last_block_for_dino(self, model: nn.Module) -> list[nn.Parameter]:
        trainable_params: list[nn.Parameter] = []
        self._freeze_all_backbone_params(model)

        module = self._resolve_backbone_module(model)

        blocks_obj = getattr(module, "blocks", None)
        if isinstance(blocks_obj, nn.ModuleList) and len(blocks_obj) > 0:
            blocks_to_unfreeze = list(blocks_obj[-2:]) if len(blocks_obj) >= 2 else [blocks_obj[-1]]
            for block in blocks_to_unfreeze:
                for param in block.parameters():
                    param.requires_grad = True
                    trainable_params.append(param)

        norm_obj = getattr(module, "norm", None)
        if isinstance(norm_obj, nn.Module):
            for param in norm_obj.parameters():
                param.requires_grad = True
                trainable_params.append(param)

        fc_norm_obj = getattr(module, "fc_norm", None)
        if isinstance(fc_norm_obj, nn.Module):
            for param in fc_norm_obj.parameters():
                param.requires_grad = True
                trainable_params.append(param)

        return trainable_params

    def _set_trainable_last_stage(self, model: nn.Module, name: str) -> list[nn.Parameter]:
        if name == "dinov2":
            return self._set_trainable_last_block_for_dino(model)

        trainable_params: list[nn.Parameter] = []
        self._freeze_all_backbone_params(model)
        module = self._resolve_backbone_module(model)
        tail_modules = self._select_tail_modules(module)

        for tail_module in tail_modules:
            for param in tail_module.parameters():
                param.requires_grad = True
                trainable_params.append(param)

        if not trainable_params:
            for param in module.parameters():
                param.requires_grad = True
                trainable_params.append(param)

        return trainable_params

    def _infer_label_map(self, image_dirs: Sequence[str]) -> dict[str, str]:
        dirs = list(image_dirs)
        if len(dirs) == 1 and os.path.isdir(dirs[0]):
            subdirs = [
                os.path.join(dirs[0], entry)
                for entry in os.listdir(dirs[0])
                if os.path.isdir(os.path.join(dirs[0], entry))
            ]
            if len(subdirs) > 0:
                dirs = sorted(subdirs)

        label_map: dict[str, str] = {}
        for idx, dir_path in enumerate(dirs):
            label_name = Path(dir_path).name.strip().upper()
            label_map[str(idx)] = label_name if label_name else str(idx)

        if not label_map:
            label_map = {"0": "BUY", "1": "SELL"}
        return label_map

    def _compute_class_counts(self, image_dirs: Sequence[str] | None = None) -> list[int]:
        dirs = list(image_dirs) if image_dirs is not None else list(self.image_dirs)

        if len(dirs) == 1 and os.path.isdir(dirs[0]):
            subdirs = [
                os.path.join(dirs[0], entry)
                for entry in os.listdir(dirs[0])
                if os.path.isdir(os.path.join(dirs[0], entry))
            ]
            if len(subdirs) > 0:
                dirs = sorted(subdirs)

        counts: list[int] = []
        for dir_path in dirs:
            count = 0
            if os.path.isdir(dir_path):
                for fname in os.listdir(dir_path):
                    if fname.lower().endswith(_VALID_IMAGE_SUFFIXES):
                        count += 1
            counts.append(count)

        if not counts:
            counts = [1, 1]
        return counts

    def _compute_class_weight_tensor(
        self,
        name: str,
        image_dirs: Sequence[str] | None = None,
    ) -> torch.Tensor:
        cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
        counts = [max(count, 1) for count in self._compute_class_counts(image_dirs)]

        if not bool(cfg.use_class_weights):
            uniform_weights = [1.0 for _ in counts]
            self.class_weights_by_model[name] = [float(weight) for weight in uniform_weights]
            return torch.tensor(uniform_weights, dtype=torch.float32, device=self.device)

        beta = 0.999
        raw_weights: list[float] = []

        for count in counts:
            effective_num = 1.0 - (beta ** count)
            weight = (1.0 - beta) / effective_num if effective_num > 0.0 else 1.0
            raw_weights.append(weight)

        total_weight = sum(raw_weights)
        num_classes = max(len(raw_weights), 1)
        normalized_weights = [
            (float(weight) * float(num_classes) / float(total_weight)) if total_weight > 0.0 else 1.0
            for weight in raw_weights
        ]

        blend = float(min(max(cfg.class_weight_blend, 0.0), 1.0))
        blended_weights = [
            blend * float(weight) + (1.0 - blend) * 1.0
            for weight in normalized_weights
        ]

        if len(blended_weights) >= 2:
            blended_weights[0] *= float(cfg.buy_weight_scale)
            blended_weights[1] *= float(cfg.sell_weight_scale)

        final_total = sum(blended_weights)
        final_weights = [
            (float(weight) * float(num_classes) / float(final_total)) if final_total > 0.0 else 1.0
            for weight in blended_weights
        ]

        self.class_weights_by_model[name] = [float(weight) for weight in final_weights]
        return torch.tensor(final_weights, dtype=torch.float32, device=self.device)

    @staticmethod
    def _confusion_matrix_from_predictions(
        labels: torch.Tensor,
        predictions: torch.Tensor,
    ) -> list[list[int]]:
        confusion_matrix = [[0, 0], [0, 0]]

        labels_cpu = labels.detach().view(-1).cpu()
        predictions_cpu = predictions.detach().view(-1).cpu()

        for true_label, pred_label in zip(labels_cpu, predictions_cpu):
            true_idx = int(true_label.item())
            pred_idx = int(pred_label.item())
            if 0 <= true_idx < 2 and 0 <= pred_idx < 2:
                confusion_matrix[true_idx][pred_idx] += 1

        return confusion_matrix

    @staticmethod
    def _metrics_from_confusion_matrix(confusion_matrix: list[list[int]]) -> dict[str, float]:
        tn_buy = confusion_matrix[0][0]
        fp_buy = confusion_matrix[0][1]
        fn_sell = confusion_matrix[1][0]
        tp_sell = confusion_matrix[1][1]

        total = tn_buy + fp_buy + fn_sell + tp_sell
        correct = tn_buy + tp_sell

        buy_recall_frac = _safe_div(tn_buy, tn_buy + fp_buy)
        sell_recall_frac = _safe_div(tp_sell, fn_sell + tp_sell)

        buy_precision_frac = _safe_div(tn_buy, tn_buy + fn_sell)
        sell_precision_frac = _safe_div(tp_sell, fp_buy + tp_sell)

        buy_f1_frac = _safe_div(2.0 * buy_precision_frac * buy_recall_frac, buy_precision_frac + buy_recall_frac)
        sell_f1_frac = _safe_div(2.0 * sell_precision_frac * sell_recall_frac, sell_precision_frac + sell_recall_frac)

        balanced_accuracy_frac = 0.5 * (buy_recall_frac + sell_recall_frac)
        macro_f1_frac = 0.5 * (buy_f1_frac + sell_f1_frac)
        min_recall_frac = min(buy_recall_frac, sell_recall_frac)
        accuracy_frac = _safe_div(correct, total)

        return {
            "accuracy": 100.0 * accuracy_frac,
            "buy_recall": 100.0 * buy_recall_frac,
            "sell_recall": 100.0 * sell_recall_frac,
            "buy_precision": 100.0 * buy_precision_frac,
            "sell_precision": 100.0 * sell_precision_frac,
            "buy_f1": 100.0 * buy_f1_frac,
            "sell_f1": 100.0 * sell_f1_frac,
            "balanced_accuracy": 100.0 * balanced_accuracy_frac,
            "macro_f1": 100.0 * macro_f1_frac,
            "min_recall": 100.0 * min_recall_frac,
        }

    @staticmethod
    def _selection_score_from_metrics(
        metrics: Mapping[str, float],
        *,
        val_loss: float,
        target_min_recall: float = 0.0,
        buy_recall_tiebreak: bool = False,
        sell_recall_tiebreak: bool = False,
        val_aux_acc: float = 0.0,
        val_aux_loss: float = 0.0,
        val_prob_gap: float = 0.0,
        train_val_gap: float = 0.0,
        aux_train_val_gap: float = 0.0,
    ) -> tuple[float, float, float, float, float, float, float, float, float, float, float, float, float]:
        recall_gap = abs(float(metrics["buy_recall"]) - float(metrics["sell_recall"]))
        min_recall = float(metrics["min_recall"])
        target_hit = (
            1.0
            if float(target_min_recall) > 0.0 and min_recall >= float(target_min_recall)
            else 0.0
        )
        return (
            target_hit,
            float(metrics["balanced_accuracy"]),
            float(metrics["macro_f1"]),
            min_recall,
            float(metrics["buy_recall"]) if bool(buy_recall_tiebreak) else 0.0,
            float(metrics["sell_recall"]) if bool(sell_recall_tiebreak) else 0.0,
            float(val_aux_acc),
            -float(aux_train_val_gap),
            -float(val_prob_gap),
            -float(train_val_gap),
            -recall_gap,
            -float(val_aux_loss),
            -float(val_loss),
        )

    @staticmethod
    def _predictions_from_buy_probs(buy_probs: torch.Tensor, threshold: float) -> torch.Tensor:
        buy_mask = buy_probs >= float(threshold)
        return torch.where(
            buy_mask,
            torch.zeros_like(buy_probs, dtype=torch.long),
            torch.ones_like(buy_probs, dtype=torch.long),
        )

    def _candidate_thresholds(self, buy_probs: torch.Tensor) -> list[float]:
        values = sorted({float(x) for x in buy_probs.detach().cpu().tolist() if isinstance(x, (float, int))})  # type: ignore[var-annotated]
        if not values:
            return [0.5]

        candidates: set[float] = {0.5}
        for value in values:
            candidates.add(min(max(value, 1e-6), 1.0 - 1e-6))

        for idx in range(len(values) - 1):
            midpoint = 0.5 * (values[idx] + values[idx + 1])
            candidates.add(min(max(midpoint, 1e-6), 1.0 - 1e-6))

        for step in range(5, 96, 5):
            candidates.add(step / 100.0)

        return sorted(candidates)

    def _select_best_threshold(
        self,
        buy_probs: torch.Tensor,
        labels: torch.Tensor,
        *,
        target_min_recall: float = 0.0,
        prefer_buy_recall: bool = False,
        prefer_sell_recall: bool = False,
    ) -> tuple[float, dict[str, Any]]:
        best_threshold = 0.5
        default_predictions = self._predictions_from_buy_probs(buy_probs, best_threshold)
        default_confusion = self._confusion_matrix_from_predictions(labels, default_predictions)
        default_metrics = self._metrics_from_confusion_matrix(default_confusion)
        default_gap = abs(float(default_metrics["buy_recall"]) - float(default_metrics["sell_recall"]))
        default_min_recall = float(default_metrics["min_recall"])
        default_target_hit = (
            1.0
            if float(target_min_recall) > 0.0 and default_min_recall >= float(target_min_recall)
            else 0.0
        )

        best_result: dict[str, Any] = {
            "threshold": float(best_threshold),
            "confusion_matrix": default_confusion,
            **default_metrics,
        }
        best_score = (
            default_target_hit,
            float(default_metrics["balanced_accuracy"]),
            float(default_metrics["macro_f1"]),
            default_min_recall,
            float(default_metrics["buy_recall"]) if bool(prefer_buy_recall) else 0.0,
            float(default_metrics["sell_recall"]) if bool(prefer_sell_recall) else 0.0,
            -default_gap,
            float(default_metrics["accuracy"]),
            -abs(float(best_threshold) - 0.5),
        )

        for threshold in self._candidate_thresholds(buy_probs):
            predictions = self._predictions_from_buy_probs(buy_probs, threshold)
            confusion_matrix = self._confusion_matrix_from_predictions(labels, predictions)
            metrics = self._metrics_from_confusion_matrix(confusion_matrix)
            recall_gap = abs(float(metrics["buy_recall"]) - float(metrics["sell_recall"]))
            min_recall = float(metrics["min_recall"])
            target_hit = (
                1.0
                if float(target_min_recall) > 0.0 and min_recall >= float(target_min_recall)
                else 0.0
            )

            score = (
                target_hit,
                float(metrics["balanced_accuracy"]),
                float(metrics["macro_f1"]),
                min_recall,
                float(metrics["buy_recall"]) if bool(prefer_buy_recall) else 0.0,
                float(metrics["sell_recall"]) if bool(prefer_sell_recall) else 0.0,
                -recall_gap,
                float(metrics["accuracy"]),
                -abs(float(threshold) - 0.5),
            )

            if score > best_score:
                best_score = score
                best_threshold = float(threshold)
                best_result = {
                    "threshold": float(best_threshold),
                    "confusion_matrix": confusion_matrix,
                    **metrics,
                }

        return best_threshold, best_result

    def _fit_temperature(
        self,
        name: str,
        logits: torch.Tensor,
        labels: torch.Tensor,
    ) -> float:
        if logits.numel() == 0 or labels.numel() == 0:
            return 1.0

        cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
        min_temperature = float(max(cfg.min_temperature, 0.5))
        best_temp = min_temperature
        baseline_logits = logits / min_temperature
        best_objective = float(F.cross_entropy(baseline_logits, labels).item())
        best_objective += float(cfg.temperature_gap_weight) * float(
            probability_gap_penalty(
                baseline_logits,
                target_gap=float(cfg.prob_gap_target),
            ).item()
        )

        start_idx = max(10, int(round(min_temperature * 20.0)))
        for idx in range(start_idx, 161):
            temperature = idx / 20.0
            scaled_logits = logits / temperature
            objective = float(F.cross_entropy(scaled_logits, labels).item())
            objective += float(cfg.temperature_gap_weight) * float(
                probability_gap_penalty(
                    scaled_logits,
                    target_gap=float(cfg.prob_gap_target),
                ).item()
            )
            if objective < best_objective:
                best_objective = objective
                best_temp = float(temperature)

        return best_temp

    def _collect_split_outputs(
        self,
        model: nn.Module,
        head: nn.Module,
        name: str,
        image_dirs: Sequence[str],
        batch_size: int,
    ) -> dict[str, Any]:
        cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
        eval_batch = max(1, min(batch_size, cfg.batch_size))
        dataloader = self._get_dataloader(
            batch_size=eval_batch,
            model_name=name,
            image_dirs=image_dirs,
            shuffle=False,
            balanced=False,
            is_training=False,
        )
        dataset = cast(ChartImageDataset, dataloader.dataset)

        embeddings_parts: list[torch.Tensor] = []
        logits_parts: list[torch.Tensor] = []
        labels_parts: list[torch.Tensor] = []

        model.eval()
        head.eval()

        with torch.no_grad():
            for imgs, labels, _aux_targets in dataloader:
                imgs = imgs.to(self.device, non_blocking=True)
                labels = labels.to(self.device, non_blocking=True)

                features = forward_features(model, imgs)
                features_for_head = F.normalize(features, p=2, dim=1) if name == "dinov2" else features
                logits = head(features_for_head)

                embeddings_parts.append(features_for_head.detach().cpu())
                logits_parts.append(logits.detach().cpu())
                labels_parts.append(labels.detach().cpu())

        embeddings = (
            torch.cat(embeddings_parts, dim=0)
            if embeddings_parts
            else torch.empty((0, int(self.feature_dims.get(name, 0))), dtype=torch.float32)
        )
        logits = (
            torch.cat(logits_parts, dim=0)
            if logits_parts
            else torch.empty((0, 2), dtype=torch.float32)
        )
        labels_tensor = (
            torch.cat(labels_parts, dim=0)
            if labels_parts
            else torch.empty((0,), dtype=torch.long)
        )

        return {
            "paths": list(dataset.samples),
            "embeddings": embeddings,
            "logits": logits,
            "labels": labels_tensor,
        }

    def _save_sidecar_artifacts(
        self,
        model_dir: Path,
        model: nn.Module,
        head: nn.Module,
        name: str,
        val_dirs: list[str],
        batch_size: int,
    ) -> None:
        temperature = float(self.temperature_scalers.get(name, 1.0))
        threshold = float(self.decision_thresholds.get(name, 0.5))
        label_map = self._infer_label_map(self.image_dirs)

        for split_name, split_dirs in (("train", self.image_dirs), ("val", val_dirs)):
            split_outputs = self._collect_split_outputs(
                model=model,
                head=head,
                name=name,
                image_dirs=split_dirs,
                batch_size=batch_size,
            )

            scaled_logits = split_outputs["logits"] / temperature if split_outputs["logits"].numel() > 0 else split_outputs["logits"]
            probs = torch.softmax(scaled_logits, dim=-1) if scaled_logits.numel() > 0 else scaled_logits
            buy_probs = probs[:, 0] if probs.numel() > 0 else torch.empty((0,), dtype=torch.float32)
            predictions = self._predictions_from_buy_probs(buy_probs, threshold) if buy_probs.numel() > 0 else torch.empty((0,), dtype=torch.long)

            artifact_payload: dict[str, Any] = {
                "model_name": name,
                "split": split_name,
                "paths": list(split_outputs["paths"]),
                "labels": split_outputs["labels"],
                "embeddings": split_outputs["embeddings"],
                "logits": split_outputs["logits"],
                "calibrated_logits": scaled_logits,
                "probabilities": probs,
                "predictions": predictions,
                "temperature": float(temperature),
                "decision_threshold": float(threshold),
                "label_map": label_map,
            }

            artifact_path = model_dir / f"{name}_{split_name}_embeddings.pt"
            torch.save(artifact_payload, artifact_path)

        metadata_payload: dict[str, Any] = {
            "model_name": name,
            "random_seed": int(self.random_seed),
            "feature_dim": int(self.feature_dims.get(name, 0)),
            "label_map": label_map,
            "class_counts": self._compute_class_counts(self.image_dirs),
            "class_weights": self.class_weights_by_model.get(name, []),
            "temperature": float(temperature),
            "decision_threshold": float(threshold),
            "evaluation_metrics": self.evaluation_metrics.get(name, {}),
            "bundle_file": self.MODEL_SPECS[name]["save_name"],
            "train_embeddings_file": f"{name}_train_embeddings.pt",
            "val_embeddings_file": f"{name}_val_embeddings.pt",
            "sequence_manifest_path": self.sequence_manifest_path,
            "sequence_manifest_quality": self.sequence_manifest_quality,
            "sequence_manifest_filter": self.sequence_manifest_filter_stats,
            "sequence_aux_loss_weight": float(self.sequence_aux_loss_weight),
            "sequence_task_values": self.sequence_task_values,
            "sequence_aux_metrics": self.sequence_aux_metrics.get(name, {}),
            "runtime_calibration": {},
            "lora": collect_lora_summary(self._resolve_backbone_module(model)),
            "continual_learning": {
                "replay_buffer_path": str(RUNTIME.replay_buffer_path),
                "adapter_bank_path": str(RUNTIME.adapter_bank_path),
                "pending_contexts_path": str(RUNTIME.pending_contexts_path),
                "ewc_lambda": float(TRAIN.ewc_lambda),
                "lwf_temperature": float(TRAIN.lwf_temperature),
                "lwf_loss_weight": float(TRAIN.lwf_loss_weight),
                "replay_buffer_size": int(TRAIN.replay_buffer_size),
                "replay_sample_count": int(len(self.continual_states.get(name, ContinualTrainingState()).replay_samples)),
                "adapter_name": str(self.continual_states.get(name, ContinualTrainingState()).adapter_name),
            },
        }

        metadata_path = model_dir / f"{name}_metadata.json"
        if metadata_path.exists():
            try:
                existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(existing_metadata, dict):
                    metadata_payload["runtime_calibration"] = dict(
                        cast(Mapping[str, Any], existing_metadata.get("runtime_calibration", {}))
                    )
            except Exception:
                metadata_payload["runtime_calibration"] = {}
        with metadata_path.open("w", encoding="utf-8") as metadata_file:
            json.dump(metadata_payload, metadata_file, indent=2)

    def _optimizer_for_stage(
        self,
        name: str,
        model: nn.Module,
        head: nn.Module,
        stage: str,
    ) -> optim.Optimizer:
        cfg = TRAIN_CONFIGS[name]
        aux_head = self._ensure_aux_head(name)
        head_params = [param for param in head.parameters() if param.requires_grad]
        if aux_head is not None:
            head_params.extend([param for param in aux_head.parameters() if param.requires_grad])
        backbone_params = self._backbone_param_list(model)

        param_groups: list[dict[str, Any]]

        if stage == "head_only":
            param_groups = [
                {
                    "params": head_params,
                    "lr": cfg.head_lr,
                    "weight_decay": cfg.weight_decay,
                }
            ]
        elif stage == "lora_adapter":
            param_groups = []
            if backbone_params:
                param_groups.append(
                    {
                        "params": backbone_params,
                        "lr": cfg.backbone_lr,
                        "weight_decay": cfg.weight_decay,
                    }
                )
            param_groups.append(
                {
                    "params": head_params,
                    "lr": cfg.head_lr,
                    "weight_decay": cfg.weight_decay,
                }
            )
        elif stage == "last_block":
            module = self._resolve_backbone_module(model)

            if name == "dinov2":
                block_params: list[nn.Parameter] = []
                blocks_obj = getattr(module, "blocks", None)
                if isinstance(blocks_obj, nn.ModuleList) and len(blocks_obj) > 0:
                    second_last_params = (
                        [param for param in blocks_obj[-2].parameters() if param.requires_grad]
                        if len(blocks_obj) >= 2 else []
                    )
                    last_block_params = [param for param in blocks_obj[-1].parameters() if param.requires_grad]
                else:
                    second_last_params = []
                    last_block_params = []

                norm_params: list[nn.Parameter] = []
                for norm_name in ("norm", "fc_norm"):
                    norm_obj = getattr(module, norm_name, None)
                    if isinstance(norm_obj, nn.Module):
                        norm_params.extend([param for param in norm_obj.parameters() if param.requires_grad])

                used_ids = {
                    id(param)
                    for param in second_last_params + last_block_params + norm_params
                }
                remaining_backbone_params = [
                    param for param in backbone_params if id(param) not in used_ids
                ]

                lower_block_lr = float(cfg.backbone_lr) * float(cfg.backbone_layer_decay)
                param_groups = []
                if second_last_params:
                    param_groups.append(
                        {
                            "params": second_last_params,
                            "lr": lower_block_lr,
                            "weight_decay": cfg.weight_decay,
                        }
                    )
                if last_block_params:
                    param_groups.append(
                        {
                            "params": last_block_params,
                            "lr": cfg.backbone_lr,
                            "weight_decay": cfg.weight_decay,
                        }
                    )
                if norm_params:
                    param_groups.append(
                        {
                            "params": norm_params,
                            "lr": lower_block_lr,
                            "weight_decay": cfg.weight_decay,
                        }
                    )
                if remaining_backbone_params:
                    param_groups.append(
                        {
                            "params": remaining_backbone_params,
                            "lr": lower_block_lr,
                            "weight_decay": cfg.weight_decay,
                        }
                    )
                param_groups.append(
                    {
                        "params": head_params,
                        "lr": cfg.head_lr,
                        "weight_decay": cfg.weight_decay,
                    }
                )
                return optim.AdamW(param_groups, betas=(0.9, 0.99), eps=1e-8)
            param_groups = [
                {
                    "params": backbone_params,
                    "lr": cfg.backbone_lr,
                    "weight_decay": cfg.weight_decay,
                },
                {
                    "params": head_params,
                    "lr": cfg.head_lr,
                    "weight_decay": cfg.weight_decay,
                },
            ]
        elif stage == "full":
            param_groups = [
                {
                    "params": backbone_params,
                    "lr": cfg.backbone_lr,
                    "weight_decay": cfg.weight_decay,
                },
                {
                    "params": head_params,
                    "lr": cfg.head_lr,
                    "weight_decay": cfg.weight_decay,
                },
            ]
        else:
            raise ValueError(f"Unknown training stage: {stage}")

        return optim.AdamW(param_groups, betas=(0.9, 0.99), eps=1e-8)

    def _run_training_stage(
        self,
        model: nn.Module,
        head: nn.Module,
        name: str,
        optimizer: optim.Optimizer,
        epochs: int,
        stage_label: str,
        val_dirs: list[str] | None = None,
        verbose: int = 0,
    ) -> tuple[
        float,
        tuple[float, float, float, float, float, float, float, float, float, float, float, float, float],
        list[dict[str, float]],
        dict[str, torch.Tensor],
        dict[str, torch.Tensor],
        dict[str, torch.Tensor],
        dict[str, Any],
        int,
    ]:
        from torch.optim.lr_scheduler import CosineAnnealingLR, LinearLR, SequentialLR

        device = self.device
        cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
        batch_size = max(1, min(cfg.batch_size, 32))
        grad_accum_steps = max(1, cfg.grad_accum_steps)
        max_grad_norm = float(cfg.max_grad_norm)
        continual_state = self.continual_states.get(name, ContinualTrainingState())

        train_loader = self._get_dataloader(
            batch_size=batch_size,
            model_name=name,
            image_dirs=self.image_dirs,
            shuffle=True,
            balanced=bool(cfg.use_balanced_sampler),
            is_training=True,
            include_replay=bool(continual_state.replay_samples),
            replay_samples=continual_state.replay_samples,
        )

        val_loader: DataLoader[Any] | None = None
        if val_dirs:
            val_loader = self._get_dataloader(
                batch_size=batch_size,
                model_name=name,
                image_dirs=val_dirs,
                shuffle=False,
                balanced=False,
                is_training=False,
            )

        class_weight_tensor: torch.Tensor | None = None
        if bool(cfg.use_class_weights):
            class_weight_tensor = self._compute_class_weight_tensor(name=name, image_dirs=self.image_dirs)
        else:
            self.class_weights_by_model[name] = [1.0 for _ in self._compute_class_counts(self.image_dirs)]

        if bool(cfg.use_focal_loss):
            train_criterion: nn.Module = FocalCrossEntropyLoss(
                weight=class_weight_tensor,
                gamma=float(cfg.focal_gamma),
            )
        elif class_weight_tensor is not None:
            train_criterion = nn.CrossEntropyLoss(
                weight=class_weight_tensor,
                label_smoothing=float(cfg.label_smoothing),
            )
        else:
            train_criterion = nn.CrossEntropyLoss(
                label_smoothing=float(cfg.label_smoothing),
            )

        eval_criterion = nn.CrossEntropyLoss()

        warmup_epochs = min(int(cfg.warmup_epochs), max(epochs - 1, 0))
        if warmup_epochs > 0:
            warmup_scheduler = LinearLR(
                optimizer,
                start_factor=0.1,
                end_factor=1.0,
                total_iters=warmup_epochs,
            )
            cosine_scheduler = CosineAnnealingLR(
                optimizer,
                T_max=max(epochs - warmup_epochs, 1),
                eta_min=float(cfg.min_lr),
            )
            scheduler: LinearLR | CosineAnnealingLR | SequentialLR = SequentialLR(
                optimizer,
                schedulers=[warmup_scheduler, cosine_scheduler],
                milestones=[warmup_epochs],
            )
        else:
            scheduler = CosineAnnealingLR(
                optimizer,
                T_max=max(epochs, 1),
                eta_min=float(cfg.min_lr),
            )

        best_acc = 0.0
        best_model_state: dict[str, torch.Tensor] = copy.deepcopy(model.state_dict())
        best_head_state: dict[str, torch.Tensor] = copy.deepcopy(head.state_dict())
        aux_head = self._ensure_aux_head(name)
        best_aux_head_state: dict[str, torch.Tensor] = (
            copy.deepcopy(aux_head.state_dict())
            if aux_head is not None
            else {}
        )
        best_optimizer_state: dict[str, Any] = copy.deepcopy(optimizer.state_dict())
        best_epoch = 0
        best_score = (
            -1.0,
            -1.0,
            -1.0,
            -1.0,
            -1.0,
            -1.0,
            -1.0,
            float("-inf"),
            float("-inf"),
            float("-inf"),
            float("-inf"),
            float("-inf"),
            float("-inf"),
        )
        patience_counter = 0
        history: list[dict[str, float]] = []
        for epoch in range(epochs):
            model.train()
            head.train()
            if aux_head is not None:
                if any(param.requires_grad for param in aux_head.parameters()):
                    aux_head.train()
                else:
                    aux_head.eval()

            if name in {"mobilenetv3", "simclr", "byol", "swav"}:
                _freeze_batchnorm_stats(model)

            total_loss = 0.0
            correct = 0
            total = 0
            num_train_batches = 0
            total_aux_loss = 0.0
            total_aux_correct = 0.0
            total_aux_count = 0
            num_train_aux_batches = 0
            total_train_prob_gap = 0.0
            total_lwf_loss = 0.0
            total_ewc_loss = 0.0
            current_aux_weight = self._epoch_aux_loss_weight(name, stage_label, epoch, epochs)

            optimizer.zero_grad(set_to_none=True)

            for step, (imgs, labels, aux_targets) in enumerate(train_loader):
                imgs = imgs.to(device, non_blocking=True)
                labels = labels.to(device, non_blocking=True)
                aux_targets = {
                    task_name: targets.to(device, non_blocking=True)
                    for task_name, targets in aux_targets.items()
                }

                features = forward_features(model, imgs)
                features_for_head = F.normalize(features, p=2, dim=1) if name == "dinov2" else features
                logits = head(features_for_head)

                ce_loss = train_criterion(logits, labels)
                gap_penalty = probability_gap_penalty(
                    logits,
                    target_gap=float(cfg.prob_gap_target),
                )

                if float(cfg.metric_loss_weight) > 0.0:
                    metric_features = F.normalize(features, p=2, dim=1)
                    metric_loss = batch_hard_triplet_loss(metric_features, labels)
                else:
                    metric_loss = torch.zeros((), device=device, dtype=ce_loss.dtype)

                aux_loss, aux_acc, aux_count = self._compute_auxiliary_loss(
                    aux_head,
                    features_for_head,
                    aux_targets,
                    label_smoothing=float(cfg.aux_label_smoothing),
                )
                lwf_loss = torch.zeros((), device=device, dtype=ce_loss.dtype)
                if (
                    continual_state.enabled
                    and continual_state.teacher_model is not None
                    and continual_state.teacher_head is not None
                ):
                    with torch.no_grad():
                        teacher_features = forward_features(continual_state.teacher_model, imgs)
                        teacher_features_for_head = (
                            F.normalize(teacher_features, p=2, dim=1)
                            if name == "dinov2"
                            else teacher_features
                        )
                        teacher_logits = continual_state.teacher_head(teacher_features_for_head)
                    lwf_loss = self._distillation_loss(
                        logits,
                        teacher_logits,
                        temperature=float(TRAIN.lwf_temperature),
                    )
                ewc_loss = (
                    self._ewc_penalty(continual_state, model, head, aux_head)
                    if continual_state.enabled
                    else torch.zeros((), device=device, dtype=ce_loss.dtype)
                )
                raw_loss = (
                    ce_loss
                    + float(cfg.metric_loss_weight) * metric_loss
                    + float(current_aux_weight) * aux_loss
                    + float(cfg.prob_gap_penalty_weight) * gap_penalty
                    + float(TRAIN.lwf_loss_weight) * lwf_loss
                    + float(TRAIN.ewc_lambda) * ewc_loss
                )
                loss = raw_loss / grad_accum_steps
                loss.backward()

                total_loss += float(raw_loss.item())
                num_train_batches += 1
                total_train_prob_gap += mean_probability_gap(logits)
                total_aux_loss += float(aux_loss.item()) if aux_count > 0 else 0.0
                total_aux_correct += float(aux_acc * aux_count / 100.0) if aux_count > 0 else 0.0
                total_aux_count += int(aux_count)
                total_lwf_loss += float(lwf_loss.item())
                total_ewc_loss += float(ewc_loss.item())
                if aux_count > 0:
                    num_train_aux_batches += 1

                predictions = torch.argmax(logits, dim=1)
                correct += int((predictions == labels).sum().item())
                total += int(labels.size(0))

                if (step + 1) % grad_accum_steps == 0 or (step + 1) == len(train_loader):
                    clip_params = list(model.parameters()) + list(head.parameters())
                    if aux_head is not None:
                        clip_params.extend(list(aux_head.parameters()))
                    nn.utils.clip_grad_norm_(
                        clip_params,
                        max_grad_norm,
                    )
                    optimizer.step()
                    optimizer.zero_grad(set_to_none=True)

            train_acc = 100.0 * float(correct) / float(total) if total > 0 else 0.0
            avg_loss = total_loss / float(max(num_train_batches, 1))
            train_aux_loss = total_aux_loss / float(max(num_train_aux_batches, 1)) if total_aux_count > 0 else 0.0
            train_aux_acc = 100.0 * float(total_aux_correct) / float(total_aux_count) if total_aux_count > 0 else 0.0
            train_prob_gap = total_train_prob_gap / float(max(num_train_batches, 1))
            train_lwf_loss = total_lwf_loss / float(max(num_train_batches, 1))
            train_ewc_loss = total_ewc_loss / float(max(num_train_batches, 1))

            val_acc = 0.0
            val_loss = avg_loss
            val_balanced_accuracy = 0.0
            val_macro_f1 = 0.0
            val_threshold = 0.5
            val_aux_loss = 0.0
            val_aux_acc = 0.0
            val_prob_gap = train_prob_gap
            aux_train_val_gap = 0.0

            if val_loader is not None:
                model.eval()
                head.eval()
                if aux_head is not None:
                    aux_head.eval()

                total_loss_val = 0.0
                num_val_batches = 0
                val_logits_parts: list[torch.Tensor] = []
                val_labels_parts: list[torch.Tensor] = []
                total_aux_loss_val = 0.0
                total_aux_correct_val = 0.0
                total_aux_count_val = 0
                num_val_aux_batches = 0
                total_val_prob_gap = 0.0

                with torch.no_grad():
                    for imgs, labels, aux_targets in val_loader:
                        imgs = imgs.to(self.device, non_blocking=True)
                        labels = labels.to(self.device, non_blocking=True)
                        aux_targets = {
                            task_name: targets.to(self.device, non_blocking=True)
                            for task_name, targets in aux_targets.items()
                        }

                        features = forward_features(model, imgs)
                        features_for_head = F.normalize(features, p=2, dim=1) if name == "dinov2" else features
                        logits = head(features_for_head)

                        loss_val = eval_criterion(logits, labels)
                        aux_loss_val, aux_acc_val, aux_count_val = self._compute_auxiliary_loss(
                            aux_head,
                            features_for_head,
                            aux_targets,
                            label_smoothing=float(cfg.aux_label_smoothing),
                        )

                        val_logits_parts.append(logits.detach().cpu())
                        val_labels_parts.append(labels.detach().cpu())
                        total_loss_val += float(loss_val.item())
                        total_val_prob_gap += mean_probability_gap(logits)
                        total_aux_loss_val += float(aux_loss_val.item()) if aux_count_val > 0 else 0.0
                        total_aux_correct_val += (
                            float(aux_acc_val * aux_count_val / 100.0)
                            if aux_count_val > 0
                            else 0.0
                        )
                        total_aux_count_val += int(aux_count_val)
                        if aux_count_val > 0:
                            num_val_aux_batches += 1
                        num_val_batches += 1

                val_logits_tensor = (
                    torch.cat(val_logits_parts, dim=0)
                    if val_logits_parts
                    else torch.empty((0, 2), dtype=torch.float32)
                )
                val_labels_tensor = (
                    torch.cat(val_labels_parts, dim=0)
                    if val_labels_parts
                    else torch.empty((0,), dtype=torch.long)
                )

                val_loss = total_loss_val / float(max(num_val_batches, 1))
                val_prob_gap = total_val_prob_gap / float(max(num_val_batches, 1))
                val_aux_loss = (
                    total_aux_loss_val / float(max(num_val_aux_batches, 1))
                    if total_aux_count_val > 0
                    else 0.0
                )
                val_aux_acc = (
                    100.0 * float(total_aux_correct_val) / float(total_aux_count_val)
                    if total_aux_count_val > 0
                    else 0.0
                )
                aux_train_val_gap = max(float(train_aux_acc) - float(val_aux_acc), 0.0)

                if val_logits_tensor.numel() > 0 and val_labels_tensor.numel() > 0:
                    val_probs = torch.softmax(val_logits_tensor, dim=-1)[:, 0]
                    val_threshold, threshold_metrics = self._select_best_threshold(
                        val_probs,
                        val_labels_tensor,
                        target_min_recall=float(cfg.target_min_recall),
                        prefer_buy_recall=bool(cfg.buy_recall_tiebreak),
                        prefer_sell_recall=bool(cfg.sell_recall_tiebreak),
                    )
                    val_acc = float(threshold_metrics["accuracy"])
                    val_balanced_accuracy = float(threshold_metrics["balanced_accuracy"])
                    val_macro_f1 = float(threshold_metrics["macro_f1"])
                    current_score = self._selection_score_from_metrics(
                        threshold_metrics,
                        val_loss=val_loss,
                        target_min_recall=float(cfg.target_min_recall),
                        buy_recall_tiebreak=bool(cfg.buy_recall_tiebreak),
                        sell_recall_tiebreak=bool(cfg.sell_recall_tiebreak),
                        val_aux_acc=val_aux_acc,
                        val_aux_loss=val_aux_loss,
                        val_prob_gap=val_prob_gap,
                        train_val_gap=max(train_acc - val_acc, 0.0),
                        aux_train_val_gap=aux_train_val_gap,
                    )
                else:
                    current_score = (
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        val_aux_acc,
                        -float(aux_train_val_gap),
                        -float(val_prob_gap),
                        -float(max(train_acc - val_acc, 0.0)),
                        float("-inf"),
                        -val_aux_loss,
                        -val_loss,
                    )

                if current_score > best_score:
                    best_score = current_score
                    best_acc = val_acc
                    best_model_state = copy.deepcopy(model.state_dict())
                    best_head_state = copy.deepcopy(head.state_dict())
                    best_aux_head_state = copy.deepcopy(aux_head.state_dict()) if aux_head is not None else {}
                    best_optimizer_state = copy.deepcopy(optimizer.state_dict())
                    best_epoch = epoch + 1
                    patience_counter = 0
                else:
                    patience_counter += 1

                if (
                    aux_train_val_gap > float(cfg.aux_overfit_tolerance)
                    and epoch + 1 >= max(2, int(cfg.warmup_epochs) + 1)
                ):
                    patience_counter += 1
            else:
                current_score = (
                    train_acc,
                    train_acc,
                    train_acc,
                    0.0,
                    0.0,
                    train_aux_acc,
                    0.0,
                    -float(train_prob_gap),
                    0.0,
                    0.0,
                    -train_aux_loss,
                    -avg_loss,
                )
                if current_score > best_score:
                    best_score = current_score
                    best_acc = train_acc
                    best_model_state = copy.deepcopy(model.state_dict())
                    best_head_state = copy.deepcopy(head.state_dict())
                    best_aux_head_state = copy.deepcopy(aux_head.state_dict()) if aux_head is not None else {}
                    best_optimizer_state = copy.deepcopy(optimizer.state_dict())
                    best_epoch = epoch + 1
                    patience_counter = 0
                else:
                    patience_counter += 1

            history.append(
                {
                    "epoch": float(epoch + 1),
                    "train_loss": float(avg_loss),
                    "train_acc": float(train_acc),
                    "val_loss": float(val_loss),
                    "val_acc": float(val_acc),
                    "val_balanced_accuracy": float(val_balanced_accuracy),
                    "val_macro_f1": float(val_macro_f1),
                    "train_aux_loss": float(train_aux_loss),
                    "train_aux_acc": float(train_aux_acc),
                    "train_prob_gap": float(train_prob_gap),
                    "train_lwf_loss": float(train_lwf_loss),
                    "train_ewc_loss": float(train_ewc_loss),
                    "val_aux_loss": float(val_aux_loss),
                    "val_aux_acc": float(val_aux_acc),
                    "val_prob_gap": float(val_prob_gap),
                    "aux_train_val_gap": float(aux_train_val_gap),
                    "effective_aux_loss_weight": float(current_aux_weight),
                    "decision_threshold": float(val_threshold),
                    "replay_samples": float(len(continual_state.replay_samples)),
                    "continual_learning_enabled": float(1.0 if continual_state.enabled else 0.0),
                }
            )

            if len(optimizer.param_groups) == 1:
                current_backbone_lr = 0.0
                current_head_lr = float(optimizer.param_groups[0]["lr"])
            else:
                current_backbone_lr = float(optimizer.param_groups[0]["lr"])
                current_head_lr = float(optimizer.param_groups[-1]["lr"])

            if verbose:
                print(
                    f"[EPOCH {epoch + 1}/{epochs}] {name} | "
                    f"loss: {avg_loss:.4f} | train_acc: {train_acc:.2f}% | "
                    f"val_loss: {val_loss:.4f} | val_acc: {val_acc:.2f}% | "
                    f"val_bal_acc: {val_balanced_accuracy:.2f}% | macro_f1: {val_macro_f1:.2f}% | "
                    f"train_aux: {train_aux_acc:.2f}% | val_aux: {val_aux_acc:.2f}% | "
                    f"aux_delta: {aux_train_val_gap:.2f} | "
                    f"lwf: {train_lwf_loss:.4f} | ewc: {train_ewc_loss:.4f} | "
                    f"train_gap: {train_prob_gap:.3f} | val_gap: {val_prob_gap:.3f} | "
                    f"aux_w: {current_aux_weight:.3f} | "
                    f"backbone_lr: {current_backbone_lr:.2e} | head_lr: {current_head_lr:.2e}"
                )

            scheduler.step()

            if patience_counter >= int(cfg.early_stop_patience):
                if verbose:
                    print(
                        f"[EARLY STOP] {name} [{stage_label}] stopped at epoch {epoch + 1} "
                        f"(best epoch: {best_epoch}, best val acc: {best_acc:.2f}%)"
                    )
                break

        return (
            best_acc,
            cast(tuple[float, float, float, float, float, float, float, float, float, float, float, float, float], best_score),
            history,
            best_model_state,
            best_head_state,
            best_aux_head_state,
            best_optimizer_state,
            best_epoch,
        )

    def _save_bundle(
        self,
        *,
        model_dir: Path,
        model: nn.Module,
        head: nn.Module,
        optimizer: optim.Optimizer,
        optimizer_state_dict: dict[str, Any] | None,
        name: str,
        mode: str,
        history: list[dict[str, float]],
        extra_suffix: str = "",
    ) -> Path:
        save_name = self.MODEL_SPECS[name]["save_name"]
        if extra_suffix:
            save_name = save_name.replace(".pkl", f"{extra_suffix}.pkl")

        save_path = model_dir / save_name
        aux_head = self.sequence_aux_heads.get(name)
        continual_state = self.continual_states.get(name, ContinualTrainingState())
        lora_summary = collect_lora_summary(self._resolve_backbone_module(model))
        existing_runtime_calibration: dict[str, Any] = {}
        existing_metadata_path = model_dir / f"{name}_metadata.json"
        if existing_metadata_path.exists():
            try:
                existing_metadata = json.loads(existing_metadata_path.read_text(encoding="utf-8"))
                if isinstance(existing_metadata, dict):
                    existing_runtime_calibration = dict(
                        cast(Mapping[str, Any], existing_metadata.get("runtime_calibration", {}))
                    )
            except Exception:
                existing_runtime_calibration = {}
        payload: dict[str, Any] = {
            "model_name": name,
            "backbone_state_dict": model.state_dict(),
            "head_state_dict": head.state_dict(),
            "optimizer_state_dict": optimizer_state_dict if optimizer_state_dict is not None else optimizer.state_dict(),
            "mode": mode,
            "feature_dim": int(self.feature_dims.get(name, 0)),
            "training_history": history,
            "best_val_accuracy": float(self.best_val_accuracy.get(name, 0.0)),
            "evaluation_metrics": self.evaluation_metrics.get(name, {}),
            "class_weights": self.class_weights_by_model.get(name, []),
            "temperature": float(self.temperature_scalers.get(name, 1.0)),
            "decision_threshold": float(self.decision_thresholds.get(name, 0.5)),
            "label_map": self._infer_label_map(self.image_dirs),
            "sequence_manifest_path": self.sequence_manifest_path,
            "sequence_aux_loss_weight": float(self.sequence_aux_loss_weight),
            "sequence_task_values": self.sequence_task_values,
            "sequence_aux_metrics": self.sequence_aux_metrics.get(name, {}),
            "runtime_calibration": existing_runtime_calibration,
            "lora": lora_summary,
            "continual_learning": {
                "enabled": bool(continual_state.enabled),
                "previous_bundle_path": str(continual_state.previous_bundle_path),
                "replay_sample_count": int(len(continual_state.replay_samples)),
                "dominant_context_key": str(continual_state.dominant_context_key),
                "adapter_name": str(continual_state.adapter_name),
                "used_lora": bool(continual_state.used_lora),
                "ewc_lambda": float(TRAIN.ewc_lambda),
                "lwf_temperature": float(TRAIN.lwf_temperature),
                "lwf_loss_weight": float(TRAIN.lwf_loss_weight),
            },
        }
        if aux_head is not None:
            payload["aux_head_state_dict"] = aux_head.state_dict()
        print(f"[SAVE] Saving {name} bundle to {save_path} ...")
        torch.save(payload, save_path)
        adapter_file = ""
        if bool(lora_summary.get("enabled", False)):
            adapter_file = self._save_adapter_sidecar(
                model=model,
                name=name,
                adapter_name=str(lora_summary.get("active_adapter", "")),
                target_paths=cast(list[str], lora_summary.get("target_paths", [])),
            )
            if continual_state.dominant_context_key and str(lora_summary.get("active_adapter", "")):
                self._register_context_adapter_mapping(
                    context_key=continual_state.dominant_context_key,
                    adapter_name=str(lora_summary.get("active_adapter", "")),
                    adapter_file=adapter_file,
                )
        print(f"[SAVE DONE] {name} bundle saved to {save_path}")
        return save_path

    @staticmethod
    def check_no_overlap(train_dirs: list[str], val_dirs: list[str]) -> None:
        def collect_paths(dirs: list[str]) -> set[str]:
            paths: set[str] = set()
            for directory in dirs:
                if not os.path.isdir(directory):
                    continue
                for fname in os.listdir(directory):
                    if fname.lower().endswith(_VALID_IMAGE_SUFFIXES):
                        paths.add(os.path.abspath(os.path.join(directory, fname)))
            return paths

        train_paths = collect_paths(train_dirs)
        val_paths = collect_paths(val_dirs)
        overlap = train_paths & val_paths
        if overlap:
            raise RuntimeError(
                "Data leakage detected: "
                f"{len(overlap)} images appear in both train and val sets. "
                f"Example: {list(overlap)[:3]}"
            )

    def fine_tune_all(
        self,
        epochs: int,
        save_dir: str,
        val_dirs: list[str],
        only_models: list[str] | None = None,
        verbose: int = 0,
    ) -> None:
        print("[CHECK] Checking for data leakage between train and val splits...")
        try:
            self.check_no_overlap(self.image_dirs, val_dirs)
            print("[CHECK PASSED] No overlap detected between train and val splits.")
        except Exception as exc:
            print(f"[CHECK FAILED] Data leakage detected: {exc}")
            raise

        print("[INFO] Initializing models and transforms...")
        _set_global_seed(self.random_seed)
        self._init_models()

        if only_models is not None:
            models_to_train = [name for name in only_models if name in self.models]
            skipped_models = [name for name in only_models if name not in self.models]
        else:
            models_to_train = [name for name in self.target_models if name in self.models]
            skipped_models = [name for name in self.target_models if name not in self.models]

        if skipped_models:
            print(f"[WARNING] Skipping models that failed to initialize: {skipped_models}")
        if not models_to_train:
            print("[ERROR] No models available for training. Exiting.")
            return

        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)

        for name in models_to_train:
            if name not in self.models:
                print(f"[SKIP] Model {name} was not initialized due to a previous error. Skipping training.")
                continue

            print(f"\n[TRAINING] Model: {name}")
            print(f"[INFO] Initializing model and head for {name}...")
            model = self.models[name]
            head = self._ensure_head(name)
            aux_head = self._ensure_aux_head(name)
            continual_state = self._prepare_continual_state(
                name=name,
                model=model,
                head=head,
                aux_head=aux_head,
                model_dir=save_path,
            )
            print(f"[INFO] Model {name} initialized. Feature dim: {self.feature_dims.get(name, 'unknown')}")
            if continual_state.enabled:
                print(
                    f"[CONTINUAL] {name} loaded prior bundle from {continual_state.previous_bundle_path} "
                    f"| replay_samples={len(continual_state.replay_samples)} "
                    f"| lora={continual_state.used_lora} "
                    f"| adapter={continual_state.adapter_name or 'none'}"
                )
            if aux_head is not None:
                print(
                    f"[SEQUENCE TEACHER] {name} auxiliary tasks: "
                    f"{', '.join(self._ordered_sequence_task_names(list(self.sequence_task_values.keys())))}"
                )
                self._validate_sequence_supervision(
                    model_name=name,
                    train_dirs=self.image_dirs,
                    val_dirs=val_dirs,
                )

            cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
            best_acc = 0.0
            history: list[dict[str, float]] = []
            best_model_state: dict[str, torch.Tensor] = copy.deepcopy(model.state_dict())
            best_head_state: dict[str, torch.Tensor] = copy.deepcopy(head.state_dict())
            best_aux_head_state: dict[str, torch.Tensor] = (
                copy.deepcopy(aux_head.state_dict())
                if aux_head is not None
                else {}
            )
            best_optimizer_state: dict[str, Any] | None = None

            remaining_epochs = max(epochs, 0)

            if cfg.head_only_epochs > 0 and epochs > 0:
                stage1_epochs = min(cfg.head_only_epochs, epochs)
                remaining_epochs = max(epochs - stage1_epochs, 0)

                self._freeze_all_backbone_params(model)
                if continual_state.used_lora:
                    set_adapter_trainable(
                        self._resolve_backbone_module(model),
                        adapter_name=continual_state.adapter_name,
                        trainable=False,
                    )
                self._set_aux_head_trainable(name, True)
                print(f"[{name}] Stage 1: head-only training ({stage1_epochs} epochs)")
                optimizer = self._optimizer_for_stage(name, model, head, stage="head_only")

                best_acc1, best_score1, history1, best_model_state1, best_head_state1, best_aux_head_state1, best_optimizer_state1, _best_epoch1 = self._run_training_stage(
                    model,
                    head,
                    name,
                    optimizer,
                    stage1_epochs,
                    stage_label="head_only",
                    val_dirs=val_dirs,
                    verbose=verbose,
                )

                best_acc = best_acc1
                best_score = best_score1
                history = history1
                best_model_state = best_model_state1
                best_head_state = best_head_state1
                best_aux_head_state = best_aux_head_state1
                best_optimizer_state = best_optimizer_state1
                stage1_peak_val_acc = max(
                    (float(epoch_metrics.get("val_acc", 0.0)) for epoch_metrics in history1),
                    default=float(best_acc1),
                )
                stage1_peak_bal_acc = max(
                    (float(epoch_metrics.get("val_balanced_accuracy", 0.0)) for epoch_metrics in history1),
                    default=0.0,
                )

                if remaining_epochs > 0:
                    model.load_state_dict(best_model_state1)
                    head.load_state_dict(best_head_state1)
                    if aux_head is not None and best_aux_head_state1:
                        aux_head.load_state_dict(best_aux_head_state1)

                    run_stage2 = True
                    stage2_mode = "full"

                    if continual_state.used_lora:
                        print(
                            f"[{name}] Stage 2: train LoRA adapter '{continual_state.adapter_name}' + head "
                            f"({remaining_epochs} epochs)"
                        )
                        self._freeze_all_backbone_params(model)
                        set_adapter_trainable(
                            self._resolve_backbone_module(model),
                            adapter_name=continual_state.adapter_name,
                            trainable=True,
                        )
                        stage2_mode = "lora_adapter"
                    elif name == "dinov2":
                        if stage1_peak_val_acc >= cfg.unfreeze_threshold:
                            print(
                                f"[DINOv2] Stage 2: unfreeze top DINO blocks with layer-wise LR decay "
                                f"(remaining {remaining_epochs} epochs; "
                                f"stage1 peak val_acc={stage1_peak_val_acc:.2f}%, "
                                f"peak val_bal_acc={stage1_peak_bal_acc:.2f}%)"
                            )
                            self._set_trainable_last_stage(model, name)
                            stage2_mode = "last_block"
                        else:
                            print(
                                f"[DINOv2] Skipping stage 2: stage 1 peak val_acc "
                                f"{stage1_peak_val_acc:.2f}% < threshold {cfg.unfreeze_threshold:.2f}% "
                                f"(peak val_bal_acc={stage1_peak_bal_acc:.2f}%)"
                            )
                            run_stage2 = False
                    else:
                        stage2_mode = str(cfg.stage2_mode).strip().lower() or "last_block"
                        if stage2_mode == "last_block":
                            print(f"[{name}] Stage 2: unfreeze last backbone stage + head ({remaining_epochs} epochs)")
                            self._set_trainable_last_stage(model, name)
                        else:
                            print(f"[{name}] Stage 2: unfreeze backbone + head ({remaining_epochs} epochs)")
                            self._set_backbone_trainable(model, True)

                    if bool(cfg.freeze_aux_head_after_head_only):
                        self._set_aux_head_trainable(name, False)
                    else:
                        self._set_aux_head_trainable(name, True)

                    if run_stage2:
                        optimizer = self._optimizer_for_stage(name, model, head, stage=stage2_mode)
                        best_acc2, best_score2, history2, best_model_state2, best_head_state2, best_aux_head_state2, best_optimizer_state2, _best_epoch2 = self._run_training_stage(
                            model,
                            head,
                            name,
                            optimizer,
                            remaining_epochs,
                            stage_label=stage2_mode,
                            val_dirs=val_dirs,
                            verbose=verbose,
                        )

                        history.extend(history2)

                        if best_score2 > best_score:
                            best_acc = best_acc2
                            best_score = best_score2
                            best_model_state = best_model_state2
                            best_head_state = best_head_state2
                            best_aux_head_state = best_aux_head_state2
                            best_optimizer_state = best_optimizer_state2
                else:
                    optimizer = self._optimizer_for_stage(name, model, head, stage="head_only")
            else:
                if continual_state.used_lora:
                    self._freeze_all_backbone_params(model)
                    set_adapter_trainable(
                        self._resolve_backbone_module(model),
                        adapter_name=continual_state.adapter_name,
                        trainable=True,
                    )
                    stage_name = "lora_adapter"
                else:
                    self._set_backbone_trainable(model, True)
                    stage_name = "full"
                self._set_aux_head_trainable(name, True)
                optimizer = self._optimizer_for_stage(name, model, head, stage=stage_name)
                best_acc, best_score, history, best_model_state, best_head_state, best_aux_head_state, best_optimizer_state, _best_epoch = self._run_training_stage(
                    model,
                    head,
                    name,
                    optimizer,
                    epochs,
                    stage_label=stage_name,
                    val_dirs=val_dirs,
                    verbose=verbose,
                )

            model.load_state_dict(best_model_state)
            head.load_state_dict(best_head_state)
            if aux_head is not None and best_aux_head_state:
                aux_head.load_state_dict(best_aux_head_state)
            self.best_val_accuracy[name] = best_acc
            self.training_history[name] = history
            self.evaluation_metrics[name] = self._evaluate_single(model, name, val_dirs, batch_size=cfg.batch_size)
            if aux_head is not None:
                self.sequence_aux_metrics[name] = {
                    "enabled": True,
                    "loss_weight": float(self.sequence_aux_loss_weight),
                    "task_names": self._ordered_sequence_task_names(list(self.sequence_task_values.keys())),
                    "best_train_aux_acc": max((float(epoch_metrics.get("train_aux_acc", 0.0)) for epoch_metrics in history), default=0.0),
                    "best_val_aux_acc": max((float(epoch_metrics.get("val_aux_acc", 0.0)) for epoch_metrics in history), default=0.0),
                    "manifest_path": self.sequence_manifest_path,
                }
            else:
                self.sequence_aux_metrics[name] = {
                    "enabled": False,
                    "loss_weight": float(self.sequence_aux_loss_weight),
                    "task_names": [],
                    "manifest_path": self.sequence_manifest_path,
                }

            current_metrics = self.evaluation_metrics.get(name, {})
            current_score = self._artifact_score(current_metrics)
            existing_metadata = self._load_existing_metadata(save_path, name)
            if existing_metadata is not None:
                existing_metrics = existing_metadata.get("evaluation_metrics", {})
                existing_score = self._artifact_score(existing_metrics)
                if current_score <= existing_score:
                    print(
                        f"[KEEP] Existing saved {name} bundle remains in place "
                        f"(existing score={existing_score}, current score={current_score})."
                    )
                    print(f"[TRAINING DONE] Model: {name} | Best val acc: {best_acc:.2f}%\n")
                    continue

            mode = self.MODEL_SPECS[name]["mode"]
            print(f"[INFO] Saving model bundle for {name}...")
            self._save_bundle(
                model_dir=save_path,
                model=model,
                head=head,
                optimizer=optimizer,
                optimizer_state_dict=best_optimizer_state,
                name=name,
                mode=mode,
                history=history,
            )
            self._save_sidecar_artifacts(
                model_dir=save_path,
                model=model,
                head=head,
                name=name,
                val_dirs=val_dirs,
                batch_size=cfg.batch_size,
            )
            print(f"[TRAINING DONE] Model: {name} | Best val acc: {best_acc:.2f}%\n")

    def _evaluate_single(
        self,
        model: nn.Module,
        name: str,
        val_dirs: list[str],
        batch_size: int = 16,
    ) -> dict[str, Any]:
        head = self._ensure_head(name)
        cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
        split_outputs = self._collect_split_outputs(
            model=model,
            head=head,
            name=name,
            image_dirs=val_dirs,
            batch_size=max(1, min(batch_size, cfg.batch_size)),
        )

        logits = cast(torch.Tensor, split_outputs["logits"])
        labels = cast(torch.Tensor, split_outputs["labels"])

        temperature = self._fit_temperature(name, logits, labels) if logits.numel() > 0 else 1.0
        self.temperature_scalers[name] = float(temperature)

        calibrated_logits = logits / temperature if logits.numel() > 0 else logits
        avg_loss = float(F.cross_entropy(calibrated_logits, labels).item()) if labels.numel() > 0 else 0.0

        buy_probs = (
            torch.softmax(calibrated_logits, dim=-1)[:, 0]
            if calibrated_logits.numel() > 0
            else torch.empty((0,), dtype=torch.float32)
        )
        threshold: float
        threshold_metrics: dict[str, Any]

        if labels.numel() > 0:
            cfg = TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"])
            threshold, threshold_metrics = self._select_best_threshold(
                buy_probs,
                labels,
                target_min_recall=float(cfg.target_min_recall),
                prefer_buy_recall=bool(cfg.buy_recall_tiebreak),
                prefer_sell_recall=bool(cfg.sell_recall_tiebreak),
            )
        else:
            threshold = 0.5
            threshold_metrics = {
                "threshold": 0.5,
                "confusion_matrix": [[0, 0], [0, 0]],
                "accuracy": 0.0,
                "buy_recall": 0.0,
                "sell_recall": 0.0,
                "buy_precision": 0.0,
                "sell_precision": 0.0,
                "buy_f1": 0.0,
                "sell_f1": 0.0,
                "balanced_accuracy": 0.0,
                "macro_f1": 0.0,
                "min_recall": 0.0,
            }
        threshold = float(threshold)
        self.decision_thresholds[name] = float(threshold)

        confusion_matrix = cast(list[list[int]], threshold_metrics["confusion_matrix"])
        correct = int(confusion_matrix[0][0] + confusion_matrix[1][1])
        total = int(sum(sum(row) for row in confusion_matrix))
        acc = float(threshold_metrics["accuracy"])

        result: dict[str, Any] = {
            "model": name,
            "validation_accuracy": acc,
            "validation_loss": avg_loss,
            "correct": correct,
            "total": total,
            "confusion_matrix": confusion_matrix,
            "buy_recall": float(threshold_metrics["buy_recall"]),
            "sell_recall": float(threshold_metrics["sell_recall"]),
            "balanced_accuracy": float(threshold_metrics["balanced_accuracy"]),
            "macro_f1": float(threshold_metrics["macro_f1"]),
            "buy_precision": float(threshold_metrics["buy_precision"]),
            "sell_precision": float(threshold_metrics["sell_precision"]),
            "buy_f1": float(threshold_metrics["buy_f1"]),
            "sell_f1": float(threshold_metrics["sell_f1"]),
            "temperature": float(temperature),
            "decision_threshold": float(threshold),
        }
        print(f"Model {name} validation accuracy: {acc:.2f}% ({correct}/{total})")
        print(f"Model {name} confusion matrix [true x pred]:")
        print(f"  BUY : {confusion_matrix[0]}")
        print(f"  SELL: {confusion_matrix[1]}")
        print(
            f"Model {name} BUY recall: {float(result['buy_recall']):.2f}% | "
            f"SELL recall: {float(result['sell_recall']):.2f}% | "
            f"Balanced accuracy: {float(result['balanced_accuracy']):.2f}% | "
            f"Macro F1: {float(result['macro_f1']):.2f}%"
        )
        return result

    def evaluate(self, val_dirs: list[str], batch_size: int = 16) -> None:
        for name, model in self.models.items():
            result = self._evaluate_single(model, name, val_dirs, batch_size=batch_size)
            print(
                f"Model {name} validation accuracy: {float(result['validation_accuracy']):.2f}% "
                f"({int(result['correct'])}/{int(result['total'])})"
            )

    def predict_ensemble(self, image: Image.Image) -> dict[str, Any]:
        results: dict[str, Any] = {}
        buy_probs: list[float] = []
        sell_probs: list[float] = []

        for name, model in self.models.items():
            head = self.heads.get(name)
            if head is None:
                continue

            transform = self.eval_transforms.get(name)
            if transform is None:
                transform = build_basic_transform(
                    TRAIN_CONFIGS.get(name, TRAIN_CONFIGS["mobilenetv3"]).input_size,
                    is_training=False,
                )

            img_tensor = transform(image.convert("RGB")).unsqueeze(0).to(self.device)

            model.eval()
            head.eval()

            with torch.no_grad():
                features = forward_features(model, img_tensor)
                if name == "dinov2":
                    features = F.normalize(features, p=2, dim=1)
                logits = head(features)
                temperature = float(self.temperature_scalers.get(name, 1.0))
                calibrated_logits = logits / temperature
                probabilities = torch.softmax(calibrated_logits, dim=-1).detach().cpu().numpy()
                buy_prob = float(probabilities[0, 0].item()) if probabilities.shape[-1] > 0 else 0.0
                sell_prob = float(probabilities[0, 1].item()) if probabilities.shape[-1] > 1 else 0.0

            results[name] = {"buy_prob": buy_prob, "sell_prob": sell_prob}
            buy_probs.append(buy_prob)
            sell_probs.append(sell_prob)

        results["ensemble"] = {
            "buy_prob": float(sum(buy_probs) / len(buy_probs)) if buy_probs else 0.0,
            "sell_prob": float(sum(sell_probs) / len(sell_probs)) if sell_probs else 0.0,
        }
        return results
