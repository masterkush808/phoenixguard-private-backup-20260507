from __future__ import annotations
import pytest

from pathlib import Path
from typing import Any, Mapping

from PIL import Image

from phoenixguard.mobile_api.live_state_v3 import (
    LIVE_STATE_SCHEMA_VERSION,
    _compact_session_payload,
    build_live_state_v3,
    build_live_state_v3_from_tracker_service,
)
from phoenixguard.mobile_api.window_tracker import _model_council_study_packet_from_payload


def _png(path: Path, size: tuple[int, int] = (320, 180)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(20, 24, 32)).save(path)
    return path


def test_compact_session_payload_preserves_v3_authority_packets_and_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("phoenixguard.mobile_api.window_tracker.time.time", lambda: 100.0)
    sequence_context = {
        "sequence_id": "seq-100",
        "status": "COMPLETE",
        "sequence_status": "COMPLETE",
        "sequence_length": 64,
        "frames_received": 64,
        "frames_used": 62,
        "box_history": [{"track_id": "box-1"}],
        "entry_progression": {"steps": [{"phase": "pullback"}]},
        "sequence_signature": "sig-100",
        "sequence_confidence": 0.91,
    }
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_type": "STUDY_PACKET",
        "packet_id": "study-100",
        "session_id": "pocket-live-8788",
        "created_epoch": 99.0,
        "valid_until_epoch": 120.0,
        "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
        "model_council": {
            "final_state": "WATCHING",
            "final_side": "SELL",
            "sequence_context": sequence_context,
        },
        "promotion_trace": {"denied_at": "TIMING_WAIT", "next_required": "wait retest"},
    }
    execution_packet = {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": "exec-100",
        "created_epoch": 99.0,
        "valid_until_epoch": 120.0,
        "valid_until_epoch_sec": 120.0,
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": "SELL",
            "expiry_seconds": 600,
        },
        "model_council": {
            "final_state": "EXECUTABLE",
            "final_side": "SELL",
        },
    }
    compact = _compact_session_payload(
        {
            "session_id": "pocket-live-8788",
            "status": "running",
            "tracking_enabled": True,
            "last_capture_epoch": 100.0,
            "capture_count": 101,
            "frame_index": 42,
            "model_council_study_packet": study_packet,
            "model_council_packet": execution_packet,
            "execution_packet": execution_packet,
            "model_council_result": {
                "packet_id": "result-100",
                "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
                "model_council": {
                    "final_state": "WATCHING",
                    "final_side": "SELL",
                    "sequence_context": sequence_context,
                    "promotion_trace": {"denied_at": "TIMING_WAIT"},
                },
                "promotion_trace": {"denied_at": "TIMING_WAIT", "next_required": "wait retest"},
            },
        }
    )

    assert compact["model_council_study_packet"]["packet_id"] == "study-100"
    assert compact["model_council_packet"]["packet_id"] == "exec-100"
    assert compact["execution_packet"]["packet_id"] == "exec-100"
    compact_council = compact["model_council_result"]["model_council"]
    assert compact_council["sequence_context"]["sequence_id"] == "seq-100"
    assert compact["model_council_result"]["promotion_trace"]["next_required"] == "wait retest"
    resolved_study = _model_council_study_packet_from_payload(compact)
    assert resolved_study["packet_id"] == "study-100"


def test_compact_session_payload_drops_expired_execution_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("phoenixguard.mobile_api.live_state_v3.time.time", lambda: 150.0)
    expired_packet = {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": "exec-expired",
        "created_epoch": 100.0,
        "valid_until_epoch": 120.0,
        "valid_until_epoch_sec": 120.0,
    }

    compact = _compact_session_payload(
        {
            "session_id": "pocket-live-8788",
            "status": "running",
            "tracking_enabled": True,
            "model_council_packet": expired_packet,
            "execution_packet": expired_packet,
            "broker_execution_state": {
                "status": "external_shooter_required",
                "side": "BUY",
                "lane": "MODEL_COUNCIL_PACKET_V3",
                "actionable": True,
            },
        }
    )

    assert "model_council_packet" not in compact
    assert "execution_packet" not in compact
    assert compact["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert compact["broker_execution_state"]["side"] == "HOLD"


def test_compact_session_payload_drops_demoted_execution_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("phoenixguard.mobile_api.live_state_v3.time.time", lambda: 150.0)
    demoted_packet = {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": "exec-demoted",
        "created_epoch": 149.0,
        "valid_until_epoch": 180.0,
        "valid_until_epoch_sec": 180.0,
        "execution": {
            "enabled": False,
            "state": "WATCHING",
            "side": None,
            "expiry_seconds": 600,
        },
        "model_council": {
            "final_state": "WATCHING",
            "final_side": None,
        },
    }

    compact = _compact_session_payload(
        {
            "session_id": "pocket-live-8788",
            "status": "running",
            "tracking_enabled": True,
            "model_council_packet": demoted_packet,
            "execution_packet": demoted_packet,
            "broker_execution_state": {
                "status": "external_shooter_required",
                "side": "SELL",
                "lane": "MODEL_COUNCIL_PACKET_V3",
                "actionable": True,
            },
        }
    )

    assert "model_council_packet" not in compact
    assert "execution_packet" not in compact
    assert compact["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert compact["broker_execution_state"]["side"] == "HOLD"


def test_build_live_state_v3_returns_one_truthful_visual_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", raising=False)
    window = _png(tmp_path / "window.png", (640, 360))
    chart = _png(tmp_path / "chart.png", (560, 260))
    overlay = _png(tmp_path / "overlay.png", (560, 260))
    session = {
        "session_id": "pocket-live-8788",
        "status": "running",
        "tracking_enabled": True,
        "capture_interval_sec": 1.0,
        "last_capture_epoch": 100.0,
        "decision_valid_until_epoch": 140.0,
        "state_version": 54,
        "capture_count": 54,
        "descriptor": {"title": "The Most Innovative Trading Platform - Microsoft Edge", "hwnd": 808},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.05, 0.12, 0.78, 0.92]},
        "tracking_summary": {
            "detected_market": "EUR/JPY OTC",
            "detected_timeframe": "M5",
            "chart_region": {"pixel_bbox": [40, 30, 600, 330], "confidence": 0.91, "source": "manual_focus"},
            "visible_candle_count": 54,
            "global_direction": "BUY",
            "local_direction": "BUY",
            "impulse_direction": "BUY",
            "tracked_candles": [
                {"index": 53, "pixel_bbox": [520, 130, 536, 210], "direction": "BUY", "confidence": 0.84}
            ],
            "support_resistance_zones": [
                {"label": "nearest resistance", "pixel_bbox": [430, 65, 610, 96], "truth_score": 0.72}
            ],
            "broker_surface": {"broker": "PocketOption", "controls_ready": True},
        },
        "latest_signal": {
            "market": "EUR/JPY OTC",
            "focus_timeframe": "M5",
            "side": "BUY",
            "execution_action": "BUY",
            "effective_confidence": 0.97,
            "summary": "Confirmation BUY is ready.",
            "market_confidence": 0.93,
            "timeframe_confidence": 0.88,
            "two_candle_study": {
                "schema_version": "PG_TWO_CANDLE_STUDY_V3",
                "display_as": "TEXT_AND_BANDS_ONLY",
                "do_not_render_synthetic_candles": True,
                "summary": "Study only; no synthetic candles are rendered.",
                "next_candle_forecast": {"direction_bias": "BUY", "confidence": 0.62},
                "second_next_candle_forecast": {"direction_bias": "BUY", "confidence": 0.51},
            },
            "lstm_contribution": {
                "schema_version": "PG_LSTM_CANDLE_SEQUENCE_CONTRIBUTION_V3",
                "skill": "LSTM_CANDLE_SEQUENCE",
                "fresh": False,
                "blocker": False,
                "contribution": 0.0,
                "side": "BUY",
            },
        },
        "model_council_result": {
            "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
            "model_council": {
                "final_state": "WATCHING",
                "final_side": "BUY",
                "arbitration_reason": "wait for clean retest",
            },
            "promotion_trace": {"denied_at": "TIMING_WAIT", "next_required": "full sequence context required"},
        },
        "signal_thesis_v3": {
            "schema_version": "PG_SIGNAL_THESIS_V3",
            "active": True,
            "status": "TRACKING",
            "side": "BUY",
            "effective_side": "BUY",
            "entry_frame_id": 48,
            "countertrend_blocked": True,
            "blocked_countertrend_side": "SELL",
            "entry_zone": {"bbox": [500, 150, 545, 225]},
            "target_zone": {"bbox": [560, 80, 620, 115]},
            "invalidation_zone": {"bbox": [485, 225, 552, 245]},
            "plain_language": "Tracking the active BUY idea.",
        },
    }
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_type": "STUDY_PACKET",
        "packet_id": "study-1",
        "created_epoch": 100.0,
        "valid_until_epoch": 140.0,
        "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
        "model_council": {"final_state": "WATCHING", "final_side": "BUY"},
        "promotion_trace": {"next_required": "full sequence context required"},
    }
    active_objects = [
        {
            "overlay_id": "sniper-1",
            "object_id": "obj-sniper",
            "track_id": "trk-sniper",
            "truth_score": 0.95,
            "lifecycle_state": "ACTIVE",
            "overlay": {
                "type": "SNIPER_ENTRY_BOX",
                "side": "BUY",
                "pixel_bbox": [500, 150, 545, 225],
                "confidence": 0.95,
                "reason": "BUY agg sniper",
            },
        }
    ]

    state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart, "overlay": overlay},
        active_objects=active_objects,
        registry_entries=active_objects,
        study_packet=study_packet,
        model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        shooter_state={"session_id": "pocket-live-8788", "state": "WAITING", "mode": "LIVE_READY", "side": "BUY"},
        now_epoch=110.0,
    )

    assert state["schema_version"] == LIVE_STATE_SCHEMA_VERSION
    assert state["surface"]["selected_plane"] == "full_broker_surface"
    assert state["surface"]["frame"]["exists"] is True
    assert state["surface"]["frame"]["width"] == 640
    assert state["chart"]["plot_area"]["exists"] is True
    assert state["chart"]["plot_area"]["bounds"]["width"] == 560
    assert state["instrument"]["market"] == "EUR/JPY OTC"
    assert state["instrument"]["timeframe"] == "M5"
    assert state["instrument"]["identity_locked"] is True
    assert state["model_council"]["side"] == "BUY"
    assert state["signal_thesis_v3"]["active"] is True
    assert state["live_visual_state"]["signal_thesis_v3"]["side"] == "BUY"
    assert state["live_visual_state"]["two_candle_study"]["display_as"] == "TEXT_AND_BANDS_ONLY"
    assert state["live_visual_state"]["two_candle_study"]["do_not_render_synthetic_candles"] is True
    assert state["live_visual_state"]["lstm_contribution"]["blocker"] is False
    assert state["live_visual_state"]["prediction_overlay"]["enabled"] is False
    assert state["live_visual_state"]["visual_plane"]["auto_zoom_enabled"] is False
    assert state["live_visual_state"]["overlay_layout"]["schema_version"] == "PG_OVERLAY_LAYOUT_V3"
    assert state["model_council"]["next_required"] == "full sequence context required"
    assert state["packets"]["study"]["exists"] is True
    assert state["packets"]["study"]["fresh"] is True
    assert state["model_health"]["all_required_models_awake"] is True
    assert state["shooter"]["available"] is True
    assert state["visual_health"]["full_broker_surface_visible"] is True
    assert state["visual_health"]["overlay_contract_ok"] is True
    assert state["requested_mode"] == "CLEAN_LIVE"
    assert state["active_mode"] == "CLEAN_LIVE"
    assert "trigger_zones" in state["visible_layers"]
    assert state["overlay_mode"]["requested"] == "CLEAN_LIVE"
    assert state["overlay_mode"]["active"] == "CLEAN_LIVE"
    assert "CLEAN_LIVE" in state["overlay_mode"]["available_modes"]
    assert state["overlay_mode"]["visible_layers"] == state["visible_layers"]
    assert state["reason_if_empty"] == ""
    assert state["overlay_mode"]["reason_if_empty"] == ""
    assert state["broker_source"] == {
        "lock_id": "808",
        "valid": True,
        "status": "VALID",
        "wrong_surface": False,
        "url_valid": True,
        "title_valid": True,
        "pixel_fingerprint_valid": True,
    }
    assert state["broker_surface"]["frame_id"] == 54
    assert state["broker_surface"]["frame_url"] == state["artifacts"]["window"]["url"]
    assert state["broker_surface"]["width"] == 640
    assert state["broker_surface"]["height"] == 360
    assert state["overlays"]["count"] >= 3
    assert state["overlays"]["total_count"] == state["overlay_count"]
    assert state["overlays"]["renderable_count"] == state["renderable_count"] == len(state["overlay_objects"])
    assert state["overlays"]["hidden_count"] >= 0
    assert state["overlays"]["rejected_count"] >= 0
    assert state["overlays"]["objects"] == state["overlay_objects"]
    assert state["overlay_ledger_v3"]["schema_version"] == "PG_OVERLAY_LEDGER_V3"
    assert state["overlays"]["ledger"] == state["overlay_ledger_v3"]
    assert state["overlay_ledger_v3"]["ledger_count"] >= state["renderable_count"]
    assert isinstance(state["overlay_ledger_v3"]["display_state_counts"], dict)
    assert state["live_visual_state"]["vlm_context_skeleton_v3"]["overlay_story"]["ledger"]["schema_version"] == "PG_OVERLAY_LEDGER_V3"
    thesis_overlays = [
        overlay for overlay in state["overlay_objects"]
        if str(overlay.get("overlay_id", "")).startswith("thesis_")
    ]
    assert thesis_overlays == []
    assert state["overlay_precision_audit"]["precision_report"]["floating_unanchored_rejected"] >= 2
    for overlay_object in state["overlays"]["objects"]:
        assert {
            "overlay_id",
            "object_id",
            "track_id",
            "type",
            "side",
            "source_agent",
            "frame_id",
            "sequence_id",
            "chart_transform_id",
            "coordinate_mode",
            "anchor_type",
            "bounds",
            "bounds_rect",
            "truth_score",
            "confidence",
            "lifecycle_state",
            "visible_modes",
            "ttl_ms",
            "reason",
            "display_state",
            "visual_weight",
            "geometry_visible",
            "label_visible",
            "inspector_visible",
            "label_mode",
            "style",
        }.issubset(overlay_object)
        assert overlay_object["bounds_rect"]["width"] > 0
        assert isinstance(overlay_object["bounds"], list)
        assert overlay_object["display_state"] in {
            "FULL",
            "COMPACT",
            "GHOSTED",
            "ICON_ONLY",
            "GROUPED",
            "NESTED",
            "INSPECTOR_ONLY_LABEL",
            "FOCUS_EXPANDED",
        }
    assert state["sequence_context"]["schema_version"] == "PG_SEQUENCE_CONTEXT_V3"
    assert state["sequence_context"]["placeholder"] is True
    assert state["sequence_context"]["tracked_objects"]


def test_live_state_suppresses_overlay_objects_when_visual_artifact_frame_is_reused(tmp_path: Path) -> None:
    window = _png(tmp_path / "54_pocket_window.png", (640, 360))
    chart = _png(tmp_path / "54_pocket_chart.png", (560, 260))
    stale_overlay = _png(tmp_path / "53_pocket_overlay.png", (560, 260))
    session = {
        "session_id": "pocket-live-8788",
        "frame_index": 54,
        "capture_count": 54,
        "state_version": 54,
        "descriptor": {"title": "Pocket Option", "hwnd": 808},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.05, 0.12, 0.78, 0.92]},
        "tracking_summary": {
            "chart_region": {"pixel_bbox": [40, 30, 600, 330], "confidence": 0.91},
            "broker_surface": {"controls_ready": True},
        },
        "latest_signal": {"side": "BUY", "execution_action": "BUY"},
    }
    active_objects = [
        {
            "overlay_id": "sniper-54",
            "object_id": "obj-sniper",
            "track_id": "trk-sniper",
            "truth_score": 0.95,
            "lifecycle_state": "ACTIVE",
            "overlay": {
                "type": "SNIPER_ENTRY_BOX",
                "side": "BUY",
                "pixel_bbox": [500, 150, 545, 225],
                "confidence": 0.95,
                "reason": "BUY sniper",
            },
        }
    ]

    state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart, "overlay": stale_overlay},
        active_objects=active_objects,
        registry_entries=active_objects,
        now_epoch=110.0,
    )

    assert state["overlay_artifact_frame_id"] == 53
    assert state["overlay_object_frame_id"] == 54
    assert state["overlay_artifact_frame_aligned"] is False
    assert state["renderable_count"] == 0
    assert state["overlay_objects"] == []
    assert "does not match overlay object frame" in state["reason_if_empty"]


def test_live_state_keeps_locked_overlay_objects_when_surface_authority_matches(tmp_path: Path) -> None:
    window = _png(tmp_path / "54_pocket_window.png", (640, 360))
    chart = _png(tmp_path / "54_pocket_chart.png", (560, 260))
    stale_overlay = _png(tmp_path / "53_pocket_overlay.png", (560, 260))
    session = {
        "session_id": "pocket-live-8788",
        "frame_index": 54,
        "display_frame_id": 120,
        "capture_count": 54,
        "state_version": 54,
        "display_snapshot_only_v3": True,
        "last_display_surface_signature": "locked-surface",
        "last_window_surface_signature": "locked-surface",
        "overlay_source_window_signature": "locked-surface",
        "descriptor": {"title": "Pocket Option", "hwnd": 808},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.05, 0.12, 0.78, 0.92]},
        "tracking_summary": {
            "chart_region": {"pixel_bbox": [40, 30, 600, 330], "confidence": 0.91},
            "broker_surface": {"controls_ready": True},
        },
        "latest_signal": {"side": "BUY", "execution_action": "BUY"},
    }
    active_objects = [
        {
            "overlay_id": "sniper-54",
            "object_id": "obj-sniper",
            "track_id": "trk-sniper",
            "truth_score": 0.95,
            "lifecycle_state": "ACTIVE",
            "frame_id": 54,
            "overlay": {
                "type": "SNIPER_ENTRY_BOX",
                "side": "BUY",
                "pixel_bbox": [500, 150, 545, 225],
                "confidence": 0.95,
                "reason": "BUY sniper",
            },
        }
    ]

    state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart, "overlay": stale_overlay},
        active_objects=active_objects,
        registry_entries=active_objects,
        now_epoch=110.0,
    )

    assert state["overlay_artifact_frame_id"] == 53
    assert state["overlay_object_frame_id"] == 54
    assert state["overlay_artifact_frame_aligned"] is True
    assert state["overlay_artifact_authority_locked"] is True
    assert state["renderable_count"] >= 1
    assert state["overlay_objects"]
    assert state["reason_if_empty"] == ""


def test_live_state_keeps_current_frame_objects_when_overlay_artifact_is_stale(tmp_path: Path) -> None:
    window = _png(tmp_path / "182_pocket_window.png", (640, 360))
    chart = _png(tmp_path / "182_pocket_chart.png", (560, 260))
    stale_overlay = _png(tmp_path / "001_pocket_overlay.png", (560, 260))
    session = {
        "session_id": "pocket-live-8788",
        "frame_index": 182,
        "display_frame_id": 182,
        "capture_count": 182,
        "state_version": 182,
        "last_display_window_path": str(window),
        "broker_source": {"valid": True, "wrong_surface": False, "source": "BrokerSourceLockV3", "lock_id": "locked-window-808"},
        "descriptor": {"title": "Pocket Option", "hwnd": 808},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.05, 0.12, 0.78, 0.92]},
        "tracking_summary": {
            "chart_region": {"pixel_bbox": [40, 30, 600, 330], "confidence": 0.91},
            "broker_surface": {"controls_ready": True},
        },
        "latest_signal": {"side": "BUY", "execution_action": "BUY"},
    }
    active_objects = [
        {
            "overlay_id": "support-182",
            "object_id": "obj-support",
            "track_id": "trk-support",
            "truth_score": 0.95,
            "lifecycle_state": "ACTIVE",
            "frame_id": 182,
            "overlay": {
                "type": "SNIPER_ENTRY_BOX",
                "side": "BUY",
                "pixel_bbox": [500, 150, 545, 225],
                "confidence": 0.95,
                "reason": "current-frame object",
            },
        }
    ]

    state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart, "overlay": stale_overlay},
        active_objects=active_objects,
        registry_entries=active_objects,
        now_epoch=110.0,
    )

    assert state["overlay_artifact_frame_id"] == 1
    assert state["overlay_object_frame_id"] == 182
    assert state["overlay_artifact_frame_aligned"] is True
    assert state["overlay_artifact_authority_locked"] is True
    assert state["renderable_count"] >= 1
    assert state["overlay_objects"]
    assert state["reason_if_empty"] == ""


def test_build_live_state_v3_is_tolerant_of_missing_tracker_fields() -> None:
    state = build_live_state_v3(
        {"session_id": "empty"},
        model_health={"all_required_models_awake": False, "council_status": "STALE"},
        now_epoch=50.0,
    )

    assert state["schema_version"] == LIVE_STATE_SCHEMA_VERSION
    assert state["session_id"] == "empty"
    assert state["surface"]["frame"]["exists"] is False
    assert state["chart"]["plot_area"]["exists"] is False
    assert state["packets"]["study"]["exists"] is False
    assert state["packets"]["execution"]["exists"] is False
    assert state["sequence_context"]["placeholder"] is True
    assert state["sequence_context"]["tracked_objects"] == []
    assert state["visual_health"]["ok"] is False
    assert state["shooter"]["available"] is False
    assert state["requested_mode"] == "CLEAN_LIVE"
    assert state["active_mode"] == "CLEAN_LIVE"
    assert state["renderable_count"] == 0
    assert state["overlays"]["objects"] == []
    assert state["reason_if_empty"] == "no market overlays available for the current broker surface"


def test_build_live_state_v3_respects_unavailable_shooter_fallback() -> None:
    state = build_live_state_v3(
        {"session_id": "pocket-live-8788"},
        shooter_state={
            "session_id": "pocket-live-8788",
            "state": "WAITING",
            "mode": "LIVE_READY",
            "available": False,
            "reason": "Shooter handshake not found.",
        },
        now_epoch=50.0,
    )

    assert state["shooter"]["available"] is False
    assert state["shooter"]["state"] == "WAITING"
    assert state["shooter"]["session_match"] is True
    assert state["shooter_state"]["reason"] == "Shooter handshake not found."


def test_replay_mode_ignores_clean_live_prefilter_env(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("PHOENIXGUARD_LIVE_STATE_CLEAN_OVERLAYS_ONLY", "1")
    window = _png(tmp_path / "window.png", (640, 360))
    chart = _png(tmp_path / "chart.png", (560, 260))
    session = {
        "session_id": "pocket-live-replay",
        "frame_index": 12,
        "tracking_enabled": True,
        "tracking_summary": {"chart_region": {"pixel_bbox": [0, 0, 560, 260]}},
    }
    active_objects = [
        {
            "overlay_id": "history-locked",
            "truth_score": 0.82,
            "overlay": {
                "overlay_id": "history-locked",
                "type": "HISTORICAL_REPLAY",
                "layer": "historical_replay",
                "side": "SELL",
                "bbox": [120, 80, 260, 210],
                "confidence": 0.82,
                "visible_modes": ["REPLAY", "FULL_HISTORY_READ", "INSPECTOR"],
            },
        }
    ]

    replay_state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart},
        active_objects=active_objects,
        overlay_mode="REPLAY",
        now_epoch=110.0,
    )
    clean_state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart},
        active_objects=active_objects,
        overlay_mode="CLEAN_LIVE",
        now_epoch=110.0,
    )

    assert replay_state["renderable_count"] == 1
    assert replay_state["overlays"]["objects"][0]["layer"] == "historical_replay"
    assert clean_state["renderable_count"] == 1
    assert clean_state["overlays"]["objects"][0]["layer"] == "historical_replay"


def test_unknown_overlay_labels_hidden_from_live_and_collected_for_diagnostics(tmp_path: Path) -> None:
    window = _png(tmp_path / "window.png", (640, 360))
    chart = _png(tmp_path / "chart.png", (560, 260))
    session = {
        "session_id": "pocket-live-vocab",
        "frame_index": 12,
        "tracking_enabled": True,
        "tracking_summary": {"chart_region": {"pixel_bbox": [0, 0, 560, 260]}},
    }
    active_objects = [
        {
            "overlay_id": "unknown-leftover",
            "truth_score": 0.82,
            "overlay": {
                "overlay_id": "unknown-leftover",
                "type": "OLD_NOW_DEBUG_BOX",
                "side": "SELL",
                "bbox": [120, 80, 180, 140],
                "confidence": 0.82,
                "visible_modes": ["CLEAN_LIVE", "DIAGNOSTICS", "INSPECTOR"],
                "label": "OLD NOW DEBUG BOX",
                "display_label": "OLD NOW DEBUG BOX",
            },
        }
    ]

    clean_state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart},
        active_objects=active_objects,
        overlay_mode="CLEAN_LIVE",
        now_epoch=110.0,
    )
    diagnostics_state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart},
        active_objects=active_objects,
        overlay_mode="DIAGNOSTICS",
        now_epoch=110.0,
    )

    assert clean_state["overlay_count"] == 1
    assert clean_state["renderable_count"] == 0
    assert "OLD NOW DEBUG BOX" in clean_state["unknown_or_unmapped_terms"]
    assert diagnostics_state["renderable_count"] == 1
    assert diagnostics_state["overlays"]["objects"][0]["display_label"] == "DEBUG RAW DETECTION"
    assert diagnostics_state["overlays"]["objects"][0]["label"] == "DEBUG RAW DETECTION"
    assert diagnostics_state["overlays"]["objects"][0]["raw_label"] == "OLD NOW DEBUG BOX"
    assert diagnostics_state["overlay_vocabulary"]["dictionary_coverage_ok"] is True
    assert "OLD NOW DEBUG BOX" in diagnostics_state["unknown_or_unmapped_terms"]


def test_build_live_state_v3_rejects_market_overlays_for_wrong_broker_source(tmp_path: Path) -> None:
    window = _png(tmp_path / "window.png", (640, 360))
    chart = _png(tmp_path / "chart.png", (560, 260))
    session = {
        "session_id": "pocket-live-wrong-source",
        "frame_index": 12,
        "locked_window": {"hwnd": 808, "title": "Pocket Option"},
        "broker_source": {
            "lock_id": "locked-window-808",
            "valid": False,
            "wrong_surface": True,
            "url_valid": True,
            "title_valid": False,
            "pixel_fingerprint_valid": True,
        },
    }
    active_objects = [
        {
            "overlay_id": "blocked-sniper",
            "truth_score": 0.92,
            "overlay": {
                "type": "SNIPER_ENTRY_BOX",
                "side": "BUY",
                "pixel_bbox": [100, 80, 160, 140],
                "confidence": 0.92,
            },
        }
    ]

    state = build_live_state_v3(
        session,
        artifacts={"window": window, "chart": chart},
        active_objects=active_objects,
        now_epoch=110.0,
    )

    assert state["broker_source"]["lock_id"] == "locked-window-808"
    assert state["broker_source"]["valid"] is False
    assert state["broker_source"]["wrong_surface"] is True
    assert state["overlay_count"] == 1
    assert state["renderable_count"] == 0
    assert state["overlays"]["total_count"] == 1
    assert state["overlays"]["renderable_count"] == 0
    assert state["overlays"]["rejected_count"] == 1
    assert state["overlays"]["objects"] == []
    assert state["overlay_objects"] == []
    assert state["reason_if_empty"] == "broker source rejected: wrong surface"
    assert state["overlay_mode"]["reason_if_empty"] == state["reason_if_empty"]


class _FakeTrackerService:
    def __init__(self, artifacts: Mapping[str, Path]) -> None:
        self.artifacts = dict(artifacts)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "status": "running",
            "tracking_enabled": True,
            "last_capture_epoch": 100.0,
            "decision_valid_until_epoch": 120.0,
            "tracking_summary": {
                "chart_region": {"pixel_bbox": [10, 20, 210, 120]},
                "detected_market": "GBP/JPY OTC",
                "detected_timeframe": "M5",
            },
            "latest_signal": {"side": "SELL", "confidence": 0.81},
        }

    def latest_artifact_path(self, _session_id: str, kind: str) -> Path:
        if kind not in self.artifacts:
            raise FileNotFoundError(kind)
        return self.artifacts[kind]

    def latest_model_council_study_packet(self, _session_id: str) -> dict[str, Any]:
        return {
            "packet_id": "study-helper",
            "packet_type": "STUDY_PACKET",
            "execution": {"state": "WATCHING", "side": "SELL"},
            "model_council": {"final_state": "WATCHING", "final_side": "SELL"},
        }

    def latest_model_council_packet(self, _session_id: str) -> dict[str, Any]:
        raise KeyError("no executable packet")

    def latest_model_council_state(self, _session_id: str) -> dict[str, Any]:
        return {"model_council": {"final_state": "WATCHING", "final_side": "SELL"}}


def test_build_live_state_v3_from_tracker_service_resolves_common_inputs(tmp_path: Path) -> None:
    service = _FakeTrackerService({"window": _png(tmp_path / "window.png"), "chart": _png(tmp_path / "chart.png")})

    state = build_live_state_v3_from_tracker_service(
        service,
        "pocket-live-8788",
        model_health_builder=lambda _payload: {"all_required_models_awake": True, "council_status": "AWAKE"},
        shooter_state_loader=lambda session_id: {"session_id": session_id, "state": "WAITING"},
        active_object_loader=lambda _session_id: [
            {
                "overlay_id": "target-1",
                "truth_score": 0.9,
                "overlay": {"type": "TARGET_ZONE_BOX", "pixel_bbox": [100, 40, 170, 80]},
            }
        ],
        registry_loader=lambda _session_id: [],
        now_epoch=105.0,
    )

    assert state["session_id"] == "pocket-live-8788"
    assert state["artifacts"]["window"]["exists"] is True
    assert state["packets"]["study"]["packet_id"] == "study-helper"
    assert state["packets"]["execution"]["exists"] is False
    assert state["provider_status"]["degraded"] is True
    assert any(row["source"] == "model_council_execution_packet" for row in state["provider_status"]["degraded_sources"])
    assert state["market_objects"]["active_count"] == 1
    assert state["overlays"]["objects"][0]["type"] == "TARGET_ZONE_BOX"
    assert state["shooter"]["session_match"] is True
