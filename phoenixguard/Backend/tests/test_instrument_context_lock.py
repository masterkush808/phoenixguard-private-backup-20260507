from __future__ import annotations

from typing import Any

from phoenixguard.runtime.instrument_context import (
    CTX_BROKER_CLICK_SAFE,
    CTX_INVALIDATED,
    IDENTITY_CONTINUITY_LOCKED,
    IDENTITY_UNKNOWN_NOT_EXECUTABLE,
    IDENTITY_USER_LOCKED,
    build_instrument_context,
    validate_instrument_context,
)


def _base_snapshot(**overrides: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "viewport_hash": "viewport-a",
        "broker_surface_hash": "broker-a",
        "ocr_symbol": "",
    }
    snapshot.update(overrides)
    return snapshot


def test_blank_symbol_user_lock_is_paper_safe_not_broker_click_safe() -> None:
    context = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={"user_symbol": "EUR/GBP OTC"},
        )
    )

    assert context["identity_state"] == IDENTITY_USER_LOCKED
    assert context["display_symbol"] == "EUR/GBP OTC"
    assert context["ocr_symbol"] == ""
    assert context["paper_safe"] is True
    assert context["broker_click_safe"] is False
    assert validate_instrument_context(context, mode="paper").ok is True
    assert validate_instrument_context(context, mode="broker_click").first_reason == "INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE"


def test_deliberate_locked_surface_profile_can_be_broker_click_safe() -> None:
    context = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={
                "user_symbol": "USER_LOCKED_ACTIVE_CHART",
                "session_id": "pocket-live-8788",
                "timeframe": "M5",
                "viewport_hash": "viewport-a",
                "broker_surface_hash": "broker-a",
                "window_handle": "hwnd-1",
                "window_rect": [0, 0, 1280, 720],
                "calibration_layout_id": "layout-a",
                "broker_click_safe": True,
                "window_handle_stable": True,
                "window_rect_stable": True,
                "viewport_hash_stable": True,
                "broker_surface_hash_stable": True,
                "calibration_layout_match": True,
                "session_active": True,
                "packet_fresh": True,
                "models_awake": True,
                "profile_mismatch": False,
            },
        )
    )

    assert context["identity_state"] == IDENTITY_USER_LOCKED
    assert context["display_symbol"] == "USER_LOCKED_ACTIVE_CHART"
    assert context["paper_safe"] is True
    assert context["broker_click_safe"] is True
    assert context["instrument_context_state"] == CTX_BROKER_CLICK_SAFE
    assert context["evidence"]["calibration_layout_match"] is True
    assert context["release_condition"] == "none"
    assert validate_instrument_context(context, mode="broker_click").ok is True


def test_continuity_lock_survives_same_session_timeframe_and_viewport() -> None:
    previous = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={"user_symbol": "EUR/GBP OTC"},
        )
    )

    context = build_instrument_context(_base_snapshot(), previous_context=previous)

    assert context["identity_state"] == IDENTITY_CONTINUITY_LOCKED
    assert context["display_symbol"] == "EUR/GBP OTC"
    assert context["paper_safe"] is True


def test_viewport_hash_change_invalidates_identity() -> None:
    previous = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={"user_symbol": "EUR/GBP OTC"},
        )
    )

    context = build_instrument_context(_base_snapshot(viewport_hash="viewport-b"), previous_context=previous)

    assert context["identity_state"] == IDENTITY_UNKNOWN_NOT_EXECUTABLE
    assert context["instrument_context_state"] == CTX_INVALIDATED
    assert context["invalidated"] is True
    assert context["invalidation_reason"] == "VIEWPORT_HASH_CHANGED"
    assert context["paper_safe"] is False
    assert validate_instrument_context(context, mode="paper").first_reason == "INSTRUMENT_CONTEXT_INVALIDATED"


def test_session_change_invalidates_identity() -> None:
    previous = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={"user_symbol": "EUR/GBP OTC"},
        )
    )

    context = build_instrument_context(_base_snapshot(session_id="new-session"), previous_context=previous)

    assert context["identity_state"] == IDENTITY_UNKNOWN_NOT_EXECUTABLE
    assert context["instrument_context_state"] == CTX_INVALIDATED
    assert context["invalidated"] is True
    assert context["invalidation_reason"] == "SESSION_CHANGED"
    assert context["paper_safe"] is False


def test_timeframe_change_invalidates_identity() -> None:
    previous = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={"user_symbol": "EUR/GBP OTC"},
        )
    )

    context = build_instrument_context(_base_snapshot(timeframe="M1"), previous_context=previous)

    assert context["identity_state"] == IDENTITY_UNKNOWN_NOT_EXECUTABLE
    assert context["instrument_context_state"] == CTX_INVALIDATED
    assert context["invalidated"] is True
    assert context["invalidation_reason"] == "TIMEFRAME_CHANGED"


def test_blank_ocr_symbol_does_not_block_when_user_profile_locked() -> None:
    context = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={
                "user_symbol": "USER_LOCKED_ACTIVE_CHART",
                "session_id": "pocket-live-8788",
                "timeframe": "M5",
                "viewport_hash": "viewport-a",
                "broker_surface_hash": "broker-a",
                "window_handle": "hwnd-1",
                "window_rect": [0, 0, 1280, 720],
                "calibration_layout_id": "layout-a",
                "window_handle_stable": True,
                "window_rect_stable": True,
                "viewport_hash_stable": True,
                "broker_surface_hash_stable": True,
                "calibration_layout_match": True,
                "session_active": True,
                "packet_fresh": True,
                "models_awake": True,
                "profile_mismatch": False,
            },
        )
    )

    assert context["ocr_symbol"] == ""
    assert context["display_symbol"] == "USER_LOCKED_ACTIVE_CHART"
    assert context["broker_click_safe"] is True
    assert context["instrument_context_state"] == CTX_BROKER_CLICK_SAFE
    assert context["evidence"]["ocr_symbol_uncertain"] is True
    assert context["evidence"]["symbol_uncertainty"] == "OCR_SYMBOL_MISSING_USER_PROFILE_LOCKED"
    assert validate_instrument_context(context, mode="broker_click").ok is True


def test_broker_click_safe_requires_stable_window_handle() -> None:
    context = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={
                "user_symbol": "USER_LOCKED_ACTIVE_CHART",
                "session_id": "pocket-live-8788",
                "timeframe": "M5",
                "viewport_hash": "viewport-a",
                "broker_surface_hash": "broker-a",
                "window_rect_stable": True,
                "viewport_hash_stable": True,
                "broker_surface_hash_stable": True,
                "calibration_layout_match": True,
                "session_active": True,
                "packet_fresh": True,
                "models_awake": True,
                "profile_mismatch": False,
            },
        )
    )

    assert context["broker_click_safe"] is False
    assert context["evidence"]["window_handle_stable"] is False
    assert "window_handle_stable" in context["release_condition"]


def test_broker_click_safe_requires_calibration_layout_match() -> None:
    context = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={
                "user_symbol": "USER_LOCKED_ACTIVE_CHART",
                "session_id": "pocket-live-8788",
                "timeframe": "M5",
                "viewport_hash": "viewport-a",
                "broker_surface_hash": "broker-a",
                "window_handle": "hwnd-1",
                "window_rect": [0, 0, 1280, 720],
                "calibration_layout_id": "layout-a",
                "window_handle_stable": True,
                "window_rect_stable": True,
                "viewport_hash_stable": True,
                "broker_surface_hash_stable": True,
                "calibration_layout_match": False,
                "session_active": True,
                "packet_fresh": True,
                "models_awake": True,
                "profile_mismatch": False,
            },
        )
    )

    assert context["broker_click_safe"] is False
    assert context["evidence"]["calibration_layout_match"] is False
    assert "calibration_layout_match" in context["release_condition"]


def test_viewport_hash_change_invalidates_context() -> None:
    previous = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={"user_symbol": "EUR/GBP OTC"},
        )
    )

    context = build_instrument_context(_base_snapshot(viewport_hash="viewport-b"), previous_context=previous)

    assert context["identity_state"] == IDENTITY_UNKNOWN_NOT_EXECUTABLE
    assert context["instrument_context_state"] == CTX_INVALIDATED
    assert context["invalidated"] is True
    assert context["invalidation_reason"] == "VIEWPORT_HASH_CHANGED"
    assert validate_instrument_context(context, mode="broker_click").first_reason == "INSTRUMENT_CONTEXT_INVALIDATED"


def test_timeframe_change_invalidates_context() -> None:
    previous = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={"user_symbol": "EUR/GBP OTC"},
        )
    )

    context = build_instrument_context(_base_snapshot(timeframe="M1"), previous_context=previous)

    assert context["identity_state"] == IDENTITY_UNKNOWN_NOT_EXECUTABLE
    assert context["instrument_context_state"] == CTX_INVALIDATED
    assert context["invalidated"] is True
    assert context["invalidation_reason"] == "TIMEFRAME_CHANGED"


def test_window_rect_shift_invalidates_context() -> None:
    previous = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={
                "user_symbol": "EUR/GBP OTC",
                "window_handle": 123,
                "window_rect": [0, 0, 1920, 1080],
                "viewport_hash": "viewport-a",
                "broker_surface_hash": "broker-a",
            },
            window_handle=123,
            window_rect=[0, 0, 1920, 1080],
        )
    )

    context = build_instrument_context(
        _base_snapshot(window_handle=123, window_rect=[50, 0, 1970, 1080]),
        previous_context=previous,
    )

    assert context["identity_state"] == IDENTITY_UNKNOWN_NOT_EXECUTABLE
    assert context["instrument_context_state"] == CTX_INVALIDATED
    assert context["invalidated"] is True
    assert context["invalidation_reason"] == "WINDOW_RECT_CHANGED"
    assert context["evidence"]["window_rect_stable"] is False
    assert context["reason"]
    assert validate_instrument_context(context, mode="broker_click").first_reason == "INSTRUMENT_CONTEXT_INVALIDATED"


def test_instrument_context_reason_is_never_empty() -> None:
    safe_context = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={
                "user_symbol": "USER_LOCKED_ACTIVE_CHART",
                "session_id": "pocket-live-8788",
                "timeframe": "M5",
                "viewport_hash": "viewport-a",
                "broker_surface_hash": "broker-a",
                "window_handle": "hwnd-1",
                "window_rect": [0, 0, 1280, 720],
                "calibration_layout_id": "layout-a",
                "window_handle_stable": True,
                "window_rect_stable": True,
                "viewport_hash_stable": True,
                "broker_surface_hash_stable": True,
                "calibration_layout_match": True,
                "session_active": True,
                "packet_fresh": True,
                "models_awake": True,
                "profile_mismatch": False,
            },
        )
    )
    unsafe_context = build_instrument_context(_base_snapshot())

    assert safe_context["reason"]
    assert unsafe_context["reason"]
    assert validate_instrument_context(safe_context, mode="broker_click").first_reason
    assert validate_instrument_context(unsafe_context, mode="broker_click").first_reason


def test_instrument_context_emits_evidence_fields() -> None:
    context = build_instrument_context(
        _base_snapshot(
            instrument_identity_lock={
                "user_symbol": "USER_LOCKED_ACTIVE_CHART",
                "session_id": "pocket-live-8788",
                "timeframe": "M5",
                "viewport_hash": "viewport-a",
                "broker_surface_hash": "broker-a",
                "window_handle_stable": True,
                "window_rect_stable": True,
                "viewport_hash_stable": True,
                "broker_surface_hash_stable": True,
                "calibration_layout_match": True,
                "session_active": True,
                "packet_fresh": True,
                "models_awake": True,
                "profile_mismatch": False,
            },
        )
    )

    evidence = context["evidence"]
    for field in (
        "window_handle_stable",
        "window_rect_stable",
        "window_rect_tolerance_px",
        "viewport_hash_stable",
        "broker_surface_hash_stable",
        "calibration_layout_match",
        "timeframe_known",
        "session_active",
        "packet_fresh",
        "models_awake",
        "user_profile_locked",
        "profile_mismatch",
        "visual_continuity_frames",
        "ocr_symbol_present",
        "ocr_symbol_uncertain",
        "symbol_uncertainty",
        "window_handle",
        "window_rect",
        "viewport_hash",
        "broker_surface_hash",
        "calibration_layout_id",
    ):
        assert field in evidence
