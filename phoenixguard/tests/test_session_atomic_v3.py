from __future__ import annotations
from pathlib import Path

import json

from phoenixguard.runtime.realtime_performance_v3 import (
    SessionAtomicWriterV3,
    SessionFreshnessValidatorV3,
)


def test_session_touch_without_capture_is_not_fresh() -> None:
    previous = {
        "session_id": "s1",
        "frame_index": 7,
        "display_frame_id": 7,
        "display_capture_epoch": 100.0,
        "chart_frame_id": 7,
        "overlay_frame_id": 7,
        "model_vote_frame_id": 7,
        "model_capture_epoch": 100.0,
        "state_version": 7000,
        "source_capture_id": "capture:s1:7:7",
        "updated_at": "old",
    }
    current = dict(previous, updated_at="new", state_version=7001)

    result = SessionFreshnessValidatorV3().validate(previous, current, now_epoch=101.0)

    assert result["ok"] is False
    assert result["status"] == "TOUCH_ONLY_STALE"
    assert result["visual_health"] == "FAIL"


def test_atomic_session_write_preserves_frame_fields(tmp_path: Path) -> None:
    target = tmp_path / "session.json"
    payload = {
        "session_id": "s1",
        "capture_count": 3,
        "frame_index": 3,
        "last_capture_started_epoch": 200.0,
        "last_capture_epoch": 201.0,
        "updated_at": "now",
    }

    written = SessionAtomicWriterV3.write_atomic(target, payload)
    loaded = json.loads(target.read_text(encoding="utf-8"))

    assert written["display_frame_id"] == 3
    assert loaded["chart_frame_id"] == 3
    assert loaded["overlay_frame_id"] == 3
    assert loaded["model_vote_frame_id"] == 3
    assert loaded["source_capture_id"]
    assert loaded["session_freshness_v3"]["missing_fields"] == []


def test_display_only_write_does_not_advance_model_epoch() -> None:
    previous = {
        "session_id": "s1",
        "capture_count": 7,
        "frame_index": 6,
        "display_frame_id": 7,
        "display_capture_epoch": 100.0,
        "chart_frame_id": 6,
        "overlay_frame_id": 6,
        "model_vote_frame_id": 6,
        "model_capture_epoch": 99.0,
        "tracking_summary": {
            "pipeline_timing": {
                "capture_started_epoch": 99.0,
                "published_epoch": 100.0,
            }
        },
        "source_capture_id": "capture:s1:7:6",
    }
    current = dict(
        previous,
        capture_count=8,
        display_frame_id=8,
        display_capture_epoch=101.0,
        last_capture_started_epoch=101.0,
        last_capture_epoch=101.2,
        display_snapshot_only_v3=True,
    )

    written = SessionAtomicWriterV3.prepare_payload(current, previous=previous)

    assert written["display_frame_id"] == 8
    assert written["model_capture_epoch"] == 99.0
    assert written["model_vote_frame_id"] == 6


def test_partial_session_write_rejected() -> None:
    current = {"session_id": "s1", "frame_index": 4, "state_version": 4}

    result = SessionFreshnessValidatorV3().validate({}, current, now_epoch=10.0)

    assert result["ok"] is False
    assert result["status"] == "PARTIAL_SESSION"
    assert "display_capture_epoch" in result["missing_fields"]


def test_capture_epoch_must_advance_with_frame_id() -> None:
    previous = {
        "session_id": "s1",
        "frame_index": 4,
        "display_frame_id": 4,
        "display_capture_epoch": 100.0,
        "chart_frame_id": 4,
        "overlay_frame_id": 4,
        "model_vote_frame_id": 4,
        "model_capture_epoch": 100.0,
        "state_version": 4,
        "source_capture_id": "capture:s1:4:4",
    }
    current = dict(previous, frame_index=5, display_frame_id=5, chart_frame_id=5, overlay_frame_id=5, model_vote_frame_id=5)

    result = SessionFreshnessValidatorV3().validate(previous, current, now_epoch=101.0)

    assert result["frame_id_advanced"] is True
    assert result["capture_epoch_advanced"] is False
    assert result["model_capture_epoch_advanced"] is False
    assert result["ok"] is False
    assert result["status"] == "FRAME_EPOCH_MISMATCH"
