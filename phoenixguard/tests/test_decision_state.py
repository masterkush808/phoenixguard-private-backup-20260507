from __future__ import annotations
from typing import Any

from phoenixguard.core.decision_state import (
    TradeIntent,
    build_trade_intent,
    derive_state_version,
    derive_valid_until_epoch,
)


def test_derive_state_version_prefers_latest_capture_metadata() -> None:
    version = derive_state_version(capture_count=4, frame_index=9, published_epoch=1710000.123)

    assert version == 1710000123


def test_derive_valid_until_epoch_uses_shorter_expiry_window() -> None:
    valid_until = derive_valid_until_epoch(published_epoch=100.0, freshness_window_sec=8.0, expiry_seconds=30)

    assert valid_until == 108.0


def test_build_trade_intent_returns_frozen_actionable_intent() -> None:
    latest_signal: dict[str, Any] = {
        "signal_id": "tracker_abc_123",
        "action": "BUY",
        "published_epoch": 100.0,
        "freshness_window_sec": 8.0,
        "expiry_seconds": 30,
        "freshness_score": 0.95,
        "summary": "BUY setup remains valid near support.",
        "status": "tracking",
    }
    session_payload: dict[str, Any] = {
        "capture_count": 3,
        "frame_index": 7,
        "capture_interval_sec": 2.0,
    }

    intent = build_trade_intent(latest_signal, session_payload=session_payload)

    assert isinstance(intent, TradeIntent)
    assert intent.signal_id == "tracker_abc_123"
    assert intent.side == "BUY"
    assert intent.state_version == 100000
    assert intent.valid_until_epoch == 108.0
    assert intent.to_dict()["source"] == "tracker"


def test_build_trade_intent_ignores_hold_signals() -> None:
    intent = build_trade_intent({"signal_id": "abc", "action": "HOLD"})

    assert intent is None