from __future__ import annotations
from pathlib import Path
import pytest

from concurrent.futures import ThreadPoolExecutor
import time

from phoenixguard.mobile_api import realtime_sync_v3
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


def test_record_and_load_frontend_heartbeat(tmp_path: Path) -> None:
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


def test_replay_dashboard_heartbeat_does_not_overwrite_live_dashboard(tmp_path: Path) -> None:
    live = record_frontend_heartbeat(
        {
            "session_id": "pocket-live-8788",
            "surface_id": "dashboard",
            "route": "live",
            "overlay_mode": "CLEAN_LIVE",
            "frame_id": 10,
            "rendered_frame_id": 10,
            "overlay_count": 8,
        },
        store_dir=tmp_path,
        now_ms=1_000_000,
    )
    replay = record_frontend_heartbeat(
        {
            "session_id": "pocket-live-8788",
            "surface_id": "dashboard",
            "route": "replay",
            "overlay_mode": "REPLAY",
            "frame_id": 20,
            "rendered_frame_id": 20,
            "overlay_count": 3,
        },
        store_dir=tmp_path,
        now_ms=1_000_100,
    )

    assert live["surface_id"] == "dashboard"
    assert replay["surface_id"] == "dashboard_replay_replay"
    live_loaded = latest_frontend_heartbeat("pocket-live-8788", surface_id="dashboard", store_dir=tmp_path)
    replay_loaded = latest_frontend_heartbeat("pocket-live-8788", surface_id="dashboard_replay_replay", store_dir=tmp_path)
    assert live_loaded is not None
    assert replay_loaded is not None
    assert live_loaded["route"] == "live"
    assert live_loaded["frame_id"] == 10
    assert replay_loaded["route"] == "replay"
    assert replay_loaded["frame_id"] == 20


def test_record_frontend_heartbeat_uses_unique_temp_files_under_concurrency(tmp_path: Path) -> None:
    def write_heartbeat(frame_id: int) -> int:
        heartbeat = record_frontend_heartbeat(
            {
                "session_id": "pocket-live-8788",
                "surface_id": "dashboard",
                "route": "/dashboard",
                "frame_id": frame_id,
                "rendered_frame_id": frame_id,
                "chart_transform_id": f"ct_{frame_id}",
                "overlay_count": frame_id % 5,
            },
            store_dir=tmp_path,
            now_ms=1_000_000 + frame_id,
        )
        return int(heartbeat["frame_id"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        written = list(pool.map(write_heartbeat, range(40)))

    assert sorted(written) == list(range(40))
    assert latest_frontend_heartbeat("pocket-live-8788", surface_id="dashboard", store_dir=tmp_path) is not None
    assert not list(tmp_path.glob("*.tmp"))


def test_record_frontend_heartbeat_falls_back_to_memory_when_replace_is_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def locked_replace(_source: object, _target: object) -> None:
        raise PermissionError("simulated locked heartbeat target")

    monkeypatch.setattr(realtime_sync_v3.os, "replace", locked_replace)
    heartbeat = record_frontend_heartbeat(
        {
            "session_id": "locked-heartbeat",
            "surface_id": "dashboard",
            "route": "/dashboard",
            "frame_id": 99,
            "rendered_frame_id": 99,
            "overlay_count": 7,
        },
        store_dir=tmp_path,
        now_ms=time.time() * 1000.0,
    )

    assert heartbeat["write_status"] == "DEGRADED_MEMORY_ONLY"
    assert heartbeat["frame_id"] == 99
    assert not list(tmp_path.glob("*.tmp"))
    loaded = latest_frontend_heartbeat("locked-heartbeat", surface_id="dashboard", store_dir=tmp_path)
    assert loaded is not None
    assert loaded["frame_id"] == 99
    assert loaded["overlay_count"] == 7


def test_build_frontend_sync_status_flags_mismatch(tmp_path: Path) -> None:
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


def test_visual_realtime_health_passes_when_artifacts_and_sync_match(tmp_path: Path) -> None:
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


def test_prune_frontend_heartbeats(tmp_path: Path) -> None:
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
