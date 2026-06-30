from typing import Any
from pathlib import Path
from fastapi.testclient import TestClient
from phoenixguard.mobile_api.app import create_app
from phoenixguard.vision.market_registry import merge_market_objects


client = TestClient(create_app())


def test_registry_active_endpoint(tmp_path: Path):
    session = "test-api-session-1"
    o: dict[str, Any] = {"id": "api_o1", "bbox": [0, 0, 10, 10], "confidence": 0.9}
    merge_market_objects(session, [o])
    resp = client.get(f"/v1/mobile/registry/sessions/{session}/active")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("session_id") == session
    assert payload.get("count", 0) >= 1
    assert payload.get("legacy_active_count", 0) >= 1
    assert payload.get("precision_count") == 0

    precision_resp = client.get(f"/v1/mobile/registry/sessions/{session}/active?precision_only=true")
    assert precision_resp.status_code == 200
    precision_payload = precision_resp.json()
    assert precision_payload.get("count") == 0
    assert precision_payload.get("precision_only") is True


def test_visual_health_endpoint(tmp_path: Path):
    session = "test-api-session-1"
    resp = client.get(f"/v1/mobile/visual/health/v3?session_id={session}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("schema_version") == "PG_VISUAL_HEALTH_V3"
    assert "registry" in payload
