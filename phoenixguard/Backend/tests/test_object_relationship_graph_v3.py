from __future__ import annotations

import json
from typing import Any

import pytest

from phoenixguard.study.object_relationship_graph_v3 import (
    OBJECT_RELATIONSHIP_GRAPH_SCHEMA_VERSION,
    RETRACEMENT_CONFLUENCE_SCHEMA_VERSION,
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


def _proven_swing_candles(
    *,
    coordinate_space: str = "PRICE",
    latest_closed: bool = True,
    identity_proven: bool = True,
) -> list[dict[str, Any]]:
    geometries = (
        (102.0, 103.0, 101.0, 102.5),
        (101.5, 102.0, 100.0, 101.0),
        (102.5, 106.0, 102.0, 105.0),
        (105.5, 110.0, 105.0, 109.0),
        (108.5, 109.0, 104.0, 105.0),
    )
    rows: list[dict[str, Any]] = []
    for index, (open_value, high, low, close) in enumerate(geometries):
        row = _candle(index, latest=index == len(geometries) - 1)
        row.update(
            {
                "closed": latest_closed if index == len(geometries) - 1 else True,
                "identity_stable": identity_proven,
                "coordinate_space": coordinate_space,
                "ohlc": {
                    "open": open_value,
                    "high": high,
                    "low": low,
                    "close": close,
                },
            }
        )
        rows.append(row)
    return rows


def _value_object(
    object_type: str,
    object_id: str,
    bounds: list[float],
    *,
    coordinate_space: str = "PRICE",
) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "object_id": object_id,
        "identity_scope": "EXPLICIT",
        "identity_stable": True,
        "confidence": 0.9,
        "normalized_bounds": [0.1, 0.2, 0.4, 0.5],
        "value_bounds": bounds,
        "value_coordinate_space": coordinate_space,
        "value_axis_source": "TEST_EXPLICIT",
    }


def test_graph_preserves_object_evidence_and_emits_only_proven_relations() -> None:
    candles = [_candle(0), _candle(1), _candle(2, latest=True)]
    objects = [
        {
            "object_type": "reaction zone",
            "object_id": "zone-7",
            "identity_scope": "EXPLICIT",
            "identity_stable": True,
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
                "identity_stable": True,
                "confidence": 0.8,
                "coordinate_space": "NORMALIZED",
                "bounds": [0.1, 0.2, 0.4, 0.3],
            }
        ],
    )

    node = _object_node(graph, "observation-1-1")
    assert node["identity_scope"] == "OBSERVATION_ONLY"
    assert node["identity_stable"] is False


def test_object_id_alone_never_proves_lifelong_retracement_identity() -> None:
    positional = _value_object("order block", "position-7", [102.9, 103.0])
    positional.update({"identity_scope": "POSITIONAL", "identity_stable": True})
    explicitly_unstable = _value_object(
        "fair value gap", "tracked-but-unproven", [102.8, 103.0]
    )
    explicitly_unstable["identity_stable"] = False
    id_only = _value_object("crowded price area", "generic-1", [102.7, 103.0])
    id_only.pop("identity_stable")
    id_only.pop("identity_scope")

    graph = build_object_relationship_graph_v3(
        _proven_swing_candles(),
        [positional, explicitly_unstable, id_only],
    )

    study = graph["retracement_study"]
    assert study["status"] == "NO_COMPARABLE_OBJECTS"
    assert study["counts"]["comparable_objects"] == 0
    assert study["observations"] == []
    assert study["relations"] == []
    assert graph["relationship_contract"][
        "retracement_requires_explicit_stable_object_identity"
    ] is True
    assert _object_node(graph, "position-7")["identity_stable"] is False
    assert _object_node(graph, "tracked-but-unproven")["identity_stable"] is False
    generic = _object_node(graph, "generic-1")
    assert generic["identity_scope"] == "EXPLICIT"
    assert generic["identity_stable"] is False

    # Current-frame display relationships remain available without promoting
    # those IDs into durable retracement evidence.
    assert graph["relation_counts"]["OBSERVED_WITH"] == 3
    assert graph["relation_counts"]["OVERLAPS"] == 3
    assert graph["relation_counts"]["CO_OCCURS"] == 3


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


def test_retracement_studies_ote_and_user_experimental_level_explainably() -> None:
    graph = build_object_relationship_graph_v3(
        _proven_swing_candles(),
        [
            _value_object("order block", "ob-1", [102.94, 102.96]),
            _value_object("fair value gap", "fvg-1", [102.81, 102.83]),
            _value_object("crowded price area", "crowd-1", [102.68, 102.70]),
            _value_object("resistance", "ignored-1", [102.8, 103.0]),
        ],
    )

    study = graph["retracement_study"]
    assert study["schema_version"] == RETRACEMENT_CONFLUENCE_SCHEMA_VERSION
    assert study["status"] == "STUDIED"
    assert study["study_only"] is True
    assert study["observation_only"] is True
    assert study["execution_authority"] is False
    assert study["proof_status"] == "PROVEN_NEWEST_CONTIGUOUS_RUN"
    assert study["proof_audit"]["selected_proven_candles"] == 5
    assert study["proof_audit"]["excluded_candles"] == 0
    assert study["counts"] == {
        "proven_completed_swings": 1,
        "comparable_objects": 3,
        "evaluated_level_object_pairs": 6,
        "observations": 5,
        "relations": 5,
    }

    catalog = {row["level_id"]: row for row in study["level_catalog"]}
    assert catalog["FIB_61_8"]["standard_fibonacci"] is True
    assert catalog["FIB_78_6"]["standard_fibonacci"] is True
    assert catalog["OTE_70_5"] == {
        "level_id": "OTE_70_5",
        "level_ratio": 0.705,
        "classification": "ICT_STYLE_OTE_REFERENCE",
        "label": "70.5% ICT-style OTE reference",
        "standard_fibonacci": False,
        "user_defined": False,
        "experimental": False,
        "evaluated_for_object_confluence": True,
    }
    assert catalog["CUSTOM_71_8"] == {
        "level_id": "CUSTOM_71_8",
        "level_ratio": 0.718,
        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
        "label": "71.8% user-defined experimental retracement",
        "standard_fibonacci": False,
        "user_defined": True,
        "experimental": True,
        "evaluated_for_object_confluence": True,
    }
    assert "nonstandard" in study["method"]["experimental_level_notice"]

    observations = study["observations"]
    ote_order_block = next(
        row
        for row in observations
        if row["level_id"] == "OTE_70_5" and row["object_id"] == "ob-1"
    )
    assert ote_order_block["status"] == "COMPLETED"
    assert ote_order_block["identity_stable"] is True
    assert ote_order_block["swing_direction"] == "UP"
    assert ote_order_block["side"] == "BULLISH"
    assert ote_order_block["observation_regime"] == "TRENDING_UP"
    assert ote_order_block["regime_basis"] == (
        "CURRENT_STUDY_FRAME_AT_CONFLUENCE_OBSERVATION"
    )
    assert "regime" not in ote_order_block
    assert ote_order_block["coordinate_space"] == "PRICE"
    assert ote_order_block["swing_start_value"] == 100.0
    assert ote_order_block["swing_end_value"] == 110.0
    assert ote_order_block["swing_range"] == 10.0
    assert ote_order_block["level_value"] == 102.95
    assert ote_order_block["relation"] == "RETRACEMENT_LEVEL_OVERLAPS_OBJECT"
    assert ote_order_block["distance"] == 0.0
    assert ote_order_block["normalized_distance"] == 0.0
    assert ote_order_block["tolerance"] == 0.15
    assert ote_order_block["tolerance_ratio"] == 0.015
    assert ote_order_block["observational_confluence"] is True
    assert ote_order_block["causal"] is False
    assert ote_order_block["completion_proof"] == {
        "kind": "TWO_SIDED_CLOSED_CANDLE_PIVOT_CONFIRMATION",
        "end_pivot_confirmed_by_candle_id": "bar-4",
        "uses_forming_candle": False,
    }

    custom_fvg = next(
        row
        for row in observations
        if row["level_id"] == "CUSTOM_71_8" and row["object_id"] == "fvg-1"
    )
    assert custom_fvg["level_value"] == 102.82
    assert custom_fvg["classification"] == "USER_DEFINED_EXPERIMENTAL_NONSTANDARD"
    assert custom_fvg["standard_fibonacci"] is False
    assert custom_fvg["user_defined"] is True
    assert custom_fvg["experimental"] is True
    assert custom_fvg["object_family"] == "FVG_IMBALANCE"
    assert len({row["study_id"] for row in observations}) == len(observations)
    assert {row["study_id"] for row in observations} == {
        row["study_id"] for row in study["relations"]
    }


def test_retracement_near_touch_distance_is_normalized_by_swing_range() -> None:
    graph = build_object_relationship_graph_v3(
        _proven_swing_candles(),
        [_value_object("order block", "point-ob", [102.8, 102.8])],
    )

    observation = next(
        row
        for row in graph["retracement_study"]["observations"]
        if row["level_id"] == "OTE_70_5"
    )
    assert observation["relation"] == "RETRACEMENT_LEVEL_NEAR_TOUCHES_OBJECT"
    assert observation["distance"] == 0.15
    assert observation["normalized_distance"] == 0.015
    assert observation["tolerance"] == 0.15

    exact_only = build_object_relationship_graph_v3(
        _proven_swing_candles(),
        [_value_object("order block", "exact-custom", [102.82, 102.82])],
        retracement_tolerance_ratio=0.0,
    )["retracement_study"]
    assert [row["level_id"] for row in exact_only["observations"]] == ["CUSTOM_71_8"]
    assert exact_only["observations"][0]["relation"] == (
        "RETRACEMENT_LEVEL_OVERLAPS_OBJECT"
    )


def test_retracement_never_uses_unconfirmed_or_unproven_candles() -> None:
    unconfirmed = _proven_swing_candles()[:-1]
    unconfirmed[-1]["sequence_position"]["is_latest"] = True
    graph = build_object_relationship_graph_v3(
        unconfirmed,
        [_value_object("order block", "ob-1", [102.9, 103.0])],
    )
    study = graph["retracement_study"]
    assert study["status"] == "NO_PROVEN_COMPLETED_SWINGS"
    assert study["counts"]["proven_completed_swings"] == 0
    assert study["observations"] == []

    forming_confirmation = build_object_relationship_graph_v3(
        _proven_swing_candles(latest_closed=False),
        [_value_object("order block", "ob-1", [102.9, 103.0])],
    )["retracement_study"]
    assert forming_confirmation["proof_status"] == "PROVEN_NEWEST_CONTIGUOUS_RUN"
    assert forming_confirmation["proof_audit"]["selected_proven_candles"] == 4
    assert forming_confirmation["proof_audit"]["excluded_without_closed_proof"] == 1
    assert forming_confirmation["observations"] == []

    unstable_identity = build_object_relationship_graph_v3(
        _proven_swing_candles(identity_proven=False),
        [_value_object("order block", "ob-1", [102.9, 103.0])],
    )["retracement_study"]
    assert unstable_identity["proof_status"] == (
        "NO_CONTIGUOUS_CANDLES_WITH_STABLE_IDENTITY_PROOF"
    )
    assert unstable_identity["proof_audit"]["excluded_without_identity_proof"] == 5
    assert unstable_identity["observations"] == []


def test_retracement_keeps_frame_geometry_separate_from_value_geometry() -> None:
    graph = build_object_relationship_graph_v3(
        _proven_swing_candles(),
        [
            {
                "object_type": "order block",
                "object_id": "normalized-only",
                "confidence": 0.9,
                "normalized_bounds": [0.0, 0.0, 1.0, 1.0],
            }
        ],
    )

    study = graph["retracement_study"]
    assert study["status"] == "NO_COMPARABLE_OBJECTS"
    assert study["counts"]["comparable_objects"] == 0
    assert study["observations"] == []
    node = _object_node(graph, "normalized-only")
    assert node["geometry"]["coordinate_space"] == "NORMALIZED_FRAME"
    assert node["value_geometry"] is None


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("MIXED_SPACE", "must match exactly"),
        ("MISSING_SPACE", "requires both value_bounds"),
        ("REVERSED_BOUNDS", "ordered from low to high"),
    ],
)
def test_retracement_fails_closed_for_mixed_or_unsafe_value_axes(
    mutation: str,
    match: str,
) -> None:
    candles = _proven_swing_candles()
    objects = [_value_object("order block", "ob-1", [102.9, 103.0])]
    if mutation == "MIXED_SPACE":
        objects[0]["value_coordinate_space"] = "NORMALIZED_PRICE_PROXY"
    elif mutation == "MISSING_SPACE":
        objects[0].pop("value_coordinate_space")
    else:
        objects[0]["value_bounds"] = [103.0, 102.0]

    with pytest.raises(ObjectRelationshipGraphValidationError, match=match):
        build_object_relationship_graph_v3(candles, objects)


def test_retracement_uses_only_newest_contiguous_same_space_proven_run() -> None:
    candles = _proven_swing_candles()
    older = _proven_swing_candles(coordinate_space="NORMALIZED_PRICE_PROXY")
    for index, row in enumerate(older):
        row["candle_id"] = f"older-{index}"
        row["sequence_position"]["index"] = index
        row["sequence_position"]["is_latest"] = False
    for index, row in enumerate(candles, start=10):
        row["sequence_position"]["index"] = index
    candles[-1]["sequence_position"]["is_latest"] = True

    graph = build_object_relationship_graph_v3(
        older + candles,
        [_value_object("order block", "ob-1", [102.9, 103.0])],
    )
    study = graph["retracement_study"]
    assert study["status"] == "STUDIED"
    assert study["proof_audit"] == {
        "input_candles": 10,
        "selected_proven_candles": 5,
        "excluded_candles": 5,
        "excluded_without_value_geometry": 0,
        "excluded_without_closed_proof": 0,
        "excluded_without_identity_proof": 0,
        "excluded_unsupported_coordinate_space": 0,
        "excluded_outside_newest_contiguous_run": 5,
        "selected_coordinate_space": "PRICE",
        "selected_start_sequence_index": 10,
        "selected_end_sequence_index": 14,
    }
    assert all(row["coordinate_space"] == "PRICE" for row in study["observations"])
    assert all(row["start_candle_id"] == "bar-1" for row in study["observations"])

    mixed_latest = _proven_swing_candles()
    mixed_latest[-1]["coordinate_space"] = "NORMALIZED_PRICE_PROXY"
    safely_empty = build_object_relationship_graph_v3(
        mixed_latest,
        [
            _value_object(
                "order block",
                "proxy-ob",
                [102.9, 103.0],
                coordinate_space="NORMALIZED_PRICE_PROXY",
            )
        ],
    )["retracement_study"]
    assert safely_empty["status"] == "NO_PROVEN_COMPLETED_SWINGS"
    assert safely_empty["proof_audit"]["selected_proven_candles"] == 1
    assert safely_empty["observations"] == []


def test_retracement_accepts_exact_matching_pixel_price_proxy_axes() -> None:
    candles = _proven_swing_candles(coordinate_space="PIXEL_PRICE_PROXY")
    graph = build_object_relationship_graph_v3(
        candles,
        [
            _value_object(
                "price imbalance",
                "pixel-fvg",
                [102.8, 103.0],
                coordinate_space="PIXEL_PRICE_PROXY",
            )
        ],
    )

    observations = graph["retracement_study"]["observations"]
    assert {row["level_id"] for row in observations} == {"OTE_70_5", "CUSTOM_71_8"}
    assert all(row["coordinate_space"] == "PIXEL_PRICE_PROXY" for row in observations)
    assert all(row["object_family"] == "FVG_IMBALANCE" for row in observations)


def test_down_swing_retracement_formula_uses_the_completed_high_to_low_leg() -> None:
    geometries = (
        (108.0, 110.0, 107.0, 109.0),
        (109.0, 112.0, 108.0, 111.0),
        (108.0, 109.0, 105.0, 106.0),
        (105.0, 106.0, 100.0, 101.0),
        (102.0, 107.0, 101.0, 106.0),
    )
    candles: list[dict[str, Any]] = []
    for index, (open_value, high, low, close) in enumerate(geometries):
        row = _candle(index, latest=index == 4)
        row.update(
            {
                "closed": True,
                "identity_stable": True,
                "ohlc": {
                    "open": open_value,
                    "high": high,
                    "low": low,
                    "close": close,
                },
            }
        )
        candles.append(row)

    observations = build_object_relationship_graph_v3(
        candles,
        [_value_object("consolidation zone", "rest-1", [108.4, 108.7])],
    )["retracement_study"]["observations"]
    ote = next(row for row in observations if row["level_id"] == "OTE_70_5")
    custom = next(row for row in observations if row["level_id"] == "CUSTOM_71_8")
    assert ote["swing_direction"] == "DOWN"
    assert ote["side"] == "BEARISH"
    assert ote["swing_start_value"] == 112.0
    assert ote["swing_end_value"] == 100.0
    assert ote["level_value"] == 108.46
    assert custom["level_value"] == 108.616
    assert custom["object_family"] == "CROWDED_CONSOLIDATION"


def test_retracement_caps_and_study_ids_are_deterministic() -> None:
    candles = _proven_swing_candles()
    objects = [
        _value_object("order block", f"ob-{index:02d}", [102.8, 103.0])
        for index in range(8)
    ]
    first = build_object_relationship_graph_v3(
        candles,
        objects,
        max_retracement_observations=3,
    )
    reordered = build_object_relationship_graph_v3(
        list(reversed(candles)),
        list(reversed(objects)),
        max_retracement_observations=3,
    )

    assert first == reordered
    study = first["retracement_study"]
    assert study["status"] == "STUDIED_TRUNCATED"
    assert study["truncated"] is True
    assert study["counts"]["observations"] == 3
    assert study["counts"]["relations"] == 3
    assert study["truncated_counts"]["observations"] == 13
    assert len({row["study_id"] for row in study["observations"]}) == 3


@pytest.mark.parametrize("ratio", [-0.001, 0.100001, float("nan"), True])
def test_retracement_tolerance_rejects_unsafe_values(ratio: object) -> None:
    with pytest.raises(ObjectRelationshipGraphValidationError):
        build_object_relationship_graph_v3(
            _proven_swing_candles(),
            [_value_object("order block", "ob-1", [102.9, 103.0])],
            retracement_tolerance_ratio=ratio,  # type: ignore[arg-type]
        )


def test_retracement_observations_carry_no_probability_or_trade_authority() -> None:
    object_row = _value_object("order block", "ob-safe", [102.9, 103.0])
    object_row.update(
        {
            "win_probability": 0.99,
            "action": "BUY",
            "entry_permission": True,
            "execution_authority": True,
            "order": {"side": "BUY", "quantity": 500},
        }
    )
    study = build_object_relationship_graph_v3(
        _proven_swing_candles(),
        [object_row],
    )["retracement_study"]

    serialized = json.dumps(study, sort_keys=True)
    assert "probability" not in serialized
    assert '"action"' not in serialized
    assert '"entry_permission"' not in serialized
    assert '"order"' not in serialized
    assert study["execution_authority"] is False
    assert study["safety"]["grants_entry_permission"] is False
    assert all(row["causal"] is False for row in study["observations"])
    assert all(row["observational_confluence"] is True for row in study["observations"])
