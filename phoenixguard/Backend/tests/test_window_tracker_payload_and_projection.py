from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any, Mapping

from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService


class FakeAdapter:
    def build_memory_projection(
        self,
        surface_image: Image.Image,
        tracking_summary: dict[str, Any],
        latest_signal: dict[str, Any],
        mode: str,
        session_payload: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        return {"status": "ready", "message": "ok"}

    def render_memory_projection_artifacts(
        self,
        artifact_dir: str | Path,
        artifact_stem: str,
        *,
        surface_image: Image.Image,
        tracking_summary: dict[str, Any],
        latest_signal: dict[str, Any],
        projection_payload: dict[str, Any],
    ) -> dict[str, str]:
        # create two artifact files in artifact_dir and return filenames
        proj = Path(artifact_dir) / "proj.png"
        ref = Path(artifact_dir) / "ref.png"
        proj.write_bytes(b"PNGDATA")
        ref.write_bytes(b"PNGREF")
        return {"projection": "proj.png", "reference": "ref.png"}


class BlockingProjectionAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def build_memory_projection(
        self,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        mode: str,
        session_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        _ = surface_image, tracking_summary, latest_signal, mode, session_payload
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=5.0):
            raise TimeoutError("test projection release timed out")
        return {"status": "ready", "summary": "forecast ready"}


class FailingProjectionAdapter(FakeAdapter):
    def build_memory_projection(
        self,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        mode: str,
        session_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        _ = surface_image, tracking_summary, latest_signal, mode, session_payload
        raise RuntimeError("private backend failure detail")


def _wait_for_terminal_action(
    service: ContinuousWindowTrackerService,
    session_id: str,
    request_id: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        action = service.get_memory_projection_action(session_id, request_id)
        if bool(action["terminal"]):
            return action
        time.sleep(0.01)
    raise AssertionError(f"forecast action {request_id} did not complete")


def testpublic_session_payload_normalizes_chart_overlay_paths(tmp_path: Path) -> None:
    svc = ContinuousWindowTrackerService(root_dir=tmp_path / "wt")
    payload: dict[str, Any] = {"session_id": "s1", "last_display_chart_path": "display.png", "last_full_overlay_path": "overlay.png", "last_frame_path": "frame.png", "manual_focus_region": {"enabled": True}}
    public = svc.public_session_payload(payload)
    assert public.get("last_chart_path") == "display.png"
    assert public.get("last_overlay_path") == "overlay.png"
    assert public.get("last_window_path") == "frame.png"


def test_run_memory_projection_resolves_adapter_artifacts(tmp_path: Path) -> None:
    svc = ContinuousWindowTrackerService(root_dir=tmp_path / "wt", tracking_adapter=FakeAdapter())
    session_id = "s_proj"
    # create a chart file and session payload
    chart_path = tmp_path / "chart.png"
    img = Image.new("RGB", (10, 10), color=(255, 255, 255))
    img.save(chart_path)
    payload: dict[str, Any] = {"session_id": session_id, "manual_focus_region": {"enabled": True, "normalized_bbox": [0, 0, 1, 1]}, "frame_index": 1, "last_chart_path": str(chart_path)}
    svc.save_session(payload)
    result = svc.run_memory_projection(session_id, mode="future")
    mem = result.get("memory_projection_future") or result.get("memory_projection_current")
    assert mem is not None
    assert "projection_image_path" in mem and mem["projection_image_path"]
    assert "reference_image_path" in mem and mem["reference_image_path"]
    # files should exist
    assert Path(mem["projection_image_path"]).exists()
    assert Path(mem["reference_image_path"]).exists()


def test_api_show_future_and_artifact_endpoint(tmp_path: Path) -> None:
    svc = ContinuousWindowTrackerService(root_dir=tmp_path / "wt", tracking_adapter=FakeAdapter())
    session_id = "s_api"
    chart_path = tmp_path / "chart.png"
    img = Image.new("RGB", (8, 8), color=(0, 0, 0))
    img.save(chart_path)
    payload: dict[str, Any] = {"session_id": session_id, "manual_focus_region": {"enabled": True, "normalized_bbox": [0, 0, 1, 1]}, "frame_index": 1, "last_chart_path": str(chart_path)}
    svc.save_session(payload)
    app = create_app(window_tracker_service=svc)
    client = TestClient(app)
    resp = client.post(f"/v1/mobile/window-tracker/sessions/{session_id}/show-future")
    assert resp.status_code == 202
    data = resp.json()
    assert data["schema_version"] == "PG_FORECAST_ACTION_V1"
    assert data["mode"] == "future"
    action = _wait_for_terminal_action(svc, session_id, str(data["request_id"]))
    assert action["status"] == "ready"
    art = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-projection")
    assert art.status_code == 200
    assert art.content and len(art.content) > 0


def test_forecast_action_api_is_nonblocking_narrow_deduplicated_and_cached(tmp_path: Path) -> None:
    adapter = BlockingProjectionAdapter()
    svc = ContinuousWindowTrackerService(root_dir=tmp_path / "wt", tracking_adapter=adapter)
    session_id = "forecast-contract"
    chart_path = tmp_path / "chart.png"
    Image.new("RGB", (16, 12), color=(18, 24, 32)).save(chart_path)
    svc.save_session(
        {
            "session_id": session_id,
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0, 0, 1, 1]},
            "frame_index": 7,
            "state_version": 11,
            "last_chart_path": str(chart_path),
        }
    )
    client = TestClient(create_app(window_tracker_service=svc))
    try:
        started = time.monotonic()
        first_response = client.post(
            f"/v1/mobile/window-tracker/sessions/{session_id}/predict"
        )
        elapsed = time.monotonic() - started
        assert first_response.status_code == 202
        assert elapsed < 1.0
        first = first_response.json()
        assert first == {
            "schema_version": "PG_FORECAST_ACTION_V1",
            "request_id": first["request_id"],
            "session_id": session_id,
            "mode": "predict",
            "status": first["status"],
            "terminal": False,
            "cached": False,
            "is_current": True,
            "source_frame_index": 7,
            "source_state_version": 11,
            "submitted_at": first["submitted_at"],
            "started_at": first["started_at"],
            "completed_at": "",
            "summary": first["summary"],
            "poll_after_ms": 250,
            "status_url": (
                f"/v1/mobile/window-tracker/sessions/{session_id}"
                f"/forecast-actions/{first['request_id']}"
            ),
        }
        serialized = first_response.text.lower()
        for forbidden in (
            "last_chart_path",
            "source_chart_path",
            "tracking_summary",
            "latest_signal",
            "runtime_telemetry",
            "hwnd",
            "projection_image_path",
        ):
            assert forbidden not in serialized
        assert adapter.started.wait(timeout=1.0)

        duplicate_response = client.post(
            f"/v1/mobile/window-tracker/sessions/{session_id}/predict"
        )
        assert duplicate_response.status_code == 202
        assert duplicate_response.json()["request_id"] == first["request_id"]
        assert adapter.calls == 1

        poll_response = client.get(str(first["status_url"]))
        assert poll_response.status_code == 200
        assert poll_response.json()["status"] == "running"
        adapter.release.set()
        completed = _wait_for_terminal_action(svc, session_id, str(first["request_id"]))
        assert completed["status"] == "ready"
        assert completed["is_current"] is True

        cached_response = client.post(
            f"/v1/mobile/window-tracker/sessions/{session_id}/predict"
        )
        assert cached_response.status_code == 200
        assert cached_response.json()["request_id"] == first["request_id"]
        assert cached_response.json()["cached"] is True
        assert adapter.calls == 1
    finally:
        adapter.release.set()
        svc.shutdown()


def test_forecast_action_marks_old_frame_stale_and_newer_request_wins(tmp_path: Path) -> None:
    adapter = BlockingProjectionAdapter()
    svc = ContinuousWindowTrackerService(root_dir=tmp_path / "wt", tracking_adapter=adapter)
    session_id = "forecast-cas"
    first_chart = tmp_path / "frame-1.png"
    second_chart = tmp_path / "frame-2.png"
    Image.new("RGB", (16, 12), color=(20, 30, 40)).save(first_chart)
    Image.new("RGB", (16, 12), color=(40, 30, 20)).save(second_chart)
    base_payload: dict[str, Any] = {
        "session_id": session_id,
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0, 0, 1, 1]},
        "frame_index": 1,
        "state_version": 3,
        "last_chart_path": str(first_chart),
    }
    svc.save_session(base_payload)
    try:
        first = svc.enqueue_memory_projection(session_id, mode="predict")
        assert adapter.started.wait(timeout=1.0)
        advanced_payload = svc.require_session(session_id)
        advanced_payload["frame_index"] = 2
        advanced_payload["state_version"] = 4
        advanced_payload["last_chart_path"] = str(second_chart)
        svc.save_session(advanced_payload)
        second = svc.enqueue_memory_projection(session_id, mode="predict")
        assert second["request_id"] != first["request_id"]

        adapter.release.set()
        first_done = _wait_for_terminal_action(svc, session_id, str(first["request_id"]))
        second_done = _wait_for_terminal_action(svc, session_id, str(second["request_id"]))
        assert first_done["status"] == "stale"
        assert first_done["is_current"] is False
        assert second_done["status"] == "ready"
        assert second_done["is_current"] is True
        assert adapter.calls == 2
        session = svc.require_session(session_id)
        projection = session["memory_projection_predict"]
        assert projection["source_frame_index"] == 2
        assert projection["source_state_version"] == 4
        assert projection["is_current"] is True
    finally:
        adapter.release.set()
        svc.shutdown()


def test_forecast_action_failure_is_terminal_and_sanitized(tmp_path: Path) -> None:
    svc = ContinuousWindowTrackerService(
        root_dir=tmp_path / "wt",
        tracking_adapter=FailingProjectionAdapter(),
    )
    session_id = "forecast-error"
    chart_path = tmp_path / "chart.png"
    Image.new("RGB", (12, 10), color=(0, 0, 0)).save(chart_path)
    svc.save_session(
        {
            "session_id": session_id,
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0, 0, 1, 1]},
            "frame_index": 1,
            "state_version": 1,
            "last_chart_path": str(chart_path),
        }
    )
    try:
        submitted = svc.enqueue_memory_projection(session_id, mode="future")
        completed = _wait_for_terminal_action(svc, session_id, str(submitted["request_id"]))
        assert completed["status"] == "error"
        assert completed["terminal"] is True
        assert completed["is_current"] is False
        assert "private backend failure detail" not in str(completed)
    finally:
        svc.shutdown()
