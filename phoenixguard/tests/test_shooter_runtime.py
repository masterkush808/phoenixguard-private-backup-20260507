import ctypes
import importlib.util
from pathlib import Path
from typing import Any

import pytest

if not hasattr(ctypes, "windll"):
    pytest.skip("shooter.py runtime is Windows-only", allow_module_level=True)


def _load_shooter():
    module_path = Path(__file__).resolve().parents[1] / "shooter.py"
    spec = importlib.util.spec_from_file_location("_shooter_runtime_test", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extract_signal_payload_unwraps_latest_signal() -> None:
    shooter = _load_shooter()

    signal = {"action": "BUY", "actionable": True}
    assert shooter._extract_signal_payload({"latest_signal": signal}) == signal


def test_ocr_read_time_region_uses_visual_template_without_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    Image = pytest.importorskip("PIL.Image")
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")

    image_array = np.zeros((40, 156, 3), dtype=np.uint8)
    image_array[:] = (32, 38, 58)
    cv2.putText(
        image_array,
        "00:10:00",
        (12, 27),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    image = Image.fromarray(image_array, "RGB")

    def _get_window_rect(_hwnd: int) -> Any:
        return shooter.RECT(0, 0, 200, 100)

    def _screenshot(region: object | None = None) -> Any:
        return image

    monkeypatch.setattr(shooter, "has_ocr", False)
    monkeypatch.setattr(shooter, "pytesseract", None)
    monkeypatch.setattr(shooter, "get_window_rect", _get_window_rect)
    monkeypatch.setattr(shooter.pyautogui, "screenshot", _screenshot)

    assert shooter.ocr_read_time_region(1, {"time_input": {"x": 0.5, "y": 0.5}}) == 600


def test_confirmed_expiry_cache_is_target_and_window_scoped() -> None:
    shooter = _load_shooter()

    with shooter._confirmed_expiry_cache_lock:
        shooter._confirmed_expiry_cache.clear()
    rect = shooter.RECT(0, 0, 1000, 800)
    shifted_rect = shooter.RECT(200, 0, 1200, 800)

    shooter._remember_confirmed_expiry(11, rect, 600, source="test")

    assert shooter._get_cached_confirmed_expiry(11, rect, 600) == 600
    assert shooter._get_cached_confirmed_expiry(11, rect, 900) is None
    assert shooter._get_cached_confirmed_expiry(12, rect, 600) is None
    assert shooter._get_cached_confirmed_expiry(11, shifted_rect, 600) is None


def test_parse_trade_signal_accepts_explicit_execution_action_payload() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "explicit-sell-1",
            "action": "SELL",
            "execution_action": "SELL",
            "actionable": "true",
            "expiry_seconds": "00:10:00",
            "focus_timeframe": "M1",
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.68,
            },
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is not None
    assert parsed[:3] == ("SELL", 600, "explicit-sell-1")
    assert parsed[4] == "expiry_seconds"
    assert parsed[6] == "execution_action"


def test_parse_trade_signal_accepts_ready_pullback_reload_payload() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "pullback-ready-1",
            "action": "SELL",
            "execution_action": "SELL",
            "entry_state": "SNIPER_READY",
            "actionable": True,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "decision_kernel": {
                "trade_mode": "PULLBACK_WAIT",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.62,
            },
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is not None
    assert parsed[:3] == ("SELL", 3000, "pullback-ready-1")


def test_parse_trade_signal_accepts_location_sniper_payload() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "location-sniper-1",
            "action": "SELL",
            "execution_action": "SELL",
            "execution_lane": "LOCATION_SNIPER",
            "actionable": True,
            "expiry_seconds": 240,
            "focus_timeframe": "M5",
            "execution_timing": {
                "entry_allowed": True,
                "significant_entry_context": True,
                "entry_area_score": 0.74,
                "history_area_sample_size": 8,
            },
            "decision_kernel": {
                "trade_mode": "STAND_ASIDE",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "target_horizon_candles": 4,
                "p_target_before_invalidation": 0.62,
            },
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is not None
    assert parsed[:3] == ("SELL", 240, "location-sniper-1")


def test_parse_trade_signal_accepts_opposing_force_reaction_lane() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "opposing-force-buy-1",
            "action": "BUY",
            "execution_action": "BUY",
            "actionable": True,
            "execution_lane": "OPPOSING_FORCE_REACTION",
            "expiry_seconds": 300,
            "focus_timeframe": "M5",
            "execution_timing": {
                "lane": "OPPOSING_FORCE_REACTION",
                "entry_allowed": True,
                "opposing_force_reaction_ready": True,
                "recommended_expiry_seconds": 300,
            },
            "decision_kernel": {
                "trade_mode": "PULLBACK_WAIT",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.58,
            },
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is not None
    assert parsed[:3] == ("BUY", 300, "opposing-force-buy-1")


def test_preferred_source_rejects_shadow_controls_even_when_source_matches() -> None:
    shooter = _load_shooter()

    shadow_payload = {
        "source": "tracker",
        "execution_controls": {
            "live_execution_enabled": False,
            "execution_mode": "shadow",
        },
    }
    live_payload = {
        "source": "tracker",
        "execution_controls": {
            "live_execution_enabled": True,
            "execution_mode": "live",
        },
    }

    assert shooter._payload_from_preferred_source(shadow_payload, "tracker") is False
    assert shooter._payload_from_preferred_source(live_payload, "tracker") is True


def test_parse_trade_signal_respects_tracker_broker_wait_state() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "wait-state-sell-1",
            "action": "SELL",
            "execution_action": "SELL",
            "actionable": True,
            "expiry_seconds": 300,
            "focus_timeframe": "M5",
            "broker_execution_state": {
                "status": "watching",
                "side": "HOLD",
                "lane": "OPPOSING_FORCE_REACTION_WAIT",
                "actionable": False,
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.62,
            },
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is None


def test_parse_visible_time_seconds_reads_broker_clock_text() -> None:
    shooter = _load_shooter()

    assert shooter._parse_visible_time_seconds("00:05:00") == 300
    assert shooter._parse_visible_time_seconds("0O:15:3O") == 930


def test_v3_gate3_blocks_bad_entry_location_for_side() -> None:
    shooter = _load_shooter()

    packet = {
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": "SELL",
            "expiry_seconds": 300,
            "time_sequence": {
                "target_seconds": 300,
                "target_text": "00:05:00",
                "steps": [
                    {"action": "focus_time_field", "field": "time"},
                    {"action": "set_time", "field": "time", "value": "00:05:00"},
                    {"action": "confirm_time", "field": "time"},
                ],
            },
            "tracking_summary": {
                "behavior": {
                    "candle_tokens": [
                        {"close_position": 0.08, "micro_structure_event": "continuation_up"},
                    ]
                }
            },
        },
        "model_council": {
            "final_side": "SELL",
            "final_state": "EXECUTABLE",
            "sequence_context": {
                "sequence_id": "seq-808-shooter-1",
                "session_id": "session-808",
                "sequence_index": 4,
                "frame_start": 1,
                "frame_end": 64,
                "sequence_length": 64,
                "frames_received": 64,
                "frames_used": 64,
                "candle_count": 64,
                "timeframe": "M5",
                "sequence_signature": "seqsig-808-complete",
                "sequence_confidence": 0.98,
                "global_direction": "SELL",
                "local_direction": "SELL",
                "current_phase": "ENTRY",
                "progression_score": 0.92,
                "progression": [{"stage": "impulse", "direction": "SELL"}],
                "motifs": ["impulse", "breakout"],
                "box_history": [{"label": "H1 SELL", "bbox": [4, 4, 12, 12]}],
                "angle_vectors": [[-1.0, 0.0]],
                "sniper_zones": [{"label": "sniper", "bbox": [6, 6, 10, 10]}],
                "target_zones": [{"label": "target", "bbox": [14, 4, 20, 12]}],
                "invalidation_zones": [{"label": "invalidation", "bbox": [1, 14, 5, 18]}],
                "sequence_status": "COMPLETE",
                "frame_range": [1, 64],
                "candle_range": [1, 64],
                "frames_dropped": 0,
                "sequence_age_ms": 40,
                "packet_age_ms": 90,
                "decision_age_ms": 70,
                "model_vote_age_ms": 50,
                "entry_progression": {"progression_stage": "progression", "maturity_score": 0.9},
                "tracking_summary": {"global_direction": "SELL", "local_direction": "SELL"},
                "sequence_history": [{"label": "H1 SELL", "bbox": [4, 4, 12, 12]}],
            },
        },
        "runtime_model_health": {"all_required_models_awake": True},
    }

    ok, reason = shooter._v3_gate3_model_council(packet)

    assert ok is False
    assert str(reason).startswith("MODEL_COUNCIL_ENTRY_LOCATION_BLOCKED:")


def test_v3_gate3_rejects_missing_sequence_context() -> None:
    shooter = _load_shooter()

    packet = {
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": "SELL",
            "expiry_seconds": 300,
            "time_sequence": {
                "target_seconds": 300,
                "target_text": "00:05:00",
                "steps": [
                    {"action": "focus_time_field", "field": "time"},
                    {"action": "set_time", "field": "time", "value": "00:05:00"},
                    {"action": "confirm_time", "field": "time"},
                ],
            },
            "tracking_summary": {"behavior": {"candle_tokens": [{"close_position": 0.08}] }},
        },
        "model_council": {
            "final_side": "SELL",
            "final_state": "EXECUTABLE",
        },
        "runtime_model_health": {"all_required_models_awake": True},
    }

    ok, reason = shooter._v3_gate3_model_council(packet)

    assert ok is False
    assert reason == "MODEL_COUNCIL_SEQUENCE_CONTEXT_MISSING"


def test_v3_gate3_rejects_partial_sequence_context() -> None:
    shooter = _load_shooter()

    packet = {
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": "SELL",
            "expiry_seconds": 300,
            "time_sequence": {
                "target_seconds": 300,
                "target_text": "00:05:00",
                "steps": [
                    {"action": "focus_time_field", "field": "time"},
                    {"action": "set_time", "field": "time", "value": "00:05:00"},
                    {"action": "confirm_time", "field": "time"},
                ],
            },
            "tracking_summary": {"behavior": {"candle_tokens": [{"close_position": 0.08}] }},
        },
        "model_council": {
            "final_side": "SELL",
            "final_state": "EXECUTABLE",
            "sequence_context": {
                "sequence_id": "seq-808-shooter-2",
                "session_id": "session-808",
                "sequence_index": 4,
                "frame_start": 1,
                "frame_end": 12,
                "sequence_length": 12,
                "frames_received": 12,
                "frames_used": 12,
                "candle_count": 12,
                "timeframe": "M5",
                "sequence_signature": "seqsig-808-partial",
                "sequence_confidence": 0.44,
                "global_direction": "SELL",
                "local_direction": "SELL",
                "current_phase": "ENTRY",
                "progression_score": 0.31,
                "progression": [],
                "motifs": [],
                "box_history": [{"label": "H1 SELL", "bbox": [4, 4, 12, 12]}],
                "angle_vectors": [],
                "sniper_zones": [],
                "target_zones": [],
                "invalidation_zones": [],
                "sequence_status": "PARTIAL_SEQUENCE",
                "frame_range": [1, 12],
                "candle_range": [1, 12],
                "frames_dropped": 0,
                "sequence_age_ms": 40,
                "packet_age_ms": 90,
                "decision_age_ms": 70,
                "model_vote_age_ms": 50,
                "entry_progression": {"progression_stage": "progression"},
                "tracking_summary": {"global_direction": "SELL", "local_direction": "SELL"},
                "sequence_history": [],
            },
        },
        "runtime_model_health": {"all_required_models_awake": True},
    }

    ok, reason = shooter._v3_gate3_model_council(packet)

    assert ok is False
    assert str(reason).startswith("MODEL_COUNCIL_PARTIAL_SEQUENCE_NOT_EXECUTABLE:")


def test_parse_trade_signal_rejects_pullback_watch_payload() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "pullback-watch-1",
            "action": "SELL",
            "execution_action": "SELL",
            "entry_state": "SNIPER_WATCH",
            "actionable": True,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "decision_kernel": {
                "trade_mode": "PULLBACK_WAIT",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.62,
            },
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_action_only_payload() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "advisory-sell-1",
            "action": "SELL",
            "actionable": "true",
            "expiry_seconds": "00:02:00",
        }
    )

    assert parsed is None


def test_parse_trade_signal_ignores_non_executable_signal_without_expiry() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "forming-sell-1",
            "action": "SELL",
            "execution_action": "HOLD",
            "actionable": False,
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "STAND_ASIDE",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "target_horizon_candles": 10,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_timestamp_identity_fallback() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "execution_action": "BUY",
            "actionable": True,
            "expiry_seconds": 120,
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_non_swing_kernel_payload() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "scalp-buy-1",
            "execution_action": "BUY",
            "actionable": True,
            "expiry_seconds": 120,
            "focus_timeframe": "M1",
            "decision_kernel": {
                "trade_mode": "COUNTERTREND_SCALP",
                "major_trend_side": "buy",
                "dominant_side": "buy",
                "target_horizon_candles": 2,
                "p_target_before_invalidation": 0.70,
            },
        }
    )

    assert parsed is None


def test_safety_lockout_is_20_minutes() -> None:
    shooter = _load_shooter()

    assert shooter.SAFETY_LOCKOUT_SECONDS == 20 * 60


def test_parse_trade_signal_accepts_profile_expiry_when_present() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "short-expiry-buy-1",
            "execution_action": "BUY",
            "actionable": True,
            "expiry_seconds": 240,
            "execution_timing": {"entry_allowed": True, "recommended_expiry_seconds": 240},
            "focus_timeframe": "M1",
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "major_trend_side": "buy",
                "dominant_side": "buy",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.70,
            },
        }
    )

    assert parsed is not None
    assert parsed[:3] == ("BUY", 240, "short-expiry-buy-1")


def test_setup_hotkey_listener_uses_native_fallback_without_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    started = {"native": False}

    monkeypatch.setattr(shooter, "has_keyboard", False)
    monkeypatch.setattr(shooter, "keyboard", None)

    def fake_native_listener() -> bool:
        started["native"] = True
        return True

    monkeypatch.setattr(shooter, "_start_native_ctrl_b_listener", fake_native_listener)

    assert shooter.setup_hotkey_listener() is True
    assert started["native"] is True


def test_prepare_pocket_option_window_auto_opens_before_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    find_calls: list[tuple[bool, bool]] = []
    opened: list[str] = []

    def fake_find(
        window_query: str | None,
        *,
        allow_active_fallback: bool = True,
        quiet: bool = False,
    ) -> int | None:
        assert window_query == "Pocket Option"
        find_calls.append((allow_active_fallback, quiet))
        return 555 if len(find_calls) >= 2 else None

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(shooter, "find_pocket_option_window", fake_find)
    monkeypatch.setattr(shooter, "open_broker_window", fake_open)

    hwnd = shooter.prepare_pocket_option_window(
        "Pocket Option",
        auto_open=True,
        broker_url="https://example.test/pocket",
        open_timeout=1.0,
        allow_active_fallback=False,
    )

    assert hwnd == 555
    assert opened == ["https://example.test/pocket"]
    assert find_calls[0] == (False, True)
    assert find_calls[1] == (False, True)


def test_explicit_window_query_miss_does_not_use_active_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()

    def _list_visible_windows(query: str | None = None) -> list[tuple[int, str, str]]:
        return []

    monkeypatch.setattr(shooter, "list_visible_windows", _list_visible_windows)

    class FakeUser32:
        def GetForegroundWindow(self) -> int:
            raise AssertionError("explicit query miss must not inspect active foreground window")

    monkeypatch.setattr(shooter, "USER32", FakeUser32())

    hwnd = shooter.find_pocket_option_window("Pocket Option", allow_active_fallback=True, quiet=True)

    assert hwnd is None


def test_activate_window_preserves_non_minimized_browser_state(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    calls: list[tuple[str, int, int | None]] = []

    class FakeUser32:
        def IsIconic(self, hwnd: int) -> int:
            calls.append(("is_iconic", hwnd, None))
            return 0

        def ShowWindow(self, hwnd: int, mode: int) -> int:
            calls.append(("show", hwnd, mode))
            return 1

        def SetForegroundWindow(self, hwnd: int) -> int:
            calls.append(("foreground", hwnd, None))
            return 1

        def SetFocus(self, hwnd: int) -> int:
            calls.append(("focus", hwnd, None))
            return 1

    def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(shooter, "USER32", FakeUser32())
    monkeypatch.setattr(shooter.time, "sleep", fake_sleep)

    assert shooter.activate_window(777) is True
    assert ("show", 777, 9) not in calls
    assert ("foreground", 777, None) in calls


def test_extract_signal_payload_merges_tracker_timer_context() -> None:
    shooter = _load_shooter()

    extracted = shooter._extract_signal_payload(
        {
            "session_id": "pocket-live",
            "next_capture_in_sec": 8.5,
            "effective_capture_interval_sec": 10.0,
            "tracking_summary": {"global_direction": "BUY", "detected_timeframe": "M1"},
            "latest_signal": {"action": "HOLD", "candidate_action": "BUY", "actionable": False},
        }
    )

    assert extracted is not None
    assert extracted["next_capture_in_sec"] == 8.5
    assert extracted["major_bias"] == "BUY"
    assert extracted["focus_timeframe"] == "M1"


def test_resolve_next_study_seconds_keeps_fast_tracker_cadence() -> None:
    shooter = _load_shooter()

    assert shooter._resolve_next_study_seconds({"next_capture_in_sec": 0.5}) == 0.5
    assert shooter._resolve_next_study_seconds({"effective_capture_interval_sec": 3.0}) == 3.0
    assert shooter._resolve_next_study_seconds({}) == 3.0


def test_payload_age_uses_phoenix_publish_time_not_capture_start(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    monkeypatch.setattr(shooter.time, "time", lambda: 110.0)

    age = shooter._payload_age_seconds(
        {
            "capture_started_epoch": 90.0,
            "published_epoch": 107.0,
        }
    )

    assert age == 3.0


def test_payload_age_prefers_explicit_signal_age() -> None:
    shooter = _load_shooter()

    assert shooter._payload_age_seconds({"signal_age_sec": "0.42", "published_epoch": 1.0}) == 0.42


def test_signal_mode_defaults_to_fast_publish_age_window() -> None:
    shooter = _load_shooter()

    args = shooter.build_parser().parse_args(["signal", "--session-id", "pocket-live"])

    assert args.poll == 0.05
    assert args.max_signal_age == 8.0
    assert args.cooldown == 20 * 60
    assert args.auto_open_broker is False


def test_signal_mode_broker_auto_open_is_explicit_opt_in() -> None:
    shooter = _load_shooter()

    args = shooter.build_parser().parse_args(["signal", "--session-id", "pocket-live", "--auto-open"])

    assert args.auto_open_broker is True


def test_payload_freshness_rejects_expired_decision_window(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    monkeypatch.setattr(shooter.time, "time", lambda: 200.0)

    assert shooter._payload_is_fresh(
        {
            "status": "tracking",
            "signal_age_sec": 1.0,
            "state_version": 10,
            "decision_version": 10,
            "decision_valid_until_epoch": 199.0,
        },
        8.0,
    ) is False


def test_signal_context_copies_tracker_freshness_fields() -> None:
    shooter = _load_shooter()

    merged = shooter._signal_with_tracker_context(
        {"signal_id": "sig-1", "action": "BUY"},
        {
            "state_version": 11,
            "decision_version": 11,
            "decision_valid_until_epoch": 1234.5,
            "last_capture_epoch": 1230.0,
            "latest_signal": {"signal_id": "sig-1", "action": "BUY"},
        },
    )

    assert merged["decision_valid_until_epoch"] == 1234.5
    assert merged["state_version"] == 11
    assert merged["decision_version"] == 11


def test_fetch_latest_signal_prefers_current_tracker_over_stale_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()

    tracker_payload = {
        "session_id": "pocket-live",
        "next_capture_in_sec": 8.5,
        "latest_signal": {
            "signal_id": "tracker_current",
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 1200,
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
            },
        },
    }
    observer_payload = {
        "signal_id": "observer_stale",
        "status": "stale",
        "action": "HOLD",
        "candidate_action": "SELL",
        "execution_action": "HOLD",
        "freshness_score": 0.0,
        "stale": True,
    }

    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def read(self) -> bytes:
            return self.payload

        def __init__(self, payload: bytes) -> None:
            self.payload = payload

    def fake_urlopen(req: Any, timeout: float = 1.25) -> _Resp:
        del timeout
        url = str(req.full_url)
        payload = tracker_payload if "window-tracker" in url else observer_payload
        raw = __import__("json").dumps(payload).encode("utf-8")
        return _Resp(raw)

    monkeypatch.setattr(shooter.urllib.request, "urlopen", fake_urlopen)

    latest = shooter.fetch_latest_signal("http://127.0.0.1:8793", "pocket-live")

    assert latest is not None
    assert latest["signal_id"] == "tracker_current"
    assert latest["action"] == "BUY"
    assert latest["expiry_seconds"] == 1200


def test_fetch_phoenix_major_bias_rejects_stale_fallback_bias(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()

    stale_observer_payload = {
        "signal_id": "observer_stale_bias",
        "status": "stale",
        "action": "BUY",
        "candidate_action": "BUY",
        "execution_action": "BUY",
        "freshness_score": 0.0,
        "stale": True,
        "decision_kernel": {
            "state": "ARMED",
            "dominant_side": "buy",
            "next_most_likely_event": "trigger",
        },
    }

    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def read(self) -> bytes:
            return self.payload

        def __init__(self, payload: bytes) -> None:
            self.payload = payload

    def fake_urlopen(req: Any, timeout: float = 1.25) -> _Resp:
        del timeout
        url = str(req.full_url)
        if "window-tracker" in url:
            raw = __import__("json").dumps({"session_id": "pocket-live", "latest_signal": {"status": "stale", "stale": True}}).encode("utf-8")
        else:
            raw = __import__("json").dumps(stale_observer_payload).encode("utf-8")
        return _Resp(raw)

    monkeypatch.setattr(shooter.urllib.request, "urlopen", fake_urlopen)

    assert shooter.fetch_phoenix_major_bias("http://127.0.0.1:8793", "pocket-live") is None


def test_parse_trade_signal_rejects_armed_tracker_trigger_without_actionable_execution() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_current",
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "BUY", "transition_type": "continue"},
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "major_trend_side": "buy",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.71,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.99,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_pullback_buy_without_explicit_execution_action() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_pullback_buy",
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "tracking_summary": {
                "behavior": {
                    "candle_tokens": [
                        {
                            "close_position": 0.28,
                            "micro_structure_event": "bullish_pullback_into_zone",
                        }
                    ]
                },
                "support_resistance_zones": [
                    {
                        "label": "NEAREST SUPPORT",
                        "candidate_side": "BUY",
                        "price_relation": "below_price",
                        "entry_relevance": "entry_support",
                        "distance_to_latest_norm": 0.05,
                    }
                ],
            },
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "BUY", "transition_type": "continue"},
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "major_trend_side": "buy",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.71,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.99,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_buy_without_significant_entry_area() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_mid_buy_without_area",
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "tracking_summary": {
                "tracked_candles": [
                    {"close_proxy": value}
                    for value in [0.20, 0.32, 0.44, 0.70, 0.82, 0.54, 0.56, 0.55]
                ],
                "latest_price_proxy": 0.55,
                "behavior": {
                    "candle_tokens": [
                        {
                            "close_position": 0.52,
                            "micro_structure_event": "bullish_continuation",
                        }
                    ]
                },
            },
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "BUY", "transition_type": "continue"},
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "major_trend_side": "buy",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.81,
                "p_trigger_next_1": 0.82,
                "p_trigger_next_3": 0.99,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_buy_when_entry_is_stretched_high() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_stretched_buy",
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "tracking_summary": {
                "behavior": {
                    "candle_tokens": [
                        {
                            "close_position": 0.92,
                            "micro_structure_event": "bullish_continuation",
                        }
                    ]
                },
                "support_resistance_zones": [
                    {
                        "label": "NEAREST RESISTANCE",
                        "candidate_side": "BUY",
                        "price_relation": "above_price",
                        "entry_relevance": "target_resistance",
                        "distance_to_latest_norm": 0.03,
                    }
                ],
            },
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "BUY", "transition_type": "continue"},
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "major_trend_side": "buy",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.71,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.99,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_sell_when_history_area_is_already_low() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_sell_history_low",
            "action": "SELL",
            "candidate_action": "SELL",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "tracking_summary": {
                "tracked_candles": [
                    {"close_proxy": value}
                    for value in [0.88, 0.72, 0.56, 0.44, 0.32, 0.24, 0.27, 0.26]
                ],
                "behavior": {
                    "candle_tokens": [
                        {
                            "close_position": 0.54,
                            "micro_structure_event": "bearish_continuation",
                        }
                    ]
                },
                "support_resistance_zones": [
                    {
                        "label": "NEAREST SUPPORT",
                        "candidate_side": "SELL",
                        "price_relation": "below_price",
                        "entry_relevance": "target_support",
                        "distance_to_latest_norm": 0.02,
                    }
                ],
            },
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "SELL", "transition_type": "continue"},
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "candle_execution_side": "sell",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.74,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.99,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_live_sell_continuation_without_explicit_execution_action() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_sell_live_flow",
            "action": "SELL",
            "candidate_action": "SELL",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "tracking_summary": {
                "tracked_candles": [
                    {"close_proxy": value}
                    for value in [0.88, 0.72, 0.56, 0.44, 0.32, 0.24, 0.27, 0.26]
                ],
                "latest_price_proxy": 0.26,
                "global_direction": "SELL",
                "local_direction": "SELL",
                "impulse_direction": "SELL",
                "behavior": {
                    "candle_tokens": [
                        {
                            "close_position": 0.10,
                            "micro_structure_event": "bearish_continuation",
                        }
                    ]
                },
                "support_resistance_zones": [
                    {
                        "label": "BROKEN SUPPORT",
                        "candidate_side": "SELL",
                        "price_relation": "below_price",
                        "entry_relevance": "target_support",
                        "distance_to_latest_norm": 0.08,
                    }
                ],
            },
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "SELL", "transition_type": "continue"},
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "major_trend_side": "sell",
                "dominant_side": "sell",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "candle_execution_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 4,
                "p_target_before_invalidation": 0.72,
                "p_trigger_next_1": 0.88,
                "p_trigger_next_3": 0.99,
                "p_expire_before_trigger": 0.08,
                "hazard_trigger": 0.82,
                "hazard_invalidation": 0.18,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_buy_when_history_area_is_already_high() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_buy_history_high",
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 3000,
            "focus_timeframe": "M5",
            "tracking_summary": {
                "tracked_candles": [
                    {"close_proxy": value}
                    for value in [0.12, 0.24, 0.39, 0.56, 0.68, 0.78, 0.86, 0.84]
                ],
                "behavior": {
                    "candle_tokens": [
                        {
                            "close_position": 0.48,
                            "micro_structure_event": "bullish_continuation",
                        }
                    ]
                },
                "support_resistance_zones": [
                    {
                        "label": "NEAREST RESISTANCE",
                        "candidate_side": "BUY",
                        "price_relation": "above_price",
                        "entry_relevance": "target_resistance",
                        "distance_to_latest_norm": 0.02,
                    }
                ],
            },
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "BUY", "transition_type": "continue"},
            },
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "major_trend_side": "buy",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "target_horizon_candles": 10,
                "p_target_before_invalidation": 0.74,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.99,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_rejects_advisory_kernel_fields_without_actionable_execution() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "phoenix_trigger_ready",
            "action": "HOLD",
            "candidate_action": "SELL",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 180,
            "decision_kernel": {
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "dominant_side": "sell",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "candle_execution_side": "sell",
                "firewall_action": "ALLOW",
                "firewall_reasons": ["trend_stack_aligned", "fresh_capture"],
                "reason_codes": ["DK_TRIGGER_READY", "FW_ALLOW"],
                "expected_value_R": 1.65,
            },
        }
    )

    assert parsed is None


def test_parse_trade_signal_does_not_promote_scenario_when_kernel_is_not_triggering() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_idle",
            "action": "HOLD",
            "candidate_action": "HOLD",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 300,
            "scenario_top_direction": "SELL",
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "SELL", "transition_type": "continue"},
            },
            "decision_kernel": {
                "state": "IDLE",
                "dominant_side": "hold",
                "next_most_likely_event": "invalidation",
            },
        }
    )

    assert parsed is None


def test_payload_freshness_requires_explicit_age_in_strict_mode() -> None:
    shooter = _load_shooter()

    assert shooter._payload_is_fresh(
        {
            "status": "tracking",
            "stale": False,
            "action": "BUY",
            "candidate_action": "BUY",
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
            },
        },
        3.0,
    ) is False


def test_payload_freshness_accepts_current_tracker_signal_with_age_fields() -> None:
    shooter = _load_shooter()

    assert shooter._payload_is_fresh(
        {
            "status": "tracking",
            "stale": False,
            "action": "BUY",
            "candidate_action": "BUY",
            "published_epoch": shooter.time.time(),
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
            },
        },
        3.0,
    ) is True


def test_strict_adaptive_expiry_rejects_requested_expiry_fallback() -> None:
    shooter = _load_shooter()

    with pytest.raises(ValueError, match="explicit PhoenixGuard expiry_seconds"):
        shooter._choose_adaptive_expiry({"signal_id": "strict-no-expiry"}, 300, None)


def test_strict_adaptive_expiry_accepts_explicit_expiry_field() -> None:
    shooter = _load_shooter()

    assert shooter._choose_adaptive_expiry({"expiry_seconds": "00:02:00"}, 0, None) == 120


def test_floating_status_resolves_raw_side_and_expiry_without_na() -> None:
    shooter = _load_shooter()
    box = shooter.FloatingStatusBox("pocket-live")
    payload = {
        "action": "HOLD",
        "execution_action": "HOLD",
        "major_bias": "SELL",
        "scenario_top_direction": "BUY",
        "next_capture_in_sec": 5.7,
    }

    assert box._build_raw_side_text(payload) == "Side raw: scenario_top_direction = BUY"
    assert box._build_raw_expiry_text(payload) == "Expiry raw: next_capture_in_sec = 5.7"


def test_extract_bias_side_reads_nested_decision_kernel() -> None:
    shooter = _load_shooter()

    side, source = shooter._extract_bias_side(
        {
            "action": "HOLD",
            "execution_action": "HOLD",
            "decision_kernel": {"dominant_side": "sell"},
            "tracking_summary": {"global_direction": "BUY"},
        }
    )

    assert side == "SELL"
    assert source == "bias_field(decision_kernel.dominant_side)"


def test_generate_test_signal_uses_phoenix_bias_and_countdown(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()

    def fake_phoenix_major_bias(_base_url: str, _session_id: str) -> str:
        return "SELL"

    def fake_countdown_and_timeframe(_base_url: str, _session_id: str) -> tuple[int, str]:
        return 17, "M1"

    monkeypatch.setattr(shooter, "fetch_phoenix_major_bias", fake_phoenix_major_bias)
    monkeypatch.setattr(shooter, "_fetch_phoenix_countdown_and_timeframe", fake_countdown_and_timeframe)

    signal = shooter.generate_test_signal("http://127.0.0.1:8000", "pocket-live", fallback_expiry=30)

    assert signal["execution_action"] == "SELL"
    assert signal["expiry_seconds"] == 17
    assert signal["focus_timeframe"] == "M1"


def test_generate_test_signal_refuses_random_direction_when_bias_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()

    def fake_missing_phoenix_bias(_base_url: str, _session_id: str) -> None:
        return None

    monkeypatch.setattr(shooter, "fetch_phoenix_major_bias", fake_missing_phoenix_bias)

    signal = shooter.generate_test_signal("http://127.0.0.1:8000", "pocket-live", fallback_expiry=30)

    assert signal["action"] == "HOLD"
    assert signal["actionable"] is False
    assert signal["status"] == "TEST_WAITING_FOR_PHOENIX_BIAS"
