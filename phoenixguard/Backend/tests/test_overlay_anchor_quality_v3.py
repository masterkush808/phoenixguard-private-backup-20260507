from __future__ import annotations

from typing import Any, Mapping, cast

from phoenixguard.vision.v3_overlay_contract import normalize_v3_overlay_object, overlay_is_visible, overlay_rejection_reasons


def _quality(row: Mapping[str, object]) -> Mapping[str, object]:
    value = row.get("anchor_quality")
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _quality_score(row: Mapping[str, object]) -> float:
    score = _quality(row).get("score")
    return float(score) if isinstance(score, (int, float, str)) else 0.0


def _base_overlay(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "overlay_id": "anchor-test",
        "object_id": "anchor-test",
        "track_id": "anchor-test",
        "type": "SNIPER_ENTRY_BOX",
        "side": "BUY",
        "source_agent": "market_object_tracker_v3",
        "layer": "trigger_zones",
        "frame_id": 88,
        "sequence_id": "seq-anchor-88",
        "chart_transform_id": "ct_88",
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "anchor_type": "BOX",
        "anchor_candles": [10, 11],
        "anchor_wick_points": [[180, 336], [214, 331]],
        "bounds": [170, 324, 236, 346],
        "truth_score": 0.86,
        "confidence": 0.88,
        "lifecycle_state": "ACTIVE",
        "visible_modes": ["CLEAN_LIVE", "INSPECTOR"],
        "ttl_ms": 15000,
        "reason": "anchored test overlay",
    }
    payload.update(overrides)
    return payload


def test_current_candle_anchor_latest_visible_candle() -> None:
    current = normalize_v3_overlay_object(
        _base_overlay(
            type="CURRENT_CANDLE",
            layer="recent_candles",
            label="NOW",
            anchor_type="CANDLE",
            anchor_candles=[23],
            anchor_wick_points=[],
            bounds=[498, 166, 504, 237],
        )
    )

    quality = _quality(current)

    assert current["anchor_candle_indices"] == [23]
    assert quality["has_candle_anchor"] is True
    assert _quality_score(current) >= 0.65


def test_supply_zone_anchors_to_wick_rejection_cluster() -> None:
    supply = normalize_v3_overlay_object(
        _base_overlay(
            type="SUPPLY_ZONE",
            side="SELL",
            layer="supply_demand",
            label="SUPPLY",
            anchor_type="wick_rejection_cluster",
            anchor_candles=[15, 16, 17],
            anchor_price_band={"top_y": 188.0, "bottom_y": 204.0, "source": "wick_touch_cluster"},
            anchor_wick_points=[[410, 188], [437, 194], [462, 190]],
            touch_points=[[410, 188], [437, 194], [462, 190]],
            bounds=[396, 186, 488, 206],
            visible_modes=["SUPPLY_DEMAND", "CLEAN_LIVE", "INSPECTOR"],
        )
    )

    quality = _quality(supply)

    assert supply["anchor_evidence_status"] == "VALID"
    assert supply["anchor_wick_points"] == [[410.0, 188.0], [437.0, 194.0], [462.0, 190.0]]
    assert quality["has_wick_anchor"] is True
    assert _quality_score(supply) >= 0.85


def test_support_trendline_anchors_to_two_wick_touch_points() -> None:
    support = normalize_v3_overlay_object(
        _base_overlay(
            type="SUPPORT_TRENDLINE",
            side="BUY",
            layer="trendlines",
            label="SUPPORT TRENDLINE",
            anchor_type="LINE",
            anchor_candles=[3, 9],
            anchor_wick_points=[[110, 420], [380, 318]],
            touch_points=[[110, 420], [380, 318]],
            line_points=[[110, 420], [380, 318], [600, 235]],
            bounds=None,
            visible_modes=["TRENDLINES", "CLEAN_LIVE", "INSPECTOR"],
        ),
        strict=False,
    )

    quality = _quality(support)

    assert support["anchor_evidence_status"] == "VALID"
    line_points = support.get("line_points")
    assert isinstance(line_points, list)
    assert line_points[:2] == [[110.0, 420.0], [380.0, 318.0]]
    assert quality["has_wick_anchor"] is True
    assert _quality_score(support) >= 0.85


def test_unanchored_overlay_routes_to_diagnostics() -> None:
    floating = _base_overlay(
        type="SUPPLY_ZONE",
        side="SELL",
        layer="supply_demand",
        anchor_candles=[],
        anchor_wick_points=[],
        touch_points=[],
        bounds=[100, 100, 240, 180],
        visible_modes=["CLEAN_LIVE", "INSPECTOR"],
    )

    reasons = overlay_rejection_reasons(floating, "CLEAN_LIVE")

    assert "missing_anchor_evidence" in reasons
    assert any(reason.startswith("anchor_quality_below_live_threshold") for reason in reasons)
    assert overlay_is_visible(floating, "CLEAN_LIVE") is False
