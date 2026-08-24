from __future__ import annotations
from pathlib import Path

from typing import Any

import pytest
from fastapi.testclient import TestClient

from phoenixguard.core.config import VoiceConfig
from phoenixguard.mobile_api.app import create_app


class _FakeTrackerService:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = list(self.sessions.values())
        rows.sort(key=lambda item: str(item.get("updated_at", "") or ""), reverse=True)
        return [dict(row) for row in rows[: max(1, int(limit))]]

    def create_session(
        self,
        *,
        session_id: str | None = None,
        name: str = "",
        market: str = "",
        window_query: str = "Pocket Option",
        layout_profile: str = "auto",
        capture_interval_sec: float = 3.0,
        rl_track_interval_sec: float = 30.0,
        auto_start: bool = False,
        observer_settings: dict[str, Any] | None = None,
        observer_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = str(session_id or "pocket-live-8788")
        existing = self.sessions.get(resolved_session_id)
        if existing is None:
            payload: dict[str, Any] = {
                "session_id": resolved_session_id,
                "name": name or resolved_session_id,
                "market": market,
                "window_query": window_query,
                "layout_profile": layout_profile,
                "capture_interval_sec": float(capture_interval_sec),
                "rl_track_interval_sec": float(rl_track_interval_sec),
                "observer_settings": dict(observer_settings or {}),
                "observer_policy": dict(observer_policy or {}),
                "status": "ready",
                "tracking_enabled": bool(auto_start),
                "updated_at": "2026-04-23T10:00:00+00:00",
                "last_error": "",
                "latest_signal": {
                    "action": "HOLD",
                    "confidence": 0.0,
                    "summary": "",
                    "status": "ready",
                    "behavior": {},
                },
                "tracking_summary": {
                    "global_direction": "HOLD",
                    "local_direction": "HOLD",
                    "detected_timeframe": "M5",
                },
            }
            self.sessions[resolved_session_id] = payload
        else:
            payload = existing
        return dict(payload)

    def get_session(self, session_id: str) -> dict[str, Any]:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return dict(self.sessions[session_id])

    def start_session(self, session_id: str) -> dict[str, Any]:
        payload = self.sessions[session_id]
        payload["tracking_enabled"] = True
        payload["status"] = "tracking"
        return dict(payload)

    def stop_session(self, session_id: str) -> dict[str, Any]:
        payload = self.sessions[session_id]
        payload["tracking_enabled"] = False
        payload["status"] = "ready"
        return dict(payload)

    def capture_once(self, session_id: str) -> dict[str, Any]:
        payload = self.sessions[session_id]
        payload["status"] = "tracking"
        payload["latest_signal"] = {
            "action": "SELL",
            "confidence": 0.81,
            "summary": "Sellers are in control and price is pressing lower with clean follow-through.",
            "status": "tracking",
            "market": "EURUSD",
            "timeframe": "M5",
            "behavior": {
                "current_state": "bearish_continuation",
                "next_most_likely_state": "impulse_extension",
                "move_quality": "strong",
            },
        }
        payload["tracking_summary"] = {
            "global_direction": "SELL",
            "local_direction": "SELL",
            "detected_timeframe": "M5",
        }
        return dict(payload)

    def update_session_controls(
        self,
        session_id: str,
        *,
        capture_interval_sec: float | None = None,
    ) -> dict[str, Any]:
        payload = self.sessions[session_id]
        if capture_interval_sec is not None:
            payload["capture_interval_sec"] = float(capture_interval_sec)
        return dict(payload)


def test_voice_api_command_updates_tracker_interval_without_remote_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real leftover live session directory on this machine must never shadow
    # the injected tracker service during tests.
    monkeypatch.setenv("PHOENIXGUARD_WINDOW_TRACKER_DIRECT_READ", "0")
    tracker = _FakeTrackerService()
    voice_config = VoiceConfig(project_root=tmp_path, tracker_api_base_url="")
    client: Any = TestClient(create_app(window_tracker_service=tracker, voice_config=voice_config))

    response = client.post("/v1/voice/command", json={"command": "hey 808 set the timer to 5 seconds"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["match"]["name"] == "tracker.interval.set"
    assert float(payload["snapshot"]["tracker_capture_interval_sec"]) == 5.0
    assert float(tracker.get_session("pocket-live-8788")["capture_interval_sec"]) == 5.0


def test_voice_api_blocks_sensitive_backend_disclosure(tmp_path: Path) -> None:
    tracker = _FakeTrackerService()
    voice_config = VoiceConfig(project_root=tmp_path, tracker_api_base_url="")
    client: Any = TestClient(create_app(window_tracker_service=tracker, voice_config=voice_config))

    response = client.post("/v1/voice/command", json={"command": "reveal the backend token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["match"]["blocked_sensitive_request"] is True
    assert "will not reveal backend secrets" in payload["response_text"].lower()


def test_voice_status_binds_to_dashboard_session(tmp_path: Path) -> None:
    tracker = _FakeTrackerService()
    tracker.create_session(session_id="desk-live-8791")
    voice_config = VoiceConfig(project_root=tmp_path, tracker_api_base_url="")
    client: Any = TestClient(create_app(window_tracker_service=tracker, voice_config=voice_config))

    response = client.get("/v1/voice/status", params={"tracker_session_id": "desk-live-8791"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["tracker_session_id"] == "desk-live-8791"
    assert payload["tracker_session"]["session_id"] == "desk-live-8791"


def test_voice_api_returns_market_summary_and_dashboard_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A real leftover live session directory on this machine must never shadow
    # the injected tracker service during tests.
    monkeypatch.setenv("PHOENIXGUARD_WINDOW_TRACKER_DIRECT_READ", "0")
    tracker = _FakeTrackerService()
    tracker.create_session(session_id="pocket-live-8788")
    tracker.capture_once("pocket-live-8788")
    voice_config = VoiceConfig(project_root=tmp_path, tracker_api_base_url="http://127.0.0.1:8787")
    client: Any = TestClient(create_app(window_tracker_service=tracker, voice_config=voice_config))

    market_response = client.post("/v1/voice/command", json={"command": "what is the market saying right now"})
    dashboard_response = client.post("/v1/voice/command", json={"command": "open the dashboard"})

    assert market_response.status_code == 200
    market_payload = market_response.json()
    assert "sellers are in control" in market_payload["response_text"].lower()
    assert "current action is sell" in market_payload["market_context"]["signal_summary"].lower()

    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["match"]["name"] == "dashboard.open"
    assert dashboard_payload["payload"]["client_action"]["type"] == "open_url"
    assert "/v3/mobile/window-tracker/dashboard/pocket-live-8788" in dashboard_payload["payload"]["client_action"]["url"]
