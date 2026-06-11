"""
PhoenixGuard SIGE-VLA 3.0 - 12 Skill Gates + Trainable Router
==============================================================
Gates 1-8  (original) + 4 new gates:
  9.  gate_regression_error_estimation  - poly R2 on extracted prices
  10. gate_knowledge_representation     - ontology predicate match
  11. gate_formal_automata              - FSM: Idle->Consol->Reversal->Impulse
  12. gate_predictive_analytics         - full probabilistic fusion

Router: LinearRouter(12->12) updated by EMA over session feedback history.
Consensus: confidence >= 0.82 AND gates_passing >= 9 AND memory_sim >= 0.87
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
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


def _finite_mean(values: Any, default: float = 0.0) -> float:
    try:
        arr = np.asarray(values, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return float(default)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return float(default)
    return float(np.mean(finite))


@dataclass
class GateOutput:
    name: str
    score: float
    pass_fail: bool
    detail: dict[str, Any]


class LinearRouter(nn.Module):
    """Trainable 12->12 gate importance router (EMA updated from feedback)."""
    def __init__(self, n_gates: int = 12):
        super().__init__()  # pyright: ignore[reportUnknownMemberType]
        self.layer = nn.Linear(n_gates, n_gates, bias=False)
        nn.init.eye_(self.layer.weight)  # pyright: ignore[reportUnknownMemberType]  # identity init = equal weight start

    def forward(self, scores: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.layer(scores))


class SkillGatedMoE(nn.Module):
    def __init__(self, n_features: int = 16, n_gates: int = 12):
        super().__init__()  # pyright: ignore[reportUnknownMemberType]
        self.router = nn.Sequential(
            nn.Linear(n_features, 32),
            nn.GELU(),
            nn.Linear(32, n_gates),
        )

    def route_weights(self, feat: NDArray[np.float32]) -> NDArray[np.float32]:
        x = torch.tensor(feat, dtype=torch.float32).unsqueeze(0)
        with torch.inference_mode():
            w = torch.softmax(self.router(x), dim=-1).squeeze(0).numpy()
        return w


class CurriculumGates:
    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self.state = "Neutral"
        self._fa_state: str = "Idle"
        self._router = LinearRouter(n_gates=12)
        self._feedback_history: list[NDArray[np.float32]] = []

    # ------------------------------------------------------------------
    # Gates 1-8 (original, enhanced)
    # ------------------------------------------------------------------

    def gate_probability_conformal(self, probs: dict[str, float], q05: float, q95: float) -> GateOutput:
        lower = _finite_float(q05, 0.0)
        upper = _finite_float(q95, 0.0)
        interval = abs(upper - lower)
        confidence = max((_clip01(value, 0.0) for value in probs.values()), default=0.0)
        calibrated = max(0.0, min(1.0, confidence * (1.0 - min(interval / 2.0, 0.8))))
        return GateOutput("prob_stats", calibrated, calibrated >= 0.5, {"interval": interval, "calibrated": calibrated})

    def gate_discrete_fsm(self, momentum_bias: str, explanation: str) -> GateOutput:
        idx = FSM_STATES.index(self.state)
        # Lower indices are more bullish and higher indices are more bearish.
        step = -1 if momentum_bias == "bullish" else (1 if momentum_bias == "bearish" else 0)
        if re.search(r"breakout|impulse|trend up", explanation.lower()):
            step -= 1
        if re.search(r"rejection|trend down|sell pressure", explanation.lower()):
            step += 1
        idx = int(np.clip(idx + step, 0, len(FSM_STATES) - 1))
        self.state = FSM_STATES[idx]
        strength = 1.0 - abs(idx - 3) / 3.0
        return GateOutput("discrete_fsm", float(max(0.0, min(1.0, strength))), True, {"state": self.state})

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
        return GateOutput("algo_heap", max(0.0, min(1.0, score)), score > 0.4, {"top": top})

    def gate_meta_stacking(self, module_logits: NDArray[np.float32]) -> GateOutput:
        score = _clip01(_finite_mean(module_logits, 0.0), 0.0)
        return GateOutput("ml_stacking", score, score > 0.45, {"mean": score})

    def gate_context_retrieval(self, recent_feedback_count: int) -> GateOutput:
        feedback_count = max(0.0, _finite_float(recent_feedback_count, 0.0))
        score = float(np.tanh(feedback_count / 50.0))
        return GateOutput("db_context", score, True, {"feedback_count": recent_feedback_count})

    def gate_ops_stability(self, queue_depth: int, gpu_mem_ok: bool) -> GateOutput:
        queue = max(0.0, _finite_float(queue_depth, 0.0))
        score = 1.0 - (0.25 if queue > 4 else 0.0) - (0.25 if not gpu_mem_ok else 0.0)
        return GateOutput("ops_stability", score, score >= 0.5, {"queue_depth": queue_depth})

    def gate_ui_analytics(self, has_dashboard: bool) -> GateOutput:
        score = 1.0 if has_dashboard else 0.6
        return GateOutput("ui_analytics", score, True, {"dashboard": has_dashboard})

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
        local_with_trend = local_phase.lower() in _WITH_TREND_PHASES
        magnitude = _clip01(abs(_finite_float(control_strength_delta, 0.0)), 0.0)
        base = 0.35 if local_with_trend else 0.10
        strength = float(np.clip(base + 0.65 * magnitude, 0.0, 1.0))
        pass_fail = bool(local_with_trend and strength >= 0.58)
        return GateOutput("continuation_strength", strength, pass_fail, {"macro_trend": macro_trend, "local_phase": local_phase, "control_strength_delta": control_strength_delta})

    def opposition_strength_gate(self, macro_trend: str, local_phase: str, geometry_conflict: float) -> GateOutput:
        """
        Scores how strong the opposition to the current trend is, using local/macro contradiction and geometry conflict.
        """
        local_against_trend = local_phase.lower() in _COUNTER_TREND_PHASES
        conflict_score = _clip01(geometry_conflict, 0.0)
        phase_pressure = 0.65 if local_against_trend else 0.15
        opposition = float(np.clip(0.55 * phase_pressure + 0.45 * conflict_score, 0.0, 1.0))
        pass_fail = bool(local_against_trend and opposition >= 0.55)
        return GateOutput("opposition_strength", opposition, pass_fail, {"macro_trend": macro_trend, "local_phase": local_phase, "geometry_conflict": geometry_conflict})

    def macro_local_alignment_gate(self, macro_trend: str, local_phase: str) -> GateOutput:
        """
        Scores the alignment between macro and local structure.
        """
        phase = local_phase.lower()
        if phase == "with_trend_push":
            score = 1.0
        elif phase in {"with_trend_pause", "continuation_base"}:
            score = 0.78
        elif phase == "counter_trend_pullback":
            score = 0.25
        else:
            score = 0.0
        aligned = phase in _WITH_TREND_PHASES
        return GateOutput("macro_local_alignment", score, aligned, {"macro_trend": macro_trend, "local_phase": local_phase})

    def memory_regime_agreement_gate(self, macro_trend: str, memory_similarity: float, memory_label: str) -> GateOutput:
        """
        Scores agreement between macro trend and memory label, weighted by similarity.
        """
        mapped_label = "BUY" if macro_trend.upper() == "BULL" else ("SELL" if macro_trend.upper() == "BEAR" else "HOLD")
        normalized_label = memory_label.upper()
        similarity = _clip01(memory_similarity, 0.0)
        if similarity <= 0.0 or normalized_label not in {"BUY", "SELL"}:
            return GateOutput(
                "memory_regime_agreement",
                0.0,
                True,
                {"macro_trend": macro_trend, "memory_label": memory_label, "memory_similarity": similarity, "reason": "no_memory_alignment_signal"},
            )
        agreement = mapped_label in {"BUY", "SELL"} and normalized_label == mapped_label
        score = similarity if agreement else 0.0
        return GateOutput("memory_regime_agreement", score, agreement, {"macro_trend": macro_trend, "memory_label": memory_label, "memory_similarity": memory_similarity})

    def execution_permission_gate(self, latest_candle_confidence: float, phase_risk: str, reliability: float = 1.0) -> GateOutput:
        """
        Determines if execution is permitted, based on confidence, phase risk, and reliability.
        """
        normalized_risk = phase_risk.lower()
        if normalized_risk in {"exhaustion_risk", "managed_counter_trend", "contradiction"}:
            risk_multiplier = 0.60
        elif normalized_risk == "chop_risk":
            risk_multiplier = 0.78
        else:
            risk_multiplier = 1.0
        score = _clip01(latest_candle_confidence, 0.0) * _clip01(reliability, 0.0) * risk_multiplier
        threshold = 0.46 if normalized_risk == "breakout_risk" else 0.52
        pass_fail = bool(score >= threshold)
        return GateOutput("execution_permission", score, pass_fail, {"latest_candle_confidence": latest_candle_confidence, "phase_risk": phase_risk, "reliability": reliability})

    def forecast_calibration_gate(
        self,
        forecast: dict[str, Any],
        latest_candle_confidence: float,
        reliability: float,
    ) -> GateOutput:
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
        pass_fail = bool(ordered and score >= 0.56 and interval <= threshold * 1.18)
        return GateOutput(
            "forecast_calibration",
            score,
            pass_fail,
            {
                "ordered": ordered,
                "interval": interval,
                "hold_threshold_used": threshold,
                "path_confidence": path_confidence,
                "execution_readiness": execution_readiness,
                "contradiction_score": contradiction_score,
            },
        )

    def interval_efficiency_gate(self, forecast: dict[str, Any]) -> GateOutput:
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
        pass_fail = bool(ratio <= 1.10 and score >= 0.52)
        return GateOutput(
            "interval_efficiency",
            score,
            pass_fail,
            {
                "interval": interval,
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
        entry_type = str(chart_state.get("entry_type", "")).lower().replace(" ", "_")
        structure_setup = str(forecast.get("structure_setup", chart_state.get("structure_setup", "none"))).lower()
        projected_confidence = _clip01(forecast.get("projected_box_confidence", 0.0), 0.0)
        path_confidence = _clip01(forecast.get("path_confidence", 0.0), 0.0)
        structure_ready = _clip01(forecast.get("structure_trade_ready", chart_state.get("structure_trade_ready", 0.0)), 0.0)

        if entry_type == "continuation" or structure_setup in {"consolidation_breakout", "impulse_chain"}:
            favorable = continue_prob
            hazard = max(fakeout_prob, pullback_prob, 0.85 * reversal_prob)
        elif entry_type == "reversal" or structure_setup == "reversal_release":
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
        pass_fail = bool(favorable >= hazard and score >= 0.54)
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
        try:
            coeffs = np.polyfit(x, y, 2)
            y_hat = np.poly1d(coeffs)(x)
            ss_res = float(np.sum((y - y_hat) ** 2))
            ss_tot = float(np.sum((y - y.mean()) ** 2)) + 1e-10
            r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot))
        except Exception:
            r2 = 0.0
        score = 0.6 * r2 + 0.4 * direction_strength
        return GateOutput("regression_est", score, r2 >= 0.55 and direction_strength >= 0.55, {"r2": r2})

    # ------------------------------------------------------------------
    # Gate 10 — Knowledge Representation (Ontology)
    # ------------------------------------------------------------------
    def gate_knowledge_representation(self, chart_state: dict[str, Any]) -> GateOutput:
        """
        Knowledge Representation — check if detected pattern is in 808FX ontology.
        Confirming entry_type + reversal_signal = valid style predicate → pass.
        """
        entry_type = str(chart_state.get("entry_type", "")).lower().replace(" ", "_")
        rev_signal = str(chart_state.get("reversal_signal", "")).lower().replace(" ", "_")
        cont_signal = str(chart_state.get("continuation_signal", "")).lower().replace(" ", "_")
        direction = str(chart_state.get("direction", "HOLD")).upper()

        entry_valid = entry_type in _VALID_ENTRY_TYPES
        matched_buy = rev_signal in _ONTOLOGY_BUY or cont_signal in _ONTOLOGY_BUY
        matched_sell = rev_signal in _ONTOLOGY_SELL or cont_signal in _ONTOLOGY_SELL
        # Directional coherence check
        coherent = entry_valid and ((direction == "BUY" and matched_buy) or (direction == "SELL" and matched_sell))
        score = 0.92 if coherent else (0.58 if entry_valid and (matched_buy or matched_sell) else (0.35 if entry_valid else 0.2))
        return GateOutput(
            "knowledge_rep", score, coherent,
            {
                "entry_type": entry_type,
                "reversal_signal": rev_signal,
                "continuation_signal": cont_signal,
                "coherent": coherent,
                "matched_buy": matched_buy,
                "matched_sell": matched_sell,
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
        consol_type = str(chart_state.get("consolidation_type", "none")).strip().lower()
        consol_score = _clip01(chart_state.get("consolidation_score", 0.0), 0.0)
        rev_sig = str(chart_state.get("reversal_signal", "")).strip().lower()
        cont_sig = str(chart_state.get("continuation_signal", "")).strip().lower()
        cont_prob = _clip01(chart_state.get("continuation_probability", 0.0), 0.0)
        rev_prob = _clip01(chart_state.get("reversal_probability", 0.0), 0.0)

        consolidation_detected = bool(
            (consol >= 4)
            or (consol >= 3 and consol_score >= 0.55)
        )
        reversal_detected = bool(
            (rev_sig and rev_sig in {"engulfing", "wick_rejection", "reversal"})
            or (rev_sig and rev_sig != "none" and rev_prob >= 0.20)
        )
        continuation_detected = bool(
            (cont_sig and cont_sig in {"breakout", "impulse_pause", "reversal_release"})
            or (cont_sig and cont_sig != "none" and cont_prob >= max(0.35, rev_prob))
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
        passes = self._fa_state in ("ReversalAfterConsolidation", "ContinuationImpulse")
        return GateOutput(
            "formal_automata",
            score,
            passes,
            {"fa_state": self._fa_state, "consol": consol, "consol_type": consol_type, "consol_score": consol_score},
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
        base_max = max((_clip01(value, 0.0) for value in probs.values()), default=0.33)
        mcts_buy = _clip01(mcts.get("buy_prob", 0.33), 0.33)
        mcts_sell = _clip01(mcts.get("sell_prob", 0.33), 0.33)
        mcts_best = max(mcts_buy, mcts_sell, 1.0 - mcts_buy - mcts_sell)
        mem_factor = _clip01(memory_sim, 0.0)

        latest_factor = _clip01(latest_candle_confidence, 0.0)
        mem_w = 0.05 if geometry_conflict else 0.10
        state = chart_state or {}
        consolidation_bonus = _clip01(state.get("consolidation_score", 0.0), 0.0)
        structure_ready = float(1.0 if state.get("structure_trade_ready", False) else 0.0)
        path_clarity = _clip01(state.get("path_clarity", 0.0), 0.0)
        fused = (
            0.36 * base_max
            + 0.30 * mcts_best
            + 0.14 * latest_factor
            + mem_w * mem_factor
            + 0.10 * consolidation_bonus
            + 0.05 * path_clarity
            + 0.05 * structure_ready
        )
        score = float(np.clip(fused, 0.0, 1.0))
        return GateOutput(
            "predictive_analytics", score, score >= (0.56 if structure_ready > 0.5 else 0.60),
            {
                "base_max": base_max,
                "mcts_best": mcts_best,
                "latest_factor": latest_factor,
                "memory_sim": mem_factor,
                "geometry_conflict": geometry_conflict,
                "consolidation_score": consolidation_bonus,
                "path_clarity": path_clarity,
                "structure_trade_ready": structure_ready,
            },
        )

    # ------------------------------------------------------------------
    # run_all — 12 gates
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
            self.gate_formal_automata(state),
            self.gate_predictive_analytics(
                probs,
                mcts or {},
                memory_sim,
                latest_candle_confidence=latest_candle_confidence,
                geometry_conflict=geometry_conflict,
                chart_state=state,
            ),
        ]

        # Apply trainable router weights
        scores = torch.tensor([g.score for g in outputs], dtype=torch.float32)
        with torch.inference_mode():
            routed = self._router(scores).numpy()
        for i, g in enumerate(outputs):
            g.score = float(np.clip(g.score * routed[i], 0.0, 1.0))

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
            outputs.append(self.opposition_strength_gate(macro_trend, local_phase, float(bool(geometry_conflict))))
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
        self._feedback_history.append(gate_scores * reward)
        if len(self._feedback_history) >= 10:
            batch = np.array(self._feedback_history[-10:], dtype=np.float32)
            avg = np.mean(batch, axis=0)
            grad_approx = torch.tensor(avg, dtype=torch.float32)
            with torch.no_grad():
                w = self._router.layer.weight
                w.copy_(0.95 * w + 0.05 * torch.diag(grad_approx))
