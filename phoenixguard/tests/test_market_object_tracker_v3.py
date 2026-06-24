from __future__ import annotations

from copy import deepcopy
from typing import Any

from phoenixguard.tracking.market_object_tracker_v3 import (
    OVERLAY_SCHEMA_VERSION,
    TRACKER_SCHEMA_VERSION,
    MarketObjectTrackerV3,
    derive_trendline_overlays,
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
                    "sniper_window": [300, 232, 382, 264],
                    "trigger_window": [318, 198, 404, 228],
                    "target_window": [360, 132, 448, 164],
                    "invalidation_y": 292,
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
                    "sniper_window": [348, 252, 388, 274],
                    "trigger_window": [362, 236, 408, 256],
                    "target_window": [396, 210, 426, 230],
                    "invalidation_y": 302,
                    "start_point": [338, 294],
                    "end_point": [426, 206],
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
        "INNER_TRENDLINE",
        "SNIPER_ENTRY_BOX",
        "RETEST_BOX",
        "TARGET_ZONE_BOX",
        "INVALIDATION_BOX",
        "ANGLE_VECTOR",
        "PREDICTION_PATH",
        "REPLAY_ENTRY",
        "REPLAY_EXIT",
    }.issubset(object_types)
    assert all(overlay["schema_version"] == OVERLAY_SCHEMA_VERSION for overlay in payload["overlay_objects"])
    assert all(overlay["bounds"] == overlay["bbox"] for overlay in payload["overlay_objects"])
    assert all(overlay["visible_modes"] for overlay in payload["overlay_objects"])
    trendline_overlays = [overlay for overlay in payload["overlay_objects"] if str(overlay.get("type", "")).endswith("_TRENDLINE")]
    assert trendline_overlays
    assert all(overlay.get("line_points") for overlay in trendline_overlays)
    assert {overlay.get("display_label") for overlay in trendline_overlays} == {"INNER TRENDLINE"}
    replay_labels = {overlay.get("display_label") for overlay in payload["overlay_objects"] if overlay.get("type") in {"REPLAY_ENTRY", "REPLAY_EXIT"}}
    assert {"WOULD HAVE ENTERED", "WOULD HAVE EXITED"}.issubset(replay_labels)
    source_paths = {obj["source_path"] for obj in payload["object_registry"]}
    assert "tracking_summary.structure_boxes[1].sniper_window" in source_paths
    assert "tracking_summary.structure_boxes[1].trigger_window" in source_paths
    assert "tracking_summary.structure_boxes[1].target_window" in source_paths
    assert "tracking_summary.structure_boxes[1].invalidation_y" in source_paths
    assert registry.sequence_context.sniper_entries
    assert registry.sequence_context.retest_tracks
    assert registry.sequence_context.target_zones
    assert registry.sequence_context.invalidation_zones


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


def test_trendline_derivation_rejects_horizontal_lines() -> None:
    horizontal_lows: list[dict[str, Any]] = [
        {"bbox": [10 + index * 20, 100, 20 + index * 20, 140], "center_x": 15 + index * 20, "center_y": 120}
        for index in range(8)
    ]

    assert not [row for row in derive_trendline_overlays(horizontal_lows) if row["type"].endswith("_TRENDLINE")]


def test_trendline_derivation_emits_valid_downtrend_resistance_only_when_clean() -> None:
    downtrend: list[dict[str, Any]] = [
        {
            "bbox": [10 + index * 42, 80 + index * 9, 24 + index * 42, 126 + index * 7],
            "center_x": 17 + index * 42,
            "center_y": 103 + index * 8,
        }
        for index in range(8)
    ]

    overlays = derive_trendline_overlays(downtrend)
    resistance = [row for row in overlays if row["type"] == "RESISTANCE_TRENDLINE"]

    assert resistance
    assert all(row["display_label"] == "RESISTANCE TRENDLINE" for row in resistance)
    assert all(row["line_obstruction_count"] == 0 for row in resistance)
    assert all(row["significant_close"] is False for row in resistance)


def test_trendline_derivation_uses_two_wick_anchors_before_extension() -> None:
    uptrend: list[dict[str, Any]] = []
    for index in range(8):
        left = 10 + index * 36
        wick_bottom = 220 - index * 10
        uptrend.append(
            {
                "bbox": [left, wick_bottom - 46, left + 10, wick_bottom - 16],
                "wick_top": wick_bottom - 58,
                "wick_bottom": wick_bottom,
                "center_x": left + 5,
                "center_y": wick_bottom - 31,
            }
        )

    overlays = derive_trendline_overlays(uptrend)
    trendline = next(row for row in overlays if row["type"].endswith("_TRENDLINE"))

    assert trendline["touch_count"] >= 2
    assert len(trendline["touch_points"]) >= 2
    assert trendline["line_points"][:2] == trendline["touch_points"][:2]
    assert trendline["anchor_candles"] == [0, 1]
    assert trendline["line_obstruction_count"] == 0
    assert trendline["significant_close"] is False
    assert trendline["trendline_validation"] == "wick_anchor_no_obstruction_no_significant_close"


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
