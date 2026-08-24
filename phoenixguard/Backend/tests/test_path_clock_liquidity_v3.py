from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

import pytest

from phoenixguard.study.path_clock_liquidity_v3 import (
    MAX_STUDIED_DURATION_SECONDS,
    PATH_CLOCK_REPLAY_SCORE_SCHEMA_VERSION,
    JointPathClockLiquidityFieldV3,
    PathClockLiquidityValidationError,
    build_hierarchical_forward_timing_forecast_v3,
    evaluate_path_clock_promotion_gate_v3,
    score_path_clock_replays_v3,
)


SCOPE: dict[str, Any] = {
    "symbol": "USD/CAD OTC",
    "timeframe": "M5",
    "coordinate_space": "NORMALIZED_MEDIAN_RANGE",
    "order_domain": "SOURCE_CANDLE_CLOSE_ORDER",
}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _field(**overrides: Any) -> JointPathClockLiquidityFieldV3:
    config: dict[str, Any] = {
        **SCOPE,
        "clock_step_seconds": 300,
        "max_points_per_trajectory": 25,
        "max_trajectories": 8,
        "max_freezes": 8,
        "max_neighbors": 8,
    }
    config.update(overrides)
    return JointPathClockLiquidityFieldV3(**config)


def _liquidity(
    *,
    order_index: int = 10,
    as_of_seconds: float = 10_000.0,
    forming: bool = False,
) -> dict[str, Any]:
    return {
        "wick_entropy": 0.35,
        "repeated_area_touches": 3,
        "late_sweep_motif_distance": 0.2,
        "wick_body_asymmetry": -0.4,
        "object_copresence_density": 0.6,
        "as_of_order_index": order_index,
        "as_of_seconds": as_of_seconds,
        "wick_body_asymmetry_source": (
            "FORMING_CANDLE_AS_OF_CUTOFF" if forming else "CLOSED_CANDLE"
        ),
        "source_candle_closed": not forming,
        "frozen_before_outcome": True,
    }


def _trajectory(
    trajectory_id: str,
    paths: list[tuple[int, float, float, float]],
    *,
    duration_seconds: int = 1_200,
    direction: str = "UP",
    liquidity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **SCOPE,
        "trajectory_id": trajectory_id,
        "study_only": True,
        "completed": True,
        "anchor": {
            "closed_candle_key": f"C-{trajectory_id}",
            "order_index": 10,
            "closed_at_seconds": 10_000.0,
            "closed": True,
        },
        "duration_seconds": duration_seconds,
        "source_cadence_seconds": 300,
        "exact_subcandle_timestamps_proven": False,
        "studied_direction": direction,
        "liquidity_state": liquidity or _liquidity(),
        "points": [
            {
                "elapsed_seconds": elapsed,
                "path_mru": path,
                "high_mru": high,
                "low_mru": low,
            }
            for elapsed, path, high, low in paths
        ],
    }


def _winning_path() -> list[tuple[int, float, float, float]]:
    return [
        (0, 0.0, 0.0, 0.0),
        (300, 0.1, 0.2, -0.1),
        (600, 0.35, 0.4, 0.0),
        (900, 0.8, 0.9, 0.6),
        (1_200, 1.0, 1.1, 0.75),
    ]


def _swept_path() -> list[tuple[int, float, float, float]]:
    return [
        (0, 0.0, 0.0, 0.0),
        (300, -0.45, 0.05, -0.55),
        (600, 0.2, 0.3, -0.4),
        (900, 0.7, 0.8, 0.1),
        (1_200, 0.9, 1.0, 0.65),
    ]


def test_scope_requires_median_range_and_stable_closed_order() -> None:
    with pytest.raises(PathClockLiquidityValidationError, match="coordinate_space"):
        _field(coordinate_space="PRICE")
    with pytest.raises(PathClockLiquidityValidationError, match="order_domain"):
        _field(order_domain="")
    with pytest.raises(PathClockLiquidityValidationError, match="stable"):
        _field(order_domain="SCREEN_ARRIVAL_ORDER")


def test_trajectory_excludes_under_15_minutes_and_above_policy_horizon() -> None:
    field = _field()
    too_short = _trajectory(
        "SHORT",
        [(0, 0.0, 0.0, 0.0), (899, 0.5, 0.6, -0.1)],
        duration_seconds=899,
    )
    with pytest.raises(PathClockLiquidityValidationError, match=">= 900"):
        field.add_trajectory(too_short)

    too_long = _trajectory(
        "LONG",
        [
            (0, 0.0, 0.0, 0.0),
            (MAX_STUDIED_DURATION_SECONDS + 1, 0.5, 0.6, -0.1),
        ],
        duration_seconds=MAX_STUDIED_DURATION_SECONDS + 1,
    )
    with pytest.raises(PathClockLiquidityValidationError, match="cannot exceed"):
        field.add_trajectory(too_long)


def test_forming_asymmetry_requires_pre_outcome_causal_freeze() -> None:
    field = _field()
    accepted: dict[str, Any] = field.add_trajectory(
        _trajectory("FORMING-OK", _winning_path(), liquidity=_liquidity(forming=True))
    )
    assert accepted["liquidity_state"]["wick_body_asymmetry_source"] == (
        "FORMING_CANDLE_AS_OF_CUTOFF"
    )
    assert accepted["closed_candle_causal"] is True

    leaked = _liquidity(forming=True)
    leaked["as_of_seconds"] = 10_001.0
    with pytest.raises(PathClockLiquidityValidationError, match="causal cutoff"):
        field.add_trajectory(
            _trajectory("FORMING-LEAK", _winning_path(), liquidity=leaked)
        )
    missing_proof = _liquidity(forming=True)
    missing_proof["frozen_before_outcome"] = False
    with pytest.raises(PathClockLiquidityValidationError, match="causal freeze"):
        field.add_trajectory(
            _trajectory("FORMING-NO-PROOF", _winning_path(), liquidity=missing_proof)
        )


def test_common_clock_grid_preserves_excursions_and_joint_distribution() -> None:
    field = _field()
    stored: dict[str, Any] = field.add_trajectory(_trajectory("WIN", _winning_path()))
    assert [point["elapsed_seconds"] for point in stored["points"]] == [
        0,
        300,
        600,
        900,
        1_200,
    ]
    assert stored["points"][1]["path_mru"] == pytest.approx(0.1)
    assert stored["maximum_adverse_excursion_mru"] == pytest.approx(0.1)
    assert stored["maximum_favorable_excursion_mru"] == pytest.approx(1.1)
    assert stored["final_direction"] == "UP"

    distribution: dict[str, Any] = field.joint_clock_distribution()
    assert distribution["clock_step_seconds"] == 300
    assert distribution["row_count"] == 5
    assert distribution["rows"][2]["support_count"] == 1
    assert distribution["rows"][4]["final_direction_counts"] == {
        "UP": 1,
        "DOWN": 0,
        "FLAT": 0,
    }
    assert distribution["execution_authority"] is False


def test_joint_distribution_keeps_duration_remaining_clock_and_liquidity_axes() -> None:
    field = _field()
    field.add_trajectory(_trajectory("D1200-A", _winning_path()))

    different_liquidity = _liquidity()
    different_liquidity["wick_entropy"] = 0.95
    field.add_trajectory(
        _trajectory(
            "D1200-B",
            _winning_path(),
            liquidity=different_liquidity,
        )
    )
    long_path = [
        *_winning_path(),
        (1_500, 0.85, 1.0, 0.7),
        (1_800, 1.2, 1.3, 0.8),
    ]
    field.add_trajectory(
        _trajectory(
            "D1800-B",
            long_path,
            duration_seconds=1_800,
            liquidity=different_liquidity,
        )
    )

    distribution: dict[str, Any] = field.joint_clock_distribution()
    same_remaining = [
        row for row in distribution["rows"] if row["remaining_seconds"] == 1_200
    ]

    assert len(same_remaining) == 3
    assert {row["contract_duration_seconds"] for row in same_remaining} == {
        1_200,
        1_800,
    }
    duration_1200 = [
        row
        for row in same_remaining
        if row["contract_duration_seconds"] == 1_200
    ]
    assert len(duration_1200) == 2
    assert len(
        {row["liquidity_bin"]["wick_entropy"] for row in duration_1200}
    ) == 2
    assert all(row["support_count"] == 1 for row in same_remaining)


def test_stop_survival_joint_query_observes_event_order_and_never_authorizes() -> None:
    field = _field()
    field.add_trajectory(_trajectory("WIN", _winning_path()))
    field.add_trajectory(_trajectory("SWEPT", _swept_path()))
    result: dict[str, Any] = field.estimate_stop_survival(
        studied_direction="UP",
        contract_duration_seconds=1_200,
        elapsed_seconds=0,
        current_path_mru=0.0,
        stop_distance_mru=0.3,
        move_size_mru=0.5,
        liquidity_state=_liquidity(),
        causal_order_index=10,
        causal_cutoff_seconds=10_000.0,
        minimum_support=2,
    )
    assert result["status"] == "STUDIED"
    assert result["support_count"] == 2
    assert result["survival_probability"] == pytest.approx(0.5)
    evidence = {
        row["trajectory_id"]: row["target_before_stop"]
        for row in result["neighbor_evidence"]
    }
    assert evidence == {"SWEPT": False, "WIN": True}
    assert result["execution_authority"] is False
    assert result["broker_click_authority"] is False
    assert result["grants_entry_permission"] is False


def test_late_clock_keeps_history_visible_without_public_studied_eligibility() -> None:
    field = _field()
    field.add_trajectory(_trajectory("WIN", _winning_path()))
    result: dict[str, Any] = field.estimate_stop_survival(
        studied_direction="UP",
        contract_duration_seconds=1_200,
        elapsed_seconds=301,
        current_path_mru=0.1,
        stop_distance_mru=0.3,
        move_size_mru=0.5,
        liquidity_state=_liquidity(),
        causal_order_index=10,
        causal_cutoff_seconds=10_000.0,
    )
    assert result["status"] == "ACTIVE_TRACKING_ONLY"
    assert result["eligible"] is False
    assert result["historical_estimate_available"] is True
    assert result["contract_admitted"] is True
    assert result["new_entry_eligible"] is False
    assert result["remaining_seconds"] == 899
    assert result["survival_probability"] == 1.0


def test_worst_drawdown_probability_tracks_global_event_after_live_clock() -> None:
    field = _field()
    field.add_trajectory(
        _trajectory(
            "WORST-BEFORE",
            [
                (0, 0.0, 0.0, 0.0),
                (300, -1.0, 0.0, -1.0),
                (600, 0.0, 0.1, -0.1),
                (900, -0.2, 0.1, -0.2),
                (1_200, 0.8, 0.8, -0.1),
            ],
        )
    )
    result: dict[str, Any] = field.estimate_stop_survival(
        studied_direction="UP",
        contract_duration_seconds=1_200,
        elapsed_seconds=600,
        current_path_mru=0.0,
        stop_distance_mru=0.3,
        move_size_mru=0.5,
        liquidity_state=_liquidity(),
        causal_order_index=10,
        causal_cutoff_seconds=10_000.0,
    )

    assert result["probability_worst_drawdown_still_ahead"] == 0.0
    assert result["neighbor_evidence"][0]["global_worst_drawdown_index"] == 1
    assert result["neighbor_evidence"][0]["worst_drawdown_still_ahead"] is False


def test_forward_forecast_normalizes_live_pair_dna_coordinate_prefixes() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="SELL",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.72,
        current_regime="DOWNTREND",
        current_behavior={
            "status": "STUDIED",
            "candle_count": 28,
            "current_state": {"state": "REST", "candle_count": 1},
            "swing_summary": {
                "up": {"average_candles": 1.0},
                "down": {"average_candles": 1.6667},
            },
            "rest_summary": {"average_candles": 1.0},
        },
        pair_profile={
            "observation_count": 14,
            "candle_count": 22,
            "behavior": {
                "segment_counts": {
                    "PIXEL_PRICE_PROXY|DOWN_SWING": 3,
                    "PIXEL_PRICE_PROXY|REST": 3,
                    "PIXEL_PRICE_PROXY|UP_SWING": 1,
                },
                "segment_averages": {
                    "PIXEL_PRICE_PROXY|DOWN_SWING": {
                        "candles": 1.6667,
                        "duration_seconds": 500.0,
                    },
                    "PIXEL_PRICE_PROXY|REST": {
                        "candles": 1.0,
                        "duration_seconds": 300.0,
                    },
                    "PIXEL_PRICE_PROXY|UP_SWING": {
                        "candles": 1.0,
                        "duration_seconds": 300.0,
                    },
                },
                "transition_counts": {
                    "PIXEL_PRICE_PROXY|REST->DOWN_SWING": 3,
                    "PIXEL_PRICE_PROXY|REST->UP_SWING": 1,
                },
                "transition_probabilities": {
                    "PIXEL_PRICE_PROXY|REST->DOWN_SWING": 0.75,
                    "PIXEL_PRICE_PROXY|REST->UP_SWING": 0.25,
                },
            },
        },
    )

    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["candidate_direction"] == "DOWN"
    assert forecast["forecast_horizon_seconds"] == 3_000
    assert forecast["directional_model"]["score"] == pytest.approx(0.72)
    assert forecast["timing_estimate"]["source_tier"] == "PAIR"
    assert forecast["timing_estimate"]["support_count"] == 3
    assert forecast["probability"]["source_tier"] == "NONE"
    assert forecast["probability"]["support_count"] == 0
    assert forecast["probability"]["value"] is None
    assert forecast["probability"]["confidence"] is None
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["evidence_confidence"]["value"] is None
    assert forecast["state_transition_estimate"]["value"] == pytest.approx(0.75)
    assert forecast["state_transition_estimate"]["support_count"] == 4
    assert forecast["state_transition_estimate"]["is_directional_likelihood"] is False
    assert forecast["expected_pre_move"]["sweep_probability"] is None
    assert forecast["stop_survival"]["value"] is None
    assert forecast["move_window"]["earliest"]["candles"] >= 3
    assert forecast["move_window"]["latest"]["candles"] <= 10
    assert forecast["move_window"]["exact_wall_clock_proven"] is False
    assert forecast["enter_now"]["permission"] is False
    pair_tier = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "PAIR"
    )
    assert pair_tier["available"] is True


def test_forward_forecast_uses_live_m5_sequence_before_neutral_prior() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.6,
        current_regime="UPTREND",
        current_behavior={
            "status": "STUDIED",
            "candle_count": 8,
            "current_state": {"state": "REST", "candle_count": 1},
                "swing_summary": {
                    "up": {"average_candles": 2.0, "segment_count": 3},
                    "down": {"average_candles": 1.0, "segment_count": 2},
                },
            "rest_summary": {"average_candles": 1.0, "segment_count": 1},
        },
        pair_profile={},
    )

    assert forecast["timing_estimate"]["source_tier"] == "LIVE_M5_SEQUENCE"
    assert forecast["timing_estimate"]["support_count"] == 0
    assert forecast["timing_estimate"]["empirical_timing_evidence"] is False
    assert forecast["directional_model"]["score"] == pytest.approx(0.6)
    assert forecast["probability"]["value"] is None
    assert forecast["probability"]["confidence"] is None
    assert forecast["probability"]["support_count"] == 0
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["evidence_confidence"]["value"] is None
    assert forecast["expected_pre_move"]["sweep_probability"] is None
    assert forecast["stop_survival"]["value"] is None
    assert forecast["move_window"]["earliest"]["candles"] >= 3
    assert forecast["evidence_hierarchy"]["pooled_prior"]["calibrated"] is False
    assert forecast["evidence_hierarchy"]["pooled_prior"]["direction_probability"] is None
    assert forecast["evidence_hierarchy"]["pooled_prior"]["published_as_likelihood"] is False


@pytest.mark.parametrize(
    ("history_candles", "rest_segments", "available"),
    [(7, 1, False), (8, 0, False), (8, 1, True)],
)
def test_live_timing_requires_declared_history_and_segment_floors(
    history_candles: int,
    rest_segments: int,
    available: bool,
) -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "candle_count": history_candles,
            "current_state": {"state": "REST", "candle_count": 2},
            "swing_summary": {
                "up": {"average_candles": 2.0, "segment_count": 1},
            },
            "rest_summary": {
                "average_candles": 5.0,
                "segment_count": rest_segments,
            },
        },
    )

    live = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "LIVE_M5_SEQUENCE"
    )
    assert live["available"] is available
    assert live["minimum_history_candles"] == 8
    assert live["minimum_completed_segments"] == 2
    assert forecast["timing_estimate"]["source_tier"] == (
        "LIVE_M5_SEQUENCE" if available else "POLICY_WINDOW"
    )


def test_eur_nzd_style_62_candle_buy_read_yields_3_to_7_live_closes() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.73,
        current_regime="UPTREND",
        current_behavior={
            "candle_count": 62,
            "current_state": {"state": "REST", "candle_count": 2},
            "swing_summary": {
                "up": {"average_candles": 3.0, "segment_count": 5},
                "down": {"average_candles": 2.0, "segment_count": 3},
            },
            "rest_summary": {"average_candles": 5.0, "segment_count": 6},
        },
        lineage={
            "symbol": "EUR/NZD",
            "timeframe": "M5",
            "closed_candle_key": "eur-nzd-m5-62",
            "closed_candle_sequence": 62,
        },
    )

    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["candidate_direction"] == "UP"
    assert forecast["timing_estimate"]["source_tier"] == "LIVE_M5_SEQUENCE"
    assert forecast["timing_estimate"]["event_definition"] == (
        "TARGET_MOVE_START_AFTER_ANCHOR"
    )
    assert forecast["move_window"]["earliest"]["candles"] == 3
    assert forecast["move_window"]["latest"]["candles"] == 7
    assert forecast["move_window"]["earliest"]["minutes"] == 15.0
    assert forecast["move_window"]["latest"]["minutes"] == 35.0
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["evidence_confidence"]["value"] is None
    assert forecast["move_window"]["rolling_wall_clock"] is False
    assert "anchor_close_epoch_seconds" not in forecast["move_window"]


def test_forward_forecast_derives_pair_averages_from_raw_pair_dna_sums() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="SELL",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "status": "STUDIED",
            "candle_count": 28,
            "current_state": {"state": "REST", "candle_count": 1},
            "swing_summary": {
                "up": {"average_candles": 1.0},
                "down": {"average_candles": 1.5},
            },
            "rest_summary": {"average_candles": 1.0},
        },
        pair_profile={
            "observation_count": 14,
            "behavior": {
                "segment_counts": {
                    "PIXEL_PRICE_PROXY|DOWN_SWING": 3,
                    "PIXEL_PRICE_PROXY|REST": 3,
                },
                "segment_candle_sum": {
                    "PIXEL_PRICE_PROXY|DOWN_SWING": 5,
                    "PIXEL_PRICE_PROXY|REST": 3,
                },
                "segment_duration_sum": {
                    "PIXEL_PRICE_PROXY|DOWN_SWING": 1_500,
                    "PIXEL_PRICE_PROXY|REST": 900,
                },
                "transition_counts": {
                    "PIXEL_PRICE_PROXY|REST->DOWN_SWING": 3,
                    "PIXEL_PRICE_PROXY|REST->UP_SWING": 1,
                },
            },
        },
        lineage={
            "symbol": "EUR/NZD",
            "timeframe": "M5",
            "closed_candle_key": "eur-nzd-close-49",
            "closed_candle_sequence": 49,
        },
    )

    assert forecast["timing_estimate"]["source_tier"] == "PAIR"
    assert forecast["timing_estimate"]["support_count"] == 3
    assert forecast["probability"]["value"] is None
    assert forecast["probability"]["confidence"] is None
    assert forecast["state_transition_estimate"]["value"] == pytest.approx(0.75)
    assert forecast["state_transition_estimate"]["support_count"] == 4
    assert forecast["lineage"]["symbol"] == "EUR/NZD"
    assert forecast["lineage"]["closed_candle_sequence"] == 49


@pytest.mark.parametrize("support", [0, 1, 2, 3])
def test_pair_timing_and_transition_estimates_require_minimum_support(
    support: int,
) -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "current_state": {"state": "REST", "candle_count": 1},
        },
        pair_profile={
            "behavior": {
                "segment_counts": {
                    "UP_SWING": support,
                    "REST": support,
                },
                "segment_averages": {
                    "UP_SWING": {"candles": 6.0},
                    "REST": {"candles": 4.0},
                },
                "transition_counts": {"REST->UP_SWING": support},
            },
        },
    )

    eligible = support >= 3
    pair = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "PAIR"
    )
    assert pair["available"] is eligible
    assert pair["minimum_support"] == 3
    assert forecast["state_transition_estimate"]["eligible"] is eligible
    assert forecast["state_transition_estimate"]["value"] == (
        1.0 if eligible else None
    )


def test_pair_rest_wait_is_counted_once_and_target_duration_is_excluded() -> None:
    def forecast(target_average: float) -> dict[str, Any]:
        return build_hierarchical_forward_timing_forecast_v3(
            candidate_direction="BUY",
            duration_contract={
                "requested_duration_seconds": 3_000,
                "new_entry_eligible": True,
            },
            source_cadence_seconds=300,
            directional_confidence=0.7,
            current_behavior={
                "current_state": {"state": "REST", "candle_count": 2},
            },
            pair_profile={
                "behavior": {
                    "segment_counts": {"UP_SWING": 8, "REST": 8},
                    "segment_averages": {
                        "UP_SWING": {"candles": target_average},
                        "REST": {"candles": 4.0},
                    },
                },
            },
        )

    short_target = forecast(2.0)
    long_target = forecast(20.0)
    assert short_target["move_window"] == long_target["move_window"]
    components = short_target["timing_estimate"]["components"]
    assert components["current_state_remaining_candles"] == 2.0
    assert components["expected_intermediate_rest_candles"] == 0.0
    assert components["wait_to_target_start_candles"] == 2.0
    assert components["target_duration_included"] is False


def test_active_target_forecasts_next_same_direction_swing_after_rest() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.83,
        current_behavior={
            "candle_count": 60,
            "current_state": {"state": "UP_SWING", "candle_count": 2},
            "swing_summary": {
                "up": {
                    "average_candles": 1.3,
                    "maximum_candles": 4,
                    "segment_count": 20,
                },
            },
            "rest_summary": {
                "average_candles": 1.5,
                "maximum_candles": 3,
                "segment_count": 6,
            },
        },
    )

    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["timing_estimate"]["source_tier"] == "LIVE_M5_SEQUENCE"
    assert forecast["timing_estimate"]["event_definition"] == (
        "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"
    )
    assert forecast["move_window"]["earliest"]["candles"] >= 3
    assert forecast["move_window"]["earliest"]["minutes"] >= 15.0
    assert forecast["timing_estimate"]["current_target_state"] == (
        "ALREADY_ACTIVE_AT_ANCHOR"
    )
    components = forecast["timing_estimate"]["components"]
    assert components["current_state_remaining_candles"] == 0.0
    assert components["expected_intermediate_rest_candles"] == 1.5
    assert components["wait_to_target_start_candles"] == 1.5
    assert components["active_target_remaining_included"] is True
    assert components["target_duration_included"] is False
    assert components["target_distribution"]["segment_count"] == 20
    assert components["rest_distribution"]["segment_count"] == 6
    assert forecast["expected_pre_move"]["state"] == "REST_THEN_MOVE"
    assert forecast["expected_pre_move"]["rest_source_tier"] == "LIVE_BEHAVIOR"
    assert forecast["expected_pre_move"]["rest_support_count"] == 6
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["probability"]["value"] is None


@pytest.mark.parametrize(
    (
        "history_candles",
        "target_support",
        "rest_support",
        "score",
        "available",
    ),
    [
        (7, 20, 6, 0.8, False),
        (8, 0, 6, 0.8, False),
        (8, 1, 0, 0.8, False),
        (8, 1, 1, 0.0, False),
        (8, 1, 1, 0.8, True),
    ],
)
def test_active_target_next_swing_live_timing_requires_all_evidence_floors(
    history_candles: int,
    target_support: int,
    rest_support: int,
    score: float,
    available: bool,
) -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=score,
        current_behavior={
            "candle_count": history_candles,
            "current_state": {"state": "UP_SWING", "candle_count": 2},
            "swing_summary": {
                "up": {
                    "average_candles": 1.3,
                    "maximum_candles": 3,
                    "segment_count": target_support,
                },
            },
            "rest_summary": {
                "average_candles": 1.5,
                "maximum_candles": 2,
                "segment_count": rest_support,
            },
        },
    )

    live = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "LIVE_M5_SEQUENCE"
    )
    assert live["available"] is available
    assert live["target_segment_count"] == target_support
    assert live["rest_segment_count"] == rest_support
    assert forecast["timing_estimate"]["source_tier"] == (
        "LIVE_M5_SEQUENCE" if available else "POLICY_WINDOW"
    )
    if available:
        assert forecast["status"] == "FORECAST_AVAILABLE"
        assert forecast["timing_estimate"]["event_definition"] == (
            "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"
        )
        assert forecast["move_window"]["earliest"]["candles"] >= 3
    else:
        assert forecast["status"] == "TARGET_MOVE_ALREADY_ACTIVE"
        assert forecast["move_window"]["earliest"] is None


def test_opposite_swing_wait_is_remaining_opposite_plus_expected_rest() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "current_state": {"state": "DOWN_SWING", "candle_count": 2},
        },
        pair_profile={
            "behavior": {
                "segment_counts": {
                    "UP_SWING": 6,
                    "DOWN_SWING": 6,
                    "REST": 6,
                },
                "segment_averages": {
                    "UP_SWING": {"candles": 9.0},
                    "DOWN_SWING": {"candles": 5.0},
                    "REST": {"candles": 2.0},
                },
            }
        },
    )

    components = forecast["timing_estimate"]["components"]
    assert components["current_state_remaining_candles"] == 3.0
    assert components["expected_intermediate_rest_candles"] == 2.0
    assert components["wait_to_target_start_candles"] == 5.0
    assert components["target_duration_included"] is False


def test_forward_forecast_does_not_turn_policy_horizon_into_move_window() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.65,
        current_behavior={},
        pair_profile={},
    )

    assert forecast["status"] == "TIMING_UNRATED"
    assert forecast["candidate_direction"] == "UP"
    assert forecast["forecast_horizon_seconds"] == 1_800
    assert forecast["move_window"]["earliest"] is None
    assert forecast["move_window"]["central"] is None
    assert forecast["move_window"]["latest"] is None
    assert forecast["timing_estimate"]["source_tier"] == "POLICY_WINDOW"
    assert forecast["directional_model"]["score"] == pytest.approx(0.65)
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["evidence_confidence"]["value"] is None
    assert forecast["expected_pre_move"]["sweep_probability"] is None


def test_buy_only_pair_dna_cannot_publish_sell_timing_window() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="SELL",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={},
        pair_profile={
            "behavior": {
                "segment_counts": {
                    "PIXEL_PRICE_PROXY|UP_SWING": 9,
                    "PIXEL_PRICE_PROXY|REST": 4,
                },
                "segment_averages": {
                    "PIXEL_PRICE_PROXY|UP_SWING": {
                        "candles": 2.5,
                        "duration_seconds": 750,
                    },
                    "PIXEL_PRICE_PROXY|REST": {
                        "candles": 1.0,
                        "duration_seconds": 300,
                    },
                },
            }
        },
    )

    assert forecast["candidate_direction"] == "DOWN"
    assert forecast["status"] == "TIMING_UNRATED"
    assert forecast["move_window"]["earliest"] is None
    pair_tier = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "PAIR"
    )
    assert pair_tier["available"] is False
    assert pair_tier["target_state"] == "DOWN_SWING"
    assert pair_tier["support_count"] == 0


def test_up_only_current_sequence_cannot_publish_sell_timing_window() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="SELL",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "candle_count": 12,
            "current_state": {"state": "REST", "candle_count": 1},
            "swing_summary": {
                "up": {"average_candles": 2.5, "segment_count": 4},
                "down": {"average_candles": 0.0, "segment_count": 0},
            },
            "rest_summary": {"average_candles": 1.0, "segment_count": 3},
        },
        pair_profile={},
    )

    assert forecast["candidate_direction"] == "DOWN"
    assert forecast["status"] == "TIMING_UNRATED"
    assert forecast["move_window"]["earliest"] is None
    live_tier = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "LIVE_M5_SEQUENCE"
    )
    assert live_tier["available"] is False
    assert live_tier["target_segment_count"] == 0
    assert forecast["directional_model"]["score"] == pytest.approx(0.7)


def test_survival_forecast_rejects_conditioned_and_unsupported_curves() -> None:
    def curve(
        *,
        object_type: str,
        status: str,
        support: int,
        minimum_support: int,
        event_probability: float,
    ) -> dict[str, object]:
        return {
            "object_type": object_type,
            "origin_state": "REST",
            "event_type": "REST_END",
            "status": status,
            "support": support,
            "minimum_support": minimum_support,
            "curve": [
                {
                    "closed_candles": 3,
                    "cumulative_event_probability": event_probability,
                }
            ],
        }

    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "candle_count": 12,
            "current_state": {"state": "REST", "candle_count": 1},
            "swing_summary": {
                "up": {"average_candles": 2.0, "segment_count": 3}
            },
            "rest_summary": {"average_candles": 1.0, "segment_count": 3},
        },
        survival_network={
            "curves": [
                curve(
                    object_type="ORDER_BLOCK",
                    status="SUPPORTED",
                    support=20,
                    minimum_support=8,
                    event_probability=0.9,
                ),
                curve(
                    object_type="",
                    status="INSUFFICIENT_SUPPORT",
                    support=1,
                    minimum_support=8,
                    event_probability=0.9,
                ),
                curve(
                    object_type="PAIR_STATE",
                    status="SUPPORTED",
                    support=8,
                    minimum_support=8,
                    event_probability=0.4,
                ),
            ]
        },
    )

    assert forecast["event_likelihood"]["value"] == pytest.approx(0.4)
    assert forecast["event_likelihood"]["support_count"] == 8
    assert forecast["event_likelihood"]["source_tier"] == "PAIR_STATE_SURVIVAL"
    survival_tier = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "PAIR_STATE_SURVIVAL"
    )
    assert survival_tier["available"] is True
    assert survival_tier["minimum_support"] == 8
    assert survival_tier["rejected_object_conditioned_count"] == 1
    assert survival_tier["rejected_unsupported_count"] == 1


def test_survival_only_window_does_not_invent_rest_behavior() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "current_state": {"state": "REST", "candle_count": 1},
        },
        survival_network={
            "curves": [
                {
                    "object_type": "PAIR_STATE",
                    "origin_state": "REST",
                    "event_type": "REST_END",
                    "status": "SUPPORTED",
                    "support": 8,
                    "minimum_support": 8,
                    "curve": [
                        {
                            "closed_candles": 3,
                            "cumulative_event_probability": 0.6,
                        }
                    ],
                }
            ]
        },
    )

    assert forecast["timing_estimate"]["source_tier"] == "PAIR_STATE_SURVIVAL"
    assert forecast["expected_pre_move"]["state"] == "PRE_MOVE_STATE_UNRATED"
    assert forecast["expected_pre_move"]["rest_window_candles"] is None
    assert forecast["expected_pre_move"]["rest_window_minutes"] is None
    assert forecast["expected_pre_move"]["rest_source_tier"] == "NONE"
    assert forecast["expected_pre_move"]["rest_support_count"] == 0
    assert forecast["expected_pre_move"]["rest_zero_observed"] is False


def test_single_motif_case_cannot_override_live_or_publish_outcomes() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "candle_count": 8,
            "current_state": {"state": "REST", "candle_count": 1},
            "swing_summary": {
                "up": {"average_candles": 2.0, "segment_count": 3}
            },
            "rest_summary": {"average_candles": 1.0, "segment_count": 3},
        },
        motif_lattice={
            "closed_candle_count": 5,
            "levels": [
                {
                    "level": 1,
                    "nodes": [
                        {
                            "motif_token": "ONE-PERFECT-MATCH",
                            "span": {"end_index": 4, "candle_count": 3},
                        }
                    ],
                }
            ],
        },
        motif_trajectory_library={
            "entries": [
                {
                    "motif_token": "ONE-PERFECT-MATCH",
                    "reference_direction": "UP",
                    "points": [
                        {
                            "offset_closed_candles": 3,
                            "cumulative_favorable_excursion_in_median_ranges": 0.7,
                            "cumulative_adverse_excursion_in_median_ranges": 0.3,
                            "state": "REST",
                        }
                    ],
                }
            ]
        },
    )

    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["timing_estimate"]["source_tier"] == "LIVE_M5_SEQUENCE"
    assert forecast["timing_estimate"]["support_count"] == 0
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["event_likelihood"]["support_count"] == 0
    assert forecast["evidence_confidence"]["value"] is None
    assert forecast["probability"]["value"] is None
    assert forecast["probability"]["confidence"] is None
    assert forecast["expected_pre_move"]["sweep_probability"] is None
    assert forecast["expected_pre_move"]["sweep_support_count"] == 0
    motif_tier = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "PAIR_MOTIF"
    )
    assert motif_tier["available"] is False
    assert motif_tier["raw_match_available"] is True
    assert motif_tier["sparse_diagnostic_only"] is True


@pytest.mark.parametrize("support", [0, 1, 2, 3])
def test_motif_timing_requires_minimum_independent_outcome_support(
    support: int,
) -> None:
    entries = [
        {
            "motif_token": "BOUNDARY-MOTIF",
            "reference_direction": "UP",
            "points": [
                {
                    "offset_closed_candles": 3,
                    "cumulative_favorable_excursion_in_median_ranges": 0.7,
                    "cumulative_adverse_excursion_in_median_ranges": 0.1,
                    "state": "UP_SWING",
                }
            ],
        }
        for _ in range(support)
    ]
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "current_state": {"state": "REST", "candle_count": 1},
        },
        motif_lattice={
            "closed_candle_count": 5,
            "levels": [
                {
                    "level": 1,
                    "nodes": [
                        {
                            "motif_token": "BOUNDARY-MOTIF",
                            "span": {"end_index": 4, "candle_count": 3},
                        }
                    ],
                }
            ],
        },
        motif_trajectory_library={"entries": entries},
    )

    eligible = support >= 3
    motif_tier = next(
        row
        for row in forecast["evidence_hierarchy"]["attempted_tiers"]
        if row["tier"] == "PAIR_MOTIF"
    )
    assert motif_tier["available"] is eligible
    assert motif_tier["timing_window_eligible"] is eligible
    assert motif_tier["raw_match_available"] is (support > 0)
    assert motif_tier["sparse_diagnostic_only"] is (0 < support < 3)
    assert forecast["timing_estimate"]["source_tier"] == (
        "PAIR_MOTIF" if eligible else "POLICY_WINDOW"
    )
    if eligible:
        assert forecast["move_window"]["earliest"] is not None
    else:
        assert forecast["move_window"]["earliest"] is None
    assert forecast["event_likelihood"]["value"] == (
        1.0 if eligible else None
    )


def test_motif_outcomes_own_occurrence_likelihood_and_sweep_estimate() -> None:
    entries: list[dict[str, Any]] = []
    for index in range(4):
        entries.append(
            {
                "motif_token": "REST-BREAKOUT",
                "reference_direction": "UP",
                "points": [
                    {
                        "offset_closed_candles": 3,
                        "cumulative_favorable_excursion_in_median_ranges": (
                            0.6 if index < 3 else 0.2
                        ),
                        "cumulative_adverse_excursion_in_median_ranges": (
                            0.3 if index in {0, 1} else 0.1
                        ),
                        "state": "REST" if index in {0, 1} else "UP_SWING",
                    }
                ],
            }
        )
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "candle_count": 8,
            "current_state": {"state": "REST", "candle_count": 1},
            "swing_summary": {
                "up": {"average_candles": 2.0, "segment_count": 3}
            },
            "rest_summary": {"average_candles": 1.0},
        },
        motif_lattice={
            "closed_candle_count": 5,
            "levels": [
                {
                    "level": 1,
                    "nodes": [
                        {
                            "motif_token": "REST-BREAKOUT",
                            "span": {"end_index": 4, "candle_count": 3},
                        }
                    ],
                }
            ],
        },
        motif_trajectory_library={"entries": entries},
    )

    assert forecast["event_likelihood"]["value"] == pytest.approx(0.75)
    assert forecast["event_likelihood"]["support_count"] == 4
    assert forecast["event_likelihood"]["source_tier"] == "PAIR_MOTIF"
    assert forecast["probability"]["value"] == pytest.approx(0.75)
    assert forecast["probability"]["metric"] == (
        "MOTIF_TARGET_FOLLOW_THROUGH_WITHIN_FORECAST_HORIZON"
    )
    assert forecast["evidence_confidence"]["value"] is not None
    assert forecast["expected_pre_move"]["sweep_probability"] == pytest.approx(0.5)
    assert forecast["expected_pre_move"]["sweep_support_count"] == 4
    assert forecast["stop_survival"]["value"] is None


def test_exact_stop_survival_is_not_blended_into_directional_likelihood() -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="SELL",
        duration_contract={
            "status": "ELIGIBLE",
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "candle_count": 8,
            "current_state": {"state": "REST", "candle_count": 1},
            "swing_summary": {
                "down": {"average_candles": 2.0, "segment_count": 3}
            },
            "rest_summary": {"average_candles": 1.0},
        },
        exact_jpclf_estimate={
            "support_count": 12,
            "survival_probability": 0.8,
            "probability_worst_drawdown_still_ahead": 0.25,
            "target_time_seconds": {"p10": 900, "median": 1_200, "p90": 1_500},
            "stop_distance_mru": 0.3,
            "move_size_mru": 0.5,
        },
        exact_time_proven=True,
        exact_promotion_passed=False,
        lineage={
            "symbol": "EUR/NZD",
            "timeframe": "M5",
            "closed_candle_key": "eur-nzd-close-62",
            "closed_candle_sequence": 62,
            "anchor_close_epoch_seconds": 100_000.0,
        },
    )

    assert forecast["directional_model"]["score"] == pytest.approx(0.7)
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["probability"]["value"] is None
    assert forecast["probability"]["confidence"] is None
    assert forecast["stop_survival"]["value"] == pytest.approx(0.8)
    assert forecast["stop_survival"]["support_count"] == 12
    assert forecast["stop_survival"]["exact_wall_clock_proven"] is True
    assert forecast["adverse_excursion_risk"][
        "worst_drawdown_still_ahead_probability"
    ] == pytest.approx(0.25)
    assert forecast["timing_estimate"]["source_tier"] == "EXACT_JPCLF"
    assert forecast["move_window"]["exact_wall_clock_proven"] is True
    assert forecast["move_window"]["anchor_time_proven"] is True
    assert forecast["move_window"]["estimate_calibrated"] is False
    assert forecast["move_window"]["basis"] == "CLOCK_ANCHORED_SHRUNK_ESTIMATE"
    assert forecast["move_window"]["anchor_close_epoch_seconds"] == 100_000.0
    assert forecast["move_window"]["target_window_start_epoch_seconds"] == (
        100_000.0
        + forecast["move_window"]["earliest"]["seconds"]
    )
    assert forecast["move_window"]["target_window_end_epoch_seconds"] == (
        100_000.0
        + forecast["move_window"]["latest"]["seconds"]
    )


@pytest.mark.parametrize("support", [0, 1, 2, 3])
def test_exact_timing_and_probabilities_require_minimum_support(
    support: int,
) -> None:
    forecast: dict[str, Any] = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction="BUY",
        duration_contract={
            "requested_duration_seconds": 1_800,
            "new_entry_eligible": True,
        },
        source_cadence_seconds=300,
        directional_confidence=0.7,
        current_behavior={
            "current_state": {"state": "REST", "candle_count": 1},
        },
        exact_jpclf_estimate={
            "support_count": support,
            "survival_probability": 0.8,
            "probability_worst_drawdown_still_ahead": 0.3,
            "target_time_seconds": {"p10": 900, "median": 1_200, "p90": 1_500},
            "stop_distance_mru": 0.3,
            "move_size_mru": 0.5,
        },
        exact_time_proven=True,
        lineage={
            "symbol": "EUR/NZD",
            "timeframe": "M5",
            "closed_candle_key": "eur-nzd-m5-62",
            "closed_candle_sequence": 62,
            "anchor_close_epoch_seconds": 100_000.0,
        },
    )

    eligible = support >= 3
    assert forecast["stop_survival"]["value"] == (0.8 if eligible else None)
    assert forecast["adverse_excursion_risk"][
        "worst_drawdown_still_ahead_probability"
    ] == (0.3 if eligible else None)
    assert forecast["timing_estimate"]["source_tier"] == (
        "EXACT_JPCLF" if eligible else "POLICY_WINDOW"
    )
    if eligible:
        assert forecast["move_window"]["anchor_close_epoch_seconds"] == 100_000.0
        assert forecast["expected_pre_move"]["state"] == (
            "PRE_MOVE_STATE_UNRATED"
        )
        assert forecast["expected_pre_move"]["rest_window_candles"] is None
    else:
        assert "anchor_close_epoch_seconds" not in forecast["move_window"]


def test_freezes_are_closed_candle_ordered_bounded_and_restart_safe() -> None:
    field = _field(max_freezes=1)
    field.add_trajectory(_trajectory("WIN", _winning_path()))
    freeze: dict[str, Any] = field.freeze_closed_candle_state(
        closed_candle_key="C-LIVE-11",
        order_index=11,
        closed_at_seconds=10_300.0,
        studied_direction="UP",
        contract_duration_seconds=1_200,
        elapsed_seconds=0,
        current_path_mru=0.0,
        liquidity_state=_liquidity(order_index=11, as_of_seconds=10_300.0),
        scenarios=[{"stop_distance_mru": 0.3, "move_size_mru": 0.5}],
    )
    assert freeze["scenario_estimates"][0]["status"] == "STUDIED"
    assert freeze["execution_authority"] is False
    assert freeze["trajectory_library_revision"] == 1
    # Later history must not rewrite the exact as-of field frozen at C-LIVE-11.
    field.add_trajectory(_trajectory("LATER", _swept_path()))
    replayed = field.freeze_closed_candle_state(
        closed_candle_key="C-LIVE-11",
        order_index=11,
        closed_at_seconds=10_300.0,
        studied_direction="UP",
        contract_duration_seconds=1_200,
        elapsed_seconds=0,
        current_path_mru=0.0,
        liquidity_state=_liquidity(order_index=11, as_of_seconds=10_300.0),
        scenarios=[{"stop_distance_mru": 0.3, "move_size_mru": 0.5}],
    )
    assert replayed == freeze
    conflicting: dict[str, Any] = dict(
        closed_candle_key="C-LIVE-11",
        order_index=11,
        closed_at_seconds=10_300.0,
        studied_direction="UP",
        contract_duration_seconds=1_200,
        elapsed_seconds=0,
        current_path_mru=0.2,
        liquidity_state=_liquidity(order_index=11, as_of_seconds=10_300.0),
    )
    with pytest.raises(PathClockLiquidityValidationError, match="conflicts"):
        field.freeze_closed_candle_state(**conflicting)
    with pytest.raises(PathClockLiquidityValidationError, match="capacity"):
        field.freeze_closed_candle_state(
            closed_candle_key="C-LIVE-12",
            order_index=12,
            closed_at_seconds=10_600.0,
            studied_direction="UP",
            contract_duration_seconds=1_200,
            elapsed_seconds=0,
            current_path_mru=0.1,
            liquidity_state=_liquidity(order_index=12, as_of_seconds=10_600.0),
        )

    snapshot: dict[str, Any] = field.snapshot()
    restored = JointPathClockLiquidityFieldV3.from_snapshot(snapshot)
    assert restored.snapshot() == snapshot
    tampered = deepcopy(snapshot)
    tampered["trajectories"][0]["points"][1]["path_mru"] = 99.0
    with pytest.raises(PathClockLiquidityValidationError, match="digest"):
        JointPathClockLiquidityFieldV3.from_snapshot(tampered)

    forged = deepcopy(snapshot)
    forged_trajectory = forged["trajectories"][0]
    forged_trajectory["studied_direction"] = "SIDEWAYS"
    forged_trajectory_body = deepcopy(forged_trajectory)
    forged_trajectory_body.pop("trajectory_digest")
    forged_trajectory["trajectory_digest"] = _digest(forged_trajectory_body)
    forged_body = deepcopy(forged)
    forged_body.pop("state_digest")
    forged["state_digest"] = _digest(forged_body)
    with pytest.raises(PathClockLiquidityValidationError, match="UP or DOWN"):
        JointPathClockLiquidityFieldV3.from_snapshot(forged)


def test_trajectory_and_freeze_capacity_reject_instead_of_unbounded_growth() -> None:
    field = _field(max_trajectories=1)
    first_input = _trajectory("ONE", _winning_path())
    first = field.add_trajectory(first_input)
    assert field.add_trajectory(first_input) == first
    conflicting = deepcopy(first_input)
    conflicting["points"][-1]["path_mru"] = 1.2
    conflicting["points"][-1]["high_mru"] = 1.2
    with pytest.raises(PathClockLiquidityValidationError, match="conflicts"):
        field.add_trajectory(conflicting)
    with pytest.raises(PathClockLiquidityValidationError, match="capacity"):
        field.add_trajectory(_trajectory("TWO", _swept_path()))
    snapshot: dict[str, Any] = field.snapshot()
    assert len(snapshot["trajectories"]) == 1
    assert snapshot["config"]["max_trajectories"] == 1
    assert snapshot["persistence_contract"]["pair_dna_embeddable"] is False
    compact = field.pair_dna_partition_summary()
    assert compact["contains_trajectory_points"] is False
    assert "trajectories" not in compact


def test_fine_grid_requires_exact_subcandle_samples_not_m5_interpolation() -> None:
    field = JointPathClockLiquidityFieldV3(
        **SCOPE,
        clock_step_seconds=30,
        max_points_per_trajectory=241,
    )
    m5 = _trajectory("M5-AS-30S", _winning_path())
    with pytest.raises(PathClockLiquidityValidationError, match="source cadence"):
        field.add_trajectory(m5)
    m5["exact_subcandle_timestamps_proven"] = True
    with pytest.raises(PathClockLiquidityValidationError, match="too sparse"):
        field.add_trajectory(m5)


def _replay(
    index: int,
    *,
    predicted: str,
    observed: str,
    move_time: int,
    probability: float,
    survived: bool,
) -> dict[str, Any]:
    return {
        **SCOPE,
        "closed_candle_key": f"R-{index}",
        "frozen_on_closed_candle": True,
        "future_leakage_detected": False,
        "horizon_seconds": 1_200,
        "predicted_direction": predicted,
        "observed_direction": observed,
        "timing_window_seconds": {"start": 900, "end": 1_100},
        "observed_move_time_seconds": move_time,
        "sweep_outcomes": [
            {
                "stop_distance_mru": 0.3,
                "move_size_mru": 0.5,
                "predicted_survival_probability": probability,
                "survived_until_move": survived,
            }
        ],
    }


def test_replay_scores_four_independent_axes_and_probability_calibration() -> None:
    records = [
        _replay(1, predicted="UP", observed="UP", move_time=900, probability=0.8, survived=True),
        _replay(2, predicted="UP", observed="DOWN", move_time=1_150, probability=0.2, survived=False),
        _replay(3, predicted="DOWN", observed="DOWN", move_time=1_000, probability=0.7, survived=True),
        _replay(4, predicted="UP", observed="UP", move_time=1_200, probability=0.3, survived=False),
        _replay(5, predicted="DOWN", observed="UP", move_time=300, probability=1.0, survived=False),
    ]
    score: dict[str, Any] = score_path_clock_replays_v3(records, **SCOPE)
    metrics = score["metrics"]
    assert metrics["directional_accuracy"] == 0.75
    assert metrics["timing_accuracy"] == 0.5
    assert metrics["sweep_survival_rate"] == 0.5
    assert metrics["calibration_score"] == pytest.approx(0.75)
    assert metrics["brier_score"] == pytest.approx(0.065)
    assert score["eligible_replay_count"] == 4
    assert score["audited_replay_count"] == 5
    assert score["excluded_early_move_count"] == 1
    assert len(score["replay_key_digest"]) == 64
    assert len(score["scenario_grid_digest"]) == 64
    assert len(score["evaluation_cohort_digest"]) == 64
    assert score["execution_authority"] is False

    leaked = deepcopy(records)
    leaked[0]["future_leakage_detected"] = True
    with pytest.raises(PathClockLiquidityValidationError, match="causal"):
        score_path_clock_replays_v3(leaked, **SCOPE)
    too_short = deepcopy(records)
    too_short[0]["horizon_seconds"] = 899
    with pytest.raises(PathClockLiquidityValidationError, match=">= 900"):
        score_path_clock_replays_v3(too_short, **SCOPE)


def test_completed_no_target_replay_is_scored_as_censored_failure() -> None:
    no_target = _replay(
        6,
        predicted="UP",
        observed="FLAT",
        move_time=1_200,
        probability=0.8,
        survived=False,
    )
    no_target["observed_move_occurred"] = False

    score: dict[str, Any] = score_path_clock_replays_v3([no_target], **SCOPE)

    assert score["eligible_replay_count"] == 1
    assert score["excluded_early_move_count"] == 0
    assert score["metrics"]["directional_accuracy"] == 0.0
    assert score["metrics"]["timing_accuracy"] == 0.0
    assert score["metrics"]["sweep_survival_rate"] == 0.0
    assert score["metrics"]["calibration_score"] == pytest.approx(0.2)
    assert score["metrics"]["brier_score"] == pytest.approx(0.64)

    invalid_censor = deepcopy(no_target)
    invalid_censor["observed_move_time_seconds"] = 1_199
    with pytest.raises(PathClockLiquidityValidationError, match="right-censored"):
        score_path_clock_replays_v3([invalid_censor], **SCOPE)


def _score(metrics: dict[str, float], support: int = 64) -> dict[str, Any]:
    return {
        "schema_version": PATH_CLOCK_REPLAY_SCORE_SCHEMA_VERSION,
        "scope": SCOPE,
        "eligible_replay_count": support,
        "audited_replay_count": support,
        "sweep_outcome_count": support,
        "replay_key_digest": "A" * 64,
        "scenario_grid_digest": "B" * 64,
        "evaluation_cohort_digest": "C" * 64,
        "metrics": metrics,
        "study_only": True,
        "observation_only": True,
        "closed_candle_causal": True,
        "establishes_causation": False,
        "execution_authority": False,
        "broker_click_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


def test_promotion_requires_every_axis_to_improve_together() -> None:
    baseline_metrics = {
        "directional_accuracy": 0.60,
        "timing_accuracy": 0.55,
        "sweep_survival_rate": 0.58,
        "calibration_score": 0.70,
    }
    candidate_metrics = {
        "directional_accuracy": 0.66,
        "timing_accuracy": 0.61,
        "sweep_survival_rate": 0.64,
        "calibration_score": 0.75,
    }
    passed: dict[str, Any] = evaluate_path_clock_promotion_gate_v3(
        baseline_score=_score(baseline_metrics),
        candidate_score=_score(candidate_metrics),
        minimum_replays=32,
    )
    assert passed["passed"] is True
    assert passed["all_axes_improved"] is True
    assert passed["paired_evaluation"]["passed"] is True
    assert passed["execution_authority"] is False

    one_regression = deepcopy(candidate_metrics)
    one_regression["timing_accuracy"] = baseline_metrics["timing_accuracy"]
    failed: dict[str, Any] = evaluate_path_clock_promotion_gate_v3(
        baseline_score=_score(baseline_metrics),
        candidate_score=_score(one_regression),
        minimum_replays=32,
    )
    assert failed["passed"] is False
    assert failed["status"] == "RETAIN_BASELINE"
    assert failed["axes"]["timing_accuracy"]["improved"] is False

    unpaired_candidate = _score(candidate_metrics)
    unpaired_candidate["scenario_grid_digest"] = "D" * 64
    unpaired: dict[str, Any] = evaluate_path_clock_promotion_gate_v3(
        baseline_score=_score(baseline_metrics),
        candidate_score=unpaired_candidate,
        minimum_replays=32,
    )
    assert unpaired["passed"] is False
    assert unpaired["paired_evaluation"]["passed"] is False
    assert (
        unpaired["paired_evaluation"]["digests"]["scenario_grid_digest"][
            "matches"
        ]
        is False
    )


def test_replay_sweep_grid_requires_unique_stop_and_move_identity() -> None:
    replay = _replay(
        7,
        predicted="UP",
        observed="UP",
        move_time=900,
        probability=0.8,
        survived=True,
    )
    missing_move = deepcopy(replay)
    missing_move["sweep_outcomes"][0].pop("move_size_mru")
    with pytest.raises(PathClockLiquidityValidationError, match="move_size_mru"):
        score_path_clock_replays_v3([missing_move], **SCOPE)

    duplicated = deepcopy(replay)
    duplicated["sweep_outcomes"].append(
        deepcopy(duplicated["sweep_outcomes"][0])
    )
    with pytest.raises(PathClockLiquidityValidationError, match="unique"):
        score_path_clock_replays_v3([duplicated], **SCOPE)
