from __future__ import annotations

import sys
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.vision.cv_reasoning import (
    CVReasoningTrace,
    MarketState,
    normalize_transition_probabilities,
    validate_market_state,
)


def test_market_state_validation_accepts_taxonomy_values() -> None:
    state = MarketState(
        macro_trend="BEAR",
        local_phase="counter_trend_pullback",
        phase_risk="exhaustion_risk",
        intent_next="continue",
        control_state="counter_trend",
        control_strength_delta=0.34,
        conflict_type="healthy_pullback",
        time_to_resolution_candles=2,
    )

    validate_market_state(state)


def test_market_state_validation_accepts_managed_counter_trend() -> None:
    state = MarketState(
        macro_trend="BULL",
        local_phase="continuation_base",
        phase_risk="managed_counter_trend",
        intent_next="continue",
        control_state="transition",
        control_strength_delta=0.28,
        conflict_type="healthy_pullback",
        time_to_resolution_candles=2,
    )

    validate_market_state(state)


def test_market_state_validation_rejects_invalid_value() -> None:
    state = MarketState(
        macro_trend="BULL",
        local_phase="with_trend_push",
        phase_risk="breakout_risk",
        intent_next="continue",
        control_state="with_trend",
        control_strength_delta=0.12,
    )
    state.local_phase = "invalid_phase"  # type: ignore[assignment]

    with pytest.raises(ValueError):
        validate_market_state(state)


def test_transition_probabilities_are_normalized() -> None:
    probs = normalize_transition_probabilities(
        {
            "continue": 0.6,
            "pullback": 0.2,
            "reversal_attempt": 0.1,
            "fakeout": 0.1,
        }
    )

    total = (
        probs["continue_prob"]
        + probs["pullback_prob"]
        + probs["reversal_attempt_prob"]
        + probs["fakeout_prob"]
    )
    assert abs(total - 1.0) < 1e-6


def test_reasoning_trace_serializes() -> None:
    state = MarketState(
        macro_trend="BEAR",
        local_phase="counter_trend_pullback",
        phase_risk="chop_risk",
        intent_next="pullback",
        control_state="counter_trend",
        control_strength_delta=0.05,
        conflict_type="noise_conflict",
        time_to_resolution_candles=1,
    )
    trace = CVReasoningTrace(
        market_state=state,
        transition_probabilities={
            "continue_prob": 0.25,
            "pullback_prob": 0.25,
            "reversal_attempt_prob": 0.25,
            "fakeout_prob": 0.25,
        },
        episode_matches=[],
        final_trade_bias="SELL",
        explanation="test",
    )
    payload = trace.to_dict()

    assert payload["market_state"]["macro_trend"] == "BEAR"
    assert payload["final_trade_bias"] == "SELL"
