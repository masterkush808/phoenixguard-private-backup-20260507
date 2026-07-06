from __future__ import annotations

from phoenixguard.decision.book_strategy import (
    BlockerClass,
    MaturityState,
    Side,
    blockers_from_payload,
    classify_blocker_code,
    decision_from_payload,
    split_blockers,
)


def test_book_strategy_contracts_normalize_legacy_master_payload() -> None:
    payload: dict[str, object] = {
        "schema_version": "PG_BOOK_STRATEGY_MASTER_V3",
        "maturity": "ENTER_NOW",
        "side": "bullish",
        "playbook_signal": "BUY",
        "playbook": "DEMAND_REJECTION",
        "headline": "BUY Demand Rejection",
        "confidence": "0.84",
        "next_required": "publish validated PG_EXECUTION_PACKET_V3 after hard runtime gates pass",
        "blockers": [
            {
                "field": "live_integrity",
                "received": {"is_live": False},
                "required": "fresh advancing live frame/capture/state",
                "reason": "Live source truth is not advancing cleanly.",
                "hard": True,
            },
            {
                "field": "timing_mode",
                "received": "WAIT_FOR_RETEST",
                "required": "ENTER_NOW",
                "reason": "timing contributor has not reached immediate-entry mode",
                "hard": False,
            },
        ],
        "evidence": {
            "playbook": "DEMAND_REJECTION",
            "entry_profile": "AGGRESSIVE_SNIPER",
            "strategy_combo": ["DEMAND_REJECTION", "CANDLE_CONFIRMATION_AT_ZONE"],
            "market_phase_v3": "BUY_TREND",
            "reaction_type": "WICK_REJECTION",
        },
    }

    decision = decision_from_payload(payload)

    assert decision.maturity is MaturityState.ENTER_NOW
    assert decision.side is Side.BUY
    assert decision.playbook_signal is Side.BUY
    assert decision.confidence == 0.84
    assert decision.evidence.market_phase == "BUY_TREND"
    assert decision.evidence.strategy_combo == ("DEMAND_REJECTION", "CANDLE_CONFIRMATION_AT_ZONE")
    assert decision.hard_blockers[0].blocker_class is BlockerClass.TRUE_HARD_BLOCKER
    assert decision.hard_blockers[0].can_block_package is True
    assert decision.blockers[1].blocker_class is BlockerClass.WAIT_STATE
    assert decision.blockers[1].can_block_package is False
    assert decision.to_payload()["maturity_state"] == "ENTER_NOW"


def test_book_strategy_blocker_taxonomy_splits_raw_payload_rows() -> None:
    blockers_payload: list[object] = [
        {"field": "bad_entry_filter.class", "reason": "bad entry", "hard": True},
        {"field": "current_candle.entry_allowed", "reason": "wait for candle", "hard": False},
        "study_only",
    ]

    blockers = blockers_from_payload(blockers_payload)
    split = split_blockers(blockers)

    assert classify_blocker_code("bad_entry_filter.class") is BlockerClass.STRATEGY_CAUTION
    assert classify_blocker_code("current_candle.entry_allowed") is BlockerClass.STRATEGY_CAUTION
    assert classify_blocker_code("study_only") is BlockerClass.DIAGNOSTIC_ONLY
    assert len(split.hard) == 1
    assert len(split.caution) == 1
    assert len(split.diagnostic) == 1
