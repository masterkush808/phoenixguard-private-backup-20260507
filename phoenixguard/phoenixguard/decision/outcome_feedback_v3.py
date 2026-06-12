from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from phoenixguard.decision.pair_behavior_profile_v3 import update_pair_profile_from_outcome
from phoenixguard.paths import PROJECT_ROOT


OUTCOME_FEEDBACK_VERSION = "PG_OUTCOME_FEEDBACK_V3"
DEFAULT_OUTCOME_LOG = PROJECT_ROOT / "data" / "outcome_feedback_v3.jsonl"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def build_outcome_record(
    candidate: Mapping[str, Any],
    outcome: Mapping[str, Any] | None = None,
    *,
    event_type: str = "candidate_outcome",
    now_epoch: float | None = None,
) -> dict[str, Any]:
    source = dict(candidate)
    result = dict(outcome or {})
    memory = result.get("memory_matches", source.get("memory_matches", source.get("memory_confirmation", [])))
    if isinstance(memory, Mapping):
        memory_matches = [str(item.get("memory_id") or item.get("entry_id") or item) for item in [memory]]
    elif isinstance(memory, list):
        memory_matches = [
            str(_mapping(item).get("memory_id") or _mapping(item).get("entry_id") or item)
            for item in memory
        ]
    else:
        memory_matches = []
    return {
        "version": OUTCOME_FEEDBACK_VERSION,
        "event_type": str(event_type),
        "ts_epoch": float(time.time() if now_epoch is None else now_epoch),
        "candidate_id": str(source.get("candidate_id") or result.get("candidate_id") or ""),
        "play": str(source.get("play") or source.get("primary_play") or result.get("play") or ""),
        "decision": str(source.get("decision") or source.get("state") or result.get("decision") or ""),
        "side": str(source.get("side") or result.get("side") or "").upper(),
        "entry_location": str(source.get("entry_location") or source.get("price_location") or result.get("entry_location") or ""),
        "timing_mode": str(source.get("timing_mode") or result.get("timing_mode") or ""),
        "memory_matches": memory_matches,
        "result_after_1_candle": str(result.get("result_after_1_candle") or ""),
        "result_after_2_candles": str(result.get("result_after_2_candles") or ""),
        "max_adverse_excursion": _float(result.get("max_adverse_excursion"), 0.0),
        "max_favourable_excursion": _float(result.get("max_favourable_excursion"), 0.0),
        "lesson": str(result.get("lesson") or ""),
    }


def log_outcome_feedback(
    candidate: Mapping[str, Any],
    outcome: Mapping[str, Any] | None = None,
    *,
    path: str | Path | None = None,
    event_type: str = "candidate_outcome",
    now_epoch: float | None = None,
) -> dict[str, Any]:
    record = build_outcome_record(candidate, outcome, event_type=event_type, now_epoch=now_epoch)
    target = Path(path) if path is not None else DEFAULT_OUTCOME_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    return record


def apply_outcome_to_pair_profile(pair_profile: Mapping[str, Any], outcome: Mapping[str, Any]) -> dict[str, Any]:
    return update_pair_profile_from_outcome(pair_profile, outcome)


__all__ = [
    "DEFAULT_OUTCOME_LOG",
    "OUTCOME_FEEDBACK_VERSION",
    "apply_outcome_to_pair_profile",
    "build_outcome_record",
    "log_outcome_feedback",
]
