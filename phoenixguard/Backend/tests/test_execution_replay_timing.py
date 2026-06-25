from __future__ import annotations

import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.execution.timing import TimingProfile, TimingWindow, build_timing_profile, profile_key
from Backend.scripts_runtime.replay_signals import evaluate_replay_event, evaluate_replay_events


def _profiles() -> dict[str, TimingProfile]:
    continuation = build_timing_profile(
        symbol="EURUSD",
        timeframe="M1",
        setup_type="continuation",
        historical_durations_seconds=[90, 120, 150, 180],
        safe_expiry_range_seconds=(120, 300),
        best_historical_expiry_seconds=180,
        late_entry_threshold_seconds=210,
        fakeout_window_seconds=(0, 45),
        reversal_window_seconds=(45, 105),
        continuation_window_seconds=(60, 210),
    )
    reversal = build_timing_profile(
        symbol="GBPUSD",
        timeframe="M1",
        setup_type="reversal",
        historical_durations_seconds=[120, 150, 180, 210],
        safe_expiry_range_seconds=(180, 420),
        best_historical_expiry_seconds=300,
        late_entry_threshold_seconds=260,
        fakeout_window_seconds=(0, 60),
        reversal_window_seconds=(90, 260),
        continuation_window_seconds=(150, 300),
    )
    fakeout = TimingProfile(
        symbol="USDJPY",
        timeframe="M1",
        setup_type="fakeout",
        average_setup_duration_seconds=90,
        median_setup_duration_seconds=75,
        min_safe_expiry_seconds=60,
        max_safe_expiry_seconds=180,
        best_historical_expiry_seconds=90,
        late_entry_threshold_seconds=120,
        fakeout_window=TimingWindow(0, 90),
        reversal_window=TimingWindow(60, 150),
        continuation_window=TimingWindow(90, 180),
    )
    return {profile.key: profile for profile in (continuation, reversal, fakeout)}


def test_pair_specific_profile_has_expected_duration_intelligence() -> None:
    profile = _profiles()[profile_key("EURUSD", "M1", "continuation")]

    assert profile.average_setup_duration_seconds == 135
    assert profile.median_setup_duration_seconds == 135
    assert profile.min_safe_expiry_seconds == 120
    assert profile.max_safe_expiry_seconds == 300
    assert profile.best_historical_expiry_seconds == 180
    assert profile.late_entry_threshold_seconds == 210
    assert profile.continuation_window == TimingWindow(60, 210)


def test_replay_accepts_known_winning_buy_continuation() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "win-buy-continuation",
            "symbol": "EURUSD",
            "timeframe": "M1",
            "setup_type": "continuation",
            "execution_action": "BUY",
            "entry_age_seconds": 120,
            "expiry_seconds": 180,
            "outcome": "win",
        },
        _profiles(),
    )

    assert result.accepted is True
    assert result.reason_codes == ()
    assert result.recommended_expiry_seconds == 180


def test_replay_rejects_losing_late_buy() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "loss-late-buy",
            "symbol": "EURUSD",
            "timeframe": "M1",
            "setup_type": "continuation",
            "execution_action": "BUY",
            "entry_age_seconds": 240,
            "expiry_seconds": 180,
            "outcome": "loss",
        },
        _profiles(),
    )

    assert result.accepted is False
    assert "late_entry" in result.reason_codes
    assert "known_losing_replay" in result.reason_codes


def test_replay_accepts_winning_sell_reversal() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "win-sell-reversal",
            "symbol": "GBPUSD",
            "timeframe": "M1",
            "setup_type": "reversal",
            "execution_action": "SELL",
            "entry_age_seconds": 150,
            "expiry_seconds": 300,
            "support_proximity": 0.20,
            "outcome": "win",
        },
        _profiles(),
    )

    assert result.accepted is True
    assert result.reason_codes == ()
    assert result.recommended_expiry_seconds == 300


def test_replay_rejects_losing_sell_into_support() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "loss-sell-support",
            "symbol": "GBPUSD",
            "timeframe": "M1",
            "setup_type": "reversal",
            "execution_action": "SELL",
            "entry_age_seconds": 150,
            "expiry_seconds": 300,
            "support_proximity": 0.92,
            "outcome": "loss",
        },
        _profiles(),
    )

    assert result.accepted is False
    assert "sell_into_support" in result.reason_codes
    assert "known_losing_replay" in result.reason_codes


def test_replay_rejects_fakeout_window() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "fakeout",
            "symbol": "USDJPY",
            "timeframe": "M1",
            "setup_type": "fakeout",
            "execution_action": "BUY",
            "entry_age_seconds": 45,
            "expiry_seconds": 90,
            "fakeout_probability": 0.82,
        },
        _profiles(),
    )

    assert result.accepted is False
    assert "fakeout_window" in result.reason_codes
    assert "fakeout_risk" in result.reason_codes


def test_replay_rejects_reversal_that_needs_wait() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "reversal-too-early",
            "symbol": "GBPUSD",
            "timeframe": "M1",
            "setup_type": "reversal",
            "execution_action": "SELL",
            "entry_age_seconds": 60,
            "expiry_seconds": 300,
        },
        _profiles(),
    )

    assert result.accepted is False
    assert result.reason_codes == ("reversal_needs_wait",)


def test_replay_rejects_continuation_expiry_too_early() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "continuation-expiry-too-early",
            "symbol": "EURUSD",
            "timeframe": "M1",
            "setup_type": "continuation",
            "execution_action": "BUY",
            "entry_age_seconds": 120,
            "expiry_seconds": 60,
        },
        _profiles(),
    )

    assert result.accepted is False
    assert result.reason_codes == ("expiry_too_early",)


def test_replay_rejects_continuation_expiry_too_late() -> None:
    result = evaluate_replay_event(
        {
            "signal_id": "continuation-expiry-too-late",
            "symbol": "EURUSD",
            "timeframe": "M1",
            "setup_type": "continuation",
            "execution_action": "BUY",
            "entry_age_seconds": 120,
            "expiry_seconds": 420,
        },
        _profiles(),
    )

    assert result.accepted is False
    assert result.reason_codes == ("expiry_too_late",)


def test_replay_batch_evaluation_stays_pure() -> None:
    results = evaluate_replay_events(
        [
            {
                "signal_id": "win-buy-continuation",
                "symbol": "EURUSD",
                "timeframe": "M1",
                "setup_type": "continuation",
                "execution_action": "BUY",
                "entry_age_seconds": 120,
                "expiry_seconds": 180,
            },
            {
                "signal_id": "reversal-too-early",
                "symbol": "GBPUSD",
                "timeframe": "M1",
                "setup_type": "reversal",
                "execution_action": "SELL",
                "entry_age_seconds": 60,
                "expiry_seconds": 300,
            },
        ],
        _profiles(),
    )

    assert [result.accepted for result in results] == [True, False]
    assert results[1].reason_codes == ("reversal_needs_wait",)
