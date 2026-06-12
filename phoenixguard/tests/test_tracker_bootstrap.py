from __future__ import annotations

import json
import time
from pathlib import Path

import start_phoenixguard_24_7_tracker as tracker_launcher
from phoenixguard.runtime.tracker_bootstrap import (
    build_locked_tracker_controls,
    tracker_focus_is_locked,
    tracker_session_is_running,
    tracker_session_is_stale,
    tracker_session_runtime_state,
)


def test_build_locked_tracker_controls_uses_safe_tracking_defaults() -> None:
    controls = build_locked_tracker_controls(1.0)

    assert controls["capture_interval_sec"] == 1.0
    assert controls["live_execution_enabled"] is True
    assert controls["execution_mode"] == "live"
    assert controls["require_market_identity"] is True
    assert controls["require_timeframe_identity"] is True
    assert controls["allow_locked_surface_identity_fallback"] is True
    assert controls["adaptive_timer_enabled"] is True
    assert controls["trade_profile"] == "HIGH_FREQUENCY"
    assert controls["high_frequency_expiry_seconds"] == 600
    assert controls["scenario_generation_enabled"] is False
    assert controls["max_executions_per_window"] == 1
    assert controls["execution_window_sec"] == 600.0
    assert controls["cooldown_sec"] == 600.0


def test_tracker_focus_is_locked_from_manual_focus_region() -> None:
    session = {"manual_focus_region": {"enabled": True, "normalized_bbox": [0.1, 0.2, 0.8, 0.9]}}

    assert tracker_focus_is_locked(session) is True


def test_tracker_focus_is_locked_from_focus_selector_state() -> None:
    session = {"focus_selector": {"status": "selected"}}

    assert tracker_focus_is_locked(session) is True


def test_tracker_session_is_running_requires_tracking_enabled_and_status() -> None:
    assert tracker_session_is_running({"tracking_enabled": True, "status": "running"}) is True
    assert tracker_session_is_running({"tracking_enabled": True, "status": "ready"}) is False


def test_tracker_session_runtime_state_marks_old_capture_stale() -> None:
    state = tracker_session_runtime_state(
        {
            "tracking_enabled": True,
            "status": "running",
            "capture_interval_sec": 0.5,
            "last_capture_epoch": 100.0,
            "decision_valid_until_epoch": 120.0,
        },
        now_epoch=200.0,
        max_capture_staleness_sec=30.0,
    )

    assert state["status"] == "STALE"
    assert state["stale"] is True
    assert "last_capture_epoch" in state["reason"]


def test_tracker_session_runtime_state_allows_slow_live_pipeline_window() -> None:
    state = tracker_session_runtime_state(
        {
            "tracking_enabled": True,
            "status": "running",
            "capture_interval_sec": 0.5,
            "last_capture_epoch": 100.0,
            "decision_valid_until_epoch": 400.0,
            "latest_signal": {
                "pipeline_latency_sec": 65.0,
                "freshness_window_sec": 195.0,
            },
        },
        now_epoch=200.0,
        max_capture_staleness_sec=30.0,
    )

    assert state["status"] == "FRESH"
    assert state["stale"] is False


def test_tracker_session_runtime_state_marks_expired_decision_stale() -> None:
    state = tracker_session_runtime_state(
        {
            "tracking_enabled": True,
            "status": "running",
            "capture_interval_sec": 0.5,
            "last_capture_epoch": 198.0,
            "decision_valid_until_epoch": 150.0,
        },
        now_epoch=200.0,
        decision_stale_grace_sec=8.0,
    )

    assert state["status"] == "STALE"
    assert tracker_session_is_stale(
        {
            "tracking_enabled": True,
            "status": "running",
            "capture_interval_sec": 0.5,
            "last_capture_epoch": 198.0,
            "decision_valid_until_epoch": 150.0,
        },
        now_epoch=200.0,
        decision_stale_grace_sec=8.0,
    ) is True


def test_tracker_session_runtime_state_allows_first_capture_warming() -> None:
    state = tracker_session_runtime_state(
        {"tracking_enabled": True, "status": "running", "capture_interval_sec": 0.5},
        now_epoch=200.0,
    )

    assert state["status"] == "WARMING"
    assert state["stale"] is False


def test_tracker_status_file_write_success(tmp_path: Path) -> None:
    status_path = tmp_path / "tracker_status.json"

    assert tracker_launcher._write_status_file(status_path, {"status": "running"}) is True

    assert json.loads(status_path.read_text(encoding="utf-8")) == {"status": "running"}
    assert list(tmp_path.glob("tracker_status.json.*.tmp")) == []


def test_tracker_status_file_write_failure_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    status_path = tmp_path / "tracker_status.json"
    original_replace = Path.replace

    def _locked_replace(self: Path, target: Path) -> Path:
        if Path(target) == status_path:
            raise OSError("status file locked")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", _locked_replace)

    assert tracker_launcher._write_status_file(status_path, {"status": "running"}) is False
    assert not status_path.exists()
    assert list(tmp_path.glob("tracker_status.json.*.tmp")) == []


def test_resolve_python_launcher_unwraps_windows_venv_redirector(monkeypatch, tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    base_python = tmp_path / "Python311" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    base_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    base_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(tracker_launcher, "_is_windows", lambda: True)
    monkeypatch.setattr(tracker_launcher.sys, "_base_executable", str(base_python), raising=False)

    python_exe, pyvenv_launcher = tracker_launcher._resolve_python_launcher(
        {"PHOENIXGUARD_PYTHON_BASE_EXE": str(venv_python)}
    )

    assert Path(python_exe).resolve() == base_python.resolve()
    assert Path(pyvenv_launcher).resolve() == venv_python.resolve()


def test_resolve_python_launcher_keeps_requested_exe_on_non_windows(monkeypatch, tmp_path: Path) -> None:
    requested_python = tmp_path / ".venv" / "bin" / "python"
    requested_python.parent.mkdir(parents=True)
    requested_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(tracker_launcher, "_is_windows", lambda: False)

    python_exe, _ = tracker_launcher._resolve_python_launcher(
        {"PHOENIXGUARD_PYTHON_BASE_EXE": str(requested_python)}
    )

    assert Path(python_exe).resolve() == requested_python.resolve()


def test_restart_tracker_worker_stops_starts_and_waits_for_fresh(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_request_json(
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        timeout: int = 30,
    ) -> dict:
        del base_url, payload, timeout
        calls.append((method, path))
        if path.endswith("/stop"):
            return {"status": "ready", "tracking_enabled": False}
        if path.endswith("/start"):
            return {"status": "running", "tracking_enabled": True}
        now = time.time()
        return {
            "session_id": "pocket-live-8788",
            "status": "running",
            "tracking_enabled": True,
            "capture_interval_sec": 0.5,
            "last_capture_epoch": now,
            "decision_valid_until_epoch": now + 5.0,
        }

    monkeypatch.setattr(tracker_launcher, "_request_json", _fake_request_json)

    session = tracker_launcher._restart_tracker_worker("http://127.0.0.1:8793", "pocket-live-8788", 0.5)

    assert session["tracking_enabled"] is True
    assert calls[0] == ("POST", "/v1/mobile/window-tracker/sessions/pocket-live-8788/stop")
    assert calls[1] == ("POST", "/v1/mobile/window-tracker/sessions/pocket-live-8788/start")
    assert calls[2] == ("GET", "/v1/mobile/window-tracker/sessions/pocket-live-8788")


def test_wait_for_started_session_accepts_running_before_fresh(monkeypatch) -> None:
    calls = 0

    def _fake_request_json(
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        timeout: int = 30,
    ) -> dict:
        nonlocal calls
        del base_url, path, method, payload, timeout
        calls += 1
        return {
            "session_id": "pocket-live-8788",
            "status": "running",
            "tracking_enabled": True,
            "capture_interval_sec": 0.5,
        }

    monkeypatch.setattr(tracker_launcher, "_request_json", _fake_request_json)

    session = tracker_launcher._wait_for_started_session("http://127.0.0.1:8793", "pocket-live-8788", 0.5)

    assert calls == 1
    assert session["status"] == "running"
    assert session["tracking_enabled"] is True


def test_wait_for_fresh_session_or_started_keeps_running_warming_session(monkeypatch) -> None:
    def _fake_request_json(
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        timeout: int = 30,
    ) -> dict:
        del base_url, path, method, payload, timeout
        return {
            "session_id": "pocket-live-8788",
            "status": "running",
            "tracking_enabled": True,
            "capture_interval_sec": 0.5,
        }

    def _fake_wait_for_fresh_session(*args, **kwargs) -> dict:
        del args, kwargs
        raise RuntimeError("still warming")

    monkeypatch.setattr(tracker_launcher, "_request_json", _fake_request_json)
    monkeypatch.setattr(tracker_launcher, "_wait_for_fresh_session", _fake_wait_for_fresh_session)

    session = tracker_launcher._wait_for_fresh_session_or_started(
        "http://127.0.0.1:8793",
        "pocket-live-8788",
        0.5,
        started_timeout_sec=1.0,
        fresh_timeout_sec=1.0,
    )

    assert session["status"] == "running"
    assert session["tracking_enabled"] is True
