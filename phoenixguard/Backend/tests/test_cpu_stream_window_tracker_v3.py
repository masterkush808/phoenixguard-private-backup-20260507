# pyright: reportPrivateUsage=false

from __future__ import annotations

# ruff: noqa: SLF001

import copy
from pathlib import Path
from types import SimpleNamespace
import threading
from typing import Any, Callable, Mapping

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

import phoenixguard.mobile_api.app as app_module
import phoenixguard.mobile_api.window_tracker as tracker_module
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
from phoenixguard.vision.cpu_stream_v3 import CPUStreamConfig, CPUStreamObserver


_TITLE = "Pocket Option - Microsoft Edge"
_DESCRIPTOR = {
    "hwnd": 501,
    "title": _TITLE,
    "bbox": [10, 20, 650, 380],
    "width": 640,
    "height": 360,
}


class _CaptureBackend:
    def __init__(self, descriptor: Mapping[str, Any] | None = None) -> None:
        self.descriptor = dict(descriptor or _DESCRIPTOR)
        self.closed = 0

    def list_windows(self, title_query: str | None = None) -> list[dict[str, Any]]:
        _ = title_query
        return [dict(self.descriptor)]

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        return Image.new("RGB", (640, 360), color=(18, 24, 36))

    def close(self) -> None:
        self.closed += 1


class _SnapshotObserver:
    def __init__(self, snapshot: Mapping[str, Any]) -> None:
        self.value = dict(snapshot)

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.value)


def test_windows_capture_prefers_active_input_desktop_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _DesktopApi:
        def OpenInputDesktop(self, *_args: Any) -> int:
            calls.append("input")
            return 202

        def OpenDesktopW(self, *_args: Any) -> int:
            calls.append("default")
            return 101

    monkeypatch.setattr(
        tracker_module.WindowsWindowCaptureBackend,
        "_configure_desktop_api_v3",
        staticmethod(lambda *_args: None),
    )

    handles = tracker_module.WindowsWindowCaptureBackend._open_interactive_desktops_v3(
        _DesktopApi(),
        object(),
        object(),
    )

    assert calls == ["input", "default"]
    assert handles == [202, 101]


def test_cpu_stream_fps_defaults_to_cpu_safe_quarter_hz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_CPU_STREAM_FPS", raising=False)
    assert ContinuousWindowTrackerService._cpu_stream_target_fps_v3() == 0.25

    monkeypatch.setenv("PHOENIXGUARD_CPU_STREAM_FPS", "0.1")
    assert ContinuousWindowTrackerService._cpu_stream_target_fps_v3() == 0.25

    monkeypatch.setenv("PHOENIXGUARD_CPU_STREAM_FPS", "20")
    assert ContinuousWindowTrackerService._cpu_stream_target_fps_v3() == 8.0


def test_cpu_stream_duplicate_recovery_configuration_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "1")
    assert (
        ContinuousWindowTrackerService._cpu_stream_duplicate_recovery_threshold_v3()
        == 2
    )
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "99")
    assert (
        ContinuousWindowTrackerService._cpu_stream_duplicate_recovery_threshold_v3()
        == 10
    )

    monkeypatch.setenv(
        "PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC",
        "0",
    )
    assert (
        ContinuousWindowTrackerService._cpu_stream_duplicate_recovery_min_interval_sec_v3()
        == 1.0
    )
    monkeypatch.setenv(
        "PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC",
        "9999",
    )
    assert (
        ContinuousWindowTrackerService._cpu_stream_duplicate_recovery_min_interval_sec_v3()
        == 300.0
    )


def test_interactive_desktop_capture_uses_native_imagegrab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = tracker_module.WindowsWindowCaptureBackend()
    setattr(backend, "_desktop_thread_state_v3", SimpleNamespace(attached=True))
    expected = Image.new("RGB", (640, 360), color=(12, 34, 56))
    imagegrab_calls: list[tuple[tuple[int, int, int, int] | None, bool]] = []

    def grab(
        *,
        bbox: tuple[int, int, int, int] | None = None,
        all_screens: bool = False,
    ) -> Image.Image:
        imagegrab_calls.append((bbox, all_screens))
        return expected.copy()

    def unexpected_import(_name: str) -> Any:
        raise AssertionError(
            "mss must not initialize after an interactive desktop attach"
        )

    monkeypatch.setattr(tracker_module.ImageGrab, "grab", grab)
    monkeypatch.setattr(
        tracker_module,
        "import_module",
        unexpected_import,
    )

    captured = backend._capture_window_imagegrab(_DESCRIPTOR)

    assert captured.getpixel((0, 0)) == (12, 34, 56)
    assert imagegrab_calls == [((10, 20, 650, 380), True)]


class _NonDisruptiveWindowsBackend(tracker_module.WindowsWindowCaptureBackend):
    def __init__(self) -> None:
        self.descriptor = {
            **_DESCRIPTOR,
            "title": "Broker chart - Microsoft Edge",
        }
        self.offscreen_calls = 0
        self.legacy_capture_calls = 0
        self.activation_calls = 0
        self.desktop_attach_calls = 0

    def _is_windows(self) -> bool:
        return True

    def _ensure_dpi_awareness(self) -> None:
        return None

    def foreground_window_hwnd(self) -> int:
        return 999

    def _ensure_interactive_desktop_for_current_thread_v3(self) -> bool:
        self.desktop_attach_calls += 1
        return True

    def list_windows(self, title_query: str | None = None) -> list[dict[str, Any]]:
        _ = title_query
        return [dict(self.descriptor)]

    def _capture_window_printwindow(
        self,
        hwnd: int,
        descriptor: Mapping[str, Any],
    ) -> Image.Image:
        _ = hwnd, descriptor
        self.offscreen_calls += 1
        return Image.new("RGB", (640, 360), color=(35, 50, 70))

    def _looks_blank(self, image: Image.Image) -> bool:
        _ = image
        return False

    def _looks_browser_content_blank(self, image: Image.Image) -> bool:
        _ = image
        return False

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        self.legacy_capture_calls += 1
        raise AssertionError("stream capture must not enter the activating snapshot chain")

    def _activate_window_for_visible_capture(self, hwnd: int) -> bool:
        _ = hwnd
        self.activation_calls += 1
        raise AssertionError("stream capture must never activate the broker window")


class _RecordingObserver:
    def __init__(self, *, max_frame_pixels: int) -> None:
        self.config = SimpleNamespace(max_frame_pixels=max_frame_pixels)
        self.push_calls = 0

    def push(self, *_args: Any, **_kwargs: Any) -> Any:
        self.push_calls += 1
        raise AssertionError("oversized full-window pixels must be rejected before observation")

    def snapshot(self) -> dict[str, Any]:
        return _snapshot()


class _SequencedStateObserver:
    def __init__(self, states: list[str]) -> None:
        self.config = SimpleNamespace(max_frame_pixels=16_777_216)
        self.states = list(states)
        self.push_calls = 0
        self.last_hash = ""
        self.last_state = ""
        self.last_keyframe_seq = 0
        self.last_keyframe_hash = ""

    def push(
        self,
        image: Image.Image,
        *,
        captured_epoch: float,
        identity: Mapping[str, Any],
        allow_heartbeat: bool,
        allow_study_keyframe: bool,
        force_study_keyframe: bool,
    ) -> Any:
        _ = captured_epoch, identity, allow_heartbeat
        state = self.states[min(self.push_calls, len(self.states) - 1)]
        self.push_calls += 1
        self.last_state = state
        self.last_hash = tracker_module._cpu_stream_frame_hash_v3(image)
        accepted = bool(
            force_study_keyframe
            and allow_study_keyframe
            and state in {"keyframe", "rest", "motion", "material_change"}
        )
        if accepted:
            self.last_keyframe_seq = self.push_calls
            self.last_keyframe_hash = self.last_hash
        return SimpleNamespace(
            accepted_for_study=accepted,
            reason="visible_duplicate_recovery" if accepted else state,
            frame_seq=self.push_calls,
            stream_id="stream-recovery",
            stream_generation=1,
            input_frame_hash=self.last_hash,
            temporal_evidence={"state": state},
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "stream_id": "stream-recovery",
            "stream_generation": 1,
            "last_keyframe_seq": self.last_keyframe_seq,
            "last_keyframe_hash": self.last_keyframe_hash,
            "frame_seq": self.push_calls,
        }


class _DuplicateRecoveryBackend(_CaptureBackend):
    def __init__(
        self,
        *,
        stop_evt: threading.Event,
        stop_after_stream_calls: int,
        live_error: Exception | None = None,
        stop_on_live: bool = False,
        on_stream_call: Any = None,
    ) -> None:
        super().__init__()
        self.stop_evt = stop_evt
        self.stop_after_stream_calls = stop_after_stream_calls
        self.live_error = live_error
        self.stop_on_live = stop_on_live
        self.on_stream_call = on_stream_call
        self.call_order: list[str] = []
        self.stream_calls = 0
        self.live_calls = 0
        self.live_descriptors: list[dict[str, Any]] = []
        self.stream_image = Image.new("RGB", (640, 360), color=(18, 24, 36))
        self.live_image = self.stream_image.copy()
        ImageDraw.Draw(self.live_image).rectangle(
            (260, 140, 320, 220),
            fill=(40, 210, 92),
        )

    def capture_window_stream(
        self,
        descriptor: Mapping[str, Any],
    ) -> Image.Image:
        _ = descriptor
        self.call_order.append("stream")
        self.stream_calls += 1
        if callable(self.on_stream_call):
            self.on_stream_call(self.stream_calls)
        if self.stream_calls >= self.stop_after_stream_calls:
            self.stop_evt.set()
        return self.stream_image.copy()

    def capture_window_live(
        self,
        descriptor: Mapping[str, Any],
    ) -> Image.Image:
        self.call_order.append("live")
        self.live_calls += 1
        self.live_descriptors.append(dict(descriptor))
        if self.stop_on_live:
            self.stop_evt.set()
        if self.live_error is not None:
            raise self.live_error
        return self.live_image.copy()


def _new_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture_backend: _CaptureBackend | None = None,
) -> ContinuousWindowTrackerService:
    monkeypatch.setattr(tracker_module.RUNTIME, "background_warmup_on_launch", False)
    return ContinuousWindowTrackerService(
        root_dir=tmp_path / "tracker",
        capture_backend=capture_backend or _CaptureBackend(),
        tracking_adapter=SimpleNamespace(),
    )


def _snapshot(*, seq: int = 1, frame_hash: str = "frame-1") -> dict[str, Any]:
    return {
        "stream_id": "stream-a",
        "stream_generation": 1,
        "last_keyframe_seq": seq,
        "last_keyframe_hash": frame_hash,
    }


def _lineage(*, seq: int = 1, frame_hash: str = "frame-1") -> dict[str, Any]:
    return {
        "stream_id": "stream-a",
        "stream_generation": 1,
        "frame_seq": seq,
        "input_frame_hash": frame_hash,
        "accepted_reason": "material_change",
        "broker_click_authority": False,
    }


def _control(
    observer: Any,
    backend: _CaptureBackend | None = None,
    *,
    thread: threading.Thread | None = None,
    stop_evt: threading.Event | None = None,
) -> Any:
    stream_stop_evt = stop_evt or threading.Event()
    stream_thread = thread
    if stream_thread is None:
        stream_thread = threading.Thread(target=lambda: None)
        stream_thread.start()
        stream_thread.join(timeout=1.0)
    return tracker_module._CPUStreamControlV3(
        thread=stream_thread,
        stop_evt=stream_stop_evt,
        observer=observer,
        capture_backend=backend or _CaptureBackend(),
        target_fps=4.0,
    )


def _locked_stream_payload(*, focus_enabled: bool = True) -> dict[str, Any]:
    return {
        "session_id": "session-a",
        "tracking_enabled": True,
        "locked_window": dict(_DESCRIPTOR),
        "locked_title": _TITLE,
        "window_query": "Pocket Option",
        "manual_focus_region": {
            "enabled": focus_enabled,
            "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
        },
        "tracking_summary": {
            "detected_market": "EUR/USD OTC",
            "detected_timeframe": "M5",
        },
        "latest_signal": {},
    }


def _session_loader(
    payload: Mapping[str, Any],
) -> Callable[[str], dict[str, Any]]:
    def load_session(_session_id: str) -> dict[str, Any]:
        return copy.deepcopy(dict(payload))

    return load_session


def test_stream_hash_and_lineage_match_the_core_observer_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    try:
        image = Image.new("RGB", (64, 48), color=(31, 42, 53))
        identity = {
            "window_handle": "501",
            "window_title": _TITLE,
            "geometry_hash": "geometry-a",
        }
        observer = CPUStreamObserver(
            CPUStreamConfig(heartbeat_interval_sec=30.0),
            stream_id="stream-a",
        )
        decision = observer.push(image, captured_epoch=10.0, identity=identity)

        assert decision.input_frame_hash == tracker_module._cpu_stream_frame_hash_v3(image)
        lineage = service._cpu_stream_decision_lineage_v3(
            decision,
            captured_epoch=10.0,
            identity=identity,
        )
        assert lineage["input_frame_hash"] == decision.input_frame_hash
        assert lineage["focus_sha256"] == decision.input_frame_hash
        assert lineage["broker_click_authority"] is False
        assert lineage["market_identity_proven"] is False
    finally:
        service.shutdown()


def test_live_descriptor_tolerates_edge_title_drift_but_fails_stable_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable_descriptor = {
        **_DESCRIPTOR,
        "process_id": 1204,
        "class_name": "Chrome_WidgetWin_1",
    }
    backend = _CaptureBackend(stable_descriptor)
    service = _new_service(tmp_path, monkeypatch, capture_backend=backend)
    payload = {
        "locked_window": dict(stable_descriptor),
        "locked_title": _TITLE,
        "window_query": "Pocket Option",
    }
    try:
        resolved = service._cpu_stream_locked_descriptor_v3(payload, backend)
        assert resolved["hwnd"] == _DESCRIPTOR["hwnd"]
        assert resolved["process_id"] == 1204
        assert resolved["class_name"] == "Chrome_WidgetWin_1"

        backend.descriptor["title"] = "Pocket Option and 32 more pages - Microsoft Edge"
        resolved_after_title_drift = service._cpu_stream_locked_descriptor_v3(
            payload,
            backend,
        )
        assert resolved_after_title_drift["hwnd"] == _DESCRIPTOR["hwnd"]
        assert "32 more pages" in resolved_after_title_drift["title"]

        # Geometry is allowed to start a new observer generation; an accepted
        # keyframe still has to match the current geometry/hash before study.
        backend.descriptor.update(
            {
                "bbox": [20, 30, 680, 400],
                "width": 660,
                "height": 370,
            }
        )
        assert service._cpu_stream_locked_descriptor_v3(payload, backend)["bbox"] == [
            20,
            30,
            680,
            400,
        ]

        backend.descriptor = {**stable_descriptor, "process_id": 9999}
        with pytest.raises(tracker_module.CaptureSurfaceUnavailableError):
            service._cpu_stream_locked_descriptor_v3(payload, backend)

        backend.descriptor = {**stable_descriptor, "class_name": "UnrelatedWindow"}
        with pytest.raises(tracker_module.CaptureSurfaceUnavailableError):
            service._cpu_stream_locked_descriptor_v3(payload, backend)

        backend.descriptor = {**stable_descriptor, "hwnd": 777}
        with pytest.raises(tracker_module.CaptureSurfaceUnavailableError):
            service._cpu_stream_locked_descriptor_v3(payload, backend)

        backend.descriptor = dict(stable_descriptor)
        backend.descriptor["title"] = "A different browser tab"
        with pytest.raises(tracker_module.CaptureSurfaceUnavailableError):
            service._cpu_stream_locked_descriptor_v3(payload, backend)
    finally:
        service.shutdown()


def test_pair_timeframe_focus_and_window_geometry_reset_stream_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    try:
        image = Image.new("RGB", (640, 360), color=(20, 30, 40))
        base_payload: dict[str, Any] = {
            "session_id": "pair-session",
            "locked_window": dict(_DESCRIPTOR),
            "locked_title": _TITLE,
            "manual_focus_region": {
                "enabled": True,
                "normalized_bbox": [0.1, 0.2, 0.8, 0.7],
            },
            "tracking_summary": {
                "detected_market": "EUR/USD OTC",
                "detected_timeframe": "M5",
            },
        }
        first_identity = service._cpu_stream_identity_v3(base_payload, _DESCRIPTOR, image)
        changed_payload = copy.deepcopy(base_payload)
        changed_payload["tracking_summary"]["detected_market"] = "GBP/USD OTC"
        changed_payload["tracking_summary"]["detected_timeframe"] = "M1"
        changed_payload["manual_focus_region"]["normalized_bbox"] = [0.2, 0.2, 0.7, 0.7]
        moved_descriptor = {**_DESCRIPTOR, "bbox": [20, 30, 660, 390]}
        second_identity = service._cpu_stream_identity_v3(
            changed_payload,
            moved_descriptor,
            image,
        )

        assert first_identity["market_identity_proven"] is False
        assert first_identity["symbol_hint"] == "EUR/USD OTC"
        assert first_identity["timeframe_hint"] == "M5"
        assert second_identity["geometry_hash"] != first_identity["geometry_hash"]

        observer = CPUStreamObserver(
            CPUStreamConfig(heartbeat_interval_sec=30.0),
            stream_id="stream-a",
        )
        first = observer.push(image, captured_epoch=1.0, identity=first_identity)
        second = observer.push(image, captured_epoch=2.0, identity=second_identity)
        assert first.stream_generation == 1
        assert second.stream_generation == 2
        assert second.accepted_for_study is True
        assert "reset" in second.reason
    finally:
        service.shutdown()


def _selector_surface(*, pair_variant: int, timeframe_variant: int) -> Image.Image:
    image = Image.new("RGB", (640, 360), color=(18, 24, 36))
    draw = ImageDraw.Draw(image)
    pair_x = 43 + pair_variant * 4
    draw.rectangle((pair_x, 42, pair_x + 8, 61), fill="white")
    draw.rectangle((pair_x + 14, 47, pair_x + 26, 54), fill="white")
    timeframe_x = 104 + timeframe_variant * 4
    draw.rectangle((timeframe_x, 43, timeframe_x + 7, 61), fill=(80, 190, 255))
    draw.rectangle((timeframe_x + 10, 47, timeframe_x + 17, 55), fill="white")
    return image


def test_stream_identity_uses_locked_focus_not_animated_browser_chrome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    payload: dict[str, Any] = {
        "session_id": "focused-selector-session",
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.1, 0.2, 0.9, 0.9],
        },
        "tracking_summary": {
            "detected_market": "EUR/USD OTC",
            "detected_timeframe": "M5",
        },
    }
    try:
        first_focus = _selector_surface(pair_variant=0, timeframe_variant=0)
        same_focus = first_focus.copy()
        changed_focus = _selector_surface(pair_variant=1, timeframe_variant=0)
        first_window = Image.new("RGB", (800, 500), color=(18, 24, 36))
        animated_window = first_window.copy()
        ImageDraw.Draw(animated_window).rectangle(
            (120, 20, 260, 55),
            fill=(255, 255, 255),
        )

        first_identity = service._cpu_stream_identity_v3(
            payload,
            _DESCRIPTOR,
            first_window,
            selector_image=first_focus,
        )
        animated_identity = service._cpu_stream_identity_v3(
            payload,
            _DESCRIPTOR,
            animated_window,
            selector_image=same_focus,
        )
        switched_identity = service._cpu_stream_identity_v3(
            payload,
            _DESCRIPTOR,
            animated_window,
            selector_image=changed_focus,
        )

        assert (
            first_identity["selector_visual_fingerprint"]
            == animated_identity["selector_visual_fingerprint"]
        )
        assert (
            switched_identity["selector_visual_fingerprint"]
            != first_identity["selector_visual_fingerprint"]
        )
        assert first_identity["geometry_hash"] == animated_identity["geometry_hash"]
    finally:
        service.shutdown()


def test_visible_pair_selector_change_resets_identity_with_unchanged_cached_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    payload: dict[str, Any] = {
        "session_id": "pair-session",
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.2, 0.2, 0.8, 0.8],
        },
        "tracking_summary": {
            "detected_market": "EUR/USD OTC",
            "detected_timeframe": "M5",
        },
    }
    try:
        first_identity = service._cpu_stream_identity_v3(
            payload,
            _DESCRIPTOR,
            _selector_surface(pair_variant=0, timeframe_variant=0),
        )
        second_identity = service._cpu_stream_identity_v3(
            payload,
            _DESCRIPTOR,
            _selector_surface(pair_variant=1, timeframe_variant=0),
        )

        assert first_identity["symbol_hint"] == second_identity["symbol_hint"]
        assert first_identity["selector_visual_fingerprint"]
        assert (
            first_identity["selector_visual_fingerprint"]
            != second_identity["selector_visual_fingerprint"]
        )
        observer = CPUStreamObserver(stream_id="pair-selector")
        focus = Image.new("RGB", (80, 60), color=(20, 30, 40))
        observer.push(focus, captured_epoch=1.0, identity=first_identity)
        switched = observer.push(focus, captured_epoch=2.0, identity=second_identity)
        assert switched.reason == "identity_reset"
        assert switched.stream_generation == 2
    finally:
        service.shutdown()


def test_visible_timeframe_chip_change_resets_identity_with_unchanged_cached_timeframe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    payload: dict[str, Any] = {
        "session_id": "timeframe-session",
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.2, 0.2, 0.8, 0.8],
        },
        "tracking_summary": {
            "detected_market": "EUR/USD OTC",
            "detected_timeframe": "M5",
        },
    }
    try:
        first_identity = service._cpu_stream_identity_v3(
            payload,
            _DESCRIPTOR,
            _selector_surface(pair_variant=0, timeframe_variant=0),
        )
        second_identity = service._cpu_stream_identity_v3(
            payload,
            _DESCRIPTOR,
            _selector_surface(pair_variant=0, timeframe_variant=1),
        )

        assert first_identity["timeframe_hint"] == second_identity["timeframe_hint"] == "M5"
        assert (
            first_identity["selector_visual_fingerprint"]
            != second_identity["selector_visual_fingerprint"]
        )
        observer = CPUStreamObserver(stream_id="timeframe-selector")
        focus = Image.new("RGB", (80, 60), color=(20, 30, 40))
        observer.push(focus, captured_epoch=1.0, identity=first_identity)
        switched = observer.push(focus, captured_epoch=2.0, identity=second_identity)
        assert switched.reason == "identity_reset"
        assert switched.stream_generation == 2
    finally:
        service.shutdown()


def test_publication_guard_rejects_a_superseded_keyframe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    observer = _SnapshotObserver(_snapshot())
    control = _control(observer)
    service._cpu_streams["session-a"] = control
    try:
        with service._cpu_stream_publication_guard_v3("session-a", _lineage()) as current:
            assert current is True

        observer.value = _snapshot(seq=2, frame_hash="frame-2")
        with service._cpu_stream_publication_guard_v3("session-a", _lineage()) as current:
            assert current is False
        assert control.stale_generation_drops == 1
    finally:
        service.shutdown()


def test_duplicate_health_update_never_publishes_or_replaces_trade_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    observer = _SnapshotObserver(_snapshot())
    control = _control(observer)
    service._cpu_streams["session-a"] = control
    save_calls = 0

    def unexpected_save(_payload: Mapping[str, Any]) -> None:
        nonlocal save_calls
        save_calls += 1

    monkeypatch.setattr(service, "_save_session", unexpected_save)
    try:
        assert service._record_cpu_stream_duplicate_v3("session-a", _lineage()) is True
        assert save_calls == 0
        assert control.last_lineage["analysis_result"] == "DUPLICATE_VISUAL_FRAME_NO_NEW_EVIDENCE"
    finally:
        service.shutdown()


def test_pending_keyframe_slot_is_latest_wins_and_never_grows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    observer = _SnapshotObserver(_snapshot())
    control = _control(observer)
    first = tracker_module._CPUStreamKeyframeV3(
        image=Image.new("RGB", (10, 10)),
        captured_epoch=1.0,
        source={},
        lineage=_lineage(),
    )
    second = tracker_module._CPUStreamKeyframeV3(
        image=Image.new("RGB", (10, 10), color="white"),
        captured_epoch=2.0,
        source={},
        lineage=_lineage(seq=2, frame_hash="frame-2"),
    )
    control.latest_keyframe = second
    service._cpu_streams["session-a"] = control
    try:
        service._requeue_cpu_stream_keyframe_v3("session-a", first)
        assert control.latest_keyframe is second
        assert control.coalesced_keyframe_drops == 1
        assert service._take_cpu_stream_keyframe_v3("session-a") is second
        assert control.latest_keyframe is None
        assert control.in_flight_keyframe is second
        service._finish_cpu_stream_keyframe_v3("session-a", second)
        assert control.in_flight_keyframe is None
    finally:
        service.shutdown()


def test_only_one_local_cpu_stream_can_be_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    stop_evt = threading.Event()
    live_thread = threading.Thread(target=stop_evt.wait, daemon=True)
    live_thread.start()
    service._cpu_streams["first"] = _control(
        _SnapshotObserver(_snapshot()),
        thread=live_thread,
        stop_evt=stop_evt,
    )
    monkeypatch.setattr(service, "_cpu_stream_requested_v3", lambda: True)
    try:
        service._ensure_cpu_stream_v3("second")
        assert "second" not in service._cpu_streams
        failure = service._cpu_stream_failures["second"]
        assert failure["status"] == "capacity_snapshot_fallback"
        assert "already assigned" in failure["last_error"]
    finally:
        service.shutdown()


def test_stream_backend_closes_on_normal_loop_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    backend = _CaptureBackend()
    control = _control(_SnapshotObserver(_snapshot()), backend)
    def no_session(_session_id: str) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(service, "_load_session", no_session)
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)
        assert backend.closed == 1
        assert control.cleanup_completed is True
        assert control.status == "stopped"
    finally:
        service.shutdown()


def test_failed_stream_start_closes_the_separate_capture_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    reader = _new_service(tmp_path, monkeypatch)
    stream_backend = _CaptureBackend()
    service._cpu_stream_capture_backend_factory = lambda: stream_backend

    def fail_observer(**_kwargs: Any) -> Any:
        raise RuntimeError("observer failed")

    service._cpu_stream_observer_factory = fail_observer
    monkeypatch.setattr(service, "_cpu_stream_requested_v3", lambda: True)
    monkeypatch.setattr(reader, "_cpu_stream_requested_v3", lambda: True)
    try:
        service._ensure_cpu_stream_v3("session-a")
        assert stream_backend.closed == 1
        assert service._cpu_stream_failures["session-a"]["status"] == "fallback_snapshot"
        reader_health = reader.cpu_stream_health_v3("session-a")
        assert reader_health["status"] == "fallback_snapshot"
        assert reader_health["available"] is False
        assert "observer failed" in reader_health["last_error"]
        assert reader_health["broker_click_authority"] is False
    finally:
        service.shutdown()
        reader.shutdown()


def test_stream_capture_never_enters_focus_activating_snapshot_fallback() -> None:
    backend = _NonDisruptiveWindowsBackend()

    image = backend.capture_window_stream(backend.descriptor)

    assert image.size == (640, 360)
    assert backend.offscreen_calls == 1
    assert backend.legacy_capture_calls == 0
    assert backend.activation_calls == 0
    assert backend.desktop_attach_calls == 1


def test_background_only_duplicate_streak_never_uses_visible_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", raising=False)
    stop_evt = threading.Event()
    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=4,
    )
    observer = _SequencedStateObserver(
        ["keyframe", "duplicate", "duplicate", "duplicate"]
    )
    service = _new_service(tmp_path, monkeypatch)
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    service._cpu_streams["session-a"] = control
    payload = _locked_stream_payload()
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setenv(
        "PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC",
        "1",
    )
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == ["stream", "stream", "stream", "stream"]
        assert backend.live_calls == 0
        assert control.duplicate_recovery_attempts == 0
        assert control.duplicate_recovery_successes == 0
        assert control.duplicate_recovery_errors == 0
        assert control.duplicate_recovery_pending is False
        recovery = control.last_observation_lineage["duplicate_recovery_v3"]
        assert recovery["background_capture_only"] is True
        assert recovery["attempted"] is False
    finally:
        service.shutdown()


def test_duplicate_streak_uses_one_visible_recovery_then_resumes_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    stop_evt = threading.Event()
    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=4,
    )
    observer = _SequencedStateObserver(
        [
            "keyframe",
            "duplicate",
            "duplicate",
            "material_change",
            "material_change",
        ]
    )
    service = _new_service(tmp_path, monkeypatch)
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    service._cpu_streams["session-a"] = control
    payload = _locked_stream_payload()
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setenv(
        "PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC",
        "1",
    )
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == [
            "stream",
            "stream",
            "stream",
            "live",
            "stream",
        ]
        assert backend.live_calls == 1
        assert backend.live_descriptors[0]["hwnd"] == _DESCRIPTOR["hwnd"]
        assert control.duplicate_recovery_attempts == 1
        assert control.duplicate_recovery_successes == 1
        assert control.duplicate_recovery_errors == 0
        assert control.duplicate_recovery_pending is False
        recovery_lineage = control.last_duplicate_recovery_lineage
        assert recovery_lineage["capture_mode"] == "visible_duplicate_recovery"
        assert recovery_lineage["window_handle"] == str(_DESCRIPTOR["hwnd"])
        assert recovery_lineage["duplicate_recovery_v3"]["attempted"] is True
        assert recovery_lineage["duplicate_recovery_v3"]["succeeded"] is True
        assert control.last_observation_lineage["capture_mode"] == "offscreen_stream"
    finally:
        service.shutdown()


def test_pending_recovery_refreshes_pair_and_focus_identity_before_live_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    stop_evt = threading.Event()
    payload = _locked_stream_payload()

    def change_locked_study_identity(stream_call: int) -> None:
        if stream_call != 3:
            return
        payload["tracking_summary"]["detected_market"] = "GBP/JPY OTC"
        payload["manual_focus_region"]["normalized_bbox"] = [
            0.2,
            0.2,
            0.8,
            0.8,
        ]

    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=99,
        stop_on_live=True,
        on_stream_call=change_locked_study_identity,
    )
    observer = _SequencedStateObserver(
        ["keyframe", "duplicate", "duplicate", "material_change"]
    )
    service = _new_service(tmp_path, monkeypatch)
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    service._cpu_streams["session-a"] = control
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == ["stream", "stream", "stream", "live"]
        assert backend.live_descriptors[0]["hwnd"] == _DESCRIPTOR["hwnd"]
        recovery_lineage = control.last_duplicate_recovery_lineage
        assert recovery_lineage["symbol_hint"] == "GBP/JPY OTC"
        assert recovery_lineage["focus_normalized_bbox"] == [
            0.2,
            0.2,
            0.8,
            0.8,
        ]
    finally:
        service.shutdown()


@pytest.mark.parametrize("occupied_slot", ("latest_keyframe", "in_flight_keyframe"))
def test_pending_duplicate_recovery_waits_for_the_one_study_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    occupied_slot: str,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    stop_evt = threading.Event()
    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=1,
    )
    observer = _SequencedStateObserver(["duplicate"])
    service = _new_service(tmp_path, monkeypatch)
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    control.duplicate_recovery_pending = True
    control.duplicate_streak_frames = 2
    setattr(
        control,
        occupied_slot,
        tracker_module._CPUStreamKeyframeV3(
            image=Image.new("RGB", (10, 10)),
            captured_epoch=1.0,
            source={},
            lineage=_lineage(),
        ),
    )
    service._cpu_streams["session-a"] = control
    payload = _locked_stream_payload()
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == ["stream"]
        assert backend.live_calls == 0
        assert control.duplicate_recovery_attempts == 0
        assert control.duplicate_recovery_pending is True
        assert control.last_observation_lineage["duplicate_recovery_v3"][
            "pending"
        ] is True
    finally:
        service.shutdown()


def test_duplicate_recovery_failure_is_throttled_and_stream_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    stop_evt = threading.Event()
    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=5,
        live_error=tracker_module.CaptureSurfaceUnavailableError(
            "foreground identity changed"
        ),
    )
    observer = _SequencedStateObserver(
        ["keyframe", "duplicate", "duplicate", "duplicate", "duplicate"]
    )
    service = _new_service(tmp_path, monkeypatch)
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    service._cpu_streams["session-a"] = control
    payload = _locked_stream_payload()
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setenv(
        "PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC",
        "120",
    )
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == [
            "stream",
            "stream",
            "stream",
            "live",
            "stream",
            "stream",
        ]
        assert control.duplicate_recovery_attempts == 1
        assert control.duplicate_recovery_successes == 0
        assert control.duplicate_recovery_errors == 1
        assert control.capture_errors == 0
        assert control.duplicate_recovery_pending is False
        assert "ordinary offscreen capture resumes" in (
            control.last_duplicate_recovery_error
        )
        assert control.last_observation_lineage["capture_mode"] == "offscreen_stream"
    finally:
        service.shutdown()


def test_post_capture_recovery_validation_error_uses_recovery_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    stop_evt = threading.Event()
    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=4,
    )
    backend.live_image = Image.new("RGB", (800, 800), color=(30, 40, 50))
    observer = _SequencedStateObserver(
        ["keyframe", "duplicate", "duplicate", "duplicate"]
    )
    observer.config.max_frame_pixels = 300_000
    service = _new_service(tmp_path, monkeypatch)
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    service._cpu_streams["session-a"] = control
    payload = _locked_stream_payload()
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setenv(
        "PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC",
        "120",
    )
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == [
            "stream",
            "stream",
            "stream",
            "live",
            "stream",
        ]
        assert control.duplicate_recovery_attempts == 1
        assert control.duplicate_recovery_successes == 0
        assert control.duplicate_recovery_errors == 1
        assert control.capture_errors == 0
        assert "bounded pixel budget" in control.last_duplicate_recovery_error
    finally:
        service.shutdown()


@pytest.mark.parametrize("live_pixels_changed", (True, False))
def test_real_observer_admits_only_fresh_visible_recovery_pixels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_pixels_changed: bool,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    stop_evt = threading.Event()
    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=99,
        stop_on_live=True,
    )
    if not live_pixels_changed:
        backend.live_image = backend.stream_image.copy()
    service = _new_service(tmp_path, monkeypatch)
    payload = _locked_stream_payload()
    observer = CPUStreamObserver(
        CPUStreamConfig(
            keyframe_min_interval_sec=0.0,
            heartbeat_interval_sec=30.0,
        ),
        stream_id="real-recovery",
    )
    identity = service._cpu_stream_identity_v3(
        payload,
        backend.descriptor,
        backend.stream_image,
    )
    observer.push(
        backend.stream_image,
        captured_epoch=tracker_module._now_epoch() - 5.0,
        identity=identity,
    )
    initial_generation = observer.stream_generation
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    service._cpu_streams["session-a"] = control
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == ["stream", "stream", "live"]
        assert control.duplicate_recovery_attempts == 1
        assert control.duplicate_recovery_successes == int(live_pixels_changed)
        assert control.duplicate_recovery_errors == 0
        recovery_lineage = control.last_duplicate_recovery_lineage
        assert recovery_lineage["capture_mode"] == "visible_duplicate_recovery"
        assert recovery_lineage["duplicate_recovery_v3"]["succeeded"] is (
            live_pixels_changed
        )
        assert recovery_lineage["stream_generation"] == initial_generation
        if live_pixels_changed:
            assert control.latest_keyframe is not None
            assert recovery_lineage["accepted_reason"] == (
                "visible_duplicate_recovery"
            )
            assert recovery_lineage["temporal_evidence"]["keyframe"][
                "reason"
            ] == "visible_duplicate_recovery"
            assert recovery_lineage["duplicate_recovery_v3"][
                "forced_keyframe"
            ] is True
            expected_hash = tracker_module._cpu_stream_frame_hash_v3(
                backend.live_image
            )
            assert control.latest_keyframe.lineage["input_frame_hash"] == (
                expected_hash
            )
            assert control.latest_keyframe.source["cpu_stream_lineage_v3"] == (
                control.latest_keyframe.lineage
            )
        else:
            assert control.latest_keyframe is None
        observer_counters = observer.snapshot()["counters"]
        assert isinstance(observer_counters, dict)
        assert observer_counters["manual_resets"] == 0
    finally:
        service.shutdown()


@pytest.mark.parametrize(
    ("states", "focus_enabled"),
    (
        (["keyframe", "rest", "rest", "material_change"], True),
        (["keyframe", "duplicate", "duplicate", "duplicate"], False),
    ),
)
def test_duplicate_recovery_is_not_armed_by_live_rest_or_without_chart_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    states: list[str],
    focus_enabled: bool,
) -> None:
    stop_evt = threading.Event()
    backend = _DuplicateRecoveryBackend(
        stop_evt=stop_evt,
        stop_after_stream_calls=4,
    )
    observer = _SequencedStateObserver(states)
    service = _new_service(tmp_path, monkeypatch)
    control = _control(observer, backend, stop_evt=stop_evt)
    control.target_fps = 8.0
    service._cpu_streams["session-a"] = control
    payload = _locked_stream_payload(focus_enabled=focus_enabled)
    if not focus_enabled:
        def unlocked_focus(_value: Any) -> dict[str, Any]:
            return {
                "enabled": False,
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            }

        monkeypatch.setattr(
            tracker_module,
            "_public_manual_focus_region",
            unlocked_focus,
        )
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setattr(
        service,
        "_load_session",
        _session_loader(payload),
    )
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)

        assert backend.call_order == ["stream", "stream", "stream", "stream"]
        assert control.duplicate_recovery_attempts == 0
        assert control.duplicate_recovery_successes == 0
        assert control.duplicate_recovery_errors == 0
        assert control.duplicate_recovery_pending is False
        assert control.capture_errors == 0
        assert observer.push_calls == 4
    finally:
        service.shutdown()


def test_stream_capture_accepts_benign_edge_tab_count_title_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _NonDisruptiveWindowsBackend()
    locked = {
        **backend.descriptor,
        "title": "The Most Innovative Trading Platform and 31 more pages - Microsoft Edge",
        "window_query": "The Most Innovative Trading Platform",
    }
    backend.descriptor["title"] = (
        "The Most Innovative Trading Platform and 32 more pages - Microsoft Edge"
    )

    def looks_like_pocket_surface(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(
        tracker_module,
        "_capture_looks_like_pocket_option_visible_surface",
        looks_like_pocket_surface,
    )

    image = backend._capture_window_stream_offscreen(locked)

    assert image.size == (640, 360)
    assert backend.offscreen_calls == 1
    backend.descriptor["title"] = "Unrelated browser settings"
    with pytest.raises(tracker_module.CaptureSurfaceUnavailableError):
        backend._capture_window_stream_offscreen(locked)


def test_stale_local_keyframe_drop_does_not_mutate_current_session_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    observer = _SnapshotObserver(_snapshot())
    control = _control(observer)
    service._cpu_streams["session-a"] = control
    surface_mutations = 0

    def reject_stale(*_args: Any, **_kwargs: Any) -> None:
        raise tracker_module.StaleCPUStreamKeyframeError("pair changed")

    def unexpected_surface_mutation(_session_id: str, _message: str) -> None:
        nonlocal surface_mutations
        surface_mutations += 1

    monkeypatch.setattr(service, "_capture_and_analyze_claimed", reject_stale)
    monkeypatch.setattr(service, "_mark_capture_surface_unavailable", unexpected_surface_mutation)
    try:
        processed = service._capture_and_analyze(
            "session-a",
            force=True,
            external_window_image=Image.new("RGB", (10, 10)),
            external_source={"cpu_stream_lineage_v3": _lineage()},
            external_capture_epoch=1.0,
        )

        assert processed is True
        assert surface_mutations == 0
        assert control.stale_generation_drops == 1
        assert control.last_lineage["analysis_result"] == "STALE_KEYFRAME_REJECTED"
    finally:
        service.shutdown()


def test_starting_stream_reservation_blocks_second_stream_before_thread_is_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    starting = _control(_SnapshotObserver(_snapshot()))
    starting.status = "starting"
    service._cpu_streams["first"] = starting
    monkeypatch.setattr(service, "_cpu_stream_requested_v3", lambda: True)
    try:
        service._ensure_cpu_stream_v3("second")
        assert "second" not in service._cpu_streams
        assert service._cpu_stream_failures["second"]["status"] == "capacity_snapshot_fallback"
    finally:
        service.shutdown()


def test_full_window_pixel_bound_applies_before_small_focus_crop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_evt = threading.Event()
    descriptor = {
        "hwnd": 701,
        "title": "Broker chart - Microsoft Edge",
        "bbox": [0, 0, 640, 360],
        "width": 640,
        "height": 360,
    }

    class OversizeBackend(_CaptureBackend):
        def capture_window_stream(self, _descriptor: Mapping[str, Any]) -> Image.Image:
            stop_evt.set()
            return Image.new("RGB", (640, 360), color=(20, 30, 40))

    backend = OversizeBackend(descriptor)
    service = _new_service(tmp_path, monkeypatch)
    observer = _RecordingObserver(max_frame_pixels=100_000)
    control = _control(observer, backend, stop_evt=stop_evt)
    payload: dict[str, Any] = {
        "session_id": "session-a",
        "tracking_enabled": True,
        "locked_window": dict(descriptor),
        "locked_title": descriptor["title"],
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.4, 0.4, 0.6, 0.6],
        },
        "tracking_summary": {},
        "latest_signal": {},
    }
    def load_session(_session_id: str) -> dict[str, Any]:
        return copy.deepcopy(payload)

    monkeypatch.setattr(service, "_load_session", load_session)
    try:
        service._cpu_stream_producer_loop_v3("session-a", control)
        assert observer.push_calls == 0
        assert control.capture_errors == 1
        assert control.latest_keyframe is None
        assert backend.closed == 1
    finally:
        service.shutdown()


def test_degraded_stream_remains_owner_of_duplicate_watchdog_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    stop_evt = threading.Event()
    thread = threading.Thread(target=stop_evt.wait, daemon=True)
    thread.start()
    control = _control(
        _SnapshotObserver(_snapshot()),
        thread=thread,
        stop_evt=stop_evt,
    )
    control.status = "degraded_snapshot_fallback"
    service._cpu_streams["session-a"] = control
    try:
        assert service._cpu_stream_active_v3("session-a") is True
    finally:
        service.shutdown()


def test_stream_status_sidecar_is_bounded_and_cross_process_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _new_service(tmp_path, monkeypatch)
    reader = _new_service(tmp_path, monkeypatch)
    stop_evt = threading.Event()
    thread = threading.Thread(target=stop_evt.wait, daemon=True)
    thread.start()
    control = _control(
        _SnapshotObserver(_snapshot()),
        thread=thread,
        stop_evt=stop_evt,
    )
    control.status = "degraded_snapshot_fallback"
    control.started_epoch = tracker_module._now_epoch()
    control.observed_frames = 17
    control.capture_errors = 3
    control.duplicate_streak_frames = 2
    control.duplicate_recovery_attempts = 2
    control.duplicate_recovery_successes = 1
    control.duplicate_recovery_errors = 1
    control.last_duplicate_recovery_lineage = {
        "capture_mode": "visible_duplicate_recovery",
        "input_frame_hash": "recovery-frame-hash",
    }
    control.last_error = "Waiting for an exact broker window."
    writer._cpu_streams["session-a"] = control
    monkeypatch.setattr(writer, "_cpu_stream_requested_v3", lambda: True)
    monkeypatch.setattr(reader, "_cpu_stream_requested_v3", lambda: True)
    try:
        writer._publish_cpu_stream_status_v3("session-a")

        health = reader.cpu_stream_health_v3("session-a")
        status_path = writer._cpu_stream_status_path_v3("session-a")

        assert status_path.is_file()
        assert list(status_path.parent.glob("cpu_stream_v3*.json")) == [status_path]
        assert health["requested"] is True
        assert health["available"] is True
        assert health["status"] == "degraded_snapshot_fallback"
        assert health["observed_frames"] == 17
        assert health["capture_errors"] == 3
        assert health["duplicate_streak_frames"] == 2
        assert health["duplicate_recovery_attempts"] == 2
        assert health["duplicate_recovery_successes"] == 1
        assert health["duplicate_recovery_errors"] == 1
        assert health["last_duplicate_recovery_lineage"] == {
            "capture_mode": "visible_duplicate_recovery",
            "input_frame_hash": "recovery-frame-hash",
        }
        assert health["keyframe_slot_capacity"] == 1
        assert health["broker_click_authority"] is False
        assert not list(status_path.parent.glob("*.png"))
        assert not list(status_path.parent.glob("*.jpg"))
    finally:
        writer.shutdown()
        reader.shutdown()


def test_stale_stream_sidecar_fails_closed_and_reasserts_safety_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "_cpu_stream_requested_v3", lambda: True)
    status_path = service._cpu_stream_status_path_v3("session-a")
    tracker_module._write_json_atomic(
        status_path,
        {
            "schema_version": "PG_CPU_STREAM_RUNTIME_V3",
            "session_id": "session-a",
            "requested": False,
            "enabled": False,
            "available": True,
            "status": "active",
            "mode": "event_driven_cpu_stream",
            "keyframe_slot_capacity": 99,
            "broker_click_authority": True,
            "status_updated_epoch": tracker_module._now_epoch() - 10.0,
        },
    )
    try:
        health = service.cpu_stream_health_v3("session-a")

        assert health["requested"] is True
        assert health["enabled"] is True
        assert health["available"] is False
        assert health["status"] == "stale_snapshot_fallback"
        assert health["mode"] == "snapshot_fallback"
        assert health["keyframe_slot_capacity"] == 1
        assert health["broker_click_authority"] is False
    finally:
        service.shutdown()


def test_public_session_compact_read_attaches_cross_process_stream_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    compact = {
        "session_id": "session-a",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 0,
    }
    expected = {
        "schema_version": "PG_CPU_STREAM_RUNTIME_V3",
        "requested": True,
        "enabled": True,
        "available": True,
        "status": "degraded_snapshot_fallback",
        "mode": "snapshot_fallback",
        "target_fps": 4.0,
        "keyframe_slot_capacity": 1,
        "can_grant_entry_permission": False,
        "execution_authority": False,
        "broker_click_authority": False,
    }
    observed_persisted: dict[str, Any] = {}

    def stream_health(_session_id: str, persisted: Mapping[str, Any]) -> dict[str, Any]:
        observed_persisted.update(persisted)
        return dict(expected)

    def compact_snapshot(
        session_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        return dict(compact) if session_id == "session-a" else None

    monkeypatch.setattr(
        app_module,
        "_direct_window_tracker_compact_session_snapshot",
        compact_snapshot,
    )
    monkeypatch.setattr(service, "cpu_stream_health_v3", stream_health)
    try:
        response = TestClient(create_app(window_tracker_service=service)).get(
            "/v1/mobile/window-tracker/sessions/session-a"
        )

        assert response.status_code == 200
        assert observed_persisted == compact
        assert response.json()["cpu_stream_v3"] == expected
    finally:
        service.shutdown()


def test_shutdown_during_stream_construction_prevents_late_thread_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _new_service(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    backend = _CaptureBackend()

    def blocking_factory() -> _CaptureBackend:
        entered.set()
        assert release.wait(timeout=5.0)
        return backend

    service._cpu_stream_capture_backend_factory = blocking_factory
    def observer_factory(**_kwargs: Any) -> _SnapshotObserver:
        return _SnapshotObserver(_snapshot())

    service._cpu_stream_observer_factory = observer_factory
    monkeypatch.setattr(service, "_cpu_stream_requested_v3", lambda: True)
    starter = threading.Thread(
        target=service._ensure_cpu_stream_v3,
        args=("session-a",),
        daemon=True,
    )
    starter.start()
    assert entered.wait(timeout=2.0)

    service.shutdown()
    release.set()
    starter.join(timeout=5.0)

    assert starter.is_alive() is False
    assert "session-a" not in service._cpu_streams
    assert backend.closed == 1
