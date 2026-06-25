from __future__ import annotations

from typing import Any, Mapping, Sequence


def expected_decisions_from_labels(labels: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for label in labels:
        frame_id = str(label.get("frame_id") or label.get("label_id") or "")
        if not frame_id:
            continue
        action = str(label.get("target_action") or "HOLD").upper()
        decisions[frame_id] = {
            "expected": {
                "dominant_side": action if action in {"BUY", "SELL"} else "HOLD",
                "execution_state": "EXECUTABLE" if bool(label.get("trade_allowed")) else "WATCHING",
                "entry_quality": "GOOD_ENTRY" if bool(label.get("trade_allowed")) else "BAD_NOW",
            }
        }
    return decisions


__all__ = ["expected_decisions_from_labels"]
