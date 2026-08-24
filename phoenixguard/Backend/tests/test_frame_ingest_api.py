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

from phoenixguard.mobile_api import frame_ingest as frame_ingest_module
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.frame_ingest import reset_frame_ingest_runtime_state_for_tests


def _client(tracker: _FakeFrameTracker | None = None) -> Any:
    reset_frame_ingest_runtime_state_for_tests()
    return TestClient(create_app(window_tracker_service=tracker or _FakeFrameTracker()))


def _wait_until(predicate: Any, *, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bool(predicate()):
            return True
        time.sleep(0.01)
    return bool(predicate())


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


class _HeartbeatReclaimFrameTracker(_FakeFrameTracker):
    def heartbeat_external_source(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        super().heartbeat_external_source(session_id, **kwargs)
        self.capture_source_v3 = {
            "schema_version": "PG_CAPTURE_SOURCE_V3",
            "state_revision": 2,
            "state": "NO_SOURCE",
            "source_id": "",
            "source_generation": 0,
            "source_type": "",
            "coordinate_space": "",
            "selection_id": "",
            "sequence_id": "",
            "reason_code": "SOURCE_RECLAIM_REQUIRED",
        }
        return dict(self.capture_source_v3)


def test_healthy_heartbeat_returns_reclaim_conflict_after_failed_lease_release(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    tracker = _HeartbeatReclaimFrameTracker()
    client = _client(tracker)
    headers = {"Authorization": "Bearer secret-token"}
    claim_response = client.post(
        "/v1/mobile/frame-ingest/sessions/reclaim-live/source-control/claim",
        headers=headers,
        json={
            "source_id": "edge-roi",
            "sequence_id": "reclaim-sequence",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "reclaim-selection",
            "display_name": "Reclaim chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    )
    assert claim_response.status_code == 201
    claim = claim_response.json()

    heartbeat = client.post(
        "/v1/mobile/frame-ingest/sessions/reclaim-live/source-control/heartbeat",
        headers=headers,
        json={
            "source_id": "edge-roi",
            "sequence_id": "reclaim-sequence",
            "source_generation": claim["source_generation"],
            "source_lease_id": claim["source_lease_id"],
            "capture_epoch_ms": int(time.time() * 1000),
            "source_render_fresh": True,
            "material_change_pending": False,
            "capture_status": "active",
            "decoder_frame_age_ms": 10,
        },
    )

    assert heartbeat.status_code == 409
    assert heartbeat.json()["detail"]["reason_code"] == (
        "SOURCE_RECLAIM_REQUIRED"
    )
    assert tracker.capture_source_v3["state"] == "NO_SOURCE"


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


class _ProcessingFailOnceFrameTracker(_FakeFrameTracker):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_ingest = True
        self.attempted_frame_ids: list[int] = []

    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        frame_id = int(kwargs.get("frame_id", 0) or 0)
        self.attempted_frame_ids.append(frame_id)
        self.capture_source_v3["state"] = "VALIDATING"
        self.capture_source_v3["stream"] = {
            **dict(self.capture_source_v3.get("stream", {})),
            "processing": True,
            "processing_frame_id": frame_id,
        }
        if self.fail_next_ingest:
            self.fail_next_ingest = False
            raise RuntimeError("synthetic secret=do-not-publish")
        result = super().ingest_external_frame(*args, **kwargs)
        stream = dict(self.capture_source_v3.get("stream", {}))
        stream.update(
            {
                "processing": False,
                "processing_frame_id": 0,
                "last_frame_id": frame_id,
            }
        )
        self.capture_source_v3.update({"state": "LIVE", "stream": stream})
        return result

    def fail_external_frame_analysis(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del session_id
        frame_id = int(kwargs.get("frame_id", 0) or 0)
        validation = self.validate_external_source_lease(
            "",
            source_id=str(kwargs.get("source_id", "") or ""),
            sequence_id=str(kwargs.get("sequence_id", "") or ""),
            source_generation=int(kwargs.get("source_generation", 0) or 0),
            source_lease_id=str(kwargs.get("source_lease_id", "") or ""),
        )
        stream = dict(self.capture_source_v3.get("stream", {}))
        if (
            not bool(validation.get("allowed", False))
            or stream.get("processing") is not True
            or int(stream.get("processing_frame_id", 0) or 0) != frame_id
        ):
            return {"cleared": False, "reason_code": "SOURCE_SUPERSEDED"}
        stream.update(
            {
                "processing": False,
                "processing_frame_id": 0,
                "last_failed_frame_id": frame_id,
                "last_failure_reason_code": str(
                    kwargs.get("reason_code", "") or ""
                ),
                "last_failure_error_type": str(
                    kwargs.get("error_type", "") or ""
                ),
            }
        )
        self.capture_source_v3.update(
            {
                "state": "ERROR",
                "reason_code": "FRAME_ANALYSIS_FAILED",
                "stream": stream,
            }
        )
        return {"cleared": True, "reason_code": "FRAME_ANALYSIS_FAILED"}

    def get_external_frame_transport_status(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        stream = dict(self.capture_source_v3.get("stream", {}))
        return {
            "schema_version": "PG_EXTERNAL_FRAME_TRANSPORT_STATUS_V1",
            "session_id": session_id,
            "status": str(self.capture_source_v3.get("state", "NO_SOURCE")),
            "source_state": str(
                self.capture_source_v3.get("state", "NO_SOURCE")
            ),
            "reason_code": str(self.capture_source_v3.get("reason_code", "")),
            "source_id": str(self.capture_source_v3.get("source_id", "")),
            "source_type": str(self.capture_source_v3.get("source_type", "")),
            "sequence_id": str(self.capture_source_v3.get("sequence_id", "")),
            "source_generation": int(
                self.capture_source_v3.get("source_generation", 0) or 0
            ),
            "coordinate_space": str(
                self.capture_source_v3.get("coordinate_space", "")
            ),
            "last_frame_id": int(stream.get("last_frame_id", 0) or 0),
            "stream": stream,
        }


class _StudyRejectOnceFrameTracker(_FakeFrameTracker):
    def __init__(self) -> None:
        super().__init__()
        self.reject_next_study = True
        self.attempted_frame_ids: list[int] = []

    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        frame_id = int(kwargs.get("frame_id", 0) or 0)
        self.attempted_frame_ids.append(frame_id)
        if self.reject_next_study:
            self.reject_next_study = False
            return {
                "frame_ingest": {
                    "accepted": False,
                    "failure_reason_code": "FRAME_STUDY_NOT_ACCEPTED",
                }
            }
        return super().ingest_external_frame(*args, **kwargs)


class _LateLeaseFailureTracker(_FakeFrameTracker):
    def __init__(self, *, status_code: int, reason_code: str) -> None:
        super().__init__()
        self.status_code = status_code
        self.reason_code = reason_code

    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise _FakeSourceLeaseError(self.status_code, self.reason_code)


class _WorkerLeaseValidationFailureTracker(_FakeFrameTracker):
    def __init__(self) -> None:
        super().__init__()
        self.validation_calls = 0

    def validate_external_source_lease(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.validation_calls += 1
        if self.validation_calls >= 2:
            raise RuntimeError("secret lease backend failure")
        return super().validate_external_source_lease(*args, **kwargs)


class _BlockingFrameTracker(_FakeFrameTracker):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.entered.set()
        if not self.release.wait(timeout=10.0):
            raise RuntimeError("test did not release blocked frame ingest")
        return super().ingest_external_frame(*args, **kwargs)


class _RejectOnceClaimBlockingTracker(_BlockingFrameTracker):
    def __init__(self) -> None:
        super().__init__()
        self.reject_next_claim = False

    def claim_external_source(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.reject_next_claim:
            self.reject_next_claim = False
            raise _FakeSourceLeaseError(409, "SOURCE_SUPERSEDED")
        return super().claim_external_source(*args, **kwargs)


class _CommitFencedClaimBlockingTracker(_RejectOnceClaimBlockingTracker):
    def ingest_external_frame(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.entered.set()
        if not self.release.wait(timeout=10.0):
            raise RuntimeError("test did not release blocked frame ingest")
        metadata = dict(kwargs.get("metadata") or {})
        if metadata.get("coordinate_space") in {"edge_tab_roi_v1", "wgc_hwnd_roi_v1"}:
            validation = self.validate_external_source_lease(
                str(args[0]),
                source_id=str(kwargs.get("source_id", "") or ""),
                sequence_id=str(kwargs.get("sequence_id", "") or ""),
                source_generation=int(metadata.get("source_generation", 0) or 0),
                source_lease_id=str(metadata.get("source_lease_id", "") or ""),
            )
            if not bool(validation.get("allowed", False)):
                raise _FakeSourceLeaseError(
                    int(validation.get("status_code", 409) or 409),
                    str(validation.get("reason_code", "SOURCE_SUPERSEDED") or "SOURCE_SUPERSEDED"),
                )
        return _FakeFrameTracker.ingest_external_frame(self, *args, **kwargs)


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
    assert payload["retry_after_ms"] >= 1_000
    assert payload["analysis_mailbox"]["max_active_per_session"] == 1
    assert payload["analysis_mailbox"]["max_pending_per_session"] == 1
    assert payload["analysis_mailbox"]["latest_pending_replaces"] is True


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
    assert payload["analysis_busy"] is True
    assert payload["active_frame_id"] == 42
    assert payload["pending_frame_id"] is None
    assert payload["replaced_frame_count"] == 0
    assert payload["retry_after_ms"] > 0
    assert _wait_until(lambda: len(tracker.calls) == 1)
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
    assert _wait_until(lambda: len(tracker.calls) == 1)
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
    assert _wait_until(lambda: len(tracker.calls) == 1)
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
    accepted_entries = [entry for entry in entries if entry["event"] == "frame_ingest_accepted"]
    assert accepted_entries[-1]["frame_sha256"] == hashlib.sha256(frame_bytes).hexdigest()


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


def test_frame_ingest_async_failure_is_audited_without_secret_or_exception_message(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    audit_log = tmp_path / "security_audit.jsonl"
    monkeypatch.setenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", str(audit_log))
    tracker = _FailOnceFrameTracker()
    client = _client(tracker)

    accepted = client.post(
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

    assert accepted.status_code == 202
    assert _wait_until(
        lambda: audit_log.exists()
        and "frame_analysis_failed" in audit_log.read_text(encoding="utf-8"),
    )
    audit_text = audit_log.read_text(encoding="utf-8")
    assert "secret-token" not in audit_text
    assert "synthetic analysis failure" not in audit_text
    assert '"error_type":"ValueError"' in audit_text


def test_async_failure_clears_exact_processing_frame_and_allows_retry(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "1")
    audit_log = tmp_path / "security_audit.jsonl"
    monkeypatch.setenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", str(audit_log))
    tracker = _ProcessingFailOnceFrameTracker()
    client = _client(tracker)
    headers = {"Authorization": "Bearer secret-token"}
    claim_response = client.post(
        "/v1/mobile/frame-ingest/sessions/failure-retry-live/source-control/claim",
        headers=headers,
        json={
            "source_id": "edge-roi",
            "sequence_id": "failure-retry-sequence",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "failure-retry-selection",
            "display_name": "Failure retry chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    )
    assert claim_response.status_code == 201
    claim = claim_response.json()
    base_epoch_ms = int(time.time() * 1000)
    metadata_json = json.dumps(
        {
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_render_fresh": True,
        }
    )

    def send_frame(frame_id: int) -> Any:
        return client.post(
            "/v1/mobile/frame-ingest/sessions/failure-retry-live/frames",
            headers=headers,
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-roi",
                "sequence_id": "failure-retry-sequence",
                "capture_epoch_ms": str(base_epoch_ms + frame_id * 2_000),
                "frame_id": str(frame_id),
                "source_generation": str(claim["source_generation"]),
                "source_lease_id": str(claim["source_lease_id"]),
                "metadata_json": metadata_json,
            },
        )

    def status_payload() -> dict[str, Any]:
        response = client.get(
            "/v1/mobile/frame-ingest/sessions/failure-retry-live/status",
            headers=headers,
        )
        assert response.status_code == 200
        return response.json()

    first = send_frame(1)
    assert first.status_code == 202
    assert _wait_until(lambda: status_payload()["last_failed_frame_id"] == 1)
    failed = status_payload()
    assert failed["analysis_busy"] is False
    assert failed["last_failed_epoch_ms"] > 0
    assert failed["last_failure_reason_code"] == "FRAME_ANALYSIS_FAILED"
    assert failed["last_failure_error_type"] == "RuntimeError"
    assert failed["transport_state"]["source_state"] == "ERROR"
    assert failed["transport_state"]["stream"]["processing"] is False
    assert failed["transport_state"]["stream"]["processing_frame_id"] == 0
    assert "do-not-publish" not in json.dumps(failed)

    time.sleep(1.05)
    second = send_frame(2)
    assert second.status_code == 202
    assert second.json()["last_failed_frame_id"] == 1
    assert _wait_until(lambda: status_payload()["last_completed_frame_id"] == 2)
    recovered = status_payload()
    assert recovered["analysis_busy"] is False
    assert recovered["last_completed_epoch_ms"] >= failed["last_failed_epoch_ms"]
    assert recovered["transport_state"]["stream"]["processing"] is False
    assert tracker.attempted_frame_ids == [1, 2]
    assert [call["frame_id"] for call in tracker.calls] == [2]
    audit_text = audit_log.read_text(encoding="utf-8")
    assert "do-not-publish" not in audit_text


def test_transient_study_rejection_retries_same_frame_without_new_upload(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "1")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_STUDY_TRANSIENT_RETRY_DELAY_MS", "1")
    audit_log = tmp_path / "security_audit.jsonl"
    monkeypatch.setenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", str(audit_log))
    tracker = _StudyRejectOnceFrameTracker()
    client = _client(tracker)
    headers = {"Authorization": "Bearer secret-token"}
    claim_response = client.post(
        "/v1/mobile/frame-ingest/sessions/study-retry-live/source-control/claim",
        headers=headers,
        json={
            "source_id": "edge-roi",
            "sequence_id": "study-retry-sequence",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "study-retry-selection",
            "display_name": "Study retry chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    )
    assert claim_response.status_code == 201
    claim = claim_response.json()
    accepted = client.post(
        "/v1/mobile/frame-ingest/sessions/study-retry-live/frames",
        headers=headers,
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-roi",
            "sequence_id": "study-retry-sequence",
            "capture_epoch_ms": str(int(time.time() * 1000)),
            "frame_id": "1",
            "source_generation": str(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
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

    def status_payload() -> dict[str, Any]:
        response = client.get(
            "/v1/mobile/frame-ingest/sessions/study-retry-live/status",
            headers=headers,
        )
        assert response.status_code == 200
        return response.json()

    assert _wait_until(lambda: status_payload()["last_completed_frame_id"] == 1)
    recovered = status_payload()
    assert recovered["analysis_busy"] is False
    assert recovered["last_failed_frame_id"] is None
    assert recovered["last_failure_reason_code"] == ""
    assert tracker.attempted_frame_ids == [1, 1]
    assert [call["frame_id"] for call in tracker.calls] == [1]
    assert "frame_analysis_retry_scheduled" in audit_log.read_text(encoding="utf-8")


def test_worker_lease_validation_exception_is_a_visible_bounded_failure(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    audit_log = tmp_path / "security_audit.jsonl"
    monkeypatch.setenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", str(audit_log))
    tracker = _WorkerLeaseValidationFailureTracker()
    client = _client(tracker)
    headers = {"Authorization": "Bearer secret-token"}
    claim_response = client.post(
        "/v1/mobile/frame-ingest/sessions/lease-validation-live/source-control/claim",
        headers=headers,
        json={
            "source_id": "edge-roi",
            "sequence_id": "lease-validation-sequence",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "lease-validation-selection",
            "display_name": "Lease validation chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    )
    assert claim_response.status_code == 201
    claim = claim_response.json()
    accepted = client.post(
        "/v1/mobile/frame-ingest/sessions/lease-validation-live/frames",
        headers=headers,
        files={"frame": ("chart.png", _png_bytes(), "image/png")},
        data={
            "source_id": "edge-roi",
            "sequence_id": "lease-validation-sequence",
            "capture_epoch_ms": str(int(time.time() * 1000)),
            "frame_id": "1",
            "source_generation": str(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
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

    def status_payload() -> dict[str, Any]:
        response = client.get(
            "/v1/mobile/frame-ingest/sessions/lease-validation-live/status",
            headers=headers,
        )
        assert response.status_code == 200
        return response.json()

    assert _wait_until(lambda: status_payload()["last_failed_frame_id"] == 1)
    failed = status_payload()
    assert failed["analysis_busy"] is False
    assert failed["last_failure_reason_code"] == "LEASE_REVALIDATION_FAILED"
    assert failed["last_failure_error_type"] == "RuntimeError"
    assert tracker.calls == []
    assert "secret lease backend failure" not in json.dumps(failed)
    assert "secret lease backend failure" not in audit_log.read_text(encoding="utf-8")


def test_frame_ingest_mailbox_returns_quickly_and_replaces_only_pending_frame(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "1")
    tracker = _BlockingFrameTracker()
    client = _client(tracker)
    tracked_images: dict[str, Image.Image] = {}

    async def read_tracked_image(frame: Any) -> tuple[Image.Image, str, int]:
        data = await frame.read()
        filename = str(frame.filename or "chart-0.png")
        frame_number = int(filename.removeprefix("chart-").removesuffix(".png"))
        image = Image.new("RGB", (160, 120), (frame_number, 12, 18))
        tracked_images[filename] = image
        return image, hashlib.sha256(data).hexdigest(), len(data)

    monkeypatch.setattr(frame_ingest_module, "_read_image_upload", read_tracked_image)

    def send_frame(frame_id: int) -> tuple[Any, float]:
        started = time.monotonic()
        response = client.post(
            "/v1/mobile/frame-ingest/sessions/external-live/frames",
            headers={"Authorization": "Bearer secret-token"},
            files={"frame": (f"chart-{frame_id}.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-agent",
                "sequence_id": "sequence-1",
                "capture_epoch_ms": str(1_780_000_000_000 + frame_id * 2_000),
                "frame_id": str(frame_id),
            },
        )
        return response, time.monotonic() - started

    try:
        first, first_elapsed = send_frame(1)
        assert first.status_code == 202
        assert first_elapsed < 2.0
        assert tracker.entered.wait(timeout=3.0)
        assert first.json()["analysis_disposition"] == "active"

        time.sleep(1.05)
        second, second_elapsed = send_frame(2)
        assert second.status_code == 202
        assert second_elapsed < 2.0
        assert second.json()["analysis_disposition"] == "pending"
        assert second.json()["pending_frame_id"] == 2
        assert tracked_images["chart-2.png"].getchannel("R").getpixel((0, 0)) == 2

        time.sleep(1.05)
        third, third_elapsed = send_frame(3)
        assert third.status_code == 202
        assert third_elapsed < 2.0
        assert third.json()["analysis_disposition"] == "replaced_pending"
        assert third.json()["active_frame_id"] == 1
        assert third.json()["pending_frame_id"] == 3
        assert third.json()["replaced_frame_count"] == 1
        try:
            tracked_images["chart-2.png"].getpixel((0, 0))
            replaced_image_closed = False
        except ValueError:
            replaced_image_closed = True
        assert replaced_image_closed is True

        status_response = client.get(
            "/v1/mobile/frame-ingest/sessions/external-live/status",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert status_response.status_code == 200
        assert status_response.json()["analysis_busy"] is True
        assert status_response.json()["active_frame_id"] == 1
        assert status_response.json()["pending_frame_id"] == 3
    finally:
        tracker.release.set()

    assert _wait_until(lambda: len(tracker.calls) == 2)
    assert [call["frame_id"] for call in tracker.calls] == [1, 3]


def test_idle_analysis_mailboxes_are_evicted_at_configured_bound(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MAX_MAILBOX_STATES", "2")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_TOTAL", "8")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN", "8")
    tracker = _FakeFrameTracker()
    client = _client(tracker)
    headers = {"Authorization": "Bearer secret-token"}
    base_epoch_ms = int(time.time() * 1000)

    for index in range(3):
        session_id = f"bounded-mailbox-{index}"
        response = client.post(
            f"/v1/mobile/frame-ingest/sessions/{session_id}/frames",
            headers=headers,
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-agent",
                "sequence_id": f"sequence-{index}",
                "capture_epoch_ms": str(base_epoch_ms + index),
                "frame_id": "1",
            },
        )
        assert response.status_code == 202
        assert _wait_until(
            lambda session_id=session_id: client.get(
                f"/v1/mobile/frame-ingest/sessions/{session_id}/status",
                headers=headers,
            ).json()["analysis_busy"]
            is False
        )

    config = client.get("/v1/mobile/frame-ingest/config").json()
    capacity = config["analysis_mailbox"]
    assert capacity["max_mailbox_states"] == 2
    assert capacity["mailbox_state_count"] == 2


def test_source_replacement_discards_old_generation_pending_frame(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "1")
    tracker = _CommitFencedClaimBlockingTracker()
    client = _client(tracker)
    claim_path = "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim"
    frame_path = "/v1/mobile/frame-ingest/sessions/external-live/frames"
    headers = {"Authorization": "Bearer secret-token"}
    metadata_json = json.dumps(
        {
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_render_fresh": True,
        }
    )
    first_claim = client.post(
        claim_path,
        headers=headers,
        json={
            "source_id": "edge-roi",
            "sequence_id": "generation-one",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "selection-one",
            "display_name": "Chart one",
            "coordinate_space": "edge_tab_roi_v1",
        },
    ).json()
    base_epoch_ms = int(time.time() * 1000)

    def send_frame(*, frame_id: int, sequence_id: str, claim: Mapping[str, Any]) -> Any:
        return client.post(
            frame_path,
            headers=headers,
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-roi",
                "sequence_id": sequence_id,
                "capture_epoch_ms": str(base_epoch_ms + frame_id * 2_000),
                "frame_id": str(frame_id),
                "source_generation": str(claim["source_generation"]),
                "source_lease_id": str(claim["source_lease_id"]),
                "metadata_json": metadata_json,
            },
        )

    try:
        first = send_frame(frame_id=1, sequence_id="generation-one", claim=first_claim)
        assert first.status_code == 202
        assert tracker.entered.wait(timeout=3.0)
        time.sleep(1.05)
        pending = send_frame(frame_id=2, sequence_id="generation-one", claim=first_claim)
        assert pending.status_code == 202
        assert pending.json()["pending_frame_id"] == 2

        second_claim_payload = {
            "source_id": "edge-roi",
            "sequence_id": "generation-two",
            "source_type": "browser_tab_roi_capture",
            "selection_id": "selection-two",
            "display_name": "Chart two",
            "coordinate_space": "edge_tab_roi_v1",
        }
        tracker.reject_next_claim = True
        rejected_claim = client.post(
            claim_path,
            headers=headers,
            json=second_claim_payload,
        )
        assert rejected_claim.status_code == 409
        mailbox_after_rejection = client.get(
            "/v1/mobile/frame-ingest/sessions/external-live/status",
            headers=headers,
        ).json()
        assert mailbox_after_rejection["pending_frame_id"] == 2

        second_claim_response = client.post(
            claim_path,
            headers=headers,
            json=second_claim_payload,
        )
        assert second_claim_response.status_code == 201
        second_claim = second_claim_response.json()
        assert second_claim["source_generation"] == first_claim["source_generation"] + 1
        mailbox_status = client.get(
            "/v1/mobile/frame-ingest/sessions/external-live/status",
            headers=headers,
        ).json()
        assert mailbox_status["pending_frame_id"] is None
        current = send_frame(
            frame_id=1,
            sequence_id="generation-two",
            claim=second_claim,
        )
        assert current.status_code == 202
        assert current.json()["active_frame_id"] == 1
        assert current.json()["active_superseded"] is True
        assert current.json()["pending_frame_id"] == 1
        superseded_status = client.get(
            "/v1/mobile/frame-ingest/sessions/external-live/status",
            headers=headers,
        ).json()
        assert superseded_status["analysis_busy"] is True
        assert superseded_status["active_superseded"] is True
        assert superseded_status["pending_frame_id"] == 1
    finally:
        tracker.release.set()

    assert _wait_until(lambda: len(tracker.calls) == 1)
    assert tracker.calls[-1]["sequence_id"] == "generation-two"
    assert tracker.calls[-1]["metadata"]["source_generation"] == second_claim["source_generation"]


def test_source_kill_discards_pending_frame(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "1")
    tracker = _CommitFencedClaimBlockingTracker()
    client = _client(tracker)
    headers = {"Authorization": "Bearer secret-token"}
    sequence_id = "kill-pending-sequence"
    claim = client.post(
        "/v1/mobile/frame-ingest/sessions/external-live/source-control/claim",
        headers=headers,
        json={
            "source_id": "edge-roi",
            "sequence_id": sequence_id,
            "source_type": "browser_tab_roi_capture",
            "selection_id": "kill-pending-selection",
            "display_name": "Kill pending chart",
            "coordinate_space": "edge_tab_roi_v1",
        },
    ).json()
    base_epoch_ms = int(time.time() * 1000)
    metadata_json = json.dumps(
        {
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_render_fresh": True,
        }
    )

    def send_frame(frame_id: int) -> Any:
        return client.post(
            "/v1/mobile/frame-ingest/sessions/external-live/frames",
            headers=headers,
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-roi",
                "sequence_id": sequence_id,
                "capture_epoch_ms": str(base_epoch_ms + frame_id * 2_000),
                "frame_id": str(frame_id),
                "source_generation": str(claim["source_generation"]),
                "source_lease_id": str(claim["source_lease_id"]),
                "metadata_json": metadata_json,
            },
        )

    try:
        first = send_frame(1)
        assert first.status_code == 202
        assert tracker.entered.wait(timeout=3.0)
        time.sleep(1.05)
        pending = send_frame(2)
        assert pending.status_code == 202
        assert pending.json()["pending_frame_id"] == 2

        killed = client.post(
            "/v1/mobile/frame-ingest/sessions/external-live/source-control/kill",
            headers=headers,
            json={
                "source_id": "edge-roi",
                "sequence_id": sequence_id,
                "source_generation": claim["source_generation"],
                "source_lease_id": claim["source_lease_id"],
                "reason": "Test kill invalidates pending work.",
            },
        )
        assert killed.status_code == 200
        mailbox_status = client.get(
            "/v1/mobile/frame-ingest/sessions/external-live/status",
            headers=headers,
        ).json()
        assert mailbox_status["pending_frame_id"] is None
    finally:
        tracker.release.set()

    time.sleep(0.05)
    assert tracker.calls == []


def test_frame_ingest_runtime_reset_retires_worker_and_drops_pending_job(monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "1")
    tracker = _BlockingFrameTracker()
    client = _client(tracker)
    path = "/v1/mobile/frame-ingest/sessions/reset-live/frames"
    headers = {"Authorization": "Bearer secret-token"}

    def send_frame(frame_id: int) -> Any:
        return client.post(
            path,
            headers=headers,
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-agent",
                "sequence_id": "reset-sequence",
                "capture_epoch_ms": str(1_780_000_000_000 + frame_id * 2_000),
                "frame_id": str(frame_id),
            },
        )

    try:
        assert send_frame(1).status_code == 202
        assert tracker.entered.wait(timeout=3.0)
        time.sleep(1.05)
        pending = send_frame(2)
        assert pending.status_code == 202
        assert pending.json()["pending_frame_id"] == 2
        reset_frame_ingest_runtime_state_for_tests()
    finally:
        tracker.release.set()

    assert _wait_until(lambda: len(tracker.calls) == 1)
    assert _wait_until(
        lambda: not any(
            thread.is_alive()
            and thread.name == "phoenixguard-frame-analysis-reset-live"
            for thread in threading.enumerate()
        )
    )
    assert [call["frame_id"] for call in tracker.calls] == [1]


def test_worker_start_failure_clears_racing_pending_job(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", "1")
    audit_log = tmp_path / "security_audit.jsonl"
    monkeypatch.setenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", str(audit_log))
    reset_frame_ingest_runtime_state_for_tests()
    tracker = _FakeFrameTracker()
    app = create_app(window_tracker_service=tracker)
    first_client: Any = TestClient(app)
    second_client: Any = TestClient(app)
    start_entered = threading.Event()
    release_start = threading.Event()
    first_result: dict[str, Any] = {}

    def fail_worker_start(worker: threading.Thread) -> None:
        del worker
        start_entered.set()
        if not release_start.wait(timeout=5.0):
            raise RuntimeError("worker start test timed out")
        raise RuntimeError("synthetic worker start failure")

    monkeypatch.setattr(frame_ingest_module, "_start_analysis_worker", fail_worker_start)

    def send_frame(client: Any, frame_id: int) -> Any:
        return client.post(
            "/v1/mobile/frame-ingest/sessions/start-failure-live/frames",
            headers={"Authorization": "Bearer secret-token"},
            files={"frame": ("chart.png", _png_bytes(), "image/png")},
            data={
                "source_id": "edge-agent",
                "sequence_id": "start-failure-sequence",
                "capture_epoch_ms": str(1_780_000_000_000 + frame_id * 2_000),
                "frame_id": str(frame_id),
            },
        )

    def send_first() -> None:
        try:
            first_result["response"] = send_frame(first_client, 1)
        except Exception as exc:
            first_result["error"] = exc

    first_request = threading.Thread(target=send_first, daemon=True)
    first_request.start()
    assert start_entered.wait(timeout=3.0)
    time.sleep(1.05)
    pending = send_frame(second_client, 2)
    assert pending.status_code == 202
    assert pending.json()["pending_frame_id"] == 2
    release_start.set()
    first_request.join(timeout=5.0)

    assert not first_request.is_alive()
    assert isinstance(first_result.get("error"), RuntimeError)
    status_response = second_client.get(
        "/v1/mobile/frame-ingest/sessions/start-failure-live/status",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["analysis_busy"] is False
    assert status_response.json()["pending_frame_id"] is None
    assert tracker.calls == []
    assert _wait_until(
        lambda: audit_log.exists()
        and audit_log.read_text(encoding="utf-8").count("WORKER_START_FAILED") >= 2,
    )


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
                assert status_response.json()["analysis_busy"] is True
                assert status_response.json()["active_frame_id"] == 1
                assert status_response.json()["status"] == "analysis_queued"
            finally:
                tracker.release.set()
            ingest_response = await asyncio.wait_for(ingest_task, timeout=3.0)
            assert ingest_response.status_code == 202

    asyncio.run(exercise())


def test_frame_ingest_audits_late_tracker_source_lease_hard_stop(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", raising=False)
    monkeypatch.setenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "secret-token")
    audit_log = tmp_path / "security_audit.jsonl"
    monkeypatch.setenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", str(audit_log))
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

    assert response.status_code == 202
    assert _wait_until(
        lambda: audit_log.exists()
        and "frame_analysis_discarded" in audit_log.read_text(encoding="utf-8"),
    )
    audit_text = audit_log.read_text(encoding="utf-8")
    assert '"reason_code":"SOURCE_SUPERSEDED"' in audit_text
    assert "source lease is not current" not in audit_text


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
    assert _wait_until(lambda: len(tracker.calls) == 1)
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
