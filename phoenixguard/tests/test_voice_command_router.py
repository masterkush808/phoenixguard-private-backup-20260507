from __future__ import annotations

from pathlib import Path

from phoenixguard.voice.agent import LocalVoiceAgentRuntime
from phoenixguard.voice.router import VoiceCommandRouter


class _StubConfig:
    enabled = True
    bundle_root = Path("models/voice")
    preferred_device = "cuda"
    max_cpu_memory_mb = 1024
    require_local_files_only = True
    require_safetensors_for_heavy_models = True
    forbid_cpu_offload_for_heavy_models = True
    wake_word_bundle_name = "openwakeword-local"
    speech_to_text_bundle_name = "whisper-large-v3-local"
    brain_bundle_name = "phoenixguard-voice-brain-local"
    speech_bundle_name = "openvoice-v2-local"


def test_router_executes_registered_command_by_alias() -> None:
    router = VoiceCommandRouter()
    router.register(
        "voice.stack.status",
        "Return the current voice stack status.",
        lambda args, _context: {"bundle": args.get("bundle", "default")},
        aliases=("voice.stack",),
    )

    result = router.execute("voice.stack", args={"bundle": "brain"})

    assert result.executed is True
    assert result.name == "voice.stack.status"
    assert result.payload["bundle"] == "brain"


def test_router_requires_confirmation_when_flagged() -> None:
    router = VoiceCommandRouter()
    router.register(
        "phoenixguard.restart_voice_runtime",
        "Restart the local voice runtime.",
        lambda _args, _context: {"restarted": True},
        confirmation_required=True,
    )

    blocked = router.execute("phoenixguard.restart_voice_runtime")
    allowed = router.execute("phoenixguard.restart_voice_runtime", confirmed=True)

    assert blocked.executed is False
    assert blocked.confirmation_required is True
    assert allowed.executed is True
    assert allowed.payload["restarted"] is True


def test_local_voice_agent_runtime_exposes_default_status_commands() -> None:
    runtime = LocalVoiceAgentRuntime(config=_StubConfig())

    commands = runtime.execute_command("voice.list_commands")
    snapshot = runtime.execute_command("phoenixguard.runtime_snapshot")

    assert commands.executed is True
    assert any(item["name"] == "voice.list_commands" for item in commands.payload["commands"])
    assert snapshot.payload["voice_enabled"] is True
