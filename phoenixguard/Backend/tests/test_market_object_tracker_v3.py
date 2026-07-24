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


def _canonical_candle(
    index: int,
    *,
    wick_top: float,
    wick_bottom: float,
    open_y: float | None = None,
    close_y: float | None = None,
    closed: bool = True,
    x_start: float = 10.0,
    x_step: float = 30.0,
    candle_width: float = 10.0,
) -> dict[str, Any]:
    """Build a detector-shaped candle with authoritative pixel geometry."""

    left = x_start + index * x_step
    center_x = left + candle_width * 0.5
    midpoint = (wick_top + wick_bottom) * 0.5
    resolved_open = midpoint + 4.0 if open_y is None else open_y
    resolved_close = midpoint - 4.0 if close_y is None else close_y
    return {
        "track_id": index + 1,
        # The bbox deliberately remains a transport envelope. Trendline price
        # anchors must come from the canonical wick/body fields below.
        "bbox": [left, wick_top, left + candle_width, wick_bottom],
        "center_x_px": center_x,
        "center_y_px": midpoint,
        "wick_top_px": wick_top,
        "wick_bottom_px": wick_bottom,
        "body_top_px": min(resolved_open, resolved_close),
        "body_bottom_px": max(resolved_open, resolved_close),
        "open_y_px": resolved_open,
        "close_y_px": resolved_close,
        "direction": "BUY" if resolved_close < resolved_open else "SELL",
        "is_closed": closed,
        "confidence": 0.82,
    }


def _confirmed_support_candles(*, latest_closed: bool = False) -> list[dict[str, Any]]:
    # Pivots 0, 3, and 6 sit exactly on y = 240 - 5 * bar. Other
    # candles remain above the support line in pixel-price space.
    wick_bottoms = [240.0, 220.0, 210.0, 225.0, 200.0, 190.0, 210.0, 185.0, 180.0, 170.0]
    return [
        _canonical_candle(
            index,
            wick_top=wick_bottom - 40.0,
            wick_bottom=wick_bottom,
            closed=latest_closed or index < len(wick_bottoms) - 1,
        )
        for index, wick_bottom in enumerate(wick_bottoms)
    ]


def _sample_payload() -> dict[str, Any]:
    return {
        "session_id": "pocket-live-8788",
        "frame_index": 621,
        "capture_count": 77,
        "tracking_summary": {
            "chart_valid": True,
            "visible_candle_count": 10,
            "chart_region": {"pixel_bbox": [0, 0, 960, 540], "width": 960, "height": 540},
            "display_region": {"pixel_bbox": [0, 0, 960, 540], "width": 960, "height": 540},
            "detected_timeframe": "M5",
            "global_direction": "BUY",
            "local_direction": "BUY",
            "impulse_direction": "BUY",
            "overlay_kind": "CONTINUATION BUY",
            "tracked_candles": [
                {
                    **candle,
                    "color": "green" if index % 3 != 1 else "magenta",
                    "price_proxy": 0.32 + index * 0.035,
                }
                for index, candle in enumerate(
                    [
                        _canonical_candle(
                            index,
                            wick_top=wick_bottom - 40.0,
                            wick_bottom=wick_bottom,
                            closed=index < 9,
                            x_start=100.0,
                            x_step=22.0,
                            candle_width=12.0,
                        )
                        for index, wick_bottom in enumerate(
                            [350.0, 326.0, 314.0, 332.0, 296.0, 284.0, 314.0, 278.0, 270.0, 260.0]
                        )
                    ]
                )
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
            "SUPPORT_TRENDLINE",
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
    assert {overlay.get("display_label") for overlay in trendline_overlays} == {"SUPPORT TRENDLINE"}
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


def test_registry_overlays_bind_confirmed_pair_timeframe_and_selector_identity() -> None:
    payload = _sample_payload()
    payload["tracking_summary"].update(
        {
            "detected_market": "GBP/USD OTC",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "market_selector_visual_fingerprint": "selector_v2_gbp_usd",
        }
    )
    payload["latest_signal"].update(
        {
            "market": "GBP/USD OTC",
            "focus_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "market_selector_visual_fingerprint": "selector_v2_gbp_usd",
        }
    )

    overlays = build_market_object_registry_v3(payload).as_dict()["overlay_objects"]

    assert overlays
    assert all(row["symbol"] == "GBP/USD OTC" for row in overlays)
    assert all(row["timeframe"] == "M5" for row in overlays)
    assert all(
        row["market_selector_visual_fingerprint"] == "selector_v2_gbp_usd"
        for row in overlays
    )
    assert all(row["instrument_identity_status"] == "LOCKED" for row in overlays)
    assert all(row["anchor_quality"]["matches_symbol_timeframe"] is True for row in overlays)
    assert all(row["anchor_quality"]["matches_selector_fingerprint"] is True for row in overlays)


def test_market_object_registry_turns_historical_progression_into_path_geometry() -> None:
    payload = deepcopy(_sample_payload())
    historical = payload["tracking_summary"]["historical_structure"]
    historical.append(
        {
            "key": "h11",
            "label": "H11 BUY",
            "story": "buyers lifted through prior structure",
            "bbox": [80, 120, 780, 420],
            "line_points": [[120, 390], [240, 340], [420, 260], [640, 190]],
            "start_point": [120, 390],
            "end_point": [640, 190],
            "direction": "BUY",
            "confidence": 0.78,
            "source_indices": [0, 1, 2, 3],
        }
    )

    registry = build_market_object_registry_v3(payload)
    history_rows = [
        overlay
        for overlay in registry.as_dict()["overlay_objects"]
        if overlay.get("type") == "PROGRESSION_PATH"
        and overlay.get("source_path") == "tracking_summary.historical_structure[1]"
    ]

    assert len(history_rows) == 1
    history = history_rows[0]
    assert history["anchor_type"] == "POLYGON"
    assert history["line_points"] == [[120.0, 390.0], [240.0, 340.0], [420.0, 260.0], [640.0, 190.0]]
    assert history["bounds"] == [120.0, 190.0, 640.0, 390.0]
    assert history["visible_default"] is True
    assert "CLEAN_LIVE" in history["visible_modes"]


def test_market_object_registry_skips_ambiguous_supply_demand_zone() -> None:
    payload = deepcopy(_sample_payload())
    payload["tracking_summary"]["support_resistance_zones"].append(
        {
            "key": "ambiguous_zone",
            "role": "",
            "label": "UNCLASSIFIED LIQUIDITY",
            "bbox": [280, 180, 520, 220],
            "confidence": 0.88,
        }
    )

    registry = build_market_object_registry_v3(payload)
    overlays = registry.as_dict()["overlay_objects"]

    assert not any(overlay.get("source_path") == "tracking_summary.support_resistance_zones[2]" for overlay in overlays)


def test_market_object_registry_maps_supply_demand_lifecycle_to_reference_state() -> None:
    payload = deepcopy(_sample_payload())
    payload["tracking_summary"]["support_resistance_zones"][0]["lifecycle_state"] = "BROKEN"
    payload["tracking_summary"]["support_resistance_zones"][0]["entry_authority_allowed"] = False
    payload["tracking_summary"]["execution_timing"]["opposing_force_zone"]["lifecycle_state"] = "BROKEN"

    registry = build_market_object_registry_v3(payload)
    overlays = registry.as_dict()["overlay_objects"]
    support = next(
        overlay
        for overlay in overlays
        if overlay.get("source_path") == "tracking_summary.support_resistance_zones[0]"
    )

    assert support["lifecycle_state"] == "BROKEN_REFERENCE"
    assert support["display_state"] == "GHOSTED"
    assert support["entry_authority_active"] is False
    assert not any(overlay.get("source_path") == "tracking_summary.execution_timing.opposing_force_zone" for overlay in overlays)


def test_market_object_registry_emits_only_hard_anchored_smart_money_geometry() -> None:
    payload = deepcopy(_sample_payload())
    payload["tracking_summary"]["support_resistance_zones"][0]["source_indices"] = [4, 5]
    payload["tracking_summary"]["smart_money_context"] = {
        "order_blocks": [
            {
                "type": "bullish_order_block",
                "direction": "BUY",
                "source_index": 4,
                "bbox": [184, 258, 208, 306],
                "confidence": 0.82,
            }
        ],
        "fair_value_gaps": [
            {
                "type": "bullish_fvg",
                "direction": "BUY",
                "source_index": 6,
                "bbox": [206, 246, 248, 274],
                "confidence": 0.78,
            }
        ],
        "liquidity_pools": [
            {"key": "support_5t", "direction": "BUY", "confidence": 0.76},
            {"key": "unresolved_pool", "bbox": [10, 10, 90, 40], "confidence": 0.99},
        ],
        "liquidity_sweeps": [
            {"zone_key": "support_5t", "direction": "BUY", "confidence": 0.84},
            {"zone_key": "unresolved_sweep", "bbox": [10, 10, 90, 40], "confidence": 0.99},
        ],
        "market_structure_shift": {
            "active": True,
            "direction": "BUY",
            "from": "SELL",
            "to": "BUY",
            "bbox": [228, 232, 270, 282],
            "anchor_candle_indices": [6, 7],
            "confidence": 0.8,
        },
    }

    overlays = build_market_object_registry_v3(payload).as_dict()["overlay_objects"]
    smart_money = [overlay for overlay in overlays if overlay.get("layer") == "smart_money"]
    by_type = {str(overlay["type"]): overlay for overlay in smart_money}

    assert set(by_type) == {
        "ORDER_BLOCK",
        "FAIR_VALUE_GAP",
        "LIQUIDITY_POOL",
        "LIQUIDITY_SWEEP",
        "MARKET_STRUCTURE_SHIFT",
    }
    assert by_type["ORDER_BLOCK"]["bounds"] == [184.0, 258.0, 208.0, 306.0]
    assert by_type["FAIR_VALUE_GAP"]["bounds"] == [206.0, 246.0, 248.0, 274.0]
    liquidity_pool = by_type["LIQUIDITY_POOL"]
    assert liquidity_pool["geometry_kind"] == "LIQUIDITY_PRICE_BAND"
    assert liquidity_pool["source_bbox"] == [210.0, 300.0, 520.0, 330.0]
    assert liquidity_pool["bounds"] == [187.4, 313.0, 222.6, 317.0]
    assert liquidity_pool["bounds"][3] - liquidity_pool["bounds"][1] <= 10.0
    assert liquidity_pool["bounds"][2] - liquidity_pool["bounds"][0] < 80.0
    assert liquidity_pool["touch_count"] == 2
    assert liquidity_pool["price_level_y"] == liquidity_pool["line_y"] == 315.0
    assert by_type["LIQUIDITY_SWEEP"]["bounds"] == [210.0, 300.0, 520.0, 330.0]
    assert by_type["MARKET_STRUCTURE_SHIFT"]["bounds"] == [228.0, 232.0, 270.0, 282.0]
    assert all(overlay["visible_modes"] == ["SMART_MONEY", "INSPECTOR"] for overlay in smart_money)
    assert all(overlay["anchor_evidence"]["valid"] is True for overlay in smart_money)
    assert all(overlay["anchor_candle_indices"] for overlay in smart_money)
    assert all(float(overlay["anchor_quality"]["score"]) >= 0.68 for overlay in smart_money)
    assert len([overlay for overlay in smart_money if overlay["type"] == "LIQUIDITY_POOL"]) == 1
    assert len([overlay for overlay in smart_money if overlay["type"] == "LIQUIDITY_SWEEP"]) == 1


def test_market_object_registry_rejects_single_touch_liquidity_level_as_pool() -> None:
    payload = deepcopy(_sample_payload())
    source_zone = payload["tracking_summary"]["support_resistance_zones"][0]
    source_zone.update(
        {
            "source_indices": [5],
            "touch_count": 1,
            "line_y": 315,
            "wick_anchor_y": 315,
            "touch_points": [[216, 315]],
        }
    )
    payload["tracking_summary"]["smart_money_context"] = {
        "liquidity_pools": [
            {"key": "support_5t", "direction": "BUY", "confidence": 0.91},
        ]
    }

    overlays = build_market_object_registry_v3(payload).as_dict()["overlay_objects"]

    assert not [row for row in overlays if row.get("type") == "LIQUIDITY_POOL"]


def test_market_object_registry_skips_state_only_or_unresolved_smart_money_rows() -> None:
    payload = deepcopy(_sample_payload())
    payload["tracking_summary"]["smart_money_context"] = {
        "order_blocks": [{"direction": "BUY", "source_index": 4, "confidence": 0.9}],
        "fair_value_gaps": [{"direction": "BUY", "source_index": 5, "confidence": 0.9}],
        "liquidity_pools": [{"key": "missing", "bbox": [180, 250, 220, 300], "confidence": 0.9}],
        "liquidity_sweeps": [{"zone_key": "missing", "bbox": [180, 250, 220, 300], "confidence": 0.9}],
        "market_structure_shift": {
            "active": True,
            "direction": "BUY",
            "confidence": 0.9,
        },
    }

    overlays = build_market_object_registry_v3(payload).as_dict()["overlay_objects"]

    assert not [overlay for overlay in overlays if overlay.get("layer") == "smart_money"]


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
    assert second_by_source["tracking_summary.tracked_candles[9]"] == first_by_source["tracking_summary.tracked_candles[9]"]
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
    # Lower-high pivots 0, 3, and 6 sit on y = 80 + 6 * bar.
    wick_tops = [80.0, 100.0, 108.0, 98.0, 126.0, 134.0, 116.0, 142.0, 148.0, 154.0]
    downtrend = [
        _canonical_candle(
            index,
            wick_top=wick_top,
            wick_bottom=wick_top + 40.0,
            closed=index < len(wick_tops) - 1,
            x_step=42.0,
        )
        for index, wick_top in enumerate(wick_tops)
    ]

    overlays = derive_trendline_overlays(downtrend)
    resistance = [row for row in overlays if row["type"] == "RESISTANCE_TRENDLINE"]

    assert resistance
    assert all(row["display_label"] == "RESISTANCE TRENDLINE" for row in resistance)
    assert all(row["line_obstruction_count"] == 0 for row in resistance)
    assert all(row["significant_close"] is False for row in resistance)


def test_trendline_derivation_uses_two_wick_anchors_before_extension() -> None:
    uptrend = _confirmed_support_candles()
    # Prove the outer bbox cannot displace canonical wick coordinates.
    uptrend[0]["bbox"] = [10.0, 150.0, 20.0, 255.0]
    overlays = derive_trendline_overlays(uptrend)
    trendline = next(row for row in overlays if row["type"] == "SUPPORT_TRENDLINE")

    assert trendline["touch_count"] >= 2
    assert len(trendline["touch_points"]) >= 2
    assert trendline["line_points"][:2] == trendline["anchor_wick_points"]
    assert trendline["anchor_candles"] == [0, 6]
    assert trendline["anchor_candle_indices"] == [0, 6]
    assert trendline["anchor_wick_points"] == [[15.0, 240.0], [195.0, 210.0]]
    assert trendline["line_points"][-1][0] == uptrend[-1]["center_x_px"]
    assert trendline["anchor_span_bars"] == 6
    assert trendline["anchor_span_fraction"] > 0.6
    assert trendline["line_obstruction_count"] == 0
    assert trendline["significant_close"] is False
    assert trendline["trendline_validation"] == "wick_anchor_no_obstruction_closed_body_validation"
    assert trendline["anchor_type"] == "TRENDLINE_TOUCH_POINTS"


def test_outer_local_pivots_outrank_short_line_with_repeated_wick_probes() -> None:
    wick_tops = [210.0, 205.0, 198.0, 212.0, 188.0, 196.0, 178.0, 190.0, 170.0, 182.0, 160.0, 172.0]
    wick_bottoms = [430.0, 390.0, 360.0, 382.0, 340.0, 354.0, 320.0, 338.0, 300.0, 318.0, 280.0, 298.0]
    candles: list[dict[str, Any]] = []
    for index, (wick_top, wick_bottom) in enumerate(zip(wick_tops, wick_bottoms)):
        center_x = 100.0 + index * 40.0
        body_top = wick_top + 18.0
        body_bottom = wick_bottom - 18.0
        candles.append(
            {
                "bbox": [center_x - 4.0, body_top, center_x + 4.0, body_bottom],
                "body_bbox": [center_x - 4.0, body_top, center_x + 4.0, body_bottom],
                "wick_top": wick_top,
                "wick_bottom": wick_bottom,
                "center_x": center_x,
                "center_y": (wick_top + wick_bottom) * 0.5,
            }
        )

    trendline = next(
        row for row in derive_trendline_overlays(candles) if row["type"] == "INNER_TRENDLINE"
    )

    assert trendline["anchor_candle_indices"] == [0, 9]
    assert trendline["line_points"][:2] == [[100.0, 430.0], [460.0, 318.0]]
    assert trendline["wick_probe_count"] == 0


def test_trendline_derivation_rejects_ranked_extremes_without_real_pivots() -> None:
    monotonic = [
        _canonical_candle(
            index,
            wick_top=180.0 - index * 5.0,
            wick_bottom=220.0 - index * 5.0,
            closed=index < 7,
        )
        for index in range(8)
    ]

    assert derive_trendline_overlays(monotonic) == []


def test_trendline_derivation_rejects_adjacent_or_immaterial_pivot_span() -> None:
    wick_bottoms = [200.0, 218.0, 230.0, 210.0, 225.0, 205.0, 212.0, 235.0]
    candles = [
        _canonical_candle(
            index,
            wick_top=100.0,
            wick_bottom=wick_bottom,
            closed=index < len(wick_bottoms) - 1,
        )
        for index, wick_bottom in enumerate(wick_bottoms)
    ]

    support = [row for row in derive_trendline_overlays(candles) if row["trendline_role"] == "support"]
    assert support == []


def test_trendline_wick_probe_survives_but_closed_body_breach_invalidates() -> None:
    # Two closed bars to the right confirm the second anchor at index 6;
    # later wicks may probe below it without rewriting that pivot.
    wick_bottoms = [
        240.0,
        220.0,
        210.0,
        225.0,
        200.0,
        190.0,
        210.0,
        185.0,
        180.0,
        220.0,
        218.0,
        225.0,
    ]
    probe = [
        _canonical_candle(
            index,
            wick_top=wick_bottom - 40.0,
            wick_bottom=wick_bottom,
            closed=index < len(wick_bottoms) - 1,
        )
        for index, wick_bottom in enumerate(wick_bottoms)
    ]
    active_support = next(
        row for row in derive_trendline_overlays(probe) if row["type"] == "SUPPORT_TRENDLINE"
    )
    assert active_support["wick_probe_count"] >= 1
    assert active_support["significant_close"] is False

    breached = deepcopy(probe)
    breached[9]["open_y_px"] = 200.0
    breached[9]["close_y_px"] = 218.0
    breached[9]["body_top_px"] = 200.0
    breached[9]["body_bottom_px"] = 218.0
    breached_support = [
        row for row in derive_trendline_overlays(breached) if row["type"] == "SUPPORT_TRENDLINE"
    ]
    assert breached_support == []


def test_forming_candle_cannot_invalidate_confirmed_trendline() -> None:
    wick_bottoms = [240.0, 220.0, 210.0, 225.0, 200.0, 190.0, 210.0, 185.0, 180.0, 230.0]
    candles = [
        _canonical_candle(
            index,
            wick_top=wick_bottom - (60.0 if index == len(wick_bottoms) - 1 else 40.0),
            wick_bottom=wick_bottom,
            open_y=205.0 if index == len(wick_bottoms) - 1 else None,
            close_y=225.0 if index == len(wick_bottoms) - 1 else None,
            closed=index < len(wick_bottoms) - 1,
        )
        for index, wick_bottom in enumerate(wick_bottoms)
    ]

    support = next(
        row for row in derive_trendline_overlays(candles) if row["type"] == "SUPPORT_TRENDLINE"
    )
    assert support["significant_close"] is False
    assert support["wick_probe_count"] >= 1


def test_trendline_touch_strength_distinguishes_developing_and_confirmed() -> None:
    developing_bottoms = [240.0, 220.0, 210.0, 215.0, 200.0, 190.0, 210.0, 185.0, 180.0, 170.0]
    developing = [
        _canonical_candle(
            index,
            wick_top=wick_bottom - 40.0,
            wick_bottom=wick_bottom,
            closed=index < len(developing_bottoms) - 1,
        )
        for index, wick_bottom in enumerate(developing_bottoms)
    ]
    developing_line = next(
        row for row in derive_trendline_overlays(developing) if row["type"] == "SUPPORT_TRENDLINE"
    )
    confirmed_line = next(
        row
        for row in derive_trendline_overlays(_confirmed_support_candles())
        if row["type"] == "SUPPORT_TRENDLINE"
    )

    assert developing_line["touch_count"] == 2
    assert developing_line["confirmation_state"] == "DEVELOPING"
    assert developing_line["touch_quality"] == "DEVELOPING"
    assert confirmed_line["touch_count"] >= 3
    assert confirmed_line["confirmation_state"] == "CONFIRMED"
    assert confirmed_line["touch_quality"] == "CONFIRMED"


def test_trendline_derivation_rejects_excessive_normalized_steepness() -> None:
    wick_bottoms = [600.0, 530.0, 480.0, 450.0, 380.0, 330.0, 300.0, 250.0, 200.0, 150.0]
    candles = [
        _canonical_candle(
            index,
            wick_top=wick_bottom - 40.0,
            wick_bottom=wick_bottom,
            closed=index < len(wick_bottoms) - 1,
        )
        for index, wick_bottom in enumerate(wick_bottoms)
    ]

    assert not [row for row in derive_trendline_overlays(candles) if row["trendline_role"] == "support"]


def test_equivalent_major_and_inner_trendlines_are_not_duplicated() -> None:
    overlays = derive_trendline_overlays(_confirmed_support_candles())
    support = [row for row in overlays if row["trendline_role"] == "support"]

    assert len(support) == 1
    assert support[0]["type"] == "SUPPORT_TRENDLINE"
    assert not [row for row in overlays if row["type"] == "INNER_TRENDLINE" and row["trendline_role"] == "support"]


def test_registry_preserves_trendline_touch_point_anchor_geometry() -> None:
    registry = build_market_object_registry_v3(_sample_payload())
    payload = registry.as_dict()
    trendlines = [
        overlay
        for overlay in payload["overlay_objects"]
        if str(overlay.get("type", "")).endswith("_TRENDLINE")
    ]

    assert trendlines
    for trendline in trendlines:
        assert trendline["anchor_type"] == "TRENDLINE_TOUCH_POINTS"
        assert len(trendline["line_points"]) >= 2
        assert len(trendline["trendline_touch_points"]) >= 2
        assert trendline["anchor_evidence_status"] == "VALID"


def test_registry_prefers_published_validated_trendlines_over_fresh_derivation() -> None:
    payload = _sample_payload()
    published_points = [[120.0, 210.0], [300.0, 250.0]]
    payload["tracking_summary"]["trendlines_v3"] = [
        {
            "type": "RESISTANCE_TRENDLINE",
            "role": "resistance_trendline",
            "trendline_role": "resistance",
            "label": "RESISTANCE TRENDLINE",
            "direction": "SELL",
            "bounds": [120.0, 210.0, 300.0, 250.0],
            "points": published_points,
            "line_points": published_points,
            "touch_points": published_points,
            "anchor_wick_points": published_points,
            "anchor_candles": [1, 8],
            "anchor_type": "TRENDLINE_TOUCH_POINTS",
            "trendline_validation": "wick_anchor_no_obstruction_closed_body_validation",
            "confidence": 0.91,
            "lifecycle_state": "ACTIVE",
        }
    ]

    registry = build_market_object_registry_v3(payload).as_dict()
    trendlines = [
        row
        for row in registry["overlay_objects"]
        if str(row.get("type", "")).endswith("_TRENDLINE")
    ]

    assert [row["type"] for row in trendlines] == ["RESISTANCE_TRENDLINE"]
    assert trendlines[0]["line_points"] == published_points
    assert trendlines[0]["trendline_touch_points"] == published_points
    assert trendlines[0]["source_path"] == "tracking_summary.trendlines_v3[0]"


def test_market_object_tracker_prefers_explicit_source_indices_for_history() -> None:
    registry = build_market_object_registry_v3(_sample_payload())
    payload = registry.as_dict()
    history_rows = [
        overlay
        for overlay in payload["overlay_objects"]
        if str(overlay.get("source_path", "")).startswith("tracking_summary.historical_structure[0]")
    ]

    assert history_rows
    assert all(row["anchor_candle_indices"] == [4, 5] for row in history_rows)


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
