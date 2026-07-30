from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PIL import Image
import pytest

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
        "display_frame_id": 14494,
        "model_vote_frame_id": 14494,
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
            "detected_market": "EUR/USD OTC",
            "detected_timeframe": "M5",
            "market_confidence": 0.93,
            "timeframe_confidence": 0.91,
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "market_selector_visual_fingerprint": "selector_v2_eurusdotc",
            "market_selector_rebind_required": False,
            "market_selector_studying_new_pair": False,
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
        "latest_signal": {
            "action": "SELL",
            "confidence": 0.9,
            "effective_confidence": 0.9,
            "market": "EUR/USD OTC",
            "focus_timeframe": "M5",
            "market_confidence": 0.93,
            "timeframe_confidence": 0.91,
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "market_selector_visual_fingerprint": "selector_v2_eurusdotc",
            "market_selector_rebind_required": False,
            "market_selector_studying_new_pair": False,
        },
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


def _strict_lstm_forecast(payload: dict[str, Any]) -> dict[str, Any]:
    """Complete a valid public fixture with one atomic 12-event scenario bundle."""

    path = cast(list[dict[str, Any]], payload["forecast_path"])
    assert [int(row["step"]) for row in path] == list(range(1, 13))
    primary_side = str(payload.get("path_side") or payload.get("side") or "HOLD").upper()
    assert primary_side in {"BUY", "SELL", "HOLD"}
    features = cast(list[dict[str, Any]], payload.get("features") or [])
    anchor_close = (
        float(features[-1].get("relative_price_location", 0.5))
        if features
        else 0.5
    )

    def scenario_path(side: str) -> list[dict[str, Any]]:
        if side == primary_side:
            return [
                {
                    "step": int(row["step"]),
                    "expected_close_norm": float(row["expected_close_norm"]),
                }
                for row in path
            ]
        return [
            {
                "step": step,
                "expected_close_norm": max(
                    0.0,
                    min(
                        1.0,
                        anchor_close
                        + (
                            0.006 * step
                            if side == "BUY"
                            else -0.006 * step
                            if side == "SELL"
                            else (0.001 if step % 2 else -0.001)
                        ),
                    ),
                ),
            }
            for step in range(1, 13)
        ]

    payload["trajectory_mode"] = primary_side
    payload["trajectory_mode_probability_calibrated"] = False
    payload["trajectory_scenarios"] = [
        {
            "side": side,
            "probability": 0.6 if side == primary_side else 0.2,
            "probability_calibrated": False,
            "selected": side == primary_side,
            "forecast_path": scenario_path(side),
        }
        for side in ("BUY", "SELL", "HOLD")
    ]
    return payload


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


@pytest.mark.skip(reason="retired public forecast modes are no longer renderable")
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


def test_study_modes_do_not_synthesize_overlays_without_model_payloads(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)

    two_candle_state = build_live_state_v3(session, overlay_mode="TWO_CANDLE_STUDY", now_epoch=120.0)
    lstm_state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)

    assert not any(row.get("type") == "TWO_CANDLE_STUDY" for row in two_candle_state["overlay_objects"])
    assert not any(row.get("type") == "LSTM_STUDY" for row in lstm_state["overlay_objects"])


def test_study_overlays_reject_wrong_frame_and_expired_packets(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["model_council_study_packet"] = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_PACKET_V3",
        "valid_until_epoch": 130.0,
        "two_candle_study": {"confidence": 0.71, "side": "BUY"},
        "lstm_contribution": {
            "confidence": 0.69,
            "side": "BUY",
            "source_image_size": [1000, 800],
            "forecast_path": [{"step": 1, "expected_close_norm": 0.58}],
        },
    }


    session["model_vote_frame_id"] = 14493

    wrong_frame_state = build_live_state_v3(session, overlay_mode="INSPECTOR", now_epoch=120.0)
    assert not any(
        row.get("type") in {"TWO_CANDLE_STUDY", "LSTM_STUDY"}
        for row in wrong_frame_state["overlay_objects"]
    )

    session["model_vote_frame_id"] = 14494
    session["model_council_study_packet"]["valid_until_epoch"] = 119.999
    expired_state = build_live_state_v3(session, overlay_mode="INSPECTOR", now_epoch=120.0)
    assert not any(
        row.get("type") in {"TWO_CANDLE_STUDY", "LSTM_STUDY"}
        for row in expired_state["overlay_objects"]
    )

    study_packet = cast(dict[str, Any], session["model_council_study_packet"])
    study_packet["valid_until_epoch"] = 130.0
    cast(dict[str, Any], study_packet["two_candle_study"])["fresh"] = False
    cast(dict[str, Any], study_packet["lstm_contribution"])["fresh"] = False
    explicitly_stale_state = build_live_state_v3(
        session,
        overlay_mode="INSPECTOR",
        now_epoch=120.0,
    )
    assert not any(
        row.get("type") in {"TWO_CANDLE_STUDY", "LSTM_STUDY"}
        for row in explicitly_stale_state["overlay_objects"]
    )


@pytest.mark.skip(reason="retired public forecast overlays are no longer renderable")
def test_waiting_frame_renders_last_aligned_forecast_as_stale_diagnostic(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["visual_observation_v3"] = {
        "status": "WAITING_FOR_NEW_FRAME",
        "new_visual_evidence": False,
    }
    session["forecast_snapshot_v3"] = {
        "schema_version": "PG_FORECAST_SNAPSHOT_V3",
        "source_frame_id": 14494,
        "observed_epoch": 110.0,
        "stale": True,
        "diagnostic_only": True,
        "two_candle_study": {
            "schema_version": "PG_TWO_CANDLE_STUDY_V3",
            "frame_id": 14494,
            "stale": True,
            "diagnostic_only": True,
            "confidence": 0.64,
            "primary_pressure": "SELL",
        },
        "lstm_contribution": {
            "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
            "frame_id": 14494,
            "stale": True,
            "diagnostic_only": True,
            "fresh": True,
            "forecast_available": True,
            "confidence": 0.78,
            "path_side": "SELL",
            "selective_side": "SELL",
            "selective_status": "AUTHORIZED",
            "selective_authorized": True,
            "source_image_size": [1000, 800],
            "features": [{"relative_price_location": 0.52}],
            "forecast_path": [
                {
                    "step": step,
                    "expected_close_norm": 0.52 - step * 0.01,
                    "close_lower_90_norm": 0.48 - step * 0.008,
                    "close_upper_90_norm": 0.56 - step * 0.012,
                }
                for step in range(1, 13)
            ],
        },
    }
    _strict_lstm_forecast(
        cast(
            dict[str, Any],
            cast(dict[str, Any], session["forecast_snapshot_v3"])["lstm_contribution"],
        )
    )

    lstm_state = build_live_state_v3(
        session,
        overlay_mode="LSTM_STUDY",
        now_epoch=180.0,
    )
    lstm_rows = [
        row
        for row in lstm_state["overlay_objects"]
        if row.get("type") == "LSTM_STUDY"
    ]
    assert len(lstm_rows) == 1
    assert all(row.get("frame_id") == 14494 for row in lstm_rows)
    assert all(str(row.get("role")).endswith("_stale_diagnostic") for row in lstm_rows)
    assert all(row.get("side") == "HOLD" for row in lstm_rows)
    assert all("LAST VALID" in str(row.get("reason")) for row in lstm_rows)
    assert lstm_rows[0].get("role") == "lstm_forecast_composite_stale_diagnostic"
    assert len(cast(list[dict[str, Any]], lstm_rows[0].get("forecast_candles"))) == 12
    assert lstm_rows[0].get("forecast_band_points") in (None, [])
    assert cast(dict[str, Any], lstm_rows[0].get("interval"))["calibrated"] is False

    two_candle_state = build_live_state_v3(
        session,
        overlay_mode="TWO_CANDLE_STUDY",
        now_epoch=180.0,
    )
    two_candle_rows = [
        row
        for row in two_candle_state["overlay_objects"]
        if row.get("type") == "TWO_CANDLE_STUDY"
    ]
    assert len(two_candle_rows) == 1
    assert two_candle_rows[0]["role"] == "two_candle_study_stale_diagnostic"


def test_waiting_frame_never_projects_forecast_snapshot_onto_new_frame(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["visual_observation_v3"] = {
        "status": "WAITING_FOR_NEW_FRAME",
        "new_visual_evidence": False,
    }
    session["forecast_snapshot_v3"] = {
        "schema_version": "PG_FORECAST_SNAPSHOT_V3",
        "source_frame_id": 14493,
        "stale": True,
        "diagnostic_only": True,
        "lstm_contribution": {
            "frame_id": 14493,
            "stale": True,
            "diagnostic_only": True,
            "fresh": True,
            "forecast_available": True,
            "confidence": 0.91,
            "path_side": "BUY",
            "source_image_size": [1000, 800],
            "features": [{"relative_price_location": 0.50}],
            "forecast_path": [{"step": 1, "expected_close_norm": 0.55}],
        },
    }

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=180.0)

    assert not any(row.get("type") == "LSTM_STUDY" for row in state["overlay_objects"])


@pytest.mark.skip(reason="retired public forecast overlays are no longer renderable")
def test_current_root_study_packet_emits_truthful_neutral_studies_and_lstm_path(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session.pop("model_vote_frame_id")
    session["latest_signal"]["action"] = "SELL"
    session["model_council_study_packet"] = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_PACKET_V3",
        "frame_id": 14494,
        "valid_until_epoch": 130.0,
        "two_candle_study": {
            "schema_version": "PG_TWO_CANDLE_STUDY_V3",
            "confidence": 0.71,
            "summary": "Current two-candle evidence without a directional vote.",
        },
        "lstm_contribution": {
            "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
            "confidence": 0.69,
            "source_image_size": [1000, 800],
            "features": [{"relative_price_location": 0.51}],
            "forecast_path": [
                {
                    "step": step,
                    "expected_close_norm": 0.51 + step * 0.01,
                }
                for step in range(1, 13)
            ],
        },
    }
    _strict_lstm_forecast(
        cast(
            dict[str, Any],
            cast(dict[str, Any], session["model_council_study_packet"])[
                "lstm_contribution"
            ],
        )
    )

    state = build_live_state_v3(session, overlay_mode="INSPECTOR", now_epoch=120.0)
    study_rows = [
        row
        for row in state["overlay_objects"]
        if row.get("type") in {"TWO_CANDLE_STUDY", "LSTM_STUDY"}
    ]

    assert {row.get("layer") for row in study_rows} == {
        "active_council_decision",
        "prediction_path",
    }
    assert all(row.get("frame_id") == 14494 for row in study_rows)
    assert all(row.get("side") == "HOLD" for row in study_rows)
    assert any(row.get("role") == "two_candle_study" for row in study_rows)
    assert not any(row.get("role") == "lstm_study" for row in study_rows)
    composite = next(
        row
        for row in study_rows
        if row.get("role") == "lstm_forecast_composite_no_edge"
    )
    assert composite.get("side") == "HOLD"
    assert composite.get("forecast_band_points") in (None, [])
    assert len(cast(list[dict[str, Any]], composite.get("forecast_candles"))) == 12


@pytest.mark.skip(reason="retired public LSTM mode is no longer renderable")
def test_lstm_mode_renders_learned_candle_event_progression_path(tmp_path: Path) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=8)
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "skill": "LSTM_CANDLE_PATH",
        "fresh": True,
        "confidence": 0.68,
        "side": "BUY",
        "source_image_size": [1000, 800],
        "features": [{"relative_price_location": 0.44}],
        "forecast_path": [
            {
                "step": step,
                "expected_close_norm": 0.44 + step * 0.008,
                "close_lower_90_norm": 0.42 + step * 0.006,
                "close_upper_90_norm": 0.46 + step * 0.010,
                "direction": "BUY",
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step in range(1, 13)
        ],
        "interpretation": "Twelve causal candle events project upward.",
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    lstm_overlays = [row for row in state["overlay_objects"] if row.get("type") == "LSTM_STUDY"]
    path_rows = [row for row in lstm_overlays if row.get("layer") == "prediction_path"]
    assert len(candles) == 8
    assert len(path_rows) == 1
    assert not any(row.get("layer") == "active_council_decision" for row in lstm_overlays)
    composite = path_rows[0]
    assert composite["role"] == "lstm_forecast_composite_no_edge"
    assert len(composite["line_points"]) == 13
    assert composite["coordinate_mode"] == "CHART_IMAGE_SPACE"
    assert composite["side"] == "HOLD"
    assert composite["forecast_direction"] == "BUY"
    assert composite["line_points"][-1][0] > composite["line_points"][0][0]
    events = cast(list[dict[str, Any]], composite["forecast_candles"])
    assert [row["step"] for row in events] == list(range(1, 13))
    assert all(row["movement_side"] == "BUY" for row in events)
    assert all(row["high_y_norm"] <= min(row["open_y_norm"], row["close_y_norm"]) for row in events)
    assert all(row["low_y_norm"] >= max(row["open_y_norm"], row["close_y_norm"]) for row in events)
    assert composite.get("forecast_band_points") in (None, [])
    assert "NO EDGE" in str(composite.get("reason"))


@pytest.mark.skip(reason="retired public LSTM mode is no longer renderable")
def test_lstm_multimodal_scenarios_share_causal_geometry_and_select_primary(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    selected_closes = [round(0.44 - 0.012 * step, 3) for step in range(1, 13)]
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "skill": "LSTM_CANDLE_PATH",
        "fresh": True,
        "side": "SELL",
        "path_side": "SELL",
        "forecast_quality_status": "LOW_CONFIDENCE",
        "trade_authorization_status": "NO_EDGE",
        "selective_status": "NO_EDGE",
        "selective_authorized": False,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "source_image_size": [1000, 800],
        "features": [
            {"center_x_px": 500.0, "relative_price_location": 0.46},
            {"center_x_px": 524.0, "relative_price_location": 0.45},
            {"center_x_px": 548.0, "relative_price_location": 0.44},
        ],
        "forecast_path": [
            {
                "step": step,
                "expected_close_norm": close,
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step, close in enumerate(selected_closes, start=1)
        ],
        "trajectory_mode": "SELL",
        "trajectory_mode_probability_calibrated": False,
        # Deliberately probability-ordered rather than selection-ordered.  The
        # selected MAP branch must still become the primary public scenario.
        "trajectory_scenarios": [
            {
                "side": "BUY",
                "probability": 0.60,
                "probability_calibrated": False,
                "selected": False,
                "forecast_path": [
                    {
                        "step": step,
                        "expected_close_norm": 0.44 + 0.01 * step,
                    }
                    for step in range(1, 13)
                ],
            },
            {
                "side": "HOLD",
                "probability": 0.10,
                "probability_calibrated": False,
                "selected": False,
                "forecast_path": [
                    {
                        "step": step,
                        "expected_close_norm": 0.44
                        + (0.001 if step % 2 else -0.001),
                    }
                    for step in range(1, 13)
                ],
            },
            {
                "side": "SELL",
                "probability": 0.30,
                "probability_calibrated": False,
                "selected": True,
                "forecast_path": [
                    {"step": step, "expected_close_norm": close}
                    for step, close in enumerate(selected_closes, start=1)
                ],
            },
        ],
    }

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    composite = next(
        row
        for row in state["overlay_objects"]
        if row.get("role") == "lstm_forecast_composite_low_confidence"
    )
    scenarios = cast(list[dict[str, Any]], composite["forecast_scenarios"])

    assert [row["side"] for row in scenarios] == ["SELL", "BUY", "HOLD"]
    assert [row["selected"] for row in scenarios] == [True, False, False]
    assert [row["probability"] for row in scenarios] == [0.30, 0.60, 0.10]
    assert all(row["probability_calibrated"] is False for row in scenarios)
    assert all(row["event_count"] == 12 for row in scenarios)
    assert composite["trajectory_mode"] == "SELL"
    assert composite["trajectory_mode_probability_calibrated"] is False

    primary_points = cast(list[list[float]], scenarios[0]["line_points"])
    assert [[round(value, 6) for value in point] for point in primary_points] == [
        [
            round(0.548 + 0.024 * step, 6),
            round(0.56 if step == 0 else 1.0 - selected_closes[step - 1], 6),
        ]
        for step in range(13)
    ]
    for scenario in scenarios:
        points = cast(list[list[float]], scenario["line_points"])
        assert [round(value, 6) for value in points[0]] == [0.548, 0.56]
        assert [round(point[0], 6) for point in points] == [
            round(0.548 + 0.024 * step, 6)
            for step in range(13)
        ]
    assert [round(point[1], 6) for point in scenarios[1]["line_points"]] == [
        0.56,
        *[round(0.56 - 0.01 * step, 6) for step in range(1, 13)],
    ]


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_low_quality_direct_lstm_path_remains_visible_as_neutral_diagnostic(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_LIVE_STATE_CLEAN_OVERLAYS_ONLY", "1")
    session = _session(tmp_path)
    _install_visible_candles(session, count=12)
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "skill": "LSTM_CANDLE_PATH",
        "fresh": True,
        "side": "BUY",
        "path_side": "BUY",
        "forecast_quality_status": "LOW_CONFIDENCE",
        "trade_authorization_status": "NO_EDGE",
        "selective_status": "NO_EDGE",
        "selective_authorized": False,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "sequence_quality": {
            "ready": False,
            "reasons": ["missing_or_duplicate_candle_slots"],
        },
        "source_image_size": [1000, 800],
        "features": [{"relative_price_location": 0.44}],
        "forecast_path": [
            {
                "step": step,
                "expected_open_norm": 0.44 + (step - 1) * 0.004,
                "expected_close_norm": 0.44 + step * 0.004,
                "expected_range_norm": 0.01,
                "candle_body_direction": "BUY",
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step in range(1, 13)
        ],
        "interpretation": "Twelve low-confidence direct candle events project upward.",
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    composite = next(
        row
        for row in state["overlay_objects"]
        if row.get("role") == "lstm_forecast_composite_low_confidence"
    )

    assert composite["side"] == "HOLD"
    assert composite["forecast_direction"] == "BUY"
    assert composite["forecast_quality_status"] == "LOW_CONFIDENCE"
    assert composite["trade_authorization_status"] == "NO_EDGE"
    assert len(cast(list[dict[str, Any]], composite["forecast_candles"])) == 12
    assert "LOW CONFIDENCE" in str(composite["reason"])


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_lstm_overlay_keeps_path_movement_separate_from_candle_body(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "frame_id": 14494,
        "fresh": True,
        "forecast_available": True,
        "forecast_quality_status": "LOW_CONFIDENCE",
        "selective_status": "NO_EDGE",
        "selective_authorized": False,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "path_side": "BUY",
        "source_image_size": [1000, 800],
        "features": [{"center_x_px": 610.0, "relative_price_location": 0.50}],
        "forecast_path": [
            {
                "step": 1,
                # The body is SELL, while the close moved up from the 0.50
                # path anchor.  Those are separate model outputs.
                "expected_open_norm": 0.58,
                "expected_high_norm": 0.60,
                "expected_low_norm": 0.49,
                "expected_close_norm": 0.52,
                "expected_range_norm": 0.11,
                "candle_body_direction": "SELL",
                "movement_direction": "BUY",
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            },
            {
                "step": 2,
                "expected_open_norm": 0.56,
                "expected_high_norm": 0.58,
                "expected_low_norm": 0.50,
                "expected_close_norm": 0.51,
                "expected_range_norm": 0.08,
                "candle_body_direction": "SELL",
                "movement_direction": "SELL",
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            },
            *[
                {
                    "step": step,
                    "expected_open_norm": 0.53 - 0.005 * (step - 2),
                    "expected_high_norm": 0.54 - 0.005 * (step - 2),
                    "expected_low_norm": 0.50 - 0.005 * (step - 2),
                    "expected_close_norm": 0.51 - 0.005 * (step - 2),
                    "expected_range_norm": 0.04,
                    "candle_body_direction": "SELL",
                    "movement_direction": "SELL",
                    "selective_status": "NO_EDGE",
                    "selective_authorized": False,
                }
                for step in range(3, 13)
            ],
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    composite = next(
        row
        for row in state["overlay_objects"]
        if row.get("role") == "lstm_forecast_composite_low_confidence"
    )
    events = cast(list[dict[str, Any]], composite["forecast_candles"])

    assert [row["movement_side"] for row in events] == ["BUY", *["SELL"] * 11]
    assert [row["body_bias"] for row in events] == ["SELL"] * 12
    assert [row["direction_conflict"] for row in events] == [True, *[False] * 11]


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_current_direct_lstm_path_outranks_legacy_study_packet_copy(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=12)
    session["model_council_study_packet"] = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_PACKET_V3",
        "frame_id": 14494,
        "valid_until_epoch": 100.0,
        "lstm_contribution": {
            "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
            "legacy_restored": True,
            "fresh": True,
            "path_side": "SELL",
            "source_image_size": [1000, 800],
            "features": [{"relative_price_location": 0.50}],
            "forecast_path": [
                {"step": step, "expected_close_norm": 0.50 - step * 0.01}
                for step in range(1, 13)
            ],
        },
    }
    session["forecast_snapshot_v3"] = {
        "source_frame_id": 14493,
        "stale": True,
        "diagnostic_only": True,
    }
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "frame_id": 14494,
        "fresh": True,
        "legacy_restored": False,
        "forecast_available": True,
        "forecast_quality_status": "LOW_CONFIDENCE",
        "selective_status": "NO_EDGE",
        "selective_authorized": False,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "path_side": "BUY",
        "source_image_size": [1000, 800],
        "features": [{"relative_price_location": 0.44}],
        "forecast_path": [
            {
                "step": step,
                "expected_open_norm": 0.44 + (step - 1) * 0.004,
                "expected_close_norm": 0.44 + step * 0.004,
                "expected_range_norm": 0.01,
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step in range(1, 13)
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    composites = [
        row
        for row in state["overlay_objects"]
        if row.get("role") == "lstm_forecast_composite_low_confidence"
    ]

    assert len(composites) == 1
    assert composites[0]["forecast_direction"] == "BUY"
    assert composites[0]["side"] == "HOLD"
    assert len(cast(list[dict[str, Any]], composites[0]["forecast_candles"])) == 12


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_direct_lstm_path_uses_causal_feature_anchor_when_compact_candles_are_absent(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    session["tracking_summary"]["tracked_candles"] = []
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "frame_id": 14494,
        "fresh": True,
        "forecast_available": True,
        "forecast_quality_status": "LOW_CONFIDENCE",
        "selective_status": "NO_EDGE",
        "selective_authorized": False,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "path_side": "BUY",
        "source_image_size": [1000, 800],
        "features": [
            {"center_x_px": 610.0, "relative_price_location": 0.44},
        ],
        "forecast_path": [
            {
                "step": step,
                "expected_open_norm": 0.44 + (step - 1) * 0.004,
                "expected_close_norm": 0.44 + step * 0.004,
                "expected_range_norm": 0.01,
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step in range(1, 13)
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    composite = next(
        row
        for row in state["overlay_objects"]
        if row.get("role") == "lstm_forecast_composite_low_confidence"
    )

    assert abs(float(composite["line_points"][0][0]) / 1434.0 - 0.61) < 1e-6
    assert len(cast(list[dict[str, Any]], composite["forecast_candles"])) == 12


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_direct_lstm_path_snaps_to_matching_latest_candle_close(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=8)
    latest = candles[-1]
    center_x = 0.5 * (float(latest["bbox"][0]) + float(latest["bbox"][2]))
    close_y = float(latest["bbox"][1])  # latest fixture candle is BUY
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "frame_id": 14494,
        "fresh": True,
        "forecast_available": True,
        "forecast_quality_status": "LOW_CONFIDENCE",
        "selective_status": "NO_EDGE",
        "selective_authorized": False,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "path_side": "BUY",
        "source_image_size": [1434, 847],
        "features": [
            {
                "center_x_px": center_x,
                # Deliberately differs from the exact rendered body close.
                "relative_price_location": 0.50,
            }
        ],
        "forecast_path": [
            {
                "step": step,
                "expected_open_norm": 0.50 + (step - 1) * 0.004,
                "expected_close_norm": 0.50 + step * 0.004,
                "expected_range_norm": 0.01,
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step in range(1, 13)
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(
        session,
        overlay_mode="LSTM_STUDY",
        now_epoch=120.0,
    )
    composite = next(
        row
        for row in state["overlay_objects"]
        if row.get("role") == "lstm_forecast_composite_low_confidence"
    )

    assert abs(float(composite["line_points"][0][0]) - center_x) < 0.002
    assert abs(float(composite["line_points"][0][1]) - close_y) < 0.002
    assert len(cast(list[dict[str, Any]], composite["forecast_candles"])) == 12


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_direct_lstm_path_does_not_snap_to_adjacent_tracker_candle(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    candles = _install_visible_candles(session, count=8)
    causal = candles[-2]
    tracker_latest = candles[-1]
    causal_center_x = 0.5 * (
        float(causal["bbox"][0]) + float(causal["bbox"][2])
    )
    tracker_center_x = 0.5 * (
        float(tracker_latest["bbox"][0]) + float(tracker_latest["bbox"][2])
    )
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "frame_id": 14494,
        "fresh": True,
        "forecast_available": True,
        "forecast_quality_status": "LOW_CONFIDENCE",
        "selective_status": "NO_EDGE",
        "selective_authorized": False,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "path_side": "BUY",
        "source_image_size": [1434, 847],
        "features": [
            {
                "center_x_px": causal_center_x,
                "relative_price_location": 0.50,
            }
        ],
        "forecast_path": [
            {
                "step": step,
                "expected_open_norm": 0.50 + (step - 1) * 0.004,
                "expected_close_norm": 0.50 + step * 0.004,
                "expected_range_norm": 0.01,
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step in range(1, 13)
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(
        session,
        overlay_mode="LSTM_STUDY",
        now_epoch=120.0,
    )
    composite = next(
        row
        for row in state["overlay_objects"]
        if row.get("role") == "lstm_forecast_composite_low_confidence"
    )

    start_x = float(composite["line_points"][0][0])
    assert abs(start_x - causal_center_x) < 0.002
    assert abs(start_x - tracker_center_x) > 10.0
    assert len(cast(list[dict[str, Any]], composite["forecast_candles"])) == 12


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_lstm_forecast_overlay_visually_marks_authorized_path_without_candle_boxes(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "fresh": True,
        "forecast_available": True,
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
        "confidence": 0.89,
        "side": "BUY",
        "path_side": "BUY",
        "selective_side": "BUY",
        "selective_status": "AUTHORIZED",
        "selective_authorized": True,
        "production_authorized": True,
        "artifact_production_gate_passed": True,
        "trade_authorization_status": "AUTHORIZED",
        "source_image_size": [1000, 800],
        "features": [{"relative_price_location": 0.50}],
        "forecast_path": [
            {
                "step": step,
                "expected_close_norm": 0.50 + step * 0.01,
                "close_lower_90_norm": 0.48 + step * 0.008,
                "close_upper_90_norm": 0.52 + step * 0.012,
                "selective_status": "AUTHORIZED",
                "selective_authorized": True,
            }
            for step in range(1, 13)
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    rows = [
        row
        for row in state["overlay_objects"]
        if row.get("type") == "LSTM_STUDY"
    ]

    assert len(rows) == 1
    assert all(row.get("layer") == "prediction_path" for row in rows)
    assert all(row.get("side") == "BUY" for row in rows)
    assert all(str(row.get("role")).endswith("_authorized") for row in rows)
    assert not any(row.get("role") == "lstm_study" for row in rows)
    assert rows[0].get("role") == "lstm_forecast_composite_authorized"
    assert len(cast(list[dict[str, Any]], rows[0].get("forecast_candles"))) == 12


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_lstm_path_cannot_authorize_when_production_gate_is_false(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "fresh": True,
        "side": "BUY",
        "path_side": "BUY",
        "selective_status": "AUTHORIZED",
        "selective_authorized": True,
        "production_authorized": False,
        "artifact_production_gate_passed": False,
        "source_image_size": [1000, 800],
        "features": [{"relative_price_location": 0.50}],
        "forecast_path": [
            {
                "step": step,
                "expected_close_norm": 0.50 + step * 0.01,
                "selective_status": "AUTHORIZED",
                "selective_authorized": True,
            }
            for step in range(1, 13)
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(
        session,
        overlay_mode="LSTM_STUDY",
        now_epoch=120.0,
    )
    row = next(
        row
        for row in state["overlay_objects"]
        if row.get("type") == "LSTM_STUDY"
    )

    assert row["side"] == "HOLD"
    assert row["trade_authorization_status"] == "NO_EDGE"
    assert not str(row["role"]).endswith("_authorized")


@pytest.mark.skip(reason="retired public LSTM path is no longer renderable")
def test_lstm_path_never_inherits_authority_from_conflicting_top_level_metadata(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "fresh": True,
        "confidence": 0.81,
        "side": "BUY",
        "path_side": "SELL",
        "selective_side": "BUY",
        "selective_status": "AUTHORIZED",
        "selective_authorized": True,
        "source_image_size": [1000, 800],
        "features": [{"relative_price_location": 0.50}],
        "forecast_path": [
            {
                "step": step,
                "expected_close_norm": 0.50 - step * 0.01,
                "close_lower_90_norm": 0.48 - step * 0.012,
                "close_upper_90_norm": 0.52 - step * 0.008,
                "direction": "BUY",
                "movement_direction": "SELL",
                "selective_status": "NO_EDGE",
                "selective_authorized": False,
            }
            for step in range(1, 13)
        ],
    }
    _strict_lstm_forecast(session["latest_signal"]["lstm_contribution"])

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)
    rows = [
        row
        for row in state["overlay_objects"]
        if row.get("type") == "LSTM_STUDY"
    ]

    assert len(rows) == 1
    assert all(row.get("side") == "HOLD" for row in rows)
    assert all(str(row.get("role")).endswith("_no_edge") for row in rows)
    assert not any(str(row.get("role")).endswith("_authorized") for row in rows)
    assert rows[0].get("forecast_direction") == "SELL"
    assert rows[0].get("direction_conflict") is True


def test_legacy_body_colour_artifact_never_draws_a_future_price_path(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    _install_visible_candles(session, count=8)
    session["latest_signal"]["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "fresh": True,
        "legacy_restored": True,
        "production_authorized": False,
        "confidence": 0.90,
        "side": "SELL",
        "path_side": "SELL",
        "source_image_size": [1000, 800],
        "features": [{"relative_price_location": 0.50}],
        "forecast_path": [
            {
                "step": step,
                "expected_close_norm": 0.50 - step * 0.01,
                "direction": "SELL",
                "movement_direction": "SELL",
            }
            for step in range(1, 4)
        ],
    }

    state = build_live_state_v3(session, overlay_mode="LSTM_STUDY", now_epoch=120.0)

    assert not [
        row
        for row in state["overlay_objects"]
        if row.get("type") == "LSTM_STUDY"
    ]


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
    inspector_only = [row for row in resolved if row.get("display_state") == "INSPECTOR_LABEL"]

    assert audit["rendered_count"] == 24
    assert inspector_only
    assert all(row["geometry_visible"] is True for row in inspector_only)
    assert all(row["label_visible"] is False for row in inspector_only)
    assert all(row["style"]["fill_opacity"] == 0.0 for row in inspector_only)
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
    assert resolved[0]["precision_rejected"] is True
    assert "stale_source_frame_id" in resolved[0]["precision_flags"]
    assert audit["precision_report"]["stale_frame_id"] == 1
    assert audit["rendered_count"] == 0


def test_precision_resolver_rejects_missing_transform_in_live_mode(tmp_path: Path) -> None:
    session = _session(tmp_path)
    scene = build_broker_scene_graph_v3(session).as_dict()["scene_graph"]
    overlays: list[dict[str, Any]] = [
        {
            "overlay_id": "missing-transform",
            "object_id": "missing-transform",
            "track_id": "missing-transform",
            "type": "SUPPLY_ZONE",
            "side": "SELL",
            "source_agent": "test",
            "frame_id": 14494,
            "sequence_id": "seq-current",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "WICK_REJECTION_CLUSTER",
            "anchor_candles": [4, 8],
            "anchor_wick_points": [[320, 180], [460, 184]],
            "bounds": [300, 170, 490, 202],
            "truth_score": 0.9,
            "visible_modes": ["CLEAN_LIVE", "INSPECTOR"],
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="CLEAN_LIVE",
        current_side="SELL",
        frame_id=14494,
    )

    assert resolved[0]["precision_rejected"] is True
    assert "missing_or_pending_chart_transform" in resolved[0]["precision_flags"]
    assert audit["precision_report"]["missing_transform"] == 1
    assert audit["rendered_count"] == 0


def test_precision_resolver_projects_all_trendline_anchor_geometry() -> None:
    scene = {
        "frame_id": 88,
        "chart_region_chart_bounds": [0, 0, 800, 500],
        "plot_area_chart_bounds": [100, 50, 700, 450],
    }
    overlays: list[dict[str, Any]] = [
        {
            "overlay_id": "normalized-trend",
            "object_id": "normalized-trend",
            "track_id": "normalized-trend",
            "type": "SUPPORT_TRENDLINE",
            "side": "BUY",
            "source_agent": "test",
            "frame_id": 88,
            "sequence_id": "seq-88",
            "chart_transform_id": "chart-88",
            "coordinate_mode": "PLOT_AREA_NORMALIZED",
            "anchor_type": "TRENDLINE_TOUCH_POINTS",
            "anchor_candles": [3, 9],
            "anchor_wick_points": [[0.1, 0.8], [0.5, 0.5]],
            "trendline_touch_points": [[0.1, 0.8], [0.5, 0.5]],
            "touch_points": [[0.1, 0.8], [0.5, 0.5]],
            "line_points": [[0.1, 0.8], [0.5, 0.5], [0.9, 0.2]],
            "bounds": [0.1, 0.2, 0.9, 0.8],
            "truth_score": 0.9,
            "confidence": 0.9,
            "visible_modes": ["TRENDLINES", "CLEAN_LIVE", "INSPECTOR"],
        }
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="TRENDLINES",
        current_side="BUY",
        frame_id=88,
    )

    assert audit["rendered_count"] == 1
    trendline = resolved[0]
    assert trendline["anchor_wick_points"] == [[160.0, 370.0], [400.0, 250.0]]
    assert trendline["trendline_touch_points"] == [[160.0, 370.0], [400.0, 250.0]]
    assert trendline["touch_points"] == trendline["anchor_wick_points"]
    assert trendline["line_points"][:2] == trendline["anchor_wick_points"]


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


def test_inspector_labels_only_the_latest_current_candle() -> None:
    scene = {
        "frame_id": 220,
        "plot_area_chart_bounds": [0, 0, 1000, 700],
        "chart_region_chart_bounds": [0, 0, 1000, 700],
    }
    overlays = [
        {
            "overlay_id": f"candle-{index}",
            "object_id": f"candle-{index}",
            "track_id": f"candle-{index}",
            "type": "CURRENT_CANDLE",
            "side": "SELL",
            "source_agent": "market_object_tracker_v3",
            "source_path": f"tracking_summary.tracked_candles[{index}]",
            "frame_id": 220,
            "sequence_id": "seq-220",
            "chart_transform_id": "chart-220",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "CANDLE",
            "anchor_candles": [index],
            "candle_index": index,
            "is_latest_candle": index == 3,
            "bounds": [700 + index * 35, 260 + index * 20, 712 + index * 35, 390 + index * 20],
            "truth_score": 0.92,
            "confidence": 0.92,
            "lifecycle_state": "ACTIVE",
            "visible_modes": ["CANDLES", "INSPECTOR"],
            "visible_default": index == 3,
            "label": "NOW" if index == 3 else "CANDLES",
            "display_label": "NOW" if index == 3 else "CANDLES",
            "label_hidden": index != 3,
        }
        for index in range(4)
    ]

    resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="INSPECTOR",
        current_side="SELL",
        frame_id=220,
    )
    candles = [row for row in resolved if row.get("type") == "CURRENT_CANDLE"]
    visible_labels = [row for row in candles if row.get("label_hidden") is not True]

    assert len(candles) == 4
    assert len(visible_labels) == 1
    assert visible_labels[0]["anchor_candles"] == [3]
    assert visible_labels[0]["display_label"] == "NOW"
    assert all(row.get("label_hidden") is True for row in candles if row is not visible_labels[0])
    assert audit["precision_report"]["duplicate_now_hidden"] == 3


def test_inspector_uses_thin_liquidity_bands_and_one_inline_label_per_family() -> None:
    scene = {
        "frame_id": 221,
        "plot_area_chart_bounds": [0, 0, 1000, 700],
        "chart_region_chart_bounds": [0, 0, 1000, 700],
    }

    def overlay(
        overlay_id: str,
        overlay_type: str,
        bounds: list[float],
        *,
        lifecycle: str = "ACTIVE",
        source_path: str,
        line_y: float | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "overlay_id": overlay_id,
            "object_id": overlay_id,
            "track_id": overlay_id,
            "type": overlay_type,
            "side": "SELL",
            "source_agent": "market_object_tracker_v3",
            "source_path": source_path,
            "frame_id": 221,
            "sequence_id": "seq-221",
            "chart_transform_id": "chart-221",
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "CANDLE_CLUSTER",
            "anchor_candles": [8, 9],
            "touch_points": [[bounds[0] + 12, (bounds[1] + bounds[3]) * 0.5], [bounds[2] - 12, (bounds[1] + bounds[3]) * 0.5]],
            "bounds": bounds,
            "truth_score": 0.86,
            "confidence": 0.86,
            "lifecycle_state": lifecycle,
            "visible_modes": ["SMART_MONEY", "TRIGGERS", "INSPECTOR"],
            "visible_default": True,
            "label": overlay_type.replace("_", " "),
            "label_hidden": False,
        }
        if line_y is not None:
            row["line_y"] = line_y
            row["price_level_y"] = line_y
        return row

    overlays = [
        overlay(
            "retest-primary",
            "RETEST_BOX",
            [760, 360, 830, 405],
            source_path="tracking_summary.projection.zones[1]",
        ),
        overlay(
            "retest-secondary",
            "RETEST_BOX",
            [650, 300, 720, 345],
            source_path="tracking_summary.structure_boxes[0].trigger_window",
        ),
        overlay(
            "retest-history",
            "RETEST_BOX",
            [260, 240, 330, 285],
            lifecycle="HISTORICAL",
            source_path="tracking_summary.historical_structure[0].trigger_window",
        ),
        overlay(
            "pool-near",
            "LIQUIDITY_POOL",
            [420, 410, 920, 500],
            source_path="tracking_summary.smart_money_context.liquidity_pools[0]",
            line_y=455,
        ),
        overlay(
            "pool-far",
            "LIQUIDITY_POOL",
            [250, 120, 900, 220],
            source_path="tracking_summary.smart_money_context.liquidity_pools[1]",
            line_y=170,
        ),
    ]
    overlays[3]["distance_to_latest_norm"] = 0.08
    overlays[4]["distance_to_latest_norm"] = 0.42

    resolved, _audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        mode="INSPECTOR",
        current_side="SELL",
        frame_id=221,
    )
    retests = [row for row in resolved if row.get("type") == "RETEST_BOX"]
    pools = [row for row in resolved if row.get("type") == "LIQUIDITY_POOL"]

    assert all(row["bounds"][3] - row["bounds"][1] <= 10.0 for row in pools)
    assert len([row for row in retests if row.get("label_hidden") is not True]) == 1
    assert len([row for row in pools if row.get("label_hidden") is not True]) == 1
    historical = next(row for row in retests if row["overlay_id"] == "retest-history")
    assert historical["display_state"] == "GHOSTED"
    assert historical["label_hidden"] is True


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
