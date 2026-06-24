from __future__ import annotations
from typing import Any

from phoenixguard.execution.enter_now_monitor import extract_enter_now_packages, format_enter_now_notification


def test_extracts_blocked_enter_now_study_packet() -> None:
    now = 1_800.0
    payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "broker_execution_state": {
            "status": "blocked_by_runtime",
            "message": "Live broker clicks disabled by runtime controls.",
        },
        "model_council_study_packet": {
            "packet_id": "study-enter-now",
            "packet_type": "STUDY_PACKET",
            "session_id": "pocket-live-8788",
            "created_epoch_sec": now - 2.0,
            "valid_until_epoch_sec": now + 20.0,
            "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
            "model_council": {"final_execution_score": 0.68, "execution_threshold": 0.70},
            "execution_lane": {"name": "SNIPER_ZONE_ENTRY", "accepted": True},
            "timing_decision": {"timing_mode": "ENTER_NOW", "entry_now_allowed": True},
            "promotion_trace": {"next_required": "publish PG_EXECUTION_PACKET_V3 when executable"},
        },
    }

    packages = extract_enter_now_packages(payload, now_epoch=now)

    assert len(packages) == 1
    package = packages[0]
    assert package.packet_id == "study-enter-now"
    assert package.packet_type == "STUDY_PACKET"
    assert package.side == "BUY"
    assert package.lane == "SNIPER_ZONE_ENTRY"
    assert package.entry_now_allowed is True
    assert package.blocked is True
    assert package.broker_status == "blocked_by_runtime"
    assert "Live broker clicks disabled" in package.blocker
    assert "BLOCKED" in format_enter_now_notification(package)


def test_extracts_runtime_blocked_execution_packet() -> None:
    now = 2_000.0
    payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "broker_execution_state": {"status": "blocked_by_runtime", "message": "Shooter mode LIVE_DISABLED."},
            "model_council_packet": {
                "packet_id": "exec-enter-now",
                "packet_type": "PG_EXECUTION_PACKET_V3",
                "session_id": "pocket-live-8788",
                "created_epoch_sec": now,
                "valid_until_epoch_sec": now + 30.0,
                "execution": {"enabled": True, "state": "EXECUTABLE", "side": "SELL", "expiry_seconds": 600},
                "model_council": {"final_execution_score": 0.82, "execution_threshold": 0.70},
                "execution_lane": {"name": "HIGH_FREQUENCY_TWO_CANDLE", "accepted": True},
                "promotion_trace": {"timing_mode": "ENTER_NOW"},
            },
        },
    }

    packages = extract_enter_now_packages(payload, now_epoch=now)

    assert len(packages) == 1
    assert packages[0].packet_type == "PG_EXECUTION_PACKET_V3"
    assert packages[0].side == "SELL"
    assert packages[0].blocked is True
    assert packages[0].broker_message == "Shooter mode LIVE_DISABLED."


def test_ignores_non_enter_now_packet_and_stale_package_when_requested() -> None:
    now = 3_000.0
    payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "model_council_study_packet": {
            "packet_id": "wait-packet",
            "packet_type": "STUDY_PACKET",
            "created_epoch_sec": now - 1.0,
            "valid_until_epoch_sec": now + 30.0,
            "execution": {"enabled": False, "side": "BUY"},
            "timing_decision": {"timing_mode": "WAIT_FOR_PULLBACK", "entry_now_allowed": False},
        },
        "tracking_summary": {
            "model_council_packet": {
                "packet_id": "expired-enter-now",
                "packet_type": "PG_EXECUTION_PACKET_V3",
                "created_epoch_sec": now - 1_200.0,
                "valid_until_epoch_sec": now - 10.0,
                "execution": {"enabled": True, "side": "BUY"},
                "timing_decision": {"timing_mode": "ENTER_NOW", "entry_now_allowed": True},
            },
        },
    }

    assert extract_enter_now_packages(payload, now_epoch=now, fresh_only=True) == []


def test_deduplicates_same_packet_published_in_multiple_session_fields() -> None:
    now = 4_000.0
    packet: dict[str, Any] = {
        "packet_id": "same-enter-now",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "created_epoch_sec": now,
        "valid_until_epoch_sec": now + 20.0,
        "execution": {"enabled": True, "side": "BUY"},
        "timing_decision": {"timing_mode": "ENTER_NOW", "entry_now_allowed": True},
    }
    payload: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "model_council_packet": packet,
        "latest_signal": {"execution_packet": dict(packet)},
        "tracking_summary": {"model_council_packet": dict(packet)},
    }

    packages = extract_enter_now_packages(payload, now_epoch=now)

    assert [package.packet_id for package in packages] == ["same-enter-now"]
