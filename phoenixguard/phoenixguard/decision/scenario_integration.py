"""
PhoenixGuard Scenario Integration Layer
========================================
Bridges A* scenario prediction with regression forecasts and ensemble decisions.

Provides:
  - Conversion from regression outputs to A* inputs
  - Memory bank integration for transition bias
  - Scenario ranking by ensemble agreement
  - Paint layer generation for visualization
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence, cast

from phoenixguard.decision.a_star_scenarios import (
    CandleState,
    A_StarScenarioPredictor,
    ScenarioPrediction,
)


def chart_state_to_candle(chart_state: Mapping[str, Any]) -> CandleState:
    """Convert PhoenixGuard chart_state dict to CandleState."""
    entry_candle_raw = chart_state.get("entry_candle", {})
    entry_candle = cast(dict[str, Any], entry_candle_raw if isinstance(entry_candle_raw, Mapping) else {})

    open_val = float(cast(Any, entry_candle.get("o") or 0.0))
    high_val = float(cast(Any, entry_candle.get("h") or 0.0))
    low_val = float(cast(Any, entry_candle.get("l") or 0.0))
    close_val = float(cast(Any, entry_candle.get("c") or 0.0))
    volume_val = float(cast(Any, entry_candle.get("v") or 1.0))

    # Direction from chart state or entry candle
    direction = str(chart_state.get("direction") or "HOLD").upper()
    confidence = float(cast(Any, chart_state.get("direction_probability") or 0.5))

    return CandleState(
        open=open_val,
        high=high_val,
        low=low_val,
        close=close_val,
        volume=volume_val,
        time_idx=0,
        direction=direction,
        confidence=confidence,
    )


def historical_candles_from_chart_state(
    chart_state: Mapping[str, Any], context_depth: int = 25
) -> list[CandleState]:
    """Extract historical context candles from chart_state."""
    candles: list[CandleState] = []

    recent_candles = chart_state.get("recent_candles", [])
    if isinstance(recent_candles, Sequence) and not isinstance(recent_candles, (str, bytes, bytearray)):
        for idx, c in enumerate(list(cast(Sequence[Any], recent_candles))[-context_depth:]):
            if not isinstance(c, Mapping):
                continue
            c_dict = cast(Mapping[str, Any], c)
            candles.append(
                CandleState(
                    open=float(cast(Any, c_dict.get("o") or 0.0)),
                    high=float(cast(Any, c_dict.get("h") or 0.0)),
                    low=float(cast(Any, c_dict.get("l") or 0.0)),
                    close=float(cast(Any, c_dict.get("c") or 0.0)),
                    volume=float(cast(Any, c_dict.get("v") or 1.0)),
                    time_idx=idx,
                    direction=str(c_dict.get("dir") or "HOLD").upper(),
                    confidence=float(cast(Any, c_dict.get("conf") or 0.5)),
                )
            )


    return candles

def memory_stats_to_bias(memory_recall: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract memory bank statistics for scenario bias."""
    if not memory_recall:
        return {}

    memory_align: Any = memory_recall.get("memory_alignment", 0.5)
    memory_labels_raw: Any = memory_recall.get("memory_labels", [])
    memory_labels: Sequence[Any] = (
        cast(Sequence[Any], memory_labels_raw)
        if isinstance(memory_labels_raw, Sequence) and not isinstance(memory_labels_raw, (str, bytes, bytearray))
        else ()
    )
    label_counts: dict[Any, int] = {}

    for label in memory_labels:
        count: int = label_counts.get(label, 0)
        label_counts[label] = count + 1

    buy_count: int = (label_counts.get("BUY", 0) or 0) + (label_counts.get("buy", 0) or 0)
    sell_count: int = (label_counts.get("SELL", 0) or 0) + (label_counts.get("sell", 0) or 0)
    total: int = buy_count + sell_count + 1

    return {
        "memory_alignment": float(memory_align),
        "buy_frequency": buy_count / total,
        "sell_frequency": sell_count / total,
        "total_samples": int(total),
    }


def forecast_to_scenario_input(
    forecast_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert regression Forecast3MOutput to A* scenario input."""
    return {
        "q05": float(forecast_output.get("q05", 0.0) or 0.0),
        "q50": float(forecast_output.get("q50", 0.0) or 0.0),
        "q95": float(forecast_output.get("q95", 0.0) or 0.0),
        "poly_slope": float(forecast_output.get("poly_slope", 0.0) or 0.0),
        "path_confidence": float(forecast_output.get("path_confidence", 0.5) or 0.5),
        "continue_prob": float(forecast_output.get("continue_prob", 0.5) or 0.5),
        "pullback_prob": float(forecast_output.get("pullback_prob", 0.25) or 0.25),
        "reversal_attempt_prob": float(
            forecast_output.get("reversal_attempt_prob", 0.15) or 0.15
        ),
        "fakeout_prob": float(forecast_output.get("fakeout_prob", 0.10) or 0.10),
        "structure_trade_ready": float(
            forecast_output.get("structure_trade_ready", 0.0) or 0.0
        ),
        "volume": float(forecast_output.get("volume", 1.0) or 1.0),
        "atr": float(forecast_output.get("interval", 0.01) or 0.01),
    }


def predict_scenarios_from_chart_and_forecast(
    chart_state: Mapping[str, Any],
    forecast_output: Mapping[str, Any],
    memory_recall: Mapping[str, Any] | None = None,
    num_scenarios: int = 5,
    max_depth: int = 5,
) -> list[ScenarioPrediction]:
    """
    Main entry point: generate scenario predictions from chart analysis + regression.

    Args:
        chart_state: Current chart analysis state
        forecast_output: Regression forecaster output (Forecast3MOutput dict)
        memory_recall: Memory bank recall (with pattern biases)
        num_scenarios: Number of top scenarios to return
        max_depth: How many candles ahead to predict

    Returns:
        Ranked list of ScenarioPrediction with paint annotations.
    """
    # Extract components
    last_candle = chart_state_to_candle(chart_state)
    historical = historical_candles_from_chart_state(chart_state, context_depth=25)
    memory_bias = memory_stats_to_bias(memory_recall)
    forecast_input = forecast_to_scenario_input(forecast_output)

    # Run A* prediction
    predictor = A_StarScenarioPredictor(
        max_depth=max_depth,
        max_scenarios=num_scenarios,
        expand_factor=3,
    )

    scenarios = predictor.predict_scenarios(
        last_candle=last_candle,
        historical_context=historical,
        forecast_data=forecast_input,
        memory_bias=memory_bias,
        transition_probs={
            "continue": forecast_input["continue_prob"],
            "pullback": forecast_input["pullback_prob"],
            "reversal_attempt": forecast_input["reversal_attempt_prob"],
            "fakeout": forecast_input["fakeout_prob"],
        },
        max_depth=max_depth,
    )

    return scenarios


def rank_scenarios_by_ensemble_agreement(
    scenarios: Sequence[ScenarioPrediction],
    ensemble_decision: str,
    ensemble_confidence: float,
) -> list[ScenarioPrediction]:
    """
    Re-rank scenarios by how well they align with ensemble decision.

    Args:
        scenarios: List of predictions
        ensemble_decision: "BUY", "SELL", or "HOLD"
        ensemble_confidence: How confident the ensemble is (0-1)

    Returns:
        Re-ranked scenarios (most aligned first).
    """
    if not scenarios:
        return list(scenarios)

    scored: list[tuple[float, ScenarioPrediction]] = []
    for scenario in scenarios:
        last_candle = scenario.scenario.last_candle()
        if last_candle is None:
            continue
        alignment_score = 0.0

        # Match direction
        if last_candle.direction == ensemble_decision:
            alignment_score += 0.7 * ensemble_confidence
        else:
            alignment_score -= 0.3

        # Boost by memory alignment
        alignment_score += 0.3 * scenario.scenario.memory_alignment

        # Include original probability
        alignment_score += 0.1 * scenario.probability

        scored.append((alignment_score, scenario))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [s[1] for s in scored]


def scenarios_to_paint_layer(
    scenarios: Sequence[ScenarioPrediction],
    chart_state: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Convert scenario predictions to paint/visualization layer.

    Returns dict with:
      - "scenarios": list of paint dicts
      - "confidence_heatmap": confidence levels across steps
      - "tree_structure": scenario branching structure
      - "top_ranked": best scenario details
    """
    if not scenarios:
        return {"scenarios": [], "confidence_heatmap": [], "tree_structure": {}}

    paint_scenarios = [s.to_paint_dict() for s in scenarios]

    # Build confidence heatmap (matrix of [depth x scenario])
    max_depth = max((len(s.projected_candles) for s in scenarios), default=1)
    heatmap: list[list[float]] = []
    for depth in range(max_depth):
        row: list[float] = []
        for scenario in scenarios:
            if depth < len(scenario.projected_candles):
                conf = scenario.projected_candles[depth].confidence
            else:
                conf = 0.0
            row.append(conf)

        heatmap.append(row)
    # Build tree structure (which scenarios share common ancestors)
    tree: dict[str, Any] = {
        "branches": len(scenarios),
        "max_depth": max_depth,
        "scenarios": [
            {
                "rank": s.rank,
                "probability": s.probability,
                "transition_type": s.scenario.transition_type.value,
                "steps": len(s.projected_candles),
            }
            for s in scenarios
        ],
    }

    top_ranked = scenarios[0].to_paint_dict() if scenarios else {}

    return {
        "scenarios": paint_scenarios,
        "confidence_heatmap": heatmap,
        "tree_structure": tree,
        "top_ranked": top_ranked,
        "summary": f"{len(scenarios)} scenarios explored, {max_depth} steps ahead",
    }


def enhanced_forecast_with_scenarios(
    forecast_output: Mapping[str, Any],
    chart_state: Mapping[str, Any],
    memory_recall: Mapping[str, Any] | None = None,
    ensemble_decision: str = "HOLD",
    ensemble_confidence: float = 0.5,
) -> dict[str, Any]:
    """
    Enhance a standard forecast with multi-step scenario predictions.

    Wraps the forecast output with scenario data for unified decision layer.

    Args:
        forecast_output: Standard regression forecast
        chart_state: Chart analysis state
        memory_recall: Memory bank statistics
        ensemble_decision: Ensemble consensus (BUY/SELL/HOLD)
        ensemble_confidence: Ensemble confidence (0-1)

    Returns:
        Enhanced forecast dict with "scenarios" key added.
    """
    # Generate scenarios
    scenarios = predict_scenarios_from_chart_and_forecast(
        chart_state=chart_state,
        forecast_output=forecast_output,
        memory_recall=memory_recall,
        num_scenarios=5,
        max_depth=5,
    )

    # Rank by ensemble agreement
    ranked = rank_scenarios_by_ensemble_agreement(
        scenarios, ensemble_decision, ensemble_confidence
    )

    # Convert to paint layer
    paint_layer = scenarios_to_paint_layer(ranked, chart_state)

    # Return enhanced forecast
    enhanced = dict(forecast_output)  # Preserve original fields
    enhanced["scenarios"] = paint_layer
    enhanced["scenarios_raw"] = [s.to_paint_dict() for s in ranked]
    top_last = ranked[0].scenario.last_candle() if ranked else None
    enhanced["scenario_summary"] = {
        "total_scenarios": len(ranked),
        "top_scenario_probability": ranked[0].probability if ranked else 0.0,
        "top_scenario_direction": top_last.direction if top_last is not None else "HOLD",
        "ensemble_agreement": ensemble_decision,
    }

    return enhanced


def scenario_explanation_for_ui(scenario: ScenarioPrediction) -> str:
    """
    Generate detailed explanation of a scenario for UI display.

    Returns markdown-formatted string.
    """
    last = scenario.scenario.last_candle()
    if last is None:
        return f"### Scenario #{scenario.rank}\n\nNo candle sequence is available for this scenario."
    first = scenario.scenario.candle_sequence[0]
    candle_count = len(scenario.scenario.candle_sequence)

    lines = [
        f"### Scenario #{scenario.rank}",
        f"**Probability:** {scenario.probability:.1%}",
        f"**Direction:** {last.direction}",
        f"**Confidence:** {last.confidence:.1%}",
        f"**Steps:** {candle_count} candles",
        "",
        f"**Transition Type:** {scenario.scenario.transition_type.value.title()}",
        f"**Memory Alignment:** {scenario.scenario.memory_alignment:.1%}",
        f"**Path Cost:** {scenario.scenario.cost:.3f}",
        "",
        f"**Price Range:**",
        f"- Open: {first.open:.4f}",
        f"- Latest Close: {last.close:.4f}",
        f"- High: {last.high:.4f}",
        f"- Low: {last.low:.4f}",
        "",
        f"**Summary:** {scenario.summary}",
    ]

    return "\n".join(lines)
