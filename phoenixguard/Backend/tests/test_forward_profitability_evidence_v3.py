from __future__ import annotations

from phoenixguard.study.path_clock_liquidity_store_v3 import (
    PathClockLiquiditySideStoreV3,
)


def test_forward_profitability_requires_conservative_positive_expectancy() -> None:
    evidence = PathClockLiquiditySideStoreV3._profitability_evidence(
        matured_count=500,
        candidate_metrics={"directional_accuracy": 0.70},
        baseline_metrics={"directional_accuracy": 0.55},
    )

    assert evidence["status"] == "PROVEN_FORWARD_POSITIVE_EXPECTANCY"
    assert evidence["promotion_eligible"] is True
    assert evidence["conservative_promotion_scenario"]["lower_bound_positive"] is True
    assert evidence["reference_scenario"]["expected_value_per_unit_point"] == 0.225
    assert evidence["execution_authority"] is False


def test_directional_accuracy_cannot_promote_negative_payout_expectancy() -> None:
    evidence = PathClockLiquiditySideStoreV3._profitability_evidence(
        matured_count=1000,
        candidate_metrics={"directional_accuracy": 0.56},
        baseline_metrics={"directional_accuracy": 0.50},
    )

    assert evidence["status"] == "NEGATIVE_EXPECTANCY_AT_REFERENCE_PAYOUT"
    assert evidence["promotion_eligible"] is False
    assert evidence["reference_scenario"]["expected_value_per_unit_point"] == -0.02


def test_small_winning_sample_stays_unproven() -> None:
    evidence = PathClockLiquiditySideStoreV3._profitability_evidence(
        matured_count=25,
        candidate_metrics={"directional_accuracy": 0.80},
        baseline_metrics={"directional_accuracy": 0.50},
    )

    assert evidence["status"] == "INSUFFICIENT_FORWARD_SUPPORT"
    assert evidence["promotion_eligible"] is False
    assert evidence["minimum_forward_outcomes"] == 200
