from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_signal_overview_keeps_decimal_confidence_snippets_intact() -> None:
    result = { # pyright: ignore[reportUnknownVariableType]
        "action": "BUY",
        "confidence": 0.643,
        "decision_state": "PROJECTED",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "expected_3min_move_pct": -0.00415,
        "position_size_pct": 0.62,
        "gates_passing": 7,
        "memory_similarity": 0.22,
        "latest_parse_quality": 1.0,
        "module_reliability": {"cv_quality": 0.64},
        "projection": {"direction": "SELL", "confidence": 0.89},
        "chart_state": {"entry_type": "continuation", "momentum_bias": "bullish", "structure_setup": "none"},
        "timing_signal": {
            "entry_state": "READY",
            "eta_minutes": {"low": 20.0, "high": 40.0},
            "timeframe": "M5",
        },
        "multi_timeframe": {"gate_state": "confirmed"},
        "explanation": "Higher timeframe confirms the BUY trigger: higher bias is BUY (0.64); higher council bias backs BUY (0.62); lower council bias agrees (0.58).",
    }

    html = main.build_signal_overview_html(result)

    assert "(0.64)" in html
    assert "(0.62)" in html
    assert "(0.58)" in html


def test_signal_overview_prefers_execution_action_over_headline_bias() -> None:
    result = { # pyright: ignore[reportUnknownVariableType]
        "action": "BUY",
        "headline_action": "BUY",
        "execution_action": "HOLD",
        "confidence": 0.643,
        "decision_state": "PROJECTED",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "expected_3min_move_pct": -0.00415,
        "position_size_pct": 0.62,
        "gates_passing": 7,
        "memory_similarity": 0.22,
        "latest_parse_quality": 1.0,
        "module_reliability": {"cv_quality": 0.64},
        "projection": {"direction": "SELL", "confidence": 0.89},
        "chart_state": {"entry_type": "continuation", "momentum_bias": "bullish", "structure_setup": "none"},
        "timing_signal": {
            "entry_state": "READY",
            "eta_minutes": {"low": 20.0, "high": 40.0},
            "timeframe": "M5",
        },
        "multi_timeframe": {"gate_state": "confirmed"},
        "explanation": "Higher bias is BUY (0.64); higher council bias backs BUY (0.62); lower council bias agrees (0.58).",
    }

    html = main.build_signal_overview_html(result)

    assert "<div class='pg-action-label pg-hold'>HOLD</div>" in html
    assert "Headline BUY" in html
    assert "Active BUY ON CONFIRMATION" in html


def test_signal_overview_uses_reference_operator_labels() -> None:
    result = { # pyright: ignore[reportUnknownVariableType]
        "action": "BUY",
        "confidence": 0.742,
        "decision_state": "PROJECTED",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "expected_3min_move_pct": 0.01097,
        "position_size_pct": 0.80,
        "gates_passing": 6,
        "memory_similarity": 0.158,
        "latest_parse_quality": 0.0,
        "module_reliability": {"cv_quality": 0.32},
        "projection": {"direction": "BUY", "confidence": 0.71},
        "chart_state": {"entry_type": "continuation", "momentum_bias": "bullish", "structure_setup": "none"},
        "multi_timeframe": {"gate_state": "confirmed"},
        "explanation": "Higher timeframe confirms the BUY trigger while the lower timeframe still asks for patience.",
    }

    html = main.build_signal_overview_html(result)

    assert "808FX Direction" in html
    assert "Consensus Watch" in html
    assert "Memory HOLD" in html
    assert "Execution Wait For Confirmation" in html
    assert "Parse Quality" in html
    assert "cv_quality=0.32" in html
