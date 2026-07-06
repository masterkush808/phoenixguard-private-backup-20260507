from __future__ import annotations

import json
from pathlib import Path

from Backend.tools import classify_missed_opportunity_replay_v3 as classifier


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def test_classifies_missed_opportunity_from_nested_burn_sample() -> None:
    row = {
        "seq": 12,
        "frames": {"display_frame_id": 801, "capture_count": 55},
        "state_version": 1201,
        "entry": {
            "allowed": True,
            "execution_authorized": False,
            "packet_present": False,
            "side": "BUY",
            "playbook_strategy_authorized": True,
            "playbook_state": "ENTER_NOW",
            "opportunity_maturity_state": "ENTER_NOW",
            "blocked_by": "NONE",
        },
        "candle_movement_context_v3": {
            "current_leg": {"side": "BUY", "candle_count": 4, "move_stage": "DEVELOPING"}
        },
    }

    result = classifier.classify_replay_row(row)

    assert result["classification"] == "missed_opportunity"
    assert result["window"] == {"seq": 12, "frame_id": 801, "state_version": 1201, "capture_count": 55}
    assert result["signals"]["strong_opportunity"] is True


def test_classifies_bad_block_when_operational_blocker_rejects_enter_now_playbook() -> None:
    row = {
        "side": "SELL",
        "allowed": False,
        "execution_packet_present": False,
        "blocked_by": "NO_EXECUTION_PACKET",
        "opportunity_maturity": "ENTER_NOW",
        "book_strategy": {"state": "ENTER_NOW", "evidence": {"professional_grade": True}},
        "candle_movement_context_v3": {
            "current_leg": {"side": "SELL", "candle_count": 3, "move_stage": "DEVELOPING"}
        },
    }

    result = classifier.classify_replay_row(row)

    assert result["classification"] == "bad_block"
    assert result["signals"]["operational_blocker"] is True


def test_classifies_late_chase_avoided_for_late_block_without_enter_now_evidence() -> None:
    row = {
        "side": "BUY",
        "allowed": False,
        "blocked_by": "LATE_CHASE",
        "maturity": "WAIT",
        "current_leg": {"side": "BUY", "candle_count": 12, "move_stage": "MATURE"},
    }

    result = classifier.classify_replay_row(row)

    assert result["classification"] == "late_chase_avoided"
    assert result["signals"]["late_context"] is True


def test_classifies_late_recognition_when_packet_arrives_after_exhausted_leg() -> None:
    row = {
        "side": "SELL",
        "allowed": True,
        "execution_packet_present": True,
        "entry_now_allowed": True,
        "blocked_by": "NONE",
        "candle_movement_context_v3": {
            "current_leg": {"side": "SELL", "candle_count": 10, "move_stage": "MATURE"}
        },
    }

    result = classifier.classify_replay_row(row)

    assert result["classification"] == "late_recognition"
    assert result["decision"]["executable"] is True


def test_classifies_correct_block_for_dangerous_bad_entry() -> None:
    row = {
        "side": "BUY",
        "allowed": False,
        "blocked_by": "BAD_ENTRY_CLASS_ACTIVE",
        "maturity": "WATCH",
        "current_leg": {"side": "BUY", "candle_count": 2, "move_stage": "DEVELOPING"},
    }

    result = classifier.classify_replay_row(row)

    assert result["classification"] == "correct_block"
    assert result["signals"]["safety_blocker"] is True


def test_classifies_good_wait_for_prepare_retest_window() -> None:
    row = {
        "side": "SELL",
        "allowed": False,
        "blocked_by": "WAIT_FOR_RETEST",
        "book_strategy": {"state": "PREPARE"},
        "candle_movement_context_v3": {
            "current_leg": {"side": "SELL", "candle_count": 2, "move_stage": "DEVELOPING"}
        },
    }

    result = classifier.classify_replay_row(row)

    assert result["classification"] == "good_wait"
    assert result["signals"]["wait_signal"] is True


def test_cli_reads_jsonl_and_writes_json_report(tmp_path: Path) -> None:
    source = tmp_path / "samples.jsonl"
    output = tmp_path / "classified.json"
    _write_jsonl(
        source,
        [
            {
                "side": "BUY",
                "allowed": False,
                "blocked_by": "WAIT_FOR_PULLBACK",
                "maturity": "PREPARE",
            },
            {
                "side": "SELL",
                "allowed": False,
                "blocked_by": "BAD_ENTRY_TRAP",
            },
        ],
    )

    exit_code = classifier.main(["--input", str(source), "--output", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["summary"]["row_count"] == 2
    assert payload["summary"]["counts"]["good_wait"] == 1
    assert payload["summary"]["counts"]["correct_block"] == 1
