from __future__ import annotations

from typing import Any

from phoenixguard.decision.book_strategy_master_v3 import (
    BOOK_STRATEGY_SCHEMA_VERSION,
    BOOK_STRATEGY_EXECUTION_AUTHORITY,
    MODEL_COUNCIL_CONTRIBUTOR_ROLE,
    evaluate_book_strategy_master_v3,
)
from phoenixguard.decision.candle_movement_context_v3 import build_candle_movement_context_v3
from phoenixguard.decision.market_play_engine_v3 import analyze_market_play_v3
from phoenixguard.decision.model_council_v3 import ModelCouncilV3


NOW = 1_800_010_000.0


def _zone(zone_id: str, zone_type: str, *, side: str, inside: bool = False, distance: float = 0.08) -> dict[str, Any]:
    return {
        "zone_id": zone_id,
        "zone_type": zone_type,
        "side": side,
        "current_price_inside": inside,
        "distance_from_current": distance,
        "role_flip_confirmed": False,
        "confidence": 0.82,
    }


def _strategy_snapshot(side: str = "BUY") -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY"
    return {
        "session_id": "pocket-live-8788",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": 901,
        "capture_count": 903,
        "state_version": 1901,
        "input_frame_hash": "frame_901",
        "previous_frame_hash": "frame_900",
        "live_integrity": {
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "cache_status": "fresh",
        },
        "runtime_model_health": {
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "max_model_latency_ms": 32,
            "queue_depth": 0,
        },
        "candidate_side": side,
        "global_structure": {"global_side": side, "global_confidence": 0.86},
        "local_micro_structure": {"local_side": side, "confidence": 0.84},
        "market_context": {
            "global_side": side,
            "local_side": side,
            "dominant_side": side,
            "current_location": "DEMAND_ZONE" if side == "BUY" else "SUPPLY_ZONE",
            "inside_valid_trigger_zone": True,
            "opposing_force_distance_ok": True,
            "is_continuation_confirmed": True,
            "is_late_chase": False,
            "middle_safe": True,
        },
        "zone_liquidity": {
            "side": side,
            "zone_type": "DEMAND" if side == "BUY" else "SUPPLY",
            "inside_valid_trigger_zone": True,
        },
        "zones": [
            _zone("active_001", "DEMAND" if side == "BUY" else "SUPPLY", side=side, inside=True),
            _zone("opposing_001", "SUPPLY" if side == "BUY" else "DEMAND", side=opposite, distance=0.42),
        ],
        "risk_context": {
            "distance_to_opposing_force": 0.42,
            "minimum_required_distance": 0.22,
            "distance_ok": True,
        },
        "angle_features": {
            "angle_class": "HEALTHY_TREND",
            "late_chase_risk": False,
            "post_impulse_wait_required": False,
        },
        "historical_pattern": {
            "similarity_state": "REPEATING_SUCCESSFUL_PATH",
            "would_have_exited_here": False,
            "historical_entry_quality": "GOOD",
        },
        "timing": {"state": "READY", "side": side, "expiry_seconds": 300},
        "current_candle_acceptance": {
            "state": "VALID",
            "phase": "VALID",
            "entry_allowed": True,
            "current_candle_closed": True,
        },
        "continuation_confirmed": True,
        "pullback_confirmed": True,
        "retest_confirmed": True,
        "sequence_length": 50,
        "frames_used": 50,
        "frames_received": 50,
        "sequence_confidence": 0.94,
        "sequence_status": "COMPLETE",
        "historical_structure": [
            {"key": "history_1", "label": f"H1 {side}", "bbox": [10, 20, 150, 180], "direction": side}
        ],
        "progression": [{"stage": "context_confirmed", "direction": side, "confidence": 0.92}],
        "entry_progression": {
            "progression_stage": "SNIPER_READY",
            "maturity_score": 0.91,
            "progression_velocity": 0.34,
            "continuation_strength": 0.86,
            "exhaustion_risk": 0.12,
        },
    }


def _attach_candle_movement_fixture(snapshot: dict[str, Any], side: str) -> None:
    opposite = "SELL" if side == "BUY" else "BUY"
    candles = [
        {
            "index": index,
            "bbox": [20 + index * 10, 100 + index * 2, 28 + index * 10, 126 + index * 2],
            "center_x": 24 + index * 10,
            "center_y": 113 + index * 2,
            "direction": opposite,
            "price_proxy": 1.0 - index * 0.01,
        }
        for index in range(4)
    ] + [
        {
            "index": index,
            "bbox": [20 + index * 10, 180 - index * 3, 28 + index * 10, 206 - index * 3],
            "center_x": 24 + index * 10,
            "center_y": 193 - index * 3,
            "direction": side,
            "price_proxy": 0.96 + (index - 4) * 0.012,
        }
        for index in range(4, 12)
    ]
    snapshot["tracking_summary"] = {
        "detected_timeframe": "M5",
        "visible_candle_count": len(candles),
        "tracked_candles": candles,
        "historical_structure": [
            {"label": f"H1 {opposite}", "direction": opposite, "source_indices": list(range(4)), "candle_count": 4},
            {"label": f"H2 {side}", "direction": side, "source_indices": list(range(4, 12)), "candle_count": 8},
        ],
        "support_resistance_zones": [
            {
                "label": "DEMAND" if side == "BUY" else "SUPPLY",
                "role": "DEMAND" if side == "BUY" else "SUPPLY",
                "bbox": [58, 120, 132, 190],
                "anchor_candles": [4, 5, 6],
            }
        ],
    }
    snapshot["risk_opposing_force"] = {"distance_to_opposing_force": 0.38, "distance_ok": True}
    snapshot["candle_movement_context_v3"] = build_candle_movement_context_v3(snapshot)


def _attach_executable_identity_lock(snapshot: dict[str, Any]) -> None:
    identity_lock: dict[str, Any] = {
        "user_symbol": "EUR/GBP OTC",
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "viewport_hash": "chart-viewport-a",
        "broker_surface_hash": "broker-a",
        "window_handle": "hwnd-1",
        "window_rect": [0, 0, 640, 420],
        "calibration_layout_id": "layout-a",
        "expected_calibration_layout_id": "layout-a",
        "window_handle_stable": True,
        "window_rect_stable": True,
        "viewport_hash_stable": True,
        "broker_surface_hash_stable": True,
        "calibration_layout_match": True,
        "session_active": True,
        "packet_fresh": True,
        "models_awake": True,
        "profile_mismatch": False,
    }
    snapshot.update(
        {
            "symbol": "",
            "market": "",
            "ocr_symbol": "",
            "viewport_hash": "chart-viewport-a",
            "broker_surface_hash": "broker-a",
            "instrument_identity_lock": identity_lock,
        }
    )


def test_book_strategy_master_signs_valid_single_timeframe_reaction() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.44
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.72
    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "LIVE_TOUCH_REJECTION"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.83,
        lane_required_score=0.70,
    )

    assert result["schema_version"] == BOOK_STRATEGY_SCHEMA_VERSION
    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook"] == "DEMAND_REJECTION"
    assert result["execution_authority"] == BOOK_STRATEGY_EXECUTION_AUTHORITY
    assert result["model_council_role"] == MODEL_COUNCIL_CONTRIBUTOR_ROLE
    assert result["single_timeframe_mode"] is True
    assert result["multiple_timeframe_required"] is False
    assert result["playbook_signal"] == "BUY"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert result["reaction_type"] in {"BODY_ACCEPTANCE", "RETEST_HOLD", "WICK_REJECTION", "RECLAIM_AFTER_SWEEP"}
    assert "DEMAND_REJECTION" in result["strategy_combo"]


def test_book_strategy_blocks_buy_into_active_resistance_area() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["market_context"]["current_location"] = "RESISTANCE_ZONE"
    snapshot["zones"] = [
        _zone("active_resistance_001", "SUPPLY", side="SELL", inside=True, distance=0.02),
        _zone("lower_demand_001", "DEMAND", side="BUY", inside=False, distance=0.44),
    ]

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "LIVE_PRESSURE"},
            "price_location": {"relative_location": "RESISTANCE_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "PREPARE"
    assert result["playbook_signal"] == "HOLD"
    assert result["evidence"]["wrong_side_location_blocked"] is True
    assert any(row["field"] == "wrong_side_location" for row in result["hard_blockers"])


def test_book_strategy_blocks_sell_into_active_support_area() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["market_context"]["current_location"] = "SUPPORT_ZONE"
    snapshot["zones"] = [
        _zone("active_support_001", "DEMAND", side="BUY", inside=True, distance=0.02),
        _zone("upper_supply_001", "SUPPLY", side="SELL", inside=False, distance=0.44),
    ]

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "LIVE_PRESSURE"},
            "price_location": {"relative_location": "SUPPORT_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "PREPARE"
    assert result["playbook_signal"] == "HOLD"
    assert result["evidence"]["wrong_side_location_blocked"] is True
    assert any(row["field"] == "wrong_side_location" for row in result["hard_blockers"])


def test_book_strategy_uses_conservative_retest_profile_after_role_flip() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["role_flip_confirmed"] = True
    snapshot["retest_confirmed"] = True
    snapshot["break_of_structure_confirmed"] = True
    snapshot["zones"][0]["current_price_inside"] = False
    snapshot["zones"][0]["role_flip_confirmed"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "BMS_ROLE_FLIP", "play_stage": "RETEST_ACCEPTED"},
            "price_location": {"relative_location": "ROLE_FLIP_RETEST"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={"name": "FAILED_RETEST_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "legacy lane waiting"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_RETEST",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.72,
        lane_required_score=0.90,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "SELL"
    assert result["entry_profile"] == "CONSERVATIVE_RETEST"
    assert "ROLE_FLIP" in result["strategy_combo"]
    assert "RETEST_CONFIRMED" in result["strategy_combo"]


def test_book_strategy_allows_buy_after_resistance_role_flip_retest() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"]["current_location"] = "RESISTANCE_ROLE_FLIP_RETEST"
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["role_flip_confirmed"] = True
    snapshot["retest_confirmed"] = True
    snapshot["break_of_structure_confirmed"] = True
    snapshot["zones"] = [
        _zone("failed_supply_001", "SUPPLY", side="BUY", inside=True, distance=0.02),
        _zone("target_supply_001", "SUPPLY", side="SELL", inside=False, distance=0.42),
    ]
    snapshot["zones"][0]["role_flip_confirmed"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "BMS_ROLE_FLIP", "play_stage": "RETEST_ACCEPTED"},
            "price_location": {"relative_location": "RESISTANCE_ROLE_FLIP_RETEST"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "FAILED_RETEST_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "legacy timing contributor waiting"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_RETEST",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.70,
        lane_required_score=0.90,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["evidence"]["wrong_side_location_evidence"]
    assert result["evidence"]["wrong_side_location_role_flip_exception"] is True
    assert result["evidence"]["wrong_side_location_blocked"] is False
    assert not any(row["field"] == "wrong_side_location" for row in result["hard_blockers"])


def test_book_strategy_blocks_raw_momentum_without_structure_or_wave_proof() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["zones"] = []
    snapshot["trendlines_v3"] = []
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"]["current_location"] = "MIDDLE_DANGER"
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["continuation_confirmed"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["break_of_structure_confirmed"] = False
    snapshot["structure_shift_confirmed"] = False

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "IMPULSE_MOMENTUM", "play_stage": "MOMENTUM_ACCEPTANCE"},
            "price_location": {"relative_location": "MIDDLE_DANGER"},
            "zones": [],
        },
        candidate_side="BUY",
        execution_lane={
            "name": "MOMENTUM_ACCEPTANCE_ENTRY",
            "accepted": True,
            "wave_context": {"phase": "MID_RANGE_TIMING_ONLY", "blockers": ["MID_RANGE_NEEDS_FLOW_PROOF"]},
        },
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.93,
        lane_required_score=0.88,
    )

    assert result["maturity_state"] == "PREPARE"
    assert result["entry_profile"] == "WATCH_ONLY"
    assert result["evidence"]["momentum_interpretation_v3"] == "RAW_MOMENTUM_DIAGNOSTIC_ONLY"
    assert result["evidence"]["momentum_context_ready"] is False
    assert any(row["field"] == "momentum_context_ready" for row in result["hard_blockers"])


def test_book_strategy_allows_momentum_as_clean_wave_reentry_confirmation() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"]["current_location"] = "MIDDLE_SAFE"
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    wave_context: dict[str, Any] = {
        "phase": "CLEAR_PATH_CONTINUATION",
        "blockers": [],
        "wave_entry_ok": True,
        "granular_entry_ok": True,
        "continuation_ready": True,
        "clear_path_ready": True,
        "buy_low_sell_high_ok": True,
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "IMPULSE_MOMENTUM", "play_stage": "MOMENTUM_ACCEPTANCE"},
            "price_location": {"relative_location": "MIDDLE_SAFE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "MOMENTUM_ACCEPTANCE_ENTRY", "accepted": True, "wave_context": wave_context},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.93,
        lane_required_score=0.88,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["entry_profile"] == "CONTINUATION_RETEST"
    assert result["evidence"]["momentum_interpretation_v3"] == "TREND_REENTRY_SUPPORT"
    assert result["evidence"]["momentum_context_ready"] is True
    assert not result["hard_blockers"]


def test_book_strategy_uses_replay_entry_exit_template_for_wave_entry_quality() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["tracking_summary"] = {
        "visible_candle_count": 56,
        "historical_structure": [
            {
                "key": "history_sell_wave_1",
                "label": "H5 SELL",
                "direction": "SELL",
                "sniper_window": [240, 112, 292, 148],
                "target_window": [420, 260, 486, 304],
                "source_indices": list(range(16)),
                "candle_count": 16,
                "confidence": 0.91,
                "truth_score": 0.88,
            }
        ],
    }
    snapshot["historical_pattern"]["would_have_entered_here"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "SUPPLY_REJECTION", "play_stage": "LIVE_TOUCH_REJECTION"},
            "price_location": {"relative_location": "SUPPLY_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.89,
        lane_required_score=0.70,
    )

    replay_template = result["evidence"]["replay_wave_template_v3"]
    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "SELL"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert replay_template["entry_alignment_ready"] is True
    assert replay_template["template_profitable"] is True
    assert replay_template["best_expected_move_candles"] >= 16
    assert "REPLAY_WAVE_TEMPLATE_ENTRY" in result["strategy_combo"]


def test_book_strategy_blocks_replay_template_mid_leg_chase() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["zones"][0]["current_price_inside"] = False
    snapshot["continuation_confirmed"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["current_candle_acceptance"]["entry_allowed"] = True
    snapshot["current_candle_acceptance"]["accepted"] = True
    snapshot["tracking_summary"] = {
        "visible_candle_count": 56,
        "historical_structure": [
            {
                "key": "history_sell_wave_2",
                "label": "H7 SELL",
                "direction": "SELL",
                "sniper_window": [240, 112, 292, 148],
                "target_window": [420, 260, 486, 304],
                "source_indices": list(range(16)),
                "candle_count": 16,
                "confidence": 0.91,
                "truth_score": 0.88,
            }
        ],
    }
    snapshot["candle_movement_context_v3"] = {
        "visible_candle_count": 56,
        "move_stage": "MATURE",
        "current_leg": {"side": "SELL", "candle_count": 10, "move_stage": "MATURE"},
        "opposing_force_room": {"room_ok": True, "estimated_candles_to_force": 12},
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "SUPPLY_REJECTION", "play_stage": "MID_LEG"},
            "price_location": {"relative_location": "MIDDLE_SAFE"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.89,
        lane_required_score=0.70,
    )

    replay_template = result["evidence"]["replay_wave_template_v3"]
    assert result["maturity_state"] == "LATE_CHASE"
    assert result["playbook_signal"] == "HOLD"
    assert replay_template["late_template_chase_risk"] is True
    assert any(row["field"] == "replay_wave_template.phase" for row in result["hard_blockers"])


def test_book_strategy_ingests_full_overlay_suite_from_tracking_summary() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["zones"] = []
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["tracking_summary"] = {
        "visible_candle_count": 49,
        "structure_boxes": [
            {
                "id": "structure_buy_1",
                "label": "IMPULSE",
                "direction": "BUY",
                "bbox": [220, 210, 390, 132],
                "sniper_window": [232, 192, 266, 220],
                "trigger_window": [250, 174, 318, 202],
                "target_window": [420, 82, 482, 118],
                "invalidation_y": 246,
                "source_indices": list(range(18)),
                "confidence": 0.88,
            }
        ],
        "support_resistance_zones": [
            {
                "zone_id": "tracking_demand_1",
                "label": "DEMAND",
                "role": "DEMAND",
                "side": "BUY",
                "bbox": [220, 190, 320, 234],
                "current_price_inside": True,
                "distance_from_current": 0.04,
                "confidence": 0.86,
            },
            {
                "zone_id": "tracking_supply_1",
                "label": "SUPPLY",
                "role": "SUPPLY",
                "side": "SELL",
                "bbox": [430, 70, 500, 110],
                "distance_from_current": 0.46,
                "confidence": 0.82,
            },
        ],
        "trendlines_v3": [
            {
                "trendline_id": "support_line_1",
                "role": "SUPPORT_TRENDLINE",
                "side": "BUY",
                "touching_now": True,
                "confidence": 0.84,
                "line_points": [[220, 230], [500, 116]],
            }
        ],
        "projection": {
            "direction": "BUY",
            "zones": [
                {
                    "id": "projection_buy_1",
                    "kind": "sniper",
                    "direction": "BUY",
                    "bbox": [232, 192, 266, 220],
                    "target_bbox": [420, 82, 482, 118],
                    "invalidation_y": 246,
                    "path": [[232, 210], [305, 176], [382, 132], [454, 96]],
                    "expected_move_candles": 18,
                    "confidence": 0.9,
                }
            ],
        },
        "angle_vectors": [
            {
                "id": "angle_buy_1",
                "direction": "BUY",
                "line_points": [[232, 210], [454, 96]],
                "confidence": 0.81,
            }
        ],
        "execution_timing": {
            "entry_area_zone": {
                "side": "BUY",
                "bbox": [232, 192, 266, 220],
                "current_price_inside": True,
                "confidence": 0.91,
            },
            "opposing_force_zone": {
                "side": "SELL",
                "bbox": [420, 82, 482, 118],
                "confidence": 0.86,
            },
        },
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "FULL_OVERLAY_SUITE"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
    )

    suite = result["evidence"]["overlay_suite_evidence_v3"]
    assert result["maturity_state"] == "ENTER_NOW"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert result["evidence"]["active_zone_type"] == "DEMAND"
    assert result["evidence"]["active_trendline_role"] == "SUPPORT_TRENDLINE"
    assert suite["entry_ready"] is True
    assert suite["target_ready"] is True
    assert suite["invalidation_ready"] is True
    assert suite["projection_ready"] is True
    assert suite["angle_ready"] is True
    assert suite["full_suite_ready"] is True
    assert suite["expected_move_candles_from_projection"] >= 18
    assert suite["counts_by_type"]["SNIPER_ENTRY_BOX"] >= 1
    assert suite["counts_by_type"]["TARGET_ZONE_BOX"] >= 1
    assert "OVERLAY_SUITE_FULL_READ" in result["strategy_combo"]
    assert "OVERLAY_ENTRY_TARGET_MAP" in result["strategy_combo"]
    assert "OVERLAY_PROJECTION_PATH" in result["strategy_combo"]


def test_book_strategy_ingests_full_overlay_suite_from_market_object_registry() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["zones"] = []
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["tracking_summary"] = {
        "visible_candle_count": 24,
        "tracked_candles": [
            {
                "index": index,
                "bbox": [20 + index * 12, 180 - index * 2, 28 + index * 12, 212 - index * 2],
                "direction": "BUY" if index >= 16 else "SELL",
                "price_proxy": 1.0 + index * 0.01,
            }
            for index in range(24)
        ],
    }
    snapshot["market_objects"] = {
        "objects": [
            {
                "object_type": "IMPULSE_BOX",
                "label": "GLOBAL",
                "direction": "BUY",
                "bbox": [120, 260, 520, 92],
                "source_indices": list(range(18)),
                "confidence": 0.88,
            },
            {
                "object_type": "PULLBACK_BOX",
                "label": "LOCAL",
                "direction": "BUY",
                "bbox": [330, 210, 430, 148],
                "source_indices": list(range(16, 22)),
                "confidence": 0.84,
            },
            {
                "object_type": "SNIPER_ENTRY_BOX",
                "label": "SNIPER BUY",
                "direction": "BUY",
                "bbox": [356, 176, 386, 204],
                "current_price_inside": True,
                "anchor_candles": [21, 22],
                "confidence": 0.91,
            },
            {
                "object_type": "RETEST_BOX",
                "label": "TRIGGER BUY",
                "direction": "BUY",
                "bbox": [370, 156, 430, 186],
                "anchor_candles": [22],
                "confidence": 0.86,
            },
            {
                "object_type": "TARGET_ZONE_BOX",
                "label": "BUY TARGET",
                "direction": "BUY",
                "bbox": [500, 76, 570, 112],
                "confidence": 0.83,
            },
            {
                "object_type": "INVALIDATION_BOX",
                "label": "BUY INVALIDATION",
                "direction": "BUY",
                "bbox": [328, 230, 430, 252],
                "confidence": 0.78,
            },
            {
                "object_type": "PREDICTION_PATH",
                "label": "BUY PREDICTION PATH",
                "direction": "BUY",
                "path": [[356, 190], [418, 154], [492, 112], [540, 90]],
                "expected_move_candles": 18,
                "confidence": 0.89,
            },
            {
                "object_type": "SUPPORT_TRENDLINE",
                "label": "SUPPORT TRENDLINE",
                "direction": "BUY",
                "line_points": [[128, 260], [540, 96]],
                "touching_now": True,
                "anchor_candles": [4, 22],
                "confidence": 0.82,
            },
            {
                "object_type": "DEMAND_ZONE",
                "label": "DEMAND",
                "direction": "BUY",
                "bbox": [320, 182, 410, 224],
                "current_price_inside": True,
                "confidence": 0.87,
            },
            {
                "object_type": "OPPOSING_FORCE",
                "label": "NEAREST SUPPLY",
                "direction": "SELL",
                "bbox": [500, 76, 570, 112],
                "confidence": 0.76,
            },
            {
                "object_type": "REPLAY_ENTRY",
                "label": "WOULD HAVE ENTERED",
                "direction": "BUY",
                "bbox": [260, 214, 360, 238],
                "confidence": 0.8,
            },
            {
                "object_type": "REPLAY_EXIT",
                "label": "WOULD HAVE EXITED",
                "direction": "BUY",
                "bbox": [490, 86, 580, 116],
                "confidence": 0.8,
            },
        ]
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "FULL_REGISTRY_SUITE"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
    )

    suite = result["evidence"]["overlay_suite_evidence_v3"]
    assert suite["raw_overlay_rows_seen"] >= 12
    assert suite["rows_total"] >= 12
    assert suite["structure_box_count"] >= 2
    assert suite["trendline_count"] >= 1
    assert suite["entry_window_count"] >= 2
    assert suite["target_window_count"] >= 1
    assert suite["invalidation_count"] >= 1
    assert suite["prediction_path_count"] >= 1
    assert suite["opposing_force_count"] >= 1
    assert suite["replay_path_count"] >= 2
    assert suite["first_class_feeds"]["tracking_summary.structure_boxes"] is True
    assert suite["first_class_feeds"]["tracking_summary.trendlines_v3"] is True
    assert "tracking_summary.structure_boxes" not in suite["missing_first_class_feeds"]
    assert "tracking_summary.trendlines_v3" not in suite["missing_first_class_feeds"]


def test_book_strategy_keeps_watching_without_candle_reaction() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["current_candle_acceptance"]["entry_allowed"] = False
    snapshot["current_candle_acceptance"]["accepted"] = False
    snapshot["current_candle_acceptance"]["state"] = "FORMING"

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "AT_DEMAND"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "wait for candle reaction"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=True,
        timing_enter_now=False,
        lane_score=0.80,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "VALID_WATCH"
    assert result["playbook_signal"] == "HOLD"
    assert result["entry_profile"] == "WATCH_ONLY"
    assert not result["hard_blockers"]
    assert any(row["field"] == "current_candle.entry_allowed" for row in result["soft_warnings"])


def test_book_strategy_wait_for_pullback_reclaimed_can_enter_now() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["market_context"]["current_location"] = "MIDDLE_SAFE"
    snapshot["role_flip_confirmed"] = True
    snapshot["pullback_reclaim_ready"] = True
    snapshot["current_candle_acceptance"]["accepted"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "DEMAND_BREAK_RETEST_CONTINUATION"},
            "price_location": {"relative_location": "MID_RANGE", "range_position": 0.5},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "wait for pullback"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=True,
        timing_enter_now=False,
        lane_score=0.86,
        lane_required_score=0.70,
    )

    astar_state = result["astar_decision_state_v3"]
    assert astar_state["pullback_phase"] == "PULLBACK_RECLAIMED"
    assert astar_state["final_state"] == "ENTER_NOW"
    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert not any(row["field"] == "timing_mode" for row in result["hard_blockers"])


def test_book_strategy_blocks_when_models_not_awake() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["runtime_model_health"]["all_required_models_awake"] = False

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={"market_context": snapshot["market_context"], "zones": snapshot["zones"]},
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.84,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "PREPARE"
    assert result["playbook_signal"] == "HOLD"
    assert any(row["field"] == "runtime_model_health" for row in result["hard_blockers"])


def test_book_strategy_can_authorize_without_legacy_lane_acceptance() -> None:
    snapshot = _strategy_snapshot("BUY")
    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "TREND_CONTINUATION", "play_stage": "RETEST_ACCEPTED"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False, "reason": "LANE_SCORE_BELOW_THRESHOLD"},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "legacy timing contributor waiting"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.79,
        lane_required_score=0.95,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["final_decider"] is True
    assert result["evidence"]["lane_authority_ready"] is False
    assert result["evidence"]["final_score_passed"] is False
    assert not result["blockers"]
    assert result["evidence"]["inside_valid_trigger_zone"] is True


def test_book_strategy_downgrades_local_countertrend_reaction_to_scalp_watch() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["global_structure"]["global_side"] = "SELL"
    snapshot["local_micro_structure"]["local_side"] = "BUY"
    snapshot["market_context"]["global_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "BUY"
    snapshot["market_context"]["dominant_side"] = "SELL"

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "BEARISH_PULLBACK_CONTINUATION", "play_stage": "PULLBACK_FAILING"},
            "price_location": {"relative_location": "SUPPLY_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={
            "name": "SNIPER_ZONE_ENTRY",
            "accepted": False,
            "live_trigger_reaction": {"accepted": True},
        },
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "wait for larger bias alignment"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.56,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "PREPARE"
    assert result["playbook_signal"] == "HOLD"
    assert result["playbook"] == "COUNTERTREND_SCALP_ONLY"
    assert result["evidence"]["countertrend_scalp_only"] is True
    assert result["evidence"]["countertrend_against_local"] is True
    assert result["evidence"]["measured_reaction_can_override_timing"] is False
    assert any(row["field"] == "bias_alignment" for row in result["blockers"])


def test_book_strategy_allows_professional_sell_leg_inside_buy_bias() -> None:
    snapshot = _strategy_snapshot("SELL")
    _attach_candle_movement_fixture(snapshot, "SELL")
    snapshot["global_structure"]["global_side"] = "BUY"
    snapshot["local_micro_structure"]["local_side"] = "BUY"
    snapshot["market_context"]["global_side"] = "BUY"
    snapshot["market_context"]["local_side"] = "BUY"
    snapshot["market_context"]["dominant_side"] = "BUY"
    snapshot["professional_thesis_resolution_v3"] = {
        "thesis_state": "SELL_IN_BUY_TRADEABLE_COUNTER_LEG",
        "authority_side": "SELL",
        "primary_bias_side": "BUY",
        "tradeable_counter_leg": True,
        "current_leg_side": "SELL",
        "current_leg_candle_count": 8,
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "SELL_IN_BUY_COUNTER_LEG", "play_stage": "CURRENT_LEG_ACCEPTED"},
            "price_location": {"relative_location": "SUPPLY_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="SELL",
        execution_lane={
            "name": "SNIPER_ZONE_ENTRY",
            "accepted": False,
            "live_trigger_reaction": {"accepted": True},
        },
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "professional counter-leg accepted"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.60,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "SELL"
    assert result["playbook"] == "SELL_IN_BUY_PROFESSIONAL_COUNTER_LEG"
    assert result["market_phase_v3"] == "SELL_IN_BUY_DISTRIBUTION"
    assert result["evidence"]["counter_leg_is_current_truth"] is True
    assert result["evidence"]["countertrend_scalp_only"] is False
    assert result["evidence"]["bias_alignment"] == "SELL_IN_BUY_TRADEABLE_COUNTER_LEG"
    assert not result["hard_blockers"]


def test_book_strategy_promotes_failed_supply_reclaim_buy_continuation() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["role_flip_confirmed"] = True
    snapshot["break_of_structure_confirmed"] = True
    snapshot["retest_confirmed"] = True
    snapshot["zones"] = [
        _zone("failed_supply_001", "SUPPLY", side="BUY", inside=False),
        _zone("target_supply_001", "SUPPLY", side="SELL", distance=0.42),
    ]
    snapshot["zones"][0]["role_flip_confirmed"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "BMS_ROLE_FLIP", "play_stage": "RETEST_ACCEPTED"},
            "price_location": {"relative_location": "ROLE_FLIP_RETEST"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "FAILED_RETEST_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "legacy timing contributor waiting"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_RETEST",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.70,
        lane_required_score=0.90,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["playbook"] == "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION"
    assert result["entry_profile"] == "CONSERVATIVE_RETEST"
    assert result["evidence"]["countertrend_reversal_override"] is True
    assert result["evidence"]["large_move_bias_aligned"] is True


def test_book_strategy_arms_failed_sell_into_demand_aggressive_buy_reversal() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["global_structure"]["global_side"] = "SELL"
    snapshot["local_micro_structure"]["local_side"] = "SELL"
    snapshot["market_context"]["global_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "SELL"
    snapshot["market_context"]["dominant_side"] = "SELL"
    snapshot["market_context"]["current_location"] = "DEMAND_ZONE"
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["angle_features"]["late_chase_risk"] = True
    snapshot["continuation_confirmed"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.46
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.74
    snapshot["two_candle_study"] = {"next_1_direction": "BUY", "next_1_probability": 0.64}

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL", "play_stage": "AGGRESSIVE_REVERSAL_ARMED"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "aggressive reversal reaction"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.57,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["playbook"] == "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert result["evidence"]["failed_continuation_reversal"] is True
    assert result["evidence"]["countertrend_reversal_override"] is True
    assert result["evidence"]["late_chase"] is False
    assert result["evidence"]["late_chase_softened_by_extreme_reversal"] is True


def test_market_play_classifies_failed_sell_into_demand_before_countertrend_scalp() -> None:
    result = analyze_market_play_v3(
        {
            "candidate_side": "BUY",
            "global_side": "SELL",
            "local_side": "SELL",
            "market_context": {
                "global_side": "SELL",
                "local_side": "SELL",
                "current_location": "DEMAND_ZONE",
                "active_zone_type": "DEMAND",
            },
            "local_micro_structure": {"rejection_score": 0.52},
        },
        side="BUY",
        price_location={"relative_location": "LOCAL_LOW", "local_range_position": 0.18},
    )

    play = result["market_play"]
    assert play["primary_play"] == "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL"
    assert play["side_bias"] == "BUY"
    assert play["entry_logic"] == "AGGRESSIVE_BUY_ON_DEMAND_REJECTION_OR_CONSERVATIVE_RETEST"


def test_book_strategy_promotes_live_overlay_buy_reclaim_over_lagging_sell_leg() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["global_structure"]["global_side"] = "SELL"
    snapshot["local_micro_structure"]["local_side"] = "SELL"
    snapshot["market_context"]["global_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "SELL"
    snapshot["market_context"]["dominant_side"] = "SELL"
    snapshot["market_context"]["inside_valid_trigger_zone"] = False
    snapshot["zone_liquidity"]["inside_valid_trigger_zone"] = False
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["continuation_confirmed"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.78
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.34
    snapshot["candle_movement_context_v3"] = {
        "move_stage": "MATURE",
        "visible_candle_count": 57,
        "current_leg": {
            "side": "SELL",
            "candle_count": 7,
            "move_stage": "MATURE",
        },
        "opposing_force_room": {
            "room_ok": True,
            "estimated_candles_to_force": 9,
        },
    }
    snapshot["tracking_summary"] = {
        "current_box": {
            "type": "CURRENT",
            "side": "BUY",
            "bbox": [1127, 309, 1171, 407],
            "contained_candles": [59, 60, 61, 62, 63],
            "anchor_quality": 0.72,
        },
        "support_resistance_zones": [
            {
                "label": "DEMAND",
                "role": "DEMAND",
                "side": "BUY",
                "bbox": [1110, 360, 1160, 392],
                "anchor_candles": [59, 60, 61],
                "current_price_inside": True,
            }
        ],
        "projection": {
            "direction": "BUY",
            "zones": [
                {
                    "label": "BUY_RECLAIM_TRIGGER",
                    "direction": "BUY",
                    "bbox": [1115, 312, 1150, 338],
                    "target_bbox": [1115, 240, 1165, 275],
                    "invalidation_window": [1115, 395, 1165, 420],
                    "contained_candles": [59, 60, 61],
                    "expected_move_candles": 9,
                    "anchor_quality": 0.64,
                    "entry_allowed": True,
                }
            ],
        },
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "RECLAIM_BREAKOUT", "play_stage": "LIVE_BUY_RECLAIM"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "wait for pullback"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="WAIT_FOR_PULLBACK",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.64,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["evidence"]["live_overlay_reclaim_is_current_truth"] is True
    assert result["evidence"]["countertrend_scalp_only"] is False
    assert result["evidence"]["overlay_suite_evidence_v3"]["same_side_current_box_candle_count"] == 5
    assert result["denied_at"] == "NONE"


def test_book_strategy_full_suite_projection_overrides_false_near_zone_late_chase() -> None:
    snapshot = _strategy_snapshot("BUY")
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["angle_features"]["late_chase_risk"] = True
    snapshot["current_candle_acceptance"]["entry_allowed"] = False
    snapshot["current_candle_acceptance"]["accepted"] = False
    snapshot["candle_movement_context_v3"] = {
        "move_stage": "MATURE",
        "visible_candle_count": 42,
        "current_leg": {
            "side": "BUY",
            "candle_count": 8,
            "move_stage": "MATURE",
        },
        "opposing_force_room": {
            "room_ok": True,
            "estimated_candles_to_force": 42,
        },
    }
    snapshot["zones"] = [
        _zone("active_001", "DEMAND", side="BUY", inside=True, distance=0.16),
        _zone("near_supply", "SUPPLY", side="SELL", distance=0.04),
    ]
    snapshot["tracking_summary"] = {
        "current_box": {
            "type": "CURRENT_BOX",
            "side": "BUY",
            "bbox": [1000, 290, 1070, 365],
            "contained_candles": [34, 35, 36, 37, 38],
            "anchor_quality": 0.76,
        },
        "support_resistance_zones": [
            {
                "label": "DEMAND",
                "role": "DEMAND",
                "side": "BUY",
                "bbox": [930, 340, 1080, 382],
                "anchor_candles": [34, 35, 36],
                "current_price_inside": True,
            },
            {
                "label": "SUPPLY",
                "role": "SUPPLY",
                "side": "SELL",
                "bbox": [1020, 250, 1090, 276],
                "anchor_candles": [30, 31],
            },
        ],
        "projection": {
            "direction": "BUY",
            "zones": [
                {
                    "label": "BUY_RECLAIM_TRIGGER",
                    "direction": "BUY",
                    "bbox": [1010, 292, 1065, 330],
                    "target_bbox": [1015, 150, 1095, 190],
                    "invalidation_window": [990, 394, 1080, 425],
                    "contained_candles": [34, 35, 36, 37, 38],
                    "expected_move_candles": 18,
                    "anchor_quality": 0.72,
                    "entry_allowed": True,
                }
            ],
        },
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "RECLAIM_BREAKOUT", "play_stage": "LIVE_BUY_RECLAIM"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": False},
        timing_decision={"entry_now_allowed": False, "entry_timing": {"next_condition": "skip late chase"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="SKIP_LATE_ENTRY",
        final_score_passed=False,
        timing_enter_now=False,
        lane_score=0.33,
        lane_required_score=0.70,
        bad_entry_filter={"active": True, "class": "LATE_CHASE", "severity": 0.9},
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["evidence"]["live_overlay_reclaim_is_current_truth"] is True
    assert result["evidence"]["live_overlay_entry_contract_ready"] is True
    assert result["evidence"]["professional_profit_room_ok"] is True
    assert result["evidence"]["professional_profit_room_source"] == "full_overlay_suite_projection_overrides_near_zone"
    assert result["evidence"]["late_chase_bad_entry_full_suite_override"] is True
    assert result["denied_at"] == "NONE"


def test_book_strategy_master_blocks_late_chase() -> None:
    snapshot = _strategy_snapshot("SELL")
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["angle_features"]["late_chase_risk"] = True

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={"market_context": snapshot["market_context"], "zones": snapshot["zones"]},
        candidate_side="SELL",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.84,
        lane_required_score=0.70,
    )

    assert result["maturity_state"] == "LATE_CHASE"
    assert result["denied_at"] == "LATE_CHASE"
    assert any(row["field"] == "late_chase" for row in result["hard_blockers"])


def test_book_strategy_softens_late_chase_for_book_valid_aggressive_reclaim() -> None:
    snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(snapshot, "BUY")
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["angle_features"]["late_chase_risk"] = True
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.48
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.76

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "DEMAND_REJECTION", "play_stage": "LIVE_TOUCH_REJECTION"},
            "price_location": {"relative_location": "DEMAND_ZONE"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
        bad_entry_filter={"active": True, "class": "LATE_CHASE", "severity": 0.86},
    )

    assert result["maturity_state"] == "ENTER_NOW"
    assert result["playbook_signal"] == "BUY"
    assert result["entry_profile"] == "AGGRESSIVE_SNIPER"
    assert result["evidence"]["raw_late_chase"] is True
    assert result["evidence"]["late_chase"] is False
    assert result["evidence"]["book_valid_reaction"] is True
    assert result["evidence"]["late_chase_softened_by_book_reaction"] is True
    assert result["evidence"]["current_leg_candle_count"] == 8
    assert result["evidence"]["movement_stage"] == "MATURE"
    assert not result["hard_blockers"]


def test_book_strategy_keeps_true_late_chase_when_extension_has_no_room() -> None:
    snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(snapshot, "BUY")
    snapshot["market_context"]["is_late_chase"] = True
    snapshot["market_context"]["opposing_force_distance_ok"] = False
    snapshot["angle_features"]["late_chase_risk"] = True
    snapshot["risk_opposing_force"] = {"distance_to_opposing_force": 0.06, "distance_ok": False}
    late_room: dict[str, Any] = {
        "candidate_side": "BUY",
        "room_ok": False,
        "distance_norm": 0.06,
        "risk_state": "NEAR_OPPOSING_FORCE",
        "estimated_candles_to_force": 1,
        "zone": {},
        "reason": "Opposing force is too close.",
    }
    snapshot["candle_movement_context_v3"] = {
        **snapshot["candle_movement_context_v3"],
        "move_stage": "LATE",
        "opposing_force_room": late_room,
        "current_leg": {
            **snapshot["candle_movement_context_v3"]["current_leg"],
            "move_stage": "LATE",
            "candle_count": 16,
            "opposing_force_room": late_room,
        },
    }

    result = evaluate_book_strategy_master_v3(
        snapshot,
        market={
            "market_context": snapshot["market_context"],
            "market_play": {"primary_play": "LATE_CHASE_AFTER_IMPULSE", "play_stage": "MATURE_OR_LATE"},
            "price_location": {"relative_location": "LOCAL_HIGH"},
            "zones": snapshot["zones"],
        },
        candidate_side="BUY",
        execution_lane={"name": "SNIPER_ZONE_ENTRY", "accepted": True},
        timing_decision={"entry_now_allowed": True, "entry_timing": {"next_condition": "none"}},
        current_candle=snapshot["current_candle_acceptance"],
        timing_mode="ENTER_NOW",
        final_score_passed=True,
        timing_enter_now=True,
        lane_score=0.86,
        lane_required_score=0.70,
        bad_entry_filter={"active": True, "class": "LATE_CHASE_STEEP_IMPULSE", "severity": 0.88},
    )

    assert result["maturity_state"] == "LATE_CHASE"
    assert result["playbook_signal"] == "HOLD"
    assert result["evidence"]["late_chase"] is True
    assert result["evidence"]["current_leg_exhausted"] is True
    assert any(row["field"] == "late_chase" for row in result["hard_blockers"])


def test_model_council_promotes_short_horizon_buy_warning_at_demand_to_candidate_side() -> None:
    council = ModelCouncilV3()
    snapshot = _strategy_snapshot("SELL")
    snapshot["candidate_side"] = "SELL"
    snapshot["global_structure"]["global_side"] = "SELL"
    snapshot["local_micro_structure"]["local_side"] = "SELL"
    snapshot["market_context"]["global_side"] = "SELL"
    snapshot["market_context"]["local_side"] = "SELL"
    snapshot["market_context"]["dominant_side"] = "SELL"
    snapshot["market_context"]["current_location"] = "DEMAND_ZONE"
    snapshot["market_context"]["is_continuation_confirmed"] = False
    snapshot["zone_liquidity"] = {"side": "BUY", "zone_type": "DEMAND", "inside_valid_trigger_zone": True}
    snapshot["zones"] = [
        _zone("demand_001", "DEMAND", side="BUY", inside=True),
        _zone("supply_001", "SUPPLY", side="SELL", distance=0.42),
    ]
    snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.44
    snapshot["current_candle_acceptance"]["close_location_value"] = 0.72
    snapshot["two_candle_study"] = {"next_1_direction": "BUY", "next_1_probability": 0.64}
    snapshot["continuation_confirmed"] = False
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False

    result = council.evaluate(snapshot, now_epoch=NOW)

    assert result["model_council"]["final_side"] == "BUY"
    assert result["book_strategy"]["playbook"] == "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL"
    assert result["book_strategy"]["evidence"]["failed_continuation_reversal"] is True


def test_model_council_reframes_suppressed_current_leg_as_professional_counter_leg() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("BUY")
    second_snapshot = _strategy_snapshot("BUY")
    for snapshot in (first_snapshot, second_snapshot):
        _attach_candle_movement_fixture(snapshot, "SELL")
        snapshot["candidate_side"] = "BUY"
        snapshot["action"] = "BUY"
        snapshot["buy_score"] = 0.70
        snapshot["sell_score"] = 0.52
        snapshot["global_structure"]["global_side"] = "BUY"
        snapshot["local_micro_structure"]["local_side"] = "BUY"
        snapshot["market_context"]["global_side"] = "BUY"
        snapshot["market_context"]["local_side"] = "BUY"
        snapshot["market_context"]["dominant_side"] = "BUY"
        snapshot["market_context"]["current_location"] = "SUPPLY_ZONE"
        snapshot["zone_liquidity"] = {
            "side": "SELL",
            "zone_type": "SUPPLY",
            "inside_valid_trigger_zone": True,
        }
        snapshot["zones"] = [
            _zone("active_supply_001", "SUPPLY", side="SELL", inside=True),
            _zone("opposing_demand_001", "DEMAND", side="BUY", distance=0.42),
        ]
    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 952
    second_snapshot["capture_count"] = 954
    second_snapshot["state_version"] = 1952
    second_snapshot["input_frame_hash"] = "frame_952"
    second_snapshot["previous_frame_hash"] = "frame_951"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)
    resolution = result["model_council"]["professional_thesis_resolution"]
    plan = result["model_council"]["professional_trade_plan"]

    assert result["model_council"]["final_side"] == "SELL"
    assert resolution["thesis_state"] == "SELL_IN_BUY_TRADEABLE_COUNTER_LEG"
    assert resolution["side_reframed"] is True
    assert resolution["tradeable_counter_leg"] is True
    assert result["book_strategy"]["playbook"] == "SELL_IN_BUY_PROFESSIONAL_COUNTER_LEG"
    assert result["book_strategy"]["evidence"]["counter_leg_is_current_truth"] is True
    assert plan["professional_grade"] is True
    assert plan["trend_alignment"]["professional_counter_leg"] is True


def test_model_council_reframes_buy_bias_at_tested_resistance_to_sell_reaction() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("BUY")
    second_snapshot = _strategy_snapshot("BUY")
    for snapshot in (first_snapshot, second_snapshot):
        _attach_candle_movement_fixture(snapshot, "BUY")
        snapshot["candle_movement_context_v3"]["visible_candle_count"] = 56
        snapshot["candle_movement_context_v3"]["opposing_force_room"] = {
            "estimated_room_candles": 5,
            "opposing_force_ok": False,
        }
        snapshot["candle_movement_context"] = snapshot["candle_movement_context_v3"]
        snapshot["candidate_side"] = "BUY"
        snapshot["action"] = "BUY"
        snapshot["buy_score"] = 0.72
        snapshot["sell_score"] = 0.66
        snapshot["global_structure"]["global_side"] = "BUY"
        snapshot["local_micro_structure"]["local_side"] = "BUY"
        snapshot["market_context"]["global_side"] = "BUY"
        snapshot["market_context"]["local_side"] = "BUY"
        snapshot["market_context"]["dominant_side"] = "BUY"
        snapshot["market_context"]["current_location"] = "SUPPLY_ZONE"
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["opposing_force_distance_ok"] = False
        snapshot["zone_liquidity"] = {
            "side": "HOLD",
            "zone_type": "REFERENCE",
            "inside_valid_trigger_zone": False,
        }
        snapshot["zones"] = [
            {
                **_zone("tested_supply_001", "SUPPLY", side="SELL", inside=True, distance=0.02),
                "role": "resistance",
                "zone_family": "SUPPLY_ZONE",
                "touch_count": 6,
                "reaction_count": 3,
                "retest_count": 6,
                "last_touch_age_candles": 1,
                "freshness_state": "TESTED_TWICE",
                "zone_pattern": "DROP_BASE_DROP",
                "significance_score": 0.86,
            },
            _zone("demand_target_001", "DEMAND", side="BUY", distance=0.46),
        ]
        snapshot["risk_opposing_force"] = {
            "side": "BUY",
            "distance_to_opposing_force": 0.04,
            "opposing_force_distance_norm": 0.04,
            "minimum_required_distance": 0.22,
            "distance_ok": False,
            "risk_state": "NEAR_OPPOSING_FORCE",
            "zone": snapshot["zones"][0],
        }
        snapshot["current_candle_acceptance"]["upper_shadow_range_ratio"] = 0.43
        snapshot["current_candle_acceptance"]["close_location_value"] = 0.28
        snapshot["continuation_confirmed"] = False
        snapshot["pullback_confirmed"] = False
        snapshot["retest_confirmed"] = False

    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 962
    second_snapshot["capture_count"] = 964
    second_snapshot["state_version"] = 1962
    second_snapshot["input_frame_hash"] = "frame_962"
    second_snapshot["previous_frame_hash"] = "frame_961"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)
    resolution = result["model_council"]["professional_thesis_resolution"]
    book_strategy = result["book_strategy"]
    plan = result["model_council"]["professional_trade_plan"]

    assert result["model_council"]["final_side"] == "SELL"
    assert resolution["thesis_state"] == "SELL_IN_BUY_OPPOSING_FORCE_REACTION"
    assert resolution["opposing_force_reaction_ready"] is True
    assert resolution["side_reframed"] is True
    assert book_strategy["playbook"] == "SELL_IN_BUY_OPPOSING_FORCE_REACTION"
    assert book_strategy["state"] == "PREPARE"
    assert book_strategy["evidence"]["professional_reaction_is_current_truth"] is False
    assert book_strategy["evidence"]["professional_counter_reaction_needs_confirmation"] is True
    assert book_strategy["evidence"]["opposing_force_ok"] is True
    assert book_strategy["evidence"]["bad_entry_overridden_by_professional_reaction"] is True
    assert result["packet_type"] == "STUDY_PACKET"
    assert result["model_council"]["true_blocker"] == "PLAYBOOK_MATURITY_PREPARE"
    assert result["model_council"].get("blocked_by") != "BUY_AND_SELL_EXECUTABLE_CONFLICT"
    assert plan["professional_grade"] is False
    assert plan["trend_alignment"]["professional_opposing_force_reaction"] is True


def test_model_council_preserves_current_buy_pressure_until_resistance_rejects() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("BUY")
    second_snapshot = _strategy_snapshot("BUY")
    for snapshot in (first_snapshot, second_snapshot):
        _attach_candle_movement_fixture(snapshot, "BUY")
        _attach_executable_identity_lock(snapshot)
        snapshot["candidate_side"] = "BUY"
        snapshot["action"] = "BUY"
        snapshot["buy_score"] = 0.74
        snapshot["sell_score"] = 0.69
        snapshot["global_structure"]["global_side"] = "BUY"
        snapshot["local_micro_structure"]["local_side"] = "BUY"
        snapshot["market_context"]["global_side"] = "BUY"
        snapshot["market_context"]["local_side"] = "BUY"
        snapshot["market_context"]["dominant_side"] = "BUY"
        snapshot["market_context"]["current_location"] = "SUPPLY_ZONE"
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["opposing_force_distance_ok"] = False
        tested_supply = {
            **_zone("tested_supply_watch_001", "SUPPLY", side="SELL", inside=True, distance=0.02),
            "role": "resistance",
            "zone_family": "SUPPLY_ZONE",
            "touch_count": 6,
            "reaction_count": 3,
            "retest_count": 6,
            "last_touch_age_candles": 1,
            "freshness_state": "TESTED_TWICE",
            "zone_pattern": "DROP_BASE_DROP",
            "significance_score": 0.86,
        }
        snapshot["zones"] = [
            tested_supply,
            _zone("buy_target_001", "SUPPLY", side="SELL", distance=0.46),
        ]
        snapshot["risk_opposing_force"] = {
            "side": "BUY",
            "distance_to_opposing_force": 0.04,
            "opposing_force_distance_norm": 0.04,
            "minimum_required_distance": 0.22,
            "distance_ok": False,
            "risk_state": "NEAR_OPPOSING_FORCE",
            "zone": tested_supply,
        }
        snapshot["current_candle_acceptance"]["upper_shadow_range_ratio"] = 0.08
        snapshot["current_candle_acceptance"]["lower_shadow_range_ratio"] = 0.36
        snapshot["current_candle_acceptance"]["close_location_value"] = 0.76
        snapshot["two_candle_study"] = {"next_1_direction": "BUY", "next_1_probability": 0.62}
        candle_context = snapshot["candle_movement_context_v3"]
        candle_context["current_leg"] = {
            "side": "BUY",
            "candle_count": 9,
            "stage": "MATURE",
            "strength": 0.74,
        }
        candle_context["move_stage"] = "MATURE"
        candle_context["visible_candle_count"] = 56
        candle_context["opposing_force_room"] = {
            "estimated_room_candles": 9,
            "opposing_force_ok": True,
        }
        snapshot["candle_movement_context"] = candle_context

    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 963
    second_snapshot["capture_count"] = 965
    second_snapshot["state_version"] = 1963
    second_snapshot["input_frame_hash"] = "frame_963"
    second_snapshot["previous_frame_hash"] = "frame_962"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)
    resolution = result["model_council"]["professional_thesis_resolution"]
    dual = result["model_council"]["dual_thesis_report_v3"]

    assert result["model_council"]["final_side"] == "BUY"
    assert resolution["authority_side"] == "BUY"
    assert resolution["current_pressure_defends_against_opposing_force"] is True
    assert resolution["opposing_force_reaction_ready"] is True
    assert resolution["opposing_force_rejection_confirmed"] is False
    assert dual["current_pressure_side"] == "BUY"
    assert dual["buy"]["status"] == "CURRENT_PRESSURE_DEFENDED"
    assert dual["sell"]["status"] == "WAITING_FOR_REJECTION_PROOF"
    study_dual = result["model_council_study_packet"]["dual_thesis_report_v3"]
    assert study_dual["current_pressure_side"] == "BUY"
    assert result["model_council_study_packet"]["model_council"]["dual_thesis_report_v3"]["buy"]["status"] == "CURRENT_PRESSURE_DEFENDED"


def test_model_council_publishes_packet_for_tested_resistance_sell_reaction_with_room() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("BUY")
    second_snapshot = _strategy_snapshot("BUY")
    for snapshot in (first_snapshot, second_snapshot):
        _attach_candle_movement_fixture(snapshot, "SELL")
        _attach_executable_identity_lock(snapshot)
        snapshot["candidate_side"] = "SELL"
        snapshot["action"] = "SELL"
        snapshot["buy_score"] = 0.72
        snapshot["sell_score"] = 0.7205
        snapshot["global_structure"]["global_side"] = "BUY"
        snapshot["local_micro_structure"]["local_side"] = "SELL"
        snapshot["market_context"]["global_side"] = "BUY"
        snapshot["market_context"]["local_side"] = "SELL"
        snapshot["market_context"]["dominant_side"] = "BUY"
        snapshot["market_context"]["current_location"] = "SUPPLY_ZONE"
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["opposing_force_distance_ok"] = False
        snapshot["market_context"]["is_late_chase"] = True
        snapshot["market_context"]["middle_safe"] = False
        snapshot["zone_liquidity"] = {
            "side": "HOLD",
            "zone_type": "REFERENCE",
            "inside_valid_trigger_zone": False,
        }
        tested_supply = {
            **_zone("tested_supply_001", "SUPPLY", side="SELL", inside=True, distance=0.02),
            "role": "resistance",
            "zone_family": "SUPPLY_ZONE",
            "touch_count": 19,
            "reaction_count": 14,
            "retest_count": 24,
            "last_touch_age_candles": 0,
            "freshness_state": "TESTED_TWICE",
            "zone_pattern": "DROP_BASE_DROP",
            "significance_score": 1.0,
        }
        snapshot["zones"] = [
            tested_supply,
            _zone("demand_target_001", "DEMAND", side="BUY", distance=0.46),
        ]
        snapshot["risk_opposing_force"] = {
            "side": "BUY",
            "distance_to_opposing_force": 0.04,
            "opposing_force_distance_norm": 0.04,
            "minimum_required_distance": 0.22,
            "distance_ok": False,
            "risk_state": "NEAR_OPPOSING_FORCE",
            "zone": tested_supply,
        }
        snapshot["current_candle_acceptance"]["phase"] = "VALID"
        snapshot["current_candle_acceptance"]["candle_phase"] = "VALID"
        snapshot["current_candle_acceptance"]["entry_allowed"] = True
        snapshot["current_candle_acceptance"]["upper_shadow_range_ratio"] = 0.43
        snapshot["current_candle_acceptance"]["close_location_value"] = 0.28
        snapshot["angle_features"]["late_chase_risk"] = True
        snapshot["angle_features"]["post_impulse_wait_required"] = True
        snapshot["timing"] = {"state": "READY", "side": "SELL", "expiry_seconds": 300}
        snapshot["timing_decision"] = {
            "entry_now_allowed": True,
            "side": "SELL",
            "entry_timing": {
                "entry_window": "READY",
                "valid_until_epoch_ms": int((NOW + 300) * 1000),
                "expiry_seconds": 300,
            },
        }
        candle_context = snapshot["candle_movement_context_v3"]
        candle_context["current_leg"] = {
            "side": "SELL",
            "candle_count": 4,
            "stage": "STILL_RECLAIMING",
            "strength": 0.74,
        }
        candle_context["move_stage"] = "MATURE"
        candle_context["visible_candle_count"] = 56
        candle_context["opposing_force_room"] = {
            "estimated_room_candles": 5,
            "opposing_force_ok": False,
        }
        snapshot["candle_movement_context"] = candle_context

    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 1964
    second_snapshot["capture_count"] = 1964
    second_snapshot["state_version"] = 2964
    second_snapshot["input_frame_hash"] = "frame_1964"
    second_snapshot["previous_frame_hash"] = "frame_1963"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)
    council_state = result["model_council"]
    expected_move = result["allowance_package"]["expected_move_time"]

    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["execution"]["enabled"] is True
    assert result["allowance_package"]["executable"] is True
    assert council_state["final_state"] == "EXECUTABLE"
    assert council_state["final_side"] == "SELL"
    assert council_state["true_blocker"] == "NONE"
    assert council_state["execution_lane"]["professional_reaction_lane_authority"] is True
    assert council_state["trade_permission"]["permission_state"] == "GRANTED"
    assert result["book_strategy"]["state"] == "ENTER_NOW"
    assert result["book_strategy"]["playbook"] == "SELL_IN_BUY_OPPOSING_FORCE_REACTION"
    assert expected_move["expected_candle_count"] >= 8
    assert expected_move["expected_duration_sec"] >= 40 * 60
    assert expected_move["professional_trade_plan"]["profit_discipline"]["micro_horizon_is_diagnostic_only"] is True


def test_model_council_blocks_tested_resistance_reaction_when_target_room_is_tiny() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("BUY")
    second_snapshot = _strategy_snapshot("BUY")
    for snapshot in (first_snapshot, second_snapshot):
        _attach_candle_movement_fixture(snapshot, "SELL")
        _attach_executable_identity_lock(snapshot)
        snapshot["candidate_side"] = "SELL"
        snapshot["action"] = "SELL"
        snapshot["buy_score"] = 0.72
        snapshot["sell_score"] = 0.7205
        snapshot["global_structure"]["global_side"] = "BUY"
        snapshot["local_micro_structure"]["local_side"] = "SELL"
        snapshot["market_context"]["global_side"] = "BUY"
        snapshot["market_context"]["local_side"] = "SELL"
        snapshot["market_context"]["dominant_side"] = "BUY"
        snapshot["market_context"]["current_location"] = "SUPPLY_ZONE"
        snapshot["market_context"]["inside_valid_trigger_zone"] = False
        snapshot["market_context"]["opposing_force_distance_ok"] = False
        tested_supply = {
            **_zone("tested_supply_tight_001", "SUPPLY", side="SELL", inside=True, distance=0.02),
            "role": "resistance",
            "zone_family": "SUPPLY_ZONE",
            "touch_count": 7,
            "reaction_count": 3,
            "retest_count": 7,
            "last_touch_age_candles": 0,
            "freshness_state": "TESTED_TWICE",
            "zone_pattern": "DROP_BASE_DROP",
            "significance_score": 0.9,
        }
        snapshot["zones"] = [
            tested_supply,
            _zone("near_demand_target_001", "DEMAND", side="BUY", distance=0.08),
        ]
        snapshot["risk_opposing_force"] = {
            "side": "BUY",
            "distance_to_opposing_force": 0.04,
            "opposing_force_distance_norm": 0.04,
            "minimum_required_distance": 0.22,
            "distance_ok": False,
            "risk_state": "NEAR_OPPOSING_FORCE",
            "zone": tested_supply,
        }
        snapshot["current_candle_acceptance"]["phase"] = "VALID"
        snapshot["current_candle_acceptance"]["candle_phase"] = "VALID"
        snapshot["current_candle_acceptance"]["entry_allowed"] = True
        snapshot["current_candle_acceptance"]["upper_shadow_range_ratio"] = 0.43
        snapshot["current_candle_acceptance"]["close_location_value"] = 0.28
        snapshot["angle_features"]["late_chase_risk"] = False
        candle_context = snapshot["candle_movement_context_v3"]
        candle_context["current_leg"] = {
            "side": "SELL",
            "candle_count": 4,
            "stage": "STILL_RECLAIMING",
            "strength": 0.74,
        }
        candle_context["move_stage"] = "MATURE"
        candle_context["visible_candle_count"] = 56
        candle_context["opposing_force_room"] = {
            "estimated_room_candles": 4,
            "opposing_force_ok": False,
        }
        snapshot["candle_movement_context"] = candle_context

    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 1974
    second_snapshot["capture_count"] = 1974
    second_snapshot["state_version"] = 2974
    second_snapshot["input_frame_hash"] = "frame_1974"
    second_snapshot["previous_frame_hash"] = "frame_1973"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)
    hard_fields = {row["field"] for row in result["book_strategy"]["hard_blockers"]}

    assert result["packet_type"] == "STUDY_PACKET"
    assert "professional_profit_room" in hard_fields
    assert result["book_strategy"]["evidence"]["professional_profit_room_candles"] < 8


def test_model_council_allows_sell_resumption_when_buy_pullback_rejects_supply() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("SELL")
    second_snapshot = _strategy_snapshot("SELL")
    for snapshot in (first_snapshot, second_snapshot):
        _attach_candle_movement_fixture(snapshot, "BUY")
        snapshot["candle_movement_context_v3"]["visible_candle_count"] = 56
        snapshot["candle_movement_context_v3"]["opposing_force_room"] = {
            "estimated_room_candles": 5,
            "opposing_force_ok": False,
        }
        snapshot["candle_movement_context"] = snapshot["candle_movement_context_v3"]
        snapshot["candidate_side"] = "BUY"
        snapshot["action"] = "BUY"
        snapshot["buy_score"] = 0.78
        snapshot["sell_score"] = 0.72
        snapshot["global_structure"]["global_side"] = "SELL"
        snapshot["local_micro_structure"]["local_side"] = "BUY"
        snapshot["market_context"]["global_side"] = "SELL"
        snapshot["market_context"]["local_side"] = "BUY"
        snapshot["market_context"]["dominant_side"] = "SELL"
        snapshot["market_context"]["current_location"] = "SUPPLY_ZONE"
        snapshot["market_context"]["inside_valid_trigger_zone"] = True
        snapshot["market_context"]["opposing_force_distance_ok"] = False
        snapshot["zone_liquidity"] = {
            "side": "SELL",
            "zone_type": "SUPPLY",
            "inside_valid_trigger_zone": True,
        }
        snapshot["zones"] = [
            {
                **_zone("tested_supply_resume_001", "SUPPLY", side="SELL", inside=True, distance=0.01),
                "role": "resistance",
                "zone_family": "SUPPLY_ZONE",
                "touch_count": 4,
                "reaction_count": 2,
                "retest_count": 4,
                "last_touch_age_candles": 1,
                "freshness_state": "TESTED_TWICE",
                "zone_pattern": "RALLY_BASE_DROP",
                "significance_score": 0.82,
            },
            _zone("demand_target_resume_001", "DEMAND", side="BUY", distance=0.44),
        ]
        snapshot["risk_opposing_force"] = {
            "side": "BUY",
            "distance_to_opposing_force": 0.03,
            "opposing_force_distance_norm": 0.03,
            "minimum_required_distance": 0.22,
            "distance_ok": False,
            "risk_state": "NEAR_OPPOSING_FORCE",
            "zone": snapshot["zones"][0],
        }
        snapshot["current_candle_acceptance"]["upper_shadow_range_ratio"] = 0.45
        snapshot["current_candle_acceptance"]["close_location_value"] = 0.24
        snapshot["continuation_confirmed"] = False
        snapshot["pullback_confirmed"] = False
        snapshot["retest_confirmed"] = False

    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 972
    second_snapshot["capture_count"] = 974
    second_snapshot["state_version"] = 1972
    second_snapshot["input_frame_hash"] = "frame_972"
    second_snapshot["previous_frame_hash"] = "frame_971"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)
    resolution = result["model_council"]["professional_thesis_resolution"]
    book_strategy = result["book_strategy"]
    plan = result["model_council"]["professional_trade_plan"]

    assert result["model_council"]["final_side"] == "SELL"
    assert resolution["thesis_state"] == "SELL_TREND_RESUMPTION_FROM_SUPPLY"
    assert resolution["primary_bias_zone_rejection_ready"] is True
    assert resolution["side_reframed"] is True
    assert book_strategy["playbook"] == "SELL_TREND_RESUMPTION_FROM_SUPPLY"
    assert book_strategy["maturity_state"] == "ENTER_NOW"
    assert book_strategy["evidence"]["professional_bias_resumption_reaction"] is True
    assert book_strategy["evidence"]["professional_reaction_is_current_truth"] is True
    assert book_strategy["evidence"]["countertrend_scalp_only"] is False
    assert plan["professional_grade"] is True
    assert plan["trend_alignment"]["professional_bias_resumption_reaction"] is True
    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["promotion_trace"]["reasoning_execution_blocked"] is False
    assert result["promotion_trace"]["hard_bad_entry_class_active"] is False


def test_model_council_carries_book_strategy_in_study_and_packet() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(first_snapshot, "BUY")
    first = council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot = _strategy_snapshot("BUY")
    _attach_candle_movement_fixture(second_snapshot, "BUY")
    second_snapshot["frame_id"] = 902
    second_snapshot["capture_count"] = 904
    second_snapshot["state_version"] = 1902
    second_snapshot["input_frame_hash"] = "frame_902"
    second_snapshot["previous_frame_hash"] = "frame_901"
    second = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)

    assert first["book_strategy"]["single_timeframe_mode"] is True
    assert second["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert second["book_strategy"]["maturity_state"] == "ENTER_NOW"
    assert second["allowance_package"]["book_strategy_maturity"] == "ENTER_NOW"
    assert second["allowance_package"]["execution_authority"] == BOOK_STRATEGY_EXECUTION_AUTHORITY
    assert second["allowance_package"]["candle_movement"]["visible_candle_count"] == 12
    assert second["allowance_package"]["candle_movement"]["current_leg_candle_count"] == 8
    assert second["allowance_package"]["entry_window"]["duration_sec"] == 300
    assert second["allowance_package"]["entry_window"]["candle_count"] == 1
    assert second["allowance_package"]["thesis_horizon"]["expected_candle_count"] >= 4
    assert second["allowance_package"]["expected_move_time"]["expected_duration_sec"] >= 20 * 60
    assert second["allowance_package"]["expected_move_time"]["expected_candle_count"] >= 4
    assert second["allowance_package"]["expected_move_time"]["projected_total_current_leg_candles"] >= 12
    assert second["allowance_package"]["professional_trade_plan"]["professional_grade"] is True
    assert second["candle_movement_context_v3"]["move_stage"] == "MATURE"
    assert second["model_council"]["strategy_read"]["doctrine"] == "single_timeframe_visible_history_only"


def test_model_council_playbook_publishes_when_legacy_lane_rejects_score() -> None:
    council = ModelCouncilV3()
    first_snapshot = _strategy_snapshot("SELL")
    second_snapshot = _strategy_snapshot("SELL")
    for snapshot in (first_snapshot, second_snapshot):
        snapshot["execution_threshold"] = 0.98
        snapshot["lane_thresholds"] = {
            "SNIPER_ZONE_ENTRY": 0.98,
            "LOCAL_BREAKDOWN_CONTINUATION": 0.98,
            "FAILED_RETEST_ENTRY": 0.98,
            "WAVE_RIDING_CONTINUATION": 0.98,
            "MOMENTUM_ACCEPTANCE_ENTRY": 0.98,
            "HISTORY_MATCHED_CONTINUATION": 0.98,
        }
    council.evaluate(first_snapshot, now_epoch=NOW)
    second_snapshot["frame_id"] = 912
    second_snapshot["capture_count"] = 914
    second_snapshot["state_version"] = 1912
    second_snapshot["input_frame_hash"] = "frame_912"
    second_snapshot["previous_frame_hash"] = "frame_911"

    result = council.evaluate(second_snapshot, now_epoch=NOW + 0.5)

    assert result["packet_type"] == "PG_EXECUTION_PACKET_V3"
    assert result["book_strategy_state"] == "ENTER_NOW"
    assert result["model_council"]["execution_lane"]["accepted"] is False
    assert result["allowance_package"]["playbook_authorized"] is True
    assert result["allowance_package"]["lane_is_contributor"] is True
    assert result["allowance_package"]["score_passed"] is False
    assert result["model_council"]["contributors_are_diagnostic"] is True
