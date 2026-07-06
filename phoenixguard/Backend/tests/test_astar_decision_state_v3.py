from __future__ import annotations

from typing import cast

from phoenixguard.decision.astar_decision_state_v3 import (
    BlockerTaxonomyV3,
    ConfirmationEventV3,
    InteractionStateV3,
    LiveThesisStateV3,
    MarketLocationV3,
    MidRangeDecisionDisciplineV3,
    PullbackPhaseV3,
    build_authorization_survival_trace_v3,
    build_candidate_decision_ledger_v3,
    derive_live_thesis_state_v3,
    derive_pullback_phase_v3,
    evaluate_interaction_state_v3,
    evaluate_mid_range_decision_discipline_v3,
    evaluate_support_resistance_interaction_v3,
)


def _string_list(value: object) -> list[str]:
    return cast(list[str], value)


def _dict_list(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def test_pullback_reclaimed_can_reach_enter_now() -> None:
    snapshot: dict[str, object] = {
        "candidate_side": "BUY",
        "timing_mode": "WAIT_FOR_PULLBACK",
        "pullback_reclaimed": True,
        "current_candle_accepted": True,
    }

    ledger = build_candidate_decision_ledger_v3(snapshot)

    assert derive_pullback_phase_v3(snapshot) == PullbackPhaseV3.PULLBACK_RECLAIMED
    assert derive_live_thesis_state_v3(snapshot) == LiveThesisStateV3.ENTER_NOW
    assert ledger["final_state"] == LiveThesisStateV3.ENTER_NOW.value
    assert ledger["decision_allowed"] is True
    assert ConfirmationEventV3.PULLBACK_RECLAIMED.value in _string_list(ledger["confirmation_events"])


def test_wait_for_pullback_does_not_block_after_pullback_held() -> None:
    snapshot: dict[str, object] = {
        "candidate_side": "SELL",
        "timing_mode": "WAIT_FOR_PULLBACK",
        "pullback_held": True,
        "current_candle_entry_allowed": True,
    }

    ledger = build_candidate_decision_ledger_v3(snapshot)

    assert ledger["pullback_phase"] == PullbackPhaseV3.PULLBACK_HELD.value
    assert ledger["final_state"] == LiveThesisStateV3.ENTER_NOW.value
    assert "WAIT_FOR_PULLBACK" not in _string_list(ledger["blocker_codes"])


def test_resistance_accepted_above_flips_to_buy_evidence() -> None:
    interaction = evaluate_interaction_state_v3(
        {
            "zone_role": "resistance",
            "accepted_above": True,
        }
    )

    assert interaction["interaction_state"] == InteractionStateV3.RESISTANCE_ACCEPTED_ABOVE.value
    assert interaction["evidence_side"] == "BUY"
    assert interaction["role_flip"] is True
    assert interaction["confirmation_event"] == ConfirmationEventV3.RESISTANCE_ACCEPTED_ABOVE.value


def test_support_rejected_flips_to_sell_evidence() -> None:
    interaction = evaluate_support_resistance_interaction_v3(
        {
            "zone_role": "support",
            "support_rejected": True,
        }
    )

    assert interaction["interaction_state"] == InteractionStateV3.SUPPORT_REJECTED.value
    assert interaction["evidence_side"] == "SELL"
    assert interaction["role_flip"] is True
    assert interaction["confirmation_event"] == ConfirmationEventV3.SUPPORT_REJECTED.value


def test_soft_warning_does_not_silently_veto_enter_now() -> None:
    snapshot: dict[str, object] = {
        "candidate_side": "BUY",
        "timing_mode": "ENTER_NOW",
        "current_candle_accepted": True,
        "soft_warnings": [
            {
                "field": "late_chase",
                "effect": "observe_pullback_risk",
                "hard": False,
            }
        ],
    }

    ledger = build_candidate_decision_ledger_v3(snapshot)
    hard_blockers = _dict_list(ledger["hard_blockers"])
    soft_warnings = _dict_list(ledger["soft_warnings"])

    assert ledger["final_state"] == LiveThesisStateV3.ENTER_NOW.value
    assert ledger["decision_allowed"] is True
    assert hard_blockers == []
    assert soft_warnings
    assert soft_warnings[0]["taxonomy"] == BlockerTaxonomyV3.SOFT_WARNING.value


def test_hard_runtime_failure_still_blocks() -> None:
    snapshot: dict[str, object] = {
        "candidate_side": "BUY",
        "timing_mode": "ENTER_NOW",
        "runtime_pass": False,
        "current_candle_accepted": True,
    }

    ledger = build_candidate_decision_ledger_v3(snapshot)
    hard_blockers = _dict_list(ledger["hard_blockers"])

    assert ledger["final_state"] == LiveThesisStateV3.BLOCKED_BY_RUNTIME.value
    assert ledger["decision_allowed"] is False
    assert hard_blockers[0]["taxonomy"] == BlockerTaxonomyV3.HARD_RUNTIME_FAILURE.value
    assert "RUNTIME_INTEGRITY_FAILED" in _string_list(ledger["blocker_codes"])


def test_enter_now_survival_trace_names_downgrade_layer() -> None:
    trace = build_authorization_survival_trace_v3(
        {
            "candidate_side": "BUY",
            "requested_state": "ENTER_NOW",
            "market_location": "MID_RANGE",
            "confirmation_score": 0.41,
        }
    )

    assert trace["final_state"] == LiveThesisStateV3.PREPARING.value
    assert trace["survived_enter_now"] is False
    assert trace["downgrade_layer"] == "MID_RANGE_DECISION_DISCIPLINE"
    assert "downgrade:MID_RANGE_DECISION_DISCIPLINE" in _string_list(trace["trace_steps"])


def test_mid_range_requires_stronger_confirmation() -> None:
    weak = evaluate_mid_range_decision_discipline_v3(
        {
            "market_location": "MID_RANGE",
            "confirmation_score": 0.61,
        }
    )
    strong = evaluate_mid_range_decision_discipline_v3(
        {
            "market_location": MarketLocationV3.MID_RANGE.value,
            "confirmation_score": 0.73,
        }
    )

    assert weak["market_location"] == MarketLocationV3.MID_RANGE.value
    assert weak["discipline"] == MidRangeDecisionDisciplineV3.BLOCKED_WEAK_CONFIRMATION.value
    assert weak["blocked"] is True
    assert weak["blocker"] == "MID_RANGE_NEEDS_STRONG_CONFIRMATION"
    assert strong["discipline"] == MidRangeDecisionDisciplineV3.CONFIRMED.value
    assert strong["blocked"] is False
