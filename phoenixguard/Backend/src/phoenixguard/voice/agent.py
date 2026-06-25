from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .bundles import LocalVoiceStack, SupportsVoiceConfig, resolve_local_voice_stack
from .router import VoiceCommandResult, VoiceCommandRouter, build_default_voice_command_router


@dataclass(slots=True)
class LocalVoiceAgentRuntime:
    config: SupportsVoiceConfig
    router: VoiceCommandRouter | None = None
    _stack: LocalVoiceStack | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.router is None:
            self.router = build_default_voice_command_router(
                runtime_snapshot=self.runtime_snapshot,
                stack_snapshot=self.stack_status,
            )

    def resolve_stack(self, *, force_reload: bool = False) -> LocalVoiceStack:
        if force_reload or self._stack is None:
            self._stack = resolve_local_voice_stack(self.config)
        return self._stack

    def runtime_snapshot(self) -> dict[str, Any]:
        return {
            "voice_enabled": bool(getattr(self.config, "enabled", False)),
            "bundle_root": str(self.config.bundle_root),
            "preferred_device": str(self.config.preferred_device),
            "max_cpu_memory_mb": int(self.config.max_cpu_memory_mb),
            "require_local_files_only": bool(self.config.require_local_files_only),
            "require_safetensors_for_heavy_models": bool(self.config.require_safetensors_for_heavy_models),
            "forbid_cpu_offload_for_heavy_models": bool(self.config.forbid_cpu_offload_for_heavy_models),
        }

    def stack_status(self) -> dict[str, Any]:
        try:
            return {
                "status": "ready",
                **self.resolve_stack().summary(),
            }
        except Exception as exc:
            return {
                "status": "missing_or_invalid",
                "error": str(exc),
            }

    def execute_command(
        self,
        name: str,
        *,
        args: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        confirmed: bool = False,
    ) -> VoiceCommandResult:
        router = self.router
        if router is None:
            raise RuntimeError("Voice command router is unavailable.")
        return router.execute(
            name,
            args=args,
            context=context,
            confirmed=confirmed,
        )
