from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from phoenixguard.mobile_api import realtime_sync_v3
from phoenixguard.mobile_api.app import create_app


def test_frontend_heartbeat_records_current_degraded_overlay_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime_sync_v3, "DEFAULT_HEARTBEAT_STORE_DIR", tmp_path)
    client = TestClient(create_app())
    session_id = "pocket-live-8788"
    alive_payload: dict[str, Any] = {
        "session_id": session_id,
        "surface_id": "dashboard",
        "frame_id": 100,
        "rendered_frame_id": 100,
        "display_frame_id": 1400,
        "chart_frame_id": 100,
        "overlay_render_frame_id": 100,
        "overlay_state_version": "ov_alive",
        "overlay_count": 4,
        "visible_overlay_count": 4,
        "full_broker_surface_visible": True,
    }

    alive_response = client.post("/v1/mobile/frontend/heartbeat/v3", json=alive_payload)

    assert alive_response.status_code == 200
    assert alive_response.json()["status"] == "ALIVE"

    degraded_payload = {
        **alive_payload,
        "frame_id": 101,
        "rendered_frame_id": 101,
        "chart_frame_id": 101,
        "overlay_render_frame_id": 101,
        "overlay_state_version": "ov_degraded",
        "visible_overlay_count": 0,
    }

    degraded_response = client.post("/v1/mobile/frontend/heartbeat/v3", json=degraded_payload)

    assert degraded_response.status_code == 200
    degraded_body = degraded_response.json()
    assert degraded_body["status"] == "DEGRADED"
    assert degraded_body["reason"] == "degraded_overlay_heartbeat"
    assert degraded_body["degraded_reason"] == "degraded_overlay_heartbeat"
    assert degraded_body["frame_id"] == 101
    assert degraded_body["visible_overlay_count"] == 0

    latest_response = client.get(f"/v1/mobile/frontend/heartbeat/v3?session_id={session_id}")

    assert latest_response.status_code == 200
    latest_body = latest_response.json()
    assert latest_body["status"] == "DEGRADED"
    assert latest_body["frame_id"] == 101
    assert latest_body["overlay_state_version"] == "ov_degraded"


def test_frontend_heartbeat_api_keeps_replay_separate_from_live_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime_sync_v3, "DEFAULT_HEARTBEAT_STORE_DIR", tmp_path)
    client = TestClient(create_app())
    session_id = "pocket-live-8788"
    replay_payload: dict[str, Any] = {
        "session_id": session_id,
        "surface_id": "dashboard",
        "route": "replay",
        "overlay_mode": "REPLAY",
        "frame_id": 210,
        "rendered_frame_id": 210,
        "display_frame_id": 210,
        "chart_frame_id": 210,
        "overlay_render_frame_id": 210,
        "overlay_state_version": "ov_replay",
        "overlay_count": 5,
        "visible_overlay_count": 5,
        "full_broker_surface_visible": True,
    }

    replay_response = client.post("/v1/mobile/frontend/heartbeat/v3", json=replay_payload)

    assert replay_response.status_code == 200
    assert replay_response.json()["surface_id"] == "dashboard_replay_replay"

    default_response = client.get(f"/v1/mobile/frontend/heartbeat/v3?session_id={session_id}")
    replay_surface_response = client.get(
        f"/v1/mobile/frontend/heartbeat/v3?session_id={session_id}&surface_id=dashboard_replay_replay"
    )

    assert default_response.status_code == 200
    assert default_response.json()["status"] == "missing"
    assert replay_surface_response.status_code == 200
    replay_surface_body = replay_surface_response.json()
    assert replay_surface_body["route"] == "replay"
    assert replay_surface_body["overlay_mode"] == "REPLAY"
    assert replay_surface_body["frame_id"] == 210


def test_frontend_heartbeat_api_ignores_transient_empty_live_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime_sync_v3, "DEFAULT_HEARTBEAT_STORE_DIR", tmp_path)
    client = TestClient(create_app())
    session_id = "pocket-live-8788"
    live_payload: dict[str, Any] = {
        "session_id": session_id,
        "surface_id": "dashboard",
        "route": "live",
        "overlay_mode": "CLEAN_LIVE",
        "frame_id": 300,
        "rendered_frame_id": 300,
        "display_frame_id": 300,
        "chart_frame_id": 300,
        "overlay_render_frame_id": 300,
        "overlay_state_version": "ov_live",
        "overlay_count": 24,
        "visible_overlay_count": 24,
        "full_broker_surface_visible": True,
    }

    live_response = client.post("/v1/mobile/frontend/heartbeat/v3", json=live_payload)
    transient_response = client.post(
        "/v1/mobile/frontend/heartbeat/v3",
        json={
            **live_payload,
            "frame_id": 301,
            "rendered_frame_id": 301,
            "display_frame_id": 301,
            "chart_frame_id": 301,
            "overlay_render_frame_id": 301,
            "overlay_state_version": "ov_live_refresh",
            "overlay_count": 0,
            "visible_overlay_count": 0,
        },
    )
    latest_response = client.get(f"/v1/mobile/frontend/heartbeat/v3?session_id={session_id}")

    assert live_response.status_code == 200
    assert transient_response.status_code == 200
    assert transient_response.json()["status"] == "ignored"
    assert transient_response.json()["reason"] == "transient_empty_overlay_heartbeat"
    latest_body = latest_response.json()
    assert latest_body["status"] == "ALIVE"
    assert latest_body["frame_id"] == 300
    assert latest_body["visible_overlay_count"] == 24
