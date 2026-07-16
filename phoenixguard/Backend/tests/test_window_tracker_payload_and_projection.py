from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, cast

from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api.app import create_app
import phoenixguard.mobile_api.window_tracker as window_tracker_module
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


def test_forecast_snapshot_keeps_three_complete_trajectory_scenarios() -> None:
    scenario_sides = ("BUY", "SELL", "HOLD")
    scenarios = [
        {
            "side": side,
            "probability": 0.5 - scenario_index * 0.15,
            "probability_calibrated": False,
            "selected": scenario_index == 0,
            "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
            "forecast_path": [
                {
                    "step": step,
                    "event": f"CANDLE_EVENT_{step}",
                    "expected_close_norm": round(
                        0.5 + (1 - scenario_index) * step * 0.004,
                        6,
                    ),
                    "expected_cumulative_delta_norm": round(
                        (1 - scenario_index) * step * 0.004,
                        6,
                    ),
                    "decoder_hidden_state": ["must", "not", "leak"],
                }
                for step in range(12, 0, -1)
            ],
            "raw_payload": {"private": True},
        }
        for scenario_index, side in enumerate(scenario_sides)
    ]
    mode_probabilities = {"BUY": 0.5, "SELL": 0.35, "HOLD": 0.15}

    snapshot = window_tracker_module._forecast_snapshot_v3(  # pyright: ignore[reportPrivateUsage]
        {
            "frame_index": 9,
            "model_vote_frame_id": 9,
            "model_capture_epoch": 500.0,
            "visual_observation_v3": {
                "status": "NEW_FRAME",
                "new_visual_evidence": True,
            },
            "tracking_summary": {
                "lstm_contribution": {
                    "frame_id": 9,
                    "forecast_available": True,
                    "trajectory_modes": 3,
                    "trajectory_decoder_status": "AVAILABLE",
                    "trajectory_mode": "BUY",
                    "trajectory_mode_probabilities": mode_probabilities,
                    "trajectory_mode_probability_calibrated": False,
                    "trajectory_scenarios": scenarios,
                    "unrelated_raw_payload": {"private": True},
                }
            },
        }
    )

    lstm = snapshot["lstm_contribution"]
    assert lstm["trajectory_modes"] == 3
    assert lstm["trajectory_decoder_status"] == "AVAILABLE"
    assert lstm["trajectory_mode"] == "BUY"
    assert lstm["trajectory_mode_probabilities"] == mode_probabilities
    assert lstm["trajectory_mode_probability_calibrated"] is False
    retained = lstm["trajectory_scenarios"]
    assert [scenario["side"] for scenario in retained] == list(scenario_sides)
    for scenario in retained:
        path = scenario["forecast_path"]
        assert len(path) == 12
        assert [row["step"] for row in path] == list(range(1, 13))
        assert "raw_payload" not in scenario
        assert all("decoder_hidden_state" not in row for row in path)
    assert "unrelated_raw_payload" not in lstm


def test_scene_forecast_snapshot_keeps_provider_truth_and_causal_suite_audit() -> None:
    audit: dict[str, object] = {
        "schema_version": "scene_forecast_features_v3",
        "causal_fields": 41,
        "rejected_future_fields": [],
        "closed_candle_only": True,
    }
    identity_state = {
        "schema_version": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
        "pair": "NZDUSD",
        "timeframe": "M5",
        "event_key": "closed-event-12",
        "event_sequence": 12,
        "latest_closed": {"track_id": "45", "side": "SELL"},
        "forming": {"track_id": "46", "side": "BUY"},
    }
    snapshot_builder = cast(
        Callable[[Mapping[str, Any]], dict[str, Any]],
        getattr(window_tracker_module, "_forecast_snapshot_v3"),
    )
    snapshot = snapshot_builder(
        {
            "frame_index": 12,
            "model_vote_frame_id": 12,
            "model_capture_epoch": 700.0,
            "tracking_summary": {
                "scene_forecast_contribution": {
                    "frame_id": 12,
                    "forecast_available": True,
                    "provider": "SCENE_STATISTICAL_FALLBACK_V3",
                    "requested_provider": "CHRONOS_2_LOCAL",
                    "provider_status": "FOUNDATION_DISABLED_FALLBACK",
                    "scene_feature_audit": audit,
                    "closed_candle_identity_state": identity_state,
                    "closed_candle_transition_observed": False,
                    "closed_candle_transition_reason": "FORMING_CANDLE_STILL_ACTIVE",
                }
            },
        }
    )

    scene = snapshot["scene_forecast_contribution"]
    assert scene["provider"] == "SCENE_STATISTICAL_FALLBACK_V3"
    assert scene["requested_provider"] == "CHRONOS_2_LOCAL"
    assert scene["provider_status"] == "FOUNDATION_DISABLED_FALLBACK"
    assert scene["scene_feature_audit"] == audit
    assert scene["closed_candle_identity_state"] == identity_state
    assert scene["closed_candle_transition_observed"] is False
    assert scene["closed_candle_transition_reason"] == "FORMING_CANDLE_STILL_ACTIVE"


def test_forecast_snapshot_preserves_per_study_frames_and_prefers_scene_root() -> None:
    snapshot = window_tracker_module._forecast_snapshot_v3(  # pyright: ignore[reportPrivateUsage]
        {
            "frame_index": 99,
            "display_frame_id": 99,
            "model_vote_frame_id": 99,
            "model_capture_epoch": 800.0,
            "tracking_summary": {
                "two_candle_study": {
                    "frame_id": 11,
                    "display_frame_id": 12,
                    "status": "READY",
                },
                "lstm_contribution": {
                    "frame_id": 21,
                    "display_frame_id": 22,
                    "forecast_available": True,
                    "path_side": "SELL",
                },
                "scene_forecast_contribution": {
                    "frame_id": 31,
                    "display_frame_id": 32,
                    "forecast_available": True,
                    "pair": "NZDUSD_OTC",
                    "timeframe": "M5",
                    "provider": "SCENE_STATISTICAL_FALLBACK_V3",
                },
            },
        }
    )

    assert snapshot["source_frame_id"] == 32
    two_candle = cast(dict[str, Any], snapshot["two_candle_study"])
    lstm = cast(dict[str, Any], snapshot["lstm_contribution"])
    scene = cast(dict[str, Any], snapshot["scene_forecast_contribution"])
    assert (two_candle["frame_id"], two_candle["display_frame_id"]) == (11, 12)
    assert (lstm["frame_id"], lstm["display_frame_id"]) == (21, 22)
    assert (scene["frame_id"], scene["display_frame_id"]) == (31, 32)
    assert len({two_candle["frame_id"], lstm["frame_id"], scene["frame_id"]}) == 3


def test_stale_forecast_snapshot_retains_each_existing_study_frame() -> None:
    snapshot = window_tracker_module._forecast_snapshot_v3(  # pyright: ignore[reportPrivateUsage]
        {
            "frame_index": 999,
            "display_frame_id": 999,
            "visual_observation_v3": {
                "status": "WAITING_FOR_NEW_FRAME",
                "new_visual_evidence": False,
                "last_observed_epoch": 700.0,
            },
            "forecast_snapshot_v3": {
                "source_frame_id": 302,
                "observed_epoch": 700.0,
                "two_candle_study": {
                    "frame_id": 101,
                    "display_frame_id": 102,
                    "status": "READY",
                },
                "lstm_contribution": {
                    "frame_id": 201,
                    "display_frame_id": 202,
                    "forecast_available": True,
                    "path_side": "BUY",
                    "forecast_path": [{"step": 1, "expected_close_norm": 0.6}],
                },
                "scene_forecast_contribution": {
                    "frame_id": 301,
                    "display_frame_id": 302,
                    "forecast_available": True,
                    "pair": "EURUSD_OTC",
                    "timeframe": "M5",
                    "provider": "SCENE_STATISTICAL_FALLBACK_V3",
                },
            },
        }
    )

    assert snapshot["source_frame_id"] == 302
    assert snapshot["observed_epoch"] == 700.0
    assert snapshot["status"] == "STALE_DIAGNOSTIC"
    assert snapshot["stale"] is True
    two_candle = cast(dict[str, Any], snapshot["two_candle_study"])
    lstm = cast(dict[str, Any], snapshot["lstm_contribution"])
    scene = cast(dict[str, Any], snapshot["scene_forecast_contribution"])
    assert (two_candle["frame_id"], two_candle["display_frame_id"]) == (101, 102)
    assert (lstm["frame_id"], lstm["display_frame_id"]) == (201, 202)
    assert (scene["frame_id"], scene["display_frame_id"]) == (301, 302)
    assert lstm["forecast_path"] == [{"step": 1, "expected_close_norm": 0.6}]
    assert all(row["diagnostic_only"] is True for row in (two_candle, lstm, scene))


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
            "snapshot_ready": False,
            "snapshot_status": "CURRENT",
            "source_frame_index": 7,
            "source_state_version": 11,
            "current_frame_index": 7,
            "source_frame_age": 0,
            "trade_authorized": False,
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
        still_running = svc.get_memory_projection_action(
            session_id,
            str(first["request_id"]),
        )
        assert still_running["terminal"] is False
        assert still_running["status"] in {"queued", "running"}
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


def test_forecast_action_keeps_completed_immutable_snapshot_when_live_frame_advances(
    tmp_path: Path,
) -> None:
    adapter = BlockingProjectionAdapter()
    svc = ContinuousWindowTrackerService(root_dir=tmp_path / "wt", tracking_adapter=adapter)
    session_id = "forecast-snapshot"
    source_chart = tmp_path / "frame-10.png"
    live_chart = tmp_path / "frame-11.png"
    Image.new("RGB", (16, 12), color=(20, 30, 40)).save(source_chart)
    Image.new("RGB", (16, 12), color=(40, 30, 20)).save(live_chart)
    svc.save_session(
        {
            "session_id": session_id,
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0, 0, 1, 1]},
            "frame_index": 10,
            "state_version": 20,
            "last_chart_path": str(source_chart),
        }
    )
    try:
        submitted = svc.enqueue_memory_projection(session_id, mode="future")
        assert adapter.started.wait(timeout=1.0)
        advanced = svc.require_session(session_id)
        advanced["frame_index"] = 11
        advanced["state_version"] = 21
        advanced["last_chart_path"] = str(live_chart)
        svc.save_session(advanced)

        adapter.release.set()
        completed = _wait_for_terminal_action(svc, session_id, str(submitted["request_id"]))

        assert completed["status"] == "ready"
        assert completed["is_current"] is False
        assert completed["snapshot_ready"] is True
        assert completed["snapshot_status"] == "READY"
        assert completed["source_frame_index"] == 10
        assert completed["current_frame_index"] == 11
        assert completed["source_frame_age"] == 1
        assert completed["trade_authorized"] is False
        projection = svc.get_session(session_id)["memory_projection_future"]
        assert projection["status"] == "ready"
        assert projection["snapshot_ready"] is True
        assert projection["source_frame_age"] == 1
        assert projection["actionable"] is False
        assert projection["execution_permission"] == "WAIT_FOR_CONFIRMATION"
        assert projection["trade_authorized"] is False
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
