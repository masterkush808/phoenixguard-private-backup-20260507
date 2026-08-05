from __future__ import annotations

import asyncio
import hashlib
import hmac
from io import BytesIO
import json
import threading
import time
from typing import Any, Mapping
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from PIL import Image

from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.frame_ingest import reset_frame_ingest_runtime_state_for_tests


def _client(tracker: _FakeFrameTracker | None = None) -> TestClient:
    reset_frame_ingest_runtime_state_for_tests()
    return TestClient(create_app(window_tracker_service=tracker or _FakeFrameTracker()))


class _FakeFrameTracker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.heartbeat_calls: list[dict[str, Any]] = []
        self.capture_source_v3: dict[str, Any] = {
            "schema_version": "PG_CAPTURE_SOURCE_V3",
            "state": "NO_SOURCE",
            "source_generation": 0,
        }

    def claim_external_source(
        self,
        session_id: str,
        *,
        source_id: str,
        sequence_id: str,
        source_type: str,
        selection_id: str,
        display_name: str,
        coordinate_space: str,
        expected_source_control: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del session_id, expected_source_control
        generation = int(self.capture_source_v3.get("source_generation", 0) or 0) + 1
        self.capture_source_v3 = {
            "schema_version": "PG_CAPTURE_SOURCE_V3",
            "state": "VALIDATING",
            "source_id": source_id,
            "sequence_id": sequence_id,
            "source_type": source_type,
            "selection_id": selection_id,
            "display_name": display_name,
            "coordinate_space": coordinate_space,
            "source_generation": generation,
            "source_lease_id": f"lease-{generation}",
        }
        return dict(self.capture_source_v3)

    def validate_external_source_lease(
        self,
        session_id: str,
        *,
        source_id: str,
        sequence_id: str,
        source_generation: int,
        source_lease_id: str,
    ) -> dict[str, Any]:
        del session_id
        if self.capture_source_v3.get("state") == "KILLED":
            return {
                "allowed": False,
                "status_code": 410,
                "reason_code": "SOURCE_KILLED",
            }
        allowed = bool(
            source_id == self.capture_source_v3.get("source_id")
            and sequence_id == self.capture_source_v3.get("sequence_id")
            and source_generation == self.capture_source_v3.get("source_generation")
            and source_lease_id == self.capture_source_v3.get("source_lease_id")
        )
        return {
            "allowed": allowed,
            "status_code": 200 if allowed else 409,
            "reason_code": "SOURCE_LEASE_CURRENT" if allowed else "SOURCE_SUPERSEDED",
        }

    def kill_external_source(
        self,
        session_id: str,
        *,
        reason: str,
        source_id: str = "",
        sequence_id: str = "",
        source_generation: int = 0,
        source_lease_id: str = "",
    ) -> dict[str, Any]:
        del session_id
        if source_id or sequence_id or source_generation or source_lease_id:
            validation = self.validate_external_source_lease(
                "",
                source_id=source_id,
                sequence_id=sequence_id,
                source_generation=source_generation,
                source_lease_id=source_lease_id,
            )
            if not bool(validation.get("allowed", False)):
                raise _FakeSourceLeaseError(
                    int(validation.get("status_code", 409) or 409),
                    str(validation.get("reason_code", "SOURCE_SUPERSEDED") or "SOURCE_SUPERSEDED"),
                )
        self.capture_source_v3.update(
            {
                "state": "KILLED",
                "source_generation": int(self.capture_source_v3.get("source_generation", 0) or 0) + 1,
                "source_lease_id": "",
                "reason_code": "SOURCE_KILLED",
                "message": reason,
            }
        )
        return dict(self.capture_source_v3)

    def heartbeat_external_source(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        validation = self.validate_external_source_lease(
            session_id,
            source_id=str(kwargs.get("source_id", "") or ""),
            sequence_id=str(kwargs.get("sequence_id", "") or ""),
            source_generation=int(kwargs.get("source_generation", 0) or 0),
            source_lease_id=str(kwargs.get("source_lease_id", "") or ""),
        )
        if not bool(validation.get("allowed", False)):
            raise _FakeSourceLeaseError(
                int(validation.get("status_code", 409) or 409),
                str(
                    validation.get("reason_code", "SOURCE_SUPERSEDED")
                    or "SOURCE_SUPERSEDED"
                ),
            )
        call = {"session_id": session_id, **kwargs}
        self.heartbeat_calls.append(call)
        self.capture_source_v3.update(
            {
                "state": "VALIDATING",
                "fresh": kwargs.get("source_render_fresh") is True,
                "decision_usable": False,
                "reason_code": "FRAME_PENDING"
                if kwargs.get("material_change_pending") is True
                else "FRAME_PROCESSING",
                "roi": {
                    "normalized_bbox": list(kwargs.get("roi_normalized", []))
                },
            }
        )
        return dict(self.capture_source_v3)

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
            "capture_source_v3": dict(self.capture_source_v3),
        }


class _FakeSourceLeaseError(RuntimeError):
    def __init__(self, status_code: int, reason_code: str) -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = f"{self.reason_code}: source lease is not current."
        super().__init__(self.message)

    def as_detail(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "message": self.message}


class _FailOnceFrameTracker(_FakeFrameTracker):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_ingest = True

    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.fail_next_ingest:
            self.fail_next_ingest = False
            raise ValueError("synthetic analysis failure")
        return super().ingest_external_frame(*args, **kwargs)


class _LateLeaseFailureTracker(_FakeFrameTracker):
    def __init__(self, *, status_code: int, reason_code: str) -> None:
        super().__init__()
        self.status_code = status_code
        self.reason_code = reason_code

    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise _FakeSourceLeaseError(self.status_code, self.reason_code)


class _BlockingFrameTracker(_FakeFrameTracker):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.entered.set()
        if not self.release.wait(timeout=5.0):
            raise RuntimeError("test did not release blocked frame ingest")
        return super().ingest_external_frame(*args, **kwargs)


def _png_bytes(width: int = 160, height: int = 120) -> bytes:
    image = Image.new("RGB", (width, height), (8, 12, 18))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _bmp_bytes(width: int = 160, height: int = 120) -> bytes:
    image = Image.new("RGB", (width, height), (8, 12, 18))
    output = BytesIO()
    image.save(output, format="BMP")
    return output.getvalue()


def _signature_headers(
    *,
    frame_bytes: bytes,
    session_id: str = "external-live",
    source_id: str = "edge-agent",
    sequence_id: str = "",
    frame_id: int = 1,
    capture_epoch_ms: int = 1_780_000_000_000,
    secret: str = "signing-secret",
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = str(int(round(time.time() * 1000.0)))
    resolved_nonce = nonce or uuid4().hex
    frame_sha256 = hashlib.sha256(frame_bytes).hexdigest()
    canonical = "\n".join(
        [
            "PG_FRAME_INGEST_V1",
            "POST",
            f"/v1/mobile/frame-ingest/sessions/{session_id}/frames",
            session_id,
            source_id,
            sequence_id,
            str(frame_id),
            str(capture_epoch_ms),
            frame_sha256,
            timestamp,
            resolved_nonce,
        ]
    )
    signature = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "X-PhoenixGuard-Signature-Alg": "HMAC-SHA256-V1",
        "X-PhoenixGuard-Timestamp": timestamp,
        "X-PhoenixGuard-Nonce": resolved_nonce,
        "X-PhoenixGuard-Signature": f"v1={signature}",
    }


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
    assert "browser_extension_capture" in payload["supported_sources"]
    assert payload["browser_extension_coordinate_space"] == "edge_tab_content_v1"
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
            "metadata_json": (
                '{"plane":"chart","source_type":"browser_extension_capture",'
                '"coordinate_space":"edge_tab_content_v1"}'
            ),
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["schema_version"] == "PG_FRAME_INGEST_ACCEPTED_V1"
    assert payload["accepted"] is True
    assert payload["session_id"] == "external-live"
    assert payload["external_frame_feed"]["source_id"] == "edge-agent"
    assert len(tracker.calls) == 1
    call = tracker.calls[0]
    assert call["size"] == (160, 120)
    assert call["symbol"] == "EURCAD"
    assert call["timeframe"] == "M5"
    assert call["frame_id"] == 42
    assert call["metadata"]["plane"] == "chart"
    assert call["metadata"]["source_type"] == "browser_extension_capture"
    assert call["metadata"]["coordinate_space"] == "edge_tab_content_v1"
    assert len(response.content) < 8_192


def test_leased_browser_roi_source_claim_accepts_only_current_generation(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    claim = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "edge-roi",
            "sequence_id": "edge-roi-seq-1",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "selection-1",
            "display_name": "TradingView chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    )
    assert claim.status_code == 201
    lease = claim.json()
    assert lease["source_generation"] == 1
    assert lease["source_lease_id"] == "lease-1"

    now_ms = int(time.time() * 1000)
    accepted = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-roi",
            "symbol": "CHF/JPY OTC",
            "timeframe": "M5",
            "sequence_id": "edge-roi-seq-1",
            "capture_epoch_ms": str(now_ms),
            "frame_id": "1",
            "source_generation": "1",
            "source_lease_id": "lease-1",
            "metadata_json": json.dumps(
                {
                    "source_type": "browser_tab_roi_capture",
                    "coordinate_space": "edge_tab_roi_v1",
                    "source_render_fresh": True,
                }
            ),
        },
    )
    assert accepted.status_code == 202
    assert tracker.calls[-1]["symbol"] == ""
    assert tracker.calls[-1]["timeframe"] == ""
    assert tracker.calls[-1]["metadata"]["identity_hint_policy"] == "visual_reproof_required"
    assert tracker.calls[-1]["metadata"]["identity_hints_ignored"] is True
    assert tracker.calls[-1]["metadata"]["source_generation"] == 1
    assert tracker.calls[-1]["metadata"]["source_lease_id"] == "lease-1"


def test_source_control_heartbeat_is_lease_fenced_and_does_not_run_analysis(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    claim = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "edge-roi",
            "sequence_id": "edge-roi-heartbeat",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "selection-heartbeat",
            "display_name": "Pocket Option chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    ).json()
    heartbeat_body = {
        "source_id": "edge-roi",
        "sequence_id": "edge-roi-heartbeat",
        "source_generation": claim["source_generation"],
        "source_lease_id": claim["source_lease_id"],
        "capture_epoch_ms": int(time.time() * 1000),
        "source_render_fresh": True,
        "material_change_pending": True,
        "roi_normalized": [0.0, 0.0, 1.0, 1.0],
        "roi_source_pixels": {
            "x": 0,
            "y": 0,
            "width": 1920,
            "height": 1080,
        },
        "source_surface_width": 1920,
        "source_surface_height": 1080,
        "transport_frame_age_ms": 10,
        "decoder_frame_age_ms": 20,
        "capture_health_reason": "capture_confirmed",
        "capture_status": "active",
        "presented_frames": 808,
        "media_time": 42.5,
    }

    accepted = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/heartbeat",
        headers={"Authorization": "Bearer secret-token"},
        json=heartbeat_body,
    )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["accepted"] is True
    assert body["source_control"]["state"] == "VALIDATING"
    assert body["source_control"]["decision_usable"] is False
    assert body["source_control"]["roi"]["normalized_bbox"] == [0.0, 0.0, 1.0, 1.0]
    assert tracker.calls == []
    assert len(tracker.heartbeat_calls) == 1

    superseded = dict(heartbeat_body)
    superseded["source_lease_id"] = "old-lease"
    rejected = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/heartbeat",
        headers={"Authorization": "Bearer secret-token"},
        json=superseded,
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["reason_code"] == "SOURCE_SUPERSEDED"
    assert len(tracker.heartbeat_calls) == 1

    malformed = dict(heartbeat_body)
    malformed["source_surface_width"] = []
    invalid = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/heartbeat",
        headers={"Authorization": "Bearer secret-token"},
        json=malformed,
    )
    assert invalid.status_code == 400
    assert len(tracker.heartbeat_calls) == 1


def test_source_heartbeat_remains_responsive_while_frame_analysis_is_blocked(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    reset_frame_ingest_runtime_state_for_tests()
    tracker = _BlockingFrameTracker()
    app = create_app(window_tracker_service=tracker)

    async def exercise() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            claim_response = await client.post(
                "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim",
                headers={"Authorization": "Bearer secret-token"},
                json={
                    "source_id": "edge-roi",
                    "sequence_id": "edge-blocked-analysis",
                    "source_type": "browser_tab_roi_capture",
                    "selection_id": "selection-blocked-analysis",
                    "display_name": "Pocket Option chart",
                    "coordinate_space": "edge_tab_roi_v1",
                },
            )
            assert claim_response.status_code == 201
            claim = claim_response.json()
            now_ms = int(time.time() * 1000)
            ingest_task = asyncio.create_task(
                client.post(
                    "/v1/mobile/frame-ingest/sessions/external-live/frames",
                    headers={"Authorization": "Bearer secret-token"},
                    files={"frame": ("chart.png", _png_bytes(), "image/png")},
                    data={
                        "source_id": "edge-roi",
                        "sequence_id": "edge-blocked-analysis",
                        "capture_epoch_ms": str(now_ms),
                        "frame_id": "1",
                        "source_generation": str(claim["source_generation"]),
                        "source_lease_id": claim["source_lease_id"],
                        "metadata_json": json.dumps(
                            {
                                "source_type": "browser_tab_roi_capture",
                                "coordinate_space": "edge_tab_roi_v1",
                                "source_render_fresh": True,
                            }
                        ),
                    },
                )
            )
            try:
                entered = await asyncio.to_thread(tracker.entered.wait, 3.0)
                assert entered is True
                heartbeat = await asyncio.wait_for(
                    client.post(
                        "/v1/mobile/frame-ingest/sessions/external-live/source-control/heartbeat",
                        headers={"Authorization": "Bearer secret-token"},
                        json={
                            "source_id": "edge-roi",
                            "sequence_id": "edge-blocked-analysis",
                            "source_generation": claim["source_generation"],
                            "source_lease_id": claim["source_lease_id"],
                            "capture_epoch_ms": int(time.time() * 1000),
                            "source_render_fresh": True,
                            "material_change_pending": True,
                            "roi_normalized": [0.0, 0.0, 1.0, 1.0],
                        },
                    ),
                    timeout=1.0,
                )
                assert heartbeat.status_code == 200
                assert heartbeat.json()["accepted"] is True
                assert len(tracker.heartbeat_calls) == 1
            finally:
                tracker.release.set()
            ingest_response = await asyncio.wait_for(ingest_task, timeout=3.0)
            assert ingest_response.status_code == 202

    asyncio.run(exercise())


def test_active_claim_rejects_frame_that_omits_lease_contract_before_tracker_call(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    claim = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "wgc-roi",
            "sequence_id": "wgc-sequence",
            "source_type": "windows_graphics_capture_roi",
            "selection_id": "selection-1",
            "display_name": "Selected chart",
            "coordinate_space": "wgc_hwnd_roi_v1",
        },
    )
    assert claim.status_code == 201

    omitted = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "wgc-roi",
            "sequence_id": "wgc-sequence",
            "capture_epoch_ms": str(int(time.time() * 1000)),
            "frame_id": "1",
            "metadata_json": "{}",
        },
    )

    assert omitted.status_code == 409
    assert omitted.json()["detail"]["reason_code"] == "SOURCE_SUPERSEDED"
    assert tracker.calls == []


def test_claim_retires_superseded_feed_capacity_before_first_leased_frame(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN", "1")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    first = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "legacy-source",
            "sequence_id": "legacy-sequence",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "1",
        },
    )
    assert first.status_code == 202
    claim = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "wgc-source",
            "sequence_id": "wgc-sequence",
            "source_type": "windows_graphics_capture_roi",
            "selection_id": "selection-2",
            "display_name": "Chart",
            "coordinate_space": "wgc_hwnd_roi_v1",
        },
    ).json()

    replacement = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "wgc-source",
            "sequence_id": "wgc-sequence",
            "capture_epoch_ms": "1780000001000",
            "frame_id": "1",
            "source_generation": str(claim["source_generation"]),
            "source_lease_id": claim["source_lease_id"],
            "metadata_json": (
                '{"source_type":"windows_graphics_capture_roi",'
                '"coordinate_space":"wgc_hwnd_roi_v1"}'
            ),
        },
    )

    assert replacement.status_code == 202


def test_reclaim_same_source_allows_new_generation_without_old_cadence_poison(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    claim_path = "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim"
    claim_payload = {
        "source_id": "wgc-source",
        "sequence_id": "wgc-sequence-1",
        "source_type": "windows_graphics_capture_roi",
        "selection_id": "selection-1",
        "display_name": "Chart",
        "coordinate_space": "wgc_hwnd_roi_v1",
    }
    first_claim = client.post(
        claim_path,
        headers={"Authorization": "Bearer secret-token"},
        json=claim_payload,
    ).json()
    common_metadata = (
        '{"source_type":"windows_graphics_capture_roi",'
        '"coordinate_space":"wgc_hwnd_roi_v1"}'
    )
    first = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "wgc-source",
            "sequence_id": "wgc-sequence-1",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "1",
            "source_generation": str(first_claim["source_generation"]),
            "source_lease_id": first_claim["source_lease_id"],
            "metadata_json": common_metadata,
        },
    )
    assert first.status_code == 202
    second_claim = client.post(
        claim_path,
        headers={"Authorization": "Bearer secret-token"},
        json={**claim_payload, "sequence_id": "wgc-sequence-2", "selection_id": "selection-2"},
    ).json()
    assert second_claim["source_generation"] == first_claim["source_generation"] + 1

    second = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "wgc-source",
            "sequence_id": "wgc-sequence-2",
            "capture_epoch_ms": "1780000001000",
            "frame_id": "1",
            "source_generation": str(second_claim["source_generation"]),
            "source_lease_id": second_claim["source_lease_id"],
            "metadata_json": common_metadata,
        },
    )

    assert second.status_code == 202


def test_superseded_source_cannot_upload_or_kill_new_owner(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    claim_path = "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim"
    first = client.post(
        claim_path,
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "edge-old",
            "sequence_id": "old-sequence",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "old-selection",
            "display_name": "Old chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    ).json()
    second = client.post(
        claim_path,
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "edge-new",
            "sequence_id": "new-sequence",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "new-selection",
            "display_name": "New chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    ).json()
    assert second["source_generation"] == first["source_generation"] + 1

    now_ms = int(time.time() * 1000)
    old_frame = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-old",
            "sequence_id": "old-sequence",
            "capture_epoch_ms": str(now_ms),
            "frame_id": "1",
            "source_generation": str(first["source_generation"]),
            "source_lease_id": first["source_lease_id"],
            "metadata_json": '{"source_type":"browser_tab_roi_capture","coordinate_space":"edge_tab_roi_v1"}',
        },
    )
    assert old_frame.status_code == 409
    assert old_frame.json()["detail"]["reason_code"] == "SOURCE_SUPERSEDED"

    old_kill = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/kill",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "edge-old",
            "sequence_id": "old-sequence",
            "source_generation": first["source_generation"],
            "source_lease_id": first["source_lease_id"],
            "reason": "Old owner tried to stop capture.",
        },
    )
    assert old_kill.status_code == 409
    assert tracker.capture_source_v3["source_id"] == "edge-new"
    assert tracker.capture_source_v3["state"] == "VALIDATING"


def test_current_source_kill_revokes_lease_with_gone_response(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    claim = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "edge-roi",
            "sequence_id": "edge-roi-seq",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "selection",
            "display_name": "Chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    ).json()
    kill = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/kill",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source_id": "edge-roi",
            "sequence_id": "edge-roi-seq",
            "source_generation": claim["source_generation"],
            "source_lease_id": claim["source_lease_id"],
            "reason": "Operator kill switch.",
        },
    )
    assert kill.status_code == 200
    assert kill.json()["source_control"]["state"] == "KILLED"
    assert "source_lease_id" not in kill.json()["source_control"]

    now_ms = int(time.time() * 1000)
    stopped_frame = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-roi",
            "sequence_id": "edge-roi-seq",
            "capture_epoch_ms": str(now_ms),
            "frame_id": "1",
            "source_generation": str(claim["source_generation"]),
            "source_lease_id": claim["source_lease_id"],
            "metadata_json": '{"source_type":"browser_tab_roi_capture","coordinate_space":"edge_tab_roi_v1"}',
        },
    )
    assert stopped_frame.status_code == 410
    assert stopped_frame.json()["detail"]["reason_code"] == "SOURCE_KILLED"


def test_frame_ingest_rejects_wrong_browser_extension_coordinate_space(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    client = _client()

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-tab",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "1",
            "metadata_json": (
                '{"source_type":"browser_extension_capture",'
                '"coordinate_space":"desktop_pixels_v1"}'
            ),
        },
    )

    assert response.status_code == 400
    assert "edge_tab_content_v1" in response.json()["detail"]


def test_frame_ingest_overwrites_client_supplied_security_metadata(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _FakeFrameTracker()
    client = _client(tracker)

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-tab",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "1",
            "metadata_json": (
                '{"source_type":"browser_extension_capture",'
                '"coordinate_space":"edge_tab_content_v1",'
                '"feed_token_name":"spoofed","frame_bytes":1}'
            ),
        },
    )

    assert response.status_code == 202
    metadata = tracker.calls[0]["metadata"]
    assert metadata["feed_token_name"] == "global"
    assert metadata["frame_bytes"] == len(_png_bytes())


def test_frame_ingest_requires_hmac_signature_when_enabled(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET", "signing-secret")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE", "1")
    client = _client()

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )

    assert response.status_code == 401
    assert "signature headers" in response.json()["detail"]


def test_frame_ingest_accepts_valid_signature_and_writes_audit(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET", "signing-secret")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE", "1")
    audit_log = tmp_path / "security_audit.jsonl"
    monkeypatch.setenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", str(audit_log))
    frame_bytes = _png_bytes()
    tracker = _FakeFrameTracker()
    client = _client(tracker)

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token", **_signature_headers(frame_bytes=frame_bytes)},
        files={"frame": ("chart.png", frame_bytes, "image/png")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )

    assert response.status_code == 202
    entries = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["event"] == "frame_ingest_accepted"
    assert entries[-1]["frame_sha256"] == hashlib.sha256(frame_bytes).hexdigest()


def test_frame_ingest_rejects_signature_nonce_replay(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET", "signing-secret")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE", "1")
    frame_bytes = _png_bytes()
    headers = {"Authorization": "Bearer secret-token", **_signature_headers(frame_bytes=frame_bytes, nonce="replay-nonce")}
    client = _client()

    first = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers=headers,
        files={"frame": ("chart.png", frame_bytes, "image/png")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )
    second = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers=headers,
        files={"frame": ("chart.png", frame_bytes, "image/png")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert "nonce" in second.json()["detail"]


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
    assert second.headers["Retry-After"] == "60"


def test_frame_ingest_sequence_rollover_cannot_bypass_feed_cadence(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN", "1")
    client = _client()
    common = {
        "source_id": "edge-background-tab-v3",
        "metadata_json": (
            '{"source_type":"browser_extension_capture",'
            '"coordinate_space":"edge_tab_content_v1"}'
        ),
    }

    first = client.post(
        "/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            **common,
            "sequence_id": "edge-tab-1-first",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "1",
        },
    )
    rollover = client.post(
        "/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            **common,
            "sequence_id": "edge-tab-1-second",
            "capture_epoch_ms": "1780000001000",
            "frame_id": "1",
        },
    )

    assert first.status_code == 202
    assert rollover.status_code == 429
    assert rollover.headers["Retry-After"] == "60"


def test_frame_ingest_tracker_failure_rolls_back_atomic_feed_reservation(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    tracker = _FailOnceFrameTracker()
    client = _client(tracker)

    def send_frame() -> Any:
        return client.post(
            "/v1/mobile/frame-ingest/sessions/external-live/frames",
            headers={"Authorization": "Bearer secret-token"},
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-agent",
                "sequence_id": "sequence-1",
                "capture_epoch_ms": "1780000000000",
                "frame_id": "1",
            },
        )

    failed = send_frame()
    retried = send_frame()

    assert failed.status_code == 400
    assert retried.status_code == 202
    assert len(tracker.calls) == 1


def test_frame_ingest_reservation_blocks_concurrent_preflight_race(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    reset_frame_ingest_runtime_state_for_tests()
    tracker = _BlockingFrameTracker()
    app = create_app(window_tracker_service=tracker)
    first_client = TestClient(app)
    second_client = TestClient(app)
    first_result: dict[str, Any] = {}

    def send_first() -> None:
        first_result["response"] = first_client.post(
            "/v1/mobile/frame-ingest/sessions/external-live/frames",
            headers={"Authorization": "Bearer secret-token"},
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-agent",
                "sequence_id": "sequence-1",
                "capture_epoch_ms": "1780000000000",
                "frame_id": "1",
            },
        )

    worker = threading.Thread(target=send_first, daemon=True)
    worker.start()
    assert tracker.entered.wait(timeout=3.0)
    second = second_client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-agent",
            "sequence_id": "sequence-1",
            "capture_epoch_ms": "1780000001000",
            "frame_id": "2",
        },
    )
    tracker.release.set()
    worker.join(timeout=5.0)

    assert second.status_code == 429
    assert not worker.is_alive()
    assert first_result["response"].status_code == 202


def test_frame_ingest_analysis_does_not_block_status_requests(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    reset_frame_ingest_runtime_state_for_tests()
    tracker = _BlockingFrameTracker()
    app = create_app(window_tracker_service=tracker)

    async def exercise() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            ingest_task = asyncio.create_task(
                client.post(
                    "/v1/mobile/frame-ingest/sessions/external-live/frames",
                    headers={"Authorization": "Bearer secret-token"},
                    files={"frame": ("chart.png", _png_bytes(), "image/png")},
                    data={
                        "source_id": "edge-agent",
                        "sequence_id": "sequence-1",
                        "capture_epoch_ms": "1780000000000",
                        "frame_id": "1",
                    },
                )
            )
            try:
                entered = await asyncio.to_thread(tracker.entered.wait, 3.0)
                assert entered is True
                status_response = await asyncio.wait_for(
                    client.get(
                        "/v1/mobile/frame-ingest/sessions/external-live/status",
                        headers={"Authorization": "Bearer secret-token"},
                    ),
                    timeout=1.0,
                )
                assert status_response.status_code == 200
            finally:
                tracker.release.set()
            ingest_response = await asyncio.wait_for(ingest_task, timeout=3.0)
            assert ingest_response.status_code == 202

    asyncio.run(exercise())


def test_frame_ingest_preserves_late_tracker_source_lease_hard_stop(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _LateLeaseFailureTracker(status_code=409, reason_code="SOURCE_SUPERSEDED")
    client = _client(tracker)

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-agent",
            "sequence_id": "sequence-1",
            "capture_epoch_ms": "1780000000000",
            "frame_id": "1",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["reason_code"] == "SOURCE_SUPERSEDED"


def test_frame_ingest_rejected_image_does_not_poison_feed_interval(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "60")
    client = _client()

    bad = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.txt", b"not an image", "text/plain")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )
    good = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )

    assert bad.status_code == 400
    assert good.status_code == 202


def test_frame_ingest_rejects_unsupported_image_format(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    client = _client()

    response = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/frames",
        headers={"Authorization": "Bearer secret-token"},
        files={"frame": ("chart.bmp", _bmp_bytes(), "image/bmp")},
        data={"source_id": "edge-agent", "capture_epoch_ms": "1780000000000", "frame_id": "1"},
    )

    assert response.status_code == 400
    assert "format is not allowed" in response.json()["detail"]


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
