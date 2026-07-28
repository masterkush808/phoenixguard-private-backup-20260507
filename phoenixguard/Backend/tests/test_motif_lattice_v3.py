from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest

from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.motif_lattice_v3 import (
    HISTORICAL_PATH_SCHEMA_VERSION,
    MAX_LATTICE_DEPTH,
    MAX_NODES_PER_LEVEL,
    MAX_PATH_CANDLES,
    MAX_SURVIVAL_HORIZON,
    MOTIF_LATTICE_SCHEMA_VERSION,
    SURVIVAL_EVIDENCE_SCHEMA_VERSION,
    MotifLatticeValidationError,
    build_hierarchical_motif_lattice_v3,
    build_time_to_event_survival_evidence_v3,
    reconstruct_normalized_historical_path_v3,
)


def _bars(count: int = 16, *, timestamp_start: int = 1_700_000_000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous_close = 100.0
    deltas = (0.4, 0.4, 0.8, 0.7, 0.05, -0.04, -0.03, -0.8, -0.7, -0.6)
    for index in range(count):
        open_value = previous_close
        close = open_value + deltas[index % len(deltas)]
        high = max(open_value, close) + 0.45
        low = min(open_value, close) - 0.45
        if index == 0:
            high = 101.0
            low = 99.0
        elif index == 1:
            # An exact upper penetration and close reclaim of candle zero.
            high = 102.0
            low = 99.8
            close = 100.8
        rows.append(
            {
                "candle_id": f"bar-{timestamp_start}-{index}",
                "timestamp": timestamp_start + index * 300,
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "is_closed": True,
            }
        )
        previous_close = close
    return rows


def _history(
    *,
    bars: list[dict[str, Any]] | None = None,
    states: list[str] | None = None,
    symbol: str = "CAD/JPY OTC",
    timestamp_start: int = 1_700_000_000,
) -> dict[str, Any]:
    raw = bars if bars is not None else _bars(timestamp_start=timestamp_start)
    candle_study = analyze_candle_sequence_v3(raw)
    behavior_study = measure_market_behavior_v3(candle_study, timeframe_seconds=300)
    if states is not None:
        assert len(states) == len(raw)
        behavior_study["states"] = [
            {
                "index": index,
                "candle_id": candle_study["candles"][index]["candle_id"],
                "state": state,
                "movement_vs_median_range": 0.0,
            }
            for index, state in enumerate(states)
        ]
    return {
        "symbol": symbol,
        "timeframe": "M5",
        "order_domain": "CLOSED_TIMESTAMP_V1",
        "candle_study": candle_study,
        "behavior_study": behavior_study,
    }


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in cast(Mapping[object, object], value).items():
            keys.add(str(key))
            keys.update(_all_keys(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in cast(Sequence[object], value):
            keys.update(_all_keys(nested))
    return keys


def test_hierarchical_lattice_composes_all_four_levels_deterministically() -> None:
    history = _history(
        states=[
            "UP_SWING",
            "UP_SWING",
            "UP_SWING",
            "REST",
            "REST",
            "DOWN_SWING",
            "DOWN_SWING",
            "DOWN_SWING",
            "REST",
            "REST",
            "UP_SWING",
            "UP_SWING",
            "UP_SWING",
            "UP_SWING",
            "REST",
            "DOWN_SWING",
        ]
    )

    first = build_hierarchical_motif_lattice_v3(history)
    second = build_hierarchical_motif_lattice_v3(history)

    assert first == second
    assert first["schema_version"] == MOTIF_LATTICE_SCHEMA_VERSION
    assert first["study_only"] is True
    assert first["causal"] is False
    assert first["execution_authority"] is False
    assert first["grants_entry_permission"] is False
    assert first["depth"] == 4
    assert len(first["levels"]) == 4
    assert all(level["published_count"] <= MAX_NODES_PER_LEVEL for level in first["levels"])

    level_zero = first["levels"][0]["nodes"]
    penetration = level_zero[1]["features"]["wick_penetration"]
    assert penetration["above_previous_high_in_current_ranges"] > 0.0
    assert penetration["upper_reclaim_depth_in_current_ranges"] > 0.0
    assert level_zero[1]["composition"]["child_node_ids"] == []

    assert first["levels"][1]["nodes"]
    assert first["levels"][2]["nodes"]
    assert first["levels"][3]["nodes"]
    for node in first["levels"][1]["nodes"]:
        assert node["composition"]["child_level"] == 0
        assert len(node["composition"]["child_node_ids"]) == node["span"]["candle_count"]
    for node in first["levels"][2]["nodes"]:
        assert node["composition"]["child_level"] == 1
        assert node["features"]["atom_span_sequence"]
        assert sum(node["features"]["atom_span_sequence"]) == node["span"]["candle_count"]
    for node in first["levels"][3]["nodes"]:
        assert node["composition"]["published_child_count"] >= 1
        assert node["features"]["regime_state"] in {"UP_SWING", "REST", "DOWN_SWING"}

    assert not {
        "candle_id",
        "timestamp",
        "stable_candle_identity",
        "source_values",
        "ohlc",
    } & _all_keys(first)


def test_lattice_bounds_depth_and_nodes_without_nondeterministic_eviction() -> None:
    history = _history(bars=_bars(64))
    result = build_hierarchical_motif_lattice_v3(
        history,
        max_depth=2,
        max_nodes_per_level=11,
    )

    assert result["depth"] == 2
    assert len(result["levels"]) == 2
    assert all(level["published_count"] <= 11 for level in result["levels"])
    assert result["levels"][0]["candidate_count"] == 64
    assert result["levels"][0]["truncated_count"] == 53
    assert result == build_hierarchical_motif_lattice_v3(
        history,
        max_depth=2,
        max_nodes_per_level=11,
    )

    with pytest.raises(MotifLatticeValidationError, match="max_depth"):
        build_hierarchical_motif_lattice_v3(history, max_depth=MAX_LATTICE_DEPTH + 1)
    with pytest.raises(MotifLatticeValidationError, match="max_nodes_per_level"):
        build_hierarchical_motif_lattice_v3(
            history,
            max_nodes_per_level=MAX_NODES_PER_LEVEL + 1,
        )


def test_lattice_rejects_forming_mixed_coordinate_and_gapped_history() -> None:
    forming = _history()
    forming["candle_study"]["candles"][4]["closed"] = False
    with pytest.raises(MotifLatticeValidationError, match="not a proven closed candle"):
        build_hierarchical_motif_lattice_v3(forming)

    mixed = _history()
    mixed["candle_study"]["candles"][4]["coordinate_space"] = "PIXEL_PRICE_PROXY"
    with pytest.raises(MotifLatticeValidationError, match="mix coordinate spaces"):
        build_hierarchical_motif_lattice_v3(mixed)

    gapped = _history()
    gapped["candle_study"]["candles"][4]["timestamp"] += 300
    with pytest.raises(MotifLatticeValidationError, match="contiguous timeframe interval"):
        build_hierarchical_motif_lattice_v3(gapped)


def test_tracker_event_domain_requires_resolver_proof_and_contiguous_sequence() -> None:
    history = _history()
    history["order_domain"] = "TRACKER_EVENT_SEQUENCE_V3"
    for index, candle in enumerate(history["candle_study"]["candles"]):
        candle["timestamp"] = None
        candle["identity_proof_source"] = "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
        candle["closed_candle_sequence"] = 40 + index

    valid = build_hierarchical_motif_lattice_v3(history, max_depth=1)
    assert valid["order_domain"] == "TRACKER_EVENT_SEQUENCE_V3"

    history["candle_study"]["candles"][5]["closed_candle_sequence"] += 1
    with pytest.raises(MotifLatticeValidationError, match="contiguous resolver events"):
        build_hierarchical_motif_lattice_v3(history, max_depth=1)


def test_survival_evidence_exposes_at_risk_events_censoring_and_km_curve() -> None:
    states = ["REST", "REST", "UP_SWING", "UP_SWING", "REST", "DOWN_SWING"]
    histories = [
        _history(bars=_bars(6, timestamp_start=1_700_000_000), states=states),
        _history(bars=_bars(6, timestamp_start=1_700_100_000), states=states),
    ]

    result = build_time_to_event_survival_evidence_v3(
        histories,
        max_horizon=8,
        min_support=2,
    )

    assert result["schema_version"] == SURVIVAL_EVIDENCE_SCHEMA_VERSION
    assert result["history_count"] == 2
    assert result["causal"] is False
    assert result["execution_authority"] is False
    rest_end = next(
        curve
        for curve in result["curves"]
        if curve["event_type"] == "REST_END" and curve["origin_state"] == "REST"
    )
    assert rest_end["status"] == "SUPPORTED"
    assert rest_end["support"] == 6
    assert rest_end["event_count"] == 6
    assert rest_end["right_censored_count"] == 0
    assert rest_end["median_event_time_closed_candles"] == 1
    assert rest_end["restricted_mean_event_free_closed_candles"] == 1.333333
    assert rest_end["curve"] == [
        {
            "closed_candles": 1,
            "elapsed_seconds": 300,
            "at_risk": 6,
            "events": 4,
            "censored": 0,
            "survival_probability": 0.333333,
            "cumulative_event_probability": 0.666667,
            "survival_confidence_interval_95": [0.046082, 0.675564],
        },
        {
            "closed_candles": 2,
            "elapsed_seconds": 600,
            "at_risk": 2,
            "events": 2,
            "censored": 0,
            "survival_probability": 0.0,
            "cumulative_event_probability": 1.0,
            "survival_confidence_interval_95": [0.0, 0.0],
        },
    ]
    direction_change = next(
        curve
        for curve in result["curves"]
        if curve["event_type"] == "DIRECTION_CHANGE"
        and curve["origin_state"] == "UP_SWING"
    )
    assert direction_change["support"] == 4
    assert direction_change["event_count"] == 4
    assert direction_change["curve"][0]["events"] == 0
    assert direction_change["curve"][1]["events"] == 2
    assert "candle_id" not in _all_keys(result)
    assert "timestamp" not in _all_keys(result)


def test_survival_evidence_keeps_right_censoring_and_deduplicates_exact_history() -> None:
    states = ["UP_SWING", "UP_SWING", "UP_SWING", "REST", "REST"]
    history = _history(bars=_bars(5), states=states)
    result = build_time_to_event_survival_evidence_v3(
        [history, deepcopy(history)],
        max_horizon=3,
        min_support=2,
    )

    assert result["history_count"] == 1
    assert result["duplicate_history_count"] == 1
    direction_change = next(
        curve
        for curve in result["curves"]
        if curve["event_type"] == "DIRECTION_CHANGE"
        and curve["origin_state"] == "UP_SWING"
    )
    assert direction_change["event_count"] == 0
    assert direction_change["right_censored_count"] == direction_change["support"]
    assert direction_change["median_event_time_closed_candles"] is None
    assert direction_change["curve"][-1]["censored"] >= 1


def test_survival_rejects_mixed_scope_and_hard_bound_overruns() -> None:
    first = _history(bars=_bars(6, timestamp_start=1_700_000_000))
    second = _history(
        bars=_bars(6, timestamp_start=1_700_100_000),
        symbol="GBP/USD OTC",
    )
    with pytest.raises(MotifLatticeValidationError, match="cannot mix pair"):
        build_time_to_event_survival_evidence_v3([first, second])
    with pytest.raises(MotifLatticeValidationError, match="max_horizon"):
        build_time_to_event_survival_evidence_v3(
            [first],
            max_horizon=MAX_SURVIVAL_HORIZON + 1,
        )
    with pytest.raises(MotifLatticeValidationError, match="bounded capacity"):
        build_time_to_event_survival_evidence_v3([first], max_observations=1)


def _path_bars(*, future_high: float = 103.5) -> list[dict[str, Any]]:
    geometry = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 102.0, 99.0, 101.0),
        (101.0, 102.0, 100.0, 101.0),
        (101.0, 104.0, 100.0, 103.0),
        (103.0, future_high, 99.0, 100.0),
    ]
    return [
        {
            "candle_id": f"path-{index}",
            "timestamp": 1_700_000_000 + index * 300,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "is_closed": True,
        }
        for index, (open_value, high, low, close) in enumerate(geometry)
    ]


def test_exact_path_reconstruction_reports_mae_mfe_efficiency_and_state_time() -> None:
    history = _history(
        bars=_path_bars(),
        states=["REST", "UP_SWING", "REST", "UP_SWING", "DOWN_SWING"],
    )
    result = reconstruct_normalized_historical_path_v3(
        history,
        anchor_index=2,
        reference_direction="UP",
        normalization_lookback=3,
    )

    assert result["schema_version"] == HISTORICAL_PATH_SCHEMA_VERSION
    assert result["status"] == "RECONSTRUCTED"
    assert result["causal"] is False
    assert result["historical_only"] is True
    assert result["execution_authority"] is False
    assert result["reference_direction_is_trade_instruction"] is False
    assert result["point_count"] == 3
    assert result["normalization"] == {
        "unit": "MEDIAN_CANDLE_RANGE",
        "lookback_start_index": 0,
        "lookback_end_index": 2,
        "lookback_candle_count": 3,
        "uses_only_candles_known_at_anchor": True,
        "future_path_influences_scale": False,
        "raw_scale_published": False,
    }
    assert result["excursion_window"] == {
        "anchor_candle_is_reference_only": True,
        "excursions_begin_after_anchor": True,
    }
    assert result["points"][1]["normalized_ohlc_from_anchor_close"] == {
        "open": 0.0,
        "high": 1.5,
        "low": -0.5,
        "close": 1.0,
    }
    summary = result["path_summary"]
    assert summary["maximum_favorable_excursion_in_median_ranges"] == 1.5
    assert summary["maximum_adverse_excursion_in_median_ranges"] == 1.0
    assert summary["maximum_favorable_excursion_offset"] == 1
    assert summary["maximum_adverse_excursion_offset"] == 2
    assert summary["final_displacement_in_median_ranges"] == -0.5
    assert summary["final_path_efficiency"] == 0.2
    assert summary["state_transition_count"] == 2
    assert sum(row["closed_candles"] for row in summary["time_in_states"].values()) == 3
    assert result["proof_certificate"]["closed_candles_only"] is True
    assert not {
        "candle_id",
        "timestamp",
        "stable_candle_identity",
        "source_values",
        "ohlc",
    } & _all_keys(result)


def test_path_normalization_excludes_future_and_down_reference_flips_excursions() -> None:
    ordinary = _history(bars=_path_bars())
    extreme_future = _history(bars=_path_bars(future_high=1_000.0))
    up = reconstruct_normalized_historical_path_v3(
        ordinary,
        anchor_index=2,
        reference_direction="UP",
        normalization_lookback=3,
    )
    changed = reconstruct_normalized_historical_path_v3(
        extreme_future,
        anchor_index=2,
        reference_direction="UP",
        normalization_lookback=3,
    )
    down = reconstruct_normalized_historical_path_v3(
        ordinary,
        anchor_index=2,
        reference_direction="DOWN",
        normalization_lookback=3,
    )

    assert (
        up["points"][1]["normalized_ohlc_from_anchor_close"]
        == changed["points"][1]["normalized_ohlc_from_anchor_close"]
    )
    assert down["path_summary"]["maximum_favorable_excursion_in_median_ranges"] == 1.0
    assert down["path_summary"]["maximum_adverse_excursion_in_median_ranges"] == 1.5
    assert down["path_id"] != up["path_id"]


def test_path_reconstruction_rejects_invalid_direction_and_requested_overrun() -> None:
    history = _history(bars=_path_bars())
    with pytest.raises(MotifLatticeValidationError, match="reference_direction"):
        reconstruct_normalized_historical_path_v3(
            history,
            anchor_index=2,
            reference_direction="BUY",
        )
    with pytest.raises(MotifLatticeValidationError, match="max_path_candles"):
        reconstruct_normalized_historical_path_v3(
            history,
            anchor_index=0,
            end_index=4,
            max_path_candles=4,
            reference_direction="UP",
        )
    with pytest.raises(MotifLatticeValidationError, match="max_path_candles"):
        reconstruct_normalized_historical_path_v3(
            history,
            anchor_index=2,
            max_path_candles=MAX_PATH_CANDLES + 1,
            reference_direction="UP",
        )
