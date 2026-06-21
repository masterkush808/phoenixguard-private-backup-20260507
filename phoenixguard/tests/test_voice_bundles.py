from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from phoenixguard.core.config import VoiceConfig
from phoenixguard.voice.bundles import (
    VoiceBundleValidationError,
    resolve_local_voice_bundle,
    resolve_local_voice_stack,
)

try:
    from safetensors.torch import save_file as save_safetensors_file
except Exception:  # pragma: no cover - optional dependency guard
    save_safetensors_file = None


def _write_safetensors(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if save_safetensors_file is None:
        path.write_bytes(b"placeholder")
        return
    save_safetensors_file({"weight": torch.ones(1, dtype=torch.float32)}, str(path))


def _write_manifest(bundle_dir: Path, payload: dict[str, object]) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_resolve_local_voice_stack_reads_local_bundle_manifests(tmp_path: Path) -> None:
    wake_dir = tmp_path / "models" / "voice" / "wake_word" / "openwakeword-local"
    stt_dir = tmp_path / "models" / "voice" / "speech_to_text" / "whisper-large-v3-local"
    brain_dir = tmp_path / "models" / "voice" / "brain" / "phoenixguard-voice-brain-local"
    speech_dir = tmp_path / "models" / "voice" / "speech" / "openvoice-v2-local"

    wake_dir.mkdir(parents=True, exist_ok=True)
    (wake_dir / "wake.onnx").write_bytes(b"wake-model")
    _write_safetensors(stt_dir / "encoder.safetensors")
    _write_safetensors(brain_dir / "model.safetensors")
    _write_safetensors(speech_dir / "tts.safetensors")
    for config_dir in (stt_dir, brain_dir, speech_dir):
        (config_dir / "config.json").write_text("{}", encoding="utf-8")

    _write_manifest(
        wake_dir,
        {
            "name": "openwakeword-local",
            "runtime": "openwakeword",
            "storage_format": "onnx",
            "load_policy": "balanced",
            "device": "cpu",
            "weights": ["wake.onnx"],
            "metadata": {"family": "wake-word"},
        },
    )
    _write_manifest(
        stt_dir,
        {
            "name": "whisper-large-v3-local",
            "runtime": "ctranslate2",
            "storage_format": "safetensors",
            "load_policy": "gpu_only",
            "weights": ["encoder.safetensors"],
            "config_files": ["config.json"],
            "memory_map": True,
        },
    )
    _write_manifest(
        brain_dir,
        {
            "name": "phoenixguard-voice-brain-local",
            "runtime": "llama.cpp",
            "storage_format": "safetensors",
            "load_policy": "gpu_only",
            "weights": ["model.safetensors"],
            "config_files": ["config.json"],
            "memory_map": True,
            "metadata": {"family": "phoenixguard-voice-brain", "tool_use": True},
        },
    )
    _write_manifest(
        speech_dir,
        {
            "name": "openvoice-v2-local",
            "runtime": "openvoice",
            "storage_format": "safetensors",
            "load_policy": "gpu_only",
            "weights": ["tts.safetensors"],
            "config_files": ["config.json"],
            "memory_map": True,
        },
    )

    config = VoiceConfig(project_root=tmp_path)
    stack = resolve_local_voice_stack(config)

    assert stack.brain.name == "phoenixguard-voice-brain-local"
    assert stack.speech.storage_format == "safetensors"
    assert stack.speech_to_text.memory_map is True
    assert stack.total_weight_bytes >= stack.brain.total_weight_bytes


def test_resolve_local_voice_bundle_rejects_remote_manifest_fields(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "models" / "voice" / "brain" / "remote-brain"
    _write_safetensors(bundle_dir / "model.safetensors")
    _write_manifest(
        bundle_dir,
        {
            "name": "remote-brain",
            "runtime": "llama.cpp",
            "storage_format": "safetensors",
            "load_policy": "gpu_only",
            "weights": ["model.safetensors"],
            "memory_map": True,
            "repo_id": "Example/Remote-Brain",
        },
    )

    with pytest.raises(VoiceBundleValidationError, match="remote field 'repo_id'"):
        resolve_local_voice_bundle(tmp_path / "models" / "voice", "brain", "remote-brain")


def test_resolve_local_voice_bundle_requires_safetensors_for_heavy_models(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "models" / "voice" / "brain" / "local-gguf-brain"
    (bundle_dir / "model.gguf").parent.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "model.gguf").write_bytes(b"gguf")
    _write_manifest(
        bundle_dir,
        {
            "name": "local-gguf-brain",
            "runtime": "llama.cpp",
            "storage_format": "gguf",
            "load_policy": "gpu_only",
            "weights": ["model.gguf"],
            "memory_map": True,
        },
    )

    with pytest.raises(VoiceBundleValidationError, match="must use safetensors"):
        resolve_local_voice_bundle(tmp_path / "models" / "voice", "brain", "local-gguf-brain")
