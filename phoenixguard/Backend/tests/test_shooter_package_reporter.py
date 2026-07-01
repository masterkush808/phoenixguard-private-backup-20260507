from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from tests.support.v3_packet_samples import complete_sequence_context_v3

import shooter


NOW = 1_800_000_000.0


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
        "entry_now_allowed": package_type == "INTRADAY_ENTER_NOW",
        "timing_mode": "ENTER_NOW" if package_type == "INTRADAY_ENTER_NOW" else "WAIT_FOR_PULLBACK",
        "selected_lane": "SNIPER_ZONE_ENTRY",
        "score": 0.84,
        "threshold": 0.70,
    }


def _packet(*, allowance_package: dict[str, object] | None = None) -> dict[str, Any]:
    return build_execution_packet_v3(
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
