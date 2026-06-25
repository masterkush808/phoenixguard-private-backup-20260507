from typing import Any
from pathlib import Path
from fastapi.testclient import TestClient
from phoenixguard.mobile_api.app import create_app
from phoenixguard.paths import PROJECT_ROOT
from phoenixguard.vision.market_registry import merge_market_objects

client = TestClient(create_app())


def test_render_endpoint_returns_png(tmp_path: Path):
    session = "test-render-session-1"
    o: dict[str, Any] = {"id": "r1", "bbox": [10, 10, 60, 60], "confidence": 0.85}
    merge_market_objects(session, [o])
    resp = client.get(f"/v1/mobile/registry/sessions/{session}/render/latest.png")
    assert resp.status_code == 200
    data = resp.content
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    candidate = PROJECT_ROOT / ".codex_runtime" / "visual_evidence" / f"{session}_render_latest.png"
    assert candidate.exists()
