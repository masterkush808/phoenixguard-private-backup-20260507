from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from phoenixguard.mobile_api.live_state_v3 import build_live_state_v3
from phoenixguard.vision.broker_scene_graph_v3 import build_broker_scene_graph_v3
from phoenixguard.vision.box_refinement_v3 import resolve_precision_overlays_v3
from phoenixguard.vision.v3_overlay_contract import overlay_is_visible, rectangles_overlap


def _png(path: Path, size: tuple[int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=(10, 10, 10)).save(path)
    return path


def _session(tmp_path: Path) -> dict[str, Any]:
    window = _png(tmp_path / "window.png", (1938, 1038))
    chart = _png(tmp_path / "chart.png", (1434, 847))
    return {
        "session_id": "pocket-live-8788",
        "frame_index": 14494,
        "capture_count": 14494,
        "last_window_path": str(window),
        "last_chart_path": str(chart),
        "broker_surface": {
            "capture_plane": {"width": 1938, "height": 1038},
            "execution_boxes": {
                "buy_button": {"bbox": [1655, 474, 1813, 528]},
                "sell_button": {"bbox": [1655, 537, 1813, 591]},
            },
        },
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.02, 0.06, 0.76, 0.94]},
        "tracking_summary": {
            "focus_region": {"pixel_bbox": [39, 62, 1473, 976]},
            "chart_region": {"pixel_bbox": [0, 67, 1434, 914], "width": 1434, "height": 847},
            "display_region": {"pixel_bbox": [0, 67, 1434, 914], "width": 1434, "height": 847},
            "tracked_candles": [{"bbox": [1082, 553, 1091, 666], "direction": "SELL", "confidence": 0.96}],
            "structure_boxes": [
                {"key": "global", "label": "GLOBAL", "bbox": [807, 36, 1101, 820], "confidence": 0.95},
                {"key": "local", "label": "LOCAL", "bbox": [969, 36, 1101, 729], "confidence": 0.0},
            ],
            "support_resistance_zones": [
                {"key": "demand_a", "role": "support", "label": "DEMAND ZONE", "bbox": [528, 582, 1092, 670], "truth_score": 0.81},
                {"key": "demand_b", "role": "support", "label": "DEMAND ZONE", "bbox": [540, 588, 1096, 674], "truth_score": 0.72},
            ],
            "projection": {
                "direction": "SELL",
                "zones": [
                    {"kind": "sniper", "direction": "SELL", "label": "SNIPER ENTRY BOX", "bbox": [1087, 574, 1117, 616], "confidence": 0.83},
                    {
                        "kind": "primary",
                        "direction": "SELL",
                        "label": "CONTINUATION BOX",
                        "bbox": [1090, 646, 1120, 688],
                        "target_bbox": [1090, 764, 1120, 824],
                        "invalidation_y": 40,
                        "confidence": 0.86,
                    },
                ],
            },
        },
        "latest_signal": {"action": "SELL", "confidence": 0.9, "effective_confidence": 0.9},
    }


def test_broker_scene_graph_locks_plot_area_inside_full_window(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(
        session,
        artifacts={
            "window": {"path": session["last_window_path"], "width": 1938, "height": 1038},
            "chart": {"path": session["last_chart_path"], "width": 1434, "height": 847},
        },
    ).as_dict()["scene_graph"]

    assert scene["valid"] is True
    assert scene["broker_surface_bounds"] == [0.0, 0.0, 1938.0, 1038.0]
    assert scene["plot_area_bounds"][0] > scene["chart_region_bounds"][0]
    assert scene["plot_area_bounds"][1] > scene["chart_region_bounds"][1]
    assert scene["right_order_panel_bounds"][0] > scene["chart_region_bounds"][0]
    assert scene["plot_area_chart_bounds"][0] > 0


def test_precision_resolver_tightens_boxes_suppresses_duplicates_and_shortens_labels(tmp_path: Path) -> None:
    session = _session(tmp_path)
    state = build_live_state_v3(session)
    audit = state["overlay_precision_audit"]
    report = audit["precision_report"]
    overlays = state["overlay_objects"]
    clean = [row for row in overlays if row.get("visible_default") is not False and not row.get("precision_rejected")]
    labels = [row.get("display_label") for row in clean]

    assert report["unanchored_boxes"] == 0
    assert report["outside_plot_area"] == 0
    assert report["stale_frame_id"] == 0
    assert report["missing_transform"] == 0
    assert audit["rendered_count"] < audit["overlay_count"]
    assert "SNIPER SELL" in labels
    assert "SNIPER ENTRY BOX" not in labels
    assert "TARGET ZONE BOX" not in labels
    assert report["duplicate_boxes"] >= 1
    assert audit["rejected_count"] >= 1

    visible_labels = [row["label_bounds"]["bbox"] for row in clean if row.get("label_bounds", {}).get("bbox") and not row.get("label_hidden")]
    for index, first in enumerate(visible_labels):
        for second in visible_labels[index + 1 :]:
            assert rectangles_overlap(first, second, padding=2.0) is False


def test_live_state_respects_requested_granular_overlay_mode(tmp_path: Path) -> None:
    session = _session(tmp_path)
    state = build_live_state_v3(session, overlay_mode="TARGET")

    assert state["requested_mode"] == "TARGET"
    assert state["active_mode"] == "TARGET"
    assert state["overlay_mode"]["requested"] == "TARGET"
    assert state["overlay_mode"]["active"] == "TARGET"
    assert state["overlay_mode"]["visible_layers"] == state["visible_layers"]
    assert "target_zones" in state["visible_layers"]
    assert "invalidation" not in state["visible_layers"]
    assert "TARGET" in state["overlay_mode"]["available_modes"]
    assert state["renderable_count"] == len(state["overlay_objects"])
    assert state["overlay_layer_manager_v3"]["mode"] == "TARGET"
    assert state["overlay_layer_manager_v3"]["active_budget"] == 16
    assert all(
        row.get("layer") in {"target_zones", "supply_demand", "prediction_path"}
        for row in state["overlay_objects"]
    )


def test_precision_resolver_can_run_directly_on_overlay_contract_objects(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "target-1",
            "object_id": "target-1",
            "track_id": "target-1",
            "type": "TARGET_ZONE_BOX",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [10, 10, 400, 700],
            "truth_score": 0.8,
            "confidence": 0.8,
            "lifecycle_state": "ACTIVE",
            "visible_modes": ["CLEAN_LIVE", "DEBUG"],
            "ttl_ms": 30000,
            "reason": "oversized target must be tightened",
            "label": "TARGET ZONE BOX",
            "touch_points": [[245, 332], [272, 352]],
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(overlays, scene_graph=scene, current_side="SELL", frame_id=14494)

    assert audit["precision_report"]["outside_plot_area"] == 0
    assert audit["precision_report"]["missing_transform"] == 0
    assert resolved[0]["display_label"] == "TARGET"
    assert resolved[0]["bounds"][2] - resolved[0]["bounds"][0] < 300


def test_precision_resolver_assigns_display_state_and_visual_weight(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "sniper-buy-display",
            "object_id": "sniper-buy-display",
            "track_id": "sniper-buy-display",
            "type": "SNIPER_ENTRY_BOX",
            "side": "BUY",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [500, 300, 570, 348],
            "truth_score": 0.92,
            "confidence": 0.92,
            "visible_modes": ["CLEAN_LIVE", "TRIGGER", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "anchored sniper buy",
            "label": "SNIPER BUY",
            "parent_label": "local pullback",
            "touch_points": [[520, 326], [548, 332]],
        },
        {
            "overlay_id": "demand-context-display",
            "object_id": "demand-context-display",
            "track_id": "demand-context-display",
            "type": "DEMAND_ZONE",
            "side": "BUY",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [440, 410, 680, 500],
            "truth_score": 0.71,
            "confidence": 0.71,
            "visible_modes": ["CLEAN_LIVE", "SUPPLY_DEMAND", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "anchored demand context",
            "label": "DEMAND",
            "touch_points": [[520, 456], [560, 462]],
            "anchor_candles": [10, 11],
        },
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="BUY",
        frame_id=14494,
    )
    by_id = {row["overlay_id"]: row for row in resolved}

    assert audit["rendered_count"] == 2
    assert by_id["sniper-buy-display"]["display_state"] == "FULL"
    assert by_id["sniper-buy-display"]["visual_weight"] >= 0.95
    assert by_id["sniper-buy-display"]["geometry_visible"] is True
    assert by_id["sniper-buy-display"]["label_visible"] is True
    assert by_id["sniper-buy-display"]["style"]["label_mode"] == "full"
    assert by_id["demand-context-display"]["display_state"] in {"COMPACT", "NESTED"}
    assert by_id["demand-context-display"]["geometry_visible"] is True
    assert by_id["demand-context-display"]["inspector_visible"] is True
    assert "visible_label_count" in audit["precision_report"]


def test_crowded_valid_overlays_keep_geometry_when_labels_move_to_inspector(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = []
    for index in range(24):
        left = 60 + (index % 6) * 92
        top = 95 + (index // 6) * 72
        overlays.append(
            {
                "overlay_id": f"pullback-{index}",
                "object_id": f"pullback-{index}",
                "track_id": f"pullback-{index}",
                "type": "PULLBACK_BOX",
                "side": "BUY",
                "source_agent": "test",
                "frame_id": 14494,
                "sequence_id": "seq",
                "chart_transform_id": "ct",
                "coordinate_mode": "CHART_IMAGE_SPACE",
                "anchor_type": "BOX",
                "bounds": [left, top, left + 70, top + 44],
                "truth_score": 0.66,
                "confidence": 0.66,
                "visible_modes": ["CLEAN_LIVE", "LOCAL", "FULL_HISTORY_READ", "INSPECTOR"],
                "ttl_ms": 30000,
                "reason": "crowded pullback context",
                "label": "PULLBACK",
            }
        )

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="BUY",
        frame_id=14494,
    )
    inspector_only = [row for row in resolved if row.get("display_state") == "INSPECTOR_ONLY_LABEL"]

    assert audit["rendered_count"] == 24
    assert inspector_only
    assert all(row["geometry_visible"] is True for row in inspector_only)
    assert all(row["label_visible"] is False for row in inspector_only)
    assert audit["precision_report"]["inspector_only_label_count"] >= 1


def test_precision_resolver_rejects_floating_unanchored_live_zone(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "floating-zone",
            "object_id": "floating-zone",
            "track_id": "floating-zone",
            "type": "SUPPLY_ZONE",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [420, 220, 820, 330],
            "truth_score": 0.91,
            "confidence": 0.91,
            "visible_modes": ["CLEAN_LIVE", "SUPPLY_DEMAND", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "naked rectangle without wick, candle, parent, or source rule evidence",
            "label": "SUPPLY",
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="SELL",
        frame_id=14494,
    )

    assert audit["rendered_count"] == 0
    assert audit["precision_report"]["floating_unanchored_rejected"] == 1
    assert resolved[0]["precision_rejected"] is True
    assert resolved[0]["precision_rejection_reason"] == "floating_unanchored_overlay"
    assert "CLEAN_LIVE" not in resolved[0]["visible_modes"]


def test_precision_resolver_snaps_anchored_zone_to_touch_cluster(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "anchored-demand",
            "object_id": "anchored-demand",
            "track_id": "anchored-demand",
            "type": "DEMAND_ZONE",
            "side": "BUY",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [160, 440, 920, 610],
            "truth_score": 0.86,
            "confidence": 0.86,
            "visible_modes": ["CLEAN_LIVE", "SUPPLY_DEMAND", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "touch-supported demand zone",
            "label": "DEMAND",
            "touch_points": [[520, 522], [574, 528], [612, 518]],
            "anchor_candles": [12, 13, 14],
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="BUY",
        frame_id=14494,
    )
    row = resolved[0]

    assert audit["rendered_count"] == 1
    assert audit["precision_report"]["floating_unanchored_rejected"] == 0
    assert audit["precision_report"]["anchor_snap_refined"] == 1
    assert row.get("precision_rejected") is not True
    assert row["bounds"][0] >= 470
    assert row["bounds"][2] <= 665
    assert row["bounds"][1] <= 522 <= row["bounds"][3]


def test_precision_resolver_preserves_source_frame_before_stale_check(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "old-trigger",
            "object_id": "old-trigger",
            "track_id": "old-trigger",
            "type": "TRIGGER_ZONE_BOX",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 1,
            "sequence_id": "seq-old",
            "chart_transform_id": "ct-old",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [100, 100, 180, 150],
            "truth_score": 0.9,
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(overlays, scene_graph=scene, current_side="SELL", frame_id=14494)

    assert resolved[0]["frame_id"] == 1
    assert audit["precision_report"]["stale_frame_id"] == 1


def test_precision_resolver_nests_local_and_replay_children_inside_global_parent(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "global-1",
            "object_id": "global-1",
            "track_id": "global-1",
            "type": "IMPULSE_BOX",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [200, 150, 900, 720],
            "truth_score": 0.95,
            "confidence": 0.95,
            "lifecycle_state": "ACTIVE",
            "visible_modes": ["ACTIVE_CONTEXT", "FULL_HISTORY_READ", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "global parent",
            "label": "GLOBAL",
        },
        {
            "overlay_id": "local-1",
            "object_id": "local-1",
            "track_id": "local-1",
            "type": "PULLBACK_BOX",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [260, 300, 760, 640],
            "truth_score": 0.80,
            "confidence": 0.80,
            "lifecycle_state": "ACTIVE",
            "visible_modes": ["ACTIVE_CONTEXT", "FULL_HISTORY_READ", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "local child",
            "label": "LOCAL",
        },
        {
            "overlay_id": "replay-1",
            "object_id": "replay-1",
            "track_id": "replay-1",
            "type": "PROGRESSION_PATH",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [300, 420, 820, 680],
            "truth_score": 0.70,
            "confidence": 0.70,
            "lifecycle_state": "HISTORICAL",
            "visible_modes": ["REPLAY", "FULL_HISTORY_READ", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "replay child",
            "label": "REPLAY",
        },
    ]

    resolved, audit = resolve_precision_overlays_v3(overlays, scene_graph=scene, mode="ACTIVE_CONTEXT", current_side="SELL", frame_id=14494)
    by_id = {row["overlay_id"]: row for row in resolved}

    assert audit["precision_report"]["nested_overlays"] >= 2
    assert by_id["local-1"]["parent_overlay_id"] == "global-1"
    assert by_id["replay-1"]["parent_overlay_id"] == "global-1"
    assert by_id["global-1"]["child_overlay_ids"]
    assert by_id["local-1"]["nesting_depth"] == 1


def test_precision_resolver_clean_live_budget_does_not_suppress_active_context_counter_side(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "buy-sniper-counter-side",
            "object_id": "buy-sniper-counter-side",
            "track_id": "buy-sniper-counter-side",
            "type": "SNIPER_ENTRY_BOX",
            "side": "BUY",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [500, 300, 590, 342],
            "truth_score": 0.86,
            "confidence": 0.86,
            "lifecycle_state": "ACTIVE",
            "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "counter-side context should remain visible outside clean live",
            "label": "SNIPER BUY",
            "parent_label": "local pullback",
        }
    ]

    active, active_audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="ACTIVE_CONTEXT",
        current_side="SELL",
        frame_id=14494,
    )
    clean, clean_audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="SELL",
        frame_id=14494,
    )

    assert active_audit["rendered_count"] == 1
    assert active_audit["rejected_count"] == 0
    assert "ACTIVE_CONTEXT" in active[0]["visible_modes"]
    assert active[0].get("visible_default") is not False
    assert active[0].get("precision_rejected") is not True
    assert clean_audit["rendered_count"] == 0
    assert clean_audit["rejected_count"] == 1
    assert "CLEAN_LIVE" not in clean[0]["visible_modes"]
    assert clean[0]["visible_default"] is False


def test_precision_resolver_counts_replay_hidden_defaults_as_rendered(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "replay-path-hidden-default",
            "object_id": "replay-path-hidden-default",
            "track_id": "replay-path-hidden-default",
            "type": "PROGRESSION_PATH",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [420, 360, 720, 520],
            "truth_score": 0.74,
            "confidence": 0.74,
            "lifecycle_state": "HISTORICAL",
            "visible_modes": ["REPLAY", "FULL_HISTORY_READ", "INSPECTOR"],
            "visible_default": False,
            "ttl_ms": 30000,
            "reason": "replay context is hidden by default in clean live only",
            "label": "REPLAY",
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="REPLAY",
        current_side="SELL",
        frame_id=14494,
    )

    assert audit["rendered_count"] == 1
    assert audit["rejected_count"] == 0
    assert resolved[0]["visible_default"] is False
    assert "REPLAY" in resolved[0]["visible_modes"]
    assert resolved[0].get("precision_rejected") is not True


def test_no_duplicate_now_labels_in_clean_live_and_history_maps_to_replay(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays = [
        {
            "overlay_id": "current-live",
            "object_id": "current-live",
            "track_id": "current-live",
            "type": "CURRENT_CANDLE",
            "side": "SELL",
            "source_agent": "current_candle_tracker",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [1020, 400, 1040, 520],
            "truth_score": 0.96,
            "confidence": 0.96,
            "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT", "COUNCIL", "INSPECTOR"],
            "label": "NOW",
        },
        {
            "overlay_id": "current-duplicate",
            "object_id": "current-duplicate",
            "track_id": "current-duplicate",
            "type": "CURRENT_CANDLE",
            "side": "SELL",
            "source_agent": "current_candle_tracker",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [980, 390, 1000, 510],
            "truth_score": 0.82,
            "confidence": 0.82,
            "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT", "COUNCIL", "INSPECTOR"],
            "label": "NOW",
        },
        {
            "overlay_id": "historical-now",
            "object_id": "historical-now",
            "track_id": "historical-now",
            "type": "CURRENT_CANDLE",
            "side": "SELL",
            "source_agent": "historical_replay",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [650, 380, 670, 500],
            "truth_score": 0.74,
            "confidence": 0.74,
            "lifecycle_state": "HISTORICAL",
            "visible_modes": ["CLEAN_LIVE", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
            "label": "NOW",
        },
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="SELL",
        frame_id=14494,
    )
    live_now = [
        row
        for row in resolved
        if row.get("type") == "CURRENT_CANDLE"
        and row.get("display_label") == "NOW"
        and overlay_is_visible(row, "CLEAN_LIVE")
        and row.get("visible_default") is not False
    ]
    history_rows = [row for row in resolved if row.get("overlay_id") == "historical-now"]

    assert audit["precision_report"]["duplicate_now_hidden"] == 2
    assert len(live_now) == 1
    assert history_rows
    assert history_rows[0]["type"] == "PROGRESSION_PATH"
    assert history_rows[0]["display_label"] == "HISTORICAL PROGRESSION"
    assert overlay_is_visible(history_rows[0], "REPLAY") is True
    assert overlay_is_visible(history_rows[0], "CLEAN_LIVE") is False
