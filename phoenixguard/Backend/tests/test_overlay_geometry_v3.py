from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageDraw

from phoenixguard.mobile_api.window_tracker import PhoenixGuardWindowTrackingAdapter, overlay_font
from phoenixguard.vision.overlay_geometry import (
    DEFAULT_LAYER_VISIBILITY,
    build_overlay_truth_audit,
    bbox_iou,
    merge_same_type_boxes,
    prepare_overlay_geometry,
    sanitize_overlay_box,
)


def test_overlay_zone_box_is_clipped_inside_chart_bounds() -> None:
    zone: dict[str, Any] = {
        "key": "support_1",
        "role": "support",
        "bbox": [-40, 92, 130, 118],
        "line_y": 104,
        "line_x0": -20,
        "line_x1": 130,
        "confidence": 0.7,
    }

    sanitized = sanitize_overlay_box(
        zone,
        chart_bounds=[0, 0, 180, 120],
        layer="supply_demand",
        require_anchor=True,
    )

    assert sanitized is not None
    x0, y0, x1, y1 = sanitized["bbox"]
    assert 0 <= x0 < x1 <= 180
    assert 0 <= y0 < y1 <= 120


def test_base_departure_zone_keeps_historical_reaction_span() -> None:
    zone: dict[str, Any] = {
        "key": "resistance_1",
        "role": "resistance",
        "bbox": [700, 340, 1260, 390],
        "line_y": 366,
        "line_x0": 700,
        "line_x1": 1260,
        "confidence": 0.76,
        "supply_demand_origin": "base_departure_imbalance",
        "zone_pattern": "DROP_BASE_DROP",
        "touch_points": [[51, 372], [580, 367], [931, 366], [1038, 388]],
    }

    sanitized = sanitize_overlay_box(
        zone,
        chart_bounds=[0, 0, 1628, 585],
        layer="supply_demand",
        require_anchor=True,
    )

    assert sanitized is not None
    x0, _y0, x1, _y1 = sanitized["bbox"]
    assert x0 < x1
    assert x1 - x0 >= 1628 * 0.25
    assert x1 - x0 <= 1628 * 0.31


def test_overlay_broker_panel_is_not_accepted_as_chart_zone() -> None:
    chart_bounds = [0, 0, 1000, 500]
    broker_panel = [720, 0, 1000, 500]
    zone: dict[str, Any] = {
        "key": "resistance_1",
        "role": "resistance",
        "bbox": [650, 120, 760, 154],
        "line_y": 137,
        "line_x0": 650,
        "line_x1": 760,
        "confidence": 0.66,
    }

    sanitized = sanitize_overlay_box(
        zone,
        chart_bounds=chart_bounds,
        broker_exclusion_boxes=[broker_panel],
        layer="supply_demand",
        require_anchor=True,
    )
    panel_only = sanitize_overlay_box(
        {**zone, "bbox": [760, 120, 930, 154], "line_x0": 760, "line_x1": 930},
        chart_bounds=chart_bounds,
        broker_exclusion_boxes=[broker_panel],
        layer="supply_demand",
        require_anchor=True,
    )

    assert sanitized is not None
    assert sanitized["bbox"][2] <= broker_panel[0]
    assert panel_only is None


def test_overlay_geometry_tightens_micro_windows_and_level_lines_against_exclusions() -> None:
    prepared = prepare_overlay_geometry(
        {
            "structure_boxes": [
                {
                    "key": "current",
                    "label": "CURRENT",
                    "direction": "SELL",
                    "bbox": [880, 140, 1160, 420],
                    "start_point": [890, 390],
                    "end_point": [1138, 210],
                    "sniper_window": [40, 176, 1180, 252],
                    "trigger_window": [32, 292, 1190, 366],
                    "confidence": 0.83,
                }
            ],
            "support_resistance_zones": [
                {
                    "key": "wide_resistance",
                    "role": "resistance",
                    "label": "WIDE RESISTANCE",
                    "bbox": [120, 156, 1080, 232],
                    "line_y": 194,
                    "line_x0": 0,
                    "line_x1": 1180,
                    "confidence": 0.76,
                }
            ],
        },
        {"action": "SELL"},
        chart_size=[1200, 700],
        broker_exclusion_boxes=[[1040, 0, 1200, 700]],
    )

    current = next(box for box in prepared["tracking_summary"]["structure_boxes"] if box["key"] == "current")
    sniper = current["sniper_window"]
    trigger = current["trigger_window"]
    assert sniper[2] - sniper[0] <= 1200 * 0.22 + 1
    assert trigger[2] - trigger[0] <= 1200 * 0.22 + 1
    assert sniper[2] <= 1040
    assert trigger[2] <= 1040

    zone = prepared["tracking_summary"]["support_resistance_zones"][0]
    assert zone["bbox"][2] <= 1040
    assert zone["line_x0"] == zone["bbox"][0]
    assert zone["line_x1"] == zone["bbox"][2]
    assert zone["visible_default"] is True


def test_overlay_rejects_box_area_above_layer_max() -> None:
    oversized: dict[str, Any] = {
        "key": "support_fullscreen",
        "role": "support",
        "bbox": [0, 0, 1000, 500],
        "line_y": 250,
        "line_x0": 0,
        "line_x1": 1000,
        "confidence": 0.9,
    }

    assert (
        sanitize_overlay_box(
            oversized,
            chart_bounds=[0, 0, 1000, 500],
            layer="supply_demand",
            require_anchor=True,
        )
        is None
    )


def test_overlay_merges_overlapping_same_type_boxes() -> None:
    boxes: list[dict[str, Any]] = [
        {
            "key": "support_1",
            "role": "support",
            "layer": "supply_demand",
            "direction": "BUY",
            "bbox": [40, 180, 180, 215],
            "confidence": 0.62,
        },
        {
            "key": "support_2",
            "role": "support",
            "layer": "supply_demand",
            "direction": "BUY",
            "bbox": [52, 182, 190, 216],
            "confidence": 0.74,
        },
    ]

    merged = merge_same_type_boxes(boxes)

    assert len(merged) == 1
    assert merged[0]["merged_count"] == 2
    assert bbox_iou(merged[0]["bbox"], [40, 180, 190, 216]) > 0.99
    assert merged[0]["confidence"] == 0.74


def test_overlay_zone_requires_structural_anchor() -> None:
    floating_zone: dict[str, Any] = {
        "key": "floating_zone",
        "role": "support",
        "bbox": [40, 180, 180, 215],
        "confidence": 0.7,
    }

    assert (
        sanitize_overlay_box(
            floating_zone,
            chart_bounds=[0, 0, 420, 260],
            layer="supply_demand",
            require_anchor=True,
        )
        is None
    )


def test_overlay_active_live_view_shows_historical_replay_by_default() -> None:
    prepared = prepare_overlay_geometry(
        {
            "tracked_candles": [
                {"bbox": [10, 90, 14, 112], "direction": "BUY", "center_x": 12, "center_y": 101},
                {"bbox": [30, 82, 34, 104], "direction": "BUY", "center_x": 32, "center_y": 93},
            ],
            "historical_structure": [
                {
                    "key": "history_1",
                    "label": "H1 BUY",
                    "direction": "BUY",
                    "bbox": [10, 70, 90, 120],
                    "start_point": [12, 101],
                    "end_point": [88, 82],
                    "candle_count": 4,
                }
            ],
        },
        {"action": "BUY", "execution_action": "HOLD"},
        chart_size=[240, 160],
    )
    geometry = prepared["overlay_geometry"]
    historical = [box for box in geometry["boxes"] if box["layer"] == "historical_replay"]

    assert DEFAULT_LAYER_VISIBILITY["historical_replay"] is True
    assert geometry["layer_visibility"]["historical_replay"] is True
    assert historical
    assert all(box["visible_default"] is True for box in historical)


def test_overlay_cancel_line_stays_near_trigger_zone_not_full_chart(monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = Image.new("RGBA", (1000, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    captured: list[tuple[tuple[int, int], tuple[int, int]]] = []

    def capture_line(_draw: Any, start: tuple[int, int], end: tuple[int, int], *_args: Any, **_kwargs: Any) -> None:
        captured.append((start, end))

    monkeypatch.setattr(adapter, "_draw_dashed_line", capture_line)

    adapter.draw_projection_layer(  # noqa: SLF001
        draw,
        {
            "target_first_probability": 0.6,
            "invalidation_first_probability": 0.2,
            "zones": [
                {
                    "kind": "primary",
                    "direction": "BUY",
                    "label": "BUY TRIGGER",
                    "bbox": [700, 220, 760, 250],
                    "target_bbox": [740, 150, 800, 180],
                    "invalidation_y": 330,
                    "path": [[660, 270], [730, 235], [780, 165]],
                    "confidence": 0.82,
                }
            ],
        },
        chart_box=[0, 0, 1000, 500],
        offset=(0, 0),
        colors={"current": (138, 160, 181)},
        font=overlay_font(12),
    )

    cancel_lines = [(start, end) for start, end in captured if start[1] == 330 and end[1] == 330]
    assert cancel_lines
    start, end = cancel_lines[-1]
    assert start[0] > 600
    assert end[0] < 840
    assert end[0] - start[0] < 260


def test_live_overlay_does_not_draw_prediction_path(monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    monkeypatch.delenv("PHOENIXGUARD_ENABLE_LIVE_PREDICTION_PATH", raising=False)

    def fail_projection_draw(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("live overlay must not draw prediction path overlays")

    monkeypatch.setattr(adapter, "draw_projection_layer", fail_projection_draw)
    image = Image.new("RGB", (320, 220), (8, 12, 18))
    tracking_summary: dict[str, Any] = {
        "tracked_candles": [
            {"bbox": [40, 110, 48, 160], "center_x": 44, "center_y": 135, "direction": "SELL"},
            {"bbox": [70, 120, 78, 174], "center_x": 74, "center_y": 147, "direction": "SELL"},
        ],
        "projection": {
            "zones": [
                {
                    "kind": "primary",
                    "direction": "SELL",
                    "label": "SELL RECLAIM TRIGGER",
                    "bbox": [230, 130, 280, 155],
                    "target_bbox": [230, 176, 280, 195],
                    "path": [[210, 124], [250, 145], [270, 188]],
                    "confidence": 0.81,
                }
            ]
        },
        "overlay_geometry": {"layer_visibility": {"trigger_zones": True}},
    }

    adapter.render_overlay(image, [0, 0, 320, 220], tracking_summary, {"action": "SELL"})  # noqa: SLF001


def test_live_overlay_renderer_honors_visible_default_for_hidden_layers(monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = Image.new("RGB", (320, 220), (8, 12, 18))
    canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    hidden_zone: dict[str, Any] = {
        "key": "hidden_support",
        "role": "support",
        "bbox": [40, 140, 220, 168],
        "line_y": 154,
        "line_x0": 40,
        "line_x1": 220,
        "confidence": 0.8,
        "visible_default": False,
    }

    before = canvas.tobytes()
    adapter.draw_support_resistance_layer(  # noqa: SLF001
        draw,
        [hidden_zone],
        chart_box=[0, 0, 320, 220],
        offset=(0, 0),
        font=overlay_font(12),
        require_visible_default=True,
    )
    assert canvas.tobytes() == before

    def fail_structure_draw(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("hidden structure boxes must not be rendered in live mode")

    monkeypatch.setattr(adapter, "_draw_structure_box", fail_structure_draw)
    adapter.render_overlay(  # noqa: SLF001
        image,
        [0, 0, 320, 220],
        {
            "tracked_candles": [],
            "support_resistance_zones": [hidden_zone],
            "structure_boxes": [
                {
                    "key": "local",
                    "layer": "local_swings",
                    "bbox": [30, 60, 260, 190],
                    "visible_default": False,
                }
            ],
            "overlay_geometry": {"layer_visibility": {"supply_demand": True, "local_swings": True}},
        },
        {"action": "HOLD"},
    )


def test_overlay_geometry_keeps_prediction_zones_out_of_live_boxes() -> None:
    prepared = prepare_overlay_geometry(
        {
            "tracked_candles": [
                {"bbox": [35, 90, 42, 138], "direction": "SELL", "center_x": 38, "center_y": 114},
                {"bbox": [58, 110, 65, 158], "direction": "SELL", "center_x": 61, "center_y": 134},
            ],
            "projection": {
                "zones": [
                    {
                        "key": "future_sell_path",
                        "kind": "primary",
                        "direction": "SELL",
                        "label": "SELL RECLAIM TRIGGER",
                        "bbox": [220, 128, 280, 154],
                        "target_bbox": [220, 176, 280, 198],
                        "path": [[200, 120], [246, 140], [270, 188]],
                        "invalidation_y": 80,
                        "confidence": 0.82,
                    }
                ]
            },
        },
        {"action": "SELL"},
        chart_size=[320, 220],
    )

    geometry = prepared["overlay_geometry"]
    labels = " ".join(str(box.get("label", "")) for box in geometry["boxes"])
    assert "RECLAIM TRIGGER" not in labels
    assert "TARGET" not in labels
    assert geometry["layer_counts"]["trigger_zones"] == 0
    projection = prepared["tracking_summary"]["projection"]
    assert projection["visual_overlay_disabled"] is True


def test_regression_lines_anchor_to_wick_envelope_not_candle_centers() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()

    class DrawSpy:
        def __init__(self) -> None:
            self.lines: list[tuple[float, float, float, float]] = []

        def line(self, coords: Any, **_kwargs: Any) -> None:
            values = tuple(float(value) for value in coords)
            assert len(values) == 4
            self.lines.append((values[0], values[1], values[2], values[3]))

        def ellipse(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    uptrend_draw = DrawSpy()
    adapter.draw_regression_line(  # noqa: SLF001
        cast(ImageDraw.ImageDraw, uptrend_draw),
        [
            {"bbox": [10, 90, 16, 130], "center_x": 13, "center_y": 110, "direction": "BUY"},
            {"bbox": [30, 70, 36, 110], "center_x": 33, "center_y": 90, "direction": "BUY"},
            {"bbox": [50, 50, 56, 90], "center_x": 53, "center_y": 70, "direction": "BUY"},
        ],
        (255, 220, 80, 255),
    )
    assert uptrend_draw.lines
    _x0, y0, _x1, y1 = uptrend_draw.lines[-1]
    assert abs(y0 - 130.0) <= 0.01
    assert abs(y1 - 90.0) <= 0.01

    downtrend_draw = DrawSpy()
    adapter.draw_regression_line(  # noqa: SLF001
        cast(ImageDraw.ImageDraw, downtrend_draw),
        [
            {"bbox": [10, 50, 16, 90], "center_x": 13, "center_y": 70, "direction": "SELL"},
            {"bbox": [30, 70, 36, 110], "center_x": 33, "center_y": 90, "direction": "SELL"},
            {"bbox": [50, 90, 56, 130], "center_x": 53, "center_y": 110, "direction": "SELL"},
        ],
        (255, 220, 80, 255),
    )
    assert downtrend_draw.lines
    _x0, y0, _x1, y1 = downtrend_draw.lines[-1]
    assert abs(y0 - 50.0) <= 0.01
    assert abs(y1 - 90.0) <= 0.01


def test_overlay_geometry_perf_metadata_is_live_safe() -> None:
    prepared = prepare_overlay_geometry(
        {
            "tracked_candles": [
                {"bbox": [30, 90, 36, 118], "direction": "BUY", "center_x": 33, "center_y": 104},
            ],
            "support_resistance_zones": [
                {
                    "key": "support_1",
                    "role": "support",
                    "bbox": [20, 116, 130, 126],
                    "line_y": 121,
                    "line_x0": 20,
                    "line_x1": 130,
                    "confidence": 0.7,
                }
            ],
        },
        {"action": "BUY"},
        chart_size=[240, 160],
    )
    geometry = prepared["overlay_geometry"]

    assert geometry["render_budget_ms"] <= 16
    assert geometry["debug_enabled"] is False
    assert geometry["diagnostics_enabled"] is False
    assert geometry["static_layer_hash"]
    assert geometry["temporal_smoothing"]["enabled"] is True
    assert geometry["truth_audit"]["version"] == "OVERLAY_TRUTH_AUDIT_V1"
    assert geometry["truth_audit"]["valid_for_execution"] is True


def test_overlay_truth_audit_rejects_unanchored_decision_box() -> None:
    audit = build_overlay_truth_audit(
        [
            {
                "key": "floating_supply",
                "role": "supply",
                "layer": "supply_demand",
                "bbox": [10, 20, 200, 60],
                "confidence": 0.3,
                "structural_anchor": False,
            }
        ]
    )

    assert audit["valid_for_execution"] is False
    assert audit["decision_invalid_object_count"] == 1
    assert audit["objects"][0]["valid_for_decision"] is False


def test_dashboard_exposes_layer_controls_latency_and_nonblocking_health() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    for layer_name in (
        "chart_bounds",
        "recent_candles",
        "major_swings",
        "local_swings",
        "supply_demand",
        "trigger_zones",
        "active_council_decision",
        "historical_replay",
        "broker_controls",
        "diagnostics",
    ):
        assert layer_name in dashboard_html
    assert "historical_replay: true" in dashboard_html
    assert "latency-pipeline" in dashboard_html
    assert "latency-overlay" in dashboard_html
    assert "model-health-panel" in dashboard_html
    assert "static_layer_hash" in dashboard_html
    assert "reuseStaticLayers" in dashboard_html
    assert "debug_enabled" in dashboard_html
