from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from fastapi.testclient import TestClient
import pytest
from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from phoenixguard.mobile_api.app import create_app
import phoenixguard.mobile_api.window_tracker as window_tracker_module
from tests.support.v3_packet_samples import complete_sequence_context_v3

import shooter


NOW = 1_800_000_000.0


def compact_live_state_execution_packet(value: Mapping[str, Any]) -> dict[str, Any]:
    compactor = cast(
        Callable[[Any], dict[str, Any]],
        getattr(window_tracker_module, "_compact_live_state_execution_packet"),
    )
    return compactor(value)


def compact_persisted_execution_packet(value: Mapping[str, Any]) -> dict[str, Any]:
    compactor = cast(
        Callable[[Mapping[str, Any]], dict[str, Any]],
        getattr(window_tracker_module, "_compact_persisted_execution_packet"),
    )
    return compactor(value)


def _allowance_package(*, execution_ready: bool = True, package_type: str = "INTRADAY_ENTER_NOW") -> dict[str, object]:
    return {
        "schema_version": "PG_ALLOWANCE_PACKAGE_V1",
        "package_type": package_type,
        "allowance_family": "INTRADAY" if package_type == "INTRADAY_ENTER_NOW" else "SWING",
        "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3",
        "packet_authority": "PG_EXECUTION_PACKET_V3",
        "side": "BUY",
        "accepted": True,
        "decision_accepted": True,
        "execution_ready": execution_ready,
        "executable": execution_ready,
        "entry_now_allowed": package_type == "INTRADAY_ENTER_NOW",
        "timing_mode": "ENTER_NOW" if package_type == "INTRADAY_ENTER_NOW" else "WAIT_FOR_PULLBACK",
        "model_council_role": "CONTRIBUTOR_ONLY",
        "playbook_authorized": True,
        "opportunity_maturity": "ENTER_NOW",
        "visual_integrity": "PASS",
        "intraday_capture_active": package_type == "INTRADAY_ENTER_NOW",
        "lane_accepted": True,
        "accepted_lanes": ["SNIPER_ZONE_ENTRY"],
        "score_passed": True,
        "preferred_expiry_sec": 300,
        "release_state": "RELEASED",
        "reasoning_override_allowed": False,
        "selected_lane": "SNIPER_ZONE_ENTRY",
        "score": 0.84,
        "threshold": 0.70,
    }


def _packet(*, allowance_package: dict[str, object] | None = None) -> dict[str, Any]:
    packet = build_execution_packet_v3(
        packet_id="pgpkt-shooter-report-001",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=120,
        capture_count=130,
        state_version=140,
        created_epoch=NOW - 0.2,
        valid_until_epoch=NOW + 2.0,
        side="BUY",
        expiry_seconds=300,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "frame-hash",
            "previous_frame_hash": "prev-frame-hash",
        },
        model_council={
            "final_state": "EXECUTABLE",
            "final_side": "BUY",
            "decision_id": "mc-shooter-report-001",
            "maturity_stage": "EXECUTABLE_PACKET",
        },
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        sequence_context=complete_sequence_context_v3(
            sequence_id="seq-shooter-report-001",
            session_id="pocket-live-8788",
            side="BUY",
        ),
        allowance_package=allowance_package or _allowance_package(),
    )
    packet["trade_permission"] = {"permission_state": "GRANTED", "executable_allowed": True}
    packet["entry_quality"] = {"state": "ACCEPTABLE_ENTRY", "passes_executable_threshold": True}
    packet["market_trap"] = {"execution_allowed": True, "active_traps": []}
    entry_permission_v3 = {
        "schema_version": "PG_ENTRY_PERMISSION_V3",
        "state": "AUTHORIZED_NOW",
        "side": "BUY",
        "execution_packet_required": True,
        "execution_packet_present": True,
        "execution_packet_id": packet["packet_id"],
        "current_pressure_side": "SELL",
        "selected_authority_side": "BUY",
        "counter_pressure_entry": True,
        "override_applied": True,
        "override_basis": "FULL_SUITE_PROFESSIONAL_STORY",
        "raw_evidence_status_preserved": True,
        "study_packet_executable": False,
    }
    packet["entry_permission_v3"] = entry_permission_v3
    packet["allowance_package"]["entry_permission_v3"] = entry_permission_v3
    packet["overlay_truth_audit"] = {
        "valid_for_execution": True,
        "execution_safe": True,
        "frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "input_frame_hash": packet["live_integrity"]["input_frame_hash"],
        "objects": [],
    }
    return packet


def test_shooter_reports_allowed_intraday_package(tmp_path: Path) -> None:
    packet = _packet()
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is not None
    assert report["schema_version"] == "PG_SHOOTER_PACKAGE_REPORT_V1"
    assert report["state"] == "ALLOWED_PACKAGE_REPORTED"
    assert report["package_type"] == "INTRADAY_ENTER_NOW"
    assert report["broker_click_allowed"] is False
    assert report["will_click"] is False
    decoded = json.loads(report_path.read_text(encoding="utf-8"))
    assert decoded["packet_id"] == "pgpkt-shooter-report-001"
    assert decoded["allowance_package"]["execution_ready"] is True


def test_shooter_reports_allowed_swing_package(tmp_path: Path) -> None:
    packet = _packet(allowance_package=_allowance_package(package_type="SWING"))
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is not None
    assert report["package_type"] == "SWING"
    assert report["allowance_family"] == "SWING"
    assert report_path.exists()


def test_shooter_does_not_update_for_non_ready_package(tmp_path: Path) -> None:
    packet = _packet(allowance_package=_allowance_package(execution_ready=False))
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is None
    assert not report_path.exists()


def test_compactor_execution_endpoint_and_shooter_keep_allowance_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _packet()
    compacted = compact_live_state_execution_packet(packet)
    persisted = compact_persisted_execution_packet(packet)
    source_allowance = packet["allowance_package"]
    compact_allowance = compacted["allowance_package"]
    assert isinstance(source_allowance, Mapping)
    assert isinstance(compact_allowance, Mapping)
    source_allowance_mapping = cast(Mapping[str, Any], source_allowance)
    compact_allowance_mapping = cast(Mapping[str, Any], compact_allowance)
    for key, value in source_allowance_mapping.items():
        if not isinstance(value, Mapping):
            assert compact_allowance_mapping[key] == value
    assert compact_allowance_mapping["entry_now_allowed"] is True
    assert compact_allowance_mapping["timing_mode"] == "ENTER_NOW"
    assert compacted["entry_permission_v3"] == packet["entry_permission_v3"]
    assert compact_allowance_mapping["entry_permission_v3"] == packet["entry_permission_v3"]
    assert persisted["entry_permission_v3"] == packet["entry_permission_v3"]
    assert persisted["allowance_package"]["entry_permission_v3"] == packet["entry_permission_v3"]
    assert compacted["overlay_truth_audit"]["execution_safe"] is True

    class _Tracker:
        def get_session(self, session_id: str) -> dict[str, Any]:
            return {"session_id": session_id, "model_council_packet": compacted}

        def list_sessions(self, limit: int = 1) -> list[dict[str, Any]]:
            return [self.get_session("pocket-live-8788")][:limit]

        def latest_model_council_packet(self, session_id: str) -> dict[str, Any]:
            assert session_id == "pocket-live-8788"
            return compacted

    monkeypatch.setattr(window_tracker_module, "_now_epoch", lambda: NOW)
    client = TestClient(create_app(window_tracker_service=_Tracker()))  # type: ignore[arg-type]
    response = client.get(
        "/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest"
    )

    assert response.status_code == 200
    endpoint_packet = response.json()
    endpoint_allowance = endpoint_packet["allowance_package"]
    assert endpoint_allowance["entry_now_allowed"] is True
    assert endpoint_allowance["timing_mode"] == "ENTER_NOW"
    assert endpoint_packet["entry_permission_v3"]["state"] == "AUTHORIZED_NOW"
    assert endpoint_packet["entry_permission_v3"]["execution_packet_id"] == endpoint_packet["packet_id"]

    report_path = tmp_path / "shooter_handshake.json"
    report = shooter.publish_allowed_package_report(
        endpoint_packet,
        source_url=str(response.request.url),
        path=report_path,
        now_epoch=NOW,
    )

    assert report is not None
    assert report["state"] == "ALLOWED_PACKAGE_REPORTED"
    assert report["timing_mode"] == "ENTER_NOW"
    assert report["broker_click_allowed"] is False
    assert report["will_click"] is False


def test_shooter_rejects_packet_without_live_overlay_truth(tmp_path: Path) -> None:
    packet = _packet()
    packet.pop("overlay_truth_audit")
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is None
    assert not report_path.exists()


@pytest.mark.parametrize(
    "field",
    ("trade_permission", "entry_quality", "market_trap"),
)
def test_shooter_rejects_missing_live_decision_truth(tmp_path: Path, field: str) -> None:
    packet = _packet()
    packet.pop(field)
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is None
    assert not report_path.exists()


def test_shooter_rejects_contradictory_council_denial(tmp_path: Path) -> None:
    packet = _packet()
    packet["model_council"]["trade_permission"] = {
        "permission_state": "DENIED",
        "executable_allowed": False,
        "deny_reason": "COUNCIL_VETO",
    }
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is None
    assert not report_path.exists()


def test_shooter_rejects_missing_allowance_side(tmp_path: Path) -> None:
    packet = _packet()
    packet["allowance_package"].pop("side")
    packet["model_council"]["allowance_package"].pop("side", None)
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is None
    assert not report_path.exists()


@pytest.mark.parametrize("field", ("frame_id", "capture_count", "input_frame_hash"))
def test_shooter_rejects_overlay_identity_mismatch(tmp_path: Path, field: str) -> None:
    packet = _packet()
    packet["overlay_truth_audit"][field] = "mismatch" if field == "input_frame_hash" else 999
    report_path = tmp_path / "shooter_handshake.json"

    report = shooter.publish_allowed_package_report(
        packet,
        source_url="http://127.0.0.1:8793/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest",
        path=report_path,
        now_epoch=NOW,
    )

    assert report is None
    assert not report_path.exists()


@pytest.mark.parametrize(
    "case",
    (
        "denied_trap",
        "bad_entry",
        "missing_overlay",
        "overlay_frame_mismatch",
        "overlay_capture_mismatch",
        "overlay_hash_mismatch",
    ),
)
def test_execution_latest_endpoint_rejects_unsafe_live_packet(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    compacted = compact_live_state_execution_packet(_packet())
    unsafe = deepcopy(compacted)
    if case == "denied_trap":
        unsafe["market_trap"] = {
            "detected": True,
            "executable_allowed": False,
            "trap_type": "LATE_CHASE_TRAP",
        }
    elif case == "bad_entry":
        unsafe["entry_quality"] = {
            "state": "BAD_NOW",
            "passes_executable_threshold": False,
        }
    elif case == "missing_overlay":
        unsafe.pop("overlay_truth_audit")
    elif case == "overlay_frame_mismatch":
        unsafe["overlay_truth_audit"]["frame_id"] = 999
    elif case == "overlay_capture_mismatch":
        unsafe["overlay_truth_audit"]["capture_count"] = 999
    else:
        unsafe["overlay_truth_audit"]["input_frame_hash"] = "wrong-frame"

    class _Tracker:
        def get_session(self, session_id: str) -> dict[str, Any]:
            return {"session_id": session_id, "model_council_packet": unsafe}

        def list_sessions(self, limit: int = 1) -> list[dict[str, Any]]:
            return [self.get_session("pocket-live-8788")][:limit]

        def latest_model_council_packet(self, session_id: str) -> dict[str, Any]:
            raise KeyError(session_id)

    monkeypatch.setattr(window_tracker_module, "_now_epoch", lambda: NOW)
    client = TestClient(create_app(window_tracker_service=_Tracker()))  # type: ignore[arg-type]

    response = client.get(
        "/v1/mobile/model-council/sessions/pocket-live-8788/execution/latest"
    )

    assert response.status_code == 404
