from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, cast
from urllib import error as urllib_error
from urllib import request as urllib_request

from phoenixguard.runtime.tracker_bootstrap import (
    build_locked_tracker_controls,
    tracker_focus_is_locked,
    tracker_session_is_running,
    tracker_session_is_stale,
    tracker_session_runtime_state,
)


def _is_windows() -> bool:
    return os.name == "nt"


def _request_json(base_url: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    effective_timeout = float(timeout)
    if effective_timeout >= 30.0:
        try:
            effective_timeout = max(effective_timeout, float(os.getenv("PHOENIXGUARD_TRACKER_API_REQUEST_TIMEOUT_SEC", "240") or "240"))
        except ValueError:
            effective_timeout = max(effective_timeout, 240.0)
    data: bytes | None = None
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = urllib_request.Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method.upper())
    try:
        with urllib_request.urlopen(req, timeout=effective_timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Tracker API unreachable: {exc.reason}") from exc
    parsed = json.loads(raw)
    return dict(parsed) if isinstance(parsed, dict) else {"payload": parsed}


def _health(base_url: str) -> bool:
    try:
        try:
            timeout_sec = int(float(os.getenv("PHOENIXGUARD_TRACKER_HEALTH_REQUEST_TIMEOUT_SEC", "180") or "180"))
        except ValueError:
            timeout_sec = 180
        response = _request_json(base_url, "/v1/mobile/health", timeout=max(5, timeout_sec))
        return str(response.get("status", "")).lower() == "ok"
    except Exception:
        return False


def _wait_for_health(base_url: str, timeout_sec: int) -> bool:
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        if _health(base_url):
            return True
        time.sleep(0.8)
    return False


def _health_recovers(base_url: str, *, attempts: int = 6, delay_sec: float = 5.0) -> bool:
    for _ in range(max(1, int(attempts))):
        if _health(base_url):
            return True
        time.sleep(max(0.1, float(delay_sec)))
    return False


def _live_fast_display_heartbeat(
    base_url: str,
    session_id: str,
    session: dict[str, Any],
    *,
    last_heartbeat_epoch: float,
    script_dir: Path | None = None,
) -> float:
    """Keep the broker display buffer fresh while the study worker is busy."""
    if not bool(session.get("tracking_enabled", False)):
        return last_heartbeat_epoch
    enabled = str(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT", "1") or "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return last_heartbeat_epoch
    try:
        interval_sec = max(0.2, float(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC", "0.5") or "0.5"))
    except ValueError:
        interval_sec = 1.0
    now = time.time()
    if now - float(last_heartbeat_epoch or 0.0) < interval_sec:
        return last_heartbeat_epoch
    resolved_script_dir = script_dir or Path(__file__).resolve().parent
    file_thread_enabled = str(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_FILE_THREAD", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    if file_thread_enabled and _display_state_has_locked_window(resolved_script_dir, session_id):
        return now
    if _live_fast_display_file_heartbeat(resolved_script_dir, session_id, now_epoch=now):
        return now
    try:
        timeout_sec = max(0.2, float(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_TIMEOUT_SEC", "0.35") or "0.35"))
    except ValueError:
        timeout_sec = 0.35
    try:
        _request_json(
            base_url,
            f"/v1/mobile/window-tracker/sessions/{session_id}/capture-once?display_only=1",
            method="POST",
            timeout=timeout_sec,
        )
        return now
    except Exception:
        return now


def _parse_focus_region(raw: str | None) -> list[float] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        values = [float(part.strip()) for part in text.split(",")]
    except ValueError:
        return None
    if len(values) != 4:
        return None
    left, top, right, bottom = values
    left = max(0.0, min(1.0, left))
    top = max(0.0, min(1.0, top))
    right = max(0.0, min(1.0, right))
    bottom = max(0.0, min(1.0, bottom))
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _parse_positive_int(raw: Any) -> int:
    try:
        value = int(str(raw or "").strip() or "0")
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _stop_process(proc: subprocess.Popen[str], *, timeout_sec: float = 8.0) -> None:
    if proc.poll() is not None:
        return
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            proc.wait(timeout=max(0.1, float(timeout_sec)))
            return
        except Exception:
            pass
    proc.terminate()
    try:
        proc.wait(timeout=max(0.1, float(timeout_sec)))
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


def _resolve_python_launcher(env: dict[str, str]) -> tuple[str, str]:
    requested_exe = env.get("PHOENIXGUARD_PYTHON_BASE_EXE") or getattr(sys, "_base_executable", sys.executable)
    pyvenv_launcher = env.get("PHOENIXGUARD_PYVENV_LAUNCHER") or sys.executable
    python_exe = requested_exe
    try:
        requested_path = Path(requested_exe).resolve()
        is_windows_venv_redirector = (
            _is_windows()
            and requested_path.name.lower() == "python.exe"
            and requested_path.parent.name.lower() == "scripts"
        )
        base_exe = Path(str(getattr(sys, "_base_executable", ""))).resolve()
        if is_windows_venv_redirector and base_exe.exists() and base_exe != requested_path:
            python_exe = str(base_exe)
            pyvenv_launcher = env.get("PHOENIXGUARD_PYVENV_LAUNCHER") or str(requested_path)
    except Exception:
        python_exe = requested_exe
    return python_exe, pyvenv_launcher


def _default_live_runtime_dir(script_dir: Path, leaf: str) -> Path:
    local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "PhoenixGuard" / "codex_runtime" / leaf
    return script_dir / ".codex_runtime" / leaf


def _slugify_session_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "")).strip("._").lower() or "session"


def _live_data_dir(script_dir: Path) -> Path:
    return Path(os.getenv("PHOENIXGUARD_DATA_DIR") or _default_live_runtime_dir(script_dir, "data_live"))


def _display_state_path(script_dir: Path, session_id: str) -> Path:
    return _live_data_dir(script_dir) / "mobile_api" / "window_tracker" / "sessions" / _slugify_session_id(session_id) / "display_state.json"


def _session_dir(script_dir: Path, session_id: str) -> Path:
    return _live_data_dir(script_dir) / "mobile_api" / "window_tracker" / "sessions" / _slugify_session_id(session_id)


def _quarantine_stale_session_on_boot(script_dir: Path, session_id: str) -> bool:
    enabled = str(os.getenv("PHOENIXGUARD_RESET_STALE_TRACKER_SESSION_ON_BOOT", "1") or "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return False
    try:
        stale_after_sec = max(
            30.0,
            float(os.getenv("PHOENIXGUARD_RESET_STALE_TRACKER_SESSION_AGE_SEC", "300") or "300"),
        )
    except ValueError:
        stale_after_sec = 300.0
    session_path = _session_dir(script_dir, session_id)
    session_json = session_path / "session.json"
    if not session_json.exists():
        return False
    try:
        payload = json.loads(session_json.read_text(encoding="utf-8"))
        last_capture_epoch = float(dict(payload).get("last_capture_epoch") or 0.0) if isinstance(payload, dict) else 0.0
    except Exception:
        last_capture_epoch = 0.0
    if last_capture_epoch <= 0.0 or time.time() - last_capture_epoch <= stale_after_sec:
        return False
    target = session_path.with_name(f"{session_path.name}_stale_{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        session_path.replace(target)
        print(f"Quarantined stale tracker session '{session_id}' -> {target}", flush=True)
        return True
    except Exception as exc:
        print(f"WARNING: stale tracker session quarantine skipped: {exc}", flush=True)
        return False


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _live_fast_display_file_heartbeat(script_dir: Path, session_id: str, *, now_epoch: float) -> bool:
    enabled = str(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_FILE_HEARTBEAT", "1") or "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return False
    path = _display_state_path(script_dir, session_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    current = dict(raw)
    window_path = str(
        current.get("last_display_window_path")
        or current.get("last_window_path")
        or current.get("last_frame_path")
        or ""
    ).strip()
    if not window_path:
        return False
    try:
        previous_frame = int(float(current.get("display_frame_id") or 0))
    except (TypeError, ValueError):
        previous_frame = 0
    if previous_frame <= 0:
        return False
    next_frame = previous_frame + 1
    current["session_id"] = str(session_id)
    current.pop("last_capture_epoch", None)
    current.pop("last_capture_started_epoch", None)
    current["display_frame_id"] = next_frame
    current["display_published_epoch"] = float(now_epoch)
    current["last_display_published_epoch"] = float(now_epoch)
    current["last_display_window_path"] = window_path
    current["display_snapshot_only_v3"] = True
    current["display_fast_path_v3"] = {
        "schema_version": "PG_DISPLAY_FAST_PATH_V3",
        "reason": "supervisor_file_reuse_heartbeat",
        "display_frame_id": next_frame,
        "capture_count": int(float(current.get("capture_count") or current.get("frame_index") or next_frame)),
        "capture_epoch": float(current.get("display_capture_epoch") or current.get("last_display_capture_epoch") or now_epoch),
        "published_epoch": float(now_epoch),
        "window_path": window_path,
        "surface_signature": str(current.get("last_display_surface_signature") or current.get("last_window_surface_signature") or ""),
        "reused_window_path": True,
        "reuse_only_heartbeat": True,
    }
    current["display_reuse_only_heartbeat_v3"] = {
        "schema_version": "PG_DISPLAY_REUSE_ONLY_HEARTBEAT_V1",
        "display_frame_id": next_frame,
        "published_epoch": float(now_epoch),
        "window_path": window_path,
    }
    try:
        _write_json_atomic(path, current)
    except Exception:
        return False
    return True


def _display_state_has_locked_window(script_dir: Path, session_id: str) -> bool:
    try:
        raw = json.loads(_display_state_path(script_dir, session_id).read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    try:
        display_frame_id = int(float(raw.get("display_frame_id") or 0))
    except (TypeError, ValueError):
        display_frame_id = 0
    return bool(
        display_frame_id > 0
        and str(raw.get("last_display_window_path") or raw.get("last_window_path") or raw.get("last_frame_path") or "").strip()
    )


def _start_live_fast_display_file_heartbeat_thread(script_dir: Path, session_id: str) -> threading.Event | None:
    enabled = str(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_FILE_THREAD", "1") or "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return None
    stop_event = threading.Event()

    def _run() -> None:
        last_epoch = 0.0
        while not stop_event.is_set():
            try:
                interval_sec = max(0.2, float(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC", "0.5") or "0.5"))
            except ValueError:
                interval_sec = 0.5
            now_epoch = time.time()
            if now_epoch - last_epoch >= interval_sec:
                if _live_fast_display_file_heartbeat(script_dir, session_id, now_epoch=now_epoch):
                    last_epoch = now_epoch
            stop_event.wait(min(0.2, max(0.05, interval_sec / 2.0)))

    thread = threading.Thread(
        target=_run,
        name=f"phoenixguard-display-heartbeat-{_slugify_session_id(session_id)}",
        daemon=True,
    )
    thread.start()
    return stop_event


def _launch_mobile_api(script_dir: Path, host: str, port: int) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PHOENIXGUARD_MOBILE_API_HOST"] = host
    env["PHOENIXGUARD_MOBILE_API_PORT"] = str(port)
    env.setdefault("PHOENIXGUARD_DATA_DIR", str(_default_live_runtime_dir(script_dir, "data_live")))
    env.setdefault("PHOENIXGUARD_LOGS_DIR", str(_default_live_runtime_dir(script_dir, "logs_live")))
    if str(env.get("PHOENIXGUARD_ENABLE_OTEL", "") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        env["OTEL_SDK_DISABLED"] = "true"
        env["OTEL_TRACES_EXPORTER"] = "none"
        env["OTEL_METRICS_EXPORTER"] = "none"
        env["OTEL_LOGS_EXPORTER"] = "none"
        env["PHOENIXGUARD_TRACING_DISABLED"] = "true"
    python_exe, pyvenv_launcher = _resolve_python_launcher(env)
    if pyvenv_launcher and Path(str(python_exe)).resolve() != Path(str(pyvenv_launcher)).resolve():
        env["__PYVENV_LAUNCHER__"] = pyvenv_launcher
    return cast(subprocess.Popen[str], subprocess.Popen(
        [python_exe, str(script_dir / "start_phoenixguard_mobile_api.py")],
        cwd=str(script_dir),
        env=env,
    ))


def _write_status_file(status_path: Path, payload: dict[str, Any]) -> bool:
    tmp_path = status_path.with_name(f"{status_path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp_path.replace(status_path)
        return True
    except Exception as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        print(f"WARNING: tracker status file update skipped: {exc}", flush=True)
        return False


def _runtime_state_for_session(session: dict[str, Any], capture_interval_sec: float) -> dict[str, Any]:
    return tracker_session_runtime_state(
        session,
        max_capture_staleness_sec=max(30.0, float(capture_interval_sec) * 30.0),
        decision_stale_grace_sec=max(8.0, float(capture_interval_sec) * 8.0),
    )


def _status_file_payload(
    *,
    base_url: str,
    session: dict[str, Any],
    session_id: str,
    capture_interval_sec: float,
    dashboard_url: str,
    api_pid: int,
) -> dict[str, Any]:
    status_source = dict(session)
    try:
        live_state = _request_json(base_url, f"/v1/mobile/live/state/v3/{session_id}?mode=DIAGNOSTICS", timeout=5)
    except Exception:
        live_state = {}
    if live_state:
        for key in (
            "status",
            "tracking_enabled",
            "capture_count",
            "frame_index",
            "last_capture_epoch",
            "state_version",
            "decision_version",
            "decision_valid_until_epoch",
            "stale_status",
            "active_mode",
            "overlay_mode",
        ):
            value = live_state.get(key)
            if value not in (None, "", [], {}):
                if key in {"last_capture_epoch", "state_version", "decision_version", "decision_valid_until_epoch"}:
                    try:
                        if float(value) <= 0.0 and float(status_source.get(key, 0.0) or 0.0) > 0.0:
                            continue
                    except (TypeError, ValueError):
                        pass
                status_source[key] = value
    runtime_state = _runtime_state_for_session(status_source, capture_interval_sec)
    return {
        "session_id": status_source.get("session_id", session_id),
        "status": status_source.get("status", ""),
        "tracking_enabled": status_source.get("tracking_enabled", False),
        "focus_locked": tracker_focus_is_locked(status_source),
        "runtime_state": runtime_state,
        "capture_interval_sec": status_source.get("capture_interval_sec", capture_interval_sec),
        "capture_count": status_source.get("capture_count", 0),
        "frame_index": status_source.get("frame_index", 0),
        "last_capture_epoch": status_source.get("last_capture_epoch", 0.0),
        "state_version": status_source.get("state_version", 0),
        "decision_version": status_source.get("decision_version", 0),
        "decision_valid_until_epoch": status_source.get("decision_valid_until_epoch", 0.0),
        "stale_status": status_source.get("stale_status", ""),
        "active_mode": status_source.get("active_mode", ""),
        "overlay_mode": status_source.get("overlay_mode", ""),
        "dashboard_url": dashboard_url,
        "base_url": base_url,
        "api_pid": api_pid,
        "timestamp_epoch": time.time(),
    }


def _session_needs_worker_restart(session: dict[str, Any], capture_interval_sec: float) -> bool:
    return tracker_session_is_stale(
        session,
        max_capture_staleness_sec=max(30.0, float(capture_interval_sec) * 30.0),
        decision_stale_grace_sec=max(8.0, float(capture_interval_sec) * 8.0),
    )


def _wait_for_fresh_session(
    base_url: str,
    session_id: str,
    capture_interval_sec: float,
    *,
    timeout_sec: float = 90.0,
    poll_sec: float = 0.5,
) -> dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_sec))
    last_session: dict[str, Any] = {}
    while time.time() < deadline:
        last_session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}", timeout=30)
        runtime_state = _runtime_state_for_session(last_session, capture_interval_sec)
        if bool(runtime_state.get("fresh", False)):
            return last_session
        time.sleep(max(0.1, float(poll_sec)))
    if last_session:
        runtime_state = _runtime_state_for_session(last_session, capture_interval_sec)
        raise RuntimeError(
            "tracker worker did not publish fresh runtime state after restart: "
            f"{runtime_state.get('status', 'UNKNOWN')} {runtime_state.get('reason', '')}"
        )
    raise RuntimeError("tracker worker did not publish session state after restart")


def _wait_for_started_session(
    base_url: str,
    session_id: str,
    capture_interval_sec: float,
    *,
    timeout_sec: float = 20.0,
    poll_sec: float = 0.5,
) -> dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_sec))
    last_session: dict[str, Any] = {}
    while time.time() < deadline:
        last_session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}", timeout=30)
        if tracker_session_is_running(last_session):
            return last_session
        time.sleep(max(0.1, float(poll_sec)))
    if last_session:
        runtime_state = _runtime_state_for_session(last_session, capture_interval_sec)
        raise RuntimeError(
            "tracker worker did not enter running state after start: "
            f"{runtime_state.get('status', 'UNKNOWN')} {runtime_state.get('reason', '')}"
        )
    raise RuntimeError("tracker worker did not publish session state after start")


def _wait_for_fresh_session_or_started(
    base_url: str,
    session_id: str,
    capture_interval_sec: float,
    *,
    started_timeout_sec: float = 20.0,
    fresh_timeout_sec: float = 90.0,
) -> dict[str, Any]:
    session = _wait_for_started_session(
        base_url,
        session_id,
        capture_interval_sec,
        timeout_sec=started_timeout_sec,
    )
    try:
        return _wait_for_fresh_session(
            base_url,
            session_id,
            capture_interval_sec,
            timeout_sec=fresh_timeout_sec,
        )
    except RuntimeError as exc:
        latest_session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}", timeout=30)
        if tracker_session_is_running(latest_session):
            runtime_state = _runtime_state_for_session(latest_session, capture_interval_sec)
            print(
                "Tracker worker is running but still warming; keeping API alive: "
                f"{runtime_state.get('status', 'UNKNOWN')} {runtime_state.get('reason', exc)}",
                flush=True,
            )
            return latest_session
        raise


def _restart_tracker_worker(
    base_url: str,
    session_id: str,
    capture_interval_sec: float,
    *,
    timeout_sec: float = 90.0,
) -> dict[str, Any]:
    try:
        _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}/stop", method="POST", timeout=30)
    except Exception:
        pass
    _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}/start", method="POST", timeout=30)
    return _wait_for_started_session(
        base_url,
        session_id,
        capture_interval_sec,
        timeout_sec=min(max(5.0, float(timeout_sec)), 20.0),
    )


def _ensure_session(
    base_url: str,
    session_id: str,
    capture_interval_sec: float,
    wait_for_lock: bool,
    window_query: str,
    window_hwnd: int,
    focus_region: list[float] | None,
) -> dict[str, Any]:
    locked_hwnd = _parse_positive_int(window_hwnd)
    try:
        session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}", timeout=30)
    except Exception:
        create_payload = {
                "session_id": session_id,
                "name": session_id,
                "window_query": window_query or "Pocket Option",
                "layout_profile": "auto",
                "capture_interval_sec": float(capture_interval_sec),
            "auto_start": False,
            "observer_policy": {
                "single_surface_mode": True,
                "min_actionable_confidence": 0.58,
                "min_thesis_confidence": 0.46,
                "signal_cooldown_sec": 8.0,
            },
        }
        if locked_hwnd > 0:
            create_payload["locked_hwnd"] = locked_hwnd
            create_payload["locked_title"] = window_query or "Pocket Option"
        session = _request_json(base_url, "/v1/mobile/window-tracker/sessions", method="POST", payload=create_payload, timeout=30)
    if locked_hwnd > 0:
        locked = session.get("locked_window", {})
        current_hwnd = 0
        if isinstance(locked, dict):
            current_hwnd = _parse_positive_int(locked.get("hwnd"))
        if current_hwnd != locked_hwnd:
            session = _request_json(
                base_url,
                f"/v1/mobile/window-tracker/sessions/{session_id}/locked-window",
                method="PATCH",
                payload={"locked_hwnd": locked_hwnd, "locked_title": window_query or "Pocket Option"},
                timeout=30,
            )

    live_execution_enabled = str(os.getenv("PHOENIXGUARD_LIVE_EXECUTION_ENABLED", "1") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    execution_mode = "live" if live_execution_enabled else "shadow"
    controls = build_locked_tracker_controls(
        capture_interval_sec=capture_interval_sec,
        live_execution_enabled=live_execution_enabled,
        execution_mode=execution_mode,
        max_capture_interval_sec=max(1.0, float(capture_interval_sec)),
        min_capture_interval_sec=0.5,
        broker_surface_cache_sec=float(os.getenv("PHOENIXGUARD_BROKER_SURFACE_CACHE_SEC", "30") or "30"),
        cooldown_sec=float(os.getenv("PHOENIXGUARD_EXECUTION_COOLDOWN_SEC", "600") or "600"),
        loss_guard_enabled=str(os.getenv("PHOENIXGUARD_LOSS_GUARD_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"},
        loss_guard_max_consecutive_losses=int(os.getenv("PHOENIXGUARD_LOSS_GUARD_MAX_CONSECUTIVE_LOSSES", "2") or "2"),
        loss_guard_window_sec=float(os.getenv("PHOENIXGUARD_LOSS_GUARD_WINDOW_SEC", "5400") or "5400"),
        loss_guard_pause_sec=float(os.getenv("PHOENIXGUARD_LOSS_GUARD_PAUSE_SEC", "2700") or "2700"),
    )
    session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}/controls", method="PATCH", payload=controls, timeout=30)

    if focus_region is not None:
        for _attempt in range(3):
            manual_focus = session.get("manual_focus_region", {})
            manual_focus_enabled = isinstance(manual_focus, dict) and bool(manual_focus.get("enabled", False))
            if not manual_focus_enabled:
                session = _request_json(
                    base_url,
                    f"/v1/mobile/window-tracker/sessions/{session_id}/focus-region",
                    method="PUT",
                    payload={"normalized_bbox": focus_region, "source": "launcher_auto_chart_region"},
                    timeout=30,
                )
            if tracker_focus_is_locked(session):
                break
            time.sleep(0.5)
            session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}", timeout=30)

    if wait_for_lock and not tracker_focus_is_locked(session):
        return session
    if tracker_session_is_running(session) and _session_needs_worker_restart(session, capture_interval_sec):
        session = _restart_tracker_worker(base_url, session_id, capture_interval_sec)
    elif not tracker_session_is_running(session):
        _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}/start", method="POST", timeout=30)
        session = _wait_for_started_session(base_url, session_id, capture_interval_sec)
    return session


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the PhoenixGuard 24/7 locked tracker.")
    parser.add_argument("--host", default=os.getenv("PHOENIXGUARD_MOBILE_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PHOENIXGUARD_MOBILE_API_PORT", "8793")))
    parser.add_argument("--session-id", default=os.getenv("PHOENIXGUARD_TRACKER_SESSION_ID", "pocket-live-8788"))
    parser.add_argument("--capture-interval", type=float, default=float(os.getenv("PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC", "1.0")))
    parser.add_argument("--window-query", default=os.getenv("PHOENIXGUARD_BROKER_WINDOW_QUERY", "Pocket Option"))
    parser.add_argument("--window-hwnd", type=int, default=_parse_positive_int(os.getenv("PHOENIXGUARD_BROKER_WINDOW_HWND", "0")))
    parser.add_argument(
        "--focus-region",
        default=os.getenv("PHOENIXGUARD_TRACKER_FOCUS_REGION", "0.03,0.13,0.87,0.96"),
        help="Normalized chart focus box as left,top,right,bottom. Use empty string to require manual dashboard lock.",
    )
    parser.add_argument("--health-timeout", type=int, default=int(os.getenv("PHOENIXGUARD_TRACKER_HEALTH_TIMEOUT_SEC", "60")))
    parser.add_argument("--status-file", default=os.getenv("PHOENIXGUARD_TRACKER_STATUS_FILE", str(Path(__file__).resolve().parent / ".codex_runtime" / "tracker_status.json")))
    parser.add_argument("--health-probe-retries", type=int, default=int(os.getenv("PHOENIXGUARD_TRACKER_HEALTH_PROBE_RETRIES", "6")))
    parser.add_argument("--session-read-failures", type=int, default=int(os.getenv("PHOENIXGUARD_TRACKER_SESSION_READ_FAILURES", "3")))
    parser.add_argument("--wait-for-lock", action="store_true", default=True)
    parser.add_argument("--no-wait-for-lock", dest="wait_for_lock", action="store_false")
    parser.add_argument("--open-dashboard", action="store_true", default=True)
    parser.add_argument("--no-open-dashboard", dest="open_dashboard", action="store_false")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    os.environ.setdefault("PHOENIXGUARD_DATA_DIR", str(_default_live_runtime_dir(script_dir, "data_live")))
    os.environ.setdefault("PHOENIXGUARD_LOGS_DIR", str(_default_live_runtime_dir(script_dir, "logs_live")))
    os.environ.setdefault("PHOENIXGUARD_LIVE_STATE_CACHE_TTL_SEC", "5.0")
    os.environ.setdefault("PHOENIXGUARD_FRONTEND_HEARTBEAT_STALE_SEC", "8.0")
    os.environ.setdefault("PHOENIXGUARD_CAPTURE_ONCE_FAST_DISPLAY", "1")
    os.environ.setdefault("PHOENIXGUARD_DISPLAY_FAST_VISIBLE_CAPTURE", "1")
    os.environ.setdefault("PHOENIXGUARD_DISPLAY_REUSE_IDENTICAL_SURFACE", "1")
    os.environ.setdefault("PHOENIXGUARD_DISPLAY_BUSY_REUSE_HEARTBEAT", "1")
    os.environ.setdefault("PHOENIXGUARD_DISPLAY_REUSE_ONLY_HEARTBEAT", "1")
    os.environ.setdefault("PHOENIXGUARD_DISPLAY_SNAPSHOT_STALE_RESET_SEC", "30.0")
    os.environ.setdefault("PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_POLL_SEC", "0.50")
    os.environ.setdefault("PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_TIMEOUT_SEC", "0.35")
    os.environ.setdefault("PHOENIXGUARD_LIVE_FAST_DISPLAY_FILE_HEARTBEAT", "1")
    os.environ.setdefault("PHOENIXGUARD_LIVE_FAST_DISPLAY_FILE_THREAD", "1")
    os.environ.setdefault("PHOENIXGUARD_POCKET_FAST_FOREGROUND_IMAGEGRAB", "0")
    os.environ.setdefault("PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL", "0")
    os.environ.setdefault("PHOENIXGUARD_LIVE_WINDOW_JPEG", "1")
    os.environ.setdefault("PHOENIXGUARD_LIVE_WINDOW_JPEG_QUALITY", "78")
    os.environ.setdefault("PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS", "1")
    os.environ.setdefault("PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N", "300")
    os.environ.setdefault("PHOENIXGUARD_TRUST_LOCKED_WINDOW_DESCRIPTOR", "1")
    os.environ.setdefault("PHOENIXGUARD_LIVE_STATE_CLEAN_OVERLAYS_ONLY", "1")
    os.environ.setdefault("PHOENIXGUARD_LIVE_MIN_CAPTURE_INTERVAL_SEC", "0.5")
    base_url = f"http://{args.host}:{args.port}"
    dashboard_url = f"{base_url}/v1/mobile/window-tracker/dashboard/{args.session_id}"
    configured_focus_region = _parse_focus_region(args.focus_region)
    status_path = Path(args.status_file)
    consecutive_restart_count = 0
    consecutive_session_read_failures = 0
    session: dict[str, Any] = {}
    api_proc: subprocess.Popen[str] | None = None
    file_heartbeat_stop = _start_live_fast_display_file_heartbeat_thread(script_dir, args.session_id)

    try:
        if args.open_dashboard:
            try:
                webbrowser.open(dashboard_url)
            except Exception:
                pass

        while True:
            _quarantine_stale_session_on_boot(script_dir, args.session_id)
            api_proc = _launch_mobile_api(script_dir, args.host, args.port)
            print(f"Launching mobile API on {base_url} (PID {api_proc.pid})")

            if not _wait_for_health(base_url, args.health_timeout):
                _stop_process(api_proc)
                consecutive_restart_count += 1
                print(
                    f"PhoenixGuard mobile API did not become healthy on {base_url}; "
                    f"restart attempt {consecutive_restart_count}."
                )
                time.sleep(min(30.0, 2.0 * consecutive_restart_count))
                continue

            try:
                session = _ensure_session(
                    base_url,
                    args.session_id,
                args.capture_interval,
                args.wait_for_lock,
                args.window_query,
                args.window_hwnd,
                configured_focus_region,
            )
            except Exception as exc:
                _stop_process(api_proc)
                consecutive_restart_count += 1
                print(
                    f"Tracker session setup failed; restarting mobile API "
                    f"(attempt {consecutive_restart_count}): {exc}",
                    flush=True,
                )
                time.sleep(min(30.0, 2.0 * consecutive_restart_count))
                continue
            consecutive_restart_count = 0
            consecutive_session_read_failures = 0
            if args.wait_for_lock and not tracker_focus_is_locked(session):
                print(f"Session '{args.session_id}' is ready but not focus-locked yet.")
                print(f"Open the dashboard and lock the {args.window_query or 'broker'} chart: {dashboard_url}")
                while True:
                    _write_status_file(status_path, {
                        "session_id": args.session_id,
                        "status": session.get("status", "waiting-for-lock"),
                        "tracking_enabled": session.get("tracking_enabled", False),
                        "focus_locked": False,
                        "dashboard_url": dashboard_url,
                        "base_url": base_url,
                        "api_pid": api_proc.pid,
                        "timestamp_epoch": time.time(),
                    })
                    time.sleep(2.0)
                    if api_proc.poll() is not None:
                        consecutive_restart_count += 1
                        print(
                            f"Mobile API exited while waiting for focus lock; "
                            f"restart attempt {consecutive_restart_count}."
                        )
                        break
                    if not _health(base_url):
                        if _health_recovers(base_url, attempts=args.health_probe_retries):
                            consecutive_restart_count = 0
                            continue
                        _stop_process(api_proc)
                        consecutive_restart_count += 1
                        print(
                            f"PhoenixGuard mobile API health failed while waiting for focus lock; "
                            f"restart attempt {consecutive_restart_count}."
                        )
                        break
                    try:
                        session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{args.session_id}", timeout=30)
                        consecutive_session_read_failures = 0
                    except Exception as exc:
                        consecutive_session_read_failures += 1
                        print(
                            f"Tracker session read failed while waiting for focus lock "
                            f"({consecutive_session_read_failures}/{args.session_read_failures}): {exc}"
                        )
                        if consecutive_session_read_failures >= max(1, args.session_read_failures):
                            if _health_recovers(base_url, attempts=args.health_probe_retries):
                                consecutive_session_read_failures = 0
                                continue
                            _stop_process(api_proc)
                            consecutive_restart_count += 1
                            break
                        continue
                    if configured_focus_region is not None and not tracker_focus_is_locked(session):
                        try:
                            session = _request_json(
                                base_url,
                                f"/v1/mobile/window-tracker/sessions/{args.session_id}/focus-region",
                                method="PUT",
                                payload={
                                    "normalized_bbox": configured_focus_region,
                                    "source": "launcher_auto_chart_region_relock",
                                },
                                timeout=30,
                            )
                            consecutive_session_read_failures = 0
                        except Exception as exc:
                            consecutive_session_read_failures += 1
                            print(
                                f"Tracker focus relock failed while waiting for lock "
                                f"({consecutive_session_read_failures}/{args.session_read_failures}): {exc}",
                                flush=True,
                            )
                            if consecutive_session_read_failures >= max(1, args.session_read_failures):
                                _stop_process(api_proc)
                                consecutive_restart_count += 1
                                break
                            continue
                    if tracker_focus_is_locked(session):
                        session = _ensure_session(
                            base_url,
                            args.session_id,
                            args.capture_interval,
                            False,
                            args.window_query,
                            configured_focus_region,
                        )
                        consecutive_session_read_failures = 0
                        break
                if not tracker_focus_is_locked(session):
                    continue
                print("Focus lock detected. Tracker is now armed and running.")

            runtime_state = _runtime_state_for_session(session, args.capture_interval)
            _write_status_file(
                status_path,
                _status_file_payload(
                    base_url=base_url,
                    session=session,
                    session_id=args.session_id,
                    capture_interval_sec=args.capture_interval,
                    dashboard_url=dashboard_url,
                    api_pid=api_proc.pid,
                ),
            )

            print(json.dumps({
                "session_id": session.get("session_id", args.session_id),
                "status": session.get("status", ""),
                "tracking_enabled": session.get("tracking_enabled", False),
                "capture_interval_sec": session.get("capture_interval_sec", args.capture_interval),
                "runtime_state": runtime_state,
                "last_capture_epoch": session.get("last_capture_epoch", 0.0),
                "state_version": session.get("state_version", 0),
                "decision_version": session.get("decision_version", 0),
                "decision_valid_until_epoch": session.get("decision_valid_until_epoch", 0.0),
            }, indent=2, ensure_ascii=True))

            last_fast_display_heartbeat_epoch = 0.0
            while True:
                try:
                    heartbeat_poll_sec = max(
                        0.05,
                        float(os.getenv("PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_POLL_SEC", "0.25") or "0.25"),
                    )
                except ValueError:
                    heartbeat_poll_sec = 0.25
                for _ in range(max(1, int(round(10.0 / heartbeat_poll_sec)))):
                    time.sleep(heartbeat_poll_sec)
                    if api_proc.poll() is not None:
                        break
                    last_fast_display_heartbeat_epoch = _live_fast_display_heartbeat(
                        base_url,
                        args.session_id,
                        session,
                        last_heartbeat_epoch=last_fast_display_heartbeat_epoch,
                        script_dir=script_dir,
                    )
                if api_proc.poll() is not None:
                    consecutive_restart_count += 1
                    print(f"Mobile API exited with code {api_proc.returncode}; restarting.")
                    break

                if not _health(base_url):
                    if _health_recovers(base_url, attempts=args.health_probe_retries):
                        consecutive_restart_count = 0
                        continue
                    _stop_process(api_proc)
                    consecutive_restart_count += 1
                    print(
                        f"Mobile API health check failed after retries; "
                        f"restarting (attempt {consecutive_restart_count})."
                    )
                    break

                try:
                    session = _request_json(base_url, f"/v1/mobile/window-tracker/sessions/{args.session_id}", timeout=30)
                    consecutive_session_read_failures = 0
                    consecutive_restart_count = 0
                except Exception as exc:
                    consecutive_session_read_failures += 1
                    print(
                        f"Tracker session read failed "
                        f"({consecutive_session_read_failures}/{args.session_read_failures}): {exc}"
                    )
                    if consecutive_session_read_failures < max(1, args.session_read_failures):
                        continue
                    if _health_recovers(base_url, attempts=args.health_probe_retries):
                        consecutive_session_read_failures = 0
                        continue
                    _stop_process(api_proc)
                    consecutive_restart_count += 1
                    print(
                        f"Tracker session remained unreadable; restarting mobile API "
                        f"(attempt {consecutive_restart_count})."
                    )
                    break
                if configured_focus_region is not None and not tracker_focus_is_locked(session):
                    print(
                        "Tracker focus lock is missing; reapplying configured broker focus region.",
                        flush=True,
                    )
                    try:
                        session = _request_json(
                            base_url,
                            f"/v1/mobile/window-tracker/sessions/{args.session_id}/focus-region",
                            method="PUT",
                            payload={
                                "normalized_bbox": configured_focus_region,
                                "source": "launcher_auto_chart_region_relock",
                            },
                            timeout=30,
                        )
                        consecutive_session_read_failures = 0
                    except Exception as exc:
                        consecutive_restart_count += 1
                        print(
                            f"Tracker focus relock failed; restarting mobile API "
                            f"(attempt {consecutive_restart_count}): {exc}",
                            flush=True,
                        )
                        _stop_process(api_proc)
                        break
                runtime_state = _runtime_state_for_session(session, args.capture_interval)
                if not tracker_session_is_running(session):
                    print(
                        "Tracker session is not running; starting tracker worker: "
                        f"{runtime_state.get('reason', 'tracker session stopped')}",
                        flush=True,
                    )
                    try:
                        _request_json(
                            base_url,
                            f"/v1/mobile/window-tracker/sessions/{args.session_id}/start",
                            method="POST",
                            timeout=30,
                        )
                        session = _wait_for_started_session(base_url, args.session_id, args.capture_interval)
                        runtime_state = _runtime_state_for_session(session, args.capture_interval)
                    except Exception as exc:
                        consecutive_restart_count += 1
                        print(
                            f"Tracker worker start failed; restarting mobile API "
                            f"(attempt {consecutive_restart_count}): {exc}",
                            flush=True,
                        )
                        _stop_process(api_proc)
                        break
                elif bool(runtime_state.get("stale", False)):
                    print(
                        "Tracker session is stale; restarting tracker worker: "
                        f"{runtime_state.get('reason', 'stale runtime state')}",
                        flush=True,
                    )
                    try:
                        session = _restart_tracker_worker(base_url, args.session_id, args.capture_interval)
                        runtime_state = _runtime_state_for_session(session, args.capture_interval)
                    except Exception as exc:
                        consecutive_restart_count += 1
                        print(
                            f"Tracker worker restart failed; restarting mobile API "
                            f"(attempt {consecutive_restart_count}): {exc}",
                            flush=True,
                        )
                        _stop_process(api_proc)
                        break
                _write_status_file(
                    status_path,
                    _status_file_payload(
                        base_url=base_url,
                        session=session,
                        session_id=args.session_id,
                        capture_interval_sec=args.capture_interval,
                        dashboard_url=dashboard_url,
                        api_pid=api_proc.pid,
                    ),
                )
    except KeyboardInterrupt:
        pass
    finally:
        if file_heartbeat_stop is not None:
            file_heartbeat_stop.set()
        try:
            if session:
                _write_status_file(status_path, {
                    "session_id": session.get("session_id", args.session_id),
                    "status": session.get("status", "stopped"),
                    "tracking_enabled": False,
                    "focus_locked": tracker_focus_is_locked(session),
                    "dashboard_url": dashboard_url,
                    "base_url": base_url,
                    "timestamp_epoch": time.time(),
                })
        except Exception:
            pass
        # The launcher is meant to be long-running; only terminate child API on explicit stop.
        if api_proc is not None and api_proc.poll() is None:
            _stop_process(api_proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
