from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from phoenixguard.decision.outcome_feedback_v3 import log_outcome_feedback
from phoenixguard.paths import PROJECT_ROOT


MISSED_OPPORTUNITY_LOGGER_VERSION = "PG_MISSED_OPPORTUNITY_LOGGER_V3"
DEFAULT_MISSED_OPPORTUNITY_LOG = PROJECT_ROOT / "data" / "missed_opportunity_v3.jsonl"


def log_missed_opportunity(
    candidate: Mapping[str, Any],
    outcome: Mapping[str, Any] | None = None,
    *,
    path: str | Path | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    record = log_outcome_feedback(
        candidate,
        outcome or {},
        path=path or DEFAULT_MISSED_OPPORTUNITY_LOG,
        event_type="missed_opportunity",
        now_epoch=now_epoch,
    )
    record["logger_version"] = MISSED_OPPORTUNITY_LOGGER_VERSION
    return record


__all__ = [
    "DEFAULT_MISSED_OPPORTUNITY_LOG",
    "MISSED_OPPORTUNITY_LOGGER_VERSION",
    "log_missed_opportunity",
]
