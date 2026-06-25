from __future__ import annotations

from typing import Any, Mapping

from phoenixguard.decision.decision_kernel import analyze_decision_kernel


def _assert_close(actual: float, expected: float, *, tolerance: float = 1e-9) -> None:
    assert abs(actual - expected) <= tolerance


def _assert_probability_range(result: Mapping[str, Any]) -> None:
    fields = (
        "freshness",
        "bias_strength",
        "conflict_score",
        "belief_buy",
        "belief_sell",
        "belief_hold",
        "belief_uncertainty",
        "directional_edge",
        "evidence_mass",
        "usable_bias",
        "p_trigger_next_1",
        "p_trigger_next_3",
        "p_target_before_invalidation",
        "p_expire_before_trigger",
        "hazard_trigger",
        "hazard_invalidation",
        "hazard_expiry",
        "p_next_buy",
        "p_next_sell",
        "p_next_hold",
    )
    for field in fields:
        value = float(result[field])
        assert 0.0 <= value <= 1.0, field


def _assert_probability_sum(payload: Mapping[str, Any], *, expected: float = 1.0) -> None:
    values = [float(value) for value in payload.values()]
    _assert_close(sum(values), expected)


def test_decision_kernel_arms_fresh_sell_stack_near_trigger() -> None:
    result = analyze_decision_kernel(
        {
            "pair": "GBPAUD_OTC",
            "timeframe": "M5",
            "signals": [
                {"side": "SELL", "confidence": 0.84, "quality": 0.82, "zone_type": "global", "age_candles": 3, "distance_to_trigger": 0.16},
                {"side": "SELL", "confidence": 0.87, "quality": 0.86, "zone_type": "local", "age_candles": 2, "distance_to_trigger": 0.12},
                {"side": "SELL", "confidence": 0.80, "quality": 0.78, "zone_type": "current", "age_candles": 1, "distance_to_trigger": 0.10},
                {"side": "BUY", "confidence": 0.24, "quality": 0.42, "zone_type": "opposition", "age_candles": 6, "distance_to_trigger": 0.90},
            ],
            "distances": {"trigger": 0.12, "target": 0.44, "invalidation": 0.26},
            "directional_speed": 0.12,
            "probability": {"target_first_probability": 0.62, "invalidation_first_probability": 0.20},
            "context": {
                "setup": "IMPULSE SELL",
                "entry_state": "WAIT_FOR_TRIGGER",
                "global_direction": "SELL",
                "local_direction": "SELL",
                "current_direction": "SELL",
                "setup_age_candles": 3,
                "ttl_candles": 7,
                "timing_score": 0.62,
                "persistence": 0.72,
            },
        }
    )

    assert result["dominant_side"] == "sell"
    assert result["state"] == "ARMED"
    assert result["decision"] == "WATCH_FOR_TRIGGER"
    assert float(result["sell_evidence"]) > float(result["buy_evidence"])
    assert int(result["eta_trigger_candles"]) <= 3
    assert float(result["p_trigger_next_3"]) > float(result["p_trigger_next_1"])
    assert result["next_most_likely_event"] in {"trigger", "target"}
    _assert_probability_range(result)
    _assert_probability_sum(dict(result["competing_event_probabilities"]))
    _assert_probability_sum(dict(result["target_race_probabilities"]))
    assert "STATE_ARMED" in set(result["reason_codes"])


def test_decision_kernel_marks_old_untriggered_setup_stale() -> None:
    result = analyze_decision_kernel(
        {
            "signals": [
                {"side": "BUY", "confidence": 0.78, "quality": 0.74, "zone_type": "local", "age_candles": 10, "distance_to_trigger": 0.58},
                {"side": "BUY", "confidence": 0.70, "quality": 0.70, "zone_type": "current", "age_candles": 10, "distance_to_trigger": 0.58},
            ],
            "distances": {"trigger": 0.58, "target": 0.70, "invalidation": 0.18},
            "context": {
                "setup": "CONTINUATION BUY",
                "entry_state": "WAIT_FOR_TRIGGER",
                "global_direction": "BUY",
                "local_direction": "BUY",
                "current_direction": "BUY",
                "setup_age_candles": 10,
                "ttl_candles": 6,
            },
        }
    )

    assert result["state"] == "STALE"
    assert result["decision"] == "CANCEL_SETUP"
    assert int(result["stale_after_candles"]) == 0
    assert result["next_most_likely_event"] == "stale"
    assert float(result["p_trigger_next_1"]) <= 0.08
    assert result["firewall_action"] == "WAIT"
    assert result["confidence_tier"] == "X"


def test_decision_kernel_uses_recent_memory_rows_for_duration_context() -> None:
    memory_rows: list[dict[str, Any]] = [
        {"action": "SELL", "setup": "REVERSAL ATTEMPT SELL", "candles_to_trigger": 2, "candles_to_target": 3, "triggered": True},
        {"action": "SELL", "setup": "REVERSAL ATTEMPT SELL", "candles_to_trigger": 3, "candles_to_target": 4, "triggered": True},
        {"action": "SELL", "setup": "REVERSAL ATTEMPT SELL", "candles_to_trigger": 2, "candles_to_target": 5, "triggered": True},
    ]

    result = analyze_decision_kernel(
        {
            "signals": [
                {"side": "SELL", "confidence": 0.76, "quality": 0.77, "zone_type": "local", "age_candles": 2, "distance_to_trigger": 0.20},
                {"side": "SELL", "confidence": 0.74, "quality": 0.74, "zone_type": "current", "age_candles": 1, "distance_to_trigger": 0.20},
            ],
            "distances": {"trigger": 0.20, "target": 0.62, "invalidation": 0.24},
            "directional_speed": 0.08,
            "memory_rows": memory_rows,
            "context": {
                "setup": "REVERSAL ATTEMPT SELL",
                "global_direction": "SELL",
                "local_direction": "SELL",
                "current_direction": "SELL",
                "setup_age_candles": 2,
                "ttl_candles": 7,
            },
        }
    )

    memory = dict(result["memory"])
    medians = dict(memory["median_durations"])
    assert memory["similar_setup_count"] == 3
    assert medians["candles_to_trigger"] == 2.0
    _assert_close(float(memory["memory_weight"]), 3.0 / 33.0)
    assert float(memory["memory_weight"]) < 0.10
    assert int(result["eta_trigger_candles"]) >= 1


def test_decision_kernel_separates_countertrend_scalp_from_dominant_trend() -> None:
    tokens: list[dict[str, Any]] = [
        {"direction": "BUY", "micro_structure_event": "bullish_continuation", "distance_to_trigger": 0.22, "distance_to_invalidation": 0.42},
        {"direction": "BUY", "micro_structure_event": "bullish_impulse", "distance_to_trigger": 0.18, "distance_to_invalidation": 0.48},
        {"direction": "SELL", "micro_structure_event": "exhaustion_against_bias", "distance_to_trigger": 0.30, "distance_to_invalidation": 0.16},
        {"direction": "SELL", "micro_structure_event": "reversal_attempt", "distance_to_trigger": 0.34, "distance_to_invalidation": 0.12},
        {"direction": "SELL", "micro_structure_event": "failed_breakout", "distance_to_trigger": 0.36, "distance_to_invalidation": 0.10},
    ]

    result = analyze_decision_kernel(
        {
            "signals": [
                {"side": "BUY", "confidence": 0.86, "quality": 0.84, "zone_type": "global", "age_candles": 2, "distance_to_trigger": 0.20},
                {"side": "BUY", "confidence": 0.82, "quality": 0.80, "zone_type": "local", "age_candles": 2, "distance_to_trigger": 0.20},
                {"side": "SELL", "confidence": 0.50, "quality": 0.70, "zone_type": "current", "age_candles": 0, "distance_to_trigger": 0.36},
            ],
            "distances": {"trigger": 0.34, "target": 0.62, "invalidation": 0.10},
            "candle_statistics": {
                "sample_size": 18,
                "candidate_ratio": 0.62,
                "opposing_ratio": 0.38,
                "momentum_consistency": 0.54,
                "average_step": 0.06,
            },
            "behavior": {
                "current_state": "exhaustion",
                "next_most_likely_state": "reversal_attempt",
                "candle_tokens": tokens,
                "box_context": {"failure_risk": 0.58},
            },
            "context": {
                "setup": "CONTINUATION BUY",
                "entry_state": "WATCH",
                "global_direction": "BUY",
                "local_direction": "BUY",
                "current_direction": "SELL",
                "setup_age_candles": 2,
                "ttl_candles": 8,
                "candle_tokens": tokens,
            },
        }
    )

    assert result["dominant_side"] == "buy"
    assert result["next_candle_bias"] == "sell"
    assert result["trade_mode"] == "PULLBACK_WAIT"
    assert result["candle_execution_side"] == "hold"
    assert int(result["countertrend_window_candles"]) == 0
    assert result["micro_pullback_against_major"] is True
    assert "pullback" in str(result["candle_instruction"]).lower()


def test_decision_kernel_only_opens_countertrend_lane_when_explicitly_enabled() -> None:
    tokens: list[dict[str, Any]] = [
        {"direction": "BUY", "micro_structure_event": "bullish_continuation", "distance_to_trigger": 0.22, "distance_to_invalidation": 0.42},
        {"direction": "BUY", "micro_structure_event": "bullish_impulse", "distance_to_trigger": 0.18, "distance_to_invalidation": 0.48},
        {"direction": "SELL", "micro_structure_event": "exhaustion_against_bias", "distance_to_trigger": 0.30, "distance_to_invalidation": 0.16},
        {"direction": "SELL", "micro_structure_event": "reversal_attempt", "distance_to_trigger": 0.34, "distance_to_invalidation": 0.12},
        {"direction": "SELL", "micro_structure_event": "failed_breakout", "distance_to_trigger": 0.36, "distance_to_invalidation": 0.10},
    ]

    result = analyze_decision_kernel(
        {
            "signals": [
                {"side": "BUY", "confidence": 0.86, "quality": 0.84, "zone_type": "global", "age_candles": 2, "distance_to_trigger": 0.20},
                {"side": "BUY", "confidence": 0.82, "quality": 0.80, "zone_type": "local", "age_candles": 2, "distance_to_trigger": 0.20},
                {"side": "SELL", "confidence": 0.50, "quality": 0.70, "zone_type": "current", "age_candles": 0, "distance_to_trigger": 0.36},
            ],
            "distances": {"trigger": 0.34, "target": 0.62, "invalidation": 0.10},
            "candle_statistics": {
                "sample_size": 18,
                "candidate_ratio": 0.62,
                "opposing_ratio": 0.38,
                "momentum_consistency": 0.54,
                "average_step": 0.06,
            },
            "behavior": {
                "current_state": "exhaustion",
                "next_most_likely_state": "reversal_attempt",
                "candle_tokens": tokens,
                "box_context": {"failure_risk": 0.58},
            },
            "context": {
                "setup": "CONTINUATION BUY",
                "entry_state": "WATCH",
                "global_direction": "BUY",
                "local_direction": "BUY",
                "current_direction": "SELL",
                "major_trend_side": "BUY",
                "major_trend_confidence": 0.72,
                "allow_countertrend_scalp": True,
                "setup_age_candles": 2,
                "ttl_candles": 8,
                "candle_tokens": tokens,
            },
        }
    )

    assert result["dominant_side"] == "buy"
    assert result["trade_mode"] == "COUNTERTREND_SCALP"
    assert result["candle_execution_side"] == "sell"
    assert result["countertrend_scalp_enabled"] is True


def test_decision_kernel_marks_next_candle_trend_follow_when_micro_agrees() -> None:
    tokens: list[dict[str, Any]] = [
        {"direction": "BUY", "micro_structure_event": "bullish_rejection", "distance_to_trigger": 0.10, "distance_to_invalidation": 0.52},
        {"direction": "BUY", "micro_structure_event": "bullish_impulse", "distance_to_trigger": 0.06, "distance_to_invalidation": 0.58},
        {"direction": "BUY", "micro_structure_event": "bullish_continuation", "distance_to_trigger": 0.03, "distance_to_invalidation": 0.62},
    ]

    result = analyze_decision_kernel(
        {
            "signals": [
                {"side": "BUY", "confidence": 0.88, "quality": 0.86, "zone_type": "local", "age_candles": 1, "distance_to_trigger": 0.06},
                {"side": "BUY", "confidence": 0.84, "quality": 0.82, "zone_type": "current", "age_candles": 0, "distance_to_trigger": 0.06},
            ],
            "distances": {"trigger": 0.06, "target": 0.44, "invalidation": 0.58},
            "candle_statistics": {
                "sample_size": 14,
                "candidate_ratio": 0.86,
                "opposing_ratio": 0.14,
                "momentum_consistency": 0.82,
                "average_step": 0.08,
            },
            "behavior": {
                "current_state": "bullish_continuation",
                "next_most_likely_state": "bullish_continuation",
                "candle_tokens": tokens,
                "box_context": {"failure_risk": 0.12},
            },
            "context": {
                "setup": "CONTINUATION BUY",
                "entry_state": "WAIT_FOR_TRIGGER",
                "global_direction": "BUY",
                "local_direction": "BUY",
                "current_direction": "BUY",
                "setup_age_candles": 1,
                "ttl_candles": 8,
                "candle_tokens": tokens,
            },
        }
    )

    assert result["next_candle_bias"] == "buy"
    assert result["trade_mode"] == "TREND_FOLLOW"
    assert result["candle_execution_side"] == "buy"
    assert int(result["trend_follow_window_candles"]) >= 10
    assert int(result["hold_for_candles"]) == int(result["target_horizon_candles"])


def test_decision_kernel_terminal_states_force_safe_invariants() -> None:
    result = analyze_decision_kernel(
        {
            "signals": [
                {"side": "BUY", "confidence": 1.4, "quality": 1.2, "zone_type": "local", "age_candles": 1, "distance_to_trigger": -2},
                {"side": "SELL", "confidence": "0.8", "quality": "0.7", "zone_type": "opposition", "age_candles": 1, "distance_to_trigger": 0.01},
            ],
            "distances": {"trigger": -1.0, "target": 9.0, "invalidation": 0.0},
            "context": {
                "setup": "IMPULSE BUY",
                "entry_state": "INVALIDATED",
                "global_direction": "BUY",
                "local_direction": "BUY",
                "current_direction": "BUY",
                "setup_age_candles": 1,
                "ttl_candles": 8,
            },
        }
    )

    _assert_probability_range(result)
    _assert_probability_sum(dict(result["next_event_likelihoods"]))
    _assert_probability_sum(dict(result["competing_event_probabilities"]))
    assert float(result["p_trigger_next_1"]) == 0.0
    assert float(result["p_trigger_next_3"]) == 0.0
    assert result["state"] == "INVALIDATED"
    assert result["next_most_likely_event"] == "invalidation"
    assert result["candle_execution_side"] == "hold"
    assert result["confidence_tier"] == "X"


def test_decision_kernel_high_sample_memory_has_stronger_shrinkage_weight() -> None:
    memory_rows: list[dict[str, Any]] = [
        {
            "action": "BUY",
            "setup": "CONTINUATION BUY",
            "candles_to_trigger": 1 + (index % 3),
            "candles_to_target": 3 + (index % 4),
            "target_before_invalidation": index % 5 != 0,
            "triggered": True,
        }
        for index in range(100)
    ]

    result = analyze_decision_kernel(
        {
            "signals": [
                {"side": "BUY", "confidence": 0.78, "quality": 0.78, "zone_type": "local", "age_candles": 2, "distance_to_trigger": 0.22},
                {"side": "BUY", "confidence": 0.72, "quality": 0.72, "zone_type": "current", "age_candles": 1, "distance_to_trigger": 0.22},
            ],
            "distances": {"trigger": 0.22, "target": 0.58, "invalidation": 0.24},
            "memory_rows": memory_rows,
            "context": {
                "setup": "CONTINUATION BUY",
                "global_direction": "BUY",
                "local_direction": "BUY",
                "current_direction": "BUY",
                "setup_age_candles": 2,
                "ttl_candles": 8,
            },
        }
    )

    memory = dict(result["memory"])
    assert memory["similar_setup_count"] == 100
    _assert_close(float(memory["memory_weight"]), 100.0 / 130.0)
    assert float(memory["memory_confidence"]) > 0.70


def test_decision_kernel_expected_utility_penalizes_cost_and_uncertainty() -> None:
    base_snapshot: dict[str, Any] = {
        "signals": [
            {"side": "SELL", "confidence": 0.86, "quality": 0.84, "zone_type": "local", "age_candles": 1, "distance_to_trigger": 0.08},
            {"side": "SELL", "confidence": 0.82, "quality": 0.80, "zone_type": "current", "age_candles": 0, "distance_to_trigger": 0.08},
        ],
        "distances": {"trigger": 0.08, "target": 0.60, "invalidation": 0.24},
        "probability": {"target_first_probability": 0.68, "invalidation_first_probability": 0.22},
        "context": {
            "setup": "IMPULSE SELL",
            "entry_state": "TRIGGER_READY",
            "global_direction": "SELL",
            "local_direction": "SELL",
            "current_direction": "SELL",
            "setup_age_candles": 1,
            "ttl_candles": 7,
            "reward_R": 2.0,
            "loss_R": 1.0,
            "cost_R": 0.02,
        },
    }
    clean = analyze_decision_kernel(base_snapshot)
    expensive = analyze_decision_kernel(
        {
            **base_snapshot,
            "box_context": {"failure_risk": 0.72},
            "context": {
                **dict(base_snapshot["context"]),
                "cost_R": 0.80,
                "drawdown_penalty_R": 0.45,
            },
        }
    )

    assert float(clean["raw_expected_value_R"]) > 0.0
    assert float(clean["expected_value_R"]) > float(expensive["expected_value_R"])
    assert float(expensive["uncertainty_tax_R"]) > float(clean["uncertainty_tax_R"])
