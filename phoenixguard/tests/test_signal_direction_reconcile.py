from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_reconcile_projection_action_conflict_flips_to_projection_direction() -> None:
    decision: dict[str, Any] = {
        "action": "BUY",
        "trade_bias": "BUY",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "decision_state": "UNCERTAIN",
        "confidence": 0.64,
        "calibrated_probs": {"BUY": 0.64, "SELL": 0.23, "HOLD": 0.13},
    }
    chart_state: dict[str, Any] = {
        "projection_bias_direction": "SELL",
        "projection_bias_confidence": 0.89,
        "projection_dominance": 0.07,
    }
    projection_view: dict[str, Any] = {
        "direction": "SELL",
        "confidence": 0.89,
        "dominance_gap": 0.07,
    }

    adjusted = main.reconcile_projection_action_conflict(
        decision,
        chart_state=chart_state,
        projection_view=projection_view,
    )

    assert adjusted["projection_conflict_override"] is True
    assert adjusted["action"] == "SELL"
    assert adjusted["trade_bias"] == "SELL"
    assert adjusted["decision_state"] == "PROJECTED"
    assert adjusted["confidence"] >= 0.70


def test_reconcile_projection_action_conflict_does_not_override_execute_state() -> None:
    decision: dict[str, Any] = {
        "action": "BUY",
        "trade_bias": "BUY",
        "execution_permission": "EXECUTE",
        "decision_state": "CONFIRMED",
        "confidence": 0.72,
        "calibrated_probs": {"BUY": 0.72, "SELL": 0.18, "HOLD": 0.10},
    }
    chart_state: dict[str, Any] = {
        "projection_bias_direction": "SELL",
        "projection_bias_confidence": 0.91,
        "projection_dominance": 0.10,
    }
    projection_view: dict[str, Any] = {
        "direction": "SELL",
        "confidence": 0.91,
        "dominance_gap": 0.10,
    }

    adjusted = main.reconcile_projection_action_conflict(
        decision,
        chart_state=chart_state,
        projection_view=projection_view,
    )

    assert adjusted.get("projection_conflict_override", False) is False
    assert adjusted["action"] == "BUY"
    assert adjusted["decision_state"] == "CONFIRMED"


def test_missing_directional_evidence_stays_neutral_in_chart_helpers() -> None:
    swing_state = main.classify_swing_state([], {}, market_state=None)
    trend_regime = main.summarize_trend_regime([], {}, market_state=None)

    assert swing_state["current_direction"] == "HOLD"
    assert swing_state["macro_direction"] == "HOLD"
    assert swing_state["recent_swing_direction"] == "HOLD"
    assert trend_regime["trend_direction"] == "HOLD"


def test_active_trade_overlay_promotes_bearish_hold_to_sell_on_confirmation() -> None:
    result: dict[str, Any] = {
        "action": "HOLD",
        "headline_action": "HOLD",
        "execution_action": "HOLD",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "decision_state": "UNCERTAIN",
        "confidence": 0.41,
        "projection_bias_ready": False,
        "projection": {
            "direction": "SELL",
            "box_type": "reversal_base",
            "confidence": 0.75,
            "dominance": 0.62,
        },
        "projection_chain_boxes": [
            {"direction": "SELL", "box_type": "reversal_base", "confidence": 0.75},
            {"direction": "SELL", "box_type": "impulse", "confidence": 0.91},
            {"direction": "SELL", "box_type": "impulse", "confidence": 0.93},
        ],
        "latest_candle_confidence": 0.76,
        "geometry_reference_direction": "SELL",
        "current_box": {"direction": "BUY", "box_type": "impulse", "confidence": 0.79},
        "local_ensemble": {
            "ensemble": {
                "predicted_label": "BUY",
                "confidence": 0.68,
                "consensus_ratio": 0.46,
                "disagreement": 0.37,
            }
        },
        "memory_direction": "SELL",
        "memory_similarity": 0.18,
        "gates_passing": 6,
        "consensus_ok": False,
        "support_gates_ok": False,
        "execution_guard_ok": False,
        "opposition_alert": True,
        "module_reliability": {"cv_quality": 0.74, "structure_consistency": 0.71},
        "forecast_debug": {"execution_readiness": 0.31},
        "chart_state": {
            "path_clarity": 0.46,
            "momentum_bias": "bearish",
            "council_projection_direction": "SELL",
            "council_projection_confidence": 0.72,
            "council_bias_direction": "BUY",
            "council_bias_confidence": 0.61,
        },
        "multi_timeframe": {
            "lead_bias_direction": "SELL",
            "lead_bias_strength": 0.82,
            "lead_projection": "SELL",
            "lead_projection_confidence": 0.75,
            "trigger_direction": "BUY",
            "gate_state": "blocked",
            "gate_strength": 0.81,
        },
        "detections": [
            {"pattern": "next_candle_sell", "confidence": 0.79},
            {"pattern": "latest_candle_sell", "confidence": 0.76},
        ],
    }

    overlay = main.derive_active_trade_overlay(result)

    assert overlay["directional_intent"] == "SELL"
    assert overlay["active_trade_state"] == "SELL_ON_CONFIRMATION"
    assert overlay["hold_subtype"] == "HOLD_BEARISH"
