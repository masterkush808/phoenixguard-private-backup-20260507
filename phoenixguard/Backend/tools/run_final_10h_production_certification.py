from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence, cast
import urllib.error
import urllib.parse
import urllib.request


SCHEMA_VERSION = "PG_FINAL_10H_CERTIFICATION_V1"
DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION_ID = "pocket-live-8788"
ALLOWED_PACKAGE_TYPES = {"SWING", "INTRADAY_ENTER_NOW", "SWING_ENTER_NOW"}
PROGRESSION_HORIZONS_SEC = (30, 60, 120, 300)
DIAGNOSTIC_ENDPOINTS = frozenset(
    {
        "runtime_trace",
        "live_state",
        "session",
        "performance",
        "model_latest",
        "study_latest",
        "execution_latest",
        "floating_state",
    }
)
EndpointResult = tuple[int, dict[str, Any], str, float]


@dataclass(frozen=True)
class CaptureJob:
    kind: str
    started_epoch: float
    command: list[str]
    out_dir: Path
    stdout_path: Path
    stderr_path: Path
    timeout_sec: float
    process: subprocess.Popen[bytes]


def _now_epoch() -> float:
    return time.time()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[Any]:
    return list(cast(Sequence[Any], value)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _upper(value: object, default: str = "") -> str:
    return _text(value, default).upper()


def _float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _age_ms(primary: object, fallback: object, default: float = 0.0) -> float:
    primary_value = _float(primary, -1.0)
    if primary_value > 0.0:
        return primary_value
    fallback_value = _float(fallback, -1.0)
    if fallback_value > 0.0:
        return fallback_value
    return default


def _epoch_age_ms(value: object, *, now_epoch: float | None = None, default: float = 0.0) -> float:
    epoch = _float(value, 0.0)
    if epoch <= 0.0:
        return default
    now = float(now_epoch if now_epoch is not None else _now_epoch())
    if epoch > 10_000_000_000.0:
        epoch = epoch / 1000.0
    return max(0.0, (now - epoch) * 1000.0)


def _int(value: object, default: int = 0) -> int:
    return int(_float(value, float(int(default))))


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    tmp_path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=True, default=str), encoding="utf-8")
    tmp_path.replace(path)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _get_json(url: str, timeout_sec: float) -> EndpointResult:
    started = time.perf_counter()
    request = urllib.request.Request(url=url, method="GET", headers={"Accept": "application/json", "Connection": "close"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310 - local operator endpoint
            raw = response.read().decode("utf-8", errors="replace")
        payload: object = json.loads(raw) if raw.strip() else {}
        return int(response.status), _mapping(payload), "", (time.perf_counter() - started) * 1000.0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return int(exc.code), _mapping(payload), "", (time.perf_counter() - started) * 1000.0
    except Exception as exc:
        return 0, {}, f"{type(exc).__name__}: {exc}", (time.perf_counter() - started) * 1000.0


def _fetch_endpoint_results(
    urls: Mapping[str, str],
    endpoint_names: Sequence[str],
    timeout_sec: float,
) -> dict[str, EndpointResult]:
    names = [name for name in endpoint_names if name in urls]
    if not names:
        return {}
    max_workers = max(1, min(len(names), 6))
    results: dict[str, EndpointResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pg-cert-endpoint") as executor:
        futures = {executor.submit(_get_json, urls[name], timeout_sec): name for name in names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = (0, {}, f"{type(exc).__name__}: {exc}", 0.0)
    return results


def fetch_endpoint_results_for_certification(
    urls: Mapping[str, str],
    endpoint_names: Sequence[str],
    timeout_sec: float,
) -> dict[str, EndpointResult]:
    return _fetch_endpoint_results(urls, endpoint_names, timeout_sec)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _python_executable() -> str:
    env_exe = os.getenv("PHOENIXGUARD_PYTHON_EXE", "").strip()
    if env_exe and Path(env_exe).exists():
        return env_exe
    repo_python = _repo_root() / ".venv" / "Scripts" / "python.exe"
    if repo_python.exists():
        return str(repo_python)
    return sys.executable


def _common_files_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
    return _repo_root() / ".codex_runtime" / "mt4_common_files"


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _mapping(parsed)


def _slugify_session_id(value: str) -> str:
    text = str(value or "").strip()
    slug = "".join(char if char.isalnum() or char in "._-" else "_" for char in text).strip("._").lower()
    return slug or "session"


def _direct_display_state_path(session_id: str) -> Path:
    return (
        _repo_root()
        / ".codex_runtime"
        / "data_live"
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / _slugify_session_id(session_id)
        / "display_state.json"
    )


def _direct_display_state(session_id: str) -> dict[str, Any]:
    return _read_json_file(_direct_display_state_path(session_id))


def _direct_display_summary(display_state: Mapping[str, Any]) -> dict[str, object]:
    published_age_ms = _epoch_age_ms(
        display_state.get("display_published_epoch")
        or display_state.get("last_display_published_epoch")
        or _mapping(display_state.get("display_fast_path_v3")).get("published_epoch")
    )
    return {
        "available": bool(display_state),
        "frame_index": _int(display_state.get("frame_index"), 0),
        "display_frame_id": _int(display_state.get("display_frame_id"), 0),
        "chart_frame_id": _int(display_state.get("chart_frame_id"), 0),
        "overlay_frame_id": _int(display_state.get("overlay_frame_id"), 0),
        "model_vote_frame_id": _int(display_state.get("model_vote_frame_id"), 0),
        "published_age_ms": round(published_age_ms, 3),
        "display_snapshot_only": bool(display_state.get("display_snapshot_only_v3") is True),
    }


def _notify(title: str, message: str, *, loud: bool = False) -> None:
    try:
        import winsound

        if loud:
            for _ in range(3):
                winsound.Beep(1200, 450)
                time.sleep(0.08)
        else:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass
    username = os.environ.get("USERNAME", "")
    if username:
        try:
            subprocess.run(
                ["msg", username, f"{title}: {message}"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except Exception:
            pass


def _endpoint_urls(base_url: str, session_id: str) -> dict[str, str]:
    base = base_url.rstrip("/")
    quoted = urllib.parse.quote(session_id, safe="")
    return {
        "health": f"{base}/v1/mobile/health",
        "runtime_trace": f"{base}/v1/mobile/runtime/trace/v3?session_id={quoted}",
        "live_state": f"{base}/v1/mobile/live/state/v3/{quoted}?mode=CLEAN_LIVE&compact=1",
        "session": f"{base}/v1/mobile/window-tracker/sessions/{quoted}",
        "performance": f"{base}/v1/mobile/performance/trace/v3/{quoted}",
        "model_latest": f"{base}/v1/mobile/model-council/latest?session_id={quoted}",
        "study_latest": f"{base}/v1/mobile/model-council/sessions/{quoted}/study/latest",
        "execution_latest": f"{base}/v1/mobile/model-council/sessions/{quoted}/execution/latest",
        "floating_state": f"{base}/v1/mobile/floating/sessions/{quoted}/state",
        "frontend_heartbeat": f"{base}/v1/mobile/frontend/heartbeat/v3?session_id={quoted}",
    }


def _latency_row(at_epoch: float, endpoint: str, status: int, latency_ms: float, error: str) -> dict[str, object]:
    return {
        "at_epoch": at_epoch,
        "at_utc": _utc_now(),
        "endpoint": endpoint,
        "http_status": status,
        "latency_ms": round(latency_ms, 3),
        "error": error,
    }


def _source_lock_status(live: Mapping[str, Any], runtime_trace: Mapping[str, Any]) -> dict[str, object]:
    broker_source = _mapping(live.get("broker_source"))
    broker_source_lock = _mapping(live.get("broker_source_lock"))
    nodes = _mapping(_mapping(runtime_trace.get("dataflow_contract_trace")).get("nodes"))
    top_level_lock_id = _text(live.get("broker_source_lock_id") or live.get("source_lock_id"))
    top_level_transform_id = _text(live.get("chart_transform_id"))
    top_level_status = _upper(live.get("broker_source_lock_status") or live.get("source_lock_status"), "")
    wrong_surface = bool(broker_source.get("wrong_surface") or broker_source_lock.get("wrong_surface") or live.get("wrong_surface"))
    identity_valid = bool(top_level_lock_id and top_level_transform_id and not wrong_surface)
    status = _upper(
        broker_source_lock.get("status")
        or broker_source.get("status")
        or nodes.get("BrokerSourceLockV3")
        or top_level_status
        or ("VALID" if identity_valid else ""),
        "UNKNOWN",
    )
    valid = bool(
        broker_source_lock.get("valid") is True
        or broker_source.get("valid") is True
        or status in {"PASS", "VALID", "LOCKED"}
        or identity_valid
    )
    return {
        "status": status,
        "valid": valid,
        "lock_id": _text(
            broker_source.get("lock_id")
            or broker_source_lock.get("lock_id")
            or broker_source_lock.get("broker_source_lock_id")
            or top_level_lock_id
        ),
        "wrong_surface": wrong_surface,
        "reason": _text(
            broker_source_lock.get("reason")
            or broker_source.get("reason")
            or ("live_state_source_lock_identity" if identity_valid else "")
        ),
    }


def _min_positive(left: float, right: float) -> float:
    if left <= 0.0:
        return right
    if right <= 0.0:
        return left
    return min(left, right)


def _timing_status(
    live: Mapping[str, Any],
    performance: Mapping[str, Any],
    direct_display: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    live_timing = _mapping(live.get("frame_timing_trace_v3"))
    performance_timing = _mapping(performance.get("timing_trace"))
    display_state = _mapping(direct_display)
    frame_age_ms = _age_ms(live_timing.get("frame_age_ms"), performance_timing.get("frame_age_ms") or live.get("frame_age_ms"))
    display_age_ms = _epoch_age_ms(
        display_state.get("display_published_epoch")
        or display_state.get("last_display_published_epoch")
        or _mapping(display_state.get("display_fast_path_v3")).get("published_epoch")
        or live.get("display_published_epoch")
        or live.get("last_display_published_epoch")
        or live_timing.get("display_published_epoch_ms")
        or live_timing.get("display_published_epoch")
    )
    if display_age_ms > 0.0:
        frame_age_ms = _min_positive(frame_age_ms, display_age_ms)
    overlay_age_ms = _age_ms(
        live_timing.get("overlay_age_ms"),
        performance_timing.get("overlay_age_ms") or live.get("overlay_age_ms"),
    )
    model_vote_age_ms = _age_ms(live_timing.get("model_vote_age_ms"), performance_timing.get("model_vote_age_ms"))
    direct_frame_id = _int(display_state.get("frame_index") or display_state.get("chart_frame_id"), 0)
    if display_age_ms > 0.0 and direct_frame_id > 0:
        if _int(display_state.get("overlay_frame_id"), 0) == direct_frame_id:
            overlay_age_ms = _min_positive(overlay_age_ms, display_age_ms)
        if _int(display_state.get("model_vote_frame_id"), 0) == direct_frame_id:
            model_vote_age_ms = _min_positive(model_vote_age_ms, display_age_ms)
    packet_age_ms = _age_ms(live_timing.get("packet_age_ms"), performance_timing.get("packet_age_ms"))
    frontend_render_age_ms = _age_ms(live_timing.get("frontend_render_age_ms"), performance_timing.get("frontend_render_age_ms"))
    visual = _mapping(performance.get("visual_health"))
    return {
        "frame_age_ms": frame_age_ms,
        "display_age_ms": display_age_ms,
        "overlay_age_ms": overlay_age_ms,
        "model_vote_age_ms": model_vote_age_ms,
        "packet_age_ms": packet_age_ms,
        "frontend_render_age_ms": frontend_render_age_ms,
        "direct_display_age_ms": display_age_ms,
        "timing_missing": bool(frame_age_ms <= 0.0 or overlay_age_ms <= 0.0),
        "stale_status": _text(live_timing.get("stale_status") or performance_timing.get("stale_status") or visual.get("status"), "UNKNOWN"),
        "stale_flags": _sequence(live_timing.get("stale_flags") or performance_timing.get("stale_flags") or visual.get("stale_flags")),
    }


def timing_status_for_certification(
    live: Mapping[str, Any],
    performance: Mapping[str, Any],
    direct_display: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    return _timing_status(live, performance, direct_display)


def source_lock_status_for_certification(live: Mapping[str, Any], runtime_trace: Mapping[str, Any]) -> dict[str, object]:
    return _source_lock_status(live, runtime_trace)


def _sequence_status(live: Mapping[str, Any], runtime_trace: Mapping[str, Any]) -> dict[str, object]:
    tracking = _mapping(live.get("tracking_summary"))
    sequence_context = _mapping(tracking.get("sequence_context_v3") or tracking.get("sequence_context"))
    gates = _mapping(runtime_trace.get("certification_gates"))
    sequence_gate = _mapping(gates.get("sequence_context"))
    evidence = _mapping(sequence_gate.get("evidence"))
    return {
        "sequence_id": _text(sequence_context.get("sequence_id") or evidence.get("sequence_id")),
        "status": _text(sequence_context.get("status") or sequence_context.get("sequence_status") or evidence.get("status"), "UNKNOWN"),
        "phase": _text(sequence_context.get("phase") or evidence.get("phase")),
        "ready": bool(sequence_context.get("ready") is True or sequence_context.get("complete") is True or sequence_gate.get("passed") is True),
        "sequence_length": _int(sequence_context.get("sequence_length") or evidence.get("sequence_length"), 0),
        "confidence": _float(sequence_context.get("confidence") or sequence_context.get("sequence_confidence"), 0.0),
    }


def _model_status(live: Mapping[str, Any], performance: Mapping[str, Any], runtime_trace: Mapping[str, Any]) -> dict[str, object]:
    model_state = _mapping(live.get("model_state") or performance.get("model_state"))
    gates = _mapping(runtime_trace.get("certification_gates"))
    warm_state = _mapping(gates.get("model_warm_state"))
    models_awake = _int(model_state.get("models_awake") or model_state.get("awake"), 0)
    models_total = _int(model_state.get("models_total") or model_state.get("total"), 0)
    return {
        "models_awake": models_awake,
        "models_total": models_total,
        "all_required_models_awake": bool(
            model_state.get("all_required_models_awake") is True
            or warm_state.get("passed") is True
            or (models_total >= 7 and models_awake >= models_total)
        ),
        "queue_depth": _int(model_state.get("queue_depth"), 0),
    }


def model_status_for_certification(
    live: Mapping[str, Any],
    performance: Mapping[str, Any],
    runtime_trace: Mapping[str, Any],
) -> dict[str, object]:
    return _model_status(live, performance, runtime_trace)


def _frontend_status(heartbeat: Mapping[str, Any]) -> dict[str, object]:
    received_ms = _float(heartbeat.get("received_at_ms"), 0.0)
    age_ms = max(0.0, time.time() * 1000.0 - received_ms) if received_ms > 0 else 0.0
    render_size = _mapping(heartbeat.get("render_size"))
    return {
        "status": _text(heartbeat.get("status"), "UNKNOWN"),
        "overlay_mode": _text(heartbeat.get("overlay_mode")),
        "age_ms": round(age_ms, 3),
        "document_hidden": bool(heartbeat.get("document_hidden") is True or _text(heartbeat.get("page_visibility")).lower() == "hidden"),
        "rendered_frame_id": _int(heartbeat.get("rendered_frame_id"), 0),
        "display_frame_id": _int(heartbeat.get("display_frame_id"), 0),
        "overlay_render_frame_id": _int(heartbeat.get("overlay_render_frame_id"), 0),
        "overlay_count": _int(heartbeat.get("overlay_count"), 0),
        "visible_overlay_count": _int(heartbeat.get("visible_overlay_count"), 0),
        "render_width": _int(render_size.get("width") or render_size.get("canvas_width"), 0),
        "render_height": _int(render_size.get("height") or render_size.get("canvas_height"), 0),
    }


def _file_mtime_epoch(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _mt4_status(*, stale_sec: float) -> dict[str, object]:
    root = _common_files_dir() / "PhoenixGuard"
    status_path = root / "mt4_bridge_status.json"
    command_path = root / "mt4_execution_command.json"
    status = _read_json_file(root / "mt4_bridge_status.json")
    command = _read_json_file(root / "mt4_execution_command.json")
    status_written = _float(status.get("written_epoch"), 0.0)
    command_written = _float(command.get("bridge_written_epoch") or command.get("written_epoch"), 0.0)
    newest_epoch = max(status_written, command_written, _file_mtime_epoch(status_path), _file_mtime_epoch(command_path))
    age_sec = max(0.0, time.time() - newest_epoch) if newest_epoch > 0.0 else 0.0
    status_exists = status_path.exists()
    command_exists = command_path.exists()
    bridge_status = _text(status.get("bridge_status") or status.get("status"), "MISSING")
    fresh = bool(status_exists and command_exists and newest_epoch > 0.0 and age_sec <= max(1.0, stale_sec))
    return {
        "common_dir": str(root),
        "bridge_status": bridge_status,
        "bridge_fresh": fresh,
        "bridge_age_sec": round(age_sec, 3),
        "bridge_stale_sec": round(max(1.0, stale_sec), 3),
        "status_file_exists": status_exists,
        "command_file_exists": command_exists,
        "bridge_sequence": _int(status.get("bridge_sequence"), 0),
        "status_written_epoch": status_written,
        "command_written_epoch": command_written,
        "packet_id": _text(command.get("packet_id")),
        "command_schema": _text(command.get("schema_version")),
        "entry_eligible": bool(_mapping(command.get("entry_eligibility")).get("eligible") is True),
        "allowance_package_type": _text(_mapping(command.get("allowance_package")).get("package_type")),
    }


def _execution_allowed(execution_payload: Mapping[str, Any]) -> tuple[bool, dict[str, object]]:
    if execution_payload.get("schema_version") != "PG_EXECUTION_PACKET_V3":
        return False, {"reason": "no_pg_execution_packet"}
    execution = _mapping(execution_payload.get("execution"))
    permission = _mapping(execution_payload.get("trade_permission"))
    allowance = _mapping(execution_payload.get("allowance_package"))
    package_type = _upper(allowance.get("package_type"))
    state = _upper(execution.get("state"))
    side = _upper(execution.get("side"))
    allowed = bool(
        state == "EXECUTABLE"
        and side in {"BUY", "SELL"}
        and package_type in ALLOWED_PACKAGE_TYPES
        and allowance.get("execution_authority") == "PG_EXECUTION_PACKET_V3"
        and allowance.get("accepted") is True
        and allowance.get("execution_ready") is True
        and permission.get("executable_allowed") is True
    )
    return allowed, {
        "packet_id": _text(execution_payload.get("packet_id")),
        "side": side,
        "state": state,
        "package_type": package_type,
        "symbol": _text(execution_payload.get("symbol")),
        "timeframe": _text(execution_payload.get("timeframe")),
        "confidence": _float(execution_payload.get("confidence") or execution_payload.get("final_confidence"), 0.0),
        "reason": "allowed" if allowed else "execution_packet_not_allowed",
    }


def _bridge_execution_allowed(mt4_payload: Mapping[str, object]) -> tuple[bool, dict[str, object]]:
    package_type = _upper(mt4_payload.get("allowance_package_type"))
    packet_id = _text(mt4_payload.get("packet_id"))
    allowed = bool(mt4_payload.get("entry_eligible") is True and package_type in ALLOWED_PACKAGE_TYPES and packet_id)
    return allowed, {
        "packet_id": packet_id,
        "side": "",
        "state": "BRIDGE_ALLOWED" if allowed else _upper(mt4_payload.get("bridge_status"), "UNKNOWN"),
        "package_type": package_type,
        "symbol": "",
        "timeframe": "",
        "confidence": 0.0,
        "reason": "bridge_allowed" if allowed else "bridge_packet_not_allowed",
    }


def _tail_text(path: Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    return data[-max(1, int(limit)) :].decode("utf-8", errors="replace")


def _dashboard_capture_command(base_url: str, session_id: str, out_dir: Path, *, timeout_sec: float) -> list[str]:
    return [
        _python_executable(),
        "Backend/tools/capture_dashboard_visual_v3.py",
        "--base-url",
        base_url,
        "--session",
        session_id,
        "--out-dir",
        str(out_dir),
        "--timeout",
        f"{max(1.0, float(timeout_sec)):.3f}",
        "--soft",
    ]


def _overlay_modes_capture_command(base_url: str, session_id: str, out_dir: Path, *, timeout_sec: float) -> list[str]:
    return [
        _python_executable(),
        "Backend/tools/capture_overlay_mode_screenshots_v3.py",
        "--base-url",
        base_url,
        "--session",
        session_id,
        "--modes",
        "CLEAN_LIVE,SUPPLY_DEMAND,TRENDLINES",
        "--out",
        str(out_dir),
        "--timeout",
        f"{max(1.0, float(timeout_sec)):.3f}",
    ]


def _run_capture(command: list[str], out_dir: Path, log_path: Path, *, timeout_sec: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = _now_epoch()
    try:
        result = subprocess.run(
            command,
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=max(5.0, float(timeout_sec)),
            check=False,
        )
        returncode = int(result.returncode)
        stdout_tail = result.stdout[-2000:]
        stderr_tail = result.stderr[-2000:]
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = -1
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        stdout_tail = stdout[-2000:]
        stderr_tail = (stderr or f"capture_timeout_after_{timeout_sec:.1f}s")[-2000:]
        timed_out = True
    _append_jsonl(
        log_path,
        {
            "at_epoch": started,
            "at_utc": _utc_now(),
            "command": command,
            "returncode": returncode,
            "timed_out": timed_out,
            "timeout_sec": round(float(timeout_sec), 3),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        },
    )


def _start_capture_job(
    kind: str,
    command: list[str],
    out_dir: Path,
    log_path: Path,
    *,
    timeout_sec: float,
) -> CaptureJob:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir = out_dir / "_capture_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    started = _now_epoch()
    stem = f"{kind}_{int(started * 1000)}"
    stdout_path = log_dir / f"{stem}.stdout.txt"
    stderr_path = log_dir / f"{stem}.stderr.txt"
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=str(_repo_root()),
            stdout=stdout_file,
            stderr=stderr_file,
        )
    job = CaptureJob(
        kind=kind,
        started_epoch=started,
        command=list(command),
        out_dir=out_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout_sec=max(0.001, float(timeout_sec)),
        process=process,
    )
    _append_jsonl(
        log_path,
        {
            "at_epoch": started,
            "at_utc": _utc_now(),
            "event": "capture_started",
            "kind": kind,
            "command": command,
            "pid": process.pid,
            "timeout_sec": round(job.timeout_sec, 3),
            "out_dir": str(out_dir),
        },
    )
    return job


def _poll_capture_jobs(jobs: Sequence[CaptureJob], log_path: Path) -> list[CaptureJob]:
    active: list[CaptureJob] = []
    now_epoch = _now_epoch()
    for job in jobs:
        returncode = job.process.poll()
        timed_out = False
        if returncode is None and now_epoch - job.started_epoch > job.timeout_sec:
            timed_out = True
            job.process.kill()
            try:
                returncode = job.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                returncode = -1
        if returncode is None:
            active.append(job)
            continue
        _append_jsonl(
            log_path,
            {
                "at_epoch": now_epoch,
                "at_utc": _utc_now(),
                "event": "capture_finished",
                "kind": job.kind,
                "command": job.command,
                "pid": job.process.pid,
                "returncode": int(returncode),
                "timed_out": timed_out,
                "timeout_sec": round(job.timeout_sec, 3),
                "duration_sec": round(now_epoch - job.started_epoch, 3),
                "stdout_tail": _tail_text(job.stdout_path),
                "stderr_tail": _tail_text(job.stderr_path),
                "out_dir": str(job.out_dir),
            },
        )
    return active


def start_capture_job_for_certification(
    kind: str,
    command: list[str],
    out_dir: Path,
    log_path: Path,
    *,
    timeout_sec: float,
) -> CaptureJob:
    return _start_capture_job(kind, command, out_dir, log_path, timeout_sec=timeout_sec)


def poll_capture_jobs_for_certification(jobs: Sequence[CaptureJob], log_path: Path) -> list[CaptureJob]:
    return _poll_capture_jobs(jobs, log_path)


def dashboard_capture_command_for_certification(
    base_url: str,
    session_id: str,
    out_dir: Path,
    *,
    timeout_sec: float,
) -> list[str]:
    return _dashboard_capture_command(base_url, session_id, out_dir, timeout_sec=timeout_sec)


def overlay_modes_capture_command_for_certification(
    base_url: str,
    session_id: str,
    out_dir: Path,
    *,
    timeout_sec: float,
) -> list[str]:
    return _overlay_modes_capture_command(base_url, session_id, out_dir, timeout_sec=timeout_sec)


def _capture_dashboard(base_url: str, session_id: str, out_dir: Path, log_path: Path, *, timeout_sec: float) -> None:
    _run_capture(
        _dashboard_capture_command(base_url, session_id, out_dir, timeout_sec=timeout_sec),
        out_dir,
        log_path,
        timeout_sec=timeout_sec,
    )


def _write_progress_report(
    path: Path,
    *,
    elapsed_sec: float,
    duration_sec: float,
    sample_count: int,
    stale_count: int,
    display_lag_count: int,
    frontend_gap_count: int,
    frontend_latency_warning_count: int,
    monitor_endpoint_warning_count: int,
    source_lock_fail_count: int,
    mt4_bridge_error_count: int,
    allowed_count: int,
    latest_summary: Mapping[str, object],
) -> None:
    remaining_sec = max(0.0, duration_sec - elapsed_sec)
    lines = [
        "# PhoenixGuard Final 10H Certification Progress",
        "",
        f"- Updated UTC: {_utc_now()}",
        f"- Elapsed minutes: {elapsed_sec / 60.0:.1f}",
        f"- Remaining minutes: {remaining_sec / 60.0:.1f}",
        f"- Samples: {sample_count}",
        f"- Stale market-truth events: {stale_count}",
        f"- Display lag events: {display_lag_count}",
        f"- Frontend heartbeat gaps: {frontend_gap_count}",
        f"- Frontend latency warnings: {frontend_latency_warning_count}",
        f"- Monitor endpoint warnings: {monitor_endpoint_warning_count}",
        f"- Source-lock failures: {source_lock_fail_count}",
        f"- MT4 bridge error samples: {mt4_bridge_error_count}",
        f"- Allowed package events: {allowed_count}",
        f"- Latest source lock: {_text(latest_summary.get('source_lock_status'), 'UNKNOWN')}",
        f"- Latest frame age ms: {latest_summary.get('frame_age_ms', 'UNKNOWN')}",
        f"- Latest overlay age ms: {latest_summary.get('overlay_age_ms', 'UNKNOWN')}",
        f"- Latest frontend visible overlays: {latest_summary.get('frontend_visible_overlay_count', 'UNKNOWN')}",
        f"- Latest MT4 bridge status: {_text(latest_summary.get('mt4_bridge_status'), 'UNKNOWN')}",
        f"- Latest MT4 bridge fresh: {latest_summary.get('mt4_bridge_fresh', 'UNKNOWN')}",
        f"- Latest MT4 bridge age sec: {latest_summary.get('mt4_bridge_age_sec', 'UNKNOWN')}",
    ]
    _write_text(path, "\n".join(lines) + "\n")


def _write_final_reports(out_dir: Path, reports_dir: Path, verdict: str, summary: Mapping[str, object]) -> None:
    final_payload = {"schema_version": SCHEMA_VERSION, "verdict": verdict, **dict(summary)}
    _write_json(out_dir / "final_summary.json", final_payload)
    _write_json(reports_dir / "FINAL_10H_PRODUCTION_CERTIFICATION_REPORT.json", final_payload)
    lines = [
        "# Final 10H Production Certification Report",
        "",
        f"- Verdict: {verdict}",
        f"- Completed UTC: {_utc_now()}",
        f"- Samples: {summary.get('sample_count')}",
        f"- Stale market-truth events: {summary.get('stale_event_count')}",
        f"- Display lag events: {summary.get('display_lag_count')}",
        f"- Frontend heartbeat gaps: {summary.get('frontend_gap_count')}",
        f"- Frontend latency warnings: {summary.get('frontend_latency_warning_count')}",
        f"- Monitor endpoint warnings: {summary.get('monitor_endpoint_warning_count')}",
        f"- Source-lock failures: {summary.get('source_lock_fail_count')}",
        f"- MT4 bridge stale samples: {summary.get('mt4_bridge_stale_count')}",
        f"- MT4 bridge missing samples: {summary.get('mt4_bridge_missing_count')}",
        f"- MT4 bridge error samples: {summary.get('mt4_bridge_error_count')}",
        f"- Allowed package events: {summary.get('allowed_package_count')}",
        f"- MT4 bridge status: {summary.get('last_mt4_bridge_status')}",
        f"- Output directory: {out_dir}",
    ]
    _write_text(reports_dir / "FINAL_10H_PRODUCTION_CERTIFICATION_REPORT.md", "\n".join(lines) + "\n")
    _write_text(
        reports_dir / "FINAL_STALE_DATA_ERADICATION_REPORT.md",
        "\n".join(
            [
                "# Final Stale Data Eradication Report",
                "",
                f"- Stale accepted as live truth: {summary.get('stale_accepted_as_live', 0)}",
                f"- Stale market-truth events observed: {summary.get('stale_event_count')}",
                f"- Display lag events observed: {summary.get('display_lag_count')}",
                f"- Frontend heartbeat gaps observed: {summary.get('frontend_gap_count')}",
                f"- Frontend latency warnings observed: {summary.get('frontend_latency_warning_count')}",
                f"- Monitor endpoint warnings observed: {summary.get('monitor_endpoint_warning_count')}",
                f"- Source-lock failures: {summary.get('source_lock_fail_count')}",
            ]
        )
        + "\n",
    )
    _write_text(
        reports_dir / "FINAL_MT4_BRIDGE_CERTIFICATION_REPORT.md",
        "\n".join(
            [
                "# Final MT4 Bridge Certification Report",
                "",
                f"- Last bridge status: {summary.get('last_mt4_bridge_status')}",
                f"- Bridge stale samples: {summary.get('mt4_bridge_stale_count')}",
                f"- Bridge missing samples: {summary.get('mt4_bridge_missing_count')}",
                f"- Bridge error samples: {summary.get('mt4_bridge_error_count')}",
                f"- Allowed packages observed: {summary.get('allowed_package_count')}",
                f"- Bridge packet records: {summary.get('mt4_packet_record_count')}",
            ]
        )
        + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PhoenixGuard V3 final 10-hour production certification monitor.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--duration-sec", type=float, default=36_000.0)
    parser.add_argument("--sample-sec", type=float, default=15.0)
    parser.add_argument("--diagnostic-sec", type=float, default=300.0)
    parser.add_argument("--timeout-sec", type=float, default=20.0)
    parser.add_argument("--screenshot-sec", type=float, default=900.0)
    parser.add_argument("--capture-timeout-sec", type=float, default=180.0)
    parser.add_argument("--update-sec", type=float, default=1800.0)
    parser.add_argument("--frontend-heartbeat-max-age-ms", type=float, default=45_000.0)
    parser.add_argument("--display-freshness-max-age-ms", type=float, default=30_000.0)
    parser.add_argument("--authority-frame-max-age-ms", type=float, default=30_000.0)
    parser.add_argument("--authority-model-max-age-ms", type=float, default=30_000.0)
    parser.add_argument("--mt4-bridge-stale-sec", type=float, default=45.0)
    parser.add_argument("--out-dir", default=".codex_runtime/10h_cert")
    parser.add_argument("--no-screenshots", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    reports_dir = Path("reports")
    screenshots_dir = out_dir / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    urls = _endpoint_urls(args.base_url, args.session_id)
    start = _now_epoch()
    next_screenshot = start + max(60.0, float(args.screenshot_sec))
    next_update = start
    next_diagnostic = start + max(float(args.sample_sec), float(args.diagnostic_sec))
    cached_payloads: dict[str, dict[str, Any]] = {}
    cached_endpoint_status: dict[str, int] = {}
    sample_count = 0
    stale_count = 0
    stale_accepted_as_live = 0
    display_lag_count = 0
    frontend_gap_count = 0
    frontend_latency_warning_count = 0
    monitor_endpoint_warning_count = 0
    source_lock_fail_count = 0
    mt4_bridge_stale_count = 0
    mt4_bridge_missing_count = 0
    mt4_bridge_error_count = 0
    allowed_seen: set[str] = set()
    mt4_packet_seen: set[str] = set()
    progression: dict[str, dict[str, Any]] = {}
    latest_summary: dict[str, object] = {}
    capture_log = out_dir / "screenshot_capture_log.jsonl"
    periodic_capture_jobs: list[CaptureJob] = []
    periodic_capture_queue: list[tuple[str, list[str], Path, float]] = []

    _write_json(
        out_dir / "run_metadata.json",
        {
            "schema_version": SCHEMA_VERSION,
            "session_id": args.session_id,
            "base_url": args.base_url,
            "duration_sec": args.duration_sec,
            "started_epoch": start,
            "started_utc": _utc_now(),
            "python": _python_executable(),
        },
    )
    _notify("PhoenixGuard 10H certification started", f"Session {args.session_id}; monitoring every {args.sample_sec:g}s", loud=False)

    while True:
        loop_started = _now_epoch()
        periodic_capture_jobs = _poll_capture_jobs(periodic_capture_jobs, capture_log)
        if not periodic_capture_jobs and periodic_capture_queue:
            capture_kind, capture_command, capture_out_dir, capture_timeout_sec = periodic_capture_queue.pop(0)
            periodic_capture_jobs = [
                _start_capture_job(
                    capture_kind,
                    capture_command,
                    capture_out_dir,
                    capture_log,
                    timeout_sec=capture_timeout_sec,
                )
            ]
        elapsed = loop_started - start
        if elapsed >= args.duration_sec:
            break
        sample_count += 1
        diagnostic_due = loop_started >= next_diagnostic
        requested_endpoints = [
            name for name in urls if diagnostic_due or name not in DIAGNOSTIC_ENDPOINTS or name not in cached_payloads
        ]
        endpoint_results = _fetch_endpoint_results(urls, requested_endpoints, float(args.timeout_sec))
        for name, result in endpoint_results.items():
            cached_endpoint_status[name] = int(result[0])
            cached_payloads[name] = result[1]
        if diagnostic_due:
            next_diagnostic = loop_started + max(float(args.sample_sec), float(args.diagnostic_sec))
        payloads = {name: cached_payloads.get(name, {}) for name in urls}
        live = payloads["live_state"]
        runtime_trace = payloads["runtime_trace"]
        performance = payloads["performance"]
        heartbeat = payloads["frontend_heartbeat"]
        execution = payloads["execution_latest"]
        direct_display = _direct_display_state(args.session_id)
        direct_display_summary = _direct_display_summary(direct_display)
        source_lock = _source_lock_status(live, runtime_trace)
        timing = _timing_status(live, performance, direct_display)
        sequence_status = _sequence_status(live, runtime_trace)
        model_status = _model_status(live, performance, runtime_trace)
        frontend = _frontend_status(heartbeat)
        mt4 = _mt4_status(stale_sec=float(args.mt4_bridge_stale_sec))
        endpoint_allowed, endpoint_allowed_meta = _execution_allowed(execution)
        bridge_allowed, bridge_allowed_meta = _bridge_execution_allowed(mt4)
        allowed = endpoint_allowed or bridge_allowed
        allowed_meta = endpoint_allowed_meta if endpoint_allowed else bridge_allowed_meta

        for endpoint_name, (status, _payload, error, latency_ms) in endpoint_results.items():
            _append_jsonl(out_dir / "api_latency.jsonl", _latency_row(loop_started, endpoint_name, status, latency_ms, error))
        endpoint_warning_reasons = [
            f"{endpoint_name}_request_failed"
            for endpoint_name, (status, _payload, _error, _latency_ms) in endpoint_results.items()
            if int(status) == 0
        ]
        if endpoint_warning_reasons:
            monitor_endpoint_warning_count += 1
            _append_jsonl(
                out_dir / "monitor_endpoint_warnings.jsonl",
                {
                    "at_epoch": loop_started,
                    "at_utc": _utc_now(),
                    "reasons": endpoint_warning_reasons,
                    "endpoint_status": {name: int(result[0]) for name, result in endpoint_results.items()},
                },
            )
        _append_jsonl(out_dir / "source_lock.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), **source_lock})
        _append_jsonl(out_dir / "sequence_context.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), **sequence_status})
        _append_jsonl(out_dir / "frontend_latency.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), **frontend})
        _append_jsonl(out_dir / "mt4_bridge_acks.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), **mt4})
        if _text(mt4.get("packet_id")) and _text(mt4.get("packet_id")) not in mt4_packet_seen:
            mt4_packet_seen.add(_text(mt4.get("packet_id")))
            _append_jsonl(out_dir / "mt4_bridge_packets.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), **mt4})

        stale_reasons: list[str] = []
        display_lag_reasons: list[str] = []
        frontend_gap_reasons: list[str] = []
        live_state_current_status = int(endpoint_results.get("live_state", (cached_endpoint_status.get("live_state", 0), {}, "", 0.0))[0])
        live_state_current_ok = live_state_current_status == 200
        display_max_age_ms = max(1000.0, float(args.display_freshness_max_age_ms))
        authority_frame_max_age_ms = max(500.0, float(args.authority_frame_max_age_ms))
        authority_model_max_age_ms = max(500.0, float(args.authority_model_max_age_ms))
        if live_state_current_ok:
            if _float(timing["frame_age_ms"]) > display_max_age_ms:
                display_lag_reasons.append(f"frame_age_gt_{display_max_age_ms:.0f}ms")
            if _float(timing["overlay_age_ms"]) > display_max_age_ms:
                display_lag_reasons.append(f"overlay_age_gt_{display_max_age_ms:.0f}ms")
            if _float(timing["model_vote_age_ms"]) > display_max_age_ms:
                display_lag_reasons.append(f"model_vote_age_gt_{display_max_age_ms:.0f}ms")
            if allowed:
                if _float(timing["frame_age_ms"]) > authority_frame_max_age_ms:
                    stale_reasons.append(f"allowed_packet_frame_age_gt_{authority_frame_max_age_ms:.0f}ms")
                if _float(timing["overlay_age_ms"]) > authority_frame_max_age_ms:
                    stale_reasons.append(f"allowed_packet_overlay_age_gt_{authority_frame_max_age_ms:.0f}ms")
                if _float(timing["model_vote_age_ms"]) > authority_model_max_age_ms:
                    stale_reasons.append(f"allowed_packet_model_vote_age_gt_{authority_model_max_age_ms:.0f}ms")
            if bool(timing.get("timing_missing")):
                if allowed:
                    stale_reasons.append("allowed_packet_missing_frame_or_overlay_timing")
                else:
                    display_lag_reasons.append("missing_frame_or_overlay_timing")
        elif live_state_current_status == 0:
            monitor_endpoint_warning_count += 1
            _append_jsonl(
                out_dir / "monitor_endpoint_warnings.jsonl",
                {
                    "at_epoch": loop_started,
                    "at_utc": _utc_now(),
                    "reasons": ["live_state_unavailable_skip_truth_stale_eval"],
                    "endpoint_status": {name: int(result[0]) for name, result in endpoint_results.items()},
                },
            )
        frontend_heartbeat_age_ms = _float(frontend["age_ms"])
        frontend_visible_count = _int(frontend.get("visible_overlay_count"), 0)
        frontend_alive = _text(frontend.get("status")).upper() == "ALIVE"
        backend_fresh_for_frontend = bool(
            not timing.get("timing_missing")
            and _float(timing["frame_age_ms"]) <= display_max_age_ms
            and _float(timing["overlay_age_ms"]) <= display_max_age_ms
            and _float(timing["model_vote_age_ms"]) <= display_max_age_ms
        )
        frontend_max_age_ms = max(1000.0, float(args.frontend_heartbeat_max_age_ms))
        frontend_hidden = bool(frontend.get("document_hidden") is True)
        if frontend_heartbeat_age_ms > frontend_max_age_ms:
            if frontend_hidden and backend_fresh_for_frontend:
                _append_jsonl(
                    out_dir / "frontend_hidden_heartbeat.jsonl",
                    {
                        "at_epoch": loop_started,
                        "at_utc": _utc_now(),
                        "reason": "dashboard_hidden_backend_fresh",
                        "allowed_packet_present": allowed,
                        "source_lock": source_lock,
                        "timing": timing,
                        "frontend": frontend,
                    },
                )
            elif frontend_alive and frontend_visible_count > 0 and backend_fresh_for_frontend:
                frontend_latency_warning_count += 1
                _append_jsonl(
                    out_dir / "frontend_latency_warnings.jsonl",
                    {
                        "at_epoch": loop_started,
                        "at_utc": _utc_now(),
                        "reason": f"frontend_heartbeat_age_gt_{frontend_max_age_ms:.0f}ms_but_visible_and_backend_fresh",
                        "allowed_packet_present": allowed,
                        "source_lock": source_lock,
                        "timing": timing,
                        "frontend": frontend,
                    },
                )
            else:
                frontend_gap_reasons.append(f"frontend_heartbeat_age_gt_{frontend_max_age_ms:.0f}ms")
        if not bool(mt4.get("status_file_exists")) or not bool(mt4.get("command_file_exists")):
            mt4_bridge_missing_count += 1
            _append_jsonl(out_dir / "mt4_bridge_stale.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), "reason": "mt4_bridge_file_missing", **mt4})
        elif _upper(mt4.get("bridge_status")) == "BRIDGE_ERROR":
            mt4_bridge_error_count += 1
            _append_jsonl(out_dir / "mt4_bridge_errors.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), "reason": "mt4_bridge_error_status", **mt4})
        elif not bool(mt4.get("bridge_fresh")):
            mt4_bridge_stale_count += 1
            _append_jsonl(out_dir / "mt4_bridge_stale.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), "reason": "mt4_bridge_status_stale", **mt4})
        if live_state_current_ok and not bool(source_lock.get("valid")):
            stale_reasons.append("source_lock_not_valid")
            source_lock_fail_count += 1
        monitor_truth_status = "PASS"
        monitor_truth_reasons: list[str] = []
        if stale_reasons:
            monitor_truth_status = "REJECT"
            monitor_truth_reasons = list(stale_reasons)
        elif frontend_gap_reasons:
            monitor_truth_status = "FRONTEND_GAP"
            monitor_truth_reasons = list(frontend_gap_reasons)
        elif display_lag_reasons:
            monitor_truth_status = "DISPLAY_LAG"
            monitor_truth_reasons = list(display_lag_reasons)
        elif _text(timing.get("stale_status")).upper() in {"STALE", "REJECT"} and not allowed:
            monitor_truth_status = "BACKGROUND_CADENCE_WAIT"
            monitor_truth_reasons = ["raw_backend_stale_flag_without_allowed_execution_packet"]
        timing = {
            **timing,
            "monitor_truth_status": monitor_truth_status,
            "monitor_truth_reasons": monitor_truth_reasons,
            "allowed_packet_present": allowed,
        }
        _append_jsonl(out_dir / "model_freshness.jsonl", {"at_epoch": loop_started, "at_utc": _utc_now(), **model_status, **timing})
        if frontend_gap_reasons:
            frontend_gap_count += 1
            _append_jsonl(
                out_dir / "frontend_gap_events.jsonl",
                {
                    "at_epoch": loop_started,
                    "at_utc": _utc_now(),
                    "reasons": frontend_gap_reasons,
                    "allowed_packet_present": allowed,
                    "source_lock": source_lock,
                    "timing": timing,
                    "frontend": frontend,
                },
            )
        if display_lag_reasons:
            display_lag_count += 1
            _append_jsonl(
                out_dir / "display_lag_events.jsonl",
                {
                    "at_epoch": loop_started,
                    "at_utc": _utc_now(),
                    "reasons": display_lag_reasons,
                    "allowed_packet_present": allowed,
                    "source_lock": source_lock,
                    "timing": timing,
                    "frontend": frontend,
                },
            )
        if stale_reasons:
            stale_count += 1
            if allowed:
                stale_accepted_as_live += 1
            _append_jsonl(
                out_dir / "stale_events.jsonl",
                {
                    "at_epoch": loop_started,
                    "at_utc": _utc_now(),
                    "reasons": stale_reasons,
                    "allowed_packet_present": allowed,
                    "source_lock": source_lock,
                    "timing": timing,
                    "frontend": frontend,
                },
            )

        if allowed:
            packet_id = _text(allowed_meta.get("packet_id"), f"packet_{sample_count}")
            if packet_id not in allowed_seen:
                allowed_seen.add(packet_id)
                progression[packet_id] = {"first_seen_epoch": loop_started, "captured_horizons": []}
                _notify(
                    "PHOENIXGUARD ALLOWED PACKAGE",
                    f"{allowed_meta.get('side')} {allowed_meta.get('package_type')} {allowed_meta.get('symbol')} {allowed_meta.get('timeframe')} packet={packet_id}",
                    loud=True,
                )
                package_dir = screenshots_dir / f"package_{packet_id}" / "before"
                if not args.no_screenshots:
                    _capture_dashboard(args.base_url, args.session_id, package_dir, capture_log, timeout_sec=args.capture_timeout_sec)
                _append_jsonl(
                    out_dir / "package_progression.jsonl",
                    {"at_epoch": loop_started, "at_utc": _utc_now(), "stage": "before", **allowed_meta, "mt4": mt4, "timing": timing},
                )

        for packet_id, state in list(progression.items()):
            first_seen = _float(state.get("first_seen_epoch"), loop_started)
            captured = set(_sequence(state.get("captured_horizons")))
            for horizon in PROGRESSION_HORIZONS_SEC:
                if horizon in captured or loop_started - first_seen < float(horizon):
                    continue
                captured.add(horizon)
                state["captured_horizons"] = sorted(captured)
                stage = f"T+{horizon}s"
                if not args.no_screenshots:
                    _capture_dashboard(
                        args.base_url,
                        args.session_id,
                        screenshots_dir / f"package_{packet_id}" / stage,
                        capture_log,
                        timeout_sec=args.capture_timeout_sec,
                    )
                _append_jsonl(
                    out_dir / "package_progression.jsonl",
                    {"at_epoch": loop_started, "at_utc": _utc_now(), "packet_id": packet_id, "stage": stage, "mt4": mt4, "timing": timing},
                )

        latest_summary = {
            "source_lock_status": source_lock.get("status"),
            "frame_age_ms": timing.get("frame_age_ms"),
            "overlay_age_ms": timing.get("overlay_age_ms"),
            "monitor_truth_status": timing.get("monitor_truth_status"),
            "frontend_visible_overlay_count": frontend.get("visible_overlay_count"),
            "mt4_bridge_status": mt4.get("bridge_status"),
            "mt4_bridge_fresh": mt4.get("bridge_fresh"),
            "mt4_bridge_age_sec": mt4.get("bridge_age_sec"),
        }
        sample_row: dict[str, object] = {
            "at_epoch": loop_started,
            "at_utc": _utc_now(),
            "sample": sample_count,
            "source_lock": source_lock,
            "timing": timing,
            "sequence": sequence_status,
            "model": model_status,
            "frontend": frontend,
            "direct_display": direct_display_summary,
            "mt4": mt4,
            "allowed": allowed,
            "allowed_meta": allowed_meta,
            "endpoint_status": dict(cached_endpoint_status),
            "sampled_endpoints": requested_endpoints,
            "cached_diagnostic_endpoints": sorted(name for name in DIAGNOSTIC_ENDPOINTS if name not in requested_endpoints),
        }
        _append_jsonl(out_dir / "samples.jsonl", sample_row)

        if not args.no_screenshots and loop_started >= next_screenshot:
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(loop_started))
            periodic_dir = screenshots_dir / f"periodic_{stamp}"
            periodic_timeout_sec = min(max(5.0, float(args.sample_sec) * 2.0), max(5.0, float(args.capture_timeout_sec)))
            if periodic_capture_jobs or periodic_capture_queue:
                _append_jsonl(
                    capture_log,
                    {
                        "at_epoch": loop_started,
                        "at_utc": _utc_now(),
                        "event": "periodic_capture_skipped_active_job",
                        "active_jobs": len(periodic_capture_jobs),
                        "queued_jobs": len(periodic_capture_queue),
                        "next_screenshot_sec": max(60.0, float(args.screenshot_sec)),
                    },
                )
            else:
                periodic_capture_queue.extend(
                    [
                        (
                            "periodic_dashboard",
                            _dashboard_capture_command(
                                args.base_url,
                                args.session_id,
                                periodic_dir / "dashboard",
                                timeout_sec=periodic_timeout_sec,
                            ),
                            periodic_dir / "dashboard",
                            periodic_timeout_sec,
                        ),
                        (
                            "periodic_overlay_modes",
                            _overlay_modes_capture_command(
                                args.base_url,
                                args.session_id,
                                periodic_dir / "overlay_modes",
                                timeout_sec=periodic_timeout_sec,
                            ),
                            periodic_dir / "overlay_modes",
                            periodic_timeout_sec,
                        ),
                    ]
                )
                _append_jsonl(
                    capture_log,
                    {
                        "at_epoch": loop_started,
                        "at_utc": _utc_now(),
                        "event": "periodic_capture_queued",
                        "jobs": [kind for kind, _command, _out_dir, _timeout in periodic_capture_queue],
                        "timeout_sec": round(periodic_timeout_sec, 3),
                        "out_dir": str(periodic_dir),
                    },
                )
                capture_kind, capture_command, capture_out_dir, capture_timeout_sec = periodic_capture_queue.pop(0)
                periodic_capture_jobs = [
                    _start_capture_job(
                        capture_kind,
                        capture_command,
                        capture_out_dir,
                        capture_log,
                        timeout_sec=capture_timeout_sec,
                    )
                ]
            next_screenshot = loop_started + max(60.0, float(args.screenshot_sec))

        if periodic_capture_jobs or periodic_capture_queue:
            latest_summary = {
                **latest_summary,
                "periodic_capture_active": len(periodic_capture_jobs),
                "periodic_capture_queued": len(periodic_capture_queue),
            }

        if loop_started >= next_update:
            _write_progress_report(
                reports_dir / "FINAL_10H_PRODUCTION_CERTIFICATION_PROGRESS.md",
                elapsed_sec=elapsed,
                duration_sec=float(args.duration_sec),
                sample_count=sample_count,
                stale_count=stale_count,
                display_lag_count=display_lag_count,
                frontend_gap_count=frontend_gap_count,
                frontend_latency_warning_count=frontend_latency_warning_count,
                monitor_endpoint_warning_count=monitor_endpoint_warning_count,
                source_lock_fail_count=source_lock_fail_count,
                mt4_bridge_error_count=mt4_bridge_error_count,
                allowed_count=len(allowed_seen),
                latest_summary=latest_summary,
            )
            _notify(
                "PhoenixGuard 10H certification update",
                f"{elapsed / 60.0:.1f}m elapsed; samples={sample_count}; stale={stale_count}; display_lag={display_lag_count}; frontend_gaps={frontend_gap_count}; allowed={len(allowed_seen)}; bridge={mt4.get('bridge_status')}",
                loud=False,
            )
            next_update = loop_started + max(60.0, float(args.update_sec))

        _write_json(
            out_dir / "status.json",
            {
                "schema_version": SCHEMA_VERSION,
                "running": True,
                "sample_count": sample_count,
                "elapsed_sec": round(elapsed, 3),
                "remaining_sec": round(max(0.0, float(args.duration_sec) - elapsed), 3),
                "stale_event_count": stale_count,
                "display_lag_count": display_lag_count,
                "frontend_gap_count": frontend_gap_count,
                "frontend_latency_warning_count": frontend_latency_warning_count,
                "monitor_endpoint_warning_count": monitor_endpoint_warning_count,
                "source_lock_fail_count": source_lock_fail_count,
                "mt4_bridge_stale_count": mt4_bridge_stale_count,
                "mt4_bridge_missing_count": mt4_bridge_missing_count,
                "mt4_bridge_error_count": mt4_bridge_error_count,
                "allowed_package_count": len(allowed_seen),
                "periodic_capture_active": len(periodic_capture_jobs),
                "periodic_capture_queued": len(periodic_capture_queue),
                "latest_summary": latest_summary,
            },
        )
        sleep_for = max(0.1, float(args.sample_sec) - (time.time() - loop_started))
        time.sleep(sleep_for)

    duration = _now_epoch() - start
    periodic_capture_jobs = _poll_capture_jobs(periodic_capture_jobs, capture_log)
    verdict = "PASS_PRODUCTION_READY" if stale_accepted_as_live == 0 and source_lock_fail_count == 0 else "FAIL_SOURCE_LOCK"
    if verdict == "PASS_PRODUCTION_READY" and (
        mt4_bridge_missing_count > 0 or mt4_bridge_stale_count > 0 or mt4_bridge_error_count > 0
    ):
        verdict = "FAIL_MT4_BRIDGE"
    if not allowed_seen and verdict == "PASS_PRODUCTION_READY":
        verdict = "PASS_RUNTIME_ONLY_NO_ALLOWED_PACKAGES"
    summary = {
        "session_id": args.session_id,
        "base_url": args.base_url,
        "started_epoch": start,
        "completed_epoch": _now_epoch(),
        "duration_sec": round(duration, 3),
        "sample_count": sample_count,
        "stale_event_count": stale_count,
        "stale_accepted_as_live": stale_accepted_as_live,
        "display_lag_count": display_lag_count,
        "frontend_gap_count": frontend_gap_count,
        "frontend_latency_warning_count": frontend_latency_warning_count,
        "monitor_endpoint_warning_count": monitor_endpoint_warning_count,
        "source_lock_fail_count": source_lock_fail_count,
        "mt4_bridge_stale_count": mt4_bridge_stale_count,
        "mt4_bridge_missing_count": mt4_bridge_missing_count,
        "mt4_bridge_error_count": mt4_bridge_error_count,
        "allowed_package_count": len(allowed_seen),
        "mt4_packet_record_count": len(mt4_packet_seen),
        "last_mt4_bridge_status": _text(latest_summary.get("mt4_bridge_status"), "UNKNOWN"),
        "latest_summary": dict(latest_summary),
    }
    _write_json(out_dir / "status.json", {"schema_version": SCHEMA_VERSION, "running": False, "verdict": verdict, **summary})
    _write_final_reports(out_dir, reports_dir, verdict, summary)
    _notify(
        "PhoenixGuard 10H certification finished",
        f"Verdict {verdict}; allowed={len(allowed_seen)}; stale={stale_count}; display_lag={display_lag_count}; frontend_gaps={frontend_gap_count}",
        loud=True,
    )
    print(json.dumps({"verdict": verdict, "out_dir": str(out_dir), **summary}, indent=2, sort_keys=True))
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
