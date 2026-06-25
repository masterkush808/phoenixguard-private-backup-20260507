from __future__ import annotations

from typing import Any, Mapping, cast


INTERPRETER_SCHEMA: list[str] = [
    "schema_version",
    "setup_type",
    "structure_summary",
    "memory_match_quality",
    "forecast_direction",
    "forecast_magnitude",
    "forecast_range",
    "decision_state",
    "execution_permission",
    "active_trade_state",
    "directional_intent",
    "confidence_level",
    "confidence_band",
    "action_bias",
    "final_action",
    "gate_alignment",
    "gate_blockers",
    "support_alignment",
    "risk_factors",
    "invalidation_condition",
    "rationale",
    "trade_plan",
]

_VALID_ACTIONS = {"BUY", "SELL", "HOLD"}
_VALID_ACTIVE_TRADE_STATES = {
    "BUY_NOW",
    "BUY_ON_CONFIRMATION",
    "SELL_NOW",
    "SELL_ON_CONFIRMATION",
    "HOLD_TRUE",
}


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _clip01(value: Any, default: float = 0.0) -> float:
    return min(max(_finite_float(value, default), 0.0), 1.0)


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}
    return {}


def _safe_action(value: Any, default: str = "HOLD") -> str:
    action = str(value or "").strip().upper()
    return action if action in _VALID_ACTIONS else default


def _confidence_band(confidence: float) -> str:
    if confidence >= 0.82:
        return "high"
    if confidence >= 0.62:
        return "moderate"
    if confidence >= 0.45:
        return "guarded"
    return "low"


def _safe_active_trade_state(value: Any, default: str = "HOLD_TRUE") -> str:
    state = str(value or "").strip().upper()
    return state if state in _VALID_ACTIVE_TRADE_STATES else default


def _active_trade_plan(state: str, confidence_band: str) -> str:
    if state == "BUY_NOW":
        return f"Buy now with {confidence_band} conviction"
    if state == "SELL_NOW":
        return f"Sell now with {confidence_band} conviction"
    if state == "BUY_ON_CONFIRMATION":
        return "Long bias is active; enter only on structural confirmation"
    if state == "SELL_ON_CONFIRMATION":
        return "Short bias is active; enter only on structural confirmation"
    return "Hold until the projection, gates, and execution checks tighten up"


def _memory_quality_label(memory: Mapping[str, Any]) -> str:
    similarity = _clip01(memory.get("similarity", 0.0), 0.0)
    ambiguity = _clip01(memory.get("ambiguity", 0.0), 0.0)
    consensus_ratio = _clip01(memory.get("consensus_ratio", 0.0), 0.0)
    if similarity >= 0.87 and ambiguity <= 0.18 and consensus_ratio >= 0.70:
        return "high"
    if similarity >= 0.72 and ambiguity <= 0.30:
        return "medium"
    if similarity > 0.0:
        return "low"
    return "none"


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        items: list[str] = []
        for row in cast(list[Any], value):
            text = str(row or "").strip()
            if text:
                items.append(text)
        return items
    return []


def _join_or_default(items: list[str], default: str) -> str:
    return ", ".join(items) if items else default


def interpret(fusion: Mapping[str, Any]) -> dict[str, Any]:
    """
    Convert the post-ensemble fusion payload into a stable machine summary plus
    a compact operator-facing narrative.
    """
    cv = _safe_mapping(fusion.get("cv", {}))
    memory = _safe_mapping(fusion.get("memory", {}))
    forecast = _safe_mapping(fusion.get("forecast", {}))
    rl = _safe_mapping(fusion.get("rl", {}))
    gates = _safe_mapping(fusion.get("gates", {}))
    ensemble = _safe_mapping(fusion.get("ensemble", {}))
    context = _safe_mapping(fusion.get("context", {}))

    setup_type = str(cv.get("setup", forecast.get("structure_setup", "unknown")) or "unknown").strip() or "unknown"
    structure_summary = str(cv.get("structure", cv.get("notes", "Structure unavailable")) or "Structure unavailable").strip()
    memory_match_quality = str(memory.get("match_quality", "") or "").strip().lower() or _memory_quality_label(memory)
    forecast_direction = _safe_action(
        forecast.get("direction", ensemble.get("trade_bias", ensemble.get("execution_action", ensemble.get("action", rl.get("action", "HOLD"))))),
        default="HOLD",
    )
    forecast_magnitude = _finite_float(forecast.get("magnitude", forecast.get("q50", 0.0)), 0.0)
    forecast_range = [
        _finite_float(forecast.get("q05", 0.0), 0.0),
        _finite_float(forecast.get("q95", 0.0), 0.0),
    ]
    confidence_level = _clip01(ensemble.get("confidence", 0.0), 0.0)
    confidence_band = _confidence_band(confidence_level)
    action_bias = _safe_action(
        ensemble.get("trade_bias", ensemble.get("execution_action", rl.get("action", ensemble.get("action", "HOLD")))),
        default="HOLD",
    )
    directional_intent = _safe_action(ensemble.get("directional_intent", action_bias), default=action_bias)
    final_action = _safe_action(ensemble.get("action", action_bias), default=action_bias)
    decision_state = str(ensemble.get("decision_state", "UNCERTAIN") or "UNCERTAIN").strip().upper()
    execution_permission = str(
        ensemble.get("execution_permission", "WAIT_FOR_CONFIRMATION") or "WAIT_FOR_CONFIRMATION"
    ).strip().upper()
    raw_active_trade_state = str(ensemble.get("active_trade_state", "") or "").strip().upper()
    if raw_active_trade_state:
        active_trade_state = _safe_active_trade_state(raw_active_trade_state)
    elif final_action in {"BUY", "SELL"}:
        active_trade_state = f"{final_action}_{'NOW' if execution_permission == 'EXECUTE' else 'ON_CONFIRMATION'}"
    else:
        active_trade_state = "HOLD_TRUE"

    gates_passing = int(_finite_float(gates.get("passing", 0), 0.0))
    gates_total = max(int(_finite_float(gates.get("total", 0), 0.0)), gates_passing, 0)
    gate_alignment = f"{gates_passing}/{gates_total}" if gates_total > 0 else "0/0"
    gate_blockers = _as_text_list(gates.get("blockers", []))
    support_blockers = _as_text_list(gates.get("support_blockers", []))
    support_alignment = "aligned" if not support_blockers and bool(gates.get("support_ok", True)) else "watch"

    risk_factors: list[str] = []
    if str(gates.get("risk", "")).strip():
        risk_factors.append(str(gates.get("risk", "")).strip())
    risk_factors.extend(_as_text_list(context.get("risk_factors", [])))
    if gate_blockers:
        risk_factors.append(f"primary gates watching: {', '.join(gate_blockers[:3])}")
    if support_blockers:
        risk_factors.append(f"support checks watching: {', '.join(support_blockers[:3])}")
    if not risk_factors:
        risk_factors.append("No elevated risk factors surfaced by the current stack.")

    invalidation_condition = str(
        context.get("invalidation", gates.get("invalidation", "Wait if structure loses confirmation."))
        or "Wait if structure loses confirmation."
    ).strip()

    memory_direction = _safe_action(memory.get("direction", memory.get("dominant_label", "HOLD")), default="HOLD")
    projection_direction = _safe_action(context.get("projection_direction", forecast_direction), default=forecast_direction)
    rationale_parts = [
        f"bias={action_bias}",
        f"intent={directional_intent}",
        f"active={active_trade_state}",
        f"projection={projection_direction}",
        f"memory={memory_direction}",
        f"gates={gate_alignment}",
    ]
    if support_alignment != "aligned":
        rationale_parts.append(f"support={support_alignment}")
    rationale = "; ".join(rationale_parts)

    trade_plan = _active_trade_plan(active_trade_state, confidence_band)
    if execution_permission != "EXECUTE" and active_trade_state in {"BUY_NOW", "SELL_NOW", "HOLD_TRUE"}:
        trade_plan += f"; execution={execution_permission.lower().replace('_', ' ')}"

    human_lines = [
        f"Action: {final_action} | Bias: {action_bias} | Intent: {directional_intent} | Active: {active_trade_state} | State: {decision_state} | Execution: {execution_permission}",
        f"Setup: {setup_type}",
        f"Structure: {structure_summary}",
        (
            f"Memory: {memory_match_quality} match"
            f" ({memory_direction}, sim={_clip01(memory.get('similarity', 0.0), 0.0):.2f}, "
            f"ambiguity={_clip01(memory.get('ambiguity', 0.0), 0.0):.2f})"
        ),
        (
            f"Forecast: {forecast_direction} {forecast_magnitude:+.3f}% "
            f"[{forecast_range[0]:+.3f}, {forecast_range[1]:+.3f}]"
        ),
        f"Gates: {gate_alignment} passing; support={support_alignment}",
        f"Risk: {_join_or_default(risk_factors, 'No elevated risk factors.')}",
        f"Invalidation: {invalidation_condition}",
        f"Plan: {trade_plan}",
    ]

    machine_output: dict[str, Any] = {
        "schema_version": "2.0",
        "setup_type": setup_type,
        "structure_summary": structure_summary,
        "memory_match_quality": memory_match_quality,
        "forecast_direction": forecast_direction,
        "forecast_magnitude": forecast_magnitude,
        "forecast_range": forecast_range,
        "decision_state": decision_state,
        "execution_permission": execution_permission,
        "active_trade_state": active_trade_state,
        "directional_intent": directional_intent,
        "confidence_level": confidence_level,
        "confidence_band": confidence_band,
        "action_bias": action_bias,
        "final_action": final_action,
        "gate_alignment": gate_alignment,
        "gate_blockers": gate_blockers,
        "support_alignment": support_alignment,
        "risk_factors": risk_factors,
        "invalidation_condition": invalidation_condition,
        "rationale": rationale,
        "trade_plan": trade_plan,
        "raw": dict(fusion),
    }

    return {
        "machine": machine_output,
        "human": "\n".join(human_lines),
        "schema": list(INTERPRETER_SCHEMA),
    }
