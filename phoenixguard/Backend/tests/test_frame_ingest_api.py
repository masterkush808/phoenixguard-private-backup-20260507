from __future__ import annotations

from io import BytesIO
from typing import Any, Mapping

from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api.app import create_app


class _FakeFrameTracker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def ingest_external_frame(
        self,
        session_id: str,
        image: Image.Image,
        *,
        source_id: str,
        symbol: str = "",
        timeframe: str = "",
        source_url: str = "",
        sequence_id: str = "",
        capture_epoch_ms: int = 0,
        frame_id: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        call = {
            "session_id": session_id,
            "size": image.size,
            "source_id": source_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "source_url": source_url,
            "sequence_id": sequence_id,
            "capture_epoch_ms": capture_epoch_ms,
            "frame_id": frame_id,
            "metadata": dict(metadata or {}),
        }
        self.calls.append(call)
        return {
            "session_id": session_id,
            "status": "external_frame_feed",
            "capture_count": 1,
            "frame_index": 1,
            "state_version": 1,
            "external_frame_feed": {
                "source_id": source_id,
                "symbol": symbol,
                "timeframe": timeframe,
            },
        }

    def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "status": "external_frame_feed",
            "capture_count": 1,
            "frame_index": 1,
            "external_frame_feed": {"source_id": "edge-agent"},
        }


def _png_bytes(width: int = 160, height: int = 120) -> bytes:
    image = Image.new("RGB", (width, height), (8, 12, 18))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_frame_ingest_config_reports_contract() -> None:
    client = TestClient(create_app(window_tracker_service=_FakeFrameTracker()))

    response = client.get("/v1/mobile/frame-ingest/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "PG_FRAME_INGEST_CONFIG_V1"
    assert payload["token_required"] is True
    assert "edge_agent_screenshot" in payload["supported_sources"]


def test_frame_ingest_requires_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", raising=False)
    client = TestClient(create_app(window_tracker_service=_FakeFrameTracker()))

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "edge-agent"},
    )

    assert response.status_code == 503
    assert "not armed" in response.json()["detail"]


def test_frame_ingest_accepts_authenticated_chart_frame(monkeypatch: Any) -> None:
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = TestClient(create_app(window_tracker_service=tracker))

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-agent",
            "symbol": "EURCAD",
            "timeframe": "M5",
            "source_url": "https://example.test/chart",
            "sequence_id": "edge-seq-1",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "42",
            "metadata_json": '{"plane":"chart"}',
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["session_id"] == "external-live"
    assert payload["external_frame_feed"]["source_id"] == "edge-agent"
    assert len(tracker.calls) == 1
    call = tracker.calls[0]
    assert call["size"] == (160, 120)
    assert call["symbol"] == "EURCAD"
    assert call["timeframe"] == "M5"
    assert call["frame_id"] == 42
    assert call["metadata"]["plane"] == "chart"
