from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from phoenixguard.mobile_api.live_state_v3 import build_live_state_v3
from phoenixguard.tracking.market_object_tracker_v3 import build_v3_overlays_from_session
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


def _install_visible_candles(session: dict[str, Any], count: int = 8) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for index in range(count):
        left = 660 + index * 24
        right = left + 10
        wick_top = 470 - (index % 3) * 9
        wick_bottom = 610 + (index % 4) * 10
        body_top = wick_top + 18 + (index % 2) * 5
        body_bottom = wick_bottom - 14 - (index % 3) * 4
        direction = "BUY" if index % 2 else "SELL"
        candles.append(
            {
                "index": index,
                "track_id": f"visible-candle-{index}",
                "bbox": [left, body_top, right, body_bottom],
                "wick_top": wick_top,
                "wick_bottom": wick_bottom,
                "center_x": (left + right) / 2,
                "center_y": (body_top + body_bottom) / 2,
                "direction": direction,
                "confidence": 0.91,
            }
        )
    session["tracking_summary"]["tracked_candles"] = candles
    session["tracking_summary"]["visible_candle_count"] = count
    return candles


def _trendline_candle(index: int, center_x: float, wick_top: float, wick_bottom: float) -> dict[str, Any]:
    body_top = wick_top + 18.0
    body_bottom = wick_bottom - 18.0
    return {
        "index": index,
        "track_id": f"trendline-candle-{index}",
        "bbox": [center_x - 4.0, body_top, center_x + 4.0, body_bottom],
        "body_bbox": [center_x - 4.0, body_top, center_x + 4.0, body_bottom],
        "wick_top": wick_top,
        "wick_bottom": wick_bottom,
        "center_x": center_x,
        "center_y": (wick_top + wick_bottom) / 2.0,
        "direction": "BUY",
        "confidence": 0.93,
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


def test_precision_resolver_keeps_overlapping_major_and_inner_trendlines_visible() -> None:
    scene_graph: dict[str, Any] = {
        "frame_id": 88,
        "plot_area_chart_bounds": [0, 0, 800, 500],
        "chart_region_chart_bounds": [0, 0, 800, 500],
    }
    shared_points = [[160, 210], [520, 280]]
    base_overlay: dict[str, Any] = {
        "source_agent": "market_object_tracker_v3",
        "frame_id": 88,
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "chart_transform_id": "chart-88",
        "truth_score": 0.82,
        "confidence": 0.82,
        "lifecycle_state": "ACTIVE",
        "visible_modes": ["CLEAN_LIVE", "TRENDLINES", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
        "visible_default": True,
    }
    overlays: list[dict[str, Any]] = [
        {
            **base_overlay,
            "overlay_id": "major-structure-parent",
            "object_id": "major-structure-parent",
            "track_id": "major-structure-parent",
            "type": "IMPULSE_BOX",
            "label": "IMPULSE",
            "bounds": [100, 120, 680, 390],
            "anchor_type": "BOX",
            "anchor_candles": [1, 5],
        },
        {
            **base_overlay,
            "overlay_id": "major-resistance-line",
            "object_id": "major-resistance-line",
            "track_id": "major-resistance-line",
            "type": "RESISTANCE_TRENDLINE",
            "label": "RESISTANCE TRENDLINE",
            "bounds": [160, 210, 520, 280],
            "line_points": shared_points,
            "anchor_type": "LINE",
            "anchor_candles": [2, 6],
        },
        {
            **base_overlay,
            "overlay_id": "inner-resistance-line",
            "object_id": "inner-resistance-line",
            "track_id": "inner-resistance-line",
            "type": "INNER_TRENDLINE",
            "label": "INNER TRENDLINE",
            "bounds": [160, 210, 520, 280],
            "line_points": shared_points,
            "anchor_type": "LINE",
            "anchor_candles": [2, 6],
        },
    ]

    rows, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene_graph,
        mode="CLEAN_LIVE",
        current_side="SELL",
        frame_id=88,
    )

    trendlines = {row["type"]: row for row in rows if row.get("type") in {"RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}}
    assert set(trendlines) == {"RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}
    assert trendlines["INNER_TRENDLINE"]["visible_default"] is True
    assert "CLEAN_LIVE" in trendlines["INNER_TRENDLINE"]["visible_modes"]
    assert trendlines["INNER_TRENDLINE"]["display_state"] != "INSPECTOR_ONLY_LABEL"
    assert "trendline_sibling_overlap_kept" in trendlines["INNER_TRENDLINE"]["precision_flags"]
    assert trendlines["INNER_TRENDLINE"]["line_points"] == shared_points
    assert trendlines["RESISTANCE_TRENDLINE"]["line_points"] == shared_points
    assert audit["rendered_count"] >= 3


def test_market_object_tracker_preserves_trendline_wick_touch_points() -> None:
    candles = [
        _trendline_candle(0, 100.0, 210.0, 430.0),
        _trendline_candle(1, 140.0, 205.0, 390.0),
        _trendline_candle(2, 180.0, 198.0, 360.0),
        _trendline_candle(3, 220.0, 212.0, 382.0),
        _trendline_candle(4, 260.0, 188.0, 340.0),
        _trendline_candle(5, 300.0, 196.0, 354.0),
        _trendline_candle(6, 340.0, 178.0, 320.0),
        _trendline_candle(7, 380.0, 190.0, 338.0),
        _trendline_candle(8, 420.0, 170.0, 300.0),
        _trendline_candle(9, 460.0, 182.0, 318.0),
        _trendline_candle(10, 500.0, 160.0, 280.0),
        _trendline_candle(11, 540.0, 172.0, 298.0),
    ]
    overlays = build_v3_overlays_from_session(
        {
            "session_id": "precision-trendline",
            "frame_index": 10,
            "tracking_summary": {"tracked_candles": candles},
            "latest_signal": {"action": "BUY"},
        }
    )
    trendline = next(row for row in overlays if row.get("type") == "INNER_TRENDLINE")
    expected_anchor_points = [[100.0, 430.0], [460.0, 318.0]]

    assert trendline["line_points"][:2] == expected_anchor_points
    for point in expected_anchor_points:
        assert point in trendline["touch_points"]
        assert point in trendline["anchor_evidence"]["touch_points"]
    assert 0 in trendline["anchor_candles"]
    assert 9 in trendline["anchor_candles"]
    assert len(trendline["touch_points"]) >= 2


def test_precision_resolver_keeps_support_resistance_and_opposing_force_families_visible() -> None:
    scene_graph: dict[str, Any] = {
        "frame_id": 89,
        "plot_area_chart_bounds": [0, 0, 900, 540],
        "chart_region_chart_bounds": [0, 0, 900, 540],
    }
    base_overlay: dict[str, Any] = {
        "source_agent": "market_object_tracker_v3",
        "frame_id": 89,
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "chart_transform_id": "chart-89",
        "truth_score": 0.78,
        "confidence": 0.78,
        "lifecycle_state": "ACTIVE",
        "visible_modes": ["CLEAN_LIVE", "SUPPLY_DEMAND", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "INSPECTOR"],
        "visible_default": True,
        "anchor_type": "BOX",
        "anchor_candles": [2, 4],
        "still_significant": True,
    }
    shared_bounds = [240, 220, 560, 285]
    overlays: list[dict[str, Any]] = [
        {
            **base_overlay,
            "overlay_id": "resistance-zone",
            "object_id": "resistance-zone",
            "track_id": "resistance-zone",
            "type": "SUPPLY_ZONE",
            "role": "resistance",
            "label": "SUPPLY",
            "bounds": shared_bounds,
        },
        {
            **base_overlay,
            "overlay_id": "support-zone",
            "object_id": "support-zone",
            "track_id": "support-zone",
            "type": "DEMAND_ZONE",
            "role": "support",
            "label": "DEMAND",
            "bounds": shared_bounds,
        },
        {
            **base_overlay,
            "overlay_id": "opposing-force-zone",
            "object_id": "opposing-force-zone",
            "track_id": "opposing-force-zone",
            "type": "OPPOSING_FORCE",
            "role": "opposing_force_zone",
            "label": "OPPOSING FORCE",
            "bounds": shared_bounds,
        },
    ]

    rows, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene_graph,
        mode="CLEAN_LIVE",
        current_side="SELL",
        frame_id=89,
    )

    zones = {row["type"]: row for row in rows if row.get("type") in {"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"}}
    assert set(zones) == {"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"}
    assert zones["SUPPLY_ZONE"]["visible_default"] is True
    assert zones["DEMAND_ZONE"]["visible_default"] is True
    assert zones["OPPOSING_FORCE"]["visible_default"] is True
    assert all(row.get("precision_rejection_reason") != "duplicate_weaker_track" for row in zones.values())
    assert audit["precision_report"]["duplicate_boxes"] == 0


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


def test_candles_mode_renders_every_visible_candle_box(tmp_path: Path) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=8)

    state = build_live_state_v3(session, overlay_mode="CANDLES", now_epoch=120.0)
    candle_overlays = [row for row in state["overlay_objects"] if row.get("type") == "CURRENT_CANDLE"]

    assert state["active_mode"] == "CANDLES"
    assert state["overlay_layer_manager_v3"]["active_budget"] == 120
    assert len(candle_overlays) == len(candles)
    assert state["reason_if_empty"] == ""
    assert all(row.get("layer") == "recent_candles" for row in candle_overlays)
    assert all(row.get("label_hidden") is True for row in candle_overlays)
    assert all(row.get("geometry_visible") is not False for row in candle_overlays)
    assert all(row.get("bounds_rect", {}).get("exists") is True for row in candle_overlays)
    assert {tuple(row.get("anchor_candles") or []) for row in candle_overlays} == {
        (index,) for index in range(len(candles))
    }


def test_two_candle_and_lstm_modes_render_anchored_study_overlays(tmp_path: Path) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=8)
    session["latest_signal"].update(
        {
            "two_candle_study": {
                "schema_version": "PG_TWO_CANDLE_STUDY_V3",
                "display_as": "TEXT_AND_BANDS_ONLY",
                "do_not_render_synthetic_candles": True,
                "summary": "Study anchored to the latest two visible candles.",
                "confidence": 0.66,
                "side": "SELL",
            },
            "lstm_contribution": {
                "schema_version": "PG_LSTM_CANDLE_SEQUENCE_CONTRIBUTION_V3",
                "skill": "LSTM_CANDLE_SEQUENCE",
                "fresh": True,
                "blocker": False,
                "contribution": 0.48,
                "side": "SELL",
            },
        }
    )

    two_candle_state = build_live_state_v3(session, overlay_mode="TWO_CANDLE_STUDY", now_epoch=120.0)
    two_candle_overlays = [row for row in two_candle_state["overlay_objects"] if row.get("type") == "TWO_CANDLE_STUDY"]
    assert two_candle_state["active_mode"] == "TWO_CANDLE_STUDY"
    assert len(two_candle_overlays) == 1
    assert two_candle_overlays[0]["anchor_candles"] == [len(candles) - 2, len(candles) - 1]
    assert two_candle_overlays[0]["bounds_rect"]["exists"] is True
    assert two_candle_overlays[0]["layer"] == "active_council_decision"

    lstm_state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    lstm_overlays = [row for row in lstm_state["overlay_objects"] if row.get("type") == "LSTM_STUDY"]
    assert lstm_state["active_mode"] == "LSTM_STUDY"
    assert len(lstm_overlays) == 1
    assert lstm_overlays[0]["anchor_candles"] == list(range(len(candles)))
    assert lstm_overlays[0]["bounds_rect"]["exists"] is True
    assert lstm_overlays[0]["layer"] == "active_council_decision"


def test_council_mode_renders_active_marker_from_chart_context(tmp_path: Path) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=5)
    session["model_council_result"] = {
        "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
        "model_council": {
            "final_state": "WATCHING",
            "final_side": "SELL",
            "arbitration_reason": "wait for wick retest confirmation",
        },
        "promotion_trace": {"next_required": "latest candle retest"},
    }

    state = build_live_state_v3(session, overlay_mode="COUNCIL", now_epoch=120.0)
    council_markers = [row for row in state["overlay_objects"] if row.get("type") == "MODEL_COUNCIL_MARKER"]

    assert state["active_mode"] == "COUNCIL"
    assert state["reason_if_empty"] == ""
    assert len(council_markers) == 1
    assert council_markers[0]["layer"] == "active_council_decision"
    assert council_markers[0]["anchor_candles"] == [len(candles) - 1]
    assert council_markers[0]["bounds_rect"]["exists"] is True


def test_broker_mode_emits_locked_control_overlays_on_broker_surface(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session["broker_surface"]["execution_boxes"].update(
        {
            "order_panel": {"bbox": [1620, 180, 1840, 610], "confidence": 0.94, "locked": True},
            "time_field": {"bbox": [1655, 210, 1813, 255], "confidence": 0.91, "locked": True},
            "amount_field": {"bbox": [1655, 286, 1813, 330], "confidence": 0.88, "locked": True},
        }
    )

    broker_state = build_live_state_v3(session, overlay_mode="BROKER", now_epoch=110.0)
    clean_state = build_live_state_v3(session, overlay_mode="CLEAN_LIVE", now_epoch=110.0)
    labels = {row["display_label"] for row in broker_state["overlay_objects"]}
    source_keys = {row["source_key"] for row in broker_state["overlay_objects"]}

    assert broker_state["renderable_count"] >= 6
    assert all(row["type"] == "BROKER_CONTROL" for row in broker_state["overlay_objects"])
    assert all(row["layer"] == "broker_controls" for row in broker_state["overlay_objects"])
    assert all(row["coordinate_mode"] == "FULL_BROKER_SURFACE" for row in broker_state["overlay_objects"])
    assert {
        "BROKER SURFACE",
        "RIGHT ORDER PANEL",
        "TIME BUTTON",
        "AMOUNT FIELD",
        "BUY BUTTON",
        "SELL BUTTON",
    }.issubset(labels)
    assert {"broker_screen", "right_order_panel", "time_button", "amount_field", "buy_icon", "sell_icon"}.issubset(source_keys)
    assert broker_state["overlay_vocabulary"]["dictionary_coverage_ok"] is True
    assert broker_state["unknown_or_unmapped_terms"] == []
    buy = next(row for row in broker_state["overlay_objects"] if row["source_key"] == "buy_icon")
    assert buy["label_bounds"]["left"] >= 1600
    assert all(row["type"] != "BROKER_CONTROL" for row in clean_state["overlay_objects"])


def test_precision_resolver_can_run_directly_on_overlay_contract_objects(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
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
    overlays: list[dict[str, Any]] = [
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
    overlays: list[dict[str, Any]] = []
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
    overlays: list[dict[str, Any]] = [
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


def test_precision_resolver_rejects_metadata_only_live_zone(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
        {
            "overlay_id": "metadata-zone",
            "object_id": "metadata-zone",
            "track_id": "metadata-zone",
            "type": "DEMAND_ZONE",
            "side": "BUY",
            "source_agent": "market_object_tracker_v3",
            "source_path": "tracking_summary.support_resistance_zones[0]",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [420, 420, 820, 500],
            "truth_score": 0.88,
            "confidence": 0.88,
            "visible_modes": ["CLEAN_LIVE", "SUPPLY_DEMAND", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "metadata should not promote a naked zone",
            "label": "DEMAND",
            "structural_anchor": True,
            "zone_family": "DEMAND_ZONE",
            "source_rule": "support_reclaim",
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="BUY",
        frame_id=14494,
    )

    assert audit["rendered_count"] == 0
    assert audit["precision_report"]["floating_unanchored_rejected"] == 1
    assert resolved[0]["precision_rejection_reason"] == "metadata_only_anchor"
    assert resolved[0]["precision_rejected"] is True


def test_precision_resolver_rejects_parent_only_actionable_child(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
        {
            "overlay_id": "parent-impulse",
            "object_id": "parent-impulse",
            "track_id": "parent-impulse",
            "type": "IMPULSE_BOX",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [280, 220, 760, 520],
            "truth_score": 0.72,
            "confidence": 0.72,
            "visible_modes": ["ACTIVE_CONTEXT", "GLOBAL", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "parent context",
            "label": "IMPULSE",
            "anchor_candles": [8, 12],
        },
        {
            "overlay_id": "child-sniper-parent-only",
            "object_id": "child-sniper-parent-only",
            "track_id": "child-sniper-parent-only",
            "type": "SNIPER_ENTRY_BOX",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [500, 340, 590, 382],
            "truth_score": 0.86,
            "confidence": 0.86,
            "visible_modes": ["ACTIVE_CONTEXT", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "parent-only actionable child must not render",
            "label": "SNIPER SELL",
            "parent_label": "parent impulse",
        },
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="ACTIVE_CONTEXT",
        current_side="SELL",
        frame_id=14494,
    )
    by_id = {row["overlay_id"]: row for row in resolved}

    assert audit["rendered_count"] == 1
    assert audit["precision_report"]["floating_unanchored_rejected"] == 1
    assert by_id["child-sniper-parent-only"]["precision_rejection_reason"] == "parent_only_anchor"
    assert by_id["child-sniper-parent-only"]["precision_rejected"] is True


def test_precision_resolver_rejects_line_level_without_touch_evidence(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
        {
            "overlay_id": "line-only-supply",
            "object_id": "line-only-supply",
            "track_id": "line-only-supply",
            "type": "SUPPLY_ZONE",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [420, 220, 820, 300],
            "line_y": 250,
            "line_x0": 420,
            "line_x1": 820,
            "truth_score": 0.88,
            "confidence": 0.88,
            "visible_modes": ["CLEAN_LIVE", "SUPPLY_DEMAND", "INSPECTOR"],
            "ttl_ms": 30000,
            "reason": "line level needs touch evidence",
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
    assert resolved[0]["precision_rejection_reason"] == "line_level_without_touch_evidence"
    assert resolved[0]["precision_rejected"] is True


def test_precision_resolver_snaps_anchored_zone_to_touch_cluster(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
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


def test_tracker_snaps_supply_demand_to_recent_visible_touch_cluster(tmp_path: Path) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=12)
    recent_touch_points = [
        [candles[7]["center_x"], candles[7]["center_y"]],
        [candles[8]["center_x"], candles[8]["center_y"]],
        [candles[9]["center_x"], candles[9]["center_y"]],
        [candles[10]["center_x"], candles[10]["center_y"]],
        [candles[11]["center_x"], candles[11]["center_y"]],
    ]
    session["tracking_summary"]["support_resistance_zones"] = [
        {
            "key": "wide_recent_support",
            "role": "support",
            "label": "WIDE DEMAND",
            "direction": "BUY",
            "bbox": [600, 510, 980, 640],
            "bounds": [600, 510, 980, 640],
            "line_y": recent_touch_points[-1][1],
            "line_x0": 600,
            "line_x1": 980,
            "touch_points": [[610, 626], [635, 618], [658, 610], *recent_touch_points],
            "source_indices": list(range(24)),
            "confidence": 0.9,
            "truth_score": 0.9,
        }
    ]

    overlays = build_v3_overlays_from_session(session)
    demand = next(row for row in overlays if row["source_path"] == "tracking_summary.support_resistance_zones[0]")
    bounds = demand["bounds"]

    assert demand["anchor_evidence_status"] == "VALID"
    assert demand["anchor_quality"]["local_cluster_snap"] is True
    assert bounds[0] >= candles[8]["bbox"][0] - 18
    assert bounds[2] <= candles[-1]["bbox"][2] + 24
    assert bounds[2] - bounds[0] <= 80
    assert set(demand["anchor_candles"]).issubset(set(range(6, 12)))


def test_tracker_replay_micro_boxes_prefer_child_bbox_over_parent_bounds(tmp_path: Path) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=12)
    parent_bounds = [candles[1]["bbox"][0] - 40, 320, candles[-1]["bbox"][2] + 80, 720]
    sniper_window = [candles[5]["bbox"][0] - 8, 548, candles[8]["bbox"][2] + 8, 570]
    target_window = [candles[7]["bbox"][0] - 8, 382, candles[10]["bbox"][2] + 8, 404]
    session["tracking_summary"]["historical_structure"] = [
        {
            "key": "history_micro",
            "label": "H MICRO",
            "direction": "BUY",
            "bbox": parent_bounds,
            "bounds": parent_bounds,
            "sniper_window": sniper_window,
            "target_window": target_window,
            "source_indices": list(range(12)),
            "start_point": [candles[5]["center_x"], 559],
            "end_point": [candles[10]["center_x"], 393],
            "path": [[candle["center_x"], candle["center_y"]] for candle in candles[1:11]],
            "confidence": 0.88,
            "truth_score": 0.88,
        }
    ]

    overlays = build_v3_overlays_from_session(session)
    replay_entry = next(row for row in overlays if row["source_path"].endswith("historical_structure[0].sniper_window"))
    replay_exit = next(row for row in overlays if row["source_path"].endswith("historical_structure[0].target_window"))
    entry_bounds = replay_entry["bounds"]
    exit_bounds = replay_exit["bounds"]

    assert replay_entry["type"] == "REPLAY_ENTRY"
    assert replay_entry["anchor_quality"]["local_cluster_snap"] is True
    assert entry_bounds[2] - entry_bounds[0] < (parent_bounds[2] - parent_bounds[0]) * 0.35
    assert entry_bounds[3] - entry_bounds[1] <= 36
    assert sniper_window[1] - 4 <= entry_bounds[1] <= sniper_window[3] + 4
    assert replay_exit["type"] == "REPLAY_EXIT"
    assert replay_exit["anchor_quality"]["local_cluster_snap"] is True
    assert exit_bounds[2] - exit_bounds[0] < (parent_bounds[2] - parent_bounds[0]) * 0.35
    assert exit_bounds[3] - exit_bounds[1] <= 36
    assert target_window[1] - 4 <= exit_bounds[1] <= target_window[3] + 4


def test_precision_resolver_preserves_source_frame_before_stale_check(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
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
    overlays: list[dict[str, Any]] = [
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


def test_precision_resolver_clean_live_budget_ghosts_counter_side_context(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
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
            "anchor_candles": [10, 11],
            "touch_points": [[524, 318], [562, 326]],
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
    assert clean_audit["rendered_count"] == 1
    assert clean_audit["rejected_count"] == 0
    assert "CLEAN_LIVE" in clean[0]["visible_modes"]
    assert clean[0]["visible_default"] is True
    assert clean[0]["geometry_visible"] is True
    assert clean[0]["display_state"] == "GHOSTED"
    assert clean[0]["label_hidden"] is True
    assert "counter_side_ghosted_not_hidden" in clean[0]["precision_flags"]


def test_precision_resolver_counts_replay_hidden_defaults_as_rendered(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
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
    overlays: list[dict[str, Any]] = [
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
            "anchor_candles": [20],
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
            "anchor_candles": [19],
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
            "anchor_candles": [12],
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


def test_replay_mode_does_not_publish_current_candle_boxes(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
        {
            "overlay_id": "current-live",
            "object_id": "current-live",
            "track_id": "current-live",
            "type": "CURRENT_CANDLE",
            "side": "BUY",
            "source_agent": "current_candle_tracker",
            "frame_id": 14494,
            "sequence_id": "seq",
            "chart_transform_id": "ct",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "bounds": [1020, 400, 1040, 520],
            "truth_score": 0.96,
            "confidence": 0.96,
            "visible_modes": ["CLEAN_LIVE", "CANDLES", "ACTIVE_CONTEXT", "INSPECTOR"],
            "label": "NOW",
            "anchor_candles": [20],
        }
    ]

    resolved, _audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="REPLAY",
        current_side="BUY",
        frame_id=14494,
    )

    assert not any(
        row.get("type") == "CURRENT_CANDLE" and overlay_is_visible(row, "REPLAY")
        for row in resolved
    )
