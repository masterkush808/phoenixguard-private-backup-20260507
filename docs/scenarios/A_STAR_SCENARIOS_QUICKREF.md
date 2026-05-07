# PhoenixGuard A* Scenario Prediction — Quick Reference

## Files Created

```text

phoenixguard/decision/
├── a_star_scenarios.py              # A* engine + candle generation
├── scenario_integration.py           # Regression/ensemble bridge
├── scenario_paint.py                 # Visualization/Plotly
└── scenario_decision_kernel.py       # Orchestration + integration

tests/
└── test_a_star_scenarios.py          # 4 demos + test suite

docs/
├── A_STAR_SCENARIOS_GUIDE.md         # Full technical guide
├── SCENARIO_INTEGRATION_EXAMPLES.md  # Integration pseudocode
└── A_STAR_SCENARIOS_DELIVERY_SUMMARY.md

```

## API Cheat Sheet

### Entry Points (Use These)

```python

## 1. Predict scenarios

from phoenixguard.decision.scenario_integration import predict_scenarios_from_chart_and_forecast

scenarios = predict_scenarios_from_chart_and_forecast(
    chart_state=chart,
    forecast_output=forecast,
    memory_recall=memory_stats,
    num_scenarios=5,
    max_depth=5,
)

## 2. Enhance existing forecast

from phoenixguard.decision.scenario_decision_kernel import enhanced_forecast_with_scenarios

enhanced = enhanced_forecast_with_scenarios(
    forecast_output=forecast,
    chart_state=chart,
    ensemble_decision="BUY",
    ensemble_confidence=0.65,
)

## 3. Full decision pipeline

from phoenixguard.decision.scenario_decision_kernel import decision_kernel_extension

result = decision_kernel_extension(
    chart_state=chart,
    forecast_output=forecast,
    ensemble_consensus="BUY",
    ensemble_confidence=0.65,
    memory_recall=memory_stats,
    skill_gates=existing_gates,
)

## 4. Visualize

from phoenixguard.decision.scenario_paint import create_scenario_dashboard_layout

dashboard = create_scenario_dashboard_layout(scenarios_paint_data)

```

## Key Classes

```python

## CandleState — single OHLCV candle

CandleState(open=1.05, high=1.052, low=1.049, close=1.051, confidence=0.75)

## ScenarioPrediction — top-ranked scenario

scenario.rank               # 1-5
scenario.probability        # 0.0-1.0
scenario.projected_candles  # list[CandleState]
scenario.to_paint_dict()    # → visualization format

## TransitionType — market behavior

TransitionType.CONTINUE            # Trend continues
TransitionType.PULLBACK            # Pull back to support
TransitionType.REVERSAL_ATTEMPT    # Try to reverse
TransitionType.FAKEOUT             # Quick reversal

## A_StarScenarioPredictor — main engine

predictor = A_StarScenarioPredictor(max_depth=5, max_scenarios=8, expand_factor=3)
scenarios = predictor.predict_scenarios(...)

```

## Configuration

```python

## Conservative (fast)

A_StarScenarioPredictor(max_depth=3, expand_factor=2, max_scenarios=5)

## Balanced (default)

A_StarScenarioPredictor(max_depth=5, expand_factor=3, max_scenarios=8)

## Thorough (slow)

A_StarScenarioPredictor(max_depth=8, expand_factor=4, max_scenarios=12)

## Time: 50ms → 100-150ms → 300-500ms

```

## Decision Output Structure

```python

result = {
    "action": "BUY|SELL|HOLD",
    "confidence": 0.72,  # may be boosted by scenarios
    "reason": "Scenarios support action with high quality",
    "skill_gates": {
        "scenario_agreement": True,     # NEW
        "scenario_confidence": True,    # NEW
        "scenario_quality": True,       # NEW
        # ... existing gates ...
    },
    "forecast": {
        "path_confidence": 0.72,
        "scenarios": {...},             # visualization data
        "scenarios_raw": [...],         # raw scenario dicts
        "top_scenario": {
            "rank": 1,
            "probability": 0.48,
            "direction": "BUY",
        },
    },
    "scenario_summary": {
        "total_paths_explored": 5,
        "top_scenario_probability": 0.48,
        "top_scenario_direction": "BUY",
        "consensus_boost": 0.075,
        "gates_passed": 3,
        "gates_total": 3,
    },
}

```

## Visualization Output

```python

dashboard = create_scenario_dashboard_layout(scenarios_paint_data)

dashboard.keys():
  "candlestick_traces"      # Plotly trace dicts (one per scenario)
  "line_traces"             # Close price paths
  "heatmap_trace"           # Confidence matrix
  "layout"                  # Plotly layout config

```

## Integration Patterns

### In Decision Loop

```python

## Replace: ensemble_result → decision

final = decision_kernel_extension(
    chart_state, forecast, ensemble.decision(), ensemble.confidence()
)

```

### In Gradio UI

```python

traces = extract_scenario_traces_for_gradio(final)
fig.add_trace(traces["candlestick_traces"])

```

### In Mobile API

```python

overlay = scenario_overlay_for_live_dashboard(final)
return {"scenarios": overlay, "gates": scenario_gates_as_metrics(final)}

```

## Troubleshooting

| Problem | Solution |
| -------- | -------- |
| Scenarios not generated | Check `forecast_output` has `q05`, `q50`, `q95` |
| Low confidence scenarios | Increase `path_confidence` in forecast, check memory bias |
| Slow generation (~500ms+) | Reduce `max_depth` to 3, reduce `expand_factor` to 2 |
| Import error | Ensure module files are in `phoenixguard/decision/` |
| Visualization blank | Check `scenarios_raw` list is not empty |

## Performance Targets

```text

Depth=3, Factor=2: ~50ms    (fast, shallow)
Depth=5, Factor=3: ~100-150ms (balanced, default)
Depth=8, Factor=4: ~300-500ms (thorough, long-term)

Memory: 5-10MB per tree
Throughput: 50+ predictions/min
Thread-safe: Yes

```

## Example: End-to-End

```python

## 1. Get inputs

chart_state = cv_module.analyze(screenshot)
forecast = regression_module.forecast_3m(chart_state)
memory_stats = memory_bank.recall_similar(chart_state)

## 2. Predict scenarios

scenarios = predict_scenarios_from_chart_and_forecast(
    chart_state, forecast, memory_stats
)

## 3. Full decision

result = decision_kernel_extension(
    chart_state, forecast, "BUY", 0.65, memory_stats
)

## 4. Visualize

dashboard = create_scenario_dashboard_layout(result["forecast"]["scenarios"])

## 5. Use

print(result["action"], result["confidence"])

```

## New Skill Gates

```python

skill_gates = {
    "scenario_agreement": bool,      # Top 3 scenarios agree
    "scenario_confidence": bool,     # Avg confidence ≥ 55%
    "scenario_quality": bool,        # Top scenario cost ≤ 3.0
}

```

## Key Metrics from Result

```python

result["scenario_summary"].keys():

  - total_paths_explored          # Number of scenarios
  - top_scenario_probability      # Best path probability
  - top_scenario_direction        # BUY/SELL/HOLD
  - consensus_boost               # Confidence delta
  - gates_passed                  # How many scenario gates passed
  - gates_total                   # Total scenario gates

```

## Confidence Boosting Logic

```text

if top_scenario_direction == ensemble_decision:
    confidence += 0.1 * top_scenario_probability   # Max +10%
else:
    confidence -= 0.1 * (1 - top_scenario_probability)  # Max -10%

```

## Export Scenarios

```python

from phoenixguard.decision.scenario_paint import export_scenarios_as_json, export_scenarios_as_csv

json_str = export_scenarios_as_json(scenarios_raw)
csv_str = export_scenarios_as_csv(scenarios_raw)

```

## Testing

```bash

python tests/test_a_star_scenarios.py    # Run all 4 demos

```

Demos:

1. Basic A* prediction
2. Integration with forecast
3. Full decision kernel
4. Export to JSON/CSV

## References

- **Guide**: `A_STAR_SCENARIOS_GUIDE.md`
- **Examples**: `SCENARIO_INTEGRATION_EXAMPLES.md`
- **Summary**: `A_STAR_SCENARIOS_DELIVERY_SUMMARY.md`
- **Code**: Docstrings in each module
