from __future__ import annotations

from typing import Any

from phoenixguard.study.behavioral_sequence_v3 import (
    BEHAVIORAL_SEQUENCE_SCHEMA_VERSION,
    measure_market_behavior_v3,
    summarize_regime_transitions_v3,
)
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3


def _market_path(closes: list[float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous = closes[0] - 1.0
    for index, close in enumerate(closes):
        open_value = previous
        resting = abs(close - open_value) <= 0.10
        padding = 0.50 if not resting else 0.70
        rows.append(
            {
                "candle_id": f"c{index}",
                "timestamp": 1_700_000_000 + index * 300,
                "open": open_value,
                "high": max(open_value, close) + padding,
                "low": min(open_value, close) - padding,
                "close": close,
                "is_closed": True,
            }
        )
        previous = close
    return rows


def test_behavior_study_measures_swings_rests_transitions_and_two_trends() -> None:
    candles = _market_path([101.0, 103.0, 105.0, 105.05, 105.0, 103.0, 101.0])
    candle_study = analyze_candle_sequence_v3(candles, regime="TRENDING_UP")

    result = measure_market_behavior_v3(candle_study, timeframe_seconds=300, inner_window=2)

    assert result["schema_version"] == BEHAVIORAL_SEQUENCE_SCHEMA_VERSION
    assert result["status"] == "STUDIED"
    assert result["execution_authority"] is False
    assert [(row["state"], row["candle_count"]) for row in result["segments"]] == [
        ("UP_SWING", 3),
        ("REST", 2),
        ("DOWN_SWING", 2),
    ]
    assert result["rest_summary"]["average_candles"] == 2.0
    assert result["rest_summary"]["average_duration_seconds"] == 600.0
    assert result["rest_summary"]["breakout_down_count"] == 1
    assert result["major_trend"]["label"] == "SIDEWAYS"
    assert result["inner_trend"]["label"] == "DOWN"
    assert result["current_state"] == {
        "state": "DOWN_SWING",
        "direction": "DOWN",
        "candle_count": 2,
        "duration_seconds": 600,
        "started_at_index": 5,
    }
    transitions = {
        (row["from"], row["to"]): row["count"]
        for row in result["segment_transition_summary"]["rows"]
    }
    assert transitions == {("REST", "DOWN_SWING"): 1, ("UP_SWING", "REST"): 1}
    assert "Major trend: SIDEWAYS. Inner trend: DOWN." in result["market_story"]
    assert result["segments"][0]["absolute_change_in_median_ranges"] > 0.0


def test_behavior_duration_uses_iso_timestamps_when_available() -> None:
    candles = _market_path([101.0, 103.0, 105.0])
    candles[0]["timestamp"] = "2026-07-24T00:00:00+00:00"
    candles[1]["timestamp"] = "2026-07-24T00:05:00+00:00"
    candles[2]["timestamp"] = "2026-07-24T00:10:00+00:00"

    result = measure_market_behavior_v3(
        analyze_candle_sequence_v3(candles),
        timeframe_seconds=300,
    )

    assert result["segments"][0]["duration_seconds"] == 900


def test_behavior_study_reports_insufficient_history_without_inventing_a_trend() -> None:
    empty_candle_study = analyze_candle_sequence_v3([])

    result = measure_market_behavior_v3(empty_candle_study)

    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["major_trend"]["direction"] == "UNKNOWN"
    assert result["inner_trend"]["direction"] == "UNKNOWN"
    assert result["segments"] == []


def test_transition_summary_preserves_self_transitions_and_probabilities() -> None:
    summary = summarize_regime_transitions_v3(
        ["UP_SWING", "UP_SWING", "REST", "DOWN_SWING", "DOWN_SWING"]
    )

    assert summary["observation_count"] == 4
    assert summary["matrix"]["UP_SWING"]["UP_SWING"] == 0.5
    assert summary["matrix"]["UP_SWING"]["REST"] == 0.5
    assert summary["matrix"]["DOWN_SWING"]["DOWN_SWING"] == 1.0
