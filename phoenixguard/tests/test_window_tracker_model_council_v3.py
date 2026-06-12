from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from typing import Any, Mapping

from PIL import Image

from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from phoenixguard.execution.packet_v3 import validate_execution_packet_v3
from phoenixguard.execution.sequence_context import resolve_sequence_context
from phoenixguard.decision.model_council_v3 import ModelCouncilV3
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService


class _FakeCaptureBackend:
    def list_windows(self, query: str | None = None) -> list[dict[str, Any]]:
        return []

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        return Image.new("RGB", (640, 420), (20, 25, 35))


class _FakeTrackingAdapter:
    pass


class _FakeFocusSelectionBackend:
    def is_supported(self) -> bool:
        return False


class _FakeExecutionBackend:
    def __init__(self) -> None:
        self.clicks: list[dict[str, Any]] = []

    def prepare_and_click(self, **kwargs: Any) -> dict[str, Any]:
        self.clicks.append(dict(kwargs))
        return {"status": "clicked", "message": "clicked"}


def _service(tmp_path: Path, execution_backend: _FakeExecutionBackend) -> ContinuousWindowTrackerService:
    service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(),
        tracking_adapter=_FakeTrackingAdapter(),  # type: ignore[arg-type]
        focus_selector_backend=_FakeFocusSelectionBackend(),  # type: ignore[arg-type]
        execution_backend=execution_backend,  # type: ignore[arg-type]
    )
    service._read_broker_surface = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "controls_ready": True,
        "expiry_lock": {"field_ready": True, "configured_seconds": 300, "configured_text": "00:05:00"},
        "amount_lock": {"policy": "PRESERVE_VISIBLE_AMOUNT", "verified": True},
    }
    return service


def test_tracker_live_backend_refuses_raw_signal_without_model_council_packet(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)

    _, state, _ = service._evaluate_broker_execution(
        payload={
            "session_id": "pocket-live-8788",
            "execution_controls": {"live_execution_enabled": True, "execution_mode": "live"},
            "broker_execution_state": {},
        },
        descriptor={"hwnd": 123, "title": "Pocket Option"},
        window_image=Image.new("RGB", (640, 420)),
        surface_image=Image.new("RGB", (640, 420)),
        tracking_summary={"detected_market": "EUR/GBP OTC", "detected_timeframe": "M5"},
        latest_signal={"actionable": True, "execution_action": "BUY", "expiry_seconds": 300},
    )

    assert state["status"] == "blocked_by_runtime"
    assert "Model Council V3 executable packet required" in state["message"]
    assert execution_backend.clicks == []


def test_tracker_live_backend_reads_broker_surface_for_identity_before_v3_packet(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)
    scans: list[dict[str, Any]] = []

    def _identity_broker_scan(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        scans.append(dict(kwargs))
        return {
            "controls_ready": True,
            "broker_surface_hash": "surface-identity",
            "expiry_lock": {"field_ready": True},
            "amount_lock": {"policy": "PRESERVE_VISIBLE_AMOUNT", "verified": True},
        }

    service._read_broker_surface = _identity_broker_scan  # type: ignore[method-assign]

    broker_surface, state, _ = service._evaluate_broker_execution(
        payload={
            "session_id": "pocket-live-8788",
            "execution_controls": {"live_execution_enabled": True, "execution_mode": "live"},
            "broker_execution_state": {},
        },
        descriptor={"hwnd": 123, "title": "Pocket Option"},
        window_image=Image.new("RGB", (640, 420)),
        surface_image=Image.new("RGB", (640, 420)),
        tracking_summary={"detected_market": "EUR/GBP OTC", "detected_timeframe": "M5"},
        latest_signal={"actionable": False, "execution_action": "HOLD"},
    )

    assert state["status"] == "blocked_by_runtime"
    assert scans
    assert scans[0]["source"] == "full_window_gui_identity_probe"
    assert broker_surface["scan_skipped"] is False
    assert broker_surface["broker_surface_hash"] == "surface-identity"
    assert execution_backend.clicks == []


def test_tracker_live_backend_defers_valid_packet_to_standalone_shooter(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)
    sequence_context = {
        "sequence_id": "seq_pocket-live-8788_21",
        "session_id": "pocket-live-8788",
        "sequence_index": 3,
        "frame_start": 1,
        "frame_end": 21,
        "sequence_length": 64,
        "frames_received": 64,
        "frames_used": 64,
        "candle_count": 64,
        "timeframe": "M5",
        "sequence_signature": "seqsig-21-complete",
        "sequence_confidence": 0.91,
        "global_direction": "BUY",
        "local_direction": "BUY",
        "current_phase": "PULLBACK",
        "progression_score": 0.9,
        "progression": [{"stage": "impulse", "direction": "BUY"}],
        "motifs": ["impulse", "pullback"],
        "box_history": [{"label": "H1 BUY", "bbox": [10, 10, 40, 40]}],
        "angle_vectors": [[1.0, 0.0]],
        "sniper_zones": [{"label": "sniper", "bbox": [20, 12, 36, 32]}],
        "target_zones": [{"label": "target", "bbox": [42, 8, 58, 24]}],
        "invalidation_zones": [{"label": "invalidation", "bbox": [6, 42, 18, 56]}],
        "sequence_status": "COMPLETE",
        "frame_range": [1, 21],
        "candle_range": [1, 64],
        "frames_dropped": 0,
        "sequence_age_ms": 50,
        "packet_age_ms": 100,
        "decision_age_ms": 80,
        "model_vote_age_ms": 60,
        "entry_progression": {"progression_stage": "progression", "maturity_score": 0.86},
        "tracking_summary": {"global_direction": "BUY", "local_direction": "BUY"},
        "sequence_history": [{"label": "H1 BUY", "bbox": [10, 10, 40, 40]}],
    }
    packet = build_execution_packet_v3(
        packet_id="pgpkt-tracker-v3",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=20,
        capture_count=21,
        state_version=120,
        side="BUY",
        expiry_seconds=300,
        input_frame_hash="frame-tracker",
        valid_for_seconds=60.0,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "frame-tracker",
            "previous_frame_hash": "frame-tracker-prev",
            "packet_age_ms": 100,
        },
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        sequence_context=sequence_context,
    )

    resolved_sequence_context = resolve_sequence_context(packet)
    assert resolved_sequence_context.sequence_status == "COMPLETE"
    assert resolved_sequence_context.sequence_signature == "seqsig-21-complete"
    assert packet["sequence_id"] == "seq_pocket-live-8788_21"
    assert packet["sequence_signature"] == "seqsig-21-complete"
    assert packet["sequence_length"] == 64
    assert packet["frames_used"] == 64
    assert packet["model_council"]["sequence_context"]["box_history"]

    _, state, _ = service._evaluate_broker_execution(
        payload={
            "session_id": "pocket-live-8788",
            "execution_controls": {"live_execution_enabled": True, "execution_mode": "live"},
            "broker_execution_state": {},
            "model_council_packet": packet,
        },
        descriptor={"hwnd": 123, "title": "Pocket Option"},
        window_image=Image.new("RGB", (640, 420)),
        surface_image=Image.new("RGB", (640, 420)),
        tracking_summary={"detected_market": "EUR/GBP OTC", "detected_timeframe": "M5"},
        latest_signal={"model_council_packet": packet},
    )

    assert state["status"] == "external_shooter_required"
    assert state["side"] == "BUY"
    assert state["lane"] == "MODEL_COUNCIL_PACKET_V3"
    assert state["expiry_seconds"] == 300
    assert execution_backend.clicks == []


def test_tracker_live_backend_rejects_partial_sequence_context_packet(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)
    packet = build_execution_packet_v3(
        packet_id="pgpkt-tracker-v3-partial",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=20,
        capture_count=21,
        state_version=120,
        side="BUY",
        expiry_seconds=300,
        input_frame_hash="frame-tracker",
        valid_for_seconds=60.0,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "frame-tracker",
            "previous_frame_hash": "frame-tracker-prev",
            "packet_age_ms": 100,
        },
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        sequence_context={
            "sequence_id": "seq_pocket-live-8788_21",
            "session_id": "pocket-live-8788",
            "sequence_index": 3,
            "frame_start": 1,
            "frame_end": 21,
            "sequence_length": 12,
            "frames_received": 12,
            "frames_used": 12,
            "candle_count": 12,
            "timeframe": "M5",
            "sequence_signature": "seqsig-partial",
            "sequence_confidence": 0.41,
            "global_direction": "BUY",
            "local_direction": "BUY",
            "current_phase": "PULLBACK",
            "progression_score": 0.41,
            "progression": [],
            "motifs": [],
            "box_history": [{"label": "H1 BUY", "bbox": [10, 10, 40, 40]}],
            "angle_vectors": [],
            "sniper_zones": [],
            "target_zones": [],
            "invalidation_zones": [],
            "sequence_status": "PARTIAL_SEQUENCE",
            "frame_range": [1, 21],
            "candle_range": [1, 12],
            "frames_dropped": 0,
            "sequence_age_ms": 50,
            "packet_age_ms": 100,
            "decision_age_ms": 80,
            "model_vote_age_ms": 60,
            "entry_progression": {"progression_stage": "progression"},
            "tracking_summary": {"global_direction": "BUY", "local_direction": "BUY"},
            "sequence_history": [],
        },
    )

    broken_packet = deepcopy(packet)
    broken_packet["model_council"]["sequence_context"]["sequence_status"] = "PARTIAL_SEQUENCE"
    validation = validate_execution_packet_v3(broken_packet, expected_session_id="pocket-live-8788")

    assert validation.ok is False
    assert "PARTIAL_SEQUENCE_NOT_EXECUTABLE" in validation.reason_codes


def test_sequence_context_not_read_from_wrong_packet_level(tmp_path: Path) -> None:
    packet = build_execution_packet_v3(
        packet_id="pgpkt-shadow-sequence-check",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=10,
        capture_count=11,
        state_version=120,
        side="BUY",
        expiry_seconds=300,
        input_frame_hash="frame-a",
        valid_for_seconds=60.0,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "frame-a",
            "previous_frame_hash": "frame-b",
            "packet_age_ms": 100,
        },
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        sequence_context={
            "sequence_id": "seq-shadow-1",
            "session_id": "pocket-live-8788",
            "sequence_index": 7,
            "frame_start": 1,
            "frame_end": 11,
            "sequence_length": 64,
            "frames_received": 64,
            "frames_used": 64,
            "candle_count": 64,
            "timeframe": "M5",
            "sequence_signature": "seqsig-shadow-1",
            "sequence_confidence": 0.93,
            "global_direction": "BUY",
            "local_direction": "BUY",
            "current_phase": "PULLBACK",
            "progression_score": 0.82,
            "progression": [{"stage": "impulse", "direction": "BUY"}],
            "motifs": ["impulse"],
            "box_history": [{"label": "H1 BUY", "bbox": [10, 10, 20, 20]}],
            "angle_vectors": [[1.0, 0.0]],
            "sniper_zones": [],
            "target_zones": [],
            "invalidation_zones": [],
            "sequence_status": "COMPLETE",
            "frame_range": [1, 11],
            "candle_range": [1, 64],
            "frames_dropped": 0,
            "sequence_age_ms": 50,
            "packet_age_ms": 100,
            "decision_age_ms": 80,
            "model_vote_age_ms": 60,
            "entry_progression": {"progression_stage": "progression"},
            "tracking_summary": {"global_direction": "BUY", "local_direction": "BUY"},
            "sequence_history": [{"label": "H1 BUY", "bbox": [10, 10, 20, 20]}],
        },
    )
    packet["sequence_id"] = "wrong-top-level-id"
    resolved = resolve_sequence_context(packet)

    assert resolved.sequence_id != "wrong-top-level-id"
    assert resolved.sequence_id == packet["model_council"]["sequence_context"]["sequence_id"]


def test_locked_surface_fallback_creates_paper_safe_instrument_context(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)

    snapshot = service._build_model_council_v3_snapshot(
        payload={
            "session_id": "pocket-live-8788",
            "execution_controls": {
                "live_execution_enabled": True,
                "execution_mode": "live",
                "allow_locked_surface_identity_fallback": True,
            },
            "manual_focus_region": {"enabled": True, "pixel_bbox": [10, 20, 600, 380]},
        },
        tracking_summary={
            "detected_market": "",
            "detected_timeframe": "M5",
            "market_context": {
                "dominant_side": "BUY",
                "inside_valid_trigger_zone": True,
                "opposing_force_distance_ok": True,
            },
        },
        latest_signal={"candidate_action": "BUY", "confidence": 0.7},
        frame_index=10,
        capture_count=11,
        input_frame_hash="frame-a",
        capture_started_epoch=1000.0,
    )

    # mark sequence as complete for V3 canonicalization (requires length >= 50)
    snapshot["sequence_length"] = 50
    snapshot["frames_used"] = 50
    snapshot["frames_received"] = 50
    snapshot["sequence_confidence"] = 0.92

    context = snapshot["instrument_context"]
    assert context["identity_state"] == "IDENTITY_LOCKED_BY_USER_PROFILE"
    assert context["display_symbol"] == "USER_LOCKED_ACTIVE_CHART"
    assert context["paper_safe"] is True
    assert context["broker_click_safe"] is False


def test_locked_surface_fallback_is_broker_click_safe_when_profile_is_proven(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)

    snapshot = service._build_model_council_v3_snapshot(
        payload={
            "session_id": "pocket-live-8788",
            "execution_controls": {
                "live_execution_enabled": True,
                "execution_mode": "live",
                "allow_locked_surface_identity_fallback": True,
            },
            "manual_focus_region": {"enabled": True, "pixel_bbox": [10, 20, 600, 380]},
            "locked_window": {"hwnd": 123, "title": "Pocket Option", "bbox": [0, 0, 640, 420], "width": 640, "height": 420},
            "broker_surface": {
                "controls_ready": True,
                "broker_surface_hash": "surface-a",
            },
        },
        tracking_summary={
            "detected_market": "",
            "detected_timeframe": "M5",
            "market_context": {
                "dominant_side": "BUY",
                "inside_valid_trigger_zone": True,
                "opposing_force_distance_ok": True,
            },
        },
        latest_signal={"candidate_action": "BUY", "confidence": 0.7},
        frame_index=10,
        capture_count=11,
        input_frame_hash="frame-a",
        capture_started_epoch=1000.0,
    )

    # mark sequence as complete for V3 canonicalization (requires length >= 50)
    snapshot["sequence_length"] = 50
    snapshot["frames_used"] = 50
    snapshot["frames_received"] = 50
    snapshot["sequence_confidence"] = 0.92

    context = snapshot["instrument_context"]
    assert context["identity_state"] == "IDENTITY_LOCKED_BY_USER_PROFILE"
    assert context["display_symbol"] == "USER_LOCKED_ACTIVE_CHART"
    assert context["paper_safe"] is True
    assert context["broker_click_safe"] is True


def test_actionable_broker_timing_becomes_model_council_execution_evidence(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)
    payload = {
        "session_id": "pocket-live-8788",
        "execution_controls": {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "allow_locked_surface_identity_fallback": True,
        },
        "manual_focus_region": {"enabled": True, "pixel_bbox": [10, 20, 600, 380]},
        "locked_window": {"hwnd": 123, "title": "Pocket Option", "bbox": [0, 0, 640, 420], "width": 640, "height": 420},
        "broker_surface": {"controls_ready": True, "broker_surface_hash": "surface-a"},
        "broker_execution_state": {
            "status": "blocked_by_runtime",
            "side": "SELL",
            "lane": "OPPOSING_FORCE_REACTION",
            "actionable": True,
            "expiry_seconds": 900,
            "execution_timing": {
                "side": "SELL",
                "entry_allowed": True,
                "entry_area_score": 0.86,
                "opposing_force_risk": 0.82,
                "expiry_seconds": 900,
                "rationale": "Active resistance reaction accepted by PhoenixGuard timing.",
            },
        },
    }

    snapshot = service._build_model_council_v3_snapshot(
        payload=payload,
        tracking_summary={
            "detected_market": "",
            "detected_timeframe": "M5",
            "market_context": {
                "dominant_side": "BUY",
                "inside_valid_trigger_zone": False,
                "opposing_force_distance_ok": True,
            },
        },
        latest_signal={"action": "HOLD", "confidence": 0.2},
        frame_index=10,
        capture_count=11,
        input_frame_hash="frame-a",
        capture_started_epoch=1000.0,
    )

    assert snapshot["candidate_side"] == "SELL"
    assert snapshot["entry_quality"] == "GOOD_ENTRY"
    assert snapshot["timing"]["state"] == "READY"
    assert snapshot["timing"]["expiry_seconds"] == 900
    assert snapshot["inside_valid_trigger_zone"] is True
    assert snapshot["instrument_context"]["broker_click_safe"] is True

    council = ModelCouncilV3()
    first = council.evaluate(snapshot, now_epoch=1000.0)
    assert first["execution"]["enabled"] is False
    second = dict(snapshot)
    second["frame_id"] = 11
    second["capture_count"] = 12
    second["state_version"] = int(snapshot["state_version"]) + 1
    second["input_frame_hash"] = "frame-b"
    packet = council.evaluate(second, now_epoch=1000.5)
    # removed temporary debug prints
    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "SELL"
    assert packet["execution"]["expiry_seconds"] == 900
    assert packet["trade_permission"]["permission_state"] == "GRANTED"


def test_near_trigger_kernel_candidate_becomes_model_council_execution_evidence(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    service = _service(tmp_path, execution_backend)
    payload = {
        "session_id": "pocket-live-8788",
        "execution_controls": {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "allow_locked_surface_identity_fallback": True,
        },
        "manual_focus_region": {"enabled": True, "pixel_bbox": [10, 20, 600, 380]},
        "locked_window": {"hwnd": 123, "title": "Pocket Option", "bbox": [0, 0, 640, 420], "width": 640, "height": 420},
        "broker_surface": {"controls_ready": True, "broker_surface_hash": "surface-a"},
        "broker_execution_state": {
            "status": "blocked_by_runtime",
            "side": "HOLD",
            "lane": "LIVE_MARKET_FLOW_WAIT",
            "actionable": False,
        },
    }
    latest_signal = {
        "action": "SELL",
        "candidate_action": "SELL",
        "execution_action": "HOLD",
        "confidence": 0.81,
        "effective_confidence": 0.81,
        "focus_timeframe": "M5",
        "entry_quality": "NONE",
        "decision": "WATCH_FOR_TRIGGER",
        "setup_state": "ARMED",
        "decision_kernel": {
            "state": "ARMED",
            "decision": "WATCH_FOR_TRIGGER",
            "dominant_side": "sell",
            "candle_execution_side": "sell",
            "directional_edge": 1.0,
            "belief_sell": 0.84,
            "distance_to_trigger": 0.012,
            "p_trigger_next_1": 0.88,
            "p_target_before_invalidation": 0.64,
            "target_race_probabilities": {"target": 0.62},
            "firewall_action": "WAIT",
            "firewall_reasons": ["ADVISORY_FIREWALL_ONLY", "EXPECTED_VALUE_NEGATIVE"],
        },
    }

    snapshot = service._build_model_council_v3_snapshot(
        payload=payload,
        tracking_summary={
            "detected_market": "",
            "detected_timeframe": "M5",
            "market_context": {"dominant_side": "SELL"},
        },
        latest_signal=latest_signal,
        frame_index=20,
        capture_count=21,
        input_frame_hash="kernel-frame-a",
        capture_started_epoch=1000.0,
    )

    assert snapshot["candidate_side"] == "SELL"
    assert snapshot["entry_quality"] == "GOOD_ENTRY"
    assert snapshot["timing"]["state"] == "READY"
    assert snapshot["timing"]["expiry_seconds"] == 300
    assert snapshot["inside_valid_trigger_zone"] is True
    assert snapshot["v3_execution_candidate"]["source"] == "decision_kernel_trigger_proximity"

    council = ModelCouncilV3()
    first = council.evaluate(snapshot, now_epoch=1000.0)
    assert first["execution"]["enabled"] is False
    second = dict(snapshot)
    second["frame_id"] = 21
    second["capture_count"] = 22
    second["state_version"] = int(snapshot["state_version"]) + 1
    second["input_frame_hash"] = "kernel-frame-b"
    packet = council.evaluate(second, now_epoch=1000.5)
    # removed temporary debug prints
    assert packet["execution"]["enabled"] is True
    assert packet["execution"]["side"] == "SELL"
    assert packet["execution"]["expiry_seconds"] == 300
    assert packet["trade_permission"]["permission_state"] == "GRANTED"
