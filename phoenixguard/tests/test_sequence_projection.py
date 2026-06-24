from __future__ import annotations
import pytest

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
from PIL import Image


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.decision.ensemble import EnsembleDecisionEngine
from phoenixguard.decision.skill_gates import CurriculumGates
from phoenixguard.memory.memory_features import (
    build_metric_profile,
    build_trajectory_signature,
    derive_entry_progression_profile,
)
from main import (
    MAX_SEQUENCE_HISTORY_DEPTH,
    adaptive_overlay_label_controls as _adaptive_overlay_label_controls,
    apply_parse_quality_cap_to_detections as _apply_parse_quality_cap_to_detections,
    apply_zone_memory_to_result as _apply_zone_memory_to_result,
    build_box_history as _build_box_history,
    build_chart_state as _build_chart_state,
    build_council_influence_profile as _build_council_influence_profile,
    build_council_sequence_summary as _build_council_sequence_summary,
    build_next_box_hypotheses as _build_next_box_hypotheses,
    build_projected_candle_candidates as _build_projected_candle_candidates,
    build_projection_chain_boxes as _build_projection_chain_boxes,
    build_render_config as _build_render_config,
    build_sequence_model_summary as _build_sequence_model_summary,
    choose_overlay_label_rect as _choose_overlay_label_rect,
    classify_swing_state as _classify_swing_state,
    default_projected_entry_level_norm as _default_projected_entry_level_norm,
    ensemble_base_probs as _ensemble_base_probs,
    enrich_next_box_hypotheses_with_projected_candles as _enrich_next_box_hypotheses_with_projected_candles,
    extract_latest_signal_state as _extract_latest_signal_state,
    fuse_transition_probabilities as _fuse_transition_probabilities,
    projected_box_path_anchors as _projected_box_path_anchors,
    rebuild_projection_synced_state as _rebuild_projection_synced_state,
    rect_overlap_area as _rect_overlap_area,
    sample_overlay_candle_palette as _sample_overlay_candle_palette,
    score_projected_box_with_council as _score_projected_box_with_council,
    should_relax_hold_veto as _should_relax_hold_veto,
    summarize_trend_regime as _summarize_trend_regime,
    build_model_council_html,
    draw_overlay,
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


def _forecast_3m(regressor: ImageFusionRegressor, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return cast(dict[str, Any], regressor.forecast_3m(*args, **kwargs))


def _infer(engine: EnsembleDecisionEngine, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return engine.infer(*args, **kwargs)


def _make_candle(
    x1: float,
    x2: float,
    y1: float,
    y2: float,
    *,
    green: bool,
    body: float = 0.16,
    upper_wick: float = 0.18,
    lower_wick: float = 0.20,
) -> dict[str, Any]:
    return {
        "bbox": [x1, y1, x2, y2],
        "parse_conf": 0.88,
        "body_height_pct": body,
        "upper_wick_pct": upper_wick,
        "lower_wick_pct": lower_wick,
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


def test_trend_regime_identifies_pullback_inside_buy_trend() -> None:
    box_history: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.78,
            "maturity": 0.72,
            "price_span": 84.0,
            "consolidation_score": 0.14,
        },
        {
            "sequence_index": 2,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.80,
            "maturity": 0.76,
            "price_span": 92.0,
            "consolidation_score": 0.12,
        },
        {
            "sequence_index": 3,
            "box_type": "pullback",
            "direction": "BUY",
            "confidence": 0.74,
            "maturity": 0.48,
            "price_span": 38.0,
            "consolidation_score": 0.34,
        },
    ]

    regime = _summarize_trend_regime(
        box_history,
        {
            "current_box": box_history[-1],
            "recent_colors": ["green", "green", "red", "green", "red"],
            "fakeout_probability": 0.10,
        },
    )

    assert regime["trend_direction"] == "BUY"
    assert float(regime["trend_strength"]) >= 0.45
    assert regime["pullback_active"] is True
    assert float(regime["continuation_reload_score"]) >= 0.50
    assert str(regime["trend_phase"]) == "trend_pullback"


def test_trend_regime_flags_exhausted_buy_trend_reversal_watch() -> None:
    box_history: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.80,
            "maturity": 0.78,
            "price_span": 88.0,
            "consolidation_score": 0.12,
            "dominant_wick": "lower",
        },
        {
            "sequence_index": 2,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.84,
            "maturity": 0.82,
            "price_span": 96.0,
            "consolidation_score": 0.10,
            "dominant_wick": "upper",
        },
        {
            "sequence_index": 3,
            "box_type": "pullback",
            "direction": "BUY",
            "confidence": 0.74,
            "maturity": 0.62,
            "price_span": 54.0,
            "consolidation_score": 0.26,
            "dominant_wick": "upper",
        },
    ]

    regime = _summarize_trend_regime(
        box_history,
        {
            "current_box": box_history[-1],
            "recent_colors": ["green", "green", "red", "red", "red"],
            "recent_body_pcts": [0.30, 0.28, 0.22, 0.18, 0.16],
            "recent_upper_wicks": [0.18, 0.22, 0.56, 0.60, 0.64],
            "recent_lower_wicks": [0.14, 0.12, 0.10, 0.08, 0.08],
            "fakeout_probability": 0.12,
        },
    )

    assert regime["trend_direction"] == "BUY"
    assert float(regime["breakout_failure_risk"]) >= 0.48
    assert float(regime["reversal_risk"]) >= 0.32
    assert str(regime["trend_phase"]) == "reversal_watch"


def test_swing_state_respects_market_macro_direction_for_countertrend_sell_push() -> None:
    box_history: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "SELL",
            "confidence": 0.80,
            "maturity": 0.78,
            "price_span": 88.0,
            "consolidation_score": 0.12,
        },
        {
            "sequence_index": 2,
            "box_type": "impulse",
            "direction": "SELL",
            "confidence": 0.84,
            "maturity": 0.82,
            "price_span": 96.0,
            "consolidation_score": 0.10,
        },
        {
            "sequence_index": 3,
            "box_type": "impulse",
            "direction": "SELL",
            "confidence": 0.82,
            "maturity": 0.84,
            "price_span": 92.0,
            "consolidation_score": 0.10,
        },
    ]

    swing_state = _classify_swing_state(
        box_history,
        box_history[-1],
        market_state={"macro_trend": "BULL"},
    )

    assert swing_state["macro_direction"] == "BUY"
    assert swing_state["macro_swing_direction"] == "BUY"
    assert swing_state["recent_swing_direction"] == "SELL"
    assert swing_state["swing_phase"] == "macro_pullback"


def test_projected_candles_follow_recent_chart_cadence() -> None:
    visible_candles = [
        _make_candle(100, 106, 116, 178, green=False, body=0.18),
        _make_candle(110, 116, 112, 172, green=True, body=0.20),
        _make_candle(120, 126, 108, 170, green=True, body=0.22),
        _make_candle(130, 136, 104, 164, green=True, body=0.24),
        _make_candle(140, 146, 100, 160, green=True, body=0.28),
        _make_candle(150, 156, 96, 156, green=True, body=0.30),
    ]
    projected = _build_projected_candle_candidates(
        projected_box={
            "bbox": [162.0, 88.0, 246.0, 170.0],
            "direction": "BUY",
            "confidence": 0.82,
            "box_type": "impulse",
            "trigger": "breakout",
        },
        detections=[{"pattern": "next_move_large", "confidence": 0.74}],
        chart_state={
            "direction": "BUY",
            "projection_bias_direction": "BUY",
            "projection_bias_confidence": 0.82,
        },
        sequence_state={
            "all_visible_candles": visible_candles,
            "body_mean_pct": 0.24,
        },
        local_ensemble={"ensemble": {"sequence_task_consensus": {}}},
    )

    assert len(projected) >= 3
    visible_widths = [
        float(candle["bbox"][2]) - float(candle["bbox"][0])
        for candle in visible_candles
    ]
    visible_spacing = [
        (float(right["bbox"][0]) + float(right["bbox"][2])) * 0.5 - (float(left["bbox"][0]) + float(left["bbox"][2])) * 0.5
        for left, right in zip(visible_candles[:-1], visible_candles[1:])
    ]
    projected_widths = [
        float(candle["body_bbox"][2]) - float(candle["body_bbox"][0])
        for candle in projected
    ]
    projected_spacing = [
        float(right["center_x"]) - float(left["center_x"])
        for left, right in zip(projected[:-1], projected[1:])
    ]

    assert max(projected_widths) <= float(np.median(np.asarray(visible_widths, dtype=np.float32))) * 1.45
    assert min(projected_spacing) >= float(np.median(np.asarray(visible_spacing, dtype=np.float32))) * 0.75
    assert float(projected[0]["center_x"]) >= 160.0


def test_projected_box_path_anchors_stay_neutral_for_unknown_direction() -> None:
    assert _default_projected_entry_level_norm("impulse", "HOLD") == 0.5

    anchors = _projected_box_path_anchors(
        projected_type="impulse",
        projected_direction="HOLD",
        projected_confidence=0.84,
        projected_role="unknown",
        trigger="unknown",
        seed_level_norm=None,
    )

    assert anchors
    assert all(abs(float(level) - 0.5) < 1e-6 for _, level in anchors)


def test_projected_pullback_box_shows_internal_counter_move_and_reclaim() -> None:
    projected = _build_projected_candle_candidates(
        projected_box={
            "bbox": [180.0, 90.0, 260.0, 170.0],
            "direction": "BUY",
            "confidence": 0.76,
            "box_type": "pullback",
            "trigger": "pause_reset",
        },
        detections=[{"pattern": "next_move_medium", "confidence": 0.68}],
        chart_state={
            "direction": "BUY",
            "projection_bias_direction": "BUY",
            "projection_bias_confidence": 0.76,
        },
        sequence_state={
            "all_visible_candles": [
                _make_candle(110, 116, 118, 178, green=True, body=0.20),
                _make_candle(120, 126, 114, 172, green=True, body=0.22),
                _make_candle(130, 136, 110, 168, green=True, body=0.24),
            ],
            "body_mean_pct": 0.22,
        },
        local_ensemble={"ensemble": {"sequence_task_consensus": {}}},
    )

    directions = [str(candle.get("candle_direction", "")) for candle in projected]
    close_levels = [float(candle.get("close_level_norm", 0.5)) for candle in projected]
    assert "SELL" in directions
    assert "BUY" in directions
    assert close_levels[-1] > close_levels[len(close_levels) // 2]
    assert float(projected[-1]["center_x"]) - float(projected[0]["center_x"]) >= 44.0


def test_council_influence_profile_dampens_contradictory_sequence_direction() -> None:
    local_ensemble: dict[str, Any] = {
        "ensemble": {
            "predicted_label": "BUY",
            "confidence": 0.56,
            "consensus_ratio": 0.67,
            "disagreement": 0.04,
            "router_direction": "BUY",
            "router_strength": 0.36,
            "router_regime_confidence": 0.62,
            "sequence_task_consensus": {
                "projection_direction": {"value": "SELL", "confidence": 1.0, "support": 2.76, "n_models": 6},
                "current_box_direction": {"value": "SELL", "confidence": 1.0, "support": 2.40, "n_models": 6},
                "next_box_direction": {"value": "SELL", "confidence": 1.0, "support": 2.67, "n_models": 6},
            },
        }
    }

    council_sequence = _build_council_sequence_summary(local_ensemble)
    assert float(council_sequence["projection_direction"]["calibration_scale"]) < 1.0
    assert float(council_sequence["projection_direction"]["confidence"]) < float(
        council_sequence["projection_direction"]["raw_confidence"]
    )

    profile = _build_council_influence_profile(local_ensemble, council_sequence=council_sequence)

    assert profile["bias_direction"] == "BUY"
    assert profile["projection_direction"] == "BUY"
    assert float(profile["projection_confidence"]) > 0.0


def test_council_influence_profile_rejects_weak_opposing_router_sequence_direction() -> None:
    local_ensemble: dict[str, Any] = {
        "ensemble": {
            "predicted_label": "BUY",
            "confidence": 0.54,
            "consensus_ratio": 0.83,
            "disagreement": 0.055,
            "router_direction": "SELL",
            "router_strength": 0.31,
            "router_regime_confidence": 0.52,
            "sequence_task_consensus": {
                "projection_direction": {"value": "SELL", "confidence": 1.0, "support": 2.96, "n_models": 6},
                "current_box_direction": {"value": "SELL", "confidence": 1.0, "support": 2.83, "n_models": 6},
                "next_box_direction": {"value": "SELL", "confidence": 1.0, "support": 2.67, "n_models": 6},
            },
        }
    }

    council_sequence = _build_council_sequence_summary(local_ensemble)

    assert float(council_sequence["projection_direction"]["calibration_scale"]) < 0.5
    assert float(council_sequence["projection_direction"]["confidence"]) < 0.5

    profile = _build_council_influence_profile(local_ensemble, council_sequence=council_sequence)

    assert profile["bias_direction"] == "BUY"
    assert profile["projection_direction"] == "BUY"


def test_ensemble_base_probs_dampen_weak_opposing_projection_and_council_bias() -> None:
    probs = _ensemble_base_probs(
        {
            "ensemble": {
                "buy_prob": 0.586,
                "sell_prob": 0.414,
                "predicted_label": "BUY",
                "confidence": 0.586,
                "disagreement": 0.10,
                "consensus_ratio": 1.0,
            }
        },
        chart_state={
            "path_clarity": 0.77,
            "continuation_probability": 0.23,
            "has_active_consolidation": False,
            "structure_trade_ready": False,
            "projected_next_box": {"direction": "SELL", "confidence": 0.69},
            "projection_bias_confidence": 0.59,
            "projection_dominance": 0.03,
            "council_bias_direction": "SELL",
            "council_bias_confidence": 0.51,
            "council_projection_direction": "SELL",
            "council_projection_confidence": 0.66,
            "council_structure_setup": "none",
            "council_structure_confidence": 0.0,
            "council_alignment_score": 0.12,
            "council_influence_score": 0.22,
            "council_router_direction": "SELL",
            "council_router_strength": 0.32,
        },
        memory_summary={"mixed_labels": True, "consensus_ratio": 0.20, "dominant_label": "HOLD"},
    )

    assert probs["BUY"] > probs["SELL"]


def test_projected_candles_ignore_contradictory_council_direction_override() -> None:
    visible_candles = [
        _make_candle(100, 106, 116, 178, green=False, body=0.18),
        _make_candle(110, 116, 112, 172, green=True, body=0.20),
        _make_candle(120, 126, 108, 170, green=True, body=0.22),
        _make_candle(130, 136, 104, 164, green=True, body=0.24),
        _make_candle(140, 146, 100, 160, green=True, body=0.28),
        _make_candle(150, 156, 96, 156, green=True, body=0.30),
    ]
    projected = _build_projected_candle_candidates(
        projected_box={
            "bbox": [162.0, 88.0, 246.0, 170.0],
            "direction": "BUY",
            "confidence": 0.82,
            "box_type": "impulse",
            "trigger": "breakout",
        },
        detections=[{"pattern": "next_move_large", "confidence": 0.74}],
        chart_state={
            "direction": "BUY",
            "projection_bias_direction": "BUY",
            "projection_bias_confidence": 0.82,
        },
        sequence_state={
            "all_visible_candles": visible_candles,
            "body_mean_pct": 0.24,
        },
        local_ensemble={
            "ensemble": {
                "predicted_label": "BUY",
                "confidence": 0.56,
                "consensus_ratio": 0.67,
                "disagreement": 0.04,
                "router_direction": "BUY",
                "router_strength": 0.36,
                "router_regime_confidence": 0.62,
                "sequence_task_consensus": {
                    "projection_direction": {"value": "SELL", "confidence": 1.0, "support": 2.76, "n_models": 6},
                    "next_box_direction": {"value": "SELL", "confidence": 1.0, "support": 2.67, "n_models": 6},
                },
            }
        },
    )

    assert projected
    assert projected[0]["direction"] == "BUY"
    assert projected[0]["candle_direction"] == "BUY"


def test_projected_candles_ignore_weak_opposing_router_sequence_override() -> None:
    visible_candles = [
        _make_candle(100, 106, 116, 178, green=False, body=0.18),
        _make_candle(110, 116, 112, 172, green=True, body=0.20),
        _make_candle(120, 126, 108, 170, green=True, body=0.22),
        _make_candle(130, 136, 104, 164, green=True, body=0.24),
        _make_candle(140, 146, 100, 160, green=True, body=0.28),
        _make_candle(150, 156, 96, 156, green=True, body=0.30),
    ]
    projected = _build_projected_candle_candidates(
        projected_box={
            "bbox": [162.0, 88.0, 246.0, 170.0],
            "direction": "BUY",
            "confidence": 0.82,
            "box_type": "impulse",
            "trigger": "breakout",
        },
        detections=[{"pattern": "next_move_large", "confidence": 0.74}],
        chart_state={
            "direction": "BUY",
            "projection_bias_direction": "BUY",
            "projection_bias_confidence": 0.82,
        },
        sequence_state={
            "all_visible_candles": visible_candles,
            "body_mean_pct": 0.24,
        },
        local_ensemble={
            "ensemble": {
                "predicted_label": "BUY",
                "confidence": 0.54,
                "consensus_ratio": 0.83,
                "disagreement": 0.055,
                "router_direction": "SELL",
                "router_strength": 0.31,
                "router_regime_confidence": 0.52,
                "sequence_task_consensus": {
                    "projection_direction": {"value": "SELL", "confidence": 1.0, "support": 2.96, "n_models": 6},
                    "next_box_direction": {"value": "SELL", "confidence": 1.0, "support": 2.67, "n_models": 6},
                },
            }
        },
    )

    assert projected
    assert projected[0]["direction"] == "BUY"
    assert projected[0]["candle_direction"] == "BUY"


def test_projected_candles_preserve_explicit_box_direction_against_sell_council() -> None:
    visible_candles = [
        _make_candle(100, 106, 116, 178, green=False, body=0.18),
        _make_candle(110, 116, 112, 172, green=False, body=0.20),
        _make_candle(120, 126, 108, 170, green=False, body=0.22),
        _make_candle(130, 136, 104, 164, green=False, body=0.24),
        _make_candle(140, 146, 100, 160, green=False, body=0.28),
        _make_candle(150, 156, 96, 156, green=False, body=0.30),
    ]
    projected = _build_projected_candle_candidates(
        projected_box={
            "bbox": [162.0, 88.0, 246.0, 170.0],
            "direction": "BUY",
            "confidence": 0.66,
            "box_type": "reversal_base",
            "trigger": "trend_exhaustion_reversal",
        },
        detections=[{"pattern": "next_move_medium", "confidence": 0.62}],
        chart_state={
            "direction": "SELL",
            "projection_bias_direction": "BUY",
            "projection_bias_confidence": 0.66,
        },
        sequence_state={
            "all_visible_candles": visible_candles,
            "body_mean_pct": 0.24,
        },
        local_ensemble={
            "ensemble": {
                "predicted_label": "SELL",
                "confidence": 0.57,
                "consensus_ratio": 1.0,
                "disagreement": 0.04,
                "router_direction": "SELL",
                "router_strength": 0.45,
                "router_regime_confidence": 0.68,
                "sequence_task_consensus": {
                    "projection_direction": {"value": "SELL", "confidence": 1.0, "support": 2.76, "n_models": 6},
                    "next_box_direction": {"value": "SELL", "confidence": 1.0, "support": 2.67, "n_models": 6},
                },
            }
        },
    )

    assert projected
    assert projected[0]["direction"] == "BUY"


def test_projected_box_council_rerank_does_not_penalize_unaligned_base_case() -> None:
    reranked, alignment = _score_projected_box_with_council(
        {
            "direction": "BUY",
            "box_type": "impulse",
            "trigger": "breakout",
            "confidence": 0.66,
        },
        projected_candles=[],
        council_sequence={},
        chart_state={
            "direction": "BUY",
            "projection_bias_direction": "BUY",
        },
    )

    assert reranked == 0.66
    assert float(alignment["alignment"]) == 0.0


def test_projection_enrichment_keeps_stronger_buy_hypothesis_ahead_of_bearish_fakeout() -> None:
    current_box: dict[str, Any] = {
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.78,
        "maturity": 0.72,
        "consolidation_score": 0.12,
        "bbox": [66.0, 74.0, 92.0, 138.0],
    }
    sequence_state: dict[str, Any] = {
        "recent_colors": ["green", "green", "red", "green", "red"],
        "continuation_probability": 0.56,
        "reversal_probability": 0.18,
        "fakeout_probability": 0.12,
        "color_flip_rate": 0.36,
        "small_body_ratio": 0.14,
        "body_std_pct": 0.06,
        "spacing_consistency": 0.68,
        "current_box": current_box,
        "box_history": [
            {"direction": "BUY", "box_type": "impulse", "confidence": 0.74, "maturity": 0.70, "bbox": [10.0, 88.0, 34.0, 148.0]},
            {"direction": "BUY", "box_type": "pullback", "confidence": 0.69, "maturity": 0.44, "bbox": [38.0, 96.0, 60.0, 150.0]},
            current_box,
        ],
        "box_sequence_agreement": 0.61,
        "path_clarity": 0.58,
        "has_active_consolidation": False,
        "recent_box_consolidation": 0.12,
        "body_mean_pct": 0.24,
        "all_visible_candles": [
            _make_candle(72, 78, 92, 148, green=True, body=0.24),
            _make_candle(82, 88, 88, 142, green=True, body=0.22),
            _make_candle(92, 98, 96, 152, green=False, body=0.20),
        ],
    }
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "projection_bias_direction": "BUY",
        "projection_bias_confidence": 0.72,
        "projection_dominance": 0.08,
        "continuation_probability": 0.56,
        "reversal_probability": 0.18,
        "fakeout_probability": 0.12,
        "path_clarity": 0.58,
    }
    local_ensemble: dict[str, Any] = {
        "ensemble": {
            "predicted_label": "BUY",
            "confidence": 0.66,
            "buy_prob": 0.66,
            "sell_prob": 0.34,
            "disagreement": 0.10,
            "sequence_task_consensus": {
                "next_box_direction": {"value": "SELL", "confidence": 0.92},
                "next_box_type": {"value": "fakeout", "confidence": 0.90},
                "trigger": {"value": "counter_fakeout", "confidence": 0.88},
            },
        }
    }

    enriched = _enrich_next_box_hypotheses_with_projected_candles(
        [
            {
                "rank": 1,
                "box_type": "reversal_base",
                "direction": "BUY",
                "confidence": 0.67,
                "dominance_gap": 0.04,
                "path_clarity": 0.58,
                "trigger": "trend_reversal_watch",
                "bbox": [98.0, 62.0, 126.0, 126.0],
            },
            {
                "rank": 2,
                "box_type": "fakeout",
                "direction": "SELL",
                "confidence": 0.65,
                "dominance_gap": 0.02,
                "path_clarity": 0.58,
                "trigger": "counter_fakeout",
                "bbox": [98.0, 78.0, 126.0, 144.0],
            },
        ],
        detections=[],
        chart_state=chart_state,
        sequence_state=sequence_state,
        local_ensemble=local_ensemble,
    )

    assert enriched[0]["direction"] == "BUY"
    assert enriched[0]["rank"] == 1
    assert enriched[1]["rank"] == 2
    assert float(enriched[0]["dominance_gap"]) > 0.0


def test_projection_hypotheses_reclaim_trend_after_pullback() -> None:
    box_history: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "BUY",
            "bbox": [20.0, 112.0, 46.0, 192.0],
            "confidence": 0.79,
            "maturity": 0.76,
            "price_span": 80.0,
            "consolidation_score": 0.14,
        },
        {
            "sequence_index": 2,
            "box_type": "pullback",
            "direction": "BUY",
            "bbox": [52.0, 124.0, 74.0, 182.0],
            "confidence": 0.71,
            "maturity": 0.42,
            "price_span": 58.0,
            "consolidation_score": 0.32,
        },
    ]
    sequence_state: dict[str, Any] = {
        "continuation_probability": 0.58,
        "pullback_probability": 0.20,
        "reversal_probability": 0.10,
        "fakeout_probability": 0.12,
        "current_box": box_history[-1],
        "trend_regime": {
            "trend_direction": "BUY",
            "trend_strength": 0.68,
            "pullback_active": True,
            "pullback_depth": 0.64,
            "continuation_reload_score": 0.74,
            "impulse_extension": 0.22,
            "reversal_risk": 0.18,
            "trend_phase": "trend_pullback",
        },
    }

    hypotheses = _build_next_box_hypotheses(
        box_history,
        sequence_state,
        {"plot_bbox": [0.0, 0.0, 260.0, 200.0]},
        market_state={"macro_trend": "BULL"},
    )

    assert hypotheses
    assert hypotheses[0]["direction"] == "BUY"
    assert hypotheses[0]["box_type"] == "impulse"
    assert "trend_pullback_reclaim" in str(hypotheses[0]["trigger"])


def test_projection_hypotheses_flip_sell_on_exhausted_buy_trend() -> None:
    box_history: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "BUY",
            "bbox": [24.0, 120.0, 48.0, 198.0],
            "confidence": 0.80,
            "maturity": 0.78,
            "price_span": 78.0,
            "consolidation_score": 0.12,
            "dominant_wick": "lower",
        },
        {
            "sequence_index": 2,
            "box_type": "impulse",
            "direction": "BUY",
            "bbox": [54.0, 104.0, 82.0, 182.0],
            "confidence": 0.84,
            "maturity": 0.82,
            "price_span": 86.0,
            "consolidation_score": 0.10,
            "dominant_wick": "upper",
        },
        {
            "sequence_index": 3,
            "box_type": "pullback",
            "direction": "BUY",
            "bbox": [88.0, 112.0, 116.0, 186.0],
            "confidence": 0.74,
            "maturity": 0.62,
            "price_span": 58.0,
            "consolidation_score": 0.24,
            "dominant_wick": "upper",
        },
    ]
    sequence_state: dict[str, Any] = {
        "continuation_probability": 0.52,
        "pullback_probability": 0.18,
        "reversal_probability": 0.20,
        "fakeout_probability": 0.10,
        "current_box": box_history[-1],
        "recent_colors": ["green", "green", "red", "red", "red"],
        "recent_body_pcts": [0.30, 0.28, 0.22, 0.18, 0.16],
        "recent_upper_wicks": [0.18, 0.22, 0.56, 0.60, 0.64],
        "recent_lower_wicks": [0.14, 0.12, 0.10, 0.08, 0.08],
    }

    hypotheses = _build_next_box_hypotheses(
        box_history,
        sequence_state,
        {"plot_bbox": [0.0, 0.0, 280.0, 220.0]},
        market_state={"macro_trend": "BULL"},
    )

    assert hypotheses
    assert hypotheses[0]["direction"] == "SELL"
    assert hypotheses[0]["box_type"] == "reversal_base"
    assert "trend_exhaustion_reversal" in str(hypotheses[0]["trigger"])


def test_overlay_candle_palette_follows_chart_colors() -> None:
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[6:18, 4:9] = np.array([92, 214, 104], dtype=np.uint8)
    image[5:19, 16:21] = np.array([236, 82, 194], dtype=np.uint8)

    palette = _sample_overlay_candle_palette(
        image,
        {
            "all_visible_candles": [
                {"bbox": [4.0, 6.0, 9.0, 18.0], "candle_color_green": 1.0},
                {"bbox": [16.0, 5.0, 21.0, 19.0], "candle_color_green": 0.0},
            ]
        },
    )

    bull = palette["bull"]
    bear = palette["bear"]
    assert bull[1] > bull[0] and bull[1] > bull[2]
    assert bear[0] > bear[1] and bear[0] > bear[2]
    assert bear[2] > bear[1]


def test_adaptive_overlay_label_controls_reduce_budget_on_crowded_chart() -> None:
    controls = _adaptive_overlay_label_controls(
        [
            (4.0, 8.0, 28.0, 26.0),
            (18.0, 10.0, 42.0, 28.0),
            (34.0, 12.0, 58.0, 30.0),
            (50.0, 14.0, 74.0, 32.0),
            (66.0, 16.0, 90.0, 34.0),
            (82.0, 18.0, 106.0, 36.0),
        ],
        (0.0, 0.0, 112.0, 44.0),
        requested_budget=12,
        presentation_mode=True,
    )

    assert int(controls["budget"]) < 12
    assert float(controls["crowd_score"]) > 0.3
    assert float(controls["detection_confidence_floor"]) > 0.45


def test_choose_overlay_label_rect_avoids_existing_label_collisions() -> None:
    rect = _choose_overlay_label_rect(
        text_w=44.0,
        text_h=12.0,
        anchor_x=18.0,
        anchor_y=18.0,
        image_size=(120.0, 72.0),
        occupied_rects=[(4.0, 4.0, 110.0, 24.0)],
        obstacle_rects=[(12.0, 18.0, 42.0, 42.0)],
        prefer_above=True,
        crowd_score=0.72,
    )

    assert rect is not None
    assert _rect_overlap_area(rect, (4.0, 4.0, 110.0, 24.0)) == 0.0
    assert rect[1] > 18.0


def test_history_plus_projection_overlay_skips_debug_geometry_frames() -> None:
    base = Image.new("RGB", (64, 48), color=(0, 0, 0))
    chart_geometry = {
        "plot_bbox": [0.0, 0.0, 63.0, 47.0],
        "plot_inner_bbox": [0.0, 0.0, 63.0, 47.0],
        "latest_sequence_bbox": [20.0, 12.0, 36.0, 32.0],
    }
    history_box: dict[str, Any] = {
        "sequence_index": 3,
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.82,
        "bbox": [18.0, 14.0, 36.0, 30.0],
    }

    presentation = draw_overlay(
        base,
        [],
        [],
        overlay_mode="history-plus-projection",
        chart_structure={
            "chart_geometry": chart_geometry,
            "box_history": [history_box],
            "current_box": history_box,
            "next_box_hypotheses": [],
        },
    )
    debug = draw_overlay(
        base,
        [],
        [],
        overlay_mode="debug-all",
        chart_structure={
            "chart_geometry": chart_geometry,
            "box_history": [history_box],
            "current_box": history_box,
            "next_box_hypotheses": [],
        },
    )

    assert presentation.getpixel((0, 0)) == (0, 0, 0)
    assert debug.getpixel((0, 0)) != (0, 0, 0)


def test_history_plus_projection_overlay_keeps_history_boxes_as_outlines() -> None:
    base = Image.new("RGB", (80, 60), color=(0, 0, 0))
    history_boxes: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.78,
            "bbox": [4.0, 22.0, 14.0, 34.0],
        },
        {
            "sequence_index": 2,
            "box_type": "pullback",
            "direction": "SELL",
            "confidence": 0.74,
            "bbox": [18.0, 21.0, 28.0, 33.0],
        },
        {
            "sequence_index": 3,
            "box_type": "balance",
            "direction": "BUY",
            "confidence": 0.72,
            "bbox": [32.0, 20.0, 42.0, 32.0],
        },
        {
            "sequence_index": 4,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.80,
            "bbox": [46.0, 18.0, 56.0, 30.0],
        },
        {
            "sequence_index": 5,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.86,
            "bbox": [60.0, 16.0, 72.0, 28.0],
        },
    ]

    presentation = draw_overlay(
        base,
        [],
        [],
        overlay_mode="history-plus-projection",
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 79.0, 59.0]},
            "box_history": history_boxes,
            "current_box": history_boxes[-1],
            "next_box_hypotheses": [],
        },
    )
    draw_overlay(
        base,
        [],
        [],
        overlay_mode="debug-all",
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 79.0, 59.0]},
            "box_history": history_boxes,
            "current_box": history_boxes[-1],
            "next_box_hypotheses": [],
        },
    )

    assert presentation.getpixel((4, 22)) != (0, 0, 0)
    assert presentation.getpixel((8, 26)) == (0, 0, 0)


def test_history_plus_projection_overlay_suppresses_projected_candles() -> None:
    base = Image.new("RGB", (72, 56), color=(0, 0, 0))
    projection_box: dict[str, Any] = {
        "rank": 1,
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.88,
        "bbox": [40.0, 12.0, 60.0, 40.0],
        "projected_candles": [
            {
                "direction": "BUY",
                "confidence": 0.86,
                "center_x": 50.0,
                "body_bbox": [47.0, 20.0, 53.0, 30.0],
                "wick_top": 16.0,
                "wick_bottom": 36.0,
            }
        ],
    }

    overlay = draw_overlay(
        base,
        [],
        [],
        overlay_mode="history-plus-projection",
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 71.0, 55.0]},
            "box_history": [],
            "current_box": {},
            "next_box_hypotheses": [projection_box],
        },
    )

    assert overlay.getpixel((50, 25)) == (0, 0, 0)
    assert overlay.getpixel((50, 18)) == (0, 0, 0)


def test_projection_overlay_extra_draws_projected_boxes_and_candles_in_history_view() -> None:
    base = Image.new("RGB", (72, 56), color=(0, 0, 0))
    projection_box: dict[str, Any] = {
        "rank": 1,
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.88,
        "bbox": [40.0, 12.0, 60.0, 40.0],
        "projected_candles": [
            {
                "direction": "BUY",
                "confidence": 0.86,
                "center_x": 50.0,
                "body_bbox": [47.0, 20.0, 53.0, 30.0],
                "wick_top": 16.0,
                "wick_bottom": 36.0,
            }
        ],
    }

    overlay = draw_overlay(
        base,
        [],
        [],
        overlay_mode="history-boxes",
        vision_extras=["projection-overlay"],
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 71.0, 55.0]},
            "box_history": [],
            "current_box": {},
            "next_box_hypotheses": [projection_box],
        },
    )

    assert overlay.getpixel((40, 12)) != (0, 0, 0)
    assert overlay.getpixel((50, 25)) != (0, 0, 0)


def test_projection_chain_builds_four_future_boxes() -> None:
    visible_candles = [
        _make_candle(100, 106, 126, 186, green=False, body=0.18),
        _make_candle(110, 116, 122, 180, green=True, body=0.20),
        _make_candle(120, 126, 118, 176, green=True, body=0.22),
        _make_candle(130, 136, 114, 172, green=True, body=0.24),
        _make_candle(140, 146, 110, 168, green=True, body=0.26),
        _make_candle(150, 156, 106, 164, green=True, body=0.28),
    ]
    box_history: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.78,
            "maturity": 0.72,
            "bbox": [96.0, 116.0, 126.0, 186.0],
            "consolidation_score": 0.14,
        },
        {
            "sequence_index": 2,
            "box_type": "pullback",
            "direction": "BUY",
            "confidence": 0.70,
            "maturity": 0.46,
            "bbox": [130.0, 124.0, 158.0, 188.0],
            "consolidation_score": 0.32,
        },
        {
            "sequence_index": 3,
            "box_type": "impulse",
            "direction": "BUY",
            "confidence": 0.82,
            "maturity": 0.76,
            "bbox": [162.0, 100.0, 192.0, 170.0],
            "consolidation_score": 0.16,
        },
    ]
    sequence_state: dict[str, Any] = {
        "all_visible_candles": visible_candles,
        "body_mean_pct": 0.24,
        "box_history": box_history,
        "current_box": box_history[-1],
        "continuation_probability": 0.64,
        "pullback_probability": 0.18,
        "reversal_probability": 0.10,
        "fakeout_probability": 0.08,
        "box_sequence_agreement": 0.74,
        "path_clarity": 0.71,
        "recent_box_consolidation": 0.18,
        "spacing_consistency": 0.66,
        "color_flip_rate": 0.22,
        "small_body_ratio": 0.12,
        "body_std_pct": 0.05,
    }
    chart_geometry: dict[str, Any] = {"plot_bbox": [0.0, 0.0, 360.0, 220.0], "body_height_pct": 0.56, "geometry_confidence": 0.84}
    base_hypotheses = _build_next_box_hypotheses(
        box_history,
        sequence_state,
        chart_geometry,
        market_state={"macro_trend": "BULL"},
    )
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "projection_bias_direction": "BUY",
        "projection_bias_confidence": 0.78,
        "projection_dominance": 0.10,
        "structure_setup": "impulse_chain",
    }
    enriched = _enrich_next_box_hypotheses_with_projected_candles(
        base_hypotheses,
        detections=[{"pattern": "next_move_large", "confidence": 0.74}],
        chart_state=chart_state,
        sequence_state=sequence_state,
        local_ensemble={"ensemble": {"sequence_task_consensus": {}}},
    )

    chain = _build_projection_chain_boxes(
        base_hypotheses=enriched,
        detections=[{"pattern": "next_move_large", "confidence": 0.74}],
        chart_state=chart_state,
        sequence_state=sequence_state,
        chart_geometry=chart_geometry,
        local_ensemble={"ensemble": {"sequence_task_consensus": {}}},
        market_state={"macro_trend": "BULL"},
        depth=4,
    )

    assert len(chain) == 4
    assert [int(box["projection_step"]) for box in chain] == [1, 2, 3, 4]
    centers = [float(box["bbox"][0] + box["bbox"][2]) * 0.5 for box in chain]
    assert centers == sorted(centers)
    assert all(cast(list[dict[str, Any]], box.get("projected_candles", [])) for box in chain)
    assert abs(float(chain[1].get("entry_level_norm", 0.5)) - float(chain[0].get("exit_level_norm", 0.5))) <= 0.18
    assert any(str(box.get("box_type", "")) == "pullback" for box in chain)


def test_history_plus_projection_overlay_suppresses_projection_chain_boxes() -> None:
    base = Image.new("RGB", (112, 64), color=(0, 0, 0))
    projection_chain: list[dict[str, Any]] = [
        {"rank": 1, "projection_step": 1, "box_type": "impulse", "direction": "BUY", "confidence": 0.84, "bbox": [36.0, 22.0, 46.0, 36.0], "projected_candles": []},
        {"rank": 2, "projection_step": 2, "box_type": "pullback", "direction": "BUY", "confidence": 0.78, "bbox": [50.0, 18.0, 60.0, 32.0], "projected_candles": []},
        {"rank": 3, "projection_step": 3, "box_type": "impulse", "direction": "BUY", "confidence": 0.74, "bbox": [64.0, 14.0, 74.0, 28.0], "projected_candles": []},
        {"rank": 4, "projection_step": 4, "box_type": "balance", "direction": "BUY", "confidence": 0.70, "bbox": [78.0, 10.0, 88.0, 24.0], "projected_candles": []},
    ]

    overlay = draw_overlay(
        base,
        [],
        [],
        overlay_mode="history-plus-projection",
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 111.0, 63.0]},
            "box_history": [],
            "current_box": {},
            "next_box_hypotheses": [],
            "projection_chain_boxes": projection_chain,
        },
    )

    assert overlay.getpixel((78, 10)) == (0, 0, 0)
    assert overlay.getpixel((88, 24)) == (0, 0, 0)


def test_yolo_only_overlay_draws_raw_yolo_boxes_without_structure_noise() -> None:
    base = Image.new("RGB", (96, 64), color=(0, 0, 0))

    overlay = draw_overlay(
        base,
        [
            {"pattern": "ascending_triangle", "confidence": 0.82, "bbox": [10.0, 12.0, 24.0, 28.0]},
            {"pattern": "latest_candle_buy", "confidence": 0.90, "bbox": [34.0, 12.0, 48.0, 28.0]},
            {"pattern": "buy_memory_bias", "confidence": 0.88, "bbox": [0.0, 0.0, 95.0, 63.0]},
        ],
        [{"type": "support", "price": 0.5}],
        user_zones=[{"bbox": [8.0, 42.0, 20.0, 54.0], "kind": "support", "label": "Saved zone"}],
        overlay_mode="yolo-only",
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 95.0, 63.0], "plot_inner_bbox": [0.0, 0.0, 95.0, 63.0]},
            "box_history": [
                {
                    "sequence_index": 1,
                    "box_type": "impulse",
                    "direction": "BUY",
                    "confidence": 0.82,
                    "bbox": [56.0, 14.0, 72.0, 32.0],
                }
            ],
            "current_box": {
                "sequence_index": 1,
                "box_type": "impulse",
                "direction": "BUY",
                "confidence": 0.82,
                "bbox": [56.0, 14.0, 72.0, 32.0],
            },
        },
    )

    assert overlay.getpixel((10, 12)) != (0, 0, 0)
    assert overlay.getpixel((41, 24)) == (0, 0, 0)
    assert overlay.getpixel((64, 24)) == (0, 0, 0)
    assert overlay.getpixel((48, 32)) == (0, 0, 0)
    assert overlay.getpixel((8, 42)) == (0, 0, 0)


def test_hybrid_vision_overlay_draws_yolo_and_structure_together() -> None:
    base = Image.new("RGB", (96, 64), color=(0, 0, 0))

    overlay = draw_overlay(
        base,
        [
            {"pattern": "ascending_triangle", "confidence": 0.82, "bbox": [10.0, 12.0, 24.0, 28.0]},
            {"pattern": "latest_candle_buy", "confidence": 0.90, "bbox": [34.0, 12.0, 48.0, 28.0]},
        ],
        [{"type": "support", "price": 0.5}],
        user_zones=[{"bbox": [8.0, 42.0, 20.0, 54.0], "kind": "support", "label": "Saved zone"}],
        overlay_mode="hybrid-vision",
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 95.0, 63.0], "plot_inner_bbox": [0.0, 0.0, 95.0, 63.0]},
            "box_history": [
                {
                    "sequence_index": 1,
                    "box_type": "impulse",
                    "direction": "BUY",
                    "confidence": 0.82,
                    "bbox": [56.0, 14.0, 72.0, 32.0],
                }
            ],
            "current_box": {
                "sequence_index": 1,
                "box_type": "impulse",
                "direction": "BUY",
                "confidence": 0.82,
                "bbox": [56.0, 14.0, 72.0, 32.0],
            },
        },
    )

    assert overlay.getpixel((10, 12)) != (0, 0, 0)
    assert overlay.getpixel((56, 14)) != (0, 0, 0)
    assert overlay.getpixel((34, 12)) == (0, 0, 0)
    assert overlay.getpixel((48, 32)) == (0, 0, 0)
    assert overlay.getpixel((8, 42)) == (0, 0, 0)


def test_model_council_html_exposes_focus_crops_and_honest_vision_copy() -> None:
    source_image = Image.new("RGB", (128, 80), color=(12, 18, 24))
    result: dict[str, Any] = {
        "meta": {"sha256": "council-vision-demo"},
        "chart_geometry": {
            "latest_sequence_bbox": [58.0, 14.0, 104.0, 58.0],
            "plot_inner_bbox": [10.0, 8.0, 118.0, 72.0],
        },
        "box_history": [
            {"sequence_index": 1, "box_type": "balance", "direction": "BUY", "confidence": 0.66, "bbox": [24.0, 22.0, 46.0, 46.0]},
            {"sequence_index": 2, "box_type": "impulse", "direction": "BUY", "confidence": 0.82, "bbox": [50.0, 18.0, 74.0, 50.0]},
        ],
        "current_box": {"sequence_index": 2, "box_type": "impulse", "direction": "BUY", "confidence": 0.82, "bbox": [50.0, 18.0, 74.0, 50.0]},
        "projection": {"next_box": {"bbox": [78.0, 16.0, 104.0, 46.0], "direction": "BUY", "box_type": "impulse"}},
        "detections": [
            {"pattern": "ascending_triangle", "confidence": 0.81, "bbox": [60.0, 16.0, 98.0, 52.0]},
            {"pattern": "latest_candle_buy", "confidence": 0.89, "bbox": [88.0, 20.0, 100.0, 38.0]},
        ],
        "local_ensemble": {
            "models": {
                "dinov2": {
                    "name": "dinov2",
                    "role": "structure_specialist",
                    "live_enabled": True,
                    "predicted_label": "BUY",
                    "confidence": 0.84,
                    "dynamic_weight": 0.91,
                    "decision_threshold": 0.53,
                    "entropy": 0.22,
                    "routing_alignment": 0.88,
                    "routing_factor": 1.37,
                    "runtime_backend": "onnx",
                    "sequence_tasks": {
                        "current_box_type": {"value": "impulse", "confidence": 0.79},
                        "projection_direction": {"value": "BUY", "confidence": 0.82},
                    },
                },
                "clip": {
                    "name": "clip",
                    "role": "buy_specialist",
                    "live_enabled": False,
                    "predicted_label": "BUY",
                    "confidence": 0.78,
                    "shadow_weight": 0.57,
                    "decision_threshold": 0.51,
                    "entropy": 0.31,
                    "routing_alignment": 0.81,
                    "routing_factor": 1.18,
                    "runtime_backend": "pytorch",
                    "sequence_tasks": {
                        "trigger": {"value": "continuation", "confidence": 0.71},
                        "next_box_type": {"value": "impulse", "confidence": 0.69},
                    },
                },
            },
            "ensemble": {
                "champion_model": "dinov2",
                "confirmer_model": "clip",
                "predicted_label": "BUY",
                "confidence": 0.83,
                "margin": 0.19,
                "disagreement": 0.08,
                "consensus_ratio": 0.75,
                "router_direction": "BUY",
                "router_strength": 0.72,
                "router_uncertainty": 0.24,
                "router_regime_confidence": 0.68,
            },
            "selection": {
                "selected_models": ["dinov2", "clip"],
                "skipped_models": ["swav"],
                "budget": 2,
                "reason": "structure_route",
            },
        },
        "model_council": {"source": "inline", "status": "ready"},
    }

    html = build_model_council_html(result, source_image)

    assert "Cross-Checks" in html
    assert "Cross-Check 1" in html
    assert "deeper second opinion" in html
    assert "Alignment" in html
    assert "YOLO" not in html
    assert "data:image/png;base64," in html


def test_model_council_html_respects_operator_scope_off() -> None:
    source_image = Image.new("RGB", (64, 48), color=(14, 18, 22))
    result: dict[str, Any] = {
        "local_ensemble": {
            "models": {
                "swav": {
                    "name": "swav",
                    "role": "generalist",
                    "live_enabled": True,
                    "predicted_label": "BUY",
                    "confidence": 0.72,
                    "dynamic_weight": 0.84,
                }
            },
            "ensemble": {"predicted_label": "BUY", "confidence": 0.72},
            "selection": {"selected_models": ["swav"], "budget": 1},
        },
        "model_council": {"scope": "full", "source": "inline", "status": "ready"},
    }

    html = build_model_council_html(result, source_image, requested_scope="off")

    assert "Cross-check review is off in the current controls" in html


def test_render_config_normalizes_advanced_vision_controls() -> None:
    config = _build_render_config(
        overlay_mode="hybrid-vision",
        min_conf_global=0.42,
        min_conf_latest=0.5,
        history_depth=999,
        label_density=10,
        projection_focus=0.35,
        debug_depth=6,
        vision_extras=["projection-overlay", "grounded-zones", "grounded-objects", "grounded-zones", "invalid"],
        council_scope="full",
    )

    assert config["vision_extras"] == ["projection-overlay", "grounded-zones", "grounded-objects"]
    assert config["council_scope"] == "full"
    assert config["history_depth"] == MAX_SEQUENCE_HISTORY_DEPTH


def test_overlay_extras_draw_grounded_regions_and_tta_tag() -> None:
    base = Image.new("RGB", (120, 80), color=(0, 0, 0))

    overlay = draw_overlay(
        base,
        [],
        [],
        overlay_mode="history-boxes",
        vision_extras=["grounded-zones", "grounded-objects", "tta-tag"],
        chart_structure={
            "chart_geometry": {"plot_bbox": [0.0, 0.0, 119.0, 79.0], "plot_inner_bbox": [0.0, 0.0, 119.0, 79.0]},
            "grounded_chart": {
                "zones": [
                    {"kind": "grounded_zone", "pattern": "support", "bbox": [16.0, 18.0, 40.0, 42.0], "confidence": 0.71}
                ],
                "objects": [
                    {"kind": "grounded_region", "label": "breakout lane", "bbox": [62.0, 16.0, 84.0, 36.0], "confidence": 0.66}
                ],
            },
            "test_time_adaptation": {
                "selected_view": "crop_clahe",
                "candidates": [
                    {"name": "crop_clahe", "score": 0.71},
                    {"name": "raw", "score": 0.58},
                ],
            },
        },
    )

    assert overlay.getpixel((20, 22)) != (0, 0, 0)
    assert overlay.getpixel((62, 16)) != (0, 0, 0)
    assert overlay.getpixel((10, 10)) != (0, 0, 0)


def test_sequence_model_summary_tracks_directional_pressure() -> None:
    sequence_state: dict[str, Any] = {
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
    sequence_state: dict[str, Any] = {
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
    base_chart_state: dict[str, Any] = {
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

    without_consol = _forecast_3m(reg, 
        {**base_chart_state, "has_active_consolidation": False, "structure_trade_ready": False},
        detections=[],
        memory_similarity=0.82,
        memory_direction="BUY",
        transition_summary={"continue_prob": 0.58, "pullback_prob": 0.16, "reversal_attempt_prob": 0.12, "fakeout_prob": 0.14},
        memory_summary={"top_similarity": 0.82, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )
    with_consol = _forecast_3m(reg, 
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


def test_entry_progression_profile_publishes_maturity_and_velocity() -> None:
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "continuation_probability": 0.67,
        "reversal_probability": 0.14,
        "fakeout_probability": 0.09,
        "path_clarity": 0.74,
        "box_sequence_agreement": 0.77,
        "color_flip_rate": 0.18,
        "sequence_state": {
            "continuation_probability": 0.67,
            "reversal_probability": 0.14,
            "fakeout_probability": 0.09,
            "path_clarity": 0.74,
            "box_sequence_agreement": 0.77,
            "sequence_model": {
                "history_coherence": 0.71,
                "uncertainty": 0.19,
            },
        },
        "projected_next_box": {"direction": "BUY", "confidence": 0.81, "box_type": "impulse"},
        "memory_candle_regression": {"confidence": 0.66, "alignment_to_label": 0.84},
    }

    progression = derive_entry_progression_profile(chart_state)
    metric_profile = build_metric_profile({**chart_state, "entry_progression": progression})
    trajectory_signature = build_trajectory_signature({**chart_state, "entry_progression": progression}, sequence_index=3)

    assert progression["progression_stage"] in {"developing_continuation", "late_continuation", "progression"}
    assert float(progression["maturity_score"]) > 0.0
    assert float(progression["progression_velocity"]) >= 0.0
    assert float(metric_profile["entry_progression_maturity"]) == float(progression["maturity_score"])
    assert float(metric_profile["entry_progression_velocity"]) == float(progression["progression_velocity"])
    assert len(trajectory_signature) == 16


def test_forecast_releases_for_impulse_chain_without_consolidation() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
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

    result = _forecast_3m(reg, 
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


def test_forecast_accepts_canonical_transition_summary_keys() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "direction_probability": 0.58,
        "implied_3min_move_pct": 0.18,
        "entry_candle": {"body_pct": 0.44},
        "mcts": {"buy_prob": 0.57, "sell_prob": 0.43},
        "box_sequence_agreement": 0.72,
        "path_clarity": 0.68,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": True,
        "structure_setup": "reversal_release",
        "projected_next_box": {"box_type": "reversal_base", "direction": "BUY", "confidence": 0.69},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[],
        memory_similarity=0.76,
        memory_direction="BUY",
        transition_summary={"continue": 0.62, "pullback": 0.14, "reversal_attempt": 0.18, "fakeout": 0.06},
        memory_summary={"top_similarity": 0.76, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["continue_prob"] == 0.62
    assert result["pullback_prob"] == 0.14
    assert result["reversal_attempt_prob"] == 0.18
    assert result["fakeout_prob"] == 0.06


def test_forecast_releases_counter_macro_reversal_base_against_bearish_ensemble() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "SELL",
        "direction_probability": 0.57,
        "implied_3min_move_pct": 0.22,
        "entry_candle": {"body_pct": 0.46},
        "mcts": {"buy_prob": 0.66, "sell_prob": 0.34},
        "box_sequence_agreement": 0.58,
        "path_clarity": 0.63,
        "projection_alignment": 0.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.11,
        "projection_bias_confidence": 0.55,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "counter_macro_reversal",
            "macro_direction": "BUY",
        },
        "projected_next_box": {
            "box_type": "reversal_base",
            "direction": "BUY",
            "confidence": 0.67,
        },
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "bullish_engulfing", "confidence": 0.73}],
        memory_similarity=0.74,
        memory_direction="BUY",
        transition_summary={"continue": 0.32, "pullback": 0.15, "reversal_attempt": 0.42, "fakeout": 0.11},
        memory_summary={"top_similarity": 0.74, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "reversal_release"
    assert result["structure_trade_ready"] == 1.0
    assert result["projected_box_direction"] == "BUY"
    assert result["q50"] > 0.0
    assert result["force_hold"] is False


def test_forecast_promotes_macro_pullback_reclaim_to_trade_ready_structure() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "direction_probability": 0.64,
        "implied_3min_move_pct": 0.26,
        "entry_candle": {"body_pct": 0.44},
        "mcts": {"buy_prob": 0.63, "sell_prob": 0.37},
        "box_sequence_agreement": 0.58,
        "path_clarity": 0.62,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.0,
        "projection_bias_confidence": 0.61,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "macro_pullback",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "reversal_base", "direction": "BUY", "confidence": 0.72},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "bullish_engulfing", "confidence": 0.70}],
        memory_similarity=0.74,
        memory_direction="BUY",
        transition_summary={"continue": 0.23, "pullback": 0.24, "reversal_attempt": 0.43, "fakeout": 0.10},
        memory_summary={"top_similarity": 0.74, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "reversal_release"
    assert result["structure_trade_ready"] == 1.0
    assert result["projected_box_direction"] == "BUY"


def test_forecast_promotes_aligned_reversal_base_to_reversal_release() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "direction_probability": 0.63,
        "implied_3min_move_pct": 0.24,
        "entry_candle": {"body_pct": 0.41},
        "mcts": {"buy_prob": 0.62, "sell_prob": 0.38},
        "box_sequence_agreement": 0.60,
        "path_clarity": 0.58,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.0,
        "projection_bias_confidence": 0.55,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "counter_macro_reversal",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "reversal_base", "direction": "BUY", "confidence": 0.67},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "bullish_engulfing", "confidence": 0.68}],
        memory_similarity=0.72,
        memory_direction="BUY",
        transition_summary={"continue": 0.24, "pullback": 0.22, "reversal_attempt": 0.42, "fakeout": 0.09},
        memory_summary={"top_similarity": 0.72, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "reversal_release"
    assert result["structure_trade_ready"] == 1.0


def test_forecast_promotes_with_trend_pullback_resume_to_impulse_chain() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "direction_probability": 0.66,
        "implied_3min_move_pct": 0.27,
        "entry_candle": {"body_pct": 0.46},
        "mcts": {"buy_prob": 0.65, "sell_prob": 0.35},
        "box_sequence_agreement": 0.98,
        "path_clarity": 0.77,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.23,
        "projection_bias_confidence": 0.79,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "with_macro_push",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "pullback", "direction": "BUY", "confidence": 0.84},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_buy", "confidence": 0.74}],
        memory_similarity=0.79,
        memory_direction="BUY",
        transition_summary={"continue": 0.31, "pullback": 0.26, "reversal_attempt": 0.34, "fakeout": 0.09},
        memory_summary={"top_similarity": 0.79, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "impulse_chain"
    assert result["structure_trade_ready"] == 1.0


def test_forecast_promotes_high_conviction_sell_pullback_resume_to_impulse_chain() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "SELL",
        "direction_probability": 0.64,
        "implied_3min_move_pct": 0.27,
        "entry_candle": {"body_pct": 0.45},
        "mcts": {"buy_prob": 0.34, "sell_prob": 0.66},
        "box_sequence_agreement": 0.61,
        "path_clarity": 0.79,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.19,
        "projection_bias_confidence": 0.82,
        "macro_trend": "BEAR",
        "swing_state": {
            "swing_phase": "with_macro_push",
            "macro_direction": "SELL",
        },
        "projected_next_box": {"box_type": "pullback", "direction": "SELL", "confidence": 0.86},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_sell", "confidence": 0.77}],
        memory_similarity=0.81,
        memory_direction="SELL",
        transition_summary={"continue": 0.29, "pullback": 0.26, "reversal_attempt": 0.35, "fakeout": 0.10},
        memory_summary={"top_similarity": 0.81, "dominant_label": "SELL", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "impulse_chain"
    assert result["structure_trade_ready"] == 1.0
    assert result["projected_box_direction"] == "SELL"
    assert result["q50"] < 0.0


def test_forecast_keeps_weak_sell_pullback_resume_unconfirmed() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "SELL",
        "direction_probability": 0.64,
        "implied_3min_move_pct": 0.25,
        "entry_candle": {"body_pct": 0.42},
        "mcts": {"buy_prob": 0.35, "sell_prob": 0.65},
        "box_sequence_agreement": 0.57,
        "path_clarity": 0.68,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.08,
        "projection_bias_confidence": 0.73,
        "macro_trend": "BEAR",
        "swing_state": {
            "swing_phase": "with_macro_push",
            "macro_direction": "SELL",
        },
        "projected_next_box": {"box_type": "pullback", "direction": "SELL", "confidence": 0.81},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_sell", "confidence": 0.73}],
        memory_similarity=0.77,
        memory_direction="SELL",
        transition_summary={"continue": 0.28, "pullback": 0.27, "reversal_attempt": 0.34, "fakeout": 0.11},
        memory_summary={"top_similarity": 0.77, "dominant_label": "SELL", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "none"
    assert result["structure_trade_ready"] == 0.0


def test_forecast_promotes_counter_macro_sell_breakaway_to_reversal_release() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "SELL",
        "direction_probability": 0.63,
        "implied_3min_move_pct": 0.26,
        "entry_candle": {"body_pct": 0.44},
        "mcts": {"buy_prob": 0.35, "sell_prob": 0.65},
        "box_sequence_agreement": 0.86,
        "path_clarity": 0.78,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.15,
        "projection_bias_confidence": 0.77,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "macro_pullback",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "pullback", "direction": "SELL", "confidence": 0.82},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_sell", "confidence": 0.75}],
        memory_similarity=0.78,
        memory_direction="SELL",
        transition_summary={"continue": 0.31, "pullback": 0.25, "reversal_attempt": 0.34, "fakeout": 0.10},
        memory_summary={"top_similarity": 0.78, "dominant_label": "SELL", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "reversal_release"
    assert result["structure_trade_ready"] == 1.0
    assert result["projected_box_direction"] == "SELL"
    assert result["q50"] < 0.0


def test_forecast_keeps_weak_counter_macro_sell_breakaway_unconfirmed() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "SELL",
        "direction_probability": 0.63,
        "implied_3min_move_pct": 0.24,
        "entry_candle": {"body_pct": 0.41},
        "mcts": {"buy_prob": 0.36, "sell_prob": 0.64},
        "box_sequence_agreement": 0.73,
        "path_clarity": 0.70,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.09,
        "projection_bias_confidence": 0.73,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "macro_pullback",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "pullback", "direction": "SELL", "confidence": 0.80},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_sell", "confidence": 0.73}],
        memory_similarity=0.76,
        memory_direction="SELL",
        transition_summary={"continue": 0.28, "pullback": 0.27, "reversal_attempt": 0.35, "fakeout": 0.10},
        memory_summary={"top_similarity": 0.76, "dominant_label": "SELL", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "none"
    assert result["structure_trade_ready"] == 0.0


def test_forecast_promotes_counter_macro_sell_extension_to_reversal_release() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "SELL",
        "direction_probability": 0.62,
        "implied_3min_move_pct": 0.24,
        "entry_candle": {"body_pct": 0.40},
        "mcts": {"buy_prob": 0.37, "sell_prob": 0.63},
        "box_sequence_agreement": 0.84,
        "path_clarity": 0.57,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.08,
        "projection_bias_confidence": 0.72,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "counter_macro_reversal",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "pullback", "direction": "SELL", "confidence": 0.76},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_sell", "confidence": 0.74}],
        memory_similarity=0.77,
        memory_direction="SELL",
        transition_summary={"continue": 0.24, "pullback": 0.28, "reversal_attempt": 0.37, "fakeout": 0.11},
        memory_summary={"top_similarity": 0.77, "dominant_label": "SELL", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "reversal_release"
    assert result["structure_trade_ready"] == 1.0
    assert result["projected_box_direction"] == "SELL"


def test_forecast_keeps_low_agreement_counter_macro_sell_extension_unconfirmed() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "SELL",
        "direction_probability": 0.62,
        "implied_3min_move_pct": 0.24,
        "entry_candle": {"body_pct": 0.40},
        "mcts": {"buy_prob": 0.37, "sell_prob": 0.63},
        "box_sequence_agreement": 0.74,
        "path_clarity": 0.57,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.08,
        "projection_bias_confidence": 0.72,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "counter_macro_reversal",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "pullback", "direction": "SELL", "confidence": 0.76},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_sell", "confidence": 0.74}],
        memory_similarity=0.77,
        memory_direction="SELL",
        transition_summary={"continue": 0.24, "pullback": 0.28, "reversal_attempt": 0.37, "fakeout": 0.11},
        memory_summary={"top_similarity": 0.77, "dominant_label": "SELL", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "none"
    assert result["structure_trade_ready"] == 0.0


def test_forecast_promotes_counter_macro_impulse_release_to_reversal_release() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "direction_probability": 0.64,
        "implied_3min_move_pct": 0.25,
        "entry_candle": {"body_pct": 0.43},
        "mcts": {"buy_prob": 0.63, "sell_prob": 0.37},
        "box_sequence_agreement": 0.45,
        "path_clarity": 0.77,
        "projection_alignment": 1.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.08,
        "projection_bias_confidence": 0.66,
        "macro_trend": "BULL",
        "swing_state": {
            "swing_phase": "counter_macro_reversal",
            "macro_direction": "BUY",
        },
        "projected_next_box": {"box_type": "impulse", "direction": "BUY", "confidence": 0.77},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[{"pattern": "next_candle_buy", "confidence": 0.74}],
        memory_similarity=0.73,
        memory_direction="BUY",
        transition_summary={"continue": 0.27, "pullback": 0.08, "reversal_attempt": 0.53, "fakeout": 0.12},
        memory_summary={"top_similarity": 0.73, "dominant_label": "BUY", "mixed_labels": False, "ambiguity": 0.0, "label_entropy": 0.0},
    )

    assert result["structure_setup"] == "reversal_release"
    assert result["structure_trade_ready"] == 1.0


def test_transition_alignment_prefers_reversal_release_over_stale_continuation_entry_type() -> None:
    gates = CurriculumGates(_NullLogger())

    result = gates.transition_alignment_gate(
        chart_state={"entry_type": "continuation", "structure_setup": "none"},
        forecast={
            "structure_setup": "reversal_release",
            "structure_trade_ready": 1.0,
            "projected_box_confidence": 0.68,
            "path_confidence": 0.82,
        },
        transition_summary={"continue": 0.28, "pullback": 0.18, "reversal_attempt": 0.46, "fakeout": 0.08},
    )

    assert result.pass_fail is True
    assert result.detail["structure_setup"] == "reversal_release"
    assert float(result.detail["favorable"]) > float(result.detail["hazard"])


def test_zone_memory_preserves_projected_action_without_matching_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    def _match_zone_memory_to_result(_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_count": 0,
            "preferred_action": "HOLD",
            "probability_bias": 0.0,
            "alignment_score": 0.0,
            "buy_bias": 0.0,
            "sell_bias": 0.0,
            "matching_zones": [],
            "visible_zones": [],
        }

    monkeypatch.setattr(
        "main._match_zone_memory_to_result",
        _match_zone_memory_to_result,
    )

    result = _apply_zone_memory_to_result(
        {
            "action": "BUY",
            "trade_bias": "BUY",
            "decision_state": "PROJECTED",
            "confidence": 0.58,
            "probabilities": {"BUY": 0.26, "SELL": 0.68, "HOLD": 0.06},
        }
    )

    assert result["action"] == "BUY"
    assert result["trade_bias"] == "BUY"


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


def test_ensemble_base_probs_strengthen_council_aligned_bias() -> None:
    base_probs = _ensemble_base_probs(
        {
            "ensemble": {
                "buy_prob": 0.53,
                "sell_prob": 0.47,
                "predicted_label": "BUY",
                "disagreement": 0.11,
                "consensus_ratio": 0.66,
            }
        },
        chart_state={
            "has_active_consolidation": False,
            "structure_trade_ready": True,
            "path_clarity": 0.71,
            "continuation_probability": 0.63,
            "projected_next_box": {"direction": "BUY", "confidence": 0.74},
        },
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "consensus_ratio": 0.8,
        },
    )
    council_probs = _ensemble_base_probs(
        {
            "ensemble": {
                "buy_prob": 0.53,
                "sell_prob": 0.47,
                "predicted_label": "BUY",
                "disagreement": 0.11,
                "consensus_ratio": 0.66,
            }
        },
        chart_state={
            "has_active_consolidation": False,
            "structure_trade_ready": True,
            "path_clarity": 0.71,
            "continuation_probability": 0.63,
            "projected_next_box": {"direction": "BUY", "confidence": 0.74},
            "council_bias_direction": "BUY",
            "council_bias_confidence": 0.82,
            "council_projection_direction": "BUY",
            "council_projection_confidence": 0.79,
            "council_structure_setup": "impulse_chain",
            "council_structure_confidence": 0.73,
            "council_alignment_score": 0.77,
            "council_influence_score": 0.80,
            "council_router_direction": "BUY",
            "council_router_strength": 0.74,
        },
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "consensus_ratio": 0.8,
        },
    )

    assert council_probs["BUY"] > base_probs["BUY"]
    assert (council_probs["BUY"] - council_probs["SELL"]) > (base_probs["BUY"] - base_probs["SELL"])


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

    result = _infer(engine, 
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

    result = _infer(engine, 
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
    box_history: list[dict[str, Any]] = [
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
    sequence_state: dict[str, Any] = {
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


def test_projection_hypotheses_reclaim_countertrend_impulse_back_to_trend() -> None:
    box_history: list[dict[str, Any]] = [
        {
            "sequence_index": 1,
            "box_type": "impulse",
            "direction": "BUY",
            "bbox": [20.0, 112.0, 46.0, 192.0],
            "confidence": 0.80,
            "maturity": 0.78,
            "price_span": 80.0,
            "consolidation_score": 0.14,
        },
        {
            "sequence_index": 2,
            "box_type": "impulse",
            "direction": "BUY",
            "bbox": [52.0, 106.0, 78.0, 188.0],
            "confidence": 0.82,
            "maturity": 0.82,
            "price_span": 82.0,
            "consolidation_score": 0.12,
        },
        {
            "sequence_index": 3,
            "box_type": "impulse",
            "direction": "BUY",
            "bbox": [84.0, 100.0, 110.0, 184.0],
            "confidence": 0.83,
            "maturity": 0.84,
            "price_span": 84.0,
            "consolidation_score": 0.10,
        },
        {
            "sequence_index": 4,
            "box_type": "impulse",
            "direction": "SELL",
            "bbox": [116.0, 128.0, 142.0, 186.0],
            "confidence": 0.65,
            "maturity": 0.66,
            "price_span": 58.0,
            "consolidation_score": 0.20,
        },
    ]
    sequence_state: dict[str, Any] = {
        "continuation_probability": 0.23,
        "pullback_probability": 0.18,
        "reversal_probability": 0.45,
        "fakeout_probability": 0.14,
        "current_box": box_history[-1],
        "trend_regime": {
            "trend_direction": "BUY",
            "trend_strength": 0.59,
            "pullback_active": False,
            "continuation_reload_score": 0.03,
            "impulse_extension": 0.30,
            "reversal_risk": 0.58,
            "trend_phase": "reversal_watch",
            "rejection_pressure": 0.56,
            "breakout_failure_risk": 0.60,
            "counter_run_len": 5,
        },
    }

    hypotheses = _build_next_box_hypotheses(
        box_history,
        sequence_state,
        {"plot_bbox": [0.0, 0.0, 240.0, 180.0]},
        market_state={"macro_trend": "BEAR"},
        memory_summary={
            "dominant_label": "BUY",
            "top_similarity": 0.80,
            "consensus_ratio": 1.0,
            "ambiguity": 0.0,
        },
        memory_episode_matches=[
            {"label": "BUY", "similarity": 0.80},
            {"label": "BUY", "similarity": 0.78},
        ],
    )

    assert hypotheses
    assert hypotheses[0]["direction"] == "BUY"
    assert str(hypotheses[0]["trigger"]) in {"countertrend_fail_reclaim", "trend_reclaim_watch"}


def test_chart_state_classifies_reversal_release_structure() -> None:
    current_box: dict[str, Any] = {
        "box_type": "reversal_base",
        "direction": "BUY",
        "confidence": 0.81,
        "maturity": 0.60,
        "consolidation_score": 0.45,
    }
    projected_box: dict[str, Any] = {
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


def test_chart_state_promotes_council_backed_structure_when_core_setup_is_none() -> None:
    current_box: dict[str, Any] = {
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.71,
        "maturity": 0.58,
        "consolidation_score": 0.16,
    }
    projected_box: dict[str, Any] = {
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.67,
        "dominance_gap": 0.10,
        "explanation": "impulse:BUY via impulse_chain",
    }
    chart_state = _build_chart_state(
        detections=[],
        local_ensemble={
            "ensemble": {
                "predicted_label": "BUY",
                "confidence": 0.69,
                "buy_prob": 0.69,
                "sell_prob": 0.31,
                "champion_model": "structure_wave",
                "confirmer_model": "rapid_pulse",
                "disagreement": 0.08,
                "consensus_ratio": 0.83,
                "router_direction": "BUY",
                "router_strength": 0.76,
                "router_regime_confidence": 0.71,
                "sequence_task_consensus": {
                    "projection_direction": {"value": "BUY", "confidence": 0.84, "support": 2.6, "n_models": 3},
                    "current_box_direction": {"value": "BUY", "confidence": 0.79, "support": 2.3, "n_models": 3},
                    "next_box_direction": {"value": "BUY", "confidence": 0.86, "support": 2.7, "n_models": 3},
                    "structure_setup": {"value": "impulse_chain", "confidence": 0.88, "support": 2.8, "n_models": 3},
                    "trigger": {"value": "impulse_chain", "confidence": 0.82, "support": 2.5, "n_models": 3},
                },
            }
        },
        reasoning_trace={"market_state": {"local_phase": "with_trend_pause", "macro_trend": "BULL"}},
        chart_geometry={"body_height_pct": 0.47, "upper_wick_pct": 0.18, "lower_wick_pct": 0.21},
        sequence_state={
            "recent_colors": ["green", "green", "red", "green", "green"],
            "continuation_probability": 0.60,
            "reversal_probability": 0.20,
            "fakeout_probability": 0.12,
            "color_flip_rate": 0.28,
            "small_body_ratio": 0.08,
            "current_box": current_box,
            "primary_next_box": projected_box,
            "box_history": [
                {"direction": "BUY", "box_type": "impulse", "confidence": 0.76, "maturity": 0.78, "price_span": 90.0},
                {"direction": "BUY", "box_type": "pullback", "confidence": 0.70, "maturity": 0.44, "price_span": 58.0},
                current_box,
            ],
            "box_sequence_agreement": 0.52,
            "path_clarity": 0.60,
            "has_active_consolidation": False,
            "recent_box_consolidation": 0.18,
        },
    )

    assert chart_state["structure_setup_core"] == "none"
    assert chart_state["structure_setup"] == "impulse_chain"
    assert chart_state["structure_setup_source"] == "council"
    assert chart_state["council_bias_direction"] == "BUY"
    assert chart_state["council_structure_confidence"] >= 0.75


def test_projection_sync_refreshes_chart_state_after_council_rerank() -> None:
    current_box: dict[str, Any] = {
        "box_type": "impulse",
        "direction": "BUY",
        "confidence": 0.78,
        "maturity": 0.72,
        "consolidation_score": 0.12,
        "bbox": [66.0, 74.0, 92.0, 138.0],
    }
    sequence_state: dict[str, Any] = {
        "recent_colors": ["green", "green", "red", "green", "red"],
        "continuation_probability": 0.56,
        "reversal_probability": 0.18,
        "fakeout_probability": 0.12,
        "color_flip_rate": 0.36,
        "small_body_ratio": 0.14,
        "body_std_pct": 0.06,
        "spacing_consistency": 0.68,
        "current_box": current_box,
        "box_history": [
            {"direction": "BUY", "box_type": "impulse", "confidence": 0.74, "maturity": 0.70, "bbox": [10.0, 88.0, 34.0, 148.0]},
            {"direction": "BUY", "box_type": "pullback", "confidence": 0.69, "maturity": 0.44, "bbox": [38.0, 96.0, 60.0, 150.0]},
            current_box,
        ],
        "box_sequence_agreement": 0.61,
        "path_clarity": 0.58,
        "has_active_consolidation": False,
        "recent_box_consolidation": 0.12,
        "body_mean_pct": 0.24,
        "all_visible_candles": [
            _make_candle(72, 78, 92, 148, green=True, body=0.24),
            _make_candle(82, 88, 88, 142, green=True, body=0.22),
            _make_candle(92, 98, 96, 152, green=False, body=0.20),
        ],
        "next_box_hypotheses": [
            {
                "rank": 1,
                "box_type": "impulse",
                "direction": "BUY",
                "confidence": 0.66,
                "dominance_gap": 0.07,
                "path_clarity": 0.58,
                "trigger": "breakout",
                "bbox": [98.0, 62.0, 126.0, 126.0],
            },
            {
                "rank": 2,
                "box_type": "pullback",
                "direction": "SELL",
                "confidence": 0.63,
                "dominance_gap": 0.09,
                "path_clarity": 0.72,
                "trigger": "pause_reset",
                "bbox": [98.0, 78.0, 126.0, 144.0],
            },
        ],
    }
    local_ensemble: dict[str, Any] = {
        "ensemble": {
            "predicted_label": "BUY",
            "confidence": 0.64,
            "buy_prob": 0.64,
            "sell_prob": 0.36,
            "champion_model": "dinov2",
            "confirmer_model": "mobilenetv3",
            "disagreement": 0.12,
            "sequence_task_consensus": {
                "projection_direction": {"value": "SELL", "confidence": 0.92},
                "next_box_direction": {"value": "SELL", "confidence": 0.90},
                "next_box_type": {"value": "pullback", "confidence": 0.86},
            },
        }
    }
    reasoning_trace = {"market_state": {"local_phase": "with_trend_push", "macro_trend": "BULL"}}
    chart_geometry = {"body_height_pct": 0.54, "upper_wick_pct": 0.22, "lower_wick_pct": 0.24, "geometry_confidence": 0.82}

    initial_chart_state, initial_hypotheses, _ = _rebuild_projection_synced_state(
        detections=[],
        local_ensemble=local_ensemble,
        reasoning_trace=reasoning_trace,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
        grounded_chart={},
    )
    enriched = _enrich_next_box_hypotheses_with_projected_candles(
        initial_hypotheses,
        detections=[],
        chart_state=initial_chart_state,
        sequence_state=sequence_state,
        local_ensemble=local_ensemble,
    )
    synced_chart_state, synced_hypotheses, _ = _rebuild_projection_synced_state(
        detections=[],
        local_ensemble=local_ensemble,
        reasoning_trace=reasoning_trace,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
        grounded_chart={},
        next_box_hypotheses=enriched,
    )

    assert initial_chart_state["projection_bias_direction"] == "BUY"
    assert synced_hypotheses[0]["direction"] == "SELL"
    assert sequence_state["primary_next_box"]["direction"] == "SELL"
    assert synced_chart_state["projected_next_box"]["direction"] == "SELL"
    assert synced_chart_state["projection_bias_direction"] == "SELL"


def test_forecast_releases_for_reversal_release_without_latest_candle_support() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
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

    result = _forecast_3m(reg, 
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

    result = _infer(engine, 
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


def test_ensemble_reversal_release_projection_flips_bearish_rl_with_weak_memory() -> None:
    gates = [
        SimpleNamespace(name=f"g{i}", score=0.92, pass_fail=True, detail={})
        for i in range(8)
    ]
    support_gates = [
        SimpleNamespace(name="execution_permission", score=0.38, pass_fail=False, detail={}),
        SimpleNamespace(name="opposition_strength", score=0.08, pass_fail=False, detail={}),
        SimpleNamespace(name="forecast_calibration", score=0.88, pass_fail=True, detail={}),
        SimpleNamespace(name="interval_efficiency", score=0.91, pass_fail=True, detail={}),
        SimpleNamespace(name="regime_stability", score=0.84, pass_fail=True, detail={}),
        SimpleNamespace(name="transition_alignment", score=0.86, pass_fail=True, detail={}),
    ]
    engine = EnsembleDecisionEngine(
        consensus_threshold=0.82,
        max_interval_pct=0.40,
        risk_min_pct=0.5,
        risk_max_pct=2.0,
        gates_pass_minimum=9,
        memory_veto_threshold=0.87,
    )

    result = _infer(engine, 
        rl_probs={"BUY": 0.29, "SELL": 0.57, "HOLD": 0.14},
        forecast={
            "q05": 0.06,
            "q50": 0.13,
            "q95": 0.19,
            "ad_indicator": 0.24,
            "poly_slope": 0.18,
            "execution_readiness": 0.74,
            "active_consolidation": 0.0,
            "structure_trade_ready": 1.0,
            "structure_setup": "reversal_release",
            "projected_box_direction": "BUY",
            "projected_box_confidence": 0.68,
            "projection_bias_confidence": 0.58,
            "projection_dominance": 0.0,
        },
        gate_outputs=gates,
        memory_bank_similarity=0.26,
        module_reliability={"cv_quality": 0.66, "structure_consistency": 0.67},
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "ambiguity": 0.0,
            "label_entropy": 0.0,
            "consensus_ratio": 0.90,
        },
        latest_candle_confidence=0.74,
        transition_summary={"continue_prob": 0.27, "pullback_prob": 0.26, "reversal_attempt_prob": 0.42, "fakeout_prob": 0.05},
        support_gate_outputs=support_gates,
    )

    assert result["projection_support"] is True
    assert result["projection_bias_ready"] is True
    assert result["projection_watch_ready"] is True
    assert result["hard_support_ok"] is False
    assert result["support_gates_ok"] is False
    assert result["execution_guard_ok"] is False
    assert result["action"] == "BUY"
    assert result["execution_permission"] == "WAIT_FOR_CONFIRMATION"
    assert result["decision_state"] == "PROJECTED"


def test_ensemble_projection_watch_surfaces_buy_even_when_force_hold_blocks_execution() -> None:
    gates = [
        SimpleNamespace(name=f"g{i}", score=0.92, pass_fail=True, detail={})
        for i in range(8)
    ]
    support_gates = [
        SimpleNamespace(name="execution_permission", score=0.38, pass_fail=False, detail={}),
        SimpleNamespace(name="opposition_strength", score=0.08, pass_fail=False, detail={}),
        SimpleNamespace(name="forecast_calibration", score=0.84, pass_fail=True, detail={}),
        SimpleNamespace(name="interval_efficiency", score=0.24, pass_fail=False, detail={}),
        SimpleNamespace(name="regime_stability", score=0.82, pass_fail=True, detail={}),
        SimpleNamespace(name="transition_alignment", score=0.86, pass_fail=True, detail={}),
    ]
    engine = EnsembleDecisionEngine(
        consensus_threshold=0.82,
        max_interval_pct=0.40,
        risk_min_pct=0.5,
        risk_max_pct=2.0,
        gates_pass_minimum=9,
        memory_veto_threshold=0.87,
    )

    result = _infer(engine, 
        rl_probs={"BUY": 0.29, "SELL": 0.57, "HOLD": 0.14},
        forecast={
            "q05": 0.02,
            "q50": 0.45,
            "q95": 0.87,
            "ad_indicator": 0.24,
            "poly_slope": 0.18,
            "execution_readiness": 0.48,
            "active_consolidation": 0.0,
            "structure_trade_ready": 1.0,
            "structure_setup": "reversal_release",
            "projected_box_direction": "BUY",
            "projected_box_confidence": 0.72,
            "projection_bias_confidence": 0.61,
            "projection_dominance": 0.0,
        },
        gate_outputs=gates,
        memory_bank_similarity=0.25,
        force_hold=True,
        module_reliability={"cv_quality": 0.61, "structure_consistency": 0.62},
        memory_summary={
            "dominant_label": "BUY",
            "mixed_labels": False,
            "ambiguity": 0.0,
            "label_entropy": 0.0,
            "consensus_ratio": 0.90,
        },
        latest_candle_confidence=0.65,
        transition_summary={"continue_prob": 0.23, "pullback_prob": 0.24, "reversal_attempt_prob": 0.43, "fakeout_prob": 0.10},
        support_gate_outputs=support_gates,
    )

    assert result["projection_support"] is True
    assert result["projection_watch_ready"] is True
    assert result["projection_bias_ready"] is False
    assert result["action"] == "BUY"
    assert result["execution_permission"] == "WAIT_FOR_CONFIRMATION"
    assert result["decision_state"] == "PROJECTED"


def test_ensemble_skill_gate_failures_are_diagnostic_for_armed_live_setups() -> None:
    failed_gates = [
        SimpleNamespace(name=f"g{i}", score=0.02, pass_fail=False, detail={})
        for i in range(12)
    ]
    support_gates = [
        SimpleNamespace(name="execution_permission", score=0.05, pass_fail=False, detail={}),
        SimpleNamespace(name="opposition_strength", score=0.94, pass_fail=True, detail={}),
        SimpleNamespace(name="forecast_calibration", score=0.10, pass_fail=False, detail={}),
        SimpleNamespace(name="interval_efficiency", score=0.12, pass_fail=False, detail={}),
    ]
    engine = EnsembleDecisionEngine(
        consensus_threshold=0.55,
        max_interval_pct=0.40,
        risk_min_pct=0.5,
        risk_max_pct=2.0,
        gates_pass_minimum=9,
        memory_veto_threshold=0.87,
    )

    for side, probs, q50 in (
        ("BUY", {"BUY": 0.70, "SELL": 0.18, "HOLD": 0.12}, 0.18),
        ("SELL", {"BUY": 0.18, "SELL": 0.70, "HOLD": 0.12}, -0.18),
    ):
        result = _infer(engine, 
            rl_probs=probs,
            forecast={
                "q05": min(q50 - 0.05, q50 + 0.05),
                "q50": q50,
                "q95": max(q50 - 0.05, q50 + 0.05),
                "execution_readiness": 0.78,
                "active_consolidation": 0.0,
                "structure_trade_ready": 1.0,
                "structure_setup": "impulse_chain",
                "projected_box_direction": side,
                "projected_box_confidence": 0.76,
                "projection_bias_confidence": 0.74,
                "projection_dominance": 0.12,
            },
            gate_outputs=failed_gates,
            memory_bank_similarity=0.0,
            module_reliability={"cv_quality": 0.72, "structure_consistency": 0.86},
            memory_summary=None,
            latest_candle_confidence=0.70,
            transition_summary={
                "continue_prob": 0.60,
                "pullback_prob": 0.14,
                "reversal_attempt_prob": 0.14,
                "fakeout_prob": 0.12,
            },
            support_gate_outputs=support_gates,
        )

        assert result["action"] == side
        assert result["decision_state"] in {"CONFIRMED", "PROJECTED"}
        assert result["gates_ok"] is False
        assert result["support_gates_ok"] is False
        assert result["execution_guard_ok"] is False
        assert result["opposition_alert"] is True
        assert result["gates_passing"] == 0
        assert result["gate_scores"]["g0"] == 0.02
        assert result["support_gate_scores"]["execution_permission"] == 0.05
        assert result["support_gate_pass"]["execution_permission"] is False


def test_ensemble_gate_scores_do_not_inflate_live_confidence() -> None:
    failed_gates = [
        SimpleNamespace(name=f"g{i}", score=0.01, pass_fail=False, detail={})
        for i in range(12)
    ]
    passing_gates = [
        SimpleNamespace(name=f"g{i}", score=0.99, pass_fail=True, detail={})
        for i in range(12)
    ]
    engine = EnsembleDecisionEngine(
        consensus_threshold=0.55,
        max_interval_pct=0.40,
        risk_min_pct=0.5,
        risk_max_pct=2.0,
        gates_pass_minimum=9,
        memory_veto_threshold=0.87,
    )
    kwargs = dict(
        rl_probs={"BUY": 0.69, "SELL": 0.19, "HOLD": 0.12},
        forecast={
            "q05": 0.08,
            "q50": 0.18,
            "q95": 0.24,
            "execution_readiness": 0.74,
            "structure_trade_ready": 1.0,
            "structure_setup": "impulse_chain",
            "projected_box_direction": "BUY",
            "projected_box_confidence": 0.74,
            "projection_bias_confidence": 0.72,
        },
        memory_bank_similarity=0.0,
        module_reliability={"cv_quality": 0.70, "structure_consistency": 0.84},
        memory_summary=None,
        latest_candle_confidence=0.68,
        transition_summary={
            "continue_prob": 0.58,
            "pullback_prob": 0.16,
            "reversal_attempt_prob": 0.14,
            "fakeout_prob": 0.12,
        },
    )

    failed = _infer(engine, gate_outputs=failed_gates, **kwargs)
    passing = _infer(engine, gate_outputs=passing_gates, **kwargs)

    assert failed["action"] == "BUY"
    assert passing["action"] == "BUY"
    assert failed["confidence"] == passing["confidence"]
    assert failed["calibrated_probs"] == passing["calibrated_probs"]
    assert failed["gates_ok"] is False
    assert passing["gates_ok"] is True


def test_forecast_uses_projection_direction_when_projection_overrides_ensemble() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
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

    result = _forecast_3m(reg, 
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


def test_forecast_keeps_ensemble_direction_when_opposing_projection_is_weak() -> None:
    reg = ImageFusionRegressor(_NullLogger())
    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "direction_probability": 0.61,
        "implied_3min_move_pct": 0.34,
        "entry_candle": {"body_pct": 0.52},
        "mcts": {"buy_prob": 0.61, "sell_prob": 0.39},
        "box_sequence_agreement": 0.72,
        "path_clarity": 0.70,
        "projection_alignment": 0.0,
        "has_active_consolidation": False,
        "structure_trade_ready": False,
        "structure_setup": "none",
        "projection_dominance": 0.05,
        "projection_bias_confidence": 0.62,
        "projected_next_box": {"box_type": "impulse", "direction": "SELL", "confidence": 0.64},
    }

    result = _forecast_3m(reg, 
        chart_state,
        detections=[],
        memory_similarity=0.30,
        memory_direction="HOLD",
        transition_summary={"continue_prob": 0.58, "pullback_prob": 0.20, "reversal_attempt_prob": 0.14, "fakeout_prob": 0.08},
        memory_summary={"top_similarity": 0.30, "dominant_label": "HOLD", "mixed_labels": True, "ambiguity": 0.50, "label_entropy": 0.50},
    )

    assert result["projected_box_direction"] == "SELL"
    assert result["q50"] > 0.0
    assert result["poly_slope"] > 0.0


def test_latest_signal_state_reflects_post_cap_parse_quality() -> None:
    detections: list[dict[str, Any]] = [
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
