from __future__ import annotations

from phoenixguard.decision.market_memory_constitution import applicable_market_memory_rules


def test_market_memory_constitution_blocks_late_chase_rule() -> None:
    result = applicable_market_memory_rules(
        {
            "side": "BUY",
            "trap_assessment": {
                "active_traps": [{"trap": "LATE_CHASE_AFTER_IMPULSE", "severity": 0.9}]
            },
        }
    )

    assert result["execution_allowed"] is False
    assert result["applicable_rules"][0]["rule_id"] == "rule_steep_buy_m5_otc_001"


def test_market_memory_constitution_allows_when_no_rule_applies() -> None:
    result = applicable_market_memory_rules({"side": "BUY", "market_context": {"opposing_force_distance_ok": True}})

    assert result["execution_allowed"] is True
    assert result["rule_count"] == 0
