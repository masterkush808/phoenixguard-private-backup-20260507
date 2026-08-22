"""Strategist resolution contract tests for the rebuilt V3 book strategist.

Covers the August-21 live-session starvation fixes: every book family must be
resolvable by name with confluence and reasons, completed setups must stay
actionable inside the entry window, profit room must gate exhausted moves, and
CHOP may only publish when the market carries no directional evidence at all.
"""

from __future__ import annotations

from phoenixguard.decision.book_rule_action_signal_v3 import (
    BOOK_RULE_ACTION_SIGNAL_SCHEMA_V3,
    build_book_rule_action_signal_v3,
)
from phoenixguard.decision.book_strategy_context_v3 import (
    STRATEGIST_ENTRY_WINDOW_CANDLES_V3,
    STATUS_BOOK_ACTION_CONFIRMED_V3,
    STATUS_BOOK_EVIDENCE_CONFLICT_V3,
    STATUS_MARKET_CHOP_V3,
    STATUS_WAITING_FOR_TRIGGER_V3,
    select_current_book_action_v3,
)
from phoenixguard.decision.book_strategy_full_stack_v3 import (
    _news_pivot,
    _strict_trendline_contracts_v3,
    _sunday_gap_fade,
)
from phoenixguard.decision.candlestick_rule_catalog_v3 import (
    evaluate_candlestick_catalog_v3,
)

GEOMETRY = {
    "latest_close_y_px": 100.0,
    "median_candle_range_y_px": 32.0,
}


def _base_control(**overrides):
    control = {
        "schema": "PG_BOOK_STRATEGY_FORECAST_CONTROL_V3",
        "observed_candle_count": 40,
        "major_structure_side": "SELL",
        "inner_structure_side": "SELL",
        "higher_timeframe_authority_v3": {
            "strictly_enforced": True,
            "effective_side": "SELL",
            "side": "SELL",
            "authority_timeframe": "H1",
        },
        "session_news_context_v3": {},
        "hlz_sequence_v3": {},
        "role_flip_sequence_v3": {},
        "candlestick_catalog_v3": {},
        "fibonacci_ote_v3": {},
        "pair_dna_forecast_context_v3": {},
        "market_structure_full_v3": {"bms_events": [], "sms_events": []},
        "order_blocks_full_v3": {},
        "liquidity_turtle_soup_v3": {},
        "amd_v3": {},
        "news_pivot_v3": {},
        "sakata_v3": {},
        "opposing_force_targets_v3": {},
        "support_resistance_full_v3": {},
        "trendline_contracts_full_v3": {},
        "full_non_indicator_stack_v3": {},
    }
    control.update(overrides)
    return control


def _family_note(verdict: dict, strategy_id: str) -> dict:
    return next(
        row for row in verdict["family_resolutions"] if row["strategy_id"] == strategy_id
    )


def test_break_retest_resolves_by_name_with_confluence_reason() -> None:
    control = _base_control(
        trendline_contracts_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 39}
            ]
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "BREAK_RETEST"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert verdict["action"] == "SELL"
    assert verdict["resolution"] == "ACTIONABLE"
    assert verdict["confluence_count"] >= 2
    assert "MAJOR_STRUCTURE_DIRECTION" in verdict["rule_ids"]
    assert "HTF_DIRECTIONAL_AUTHORITY" in verdict["rule_ids"]
    assert "confluence" in verdict["scenario"].lower()
    assert _family_note(verdict, "STRICT_WICK_TRENDLINE")["resolution"] == "ACTIONABLE"


def test_zone_role_flip_resolves_by_name() -> None:
    control = _base_control(
        support_resistance_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "BUY", "retest_index": 39}
            ],
        },
        major_structure_side="BUY",
        inner_structure_side="BUY",
        higher_timeframe_authority_v3={
            "strictly_enforced": True,
            "effective_side": "BUY",
            "side": "BUY",
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "ROLE_FLIP_RETEST"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert verdict["action"] == "BUY"


def test_trendline_and_zone_rejections_resolve_by_name() -> None:
    line = _base_control(
        trendline_contracts_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
    )
    zone = _base_control(
        support_resistance_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
    )

    line_verdict = select_current_book_action_v3(line, market_geometry=dict(GEOMETRY))
    zone_verdict = select_current_book_action_v3(zone, market_geometry=dict(GEOMETRY))

    assert line_verdict["playbook"] == "TRENDLINE_REJECTION"
    assert line_verdict["profile"] == "AGGRESSIVE"
    assert line_verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert zone_verdict["playbook"] == "SUPPORT_RESISTANCE_REJECTION"
    assert zone_verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3


def test_hlz_fresh_sequence_names_stop_hunt_bms_rto() -> None:
    control = _base_control(
        hlz_sequence_v3={
            "entry_sequence_ready": True,
            "current_terminal_event": True,
            "stop_hunt": True,
            "bms": {"side": "SELL", "index": 38, "confirmed": True},
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "STOP_HUNT_BMS_RTO"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert verdict["action"] == "SELL"


def test_hlz_sequence_survives_entry_window_after_terminal_candle() -> None:
    """Regression: the Aug-21 session resolved only on the exact terminal candle."""
    control = _base_control(
        hlz_sequence_v3={
            "entry_sequence_ready": False,
            "current_terminal_event": False,
            "stop_hunt": True,
            "bms": {"side": "SELL", "index": 36, "confirmed": True},
        },
        fibonacci_ote_v3={
            "side": "SELL",
            "at_or_beyond_50": True,
            "in_ote": False,
            "evaluation_index": 38,
            "retracement_ratio": 0.66,
        },
    )
    assert 38 - 36 <= STRATEGIST_ENTRY_WINDOW_CANDLES_V3

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "STOP_HUNT_BMS_RTO"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert "entry window" in verdict["scenario"]
    assert _family_note(verdict, "HLZ_EVENT_SEQUENCE")["resolution"] == "ACTIONABLE"


def test_hlz_sequence_expires_after_entry_window() -> None:
    control = _base_control(
        hlz_sequence_v3={
            "entry_sequence_ready": False,
            "current_terminal_event": False,
            "stop_hunt": True,
            "bms": {"side": "SELL", "index": 30, "confirmed": True},
        },
        fibonacci_ote_v3={
            "side": "SELL",
            "at_or_beyond_50": True,
            "evaluation_index": 31,
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_WAITING_FOR_TRIGGER_V3
    assert verdict["playbook"] == "AWAITING_BOOK_TRIGGER"
    assert verdict["watch_side"] == "SELL"
    assert verdict["action"] == "WAIT"


def _actionable(select_verdict: dict) -> bool:
    return select_verdict.get("action") in {"BUY", "SELL"}


def test_order_block_rto_resolves_by_name() -> None:
    control = _base_control(
        order_blocks_full_v3={
            "active_block": {
                "side": "SELL",
                "order_block_id": "OB_test",
                "return_to_order_block": True,
                "latest_retest_index": 38,
            }
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "ORDER_BLOCK_RTO"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert "ORDER_BLOCK_ALIGNMENT" in verdict["rule_ids"]


def test_sweep_reclaim_resolves_liquidity_playbook() -> None:
    control = _base_control(
        liquidity_turtle_soup_v3={
            "state": "SWEEP_RECLAIM_BMS_CONFIRMED",
            "complete": False,
            "latest_sweep": {"index": 39, "side": "SELL", "reclaim_close_confirmed": True},
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "LIQUIDITY_SWEEP_RECLAIM"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert "LIQUIDITY_SWEEP_SIDE" in verdict["rule_ids"]


def test_structure_continuation_resolves_after_recent_bms() -> None:
    control = _base_control(
        market_structure_full_v3={
            "structure_side": "SELL",
            "latest_bms": {
                "side": "SELL",
                "index": 38,
                "completed_close_confirmed": True,
                "broken_level": 104.0,
            },
            "bms_events": [{"side": "SELL", "index": 38}],
            "sms_events": [],
            "protected_swing": {},
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "STRUCTURE_CONTINUATION"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert "BMS_ALIGNMENT" in verdict["rule_ids"]


def test_amd_distribution_stale_event_demotes_to_confluence() -> None:
    control = _base_control(
        amd_v3={
            "state": "AMD_DISTRIBUTION_CONFIRMED",
            "complete": True,
            "side": "SELL",
            "event_index": 30,
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert _family_note(verdict, "AMD_SEQUENCE")["resolution"] == "CONFLUENCE"
    assert verdict["status"] == STATUS_WAITING_FOR_TRIGGER_V3


def test_amd_distribution_fresh_event_is_actionable() -> None:
    control = _base_control(
        amd_v3={
            "state": "AMD_DISTRIBUTION_CONFIRMED",
            "complete": True,
            "side": "SELL",
            "event_index": 39,
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "AMD_DISTRIBUTION"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3


def test_profit_room_blocks_actionable_when_target_too_close() -> None:
    control = _base_control(
        trendline_contracts_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 39}
            ]
        },
        opposing_force_targets_v3={
            "SELL": {"source": "OPPOSING_TRENDLINE", "target_y_px": 105.0}
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_EVIDENCE_CONFLICT_V3
    assert verdict["action"] == "WAIT"
    assert verdict["resolution"] == "GATED"
    assert verdict["profit_room"]["sufficient"] is False
    assert any("room remains" in reason for reason in verdict["blocked_reasons"])


def test_price_inside_opposing_zone_reports_spent_move() -> None:
    control = _base_control(
        trendline_contracts_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 39}
            ]
        },
        opposing_force_targets_v3={
            "SELL": {
                "source": "OPPOSING_ZONE",
                "bounds": [900.0, 90.0, 1200.0, 140.0],
            }
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["profit_room"]["sufficient"] is False
    assert verdict["profit_room"]["room_px"] == 0.0
    assert any("inside" in reason for reason in verdict["blocked_reasons"])


def test_profit_room_passes_when_sufficient() -> None:
    control = _base_control(
        trendline_contracts_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 39}
            ]
        },
        opposing_force_targets_v3={
            "SELL": {"source": "OPPOSING_ZONE", "distance_px": 220.0}
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert verdict["profit_room"]["sufficient"] is True


def test_unmeasured_target_does_not_starve_resolution() -> None:
    control = _base_control(
        trendline_contracts_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 39}
            ]
        },
        opposing_force_targets_v3={},
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    assert verdict["profit_room"]["measured"] is False


def test_equal_priority_directional_conflict_gates_action() -> None:
    control = _base_control(
        support_resistance_full_v3={
            "current_reactions": [
                {"current_action_side": "SELL"},
                {"current_action_side": "BUY"},
            ]
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_EVIDENCE_CONFLICT_V3
    assert any(
        "equal-priority" in reason for reason in verdict["blocked_reasons"]
    )


def test_htf_conflict_gates_action() -> None:
    control = _base_control(
        support_resistance_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
        higher_timeframe_authority_v3={
            "strictly_enforced": True,
            "effective_side": "BUY",
            "side": "BUY",
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_EVIDENCE_CONFLICT_V3
    assert any(
        "higher-timeframe conflict" in reason for reason in verdict["blocked_reasons"]
    )


def test_ltf_reversal_evidence_never_grants_counter_htf_authority() -> None:
    """HLZ p.105: HTF owns direction; p.152: reversals need HTF liquidity first.

    Full LTF reversal evidence (SMS + BMS against HTF) must stay gated.
    """
    control = _base_control(
        support_resistance_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
        higher_timeframe_authority_v3={
            "strictly_enforced": True,
            "effective_side": "BUY",
            "side": "BUY",
        },
        market_structure_full_v3={
            "structure_side": "SELL",
            "latest_bms": {
                "side": "SELL",
                "index": 38,
                "completed_close_confirmed": True,
            },
            "bms_events": [{"side": "SELL", "index": 38}],
            "sms_events": [{"side": "SELL", "index": 38}],
        },
        hlz_sequence_v3={
            "stop_hunt": True,
            "bms": {"side": "SELL", "index": 38, "confirmed": True},
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_EVIDENCE_CONFLICT_V3
    assert any(
        "higher-timeframe conflict" in reason for reason in verdict["blocked_reasons"]
    )


def test_legacy_reversal_confirmed_key_is_ignored() -> None:
    control = _base_control(
        support_resistance_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
        higher_timeframe_authority_v3={
            "strictly_enforced": True,
            "effective_side": "BUY",
            "side": "BUY",
            "reversal_confirmed": True,
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_EVIDENCE_CONFLICT_V3
    assert any(
        "higher-timeframe conflict" in reason for reason in verdict["blocked_reasons"]
    )


def test_news_suspension_gates_action() -> None:
    control = _base_control(
        support_resistance_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
        session_news_context_v3={"entry_suspended_until_news_pivot": True},
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_BOOK_EVIDENCE_CONFLICT_V3
    assert any(
        "high-impact news" in reason for reason in verdict["blocked_reasons"]
    )


def test_market_chop_publishes_only_when_truly_none() -> None:
    control = _base_control(
        major_structure_side="NEUTRAL",
        inner_structure_side="NEUTRAL",
        higher_timeframe_authority_v3={},
        pair_dna_forecast_context_v3={"profile_applied": True, "side": "NEUTRAL"},
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_MARKET_CHOP_V3
    assert verdict["playbook"] == "CHOP"
    assert verdict["watch_side"] == "NEUTRAL"
    assert verdict["action"] == "WAIT"
    assert "directionless" in verdict["scenario"]


def test_waiting_keeps_watch_side_without_chop_label() -> None:
    control = _base_control()

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["status"] == STATUS_WAITING_FOR_TRIGGER_V3
    assert verdict["playbook"] == "AWAITING_BOOK_TRIGGER"
    assert verdict["watch_side"] == "SELL"
    assert verdict["action"] == "WAIT"


def _support_line_candles(breaker: dict | None = None) -> list[dict]:
    """Pixel candles defending a horizontal support line at y=110 (y-down)."""
    wick_bottoms = [104.0, 106.0, 110.0, 106.5, 107.0, 110.0, 108.0, 109.0, 110.0, 107.5, 109.5]
    rows: list[dict] = []
    for index, bottom in enumerate(wick_bottoms):
        rows.append(
            {
                "x": float(index * 10),
                "top_y": bottom - 6.0,
                "bottom_y": bottom,
                "open_y": bottom - 3.0,
                "close_y": bottom - 1.0,
                "is_closed": True,
            }
        )
    if breaker is not None:
        breaker = dict(breaker)
        breaker["x"] = float(len(rows) * 10)
        rows.append(breaker)
    return rows


_SUPPORT_LINE = {
    "trendline_id": "support-main",
    "geometry_contract_accepted": True,
    "role": "SUPPORT",
    "anchor_wick_points": [[20.0, 110.0], [50.0, 110.0]],
    "line_points": [[20.0, 110.0], [50.0, 110.0]],
}

_SUPPORT_PIVOTS = {
    "internal_pivots": [
        {"pivot_id": "low-2", "kind": "LOW", "tier": "INTERNAL", "index": 2},
        {"pivot_id": "low-5", "kind": "LOW", "tier": "INTERNAL", "index": 5},
    ],
    "intermediate_pivots": [],
    "external_pivots": [],
}


def test_noise_close_through_does_not_break_trendline() -> None:
    """secrets revealed pp.14-18: short body / shallow close is noise; line holds."""
    candles = _support_line_candles(
        breaker={
            "top_y": 111.8,
            "bottom_y": 113.4,
            "open_y": 112.4,
            "close_y": 113.0,
            "is_closed": True,
        }
    )

    result = _strict_trendline_contracts_v3(candles, [_SUPPORT_LINE], {}, _SUPPORT_PIVOTS, "M5")

    contract = result["contracts"][0]
    assert contract["lifecycle_state"] == "ACTIVE_THREE_TOUCH"
    assert contract["break_index"] is None
    assert contract["noise_close_through_indices"]
    assert contract["unconfirmed_htf_break_index"] is None


def test_deep_long_body_close_breaks_trendline() -> None:
    """secrets revealed pp.14-18: long body closing past half the range violates the line."""
    candles = _support_line_candles(
        breaker={
            "top_y": 110.0,
            "bottom_y": 115.0,
            "open_y": 110.5,
            "close_y": 114.5,
            "is_closed": True,
        }
    )

    result = _strict_trendline_contracts_v3(candles, [_SUPPORT_LINE], {}, _SUPPORT_PIVOTS, "M5")

    contract = result["contracts"][0]
    assert contract["lifecycle_state"] == "BROKEN_WAITING_FOR_ROLE_FLIP_RETEST"
    assert contract["break_index"] == len(candles) - 1
    assert not contract["noise_close_through_indices"]
    details = contract["break_significance_details"][contract["break_index"]]
    assert details["penetration_ratio"] >= 0.5
    assert details["body_length_px"] >= 2.0


def test_htf_line_break_stays_unconfirmed_without_higher_close() -> None:
    """secrets revealed pp.14-17: sub-H1 pierces of HTF lines never confirm alone."""
    candles = _support_line_candles(
        breaker={
            "top_y": 110.0,
            "bottom_y": 115.0,
            "open_y": 110.5,
            "close_y": 114.5,
            "is_closed": True,
        }
    )
    htf_line = {**_SUPPORT_LINE, "timeframe": "H1"}

    result = _strict_trendline_contracts_v3(candles, [htf_line], {}, _SUPPORT_PIVOTS, "M5")

    contract = result["contracts"][0]
    assert contract["break_requires_htf_close"] is True
    assert contract["lifecycle_state"] == "UNCONFIRMED_BREAK_WAITING_FOR_HTF_CLOSE"
    assert contract["unconfirmed_htf_break_index"] == len(candles) - 1
    assert contract["break_index"] is None


def _railway_rows(with_confirmation: bool) -> list[dict]:
    rows: list[dict] = []
    price = 130.0
    for _ in range(12):
        rows.append({"open": price + 1.5, "high": price + 2.0, "low": price - 2.5, "close": price})
        price -= 2.0
    rows.append({"open": 106.5, "high": 107.0, "low": 101.5, "close": 102.0})
    rows.append({"open": 102.2, "high": 107.6, "low": 101.8, "close": 106.9})
    if with_confirmation:
        rows.append({"open": 107.0, "high": 110.0, "low": 105.5, "close": 109.5})
    return rows


_RAILWAY_LOCATION = {"12": "BUY", "13": "BUY", "14": "BUY"}


def test_railway_track_qualifies_with_book_provenance() -> None:
    """secrets revealed pp.88-95: railway track is a codified reversal confirmation."""
    result = evaluate_candlestick_catalog_v3(
        candles=_railway_rows(with_confirmation=True),
        prior_trend="SELL",
        location_side="BUY",
        higher_timeframe_side="BUY",
        location_history=dict(_RAILWAY_LOCATION),
    )

    qualified = {row["rule_id"]: row for row in result["qualified_detections"]}
    assert "RAILWAY_TRACK_BULL" in qualified
    detection = qualified["RAILWAY_TRACK_BULL"]
    assert detection["directional_authority"] is True
    assert detection["source_file"].endswith("secrets revealed $10 000 cost price-1-1.pdf")
    assert detection["pdf_pages"] == [88, 95]


def test_railway_track_small_bodies_never_match_geometry() -> None:
    rows = _railway_rows(with_confirmation=True)
    rows[-1] = {**rows[-1], "open": 105.0, "close": 106.0}
    rows[-2] = {**rows[-2], "open": 104.2, "close": 103.4}

    result = evaluate_candlestick_catalog_v3(
        candles=rows,
        prior_trend="SELL",
        location_side="BUY",
        higher_timeframe_side="BUY",
        location_history={"12": "BUY", "13": "BUY", "14": "BUY"},
    )

    assert all(
        "RAILWAY" not in str(row.get("rule_id")) for row in result["detections"]
    )


def test_directional_alignment_ledger_merges_every_book_side() -> None:
    """All buy teachings merge into one BUY case, all sells into one SELL case."""
    control = _base_control(
        trendline_contracts_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
        liquidity_turtle_soup_v3={
            "state": "SWEEP_RECLAIM_BMS_CONFIRMED",
            "latest_sweep": {"index": 39, "side": "SELL"},
        },
        fibonacci_ote_v3={"side": "SELL", "in_ote": True},
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))
    alignment = verdict["directional_alignment"]

    assert alignment["leader"] == "SELL"
    assert alignment["BUY"] == 0.0
    families = {row["family"] for row in alignment["contributions"]}
    assert {"HIGHER_TIMEFRAME_AUTHORITY", "STRICT_WICK_TRENDLINE", "TURTLE_SOUP", "FIBONACCI_OTE"} <= families
    assert any("opposing force" in row["reason"] for row in alignment["contributions"])

    empty = select_current_book_action_v3(_base_control(
        major_structure_side="NEUTRAL",
        inner_structure_side="NEUTRAL",
        higher_timeframe_authority_v3={},
    ), market_geometry=dict(GEOMETRY))
    assert empty["directional_alignment"]["leader"] == "NEUTRAL"


def test_regime_ranging_demotes_breakout_continuation() -> None:
    """Donnelly pp.141-144: ranges expect false breaks; fades outrank breakouts."""
    control = _base_control(
        major_structure_side="NEUTRAL",
        inner_structure_side="NEUTRAL",
        amd_v3={
            "state": "ACCUMULATION",
            "accumulation_confirmed": True,
            "complete": False,
            "side": "NEUTRAL",
        },
        support_resistance_full_v3={"current_reactions": [{"current_action_side": "SELL"}]},
        market_structure_full_v3={
            "structure_side": "SELL",
            "latest_bms": {
                "side": "SELL",
                "index": 20,
                "completed_close_confirmed": True,
            },
            "bms_events": [],
            "sms_events": [],
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["regime"] == "RANGING"
    assert verdict["playbook"] == "SUPPORT_RESISTANCE_REJECTION"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3


def test_stop_plan_publishes_donnelly_numeric_distances() -> None:
    """pp.338-342: minimum 1.2x volatility unit, 20% structure buffer, fixed at publish."""
    control = _base_control(
        trendline_contracts_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 39}
            ]
        },
    )

    builder_verdict = build_book_rule_action_signal_v3(
        control=control,
        candles=_pixel_candles(),
        pair="EUR/USD OTC",
        timeframe="M5",
        frame_id=99,
        identity_confirmed=True,
    )

    stop_plan = builder_verdict["stop_plan"]
    assert stop_plan["basis"] == "DONNELLY_PP338_342_STRUCTURE_PLUS_ADR_BUFFER"
    assert stop_plan["immovable_at_publish"] is True
    unit = stop_plan["adr_proxy_basis"] == "median_closed_candle_range_y_px"
    assert unit is True
    assert stop_plan["minimum_distance_px"] is not None
    assert stop_plan["minimum_distance_px"] >= 1.2 * 2.0


def test_persistent_newspivot_survives_and_fires_hard_exit() -> None:
    """Donnelly pp.268-270: the pre-release price is the pivot for the whole session.

    A good-news spike that folds back through it = good news/bad price and a hard
    exit for the continuation side.
    """
    candles = []
    price = 100.0
    for _ in range(5):
        candles.append({"open": price + 0.4, "high": price + 0.9, "low": price - 0.9, "close": price})
        price += 0.2
    candles.append({"open": 101.4, "high": 108.0, "low": 101.0, "close": 107.5})
    candles.append({"open": 107.8, "high": 108.4, "low": 103.2, "close": 103.8})
    candles.append({"open": 103.6, "high": 104.0, "low": 99.6, "close": 100.4})

    result = _news_pivot(candles, {"impact": "HIGH"})

    assert result["confirmed"] is True
    assert result["pivot_persists_for_session"] is True
    assert result["pre_release_price"] == 100.8
    assert result["good_news_bad_price_confirmed"] is True
    assert result["hard_exit_triggered"] is True

    mirror = [
        {"open": 100.0 - offset, "high": 100.6 - offset, "low": 99.4 - offset, "close": 100.0 - offset}
        for offset in range(5)
    ]
    mirror.append({"open": 98.6, "high": 99.0, "low": 92.0, "close": 92.5})
    mirror.append({"open": 92.2, "high": 96.8, "low": 91.6, "close": 96.2})
    mirror.append({"open": 96.4, "high": 99.9, "low": 96.0, "close": 99.6})

    sell_result = _news_pivot(mirror, {"impact": "HIGH"})

    assert sell_result["bad_news_good_price_confirmed"] is True
    assert sell_result["hard_exit_triggered"] is True


def test_sunday_gap_fade_requires_weekend_context() -> None:
    """pp.174-176: fade weekend gaps; no weekend context means never guessed."""
    gap_rows = [
        {"open": 120.0, "high": 120.5, "low": 119.5, "close": 120.2, "timestamp": 1_000_000.0},
        {"open": 116.0, "high": 116.4, "low": 112.0, "close": 112.5, "timestamp": 1_000_000.0 + 300},
        {
            "open": 110.5,
            "high": 111.4,
            "low": 108.0,
            "close": 108.5,
            "timestamp": 1_000_000.0 + 25 * 3600,
        },
    ]

    detected = _sunday_gap_fade(gap_rows, None)
    assert detected["detected"] is True
    assert detected["side"] == "BUY"
    assert detected["gap_direction"] == "DOWN"
    assert detected["base_rate"] == "ABOUT_85_PERCENT_FILL_WITHIN_48H"

    plain_rows = [
        {"open": 120.0, "high": 120.5, "low": 119.5, "close": 120.2},
        {"open": 116.0, "high": 116.4, "low": 112.0, "close": 112.5},
        {"open": 110.5, "high": 111.4, "low": 108.0, "close": 108.5},
    ]
    missing = _sunday_gap_fade(plain_rows, None)
    assert missing["detected"] is False
    assert missing["reason"] == "NO_WEEKEND_CONTEXT"


def test_sunday_gap_fade_resolves_by_name_through_strategist() -> None:
    control = _base_control(
        sunday_gap_fade_v3={
            "detected": True,
            "side": "BUY",
            "gap_direction": "DOWN",
            "gap_size": 3.5,
            "range_unit": 2.0,
            "origin_close": 120.2,
            "fill_target_price": 119.4,
            "base_rate": "ABOUT_85_PERCENT_FILL_WITHIN_48H",
        },
        major_structure_side="NEUTRAL",
        inner_structure_side="NEUTRAL",
        higher_timeframe_authority_v3={},
        pair_dna_forecast_context_v3={
            "profile_applied": True,
            "side": "BUY",
            "probability": 0.62,
        },
    )

    verdict = select_current_book_action_v3(control, market_geometry=dict(GEOMETRY))

    assert verdict["playbook"] == "SUNDAY_GAP_FADE"
    assert verdict["action"] == "BUY"
    assert verdict["status"] == STATUS_BOOK_ACTION_CONFIRMED_V3
    family_note = next(
        row for row in verdict["family_resolutions"] if row["strategy_id"] == "SUNDAY_GAP_FADE"
    )
    assert family_note["resolution"] in {"CANDIDATE", "ACTIONABLE"}


def _pixel_candles(count: int = 24) -> list[dict]:
    rows = []
    for index in range(count):
        close_y = 100.0 + index * 2.0
        rows.append(
            {
                "x": 20.0 + index * 10.0,
                "open_y": close_y - 4.0,
                "close_y": close_y,
                "top_y": close_y - 6.0,
                "bottom_y": close_y + 6.0,
            }
        )
    return rows


def test_builder_publishes_strategist_contract_for_bridge() -> None:
    control = _base_control(
        observed_candle_count=24,
        trendline_contracts_full_v3={
            "valid_count": 1,
            "contracts": [],
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 23}
            ],
        },
        hlz_sequence_v3={"entry_profiles": {}},
    )

    verdict = build_book_rule_action_signal_v3(
        control=control,
        candles=_pixel_candles(),
        pair="EUR/USD OTC",
        timeframe="M5",
        frame_id=17,
        closed_candle_key="closed-test-candle",
        closed_candle_sequence=23,
        identity_confirmed=True,
    )

    assert verdict["schema_version"] == BOOK_RULE_ACTION_SIGNAL_SCHEMA_V3
    assert verdict["actionable"] is True
    assert verdict["action"] == "SELL"
    assert verdict["playbook"] == "BREAK_RETEST"
    assert verdict["playbook_family"] == "STRICT_WICK_TRENDLINE"
    assert verdict["resolution"] == "ACTIONABLE"
    assert verdict["closed_candle_key"] == "closed-test-candle"
    assert verdict["rule_traceability"]["selected_book_rule_ids"]
    assert isinstance(verdict["profit_room"], dict)
    assert verdict["family_resolutions"]
    assert all(
        "resolved_playbook" in row and "resolution" in row
        for row in verdict["strategy_report"]
    )


def test_builder_surfaces_blocked_reasons_on_gated_setup() -> None:
    control = _base_control(
        observed_candle_count=24,
        trendline_contracts_full_v3={
            "current_role_flip_retests": [
                {"current_action_side": "SELL", "retest_index": 23}
            ]
        },
        opposing_force_targets_v3={
            "SELL": {"source": "OPPOSING_ZONE", "distance_px": 1.0}
        },
    )

    verdict = build_book_rule_action_signal_v3(
        control=control,
        candles=_pixel_candles(),
        pair="EUR/USD OTC",
        timeframe="M5",
        frame_id=18,
        identity_confirmed=True,
    )

    assert verdict["actionable"] is False
    assert verdict["playbook"] == "BREAK_RETEST"
    assert verdict["resolution"] == "GATED"
    assert verdict["blocked_reasons"]
