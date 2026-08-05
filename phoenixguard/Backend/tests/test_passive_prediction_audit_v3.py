from __future__ import annotations

from typing import Any

from phoenixguard.mobile_api.operator_workspace_v1 import (
    path_clock_liquidity_contract_v3,
)
from phoenixguard.study.path_clock_liquidity_store_v3 import (
    PathClockLiquiditySideStoreV3,
)


def _score(*, direction: float, timing: float, sweep: float, calibration: float) -> dict[str, Any]:
    return {
        "audited_replay_count": 1,
        "eligible_replay_count": 1,
        "excluded_early_move_count": 0,
        "sweep_outcome_count": 2,
        "metrics": {
            "directional_accuracy": direction,
            "timing_accuracy": timing,
            "sweep_survival_rate": sweep,
            "calibration_score": calibration,
            "expected_calibration_error": 1.0 - calibration,
            "brier_score": 0.16,
        },
        "calibration_bins": [{"private": "not public"}],
        "evaluation_cohort_digest": "private-digest",
    }


def _audit() -> dict[str, Any]:
    candidate_score = _score(
        direction=1.0,
        timing=1.0,
        sweep=0.5,
        calibration=0.8,
    )
    baseline_score = _score(
        direction=0.0,
        timing=0.0,
        sweep=0.5,
        calibration=0.5,
    )
    state = {
        "symbol": "USD/CAD OTC",
        "timeframe": "M5",
        "active_anchors": [
            {
                "anchor_closed_candle_key": "close-11",
                "anchor_timestamp_seconds": 3_300,
                "duration_seconds": 1_800,
                "studied_direction": "UP",
                "admission_prediction": {
                    "timing_window_seconds": {"start": 900, "end": 1_500},
                    "selected_stop_distance_mru": 0.5,
                    "selected_move_size_mru": 1.0,
                    "sweep_predictions": [{}, {}],
                    "frozen_on_closed_candle": True,
                    "future_leakage_detected": False,
                },
            },
            {
                # A normal active field anchor is not a frozen model forecast.
                "anchor_closed_candle_key": "close-12",
                "anchor_timestamp_seconds": 3_600,
                "duration_seconds": 1_800,
                "studied_direction": "UP",
            },
        ],
        "candidate_replays": [
            {
                "closed_candle_key": "close-5",
                "horizon_seconds": 1_800,
                "predicted_direction": "UP",
                "observed_direction": "UP",
                "observed_move_occurred": True,
                "observed_move_time_seconds": 1_200,
                "timing_window_seconds": {"start": 900, "end": 1_500},
                "sweep_outcomes": [
                    {"survived_until_move": True},
                    {"survived_until_move": False},
                ],
                "frozen_on_closed_candle": True,
                "future_leakage_detected": False,
            }
        ],
    }
    return PathClockLiquiditySideStoreV3._passive_prediction_audit(  # pyright: ignore[reportPrivateUsage]
        state,
        baseline_score=baseline_score,
        candidate_score=candidate_score,
    )


def test_passive_audit_counts_only_frozen_forecasts_and_scores_exact_outcome() -> None:
    audit = _audit()

    assert audit["status"] == "AUDITED_OUTCOMES"
    assert audit["frozen_forecast_count"] == 2
    assert audit["pending_outcome_count"] == 1
    assert audit["matured_outcome_count"] == 1
    assert audit["latest_frozen_forecast"]["closed_candle_key"] == "close-11"
    outcome = audit["latest_matured_outcome"]
    assert outcome["direction_correct"] is True
    assert outcome["timing_correct"] is True
    assert outcome["sweep_survival_rate"] == 0.5
    assert audit["axis_deltas"] == {
        "directional_accuracy": 1.0,
        "timing_accuracy": 1.0,
        "sweep_survival_rate": 0.0,
        "calibration_score": 0.3,
    }
    assert audit["tracks_market_outcomes_only"] is True
    assert audit["places_trades"] is False
    assert audit["execution_authority"] is False


def test_operator_contract_exposes_four_axes_without_private_replay_rows() -> None:
    audit = _audit()
    baseline_score = _score(
        direction=0.0,
        timing=0.0,
        sweep=0.5,
        calibration=0.5,
    )
    candidate_score = _score(
        direction=1.0,
        timing=1.0,
        sweep=0.5,
        calibration=0.8,
    )
    contract = path_clock_liquidity_contract_v3(
        {
            "schema_version": "PG_PATH_CLOCK_LIQUIDITY_PUBLIC_STUDY_V3",
            "status": "STUDIED",
            "study_only": True,
            "execution_authority": False,
            "grants_entry_permission": False,
            "symbol": "USD/CAD OTC",
            "timeframe": "M5",
            "closed_candle_key": "close-12",
            "candidate_replay_score": candidate_score,
            "baseline_replay_score": baseline_score,
            "passive_prediction_audit_v3": audit,
        }
    )

    public_audit = contract["passive_prediction_audit_v3"]
    assert public_audit["status"] == "AUDITED_OUTCOMES"
    assert public_audit["places_trades"] is False
    assert public_audit["can_grant_entry_permission"] is False
    assert public_audit["candidate_metrics"]["timing_accuracy"] == 1.0
    assert public_audit["latest_matured_outcome"]["timing_correct"] is True
    assert contract["candidate_replay_calibration"]["metrics"] == {
        "directional_accuracy": 1.0,
        "timing_accuracy": 1.0,
        "sweep_survival_rate": 0.5,
        "calibration_score": 0.8,
        "expected_calibration_error": 0.2,
        "brier_score": 0.16,
    }
    serialized = repr(contract)
    assert "private-digest" not in serialized
    assert "calibration_bins" not in serialized
