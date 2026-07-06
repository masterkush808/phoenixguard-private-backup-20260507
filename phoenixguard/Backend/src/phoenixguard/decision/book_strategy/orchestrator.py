from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from phoenixguard.decision.book_strategy.contracts import decision_from_payload
from phoenixguard.decision.book_strategy.legacy_engine import (
    BOOK_ENTRY_PROFILES,
    BOOK_REACTION_TYPES,
    BOOK_STRATEGY_EXECUTION_AUTHORITY,
    BOOK_STRATEGY_MATURITY_STATES,
    BOOK_STRATEGY_PLAYBOOKS,
    BOOK_STRATEGY_SCHEMA_VERSION,
    MARKET_PHASES_V3,
    MODEL_COUNCIL_CONTRIBUTOR_ROLE,
    evaluate_book_strategy_master_v3 as _evaluate_legacy_book_strategy_master_v3,
)


def evaluate_book_strategy_master_v3(
    snapshot: Mapping[str, Any],
    *,
    market: Mapping[str, Any],
    candidate_side: str | None,
    execution_lane: Mapping[str, Any],
    timing_decision: Mapping[str, Any],
    current_candle: Mapping[str, Any],
    timing_mode: str,
    final_score_passed: bool,
    timing_enter_now: bool,
    lane_score: float,
    lane_required_score: float,
    bad_entry_filter: Mapping[str, Any] | None = None,
    bad_entry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility-safe public orchestrator for the Book Strategy Master.

    The legacy decision engine is frozen behind this boundary while typed
    contracts and diagnostics are strangled into smaller modules. The returned
    payload stays backward-compatible for ModelCouncilV3.
    """
    normalized_candidate_side = candidate_side or ""
    payload = _evaluate_legacy_book_strategy_master_v3(
        snapshot,
        market=market,
        candidate_side=normalized_candidate_side,
        execution_lane=execution_lane,
        timing_decision=timing_decision,
        current_candle=current_candle,
        timing_mode=timing_mode,
        final_score_passed=final_score_passed,
        timing_enter_now=timing_enter_now,
        lane_score=lane_score,
        lane_required_score=lane_required_score,
        bad_entry_filter=bad_entry_filter,
        bad_entry=bad_entry,
    )
    decision = decision_from_payload(payload)
    payload["typed_contract_schema_version"] = decision.schema_version
    payload["typed_decision"] = decision.to_payload()
    return payload


__all__ = [
    "BOOK_ENTRY_PROFILES",
    "BOOK_REACTION_TYPES",
    "BOOK_STRATEGY_EXECUTION_AUTHORITY",
    "BOOK_STRATEGY_MATURITY_STATES",
    "BOOK_STRATEGY_PLAYBOOKS",
    "BOOK_STRATEGY_SCHEMA_VERSION",
    "MARKET_PHASES_V3",
    "MODEL_COUNCIL_CONTRIBUTOR_ROLE",
    "evaluate_book_strategy_master_v3",
]
