from __future__ import annotations

import json
from typing import Any

import pytest

from phoenixguard.study.object_relationship_graph_v3 import (
    OBJECT_RELATIONSHIP_GRAPH_SCHEMA_VERSION,
    ObjectRelationshipGraphValidationError,
    build_object_relationship_graph_v3,
)


def _candle(index: int, *, latest: bool = False) -> dict[str, Any]:
    return {
        "candle_id": f"bar-{index}",
        "timestamp": 1_700_000_000 + index * 300,
        "coordinate_space": "PRICE",
        "direction": "BULLISH" if index % 2 else "BEARISH",
        "type": "BULLISH_BALANCED" if index % 2 else "BEARISH_BALANCED",
        "personality": "CONTROLLED_BUYING" if index % 2 else "CONTROLLED_SELLING",
        "regime": "TRENDING_UP",
        "sequence_position": {
            "index": index,
            "is_latest": latest,
        },
    }


def _object_node(graph: dict[str, Any], object_id: str) -> dict[str, Any]:
    return next(
        node
        for node in graph["nodes"]
        if node["node_type"] == "MARKET_OBJECT" and node["object_id"] == object_id
    )


def test_graph_preserves_object_evidence_and_emits_only_proven_relations() -> None:
    candles = [_candle(0), _candle(1), _candle(2, latest=True)]
    objects = [
        {
            "object_type": "reaction zone",
            "object_id": "zone-7",
            "direction": "BUY",
            "confidence": 0.91,
            "anchor_candle_id": "bar-1",
            "lifecycle": {
                "state": "active",
                "first_seen": 101,
                "last_seen": 109,
                "duration_candles": 8,
                "age_candles": 9,
            },
            "normalized_bounds": {
                "left": 0.20,
                "top": 0.30,
                "right": 0.60,
                "bottom": 0.70,
            },
            "normalized_points": [[0.2, 0.3], [0.6, 0.7]],
        },
        {
            "object_type": "price imbalance",
            "object_id": "imbalance-2",
            "direction": "SELL",
            "confidence": 0.73,
            "lifecycle_state": "forming",
            "first_seen": "frame-108",
            "last_seen": "frame-109",
            "duration": 1,
            "age": 2,
            "geometry": {
                "coordinate_space": "NORMALIZED_FRAME",
                "bounds": [0.40, 0.50, 0.80, 0.90],
            },
        },
    ]

    graph = build_object_relationship_graph_v3(candles, objects)

    assert graph["schema_version"] == OBJECT_RELATIONSHIP_GRAPH_SCHEMA_VERSION
    assert graph["status"] == "READY"
    assert graph["study_only"] is True
    assert graph["observation_only"] is True
    assert graph["execution_authority"] is False
    assert graph["latest_candle_id"] == "bar-2"
    assert graph["selected_counts"] == {
        "candle_nodes": 2,
        "object_nodes": 2,
        "edges": 5,
    }
    assert graph["relation_counts"] == {
        "ANCHORED_TO_CANDLE": 1,
        "OBSERVED_WITH": 2,
        "OVERLAPS": 1,
        "CO_OCCURS": 1,
    }

    zone = _object_node(graph, "zone-7")
    assert zone["object_type"] == "REACTION_ZONE"
    assert zone["direction"] == "BUY"
    assert zone["confidence"] == 0.91
    assert zone["identity_stable"] is True
    assert zone["lifecycle"] == {
        "state": "ACTIVE",
        "first_seen": 101,
        "last_seen": 109,
        "duration": 8,
        "duration_unit": "CANDLES",
        "age": 9,
        "age_unit": "CANDLES",
    }
    assert zone["geometry"] == {
        "coordinate_space": "NORMALIZED_FRAME",
        "bounds": {"left": 0.2, "top": 0.3, "right": 0.6, "bottom": 0.7},
        "points": [{"x": 0.2, "y": 0.3}, {"x": 0.6, "y": 0.7}],
        "points_truncated_count": 0,
    }
    assert zone["explicit_candle_associations"] == ["bar-1"]
    assert zone["matched_candle_associations"] == ["bar-1"]

    anchor = next(edge for edge in graph["edges"] if edge["relation"] == "ANCHORED_TO_CANDLE")
    assert anchor["source"] == zone["node_id"]
    assert anchor["proof"] == {
        "kind": "EXPLICIT_CANDLE_IDENTITY",
        "candle_id": "bar-1",
    }
    overlap = next(edge for edge in graph["edges"] if edge["relation"] == "OVERLAPS")
    assert overlap["proof"]["kind"] == "POSITIVE_NORMALIZED_RECTANGLE_INTERSECTION"
    assert overlap["proof"]["intersection_area"] == 0.04
    assert overlap["proof"]["intersection_over_union"] > 0.0


def test_graph_does_not_invent_anchors_or_overlap_from_points() -> None:
    graph = build_object_relationship_graph_v3(
        [_candle(0), _candle(1, latest=True)],
        [
            {
                "object_type": "support",
                "object_id": "support-1",
                "confidence": 0.8,
                "associated_candle_ids": ["missing-candle"],
                "normalized_points": [[0.1, 0.1], [0.9, 0.9]],
            },
            {
                "object_type": "resistance",
                "object_id": "resistance-1",
                "confidence": 0.7,
                "normalized_points": [[0.9, 0.1], [0.1, 0.9]],
            },
        ],
    )

    relations = [edge["relation"] for edge in graph["edges"]]
    assert "ANCHORED_TO_CANDLE" not in relations
    assert "OVERLAPS" not in relations
    assert relations.count("OBSERVED_WITH") == 2
    assert relations.count("CO_OCCURS") == 1
    support = _object_node(graph, "support-1")
    assert support["matched_candle_associations"] == []
    assert support["unresolved_or_omitted_candle_associations"] == ["missing-candle"]
    assert graph["relationship_contract"]["observed_with_is_anchor"] is False
    assert graph["relationship_contract"]["object_co_occurrence_is_causal"] is False


def test_observation_only_object_identity_is_never_reported_as_stable() -> None:
    graph = build_object_relationship_graph_v3(
        [_candle(0, latest=True)],
        [
            {
                "object_type": "support",
                "object_id": "observation-1-1",
                "identity_scope": "OBSERVATION_ONLY",
                "confidence": 0.8,
                "coordinate_space": "NORMALIZED",
                "bounds": [0.1, 0.2, 0.4, 0.3],
            }
        ],
    )

    node = _object_node(graph, "observation-1-1")
    assert node["identity_scope"] == "OBSERVATION_ONLY"
    assert node["identity_stable"] is False


def test_explicit_latest_anchor_replaces_generic_observed_with_edge() -> None:
    graph = build_object_relationship_graph_v3(
        [_candle(0), _candle(1, latest=True)],
        [
            {
                "object_type": "structure change",
                "object_id": "bos-1",
                "confidence": 1.0,
                "candle_id": "bar-1",
            }
        ],
    )

    assert [edge["relation"] for edge in graph["edges"]] == ["ANCHORED_TO_CANDLE"]
    assert graph["relation_counts"]["OBSERVED_WITH"] == 0


def test_graph_caps_are_deterministic_and_report_every_omission() -> None:
    candles = [_candle(index, latest=index == 19) for index in range(20)]
    objects = [
        {
            "object_type": "zone",
            "object_id": f"zone-{index:02d}",
            "confidence": index / 20.0,
            "anchor_candle_id": f"bar-{index}",
            "normalized_bounds": [0.1, 0.1, 0.8, 0.8],
            "normalized_points": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3], [0.4, 0.4]],
        }
        for index in range(20)
    ]

    graph = build_object_relationship_graph_v3(
        candles,
        objects,
        max_object_nodes=5,
        max_candle_nodes=3,
        max_edges=12,
        max_points_per_object=2,
    )
    reordered = build_object_relationship_graph_v3(
        list(reversed(candles)),
        list(reversed(objects)),
        max_object_nodes=5,
        max_candle_nodes=3,
        max_edges=12,
        max_points_per_object=2,
    )

    assert graph == reordered
    assert graph["status"] == "READY_TRUNCATED"
    assert graph["truncated"] is True
    assert graph["selected_counts"] == {
        "candle_nodes": 3,
        "object_nodes": 5,
        "edges": 12,
    }
    assert graph["truncated_counts"]["objects"] == 15
    assert graph["truncated_counts"]["candles"] == 3
    assert graph["truncated_counts"]["edges"] > 0
    assert all(
        node["geometry"]["points_truncated_count"] == 2
        for node in graph["nodes"]
        if node["node_type"] == "MARKET_OBJECT"
    )


@pytest.mark.parametrize(
    "unsafe_object,match",
    [
        (
            {
                "object_type": "zone",
                "object_id": "bad-out-of-range",
                "normalized_bounds": [-0.1, 0.2, 0.5, 0.6],
            },
            "must be in \\[0, 1\\]",
        ),
        (
            {
                "object_type": "zone",
                "object_id": "bad-pixels",
                "coordinate_space": "PIXEL",
                "bounds": [10, 20, 30, 40],
            },
            "requires an explicit normalized coordinate space",
        ),
        (
            {
                "object_type": "zone",
                "object_id": "bad-degenerate",
                "normalized_bounds": [0.5, 0.5, 0.5, 0.8],
            },
            "positive width and height",
        ),
        (
            {
                "object_type": "zone",
                "object_id": "bad-confidence",
                "confidence": float("nan"),
            },
            "must be a finite number",
        ),
    ],
)
def test_graph_fails_closed_on_unsafe_geometry(
    unsafe_object: dict[str, Any], match: str
) -> None:
    with pytest.raises(ObjectRelationshipGraphValidationError, match=match):
        build_object_relationship_graph_v3([_candle(0, latest=True)], [unsafe_object])


def test_graph_strips_trade_instructions_and_never_grants_authority() -> None:
    graph = build_object_relationship_graph_v3(
        [_candle(0, latest=True)],
        [
            {
                "object_type": "reaction zone",
                "object_id": "zone-safe",
                "confidence": 0.6,
                "action": "BUY",
                "entry_permission": True,
                "execution_authority": True,
                "order": {"side": "BUY", "quantity": 999},
            }
        ],
    )

    serialized = json.dumps(graph, sort_keys=True)
    assert '"action"' not in serialized
    assert '"entry_permission"' not in serialized
    assert '"order"' not in serialized
    assert graph["execution_authority"] is False
    assert graph["safety"] == {
        "causal_claim": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
        "may_issue_orders": False,
    }
    assert all(edge["causal"] is False for edge in graph["edges"])
