from pathlib import Path
from typing import Any
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
from PIL import Image
from fastapi.testclient import TestClient
from phoenixguard.mobile_api.app import create_app


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
    assert resp.status_code == 200
    data = resp.json()
    mem = data.get("memory_projection_future") or data.get("memory_projection_current")
    assert mem and mem.get("projection_image_path")
    art = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-projection")
    assert art.status_code == 200
    assert art.content and len(art.content) > 0
