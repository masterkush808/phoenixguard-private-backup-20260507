from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypedDict, cast

MacroTrend = Literal["BULL", "BEAR"]
LocalPhase = Literal[
    "with_trend_push",
    "with_trend_pause",
    "counter_trend_pullback",
    "counter_trend_spike",
    "reversal_base",
    "continuation_base",
]
PhaseRisk = Literal["exhaustion_risk", "breakout_risk", "chop_risk", "managed_counter_trend"]
IntentNext = Literal["continue", "pullback", "reversal_attempt", "fakeout"]
ControlState = Literal["with_trend", "counter_trend", "transition"]
ConflictType = Literal["healthy_pullback", "possible_reversal", "noise_conflict", "none"]


class EpisodeMatch(TypedDict):
    entry_id: str
    label: str
    similarity: float
    macro_trend: MacroTrend
    local_phase: LocalPhase
    intent_next: IntentNext


class TransitionProbabilities(TypedDict):
    continue_prob: float
    pullback_prob: float
    reversal_attempt_prob: float
    fakeout_prob: float


@dataclass
class MarketState:
    macro_trend: MacroTrend
    local_phase: LocalPhase
    phase_risk: PhaseRisk
    intent_next: IntentNext
    control_state: ControlState
    control_strength_delta: float
    conflict_type: ConflictType = "none"
    time_to_resolution_candles: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CVReasoningTrace:
    market_state: MarketState
    transition_probabilities: TransitionProbabilities
    episode_matches: list[EpisodeMatch] = field(default_factory=lambda: cast(list[EpisodeMatch], []))
    final_trade_bias: Literal["BUY", "SELL", "HOLD"] = "SELL"
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def _check_membership(value: str, allowed: tuple[str, ...], field_name: str) -> None:
    if value not in allowed:
        raise ValueError(f"Invalid {field_name}: {value}")


def validate_market_state(state: MarketState) -> None:
    _check_membership(state.macro_trend, ("BULL", "BEAR"), "macro_trend")
    _check_membership(
        state.local_phase,
        (
            "with_trend_push",
            "with_trend_pause",
            "counter_trend_pullback",
            "counter_trend_spike",
            "reversal_base",
            "continuation_base",
        ),
        "local_phase",
    )
    _check_membership(
        state.phase_risk,
        ("exhaustion_risk", "breakout_risk", "chop_risk", "managed_counter_trend"),
        "phase_risk",
    )
    _check_membership(state.intent_next, ("continue", "pullback", "reversal_attempt", "fakeout"), "intent_next")
    _check_membership(state.control_state, ("with_trend", "counter_trend", "transition"), "control_state")
    _check_membership(
        state.conflict_type,
        ("healthy_pullback", "possible_reversal", "noise_conflict", "none"),
        "conflict_type",
    )


def normalize_transition_probabilities(raw: dict[str, float]) -> TransitionProbabilities:
    keys = ("continue", "pullback", "reversal_attempt", "fakeout")
    cleaned: dict[str, float] = {}
    total = 0.0
    for k in keys:
        val = float(raw.get(k, 0.0) or 0.0)
        val = 0.0 if val < 0.0 else val
        cleaned[k] = val
        total += val

    if total <= 1e-9:
        base = 0.25
        return {
            "continue_prob": base,
            "pullback_prob": base,
            "reversal_attempt_prob": base,
            "fakeout_prob": base,
        }

    return {
        "continue_prob": cleaned["continue"] / total,
        "pullback_prob": cleaned["pullback"] / total,
        "reversal_attempt_prob": cleaned["reversal_attempt"] / total,
        "fakeout_prob": cleaned["fakeout"] / total,
    }
