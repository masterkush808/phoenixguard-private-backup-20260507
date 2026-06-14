from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping

from fastapi.testclient import TestClient

import phoenixguard.mobile_api.app as mobile_app
from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from phoenixguard.mobile_api.app import create_app
from phoenixguard.runtime.cache_v3 import (
    CACHE_SCHEMA_VERSION,
    EXECUTION_PACKET_SCHEMA_VERSION,
    attach_cache_v3_metadata,
    validate_cache_record,
    validate_execution_packet_for_live_execution,
    validate_study_packet_for_current_state,
)
from phoenixguard.runtime.observability_v3 import (
    BAD_ENTRY_CLASS_001,
    append_forensic_decision_log,
    build_intelligence_health,
    build_model_council_health,
    build_model_council_health_from_session,
    evaluate_bad_entry_replay,
    model_health_allows_executable,
    packet_health_allows_executable,
    record_paper_mode_decision,
)


def _cache_record(**updates: Any) -> dict[str, Any]:
    record = attach_cache_v3_metadata(
        {"payload": "cached"},
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=10,
        capture_count=12,
        state_version=99,
        input_frame_hash="frame-a",
        viewport_hash="viewport-a",
        model_version_hash="model-a",
        preprocess_version_hash="pre-a",
        calibration_profile_id="cal-a",
        created_epoch=1000.0,
        valid_until_epoch=1002.0,
    )
    record.update(updates)
    return record


def _execution_packet(**updates: Any) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "schema_version": EXECUTION_PACKET_SCHEMA_VERSION,
        "packet_id": "pgpkt-test",
        "session_id": "pocket-live-8788",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": 10,
        "capture_count": 12,
        "state_version": 99,
        "created_epoch_sec": 1000.0,
        "valid_until_epoch_sec": 1002.0,
        "created_epoch": 1000.0,
        "valid_until_epoch": 1002.0,
        "live_integrity": {
            "is_live": True,
            "cache_status": "fresh",
            "input_frame_hash": "frame-a",
        },
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": "BUY",
            "expiry_seconds": 300,
            "time_sequence": {"mode": "TYPE_OR_ADJUST", "target_seconds": 300},
        },
        "model_council": {"final_state": "EXECUTABLE", "final_side": "BUY"},
        "runtime_model_health": {"all_required_models_awake": True},
    }
    packet.update(updates)
    return packet


def test_old_cache_schema_rejected() -> None:
    result = validate_cache_record(
        _cache_record(cache_schema_version="PG_CACHE_V2"),
        now_epoch=1001.0,
    )

    assert result.ok is False
    assert "old_or_missing_cache_schema" in result.reasons


def test_cache_entry_requires_schema_version() -> None:
    record = _cache_record()
    record.pop("schema_version")

    result = validate_cache_record(record, now_epoch=1001.0)

    assert result.ok is False
    assert "missing_or_invalid_cache_entry_schema_version" in result.reasons


def test_cache_entry_requires_created_epoch_sec() -> None:
    record = _cache_record()
    record.pop("created_epoch_sec")

    result = validate_cache_record(record, now_epoch=1001.0)

    assert result.ok is False
    assert "created_epoch_sec_missing_or_invalid" in result.reasons


def test_cache_entry_rejected_after_ttl() -> None:
    result = validate_cache_record(
        _cache_record(created_epoch_sec=1000.0, valid_until_epoch_sec=1001.0, expiry_seconds=1),
        now_epoch=1001.0,
    )

    assert result.ok is False
    assert "cache_entry_expired" in result.reasons


def test_study_packet_expires_by_ttl() -> None:
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_type": "STUDY_PACKET",
        "packet_id": "study-expired",
        "created_epoch_sec": 1000.0,
        "execution": {"enabled": False, "state": "WATCHING", "side": "NONE"},
        "model_council": {"final_state": "WATCHING", "final_side": "NONE"},
        "promotion_trace": {"blocked_by": "ttl_test", "denied_at": "ttl_test", "next_required": "fresh read"},
    }

    result = validate_study_packet_for_current_state(study_packet, now_epoch=1008.1, ttl_seconds=8.0)

    assert result.ok is False
    assert "study_packet_expired" in result.reasons


def test_expired_packet_not_visible_as_current_execution() -> None:
    packet = _execution_packet(valid_until_epoch_sec=999.0, valid_until_epoch=1005.0)

    result = validate_execution_packet_for_live_execution(packet, now_epoch=1000.0)

    assert result.ok is False
    assert "packet_expired" in result.reasons


def test_cache_invalidates_on_frame_hash_change() -> None:
    result = validate_cache_record(
        _cache_record(input_frame_hash="frame-b"),
        expected_context={"input_frame_hash": "frame-a"},
        now_epoch=1001.0,
    )

    assert result.ok is False
    assert any(reason.startswith("input_frame_hash_mismatch") for reason in result.reasons)


def test_cache_invalidates_on_symbol_change() -> None:
    result = validate_cache_record(
        _cache_record(symbol="GBP/JPY OTC"),
        expected_context={"symbol": "EUR/GBP OTC"},
        now_epoch=1001.0,
    )

    assert result.ok is False
    assert any(reason.startswith("symbol_mismatch") for reason in result.reasons)


def test_cache_invalidates_on_timeframe_change() -> None:
    result = validate_cache_record(
        _cache_record(timeframe="M1"),
        expected_context={"timeframe": "M5"},
        now_epoch=1001.0,
    )

    assert result.ok is False
    assert any(reason.startswith("timeframe_mismatch") for reason in result.reasons)


def test_cache_invalidates_on_model_version_change() -> None:
    result = validate_cache_record(
        _cache_record(model_version_hash="model-b"),
        expected_context={"model_version_hash": "model-a"},
        now_epoch=1001.0,
    )

    assert result.ok is False
    assert any(reason.startswith("model_version_hash_mismatch") for reason in result.reasons)


def test_stale_packet_cannot_execute() -> None:
    packet = _execution_packet(valid_until_epoch=999.0)

    result = validate_execution_packet_for_live_execution(packet, now_epoch=1000.0)

    assert result.ok is False
    assert "packet_expired" in result.reasons
    assert packet_health_allows_executable(packet, now_epoch=1000.0) is False


def test_model_heartbeat_missing_blocks_executable_packet() -> None:
    health = build_model_council_health(
        session_id="pocket-live-8788",
        heartbeats=[],
        required_roles=("global_structure",),
        now_epoch=1000.0,
    )

    assert health["all_required_models_awake"] is False
    assert health["models"][0]["status"] == "STALE"
    assert model_health_allows_executable(health) is False


def test_all_required_models_awake_permits_packet_publication() -> None:
    health = build_model_council_health(
        session_id="pocket-live-8788",
        heartbeats=[
            {
                "name": "global_structure_model",
                "role": "global_structure",
                "status": "AWAKE",
                "last_heartbeat_epoch": 999.5,
                "last_inference_epoch": 999.8,
                "latency_ms": 42.0,
                "queue_depth": 0,
            }
        ],
        required_roles=("global_structure",),
        now_epoch=1000.0,
    )

    assert health["all_required_models_awake"] is True
    assert health["council_status"] == "AWAKE"
    assert model_health_allows_executable(health) is True


def test_forensic_log_written_every_cycle(tmp_path: Path) -> None:
    log_path = tmp_path / "forensic" / "decision_cycles.jsonl"
    decision = {"packet_id": "pgpkt-test", "will_click": False, "reason": "WAITING_SECOND_LIVE_READ"}

    row = append_forensic_decision_log(log_path, decision, packet=_execution_packet(), now_epoch=1000.0)

    assert row["cache_schema_version"] == CACHE_SCHEMA_VERSION
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["decision"]["reason"] == "WAITING_SECOND_LIVE_READ"


def test_bad_entry_replay_blocks_execution() -> None:
    replay_record = {
        "schema_version": EXECUTION_PACKET_SCHEMA_VERSION,
        "block_reason": BAD_ENTRY_CLASS_001,
        "market_context": {"is_late_chase": True},
        "angle_context": {"late_chase_risk": True, "post_impulse_wait_required": True},
        "history_context": {"similarity_state": "RESEMBLES_LATE_LOSS"},
    }

    result = evaluate_bad_entry_replay(replay_record)

    assert result["execution_allowed"] is False
    assert result["block_reason"] == BAD_ENTRY_CLASS_001


def test_paper_mode_records_without_clicking(tmp_path: Path) -> None:
    log_path = tmp_path / "paper" / "decisions.jsonl"
    clicked: list[str] = []

    row = record_paper_mode_decision(
        log_path,
        _execution_packet(),
        {"will_click": True, "reason": "PAPER_ONLY"},
        now_epoch=1000.0,
        click_callback=lambda: clicked.append("clicked"),
    )

    assert row["paper_mode"] is True
    assert row["would_click"] is True
    assert row["actual_clicked"] is False
    assert clicked == []
    assert json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])["event"] == "paper_mode_decision"


def test_model_council_health_endpoint_reads_tracker_session() -> None:
    class _FakeTracker:
        def __init__(self) -> None:
            self.session = {
                "session_id": "pocket-live-8788",
                "cache_metrics": {"hits": 2, "misses": 1, "rejects": 1, "entries": 3},
                "dropped_frames": 1,
                "latest_signal": {
                    "runtime_model_health": {
                        "all_required_models_awake": True,
                        "max_model_latency_ms": 18.0,
                        "queue_depth": 2,
                    },
                    "execution_packet": _execution_packet(
                        created_epoch=1000.0,
                        valid_until_epoch=1002.0,
                        live_integrity={
                            "is_live": True,
                            "cache_status": "fresh",
                            "input_frame_hash": "frame-a",
                            "packet_age_ms": 500,
                        },
                    ),
                    "decision_kernel": {
                        "p_target_before_invalidation": 0.61,
                        "next_most_likely_event": "trigger",
                    }
                },
                "broker_execution_state": {
                    "last_result": {"status": "won", "outcome": "win", "timing_grade": "quick_capture"}
                },
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, Any]]:
            return [self.session]

        def get_session(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return self.session

    client = TestClient(create_app(window_tracker_service=_FakeTracker()))

    response = client.get("/v1/mobile/model-council/health?session_id=pocket-live-8788")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "pocket-live-8788"
    assert payload["all_required_models_awake"] is True
    assert payload["queue_depth"] == 2
    assert payload["runtime_telemetry"]["packet"]["age_sec"] == 0.5
    assert payload["runtime_telemetry"]["cache"]["rejects"] == 1
    assert payload["runtime_telemetry"]["paper"]["wins"] == 1
    assert payload["runtime_telemetry"]["path_quality"]["label"] == "MEDIUM"


def test_model_council_latest_execution_packet_endpoints_return_v3_packet() -> None:
    now = time.time()
    packet = build_execution_packet_v3(
        packet_id="pgpkt-endpoint",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=20,
        capture_count=21,
        state_version=120,
        side="BUY",
        expiry_seconds=300,
        input_frame_hash="frame-endpoint",
        created_epoch=now,
        valid_until_epoch=now + 2.0,
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
    )

    class _FakeTracker:
        def list_sessions(self, limit: int = 1) -> list[dict[str, Any]]:
            return [{"session_id": "pocket-live-8788", "model_council_packet": packet}]

        def get_session(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return {"session_id": session_id, "model_council_packet": packet}

        def latest_model_council_packet(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return packet

    client = TestClient(create_app(window_tracker_service=_FakeTracker()))

    direct = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest")
    alias = client.get("/v1/mobile/model-council/execution/latest?session_id=pocket-live-8788")

    assert direct.status_code == 200
    assert alias.status_code == 200
    assert direct.json()["schema_version"] == "PG_EXECUTION_PACKET_V3"
    assert alias.json()["packet_id"] == "pgpkt-endpoint"


def test_dashboard_asset_route_rejects_encoded_path_traversal() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/mobile/window-tracker/assets/js/%2e%2e/%2e%2e/.hintrc")

    assert response.status_code == 404


def test_live_state_v3_direct_read_waits_for_missing_shooter_handshake(monkeypatch: Any, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    mobile_app._LIVE_STATE_V3_CACHE.clear()
    mobile_app._LIVE_STATE_REGISTRY_CACHE.clear()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 1,
                "last_capture_epoch": time.time(),
                "tracking_summary": {},
                "latest_signal": {},
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788")

    assert response.status_code == 200
    payload = response.json()
    assert payload["shooter"]["available"] is False
    assert payload["shooter"]["state"] == "WAITING"
    assert payload["shooter_state"]["next_required"] == "shooter handshake publish"

    compact_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?compact=1")

    assert compact_response.status_code == 200
    compact = compact_response.json()
    assert compact["shooter"]["available"] is False
    assert compact["overlay_objects"] == compact["overlays"]["objects"]
    assert "market_object_registry" not in compact


def test_live_state_v3_direct_read_skips_legacy_registry_when_v3_sources_exist(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    mobile_app._LIVE_STATE_V3_CACHE.clear()
    mobile_app._LIVE_STATE_REGISTRY_CACHE.clear()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    now_epoch = time.time()
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 1,
                "frame_index": 1,
                "last_capture_epoch": now_epoch,
                "tracking_summary": {
                    "tracked_candles": [
                        {
                            "track_id": "candle-1",
                            "bbox": [10, 20, 30, 80],
                            "direction": "up",
                            "confidence": 0.9,
                        }
                    ]
                },
                "latest_signal": {},
            }
        ),
        encoding="utf-8",
    )

    def fail_registry_load(*_args: Any, **_kwargs: Any) -> list[Mapping[str, Any]]:
        raise AssertionError("legacy registry loader should not be used for V3 session overlays")

    monkeypatch.setattr(mobile_app, "load_recent_market_objects", fail_registry_load)
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?compact=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_status"]["direct_registry_source"] == "skipped_session_v3_overlay_sources"
    assert payload["provider_status"]["direct_registry_entries"] == 0


def test_live_state_v3_direct_read_invalidates_cache_when_display_state_advances(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    mobile_app._LIVE_STATE_V3_CACHE.clear()
    mobile_app._LIVE_STATE_REGISTRY_CACHE.clear()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    first_window = artifact_dir / "000001_window.png"
    second_window = artifact_dir / "000002_window.png"
    first_window.write_bytes(b"first-window")
    second_window.write_bytes(b"second-window")
    now_epoch = time.time()
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 1,
                "frame_index": 1,
                "display_frame_id": 1,
                "display_capture_epoch": now_epoch,
                "display_published_epoch": now_epoch,
                "last_capture_epoch": now_epoch,
                "last_window_path": str(first_window),
                "tracking_summary": {},
                "latest_signal": {},
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    first_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788")

    assert first_response.status_code == 200
    assert first_response.json()["broker_surface_frame"]["frame_id"] == 1

    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "capture_count": 2,
                "display_frame_id": 2,
                "display_capture_epoch": now_epoch + 1.0,
                "display_published_epoch": now_epoch + 1.0,
                "last_window_path": str(second_window),
                "last_frame_path": str(second_window),
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    second_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788")

    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["broker_surface_frame"]["frame_id"] == 2
    assert second_payload["artifacts"]["window"]["path"] == str(second_window)


def test_performance_trace_v3_uses_direct_display_state_fast_path(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    mobile_app._LIVE_STATE_V3_CACHE.clear()
    mobile_app._LIVE_STATE_REGISTRY_CACHE.clear()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000002_window.jpg"
    window.write_bytes(b"window")
    now_epoch = time.time()
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 1,
                "frame_index": 1,
                "display_frame_id": 1,
                "overlay_frame_id": 1,
                "model_vote_frame_id": 1,
                "display_published_epoch": now_epoch - 20.0,
                "last_capture_epoch": now_epoch - 20.0,
                "last_window_path": str(window),
                "overlay_source_window_signature": "display",
                "tracking_summary": {},
                "latest_signal": {},
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "display_frame_id": 2,
                "display_capture_epoch": now_epoch,
                "display_published_epoch": now_epoch,
                "last_display_window_path": str(window),
                "last_display_surface_signature": "display",
                "last_window_surface_signature": "display",
                "overlay_source_window_signature": "old-surface",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/performance/trace/v3/pocket-live-8788")

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame_id"] == 2
    assert payload["display_frame"]["frame_id"] == 2
    assert payload["display_frame"]["age_ms"] < 2500
    assert payload["timing_trace"]["frame_gap_status"] in {"ALIGNED", "AUTHORITY_LOCKED"}
    assert payload["timing_trace"]["surface_signature_aligned"] is True
    assert payload["display_frame"]["url"] == str(window)


def test_performance_trace_v3_uses_compact_display_state_without_session_json(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    mobile_app._LIVE_STATE_V3_CACHE.clear()
    mobile_app._LIVE_STATE_REGISTRY_CACHE.clear()
    mobile_app._DIRECT_PERFORMANCE_TRACE_CACHE.clear()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000022_window.jpg"
    overlay = artifact_dir / "000002_overlay.png"
    window.write_bytes(b"window")
    overlay.write_bytes(b"overlay")
    now_epoch = time.time()
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "display_frame_id": 22,
                "capture_count": 22,
                "frame_index": 2,
                "chart_frame_id": 2,
                "overlay_frame_id": 2,
                "model_vote_frame_id": 2,
                "display_capture_epoch": now_epoch,
                "display_published_epoch": now_epoch,
                "last_capture_epoch": now_epoch,
                "last_capture_started_epoch": now_epoch,
                "last_display_window_path": str(window),
                "last_overlay_path": str(overlay),
                "last_display_surface_signature": "display",
                "last_window_surface_signature": "display",
                "overlay_source_window_signature": "studied",
                "display_snapshot_only_v3": True,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/performance/trace/v3/pocket-live-8788")

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame_id"] == 22
    assert payload["display_frame"]["url"] == str(window)
    assert payload["model_state"]["models_awake"] == 7
    assert payload["timing_trace"]["display_only_authority_locked"] is True
    assert payload["timing_trace"]["frame_gap_status"] == "AUTHORITY_LOCKED"


def test_performance_trace_v3_reuses_short_direct_cache_on_read_race(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    monkeypatch.setenv("PHOENIXGUARD_PERFORMANCE_TRACE_DIRECT_ONLY", "1")
    monkeypatch.setenv("PHOENIXGUARD_DIRECT_PERFORMANCE_TRACE_CACHE_TTL_SEC", "5")
    mobile_app._LIVE_STATE_V3_CACHE.clear()
    mobile_app._LIVE_STATE_REGISTRY_CACHE.clear()
    mobile_app._DIRECT_PERFORMANCE_TRACE_CACHE.clear()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000010_window.jpg"
    window.write_bytes(b"window")
    now_epoch = time.time()
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 10,
                "frame_index": 10,
                "display_frame_id": 10,
                "overlay_frame_id": 10,
                "model_vote_frame_id": 10,
                "display_published_epoch": now_epoch,
                "last_capture_epoch": now_epoch,
                "last_window_path": str(window),
                "tracking_summary": {},
                "latest_signal": {},
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())
    first_response = client.get("/v1/mobile/performance/trace/v3/pocket-live-8788")
    assert first_response.status_code == 200
    assert "direct_trace_cache_reused_v3" not in first_response.json()

    monkeypatch.setattr(mobile_app, "_direct_window_tracker_session_snapshot", lambda _session_id: None)
    second_response = client.get("/v1/mobile/performance/trace/v3/pocket-live-8788")

    assert second_response.status_code == 200
    payload = second_response.json()
    assert payload["frame_id"] == 10
    assert payload["direct_trace_cache_reused_v3"]["reason"] == "direct_snapshot_read_race"


def test_performance_trace_v3_treats_locked_display_overlay_as_authority_locked(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    mobile_app._LIVE_STATE_V3_CACHE.clear()
    mobile_app._LIVE_STATE_REGISTRY_CACHE.clear()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000300_window.jpg"
    overlay = artifact_dir / "000001_overlay.png"
    window.write_bytes(b"window")
    overlay.write_bytes(b"overlay")
    now_epoch = time.time()
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 300,
                "frame_index": 300,
                "display_frame_id": 300,
                "overlay_frame_id": 1,
                "model_vote_frame_id": 300,
                "display_published_epoch": now_epoch,
                "last_capture_epoch": now_epoch,
                "last_window_path": str(window),
                "last_overlay_path": str(overlay),
                "overlay_source_window_signature": "studied-surface",
                "tracking_summary": {},
                "latest_signal": {},
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "display_frame_id": 320,
                "display_capture_epoch": now_epoch,
                "display_published_epoch": now_epoch,
                "last_display_window_path": str(window),
                "last_display_surface_signature": "current-display",
                "last_window_surface_signature": "current-display",
                "display_snapshot_only_v3": True,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/performance/trace/v3/pocket-live-8788")

    assert response.status_code == 200
    payload = response.json()
    assert payload["timing_trace"]["raw_overlay_frame_gap"] == 319
    assert payload["timing_trace"]["overlay_frame_gap"] == 0
    assert payload["timing_trace"]["frame_gap_status"] == "AUTHORITY_LOCKED"
    assert payload["timing_trace"]["display_only_authority_locked"] is True


def test_model_council_latest_state_endpoint_returns_non_executable_study_packet() -> None:
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "pgpkt-study",
        "packet_type": "STUDY_PACKET",
        "session_id": "pocket-live-8788",
        "execution": {"enabled": False, "state": "WATCHING"},
        "model_council": {"final_state": "WATCHING", "final_side": "BUY"},
        "promotion_trace": {
            "promotion_result": "WAITING",
            "blocked_by": "candidate_flip_count",
        },
    }

    class _FakeTracker:
        def list_sessions(self, limit: int = 1) -> list[dict[str, Any]]:
            return [{"session_id": "pocket-live-8788", "model_council_study_packet": study_packet}]

        def get_session(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return {"session_id": session_id, "model_council_study_packet": study_packet}

        def latest_model_council_state(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return {
                "session_id": session_id,
                "model_council_result": {"study_packet": study_packet},
                "model_council_study_packet": study_packet,
                "model_council_packet": {},
                "execution_packet_present": False,
                "execution_packet_id": "",
                "promotion_trace": study_packet["promotion_trace"],
            }

    client = TestClient(create_app(window_tracker_service=_FakeTracker()))

    direct = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/latest")
    alias = client.get("/v1/mobile/model-council/latest?session_id=pocket-live-8788")

    assert direct.status_code == 200
    assert alias.status_code == 200
    assert direct.json()["execution_packet_present"] is False
    assert direct.json()["model_council_study_packet"]["packet_id"] == "pgpkt-study"
    assert alias.json()["promotion_trace"]["blocked_by"] == "candidate_flip_count"


def test_model_council_latest_study_packet_endpoints_return_visibility_packet() -> None:
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "pgpkt-study-visible",
        "packet_type": "STUDY_PACKET",
        "session_id": "pocket-live-8788",
        "execution": {"enabled": False, "state": "PREPARING", "side": "SELL"},
        "model_council": {"final_state": "PREPARING", "final_side": "SELL"},
        "promotion_trace": {
            "promotion_result": "PREPARING",
            "next_required": "timing READY with explicit expiry",
        },
    }

    class _FakeTracker:
        def list_sessions(self, limit: int = 1) -> list[dict[str, Any]]:
            return [{"session_id": "pocket-live-8788", "model_council_study_packet": study_packet}]

        def get_session(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return {"session_id": session_id, "model_council_study_packet": study_packet}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return study_packet

    client = TestClient(create_app(window_tracker_service=_FakeTracker()))

    direct = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/study/latest")
    alias = client.get("/v1/mobile/model-council/study/latest?session_id=pocket-live-8788")

    assert direct.status_code == 200
    assert alias.status_code == 200
    assert direct.json()["packet_id"] == "pgpkt-study-visible"
    assert direct.json()["packet_type"] == "STUDY_PACKET"
    assert alias.json()["promotion_trace"]["next_required"] == "timing READY with explicit expiry"


def test_shooter_handshake_endpoint_reads_runtime_file(monkeypatch: Any, tmp_path: Path) -> None:
    handshake_path = tmp_path / "shooter_handshake.json"
    handshake_path.write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "packet_seen": True,
                "packet_id": "study_endpoint",
                "packet_type": "STUDY_PACKET",
                "reason": "WAITING_FOR_EXECUTABLE_MODEL_COUNCIL_PACKET",
                "gate_1_second_read": "NOT_CHECKED",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", handshake_path)
    client = TestClient(create_app())

    direct = client.get("/v1/mobile/shooter/sessions/pocket-live-8788/handshake")
    alias = client.get("/v1/mobile/shooter/handshake?session_id=pocket-live-8788")

    assert direct.status_code == 200
    assert alias.status_code == 200
    assert direct.json()["packet_id"] == "study_endpoint"
    assert direct.json()["packet_type"] == "STUDY_PACKET"
    assert alias.json()["gate_1_second_read"] == "NOT_CHECKED"


def test_observability_reads_nested_execution_packet_from_model_council_result() -> None:
    packet = build_execution_packet_v3(
        packet_id="pgpkt-nested",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=20,
        capture_count=21,
        state_version=120,
        side="BUY",
        expiry_seconds=300,
        input_frame_hash="frame-nested",
        created_epoch=1000.0,
        valid_until_epoch=1002.0,
        model_council={
            "final_state": "EXECUTABLE",
            "final_side": "BUY",
            "dominance_margin": 0.66,
            "disagreement_score": 0.12,
            "flip_flop_state": "STABLE_EXECUTABLE",
            "arbitration_reason": "Nested packet compatibility check.",
        },
        market_context={
            "global_side": "BUY",
            "local_side": "BUY",
            "current_location": "MIDDLE_SAFE",
            "opposing_force_distance_ok": True,
        },
        angle_context={"angle_class": "STRONG_BUT_SUSTAINABLE"},
        history_context={"similarity_state": "REPEATING_SUCCESSFUL_PATH"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
    )
    session = {"session_id": "pocket-live-8788", "latest_signal": {"execution_packet": packet}}

    health = build_model_council_health_from_session(session, now_epoch=1000.5)
    intelligence = build_intelligence_health(session)

    assert health["all_required_models_awake"] is True
    assert intelligence["council_final_state"] == "EXECUTABLE"
    assert intelligence["council_final_side"] == "BUY"
    assert intelligence["global_agent"] == "BUY"


def test_observability_reads_v3_study_result_without_execution_packet() -> None:
    session = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "model_council_result": {
                "execution": {"enabled": False, "state": "BLOCKED_BY_RUNTIME"},
                "model_council": {
                    "final_state": "BLOCKED_BY_RUNTIME",
                    "final_side": "BUY",
                    "dominance_margin": 0.67,
                    "disagreement_score": 0.33,
                    "flip_flop_state": "STUDYING",
                    "arbitration_reason": "Market identity is missing.",
                },
                "market_context": {"global_side": "BUY", "local_side": "BUY"},
                "runtime_model_health": {"all_required_models_awake": True, "council_status": "AWAKE"},
            }
        },
    }

    health = build_model_council_health_from_session(session, now_epoch=1000.5)
    intelligence = build_intelligence_health(session)

    assert health["all_required_models_awake"] is True
    assert intelligence["all_models_awake"] is True
    assert intelligence["council_final_state"] == "BLOCKED_BY_RUNTIME"
    assert intelligence["council_final_side"] == "BUY"
