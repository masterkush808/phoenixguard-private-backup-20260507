from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, TypedDict, cast

import numpy as np
from numpy.typing import NDArray

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from phoenixguard.decision.ensemble import EnsembleDecisionEngine, TransitionSummary
from phoenixguard.decision.skill_gates import CurriculumGates, GateOutput


JsonObject = dict[str, object]
OUTPUT_DIR = REPO_ROOT / "reports" / "skill_gate_visualizer"
OUTPUT_HTML = OUTPUT_DIR / "index.html"
OUTPUT_JSON = OUTPUT_DIR / "skill_gate_visualizer_data.json"

CORE_GATE_META: dict[str, tuple[str, str, str]] = {
    "prob_stats": ("Probability Trust", "Probability", "Tests BUY/SELL/HOLD edge and conformal interval order."),
    "discrete_fsm": ("Momentum State", "State", "Tracks bullish/bearish finite-state pressure."),
    "algo_heap": ("Pattern Evidence", "Pattern", "Ranks independent chart sub-signals and ignores parse artifacts."),
    "ml_stacking": ("Model Stack", "Model", "Checks model-logit agreement and strength."),
    "db_context": ("Feedback Depth", "Memory", "Scores available feedback/history context."),
    "ops_stability": ("Runtime Health", "Ops", "Scores queue and GPU/runtime stability."),
    "ui_analytics": ("Dashboard Ready", "Ops", "Confirms dashboard/operator visibility."),
    "meta_constraints": ("Guardrails", "Safety", "Confirms risk and safety constraints."),
    "regression_est": ("Price Structure", "Structure", "Scores clean price curve fit and directional strength."),
    "knowledge_rep": ("808FX Ontology", "Structure", "Checks entry type, signal, and direction coherence."),
    "candle_group_context": ("25-Candle Box", "Structure", "Checks group story, zone, pullback, and path clarity."),
    "formal_automata": ("Setup Sequence", "Sequence", "Tracks consolidation to reversal to continuation progression."),
    "predictive_analytics": ("Fused Forecast", "Forecast", "Blends base probs, MCTS, memory, latest candle, and structure."),
}

SUPPORT_GATE_META: dict[str, tuple[str, str, str]] = {
    "continuation_strength": ("Continuation", "Structure", "Scores trend-continuation pressure."),
    "memory_regime_agreement": ("Memory Alignment", "Memory", "Checks memory label agreement with macro regime."),
    "opposition_strength": ("Opposition", "Risk", "Scores counter-trend or geometry pressure."),
    "macro_local_alignment": ("Macro/Local", "Structure", "Checks local phase agreement with macro trend."),
    "execution_permission": ("Entry Telemetry", "Safety", "Scores latest candle, phase risk, and reliability."),
    "forecast_calibration": ("Forecast Quality", "Forecast", "Checks forecast interval order, width, and readiness."),
    "interval_efficiency": ("Interval Efficiency", "Forecast", "Scores whether the forecast interval is useful."),
    "regime_stability": ("Regime Stability", "Risk", "Penalizes contradiction, fakeout, novelty, and conflict."),
    "transition_alignment": ("Next-Candle Path", "Sequence", "Checks favorable transition path versus hazard."),
    "candle_group_context": ("25-Candle Box", "Structure", "Checks group story, zone, pullback, and path clarity."),
}


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        return None


@dataclass(frozen=True)
class GateScenario:
    name: str
    profile: str
    focus: str
    probs: dict[str, float]
    q05: float
    q95: float
    momentum_bias: str
    explanation: str
    sub_signals: list[tuple[float, str]]
    module_logits: NDArray[np.float32]
    recent_feedback_count: int
    queue_depth: int
    gpu_mem_ok: bool
    has_dashboard: bool
    risk_ethical_ok: bool
    chart_state: dict[str, Any]
    prices: list[float]
    direction_prob: float
    mcts: dict[str, Any]
    memory_sim: float
    latest_candle_confidence: float
    geometry_conflict: bool
    forecast: dict[str, Any]
    market_state: dict[str, Any]
    transition_summary: dict[str, Any]
    memory_summary: dict[str, Any]
    ood_summary: dict[str, Any]
    memory_label: str
    reliability: float


class GateRow(TypedDict):
    name: str
    label: str
    bucket: str
    purpose: str
    score: float
    raw_score: float
    router_weight: float
    pass_fail: bool
    detail: JsonObject


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[object, object], value)
        return {str(key): _json_ready(item) for key, item in mapping_value.items()}
    if isinstance(value, np.ndarray):
        return _json_ready(value.tolist())
    if isinstance(value, np.generic):
        return _json_ready(value.item())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence_value = cast(Sequence[object], value)
        return [_json_ready(item) for item in sequence_value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _gate_meta(name: str, *, support: bool) -> tuple[str, str, str]:
    source = SUPPORT_GATE_META if support else CORE_GATE_META
    return source.get(name, (name.replace("_", " ").title(), "Other", "Diagnostic contributor."))


def _gate_row(gate: GateOutput, *, support: bool) -> GateRow:
    label, bucket, purpose = _gate_meta(gate.name, support=support)
    raw_value = gate.detail.get("raw_score", gate.score)
    routed_value = gate.detail.get("router_weight", 1.0)
    detail = _json_ready(gate.detail)
    detail_obj: JsonObject = cast(JsonObject, detail) if isinstance(detail, dict) else {}
    return {
        "name": gate.name,
        "label": label,
        "bucket": bucket,
        "purpose": purpose,
        "score": round(float(gate.score), 4),
        "raw_score": round(float(raw_value), 4),
        "router_weight": round(float(routed_value), 4),
        "pass_fail": bool(gate.pass_fail),
        "detail": detail_obj,
    }


def _summary(rows: list[GateRow]) -> JsonObject:
    passing = sum(1 for row in rows if row["pass_fail"] is True)
    total = len(rows)
    score_values = [row["score"] for row in rows]
    avg_score = float(np.mean(score_values)) if score_values else 0.0
    return {
        "passing": passing,
        "total": total,
        "avg_score": round(avg_score, 4),
        "failing": [str(row["name"]) for row in rows if row["pass_fail"] is not True],
    }


def _bucket_totals(rows: list[GateRow]) -> list[JsonObject]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        bucket = row["bucket"]
        totals[bucket] = totals.get(bucket, 0.0) + row["score"]
        counts[bucket] = counts.get(bucket, 0) + 1
    return [
        {
            "bucket": bucket,
            "score": round(total / max(1, counts[bucket]), 4),
            "count": counts[bucket],
        }
        for bucket, total in sorted(totals.items())
    ]


def _scenario_rows(scenario: GateScenario) -> JsonObject:
    gates = CurriculumGates(_NullLogger())
    core_outputs = gates.run_all(
        probs=scenario.probs,
        q05=scenario.q05,
        q95=scenario.q95,
        momentum_bias=scenario.momentum_bias,
        explanation=scenario.explanation,
        sub_signals=scenario.sub_signals,
        module_logits=scenario.module_logits,
        recent_feedback_count=scenario.recent_feedback_count,
        queue_depth=scenario.queue_depth,
        gpu_mem_ok=scenario.gpu_mem_ok,
        has_dashboard=scenario.has_dashboard,
        risk_ethical_ok=scenario.risk_ethical_ok,
        chart_state=scenario.chart_state,
        prices=scenario.prices,
        direction_prob=scenario.direction_prob,
        mcts=scenario.mcts,
        memory_sim=scenario.memory_sim,
        latest_candle_confidence=scenario.latest_candle_confidence,
        geometry_conflict=scenario.geometry_conflict,
    )
    support_outputs = gates.run_support_gates(
        chart_state=scenario.chart_state,
        market_state=scenario.market_state,
        forecast=scenario.forecast,
        transition_summary=scenario.transition_summary,
        memory_summary=scenario.memory_summary,
        ood_summary=scenario.ood_summary,
        memory_similarity=scenario.memory_sim,
        memory_label=scenario.memory_label,
        latest_candle_confidence=scenario.latest_candle_confidence,
        geometry_conflict=scenario.geometry_conflict,
        reliability=scenario.reliability,
    )
    engine = EnsembleDecisionEngine()
    decision = engine.infer(
        rl_probs=scenario.probs,
        forecast=scenario.forecast,
        gate_outputs=core_outputs,
        memory_bank_similarity=scenario.memory_sim,
        module_reliability={
            "cv_quality": scenario.latest_candle_confidence,
            "structure_consistency": scenario.reliability,
            "sequence_clarity": scenario.forecast.get("path_confidence", 0.0),
            "consolidation_quality": scenario.chart_state.get("consolidation_score", 0.0),
            "memory_novelty": scenario.ood_summary.get("style_novelty", 0.0),
        },
        memory_summary=scenario.memory_summary,
        latest_candle_confidence=scenario.latest_candle_confidence,
        transition_summary=cast(TransitionSummary, scenario.transition_summary),
        support_gate_outputs=support_outputs,
    )
    core_rows = [_gate_row(gate, support=False) for gate in core_outputs]
    support_rows = [_gate_row(gate, support=True) for gate in support_outputs]
    return {
        "name": scenario.name,
        "profile": scenario.profile,
        "focus": scenario.focus,
        "decision": _json_ready(decision),
        "core": {"summary": _summary(core_rows), "rows": core_rows},
        "support": {"summary": _summary(support_rows), "rows": support_rows},
        "buckets": _bucket_totals(core_rows + support_rows),
    }


def _scenarios() -> list[GateScenario]:
    return [
        GateScenario(
            name="Intraday Enter Now Candidate",
            profile="INTRADAY_ENTER_NOW",
            focus="Fresh BUY pressure with structure agreement and current-candle support.",
            probs={"BUY": 0.72, "SELL": 0.10, "HOLD": 0.18},
            q05=0.08,
            q95=0.32,
            momentum_bias="bullish",
            explanation="breakout impulse trend up after bottom reclaim",
            sub_signals=[
                (0.88, "bottom_reclaim"),
                (0.84, "breakout"),
                (0.79, "latest_candle_buy"),
                (0.76, "impulse_pause"),
                (0.71, "box_sequence_agreement"),
            ],
            module_logits=np.array([0.74, 0.09, 0.17], dtype=np.float32),
            recent_feedback_count=54,
            queue_depth=0,
            gpu_mem_ok=True,
            has_dashboard=True,
            risk_ethical_ok=True,
            chart_state={
                "entry_type": "continuation",
                "continuation_signal": "breakout",
                "reversal_signal": "reversal_release",
                "direction": "BUY",
                "direction_probability": 0.78,
                "macro_trend": "BULL",
                "local_phase": "with_trend_push",
                "phase_risk": "breakout_risk",
                "path_clarity": 0.76,
                "structure_trade_ready": True,
                "consolidation_streak": 5,
                "consolidation_type": "box",
                "consolidation_score": 0.72,
                "continuation_probability": 0.68,
                "reversal_probability": 0.34,
                "candle_group_summary": {
                    "window_size": 25,
                    "window_ready": True,
                    "box_zone": "bottom",
                    "group_story": "bottom_reclaim",
                    "group_bias_direction": "BUY",
                    "group_bias_confidence": 0.82,
                    "buy_pullback_valid": True,
                    "sell_pullback_valid": False,
                    "entry_ready": True,
                    "path_clarity": 0.77,
                    "box_sequence_agreement": 0.73,
                    "trend_strength": 0.68,
                },
            },
            prices=[2.054, 2.057, 2.058, 2.061, 2.064, 2.066, 2.069, 2.071],
            direction_prob=0.76,
            mcts={"buy_prob": 0.68, "sell_prob": 0.09, "hold_prob": 0.23},
            memory_sim=0.66,
            latest_candle_confidence=0.78,
            geometry_conflict=False,
            forecast={
                "q05": 0.08,
                "q50": 0.20,
                "q95": 0.32,
                "hold_threshold_used": 0.36,
                "path_confidence": 0.74,
                "execution_readiness": 0.72,
                "contradiction_score": 0.08,
                "fakeout_prob": 0.12,
                "reversal_attempt_prob": 0.18,
                "structure_setup": "consolidation_breakout",
                "projected_box_confidence": 0.75,
                "structure_trade_ready": 0.86,
            },
            market_state={
                "macro_trend": "BULL",
                "local_phase": "with_trend_push",
                "control_strength_delta": 0.72,
                "phase_risk": "breakout_risk",
            },
            transition_summary={
                "continue_prob": 0.70,
                "pullback_prob": 0.14,
                "reversal_attempt_prob": 0.12,
                "fakeout_prob": 0.08,
            },
            memory_summary={
                "ambiguity": 0.12,
                "label_entropy": 0.10,
                "consensus_ratio": 0.78,
                "mixed_labels": False,
                "dominant_label": "BUY",
            },
            ood_summary={"style_novelty": 0.10},
            memory_label="BUY",
            reliability=0.78,
        ),
        GateScenario(
            name="Swing Pullback Watch",
            profile="SWING",
            focus="Strong larger-picture SELL context, but latest candle is not entry-clean.",
            probs={"BUY": 0.15, "SELL": 0.65, "HOLD": 0.20},
            q05=-0.34,
            q95=-0.06,
            momentum_bias="bearish",
            explanation="sell pressure rejection after top-zone pullback",
            sub_signals=[
                (0.86, "top_rejection"),
                (0.80, "wick_rejection"),
                (0.73, "sell_memory_bias"),
                (0.68, "macro_local_alignment"),
                (0.57, "range_break_watch"),
            ],
            module_logits=np.array([0.16, 0.67, 0.17], dtype=np.float32),
            recent_feedback_count=42,
            queue_depth=1,
            gpu_mem_ok=True,
            has_dashboard=True,
            risk_ethical_ok=True,
            chart_state={
                "entry_type": "reversal",
                "continuation_signal": "range_break_watch",
                "reversal_signal": "wick_rejection",
                "direction": "SELL",
                "direction_probability": 0.70,
                "macro_trend": "BEAR",
                "local_phase": "counter_trend_pullback",
                "phase_risk": "managed_counter_trend",
                "path_clarity": 0.70,
                "structure_trade_ready": True,
                "consolidation_streak": 4,
                "consolidation_type": "range",
                "consolidation_score": 0.64,
                "continuation_probability": 0.32,
                "reversal_probability": 0.58,
                "candle_group_summary": {
                    "window_size": 25,
                    "window_ready": True,
                    "box_zone": "top",
                    "group_story": "top_rejection",
                    "group_bias_direction": "SELL",
                    "group_bias_confidence": 0.76,
                    "buy_pullback_valid": False,
                    "sell_pullback_valid": True,
                    "entry_ready": True,
                    "path_clarity": 0.71,
                    "box_sequence_agreement": 0.62,
                    "trend_strength": 0.59,
                },
            },
            prices=[2.105, 2.102, 2.100, 2.098, 2.096, 2.095, 2.093, 2.091],
            direction_prob=0.70,
            mcts={"buy_prob": 0.17, "sell_prob": 0.62, "hold_prob": 0.21},
            memory_sim=0.72,
            latest_candle_confidence=0.42,
            geometry_conflict=False,
            forecast={
                "q05": -0.34,
                "q50": -0.19,
                "q95": -0.06,
                "hold_threshold_used": 0.42,
                "path_confidence": 0.70,
                "execution_readiness": 0.52,
                "contradiction_score": 0.18,
                "fakeout_prob": 0.20,
                "reversal_attempt_prob": 0.58,
                "structure_setup": "reversal_release",
                "projected_box_confidence": 0.68,
                "structure_trade_ready": 0.74,
            },
            market_state={
                "macro_trend": "BEAR",
                "local_phase": "counter_trend_pullback",
                "control_strength_delta": 0.44,
                "phase_risk": "managed_counter_trend",
            },
            transition_summary={
                "continue_prob": 0.28,
                "pullback_prob": 0.22,
                "reversal_attempt_prob": 0.60,
                "fakeout_prob": 0.16,
            },
            memory_summary={
                "ambiguity": 0.16,
                "label_entropy": 0.14,
                "consensus_ratio": 0.74,
                "mixed_labels": False,
                "dominant_label": "SELL",
            },
            ood_summary={"style_novelty": 0.12},
            memory_label="SELL",
            reliability=0.72,
        ),
        GateScenario(
            name="Chop Blocked Read",
            profile="STUDY_ONLY",
            focus="Mixed structure, weak edge, geometry conflict, and low current-candle quality.",
            probs={"BUY": 0.34, "SELL": 0.32, "HOLD": 0.34},
            q05=-0.44,
            q95=0.45,
            momentum_bias="neutral",
            explanation="flat momentum with conflicting rejection",
            sub_signals=[
                (0.44, "range_noise"),
                (0.38, "latest_parse_quality"),
                (0.36, "scene_parse_quality"),
                (0.41, "mixed_wicks"),
            ],
            module_logits=np.array([0.34, 0.32, 0.34], dtype=np.float32),
            recent_feedback_count=8,
            queue_depth=5,
            gpu_mem_ok=True,
            has_dashboard=True,
            risk_ethical_ok=True,
            chart_state={
                "entry_type": "unknown",
                "continuation_signal": "none",
                "reversal_signal": "none",
                "direction": "HOLD",
                "direction_probability": 0.40,
                "macro_trend": "HOLD",
                "local_phase": "counter_trend_spike",
                "phase_risk": "contradiction",
                "path_clarity": 0.24,
                "structure_trade_ready": False,
                "consolidation_streak": 2,
                "consolidation_type": "none",
                "consolidation_score": 0.28,
                "continuation_probability": 0.26,
                "reversal_probability": 0.27,
                "candle_group_summary": {
                    "window_size": 18,
                    "window_ready": False,
                    "box_zone": "middle",
                    "group_story": "monitor_only",
                    "group_bias_direction": "HOLD",
                    "group_bias_confidence": 0.24,
                    "buy_pullback_valid": False,
                    "sell_pullback_valid": False,
                    "entry_ready": False,
                    "path_clarity": 0.22,
                    "box_sequence_agreement": 0.20,
                    "trend_strength": 0.18,
                },
            },
            prices=[2.060, 2.061, 2.0605, 2.0612, 2.0608, 2.0610, 2.0606, 2.0611],
            direction_prob=0.40,
            mcts={"buy_prob": 0.33, "sell_prob": 0.31, "hold_prob": 0.36},
            memory_sim=0.22,
            latest_candle_confidence=0.28,
            geometry_conflict=True,
            forecast={
                "q05": -0.44,
                "q50": 0.01,
                "q95": 0.45,
                "hold_threshold_used": 0.35,
                "path_confidence": 0.24,
                "execution_readiness": 0.18,
                "contradiction_score": 0.62,
                "fakeout_prob": 0.48,
                "reversal_attempt_prob": 0.34,
                "structure_setup": "none",
                "projected_box_confidence": 0.20,
                "structure_trade_ready": 0.0,
            },
            market_state={
                "macro_trend": "HOLD",
                "local_phase": "counter_trend_spike",
                "control_strength_delta": 0.08,
                "phase_risk": "contradiction",
            },
            transition_summary={
                "continue_prob": 0.24,
                "pullback_prob": 0.34,
                "reversal_attempt_prob": 0.26,
                "fakeout_prob": 0.44,
            },
            memory_summary={
                "ambiguity": 0.58,
                "label_entropy": 0.62,
                "consensus_ratio": 0.40,
                "mixed_labels": True,
                "dominant_label": "HOLD",
            },
            ood_summary={"style_novelty": 0.48},
            memory_label="HOLD",
            reliability=0.38,
        ),
    ]


def build_data() -> JsonObject:
    scenario_rows = [_scenario_rows(scenario) for scenario in _scenarios()]
    return {
        "schema_version": "PG_SKILL_GATE_VISUALIZER_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "phoenixguard.decision.skill_gates.CurriculumGates",
        "doctrine": "Skill gates are diagnostic contributors. They do not authorize execution.",
        "core_gate_count": 13,
        "scenarios": scenario_rows,
    }


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PhoenixGuard Skill Gate Visualizer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #11110f;
      --panel: #191915;
      --panel-2: #20201a;
      --line: #363428;
      --text: #f2efe4;
      --muted: #a8a08f;
      --amber: #d9a72b;
      --green: #35c478;
      --red: #ec5c54;
      --cyan: #56b6d7;
      --violet: #9b7ce9;
      --steel: #7e8794;
      --bar: #c89a2d;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px),
        linear-gradient(180deg, rgba(255,255,255,.025) 1px, transparent 1px),
        var(--bg);
      background-size: 48px 48px;
      color: var(--text);
      letter-spacing: 0;
    }
    button, select { font: inherit; }
    .shell {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr) 360px;
      min-height: 100vh;
    }
    .rail, .inspector {
      border-color: var(--line);
      background: rgba(18,18,15,.94);
      min-height: 100vh;
      overflow: auto;
    }
    .rail { border-right: 1px solid var(--line); padding: 18px 14px; }
    .inspector { border-left: 1px solid var(--line); padding: 16px; }
    .brand {
      display: grid;
      gap: 2px;
      margin-bottom: 18px;
    }
    .brand small, .label, .micro {
      color: var(--muted);
      text-transform: uppercase;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .12em;
    }
    .brand h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.04;
      letter-spacing: 0;
    }
    .scenario {
      width: 100%;
      border: 1px solid var(--line);
      background: transparent;
      color: var(--text);
      text-align: left;
      padding: 12px;
      margin-bottom: 8px;
      border-radius: 6px;
      cursor: pointer;
      transition: border-color .16s ease, background .16s ease, transform .16s ease;
    }
    .scenario:hover { border-color: rgba(217,167,43,.65); transform: translateY(-1px); }
    .scenario.active { background: rgba(217,167,43,.12); border-color: var(--amber); }
    .scenario strong { display: block; font-size: 13px; margin-bottom: 6px; }
    .scenario span { color: var(--muted); font-size: 12px; line-height: 1.35; }
    .main {
      min-width: 0;
      padding: 18px;
      display: grid;
      grid-template-rows: auto auto minmax(320px, 1fr) auto;
      gap: 14px;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 14px;
    }
    .title h2 {
      margin: 0 0 6px;
      font-size: clamp(24px, 3vw, 38px);
      line-height: 1;
      letter-spacing: 0;
    }
    .title p { margin: 0; color: var(--muted); max-width: 780px; font-size: 13px; line-height: 1.45; }
    .mode-toggle {
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      flex: 0 0 auto;
    }
    .mode-toggle button {
      border: 0;
      border-right: 1px solid var(--line);
      padding: 8px 12px;
      color: var(--muted);
      background: transparent;
      cursor: pointer;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .mode-toggle button:last-child { border-right: 0; }
    .mode-toggle button.active { background: rgba(217,167,43,.15); color: var(--text); }
    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      background: rgba(25,25,21,.82);
      border-radius: 6px;
      padding: 11px 12px;
      min-height: 74px;
    }
    .metric b {
      display: block;
      font-size: 22px;
      margin-top: 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .graph-area {
      display: grid;
      grid-template-rows: auto minmax(280px, 1fr);
      border: 1px solid var(--line);
      background: rgba(16,16,13,.78);
      border-radius: 6px;
      overflow: hidden;
      min-height: 430px;
    }
    .graph-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
    }
    .legend { display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }
    .legend span { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--bar); }
    canvas {
      width: 100%;
      height: 100%;
      display: block;
    }
    .bucket-row {
      display: grid;
      grid-template-columns: 120px 1fr 52px;
      gap: 10px;
      align-items: center;
      padding: 8px 0;
      border-bottom: 1px solid rgba(255,255,255,.06);
    }
    .bucket-row:last-child { border-bottom: 0; }
    .track { height: 9px; background: #2a2922; border-radius: 99px; overflow: hidden; }
    .fill { height: 100%; background: var(--amber); border-radius: 99px; width: 0; transition: width .24s ease; }
    .gate-list {
      display: grid;
      gap: 8px;
      margin-top: 14px;
    }
    .gate-item {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: rgba(29,29,24,.68);
      cursor: pointer;
      transition: border-color .16s ease, background .16s ease;
    }
    .gate-item:hover, .gate-item.active { border-color: var(--amber); background: rgba(217,167,43,.10); }
    .gate-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
    }
    .gate-item strong { font-size: 13px; }
    .badge {
      font-size: 10px;
      font-weight: 900;
      padding: 4px 6px;
      border-radius: 4px;
      border: 1px solid currentColor;
      color: var(--green);
    }
    .badge.fail { color: var(--red); }
    .gate-item p { margin: 8px 0 0; color: var(--muted); font-size: 12px; line-height: 1.35; }
    .detail {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(10,10,8,.66);
      padding: 12px;
    }
    .detail h3 { margin: 0 0 8px; font-size: 18px; letter-spacing: 0; }
    .detail pre {
      margin: 12px 0 0;
      overflow: auto;
      max-height: 260px;
      padding: 10px;
      border-radius: 5px;
      background: #0b0b09;
      color: #d8d0bf;
      font-size: 11px;
      line-height: 1.45;
    }
    .hover-tip {
      min-height: 28px;
      color: var(--muted);
      font-size: 12px;
      padding: 8px 0;
    }
    @media (max-width: 1180px) {
      .shell { grid-template-columns: 230px minmax(0, 1fr); }
      .inspector { grid-column: 1 / -1; min-height: auto; border-left: 0; border-top: 1px solid var(--line); }
      .metrics { grid-template-columns: repeat(3, minmax(130px, 1fr)); }
    }
    @media (max-width: 760px) {
      .shell { display: block; }
      .rail, .inspector { min-height: auto; border: 0; border-bottom: 1px solid var(--line); }
      .main { padding: 14px; }
      .topbar { align-items: stretch; flex-direction: column; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .graph-area { min-height: 380px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="rail">
      <div class="brand">
        <small>PhoenixGuard</small>
        <h1>Skill Gate Visualizer</h1>
      </div>
      <div class="label">Scenario</div>
      <div id="scenario-list"></div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div class="title">
          <h2 id="scenario-title">Scenario</h2>
          <p id="scenario-focus"></p>
        </div>
        <div class="mode-toggle" aria-label="Gate set">
          <button id="mode-core" class="active" type="button">Core</button>
          <button id="mode-support" type="button">Support</button>
        </div>
      </div>
      <section class="metrics" id="metrics"></section>
      <section class="graph-area">
        <div class="graph-head">
          <div>
            <div class="label" id="graph-label">Core Gates</div>
            <div class="hover-tip" id="hover-tip">Hover a bar for gate detail.</div>
          </div>
          <div class="legend">
            <span><i class="dot" style="background:var(--green)"></i>pass</span>
            <span><i class="dot" style="background:var(--red)"></i>fail</span>
            <span><i class="dot" style="background:var(--amber)"></i>routed score</span>
          </div>
        </div>
        <canvas id="gate-canvas" width="1200" height="520"></canvas>
      </section>
      <section>
        <div class="label">Bucket Contribution</div>
        <div id="bucket-list"></div>
      </section>
    </main>
    <aside class="inspector">
      <div class="label">Gate Inspector</div>
      <div id="gate-list" class="gate-list"></div>
      <div id="gate-detail" class="detail"></div>
    </aside>
  </div>
  <script>
    window.PG_SKILL_GATE_DATA = __DATA__;
  </script>
  <script>
    const data = window.PG_SKILL_GATE_DATA;
    let scenarioIndex = 0;
    let mode = "core";
    let selectedGate = 0;
    let bars = [];

    const colors = {
      Probability: "#d9a72b",
      State: "#56b6d7",
      Pattern: "#35c478",
      Model: "#9b7ce9",
      Memory: "#c58a55",
      Ops: "#7e8794",
      Safety: "#ec5c54",
      Structure: "#2fbf9a",
      Sequence: "#e0c15e",
      Forecast: "#74a7ff",
      Risk: "#df6b73",
      Other: "#c89a2d"
    };

    function currentScenario() {
      return data.scenarios[scenarioIndex];
    }

    function currentRows() {
      return currentScenario()[mode].rows;
    }

    function currentSummary() {
      return currentScenario()[mode].summary;
    }

    function formatPct(value) {
      return `${Math.round(Number(value || 0) * 100)}%`;
    }

    function renderScenarios() {
      const list = document.getElementById("scenario-list");
      list.innerHTML = "";
      data.scenarios.forEach((scenario, index) => {
        const button = document.createElement("button");
        button.className = "scenario" + (index === scenarioIndex ? " active" : "");
        button.type = "button";
        button.innerHTML = `<strong>${scenario.name}</strong><span>${scenario.profile}</span>`;
        button.addEventListener("click", () => {
          scenarioIndex = index;
          selectedGate = 0;
          renderAll();
        });
        list.appendChild(button);
      });
    }

    function renderTitle() {
      const scenario = currentScenario();
      document.getElementById("scenario-title").textContent = scenario.name;
      document.getElementById("scenario-focus").textContent = scenario.focus;
      document.getElementById("graph-label").textContent = mode === "core" ? "Core Gates" : "Support Gates";
      document.getElementById("mode-core").classList.toggle("active", mode === "core");
      document.getElementById("mode-support").classList.toggle("active", mode === "support");
    }

    function renderMetrics() {
      const scenario = currentScenario();
      const summary = currentSummary();
      const decision = scenario.decision;
      const metrics = [
        ["Action", decision.action || "HOLD"],
        ["Confidence", formatPct(decision.confidence)],
        ["Core Passing", `${scenario.core.summary.passing}/${scenario.core.summary.total}`],
        ["Support OK", decision.support_gates_ok ? "YES" : "NO"],
        ["Mode Avg", formatPct(summary.avg_score)]
      ];
      document.getElementById("metrics").innerHTML = metrics.map(([label, value]) => `
        <div class="metric"><div class="micro">${label}</div><b>${value}</b></div>
      `).join("");
    }

    function resizeCanvas(canvas) {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(720, Math.floor(rect.width * ratio));
      const height = Math.max(360, Math.floor(rect.height * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      return { width: rect.width, height: rect.height, ctx };
    }

    function renderCanvas() {
      const canvas = document.getElementById("gate-canvas");
      const { width, height, ctx } = resizeCanvas(canvas);
      const rows = currentRows();
      bars = [];
      ctx.clearRect(0, 0, width, height);
      const pad = { left: 52, right: 22, top: 28, bottom: 96 };
      const graphW = width - pad.left - pad.right;
      const graphH = height - pad.top - pad.bottom;
      ctx.strokeStyle = "rgba(255,255,255,.12)";
      ctx.lineWidth = 1;
      ctx.font = "11px Inter, system-ui, sans-serif";
      ctx.fillStyle = "#a8a08f";
      for (let i = 0; i <= 4; i += 1) {
        const y = pad.top + graphH - graphH * (i / 4);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        ctx.fillText(`${i * 25}%`, 10, y + 4);
      }
      const thresholdY = pad.top + graphH - graphH * 0.5;
      ctx.strokeStyle = "rgba(217,167,43,.55)";
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(pad.left, thresholdY);
      ctx.lineTo(width - pad.right, thresholdY);
      ctx.stroke();
      ctx.setLineDash([]);

      const gap = Math.max(6, graphW / rows.length * 0.18);
      const barW = Math.max(18, (graphW - gap * (rows.length - 1)) / rows.length);
      rows.forEach((row, index) => {
        const x = pad.left + index * (barW + gap);
        const h = graphH * Number(row.score || 0);
        const y = pad.top + graphH - h;
        const base = colors[row.bucket] || colors.Other;
        ctx.fillStyle = row.pass_fail ? base : "rgba(236,92,84,.68)";
        ctx.fillRect(x, y, barW, h);
        ctx.fillStyle = "rgba(255,255,255,.10)";
        ctx.fillRect(x, pad.top, barW, graphH - h);
        ctx.strokeStyle = index === selectedGate ? "#f2efe4" : "rgba(255,255,255,.12)";
        ctx.lineWidth = index === selectedGate ? 2 : 1;
        ctx.strokeRect(x, y, barW, h);
        ctx.save();
        ctx.translate(x + barW * 0.5, height - 20);
        ctx.rotate(-Math.PI / 4);
        ctx.fillStyle = "#a8a08f";
        ctx.textAlign = "right";
        ctx.fillText(row.label, 0, 0);
        ctx.restore();
        ctx.fillStyle = "#f2efe4";
        ctx.textAlign = "center";
        ctx.fillText(formatPct(row.score), x + barW * 0.5, y - 6);
        bars.push({ x, y: pad.top, w: barW, h: graphH, index });
      });
    }

    function renderBuckets() {
      const buckets = currentScenario().buckets;
      document.getElementById("bucket-list").innerHTML = buckets.map(row => `
        <div class="bucket-row">
          <div class="micro">${row.bucket}</div>
          <div class="track"><div class="fill" style="width:${Math.round(row.score * 100)}%; background:${colors[row.bucket] || colors.Other}"></div></div>
          <div>${formatPct(row.score)}</div>
        </div>
      `).join("");
    }

    function renderGateList() {
      const rows = currentRows();
      const list = document.getElementById("gate-list");
      list.innerHTML = rows.map((row, index) => `
        <div class="gate-item ${index === selectedGate ? "active" : ""}" data-index="${index}">
          <div class="gate-row">
            <strong>${row.label}</strong>
            <span class="badge ${row.pass_fail ? "" : "fail"}">${row.pass_fail ? "PASS" : "FAIL"}</span>
          </div>
          <div class="track"><div class="fill" style="width:${Math.round(row.score * 100)}%; background:${colors[row.bucket] || colors.Other}"></div></div>
          <p>${row.bucket} · ${row.purpose}</p>
        </div>
      `).join("");
      list.querySelectorAll(".gate-item").forEach(item => {
        item.addEventListener("click", () => {
          selectedGate = Number(item.getAttribute("data-index") || 0);
          renderCanvas();
          renderGateList();
          renderGateDetail();
        });
      });
    }

    function renderGateDetail() {
      const row = currentRows()[selectedGate] || currentRows()[0];
      if (!row) {
        document.getElementById("gate-detail").innerHTML = "";
        return;
      }
      document.getElementById("gate-detail").innerHTML = `
        <h3>${row.label}</h3>
        <div class="micro">${row.name} · ${row.bucket}</div>
        <p>${row.purpose}</p>
        <div class="bucket-row"><div class="micro">Routed</div><div class="track"><div class="fill" style="width:${Math.round(row.score * 100)}%; background:${colors[row.bucket] || colors.Other}"></div></div><div>${formatPct(row.score)}</div></div>
        <div class="bucket-row"><div class="micro">Raw</div><div class="track"><div class="fill" style="width:${Math.round(row.raw_score * 100)}%; background:#7e8794"></div></div><div>${formatPct(row.raw_score)}</div></div>
        <pre>${JSON.stringify(row.detail, null, 2)}</pre>
      `;
    }

    function renderAll() {
      renderScenarios();
      renderTitle();
      renderMetrics();
      renderBuckets();
      renderCanvas();
      renderGateList();
      renderGateDetail();
    }

    document.getElementById("mode-core").addEventListener("click", () => {
      mode = "core";
      selectedGate = 0;
      renderAll();
    });
    document.getElementById("mode-support").addEventListener("click", () => {
      mode = "support";
      selectedGate = 0;
      renderAll();
    });
    document.getElementById("gate-canvas").addEventListener("mousemove", event => {
      const rect = event.currentTarget.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const hit = bars.find(bar => x >= bar.x && x <= bar.x + bar.w && y >= bar.y && y <= bar.y + bar.h);
      if (hit) {
        const row = currentRows()[hit.index];
        document.getElementById("hover-tip").textContent = `${row.label}: ${formatPct(row.score)} · ${row.bucket} · ${row.pass_fail ? "PASS" : "FAIL"}`;
      } else {
        document.getElementById("hover-tip").textContent = "Hover a bar for gate detail.";
      }
    });
    document.getElementById("gate-canvas").addEventListener("click", event => {
      const rect = event.currentTarget.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const hit = bars.find(bar => x >= bar.x && x <= bar.x + bar.w && y >= bar.y && y <= bar.y + bar.h);
      if (hit) {
        selectedGate = hit.index;
        renderAll();
      }
    });
    window.addEventListener("resize", renderCanvas);
    renderAll();
  </script>
</body>
</html>
"""


def write_visualizer() -> tuple[Path, Path]:
    data = build_data()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    return OUTPUT_HTML, OUTPUT_JSON


def main() -> int:
    html_path, json_path = write_visualizer()
    print(f"wrote {html_path}")
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
