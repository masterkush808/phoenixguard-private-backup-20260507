"""
PhoenixGuard Decision Kernel Extension
=======================================
Extends existing regression + ensemble with A* scenario prediction.

Integrates into:
  - ImageFusionRegressor (forecast_3m)
  - DecisionKernel (full decision pipeline)
  - Skill gates (execution gates)

Adds scenario-aware confidence boosting and multi-path decision logic.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from phoenixguard.decision.scenario_integration import (
    predict_scenarios_from_chart_and_forecast,
    rank_scenarios_by_ensemble_agreement,
    scenarios_to_paint_layer,
    enhanced_forecast_with_scenarios,
)
from phoenixguard.decision.scenario_paint import (
    ScenarioPainter,
    create_scenario_dashboard_layout,
)


def enhance_forecast_with_scenario_consensus(
    forecast_output: dict[str, Any],
    chart_state: dict[str, Any],
    memory_bank_recall: dict[str, Any] | None = None,
    ensemble_decision: str = "HOLD",
    ensemble_confidence: float = 0.5,
) -> dict[str, Any]:
    """
    Enhance a forecast by incorporating multi-path scenario analysis.

    Boosts forecast confidence if scenarios agree with ensemble.
    Modifies force_hold if scenarios show strong alternative path.

    Args:
        forecast_output: Original Forecast3MOutput from regression
        chart_state: Current chart analysis
        memory_bank_recall: Memory statistics
        ensemble_decision: Consensus direction
        ensemble_confidence: Ensemble confidence

    Returns:
        Modified forecast_output with scenario data injected
    """
    # Generate scenarios
    scenarios = predict_scenarios_from_chart_and_forecast(
        chart_state=chart_state,
        forecast_output=forecast_output,
        memory_recall=memory_bank_recall,
        num_scenarios=5,
        max_depth=5,
    )

    if not scenarios:
        return forecast_output

    # Rank by ensemble agreement
    ranked = rank_scenarios_by_ensemble_agreement(
        scenarios, ensemble_decision, ensemble_confidence
    )

    # Extract top scenario
    top_scenario = ranked[0]
    top_last = top_scenario.scenario.last_candle()
    if top_last is None:
        return forecast_output
    top_dir = top_last.direction
    top_prob = top_scenario.probability

    # Build paint layer
    paint_layer = scenarios_to_paint_layer(ranked, chart_state)

    # Enhance the forecast
    enhanced = dict(forecast_output)

    # Confidence boosting: if top scenario agrees with ensemble, increase confidence
    if top_dir == ensemble_decision:
        existing_conf = forecast_output.get("path_confidence", 0.5)
        boost = 0.1 * top_prob
        enhanced["path_confidence"] = min(1.0, existing_conf + boost)
        enhanced["scenario_confidence_boost"] = boost
    else:
        # Reduce confidence if scenarios suggest alternative
        existing_conf = forecast_output.get("path_confidence", 0.5)
        reduction = 0.1 * (1.0 - top_prob)
        enhanced["path_confidence"] = max(0.1, existing_conf - reduction)
        enhanced["scenario_confidence_reduction"] = reduction

    # Consider relaxing force_hold if scenarios show strong setup
    if enhanced.get("force_hold"):
        avg_scenario_conf = sum(s.probability for s in ranked) / len(ranked)
        top_scenario_quality = ranked[0].scenario.cost  # Lower = better

        if avg_scenario_conf > 0.6 and top_scenario_quality < 2.0:
            # Scenarios show strong setup, consider relaxing hold
            enhanced["force_hold_relaxed"] = True
            enhanced["force_hold_relaxation_reason"] = (
                f"Scenarios show {avg_scenario_conf:.0%} avg confidence, "
                f"top scenario cost {top_scenario_quality:.2f}"
            )

    # Attach scenario data
    enhanced["scenarios"] = paint_layer
    enhanced["scenarios_raw"] = [s.to_paint_dict() for s in ranked]
    enhanced["top_scenario"] = {
        "rank": 1,
        "direction": top_dir,
        "probability": top_prob,
        "transition": ranked[0].scenario.transition_type.value,
    }

    return enhanced


def scenario_dashboard_for_forecast(
    forecast_with_scenarios: Mapping[str, Any],
    title: str = "Forecast Dashboard with Scenarios",
) -> dict[str, Any]:
    """
    Create Plotly dashboard layout for displaying forecast + scenarios.

    Args:
        forecast_with_scenarios: Enhanced forecast from enhance_forecast_*
        title: Dashboard title

    Returns:
        Dict with traces and layout ready for Plotly
    """
    scenarios_data = forecast_with_scenarios.get("scenarios", {})

    if not scenarios_data:
        return {"traces": [], "layout": {}}

    return create_scenario_dashboard_layout(scenarios_data, title=title)


def scenario_gates_for_execution(
    forecast_with_scenarios: Mapping[str, Any],
    existing_gates: dict[str, bool],
) -> dict[str, bool]:
    """
    Apply scenario-based gates to execution gates.

    Adds new gates:
      - "scenario_agreement": top 3 scenarios align on direction
      - "scenario_confidence": average scenario confidence >= threshold
      - "scenario_quality": top scenario has good quality score

    Args:
        forecast_with_scenarios: Enhanced forecast
        existing_gates: Current skill gates dict

    Returns:
        Updated gates dict
    """
    updated_gates = dict(existing_gates)

    scenarios_raw = forecast_with_scenarios.get("scenarios_raw", [])
    if not scenarios_raw:
        updated_gates["scenario_agreement"] = False
        updated_gates["scenario_confidence"] = False
        updated_gates["scenario_quality"] = False
        return updated_gates

    # Top 3 scenarios should agree on direction
    top_3 = scenarios_raw[:3]
    if top_3:
        directions = [
            s.get("candles", [])[-1].get("direction", "HOLD")
            if s.get("candles")
            else "HOLD"
            for s in top_3
        ]
        agreement = len([d for d in directions if d == directions[0]]) / len(directions)
        updated_gates["scenario_agreement"] = agreement >= 0.67  # 2 out of 3

    # Average confidence
    avg_conf = sum(s.get("probability", 0.0) for s in scenarios_raw) / max(len(scenarios_raw), 1)
    updated_gates["scenario_confidence"] = avg_conf >= 0.55

    # Quality of top scenario
    top_scenario = scenarios_raw[0]
    top_cost = top_scenario.get("cost", 10.0)
    updated_gates["scenario_quality"] = top_cost <= 3.0

    return updated_gates


def decision_kernel_extension(
    chart_state: dict[str, Any],
    forecast_output: dict[str, Any],
    ensemble_consensus: str,
    ensemble_confidence: float,
    memory_recall: dict[str, Any] | None = None,
    skill_gates: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """
    Extended decision kernel that includes scenario analysis.

    High-level orchestration for the full pipeline:
    1. Generate scenarios from forecast
    2. Rank by ensemble agreement
    3. Apply scenario gates
    4. Recommend action with confidence

    Args:
        chart_state: Current analysis
        forecast_output: Regression forecast
        ensemble_consensus: Ensemble decision
        ensemble_confidence: Ensemble confidence
        memory_recall: Memory bank stats
        skill_gates: Existing gates

    Returns:
        Decision dict with scenarios, gates, action, confidence
    """
    skill_gates = skill_gates or {}

    # Enhance forecast with scenarios
    enhanced = enhance_forecast_with_scenario_consensus(
        forecast_output=forecast_output,
        chart_state=chart_state,
        memory_bank_recall=memory_recall,
        ensemble_decision=ensemble_consensus,
        ensemble_confidence=ensemble_confidence,
    )

    # Apply scenario gates
    updated_gates = scenario_gates_for_execution(enhanced, skill_gates)

    # Determine action
    action = ensemble_consensus
    action_confidence = enhanced.get("path_confidence", ensemble_confidence)

    # Check if scenarios suggest blocking or boosting
    if not updated_gates.get("scenario_agreement"):
        action = "HOLD"
        action_reason = "Scenarios do not agree on direction"
    elif not updated_gates.get("scenario_confidence"):
        action = "HOLD"
        action_reason = "Average scenario confidence too low"
    elif updated_gates.get("scenario_quality"):
        action_confidence = min(1.0, action_confidence + 0.1)
        action_reason = "Scenarios support action with high quality"
    else:
        action_reason = f"Scenarios align with {ensemble_consensus}"

    # Scenario summary
    top_scenario = enhanced.get("top_scenario", {})
    scenario_summary = {
        "total_paths_explored": len(enhanced.get("scenarios_raw", [])),
        "top_scenario_rank": 1,
        "top_scenario_probability": top_scenario.get("probability", 0.0),
        "top_scenario_direction": top_scenario.get("direction", "HOLD"),
        "consensus_boost": enhanced.get("scenario_confidence_boost", 0.0),
        "gates_passed": sum(1 for v in updated_gates.values() if v),
        "gates_total": len(updated_gates),
    }

    return {
        "action": action,
        "confidence": action_confidence,
        "reason": action_reason,
        "skill_gates": updated_gates,
        "forecast": enhanced,
        "scenario_summary": scenario_summary,
        "dashboard": scenario_dashboard_for_forecast(enhanced),
    }


def scenario_overlay_for_live_dashboard(
    decision_output: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Extract scenario overlay data for live trading dashboard.

    Formats scenario data for real-time display on mobile API dashboard.

    Args:
        decision_output: From decision_kernel_extension

    Returns:
        Dict with overlay components
    """
    forecast = decision_output.get("forecast", {})
    scenarios_paint = forecast.get("scenarios", {})
    scenario_summary = decision_output.get("scenario_summary", {})

    top_ranked = scenarios_paint.get("top_ranked", {})
    confidence_heatmap = scenarios_paint.get("confidence_heatmap", [])
    tree_structure = scenarios_paint.get("tree_structure", {})

    overlay = {
        "type": "scenario_forecast",
        "enabled": True,
        "scenarios": {
            "total": scenario_summary.get("total_paths_explored", 0),
            "top_probability": scenario_summary.get("top_scenario_probability", 0.0),
            "top_direction": scenario_summary.get("top_scenario_direction", "HOLD"),
            "gates_passed": scenario_summary.get("gates_passed", 0),
            "gates_total": scenario_summary.get("gates_total", 0),
        },
        "top_scenario": {
            "candle_count": len(top_ranked.get("candles", [])),
            "transition_type": top_ranked.get("transition_type", "UNKNOWN"),
            "memory_alignment": top_ranked.get("memory_alignment", 0.0),
            "confidence": top_ranked.get("probability", 0.0),
        },
        "tree": tree_structure,
        "heatmap_shape": (len(confidence_heatmap), tree_structure.get("branches", 1)),
    }

    return overlay
