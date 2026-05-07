import ctypes
import importlib.util
from pathlib import Path
from typing import Any

import pytest

if not hasattr(ctypes, "windll"):
    pytest.skip("808 Shooter runtime is Windows-only", allow_module_level=True)


def _load_shooter():
    module_path = Path(__file__).resolve().parents[1] / "808 Shooter.py"
    spec = importlib.util.spec_from_file_location("_808_shooter_runtime_test", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extract_signal_payload_unwraps_latest_signal() -> None:
    shooter = _load_shooter()

    signal = {"action": "BUY", "actionable": True}
    assert shooter._extract_signal_payload({"latest_signal": signal}) == signal


def test_parse_trade_signal_accepts_action_only_payload() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "action": "SELL",
            "actionable": "true",
            "expiry_seconds": "00:02:00",
            "timestamp": "2026-05-02T12:00:00+00:00",
        }
    )

    assert parsed is not None
    assert parsed[:3] == ("SELL", 120, "2026-05-02T12:00:00+00:00")
    assert parsed[4] == "expiry_seconds"
    assert parsed[6] == "action"


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


def test_parse_trade_signal_rejects_armed_tracker_trigger_without_actionable_execution() -> None:
    shooter = _load_shooter()

    parsed = shooter.parse_trade_signal(
        {
            "signal_id": "tracker_current",
            "action": "BUY",
            "candidate_action": "BUY",
            "execution_action": "HOLD",
            "actionable": False,
            "expiry_seconds": 1200,
            "scenario_analysis": {
                "status": "ready",
                "top_scenario": {"direction": "BUY", "transition_type": "continue"},
            },
            "decision_kernel": {
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "dominant_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
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


def test_payload_freshness_accepts_current_tracker_signal_without_age_fields() -> None:
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
    ) is True


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
