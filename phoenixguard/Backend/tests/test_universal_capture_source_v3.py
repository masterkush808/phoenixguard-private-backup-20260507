from __future__ import annotations

import time
from typing import Any

from PIL import Image
import pytest

from phoenixguard.mobile_api.window_tracker import (
    ContinuousWindowTrackerService,
    ExternalSourceLeaseError,
    _external_duplicate_evidence_guard_armed_v3,  # pyright: ignore[reportPrivateUsage]
    _external_evidence_lineage_v3,  # pyright: ignore[reportPrivateUsage]
)


def _service_with_session(tmp_path: Any) -> ContinuousWindowTrackerService:
    service = ContinuousWindowTrackerService(root_dir=tmp_path / "universal-source")
    service.create_session(session_id="universal-live", auto_start=False)
    return service


def _claim_browser_source(
    service: ContinuousWindowTrackerService,
    *,
    source_id: str,
    sequence_id: str,
) -> dict[str, Any]:
    return service.claim_external_source(
        "universal-live",
        source_id=source_id,
        sequence_id=sequence_id,
        source_type="browser_tab_roi_capture",
        selection_id=f"selection-{sequence_id}",
        display_name="TradingView chart",
        coordinate_space="edge_tab_roi_v1",
    )


def _accept_capture(*args: Any, **kwargs: Any) -> bool:
    del args, kwargs
    return True


def test_external_analysis_exception_clears_processing_and_allows_next_frame(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(
            service,
            source_id="edge-chart",
            sequence_id="seq-failure-retry",
        )
        attempts = 0

        def fail_once(*args: Any, **kwargs: Any) -> bool:
            nonlocal attempts
            del args, kwargs
            attempts += 1
            if attempts == 1:
                raise RuntimeError("secret=must-not-be-persisted")
            return True

        monkeypatch.setattr(service, "_capture_and_analyze", fail_once)
        base_epoch_ms = int(time.time() * 1000)
        common = {
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": claim["source_generation"],
            "source_lease_id": claim["source_lease_id"],
            "source_render_fresh": True,
        }

        with pytest.raises(RuntimeError, match="must-not-be-persisted"):
            service.ingest_external_frame(
                "universal-live",
                Image.new("RGB", (640, 360), (15, 20, 30)),
                source_id="edge-chart",
                sequence_id="seq-failure-retry",
                capture_epoch_ms=base_epoch_ms,
                frame_id=1,
                metadata=common,
            )

        failed = service.load_session_payload("universal-live")
        failed_source = failed["capture_source_v3"]
        failed_stream = failed_source["stream"]
        assert failed["status"] == "external_source_error"
        assert failed_source["state"] == "ERROR"
        assert failed_source["decision_usable"] is False
        assert failed_source["reason_code"] == "FRAME_ANALYSIS_FAILED"
        assert failed_stream["processing"] is False
        assert failed_stream["processing_frame_id"] == 0
        assert failed_stream["last_failed_frame_id"] == 1
        assert failed_stream["last_failure_error_type"] == "RuntimeError"
        assert "must-not-be-persisted" not in str(failed)
        transport = service.get_external_frame_transport_status("universal-live")
        assert transport["source_state"] == "ERROR"
        assert transport["stream"]["processing"] is False

        recovered = service.ingest_external_frame(
            "universal-live",
            Image.new("RGB", (640, 360), (15, 20, 30)),
            source_id="edge-chart",
            sequence_id="seq-failure-retry",
            capture_epoch_ms=base_epoch_ms + 1_000,
            frame_id=2,
            metadata=common,
        )
        recovered_source = recovered["capture_source_v3"]
        assert recovered_source["state"] == "LIVE"
        assert recovered_source["decision_usable"] is True
        assert recovered_source["stream"]["processing"] is False
        assert recovered_source["stream"]["processing_frame_id"] == 0
        assert recovered_source["last_frame_id"] == 2
        assert attempts == 2
    finally:
        service.shutdown()


def test_rejected_study_healthy_heartbeat_releases_lease_for_fresh_recapture(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(
            service,
            source_id="edge-chart",
            sequence_id="seq-study-reclaim",
        )
        def failing_capture(*args: Any, **kwargs: Any) -> bool:
            return False

        monkeypatch.setattr(
            service,
            "_capture_and_analyze",
            failing_capture,
        )
        now_ms = int(time.time() * 1000)
        metadata = {
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": claim["source_generation"],
            "source_lease_id": claim["source_lease_id"],
            "source_render_fresh": True,
        }
        failed = service.ingest_external_frame(
            "universal-live",
            Image.new("RGB", (640, 360), (15, 20, 30)),
            source_id="edge-chart",
            sequence_id="seq-study-reclaim",
            capture_epoch_ms=now_ms,
            frame_id=1,
            metadata=metadata,
        )
        assert failed["frame_ingest"]["accepted"] is False
        assert failed["frame_ingest"]["failure_reason_code"] == (
            "FRAME_STUDY_NOT_ACCEPTED"
        )

        released = service.heartbeat_external_source(
            "universal-live",
            source_id="edge-chart",
            sequence_id="seq-study-reclaim",
            source_generation=int(claim["source_generation"]),
            source_lease_id=str(claim["source_lease_id"]),
            capture_epoch_ms=now_ms + 1_000,
            source_render_fresh=True,
            capture_status="active",
            decoder_frame_age_ms=10,
        )
        assert released["state"] == "NO_SOURCE"
        assert released["reason_code"] == "SOURCE_RECLAIM_REQUIRED"
        assert released["source_id"] == ""
        assert released["sequence_id"] == ""
        assert released["source_generation"] == 0
        assert "source_lease_id" not in released
        assert released["stream"]["study_reclaim_attempts"] == 1

        reclaimed = service.claim_external_source(
            "universal-live",
            source_id="edge-chart",
            sequence_id="seq-study-reclaim",
            source_type="browser_tab_roi_capture",
            selection_id="selection-seq-study-reclaim",
            display_name="TradingView chart",
            coordinate_space="edge_tab_roi_v1",
            expected_source_control=released,
        )
        assert reclaimed["state"] == "VALIDATING"
        assert reclaimed["source_generation"] == 1
        assert reclaimed["source_lease_id"] != claim["source_lease_id"]
        assert reclaimed["stream"]["study_reclaim_attempts"] == 1

        monkeypatch.setattr(service, "_capture_and_analyze", _accept_capture)
        recovered = service.ingest_external_frame(
            "universal-live",
            Image.new("RGB", (640, 360), (15, 20, 30)),
            source_id="edge-chart",
            sequence_id="seq-study-reclaim",
            capture_epoch_ms=now_ms + 2_000,
            frame_id=1,
            metadata={
                **metadata,
                "source_generation": reclaimed["source_generation"],
                "source_lease_id": reclaimed["source_lease_id"],
            },
        )
        assert recovered["frame_ingest"]["accepted"] is True
        assert recovered["capture_source_v3"]["state"] == "LIVE"
        assert (
            recovered["capture_source_v3"]["stream"][
                "study_reclaim_attempts"
            ]
            == 0
        )
    finally:
        service.shutdown()


def test_source_claim_switch_and_kill_are_generation_fenced(tmp_path: Any) -> None:
    service = _service_with_session(tmp_path)
    try:
        first = _claim_browser_source(service, source_id="edge-chart-a", sequence_id="seq-a")
        assert first["state"] == "VALIDATING"
        assert first["source_generation"] == 1
        assert first["source_lease_id"]
        assert service.validate_external_source_lease(
            "universal-live",
            source_id="edge-chart-a",
            sequence_id="seq-a",
            source_generation=1,
            source_lease_id=first["source_lease_id"],
        )["allowed"] is True

        second = _claim_browser_source(service, source_id="edge-chart-b", sequence_id="seq-b")
        assert second["source_generation"] == 2
        superseded = service.validate_external_source_lease(
            "universal-live",
            source_id="edge-chart-a",
            sequence_id="seq-a",
            source_generation=1,
            source_lease_id=first["source_lease_id"],
        )
        assert superseded["allowed"] is False
        assert superseded["status_code"] == 409
        assert superseded["reason_code"] == "SOURCE_SUPERSEDED"

        killed = service.kill_external_source(
            "universal-live",
            reason="Tracking stopped. The previous picture is historical.",
        )
        assert killed["state"] == "KILLED"
        assert killed["source_generation"] == 3
        assert "source_lease_id" not in killed
        stopped = service.validate_external_source_lease(
            "universal-live",
            source_id="edge-chart-b",
            sequence_id="seq-b",
            source_generation=2,
            source_lease_id=second["source_lease_id"],
        )
        assert stopped["allowed"] is False
        assert stopped["status_code"] == 410
        assert stopped["reason_code"] == "SOURCE_KILLED"
    finally:
        service.shutdown()


def test_conditional_recovery_claim_atomically_rejects_interleaving_owner(
    tmp_path: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        observed = service.get_session_snapshot("universal-live")["capture_source_v3"]
        assert observed["state"] == "NO_SOURCE"
        assert observed["state_revision"] == 0

        competitor = _claim_browser_source(
            service,
            source_id="edge-chart-competitor",
            sequence_id="seq-competitor",
        )

        with pytest.raises(ExternalSourceLeaseError) as exc_info:
            service.claim_external_source(
                "universal-live",
                source_id="windows-region-capture-v3",
                sequence_id="seq-recovery",
                source_type="windows_graphics_capture_roi",
                selection_id="selection-recovery",
                display_name="Recovered chart",
                coordinate_space="wgc_hwnd_roi_v1",
                expected_source_control=observed,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.reason_code == "SOURCE_RECOVERY_RACE"
        current = service.load_session_payload("universal-live")["capture_source_v3"]
        assert current["source_id"] == "edge-chart-competitor"
        assert current["sequence_id"] == "seq-competitor"
        assert current["source_generation"] == competitor["source_generation"]
        assert current["source_lease_id"] == competitor["source_lease_id"]
    finally:
        service.shutdown()


def test_first_accepted_roi_frame_promotes_source_live_without_leaking_lease(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(service, source_id="edge-chart", sequence_id="seq-live")
        monkeypatch.setattr(service, "_capture_and_analyze", _accept_capture)
        now_ms = int(time.time() * 1000)
        result = service.ingest_external_frame(
            "universal-live",
            Image.new("RGB", (640, 360), (15, 20, 30)),
            source_id="edge-chart",
            symbol="CHF/JPY OTC",
            timeframe="M5",
            sequence_id="seq-live",
            capture_epoch_ms=now_ms,
            frame_id=1,
            metadata={
                "source_type": "browser_tab_roi_capture",
                "coordinate_space": "edge_tab_roi_v1",
                "source_generation": claim["source_generation"],
                "source_lease_id": claim["source_lease_id"],
                "selection_id": "selection-seq-live",
                "roi_normalized": [0.1, 0.2, 0.9, 0.8],
                "roi_source_pixels": {
                    "x": 192,
                    "y": 216,
                    "width": 1536,
                    "height": 648,
                },
                "source_surface_width": 1920,
                "source_surface_height": 1080,
                "source_render_fresh": True,
            },
        )

        source = result["capture_source_v3"]
        assert source["state"] == "LIVE"
        assert source["decision_usable"] is True
        assert source["fresh"] is True
        assert source["last_frame_id"] == 1
        assert source["roi"]["normalized_bbox"] == [0.1, 0.2, 0.9, 0.8]
        assert "source_lease_id" not in source
        assert result["external_frame_feed"]["source_id"] == "edge-chart"
        assert result["external_frame_feed"]["sequence_id"] == "seq-live"
        assert result["external_frame_feed"]["frame_id"] == 1
        assert result["external_frame_feed"]["symbol"] == ""
        assert result["external_frame_feed"]["timeframe"] == ""
        assert result["market"] == ""
        persisted = service.load_session_payload("universal-live")
        assert persisted["capture_source_v3"]["source_lease_id"] == claim["source_lease_id"]
        assert persisted["external_frame_feed"]["source_id"] == "edge-chart"
        assert persisted["external_frame_feed"]["frame_id"] == 1
        assert persisted["external_frame_feed"]["symbol"] == ""
        assert persisted["external_frame_feed"]["timeframe"] == ""
        assert persisted["market"] == ""
    finally:
        service.shutdown()


def test_transport_heartbeat_keeps_source_fresh_but_newer_material_frame_blocks_old_result(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(
            service,
            source_id="edge-chart",
            sequence_id="seq-heartbeat",
        )
        first_heartbeat_epoch_ms = int(time.time() * 1000)
        heartbeat = service.heartbeat_external_source(
            "universal-live",
            source_id="edge-chart",
            sequence_id="seq-heartbeat",
            source_generation=int(claim["source_generation"]),
            source_lease_id=str(claim["source_lease_id"]),
            capture_epoch_ms=first_heartbeat_epoch_ms,
            source_render_fresh=True,
            material_change_pending=False,
            roi_normalized={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            roi_source_pixels={"x": 0, "y": 0, "width": 1920, "height": 1080},
            source_surface_width=1920,
            source_surface_height=1080,
            capture_health_reason="capture_confirmed",
            capture_status="active",
        )
        assert heartbeat["fresh"] is True
        assert heartbeat["decision_usable"] is False
        assert heartbeat["roi"]["normalized_bbox"] == [0.0, 0.0, 1.0, 1.0]
        public = service.get_session_snapshot("universal-live")["capture_source_v3"]
        assert public["fresh"] is True
        assert public["roi"]["normalized_bbox"] == [0.0, 0.0, 1.0, 1.0]

        analyzed_capture_epoch_ms = first_heartbeat_epoch_ms - 1_000

        def receive_newer_frame_while_analysis_runs(*_args: Any, **_kwargs: Any) -> bool:
            pending_epoch_ms = int(time.time() * 1000)
            service.heartbeat_external_source(
                "universal-live",
                source_id="edge-chart",
                sequence_id="seq-heartbeat",
                source_generation=int(claim["source_generation"]),
                source_lease_id=str(claim["source_lease_id"]),
                capture_epoch_ms=pending_epoch_ms,
                source_render_fresh=True,
                material_change_pending=True,
                roi_normalized={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            )
            # A later unchanged pulse must not erase the queued changed frame.
            service.heartbeat_external_source(
                "universal-live",
                source_id="edge-chart",
                sequence_id="seq-heartbeat",
                source_generation=int(claim["source_generation"]),
                source_lease_id=str(claim["source_lease_id"]),
                capture_epoch_ms=pending_epoch_ms,
                source_render_fresh=True,
                material_change_pending=False,
                roi_normalized=[0.0, 0.0, 1.0, 1.0],
            )
            return True

        monkeypatch.setattr(
            service,
            "_capture_and_analyze",
            receive_newer_frame_while_analysis_runs,
        )
        result = service.ingest_external_frame(
            "universal-live",
            Image.new("RGB", (640, 360), (15, 20, 30)),
            source_id="edge-chart",
            sequence_id="seq-heartbeat",
            capture_epoch_ms=analyzed_capture_epoch_ms,
            frame_id=1,
            metadata={
                "source_type": "browser_tab_roi_capture",
                "coordinate_space": "edge_tab_roi_v1",
                "source_generation": claim["source_generation"],
                "source_lease_id": claim["source_lease_id"],
                "source_render_fresh": True,
                "roi_normalized": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            },
        )

        guarded = result["capture_source_v3"]
        assert guarded["state"] == "VALIDATING"
        assert guarded["reason_code"] == "FRAME_PENDING"
        assert guarded["fresh"] is True
        assert guarded["decision_usable"] is False
        assert guarded["stream"]["material_change_pending"] is True
        assert guarded["roi"]["normalized_bbox"] == [0.0, 0.0, 1.0, 1.0]
    finally:
        service.shutdown()


def test_same_lease_pending_frame_does_not_starve_completed_study_publication(
    tmp_path: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(
            service,
            source_id="edge-chart",
            sequence_id="seq-no-starvation",
        )
        study_capture_epoch = time.time() - 1.0
        service.heartbeat_external_source(
            "universal-live",
            source_id="edge-chart",
            sequence_id="seq-no-starvation",
            source_generation=int(claim["source_generation"]),
            source_lease_id=str(claim["source_lease_id"]),
            capture_epoch_ms=int(time.time() * 1000),
            source_render_fresh=True,
            material_change_pending=True,
            roi_normalized=[0.0, 0.0, 1.0, 1.0],
        )
        source = {
            "source_id": "edge-chart",
            "sequence_id": "seq-no-starvation",
            "source_generation": int(claim["source_generation"]),
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "metadata": {"source_lease_id": str(claim["source_lease_id"])},
        }

        with service._external_source_publication_guard_v3(  # pyright: ignore[reportPrivateUsage]
            "universal-live",
            source,
            capture_epoch=study_capture_epoch,
        ) as publication_current:
            assert publication_current is True

        public = service.get_session_snapshot("universal-live")[
            "capture_source_v3"
        ]
        assert public["state"] == "VALIDATING"
        assert public["reason_code"] == "FRAME_PENDING"
        assert public["fresh"] is True
        assert public["decision_usable"] is False
    finally:
        service.shutdown()


def test_generation_fenced_kill_cannot_stop_a_superseding_source(tmp_path: Any) -> None:
    service = _service_with_session(tmp_path)
    try:
        first = _claim_browser_source(service, source_id="edge-chart-a", sequence_id="seq-a")
        second = _claim_browser_source(service, source_id="edge-chart-b", sequence_id="seq-b")

        with pytest.raises(ExternalSourceLeaseError) as exc_info:
            service.kill_external_source(
                "universal-live",
                reason="Stop the old source.",
                source_id="edge-chart-a",
                sequence_id="seq-a",
                source_generation=int(first["source_generation"]),
                source_lease_id=str(first["source_lease_id"]),
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.reason_code == "SOURCE_SUPERSEDED"
        current = service.load_session_payload("universal-live")["capture_source_v3"]
        assert current["state"] == "VALIDATING"
        assert current["source_id"] == "edge-chart-b"
        assert current["source_generation"] == second["source_generation"]
        assert current["source_lease_id"] == second["source_lease_id"]

        killed = service.kill_external_source(
            "universal-live",
            reason="Stop the current source.",
            source_id="edge-chart-b",
            sequence_id="seq-b",
            source_generation=int(second["source_generation"]),
            source_lease_id=str(second["source_lease_id"]),
        )
        assert killed["state"] == "KILLED"
        killed_generation = int(killed["source_generation"])

        with pytest.raises(ExternalSourceLeaseError) as killed_exc_info:
            service.kill_external_source(
                "universal-live",
                reason="Stop it twice.",
                source_id="edge-chart-b",
                sequence_id="seq-b",
                source_generation=int(second["source_generation"]),
                source_lease_id=str(second["source_lease_id"]),
            )
        assert killed_exc_info.value.status_code == 410
        assert killed_exc_info.value.reason_code == "SOURCE_KILLED"
        after_rejected_retry = service.load_session_payload("universal-live")["capture_source_v3"]
        assert after_rejected_retry["source_generation"] == killed_generation
    finally:
        service.shutdown()


def test_claimed_source_rejects_frame_with_omitted_lease_metadata_before_analysis(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(service, source_id="edge-chart", sequence_id="seq-live")
        analysis_calls: list[str] = []

        def record_analysis_call(*args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            analysis_calls.append("called")
            return True

        monkeypatch.setattr(
            service,
            "_capture_and_analyze",
            record_analysis_call,
        )

        with pytest.raises(ExternalSourceLeaseError) as exc_info:
            service.ingest_external_frame(
                "universal-live",
                Image.new("RGB", (640, 360), (15, 20, 30)),
                source_id="edge-chart",
                sequence_id="seq-live",
                capture_epoch_ms=int(time.time() * 1000),
                frame_id=1,
                metadata={},
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.reason_code == "SOURCE_SUPERSEDED"
        assert analysis_calls == []
        persisted = service.load_session_payload("universal-live")
        assert persisted["capture_source_v3"]["source_generation"] == claim["source_generation"]
        assert persisted["capture_source_v3"]["state"] == "VALIDATING"
        assert persisted["external_frame_feed"] == {}
    finally:
        service.shutdown()


def test_superseded_source_frame_cannot_mutate_new_claim_or_run_analysis(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        first = _claim_browser_source(service, source_id="edge-chart-a", sequence_id="seq-a")
        second = _claim_browser_source(service, source_id="edge-chart-b", sequence_id="seq-b")
        analysis_calls: list[str] = []

        def record_analysis_call(*args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            analysis_calls.append("called")
            return True

        monkeypatch.setattr(
            service,
            "_capture_and_analyze",
            record_analysis_call,
        )

        with pytest.raises(ExternalSourceLeaseError) as exc_info:
            service.ingest_external_frame(
                "universal-live",
                Image.new("RGB", (640, 360), (15, 20, 30)),
                source_id="edge-chart-a",
                sequence_id="seq-a",
                capture_epoch_ms=int(time.time() * 1000),
                frame_id=1,
                metadata={
                    "source_type": "browser_tab_roi_capture",
                    "coordinate_space": "edge_tab_roi_v1",
                    "source_generation": first["source_generation"],
                    "source_lease_id": first["source_lease_id"],
                },
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.reason_code == "SOURCE_SUPERSEDED"
        assert analysis_calls == []
        persisted = service.load_session_payload("universal-live")
        current = persisted["capture_source_v3"]
        assert current["source_id"] == "edge-chart-b"
        assert current["source_generation"] == second["source_generation"]
        assert current["source_lease_id"] == second["source_lease_id"]
        assert persisted["external_frame_feed"] == {}
    finally:
        service.shutdown()


def test_killed_source_rejects_frame_with_typed_gone_error(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(service, source_id="edge-chart", sequence_id="seq-live")
        service.kill_external_source(
            "universal-live",
            reason="Operator stopped the source.",
            source_id="edge-chart",
            sequence_id="seq-live",
            source_generation=int(claim["source_generation"]),
            source_lease_id=str(claim["source_lease_id"]),
        )
        analysis_calls: list[str] = []

        def record_analysis_call(*args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            analysis_calls.append("called")
            return True

        monkeypatch.setattr(
            service,
            "_capture_and_analyze",
            record_analysis_call,
        )

        with pytest.raises(ExternalSourceLeaseError) as exc_info:
            service.ingest_external_frame(
                "universal-live",
                Image.new("RGB", (640, 360), (15, 20, 30)),
                source_id="edge-chart",
                sequence_id="seq-live",
                capture_epoch_ms=int(time.time() * 1000),
                frame_id=1,
                metadata={
                    "source_type": "browser_tab_roi_capture",
                    "coordinate_space": "edge_tab_roi_v1",
                    "source_generation": claim["source_generation"],
                    "source_lease_id": claim["source_lease_id"],
                },
            )

        assert exc_info.value.status_code == 410
        assert exc_info.value.reason_code == "SOURCE_KILLED"
        assert analysis_calls == []
        persisted = service.load_session_payload("universal-live")
        assert persisted["capture_source_v3"]["state"] == "KILLED"
        assert persisted["external_frame_feed"] == {}
    finally:
        service.shutdown()


def test_external_frame_id_must_advance_even_when_capture_time_advances(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(service, source_id="edge-chart", sequence_id="seq-live")
        analysis_calls: list[int] = []

        def record_analysis_call(*args: Any, **kwargs: Any) -> bool:
            del args, kwargs
            analysis_calls.append(1)
            return True

        monkeypatch.setattr(
            service,
            "_capture_and_analyze",
            record_analysis_call,
        )
        metadata = {
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": claim["source_generation"],
            "source_lease_id": claim["source_lease_id"],
        }
        now_ms = int(time.time() * 1000)
        service.ingest_external_frame(
            "universal-live",
            Image.new("RGB", (640, 360), (15, 20, 30)),
            source_id="edge-chart",
            sequence_id="seq-live",
            capture_epoch_ms=now_ms,
            frame_id=7,
            metadata=metadata,
        )

        with pytest.raises(ValueError, match="frame_id did not advance"):
            service.ingest_external_frame(
                "universal-live",
                Image.new("RGB", (640, 360), (15, 20, 30)),
                source_id="edge-chart",
                sequence_id="seq-live",
                capture_epoch_ms=now_ms + 1,
                frame_id=7,
                metadata=metadata,
            )

        assert analysis_calls == [1]
        persisted = service.load_session_payload("universal-live")
        assert persisted["external_frame_feed"]["frame_id"] == 7
        assert persisted["capture_source_v3"]["last_frame_id"] == 7
    finally:
        service.shutdown()


def test_external_duplicate_guard_is_scoped_to_generation_and_sequence() -> None:
    incoming = _external_evidence_lineage_v3(
        {
            "source_id": "windows-region-capture-v3",
            "sequence_id": "sequence-a",
            "source_generation": 4,
            "source_type": "windows_graphics_capture_roi",
            "coordinate_space": "wgc_hwnd_roi_v1",
        }
    )
    assert _external_duplicate_evidence_guard_armed_v3(
        capture_started_with_tracking_enabled=False,
        using_external_frame=True,
        using_local_cpu_stream_frame=False,
        previous_model_frame=0,
        previous_lineage={},
        incoming_lineage=incoming,
    ) is False

    previous = _external_evidence_lineage_v3(
        incoming,
        model_frame_id=21,
        study_surface_signature="same-pixels",
        published_epoch=time.time(),
    )
    assert _external_duplicate_evidence_guard_armed_v3(
        capture_started_with_tracking_enabled=False,
        using_external_frame=True,
        using_local_cpu_stream_frame=False,
        previous_model_frame=21,
        previous_lineage=previous,
        incoming_lineage=incoming,
    ) is True

    for changed in (
        {**incoming, "source_generation": 5},
        {**incoming, "sequence_id": "sequence-b"},
    ):
        assert _external_duplicate_evidence_guard_armed_v3(
            capture_started_with_tracking_enabled=False,
            using_external_frame=True,
            using_local_cpu_stream_frame=False,
            previous_model_frame=21,
            previous_lineage=previous,
            incoming_lineage=changed,
        ) is False


def test_live_promotion_cannot_overwrite_newer_source_generation(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(service, source_id="edge-chart", sequence_id="seq-live")
        monkeypatch.setattr(service, "_capture_and_analyze", _accept_capture)
        original_save = service._save_session  # pyright: ignore[reportPrivateUsage]
        race_injected = {"value": False}

        def save_with_superseding_claim(payload: dict[str, Any]) -> None:
            source = dict(payload.get("capture_source_v3", {}))
            if source.get("state") == "LIVE" and not race_injected["value"]:
                race_injected["value"] = True
                newer = service.load_session_payload("universal-live")
                newer_source = dict(newer.get("capture_source_v3", {}))
                newer_source.update(
                    {
                        "state": "VALIDATING",
                        "source_id": "edge-chart-new",
                        "sequence_id": "seq-new",
                        "source_generation": int(source["source_generation"]) + 1,
                        "source_lease_id": "newer-lease",
                        "source_type": "browser_tab_roi_capture",
                        "coordinate_space": "edge_tab_roi_v1",
                        "fresh": False,
                        "decision_usable": False,
                    }
                )
                newer["capture_source_v3"] = newer_source
                newer["external_frame_feed"] = {}
                newer["__control_write_v3"] = True
                original_save(newer)
            original_save(payload)

        monkeypatch.setattr(service, "_save_session", save_with_superseding_claim)
        service.ingest_external_frame(
            "universal-live",
            Image.new("RGB", (640, 360), (15, 20, 30)),
            source_id="edge-chart",
            sequence_id="seq-live",
            capture_epoch_ms=int(time.time() * 1000),
            frame_id=1,
            metadata={
                "source_type": "browser_tab_roi_capture",
                "coordinate_space": "edge_tab_roi_v1",
                "source_generation": claim["source_generation"],
                "source_lease_id": claim["source_lease_id"],
            },
        )

        assert race_injected["value"] is True
        persisted = service.load_session_payload("universal-live")
        assert persisted["capture_source_v3"]["source_id"] == "edge-chart-new"
        assert persisted["capture_source_v3"]["source_generation"] == int(claim["source_generation"]) + 1
        assert persisted["capture_source_v3"]["state"] == "VALIDATING"
    finally:
        service.shutdown()


def test_public_live_source_becomes_stale_and_revokes_execution_when_frames_stop(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    service = _service_with_session(tmp_path)
    try:
        claim = _claim_browser_source(service, source_id="edge-chart", sequence_id="seq-stale")
        payload = service.load_session_payload("universal-live")
        payload["capture_source_v3"].update(
            {
                "state": "LIVE",
                "fresh": True,
                "decision_usable": True,
                "last_frame_epoch": time.time() - 30.0,
                "last_frame_id": 8,
            }
        )
        payload["latest_signal"] = {
            "signal_id": "unsafe-old-signal",
            "actionable": True,
            "execution_action": "BUY",
            "execution_permission": "ENTER",
        }
        payload["__control_write_v3"] = True
        service._save_session(payload)  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setenv("PHOENIXGUARD_SELECTED_SOURCE_STALE_SEC", "5")

        public = service.get_session_snapshot("universal-live")
        assert public["capture_source_v3"]["state"] == "STALE"
        assert public["capture_source_v3"]["stale_after_sec"] == 5.0
        assert public["capture_source_v3"]["decision_usable"] is False
        assert public["latest_signal"]["actionable"] is False
        assert public["latest_signal"]["execution_action"] == "HOLD"
        assert public["decision_valid_until_epoch"] == 0.0
        assert "source_lease_id" not in public["capture_source_v3"]
        assert claim["source_lease_id"]
    finally:
        service.shutdown()
