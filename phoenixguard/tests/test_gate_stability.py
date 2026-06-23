from __future__ import annotations

import unittest
from typing import Any, cast

import numpy as np

from phoenixguard.decision.ensemble import EnsembleDecisionEngine
from phoenixguard.decision.skill_gates import CurriculumGates, GateOutput


class TestGateStability(unittest.TestCase):
    def setUp(self) -> None:
        self.gates = CurriculumGates(_NullLogger())
        self.ensemble = EnsembleDecisionEngine(0.78, 0.65, 0.5, 2.0)
        self.rng = np.random.default_rng(808)

    def _mixed_number(self, *, allow_none: bool = True, low: float = -1.5, high: float = 1.5) -> float | None:
        variants: list[float | None] = [
            float(self.rng.uniform(low, high)),
            float(self.rng.normal()),
            np.nan,
            np.inf,
            -np.inf,
        ]
        if allow_none:
            variants.append(None)
        return variants[int(self.rng.integers(0, len(variants)))]

    def _choice(self, values: list[str]) -> str:
        return values[int(self.rng.integers(0, len(values)))]

    def _assert_gate_outputs_are_stable(self, outputs: list[GateOutput]) -> None:
        self.assertGreater(len(outputs), 0)
        for gate in outputs:
            self.assertTrue(np.isfinite(gate.score), msg=f"{gate.name} produced non-finite score")
            self.assertGreaterEqual(gate.score, 0.0, msg=gate.name)
            self.assertLessEqual(gate.score, 1.0, msg=gate.name)
            self.assertIsInstance(gate.pass_fail, bool)
            self.assertIsInstance(gate.detail, dict)

    def test_randomized_gate_pipeline_stays_finite(self) -> None:
        signal_names = [
            "pin_bar",
            "hammer",
            "engulfing",
            "latest_parse_quality",
            "scene_parse_quality",
            "range break",
        ]
        macro_trends = ["BULL", "BEAR", "HOLD", "bull", "bear", ""]
        local_phases = [
            "with_trend_push",
            "with_trend_pause",
            "continuation_base",
            "counter_trend_pullback",
            "counter_trend_spike",
            "reversal_base",
            "",
        ]
        entry_types = ["reversal", "continuation", "trend continuation", "unknown", ""]
        reversal_signals = ["wick_rejection", "engulfing", "reversal", "none", ""]
        continuation_signals = ["breakout", "impulse_pause", "reversal_release", "none", ""]
        structure_setups = ["consolidation_breakout", "impulse_chain", "reversal_release", "none", ""]

        for _ in range(36):
            probs = {
                "BUY": self._mixed_number(),
                "SELL": self._mixed_number(),
                "HOLD": self._mixed_number(),
            }
            q05 = self._mixed_number()
            q95 = self._mixed_number()
            module_logits = np.array(
                [self._mixed_number(allow_none=False) for _ in range(3)],
                dtype=np.float32,
            )
            chart_state = {
                "entry_type": self._choice(entry_types),
                "reversal_signal": self._choice(reversal_signals),
                "continuation_signal": self._choice(continuation_signals),
                "direction": self._choice(["BUY", "SELL", "HOLD", ""]),
                "consolidation_streak": self._mixed_number(),
                "consolidation_score": self._mixed_number(),
                "continuation_probability": self._mixed_number(),
                "reversal_probability": self._mixed_number(),
                "macro_trend": self._choice(macro_trends),
                "local_phase": self._choice(local_phases),
                "phase_risk": self._choice(["breakout_risk", "chop_risk", "exhaustion_risk", "", "continuation"]),
                "path_clarity": self._mixed_number(),
                "structure_trade_ready": bool(int(self.rng.integers(0, 2))),
            }
            prices = [self._mixed_number() for _ in range(8)]
            sub_signals = [(self._mixed_number(), self._choice(signal_names)) for _ in range(6)]

            core_outputs = self.gates.run_all(
                probs=cast(dict[str, float], probs),
                q05=cast(float, q05),
                q95=cast(float, q95),
                momentum_bias=self._choice(["bullish", "bearish", "neutral", ""]),
                explanation=self._choice(
                    [
                        "breakout impulse trend up",
                        "sell pressure rejection",
                        "flat momentum",
                        "",
                    ]
                ),
                sub_signals=cast(list[tuple[float, str]], sub_signals),
                module_logits=module_logits,
                recent_feedback_count=int(self.rng.integers(-10, 120)),
                queue_depth=int(self.rng.integers(-2, 12)),
                gpu_mem_ok=bool(int(self.rng.integers(0, 2))),
                has_dashboard=bool(int(self.rng.integers(0, 2))),
                risk_ethical_ok=bool(int(self.rng.integers(0, 2))),
                chart_state=chart_state,
                prices=cast(list[float], prices),
                direction_prob=cast(float, self._mixed_number()),
                mcts={
                    "buy_prob": self._mixed_number(),
                    "sell_prob": self._mixed_number(),
                },
                memory_sim=cast(float, self._mixed_number()),
                latest_candle_confidence=cast(float, self._mixed_number()),
                geometry_conflict=bool(int(self.rng.integers(0, 2))),
            )
            self.assertEqual(len(core_outputs), 13)
            self._assert_gate_outputs_are_stable(core_outputs)

            support_outputs = self.gates.run_support_gates(
                chart_state=chart_state,
                market_state={
                    "macro_trend": self._choice(macro_trends),
                    "local_phase": self._choice(local_phases),
                    "control_strength_delta": self._mixed_number(),
                    "phase_risk": self._choice(["breakout_risk", "chop_risk", "exhaustion_risk", "", "continuation"]),
                },
                memory_similarity=cast(float, self._mixed_number()),
                memory_label=self._choice(["BUY", "SELL", "HOLD", ""]),
                latest_candle_confidence=cast(float, self._mixed_number()),
                geometry_conflict=bool(int(self.rng.integers(0, 2))),
                reliability=cast(float, self._mixed_number()),
            )
            self.assertEqual(len(support_outputs), 6)
            self._assert_gate_outputs_are_stable(support_outputs)

            decision = self.ensemble.infer(
                rl_probs=cast(dict[str, float], probs),
                forecast={
                    "q05": q05,
                    "q50": self._mixed_number(),
                    "q95": q95,
                    "execution_readiness": self._mixed_number(),
                    "active_consolidation": self._mixed_number(),
                    "structure_trade_ready": self._mixed_number(),
                    "structure_setup": self._choice(structure_setups),
                    "projected_box_direction": self._choice(["BUY", "SELL", "HOLD", ""]),
                    "projected_box_confidence": self._mixed_number(),
                    "projection_bias_confidence": self._mixed_number(),
                    "projection_dominance": self._mixed_number(),
                    "ad_indicator": self._mixed_number(),
                    "poly_slope": self._mixed_number(),
                },
                gate_outputs=core_outputs,
                memory_bank_similarity=cast(float, self._mixed_number()),
                module_reliability=cast(
                    dict[str, float],
                    {
                    "cv_quality": self._mixed_number(),
                    "structure_consistency": self._mixed_number(),
                    "sequence_clarity": self._mixed_number(),
                    "consolidation_quality": self._mixed_number(),
                    "memory_novelty": self._mixed_number(),
                    },
                ),
                memory_summary={
                    "ambiguity": self._mixed_number(),
                    "label_entropy": self._mixed_number(),
                    "consensus_ratio": self._mixed_number(),
                    "mixed_labels": bool(int(self.rng.integers(0, 2))),
                    "dominant_label": self._choice(["BUY", "SELL", "HOLD", ""]),
                },
                latest_candle_confidence=cast(float, self._mixed_number()),
                transition_summary=cast(
                    Any,
                    {
                    "continue_prob": self._mixed_number(),
                    "pullback_prob": self._mixed_number(),
                    "reversal_attempt_prob": self._mixed_number(),
                    "fakeout_prob": self._mixed_number(),
                    },
                ),
                support_gate_outputs=support_outputs,
            )

            self.assertEqual(
                decision["gates_passing"],
                sum(1 for gate in core_outputs if gate.pass_fail),
            )
            self.assertTrue(np.isfinite(decision["confidence"]))
            self.assertGreaterEqual(decision["confidence"], 0.0)
            self.assertLessEqual(decision["confidence"], 1.0)
            self.assertTrue(np.isfinite(decision["position_size_pct"]))
            self.assertIn(decision["action"], {"BUY", "SELL", "HOLD"})

            calibrated = decision["calibrated_probs"]
            total = 0.0
            for key in ("BUY", "SELL", "HOLD"):
                self.assertTrue(np.isfinite(calibrated[key]), msg=key)
                self.assertGreaterEqual(calibrated[key], 0.0, msg=key)
                self.assertLessEqual(calibrated[key], 1.0, msg=key)
                total += calibrated[key]
            self.assertAlmostEqual(total, 1.0, places=6)

    def test_support_gates_do_not_inflate_core_gate_count(self) -> None:
        core_outputs = [
            GateOutput(name=f"g{i}", score=0.8, pass_fail=i < 3, detail={})
            for i in range(12)
        ]
        support_outputs = [
            GateOutput("continuation_strength", 0.95, True, {}),
            GateOutput("memory_regime_agreement", 0.95, True, {}),
            GateOutput("opposition_strength", 0.10, False, {}),
            GateOutput("macro_local_alignment", 0.95, True, {}),
            GateOutput("execution_permission", 0.95, True, {}),
        ]
        decision = self.ensemble.infer(
            rl_probs={"BUY": 0.9, "SELL": 0.05, "HOLD": 0.05},
            forecast={"q05": -0.05, "q50": 0.12, "q95": 0.25, "execution_readiness": 0.7},
            gate_outputs=core_outputs,
            support_gate_outputs=support_outputs,
        )
        self.assertEqual(decision["gates_passing"], 3)
        self.assertTrue(decision["support_gates_ok"])
        self.assertEqual(set(decision["support_gate_scores"]), {g.name for g in support_outputs})

    def test_support_gate_feature_flags_reduce_output_surface(self) -> None:
        outputs = self.gates.run_support_gates(
            chart_state={"macro_trend": "BULL", "local_phase": "with_trend_pause"},
            market_state={"control_strength_delta": 0.4},
            memory_similarity=0.7,
            memory_label="BUY",
            latest_candle_confidence=0.8,
            geometry_conflict=False,
            reliability=0.8,
            use_execution_permission=False,
            use_macro_local_alignment=False,
            use_opposition_strength=False,
        )
        self.assertEqual(
            [gate.name for gate in outputs],
            ["continuation_strength", "memory_regime_agreement", "candle_group_context"],
        )


class _NullLogger:
    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def exception(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
