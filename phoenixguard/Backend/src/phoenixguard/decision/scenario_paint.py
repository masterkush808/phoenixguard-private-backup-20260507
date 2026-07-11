"""
PhoenixGuard Scenario Paint Layer
==================================
Renders A* predicted scenarios as visual candles and annotations on Plotly charts.

Features:
  - Paint future candles with confidence gradients
  - Show scenario branching tree
  - Annotate transition types and probabilities
  - Create interactive scenario comparison
  - Export scenario data for UI overlay
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence, cast

import numpy as np


class ScenarioPainter:
    """Converts scenario predictions to Plotly trace data for charting."""

    def __init__(self, use_confidence_alpha: bool = True, branch_colors: list[str] | None = None):
        self.use_confidence_alpha = use_confidence_alpha
        self.branch_colors = branch_colors or [
            "#FF6B6B",  # Red
            "#4ECDC4",  # Teal
            "#45B7D1",  # Blue
            "#FFA07A",  # Light Salmon
            "#98D8C8",  # Mint
            "#F7DC6F",  # Yellow
            "#BB8FCE",  # Purple
            "#85C1E2",  # Light Blue
        ]

    def scenarios_to_candlestick_traces(
        self,
        scenarios: Sequence[Any],
        scenario_names: list[str] | None = None,
        opacity_mode: str = "confidence",
    ) -> list[dict[str, Any]]:
        """
        Convert scenario paint dicts to Plotly candlestick traces.

        Args:
            scenarios: List of paint dicts (from ScenarioPrediction.to_paint_dict())
            scenario_names: Optional custom names for each scenario
            opacity_mode: "confidence" or "rank" for alpha transparency

        Returns:
            List of dicts compatible with plotly go.Candlestick()
        """
        traces: list[dict[str, Any]] = []

        for idx, scenario in enumerate(scenarios):
            if not isinstance(scenario, Mapping):
                continue
            scenario_map = cast(Mapping[str, Any], scenario)

            rank = int(float(scenario_map.get("rank", idx + 1) or idx + 1))
            candles_data = scenario_map.get("candles", [])

            if not candles_data:
                continue
            candles = [
                cast(Mapping[str, Any], item)
                for item in cast(Sequence[Any], candles_data)
                if isinstance(item, Mapping)
            ]
            if not candles:
                continue

            # Extract OHLCV
            times = list(range(len(candles)))
            opens = [float(c.get("o", 0.0)) for c in candles]
            highs = [float(c.get("h", 0.0)) for c in candles]
            lows = [float(c.get("l", 0.0)) for c in candles]
            closes = [float(c.get("c", 0.0)) for c in candles]
            confidences = [float(c.get("confidence", 0.5)) for c in candles]

            # Determine opacity
            if opacity_mode == "confidence":
                opacity = float(np.mean(confidences))
            elif opacity_mode == "rank":
                opacity = max(0.3, 1.0 - (rank / 10.0))
            else:
                opacity = 0.7

            # Color
            color = self.branch_colors[idx % len(self.branch_colors)]

            # Name
            name = scenario_names[idx] if scenario_names and idx < len(scenario_names) else f"Scenario {rank}"

            trace: dict[str, Any] = {
                "x": times,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "name": name,
                "type": "candlestick",
                "increasing": {"fillcolor": color, "line": {"color": color}},
                "decreasing": {"fillcolor": color, "line": {"color": color}},
                "opacity": opacity,
                "hovertext": [
                    f"<b>{name}</b><br>"
                    f"Time: {t}<br>"
                    f"O: {open_price:.4f} | H: {high:.4f} | L: {low:.4f} | C: {close:.4f}<br>"
                    f"Confidence: {conf:.1%}"
                    for t, open_price, high, low, close, conf in zip(times, opens, highs, lows, closes, confidences)
                ],
                "hoverinfo": "text",
                "visible": (rank <= 3),  # Show top 3 by default
            }

            traces.append(trace)

        return traces

    def scenarios_to_line_traces(
        self,
        scenarios: Sequence[Any],
        line_mode: str = "close",
    ) -> list[dict[str, Any]]:
        """
        Convert scenarios to line traces (close price path).

        Useful for comparing scenario trajectories without candle detail.

        Args:
            scenarios: Paint dicts
            line_mode: "close" | "mid" (high+low)/2

        Returns:
            List of line trace dicts
        """
        traces: list[dict[str, Any]] = []

        for idx, scenario in enumerate(scenarios):
            if not isinstance(scenario, Mapping):
                continue
            scenario_map = cast(Mapping[str, Any], scenario)

            rank = int(float(scenario_map.get("rank", idx + 1) or idx + 1))
            candles_data = scenario_map.get("candles", [])
            summary = str(scenario_map.get("summary", ""))

            if not candles_data:
                continue
            candles = [
                cast(Mapping[str, Any], item)
                for item in cast(Sequence[Any], candles_data)
                if isinstance(item, Mapping)
            ]
            if not candles:
                continue

            times = list(range(len(candles)))
            if line_mode == "mid":
                values = [
                    (float(c.get("h", 0.0)) + float(c.get("l", 0.0))) / 2.0
                    for c in candles
                ]
            else:
                values = [float(c.get("c", 0.0)) for c in candles]

            color = self.branch_colors[idx % len(self.branch_colors)]

            trace: dict[str, Any] = {
                "x": times,
                "y": values,
                "mode": "lines+markers",
                "name": f"Scenario {rank}",
                "line": {"color": color, "width": 2},
                "marker": {"size": 6},
                "hovertext": [f"{summary}<br>Value: {v:.4f}" for v in values],
                "hoverinfo": "text",
                "visible": (rank <= 3),
            }

            traces.append(trace)

        return traces

    def confidence_heatmap_to_trace(
        self,
        heatmap: Sequence[Sequence[float]],
        scenario_count: int,
    ) -> dict[str, Any]:
        """
        Convert confidence heatmap to Plotly heatmap trace.

        Heatmap is [depth x scenario_idx] where each cell is confidence (0-1).

        Args:
            heatmap: 2D list of confidence values
            scenario_count: Number of scenarios

        Returns:
            Single heatmap trace dict
        """
        if not heatmap:
            return {}

        z = heatmap
        x_labels = [f"S{i+1}" for i in range(scenario_count)]
        y_labels = [f"Step {i}" for i in range(len(heatmap))]

        return {
            "z": z,
            "x": x_labels,
            "y": y_labels,
            "type": "heatmap",
            "colorscale": "RdYlGn",
            "name": "Confidence",
            "hovertemplate": "Scenario: %{x}<br>Step: %{y}<br>Confidence: %{z:.1%}<extra></extra>",
        }

    def scenario_tree_structure_to_annotations(
        self,
        tree_structure: Mapping[str, Any],
        base_x: float = 0.0,
        base_y: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        Convert scenario tree structure to Plotly annotations.

        Creates visual branching diagram showing scenario relationships.

        Args:
            tree_structure: From scenario paint layer
            base_x: Starting x position
            base_y: Starting y position

        Returns:
            List of annotation dicts (lines and text)
        """
        annotations: list[dict[str, Any]] = []
        branches = int(float(tree_structure.get("branches", 1) or 1))
        max_depth = int(float(tree_structure.get("max_depth", 1) or 1))
        scenarios_raw = tree_structure.get("scenarios", [])
        scenarios = [
            cast(Mapping[str, Any], item)
            for item in cast(Sequence[Any], scenarios_raw)
            if isinstance(item, Mapping)
        ]

        # Spacing
        x_spacing = 10.0 / max(branches, 1)
        y_spacing = 10.0 / max(max_depth, 1)

        for scenario_idx, scenario_info in enumerate(scenarios):
            rank = int(float(scenario_info.get("rank", scenario_idx + 1) or scenario_idx + 1))
            probability = float(scenario_info.get("probability", 0.5) or 0.5)
            steps = int(float(scenario_info.get("steps", 1) or 1))
            transition = str(scenario_info.get("transition_type", "unknown"))

            x_pos = base_x + (scenario_idx * x_spacing)
            y_pos = base_y + (steps * y_spacing)

            # Node annotation
            annotations.append(
                {
                    "x": x_pos,
                    "y": y_pos,
                    "text": f"<b>#{rank}</b><br>{probability:.0%}",
                    "showarrow": False,
                    "bgcolor": self.branch_colors[scenario_idx % len(self.branch_colors)],
                    "bordercolor": "black",
                    "borderwidth": 1,
                    "xanchor": "center",
                    "yanchor": "middle",
                    "font": {"color": "white", "size": 10},
                }
            )

            # Transition label
            annotations.append(
                {
                    "x": x_pos,
                    "y": y_pos - 1,
                    "text": transition.upper(),
                    "showarrow": False,
                    "xanchor": "center",
                    "yanchor": "top",
                    "font": {"size": 8, "color": "gray"},
                }
            )

        return annotations

    def top_scenario_highlight(
        self,
        top_scenario: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Create highlight/annotation for the top-ranked scenario.

        Returns dict with trace + annotation data for emphasis.
        """
        candles_data = top_scenario.get("candles", [])

        if not candles_data:
            return {}
        candles = [
            cast(Mapping[str, Any], item)
            for item in cast(Sequence[Any], candles_data)
            if isinstance(item, Mapping)
        ]
        if not candles:
            return {}

        # Draw a shape box around the scenario path
        end_idx = len(candles) - 1
        prices = [float(c.get("c", 0.0)) for c in candles]
        max_price = max(prices)
        min_price = min(prices)

        return {
            "type": "rect",
            "xref": "x",
            "yref": "y",
            "x0": 0,
            "y0": min_price,
            "x1": end_idx,
            "y1": max_price,
            "fillcolor": "#00FF00",
            "opacity": 0.1,
            "layer": "below",
            "line": {"color": "#00FF00", "width": 2, "dash": "dash"},
        }


def create_scenario_dashboard_layout(
    scenarios_paint_data: Mapping[str, Any],
    title: str = "Scenario Forecasting Dashboard",
) -> dict[str, Any]:
    """
    Create a complete Plotly layout for scenario display.

    Combines candlestick traces, heatmap, tree structure, and annotations.

    Args:
        scenarios_paint_data: Full paint layer from scenario_integration
        title: Dashboard title

    Returns:
        Plotly layout dict + traces dict
    """
    painter = ScenarioPainter()

    scenarios_list = scenarios_paint_data.get("scenarios", [])
    heatmap = scenarios_paint_data.get("confidence_heatmap", [])
    tree = scenarios_paint_data.get("tree_structure", {})
    top_scenario = scenarios_paint_data.get("top_ranked", {})

    # Build traces
    candlestick_traces = painter.scenarios_to_candlestick_traces(scenarios_list)
    line_traces = painter.scenarios_to_line_traces(scenarios_list)
    heatmap_trace = painter.confidence_heatmap_to_trace(heatmap, len(scenarios_list))

    # Build annotations
    tree_annotations = painter.scenario_tree_structure_to_annotations(tree)
    highlight = painter.top_scenario_highlight(top_scenario)

    # Layout
    layout: dict[str, Any] = {
        "title": title,
        "xaxis": {
            "title": "Time Index (Steps Ahead)",
            "gridcolor": "#E0E0E0",
        },
        "yaxis": {
            "title": "Price",
            "gridcolor": "#E0E0E0",
        },
        "hovermode": "x unified",
        "height": 600,
        "annotations": tree_annotations,
        "shapes": [highlight] if highlight else [],
    }

    return {
        "candlestick_traces": candlestick_traces,
        "line_traces": line_traces,
        "heatmap_trace": heatmap_trace,
        "layout": layout,
    }


def export_scenarios_as_json(
    scenarios: Sequence[Mapping[str, Any]],
) -> str:
    """
    Export scenarios as JSON for export/sharing.

    Args:
        scenarios: Paint dicts

    Returns:
        JSON string
    """
    import json

    export_data: dict[str, Any] = {
        "export_format": "phoenixguard_scenarios_v1",
        "scenario_count": len(scenarios),
        "scenarios": list(scenarios),
    }

    return json.dumps(export_data, indent=2, default=str)


def export_scenarios_as_csv(
    scenarios: Sequence[Any],
) -> str:
    """
    Export scenarios as CSV for analysis.

    Args:
        scenarios: Paint dicts

    Returns:
        CSV string (header + rows)
    """
    lines = [
        "Rank,Probability,Transition,Steps,Direction,Confidence,Memory_Alignment,Cost,Summary"
    ]

    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            continue
        scenario_map = cast(Mapping[str, Any], scenario)

        rank = scenario_map.get("rank", 0)
        probability = scenario_map.get("probability", 0.0)
        transition = scenario_map.get("transition_type", "UNKNOWN")
        candles_data = scenario_map.get("candles", [])
        candles = [
            cast(Mapping[str, Any], item)
            for item in cast(Sequence[Any], candles_data)
            if isinstance(item, Mapping)
        ]
        steps = len(candles)
        direction = candles[-1].get("direction", "HOLD") if candles else "HOLD"
        confidence = scenario_map.get("confidence", 0.0) if candles else 0.0
        memory_align = scenario_map.get("memory_alignment", 0.0)
        cost = scenario_map.get("cost", 0.0)
        summary = str(scenario_map.get("summary", "")).replace(",", ";")

        line = f"{rank},{probability:.4f},{transition},{steps},{direction},{confidence:.2f},{memory_align:.2f},{cost:.4f},\"{summary}\""
        lines.append(line)

    return "\n".join(lines)
