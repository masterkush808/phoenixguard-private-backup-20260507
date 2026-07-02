from __future__ import annotations
from typing import Any
import pytest

import time
from pathlib import Path

from PIL import Image

from tools import run_entry_allowance_burn as burn


def test_entry_state_does_not_promote_timing_without_playbook_or_packet_authority() -> None:
    live: dict[str, Any] = {}
    council: dict[str, Any] = {
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
    assert entry["strategy_allowed"] is False
    assert entry["allowance_mode"] == "watching"


def test_entry_state_allows_only_lane_accepted_packet_present() -> None:
    live: dict[str, Any] = {}
    council: dict[str, Any] = {
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
    assert entry["strategy_allowed"] is False
    assert entry["allowance_mode"] == "legacy_timing_entry"


def test_entry_state_allows_playbook_enter_now_without_legacy_lane_or_packet() -> None:
    live: dict[str, Any] = {}
    council: dict[str, Any] = {
        "promotion_trace": {
            "candidate_side": "BUY",
            "book_strategy_state": "ENTER_NOW",
            "opportunity_maturity_state": "ENTER_NOW",
            "lane_accepted": False,
            "timing_decision": {
                "entry_now_allowed": True,
                "playbook_strategy_authorized": True,
                "timing_mode": "ENTER_NOW",
            },
            "execution_lane": {"accepted": False, "name": "SNIPER_ZONE_ENTRY"},
            "allowance_package": {
                "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3",
                "accepted": True,
                "execution_ready": True,
                "opportunity_maturity": "ENTER_NOW",
            },
        }
    }

    entry = burn.entry_state(live, council)

    assert entry["allowed"] is True
    assert entry["strategy_allowed"] is True
    assert entry["playbook_entry_allowed"] is True
    assert entry["execution_authorized"] is False
    assert entry["packet_present"] is False
    assert entry["allowance_mode"] == "playbook_strategy_entry"


def test_compact_sample_preserves_candle_movement_context() -> None:
    context: dict[str, Any] = {
        "schema_version": "PG_CANDLE_MOVEMENT_CONTEXT_V3",
        "visible_candle_count": 28,
        "tracked_candle_count": 28,
        "move_stage": "MATURE",
        "move_duration": {"seconds": 4200, "minutes": 70.0, "text": "70.0m"},
        "current_leg": {
            "side": "BUY",
            "candle_count": 14,
            "duration": {"seconds": 4200, "minutes": 70.0, "text": "70.0m"},
            "move_stage": "MATURE",
        },
        "opposing_force_room": {"room_ok": True, "distance_norm": 0.31},
        "candles_per_leg": [{"label": "H2 BUY", "side": "BUY", "candle_count": 14}],
        "boxes_with_anchors": 3,
        "boxes_with_candles": 4,
    }
    live_resp: dict[str, Any] = {
        "ok": True,
        "latency_ms": 12.0,
        "json": {
            "status": "running",
            "tracking_enabled": True,
            "tracking_summary": {"candle_movement_context_v3": context},
        },
    }
    council_resp: dict[str, Any] = {
        "ok": True,
        "latency_ms": 10.0,
        "json": {"promotion_trace": {"candidate_side": "BUY", "timing_decision": {"entry_now_allowed": False}}},
    }
    perf_resp: dict[str, Any] = {"ok": True, "latency_ms": 8.0, "json": {}}

    sample = burn.compact_sample(2, live_resp, council_resp, perf_resp, "pocket-live-8788")

    assert sample["candle_movement_context_v3"] == context
    assert sample["candle_movement"]["current_leg_candle_count"] == 14
    assert sample["entry"]["candle_movement"]["move_duration"]["minutes"] == 70.0


def _operator_alert_entry(
    *,
    score: float,
    threshold: float = 0.82,
    leg_count: int = 5,
    leg_stage: str = "STILL_RECLAIMING",
    bad_class: str = "",
) -> dict[str, Any]:
    return {
        "allowed": True,
        "execution_authorized": True,
        "packet_present": True,
        "side": "BUY",
        "packet_id": "pgpkt_test",
        "lane_name": "MOMENTUM_ACCEPTANCE_ENTRY",
        "allowance_authority": burn.PLAYBOOK_EXECUTION_AUTHORITY,
        "final_score": score,
        "threshold": threshold,
        "reasoning_bad_entry_class": bad_class,
        "expected_move_time": {
            "expected_duration_sec": 600,
            "expected_duration_text": "10m 00s",
            "timeframe": "M5",
            "timeframe_seconds": 300,
            "expected_candle_count": 2,
            "current_leg_candle_count": leg_count,
            "current_leg_side": "BUY",
            "current_leg_stage": leg_stage,
        },
        "candle_movement": {
            "visible_candle_count": 49,
            "current_leg_side": "BUY",
            "current_leg_candle_count": leg_count,
            "current_leg_stage": leg_stage,
            "boxes_with_anchors": 25,
            "boxes_with_candles": 27,
            "opposing_force_room": {
                "room_ok": True,
                "risk_state": "OPEN",
                "estimated_candles_to_force": 6,
            },
        },
    }


def _operator_alert_sample() -> dict[str, Any]:
    return {
        "seq": 10,
        "captured_epoch": 1000.0,
        "frames": {"display_frame_id": 194, "capture_count": 194},
        "freshness": {"fresh": True},
    }


def test_operator_alert_quality_blocks_low_score_against_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BURN_OPERATOR_ALERT_MIN_SCORE", "0.70")
    decision = burn.operator_alert_quality_decision(
        _operator_alert_entry(score=0.6641, threshold=0.82, leg_count=5),
        _operator_alert_sample(),
    )

    assert decision["allowed"] is False
    assert "SCORE_BELOW_DIRECT_ALERT_0.66_LT_0.82" in decision["reasons"]


def test_operator_alert_quality_blocks_mature_distribution_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BURN_OPERATOR_ALERT_MAX_MATURE_LEG_CANDLES", "7")
    decision = burn.operator_alert_quality_decision(
        _operator_alert_entry(score=0.9, threshold=0.82, leg_count=9, leg_stage="MATURE"),
        _operator_alert_sample(),
    )

    assert decision["allowed"] is False
    assert "MATURE_CONTINUATION_TOO_EXTENDED_9_GT_7" in decision["reasons"]


def test_operator_alert_quality_allows_fresh_strong_reclaim() -> None:
    decision = burn.operator_alert_quality_decision(
        _operator_alert_entry(score=0.9, threshold=0.82, leg_count=5, leg_stage="STILL_RECLAIMING"),
        _operator_alert_sample(),
    )

    assert decision["allowed"] is True
    assert decision["classification"] == "DISTRIBUTION_CONTINUATION"
    assert decision["expected_duration_sec"] == 600
    assert decision["valid_until_epoch"] == 1600.0


def test_operator_alert_message_explicitly_expires_packet_window() -> None:
    entry = _operator_alert_entry(score=0.9, threshold=0.82, leg_count=5, leg_stage="STILL_RECLAIMING")
    sample = _operator_alert_sample()
    gate = burn.operator_alert_quality_decision(entry, sample)

    active = burn.operator_alert_message(entry, sample, gate)
    expired = burn.operator_alert_message(entry, sample, gate, expired=True)

    assert "STATUS: ACTIVE DIRECT ENTRY WINDOW" in active
    assert "VALID UNTIL UTC:" in active
    assert "SCORE: 0.90 | DIRECT ALERT REQUIRED: 0.82" in active
    assert "STATUS: EXPIRED - DO NOT ENTER" in expired
    assert "DO NOT ENTER FROM THIS WINDOW" in expired


def test_runtime_freshness_prefers_fresh_published_frame_over_old_display_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_CAPTURE_AGE_SEC", "4")
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_FRAME_AGE_MS", "2500")
    live: dict[str, Any] = {
        "tracking_enabled": True,
        "status": "running",
        "display_capture_epoch": now - 18.0,
    }
    perf: dict[str, Any] = {
        "generated_epoch": now - 0.2,
        "timing_trace": {
            "frame_age_ms": 220,
            "stale_status": "PASS",
            "display_published_epoch_ms": int((now - 0.2) * 1000),
        },
    }

    freshness = burn.runtime_freshness_state(
        {"ok": True},
        {"ok": True},
        {"ok": True},
        live,
        perf,
    )

    assert freshness["fresh"] is True
    assert freshness["published_frame_fresh"] is True
    assert freshness["capture_epoch_source"] == "timing_trace.display_published_epoch_ms"
    assert freshness["capture_age_warning"] is None
    assert freshness["reasons"] == []


def test_runtime_freshness_blocks_publish_epoch_lag_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_CAPTURE_AGE_SEC", "4")
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_FRAME_AGE_MS", "2500")
    live: dict[str, Any] = {
        "tracking_enabled": True,
        "status": "running",
        "display_capture_epoch": now - 60.0,
    }
    perf: dict[str, Any] = {
        "generated_epoch": now - 12.0,
        "timing_trace": {
            "frame_age_ms": 380,
            "stale_status": "PASS",
            "display_published_epoch_ms": int((now - 12.0) * 1000),
        },
    }

    freshness = burn.runtime_freshness_state(
        {"ok": True},
        {"ok": True},
        {"ok": True},
        live,
        perf,
    )

    assert freshness["fresh"] is False
    assert freshness["published_frame_fresh"] is True
    assert freshness["published_age_warning"].startswith("PUBLISHED_AGE_")
    assert freshness["capture_age_warning"].startswith("CAPTURE_START_AGE_")
    assert any(reason.startswith("PUBLISHED_AGE_") for reason in freshness["reasons"])
    assert any(reason.startswith("CAPTURE_AGE_") for reason in freshness["reasons"])


def test_runtime_freshness_warning_relaxation_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_CAPTURE_AGE_SEC", "4")
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_FRAME_AGE_MS", "2500")
    monkeypatch.setenv("PHOENIXGUARD_BURN_REJECT_PUBLISHED_AGE_WARNING", "0")
    monkeypatch.setenv("PHOENIXGUARD_BURN_REJECT_CAPTURE_AGE_WARNING", "0")
    live: dict[str, Any] = {
        "tracking_enabled": True,
        "status": "running",
        "last_capture_epoch": now - 18.0,
    }
    perf: dict[str, Any] = {
        "generated_epoch": now - 0.2,
        "timing_trace": {
            "frame_age_ms": 220,
            "stale_status": "PASS",
        },
    }

    freshness = burn.runtime_freshness_state(
        {"ok": True},
        {"ok": True},
        {"ok": True},
        live,
        perf,
    )

    assert freshness["fresh"] is True
    assert freshness["capture_age_warning"].startswith("CAPTURE_START_AGE_")
    assert freshness["reasons"] == []


def test_marker_point_refuses_contextual_fallback() -> None:
    live: dict[str, Any] = {
        "signal_thesis_v3": {"current_price_proxy": 120},
        "tracking_summary": {
            "entry_zone": {"label": "SUPPORT 6T", "bbox": [10, 20, 30, 40]},
            "current_box": {"label": "RESISTANCE 5T", "bbox": [50, 60, 70, 80]},
        },
    }

    assert burn.marker_point(live, {}, (400, 300), "SELL") is None


def test_marker_point_uses_latest_candle_now_right_edge() -> None:
    live: dict[str, Any] = {
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
    sample: dict[str, Any] = {
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
    live: dict[str, Any] = {
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


def test_capture_entry_evidence_refuses_unresolved_marker_images(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    overlay = artifact_dir / "000100_live_overlay.jpg"
    window = artifact_dir / "000100_live_window.jpg"
    Image.new("RGB", (200, 140), (25, 25, 28)).save(overlay)
    Image.new("RGB", (200, 140), (12, 16, 24)).save(window)
    sample: dict[str, Any] = {
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
    live: dict[str, Any] = {
        "frame_index": 100,
        "last_overlay_path": str(overlay),
        "last_display_window_path": str(window),
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "overlay_objects": [],
    }

    event = burn.capture_entry_evidence(tmp_path, sample, live, {}, "missing-session", "http://127.0.0.1:9", 0.01)

    assert event["error"] == "ENTRY_MARKER_UNRESOLVED"
    assert event["evidence_images_written"] is True
    assert event["marker_unresolved"] is True
    assert Path(str(event["overlay_evidence_path"])).exists()
    assert Path(str(event["broker_evidence_path"])).exists()
    assert event["entry"]["allowed"] is False
    evidence_dir = tmp_path / "entry_evidence"
    assert len(list(evidence_dir.glob("*.json"))) == 1
    assert len(list(evidence_dir.glob("*.jpg"))) == 2


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
        protected_paths={burn.path_key(protected_file)},
    )

    assert result["removed"] == 1
    assert not old_file.exists()
    assert protected_file.exists()


def test_prune_path_budget_preserves_allowed_entry_evidence(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "entry_evidence"
    evidence_dir.mkdir()
    allowed_overlay = evidence_dir / "000010_111111_sell_entry_overlay.jpg"
    allowed_broker = evidence_dir / "000010_111111_sell_entry_broker.jpg"
    allowed_meta = evidence_dir / "000010_111111_sell_entry.json"
    blocked_overlay = evidence_dir / "000011_111112_sell_blocked_enter_now_overlay.jpg"
    for item in (allowed_overlay, allowed_broker, allowed_meta, blocked_overlay):
        item.write_bytes(b"x" * 1024)

    old_time = time.time() - 10_000
    import os

    for item in (allowed_overlay, allowed_broker, allowed_meta, blocked_overlay):
        os.utime(item, (old_time, old_time))

    protected = burn.protected_allowed_entry_evidence_paths(evidence_dir)
    result = burn.prune_path_budget(
        evidence_dir,
        max_mb=0.001,
        max_files=0,
        max_age_sec=1,
        protected_paths=protected,
    )

    assert result["removed"] == 1
    assert allowed_overlay.exists()
    assert allowed_broker.exists()
    assert allowed_meta.exists()
    assert not blocked_overlay.exists()


def test_compact_sample_includes_grade_a_star_audit() -> None:
    live_resp: dict[str, Any] = {
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
    council_resp: dict[str, Any] = {
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
    perf_resp: dict[str, Any] = {
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


def test_compact_sample_blocks_entry_when_runtime_is_stale() -> None:
    live_resp: dict[str, Any] = {
        "ok": True,
        "latency_ms": 10.0,
        "json": {
            "status": "running",
            "tracking_enabled": True,
            "display_frame_id": 77,
            "overlay_frame_id": 77,
            "model_vote_frame_id": 77,
            "last_capture_epoch": time.time() - 30,
            "signal_thesis_v3": {},
            "visual_health_v3": {"status": "STALE", "stale_flags": ["frame_age"]},
        },
    }
    council_resp: dict[str, Any] = {
        "ok": True,
        "latency_ms": 12.0,
        "json": {
            "execution_packet_present": True,
            "promotion_trace": {
                "candidate_side": "BUY",
                "lane_accepted": True,
                "timing_decision": {"entry_now_allowed": True, "timing_mode": "ENTER_NOW"},
                "execution_lane": {"accepted": True, "name": "HIGH_FREQUENCY_TWO_CANDLE"},
            }
        },
    }
    perf_resp: dict[str, Any] = {
        "ok": True,
        "latency_ms": 8.0,
        "json": {
            "timing_trace": {"frame_age_ms": 30_000, "stale_status": "STALE"},
            "model_health_summary": {"label": "7/7 awake", "queue_depth": 0},
        },
    }

    sample = burn.compact_sample(9, live_resp, council_resp, perf_resp, "test-session")
    entry = sample["entry"]

    assert sample["freshness"]["fresh"] is False
    assert entry["entry_now_allowed"] is True
    assert entry["lane_accepted"] is False
    assert entry["legacy_hf_lane_rejected"] is True
    assert entry["packet_present"] is True
    assert entry["raw_allowed_without_freshness_guard"] is False
    assert entry["allowed"] is False
    assert entry["execution_authorized"] is False
    assert entry["freshness_rejected"] is True
    assert entry["blocked_by"] == "STALE_RUNTIME_GUARD"


def test_pixel_freeze_guard_blocks_executable_when_artifact_hash_is_static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_STATIC_PIXEL_SEC", "10")
    monkeypatch.setenv("PHOENIXGUARD_BURN_HARD_STATIC_PIXEL_SEC", "10")
    window = tmp_path / "000001_live_window.jpg"
    Image.new("RGB", (120, 80), (20, 30, 40)).save(window)
    state: dict[str, object] = {}

    first = burn.update_pixel_freeze_state(
        state,
        {"last_display_window_path": str(window), "display_frame_id": 1, "capture_count": 10},
        1000.0,
    )
    second = burn.update_pixel_freeze_state(
        state,
        {"last_display_window_path": str(window), "display_frame_id": 40, "capture_count": 100},
        1012.0,
    )

    sample: dict[str, Any] = {
        "freshness": {"fresh": True, "reasons": []},
        "entry": {
            "allowed": True,
            "execution_authorized": True,
            "entry_now_allowed": True,
            "lane_accepted": True,
            "packet_present": True,
        },
    }
    guarded = burn.apply_pixel_freeze_guard(sample, second)

    assert first["status"] == "CHANGED"
    assert second["status"] == "FROZEN"
    assert guarded["freshness"]["fresh"] is False
    assert guarded["entry"]["allowed"] is False
    assert guarded["entry"]["execution_authorized"] is False
    assert guarded["entry"]["raw_allowed_without_freshness_guard"] is True
    assert guarded["entry"]["blocked_by"] == "STALE_RUNTIME_GUARD"
    assert guarded["freshness"]["reasons"][0].startswith("BROKER_PIXELS_FROZEN_")


def test_pixel_static_refresh_does_not_block_before_hard_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BURN_MAX_STATIC_PIXEL_SEC", "10")
    monkeypatch.setenv("PHOENIXGUARD_BURN_HARD_STATIC_PIXEL_SEC", "60")
    window = tmp_path / "000001_live_window.jpg"
    Image.new("RGB", (120, 80), (20, 30, 40)).save(window)
    state: dict[str, object] = {}

    burn.update_pixel_freeze_state(
        state,
        {"last_display_window_path": str(window), "display_frame_id": 1, "capture_count": 10},
        1000.0,
    )
    static_refresh = burn.update_pixel_freeze_state(
        state,
        {"last_display_window_path": str(window), "display_frame_id": 40, "capture_count": 100},
        1012.0,
    )

    sample: dict[str, Any] = {
        "freshness": {"fresh": True, "reasons": []},
        "entry": {
            "allowed": True,
            "execution_authorized": True,
            "entry_now_allowed": True,
            "lane_accepted": True,
            "packet_present": True,
        },
    }
    guarded = burn.apply_pixel_freeze_guard(sample, static_refresh)

    assert static_refresh["status"] == "STATIC_REFRESH"
    assert static_refresh["refresh_recommended"] is True
    assert guarded["freshness"]["fresh"] is True
    assert guarded["entry"]["allowed"] is True
    assert guarded["entry"]["execution_authorized"] is True
    assert guarded["freshness"]["reasons"] == []


def test_score_entries_ignores_blocked_enter_now_evidence() -> None:
    samples: list[dict[str, Any]] = [
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
    entries: list[dict[str, Any]] = [
        {"seq": 1, "entry": {"allowed": False}, "blocked_entry_capture": True},
        {"seq": 2, "entry": {"allowed": True}},
    ]

    scores = burn.score_entries(samples, entries)

    one_min_rows = scores["60"]["rows"]
    assert [row["seq"] for row in one_min_rows] == [2]
    assert one_min_rows[0]["verdict"] == "correct"


def test_score_events_tracks_blocked_trend_aligned_study_separately() -> None:
    samples: list[dict[str, Any]] = [
        {
            "seq": 1,
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
    entries: list[dict[str, Any]] = [
        {
            "seq": 1,
            "entry": {
                "allowed": False,
                "side": "SELL",
                "blocked_trend_aligned_study": True,
                "lane_name": "WAVE_RIDING_CONTINUATION",
                "blocked_by": "REASONING_WATCH",
            },
            "blocked_entry_capture": True,
        }
    ]

    allowed_scores = burn.score_entries(samples, entries)
    blocked_scores = burn.score_events(samples, entries, include_blocked_trend_study=True)

    assert allowed_scores["60"]["rows"] == []
    assert blocked_scores["60"]["rows"][0]["seq"] == 1
    assert blocked_scores["60"]["rows"][0]["verdict"] == "correct"
    assert blocked_scores["60"]["rows"][0]["blocked_trend_aligned_study"] is True


def test_blocked_trend_aligned_study_requires_soft_non_stale_blocker() -> None:
    soft = burn.blocked_trend_aligned_study(
        {
            "allowed": False,
            "side": "SELL",
            "entry_now_allowed": True,
            "lane_accepted": True,
            "blocked_by": "REASONING_WATCH",
            "directional_location_ok": True,
        }
    )
    stale = burn.blocked_trend_aligned_study(
        {
            "allowed": False,
            "side": "SELL",
            "entry_now_allowed": True,
            "lane_accepted": True,
            "blocked_by": "STALE_RUNTIME_GUARD",
            "directional_location_ok": True,
        }
    )
    location_risk = burn.blocked_trend_aligned_study(
        {
            "allowed": False,
            "side": "SELL",
            "entry_now_allowed": True,
            "lane_accepted": True,
            "blocked_by": "REASONING_WATCH",
            "directional_location_chase_risk": True,
        }
    )

    assert soft["active"] is True
    assert stale["active"] is False
    assert location_risk["active"] is False


def test_manual_entry_rearm_suppresses_same_candidate_until_rearmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_MANUAL_ENTRY_REARM_MIN_SEC", "300")
    monkeypatch.setenv("PHOENIXGUARD_MANUAL_ENTRY_REARM_MIN_PRICE_PX", "30")
    monkeypatch.setenv("PHOENIXGUARD_MANUAL_ENTRY_REARM_MIN_FRAME_DELTA", "10")
    state: dict[str, dict[str, object]] = {}
    entry: dict[str, Any] = {
        "allowed": True,
        "side": "SELL",
        "lane_name": "SNIPER_ZONE_ENTRY",
        "candidate_id": "cand_1",
    }
    first = burn.manual_entry_rearm_decision(
        entry,
        {"frames": {"display_frame_id": 100}, "price_proxy": {"current_y": 500.0}},
        state,
        1000.0,
    )
    duplicate = burn.manual_entry_rearm_decision(
        entry,
        {"frames": {"display_frame_id": 104}, "price_proxy": {"current_y": 510.0}},
        state,
        1020.0,
    )
    rearmed = burn.manual_entry_rearm_decision(
        entry,
        {"frames": {"display_frame_id": 115}, "price_proxy": {"current_y": 535.0}},
        state,
        1320.0,
    )

    assert first["allowed"] is True
    assert first["key"].endswith("rearm=1")
    assert duplicate["allowed"] is False
    assert duplicate["suppressed"] is True
    assert rearmed["allowed"] is True
    assert rearmed["key"].endswith("rearm=2")
