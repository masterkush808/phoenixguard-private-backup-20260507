#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, cast
from urllib import error, request

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

from _pg_bootstrap import ensure_project_paths

PROJECT_ROOT = ensure_project_paths()

def _default_live_runtime_dir(project_root: Path | None = None) -> Path:
    resolved_project_root = project_root or PROJECT_ROOT
    configured_runtime_dir = str(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or "").strip()
    if configured_runtime_dir:
        return Path(configured_runtime_dir).expanduser()
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser() / "PhoenixGuard" / "runtime" / "live"
    return resolved_project_root / "runtime" / "live"


def _runtime_lock_path() -> Path:
    configured = str(os.getenv("PHOENIXGUARD_RUNTIME_LOCK_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _default_live_runtime_dir() / "phoenixguard_stack.lock.json"


def _read_runtime_lock() -> dict[str, object]:
    lock_path = _runtime_lock_path()
    if not lock_path.exists():
        return {}
    try:
        raw = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return dict(cast(Mapping[str, object], raw))


def _resolve_stack_context(*, base_url: str | None, session_id: str | None) -> tuple[str, str]:
    bootstrap_default_base = "http://127.0.0.1:8793"
    bootstrap_default_session = "pocket-live-8788"
    resolved_base = (base_url or "").strip()
    resolved_session = (session_id or "").strip()

    if not resolved_base:
        env_host = str(os.getenv("PHOENIXGUARD_MOBILE_API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        env_port = str(os.getenv("PHOENIXGUARD_MOBILE_API_PORT") or "").strip()
        if env_port:
            resolved_base = f"http://{env_host}:{env_port}"
        else:
            lock = _read_runtime_lock()
            lock_base = str(lock.get("base_url") or "").strip()
            if lock_base:
                resolved_base = lock_base
    if not resolved_session:
        env_session = str(os.getenv("PHOENIXGUARD_TRACKER_SESSION_ID") or "").strip()
        if env_session:
            resolved_session = env_session
        else:
            lock = _read_runtime_lock()
            resolved_session = str(lock.get("session_id") or bootstrap_default_session).strip() or bootstrap_default_session
    return (resolved_base or bootstrap_default_base, resolved_session or bootstrap_default_session)


_DEFAULT_STACK_BASE_URL, _DEFAULT_STACK_SESSION_ID = _resolve_stack_context(base_url=None, session_id=None)
DEFAULT_BASE_URL = _DEFAULT_STACK_BASE_URL
DEFAULT_SESSION_ID = _DEFAULT_STACK_SESSION_ID
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_HEARTBEAT_SECONDS = 3.0
DEFAULT_LISTENER_RECONNECT_SECONDS = 1.0
def _default_calibration_dir(project_root: Path | None = None) -> Path:
    configured_dir = str(os.getenv("PHOENIXGUARD_CALIBRATION_DIR") or "").strip()
    if configured_dir:
        return Path(configured_dir).expanduser()
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser() / "PhoenixGuard" / "calibration"
    return (project_root or PROJECT_ROOT) / "data" / "calibration"


DEFAULT_TRIGGER_MANIFEST = _default_calibration_dir() / "trigger_calibration_manifest.json"
DEFAULT_CHART_FOCUS_SETTLE_SECONDS = 0.0
DEFAULT_PRE_CLICK_DELAY_SECONDS = 5.0
DEFAULT_INTER_CLICK_DELAY_SECONDS = 0.35
DEFAULT_POINTER_MOVE_DURATION_SECONDS = 0.35
# Listener delivery is the liveness contract.  A stable trend/bias may remain
# correct for hours, so an unchanged observation timestamp is not a stale
# stream.  Operators can opt into a positive age cap for polling diagnostics.
DEFAULT_MAX_SIGNAL_AGE_SECONDS = 0.0
DEFAULT_SIGNAL_SOURCE = "bias"
DEFAULT_FLIP_GUARD_SECONDS = 0.0
DEFAULT_MAX_TRADES_PER_CANDLE = 1
DEFAULT_MAX_TRADES_PER_SESSION = 8
DEFAULT_COOLDOWN_AFTER_TRADES = 0
DEFAULT_COOLDOWN_SECONDS = 0.0
DEFAULT_FRONTLINE_FRESHNESS_SECONDS = 180.0
HYBRID_READY_ENTRY_STATES = frozenset({"READY", "SNIPER_READY", "TRIGGER_READY", "TRIGGERED", "ACTIVE", "EXECUTE"})
HYBRID_WATCH_ENTRY_STATES = frozenset({"SNIPER_WATCH", "WAIT_FOR_SNIPER", "WAIT_FOR_TRIGGER", "WATCH"})
HYBRID_FAST_CONFIDENCE_FLOOR = 0.58
HIGH_FREQUENCY_CONFIDENCE_FLOOR = 0.58


class MissingCalibration(Exception):
    pass


class TradeRejected(Exception):
    def __init__(self, message: str, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class _BridgeTriggerState:
    def __init__(
        self,
        *,
        rearm_seconds: float = 0.0,
        flip_guard_seconds: float = DEFAULT_FLIP_GUARD_SECONDS,
        max_trades_per_candle: int = DEFAULT_MAX_TRADES_PER_CANDLE,
        max_trades_per_session: int = DEFAULT_MAX_TRADES_PER_SESSION,
        lock_side_per_candle: bool = False,
        cooldown_after_trades: int = DEFAULT_COOLDOWN_AFTER_TRADES,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._last_token: str = ""
        self._last_side: str = ""
        self._last_emit_epoch: float = 0.0
        self._rearm_seconds: float = max(0.0, float(rearm_seconds))
        self._flip_guard_seconds: float = max(0.0, float(flip_guard_seconds))
        self._max_trades_per_candle: int = max(0, int(max_trades_per_candle))
        self._max_trades_per_session: int = max(0, int(max_trades_per_session))
        self._lock_side_per_candle: bool = bool(lock_side_per_candle)
        self._active_candle_key: str = ""
        self._trades_this_candle: int = 0
        self._candle_side: str = ""
        self._cooldown_after_trades: int = max(0, int(cooldown_after_trades))
        self._cooldown_seconds: float = max(0.0, float(cooldown_seconds))
        self._executed_trade_count: int = 0
        self._cooldown_until_epoch: float = 0.0

    def _normalize_candle_key(self, trade: Mapping[str, object]) -> str:
        candle_key = _text(trade.get("candle_key"))
        if candle_key:
            return candle_key
        candle_sequence = _text(trade.get("candle_sequence"))
        if candle_sequence:
            return f"seq:{candle_sequence}"
        return ""

    def should_trigger(self, trade: Mapping[str, object]) -> tuple[bool, str]:
        now = time.time()
        if self._max_trades_per_session > 0 and self._executed_trade_count >= self._max_trades_per_session:
            return False, "session_trade_limit_reached"
        if self._cooldown_until_epoch > 0.0:
            if now < self._cooldown_until_epoch:
                return False, "cooldown_active"
            self._cooldown_until_epoch = 0.0

        side = _upper(trade.get("side"))
        last_side: str = self._last_side
        signal_id = _text(trade.get("signal_id"))
        published_ms = int(_float(trade.get("published_epoch"), 0.0) * 1000.0)
        trigger_token = _text(trade.get("trigger_token"))
        token = trigger_token or (f"{side}|{signal_id}" if signal_id else f"{side}|{published_ms}")
        candle_key = self._normalize_candle_key(trade)
        if not candle_key:
            return False, "missing_closed_candle_identity"

        if candle_key != self._active_candle_key:
            self._active_candle_key = candle_key
            self._trades_this_candle = 0
            self._candle_side = ""

        changed: bool = bool(token != self._last_token or side != last_side)
        rearmed = self._rearm_seconds > 0.0 and (now - self._last_emit_epoch) >= self._rearm_seconds
        if not changed and not rearmed:
            return False, "unchanged_live_state"

        if self._lock_side_per_candle and self._candle_side in {"BUY", "SELL"} and side in {"BUY", "SELL"} and side != self._candle_side:
            return False, "opposite_side_blocked_same_candle"

        if self._max_trades_per_candle > 0 and self._trades_this_candle >= self._max_trades_per_candle:
            return False, "candle_trade_limit_reached"

        if (
            last_side in {"BUY", "SELL"}
            and side in {"BUY", "SELL"}
            and side != last_side
            and self._flip_guard_seconds > 0.0
            and (now - self._last_emit_epoch) < self._flip_guard_seconds
        ):
            return False, "flip_guard_active"

        if changed or rearmed:
            self._last_token = token
            self._last_side = side
            self._last_emit_epoch = now
            self._trades_this_candle += 1
            if side in {"BUY", "SELL"}:
                self._candle_side = side
            return True, "triggered"
        return False, "unchanged_live_state"

    def record_trade_execution(self, trade: Mapping[str, object] | None = None) -> None:
        if trade is not None:
            side = _upper(trade.get("side"))
            signal_id = _text(trade.get("signal_id"))
            published_ms = int(_float(trade.get("published_epoch"), 0.0) * 1000.0)
            trigger_token = _text(trade.get("trigger_token"))
            self._last_token = trigger_token or (f"{side}|{signal_id}" if signal_id else f"{side}|{published_ms}")
            self._last_side = side
            self._last_emit_epoch = time.time()
            if side in {"BUY", "SELL"}:
                self._candle_side = side
        self._executed_trade_count += 1
        if self._cooldown_after_trades <= 0 or self._cooldown_seconds <= 0.0:
            return
        if self._executed_trade_count >= self._cooldown_after_trades:
            self._cooldown_until_epoch = time.time() + self._cooldown_seconds


class _InstanceLock:
    def __init__(self, *, session_id: str) -> None:
        lock_name = f"phoenixguard_direct_bridge_{_text(session_id) or 'default'}.lock"
        self.path = Path(tempfile.gettempdir()) / lock_name

    def acquire(self) -> None:
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"Bridge instance already running for this session ({self.path}).") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))

    def release(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping_value(mapping: Mapping[str, object], key: str) -> object:
    return mapping.get(key)


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _upper(value: object) -> str:
    return _text(value).upper()


def _freshness_context(
    payload: Mapping[str, object],
    signal: Mapping[str, object],
    *,
    now_epoch: float | None = None,
) -> dict[str, object]:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    active_now_epoch = float(now_epoch if now_epoch is not None else time.time())
    direct_visual_bias = (
        _upper(signal.get("source")) == "PHOENIXGUARD_DIRECT_VISUAL_BIAS_V3"
    )
    published_epoch = _float(
        signal.get("published_epoch")
        or latest_signal.get("published_epoch")
        or payload.get("display_published_epoch")
        or payload.get("last_capture_epoch"),
        0.0,
    )
    last_capture_epoch = _float(
        signal.get("observed_epoch")
        if direct_visual_bias
        else payload.get("last_capture_epoch"),
        0.0,
    )
    display_capture_epoch = (
        0.0
        if direct_visual_bias
        else _float(payload.get("display_capture_epoch"), 0.0)
    )
    display_published_epoch = (
        0.0
        if direct_visual_bias
        else _float(payload.get("display_published_epoch"), 0.0)
    )
    signal_age_seconds = _float(signal.get("signal_age_seconds"), 0.0)
    published_age_seconds = max(0.0, active_now_epoch - published_epoch) if published_epoch > 0.0 else 0.0
    capture_age_seconds = max(0.0, active_now_epoch - last_capture_epoch) if last_capture_epoch > 0.0 else 0.0
    display_capture_age_seconds = max(0.0, active_now_epoch - display_capture_epoch) if display_capture_epoch > 0.0 else 0.0
    observed_ages = [signal_age_seconds, published_age_seconds]
    if last_capture_epoch > 0.0:
        observed_ages.append(capture_age_seconds)
    if display_capture_epoch > 0.0:
        observed_ages.append(display_capture_age_seconds)
    effective_signal_age_seconds = max(observed_ages)
    freshness_window_seconds = _float(signal.get("freshness_window_seconds"), 0.0)
    freshness_score = _float(signal.get("freshness_score"), 0.0)
    pipeline_latency_seconds = _float(signal.get("pipeline_latency_seconds"), 0.0)
    return {
        "published_epoch": published_epoch,
        "last_capture_epoch": last_capture_epoch,
        "display_capture_epoch": display_capture_epoch,
        "display_published_epoch": display_published_epoch,
        "signal_age_seconds": round(signal_age_seconds, 3),
        "published_age_seconds": round(published_age_seconds, 3),
        "capture_age_seconds": round(capture_age_seconds, 3),
        "display_capture_age_seconds": round(display_capture_age_seconds, 3),
        "effective_signal_age_seconds": round(effective_signal_age_seconds, 3),
        "freshness_window_seconds": freshness_window_seconds,
        "freshness_score": freshness_score,
        "pipeline_latency_seconds": pipeline_latency_seconds,
        "freshness_basis": (
            "direct_visual_bias_capture"
            if direct_visual_bias
            else "completed_live_study"
        ),
    }


def _read_json_url(url: str, *, timeout_sec: float) -> dict[str, object]:
    import urllib.request

    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        data = response.read().decode("utf-8")
    parsed = json.loads(data)
    if not isinstance(parsed, Mapping):
        raise ValueError(f"endpoint did not return a JSON object: {url}")
    return dict(cast(Mapping[str, object], parsed))


def _load_json_file(path: Path) -> dict[str, object]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"JSON file is not an object: {path}")
    mapping = cast(Mapping[str, object], raw)
    return {str(key): value for key, value in mapping.items()}


_LOCAL_LIVE_STATE_CACHE: dict[Path, tuple[int, int, dict[str, object]]] = {}


def _load_cached_live_state(path: Path) -> dict[str, object]:
    stat = path.stat()
    cached = _LOCAL_LIVE_STATE_CACHE.get(path)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return dict(cached[2])
    payload = _load_json_file(path)
    _LOCAL_LIVE_STATE_CACHE[path] = (stat.st_mtime_ns, stat.st_size, payload)
    return dict(payload)


def _session_compact_live_state_path(session_id: str) -> Path:
    return (
        _default_live_runtime_dir()
        / "data_live"
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / _text(session_id)
        / "compact_live_state.json"
    )


def _session_direct_visual_bias_path(session_id: str) -> Path:
    return (
        _default_live_runtime_dir()
        / "data_live"
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / _text(session_id)
        / "direct_visual_bias_v3.json"
    )


def _attach_direct_visual_bias(
    payload: Mapping[str, object], session_id: str
) -> dict[str, object]:
    merged = dict(payload)
    sidecar_path = _session_direct_visual_bias_path(session_id)
    if not sidecar_path.is_file():
        return merged
    try:
        direct_bias = _load_cached_live_state(sidecar_path)
    except Exception:
        return merged
    if _upper(direct_bias.get("schema_version")) != "PG_DIRECT_VISUAL_BIAS_V3":
        return merged
    merged["direct_visual_bias_v3"] = direct_bias
    return merged


def _session_frontline_reasoning_path(session_id: str) -> Path:
    runtime_dir = _default_live_runtime_dir()
    data_dir = str(os.getenv("PHOENIXGUARD_DATA_DIR") or "").strip()
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    if data_dir:
        add(Path(data_dir).expanduser() / "mobile_api" / "window_tracker" / "sessions" / _text(session_id) / "frontline_reasoning_v3.json")
    add(runtime_dir / "data_live" / "mobile_api" / "window_tracker" / "sessions" / _text(session_id) / "frontline_reasoning_v3.json")
    add(PROJECT_ROOT / "data" / "mobile_api" / "window_tracker" / "sessions" / _text(session_id) / "frontline_reasoning_v3.json")
    add(PROJECT_ROOT / "data" / "window_tracker" / "sessions" / _text(session_id) / "frontline_reasoning_v3.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _attach_frontline_reasoning(
    payload: Mapping[str, object], session_id: str
) -> dict[str, object]:
    merged = dict(payload)
    sidecar_path = _session_frontline_reasoning_path(session_id)
    if not sidecar_path.is_file():
        return merged
    try:
        frontline = _load_cached_live_state(sidecar_path)
    except Exception:
        return merged
    if _upper(frontline.get("schema_version")) != "PG_FRONTLINE_REASONING_V3":
        return merged
    merged["frontline_reasoning_v3"] = frontline
    return merged


def _attach_live_sidecars(payload: Mapping[str, object], session_id: str) -> dict[str, object]:
    merged = _attach_direct_visual_bias(payload, session_id)
    return _attach_frontline_reasoning(merged, session_id)


def _payload_live_epoch(payload: Mapping[str, object]) -> float:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    visual_observation = _mapping_or_empty(payload.get("visual_observation_v3"))
    return _freshest_epoch(
        payload.get("last_capture_epoch"),
        payload.get("display_published_epoch"),
        payload.get("display_capture_epoch"),
        latest_signal.get("published_epoch"),
        visual_observation.get("last_observed_epoch"),
        visual_observation.get("attempted_epoch"),
    )


def _prefer_fresher_live_payload(
    primary_payload: Mapping[str, object],
    secondary_payload: Mapping[str, object],
) -> dict[str, object]:
    primary_epoch = _payload_live_epoch(primary_payload)
    secondary_epoch = _payload_live_epoch(secondary_payload)
    selected = secondary_payload if secondary_epoch > primary_epoch else primary_payload
    return dict(selected)


def _read_live_state(*, base_url: str, session_id: str, timeout_sec: float) -> dict[str, object]:
    local_payload: dict[str, object] | None = None
    local_path = _session_compact_live_state_path(session_id)
    if local_path.exists():
        try:
            local_payload = _load_cached_live_state(local_path)
        except Exception:
            local_payload = None

    # The runtime file is published before the HTTP view is serialized. On the
    # local bridge path, returning it first removes an avoidable API round trip.
    if local_payload is not None and _payload_live_epoch(local_payload) > 0.0:
        local_payload["_bridge_state_source"] = "local_runtime_file"
        local_payload["_bridge_state_epoch"] = _payload_live_epoch(local_payload)
        return _attach_live_sidecars(local_payload, session_id)

    url = base_url.rstrip("/") + f"/v1/mobile/live/state/v3/{session_id}?mode=CLEAN_LIVE&compact=1"
    api_payload: dict[str, object] | None = None
    api_error: Exception | None = None
    try:
        api_payload = _read_json_url(url, timeout_sec=timeout_sec)
    except Exception as exc:
        api_error = exc

    if api_payload is not None and local_payload is not None:
        api_epoch = _payload_live_epoch(api_payload)
        local_epoch = _payload_live_epoch(local_payload)
        payload = _prefer_fresher_live_payload(api_payload, local_payload)
        payload["_bridge_state_source"] = "local_runtime_file" if local_epoch > api_epoch else "mobile_api"
        payload["_bridge_state_epoch"] = _payload_live_epoch(payload)
        return _attach_live_sidecars(payload, session_id)
    if local_payload is not None:
        local_payload["_bridge_state_source"] = "local_runtime_file"
        local_payload["_bridge_state_epoch"] = _payload_live_epoch(local_payload)
        return _attach_live_sidecars(local_payload, session_id)
    if api_payload is not None:
        api_payload["_bridge_state_source"] = "mobile_api"
        api_payload["_bridge_state_epoch"] = _payload_live_epoch(api_payload)
        return _attach_live_sidecars(api_payload, session_id)
    raise RuntimeError(f"Unable to read live state from mobile API or local runtime file: {api_error}")


def _iter_phoenixguard_session_updates(
    *,
    base_url: str,
    session_id: str,
    timeout_sec: float,
) -> Iterator[dict[str, object]]:
    """Yield PhoenixGuard-published session updates without bridge-side polling."""

    stream_url = (
        base_url.rstrip("/")
        + f"/v1/mobile/window-tracker/sessions/{session_id}/stream"
    )
    stream_request = request.Request(
        stream_url,
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    )
    try:
        with request.urlopen(
            stream_request,
            timeout=max(3.0, float(timeout_sec)),
        ) as response:
            event_name = ""
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if event_name == "SESSION_UPDATE" and data_lines:
                        try:
                            decoded = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError:
                            decoded = None
                        if isinstance(decoded, Mapping):
                            yield dict(cast(Mapping[str, object], decoded))
                    event_name = ""
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
    except (OSError, TimeoutError, error.URLError) as exc:
        raise RuntimeError(
            f"PhoenixGuard listener stream is unavailable: {exc}"
        ) from exc


def _trade_from_listener_payload(
    payload: Mapping[str, object],
    *,
    base_url: str,
    session_id: str,
    score_threshold: float,
    fixed_expiry_seconds_override: int | None,
    max_signal_age_seconds: float,
    signal_source: str,
    frontline_required: bool = False,
    frontline_freshness_seconds: float = DEFAULT_FRONTLINE_FRESHNESS_SECONDS,
) -> dict[str, object]:
    """Resolve one producer-published update without rereading live state."""

    payload = _attach_live_sidecars(payload, session_id)
    trade = _resolve_trade_payload(
        payload,
        score_threshold=score_threshold,
        max_signal_age_seconds=max_signal_age_seconds,
        signal_source=signal_source,
        frontline_required=frontline_required,
        frontline_freshness_seconds=frontline_freshness_seconds,
    )
    if fixed_expiry_seconds_override is not None and int(
        fixed_expiry_seconds_override
    ) > 0:
        trade["expiry_seconds"] = int(fixed_expiry_seconds_override)
    return {
        "dry_run": True,
        **trade,
        "base_url": base_url,
        "session_id": session_id,
        "state_source": "phoenixguard_session_stream",
        "state_epoch": _float(
            _mapping_or_empty(payload.get("direct_visual_bias_v3")).get(
                "observed_epoch"
            ),
            0.0,
        ),
    }


def _read_fresh_trade(
    *,
    base_url: str,
    session_id: str,
    timeout_sec: float,
    score_threshold: float,
    max_signal_age_seconds: float,
    signal_source: str,
    frontline_required: bool = False,
    frontline_freshness_seconds: float = DEFAULT_FRONTLINE_FRESHNESS_SECONDS,
) -> tuple[dict[str, object], dict[str, object]]:
    payload = _read_live_state(
        base_url=base_url,
        session_id=session_id,
        timeout_sec=max(0.25, min(float(timeout_sec), 5.0)),
    )
    trade = _resolve_trade_payload(
        payload,
        score_threshold=score_threshold,
        max_signal_age_seconds=max_signal_age_seconds,
        signal_source=signal_source,
        frontline_required=frontline_required,
        frontline_freshness_seconds=frontline_freshness_seconds,
    )
    return payload, trade


def _normalize_score(value: object) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1.0:
            return min(number / 100.0, 1.0)
        return max(0.0, min(number, 1.0))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            number = float(text)
            if number > 1.0:
                return min(number / 100.0, 1.0)
            return max(0.0, min(number, 1.0))
        except ValueError:
            return 0.0
    return 0.0


def _weighted_signal_score(payload: Mapping[str, object]) -> float:
    candidates: list[tuple[object, float]] = [
        (_mapping_value(payload, "score"), 1.0),
        (_mapping_value(payload, "confidence"), 1.0),
        (_mapping_value(payload, "actionability_score"), 1.0),
        (_mapping_value(payload, "book_rule_score"), 1.0),
        (_mapping_value(payload, "trade_score"), 1.0),
        (_mapping_value(payload, "signal_score"), 1.2),
        (_mapping_value(payload, "market_confidence"), 0.8),
        (_mapping_value(payload, "timeframe_confidence"), 0.8),
        (_mapping_value(payload, "risk_score"), 0.6),
        (_mapping_value(payload, "decision_score"), 1.0),
        (_mapping_value(payload, "strength"), 0.7),
    ]
    score_total = 0.0
    weight_total = 0.0
    for value, weight in candidates:
        normalized = _normalize_score(value)
        if normalized <= 0.0:
            continue
        score_total += normalized * weight
        weight_total += weight

    nested = _as_mapping(_mapping_value(payload, "hidden_state"))
    for inner in nested.values():
        inner_mapping = _as_mapping(inner)
        if not inner_mapping:
            continue
        nested_score = _weighted_signal_score(inner_mapping)
        score_total += nested_score * 0.8
        weight_total += 0.8

    for key in ("book_rules", "book_rule_policy", "rule_weights", "weights"):
        weights_mapping = _as_mapping(_mapping_value(payload, key))
        for inner_value in weights_mapping.values():
            score_total += _normalize_score(inner_value) * 0.5
            weight_total += 0.5

    if weight_total <= 0.0:
        return 0.0
    return max(0.0, min(1.0, score_total / weight_total))


def _timeframe_seconds_from_text(value: object, default_seconds: int = 300) -> int:
    text = _upper(value)
    if not text:
        return int(default_seconds)
    unit = text[0]
    amount_text = text[1:]
    try:
        amount = int(amount_text)
    except ValueError:
        return int(default_seconds)
    if amount <= 0:
        return int(default_seconds)
    if unit == "S":
        return amount
    if unit == "M":
        return amount * 60
    if unit == "H":
        return amount * 3600
    return int(default_seconds)


def _candle_identity_from_payload(payload: Mapping[str, object]) -> tuple[str, int | None]:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    market_study = _mapping_or_empty(latest_signal.get("market_study_v3"))
    thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))

    candle_key = _text(
        market_study.get("closed_candle_key")
        or latest_signal.get("closed_candle_key")
        or thesis.get("closed_candle_key")
        or payload.get("closed_candle_key")
    )
    sequence_raw = (
        market_study.get("closed_candle_sequence")
        or latest_signal.get("closed_candle_sequence")
        or thesis.get("closed_candle_sequence")
        or payload.get("closed_candle_sequence")
    )
    sequence = int(_float(sequence_raw, -1.0)) if sequence_raw is not None else -1
    if sequence < 0:
        sequence_opt: int | None = None
    else:
        sequence_opt = sequence
    return candle_key, sequence_opt


def _tracking_summary_from_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    tracking_summary = payload.get("tracking_summary")
    if isinstance(tracking_summary, Mapping):
        return cast(Mapping[str, object], tracking_summary)
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    nested_tracking_summary = latest_signal.get("tracking_summary")
    if isinstance(nested_tracking_summary, Mapping):
        return cast(Mapping[str, object], nested_tracking_summary)
    return {}


def _candle_color_to_side(value: object) -> str:
    color = _upper(value)
    if color in {"GREEN", "BULL", "BULLISH", "BUY"}:
        return "BUY"
    if color in {"RED", "PINK", "MAGENTA", "BEAR", "BEARISH", "SELL"}:
        return "SELL"
    return ""


def _direction_text_to_side(value: object) -> str:
    direction = _upper(value)
    if direction in {"BUY", "BULL", "BULLISH", "UP", "UP_SWING", "UPTREND"}:
        return "BUY"
    if direction in {"SELL", "BEAR", "BEARISH", "DOWN", "DOWN_SWING", "DOWNTREND"}:
        return "SELL"
    if direction.startswith(("BUY_", "BULL_", "UP_")):
        return "BUY"
    if direction.startswith(("SELL_", "BEAR_", "DOWN_")):
        return "SELL"
    return ""


def _current_visual_bias_context(payload: Mapping[str, object]) -> dict[str, object]:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    tracking_summary = _tracking_summary_from_payload(payload)
    candle_context = _mapping_or_empty(tracking_summary.get("candle_movement_context_v3"))
    current_leg = _mapping_or_empty(candle_context.get("current_leg"))
    trend_context = _mapping_or_empty(tracking_summary.get("trend_context"))
    market_study = _mapping_or_empty(latest_signal.get("market_study_v3") or tracking_summary.get("market_study_v3"))

    visual_side = ""
    source = ""
    visual_stage = _text(candle_context.get("move_stage"))
    current_leg_side = _upper(current_leg.get("side"))
    current_leg_candidate_side = _upper(current_leg.get("candidate_side"))
    market_study_reasons: list[str] = []
    if market_study:
        behavior = _mapping_or_empty(market_study.get("behavior"))
        current_state = _mapping_or_empty(behavior.get("current_state"))
        current_segment = _mapping_or_empty(behavior.get("current_segment"))
        directional_read = _mapping_or_empty(market_study.get("directional_read"))
        regression = _mapping_or_empty(market_study.get("regression"))
        current_pressure = _mapping_or_empty(regression.get("current_pressure"))
        candle_intelligence = _mapping_or_empty(market_study.get("candle_intelligence"))
        latest_candle = _mapping_or_empty(candle_intelligence.get("latest"))

        current_frame_candidates: list[tuple[str, str]] = [
            (
                _direction_text_to_side(current_state.get("direction") or current_state.get("state")),
                "latest_signal.market_study_v3.behavior.current_state.direction",
            ),
            (
                _direction_text_to_side(latest_candle.get("direction")),
                "latest_signal.market_study_v3.candle_intelligence.latest.direction",
            ),
            (
                _direction_text_to_side(current_pressure.get("side") or current_pressure.get("direction")),
                "latest_signal.market_study_v3.regression.current_pressure.side",
            ),
            (
                _direction_text_to_side(directional_read.get("side")),
                "latest_signal.market_study_v3.directional_read.side",
            ),
            (
                _direction_text_to_side(current_segment.get("direction") or current_segment.get("state")),
                "latest_signal.market_study_v3.behavior.current_segment.direction",
            ),
        ]
        for vote_side, vote_source in current_frame_candidates:
            if vote_side in {"BUY", "SELL"}:
                if not visual_side:
                    visual_side = vote_side
                    source = vote_source
                market_study_reasons.append(f"{vote_source}={vote_side}")

        if not visual_stage:
            visual_stage = _text(
                current_state.get("state")
                or current_segment.get("state")
                or behavior.get("market_story")
            )

    if not visual_side:
        for key in ("control_direction", "local_direction", "major_trend_direction", "global_direction"):
            candidate_side = _direction_text_to_side(tracking_summary.get(key))
            if candidate_side in {"BUY", "SELL"}:
                visual_side = candidate_side
                source = f"tracking_summary.{key}"
                break
    if not visual_side:
        for key in ("micro_bias", "local_bias", "global_bias"):
            candidate_side = _direction_text_to_side(trend_context.get(key))
            if candidate_side in {"BUY", "SELL"}:
                visual_side = candidate_side
                source = f"tracking_summary.trend_context.{key}"
                break
    if not visual_side:
        candle_color_side = _candle_color_to_side(tracking_summary.get("latest_candle_color"))
        if candle_color_side in {"BUY", "SELL"}:
            visual_side = candle_color_side
            source = "tracking_summary.latest_candle_color"
    if not visual_side and current_leg_candidate_side in {"BUY", "SELL"}:
        visual_side = current_leg_candidate_side
        source = "tracking_summary.candle_movement_context_v3.current_leg.candidate_side"
    if not visual_side and current_leg_side in {"BUY", "SELL"}:
        visual_side = current_leg_side
        source = "tracking_summary.candle_movement_context_v3.current_leg.side"

    behavior = _mapping_or_empty(market_study.get("behavior"))
    current_segment = _mapping_or_empty(behavior.get("current_segment"))
    candle_intelligence = _mapping_or_empty(market_study.get("candle_intelligence"))
    latest_candle = _mapping_or_empty(candle_intelligence.get("latest"))

    market_segment_start = _text(current_segment.get("start_index"))
    market_segment_end = _text(current_segment.get("end_index"))
    market_candle_id = _text(latest_candle.get("candle_id"))

    token_parts = [
        _text(current_leg.get("label")) or visual_stage or "NO_VISUAL_STAGE",
        visual_side or "WAIT",
        current_leg_side or "HOLD",
        current_leg_candidate_side or "NONE",
        _text(current_leg.get("transition_state")) or "NONE",
        str(int(_float(current_leg.get("confirmation_count"), 0.0))),
        _upper(tracking_summary.get("latest_candle_color")) or "NONE",
        _text(current_leg.get("start_index")) or market_segment_start or "NA",
        _text(current_leg.get("end_index")) or market_segment_end or "NA",
        market_candle_id or "NA",
    ]
    return {
        "side": visual_side,
        "source": source,
        "stage": visual_stage,
        "trigger_token": "|".join(token_parts),
        "summary": _text(
            candle_context.get("summary")
            or current_leg.get("stage_reason")
            or candle_context.get("move_stage_reason")
            or "; ".join(market_study_reasons)
        ),
    }


def bridge_overlay_session_id(session_id: str | None = None) -> str:
    _, resolved_session = _resolve_stack_context(base_url=None, session_id=session_id)
    return resolved_session


def bridge_overlay_payload(session_id: str) -> tuple[dict[str, object], str, float]:
    """Return (payload, payload_path, mtime_epoch) for a live bridge session."""
    payload_path = _session_compact_live_state_path(session_id)
    mtime = 0.0
    if not payload_path.is_file():
        return {}, str(payload_path), mtime
    try:
        raw = payload_path.read_text(encoding="utf-8")
        mtime = payload_path.stat().st_mtime
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}, str(payload_path), mtime
    if not isinstance(parsed, Mapping):
        return {}, str(payload_path), mtime
    payload = _attach_direct_visual_bias(dict(cast(Mapping[str, object], parsed)), session_id)
    return payload, str(payload_path), mtime


def bridge_overlay_frame(
    payload: Mapping[str, object],
    *,
    signal_source: str = DEFAULT_SIGNAL_SOURCE,
) -> dict[str, object]:
    """Build the display frame for the floating bridge-view overlay window."""
    signal = _bias_signal_from_state(payload)
    if not signal:
        signal = _live_signal_from_state(payload, signal_source=signal_source)
    visual = _current_visual_bias_context(payload)
    placement = _mapping_or_empty(signal.get("placement_context"))
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    execution_timing = _mapping_or_empty(latest_signal.get("execution_timing"))
    thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))
    tracking_summary = _tracking_summary_from_payload(payload)
    candle_context = _mapping_or_empty(
        tracking_summary.get("candle_movement_context_v3")
    )
    current_leg = _mapping_or_empty(candle_context.get("current_leg"))
    historical_structure = _mapping_or_empty(
        tracking_summary.get("historical_structure")
    )
    price_position = _mapping_or_empty(placement.get("price_position"))

    side = _upper(signal.get("side"))
    actionable = bool(signal.get("actionable"))
    entry_timing_state = _text(signal.get("entry_timing_state"))
    entry_timing_class = _text(signal.get("entry_timing_class"))
    reject_reason = _text(signal.get("reject_reason"))
    entry_timing_reason = _text(signal.get("entry_timing_reason"))
    reason_text = reject_reason or entry_timing_reason
    gate_decision = _upper(placement.get("gate_decision"))
    gate_reason = _text(placement.get("gate_reason"))
    symbol = _text(
        signal.get("symbol")
        or thesis.get("symbol")
        or payload.get("market")
    )
    timeframe = _text(
        signal.get("timeframe")
        or thesis.get("timeframe")
        or payload.get("timeframe")
        or "M5"
    )
    thesis_side = _upper(
        thesis.get("current_signal_side")
        or thesis.get("effective_side")
        or thesis.get("side")
    )
    thesis_status = _text(thesis.get("status") or thesis.get("room_state"))
    thesis_confidence = _float(thesis.get("confidence"), 0.0)
    countertrend_blocked = bool(thesis.get("countertrend_blocked"))
    blocked_countertrend_side = _upper(thesis.get("blocked_countertrend_side"))
    opposing_force_risk = _float(execution_timing.get("opposing_force_risk"), 0.0)
    local_position = _float(
        price_position.get("local_position"),
        _float(price_position.get("close_position"), 0.5),
    )
    global_position = _float(price_position.get("global_position"), 0.5)
    entry_area_relation = _text(
        placement.get("entry_area_relation")
        or execution_timing.get("entry_area_relation")
    )
    historical_area_rule = _text(
        placement.get("historical_area_rule")
        or execution_timing.get("historical_area_rule")
    )
    flow_ready = bool(
        placement.get("current_flow_continuation_ready")
        or placement.get("breakout_confirmation")
        or execution_timing.get("current_flow_continuation_ready")
        or execution_timing.get("breakout_confirmation")
    )
    continuation_score = _float(
        placement.get("continuation_score")
        or execution_timing.get("continuation_score")
        or tracking_summary.get("continuation_score"),
        0.0,
    )
    major_trend = _mapping_or_empty(tracking_summary.get("major_trend_context"))
    major_trend_side = _upper(major_trend.get("side"))
    leg_side = _upper(current_leg.get("side"))
    leg_candidate_side = _upper(current_leg.get("candidate_side"))
    move_stage = _text(current_leg.get("move_stage"))
    leg_candles = int(_float(current_leg.get("candle_count"), 0.0))
    leg_duration_minutes = int(_float(current_leg.get("duration_minutes"), 0.0))

    lines: list[dict[str, str]] = []
    lines.append(
        {
            "text": f"{symbol or 'ACTIVE'} {timeframe}",
            "color": "header",
        }
    )
    lines.append({"text": "PhoenixGuard bridge view - LIVE", "color": "dim"})

    side_display = side or _text(signal.get("current_visual_side")) or "WAIT"
    actionable_label = "ACTIONABLE - TRADE READY" if actionable else "WAITING"
    status_color = (
        "green"
        if actionable
        else ("amber" if side_display not in {"", "WAIT"} else "red")
    )
    lines.append({"text": f"Signal: {side_display} | {actionable_label}", "color": status_color})
    visual_side = _text(visual.get("side")) or "WAIT"
    visual_stage = _text(visual.get("stage")) or "-"
    lines.append({"text": f"Visual candle/leg: {visual_side} | stage: {visual_stage}", "color": "dim"})

    if thesis_side:
        thesis_line = f"Thesis: {thesis_side} {thesis_status or ''}"
        if thesis_confidence:
            thesis_line += f" ({thesis_confidence:.2f})"
        if countertrend_blocked and blocked_countertrend_side:
            thesis_line += f" | {blocked_countertrend_side} countertrend blocked"
        lines.append({"text": thesis_line, "color": "cyan"})

    lines.append(
        {"text": f"Timing: {entry_timing_state or '-'} | {entry_timing_class or '-'}", "color": "white"}
    )
    if reason_text:
        lines.append({"text": f"  reason: {reason_text}", "color": "dim"})
    if gate_decision:
        gate_color = (
            "green"
            if gate_decision == "ALLOW"
            else ("amber" if gate_decision == "WAIT" else "red")
        )
        lines.append({"text": f"Placement: {gate_decision}", "color": gate_color})
        if gate_reason:
            lines.append({"text": f"  {gate_reason}", "color": "dim"})

    placement_values: list[str] = []
    if opposing_force_risk:
        placement_values.append(f"opposing force {opposing_force_risk:.2f}")
    placement_values.append(f"local {local_position:.2f} global {global_position:.2f}")
    if entry_area_relation:
        placement_values.append(f"area {entry_area_relation}")
    if historical_area_rule:
        placement_values.append(historical_area_rule)
    if placement_values:
        lines.append({"text": "  " + " | ".join(placement_values), "color": "dim"})

    flow_parts: list[str] = []
    flow_parts.append("FLOW LIVE" if flow_ready else "flow waiting")
    if continuation_score:
        flow_parts.append(f"continuation {continuation_score:.2f}")
    if major_trend_side:
        flow_parts.append(f"major {major_trend_side}")
    if flow_parts:
        lines.append({"text": "  " + " | ".join(flow_parts), "color": "dim"})

    if leg_side or leg_candidate_side:
        leg_parts: list[str] = [f"leg {leg_side or leg_candidate_side}"]
        if move_stage:
            leg_parts.append(move_stage)
        if leg_candles:
            leg_parts.append(f"{leg_candles} candles")
        if leg_duration_minutes:
            leg_parts.append(f"{leg_duration_minutes}m")
        if historical_structure:
            leg_parts.append(f"{len(historical_structure)} legs")
        lines.append({"text": "  " + " | ".join(leg_parts), "color": "dim"})

    return {
        "lines": lines,
        "status_color": status_color,
        "side": side_display,
        "visual_side": visual_side,
        "actionable": actionable,
        "updated": time.strftime("%H:%M:%S"),
    }


def _placement_timing_context(payload: Mapping[str, object], *, side: str) -> dict[str, object]:
    """Mirror PhoenixGuard's observed placement/state for this side.

    This reads the same observation state PhoenixGuard already publishes (price
    position in the studied range, opposing-force risk, entry-area placement,
    the historical area rule and the live-flow state of the studied move) and
    reduces it to an explicit gate decision.  It never consumes PhoenixGuard's
    execution-permission packages; it only makes the visible placement state
    explicit so the bridge buys low, sells high and rides a continuing move
    through its small rests instead of waiting it out.
    """
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    execution_timing = _mapping_or_empty(latest_signal.get("execution_timing"))
    tracking_summary = _tracking_summary_from_payload(payload)
    thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))

    normalized_side = _upper(side)
    price_position = _mapping_or_empty(execution_timing.get("price_position"))
    local_position = _float(
        price_position.get("local_position"),
        _float(price_position.get("close_position"), 0.5),
    )
    global_position = _float(price_position.get("global_position"), 0.5)
    close_position = _float(price_position.get("close_position"), local_position)
    extreme_score = _float(price_position.get("extreme_score"), 0.0)
    extreme_label = _text(price_position.get("extreme_label"))

    opposing_force_risk = _float(execution_timing.get("opposing_force_risk"), 0.0)
    global_extreme_risk = _float(
        execution_timing.get("global_extreme_risk"), extreme_score
    )
    history_area_risk = _float(execution_timing.get("history_area_risk"), 0.0)
    history_area_label = _text(execution_timing.get("history_area_label"))
    history_area_position = _float(execution_timing.get("history_area_position"), 0.5)
    entry_area_relation = _text(execution_timing.get("entry_area_relation"))
    entry_area_score = _float(execution_timing.get("entry_area_score"), 0.0)
    entry_area_near = bool(execution_timing.get("entry_area_near"))
    historical_area_rule = _text(execution_timing.get("historical_area_rule"))
    must_wait_significant = bool(execution_timing.get("must_wait_for_significant_area"))
    significant_entry_context = bool(execution_timing.get("significant_entry_context"))
    significant_zone_entry_context = bool(
        execution_timing.get("significant_zone_entry_context")
    )
    favorable_history_reclaim = bool(execution_timing.get("favorable_history_reclaim"))
    favorable_history_rejection = bool(
        execution_timing.get("favorable_history_rejection")
    )
    historical_entry_anchor = bool(execution_timing.get("historical_entry_anchor"))
    current_flow_continuation_ready = bool(
        execution_timing.get("current_flow_continuation_ready")
    )
    breakout_confirmation = bool(execution_timing.get("breakout_confirmation"))
    clear_path_score = _float(execution_timing.get("clear_path_score"), 0.0)
    entry_allowed = execution_timing.get("entry_allowed")
    timing_class = _text(execution_timing.get("timing_class"))
    block_reason = _text(execution_timing.get("block_reason"))
    continuation_score = _float(
        execution_timing.get("continuation_score"),
        _float(tracking_summary.get("continuation_score"), 0.0),
    )
    reversal_score = _float(
        execution_timing.get("reversal_score"),
        _float(tracking_summary.get("reversal_score"), 0.0),
    )
    p_target = _float(
        execution_timing.get("p_target_before_invalidation"), 0.0
    )

    thesis_side = _upper(
        thesis.get("current_signal_side")
        or thesis.get("effective_side")
        or thesis.get("side")
    )
    thesis_status = _text(
        thesis.get("status") or thesis.get("room_state")
    )
    thesis_confidence = _float(thesis.get("confidence"), 0.0)
    countertrend_blocked = bool(thesis.get("countertrend_blocked"))
    blocked_countertrend_side = _upper(thesis.get("blocked_countertrend_side"))
    thesis_entry_zone = _mapping_or_empty(thesis.get("entry_zone"))
    thesis_entry_area_relation = _text(
        thesis_entry_zone.get("entry_area_relation")
        or thesis_entry_zone.get("price_relation")
    )
    thesis_entry_area_score = _float(
        thesis_entry_zone.get("entry_area_score"), 0.0
    )

    major_trend = _mapping_or_empty(tracking_summary.get("major_trend_context"))
    major_trend_side = _upper(major_trend.get("side"))
    major_trend_confidence = _float(major_trend.get("confidence"), 0.0)
    estimated_control_candles = int(
        _float(major_trend.get("estimated_control_candles"), 0.0)
    )
    micro_pullback_active = bool(major_trend.get("micro_pullback_active"))
    confirmed_reversal_takeover = bool(
        major_trend.get("confirmed_reversal_takeover")
    )

    live_flow_ready = bool(
        current_flow_continuation_ready or breakout_confirmation
    )

    if normalized_side == "BUY":
        low_position = min(local_position, close_position) <= 0.45
        high_position = max(local_position, close_position) >= 0.60
        at_area = bool(entry_area_near or entry_area_relation == "at_price")
        favorable_placement = bool(
            low_position
            or at_area
            or favorable_history_reclaim
            or significant_entry_context
            or historical_entry_anchor
        )
        wrong_extreme = bool(
            high_position
            and not (
                favorable_history_reclaim
                or historical_entry_anchor
                or significant_zone_entry_context
            )
        )
    else:
        low_position = min(local_position, close_position) <= 0.40
        high_position = max(local_position, close_position) >= 0.55
        at_area = bool(entry_area_near or entry_area_relation == "at_price")
        favorable_placement = bool(
            high_position
            or at_area
            or favorable_history_rejection
            or significant_entry_context
            or historical_entry_anchor
        )
        wrong_extreme = bool(
            low_position
            and not (
                favorable_history_rejection
                or historical_entry_anchor
                or significant_zone_entry_context
            )
        )

    explicit_allow = bool(entry_allowed is True)
    explicit_wait = bool(entry_allowed is False)
    opposing_force_block = bool(
        opposing_force_risk >= 0.68 and not live_flow_ready and not explicit_allow
    )
    extreme_block = bool(
        global_extreme_risk >= 0.82 and not live_flow_ready and not explicit_allow
    )
    significant_wait = bool(
        must_wait_significant and not live_flow_ready and not explicit_allow
    )
    explicit_placement_wait = bool(
        explicit_wait
        and not live_flow_ready
        and (
            "opposing" in timing_class.lower()
            or "history_area" in timing_class.lower()
            or "extreme" in timing_class.lower()
            or "significant" in timing_class.lower()
        )
    )
    wrong_side_area = bool(
        wrong_extreme
        and not live_flow_ready
        and not (explicit_allow or continuation_score >= 0.54)
    )

    gate_block = bool(
        not live_flow_ready
        and (
            opposing_force_block
            or extreme_block
            or significant_wait
            or explicit_placement_wait
            or wrong_side_area
        )
    )

    reasons: list[str] = []
    if opposing_force_block:
        reasons.append(
            f"opposing force risk {opposing_force_risk:.2f} is too high for a {normalized_side} entry"
        )
    if extreme_block:
        reasons.append(
            f"price is at the global/local extreme ({extreme_label or 'extreme'}) for {normalized_side}"
        )
    if significant_wait:
        reasons.append("price is not at a mapped significant entry area yet")
    if explicit_placement_wait:
        reasons.append(
            block_reason or f"PhoenixGuard placement state waits ({timing_class})"
        )
    if wrong_side_area:
        reasons.append(
            f"price is on the wrong side of the range for a {normalized_side} entry; wait for a pullback into the area"
        )
    if gate_block:
        gate_decision = "BLOCK"
    elif not favorable_placement and not live_flow_ready and not explicit_allow:
        gate_decision = "WAIT"
    else:
        gate_decision = "ALLOW"

    return {
        "gate_decision": gate_decision,
        "gate_block": bool(gate_block),
        "gate_reason": "; ".join(reasons),
        "side": normalized_side,
        "price_position": {
            "local_position": round(local_position, 4),
            "global_position": round(global_position, 4),
            "close_position": round(close_position, 4),
            "extreme_score": round(extreme_score, 4),
            "extreme_label": extreme_label,
        },
        "opposing_force_risk": round(opposing_force_risk, 4),
        "global_extreme_risk": round(global_extreme_risk, 4),
        "history_area_risk": round(history_area_risk, 4),
        "history_area_label": history_area_label,
        "history_area_position": round(history_area_position, 4),
        "entry_area_relation": entry_area_relation,
        "entry_area_score": round(entry_area_score, 4),
        "entry_area_near": bool(entry_area_near),
        "historical_area_rule": historical_area_rule or "buy_lows_sell_highs",
        "must_wait_for_significant_area": bool(must_wait_significant),
        "significant_entry_context": bool(significant_entry_context),
        "favorable_history_reclaim": bool(favorable_history_reclaim),
        "favorable_history_rejection": bool(favorable_history_rejection),
        "current_flow_continuation_ready": bool(current_flow_continuation_ready),
        "breakout_confirmation": bool(breakout_confirmation),
        "live_flow_ready": bool(live_flow_ready),
        "clear_path_score": round(clear_path_score, 4),
        "continuation_score": round(continuation_score, 4),
        "reversal_score": round(reversal_score, 4),
        "p_target_before_invalidation": round(p_target, 4),
        "entry_allowed": bool(explicit_allow),
        "timing_class": timing_class,
        "block_reason": block_reason,
        "thesis_side": thesis_side,
        "thesis_status": thesis_status,
        "thesis_confidence": round(thesis_confidence, 4),
        "thesis_entry_area_relation": thesis_entry_area_relation,
        "thesis_entry_area_score": round(thesis_entry_area_score, 4),
        "countertrend_blocked": bool(countertrend_blocked),
        "blocked_countertrend_side": blocked_countertrend_side,
        "major_trend_side": major_trend_side,
        "major_trend_confidence": round(major_trend_confidence, 4),
        "estimated_control_candles": estimated_control_candles,
        "micro_pullback_active": bool(micro_pullback_active),
        "confirmed_reversal_takeover": bool(confirmed_reversal_takeover),
        "favorable_placement": bool(favorable_placement),
        "wrong_extreme": bool(wrong_extreme),
    }


def _bias_entry_timing_context(payload: Mapping[str, object], *, side: str) -> dict[str, object]:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    execution_timing = _mapping_or_empty(latest_signal.get("execution_timing"))
    timing_signal = _mapping_or_empty(latest_signal.get("timing_signal"))
    market_study = _mapping_or_empty(latest_signal.get("market_study_v3"))

    normalized_side = _upper(side)
    entry_state = _upper(latest_signal.get("entry_state"))
    timing_state = _upper(
        timing_signal.get("entry_state")
        or timing_signal.get("state")
        or timing_signal.get("timing_state")
    )
    timing_class = _text(execution_timing.get("timing_class"))
    block_reason = _text(execution_timing.get("block_reason"))
    instruction = _text(timing_signal.get("instruction"))
    has_timing_context = bool(execution_timing) or bool(timing_signal)
    ready_states = HYBRID_READY_ENTRY_STATES
    watch_states = HYBRID_WATCH_ENTRY_STATES | {"WAIT", "HOLD", "NOT_READY"}
    explicit_entry_allowed = execution_timing.get("entry_allowed")

    placement_context = _placement_timing_context(payload, side=normalized_side)
    placement_gate_blocked = bool(placement_context.get("gate_block"))
    placement_reason = _text(placement_context.get("gate_reason"))

    if entry_state in ready_states or timing_state in ready_states or explicit_entry_allowed is True:
        if placement_gate_blocked:
            return {
                "ready": False,
                "state": "PLACEMENT_WAIT",
                "timing_class": timing_class or "placement_wait",
                "reason": placement_reason or "Wait for a favorable placement area before this entry.",
                "source": "latest_signal.execution_timing",
                "placement_context": placement_context,
            }
        return {
            "ready": True,
            "state": timing_state or entry_state or "READY",
            "timing_class": timing_class,
            "reason": instruction or "PhoenixGuard visual entry timing is ready.",
            "source": "latest_signal.timing_signal",
            "placement_context": placement_context,
        }

    candle_intelligence = _mapping_or_empty(market_study.get("candle_intelligence"))
    latest_candle = _mapping_or_empty(candle_intelligence.get("latest"))
    latest_direction = _direction_text_to_side(latest_candle.get("direction")) if latest_candle else ""
    ratios = _mapping_or_empty(latest_candle.get("ratios"))
    range_multiple = _float(ratios.get("range_vs_sequence_median"), 0.0)
    interaction = _mapping_or_empty(latest_candle.get("interaction"))
    rejection = _mapping_or_empty(interaction.get("rejection"))
    rejection_side = _upper(rejection.get("side"))
    aligned_rejection = bool(rejection.get("detected")) and (
        (normalized_side == "SELL" and rejection_side == "HIGH")
        or (normalized_side == "BUY" and rejection_side == "LOW")
    )

    recent_raw = candle_intelligence.get("recent_candles")
    recent_candles: list[Mapping[str, object]] = []
    if isinstance(recent_raw, Sequence) and not isinstance(recent_raw, (str, bytes, bytearray)):
        recent_candles = [
            cast(Mapping[str, object], row)
            for row in cast(Sequence[object], recent_raw)
            if isinstance(row, Mapping)
        ]
    prior_rows = recent_candles[:-1] if recent_candles and recent_candles[-1] == latest_candle else recent_candles
    opposite_side = "BUY" if normalized_side == "SELL" else "SELL"
    pullback_seen = any(
        _direction_text_to_side(row.get("direction")) == opposite_side
        for row in prior_rows[-2:]
    )

    history_area_wait = timing_class.lower() == "history_area_wait"
    waiting_for_visual_trigger = has_timing_context and (entry_state in watch_states or timing_state in watch_states)
    timing_wait_requested = history_area_wait or waiting_for_visual_trigger or explicit_entry_allowed is False
    if timing_wait_requested and not latest_candle:
        return {
            "ready": False,
            "state": timing_state or entry_state or "WATCH",
            "timing_class": timing_class,
            "reason": block_reason or instruction or f"Wait for a fresh {normalized_side} pullback rejection/reclaim.",
            "source": "latest_signal.execution_timing",
            "placement_context": placement_context,
        }

    if not latest_candle:
        if placement_gate_blocked:
            return {
                "ready": False,
                "state": "PLACEMENT_WAIT",
                "timing_class": timing_class or "placement_wait",
                "reason": placement_reason or "Wait for a favorable placement area before this entry.",
                "source": "latest_signal.execution_timing",
                "placement_context": placement_context,
            }
        return {
            "ready": True,
            "state": "LEGACY_VISUAL_BIAS",
            "timing_class": "legacy_bias_fallback",
            "reason": "No explicit PhoenixGuard entry-timing wait was published.",
            "source": "bias_compatibility_fallback",
            "placement_context": placement_context,
        }

    if latest_direction == opposite_side or latest_direction == "":
        return {
            "ready": False,
            "state": "PULLBACK_IN_PROGRESS" if latest_direction == opposite_side else "WAIT_FOR_CONFIRMATION",
            "timing_class": "visual_pullback_wait",
            "reason": f"{normalized_side} bias remains active, but the current visual candle has not confirmed the entry.",
            "source": "latest_signal.market_study_v3.candle_intelligence.latest",
            "placement_context": placement_context,
        }

    if range_multiple >= 1.25 and not aligned_rejection:
        return {
            "ready": False,
            "state": "EXTENDED_IMPULSE",
            "timing_class": "anti_chase_wait",
            "reason": f"{normalized_side} direction is valid, but the latest candle is already extended; wait for a pullback/retest.",
            "source": "latest_signal.market_study_v3.candle_intelligence.latest.ratios",
            "placement_context": placement_context,
        }

    if timing_wait_requested and latest_direction == normalized_side:
        reentry_confirmed = aligned_rejection or pullback_seen
        if not reentry_confirmed:
            return {
                "ready": False,
                "state": "WAIT_FOR_RECLAIM",
                "timing_class": "visual_pullback_wait",
                "reason": (
                    block_reason
                    or instruction
                    or f"{normalized_side} bias is intact, but wait for a confirmed pullback/reclaim at the favorable area."
                ),
                "source": "latest_signal.market_study_v3.candle_intelligence",
                "placement_context": placement_context,
            }
        if placement_gate_blocked:
            return {
                "ready": False,
                "state": "PLACEMENT_WAIT",
                "timing_class": timing_class or "placement_wait",
                "reason": placement_reason or "Wait for a favorable placement area before this entry.",
                "source": "latest_signal.execution_timing",
                "placement_context": placement_context,
            }
        return {
            "ready": True,
            "state": "VISUAL_REENTRY_READY",
            "timing_class": "pullback_rejection",
            "reason": f"Fresh {normalized_side} confirmation followed a pullback or rejection.",
            "source": "latest_signal.market_study_v3.candle_intelligence",
            "placement_context": placement_context,
        }

    if latest_direction == normalized_side and (aligned_rejection or pullback_seen):
        if placement_gate_blocked:
            return {
                "ready": False,
                "state": "PLACEMENT_WAIT",
                "timing_class": timing_class or "placement_wait",
                "reason": placement_reason or "Wait for a favorable placement area before this entry.",
                "source": "latest_signal.execution_timing",
                "placement_context": placement_context,
            }
        return {
            "ready": True,
            "state": "VISUAL_REENTRY_READY",
            "timing_class": "pullback_rejection",
            "reason": f"Fresh {normalized_side} confirmation followed a pullback or rejection.",
            "source": "latest_signal.market_study_v3.candle_intelligence",
            "placement_context": placement_context,
        }

    # Older PhoenixGuard payloads did not publish timing context. Preserve their
    # bias behavior unless the visual candle positively proves a chase condition
    # or the observed placement is clearly wrong for this side.
    if placement_gate_blocked:
        return {
            "ready": False,
            "state": "PLACEMENT_WAIT",
            "timing_class": timing_class or "placement_wait",
            "reason": placement_reason or "Wait for a favorable placement area before this entry.",
            "source": "latest_signal.execution_timing",
            "placement_context": placement_context,
        }
    return {
        "ready": True,
        "state": "LEGACY_VISUAL_BIAS",
        "timing_class": "legacy_bias_fallback",
        "reason": "No explicit PhoenixGuard entry-timing wait was published.",
        "source": "bias_compatibility_fallback",
        "placement_context": placement_context,
    }


def _market_study_bias_side(market_study: Mapping[str, object]) -> str:
    directional_read = _mapping_or_empty(market_study.get("directional_read"))
    behavior = _mapping_or_empty(market_study.get("behavior"))
    current_state = _mapping_or_empty(behavior.get("current_state"))
    current_segment = _mapping_or_empty(behavior.get("current_segment"))
    regression = _mapping_or_empty(market_study.get("regression"))
    current_pressure = _mapping_or_empty(regression.get("current_pressure"))
    major_trend = _mapping_or_empty(regression.get("major_trend"))
    candle_intelligence = _mapping_or_empty(market_study.get("candle_intelligence"))
    latest_candle = _mapping_or_empty(candle_intelligence.get("latest"))

    for candidate in (
        directional_read.get("side"),
        current_state.get("direction"),
        current_state.get("state"),
        current_segment.get("direction"),
        current_segment.get("state"),
        latest_candle.get("direction"),
        current_pressure.get("side"),
        current_pressure.get("direction"),
        major_trend.get("side"),
        major_trend.get("direction"),
    ):
        side = _direction_text_to_side(candidate)
        if side in {"BUY", "SELL"}:
            return side
    return ""


def _freshest_epoch(*values: object) -> float:
    freshest = 0.0
    for value in values:
        candidate = _float(value, 0.0)
        if candidate > freshest:
            freshest = candidate
    return freshest


def _dominant_side_from_study_payload(payload: Mapping[str, object]) -> tuple[str, float]:
    score_by_side: dict[str, float] = {"BUY": 0.0, "SELL": 0.0}

    def add_signal(side: object, confidence: object = 1.0) -> None:
        normalized_side = _upper(side)
        if normalized_side not in {"BUY", "SELL"}:
            return
        score_by_side[normalized_side] += max(0.0, min(1.0, _float(confidence, 1.0)))

    def add_mapping(mapping: Mapping[str, object], *, weight: float = 1.0) -> None:
        for key in ("side", "effective_side", "current_signal_side", "raw_read_side", "direction", "trade_bias", "headline_action", "model_action", "control_direction", "selected_side"):
            value = mapping.get(key)
            if value is None:
                continue
            side = _upper(value)
            if side not in {"BUY", "SELL"}:
                continue
            signal_conf = _float(
                mapping.get("confidence")
                or mapping.get("effective_confidence")
                or mapping.get("market_confidence")
                or mapping.get("setup_confidence")
                or mapping.get("score")
                or mapping.get("final_execution_score")
                or mapping.get("strength")
                or mapping.get("signal_confidence"),
                1.0,
            )
            score_by_side[side] += max(0.0, min(1.0, signal_conf)) * weight
            return

    for key in ("latest_signal", "signal_thesis_v3", "visual_observation_v3"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            add_mapping(cast(Mapping[str, object], value), weight=1.0)

    recent = payload.get("recent_studies")
    if isinstance(recent, Sequence) and not isinstance(recent, (str, bytes, bytearray)):
        for item in cast(Sequence[object], recent):
            if not isinstance(item, Mapping):
                continue
            mapping = cast(Mapping[str, object], item)
            side = _upper(mapping.get("side") or mapping.get("effective_side") or mapping.get("direction") or mapping.get("bias") or "")
            score = _float(mapping.get("confidence") or mapping.get("score") or mapping.get("market_confidence") or 0.5, 0.5)
            if side in {"BUY", "SELL"}:
                score_by_side[side] += max(0.0, min(1.0, score))

    visual = payload.get("visual_observation_v3")
    if isinstance(visual, Mapping):
        visual_map = cast(Mapping[str, object], visual)
        dominant = _upper(visual_map.get("dominant_side") or visual_map.get("current_side") or visual_map.get("side") or "")
        if dominant in {"BUY", "SELL"}:
            score_by_side[dominant] += max(0.0, min(1.0, _float(visual_map.get("confidence") or visual_map.get("score") or 0.8, 0.8)))

    for key, value in (
        ("headline_action", payload.get("latest_signal")),
        ("side", payload.get("signal_thesis_v3")),
    ):
        if isinstance(value, Mapping):
            mapping = cast(Mapping[str, object], value)
            side = _upper(mapping.get(key) or mapping.get("execution_action") or mapping.get("action") or "")
            if side in {"BUY", "SELL"}:
                add_signal(side, mapping.get("confidence") or mapping.get("effective_confidence") or 0.75)

    if score_by_side["BUY"] == score_by_side["SELL"]:
        lane = _upper(payload.get("dominant_side") or payload.get("signal_side") or "")
        if lane in {"BUY", "SELL"}:
            return lane, max(score_by_side.values())
        return "BUY" if _float(payload.get("market_confidence") or payload.get("confidence") or 0.0, 0.0) >= 0.5 else "SELL", max(score_by_side.values())
    return ("BUY" if score_by_side["BUY"] > score_by_side["SELL"] else "SELL"), max(score_by_side.values())


def _bias_signal_from_state(payload: Mapping[str, object]) -> dict[str, object]:
    direct_bias = _mapping_or_empty(payload.get("direct_visual_bias_v3"))
    direct_side = _upper(direct_bias.get("side"))
    direct_observed_epoch = _float(direct_bias.get("observed_epoch"), 0.0)
    if (
        _upper(direct_bias.get("schema_version")) == "PG_DIRECT_VISUAL_BIAS_V3"
        and direct_side in {"BUY", "SELL"}
        and direct_observed_epoch > 0.0
    ):
        timeframe = _text(direct_bias.get("timeframe") or "M5")
        timeframe_seconds = int(
            _float(
                direct_bias.get("timeframe_seconds"),
                _timeframe_seconds_from_text(timeframe, default_seconds=300),
            )
        )
        candle_sequence = int(
            _float(
                direct_bias.get("candle_sequence"),
                direct_observed_epoch // max(1, timeframe_seconds),
            )
        )
        market = _text(
            direct_bias.get("market") or "USER_LOCKED_ACTIVE_CHART"
        )
        candle_key = _text(direct_bias.get("candle_key")) or (
            f"direct:{market}:{timeframe}:{candle_sequence}"
        )
        confidence = _normalize_score(direct_bias.get("confidence"))
        latest_signal = _mapping_or_empty(payload.get("latest_signal"))
        thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))
        structural_bias_side = _upper(
            latest_signal.get("action")
            or latest_signal.get("headline_action")
            or thesis.get("current_signal_side")
            or thesis.get("effective_side")
            or thesis.get("side")
        )
        # A red candle during a BUY pullback (or a green candle during a SELL
        # pullback) is evidence to wait, never permission to reverse the
        # structural bias.  The direct candle must reconfirm the same side
        # before the existing reclaim/anti-chase timing can release a click.
        visual_matches_structural_bias = structural_bias_side not in {"BUY", "SELL"} or direct_side == structural_bias_side
        # The direct visual side selects direction; the published timing state
        # still controls *where* that direction may enter.
        entry_timing = _bias_entry_timing_context(payload, side=direct_side)
        entry_timing_ready = bool(entry_timing.get("ready")) and visual_matches_structural_bias
        reject_reason = ""
        if not visual_matches_structural_bias:
            reject_reason = (
                f"PhoenixGuard structural {structural_bias_side} bias is active; "
                f"wait for a {structural_bias_side} pullback/reclaim confirmation instead of taking {direct_side}."
            )
        elif not entry_timing_ready:
            reject_reason = _text(entry_timing.get("reason"))
        return {
            "signal_id": f"direct_visual_{int(direct_observed_epoch * 1000)}",
            "side": direct_side,
            "actionable": entry_timing_ready,
            "reject_reason": reject_reason,
            "summary": f"PhoenixGuard current candle vision is {direct_side}.",
            "symbol": market,
            "timeframe": timeframe,
            "expiry_seconds": 300,
            "published_epoch": direct_observed_epoch,
            "observed_epoch": direct_observed_epoch,
            # A listener update is the liveness proof for direct visual bias.
            # Do not turn a stable multi-hour trend into an expired signal just
            # because its direction did not change.
            "valid_until_epoch": 0.0,
            "source": "phoenixguard_direct_visual_bias_v3",
            "entry_state": _text(entry_timing.get("state") or "CURRENT_VISUAL_BIAS"),
            "dominant_score": confidence,
            "study_count": 1,
            "candle_key": candle_key,
            "candle_sequence": candle_sequence,
            "timeframe_seconds": timeframe_seconds,
            "execution_permission": "DIRECT_VISUAL_BIAS",
            "signal_age_seconds": max(0.0, time.time() - direct_observed_epoch),
            "freshness_window_seconds": 0.0,
            "freshness_score": 1.0,
            "pipeline_latency_seconds": _float(
                direct_bias.get("pipeline_latency_seconds"), 0.0
            ),
            "current_visual_side": direct_side,
            "current_visual_source": _text(direct_bias.get("source")),
            "current_visual_stage": "CURRENT_CANDLE",
            "current_visual_summary": f"Latest detected candle is {direct_side}.",
            "entry_timing_ready": entry_timing_ready,
            "entry_timing_state": _text(entry_timing.get("state") or "CURRENT_VISUAL_BIAS"),
            "entry_timing_class": _text(entry_timing.get("timing_class") or "direct_visual_bias"),
            "entry_timing_reason": _text(entry_timing.get("reason") or "Fresh PhoenixGuard candle vision is available."),
            "entry_timing_source": _text(entry_timing.get("source") or direct_bias.get("source")),
            "placement_context": _mapping_or_empty(entry_timing.get("placement_context")),
            "trigger_token": f"{candle_key}|{direct_side}",
        }
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))
    visual = _mapping_or_empty(payload.get("visual_observation_v3"))
    tracking_summary = _tracking_summary_from_payload(payload)
    market_study = _mapping_or_empty(latest_signal.get("market_study_v3") or tracking_summary.get("market_study_v3"))
    studies = payload.get("recent_studies")
    studies_seq: Sequence[object] = cast(Sequence[object], studies) if isinstance(studies, Sequence) and not isinstance(studies, (str, bytes, bytearray)) else []
    visual_context = _current_visual_bias_context(payload)
    current_visual_side = _upper(visual_context.get("side"))

    bias_action = _upper(latest_signal.get("action"))
    if bias_action:
        bias_side = bias_action if bias_action in {"BUY", "SELL"} else ""
    else:
        bias_side = ""
    if bias_side not in {"BUY", "SELL"}:
        bias_side = _upper(
            latest_signal.get("headline_action")
            or thesis.get("current_signal_side")
            or thesis.get("effective_side")
            or thesis.get("side")
            or visual.get("current_signal_side")
            or visual.get("dominant_side")
            or visual.get("current_side")
            or visual.get("side")
        )
    if bias_side not in {"BUY", "SELL"} and market_study:
        bias_side = _market_study_bias_side(market_study)
    if bias_side not in {"BUY", "SELL"}:
        bias_side, _ = _dominant_side_from_study_payload(payload)
    if bias_side not in {"BUY", "SELL"}:
        return {}

    entry_timing = _bias_entry_timing_context(payload, side=bias_side)

    side_source: Mapping[str, object] = latest_signal if latest_signal else thesis if thesis else visual

    def first_value(*values: object) -> object:
        for value in values:
            if value not in (None, ""):
                return value
        return ""

    signal_id_value = first_value(
        side_source.get("signal_id"),
        side_source.get("thesis_id"),
        thesis.get("thesis_id"),
        payload.get("session_id"),
        "",
    )
    freshest_observation_epoch = _freshest_epoch(
        payload.get("last_capture_epoch"),
        payload.get("display_published_epoch"),
        payload.get("display_capture_epoch"),
        latest_signal.get("published_epoch"),
        side_source.get("published_epoch"),
        thesis.get("updated_epoch"),
    )
    published_epoch_value = first_value(
        freshest_observation_epoch,
        side_source.get("published_epoch"),
        thesis.get("updated_epoch"),
        payload.get("last_capture_epoch"),
        payload.get("display_published_epoch"),
        time.time(),
    )
    expiry_value = first_value(
        side_source.get("expiry_seconds"),
        side_source.get("freshness_window_sec"),
        thesis.get("valid_for_seconds"),
        payload.get("decision_valid_until_epoch"),
        300.0,
    )
    published_epoch_float = _float(published_epoch_value, freshest_observation_epoch)
    expiry_float = _float(expiry_value, 300.0)
    valid_until_value = first_value(
        side_source.get("valid_until_epoch"),
        payload.get("decision_valid_until_epoch"),
        published_epoch_float + max(1.0, float(expiry_float or 300.0)),
    )

    signal_id = _text(signal_id_value)
    published_epoch = published_epoch_float
    expiry_seconds = int(expiry_float or 300.0)
    valid_until = _float(valid_until_value, 0.0)

    summary_value = first_value(
        latest_signal.get("summary"),
        latest_signal.get("action"),
        latest_signal.get("headline_action"),
        thesis.get("effective_side"),
        payload.get("status"),
        "PhoenixGuard live execution signal",
    )
    symbol_value = first_value(payload.get("market"), thesis.get("symbol"), "USER_LOCKED_ACTIVE_CHART")
    timeframe_value = first_value(thesis.get("timeframe"), payload.get("timeframe"), "M5")
    timeframe_seconds = _timeframe_seconds_from_text(timeframe_value, default_seconds=300)
    candle_key, candle_sequence = _candle_identity_from_payload(payload)
    if not candle_key and candle_sequence is None:
        live_candle_sequence = int(published_epoch // max(1, timeframe_seconds))
        candle_sequence = live_candle_sequence
        candle_key = f"live:{_text(symbol_value) or 'ACTIVE'}:{_text(timeframe_value) or 'M5'}:{live_candle_sequence}"
    dominant_score = _float(
        latest_signal.get("execution_confidence")
        or latest_signal.get("effective_confidence")
        or latest_signal.get("confidence")
        or latest_signal.get("market_confidence"),
        0.0,
    )
    visual_alignment_conflict = (
        current_visual_side in {"BUY", "SELL"}
        and current_visual_side != bias_side
    )
    visual_alignment_reason = (
        f"PhoenixGuard bias is {bias_side}, but the current visual candle/leg is {current_visual_side}; wait for live alignment."
        if visual_alignment_conflict
        else ""
    )
    entry_timing_ready = bool(entry_timing.get("ready")) and not visual_alignment_conflict

    return {
        "signal_id": signal_id,
        "side": bias_side,
        "actionable": entry_timing_ready,
        "reject_reason": visual_alignment_reason or _text(entry_timing.get("reason")),
        "summary": _text(summary_value),
        "symbol": _text(symbol_value),
        "timeframe": _text(timeframe_value),
        "expiry_seconds": max(1, expiry_seconds),
        "published_epoch": published_epoch,
        "valid_until_epoch": valid_until,
        "source": "direct_live_bias_signal",
        "entry_state": _text(latest_signal.get("entry_state") or "LIVE_STUDY_BIAS"),
        "dominant_score": round(dominant_score, 4),
        "study_count": len(studies_seq),
        "candle_key": candle_key,
        "candle_sequence": candle_sequence,
        "timeframe_seconds": timeframe_seconds,
        "execution_permission": _text(latest_signal.get("execution_permission")),
        "signal_age_seconds": max(0.0, time.time() - published_epoch),
        "freshness_window_seconds": _float(latest_signal.get("freshness_window_sec"), 0.0),
        "freshness_score": _float(latest_signal.get("freshness_score"), 0.0),
        "pipeline_latency_seconds": _float(latest_signal.get("pipeline_latency_sec"), 0.0),
        "current_visual_side": current_visual_side,
        "current_visual_source": _text(visual_context.get("source")),
        "current_visual_stage": _text(visual_context.get("stage")),
        "current_visual_summary": _text(visual_context.get("summary")),
        "entry_timing_ready": entry_timing_ready,
        "entry_timing_state": "VISUAL_SIDE_CONFLICT" if visual_alignment_conflict else _text(entry_timing.get("state")),
        "entry_timing_class": "live_visual_alignment" if visual_alignment_conflict else _text(entry_timing.get("timing_class")),
        "entry_timing_reason": visual_alignment_reason or _text(entry_timing.get("reason")),
        "entry_timing_source": _text(visual_context.get("source")) if visual_alignment_conflict else _text(entry_timing.get("source")),
        "placement_context": _mapping_or_empty(entry_timing.get("placement_context")),
        "trigger_token": f"{candle_key}|{bias_side}",
    }


def _book_rule_signal_from_state(payload: Mapping[str, object]) -> dict[str, object]:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))
    book_signal = _mapping_or_empty(latest_signal.get("book_rule_action_signal_v3"))
    if _upper(book_signal.get("schema_version")) != "PG_BOOK_RULE_ACTION_SIGNAL_V3":
        return {}

    action = _upper(book_signal.get("action"))
    if action not in {"BUY", "SELL"}:
        return {}
    if not bool(book_signal.get("actionable")):
        return {}

    def first_value(*values: object) -> object:
        for value in values:
            if value not in (None, ""):
                return value
        return ""

    candle_key = _text(
        book_signal.get("closed_candle_key")
        or latest_signal.get("closed_candle_key")
        or thesis.get("closed_candle_key")
        or payload.get("closed_candle_key")
    )
    candle_sequence_raw = first_value(
        book_signal.get("closed_candle_sequence"),
        latest_signal.get("closed_candle_sequence"),
        thesis.get("closed_candle_sequence"),
        payload.get("closed_candle_sequence"),
    )
    candle_sequence = int(_float(candle_sequence_raw, -1.0)) if candle_sequence_raw not in (None, "") else -1
    candle_sequence_opt = candle_sequence if candle_sequence >= 0 else None
    if not candle_key and candle_sequence_opt is None:
        return {}

    published_epoch = _float(
        first_value(
            latest_signal.get("published_epoch"),
            payload.get("display_published_epoch"),
            payload.get("last_capture_epoch"),
            time.time(),
        ),
        0.0,
    )
    freshness_window_seconds = _float(latest_signal.get("freshness_window_sec"), 0.0)
    expiry_seconds = int(
        _float(
            first_value(
                latest_signal.get("expiry_seconds"),
                latest_signal.get("freshness_window_sec"),
                300.0,
            ),
            300.0,
        )
        or 300.0
    )
    valid_until_epoch = _float(
        first_value(
            latest_signal.get("valid_until_epoch"),
            payload.get("decision_valid_until_epoch"),
            published_epoch + max(1.0, float(expiry_seconds)),
        ),
        0.0,
    )
    timeframe_value = first_value(book_signal.get("timeframe"), thesis.get("timeframe"), payload.get("timeframe"), "M5")
    symbol_value = first_value(book_signal.get("pair"), payload.get("market"), thesis.get("symbol"), "USER_LOCKED_ACTIVE_CHART")
    timeframe_seconds = _timeframe_seconds_from_text(timeframe_value, default_seconds=300)
    playbook = _text(book_signal.get("playbook"))
    rule_traceability = _mapping_or_empty(book_signal.get("rule_traceability"))
    selected_book_rule_ids_raw = rule_traceability.get("selected_book_rule_ids")
    selected_book_rule_ids: list[object] = (
        list(cast(Sequence[object], selected_book_rule_ids_raw))
        if isinstance(selected_book_rule_ids_raw, Sequence) and not isinstance(selected_book_rule_ids_raw, (str, bytes, bytearray))
        else []
    )
    signal_id = _text(
        first_value(
            latest_signal.get("signal_id"),
            f"book-rule:{candle_key or candle_sequence_opt}:{action}:{playbook or 'UNRESOLVED'}",
        )
    )

    return {
        "signal_id": signal_id,
        "side": action,
        "actionable": True,
        "summary": _text(book_signal.get("scenario") or book_signal.get("trigger") or book_signal.get("status") or action),
        "symbol": _text(symbol_value),
        "timeframe": _text(timeframe_value),
        "expiry_seconds": max(1, expiry_seconds),
        "published_epoch": published_epoch,
        "valid_until_epoch": valid_until_epoch,
        "source": "direct_book_rule_signal",
        "entry_state": _text(book_signal.get("status") or "BOOK_RULE_ACTIONABLE"),
        "dominant_score": round(_float(book_signal.get("confidence"), 0.0), 4),
        "study_count": 1,
        "candle_key": candle_key,
        "candle_sequence": candle_sequence_opt,
        "timeframe_seconds": timeframe_seconds,
        "execution_permission": "",
        "signal_age_seconds": _float(latest_signal.get("signal_age_sec"), max(0.0, time.time() - published_epoch)),
        "freshness_window_seconds": freshness_window_seconds,
        "freshness_score": _float(latest_signal.get("freshness_score"), 0.0),
        "pipeline_latency_seconds": _float(latest_signal.get("pipeline_latency_sec"), 0.0),
        "playbook": playbook,
        "watch_side": _text(book_signal.get("watch_side")),
        "book_rule_ids": list(selected_book_rule_ids),
    }


def _high_frequency_signal_from_state(payload: Mapping[str, object]) -> dict[str, object]:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))
    market_study = _mapping_or_empty(latest_signal.get("market_study_v3"))
    high_frequency = _mapping_or_empty(
        latest_signal.get("high_frequency_forecast")
        or latest_signal.get("micro_candle_forecast")
    )
    two_candle = _mapping_or_empty(
        latest_signal.get("two_candle_study")
        or high_frequency.get("two_candle_study")
    )
    next_candle = _mapping_or_empty(two_candle.get("next_candle_forecast"))
    second_candle = _mapping_or_empty(two_candle.get("second_next_candle_forecast"))
    published_epoch = _float(
        latest_signal.get("published_epoch")
        or payload.get("display_published_epoch")
        or payload.get("last_capture_epoch")
        or time.time(),
        0.0,
    )

    if not high_frequency and not two_candle:
        return {
            "actionable": False,
            "reject_reason": (
                "Signal source high_frequency was selected, but the live payload has no "
                "high_frequency_forecast, micro_candle_forecast, or two_candle_study."
            ),
            "published_epoch": published_epoch,
            "signal_id": _text(latest_signal.get("signal_id") or payload.get("session_id")),
            "source": "direct_high_frequency_signal",
            "entry_state": _text(latest_signal.get("entry_state") or "HIGH_FREQUENCY_FORECAST"),
        }

    status = _upper(high_frequency.get("status") or two_candle.get("status"))
    if status != "READY":
        return {
            "actionable": False,
            "reject_reason": f"Signal source high_frequency is present but not READY (status={status or 'MISSING'}).",
            "published_epoch": published_epoch,
            "signal_id": _text(latest_signal.get("signal_id") or payload.get("session_id")),
            "source": "direct_high_frequency_signal",
            "entry_state": _text(latest_signal.get("entry_state") or "HIGH_FREQUENCY_FORECAST"),
            "high_frequency_status": status,
        }

    forecast_side = _mapping_side(
        high_frequency,
        "side",
        "action",
        "execution_action",
        "primary_pressure",
    )
    if forecast_side not in {"BUY", "SELL"}:
        forecast_side = _mapping_side(
            two_candle,
            "side",
            "action",
            "execution_action",
            "primary_pressure",
        )
    if forecast_side not in {"BUY", "SELL"}:
        forecast_side = _mapping_side(
            next_candle,
            "direction",
            "direction_bias",
            "side",
            "movement_side",
        )
    if forecast_side not in {"BUY", "SELL"}:
        forecast_side = _mapping_side(
            second_candle,
            "direction",
            "direction_bias",
            "side",
            "movement_side",
        )
    if forecast_side not in {"BUY", "SELL"}:
        return {
            "actionable": False,
            "reject_reason": "Signal source high_frequency is READY, but no BUY/SELL forecast side was present.",
            "published_epoch": published_epoch,
            "signal_id": _text(latest_signal.get("signal_id") or payload.get("session_id")),
            "source": "direct_high_frequency_signal",
            "entry_state": _text(latest_signal.get("entry_state") or "HIGH_FREQUENCY_FORECAST"),
            "high_frequency_status": status,
        }

    confidence = _float(
        high_frequency.get("confidence")
        or two_candle.get("confidence")
        or next_candle.get("confidence")
        or second_candle.get("confidence")
        or 0.0,
        0.0,
    )
    if confidence < HIGH_FREQUENCY_CONFIDENCE_FLOOR:
        return {
            "actionable": False,
            "reject_reason": (
                f"Signal source high_frequency is READY, but confidence {confidence:.3f} "
                f"is below floor {HIGH_FREQUENCY_CONFIDENCE_FLOOR:.3f}."
            ),
            "published_epoch": published_epoch,
            "signal_id": _text(latest_signal.get("signal_id") or payload.get("session_id")),
            "source": "direct_high_frequency_signal",
            "entry_state": _text(latest_signal.get("entry_state") or "HIGH_FREQUENCY_FORECAST"),
            "high_frequency_status": status,
        }

    signal_id = _text(
        latest_signal.get("signal_id")
        or f"high-frequency:{_text(market_study.get('closed_candle_key') or payload.get('closed_candle_key')) or _text(market_study.get('closed_candle_sequence') or payload.get('closed_candle_sequence'))}:{forecast_side}"
    )
    freshness_window_seconds = _float(latest_signal.get("freshness_window_sec"), 0.0)
    signal_age_seconds = _float(
        latest_signal.get("signal_age_sec"),
        max(0.0, time.time() - published_epoch),
    )
    valid_until_epoch = _float(
        latest_signal.get("valid_until_epoch")
        or payload.get("decision_valid_until_epoch")
        or (published_epoch + max(1.0, freshness_window_seconds or 300.0)),
        0.0,
    )
    expiry_seconds = int(
        _float(
            latest_signal.get("expiry_seconds")
            or latest_signal.get("required_seconds")
            or high_frequency.get("expiry_seconds")
            or high_frequency.get("recommended_expiry_seconds")
            or 180.0,
            180.0,
        )
        or 180.0
    )
    candle_key = _text(
        market_study.get("closed_candle_key")
        or latest_signal.get("closed_candle_key")
        or thesis.get("closed_candle_key")
        or payload.get("closed_candle_key")
    )
    candle_sequence_raw = (
        market_study.get("closed_candle_sequence")
        or latest_signal.get("closed_candle_sequence")
        or thesis.get("closed_candle_sequence")
        or payload.get("closed_candle_sequence")
    )
    candle_sequence = int(_float(candle_sequence_raw, -1.0)) if candle_sequence_raw is not None else -1
    candle_sequence_opt = candle_sequence if candle_sequence >= 0 else None
    if not candle_key and candle_sequence_opt is None:
        return {}

    summary = _text(
        two_candle.get("summary")
        or high_frequency.get("summary")
        or latest_signal.get("summary")
        or f"High-frequency next-candle forecast accepted {forecast_side}."
    )
    trigger_lane = _text(
        latest_signal.get("execution_lane")
        or high_frequency.get("lane")
        or "HIGH_FREQUENCY_TWO_CANDLE"
    )
    timeframe_value = _text(
        latest_signal.get("high_frequency_study_timeframe")
        or high_frequency.get("timeframe")
        or two_candle.get("timeframe")
        or thesis.get("timeframe")
        or payload.get("timeframe")
        or "M5"
    )
    symbol_value = _text(
        payload.get("market")
        or latest_signal.get("market")
        or thesis.get("symbol")
        or "USER_LOCKED_ACTIVE_CHART"
    )
    return {
        "signal_id": signal_id,
        "side": forecast_side,
        "actionable": True,
        "summary": summary,
        "symbol": symbol_value,
        "timeframe": timeframe_value,
        "expiry_seconds": max(1, expiry_seconds),
        "published_epoch": published_epoch,
        "valid_until_epoch": valid_until_epoch,
        "source": "direct_high_frequency_signal",
        "entry_state": _text(latest_signal.get("entry_state") or "HIGH_FREQUENCY_FORECAST"),
        "dominant_score": round(confidence, 4),
        "study_count": 1,
        "candle_key": candle_key,
        "candle_sequence": candle_sequence_opt,
        "timeframe_seconds": _timeframe_seconds_from_text(timeframe_value, default_seconds=300),
        "execution_permission": "",
        "signal_age_seconds": signal_age_seconds,
        "freshness_window_seconds": freshness_window_seconds,
        "freshness_score": _float(latest_signal.get("freshness_score"), 0.0),
        "pipeline_latency_seconds": _float(latest_signal.get("pipeline_latency_sec"), 0.0),
        "trigger_lane": trigger_lane,
        "high_frequency_status": status,
        "next_candle_bias": _text(
            next_candle.get("direction")
            or next_candle.get("direction_bias")
            or forecast_side
        ),
    }


def _entry_state_ready(value: object) -> bool:
    return _upper(value) in HYBRID_READY_ENTRY_STATES


def _timing_state_ready(timing_signal: Mapping[str, object]) -> bool:
    return _upper(
        timing_signal.get("state")
        or timing_signal.get("entry_state")
        or timing_signal.get("timing_state")
    ) in HYBRID_READY_ENTRY_STATES


def _mapping_side(mapping: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        side = _upper(mapping.get(key))
        if side in {"BUY", "SELL"}:
            return side
    return ""


def _hybrid_signal_from_state(payload: Mapping[str, object]) -> dict[str, object]:
    latest_signal = _mapping_or_empty(payload.get("latest_signal"))
    thesis = _mapping_or_empty(payload.get("signal_thesis_v3"))
    timing_signal = _mapping_or_empty(latest_signal.get("timing_signal"))
    book_signal = _mapping_or_empty(latest_signal.get("book_rule_action_signal_v3"))
    if _upper(book_signal.get("schema_version")) != "PG_BOOK_RULE_ACTION_SIGNAL_V3":
        return {}

    book_action = _upper(book_signal.get("action"))
    book_watch_side = _upper(book_signal.get("watch_side"))
    structure = _mapping_or_empty(book_signal.get("structure"))
    candlestick = _mapping_or_empty(book_signal.get("candlestick"))
    major_side = _upper(structure.get("major_side"))
    inner_side = _upper(structure.get("inner_side"))
    candle_side = _upper(candlestick.get("side"))
    allowed_sides: set[str] = set()
    if bool(book_signal.get("actionable")) and book_action in {"BUY", "SELL"}:
        allowed_sides.add(book_action)
    if book_watch_side in {"BUY", "SELL"}:
        if book_watch_side in {major_side, inner_side, candle_side} or (
            major_side == book_watch_side and inner_side == book_watch_side
        ):
            allowed_sides.add(book_watch_side)
    if not allowed_sides:
        return {}

    signal_id = _text(
        latest_signal.get("signal_id")
        or f"hybrid:{_text(book_signal.get('closed_candle_key')) or _text(book_signal.get('closed_candle_sequence'))}:{','.join(sorted(allowed_sides))}"
    )
    published_epoch = _float(
        latest_signal.get("published_epoch")
        or payload.get("display_published_epoch")
        or payload.get("last_capture_epoch")
        or time.time(),
        0.0,
    )
    freshness_window_seconds = _float(latest_signal.get("freshness_window_sec"), 0.0)
    signal_age_seconds = _float(
        latest_signal.get("signal_age_sec"),
        max(0.0, time.time() - published_epoch),
    )
    freshness_score = _float(latest_signal.get("freshness_score"), 0.0)
    pipeline_latency_seconds = _float(latest_signal.get("pipeline_latency_sec"), 0.0)
    valid_until_epoch = _float(
        latest_signal.get("valid_until_epoch")
        or payload.get("decision_valid_until_epoch")
        or (published_epoch + max(1.0, freshness_window_seconds or 300.0)),
        0.0,
    )
    candle_key = _text(
        book_signal.get("closed_candle_key")
        or latest_signal.get("closed_candle_key")
        or _mapping_or_empty(latest_signal.get("market_study_v3")).get("closed_candle_key")
        or thesis.get("closed_candle_key")
        or payload.get("closed_candle_key")
    )
    candle_sequence_raw = (
        book_signal.get("closed_candle_sequence")
        or latest_signal.get("closed_candle_sequence")
        or _mapping_or_empty(latest_signal.get("market_study_v3")).get("closed_candle_sequence")
        or thesis.get("closed_candle_sequence")
        or payload.get("closed_candle_sequence")
    )
    candle_sequence = int(_float(candle_sequence_raw, -1.0)) if candle_sequence_raw is not None else -1
    candle_sequence_opt = candle_sequence if candle_sequence >= 0 else None
    if not candle_key and candle_sequence_opt is None:
        return {}

    entry_state = _upper(latest_signal.get("entry_state"))
    ready_state = _entry_state_ready(entry_state) or _timing_state_ready(timing_signal)
    watch_state = entry_state in HYBRID_WATCH_ENTRY_STATES
    current_flow_ready = bool(
        timing_signal.get("current_flow_continuation_ready")
        or timing_signal.get("breakout_confirmation")
        or timing_signal.get("momentum_acceptance")
    )
    timing_score = _float(timing_signal.get("timing_score"), 0.0)

    def build_result(
        *,
        side: str,
        source: str,
        summary: str,
        confidence: float,
        expiry_seconds: int,
        trigger_lane: str,
    ) -> dict[str, object]:
        timeframe_value = _text(
            latest_signal.get("focus_timeframe")
            or book_signal.get("timeframe")
            or thesis.get("timeframe")
            or payload.get("timeframe")
            or "M5"
        )
        symbol_value = _text(
            payload.get("market")
            or book_signal.get("pair")
            or thesis.get("symbol")
            or "USER_LOCKED_ACTIVE_CHART"
        )
        return {
            "signal_id": signal_id,
            "side": side,
            "actionable": True,
            "summary": summary,
            "symbol": symbol_value,
            "timeframe": timeframe_value,
            "expiry_seconds": max(1, expiry_seconds),
            "published_epoch": published_epoch,
            "valid_until_epoch": valid_until_epoch,
            "source": source,
            "entry_state": _text(latest_signal.get("entry_state") or book_signal.get("status") or "HYBRID_TRIGGER"),
            "dominant_score": round(confidence, 4),
            "study_count": 1,
            "candle_key": candle_key,
            "candle_sequence": candle_sequence_opt,
            "timeframe_seconds": _timeframe_seconds_from_text(timeframe_value, default_seconds=300),
            "execution_permission": "",
            "signal_age_seconds": signal_age_seconds,
            "freshness_window_seconds": freshness_window_seconds,
            "freshness_score": freshness_score,
            "pipeline_latency_seconds": pipeline_latency_seconds,
            "playbook": _text(book_signal.get("playbook")),
            "watch_side": book_watch_side,
            "book_rule_ids": list(
                cast(Sequence[object], _mapping_or_empty(book_signal.get("rule_traceability")).get("selected_book_rule_ids"))
            )
            if isinstance(_mapping_or_empty(book_signal.get("rule_traceability")).get("selected_book_rule_ids"), Sequence)
            and not isinstance(_mapping_or_empty(book_signal.get("rule_traceability")).get("selected_book_rule_ids"), (str, bytes, bytearray))
            else [],
            "trigger_lane": trigger_lane,
            "allowed_sides": sorted(allowed_sides),
        }

    latest_signal_side = _mapping_side(
        latest_signal,
        "execution_action",
        "candidate_action",
        "action",
        "side",
    )
    if (
        bool(latest_signal.get("actionable"))
        and latest_signal_side in allowed_sides
    ):
        expiry_seconds = int(_float(latest_signal.get("expiry_seconds"), 180.0) or 180)
        confidence = _float(
            latest_signal.get("execution_confidence")
            or latest_signal.get("effective_confidence")
            or latest_signal.get("confidence")
            or latest_signal.get("market_confidence"),
            HYBRID_FAST_CONFIDENCE_FLOOR,
        )
        return build_result(
            side=latest_signal_side,
            source="direct_hybrid_live_trigger_signal",
            summary=_text(
                latest_signal.get("summary")
                or timing_signal.get("instruction")
                or f"Hybrid trigger accepted {latest_signal_side}."
            ),
            confidence=max(HYBRID_FAST_CONFIDENCE_FLOOR, confidence),
            expiry_seconds=expiry_seconds,
            trigger_lane=_text(latest_signal.get("execution_lane") or "LIVE_TRIGGER"),
        )

    countertrend_lane = _mapping_or_empty(latest_signal.get("countertrend_lane"))
    counter_side = _mapping_side(countertrend_lane, "side", "action", "execution_action", "candidate_action")
    if bool(countertrend_lane.get("actionable")) and counter_side in allowed_sides:
        countertrend_expiry_default = _float(latest_signal.get("expiry_seconds"), 180.0)
        return build_result(
            side=counter_side,
            source="direct_hybrid_countertrend_signal",
            summary=_text(countertrend_lane.get("reason") or countertrend_lane.get("message") or f"Hybrid countertrend scalp accepted {counter_side}."),
            confidence=max(HYBRID_FAST_CONFIDENCE_FLOOR, _float(countertrend_lane.get("confidence"), HYBRID_FAST_CONFIDENCE_FLOOR)),
            expiry_seconds=int(_float(countertrend_lane.get("expiry_seconds"), countertrend_expiry_default) or 180),
            trigger_lane="COUNTERTREND_SCALP",
        )

    high_frequency = _mapping_or_empty(
        latest_signal.get("high_frequency_forecast")
        or latest_signal.get("micro_candle_forecast")
    )
    two_candle = _mapping_or_empty(
        latest_signal.get("two_candle_study")
        or high_frequency.get("two_candle_study")
    )
    hf_side = _mapping_side(
        high_frequency,
        "side",
        "action",
        "execution_action",
        "primary_pressure",
    )
    if hf_side not in {"BUY", "SELL"}:
        hf_side = _mapping_side(two_candle, "side", "action", "execution_action", "primary_pressure")
    hf_confidence = _float(
        high_frequency.get("confidence")
        or two_candle.get("confidence")
        or 0.0,
        0.0,
    )
    hf_ready = _upper(high_frequency.get("status") or two_candle.get("status")) == "READY"
    if hf_ready and hf_side in allowed_sides and hf_confidence >= HYBRID_FAST_CONFIDENCE_FLOOR:
        if ready_state or current_flow_ready or (watch_state and timing_score >= 0.45):
            return build_result(
                side=hf_side,
                source="direct_hybrid_high_frequency_signal",
                summary=_text(
                    two_candle.get("summary")
                    or high_frequency.get("summary")
                    or f"Hybrid two-candle trigger accepted {hf_side}."
                ),
                confidence=hf_confidence,
                expiry_seconds=int(_float(latest_signal.get("expiry_seconds"), 180.0) or 180),
                trigger_lane="HIGH_FREQUENCY_TWO_CANDLE",
            )

    if book_action in {"BUY", "SELL"} and bool(book_signal.get("actionable")) and book_action in allowed_sides:
        if ready_state or current_flow_ready:
            return build_result(
                side=book_action,
                source="direct_hybrid_book_rule_ready_signal",
                summary=_text(book_signal.get("scenario") or book_signal.get("trigger") or book_signal.get("status") or book_action),
                confidence=max(HYBRID_FAST_CONFIDENCE_FLOOR, _float(book_signal.get("confidence"), HYBRID_FAST_CONFIDENCE_FLOOR)),
                expiry_seconds=int(_float(latest_signal.get("expiry_seconds"), 180.0) or 180),
                trigger_lane="BOOK_RULE_READY",
            )

    return {}


def _live_signal_from_state(payload: Mapping[str, object], *, signal_source: str = DEFAULT_SIGNAL_SOURCE) -> dict[str, object]:
    normalized_source = _text(signal_source).lower() or DEFAULT_SIGNAL_SOURCE
    if normalized_source in {"high_frequency", "high-frequency", "two_candle", "two-candle", "forecast"}:
        return _high_frequency_signal_from_state(payload)
    if normalized_source == "hybrid":
        return _hybrid_signal_from_state(payload)
    if normalized_source in {"book", "book_rule", "book_rules"}:
        return _book_rule_signal_from_state(payload)
    if normalized_source in {"bias", "study_bias", "live_bias"}:
        return _bias_signal_from_state(payload)
    return {}


def _calibration_manifest_paths(project_root: Path) -> list[Path]:
    def add_candidate(candidates: list[Path], seen: set[str], candidate: Path) -> None:
        key = os.path.normcase(str(candidate.expanduser()))
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate.expanduser())

    runtime_candidates: list[Path] = []
    seen_runtime_dirs: set[str] = set()

    def add_runtime_dir(candidate: Path | None) -> None:
        if candidate is None:
            return
        normalized = candidate.expanduser()
        key = os.path.normcase(str(normalized))
        if key in seen_runtime_dirs:
            return
        seen_runtime_dirs.add(key)
        runtime_candidates.append(normalized)

    add_runtime_dir(_default_calibration_dir(project_root))
    add_runtime_dir(_default_live_runtime_dir(project_root))
    runtime_lock_path = _runtime_lock_path()
    add_runtime_dir(runtime_lock_path.parent)
    runtime_lock = _read_runtime_lock()
    repo_root = _text(runtime_lock.get("repo_root"))
    if repo_root:
        add_runtime_dir(Path(repo_root).expanduser() / "runtime" / "live")
    data_dir = _text(runtime_lock.get("data_dir"))
    if data_dir:
        add_runtime_dir(Path(data_dir).expanduser().parent)
    add_runtime_dir(project_root / "runtime" / "live")
    add_runtime_dir(project_root / "runtime")

    paths: list[Path] = []
    seen_candidates: set[str] = set()
    candidates: list[Path] = []
    for runtime_dir in runtime_candidates:
        add_candidate(candidates, seen_candidates, runtime_dir / "trigger_calibration_manifest.json")
        add_candidate(candidates, seen_candidates, runtime_dir / "trigger_calibration_manifest.backup.json")
        add_candidate(candidates, seen_candidates, runtime_dir / "user_calibration_manifest.json")
        add_candidate(candidates, seen_candidates, runtime_dir / "data_live" / "user_calibration_manifest.json")
    add_candidate(candidates, seen_candidates, project_root / "trigger_calibration_manifest.json")
    add_candidate(candidates, seen_candidates, project_root / "user_calibration_manifest.json")
    add_candidate(candidates, seen_candidates, project_root / "data" / "user_calibration_manifest.json")
    for candidate in candidates:
        if candidate.exists():
            paths.append(candidate.resolve())
    return paths


def _load_calibration_manifest(calibration_path: str | None = None) -> dict[str, object]:
    project_root = PROJECT_ROOT
    manifest_path: Path | None = None
    discovered_paths = _calibration_manifest_paths(project_root)
    if calibration_path:
        manifest_path = Path(calibration_path).expanduser()
        if not manifest_path.exists() and discovered_paths:
            manifest_path = discovered_paths[0]
        elif not manifest_path.exists():
            raise MissingCalibration(
                "Calibration manifest not found at the requested path and no fallback manifest exists. "
                "Run Backend/launch/phoenixguard_trigger_calibration.py first."
            )
    if manifest_path is None:
        for candidate in discovered_paths:
            manifest_path = candidate
            break
        if manifest_path is None:
            raise MissingCalibration(
                "No calibration manifest found. Run Backend/launch/phoenixguard_trigger_calibration.py first."
            )

    return _load_json_file(manifest_path)


def _normalize_box(box: Any) -> tuple[int, int] | None:
    if isinstance(box, Mapping):
        mapping = cast(Mapping[str, object], box)
        x = mapping.get("x")
        y = mapping.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return int(x), int(y)
    if isinstance(box, Sequence) and not isinstance(box, (str, bytes, bytearray)):
        values = cast(Sequence[object], box)
        if len(values) >= 2:
            try:
                first = values[0]
                second = values[1]
                return int(_float(first, 0.0)), int(_float(second, 0.0))
            except (TypeError, ValueError):
                return None
    return None


def _coerce_box_mapping(payload: Mapping[str, object]) -> dict[str, tuple[int, int]]:
    boxes: dict[str, tuple[int, int]] = {}
    if not payload:
        return boxes
    settings = payload.get("settings") if isinstance(payload.get("settings"), Mapping) else payload
    mapping = cast(Mapping[str, object], settings)

    alias_map: dict[str, tuple[str, ...]] = {
        "buy_button": ("buy_button", "buy_click", "buy_icon", "buy"),
        "sell_button": ("sell_button", "sell_click", "sell_icon", "sell"),
        "time_button": ("time_button", "time_box", "time_input", "time_field", "time_click"),
        "buy_icon": ("buy_icon", "buy_button", "buy_click"),
        "sell_icon": ("sell_icon", "sell_button", "sell_click"),
    }

    for canonical_key, aliases in alias_map.items():
        for alias in aliases:
            if alias in mapping:
                candidate_value = mapping.get(alias)
                coords = _normalize_box(candidate_value)
                if coords is not None:
                    boxes[canonical_key] = coords
                    break

    nested_map = _as_mapping(mapping.get("boxes"))
    if nested_map:
        for canonical_key, aliases in alias_map.items():
            for alias in aliases:
                if alias in nested_map:
                    coords = _normalize_box(nested_map.get(alias))
                    if coords is not None:
                        boxes[canonical_key] = coords
                        break

    actions_map = _as_mapping(mapping.get("actions"))
    if actions_map:
        for side in ("buy", "sell"):
            action_entry_map = _as_mapping(actions_map.get(side))
            if not action_entry_map:
                continue
            click = _as_mapping(action_entry_map.get("click"))
            coords = _normalize_box(click)
            if coords is None:
                continue
            target_key = "buy_button" if side == "buy" else "sell_button"
            boxes[target_key] = coords

    chart_anchor = _as_mapping(mapping.get("chart_anchor"))
    if chart_anchor:
        coords = _normalize_box(chart_anchor)
        if coords is not None:
            boxes["chart_anchor"] = coords

    nested_anchor: Mapping[str, object] = _as_mapping(nested_map.get("chart_anchor")) if nested_map else {}
    if nested_anchor:
        coords = _normalize_box(nested_anchor)
        if coords is not None:
            boxes["chart_anchor"] = coords

    return boxes


def _load_boxes_from_manifest(manifest: Mapping[str, object]) -> dict[str, tuple[int, int]]:
    payload = manifest.get("calibration") if isinstance(manifest.get("calibration"), Mapping) else manifest
    candidate = cast(Mapping[str, object], payload)
    boxes = _coerce_box_mapping(candidate)
    if not boxes:
        boxes = _coerce_box_mapping(manifest)
    if not boxes:
        raise MissingCalibration("No calibrated buy/sell/time boxes found in the manifest.")
    return boxes


def _frontline_gate_context(
    payload: Mapping[str, object],
    signal: Mapping[str, object],
    *,
    now_epoch: float,
    required: bool,
    freshness_seconds: float,
) -> dict[str, object]:
    """Apply the Frontline Qwen veto gate.

    Qwen has veto power only: a fresh, matching verdict can block a trade the
    bridge wanted; it can never start one.  A missing, stale, or errored
    verdict never blocks by default (fail-safe).  When ``required`` is set the
    bridge waits for a fresh matching verdict instead of proceeding without it.
    """
    verdict = _mapping_or_empty(payload.get("frontline_reasoning_v3"))
    if not verdict:
        if required:
            raise TradeRejected(
                "Frontline Qwen verdict is required but no verdict has been published yet."
            )
        return {"applied": False, "reason": "no_verdict"}

    if _upper(verdict.get("schema_version")) != "PG_FRONTLINE_REASONING_V3":
        if required:
            raise TradeRejected("Frontline Qwen verdict has an incompatible schema.")
        return {"applied": False, "reason": "schema_mismatch"}

    state = _text(verdict.get("state")).lower()
    if state not in {"ok", "mock"}:
        if required:
            raise TradeRejected(
                _text(verdict.get("reason")) or f"Frontline Qwen verdict is unavailable (state={state})."
            )
        return {"applied": False, "reason": f"state:{state}"}

    observed_epoch = _float(verdict.get("observed_epoch"), 0.0)
    signal_epoch = _float(signal.get("observed_epoch"), _float(signal.get("published_epoch"), 0.0))
    verdict_age = max(0.0, now_epoch - _float(verdict.get("published_epoch"), now_epoch))
    candle_match = False
    verdict_candle_seq = verdict.get("candle_sequence")
    signal_candle_seq = signal.get("candle_sequence")
    if verdict_candle_seq is not None and signal_candle_seq is not None:
        candle_match = _int(verdict_candle_seq, -1) == _int(signal_candle_seq, -2)
    if candle_match:
        verdict_candle_key = _text(verdict.get("candle_key"))
        signal_candle_key = _text(signal.get("candle_key"))
        if verdict_candle_key and signal_candle_key:
            candle_match = verdict_candle_key == signal_candle_key

    age_window_ok = verdict_age <= max(0.0, float(freshness_seconds))
    epoch_close = (
        observed_epoch <= 0.0
        or signal_epoch <= 0.0
        or abs(observed_epoch - signal_epoch) <= max(30.0, float(freshness_seconds))
    )
    fresh = candle_match or (age_window_ok and epoch_close)
    if not fresh:
        if required:
            raise TradeRejected(
                "Frontline Qwen verdict has not caught up to the current candle yet."
            )
        return {"applied": False, "reason": "stale_or_mismatched"}

    verdict_kind = _text(verdict.get("verdict")).upper()
    verdict_side = _text(verdict.get("side")).upper()
    signal_side = _upper(signal.get("side"))
    vetoed = False
    veto_reason = ""
    if verdict_kind == "VETO":
        vetoed = True
        veto_reason = (
            _text(verdict.get("reason"))
            or f"Frontline Qwen vetoed the {signal_side or 'candidate'} entry."
        )
    elif verdict_side in {"BUY", "SELL"} and signal_side in {"BUY", "SELL"} and verdict_side != signal_side:
        vetoed = True
        veto_reason = (
            f"Frontline Qwen read {verdict_side} while the bridge resolved {signal_side}; "
            "wait for alignment before entering."
        )
    if vetoed:
        raise TradeRejected(veto_reason)

    return {
        "applied": True,
        "verdict": verdict_kind or "ALLOW",
        "side": verdict_side,
        "confidence": _float(verdict.get("confidence"), 0.0),
        "position_quality": _text(verdict.get("position_quality")),
        "reason": _text(verdict.get("reason")),
        "warnings": list(
            cast(Sequence[object], verdict.get("warnings"))
            if isinstance(verdict.get("warnings"), Sequence)
            and not isinstance(verdict.get("warnings"), (str, bytes, bytearray))
            else []
        ),
        "model": _text(verdict.get("model")),
        "state": state,
        "candle_match": candle_match,
        "age_seconds": round(verdict_age, 2),
    }


def _resolve_trade_payload(
    payload: Mapping[str, object],
    *,
    score_threshold: float = 0.0,
    max_signal_age_seconds: float = DEFAULT_MAX_SIGNAL_AGE_SECONDS,
    signal_source: str = DEFAULT_SIGNAL_SOURCE,
    frontline_required: bool = False,
    frontline_freshness_seconds: float = DEFAULT_FRONTLINE_FRESHNESS_SECONDS,
) -> dict[str, object]:
    signal = _live_signal_from_state(payload, signal_source=signal_source)
    if not signal:
        raise TradeRejected("No actionable live signal available from PhoenixGuard observation state.")
    now_epoch = time.time()
    freshness = _freshness_context(payload, signal, now_epoch=now_epoch)
    signal_age_seconds = _float(freshness.get("effective_signal_age_seconds"), 0.0)
    if max_signal_age_seconds > 0.0 and signal_age_seconds > max_signal_age_seconds:
        raise TradeRejected(
            f"Live signal stale before the direct bridge executed (age={signal_age_seconds:.3f}s max={float(max_signal_age_seconds):.3f}s).",
            details={**freshness, "max_signal_age_seconds": float(max_signal_age_seconds)},
        )
    freshness_score = _float(freshness.get("freshness_score"), 0.0)
    freshness_window_seconds = _float(freshness.get("freshness_window_seconds"), 0.0)
    if freshness_window_seconds > 0.0 and signal_age_seconds > freshness_window_seconds:
        raise TradeRejected(
            f"Live signal exceeded PhoenixGuard freshness window (age={signal_age_seconds:.3f}s window={freshness_window_seconds:.3f}s).",
            details=freshness,
        )
    if freshness_window_seconds > 0.0 and freshness_score <= 0.0:
        raise TradeRejected("Live signal is stale according to PhoenixGuard freshness tracking.", details=freshness)
    valid_until = _float(signal.get("valid_until_epoch"), 0.0)
    if valid_until > 0.0 and valid_until < now_epoch:
        raise TradeRejected("Live signal expired before the direct bridge executed.", details=freshness)
    if not bool(signal.get("actionable", True)):
        raise TradeRejected(
            _text(signal.get("reject_reason")) or "No actionable live signal available from PhoenixGuard observation state."
        )

    score = _float(signal.get("dominant_score"), _weighted_signal_score(payload))
    _ = score_threshold
    expiry_value = signal.get("expiry_seconds")
    expiry_seconds = int(_float(expiry_value, 300.0) or 300.0)
    frontline_context = _frontline_gate_context(
        payload,
        signal,
        now_epoch=now_epoch,
        required=frontline_required,
        freshness_seconds=frontline_freshness_seconds,
    )
    return {
        "signal_id": _text(signal.get("signal_id")),
        "side": _upper(signal.get("side")),
        "symbol": _text(signal.get("symbol")),
        "timeframe": _text(signal.get("timeframe")),
        "expiry_seconds": max(1, expiry_seconds),
        "published_epoch": _float(signal.get("published_epoch"), 0.0),
        "valid_until_epoch": valid_until,
        "summary": _text(signal.get("summary")),
        "source": _text(signal.get("source")),
        "entry_state": _text(signal.get("entry_state")),
        "execution_permission": _text(signal.get("execution_permission")),
        "weight_score": round(score, 4),
        "candle_key": _text(signal.get("candle_key")),
        "candle_sequence": signal.get("candle_sequence"),
        "timeframe_seconds": int(_float(signal.get("timeframe_seconds"), 300.0) or 300.0),
        "signal_age_seconds": round(signal_age_seconds, 3),
        "published_age_seconds": _float(freshness.get("published_age_seconds"), 0.0),
        "capture_age_seconds": _float(freshness.get("capture_age_seconds"), 0.0),
        "display_capture_age_seconds": _float(freshness.get("display_capture_age_seconds"), 0.0),
        "freshness_window_seconds": freshness_window_seconds,
        "freshness_score": freshness_score,
        "pipeline_latency_seconds": _float(freshness.get("pipeline_latency_seconds"), 0.0),
        "playbook": _text(signal.get("playbook")),
        "watch_side": _text(signal.get("watch_side")),
        "book_rule_ids": list(cast(Sequence[object], signal.get("book_rule_ids"))) if isinstance(signal.get("book_rule_ids"), Sequence) and not isinstance(signal.get("book_rule_ids"), (str, bytes, bytearray)) else [],
        "trigger_lane": _text(signal.get("trigger_lane")),
        "trigger_token": _text(signal.get("trigger_token")),
        "current_visual_side": _text(signal.get("current_visual_side")),
        "current_visual_source": _text(signal.get("current_visual_source")),
        "current_visual_stage": _text(signal.get("current_visual_stage")),
        "entry_timing_ready": bool(signal.get("entry_timing_ready", True)),
        "entry_timing_state": _text(signal.get("entry_timing_state")),
        "entry_timing_class": _text(signal.get("entry_timing_class")),
        "entry_timing_reason": _text(signal.get("entry_timing_reason")),
        "entry_timing_source": _text(signal.get("entry_timing_source")),
        "placement_context": _mapping_or_empty(signal.get("placement_context")),
        "next_candle_bias": _text(signal.get("next_candle_bias")),
        "high_frequency_status": _text(signal.get("high_frequency_status")),
        "frontline_applied": bool(frontline_context.get("applied")),
        "frontline_verdict": _text(frontline_context.get("verdict")),
        "frontline_side": _text(frontline_context.get("side")),
        "frontline_confidence": _float(frontline_context.get("confidence"), 0.0),
        "frontline_position_quality": _text(frontline_context.get("position_quality")),
        "frontline_reason": _text(frontline_context.get("reason")),
        "frontline_warnings": list(
            cast(Sequence[object], frontline_context.get("warnings"))
            if isinstance(frontline_context.get("warnings"), Sequence)
            and not isinstance(frontline_context.get("warnings"), (str, bytes, bytearray))
            else []
        ),
        "frontline_model": _text(frontline_context.get("model")),
        "frontline_state": _text(frontline_context.get("state")),
        "frontline_candle_match": bool(frontline_context.get("candle_match")),
        "frontline_age_seconds": round(_float(frontline_context.get("age_seconds"), 0.0), 2),
    }


def _timing_policy_from_manifest(manifest: Mapping[str, object]) -> dict[str, float]:
    timing = _as_mapping(manifest.get("timing_policy"))
    return {
        "chart_focus_settle_seconds": max(
            0.0,
            _float(timing.get("chart_focus_settle_seconds"), DEFAULT_CHART_FOCUS_SETTLE_SECONDS),
        ),
        "pre_click_delay_seconds": max(
            0.0,
            _float(timing.get("pre_click_delay_seconds"), DEFAULT_PRE_CLICK_DELAY_SECONDS),
        ),
        "inter_click_delay_seconds": max(
            0.0,
            _float(timing.get("inter_click_delay_seconds"), DEFAULT_INTER_CLICK_DELAY_SECONDS),
        ),
        "pointer_move_duration_seconds": max(
            0.0,
            _float(timing.get("pointer_move_duration_seconds"), DEFAULT_POINTER_MOVE_DURATION_SECONDS),
        ),
    }


def _send_direct_clicks(
    boxes: Mapping[str, tuple[int, int]],
    *,
    chart_anchor: tuple[int, int] | None,
    side: str,
    expiry_seconds: int,
    fixed_amount: float | None = None,
    timing_policy: Mapping[str, float] | None = None,
    refresh_trade_before_click: Callable[[], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover - environment-dependent.
        raise RuntimeError("pyautogui is required for raw broker clicks; install it in the active environment.") from exc

    timing = dict(timing_policy or {})
    move_duration = max(0.0, _float(timing.get("pointer_move_duration_seconds"), DEFAULT_POINTER_MOVE_DURATION_SECONDS))
    focus_settle_seconds = max(0.0, _float(timing.get("chart_focus_settle_seconds"), DEFAULT_CHART_FOCUS_SETTLE_SECONDS))
    pre_click_delay_seconds = max(0.0, _float(timing.get("pre_click_delay_seconds"), DEFAULT_PRE_CLICK_DELAY_SECONDS))
    inter_click_delay_seconds = max(0.0, _float(timing.get("inter_click_delay_seconds"), DEFAULT_INTER_CLICK_DELAY_SECONDS))

    if chart_anchor is not None:
        cx, cy = chart_anchor
        pyautogui.moveTo(cx, cy, duration=move_duration)
        pyautogui.click(cx, cy)
        chart_hold_seconds = max(focus_settle_seconds, pre_click_delay_seconds)
        if chart_hold_seconds > 0.0:
            time.sleep(chart_hold_seconds)
    elif pre_click_delay_seconds > 0.0:
        time.sleep(pre_click_delay_seconds)

    effective_side = _upper(side)
    refreshed = False
    if refresh_trade_before_click is not None:
        refreshed_trade = refresh_trade_before_click()
        refreshed_side = _upper(refreshed_trade.get("side"))
        if refreshed_side not in {"BUY", "SELL"}:
            raise TradeRejected("PhoenixGuard did not publish a fresh BUY or SELL at click time.")
        effective_side = refreshed_side
        refreshed = True

    target = "buy_click" if effective_side == "BUY" else "sell_click"
    key = target if target in boxes else ("buy_button" if effective_side == "BUY" else "sell_button")
    if key not in boxes:
        raise MissingCalibration(f"No calibrated {effective_side} click target found.")

    x, y = boxes[key]
    pyautogui.moveTo(x, y, duration=move_duration)
    # Every fired trade presses the same position twice: two contracts for this
    # specific entry at the exact window PhoenixGuard mapped.
    pyautogui.click(x, y)
    if inter_click_delay_seconds > 0.0:
        time.sleep(inter_click_delay_seconds)
    pyautogui.click(x, y)

    # Time and amount remain fixed in the Pocket Option setup. The value is logged but not typed here.
    _ = expiry_seconds
    _ = fixed_amount
    return {
        "executed_side": effective_side,
        "refreshed_before_click": refreshed,
        "press_count": 2,
    }


def _trigger_manifest_to_boxes(
    manifest: Mapping[str, object],
) -> tuple[dict[str, tuple[int, int]], tuple[int, int] | None, float, int, dict[str, float]]:
    trigger = _as_mapping(manifest.get("boxes")) if isinstance(manifest.get("boxes"), Mapping) else manifest
    chart_anchor = None
    if isinstance(trigger.get("chart_anchor"), Mapping):
        chart_mapping = cast(Mapping[str, object], trigger.get("chart_anchor"))
        x = chart_mapping.get("x")
        y = chart_mapping.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            chart_anchor = (int(x), int(y))

    boxes = _load_boxes_from_manifest(manifest)
    if not boxes:
        boxes = {}
        for key in ("buy_click", "sell_click", "buy_button", "sell_button"):
            value = trigger.get(key)
            coords = _normalize_box(value)
            if coords is not None:
                boxes[key] = coords

    if "buy_click" not in boxes and "buy_button" in boxes:
        boxes["buy_click"] = boxes["buy_button"]
    if "sell_click" not in boxes and "sell_button" in boxes:
        boxes["sell_click"] = boxes["sell_button"]

    fixed_amount = _float(manifest.get("fixed_amount"), 1.0)
    fixed_expiry = int(_float(manifest.get("fixed_expiry_seconds"), 60.0) or 60)
    timing_policy = _timing_policy_from_manifest(manifest)
    return boxes, chart_anchor, fixed_amount, fixed_expiry, timing_policy


def _execute_trade(
    *,
    trade: Mapping[str, object],
    calibration_path: str | None,
    fixed_expiry_seconds_override: int | None = None,
    refresh_trade_before_click: Callable[[], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    manifest = _load_calibration_manifest(calibration_path)
    boxes, chart_anchor, fixed_amount, fixed_expiry, timing_policy = _trigger_manifest_to_boxes(manifest)
    effective_fixed_expiry = fixed_expiry
    if fixed_expiry_seconds_override is not None and int(fixed_expiry_seconds_override) > 0:
        effective_fixed_expiry = int(fixed_expiry_seconds_override)
    expiry_seconds = int(_float(trade.get("expiry_seconds"), float(effective_fixed_expiry)) or float(effective_fixed_expiry))
    expiry_seconds = max(1, effective_fixed_expiry)
    click_meta = _send_direct_clicks(
        boxes,
        chart_anchor=chart_anchor,
        side=str(trade["side"]),
        expiry_seconds=expiry_seconds,
        fixed_amount=fixed_amount,
        timing_policy=timing_policy,
        refresh_trade_before_click=refresh_trade_before_click,
    )
    return {
        "fixed_expiry_seconds": effective_fixed_expiry,
        "fixed_amount": fixed_amount,
        "timing_policy": timing_policy,
        **click_meta,
    }


def _trade_once(
    *,
    base_url: str,
    session_id: str,
    calibration_path: str | None,
    dry_run: bool,
    score_threshold: float = 0.62,
    timeout_sec: float = DEFAULT_TIMEOUT_SECONDS,
    execute_trade: bool = True,
    fixed_expiry_seconds_override: int | None = None,
    max_signal_age_seconds: float = DEFAULT_MAX_SIGNAL_AGE_SECONDS,
    signal_source: str = DEFAULT_SIGNAL_SOURCE,
    frontline_required: bool = False,
    frontline_freshness_seconds: float = DEFAULT_FRONTLINE_FRESHNESS_SECONDS,
) -> dict[str, object]:
    resolved_base_url, resolved_session_id = _resolve_stack_context(base_url=base_url, session_id=session_id)
    payload, trade = _read_fresh_trade(
        base_url=resolved_base_url,
        session_id=resolved_session_id,
        timeout_sec=timeout_sec,
        score_threshold=score_threshold,
        max_signal_age_seconds=max_signal_age_seconds,
        signal_source=signal_source,
        frontline_required=frontline_required,
        frontline_freshness_seconds=frontline_freshness_seconds,
    )
    if fixed_expiry_seconds_override is not None and int(fixed_expiry_seconds_override) > 0:
        trade["expiry_seconds"] = int(fixed_expiry_seconds_override)
    if dry_run or not execute_trade:
        return {
            "dry_run": True,
            **trade,
            "base_url": resolved_base_url,
            "session_id": resolved_session_id,
            "state_source": _text(payload.get("_bridge_state_source")),
            "state_epoch": _float(payload.get("_bridge_state_epoch"), 0.0),
        }

    refreshed_trade: dict[str, object] = {}

    def refresh_before_click() -> Mapping[str, object]:
        _, latest_trade = _read_fresh_trade(
            base_url=resolved_base_url,
            session_id=resolved_session_id,
            timeout_sec=timeout_sec,
            score_threshold=score_threshold,
            max_signal_age_seconds=max_signal_age_seconds,
            signal_source=signal_source,
            frontline_required=frontline_required,
            frontline_freshness_seconds=frontline_freshness_seconds,
        )
        if fixed_expiry_seconds_override is not None and int(fixed_expiry_seconds_override) > 0:
            latest_trade["expiry_seconds"] = int(fixed_expiry_seconds_override)
        refreshed_trade.clear()
        refreshed_trade.update(latest_trade)
        return refreshed_trade

    exec_meta = _execute_trade(
        trade=trade,
        calibration_path=calibration_path,
        fixed_expiry_seconds_override=fixed_expiry_seconds_override,
        refresh_trade_before_click=refresh_before_click,
    )
    initial_side = _upper(trade.get("side"))
    if refreshed_trade:
        trade = dict(refreshed_trade)
        trade["initial_side"] = initial_side
    return {
        "dry_run": False,
        **trade,
        **exec_meta,
        "base_url": resolved_base_url,
        "session_id": resolved_session_id,
        "state_source": _text(payload.get("_bridge_state_source")),
        "state_epoch": _float(payload.get("_bridge_state_epoch"), 0.0),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phoenixguard_direct_trade_bridge.py",
        description=(
            "Direct trade bridge for PhoenixGuard. It listens to PhoenixGuard's live session stream "
            "and trades only a fresh current visual bias through the user-calibrated trigger boxes."
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument(
        "--transport",
        choices=("listener", "poll"),
        default="listener",
        help="Use PhoenixGuard's producer stream (default); poll is compatibility-only.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="Compatibility polling cadence; ignored by the default listener transport.",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--calibration-path", default="")
    parser.add_argument("--trigger-manifest", default="")
    parser.add_argument("--score-threshold", type=float, default=0.0)
    parser.add_argument("--max-signal-age-seconds", type=float, default=DEFAULT_MAX_SIGNAL_AGE_SECONDS)
    parser.add_argument("--signal-source", choices=("high_frequency", "hybrid", "book_rules", "bias"), default=DEFAULT_SIGNAL_SOURCE)
    parser.add_argument("--rearm-seconds", type=float, default=0.0)
    parser.add_argument("--flip-guard-seconds", type=float, default=DEFAULT_FLIP_GUARD_SECONDS)
    parser.add_argument("--fixed-expiry-seconds", type=int, default=180)
    parser.add_argument("--max-trades-per-candle", type=int, default=DEFAULT_MAX_TRADES_PER_CANDLE)
    parser.add_argument(
        "--max-trades-per-session",
        type=int,
        default=DEFAULT_MAX_TRADES_PER_SESSION,
        help="Hard cap on executed trades for this bridge process; 0 disables the cap.",
    )
    parser.add_argument("--block-opposite-side-same-candle", action="store_true")
    parser.add_argument("--cooldown-after-trades", type=int, default=DEFAULT_COOLDOWN_AFTER_TRADES)
    parser.add_argument("--cooldown-seconds", type=float, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--allow-opposite-side-same-candle", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--frontline-required",
        action="store_true",
        help="Block every entry until a fresh matching Frontline Qwen verdict is published (strict mode).",
    )
    parser.add_argument(
        "--frontline-freshness-seconds",
        type=float,
        default=DEFAULT_FRONTLINE_FRESHNESS_SECONDS,
        help="Maximum age of a Frontline Qwen verdict before it is treated as stale.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    args.base_url, args.session_id = _resolve_stack_context(
        base_url=args.base_url or None,
        session_id=args.session_id or None,
    )

    selected_manifest_path = _text(args.trigger_manifest) or _text(args.calibration_path) or None

    instance_lock = _InstanceLock(session_id=args.session_id)
    try:
        instance_lock.acquire()
        if args.once:
            try:
                if str(args.transport) == "listener":
                    listener = _iter_phoenixguard_session_updates(
                        base_url=str(args.base_url),
                        session_id=str(args.session_id),
                        timeout_sec=max(3.0, float(args.timeout)),
                    )
                    result = _trade_from_listener_payload(
                        next(listener),
                        base_url=str(args.base_url),
                        session_id=str(args.session_id),
                        score_threshold=float(args.score_threshold),
                        fixed_expiry_seconds_override=int(args.fixed_expiry_seconds),
                        max_signal_age_seconds=float(args.max_signal_age_seconds),
                        signal_source=str(args.signal_source),
                        frontline_required=bool(args.frontline_required),
                        frontline_freshness_seconds=float(args.frontline_freshness_seconds),
                    )
                else:
                    result = _trade_once(
                        base_url=args.base_url,
                        session_id=args.session_id,
                        calibration_path=selected_manifest_path,
                        dry_run=args.dry_run,
                        score_threshold=args.score_threshold,
                        timeout_sec=max(0.25, float(args.timeout)),
                        fixed_expiry_seconds_override=int(args.fixed_expiry_seconds),
                        max_signal_age_seconds=float(args.max_signal_age_seconds),
                        signal_source=str(args.signal_source),
                        frontline_required=bool(args.frontline_required),
                        frontline_freshness_seconds=float(args.frontline_freshness_seconds),
                    )
            except TradeRejected as exc:
                wait_payload: dict[str, object] = {"triggered": False, "reason": str(exc)}
                if exc.details:
                    wait_payload["freshness"] = dict(exc.details)
                print(json.dumps(wait_payload, sort_keys=True, ensure_ascii=True))
                return 0
            print(json.dumps(result, sort_keys=True, ensure_ascii=True))
            return 0

        trigger_state = _BridgeTriggerState(
            rearm_seconds=float(args.rearm_seconds),
            flip_guard_seconds=float(args.flip_guard_seconds),
            max_trades_per_candle=int(args.max_trades_per_candle),
            max_trades_per_session=int(args.max_trades_per_session),
            lock_side_per_candle=bool(args.block_opposite_side_same_candle)
            and not bool(args.allow_opposite_side_same_candle),
            cooldown_after_trades=int(args.cooldown_after_trades),
            cooldown_seconds=float(args.cooldown_seconds),
        )
        session_listener: Iterator[dict[str, object]] | None = (
            _iter_phoenixguard_session_updates(
                base_url=str(args.base_url),
                session_id=str(args.session_id),
                timeout_sec=max(3.0, float(args.timeout)),
            )
            if str(args.transport) == "listener"
            else None
        )
        while True:
            try:
                if session_listener is not None:
                    result = _trade_from_listener_payload(
                        next(session_listener),
                        base_url=str(args.base_url),
                        session_id=str(args.session_id),
                        score_threshold=float(args.score_threshold),
                        fixed_expiry_seconds_override=int(args.fixed_expiry_seconds),
                        max_signal_age_seconds=float(args.max_signal_age_seconds),
                        signal_source=str(args.signal_source),
                        frontline_required=bool(args.frontline_required),
                        frontline_freshness_seconds=float(args.frontline_freshness_seconds),
                    )
                else:
                    result = _trade_once(
                        base_url=args.base_url,
                        session_id=args.session_id,
                        calibration_path=selected_manifest_path,
                        dry_run=args.dry_run,
                        score_threshold=args.score_threshold,
                        timeout_sec=max(0.25, float(args.timeout)),
                        execute_trade=False,
                        fixed_expiry_seconds_override=int(args.fixed_expiry_seconds),
                        max_signal_age_seconds=float(args.max_signal_age_seconds),
                        signal_source=str(args.signal_source),
                        frontline_required=bool(args.frontline_required),
                        frontline_freshness_seconds=float(args.frontline_freshness_seconds),
                    )
                should_fire, reason = trigger_state.should_trigger(result)
                if not should_fire:
                    print(
                        json.dumps(
                            {
                                "triggered": False,
                                "reason": reason,
                                "side": _upper(result.get("side")),
                                "signal_id": _text(result.get("signal_id")),
                                "candle_key": _text(result.get("candle_key")),
                                "candle_sequence": result.get("candle_sequence"),
                            },
                            sort_keys=True,
                            ensure_ascii=True,
                        )
                    )
                    if session_listener is None:
                        time.sleep(max(0.1, float(args.poll_seconds)))
                    continue

                if args.dry_run:
                    print(json.dumps({"triggered": True, **result}, sort_keys=True, ensure_ascii=True))
                else:
                    refreshed_trade: dict[str, object] = {}

                    def refresh_before_click() -> Mapping[str, object]:
                        latest_result = _trade_once(
                            base_url=args.base_url,
                            session_id=args.session_id,
                            calibration_path=selected_manifest_path,
                            dry_run=True,
                            score_threshold=args.score_threshold,
                            timeout_sec=max(0.25, float(args.timeout)),
                            execute_trade=False,
                            fixed_expiry_seconds_override=int(args.fixed_expiry_seconds),
                            max_signal_age_seconds=float(args.max_signal_age_seconds),
                            signal_source=str(args.signal_source),
                            frontline_required=bool(args.frontline_required),
                            frontline_freshness_seconds=float(args.frontline_freshness_seconds),
                        )
                        refreshed_trade.clear()
                        refreshed_trade.update(latest_result)
                        return refreshed_trade

                    initial_side = _upper(result.get("side"))
                    exec_meta = _execute_trade(
                        trade=result,
                        calibration_path=selected_manifest_path,
                        fixed_expiry_seconds_override=int(args.fixed_expiry_seconds),
                        refresh_trade_before_click=refresh_before_click,
                    )
                    if refreshed_trade:
                        result = dict(refreshed_trade)
                        result["initial_side"] = initial_side
                    trigger_state.record_trade_execution(result)
                    print(json.dumps({"triggered": True, **result, **exec_meta, "dry_run": False}, sort_keys=True, ensure_ascii=True))
            except TradeRejected as exc:
                error_payload: dict[str, object] = {"triggered": False, "reason": str(exc)}
                if getattr(exc, "details", None):
                    error_payload["freshness"] = dict(exc.details)
                print(json.dumps(error_payload, sort_keys=True, ensure_ascii=True))
            except MissingCalibration as exc:
                print(json.dumps({"triggered": False, "reason": f"missing calibration: {exc}"}, sort_keys=True, ensure_ascii=True))
            except (RuntimeError, StopIteration) as exc:
                if session_listener is None:
                    raise
                print(
                    json.dumps(
                        {
                            "triggered": False,
                            "reason": str(exc) or "PhoenixGuard listener stream ended.",
                            "listener": "reconnecting",
                        },
                        sort_keys=True,
                        ensure_ascii=True,
                    )
                )
                time.sleep(DEFAULT_LISTENER_RECONNECT_SECONDS)
                session_listener = _iter_phoenixguard_session_updates(
                    base_url=str(args.base_url),
                    session_id=str(args.session_id),
                    timeout_sec=max(3.0, float(args.timeout)),
                )
            if session_listener is None:
                time.sleep(max(0.1, float(args.poll_seconds)))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover - CLI error path.
        print(json.dumps({"triggered": False, "error": str(exc)}, sort_keys=True, ensure_ascii=True), file=sys.stderr)
        return 1
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
