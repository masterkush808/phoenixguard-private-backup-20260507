from __future__ import annotations

from io import BytesIO
from typing import Any, Mapping

from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.frame_ingest import reset_frame_ingest_runtime_state_for_tests


def _client(tracker: _FakeFrameTracker | None = None) -> TestClient:
    reset_frame_ingest_runtime_state_for_tests()
    return TestClient(create_app(window_tracker_service=tracker or _FakeFrameTracker()))


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


def test_frame_ingest_config_reports_contract(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    client = _client()

    response = client.get("/v1/mobile/frame-ingest/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "PG_FRAME_INGEST_CONFIG_V1"
    assert payload["token_required"] is True
    assert payload["scoped_tokens_supported"] is True
    assert "edge_agent_screenshot" in payload["supported_sources"]
    assert "mobile_manual_upload" in payload["supported_sources"]
    assert payload["readiness"]["armed"] is False


def test_frame_ingest_readiness_requires_armed_ingest(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    client = _client()

    response = client.get("/v1/mobile/frame-ingest/readiness")

    assert response.status_code == 503
    assert response.json()["detail"]["armed"] is False


def test_frame_ingest_mobile_uploader_serves_html() -> None:
    client = _client()

    response = client.get("/v1/mobile/frame-ingest/mobile-uploader")

    assert response.status_code == 200
    assert "PhoenixGuard Frame Feed" in response.text


def test_frame_ingest_requires_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", raising=False)
    client = _client()

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "edge-agent"},
    )

    assert response.status_code == 503
    assert "not armed" in response.json()["detail"]


def test_frame_ingest_accepts_authenticated_chart_frame(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)

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


def test_frame_ingest_requires_capture_epoch_and_frame_id(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    client = _client()

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "edge-agent"},
    )

    assert response.status_code == 400
    assert "capture_epoch_ms is required" in response.json()["detail"]


def test_frame_ingest_rejects_too_fast_feed(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    client = _client()
    first = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )

    second = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000015000", "frame_id": "2"},
    )

    assert first.status_code == 202
    assert second.status_code == 429


def test_frame_ingest_accepts_scoped_token_registry(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", raising=False)
    registry_path = tmp_path / "frame_tokens.json"
    registry_path.write_text(
        """
{
  "schema_version": "PG_FRAME_INGEST_TOKEN_REGISTRY_V1",
  "tokens": [
    {
      "name": "user001-feed",
      "enabled": true,
      "user_id": "user001",
      "token_env": "TEST_FEED_TOKEN_USER001",
      "allowed_session_prefixes": ["user001-"],
      "allowed_source_ids": ["user001-desktop"],
      "allowed_symbols": ["EURCAD"],
      "allowed_timeframes": ["M5"]
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", str(registry_path))
    monkeypatch.setenv("TEST_FEED_TOKEN_USER001", "scoped-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/user001-live/frames",
        headers={"Authorization": "Bearer scoped-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "user001-desktop",
            "symbol": "EURCAD",
            "timeframe": "M5",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "5",
        },
    )

    assert response.status_code == 202
    assert tracker.calls[0]["metadata"]["feed_token_name"] == "user001-feed"
    assert tracker.calls[0]["metadata"]["feed_user_id"] == "user001"


def test_frame_ingest_rejects_scoped_source_mismatch(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", raising=False)
    registry_path = tmp_path / "frame_tokens.json"
    registry_path.write_text(
        """
{
  "tokens": [
    {
      "name": "user001-feed",
      "token": "scoped-token",
      "allowed_session_prefixes": ["user001-"],
      "allowed_source_ids": ["user001-desktop"]
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", str(registry_path))
    client = _client()

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/user001-live/frames",
        headers={"Authorization": "Bearer scoped-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "other-source"},
    )

    assert response.status_code == 403
    assert "source_id" in response.json()["detail"]
