from __future__ import annotations

from typing import Any, Mapping, Sequence


MARKET_MEMORY_CONSTITUTION_VERSION = "MARKET_MEMORY_CONSTITUTION_V1"

DEFAULT_LEARNED_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "rule_steep_buy_m5_otc_001",
        "condition": "BUY after steep impulse without pullback",
        "observed_result": "high failure rate",
        "action": "require pullback/retest before executable",
        "confidence": 0.78,
        "trap": "LATE_CHASE_AFTER_IMPULSE",
    },
    {
        "rule_id": "rule_buy_into_supply_001",
        "condition": "BUY near fresh supply",
        "observed_result": "high snapback risk",
        "action": "require more distance to opposing force",
        "confidence": 0.74,
        "trap": "BUY_INTO_SUPPLY",
    },
    {
        "rule_id": "rule_sell_into_demand_001",
        "condition": "SELL near fresh demand",
        "observed_result": "high rejection risk",
        "action": "require more distance to opposing force",
        "confidence": 0.74,
        "trap": "SELL_INTO_DEMAND",
    },
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(value)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def applicable_market_memory_rules(context: Mapping[str, Any]) -> dict[str, Any]:
    row = _mapping(context)
    traps = {
        _upper(_mapping(item).get("trap") or item)
        for item in _sequence(_mapping(row.get("trap_assessment")).get("active_traps"))
    }
    if _mapping(row.get("market_context")).get("is_late_chase") is True:
        traps.add("LATE_CHASE_AFTER_IMPULSE")
    side = _upper(row.get("side") or _mapping(row.get("market_context")).get("dominant_side"))
    if side == "BUY" and _mapping(row.get("market_context")).get("opposing_force_distance_ok") is False:
        traps.add("BUY_INTO_SUPPLY")
    if side == "SELL" and _mapping(row.get("market_context")).get("opposing_force_distance_ok") is False:
        traps.add("SELL_INTO_DEMAND")

    learned = []
    for rule in DEFAULT_LEARNED_RULES:
        if _upper(rule.get("trap")) in traps:
            learned.append(dict(rule))
    deny = any(_float(rule.get("confidence"), 0.0) >= 0.7 for rule in learned)
    return {
        "version": MARKET_MEMORY_CONSTITUTION_VERSION,
        "applicable_rules": learned,
        "rule_count": len(learned),
        "execution_allowed": not deny,
        "reason": learned[0]["action"] if learned else "No learned memory rule blocks this setup.",
    }


__all__ = [
    "DEFAULT_LEARNED_RULES",
    "MARKET_MEMORY_CONSTITUTION_VERSION",
    "applicable_market_memory_rules",
]
