from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, cast


VoiceCommandHandler = Callable[[Mapping[str, Any], Mapping[str, Any] | None], Any]


class VoiceCommandError(RuntimeError):
    pass


@dataclass(slots=True)
class VoiceCommandResult:
    name: str
    executed: bool
    confirmation_required: bool
    payload: dict[str, Any]


@dataclass(slots=True)
class _VoiceCommandSpec:
    name: str
    description: str
    handler: VoiceCommandHandler
    aliases: tuple[str, ...]
    confirmation_required: bool

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "aliases": list(self.aliases),
            "confirmation_required": self.confirmation_required,
        }


def _normalize_command_name(value: str) -> str:
    return ".".join(
        part
        for part in str(value or "").strip().lower().replace("/", ".").split(".")
        if part
    )


def _ensure_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


class VoiceCommandRouter:
    def __init__(self) -> None:
        self._commands: dict[str, _VoiceCommandSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        name: str,
        description: str,
        handler: VoiceCommandHandler,
        *,
        aliases: tuple[str, ...] = (),
        confirmation_required: bool = False,
    ) -> None:
        normalized_name = _normalize_command_name(name)
        if not normalized_name:
            raise VoiceCommandError("Voice command name cannot be empty.")
        if normalized_name in self._commands:
            raise VoiceCommandError(f"Voice command '{normalized_name}' is already registered.")
        normalized_aliases = tuple(
            alias_name
            for alias_name in (_normalize_command_name(alias) for alias in aliases)
            if alias_name
        )
        for alias_name in normalized_aliases:
            if alias_name in self._aliases or alias_name in self._commands:
                raise VoiceCommandError(f"Voice command alias '{alias_name}' is already registered.")
        spec = _VoiceCommandSpec(
            name=normalized_name,
            description=str(description).strip(),
            handler=handler,
            aliases=normalized_aliases,
            confirmation_required=bool(confirmation_required),
        )
        self._commands[normalized_name] = spec
        for alias_name in normalized_aliases:
            self._aliases[alias_name] = normalized_name

    def resolve(self, name: str) -> str:
        normalized_name = _normalize_command_name(name)
        if normalized_name in self._commands:
            return normalized_name
        resolved_alias = self._aliases.get(normalized_name)
        if resolved_alias:
            return resolved_alias
        raise VoiceCommandError(f"Voice command '{name}' is not registered.")

    def catalog(self) -> list[dict[str, Any]]:
        return [
            self._commands[name].summary()
            for name in sorted(self._commands)
        ]

    def execute(
        self,
        name: str,
        *,
        args: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        confirmed: bool = False,
    ) -> VoiceCommandResult:
        resolved_name = self.resolve(name)
        spec = self._commands[resolved_name]
        if spec.confirmation_required and not confirmed:
            return VoiceCommandResult(
                name=resolved_name,
                executed=False,
                confirmation_required=True,
                payload={
                    "status": "confirmation_required",
                    "message": f"Command '{resolved_name}' requires explicit confirmation before execution.",
                },
            )
        try:
            raw_result = spec.handler(_ensure_mapping(args), context)
        except Exception as exc:
            raise VoiceCommandError(f"Voice command '{resolved_name}' failed: {exc}") from exc
        payload: dict[str, Any] = (
            {str(key): value for key, value in cast(Mapping[Any, Any], raw_result).items()}
            if isinstance(raw_result, Mapping)
            else {"result": raw_result}
        )
        payload.setdefault("status", "ok")
        return VoiceCommandResult(
            name=resolved_name,
            executed=True,
            confirmation_required=False,
            payload=payload,
        )


def build_default_voice_command_router(
    *,
    runtime_snapshot: Callable[[], Mapping[str, Any]] | None = None,
    stack_snapshot: Callable[[], Mapping[str, Any]] | None = None,
) -> VoiceCommandRouter:
    router = VoiceCommandRouter()

    router.register(
        "voice.list_commands",
        "List the currently registered local voice commands.",
        lambda _args, _context: {"commands": router.catalog()},
        aliases=("voice.commands", "help.commands"),
    )

    if runtime_snapshot is not None:
        router.register(
            "phoenixguard.runtime_snapshot",
            "Read the current PhoenixGuard voice runtime settings.",
            lambda _args, _context: dict(runtime_snapshot()),
            aliases=("phoenixguard.runtime", "voice.runtime"),
        )

    if stack_snapshot is not None:
        router.register(
            "phoenixguard.voice_stack_status",
            "Read the current local voice bundle inventory and validation status.",
            lambda _args, _context: dict(stack_snapshot()),
            aliases=("voice.stack", "phoenixguard.voice"),
        )

    return router
