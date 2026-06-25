from __future__ import annotations

from typing import Any, Mapping

from phoenixguard.decision.adversarial_market_simulator import (
    ADVERSARIAL_SCENARIOS,
    build_adversarial_snapshot,
    run_adversarial_market_suite,
)
from phoenixguard.decision.model_council_v3 import evaluate_model_council_v3


def test_adversarial_simulator_has_required_market_traps() -> None:
    assert "steep_impulse_then_reversal" in ADVERSARIAL_SCENARIOS
    assert "fake_breakout_above_resistance" in ADVERSARIAL_SCENARIOS
    assert "strong_candle_into_opposing_zone" in ADVERSARIAL_SCENARIOS


def test_adversarial_steep_impulse_snapshot_sets_late_chase_inputs() -> None:
    snapshot = build_adversarial_snapshot("steep_impulse_then_reversal")

    assert snapshot["angle_context"]["late_chase_risk"] is True
    assert snapshot["history_context"]["historical_late_entry_risk"] == "HIGH"


def test_adversarial_market_suite_blocks_dangerous_scenarios() -> None:
    def _evaluate(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        return evaluate_model_council_v3(snapshot, now=1000.0)

    result = run_adversarial_market_suite(
        _evaluate
    )

    assert result["passed"] is True
    dangerous = [row for row in result["results"] if row["scenario"] != "slow_grind_trend"]
    assert all(row["final_state"] in {"WATCHING", "BLOCKED_BY_MARKET", "BLOCKED_BY_RUNTIME", "CONFLICT"} for row in dangerous)
