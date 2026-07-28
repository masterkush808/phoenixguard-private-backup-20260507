from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenixguard.study.cross_pair_coordinator_v3 import (
    DEFAULT_COORDINATOR_MAX_NULL_SHIFTS,
    CrossPairStudyCoordinatorV3,
)


def _pseudo_random_series(size: int) -> list[float]:
    state = 917_321
    result: list[float] = []
    for _ in range(size):
        state = (1_103_515_245 * state + 12_345) % (2**31)
        result.append((state / (2**31) - 0.5) * 2.0)
    return result


def _coordinator_rows(
    symbol: str,
    timeframe: str,
    values: list[float],
) -> list[dict[str, Any]]:
    pair_id = f"{symbol}|{timeframe}"
    return [
        {
            "pair_id": pair_id,
            "candle_id": f"{pair_id}-{index}",
            "closed_timestamp": 1_700_000_000 + index * 300,
            "is_closed": True,
            "coordinate_space": "NORMALIZED_RETURN",
            "order_domain": "SYNCHRONIZED_CLOSED_TIMESTAMP_V1",
            "value": value,
        }
        for index, value in enumerate(values)
    ]


def test_live_defaults_publish_supported_edge_after_restart(tmp_path: Path) -> None:
    size = 256
    source = _pseudo_random_series(size)
    target = [0.0, 0.0, 0.0]
    for index in range(3, size):
        target.append(1.4 * source[index - 3])

    state_path = tmp_path / "cross_pair_coordinator_v3.json"
    first_process = CrossPairStudyCoordinatorV3(state_path)
    pending = first_process.update_pair(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        timeframe_seconds=300,
        series=_coordinator_rows("CAD/JPY OTC", "M5", source),
    )
    assert pending["status"] == "INSUFFICIENT_SYNCHRONIZED_PAIR"

    restarted_process = CrossPairStudyCoordinatorV3(state_path)
    result = restarted_process.update_pair(
        symbol="GBP/USD OTC",
        timeframe="M5",
        timeframe_seconds=300,
        series=_coordinator_rows("GBP/USD OTC", "M5", target),
    )

    assert DEFAULT_COORDINATOR_MAX_NULL_SHIFTS == 63
    assert restarted_process.max_null_shifts == 63
    assert result["status"] == "SUPPORTED"
    assert result["stored_pair_count"] == 2
    assert result["tested_pair_count"] == 1
    assert result["published_edge_count"] >= 1
    supported = next(
        edge
        for edge in result["edges"]
        if edge["source_pair_id"] == "CAD/JPY OTC|M5"
        and edge["target_pair_id"] == "GBP/USD OTC|M5"
    )
    assert supported["lag_completed_candles"] == 3
    assert supported["null_shift_count"] == 63
    assert supported["empirical_max_lag_p_value"] == round(1 / 64, 10)
    assert supported["bonferroni_adjusted_p_value"] == round(1 / 32, 10)
    assert supported["causal"] is False
    assert supported["execution_authority"] is False
    assert result["contract"]["publishes_only_significant_associations"] is True
