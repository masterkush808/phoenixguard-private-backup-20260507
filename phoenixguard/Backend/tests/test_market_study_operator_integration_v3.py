from __future__ import annotations

# pyright: reportPrivateUsage=false

import json
from typing import Any, cast

from phoenixguard.mobile_api.live_state_v3 import (
    _compact_live_poll_session_payload,
    _compact_latest_signal,
    _compact_tracking_summary,
)
from phoenixguard.mobile_api.app import _bounded_operator_projection_context
from phoenixguard.mobile_api.operator_workspace_v1 import build_operator_workspace_v1
from phoenixguard.mobile_api.window_tracker import (
    _compact_live_state_latest_signal_payload,
    _compact_live_state_market_payload,
    _study_entry,
)


CONTINUOUS_RESEARCH_KEYS = (
    "motif_lattice",
    "survival_network",
    "path_reconstruction",
    "adaptive_feature_ontology",
    "concept_drift",
    "regime_partition",
    "cross_pair_association",
    "claim_proofs",
)


def _market_study() -> dict[str, object]:
    return {
        "schema_version": "PG_MARKET_STUDY_V3",
        "status": "STUDIED",
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
        "symbol": "CAD/JPY OTC",
        "timeframe": "M5",
        "closed_candle_key": "closed-9",
        "closed_candle_sequence": 9,
        "regression": {
            "regime": "UPTREND",
            "major_trend": {
                "side": "BUY",
                "slope": 0.12,
                "confidence": 0.82,
                "window_candles": 18,
            },
            "inner_trend": {
                "side": "SELL",
                "slope": -0.04,
                "confidence": 0.44,
                "window_candles": 8,
            },
            "current_pressure": {
                "side": "BUY",
                "slope": 0.03,
                "confidence": 0.3,
                "window_candles": 4,
            },
        },
        "candle_intelligence": {
            "status": "STUDIED",
            "studied_count": 18,
            "summary": {
                "direction_counts": {"BULLISH": 11, "BEARISH": 7},
                "type_counts": {"BULLISH_BALANCED": 8},
                "personality_counts": {"CONTROLLED_BUYING": 8},
                "rejection_rate": 0.22,
                "acceptance_rate": 0.17,
            },
            "latest": {
                "direction": "BULLISH",
                "type": "LOWER_WICK_REJECTION",
                "personality": "LIQUIDITY_REJECTION_LOW",
                "regime": "UPTREND",
                "relation_to_previous": "HIGHER_HIGH_HIGHER_LOW",
                "ratios": {
                    "body_to_range": 0.46,
                    "upper_wick_to_range": 0.12,
                    "lower_wick_to_range": 0.42,
                },
                "interaction": {
                    "rejection": {"detected": True, "side": "LOW"},
                    "acceptance": {"detected": False, "side": "NONE"},
                },
            },
        },
        "behavior": {
            "status": "STUDIED",
            "major_trend": {"label": "UP", "strength": 0.8, "candle_count": 18},
            "inner_trend": {"label": "DOWN", "strength": 0.4, "candle_count": 8},
            "current_state": {
                "state": "REST",
                "direction": "SIDEWAYS",
                "candle_count": 3,
                "duration_seconds": 900,
            },
            "current_segment": {
                "state": "REST",
                "candle_count": 3,
                "duration_seconds": 900,
                "next_state": "NONE",
            },
            "market_story": "Major trend: UP. Inner trend: DOWN. Price is resting sideways for 3 candles.",
        },
        "historical_similarity": {
            "status": "READY",
            "match_count": 4,
            "historical_continuation": {
                "status": "SUPPORTED",
                "direction": "UP",
                "confidence": 0.67,
                "support": 4,
                "minimum_support": 3,
            },
            "matches": [],
        },
        "pair_dna": {
            "observation_count": 21,
            "candle_count": 377,
            "candle": {"direction_counts": {"BULLISH": 220}},
            "behavior": {"major_trend_counts": {"UP": 16}},
            "regime_counts": {"UPTREND": 250},
            "object_type_counts": {"LIQUIDITY_POOL": 12},
            "retracement_confluence": {
                "schema_version": "PG_PAIR_DNA_RETRACEMENT_AGGREGATES_V3",
                "study_only": True,
                "observation_only": True,
                "execution_authority": False,
                "completed_study_count": 10,
                "level_support": [
                    {"level_id": "OTE_70_5", "completed_study_count": 7},
                    {"level_id": "CUSTOM_71_8", "completed_study_count": 3},
                ],
                "partitions_truncated_count": 0,
                "level_catalog": {
                    "OTE_70_5": {
                        "level_ratio": 0.705,
                        "classification": "ICT_STYLE_OTE_REFERENCE",
                        "experimental": False,
                        "user_defined": False,
                        "standard_fibonacci": False,
                    },
                    "CUSTOM_71_8": {
                        "level_ratio": 0.718,
                        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
                        "experimental": True,
                        "user_defined": True,
                        "standard_fibonacci": False,
                    },
                },
                "empirical_partitions": [
                    {
                        "bucket_id": "private-bucket-705",
                        "partition": {
                            "symbol": "CAD/JPY OTC",
                            "timeframe": "M5",
                            "regime": "UPTREND",
                            "side": "BUY",
                            "coordinate_space": "PRICE",
                            "level_id": "OTE_70_5",
                            "level_ratio": 0.705,
                            "classification": "ICT_STYLE_OTE_REFERENCE",
                            "experimental": False,
                            "user_defined": False,
                            "standard_fibonacci": False,
                            "object_type": "REACTION_ZONE",
                        },
                        "support": {
                            "completed_studies": 7,
                            "directional_alignment_label_count": 6,
                            "side_adjusted_return_count": 4,
                        },
                        "counts": {
                            "relations": {
                                "RETRACEMENT_LEVEL_OVERLAPS_OBJECT": 7
                            },
                            "outcome_directions": {"UP": 4, "DOWN": 2},
                            "directional_alignment_count": 4,
                        },
                        "empirical_rates": {
                            "direction_frequency": {
                                "UP": 0.666667,
                                "DOWN": 0.333333,
                            },
                            "directional_alignment_rate": 0.666667,
                            "average_side_adjusted_return": 0.125,
                        },
                    },
                    {
                        "bucket_id": "private-bucket-718",
                        "partition": {
                            "symbol": "CAD/JPY OTC",
                            "timeframe": "M5",
                            "regime": "DOWNTREND",
                            "side": "SELL",
                            "coordinate_space": "PRICE",
                            "level_id": "CUSTOM_71_8",
                            "level_ratio": 0.718,
                            "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
                            "experimental": True,
                            "user_defined": True,
                            "standard_fibonacci": False,
                            "object_type": "PRICE_IMBALANCE",
                        },
                        "support": {
                            "completed_studies": 3,
                            "directional_alignment_label_count": 2,
                            "side_adjusted_return_count": 2,
                        },
                        "counts": {
                            "relations": {
                                "RETRACEMENT_LEVEL_NEAR_TOUCHES_OBJECT": 3
                            },
                            "outcome_directions": {"DOWN": 2},
                            "directional_alignment_count": 1,
                        },
                        "empirical_rates": {
                            "direction_frequency": {"DOWN": 1.0},
                            "directional_alignment_rate": 0.5,
                            "average_side_adjusted_return": -0.04,
                        },
                    },
                ],
            },
        },
        "candle_ledger": {
            "schema_version": "PG_CANDLE_LEDGER_V3",
            "status": "RECORDED",
            "study_only": True,
            "execution_authority": False,
            "pair_id": "pair-cad-jpy-m5",
            "unique_candle_count": 377,
            "total_observation_count": 402,
        },
        "object_relationship_graph": {
            "schema_version": "PG_OBJECT_RELATIONSHIP_GRAPH_V3",
            "status": "READY",
            "study_only": True,
            "observation_only": True,
            "execution_authority": False,
            "latest_candle_id": "closed-9",
            "selected_counts": {"candle_nodes": 1, "object_nodes": 4, "edges": 9},
            "relation_counts": {"OBSERVED_WITH": 4, "CO_OCCURS": 5},
            "truncated": False,
            "nodes": [{"private_geometry": [0.1, 0.2, 0.3, 0.4]}],
            "retracement_study": {
                "schema_version": "PG_RETRACEMENT_CONFLUENCE_STUDY_V3",
                "status": "STUDIED",
                "study_only": True,
                "observation_only": True,
                "execution_authority": False,
                "counts": {"observations": 3, "relations": 3},
                "truncated": False,
                "level_catalog": [
                    {
                        "level_id": "OTE_70_5",
                        "level_ratio": 0.705,
                        "classification": "ICT_STYLE_OTE_REFERENCE",
                    },
                    {
                        "level_id": "CUSTOM_71_8",
                        "level_ratio": 0.718,
                        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
                    },
                ],
                "observations": [
                    {
                        "study_id": "private-study-705-a",
                        "status": "COMPLETED",
                        "identity_stable": True,
                        "swing_id": "private-swing-a",
                        "regime": "UPTREND",
                        "side": "BUY",
                        "coordinate_space": "PRICE",
                        "level_id": "OTE_70_5",
                        "level_ratio": 0.705,
                        "object_type": "REACTION_ZONE",
                        "object_id": "private-object-a",
                        "level_value": 151.125,
                        "object_value_bounds": {"low": 151.1, "high": 151.2},
                        "relation": "RETRACEMENT_LEVEL_OVERLAPS_OBJECT",
                        "observational_confluence": True,
                        "causal": False,
                    },
                    {
                        "study_id": "private-study-705-b",
                        "status": "COMPLETED",
                        "identity_stable": True,
                        "swing_id": "private-swing-b",
                        "regime": "UPTREND",
                        "side": "BUY",
                        "coordinate_space": "PRICE",
                        "level_id": "OTE_70_5",
                        "level_ratio": 0.705,
                        "object_type": "PRICE_IMBALANCE",
                        "object_id": "private-object-b",
                        "relation": "RETRACEMENT_LEVEL_NEAR_TOUCHES_OBJECT",
                        "observational_confluence": True,
                        "causal": False,
                    },
                    {
                        "study_id": "private-study-718",
                        "status": "COMPLETED",
                        "identity_stable": True,
                        "swing_id": "private-swing-c",
                        "regime": "DOWNTREND",
                        "side": "SELL",
                        "coordinate_space": "PRICE",
                        "level_id": "CUSTOM_71_8",
                        "level_ratio": 0.718,
                        "object_type": "REACTION_ZONE",
                        "object_id": "private-object-c",
                        "relation": "RETRACEMENT_LEVEL_OVERLAPS_OBJECT",
                        "observational_confluence": True,
                        "causal": False,
                    },
                ],
            },
        },
        "outcome_maturation": {
            "status": "MATURED",
            "matched_candle_id": "closed-8",
            "coordinate_space": "PRICE",
            "study_only": True,
            "execution_authority": False,
        },
        "directional_read": {
            "side": "BUY",
            "confidence": 0.73,
            "status": "DIRECTIONAL_STUDY",
            "reasons": ["major regression: buy (82%)"],
        },
    }


def test_market_study_survives_both_live_state_compaction_paths() -> None:
    study = _market_study()
    tracking = {"detected_market": "CAD/JPY OTC", "market_study_v3": study}
    signal = {"action": "BUY", "market_study_v3": study}

    assert _compact_tracking_summary(tracking)["market_study_v3"] == study
    assert _compact_latest_signal(signal)["market_study_v3"] == study
    assert _compact_live_state_market_payload(tracking)["market_study_v3"] == study
    assert _compact_live_state_latest_signal_payload(signal)["market_study_v3"] == study
    compact_poll = _compact_live_poll_session_payload(
        {"tracking_summary": tracking, "latest_signal": signal}
    )
    assert compact_poll["tracking_summary"]["market_study_v3"] == study
    assert compact_poll["latest_signal"]["market_study_v3"] == study


def test_operator_workspace_exposes_study_separately_from_permission() -> None:
    operator = build_operator_workspace_v1(
        {
            "session_id": "study-session",
            "tracking_enabled": True,
            "display_frame_id": 12,
            "last_capture_epoch": 1_790_000_000.0,
            "tracking_summary": {
                "detected_market": "CAD/JPY OTC",
                "detected_timeframe": "M5",
                "market_study_v3": _market_study(),
            },
        },
        now_epoch=1_790_000_001.0,
    )

    operator_any = cast(dict[str, Any], operator)
    study = operator_any["tracking"]["market_study_v3"]
    assert study["regression"]["major_trend"]["side"] == "BUY"
    assert study["regression"]["inner_trend"]["side"] == "SELL"
    assert study["behavior"]["current_state"]["state"] == "REST"
    assert study["directional_read"]["side"] == "BUY"
    assert study["execution_authority"] is False
    assert "forecast" not in study
    for research_key in CONTINUOUS_RESEARCH_KEYS:
        research_contract = study[research_key]
        assert research_contract["study_only"] is True
        assert research_contract["causal"] is False
        assert research_contract["execution_authority"] is False
    retracement = study["retracement_study"]
    assert retracement["study_only"] is True
    assert retracement["observation_only"] is True
    assert retracement["execution_authority"] is False
    assert retracement["can_grant_entry_permission"] is False
    assert retracement["graph_completed_observation_count"] == 3
    assert retracement["pair_dna_completed_study_count"] == 10
    assert retracement["empirical_partitions_truncated_count"] == 0
    levels = {row["level_id"]: row for row in retracement["levels"]}
    assert levels["OTE_70_5"]["graph_support"] == 2
    assert levels["OTE_70_5"]["pair_dna_support"] == 7
    assert levels["CUSTOM_71_8"] == {
        "level_id": "CUSTOM_71_8",
        "level_ratio": 0.718,
        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
        "label": "71.8% custom experimental nonstandard retracement",
        "experimental": True,
        "user_defined": True,
        "standard_fibonacci": False,
        "graph_support": 1,
        "pair_dna_support": 3,
    }
    partitions = retracement["empirical_partitions"]
    assert partitions[0]["completed_study_count"] == 7
    assert partitions[0]["observation_regime"] == "UPTREND"
    assert partitions[0]["regime_basis"] == (
        "CURRENT_STUDY_FRAME_AT_CONFLUENCE_OBSERVATION"
    )
    assert "regime" not in partitions[0]
    assert "70.5% ICT-style OTE reference" in partitions[0][
        "partition_label"
    ]
    assert partitions[0]["directional_alignment_label_count"] == 6
    assert partitions[0]["directional_alignment_count"] == 4
    assert partitions[0]["directional_alignment_rate"] == 0.666667
    assert partitions[1]["average_side_adjusted_return"] == -0.04
    assert operator_any["permission"]["allowed"] is False


def test_automatic_recent_study_history_reports_regression_instead_of_wait() -> None:
    completed_study = _market_study()
    operator = build_operator_workspace_v1(
        {
            "session_id": "study-session",
            "display_frame_id": 11,
            "tracking_summary": {"market_study_v3": completed_study},
            "recent_studies": [
                {
                    "id": "closed-9",
                    "frame_id": 11,
                    "observed_at": 1_790_000_000.0,
                    "market_study_v3": completed_study,
                }
            ],
        },
        now_epoch=1_790_000_001.0,
    )

    operator_history = cast(list[dict[str, Any]], operator["history"])
    study_row = next(row for row in operator_history if row.get("id") == "closed-9")
    assert study_row["major_trend"]["side"] == "BUY"
    assert study_row["inner_trend"]["side"] == "SELL"
    assert study_row["behavior"]["current_state"]["state"] == "REST"
    assert study_row["regression_read"]["side"] == "BUY"
    assert "major trend up" in study_row["summary"].lower()
    assert "regression read up" in study_row["summary"].lower()


def test_normal_study_entry_populates_regression_history() -> None:
    completed_study = _market_study()
    entry = _study_entry(
        {"market_study_v3": completed_study},
        {"market_study_v3": completed_study},
        frame_id=12,
    )

    assert entry["market_study_v3"] == completed_study
    assert entry["market"] == "CAD/JPY OTC"
    assert entry["timeframe"] == "M5"
    operator = build_operator_workspace_v1(
        {
            "session_id": "normal-study-entry",
            "display_frame_id": 12,
            "tracking_summary": {"market_study_v3": completed_study},
            "recent_studies": [entry],
        }
    )
    history = cast(list[dict[str, Any]], operator["history"])
    assert len(history) == 1
    assert history[0]["major_trend"]["side"] == "BUY"
    assert history[0]["inner_trend"]["side"] == "SELL"


def test_operator_history_never_republishes_an_old_pair() -> None:
    old_study = _market_study()
    current_study = cast(dict[str, object], json.loads(json.dumps(old_study)))
    current_study["symbol"] = "GBP/USD OTC"
    current_study["closed_candle_key"] = "gbp-closed-12"

    operator = build_operator_workspace_v1(
        {
            "session_id": "pair-boundary",
            "display_frame_id": 12,
            "tracking_summary": {"market_study_v3": current_study},
            "recent_studies": [
                {
                    "id": "old-cad-row",
                    "frame_id": 11,
                    "market_study_v3": old_study,
                },
                {
                    "id": "current-gbp-row",
                    "frame_id": 12,
                    "market_study_v3": current_study,
                },
            ],
        }
    )

    history = cast(list[dict[str, Any]], operator["history"])
    assert [row["id"] for row in history] == ["gbp-closed-12"]
    assert history[0]["market_study_v3"]["symbol"] == "GBP/USD OTC"


def test_operator_projection_context_keeps_live_and_automatic_history() -> None:
    bounded = _bounded_operator_projection_context(
        {
            "session_id": "study-session",
            "tracking_summary": {"market_study_v3": _market_study()},
            "recent_studies": [
                {
                    "id": "closed-8",
                    "frame_id": 19,
                    "observed_at": 1_789_999_700.0,
                    "side": "SELL",
                    "state": "REST",
                    "summary": "Major trend up; inner trend down; resting.",
                }
            ],
        }
    )

    bounded_any = cast(dict[str, Any], bounded)
    assert bounded_any["tracking_summary"]["market_study_v3"][
        "directional_read"
    ]["side"] == "BUY"
    assert bounded_any["recent_studies"] == [
        {
            "observed_at": 1_789_999_700.0,
            "frame_id": 19,
            "side": "SELL",
            "state": "REST",
            "summary": "Major trend up; inner trend down; resting.",
        }
    ]
    assert bounded_any["history"] == bounded_any["recent_studies"]


def test_operator_projection_keeps_nested_study_evidence_without_private_payload() -> None:
    study = cast(dict[str, Any], _market_study())
    study["closed_candle_key"] = "closed-9"
    study["source_path"] = "C:/private/raw-capture.png"
    candle = cast(dict[str, Any], study["candle_intelligence"])
    latest = cast(dict[str, Any], candle["latest"])
    latest["ohlc"] = {
        "open": 151.123456,
        "high": 151.234567,
        "low": 151.012345,
        "close": 151.200001,
    }
    latest["exact_geometry"] = {"body_top_px": 422.0}
    candle["recent_candles"] = [
        {"ohlc": {"open": float(index)}, "source_path": f"private-{index}"}
        for index in range(20)
    ]
    similarity = cast(dict[str, Any], study["historical_similarity"])
    continuation = cast(
        dict[str, Any],
        similarity["historical_continuation"],
    )
    continuation["probabilities"] = {"UP": 0.7, "DOWN": 0.2, "REST": 0.1}
    similarity["matches"] = [
        {
            "sequence_id": f"historical-{index}",
            "similarity": 0.91 - index * 0.01,
            "regime": "UPTREND",
            "outcome": {
                "direction": "UP" if index % 2 == 0 else "DOWN",
                "realized_return": 0.2,
                "success": True,
                "horizon_candles": 1,
                "private_trade_id": f"trade-{index}",
            },
            "explanations": ["private high-dimensional fingerprint"],
        }
        for index in range(12)
    ]
    similarity["similarity_graph"] = {
        "schema_version": "PG_SIMILARITY_GRAPH_V3",
        "status": "READY",
        "graph_kind": "BOUNDED_HISTORICAL_SEQUENCE_SIMILARITY",
        "directed": False,
        "study_only": True,
        "execution_authority": False,
        "node_count": 64,
        "edge_count": 30,
        "nodes": [{"private_embedding": [1.0] * 128}],
        "edges": [
            {
                "source": f"node-{index}",
                "target": f"node-{index + 1}",
                "similarity": 0.8,
                "shared_object_types": ["PRIVATE_OBJECT"],
            }
            for index in range(30)
        ],
    }
    pair_dna = cast(dict[str, Any], study["pair_dna"])
    pair_dna["outcome_associations"] = [
        {
            "feature": "CANDLE_TYPE=LOWER_WICK_REJECTION",
            "support": 12,
            "direction_probabilities": {
                "UP": 0.75,
                "DOWN": 0.15,
                "REST": 0.10,
            },
            "success_rate": 0.75,
            "average_realized_return": 0.14,
            "private_rows": ["trade-1", "trade-2"],
        }
    ]

    bounded = _bounded_operator_projection_context(
        {
            "session_id": "nested-study-session",
            "tracking_summary": {"market_study_v3": study},
        }
    )
    bounded_study = cast(
        dict[str, Any],
        cast(dict[str, Any], bounded["tracking_summary"])["market_study_v3"],
    )

    bounded_latest = bounded_study["candle_intelligence"]["latest"]
    assert bounded_latest["interaction"]["rejection"] == {
        "detected": True,
        "side": "LOW",
    }
    assert bounded_latest["interaction"]["acceptance"] == {
        "detected": False,
        "side": "NONE",
    }
    bounded_similarity = bounded_study["historical_similarity"]
    assert bounded_similarity["historical_continuation"]["probabilities"]["UP"] == 0.7
    assert bounded_similarity["matches"][0]["outcome"] == {
        "direction": "UP",
        "realized_return": 0.2,
        "success": True,
        "horizon_candles": 1,
    }
    assert len(bounded_similarity["matches"]) == 8
    assert len(bounded_similarity["similarity_graph"]["edges"]) == 24
    assert bounded_study["pair_dna"]["outcome_associations"][0][
        "direction_probabilities"
    ]["UP"] == 0.75
    assert bounded_study["candle_ledger"]["unique_candle_count"] == 377
    assert bounded_study["object_relationship_graph"]["relation_counts"] == {
        "OBSERVED_WITH": 4,
        "CO_OCCURS": 5,
    }
    assert bounded_study["outcome_maturation"]["status"] == "MATURED"
    bounded_retracement = bounded_study["retracement_study"]
    assert bounded_retracement["graph_completed_observation_count"] == 3
    assert bounded_retracement["pair_dna_completed_study_count"] == 10
    assert len(bounded_retracement["levels"]) == 2
    assert len(bounded_retracement["empirical_partitions"]) == 2
    assert bounded_retracement["can_grant_entry_permission"] is False
    assert bounded_study["object_relationship_graph"]["retracement_study"] == {
        "schema_version": "PG_RETRACEMENT_CONFLUENCE_STUDY_V3",
        "status": "STUDIED",
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "truncated": False,
        "completed_observation_count": 3,
        "level_support": [
            {
                "level_id": "OTE_70_5",
                "level_ratio": 0.705,
                "classification": "ICT_STYLE_OTE_REFERENCE",
                "label": "70.5% ICT-style OTE reference",
                "experimental": False,
                "user_defined": False,
                "standard_fibonacci": False,
                "completed_observation_count": 2,
            },
            {
                "level_id": "CUSTOM_71_8",
                "level_ratio": 0.718,
                "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
                "label": "71.8% custom experimental nonstandard retracement",
                "experimental": True,
                "user_defined": True,
                "standard_fibonacci": False,
                "completed_observation_count": 1,
            },
        ],
    }

    serialized = json.dumps(bounded_study).lower()
    assert "source_path" not in serialized
    assert "ohlc" not in serialized
    assert "exact_geometry" not in serialized
    assert "recent_candles" not in serialized
    assert "private_trade_id" not in serialized
    assert "private_embedding" not in serialized
    assert "private_rows" not in serialized
    assert "private_geometry" not in serialized
    assert "private-study" not in serialized
    assert "private-swing" not in serialized
    assert "private-object" not in serialized
    assert "private-bucket" not in serialized
    assert "object_value_bounds" not in serialized
    assert "level_value" not in serialized

    latest_only = cast(
        dict[str, Any],
        _bounded_operator_projection_context(
            {"latest_signal": {"market_study_v3": study}}
        ),
    )
    assert latest_only["latest_signal"]["market_study_v3"][
        "candle_intelligence"
    ]["latest"]["interaction"]["rejection"]["detected"] is True

    operator = build_operator_workspace_v1(bounded, now_epoch=1_790_000_001.0)
    public_study = cast(dict[str, Any], operator)["tracking"][
        "market_study_v3"
    ]
    assert public_study["candle_intelligence"]["latest"]["rejection"] == {
        "detected": True,
        "side": "LOW",
    }
    assert public_study["candle_intelligence"]["latest"]["acceptance"] == {
        "detected": False,
        "side": "NONE",
    }
    assert public_study["historical_similarity"]["matches"][0][
        "outcome_direction"
    ] == "UP"
    assert public_study["historical_similarity"]["historical_continuation"][
        "direction"
    ] == "UP"
    assert public_study["closed_candle_key"] == "closed-9"
    assert public_study["candle_ledger"]["unique_candle_count"] == 377
    assert public_study["object_relationship_graph"]["relation_counts"][
        "OBSERVED_WITH"
    ] == 4
    assert public_study["outcome_maturation"]["status"] == "MATURED"
    assert public_study["retracement_study"] == bounded_retracement


def test_retracement_projection_is_bounded_and_ignores_spoofed_top_level() -> None:
    study = cast(dict[str, Any], _market_study())
    study["retracement_study"] = {
        "graph_completed_observation_count": 999_999,
        "pair_dna_completed_study_count": 999_999,
        "can_grant_entry_permission": True,
    }
    pair_dna = cast(dict[str, Any], study["pair_dna"])
    retracement = cast(dict[str, Any], pair_dna["retracement_confluence"])
    source_partition = cast(
        list[dict[str, Any]], retracement["empirical_partitions"]
    )[0]
    retracement["empirical_partitions"] = [
        {
            **source_partition,
            "bucket_id": f"private-bucket-{index}",
            "partition": {
                **cast(dict[str, Any], source_partition["partition"]),
                "level_id": "OTE_70_5" if index % 2 == 0 else "CUSTOM_71_8",
                "level_ratio": 0.705 if index % 2 == 0 else 0.718,
                "object_type": f"REACTION_ZONE_{index:02d}",
            },
            "support": {
                "completed_studies": 24 - index,
                "directional_alignment_label_count": 1,
                "side_adjusted_return_count": 1,
            },
            "counts": {
                "relations": {f"RELATION_{offset}": 1 for offset in range(12)},
                "outcome_directions": {"UP": 1},
                "directional_alignment_count": 1,
            },
        }
        for index in range(24)
    ]
    retracement["partitions_truncated_count"] = 8

    bounded = _bounded_operator_projection_context(
        {"tracking_summary": {"market_study_v3": study}}
    )
    bounded_study = cast(
        dict[str, Any],
        cast(dict[str, Any], bounded["tracking_summary"])["market_study_v3"],
    )
    projected = bounded_study["retracement_study"]
    assert projected["graph_completed_observation_count"] == 3
    assert projected["pair_dna_completed_study_count"] == 10
    assert projected["empirical_partitions_truncated_count"] == 8
    assert projected["can_grant_entry_permission"] is False
    assert len(projected["levels"]) == 2
    assert len(projected["empirical_partitions"]) == 16
    bounded_pair = bounded_study["pair_dna"]["retracement_confluence"]
    assert bounded_pair["level_support"] == [
        {"level_id": "OTE_70_5", "completed_study_count": 7},
        {"level_id": "CUSTOM_71_8", "completed_study_count": 3},
    ]
    assert all(
        len(row["relation_counts"]) <= 8
        for row in projected["empirical_partitions"]
    )
    assert "999999" not in json.dumps(projected)
    assert "private-bucket" not in json.dumps(projected).lower()


def test_retracement_projection_fails_closed_without_observation_contracts() -> None:
    study = cast(dict[str, Any], _market_study())
    pair_dna = cast(dict[str, Any], study["pair_dna"])
    pair_retracement = cast(
        dict[str, Any], pair_dna["retracement_confluence"]
    )
    pair_retracement["execution_authority"] = True
    graph = cast(dict[str, Any], study["object_relationship_graph"])
    graph_retracement = cast(dict[str, Any], graph["retracement_study"])
    graph_retracement["observation_only"] = False

    bounded = _bounded_operator_projection_context(
        {"tracking_summary": {"market_study_v3": study}}
    )
    bounded_study = cast(
        dict[str, Any],
        cast(dict[str, Any], bounded["tracking_summary"])["market_study_v3"],
    )
    assert "retracement_study" not in bounded_study
    assert "retracement_confluence" not in bounded_study["pair_dna"]
    assert (
        "retracement_study"
        not in bounded_study["object_relationship_graph"]
    )
