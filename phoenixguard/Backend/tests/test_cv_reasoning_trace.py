from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.vision.cv_module import CVPatternDetector


def test_build_reasoning_trace_for_conflict_setup() -> None:
    detector = CVPatternDetector.__new__(CVPatternDetector)
    detections: list[dict[str, Any]] = [
        {"pattern": "sell_memory_bias", "confidence": 0.95, "bbox": [0, 0, 1, 1]},
        {"pattern": "latest_candle_buy", "confidence": 0.81, "bbox": [0, 0, 1, 1]},
        {"pattern": "next_candle_buy", "confidence": 0.87, "bbox": [0, 0, 1, 1]},
        {"pattern": "wick_dominance_upper", "confidence": 0.62, "bbox": [0, 0, 1, 1]},
        {"pattern": "next_move_medium", "confidence": 0.70, "bbox": [0, 0, 1, 1]},
    ]

    trace = detector.build_reasoning_trace(detections)

    market = trace["market_state"]
    probs = trace["transition_probabilities"]
    assert market["macro_trend"] == "BEAR"
    assert market["local_phase"] in {
        "counter_trend_pullback",
        "counter_trend_spike",
        "reversal_base",
    }
    total = (
        probs["continue_prob"]
        + probs["pullback_prob"]
        + probs["reversal_attempt_prob"]
        + probs["fakeout_prob"]
    )
    assert abs(total - 1.0) < 1e-6


def test_build_reasoning_trace_for_with_trend_continuation() -> None:
    detector = CVPatternDetector.__new__(CVPatternDetector)
    detections: list[dict[str, Any]] = [
        {"pattern": "buy_memory_bias", "confidence": 0.93, "bbox": [0, 0, 1, 1]},
        {"pattern": "latest_candle_buy", "confidence": 0.84, "bbox": [0, 0, 1, 1]},
        {"pattern": "next_candle_buy", "confidence": 0.89, "bbox": [0, 0, 1, 1]},
        {"pattern": "next_move_large", "confidence": 0.75, "bbox": [0, 0, 1, 1]},
    ]

    trace = detector.build_reasoning_trace(detections)
    market = trace["market_state"]

    assert market["macro_trend"] == "BULL"
    assert market["control_state"] in {"with_trend", "transition"}
    assert trace["final_trade_bias"] == "BUY"
