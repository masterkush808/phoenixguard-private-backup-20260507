import importlib.util
import json
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "Backend" / "launch" / "phoenixguard_direct_trade_bridge.py"
CALIBRATION_MODULE_PATH = PROJECT_ROOT / "Backend" / "launch" / "phoenixguard_trigger_calibration.py"


def _load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_bridge_module(module_name: str = "phoenixguard_direct_trade_bridge"):
    return _load_module(module_name, MODULE_PATH)


def _load_calibration_module(module_name: str = "phoenixguard_trigger_calibration"):
    return _load_module(module_name, CALIBRATION_MODULE_PATH)


def test_bridge_accepts_bias_signal_by_default():
    bridge = _load_bridge_module()
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "BUY",
            "headline_action": "BUY",
            "entry_state": "WAIT_FOR_TRIGGER",
            "market": "USD/CAD OTC",
            "published_epoch": time.time(),
            "signal_age_sec": 0.5,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "expiry_seconds": 180,
            "valid_until_epoch": time.time() + 300,
            "market_confidence": 0.88,
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
            },
        },
        "tracking_summary": {
            "latest_candle_color": "green",
            "trend_context": {
                "micro_bias": "BUY",
                "local_bias": "BUY",
                "global_bias": "BUY",
            },
            "candle_movement_context_v3": {
                "move_stage": "TRANSITION",
                "summary": "Fresh BUY reversal is forming.",
                "current_leg": {
                    "label": "L9 BUY TRANSITION",
                    "side": "HOLD",
                    "candidate_side": "BUY",
                    "transition_state": "FORMING",
                    "confirmation_count": 1,
                    "start_index": 156,
                    "end_index": 156,
                },
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0)
    assert result["side"] == "BUY"
    assert result["source"] == "direct_live_bias_signal"
    assert result["current_visual_side"] == "BUY"
    assert result["trigger_token"]


def test_bridge_book_rules_mode_still_available_when_explicitly_requested():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_book_rules_mode")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "published_epoch": time.time(),
            "signal_age_sec": 0.5,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "ROLE_FLIP_RETEST_READY",
                "action": "BUY",
                "watch_side": "BUY",
                "actionable": True,
                "confidence": 0.88,
                "playbook": "ROLE_FLIP_RETEST",
                "scenario": "Completed book-rule buy retest is aligned.",
                "trigger": "Enter on the completed retest candle.",
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
                "pair": "USD/CAD OTC",
                "timeframe": "M5",
                "rule_traceability": {
                    "selected_book_rule_ids": ["RULE_A", "RULE_B"],
                },
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="book_rules")
    assert result["side"] == "BUY"
    assert result["source"] == "direct_book_rule_signal"


def test_bridge_high_frequency_mode_still_available_when_explicitly_requested():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_high_frequency_mode")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "entry_state": "WAIT_FOR_TRIGGER",
            "market": "USD/CAD OTC",
            "published_epoch": time.time(),
            "signal_age_sec": 0.5,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "expiry_seconds": 180,
            "valid_until_epoch": time.time() + 300,
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
            },
            "two_candle_study": {
                "status": "READY",
                "timeframe": "M5",
                "primary_pressure": "BUY",
                "confidence": 0.79,
                "summary": "Two-candle continuation is leaning BUY.",
                "next_candle_forecast": {
                    "direction": "BUY",
                    "confidence": 0.79,
                },
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="high_frequency")
    assert result["side"] == "BUY"
    assert result["source"] == "direct_high_frequency_signal"
    assert result["next_candle_bias"] == "BUY"
    assert result["trigger_lane"] == "HIGH_FREQUENCY_TWO_CANDLE"


def test_bridge_bias_mode_still_available_when_explicitly_requested():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_bias_mode")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "execution_permission": "WAIT",
            "entry_state": "WAIT_FOR_TRIGGER",
            "headline_action": "BUY",
            "market_confidence": 0.88,
            "confidence": 0.88,
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")
    assert result["side"] == "BUY"
    assert result["source"] == "direct_live_bias_signal"


def test_bridge_bias_direction_ignores_execution_permission_when_visual_timing_is_ready():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_bias_timing_ready")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "SELL",
            "execution_permission": "WAIT",
            "entry_state": "SNIPER_READY",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "execution_timing": {
                # This field can describe the counter-move being timed. It must
                # not replace PhoenixGuard's published SELL bias authority.
                "side": "BUY",
                "entry_allowed": True,
                "timing_class": "pullback_rejection",
            },
            "timing_signal": {
                "entry_state": "TRIGGER_READY",
                "instruction": "Fresh SELL rejection is visible.",
            },
            "market_study_v3": {
                "closed_candle_key": "closed-ready-sell",
                "closed_candle_sequence": 12,
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")

    assert result["side"] == "SELL"
    assert result["execution_permission"] == "WAIT"
    assert result["entry_timing_ready"] is True
    assert result["entry_timing_state"] == "TRIGGER_READY"


def test_bridge_reports_visual_watch_instead_of_permanent_timing_side_mismatch():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_timing_side_watch")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "BUY",
            "entry_state": "SNIPER_WATCH",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "execution_timing": {
                "side": "SELL",
                "entry_allowed": False,
                "timing_class": "history_area_wait",
                "block_reason": "Nearest significant support/resistance is opposing the entry before a clean trigger.",
            },
            "timing_signal": {
                "entry_state": "WATCH",
                "instruction": "BUY watch area is being tested. Wait for rejection/reclaim before entry.",
            },
            "market_study_v3": {
                "closed_candle_key": "closed-buy-watch",
                "closed_candle_sequence": 16,
            },
        },
    }

    signal = bridge._bias_signal_from_state(payload)

    assert signal["side"] == "BUY"
    assert signal["actionable"] is False
    assert signal["entry_timing_state"] == "WATCH"
    assert "support/resistance" in signal["entry_timing_reason"]
    assert "not current BUY bias" not in signal["entry_timing_reason"]


def test_bridge_bias_watch_requires_reclaim_not_only_an_aligned_candle():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_watch_aligned")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "SELL",
            "entry_state": "SNIPER_WATCH",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "execution_timing": {
                "entry_allowed": False,
                "timing_class": "history_area_wait",
                "block_reason": "SELL is already low; wait for a resistance pullback.",
            },
            "timing_signal": {"entry_state": "WATCH"},
            "market_study_v3": {
                "closed_candle_key": "closed-sell-aligned",
                "closed_candle_sequence": 17,
                "candle_intelligence": {
                    "latest": {
                        "direction": "BEARISH",
                        "ratios": {"range_vs_sequence_median": 0.82},
                        "interaction": {"rejection": {"detected": False, "side": "NONE"}},
                    }
                },
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="resistance pullback"):
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")


def test_bridge_bias_watch_waits_when_current_visual_candle_opposes_bias():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_watch_opposed")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "BUY",
            "entry_state": "SNIPER_WATCH",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "execution_timing": {"entry_allowed": False},
            "timing_signal": {"entry_state": "WATCH"},
            "market_study_v3": {
                "closed_candle_key": "closed-buy-opposed",
                "closed_candle_sequence": 18,
                "candle_intelligence": {
                    "latest": {
                        "direction": "BEARISH",
                        "ratios": {"range_vs_sequence_median": 0.7},
                    }
                },
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="current visual candle/leg is SELL"):
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")


def test_bridge_reports_staleness_before_a_timing_wait():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_stale_before_timing")
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": time.time() - 45,
        "latest_signal": {
            "action": "SELL",
            "entry_state": "SNIPER_WATCH",
            "published_epoch": time.time() - 40,
            "valid_until_epoch": time.time() + 300,
            "execution_timing": {"entry_allowed": False},
            "timing_signal": {"entry_state": "WATCH"},
            "market_study_v3": {
                "closed_candle_key": "closed-stale-watch",
                "closed_candle_sequence": 19,
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="Live signal stale"):
        bridge._resolve_trade_payload(
            payload,
            score_threshold=0.0,
            max_signal_age_seconds=15,
            signal_source="bias",
        )


@pytest.mark.parametrize(
    ("side", "timing_class", "block_reason"),
    [
        ("SELL", "history_area_wait", "SELL is already low; wait for a resistance pullback or clean retest."),
        ("BUY", "history_area_wait", "BUY is already high; wait for a support pullback or clean retest."),
    ],
)
def test_bridge_bias_waits_instead_of_chasing_history_extremes(side, timing_class, block_reason):
    bridge = _load_bridge_module(f"phoenixguard_direct_trade_bridge_anti_chase_{side.lower()}")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": side,
            "entry_state": "SNIPER_WATCH",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "execution_timing": {
                "side": side,
                "entry_allowed": False,
                "timing_class": timing_class,
                "block_reason": block_reason,
            },
            "timing_signal": {
                "entry_state": "WATCH",
                "instruction": "Wait for rejection/reclaim before entry.",
            },
            "market_study_v3": {
                "closed_candle_key": f"closed-extended-{side.lower()}",
                "closed_candle_sequence": 13,
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="wait for"):
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")


def test_bridge_bias_fallback_rejects_an_extended_directional_impulse():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_extended_impulse")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "SELL",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "market_study_v3": {
                "closed_candle_key": "closed-extended-impulse",
                "closed_candle_sequence": 14,
                "candle_intelligence": {
                    "latest": {
                        "candle_id": "14",
                        "direction": "BEARISH",
                        "ratios": {"range_vs_sequence_median": 2.4},
                        "interaction": {"rejection": {"detected": False, "side": "NONE"}},
                    },
                },
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="already extended"):
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")


def test_bridge_bias_fallback_accepts_fresh_rejection_after_pullback():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_pullback_rejection")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "SELL",
            "execution_permission": "WAIT",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "market_study_v3": {
                "closed_candle_key": "closed-pullback-rejection",
                "closed_candle_sequence": 15,
                "candle_intelligence": {
                    "latest": {
                        "candle_id": "15",
                        "direction": "BEARISH",
                        "ratios": {"range_vs_sequence_median": 0.72},
                        "interaction": {"rejection": {"detected": True, "side": "HIGH"}},
                    },
                    "recent_candles": [
                        {"candle_id": "14", "direction": "BULLISH"},
                        {
                            "candle_id": "15",
                            "direction": "BEARISH",
                            "ratios": {"range_vs_sequence_median": 0.72},
"interaction": {"rejection": {"detected": True, "side": "HIGH"}},
                        },
                    ],
                },
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")

    assert result["side"] == "SELL"
    assert result["entry_timing_ready"] is True
    assert result["entry_timing_class"] == "pullback_rejection"


def test_bridge_bias_reentry_still_waits_when_placement_gate_blocks_the_side():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_reentry_placement_blocked")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "SELL",
            "execution_permission": "WAIT",
            "entry_state": "WAIT_FOR_TRIGGER",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "execution_timing": {
                "side": "SELL",
                "entry_allowed": False,
                "timing_class": "history_area_wait",
                "block_reason": "Nearest significant support/resistance is opposing the entry before a clean trigger.",
                "opposing_force_risk": 0.92,
                "current_flow_continuation_ready": False,
                "breakout_confirmation": False,
            },
            "timing_signal": {
                "entry_state": "WATCH",
                "instruction": "Wait for rejection/reclaim before entry.",
            },
            "market_study_v3": {
                "closed_candle_key": "closed-reentry-blocked",
                "closed_candle_sequence": 15,
                "candle_intelligence": {
                    "latest": {
                        "candle_id": "15",
                        "direction": "BEARISH",
                        "ratios": {"range_vs_sequence_median": 0.72},
                        "interaction": {"rejection": {"detected": True, "side": "HIGH"}},
                    },
                    "recent_candles": [
                        {"candle_id": "14", "direction": "BULLISH"},
                        {
                            "candle_id": "15",
                            "direction": "BEARISH",
                            "ratios": {"range_vs_sequence_median": 0.72},
                            "interaction": {"rejection": {"detected": True, "side": "HIGH"}},
                        },
                    ],
                },
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="opposing force risk"):
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")


def test_bridge_bias_mode_falls_back_to_market_study_when_top_level_action_is_hold():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_bias_hold_fallback")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "HOLD",
            "execution_action": "HOLD",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
                "directional_read": {
                    "side": "SELL",
                },
                "behavior": {
                    "current_state": {
                        "direction": "DOWN",
                    },
                    "current_segment": {
                        "direction": "DOWN",
                        "start_index": 94,
                        "end_index": 94,
                    },
                },
                "candle_intelligence": {
                    "latest": {
                        "candle_id": "closed-bootstrap",
                        "direction": "BEARISH",
                    },
                },
            },
        },
        "tracking_summary": {
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")
    assert result["side"] == "SELL"
    assert result["source"] == "direct_live_bias_signal"


def test_bridge_bias_mode_waits_when_current_visual_leg_conflicts_with_published_bias():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_bias_conflict")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "SELL",
            "headline_action": "SELL",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
            },
        },
        "tracking_summary": {
            "latest_candle_color": "green",
            "trend_context": {
                "micro_bias": "BUY",
                "local_bias": "BUY",
            },
            "candle_movement_context_v3": {
                "move_stage": "TRANSITION",
                "current_leg": {
                    "label": "L9 BUY TRANSITION",
                    "side": "HOLD",
                    "candidate_side": "BUY",
                    "transition_state": "FORMING",
                    "confirmation_count": 1,
                    "start_index": 156,
                    "end_index": 156,
                },
            },
        },
    }

    signal = bridge._bias_signal_from_state(payload)
    assert signal["side"] == "SELL"
    assert signal["current_visual_side"] == "BUY"
    assert signal["actionable"] is False
    assert signal["entry_timing_state"] == "VISUAL_SIDE_CONFLICT"
    assert "current visual candle/leg is BUY" in signal["reject_reason"]


def test_bridge_bias_mode_uses_market_study_fallback_when_tracking_summary_is_sparse():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_bias_market_study_fallback")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "action": "SELL",
            "headline_action": "SELL",
            "published_epoch": time.time(),
            "valid_until_epoch": time.time() + 300,
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
                "directional_read": {
                    "side": "BUY",
                },
                "behavior": {
                    "current_state": {
                        "state": "UP_SWING",
                        "direction": "UP",
                    },
                    "current_segment": {
                        "state": "UP_SWING",
                        "direction": "UP",
                        "start_index": 94,
                        "end_index": 94,
                    },
                },
                "regression": {
                    "current_pressure": {
                        "side": "SELL",
                    },
                },
                "candle_intelligence": {
                    "latest": {
                        "candle_id": "closed-bootstrap",
                        "direction": "BULLISH",
                    },
                },
            },
        },
        "tracking_summary": {
            "market_study_v3": {
                "closed_candle_key": "closed-bootstrap",
            },
        },
    }

    signal = bridge._bias_signal_from_state(payload)
    assert signal["side"] == "SELL"
    assert signal["current_visual_side"] == "BUY"
    assert signal["actionable"] is False
    assert signal["entry_timing_state"] == "VISUAL_SIDE_CONFLICT"


def test_bridge_does_not_let_a_historical_current_leg_reverse_live_sell_pressure():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_historical_leg_regression")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": now,
        "latest_signal": {
            "action": "SELL",
            "headline_action": "SELL",
            "published_epoch": now,
            "market_study_v3": {
                "closed_candle_key": "closed-live-sell",
                "closed_candle_sequence": 96,
                "behavior": {
                    "current_state": {"state": "DOWN_SWING", "direction": "DOWN"},
                    "current_segment": {"direction": "DOWN", "start_index": 88, "end_index": 88},
                },
                "candle_intelligence": {
                    "latest": {"candle_id": "94", "direction": "BEARISH"},
                },
                "regression": {
                    "current_pressure": {"side": "SELL", "confidence": 1.0},
                },
            },
        },
        "tracking_summary": {
            "control_direction": "SELL",
            "local_direction": "SELL",
            "latest_candle_color": "green",
            "candle_movement_context_v3": {
                "move_stage": "EXHAUSTED",
                "current_leg": {
                    "label": "H7 BUY",
                    "side": "BUY",
                    "candle_count": 19,
                    "duration": {"seconds": 5700},
                    "start_index": 57,
                    "end_index": 75,
                },
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")
    assert result["side"] == "SELL"
    assert result["current_visual_side"] == "SELL"
    assert result["current_visual_source"] == "latest_signal.market_study_v3.behavior.current_state.direction"
    assert result["trigger_token"] == "closed-live-sell|SELL"


def test_bridge_synthesizes_live_candle_identity_instead_of_dropping_fresh_bias():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_synthetic_candle")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "last_capture_epoch": now,
        "latest_signal": {
            "action": "BUY",
            "published_epoch": now,
        },
        "tracking_summary": {"control_direction": "BUY"},
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")
    assert result["side"] == "BUY"
    assert str(result["candle_key"]).startswith("live:")
    assert isinstance(result["candle_sequence"], int)


def test_coerce_box_mapping_handles_box_entries_without_unpacking_error():
    bridge = _load_bridge_module()
    payload = {
        "buy_button": {"x": 100, "y": 200},
        "sell_button": [300, 400],
    }

    result = bridge._coerce_box_mapping(payload)
    assert result["buy_button"] == (100, 200)
    assert result["sell_button"] == (300, 400)


def test_coerce_box_mapping_reads_runtime_trigger_manifest_aliases():
    bridge = _load_bridge_module()
    payload = {
        "boxes": {
            "buy_click": {"x": 1841, "y": 232},
            "sell_click": {"x": 1841, "y": 262},
            "chart_anchor": {"x": 1219, "y": 789},
        },
        "actions": {
            "buy": {"click": {"x": 1841, "y": 232}},
            "sell": {"click": {"x": 1841, "y": 262}},
        },
    }

    result = bridge._coerce_box_mapping(payload)
    assert result["buy_button"] == (1841, 232)
    assert result["sell_button"] == (1841, 262)
    assert result["chart_anchor"] == (1219, 789)


def test_bridge_trigger_state_blocks_duplicate_same_signal():
    bridge = _load_bridge_module()
    state = bridge._BridgeTriggerState(rearm_seconds=0.0, flip_guard_seconds=0.0)
    trade = {"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-1", "timeframe_seconds": 300}

    assert state.should_trigger(trade) == (True, "triggered")
    assert state.should_trigger(trade) == (False, "unchanged_live_state")


def test_bridge_trigger_state_blocks_duplicate_visual_cycle_even_when_signal_id_changes():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_visual_cycle")
    state = bridge._BridgeTriggerState(rearm_seconds=0.0, flip_guard_seconds=0.0, max_trades_per_candle=0)
    first = {
        "side": "BUY",
        "signal_id": "sig-1",
        "published_epoch": 1.0,
        "candle_key": "c-1",
        "timeframe_seconds": 300,
        "trigger_token": "L9 BUY TRANSITION|BUY|HOLD|BUY|FORMING|1|GREEN|156|156",
    }
    second = {
        **first,
        "signal_id": "sig-2",
        "published_epoch": 2.0,
    }

    assert state.should_trigger(first) == (True, "triggered")
    assert state.should_trigger(second) == (False, "unchanged_live_state")


def test_bridge_trigger_state_blocks_immediate_opposite_side_flip_with_guard():
    bridge = _load_bridge_module()
    state = bridge._BridgeTriggerState(
        rearm_seconds=0.0,
        flip_guard_seconds=30.0,
        lock_side_per_candle=True,
    )

    assert state.should_trigger({"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-1", "timeframe_seconds": 300}) == (True, "triggered")
    assert state.should_trigger({"side": "SELL", "signal_id": "sig-2", "published_epoch": 2.0, "candle_key": "c-1", "timeframe_seconds": 300}) == (False, "opposite_side_blocked_same_candle")


def test_bridge_trigger_state_defaults_to_one_trade_per_candle():
    bridge = _load_bridge_module()
    state = bridge._BridgeTriggerState(rearm_seconds=0.0, flip_guard_seconds=0.0)

    first = {"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-42", "timeframe_seconds": 300}
    second = {"side": "BUY", "signal_id": "sig-2", "published_epoch": 2.0, "candle_key": "c-42", "timeframe_seconds": 300}

    assert state.should_trigger(first) == (True, "triggered")
    assert state.should_trigger(second) == (False, "candle_trade_limit_reached")


def test_bridge_trigger_state_can_limit_trades_per_candle_when_requested():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_candle_limit")
    state = bridge._BridgeTriggerState(
        rearm_seconds=0.0,
        flip_guard_seconds=0.0,
        max_trades_per_candle=1,
    )

    first = {"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-42", "timeframe_seconds": 300}
    second = {"side": "SELL", "signal_id": "sig-2", "published_epoch": 2.0, "candle_key": "c-42", "timeframe_seconds": 300}

    assert state.should_trigger(first) == (True, "triggered")
    assert state.should_trigger(second) == (False, "candle_trade_limit_reached")


def test_resolve_trade_payload_includes_candle_identity_and_timeframe_seconds():
    bridge = _load_bridge_module()
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "signal_id": "sig-99",
            "published_epoch": now,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "action": "BUY",
                "actionable": True,
                "status": "STRUCTURE_CONTINUATION_READY",
                "closed_candle_key": "abc123",
                "closed_candle_sequence": 77,
                "pair": "USD/CAD OTC",
                "timeframe": "M5",
            },
            "market_study_v3": {
                "closed_candle_key": "abc123",
                "closed_candle_sequence": 77,
            },
        },
    }

    trade = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="book_rules")
    assert trade["candle_key"] == "abc123"
    assert trade["candle_sequence"] == 77
    assert trade["timeframe_seconds"] == 300


def test_high_frequency_rejects_when_forecast_is_not_ready():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_hf_not_ready")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "entry_state": "WAIT_FOR_TRIGGER",
            "published_epoch": now,
            "market_study_v3": {
                "closed_candle_key": "closed-01",
                "closed_candle_sequence": 10,
            },
            "two_candle_study": {
                "status": "WARMING",
                "timeframe": "M5",
                "primary_pressure": "BUY",
                "confidence": 0.79,
                "next_candle_forecast": {
                    "direction": "BUY",
                    "confidence": 0.79,
                },
            },
        },
    }

    try:
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="high_frequency")
    except bridge.TradeRejected as exc:
        assert "not READY" in str(exc)
    else:
        raise AssertionError("Expected TradeRejected when the high-frequency forecast is not ready")


def test_hybrid_mode_still_available_when_explicitly_requested():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_hybrid_explicit")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "entry_state": "WAIT_FOR_TRIGGER",
            "published_epoch": now,
            "signal_age_sec": 0.4,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "timing_signal": {
                "entry_state": "WATCH",
                "timing_score": 0.52,
                "instruction": "Bias is BUY and the fast cycle is near trigger.",
            },
            "two_candle_study": {
                "status": "READY",
                "primary_pressure": "BUY",
                "confidence": 0.79,
                "summary": "Two-candle continuation is leaning BUY.",
            },
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "WAITING_FOR_CURRENT_BOOK_TRIGGER",
                "action": "WAIT",
                "watch_side": "BUY",
                "actionable": False,
                "confidence": 0.0,
                "playbook": "STRUCTURE_CONTINUATION",
                "closed_candle_key": "closed-hf-01",
                "closed_candle_sequence": 33,
                "pair": "USD/CAD OTC",
                "timeframe": "M5",
                "structure": {
                    "major_side": "BUY",
                    "inner_side": "BUY",
                },
                "candlestick": {
                    "side": "BUY",
                },
            },
            "market_study_v3": {
                "closed_candle_key": "closed-hf-01",
                "closed_candle_sequence": 33,
            },
        },
    }

    trade = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="hybrid")
    assert trade["side"] == "BUY"
    assert trade["source"] == "direct_hybrid_high_frequency_signal"


def test_resolve_trade_payload_does_not_drop_bias_when_closed_candle_identity_is_missing():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_closed_candle_fallback")
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "action": "BUY",
            "published_epoch": time.time(),
        },
        "tracking_summary": {"control_direction": "BUY"},
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0)
    assert result["side"] == "BUY"
    assert str(result["candle_key"]).startswith("live:")


def test_resolve_trade_payload_rejects_waiting_book_rule_signal():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_waiting_book_rule")
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "execution_action": "HOLD",
            "actionable": False,
            "execution_permission": "WAIT",
            "published_epoch": time.time(),
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "WAITING_FOR_CURRENT_BOOK_TRIGGER",
                "action": "WAIT",
                "watch_side": "BUY",
                "actionable": False,
                "closed_candle_key": "closed-02",
                "closed_candle_sequence": 11,
            },
            "market_study_v3": {
                "closed_candle_key": "closed-02",
                "closed_candle_sequence": 11,
            },
        },
    }

    try:
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="book_rules")
    except bridge.TradeRejected as exc:
        assert "No actionable live signal" in str(exc)
    else:
        raise AssertionError("Expected TradeRejected when the book-rule signal is still WAIT")


def test_resolve_trade_payload_rejects_stale_high_frequency_signal():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_stale_high_frequency")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "entry_state": "WAIT_FOR_TRIGGER",
            "published_epoch": now - 21.0,
            "signal_age_sec": 21.0,
            "pipeline_latency_sec": 18.0,
            "freshness_window_sec": 300.0,
            "market_study_v3": {
                "closed_candle_key": "closed-03",
                "closed_candle_sequence": 12,
            },
            "two_candle_study": {
                "status": "READY",
                "timeframe": "M5",
                "primary_pressure": "SELL",
                "confidence": 0.91,
                "summary": "Two-candle continuation is leaning SELL.",
                "next_candle_forecast": {
                    "direction": "SELL",
                    "confidence": 0.91,
                },
            },
        },
    }

    try:
        bridge._resolve_trade_payload(
            payload,
            score_threshold=0.0,
            max_signal_age_seconds=15.0,
            signal_source="high_frequency",
        )
    except bridge.TradeRejected as exc:
        assert "Live signal stale" in str(exc)
        assert exc.details["effective_signal_age_seconds"] >= 21.0
        assert "published_age_seconds" in exc.details
        assert "capture_age_seconds" in exc.details
    else:
        raise AssertionError("Expected TradeRejected when the high-frequency signal is stale")


def test_resolve_trade_payload_reports_freshness_metrics_on_success():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_freshness_metrics")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "last_capture_epoch": now - 0.4,
        "display_capture_epoch": now - 0.3,
        "display_published_epoch": now - 0.2,
        "latest_signal": {
            "action": "BUY",
            "headline_action": "BUY",
            "published_epoch": now - 0.5,
            "signal_age_sec": 0.5,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "pipeline_latency_sec": 0.18,
            "market_study_v3": {
                "closed_candle_key": "closed-freshness",
                "closed_candle_sequence": 44,
            },
        },
        "tracking_summary": {
            "latest_candle_color": "green",
            "trend_context": {
                "micro_bias": "BUY",
            },
        },
    }

    trade = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias")
    assert trade["signal_age_seconds"] < 1.0
    assert trade["published_age_seconds"] >= 0.0
    assert trade["capture_age_seconds"] >= 0.0
    assert trade["display_capture_age_seconds"] >= 0.0
    assert trade["pipeline_latency_seconds"] == 0.18


def test_bias_mode_uses_fresh_observation_timestamp_instead_of_stale_signal_age_field():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_bias_fresh_observation")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "last_capture_epoch": now - 0.2,
        "display_capture_epoch": now - 0.3,
        "display_published_epoch": now - 0.25,
        "latest_signal": {
            "action": "SELL",
            "published_epoch": now - 0.2,
            "signal_age_sec": 75.0,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "market_study_v3": {
                "closed_candle_key": "closed-fresh-observation",
                "closed_candle_sequence": 55,
                "directional_read": {
                    "side": "SELL",
                },
                "behavior": {
                    "current_state": {
                        "direction": "DOWN",
                    },
                    "current_segment": {
                        "direction": "DOWN",
                        "start_index": 94,
                        "end_index": 94,
                    },
                },
                "candle_intelligence": {
                    "latest": {
                        "candle_id": "closed-fresh-observation",
                        "direction": "BEARISH",
                    },
                },
            },
        },
        "tracking_summary": {
            "market_study_v3": {
                "closed_candle_key": "closed-fresh-observation",
            },
        },
    }

    trade = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="bias", max_signal_age_seconds=15.0)
    assert trade["side"] == "SELL"
    assert trade["signal_age_seconds"] < 2.0


def test_bridge_defaults_trigger_manifest_to_durable_user_store(monkeypatch, tmp_path):
    local_app_data = tmp_path / "localappdata"
    manifest_path = local_app_data / "PhoenixGuard" / "calibration" / "trigger_calibration_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("PHOENIXGUARD_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_RUNTIME_LOCK_PATH", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_CALIBRATION_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_localappdata")
    assert bridge.DEFAULT_TRIGGER_MANIFEST == manifest_path
    assert bridge._calibration_manifest_paths(bridge.PROJECT_ROOT) == [manifest_path.resolve()]


def test_read_live_state_prefers_fresher_local_runtime_snapshot(monkeypatch, tmp_path):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_prefer_local_runtime")
    runtime_dir = tmp_path / "runtime" / "live"
    session_dir = runtime_dir / "data_live" / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    local_state_path = session_dir / "compact_live_state.json"
    local_payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": 200.0,
        "latest_signal": {
            "published_epoch": 200.0,
            "action": "SELL",
        },
    }
    local_state_path.write_text(json.dumps(local_payload), encoding="utf-8")

    monkeypatch.setattr(bridge, "_default_live_runtime_dir", lambda project_root=None: runtime_dir)
    monkeypatch.setattr(
        bridge,
        "_read_json_url",
        lambda url, timeout_sec: {
            "session_id": "pocket-live-8788",
            "last_capture_epoch": 100.0,
            "latest_signal": {
                "published_epoch": 100.0,
                "action": "BUY",
            },
        },
    )

    payload = bridge._read_live_state(
        base_url="http://127.0.0.1:8793",
        session_id="pocket-live-8788",
        timeout_sec=30.0,
    )
    assert payload["_bridge_state_source"] == "local_runtime_file"
    assert payload["last_capture_epoch"] == 200.0
    assert payload["latest_signal"]["action"] == "SELL"


def test_read_fresh_trade_does_not_spin_until_timeout_on_a_stale_snapshot(monkeypatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_stale_single_read")
    calls = 0
    stale_epoch = time.time() - 60
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": stale_epoch,
        "display_capture_epoch": stale_epoch,
        "latest_signal": {
            "action": "SELL",
            "published_epoch": stale_epoch,
            "market_study_v3": {
                "closed_candle_key": "closed-stale-single-read",
                "closed_candle_sequence": 21,
            },
        },
    }

    def read_state(**_kwargs):
        nonlocal calls
        calls += 1
        return payload

    monkeypatch.setattr(bridge, "_read_live_state", read_state)

    with pytest.raises(bridge.TradeRejected, match="Live signal stale"):
        bridge._read_fresh_trade(
            base_url="http://127.0.0.1:8793",
            session_id="pocket-live-8788",
            timeout_sec=30,
            score_threshold=0,
            max_signal_age_seconds=15,
            signal_source="bias",
        )

    assert calls == 1


def test_trigger_calibration_defaults_output_to_durable_user_store(monkeypatch, tmp_path):
    local_app_data = tmp_path / "localappdata"
    calibration_dir = local_app_data / "PhoenixGuard" / "calibration"

    monkeypatch.delenv("PHOENIXGUARD_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_CALIBRATION_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    calibration = _load_calibration_module("phoenixguard_trigger_calibration_localappdata")
    assert calibration.DEFAULT_OUTPUT == calibration_dir / "trigger_calibration_manifest.json"


def test_trigger_calibration_writes_atomic_primary_and_backup(tmp_path):
    calibration = _load_calibration_module("phoenixguard_trigger_calibration_backup")
    output = tmp_path / "trigger_calibration_manifest.json"
    manifest = {"boxes": {"buy_click": {"x": 1, "y": 2}, "sell_click": {"x": 3, "y": 4}}}

    calibration._write_manifest(output, manifest)

    assert json.loads(output.read_text(encoding="utf-8")) == manifest
    backup = tmp_path / "trigger_calibration_manifest.backup.json"
    assert json.loads(backup.read_text(encoding="utf-8")) == manifest
    assert not output.with_suffix(".json.tmp").exists()


def test_trigger_calibration_manifest_stores_saved_timing_policy():
    calibration = _load_calibration_module("phoenixguard_trigger_calibration_timing")
    manifest = calibration._build_manifest(
        chart_anchor=(10, 20),
        buy_click=(30, 40),
        sell_click=(50, 60),
        fixed_expiry_seconds=180,
        fixed_amount=1.0,
        score_threshold=0.0,
        chart_focus_settle_seconds=5.0,
        pre_click_delay_seconds=6.0,
        pointer_move_duration_seconds=0.4,
    )

    assert manifest["timing_policy"] == {
        "chart_focus_settle_seconds": 5.0,
        "pre_click_delay_seconds": 6.0,
        "pointer_move_duration_seconds": 0.4,
    }


def test_trigger_manifest_to_boxes_uses_saved_timing_policy():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_timing")
    manifest = {
        "boxes": {
            "chart_anchor": {"x": 100, "y": 200},
            "buy_click": {"x": 300, "y": 400},
            "sell_click": {"x": 500, "y": 600},
        },
        "fixed_amount": 2.0,
        "fixed_expiry_seconds": 180,
        "timing_policy": {
            "chart_focus_settle_seconds": 5.0,
            "pre_click_delay_seconds": 8.0,
            "inter_click_delay_seconds": 0.6,
            "pointer_move_duration_seconds": 0.5,
        },
    }

    boxes, chart_anchor, fixed_amount, fixed_expiry, timing_policy = bridge._trigger_manifest_to_boxes(manifest)
    assert boxes["buy_click"] == (300, 400)
    assert chart_anchor == (100, 200)
    assert fixed_amount == 2.0
    assert fixed_expiry == 180
    assert timing_policy == {
        "chart_focus_settle_seconds": 5.0,
        "pre_click_delay_seconds": 8.0,
        "inter_click_delay_seconds": 0.6,
        "pointer_move_duration_seconds": 0.5,
    }


def test_send_direct_clicks_honors_saved_delays(monkeypatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_click_timing")
    recorded_calls: list[tuple[object, ...]] = []
    recorded_sleeps: list[float] = []

    fake_pyautogui = SimpleNamespace(
        moveTo=lambda x, y, duration=0.0: recorded_calls.append(("moveTo", x, y, duration)),
        click=lambda x, y: recorded_calls.append(("click", x, y)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: recorded_sleeps.append(seconds))

    bridge._send_direct_clicks(
        {"buy_click": (30, 40)},
        chart_anchor=(10, 20),
        side="BUY",
        expiry_seconds=180,
        fixed_amount=1.0,
        timing_policy={
            "chart_focus_settle_seconds": 5.0,
            "pre_click_delay_seconds": 7.0,
            "inter_click_delay_seconds": 0.5,
            "pointer_move_duration_seconds": 0.4,
        },
    )

    assert recorded_calls == [
        ("moveTo", 10, 20, 0.4),
        ("click", 10, 20),
        ("moveTo", 30, 40, 0.4),
        ("click", 30, 40),
        ("click", 30, 40),
    ]
    assert recorded_sleeps == [7.0, 0.5]


def test_send_direct_clicks_refreshes_bias_after_wait_before_final_click(monkeypatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_click_refresh")
    events: list[tuple[object, ...]] = []

    fake_pyautogui = SimpleNamespace(
        moveTo=lambda x, y, duration=0.0: events.append(("moveTo", x, y, duration)),
        click=lambda x, y: events.append(("click", x, y)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    def refresh_trade():
        events.append(("refresh",))
        return {"side": "SELL"}

    result = bridge._send_direct_clicks(
        {"buy_click": (30, 40), "sell_click": (50, 60)},
        chart_anchor=(10, 20),
        side="BUY",
        expiry_seconds=180,
        timing_policy={
            "chart_focus_settle_seconds": 0.0,
            "pre_click_delay_seconds": 5.0,
            "inter_click_delay_seconds": 0.4,
            "pointer_move_duration_seconds": 0.4,
        },
        refresh_trade_before_click=refresh_trade,
    )

    assert events == [
        ("moveTo", 10, 20, 0.4),
        ("click", 10, 20),
        ("sleep", 5.0),
        ("refresh",),
        ("moveTo", 50, 60, 0.4),
        ("click", 50, 60),
        ("sleep", 0.4),
        ("click", 50, 60),
    ]
    assert result == {"executed_side": "SELL", "refreshed_before_click": True, "press_count": 2}


def test_bridge_trigger_state_starts_cooldown_after_ten_executed_trades(monkeypatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_cooldown")
    clock = {"now": 1000.0}
    monkeypatch.setattr(bridge.time, "time", lambda: clock["now"])

    state = bridge._BridgeTriggerState(
        rearm_seconds=0.0,
        flip_guard_seconds=0.0,
        max_trades_per_candle=1,
        max_trades_per_session=0,
        cooldown_after_trades=10,
        cooldown_seconds=480.0,
    )

    for index in range(10):
        trade = {
            "side": "BUY",
            "signal_id": f"sig-{index}",
            "published_epoch": float(index + 1),
            "candle_key": f"c-{index}",
            "timeframe_seconds": 300,
        }
        assert state.should_trigger(trade) == (True, "triggered")
        state.record_trade_execution()

    blocked_trade = {
        "side": "BUY",
        "signal_id": "sig-blocked",
        "published_epoch": 99.0,
        "candle_key": "c-blocked",
        "timeframe_seconds": 300,
    }
    assert state.should_trigger(blocked_trade) == (False, "cooldown_active")

    clock["now"] += 481.0
    assert state.should_trigger(blocked_trade) == (True, "triggered")


def test_bridge_trigger_state_default_cooldown_is_disabled():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_no_default_cooldown")
    state = bridge._BridgeTriggerState(max_trades_per_session=0)

    for index in range(12):
        trade = {
            "side": "BUY" if index % 2 == 0 else "SELL",
            "signal_id": f"sig-{index}",
            "published_epoch": float(index + 1),
            "candle_key": f"c-{index}",
            "timeframe_seconds": 300,
        }
        assert state.should_trigger(trade) == (True, "triggered")
        state.record_trade_execution()


def test_bridge_trigger_state_stops_after_eight_executed_trades():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_session_trade_cap")
    state = bridge._BridgeTriggerState(max_trades_per_candle=1)

    for index in range(8):
        trade = {
            "side": "BUY",
            "signal_id": f"sig-{index}",
            "published_epoch": float(index + 1),
            "candle_key": f"c-{index}",
        }
        assert state.should_trigger(trade) == (True, "triggered")
        state.record_trade_execution(trade)

    assert state.should_trigger(
        {"side": "BUY", "signal_id": "sig-8", "published_epoch": 9.0, "candle_key": "c-8"}
    ) == (False, "session_trade_limit_reached")


def test_bias_mode_uses_fresh_direct_visual_bias_instead_of_stale_full_study():
    bridge = _load_bridge_module("phoenixguard_direct_visual_bias_fresh")
    now_epoch = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": now_epoch - 90.0,
        "display_capture_epoch": now_epoch - 120.0,
        "display_published_epoch": now_epoch - 100.0,
        "latest_signal": {
            "action": "SELL",
            "published_epoch": now_epoch - 90.0,
        },
        "direct_visual_bias_v3": {
            "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
            "side": "SELL",
            "confidence": 0.91,
            "market": "GBP/AUD OTC",
            "timeframe": "M5",
            "timeframe_seconds": 300,
            "candle_sequence": 42,
            "candle_key": "direct:GBP/AUD OTC:M5:42",
            "observed_epoch": now_epoch - 2.0,
            "published_epoch": now_epoch - 1.0,
            "pipeline_latency_seconds": 1.0,
            "source": "phoenixguard_candle_palette_v3",
        },
    }

    trade = bridge._resolve_trade_payload(
        payload,
        signal_source="bias",
        max_signal_age_seconds=15.0,
    )
    freshness = bridge._freshness_context(payload, trade, now_epoch=now_epoch)

    assert trade["side"] == "SELL"
    assert trade["source"] == "phoenixguard_direct_visual_bias_v3"
    assert trade["candle_key"] == "direct:GBP/AUD OTC:M5:42"
    assert freshness["effective_signal_age_seconds"] < 3.0
    assert freshness["freshness_basis"] == "direct_visual_bias_capture"
    assert freshness["display_capture_age_seconds"] == 0.0


def test_direct_visual_bias_waits_for_reclaim_when_it_opposes_structural_bias():
    bridge = _load_bridge_module("phoenixguard_direct_visual_bias_structural_alignment")
    now_epoch = time.time()
    payload = {
        "latest_signal": {
            "action": "BUY",
            "entry_state": "TRIGGER_READY",
            "execution_timing": {"entry_allowed": True},
        },
        "direct_visual_bias_v3": {
            "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
            "side": "SELL",
            "market": "USD/CAD OTC",
            "timeframe": "M5",
            "observed_epoch": now_epoch,
        },
    }

    with pytest.raises(bridge.TradeRejected, match="structural BUY bias"):
        bridge._resolve_trade_payload(payload, signal_source="bias")

    payload["direct_visual_bias_v3"]["side"] = "BUY"
    trade = bridge._resolve_trade_payload(payload, signal_source="bias")
    assert trade["side"] == "BUY"
    assert trade["entry_timing_ready"] is True


def test_bias_mode_rejects_stale_direct_visual_bias():
    bridge = _load_bridge_module("phoenixguard_direct_visual_bias_stale")
    stale_epoch = time.time() - 20.0
    payload = {
        "direct_visual_bias_v3": {
            "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
            "side": "BUY",
            "market": "GBP/AUD OTC",
            "timeframe": "M5",
            "observed_epoch": stale_epoch,
        }
    }

    with pytest.raises(bridge.TradeRejected, match="Live signal stale"):
        bridge._resolve_trade_payload(
            payload,
            signal_source="bias",
            max_signal_age_seconds=15.0,
        )


def test_direct_visual_bias_uses_listener_liveness_by_default_and_honors_entry_wait():
    bridge = _load_bridge_module("phoenixguard_direct_visual_bias_listener_liveness")
    stale_epoch = time.time() - 3_600.0
    payload = {
        "latest_signal": {
            "entry_state": "WAIT_FOR_TRIGGER",
            "execution_timing": {
                "entry_allowed": False,
                "block_reason": "Wait for the BUY pullback/reclaim.",
            },
        },
        "direct_visual_bias_v3": {
            "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
            "side": "BUY",
            "market": "USD/JPY OTC",
            "timeframe": "M5",
            "observed_epoch": stale_epoch,
        },
    }

    with pytest.raises(bridge.TradeRejected, match="pullback/reclaim"):
        bridge._resolve_trade_payload(payload, signal_source="bias")

    payload["latest_signal"] = {
        "entry_state": "TRIGGER_READY",
        "execution_timing": {"entry_allowed": True},
    }
    trade = bridge._resolve_trade_payload(payload, signal_source="bias")

    assert bridge.DEFAULT_MAX_SIGNAL_AGE_SECONDS == 0.0
    assert trade["side"] == "BUY"
    assert trade["entry_timing_ready"] is True
    assert trade["valid_until_epoch"] == 0.0


def test_read_live_state_attaches_direct_visual_bias_sidecar(monkeypatch, tmp_path):
    bridge = _load_bridge_module("phoenixguard_direct_visual_bias_sidecar")
    runtime_dir = tmp_path / "runtime" / "live"
    session_dir = (
        runtime_dir
        / "data_live"
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / "pocket-live-8788"
    )
    session_dir.mkdir(parents=True)
    (session_dir / "compact_live_state.json").write_text(
        json.dumps({"session_id": "pocket-live-8788", "last_capture_epoch": 100.0}),
        encoding="utf-8",
    )
    (session_dir / "direct_visual_bias_v3.json").write_text(
        json.dumps(
            {
                "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
                "side": "SELL",
                "observed_epoch": 101.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bridge, "_default_live_runtime_dir", lambda project_root=None: runtime_dir
    )

    payload = bridge._read_live_state(
        base_url="http://127.0.0.1:8793",
        session_id="pocket-live-8788",
        timeout_sec=1.0,
    )

    assert payload["direct_visual_bias_v3"]["side"] == "SELL"


def test_bridge_listener_uses_phoenixguard_session_updates(monkeypatch, tmp_path):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_listener")
    runtime_dir = tmp_path / "runtime" / "live"
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(
        bridge, "_default_live_runtime_dir", lambda project_root=None: runtime_dir
    )
    now_epoch = time.time()
    event = {
        "session_id": "pocket-live-8788",
        "direct_visual_bias_v3": {
            "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
            "side": "BUY",
            "market": "USD/JPY OTC",
            "timeframe": "M5",
            "observed_epoch": now_epoch,
            "published_epoch": now_epoch,
            "source": "phoenixguard_candle_palette_v3",
        },
    }

    class _StreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter(
                [
                    b": heartbeat\n",
                    b"\n",
                    b"event: SESSION_UPDATE\n",
                    f"data: {json.dumps(event)}\n".encode("utf-8"),
                    b"\n",
                ]
            )

    monkeypatch.setattr(bridge.request, "urlopen", lambda *_args, **_kwargs: _StreamResponse())

    update = next(
        bridge._iter_phoenixguard_session_updates(
            base_url="http://127.0.0.1:8793",
            session_id="pocket-live-8788",
            timeout_sec=30.0,
        )
    )
    trade = bridge._trade_from_listener_payload(
        update,
        base_url="http://127.0.0.1:8793",
        session_id="pocket-live-8788",
        score_threshold=0.0,
        fixed_expiry_seconds_override=180,
        max_signal_age_seconds=15.0,
        signal_source="bias",
    )

    assert update == event
    assert trade["side"] == "BUY"
    assert trade["state_source"] == "phoenixguard_session_stream"
    assert trade["expiry_seconds"] == 180


def test_bridge_uses_listener_transport_by_default():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_listener_default")

    assert bridge._build_parser().parse_args([]).transport == "listener"


def test_bridge_once_mode_uses_the_phoenixguard_listener(
    monkeypatch,
    capsys,
):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_listener_once")

    class NoopLock:
        def __init__(self, **_kwargs):
            pass

        def acquire(self) -> None:
            return None

        def release(self) -> None:
            return None

    event = {"session_id": "pocket-live-8788", "direct_visual_bias_v3": {"side": "SELL"}}
    monkeypatch.setattr(bridge, "_InstanceLock", NoopLock)
    monkeypatch.setattr(
        bridge,
        "_iter_phoenixguard_session_updates",
        lambda **_kwargs: iter([event]),
    )
    monkeypatch.setattr(
        bridge,
        "_trade_from_listener_payload",
        lambda payload, **_kwargs: {
            "side": payload["direct_visual_bias_v3"]["side"],
            "state_source": "phoenixguard_session_stream",
        },
    )
    monkeypatch.setattr(
        bridge,
        "_trade_once",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("poll fallback ran")),
    )

    assert bridge.main(["--once", "--dry-run", "--transport", "listener"]) == 0
    assert '"state_source": "phoenixguard_session_stream"' in capsys.readouterr().out
