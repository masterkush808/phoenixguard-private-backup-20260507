from __future__ import annotations

from typing import Any

from phoenixguard.execution.signal_thesis_tracker import (
    SIGNAL_THESIS_SCHEMA_VERSION,
    thesis_blocks_countertrend,
    update_signal_thesis_v3,
)


def _snapshot(side: str, *, symbol: str = "EUR/GBP OTC", y: float = 110.0, frame_id: int = 10) -> dict[str, Any]:
    return {
        "session_id": "pocket-live-8788",
        "symbol": symbol,
        "market": symbol,
        "timeframe": "M5",
        "frame_id": frame_id,
        "capture_count": frame_id + 1,
        "state_version": frame_id + 100,
        "candidate_side": side,
        "confidence": 0.82,
        "current_box": {"bbox": [650, y - 6, 666, y + 6], "direction": side},
        "tracking_summary": {
            "detected_market": symbol,
            "detected_timeframe": "M5",
            "chart_region": {"pixel_bbox": [0, 0, 1000, 500]},
            "tracked_candles": [{"bbox": [650, y - 6, 666, y + 6], "direction": side}],
        },
        "latest_signal": {
            "market": symbol,
            "focus_timeframe": "M5",
            "side": side,
            "execution_action": side,
            "effective_confidence": 0.82,
        },
    }


def _result(side: str, *, score: float = 0.82, state: str = "PREPARING", symbol: str = "EUR/GBP OTC") -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY"
    return {
        "packet_id": f"study-{side.lower()}",
        "symbol": symbol,
        "timeframe": "M5",
        "execution": {"enabled": False, "state": state, "side": side},
        "model_council": {
            "final_state": state,
            "final_side": side,
            "buy_score": score if side == "BUY" else 1.0 - score,
            "sell_score": score if side == "SELL" else 1.0 - score,
            "final_score": score,
            "candidate_id": f"cand-{side.lower()}",
            "selected_lane": "SNIPER_ZONE_ENTRY",
            "arbitration_reason": f"{side} setup is being tracked.",
            "sequence_context": {
                "sequence_id": "seq-test",
                "sequence_status": "COMPLETE",
                "sequence_length": 64,
                "sniper_zones": [{"label": f"{side} entry", "bbox": [610, 98, 700, 122]}],
                "target_zones": [{"label": f"{side} target", "bbox": [760, 52, 850, 68] if side == "BUY" else [760, 152, 850, 168]}],
                "invalidation_zones": [{"label": f"{opposite} invalidation", "bbox": [600, 184, 720, 196] if side == "BUY" else [600, 24, 720, 36]}],
            },
        },
        "promotion_trace": {
            "candidate_side": side,
            "candidate_id": f"cand-{side.lower()}",
            "candidate_stage": "PREPARING",
            "next_required": "trigger confirmation",
        },
    }


def test_signal_thesis_starts_from_mature_council_read() -> None:
    thesis = update_signal_thesis_v3(
        None,
        snapshot=_snapshot("BUY"),
        model_council_result=_result("BUY"),
        now_epoch=100.0,
    )

    assert thesis["schema_version"] == SIGNAL_THESIS_SCHEMA_VERSION
    assert thesis["active"] is True
    assert thesis["side"] == "BUY"
    assert thesis["effective_side"] == "BUY"
    assert thesis["entry_frame_id"] == 10
    assert thesis["countertrend_blocked"] is True
    assert thesis["blocked_countertrend_side"] == "SELL"
    assert thesis["countertrend_policy"] == "BLOCK_OPPOSITE_EXECUTION_UNTIL_INVALIDATION"
    assert thesis_blocks_countertrend(thesis, {"side": "SELL"}) is True
    assert thesis_blocks_countertrend(thesis, {"side": "BUY"}) is False


def test_opposite_read_is_watch_only_until_buy_thesis_invalidates() -> None:
    thesis = update_signal_thesis_v3(
        None,
        snapshot=_snapshot("BUY", y=110.0, frame_id=10),
        model_council_result=_result("BUY"),
        now_epoch=100.0,
    )

    updated = update_signal_thesis_v3(
        thesis,
        snapshot=_snapshot("SELL", y=84.0, frame_id=11),
        model_council_result=_result("SELL", score=0.84),
        now_epoch=101.0,
    )

    assert updated["active"] is True
    assert updated["side"] == "BUY"
    assert updated["effective_side"] == "BUY"
    assert updated["raw_read_side"] == "SELL"
    assert updated["countertrend_blocked"] is True
    assert "watch-only" in updated["plain_language"]
    assert thesis_blocks_countertrend(updated, {"side": "SELL"}) is True
    assert thesis_blocks_countertrend(updated, {"side": "BUY"}) is False


def test_thesis_invalidates_only_after_zone_breach_and_confirmed_opposite() -> None:
    thesis = update_signal_thesis_v3(
        None,
        snapshot=_snapshot("BUY", y=110.0, frame_id=10),
        model_council_result=_result("BUY"),
        now_epoch=100.0,
    )

    invalidated = update_signal_thesis_v3(
        thesis,
        snapshot=_snapshot("SELL", y=224.0, frame_id=12),
        model_council_result=_result("SELL", score=0.86, state="PREPARING"),
        now_epoch=102.0,
    )

    assert invalidated["active"] is False
    assert invalidated["status"] == "INVALIDATED"
    assert invalidated["effective_side"] == "HOLD"
    assert invalidated["invalidated"] is True
    assert "invalidation" in invalidated["invalidation_reason"].lower()


def test_pair_switch_resets_old_thesis_and_can_start_new_pair_thesis() -> None:
    thesis = update_signal_thesis_v3(
        None,
        snapshot=_snapshot("BUY", symbol="EUR/GBP OTC", y=110.0, frame_id=10),
        model_council_result=_result("BUY", symbol="EUR/GBP OTC"),
        now_epoch=100.0,
    )

    reset = update_signal_thesis_v3(
        thesis,
        snapshot=_snapshot("SELL", symbol="AUD/NZD OTC", y=160.0, frame_id=13),
        model_council_result=_result("SELL", score=0.79, symbol="AUD/NZD OTC"),
        now_epoch=103.0,
    )

    assert reset["active"] is False
    assert reset["status"] == "PAIR_SWITCH_RESET"
    assert reset["invalidation_reason"] == "pair switched"
    assert reset["replaced_by"]["active"] is True
    assert reset["replaced_by"]["side"] == "SELL"
    assert reset["replaced_by"]["symbol"] == "AUD/NZD OTC"


def test_generic_locked_chart_thesis_resets_on_strong_opposite_executable_read() -> None:
    buy_result = _result("BUY", symbol="USER_LOCKED_ACTIVE_CHART")
    buy_result["model_council"]["sequence_context"]["invalidation_zones"] = []
    thesis = update_signal_thesis_v3(
        None,
        snapshot=_snapshot("BUY", symbol="USER_LOCKED_ACTIVE_CHART", y=110.0, frame_id=10),
        model_council_result=buy_result,
        now_epoch=100.0,
    )

    sell_result = _result(
        "SELL",
        score=0.9,
        state="EXECUTABLE",
        symbol="USER_LOCKED_ACTIVE_CHART",
    )
    sell_result["execution"] = {"enabled": True, "state": "EXECUTABLE", "side": "SELL"}
    sell_result["model_council"]["sequence_context"]["invalidation_zones"] = []
    updated = update_signal_thesis_v3(
        thesis,
        snapshot=_snapshot("SELL", symbol="USER_LOCKED_ACTIVE_CHART", y=240.0, frame_id=14),
        model_council_result=sell_result,
        execution_packet={"packet_id": "exec-sell", "side": "SELL", "symbol": "USER_LOCKED_ACTIVE_CHART"},
        now_epoch=104.0,
    )

    assert updated["active"] is False
    assert updated["status"] == "INVALIDATED"
    assert updated["countertrend_blocked"] is False
    assert updated["pair_switch_suspected"] is True
    assert "generic" in updated["invalidation_reason"].lower()
    assert updated["replaced_by"]["active"] is True
    assert updated["replaced_by"]["side"] == "SELL"
    assert updated["replaced_by"]["countertrend_blocked"] is True
    assert updated["replaced_by"]["blocked_countertrend_side"] == "BUY"
    assert thesis_blocks_countertrend(updated, {"side": "SELL"}) is False


def test_confirmed_book_reclaim_releases_old_opposite_thesis() -> None:
    thesis = update_signal_thesis_v3(
        None,
        snapshot=_snapshot("SELL", y=110.0, frame_id=10),
        model_council_result=_result("SELL"),
        now_epoch=100.0,
    )

    buy_result = _result("BUY", score=0.86, state="PREPARING")
    buy_result["book_strategy"] = {
        "playbook": "FAILED_SUPPLY_RECLAIM_BUY_CONTINUATION",
        "maturity_state": "ENTER_NOW",
        "evidence": {
            "bias_alignment": "REVERSAL_OVERRIDE",
            "countertrend_reversal_override": True,
        },
    }

    updated = update_signal_thesis_v3(
        thesis,
        snapshot=_snapshot("BUY", y=80.0, frame_id=15),
        model_council_result=buy_result,
        now_epoch=105.0,
    )

    assert updated["active"] is False
    assert updated["status"] == "INVALIDATED"
    assert updated["countertrend_blocked"] is False
    assert updated["replaced_by"]["active"] is True
    assert updated["replaced_by"]["side"] == "BUY"
    assert thesis_blocks_countertrend(updated, {"side": "BUY"}) is False


def test_book_armed_failed_continuation_reversal_releases_old_thesis() -> None:
    thesis = update_signal_thesis_v3(
        None,
        snapshot=_snapshot("SELL", y=110.0, frame_id=10),
        model_council_result=_result("SELL"),
        now_epoch=100.0,
    )

    buy_result = _result("BUY", score=0.58, state="PREPARING")
    buy_result["book_strategy"] = {
        "playbook": "FAILED_SELL_INTO_DEMAND_BUY_REVERSAL",
        "maturity_state": "PREPARE",
        "evidence": {
            "failed_continuation_reversal": True,
            "countertrend_reversal_override": True,
            "structural_extreme_for_side": True,
        },
    }

    updated = update_signal_thesis_v3(
        thesis,
        snapshot=_snapshot("BUY", y=96.0, frame_id=16),
        model_council_result=buy_result,
        now_epoch=106.0,
    )

    assert updated["active"] is False
    assert updated["status"] == "INVALIDATED"
    assert updated["countertrend_blocked"] is False
    assert "structural opposite reversal" in updated["invalidation_reason"]
    assert updated["replaced_by"]["active"] is True
    assert updated["replaced_by"]["side"] == "BUY"
    assert thesis_blocks_countertrend(updated, {"side": "BUY"}) is False
