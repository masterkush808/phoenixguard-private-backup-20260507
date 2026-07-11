from __future__ import annotations

import json
import math
from pathlib import Path
import time
from typing import Any, Callable, Mapping, MutableMapping, cast

from fastapi.testclient import TestClient
import pytest

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


def _compact_live_state_response_cache_signature(session_id: str) -> str:
    fn = cast(
        Callable[[str], str],
        getattr(mobile_app, "_compact_live_state_response_cache_signature"),
    )
    return fn(session_id)


def _live_state_cache_signature(session_id: str, *, compact_public: bool = False) -> str:
    fn = cast(
        Callable[..., str],
        getattr(mobile_app, "_live_state_cache_signature"),
    )
    return fn(session_id, compact_public=compact_public)


def _clear_mobile_live_state_caches(*, direct_trace: bool = False) -> None:
    cast(MutableMapping[Any, Any], getattr(mobile_app, "_LIVE_STATE_V3_CACHE")).clear()
    cast(MutableMapping[Any, Any], getattr(mobile_app, "_LIVE_STATE_REGISTRY_CACHE")).clear()
    cast(MutableMapping[Any, Any], getattr(mobile_app, "_COMPACT_LIVE_STATE_RESPONSE_CACHE")).clear()
    if direct_trace:
        cast(MutableMapping[Any, Any], getattr(mobile_app, "_DIRECT_PERFORMANCE_TRACE_CACHE")).clear()


def test_compact_live_state_cache_rejects_frame_behind_latest_complete_session() -> None:
    can_reuse = cast(Callable[..., bool], getattr(mobile_app, "_compact_live_state_cache_can_reuse"))
    cached_payload = {
        "frame_id": 2,
        "display_frame_id": 2,
        "frame_index": 2,
        "chart_frame_id": 2,
        "overlay_frame_id": 2,
        "full_overlay_frame_id": 2,
        "model_vote_frame_id": 2,
        "overlays": {"objects": [{"frame_id": 2, "id": "old-overlay"}]},
    }
    current_payload = {
        **cached_payload,
        "frame_id": 3,
        "display_frame_id": 3,
        "frame_index": 3,
        "chart_frame_id": 3,
        "overlay_frame_id": 3,
        "full_overlay_frame_id": 3,
        "model_vote_frame_id": 3,
        "overlays": {"objects": [{"frame_id": 3, "id": "current-overlay"}]},
    }

    assert can_reuse(cached_payload, 1.0, latest_complete_frame_id=3) is False
    assert can_reuse(current_payload, 1.0, latest_complete_frame_id=3) is True


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


def _fresh_endpoint_execution_packet(*, packet_id: str, frame_id: int, capture_count: int, side: str = "BUY") -> dict[str, Any]:
    now = time.time()
    input_frame_hash = f"frame-{frame_id}"
    packet = build_execution_packet_v3(
        packet_id=packet_id,
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=frame_id,
        capture_count=capture_count,
        state_version=100 + frame_id,
        side=side,
        expiry_seconds=300,
        input_frame_hash=input_frame_hash,
        created_epoch=now,
        valid_until_epoch=now + 120.0,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": input_frame_hash,
            "previous_frame_hash": f"frame-{max(0, frame_id - 1)}",
            "packet_age_ms": 10,
        },
        model_council={"final_state": "EXECUTABLE", "final_side": side},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        sequence_context={
            "sequence_id": f"seq-{frame_id}",
            "session_id": "pocket-live-8788",
            "timeframe": "M5",
            "sequence_signature": f"seqsig-{frame_id}",
            "sequence_status": "COMPLETE",
            "sequence_length": 50,
            "frames_used": 50,
            "sequence_confidence": 0.95,
            "box_history": [{"type": "IMPULSE_BOX", "bounds": [0.1, 0.2, 0.3, 0.4]}],
            "progression": [{"type": "IMPULSE_BOX", "index": 1}],
            "entry_progression": {"steps": [{"type": "TRIGGER", "index": 1}]},
        },
        allowance_package={
            "schema_version": "PG_ALLOWANCE_PACKAGE_V1",
            "package_type": "INTRADAY_ENTER_NOW",
            "allowance_family": "INTRADAY",
            "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3",
            "packet_authority": "PG_EXECUTION_PACKET_V3",
            "side": side,
            "accepted": True,
            "decision_accepted": True,
            "execution_ready": True,
            "entry_now_allowed": True,
            "timing_mode": "ENTER_NOW",
            "selected_lane": "SNIPER_ZONE_ENTRY",
            "score": 0.84,
            "threshold": 0.70,
        },
    )
    packet["trade_permission"] = {
        "permission_state": "GRANTED",
        "executable_allowed": True,
        "failed_reasons": [],
        "blocking_reasons": [],
    }
    packet["entry_quality"] = {"state": "ACCEPTABLE_ENTRY", "passes_executable_threshold": True}
    packet["market_trap"] = {"detected": False, "executable_allowed": True, "active_traps": []}
    packet["overlay_truth_audit"] = {
        "valid_for_execution": True,
        "execution_safe": True,
        "frame_id": frame_id,
        "capture_count": capture_count,
        "input_frame_hash": input_frame_hash,
        "objects": [],
    }
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


def test_live_state_compact_cache_signatures_track_display_artifacts(tmp_path: Path, monkeypatch: Any) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "tracking_enabled": True,
                "window_query": "Pocket Option",
                "locked_title": "Pocket Option",
            }
        ),
        encoding="utf-8",
    )
    display_state: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "frame_index": 10,
        "display_frame_id": 100,
        "chart_frame_id": 10,
        "overlay_frame_id": 10,
        "full_overlay_frame_id": 10,
        "model_vote_frame_id": 10,
        "last_display_window_path": "0010_window.jpg",
        "last_chart_path": "hot_latest_chart.jpg",
        "last_overlay_path": "hot_latest_overlay.jpg",
        "last_full_overlay_path": "hot_latest_full_overlay.jpg",
    }
    (session_dir / "display_state.json").write_text(json.dumps(display_state), encoding="utf-8")

    compact_sig_1 = _compact_live_state_response_cache_signature("pocket-live-8788")
    live_sig_1 = _live_state_cache_signature("pocket-live-8788", compact_public=True)

    display_state["frame_index"] = 11
    display_state["display_frame_id"] = 101
    display_state["last_display_window_path"] = "0011_window.jpg"
    (session_dir / "display_state.json").write_text(json.dumps(display_state), encoding="utf-8")

    compact_sig_2 = _compact_live_state_response_cache_signature("pocket-live-8788")
    live_sig_2 = _live_state_cache_signature("pocket-live-8788", compact_public=True)

    assert compact_sig_2 != compact_sig_1
    assert live_sig_2 != live_sig_1


def test_study_packet_expires_by_ttl() -> None:
    study_packet: dict[str, Any] = {
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
    assert health["synthetic"] is False
    assert health["health_kind"] == "measured_models"
    assert model_health_allows_executable(health) is True


def test_forensic_log_written_every_cycle(tmp_path: Path) -> None:
    log_path = tmp_path / "forensic" / "decision_cycles.jsonl"
    decision: dict[str, Any] = {"packet_id": "pgpkt-test", "will_click": False, "reason": "WAITING_SECOND_LIVE_READ"}

    row = append_forensic_decision_log(log_path, decision, packet=_execution_packet(), now_epoch=1000.0)

    assert row["cache_schema_version"] == CACHE_SCHEMA_VERSION
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["decision"]["reason"] == "WAITING_SECOND_LIVE_READ"


def test_bad_entry_replay_blocks_execution() -> None:
    replay_record: dict[str, Any] = {
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
            self.session: dict[str, Any] = {
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
    assert payload["synthetic"] is True
    assert payload["health_kind"] == "logical_role_readiness"
    assert {row["unit_kind"] for row in payload["models"]} == {"logical_role"}
    assert payload["queue_depth"] == 2
    assert payload["runtime_telemetry"]["packet"]["age_sec"] == 0.5
    assert payload["runtime_telemetry"]["cache"]["rejects"] == 1
    assert payload["runtime_telemetry"]["paper"]["wins"] == 1
    assert payload["runtime_telemetry"]["path_quality"]["label"] == "MEDIUM"


def test_model_council_health_prefers_compact_sidecar_and_caches_bounded_daemon_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    common = {
        "session_id": "pocket-live-8788",
        "status": "running",
        "tracking_enabled": True,
        "last_capture_epoch": time.time(),
    }
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                **common,
                "latest_signal": {"runtime_model_health": {"queue_depth": 99}},
                "unrelated_large_blob": "x" * 1_000_000,
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "compact_live_state.json").write_text(
        json.dumps(
            {
                **common,
                "latest_signal": {
                    "runtime_model_health": {
                        "all_required_models_awake": True,
                        "max_model_latency_ms": 12.0,
                        "queue_depth": 3,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    probe_timeouts: list[float] = []

    def missing_daemon(_request: object, *, timeout: float) -> object:
        probe_timeouts.append(timeout)
        raise mobile_app.urllib.error.URLError("daemon unavailable")

    monkeypatch.setattr(mobile_app.urllib.request, "urlopen", missing_daemon)
    client = TestClient(create_app())

    first = client.get("/v1/mobile/model-council/health?session_id=pocket-live-8788")
    second = client.get("/v1/mobile/model-council/health?session_id=pocket-live-8788")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["queue_depth"] == 3
    assert second.json()["queue_depth"] == 3
    assert len(probe_timeouts) == 1
    assert math.isclose(probe_timeouts[0], 0.1, rel_tol=1e-9, abs_tol=1e-9)


def test_direct_window_tracker_stream_uses_compact_signature_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "session.json"
    compact_path = session_dir / "compact_live_state.json"
    session_path.write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "tracking_enabled": True,
                "capture_count": 1,
                "unrelated_large_blob": "x" * 1_000_000,
            }
        ),
        encoding="utf-8",
    )
    compact_path.write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "tracking_enabled": True,
                "capture_count": 1,
            }
        ),
        encoding="utf-8",
    )
    probe = cast(
        Callable[..., tuple[str, tuple[str, int, int] | None, dict[str, object] | None]],
        getattr(mobile_app, "_direct_window_tracker_stream_snapshot"),
    )

    state, signature, payload = probe("pocket-live-8788", None)

    assert state == "updated"
    assert signature is not None
    assert payload is not None
    assert payload["capture_count"] == 1
    assert "unrelated_large_blob" not in payload

    session_path.write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "tracking_enabled": True,
                "capture_count": 2,
                "unrelated_large_blob": "y" * 1_100_000,
            }
        ),
        encoding="utf-8",
    )
    state, unchanged_signature, unchanged_payload = probe("pocket-live-8788", signature)
    assert state == "unchanged"
    assert unchanged_signature == signature
    assert unchanged_payload is None

    compact_path.write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "tracking_enabled": True,
                "capture_count": 2,
                "signature_change_marker": True,
            }
        ),
        encoding="utf-8",
    )
    state, changed_signature, changed_payload = probe("pocket-live-8788", signature)
    assert state == "updated"
    assert changed_signature != signature
    assert changed_payload is not None
    assert changed_payload["capture_count"] == 2


def test_model_council_latest_execution_packet_endpoints_return_v3_packet() -> None:
    packet = _fresh_endpoint_execution_packet(
        packet_id="pgpkt-endpoint",
        frame_id=20,
        capture_count=21,
        side="BUY",
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


def test_study_latest_falls_back_to_full_session_when_compact_study_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = time.time()
    execution_packet = _fresh_endpoint_execution_packet(
        packet_id="pgpkt-current-execution",
        frame_id=90,
        capture_count=91,
        side="SELL",
    )
    dual_thesis_report = {
        "buy": {"status": "CURRENT_PRESSURE_DEFENDED"},
        "sell": {"status": "CURRENT_PRESSURE_ACTIVE"},
    }
    compact_payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "tracking_enabled": True,
        "frame_index": 90,
        "model_vote_frame_id": 90,
        "capture_count": 91,
        "last_capture_epoch": now_epoch - 400.0,
        "execution_packet_present": True,
        "model_council_packet": execution_packet,
        "model_council_result": {
            "execution": {"state": "EXECUTABLE", "side": "SELL"},
            "model_council": {
                "final_state": "EXECUTABLE",
                "final_side": "SELL",
                "dual_thesis_report_v3": dual_thesis_report,
            },
            "dual_thesis_report_v3": dual_thesis_report,
        },
    }
    full_payload: dict[str, Any] = {
        **compact_payload,
        "model_council_result": {
            "packet_id": "pgpkt-current-study-source",
            "session_id": "pocket-live-8788",
            "created_epoch": now_epoch,
            "valid_until_epoch": now_epoch + 120.0,
            "execution": {"state": "PREPARING", "side": "SELL"},
            "model_council": {
                "final_state": "PREPARING",
                "final_side": "SELL",
                "dual_thesis_report_v3": dual_thesis_report,
            },
            "dual_thesis_report_v3": dual_thesis_report,
        },
    }
    full_snapshot_calls: list[bool] = []

    def full_snapshot(
        session_id: str,
        *,
        require_complete_display_bundle: bool = True,
    ) -> dict[str, Any]:
        assert session_id == "pocket-live-8788"
        full_snapshot_calls.append(require_complete_display_bundle)
        return dict(full_payload)

    def compact_snapshot(_session_id: str) -> dict[str, Any]:
        return dict(compact_payload)

    monkeypatch.setattr(mobile_app, "_direct_model_council_fast_payload", compact_snapshot)
    monkeypatch.setattr(mobile_app, "_direct_window_tracker_session_snapshot", full_snapshot)
    client = TestClient(create_app())

    study_response = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/study/latest")
    execution_response = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest")

    assert study_response.status_code == 200
    assert execution_response.status_code == 200
    study = study_response.json()
    assert study["packet_id"] == "pgpkt-current-study-source"
    assert study["packet_type"] == "STUDY_PACKET"
    assert study["execution"]["enabled"] is False
    assert study["execution"]["state"] == "PREPARING"
    assert study["entry_permission_v3"]["state"] == "AUTHORIZED_NOW"
    assert study["entry_permission_v3"]["side"] == "SELL"
    assert study["entry_permission_v3"]["execution_packet_id"] == "pgpkt-current-execution"
    assert study["dual_thesis_report_v3"]["buy"] == dual_thesis_report["buy"]
    assert study["dual_thesis_report_v3"]["sell"] == dual_thesis_report["sell"]
    assert execution_response.json()["packet_id"] == "pgpkt-current-execution"
    assert full_snapshot_calls == [False]


def test_study_latest_compact_synthesis_uses_canonical_visibility_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = time.time()
    execution_packet = _fresh_endpoint_execution_packet(
        packet_id="pgpkt-current-execution-compact-study",
        frame_id=92,
        capture_count=93,
        side="BUY",
    )
    dual_thesis_report = {
        "buy": {"side": "BUY", "status": "AUTHORITY_ACTIVE"},
        "sell": {"side": "SELL", "status": "STUDYING"},
    }
    compact_payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "tracking_enabled": True,
        "frame_index": 92,
        "model_vote_frame_id": 92,
        "capture_count": 93,
        "last_capture_epoch": now_epoch - 30.0,
        "execution_packet_present": True,
        "model_council_packet": execution_packet,
        "model_council_result": {
            "execution": {"state": "EXECUTABLE", "side": "BUY"},
            "model_council": {
                "final_state": "EXECUTABLE",
                "final_side": "BUY",
                "dual_thesis_report_v3": dual_thesis_report,
            },
            "dual_thesis_report_v3": dual_thesis_report,
        },
    }

    def unexpected_full_snapshot(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("current compact study synthesis must not require the full-session fallback")

    def compact_snapshot(_session_id: str) -> dict[str, Any]:
        return dict(compact_payload)

    monkeypatch.setattr(mobile_app, "_direct_model_council_fast_payload", compact_snapshot)
    monkeypatch.setattr(mobile_app, "_direct_window_tracker_session_snapshot", unexpected_full_snapshot)
    client = TestClient(create_app())

    response = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/study/latest")

    assert response.status_code == 200
    study = response.json()
    assert study["packet_type"] == "STUDY_PACKET"
    assert study["execution"]["enabled"] is False
    assert study["execution"]["state"] == "WATCHING"
    assert study["entry_permission_v3"]["state"] == "AUTHORIZED_NOW"
    assert study["entry_permission_v3"]["side"] == "BUY"
    assert study["entry_permission_v3"]["execution_packet_id"] == "pgpkt-current-execution-compact-study"
    assert study["dual_thesis_report_v3"]["buy"] == dual_thesis_report["buy"]
    assert study["dual_thesis_report_v3"]["sell"] == dual_thesis_report["sell"]
    assert math.isclose(float(study["created_epoch"]), now_epoch - 30.0, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(
        float(study["valid_until_epoch"]) - float(study["created_epoch"]),
        300.0,
        rel_tol=1e-9,
        abs_tol=1e-9,
    )


def test_execution_latest_rejects_packet_from_previous_completed_frame(monkeypatch: Any) -> None:
    old_packet = _fresh_endpoint_execution_packet(
        packet_id="pgpkt-frame-31",
        frame_id=31,
        capture_count=31,
        side="BUY",
    )
    current_payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "tracking_enabled": True,
        "frame_index": 90,
        "model_vote_frame_id": 90,
        "capture_count": 90,
        "execution_packet_present": True,
        "model_council_packet": old_packet,
    }

    def current_fast_payload(_session_id: str) -> dict[str, Any]:
        return dict(current_payload)

    def current_full_payload(_session_id: str, **_kwargs: Any) -> dict[str, Any]:
        return dict(current_payload)

    monkeypatch.setattr(mobile_app, "_direct_model_council_fast_payload", current_fast_payload)
    monkeypatch.setattr(
        mobile_app,
        "_direct_window_tracker_session_snapshot",
        current_full_payload,
    )
    client = TestClient(create_app())

    direct = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest")
    alias = client.get("/v1/mobile/model-council/execution/latest?session_id=pocket-live-8788")

    assert direct.status_code == 404
    assert alias.status_code == 404


def test_execution_latest_honors_current_packet_revocation(monkeypatch: Any) -> None:
    revoked_packet = _fresh_endpoint_execution_packet(
        packet_id="pgpkt-revoked-frame-90",
        frame_id=90,
        capture_count=90,
        side="SELL",
    )
    revoked_payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "tracking_enabled": True,
        "frame_index": 90,
        "model_vote_frame_id": 90,
        "capture_count": 90,
        "execution_packet_present": False,
        "model_council_packet": revoked_packet,
    }

    def revoked_fast_payload(_session_id: str) -> dict[str, Any]:
        return dict(revoked_payload)

    def revoked_full_payload(_session_id: str, **_kwargs: Any) -> dict[str, Any]:
        return dict(revoked_payload)

    monkeypatch.setattr(mobile_app, "_direct_model_council_fast_payload", revoked_fast_payload)
    monkeypatch.setattr(
        mobile_app,
        "_direct_window_tracker_session_snapshot",
        revoked_full_payload,
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest")

    assert response.status_code == 404


def test_execution_latest_service_fallback_cannot_resurrect_previous_frame() -> None:
    old_packet = _fresh_endpoint_execution_packet(
        packet_id="pgpkt-service-frame-31",
        frame_id=31,
        capture_count=31,
    )
    current_payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "frame_index": 90,
        "model_vote_frame_id": 90,
        "capture_count": 90,
    }

    class _FakeTracker:
        def list_sessions(self, limit: int = 1) -> list[dict[str, Any]]:
            return [dict(current_payload)]

        def get_session(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return dict(current_payload)

        def latest_model_council_packet(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return old_packet

    client = TestClient(create_app(window_tracker_service=_FakeTracker()))

    direct = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest")
    alias = client.get("/v1/mobile/model-council/execution/latest?session_id=pocket-live-8788")

    assert direct.status_code == 404
    assert alias.status_code == 404


def test_dashboard_asset_route_rejects_encoded_path_traversal() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/mobile/window-tracker/assets/js/%2e%2e/%2e%2e/.hintrc")

    assert response.status_code == 404


def test_dashboard_asset_route_serves_floating_window_stylesheet() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/mobile/window-tracker/assets/floating-windows/overlay_editor.css")
    traversal = client.get("/v1/mobile/window-tracker/assets/floating-windows/%2e%2e/window_tracker_dashboard.html")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ".overlay-editor" in response.text
    assert traversal.status_code == 404


def test_overlay_editor_settings_hard_save_and_dashboard_embed(monkeypatch: Any, tmp_path: Path) -> None:
    settings_path = tmp_path / "floating_windows" / "overlay_editor_settings.json"
    monkeypatch.setattr(mobile_app, "_WINDOW_TRACKER_FLOATING_WINDOWS_DIR", settings_path.parent)
    monkeypatch.setattr(mobile_app, "_WINDOW_TRACKER_OVERLAY_EDITOR_SETTINGS_PATH", settings_path)
    client = TestClient(create_app())

    save = client.post(
        "/v1/mobile/window-tracker/floating-windows/overlay-editor/settings",
        json={
            "schemaVersion": 2,
            "opacityScale": 0.72,
            "lineScale": 1.34,
            "labelMaxWidth": 126,
            "layers": {"trendlines": False},
            "colors": {"demand": "#123abc", "supply": "#not-real"},
        },
    )
    read_back = client.get("/v1/mobile/window-tracker/floating-windows/overlay-editor/settings")
    dashboard = client.get("/dashboard/live/pocket-live-8788")

    assert save.status_code == 200
    assert read_back.status_code == 200
    assert dashboard.status_code == 200
    assert settings_path.is_file()
    settings = read_back.json()
    assert settings["profileSaved"] is True
    assert settings["opacityScale"] == 0.72
    assert settings["lineScale"] == 1.34
    assert settings["labelMaxWidth"] == 126
    assert settings["layers"] == {}
    assert settings["colors"]["demand"] == "#123abc"
    assert settings["colors"]["supply"] == "#f8ca5c"
    assert '"opacityScale": 0.72' in dashboard.text
    assert '"demand": "#123abc"' in dashboard.text
    assert "id=\"overlay-editor-open\" type=\"button\" hidden" in dashboard.text


def test_live_state_v3_direct_read_waits_for_missing_shooter_handshake(monkeypatch: Any, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
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
    assert payload["shooter_state"]["next_required"] == "fresh accepted intraday or swing allowance package"

    compact_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?compact=1")

    assert compact_response.status_code == 200
    compact = compact_response.json()
    assert compact["shooter"]["available"] is False
    assert isinstance(compact["overlays"]["objects"], list)
    assert "all_objects" not in compact["overlays"]
    assert "live_visual_state" not in compact
    assert "overlay_objects" not in compact
    assert "market_object_registry" not in compact


def test_compact_live_state_projects_study_only_decision_command_center(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    now_epoch = time.time()
    study_packet: dict[str, Any] = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_type": "STUDY_PACKET",
        "packet_id": "pgpkt-study-command-center",
        "session_id": "pocket-live-8788",
        "created_epoch": now_epoch - 2.0,
        "valid_until_epoch": now_epoch + 60.0,
        "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
        "dual_thesis_report_v3": {
            "selected_authority_side": "SELL",
            "primary_bias_side": "BUY",
            "current_pressure_side": "SELL",
            "current_pressure": {"side": "SELL", "candle_count": 4, "stage": "CONTINUATION"},
            "buy": {"score": 0.6036, "status": "STUDYING", "role": "PRIMARY_BIAS_WAITING"},
            "sell": {"score": 0.8183, "status": "AUTHORITY_ACTIVE", "role": "SELECTED_AUTHORITY"},
            "full_suite_story_lock_v3": {
                "state": "FULL_SUITE_STORY_CONFIRMED",
                "confirmed": True,
                "effective_side": "SELL",
            },
        },
        "playbook_ai_summary_v3": {
            "full_suite_ready": True,
            "thesis_arbitration": {
                "scores": {"BUY": {"score": 0.6036}, "SELL": {"score": 0.8183}},
            },
            "full_suite_story_lock_v3": {
                "state": "FULL_SUITE_STORY_CONFIRMED",
                "confirmed": True,
                "effective_side": "SELL",
            },
            "horizon": {
                "selected_side": "SELL",
                "optimized_candle_count": 18,
                "optimized_duration_sec": 5400,
                "optimized_duration_text": "1h 30m",
                "horizon_class": "EXTENDED_THESIS_13_PLUS_CANDLES",
                "basis": "professional_trade_plan_thesis_horizon",
                "target_before_invalidation_probability": 0.84,
                "by_side": {"SELL": {"execution_authorized": True}},
            },
        },
        "book_strategy_playbook": "SELL_IN_BUY_OPPOSING_FORCE_REACTION",
        "execution_opportunity_window_v3": {
            "state": "OPEN",
            "side": "SELL",
            "duration_sec": 90.0,
            "remaining_sec": 90.0,
            "opened_epoch": now_epoch - 10.0,
            "opened_epoch_sec": now_epoch - 10.0,
            "valid_until_epoch": now_epoch + 80.0,
            "valid_until_epoch_sec": now_epoch + 80.0,
            "integrity_valid": True,
            "lineage_rejected": False,
            "anchor_reused": True,
            "out_of_order_ignored": False,
            "opportunity_id": "must-not-leak",
            "execution_authorized": True,
            "entry_permission_v3": {"allowed": True},
        },
        "promotion_trace": {
            "denied_at": "EXECUTION_OPPORTUNITY_WINDOW_EXPIRED",
            "next_required": "wait for a fresh entry window",
        },
        "model_council": {"final_state": "WATCHING", "final_side": "SELL"},
    }
    expired_execution_packet = {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": "pgpkt-expired-command-center",
        "session_id": "pocket-live-8788",
        "created_epoch": now_epoch - 120.0,
        "valid_until_epoch": now_epoch - 60.0,
        "execution": {"enabled": True, "state": "EXECUTABLE", "side": "SELL", "expiry_seconds": 60},
        "model_council": {"final_state": "EXECUTABLE", "final_side": "SELL"},
    }
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 21,
                "frame_index": 21,
                "last_capture_epoch": now_epoch,
                "tracking_summary": {},
                "latest_signal": {},
                "model_council_study_packet": study_packet,
                "model_council_result": {"study_packet": study_packet, "promotion_trace": study_packet["promotion_trace"]},
                "model_council_packet": expired_execution_packet,
                "execution_packet": expired_execution_packet,
                "execution_packet_present": False,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?compact=1")

    assert response.status_code == 200
    payload = response.json()
    command = payload["decision_command_center"]
    assert command["schema_version"] == "PG_DECISION_COMMAND_CENTER_V3"
    assert command["study_packet_id"] == "pgpkt-study-command-center"
    assert command["selected_side"] == "SELL"
    assert command["current_pressure_side"] == "SELL"
    assert command["primary_bias_side"] == "BUY"
    assert math.isclose(float(command["sides"]["BUY"]["score"]), 0.6036, rel_tol=1e-6, abs_tol=1e-12)
    assert math.isclose(float(command["sides"]["SELL"]["score"]), 0.8183, rel_tol=1e-6, abs_tol=1e-12)
    assert command["story"]["state"] == "FULL_SUITE_STORY_CONFIRMED"
    assert command["blocker"] == "EXECUTION_OPPORTUNITY_WINDOW_EXPIRED"
    assert command["next_required"] == "wait for a fresh entry window"
    assert command["book_strategy_playbook"] == "SELL_IN_BUY_OPPOSING_FORCE_REACTION"
    assert command["horizon"] == {
        "selected_side": "SELL",
        "optimized_candle_count": 18,
        "optimized_duration_sec": 5400,
        "optimized_duration_text": "1h 30m",
        "horizon_class": "EXTENDED_THESIS_13_PLUS_CANDLES",
        "basis": "professional_trade_plan_thesis_horizon",
        "target_before_invalidation_probability": 0.84,
    }
    assert set(command["execution_opportunity_window_v3"]) == {
        "state",
        "side",
        "duration_sec",
        "remaining_sec",
        "opened_epoch",
        "opened_epoch_sec",
        "valid_until_epoch",
        "valid_until_epoch_sec",
        "integrity_valid",
        "lineage_rejected",
        "anchor_reused",
        "out_of_order_ignored",
    }
    assert command["execution_opportunity_window_v3"]["state"] == "OPEN"
    assert command["execution_opportunity_window_v3"]["side"] == "SELL"
    assert command["execution_opportunity_window_v3"]["integrity_valid"] is True
    assert "execution_authorized" not in command["execution_opportunity_window_v3"]
    assert "entry_permission_v3" not in command["execution_opportunity_window_v3"]
    assert "by_side" not in command["horizon"]
    assert command["fresh"] is True
    assert command["execution_packet_present"] is False
    assert command["contains_execution_authority"] is False
    assert "execution" not in command
    assert "model_council_packet" not in payload
    assert "execution_packet" not in payload
    assert "model_council_study_packet" not in payload

    refresh_freshness = cast(
        Callable[..., dict[str, object]],
        getattr(mobile_app, "_refresh_decision_command_center_freshness_v3"),
    )
    cached_command = refresh_freshness(command, now_epoch=now_epoch + 61.0)
    assert cached_command["study_packet_id"] == "pgpkt-study-command-center"
    assert cached_command["age_ms"] > command["age_ms"]
    assert cached_command["fresh"] is False
    assert cached_command["freshness_status"] == "STALE"

    build_summary = cast(
        Callable[..., dict[str, object]],
        getattr(mobile_app, "_decision_command_center_summary_v3"),
    )
    arbitration_fallback = build_summary(
        {
            "model_council_result": {
                "final_side": "BUY",
                "playbook_ai_summary_v3": {
                    "thesis_arbitration": {
                        "winner": "SELL",
                        "candidate_side": "SELL",
                    }
                },
            }
        },
        now_epoch=now_epoch,
    )
    result_fallback = build_summary(
        {"model_council_result": {"final_side": "BUY"}},
        now_epoch=now_epoch,
    )
    assert arbitration_fallback["selected_side"] == "SELL"
    assert result_fallback["selected_side"] == "BUY"


def test_live_state_v3_direct_read_skips_legacy_registry_when_v3_sources_exist(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
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
                            "track_id": f"candle-{index}",
                            "bbox": [10 + index * 8, 20, 18 + index * 8, 80],
                            "direction": "up",
                            "confidence": 0.9,
                        }
                        for index in range(16)
                    ],
                    "structure_boxes": [
                        {
                            "key": "global-impulse",
                            "bbox": [10, 20, 150, 90],
                            "direction": "BUY",
                            "confidence": 0.86,
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


def test_live_state_v3_thin_direct_sources_load_locked_registry_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    now_epoch = time.time()
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 9,
                "frame_index": 9,
                "last_capture_epoch": now_epoch,
                "broker_surface": {
                    "capture_plane": {"width": 400, "height": 240},
                },
                "tracking_summary": {
                    "chart_region": {
                        "pixel_bbox": [0, 0, 400, 240],
                        "width": 400,
                        "height": 240,
                        "confidence": 0.92,
                    },
                    "tracked_candles": [
                        {
                            "track_id": "thin-candle",
                            "bbox": [100, 120, 112, 180],
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
    registry_entry: dict[str, Any] = {
        "overlay_id": "support-1",
        "object_id": "support-1",
        "track_id": "support-1",
        "truth_score": 0.91,
        "lifecycle_state": "CONFIRMED",
        "chart_transform": {
            "chart_image_bounds": [0, 0, 400, 240],
        },
            "overlay": {
                "overlay_id": "support-1",
                "object_id": "support-1",
                "track_id": "support-1",
                "type": "SUPPORT",
                "layer": "supply_demand",
                "bbox": [40, 160, 220, 190],
                "anchor_candles": [0],
                "touch_points": [[106, 170]],
                "truth_score": 0.91,
                "confidence": 0.91,
                "visible_modes": ["REPLAY", "ACTIVE_CONTEXT"],
            },
    }

    def load_registry_context(*_args: Any, **_kwargs: Any) -> list[Mapping[str, Any]]:
        return [registry_entry]

    monkeypatch.setattr(mobile_app, "load_recent_market_objects", load_registry_context)
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=FULL_HISTORY_READ&compact=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_status"]["direct_registry_source"] == "registry_context_for_thin_direct_sources"
    assert payload["provider_status"]["direct_registry_entries"] == 1
    overlay_types = {str(row["type"]) for row in payload["overlays"]["objects"]}
    assert "DEMAND_ZONE" in overlay_types


def test_live_state_v3_compact_preserves_overlay_snap_scene_graph(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    now_epoch = time.time()
    chart_region = [72, 84, 1134, 688]
    broker_surface = {
        "capture_plane": {"width": 1280, "height": 720},
        "execution_boxes": {
            "buy_button": {"bbox": [1168, 392, 1234, 430], "confidence": 0.96},
            "sell_button": {"bbox": [1168, 438, 1234, 476], "confidence": 0.96},
        },
    }
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "status": "running",
                "tracking_enabled": True,
                "capture_count": 123,
                "frame_index": 123,
                "display_frame_id": 123,
                "last_capture_epoch": now_epoch,
                "display_capture_epoch": now_epoch,
                "display_published_epoch": now_epoch,
                "last_display_window_path": str(session_dir / "artifacts" / "000123_window.png"),
                "window_query": "Pocket Option",
                "locked_title": "The Most Innovative Trading Platform",
                "broker_surface": broker_surface,
                "tracking_summary": {
                    "chart_region": {
                        "pixel_bbox": chart_region,
                        "width": chart_region[2] - chart_region[0],
                        "height": chart_region[3] - chart_region[1],
                        "confidence": 0.95,
                        "source": "locked_broker_surface",
                    },
                    "detected_market": "CAD/JPY OTC",
                    "detected_timeframe": "M5",
                    "local_direction": "BUY",
                    "tracked_candles": [
                        {"track_id": "candle-122", "bbox": [880, 340, 895, 456], "direction": "SELL", "confidence": 0.88},
                        {"track_id": "candle-123", "bbox": [902, 316, 918, 440], "direction": "BUY", "confidence": 0.91},
                    ],
                },
                "latest_signal": {"side": "BUY", "action": "BUY", "timeframe": "M5", "symbol": "CAD/JPY OTC"},
                "signal_thesis_v3": {
                    "active": True,
                    "thesis_id": "snap-test",
                    "side": "BUY",
                    "effective_side": "BUY",
                    "confidence": 0.83,
                    "entry_zone": {"bbox": [884, 320, 924, 456]},
                    "target_zone": {"bbox": [938, 248, 1002, 304]},
                    "invalidation_zone": {"bbox": [846, 468, 928, 506]},
                    "plain_language": "Regression fixture for candle-plane overlay snapping.",
                },
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "capture_count": 123,
                "frame_index": 123,
                "display_frame_id": 123,
                "display_capture_epoch": now_epoch,
                "display_published_epoch": now_epoch,
                "last_display_window_path": str(session_dir / "artifacts" / "000123_window.png"),
                "last_display_surface_signature": "snap-surface-123",
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")

    assert response.status_code == 200
    raw_payload: object = response.json()
    assert isinstance(raw_payload, dict)
    payload = cast(dict[str, object], raw_payload)

    def numeric_bounds(value: object) -> list[float]:
        assert isinstance(value, list)
        bounds: list[float] = []
        for item in cast(list[object], value):
            assert isinstance(item, (int, float, str))
            bounds.append(float(item))
        return bounds

    raw_scene_graph = payload.get("scene_graph")
    assert isinstance(raw_scene_graph, dict)
    scene_graph = cast(dict[str, object], raw_scene_graph)
    chart_region_bounds = numeric_bounds(scene_graph["chart_region_bounds"])
    plot_area_bounds = numeric_bounds(scene_graph["plot_area_bounds"])
    assert [round(value, 2) for value in chart_region_bounds] == [72.0, 84.0, 1134.0, 688.0]
    assert plot_area_bounds[0] > 72.0
    assert plot_area_bounds[1] > 84.0
    assert plot_area_bounds[2] < 1134.0
    assert plot_area_bounds[3] < 688.0
    raw_chart = payload.get("chart")
    assert isinstance(raw_chart, dict)
    chart = cast(dict[str, object], raw_chart)
    assert chart["scene_graph"] == scene_graph
    raw_overlays = payload.get("overlays")
    assert isinstance(raw_overlays, dict)
    overlays = cast(dict[str, object], raw_overlays)
    assert "all_objects" not in overlays
    assert payload.get("live_visual_state") is None
    provider = payload.get("provider_status")
    assert isinstance(provider, dict)
    assert provider["compact_public_payload_v3"] is True
    assert isinstance(provider["compact_public_all_objects_omitted_v3"], int)
    raw_objects = overlays.get("objects")
    assert isinstance(raw_objects, list)
    overlay_objects = cast(list[object], raw_objects)
    assert overlay_objects


def test_live_state_v3_direct_read_invalidates_cache_when_display_state_advances(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
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


def test_compact_live_state_reuses_cached_response_for_display_heartbeat(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000001_same_window.png"
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
                "display_published_epoch": now_epoch,
                "last_capture_epoch": now_epoch,
                "last_window_path": str(window),
                "last_display_window_path": str(window),
                "last_display_surface_signature": "same",
                "overlay_source_window_signature": "same",
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
                "capture_count": 1,
                "frame_index": 1,
                "display_frame_id": 1,
                "overlay_frame_id": 1,
                "model_vote_frame_id": 1,
                "display_published_epoch": now_epoch,
                "last_display_window_path": str(window),
                "last_display_surface_signature": "same",
                "overlay_source_window_signature": "same",
                "display_snapshot_only_v3": True,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())
    first_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")
    assert first_response.status_code == 200

    def fail_full_build(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise AssertionError("compact display heartbeat should reuse cached live state")

    monkeypatch.setattr(mobile_app, "build_live_state_v3", fail_full_build)
    next_window = artifact_dir / "000002_window.png"
    next_window.write_bytes(b"window-2")
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "capture_count": 2,
                "frame_index": 1,
                "display_frame_id": 2,
                "overlay_frame_id": 1,
                "model_vote_frame_id": 1,
                "display_published_epoch": now_epoch + 1.0,
                "last_display_window_path": str(next_window),
                "last_display_surface_signature": "same",
                "overlay_source_window_signature": "same",
                "display_snapshot_only_v3": True,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    second_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")

    assert second_response.status_code == 200
    payload = second_response.json()
    assert payload["frame_id"] == 1
    assert payload["last_display_window_path"] == str(window)
    assert payload["provider_status"]["compact_cache_previous_signature_reused_v3"] is True


def test_compact_live_state_keeps_display_frame_when_overlay_identity_is_older(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000050_window.png"
    window.write_bytes(b"window")
    now_epoch = time.time()
    session_payload: dict[str, object] = {
        "session_id": "pocket-live-8788",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 50,
        "frame_index": 50,
        "display_frame_id": 50,
        "overlay_frame_id": 1,
        "model_vote_frame_id": 1,
        "display_published_epoch": now_epoch,
        "last_capture_epoch": now_epoch,
        "last_window_path": str(window),
        "last_display_window_path": str(window),
        "last_display_surface_signature": "same",
        "overlay_source_window_signature": "same",
        "tracking_summary": {},
        "latest_signal": {},
    }
    (session_dir / "session.json").write_text(json.dumps(session_payload), encoding="utf-8")
    (session_dir / "display_state.json").write_text(json.dumps(session_payload), encoding="utf-8")
    stale_epoch = now_epoch - 60.0
    cache_key = (
        "pocket-live-8788",
        "CLEAN_LIVE",
        _compact_live_state_response_cache_signature("pocket-live-8788"),
    )
    cached_payload: dict[str, object] = {
        "session_id": "pocket-live-8788",
        "frame_id": 1,
        "display_frame_id": 1,
        "requested_mode": "CLEAN_LIVE",
        "active_mode": "CLEAN_LIVE",
        "display_published_epoch": stale_epoch,
        "last_capture_epoch": stale_epoch,
        "tracking_summary": {"last_capture_epoch": stale_epoch},
        "overlays": {
            "objects": [
                {
                    "type": "DEMAND_ZONE",
                    "label": "DEMAND",
                    "bounds": [10, 20, 80, 60],
                    "frame_id": 1,
                    "chart_transform_id": "ct_pocket-live-8788_1",
                    "broker_source_lock_id": "vp:fixture",
                    "sequence_id": "seq_fixture",
                }
            ],
            "all_objects": [
                {
                    "type": "DEMAND_ZONE",
                    "label": "DEMAND",
                    "bounds": [10, 20, 80, 60],
                    "frame_id": 1,
                    "chart_transform_id": "ct_pocket-live-8788_1",
                    "broker_source_lock_id": "vp:fixture",
                    "sequence_id": "seq_fixture",
                }
            ],
        },
        "provider_status": {},
    }
    compact_cache = cast(
        MutableMapping[tuple[str, str, str], tuple[float, dict[str, object]]],
        getattr(mobile_app, "_COMPACT_LIVE_STATE_RESPONSE_CACHE"),
    )
    compact_cache[cache_key] = (time.time(), cached_payload)
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame_id"] == 50
    assert payload["display_frame_id"] == 50
    assert payload["frame_id"] != 1
    assert abs(float(payload["last_capture_epoch"]) - now_epoch) < 0.25
    assert "all_objects" not in payload["overlays"]


def test_compact_live_state_rebuilds_when_cached_overlay_frame_lags_chart_frame(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000050_window.png"
    chart = artifact_dir / "000050_chart.png"
    overlay = artifact_dir / "000050_overlay.png"
    window.write_bytes(b"window")
    chart.write_bytes(b"chart")
    overlay.write_bytes(b"overlay")
    now_epoch = time.time()
    session_payload: dict[str, object] = {
        "session_id": "pocket-live-8788",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 50,
        "frame_index": 50,
        "display_frame_id": 50,
        "chart_frame_id": 50,
        "overlay_frame_id": 50,
        "model_vote_frame_id": 50,
        "display_published_epoch": now_epoch,
        "last_capture_epoch": now_epoch,
        "last_window_path": str(window),
        "last_display_window_path": str(window),
        "last_chart_path": str(chart),
        "last_overlay_path": str(overlay),
        "last_display_surface_signature": "same",
        "overlay_source_window_signature": "same",
        "tracking_summary": {},
        "latest_signal": {},
    }
    (session_dir / "session.json").write_text(json.dumps(session_payload), encoding="utf-8")
    (session_dir / "display_state.json").write_text(json.dumps(session_payload), encoding="utf-8")
    stale_epoch = now_epoch - 60.0
    cache_key = (
        "pocket-live-8788",
        "CLEAN_LIVE",
        _compact_live_state_response_cache_signature("pocket-live-8788"),
    )
    cached_payload: dict[str, object] = {
        "session_id": "pocket-live-8788",
        "frame_id": 1,
        "display_frame_id": 1,
        "chart_frame_id": 1,
        "overlay_frame_id": 1,
        "overlay_object_frame_id": 1,
        "requested_mode": "CLEAN_LIVE",
        "active_mode": "CLEAN_LIVE",
        "display_published_epoch": stale_epoch,
        "last_capture_epoch": stale_epoch,
        "tracking_summary": {"last_capture_epoch": stale_epoch},
        "overlays": {
            "objects": [
                {
                    "type": "DEMAND_ZONE",
                    "label": "DEMAND",
                    "bounds": [10, 20, 80, 60],
                    "frame_id": 1,
                    "chart_transform_id": "ct_pocket-live-8788_1",
                    "broker_source_lock_id": "vp:fixture",
                    "sequence_id": "seq_fixture",
                }
            ],
            "all_objects": [
                {
                    "type": "DEMAND_ZONE",
                    "label": "DEMAND",
                    "bounds": [10, 20, 80, 60],
                    "frame_id": 1,
                    "chart_transform_id": "ct_pocket-live-8788_1",
                    "broker_source_lock_id": "vp:fixture",
                    "sequence_id": "seq_fixture",
                }
            ],
        },
        "provider_status": {},
    }
    compact_cache = cast(
        MutableMapping[tuple[str, str, str], tuple[float, dict[str, object]]],
        getattr(mobile_app, "_COMPACT_LIVE_STATE_RESPONSE_CACHE"),
    )
    compact_cache[cache_key] = (time.time(), cached_payload)
    build_calls = 0

    def fresh_full_build(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        nonlocal build_calls
        build_calls += 1
        fresh_overlay = {
            "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
            "overlay_id": "fresh-overlay",
            "object_id": "fresh-object",
            "track_id": "fresh-track",
            "type": "DEMAND_ZONE",
            "label": "DEMAND",
            "layer": "supply_demand",
            "bounds": [120, 220, 180, 260],
            "bbox": [120, 220, 180, 260],
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "confidence": 0.9,
            "truth_score": 0.9,
            "frame_id": 50,
            "chart_transform_id": "ct_pocket-live-8788_50",
            "broker_source_lock_id": "vp:fixture",
            "sequence_id": "seq_fixture",
            "source_agent": "test",
            "source_version": "PG_V3_OVERLAY_OBJECT_V1",
            "reason": "fresh overlay frame",
        }
        overlays_payload = {
            "count": 1,
            "total_count": 1,
            "renderable_count": 1,
            "hidden_count": 0,
            "rejected_count": 0,
            "overlay_object_frame_id": 50,
            "objects": [fresh_overlay],
            "all_objects": [fresh_overlay],
        }
        frame_timing = {
            "display_frame_id": 50,
            "overlay_frame_id": 50,
            "model_vote_frame_id": 50,
            "frame_age_ms": 0,
            "overlay_age_ms": 0,
            "model_vote_age_ms": 0,
            "packet_age_ms": 0,
            "frontend_render_age_ms": 0,
            "overlay_state_version": "ovlock_1_fresh",
            "overlay_frame_state_version": "ov_50_1_fresh",
        }
        return {
            **session_payload,
            "schema_version": "PG_LIVE_STATE_V3",
            "frame_id": 50,
            "overlay_object_frame_id": 50,
            "chart_transform_id": "ct_pocket-live-8788_50",
            "broker_source_lock_id": "vp:fixture",
            "requested_mode": "CLEAN_LIVE",
            "active_mode": "CLEAN_LIVE",
            "visible_layers": ["supply_demand"],
            "overlay_count": 1,
            "renderable_count": 1,
            "hidden_count": 0,
            "rejected_count": 0,
            "reason_if_empty": "",
            "overlay_state_version": "ovlock_1_fresh",
            "overlay_frame_state_version": "ov_50_1_fresh",
            "overlay_mode": {
                "requested": "CLEAN_LIVE",
                "active": "CLEAN_LIVE",
                "visible_layers": ["supply_demand"],
                "reason_if_empty": "",
                "overlay_object_frame_id": 50,
            },
            "overlays": overlays_payload,
            "overlay_objects": [fresh_overlay],
            "provider_status": {},
            "frame_timing_trace_v3": frame_timing,
            "frame_timing": frame_timing,
            "performance_trace_v3": {},
            "visual_health_v3": {},
            "frontend_heartbeat": {},
            "shooter": {},
        }

    monkeypatch.setattr(mobile_app, "build_live_state_v3", fresh_full_build)
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")

    assert response.status_code == 200
    payload = response.json()
    assert build_calls == 1
    assert payload["overlay_object_frame_id"] == 50
    assert payload["chart_transform_id"] == "ct_pocket-live-8788_50"
    assert payload["overlays"]["objects"][0]["frame_id"] == 50
    assert payload["overlays"]["objects"][0]["bounds"] == [120, 220, 180, 260]


def test_compact_live_state_holds_complete_session_while_display_snapshot_is_incomplete(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    stale_window = artifact_dir / "000001_window.png"
    fresh_window = artifact_dir / "000002_window.png"
    stale_window.write_bytes(b"window-1")
    fresh_window.write_bytes(b"window-2")
    now_epoch = time.time()
    stale_epoch = now_epoch - 60.0
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
                "display_published_epoch": stale_epoch,
                "last_capture_epoch": stale_epoch,
                "last_window_path": str(stale_window),
                "last_display_window_path": str(stale_window),
                "last_display_surface_signature": "surface-a",
                "overlay_source_window_signature": "surface-a",
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
                "capture_count": 2,
                "frame_index": 1,
                "display_frame_id": 2,
                "overlay_frame_id": 1,
                "model_vote_frame_id": 1,
                "display_capture_epoch": now_epoch,
                "display_published_epoch": now_epoch,
                "last_display_window_path": str(fresh_window),
                "last_display_surface_signature": "surface-a",
                "overlay_source_window_signature": "surface-a",
                "display_snapshot_only_v3": True,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame_id"] == 1
    assert payload["display_frame_id"] == 1
    assert abs(float(payload["last_capture_epoch"]) - stale_epoch) < 0.25
    assert payload["last_window_path"] == str(stale_window)
    assert payload["frame_timing_trace_v3"]["display_frame_id"] == 1
    assert payload["frame_timing_trace_v3"]["overlay_frame_id"] == 1
    assert payload["frame_timing_trace_v3"]["model_vote_frame_id"] == 1
    assert payload["frame_timing_trace_v3"]["backpressure"]["stale_limit_ms"] >= 30_000
    assert payload["frame_timing_trace_v3"]["backpressure"]["reject_limit_ms"] >= 45_000


def test_compact_live_state_holds_complete_session_when_surface_outruns_overlay_authority(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000002_new_window.png"
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
                "display_published_epoch": now_epoch,
                "last_capture_epoch": now_epoch,
                "last_window_path": str(window),
                "tracking_summary": {
                    "market_selector_visual_changed": True,
                    "market_selector_rebind_required": True,
                },
                "latest_signal": {
                    "market_selector_visual_changed": True,
                    "market_selector_rebind_required": True,
                },
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "capture_count": 2,
                "frame_index": 1,
                "display_frame_id": 2,
                "overlay_frame_id": 1,
                "model_vote_frame_id": 1,
                "display_published_epoch": now_epoch + 1.0,
                "last_display_window_path": str(window),
                "last_display_surface_signature": "new",
                "overlay_source_window_signature": "old",
                "display_snapshot_only_v3": True,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    client = TestClient(create_app())

    response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame_id"] == 1
    assert payload["display_frame_id"] == 1
    assert payload["renderable_count"] == 0
    assert payload["overlays"]["objects"] == []
    assert payload["provider_status"].get("compact_studying_new_pair_fast_path_v3") is not True
    assert payload["provider_status"]["live_state_source"] == "direct_file"


def test_compact_live_state_does_not_reuse_studying_new_pair_cache_after_overlay_recovers(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
    session_dir = data_dir / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window = artifact_dir / "000002_new_window.png"
    overlay = artifact_dir / "000001_old_overlay.png"
    window.write_bytes(b"window")
    overlay.write_bytes(b"overlay")
    now_epoch = time.time()
    base_session: dict[str, object] = {
        "session_id": "pocket-live-8788",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 1,
        "frame_index": 1,
        "display_frame_id": 1,
        "overlay_frame_id": 1,
        "model_vote_frame_id": 1,
        "display_published_epoch": now_epoch,
        "last_capture_epoch": now_epoch,
        "last_window_path": str(window),
        "last_overlay_path": str(overlay),
        "tracking_summary": {
            "market_selector_visual_changed": True,
            "market_selector_rebind_required": True,
        },
        "latest_signal": {
            "market_selector_visual_changed": True,
            "market_selector_rebind_required": True,
        },
    }
    (session_dir / "session.json").write_text(json.dumps(base_session), encoding="utf-8")
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "capture_count": 2,
                "frame_index": 1,
                "display_frame_id": 2,
                "overlay_frame_id": 1,
                "model_vote_frame_id": 1,
                "display_published_epoch": now_epoch + 1.0,
                "last_display_window_path": str(window),
                "last_overlay_path": str(overlay),
                "last_display_surface_signature": "new",
                "overlay_source_window_signature": "old",
                "display_snapshot_only_v3": True,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app())
    stale_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")
    assert stale_response.status_code == 200
    assert stale_response.json()["provider_status"]["compact_studying_new_pair_fast_path_v3"] is True

    recovered_session = {
        **base_session,
        "tracking_summary": {"status": "running"},
        "latest_signal": {"status": "running"},
        "last_display_surface_signature": "new",
        "overlay_source_window_signature": "new",
    }
    (session_dir / "session.json").write_text(json.dumps(recovered_session), encoding="utf-8")
    (session_dir / "display_state.json").write_text(
        json.dumps(
            {
                "session_id": "pocket-live-8788",
                "capture_count": 3,
                "frame_index": 2,
                "display_frame_id": 3,
                "overlay_frame_id": 2,
                "model_vote_frame_id": 2,
                "display_published_epoch": now_epoch + 2.0,
                "last_display_window_path": str(window),
                "last_overlay_path": str(overlay),
                "last_display_surface_signature": "new",
                "overlay_source_window_signature": "new",
                "display_snapshot_only_v3": True,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    recovered_response = client.get("/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE&compact=1")

    assert recovered_response.status_code == 200
    payload = recovered_response.json()
    assert payload["provider_status"].get("compact_studying_new_pair_fast_path_v3") is not True
    assert payload["tracking_summary"].get("market_selector_studying_new_pair") is not True


def test_performance_trace_v3_uses_direct_display_state_fast_path(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches()
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
    display_state: dict[str, object] = {
        "session_id": "pocket-live-8788",
        "display_frame_id": 2,
        "display_capture_epoch": now_epoch,
        "display_published_epoch": now_epoch,
        "last_display_window_path": str(window),
        "last_display_surface_signature": "display",
        "last_window_surface_signature": "display",
        "overlay_source_window_signature": "old-surface",
    }
    (session_dir / "display_state.json").write_text(json.dumps(display_state), encoding="utf-8")
    client = TestClient(create_app())
    fresh_display_epoch = time.time()
    display_state["display_capture_epoch"] = fresh_display_epoch
    display_state["display_published_epoch"] = fresh_display_epoch
    (session_dir / "display_state.json").write_text(json.dumps(display_state), encoding="utf-8")

    response = client.get("/v1/mobile/performance/trace/v3/pocket-live-8788")

    assert response.status_code == 200
    payload = response.json()
    assert payload["frame_id"] == 1
    assert payload["display_frame"]["frame_id"] == 1
    assert payload["display_frame"]["age_ms"] >= 10000
    assert payload["timing_trace"]["frame_gap_status"] in {"ALIGNED", "AUTHORITY_LOCKED"}
    assert payload["timing_trace"]["surface_signature_aligned"] is False
    assert payload["display_frame"]["url"] == str(window)


def test_performance_trace_v3_uses_compact_display_state_without_session_json(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", tmp_path / "missing_shooter_handshake.json")
    _clear_mobile_live_state_caches(direct_trace=True)
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
    _clear_mobile_live_state_caches(direct_trace=True)
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

    def _direct_window_tracker_session_snapshot(_session_id: str) -> None:
        return None

    monkeypatch.setattr(mobile_app, "_direct_window_tracker_session_snapshot", _direct_window_tracker_session_snapshot)
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
    _clear_mobile_live_state_caches()
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
    study_packet: dict[str, Any] = {
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


def test_model_council_latest_study_packet_endpoints_return_visibility_packet(monkeypatch: Any) -> None:
    monkeypatch.setenv("PHOENIXGUARD_WINDOW_TRACKER_DIRECT_READ", "0")
    now = time.time()
    study_packet: dict[str, Any] = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "pgpkt-study-visible",
        "packet_type": "STUDY_PACKET",
        "session_id": "pocket-live-8788",
        "created_epoch": now,
        "created_epoch_sec": now,
        "valid_until_epoch": now + 30.0,
        "valid_until_epoch_sec": now + 30.0,
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


def test_stale_shooter_reporter_heartbeat_is_reported_waiting(monkeypatch: Any, tmp_path: Path) -> None:
    handshake_path = tmp_path / "shooter_handshake.json"
    handshake_path.write_text(
        json.dumps(
            {
                "schema_version": "PG_SHOOTER_PACKAGE_REPORTER_HEARTBEAT_V1",
                "session_id": "pocket-live-8788",
                "state": "WAITING",
                "mode": "PACKAGE_REPORTER",
                "updated_epoch_sec": time.time() - 120.0,
                "will_click": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mobile_app, "_SHOOTER_HANDSHAKE_PATH", handshake_path)
    client = TestClient(create_app())

    response = client.get("/v1/mobile/shooter/sessions/pocket-live-8788/handshake")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "WAITING"
    assert payload["available"] is False
    assert "stale" in str(payload["reason"]).lower()


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
    session: dict[str, Any] = {"session_id": "pocket-live-8788", "latest_signal": {"execution_packet": packet}}

    health = build_model_council_health_from_session(session, now_epoch=1000.5)
    intelligence = build_intelligence_health(session)

    assert health["all_required_models_awake"] is True
    assert intelligence["council_final_state"] == "EXECUTABLE"
    assert intelligence["council_final_side"] == "BUY"
    assert intelligence["global_agent"] == "BUY"


def test_observability_reads_v3_study_result_without_execution_packet() -> None:
    session: dict[str, Any] = {
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
