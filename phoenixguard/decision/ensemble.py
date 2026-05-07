"""
PhoenixGuard SIGE-VLA 3.0 - Ensemble Decision Engine
=====================================================
Strictly typed ensemble router with:
  - reliability-aware Bayesian calibration
  - ambiguity-aware memory weighting
  - latest-candle fail-safe gating
  - canonical transition-summary consumption
  - approximate SHAP-style gate attribution
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence, TypedDict

import numpy as np
from numpy.typing import NDArray

from phoenixguard.core.utils import clamp


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


def _finite_mean(values: Sequence[Any] | NDArray[np.float64], default: float = 0.0) -> float:
    try:
        arr = np.asarray(values, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return float(default)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return float(default)
    return float(np.mean(finite))


class GateLike(TypedDict, total=False):
    name: str
    score: float
    pass_fail: bool




class TransitionSummary(TypedDict, total=False):
    continue_prob: float
    pullback_prob: float
    reversal_attempt_prob: float
    fakeout_prob: float


class EnsembleDecisionEngine:
    def __init__(
        self,
        consensus_threshold: float = 0.82,
        max_interval_pct: float = 0.40,
        risk_min_pct: float = 0.5,
        risk_max_pct: float = 2.0,
        gates_pass_minimum: int = 9,
        memory_veto_threshold: float = 0.87,
    ) -> None:
        self.consensus_threshold = float(consensus_threshold)
        self.max_interval_pct = float(max_interval_pct)
        self.risk_min_pct = float(risk_min_pct)
        self.risk_max_pct = float(risk_max_pct)
        self.gates_pass_minimum = int(gates_pass_minimum)
        self.memory_veto_threshold = float(memory_veto_threshold)

    @staticmethod
    def _safe_probs(probs: Mapping[str, float]) -> NDArray[np.float64]:
        vec = np.array(
            [
                _clip01(probs.get('BUY', 0.0), 0.0),
                _clip01(probs.get('SELL', 0.0), 0.0),
                _clip01(probs.get('HOLD', 0.0), 0.0),
            ],
            dtype=np.float64,
        )
        total = float(vec.sum())
        if not np.isfinite(total) or total <= 0.0:
            vec = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=np.float64)
        vec = np.clip(vec, 1e-6, 1.0)
        return vec / max(float(vec.sum()), 1e-12)

    @staticmethod
    def _softmax(values: NDArray[np.float64]) -> NDArray[np.float64]:
        exp_values = np.exp(values - np.max(values))
        denom = max(float(exp_values.sum()), 1e-12)
        return exp_values / denom

    def _memory_weight(self, memory_bank_similarity: float, memory_summary: dict[str, Any] | None) -> float:
        similarity = _clip01(memory_bank_similarity, 0.0)
        if similarity <= 0.0:
            return 0.0
        if memory_summary is None:
            return similarity
        ambiguity = _clip01(memory_summary.get('ambiguity', 0.0), 0.0)
        label_entropy = _clip01(memory_summary.get('label_entropy', 0.0), 0.0)
        consensus_ratio = _clip01(memory_summary.get('consensus_ratio', 0.0), 0.0)
        mixed_labels = bool(memory_summary.get('mixed_labels', False))
        weight = similarity * (1.0 - ambiguity) * consensus_ratio
        weight *= (1.0 - 0.15 * label_entropy)
        if mixed_labels:
            weight *= 0.90
        return float(np.clip(weight, 0.0, 1.0))

    def _bayesian_average(
        self,
        probs: Mapping[str, float],
        gate_scores: NDArray[np.float64],
        memory_bank_similarity: float,
        memory_summary: dict[str, Any] | None,
        transition_summary: TransitionSummary | None,
        latest_candle_confidence: float,
        module_reliability: Mapping[str, float] | None,
        support_gate_outputs: Sequence[Any] | None = None,
    ) -> dict[str, float]:
        alpha = self._safe_probs(probs)
        reliability = module_reliability or {}
        cv_quality = _clip01(reliability.get('cv_quality', 0.5), 0.5)
        structure_consistency = _clip01(
            reliability.get('structure_consistency', reliability.get('cv_quality', 0.5)),
            cv_quality,
        )
        sequence_clarity = _clip01(reliability.get('sequence_clarity', structure_consistency), structure_consistency)
        consolidation_quality = _clip01(reliability.get('consolidation_quality', 0.0), 0.0)
        structure_active = structure_consistency >= 0.10
        gate_factor = _clip01(_finite_mean(gate_scores, 0.5), 0.5)
        memory_weight = self._memory_weight(memory_bank_similarity, memory_summary)
        support_gate_scores = {
            str(getattr(gate, 'name', 'gate')): _clip01(getattr(gate, 'score', 0.0), 0.0)
            for gate in (support_gate_outputs or [])
        }
        continuation_strength = support_gate_scores.get('continuation_strength', 0.0)
        memory_regime_agreement = support_gate_scores.get('memory_regime_agreement', 0.0)
        macro_local_alignment = support_gate_scores.get('macro_local_alignment', 0.0)
        opposition_strength = support_gate_scores.get('opposition_strength', 0.0)
        execution_permission = support_gate_scores.get('execution_permission', 1.0)
        forecast_calibration = support_gate_scores.get('forecast_calibration', 0.0)
        interval_efficiency = support_gate_scores.get('interval_efficiency', 0.0)
        regime_stability = support_gate_scores.get('regime_stability', 0.0)
        transition_alignment = support_gate_scores.get('transition_alignment', 0.0)

        direction_logits = np.log(alpha)
        buy_idx, sell_idx, hold_idx = 0, 1, 2
        dominant_idx = buy_idx if alpha[buy_idx] >= alpha[sell_idx] else sell_idx
        opposite_idx = sell_idx if dominant_idx == buy_idx else buy_idx

        direction_logits[buy_idx] += 0.55 * gate_factor * cv_quality
        direction_logits[sell_idx] += 0.55 * gate_factor * cv_quality
        direction_logits[hold_idx] += 0.30 * (1.0 - cv_quality)
        direction_logits[hold_idx] += 0.20 * (1.0 - structure_consistency)
        direction_logits[dominant_idx] += 0.12 * macro_local_alignment + 0.10 * continuation_strength + 0.08 * memory_regime_agreement
        direction_logits[dominant_idx] += 0.10 * forecast_calibration + 0.08 * interval_efficiency + 0.08 * transition_alignment + 0.06 * regime_stability
        direction_logits[hold_idx] += 0.18 * opposition_strength + 0.24 * max(0.0, 1.0 - execution_permission)
        direction_logits[hold_idx] += 0.10 * max(0.0, 1.0 - forecast_calibration)
        direction_logits[hold_idx] += 0.10 * max(0.0, 1.0 - interval_efficiency)
        direction_logits[hold_idx] += 0.12 * max(0.0, 1.0 - regime_stability)
        direction_logits[hold_idx] += 0.08 * max(0.0, 1.0 - transition_alignment)
        if execution_permission < 0.5:
            damp = 0.08 * (0.5 - execution_permission) / 0.5
            direction_logits[buy_idx] -= damp
            direction_logits[sell_idx] -= damp
        direction_logits[dominant_idx] += 0.14 * sequence_clarity + 0.12 * consolidation_quality
        direction_logits[hold_idx] += 0.06 * max(0.0, 1.0 - max(sequence_clarity, consolidation_quality))
        if not structure_active:
            direction_logits[hold_idx] += 0.45
            direction_logits[buy_idx] -= 0.10
            direction_logits[sell_idx] -= 0.10

        if transition_summary is not None:
            cont = _clip01(transition_summary.get('continue_prob', 0.25), 0.25)
            pull = _clip01(transition_summary.get('pullback_prob', 0.25), 0.25)
            rev = _clip01(transition_summary.get('reversal_attempt_prob', 0.25), 0.25)
            fake = _clip01(transition_summary.get('fakeout_prob', 0.25), 0.25)
            directional_lift = (0.28 * cont + 0.12 * rev) * (0.72 + 0.28 * structure_consistency)
            hold_lift = 0.22 * fake + 0.16 * pull
            direction_logits[dominant_idx] += directional_lift
            direction_logits[opposite_idx] += max(0.0, 0.05 * rev - 0.10 * fake)
            direction_logits[hold_idx] += hold_lift
            if cont >= max(pull, fake):
                direction_logits[hold_idx] -= 0.08 * cont

        if memory_weight > 0.0 and memory_summary is not None:
            dominant = str(memory_summary.get('dominant_label', 'HOLD')).upper()
            if dominant == 'BUY':
                direction_logits[buy_idx] += 0.40 * memory_weight
            elif dominant == 'SELL':
                direction_logits[sell_idx] += 0.40 * memory_weight
            else:
                direction_logits[hold_idx] += 0.20 * memory_weight
            if bool(memory_summary.get('mixed_labels', False)):
                direction_logits[hold_idx] += 0.30 * (1.0 - memory_weight)

        latest_conf = _clip01(latest_candle_confidence, 0.0)
        latest_relief = _clip01(structure_consistency, 0.0)
        if latest_conf < 0.35:
            direction_logits[hold_idx] += 0.35 * (0.35 - latest_conf) / 0.35 * (1.0 - 0.55 * latest_relief)
        if latest_conf < 0.20:
            severe_weight = max(0.0, 1.0 - 1.60 * latest_relief)
            direction_logits[hold_idx] += 0.35 * severe_weight
            direction_logits[buy_idx] -= 0.15 * severe_weight
            direction_logits[sell_idx] -= 0.15 * severe_weight

        calibrated = self._softmax(direction_logits)
        return {
            'BUY': float(calibrated[buy_idx]),
            'SELL': float(calibrated[sell_idx]),
            'HOLD': float(calibrated[hold_idx]),
        }

    def _shap_contributions(
        self,
        gate_outputs: Sequence[Any],
        rl_probs: Mapping[str, float],
        module_reliability: Mapping[str, float] | None = None,
    ) -> dict[str, float]:
        rel = module_reliability or {}
        cv_rel = _clip01(rel.get('cv_quality', 0.5), 0.5)
        structure_rel = _clip01(rel.get('structure_consistency', cv_rel), cv_rel)
        mem_rel = _clip01(1.0 - _clip01(rel.get('memory_novelty', 0.5), 0.5), 0.5)
        rel_factor = 0.55 * cv_rel + 0.30 * structure_rel + 0.15 * mem_rel

        base_logits = np.log(self._safe_probs(rl_probs))
        pred_idx = int(np.argmax(base_logits))
        lifts = np.array([max(0.0, _clip01(getattr(g, 'score', 0.0), 0.0) - 0.5) * rel_factor for g in gate_outputs], dtype=np.float64)
        full_logits = base_logits.copy()
        full_logits[pred_idx] += float(np.sum(lifts))
        base_prob = float(self._softmax(full_logits)[pred_idx])

        contributions: dict[str, float] = {}
        for index, gate in enumerate(gate_outputs):
            loo_logits = full_logits.copy()
            loo_logits[pred_idx] -= lifts[index]
            loo_prob = float(self._softmax(loo_logits)[pred_idx])
            gate_name = str(getattr(gate, 'name', f'gate_{index}'))
            contributions[gate_name] = float(abs(base_prob - loo_prob))
        return contributions

    def infer(
        self,
        rl_probs: Mapping[str, float],
        forecast: Mapping[str, Any],
        gate_outputs: Sequence[Any],
        memory_bank_similarity: float = 0.0,
        force_hold: bool = False,
        module_reliability: Mapping[str, float] | None = None,
        memory_summary: dict[str, Any] | None = None,
        latest_candle_confidence: float = 0.0,
        transition_summary: TransitionSummary | None = None,
        support_gate_outputs: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        gate_scores = np.array([_clip01(getattr(g, 'score', 0.0), 0.0) for g in gate_outputs], dtype=np.float64)
        memory_similarity = _clip01(memory_bank_similarity, 0.0)
        latest_confidence_input = _clip01(latest_candle_confidence, 0.0)
        calibrated_probs = self._bayesian_average(
            rl_probs,
            gate_scores,
            memory_bank_similarity=memory_similarity,
            memory_summary=memory_summary,
            transition_summary=transition_summary,
            latest_candle_confidence=latest_confidence_input,
            module_reliability=module_reliability,
            support_gate_outputs=support_gate_outputs,
        )
        support_gates = list(support_gate_outputs or [])
        support_gate_scores = {
            str(getattr(g, 'name', f'support_gate_{i}')): _clip01(getattr(g, 'score', 0.0), 0.0)
            for i, g in enumerate(support_gates)
        }
        support_gate_pass = {
            str(getattr(g, 'name', f'support_gate_{i}')): bool(getattr(g, 'pass_fail', False))
            for i, g in enumerate(support_gates)
        }
        execution_guard_ok = support_gate_pass.get('execution_permission', True)
        opposition_alert = bool(
            support_gate_pass.get('opposition_strength', False)
            and float(support_gate_scores.get('opposition_strength', 0.0)) >= 0.55
        )
        required_support_gates = [
            gate
            for gate in support_gates
            if str(getattr(gate, 'name', '')) != 'opposition_strength'
            and bool(getattr(gate, 'detail', {}).get('required', True))
        ]
        support_gates_ok = all(bool(getattr(g, 'pass_fail', False)) for g in required_support_gates) if required_support_gates else True
        hard_support_ok = bool(support_gates_ok and execution_guard_ok and not opposition_alert)

        action = max(calibrated_probs, key=lambda key: float(calibrated_probs[key]))
        confidence = _clip01(calibrated_probs[action], 0.0)
        shap = self._shap_contributions(gate_outputs, rl_probs, module_reliability=module_reliability)
        gates_passing = int(sum(1 for gate in gate_outputs if bool(getattr(gate, 'pass_fail', False))))
        interval = abs(_finite_float(forecast.get('q95', 0.0), 0.0) - _finite_float(forecast.get('q05', 0.0), 0.0))

        confidence_ok = confidence >= self.consensus_threshold
        gates_ok = gates_passing >= self.gates_pass_minimum
        memory_weight = self._memory_weight(memory_similarity, memory_summary)
        interval_ok = interval <= self.max_interval_pct

        ambiguity = _clip01((memory_summary or {}).get('ambiguity', 0.0), 0.0)
        label_entropy = _clip01((memory_summary or {}).get('label_entropy', 0.0), 0.0)
        latest_penalty = _clip01(1.0 - latest_confidence_input, 0.0)
        execution_readiness = _clip01(forecast.get('execution_readiness', 0.0), 0.0)
        active_consolidation = _clip01(forecast.get('active_consolidation', 0.0), 0.0) >= 0.5
        structure_trade_ready = _clip01(forecast.get('structure_trade_ready', 0.0), 0.0) >= 0.5
        structure_setup = str(forecast.get('structure_setup', 'none')).lower()
        projected_box_direction = str(forecast.get('projected_box_direction', '')).upper()
        projected_box_confidence = _clip01(forecast.get('projected_box_confidence', 0.0), 0.0)
        projection_bias_confidence = _clip01(forecast.get('projection_bias_confidence', projected_box_confidence), projected_box_confidence)
        projection_dominance = _clip01(forecast.get('projection_dominance', 0.0), 0.0)
        forecast_q50 = _finite_float(forecast.get('q50', 0.0), 0.0)
        reversal_projection_rescue = (
            structure_trade_ready
            and structure_setup == 'reversal_release'
            and projected_box_direction in {'BUY', 'SELL'}
            and projected_box_confidence >= 0.52
            and projection_bias_confidence >= 0.44
            and execution_readiness >= 0.48
            and (
                (projected_box_direction == 'BUY' and forecast_q50 > 0.0)
                or (projected_box_direction == 'SELL' and forecast_q50 < 0.0)
            )
        )
        directional_action = (
            projected_box_direction
            if reversal_projection_rescue
            else ('BUY' if calibrated_probs['BUY'] >= calibrated_probs['SELL'] else 'SELL')
        )
        projection_support = (
            structure_trade_ready
            and structure_setup in {'consolidation_breakout', 'impulse_chain', 'reversal_release'}
            and projected_box_direction in {'BUY', 'SELL'}
            and projected_box_direction == directional_action
            and projected_box_confidence >= (0.52 if structure_setup == 'reversal_release' else 0.58)
            and projection_dominance >= (0.0 if structure_setup == 'reversal_release' else 0.0)
            and (active_consolidation or execution_readiness >= (0.48 if structure_setup == 'reversal_release' else 0.58))
        )
        memory_threshold_used = self.memory_veto_threshold
        if memory_similarity == 0.0:
            memory_ok = True
        elif memory_summary is None:
            memory_ok = memory_weight >= self.memory_veto_threshold
        else:
            dominant_label = str(memory_summary.get('dominant_label', 'HOLD')).upper()
            mixed_labels = bool(memory_summary.get('mixed_labels', False))
            supportive_target = projected_box_direction if projection_support else directional_action
            supportive_memory = (
                supportive_target in {'BUY', 'SELL'}
                and dominant_label == supportive_target
                and not mixed_labels
                and ambiguity <= 0.18
                and label_entropy <= 0.25
            )
            if supportive_memory:
                memory_threshold_used = max(0.74, self.memory_veto_threshold - 0.12)
            memory_ok = memory_weight >= memory_threshold_used
        weak_memory_neutral = bool(memory_similarity < 0.35 and ambiguity <= 0.20 and label_entropy <= 0.25)
        projection_memory_ok = bool(memory_ok or weak_memory_neutral)
        structure_consistency_value = _clip01(
            (module_reliability or {}).get(
                'structure_consistency',
                (module_reliability or {}).get('cv_quality', 0.0),
            ),
            0.0,
        )
        projection_rescue = (
            reversal_projection_rescue
            or (
                projected_box_direction in {'BUY', 'SELL'}
                and projected_box_confidence >= 0.54
                and projection_bias_confidence >= 0.56
                and execution_readiness >= 0.48
                and structure_trade_ready
            )
        )
        forced_uncertain = (
            ambiguity >= 0.45
            or structure_consistency_value < 0.10
            or (opposition_alert and structure_consistency_value < 0.40 and not projection_rescue)
            # Uses the sanitized latest-candle signal so sparse callers never
            # feed NaNs or None into the uncertainty rule.
            or (
                latest_confidence_input < 0.20
                and (not projection_rescue)
                and structure_consistency_value < 0.28
            )
        )
        if not execution_guard_ok and not projection_rescue:
            force_hold = True
        confidence_target = self.consensus_threshold
        gates_target = self.gates_pass_minimum
        interval_target = self.max_interval_pct
        if active_consolidation and execution_readiness >= 0.58:
            confidence_target = max(0.62, self.consensus_threshold - 0.14 * execution_readiness)
            gates_target = max(7, self.gates_pass_minimum - 2)
            interval_target = self.max_interval_pct * 1.12
        if projection_support and execution_readiness >= 0.62:
            confidence_target = max(0.56, confidence_target - 0.05)
            gates_target = max(6, gates_target - 1)
            interval_target *= 1.04
        confidence_ok = confidence >= confidence_target
        gates_ok = gates_passing >= gates_target
        interval_ok = interval <= interval_target
        consensus_ok = confidence_ok and gates_ok and memory_ok and interval_ok and hard_support_ok and (not forced_uncertain)
        directional_confidence = float(max(calibrated_probs['BUY'], calibrated_probs['SELL']))
        projection_bias_ready = (
            not force_hold
            and projection_support
            and projection_memory_ok
            and hard_support_ok
            and interval_ok
            and gates_passing >= max(4, gates_target - 3)
            and directional_confidence >= max(0.48, confidence_target - 0.12)
            and structure_consistency_value >= 0.18
            and (latest_confidence_input >= 0.10 or projection_rescue)
            and not forced_uncertain
        )
        projection_watch_ready = (
            projection_support
            and projection_memory_ok
            and gates_passing >= max(4, gates_target - 3)
            and directional_confidence >= max(0.42, confidence_target - 0.20)
            and structure_consistency_value >= 0.18
            and (latest_confidence_input >= 0.10 or projection_rescue)
            and not forced_uncertain
        )
        if (
            not force_hold
            and projection_support
            and projection_memory_ok
            and interval_ok
            and hard_support_ok
            and gates_passing >= max(6, gates_target - 1)
            and directional_confidence >= max(0.52, confidence_target - 0.06)
            and not forced_uncertain
        ):
            consensus_ok = True
            action = projected_box_direction
            confidence = max(directional_confidence, projection_bias_confidence)

        if projection_bias_ready and projected_box_direction in {'BUY', 'SELL'} and action != projected_box_direction:
            action = projected_box_direction
            confidence = max(_clip01(calibrated_probs.get(projected_box_direction, 0.0), 0.0), projection_bias_confidence)
        elif projection_bias_ready and action == 'HOLD':
            action = projected_box_direction
            confidence = max(directional_confidence, projection_bias_confidence)
        elif projection_watch_ready and projected_box_direction in {'BUY', 'SELL'}:
            action = projected_box_direction
            confidence = max(_clip01(calibrated_probs.get(projected_box_direction, 0.0), 0.0), projection_bias_confidence)

        if (force_hold and not projection_watch_ready) or ((not consensus_ok) and (not projection_bias_ready) and (not projection_watch_ready)):
            action = 'HOLD'
            confidence = float(calibrated_probs['HOLD'])

        trade_bias = action
        if (
            trade_bias == 'HOLD'
            and projected_box_direction in {'BUY', 'SELL'}
            and projected_box_confidence >= 0.52
            and memory_ok
            and interval_ok
        ):
            trade_bias = projected_box_direction

        expected_move = _finite_float(forecast.get('q50', 0.0), 0.0)
        move_strength = min(abs(expected_move) / 1.0, 1.0)
        confidence_scale = float(np.clip(confidence * (1.0 - 0.35 * ambiguity) * (1.0 - 0.20 * latest_penalty), 0.0, 1.0))
        position_pct = self.risk_min_pct + (self.risk_max_pct - self.risk_min_pct) * confidence_scale * move_strength
        position_pct = float(clamp(position_pct, self.risk_min_pct, self.risk_max_pct))

        return {
            'action': action,
            'trade_bias': trade_bias,
            'execution_permission': 'EXECUTE' if (consensus_ok and action != 'HOLD') else 'WAIT_FOR_CONFIRMATION',
            'decision_state': 'CONFIRMED' if consensus_ok else ('PROJECTED' if ((projection_bias_ready or projection_watch_ready) and action != 'HOLD') else 'UNCERTAIN'),
            'decision_confidence': confidence,
            'confidence': confidence,
            'calibrated_probs': calibrated_probs,
            'expected_move_pct': expected_move,
            'quantile_range': [_finite_float(forecast.get('q05', 0.0), 0.0), _finite_float(forecast.get('q95', 0.0), 0.0)],
            'position_size_pct': position_pct,
            'consensus_ok': consensus_ok,
            'confidence_ok': confidence_ok,
            'gates_ok': gates_ok,
            'memory_ok': memory_ok,
            'interval_ok': interval_ok,
            'support_gates_ok': support_gates_ok,
            'hard_support_ok': hard_support_ok,
            'execution_guard_ok': execution_guard_ok,
            'opposition_alert': opposition_alert,
            'required_support_gates': [str(getattr(g, 'name', 'support_gate')) for g in required_support_gates],
            'gates_passing': gates_passing,
            'gate_scores': {str(getattr(g, 'name', f'gate_{i}')): _clip01(getattr(g, 'score', 0.0), 0.0) for i, g in enumerate(gate_outputs)},
            'support_gate_scores': support_gate_scores,
            'support_gate_pass': support_gate_pass,
            'shap_contributions': shap,
            'memory_similarity': memory_similarity,
            'memory_weight': memory_weight,
            'memory_threshold_used': memory_threshold_used,
            'memory_ambiguity': ambiguity,
            'ad_indicator': _finite_float(forecast.get('ad_indicator', 0.0), 0.0),
            'poly_slope': _finite_float(forecast.get('poly_slope', 0.0), 0.0),
            'module_reliability': dict(module_reliability or {}),
            'transition_summary': dict(transition_summary or {}),
            'structure_active': bool(structure_consistency_value >= 0.10),
            'projection_support': bool(projection_support),
            'projection_bias_ready': bool(projection_bias_ready),
            'projection_watch_ready': bool(projection_watch_ready),
            'projection_dominance': projection_dominance,
            'branch_weights': {
                'cv': _clip01((module_reliability or {}).get('cv_quality', 0.0), 0.0),
                'structure': 0.0 if structure_consistency_value < 0.10 else structure_consistency_value,
                'memory': memory_weight,
                'gates': _clip01(_finite_mean(gate_scores, 0.0), 0.0),
                'execution': execution_readiness,
                'projection': projection_bias_confidence,
            },
        }
