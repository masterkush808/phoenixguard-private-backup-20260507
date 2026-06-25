from __future__ import annotations

import json
import importlib
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, cast

import torch
from torch import Tensor


class _SafeTensorReader(Protocol):
    def __enter__(self) -> "_SafeTensorReader": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...
    def keys(self) -> Sequence[str]: ...
    def get_tensor(self, key: str) -> Tensor: ...


class _SafeOpenFn(Protocol):
    def __call__(self, filename: str, *, framework: str, device: str) -> _SafeTensorReader: ...


class _SaveFileFn(Protocol):
    def __call__(self, tensors: Mapping[str, Tensor], filename: str, *, metadata: Mapping[str, str] | None = None) -> None: ...


def _load_safetensors_functions() -> tuple[_SafeOpenFn | None, _SaveFileFn | None]:
    try:
        safetensors_module = importlib.import_module("safetensors")
        safetensors_torch_module = importlib.import_module("safetensors.torch")
    except Exception:  # pragma: no cover - optional dependency guard
        return None, None
    return (
        cast(_SafeOpenFn, getattr(safetensors_module, "safe_open", None)),
        cast(_SaveFileFn, getattr(safetensors_torch_module, "save_file", None)),
    )


_safe_open_safetensors, _save_safetensors_file = _load_safetensors_functions()


INFERENCE_EXPORTS_DIRNAME = "inference_exports"
METADATA_FILENAME = "metadata.json"
BACKBONE_FILENAME = "backbone.safetensors"
HEAD_FILENAME = "head.safetensors"
AUX_HEAD_FILENAME = "aux_head.safetensors"
ONNX_FILENAME = "model.onnx"


def supports_safetensors() -> bool:
    return _safe_open_safetensors is not None and _save_safetensors_file is not None


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


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
    return _mapping(raw)


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
    save_file = _save_safetensors_file
    if not supports_safetensors() or save_file is None:
        raise RuntimeError("safetensors is not installed in this environment.")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tensors = _tensor_state_dict(state_dict)
    if not tensors:
        raise ValueError(f"No tensor values were found for safetensors export to {destination}.")
    save_file(tensors, str(destination), metadata=dict(metadata or {}))
    return destination


def load_state_dict_safetensors(path: str | Path, *, device: str | torch.device = "cpu") -> dict[str, Tensor]:
    safe_open = _safe_open_safetensors
    if not supports_safetensors() or safe_open is None:
        raise RuntimeError("safetensors is not installed in this environment.")
    requested_device = str(torch.device(device))
    state_dict: dict[str, Tensor] = {}
    with safe_open(str(Path(path)), framework="pt", device=requested_device) as handle:
        for key in handle.keys():
            state_dict[str(key)] = handle.get_tensor(key)
    return state_dict
