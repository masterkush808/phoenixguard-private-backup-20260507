from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from phoenixguard.execution.shooter_modes import ShooterMode
from phoenixguard.simulation.paper_execution import PaperExecutionEngine, PaperExecutionPaths
from tests.support.v3_packet_samples import complete_sequence_context_v3


NOW = 1000.0


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
    )
    packet["instrument_context"]["broker_click_safe"] = broker_click_safe
    return packet


def _boxes() -> dict[str, dict[str, float]]:
    return {
        "buy_icon": {"x": 0.8, "y": 0.4},
        "sell_icon": {"x": 0.8, "y": 0.5},
        "time_button": {"x": 0.8, "y": 0.2},
        "time_300": {"x": 0.7, "y": 0.25},
        "hourly_input": {"x": 0.75, "y": 0.2},
        "minute_input": {"x": 0.78, "y": 0.2},
    }


def _read_first_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def test_paper_engine_records_executable_packet_and_future_candle_outcome(tmp_path: Path) -> None:
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
    assert result["packet_id"] == "pgpkt-paper"
    assert result["outcome"]["final_outcome_proxy"] == "WIN"
    assert result["outcome"]["sample_count"] == 2

    packet_row = _read_first_json(paths.packet_log)
    assert packet_row["packet"]["packet_id"] == "pgpkt-paper"
    assert packet_row["future_candle_count"] == 2
    assert packet_row["shooter_mode_result"]["reason"] == "PAPER_EXECUTION_RECORDED"

    shooter_row = _read_first_json(paths.shooter_paper_log)
    assert shooter_row["paper_filled"] is True
    assert shooter_row["broker_click_allowed"] is False


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
    assert not paths.shooter_paper_log.exists()


def test_broker_demo_rehearses_and_records_dry_run_plan_without_click(tmp_path: Path) -> None:
    paths = PaperExecutionPaths.in_dir(tmp_path)
    engine = PaperExecutionEngine(paths)

    result = engine.rehearse_broker_demo(
        _packet(packet_id="pgpkt-dry"),
        {"gate_1_second_read": "PASS", "reason": "DEMO_DRY_RUN"},
        _boxes(),
        (0, 0, 1000, 800),
        mode=ShooterMode.DRY_RUN_CLICK,
        now_epoch=NOW + 0.5,
    )

    assert result["mode"] == "DRY_RUN_CLICK"
    assert result["rehearsal_ready"] is True
    assert result["actual_clicked"] is False
    assert result["rehearsal"]["would_click"] == "BUY"
    assert result["coordinate_report"]["ok"] is True
    assert result["shooter_mode_result"]["reason"] == "DRY_RUN_CLICK_RECORDED"

    dry_row = _read_first_json(paths.shooter_dry_run_log)
    assert dry_row["coordinate_report"]["points"]["buy_icon"] == {"x": 800, "y": 320}
    assert _read_first_json(paths.broker_demo_log)["paper_engine_click_suppressed"] is True


def test_live_ready_demo_records_rehearsal_only_and_never_clicks(tmp_path: Path) -> None:
    paths = PaperExecutionPaths.in_dir(tmp_path)
    engine = PaperExecutionEngine(paths)

    result = engine.rehearse_broker_demo(
        _packet(packet_id="pgpkt-live-ready"),
        {"gate_1_second_read": "PASS", "reason": "DEMO_LIVE_READY"},
        _boxes(),
        (0, 0, 1000, 800),
        mode=ShooterMode.LIVE_READY,
        now_epoch=NOW + 0.5,
    )

    assert result["mode"] == "LIVE_READY"
    assert result["rehearsal_ready"] is True
    assert result["actual_clicked"] is False
    assert result["paper_engine_click_suppressed"] is True
    assert result["shooter_mode_result"]["reason"] == "LIVE_READY_REHEARSAL_READY_NO_CLICK"

    live_ready_row = _read_first_json(paths.shooter_live_ready_log)
    assert live_ready_row["clicked"] is False
    assert live_ready_row["execution_rehearsal"]["ready"] is True


def test_live_ready_demo_can_invoke_explicit_demo_click_executor(tmp_path: Path) -> None:
    paths = PaperExecutionPaths.in_dir(tmp_path)
    engine = PaperExecutionEngine(paths)
    calls: list[dict[str, Any]] = []

    def demo_click_executor(packet: dict[str, Any], rehearsal: dict[str, Any], coordinate_report: dict[str, Any]) -> dict[str, Any]:
        calls.append({"packet": packet, "rehearsal": rehearsal, "coordinate_report": coordinate_report})
        return {"clicked": True, "reason": "DEMO_CALLBACK_CLICKED"}

    result = engine.rehearse_broker_demo(
        _packet(packet_id="pgpkt-live-demo"),
        {"gate_1_second_read": "PASS", "reason": "DEMO_LIVE_READY"},
        _boxes(),
        (0, 0, 1000, 800),
        mode=ShooterMode.LIVE_READY,
        now_epoch=NOW + 0.5,
        execute_live_click=True,
        broker_click_executor=demo_click_executor,
    )

    assert result["mode"] == "LIVE_READY"
    assert result["rehearsal_ready"] is True
    assert result["actual_clicked"] is True
    assert result["paper_engine_click_suppressed"] is False
    assert result["live_click_report"]["reason"] == "DEMO_CALLBACK_CLICKED"
    assert calls[0]["coordinate_report"]["points"]["buy_icon"] == {"x": 800, "y": 320}

    live_ready_row = _read_first_json(paths.shooter_live_ready_log)
    assert live_ready_row["clicked"] is True
    assert live_ready_row["live_ready_reason"] == "LIVE_READY_DEMO_CLICK_RECORDED"
