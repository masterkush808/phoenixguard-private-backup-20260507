from __future__ import annotations

from pathlib import Path

from phoenixguard.decision.book_strategy_full_stack_v3 import _trendline_contracts


def _geometry_candles() -> list[dict[str, float | bool]]:
    candles: list[dict[str, float | bool]] = []
    for index in range(18):
        close_y = 90.0 + float(index % 3)
        if index == 16:
            close_y = 75.0
        elif index == 17:
            close_y = 90.0
        candles.append(
            {
                "x": float(index * 10),
                "open_y": close_y + 3.0,
                "close_y": close_y,
                "top_y": 80.0 if index in {2, 6, 10, 14} else min(close_y, close_y + 3.0) - 1.0,
                "bottom_y": max(close_y, close_y + 3.0) + 1.0,
                "is_closed": True,
            }
        )
    return candles


def test_false_breach_redraw_zone_binding_and_72_candle_projection() -> None:
    candles = _geometry_candles()
    pivots = [
        {
            "pivot_id": f"high-{index}",
            "kind": "HIGH",
            "tier": "INTERMEDIATE",
            "index": index,
            "x": float(index * 10),
            "y": 80.0,
        }
        for index in (2, 6, 10, 14)
    ]
    structure = {
        "internal_pivots": [],
        "intermediate_pivots": pivots,
        "external_pivots": [],
    }
    trendline = {
        "trendline_id": "resistance-main",
        "geometry_contract_accepted": True,
        "role": "RESISTANCE",
        "touch_count": 4,
        "touch_candle_indices": [2, 6, 10, 14],
        "line_points": [[20.0, 80.0], [60.0, 80.0]],
        "timeframe": "M15",
    }
    zone = {
        "zone_id": "resistance-zone",
        "role": "RESISTANCE",
        "left_x": 0.0,
        "right_x": 170.0,
        "top_y": 87.0,
        "bottom_y": 96.0,
    }

    result = _trendline_contracts(candles, [trendline], [zone], structure, "M15")

    assert result["historical_zone_binding_complete"] is True
    assert any(result["candle_zone_location_history"].values())
    assert result["false_breach_redraw_count"] == 1
    replacement = result["contracts"][0]["replacement_trendline"]
    assert replacement["replaces_trendline_id"] == "resistance-main"
    assert len(replacement["line_points_v3"]) == 2
    target = result["opposing_targets"]["BUY"]
    assert target["horizon_72_x_px"] > candles[-1]["x"]
    assert target["intersection_semantics"] == "PROJECTED_LINE_INTERSECTION_NOT_GUARANTEED_PRICE"


def test_live_dashboard_exposes_complete_book_rule_architecture() -> None:
    dashboard = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    )
    source = dashboard.read_text(encoding="utf-8")

    for contract_id in (
        "book-rule-architecture-panel",
        "book-rule-status",
        "book-rule-action",
        "book-rule-scenario",
        "book-rule-trigger",
        "book-rule-invalidation",
        "book-rule-structure",
        "book-rule-trendlines",
        "book-rule-hlz",
        "book-rule-setups",
        "book-rule-candles",
        "book-rule-opposing-force",
        "book-rule-provenance",
        "book-rule-overlays",
    ):
        assert f'id="{contract_id}"' in source

    assert "function renderBookRuleArchitecture(payload)" in source
    assert "book_rule_action_signal_v3" in source
    assert "Primary strategist provider" in source
    assert "Pair-conditioned horizon" not in source
    assert '"book_rule_line"' in source
