from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Iterable, Mapping

from phoenixguard.decision.model_council_v3 import ModelCouncilV3

from .agent_vote_recorder import record_agent_votes
from .maturity_stage_evaluator import evaluate_maturity_stage


CouncilCallable = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _state(payload: Mapping[str, Any]) -> str:
    council = _mapping(payload.get("model_council"))
    execution = _mapping(payload.get("execution"))
    return str(council.get("final_state") or execution.get("state") or payload.get("final_state") or "WATCHING").upper()


def _expected(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    expected = _mapping(snapshot.get("expected"))
    nested = _mapping(expected.get("expected"))
    merged = {**nested, **expected}
    return merged


@dataclass
class CouncilReplayEngine:
    council: Any = field(default_factory=ModelCouncilV3)
    evaluator: CouncilCallable | None = None

    def evaluate(self, snapshot: Mapping[str, Any], *, now_epoch: float | None = None) -> dict[str, Any]:
        if self.evaluator is not None:
            result = dict(self.evaluator(snapshot))
        elif hasattr(self.council, "evaluate"):
            result = dict(self.council.evaluate(snapshot, now_epoch=now_epoch if now_epoch is not None else time.time()))
        elif callable(self.council):
            result = dict(self.council(snapshot))
        else:
            raise TypeError("CouncilReplayEngine requires a council with evaluate() or a callable evaluator.")
        votes = record_agent_votes(snapshot, result)
        maturity = evaluate_maturity_stage(result)
        expected = _expected(snapshot)
        expected_state = str(expected.get("execution_state") or expected.get("final_state") or "").upper()
        actual_state = _state(result)
        return {
            "frame_id": snapshot.get("frame_id"),
            "scenario_name": snapshot.get("scenario_name"),
            "actual_state": actual_state,
            "expected_state": expected_state,
            "correct": not expected_state or actual_state == expected_state or actual_state.endswith(expected_state),
            "votes": votes,
            "maturity": maturity,
            "result": result,
        }

    def replay(self, snapshots: Iterable[Mapping[str, Any]], *, now_epoch: float | None = None) -> dict[str, Any]:
        rows = [self.evaluate(snapshot, now_epoch=now_epoch) for snapshot in snapshots]
        states = Counter(str(row["actual_state"]) for row in rows)
        incorrect = [row for row in rows if not row["correct"]]
        return {
            "frames_processed": len(rows),
            "correct": len(incorrect) == 0,
            "state_distribution": dict(states),
            "incorrect_count": len(incorrect),
            "incorrect_frames": [row.get("frame_id") for row in incorrect],
            "records": rows,
        }
