from __future__ import annotations

from copy import deepcopy
from typing import Any

from phoenixguard.tracking.market_object_tracker_v3 import (
    OVERLAY_SCHEMA_VERSION,
    TRACKER_SCHEMA_VERSION,
    MarketObjectTrackerV3,
    build_market_object_registry_v3,
)


def _sample_payload() -> dict[str, Any]:
    return {
        "session_id": "pocket-live-8788",
        "frame_index": 621,
        "capture_count": 77,
        "tracking_summary": {
            "chart_valid": True,
            "visible_candle_count": 8,
            "chart_region": {"pixel_bbox": [0, 0, 960, 540], "width": 960, "height": 540},
            "display_region": {"pixel_bbox": [0, 0, 960, 540], "width": 960, "height": 540},
            "detected_timeframe": "M5",
            "global_direction": "BUY",
            "local_direction": "BUY",
            "impulse_direction": "BUY",
            "overlay_kind": "CONTINUATION BUY",
            "tracked_candles": [
                {
                    "track_id": index + 1,
                    "bbox": [100 + index * 22, 310 - index * 12, 112 + index * 22, 350 - index * 12],
                    "center_x": 106 + index * 22,
                    "center_y": 330 - index * 12,
                    "direction": "BUY" if index % 3 != 1 else "SELL",
                    "color": "green" if index % 3 != 1 else "magenta",
                    "price_proxy": 0.32 + index * 0.035,
                    "confidence": 0.82,
                }
                for index in range(8)
            ],
            "structure_boxes": [
                {
                    "key": "global",
                    "label": "BUY impulse leg",
                    "bbox": [80, 220, 330, 374],
                    "direction": "BUY",
                    "confidence": 0.77,
                },
                {
                    "key": "current",
                    "label": "BUY continuation box",
                    "bbox": [250, 154, 340, 262],
                    "direction": "BUY",
                    "confidence": 0.81,
                },
            ],
            "historical_structure": [
                {
                    "key": "h10",
                    "label": "#10 pullback B69",
                    "story": "pullback into continuation support",
                    "bbox": [338, 206, 426, 294],
                    "direction": "BUY",
                    "confidence": 0.72,
                    "source_indices": [4, 5],
                }
            ],
            "support_resistance_zones": [
                {
                    "key": "support_5t",
                    "role": "support",
                    "label": "NEAREST SUPPORT 5T",
                    "bbox": [210, 300, 520, 330],
                    "direction": "BUY",
                    "confidence": 0.76,
                    "price_relation": "below_price",
                    "distance_to_latest_norm": 0.18,
                    "still_significant": True,
                },
                {
                    "key": "resistance_10t",
                    "role": "resistance",
                    "label": "NEAREST RESISTANCE 10T",
                    "bbox": [260, 112, 560, 142],
                    "direction": "SELL",
                    "confidence": 0.79,
                    "price_relation": "above_price",
                    "distance_to_latest_norm": 0.22,
                    "still_significant": True,
                },
            ],
            "projection": {
                "direction": "BUY",
                "confidence": 0.84,
                "target_first_probability": 0.78,
                "invalidation_first_probability": 0.12,
                "zones": [
                    {
                        "kind": "sniper",
                        "direction": "BUY",
                        "label": "BUY AGGRO SNIPER",
                        "bbox": [405, 218, 486, 250],
                        "invalidation_y": 292,
                        "confidence": 0.83,
                    },
                    {
                        "kind": "primary",
                        "direction": "BUY",
                        "label": "BUY RECLAIM TRIGGER",
                        "bbox": [424, 190, 510, 224],
                        "target_bbox": [430, 132, 518, 164],
                        "invalidation_y": 292,
                        "confidence": 0.86,
                        "path": [[332, 238], [446, 234], [466, 208], [504, 148]],
                    },
                ],
            },
            "angle_vectors": [
                {"id": "local-angle", "points": [[210, 318], [340, 238]], "direction": "BUY", "confidence": 0.7}
            ],
            "execution_timing": {
                "entry_area_zone": {
                    "label": "support reclaim entry",
                    "role": "support",
                    "bbox": [220, 296, 500, 326],
                    "confidence": 0.72,
                    "direction": "BUY",
                },
                "opposing_force_zone": {
                    "label": "resistance opposing force",
                    "role": "resistance",
                    "bbox": [446, 126, 550, 156],
                    "confidence": 0.74,
                    "direction": "SELL",
                },
            },
        },
        "latest_signal": {
            "action": "BUY",
            "execution_action": "HOLD",
            "confidence": 0.87,
            "effective_confidence": 0.87,
            "setup": "CONTINUATION BUY",
            "entry_state": "SNIPER_READY",
        },
        "memory_projection_active_mode": "predict",
        "memory_projection_predict": {
            "status": "ready",
            "dominant_side": "BUY",
            "memory_similarity": 0.91,
            "memory_precision_score": 0.82,
            "primary_fit": {
                "top_matches": [
                    {"entry_id": "buy-memory-a", "label": "BUY", "similarity": 0.91, "summary": "pullback continuation"}
                ],
                "top_predictions": [
                    {"entry_id": "buy-pred-a", "direction": "BUY", "confidence": 0.86, "summary": "target push"}
                ],
            },
            "prediction_stack": [
                {"template": "B69-R1-G1", "direction": "BUY", "confidence": 0.84, "summary": "continuation path"}
            ],
            "forward_projection": {
                "projected_candles": [
                    {"bbox": [518, 142, 530, 174]},
                    {"bbox": [542, 126, 554, 158]},
                    {"bbox": [566, 110, 578, 142]},
                ]
            },
        },
    }


def test_market_object_registry_v3_extracts_tracked_objects_and_overlays() -> None:
    registry = build_market_object_registry_v3(_sample_payload())
    payload = registry.as_dict()
    object_ids = {obj["object_id"] for obj in payload["object_registry"]}
    overlay_ids = {overlay["object_id"] for overlay in payload["overlay_objects"]}
    object_types = {obj["type"] for obj in payload["object_registry"]}

    assert payload["schema_version"] == TRACKER_SCHEMA_VERSION
    assert registry.status == "READY"
    assert overlay_ids == object_ids
    assert payload["object_count"] == len(payload["object_registry"])
    assert payload["sequence_context"]["tracked_objects"] == [obj["object_id"] for obj in payload["object_registry"]]
    assert payload["sequence_context"]["memory_matches"][0]["similarity"] == 0.91
    assert {
        "CURRENT_CANDLE",
        "IMPULSE_BOX",
        "PULLBACK_BOX",
        "CONTINUATION_BOX",
        "DEMAND_ZONE",
        "SUPPLY_ZONE",
        "SNIPER_ENTRY_BOX",
        "RETEST_BOX",
        "TARGET_ZONE_BOX",
        "INVALIDATION_BOX",
        "ANGLE_VECTOR",
        "PREDICTION_PATH",
    }.issubset(object_types)
    assert all(overlay["schema_version"] == OVERLAY_SCHEMA_VERSION for overlay in payload["overlay_objects"])
    assert all(overlay["bounds"] == overlay["bbox"] for overlay in payload["overlay_objects"])
    assert all(overlay["visible_modes"] for overlay in payload["overlay_objects"])


def test_market_object_registry_v3_ids_are_stable_when_geometry_moves() -> None:
    first = _sample_payload()
    second = deepcopy(first)
    second["frame_index"] = 622
    second["tracking_summary"]["tracked_candles"][-1]["bbox"] = [260, 214, 272, 258]
    second["tracking_summary"]["projection"]["zones"][1]["bbox"] = [430, 184, 516, 218]
    second["tracking_summary"]["projection"]["zones"][1]["target_bbox"] = [436, 126, 524, 158]

    first_registry = build_market_object_registry_v3(first)
    second_registry = build_market_object_registry_v3(second)

    first_payload = first_registry.as_dict()
    second_payload = second_registry.as_dict()
    first_by_source = {obj["source_path"]: obj["object_id"] for obj in first_payload["object_registry"]}
    second_by_source = {obj["source_path"]: obj["object_id"] for obj in second_payload["object_registry"]}
    assert second_by_source["tracking_summary.tracked_candles[7]"] == first_by_source["tracking_summary.tracked_candles[7]"]
    assert second_by_source["tracking_summary.projection.zones[1]"] == first_by_source["tracking_summary.projection.zones[1]"]
    assert second_by_source["tracking_summary.projection.zones[1].target_bbox"] == first_by_source["tracking_summary.projection.zones[1].target_bbox"]
    assert first_payload["sequence_context"]["sequence_signature"] != second_payload["sequence_context"]["sequence_signature"]


def test_market_object_tracker_v3_preserves_first_seen_frame() -> None:
    tracker = MarketObjectTrackerV3()
    first = _sample_payload()
    second = deepcopy(first)
    second["frame_index"] = 628
    second["tracking_summary"]["tracked_candles"][-1]["bbox"] = [266, 208, 278, 252]

    first_registry = tracker.build_registry(first)
    second_registry = tracker.build_registry(second)
    first_payload = first_registry.as_dict()
    second_payload = second_registry.as_dict()
    current_id = next(obj["object_id"] for obj in first_payload["object_registry"] if obj["type"] == "CURRENT_CANDLE")
    current_second = next(obj for obj in second_payload["object_registry"] if obj["object_id"] == current_id)

    assert current_second["first_seen_frame"] == 621
    assert current_second["last_seen_frame"] == 628


def test_market_object_registry_v3_degrades_with_explicit_missing_sources() -> None:
    registry = build_market_object_registry_v3({"session_id": "empty-live", "frame_index": 3})
    payload = registry.as_dict()

    assert registry.status == "MISSING_CRITICAL_SOURCE"
    assert registry.degraded is True
    assert payload["object_registry"] == []
    assert payload["overlay_objects"] == []
    assert "tracking_summary" in payload["missing_sources"]
    assert "tracking_summary.tracked_candles" in payload["missing_sources"]
    assert payload["source_status"]["tracking_summary.tracked_candles"] == "MISSING"
    assert payload["sequence_context"]["status"] == "MISSING_CRITICAL_SOURCE"
    assert payload["sequence_context"]["tracked_objects"] == []
