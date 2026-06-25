from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, cast


class _SafeTensorHandle(Protocol):
    def __enter__(self) -> _SafeTensorHandle: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object: ...

    def metadata(self) -> Mapping[str, object] | None: ...

    def keys(self) -> Sequence[str]: ...

try:
    from safetensors import safe_open as _raw_safe_open_safetensors

    _safe_open_safetensors: Callable[..., _SafeTensorHandle] | None = cast(
        Callable[..., _SafeTensorHandle],
        _raw_safe_open_safetensors,
    )
except Exception:  # pragma: no cover - optional dependency guard
    _safe_open_safetensors = None


MANIFEST_FILENAME = "manifest.json"
VOICE_COMPONENTS: tuple[str, ...] = (
    "wake_word",
    "speech_to_text",
    "brain",
    "speech",
)
HEAVY_VOICE_COMPONENTS: frozenset[str] = frozenset(
    {
        "speech_to_text",
        "brain",
        "speech",
    }
)
ALLOWED_STORAGE_FORMATS: frozenset[str] = frozenset(
    {
        "safetensors",
        "onnx",
        "tflite",
        "gguf",
    }
)
ALLOWED_LOAD_POLICIES: frozenset[str] = frozenset(
    {
        "gpu_only",
        "gpu_preferred",
        "balanced",
    }
)
REMOTE_MANIFEST_KEYS: frozenset[str] = frozenset(
    {
        "repo_id",
        "model_id",
        "revision",
        "download_url",
        "source_url",
        "hf_repo",
        "hf_revision",
    }
)


class SupportsVoiceConfig(Protocol):
    bundle_root: Path
    wake_word_bundle_name: str
    speech_to_text_bundle_name: str
    brain_bundle_name: str
    speech_bundle_name: str
    require_safetensors_for_heavy_models: bool
    forbid_cpu_offload_for_heavy_models: bool
    require_local_files_only: bool
    preferred_device: str
    max_cpu_memory_mb: int


class VoiceBundleValidationError(RuntimeError):
    pass


@dataclass(slots=True)
class LocalVoiceModelBundle:
    component: str
    name: str
    bundle_dir: Path
    manifest_path: Path
    runtime: str
    storage_format: str
    load_policy: str
    device: str
    dtype: str
    memory_map: bool
    cpu_offload: bool
    max_cpu_memory_mb: int
    weight_files: tuple[Path, ...] = ()
    config_files: tuple[Path, ...] = ()
    tokenizer_files: tuple[Path, ...] = ()
    metadata: dict[str, Any] = field(default_factory=lambda: {})
    safetensors_metadata: dict[str, dict[str, str]] = field(default_factory=lambda: {})
    total_weight_bytes: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "name": self.name,
            "runtime": self.runtime,
            "storage_format": self.storage_format,
            "load_policy": self.load_policy,
            "device": self.device,
            "dtype": self.dtype,
            "memory_map": self.memory_map,
            "cpu_offload": self.cpu_offload,
            "max_cpu_memory_mb": self.max_cpu_memory_mb,
            "weight_files": [str(path) for path in self.weight_files],
            "config_files": [str(path) for path in self.config_files],
            "tokenizer_files": [str(path) for path in self.tokenizer_files],
            "total_weight_bytes": self.total_weight_bytes,
            "metadata": dict(self.metadata),
            "safetensors_metadata": {key: dict(value) for key, value in self.safetensors_metadata.items()},
        }


@dataclass(slots=True)
class LocalVoiceStack:
    wake_word: LocalVoiceModelBundle
    speech_to_text: LocalVoiceModelBundle
    brain: LocalVoiceModelBundle
    speech: LocalVoiceModelBundle

    @property
    def total_weight_bytes(self) -> int:
        return sum(bundle.total_weight_bytes for bundle in self.bundles())

    def bundles(self) -> tuple[LocalVoiceModelBundle, ...]:
        return (
            self.wake_word,
            self.speech_to_text,
            self.brain,
            self.speech,
        )

    def by_component(self) -> dict[str, LocalVoiceModelBundle]:
        return {
            bundle.component: bundle
            for bundle in self.bundles()
        }

    def summary(self) -> dict[str, Any]:
        return {
            "components": {
                bundle.component: bundle.summary()
                for bundle in self.bundles()
            },
            "total_weight_bytes": self.total_weight_bytes,
        }


def _safe_json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _safe_string(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or str(default)


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(default)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _validate_component(component: str) -> str:
    normalized = _safe_string(component).lower()
    if normalized not in VOICE_COMPONENTS:
        raise VoiceBundleValidationError(
            f"Unsupported voice component '{component}'. Expected one of: {', '.join(VOICE_COMPONENTS)}."
        )
    return normalized


def _manifest_path(bundle_root: Path, component: str, bundle_name: str) -> Path:
    safe_component = _validate_component(component)
    safe_bundle_name = _safe_string(bundle_name)
    if not safe_bundle_name:
        raise VoiceBundleValidationError(f"Bundle name is required for component '{safe_component}'.")
    return bundle_root / safe_component / safe_bundle_name / MANIFEST_FILENAME


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise VoiceBundleValidationError(f"Voice bundle manifest is missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise VoiceBundleValidationError(f"Voice bundle manifest is not valid JSON: {path}") from exc
    manifest = _safe_json_object(raw)
    if not manifest:
        raise VoiceBundleValidationError(f"Voice bundle manifest must contain a JSON object: {path}")
    return manifest


def _ensure_local_relative_path(bundle_dir: Path, relative_path: str, *, label: str) -> Path:
    candidate_text = _safe_string(relative_path)
    if not candidate_text:
        raise VoiceBundleValidationError(f"Voice bundle {label} path cannot be empty in {bundle_dir}.")
    if "://" in candidate_text or candidate_text.startswith("hf://"):
        raise VoiceBundleValidationError(
            f"Remote path '{candidate_text}' is not allowed for local voice bundle {bundle_dir}."
        )
    candidate = (bundle_dir / candidate_text).resolve()
    bundle_root = bundle_dir.resolve()
    if not candidate.is_relative_to(bundle_root):
        raise VoiceBundleValidationError(
            f"Voice bundle {label} path escapes the bundle directory: {candidate_text}"
        )
    if not candidate.exists():
        raise VoiceBundleValidationError(
            f"Voice bundle {label} file is missing: {candidate}"
        )
    return candidate


def _resolve_relative_files(bundle_dir: Path, value: Any, *, label: str) -> tuple[Path, ...]:
    if not isinstance(value, list):
        raise VoiceBundleValidationError(
            f"Voice bundle field '{label}' must be a JSON array in {bundle_dir / MANIFEST_FILENAME}."
        )
    files: list[Path] = []
    for item in cast(list[Any], value):
        files.append(_ensure_local_relative_path(bundle_dir, _safe_string(item), label=label))
    if not files:
        raise VoiceBundleValidationError(
            f"Voice bundle field '{label}' must list at least one local artifact in {bundle_dir / MANIFEST_FILENAME}."
        )
    return tuple(files)


def _read_safetensors_metadata(path: Path) -> dict[str, str]:
    if _safe_open_safetensors is None:
        return {}
    try:
        with _safe_open_safetensors(str(path), framework="pt", device="cpu") as handle:
            raw_metadata = handle.metadata() or {}
            metadata: dict[str, str] = {
                str(key): str(value)
                for key, value in raw_metadata.items()
            }
            metadata["_tensor_count"] = str(len(list(handle.keys())))
            return metadata
    except Exception as exc:
        raise VoiceBundleValidationError(f"Invalid safetensors artifact: {path}") from exc


def resolve_local_voice_bundle(
    bundle_root: str | Path,
    component: str,
    bundle_name: str,
    *,
    require_safetensors_for_heavy_models: bool = True,
    forbid_cpu_offload_for_heavy_models: bool = True,
    require_local_files_only: bool = True,
    preferred_device: str = "cuda",
    max_cpu_memory_mb: int = 1024,
) -> LocalVoiceModelBundle:
    resolved_component = _validate_component(component)
    manifest_path = _manifest_path(Path(bundle_root), resolved_component, bundle_name)
    manifest = _read_manifest(manifest_path)
    bundle_dir = manifest_path.parent

    if require_local_files_only:
        for key in REMOTE_MANIFEST_KEYS:
            if _safe_string(manifest.get(key)):
                raise VoiceBundleValidationError(
                    f"Voice bundle {resolved_component}/{bundle_name} references remote field '{key}', which is disabled."
                )

    runtime = _safe_string(manifest.get("runtime"), "custom")
    storage_format = _safe_string(manifest.get("storage_format"), "safetensors").lower()
    if storage_format not in ALLOWED_STORAGE_FORMATS:
        raise VoiceBundleValidationError(
            f"Voice bundle {resolved_component}/{bundle_name} uses unsupported storage format '{storage_format}'."
        )

    load_policy = _safe_string(manifest.get("load_policy"), "gpu_only").lower()
    if load_policy not in ALLOWED_LOAD_POLICIES:
        raise VoiceBundleValidationError(
            f"Voice bundle {resolved_component}/{bundle_name} uses unsupported load policy '{load_policy}'."
        )

    weights = _resolve_relative_files(bundle_dir, manifest.get("weights", []), label="weights")
    config_files = tuple()
    tokenizer_files = tuple()
    if "config_files" in manifest:
        config_value = manifest.get("config_files")
        if not isinstance(config_value, list):
            raise VoiceBundleValidationError(
                f"Voice bundle field 'config_files' must be a JSON array in {manifest_path}."
            )
        config_files = tuple(
            _ensure_local_relative_path(bundle_dir, _safe_string(item), label="config_files")
            for item in cast(list[Any], config_value)
        )
    if "tokenizer_files" in manifest:
        tokenizer_value = manifest.get("tokenizer_files")
        if not isinstance(tokenizer_value, list):
            raise VoiceBundleValidationError(
                f"Voice bundle field 'tokenizer_files' must be a JSON array in {manifest_path}."
            )
        tokenizer_files = tuple(
            _ensure_local_relative_path(bundle_dir, _safe_string(item), label="tokenizer_files")
            for item in cast(list[Any], tokenizer_value)
        )

    memory_map = _safe_bool(manifest.get("memory_map"), storage_format == "safetensors")
    cpu_offload = _safe_bool(manifest.get("cpu_offload"), False)
    dtype = _safe_string(manifest.get("dtype"), "float16").lower()
    device = _safe_string(manifest.get("device"), preferred_device).lower()
    if device not in {"cuda", "cpu", "auto"}:
        device = preferred_device if preferred_device in {"cuda", "cpu", "auto"} else "cuda"
    resolved_max_cpu_memory_mb = max(0, _safe_int(manifest.get("max_cpu_memory_mb"), max_cpu_memory_mb))

    if resolved_component in HEAVY_VOICE_COMPONENTS and require_safetensors_for_heavy_models and storage_format != "safetensors":
        raise VoiceBundleValidationError(
            f"Heavy voice bundle {resolved_component}/{bundle_name} must use safetensors, not '{storage_format}'."
        )
    if resolved_component in HEAVY_VOICE_COMPONENTS and not memory_map:
        raise VoiceBundleValidationError(
            f"Heavy voice bundle {resolved_component}/{bundle_name} must enable memory_map."
        )
    if resolved_component in HEAVY_VOICE_COMPONENTS and forbid_cpu_offload_for_heavy_models and cpu_offload:
        raise VoiceBundleValidationError(
            f"Heavy voice bundle {resolved_component}/{bundle_name} cannot enable cpu_offload in the current policy."
        )

    safetensors_metadata: dict[str, dict[str, str]] = {}
    total_weight_bytes = 0
    for weight_path in weights:
        total_weight_bytes += int(weight_path.stat().st_size)
        if storage_format == "safetensors":
            if weight_path.suffix.lower() != ".safetensors":
                raise VoiceBundleValidationError(
                    f"Voice bundle {resolved_component}/{bundle_name} contains non-safetensors weight file: {weight_path.name}"
                )
            safetensors_metadata[weight_path.name] = _read_safetensors_metadata(weight_path)

    metadata = _safe_json_object(manifest.get("metadata"))
    metadata.setdefault("manifest_name", _safe_string(manifest.get("name"), bundle_name))

    return LocalVoiceModelBundle(
        component=resolved_component,
        name=_safe_string(bundle_name),
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        runtime=runtime,
        storage_format=storage_format,
        load_policy=load_policy,
        device=device,
        dtype=dtype,
        memory_map=memory_map,
        cpu_offload=cpu_offload,
        max_cpu_memory_mb=resolved_max_cpu_memory_mb,
        weight_files=weights,
        config_files=config_files,
        tokenizer_files=tokenizer_files,
        metadata=metadata,
        safetensors_metadata=safetensors_metadata,
        total_weight_bytes=total_weight_bytes,
    )


def resolve_local_voice_stack(config: SupportsVoiceConfig) -> LocalVoiceStack:
    return LocalVoiceStack(
        wake_word=resolve_local_voice_bundle(
            config.bundle_root,
            "wake_word",
            config.wake_word_bundle_name,
            require_safetensors_for_heavy_models=config.require_safetensors_for_heavy_models,
            forbid_cpu_offload_for_heavy_models=config.forbid_cpu_offload_for_heavy_models,
            require_local_files_only=config.require_local_files_only,
            preferred_device=config.preferred_device,
            max_cpu_memory_mb=config.max_cpu_memory_mb,
        ),
        speech_to_text=resolve_local_voice_bundle(
            config.bundle_root,
            "speech_to_text",
            config.speech_to_text_bundle_name,
            require_safetensors_for_heavy_models=config.require_safetensors_for_heavy_models,
            forbid_cpu_offload_for_heavy_models=config.forbid_cpu_offload_for_heavy_models,
            require_local_files_only=config.require_local_files_only,
            preferred_device=config.preferred_device,
            max_cpu_memory_mb=config.max_cpu_memory_mb,
        ),
        brain=resolve_local_voice_bundle(
            config.bundle_root,
            "brain",
            config.brain_bundle_name,
            require_safetensors_for_heavy_models=config.require_safetensors_for_heavy_models,
            forbid_cpu_offload_for_heavy_models=config.forbid_cpu_offload_for_heavy_models,
            require_local_files_only=config.require_local_files_only,
            preferred_device=config.preferred_device,
            max_cpu_memory_mb=config.max_cpu_memory_mb,
        ),
        speech=resolve_local_voice_bundle(
            config.bundle_root,
            "speech",
            config.speech_bundle_name,
            require_safetensors_for_heavy_models=config.require_safetensors_for_heavy_models,
            forbid_cpu_offload_for_heavy_models=config.forbid_cpu_offload_for_heavy_models,
            require_local_files_only=config.require_local_files_only,
            preferred_device=config.preferred_device,
            max_cpu_memory_mb=config.max_cpu_memory_mb,
        ),
    )
