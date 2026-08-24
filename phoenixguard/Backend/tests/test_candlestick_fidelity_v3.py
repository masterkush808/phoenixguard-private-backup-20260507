"""Tam candlestick fidelity tests — corrected geometries from the completeness audit.

Every case encodes the book's rules of recognition (page-cited in comments) so the
strategist's candlestick evidence layer cannot silently drift back to loose or
wrong pattern encodings.
"""

from __future__ import annotations

from typing import Any

import phoenixguard.decision.book_strategy_full_stack_v3 as book_strategy_full_stack_v3
import phoenixguard.decision.candlestick_rule_catalog_v3 as candlestick_rule_catalog_v3
from phoenixguard.decision.candlestick_rule_catalog_v3 import CANDLESTICK_RULE_CATALOG_V3

_matches = getattr(candlestick_rule_catalog_v3, "_matches")
_sakata_state = getattr(book_strategy_full_stack_v3, "_sakata_state")


def _bar(open_: float, close: float, high: float, low: float) -> dict[str, float]:
    return {"open": open_, "close": close, "high": high, "low": low}


def _spec_bars(rule_id: str) -> int:
    return int(next(rule["bars"] for rule in CANDLESTICK_RULE_CATALOG_V3 if rule["rule_id"] == rule_id))


def test_doji_harami_requires_long_mother_candle() -> None:
    """p.78: first day is a LONG black (bottom) / white (top) candle followed by a doji."""
    mother_black = _bar(110.0, 105.0, 111.0, 104.0)
    mother_small = _bar(107.4, 106.6, 108.0, 106.0)
    doji = _bar(105.35, 105.25, 106.2, 104.4)

    assert _matches("DOJI_BOTTOM", [mother_black, doji]) is True
    assert _matches("DOJI_BOTTOM", [mother_small, doji]) is False
    white_mother = _bar(105.0, 110.0, 111.0, 104.0)
    assert _matches("DOJI_TOP", [white_mother, doji]) is True
    assert _matches("DOJI_TOP", [mother_black, doji]) is False


def test_tam_inside_out_is_a_two_day_pattern() -> None:
    """pp.95-96: day 1 long black; day 2 opens inside the body, closes above day-1 open."""
    day1 = _bar(110.0, 105.0, 111.0, 104.0)
    day2 = _bar(106.5, 112.0, 112.5, 106.2)

    assert _spec_bars("WHITE_INSIDE_OUT_UP") == 2
    assert _matches("WHITE_INSIDE_OUT_UP", [day1, day2]) is True
    weak_close = _bar(106.5, 109.0, 112.5, 106.2)
    assert _matches("WHITE_INSIDE_OUT_UP", [day1, weak_close]) is False

    bear1 = _bar(105.0, 110.0, 111.0, 104.0)
    bear2 = _bar(108.5, 103.0, 108.8, 102.5)
    assert _spec_bars("BLACK_INSIDE_OUT_DOWN") == 2
    assert _matches("BLACK_INSIDE_OUT_DOWN", [bear1, bear2]) is True


def test_doji_star_gaps_below_the_real_body() -> None:
    """p.122 + p.78 rule 4: black mother; doji gaps below the BODY, not just the range."""
    mother = _bar(110.0, 105.0, 111.0, 104.0)
    star_body_gap = _bar(104.9, 104.8, 104.95, 103.0)

    assert _matches("DOJI_STAR_BOTTOM", [mother, star_body_gap]) is True
    overlapping_star = _bar(104.6, 104.55, 105.3, 103.8)
    assert _matches("DOJI_STAR_BOTTOM", [mother, overlapping_star]) is False

    top_mother = _bar(105.0, 110.0, 111.0, 104.0)
    top_star = _bar(110.1, 110.0, 111.8, 110.05)
    assert _matches("DOJI_STAR_TOP", [top_mother, top_star]) is True


def test_three_river_stars_use_book_triggers() -> None:
    """p.131: star gaps below candle-1 body; long day 3 closes beyond candles 1 and 2 highs."""
    first = _bar(120.0, 113.0, 121.0, 112.0)
    star = _bar(110.9, 110.2, 111.9, 109.0)
    third = _bar(110.5, 122.0, 122.5, 110.0)

    assert _matches("THREE_RIVER_MORNING_STAR", [first, star, third]) is True
    shallow_third = _bar(110.5, 116.0, 116.5, 110.0)
    assert _matches("THREE_RIVER_MORNING_STAR", [first, star, shallow_third]) is False

    top_first = _bar(112.0, 119.0, 119.5, 111.0)
    top_star = _bar(121.2, 121.35, 122.4, 120.6)
    top_third = _bar(121.9, 110.0, 122.0, 109.5)
    assert _matches("THREE_RIVER_EVENING_STAR", [top_first, top_star, top_third]) is True


def test_tri_star_requires_true_gap_pairs() -> None:
    """p.134: day-2 doji gaps below day-1 doji; day-3 doji gaps back above day-2."""
    d1 = _bar(100.0, 100.0, 100.6, 99.4)
    d2 = _bar(98.6, 98.55, 99.0, 98.0)
    d3 = _bar(101.2, 101.3, 101.8, 100.4)

    assert _matches("TRI_STAR_BOTTOM", [d1, d2, d3]) is True

    no_gap_low = _bar(99.2, 99.15, 99.7, 98.7)
    assert _matches("TRI_STAR_BOTTOM", [d1, no_gap_low, d3]) is False

    u1 = _bar(100.0, 100.0, 100.6, 99.4)
    u2 = _bar(101.4, 101.45, 102.0, 101.0)
    u3 = _bar(99.0, 98.95, 99.5, 98.4)
    assert _matches("TRI_STAR_TOP", [u1, u2, u3]) is True


def test_breakaway_allows_mixed_small_day_colours() -> None:
    """pp.137-138: days 2-4 are small with lower lows (colours free); day 5 long white."""
    first = _bar(130.0, 124.0, 130.5, 123.5)
    gap_down = _bar(121.9, 121.4, 122.4, 120.9)
    small_up = _bar(121.0, 121.45, 122.0, 120.5)
    small_down = _bar(121.2, 120.85, 121.9, 120.1)
    fifth = _bar(120.6, 124.5, 125.0, 120.3)

    rows = [first, gap_down, small_up, small_down, fifth]
    assert _matches("BREAKAWAY_THREE_NEW_PRICE_BOTTOM", rows) is True
    short_close = _bar(120.6, 122.2, 122.6, 120.2)
    assert _matches("BREAKAWAY_THREE_NEW_PRICE_BOTTOM", [first, gap_down, small_up, small_down, short_close]) is False

    top_first = _bar(124.0, 130.0, 130.5, 123.5)
    top_gap = _bar(132.15, 132.55, 133.0, 131.6)
    top_small_down = _bar(132.9, 132.5, 133.3, 132.0)
    top_small_up = _bar(132.6, 133.0, 133.7, 132.4)
    top_fifth = _bar(133.0, 129.0, 134.0, 128.5)
    assert _matches(
        "BREAKAWAY_THREE_NEW_PRICE_TOP",
        [top_first, top_gap, top_small_down, top_small_up, top_fifth],
    ) is True


def test_three_gaps_require_fifth_fill_candle() -> None:
    """pp.143-144: four gapped blacks, then a WHITE fifth closing above the fourth high."""
    b0 = _bar(122.0, 118.2, 122.0, 118.0)
    b1 = _bar(117.6, 114.4, 117.9, 114.0)
    b2 = _bar(113.6, 110.5, 113.9, 110.0)
    b3 = _bar(109.6, 106.4, 109.9, 106.0)
    fill = _bar(106.2, 112.5, 113.0, 105.8)

    assert _spec_bars("BULLISH_BLACK_THREE_GAPS") == 5
    rows = [b0, b1, b2, b3, fill]
    assert _matches("BULLISH_BLACK_THREE_GAPS", rows) is True
    assert _matches("BULLISH_BLACK_THREE_GAPS", rows[:4]) is False

    w0 = _bar(106.0, 110.0, 110.2, 105.8)
    w1 = _bar(110.6, 114.0, 114.2, 110.4)
    w2 = _bar(114.6, 118.0, 118.2, 114.4)
    w3 = _bar(118.6, 122.0, 122.2, 118.4)
    cover = _bar(122.0, 115.5, 122.2, 115.0)
    assert _spec_bars("BEARISH_WHITE_THREE_GAPS") == 5
    assert _matches("BEARISH_WHITE_THREE_GAPS", [w0, w1, w2, w3, cover]) is True


def test_mat_hold_pullback_floor_and_day_two_target() -> None:
    """pp.190-192: day 2 black on an up-gap; pullback holds above day-1 midpoint;
    day 5 closes above DAY-2's high."""
    big_white = _bar(100.0, 120.0, 121.0, 99.5)
    gap_black = _bar(123.0, 122.4, 124.0, 122.0)
    rest_a = _bar(122.5, 122.1, 123.4, 121.8)
    rest_b = _bar(122.2, 121.9, 123.0, 121.6)
    fifth = _bar(122.6, 125.2, 125.6, 122.2)

    rows = [big_white, gap_black, rest_a, rest_b, fifth]
    assert _matches("BULLISH_MAT_HOLD", rows) is True

    white_day2 = _bar(122.0, 123.0, 124.0, 121.9)
    assert _matches("BULLISH_MAT_HOLD", [big_white, white_day2, rest_a, rest_b, fifth]) is False

    late_fifth = _bar(122.6, 123.8, 124.0, 122.2)
    assert _matches("BULLISH_MAT_HOLD", [big_white, gap_black, rest_a, rest_b, late_fifth]) is False

    big_black = _bar(120.0, 100.0, 120.5, 99.0)
    gap_white = _bar(97.05, 97.25, 98.2, 96.4)
    down_rest_a = _bar(97.6, 97.2, 98.0, 96.8)
    down_rest_b = _bar(97.4, 97.0, 97.8, 96.4)
    bear_fifth = _bar(97.2, 94.6, 97.4, 94.2)
    assert _matches(
        "BEARISH_MAT_HOLD",
        [big_black, gap_white, down_rest_a, down_rest_b, bear_fifth],
    ) is True


def test_soldiers_and_crows_must_close_at_their_extreme() -> None:
    """pp.147-148 rule 4: upper/lower shadows must be tiny for soldiers/crows."""
    clean = [
        _bar(100.0, 104.0, 104.2, 99.8),
        _bar(104.0, 108.0, 108.2, 103.8),
        _bar(108.0, 112.0, 112.2, 107.8),
    ]
    assert _matches("THREE_WHITE_SOLDIERS", clean) is True

    shadowed = clean[:2] + [_bar(108.0, 112.0, 115.0, 107.8)]
    assert _matches("THREE_WHITE_SOLDIERS", shadowed) is False

    crows = [
        _bar(112.0, 108.0, 112.2, 107.8),
        _bar(108.0, 104.0, 108.2, 103.8),
        _bar(104.0, 100.0, 104.2, 99.8),
    ]
    assert _matches("THREE_BLACK_CROWS", crows) is True
    tailed = crows[:2] + [_bar(104.0, 100.0, 104.2, 96.0)]
    assert _matches("THREE_BLACK_CROWS", tailed) is False


def test_separating_lines_require_belt_hold_open() -> None:
    """pp.172-176: opposite-colour candle opening at prior open, closing Bozu-shaped."""
    prior = _bar(110.0, 104.0, 111.0, 103.5)
    bull = _bar(110.0, 115.0, 115.4, 109.8)
    assert _matches("BULLISH_SEPARATING_LINES", [prior, bull]) is True

    tailed = _bar(110.0, 115.0, 115.4, 107.0)
    assert _matches("BULLISH_SEPARATING_LINES", [prior, tailed]) is False


def _sakata_fixture(
    pivot_highs: list[float],
    pivot_lows: list[float],
    last_rows: list[dict[str, float]] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    candles: list[dict[str, float]] = []
    price = 100.0
    for index in range(12):
        candles.append(_bar(price + 1.0, price - 1.0, price + 1.4, price - 1.4))
        price += 0.5 if index % 2 else -0.5
    if last_rows:
        candles.extend(last_rows)
    pivots: dict[str, Any] = {
        "internal_pivots": [],
        "intermediate_pivots": (
            [{"kind": "HIGH", "price": value, "index": offset} for offset, value in enumerate(pivot_highs)]
            + [{"kind": "LOW", "price": value, "index": offset} for offset, value in enumerate(pivot_lows)]
        ),
        "external_pivots": [],
    }
    return candles, pivots


def test_three_buddha_top_and_inverted_buddha_detect_from_pivots() -> None:
    """pp.256-258: head-and-shoulders — middle mountain HIGHER than near-equal shoulders."""
    candles, pivots = _sakata_fixture([100.0, 110.0, 100.5], [90.0, 80.0, 90.4])

    methods = {row["method"]: row["side"] for row in _sakata_state(candles, pivots)["active_methods"]}

    assert methods.get("THREE_BUDDHA_TOP") == "SELL"
    assert methods.get("INVERTED_THREE_BUDDHA_TOP") == "BUY"

    sunken_head_candles, sunken_head_pivots = _sakata_fixture([102.0, 100.0, 101.5], [])
    sunken_methods = {
        row["method"]
        for row in _sakata_state(sunken_head_candles, sunken_head_pivots)["active_methods"]
    }
    assert "THREE_BUDDHA_TOP" not in sunken_methods


def test_fry_pan_bottom_detects_shallow_arc_with_breakout() -> None:
    """p.256/259: small-candle saucer tracing lower-then-higher lows, broken by a long white."""
    arc = [
        _bar(104.0, 103.8, 105.2, 104.0),
        _bar(103.9, 103.7, 104.9, 103.4),
        _bar(103.6, 103.5, 104.5, 103.0),
        _bar(103.5, 103.6, 104.6, 103.2),
        _bar(103.7, 103.9, 104.9, 103.5),
        _bar(104.0, 104.1, 105.1, 103.7),
    ]
    trigger = _bar(104.3, 107.2, 107.4, 104.2)

    candles: list[dict[str, float]] = [
        {"open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]}
        for row in (*arc, trigger)
    ]
    state = _sakata_state(candles, {"intermediate_pivots": [], "internal_pivots": [], "external_pivots": []})
    methods = {row["method"] for row in state["active_methods"]}
    assert "FRY_PAN_BOTTOM" in methods


def test_double_top_filter_needs_valley_break() -> None:
    """p.260: two near-equal pivot highs, latest close beneath the intervening valley."""
    candles: list[dict[str, float]] = []
    _base = 100.0
    shape = [8.0, 10.0, 10.1, 4.0, 1.0, 9.9, 10.05, -2.0]
    for level in shape:
        candles.append({"open": level + 0.4, "close": level, "high": max(level + 0.6, level + 0.4), "low": min(level - 0.6, level)})
    pivots = {
        "internal_pivots": [],
        "intermediate_pivots": [
            {"kind": "HIGH", "price": 110.0, "index": 2},
            {"kind": "HIGH", "price": 110.2, "index": 6},
        ],
        "external_pivots": [],
    }

    methods = {row["method"] for row in _sakata_state(candles, pivots)["active_methods"]}
    assert "DOUBLE_TOP_FILTER" in methods


def test_simultaneous_three_wings_is_marubozu_chain() -> None:
    """pp.264-267: three blacks each opening ON the prior close, Bozu/Marubozu type."""
    wings = [
        _bar(120.0, 116.0, 120.0, 116.0),
        _bar(116.0, 112.5, 116.0, 112.5),
        _bar(112.5, 109.0, 112.5, 109.0),
    ]
    filler = [_bar(126.0, 124.0, 126.5, 123.5), _bar(124.0, 122.0, 124.5, 121.5)]

    state = _sakata_state(filler + wings, {"intermediate_pivots": [], "internal_pivots": [], "external_pivots": []})
    methods = {row["method"] for row in state["active_methods"]}
    assert "SIMULTANEOUS_THREE_WINGS" in methods

    plain_crows = [
        _bar(120.0, 116.0, 120.8, 115.2),
        _bar(116.5, 112.5, 117.0, 112.0),
        _bar(113.0, 109.0, 113.6, 108.4),
    ]
    state_plain = _sakata_state(filler + plain_crows, {"intermediate_pivots": [], "internal_pivots": [], "external_pivots": []})
    methods_plain = {row["method"] for row in state_plain["active_methods"]}
    assert "SIMULTANEOUS_THREE_WINGS" not in methods_plain
