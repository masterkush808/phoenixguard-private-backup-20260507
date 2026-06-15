from __future__ import annotations

from dataclasses import dataclass
import html
import json
from pathlib import Path
from threading import Lock
import time
from typing import Any, Mapping

from phoenixguard.core.config import VOICE, VoiceConfig
from phoenixguard.core.utils import utc_now_iso

from .intents import VoiceIntentMatch, parse_voice_command, public_voice_command_catalog
from .remote import VoiceRemoteClientError, WindowTrackerRemoteClient
from .router import VoiceCommandRouter, build_default_voice_command_router
from .time_utils import default_timezone_name, greeting_for_time, local_now


_VOICE_STATE_LOCK = Lock()


def _default_voice_state(config: VoiceConfig = VOICE) -> dict[str, Any]:
    timezone_name = str(config.timezone_name or "").strip() or default_timezone_name()
    return {
        "voice_enabled": bool(config.enabled),
        "listening_enabled": bool(config.listening_enabled_default),
        "automatic_timer_enabled": bool(config.automatic_timer_enabled_default),
        "tracker_capture_interval_sec": float(config.tracker_interval_sec_default),
        "wake_word": str(config.wake_word),
        "greeting_target_name": str(config.greeting_target_name),
        "timezone_name": timezone_name,
        "low_latency_mode": bool(config.low_latency_mode),
        "remote_enabled": bool(config.remote_enabled),
        "remote_base_url": str(config.remote_base_url or ""),
        "tracker_api_base_url": str(config.tracker_api_base_url or ""),
        "tracker_session_id": str(config.tracker_session_id or "pocket-live-8788"),
        "sensitive_data_guard_enabled": bool(config.sensitive_data_guard_enabled),
        "last_command": "",
        "last_intent": "",
        "last_response": greeting_for_time(
            timezone_name=timezone_name,
            target_name=str(config.greeting_target_name or "Master"),
        ),
        "last_response_at": utc_now_iso(),
        "last_market_summary": "",
        "last_transition_summary": "",
        "last_remote_status": "awaiting_remote_runtime",
        "last_remote_error": "",
    }


def _read_state(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    if not isinstance(raw, dict):
        return dict(default)
    merged = dict(default)
    merged.update(raw)
    return merged


def _write_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(dict(payload), ensure_ascii=True, indent=2, default=str),
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_voice_state(config: VoiceConfig = VOICE) -> dict[str, Any]:
    with _VOICE_STATE_LOCK:
        payload = _read_state(config.state_path, _default_voice_state(config))
        _write_state(config.state_path, payload)
        return dict(payload)


def save_voice_state(payload: Mapping[str, Any], config: VoiceConfig = VOICE) -> dict[str, Any]:
    with _VOICE_STATE_LOCK:
        merged = dict(_default_voice_state(config))
        merged.update(dict(payload))
        _write_state(config.state_path, merged)
        return dict(merged)


def update_voice_state(config: VoiceConfig = VOICE, **updates: Any) -> dict[str, Any]:
    current = load_voice_state(config)
    current.update(updates)
    current["last_response_at"] = utc_now_iso()
    return save_voice_state(current, config)


def get_voice_runtime_snapshot(config: VoiceConfig = VOICE) -> dict[str, Any]:
    state = load_voice_state(config)
    timezone_name = str(state.get("timezone_name", "") or "").strip() or default_timezone_name()
    greeting_target_name = str(state.get("greeting_target_name", "Master") or "Master")
    now_local = local_now(timezone_name)
    state["timezone_name"] = timezone_name
    state["greeting_target_name"] = greeting_target_name
    state["greeting"] = greeting_for_time(timezone_name=timezone_name, target_name=greeting_target_name)
    state["local_time"] = now_local.strftime("%Y-%m-%d %H:%M:%S")
    state["local_time_short"] = now_local.strftime("%I:%M %p").lstrip("0")
    state["command_catalog"] = public_voice_command_catalog()
    return state


def _append_command_history(command_text: str, response_text: str, *, intent_name: str, config: VoiceConfig = VOICE) -> None:
    record = {
        "timestamp": utc_now_iso(),
        "command": str(command_text or "").strip(),
        "intent": str(intent_name or ""),
        "response": str(response_text or "").strip(),
    }
    config.command_history_path.parent.mkdir(parents=True, exist_ok=True)
    with config.command_history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def _tracker_client(config: VoiceConfig, state: Mapping[str, Any]) -> WindowTrackerRemoteClient:
    return WindowTrackerRemoteClient(
        base_url=str(state.get("tracker_api_base_url", config.tracker_api_base_url) or ""),
        timeout_sec=int(config.remote_timeout_sec),
    )


def _resolve_tracker_controller(
    config: VoiceConfig,
    state: Mapping[str, Any],
    tracker_controller: Any | None = None,
) -> Any:
    if tracker_controller is not None:
        return tracker_controller
    return _tracker_client(config, state)


def _tracker_controller_configured(controller: Any) -> bool:
    configured = getattr(controller, "configured", None)
    if configured is None:
        return True
    return bool(configured)


def apply_voice_preferences(
    *,
    voice_enabled: bool,
    listening_enabled: bool,
    automatic_timer_enabled: bool,
    tracker_capture_interval_sec: float,
    timezone_name: str,
    config: VoiceConfig = VOICE,
    tracker_controller: Any | None = None,
) -> dict[str, Any]:
    current = load_voice_state(config)
    sanitized_interval = float(min(10.0, max(0.5, float(tracker_capture_interval_sec or config.tracker_interval_sec_default))))
    sanitized_timezone = str(timezone_name or "").strip() or default_timezone_name()
    current.update(
        {
            "voice_enabled": bool(voice_enabled),
            "listening_enabled": bool(listening_enabled),
            "automatic_timer_enabled": bool(automatic_timer_enabled),
            "tracker_capture_interval_sec": sanitized_interval,
            "timezone_name": sanitized_timezone,
            "last_remote_error": "",
        }
    )
    remote_message = ""
    try:
        controller = _resolve_tracker_controller(config, current, tracker_controller)
        if _tracker_controller_configured(controller):
            session_id = str(current.get("tracker_session_id", config.tracker_session_id) or config.tracker_session_id)
            controller.update_interval(session_id, capture_interval_sec=sanitized_interval)
            if automatic_timer_enabled:
                controller.start_session(session_id)
                remote_message = f"Automatic tracker running at {sanitized_interval:.0f} seconds."
            else:
                controller.stop_session(session_id)
                remote_message = "Automatic tracker is paused."
            current["last_remote_status"] = "connected"
        else:
            current["last_remote_status"] = "awaiting_remote_runtime"
            remote_message = "Saved locally. Connect the tracker API to drive live automation."
    except VoiceRemoteClientError as exc:
        current["last_remote_status"] = "tracker_api_unreachable"
        current["last_remote_error"] = str(exc)
        remote_message = f"Saved locally, but the tracker API is unavailable: {exc}"
    current["last_response"] = remote_message or current.get("last_response", "")
    current["last_response_at"] = utc_now_iso()
    return save_voice_state(current, config)


def _help_response(snapshot: Mapping[str, Any]) -> str:
    names = [row["name"] for row in public_voice_command_catalog()[:6]]
    return (
        "I can control the tracker timer, change the capture interval, read the market in plain English, "
        f"explain transitions, pause listening, and report status. Current core commands: {', '.join(names)}."
    )


def _status_response(snapshot: Mapping[str, Any], config: VoiceConfig = VOICE) -> str:
    return (
        f"{snapshot.get('greeting', 'Hello')}. Voice is {'on' if snapshot.get('voice_enabled', False) else 'off'}, "
        f"listening is {'active' if snapshot.get('listening_enabled', False) else 'paused'}, "
        f"automatic tracking is {'running' if snapshot.get('automatic_timer_enabled', False) else 'paused'}, "
        f"and the tracker interval is {float(snapshot.get('tracker_capture_interval_sec', config.tracker_interval_sec_default)):.1f} seconds."
    )


def _dashboard_url(snapshot: Mapping[str, Any], config: VoiceConfig) -> str:
    base_url = str(snapshot.get("tracker_api_base_url", config.tracker_api_base_url) or "").rstrip("/")
    session_id = str(snapshot.get("tracker_session_id", config.tracker_session_id) or config.tracker_session_id)
    if not base_url:
        return f"/v1/mobile/window-tracker/dashboard/{session_id}"
    return f"{base_url}/v1/mobile/window-tracker/dashboard/{session_id}"


def execute_voice_command(
    command_text: str,
    *,
    market_context: Mapping[str, Any] | None = None,
    config: VoiceConfig = VOICE,
    tracker_controller: Any | None = None,
) -> dict[str, Any]:
    context = dict(market_context or {})
    snapshot = load_voice_state(config)
    match = parse_voice_command(command_text)
    response_text = ""

    if match.blocked_sensitive_request and bool(snapshot.get("sensitive_data_guard_enabled", True)):
        response_text = "I will not reveal backend secrets, tokens, credentials, or protected configuration."
        updated = update_voice_state(
            config,
            last_command=str(command_text or "").strip(),
            last_intent=match.name,
            last_response=response_text,
            last_remote_status=str(snapshot.get("last_remote_status", "awaiting_remote_runtime")),
        )
        _append_command_history(command_text, response_text, intent_name=match.name, config=config)
        return {
            "match": match,
            "snapshot": get_voice_runtime_snapshot(config),
            "response_text": response_text,
            "state": updated,
        }

    router = build_default_voice_command_router(
        runtime_snapshot=lambda: get_voice_runtime_snapshot(config),
        stack_snapshot=lambda: {"status": "local_bundle_validation_pending"},
    )
    router.register("voice.help", "List supported voice commands.", lambda _args, _ctx: {"message": _help_response(get_voice_runtime_snapshot(config))})
    router.register("voice.enable", "Enable the 808 voice layer.", lambda _args, _ctx: {"message": "808 voice is now enabled.", "state": update_voice_state(config, voice_enabled=True)})
    router.register("voice.disable", "Disable the 808 voice layer.", lambda _args, _ctx: {"message": "808 voice is now muted.", "state": update_voice_state(config, voice_enabled=False, listening_enabled=False)})
    router.register("voice.listening.enable", "Resume listening.", lambda _args, _ctx: {"message": "Listening resumed.", "state": update_voice_state(config, listening_enabled=True)})
    router.register("voice.listening.disable", "Pause listening.", lambda _args, _ctx: {"message": "Listening paused.", "state": update_voice_state(config, listening_enabled=False)})

    def _start_timer(_args: Mapping[str, Any], _ctx: Mapping[str, Any] | None) -> dict[str, Any]:
        state = apply_voice_preferences(
            voice_enabled=bool(snapshot.get("voice_enabled", True)),
            listening_enabled=bool(snapshot.get("listening_enabled", True)),
            automatic_timer_enabled=True,
            tracker_capture_interval_sec=float(snapshot.get("tracker_capture_interval_sec", config.tracker_interval_sec_default)),
            timezone_name=str(snapshot.get("timezone_name", default_timezone_name())),
            config=config,
            tracker_controller=tracker_controller,
        )
        return {"message": str(state.get("last_response", "Automatic tracker started.")), "state": state}

    def _stop_timer(_args: Mapping[str, Any], _ctx: Mapping[str, Any] | None) -> dict[str, Any]:
        state = apply_voice_preferences(
            voice_enabled=bool(snapshot.get("voice_enabled", True)),
            listening_enabled=bool(snapshot.get("listening_enabled", True)),
            automatic_timer_enabled=False,
            tracker_capture_interval_sec=float(snapshot.get("tracker_capture_interval_sec", config.tracker_interval_sec_default)),
            timezone_name=str(snapshot.get("timezone_name", default_timezone_name())),
            config=config,
            tracker_controller=tracker_controller,
        )
        return {"message": str(state.get("last_response", "Automatic tracker paused.")), "state": state}

    def _set_interval(args: Mapping[str, Any], _ctx: Mapping[str, Any] | None) -> dict[str, Any]:
        seconds = float(args.get("seconds", snapshot.get("tracker_capture_interval_sec", config.tracker_interval_sec_default)))
        state = apply_voice_preferences(
            voice_enabled=bool(snapshot.get("voice_enabled", True)),
            listening_enabled=bool(snapshot.get("listening_enabled", True)),
            automatic_timer_enabled=bool(snapshot.get("automatic_timer_enabled", False)),
            tracker_capture_interval_sec=seconds,
            timezone_name=str(snapshot.get("timezone_name", default_timezone_name())),
            config=config,
            tracker_controller=tracker_controller,
        )
        return {"message": str(state.get("last_response", f"Tracker interval set to {seconds:.0f} seconds.")), "state": state}

    def _capture_once(_args: Mapping[str, Any], _ctx: Mapping[str, Any] | None) -> dict[str, Any]:
        state = load_voice_state(config)
        controller = _resolve_tracker_controller(config, state, tracker_controller)
        if not _tracker_controller_configured(controller):
            return {"message": "Tracker API is not connected yet, so I cannot force a live capture."}
        session_id = str(state.get("tracker_session_id", config.tracker_session_id) or config.tracker_session_id)
        try:
            payload = controller.capture_once(session_id)
            action = str(dict(payload.get("latest_signal", {})).get("action", "HOLD")).upper()
            return {"message": f"Live capture completed. Current tracker action is {action}.", "payload": payload}
        except VoiceRemoteClientError as exc:
            return {"message": f"I could not trigger a live capture because the tracker API is unavailable: {exc}"}

    router.register("tracker.timer.enable", "Start automatic tracker capture.", _start_timer)
    router.register("tracker.timer.disable", "Stop automatic tracker capture.", _stop_timer)
    router.register("tracker.interval.set", "Set tracker interval.", _set_interval)
    router.register("tracker.capture.once", "Trigger one capture immediately.", _capture_once)
    router.register("market.summary", "Read the market in plain English.", lambda _args, _ctx: {"message": str(context.get("market_summary", "No market summary is available yet."))})
    router.register("market.transitions", "Explain the transition in plain English.", lambda _args, _ctx: {"message": str(context.get("transition_summary", "No transition summary is available yet."))})
    router.register("market.risk", "Explain the risk posture.", lambda _args, _ctx: {"message": str(context.get("risk_summary", "No risk summary is available yet."))})
    router.register("market.signal", "Read the active signal.", lambda _args, _ctx: {"message": str(context.get("signal_summary", "No active signal summary is available yet."))})
    router.register(
        "dashboard.open",
        "Open the live PhoenixGuard dashboard.",
        lambda _args, _ctx: {
            "message": "Opening the live PhoenixGuard dashboard.",
            "client_action": {
                "type": "open_url",
                "url": _dashboard_url(get_voice_runtime_snapshot(config), config),
            },
        },
    )
    router.register("session.status", "Read the current runtime status.", lambda _args, _ctx: {"message": _status_response(get_voice_runtime_snapshot(config))})

    result = router.execute(match.name, args=match.slots, context=context, confirmed=True)
    response_text = str(result.payload.get("message", result.payload.get("result", "")) or "").strip()
    updated_state = dict(result.payload.get("state", load_voice_state(config)))
    updated_state = update_voice_state(
        config,
        **{
            **updated_state,
            "last_command": str(command_text or "").strip(),
            "last_intent": match.name,
            "last_response": response_text,
            "last_market_summary": str(context.get("market_summary", "")),
            "last_transition_summary": str(context.get("transition_summary", "")),
        },
    )
    _append_command_history(command_text, response_text, intent_name=match.name, config=config)
    return {
        "match": match,
        "snapshot": get_voice_runtime_snapshot(config),
        "response_text": response_text,
        "state": updated_state,
        "payload": dict(result.payload),
    }


def build_voice_console_html(
    *,
    snapshot: Mapping[str, Any] | None = None,
    market_context: Mapping[str, Any] | None = None,
) -> str:
    state = dict(snapshot or get_voice_runtime_snapshot())
    context = dict(market_context or {})
    voice_enabled = bool(state.get("voice_enabled", False))
    listening_enabled = bool(state.get("listening_enabled", False))
    automatic_timer_enabled = bool(state.get("automatic_timer_enabled", False))
    greeting = html.escape(str(state.get("greeting", "Hello")).strip() or "Hello")
    last_response = html.escape(str(state.get("last_response", "") or "").strip() or greeting)
    wake_word = html.escape(str(state.get("wake_word", "Hey 808") or "Hey 808"))
    timezone_name = html.escape(str(state.get("timezone_name", default_timezone_name()) or default_timezone_name()))
    remote_status = html.escape(str(state.get("last_remote_status", "awaiting_remote_runtime") or "awaiting_remote_runtime"))
    tracker_interval = float(state.get("tracker_capture_interval_sec", VOICE.tracker_interval_sec_default) or VOICE.tracker_interval_sec_default)
    market_summary = html.escape(str(context.get("market_summary", state.get("last_market_summary", "")) or "Run or refresh a signal to hear the live market summary in plain English."))
    transition_summary = html.escape(str(context.get("transition_summary", state.get("last_transition_summary", "")) or "Transition commentary will appear here after the active chart has a fresh readout."))
    status_chips = "".join(
        [
            f"<span class='pg-chip {'pg-chip-teal' if voice_enabled else 'pg-chip-soft'}'>808 Voice {'On' if voice_enabled else 'Off'}</span>",
            f"<span class='pg-chip {'pg-chip-teal' if listening_enabled else 'pg-chip-soft'}'>Listening {'Live' if listening_enabled else 'Paused'}</span>",
            f"<span class='pg-chip {'pg-chip-teal' if automatic_timer_enabled else 'pg-chip-soft'}'>Tracker {'Running' if automatic_timer_enabled else 'Paused'}</span>",
            f"<span class='pg-chip pg-chip-amber'>{tracker_interval:.0f}s Interval</span>",
            f"<span class='pg-chip pg-chip-soft'>{wake_word}</span>",
            f"<span class='pg-chip pg-chip-soft'>{timezone_name}</span>",
            f"<span class='pg-chip pg-chip-soft'>{remote_status.replace('_', ' ')}</span>",
        ]
    )
    commands = public_voice_command_catalog()[:6]
    command_rows = "".join(
        f"<li><strong>{html.escape(str(item['name']))}</strong>: {html.escape(str(item['examples'][0]))}</li>"
        for item in commands
    )
    return (
        "<div class='pg-live-panel'>"
        "<div class='pg-section-title'>808 Voice</div>"
        f"<div class='pg-chip-row'>{status_chips}</div>"
        f"<div class='pg-muted' style='margin-top:8px;'>{greeting}</div>"
        f"<div class='pg-muted' style='margin-top:6px;'><strong>Last response:</strong> {last_response}</div>"
        f"<div class='pg-muted' style='margin-top:10px;'><strong>Market readout:</strong> {market_summary}</div>"
        f"<div class='pg-muted' style='margin-top:6px;'><strong>Transitions:</strong> {transition_summary}</div>"
        "<details class='pg-brief-details' style='margin-top:12px;'>"
        "<summary>Command coverage</summary>"
        "<ul class='pg-brief-list' style='margin-top:8px;'>"
        f"{command_rows}"
        "</ul>"
        "</details>"
        "</div>"
    )
