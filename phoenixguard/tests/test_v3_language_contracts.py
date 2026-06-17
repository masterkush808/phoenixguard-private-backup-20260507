from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from phoenixguard.execution.packet_v3 import build_execution_packet_v3, validate_execution_packet_v3
from phoenixguard.execution.v3_language import (
    EXECUTION_PACKET_TYPE,
    STUDY_PACKET_TYPE,
    InstrumentContextState,
    ExecutionState,
    Side,
    is_packet_current,
    packet_age_ms,
    public_language_scorecard,
    validate_cache_entry_language,
    validate_enum_value,
    validate_execution_packet_language,
    validate_study_packet_language,
)
from phoenixguard.mobile_api.app import create_app


NOW = 1_800_000_000.0
ROOT = Path(__file__).resolve().parents[1]


def _execution_packet(**overrides):
    packet = build_execution_packet_v3(
        packet_id="pgpkt-language-001",
        session_id="pocket-live-8788",
        symbol="EUR/JPY OTC",
        timeframe="M5",
        frame_id=12,
        capture_count=13,
        state_version=14,
        side="BUY",
        expiry_seconds=300,
        created_epoch=NOW - 0.2,
        valid_until_epoch=NOW + 4.0,
        input_frame_hash="frame-current",
        previous_frame_hash="frame-prev",
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "max_model_latency_ms": 20,
            "queue_depth": 0,
        },
    )
    packet["packet_type"] = EXECUTION_PACKET_TYPE
    _deep_update(packet, overrides)
    return packet


def _study_packet(**overrides):
    packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "study-language-001",
        "packet_type": STUDY_PACKET_TYPE,
        "created_epoch": NOW,
        "valid_until_epoch": NOW + 8.0,
        "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
        "model_council": {
            "final_state": "WATCHING",
            "final_side": "BUY",
            "final_execution_score": 0.62,
            "execution_threshold": 0.70,
            "blocked_by": "TIMING_READY",
            "denied_at": "TIMING_READY",
            "next_required": "timing_ready=true",
        },
        "promotion_trace": {
            "packet_id": "study-language-001",
            "blocked_by": "TIMING_READY",
            "denied_at": "TIMING_READY",
            "next_required": "timing_ready=true",
            "packet_result": "STUDY_PACKET_PUBLISHED",
        },
    }
    _deep_update(packet, overrides)
    return packet


def _deep_update(target, updates):
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def test_v3_language_constitution_exists() -> None:
    path = ROOT / "phoenixguard" / "V3_LANGUAGE_CONSTITUTION.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Only a validated `PG_EXECUTION_PACKET_V3`" in text
    assert "`raw_side`" in text
    assert "`execution.side`" in text


def test_pg_execution_packet_v3_requires_schema_version() -> None:
    packet = _execution_packet()
    packet.pop("schema_version")
    result = validate_execution_packet_language(packet, now_epoch=NOW)
    assert result.rejected
    assert "UNKNOWN_PACKET_SCHEMA_REJECTED" in result.reason_codes


def test_pg_execution_packet_v3_requires_packet_id() -> None:
    result = validate_execution_packet_language(_execution_packet(packet_id=""), now_epoch=NOW)
    assert result.rejected
    assert "MISSING_PACKET_ID" in result.reason_codes


def test_pg_execution_packet_v3_requires_execution_side() -> None:
    packet = _execution_packet(execution={"side": None}, raw_side="BUY", action="BUY", execution_action="BUY")
    result = validate_execution_packet_language(packet, now_epoch=NOW)
    assert result.rejected
    assert "MISSING_EXECUTION_SIDE" in result.reason_codes
    assert "RAW_SIDE_ALIAS_CANNOT_EXECUTE" in result.reason_codes


def test_pg_execution_packet_v3_requires_expiry_seconds() -> None:
    result = validate_execution_packet_language(_execution_packet(execution={"expiry_seconds": 0}), now_epoch=NOW)
    assert result.rejected
    assert "MISSING_EXPIRY_SECONDS" in result.reason_codes


def test_pg_execution_packet_v3_requires_time_sequence() -> None:
    packet = _execution_packet()
    packet["execution"].pop("time_sequence")
    result = validate_execution_packet_v3(packet, now_epoch=NOW)
    assert result.rejected
    assert "MISSING_TIME_SEQUENCE" in result.reason_codes


def test_pg_execution_packet_v3_requires_time_sequence_target_text() -> None:
    packet = _execution_packet()
    packet["execution"]["time_sequence"].pop("target_text")
    result = validate_execution_packet_v3(packet, now_epoch=NOW)
    assert result.rejected
    assert "MISSING_TIME_SEQUENCE_TARGET_TEXT" in result.reason_codes


def test_study_packet_cannot_execute() -> None:
    packet = _study_packet(execution={"enabled": True, "state": "EXECUTABLE"})
    result = validate_study_packet_language(packet, now_epoch=NOW)
    assert result.rejected
    assert "STUDY_PACKET_EXECUTION_ENABLED" in result.reason_codes
    assert "STUDY_PACKET_EXECUTABLE_STATE" in result.reason_codes


def test_study_packet_requires_created_and_valid_until_epoch() -> None:
    packet = _study_packet()
    packet.pop("created_epoch", None)
    packet.pop("created_epoch_sec", None)
    packet.pop("valid_until_epoch", None)
    packet.pop("valid_until_epoch_sec", None)

    result = validate_study_packet_language(packet, now_epoch=NOW)

    assert result.rejected
    assert "MISSING_CREATED_EPOCH_SEC" in result.reason_codes
    assert "MISSING_VALID_UNTIL_EPOCH_SEC" in result.reason_codes


def test_final_side_must_equal_execution_side() -> None:
    packet = _execution_packet(model_council={"final_side": "SELL"})
    result = validate_execution_packet_language(packet, now_epoch=NOW)
    assert result.rejected
    assert "FINAL_SIDE_MUST_EQUAL_EXECUTION_SIDE" in result.reason_codes


def test_unknown_packet_schema_rejected() -> None:
    packet = _execution_packet(schema_version="PG_EXECUTION_PACKET_V2")
    result = validate_execution_packet_language(packet, now_epoch=NOW)
    assert result.rejected
    assert "UNKNOWN_PACKET_SCHEMA_REJECTED" in result.reason_codes


def test_packet_id_never_null_when_published() -> None:
    assert validate_study_packet_language(_study_packet(), now_epoch=NOW).ok
    assert validate_execution_packet_language(_execution_packet(), now_epoch=NOW).ok
    assert validate_study_packet_language(_study_packet(packet_id=None), now_epoch=NOW).rejected
    assert validate_execution_packet_language(_execution_packet(packet_id=None), now_epoch=NOW).rejected


def test_packet_age_ms_uses_seconds_epoch_correctly() -> None:
    assert packet_age_ms({"created_epoch_sec": NOW - 1.25}, now_epoch=NOW) == 1250


def test_execution_packet_expires_after_valid_until_epoch_sec() -> None:
    packet = _execution_packet(valid_until_epoch_sec=NOW - 0.1, valid_until_epoch=NOW - 0.1)

    assert is_packet_current(packet, now_epoch=NOW) is False
    result = validate_execution_packet_language(packet, now_epoch=NOW)
    assert result.rejected
    assert "EXECUTION_PACKET_EXPIRED" in result.reason_codes


def test_cache_entry_requires_schema_version_and_ttl() -> None:
    result = validate_cache_entry_language({"created_epoch_sec": NOW}, now_epoch=NOW)

    assert result.rejected
    assert "MISSING_CACHE_SCHEMA_VERSION" in result.reason_codes
    assert "MISSING_CACHE_VALID_UNTIL_EPOCH_SEC" in result.reason_codes


def test_cache_entry_rejected_after_ttl() -> None:
    result = validate_cache_entry_language(
        {
            "schema_version": "PG_CACHE_V3",
            "created_epoch_sec": NOW - 10,
            "valid_until_epoch_sec": NOW - 1,
        },
        now_epoch=NOW,
    )

    assert result.rejected
    assert "CACHE_ENTRY_EXPIRED" in result.reason_codes


def test_invalid_side_enum_rejected() -> None:
    issue = validate_enum_value(Side, "CALL", "execution.side")
    assert issue is not None
    assert issue.code == "INVALID_EXECUTION_SIDE"


def test_invalid_execution_state_rejected() -> None:
    issue = validate_enum_value(ExecutionState, "ARMED", "execution.state")
    assert issue is not None
    assert issue.code == "INVALID_EXECUTION_STATE"


def test_invalid_instrument_context_state_rejected() -> None:
    issue = validate_enum_value(InstrumentContextState, "MAYBE_SAFE", "instrument_context.instrument_context_state")
    assert issue is not None
    assert issue.code == "INVALID_INSTRUMENT_CONTEXT_INSTRUMENT_CONTEXT_STATE"


def test_no_direct_pyautogui_action_outside_low_level_adapter() -> None:
    offenders: list[str] = []
    for path in [ROOT / "shooter.py", *list((ROOT / "phoenixguard").rglob("*.py"))]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in ("pyautogui.click", "pyautogui.moveTo", "pyautogui.press", "pyautogui.hotkey", "pyautogui.typewrite"):
            if needle in text and path.name != "shooter_action_sequencer.py":
                offenders.append(f"{path.relative_to(ROOT)}:{needle}")
    assert offenders == []


def test_public_language_scorecard_names_v3_authorities() -> None:
    scorecard = public_language_scorecard()
    assert scorecard["execution_authority"] == "validated PG_EXECUTION_PACKET_V3 only"
    assert scorecard["action_authority"] == "ShooterActionSequencerV2 only"
    assert scorecard["operator_truth"] == "FloatingStateV2 reducer only"


def test_runtime_trace_v3_contains_all_core_nodes() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "cache_status": "fresh",
                "model_health": {"models_awake": 7, "models_total": 7},
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "PG_RUNTIME_TRACE_V3"
    assert payload["alignment"]["status"] == "PASS"
    for node in (
        "tracker_latest",
        "model_council_latest",
        "study_latest",
        "execution_latest",
        "floating_state",
        "shooter_handshake",
        "model_health",
        "calibration_status",
        "cache_status",
        "sequence_context",
    ):
        assert node in payload["endpoints"]
    assert "sequence_context_readiness" in payload
    assert "minimum_required_sequence_length" in payload["sequence_context_readiness"]
    assert payload["dataflow_contract_trace"]["schema_version"] == "PG_DATAFLOW_CONTRACT_TRACE_V3"
    assert payload["dataflow_contract_trace"]["nodes"]["PG_EXECUTION_PACKET_V3"] == "NOT_PUBLISHED"
    assert "sequence_context" in payload["certification_gates"]


def test_runtime_trace_uses_nested_broker_surface_source_lock() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "cache_status": "fresh",
                "model_health": {"models_awake": 7, "models_total": 7},
                "broker_surface": {
                    "broker_source_lock": {
                        "valid": True,
                        "status": "VALID",
                        "lock_id": "broker-surface-lock-001",
                    },
                },
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    payload = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788").json()

    assert payload["dataflow_contract_trace"]["nodes"]["BrokerSourceLockV3"] == "PASS"
    assert payload["certification_gates"]["source_lock"]["status"] == "PASS"
    assert payload["certification_gates"]["source_lock"]["evidence"]["lock_id"] == "broker-surface-lock-001"


def test_runtime_trace_does_not_certify_display_only_overlay_authority_as_broker_source_lock() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "cache_status": "fresh",
                "display_snapshot_only_v3": True,
                "last_overlay_path": "000001_stale_overlay.png",
                "model_health": {"models_awake": 7, "models_total": 7},
                "tracking_summary": {
                    "broker_source_lock": {
                        "valid": False,
                        "wrong_surface": True,
                        "status": "TITLE_MATCH_PIXEL_MISMATCH",
                        "lock_id": "title-only-edge-window",
                    },
                },
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    payload = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788").json()

    evidence = payload["certification_gates"]["source_lock"]["evidence"]
    assert payload["dataflow_contract_trace"]["nodes"]["BrokerSourceLockV3"] == "FAIL"
    assert payload["certification_gates"]["source_lock"]["status"] == "FAIL"
    assert payload["certification_gates"]["source_lock"]["passed"] is False
    assert evidence["display_only_overlay_authority_locked"] is True
    assert evidence["display_only_overlay_authority_status"] == "PASS"


def test_runtime_trace_does_not_synthesize_missing_broker_source_lock() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "cache_status": "fresh",
                "model_health": {"models_awake": 7, "models_total": 7},
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    payload = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788").json()

    assert payload["dataflow_contract_trace"]["nodes"]["BrokerSourceLockV3"] == "MISSING"
    assert payload["certification_gates"]["source_lock"]["status"] == "MISSING"
    assert payload["certification_gates"]["source_lock"]["evidence"] == {}


def test_runtime_trace_detects_stale_study_packet() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "cache_status": "fresh",
                "model_health": {"models_awake": 7, "models_total": 7},
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id, valid_until_epoch=1.0, valid_until_epoch_sec=1.0)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788")
    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoints"]["study_latest"]["status"] == "STALE"
    assert payload["alignment"]["status"] == "FAIL"
    assert "study_latest_stale" in payload["alignment"]["issues"]


def test_runtime_trace_ignores_stale_execution_history_when_study_is_fresh() -> None:
    stale_execution = _execution_packet(valid_until_epoch=1.0, valid_until_epoch_sec=1.0)

    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "cache_status": "fresh",
                "model_health": {"models_awake": 7, "models_total": 7},
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "model_council_result": stale_execution,
                "model_council_study_packet": _study_packet(session_id=session_id),
            }

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            return stale_execution

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788")
    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoints"]["model_council_latest"]["status"] == "PASS"
    assert payload["endpoints"]["execution_latest"]["status"] == "MISSING"
    assert payload["endpoints"]["execution_latest"]["stale_packet_id"] == "pgpkt-language-001"
    assert payload["alignment"]["status"] == "PASS"
    assert "execution_latest_stale" not in payload["alignment"]["issues"]
    assert payload["alignment"]["packet_ids"]["execution"] == ""


def test_runtime_trace_detects_legacy_study_packet_without_valid_until_as_stale() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "tracking_enabled": True,
                "last_capture_epoch": NOW,
                "decision_valid_until_epoch": NOW + 8.0,
                "cache_status": "fresh",
                "model_health": {"models_awake": 7, "models_total": 7},
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            packet = _study_packet(session_id=session_id, created_epoch=1.0)
            packet.pop("valid_until_epoch", None)
            packet.pop("valid_until_epoch_sec", None)
            return packet

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788")
    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoints"]["study_latest"]["status"] == "STALE"
    assert payload["alignment"]["status"] == "FAIL"
    assert "study_latest_stale" in payload["alignment"]["issues"]


def test_study_latest_endpoint_rejects_stale_packet() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "tracking_enabled": True,
                "last_capture_epoch": NOW,
                "decision_valid_until_epoch": NOW + 8.0,
                "cache_status": "fresh",
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id, valid_until_epoch=1.0, valid_until_epoch_sec=1.0)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/model-council/sessions/pocket-live-8788/study/latest")
    assert response.status_code == 404
    assert "stale" in response.json()["detail"].lower()


def test_runtime_trace_marks_stale_tracker_session() -> None:
    class FakeTracker:
        def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "session_id": session_id,
                "status": "running",
                "tracking_enabled": True,
                "capture_interval_sec": 0.5,
                "last_capture_epoch": 1.0,
                "decision_valid_until_epoch": 2.0,
                "cache_status": "fresh",
                "model_health": {"models_awake": 7, "models_total": 7},
            }

        def list_sessions(self, limit: int = 1) -> list[dict[str, object]]:
            return [self.get_session("pocket-live-8788")]

        def latest_model_council_state(self, session_id: str) -> dict[str, object]:
            return {"session_id": session_id, "final_state": "WATCHING", "final_side": "BUY"}

        def latest_model_council_study_packet(self, session_id: str) -> dict[str, object]:
            return _study_packet(session_id=session_id)

        def latest_model_council_packet(self, session_id: str) -> dict[str, object]:
            raise KeyError("no executable")

    client = TestClient(create_app(window_tracker_service=FakeTracker()))  # type: ignore[arg-type]
    response = client.get("/v1/mobile/runtime/trace/v3?session_id=pocket-live-8788")
    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoints"]["tracker_latest"]["status"] == "STALE"
    assert payload["alignment"]["status"] == "FAIL"
    assert "tracker_latest_stale" in payload["alignment"]["issues"]


def test_execution_validator_rejects_study_packet_type_even_with_v3_schema() -> None:
    packet = _execution_packet(packet_type=STUDY_PACKET_TYPE)
    result = validate_execution_packet_v3(packet, now_epoch=NOW)
    assert result.rejected
    assert "PACKET_TYPE_NOT_EXECUTION_PACKET" in result.reason_codes
