from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import torch
from torch import Tensor

try:
    from safetensors.torch import load_file as _load_safetensors_file
    from safetensors.torch import save_file as _save_safetensors_file
except Exception:  # pragma: no cover - optional dependency guard
    _load_safetensors_file = None
    _save_safetensors_file = None


INFERENCE_EXPORTS_DIRNAME = "inference_exports"
METADATA_FILENAME = "metadata.json"
BACKBONE_FILENAME = "backbone.safetensors"
HEAD_FILENAME = "head.safetensors"
AUX_HEAD_FILENAME = "aux_head.safetensors"
ONNX_FILENAME = "model.onnx"


def supports_safetensors() -> bool:
    return _load_safetensors_file is not None and _save_safetensors_file is not None


def inference_export_dir(model_dir: str | Path, model_name: str) -> Path:
    return Path(model_dir) / INFERENCE_EXPORTS_DIRNAME / str(model_name).strip()


def export_metadata_path(model_dir: str | Path, model_name: str) -> Path:
    return inference_export_dir(model_dir, model_name) / METADATA_FILENAME


def export_backbone_path(model_dir: str | Path, model_name: str) -> Path:
    return inference_export_dir(model_dir, model_name) / BACKBONE_FILENAME


def export_head_path(model_dir: str | Path, model_name: str) -> Path:
    return inference_export_dir(model_dir, model_name) / HEAD_FILENAME


def export_aux_head_path(model_dir: str | Path, model_name: str) -> Path:
    return inference_export_dir(model_dir, model_name) / AUX_HEAD_FILENAME


def export_onnx_path(model_dir: str | Path, model_name: str) -> Path:
    return inference_export_dir(model_dir, model_name) / ONNX_FILENAME


def write_export_metadata(model_dir: str | Path, model_name: str, payload: Mapping[str, Any]) -> Path:
    export_dir = inference_export_dir(model_dir, model_name)
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / METADATA_FILENAME
    path.write_text(json.dumps(dict(payload), indent=2), encoding="utf-8")
    return path


def read_export_metadata(model_dir: str | Path, model_name: str) -> dict[str, Any]:
    path = export_metadata_path(model_dir, model_name)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _tensor_state_dict(state_dict: Mapping[str, Any]) -> dict[str, Tensor]:
    tensors: dict[str, Tensor] = {}
    for key, value in state_dict.items():
        if isinstance(value, Tensor):
            tensor = value.detach().cpu()
            if not tensor.is_contiguous():
                tensor = tensor.contiguous()
            tensors[str(key)] = tensor
    return tensors


def save_state_dict_safetensors(path: str | Path, state_dict: Mapping[str, Any], *, metadata: Mapping[str, str] | None = None) -> Path:
    if not supports_safetensors():
        raise RuntimeError("safetensors is not installed in this environment.")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tensors = _tensor_state_dict(state_dict)
    if not tensors:
        raise ValueError(f"No tensor values were found for safetensors export to {destination}.")
    _save_safetensors_file(tensors, str(destination), metadata=dict(metadata or {}))
    return destination


def load_state_dict_safetensors(path: str | Path, *, device: str | torch.device = "cpu") -> dict[str, Tensor]:
    if not supports_safetensors():
        raise RuntimeError("safetensors is not installed in this environment.")
    loaded = _load_safetensors_file(str(Path(path)), device=str(torch.device(device)))
    return {str(key): value for key, value in loaded.items()}
