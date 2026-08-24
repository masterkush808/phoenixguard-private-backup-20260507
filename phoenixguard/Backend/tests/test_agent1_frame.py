from fastapi.testclient import TestClient
from phoenixguard.mobile_api.app import create_app
from pathlib import Path
from typing import Any
from uuid import uuid4

app = create_app()
client: Any = TestClient(app)


def test_v3_chart_state_and_frame_endpoint() -> None:
    session_id = f"test-chart-state-{uuid4().hex}"
    create_response = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={
            "session_id": session_id,
            "window_query": "Pocket Option",
            "capture_interval_sec": 0.5,
        },
    )
    assert create_response.status_code == 201

    resp = client.get(f"/v1/mobile/chart/state/v3?session_id={session_id}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("schema_version") == "V3_CHART_STATE"
    assert payload.get("session_id") == session_id
    assert isinstance(payload.get("frame_id"), int)
    assert isinstance(payload.get("frame_exists"), bool)
    if payload.get("frame_exists"):
        url = payload.get("frame_url")
        assert url, "frame_url missing"
        # fetch the image
        img_resp = client.get(url)
        assert img_resp.status_code == 200
        assert img_resp.headers.get("content-type") in {"image/png", "application/octet-stream", None}
        content = img_resp.content
        assert content[:8] == b"\x89PNG\r\n\x1a\n"
        # save evidence
        out_dir = Path('.codex_runtime/visual_evidence')
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / 'latest_chart_frame.png').write_bytes(content)
        (out_dir / 'chart_state_response.json').write_text(resp.text, encoding='utf-8')
