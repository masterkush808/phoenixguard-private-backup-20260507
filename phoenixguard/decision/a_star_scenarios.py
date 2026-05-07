"""
PhoenixGuard A* Scenario Prediction Engine
===========================================
Predicts and ranks unseen future candles using A* search.

Key features:
  - Multi-step candle generation from memory + regression forecasts
  - A* search exploration of possible market paths
  - Heuristic: maximize setup quality + probability alignment
  - Output: ranked scenarios with paint annotations for visualization
  - Memory-aware: bias scenarios toward patterns seen in BUYS/SELLS
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Optional
from enum import Enum

import numpy as np
from numpy.typing import NDArray


class TransitionType(Enum):
    """Market behavior taxonomy."""
    CONTINUE = "continue"
    PULLBACK = "pullback"
    REVERSAL_ATTEMPT = "reversal_attempt"
    FAKEOUT = "fakeout"


@dataclass(frozen=True)
class CandleState:
    """Single candle in OHLC form + metadata."""
    open: float
    high: float
    low: float
    close: float
    volume: float = 1.0
    time_idx: int = 0
    direction: str = "HOLD"  # BUY, SELL, HOLD
    confidence: float = 0.5

    def body_size(self) -> float:
        """Absolute size of body."""
        return abs(self.close - self.open)

    def range_size(self) -> float:
        """High - Low."""
        return self.high - self.low

    def wick_ratio(self) -> float:
        """Upper wick / range, clipped to [0, 1]."""
        if self.range_size() < 1e-8:
            return 0.5
        if self.close > self.open:  # bullish
            return (self.high - self.close) / self.range_size()
        else:  # bearish
            return (self.high - self.open) / self.range_size()

    def color(self) -> str:
        """'green' if bullish, 'red' if bearish."""
        return "green" if self.close >= self.open else "red"


@dataclass(frozen=True)
class ScenarioNode:
    """A* node: sequence of predicted candles + metadata."""
    candle_sequence: tuple[CandleState, ...]  # All candles so far (immutable)
    depth: int
    cost: float = 0.0  # Cumulative cost (g in A*)
    heuristic: float = 0.0  # Estimated total cost (h in A*)
    transition_type: TransitionType = TransitionType.CONTINUE
    memory_alignment: float = 0.5
    confidence_path: tuple[float, ...] = field(default_factory=tuple)
    explanation: str = ""

    def f_score(self) -> float:
        """f = g + h for A* priority."""
        return self.cost + self.heuristic

    def last_candle(self) -> CandleState:
        """Get most recent candle."""
        return self.candle_sequence[-1] if self.candle_sequence else None

    def parent_candle(self) -> CandleState | None:
        """Get previous candle (for reference)."""
        if len(self.candle_sequence) < 2:
            return None
        return self.candle_sequence[-2]

    def __lt__(self, other: ScenarioNode) -> bool:
        """Priority queue ordering (min-heap by f-score)."""
        if abs(self.f_score() - other.f_score()) > 1e-8:
            return self.f_score() < other.f_score()
        return self.depth < other.depth


@dataclass
class ScenarioPrediction:
    """Top-ranked scenario prediction with visualization metadata."""
    scenario: ScenarioNode
    rank: int
    probability: float
    projected_candles: list[CandleState]
    paint_annotations: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def to_paint_dict(self) -> dict[str, Any]:
        """Convert to paint/visualization format."""
        return {
            "rank": self.rank,
            "probability": self.probability,
            "candles": [
                {
                    "o": c.open,
                    "h": c.high,
                    "l": c.low,
                    "c": c.close,
                    "v": c.volume,
                    "color": c.color(),
                    "confidence": c.confidence,
                    "time_idx": c.time_idx,
                }
                for c in self.projected_candles
            ],
            "annotations": self.paint_annotations,
            "transition_type": self.scenario.transition_type.value,
            "memory_alignment": self.scenario.memory_alignment,
            "summary": self.summary,
        }


class A_StarScenarioPredictor:
    """
    A* search for multi-step candle prediction.
    Expands high-quality scenarios first, ranks by setup quality.
    """

    def __init__(
        self,
        logger: Any = None,
        max_depth: int = 5,
        max_scenarios: int = 8,
        expand_factor: int = 3,
    ):
        self.logger = logger
        self.max_depth = int(max_depth)
        self.max_scenarios = int(max_scenarios)
        self.expand_factor = int(expand_factor)  # branching factor per node

    def predict_scenarios(
        self,
        last_candle: CandleState,
        historical_context: Sequence[CandleState],
        forecast_data: dict[str, Any],
        memory_bias: dict[str, float] | None = None,
        transition_probs: dict[str, float] | None = None,
        max_depth: int | None = None,
    ) -> list[ScenarioPrediction]:
        """
        Predict top-N scenarios from last known candle using A* search.

        Args:
            last_candle: The most recent candle
            historical_context: Prior candles for trend/pattern context
            forecast_data: Dict with 'q05', 'q50', 'q95', 'poly_slope', etc.
            memory_bias: Dict with BUY/SELL frequency bias from memory bank
            transition_probs: Dict with continue/pullback/reversal/fakeout probs
            max_depth: Override default max_depth for this prediction

        Returns:
            List of ScenarioPrediction (sorted by rank/probability)
        """
        depth_limit = max_depth if max_depth is not None else self.max_depth
        memory_bias = memory_bias or {}
        transition_probs = transition_probs or self._default_transition_probs()

        # Initialize root node
        root = ScenarioNode(
            candle_sequence=(last_candle,),
            depth=0,
            cost=0.0,
            heuristic=self._heuristic_cost(
                last_candle, forecast_data, memory_bias, depth_limit
            ),
            confidence_path=(last_candle.confidence,),
        )

        # A* search
        open_set: list[ScenarioNode] = [root]
        closed_set: set[int] = set()
        solutions: list[ScenarioNode] = []

        while open_set and len(solutions) < self.max_scenarios:
            current = heapq.heappop(open_set)
            node_hash = hash(self._node_signature(current))

            if node_hash in closed_set:
                continue
            closed_set.add(node_hash)

            if current.depth >= depth_limit:
                solutions.append(current)
                continue

            # Expand current node
            successors = self._expand_node(
                current, forecast_data, memory_bias, transition_probs
            )
            for succ in successors:
                succ_hash = hash(self._node_signature(succ))
                if succ_hash not in closed_set:
                    heapq.heappush(open_set, succ)

        # Rank & convert to visualization format
        solutions.sort(key=lambda x: x.f_score())
        predictions = []

        for rank, solution in enumerate(solutions[: self.max_scenarios], start=1):
            prob = self._scenario_probability(solution)
            annotation = self._build_paint_annotation(solution, forecast_data)
            pred = ScenarioPrediction(
                scenario=solution,
                rank=rank,
                probability=prob,
                projected_candles=list(solution.candle_sequence),
                paint_annotations=[annotation],
                summary=self._build_summary(solution, prob),
            )
            predictions.append(pred)

        return predictions

    def _expand_node(
        self,
        node: ScenarioNode,
        forecast_data: dict[str, Any],
        memory_bias: dict[str, float],
        transition_probs: dict[str, float],
    ) -> list[ScenarioNode]:
        """Generate child nodes from current node."""
        last = node.last_candle()
        parent = node.parent_candle()

        children = []
        for transition_type in list(TransitionType):
            for branch_idx in range(self.expand_factor):
                next_candle = self._generate_next_candle(
                    last,
                    parent,
                    forecast_data,
                    memory_bias,
                    transition_type,
                    branch_idx,
                    self.expand_factor,
                )
                if next_candle is None:
                    continue

                new_seq = node.candle_sequence + (next_candle,)
                new_cost = node.cost + self._step_cost(
                    last, next_candle, transition_type, memory_bias
                )
                new_heuristic = self._heuristic_cost(
                    next_candle,
                    forecast_data,
                    memory_bias,
                    self.max_depth - node.depth - 1,
                )
                trans_prob = transition_probs.get(transition_type.value, 0.25)

                child = ScenarioNode(
                    candle_sequence=new_seq,
                    depth=node.depth + 1,
                    cost=new_cost,
                    heuristic=new_heuristic,
                    transition_type=transition_type,
                    memory_alignment=memory_bias.get("alignment", 0.5),
                    confidence_path=node.confidence_path + (next_candle.confidence,),
                    explanation=f"{transition_type.value} (branch {branch_idx})",
                )
                children.append(child)

        return children

    def _generate_next_candle(
        self,
        last: CandleState,
        parent: CandleState | None,
        forecast_data: dict[str, Any],
        memory_bias: dict[str, float],
        transition_type: TransitionType,
        branch_idx: int,
        total_branches: int,
    ) -> CandleState | None:
        """
        Generate a plausible next candle based on forecasting + transition logic.
        Uses quantile forecasts and memory patterns.
        """
        q05 = forecast_data.get("q05", last.close * 0.98)
        q50 = forecast_data.get("q50", last.close)
        q95 = forecast_data.get("q95", last.close * 1.02)
        poly_slope = forecast_data.get("poly_slope", 0.0)

        # Branch variation (spread branches across quantile range)
        branch_weight = branch_idx / max(total_branches - 1, 1.0)
        if transition_type == TransitionType.CONTINUE:
            close_target = q50 + poly_slope * 0.5
            conf_boost = 0.1
        elif transition_type == TransitionType.PULLBACK:
            # Pull back toward mid-level
            close_target = q50 - poly_slope * 0.3
            conf_boost = 0.05
        elif transition_type == TransitionType.REVERSAL_ATTEMPT:
            # Push opposite direction
            close_target = q50 - np.sign(poly_slope) * (q95 - q50) * 0.7
            conf_boost = -0.1
        else:  # FAKEOUT
            # Quick reversal after fake break
            close_target = q50 + np.sign(poly_slope) * (q95 - q50) * 0.4
            conf_boost = 0.0

        # Vary close around target
        close = close_target + (q95 - q05) * (branch_weight - 0.5) * 0.3

        # Generate OHLC from close
        body_size = abs(close - last.close) * 0.5
        range_size = body_size * 2.0
        high = max(close, last.close) + range_size * 0.3
        low = min(close, last.close) - range_size * 0.3

        # Add noise (ATR-based)
        atr_proxy = forecast_data.get("atr", (q95 - q05) * 0.5)
        noise_factor = 0.1 * atr_proxy
        high += np.random.randn() * noise_factor
        low -= np.random.randn() * noise_factor

        open_price = (last.close + close) / 2.0 + np.random.randn() * noise_factor * 0.5

        # Direction from close
        direction = "BUY" if close > open_price else "SELL" if close < open_price else "HOLD"
        confidence = np.clip(
            forecast_data.get("path_confidence", 0.5) + conf_boost, 0.0, 1.0
        )

        return CandleState(
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=forecast_data.get("volume", 1.0),
            time_idx=last.time_idx + 1,
            direction=direction,
            confidence=confidence,
        )

    def _step_cost(
        self,
        from_candle: CandleState,
        to_candle: CandleState,
        transition: TransitionType,
        memory_bias: dict[str, float],
    ) -> float:
        """Cost of transitioning from one candle to next."""
        # Penalize scenarios that diverge from memory patterns
        memory_alignment = memory_bias.get("alignment", 0.5)
        direction_match = (
            1.0
            if (from_candle.direction == "BUY" and to_candle.direction == "BUY")
            or (from_candle.direction == "SELL" and to_candle.direction == "SELL")
            else 0.5
        )

        # Penalize large moves
        move_pct = abs(to_candle.close - from_candle.close) / max(from_candle.close, 1e-8)
        move_penalty = max(0.0, move_pct - 0.05)  # Allow up to 5% without penalty

        # Cost formula: lower is better
        cost = (1.0 - memory_alignment * direction_match) + move_penalty
        return float(np.clip(cost, 0.0, 10.0))

    def _heuristic_cost(
        self,
        candle: CandleState,
        forecast_data: dict[str, Any],
        memory_bias: dict[str, float],
        remaining_depth: int,
    ) -> float:
        """Optimistic estimate of cost to complete from here."""
        if remaining_depth <= 0:
            return 0.0

        # Favor scenarios with high forecast confidence
        path_conf = forecast_data.get("path_confidence", 0.5)
        setup_quality = forecast_data.get("structure_trade_ready", 0.0)

        # Remaining cost proportional to depth & alignment with memory
        memory_alignment = memory_bias.get("alignment", 0.5)
        heur = (1.0 - path_conf * memory_alignment) * remaining_depth

        return float(np.clip(heur, 0.0, 100.0))

    def _scenario_probability(self, scenario: ScenarioNode) -> float:
        """Compute joint probability of entire scenario path."""
        if not scenario.confidence_path:
            return 0.5

        # Geometric mean of confidence scores
        conf_array = np.array(scenario.confidence_path, dtype=np.float32)
        prob = float(np.exp(np.mean(np.log(np.clip(conf_array, 0.01, 0.99)))))
        return float(np.clip(prob, 0.0, 1.0))

    def _build_paint_annotation(
        self, scenario: ScenarioNode, forecast_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Annotation for visual layer."""
        return {
            "type": "scenario_path",
            "transition": scenario.transition_type.value,
            "confidence": self._scenario_probability(scenario),
            "memory_alignment": scenario.memory_alignment,
            "explanation": scenario.explanation,
            "depth": scenario.depth,
            "cost": scenario.cost,
        }

    def _build_summary(self, scenario: ScenarioNode, prob: float) -> str:
        """Human-readable summary of scenario."""
        last = scenario.last_candle()
        candle_count = len(scenario.candle_sequence)
        direction = last.direction if last else "HOLD"
        return (
            f"Path: {scenario.transition_type.value} | "
            f"Prob: {prob:.1%} | Dir: {direction} | "
            f"Steps: {candle_count} | Cost: {scenario.cost:.3f}"
        )

    def _node_signature(self, node: ScenarioNode) -> tuple:
        """Hashable signature for deduplication."""
        last = node.last_candle()
        return (
            node.depth,
            round(last.open, 4),
            round(last.close, 4),
            last.direction,
        )

    def _default_transition_probs(self) -> dict[str, float]:
        """Fallback transition probabilities."""
        return {
            TransitionType.CONTINUE.value: 0.50,
            TransitionType.PULLBACK.value: 0.25,
            TransitionType.REVERSAL_ATTEMPT.value: 0.15,
            TransitionType.FAKEOUT.value: 0.10,
        }


def predict_future_candles(
    last_candle: CandleState,
    historical_candles: Sequence[CandleState],
    forecast_output: dict[str, Any],
    memory_bank_stats: dict[str, Any] | None = None,
    num_scenarios: int = 5,
) -> list[ScenarioPrediction]:
    """
    High-level entry point: predict future candles and return ranked scenarios.

    Args:
        last_candle: Most recent candle
        historical_candles: Context (e.g., last 20 candles)
        forecast_output: From regression module (q05, q50, q95, etc.)
        memory_bank_stats: Historical pattern frequencies
        num_scenarios: How many top scenarios to return

    Returns:
        Ranked list of ScenarioPrediction for painting.
    """
    memory_bias = _extract_memory_bias(memory_bank_stats or {})
    transition_probs = _extract_transition_probs(forecast_output)

    predictor = A_StarScenarioPredictor(
        max_depth=5,
        max_scenarios=num_scenarios,
        expand_factor=3,
    )

    return predictor.predict_scenarios(
        last_candle=last_candle,
        historical_context=historical_candles,
        forecast_data=forecast_output,
        memory_bias=memory_bias,
        transition_probs=transition_probs,
    )


def _extract_memory_bias(stats: dict[str, Any]) -> dict[str, float]:
    """Extract memory pattern bias from bank statistics."""
    total = stats.get("total_samples", 100)
    buy_count = stats.get("buy_count", 50)
    sell_count = stats.get("sell_count", 50)
    buy_freq = buy_count / max(total, 1)
    sell_freq = sell_count / max(total, 1)
    alignment = buy_freq if buy_freq > sell_freq else sell_freq

    return {
        "buy_frequency": float(np.clip(buy_freq, 0.0, 1.0)),
        "sell_frequency": float(np.clip(sell_freq, 0.0, 1.0)),
        "alignment": float(np.clip(alignment, 0.0, 1.0)),
    }


def _extract_transition_probs(forecast: dict[str, Any]) -> dict[str, float]:
    """Extract transition probabilities from forecast output."""
    return {
        "continue": float(np.clip(forecast.get("continue_prob", 0.50), 0.0, 1.0)),
        "pullback": float(np.clip(forecast.get("pullback_prob", 0.25), 0.0, 1.0)),
        "reversal_attempt": float(
            np.clip(forecast.get("reversal_attempt_prob", 0.15), 0.0, 1.0)
        ),
        "fakeout": float(np.clip(forecast.get("fakeout_prob", 0.10), 0.0, 1.0)),
    }
