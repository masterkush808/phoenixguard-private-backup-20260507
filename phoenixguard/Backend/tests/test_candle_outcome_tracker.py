from __future__ import annotations

import json
from pathlib import Path

from phoenixguard.decision.candle_outcome_tracker import (
    CANDLE_OUTCOME_TRACKER_V1,
    track_candle_outcome,
)
from phoenixguard.runtime.observability_v3 import record_candle_outcome_metrics


def test_buy_outcome_metrics_capture_path_and_trap_flags() -> None:
    metrics = track_candle_outcome(
        {
            "side": "BUY",
            "entry_price": 100.0,
            "target_price": 103.7,
            "stop_price": 97.0,
            "opposing_force_price": 103.5,
            "dominance_score": 0.72,
            "active_trend_angle_degrees": 36.0,
        },
        [
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.8, "dominance_score": 0.68, "angle": 34.0},
            {"open": 100.8, "high": 103.8, "low": 100.3, "close": 103.2, "dominance_score": 0.58, "angle": 31.0},
            {"open": 103.2, "high": 102.7, "low": 100.0, "close": 100.4, "dominance_score": 0.50, "angle_class": "BROKEN_ANGLE"},
        ],
    )

    assert metrics["version"] == CANDLE_OUTCOME_TRACKER_V1
    assert metrics["mfe"] == 3.8
    assert metrics["mae"] == 0.5
    assert metrics["time_to_best_candles"] == 2
    assert metrics["time_to_worst_candles"] == 1
    assert 0.0 < metrics["path_smoothness"] < 1.0
    assert metrics["max_drawdown"] > 0.0
    assert metrics["touched_opposing_force"] is True
    assert metrics["returned_to_entry"] is True
    assert metrics["dominance_weakened"] is True
    assert metrics["angle_broke"] is True
    assert metrics["final_outcome_proxy"] == "WIN"


def test_sell_outcome_metrics_capture_loss_proxy() -> None:
    metrics = track_candle_outcome(
        {
            "side": "SELL",
            "entry_price": 50.0,
            "target_price": 47.0,
            "stop_price": 52.0,
            "nearest_demand_price": 47.4,
        },
        [
            {"open": 50.0, "high": 51.0, "low": 49.0, "close": 49.5},
            {"open": 49.5, "high": 52.4, "low": 48.7, "close": 52.2},
        ],
    )

    assert metrics["side"] == "SELL"
    assert metrics["mfe"] == 1.3
    assert metrics["mae"] == 2.4
    assert metrics["time_to_worst_candles"] == 2
    assert metrics["stop_hit"] is True
    assert metrics["final_outcome_proxy"] == "LOSS"


def test_observability_records_candle_outcome_metrics(tmp_path: Path) -> None:
    metrics = track_candle_outcome(
        {"side": "BUY", "entry_price": 10.0, "target_price": 11.0, "stop_price": 9.5},
        [{"open": 10.0, "high": 11.1, "low": 9.9, "close": 10.8}],
    )
    log_path = tmp_path / "paper" / "candle_outcomes.jsonl"

    row = record_candle_outcome_metrics(
        log_path,
        metrics,
        packet={"packet_id": "pgpkt-paper", "session_id": "pocket-live-8788", "execution": {"side": "BUY"}},
        now_epoch=1000.0,
    )

    assert row["event"] == "candle_outcome_metrics"
    assert row["tracker_version"] == CANDLE_OUTCOME_TRACKER_V1
    persisted = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert persisted["metrics"]["final_outcome_proxy"] == "WIN"
