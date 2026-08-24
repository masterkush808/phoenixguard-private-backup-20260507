from typing import Any
from pathlib import Path
from fastapi.testclient import TestClient
import os
from phoenixguard.mobile_api.app import create_app
from phoenixguard.vision.market_registry import persist_market_objects


client: Any = TestClient(create_app())


def test_chart_transform_is_exposed(tmp_path: Path):
    session = "test-chart-transform-session"
    # sample overlay and transform
    o: dict[str, Any] = {"id": "ct_o1", "bbox": [0.1, 0.1, 0.2, 0.2], "confidence": 0.77}
    chart_transform: dict[str, Any] = {
        "chart_image_bounds": [0, 0, 800, 600],
        "scale": 1.0,
        "origin": [0, 0]
    }

    # persist with chart_transform
    persist_market_objects(session, [o], chart_transform=chart_transform)

    resp = client.get(f"/v1/mobile/registry/sessions/{session}/active")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("session_id") == session
    # API should include chart_transform key and it should be a mapping with expected keys
    ct = payload.get("chart_transform")
    assert isinstance(ct, dict)
    assert "chart_image_bounds" in ct

    # cleanup: remove registry file if created
    # load_market_objects gives list of entries; attempt to delete registry jsonl file via paths used by registry
    # best-effort cleanup: find registry file under REGISTRY_DIR environment or default
    try:
        # The registry module writes files under phoenixguard/paths.py REGISTRY_DIR; remove matching file
        from phoenixguard.vision.market_registry import REGISTRY_DIR
        fp = os.path.join(REGISTRY_DIR, f"{session}.jsonl")
        if os.path.exists(fp):
            os.remove(fp)
    except Exception:
        pass
