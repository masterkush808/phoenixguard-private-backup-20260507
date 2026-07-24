from __future__ import annotations

# pyright: reportPrivateUsage=false

import json
from typing import Any, cast

from phoenixguard.mobile_api.live_state_v3 import (
    _compact_latest_signal,
    _compact_tracking_summary,
)
from phoenixguard.mobile_api.app import _bounded_operator_projection_context
from phoenixguard.mobile_api.operator_workspace_v1 import build_operator_workspace_v1
from phoenixguard.mobile_api.window_tracker import (
    _compact_live_state_latest_signal_payload,
    _compact_live_state_market_payload,
)
from phoenixguard.tracking.tracking_episode_v3 import (
    default_tracking_episode_v1,
    tracking_episode_history_entry_v1,
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


def _episode_study() -> dict[str, object]:
    return {
        "schema_version": "PG_TRACKING_STUDY_SNAPSHOT_V3",
        "status": "STUDIED",
        "study_only": True,
        "execution_authority": False,
        "major_trend": {"side": "BUY", "slope": 0.12, "confidence": 0.82},
        "inner_trend": {"side": "SELL", "slope": -0.04, "confidence": 0.44},
        "current_pressure": {"side": "BUY", "slope": 0.03, "confidence": 0.3},
        "directional_read": {
            "side": "BUY",
            "confidence": 0.73,
            "status": "DIRECTIONAL_STUDY",
        },
        "behavior": {
            "state": "REST",
            "direction": "HOLD",
            "candle_count": 3,
            "duration_seconds": 900,
            "market_story": "Major trend up; inner pullback; resting for 3 candles.",
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
    assert operator_any["permission"]["allowed"] is False


def test_archived_episode_history_reports_regression_instead_of_wait() -> None:
    episode = default_tracking_episode_v1(session_id="study-session")
    episode.update(
        {
            "episode_id": "episode-regression",
            "state": "STOPPED",
            "started_at": "2026-07-24T00:00:00Z",
            "updated_at": "2026-07-24T00:05:00Z",
            "stopped_at": "2026-07-24T00:05:00Z",
            "pair": "CAD/JPY OTC",
            "timeframe": "M5",
            "anchor": {"frame_id": 10, "market_study_v3": _episode_study()},
            "events": [
                {
                    "event_id": "episode-regression:E1",
                    "step": 1,
                    "observed_at": "2026-07-24T00:05:00Z",
                    "predicted_block": {"side": "BUY"},
                    "actual_block": {"side": "BUY"},
                    "direction_agreement": True,
                    "after_reference": {"frame_id": 11},
                    "market_study_v3": _episode_study(),
                }
            ],
        }
    )
    archived = tracking_episode_history_entry_v1(episode)
    operator = build_operator_workspace_v1(
        {
            "session_id": "study-session",
            "display_frame_id": 11,
            "tracking_episode_history": [archived],
        },
        now_epoch=1_790_000_001.0,
    )

    operator_history = cast(list[dict[str, Any]], operator["history"])
    event = next(row for row in operator_history if row.get("event_index") == 1)
    summary = next(
        row
        for row in operator_history
        if row.get("id") == "episode-regression-summary"
    )
    assert event["major_trend"]["side"] == "BUY"
    assert event["inner_trend"]["side"] == "SELL"
    assert event["behavior"]["state"] == "REST"
    assert "major trend up" in event["summary"].lower()
    assert summary["direction"] == "BUY"
    assert "regression study" in summary["summary"].lower()


def test_operator_projection_context_keeps_live_and_episode_studies() -> None:
    episode = default_tracking_episode_v1(session_id="study-session")
    episode.update(
        {
            "episode_id": "episode-context",
            "state": "ACTIVE",
            "anchor": {"frame_id": 20, "market_study_v3": _episode_study()},
            "events": [
                {
                    "event_id": "episode-context:E1",
                    "step": 1,
                    "observed_at": "2026-07-24T00:05:00Z",
                    "predicted_block": {"side": "BUY"},
                    "actual_block": {"side": "BUY"},
                    "market_study_v3": _episode_study(),
                }
            ],
        }
    )
    archived = tracking_episode_history_entry_v1(
        {**episode, "state": "STOPPED", "stopped_at": "2026-07-24T00:06:00Z"}
    )
    bounded = _bounded_operator_projection_context(
        {
            "session_id": "study-session",
            "tracking_summary": {"market_study_v3": _market_study()},
            "tracking_episode": episode,
            "tracking_episode_history": [archived],
        }
    )

    bounded_any = cast(dict[str, Any], bounded)
    assert bounded_any["tracking_summary"]["market_study_v3"][
        "directional_read"
    ]["side"] == "BUY"
    assert bounded_any["tracking_episode"]["anchor"]["market_study_v3"][
        "major_trend"
    ]["side"] == "BUY"
    assert bounded_any["tracking_episode"]["events"][0]["market_study_v3"][
        "behavior"
    ]["state"] == "REST"
    assert bounded_any["tracking_episode_history"][0][
        "final_market_study_v3"
    ]["directional_read"]["side"] == "BUY"


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

    serialized = json.dumps(bounded_study).lower()
    assert "source_path" not in serialized
    assert "ohlc" not in serialized
    assert "exact_geometry" not in serialized
    assert "recent_candles" not in serialized
    assert "private_trade_id" not in serialized
    assert "private_embedding" not in serialized
    assert "private_rows" not in serialized
    assert "private_geometry" not in serialized

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
