from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence, cast

import numpy as np


def _clip01(value: Any, default: float = 0.0) -> float:
    try:
        return float(np.clip(float(value), 0.0, 1.0))
    except (TypeError, ValueError):
        return float(np.clip(default, 0.0, 1.0))


def derive_entry_progression_profile(
    chart_state: Mapping[str, Any],
    *,
    sequence_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    chart_sequence_state = chart_state.get("sequence_state", {})
    if not isinstance(chart_sequence_state, Mapping):
        chart_sequence_state = {}
    seq = cast(Mapping[str, Any], sequence_state or chart_sequence_state)

    progression = chart_state.get("entry_progression", {})
    if not isinstance(progression, Mapping):
        progression = {}

    sequence_model = chart_state.get("sequence_model", seq.get("sequence_model", {}))
    if not isinstance(sequence_model, Mapping):
        sequence_model = {}

    projected_box = chart_state.get("projected_next_box", {})
    if not isinstance(projected_box, Mapping):
        projected_box = {}

    regression = chart_state.get("memory_candle_regression", progression.get("candle_regression", {}))
    if not isinstance(regression, Mapping):
        regression = {}

    continuation_probability = _clip01(
        chart_state.get("continuation_probability", seq.get("continuation_probability", 0.25)),
        0.25,
    )
    reversal_probability = _clip01(chart_state.get("reversal_probability", seq.get("reversal_probability", 0.25)), 0.25)
    fakeout_probability = _clip01(chart_state.get("fakeout_probability", seq.get("fakeout_probability", 0.25)), 0.25)
    path_clarity = _clip01(chart_state.get("path_clarity", seq.get("path_clarity", 0.0)), 0.0)
    box_sequence_agreement = _clip01(
        chart_state.get("box_sequence_agreement", seq.get("box_sequence_agreement", 0.0)),
        0.0,
    )
    history_coherence = _clip01(
        chart_state.get(
            "history_coherence",
            cast(Mapping[str, Any], sequence_model).get("history_coherence", 0.0),
        ),
        0.0,
    )
    sequence_uncertainty = _clip01(
        chart_state.get(
            "sequence_uncertainty",
            cast(Mapping[str, Any], sequence_model).get("uncertainty", 0.0),
        ),
        0.0,
    )
    color_flip_rate = _clip01(chart_state.get("color_flip_rate", 0.0), 0.0)
    compression_score = _clip01(progression.get("compression_score", seq.get("recent_box_consolidation", 0.0)), 0.0)
    pullback_depth = _clip01(progression.get("pullback_depth", 0.0), 0.0)
    rejection_score = _clip01(progression.get("rejection_score", 0.0), 0.0)
    follow_through_score = _clip01(progression.get("follow_through_score", 0.0), 0.0)
    aggressive_sniper_score = _clip01(progression.get("aggressive_sniper_score", 0.0), 0.0)
    regression_confidence = _clip01(progression.get("regression_confidence", regression.get("confidence", 0.0)), 0.0)
    regression_alignment = _clip01(progression.get("regression_alignment", regression.get("alignment_to_label", 0.0)), 0.0)

    continuation_strength = _clip01(
        0.36 * continuation_probability
        + 0.18 * path_clarity
        + 0.16 * box_sequence_agreement
        + 0.14 * history_coherence
        + 0.08 * (1.0 - fakeout_probability)
        + 0.08 * (1.0 - sequence_uncertainty),
        0.0,
    )
    exhaustion_risk = _clip01(
        0.30 * reversal_probability
        + 0.18 * fakeout_probability
        + 0.16 * sequence_uncertainty
        + 0.14 * color_flip_rate
        + 0.12 * max(0.0, 1.0 - continuation_probability)
        + 0.10 * pullback_depth,
        0.0,
    )
    maturity_score = _clip01(
        0.40 * continuation_strength
        + 0.20 * box_sequence_agreement
        + 0.16 * history_coherence
        + 0.12 * follow_through_score
        + 0.12 * regression_confidence,
        0.0,
    )
    progression_velocity = _clip01(
        0.42 * (continuation_strength - exhaustion_risk)
        + 0.22 * (follow_through_score - rejection_score)
        + 0.18 * (0.50 - pullback_depth)
        + 0.12 * (regression_alignment - 0.50)
        + 0.06 * aggressive_sniper_score,
        0.0,
    )

    if exhaustion_risk >= 0.58 or reversal_probability >= continuation_probability + 0.08 or sequence_uncertainty >= 0.60:
        progression_stage = "exhaustion_risk"
    elif continuation_strength >= 0.70 and progression_velocity >= 0.04:
        progression_stage = "late_continuation" if box_sequence_agreement >= 0.72 else "developing_continuation"
    elif reversal_probability >= continuation_probability and box_sequence_agreement < 0.52:
        progression_stage = "reversal_watch"
    elif box_sequence_agreement <= 0.15 and path_clarity <= 0.25:
        progression_stage = "setup_progression"
    else:
        progression_stage = str(progression.get("progression_stage", "progression") or "progression")

    if progression_stage == "exhaustion_risk":
        progression_phase = "tapering"
    elif progression_stage == "late_continuation":
        progression_phase = "extended"
    elif progression_stage == "developing_continuation":
        progression_phase = "building"
    elif progression_stage == "reversal_watch":
        progression_phase = "turning"
    else:
        progression_phase = "setup" if progression_stage == "setup_progression" else "building"

    projected_direction = str(projected_box.get("direction", chart_state.get("direction", "HOLD"))).upper()
    if progression_stage == "exhaustion_risk":
        stage_reason = "reversal pressure and uncertainty are outrunning continuation"
    elif progression_stage == "late_continuation":
        stage_reason = "continuation is still present but the move looks extended"
    elif progression_stage == "developing_continuation":
        stage_reason = "continuation is building with improving structure"
    elif progression_stage == "reversal_watch":
        stage_reason = "opposing flow is starting to dominate the current direction"
    else:
        stage_reason = "the move is still forming and has not yet resolved"

    return {
        "progression_stage": progression_stage,
        "progression_phase": progression_phase,
        "progression_velocity": round(float(progression_velocity), 4),
        "maturity_score": round(float(maturity_score), 4),
        "continuation_strength": round(float(continuation_strength), 4),
        "exhaustion_risk": round(float(exhaustion_risk), 4),
        "compression_score": round(float(compression_score), 4),
        "pullback_depth": round(float(pullback_depth), 4),
        "rejection_score": round(float(rejection_score), 4),
        "follow_through_score": round(float(follow_through_score), 4),
        "aggressive_sniper_score": round(float(aggressive_sniper_score), 4),
        "regression_confidence": round(float(regression_confidence), 4),
        "regression_alignment": round(float(regression_alignment), 4),
        "projected_direction": projected_direction,
        "stage_reason": stage_reason,
        "entry_type": str(chart_state.get("entry_type", "none") or "none"),
    }


def _unit_vector(values: Sequence[float], dim: int) -> list[float]:
    arr = np.asarray(list(values), dtype=np.float32).reshape(-1)
    if arr.size < dim:
        arr = np.pad(arr, (0, dim - arr.size), mode="constant")
    else:
        arr = arr[:dim]
    norm = float(np.linalg.norm(arr))
    if norm > 1e-8:
        arr = arr / norm
    return arr.astype(np.float32).tolist()


def _hashed_text_vector(text: str, dim: int = 16) -> list[float]:
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)
    vec = rng.normal(size=(dim,)).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 1e-8:
        vec = vec / norm
    return vec.tolist()


def infer_style_signature_from_chart_state(chart_state: Mapping[str, Any]) -> dict[str, float]:
    entry_candle = chart_state.get("entry_candle", {})
    if not isinstance(entry_candle, Mapping):
        entry_candle = {}
    projected_box = chart_state.get("projected_next_box", {})
    if not isinstance(projected_box, Mapping):
        projected_box = {}
    return {
        "dark_theme": 0.0,
        "mean_luma": 0.5,
        "contrast": 0.5,
        "saturation": 0.5,
        "aspect_ratio": 1.0,
        "geometry_confidence": _clip01(chart_state.get("latest_parse_quality", 0.5), 0.5),
        "spacing_consistency": _clip01(chart_state.get("spacing_consistency", 0.5), 0.5),
        "candle_density": _clip01(chart_state.get("recent_candle_count", 12) / 24.0, 0.5),
        "color_flip_rate": _clip01(chart_state.get("color_flip_rate", 0.35), 0.35),
        "body_balance": _clip01(entry_candle.get("body_pct", 0.2) or 0.2, 0.2),
        "projection_bias_confidence": _clip01(
            chart_state.get("projection_bias_confidence", projected_box.get("confidence", 0.0)),
            0.0,
        ),
    }


def build_trajectory_signature(
    chart_state: Mapping[str, Any],
    *,
    sequence_index: int = 0,
    sequence_state: Mapping[str, Any] | None = None,
) -> list[float]:
    seq = sequence_state or {}
    projected_box = chart_state.get("projected_next_box", {})
    if not isinstance(projected_box, Mapping):
        projected_box = {}
    entry_candle = chart_state.get("entry_candle", {})
    if not isinstance(entry_candle, Mapping):
        entry_candle = {}
    progression = chart_state.get("entry_progression", {})
    if not isinstance(progression, Mapping):
        progression = {}
    direction = str(chart_state.get("direction", "HOLD")).upper()
    projected_direction = str(projected_box.get("direction", direction)).upper()
    direction_code = 1.0 if direction == "BUY" else (-1.0 if direction == "SELL" else 0.0)
    projected_code = 1.0 if projected_direction == "BUY" else (-1.0 if projected_direction == "SELL" else 0.0)
    signature = [
        direction_code,
        projected_code,
        _clip01(chart_state.get("direction_probability", 0.5), 0.5),
        _clip01(chart_state.get("continuation_probability", seq.get("continuation_probability", 0.25)), 0.25),
        _clip01(chart_state.get("pullback_probability", seq.get("pullback_probability", 0.25)), 0.25),
        _clip01(chart_state.get("reversal_probability", seq.get("reversal_probability", 0.25)), 0.25),
        _clip01(chart_state.get("fakeout_probability", seq.get("fakeout_probability", 0.25)), 0.25),
        _clip01(chart_state.get("path_clarity", seq.get("path_clarity", 0.0)), 0.0),
        _clip01(chart_state.get("consolidation_score", seq.get("recent_box_consolidation", 0.0)), 0.0),
        _clip01(chart_state.get("box_sequence_agreement", seq.get("box_sequence_agreement", 0.0)), 0.0),
        _clip01(progression.get("maturity_score", 0.0), 0.0),
        _clip01(progression.get("progression_velocity", 0.0), 0.0),
        _clip01(progression.get("exhaustion_risk", 0.0), 0.0),
        _clip01(entry_candle.get("body_pct", 0.0) or 0.0, 0.0),
        _clip01(projected_box.get("confidence", 0.0) or 0.0, 0.0),
        float(np.clip(sequence_index / 12.0, 0.0, 1.0)),
    ]
    return _unit_vector(signature, dim=16)


def build_metric_profile(
    chart_state: Mapping[str, Any],
    *,
    sequence_state: Mapping[str, Any] | None = None,
    grounded_chart: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    chart_sequence_state = chart_state.get("sequence_state", {})
    if not isinstance(chart_sequence_state, Mapping):
        chart_sequence_state = {}
    seq = cast(Mapping[str, Any], sequence_state or chart_sequence_state)
    grounded = grounded_chart or {}
    structure_summary = grounded.get("structure_summary", chart_state.get("grounded_structure", {}))
    if not isinstance(structure_summary, Mapping):
        structure_summary = {}
    projected_box = chart_state.get("projected_next_box", {})
    if not isinstance(projected_box, Mapping):
        projected_box = {}
    teaching = chart_state.get("memory_teaching", {})
    if not isinstance(teaching, Mapping):
        teaching = {}
    progression = chart_state.get("entry_progression", {})
    if not isinstance(progression, Mapping):
        progression = derive_entry_progression_profile(chart_state, sequence_state=seq)
    sniper_profile = chart_state.get("sniper_profile", {})
    if not isinstance(sniper_profile, Mapping):
        sniper_profile = {}
    regression = chart_state.get("memory_candle_regression", progression.get("candle_regression", {}))
    if not isinstance(regression, Mapping):
        regression = {}
    lesson_role = str(teaching.get("lesson_role", progression.get("progression_stage", "")) or "").lower()
    direction = str(chart_state.get("direction", "HOLD")).upper()
    projected_direction = str(projected_box.get("direction", chart_state.get("projection_bias_direction", direction))).upper()
    regression_direction = str(regression.get("direction", progression.get("candle_regression_direction", "HOLD")) or "HOLD").upper()
    try:
        regression_slope_abs = abs(float(regression.get("slope", progression.get("candle_regression_slope", 0.0)) or 0.0))
    except (TypeError, ValueError):
        regression_slope_abs = 0.0
    return {
        "direction_buy": 1.0 if direction == "BUY" else 0.0,
        "direction_sell": 1.0 if direction == "SELL" else 0.0,
        "direction_confidence": _clip01(chart_state.get("direction_probability", 0.5), 0.5),
        "projection_buy": 1.0 if projected_direction == "BUY" else 0.0,
        "projection_sell": 1.0 if projected_direction == "SELL" else 0.0,
        "projection_confidence": _clip01(
            chart_state.get("projection_bias_confidence", projected_box.get("confidence", 0.0)),
            0.0,
        ),
        "continuation_probability": _clip01(
            chart_state.get("continuation_probability", seq.get("continuation_probability", 0.25)),
            0.25,
        ),
        "reversal_probability": _clip01(
            chart_state.get("reversal_probability", seq.get("reversal_probability", 0.25)),
            0.25,
        ),
        "fakeout_probability": _clip01(
            chart_state.get("fakeout_probability", seq.get("fakeout_probability", 0.25)),
            0.25,
        ),
        "path_clarity": _clip01(chart_state.get("path_clarity", seq.get("path_clarity", 0.0)), 0.0),
        "box_sequence_agreement": _clip01(
            chart_state.get("box_sequence_agreement", seq.get("box_sequence_agreement", 0.0)),
            0.0,
        ),
        "sequence_buy_pressure": _clip01(
            chart_state.get(
                "sequence_buy_pressure",
                cast(Mapping[str, Any], seq.get("sequence_model", {})).get("buy_pressure", 0.0)
                if isinstance(seq.get("sequence_model", {}), Mapping)
                else 0.0,
            ),
            0.0,
        ),
        "sequence_sell_pressure": _clip01(
            chart_state.get(
                "sequence_sell_pressure",
                cast(Mapping[str, Any], seq.get("sequence_model", {})).get("sell_pressure", 0.0)
                if isinstance(seq.get("sequence_model", {}), Mapping)
                else 0.0,
            ),
            0.0,
        ),
        "continuation_readiness": _clip01(
            chart_state.get(
                "continuation_readiness",
                cast(Mapping[str, Any], seq.get("sequence_model", {})).get("continuation_readiness", 0.0)
                if isinstance(seq.get("sequence_model", {}), Mapping)
                else 0.0,
            ),
            0.0,
        ),
        "reversal_pressure": _clip01(
            chart_state.get(
                "reversal_pressure",
                cast(Mapping[str, Any], seq.get("sequence_model", {})).get("reversal_pressure", 0.0)
                if isinstance(seq.get("sequence_model", {}), Mapping)
                else 0.0,
            ),
            0.0,
        ),
        "history_coherence": _clip01(
            chart_state.get(
                "history_coherence",
                cast(Mapping[str, Any], seq.get("sequence_model", {})).get("history_coherence", 0.0)
                if isinstance(seq.get("sequence_model", {}), Mapping)
                else 0.0,
            ),
            0.0,
        ),
        "sequence_uncertainty": _clip01(
            chart_state.get(
                "sequence_uncertainty",
                cast(Mapping[str, Any], seq.get("sequence_model", {})).get("uncertainty", 0.0)
                if isinstance(seq.get("sequence_model", {}), Mapping)
                else 0.0,
            ),
            0.0,
        ),
        "support_strength": _clip01(structure_summary.get("support_strength", chart_state.get("support_strength", 0.0)), 0.0),
        "resistance_strength": _clip01(structure_summary.get("resistance_strength", chart_state.get("resistance_strength", 0.0)), 0.0),
        "breakout_strength": _clip01(structure_summary.get("breakout_strength", chart_state.get("breakout_strength", 0.0)), 0.0),
        "pullback_strength": _clip01(structure_summary.get("pullback_strength", chart_state.get("pullback_strength", 0.0)), 0.0),
        "consolidation_strength": _clip01(
            structure_summary.get("consolidation_strength", chart_state.get("consolidation_score", 0.0)),
            0.0,
        ),
        "structure_buy_pressure": _clip01(
            structure_summary.get("buy_pressure", chart_state.get("structure_buy_pressure", 0.0)),
            0.0,
        ),
        "structure_sell_pressure": _clip01(
            structure_summary.get("sell_pressure", chart_state.get("structure_sell_pressure", 0.0)),
            0.0,
        ),
        "structure_bias_confidence": _clip01(
            structure_summary.get("structure_bias_confidence", chart_state.get("structure_bias_confidence", 0.0)),
            0.0,
        ),
        "grounded_confidence": _clip01(chart_state.get("grounded_confidence", grounded.get("grounded_confidence", 0.0)), 0.0),
        "artifact_penalty": _clip01(
            1.0 - float(cast(Mapping[str, Any], grounded.get("artifact_summary", {})).get("artifact_score", chart_state.get("artifact_score", 0.0)) or 0.0),
            0.0,
        ),
        "lesson_actual_entry": 1.0 if lesson_role in {"actual_entry", "entry", "sniper_entry"} else 0.0,
        "lesson_win_resolution": 1.0 if lesson_role in {"win_resolution", "profit_resolution", "after_win"} else 0.0,
        "lesson_progression": _clip01(teaching.get("progression_score", 0.0), 0.0),
        "lesson_teaching_weight": _clip01(teaching.get("teaching_weight", 0.0), 0.0),
        "entry_compression": _clip01(progression.get("compression_score", 0.0), 0.0),
        "entry_pullback_depth": _clip01(progression.get("pullback_depth", 0.0), 0.0),
        "entry_rejection": _clip01(progression.get("rejection_score", 0.0), 0.0),
        "entry_follow_through": _clip01(progression.get("follow_through_score", 0.0), 0.0),
        "entry_progression_maturity": _clip01(progression.get("maturity_score", 0.0), 0.0),
        "entry_progression_velocity": _clip01(progression.get("progression_velocity", 0.0), 0.0),
        "entry_progression_exhaustion": _clip01(progression.get("exhaustion_risk", 0.0), 0.0),
        "entry_progression_continuation": _clip01(progression.get("continuation_strength", 0.0), 0.0),
        "entry_progression_regression": _clip01(progression.get("regression_confidence", 0.0), 0.0),
        "regression_buy": 1.0 if regression_direction == "BUY" else 0.0,
        "regression_sell": 1.0 if regression_direction == "SELL" else 0.0,
        "regression_slope_strength": _clip01(regression_slope_abs * 7.0, 0.0),
        "regression_confidence": _clip01(regression.get("confidence", progression.get("regression_confidence", 0.0)), 0.0),
        "regression_alignment": _clip01(regression.get("alignment_to_label", 0.0), 0.0),
        "aggressive_sniper_score": _clip01(
            sniper_profile.get("aggressive_entry_score", progression.get("aggressive_sniper_score", 0.0)),
            0.0,
        ),
    }


def build_late_interaction_tokens(
    chart_state: Mapping[str, Any],
    *,
    combined_embed: Sequence[float] | None = None,
    style_signature: Mapping[str, float] | None = None,
    sequence_state: Mapping[str, Any] | None = None,
    metric_profile: Mapping[str, float] | None = None,
) -> list[list[float]]:
    seq = sequence_state or {}
    style = dict(style_signature or infer_style_signature_from_chart_state(chart_state))
    metrics = dict(metric_profile or build_metric_profile(chart_state, sequence_state=sequence_state))
    combined = np.asarray(list(combined_embed or []), dtype=np.float32).reshape(-1)
    if combined.size == 0:
        combined = np.asarray(
            _hashed_text_vector(
                " ".join(
                    [
                        str(chart_state.get("direction", "HOLD")),
                        str(chart_state.get("entry_type", "none")),
                        str(chart_state.get("reversal_signal", "none")),
                        str(chart_state.get("continuation_signal", "none")),
                        str(chart_state.get("timeframe", "M5")),
                        str(chart_state.get("memory_teaching", {})),
                        str(chart_state.get("sniper_profile", {})),
                        str(chart_state.get("memory_candle_regression", {})),
                    ]
                ),
                dim=32,
            ),
            dtype=np.float32,
        )

    projected_box = chart_state.get("projected_next_box", {})
    if not isinstance(projected_box, Mapping):
        projected_box = {}
    teaching = chart_state.get("memory_teaching", {})
    if not isinstance(teaching, Mapping):
        teaching = {}
    progression = chart_state.get("entry_progression", {})
    if not isinstance(progression, Mapping):
        progression = derive_entry_progression_profile(chart_state, sequence_state=seq)
    sniper_profile = chart_state.get("sniper_profile", {})
    if not isinstance(sniper_profile, Mapping):
        sniper_profile = {}
    regression = chart_state.get("memory_candle_regression", progression.get("candle_regression", {}))
    if not isinstance(regression, Mapping):
        regression = {}

    token_global = _unit_vector(combined[:32].tolist(), dim=32)
    token_state = _unit_vector(
        [
            _clip01(chart_state.get("direction_probability", 0.5), 0.5),
            _clip01(chart_state.get("continuation_probability", seq.get("continuation_probability", 0.25)), 0.25),
            _clip01(chart_state.get("pullback_probability", seq.get("pullback_probability", 0.25)), 0.25),
            _clip01(chart_state.get("reversal_probability", seq.get("reversal_probability", 0.25)), 0.25),
            _clip01(chart_state.get("fakeout_probability", seq.get("fakeout_probability", 0.25)), 0.25),
            _clip01(chart_state.get("path_clarity", seq.get("path_clarity", 0.0)), 0.0),
            _clip01(chart_state.get("consolidation_score", seq.get("recent_box_consolidation", 0.0)), 0.0),
            _clip01(chart_state.get("box_sequence_agreement", seq.get("box_sequence_agreement", 0.0)), 0.0),
            _clip01(progression.get("maturity_score", 0.0), 0.0),
            _clip01(progression.get("progression_velocity", 0.0), 0.0),
            _clip01(progression.get("exhaustion_risk", 0.0), 0.0),
            _clip01(projected_box.get("confidence", 0.0) or 0.0, 0.0),
            _clip01(style.get("geometry_confidence", 0.0), 0.0),
            _clip01(style.get("spacing_consistency", 0.0), 0.0),
            _clip01(style.get("candle_density", 0.0), 0.0),
            _clip01(teaching.get("teaching_weight", 0.0), 0.0),
            _clip01(progression.get("compression_score", 0.0), 0.0),
            _clip01(sniper_profile.get("aggressive_entry_score", 0.0), 0.0),
            _clip01(regression.get("confidence", progression.get("regression_confidence", 0.0)), 0.0),
            _clip01(regression.get("alignment_to_label", 0.0), 0.0),
        ],
        dim=32,
    )
    token_style = _unit_vector(
        [
            _clip01(style.get("dark_theme", 0.0), 0.0),
            _clip01(style.get("mean_luma", 0.5), 0.5),
            _clip01(style.get("contrast", 0.5), 0.5),
            _clip01(style.get("saturation", 0.5), 0.5),
            _clip01(style.get("aspect_ratio", 1.0) / 2.0, 0.5),
            _clip01(style.get("projection_bias_confidence", 0.0), 0.0),
            _clip01(style.get("color_flip_rate", 0.0), 0.0),
            _clip01(style.get("body_balance", 0.0), 0.0),
        ]
        + _hashed_text_vector(str(chart_state.get("timeframe", "M5")), dim=8),
        dim=32,
    )
    token_structure = _unit_vector(
        _hashed_text_vector(
            "|".join(
                [
                    str(chart_state.get("entry_type", "none")),
                    str(chart_state.get("reversal_signal", "none")),
                    str(chart_state.get("continuation_signal", "none")),
                    str(projected_box.get("box_type", "none")),
                    str(projected_box.get("direction", chart_state.get("direction", "HOLD"))),
                    str(teaching.get("lesson_role", "")),
                    str(teaching.get("tags", [])),
                ]
            ),
            dim=32,
        ),
        dim=32,
    )
    token_metric = _unit_vector(
        [
            _clip01(metrics.get("direction_buy", 0.0), 0.0),
            _clip01(metrics.get("direction_sell", 0.0), 0.0),
            _clip01(metrics.get("projection_buy", 0.0), 0.0),
            _clip01(metrics.get("projection_sell", 0.0), 0.0),
            _clip01(metrics.get("sequence_buy_pressure", 0.0), 0.0),
            _clip01(metrics.get("sequence_sell_pressure", 0.0), 0.0),
            _clip01(metrics.get("support_strength", 0.0), 0.0),
            _clip01(metrics.get("resistance_strength", 0.0), 0.0),
            _clip01(metrics.get("breakout_strength", 0.0), 0.0),
            _clip01(metrics.get("pullback_strength", 0.0), 0.0),
            _clip01(metrics.get("continuation_readiness", 0.0), 0.0),
            _clip01(metrics.get("reversal_pressure", 0.0), 0.0),
            _clip01(metrics.get("history_coherence", 0.0), 0.0),
            _clip01(metrics.get("sequence_uncertainty", 0.0), 0.0),
            _clip01(metrics.get("grounded_confidence", 0.0), 0.0),
            _clip01(metrics.get("artifact_penalty", 0.0), 0.0),
            _clip01(metrics.get("lesson_actual_entry", 0.0), 0.0),
            _clip01(metrics.get("lesson_win_resolution", 0.0), 0.0),
            _clip01(metrics.get("entry_compression", 0.0), 0.0),
            _clip01(metrics.get("entry_pullback_depth", 0.0), 0.0),
            _clip01(metrics.get("entry_rejection", 0.0), 0.0),
            _clip01(metrics.get("entry_follow_through", 0.0), 0.0),
            _clip01(metrics.get("regression_buy", 0.0), 0.0),
            _clip01(metrics.get("regression_sell", 0.0), 0.0),
            _clip01(metrics.get("regression_confidence", 0.0), 0.0),
            _clip01(metrics.get("regression_alignment", 0.0), 0.0),
            _clip01(metrics.get("aggressive_sniper_score", 0.0), 0.0),
        ],
        dim=32,
    )
    return [token_global, token_state, token_style, token_structure, token_metric]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    left = np.asarray(list(a), dtype=np.float32).reshape(-1)
    right = np.asarray(list(b), dtype=np.float32).reshape(-1)
    if left.size == 0 or right.size == 0:
        return 0.0
    dim = min(left.size, right.size)
    left = left[:dim]
    right = right[:dim]
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm <= 1e-8 or right_norm <= 1e-8:
        return 0.0
    return float(np.clip(np.dot(left / left_norm, right / right_norm), -1.0, 1.0))


def late_interaction_score(
    query_tokens: Sequence[Sequence[float]] | None,
    candidate_tokens: Sequence[Sequence[float]] | None,
) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    best_scores: list[float] = []
    for query in query_tokens:
        per_token = max((_cosine(query, candidate) for candidate in candidate_tokens), default=0.0)
        best_scores.append(max(0.0, per_token))
    if not best_scores:
        return 0.0
    return float(np.clip(np.mean(np.asarray(best_scores, dtype=np.float32)), 0.0, 1.0))


def trajectory_alignment(
    query_signature: Sequence[float] | None,
    candidate_signature: Sequence[float] | None,
) -> float:
    return max(0.0, _cosine(query_signature or [], candidate_signature or []))


def style_alignment_score(
    query_style: Mapping[str, float] | None,
    candidate_style: Mapping[str, float] | None,
) -> float:
    if not query_style or not candidate_style:
        return 0.0
    keys = [key for key in query_style.keys() if key in candidate_style]
    if not keys:
        return 0.0
    deltas = []
    for key in keys:
        deltas.append(abs(_clip01(query_style.get(key, 0.0), 0.0) - _clip01(candidate_style.get(key, 0.0), 0.0)))
    mean_delta = float(np.mean(np.asarray(deltas, dtype=np.float32))) if deltas else 1.0
    return float(np.clip(1.0 - mean_delta, 0.0, 1.0))


def metric_profile_alignment(
    query_metric: Mapping[str, float] | None,
    candidate_metric: Mapping[str, float] | None,
) -> float:
    if not query_metric or not candidate_metric:
        return 0.0
    keys = [key for key in query_metric.keys() if key in candidate_metric]
    if not keys:
        return 0.0
    weights = {
        "direction_buy": 1.2,
        "direction_sell": 1.2,
        "projection_buy": 1.0,
        "projection_sell": 1.0,
        "sequence_buy_pressure": 1.3,
        "sequence_sell_pressure": 1.3,
        "continuation_readiness": 1.1,
        "reversal_pressure": 1.1,
        "support_strength": 1.0,
        "resistance_strength": 1.0,
        "breakout_strength": 1.1,
        "pullback_strength": 0.9,
        "structure_buy_pressure": 1.2,
        "structure_sell_pressure": 1.2,
        "structure_bias_confidence": 0.8,
        "history_coherence": 0.8,
        "sequence_uncertainty": 0.7,
        "lesson_actual_entry": 1.3,
        "lesson_win_resolution": 1.1,
        "lesson_progression": 1.0,
        "lesson_teaching_weight": 1.0,
        "entry_compression": 0.9,
        "entry_pullback_depth": 0.8,
        "entry_rejection": 1.1,
        "entry_follow_through": 1.0,
        "regression_buy": 1.0,
        "regression_sell": 1.0,
        "regression_slope_strength": 0.8,
        "regression_confidence": 1.0,
        "regression_alignment": 1.1,
        "aggressive_sniper_score": 1.4,
    }
    numer = 0.0
    denom = 0.0
    for key in keys:
        weight = float(weights.get(key, 0.6))
        numer += weight * (1.0 - abs(_clip01(query_metric.get(key, 0.0), 0.0) - _clip01(candidate_metric.get(key, 0.0), 0.0)))
        denom += weight
    if denom <= 1e-8:
        return 0.0
    return float(np.clip(numer / denom, 0.0, 1.0))
