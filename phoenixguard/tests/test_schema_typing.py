from __future__ import annotations

from dataclasses import is_dataclass

from phoenixguard.execution.floating_state_reducer import FloatingStateV2, build_floating_state


def test_s3_floating_state_v2_is_typed_dataclass() -> None:
    assert is_dataclass(FloatingStateV2)

    typed = FloatingStateV2.from_dict(
        {
            "session_id": "pocket-live-8788",
            "mode": "live",
            "timestamp": 1.0,
            "state_chip": "study",
            "packet": {"type": "STUDY"},
        }
    )

    assert typed.session_id == "pocket-live-8788"
    assert typed.as_dict()["schema_version"] == "FloatingStateV2"


def test_s3_compact_floating_state_hides_na_and_null_packet_id() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": None,
            "packet_type": "STUDY_PACKET",
            "side": "n/a",
            "raw_side": "n/a",
            "execution": {"enabled": False, "state": "WATCHING", "side": None},
            "model_council": {"final_state": "WATCHING", "final_side": None},
            "promotion_trace": {"next_required": "stable candidate"},
        },
    )

    compact = {key: value for key, value in state.items() if key != "inspector"}
    rendered = str(compact).lower()
    assert "n/a" not in rendered
    assert "null" not in rendered
    assert state["packet"]["id_short"] == ""


def test_s3_floating_state_reports_instrument_context_next_required() -> None:
    state = build_floating_state(
        session_id="pocket-live-8788",
        signal_payload={
            "packet_id": "study_context_wait",
            "packet_type": "STUDY_PACKET",
            "execution": {"enabled": False, "state": "BLOCKED_BY_RUNTIME", "side": "BUY"},
            "model_council": {
                "final_state": "BLOCKED_BY_RUNTIME",
                "final_side": "BUY",
                "final_execution_score": 0.74,
                "execution_threshold": 0.70,
                "true_blocker": "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE",
            },
            "instrument_context": {
                "instrument_context_state": "USER_PROFILE_LOCKED",
                "broker_click_safe": False,
                "timeframe": "M5",
                "release_condition": "stable viewport + broker surface lock",
            },
        },
    )

    assert state["instrument"]["state"] == "USER_PROFILE_LOCKED"
    assert state["instrument"]["broker_click_safe"] is False
    assert state["instrument"]["next_required"] == "stable viewport + broker surface lock"
