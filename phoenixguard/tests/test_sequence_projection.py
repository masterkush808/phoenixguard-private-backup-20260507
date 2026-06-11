from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.decision.ensemble import EnsembleDecisionEngine
from main import (
    _apply_parse_quality_cap_to_detections,
    _build_box_history,
    _build_chart_state,
    _build_sequence_model_summary,
    _build_next_box_hypotheses,
    _extract_latest_signal_state,
    _ensemble_base_probs,
    _fuse_transition_probabilities,
    _should_relax_hold_veto,
)
from phoenixguard.decision.regression_module import ImageFusionRegressor


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, *args: object, **kwargs: object) -> None:
        return None

    def exception(self, *args: object, **kwargs: object) -> None:
        return None

    def error(self, *args: object, **kwargs: object) -> None:
        return None


def _make_candle(x1: float, x2: float, y1: float, y2: float, *, green: bool, body: float = 0.16) -> dict[str, Any]:
    return {
        "bbox": [x1, y1, x2, y2],
        "parse_conf": 0.88,
        "body_height_pct": body,
        "upper_wick_pct": 0.18,
        "lower_wick_pct": 0.20,
        "close_pos_in_range": 0.55,
        "candle_color_green": 1.0 if green else 0.0,
    }


def test_box_history_tracks_internal_sequence_and_consolidation() -> None:
    candles = [
        _make_candle(10, 16, 110, 180, green=False, body=0.14),
        _make_candle(20, 26, 112, 182, green=True, body=0.15),
        _make_candle(30, 36, 114, 184, green=False, body=0.16),
        _make_candle(40, 46, 112, 181, green=True, body=0.14),
    ]

    boxes = _build_box_history(candles, image_width=200.0, image_height=220.0)

    assert boxes
    first = boxes[0]
    assert first["contains_consolidation"] is True
    assert str(first["sequence_signature"]).startswith("R1-G1")
    assert len(first["internal_sequence"]) >= 2
    assert sum(len(box["internal_sequence"]) for box in boxes) == 4


def test_sequence_model_summary_tracks_directional_pressure() -> None:
    sequence_state = {
        "recent_colors": ["green", "green", "green", "red", "green"],
        "box_history": [
            {"direction": "BUY", "box_type": "impulse", "confidence": 0.76, "maturity": 0.72},
            {"direction": "BUY", "box_type": "pullback", "confidence": 0.68, "maturity": 0.48},
            {"direction": "BUY", "box_type": "impulse", "confidence": 0.82, "maturity": 0.70},
        ],
        "current_box": {"direction": "BUY", "box_type": "impulse", "confidence": 0.82},
        "primary_next_box": {"direction": "BUY", "box_type": "impulse", "confidence": 0.74},
        "continuation_probability": 0.64,
        "reversal_probability": 0.14,
        "fakeout_probability": 0.12,
        "box_sequence_agreement": 0.78,
        "path_clarity": 0.72,
        "spacing_consistency": 0.67,
        "color_flip_rate": 0.24,
        "body_std_pct": 0.06,
        "small_body_ratio": 0.14,
        "has_active_consolidation": False,
    }

    summary = _build_sequence_model_summary(
        sequence_state,
        {"body_height_pct": 0.54, "geometry_confidence": 0.81},
        market_state={"macro_trend": "BULL"},
    )

    assert summary["direction"] == "BUY"
    assert float(summary["buy_pressure"]) > float(summary["sell_pressure"])
    assert 0.0 <= float(summary["uncertainty"]) <= 1.0


def test_fused_transitions_use_sequence_state_projection() -> None:
    reasoning_trace = {
        "transition_probabilities": {
            "continue_prob": 0.30,
            "pullback_prob": 0.30,
            "reversal_attempt_prob": 0.20,
            "fakeout_prob": 0.20,
        }
    }
    sequence_state = {
        "continuation_probability": 0.62,
        "pullback_probability": 0.18,
        "reversal_probability": 0.10,
        "fakeout_probability": 0.10,
        "current_box": {
            "box_type": "balance",
            "contains_consolidation": True,
            "consolidation_score": 0.72,
        },
        "next_box_hypotheses": [
            {"box_type": "impulse", "direction": "BUY", "path_clarity": 0.78}
        ],
    }

    fused = _fuse_transition_probabilities(reasoning_trace, {}, sequence_state=sequence_state)

    assert fused["continue"] > fused["pullback"]
    assert fused["continue"] > 0.40


def test_forecast_requires_consolidation_but_releases_when_present() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    base_chart_state = {
        "direction": "BUY",
        "direction_probability": 0.74,
        "implied_3min_move_pct": 0.08,
        "entry_candle": {"body_pct": 0.22},
        "mcts": {"buy_prob": 0.72, "sell_prob": 0.28},
        "box_sequence_agreement": 0.76,
        "path_clarity": 0.74,
        "projection_alignment": 1.0,
        "projected_next_box": {"box_type": "impulse", "direction": "BUY", "confidence": 0.71},
    }

    without_consol = reg.forecast_3m(
        {**base_chart_state, "has_active_consolidation": False, "structure_trade_ready": False},
        detections=[],
        memory_similarity=0.82,
        memory_direction="BUY",
        transition_summary={"continue_prob": 0.58, "pullback_prob": 0.16, "reversal_attempt_prob": 0.12, "fakeout_prob": 0.14},
        memory_summary={"top_similarity": 0.82, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )
    with_consol = reg.forecast_3m(
        {**base_chart_state, "has_active_consolidation": True, "structure_trade_ready": True, "consolidation_score": 0.71},
        detections=[],
        memory_similarity=0.82,
        memory_direction="BUY",
        transition_summary={"continue_prob": 0.58, "pullback_prob": 0.16, "reversal_attempt_prob": 0.12, "fakeout_prob": 0.14},
        memory_summary={"top_similarity": 0.82, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert without_consol["force_hold"] is True
    assert with_consol["force_hold"] is False
    assert with_consol["execution_readiness"] > without_consol["execution_readiness"]


def test_forecast_releases_for_impulse_chain_without_consolidation() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state = {
        "direction": "BUY",
        "direction_probability": 0.55,
        "implied_3min_move_pct": 0.41,
        "entry_candle": {"body_pct": 0.68},
        "mcts": {"buy_prob": 0.55, "sell_prob": 0.45},
        "box_sequence_agreement": 0.50,
        "path_clarity": 0.72,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": True,
        "structure_setup": "impulse_chain",
        "projected_next_box": {"box_type": "impulse", "direction": "BUY", "confidence": 0.59},
    }

    result = reg.forecast_3m(
        chart_state,
        detections=[],
        memory_similarity=0.82,
        memory_direction="BUY",
        transition_summary={"continue_prob": 0.59, "pullback_prob": 0.21, "reversal_attempt_prob": 0.10, "fakeout_prob": 0.10},
        memory_summary={"top_similarity": 0.82, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_trade_ready"] == 1.0
    assert result["structure_setup"] == "impulse_chain"
    assert result["force_hold"] is False


def test_ensemble_base_probs_downweights_hold_on_structural_breakout() -> None:
    probs = _ensemble_base_probs(
        {
            "ensemble": {
                "buy_prob": 0.562,
                "sell_prob": 0.438,
                "predicted_label": "BUY",
                "disagreement": 0.134,
                "consensus_ratio": 0.60,
            }
        },
        chart_state={
            "has_active_consolidation": True,
            "structure_trade_ready": True,
            "path_clarity": 0.66,
            "continuation_probability": 0.61,
            "projected_next_box": {"direction": "BUY", "confidence": 0.78},
        },
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "consensus_ratio": 1.0,
        },
    )

    assert probs["BUY"] > probs["HOLD"]
    assert probs["HOLD"] < 0.15


def test_hold_veto_relaxation_accepts_structural_alignment() -> None:
    relaxed = _should_relax_hold_veto(
        local_ensemble={"ensemble": {"predicted_label": "BUY", "confidence": 0.58}},
        memory_direction="BUY",
        memory_summary={"dominant_label": "BUY", "top_similarity": 0.83, "mixed_labels": False},
        fused_transition_probabilities={"continue": 0.61, "reversal_attempt": 0.16, "fakeout": 0.13},
        latest_candle_confidence=0.42,
        latest_candle_direction="SELL",
        reasoning_trace={"market_state": {"local_phase": "counter_trend_pullback"}},
        chart_state={
            "structure_trade_ready": True,
            "projected_next_box": {"direction": "BUY", "confidence": 0.79},
        },
    )

    assert relaxed is True


def test_ensemble_supportive_memory_can_execute_below_strict_veto() -> None:
    gates = [
        SimpleNamespace(name=f"g{i}", score=0.92, pass_fail=True, detail={})
        for i in range(8)
    ]
    engine = EnsembleDecisionEngine(
        consensus_threshold=0.62,
        max_interval_pct=0.40,
        risk_min_pct=0.5,
        risk_max_pct=2.0,
        gates_pass_minimum=6,
        memory_veto_threshold=0.87,
    )

    result = engine.infer(
        rl_probs={"BUY": 0.68, "SELL": 0.20, "HOLD": 0.12},
        forecast={
            "q05": 0.05,
            "q50": 0.18,
            "q95": 0.24,
            "ad_indicator": 0.24,
            "poly_slope": 0.18,
            "execution_readiness": 0.76,
            "active_consolidation": 1.0,
            "structure_trade_ready": 1.0,
            "structure_setup": "consolidation_breakout",
            "projected_box_direction": "BUY",
            "projected_box_confidence": 0.81,
        },
        gate_outputs=gates,
        memory_bank_similarity=0.82,
        module_reliability={"cv_quality": 0.78, "structure_consistency": 0.84},
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "ambiguity": 0.0,
            "label_entropy": 0.0,
            "consensus_ratio": 1.0,
        },
        latest_candle_confidence=0.70,
        transition_summary={"continue_prob": 0.61, "pullback_prob": 0.12, "reversal_attempt_prob": 0.15, "fakeout_prob": 0.12},
    )

    assert result["memory_ok"] is True
    assert result["projection_support"] is True
    assert result["action"] == "BUY"


def test_ensemble_supports_impulse_chain_without_consolidation() -> None:
    gates = [
        SimpleNamespace(name=f"g{i}", score=0.92, pass_fail=True, detail={})
        for i in range(8)
    ]
    engine = EnsembleDecisionEngine(
        consensus_threshold=0.62,
        max_interval_pct=0.40,
        risk_min_pct=0.5,
        risk_max_pct=2.0,
        gates_pass_minimum=6,
        memory_veto_threshold=0.87,
    )

    result = engine.infer(
        rl_probs={"BUY": 0.66, "SELL": 0.22, "HOLD": 0.12},
        forecast={
            "q05": 0.07,
            "q50": 0.18,
            "q95": 0.27,
            "ad_indicator": 0.22,
            "poly_slope": 0.20,
            "execution_readiness": 0.63,
            "active_consolidation": 0.0,
            "structure_trade_ready": 1.0,
            "structure_setup": "impulse_chain",
            "projected_box_direction": "BUY",
            "projected_box_confidence": 0.59,
        },
        gate_outputs=gates,
        memory_bank_similarity=0.82,
        module_reliability={"cv_quality": 0.74, "structure_consistency": 1.0},
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "ambiguity": 0.0,
            "label_entropy": 0.0,
            "consensus_ratio": 1.0,
        },
        latest_candle_confidence=0.65,
        transition_summary={"continue_prob": 0.59, "pullback_prob": 0.21, "reversal_attempt_prob": 0.10, "fakeout_prob": 0.10},
    )

    assert result["projection_support"] is True
    assert result["memory_ok"] is True
    assert result["action"] == "BUY"


def test_projection_hypotheses_promote_reversal_release_from_sequence_and_memory() -> None:
    box_history = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "SELL",
            "bbox": [10.0, 40.0, 34.0, 120.0],
            "confidence": 0.83,
            "maturity": 0.80,
            "price_span": 80.0,
            "consolidation_score": 0.18,
        },
        {
            "sequence_index": 2,
            "box_type": "pullback",
            "direction": "SELL",
            "bbox": [38.0, 56.0, 60.0, 126.0],
            "confidence": 0.76,
            "maturity": 0.40,
            "price_span": 70.0,
            "consolidation_score": 0.34,
        },
        {
            "sequence_index": 3,
            "box_type": "reversal_base",
            "direction": "BUY",
            "bbox": [64.0, 62.0, 88.0, 132.0],
            "confidence": 0.81,
            "maturity": 0.60,
            "price_span": 70.0,
            "consolidation_score": 0.44,
        },
    ]
    sequence_state = {
        "continuation_probability": 0.43,
        "pullback_probability": 0.08,
        "reversal_probability": 0.30,
        "fakeout_probability": 0.19,
        "current_box": box_history[-1],
    }

    hypotheses = _build_next_box_hypotheses(
        box_history,
        sequence_state,
        {"plot_bbox": [0.0, 0.0, 240.0, 180.0]},
        market_state={"macro_trend": "BEAR"},
        memory_summary={
            "dominant_label": "BUY",
            "top_similarity": 0.78,
            "consensus_ratio": 1.0,
            "ambiguity": 0.0,
        },
        memory_episode_matches=[
            {"label": "BUY", "similarity": 0.78},
            {"label": "BUY", "similarity": 0.76},
        ],
    )

    assert hypotheses
    assert hypotheses[0]["direction"] == "BUY"
    assert hypotheses[0]["box_type"] == "impulse"
    assert hypotheses[0]["confidence"] >= 0.60
    assert hypotheses[0]["dominance_gap"] > 0.0
    assert "reversal_release" in str(hypotheses[0]["trigger"])


def test_chart_state_classifies_reversal_release_structure() -> None:
    current_box = {
        "box_type": "reversal_base",
        "direction": "BUY",
        "confidence": 0.81,
        "maturity": 0.60,
        "consolidation_score": 0.45,
    }
    projected_box = {
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.69,
        "dominance_gap": 0.11,
        "explanation": "impulse:BUY via reversal_release",
        "swing_state": {
            "recent_swing_direction": "SELL",
            "macro_swing_direction": "SELL",
            "macro_direction": "SELL",
            "swing_phase": "counter_macro_reversal",
            "summary": "counter_macro_reversal:SELL->SELL",
        },
    }
    chart_state = _build_chart_state(
        detections=[],
        local_ensemble={
            "ensemble": {
                "predicted_label": "BUY",
                "confidence": 0.65,
                "buy_prob": 0.65,
                "sell_prob": 0.35,
                "champion_model": "dinov2",
                "confirmer_model": "mobilenetv3",
                "disagreement": 0.12,
            }
        },
        reasoning_trace={"market_state": {"local_phase": "with_trend_pause", "macro_trend": "BEAR"}},
        chart_geometry={"body_height_pct": 0.49, "upper_wick_pct": 0.26, "lower_wick_pct": 0.24},
        sequence_state={
            "recent_colors": ["red", "red", "green", "red", "green"],
            "continuation_probability": 0.43,
            "reversal_probability": 0.30,
            "fakeout_probability": 0.19,
            "color_flip_rate": 0.62,
            "small_body_ratio": 0.12,
            "current_box": current_box,
            "primary_next_box": projected_box,
            "box_history": [
                {"direction": "SELL", "box_type": "impulse", "confidence": 0.80, "maturity": 0.8, "price_span": 90.0},
                {"direction": "SELL", "box_type": "pullback", "confidence": 0.75, "maturity": 0.4, "price_span": 60.0},
                current_box,
            ],
            "box_sequence_agreement": 0.47,
            "path_clarity": 0.59,
            "has_active_consolidation": False,
            "recent_box_consolidation": 0.33,
        },
    )

    assert chart_state["structure_setup"] == "reversal_release"
    assert chart_state["projection_bias_direction"] == "BUY"
    assert chart_state["projection_bias_confidence"] >= 0.50
    assert chart_state["swing_state"]["swing_phase"] == "counter_macro_reversal"


def test_forecast_releases_for_reversal_release_without_latest_candle_support() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state = {
        "direction": "BUY",
        "direction_probability": 0.64,
        "implied_3min_move_pct": 0.32,
        "entry_candle": {"body_pct": 0.54},
        "mcts": {"buy_prob": 0.64, "sell_prob": 0.36},
        "box_sequence_agreement": 0.48,
        "path_clarity": 0.62,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": True,
        "structure_setup": "reversal_release",
        "projection_dominance": 0.10,
        "projection_bias_confidence": 0.71,
        "projected_next_box": {"box_type": "impulse", "direction": "BUY", "confidence": 0.69},
    }

    result = reg.forecast_3m(
        chart_state,
        detections=[],
        memory_similarity=0.79,
        memory_direction="BUY",
        transition_summary={"continue_prob": 0.43, "pullback_prob": 0.08, "reversal_attempt_prob": 0.30, "fakeout_prob": 0.19},
        memory_summary={"top_similarity": 0.79, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "reversal_release"
    assert result["force_hold"] is False


def test_ensemble_projection_bias_survives_zero_latest_candle_confidence() -> None:
    gates = [
        SimpleNamespace(name=f"g{i}", score=0.92, pass_fail=True, detail={})
        for i in range(8)
    ]
    engine = EnsembleDecisionEngine(
        consensus_threshold=0.62,
        max_interval_pct=0.40,
        risk_min_pct=0.5,
        risk_max_pct=2.0,
        gates_pass_minimum=6,
        memory_veto_threshold=0.87,
    )

    result = engine.infer(
        rl_probs={"BUY": 0.61, "SELL": 0.27, "HOLD": 0.12},
        forecast={
            "q05": 0.05,
            "q50": 0.17,
            "q95": 0.25,
            "ad_indicator": 0.18,
            "poly_slope": 0.14,
            "execution_readiness": 0.66,
            "active_consolidation": 0.0,
            "structure_trade_ready": 1.0,
            "structure_setup": "reversal_release",
            "projected_box_direction": "BUY",
            "projected_box_confidence": 0.71,
            "projection_bias_confidence": 0.74,
            "projection_dominance": 0.10,
        },
        gate_outputs=gates,
        memory_bank_similarity=0.79,
        module_reliability={"cv_quality": 0.52, "structure_consistency": 0.91},
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "ambiguity": 0.0,
            "label_entropy": 0.0,
            "consensus_ratio": 1.0,
        },
        latest_candle_confidence=0.0,
        transition_summary={"continue_prob": 0.43, "pullback_prob": 0.08, "reversal_attempt_prob": 0.30, "fakeout_prob": 0.19},
    )

    assert result["projection_support"] is True
    assert result["action"] == "BUY"
    assert result["decision_state"] in {"PROJECTED", "CONFIRMED"}


def test_forecast_uses_projection_direction_when_projection_overrides_ensemble() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state = {
        "direction": "BUY",
        "direction_probability": 0.61,
        "implied_3min_move_pct": 0.34,
        "entry_candle": {"body_pct": 0.52},
        "mcts": {"buy_prob": 0.61, "sell_prob": 0.39},
        "box_sequence_agreement": 0.77,
        "path_clarity": 0.74,
        "projection_alignment": 0.0,
        "has_active_consolidation": False,
        "structure_trade_ready": True,
        "structure_setup": "impulse_chain",
        "projection_dominance": 0.12,
        "projection_bias_confidence": 0.71,
        "projected_next_box": {"box_type": "impulse", "direction": "SELL", "confidence": 0.74},
    }

    result = reg.forecast_3m(
        chart_state,
        detections=[],
        memory_similarity=0.81,
        memory_direction="SELL",
        transition_summary={"continue_prob": 0.62, "pullback_prob": 0.16, "reversal_attempt_prob": 0.12, "fakeout_prob": 0.10},
        memory_summary={"top_similarity": 0.81, "dominant_label": "SELL", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["projected_box_direction"] == "SELL"
    assert result["q50"] < 0.0
    assert result["poly_slope"] < 0.0
    assert result["force_hold"] is False


def test_latest_signal_state_reflects_post_cap_parse_quality() -> None:
    detections = [
        {"pattern": "latest_parse_quality", "confidence": 0.92, "overlay_confidence": 0.92},
        {"pattern": "latest_candle_sell", "confidence": 0.18, "overlay_confidence": 0.18},
        {"pattern": "latest_candle_buy", "confidence": 0.04, "overlay_confidence": 0.04},
    ]

    capped = _apply_parse_quality_cap_to_detections(
        detections,
        latest_candle_confidence=0.18,
        chart_geometry={"geometry_confidence": 0.40},
        sequence_state={"spacing_consistency": 0.0},
    )
    latest_signal_state = _extract_latest_signal_state(capped)

    assert latest_signal_state["latest_candle_direction"] == "SELL"
    assert latest_signal_state["latest_candle_confidence"] == 0.18
    assert abs(float(latest_signal_state["latest_parse_quality"]) - 0.342) < 1e-6
