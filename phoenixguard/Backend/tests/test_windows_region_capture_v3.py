from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Mapping, cast

import numpy as np
from PIL import Image
import pytest

from phoenixguard.mobile_api.native_source_region_overlay import normalize_drag_bbox_v3
from phoenixguard.mobile_api.windows_region_capture_v3 import (
    ActiveRegionSourceV3,
    CapturedWindowFrameV3,
    LatestFrameSlotV3,
    PhoenixGuardRegionIngestClientV3,
    RegionBindingV3,
    RegionSelectionV3,
    SourceLeaseLostError,
    SourceLeaseV3,
    SourceSelectionCancelled,
    WGC_COORDINATE_SPACE,
    WGC_SOURCE_ID,
    WGC_SOURCE_TYPE,
    WgcRuntimeUnavailableError,
    WindowIdentityV3,
    WindowsGraphicsCaptureStreamV3,
    WindowsRegionCaptureManagerV3,
    crop_normalized_region_v3,
    normalize_region_bbox_v3,
    require_windows_capture_runtime_v3,
)
import phoenixguard.mobile_api.windows_region_capture_v3 as capture_module


def _identity(
    *,
    hwnd: int = 101,
    pid: int = 202,
    create_time: float = 1234.5,
    path: str = r"C:\Program Files\Browser\browser.exe",
    class_name: str = "Browser_WidgetWin_1",
    title: str = "TradingView",
    rect: tuple[int, int, int, int] = (-100, 20, 1180, 740),
    minimized: bool = False,
) -> WindowIdentityV3:
    return WindowIdentityV3(
        hwnd=hwnd,
        process_id=pid,
        process_create_time=create_time,
        process_path=path,
        class_name=class_name,
        title=title,
        rect=rect,
        is_visible=True,
        is_minimized=minimized,
    )


def _frame(frame_id: int, *, size: tuple[int, int] = (200, 120)) -> CapturedWindowFrameV3:
    return CapturedWindowFrameV3(
        local_generation=1,
        frame_id=frame_id,
        captured_epoch=time.time(),
        qpc_timespan=frame_id * 100,
        image=Image.new("RGB", size, (frame_id % 255, 12, 18)),
    )


def _selection(identity: WindowIdentityV3 | None = None) -> RegionSelectionV3:
    return RegionSelectionV3(
        identity=identity or _identity(),
        normalized_bbox=(0.1, 0.2, 0.9, 0.8),
        selection_id="selection-1",
        sequence_id="sequence-1",
        reference_frame_size=(200, 120),
    )


def test_region_normalization_and_crop_preserve_exact_selected_pixels() -> None:
    image = Image.new("RGB", (100, 50), (0, 0, 0))
    normalized = normalize_region_bbox_v3((0.1, 0.2, 0.9, 0.8))
    cropped = crop_normalized_region_v3(image, normalized)

    assert normalized == (0.1, 0.2, 0.9, 0.8)
    assert cropped.size == (80, 30)
    with pytest.raises(ValueError, match="too small"):
        normalize_region_bbox_v3((0.5, 0.5, 0.501, 0.501))


def test_native_drag_normalization_supports_reversed_and_negative_monitor_geometry() -> None:
    assert normalize_drag_bbox_v3((90, 80), (10, 20), width=100, height=100) == [
        0.1,
        0.2,
        0.9,
        0.8,
    ]
    assert normalize_drag_bbox_v3((-20, -30), (60, 70), width=100, height=100) == [
        0.0,
        0.0,
        0.6,
        0.7,
    ]


def test_latest_frame_slot_is_depth_one_and_never_regresses() -> None:
    slot = LatestFrameSlotV3()
    slot.publish(_frame(1))
    slot.publish(_frame(3))
    slot.publish(_frame(2))

    latest = slot.latest()
    assert latest is not None
    assert latest.frame_id == 3
    assert slot.wait_for_frame(after_frame_id=2, timeout=0.01) is latest


def test_window_identity_allows_title_and_rect_drift_but_rejects_hwnd_reuse() -> None:
    original = _identity()
    title_and_rect_changed = _identity(title="Another symbol", rect=(0, 0, 1600, 900))
    reused_by_new_process = _identity(create_time=original.process_create_time + 30.0)

    assert original.same_target(title_and_rect_changed) is True
    assert original.same_target(reused_by_new_process) is False
    assert original.same_target(_identity(pid=999)) is False
    assert original.same_target(_identity(class_name="OtherClass")) is False


class _FakeCaptureControl:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeWindowsCapture:
    def __init__(self, kwargs: Mapping[str, Any]) -> None:
        self.kwargs = dict(kwargs)
        self.handlers: dict[str, Any] = {}
        self.control = _FakeCaptureControl()

    def event(self, callback: Any) -> Any:
        self.handlers[callback.__name__] = callback
        return callback

    def start_free_threaded(self) -> _FakeCaptureControl:
        return self.control


def test_wgc_stream_is_exact_hwnd_latest_frame_capture_without_fallback() -> None:
    created: list[_FakeWindowsCapture] = []

    def factory(**kwargs: Any) -> _FakeWindowsCapture:
        capture = _FakeWindowsCapture(kwargs)
        created.append(capture)
        return capture

    stream = WindowsGraphicsCaptureStreamV3(
        _identity(hwnd=777),
        local_generation=4,
        minimum_update_interval_ms=750,
        capture_factory=factory,
    )
    stream.start()
    capture = created[0]

    assert capture.kwargs["window_hwnd"] == 777
    assert capture.kwargs["minimum_update_interval"] == 750
    assert capture.kwargs["cursor_capture"] is False
    assert capture.kwargs["draw_border"] is False

    fake_wgc_frame = type(
        "FakeWgcFrame",
        (),
        {
            "frame_buffer": np.array([[[0, 0, 255, 255], [0, 255, 0, 255]]], dtype=np.uint8),
            "timespan": 9001,
        },
    )()
    capture.handlers["on_frame_arrived"](fake_wgc_frame, capture.control)
    result = stream.wait_first_frame(timeout=0.05)

    assert result.local_generation == 4
    assert result.qpc_timespan == 9001
    assert result.image.getpixel((0, 0)) == (255, 0, 0)
    assert result.image.getpixel((1, 0)) == (0, 255, 0)
    stream.stop()
    assert capture.control.stopped is True


@dataclass
class _FakeResponse:
    status_code: int
    payload: Any
    response_headers: Mapping[str, str] | None = None

    @property
    def headers(self) -> Mapping[str, str]:
        return self.response_headers or {}

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.posts: list[dict[str, Any]] = []
        self.gets: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.gets.append({"url": url, **kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_ingest_client_claim_and_frame_carry_generation_and_lease() -> None:
    http = _FakeHttpSession(
        [
            _FakeResponse(200, {"source_generation": 8, "source_lease_id": "lease-secret"}),
            _FakeResponse(200, {"accepted": True}),
        ]
    )
    client = PhoenixGuardRegionIngestClientV3(
        base_url="http://127.0.0.1:8793",
        session_id="live-session",
        token="token-secret",
        http_session=cast(Any, http),
    )
    selection = _selection()
    lease = client.claim_source(selection)
    binding = RegionBindingV3(selection=selection, lease=lease)
    stream = type("FakeStream", (), {})()
    active = ActiveRegionSourceV3(local_generation=1, binding=binding, stream=stream)  # type: ignore[arg-type]
    frame = _frame(12)
    client.upload_frame(active, frame, crop_normalized_region_v3(frame.image, selection.normalized_bbox))

    claim = http.posts[0]
    assert claim["json"] == {
        "source_id": "windows-region-capture-v3",
        "sequence_id": "sequence-1",
        "source_type": "windows_graphics_capture_roi",
        "selection_id": "selection-1",
        "display_name": "TradingView",
        "coordinate_space": "wgc_hwnd_roi_v1",
    }
    upload = http.posts[1]
    assert upload["data"]["source_generation"] == "8"
    assert upload["data"]["source_lease_id"] == "lease-secret"
    metadata = json.loads(upload["data"]["metadata_json"])
    assert metadata["source_generation"] == 8
    assert metadata["source_lease_id"] == "lease-secret"
    assert metadata["source_type"] == WGC_SOURCE_TYPE
    assert metadata["coordinate_space"] == WGC_COORDINATE_SPACE
    assert metadata["focus_policy"] == "hwnd_wgc_no_activation"
    assert metadata["roi_normalized"] == [0.1, 0.2, 0.9, 0.8]
    assert metadata["roi_source_pixels"] == {"x": 20, "y": 24, "width": 160, "height": 72}
    assert metadata["source_surface_width"] == 200
    assert metadata["source_surface_height"] == 120
    assert metadata["source_render_fresh"] is True


def test_ingest_client_reads_public_capture_source_fence() -> None:
    http = _FakeHttpSession(
        [
            _FakeResponse(
                200,
                {
                    "source_control": {
                        "state": "LIVE",
                        "source_id": "windows-region-capture-v3",
                        "sequence_id": "sequence-1",
                    }
                },
            )
        ]
    )
    client = PhoenixGuardRegionIngestClientV3(
        base_url="http://127.0.0.1:8793",
        session_id="live-session",
        token="token-secret",
        http_session=cast(Any, http),
    )

    source = client.get_source_control()

    assert source["state"] == "LIVE"
    assert source["sequence_id"] == "sequence-1"
    assert http.gets[0]["url"].endswith("/live-session/source-control")
    assert http.gets[0]["headers"] == {"X-PhoenixGuard-Token": "token-secret"}


def test_ingest_client_sends_exact_observed_fence_for_conditional_recovery() -> None:
    http = _FakeHttpSession(
        [_FakeResponse(201, {"source_generation": 8, "source_lease_id": "lease-8"})]
    )
    client = PhoenixGuardRegionIngestClientV3(
        base_url="http://127.0.0.1:8793",
        session_id="live-session",
        token="token-secret",
        http_session=cast(Any, http),
    )
    expected = {
        "schema_version": "PG_CAPTURE_SOURCE_V3",
        "state_revision": 7,
        "state": "STALE",
        "source_id": WGC_SOURCE_ID,
        "source_generation": 7,
        "source_type": WGC_SOURCE_TYPE,
        "coordinate_space": WGC_COORDINATE_SPACE,
        "selection_id": "selection-1",
        "sequence_id": "sequence-1",
    }

    lease = client.claim_source(_selection(), expected_source_control=expected)

    assert lease == SourceLeaseV3(8, "lease-8")
    assert http.posts[0]["json"]["expected_source_control"] == expected


@pytest.mark.parametrize("status_code", (409, 410))
def test_ingest_client_hard_rejects_lost_source_lease(status_code: int) -> None:
    http = _FakeHttpSession([_FakeResponse(status_code, {"detail": "superseded"})])
    client = PhoenixGuardRegionIngestClientV3(
        base_url="http://127.0.0.1:8793",
        session_id="live-session",
        token="token-secret",
        http_session=cast(Any, http),
    )

    with pytest.raises(SourceLeaseLostError, match="no longer current"):
        client.claim_source(_selection())


def test_ingest_client_respects_server_retry_after_for_latest_frame() -> None:
    http = _FakeHttpSession(
        [
            _FakeResponse(200, {"source_generation": 3, "source_lease_id": "lease-3"}),
            _FakeResponse(429, {"detail": "too fast"}, {"Retry-After": "4"}),
        ]
    )
    client = PhoenixGuardRegionIngestClientV3(
        base_url="http://127.0.0.1:8793",
        session_id="live-session",
        token="token-secret",
        http_session=cast(Any, http),
    )
    selection = _selection()
    binding = RegionBindingV3(selection=selection, lease=client.claim_source(selection))
    active = ActiveRegionSourceV3(
        local_generation=1,
        binding=binding,
        stream=cast(Any, object()),
    )
    frame = _frame(7)

    with pytest.raises(capture_module.FrameUploadDeferredError) as exc_info:
        client.upload_frame(active, frame, crop_normalized_region_v3(frame.image, selection.normalized_bbox))
    assert exc_info.value.retry_after_sec == 4.25


class _FakeManagerStream:
    def __init__(self, identity: WindowIdentityV3, generation: int) -> None:
        self.identity = identity
        self.generation = generation
        self.slot = LatestFrameSlotV3()
        self.slot.publish(_frame(1))
        self.closed = False
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def wait_first_frame(self, *, timeout: float = 12.0) -> CapturedWindowFrameV3:
        del timeout
        frame = self.slot.latest()
        assert frame is not None
        return frame

    def stop(self) -> None:
        self.stopped = True


class _FakeIngestClient:
    def __init__(self) -> None:
        self.claims: list[RegionSelectionV3] = []
        self.claim_expectations: list[Mapping[str, Any] | None] = []
        self.kills: list[tuple[RegionBindingV3, str]] = []
        self.uploads: list[int] = []
        self.upload_attempts = 0
        self.upload_error: Exception | None = None
        self.upload_errors: list[Exception] = []
        self.source_controls: list[Mapping[str, Any]] = []
        self.source_control: Mapping[str, Any] = {
            "schema_version": "PG_CAPTURE_SOURCE_V3",
            "state_revision": 0,
            "state": "NO_SOURCE",
            "source_id": "",
            "sequence_id": "",
            "source_generation": 0,
            "source_type": "",
            "coordinate_space": "",
            "selection_id": "",
        }
        self.source_reads = 0
        self.fail_claim = False
        self.conditional_claim_error: Exception | None = None
        self.closed = False

    def claim_source(
        self,
        selection: RegionSelectionV3,
        *,
        expected_source_control: Mapping[str, Any] | None = None,
    ) -> SourceLeaseV3:
        self.claims.append(selection)
        self.claim_expectations.append(
            dict(expected_source_control) if expected_source_control is not None else None
        )
        if expected_source_control is not None and self.conditional_claim_error is not None:
            raise self.conditional_claim_error
        if self.fail_claim:
            raise RuntimeError("claim rejected")
        generation = len(self.claims)
        return SourceLeaseV3(generation, f"lease-{generation}")

    def kill_source(self, binding: RegionBindingV3, *, reason: str) -> None:
        self.kills.append((binding, reason))

    def get_source_control(self) -> Mapping[str, Any]:
        self.source_reads += 1
        if self.source_controls:
            return dict(self.source_controls.pop(0))
        return dict(self.source_control)

    def upload_frame(
        self,
        active: ActiveRegionSourceV3,
        frame: CapturedWindowFrameV3,
        roi: Image.Image,
    ) -> Mapping[str, Any]:
        del active, roi
        self.upload_attempts += 1
        if self.upload_errors:
            raise self.upload_errors.pop(0)
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append(frame.frame_id)
        return {"accepted": True}

    def close(self) -> None:
        self.closed = True


def _manager(
    tmp_path: Path,
    *,
    client: _FakeIngestClient,
    streams: list[_FakeManagerStream],
    selector: Any = None,
) -> WindowsRegionCaptureManagerV3:
    identity = _identity()

    def default_selector(
        _identity_value: WindowIdentityV3,
        _frame_value: CapturedWindowFrameV3,
    ) -> tuple[float, float, float, float]:
        return 0.1, 0.2, 0.9, 0.8

    def stream_factory(value: WindowIdentityV3, generation: int) -> Any:
        stream = _FakeManagerStream(value, generation)
        streams.append(stream)
        return stream

    return WindowsRegionCaptureManagerV3(
        ingest_client=client,  # type: ignore[arg-type]
        status_path=tmp_path / "windows_region_capture_status.json",
        identity_reader=lambda _hwnd: identity,
        foreground_reader=lambda: identity,
        selector=selector or default_selector,
        stream_factory=stream_factory,
    )


def test_source_switch_is_transactional_and_status_never_exposes_lease(tmp_path: Path) -> None:
    client = _FakeIngestClient()
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    manager.report_hotkey_registration(True)

    assert manager.select_foreground_source() is True
    first = manager.active_snapshot()
    assert first is not None
    assert streams[0].stopped is False
    assert manager.select_foreground_source() is True
    second = manager.active_snapshot()

    assert second is not None and second is not first
    assert streams[0].stopped is True
    assert streams[1].stopped is False
    status_text = (tmp_path / "windows_region_capture_status.json").read_text(encoding="utf-8")
    assert '"status": "validating"' in status_text
    assert '"source_live": false' in status_text
    assert '"hotkey_registered": true' in status_text
    assert "lease-1" not in status_text
    assert "lease-2" not in status_text
    assert "token-secret" not in status_text
    manager.shutdown()


def test_cancelled_switch_keeps_existing_source_live(tmp_path: Path) -> None:
    client = _FakeIngestClient()
    streams: list[_FakeManagerStream] = []
    selector_calls = 0

    def selector(_identity: WindowIdentityV3, _frame_value: CapturedWindowFrameV3) -> tuple[float, ...]:
        nonlocal selector_calls
        selector_calls += 1
        if selector_calls == 2:
            raise SourceSelectionCancelled("operator cancelled")
        return 0.1, 0.2, 0.9, 0.8

    manager = _manager(tmp_path, client=client, streams=streams, selector=selector)
    assert manager.select_foreground_source() is True
    original = manager.active_snapshot()
    assert manager.select_foreground_source() is False

    assert manager.active_snapshot() is original
    assert streams[0].stopped is False
    assert streams[1].stopped is True
    status = json.loads((tmp_path / "windows_region_capture_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "validating"
    assert status["source_live"] is False
    assert "existing region remains active" in status["message"]
    manager.shutdown()


def test_claim_failure_keeps_existing_source_and_kill_switch_stops_it(tmp_path: Path) -> None:
    client = _FakeIngestClient()
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    assert manager.select_foreground_source() is True
    original = manager.active_snapshot()

    client.fail_claim = True
    assert manager.select_foreground_source() is False
    assert manager.active_snapshot() is original
    assert streams[0].stopped is False
    assert streams[1].stopped is True
    assert manager.kill_active_source(reason="test_kill") is True
    assert streams[0].stopped is True
    assert manager.active_snapshot() is None
    assert client.kills[-1][1] == "test_kill"
    manager.shutdown()


def test_upload_failure_uses_backoff_instead_of_tight_retry(tmp_path: Path) -> None:
    client = _FakeIngestClient()
    client.upload_error = RuntimeError("API temporarily unavailable")
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    assert manager.select_foreground_source() is True
    manager.start()
    time.sleep(0.4)

    assert client.upload_attempts == 1
    active = manager.active_snapshot()
    assert active is not None
    assert active.next_upload_attempt_epoch > time.time()
    status = json.loads((tmp_path / "windows_region_capture_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "degraded"
    manager.shutdown()


def _wait_until(predicate: Any, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def test_lost_lease_reclaims_exact_selection_only_after_server_reports_no_source(
    tmp_path: Path,
) -> None:
    client = _FakeIngestClient()
    client.upload_errors = [SourceLeaseLostError("worker lease disappeared")]
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    assert manager.select_foreground_source() is True
    original = manager.active_snapshot()
    assert original is not None
    original_selection = original.binding.selection

    def reclaimed_current_selection() -> bool:
        current = manager.active_snapshot()
        return bool(current is not None and current.binding.lease.source_lease_id == "lease-2")

    manager.start()
    assert _wait_until(reclaimed_current_selection)
    reclaimed = manager.active_snapshot()

    assert reclaimed is original
    assert reclaimed is not None
    assert reclaimed.binding.selection is original_selection
    assert reclaimed.binding.lease.source_lease_id == "lease-2"
    assert client.source_reads == 1
    assert client.claim_expectations == [None, client.source_control]
    assert streams[0].stopped is False
    manager.shutdown()


def test_lost_lease_hard_stops_without_claim_when_another_source_owns_session(
    tmp_path: Path,
) -> None:
    client = _FakeIngestClient()
    client.upload_errors = [SourceLeaseLostError("source superseded")]
    client.source_control = {
        "schema_version": "PG_CAPTURE_SOURCE_V3",
        "state_revision": 9,
        "state": "LIVE",
        "source_id": "edge-chart-agent",
        "sequence_id": "edge-sequence-9",
        "source_generation": 9,
        "source_type": "browser_tab_roi_capture",
        "coordinate_space": "edge_tab_roi_v1",
        "selection_id": "edge-selection-9",
    }
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    assert manager.select_foreground_source() is True

    manager.start()
    assert _wait_until(lambda: manager.active_snapshot() is None)

    assert len(client.claims) == 1
    assert client.source_reads == 1
    assert streams[0].stopped is True
    assert client.kills == []
    status = json.loads((tmp_path / "windows_region_capture_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "hard_stopped"
    manager.shutdown()


def test_lost_lease_hard_stops_when_conditional_reclaim_loses_interleaving_race(
    tmp_path: Path,
) -> None:
    client = _FakeIngestClient()
    client.upload_error = SourceLeaseLostError("worker lease disappeared")
    client.conditional_claim_error = SourceLeaseLostError("conditional claim lost")
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    assert manager.select_foreground_source() is True

    manager.start()
    assert _wait_until(lambda: manager.active_snapshot() is None)

    assert len(client.claims) == 2
    assert client.claim_expectations[0] is None
    assert client.claim_expectations[1] == client.source_control
    assert client.kills == []
    assert streams[0].stopped is True
    status = json.loads(
        (tmp_path / "windows_region_capture_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "hard_stopped"
    manager.shutdown()


def test_lost_lease_never_reclaims_a_killed_source(tmp_path: Path) -> None:
    client = _FakeIngestClient()
    client.upload_errors = [SourceLeaseLostError("source killed")]
    client.source_control = {
        "schema_version": "PG_CAPTURE_SOURCE_V3",
        "state_revision": 4,
        "state": "KILLED",
        "source_id": "windows-region-capture-v3",
        "sequence_id": "sequence-1",
        "source_generation": 4,
        "source_type": WGC_SOURCE_TYPE,
        "coordinate_space": WGC_COORDINATE_SPACE,
        "selection_id": "selection-1",
    }
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    assert manager.select_foreground_source() is True

    manager.start()
    assert _wait_until(lambda: manager.active_snapshot() is None)

    assert len(client.claims) == 1
    assert streams[0].stopped is True
    assert client.kills == []
    manager.shutdown()


def test_restore_public_binding_preserves_exact_roi_and_reclaims_same_wgc_sequence(
    tmp_path: Path,
) -> None:
    client = _FakeIngestClient()
    selection = _selection()
    client.source_control = {
        "schema_version": "PG_CAPTURE_SOURCE_V3",
        "state_revision": 7,
        "state": "STALE",
        "source_id": "windows-region-capture-v3",
        "sequence_id": selection.sequence_id,
        "source_generation": 7,
        "source_type": WGC_SOURCE_TYPE,
        "coordinate_space": WGC_COORDINATE_SPACE,
        "selection_id": selection.selection_id,
    }
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    saved_payload = RegionBindingV3(
        selection=selection,
        lease=SourceLeaseV3(7, "old-private-lease"),
    ).public_payload()

    assert manager.restore_public_binding(saved_payload) is True
    active = manager.active_snapshot()

    assert active is not None
    assert active.binding.selection.normalized_bbox == selection.normalized_bbox
    assert active.binding.selection.selection_id == selection.selection_id
    assert active.binding.selection.sequence_id == selection.sequence_id
    assert active.binding.selection.reference_frame_size == selection.reference_frame_size
    assert active.binding.lease.source_lease_id == "lease-1"
    assert len(client.claims) == 1
    assert client.source_reads == 2
    assert client.claim_expectations == [client.source_control]
    assert streams[0].started is True
    assert streams[0].stopped is False
    manager.shutdown()


def test_restore_public_binding_refuses_to_start_when_another_source_owns_session(
    tmp_path: Path,
) -> None:
    client = _FakeIngestClient()
    client.source_control = {
        "schema_version": "PG_CAPTURE_SOURCE_V3",
        "state_revision": 9,
        "state": "LIVE",
        "source_id": "edge-chart-agent",
        "sequence_id": "edge-sequence-9",
        "source_generation": 9,
        "source_type": "browser_tab_roi_capture",
        "coordinate_space": "edge_tab_roi_v1",
        "selection_id": "edge-selection-9",
    }
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    saved_payload = RegionBindingV3(
        selection=_selection(),
        lease=SourceLeaseV3(3, "old-private-lease"),
    ).public_payload()

    assert manager.restore_public_binding(saved_payload) is False

    assert manager.active_snapshot() is None
    assert client.claims == []
    assert client.source_reads == 1
    assert streams == []
    manager.shutdown()


def test_kill_switch_cancels_an_open_selector_even_without_active_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeIngestClient()
    streams: list[_FakeManagerStream] = []
    manager = _manager(tmp_path, client=client, streams=streams)
    cancelled = 0

    def cancel_selector() -> int:
        nonlocal cancelled
        cancelled += 1
        return 1

    monkeypatch.setattr(capture_module, "cancel_native_region_selector_v3", cancel_selector)
    assert manager.kill_active_source(reason="operator_hotkey") is False
    assert cancelled == 1
    manager.shutdown()


def test_optional_wgc_runtime_fails_closed_when_exact_pin_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_module, "windows_capture_runtime_version_v3", lambda: "")
    with pytest.raises(WgcRuntimeUnavailableError, match="not installed"):
        require_windows_capture_runtime_v3()


def test_capture_modules_contain_no_focus_force_or_pixel_fallback() -> None:
    root = Path(capture_module.__file__).resolve().parent
    source = "\n".join(
        [
            (root / "windows_region_capture_v3.py").read_text(encoding="utf-8"),
            (root / "native_source_region_overlay.py").read_text(encoding="utf-8"),
        ]
    )
    for forbidden in (
        "SetForegroundWindow",
        "BringWindowToTop",
        "SwitchToThisWindow",
        "PrintWindow",
        "ImageGrab",
        "import mss",
    ):
        assert forbidden not in source
