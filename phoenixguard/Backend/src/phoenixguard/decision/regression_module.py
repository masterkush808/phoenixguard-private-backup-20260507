
from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Callable, Mapping, Protocol, TypedDict, cast
import numpy as np
from numpy.typing import NDArray

from phoenixguard.core.utils import can_import_chronos_safely


_BULLISH_PATTERNS = {
    "bullish_engulfing", "hammer", "morning_star", "three_white_soldiers",
    "buy_memory_bias", "reversal", "breakout", "continuation",
}
_BEARISH_PATTERNS = {
    "bearish_engulfing", "shooting_star", "evening_star", "three_black_crows",
    "sell_memory_bias", "reversal", "breakout", "continuation",
}


def _transition_prob(transition_summary: Mapping[str, Any] | None, key: str, default: float = 0.25) -> float:
    if transition_summary is None:
        return float(np.clip(default, 0.0, 1.0))
    aliases = {
        "continue": ("continue", "continue_prob"),
        "pullback": ("pullback", "pullback_prob"),
        "reversal_attempt": ("reversal_attempt", "reversal_attempt_prob"),
        "fakeout": ("fakeout", "fakeout_prob"),
    }
    for alias in aliases.get(str(key), (str(key),)):
        if alias in transition_summary:
            return float(np.clip(transition_summary.get(alias, default), 0.0, 1.0))
    return float(np.clip(default, 0.0, 1.0))


def _strict_no_fallback_enabled() -> bool:
    """
    Production can force strict behavior via PHOENIXGUARD_STRICT_NO_FALLBACK=1.
    Test/dev defaults stay relaxed so offline fallback paths remain executable.
    """
    raw = os.getenv("PHOENIXGUARD_STRICT_NO_FALLBACK")
    if raw is not None:
        return raw.strip() == "1"

    env_name = os.getenv("PHOENIXGUARD_ENV", "").strip().lower()
    if env_name in {"prod", "production"}:
        return True
    if env_name in {"dev", "development", "test", "testing", "local"}:
        return False
    if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return False
    return False


def _chronos_model_loading_enabled() -> bool:
    """Avoid native model preload during tests unless explicitly requested."""
    raw = os.getenv("PHOENIXGUARD_ENABLE_CHRONOS_MODEL_IN_TESTS")
    if raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return False
    return True


class Forecast3MCore(TypedDict):
    q05: float
    q50: float
    q95: float
    point: float
    ad_indicator: float
    poly_slope: float
    poly_r2: float
    force_hold: bool


class Forecast3MOutput(Forecast3MCore, total=False):
    force_hold_relaxed: bool
    mae_estimate: float
    mfe_estimate: float
    sequence_path_step_1: float
    sequence_path_step_2: float
    sequence_path_step_3: float
    path_confidence: float
    continue_prob: float
    pullback_prob: float
    reversal_attempt_prob: float
    fakeout_prob: float
    interval: float
    hold_threshold_used: float
    clean_memory_alignment: float
    continuation_support: float
    memory_label_entropy: float
    memory_regime_agreement: float
    contradiction_score: float
    execution_readiness: float
    active_consolidation: float
    structure_trade_ready: float
    structure_setup: str
    projected_box_type: str
    projected_box_direction: str
    projected_box_confidence: float
    projected_box_explanation: str
    projection_bias_confidence: float
    projection_dominance: float


class _MapieRegressorLike(Protocol):
    def fit(
        self,
        X: NDArray[np.float64],
        y: NDArray[np.float64],
    ) -> "_MapieRegressorLike":
        ...

    def predict(
        self,
        X: NDArray[np.float64],
        *,
        alpha: float,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        ...


_MapieRegressorFactory = Callable[..., _MapieRegressorLike]


def _load_mapie_regressor_factory() -> _MapieRegressorFactory | None:
    try:
        module = importlib.import_module("mapie.regression")
        return cast(_MapieRegressorFactory, getattr(module, "MapieRegressor"))
    except Exception:  # pragma: no cover - optional forecasting dependency
        return None


def _detection_direction_score(detections: list[dict[str, Any]] | None) -> float:
    """Convert CV detections to a directional score in [-1, 1] without duplicate latest-branch stacking."""
    if not detections:
        return 0.0

    best_by_pattern: dict[str, float] = {}
    for d in detections:
        pattern = str(d.get("pattern", "")).strip().lower().replace(" ", "_")
        conf = float(d.get("confidence", 0.0) or 0.0)
        if not pattern or conf <= 0.0:
            continue
        best_by_pattern[pattern] = max(best_by_pattern.get(pattern, 0.0), conf)

    buy = 0.0
    sell = 0.0
    for pattern, conf in best_by_pattern.items():
        if pattern in _BULLISH_PATTERNS:
            buy += conf
        if pattern in _BEARISH_PATTERNS:
            sell += conf

    denom = max(buy + sell, 1e-8)
    return float(np.clip((buy - sell) / denom, -1.0, 1.0))


class ImageFusionRegressor:
    """
    Strict image-native probabilistic forecaster for screenshot-only pipelines.
    Uses structured chart-state + CV detections + MCTS + memory similarity directly.
    """

    expects_image_signals = True

    def __init__(self, logger: Any, max_interval_pct: float = 0.40) -> None:
        self.logger = logger
        self.max_interval_pct = float(max_interval_pct)
        self.strict_no_fallback = _strict_no_fallback_enabled()

    def forecast_3m(
        self,
        chart_state: dict[str, Any],
        quantiles: tuple[float, float, float] = (0.05, 0.5, 0.95),
        detections: list[dict[str, Any]] | None = None,
        memory_similarity: float = 0.0,
        memory_direction: str = "HOLD",
        transition_summary: Mapping[str, float] | None = None,
        memory_summary: Mapping[str, Any] | None = None,
    ) -> Forecast3MOutput:
        del quantiles  # Quantile levels are fixed by construction in this image-native head.

        direction = str(chart_state.get("direction", "HOLD")).upper()
        if direction not in {"BUY", "SELL", "HOLD"}:
            if self.strict_no_fallback:
                raise RuntimeError(f"Strict mode: invalid direction '{direction}'.")
            direction = "HOLD"

        try:
            ensemble_prob = float(chart_state.get("direction_probability", 0.5) or 0.5)
        except (TypeError, ValueError):
            if self.strict_no_fallback:
                raise RuntimeError("Strict mode: missing/invalid direction_probability.")
            ensemble_prob = 0.5
        ensemble_prob = float(np.clip(ensemble_prob, 0.01, 0.99))
        active_consolidation = bool(chart_state.get("has_active_consolidation", False))
        structure_trade_ready = bool(chart_state.get("structure_trade_ready", False))
        structure_setup = str(chart_state.get("structure_setup", "none")).lower()
        path_clarity = float(np.clip(chart_state.get("path_clarity", 0.0), 0.0, 1.0))
        box_sequence_agreement = float(np.clip(chart_state.get("box_sequence_agreement", 0.0), 0.0, 1.0))
        projected_box = cast(dict[str, Any], chart_state.get("projected_next_box", {}))
        projected_box_type = str(projected_box.get("box_type", "balance")).lower()
        projected_box_direction = str(projected_box.get("direction", direction)).upper()
        projected_box_confidence = float(np.clip(projected_box.get("confidence", 0.0), 0.0, 1.0))
        projection_dominance = float(np.clip(chart_state.get("projection_dominance", projected_box.get("dominance_gap", 0.0)), 0.0, 1.0))
        projection_bias_confidence = float(np.clip(chart_state.get("projection_bias_confidence", projected_box_confidence), 0.0, 1.0))
        swing_state = cast(dict[str, Any], chart_state.get("swing_state", {}))
        swing_phase = str(swing_state.get("swing_phase", "")).lower()
        macro_direction = str(
            swing_state.get(
                "macro_direction",
                "BUY" if str(chart_state.get("macro_trend", "")).upper() == "BULL" else (
                    "SELL" if str(chart_state.get("macro_trend", "")).upper() == "BEAR" else "HOLD"
                ),
            )
        ).upper()

        try:
            implied_move = float(chart_state.get("implied_3min_move_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            if self.strict_no_fallback:
                raise RuntimeError("Strict mode: missing/invalid implied_3min_move_pct.")
            implied_move = 0.0

        entry_candle_raw: Any = chart_state.get("entry_candle", {})
        body_pct = 0.0
        if isinstance(entry_candle_raw, dict):
            entry_candle = cast(dict[str, Any], entry_candle_raw)
            try:
                body_pct = float(entry_candle.get("body_pct", 0.0) or 0.0)
            except (TypeError, ValueError):
                body_pct = 0.0

        # Image-native prior: if implied move is tiny, use candle body geometry.
        base_move_abs = abs(implied_move)
        if base_move_abs < 1e-4:
            base_move_abs = float(np.clip(body_pct * 1.6, 0.05, 1.20))

        mcts_raw: Any = chart_state.get("mcts", {})
        if not isinstance(mcts_raw, dict):
            if self.strict_no_fallback:
                raise RuntimeError("Strict mode: missing MCTS block in chart-state payload.")
            mcts_raw = {}
        mcts = cast(dict[str, Any], mcts_raw)
        try:
            mcts_buy = float(mcts.get("buy_prob", 0.5) or 0.5)
            mcts_sell = float(mcts.get("sell_prob", 0.5) or 0.5)
        except (TypeError, ValueError):
            if self.strict_no_fallback:
                raise RuntimeError("Strict mode: invalid MCTS probabilities.")
            mcts_buy, mcts_sell = 0.5, 0.5

        if transition_summary is not None:
            continue_prob = _transition_prob(transition_summary, 'continue', 0.25)
            reversal_prob = _transition_prob(transition_summary, 'reversal_attempt', 0.25)
            pullback_prob = _transition_prob(transition_summary, 'pullback', 0.25)
            fakeout_prob = _transition_prob(transition_summary, 'fakeout', 0.25)
        else:
            continue_prob = 0.25
            reversal_prob = 0.25
            pullback_prob = 0.25
            fakeout_prob = 0.25

        projection_direction_available = projected_box_direction in {"BUY", "SELL"}
        projection_opposes_ensemble = bool(
            projection_direction_available
            and direction in {"BUY", "SELL"}
            and projected_box_direction != direction
        )
        reversal_anticipation_ready = bool(
            projection_opposes_ensemble
            and projected_box_type == "reversal_base"
            and projected_box_confidence >= 0.64
            and projection_bias_confidence >= 0.44
            and path_clarity >= 0.50
            and box_sequence_agreement >= 0.44
            and reversal_prob >= max(continue_prob - 0.02, fakeout_prob + 0.08)
            and swing_phase == "counter_macro_reversal"
            and macro_direction == projected_box_direction
        )
        macro_pullback_reclaim_ready = bool(
            structure_setup == "none"
            and projection_direction_available
            and projected_box_type in {"reversal_base", "impulse"}
            and projected_box_confidence >= (0.68 if projected_box_type == "reversal_base" else 0.58)
            and projection_bias_confidence >= (0.56 if projected_box_type == "reversal_base" else 0.50)
            and path_clarity >= (0.58 if projected_box_type == "reversal_base" else 0.64)
            and box_sequence_agreement >= (0.52 if projected_box_type == "reversal_base" else 0.60)
            and swing_phase == "macro_pullback"
            and macro_direction == projected_box_direction
            and (
                reversal_prob >= max(fakeout_prob + 0.10, continue_prob - 0.02)
                if projected_box_type == "reversal_base"
                else continue_prob >= max(fakeout_prob + 0.08, pullback_prob - 0.02)
            )
        )
        aligned_reversal_release_ready = bool(
            structure_setup == "none"
            and projection_direction_available
            and projected_box_type == "reversal_base"
            and projected_box_direction == direction
            and projected_box_direction == macro_direction
            and projected_box_confidence >= 0.64
            and projection_bias_confidence >= 0.52
            and path_clarity >= 0.54
            and box_sequence_agreement >= 0.50
            and reversal_prob >= max(fakeout_prob + 0.08, continue_prob - 0.02)
        )
        trend_resume_ready = bool(
            structure_setup == "none"
            and projection_direction_available
            and projected_box_type in {"pullback", "impulse"}
            and projected_box_direction == direction
            and projected_box_direction == macro_direction
            and projected_box_confidence >= (0.74 if projected_box_type == "pullback" else 0.68)
            and projection_bias_confidence >= (0.68 if projected_box_type == "pullback" else 0.56)
            and path_clarity >= 0.70
            and box_sequence_agreement >= (0.72 if projected_box_type == "pullback" else 0.56)
            and continue_prob >= max(fakeout_prob + 0.10, reversal_prob - 0.06)
        )
        high_conviction_trend_resume_ready = bool(
            structure_setup == "none"
            and projection_direction_available
            and projected_box_type in {"pullback", "impulse"}
            and projected_box_direction == direction
            and projected_box_direction == macro_direction
            and swing_phase in {"with_macro_push", "macro_pullback"}
            and projected_box_confidence >= (0.80 if projected_box_type == "pullback" else 0.74)
            and projection_bias_confidence >= (0.74 if projected_box_type == "pullback" else 0.66)
            and path_clarity >= (0.74 if projected_box_type == "pullback" else 0.66)
            and box_sequence_agreement >= (0.58 if projected_box_type == "pullback" else 0.54)
            and projection_dominance >= (0.10 if projected_box_type == "pullback" else 0.06)
            and continue_prob >= max(fakeout_prob + 0.10, reversal_prob - 0.14)
        )
        counter_macro_breakaway_ready = bool(
            structure_setup == "none"
            and projection_direction_available
            and projected_box_type in {"pullback", "impulse"}
            and projected_box_direction == direction
            and projected_box_direction != macro_direction
            and swing_phase in {"counter_macro_reversal", "macro_pullback"}
            and projected_box_confidence >= (0.80 if projected_box_type == "pullback" else 0.82)
            and projection_bias_confidence >= (0.74 if projected_box_type == "pullback" else 0.78)
            and path_clarity >= (0.74 if projected_box_type == "pullback" else 0.72)
            and box_sequence_agreement >= (0.80 if projected_box_type == "pullback" else 0.68)
            and projection_dominance >= (0.12 if projected_box_type == "pullback" else 0.10)
            and (
                reversal_prob >= max(fakeout_prob + 0.10, continue_prob - 0.04)
                or continue_prob >= max(fakeout_prob + 0.12, reversal_prob - 0.06)
            )
        )
        counter_macro_extension_ready = bool(
            structure_setup == "none"
            and projection_direction_available
            and projected_box_type in {"pullback", "impulse"}
            and projected_box_direction == direction
            and projected_box_direction != macro_direction
            and swing_phase in {"counter_macro_reversal", "macro_pullback"}
            and projected_box_confidence >= (0.74 if projected_box_type == "pullback" else 0.75)
            and projection_bias_confidence >= (0.69 if projected_box_type == "pullback" else 0.70)
            and path_clarity >= (0.52 if projected_box_type == "pullback" else 0.60)
            and box_sequence_agreement >= (0.82 if projected_box_type == "pullback" else 0.70)
            and projection_dominance >= 0.06
            and (
                reversal_prob >= max(fakeout_prob + 0.10, continue_prob - 0.06)
                or continue_prob >= max(fakeout_prob + 0.12, reversal_prob - 0.02)
            )
        )
        counter_macro_impulse_release_ready = bool(
            structure_setup == "none"
            and projection_direction_available
            and projected_box_type == "impulse"
            and projected_box_direction == direction
            and projected_box_direction == macro_direction
            and swing_phase == "counter_macro_reversal"
            and projected_box_confidence >= 0.74
            and projection_bias_confidence >= 0.62
            and path_clarity >= 0.72
            and box_sequence_agreement >= 0.44
            and reversal_prob >= max(fakeout_prob + 0.12, continue_prob - 0.02)
        )
        effective_structure_setup = structure_setup
        effective_structure_trade_ready = bool(structure_trade_ready)
        if reversal_anticipation_ready and effective_structure_setup == "none":
            effective_structure_setup = "reversal_release"
            effective_structure_trade_ready = True
        elif macro_pullback_reclaim_ready and effective_structure_setup == "none":
            effective_structure_setup = "reversal_release" if projected_box_type == "reversal_base" else "impulse_chain"
            effective_structure_trade_ready = True
        elif aligned_reversal_release_ready and effective_structure_setup == "none":
            effective_structure_setup = "reversal_release"
            effective_structure_trade_ready = True
        elif trend_resume_ready and effective_structure_setup == "none":
            effective_structure_setup = "impulse_chain"
            effective_structure_trade_ready = True
        elif high_conviction_trend_resume_ready and effective_structure_setup == "none":
            effective_structure_setup = "impulse_chain"
            effective_structure_trade_ready = True
        elif counter_macro_breakaway_ready and effective_structure_setup == "none":
            effective_structure_setup = "reversal_release"
            effective_structure_trade_ready = True
        elif counter_macro_extension_ready and effective_structure_setup == "none":
            effective_structure_setup = "reversal_release"
            effective_structure_trade_ready = True
        elif counter_macro_impulse_release_ready and effective_structure_setup == "none":
            effective_structure_setup = "reversal_release"
            effective_structure_trade_ready = True
        continuation_structure_ready = bool(
            effective_structure_trade_ready
            and effective_structure_setup in {"impulse_chain", "reversal_release"}
        )
        confirmation_ready = bool(active_consolidation or continuation_structure_ready)
        opposing_projection_structure_ready = bool(
            effective_structure_trade_ready
            and projected_box_confidence >= 0.70
            and projection_dominance >= 0.08
            and path_clarity >= 0.58
            and box_sequence_agreement >= 0.48
        )
        if projection_opposes_ensemble:
            projection_direction_ready = bool(
                projection_direction_available
                and (
                    reversal_anticipation_ready
                    or opposing_projection_structure_ready
                    or projection_bias_confidence >= max(0.72, ensemble_prob + 0.08)
                    or (
                        projected_box_confidence >= max(0.76, ensemble_prob + 0.10)
                        and projection_dominance >= 0.10
                        and path_clarity >= 0.62
                    )
                )
            )
        else:
            projection_direction_ready = bool(
                projection_direction_available
                and (
                    effective_structure_trade_ready
                    or projection_bias_confidence >= 0.56
                    or (projected_box_confidence >= 0.62 and projection_dominance >= 0.04)
                )
            )
        effective_direction = projected_box_direction if projection_direction_ready else direction
        if effective_direction not in {"BUY", "SELL", "HOLD"}:
            effective_direction = direction
        effective_projection_alignment = float(
            1.0 if projected_box_direction in {"BUY", "SELL"} and projected_box_direction == effective_direction else 0.0
        )
        if effective_direction == "BUY":
            effective_direction_prob = max(
                ensemble_prob if direction == "BUY" else 0.0,
                projection_bias_confidence if projected_box_direction == "BUY" else 0.0,
                projected_box_confidence if projected_box_direction == "BUY" else 0.0,
                mcts_buy,
            )
        elif effective_direction == "SELL":
            effective_direction_prob = max(
                ensemble_prob if direction == "SELL" else 0.0,
                projection_bias_confidence if projected_box_direction == "SELL" else 0.0,
                projected_box_confidence if projected_box_direction == "SELL" else 0.0,
                mcts_sell,
            )
        else:
            effective_direction_prob = ensemble_prob
        base_prob = float(np.clip(effective_direction_prob, 0.01, 0.99))

        direction_sign = 1.0 if effective_direction == "BUY" else (-1.0 if effective_direction == "SELL" else 0.0)
        cv_score = _detection_direction_score(detections)
        mcts_skew = float(np.clip(mcts_buy - mcts_sell, -1.0, 1.0))

        mem_dir = str(memory_direction).upper()
        memory_direction_sign = 1.0 if mem_dir == "BUY" else (-1.0 if mem_dir == "SELL" else 0.0)
        mem_strength = float(np.clip(memory_similarity, 0.0, 1.0))

        mcts_aligned = 0.5 + 0.5 * direction_sign * mcts_skew
        cv_aligned = 0.5 + 0.5 * direction_sign * cv_score
        mem_aligned = 0.5 + 0.5 * direction_sign * memory_direction_sign * mem_strength

        fused_conf = float(np.clip(0.50 * base_prob + 0.25 * mcts_aligned + 0.20 * cv_aligned + 0.05 * mem_aligned, 0.01, 0.99))

        if transition_summary is not None:
            fused_conf = float(np.clip(fused_conf + 0.10 * continue_prob - 0.05 * fakeout_prob, 0.01, 0.99))

        ambiguity = 0.0 if memory_summary is None else float(np.clip(memory_summary.get('ambiguity', 0.0), 0.0, 1.0))
        mixed_labels = False if memory_summary is None else bool(memory_summary.get('mixed_labels', False))
        dominant_label = 'HOLD' if memory_summary is None else str(memory_summary.get('dominant_label', 'HOLD')).upper()
        top_similarity = 0.0 if memory_summary is None else float(np.clip(memory_summary.get('top_similarity', 0.0), 0.0, 1.0))
        label_entropy = 0.0 if memory_summary is None else float(np.clip(memory_summary.get('label_entropy', 0.0), 0.0, 1.0))
        clean_memory_alignment = (
            effective_direction in {'BUY', 'SELL'}
            and effective_direction == dominant_label
            and not mixed_labels
            and top_similarity >= 0.72
        )
        continuation_support = continue_prob >= 0.42 and reversal_prob <= 0.30 and fakeout_prob <= 0.34
        memory_regime_agreement = float(1.0 if clean_memory_alignment else 0.0)
        contradiction_score = float(
            np.clip(
                0.38 * (1.0 - effective_projection_alignment)
                + 0.34 * fakeout_prob
                + 0.16 * max(0.0, reversal_prob - continue_prob)
                + 0.12 * ambiguity,
                0.0,
                1.0,
            )
        )
        execution_readiness = float(
            np.clip(
                0.24 * float(1.0 if active_consolidation else 0.0)
                + 0.18 * float(1.0 if effective_structure_trade_ready else 0.0)
                + 0.24 * effective_projection_alignment
                + 0.22 * path_clarity
                + 0.20 * box_sequence_agreement
                - 0.22 * contradiction_score,
                0.0,
                1.0,
            )
        )
        if mixed_labels:
            fused_conf = float(np.clip(fused_conf * (1.0 - 0.20 * ambiguity), 0.01, 0.99))
        elif clean_memory_alignment and continuation_support:
            fused_conf = float(np.clip(fused_conf + 0.08 * continue_prob + 0.04 * top_similarity, 0.01, 0.99))
        if active_consolidation:
            fused_conf = float(np.clip(fused_conf + 0.08 * effective_projection_alignment + 0.08 * execution_readiness, 0.01, 0.99))
            if effective_structure_trade_ready and projected_box_type == "impulse":
                fused_conf = float(np.clip(fused_conf + 0.08, 0.01, 0.99))
        elif continuation_structure_ready:
            fused_conf = float(
                np.clip(
                    fused_conf
                    + 0.06 * effective_projection_alignment
                    + 0.06 * execution_readiness
                    + 0.04 * path_clarity,
                    0.01,
                    0.99,
                )
            )
        else:
            fused_conf = float(np.clip(fused_conf * 0.82, 0.01, 0.99))

        if direction == "HOLD":
            signed_point = 0.0
        else:
            signed_point = direction_sign * base_move_abs * (0.65 + 0.70 * fused_conf)

        disagreement = 0.5 * abs(mcts_skew - cv_score)
        uncertainty = float(np.clip((1.0 - fused_conf) + 0.60 * disagreement, 0.0, 1.5))
        if clean_memory_alignment and continuation_support:
            uncertainty = float(np.clip(uncertainty * (1.0 - 0.18 * continue_prob), 0.0, 1.5))
        directional_agreement = float(np.clip(0.5 + 0.5 * direction_sign * cv_score, 0.0, 1.0))
        if clean_memory_alignment and continuation_support and directional_agreement >= 0.55:
            uncertainty = float(np.clip(uncertainty * (1.0 - 0.10 * directional_agreement), 0.0, 1.5))
        if active_consolidation:
            uncertainty = float(np.clip(uncertainty * (1.0 - 0.18 * execution_readiness), 0.0, 1.5))
        elif continuation_structure_ready:
            uncertainty = float(np.clip(uncertainty * (1.0 - 0.14 * execution_readiness), 0.0, 1.5))
        else:
            uncertainty = float(np.clip(uncertainty + 0.18, 0.0, 1.5))
        interval = float(np.clip(base_move_abs * (0.55 + 1.80 * uncertainty), 0.06, 1.40))

        q05 = float(signed_point - 0.5 * interval)
        q95 = float(signed_point + 0.5 * interval)
        hold_threshold = float(self.max_interval_pct * (1.15 if (clean_memory_alignment and continuation_support) else 1.0))
        if clean_memory_alignment and continuation_support and directional_agreement >= 0.60:
            hold_threshold *= 1.08
        if active_consolidation and execution_readiness >= 0.60:
            hold_threshold *= 1.12
        elif continuation_structure_ready and execution_readiness >= 0.58:
            hold_threshold *= 1.08
        require_consolidation = os.getenv("PHOENIXGUARD_REQUIRE_CONSOLIDATION", "1").strip() == "1"
        force_hold = bool(interval > hold_threshold or (require_consolidation and not confirmation_ready))

        mae_estimate = float(np.clip(max(0.0, interval * (0.42 + 0.25 * fakeout_prob + 0.20 * pullback_prob)), 0.0, 5.0))
        mfe_estimate = float(np.clip(max(0.0, abs(signed_point) * (0.90 + 0.35 * continue_prob + 0.20 * reversal_prob)), 0.0, 5.0))
        step_1 = float(signed_point * 0.45)
        step_2 = float(signed_point * 0.80)
        step_3 = float(signed_point)
        return {
            "q05": q05,
            "q50": float(signed_point),
            "q95": q95,
            "point": float(signed_point),
            "ad_indicator": float(cv_score),
            "poly_slope": float(direction_sign * fused_conf),
            "poly_r2": float(fused_conf),
            "force_hold": bool(force_hold),
            "mae_estimate": mae_estimate,
            "mfe_estimate": mfe_estimate,
            "sequence_path_step_1": step_1,
            "sequence_path_step_2": step_2,
            "sequence_path_step_3": step_3,
            "path_confidence": float(np.clip(fused_conf * (1.0 - 0.20 * ambiguity), 0.0, 1.0)),
            "continue_prob": continue_prob,
            "pullback_prob": pullback_prob,
            "reversal_attempt_prob": reversal_prob,
            "fakeout_prob": fakeout_prob,
            "interval": interval,
            "hold_threshold_used": hold_threshold,
            "clean_memory_alignment": float(1.0 if clean_memory_alignment else 0.0),
            "continuation_support": float(1.0 if continuation_support else 0.0),
            "memory_label_entropy": label_entropy,
            "memory_regime_agreement": memory_regime_agreement,
            "contradiction_score": contradiction_score,
            "execution_readiness": execution_readiness,
            "active_consolidation": float(1.0 if active_consolidation else 0.0),
            "structure_trade_ready": float(1.0 if effective_structure_trade_ready else 0.0),
            "structure_setup": effective_structure_setup,
            "projected_box_type": projected_box_type,
            "projected_box_direction": projected_box_direction,
            "projected_box_confidence": projected_box_confidence,
            "projected_box_explanation": str(projected_box.get("explanation", "")),
            "projection_bias_confidence": projection_bias_confidence,
            "projection_dominance": projection_dominance,
        }


def _accumulation_distribution(ohlc: list[list[float]], body_sizes: NDArray[np.float32]) -> float:
    """
    Statistics for Data Science — Accumulation/Distribution indicator.
    Uses candle body size as volume proxy (no tick volume available from screenshot).
    A/D = sum over bars of: CLV * Volume_proxy
    CLV = ((Close - Low) - (High - Close)) / (High - Low)
    Positive A/D → accumulation (buyers absorbing supply) → BUY bias
    Negative A/D → distribution (sellers dominating) → SELL bias
    """
    if not ohlc or body_sizes.size == 0:
        return 0.0
    ad = 0.0
    for i, bar in enumerate(ohlc):
        if len(bar) < 4:
            continue
        _o, h, l, c = float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3])
        hl_range = h - l
        if hl_range < 1e-9:
            continue
        clv = ((c - l) - (h - c)) / hl_range
        vol_proxy = float(body_sizes[min(i, len(body_sizes) - 1)]) + 1e-6
        ad += clv * vol_proxy
    # Normalize to [-1, 1]
    max_possible = float(np.sum(np.abs(body_sizes)) + 1e-8)
    return float(np.clip(ad / max_possible, -1.0, 1.0))


def _poly_trend(closes: NDArray[np.float32], degree: int = 2) -> dict[str, float]:
    """
    Polynomial Regression — fit degree-2 curve to close prices.
    Returns slope (positive = uptrend, negative = downtrend) and R².
    """
    if closes.size < (degree + 2):
        return {"slope": 0.0, "r2": 0.0, "curvature": 0.0}
    x = np.arange(closes.size, dtype=np.float64)
    y = closes.astype(np.float64)
    try:
        coeffs = np.polyfit(x, y, degree)
        p = np.poly1d(coeffs)
        y_pred = p(x)
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2)) + 1e-10
        r2 = float(1.0 - ss_res / ss_tot)
        slope = float(coeffs[-2])          # linear coefficient
        curvature = float(coeffs[-3]) if degree >= 2 else 0.0
        return {"slope": slope, "r2": max(0.0, min(1.0, r2)), "curvature": curvature}
    except Exception:
        return {"slope": 0.0, "r2": 0.0, "curvature": 0.0}


def _conformal_interval(returns: NDArray[np.float32], alpha: float = 0.05) -> tuple[float, float]:
    """
    Predictive Analytics — conformal prediction (MAPIE-style).
    Calibrated with leave-one-out residuals on the historical return sequence.
    Returns (q_lower, q_upper) at (1-alpha) coverage = 95%.
    """
    if returns.size < 4:
        return float(np.quantile(returns, alpha)), float(np.quantile(returns, 1 - alpha))
    try:
        from sklearn.linear_model import Ridge

        mapie_factory = _load_mapie_regressor_factory()
        if mapie_factory is None:
            raise RuntimeError("MAPIE runtime unavailable.")

        X = np.arange(len(returns), dtype=np.float64).reshape(-1, 1)
        y = returns.astype(np.float64)
        mapie = mapie_factory(
            estimator=Ridge(alpha=1.0),
            method="plus",
            cv=min(5, len(returns)),
        )
        mapie.fit(X, y)
        next_x = np.array([[len(returns)]], dtype=np.float64)
        _, pis = mapie.predict(next_x, alpha=alpha)
        lo = float(pis[0, 0, 0])
        hi = float(pis[0, 1, 0])
        return lo, hi
    except Exception:
        # Fallback: normal-distribution quantiles
        mu = float(np.mean(returns))
        sigma = float(np.std(returns)) + 1e-10
        z = 1.645
        return float(mu - z * sigma), float(mu + z * sigma)


def conformal_interval(returns: NDArray[np.float32], alpha: float = 0.05) -> tuple[float, float]:
    """Public typed wrapper for conformal interval utility."""
    return _conformal_interval(returns, alpha=alpha)


class ChronosRegressor:
    """
    SIGE-VLA 3.0 probabilistic regressor:
    - Chronos-2 primary (if available)
    - A/D accumulation/distribution (body-size volume proxy)
    - Conformal 95% prediction interval
    - Polynomial trend regression
    - Bayesian averaging of 3 quantiles
    - Force HOLD if interval > 0.4%
    """

    def __init__(self, model_name: str, logger: Any) -> None:
        self.logger = logger
        self.model_name = model_name
        self.strict_no_fallback = _strict_no_fallback_enabled()
        self.pipeline = None
        try:
            if not _chronos_model_loading_enabled():
                raise RuntimeError("Chronos model preload disabled in test runtime")
            if not can_import_chronos_safely():
                raise RuntimeError("Chronos runtime probe failed")
            import importlib
            chronos_mod = importlib.import_module("chronos")
            Chronos2Pipeline = getattr(chronos_mod, "Chronos2Pipeline")
            self.pipeline = Chronos2Pipeline.from_pretrained(model_name)
            self.logger.info("Loaded Chronos model: %s", model_name)
        except Exception as e:
            self.logger.error("Chronos load failed: %s", e)
            if self.strict_no_fallback:
                raise RuntimeError(f"Strict mode: Chronos load failed ({e})")

    def forecast_3m(
        self,
        chart_state: dict[str, Any],
        quantiles: tuple[float, float, float] = (0.05, 0.5, 0.95),
    ) -> Forecast3MOutput:
        """
        Full probabilistic forecast pipeline:
        1. Extract OHLC and body sizes from chart-state payload
        2. Compute A/D accumulation indicator
        3. Polynomial trend regression
        4. Chronos-2 quantile forecast (or statistical fallback)
        5. Conformal prediction interval
        6. Bayesian average of all three 95% quantiles
        7. Force HOLD flag if interval > 0.4%
        """
        ohlc = chart_state.get("ohlc_last20", [])
        base_move = float(chart_state.get("implied_3min_move_pct", 0.0))

        if not ohlc:
            if self.strict_no_fallback:
                raise RuntimeError("Strict mode: Chronos requires non-empty OHLC input.")
            lo_c, hi_c = _conformal_interval(
                np.array([base_move - 0.1, base_move, base_move + 0.1], dtype=np.float32)
            )
            return {
                "q05": lo_c,
                "q50": base_move,
                "q95": hi_c,
                "point": base_move,
                "ad_indicator": 0.0,
                "poly_slope": 0.0,
                "poly_r2": 0.0,
                "force_hold": abs(hi_c - lo_c) > 0.40,
            }

        close_list: list[float] = []
        body_size_list: list[float] = []
        for row in ohlc:
            if not isinstance(row, (list, tuple)):
                continue
            row_vals = cast(list[Any] | tuple[Any, ...], row)
            if len(row_vals) < 4:
                continue
            open_v = float(cast(float | int | str, row_vals[0]))
            close_v = float(cast(float | int | str, row_vals[3]))
            close_list.append(close_v)
            body_size_list.append(abs(close_v - open_v))

        closes = np.array(close_list, dtype=np.float32)
        body_sizes = np.array(body_size_list, dtype=np.float32)

        if closes.size < 5:
            if self.strict_no_fallback:
                raise RuntimeError(f"Strict mode: Chronos requires >=5 close points, got {closes.size}.")
            lo_c, hi_c = _conformal_interval(np.array([base_move], dtype=np.float32))
            return {
                "q05": lo_c, "q50": base_move, "q95": hi_c, "point": base_move,
                "ad_indicator": 0.0, "poly_slope": 0.0, "poly_r2": 0.0,
                "force_hold": abs(hi_c - lo_c) > 0.40,
            }

        # Returns in percent
        returns = np.diff(closes) / np.clip(closes[:-1], 1e-8, None) * 100.0
        mu = float(np.mean(returns))
        sigma = float(np.std(returns)) + 1e-10

        # A/D scalar
        ad_val = _accumulation_distribution(ohlc, body_sizes)

        # Polynomial trend
        trend = _poly_trend(closes, degree=2)

        # Conformal prediction interval
        lo_c, hi_c = _conformal_interval(returns)

        # Chronos quantile prediction
        q05_raw: float | None = None
        q50_raw: float | None = None
        q95_raw: float | None = None
        if self.pipeline is None:
            if self.strict_no_fallback:
                raise RuntimeError("Strict mode: Chronos pipeline is unavailable.")
        else:
            try:
                import torch
                context = torch.tensor(closes.astype(np.float32))
                pred = self.pipeline.predict(context, prediction_length=3)
                # Chronos2Pipeline returns list[Tensor] of shape (num_samples, pred_len)
                samples: NDArray[np.float32] = pred[0].cpu().numpy().astype(np.float32)
                last_price = float(closes[-1])
                sample_returns: NDArray[np.float32] = ((samples - last_price) / max(abs(last_price), 1e-8) * 100.0).astype(np.float32)
                q05_raw = float(np.quantile(sample_returns, quantiles[0]))
                q50_raw = float(np.quantile(sample_returns, quantiles[1]))
                q95_raw = float(np.quantile(sample_returns, quantiles[2]))
            except Exception as e:
                self.logger.warning("Chronos inference: %s", e)
                if self.strict_no_fallback:
                    raise RuntimeError(f"Strict mode: Chronos inference failed ({e})")

        # Statistical quantiles fallback
        if q50_raw is None:
            if self.strict_no_fallback:
                raise RuntimeError("Strict mode: statistical fallback disabled for Chronos.")
            q05_raw = float(np.quantile(returns, quantiles[0]))
            q50_raw = float(np.quantile(returns, quantiles[1]))
            q95_raw = float(np.quantile(returns, quantiles[2]))

        # Bayesian weighted average of Chronos + conformal intervals
        # Weight: Chronos 50%, conformal 30%, base_move 20%
        q05_val = float(q05_raw) if q05_raw is not None else float(np.quantile(returns, quantiles[0]))
        q50_val = float(q50_raw)
        q95_val = float(q95_raw) if q95_raw is not None else float(np.quantile(returns, quantiles[2]))

        q05_final = 0.5 * q05_val + 0.3 * lo_c + 0.2 * (mu - 1.65 * sigma)
        q95_final = 0.5 * q95_val + 0.3 * hi_c + 0.2 * (mu + 1.65 * sigma)
        q50_final = 0.6 * q50_val + 0.2 * mu + 0.2 * (base_move + ad_val * 0.05)

        interval = abs(q95_final - q05_final)
        force_hold = interval > 0.40   # 0.4% interval threshold per spec

        return {
            "q05": float(q05_final),
            "q50": float(q50_final),
            "q95": float(q95_final),
            "point": float(q50_final),
            "ad_indicator": float(ad_val),
            "poly_slope": float(trend["slope"]),
            "poly_r2": float(trend["r2"]),
            "force_hold": bool(force_hold),
        }


class ForecastRouter:
    """
    Explicit forecast routing with no hidden fallback semantics.

    Modes:
      - IMAGE_FUSION: screenshot-native probabilistic forecast (default)
      - CHRONOS_OHLC: Chronos-only mode; requires OHLC in chart-state payload
    """

    def __init__(self, model_name: str, logger: Any, max_interval_pct: float = 0.40) -> None:
        self.logger = logger
        self.mode = os.getenv("PHOENIXGUARD_FORECAST_ENGINE", "IMAGE_FUSION").strip().upper()
        self.max_interval_pct = float(max_interval_pct)
        self.strict_no_fallback = _strict_no_fallback_enabled()

        if self.mode == "CHRONOS_OHLC":
            self.engine: Any = ChronosRegressor(model_name=model_name, logger=logger)
        elif self.mode == "IMAGE_FUSION":
            self.engine = ImageFusionRegressor(logger=logger, max_interval_pct=max_interval_pct)
        else:
            raise RuntimeError(
                f"Unknown PHOENIXGUARD_FORECAST_ENGINE='{self.mode}'. "
                "Use IMAGE_FUSION or CHRONOS_OHLC."
            )

        self.logger.info("Forecast router mode: %s", self.mode)

    def forecast_3m(
        self,
        chart_state: dict[str, Any],
        quantiles: tuple[float, float, float] = (0.05, 0.5, 0.95),
        detections: list[dict[str, Any]] | None = None,
        memory_similarity: float = 0.0,
        memory_direction: str = "HOLD",
        transition_summary: Mapping[str, float] | None = None,
        memory_summary: Mapping[str, Any] | None = None,
    ) -> Forecast3MOutput:
        if self.mode == "CHRONOS_OHLC":
            return cast(ChronosRegressor, self.engine).forecast_3m(chart_state, quantiles=quantiles)

        return cast(ImageFusionRegressor, self.engine).forecast_3m(
            chart_state,
            quantiles=quantiles,
            detections=detections,
            memory_similarity=memory_similarity,
            memory_direction=memory_direction,
            transition_summary=transition_summary,
            memory_summary=memory_summary,
        )
