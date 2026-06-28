from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import Literal, cast
from uuid import uuid4

from phoenixguard.paths import PROJECT_ROOT

LOCK_SCHEMA_VERSION = "PG_RUNTIME_SINGLETON_GUARD_V3"
DEFAULT_SESSION_ID = "pocket-live-8788"
DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_API_PORT = 8793
DEFAULT_STALE_AFTER_MS = 30_000
ComponentName = Literal["launcher", "tracker", "api", "shooter"]


@dataclass(frozen=True)
class RuntimeLockAssessmentV3:
    healthy: bool
    stale: bool
    reason: str
    alive_pids: tuple[int, ...]
    dead_pids: tuple[int, ...]
    heartbeat_age_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "stale": self.stale,
            "reason": self.reason,
            "alive_pids": list(self.alive_pids),
            "dead_pids": list(self.dead_pids),
            "heartbeat_age_ms": self.heartbeat_age_ms,
        }


@dataclass(frozen=True)
class RuntimeGuardResultV3:
    ok: bool
    reason: str
    lock_path: Path
    lock: dict[str, object]
    owner_token: str
    assessment: RuntimeLockAssessmentV3 | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "lock_path": str(self.lock_path),
            "lock": dict(self.lock),
            "owner_token": self.owner_token,
            "assessment": self.assessment.as_dict() if self.assessment else {},
        }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return int(default)


def _default_repo_root() -> Path:
    return PROJECT_ROOT


def default_lock_path(repo_root: Path | None = None) -> Path:
    root = repo_root or _default_repo_root()
    runtime_dir = os.getenv("PHOENIXGUARD_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "phoenixguard_stack.lock.json"
    return root / "runtime" / "live" / "phoenixguard_stack.lock.json"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except Exception:
            return False
        return completed.returncode == 0
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _port_accepts(host: str, port: int, *, timeout_sec: float = 0.15) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, object]:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return dict(cast(Mapping[str, object], parsed))


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        temp_path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8")
        last_error: OSError | None = None
        for attempt in range(8):
            try:
                temp_path.replace(path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.025 * float(attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _component_pid_keys() -> tuple[str, ...]:
    return ("launcher_pid", "tracker_pid", "api_pid", "shooter_pid")


def _lock_pids(lock: Mapping[str, object]) -> tuple[int, ...]:
    pids: list[int] = []
    for key in _component_pid_keys():
        pid = _int(lock.get(key), 0)
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return tuple(pids)


class PhoenixRuntimeSingletonGuardV3:
    def __init__(
        self,
        *,
        lock_path: Path | None = None,
        repo_root: Path | None = None,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
        pid_probe: Callable[[int], bool] = _pid_alive,
        port_probe: Callable[[str, int], bool] = _port_accepts,
    ) -> None:
        self.repo_root = repo_root or _default_repo_root()
        self.lock_path = lock_path or default_lock_path(self.repo_root)
        self.stale_after_ms = max(1_000, int(stale_after_ms))
        self._pid_probe = pid_probe
        self._port_probe = port_probe

    @classmethod
    def for_repo(cls, repo_root: Path) -> PhoenixRuntimeSingletonGuardV3:
        return cls(repo_root=repo_root, lock_path=default_lock_path(repo_root))

    def read_lock(self) -> dict[str, object]:
        return _read_json(self.lock_path)

    def assess(self, lock: Mapping[str, object] | None = None) -> RuntimeLockAssessmentV3:
        row = dict(lock or self.read_lock())
        if not row:
            return RuntimeLockAssessmentV3(False, True, "missing_lock", (), (), self.stale_after_ms + 1)
        now_ms = _now_ms()
        heartbeat = _int(row.get("heartbeat_epoch_ms") or row.get("created_epoch_ms"), 0)
        heartbeat_age = max(0, now_ms - heartbeat) if heartbeat > 0 else self.stale_after_ms + 1
        alive: list[int] = []
        dead: list[int] = []
        for pid in _lock_pids(row):
            if self._pid_probe(pid):
                alive.append(pid)
            else:
                dead.append(pid)
        api_port = _int(row.get("api_port"), DEFAULT_API_PORT)
        base_url = _text(row.get("base_url"), DEFAULT_BASE_URL)
        host = "127.0.0.1"
        if "://" in base_url:
            try:
                host_port = base_url.split("://", 1)[1].split("/", 1)[0]
                host = host_port.rsplit(":", 1)[0] or host
            except IndexError:
                host = "127.0.0.1"
        port_open = self._port_probe(host, api_port)
        stale = bool(heartbeat_age > self.stale_after_ms or (not alive and not port_open))
        healthy = bool(not stale and (alive or port_open))
        if healthy:
            reason = "healthy_lock"
        elif heartbeat_age > self.stale_after_ms:
            reason = "stale_heartbeat"
        elif not alive and not port_open:
            reason = "dead_pids_and_closed_port"
        else:
            reason = "unhealthy_lock"
        return RuntimeLockAssessmentV3(
            healthy=healthy,
            stale=stale,
            reason=reason,
            alive_pids=tuple(alive),
            dead_pids=tuple(dead),
            heartbeat_age_ms=heartbeat_age,
        )

    def acquire(
        self,
        *,
        session_id: str = DEFAULT_SESSION_ID,
        base_url: str = DEFAULT_BASE_URL,
        data_dir: str | Path = "runtime/live/data_live",
        api_port: int = DEFAULT_API_PORT,
        launcher_pid: int | None = None,
        tracker_pid: int | None = None,
        api_pid: int | None = None,
        shooter_pid: int | None = None,
        takeover_stale: bool = True,
    ) -> RuntimeGuardResultV3:
        existing = self.read_lock()
        if existing:
            assessment = self.assess(existing)
            if assessment.healthy:
                return RuntimeGuardResultV3(False, "active_stack_lock_exists", self.lock_path, existing, _text(existing.get("owner_token")), assessment)
            if not takeover_stale:
                return RuntimeGuardResultV3(False, f"stale_stack_lock_exists:{assessment.reason}", self.lock_path, existing, _text(existing.get("owner_token")), assessment)
        else:
            assessment = None
        now_ms = _now_ms()
        owner_token = uuid4().hex
        payload: dict[str, object] = {
            "schema_version": LOCK_SCHEMA_VERSION,
            "session_id": _text(session_id, DEFAULT_SESSION_ID),
            "base_url": _text(base_url, DEFAULT_BASE_URL),
            "api_port": int(api_port),
            "data_dir": str(data_dir),
            "repo_root": str(self.repo_root),
            "lock_path": str(self.lock_path),
            "owner_token": owner_token,
            "runtime_owner_id": f"phoenixguard-{_text(session_id, DEFAULT_SESSION_ID)}-{owner_token[:10]}",
            "launcher_pid": int(launcher_pid or os.getpid()),
            "tracker_pid": int(tracker_pid or 0),
            "api_pid": int(api_pid or 0),
            "shooter_pid": int(shooter_pid or 0),
            "created_epoch_ms": now_ms,
            "heartbeat_epoch_ms": now_ms,
            "state_version_owner": owner_token,
            "takeover_reason": assessment.reason if assessment else "",
        }
        _write_json_atomic(self.lock_path, payload)
        return RuntimeGuardResultV3(True, "stack_lock_acquired", self.lock_path, payload, owner_token, assessment)

    def heartbeat(self, *, owner_token: str | None = None, updates: Mapping[str, object] | None = None) -> RuntimeGuardResultV3:
        lock = self.read_lock()
        if not lock:
            return RuntimeGuardResultV3(False, "missing_lock", self.lock_path, {}, owner_token or "")
        expected = _text(lock.get("owner_token"))
        if owner_token and expected and owner_token != expected:
            return RuntimeGuardResultV3(False, "owner_token_mismatch", self.lock_path, lock, owner_token, self.assess(lock))
        lock.update(dict(updates or {}))
        lock["heartbeat_epoch_ms"] = _now_ms()
        try:
            _write_json_atomic(self.lock_path, lock)
        except OSError as exc:
            return RuntimeGuardResultV3(False, f"heartbeat_write_failed:{type(exc).__name__}", self.lock_path, lock, expected or owner_token or "", self.assess(lock))
        return RuntimeGuardResultV3(True, "heartbeat_recorded", self.lock_path, lock, expected or owner_token or "", self.assess(lock))

    def register_component(
        self,
        component: ComponentName,
        *,
        pid: int,
        session_id: str = DEFAULT_SESSION_ID,
        base_url: str = DEFAULT_BASE_URL,
        owner_token: str | None = None,
    ) -> RuntimeGuardResultV3:
        if pid <= 0:
            return RuntimeGuardResultV3(False, "invalid_pid", self.lock_path, self.read_lock(), owner_token or "")
        lock = self.read_lock()
        if not lock:
            acquired = self.acquire(
                session_id=session_id,
                base_url=base_url,
                api_pid=pid if component == "api" else None,
                tracker_pid=pid if component == "tracker" else None,
                shooter_pid=pid if component == "shooter" else None,
                launcher_pid=pid if component == "launcher" else None,
            )
            return acquired
        if _text(lock.get("session_id"), DEFAULT_SESSION_ID) != _text(session_id, DEFAULT_SESSION_ID):
            return RuntimeGuardResultV3(False, "session_id_mismatch", self.lock_path, lock, owner_token or _text(lock.get("owner_token")), self.assess(lock))
        if _text(base_url) and _text(lock.get("base_url"), DEFAULT_BASE_URL) != _text(base_url, DEFAULT_BASE_URL):
            return RuntimeGuardResultV3(False, "base_url_mismatch", self.lock_path, lock, owner_token or _text(lock.get("owner_token")), self.assess(lock))
        expected = _text(lock.get("owner_token"))
        if owner_token and expected and owner_token != expected:
            return RuntimeGuardResultV3(False, "owner_token_mismatch", self.lock_path, lock, owner_token, self.assess(lock))
        key = f"{component}_pid"
        current_pid = _int(lock.get(key), 0)
        if current_pid > 0 and current_pid != pid and self._pid_probe(current_pid):
            return RuntimeGuardResultV3(False, f"active_{component}_already_registered", self.lock_path, lock, owner_token or expected, self.assess(lock))
        lock[key] = int(pid)
        lock[f"{component}_registered_epoch_ms"] = _now_ms()
        return self.heartbeat(owner_token=owner_token or expected or None, updates=lock)

    def release(self, *, owner_token: str) -> RuntimeGuardResultV3:
        lock = self.read_lock()
        if not lock:
            return RuntimeGuardResultV3(True, "lock_already_absent", self.lock_path, {}, owner_token)
        expected = _text(lock.get("owner_token"))
        if expected and owner_token != expected:
            return RuntimeGuardResultV3(False, "owner_token_mismatch", self.lock_path, lock, owner_token, self.assess(lock))
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        return RuntimeGuardResultV3(True, "lock_released", self.lock_path, lock, owner_token)


def guard_from_environment(repo_root: Path | None = None) -> PhoenixRuntimeSingletonGuardV3:
    lock_path_text = _text(os.getenv("PHOENIXGUARD_RUNTIME_LOCK_PATH"))
    lock_path = Path(lock_path_text) if lock_path_text else None
    return PhoenixRuntimeSingletonGuardV3(lock_path=lock_path, repo_root=repo_root)


__all__ = [
    "DEFAULT_API_PORT",
    "DEFAULT_BASE_URL",
    "DEFAULT_SESSION_ID",
    "DEFAULT_STALE_AFTER_MS",
    "LOCK_SCHEMA_VERSION",
    "PhoenixRuntimeSingletonGuardV3",
    "RuntimeGuardResultV3",
    "RuntimeLockAssessmentV3",
    "default_lock_path",
    "guard_from_environment",
]
