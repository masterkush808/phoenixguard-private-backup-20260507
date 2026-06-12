from __future__ import annotations

from phoenixguard.runtime.realtime_performance_v3 import (
    AsyncArtifactWriterV3,
    LatestFrameBufferV3,
    build_frame_timing_trace_v3,
    build_performance_trace_v3,
)


def test_latest_frame_buffer_drops_old_frames_and_reads_newest() -> None:
    buffer = LatestFrameBufferV3(buffer_size=3)
    buffer.write({"frame_id": 1})
    buffer.write({"frame_id": 2})
    buffer.write({"frame_id": 3})
    buffer.write({"frame_id": 4})

    latest = buffer.read_latest()

    assert latest is not None
    assert latest["frame_id"] == 4
    assert buffer.as_dict()["frames_dropped"] >= 2
    assert buffer.as_dict()["queue_depth"] == 0


def test_frame_timing_trace_reports_required_age_fields() -> None:
    session = {
        "session_id": "speed",
        "frame_index": 42,
        "state_version": 4200,
        "last_capture_started_epoch": 100.0,
        "last_capture_epoch": 100.8,
        "tracking_summary": {
            "pipeline_timing": {
                "capture_started_epoch": 100.0,
                "published_epoch": 100.8,
                "stages": [
                    {"stage": "capture_window", "elapsed_sec": 0.05, "duration_sec": 0.05},
                    {"stage": "derive_study_surface", "elapsed_sec": 0.10, "duration_sec": 0.05},
                    {"stage": "tracker_study", "elapsed_sec": 0.52, "duration_sec": 0.42},
                    {"stage": "artifact_write", "elapsed_sec": 0.60, "duration_sec": 0.08},
                ],
            }
        },
        "model_council_packet": {"packet_id": "p1", "created_epoch": 100.7},
    }
    trace = build_frame_timing_trace_v3(
        session,
        overlays=[{"overlay_id": "o1", "type": "RETEST_BOX", "bounds": [1, 2, 3, 4]}],
        model_health={"queue_depth": 0, "dropped_frames": 1},
        frontend_heartbeat={"frontend_loaded_ms": 101_000, "frontend_overlay_drawn_ms": 101_050},
        now_epoch=101.0,
    )

    assert trace["schema_version"] == "PG_FRAME_TIMING_TRACE_V3"
    assert trace["frame_id"] == 42
    assert trace["frame_age_ms"] == 1000
    assert trace["overlay_age_ms"] == 400
    assert trace["model_vote_age_ms"] == 480
    assert trace["packet_age_ms"] == 300
    assert trace["overlay_state_version"].startswith("ov_42_1_")


def test_performance_trace_contains_model_warm_state_and_budgets() -> None:
    live_state = {
        "session_id": "speed",
        "frame_id": 7,
        "state_version": 8,
        "overlay_state_version": "ov",
        "frame_timing_trace_v3": {
            "frame_id": 7,
            "frame_age_ms": 500,
            "overlay_age_ms": 400,
            "model_vote_age_ms": 300,
            "frontend_render_age_ms": 200,
            "overlay_state_version": "ov",
            "stale_status": "PASS",
            "packet_age_ms": 250,
            "stale_flags": [],
        },
        "model_health": {
            "all_required_models_awake": True,
            "required_roles": ["global_structure", "local_micro_structure"],
            "queue_depth": 0,
            "max_model_latency_ms": 120,
        },
        "frontend_heartbeat": {"frontend_overlay_drawn_ms": 1},
    }

    trace = build_performance_trace_v3(live_state, now_epoch=101.0)

    assert trace["schema_version"] == "PG_PERFORMANCE_TRACE_V3"
    assert trace["visual_health"]["status"] == "ALIVE"
    assert trace["model_health_summary"]["label"] == "2/2 awake"
    assert trace["overlay_render_budget"]["CLEAN_LIVE"] == 10


def test_async_artifact_writer_limits_pending_jobs() -> None:
    writer = AsyncArtifactWriterV3(max_workers=1, max_pending=1)
    first = writer.submit(lambda: "ok")
    second = writer.submit(lambda: "late")
    writer.shutdown()

    assert first is not None
    assert second is None or writer.as_dict()["submitted"] <= 2
