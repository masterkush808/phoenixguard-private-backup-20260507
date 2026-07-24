from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from phoenixguard.study._persistence_v3 import StudyPersistenceError
from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.historical_similarity_v3 import (
    FINGERPRINT_VECTOR_SIZE,
    HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
    HistoricalSequenceStoreV3,
    HistoricalSimilarityValidationError,
    build_sequence_fingerprint_v3,
    build_similarity_graph_v3,
    sequence_similarity_v3,
    summarize_outcome_correlations_v3,
    validate_sequence_fingerprint_v3,
)


def _studies(
    closes: list[float],
    *,
    identity_prefix: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    previous = closes[0] - 0.5
    for index, close in enumerate(closes):
        candles.append(
            {
                "candle_id": f"{identity_prefix}-{index}",
                "timestamp": 1_700_000_000 + index * 300,
                "open": previous,
                "high": max(previous, close) + 0.3,
                "low": min(previous, close) - 0.3,
                "close": close,
                "is_closed": True,
            }
        )
        previous = close
    candle_study = analyze_candle_sequence_v3(candles, regime="TRENDING_UP")
    return candle_study, measure_market_behavior_v3(candle_study, inner_window=3)


def _fingerprint(
    sequence_id: str,
    *,
    closes: list[float] | None = None,
    symbol: str = "CAD/JPY OTC",
    outcome: dict[str, Any] | None = None,
    objects: tuple[str, ...] = ("PRICE_IMBALANCE", "REACTION_ZONE"),
) -> dict[str, Any]:
    candle_study, behavior_study = _studies(
        closes or [100.0, 101.0, 102.0, 102.1, 103.0, 104.0],
        identity_prefix=sequence_id,
    )
    return build_sequence_fingerprint_v3(
        candle_study,
        behavior_study,
        symbol=symbol,
        timeframe="M5",
        sequence_id=sequence_id,
        objects=[{"object_type": value} for value in objects],
        outcome=outcome,
    )


def test_historical_search_ranks_similar_pair_sequences_and_selects_one_tendency(tmp_path: Path) -> None:
    store = HistoricalSequenceStoreV3(
        tmp_path / "history.json",
        max_entries=16,
        max_entries_per_pair=12,
    )
    for index in range(4):
        shifted = [value + index * 0.01 for value in [100.0, 101.0, 102.0, 102.1, 103.0, 104.0]]
        store.add(
            _fingerprint(
                f"similar-{index}",
                closes=shifted,
                outcome={"direction": "UP", "realized_return": 1.0 + index, "success": True},
            )
        )
    store.add(
        _fingerprint(
            "other-pair",
            symbol="GBP/USD OTC",
            outcome={"direction": "DOWN", "realized_return": -1.0},
        )
    )
    query = _fingerprint("live-query")

    result = store.search(query, minimum_similarity=0.40, min_outcome_support=3)

    assert result["status"] == "READY"
    assert result["match_count"] == 4
    assert all(row["symbol"] == "CAD/JPY OTC" for row in result["matches"])
    assert result["matches"][0]["similarity"] == 1.0
    continuation = result["historical_continuation"]
    assert continuation["status"] == "SUPPORTED"
    assert continuation["direction"] == "UP"
    assert continuation["probabilities"]["UP"] > continuation["probabilities"]["DOWN"]
    assert continuation["execution_authority"] is False
    assert "alternate_route" not in result


def test_fingerprint_similarity_is_explainable_and_distinguishes_opposite_shape() -> None:
    query = _fingerprint("query")
    similar = _fingerprint("similar", closes=[200.0, 201.0, 202.0, 202.1, 203.0, 204.0])
    opposite = _fingerprint("opposite", closes=[104.0, 103.0, 102.0, 101.9, 101.0, 100.0])

    similar_score = sequence_similarity_v3(query, similar)
    opposite_score = sequence_similarity_v3(query, opposite)

    assert len(query["feature_vector"]) == FINGERPRINT_VECTOR_SIZE
    assert similar_score["similarity"] > opposite_score["similarity"]
    assert similar_score["components"]["body_wick_geometry"] >= 0.9
    assert "normalized price path is closely aligned" in similar_score["explanations"]
    assert similar_score["execution_authority"] is False


def test_object_candle_associations_are_pairwise_bounded_and_non_causal() -> None:
    fingerprints = [
        _fingerprint(
            f"labeled-{index}",
            outcome={"direction": "UP", "realized_return": float(index + 1)},
        )
        for index in range(4)
    ]

    result = summarize_outcome_correlations_v3(fingerprints, min_support=3)

    features = {row["feature"] for row in result["correlations"]}
    assert "OBJECT:PRICE_IMBALANCE" in features
    assert any(feature.startswith("PAIR:CANDLE_TYPE=") for feature in features)
    assert "PAIR:OBJECT=PRICE_IMBALANCE&OBJECT=REACTION_ZONE" in features
    assert result["association_contract"] == {
        "analysis_kind": "MARGINAL_AND_PAIRWISE_FEATURE_ASSOCIATION",
        "causal": False,
    }
    object_row = next(row for row in result["correlations"] if row["feature"] == "OBJECT:PRICE_IMBALANCE")
    assert object_row["support"] == 4
    assert len(object_row["dominant_probability_interval_95"]) == 2


def test_similarity_graph_connects_bounded_sequence_neighbors() -> None:
    fingerprints = [
        _fingerprint(f"node-{index}", closes=[100.0 + index, 101.0 + index, 102.0 + index, 103.0 + index])
        for index in range(4)
    ]

    graph = build_similarity_graph_v3(
        fingerprints,
        minimum_similarity=0.80,
        max_edges_per_node=2,
    )

    assert len(graph["nodes"]) == 4
    assert graph["edges"]
    degrees: dict[str, int] = {}
    for edge in graph["edges"]:
        degrees[edge["source"]] = degrees.get(edge["source"], 0) + 1
        degrees[edge["target"]] = degrees.get(edge["target"], 0) + 1
    assert max(degrees.values()) <= 2
    assert graph["execution_authority"] is False


def test_historical_store_is_bounded_and_updates_same_fingerprint_outcome(tmp_path: Path) -> None:
    store = HistoricalSequenceStoreV3(
        tmp_path / "history.json",
        max_entries=3,
        max_entries_per_pair=2,
    )
    first = _fingerprint("first")
    assert store.add(first)["status"] == "RECORDED"
    enriched = deepcopy(first)
    enriched["outcome"] = {
        "direction": "DOWN",
        "realized_return": 2.0,
        "success": True,
        "horizon_candles": 3,
    }
    assert store.add(enriched)["status"] == "UPDATED"
    store.add(_fingerprint("second"))
    store.add(_fingerprint("third"))

    entries = store.entries()
    assert len(entries) == 2
    assert {row["sequence_id"] for row in entries} == {"SECOND", "THIRD"}
    assert not list(tmp_path.glob("*.tmp"))


def test_no_direction_is_inferred_from_pnl_and_low_support_stays_unknown(tmp_path: Path) -> None:
    fingerprint = _fingerprint(
        "pnl-only",
        outcome={"realized_return": 5.0, "success": True},
    )
    assert fingerprint["outcome"]["direction"] == "UNKNOWN"
    store = HistoricalSequenceStoreV3(tmp_path / "history.json")
    store.add(fingerprint)

    result = store.search(_fingerprint("query"), minimum_similarity=0.0)

    assert result["historical_continuation"]["status"] == "INSUFFICIENT_OUTCOME_SUPPORT"
    assert result["historical_continuation"]["direction"] == "UNKNOWN"


def test_fingerprint_rejects_tampering_and_mixed_coordinate_study() -> None:
    fingerprint = _fingerprint("untampered")
    tampered = deepcopy(fingerprint)
    tampered["feature_vector"][0] = 999.0
    with pytest.raises(HistoricalSimilarityValidationError, match="digest mismatch"):
        validate_sequence_fingerprint_v3(tampered)

    candle_study, behavior_study = _studies([100.0, 101.0, 102.0], identity_prefix="mixed")
    candle_study["candles"][1]["coordinate_space"] = "PIXEL_PRICE_PROXY"
    with pytest.raises(HistoricalSimilarityValidationError, match="mix candle coordinate"):
        build_sequence_fingerprint_v3(
            candle_study,
            behavior_study,
            symbol="CAD/JPY OTC",
            timeframe="M5",
        )


def test_historical_store_fails_closed_on_corrupt_or_malformed_documents(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{bad-json", encoding="utf-8")
    with pytest.raises(StudyPersistenceError, match="valid UTF-8 JSON"):
        HistoricalSequenceStoreV3(path).entries()

    path.write_text(
        json.dumps(
            {
                "schema_version": HISTORICAL_SEQUENCE_STORE_SCHEMA_VERSION,
                "study_only": True,
                "execution_authority": False,
                "next_ordinal": 1,
                "entries": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(HistoricalSimilarityValidationError, match="entries must be a list"):
        HistoricalSequenceStoreV3(path).entries()
