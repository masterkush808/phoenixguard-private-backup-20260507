from __future__ import annotations

from phoenixguard.decision.book_strategy_forecast_v3 import (
    build_book_strategy_forecast_control_v3,
)
from phoenixguard.decision.candlestick_rule_catalog_v3 import (
    ADVANCED_CANDLE_FILTER_CATALOG_V3,
    CANDLESTICK_RULE_CATALOG_V3,
    evaluate_candlestick_catalog_v3,
)
from phoenixguard.decision.book_strategy_full_stack_v3 import (
    build_pair_conditioned_horizon_v3,
    evaluate_full_non_indicator_book_stack_v3,
)
from phoenixguard.decision.scene_forecast_contributor_v3 import (
    build_scene_forecast_contribution_v3,
)


def _candles(
    *,
    last_close_y: float = 104.0,
    bearish_last: bool = True,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    closes = [112.0, 110.0, 108.0, 106.0, 105.0, 103.0, 101.0, last_close_y]
    for index, close_y in enumerate(closes):
        open_y = close_y - 3.0 if bearish_last and index == len(closes) - 1 else close_y + 2.0
        rows.append(
            {
                "track_id": index,
                "source_index": index,
                "x_center_px": 20.0 + index * 10.0,
                "bbox": [
                    17.0 + index * 10.0,
                    min(open_y, close_y) - 2.0,
                    23.0 + index * 10.0,
                    max(open_y, close_y) + 2.0,
                ],
                "open_y_px": open_y,
                "close_y_px": close_y,
                "wick_top_y_px": min(open_y, close_y) - 2.0,
                "wick_bottom_y_px": max(open_y, close_y) + 2.0,
                "direction": "BUY" if close_y < open_y else "SELL",
                "is_closed": True,
            }
        )
    return rows


def _resistance_line(touch_count: int = 3) -> dict[str, object]:
    return {
        "geometry_contract_accepted": True,
        "role": "RESISTANCE",
        "touch_count": touch_count,
        "strategy_touch_confirmed": touch_count >= 3,
        "anchor_wick_points": [[20.0, 100.0], [90.0, 100.0]],
        "line_points": [[20.0, 100.0], [90.0, 100.0]],
    }


def test_three_touch_resistance_rejection_owns_sell_forecast() -> None:
    control = build_book_strategy_forecast_control_v3(
        candles=_candles(),
        timeframe="M5",
        trendlines=[_resistance_line(3)],
        trend_directions={"major": "SELL", "inner": "SELL"},
    )

    assert control["initial_reaction_side"] == "SELL"
    assert control["forecast_side"] == "SELL"
    assert control["playbook"] == "TRENDLINE_REJECTION"
    assert len(control["phase_multipliers"]) == 72
    assert control["future_blind"] is True


def test_two_anchor_line_has_no_strategy_authority() -> None:
    control = build_book_strategy_forecast_control_v3(
        candles=_candles(),
        timeframe="M5",
        trendlines=[_resistance_line(2)],
        trend_directions={"major": "BUY", "inner": "BUY"},
    )

    assert control["trendline_context"]["mature_line_count"] == 0
    trace = {row["rule_id"]: row for row in control["rule_trace"]}
    assert trace["TRENDLINE_TWO_ANCHOR_CANDIDATE"]["observed"] is False


def test_completed_close_through_resistance_becomes_buy_breakout() -> None:
    control = build_book_strategy_forecast_control_v3(
        candles=_candles(last_close_y=94.0, bearish_last=False),
        timeframe="M5",
        trendlines=[_resistance_line(3)],
        trend_directions={"major": "BUY", "inner": "BUY"},
    )

    assert control["initial_reaction_side"] == "BUY"
    assert control["playbook"] == "BREAK_RETEST"


def test_scene_forecast_keeps_72_fluctuating_book_conditioned_horizons() -> None:
    contribution = build_scene_forecast_contribution_v3(
        candles=_candles(),
        image_size=(1280, 720),
        timeframe="M5",
        pair="GBPUSD",
        trendlines=[_resistance_line(3)],
        trend_directions={"major": "SELL", "inner": "SELL"},
        allow_foundation_model=False,
    )

    control = contribution["book_strategy_forecast_control_v3"]
    assert contribution["provider"] == "BOOK_STRATEGY_CONDITIONED_SCENE_V3"
    assert control["forecast_side"] == "SELL"
    assert len(control["horizon_directions"]) == 72
    assert len(set(control["phase_multipliers"])) > 4


def test_complete_candlestick_catalog_has_page_traceability() -> None:
    assert len(CANDLESTICK_RULE_CATALOG_V3) >= 70
    assert len(ADVANCED_CANDLE_FILTER_CATALOG_V3) >= 14
    assert all(rule["printed_pages"] and rule["pdf_pages"] for rule in CANDLESTICK_RULE_CATALOG_V3)
    required = {
        "HAMMER",
        "HANGING_MAN",
        "BULLISH_ENGULFING",
        "BEARISH_ENGULFING",
        "THREE_RIVER_MORNING_STAR",
        "THREE_RIVER_EVENING_STAR",
        "RISING_THREE_METHODS",
        "FALLING_THREE_METHODS",
        "TASUKI_UPSIDE_GAP",
        "CONCEALING_BABY_SWALLOW",
    }
    assert required <= {str(rule["rule_id"]) for rule in CANDLESTICK_RULE_CATALOG_V3}


def test_pattern_shape_without_location_has_no_directional_authority() -> None:
    rows = _candles()
    rows[-1].update(
        {
            "open_y_px": 101.0,
            "close_y_px": 100.0,
            "wick_top_y_px": 99.0,
            "wick_bottom_y_px": 108.0,
        }
    )
    normalized = [
        {
            "open": -float(row["open_y_px"]),
            "close": -float(row["close_y_px"]),
            "high": -float(row["wick_top_y_px"]),
            "low": -float(row["wick_bottom_y_px"]),
        }
        for row in rows
    ]
    result = evaluate_candlestick_catalog_v3(
        candles=normalized,
        prior_trend="SELL",
        location_side="NEUTRAL",
    )
    hammer = next(row for row in result["detections"] if row["rule_id"] == "HAMMER")
    assert hammer["directional_authority"] is False
    assert "STRUCTURAL_LOCATION_MISSING" in hammer["failed_requirements"]


def test_hlz_ote_and_htf_authority_are_exposed_in_forecast_control() -> None:
    candles = [
        {"open": 10.0, "high": 10.3, "low": 9.8, "close": 10.1},
        {"open": 10.1, "high": 10.5, "low": 10.0, "close": 10.4},
        {"open": 10.4, "high": 10.8, "low": 10.3, "close": 10.7},
        {"open": 10.7, "high": 11.0, "low": 10.6, "close": 10.9},
        {"open": 10.9, "high": 11.5, "low": 10.8, "close": 11.4},
        {"open": 11.4, "high": 12.2, "low": 11.3, "close": 12.1},
        {"open": 12.1, "high": 12.3, "low": 11.3, "close": 11.5},
        {"open": 11.5, "high": 11.6, "low": 10.7, "close": 10.9},
    ]
    control = build_book_strategy_forecast_control_v3(
        candles=candles,
        timeframe="M5",
        trend_directions={"major": "BUY", "inner": "SELL"},
        higher_timeframe_context={"timeframe": "H1", "side": "BUY"},
    )

    assert control["higher_timeframe_authority_v3"]["strictly_enforced"] is True
    assert control["forecast_side"] == "BUY"
    assert "levels" in control["fibonacci_ote_v3"]


def test_news_pivot_is_connected_without_inventing_direction() -> None:
    control = build_book_strategy_forecast_control_v3(
        candles=_candles(),
        timeframe="M5",
        trend_directions={"major": "SELL", "inner": "SELL"},
        session_context={"active_session": "LONDON"},
        news_context={"impact": "HIGH", "event_phase": "ACTIVE"},
    )

    temporal = control["session_news_context_v3"]
    assert temporal["entry_suspended_until_news_pivot"] is True
    assert temporal["direction_inferred_from_news"] is False
    assert control["forecast_side"] == "SELL"


def test_full_stack_uses_no_technical_indicators() -> None:
    stack = evaluate_full_non_indicator_book_stack_v3(
        candles=[
            {"open": 10.0 + index, "high": 10.8 + index, "low": 9.8 + index, "close": 10.6 + index, "x": float(index)}
            for index in range(18)
        ],
        timeframe="M5",
    )

    assert stack["technical_indicators_used"] is False
    assert stack["technical_indicator_scope"] == "EXCLUDED_BY_USER"
    assert stack["market_structure"]["stable_lineage"] is True


def test_pair_conditioned_horizon_contains_exactly_72_fluctuating_events() -> None:
    horizon = build_pair_conditioned_horizon_v3(
        {
            "pair_dna": {
                "profile_applied": True,
                "current_regime": "EXPANSION",
                "dominant_personality": "LONG_BODY",
            },
            "higher_timeframe": {"effective_side": "BUY"},
            "full_non_indicator_stack_v3": {},
        },
        primary_side="BUY",
        confidence=0.75,
    )

    assert len(horizon["phase_multipliers"]) == 72
    assert len(horizon["horizon_directions"]) == 72
    assert {row["phase"] for row in horizon["phases"]} >= {"IMPULSE", "REST", "PULLBACK", "CONTINUATION"}


def test_order_block_is_independently_derived_from_bms_origin() -> None:
    candles = [
        {"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.4, "x": 0.0},
        {"open": 10.4, "high": 10.6, "low": 10.0, "close": 10.1, "x": 1.0},
        {"open": 10.1, "high": 10.7, "low": 10.0, "close": 10.6, "x": 2.0},
        {"open": 10.6, "high": 10.9, "low": 10.3, "close": 10.8, "x": 3.0},
        {"open": 10.8, "high": 11.0, "low": 10.4, "close": 10.5, "x": 4.0},
        {"open": 10.5, "high": 11.8, "low": 10.5, "close": 11.7, "x": 5.0},
        {"open": 11.7, "high": 11.8, "low": 10.6, "close": 10.8, "x": 6.0},
        {"open": 10.8, "high": 11.0, "low": 10.4, "close": 10.7, "x": 7.0},
        {"open": 10.7, "high": 11.3, "low": 10.6, "close": 11.2, "x": 8.0},
        {"open": 11.2, "high": 11.7, "low": 11.0, "close": 11.6, "x": 9.0},
        {"open": 11.6, "high": 12.0, "low": 11.4, "close": 11.9, "x": 10.0},
    ]
    stack = evaluate_full_non_indicator_book_stack_v3(
        candles=candles,
        timeframe="M5",
    )

    assert stack["order_blocks"]["independently_derived"] is True
    assert all(block["causing_bms_id"] for block in stack["order_blocks"]["blocks"])
