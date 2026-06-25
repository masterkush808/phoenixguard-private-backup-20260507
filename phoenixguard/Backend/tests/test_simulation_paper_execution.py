from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from phoenixguard.simulation.paper_execution import PaperExecutionEngine, PaperExecutionPaths
from tests.support.v3_packet_samples import complete_sequence_context_v3


NOW = 1000.0


def _allowance_package() -> dict[str, object]:
    return {
        "schema_version": "PG_ALLOWANCE_PACKAGE_V1",
        "package_type": "INTRADAY_ENTER_NOW",
        "allowance_family": "INTRADAY",
        "execution_authority": "PG_EXECUTION_PACKET_V3",
        "side": "BUY",
        "accepted": True,
        "decision_accepted": True,
        "execution_ready": True,
        "entry_now_allowed": True,
        "timing_mode": "ENTER_NOW",
        "selected_lane": "SNIPER_ZONE_ENTRY",
        "score": 0.84,
        "threshold": 0.70,
    }


def _packet(*, packet_id: str = "pgpkt-paper", side: str = "BUY", broker_click_safe: bool = True) -> dict[str, Any]:
    packet = build_execution_packet_v3(
        packet_id=packet_id,
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=20,
        capture_count=21,
        state_version=120,
        side=side,
        expiry_seconds=300,
        input_frame_hash=f"frame-{packet_id}",
        previous_frame_hash=f"frame-{packet_id}-prev",
        created_epoch=NOW,
        valid_until_epoch=NOW + 2.0,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "packet_age_ms": 100,
        },
        model_council={
            "final_state": "EXECUTABLE",
            "final_side": side,
            "decision_id": f"mc-{packet_id}",
            "maturity_stage": "EXECUTABLE_PACKET",
            "dominance_margin": 0.66,
            "disagreement_score": 0.12,
            "flip_flop_state": "STABLE_EXECUTABLE",
        },
        runtime_model_health={
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "max_model_latency_ms": 25,
            "queue_depth": 0,
        },
        sequence_context=complete_sequence_context_v3(
            sequence_id=f"seq-{packet_id}",
            session_id="pocket-live-8788",
            side=side,
        ),
        allowance_package=_allowance_package(),
    )
    packet["instrument_context"]["broker_click_safe"] = broker_click_safe
    return packet


def _read_first_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def test_paper_engine_records_executable_packet_and_package_report(tmp_path: Path) -> None:
    paths = PaperExecutionPaths.in_dir(tmp_path)
    engine = PaperExecutionEngine(paths)

    result = engine.record_executable_packet(
        _packet(),
        [
            {"open": 100.0, "high": 100.8, "low": 99.8, "close": 100.5},
            {"open": 100.5, "high": 101.8, "low": 100.3, "close": 101.6},
        ],
        entry_context={"entry_price": 100.0, "target_price": 101.5, "stop_price": 99.2},
        now_epoch=NOW + 0.5,
        expected_session_id="pocket-live-8788",
    )

    assert result["recorded"] is True
    assert result["actual_clicked"] is False
    assert result["broker_click_allowed"] is False
    assert result["packet_id"] == "pgpkt-paper"
    assert result["outcome"]["final_outcome_proxy"] == "WIN"
    assert result["package_reporter_result"]["reason"] == "PACKAGE_REPORT_RECORDED"

    packet_row = _read_first_json(paths.packet_log)
    assert packet_row["packet"]["packet_id"] == "pgpkt-paper"
    assert packet_row["future_candle_count"] == 2

    report_row = _read_first_json(paths.package_report_log)
    assert report_row["packet_id"] == "pgpkt-paper"
    assert report_row["execution_removed"] is True


def test_paper_engine_rejects_non_executable_packet_without_writing_logs(tmp_path: Path) -> None:
    paths = PaperExecutionPaths.in_dir(tmp_path)
    packet = _packet(packet_id="pgpkt-watch")
    packet["execution"]["enabled"] = False
    packet["execution"]["state"] = "WATCHING"
    packet["model_council"]["final_state"] = "WATCHING"

    result = PaperExecutionEngine(paths).record_executable_packet(packet, now_epoch=NOW + 0.5)

    assert result["recorded"] is False
    assert result["actual_clicked"] is False
    assert result["reason"] == "EXECUTION_NOT_ENABLED"
    assert any(issue["code"] == "COUNCIL_STATE_NOT_EXECUTABLE" for issue in result["validation"]["issues"])
    assert not paths.packet_log.exists()
    assert not paths.package_report_log.exists()


def test_broker_demo_rehearsal_never_invokes_click_executor(tmp_path: Path) -> None:
    paths = PaperExecutionPaths.in_dir(tmp_path)
    engine = PaperExecutionEngine(paths)
    calls: list[dict[str, Mapping[str, Any]]] = []

    def demo_click_executor(packet: Mapping[str, Any], rehearsal: Mapping[str, Any], coordinate_report: Mapping[str, Any]) -> dict[str, Any]:
        calls.append({"packet": packet, "rehearsal": rehearsal, "coordinate_report": coordinate_report})
        return {"clicked": True, "reason": "DEMO_CALLBACK_CLICKED"}

    result = engine.rehearse_broker_demo(
        _packet(packet_id="pgpkt-report"),
        {"reason": "PACKAGE_REPORTER_REHEARSAL"},
        {},
        (0, 0, 1000, 800),
        now_epoch=NOW + 0.5,
        execute_live_click=True,
        broker_click_executor=demo_click_executor,
    )

    assert result["mode"] == "PACKAGE_REPORTER"
    assert result["actual_clicked"] is False
    assert result["paper_engine_click_suppressed"] is True
    assert result["live_click_report"]["reason"] == "SHOOTER_EXECUTION_RETIRED"
    assert result["coordinate_report"]["reason"] == "BROKER_COORDINATE_EXECUTION_RETIRED"
    assert result["package_reporter_result"]["reason"] == "PACKAGE_REPORTER_REHEARSAL_RECORDED"
    assert calls == []
