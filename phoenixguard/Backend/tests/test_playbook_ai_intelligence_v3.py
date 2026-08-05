from __future__ import annotations

from typing import Any

from phoenixguard.decision.playbook_ai_intelligence_v3 import (
    PG_PLAYBOOK_AI_INTELLIGENCE_SCHEMA_VERSION,
    build_playbook_ai_intelligence_v3,
    compact_playbook_ai_intelligence_v3,
)


def _playbook_inputs(side: str = "BUY") -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    opposite = "SELL" if side == "BUY" else "BUY"
    overlay_suite = {
        "side": side,
        "rows_total": 42,
        "actionable_count": 30,
        "same_side_actionable_count": 24,
        "entry_window_count": 4,
        "same_side_entry_window_count": 4,
        "target_window_count": 3,
        "invalidation_count": 2,
        "prediction_path_count": 2,
        "structure_box_count": 4,
        "trendline_count": 2,
        "replay_path_count": 3,
        "memory_path_count": 2,
        "angle_vector_count": 2,
        "opposing_force_count": 2,
        "overlay_arsenal_score": 0.91,
        "entry_ready": True,
        "current_entry_touch": True,
        "target_ready": True,
        "invalidation_ready": True,
        "projection_ready": True,
        "structure_ready": True,
        "trendline_ready": True,
        "full_suite_ready": True,
        "expected_move_candles_from_projection": 18,
        "first_class_feeds": {
            "supply_demand": True,
            "trendlines": True,
            "sniper_entries": True,
            "targets": True,
        },
    }
    candle_context = {
        "timeframe": "M5",
        "timeframe_seconds": 300,
        "visible_candle_count": 54,
        "move_stage": "MATURE",
        "current_leg": {
            "side": side,
            "candle_count": 6,
            "move_stage": "MATURE",
            "opposing_force_room": {"room_ok": True, "estimated_candles_to_force": 18},
        },
        "opposing_force_room": {"room_ok": True, "estimated_candles_to_force": 18},
    }
    professional_plan = {
        "schema_version": "PG_PROFESSIONAL_TRADE_PLAN_V3",
        "side": side,
        "authority_side": side,
        "professional_grade": True,
        "blocker": "",
        "next_required": "none",
        "thesis_class": "TREND_ALIGNED_CONTINUATION",
        "professional_thesis_state": "PRIMARY_BIAS_ALIGNED",
        "entry_window": {"duration_sec": 300, "candle_count": 1},
        "thesis_horizon": {
            "expected_duration_sec": 3600,
            "expected_candle_count": 12,
            "minimum_professional_candles": 8,
            "current_leg_candle_count": 6,
            "current_leg_side": side,
            "current_leg_stage": "MATURE",
            "estimated_candles_to_force": 18,
        },
        "trend_alignment": {
            "overlay_suite_thesis": True,
            "replay_template_thesis": True,
            "aligned_with_primary_bias": True,
            "primary_bias_side": side,
            "global_side": side,
            "local_side": side,
        },
    }
    snapshot = {
        "candidate_side": side,
        "candle_movement_context_v3": candle_context,
        "market_context": {
            "global_side": side,
            "local_side": side,
            "dominant_side": side,
            "classifiers": {},
            "regime": {"primary": "TRENDING"},
        },
    }
    market = {
        "professional_trade_plan": professional_plan,
        "market_context": snapshot["market_context"],
    }
    book_strategy = {
        "side": side,
        "professional_trade_plan": professional_plan,
        "evidence": {
            "primary_bias_side": side,
            "dominant_side": side,
            "global_side": side,
            "local_side": side,
            "aligned_with_primary_bias": True,
            "overlay_suite_evidence_v3": overlay_suite,
            "candle_movement_context_v3": candle_context,
        },
        "strategy_read": {"play": f"{side}_CONTINUATION", "opposite": opposite},
    }
    return snapshot, market, book_strategy


def test_playbook_ai_scores_buy_and_sell_with_tradeable_horizon() -> None:
    snapshot, market, book_strategy = _playbook_inputs("BUY")

    result = build_playbook_ai_intelligence_v3(snapshot, market, book_strategy, "BUY")
    summary = compact_playbook_ai_intelligence_v3(result)

    assert result["schema_version"] == PG_PLAYBOOK_AI_INTELLIGENCE_SCHEMA_VERSION
    assert result["semantic_graph"]["coverage"]["full_suite_ready"] is True
    assert result["thesis_arbitration"]["buy_sell_scored_simultaneously"] is True
    assert result["thesis_arbitration"]["winner"] == "BUY"
    assert result["meta_label"]["candidate_tradeable"] is True
    assert result["horizon"]["selected"]["optimized_candle_count"] >= 8
    assert summary["schema_version"] == "PG_PLAYBOOK_AI_SUMMARY_V3"
    assert summary["full_suite_ready"] is True
    assert summary["thesis_arbitration"]["winner"] == "BUY"
    assert "semantic_graph" not in summary


def test_playbook_ai_never_publishes_sub_fifteen_minute_trade_horizon() -> None:
    snapshot, market, book_strategy = _playbook_inputs("BUY")
    candle_context = book_strategy["evidence"]["candle_movement_context_v3"]
    candle_context["current_leg"]["candle_count"] = 4
    candle_context["current_leg"]["opposing_force_room"][
        "estimated_candles_to_force"
    ] = 2
    candle_context["opposing_force_room"]["estimated_candles_to_force"] = 2
    overlay = book_strategy["evidence"]["overlay_suite_evidence_v3"]
    overlay["expected_move_candles_from_projection"] = 2
    plan = market["professional_trade_plan"]
    plan["thesis_horizon"]["expected_candle_count"] = 2
    plan["thesis_horizon"]["expected_duration_sec"] = 600
    plan["thesis_horizon"]["estimated_candles_to_force"] = 2
    book_strategy["professional_trade_plan"] = plan

    result = build_playbook_ai_intelligence_v3(
        snapshot,
        market,
        book_strategy,
        "BUY",
    )
    selected = result["horizon"]["selected"]
    summary = compact_playbook_ai_intelligence_v3(result)

    assert selected["minimum_eligible_trade_duration_seconds"] == 900
    assert selected["under_15_minutes_excluded"] is True
    assert selected["optimized_duration_sec"] == 0
    assert selected["optimized_candle_count"] == 0
    assert selected["trade_duration_eligible"] is False
    assert selected["observation_only"] is True
    assert selected["observation_only_candidate"]["duration_sec"] == 600
    assert summary["horizon"]["optimized_duration_sec"] == 0
    assert summary["horizon"]["trade_duration_eligible"] is False
    assert summary["horizon"]["observation_only"] is True


def test_playbook_ai_detects_opposite_thesis_without_runtime_bypass() -> None:
    snapshot, market, book_strategy = _playbook_inputs("SELL")

    result = build_playbook_ai_intelligence_v3(snapshot, market, book_strategy, "BUY")

    assert result["thesis_arbitration"]["winner"] == "SELL"
    assert result["thesis_arbitration"]["candidate_supported"] is False
    assert result["meta_label"]["selected_side"] == "SELL"
    assert result["rules_applied"]
