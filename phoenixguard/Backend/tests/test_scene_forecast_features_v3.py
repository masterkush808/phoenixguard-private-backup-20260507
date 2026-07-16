from __future__ import annotations

import math
from typing import Any, cast

from phoenixguard.decision.scene_forecast_features_v3 import (
    CANDLE_CATEGORICAL_SCHEMA,
    CANDLE_NUMERIC_SCHEMA,
    CONTEXT_CATEGORICAL_SCHEMA,
    CONTEXT_NUMERIC_SCHEMA,
    MAX_CANDLES,
    SCHEMA_FINGERPRINT,
    SCHEMA_VERSION,
    extract_scene_forecast_features_v3,
)


def _candles() -> list[dict[str, Any]]:
    return [
        {
            "timestamp": 1_700_000_000 + index * 300,
            "open": 1.1000 + index * 0.0010,
            "high": 1.1020 + index * 0.0010,
            "low": 1.0990 + index * 0.0010,
            "close": 1.1010 + index * 0.0010,
            "direction": "BUY",
            "parse_confidence": 0.90,
            "bbox": [10 + index * 12, 40 - index * 2, 16 + index * 12, 70 - index * 2],
        }
        for index in range(4)
    ]


def _full_payload() -> dict[str, Any]:
    return {
        "candles": _candles(),
        "projection": {
            "direction": "BUY",
            "confidence": 0.78,
            "dominance": 0.21,
            "message": "free text must never enter the tensor",
            "zones": [
                {
                    "kind": "primary",
                    "direction": "BUY",
                    "confidence": 0.74,
                    "bbox": [44, 26, 80, 46],
                    "target_bbox": [44, 10, 80, 24],
                    "path": [[40, 42], [60, 30]],
                },
                {"kind": "alternate", "direction": "SELL", "confidence": 0.52},
            ],
            "future_return_12": 999.0,
        },
        "candle_statistics": {
            "sample_size": 3,
            "sample_weight": 0.72,
            "buy_count": 2,
            "sell_count": 1,
            "buy_ratio": 2 / 3,
            "sell_ratio": 1 / 3,
            "recent_buy_count": 2,
            "recent_sell_count": 1,
            "recent_buy_ratio": 2 / 3,
            "recent_sell_ratio": 1 / 3,
            "direction_run": 2,
            "opposite_run": 0,
            "candidate_ratio": 2 / 3,
            "opposing_ratio": 1 / 3,
            "momentum_consistency": 0.67,
            "normalized_volatility": 0.31,
            "average_step": 0.015,
        },
        "behavior_payload": {
            "current_state": "bullish_continuation",
            "previous_state": "bullish_pullback",
            "next_most_likely_state": "bullish_continuation",
            "state_confidence": 0.73,
            "trend_phase": "trend_expansion",
            "move_quality": "clean",
            "next_state_probs": {
                "bullish_continuation": 0.55,
                "bearish_reversal": 0.10,
                "sideways_pause": 0.35,
            },
            "behavior_counts": {
                "rejection_count": 1,
                "compression_count": 2,
                "impulse_count": 3,
                "pullback_count": 1,
                "pause_count": 1,
                "exhaustion_count": 0,
                "reversal_count": 0,
            },
            "box_context": {
                "box_type": "sniper_buy",
                "candles_seen_in_box": 2,
                "entry_quality": 0.71,
                "rejection_count": 1,
                "acceptance_count": 2,
                "compression_score": 0.32,
                "momentum_exit_score": 0.69,
                "failure_risk": 0.18,
                "behavior_state": "respecting_buy_zone",
            },
            "trend_context": {
                "global_bias": "BUY",
                "local_bias": "BUY",
                "micro_bias": "BUY",
                "slope_global": 0.04,
                "slope_local": 0.06,
                "slope_current": 0.05,
                "trend_strength": 0.66,
                "recent_range": 0.22,
            },
        },
        "decision_kernel": {
            "dominant_side": "BUY",
            "major_trend_side": "BUY",
            "state": "ARMED",
            "next_most_likely_event": "trigger",
            "confidence_tier": "HIGH",
            "firewall_action": "ALLOW",
            "decision": "WATCH",
            "next_candle_bias": "BUY",
            "trade_mode": "TREND_FOLLOW",
            "candle_execution_side": "BUY",
            "countertrend_side": "SELL",
            "bias_strength": 0.71,
            "setup_age_candles": 2,
            "freshness": 0.91,
            "structure_alignment": 0.82,
            "buy_evidence": 0.75,
            "sell_evidence": 0.22,
            "net_bias": 0.53,
            "conflict_score": 0.14,
            "belief_buy": 0.72,
            "belief_sell": 0.16,
            "belief_hold": 0.12,
            "belief_uncertainty": 0.24,
            "belief_conflict": 0.12,
            "directional_edge": 0.56,
            "evidence_mass": 0.80,
            "usable_bias": 0.68,
            "distance_to_trigger": 0.08,
            "distance_to_target": 0.31,
            "distance_to_invalidation": 0.14,
            "eta_trigger_candles": 1,
            "eta_target_after_trigger_candles": 4,
            "eta_invalidation_candles": 3,
            "target_horizon_candles": 12,
            "stale_after_candles": 6,
            "p_trigger_next_1": 0.64,
            "p_trigger_next_3": 0.86,
            "p_target_before_invalidation": 0.69,
            "p_expire_before_trigger": 0.11,
            "hazard_trigger": 0.62,
            "hazard_invalidation": 0.21,
            "hazard_expiry": 0.17,
            "expected_value_R": 0.42,
            "raw_expected_value_R": 0.55,
            "uncertainty_tax_R": 0.13,
            "reward_R": 1.4,
            "loss_R": 1.0,
            "cost_R": 0.04,
            "major_trend_confidence": 0.77,
            "p_next_buy": 0.68,
            "p_next_sell": 0.18,
            "p_next_hold": 0.14,
            "countertrend_window_candles": 2,
            "trend_follow_window_candles": 12,
            "hold_for_candles": 12,
            "ground_truth_direction": "BUY",
        },
        "smart_money_context": {
            "dominant_side": "BUY",
            "confidence": 0.76,
            "decision_adjustment": {"side": "BUY", "confidence_delta": 0.08, "risk_delta": -0.04},
            "order_blocks": [
                {
                    "direction": "BUY",
                    "confidence": 0.81,
                    "age_candles": 3,
                    "mitigated": False,
                    "mitigation_state": "fresh_unmitigated",
                }
            ],
            "fair_value_gaps": [],
            "liquidity_sweeps": [{"direction": "BUY", "confidence": 0.67, "age_candles": 1}],
            "liquidity_pools": [{"direction": "SELL", "confidence": 0.61, "age_candles": 4}],
            "market_structure_shift": {"direction": "BUY", "confidence": 0.72},
            "summary": "unstructured prose is rejected",
        },
        "support_resistance_context": {
            "significant_count": 2,
            "institutional_zone_count": 1,
            "fresh_zone_count": 1,
            "reference_zone_count": 1,
            "active_authority_count": 1,
            "dominant_side": "BUY",
            "candidate_side": "BUY",
            "buy_structure_score": 0.77,
            "sell_structure_score": 0.28,
        },
        "support_resistance_zones": [
            {
                "role": "support",
                "direction": "BUY",
                "confidence": 0.82,
                "significance_score": 0.79,
                "distance_to_latest_norm": 0.04,
                "touch_count": 3,
                "age_candles": 5,
                "freshness_state": "FRESH",
                "entry_authority_allowed": True,
                "institutional_zone_score": 0.68,
            },
            {
                "role": "resistance",
                "direction": "SELL",
                "confidence": 0.71,
                "significance_score": 0.73,
                "distance_to_latest_norm": 0.15,
                "touch_count": 2,
                "age_candles": 8,
                "freshness_state": "TESTED_ONCE",
                "entry_authority_allowed": False,
                "institutional_zone_score": 0.41,
            },
        ],
        "trend_slopes": {"global": 0.04, "local": 0.06, "current": 0.05, "impulse": 0.08},
        "trend_directions": {"global": "BUY", "local": "BUY", "current": "BUY", "impulse": "BUY"},
        "timeframe": "M5",
        "pair": "EUR/USD OTC",
    }


def _all_finite(values: Any) -> bool:
    if isinstance(values, list):
        return all(_all_finite(value) for value in cast(list[object], values))
    return not isinstance(values, float) or math.isfinite(values)


def test_scene_contract_is_stable_causal_and_tensor_ready() -> None:
    result = extract_scene_forecast_features_v3(**_full_payload())

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["schema_fingerprint"] == SCHEMA_FINGERPRINT
    assert result["contract"]["causal_cut"] == "CLOSED_CANDLES_ONLY"
    assert result["context"]["numeric_schema"] == list(CONTEXT_NUMERIC_SCHEMA)
    assert result["context"]["categorical_schema"] == list(CONTEXT_CATEGORICAL_SCHEMA)
    assert result["sequence"]["numeric_schema"] == list(CANDLE_NUMERIC_SCHEMA)
    assert result["sequence"]["categorical_schema"] == list(CANDLE_CATEGORICAL_SCHEMA)
    assert len(result["context"]["numeric_values"]) == len(CONTEXT_NUMERIC_SCHEMA)
    assert len(result["context"]["categorical_values"]) == len(CONTEXT_CATEGORICAL_SCHEMA)
    assert len(result["sequence"]["numeric_rows"]) == 3
    assert result["sequence"]["source_indices"] == [0, 1, 2]
    assert all(len(row) == len(CANDLE_NUMERIC_SCHEMA) for row in result["sequence"]["numeric_rows"])
    assert _all_finite(result["context"]["numeric_values"])
    assert _all_finite(result["sequence"]["numeric_rows"])
    assert result["context"]["categorical_by_name"]["pair"] == "EUR_USD_OTC"
    assert result["context"]["categorical_by_name"]["projection.direction"] == "BUY"
    assert result["audit"]["causal_exclusions"]["forming_candles"] == 1


def test_scene_contract_audits_consumed_missing_and_rejected_fields() -> None:
    payload = _full_payload()
    payload["candle_statistics"].pop("average_step")
    result = extract_scene_forecast_features_v3(**payload)
    audit = result["audit"]

    assert audit["consumed_field_count"] == len(audit["consumed_fields"])
    assert audit["missing_field_count"] == len(audit["missing_fields"])
    assert audit["rejected_field_count"] == len(audit["rejected_fields"])
    assert "candle_statistics.buy_ratio" in audit["consumed_fields"]
    assert "candle_statistics.average_step" in audit["missing_fields"]
    assert result["context"]["numeric_by_name"]["candle_statistics.average_step__missing"] == 1.0
    rejected = {(item["path"], item["reason"]) for item in audit["rejected_fields"]}
    assert (
        "projection.zones[0].target_bbox",
        "forward_projection_geometry_not_observed",
    ) in rejected
    assert (
        "projection.zones[0].path",
        "forward_projection_geometry_not_observed",
    ) in rejected
    assert ("projection.future_return_12", "future_or_outcome_field") in rejected
    assert ("decision_kernel.ground_truth_direction", "future_or_outcome_field") in rejected
    assert not any("future_return_12" in path for path in audit["consumed_fields"])
    assert "free text must never enter the tensor" not in result["context"]["categorical_values"]


def test_scene_contract_is_independent_of_mapping_insertion_order() -> None:
    first_payload = _full_payload()
    second_payload: dict[str, Any] = {
        key: dict(reversed(list(cast(dict[str, Any], value).items())))
        if isinstance(value, dict)
        else value
        for key, value in reversed(list(first_payload.items()))
    }

    first = extract_scene_forecast_features_v3(**first_payload)
    second = extract_scene_forecast_features_v3(**second_payload)

    assert first["schema_fingerprint"] == second["schema_fingerprint"]
    assert first["context"] == second["context"]
    assert first["sequence"] == second["sequence"]
    assert first["audit"] == second["audit"]


def test_scene_contract_clips_nonfinite_values_and_bounds_history() -> None:
    candles = [
        {
            "open": 1.0 + index * 0.001,
            "high": 1.01 + index * 0.001,
            "low": 0.99 + index * 0.001,
            "close": 1.005 + index * 0.001,
            "is_closed": True,
        }
        for index in range(MAX_CANDLES + 2)
    ]
    result = extract_scene_forecast_features_v3(
        candles=candles,
        projection={"confidence": float("inf"), "direction": "SELL"},
        candle_statistics={"sample_size": 10**20, "sample_weight": float("nan")},
        timeframe="M5",
        pair="GBP/JPY OTC",
    )

    assert len(result["sequence"]["numeric_rows"]) == MAX_CANDLES
    assert result["sequence"]["source_indices"][0] == 2
    assert result["context"]["numeric_by_name"]["candle_statistics.sample_size"] == 1_000_000.0
    assert result["context"]["numeric_by_name"]["projection.confidence__missing"] == 1.0
    assert result["context"]["numeric_by_name"]["candle_statistics.sample_weight__missing"] == 1.0
    assert _all_finite(result["context"]["numeric_values"])
    assert _all_finite(result["sequence"]["numeric_rows"])
    rejected = {(item["path"], item["reason"]) for item in result["audit"]["rejected_fields"]}
    assert ("candles[0]", "outside_bounded_history_window") in rejected
    assert ("candles[1]", "outside_bounded_history_window") in rejected
    assert ("projection.confidence", "non_finite_or_invalid_numeric") in rejected


def test_scene_contract_accepts_pixel_ohlc_without_future_candle() -> None:
    candles = [
        {
            "open_y_px": 80 - index * 3,
            "close_y_px": 74 - index * 3,
            "wick_top_px": 70 - index * 3,
            "wick_bottom_px": 84 - index * 3,
            "direction": "BUY",
            "bbox": [index * 10, 70 - index * 3, index * 10 + 6, 84 - index * 3],
            "is_closed": index < 3,
        }
        for index in range(4)
    ]

    result = extract_scene_forecast_features_v3(candles=candles, timeframe="M5", pair="NZD/USD OTC")

    assert len(result["sequence"]["numeric_rows"]) == 3
    assert all(row[1] == "PIXEL_OHLC" for row in result["sequence"]["categorical_rows"])
    assert all(row[0] == "BUY" for row in result["sequence"]["categorical_rows"])
    assert result["audit"]["causal_exclusions"]["forming_candles"] == 1
