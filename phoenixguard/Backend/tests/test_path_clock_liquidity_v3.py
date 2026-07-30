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
    evaluate_path_clock_promotion_gate_v3,
    score_path_clock_replays_v3,
)


SCOPE = {
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
    accepted = field.add_trajectory(
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
    stored = field.add_trajectory(_trajectory("WIN", _winning_path()))
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

    distribution = field.joint_clock_distribution()
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

    distribution = field.joint_clock_distribution()
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
    result = field.estimate_stop_survival(
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
    result = field.estimate_stop_survival(
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
    result = field.estimate_stop_survival(
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


def test_freezes_are_closed_candle_ordered_bounded_and_restart_safe() -> None:
    field = _field(max_freezes=1)
    field.add_trajectory(_trajectory("WIN", _winning_path()))
    freeze = field.freeze_closed_candle_state(
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
    conflicting = dict(
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

    snapshot = field.snapshot()
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
    snapshot = field.snapshot()
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
    score = score_path_clock_replays_v3(records, **SCOPE)
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

    score = score_path_clock_replays_v3([no_target], **SCOPE)

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
    passed = evaluate_path_clock_promotion_gate_v3(
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
    failed = evaluate_path_clock_promotion_gate_v3(
        baseline_score=_score(baseline_metrics),
        candidate_score=_score(one_regression),
        minimum_replays=32,
    )
    assert failed["passed"] is False
    assert failed["status"] == "RETAIN_BASELINE"
    assert failed["axes"]["timing_accuracy"]["improved"] is False

    unpaired_candidate = _score(candidate_metrics)
    unpaired_candidate["scenario_grid_digest"] = "D" * 64
    unpaired = evaluate_path_clock_promotion_gate_v3(
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
