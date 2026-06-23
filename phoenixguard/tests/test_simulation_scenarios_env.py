from __future__ import annotations
from pathlib import Path

import json

from phoenixguard.simulation.adversarial_tests.cases import (
    ADVERSARIAL_TEST_CATEGORIES,
    build_adversarial_case,
    build_adversarial_suite,
)
from phoenixguard.simulation.gym_env.market_env import (
    ACTION_BUY,
    ACTION_HOLD,
    PhoenixGuardMarketEnv,
)
from phoenixguard.simulation.synthetic_scenarios.generator import (
    export_scenarios_json,
    generate_synthetic_market_scenario,
)


def test_synthetic_scenario_generation_is_deterministic_and_dictionary_native(tmp_path: Path) -> None:
    first = generate_synthetic_market_scenario("breakout_failure", seed=42, frame_count=16)
    second = generate_synthetic_market_scenario("breakout_failure", seed=42, frame_count=16)

    assert first == second
    assert isinstance(first["frames"][0], dict)
    assert isinstance(first["labels"][0], dict)
    assert isinstance(first["expected"], dict)
    assert len(first["frames"]) == len(first["labels"]) == 16
    assert first["metadata"]["broker_actions_allowed"] is False

    export_path = export_scenarios_json(first, tmp_path / "scenario.json")
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["scenario_count"] == 1
    assert payload["scenarios"][0]["scenario_id"] == first["scenario_id"]


def test_adversarial_suite_has_required_12_categories_and_offline_expectations() -> None:
    suite = build_adversarial_suite(seed=7, frame_count=18)

    assert len(ADVERSARIAL_TEST_CATEGORIES) == 12
    assert ADVERSARIAL_TEST_CATEGORIES == (
        "steep_impulse_trap",
        "fake_breakout_trap",
        "fake_breakdown_trap",
        "range_chop_trap",
        "buy_into_supply",
        "sell_into_demand",
        "mid_range_no_edge",
        "liquidity_sweep_reversal",
        "angle_break_after_vertical_move",
        "pullback_that_becomes_full_reversal",
        "continuation_after_valid_retest",
        "middle_safe_continuation",
    )
    assert {case["category"] for case in suite} == set(ADVERSARIAL_TEST_CATEGORIES)
    assert all(case["expected"]["broker_actions_allowed"] is False for case in suite)
    assert all(case["expected"]["should_trigger_broker_action"] is False for case in suite)

    supply = build_adversarial_case("buy_into_supply", seed=7, frame_count=18)
    assert supply["expected"]["expected_gate"] == "BLOCK_BUY_INTO_SUPPLY"
    assert "opposing_zone_nearby" in supply["expected"]["risk_tags"]


def test_phoenixguard_market_env_reset_step_api_scores_offline_labels() -> None:
    trap = build_adversarial_case("fake_breakout_above_resistance", seed=9, frame_count=12)
    env = PhoenixGuardMarketEnv([trap])

    observation, info = env.reset()
    assert env.observation_space.contains(observation)
    assert info["broker_action"] is None
    assert info["offline_only"] is True

    _, hold_reward, terminated, truncated, hold_info = env.step(ACTION_HOLD)
    assert hold_reward > 0
    assert terminated is False
    assert truncated is False
    assert hold_info["broker_actions_allowed"] is False

    env.reset()
    _, buy_reward, _, _, buy_info = env.step(ACTION_BUY)
    assert buy_reward < hold_reward
    assert buy_info["broker_action"] is None


def test_phoenixguard_market_env_supports_category_selection_and_truncation() -> None:
    suite = [
        generate_synthetic_market_scenario("trend_up", seed=1, frame_count=10),
        generate_synthetic_market_scenario("trend_down", seed=2, frame_count=10),
    ]
    env = PhoenixGuardMarketEnv(suite)

    observation, info = env.reset(options={"category": "trend_down", "max_steps": 2})
    assert observation["category"] == "trend_down"
    assert info["category"] == "trend_down"

    env.step("HOLD")
    _, _, terminated, truncated, _ = env.step("HOLD")
    assert terminated is False
    assert truncated is True
