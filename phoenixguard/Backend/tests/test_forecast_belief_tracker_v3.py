from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from phoenixguard.decision.forecast_belief_tracker_v3 import (
    FORECAST_BELIEF_STATE_SCHEMA_V3,
    ForecastBeliefConfigV3,
    ForecastBeliefTrackerV3,
    normalize_calibrated_emissions_v3,
)


def _config(**overrides: Any) -> ForecastBeliefConfigV3:
    values: dict[str, Any] = {
        "direction_stay_probability": 0.72,
        "hold_stay_probability": 0.64,
        "minimum_stay_probability": 0.30,
        "maximum_stay_probability": 0.96,
        "adaptive_stickiness_strength": 0.30,
        "opposite_transition_share": 0.40,
        "activation_posterior_threshold": 0.52,
        "activation_margin_threshold": 0.04,
        "hold_posterior_threshold": 0.54,
        "hold_margin_threshold": 0.04,
        "reversal_posterior_threshold": 0.56,
        "reversal_margin_threshold": 0.08,
        "reacquire_confirmation_events": 2,
        "reversal_confirmation_events": 2,
        "hold_confirmation_events": 2,
        "maximum_contiguous_event_gap": 1,
    }
    values.update(overrides)
    return ForecastBeliefConfigV3(**values)


def _update(
    tracker: ForecastBeliefTrackerV3,
    *,
    sequence: int,
    frame: int,
    side: str,
    pair: str = "NZD/USD",
    timeframe: str = "M1",
):
    emissions = {
        "BUY": 0.99 if side == "BUY" else 0.005,
        "HOLD": 0.99 if side == "HOLD" else 0.005,
        "SELL": 0.99 if side == "SELL" else 0.005,
    }
    return tracker.update(
        pair=pair,
        timeframe=timeframe,
        closed_candle_key=f"closed-{sequence}",
        closed_candle_sequence=sequence,
        frame_id=frame,
        emissions=emissions,
        calibrated=True,
        observed_at_epoch=1_700_000_000.0 + sequence,
        source_id="test-forecaster",
    )


def test_emissions_are_normalized_in_buy_hold_sell_order_and_require_calibration() -> None:
    config = _config()
    normalized = normalize_calibrated_emissions_v3(
        {"sell": 1.0, "buy": 6.0, "hold": 3.0},
        config=config,
    )
    assert math.isclose(sum(normalized), 1.0)
    assert all(
        math.isclose(actual, expected)
        for actual, expected in zip(normalized, (0.6, 0.3, 0.1), strict=True)
    )

    with pytest.raises(ValueError, match="missing required sides"):
        normalize_calibrated_emissions_v3(
            {"BUY": 0.6, "SELL": 0.4},
            config=config,
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        normalize_calibrated_emissions_v3(
            {"BUY": 0.6, "HOLD": -0.1, "SELL": 0.5},
            config=config,
        )

    tracker = ForecastBeliefTrackerV3(config)
    rejected = tracker.update(
        pair="NZD/USD",
        timeframe="M1",
        closed_candle_key="closed-1",
        closed_candle_sequence=1,
        frame_id=1,
        emissions={"BUY": 0.8, "HOLD": 0.1, "SELL": 0.1},
        calibrated=False,
    )
    assert rejected.accepted is False
    assert rejected.reason == "UNCALIBRATED_EMISSIONS"
    assert tracker.snapshot(pair="NZD/USD", timeframe="M1")["revision"] == 0


def test_reacquisition_requires_distinct_closed_candle_events() -> None:
    tracker = ForecastBeliefTrackerV3(_config())

    first = _update(tracker, sequence=1, frame=10, side="BUY")
    assert first.accepted is True
    assert first.status == "REACQUIRING"
    assert first.active_side == "HOLD"
    assert first.pending_side == "BUY"
    assert first.pending_count == 1

    duplicate = tracker.update(
        pair="NZD/USD",
        timeframe="M1",
        closed_candle_key="closed-1",
        closed_candle_sequence=1,
        frame_id=11,
        emissions={"BUY": 0.999, "HOLD": 0.0005, "SELL": 0.0005},
        calibrated=True,
    )
    assert duplicate.accepted is False
    assert duplicate.reason == "DUPLICATE_CLOSED_CANDLE"
    assert duplicate.pending_count == 1
    assert duplicate.revision == first.revision

    second = _update(tracker, sequence=2, frame=12, side="BUY")
    assert second.status == "STABLE"
    assert second.active_side == "BUY"
    assert second.reason == "REACQUISITION_CONFIRMED"
    assert second.pending_count == 0


def test_opposite_side_is_pending_until_two_consecutive_distinct_events() -> None:
    tracker = ForecastBeliefTrackerV3(_config())
    _update(tracker, sequence=1, frame=1, side="BUY")
    _update(tracker, sequence=2, frame=2, side="BUY")

    pending = _update(tracker, sequence=3, frame=3, side="SELL")
    assert pending.status == "REVERSAL_PENDING"
    assert pending.active_side == "BUY"
    assert pending.pending_side == "SELL"
    assert pending.pending_count == 1
    assert pending.required_count == 2

    duplicate = tracker.update(
        pair="NZD/USD",
        timeframe="M1",
        closed_candle_key="closed-3",
        closed_candle_sequence=3,
        frame_id=4,
        emissions={"BUY": 0.001, "HOLD": 0.001, "SELL": 0.998},
        calibrated=True,
    )
    assert duplicate.accepted is False
    assert duplicate.pending_count == 1

    confirmed = _update(tracker, sequence=4, frame=5, side="SELL")
    assert confirmed.status == "STABLE"
    assert confirmed.active_side == "SELL"
    assert confirmed.reason == "REVERSAL_CONFIRMED"
    assert confirmed.pending_side == ""


def test_reversal_confirmation_is_consecutive_not_accumulated() -> None:
    tracker = ForecastBeliefTrackerV3(_config())
    _update(tracker, sequence=1, frame=1, side="BUY")
    _update(tracker, sequence=2, frame=2, side="BUY")
    first_sell = _update(tracker, sequence=3, frame=3, side="SELL")
    assert first_sell.status == "REVERSAL_PENDING"

    recovery = _update(tracker, sequence=4, frame=4, side="BUY")
    assert recovery.status == "STABLE"
    assert recovery.active_side == "BUY"
    assert recovery.pending_count == 0

    second_sell = _update(tracker, sequence=5, frame=5, side="SELL")
    assert second_sell.status == "REVERSAL_PENDING"
    assert second_sell.active_side == "BUY"
    assert second_sell.pending_count == 1


def test_duplicate_out_of_order_and_replayed_keys_do_not_mutate_belief() -> None:
    tracker = ForecastBeliefTrackerV3(_config())
    accepted = tracker.update(
        pair="EUR/USD",
        timeframe="M5",
        closed_candle_key="event-ten",
        closed_candle_sequence=10,
        frame_id=100,
        emissions={"BUY": 0.9, "HOLD": 0.05, "SELL": 0.05},
        calibrated=True,
    )
    baseline = tracker.snapshot(pair="EUR/USD", timeframe="M5")

    attempts = (
        ("event-ten", 10, 100, "DUPLICATE_FRAME"),
        ("event-ten", 10, 101, "DUPLICATE_CLOSED_CANDLE"),
        ("event-nine", 9, 102, "OUT_OF_ORDER_CLOSED_CANDLE"),
        ("event-eleven", 11, 99, "OUT_OF_ORDER_FRAME"),
        ("event-ten", 11, 103, "REPLAYED_CLOSED_CANDLE_KEY"),
    )
    for key, sequence, frame, expected_reason in attempts:
        result = tracker.update(
            pair="EUR/USD",
            timeframe="M5",
            closed_candle_key=key,
            closed_candle_sequence=sequence,
            frame_id=frame,
            emissions={"BUY": 0.01, "HOLD": 0.01, "SELL": 0.98},
            calibrated=True,
        )
        assert result.accepted is False
        assert result.reason == expected_reason
        assert result.revision == accepted.revision

    assert tracker.snapshot(pair="EUR/USD", timeframe="M5") == baseline
    assert len(tracker.records(pair="EUR/USD", timeframe="M5")) == 1


def test_explicit_reset_and_event_gap_reenter_reacquisition() -> None:
    tracker = ForecastBeliefTrackerV3(_config())
    _update(tracker, sequence=1, frame=1, side="BUY")
    _update(tracker, sequence=2, frame=2, side="BUY")

    reset = tracker.reset(
        pair="NZD/USD",
        timeframe="M1",
        reason="PAIR_BINDING_CHANGED",
        observed_at_epoch=1_700_000_050.0,
        frame_id=50,
    )
    assert reset.status == "RESET"
    assert reset.active_side == "HOLD"
    assert reset.record is not None
    assert reset.record.event_type == "RESET"
    assert reset.record.reset_reason == "PAIR_BINDING_CHANGED"

    reacquiring = _update(tracker, sequence=10, frame=51, side="SELL")
    assert reacquiring.status == "REACQUIRING"
    assert reacquiring.active_side == "HOLD"

    acquired = _update(tracker, sequence=11, frame=52, side="SELL")
    assert acquired.active_side == "SELL"
    gap = _update(tracker, sequence=14, frame=53, side="BUY")
    assert gap.status == "REACQUIRING"
    assert gap.active_side == "HOLD"
    assert gap.reason == "EVENT_GAP_REACQUIRING"
    assert gap.record is not None
    assert gap.record.reset_reason == "CLOSED_CANDLE_EVENT_GAP"
    assert [record.event_type for record in tracker.records(pair="NZD/USD", timeframe="M1")][-2:] == [
        "RESET",
        "UPDATE",
    ]


def test_contexts_are_isolated_by_pair_and_timeframe() -> None:
    tracker = ForecastBeliefTrackerV3(_config())
    _update(tracker, sequence=1, frame=1, side="BUY", pair="EUR/USD", timeframe="M1")
    _update(tracker, sequence=2, frame=2, side="BUY", pair="EUR/USD", timeframe="M1")
    _update(tracker, sequence=1, frame=1, side="SELL", pair="EUR/USD", timeframe="M5")
    _update(tracker, sequence=2, frame=2, side="SELL", pair="EUR/USD", timeframe="M5")
    _update(tracker, sequence=1, frame=1, side="HOLD", pair="GBP/USD", timeframe="M1")
    _update(tracker, sequence=2, frame=2, side="HOLD", pair="GBP/USD", timeframe="M1")

    assert tracker.snapshot(pair="EUR/USD", timeframe="M1")["active_side"] == "BUY"
    assert tracker.snapshot(pair="EUR/USD", timeframe="M5")["active_side"] == "SELL"
    assert tracker.snapshot(pair="GBP/USD", timeframe="M1")["active_side"] == "HOLD"
    stable_hold = _update(
        tracker,
        sequence=3,
        frame=3,
        side="HOLD",
        pair="GBP/USD",
        timeframe="M1",
    )
    assert stable_hold.status == "STABLE"
    assert stable_hold.active_side == "HOLD"
    assert stable_hold.reason == "BELIEF_STABLE"
    assert len(tracker.to_state_dict()["contexts"]) == 3


def test_revision_records_are_frozen_and_state_round_trips_as_strict_json() -> None:
    config = _config()
    tracker = ForecastBeliefTrackerV3(config)
    _update(tracker, sequence=1, frame=1, side="BUY")
    _update(tracker, sequence=2, frame=2, side="BUY")
    _update(tracker, sequence=3, frame=3, side="SELL")

    records = tracker.records(pair="NZD/USD", timeframe="M1")
    assert isinstance(records, tuple)
    with pytest.raises(FrozenInstanceError):
        setattr(records[-1], "status", "STABLE")

    encoded = tracker.to_json()
    raw = json.loads(encoded)
    assert raw["schema_version"] == FORECAST_BELIEF_STATE_SCHEMA_V3
    restored = ForecastBeliefTrackerV3.from_json(encoded, config=config)
    assert restored.to_state_dict() == tracker.to_state_dict()
    assert json.dumps(restored.to_state_dict(), allow_nan=False)

    confirmed = _update(restored, sequence=4, frame=4, side="SELL")
    assert confirmed.reason == "REVERSAL_CONFIRMED"
    assert confirmed.active_side == "SELL"
