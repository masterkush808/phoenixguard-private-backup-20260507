from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenixguard.decision.market_intelligence_v3 import analyze_market_intelligence_v3
from phoenixguard.decision.outcome_feedback_v3 import log_outcome_feedback
from phoenixguard.decision.pair_behavior_profile_v3 import analyze_pair_behavior_profile_v3
from phoenixguard.decision.reasoning_arbitrator_v3 import analyze_reasoning_arbitration_v3
from phoenixguard.memory.visual_play_memory_bank import VisualPlayMemoryBank


def _candles(values: list[float]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for previous, current in zip(values, values[1:]):
        rows.append(
            {
                "open": previous,
                "close": current,
                "high": max(previous, current) + 0.02,
                "low": min(previous, current) - 0.02,
            }
        )
    return rows


def _snapshot(side: str = "SELL") -> dict[str, Any]:
    opposite = "BUY" if side == "SELL" else "SELL"
    return {
        "session_id": "reasoning-test",
        "symbol": "EURUSD_OTC",
        "timeframe": "M5",
        "candidate_side": side,
        "global_side": side,
        "local_side": side,
        "global_confidence": 0.82,
        "local_confidence": 0.78,
        "continuation_confirmed": True,
        "pullback_confirmed": True,
        "retest_confirmed": True,
        "current_location": "LOCAL_HIGH" if side == "SELL" else "LOCAL_LOW",
        "candles": _candles([1.30, 1.24, 1.20, 1.16, 1.13, 1.17, 1.19, 1.18]),
        "angle_features": {
            "screen_space_angle": 32.0,
            "impulse_length": 0.55,
            "pullback_depth": 0.34,
            "wick_rejection_score": 0.62,
            "angle_break_probability": 0.20,
            "late_chase_risk": False,
        },
        "risk_context": {
            "distance_to_opposing_force": 0.38,
            "minimum_required_distance": 0.22,
        },
        "history_context": {
            "best_matches": [
                {
                    "memory_id": "mem_144",
                    "setup_type": f"{'BEARISH' if side == 'SELL' else 'BULLISH'}_PULLBACK_CONTINUATION",
                    "side": side,
                    "regime": "PULLBACK_PHASE",
                    "entry_location": "LOCAL_HIGH" if side == "SELL" else "LOCAL_LOW",
                    "outcome": f"GOOD_{side}",
                    "similarity": 0.83,
                }
            ],
            "similarity_state": "REPEATING_SUCCESSFUL_PATH",
            "best_match_setup": f"{side}_CONTINUATION_AFTER_PULLBACK",
            "best_match_outcome": "WIN",
            "historical_entry_quality": "GOOD",
            "historical_late_entry_risk": "LOW",
        },
        "zones": [
            {
                "zone_id": "supply_001",
                "zone_type": "SUPPLY",
                "distance_from_current": 0.05 if side == "SELL" else 0.42,
                "current_price_inside": side == "SELL",
            },
            {
                "zone_id": "demand_001",
                "zone_type": "DEMAND",
                "distance_from_current": 0.42 if side == "SELL" else 0.05,
                "current_price_inside": side == "BUY",
            },
        ],
        "skill_gates": [{"name": "memory_similarity", "score": 0.12, "pass_fail": False}],
        "frames_used": 80,
    }


def test_pullback_continuation_play_detected() -> None:
    result = analyze_market_intelligence_v3(_snapshot("SELL"))

    assert result["market_play"]["primary_play"] == "BEARISH_PULLBACK_CONTINUATION"
    assert result["market_play"]["secondary_play"] == "SUPPLY_REJECTION"
    assert result["regime"]["primary"] == "PULLBACK_PHASE"
    assert result["price_location"]["relative_location"] == "LOCAL_HIGH"
    assert result["final_reasoning_decision"]["play"] == "BEARISH_PULLBACK_CONTINUATION"


def test_range_high_sell_reaction_detected() -> None:
    snapshot = _snapshot("SELL")
    snapshot["market_regime"] = "RANGING"
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False

    result = analyze_market_intelligence_v3(snapshot)

    assert result["market_play"]["primary_play"] == "RANGE_HIGH_SELL_REACTION"
    assert result["market_play"]["entry_logic"] == "SELL_HIGH_AFTER_SUPPLY_REACTION"


def test_buy_high_after_impulse_flagged() -> None:
    snapshot = _snapshot("BUY")
    snapshot["current_location"] = "LOCAL_HIGH"
    snapshot["breakout_confirmed"] = True
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["angle_features"]["impulse_length"] = 0.70
    snapshot["angle_features"]["wick_rejection_score"] = 0.10

    result = analyze_market_intelligence_v3(snapshot)

    assert result["bad_entry_filter"]["active"] is True
    assert result["bad_entry_filter"]["class"] == "BUY_HIGH_AFTER_IMPULSE"
    assert result["bad_entry_filter"]["action"] == "WAIT_FOR_PULLBACK"


def test_sell_low_after_drop_flagged() -> None:
    snapshot = _snapshot("SELL")
    snapshot["current_location"] = "LOCAL_LOW"
    snapshot["breakout_confirmed"] = True
    snapshot["pullback_confirmed"] = False
    snapshot["retest_confirmed"] = False
    snapshot["angle_features"]["impulse_length"] = 0.70
    snapshot["angle_features"]["wick_rejection_score"] = 0.10

    result = analyze_market_intelligence_v3(snapshot)

    assert result["bad_entry_filter"]["active"] is True
    assert result["bad_entry_filter"]["class"] == "SELL_LOW_AFTER_DROP"
    assert result["bad_entry_filter"]["action"] == "WAIT_FOR_PULLBACK"


def test_memory_confirmation_adjusts_confidence() -> None:
    bank = VisualPlayMemoryBank.from_rows(
        [
            {
                "memory_id": "mem_144",
                "setup_type": "BEARISH_PULLBACK_CONTINUATION",
                "side": "SELL",
                "regime": "PULLBACK_PHASE",
                "entry_location": "LOCAL_HIGH",
                "outcome": "GOOD_SELL",
                "similarity": 0.83,
            }
        ]
    )

    result = bank.confirm(
        side="SELL",
        market_play={"primary_play": "BEARISH_PULLBACK_CONTINUATION", "secondary_play": "SUPPLY_REJECTION"},
        regime={"primary": "PULLBACK_PHASE"},
        price_location={"relative_location": "LOCAL_HIGH"},
    )

    assert result["memory_confirmation"]["memory_vote"] == "SELL"
    assert result["memory_confirmation"]["confidence_adjustment"] > 0
    assert result["memory_confirmation"]["top_matches"][0]["memory_id"] == "mem_144"


def test_pair_profile_reports_wick_risk() -> None:
    result = analyze_pair_behavior_profile_v3(
        {
            "symbol": "EURUSD_OTC",
            "timeframe": "M5",
            "candles": [
                {"open": 1.0, "high": 1.8, "low": 0.8, "close": 1.05},
                {"open": 1.1, "high": 1.7, "low": 0.9, "close": 1.12},
            ],
        }
    )

    assert result["pair_profile"]["volatility_class"].startswith("WICKY")
    assert result["pair_profile"]["drawdown_first_frequency"] >= 0.37
    assert "wicks" in result["pair_profile"]["warning"]


def test_each_model_outputs_role_vote() -> None:
    result = analyze_market_intelligence_v3(_snapshot("SELL"))

    assert len(result["model_role_outputs"]) == 7
    for row in result["model_role_outputs"]:
        assert row["side_vote"] in {"BUY", "SELL", "HOLD"}
        assert row["play_vote"]
        assert row["regime_vote"]
        assert isinstance(row["frames_used"], int)
        assert row["evidence"]


def test_reasoning_arbitrator_enter_now_when_coherent() -> None:
    snapshot = _snapshot("SELL")
    result = analyze_reasoning_arbitration_v3(
        snapshot,
        side="SELL",
        market_play={
            "primary_play": "BEARISH_PULLBACK_CONTINUATION",
            "secondary_play": "SUPPLY_REJECTION",
            "side_bias": "SELL",
            "confidence": 0.82,
            "reason": "Sell-high continuation.",
        },
        regime={"primary": "PULLBACK_PHASE", "secondary": "TRENDING_DOWN", "wick_risk": 0.20, "fakeout_risk": 0.12},
        price_location={"relative_location": "LOCAL_HIGH", "buy_quality": "POOR", "sell_quality": "GOOD", "path_room": 0.42},
        memory_confirmation={"memory_vote": "SELL", "confidence_adjustment": 0.06, "similarity": 0.83, "confirmed": True},
        pair_profile={"drawdown_first_frequency": 0.22, "fakeout_frequency": 0.18},
        timing_decision={"entry_now_allowed": True, "timing_mode": "ENTER_NOW", "path_class": "DIRECT_CONTINUATION"},
        market_context={"global_side": "SELL", "opposing_force_distance_ok": True},
    )

    assert result["arbitration"]["state"] == "ENTER_NOW"
    assert result["final_reasoning_decision"]["decision"] == "ENTER_NOW"


def test_outcome_feedback_logs_candidate(tmp_path: Path) -> None:
    target = tmp_path / "outcomes.jsonl"
    record = log_outcome_feedback(
        {
            "candidate_id": "cand_001",
            "play": "BEARISH_PULLBACK_CONTINUATION",
            "decision": "ENTER_NOW",
            "side": "SELL",
            "entry_location": "LOCAL_HIGH",
            "timing_mode": "ENTER_AFTER_REJECTION",
        },
        {
            "result_after_1_candle": "FAVOURABLE",
            "max_adverse_excursion": 0.12,
            "max_favourable_excursion": 0.58,
            "lesson": "Good sell-high continuation.",
        },
        path=target,
        now_epoch=1_764_000_000.0,
    )

    assert record["candidate_id"] == "cand_001"
    assert target.read_text(encoding="utf-8").strip()
