"""
PhoenixGuard SIGE-VLA 3.0 - 13 Skill Gates + Trainable Router
==============================================================
Gates 1-8  (original) + 5 advanced gates:
    9.  gate_regression_error_estimation  - poly R2 on extracted prices
    10. gate_knowledge_representation     - ontology predicate match
    11. gate_candle_group_context         - 25-candle box story / zone context
    12. gate_formal_automata              - FSM: Idle->Consol->Reversal->Impulse
    13. gate_predictive_analytics         - full probabilistic fusion

Router: LinearRouter(13->13) updated by EMA over session feedback history.
Consensus: confidence >= 0.82 AND gates_passing >= 9 AND memory_sim >= 0.87
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast
import heapq
import re

import numpy as np
from numpy.typing import NDArray
import torch
import torch.nn as nn


# FSM states for gate_discrete_fsm (legacy) and gate_formal_automata (new)
FSM_STATES = ["StrongBull", "Bull", "WeakBull", "Neutral", "WeakBear", "Bear", "StrongBear"]
# Formal automata states for 808FX reversal-after-consolidation pattern
_FA_STATES = ["Idle", "ConsolidationDetected", "ReversalAfterConsolidation", "ContinuationImpulse"]
_SIGNAL_ARTIFACT_PATTERNS = {"latest_parse_quality", "scene_parse_quality"}
_WITH_TREND_PHASES = {"with_trend_push", "with_trend_pause", "continuation_base"}
_COUNTER_TREND_PHASES = {"counter_trend_pullback", "counter_trend_spike", "reversal_base"}
_VALID_ENTRY_TYPES = {"reversal", "continuation"}
_VALID_CONTINUATION_SIGNALS = {"impulse_pause", "breakout", "reversal_release", "range_break_watch"}
_VALID_REVERSAL_SIGNALS = {"wick_rejection", "engulfing", "reversal"}
_VALID_DIRECTIONS = {"BUY", "SELL"}
# 808FX style ontology predicates
# Includes the actual chart-state signal values (wick_rejection, engulfing, reversal,
# impulse_pause, breakout) as well as legacy canonical pattern names.
# Both BUY and SELL share these signal types -- directional coherence is enforced
# via the `direction` field check inside gate_knowledge_representation.
_ONTOLOGY_BUY = {
    # Chart-state output values (reversal_signal / continuation_signal fields)
    "wick_rejection", "engulfing", "reversal", "impulse_pause", "breakout",
    "reversal_release", "range_break_watch",
    # Legacy / canonical pattern names (kept for completeness)
    "bullish_engulf", "pin_bar_bottom", "double_bottom", "morning_star", "hammer",
}
_ONTOLOGY_SELL = {
    # Chart-state output values (reversal_signal / continuation_signal fields)
    "wick_rejection", "engulfing", "reversal", "impulse_pause", "breakout",
    "reversal_release", "range_break_watch",
    # Legacy / canonical pattern names (kept for completeness)
    "bearish_engulf", "pin_bar_top", "double_top", "evening_star", "shooting_star",
}


def _normalize_label(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(numeric):
        return float(default)
    return numeric


def _clip01(value: Any, default: float = 0.0) -> float:
    return float(np.clip(_finite_float(value, default), 0.0, 1.0))


def _torch_tensor(data: Any, *, dtype: Any) -> torch.Tensor:
    tensor_fn = cast(Callable[..., torch.Tensor], getattr(torch, "tensor"))
    return tensor_fn(data, dtype=dtype)


def _torch_sigmoid(value: Any) -> torch.Tensor:
    sigmoid_fn = cast(Callable[[Any], torch.Tensor], getattr(torch, "sigmoid"))
    return sigmoid_fn(value)


def _torch_softmax(value: Any, *, dim: int) -> Any:
    softmax_fn = cast(Callable[..., Any], getattr(torch, "softmax"))
    return softmax_fn(value, dim=dim)


def _torch_diag(value: Any) -> Any:
    diag_fn = cast(Callable[[Any], Any], getattr(torch, "diag"))
    return diag_fn(value)


def _safe_probability_view(probs: dict[str, Any]) -> tuple[dict[str, float], str, float, float]:
    raw = np.array(
        [
            _clip01(probs.get("BUY", 0.0), 0.0),
            _clip01(probs.get("SELL", 0.0), 0.0),
            _clip01(probs.get("HOLD", 0.0), 0.0),
        ],
        dtype=np.float64,
    )
    total = float(raw.sum())
    if not np.isfinite(total) or total <= 0.0:
        normalized = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=np.float64)
    else:
        normalized = raw / total
    labels = ("BUY", "SELL", "HOLD")
    order = np.argsort(normalized)[::-1]
    top_index = int(order[0])
    second_index = int(order[1])
    top_prob = float(normalized[top_index])
    second_prob = float(normalized[second_index])
    return (
        {"BUY": float(normalized[0]), "SELL": float(normalized[1]), "HOLD": float(normalized[2])},
        labels[top_index],
        top_prob,
        float(max(0.0, top_prob - second_prob)),
    )


@dataclass
class GateOutput:
    name: str
    score: float
    pass_fail: bool
    detail: dict[str, Any]


class LinearRouter(nn.Module):
    """Trainable 13->13 gate importance router (EMA updated from feedback)."""
    def __init__(self, n_gates: int = 13):
        module_init = cast(Callable[[Any], None], nn.Module.__init__)
        module_init(self)
        self.layer = nn.Linear(n_gates, n_gates, bias=False)
        init_module = cast(Any, nn.init)
        init_module.eye_(self.layer.weight)

    def forward(self, scores: torch.Tensor) -> torch.Tensor:
        return _torch_sigmoid(self.layer(scores))


class SkillGatedMoE(nn.Module):
    def __init__(self, n_features: int = 16, n_gates: int = 13):
        module_init = cast(Callable[[Any], None], nn.Module.__init__)
        module_init(self)
        self.router = nn.Sequential(
            nn.Linear(n_features, 32),
            nn.GELU(),
            nn.Linear(32, n_gates),
        )

    def route_weights(self, feat: NDArray[np.float32]) -> NDArray[np.float32]:
        x = _torch_tensor(feat, dtype=torch.float32).unsqueeze(0)
        with torch.inference_mode():
            routed = _torch_softmax(self.router(x), dim=-1).squeeze(0)
            to_numpy = cast(Callable[[], Any], getattr(routed, "numpy"))
        return np.asarray(to_numpy(), dtype=np.float32)


class CurriculumGates:
    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self.state = "Neutral"
        self._fa_state: str = "Idle"
        self._router = LinearRouter(n_gates=13)
        self._feedback_history: list[NDArray[np.float32]] = []

    # ------------------------------------------------------------------
    # Gates 1-8 (original, enhanced)
    # ------------------------------------------------------------------

    def gate_probability_conformal(self, probs: dict[str, float], q05: float, q95: float) -> GateOutput:
        lower = _finite_float(q05, 0.0)
        upper = _finite_float(q95, 0.0)
        interval = abs(upper - lower)
        normalized_probs, top_action, top_prob, edge = _safe_probability_view(probs)
        ordered = bool(lower <= upper)
        interval_penalty = float(np.clip(interval / 0.80, 0.0, 1.0))
        calibration_quality = float(np.clip(1.0 - interval_penalty, 0.0, 1.0))
        calibrated = float(np.clip(top_prob * (0.70 + 0.30 * edge) * calibration_quality, 0.0, 1.0))
        pass_fail = bool(
            ordered
            and top_action in _VALID_DIRECTIONS
            and top_prob >= 0.54
            and edge >= 0.10
            and interval <= 0.65
            and calibrated >= 0.45
        )
        return GateOutput(
            "prob_stats",
            calibrated,
            pass_fail,
            {
                "interval": interval,
                "calibrated": calibrated,
                "ordered": ordered,
                "top_action": top_action,
                "top_prob": top_prob,
                "edge": edge,
                "normalized_probs": normalized_probs,
            },
        )

    def gate_discrete_fsm(self, momentum_bias: str, explanation: str) -> GateOutput:
        idx = FSM_STATES.index(self.state)
        bias = str(momentum_bias or "").strip().lower()
        text = str(explanation or "").lower()
        # Lower indices are more bullish and higher indices are more bearish.
        step = -1 if bias == "bullish" else (1 if bias == "bearish" else 0)
        if re.search(r"breakout|impulse|trend up", text):
            step -= 1
        if re.search(r"rejection|trend down|sell pressure", text):
            step += 1
        idx = int(np.clip(idx + step, 0, len(FSM_STATES) - 1))
        self.state = FSM_STATES[idx]
        directional_strength = float(np.clip(abs(idx - 3) / 3.0, 0.0, 1.0))
        pass_fail = bool(bias in {"bullish", "bearish"} and directional_strength >= 0.33)
        return GateOutput(
            "discrete_fsm",
            directional_strength,
            pass_fail,
            {"state": self.state, "momentum_bias": bias, "directional_strength": directional_strength},
        )

    def gate_algorithmic_heap(self, sub_signals: list[tuple[float, str]]) -> GateOutput:
        best_by_pattern: dict[str, float] = {}
        for score, name in sub_signals:
            normalized = str(name).strip().lower().replace(" ", "_")
            if normalized in _SIGNAL_ARTIFACT_PATTERNS:
                continue
            clipped = _clip01(score, 0.0)
            if clipped <= best_by_pattern.get(normalized, -1.0):
                continue
            best_by_pattern[normalized] = clipped
        heap: list[tuple[float, str]] = [(-score, pattern) for pattern, score in best_by_pattern.items()]
        heapq.heapify(heap)
        top: list[tuple[float, str]] = []
        for _ in range(min(5, len(heap))):
            s, n = heapq.heappop(heap)
            top.append((-s, n))
        score = float(np.mean([x[0] for x in top])) if top else 0.0
        top_score = float(top[0][0]) if top else 0.0
        enough_independent_evidence = len(top) >= 2 or top_score >= 0.82
        pass_fail = bool(enough_independent_evidence and score >= 0.52)
        return GateOutput(
            "algo_heap",
            max(0.0, min(1.0, score)),
            pass_fail,
            {"top": top, "unique_signal_count": len(top), "top_score": top_score},
        )

    def gate_meta_stacking(self, module_logits: NDArray[np.float32]) -> GateOutput:
        try:
            values = np.asarray(module_logits, dtype=np.float64).reshape(-1)
        except (TypeError, ValueError):
            values = np.zeros(0, dtype=np.float64)
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return GateOutput("ml_stacking", 0.0, False, {"mean": 0.0, "reason": "no_finite_logits"})
        clipped = np.clip(finite, 0.0, 1.0)
        total = float(clipped.sum())
        if clipped.size >= 3 and 0.98 <= total <= 1.02:
            sorted_vals = np.sort(clipped)[::-1]
            top = float(sorted_vals[0])
            edge = float(max(0.0, sorted_vals[0] - sorted_vals[1]))
            score = float(np.clip(top * (0.85 + 0.15 * edge), 0.0, 1.0))
            pass_fail = bool(top >= 0.55 and edge >= 0.10 and score >= 0.50)
            return GateOutput("ml_stacking", score, pass_fail, {"mean": float(np.mean(clipped)), "top": top, "edge": edge})
        mean = float(np.mean(clipped))
        spread = float(np.std(clipped))
        score = float(np.clip(mean * (0.85 + 0.15 * min(1.0, spread * 2.0)), 0.0, 1.0))
        return GateOutput("ml_stacking", score, score >= 0.52, {"mean": mean, "std": spread})

    def gate_context_retrieval(self, recent_feedback_count: int) -> GateOutput:
        feedback_count = max(0.0, _finite_float(recent_feedback_count, 0.0))
        score = float(np.tanh(feedback_count / 60.0))
        pass_fail = bool(feedback_count >= 20.0 and score >= 0.32)
        return GateOutput("db_context", score, pass_fail, {"feedback_count": feedback_count})

    def gate_ops_stability(self, queue_depth: int, gpu_mem_ok: bool) -> GateOutput:
        queue = max(0.0, _finite_float(queue_depth, 0.0))
        score = 1.0 - (0.30 if queue > 4 else 0.0) - (0.35 if not gpu_mem_ok else 0.0)
        score = float(np.clip(score, 0.0, 1.0))
        return GateOutput("ops_stability", score, score >= 0.70, {"queue_depth": queue, "gpu_mem_ok": bool(gpu_mem_ok)})

    def gate_ui_analytics(self, has_dashboard: bool) -> GateOutput:
        dashboard_ready = bool(has_dashboard)
        score = 1.0 if dashboard_ready else 0.35
        return GateOutput("ui_analytics", score, dashboard_ready, {"dashboard": dashboard_ready})

    def gate_meta_constraints(self, risk_ethical_ok: bool) -> GateOutput:
        score = 1.0 if risk_ethical_ok else 0.0
        return GateOutput("meta_constraints", score, risk_ethical_ok, {"guardrail": risk_ethical_ok})

    # ------------------------------------------------------------------
    # New Gates for Non-breaking Upgrades (SIGE-VLA 3.1)
    # ------------------------------------------------------------------
    def continuation_strength_gate(self, macro_trend: str, local_phase: str, control_strength_delta: float) -> GateOutput:
        """
        Scores how strongly the current trend is continuing, based on macro/local agreement and control strength.
        """
        macro = str(macro_trend or "").strip().upper()
        phase = _normalize_label(local_phase)
        local_with_trend = phase in _WITH_TREND_PHASES
        macro_valid = macro in {"BULL", "BEAR"}
        magnitude = _clip01(abs(_finite_float(control_strength_delta, 0.0)), 0.0)
        base = 0.35 if (local_with_trend and macro_valid) else 0.10
        strength = float(np.clip(base + 0.65 * magnitude, 0.0, 1.0))
        pass_fail = bool(macro_valid and local_with_trend and strength >= 0.60)
        return GateOutput(
            "continuation_strength",
            strength,
            pass_fail,
            {"macro_trend": macro, "local_phase": phase, "control_strength_delta": control_strength_delta},
        )

    def opposition_strength_gate(self, macro_trend: str, local_phase: str, geometry_conflict: float) -> GateOutput:
        """
        Scores how strong the opposition to the current trend is, using local/macro contradiction and geometry conflict.
        """
        macro = str(macro_trend or "").strip().upper()
        phase = _normalize_label(local_phase)
        local_against_trend = phase in _COUNTER_TREND_PHASES
        conflict_score = _clip01(geometry_conflict, 0.0)
        phase_pressure = 0.65 if local_against_trend else 0.15
        opposition = float(np.clip(0.55 * phase_pressure + 0.45 * conflict_score, 0.0, 1.0))
        pass_fail = bool(macro in {"BULL", "BEAR"} and local_against_trend and opposition >= 0.55)
        return GateOutput(
            "opposition_strength",
            opposition,
            pass_fail,
            {"macro_trend": macro, "local_phase": phase, "geometry_conflict": conflict_score},
        )

    def macro_local_alignment_gate(self, macro_trend: str, local_phase: str) -> GateOutput:
        """
        Scores the alignment between macro and local structure.
        """
        macro = str(macro_trend or "").strip().upper()
        phase = _normalize_label(local_phase)
        if phase == "with_trend_push":
            score = 1.0
        elif phase in {"with_trend_pause", "continuation_base"}:
            score = 0.78
        elif phase == "counter_trend_pullback":
            score = 0.25
        else:
            score = 0.0
        aligned = macro in {"BULL", "BEAR"} and phase in _WITH_TREND_PHASES
        if macro not in {"BULL", "BEAR"}:
            score = min(score, 0.35)
        return GateOutput("macro_local_alignment", score, aligned, {"macro_trend": macro, "local_phase": phase})

    def memory_regime_agreement_gate(self, macro_trend: str, memory_similarity: float, memory_label: str) -> GateOutput:
        """
        Scores agreement between macro trend and memory label, weighted by similarity.
        """
        macro = str(macro_trend or "").strip().upper()
        mapped_label = "BUY" if macro == "BULL" else ("SELL" if macro == "BEAR" else "HOLD")
        normalized_label = str(memory_label or "").strip().upper()
        similarity = _clip01(memory_similarity, 0.0)
        if similarity < 0.35 or normalized_label not in _VALID_DIRECTIONS:
            return GateOutput(
                "memory_regime_agreement",
                0.0,
                False,
                {
                    "macro_trend": macro,
                    "memory_label": normalized_label,
                    "memory_similarity": similarity,
                    "reason": "insufficient_memory_alignment_signal",
                    "required": False,
                    "neutral": True,
                },
            )
        agreement = mapped_label in _VALID_DIRECTIONS and normalized_label == mapped_label
        score = similarity if agreement else 0.0
        pass_fail = bool(agreement and similarity >= 0.62)
        return GateOutput(
            "memory_regime_agreement",
            score,
            pass_fail,
            {"macro_trend": macro, "memory_label": normalized_label, "memory_similarity": similarity, "required": True},
        )

    def execution_permission_gate(self, latest_candle_confidence: float, phase_risk: str, reliability: float = 1.0) -> GateOutput:
        """
        Determines if execution is permitted, based on confidence, phase risk, and reliability.
        """
        normalized_risk = _normalize_label(phase_risk)
        if normalized_risk in {"exhaustion_risk", "managed_counter_trend", "contradiction"}:
            risk_multiplier = 0.60
        elif normalized_risk == "chop_risk":
            risk_multiplier = 0.78
        else:
            risk_multiplier = 1.0
        score = _clip01(latest_candle_confidence, 0.0) * _clip01(reliability, 0.0) * risk_multiplier
        threshold = 0.58 if normalized_risk == "breakout_risk" else 0.64
        if normalized_risk in {"exhaustion_risk", "managed_counter_trend", "contradiction"}:
            threshold = 0.70
        pass_fail = bool(score >= threshold)
        return GateOutput(
            "execution_permission",
            score,
            pass_fail,
            {
                "latest_candle_confidence": _clip01(latest_candle_confidence, 0.0),
                "phase_risk": normalized_risk,
                "reliability": _clip01(reliability, 0.0),
                "threshold": threshold,
            },
        )

    def forecast_calibration_gate(
        self,
        forecast: dict[str, Any],
        latest_candle_confidence: float,
        reliability: float,
    ) -> GateOutput:
        has_quantiles = all(key in forecast for key in ("q05", "q50", "q95"))
        q05 = _finite_float(forecast.get("q05", 0.0), 0.0)
        q50 = _finite_float(forecast.get("q50", forecast.get("point", 0.0)), 0.0)
        q95 = _finite_float(forecast.get("q95", 0.0), 0.0)
        interval = abs(q95 - q05)
        threshold = max(0.05, _finite_float(forecast.get("hold_threshold_used", 0.40), 0.40))
        path_confidence = _clip01(forecast.get("path_confidence", 0.0), 0.0)
        execution_readiness = _clip01(forecast.get("execution_readiness", 0.0), 0.0)
        contradiction_score = _clip01(forecast.get("contradiction_score", 0.0), 0.0)
        ordered = bool(q05 <= q50 <= q95)
        interval_penalty = float(np.clip(max(0.0, interval - threshold) / max(threshold * 1.25, 1e-6), 0.0, 1.0))
        score = float(
            np.clip(
                0.32 * path_confidence
                + 0.22 * execution_readiness
                + 0.16 * _clip01(latest_candle_confidence, 0.0)
                + 0.16 * _clip01(reliability, 0.0)
                + 0.14 * (1.0 - contradiction_score)
                + (0.08 if ordered else -0.10)
                - 0.18 * interval_penalty,
                0.0,
                1.0,
            )
        )
        pass_fail = bool(has_quantiles and ordered and interval > 0.0 and score >= 0.60 and interval <= threshold * 1.08)
        return GateOutput(
            "forecast_calibration",
            score,
            pass_fail,
            {
                "ordered": ordered,
                "has_quantiles": has_quantiles,
                "interval": interval,
                "hold_threshold_used": threshold,
                "path_confidence": path_confidence,
                "execution_readiness": execution_readiness,
                "contradiction_score": contradiction_score,
            },
        )

    def interval_efficiency_gate(self, forecast: dict[str, Any]) -> GateOutput:
        has_quantiles = "q05" in forecast and "q95" in forecast
        interval = abs(
            _finite_float(forecast.get("q95", forecast.get("interval", 0.0)), 0.0)
            - _finite_float(forecast.get("q05", 0.0), 0.0)
        )
        threshold = max(0.05, _finite_float(forecast.get("hold_threshold_used", 0.40), 0.40))
        ratio = float(interval / max(threshold, 1e-6))
        path_confidence = _clip01(forecast.get("path_confidence", 0.0), 0.0)
        execution_readiness = _clip01(forecast.get("execution_readiness", 0.0), 0.0)
        efficiency = float(np.clip(1.0 - max(0.0, ratio - 0.55) / 1.10, 0.0, 1.0))
        score = float(np.clip(0.60 * efficiency + 0.25 * path_confidence + 0.15 * execution_readiness, 0.0, 1.0))
        pass_fail = bool(has_quantiles and interval > 0.0 and ratio <= 1.00 and score >= 0.56)
        return GateOutput(
            "interval_efficiency",
            score,
            pass_fail,
            {
                "interval": interval,
                "has_quantiles": has_quantiles,
                "hold_threshold_used": threshold,
                "interval_ratio": ratio,
                "path_confidence": path_confidence,
                "execution_readiness": execution_readiness,
            },
        )

    def regime_stability_gate(
        self,
        forecast: dict[str, Any],
        market_state: dict[str, Any],
        memory_summary: dict[str, Any] | None,
        geometry_conflict: bool,
        reliability: float,
        ood_summary: dict[str, Any] | None = None,
    ) -> GateOutput:
        contradiction_score = _clip01(forecast.get("contradiction_score", 0.0), 0.0)
        fakeout_prob = _clip01(forecast.get("fakeout_prob", 0.25), 0.25)
        reversal_prob = _clip01(forecast.get("reversal_attempt_prob", 0.25), 0.25)
        ambiguity = _clip01((memory_summary or {}).get("ambiguity", 0.0), 0.0)
        novelty = _clip01((ood_summary or {}).get("style_novelty", 0.0), 0.0)
        local_phase = str(market_state.get("local_phase", "")).lower()
        local_counter = 1.0 if local_phase in _COUNTER_TREND_PHASES else 0.0
        risk = float(
            np.clip(
                0.28 * contradiction_score
                + 0.22 * fakeout_prob
                + 0.14 * reversal_prob
                + 0.14 * ambiguity
                + 0.10 * novelty
                + 0.08 * float(bool(geometry_conflict))
                + 0.04 * local_counter,
                0.0,
                1.0,
            )
        )
        score = float(np.clip(1.0 - risk + 0.10 * _clip01(reliability, 0.0), 0.0, 1.0))
        pass_fail = bool(score >= 0.52)
        return GateOutput(
            "regime_stability",
            score,
            pass_fail,
            {
                "contradiction_score": contradiction_score,
                "fakeout_prob": fakeout_prob,
                "reversal_attempt_prob": reversal_prob,
                "memory_ambiguity": ambiguity,
                "style_novelty": novelty,
                "geometry_conflict": bool(geometry_conflict),
                "local_phase": local_phase,
            },
        )

    def transition_alignment_gate(
        self,
        chart_state: dict[str, Any],
        forecast: dict[str, Any],
        transition_summary: dict[str, Any] | None,
    ) -> GateOutput:
        transition = transition_summary or {}
        has_transition_signal = any(
            key in transition or key in forecast
            for key in (
                "continue_prob",
                "continue",
                "pullback_prob",
                "pullback",
                "reversal_attempt_prob",
                "reversal_attempt",
                "fakeout_prob",
                "fakeout",
            )
        )
        continue_prob = _clip01(
            transition.get("continue_prob", transition.get("continue", forecast.get("continue_prob", 0.25))),
            0.25,
        )
        pullback_prob = _clip01(
            transition.get("pullback_prob", transition.get("pullback", forecast.get("pullback_prob", 0.25))),
            0.25,
        )
        reversal_prob = _clip01(
            transition.get("reversal_attempt_prob", transition.get("reversal_attempt", forecast.get("reversal_attempt_prob", 0.25))),
            0.25,
        )
        fakeout_prob = _clip01(
            transition.get("fakeout_prob", transition.get("fakeout", forecast.get("fakeout_prob", 0.25))),
            0.25,
        )
        entry_type = _normalize_label(chart_state.get("entry_type", ""))
        structure_setup = _normalize_label(forecast.get("structure_setup", chart_state.get("structure_setup", "none")))
        projected_confidence = _clip01(forecast.get("projected_box_confidence", 0.0), 0.0)
        path_confidence = _clip01(forecast.get("path_confidence", 0.0), 0.0)
        structure_ready = _clip01(forecast.get("structure_trade_ready", chart_state.get("structure_trade_ready", 0.0)), 0.0)

        if structure_setup == "reversal_release":
            favorable = min(1.0, reversal_prob + 0.35 * continue_prob)
            hazard = max(fakeout_prob, 0.70 * pullback_prob)
        elif entry_type == "continuation" or structure_setup in {"consolidation_breakout", "impulse_chain"}:
            favorable = continue_prob
            hazard = max(fakeout_prob, pullback_prob, 0.85 * reversal_prob)
        elif entry_type == "reversal":
            favorable = min(1.0, reversal_prob + 0.35 * continue_prob)
            hazard = max(fakeout_prob, 0.70 * pullback_prob)
        else:
            favorable = max(continue_prob, reversal_prob)
            hazard = max(fakeout_prob, pullback_prob)

        score = float(
            np.clip(
                0.50
                + 0.55 * (favorable - 0.70 * hazard)
                + 0.10 * path_confidence
                + 0.10 * projected_confidence
                + 0.05 * structure_ready,
                0.0,
                1.0,
            )
        )
        pass_fail = bool(has_transition_signal and favorable >= hazard + 0.04 and score >= 0.58)
        return GateOutput(
            "transition_alignment",
            score,
            pass_fail,
            {
                "entry_type": entry_type,
                "structure_setup": structure_setup,
                "continue_prob": continue_prob,
                "pullback_prob": pullback_prob,
                "reversal_attempt_prob": reversal_prob,
                "fakeout_prob": fakeout_prob,
                "favorable": favorable,
                "hazard": hazard,
                "has_transition_signal": has_transition_signal,
            },
        )

    def candle_group_context_gate(self, chart_state: dict[str, Any]) -> GateOutput:
        """
        Candle Group Context — require a 25-candle box story with zone confirmation.

        The gate is intentionally fail-closed: no 25-candle study, no entry.
        Bottom-zone reclaim supports BUY; top-zone rejection supports SELL.
        """
        summary_raw = chart_state.get("candle_group_summary", {})
        summary = cast(dict[str, Any], summary_raw) if isinstance(summary_raw, dict) else {}
        window_size = int(max(0.0, _finite_float(summary.get("window_size", chart_state.get("recent_candle_count", 0)), 0.0)))
        window_ready = bool(summary.get("window_ready", window_size >= 25))
        zone = _normalize_label(summary.get("box_zone", "middle"))
        group_story = _normalize_label(summary.get("group_story", "monitor_only"))
        group_bias_direction = str(summary.get("group_bias_direction", chart_state.get("direction", "HOLD")) or "HOLD").strip().upper()
        buy_pullback_valid = _truthy(summary.get("buy_pullback_valid", False))
        sell_pullback_valid = _truthy(summary.get("sell_pullback_valid", False))
        entry_ready = _truthy(summary.get("entry_ready", buy_pullback_valid or sell_pullback_valid))
        path_clarity = _clip01(summary.get("path_clarity", chart_state.get("path_clarity", 0.0)), 0.0)
        box_sequence_agreement = _clip01(summary.get("box_sequence_agreement", chart_state.get("box_sequence_agreement", 0.0)), 0.0)
        trend_strength = _clip01(summary.get("trend_strength", chart_state.get("trend_strength", 0.0)), 0.0)
        group_confidence = _clip01(summary.get("group_bias_confidence", 0.0), 0.0)
        bottom_reclaim_valid = bool(zone == "bottom" and buy_pullback_valid)
        top_rejection_valid = bool(zone == "top" and sell_pullback_valid)
        aligned = bool(
            window_ready
            and entry_ready
            and group_bias_direction in _VALID_DIRECTIONS
            and ((group_bias_direction == "BUY" and bottom_reclaim_valid) or (group_bias_direction == "SELL" and top_rejection_valid))
        )
        score = float(
            np.clip(
                0.20
                + 0.28 * min(1.0, window_size / 25.0)
                + 0.18 * group_confidence
                + 0.16 * box_sequence_agreement
                + 0.10 * path_clarity
                + 0.08 * trend_strength
                + 0.10 * float(aligned),
                0.0,
                1.0,
            )
        )
        pass_fail = bool(
            aligned
            and score >= 0.62
            and box_sequence_agreement >= 0.45
            and path_clarity >= 0.45
        )
        return GateOutput(
            "candle_group_context",
            score,
            pass_fail,
            {
                "window_size": window_size,
                "window_ready": window_ready,
                "box_zone": zone,
                "group_story": group_story,
                "group_bias_direction": group_bias_direction,
                "group_bias_confidence": group_confidence,
                "buy_pullback_valid": buy_pullback_valid,
                "sell_pullback_valid": sell_pullback_valid,
                "bottom_reclaim_valid": bottom_reclaim_valid,
                "top_rejection_valid": top_rejection_valid,
                "entry_ready": entry_ready,
                "path_clarity": path_clarity,
                "box_sequence_agreement": box_sequence_agreement,
                "trend_strength": trend_strength,
            },
        )

    # ------------------------------------------------------------------
    # Gate 9 — Regression Error Estimation
    # ------------------------------------------------------------------
    def gate_regression_error_estimation(
        self, prices: list[float], direction_prob: float
    ) -> GateOutput:
        """
        Polynomial Regression — fit degree-2 curve to extracted price floats.
        High R² means the trend is clean and the model is reading a clear structure.
        Gate passes if R² >= 0.55 AND direction_prob >= 0.55.
        """
        clean_prices: list[float] = []
        for raw_price in prices:
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if np.isfinite(price):
                clean_prices.append(price)
        direction_strength = _clip01(direction_prob, 0.0)
        if len(clean_prices) < 4:
            return GateOutput("regression_est", 0.3, False, {"r2": 0.0, "reason": "too_few_prices"})
        x = np.arange(len(clean_prices), dtype=np.float64)
        y = np.array(clean_prices, dtype=np.float64)
        price_span = float(np.max(y) - np.min(y))
        price_scale = max(float(np.mean(np.abs(y))), 1e-9)
        relative_span = float(price_span / price_scale)
        if relative_span < 1e-5:
            return GateOutput(
                "regression_est",
                0.20 * direction_strength,
                False,
                {"r2": 0.0, "price_span": price_span, "relative_span": relative_span, "reason": "flat_or_degenerate_prices"},
            )
        try:
            coeffs = np.polyfit(x, y, 2)
            y_hat = np.poly1d(coeffs)(x)
            ss_res = float(np.sum((y - y_hat) ** 2))
            ss_tot = float(np.sum((y - y.mean()) ** 2)) + 1e-10
            r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot))
        except Exception:
            r2 = 0.0
        score = float(np.clip(0.62 * r2 + 0.30 * direction_strength + 0.08 * min(1.0, relative_span / 0.001), 0.0, 1.0))
        pass_fail = bool(r2 >= 0.62 and direction_strength >= 0.58 and relative_span >= 1e-5)
        return GateOutput("regression_est", score, pass_fail, {"r2": r2, "price_span": price_span, "relative_span": relative_span})

    # ------------------------------------------------------------------
    # Gate 10 — Knowledge Representation (Ontology)
    # ------------------------------------------------------------------
    def gate_knowledge_representation(self, chart_state: dict[str, Any]) -> GateOutput:
        """
        Knowledge Representation — check if detected pattern is in 808FX ontology.
        Confirming entry_type + reversal_signal = valid style predicate → pass.
        """
        entry_type = _normalize_label(chart_state.get("entry_type", ""))
        rev_signal = _normalize_label(chart_state.get("reversal_signal", ""))
        cont_signal = _normalize_label(chart_state.get("continuation_signal", ""))
        direction = str(chart_state.get("direction", "HOLD") or "HOLD").strip().upper()
        direction_probability = _clip01(chart_state.get("direction_probability", 1.0), 1.0)
        entry_valid = entry_type in _VALID_ENTRY_TYPES
        reversal_match = entry_type == "reversal" and rev_signal in _VALID_REVERSAL_SIGNALS
        continuation_match = entry_type == "continuation" and cont_signal in _VALID_CONTINUATION_SIGNALS
        direction_specific_buy = rev_signal in _ONTOLOGY_BUY or cont_signal in _ONTOLOGY_BUY
        direction_specific_sell = rev_signal in _ONTOLOGY_SELL or cont_signal in _ONTOLOGY_SELL
        generic_match = reversal_match or continuation_match
        direction_match = (
            (direction == "BUY" and (generic_match or direction_specific_buy))
            or (direction == "SELL" and (generic_match or direction_specific_sell))
        )
        coherent = bool(entry_valid and direction in _VALID_DIRECTIONS and direction_match and direction_probability >= 0.55)
        partial_match = bool(entry_valid and (generic_match or direction_specific_buy or direction_specific_sell))
        score = 0.92 if coherent else (0.50 if partial_match else (0.30 if entry_valid else 0.15))
        return GateOutput(
            "knowledge_rep", score, coherent,
            {
                "entry_type": entry_type,
                "reversal_signal": rev_signal,
                "continuation_signal": cont_signal,
                "coherent": coherent,
                "direction": direction,
                "direction_probability": direction_probability,
                "reversal_match": reversal_match,
                "continuation_match": continuation_match,
                "matched_buy": direction_specific_buy,
                "matched_sell": direction_specific_sell,
            },
        )

    # ------------------------------------------------------------------
    # Gate 11 — Formal Automata (FSM: Reversal After Consolidation)
    # ------------------------------------------------------------------
    def gate_formal_automata(self, chart_state: dict[str, Any]) -> GateOutput:
        """
        Formal Language / Automata Theory — 4-state FSM:
          Idle -> ConsolidationDetected -> ReversalAfterConsolidation -> ContinuationImpulse
        
        State transitions driven by chart-state candlestick fields.
        The 808FX style targets: consolidation (4+ even bars) followed by sharp reversal.
        Gate passes if FSM reaches ReversalAfterConsolidation or ContinuationImpulse.
        """
        consol = int(max(0.0, _finite_float(chart_state.get("consolidation_streak", 0), 0.0)))
        consol_type = _normalize_label(chart_state.get("consolidation_type", "none"))
        consol_score = _clip01(chart_state.get("consolidation_score", 0.0), 0.0)
        rev_sig = _normalize_label(chart_state.get("reversal_signal", ""))
        cont_sig = _normalize_label(chart_state.get("continuation_signal", ""))
        cont_prob = _clip01(chart_state.get("continuation_probability", 0.0), 0.0)
        rev_prob = _clip01(chart_state.get("reversal_probability", 0.0), 0.0)

        consolidation_detected = bool(
            (consol >= 4 and consol_score >= 0.45)
            or (consol >= 3 and consol_score >= 0.62)
            or (consol_type in {"range", "box", "base", "compression", "consolidation"} and consol_score >= 0.58)
        )
        reversal_detected = bool(
            rev_sig in _VALID_REVERSAL_SIGNALS
            and rev_prob >= 0.30
        )
        continuation_detected = bool(
            cont_sig in {"breakout", "impulse_pause", "reversal_release"}
            and cont_prob >= max(0.45, 0.85 * rev_prob)
        )

        # Transitions
        if self._fa_state == "Idle":
            if consolidation_detected:
                self._fa_state = "ConsolidationDetected"
        if self._fa_state == "ConsolidationDetected":
            if reversal_detected:
                self._fa_state = "ReversalAfterConsolidation"
            elif consol < 2 and consol_score < 0.35:
                self._fa_state = "Idle"           # consolidation broken without reversal
        if self._fa_state == "ReversalAfterConsolidation":
            if continuation_detected:
                self._fa_state = "ContinuationImpulse"
        elif self._fa_state == "ContinuationImpulse":
            self._fa_state = "Idle"               # reset after impulse captured

        state_scores = {
            "Idle": 0.3,
            "ConsolidationDetected": 0.5,
            "ReversalAfterConsolidation": 0.85,
            "ContinuationImpulse": 1.0,
        }
        score = float(min(1.0, state_scores.get(self._fa_state, 0.3) + 0.08 * consol_score))
        passes = bool(self._fa_state in ("ReversalAfterConsolidation", "ContinuationImpulse") and score >= 0.84)
        return GateOutput(
            "formal_automata",
            score,
            passes,
            {
                "fa_state": self._fa_state,
                "consol": consol,
                "consol_type": consol_type,
                "consol_score": consol_score,
                "reversal_probability": rev_prob,
                "continuation_probability": cont_prob,
                "consolidation_detected": consolidation_detected,
                "reversal_detected": reversal_detected,
                "continuation_detected": continuation_detected,
            },
        )

    # ------------------------------------------------------------------
    # Gate 12 — Predictive Analytics (Full Probabilistic Fusion)
    # ------------------------------------------------------------------
    def gate_predictive_analytics(
        self,
        probs: dict[str, float],
        mcts: dict[str, Any],
        memory_sim: float = 0.0,
        latest_candle_confidence: float = 0.0,
        geometry_conflict: bool = False,
        chart_state: dict[str, Any] | None = None,
    ) -> GateOutput:
        """
        Predictive Analytics — fuse base probs + MCTS + latest-candle evidence + memory similarity.
        Weighted blend favors live/latest candle evidence and reduces memory reliance
        when geometry conflicts with historical recall.
        Gate passes if fused top-action probability >= 0.60.
        """
        normalized_probs, top_action, base_max, base_edge = _safe_probability_view(probs)
        mcts_hold_default = max(0.0, 1.0 - _clip01(mcts.get("buy_prob", 0.33), 0.33) - _clip01(mcts.get("sell_prob", 0.33), 0.33))
        mcts_probs, mcts_action, mcts_best, mcts_edge = _safe_probability_view(
            {
                "BUY": mcts.get("buy_prob", 0.33),
                "SELL": mcts.get("sell_prob", 0.33),
                "HOLD": mcts.get("hold_prob", mcts_hold_default),
            }
        )
        mem_factor = _clip01(memory_sim, 0.0)

        latest_factor = _clip01(latest_candle_confidence, 0.0)
        mem_w = 0.05 if geometry_conflict else 0.10
        state = chart_state or {}
        consolidation_bonus = _clip01(state.get("consolidation_score", 0.0), 0.0)
        structure_ready = float(1.0 if _truthy(state.get("structure_trade_ready", False)) else 0.0)
        path_clarity = _clip01(state.get("path_clarity", 0.0), 0.0)
        projected_direction = str(
            state.get("projected_box_direction", state.get("projection_bias_direction", state.get("direction", "HOLD"))) or "HOLD"
        ).strip().upper()
        direction_agreement = bool(top_action in _VALID_DIRECTIONS and mcts_action == top_action)
        structure_confirms = bool(
            top_action in _VALID_DIRECTIONS
            and projected_direction == top_action
            and structure_ready > 0.5
            and path_clarity >= 0.50
        )
        disagreement_penalty = 0.16 if (top_action in _VALID_DIRECTIONS and mcts_action in _VALID_DIRECTIONS and top_action != mcts_action) else 0.0
        conflict_penalty = 0.12 if geometry_conflict and not structure_confirms else 0.0
        fused = (
            0.34 * base_max
            + 0.24 * mcts_best
            + 0.16 * latest_factor
            + mem_w * mem_factor
            + 0.08 * consolidation_bonus
            + 0.08 * path_clarity
            + 0.06 * structure_ready
            - disagreement_penalty
            - conflict_penalty
        )
        score = float(np.clip(fused, 0.0, 1.0))
        threshold = 0.62 if structure_ready > 0.5 else 0.65
        pass_fail = bool(
            score >= threshold
            and top_action in _VALID_DIRECTIONS
            and base_edge >= 0.10
            and (direction_agreement or structure_confirms)
            and (latest_factor >= 0.20 or mem_factor >= 0.80)
            and not (geometry_conflict and not structure_confirms)
        )
        return GateOutput(
            "predictive_analytics", score, pass_fail,
            {
                "top_action": top_action,
                "base_max": base_max,
                "base_edge": base_edge,
                "mcts_action": mcts_action,
                "mcts_best": mcts_best,
                "mcts_edge": mcts_edge,
                "direction_agreement": direction_agreement,
                "structure_confirms": structure_confirms,
                "latest_factor": latest_factor,
                "memory_sim": mem_factor,
                "geometry_conflict": geometry_conflict,
                "consolidation_score": consolidation_bonus,
                "path_clarity": path_clarity,
                "structure_trade_ready": structure_ready,
                "projected_direction": projected_direction,
                "normalized_probs": normalized_probs,
                "mcts_probs": mcts_probs,
                "threshold": threshold,
            },
        )

    # ------------------------------------------------------------------
    # run_all - 13 gates
    # ------------------------------------------------------------------
    def run_all(
        self,
        probs: dict[str, float],
        q05: float,
        q95: float,
        momentum_bias: str,
        explanation: str,
        sub_signals: list[tuple[float, str]],
        module_logits: NDArray[np.float32],
        recent_feedback_count: int,
        queue_depth: int,
        gpu_mem_ok: bool,
        has_dashboard: bool,
        risk_ethical_ok: bool,
        # new SIGE-VLA 3.0 params
        chart_state: dict[str, Any] | None = None,
        prices: list[float] | None = None,
        direction_prob: float = 0.5,
        mcts: dict[str, Any] | None = None,
        memory_sim: float = 0.0,
        latest_candle_confidence: float = 0.0,
        geometry_conflict: bool = False,
    ) -> list[GateOutput]:
        # Bug fix: reset FSM state at the start of every new chart analysis so
        # state from a previous chart never bleeds into the current one.
        self.state = "Neutral"
        self._fa_state = "Idle"
        state = chart_state or {}
        outputs = [
            self.gate_probability_conformal(probs, q05, q95),
            self.gate_discrete_fsm(momentum_bias, explanation),
            self.gate_algorithmic_heap(sub_signals),
            self.gate_meta_stacking(module_logits),
            self.gate_context_retrieval(recent_feedback_count),
            self.gate_ops_stability(queue_depth, gpu_mem_ok),
            self.gate_ui_analytics(has_dashboard),
            self.gate_meta_constraints(risk_ethical_ok),
            self.gate_regression_error_estimation(prices or [], direction_prob),
            self.gate_knowledge_representation(state),
            self.candle_group_context_gate(state),
            self.gate_formal_automata(state),
            self.gate_predictive_analytics(
                probs,
                mcts or {},
                memory_sim,
                latest_candle_confidence=latest_candle_confidence,
                geometry_conflict=_truthy(geometry_conflict),
                chart_state=state,
            ),
        ]

        # Apply trainable router weights
        scores = _torch_tensor([g.score for g in outputs], dtype=torch.float32)
        with torch.inference_mode():
            routed = self._router(scores).numpy()
        for i, g in enumerate(outputs):
            raw_score = float(g.score)
            router_weight = float(np.clip(routed[i], 0.0, 1.0))
            g.detail["raw_score"] = raw_score
            g.detail["router_weight"] = router_weight
            g.score = float(np.clip(raw_score * router_weight, 0.0, 1.0))
            g.detail["routed_score"] = g.score

        return outputs

    def run_support_gates(
        self,
        *,
        chart_state: dict[str, Any] | None = None,
        market_state: dict[str, Any] | None = None,
        forecast: dict[str, Any] | None = None,
        transition_summary: dict[str, Any] | None = None,
        memory_summary: dict[str, Any] | None = None,
        ood_summary: dict[str, Any] | None = None,
        memory_similarity: float = 0.0,
        memory_label: str = "HOLD",
        latest_candle_confidence: float = 0.0,
        geometry_conflict: bool = False,
        reliability: float = 1.0,
        use_execution_permission: bool = True,
        use_macro_local_alignment: bool = True,
        use_opposition_strength: bool = True,
    ) -> list[GateOutput]:
        state = chart_state or {}
        market = market_state or {}
        forecast_view = forecast or {}
        macro_trend = str(state.get("macro_trend", market.get("macro_trend", "BULL")))
        local_phase = str(state.get("local_phase", market.get("local_phase", "continuation_base")))
        control_strength_delta = _finite_float(market.get("control_strength_delta", 0.0), 0.0)
        phase_risk = str(market.get("phase_risk", state.get("phase_risk", "chop_risk")))

        outputs = [
            self.continuation_strength_gate(macro_trend, local_phase, control_strength_delta),
            self.memory_regime_agreement_gate(macro_trend, _clip01(memory_similarity, 0.0), memory_label),
        ]
        if use_opposition_strength:
            outputs.append(self.opposition_strength_gate(macro_trend, local_phase, float(_truthy(geometry_conflict))))
        if use_macro_local_alignment:
            outputs.append(self.macro_local_alignment_gate(macro_trend, local_phase))
        if use_execution_permission:
            outputs.append(
                self.execution_permission_gate(
                    latest_candle_confidence=_clip01(latest_candle_confidence, 0.0),
                    phase_risk=phase_risk,
                    reliability=_clip01(reliability, 0.0),
                )
            )
        outputs.append(self.candle_group_context_gate(state))
        if forecast_view:
            outputs.extend(
                [
                    self.forecast_calibration_gate(
                        forecast_view,
                        latest_candle_confidence=_clip01(latest_candle_confidence, 0.0),
                        reliability=_clip01(reliability, 0.0),
                    ),
                    self.interval_efficiency_gate(forecast_view),
                    self.regime_stability_gate(
                        forecast_view,
                        market_state=market,
                        memory_summary=memory_summary,
                        geometry_conflict=geometry_conflict,
                        reliability=_clip01(reliability, 0.0),
                        ood_summary=ood_summary,
                    ),
                    self.transition_alignment_gate(
                        chart_state=state,
                        forecast=forecast_view,
                        transition_summary=transition_summary,
                    ),
                ]
            )
        return outputs

    def update_router_from_feedback(self, gate_scores: NDArray[np.float32], reward: float) -> None:
        """EMA update of router weights based on which gates predicted correctly."""
        scores = np.asarray(gate_scores, dtype=np.float32).reshape(-1)
        n_gates = int(self._router.layer.weight.shape[0])
        if scores.shape[0] < n_gates:
            scores = np.pad(scores, (0, n_gates - scores.shape[0]), mode="constant")
        elif scores.shape[0] > n_gates:
            scores = scores[:n_gates]
        self._feedback_history.append(scores * float(reward))
        if len(self._feedback_history) >= 10:
            batch = np.array(self._feedback_history[-10:], dtype=np.float32)
            avg = np.mean(batch, axis=0)
            grad_approx = _torch_tensor(avg, dtype=torch.float32)
            with torch.no_grad():
                w = self._router.layer.weight
                w.copy_(0.95 * w + 0.05 * _torch_diag(grad_approx))
