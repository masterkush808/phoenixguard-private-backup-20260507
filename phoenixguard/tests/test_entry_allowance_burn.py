from __future__ import annotations

import json
import time
from pathlib import Path

from PIL import Image

from tools import run_entry_allowance_burn as burn


def test_entry_state_requires_execution_package_authority() -> None:
    live = {}
    council = {
        "promotion_trace": {
            "candidate_side": "BUY",
            "lane_accepted": False,
            "timing_decision": {"entry_now_allowed": True, "timing_mode": "ENTER_NOW"},
            "blocked_by": "NO_EXECUTION_LANE_ACCEPTED",
        }
    }

    entry = burn.entry_state(live, council)

    assert entry["allowed"] is False
    assert entry["side"] == "BUY"
    assert entry["execution_authorized"] is False
    assert entry["allowance_mode"] == "timing_entry_now"


def test_entry_state_allows_only_lane_accepted_packet_present() -> None:
    live = {}
    council = {
        "execution_packet_present": True,
        "promotion_trace": {
            "candidate_side": "SELL",
            "lane_accepted": True,
            "timing_decision": {"entry_now_allowed": True, "timing_mode": "ENTER_NOW"},
            "execution_lane": {"accepted": True},
        },
    }

    entry = burn.entry_state(live, council)

    assert entry["allowed"] is True
    assert entry["side"] == "SELL"
    assert entry["execution_authorized"] is True


def test_marker_point_refuses_contextual_fallback() -> None:
    live = {
        "signal_thesis_v3": {"current_price_proxy": 120},
        "tracking_summary": {
            "entry_zone": {"label": "SUPPORT 6T", "bbox": [10, 20, 30, 40]},
            "current_box": {"label": "RESISTANCE 5T", "bbox": [50, 60, 70, 80]},
        },
    }

    assert burn.marker_point(live, {}, (400, 300), "SELL") is None


def test_marker_point_uses_latest_candle_now_right_edge() -> None:
    live = {
        "overlay_objects": [
            {
                "type": "CURRENT_CANDLE",
                "role": "current_candle",
                "label": "NOW",
                "bbox": [100, 80, 132, 170],
            }
        ]
    }

    assert burn.marker_point(live, {}, (400, 300), "SELL") == (132, 170, "LATEST_CANDLE_NOW")
    assert burn.marker_point(live, {}, (400, 300), "BUY") == (132, 80, "LATEST_CANDLE_NOW")


def test_capture_entry_evidence_renders_current_json_when_overlay_artifact_is_stale(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    stale_overlay = artifact_dir / "000001_old_overlay.jpg"
    current_window = artifact_dir / "000100_live_window.jpg"
    Image.new("RGB", (200, 140), (25, 25, 28)).save(stale_overlay)
    Image.new("RGB", (200, 140), (12, 16, 24)).save(current_window)
    sample = {
        "seq": 7,
        "captured_at_utc": "2026-06-20T00:00:00+00:00",
        "captured_epoch": 1000.0,
        "frames": {"display_frame_id": 100},
        "entry": {
            "allowed": True,
            "side": "SELL",
            "entry_now_allowed": True,
            "lane_accepted": True,
            "packet_present": True,
        },
    }
    live = {
        "frame_index": 100,
        "last_overlay_path": str(stale_overlay),
        "last_display_window_path": str(current_window),
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "overlay_objects": [
            {
                "type": "CURRENT_CANDLE",
                "role": "current_candle",
                "label": "NOW",
                "bbox": [70, 40, 80, 100],
                "frame_id": 100,
            },
            {
                "type": "SUPPLY_ZONE",
                "label": "SUPPLY",
                "bbox": [20, 30, 100, 55],
                "label_visible": True,
                "frame_id": 100,
            },
        ],
    }

    event = burn.capture_entry_evidence(tmp_path, sample, live, {}, "missing-session", "http://127.0.0.1:9", 0.01)

    assert event["overlay_source_mode"] == "rendered_live_json_on_current_window_crop"
    assert event["overlay_freshness"]["status"] == "STALE"
    assert event["marker_source"] == "LATEST_CANDLE_NOW"
    assert event["chart_point"] == {"x": 80, "y": 100, "source": "LATEST_CANDLE_NOW"}
    assert event["source_overlay_path"].startswith("rendered_live_json_on_current_window_crop:")
    assert Path(event["overlay_evidence_path"]).exists()
    assert Path(event["broker_evidence_path"]).exists()
    assert "_sell_entry_" in Path(event["overlay_evidence_path"]).name


def test_capture_entry_evidence_labels_blocked_enter_now_filename(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    overlay = artifact_dir / "000100_live_overlay.jpg"
    window = artifact_dir / "000100_live_window.jpg"
    Image.new("RGB", (200, 140), (25, 25, 28)).save(overlay)
    Image.new("RGB", (200, 140), (12, 16, 24)).save(window)
    sample = {
        "seq": 8,
        "captured_at_utc": "2026-06-20T00:00:00+00:00",
        "captured_epoch": 1001.0,
        "frames": {"display_frame_id": 100},
        "entry": {
            "allowed": False,
            "side": "BUY",
            "entry_now_allowed": True,
            "lane_accepted": True,
            "packet_present": False,
        },
    }
    live = {
        "frame_index": 100,
        "last_overlay_path": str(overlay),
        "last_display_window_path": str(window),
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "overlay_objects": [
            {
                "type": "CURRENT_CANDLE",
                "role": "current_candle",
                "label": "NOW",
                "bbox": [70, 40, 80, 100],
                "frame_id": 100,
            }
        ],
    }

    event = burn.capture_entry_evidence(tmp_path, sample, live, {}, "missing-session", "http://127.0.0.1:9", 0.01)

    assert "_buy_blocked_enter_now_" in Path(event["overlay_evidence_path"]).name
    assert event["entry"]["allowed"] is False
    assert event["marker_source"] == "LATEST_CANDLE_NOW"


def test_prune_path_budget_preserves_protected_latest_artifact(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    old_file = artifact_dir / "000001_dead_full_overlay.jpg"
    protected_file = artifact_dir / "000002_live_full_overlay.jpg"
    old_file.write_bytes(b"x" * 1024)
    protected_file.write_bytes(b"y" * 1024)
    old_time = time.time() - 10_000
    old_file.touch()
    protected_file.touch()
    old_file.replace(old_file)
    # Force the non-protected file to be older than the budget age.
    import os

    os.utime(old_file, (old_time, old_time))

    result = burn.prune_path_budget(
        artifact_dir,
        max_mb=0.001,
        max_files=1,
        max_age_sec=1,
        protected_paths={burn._path_key(protected_file)},
    )

    assert result["removed"] == 1
    assert not old_file.exists()
    assert protected_file.exists()


def test_compact_sample_includes_grade_a_star_audit() -> None:
    live_resp = {
        "ok": True,
        "latency_ms": 10.0,
        "json": {
            "status": "running",
            "display_frame_id": 1,
            "overlay_frame_id": 1,
            "model_vote_frame_id": 1,
            "signal_thesis_v3": {"current_price_proxy": 120, "entry_price_proxy": 130},
            "visual_health_v3": {"status": "ALIVE", "stale_flags": []},
            "overlay_objects": [{"type": "SUPPORT_TRENDLINE"}],
        },
    }
    audit = {
        "schema_version": "PG_PROMOTION_FAILURE_AUDIT_V3",
        "top_blocker": "TIMING_MODE_WAIT_FOR_PULLBACK",
        "next_required": "wait for retest",
    }
    council_resp = {
        "ok": True,
        "latency_ms": 12.0,
        "json": {
            "promotion_trace": {
                "candidate_side": "SELL",
                "promotion_failure_audit_v3": audit,
                "timing_decision": {"entry_now_allowed": False, "timing_mode": "WAIT_FOR_PULLBACK"},
                "execution_lane": {"accepted": False, "name": "HIGH_FREQUENCY_TWO_CANDLE"},
            }
        },
    }
    perf_resp = {
        "ok": True,
        "latency_ms": 8.0,
        "json": {"model_health_summary": {"label": "7/7 awake", "queue_depth": 0}},
    }

    sample = burn.compact_sample(1, live_resp, council_resp, perf_resp, "test-session")

    grade = sample["grade_a_star_audit"]
    assert grade["promotion_failure_audit_v3"] == audit
    assert grade["execution_opportunity"]["class"] == "EARLY_OPPORTUNITY"
    assert grade["no_silent_failure"]["live_endpoint"]["status"] == "PASS"
    assert sample["study_quality"]["trendline_count"] == 1


def test_score_entries_ignores_blocked_enter_now_evidence() -> None:
    samples = [
        {
            "seq": 1,
            "captured_epoch": 1000.0,
            "price_proxy": {"current_y": 100.0},
            "entry": {"side": "SELL"},
        },
        {
            "seq": 2,
            "captured_epoch": 1000.0,
            "price_proxy": {"current_y": 100.0},
            "entry": {"side": "SELL"},
        },
        {
            "seq": 3,
            "captured_epoch": 1061.0,
            "price_proxy": {"current_y": 120.0},
            "entry": {"side": "SELL"},
        },
    ]
    entries = [
        {"seq": 1, "entry": {"allowed": False}, "blocked_entry_capture": True},
        {"seq": 2, "entry": {"allowed": True}},
    ]

    scores = burn.score_entries(samples, entries)

    one_min_rows = scores["60"]["rows"]
    assert [row["seq"] for row in one_min_rows] == [2]
    assert one_min_rows[0]["verdict"] == "correct"
