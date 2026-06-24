from __future__ import annotations

import argparse
from pathlib import Path
from collections.abc import Callable
from typing import Any, Sequence, cast

import torch
from torch import nn

from phoenixguard.core.config import MEMORY_BANK as MEMORY_BANK_CFG, RUNTIME
from phoenixguard.runtime.inference_exports import (
    export_aux_head_path,
    export_backbone_path,
    export_head_path,
    export_onnx_path,
    read_export_metadata,
    save_state_dict_safetensors,
    supports_safetensors,
    write_export_metadata,
)


MODEL_SAVE_NAMES: dict[str, str] = {
    "dinov2": "dinov2_finetuned.pkl",
    "mobilenetv3": "mobilenetv3_finetuned.pkl",
    "simclr": "simclr_finetuned.pkl",
    "byol": "byol_finetuned.pkl",
    "swav": "swav_finetuned.pkl",
    "clip": "clip_finetuned.pkl",
}

MODEL_INPUT_SIZES: dict[str, int] = {
    "dinov2": 392,
    "mobilenetv3": 224,
    "simclr": 224,
    "byol": 224,
    "swav": 224,
    "clip": 224,
}


class _InferenceExportModule(nn.Module):
    def __init__(self, backbone: nn.Module, head: nn.Module, forward_features_fn: Any) -> None:
        super().__init__()
        self.backbone = backbone
        self.head = head
        self._forward_features_fn = forward_features_fn

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        features = self._forward_features_fn(self.backbone, x)
        logits = self.head(features)
        return logits, features


def _memory_image_dirs() -> list[str]:
    return [
        str(RUNTIME.project_root / MEMORY_BANK_CFG.buys_dir),
        str(RUNTIME.project_root / MEMORY_BANK_CFG.sells_dir),
    ]


def _load_bundle(path: Path) -> dict[str, Any]:
    try:
        payload = torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected bundle payload type for {path}: {type(payload)!r}")
    return cast(dict[str, Any], payload)


def _training_symbols() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    from phoenixguard.runtime.continual_adapters import (
        AdapterConfig,
        apply_lora_adapters,
        sanitize_adapter_name,
        set_active_adapter,
    )
    from phoenixguard.training.ensemble_cv_models import EnsembleCVModels, TRAIN_CONFIGS, forward_features

    return (
        EnsembleCVModels,
        TRAIN_CONFIGS,
        forward_features,
        AdapterConfig,
        apply_lora_adapters,
        sanitize_adapter_name,
        set_active_adapter,
    )


def _apply_lora_from_payload(ensemble: Any, model: nn.Module, payload: dict[str, Any]) -> str:
    (
        _ensemble_cls,
        _train_configs,
        _forward_features,
        AdapterConfig,
        apply_lora_adapters,
        sanitize_adapter_name,
        set_active_adapter,
    ) = _training_symbols()
    del _ensemble_cls, _train_configs, _forward_features

    lora_payload = cast(dict[str, Any], payload.get("lora", {}))
    if not bool(lora_payload.get("enabled", False)):
        return ""
    backbone_module = ensemble._resolve_backbone_module(model)
    target_paths = cast(list[str], lora_payload.get("target_paths", []))
    adapter_specs = cast(dict[str, dict[str, Any]], lora_payload.get("adapter_specs", {}))
    active_adapter = sanitize_adapter_name(str(lora_payload.get("active_adapter", "continual_default")))
    primary_spec = adapter_specs.get(active_adapter, {})
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


def _build_ensemble_for_model(model_name: str) -> tuple[Any, nn.Module, nn.Module, Any]:
    EnsembleCVModels, _train_configs, forward_features_fn, _AdapterConfig, _apply_lora_adapters, _sanitize_adapter_name, _set_active_adapter = _training_symbols()
    del _train_configs, _AdapterConfig, _apply_lora_adapters, _sanitize_adapter_name, _set_active_adapter
    ensemble = EnsembleCVModels(
        image_dirs=_memory_image_dirs(),
        device=torch.device("cpu"),
        target_models=[model_name],
        pretrained_backbones=False,
    )
    ensemble._init_models()
    model = ensemble.models.get(model_name)
    if model is None:
        raise RuntimeError(f"Model init failed for {model_name}.")
    head = ensemble._ensure_head(model_name)
    return ensemble, model, head, forward_features_fn


def _save_onnx_export(
    *,
    model_name: str,
    model: nn.Module,
    head: nn.Module,
    forward_features_fn: Any,
    destination: Path,
) -> Path:
    input_size = int(MODEL_INPUT_SIZES[model_name])
    bundle = _InferenceExportModule(model, head, forward_features_fn).eval()
    example = torch.randn(1, 3, input_size, input_size, dtype=torch.float32)
    example_args: tuple[torch.Tensor, ...] = (example,)
    destination.parent.mkdir(parents=True, exist_ok=True)
    onnx_export = cast(Callable[..., object], getattr(torch.onnx, "export"))
    onnx_export(
        bundle,
        example_args,
        str(destination),
        input_names=["input"],
        output_names=["logits", "features"],
        dynamic_axes={
            "input": {0: "batch"},
            "logits": {0: "batch"},
            "features": {0: "batch"},
        },
        opset_version=17,
        do_constant_folding=True,
    )
    return destination


def export_model(model_name: str, *, export_onnx: bool = False) -> dict[str, Any]:
    if not supports_safetensors():
        raise RuntimeError("safetensors is required for inference bundle export.")

    save_name = MODEL_SAVE_NAMES.get(model_name)
    if not save_name:
        raise RuntimeError(f"Unknown model name: {model_name}")
    bundle_path = Path(RUNTIME.models_dir) / save_name
    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")

    payload = _load_bundle(bundle_path)
    existing_export_metadata = read_export_metadata(RUNTIME.models_dir, model_name)
    active_adapter = str(cast(dict[str, Any], payload.get("lora", {})).get("active_adapter", "") or "")
    feature_dim = int(payload.get("feature_dim", 0) or 0)
    task_values = cast(dict[str, list[str]], payload.get("sequence_task_values", {}))

    backbone_path = save_state_dict_safetensors(
        export_backbone_path(RUNTIME.models_dir, model_name),
        cast(dict[str, Any], payload["backbone_state_dict"]),
        metadata={"model_name": model_name, "kind": "backbone"},
    )
    head_path = save_state_dict_safetensors(
        export_head_path(RUNTIME.models_dir, model_name),
        cast(dict[str, Any], payload["head_state_dict"]),
        metadata={"model_name": model_name, "kind": "head"},
    )
    aux_path = ""
    if task_values and isinstance(payload.get("aux_head_state_dict"), dict):
        aux_path = str(
            save_state_dict_safetensors(
                export_aux_head_path(RUNTIME.models_dir, model_name),
                cast(dict[str, Any], payload["aux_head_state_dict"]),
                metadata={"model_name": model_name, "kind": "aux_head"},
            )
        )

    onnx_path = ""
    if export_onnx:
        ensemble, model, head, forward_features_fn = _build_ensemble_for_model(model_name)
        active_adapter = _apply_lora_from_payload(ensemble, model, payload)
        model.load_state_dict(cast(dict[str, Any], payload["backbone_state_dict"]))
        head.load_state_dict(cast(dict[str, Any], payload["head_state_dict"]))
        model.eval()
        head.eval()
        onnx_path = str(
            _save_onnx_export(
                model_name=model_name,
                model=model,
                head=head,
                forward_features_fn=forward_features_fn,
                destination=export_onnx_path(RUNTIME.models_dir, model_name),
            )
        )

    metadata: dict[str, Any] = {
        "format_version": 1,
        "model_name": model_name,
        "source_bundle": str(bundle_path.name),
        "input_size": int(MODEL_INPUT_SIZES[model_name]),
        "feature_dim": feature_dim,
        "temperature": float(payload.get("temperature", 1.0) or 1.0),
        "decision_threshold": float(payload.get("decision_threshold", 0.5) or 0.5),
        "runtime_calibration": cast(
            dict[str, Any],
            payload.get(
                "runtime_calibration",
                existing_export_metadata.get("runtime_calibration", {}),
            ),
        ),
        "best_val_accuracy": float(payload.get("best_val_accuracy", 0.0) or 0.0),
        "evaluation_metrics": cast(dict[str, Any], payload.get("evaluation_metrics", {})),
        "sequence_task_values": task_values,
        "sequence_aux_metrics": cast(dict[str, Any], payload.get("sequence_aux_metrics", {})),
        "lora": cast(dict[str, Any], payload.get("lora", {})),
        "exported_active_adapter": active_adapter,
        "paths": {
            "backbone": str(backbone_path.name),
            "head": str(head_path.name),
            "aux_head": Path(aux_path).name if aux_path else "",
            "onnx": Path(onnx_path).name if onnx_path else "",
        },
    }
    metadata_path = write_export_metadata(RUNTIME.models_dir, model_name, metadata)
    return {
        "model_name": model_name,
        "metadata_path": str(metadata_path),
        "backbone_path": str(backbone_path),
        "head_path": str(head_path),
        "aux_head_path": aux_path,
        "onnx_path": onnx_path,
    }


def _parse_model_list(raw: str | None, *, default: Sequence[str]) -> list[str]:
    if not raw:
        return [str(name) for name in default]
    out: list[str] = []
    seen: set[str] = set()
    for part in str(raw).split(","):
        name = part.strip()
        if not name or name in seen:
            continue
        out.append(name)
        seen.add(name)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Export lean inference bundles from PhoenixGuard training checkpoints.")
    parser.add_argument(
        "--models",
        default=",".join(MODEL_SAVE_NAMES.keys()),
        help="Comma-separated model names to export as safetensors inference bundles.",
    )
    parser.add_argument(
        "--onnx-models",
        default="",
        help="Comma-separated subset of models to also export to ONNX.",
    )
    args = parser.parse_args()

    models = _parse_model_list(args.models, default=tuple(MODEL_SAVE_NAMES.keys()))
    onnx_models = set(_parse_model_list(args.onnx_models, default=()))

    for model_name in models:
        result = export_model(model_name, export_onnx=model_name in onnx_models)
        print(
            f"[EXPORT] {model_name}: metadata={result['metadata_path']} "
            f"safetensors=OK onnx={'YES' if result['onnx_path'] else 'NO'}"
        )


if __name__ == "__main__":
    main()
