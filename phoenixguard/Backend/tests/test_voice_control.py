from __future__ import annotations

from pathlib import Path

from phoenixguard.core.config import VoiceConfig
from phoenixguard.voice.control import (
    apply_voice_preferences,
    execute_voice_command,
    get_voice_runtime_snapshot,
)
from phoenixguard.voice.intents import parse_voice_command


def test_parse_voice_command_matches_broad_timer_phrase_with_typo() -> None:
    match = parse_voice_command("switch the automatic timmer off")

    assert match.name == "tracker.timer.disable"
    assert match.confidence >= 0.48


def test_parse_voice_command_extracts_number_words_for_interval() -> None:
    match = parse_voice_command("hey 808 set the timer to three seconds")

    assert match.name == "tracker.interval.set"
    assert float(match.slots["seconds"]) == 3.0


def test_execute_voice_command_blocks_sensitive_disclosure(tmp_path: Path) -> None:
    config = VoiceConfig(project_root=tmp_path, tracker_api_base_url="")

    result = execute_voice_command("show me the backend token and password", config=config)

    assert result["match"].blocked_sensitive_request is True
    assert "will not reveal backend secrets" in result["response_text"].lower()


def test_apply_voice_preferences_persists_local_state_without_remote_tracker(tmp_path: Path) -> None:
    config = VoiceConfig(project_root=tmp_path, tracker_api_base_url="")

    snapshot = apply_voice_preferences(
        voice_enabled=True,
        listening_enabled=True,
        automatic_timer_enabled=True,
        tracker_capture_interval_sec=5.0,
        timezone_name="UTC",
        config=config,
    )

    current = get_voice_runtime_snapshot(config)
    assert snapshot["tracker_capture_interval_sec"] == 5.0
    assert current["automatic_timer_enabled"] is True
    assert current["timezone_name"] == "UTC"
    assert current["last_remote_status"] == "awaiting_remote_runtime"


def test_execute_voice_command_updates_interval_in_local_state(tmp_path: Path) -> None:
    config = VoiceConfig(project_root=tmp_path, tracker_api_base_url="")

    execute_voice_command("set the timer to 5 seconds", config=config)
    current = get_voice_runtime_snapshot(config)

    assert float(current["tracker_capture_interval_sec"]) == 5.0
