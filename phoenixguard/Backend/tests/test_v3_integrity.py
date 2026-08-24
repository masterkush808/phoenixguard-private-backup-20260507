from __future__ import annotations
import pytest

import importlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from phoenixguard.execution.packet_v3 import build_execution_packet_v3, validate_execution_packet_v3
from phoenixguard.mobile_api.app import create_app
from phoenixguard.paths import PACKAGE_ROOT


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PACKAGE_ROOT / "V3_CANONICAL_MANIFEST.json"


def test_v3_canonical_manifest_exists_and_required_files_exist() -> None:
    manifest_path = MANIFEST_PATH
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["active_version"] == "V3"
    for raw in manifest["required_files"]:
        assert (ROOT / raw).exists(), raw


def test_import_v3_runtime_components() -> None:
    modules = [
        "phoenixguard.mobile_api.app",
        "phoenixguard.decision.model_council_v3",
        "phoenixguard.decision.market_reality_engine",
        "phoenixguard.execution.packet_v3",
        "phoenixguard.execution.floating_state_reducer",
        "phoenixguard.runtime.observability_v3",
    ]
    for module_name in modules:
        assert importlib.import_module(module_name)


def test_shooter_calibration_artifacts_retired() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["canonical_runtime"]["calibration_manifest"] is False
    assert manifest["required_calibration_targets"] == []
    assert not (ROOT / "808_shooter_boxes.json").exists()
    assert not (ROOT / "user_calibration_manifest.json").exists()
    assert not (ROOT / "config" / "shooter_broker_timing_profile.json").exists()


def test_final_live_profile_declares_single_canonical_launch_path() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    launch_profile = manifest["launch_profile"]
    canonical = (ROOT / launch_profile["launcher"]).read_text(encoding="utf-8")
    engine = (ROOT / launch_profile["engine_launcher"]).read_text(encoding="utf-8")

    assert launch_profile["production"] == "FINAL_LIVE"
    assert launch_profile["shooter_mode"] == "PACKAGE_REPORTER"
    assert launch_profile["live_click_arm"] == "retired"
    assert "start_phoenixguard_full_local.ps1" in canonical
    assert "FINAL_LIVE" in engine
    assert "Legacy V1/V2: OFF" in engine
    assert "Startup Test Signal: REMOVED" in engine


def test_runtime_config_collapses_to_final_live(monkeypatch: pytest.MonkeyPatch) -> None:
    from phoenixguard.core.config import RuntimeConfig

    monkeypatch.setenv("PHOENIXGUARD_PROFILE", "legacy-old-profile")
    runtime = RuntimeConfig()

    assert runtime.runtime_profile == "FINAL_LIVE"
    assert runtime.enable_test_time_adaptation is True
    assert runtime.enable_replay_continual_learning is True
    assert runtime.prefer_foundation_grounding is True
    assert runtime.auto_model_council_on_inference is True


def test_floating_state_endpoint_uses_clean_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    # Direct disk sidecar reads would shadow the injected tracker with real
    # runtime state sharing this session id; keep the fixture authoritative.
    monkeypatch.setenv("PHOENIXGUARD_WINDOW_TRACKER_DIRECT_READ", "0")

    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "status": "running", "model_health": {"models_awake": 7, "models_total": 7}}

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable packet")

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return {
                "packet_id": "pgpkt_test123456",
                "packet_type": "STUDY_PACKET",
                "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
                "model_council": {"final_execution_score": 0.5, "execution_threshold": 0.7},
                "execution_lane": {"name": "SNIPER_ZONE_ENTRY", "accepted": False, "reason": "NO_EXECUTION_LANE_ACCEPTED"},
            }

    client: Any = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/floating/state?session_id=pocket-live-8788")
    assert response.status_code == 200
    payload = response.json()
    assert payload["packet"]["type"] == "STUDY"
    assert payload["council"]["lane_short"] == "SNIPER"
    assert payload["council"]["reason_short"] == "No execution lane accepted"
    assert "inspector" not in payload
    assert "n/a" not in str(payload).lower()

    inspector_response = client.get("/v1/mobile/floating/state?session_id=pocket-live-8788&inspector=true")
    assert inspector_response.status_code == 200
    assert "inspector" in inspector_response.json()


def test_floating_state_carries_drawable_overlay_objects_when_counts_are_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Direct disk sidecar reads would shadow the injected tracker with real
    # runtime state sharing this session id; keep the fixture authoritative.
    monkeypatch.setenv("PHOENIXGUARD_WINDOW_TRACKER_DIRECT_READ", "0")

    overlay_object: dict[str, object] = {
        "overlay_id": "demand-1",
        "object_id": "demand-1",
        "track_id": "demand-1",
        "type": "DEMAND_ZONE",
        "side": "BUY",
        "source_agent": "model_council_v3",
        "source_version": "PG_V3_OVERLAY_OBJECT_V1",
        "broker_source_lock_id": "broker-lock-1",
        "frame_id": 10,
        "sequence_id": "seq-10",
        "chart_transform_id": "ct-10",
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "anchor_type": "CANDLES",
        "anchor_candles": [4, 5],
        "anchor_candle_indices": [4, 5],
        "anchor_price_band": {"top_y": 100, "bottom_y": 120},
        "anchor_time_span": {"left_x": 20, "right_x": 80},
        "anchor_evidence": {"valid": True, "evidence_type": "support_reclaim"},
        "bounds": [20, 100, 80, 120],
        "truth_score": 0.84,
        "confidence": 0.88,
        "lifecycle_state": "ACTIVE",
        "layer": "supply_demand",
        "visible_modes": ["CLEAN_LIVE", "SUPPLY_DEMAND"],
        "ttl_ms": 9000,
        "reason": "anchored demand",
        "display_state": "FULL",
        "style": {"stroke": "#00a676", "fill_opacity": 0.08},
    }

    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "frame_id": 10,
                "frame_index": 10,
                "state_version": 10,
                "overlay_count": 1,
                "renderable_count": 1,
                "overlay_object_frame_id": 10,
                "chart_transform_id": "ct-10",
                "overlay_objects": [overlay_object],
                "model_health": {"models_awake": 7, "models_total": 7},
            }

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError(session_id)

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError(session_id)

    client: Any = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/floating/state?session_id=pocket-live-8788")

    assert response.status_code == 200
    payload = response.json()
    overlays = payload["overlays"]
    assert overlays["renderable_count"] == 1
    assert overlays["overlay_object_frame_id"] == 10
    assert overlays["chart_transform_id"] == "ct-10"
    assert len(overlays["objects"]) == 1
    assert overlays["objects"][0]["overlay_id"] == "demand-1"


def test_tracker_artifact_endpoint_handles_pruned_file_race(tmp_path: Path) -> None:
    missing_path = tmp_path / "already_pruned_chart.png"

    class FakeTracker:
        def latest_artifact_path(self, session_id: str, artifact_kind: str) -> Path:
            assert session_id == "pocket-live-8788"
            assert artifact_kind == "chart"
            return missing_path

    client: Any = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/window-tracker/sessions/pocket-live-8788/artifacts/latest-chart")

    assert response.status_code == 404
    assert "expired" in response.json()["detail"].lower() or "readable" in response.json()["detail"].lower()


def test_execution_packet_requires_sequence_signature() -> None:
    packet = build_execution_packet_v3(
        packet_id="pgpkt-signature-check",
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
            "sequence_id": "seq-signature-check",
            "session_id": "pocket-live-8788",
            "sequence_index": 1,
            "frame_start": 1,
            "frame_end": 64,
            "sequence_length": 64,
            "frames_received": 64,
            "frames_used": 64,
            "candle_count": 64,
            "timeframe": "M5",
            "sequence_signature": "sig-check-1",
            "sequence_confidence": 0.95,
            "global_direction": "BUY",
            "local_direction": "BUY",
            "current_phase": "PULLBACK",
            "progression_score": 0.9,
            "progression": [{"stage": "impulse", "direction": "BUY"}],
            "motifs": ["impulse"],
            "box_history": [{"label": "H1 BUY", "bbox": [10, 10, 20, 20]}],
            "angle_vectors": [[1.0, 0.0]],
            "sniper_zones": [],
            "target_zones": [],
            "invalidation_zones": [],
            "sequence_status": "COMPLETE",
            "frame_range": [1, 64],
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

    broken_packet = deepcopy(packet)
    broken_packet["model_council"]["sequence_context"].pop("sequence_signature")

    validation = validate_execution_packet_v3(broken_packet, expected_session_id="pocket-live-8788")

    assert validation.ok is False
    assert "MISSING_SEQUENCE_SIGNATURE" in validation.reason_codes
