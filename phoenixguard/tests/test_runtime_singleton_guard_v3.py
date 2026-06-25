from __future__ import annotations

import json
from pathlib import Path

from phoenixguard.runtime.singleton_guard_v3 import (
    LOCK_SCHEMA_VERSION,
    PhoenixRuntimeSingletonGuardV3,
)


def _guard(tmp_path: Path, *, alive_pids: set[int] | None = None, port_open: bool = False) -> PhoenixRuntimeSingletonGuardV3:
    alive = set(alive_pids or set())
    return PhoenixRuntimeSingletonGuardV3(
        lock_path=tmp_path / "phoenixguard_stack.lock.json",
        repo_root=tmp_path,
        stale_after_ms=1_000,
        pid_probe=lambda pid: pid in alive,
        port_probe=lambda _host, _port: port_open,
    )


def test_singleton_guard_acquires_stack_lock(tmp_path: Path) -> None:
    guard = _guard(tmp_path, alive_pids={101, 202})

    result = guard.acquire(
        session_id="pocket-live-8788",
        base_url="http://127.0.0.1:8793",
        data_dir=tmp_path / "data_live",
        launcher_pid=101,
        tracker_pid=101,
        api_pid=202,
    )

    lock = guard.read_lock()
    assert result.ok is True
    assert lock["schema_version"] == LOCK_SCHEMA_VERSION
    assert lock["session_id"] == "pocket-live-8788"
    assert lock["tracker_pid"] == 101
    assert lock["api_pid"] == 202
    assert lock["owner_token"] == result.owner_token


def test_singleton_guard_refuses_second_healthy_owner(tmp_path: Path) -> None:
    first = _guard(tmp_path, alive_pids={101, 202}, port_open=True)
    first_result = first.acquire(launcher_pid=101, tracker_pid=101, api_pid=202)
    second = _guard(tmp_path, alive_pids={101, 202}, port_open=True)

    second_result = second.acquire(launcher_pid=303, tracker_pid=303, api_pid=404)

    assert first_result.ok is True
    assert second_result.ok is False
    assert second_result.reason == "active_stack_lock_exists"
    assert second_result.owner_token == first_result.owner_token
    assert second.read_lock()["tracker_pid"] == 101


def test_singleton_guard_takes_over_stale_dead_lock(tmp_path: Path) -> None:
    first = _guard(tmp_path, alive_pids={101})
    first_result = first.acquire(launcher_pid=101, tracker_pid=101)
    lock = first.read_lock()
    lock["heartbeat_epoch_ms"] = 1
    first.lock_path.write_text(json.dumps(lock), encoding="utf-8")

    second = _guard(tmp_path, alive_pids={303})
    second_result = second.acquire(launcher_pid=303, tracker_pid=303)

    assert first_result.ok is True
    assert second_result.ok is True
    assert second_result.owner_token != first_result.owner_token
    assert second.read_lock()["tracker_pid"] == 303
    assert second.read_lock()["takeover_reason"] == "stale_heartbeat"


def test_singleton_guard_rejects_duplicate_active_shooter(tmp_path: Path) -> None:
    guard = _guard(tmp_path, alive_pids={101, 202, 303})
    acquired = guard.acquire(launcher_pid=101, tracker_pid=101, api_pid=202)
    registered = guard.register_component("shooter", pid=303, owner_token=acquired.owner_token)
    duplicate = guard.register_component("shooter", pid=404, owner_token=acquired.owner_token)

    assert registered.ok is True
    assert duplicate.ok is False
    assert duplicate.reason == "active_shooter_already_registered"
    assert guard.read_lock()["shooter_pid"] == 303


def test_singleton_guard_rejects_release_token_mismatch(tmp_path: Path) -> None:
    guard = _guard(tmp_path, alive_pids={101})
    acquired = guard.acquire(launcher_pid=101, tracker_pid=101)

    mismatch = guard.release(owner_token="wrong-token")
    released = guard.release(owner_token=acquired.owner_token)

    assert mismatch.ok is False
    assert mismatch.reason == "owner_token_mismatch"
    assert guard.lock_path.exists() is False
    assert released.ok is True
