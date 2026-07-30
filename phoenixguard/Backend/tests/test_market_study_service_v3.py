from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from pathlib import Path
from typing import Any, cast

import pytest

from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.market_study_service_v3 import (
    MarketStudyServiceV3,
    _continuous_advanced_studies,  # pyright: ignore[reportPrivateUsage]
    _object_conditioned_time_to_event,  # pyright: ignore[reportPrivateUsage]
)
from phoenixguard.study.motif_lattice_v3 import (
    MAX_PATH_CANDLES,
    MotifLatticeValidationError,
)
from phoenixguard.study.study_claim_proof_v3 import (
    canonical_public_study_hash_v3,
)


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in cast(Mapping[object, object], value).items():
            keys.add(str(key))
            keys.update(_nested_keys(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in cast(Sequence[object], value):
            keys.update(_nested_keys(nested))
    return keys


def _candles(_offset: float, sequence: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(8):
        global_index = sequence + index
        open_value = 100.0 + global_index * 0.18
        close_value = open_value + (0.13 if global_index % 3 else 0.08)
        rows.append(
            {
                "candle_id": f"bar-{global_index}",
                "timestamp": global_index * 300,
                "open": open_value,
                "high": close_value + 0.05,
                "low": open_value - 0.04,
                "close": close_value,
                "closed": True,
            }
        )
    return rows


def _resolver_bound_jpclf_candles(sequence: int) -> list[dict[str, object]]:
    rows = _candles(0.0, sequence)
    for offset, row in enumerate(rows):
        event_sequence = sequence + offset
        row.update(
            {
                "identity_stable": True,
                "stable_candle_identity": f"jpclf-close-{event_sequence}",
                "identity_proof_source": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
                "closed_candle_sequence": event_sequence,
                "resolver_bound_row_index": offset,
            }
        )
    return rows


def _jpclf_time_proof(
    sequence: int,
    *,
    timestamp_source: str = "SOURCE_CLOSE_TIME",
) -> dict[str, object]:
    closed_sequence = sequence + 7
    close_epoch_seconds = closed_sequence * 300
    observed_epoch_seconds = close_epoch_seconds + 5
    return {
        "schema_version": "PG_PROVEN_CLOSED_CANDLE_TIME_V3",
        "symbol": "USD/CAD OTC",
        "timeframe": "M5",
        "closed_candle_key": f"jpclf-close-{closed_sequence}",
        "closed_candle_sequence": closed_sequence,
        "close_epoch_seconds": close_epoch_seconds,
        "timestamp_semantic": "BAR_CLOSE",
        "timestamp_source": timestamp_source,
        "proof_source": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
        "bound_row_index": 7,
        "transition_count": 1,
        "source_cadence_seconds": 300,
        "observed_epoch_seconds": observed_epoch_seconds,
        "observation_latency_seconds": 5,
        "contiguous_from_previous": sequence > 0,
    }


def _pixel_candles(
    sequence: int,
    *,
    scale: float,
    y_origin: float,
    identity_prefix: str = "pixel-bar",
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(8):
        global_index = sequence + index
        open_price = 100.0 + global_index * 0.18
        close_price = open_price + (0.13 if global_index % 3 else 0.08)
        high_price = close_price + 0.05
        low_price = open_price - 0.04
        rows.append(
            {
                "candle_id": f"{identity_prefix}-{global_index}",
                "timestamp": global_index * 300,
                "open_y_px": y_origin - open_price * scale,
                "close_y_px": y_origin - close_price * scale,
                "wick_top_px": y_origin - high_price * scale,
                "wick_bottom_px": y_origin - low_price * scale,
                "closed": True,
            }
        )
    return rows


def _many_candles(count: int = 128) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        open_value = 80.0 + index * 0.09
        close_value = open_value + (0.07 if index % 4 else -0.03)
        rows.append(
            {
                "candle_id": f"long-bar-{index}",
                "timestamp": 1_700_000_000 + index * 300,
                "open": open_value,
                "high": max(open_value, close_value) + 0.04,
                "low": min(open_value, close_value) - 0.04,
                "close": close_value,
                "closed": True,
            }
        )
    return rows


def _positional_pixel_candles(
    count: int,
    *,
    price_offset: float,
    scale: float,
    y_origin: float,
) -> list[dict[str, object]]:
    """Model a reacquired tracker window whose ids restart by position."""

    rows: list[dict[str, object]] = []
    for index in range(count):
        open_price = 100.0 + price_offset + index * 0.18
        close_price = open_price + (0.13 if index % 3 else 0.08)
        rows.append(
            {
                "track_id": index,
                "open_y_px": y_origin - open_price * scale,
                "close_y_px": y_origin - close_price * scale,
                "wick_top_px": y_origin - (close_price + 0.05) * scale,
                "wick_bottom_px": y_origin - (open_price - 0.04) * scale,
                "closed": True,
            }
        )
    return rows


def _regression() -> dict[str, object]:
    return {
        "timeframe_seconds": 300,
        "major_trend": {
            "side": "BUY",
            "slope": 0.18,
            "confidence": 0.82,
            "window_candles": 8,
        },
        "inner_trend": {
            "side": "BUY",
            "slope": 0.16,
            "confidence": 0.75,
            "window_candles": 4,
        },
    }


def _retracement_candles(*, include_next: bool) -> list[dict[str, object]]:
    geometries = [
        (102.0, 103.0, 101.0, 102.5),
        (101.5, 102.0, 100.0, 101.0),
        (102.5, 106.0, 102.0, 105.0),
        (105.5, 110.0, 105.0, 109.0),
        (108.5, 109.0, 104.0, 105.0),
    ]
    if include_next:
        geometries.append((105.0, 106.0, 103.5, 104.0))
    return [
        {
            "candle_id": f"retracement-bar-{index}",
            "timestamp": 1_720_000_000 + index * 300,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "closed": True,
        }
        for index, (open_value, high, low, close) in enumerate(geometries)
    ]


def _outcome_baseline_candles(*, include_next: bool) -> list[dict[str, object]]:
    geometries = [
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 105.0, 95.0, 100.0),
        (100.0, 105.0, 95.0, 100.0),
    ]
    if include_next:
        geometries.append((100.0, 150.0, 50.0, 100.3))
    return [
        {
            "candle_id": f"baseline-bar-{index}",
            "timestamp": 1_730_000_000 + index * 300,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "closed": True,
        }
        for index, (open_value, high, low, close) in enumerate(geometries)
    ]


def test_outcome_label_baseline_excludes_the_newest_outcome_candle(
    tmp_path: Path,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "causal-baseline-study")
    service.study(
        _outcome_baseline_candles(include_next=False),
        symbol="AUD/CAD OTC",
        timeframe="M5",
        closed_candle_key="baseline-close-1",
        closed_candle_sequence=1,
        regime="SIDEWAYS",
        regression=_regression(),
    )
    result = service.study(
        _outcome_baseline_candles(include_next=True),
        symbol="AUD/CAD OTC",
        timeframe="M5",
        closed_candle_key="baseline-close-2",
        closed_candle_sequence=2,
        regime="TRANSITION",
        regression=_regression(),
    )

    assert result["outcome_maturation"]["status"] == "MATURED"  # type: ignore[index]
    matured = next(
        row
        for row in service.historical.entries()
        if str(row["sequence_id"]).upper().endswith("BASELINE-CLOSE-1")
    )
    # Prior-only median range is 5.5, so +0.3 is directional. Including the
    # 100-point outcome candle would raise the median to 10 and mislabel REST.
    assert matured["outcome"]["direction"] == "UP"
    assert math.isclose(
        float(matured["outcome"]["realized_return"]),
        0.05454545,
        abs_tol=1e-8,
    )


def test_market_study_matures_retracement_confluence_into_pair_dna(
    tmp_path: Path,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "retracement-study")
    object_evidence = [
        {
            "object_type": "ORDER_BLOCK",
            "object_id": "retracement-order-block-1",
            "identity_stable": True,
            "identity_scope": "EXPLICIT",
            "confidence": 0.91,
            "value_bounds": [102.8, 103.0],
            "value_coordinate_space": "PRICE",
            "value_axis_source": "TEST_PRICE_AXIS",
        }
    ]

    first = service.study(
        _retracement_candles(include_next=False),
        symbol="EUR/USD OTC",
        timeframe="M5",
        closed_candle_key="retracement-close-1",
        closed_candle_sequence=1,
        regime="UPTREND",
        regression=_regression(),
        objects=object_evidence,
    )
    graph_study = first["object_relationship_graph"]["retracement_study"]  # type: ignore[index]
    assert graph_study["status"] == "STUDIED"
    assert {row["level_id"] for row in graph_study["observations"]} == {  # type: ignore[index]
        "OTE_70_5",
        "CUSTOM_71_8",
    }

    matured = service.study(
        _retracement_candles(include_next=True),
        symbol="EUR/USD OTC",
        timeframe="M5",
        closed_candle_key="retracement-close-2",
        closed_candle_sequence=2,
        regime="UPTREND",
        regression=_regression(),
        objects=object_evidence,
    )

    assert matured["outcome_maturation"]["status"] == "MATURED"  # type: ignore[index]
    retracement_profile = matured["pair_dna"]["retracement_confluence"]  # type: ignore[index]
    assert retracement_profile["completed_study_count"] == 2
    assert {
        row["partition"]["level_id"]
        for row in retracement_profile["empirical_partitions"]
    } == {"OTE_70_5", "CUSTOM_71_8"}
    assert retracement_profile["level_support"] == [
        {"level_id": "OTE_70_5", "completed_study_count": 1},
        {"level_id": "CUSTOM_71_8", "completed_study_count": 1},
    ]
    assert retracement_profile["partitions_truncated_count"] == 0
    assert retracement_profile["execution_authority"] is False


def test_market_study_learns_prior_outcomes_without_execution_authority(
    tmp_path: Path,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "pair-study")
    result: dict[str, object] = {}
    for sequence in range(1, 5):
        result = service.study(
            _candles(float(sequence), sequence),
            symbol="CAD/JPY OTC",
            timeframe="M5",
            closed_candle_key=f"close-{sequence}",
            closed_candle_sequence=sequence,
            regime="UPTREND",
            regression=_regression(),
            objects=[{"object_type": "CROWDED_PRICE_AREA"}],
            observed_at=f"2026-07-24T00:0{sequence}:00Z",
        )

    assert result["schema_version"] == "PG_MARKET_STUDY_V3"
    assert result["status"] == "STUDIED"
    assert result["study_only"] is True
    assert result["execution_authority"] is False
    assert result["can_grant_entry_permission"] is False
    assert result["directional_read"]["side"] == "BUY"  # type: ignore[index]
    assert result["historical_similarity"]["historical_continuation"]["status"] == "SUPPORTED"  # type: ignore[index]
    assert result["historical_similarity"]["historical_continuation"]["direction"] == "UP"  # type: ignore[index]
    assert result["pair_dna"]["observation_count"] == 3  # type: ignore[index]
    assert result["candle_ledger"]["unique_candle_count"] == 4  # type: ignore[index]
    graph = result["object_relationship_graph"]  # type: ignore[assignment]
    assert graph["schema_version"] == "PG_OBJECT_RELATIONSHIP_GRAPH_V3"  # type: ignore[index]
    assert graph["selected_counts"]["object_nodes"] == 1  # type: ignore[index]
    assert graph["execution_authority"] is False  # type: ignore[index]

    repeated = service.study(
        _candles(4.0, 4),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        closed_candle_key="close-4",
        closed_candle_sequence=4,
        regime="UPTREND",
        regression=_regression(),
    )
    assert repeated == result
    assert service.pair_dna.get_profile("CAD/JPY OTC", "M5")["profile"]["observation_count"] == 3


def test_pending_outcome_survives_service_restart(tmp_path: Path) -> None:
    root = tmp_path / "restart-study"
    first = MarketStudyServiceV3(root)
    first.study(
        _candles(1.0, 1),
        symbol="EUR/USD OTC",
        timeframe="M5",
        closed_candle_key="restart-close-1",
        closed_candle_sequence=1,
        regime="UPTREND",
        regression=_regression(),
    )

    restarted = MarketStudyServiceV3(root)
    result = restarted.study(
        _candles(2.0, 2),
        symbol="EUR/USD OTC",
        timeframe="M5",
        closed_candle_key="restart-close-2",
        closed_candle_sequence=2,
        regime="UPTREND",
        regression=_regression(),
    )

    assert result["status"] == "STUDIED"
    profile = restarted.pair_dna.get_profile("EUR/USD OTC", "M5")["profile"]
    assert profile["observation_count"] == 1
    entries = restarted.historical.entries()
    matured = next(
        row
        for row in entries
        if str(row["sequence_id"]).upper().endswith("RESTART-CLOSE-1")
    )
    assert matured["outcome"]["direction"] == "UP"
    assert restarted.candle_ledger.pair_summary("EUR/USD OTC", "M5")[
        "unique_candle_count"
    ] == 2


def test_pixel_outcome_uses_prior_close_reobserved_on_current_frame_axis(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pixel-reobservation"
    first = MarketStudyServiceV3(root)
    first.study(
        _pixel_candles(1, scale=2.0, y_origin=500.0),
        symbol="GBP/USD OTC",
        timeframe="M5",
        closed_candle_key="pixel-close-1",
        closed_candle_sequence=1,
        regime="UPTREND",
        regression=_regression(),
    )

    restarted = MarketStudyServiceV3(root)
    result = restarted.study(
        _pixel_candles(2, scale=7.0, y_origin=1200.0),
        symbol="GBP/USD OTC",
        timeframe="M5",
        closed_candle_key="pixel-close-2",
        closed_candle_sequence=2,
        regime="UPTREND",
        regression=_regression(),
    )

    assert result["outcome_maturation"]["status"] == "MATURED"  # type: ignore[index]
    entries = restarted.historical.entries()
    matured = next(
        row
        for row in entries
        if str(row["sequence_id"]).upper().endswith("PIXEL-CLOSE-1")
    )
    assert matured["outcome"]["direction"] == "UP"
    assert matured["outcome"]["coordinate_continuity"] == (
        "CURRENT_FRAME_REOBSERVATION"
    )
    profile = restarted.pair_dna.get_profile("GBP/USD OTC", "M5")["profile"]
    assert profile["candle_count"] == 8


def test_reacquired_positional_pixel_window_cannot_false_mature(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pixel-positional-reacquisition"
    service = MarketStudyServiceV3(root)
    service.study(
        _positional_pixel_candles(
            8,
            price_offset=0.0,
            scale=2.0,
            y_origin=500.0,
        ),
        symbol="CAD/CHF OTC",
        timeframe="M5",
        closed_candle_key="positional-close-1",
        closed_candle_sequence=1,
        regime="UPTREND",
        regression=_regression(),
    )

    result = service.study(
        _positional_pixel_candles(
            9,
            price_offset=20.0,
            scale=7.0,
            y_origin=1200.0,
        ),
        symbol="CAD/CHF OTC",
        timeframe="M5",
        closed_candle_key="positional-close-2",
        closed_candle_sequence=2,
        regime="UPTREND",
        regression=_regression(),
    )

    assert result["outcome_maturation"]["status"] == (  # type: ignore[index]
        "SKIPPED_UNPROVEN_COORDINATE_CONTINUITY"
    )
    latest = result["candle_intelligence"]["latest"]  # type: ignore[index]
    assert latest["candle_id"] == "8"  # type: ignore[index]
    assert latest["identity_stable"] is False  # type: ignore[index]
    profile = service.pair_dna.get_profile("CAD/CHF OTC", "M5")
    assert profile["status"] == "NOT_FOUND"
    assert profile["profile"] is None
    assert result["candle_ledger"]["unique_candle_count"] == 2  # type: ignore[index]
    prior = next(
        row
        for row in service.historical.entries()
        if str(row["sequence_id"]).upper().endswith("POSITIONAL-CLOSE-1")
    )
    assert prior.get("outcome", {}).get("direction", "UNKNOWN") == "UNKNOWN"


@pytest.mark.parametrize(
    ("current_sequence", "case_name"),
    ((3, "multi-close-gap"), (1, "replay"), (0, "out-of-order")),
)
def test_one_step_outcome_rejects_gap_replay_and_out_of_order_sequences(
    tmp_path: Path,
    current_sequence: int,
    case_name: str,
) -> None:
    service = MarketStudyServiceV3(tmp_path / case_name)
    service.study(
        _candles(1.0, 1),
        symbol="EUR/CHF OTC",
        timeframe="M5",
        closed_candle_key=f"{case_name}-first",
        closed_candle_sequence=1,
        regime="UPTREND",
        regression=_regression(),
    )

    result = service.study(
        _candles(2.0, 2),
        symbol="EUR/CHF OTC",
        timeframe="M5",
        closed_candle_key=f"{case_name}-second",
        closed_candle_sequence=current_sequence,
        regime="UPTREND",
        regression=_regression(),
    )

    maturation = result["outcome_maturation"]
    assert maturation["status"] == "SKIPPED_UNPROVEN_ONE_STEP_HORIZON"
    assert maturation["previous_closed_candle_sequence"] == 1
    assert maturation["current_closed_candle_sequence"] == current_sequence
    assert maturation["required_horizon_candles"] == 1
    profile = service.pair_dna.get_profile("EUR/CHF OTC", "M5")
    assert profile["status"] == "READY"
    assert profile["profile"]["observation_count"] == 0
    assert profile["profile"]["concept_drift"]["status"] == "READY"


def test_pixel_outcome_skips_when_prior_candle_identity_is_not_reobserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pixel-no-continuity"
    service = MarketStudyServiceV3(root)
    service.study(
        _pixel_candles(1, scale=2.0, y_origin=500.0, identity_prefix="old"),
        symbol="NZD/JPY OTC",
        timeframe="M5",
        closed_candle_key="pixel-missing-1",
        closed_candle_sequence=1,
        regime="UPTREND",
        regression=_regression(),
    )
    second = _pixel_candles(
        2,
        scale=7.0,
        y_origin=1200.0,
        identity_prefix="new",
    )
    for index, row in enumerate(second):
        row["timestamp"] = 100_000 + index * 300
    result = service.study(
        second,
        symbol="NZD/JPY OTC",
        timeframe="M5",
        closed_candle_key="pixel-missing-2",
        closed_candle_sequence=2,
        regime="UPTREND",
        regression=_regression(),
    )

    assert result["outcome_maturation"]["status"] == (  # type: ignore[index]
        "SKIPPED_UNPROVEN_COORDINATE_CONTINUITY"
    )
    profile = service.pair_dna.get_profile("NZD/JPY OTC", "M5")
    assert profile["status"] == "READY"
    assert profile["profile"]["observation_count"] == 0
    assert profile["profile"]["concept_drift"]["status"] == "READY"


def test_pending_outcome_journal_stays_bounded_for_full_window_and_objects(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bounded-pending"
    service = MarketStudyServiceV3(root)
    objects = [
        {
            "object_type": f"REACTION_ZONE_{index}",
            "object_id": f"object-{index}-" + "x" * 48,
            "direction": "BUY" if index % 2 else "SELL",
            "confidence": 0.75,
            "bounds": [0.1, 0.2, 0.7, 0.8],
            "points": [[0.1, 0.2], [0.7, 0.8]],
            "coordinate_space": "NORMALIZED",
            "lifecycle": "ACTIVE",
        }
        for index in range(64)
    ]
    service.study(
        _many_candles(),
        symbol="AUD/CAD OTC",
        timeframe="M5",
        closed_candle_key="bounded-close-127",
        closed_candle_sequence=127,
        regime="TRANSITION",
        regression=_regression(),
        objects=objects,
    )

    pending_path = root / "pending_outcomes_v3.json"
    assert pending_path.stat().st_size < 64 * 1024


def test_market_study_publishes_continuous_advanced_contracts_without_fixed_horizon(
    tmp_path: Path,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "continuous-advanced")
    result = service.study(
        _many_candles(40),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        closed_candle_key="continuous-close-39",
        closed_candle_sequence=39,
        regime="TRANSITION",
        regression=_regression(),
    )

    motif = result["motif_lattice"]
    survival = result["survival_network"]
    path = result["path_reconstruction"]
    assert motif["status"] == "STUDIED"  # type: ignore[index]
    assert motif["depth"] == 4  # type: ignore[index]
    assert motif["closed_candle_count"] == 40  # type: ignore[index]
    assert motif["continuous_window"] == {  # type: ignore[index]
        "fixed_sequence_horizon": False,
        "observed_closed_candle_count": 40,
        "retained_closed_candle_limit": 512,
        "history_source": "CURRENT_RETAINED_CLOSED_HISTORY",
    }
    assert all(
        level["published_count"] <= 512
        for level in motif["levels"]  # type: ignore[index]
    )

    assert survival["status"] == "STUDIED"  # type: ignore[index]
    assert survival["max_horizon_closed_candles"] == 39  # type: ignore[index]
    assert survival["network"]["node_count"] > 0  # type: ignore[index]
    assert survival["network"]["edge_count"] == len(survival["curves"])  # type: ignore[index]
    assert survival["network"]["edge_semantics"] == (  # type: ignore[index]
        "NON_CAUSAL_HISTORICAL_TIME_TO_EVENT_ASSOCIATION"
    )
    assert all(edge["causal"] is False for edge in survival["network"]["edges"])  # type: ignore[index]

    assert path["status"] == "RECONSTRUCTED"  # type: ignore[index]
    assert path["end_index"] == 39  # type: ignore[index]
    assert path["anchor_selection"]["fixed_sequence_horizon"] is False  # type: ignore[index]
    assert path["anchor_selection"]["reference_direction_is_trade_instruction"] is False  # type: ignore[index]
    assert path["point_count"] != 12  # type: ignore[index]
    for advanced in (motif, survival, path):
        assert advanced["study_only"] is True  # type: ignore[index]
        assert advanced["causal"] is False  # type: ignore[index]
        assert advanced["execution_authority"] is False  # type: ignore[index]
        assert advanced["grants_entry_permission"] is False  # type: ignore[index]
        assert not {
            "candle_id",
            "timestamp",
            "stable_candle_identity",
            "source_values",
            "ohlc",
        } & _nested_keys(advanced)


def test_advanced_contracts_fail_closed_without_stable_contiguous_order(
    tmp_path: Path,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "advanced-unproven-order")
    result = service.study(
        _positional_pixel_candles(
            8,
            price_offset=0.0,
            scale=2.0,
            y_origin=500.0,
        ),
        symbol="CAD/CHF OTC",
        timeframe="M5",
        closed_candle_key="unproven-order-close",
        closed_candle_sequence=1,
        regime="UPTREND",
        regression=_regression(),
    )

    assert result["status"] == "STUDIED"
    for name in ("motif_lattice", "survival_network", "path_reconstruction"):
        advanced = result[name]
        assert advanced["status"] == "INSUFFICIENT_PROVEN_HISTORY"  # type: ignore[index]
        assert "order is unproven" in advanced["reason"]  # type: ignore[index]
        assert advanced["continuous_window"]["fixed_sequence_horizon"] is False  # type: ignore[index]
        assert advanced["study_only"] is True  # type: ignore[index]
        assert advanced["execution_authority"] is False  # type: ignore[index]


def test_advanced_tracker_event_history_uses_exact_resolver_order_domain(
    tmp_path: Path,
) -> None:
    candles = _candles(0.0, 20)
    for index, row in enumerate(candles):
        row.pop("timestamp")
        row["stable_candle_identity"] = f"resolver-event-{index}"
        row["identity_stable"] = True
        row["identity_proof_source"] = "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
        row["closed_candle_sequence"] = 200 + index

    result = MarketStudyServiceV3(tmp_path / "advanced-resolver-order").study(
        candles,
        symbol="EUR/JPY OTC",
        timeframe="M5",
        closed_candle_key="resolver-close-207",
        closed_candle_sequence=207,
        regime="UPTREND",
        regression=_regression(),
    )

    assert result["motif_lattice"]["status"] == "STUDIED"  # type: ignore[index]
    assert result["motif_lattice"]["order_domain"] == (  # type: ignore[index]
        "TRACKER_EVENT_SEQUENCE_V3"
    )
    assert result["survival_network"]["order_domain"] == (  # type: ignore[index]
        "TRACKER_EVENT_SEQUENCE_V3"
    )
    assert result["path_reconstruction"]["order_domain"] == (  # type: ignore[index]
        "TRACKER_EVENT_SEQUENCE_V3"
    )


def test_continuous_price_history_extends_from_pair_ledger_after_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "advanced-restart-ledger"
    service = MarketStudyServiceV3(root)
    result: dict[str, Any] = {}
    for sequence in range(1, 15):
        result = service.study(
            _candles(0.0, sequence),
            symbol="AUD/JPY OTC",
            timeframe="M5",
            closed_candle_key=f"advanced-ledger-{sequence}",
            closed_candle_sequence=sequence,
            regime="UPTREND",
            regression=_regression(),
        )

    before_count = result["motif_lattice"]["closed_candle_count"]  # type: ignore[index]
    assert before_count > 8
    assert result["motif_lattice"]["continuous_window"]["history_source"] == (  # type: ignore[index]
        "CURRENT_HISTORY_PLUS_RESTART_SAFE_PAIR_LEDGER"
    )

    restarted = MarketStudyServiceV3(root)
    after = restarted.study(
        _candles(0.0, 15),
        symbol="AUD/JPY OTC",
        timeframe="M5",
        closed_candle_key="advanced-ledger-15",
        closed_candle_sequence=15,
        regime="UPTREND",
        regression=_regression(),
    )
    assert after["motif_lattice"]["closed_candle_count"] > before_count  # type: ignore[index]
    assert after["motif_lattice"]["symbol"] == "AUD/JPY OTC"  # type: ignore[index]
    assert after["motif_lattice"]["timeframe"] == "M5"  # type: ignore[index]
    assert after["motif_lattice"]["continuous_window"]["history_source"] == (  # type: ignore[index]
        "CURRENT_HISTORY_PLUS_RESTART_SAFE_PAIR_LEDGER"
    )

    other_pair = restarted.study(
        _candles(0.0, 15),
        symbol="GBP/JPY OTC",
        timeframe="M5",
        closed_candle_key="other-pair-close-15",
        closed_candle_sequence=15,
        regime="UPTREND",
        regression=_regression(),
    )
    assert other_pair["motif_lattice"]["closed_candle_count"] == 8  # type: ignore[index]
    assert other_pair["motif_lattice"]["history_id"] != after["motif_lattice"]["history_id"]  # type: ignore[index]


def test_continuous_research_publishes_shadow_counts_drift_and_claim_coverage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "continuous-research-contracts"
    service = MarketStudyServiceV3(root)
    result = service.study(
        _many_candles(40),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        closed_candle_key="research-close-39",
        closed_candle_sequence=39,
        regime="TRANSITION",
        regression=_regression(),
    )

    ontology = result["adaptive_feature_ontology"]
    assert ontology["status"] == "SHADOW_EVIDENCE_ACCUMULATING"  # type: ignore[index]
    assert ontology["public_features"] == []  # type: ignore[index]
    assert ontology["promoted_feature_count"] == 0  # type: ignore[index]
    assert ontology["shadow_features_excluded"] is True  # type: ignore[index]
    assert ontology["shadow_audit"] == {  # type: ignore[index]
        "shadow_feature_count": 2,
        "evaluated_shadow_feature_count": 2,
        "evidence_closed_candle_count": 40,
        "definitions_published": False,
        "promotion_requires_real_holdout_gate": True,
    }
    assert "definition" not in _nested_keys(ontology)
    assert "derivation" not in _nested_keys(ontology)

    drift = result["concept_drift"]
    regime = result["regime_partition"]
    assert drift["status"] in {"WARMING", "STABLE", "DRIFT_DETECTED"}  # type: ignore[index]
    assert drift["window_policy"] == {  # type: ignore[index]
        "adaptive": False,
        "window_size": 24,
        "fixed_sequence_horizon": False,
        "configuration_persisted_per_pair": True,
        "retained_history_replay_idempotent": True,
    }
    assert drift["private_identity_audit"]["raw_candle_identities_published"] is False  # type: ignore[index]
    assert regime["status"] == "ACTIVE"  # type: ignore[index]
    assert regime["current_partition"]["regime_partition_id"] == (  # type: ignore[index]
        drift["current_regime_partition_id"]  # type: ignore[index]
    )
    assert drift["execution_authority"] is False  # type: ignore[index]
    assert regime["predicts_direction"] is False  # type: ignore[index]

    cross_pair = result["cross_pair_association"]
    assert cross_pair["status"] == "INSUFFICIENT_SYNCHRONIZED_PAIR"  # type: ignore[index]
    assert cross_pair["edges"] == []  # type: ignore[index]
    assert cross_pair["contract"]["fabricates_missing_pair_evidence"] is False  # type: ignore[index]

    proofs = result["claim_proofs"]
    coverage = {row["claim_key"]: row for row in proofs["coverage"]}  # type: ignore[index]
    assert proofs["status"] == "PARTIAL"  # type: ignore[index]
    assert {
        "motif_lattice",
        "survival_network",
        "path_reconstruction",
        "adaptive_feature_ontology",
        "concept_drift",
        "regime_partition",
        "cross_pair_association",
        "regression",
        "candle_intelligence",
        "behavior",
        "pair_dna",
        "object_relationship_graph",
        "historical_similarity",
        "outcome_maturation",
        "directional_read",
    } <= set(coverage)
    assert coverage["cross_pair_association"]["status"] == (
        "NOT_PUBLISHED_INSUFFICIENT_SYNCHRONIZED_EVIDENCE"
    )
    assert all(
        coverage[name]["status"] == "COVERED"
        for name in (
            "motif_lattice",
            "survival_network",
            "path_reconstruction",
            "concept_drift",
            "regime_partition",
        )
    )
    assert proofs["certificate_count"] == 13  # type: ignore[index]
    assert all(
        certificate["execution_authority"] is False
        and certificate["causal"] is False
        for certificate in proofs["certificates"]  # type: ignore[index]
    )
    assert result["motif_lattice"]["claim_proof_id"] == coverage[  # type: ignore[index]
        "motif_lattice"
    ]["certificate_id"]
    assert result["motif_lattice"]["claim_bound_study_hash"] == coverage[  # type: ignore[index]
        "motif_lattice"
    ]["published_study_hash"]
    for claim_key, proof_row in coverage.items():
        if proof_row["status"] != "COVERED" or claim_key not in result:
            continue
        published = cast(Mapping[str, Any], result[claim_key])
        assert canonical_public_study_hash_v3(published) == proof_row[
            "published_study_hash"
        ]

    restarted = MarketStudyServiceV3(root).study(
        _many_candles(40),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        closed_candle_key="research-close-39",
        closed_candle_sequence=39,
        regime="TRANSITION",
        regression=_regression(),
    )
    assert restarted["concept_drift"]["partitions"] == drift["partitions"]  # type: ignore[index]
    assert restarted["adaptive_feature_ontology"] == ontology
    persisted_before = service.pair_dna.get_concept_drift_state(
        "CAD/JPY OTC",
        "M5",
    )
    assert persisted_before["status"] == "READY"
    assert persisted_before["detector_state"]["configuration"][  # type: ignore[index]
        "window_size"
    ] == 24

    appended = MarketStudyServiceV3(root).study(
        _many_candles(41),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        closed_candle_key="research-close-40",
        closed_candle_sequence=40,
        regime="TRANSITION",
        regression=_regression(),
    )
    assert [
        row["regime_partition_id"]
        for row in appended["concept_drift"]["partitions"]  # type: ignore[index]
    ] == [row["regime_partition_id"] for row in drift["partitions"]]  # type: ignore[index]
    persisted_after = service.pair_dna.get_concept_drift_state(
        "CAD/JPY OTC",
        "M5",
    )
    assert persisted_after["detector_state"]["last_order_index"] > (  # type: ignore[index]
        persisted_before["detector_state"]["last_order_index"]  # type: ignore[index]
    )


def test_cross_pair_association_requires_real_exact_peer_and_survives_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cross-pair-restart"
    first = MarketStudyServiceV3(root).study(
        _many_candles(40),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        closed_candle_key="cadjpy-cross-close",
        closed_candle_sequence=39,
        regime="UPTREND",
        regression=_regression(),
    )
    assert first["cross_pair_association"]["status"] == (  # type: ignore[index]
        "INSUFFICIENT_SYNCHRONIZED_PAIR"
    )
    assert first["cross_pair_association"]["published_edge_count"] == 0  # type: ignore[index]

    second = MarketStudyServiceV3(root).study(
        _many_candles(40),
        symbol="GBP/JPY OTC",
        timeframe="M5",
        closed_candle_key="gbpjpy-cross-close",
        closed_candle_sequence=39,
        regime="UPTREND",
        regression=_regression(),
    )
    association = second["cross_pair_association"]
    assert association["compatible_pair_count"] == 1  # type: ignore[index]
    assert association["tested_pair_count"] == 1  # type: ignore[index]
    assert association["status"] in {  # type: ignore[index]
        "SUPPORTED",
        "NO_SIGNIFICANT_ASSOCIATION",
    }
    assert association["contract"]["fabricates_missing_pair_evidence"] is False  # type: ignore[index]
    assert all(edge["causal"] is False for edge in association["edges"])  # type: ignore[index]
    cross_coverage = next(
        row
        for row in second["claim_proofs"]["coverage"]  # type: ignore[index]
        if row["claim_key"] == "cross_pair_association"
    )
    assert cross_coverage["status"] == "COVERED"
    assert cross_coverage["certificate_id"]

    third = MarketStudyServiceV3(root).study(
        _many_candles(40),
        symbol="CHF/JPY OTC",
        timeframe="M5",
        closed_candle_key="chfjpy-cross-close",
        closed_candle_sequence=39,
        regime="UPTREND",
        regression=_regression(),
    )
    multi_peer = third["cross_pair_association"]
    assert multi_peer["tested_pair_count"] == 2  # type: ignore[index]
    assert multi_peer["all_tested_peer_evidence_bound"] is True  # type: ignore[index]
    assert len(multi_peer["proof_evidence_digests"]) == 2  # type: ignore[index]
    assert multi_peer["proof_evidence_digests"] == sorted(  # type: ignore[index]
        multi_peer["proof_evidence_digests"]  # type: ignore[index]
    )
    assert (root / "cross_pair_coordinator_v3.json").stat().st_size < 128 * 1024


def test_cross_pair_association_rejects_merely_similar_unsynchronized_pairs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cross-pair-unsynchronized"
    service = MarketStudyServiceV3(root)
    service.study(
        _many_candles(40),
        symbol="AUD/JPY OTC",
        timeframe="M5",
        closed_candle_key="audjpy-synchronized-anchor",
        closed_candle_sequence=39,
        regime="UPTREND",
        regression=_regression(),
    )
    shifted = _many_candles(40)
    for row in shifted:
        row["timestamp"] = int(cast(Any, row["timestamp"])) + 60
    result = service.study(
        shifted,
        symbol="NZD/JPY OTC",
        timeframe="M5",
        closed_candle_key="nzdjpy-unsynchronized-close",
        closed_candle_sequence=39,
        regime="UPTREND",
        regression=_regression(),
    )

    association = result["cross_pair_association"]
    assert association["status"] == "INSUFFICIENT_SYNCHRONIZED_PAIR"  # type: ignore[index]
    assert association["compatible_pair_count"] == 0  # type: ignore[index]
    assert association["tested_pair_count"] == 0  # type: ignore[index]
    assert association["edges"] == []  # type: ignore[index]


def test_continuous_path_is_clamped_and_advanced_failures_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = analyze_candle_sequence_v3(
        _many_candles(300),
        regime="TRANSITION",
        require_closed=True,
        max_candles=512,
    )
    behavior = measure_market_behavior_v3(
        candles,
        timeframe_seconds=300,
        max_candles=512,
        inner_window=16,
    )
    def _fixed_path_anchor(
        _behavior: Mapping[str, Any],
        _count: int,
    ) -> tuple[int, str]:
        return 0, "TEST_RETAINED_HISTORY_START"

    monkeypatch.setattr(
        "phoenixguard.study.market_study_service_v3._path_anchor",
        _fixed_path_anchor,
    )
    studied = _continuous_advanced_studies(
        candles,
        behavior,
        symbol="CAD/JPY OTC",
        timeframe="M5",
        history_source="TEST_CLOSED_HISTORY",
    )
    path = studied["path_reconstruction"]
    assert path["status"] == "RECONSTRUCTED"
    assert path["point_count"] == MAX_PATH_CANDLES
    assert path["anchor_index"] == 300 - MAX_PATH_CANDLES
    assert path["anchor_selection"]["method"].endswith("BOUNDED_TAIL_CLAMP")
    library = path["trajectory_library"]
    assert library["entry_count"] <= library["max_entries"] == 16
    assert all(
        len(entry["points"]) <= library["max_follow_through_candles"]
        for entry in library["entries"]
    )

    def _motif_failure(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise MotifLatticeValidationError("isolated motif failure")

    monkeypatch.setattr(
        "phoenixguard.study.market_study_service_v3.build_hierarchical_motif_lattice_v3",
        _motif_failure,
    )
    isolated = _continuous_advanced_studies(
        candles,
        behavior,
        symbol="CAD/JPY OTC",
        timeframe="M5",
        history_source="TEST_CLOSED_HISTORY",
    )
    assert isolated["motif_lattice"]["status"] == "INSUFFICIENT_PROVEN_HISTORY"
    assert isolated["survival_network"]["status"] == "STUDIED"
    assert isolated["path_reconstruction"]["status"] == "RECONSTRUCTED"


def test_object_survival_uses_only_matured_pair_dna_history() -> None:
    states = [
        "REST",
        "REST",
        "UP_SWING",
        "UP_SWING",
        "DOWN_SWING",
        "REST",
        "REST",
        "UP_SWING",
        "DOWN_SWING",
        "REST",
        "UP_SWING",
        "DOWN_SWING",
    ]
    pair_profile = {
        "recent_sequences": [
            {
                "sequence_id": f"S-{index}",
                "current_state": state,
                "object_types": ["ORDER_BLOCK", "PRICE_IMBALANCE"],
            }
            for index, state in enumerate(states)
        ]
    }
    study = _object_conditioned_time_to_event(pair_profile)

    assert study["status"] == "STUDIED"
    assert study["curve_count"] > 0
    assert any(curve["status"] == "SUPPORTED" for curve in study["curves"])
    assert {
        node["node_type"] for node in study["network"]["nodes"]
    } >= {
        "MARKET_OBJECT_TYPE",
        "OBJECT_CANDLE_STATE_CONFLUENCE",
        "OBJECT_CONDITIONED_TIME_TO_EVENT",
    }
    assert all(edge["causal"] is False for edge in study["network"]["edges"])
    assert study["history_contract"] == {
        "source": "PAIR_DNA_MATURED_COMPLETED_STUDIES",
        "closed_history_only": True,
        "current_frame_objects_are_not_historical_support": True,
    }
    pending = _object_conditioned_time_to_event(
        {"recent_sequences": [{"current_state": "REST"}]}
    )
    assert pending["status"] == "INSUFFICIENT_MATURED_OBJECT_HISTORY"
    assert pending["curves"] == []


def test_market_study_tracks_admitted_jpclf_clock_through_final_interval(
    tmp_path: Path,
) -> None:
    root = tmp_path / "jpclf-market-study"
    first = MarketStudyServiceV3(root).study(
        _resolver_bound_jpclf_candles(0),
        symbol="USD/CAD OTC",
        timeframe="M5",
        closed_candle_key="jpclf-close-7",
        closed_candle_sequence=7,
        regime="UPTREND",
        regression=_regression(),
        contract_duration_seconds=900,
        closed_candle_time_proof=_jpclf_time_proof(0),
    )
    timing = first["path_clock_liquidity"]  # type: ignore[index]
    assert first["path_clock_liquidity_v3"] == timing
    assert timing["status"] == "BUILDING_HISTORY"
    assert timing["new_entry_eligible"] is True
    assert timing["active_anchor_count"] == 1

    second = MarketStudyServiceV3(root).study(
        _resolver_bound_jpclf_candles(1),
        symbol="USD/CAD OTC",
        timeframe="M5",
        closed_candle_key="jpclf-close-8",
        closed_candle_sequence=8,
        regime="UPTREND",
        regression=_regression(),
        contract_duration_seconds=60,
        closed_candle_time_proof=_jpclf_time_proof(1),
    )
    late = second["path_clock_liquidity"]  # type: ignore[index]
    assert late["new_entry_eligible"] is False
    assert late["active_tracking_continues_below_floor"] is True
    assert late["timing_read"]["remaining_seconds"] == 600
    assert late["timing_read"]["elapsed_seconds"] == 300
    assert late["timing_read"]["timing_veto"] is True

    MarketStudyServiceV3(root).study(
        _resolver_bound_jpclf_candles(2),
        symbol="USD/CAD OTC",
        timeframe="M5",
        closed_candle_key="jpclf-close-9",
        closed_candle_sequence=9,
        regime="UPTREND",
        regression=_regression(),
        contract_duration_seconds=None,
        closed_candle_time_proof=_jpclf_time_proof(2),
    )
    matured = MarketStudyServiceV3(root).study(
        _resolver_bound_jpclf_candles(3),
        symbol="USD/CAD OTC",
        timeframe="M5",
        closed_candle_key="jpclf-close-10",
        closed_candle_sequence=10,
        regime="UPTREND",
        regression=_regression(),
        contract_duration_seconds=899,
        closed_candle_time_proof=_jpclf_time_proof(3),
    )["path_clock_liquidity"]
    assert matured["trajectory_count"] == 1  # type: ignore[index]
    assert matured["pair_dna_partition"]["contains_trajectory_points"] is False  # type: ignore[index]
    assert matured["promotion_gate"]["passed"] is False  # type: ignore[index]
    assert matured["timing_read"]["timing_supports_entry"] is False  # type: ignore[index]
    assert "path_mru" not in _nested_keys(matured)
    assert "PATH_CLOCK" not in (root / "pair_dna_v3.json").read_text(
        encoding="utf-8"
    )
    side_files = list((root / "path_clock_liquidity_v3").glob("*.json"))
    assert len(side_files) == 1
    assert '"points"' in side_files[0].read_text(encoding="utf-8")


def test_same_closed_key_can_upgrade_from_missing_to_valid_time_proof(
    tmp_path: Path,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "jpclf-proof-upgrade")
    candles = _resolver_bound_jpclf_candles(0)
    common: dict[str, Any] = {
        "symbol": "USD/CAD OTC",
        "timeframe": "M5",
        "closed_candle_key": "jpclf-close-7",
        "closed_candle_sequence": 7,
        "regime": "UPTREND",
        "regression": _regression(),
        "contract_duration_seconds": 900,
    }

    censored = service.study(candles, **common)
    assert censored["path_clock_liquidity"]["status"] == (  # type: ignore[index]
        "CENSORED_INVALID_TIMING_EVIDENCE"
    )

    proof = _jpclf_time_proof(0)
    upgraded = service.study(
        candles,
        **common,
        closed_candle_time_proof=proof,
    )
    timing = upgraded["path_clock_liquidity"]  # type: ignore[index]
    assert timing["status"] == "BUILDING_HISTORY"
    assert timing["time_proof_audit"]["schema_version"] == (  # type: ignore[index]
        "PG_PROVEN_CLOSED_CANDLE_TIME_V3"
    )
    assert "closed_candle_time_proof" not in upgraded

    repeated = service.study(
        candles,
        **common,
        closed_candle_time_proof=proof,
    )
    assert repeated == upgraded


def test_conflicting_time_proof_for_same_closed_key_is_not_hidden_by_cache(
    tmp_path: Path,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "jpclf-proof-conflict")
    candles = _resolver_bound_jpclf_candles(0)
    common: dict[str, Any] = {
        "symbol": "USD/CAD OTC",
        "timeframe": "M5",
        "closed_candle_key": "jpclf-close-7",
        "closed_candle_sequence": 7,
        "regime": "UPTREND",
        "regression": _regression(),
        "contract_duration_seconds": 900,
    }
    accepted = service.study(
        candles,
        **common,
        closed_candle_time_proof=_jpclf_time_proof(0),
    )
    assert accepted["path_clock_liquidity"]["status"] == "BUILDING_HISTORY"  # type: ignore[index]

    conflicting = _jpclf_time_proof(
        0,
        timestamp_source="RESOLVER_BOUND_BOUNDARY_GRID",
    )
    rejected = service.study(
        candles,
        **common,
        closed_candle_time_proof=conflicting,
    )
    timing = rejected["path_clock_liquidity"]  # type: ignore[index]
    assert timing["status"] == "CENSORED_INVALID_TIMING_EVIDENCE"
    assert "conflicts with different JPCLF evidence" in timing["reason"]


def test_service_filters_unbound_rows_and_forwards_valid_time_proof_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MarketStudyServiceV3(tmp_path / "jpclf-proof-forwarding")
    bound = _resolver_bound_jpclf_candles(0)
    leading_unbound = dict(bound[0])
    trailing_unbound = dict(bound[-1])
    for row, candle_id, timestamp in (
        (leading_unbound, "unbound-before", -300),
        (trailing_unbound, "unbound-after", 2_400),
    ):
        row.update({"candle_id": candle_id, "timestamp": timestamp})
        row.pop("identity_stable", None)
        row.pop("stable_candle_identity", None)
        row.pop("identity_proof_source", None)
        row.pop("closed_candle_sequence", None)
        row.pop("resolver_bound_row_index", None)
    candles = [leading_unbound, *bound, trailing_unbound]
    proof = _jpclf_time_proof(0)
    observed_calls: list[dict[str, Any]] = []
    original_observe = service.path_clock_liquidity.observe_closed_candle

    def capture_observe(**kwargs: Any) -> dict[str, Any]:
        observed_calls.append(kwargs)
        return original_observe(**kwargs)

    monkeypatch.setattr(
        service.path_clock_liquidity,
        "observe_closed_candle",
        capture_observe,
    )
    result = service.study(
        candles,
        symbol="USD/CAD OTC",
        timeframe="M5",
        closed_candle_key="jpclf-close-7",
        closed_candle_sequence=7,
        regime="UPTREND",
        regression=_regression(),
        contract_duration_seconds=900,
        closed_candle_time_proof=proof,
    )

    assert result["path_clock_liquidity"]["status"] == "BUILDING_HISTORY"  # type: ignore[index]
    assert len(observed_calls) == 1
    forwarded = observed_calls[0]
    assert forwarded["closed_candle_time_proof"] is proof
    assert len(forwarded["candles"]) == len(bound)
    assert forwarded["candles"][7]["stable_candle_identity"] == (
        "EXPLICIT:jpclf-close-7"
    )
    assert forwarded["candles"][7]["timestamp"] == 2_100
