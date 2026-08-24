from __future__ import annotations

import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.decision.best_play_engine import analyze_best_play


def _build_frame(
    *,
    label: str,
    direction: str,
    confidence: float,
    family: str,
    continuation: float,
    reversal: float,
    path: float,
) -> dict[str, object]:
    opposite = "SELL" if direction == "BUY" else "BUY"
    return {
        "label": label,
        "file_name": f"{label.lower().replace(' ', '_')}.png",
        "file_path": f"{label.lower().replace(' ', '_')}.png",
        "action": direction,
        "execution_action": direction,
        "confidence": confidence,
        "probabilities": {direction: confidence, opposite: max(0.0, 1.0 - confidence - 0.08), "HOLD": 0.08},
        "memory_direction": direction,
        "memory_similarity": 0.61,
        "profile": {
            "bias_direction": direction,
            "bias_strength": confidence,
            "entry_direction": direction,
            "entry_confidence": confidence,
            "projection_direction": direction,
            "projection_confidence": min(0.95, confidence + 0.03),
            "projection_dominance": 0.22,
            "structure_direction": direction,
            "structure_confidence": max(0.0, confidence - 0.04),
            "sequence_direction": direction,
            "sequence_confidence": max(0.0, confidence - 0.06),
            "council_bias_direction": direction,
            "council_bias_confidence": max(0.0, confidence - 0.02),
            "council_alignment_score": 0.73,
            "council_projection_direction": direction,
            "council_projection_confidence": max(0.0, confidence - 0.01),
            "council_router_direction": direction,
            "council_router_strength": 0.69,
            "structure_setup": family,
            "continuation_probability": continuation,
            "reversal_probability": reversal,
        },
        "chart_state": {
            "entry_type": "reversal" if "reversal" in family else "continuation",
            "structure_setup": family,
            "momentum_bias": "bullish" if direction == "BUY" else "bearish",
            "path_clarity": path,
            "continuation_probability": continuation,
            "reversal_probability": reversal,
            "fakeout_probability": 0.12,
            "sequence_buy_pressure": 0.76 if direction == "BUY" else 0.12,
            "sequence_sell_pressure": 0.76 if direction == "SELL" else 0.12,
            "structure_buy_pressure": 0.74 if direction == "BUY" else 0.14,
            "structure_sell_pressure": 0.74 if direction == "SELL" else 0.14,
            "has_active_consolidation": family == "consolidation_breakout",
        },
        "sequence": {
            "current_box": {"box_type": "impulse", "direction": direction, "confidence": confidence},
            "primary_next_box": {"box_type": "impulse", "direction": direction, "confidence": min(0.95, confidence + 0.04)},
            "box_history": [
                {"box_type": "pullback", "direction": direction, "confidence": max(0.0, confidence - 0.10)},
                {"box_type": "impulse", "direction": direction, "confidence": confidence},
            ],
            "recent_colors": ["green", "green", "green"] if direction == "BUY" else ["red", "red", "red"],
            "has_active_consolidation": family == "consolidation_breakout",
            "continuation_probability": continuation,
            "pullback_probability": 0.22,
            "reversal_probability": reversal,
            "fakeout_probability": 0.12,
            "path_clarity": path,
            "box_sequence_agreement": 0.71,
        },
        "transitions": {
            "continue": continuation,
            "pullback": 0.18,
            "reversal_attempt": reversal,
            "fakeout": 0.08,
        },
        "patterns": {
            direction: [
                {"pattern": f"latest_candle_{direction.lower()}", "count": 2, "weight": 1.48, "max_confidence": confidence},
                {"pattern": f"{direction.lower()}_memory_bias", "count": 1, "weight": 0.82, "max_confidence": confidence - 0.06},
            ],
            opposite: [
                {"pattern": f"latest_candle_{opposite.lower()}", "count": 1, "weight": 0.28, "max_confidence": 0.22},
            ],
            "NEUTRAL": [],
        },
        "ensemble": {
            "predicted_label": direction,
            "confidence": confidence,
            "consensus_ratio": 0.78,
            "disagreement": 0.07,
            "router_direction": direction,
            "router_strength": 0.72,
            "champion_model": "dinov2",
            "confirmer_model": "clip",
            "selected_models": ["dinov2", "clip"],
            "model_votes": [
                {"direction": direction, "confidence": confidence},
                {"direction": direction, "confidence": max(0.0, confidence - 0.08)},
                {"direction": opposite, "confidence": 0.24},
            ],
            "sequence_tasks": {},
        },
        "timing": {"entry_state": "READY", "timing_score": 0.76},
        "multi_timeframe": {"gate_state": "confirmed", "gate_strength": 0.74, "aligned": True},
    }


def _buy_snapshot() -> dict[str, object]:
    higher = _build_frame(
        label="Higher TF",
        direction="BUY",
        confidence=0.78,
        family="impulse_chain",
        continuation=0.76,
        reversal=0.18,
        path=0.73,
    )
    lower = _build_frame(
        label="Lower TF",
        direction="BUY",
        confidence=0.82,
        family="impulse_chain",
        continuation=0.81,
        reversal=0.16,
        path=0.77,
    )
    combined = _build_frame(
        label="Combined Desk",
        direction="BUY",
        confidence=0.84,
        family="impulse_chain",
        continuation=0.83,
        reversal=0.14,
        path=0.79,
    )
    return {
        "generated_at": "2026-04-03T10:00:00Z",
        "combined": combined,
        "frames": [higher, lower],
    }


def _sell_snapshot() -> dict[str, object]:
    higher = _build_frame(
        label="Higher TF",
        direction="SELL",
        confidence=0.75,
        family="reversal_release",
        continuation=0.26,
        reversal=0.72,
        path=0.68,
    )
    lower = _build_frame(
        label="Lower TF",
        direction="SELL",
        confidence=0.80,
        family="reversal_release",
        continuation=0.24,
        reversal=0.78,
        path=0.72,
    )
    combined = _build_frame(
        label="Combined Desk",
        direction="SELL",
        confidence=0.83,
        family="reversal_release",
        continuation=0.22,
        reversal=0.81,
        path=0.74,
    )
    return {
        "generated_at": "2026-04-03T10:05:00Z",
        "combined": combined,
        "frames": [higher, lower],
    }


def test_analyze_best_play_prefers_buy_impulse_continuation() -> None:
    analysis = analyze_best_play(_buy_snapshot())

    assert analysis["status"] == "ready"
    assert analysis["recommended_direction"] == "BUY"
    assert "BUY" in str(analysis["recommended_play"])
    assert float(analysis["likelihoods"]["BUY"]) > float(analysis["likelihoods"]["SELL"])
    assert any("BUY" in str(item.get("label", "")) for item in analysis["frequent_sequences"])


def test_analyze_best_play_prefers_sell_reversal_release() -> None:
    analysis = analyze_best_play(_sell_snapshot())

    assert analysis["status"] == "ready"
    assert analysis["recommended_direction"] == "SELL"
    assert "SELL" in str(analysis["recommended_play"])
    assert float(analysis["sell_play"]["risk_score"]) < 0.55
    assert float(analysis["likelihoods"]["SELL"]) > float(analysis["likelihoods"]["BUY"])
