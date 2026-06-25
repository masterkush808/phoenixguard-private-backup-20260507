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
