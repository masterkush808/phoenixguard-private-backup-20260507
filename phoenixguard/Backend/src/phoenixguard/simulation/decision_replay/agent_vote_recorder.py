from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, cast


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


@dataclass(frozen=True)
class AgentVote:
    agent: str
    side: str
    confidence: float
    state: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "side": self.side,
            "confidence": round(float(self.confidence), 4),
            "state": self.state,
            "reason": self.reason,
        }


def record_agent_votes(snapshot: Mapping[str, Any], council_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    market_context = _mapping(snapshot.get("market_context") or council_result.get("market_context"))
    global_structure = _mapping(snapshot.get("global_structure"))
    local_structure = _mapping(snapshot.get("local_micro_structure"))
    zone = _mapping(snapshot.get("zone_liquidity") or snapshot.get("zone_context"))
    angle = _mapping(snapshot.get("angle_context") or snapshot.get("angle_dynamics"))
    history = _mapping(snapshot.get("history_context") or snapshot.get("historical_pattern"))
    risk = _mapping(snapshot.get("risk_context") or snapshot.get("risk_opposing_force"))
    council = _mapping(council_result.get("model_council"))
    votes = [
        AgentVote(
            "global_structure",
            _side(global_structure.get("global_side") or market_context.get("global_side")),
            _float(global_structure.get("global_confidence"), 0.0),
            str(global_structure.get("global_state") or "UNKNOWN"),
            str(global_structure.get("reason") or "Global structure replay vote."),
        ),
        AgentVote(
            "local_structure",
            _side(local_structure.get("local_side") or market_context.get("local_side")),
            _float(local_structure.get("confidence"), 0.0),
            str(local_structure.get("local_state") or "UNKNOWN"),
            str(local_structure.get("reason") or "Local micro-structure replay vote."),
        ),
        AgentVote(
            "zone_agent",
            _side(zone.get("side") or market_context.get("dominant_side")),
            _float(zone.get("strength"), 0.0),
            str(zone.get("zone_type") or "UNKNOWN"),
            str(zone.get("reason") or "Zone liquidity replay vote."),
        ),
        AgentVote(
            "angle_agent",
            _side(angle.get("side") or market_context.get("dominant_side")),
            0.0 if angle.get("late_chase_risk") or angle.get("post_impulse_wait_required") else 0.72,
            str(angle.get("angle_class") or "UNKNOWN"),
            str(angle.get("reason") or "Angle dynamics replay vote."),
        ),
        AgentVote(
            "history_agent",
            _side(history.get("side") or market_context.get("dominant_side")),
            0.15 if str(history.get("historical_late_entry_risk") or "").upper() == "HIGH" else 0.7,
            str(history.get("similarity_state") or "UNKNOWN"),
            str(history.get("reason") or "Historical pattern replay vote."),
        ),
        AgentVote(
            "risk_opposing_force",
            _side(risk.get("side") or market_context.get("dominant_side")),
            0.75 if risk.get("distance_ok", market_context.get("opposing_force_distance_ok", True)) else 0.05,
            str(risk.get("risk_state") or "UNKNOWN"),
            str(risk.get("reason") or "Opposing-force replay vote."),
        ),
        AgentVote(
            "arbitration",
            _side(council.get("final_side")),
            _float(council.get("confidence"), 0.0),
            str(council.get("final_state") or "WATCHING"),
            str(council.get("arbitration_reason") or council_result.get("block_reason") or "Council arbitration replay result."),
        ),
    ]
    return [vote.as_dict() for vote in votes]
