from __future__ import annotations

from phoenixguard.mobile_api.realtime_sync_v3 import (
    FRONTEND_HEARTBEAT_SCHEMA_VERSION,
    build_frontend_sync_status,
    build_visual_realtime_health,
    latest_frontend_heartbeat,
    normalize_frontend_heartbeat,
    prune_frontend_heartbeats,
    record_frontend_heartbeat,
)


def test_normalize_frontend_heartbeat_requires_session() -> None:
    try:
        normalize_frontend_heartbeat({"route": "/dashboard"})
    except ValueError as exc:
        assert "session_id" in str(exc)
    else:
        raise AssertionError("expected missing session_id to fail")


def test_record_and_load_frontend_heartbeat(tmp_path) -> None:
    heartbeat = record_frontend_heartbeat(
        {
            "session_id": "pocket-live-8788",
            "surface_id": "dashboard",
            "route": "/v1/mobile/window-tracker/dashboard/pocket-live-8788",
            "frame_id": 12,
            "chart_transform_id": "ct_12",
            "overlay_count": 4,
            "viewport": {"width": 1440, "height": 900},
            "render_size": {"width": 1000, "height": 600},
            "full_broker_surface_visible": True,
        },
        store_dir=tmp_path,
        now_ms=1_000_000,
    )
    assert heartbeat["schema_version"] == FRONTEND_HEARTBEAT_SCHEMA_VERSION
    loaded = latest_frontend_heartbeat("pocket-live-8788", surface_id="dashboard", store_dir=tmp_path)
    assert loaded is not None
    assert loaded["frame_id"] == 12
    assert loaded["overlay_count"] == 4


def test_build_frontend_sync_status_flags_mismatch(tmp_path) -> None:
    heartbeat = record_frontend_heartbeat(
        {
            "session_id": "s1",
            "surface_id": "dashboard",
            "frame_id": 5,
            "chart_transform_id": "ct_a",
            "overlay_count": 2,
            "viewport": {"width": 800, "height": 600},
            "render_size": {"width": 800, "height": 600},
            "full_broker_surface_visible": True,
        },
        store_dir=tmp_path,
        now_ms=10_000,
    )
    status = build_frontend_sync_status(
        "s1",
        backend_state={
            "session_id": "s1",
            "frame_id": 6,
            "chart_transform_id": "ct_a",
            "overlay_objects": [{}, {}, {}],
            "broker_surface": {"url": "/window.png"},
        },
        heartbeat=heartbeat,
        now_ms=11_000,
    )
    assert status["status"] == "MISMATCH"
    assert any("frame_id mismatch" in item for item in status["mismatches"])
    assert any("overlay_count mismatch" in item for item in status["mismatches"])


def test_visual_realtime_health_passes_when_artifacts_and_sync_match(tmp_path) -> None:
    heartbeat = record_frontend_heartbeat(
        {
            "session_id": "s2",
            "frame_id": 9,
            "chart_transform_id": "ct_9",
            "overlay_count": 1,
            "viewport": {"width": 800, "height": 600},
            "render_size": {"width": 800, "height": 600},
            "full_broker_surface_visible": True,
        },
        store_dir=tmp_path,
        now_ms=20_000,
    )
    health = build_visual_realtime_health(
        "s2",
        live_state={
            "session_id": "s2",
            "frame_id": 9,
            "chart_transform_id": "ct_9",
            "overlay_objects": [{"overlay_id": "o1"}],
            "broker_surface": {"url": "/window.png"},
        },
        visual_health={
            "artifacts": {"window": {"exists": True}, "chart": {"exists": True}},
            "overlay": {"frame_matches_chart_frame": True},
        },
        heartbeat=heartbeat,
        now_ms=21_000,
    )
    assert health["status"] == "PASS"
    assert health["ok"] is True


def test_prune_frontend_heartbeats(tmp_path) -> None:
    record_frontend_heartbeat(
        {
            "session_id": "old",
            "viewport": {"width": 1, "height": 1},
            "render_size": {"width": 1, "height": 1},
        },
        store_dir=tmp_path,
        now_ms=1_000,
    )
    assert prune_frontend_heartbeats(store_dir=tmp_path, max_age_sec=1.0, now_ms=5_000) >= 1
    assert latest_frontend_heartbeat("old", store_dir=tmp_path) is None
