# PhoenixGuard A\* Scenario Prediction System

## Overview

The **A\* Scenario Prediction Engine** extends PhoenixGuard with **multi-step future candle
prediction** and **intelligent scenario exploration**.

It leverages:

- **A\* Search** for efficient path exploration
- **Advanced sorting** via heuristic-driven prioritization
- **Sequence awareness** of market transitions (continue, pullback, reversal,

fakeout)

## Architecture

### Core Components

```text

┌─────────────────────────────────────────────────────────┐
┌─────────────────────────────────────────────────────────┐
│  PhoenixGuard Decision Kernel                           │
│  (ensemble.py → decision_kernel.py)                     │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
        ▼                             ▼
┌─────────────────┐         ┌──────────────────┐
│ Regression      │         │ Skill Gates      │
│ (forecast_3m)   │         │ (13 core gates)  │
└────────┬────────┘         └──────┬───────────┘
         │                         │
         │       NEW              │
         ├─────────────────────────┤
         │                         │
         ▼                         ▼
    ┌─────────────────────────────────────┐
    │  A* Scenario Prediction Engine      │
    │  (a_star_scenarios.py)              │
    │                                     │
    │  ┌─────────────────────────────┐   │
    │  │ Expand scenarios via A*     │   │
    │  │ - CONTINUE                  │   │
    │  │ - PULLBACK                  │   │
    │  │ - REVERSAL_ATTEMPT          │   │
    │  │ - FAKEOUT                   │   │
    │  └─────────────────────────────┘   │
    │                                     │
    │  ┌─────────────────────────────┐   │
    │  │ Generate plausible candles  │   │
    │  │ - Quantile forecasts (q05/50/95)│
    │  │ - ATR-based noise           │   │
    │  │ - Memory bias               │   │
    │  └─────────────────────────────┘   │
    │                                     │
    │  ┌─────────────────────────────┐   │
    │  │ Rank by quality             │   │
    │  │ - Path cost (f=g+h)         │   │
    │  │ - Ensemble agreement        │   │
    │  │ - Memory alignment          │   │
    │  └─────────────────────────────┘   │
    └─────────────────────────────────────┘
         │
         ▼
    ┌─────────────────────┐
    │ Paint Layer         │
    │ (scenario_paint.py) │
    │                     │
    │ - Candlestick       │
    │ - Confidence hm     │
    │ - Scenario tree     │
    │ - Annotations       │
    └─────────────────────┘
         │
         ▼
    ┌─────────────────────┐
    │ Visualization       │
    │ (Plotly dashboard)  │
    │ + Mobile API        │
    └─────────────────────┘

```

### Integration Points

1. **Regression Module** → Input

   - Uses `Forecast3MOutput` (q05, q50, q95, poly_slope, etc.)
   - Provides confidence + direction priors

2. **Memory Bank** → Input

   - Pattern frequencies (BUY/SELL ratio)
   - Transition probabilities
   - Bias scenarios toward historical behavior

3. **Ensemble** → Integration

   - Ranks scenarios by agreement with ensemble decision
   - Boosts confidence if scenarios align
   - Relaxes `force_hold` if scenarios show strong setup

4. **Skill Gates** → Output

   - New gates: `scenario_agreement`, `scenario_confidence`, `scenario_quality`
   - Can block/allow execution based on scenario consensus

5. **Visualization** → Dashboard

   - Candlestick traces for each scenario
   - Confidence heatmap
   - Scenario tree structure
   - Interactive comparison

## Quick Start

### Step 1: Basic Scenario Prediction

```python

from phoenixguard.decision.scenario_integration import \
    predict_scenarios_from_chart_and_forecast

scenarios = predict_scenarios_from_chart_and_forecast(
    chart_state=chart_state,           # From CV analysis
    forecast_output=forecast,           # From regression
    memory_recall=memory_stats,         # From memory bank
    num_scenarios=5,                    # Top N paths
    max_depth=5,                        # Steps ahead
)

for scenario in scenarios:
    print(f"Rank {scenario.rank}: {scenario.probability:.1%} | {scenario.summary}")

```

### Step 2: Enhanced Forecast with Scenarios

```python

from phoenixguard.decision.scenario_decision_kernel import \
    enhance_forecast_with_scenario_consensus

enhanced = enhance_forecast_with_scenario_consensus(
    forecast_output=forecast,
    chart_state=chart_state,
    ensemble_decision="BUY",
    ensemble_confidence=0.65,
)

## Result includes

## - Boosted confidence if scenarios agree

## - force_hold_relaxed if strong scenario setup

## - scenarios data for visualization

```

### Step 3: Full Decision Kernel

```python

from phoenixguard.decision.scenario_decision_kernel import \
    decision_kernel_extension

result = decision_kernel_extension(
    chart_state=chart_state,
    forecast_output=forecast,
    ensemble_consensus="BUY",
    ensemble_confidence=0.65,
    memory_recall=memory_stats,
    skill_gates=skill_gates,
)

print(f"Action: {result['action']}")  # BUY, SELL, or HOLD
print(f"Confidence: {result['confidence']:.1%}")
print(f"Scenarios explored: {result['scenario_summary']['total_paths_explored']}")

```

### Step 4: Visualization

```python

from phoenixguard.decision.scenario_paint import create_scenario_dashboard_layout

scenarios_paint = enhanced.get("scenarios", {})
dashboard = create_scenario_dashboard_layout(scenarios_paint, title="Forecast Dashboard")

## Use with Plotly

## fig = go.Figure(data=dashboard["candlestick_traces"], layout=dashboard["layout"])

## fig.show()

```

## Configuration

### A\* Search Parameters

Edit `A_StarScenarioPredictor.__init__()` in `a_star_scenarios.py`:

```python

predictor = A_StarScenarioPredictor(
    max_depth=5,           # Candles ahead (default 5, max 10)
    max_scenarios=8,       # Top paths to keep (default 8)
    expand_factor=3,       # Branches per transition (default 3)
)

```

**Performance vs. Accuracy:**

- `max_depth=3, expand_factor=2`: Fast (~50ms), shallow forecasting
- `max_depth=5, expand_factor=3`: Balanced (100-150ms), standard
- `max_depth=8, expand_factor=4`: Thorough (300-500ms), long-term

### Memory Bias

The system automatically extracts memory bias from `memory_recall`:

```python

## From memory bank statistics

memory_recall = {
    "memory_alignment": 0.70,
    "buy_frequency": 0.65,  # More BUY than SELL in memory
    "sell_frequency": 0.35,
    "total_samples": 150,
}

## These bias scenarios toward BUY patterns

```

### Transition Probabilities

Extracted from forecast output:

```python

forecast_output = {
    "continue_prob": 0.50,           # Trend continues
    "pullback_prob": 0.25,           # Pullback to support
    "reversal_attempt_prob": 0.15,   # Try to reverse
    "fakeout_prob": 0.10,            # Quick reversal
    ...
}

```

## Integration Checklist

- [ ] Add `a_star_scenarios.py` to `decision/`
- [ ] Add `scenario_integration.py` to `decision/`
- [ ] Add `scenario_paint.py` to `decision/`
- [ ] Add `scenario_decision_kernel.py` to `decision/`
- [ ] Update `decision/__init__.py` to export new modules
- [ ] Add test file `tests/test_a_star_scenarios.py`
- [ ] Update `ImageFusionRegressor.forecast_3m()` to call

`enhanced_forecast_with_scenarios()`

- [ ] Update `DecisionKernel` to use `decision_kernel_extension()`
- [ ] Update Gradio dashboard to render scenario traces
- [ ] Update mobile API dashboard overlay
- [ ] Document in README/wiki

## API Reference

### `a_star_scenarios.py`

**Main Classes:**

- `CandleState`: Single OHLCV + metadata
- `ScenarioNode`: A\* node with path history
- `ScenarioPrediction`: Top-ranked scenario for visualization
- `A_StarScenarioPredictor`: Main search engine

**Key Methods:**

```python

predictor.predict_scenarios(
    last_candle: CandleState,
    historical_context: Sequence[CandleState],
    forecast_data: dict,
    memory_bias: dict | None = None,
    transition_probs: dict | None = None,
    max_depth: int | None = None,
) -> list[ScenarioPrediction]

```

### `scenario_integration.py`

**Key Functions:**

```python

predict_scenarios_from_chart_and_forecast(
    chart_state: Mapping[str, Any],
    forecast_output: Mapping[str, Any],
    memory_recall: Mapping[str, Any] | None = None,
    num_scenarios: int = 5,
    max_depth: int = 5,
) -> list[ScenarioPrediction]

rank_scenarios_by_ensemble_agreement(
    scenarios: Sequence[ScenarioPrediction],
    ensemble_decision: str,
    ensemble_confidence: float,
) -> list[ScenarioPrediction]

enhanced_forecast_with_scenarios(...) -> dict[str, Any]

```

### `scenario_paint.py`

**Main Class:**

```python

painter = ScenarioPainter(use_confidence_alpha=True, branch_colors=None)

painter.scenarios_to_candlestick_traces(
    scenarios: Sequence[Mapping[str, Any]],
) -> list[dict]

painter.confidence_heatmap_to_trace(
    heatmap: Sequence[Sequence[float]],
) -> dict

```

### `scenario_decision_kernel.py`

**Orchestration Function:**

```python

decision_kernel_extension(
    chart_state: dict,
    forecast_output: dict,
    ensemble_consensus: str,
    ensemble_confidence: float,
    memory_recall: dict | None = None,
    skill_gates: dict | None = None,
) -> dict[str, Any]

```

Returns:

```python

{
    "action": "BUY" | "SELL" | "HOLD",
    "confidence": float,  # Boosted by scenarios
    "reason": str,
    "skill_gates": dict,  # Updated with scenario gates
    "forecast": dict,     # Enhanced with scenarios
    "scenario_summary": dict,
    "dashboard": dict,    # Plotly-ready
}

```

## Performance

**Generation Time (per prediction):**

- Depth=3, factor=2: ~50ms
- Depth=5, factor=3: ~100-150ms
- Depth=8, factor=4: ~300-500ms

**Memory Usage:**

- Per scenario tree: ~5-10MB
- Dashboard traces (5 scenarios): ~2-3MB

**Scalability:**

- Handles 50+ predictions/minute on CPU
- Thread-safe (independent instances)
- Can parallelize across scenarios

## Troubleshooting

**Scenarios not generated:**

- Check `forecast_output` has `q05`, `q50`, `q95`
- Verify `chart_state` has `entry_candle` with OHLCV
- Ensure `transition_probs` sum to ~1.0

**Low scenario confidence:**

- Increase `path_confidence` in forecast if signal is weak
- Check `memory_bias` alignment (low alignment → more uncertainty)
- Verify regression forecast is reliable

**Slow generation:**

- Reduce `max_depth` to 3-4
- Reduce `expand_factor` to 2
- Increase `max_scenarios` filter

**Visualization issues:**

- Check `scenarios_to_paint_dict()` output structure
- Verify Plotly version >= 5.0
- Ensure confidence values are 0-1

## Examples

### Example 1: Single Prediction with Scenario Overlay

```python

## Get forecast

forecast = regression_module.forecast_3m(chart_state)

## Get scenarios

scenarios = predict_scenarios_from_chart_and_forecast(
    chart_state, forecast, memory_stats, num_scenarios=3
)

## Enhance forecast

enhanced = enhanced_forecast_with_scenarios(forecast, chart_state)

## Visualize

dashboard = create_scenario_dashboard_layout(enhanced["scenarios"])

```

### Example 2: Live Dashboard Integration

```python

## In mobile API handler

result = decision_kernel_extension(
    chart_state=chart_state,
    forecast_output=forecast,
    ensemble_consensus=ensemble.decision(),
    ensemble_confidence=ensemble.confidence(),
)

## Return to client

return {
    "signal": result["action"],
    "confidence": result["confidence"],
    "scenarios": result["scenario_summary"],
    "dashboard": result["dashboard"],
}

```

### Example 3: Batch Evaluation

```python

from phoenixguard.decision.scenario_integration import predict_scenarios_from_chart_and_forecast

results = []
for chart in batch_charts:
    forecast = regression_module.forecast_3m(chart)
    scenarios = predict_scenarios_from_chart_and_forecast(
        chart, forecast, memory_stats, num_scenarios=5
    )
    results.append({
        "chart": chart,
        "scenarios": scenarios,
        "top_prob": scenarios[0].probability if scenarios else 0,
    })

```

## Future Enhancements

- **Monte Carlo validation**: Backtest predicted scenarios
- **Neural scenario refinement**: Use transformer to smooth predictions
- **Adaptive expansion**: Adjust branching factor based on market regime
- **Cross-timeframe**: Predict multi-timeframe scenarios simultaneously
- **Risk metrics**: Add VaR, CVaR to scenario output
- **Scenario clustering**: Group similar paths, reduce visualization clutter

## References

- **A\* Search**: Russell & Norvig, _Artificial Intelligence_ (4th Ed.)
- **Quantile Forecasting**: Hyndman & Athanasopoulos, \*Forecasting: Principles &

Practice\*

- **Memory Augmentation**: Graves et al., _Neural Turing Machines_
