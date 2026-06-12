from __future__ import annotations

import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main
from phoenixguard.interpreter import interpret


def test_interpreter_emits_post_ensemble_machine_and_human_views() -> None:
    fusion = {
        "cv": {
            "setup": "impulse_chain",
            "structure": "current impulse BUY -> projected impulse BUY -> latest=BUY 0.74 -> path=0.81",
            "notes": "structure is aligned",
        },
        "memory": {
            "match_quality": "high",
            "similarity": 0.91,
            "direction": "BUY",
            "ambiguity": 0.08,
            "consensus_ratio": 0.84,
        },
        "forecast": {
            "direction": "BUY",
            "magnitude": 0.18,
            "q05": 0.06,
            "q95": 0.31,
        },
        "rl": {
            "action": "BUY",
            "probs": {"BUY": 0.72, "SELL": 0.10, "HOLD": 0.18},
            "blend_weight": 0.32,
        },
        "gates": {
            "passing": 10,
            "total": 12,
            "blockers": ["Execution Guard"],
            "support_ok": True,
            "support_blockers": [],
            "risk": "elevated",
        },
        "ensemble": {
            "action": "BUY",
            "trade_bias": "BUY",
            "decision_state": "CONFIRMED",
            "execution_permission": "EXECUTE",
            "confidence": 0.86,
            "consensus_ok": True,
        },
        "context": {
            "projection_direction": "BUY",
            "risk_factors": ["Primary gate blockers: Execution Guard."],
            "invalidation": "stand down if projection flips away from BUY.",
        },
    }

    interpreted = interpret(fusion)
    machine = interpreted["machine"]
    human = interpreted["human"]

    assert machine["schema_version"] == "2.0"
    assert machine["final_action"] == "BUY"
    assert machine["active_trade_state"] == "BUY_NOW"
    assert machine["confidence_band"] == "high"
    assert machine["gate_alignment"] == "10/12"
    assert machine["support_alignment"] == "aligned"
    assert "Action: BUY" in human
    assert "Active: BUY_NOW" in human
    assert "State: CONFIRMED" in human
    assert "Forecast: BUY +0.180%" in human


def test_main_summary_surfaces_interpreter_output() -> None:
    result = {
        "action": "SELL",
        "trade_bias": "SELL",
        "decision_state": "PROJECTED",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "confidence": 0.68,
        "expected_3min_move_pct": -0.12,
        "quantile_range": [-0.22, -0.03],
        "position_size_pct": 1.3,
        "gates_passing": 8,
        "consensus_ok": False,
        "memory_similarity": 0.58,
        "memory_direction": "SELL",
        "ad_indicator": -0.11,
        "explanation": "projection is leading but execution still needs confirmation",
        "projection": {
            "direction": "SELL",
            "box_type": "reversal_base",
            "confidence": 0.64,
            "dominance": 0.19,
        },
        "interpreter": {
            "human": (
                "Action: SELL | Bias: SELL | State: PROJECTED | Execution: WAIT_FOR_CONFIRMATION\n"
                "Plan: SELL with guarded conviction; execution=wait for confirmation"
            ),
        },
    }

    summary = main.human_readable_summary(result)

    assert "Decision State: PROJECTED" in summary
    assert "Execution: WAIT_FOR_CONFIRMATION" in summary
    assert "Interpreter:" in summary
    assert "Plan: SELL with guarded conviction" in summary


def test_interpreter_prefers_execution_action_when_action_missing() -> None:
    fusion = {
        "cv": {"setup": "none", "structure": "structure unavailable"},
        "memory": {"match_quality": "low"},
        "forecast": {"direction": "HOLD", "magnitude": 0.0, "q05": 0.0, "q95": 0.0},
        "rl": {"action": "HOLD", "probs": {"BUY": 0.34, "SELL": 0.46, "HOLD": 0.20}},
        "gates": {"passing": 4, "total": 12, "blockers": [], "support_ok": False, "support_blockers": [], "risk": ""},
        "ensemble": {
            "trade_bias": "SELL",
            "execution_action": "SELL",
            "decision_state": "PROJECTED",
            "execution_permission": "WAIT_FOR_CONFIRMATION",
            "confidence": 0.61,
            "consensus_ok": False,
        },
        "context": {"risk_factors": []},
    }

    interpreted = interpret(fusion)

    assert interpreted["machine"]["final_action"] == "SELL"
    assert interpreted["machine"]["active_trade_state"] == "SELL_ON_CONFIRMATION"
    assert "Action: SELL" in interpreted["human"]


def test_human_readable_summary_includes_timing_signal() -> None:
    result = {
        "action": "BUY",
        "trade_bias": "BUY",
        "decision_state": "PROJECTED",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "confidence": 0.74,
        "expected_3min_move_pct": 0.11,
        "quantile_range": [0.04, 0.19],
        "position_size_pct": 0.8,
        "gates_passing": 6,
        "consensus_ok": False,
        "memory_similarity": 0.16,
        "memory_direction": "BUY",
        "ad_indicator": 0.09,
        "explanation": "higher timeframe confirms the setup while execution still needs patience",
        "projection": {
            "direction": "BUY",
            "box_type": "impulse",
            "confidence": 0.71,
            "dominance": 0.22,
        },
        "timing_signal": {
            "entry_state": "WATCH",
            "timing_score": 0.47,
            "eta_minutes": {"low": 10.0, "high": 20.0},
            "timeframe": "M5",
            "reasons": ["higher timeframe still leads", "trigger chart needs confirmation"],
        },
    }

    summary = main.human_readable_summary(result)

    assert "Timing: WATCH" in summary
    assert "score=0.47" in summary
    assert "eta=10.0-20.0m" in summary
    assert "Timing Rationale: higher timeframe still leads; trigger chart needs confirmation" in summary
