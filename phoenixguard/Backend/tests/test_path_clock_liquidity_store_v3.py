from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from phoenixguard.study.path_clock_liquidity_store_v3 import (
    PathClockLiquiditySideStoreV3,
    PathClockLiquidityStoreValidationError,
    pending_path_clock_liquidity_v3,
)


def _candles(last_index: int, *, close_shift: float = 0.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(last_index + 1):
        open_value = 100.0 + index * 0.12
        close = open_value + 0.08
        if index == last_index:
            close += close_shift
        rows.append(
            {
                "candle_id": f"bar-{index}",
                "timestamp": index * 300,
                "closed": True,
                "identity_stable": True,
                "stable_candle_identity": f"EXPLICIT:close-{index}",
                "identity_proof_source": (
                    "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
                ),
                "closed_candle_sequence": index,
                "coordinate_space": "PRICE",
                "ohlc": {
                    "open": open_value,
                    "high": max(open_value, close) + 0.04,
                    "low": min(open_value, close) - 0.03,
                    "close": close,
                },
                "direction": "BULLISH",
            }
        )
    return rows


def _time_proof(
    index: int,
    *,
    bound_row_index: int | None = None,
    closed_candle_key: str | None = None,
    timestamp_source: str = "SOURCE_CLOSE_TIME",
    contiguous_from_previous: bool = True,
    transition_count: int = 1,
    observed_offset_seconds: float = 1.0,
) -> dict[str, Any]:
    close_epoch_seconds = index * 300
    return {
        "schema_version": "PG_PROVEN_CLOSED_CANDLE_TIME_V3",
        "symbol": "USD/CAD OTC",
        "timeframe": "M5",
        "closed_candle_key": closed_candle_key or f"close-{index}",
        "closed_candle_sequence": index,
        "close_epoch_seconds": close_epoch_seconds,
        "timestamp_semantic": "BAR_CLOSE",
        "timestamp_source": timestamp_source,
        "proof_source": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
        "bound_row_index": (
            index if bound_row_index is None else bound_row_index
        ),
        "transition_count": transition_count,
        "source_cadence_seconds": 300,
        "observed_epoch_seconds": (
            close_epoch_seconds + observed_offset_seconds
        ),
        "observation_latency_seconds": observed_offset_seconds,
        "contiguous_from_previous": contiguous_from_previous,
    }


def _liquidity() -> dict[str, Any]:
    return {
        "wick_entropy": 0.72,
        "repeated_area_touches": 3,
        "late_sweep_motif_distance": 0.35,
        "wick_body_asymmetry": 0.2,
        "object_copresence_density": 0.4,
    }


def _observe(
    store: PathClockLiquiditySideStoreV3,
    index: int,
    *,
    duration: object | None,
    close_shift: float = 0.0,
    contiguous_from_previous: bool = True,
    transition_count: int = 1,
) -> dict[str, Any]:
    return store.observe_closed_candle(
        symbol="USD/CAD OTC",
        timeframe="M5",
        closed_candle_key=f"close-{index}",
        closed_candle_sequence=index,
        closed_candle_time_proof=_time_proof(
            index,
            contiguous_from_previous=contiguous_from_previous,
            transition_count=transition_count,
        ),
        candles=_candles(index, close_shift=close_shift),
        source_cadence_seconds=300,
        studied_direction="BUY",
        contract_duration_seconds=duration,
        liquidity_state=_liquidity(),
    )


def _observe_rows(
    store: PathClockLiquiditySideStoreV3,
    index: int,
    *,
    rows: list[dict[str, Any]],
    proof: dict[str, Any],
    duration: object | None = None,
) -> dict[str, Any]:
    return store.observe_closed_candle(
        symbol="USD/CAD OTC",
        timeframe="M5",
        closed_candle_key=f"close-{index}",
        closed_candle_sequence=index,
        closed_candle_time_proof=proof,
        candles=rows,
        source_cadence_seconds=300,
        studied_direction="BUY",
        contract_duration_seconds=duration,
        liquidity_state=_liquidity(),
    )


def test_duration_below_fifteen_minutes_never_opens_an_anchor(
    tmp_path: Path,
) -> None:
    result = _observe(
        PathClockLiquiditySideStoreV3(tmp_path / "jpclf"),
        3,
        duration=899,
    )

    assert result["status"] == "EXCLUDED_UNDER_15_MINUTES"
    assert result["new_entry_eligible"] is False
    assert result["active_anchor_count"] == 0
    assert result["trajectory_count"] == 0
    assert result["execution_authority"] is False
    assert result["grants_entry_permission"] is False


def test_admitted_anchor_keeps_tracking_inside_final_nine_hundred_seconds(
    tmp_path: Path,
) -> None:
    root = tmp_path / "jpclf"
    store = PathClockLiquiditySideStoreV3(root)
    opened = _observe(store, 3, duration=900)
    assert opened["new_entry_eligible"] is True
    assert opened["active_anchor_count"] == 1

    # The new request is ineligible, but the already admitted 900-second clock
    # must keep collecting exact closed candles through its final interval.
    first_late = _observe(store, 4, duration=60)
    assert first_late["status"] == "ACTIVE_TRACKING_ONLY"
    assert first_late["active_tracking_continues_below_floor"] is True
    assert first_late["active_anchor_count_below_900_seconds_remaining"] == 1
    assert first_late["latest_field_state"]["remaining_seconds"] == 600
    assert first_late["latest_field_state"]["new_entry_eligible"] is False

    _observe(PathClockLiquiditySideStoreV3(root), 5, duration=None)
    matured = _observe(PathClockLiquiditySideStoreV3(root), 6, duration=899)
    assert matured["trajectory_count"] == 1
    assert matured["active_anchor_count"] == 0
    assert matured["pair_dna_partition"]["contains_trajectory_points"] is False
    assert matured["persistence_contract"][
        "raw_trajectories_in_pair_dna_json"
    ] is False


def test_non_contiguous_clock_censors_without_interpolation(tmp_path: Path) -> None:
    store = PathClockLiquiditySideStoreV3(tmp_path / "jpclf")
    _observe(store, 3, duration=900)
    # Skip index 4: no exact 300-second observation exists for the active path.
    result = _observe(
        store,
        5,
        duration=None,
        contiguous_from_previous=False,
        transition_count=2,
    )

    assert result["status"] == "CENSORED_DISCONTINUITY"
    assert result["active_anchor_count"] == 0
    assert result["trajectory_count"] == 0
    assert result["censorship_audit"][
        "latest_discontinuity_censored_anchor_count"
    ] == 1
    assert result["persistence_contract"]["interpolates_missing_candles"] is False


def test_store_resynchronizes_after_it_misses_a_resolver_event(
    tmp_path: Path,
) -> None:
    store = PathClockLiquiditySideStoreV3(tmp_path / "missed-store-event")
    _observe(store, 3, duration=900)

    # The resolver's current event is one-step contiguous with its own prior
    # event, but the side store did not observe event 4. It must censor the
    # active path, persist event 5, and recover on event 6 instead of rejecting
    # every future proof against its stale event 3 checkpoint.
    missed = _observe(store, 5, duration=None)
    assert missed["status"] == "CENSORED_DISCONTINUITY"
    assert missed["active_anchor_count"] == 0

    recovered = _observe(store, 6, duration=None)
    assert recovered["closed_candle_sequence"] == 6
    assert recovered["censorship_audit"][
        "latest_discontinuity_censored_anchor_count"
    ] == 0


def test_restart_and_identical_closed_key_are_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "jpclf"
    first = _observe(PathClockLiquiditySideStoreV3(root), 3, duration=900)
    repeated = _observe(PathClockLiquiditySideStoreV3(root), 3, duration=900)

    assert repeated == first
    assert repeated["active_anchor_count"] == 1
    pair_files = list(root.glob("*.json"))
    assert len(pair_files) == 1


def test_same_closed_key_with_different_evidence_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "jpclf"
    _observe(PathClockLiquiditySideStoreV3(root), 3, duration=900)

    with pytest.raises(
        PathClockLiquidityStoreValidationError,
        match="conflicts with different JPCLF evidence",
    ):
        _observe(
            PathClockLiquiditySideStoreV3(root),
            3,
            duration=900,
            close_shift=0.5,
        )


def test_greatest_timestamp_without_resolver_binding_is_rejected(
    tmp_path: Path,
) -> None:
    rows = _candles(3)
    unbound = dict(rows[-1])
    unbound.update(
        {
            "candle_id": "unbound-later-row",
            "timestamp": 1_200,
            "closed_candle_sequence": 4,
        }
    )
    unbound.pop("identity_stable")
    unbound.pop("stable_candle_identity")
    unbound.pop("identity_proof_source")
    rows.append(unbound)

    with pytest.raises(
        PathClockLiquidityStoreValidationError,
        match="lacks stable resolver identity",
    ):
        _observe_rows(
            PathClockLiquiditySideStoreV3(tmp_path / "unbound-later"),
            3,
            rows=rows,
            proof=_time_proof(3),
        )


def test_time_proof_must_match_the_top_level_closed_event(tmp_path: Path) -> None:
    with pytest.raises(
        PathClockLiquidityStoreValidationError,
        match="does not match the requested closed-candle event",
    ):
        _observe_rows(
            PathClockLiquiditySideStoreV3(tmp_path / "key-mismatch"),
            3,
            rows=_candles(3),
            proof=_time_proof(3, closed_candle_key="different-close"),
        )


def test_candle_timestamp_must_align_with_its_resolver_sequence(
    tmp_path: Path,
) -> None:
    rows = _candles(3)
    rows[1]["timestamp"] = 301

    with pytest.raises(
        PathClockLiquidityStoreValidationError,
        match="timestamp is not aligned to its resolver sequence",
    ):
        _observe_rows(
            PathClockLiquiditySideStoreV3(tmp_path / "clock-mismatch"),
            3,
            rows=rows,
            proof=_time_proof(3),
        )


def test_latest_only_binding_censors_an_unreobserved_anchor(
    tmp_path: Path,
) -> None:
    store = PathClockLiquiditySideStoreV3(tmp_path / "latest-only")
    _observe(store, 3, duration=900)
    latest_only = [_candles(4)[-1]]

    result = _observe_rows(
        store,
        4,
        rows=latest_only,
        proof=_time_proof(4, bound_row_index=0),
    )

    assert result["active_anchor_count"] == 0
    assert result["censorship_audit"]["reasons"] == {
        "ANCHOR_NOT_REOBSERVED_ON_CURRENT_AXIS": 1
    }


def test_full_stable_binding_chain_retains_anchor_and_publicizes_only_sanitized_proof(
    tmp_path: Path,
) -> None:
    store = PathClockLiquiditySideStoreV3(tmp_path / "full-chain")
    _observe(store, 3, duration=900)
    proof = _time_proof(4)
    proof["raw_geometry"] = {"pixels": [1, 2, 3]}

    result = _observe_rows(
        store,
        4,
        rows=_candles(4),
        proof=proof,
    )

    assert result["active_anchor_count"] == 1
    assert result["latest_field_state"]["elapsed_seconds"] == 300
    public_proof = result["latest_field_state"]["closed_candle_time_proof"]
    assert public_proof["closed_candle_key"] == "close-4"
    assert public_proof["close_epoch_seconds"] == 1_200
    assert "raw_geometry" not in public_proof
    assert result["time_proof_audit"] == public_proof
    assert result["persistence_contract"]["raw_geometry_in_time_proof"] is False
    persisted = next((tmp_path / "full-chain").glob("*.json")).read_text(
        encoding="utf-8"
    )
    assert '"latest_closed_candle_time_proof"' in persisted
    assert '"raw_geometry":' not in persisted
    assert '"pixels"' not in persisted


def test_repeated_key_with_conflicting_valid_time_proof_fails_closed(
    tmp_path: Path,
) -> None:
    store = PathClockLiquiditySideStoreV3(tmp_path / "proof-conflict")
    rows = _candles(3)
    _observe_rows(
        store,
        3,
        rows=rows,
        proof=_time_proof(3),
        duration=900,
    )

    with pytest.raises(
        PathClockLiquidityStoreValidationError,
        match="conflicts with different JPCLF evidence",
    ):
        _observe_rows(
            store,
            3,
            rows=rows,
            proof=_time_proof(
                3,
                timestamp_source="RESOLVER_BOUND_BOUNDARY_GRID",
            ),
            duration=900,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "schema_version",
            "PG_UNPROVEN_TIME_V3",
            "must use PG_PROVEN_CLOSED_CANDLE_TIME_V3",
        ),
        ("timestamp_semantic", "BAR_OPEN", "must be BAR_CLOSE"),
        ("timestamp_source", "CAPTURE_TIME", "is not admitted"),
        ("proof_source", "CALLER_CLAIM", "must be bound by the V3 identity resolver"),
        ("bound_row_index", -1, "must be a nonnegative integer"),
        ("transition_count", -1, "must be a nonnegative integer"),
        ("source_cadence_seconds", 60, "does not match the study cadence"),
        ("contiguous_from_previous", "yes", "must be boolean"),
    ),
)
def test_time_proof_contract_rejects_unproven_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    proof = _time_proof(3)
    proof[field] = value

    with pytest.raises(PathClockLiquidityStoreValidationError, match=message):
        _observe_rows(
            PathClockLiquiditySideStoreV3(tmp_path / f"invalid-{field}"),
            3,
            rows=_candles(3),
            proof=proof,
        )


@pytest.mark.parametrize(
    ("proof_changes", "message"),
    (
        (
            {"close_epoch_seconds": 900.25},
            "must resolve to an exact whole second",
        ),
        (
            {"observed_epoch_seconds": 901.0, "observation_latency_seconds": 2.0},
            "latency does not match",
        ),
        (
            {"observed_epoch_seconds": 899.0, "observation_latency_seconds": -1.0},
            "cannot precede its proven close",
        ),
        (
            {"observed_epoch_seconds": 1_200.0, "observation_latency_seconds": 300.0},
            "must be less than one source cadence",
        ),
    ),
)
def test_time_proof_contract_rejects_inexact_or_late_clock_evidence(
    tmp_path: Path,
    proof_changes: dict[str, object],
    message: str,
) -> None:
    proof = _time_proof(3)
    proof.update(proof_changes)

    with pytest.raises(PathClockLiquidityStoreValidationError, match=message):
        _observe_rows(
            PathClockLiquiditySideStoreV3(tmp_path / "invalid-clock"),
            3,
            rows=_candles(3),
            proof=proof,
        )


def test_duration_must_have_an_exact_closed_candle_endpoint(tmp_path: Path) -> None:
    result = _observe(
        PathClockLiquiditySideStoreV3(tmp_path / "jpclf"),
        3,
        duration=1_000,
    )

    assert result["status"] == "NOT_ALIGNED_TO_CLOSED_CANDLE_GRID"
    assert result["new_entry_eligible"] is False
    assert result["active_anchor_count"] == 0


def test_missing_exact_time_proof_keeps_pair_behavior_forecast_without_veto() -> None:
    pending = pending_path_clock_liquidity_v3(
        "PG_PROVEN_CLOSED_CANDLE_TIME_V3 proof is unavailable.",
        contract_duration_seconds=3_000,
        candidate_direction="SELL",
        source_cadence_seconds=300,
        symbol="EUR/NZD",
        timeframe="M5",
        closed_candle_key="eur-nzd-close-49",
        closed_candle_sequence=49,
        forecast_context={
            "candidate_direction": "SELL",
            "directional_confidence": 0.72,
            "current_regime": "DOWNTREND",
            "current_behavior": {
                "status": "STUDIED",
                "candle_count": 28,
                "current_state": {"state": "REST", "candle_count": 1},
                "swing_summary": {
                    "up": {"average_candles": 1.0},
                    "down": {"average_candles": 1.6667},
                },
                "rest_summary": {"average_candles": 1.0},
            },
            "pair_profile": {
                "observation_count": 14,
                "candle_count": 22,
                "behavior": {
                    "segment_counts": {
                        "PIXEL_PRICE_PROXY|DOWN_SWING": 3,
                        "PIXEL_PRICE_PROXY|REST": 3,
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
                    },
                    "transition_counts": {
                        "PIXEL_PRICE_PROXY|REST->DOWN_SWING": 3,
                        "PIXEL_PRICE_PROXY|REST->UP_SWING": 1,
                    },
                },
            },
        },
    )

    assert pending["status"] == "INSUFFICIENT_PROVEN_CLOSED_CANDLE_EVIDENCE"
    assert pending["new_entry_eligible"] is True
    assert pending["timing_read"]["status"] == "FORWARD_ESTIMATE_ONLY"
    assert pending["timing_read"]["state"] == "FORECAST_AVAILABLE"
    assert pending["timing_read"]["timing_veto"] is False
    assert pending["timing_read"]["survival_probability"] is None
    assert pending["timing_read"]["support_count"] == 0
    assert pending["symbol"] == "EUR/NZD"
    assert pending["timeframe"] == "M5"
    assert pending["closed_candle_key"] == "eur-nzd-close-49"
    assert pending["closed_candle_sequence"] == 49
    assert pending["freshness_state"] == "CURRENT_CLOSED_CANDLE"
    forecast = pending["forward_timing_forecast"]
    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["candidate_direction"] == "DOWN"
    assert forecast["forecast_horizon_seconds"] == 3_000
    assert forecast["timing_estimate"]["source_tier"] == "PAIR"
    assert forecast["probability"]["value"] is None
    assert forecast["probability"]["confidence"] is None
    assert forecast["event_likelihood"]["value"] is None
    assert forecast["evidence_confidence"]["value"] is None
    assert forecast["expected_pre_move"]["sweep_probability"] is None
    assert forecast["stop_survival"]["value"] is None
    assert forecast["move_window"]["exact_wall_clock_proven"] is False
    assert forecast["move_window"]["relative_to"] == "CLOSED_CANDLE_ANCHOR"
    assert forecast["move_window"]["rolling_wall_clock"] is False
    assert "anchor_close_epoch_seconds" not in forecast["move_window"]
    assert "target_window_start_epoch_seconds" not in forecast["move_window"]
    assert "target_window_end_epoch_seconds" not in forecast["move_window"]
    assert forecast["lineage"]["closed_candle_key"] == "eur-nzd-close-49"
    assert forecast["lineage"]["freshness_state"] == "CURRENT_CLOSED_CANDLE"
    assert len(forecast["lineage"]["lineage_digest"]) == 64
    assert forecast["enter_now"]["permission"] is False


def test_replay_gate_rejects_unpaired_sweep_outcomes_even_if_axes_improve() -> None:
    candidate: list[dict[str, Any]] = []
    baseline: list[dict[str, Any]] = []
    for index in range(32):
        shared = {
            "symbol": "USD/CAD OTC",
            "timeframe": "M5",
            "coordinate_space": "NORMALIZED_MEDIAN_RANGE",
            "order_domain": "CLOSED_TIMESTAMP_V1",
            "frozen_on_closed_candle": True,
            "future_leakage_detected": False,
            "closed_candle_key": f"replay-{index}",
            "horizon_seconds": 1800,
            "observed_direction": "UP",
            "observed_move_time_seconds": 1200,
        }
        candidate.append(
            {
                **shared,
                "predicted_direction": "UP",
                "timing_window_seconds": {"start": 900, "end": 1500},
                "sweep_outcomes": [
                    {
                        "stop_distance_mru": 0.5,
                        "move_size_mru": 1.0,
                        "predicted_survival_probability": 1.0,
                        "survived_until_move": True,
                    }
                ],
            }
        )
        baseline.append(
            {
                **shared,
                "predicted_direction": "DOWN",
                "timing_window_seconds": {"start": 0, "end": 300},
                "sweep_outcomes": [
                    {
                        "stop_distance_mru": 0.5,
                        "move_size_mru": 1.0,
                        "predicted_survival_probability": 0.5,
                        "survived_until_move": False,
                    }
                ],
            }
        )

    baseline_score, candidate_score, gate = (
        PathClockLiquiditySideStoreV3._promotion_evidence(  # pyright: ignore[reportPrivateUsage]
            {
                "symbol": "USD/CAD OTC",
                "timeframe": "M5",
                "candidate_replays": candidate,
                "baseline_replays": baseline,
            }
        )
    )

    assert baseline_score["eligible_replay_count"] == 32
    assert candidate_score["eligible_replay_count"] == 32
    assert gate["passed"] is False
    assert gate["status"] == "RETAIN_BASELINE"
    assert gate["paired_evaluation"]["passed"] is False
    assert gate["all_axes_improved"] is True


def test_completed_no_target_horizon_is_not_dropped_from_replay() -> None:
    anchor = {
        "anchor_closed_candle_key": "no-target-close",
        "duration_seconds": 1_200,
        "studied_direction": "UP",
        "points": [
            {"elapsed_seconds": 0, "path_mru": 0.0, "high_mru": 0.0, "low_mru": 0.0},
            {"elapsed_seconds": 300, "path_mru": 0.05, "high_mru": 0.1, "low_mru": -0.1},
            {"elapsed_seconds": 600, "path_mru": -0.05, "high_mru": 0.1, "low_mru": -0.2},
            {"elapsed_seconds": 900, "path_mru": 0.1, "high_mru": 0.2, "low_mru": -0.1},
            {"elapsed_seconds": 1_200, "path_mru": 0.0, "high_mru": 0.15, "low_mru": -0.1},
        ],
        "admission_prediction": {
            "symbol": "USD/CAD OTC",
            "timeframe": "M5",
            "baseline_direction": "DOWN",
            "source_cadence_seconds": 300,
            "timing_window_seconds": {"start": 900, "end": 1_100},
            "selected_stop_distance_mru": 0.5,
            "selected_move_size_mru": 1.0,
            "sweep_predictions": [
                {
                    "stop_distance_mru": 0.5,
                    "move_size_mru": 1.0,
                    "predicted_survival_probability": 0.8,
                }
            ],
        },
    }

    replay = PathClockLiquiditySideStoreV3._mature_replay(anchor)  # pyright: ignore[reportPrivateUsage]

    assert replay is not None
    candidate, baseline, early = replay
    assert early is False
    assert candidate["observed_move_occurred"] is False
    assert candidate["observed_move_time_seconds"] == 1_200
    assert candidate["observed_direction"] == "FLAT"
    assert candidate["sweep_outcomes"][0]["survived_until_move"] is False
    assert candidate["sweep_outcomes"][0]["stop_distance_mru"] == 0.5
    assert candidate["sweep_outcomes"][0]["move_size_mru"] == 1.0
    assert baseline["sweep_outcomes"][0]["stop_distance_mru"] == 0.5
    assert baseline["sweep_outcomes"][0]["move_size_mru"] == 1.0
    assert (
        baseline["sweep_outcomes"][0]["survived_until_move"]
        == candidate["sweep_outcomes"][0]["survived_until_move"]
    )
    assert baseline["observed_move_occurred"] is False


@pytest.mark.parametrize(
    ("survival", "pullback_ahead", "supports", "veto", "state"),
    (
        (0.78, 0.30, True, False, "ELIGIBLE_NOW"),
        (0.48, 0.30, False, True, "SWEEP_RISK"),
        (0.78, 0.74, False, True, "DRAWDOWN_AHEAD"),
    ),
)
def test_promoted_timing_is_an_asymmetric_support_or_veto(
    monkeypatch: pytest.MonkeyPatch,
    survival: float,
    pullback_ahead: float,
    supports: bool,
    veto: bool,
    state: str,
) -> None:
    class _Field:
        @staticmethod
        def pair_dna_partition_summary() -> dict[str, Any]:
            return {"trajectory_count": 64, "contains_trajectory_points": False}

    promotion: dict[str, Any] = {
        "passed": True,
        "status": "PROMOTION_ELIGIBLE",
        "minimum_replays": 32,
        "support": {"baseline": 32, "candidate": 32, "passed": True},
        "all_axes_improved": True,
    }

    def promotion_evidence(
        _state: object,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return {}, {}, promotion

    monkeypatch.setattr(
        PathClockLiquiditySideStoreV3,
        "_promotion_evidence",
        staticmethod(promotion_evidence),
    )
    result = PathClockLiquiditySideStoreV3._compact_public(  # pyright: ignore[reportPrivateUsage]
        {"active_anchors": [], "audit": {}},
        _Field(),  # type: ignore[arg-type]
        duration={
            "new_entry_eligible": True,
            "requested_duration_seconds": 1800,
        },
        latest_freeze={
            "closed_candle_key": "close-100",
            "closed_at_seconds": 30_000,
            "studied_direction": "UP",
            "contract_duration_seconds": 1800,
            "remaining_seconds": 1800,
            "scenario_estimates": [
                {
                    "status": "STUDIED",
                    "eligible": True,
                    "support_count": 32,
                    "stop_distance_mru": 0.5,
                    "survival_probability": survival,
                    "probability_worst_drawdown_still_ahead": pullback_ahead,
                }
            ],
        },
        discontinuity_censored=0,
    )

    assert result["timing_read"]["timing_supports_entry"] is supports
    assert result["timing_read"]["timing_veto"] is veto
    assert result["timing_read"]["state"] == state
    assert result["grants_entry_permission"] is False
