from __future__ import annotations

from pathlib import Path

import pytest

from phoenixguard.study.market_study_service_v3 import MarketStudyServiceV3


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
    assert profile["status"] == "NOT_FOUND"


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
    assert profile["status"] == "NOT_FOUND"
    assert profile["profile"] is None


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
