from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from phoenixguard.simulation.decision_replay import CouncilReplayEngine, record_agent_votes
from phoenixguard.simulation.screenshot_replay import (
    ReplayLoader,
    ReplayMode,
    ReplayPacketPublisher,
    ReplaySession,
)


def _write_frame(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-png-bytes")


def test_replay_loader_reads_frames_and_expected_metadata(tmp_path: Path) -> None:
    root = tmp_path / "late_chase"
    _write_frame(root / "frames" / "frame_000001.png")
    (root / "labels.json").write_text(json.dumps({"frame_000001": {"zones": [{"label": "supply"}]}}), encoding="utf-8")
    (root / "expected_decisions.json").write_text(
        json.dumps({"frame_000001": {"expected": {"execution_state": "WATCHING", "trap": "LATE_CHASE_AFTER_IMPULSE"}}}),
        encoding="utf-8",
    )

    frames = ReplayLoader(root).load()

    assert len(frames) == 1
    assert frames[0].frame_id == 1
    assert frames[0].expected["execution_state"] == "WATCHING"
    assert frames[0].labels["zones"][0]["label"] == "supply"


def test_packet_publisher_builds_council_snapshot_from_expected_trap(tmp_path: Path) -> None:
    root = tmp_path / "late_chase"
    _write_frame(root / "frames" / "frame_000001.png")
    (root / "expected_decisions.json").write_text(
        json.dumps(
            {
                "frame_000001": {
                    "expected": {
                        "dominant_side": "BUY",
                        "entry_quality": "BAD_NOW",
                        "trap": "LATE_CHASE_AFTER_IMPULSE",
                        "execution_state": "WATCHING",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    frame = ReplayLoader(root).load()[0]

    packet = ReplayPacketPublisher().publish(frame)

    assert packet.snapshot["candidate_side"] == "BUY"
    assert packet.snapshot["angle_context"]["late_chase_risk"] is True
    assert packet.expected["trap"] == "LATE_CHASE_AFTER_IMPULSE"


def test_replay_session_records_avoided_bad_trade(tmp_path: Path) -> None:
    root = tmp_path / "late_chase"
    _write_frame(root / "frames" / "frame_000001.png")
    (root / "expected_decisions.json").write_text(
        json.dumps({"frame_000001": {"expected": {"dominant_side": "BUY", "trap": "LATE_CHASE_AFTER_IMPULSE", "execution_state": "WATCHING"}}}),
        encoding="utf-8",
    )

    session = ReplaySession.from_root(root, mode=ReplayMode.FAST_REPLAY)
    result = session.run()

    assert result["summary"]["frames_processed"] == 1
    assert result["summary"]["avoided_bad_trades"] == 1
    assert result["summary"]["late_chase_avoidance_count"] == 1


def test_replay_session_integrates_record_executable_paper_executor(tmp_path: Path) -> None:
    root = tmp_path / "paper"
    _write_frame(root / "frames" / "frame_000001.png")

    class PaperExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def record_executable_packet(self, packet: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return {"recorded": True, "packet_id": packet.get("packet_id"), "outcome_metrics": {"mfe": 1.0, "mae": 0.5}}

    def _council_evaluator(_snapshot: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "packet_id": "pgpkt-test",
            "execution": {"enabled": True},
            "model_council": {"final_state": "EXECUTABLE"},
        }

    executor = PaperExecutor()
    session = ReplaySession.from_root(
        root,
        mode=ReplayMode.FAST_REPLAY,
        council_evaluator=_council_evaluator,
        paper_executor=executor,
    )
    result = session.run()

    assert executor.calls == 1
    assert result["summary"]["paper_entries"] == 1
    assert result["summary"]["MFE/MAE ratio"] == 2.0


def test_decision_replay_records_votes_and_correctness() -> None:
    snapshot = {
        "frame_id": 7,
        "expected": {"execution_state": "WATCHING"},
        "market_context": {"dominant_side": "BUY", "global_side": "BUY", "local_side": "BUY", "opposing_force_distance_ok": True},
        "global_structure": {"global_side": "BUY", "global_confidence": 0.8},
        "local_micro_structure": {"local_side": "BUY", "confidence": 0.72},
    }
    result = {"model_council": {"final_state": "WATCHING", "final_side": "BUY", "maturity_stage": "OBSERVATION"}}

    votes = record_agent_votes(snapshot, result)
    replay = CouncilReplayEngine(evaluator=lambda _snapshot: result).replay([snapshot], now_epoch=1000.0)

    assert votes[-1]["agent"] == "arbitration"
    assert replay["correct"] is True
    assert replay["state_distribution"]["WATCHING"] == 1
