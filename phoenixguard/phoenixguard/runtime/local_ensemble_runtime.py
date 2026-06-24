from __future__ import annotations

import gc
import importlib
import json
import math
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Protocol, Sequence, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image
import torch
import torch.nn.functional as F
from torch import Tensor

from phoenixguard.runtime.continual_adapters import (
    AdapterConfig,
    apply_lora_adapters,
    available_adapters,
    sanitize_adapter_name,
    set_active_adapter,
)
from phoenixguard.runtime.inference_exports import (
    export_aux_head_path,
    export_backbone_path,
    export_head_path,
    export_metadata_path,
    export_onnx_path,
    load_state_dict_safetensors,
    read_export_metadata,
)

class _OrtNodeArgLike(Protocol):
    name: str


class _OrtSessionLike(Protocol):
    def get_inputs(self) -> Sequence[_OrtNodeArgLike]:
        ...

    def get_outputs(self) -> Sequence[_OrtNodeArgLike]:
        ...


class _OrtModuleLike(Protocol):
    InferenceSession: Callable[..., _OrtSessionLike]

    def get_available_providers(self) -> Sequence[str]:
        ...


def _load_optional_onnxruntime() -> _OrtModuleLike | None:
    try:
        module = importlib.import_module("onnxruntime")
        return cast(_OrtModuleLike, module)
    except Exception:  # pragma: no cover - optional runtime dependency
        return None


ort: _OrtModuleLike | None = _load_optional_onnxruntime()

_EnsembleCVSymbols = tuple[type[Any], dict[str, Any], Any, Any, type[Any]]
_ensemble_cv_symbols_cache: _EnsembleCVSymbols | None = None


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _empty_sequence_task_values() -> dict[str, list[str]]:
    return {}


def _empty_runtime_calibration() -> dict[str, Any]:
    return {}


def _tensor_from_numpy(value: NDArray[np.float32]) -> Tensor:
    from_numpy = cast(Callable[[NDArray[np.float32]], Tensor], getattr(torch, "from_numpy"))
    return from_numpy(value)


def _tensor_to_float_array(tensor: Tensor) -> NDArray[np.float32]:
    to_numpy = cast(Callable[[], Any], getattr(tensor, "numpy"))
    return np.asarray(to_numpy(), dtype=np.float32)


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def _sequence_task_values(value: Any) -> dict[str, list[str]]:
    task_values: dict[str, list[str]] = {}
    for task_name, raw_values in _mapping(value).items():
        values = [str(item) for item in _sequence(raw_values)]
        if values:
            task_values[str(task_name)] = values
    return task_values


def _load_ensemble_cv_symbols() -> _EnsembleCVSymbols:
    global _ensemble_cv_symbols_cache
    if _ensemble_cv_symbols_cache is None:
        module = importlib.import_module("phoenixguard.training.ensemble_cv_models")
        ensemble_cls = cast(type[Any], getattr(module, "EnsembleCVModels"))
        train_configs = cast(dict[str, Any], getattr(module, "TRAIN_CONFIGS"))
        forward_fn = getattr(module, "forward_features")
        build_transform_fn = getattr(module, "build_basic_transform")
        aux_head_cls = cast(type[Any], getattr(module, "SequenceAuxiliaryHead"))
        _ensemble_cv_symbols_cache = (ensemble_cls, train_configs, forward_fn, build_transform_fn, aux_head_cls)
    return _ensemble_cv_symbols_cache


@dataclass(slots=True)
class RuntimeModelInfo:
    name: str
    role: str
    live_enabled: bool
    base_weight: float
    bundle_path: Path | None
    metrics: dict[str, Any]
    temperature: float = 1.0
    decision_threshold: float = 0.5
    feature_dim: int = 0
    sequence_task_values: dict[str, list[str]] = field(default_factory=_empty_sequence_task_values)
    runtime_calibration: dict[str, Any] = field(default_factory=_empty_runtime_calibration)
    export_metadata_path: Path | None = None
    backbone_weights_path: Path | None = None
    head_weights_path: Path | None = None
    aux_head_weights_path: Path | None = None
    onnx_path: Path | None = None
    exported_active_adapter: str = ""


class LegacyFallbackApprovalRequired(RuntimeError):
    def __init__(
        self,
        *,
        model_name: str,
        reason: str,
        bundle_path: Path | None,
        export_ready: bool,
    ) -> None:
        self.model_name = str(model_name)
        self.reason = str(reason)
        self.bundle_path = bundle_path
        self.export_ready = bool(export_ready)
        super().__init__(
            f"Legacy council fallback approval required for {self.model_name}: {self.reason}"
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "reason": self.reason,
            "bundle_path": str(self.bundle_path) if self.bundle_path is not None else "",
            "export_ready": bool(self.export_ready),
        }


class LocalCVEnsembleRuntime:
    """
    Runtime loader for saved local CV bundles.

    The training code saves full Torch checkpoints, temperatures, and thresholds.
    This runtime reuses the backbone/head initialization logic from
    ``EnsembleCVModels`` but loads the already-trained checkpoints instead of
    fine-tuning on app startup.
    """

    DEFAULT_MODELS: tuple[str, ...] = (
        "mobilenetv3",
        "clip",
        "simclr",
        "swav",
        "dinov2",
        "byol",
    )
    CPU_DEFAULT_MODELS: tuple[str, ...] = (
        "mobilenetv3",
        "simclr",
        "swav",
    )

    SHADOW_MODELS: frozenset[str] = frozenset()

    MODEL_ROLES: dict[str, str] = {
        "mobilenetv3": "execution_specialist",
        "clip": "buy_specialist",
        "simclr": "sell_specialist",
        "swav": "generalist",
        "dinov2": "structure_specialist",
        "byol": "buy_specialist",
    }

    ROLE_MULTIPLIERS: dict[str, float] = {
        "mobilenetv3": 1.05,
        "clip": 1.08,
        "simclr": 1.09,
        "swav": 1.12,
        "dinov2": 1.00,
        "byol": 0.96,
    }

    CPU_ALWAYS_ON_MODELS: tuple[str, ...] = (
        "mobilenetv3",
        "swav",
    )
    GPU_ALWAYS_ON_MODELS: tuple[str, ...] = (
        "mobilenetv3",
        "swav",
        "dinov2",
    )

    def __init__(
        self,
        *,
        image_dirs: Sequence[str],
        model_dir: str | Path,
        compute_device: torch.device,
        logger: Any,
        target_models: Sequence[str] | None = None,
    ) -> None:
        self.logger = logger
        self.model_dir = Path(model_dir)
        requested_device = torch.device(compute_device)
        if requested_device.type == "cuda" and not torch.cuda.is_available():
            self.logger.warning(
                "Requested local ensemble device '%s' is unavailable. Falling back to CPU.",
                requested_device,
            )
            requested_device = torch.device("cpu")
        self.compute_device = requested_device
        self.storage_device = torch.device("cpu")
        self._image_dirs = [str(path) for path in image_dirs]
        self.requested_models = self.resolve_requested_models(target_models, requested_device)
        self.max_loaded_models = self._resolve_max_loaded_models(requested_device)
        self.loaded_model_names: list[str] = []
        self.failed_models: dict[str, str] = {}
        self.model_info: dict[str, RuntimeModelInfo] = {}
        self._loaded_runtimes: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._load_lock = Lock()
        self._predict_lock = Lock()
        for name in self.DEFAULT_MODELS:
            if name not in self.requested_models:
                self.failed_models[name] = f"disabled_by_runtime_profile:{requested_device.type}"
        (
            self._ensemble_cls,
            self._train_configs,
            self._forward_features,
            self._build_basic_transform,
            self._sequence_aux_head_cls,
        ) = _load_ensemble_cv_symbols()

        self._ensemble = self._ensemble_cls(
            self._image_dirs,
            self.storage_device,
            [name for name in self.requested_models if name in self._ensemble_cls.MODEL_SPECS],
            pretrained_backbones=False,
        )
        self.logger.info(
            "Local ensemble target models on %s: %s",
            requested_device.type,
            ", ".join(self.requested_models) if self.requested_models else "none",
        )
        self._discover_saved_bundles()

        if not self.loaded_model_names:
            raise RuntimeError(
                f"No saved local ensemble bundles could be loaded from {self.model_dir}."
            )

    @classmethod
    def resolve_requested_models(
        cls,
        target_models: Sequence[str] | None,
        requested_device: torch.device,
    ) -> list[str]:
        return cls._resolve_requested_models(target_models, requested_device)

    @classmethod
    def _resolve_requested_models(
        cls,
        target_models: Sequence[str] | None,
        requested_device: torch.device,
    ) -> list[str]:
        if target_models is not None:
            raw_models = [str(name).strip() for name in target_models]
        else:
            env_models = str(os.getenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", "") or "").strip()
            if env_models:
                raw_models = [part.strip() for part in env_models.split(",")]
            elif requested_device.type == "cpu" and str(os.getenv("PHOENIXGUARD_FORCE_FULL_COUNCIL_ON_CPU", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
                raw_models = list(cls.DEFAULT_MODELS)
            elif requested_device.type == "cpu":
                raw_models = list(cls.CPU_DEFAULT_MODELS)
            else:
                raw_models = list(cls.DEFAULT_MODELS)

        seen: set[str] = set()
        resolved: list[str] = []
        valid = set(cls.DEFAULT_MODELS)
        for name in raw_models:
            if not name or name in seen or name not in valid:
                continue
            resolved.append(name)
            seen.add(name)
        if resolved:
            return resolved
        return list(cls.CPU_DEFAULT_MODELS if requested_device.type == "cpu" else cls.DEFAULT_MODELS)

    @classmethod
    def _resolve_max_loaded_models(cls, requested_device: torch.device) -> int:
        raw = str(os.getenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MAX_LOADED", "") or "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except Exception:
                pass
        return 2 if requested_device.type == "cpu" else len(cls.DEFAULT_MODELS)

    @staticmethod
    def _metric_percent(metrics: dict[str, Any], keys: Sequence[str], default: float = 50.0) -> float:
        for key in keys:
            value = metrics.get(key)
            if isinstance(value, (float, int)):
                return float(value)
        return float(default)

    def _base_weight_for_model(self, name: str, metrics: dict[str, Any]) -> float:
        balanced = self._metric_percent(metrics, ("balanced_accuracy",), default=50.0) / 100.0
        macro_f1 = self._metric_percent(metrics, ("macro_f1",), default=50.0) / 100.0
        accuracy = self._metric_percent(metrics, ("validation_accuracy", "accuracy"), default=50.0) / 100.0
        buy_recall = self._metric_percent(metrics, ("buy_recall",), default=50.0) / 100.0
        sell_recall = self._metric_percent(metrics, ("sell_recall",), default=50.0) / 100.0
        recall_balance = 1.0 - min(abs(buy_recall - sell_recall), 1.0)
        base_score = 0.42 * balanced + 0.28 * macro_f1 + 0.18 * accuracy + 0.12 * recall_balance
        role_factor = float(self.ROLE_MULTIPLIERS.get(name, 1.0))
        if name in self.SHADOW_MODELS:
            role_factor *= 0.55
        return float(max(base_score * role_factor, 1e-4))

    def _load_saved_bundles(self) -> None:
        self._discover_saved_bundles()

    def _load_saved_metadata(self, name: str) -> dict[str, Any]:
        metadata_path = self.model_dir / f"{name}_metadata.json"
        if not metadata_path.exists():
            return {}
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            return _mapping(raw)
        except Exception:
            return {}

    def _load_inference_export_metadata(self, name: str) -> dict[str, Any]:
        return read_export_metadata(self.model_dir, name)

    @staticmethod
    def _supports_onnx_runtime() -> bool:
        return ort is not None

    def _onnx_providers(self) -> list[str]:
        if ort is None:
            return []
        available = {str(item) for item in _sequence(ort.get_available_providers())}
        providers: list[str] = []
        if self.compute_device.type == "cuda" and "CUDAExecutionProvider" in available:
            providers.append("CUDAExecutionProvider")
        if "CPUExecutionProvider" in available:
            providers.append("CPUExecutionProvider")
        return providers or list(available)

    def _load_aux_head(
        self,
        *,
        info: RuntimeModelInfo,
    ) -> Any | None:
        if not info.sequence_task_values:
            return None
        aux_path = info.aux_head_weights_path
        if aux_path is None or not aux_path.exists():
            return None
        feature_dim = int(max(info.feature_dim, 0))
        if feature_dim <= 0:
            return None
        aux_head = self._sequence_aux_head_cls(feature_dim, info.sequence_task_values)
        aux_state = load_state_dict_safetensors(aux_path, device=self.storage_device)
        aux_head.load_state_dict(aux_state)
        aux_head.to(self.storage_device)
        aux_head.eval()
        return aux_head

    def _apply_lora_payload(
        self,
        *,
        ensemble: Any,
        model: Any,
        lora_payload: Mapping[str, Any],
    ) -> str:
        if not bool(lora_payload.get("enabled", False)):
            return ""
        backbone_module = ensemble._resolve_backbone_module(model)
        target_paths = [str(item) for item in _sequence(lora_payload.get("target_paths", []))]
        adapter_specs = {str(name): _mapping(spec) for name, spec in _mapping(lora_payload.get("adapter_specs", {})).items()}
        active_adapter = sanitize_adapter_name(str(lora_payload.get("active_adapter", "continual_default")))
        primary_spec = _mapping(adapter_specs.get(active_adapter, {}))
        apply_lora_adapters(
            backbone_module,
            adapter_name=active_adapter,
            target_paths=target_paths,
            config=AdapterConfig(
                rank=int(primary_spec.get("rank", 8) or 8),
                alpha=float(primary_spec.get("alpha", 16.0) or 16.0),
                dropout=float(primary_spec.get("dropout", 0.05) or 0.05),
            ),
        )
        for adapter_name, adapter_spec in adapter_specs.items():
            if sanitize_adapter_name(adapter_name) == active_adapter:
                continue
            apply_lora_adapters(
                backbone_module,
                adapter_name=adapter_name,
                target_paths=target_paths,
                config=AdapterConfig(
                    rank=int(adapter_spec.get("rank", 8) or 8),
                    alpha=float(adapter_spec.get("alpha", 16.0) or 16.0),
                    dropout=float(adapter_spec.get("dropout", 0.05) or 0.05),
                ),
            )
        set_active_adapter(backbone_module, active_adapter)
        return active_adapter

    def _load_runtime_from_export(
        self,
        *,
        info: RuntimeModelInfo,
        ensemble: Any,
        model: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], float, float, float]:
        if info.backbone_weights_path is None or info.head_weights_path is None:
            raise RuntimeError(f"Inference export paths are incomplete for {info.name}.")
        export_metadata = self._load_inference_export_metadata(info.name)
        lora_payload = _mapping(export_metadata.get("lora", {}))
        if bool(lora_payload.get("enabled", False)):
            self._apply_lora_payload(ensemble=ensemble, model=model, lora_payload=lora_payload)
        head = ensemble._ensure_head(info.name)
        model.load_state_dict(load_state_dict_safetensors(info.backbone_weights_path, device=self.storage_device))
        head.load_state_dict(load_state_dict_safetensors(info.head_weights_path, device=self.storage_device))
        aux_head = self._load_aux_head(info=info)
        runtime: dict[str, Any] = {
            "ensemble": ensemble,
            "model": model,
            "head": head,
            "aux_head": aux_head,
        }
        onnx_path = info.onnx_path
        if (
            onnx_path is not None
            and onnx_path.exists()
            and self._supports_onnx_runtime()
            and ort is not None
        ):
            try:
                session = ort.InferenceSession(str(onnx_path), providers=self._onnx_providers())
                runtime["onnx_session"] = session
                runtime["onnx_input_name"] = str(session.get_inputs()[0].name)
                runtime["onnx_output_names"] = [str(node.name) for node in session.get_outputs()]
                runtime["onnx_exported_adapter"] = str(info.exported_active_adapter or "")
            except Exception as exc:
                self.logger.warning("ONNX runtime init failed for %s: %s", info.name, exc)
        metrics = _mapping(export_metadata.get("evaluation_metrics", info.metrics))
        if bool(lora_payload.get("enabled", False)):
            metrics["lora"] = {
                "active_adapter": str(export_metadata.get("exported_active_adapter", "")),
                "available_adapters": available_adapters(ensemble._resolve_backbone_module(model)),
            }
        temperature = float(export_metadata.get("temperature", info.temperature) or info.temperature)
        decision_threshold = float(export_metadata.get("decision_threshold", info.decision_threshold) or info.decision_threshold)
        best_val_accuracy = float(export_metadata.get("best_val_accuracy", 0.0) or 0.0)
        return runtime, metrics, temperature, decision_threshold, best_val_accuracy

    def _load_runtime_from_legacy_bundle(
        self,
        *,
        info: RuntimeModelInfo,
        ensemble: Any,
        model: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], float, float, float]:
        if info.bundle_path is None:
            raise RuntimeError(f"Legacy bundle path is unavailable for {info.name}.")
        try:
            payload = _mapping(torch.load(
                info.bundle_path,
                map_location=self.storage_device,
                weights_only=False,
            ))
        except TypeError:
            payload = _mapping(torch.load(
                info.bundle_path,
                map_location=self.storage_device,
            ))
        head = ensemble._ensure_head(info.name)
        lora_payload = _mapping(payload.get("lora", {}))
        if bool(lora_payload.get("enabled", False)):
            self._apply_lora_payload(ensemble=ensemble, model=model, lora_payload=lora_payload)
        model.load_state_dict(payload["backbone_state_dict"])
        head.load_state_dict(payload["head_state_dict"])
        aux_head = None
        if info.sequence_task_values and isinstance(payload.get("aux_head_state_dict"), dict) and info.feature_dim > 0:
            aux_head = self._sequence_aux_head_cls(info.feature_dim, info.sequence_task_values)
            aux_head.load_state_dict(cast(dict[str, Any], payload["aux_head_state_dict"]))
            aux_head.to(self.storage_device)
            aux_head.eval()
        runtime: dict[str, Any] = {
            "ensemble": ensemble,
            "model": model,
            "head": head,
            "aux_head": aux_head,
        }
        metrics = _mapping(payload.get("evaluation_metrics", info.metrics))
        if bool(lora_payload.get("enabled", False)):
            metrics["lora"] = {
                "active_adapter": str(lora_payload.get("active_adapter", "")),
                "available_adapters": available_adapters(ensemble._resolve_backbone_module(model)),
            }
        temperature = float(payload.get("temperature", info.temperature))
        decision_threshold = float(payload.get("decision_threshold", info.decision_threshold))
        best_val_accuracy = float(payload.get("best_val_accuracy", 0.0) or 0.0)
        return runtime, metrics, temperature, decision_threshold, best_val_accuracy

    @staticmethod
    def _normalize_sequence_task_value(task_name: str, value: str) -> str:
        uppercase_tasks = {
            "projection_direction",
            "current_box_direction",
            "next_box_direction",
            "macro_trend",
        }
        return str(value).strip().upper() if task_name in uppercase_tasks else str(value).strip().lower()

    def _predict_sequence_tasks(
        self,
        *,
        aux_head: Any | None,
        sequence_task_values: Mapping[str, Sequence[str]],
        features: Tensor,
    ) -> dict[str, dict[str, Any]]:
        if aux_head is None or not sequence_task_values:
            return {}
        aux_module: Any = aux_head
        aux_module = aux_module.to(features.device)
        aux_module.eval()
        with torch.inference_mode():
            aux_logits = cast(dict[str, Tensor], aux_module(features))
        predictions: dict[str, dict[str, Any]] = {}
        for task_name, logits in aux_logits.items():
            values = [str(item) for item in sequence_task_values.get(task_name, [])]
            if not values or logits.numel() == 0:
                continue
            probs = torch.softmax(logits.detach(), dim=-1)[0].cpu().to(torch.float32)
            best_idx = int(torch.argmax(probs).item())
            if best_idx >= len(values):
                continue
            predictions[str(task_name)] = {
                "value": self._normalize_sequence_task_value(task_name, values[best_idx]),
                "confidence": float(probs[best_idx].item()),
            }
        return predictions

    @staticmethod
    def _aggregate_sequence_task_consensus(
        model_outputs: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        task_votes: dict[str, dict[str, float]] = {}
        task_counts: dict[str, int] = {}
        for row in model_outputs.values():
            if not bool(row.get("live_enabled", False)):
                continue
            dynamic_weight = float(max(0.0, float(row.get("dynamic_weight", 0.0) or 0.0)))
            tasks = cast(Mapping[str, Any], row.get("sequence_tasks", {}))
            for task_name, raw_payload in tasks.items():
                payload = cast(Mapping[str, Any], raw_payload)
                value = str(payload.get("value", "")).strip()
                if not value:
                    continue
                confidence = float(np.clip(payload.get("confidence", 0.0), 0.0, 1.0))
                weighted_vote = max(1e-6, dynamic_weight) * max(0.05, confidence)
                task_votes.setdefault(str(task_name), {})
                task_votes[str(task_name)][value] = float(task_votes[str(task_name)].get(value, 0.0) + weighted_vote)
                task_counts[str(task_name)] = int(task_counts.get(str(task_name), 0) + 1)
        consensus: dict[str, dict[str, Any]] = {}
        for task_name, vote_map in task_votes.items():
            total = float(sum(vote_map.values()))
            if total <= 0.0:
                continue
            best_value, best_score = max(vote_map.items(), key=lambda item: item[1])
            consensus[task_name] = {
                "value": str(best_value),
                "confidence": float(np.clip(best_score / total, 0.0, 1.0)),
                "support": float(best_score),
                "n_models": int(task_counts.get(task_name, 0)),
            }
        return consensus

    def _discover_saved_bundles(self) -> None:
        for name in self.requested_models:
            spec = self._ensemble_cls.MODEL_SPECS.get(name)
            if spec is None:
                self.failed_models[name] = "unknown_model"
                continue

            bundle_path = self.model_dir / spec["save_name"]
            export_metadata = self._load_inference_export_metadata(name)
            export_meta_path = export_metadata_path(self.model_dir, name)
            has_export = bool(export_metadata) and export_meta_path.exists()
            if not bundle_path.exists() and not has_export:
                self.failed_models[name] = f"missing_bundle:{bundle_path.name}"
                continue

            try:
                metadata = export_metadata if has_export else self._load_saved_metadata(name)
                metrics = _mapping(metadata.get("evaluation_metrics", {}))
                base_weight = self._base_weight_for_model(name, metrics)
                aux_path = export_aux_head_path(self.model_dir, name)
                onnx_path = export_onnx_path(self.model_dir, name)
                self.model_info[name] = RuntimeModelInfo(
                    name=name,
                    role=self.MODEL_ROLES.get(name, "generalist"),
                    live_enabled=name not in self.SHADOW_MODELS,
                    base_weight=base_weight,
                    bundle_path=bundle_path if bundle_path.exists() else None,
                    metrics=metrics,
                    temperature=float(metadata.get("temperature", 1.0) or 1.0),
                    decision_threshold=float(metadata.get("decision_threshold", 0.5) or 0.5),
                    feature_dim=int(metadata.get("feature_dim", 0) or 0),
                    sequence_task_values=_sequence_task_values(metadata.get("sequence_task_values", {})),
                    runtime_calibration=_mapping(metadata.get("runtime_calibration", {})),
                    export_metadata_path=export_meta_path if has_export else None,
                    backbone_weights_path=export_backbone_path(self.model_dir, name) if has_export else None,
                    head_weights_path=export_head_path(self.model_dir, name) if has_export else None,
                    aux_head_weights_path=aux_path if has_export and aux_path.exists() else None,
                    onnx_path=onnx_path if has_export and onnx_path.exists() else None,
                    exported_active_adapter=str(metadata.get("exported_active_adapter", "")).strip(),
                )
                self.loaded_model_names.append(name)
            except Exception as exc:
                self.failed_models[name] = str(exc)
                self.logger.warning("Local ensemble bundle load failed for %s: %s", name, exc)

    def _ensure_model_loaded(self, name: str) -> dict[str, Any]:
        with self._load_lock:
            cached = self._loaded_runtimes.get(name)
            if cached is not None:
                self._loaded_runtimes.move_to_end(name)
                return cached

            info = self.model_info.get(name)
            if info is None:
                raise RuntimeError(f"Model metadata for {name} is unavailable.")

            ensemble = self._ensemble_cls(
                self._image_dirs,
                self.storage_device,
                [name],
                pretrained_backbones=False,
            )
            ensemble._init_models()
            model = ensemble.models.get(name)
            if model is None:
                raise RuntimeError(f"Model init failed for {name}.")

            export_ready = bool(
                info.export_metadata_path is not None
                and info.backbone_weights_path is not None
                and info.head_weights_path is not None
                and info.export_metadata_path.exists()
                and info.backbone_weights_path.exists()
                and info.head_weights_path.exists()
            )
            legacy_bundle_ready = bool(info.bundle_path is not None and info.bundle_path.exists())

            try:
                if export_ready:
                    try:
                        runtime, metrics, temperature, decision_threshold, best_val_accuracy = self._load_runtime_from_export(
                            info=info,
                            ensemble=ensemble,
                            model=model,
                        )
                    except Exception as exc:
                        if not legacy_bundle_ready:
                            raise
                        if not self._legacy_fallback_allowed():
                            raise LegacyFallbackApprovalRequired(
                                model_name=name,
                                reason=f"Export load failed: {exc}",
                                bundle_path=info.bundle_path,
                                export_ready=True,
                            ) from exc
                        runtime, metrics, temperature, decision_threshold, best_val_accuracy = self._load_runtime_from_legacy_bundle(
                            info=info,
                            ensemble=ensemble,
                            model=model,
                        )
                        metrics = dict(metrics)
                        metrics["legacy_fallback_reason"] = str(exc)
                        metrics["runtime_bundle_mode"] = "legacy_pickle"
                else:
                    if legacy_bundle_ready and not self._legacy_fallback_allowed():
                        raise LegacyFallbackApprovalRequired(
                            model_name=name,
                            reason="Lean export bundle is unavailable, so the runtime would need the legacy .pkl checkpoint.",
                            bundle_path=info.bundle_path,
                            export_ready=False,
                        )
                    runtime, metrics, temperature, decision_threshold, best_val_accuracy = self._load_runtime_from_legacy_bundle(
                        info=info,
                        ensemble=ensemble,
                        model=model,
                    )
                    metrics = dict(metrics)
                    metrics["runtime_bundle_mode"] = "legacy_pickle"
                ensemble.temperature_scalers[name] = temperature
                ensemble.decision_thresholds[name] = decision_threshold
                ensemble.evaluation_metrics[name] = metrics
                ensemble.best_val_accuracy[name] = float(best_val_accuracy)
                self.model_info[name] = RuntimeModelInfo(
                    name=info.name,
                    role=info.role,
                    live_enabled=info.live_enabled,
                    base_weight=self._base_weight_for_model(name, metrics),
                    bundle_path=info.bundle_path,
                    metrics=metrics,
                    temperature=temperature,
                    decision_threshold=decision_threshold,
                    feature_dim=info.feature_dim,
                    sequence_task_values=dict(info.sequence_task_values),
                    runtime_calibration=dict(info.runtime_calibration),
                    export_metadata_path=info.export_metadata_path,
                    backbone_weights_path=info.backbone_weights_path,
                    head_weights_path=info.head_weights_path,
                    aux_head_weights_path=info.aux_head_weights_path,
                    onnx_path=info.onnx_path,
                    exported_active_adapter=info.exported_active_adapter,
                )
                self._loaded_runtimes[name] = runtime
                self._loaded_runtimes.move_to_end(name)
                self._evict_loaded_models(exclude={name})
                self.failed_models.pop(name, None)
                return runtime
            except LegacyFallbackApprovalRequired:
                raise
            except Exception as exc:
                self.failed_models[name] = str(exc)
                self.logger.warning("Local ensemble bundle load failed for %s: %s", name, exc)
                raise

    def _evict_loaded_models(self, exclude: set[str] | None = None) -> None:
        exclude = exclude or set()
        while len(self._loaded_runtimes) > max(1, int(self.max_loaded_models)):
            eviction_candidates = [model_name for model_name in self._loaded_runtimes.keys() if model_name not in exclude]
            if not eviction_candidates:
                break
            old_name = eviction_candidates[0]
            old_runtime = self._loaded_runtimes.pop(old_name, None)
            if old_runtime is None:
                continue
            try:
                cast(Any, old_runtime.get("model")).to(self.storage_device)
                cast(Any, old_runtime.get("head")).to(self.storage_device)
                aux_head = old_runtime.get("aux_head")
                if aux_head is not None:
                    aux_head.to(self.storage_device)
            except Exception:
                pass
            del old_runtime
            gc.collect()
            if self.compute_device.type == "cuda":
                torch.cuda.empty_cache()
            self.logger.info("Evicted model council runtime for %s from resident cache.", old_name)

    @staticmethod
    def _normalized_entropy(buy_prob: float, sell_prob: float) -> float:
        probs = np.array([buy_prob, sell_prob], dtype=np.float64)
        probs = np.clip(probs, 1e-8, 1.0)
        probs /= max(float(probs.sum()), 1e-12)
        entropy = -float(np.sum(probs * np.log(probs)))
        return float(entropy / math.log(2.0))

    @staticmethod
    def _threshold_adjusted_buy_support(
        buy_prob: float,
        decision_threshold: float,
        *,
        support_mode: str = "",
        predicted_label: str = "",
        route_direction: str = "",
        route_strength: float = 0.0,
    ) -> float:
        clipped_buy = float(np.clip(buy_prob, 0.0, 1.0))
        mode = str(support_mode).strip().lower()
        if mode == "threshold_centered":
            threshold = float(np.clip(decision_threshold, 1e-6, 1.0 - 1e-6))
            if clipped_buy >= threshold:
                return float(
                    np.clip(
                        0.5 + 0.5 * ((clipped_buy - threshold) / max(1.0 - threshold, 1e-6)),
                        0.0,
                        1.0,
                    )
                )
            return float(np.clip(0.5 * (clipped_buy / max(threshold, 1e-6)), 0.0, 1.0))
        return clipped_buy

    @staticmethod
    def _resolve_effective_decision_threshold(
        row: Mapping[str, Any],
        *,
        route_direction: str = "",
    ) -> float:
        threshold = float(np.clip(row.get("decision_threshold", 0.5), 0.0, 1.0))
        if not route_direction:
            return threshold
        calibration = cast(Mapping[str, Any], row.get("runtime_calibration", {}))
        route_thresholds = cast(Mapping[str, Any], calibration.get("route_decision_thresholds", {}))
        override = route_thresholds.get(route_direction)
        if isinstance(override, (float, int)):
            return float(np.clip(float(override), 0.0, 1.0))
        return threshold

    @staticmethod
    def _resolve_support_mode(
        row: Mapping[str, Any],
        *,
        route_direction: str = "",
    ) -> str:
        calibration = cast(Mapping[str, Any], row.get("runtime_calibration", {}))
        route_modes = cast(Mapping[str, Any], calibration.get("route_support_modes", {}))
        mode = route_modes.get(route_direction) if route_direction else None
        if isinstance(mode, str) and mode.strip():
            return str(mode).strip().lower()
        fallback = calibration.get("support_mode", "")
        return str(fallback).strip().lower()

    @staticmethod
    def _resolve_route_weight_multiplier(
        row: Mapping[str, Any],
        *,
        route_direction: str = "",
    ) -> float:
        calibration = cast(Mapping[str, Any], row.get("runtime_calibration", {}))
        route_weights = cast(Mapping[str, Any], calibration.get("route_weight_multipliers", {}))
        if route_direction:
            override = route_weights.get(route_direction)
            if isinstance(override, (float, int)):
                return float(np.clip(float(override), 0.05, 4.0))
        fallback = calibration.get("weight_multiplier", 1.0)
        if isinstance(fallback, (float, int)):
            return float(np.clip(float(fallback), 0.05, 4.0))
        return 1.0

    @staticmethod
    def _legacy_fallback_allowed() -> bool:
        return str(
            os.getenv("PHOENIXGUARD_ALLOW_LEGACY_COUNCIL_FALLBACK", "") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}

    def _prediction_budget(self) -> int:
        available = max(1, len(self.loaded_model_names))
        if self.compute_device.type == "cpu":
            return max(2, min(available, max(1, int(self.max_loaded_models))))
        return max(3, min(available, max(1, int(self.max_loaded_models))))

    def _select_prediction_models(
        self,
        routing_context: Mapping[str, Any] | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        available = [name for name in self.loaded_model_names if name in self.model_info]
        if not available:
            return [], {"selected_models": [], "skipped_models": [], "reason": "no_available_models"}

        budget = min(len(available), self._prediction_budget())
        preferred = self.CPU_ALWAYS_ON_MODELS if self.compute_device.type == "cpu" else self.GPU_ALWAYS_ON_MODELS
        route_priority: list[str] = []
        fallback_priority: list[str] = []

        def _add(name: str) -> None:
            if name in available and name not in route_priority:
                route_priority.append(name)

        def _add_fallback(name: str) -> None:
            if name in available and name not in fallback_priority:
                fallback_priority.append(name)

        selection_reason = "fallback_always_on"
        if routing_context:
            chart_state = cast(Mapping[str, Any], routing_context.get("chart_state", {}))
            sequence_state = cast(Mapping[str, Any], routing_context.get("sequence_state", {}))
            grounded_chart = cast(Mapping[str, Any], routing_context.get("grounded_chart", {}))
            memory_summary = cast(Mapping[str, Any], routing_context.get("memory_summary", {}))
            projection_direction = str(chart_state.get("projection_bias_direction", chart_state.get("direction", "BUY"))).upper()
            grounded_confidence = self._safe_clip(chart_state.get("grounded_confidence", grounded_chart.get("grounded_confidence", 0.0)))
            path_clarity = self._safe_clip(chart_state.get("path_clarity", sequence_state.get("path_clarity", 0.0)))
            direction_confidence = self._safe_clip(chart_state.get("direction_probability", 0.5), 0.5)
            structure_trade_ready = float(bool(chart_state.get("structure_trade_ready", False)))
            sequence_model = cast(Mapping[str, Any], chart_state.get("sequence_model", sequence_state.get("sequence_model", {})))
            sequence_uncertainty = self._safe_clip(chart_state.get("sequence_uncertainty", sequence_model.get("uncertainty", 0.0)))
            memory_ambiguity = self._safe_clip(memory_summary.get("ambiguity", 0.0))
            needs_structure = bool(
                grounded_confidence >= 0.45
                or path_clarity <= 0.55
                or sequence_uncertainty >= 0.52
            )
            if projection_direction == "BUY":
                _add("byol")
                if direction_confidence >= 0.62:
                    _add("clip")
                selection_reason = "buy_route"
            elif projection_direction == "SELL":
                _add("simclr")
                _add("swav")
                selection_reason = "sell_route"
            if needs_structure:
                _add("dinov2")
                selection_reason = "structure_route"
            if sequence_uncertainty >= 0.60 or memory_ambiguity >= 0.42 or structure_trade_ready >= 1.0:
                _add("swav")
                if projection_direction != "SELL":
                    _add("simclr")
                selection_reason = "uncertainty_route"

        for name in preferred:
            _add_fallback(name)
        for name in available:
            _add_fallback(name)

        selected: list[str] = []
        for name in route_priority + fallback_priority:
            if name not in selected:
                selected.append(name)
            if len(selected) >= budget:
                break
        skipped = [name for name in available if name not in selected]
        return selected, {
            "selected_models": list(selected),
            "skipped_models": skipped,
            "budget": budget,
            "reason": selection_reason,
        }

    def _predict_single(
        self,
        name: str,
        image: Image.Image,
        adaptation_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        info = self.model_info[name]
        runtime = self._ensure_model_loaded(name)
        ensemble: Any = runtime["ensemble"]
        model: Any = runtime["model"]
        head: Any = runtime["head"]
        aux_head: Any | None = runtime.get("aux_head")
        onnx_session: Any | None = runtime.get("onnx_session")
        onnx_input_name = str(runtime.get("onnx_input_name", ""))
        onnx_output_names = [str(item) for item in cast(Sequence[Any], runtime.get("onnx_output_names", []))]
        exported_onnx_adapter = str(runtime.get("onnx_exported_adapter", "") or "")
        transform = ensemble.eval_transforms.get(name)
        if transform is None:
            cfg = self._train_configs.get(name, self._train_configs["mobilenetv3"])
            transform = ensemble.eval_transforms.get(
                name,
                None,
            )
            if transform is None:
                transform = self._build_basic_transform(cfg.input_size, is_training=False)
        active_adapter_name = ""
        requested_adapter = str((adaptation_profile or {}).get("lora_adapter_name", "")).strip()
        if requested_adapter:
            backbone_module = ensemble._resolve_backbone_module(model)
            if set_active_adapter(backbone_module, requested_adapter):
                active_adapter_name = sanitize_adapter_name(requested_adapter)
            else:
                model_lora = cast(dict[str, Any], info.metrics.get("lora", {}))
                default_adapter = str(model_lora.get("active_adapter", "")).strip()
                if default_adapter:
                    set_active_adapter(backbone_module, default_adapter)
                    active_adapter_name = sanitize_adapter_name(default_adapter)
        else:
            model_lora = cast(dict[str, Any], info.metrics.get("lora", {}))
            default_adapter = str(model_lora.get("active_adapter", "")).strip()
            if default_adapter:
                set_active_adapter(ensemble._resolve_backbone_module(model), default_adapter)
                active_adapter_name = sanitize_adapter_name(default_adapter)

        can_use_onnx = (
            onnx_session is not None
            and bool(onnx_input_name)
            and bool(onnx_output_names)
            and (not active_adapter_name or active_adapter_name == exported_onnx_adapter)
        )

        if not can_use_onnx:
            model = model.to(self.compute_device)
            head = head.to(self.compute_device)
            model.eval()
            head.eval()
            if aux_head is not None:
                moved_aux_head: Any = aux_head.to(self.compute_device)
                moved_aux_head.eval()
                aux_head = moved_aux_head

        try:
            with torch.inference_mode():
                x_cpu = transform(image.convert("RGB")).unsqueeze(0).to(torch.float32)
                if can_use_onnx:
                    session = onnx_session
                    if session is None:
                        raise RuntimeError("ONNX runtime session was unavailable after backend selection.")
                    output_values = cast(
                        list[Any],
                        session.run(onnx_output_names, {onnx_input_name: x_cpu.numpy()}),
                    )
                    output_map = {
                        str(output_name): np.asarray(output_values[idx], dtype=np.float32)
                        for idx, output_name in enumerate(onnx_output_names)
                        if idx < len(output_values)
                    }
                    logits = _tensor_from_numpy(output_map.get("logits", np.zeros((1, 2), dtype=np.float32)))
                    features = _tensor_from_numpy(output_map.get("features", np.zeros((1, max(info.feature_dim, 1)), dtype=np.float32)))
                else:
                    x = x_cpu.to(self.compute_device)
                    features = cast(torch.Tensor, self._forward_features(model, x))
                    if name == "dinov2":
                        features = F.normalize(features, p=2, dim=1)
                    logits = head(features)
                temperature = float(ensemble.temperature_scalers.get(name, info.temperature))
                if temperature <= 0.0:
                    temperature = 1.0
                calibrated_logits = logits / temperature
                probs_tensor = torch.softmax(calibrated_logits, dim=-1)[0].detach().cpu().to(torch.float32)
                buy_prob = float(probs_tensor[0].item()) if probs_tensor.numel() > 0 else 0.5
                sell_prob = float(probs_tensor[1].item()) if probs_tensor.numel() > 1 else float(1.0 - buy_prob)
                max_prob = max(buy_prob, sell_prob)
                margin = abs(buy_prob - sell_prob)
                threshold = float(ensemble.decision_thresholds.get(name, info.decision_threshold))
                entropy = self._normalized_entropy(buy_prob, sell_prob)
                threshold_gap = abs(buy_prob - threshold)
                certainty = float(np.clip(0.52 + 0.28 * max_prob + 0.20 * margin, 0.0, 1.0))
                uncertainty_penalty = float(np.clip(1.10 - 0.35 * entropy, 0.55, 1.10))
                dynamic_weight = float(info.base_weight * certainty * uncertainty_penalty * (0.90 + 0.20 * threshold_gap))
                predicted_label = "BUY" if buy_prob >= threshold else "SELL"
                feature_tensor = features.detach().cpu().to(torch.float32)
                feature_array = _tensor_to_float_array(feature_tensor)
                feature_norm = float(np.linalg.norm(feature_array, axis=1).mean())
                sequence_tasks = self._predict_sequence_tasks(
                    aux_head=aux_head,
                    sequence_task_values=info.sequence_task_values,
                    features=features.detach().to(
                        self.compute_device if (aux_head is not None and not can_use_onnx) else torch.device("cpu")
                    ),
                )
                return {
                    "name": name,
                    "role": info.role,
                    "live_enabled": bool(info.live_enabled),
                    "buy_prob": buy_prob,
                    "sell_prob": sell_prob,
                    "predicted_label": predicted_label,
                    "confidence": max_prob,
                    "margin": margin,
                    "entropy": entropy,
                    "decision_threshold": threshold,
                    "temperature": float(ensemble.temperature_scalers.get(name, info.temperature)),
                    "base_weight": info.base_weight,
                    "dynamic_weight": dynamic_weight if info.live_enabled else 0.0,
                    "shadow_weight": dynamic_weight if (not info.live_enabled) else 0.0,
                    "threshold_gap": threshold_gap,
                    "feature_norm": feature_norm,
                    "metrics": info.metrics,
                    "active_lora_adapter": active_adapter_name,
                    "runtime_backend": "onnx" if can_use_onnx else "pytorch",
                    "sequence_tasks": sequence_tasks,
                    "runtime_calibration": dict(info.runtime_calibration),
                }
        finally:
            if not can_use_onnx:
                model.to(self.storage_device)
                head.to(self.storage_device)
                if aux_head is not None:
                    aux_head.to(self.storage_device)
            if self.compute_device.type == "cuda":
                torch.cuda.empty_cache()

    @staticmethod
    def _apply_adaptation_profile(
        row: dict[str, Any],
        name: str,
        adaptation_profile: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not adaptation_profile:
            return row
        model_weight_biases = cast(dict[str, float], adaptation_profile.get("model_weight_biases", {}))
        direction_bias = cast(dict[str, float], adaptation_profile.get("direction_bias", {}))
        confidence_scale = float(np.clip(adaptation_profile.get("confidence_scale", 1.0), 0.75, 1.25))

        buy_prob = float(np.clip(row.get("buy_prob", 0.5), 0.0, 1.0))
        sell_prob = float(np.clip(row.get("sell_prob", 0.5), 0.0, 1.0))
        buy_prob *= max(0.0, 1.0 + float(direction_bias.get("BUY", 0.0) or 0.0))
        sell_prob *= max(0.0, 1.0 + float(direction_bias.get("SELL", 0.0) or 0.0))
        total = max(buy_prob + sell_prob, 1e-8)
        buy_prob /= total
        sell_prob /= total
        row["buy_prob"] = float(buy_prob)
        row["sell_prob"] = float(sell_prob)
        threshold = float(np.clip(row.get("decision_threshold", 0.5), 0.0, 1.0))
        row["predicted_label"] = "BUY" if buy_prob >= threshold else "SELL"
        row["confidence"] = float(max(buy_prob, sell_prob))
        row["margin"] = float(abs(buy_prob - sell_prob))
        row["entropy"] = float(LocalCVEnsembleRuntime._normalized_entropy(buy_prob, sell_prob))
        row["threshold_gap"] = float(abs(buy_prob - threshold))
        weight_bias = float(model_weight_biases.get(name, 0.0) or 0.0)
        row["dynamic_weight"] = float(max(float(row.get("dynamic_weight", 0.0)) * confidence_scale * (1.0 + weight_bias), 0.0))
        row["adaptation_bias"] = float(weight_bias)
        row["adaptation_confidence_scale"] = float(confidence_scale)
        return row

    @staticmethod
    def _safe_clip(value: Any, default: float = 0.0) -> float:
        try:
            return float(np.clip(float(value), 0.0, 1.0))
        except (TypeError, ValueError):
            return float(np.clip(default, 0.0, 1.0))

    @staticmethod
    def _aggregate_ensemble_view(
        model_outputs: Mapping[str, Mapping[str, Any]],
        *,
        failed_models: Mapping[str, str] | None = None,
        adaptation_profile: Mapping[str, Any] | None = None,
        route_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        live_weights: list[float] = []
        live_buy_probs: list[float] = []
        live_sell_probs: list[float] = []
        vote_counts = {"BUY": 0, "SELL": 0}
        route_direction = str((route_summary or {}).get("route_direction", "")).upper()
        route_strength = float(np.clip((route_summary or {}).get("route_strength", 0.0) or 0.0, 0.0, 1.0))

        for source_row in model_outputs.values():
            row = source_row if isinstance(source_row, dict) else dict(source_row)
            if not bool(row.get("live_enabled", False)):
                continue
            effective_threshold = LocalCVEnsembleRuntime._resolve_effective_decision_threshold(
                row,
                route_direction=route_direction,
            )
            support_mode = LocalCVEnsembleRuntime._resolve_support_mode(
                row,
                route_direction=route_direction,
            )
            route_weight_multiplier = LocalCVEnsembleRuntime._resolve_route_weight_multiplier(
                row,
                route_direction=route_direction,
            )
            effective_dynamic_weight = float(
                max(0.0, float(row.get("dynamic_weight", 0.0) or 0.0)) * route_weight_multiplier
            )
            row["route_weight_multiplier"] = float(route_weight_multiplier)
            row["effective_dynamic_weight"] = float(effective_dynamic_weight)
            live_weights.append(effective_dynamic_weight)
            adjusted_buy_support = LocalCVEnsembleRuntime._threshold_adjusted_buy_support(
                float(np.clip(row.get("buy_prob", 0.5), 0.0, 1.0)),
                effective_threshold,
                support_mode=support_mode,
                predicted_label=str(row.get("predicted_label", "")),
                route_direction=route_direction,
                route_strength=route_strength,
            )
            live_buy_probs.append(adjusted_buy_support)
            live_sell_probs.append(float(1.0 - adjusted_buy_support))
            label = "BUY" if float(np.clip(row.get("buy_prob", 0.5), 0.0, 1.0)) >= effective_threshold else "SELL"
            if label in vote_counts:
                vote_counts[label] += 1

        if live_weights:
            weights = np.asarray(live_weights, dtype=np.float64)
            weights = np.clip(weights, 1e-8, None)
            weights /= max(float(weights.sum()), 1e-12)
            buy_prob = float(np.sum(weights * np.asarray(live_buy_probs, dtype=np.float64)))
            sell_prob = float(np.sum(weights * np.asarray(live_sell_probs, dtype=np.float64)))
        else:
            buy_prob = 0.5
            sell_prob = 0.5
        ensemble_margin = abs(buy_prob - sell_prob)
        ensemble_entropy = LocalCVEnsembleRuntime._normalized_entropy(buy_prob, sell_prob)
        disagreement = float(np.std(np.asarray(live_buy_probs, dtype=np.float64))) if live_buy_probs else 0.0
        predicted_label = "BUY" if buy_prob >= sell_prob else "SELL"
        live_model_count = max(sum(1 for row in model_outputs.values() if bool(row.get("live_enabled", False))), 1)
        consensus_ratio = float(vote_counts[predicted_label] / live_model_count)
        sequence_task_consensus = LocalCVEnsembleRuntime._aggregate_sequence_task_consensus(model_outputs)

        champion_name = ""
        champion_score = -1.0
        confirmer_name = ""
        confirmer_score = -1.0
        for name, row in model_outputs.items():
            if not bool(row.get("live_enabled", False)):
                continue
            confidence_score = float(row.get("effective_dynamic_weight", row.get("dynamic_weight", 0.0))) * float(
                row.get("confidence", 0.0)
            )
            if confidence_score > champion_score:
                confirmer_name, confirmer_score = champion_name, champion_score
                champion_name, champion_score = name, confidence_score
            elif confidence_score > confirmer_score:
                confirmer_name, confirmer_score = name, confidence_score

        ensemble: dict[str, Any] = {
            "buy_prob": buy_prob,
            "sell_prob": sell_prob,
            "predicted_label": predicted_label,
            "confidence": max(buy_prob, sell_prob),
            "margin": ensemble_margin,
            "entropy": ensemble_entropy,
            "disagreement": disagreement,
            "consensus_ratio": consensus_ratio,
            "vote_counts": vote_counts,
            "champion_model": champion_name,
            "confirmer_model": confirmer_name,
            "live_models": [name for name, row in model_outputs.items() if bool(row.get("live_enabled", False))],
            "shadow_models": [name for name, row in model_outputs.items() if not bool(row.get("live_enabled", False))],
            "failed_models": dict(failed_models or {}),
            "adaptation_context_key": str((adaptation_profile or {}).get("context_key", "")),
            "adaptation_confidence_scale": float(np.clip((adaptation_profile or {}).get("confidence_scale", 1.0), 0.75, 1.25)),
            "requested_lora_adapter": str((adaptation_profile or {}).get("lora_adapter_name", "")),
            "sequence_task_consensus": sequence_task_consensus,
        }
        if route_summary:
            ensemble.update(
                {
                    "router_mode": str(route_summary.get("mode", "")),
                    "router_direction": str(route_summary.get("route_direction", "")),
                    "router_strength": float(route_summary.get("route_strength", 0.0) or 0.0),
                    "router_uncertainty": float(route_summary.get("uncertainty", 0.0) or 0.0),
                    "router_regime_confidence": float(route_summary.get("regime_confidence", 0.0) or 0.0),
                    "router_buy_support": float(route_summary.get("buy_support", 0.0) or 0.0),
                    "router_sell_support": float(route_summary.get("sell_support", 0.0) or 0.0),
                }
            )
        return ensemble

    @classmethod
    def _build_route_summary(
        cls,
        model_outputs: Mapping[str, Mapping[str, Any]],
        routing_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_ensemble = cls._aggregate_ensemble_view(model_outputs)
        routing_payload = _mapping(routing_context)
        chart_state = _mapping(routing_payload.get("chart_state", {}))
        sequence_state = _mapping(routing_payload.get("sequence_state", {}))
        grounded_chart = _mapping(routing_payload.get("grounded_chart", {}))
        memory_summary = _mapping(routing_payload.get("memory_summary", {}))
        reasoning_trace = _mapping(routing_payload.get("reasoning_trace", {}))
        market_state = _mapping(reasoning_trace.get("market_state", {}))
        sequence_model = _mapping(chart_state.get("sequence_model", sequence_state.get("sequence_model", {})))
        grounded_structure = _mapping(chart_state.get("grounded_structure", grounded_chart.get("structure_summary", {})))

        base_direction = str(base_ensemble.get("predicted_label", "BUY")).upper()
        base_margin = float(np.clip(base_ensemble.get("margin", 0.0), 0.0, 1.0))
        base_entropy = float(np.clip(base_ensemble.get("entropy", 1.0), 0.0, 1.0))
        disagreement = float(np.clip(float(base_ensemble.get("disagreement", 0.0) or 0.0) / 0.25, 0.0, 1.0))
        macro_trend = str(chart_state.get("macro_trend", market_state.get("macro_trend", ""))).upper()
        projection_direction = str(chart_state.get("projection_bias_direction", base_direction)).upper()
        projection_confidence = cls._safe_clip(chart_state.get("projection_bias_confidence", 0.0))
        direction = str(chart_state.get("direction", base_direction)).upper()
        direction_confidence = cls._safe_clip(chart_state.get("direction_probability", base_ensemble.get("confidence", 0.5)))
        council_projection_direction = str(chart_state.get("council_projection_direction", projection_direction)).upper()
        council_projection_confidence = cls._safe_clip(
            chart_state.get(
                "council_projection_confidence",
                projection_confidence if council_projection_direction == projection_direction else 0.0,
            )
        )
        council_current_box_direction = str(chart_state.get("council_current_box_direction", "HOLD")).upper()
        council_current_box_confidence = cls._safe_clip(chart_state.get("council_current_box_confidence", 0.0))
        council_router_direction = str(chart_state.get("council_router_direction", "HOLD")).upper()
        council_router_strength = cls._safe_clip(chart_state.get("council_router_strength", 0.0))
        path_clarity = cls._safe_clip(chart_state.get("path_clarity", sequence_state.get("path_clarity", 0.0)))
        box_sequence_agreement = cls._safe_clip(chart_state.get("box_sequence_agreement", sequence_state.get("box_sequence_agreement", 0.0)))
        grounded_confidence = cls._safe_clip(chart_state.get("grounded_confidence", grounded_chart.get("grounded_confidence", 0.0)))
        structure_trade_ready = float(bool(chart_state.get("structure_trade_ready", False)))
        local_phase = str(chart_state.get("local_phase", market_state.get("local_phase", ""))).lower()
        momentum_bias = str(chart_state.get("momentum_bias", "neutral")).lower()
        support_strength = cls._safe_clip(grounded_structure.get("support_strength", chart_state.get("support_strength", 0.0)))
        resistance_strength = cls._safe_clip(grounded_structure.get("resistance_strength", chart_state.get("resistance_strength", 0.0)))
        structure_buy_pressure = cls._safe_clip(grounded_structure.get("buy_pressure", chart_state.get("structure_buy_pressure", 0.0)))
        structure_sell_pressure = cls._safe_clip(grounded_structure.get("sell_pressure", chart_state.get("structure_sell_pressure", 0.0)))
        structure_bias_confidence = cls._safe_clip(
            grounded_structure.get("structure_bias_confidence", chart_state.get("structure_bias_confidence", 0.0))
        )
        sequence_buy_pressure = cls._safe_clip(
            chart_state.get("sequence_buy_pressure", sequence_model.get("buy_pressure", 0.0))
        )
        sequence_sell_pressure = cls._safe_clip(
            chart_state.get("sequence_sell_pressure", sequence_model.get("sell_pressure", 0.0))
        )
        sequence_uncertainty = cls._safe_clip(
            chart_state.get("sequence_uncertainty", sequence_model.get("uncertainty", 0.0))
        )
        memory_ambiguity = cls._safe_clip(memory_summary.get("ambiguity", 0.0))
        buy_support = float(
            np.clip(
                0.20 * sequence_buy_pressure
                + 0.18 * structure_buy_pressure
                + 0.16 * support_strength
                + (0.16 * projection_confidence if projection_direction == "BUY" else 0.0)
                + (0.10 * council_projection_confidence if council_projection_direction == "BUY" else 0.0)
                + (0.08 * council_current_box_confidence if council_current_box_direction == "BUY" else 0.0)
                + (0.06 * council_router_strength if council_router_direction == "BUY" else 0.0)
                + (0.12 * direction_confidence if direction == "BUY" else 0.0)
                + (0.10 if macro_trend == "BULL" else 0.0)
                + 0.08 * path_clarity,
                0.0,
                1.0,
            )
        )
        sell_support = float(
            np.clip(
                0.20 * sequence_sell_pressure
                + 0.18 * structure_sell_pressure
                + 0.16 * resistance_strength
                + (0.16 * projection_confidence if projection_direction == "SELL" else 0.0)
                + (0.10 * council_projection_confidence if council_projection_direction == "SELL" else 0.0)
                + (0.08 * council_current_box_confidence if council_current_box_direction == "SELL" else 0.0)
                + (0.06 * council_router_strength if council_router_direction == "SELL" else 0.0)
                + (0.12 * direction_confidence if direction == "SELL" else 0.0)
                + (0.10 if macro_trend == "BEAR" else 0.0)
                + 0.08 * path_clarity,
                0.0,
                1.0,
            )
        )
        macro_direction = "BUY" if macro_trend == "BULL" else ("SELL" if macro_trend == "BEAR" else "HOLD")
        countertrend_reclaim_direction = "HOLD"
        countertrend_reclaim_bonus = 0.0
        if (
            projection_direction in {"BUY", "SELL"}
            and direction in {"BUY", "SELL"}
            and projection_direction != direction
            and projection_direction == macro_direction
            and local_phase in {"counter_trend_pullback", "with_trend_pause"}
            and projection_confidence >= 0.56
        ):
            countertrend_reclaim_direction = projection_direction
            countertrend_reclaim_bonus = float(
                np.clip(
                    0.16 * projection_confidence
                    + 0.10 * path_clarity
                    + 0.08 * box_sequence_agreement
                    + 0.06 * float(
                        (projection_direction == "BUY" and momentum_bias == "bullish")
                        or (projection_direction == "SELL" and momentum_bias == "bearish")
                    )
                    - 0.06 * sequence_uncertainty,
                    0.0,
                    0.24,
                )
            )
            if projection_direction == "BUY":
                buy_support = float(np.clip(buy_support + countertrend_reclaim_bonus, 0.0, 1.0))
                sell_support = float(np.clip(sell_support * max(0.0, 1.0 - 0.40 * countertrend_reclaim_bonus), 0.0, 1.0))
            else:
                sell_support = float(np.clip(sell_support + countertrend_reclaim_bonus, 0.0, 1.0))
                buy_support = float(np.clip(buy_support * max(0.0, 1.0 - 0.40 * countertrend_reclaim_bonus), 0.0, 1.0))
        route_direction = base_direction if base_direction in {"BUY", "SELL"} else "BUY"
        if buy_support > sell_support * 1.03:
            route_direction = "BUY"
        elif sell_support > buy_support * 1.03:
            route_direction = "SELL"
        route_strength = float(np.clip(abs(buy_support - sell_support) + 0.18 * structure_bias_confidence, 0.0, 1.0))
        uncertainty = float(
            np.clip(
                0.34 * base_entropy
                + 0.24 * disagreement
                + 0.18 * (1.0 - base_margin)
                + 0.14 * sequence_uncertainty
                + 0.10 * memory_ambiguity,
                0.0,
                1.0,
            )
        )
        regime_confidence = float(
            np.clip(
                0.26 * path_clarity
                + 0.22 * box_sequence_agreement
                + 0.18 * grounded_confidence
                + 0.18 * structure_bias_confidence
                + 0.08 * structure_trade_ready
                + 0.08 * (1.0 - sequence_uncertainty),
                0.0,
                1.0,
            )
        )
        mode = "generalist_balance"
        if route_direction == "BUY" and (buy_support >= 0.52 or route_strength >= 0.16):
            mode = "buy_specialist"
        elif route_direction == "SELL" and (sell_support >= 0.52 or route_strength >= 0.16):
            mode = "sell_specialist"
        if uncertainty >= 0.58 and route_strength <= 0.20:
            mode = "generalist_balance"
        return {
            "mode": mode,
            "route_direction": route_direction,
            "route_strength": route_strength,
            "uncertainty": uncertainty,
            "regime_confidence": regime_confidence,
            "buy_support": buy_support,
            "sell_support": sell_support,
            "countertrend_reclaim_direction": countertrend_reclaim_direction,
            "countertrend_reclaim_bonus": countertrend_reclaim_bonus,
            "path_clarity": path_clarity,
            "grounded_confidence": grounded_confidence,
            "structure_trade_ready": structure_trade_ready,
            "direction_confidence": direction_confidence,
        }

    @classmethod
    def _apply_confusion_aware_routing(
        cls,
        model_outputs: dict[str, dict[str, Any]],
        routing_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        route_summary = cls._build_route_summary(model_outputs, routing_context=routing_context)
        route_direction = str(route_summary.get("route_direction", "BUY")).upper()
        route_strength = float(route_summary.get("route_strength", 0.0) or 0.0)
        uncertainty = float(route_summary.get("uncertainty", 0.0) or 0.0)
        regime_confidence = float(route_summary.get("regime_confidence", 0.0) or 0.0)
        buy_support = float(route_summary.get("buy_support", 0.0) or 0.0)
        sell_support = float(route_summary.get("sell_support", 0.0) or 0.0)
        grounded_confidence = float(route_summary.get("grounded_confidence", 0.0) or 0.0)
        path_clarity = float(route_summary.get("path_clarity", 0.0) or 0.0)
        structure_trade_ready = float(route_summary.get("structure_trade_ready", 0.0) or 0.0)
        direction_confidence = float(route_summary.get("direction_confidence", 0.0) or 0.0)

        for row in model_outputs.values():
            role = str(row.get("role", "generalist"))
            live_enabled = bool(row.get("live_enabled", False))
            weight_key = "dynamic_weight" if live_enabled else "shadow_weight"
            current_weight = float(max(0.0, float(row.get(weight_key, 0.0) or 0.0)))
            buy_prob = cls._safe_clip(row.get("buy_prob", 0.5), 0.5)
            sell_prob = cls._safe_clip(row.get("sell_prob", 0.5), 0.5)
            route_prob = buy_prob if route_direction == "BUY" else sell_prob
            off_route_prob = sell_prob if route_direction == "BUY" else buy_prob
            metrics = cast(Mapping[str, Any], row.get("metrics", {}))
            recall_value = metrics.get("buy_recall", 50.0) if route_direction == "BUY" else metrics.get("sell_recall", 50.0)
            class_recall = cls._safe_clip(
                float(recall_value) / 100.0 if isinstance(recall_value, (float, int)) else 0.5,
                0.5,
            )

            factor = 1.0
            if role == "generalist":
                factor += 0.30 * uncertainty + 0.18 * regime_confidence + 0.06 * (1.0 - route_strength)
            elif role == "buy_specialist":
                factor += 0.36 * buy_support + 0.16 * float(route_direction == "BUY") + 0.08 * (1.0 - uncertainty)
            elif role == "sell_specialist":
                factor += 0.36 * sell_support + 0.16 * float(route_direction == "SELL") + 0.08 * (1.0 - uncertainty)
            elif role == "structure_specialist":
                factor += 0.26 * grounded_confidence + 0.20 * path_clarity + 0.14 * regime_confidence
            elif role == "execution_specialist":
                factor += 0.20 * (1.0 - uncertainty) + 0.16 * direction_confidence + 0.12 * structure_trade_ready

            factor *= 0.90 + 0.22 * class_recall
            factor *= 0.92 + 0.18 * route_prob
            if route_prob >= off_route_prob:
                factor += 0.10 * route_strength
            else:
                factor *= max(0.72, 1.0 - 0.16 * route_strength)
            if uncertainty >= 0.58 and role in {"generalist", "structure_specialist"}:
                factor += 0.10 * uncertainty
            factor = float(np.clip(factor, 0.35, 2.40))
            row[weight_key] = float(max(current_weight * factor, 0.0))
            row["routing_factor"] = factor
            row["routing_alignment"] = float(route_prob)
            row["routing_target"] = route_direction
            row["routing_uncertainty"] = uncertainty
            row["routing_regime_confidence"] = regime_confidence
        return route_summary

    def reroute_prediction(
        self,
        prediction: Mapping[str, Any],
        *,
        routing_context: Mapping[str, Any] | None = None,
        adaptation_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        model_outputs = {
            str(name): _mapping(row)
            for name, row in _mapping(prediction.get("models", {})).items()
            if isinstance(row, Mapping)
        }
        if not model_outputs:
            return dict(prediction)
        route_summary = self._apply_confusion_aware_routing(model_outputs, routing_context=routing_context)
        ensemble = self._aggregate_ensemble_view(
            model_outputs,
            failed_models=cast(Mapping[str, str], _mapping(prediction.get("ensemble", {})).get("failed_models", self.failed_models)),
            adaptation_profile=adaptation_profile,
            route_summary=route_summary,
        )
        return {
            "models": model_outputs,
            "ensemble": ensemble,
            "selection": dict(cast(Mapping[str, Any], prediction.get("selection", {}))),
        }

    def predict(
        self,
        image: Image.Image,
        adaptation_profile: Mapping[str, Any] | None = None,
        routing_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        model_outputs: dict[str, dict[str, Any]] = {}
        selected_model_names, selection_meta = self._select_prediction_models(routing_context)
        model_names = selected_model_names or list(self.loaded_model_names)

        with self._predict_lock:
            for name in model_names:
                try:
                    row = self._apply_adaptation_profile(
                        self._predict_single(name, image, adaptation_profile=adaptation_profile),
                        name,
                        adaptation_profile,
                    )
                    model_outputs[name] = row
                except LegacyFallbackApprovalRequired:
                    raise
                except Exception as exc:
                    self.failed_models[name] = str(exc)
                    self.logger.warning("Local ensemble predict failed for %s: %s", name, exc)
        route_summary = None
        if routing_context:
            route_summary = self._apply_confusion_aware_routing(model_outputs, routing_context=routing_context)

        return {
            "models": model_outputs,
            "ensemble": self._aggregate_ensemble_view(
                model_outputs,
                failed_models=self.failed_models,
                adaptation_profile=adaptation_profile,
                route_summary=route_summary,
            ),
            "selection": selection_meta,
        }

    def predict_ensemble(self, image: Image.Image) -> dict[str, Any]:
        """
        Compatibility bridge for callers that still expect the old
        ``EnsembleCVModels.predict_ensemble(...)`` interface.
        """
        return self.predict(image)
