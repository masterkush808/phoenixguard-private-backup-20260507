"""

# PhoenixGuard A* Scenario Integration Example

------

This file demonstrates how to integrate the A* scenario prediction
system into the existing workstation's main decision pipeline.

## Usage

------

1. Import these utilities in your main decision kernel or skill gates
2. Call enhanced_decision_with_scenarios() in your pipeline
3. Render output via existing Plotly dashboard
4. Monitor scenario gates in skill gates panel

## Integration Points

------

- decision_kernel.py: Call enhanced_decision() instead of decision()
- regression_module.py: Wrap forecast_3m() output
- skill_gates.py: Add scenario_* gates
- Gradio UI: Add scenario traces to candlestick chart
- Mobile API: Add /scenarios endpoint

"""
from __future__ import annotations

from typing import Any, Mapping

from phoenixguard.decision.scenario_decision_kernel import (
    decision_kernel_extension,
    scenario_dashboard_for_forecast,
    scenario_gates_for_execution,
    scenario_overlay_for_live_dashboard,
)
from phoenixguard.decision.scenario_paint import (
    ScenarioPainter,
)

def enhanced_decision_pipeline(
    chart_state: dict[str, Any],
    forecast_output: dict[str, Any],
    ensemble_decision: str,
    ensemble_confidence: float,
    memory_recall: dict[str, Any] | None = None,
    skill_gates: dict[str, bool] | None = None,
    enable_scenarios: bool = True,
) -> dict[str, Any]:
    """
    Drop-in replacement for standard decision kernel.

    Wraps decision_kernel_extension to add scenario analysis.
    If scenarios are disabled, falls back to standard behavior.

    Args:
        chart_state: Current chart analysis
        forecast_output: Regression forecast
        ensemble_decision: Consensus direction
        ensemble_confidence: Confidence level
        memory_recall: Memory bank statistics
        skill_gates: Current gates
        enable_scenarios: Toggle scenario analysis

    Returns:
        Enhanced decision output with scenarios
    """
    if not enable_scenarios:
        # Fallback to standard behavior (no scenarios)
        return {
            "action": ensemble_decision,
            "confidence": ensemble_confidence,
            "reason": "Standard pipeline (scenarios disabled)",
            "skill_gates": skill_gates or {},
            "forecast": forecast_output,
        }

    try:
        return decision_kernel_extension(
            chart_state=chart_state,
            forecast_output=forecast_output,
            ensemble_consensus=ensemble_decision,
            ensemble_confidence=ensemble_confidence,
            memory_recall=memory_recall,
            skill_gates=skill_gates,
        )
    except Exception as e:
        # If scenarios fail, gracefully degrade
        print(f"Warning: Scenario generation failed ({e}), using standard
        decision")
        return {
            "action": ensemble_decision,
            "confidence": ensemble_confidence,
            "reason": f"Scenario error, using ensemble: {str(e)[:50]}",
            "skill_gates": skill_gates or {},
            "forecast": forecast_output,
        }

def extract_scenario_traces_for_gradio(
    decision_output: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Extract scenario traces for Plotly candlestick chart in Gradio UI.

    Converts decision kernel output to Plotly trace objects.

    Args:
        decision_output: From enhanced_decision_pipeline()

    Returns:
        Dict with candlestick_traces, line_traces, layout
    """
    dashboard = decision_output.get("dashboard", {})
    if not dashboard:
        return {}

    return {
        "candlestick_traces": dashboard.get("candlestick_traces", []),
        "line_traces": dashboard.get("line_traces", []),
        "heatmap_trace": dashboard.get("heatmap_trace", {}),
        "layout": dashboard.get("layout", {}),
    }

def format_scenario_summary_for_ui(
    decision_output: Mapping[str, Any],
) -> str:
    """
    Format scenario summary as readable text for UI display.

    Args:
        decision_output: From enhanced_decision_pipeline()

    Returns:
        Markdown-formatted string
    """
    scenario_summary = decision_output.get("scenario_summary", {})
    if not scenario_summary:
        return "No scenario data"

    lines = [
        "## Scenario Analysis",
        f"- **Total paths explored:**
        {scenario_summary.get('total_paths_explored', 0)}",
        f"- **Top scenario probability:**
        {scenario_summary.get('top_scenario_probability', 0):.1%}",
        f"- **Top scenario direction:**
        {scenario_summary.get('top_scenario_direction', 'HOLD')}",
        f"- **Confidence boost:** {scenario_summary.get('consensus_boost',
        0):.1%}",
        f"- **Gates passed:** {scenario_summary.get('gates_passed',
        0)}/{scenario_summary.get('gates_total', 0)}",
    ]

    return "\n".join(lines)

def scenario_gates_as_metrics(
    decision_output: Mapping[str, Any],
) -> dict[str, float]:
    """
    Convert scenario gates to numeric metrics for dashboard.

    Args:
        decision_output: From enhanced_decision_pipeline()

    Returns:
        Dict of gate_name -> binary (1.0 or 0.0)
    """
    skill_gates = decision_output.get("skill_gates", {})

    scenario_gates = {
        "scenario_agreement": 1.0 if skill_gates.get("scenario_agreement") else
        0.0,
        "scenario_confidence": 1.0 if skill_gates.get("scenario_confidence")
        else 0.0,
        "scenario_quality": 1.0 if skill_gates.get("scenario_quality") else 0.0,
    }

    return scenario_gates

def add_scenario_layer_to_existing_chart(
    fig,
    decision_output: Mapping[str, Any],
    visibility: bool = True,
):
    """
    Add scenario traces to an existing Plotly figure.

    Call this after creating the base candlestick chart.

    Args:
        fig: Plotly figure object
        decision_output: From enhanced_decision_pipeline()
        visibility: Whether to show scenarios by default

    Returns:
        Modified figure
    """
    traces = extract_scenario_traces_for_gradio(decision_output)

    if not traces:
        return fig

    # Add candlestick traces
    for trace in traces.get("candlestick_traces", []):
        trace_obj = trace.copy()
        trace_obj["visible"] = visibility
        fig.add_trace(trace_obj)

    # Add line traces
    for trace in traces.get("line_traces", []):
        trace_obj = trace.copy()
        trace_obj["visible"] = False  # Hidden by default
        fig.add_trace(trace_obj)

    # Add confidence heatmap (separate subplot)
    if traces.get("heatmap_trace"):
        fig.add_trace(traces["heatmap_trace"])

    return fig

def scenario_gates_to_skill_gates_dict(
    decision_output: Mapping[str, Any],
) -> dict[str, bool]:
    """
    Extract scenario gates for integration with skill gates panel.

    Args:
        decision_output: From enhanced_decision_pipeline()

    Returns:
        Dict of gate_name -> bool for skill gates UI
    """
    skill_gates = decision_output.get("skill_gates", {})

    return {
        "scenario_agreement": skill_gates.get("scenario_agreement", False),
        "scenario_confidence": skill_gates.get("scenario_confidence", False),
        "scenario_quality": skill_gates.get("scenario_quality", False),
    }

def create_scenario_feed_item(
    decision_output: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Create a feed item for the visual labeling + learning workflow.

    Useful for capturing scenario-informed analysis for replay learning.

    Args:
        decision_output: From enhanced_decision_pipeline()

    Returns:
        Feed item dict
    """
    top_scenario = decision_output.get("forecast", {}).get("top_scenario", {})

    return {
        "type": "scenario_forecast",
        "action": decision_output.get("action", "HOLD"),
        "confidence": decision_output.get("confidence", 0.5),
        "reason": decision_output.get("reason", ""),
        "top_scenario_direction": top_scenario.get("direction", "HOLD"),
        "top_scenario_probability": top_scenario.get("probability", 0.0),
        "scenario_count": decision_output.get("scenario_summary", {}).get(
            "total_paths_explored", 0
        ),
        "gates_passed": decision_output.get("scenario_summary",
        {}).get("gates_passed", 0),
        "tags": ["scenario", "multi_path", "a_star"],
    }

## ============================================================================

## EXAMPLE: Integration into main.py or existing decision kernel

## ============================================================================

def example_integration_in_decision_loop():
    """
    Shows how to use enhanced_decision_pipeline() in a training loop.

    Pseudocode for your main.py or decision kernel:
    """

    # Pseudo-imports (would be real in actual code)
    # from phoenixguard.decision import regression_module, ensemble, memory

    pseudocode = """
    # In your main decision loop (e.g., handle_analysis_request):

    # 1. Get chart analysis from CV
    chart_state = run_cv_analysis(screenshot)

    # 2. Get forecast from regression
    forecast = regression_module.ImageFusionRegressor(logger).forecast_3m(
        chart_state, detections=cv_detections
    )

    # 3. Get memory recall
    memory_recall = memory_bank.recall_similar(chart_state)

    # 4. Get ensemble decision
    ensemble_result = ensemble.DecisionKernel().decision(
        chart_state=chart_state,
        forecast=forecast,
        memory_recall=memory_recall,
    )

    # 5. ADD NEW STEP: Enhance with scenarios
    final_decision = enhanced_decision_pipeline(
        chart_state=chart_state,
        forecast_output=forecast,
        ensemble_decision=ensemble_result["action"],
        ensemble_confidence=ensemble_result["confidence"],
        memory_recall=memory_recall,
        skill_gates=ensemble_result["skill_gates"],
        enable_scenarios=True,  # Toggle with config
    )

    # 6. Use final decision
    action = final_decision["action"]
    confidence = final_decision["confidence"]
    scenario_summary = final_decision["scenario_summary"]

    # 7. Render in UI
    traces = extract_scenario_traces_for_gradio(final_decision)
    fig.add_trace(traces["candlestick_traces"])  # Add to Plotly

    # 8. Log for learning
    feed_item = create_scenario_feed_item(final_decision)
    feed.append(feed_item)
    """

    print(pseudocode)

def example_mobile_api_endpoint():
    """
    Shows how to add a /scenarios endpoint to mobile API.

    Pseudocode for your mobile_api.py:
    """

    pseudocode = """
    # In your Fastapi app (mobile_api.py):

    @app.post("/v1/mobile/observer/sessions/{sessionId}/scenarios")
    async def get_session_scenarios(sessionId: str) -> dict:
        '''
        Return top scenarios for current session image.
        '''
        session = observer_sessions[sessionId]
        chart_state = session.latest_chart_state
        forecast = session.latest_forecast

        decision = enhanced_decision_pipeline(
            chart_state=chart_state,
            forecast_output=forecast,
            ensemble_decision=session.latest_action,
            ensemble_confidence=session.latest_confidence,
            enable_scenarios=True,
        )

        overlay = scenario_overlay_for_live_dashboard(decision)

        return {
            "sessionId": sessionId,
            "scenarios": overlay,
            "top_scenario": decision["forecast"]["top_scenario"],
            "gates": scenario_gates_as_metrics(decision),
        }

    @app.get("/v1/mobile/observer/sessions/{sessionId}/scenarios/export")
    async def export_scenarios(sessionId: str, format: str = "json") -> str:
        '''
        Export scenarios as JSON or CSV.
        '''
        session = observer_sessions[sessionId]
        decision = session.latest_decision

        if format == "csv":
            from phoenixguard.decision.scenario_paint import
            export_scenarios_as_csv
            return
            export_scenarios_as_csv(decision["forecast"]["scenarios_raw"])
        else:
            from phoenixguard.decision.scenario_paint import
            export_scenarios_as_json
            return
            export_scenarios_as_json(decision["forecast"]["scenarios_raw"])
    """

    print(pseudocode)

def example_gradio_ui_integration():
    """
    Shows how to add scenario visualization to Gradio dashboard.

    Pseudocode for your Gradio app:
    """

    pseudocode = """
    # In your Gradio app (main dashboard):

    import gradio as gr
    import plotly.graph_objects as go

    with gr.Blocks() as demo:
        # Existing chart
        chart = gr.Plot(label="Main Chart")

        # NEW: Scenario controls
        with gr.Row():
            show_scenarios = gr.Checkbox(label="Show Scenarios", value=True)
            scenario_depth = gr.Slider(minimum=3, maximum=8, value=5,
            label="Forecast Depth")

        # NEW: Scenario summary
        scenario_text = gr.Markdown(label="Scenario Analysis")

        # NEW: Scenario gates
        with gr.Row():
            gate_agreement = gr.Checkbox(label="Scenario Agreement",
            interactive=False)
            gate_confidence = gr.Checkbox(label="Scenario Confidence",
            interactive=False)
            gate_quality = gr.Checkbox(label="Scenario Quality",
            interactive=False)

        def update_chart_with_scenarios(latest_screenshot):
            # Analyze
            chart_state = cv_module.analyze(latest_screenshot)
            forecast = regression_module.forecast_3m(chart_state)
            decision = enhanced_decision_pipeline(
                chart_state=chart_state,
                forecast_output=forecast,
                ensemble_decision=ensemble.get_decision(),
                ensemble_confidence=ensemble.get_confidence(),
                enable_scenarios=show_scenarios.value,
            )

            # Create figure
            fig = go.Figure()
            fig = add_scenario_layer_to_existing_chart(
                fig, decision, visibility=show_scenarios.value
            )

            # Update gates
            gates = scenario_gates_as_metrics(decision)

            return (
                fig,  # chart
                format_scenario_summary_for_ui(decision),  # scenario_text
                gates["scenario_agreement"],  # gate_agreement
                gates["scenario_confidence"],  # gate_confidence
                gates["scenario_quality"],  # gate_quality
            )

        # Wire up
        latest_screenshot.change(
            fn=update_chart_with_scenarios,
            inputs=[latest_screenshot],
            outputs=[
                chart,
                scenario_text,
                gate_agreement,
                gate_confidence,
                gate_quality,
            ],
        )
    """

    print(pseudocode)

    if __name__ == "__main__":
        print("PhoenixGuard A* Scenario Integration Examples\n")
        print("=" * 60)

        print("\n1. Decision Pipeline Integration:")
        example_integration_in_decision_loop()

        print("\n2. Mobile API Endpoint:")
        example_mobile_api_endpoint()

        print("\n3. Gradio UI Integration:")
        example_gradio_ui_integration()

        print("\n" + "=" * 60)
        print(
            "See A_STAR_SCENARIOS_GUIDE.md for full documentation and API
            reference."
        )
