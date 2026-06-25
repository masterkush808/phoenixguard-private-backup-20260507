from __future__ import annotations

import csv
from pathlib import Path

from Backend.scripts_data.export_runtime_contradiction_queue import (
    build_runtime_review_row,
    export_runtime_contradiction_queue,
)


def test_build_runtime_review_row_marks_projected_label_conflict_as_hard_negative() -> None:
    row = build_runtime_review_row(
        image_path="test/SELL/example.png",
        folder_label="SELL",
        result={
            "action": "BUY",
            "trade_bias": "BUY",
            "decision_state": "PROJECTED",
            "execution_permission": "WAIT_FOR_CONFIRMATION",
            "confidence": 0.71,
            "projection_support": True,
            "projection_watch_ready": True,
            "chart_state": {
                "direction": "BUY",
                "direction_probability": 0.67,
                "structure_setup": "reversal_release",
                "structure_setup_source": "forecast",
                "projection_bias_direction": "BUY",
                "projection_bias_confidence": 0.74,
                "projection_dominance": 0.11,
            },
            "projection": {
                "direction": "BUY",
                "box_type": "reversal_base",
                "confidence": 0.74,
                "dominance": 0.11,
            },
        },
    )

    assert row["review_bucket"] == "hard_negative"
    assert "action_opposes_label" in row["review_reasons"]
    assert "projection_opposes_label" in row["review_reasons"]
    assert float(row["review_priority"]) > 0.7


def test_build_runtime_review_row_marks_aligned_hold_as_ambiguous_wait() -> None:
    row = build_runtime_review_row(
        image_path="test/BUY/example.png",
        folder_label="BUY",
        result={
            "action": "HOLD",
            "trade_bias": "BUY",
            "decision_state": "UNCERTAIN",
            "execution_permission": "WAIT_FOR_CONFIRMATION",
            "confidence": 0.29,
            "projection_support": True,
            "projection_watch_ready": False,
            "chart_state": {
                "direction": "BUY",
                "direction_probability": 0.61,
                "structure_setup": "reversal_release",
                "structure_setup_source": "forecast",
                "projection_bias_direction": "BUY",
                "projection_bias_confidence": 0.62,
                "projection_dominance": 0.08,
            },
            "projection": {
                "direction": "BUY",
                "box_type": "reversal_base",
                "confidence": 0.62,
                "dominance": 0.08,
            },
        },
    )

    assert row["review_bucket"] == "ambiguous_wait"
    assert "aligned_projection_held" in row["review_reasons"]
    assert "trade_ready_structure_held" in row["review_reasons"]


def test_export_runtime_contradiction_queue_writes_sorted_review_rows(tmp_path: Path) -> None:
    split_root = tmp_path / "test"
    buy_dir = split_root / "BUY"
    sell_dir = split_root / "SELL"
    buy_dir.mkdir(parents=True, exist_ok=True)
    sell_dir.mkdir(parents=True, exist_ok=True)
    first = buy_dir / "a.png"
    second = sell_dir / "b.png"
    first.write_bytes(b"")
    second.write_bytes(b"")

    def _infer(path: Path) -> dict[str, object]:
        if path.name == "a.png":
            return {
                "action": "HOLD",
                "decision_state": "UNCERTAIN",
                "trade_bias": "BUY",
                "projection_support": True,
                "chart_state": {
                    "direction": "BUY",
                    "direction_probability": 0.60,
                    "structure_setup": "reversal_release",
                    "projection_bias_direction": "BUY",
                    "projection_bias_confidence": 0.60,
                    "projection_dominance": 0.07,
                },
                "projection": {"direction": "BUY", "box_type": "reversal_base", "confidence": 0.60, "dominance": 0.07},
            }
        return {
            "action": "BUY",
            "decision_state": "PROJECTED",
            "trade_bias": "BUY",
            "confidence": 0.72,
            "projection_support": True,
            "projection_watch_ready": True,
            "chart_state": {
                "direction": "BUY",
                "direction_probability": 0.68,
                "structure_setup": "reversal_release",
                "projection_bias_direction": "BUY",
                "projection_bias_confidence": 0.77,
                "projection_dominance": 0.15,
            },
            "projection": {"direction": "BUY", "box_type": "reversal_base", "confidence": 0.77, "dominance": 0.15},
        }

    output = tmp_path / "runtime_review.csv"
    stats = export_runtime_contradiction_queue(
        split_root=split_root,
        output_path=output,
        infer_fn=_infer,
    )

    assert stats["evaluated_count"] == 2
    assert stats["review_row_count"] == 2
    assert stats["bucket_counts"]["hard_negative"] == 1
    assert stats["bucket_counts"]["ambiguous_wait"] == 1

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["image_path"].endswith("b.png")
    assert rows[0]["review_bucket"] == "hard_negative"
    assert float(rows[0]["review_priority"]) >= float(rows[1]["review_priority"])


def test_export_runtime_contradiction_queue_keeps_running_when_inference_errors(tmp_path: Path) -> None:
    split_root = tmp_path / "test"
    sell_dir = split_root / "SELL"
    sell_dir.mkdir(parents=True, exist_ok=True)
    image_path = sell_dir / "broken.png"
    image_path.write_bytes(b"")

    def _infer(_path: Path) -> dict[str, object]:
        raise MemoryError("simulated failure")

    output = tmp_path / "runtime_review.csv"
    stats = export_runtime_contradiction_queue(
        split_root=split_root,
        output_path=output,
        infer_fn=_infer,
    )

    assert stats["evaluated_count"] == 1
    assert stats["review_row_count"] == 1
    assert stats["error_count"] == 1
    assert stats["bucket_counts"]["runtime_error"] == 1

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["review_bucket"] == "runtime_error"
    assert rows[0]["review_reasons"] == "runtime_error:MemoryError"


def test_export_runtime_contradiction_queue_accepts_direct_label_dir(tmp_path: Path) -> None:
    sell_dir = tmp_path / "SELL"
    sell_dir.mkdir(parents=True, exist_ok=True)
    image_path = sell_dir / "case.png"
    image_path.write_bytes(b"")

    def _infer(_path: Path) -> dict[str, object]:
        return {
            "action": "BUY",
            "decision_state": "PROJECTED",
            "trade_bias": "BUY",
            "confidence": 0.70,
            "projection_support": True,
            "projection_watch_ready": True,
            "chart_state": {
                "direction": "BUY",
                "direction_probability": 0.66,
                "structure_setup": "reversal_release",
                "projection_bias_direction": "BUY",
                "projection_bias_confidence": 0.73,
                "projection_dominance": 0.12,
            },
            "projection": {"direction": "BUY", "box_type": "reversal_base", "confidence": 0.73, "dominance": 0.12},
        }

    output = tmp_path / "direct_label_review.csv"
    stats = export_runtime_contradiction_queue(
        split_root=sell_dir,
        output_path=output,
        infer_fn=_infer,
    )

    assert stats["evaluated_count"] == 1
    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["trade_label"] == "SELL"
