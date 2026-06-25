from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from phoenixguard.decision.outcome_feedback_v3 import log_outcome_feedback
from phoenixguard.paths import PROJECT_ROOT


TRADE_CANDIDATE_JOURNAL_VERSION = "PG_TRADE_CANDIDATE_JOURNAL_V3"
DEFAULT_TRADE_CANDIDATE_JOURNAL = PROJECT_ROOT / "data" / "trade_candidate_journal_v3.jsonl"


def journal_trade_candidate(
    candidate: Mapping[str, Any],
    *,
    path: str | Path | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    record = log_outcome_feedback(
        candidate,
        {},
        path=path or DEFAULT_TRADE_CANDIDATE_JOURNAL,
        event_type="trade_candidate",
        now_epoch=now_epoch,
    )
    record["journal_version"] = TRADE_CANDIDATE_JOURNAL_VERSION
    return record


__all__ = [
    "DEFAULT_TRADE_CANDIDATE_JOURNAL",
    "TRADE_CANDIDATE_JOURNAL_VERSION",
    "journal_trade_candidate",
]
