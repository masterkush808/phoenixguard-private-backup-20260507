from __future__ import annotations

import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


def _make_result(
    *,
    action: str,
    confidence: float,
    projection_direction: str,
    projection_confidence: float,
    probabilities: dict[str, float],
    direction: str,
    structure_setup: str,
    structure_trade_ready: bool,
    momentum_bias: str,
    memory_direction: str,
    memory_similarity: float,
    gates_passing: int,
    consensus_ok: bool,
    path_clarity: float,
    structure_bias_direction: str,
    structure_bias_confidence: float,
    sequence_bias_direction: str,
    sequence_bias_confidence: float,
    continuation_probability: float,
    reversal_probability: float,
    entry_type: str = "continuation",
    position_size_pct: float = 1.6,
    expected_move_pct: float = 0.08,
    explanation: str = "base explanation",
) -> dict[str, object]:
    return {
        "action": action,
        "confidence": confidence,
        "probabilities": probabilities,
        "projection": {
            "direction": projection_direction,
            "confidence": projection_confidence,
            "dominance": 0.62,
            "box_type": "impulse",
        },
        "position_size_pct": position_size_pct,
        "expected_3min_move_pct": expected_move_pct,
        "quantile_range": [expected_move_pct * 0.5, expected_move_pct * 1.5],
        "gates_passing": gates_passing,
        "consensus_ok": consensus_ok,
        "memory_direction": memory_direction,
        "memory_similarity": memory_similarity,
        "execution_guard_ok": True,
        "support_gates_ok": True,
        "opposition_alert": False,
        "explanation": explanation,
        "chart_state": {
            "direction": direction,
            "direction_probability": confidence,
            "projection_bias_direction": projection_direction,
            "projection_bias_confidence": projection_confidence,
            "projection_dominance": 0.62,
            "structure_bias_direction": structure_bias_direction,
            "structure_bias_confidence": structure_bias_confidence,
            "sequence_bias_direction": sequence_bias_direction,
            "sequence_bias_confidence": sequence_bias_confidence,
            "momentum_bias": momentum_bias,
            "structure_setup": structure_setup,
            "structure_trade_ready": structure_trade_ready,
            "path_clarity": path_clarity,
            "continuation_probability": continuation_probability,
            "reversal_probability": reversal_probability,
            "macro_trend": "BULL" if momentum_bias == "bullish" else "BEAR",
            "local_phase": "with_trend_push" if structure_trade_ready else "with_trend_pause",
            "entry_type": entry_type,
        },
    }


def _make_bundle(higher: dict[str, object], lower: dict[str, object]) -> list[dict[str, object]]:
    return [
        {"result": higher, "compare_entry": {"label": "Higher TF"}},
        {"result": lower, "compare_entry": {"label": "Lower TF"}},
    ]


def test_multi_timeframe_fusion_confirms_aligned_trigger() -> None:
    higher = _make_result(
        action="BUY",
        confidence=0.82,
        projection_direction="BUY",
        projection_confidence=0.80,
        probabilities={"BUY": 0.78, "SELL": 0.08, "HOLD": 0.14},
        direction="BUY",
        structure_setup="impulse_chain",
        structure_trade_ready=True,
        momentum_bias="bullish",
        memory_direction="BUY",
        memory_similarity=0.48,
        gates_passing=10,
        consensus_ok=True,
        path_clarity=0.82,
        structure_bias_direction="BUY",
        structure_bias_confidence=0.76,
        sequence_bias_direction="BUY",
        sequence_bias_confidence=0.72,
        continuation_probability=0.72,
        reversal_probability=0.16,
        position_size_pct=2.2,
    )
    lower = _make_result(
        action="BUY",
        confidence=0.68,
        projection_direction="BUY",
        projection_confidence=0.66,
        probabilities={"BUY": 0.66, "SELL": 0.12, "HOLD": 0.22},
        direction="BUY",
        structure_setup="impulse_chain",
        structure_trade_ready=True,
        momentum_bias="bullish",
        memory_direction="BUY",
        memory_similarity=0.30,
        gates_passing=8,
        consensus_ok=False,
        path_clarity=0.74,
        structure_bias_direction="BUY",
        structure_bias_confidence=0.63,
        sequence_bias_direction="BUY",
        sequence_bias_confidence=0.59,
        continuation_probability=0.64,
        reversal_probability=0.22,
        position_size_pct=1.7,
    )

    fused = main._build_multi_timeframe_result(_make_bundle(higher, lower))
    mtf = fused["multi_timeframe"]

    assert fused["action"] == "BUY"
    assert mtf["gate_state"] == "confirmed"
    assert mtf["entry_allowed"] is True
    assert fused["position_size_pct"] > lower["position_size_pct"]
    assert "confirms the BUY trigger" in fused["explanation"]


def test_multi_timeframe_fusion_blocks_counter_bias_trigger() -> None:
    higher = _make_result(
        action="SELL",
        confidence=0.86,
        projection_direction="SELL",
        projection_confidence=0.82,
        probabilities={"BUY": 0.06, "SELL": 0.82, "HOLD": 0.12},
        direction="SELL",
        structure_setup="impulse_chain",
        structure_trade_ready=True,
        momentum_bias="bearish",
        memory_direction="SELL",
        memory_similarity=0.52,
        gates_passing=10,
        consensus_ok=True,
        path_clarity=0.84,
        structure_bias_direction="SELL",
        structure_bias_confidence=0.78,
        sequence_bias_direction="SELL",
        sequence_bias_confidence=0.70,
        continuation_probability=0.75,
        reversal_probability=0.14,
        position_size_pct=2.0,
    )
    lower = _make_result(
        action="BUY",
        confidence=0.72,
        projection_direction="BUY",
        projection_confidence=0.68,
        probabilities={"BUY": 0.71, "SELL": 0.10, "HOLD": 0.19},
        direction="BUY",
        structure_setup="impulse_chain",
        structure_trade_ready=True,
        momentum_bias="bullish",
        memory_direction="BUY",
        memory_similarity=0.34,
        gates_passing=8,
        consensus_ok=True,
        path_clarity=0.70,
        structure_bias_direction="BUY",
        structure_bias_confidence=0.61,
        sequence_bias_direction="BUY",
        sequence_bias_confidence=0.57,
        continuation_probability=0.62,
        reversal_probability=0.26,
        position_size_pct=1.8,
    )

    fused = main._build_multi_timeframe_result(_make_bundle(higher, lower))
    mtf = fused["multi_timeframe"]

    assert fused["action"] == "HOLD"
    assert mtf["gate_state"] == "blocked"
    assert mtf["entry_allowed"] is False
    assert fused["position_size_pct"] == 0.0
    assert fused["execution_guard_ok"] is False
    assert "blocks the BUY trigger" in fused["explanation"]


def test_multi_timeframe_fusion_marks_weak_higher_timeframe_as_watch() -> None:
    higher = _make_result(
        action="HOLD",
        confidence=0.38,
        projection_direction="HOLD",
        projection_confidence=0.21,
        probabilities={"BUY": 0.31, "SELL": 0.29, "HOLD": 0.40},
        direction="HOLD",
        structure_setup="none",
        structure_trade_ready=False,
        momentum_bias="neutral",
        memory_direction="HOLD",
        memory_similarity=0.04,
        gates_passing=4,
        consensus_ok=False,
        path_clarity=0.24,
        structure_bias_direction="HOLD",
        structure_bias_confidence=0.08,
        sequence_bias_direction="HOLD",
        sequence_bias_confidence=0.12,
        continuation_probability=0.34,
        reversal_probability=0.28,
        position_size_pct=0.8,
    )
    lower = _make_result(
        action="BUY",
        confidence=0.69,
        projection_direction="BUY",
        projection_confidence=0.65,
        probabilities={"BUY": 0.68, "SELL": 0.11, "HOLD": 0.21},
        direction="BUY",
        structure_setup="impulse_chain",
        structure_trade_ready=True,
        momentum_bias="bullish",
        memory_direction="BUY",
        memory_similarity=0.26,
        gates_passing=8,
        consensus_ok=False,
        path_clarity=0.71,
        structure_bias_direction="BUY",
        structure_bias_confidence=0.60,
        sequence_bias_direction="BUY",
        sequence_bias_confidence=0.56,
        continuation_probability=0.61,
        reversal_probability=0.24,
        position_size_pct=1.5,
    )

    fused = main._build_multi_timeframe_result(_make_bundle(higher, lower))
    mtf = fused["multi_timeframe"]

    assert fused["action"] == "BUY"
    assert mtf["gate_state"] == "watch"
    assert mtf["entry_allowed"] is True
    assert "mixed on the BUY trigger" in mtf["gate_explanation"]
