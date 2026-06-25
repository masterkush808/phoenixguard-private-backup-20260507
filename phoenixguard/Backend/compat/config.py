from __future__ import annotations

from _pg_bootstrap import ensure_project_paths
ensure_project_paths()

from phoenixguard.core.config import (
    MEMORY_BANK,
    MODELS,
    RUNTIME,
    SECURITY,
    TRAIN,
    VOICE,
    MemoryBankConfig,
    ModelConfig,
    RuntimeConfig,
    SecurityConfig,
    TrainConfig,
    VoiceConfig,
)

__all__ = [
    "MEMORY_BANK",
    "MODELS",
    "RUNTIME",
    "SECURITY",
    "TRAIN",
    "VOICE",
    "MemoryBankConfig",
    "ModelConfig",
    "RuntimeConfig",
    "SecurityConfig",
    "TrainConfig",
    "VoiceConfig",
]
