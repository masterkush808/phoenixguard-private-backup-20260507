from __future__ import annotations

import multiprocessing
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from phoenixguard.study.candle_intelligence_v3 import (
    adapt_tracker_candle_v3,
    analyze_candle_sequence_v3,
    analyze_candle_v3,
)
from phoenixguard.study.candle_ledger_v3 import (
    CANDLE_LEDGER_SCHEMA_VERSION,
    CandleLedgerCapacityError,
    CandleLedgerStoreV3,
)


def _studied_candles(
    identities: list[str],
    *,
    base: float = 100.0,
    timestamp_start: int = 1_700_000_000,
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for index, identity in enumerate(identities):
        open_value = base + index * 0.25
        close_value = open_value + (0.16 if index % 2 == 0 else -0.08)
        raw.append(
            {
                "candle_id": identity,
                "timestamp": timestamp_start + index * 300,
                "open": open_value,
                "high": max(open_value, close_value) + 0.07,
                "low": min(open_value, close_value) - 0.05,
                "close": close_value,
                "closed": True,
            }
        )
    study = analyze_candle_sequence_v3(raw, regime="UPTREND")
    candles = list(study["candles"])
    for identity, candle in zip(identities, candles, strict=True):
        candle["identity_stable"] = True
        candle["stable_candle_identity"] = identity
    return candles


def _record_in_separate_process(
    database_path: str,
    candle: dict[str, Any],
    observed_at: str,
) -> None:
    ledger = CandleLedgerStoreV3(database_path, busy_timeout_ms=10_000)
    ledger.record_candles(
        [candle],
        symbol="EUR/USD OTC",
        timeframe="M5",
        observed_at=observed_at,
    )


def test_rolling_windows_upsert_without_duplicate_unique_candles(
    tmp_path: Path,
) -> None:
    ledger = CandleLedgerStoreV3(tmp_path / "candles.sqlite3")
    first = ledger.record_candles(
        _studied_candles(["t-100", "t-200", "t-300"]),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        observed_at="2026-07-24T01:00:00Z",
    )
    second = ledger.record_candles(
        _studied_candles(
            ["t-200", "t-300", "t-400"],
            base=100.25,
            timestamp_start=1_700_000_300,
        ),
        symbol="CAD/JPY OTC",
        timeframe="M5",
        observed_at="2026-07-24T01:05:00Z",
    )

    assert first["schema_version"] == CANDLE_LEDGER_SCHEMA_VERSION
    assert first["inserted_count"] == 3
    assert second["status"] == "RECORDED_AND_UPDATED"
    assert second["inserted_count"] == 1
    assert second["updated_count"] == 2
    assert second["unique_candle_count"] == 4
    assert second["total_observation_count"] == 6
    summary = ledger.pair_summary("CAD/JPY OTC", "M5")
    assert summary["unique_candle_count"] == 4
    assert summary["total_observation_count"] == 6
    assert summary["study_only"] is True
    assert summary["execution_authority"] is False
    recent = ledger.recent_candles("CAD/JPY OTC", "M5", limit=4)
    assert [row["candle_identity"] for row in recent["records"]] == [
        "t-400",
        "t-300",
        "t-200",
        "t-100",
    ]
    assert recent["records"][1]["observation_count"] == 2
    assert recent["records"][0]["exact_geometry"]["range_size"] > 0.0
    assert "upper_wick_to_range" in recent["records"][0]["ratios"]


def test_ledger_reopens_after_restart_and_remains_wal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.sqlite3"
    first_process = CandleLedgerStoreV3(path)
    first_process.record_candles(
        _studied_candles(["stable-close-1", "stable-close-2"]),
        symbol="GBP/USD OTC",
        timeframe="M5",
        observed_at="2026-07-24T02:00:00Z",
    )
    del first_process

    restarted = CandleLedgerStoreV3(path)
    summary = restarted.pair_summary("GBP/USD OTC", "M5")
    assert summary["unique_candle_count"] == 2
    replay = restarted.record_candles(
        _studied_candles(["stable-close-1", "stable-close-2"]),
        symbol="GBP/USD OTC",
        timeframe="M5",
        observed_at="2026-07-24T02:05:00Z",
    )
    assert replay["inserted_count"] == 0
    assert replay["updated_count"] == 2
    assert replay["unique_candle_count"] == 2
    with sqlite3.connect(path) as connection:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
    assert journal_mode.lower() == "wal"


def test_two_processes_upsert_the_same_stable_candle_atomically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "processes.sqlite3"
    candle = _studied_candles(["cross-process-close"])[0]
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_record_in_separate_process,
            args=(str(path), candle, f"2026-07-24T03:0{index}:00Z"),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    ledger = CandleLedgerStoreV3(path)
    summary = ledger.pair_summary("EUR/USD OTC", "M5")
    assert summary["unique_candle_count"] == 1
    assert summary["total_observation_count"] == 2


def test_unstable_or_synthetic_identity_is_skipped_without_mutation(
    tmp_path: Path,
) -> None:
    ledger = CandleLedgerStoreV3(tmp_path / "unstable.sqlite3")
    no_marker = _studied_candles(["real-time-1"])[0]
    no_marker.pop("identity_stable")
    no_marker.pop("stable_candle_identity")
    explicit_unstable = _studied_candles(["real-time-2"])[0]
    explicit_unstable["identity_stable"] = False
    synthetic = _studied_candles(["real-time-3"])[0]
    synthetic["stable_candle_identity"] = "candle-000003"

    result = ledger.record_candles(
        [no_marker, explicit_unstable, synthetic],
        symbol="AUD/CAD",
        timeframe="M1",
    )

    assert result["status"] == "SKIPPED_UNSTABLE_IDENTITY"
    assert result["skipped_unstable_count"] == 3
    assert ledger.pair_summary("AUD/CAD", "M1")["unique_candle_count"] == 0
    assert ledger.recent_candles("AUD/CAD", "M1")["records"] == []


def test_same_identity_is_isolated_by_pair_and_timeframe(tmp_path: Path) -> None:
    ledger = CandleLedgerStoreV3(tmp_path / "pairs.sqlite3")
    candle = _studied_candles(["broker-close-100"])
    ledger.record_candles(candle, symbol="CAD/JPY", timeframe="M5")
    ledger.record_candles(candle, symbol="CAD/JPY", timeframe="M1")
    ledger.record_candles(candle, symbol="GBP/USD", timeframe="M5")

    cad_m5 = ledger.pair_summary("CAD/JPY", "M5")
    cad_m1 = ledger.pair_summary("CAD/JPY", "M1")
    gbp_m5 = ledger.pair_summary("GBP/USD", "M5")
    assert cad_m5["unique_candle_count"] == 1
    assert cad_m1["unique_candle_count"] == 1
    assert gbp_m5["unique_candle_count"] == 1
    assert len({cad_m5["pair_id"], cad_m1["pair_id"], gbp_m5["pair_id"]}) == 3
    assert ledger.recent_candles("CAD/JPY", "M5")["records"][0][
        "candle_identity"
    ] == "broker-close-100"


def test_proxy_and_pixel_micro_features_are_exact_and_allowlisted(
    tmp_path: Path,
) -> None:
    normalized = analyze_candle_v3(
        adapt_tracker_candle_v3(
            {
                "track_id": "proxy-1",
                "direction": "BUY",
                "open_proxy": 0.40,
                "high_proxy": 0.62,
                "low_proxy": 0.34,
                "close_proxy": 0.58,
            },
            closure_proof={
                "event_key": "proxy-event-1",
                "candle_id": "proxy-1",
                "proven_closed": True,
            },
        )
    )
    pixel = analyze_candle_v3(
        adapt_tracker_candle_v3(
            {
                "track_id": "pixel-2",
                "direction": "SELL",
                "body_top_px": 40.0,
                "body_bottom_px": 54.0,
                "wick_top_px": 31.0,
                "wick_bottom_px": 72.0,
            },
            closure_proof={
                "event_key": "pixel-event-2",
                "candle_id": "pixel-2",
                "proven_closed": True,
            },
        )
    )
    for identity, row in (("proxy-close", normalized), ("pixel-close", pixel)):
        row["identity_stable"] = True
        row["stable_candle_identity"] = identity
        row["private_image"] = "must-not-be-stored"
    ledger = CandleLedgerStoreV3(tmp_path / "geometry.sqlite3")
    ledger.record_candles(
        [normalized, pixel], symbol="NZD/JPY OTC", timeframe="M5"
    )
    records = ledger.recent_candles("NZD/JPY OTC", "M5")["records"]
    by_identity = {row["candle_identity"]: row for row in records}
    assert by_identity["proxy-close"]["coordinate_space"] == "NORMALIZED_PRICE_PROXY"
    assert by_identity["proxy-close"]["source_values"] == {
        "close_proxy": 0.58,
        "high_proxy": 0.62,
        "low_proxy": 0.34,
        "open_proxy": 0.4,
    }
    assert by_identity["pixel-close"]["coordinate_space"] == "PIXEL_PRICE_PROXY"
    assert by_identity["pixel-close"]["source_values"]["wick_bottom_px"] == 72.0
    assert all("private_image" not in row for row in records)


def test_capacity_failure_rolls_back_entire_batch(tmp_path: Path) -> None:
    ledger = CandleLedgerStoreV3(tmp_path / "capacity.sqlite3", max_records=2)
    ledger.record_candles(
        _studied_candles(["capacity-1", "capacity-2"]),
        symbol="CHF/JPY",
        timeframe="M5",
    )
    with pytest.raises(CandleLedgerCapacityError, match="no rows were changed"):
        ledger.record_candles(
            _studied_candles(["capacity-1", "capacity-3"]),
            symbol="CHF/JPY",
            timeframe="M5",
        )
    summary = ledger.pair_summary("CHF/JPY", "M5")
    assert summary["unique_candle_count"] == 2
    assert summary["total_observation_count"] == 2
