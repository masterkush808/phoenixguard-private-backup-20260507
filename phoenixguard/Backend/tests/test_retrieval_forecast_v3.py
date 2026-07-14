from __future__ import annotations

import json
import math

import numpy as np
import pytest

from phoenixguard.decision.retrieval_forecast_v3 import (
    BANK_SCHEMA_V3,
    build_retrieval_bank_v3,
    retrieve_forecast_v3,
    validate_retrieval_bank_v3,
)


def test_build_normalizes_and_round_trips_as_json() -> None:
    bank = build_retrieval_bank_v3(
        [[3.0, 4.0], [0.0, 2.0]],
        ["chart-a", "chart-b"],
        [["BUY", "SELL"], [0, 1]],
        [[0.3, -0.2], [-0.1, 0.4]],
        split_labels=["train", "TRAIN"],
        entry_ids=["event-a", "event-b"],
    )

    serialized = json.loads(json.dumps(bank))
    validated = validate_retrieval_bank_v3(serialized)

    assert validated["schema"] == BANK_SCHEMA_V3
    assert validated["embedding_dim"] == 2
    assert validated["horizon"] == 2
    assert [entry["split"] for entry in validated["entries"]] == ["train", "train"]
    assert validated["entries"][1]["next_directions"] == ["SELL", "BUY"]
    for entry in validated["entries"]:
        assert math.isclose(
            float(np.linalg.norm(entry["context_embedding"])),
            1.0,
            rel_tol=1e-6,
            abs_tol=1e-12,
        )


def test_build_and_validation_fail_closed_on_non_train_rows() -> None:
    with pytest.raises(ValueError, match="leakage"):
        build_retrieval_bank_v3(
            [[1.0, 0.0], [0.0, 1.0]],
            ["chart-a", "chart-b"],
            [["BUY"], ["SELL"]],
            [[1.0], [-1.0]],
            split_labels=["train", "validation"],
        )

    valid = build_retrieval_bank_v3(
        [[1.0, 0.0]],
        ["chart-a"],
        [["BUY"]],
        [[1.0]],
        split_labels=["train"],
    )
    tampered = json.loads(json.dumps(valid))
    tampered["entries"][0]["split"] = "test"

    with pytest.raises(ValueError, match="leakage"):
        validate_retrieval_bank_v3(tampered)
    with pytest.raises(ValueError, match="leakage"):
        retrieve_forecast_v3(tampered, [[1.0, 0.0]])


def test_retrieval_uses_one_neighbor_per_source_and_similarity_weights() -> None:
    bank = build_retrieval_bank_v3(
        [
            [1.0, 0.0],
            [0.99, 0.10],
            [0.8, 0.6],
            [-1.0, 0.0],
        ],
        ["source-a", "source-a", "source-b", "source-c"],
        [
            ["BUY", "SELL"],
            ["SELL", "BUY"],
            ["SELL", "SELL"],
            ["BUY", "BUY"],
        ],
        [
            [10.0, -2.0],
            [99.0, 99.0],
            [0.0, -4.0],
            [50.0, 50.0],
        ],
        split_labels=["train"] * 4,
        entry_ids=["a-best", "a-shadow", "b-best", "c-opposite"],
    )

    result = retrieve_forecast_v3(
        bank,
        [1.0, 0.0],
        top_k=2,
        minimum_similarity=0.01,
        similarity_power=1.0,
    )[0]

    assert result["status"] == "ok"
    assert result["neighbor_count"] == 2
    assert result["unique_source_count"] == 2
    assert [item["entry_id"] for item in result["neighbors"]] == ["a-best", "b-best"]
    assert len({item["source_id"] for item in result["neighbors"]}) == 2

    first_step = result["horizons"][0]
    second_weight = (0.8 - 0.01) / (1.0 - 0.01)
    weight_sum = 1.0 + second_weight
    assert math.isclose(
        float(first_step["probabilities"]["BUY"]),
        1.0 / weight_sum,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert math.isclose(
        float(first_step["probabilities"]["SELL"]),
        second_weight / weight_sum,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert math.isclose(
        float(first_step["continuous_mean"]),
        10.0 / weight_sum,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert first_step["continuous_uncertainty"] > 0.0

    second_step = result["horizons"][1]
    assert set(second_step["probabilities"]) == {"BUY", "SELL"}
    assert math.isclose(
        float(second_step["probabilities"]["BUY"]),
        0.0,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert math.isclose(
        float(second_step["probabilities"]["SELL"]),
        1.0,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert math.isclose(
        float(second_step["continuous_mean"]),
        (-2.0 - 4.0 * second_weight) / weight_sum,
        rel_tol=1e-6,
        abs_tol=1e-12,
    )
    assert 0.0 < result["effective_confidence"] <= 1.0


def test_batched_numpy_queries_are_deterministic_under_similarity_ties() -> None:
    bank = build_retrieval_bank_v3(
        [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        ["source-b", "source-a", "source-c"],
        [["SELL"], ["BUY"], ["SELL"]],
        [[-1.0], [1.0], [-2.0]],
        split_labels=["train", "train", "train"],
        entry_ids=["tie-b", "tie-a", "vertical"],
    )
    queries = np.asarray([[2.0, 0.0], [0.0, 3.0]], dtype=np.float32)

    first = retrieve_forecast_v3(bank, queries, top_k=1)
    second = retrieve_forecast_v3(bank, queries, top_k=1)

    assert first == second
    assert len(first) == 2
    assert first[0]["neighbors"][0]["entry_id"] == "tie-a"
    assert first[0]["horizons"][0]["predicted_side"] == "BUY"
    assert first[1]["neighbors"][0]["entry_id"] == "vertical"
    assert first[1]["horizons"][0]["predicted_side"] == "SELL"


def test_batched_torch_queries_are_supported_without_faiss() -> None:
    torch = pytest.importorskip("torch")
    bank = build_retrieval_bank_v3(
        [[1.0, 0.0], [0.0, 1.0]],
        ["horizontal", "vertical"],
        [["BUY"], ["SELL"]],
        [[1.0], [-1.0]],
        split_labels=["train", "train"],
    )

    forecasts = retrieve_forecast_v3(
        bank,
        torch.tensor([[4.0, 0.0], [0.0, 5.0]], dtype=torch.float32),
        top_k=1,
    )

    assert [row["horizons"][0]["predicted_side"] for row in forecasts] == [
        "BUY",
        "SELL",
    ]


def test_empty_bank_and_no_neighbor_have_neutral_fallbacks() -> None:
    empty = build_retrieval_bank_v3(
        [],
        [],
        [],
        [],
        split_labels=[],
        embedding_dim=3,
        horizon=2,
    )
    empty_forecasts = retrieve_forecast_v3(empty, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])

    assert [row["status"] for row in empty_forecasts] == ["empty_bank", "empty_bank"]
    for row in empty_forecasts:
        assert row["effective_confidence"] == 0.0
        assert row["neighbors"] == []
        assert len(row["horizons"]) == 2
        assert row["horizons"][0]["probabilities"] == {"BUY": 0.5, "SELL": 0.5}
        assert row["horizons"][0]["continuous_mean"] is None
        assert row["horizons"][0]["continuous_uncertainty"] is None

    nonempty = build_retrieval_bank_v3(
        [[1.0, 0.0, 0.0]],
        ["source-a"],
        [["BUY", "BUY"]],
        [[1.0, 2.0]],
        split_labels=["train"],
    )
    no_neighbor = retrieve_forecast_v3(nonempty, [-1.0, 0.0, 0.0])[0]
    assert no_neighbor["status"] == "no_eligible_neighbors"
    assert no_neighbor["horizons"][1]["predicted_side"] == "TIE"


def test_invalid_query_vectors_fail_before_forecasting() -> None:
    bank = build_retrieval_bank_v3(
        [[1.0, 0.0]],
        ["source-a"],
        [["BUY"]],
        [[1.0]],
        split_labels=["train"],
    )

    with pytest.raises(ValueError, match="zero-length"):
        retrieve_forecast_v3(bank, [[0.0, 0.0]])
    with pytest.raises(ValueError, match="embedding_dim"):
        retrieve_forecast_v3(bank, [[1.0, 0.0, 0.0]])
