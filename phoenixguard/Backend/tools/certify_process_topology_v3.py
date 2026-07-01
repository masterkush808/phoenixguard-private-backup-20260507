from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_DATA_DIR,
    DEFAULT_SESSION,
    ROOT,
    command_line,
    find_processes,
    gate_report,
    http_json,
    leaf_processes,
    normalize_path_text,
    print_gate,
    process_id,
    python_processes,
    quote_session,
    tcp_listeners,
    write_report,
)
from phoenixguard.runtime.singleton_guard_v3 import LOCK_SCHEMA_VERSION, PhoenixRuntimeSingletonGuardV3


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _parent_by_pid(processes: list[dict[str, object]]) -> dict[int, int]:
    parents: dict[int, int] = {}
    for row in processes:
        pid = process_id(row)
        parent = _int(row.get("ParentProcessId"))
        if pid > 0 and parent > 0:
            parents[pid] = parent
    return parents


def _same_logical_process(first_pid: int, second_pid: int, processes: list[dict[str, object]]) -> bool:
    if first_pid <= 0 or second_pid <= 0:
        return False
    if first_pid == second_pid:
        return True
    parents = _parent_by_pid(processes)

    def ancestors(pid: int) -> set[int]:
        output: set[int] = set()
        current = pid
        while current in parents and parents[current] not in output:
            current = parents[current]
            output.add(current)
        return output

    return first_pid in ancestors(second_pid) or second_pid in ancestors(first_pid)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 process topology.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--fallback-port", type=int, default=8787)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--allow-missing-shooter", action="store_true")
    args = parser.parse_args()

    processes = python_processes()
    listeners = tcp_listeners([args.port, args.fallback_port])
    raw_api_processes = find_processes(processes, "start_phoenixguard_mobile_api.py")
    api_processes = leaf_processes(raw_api_processes)
    tracker_processes = leaf_processes(find_processes(processes, "start_phoenixguard_24_7_tracker.py"))
    shooter_processes = leaf_processes(find_processes(processes, "shooter.py"))
    listener_rows = [row for row in listeners if int(row.get("LocalPort") or 0) == int(args.port)]
    fallback_rows = [row for row in listeners if int(row.get("LocalPort") or 0) == int(args.fallback_port)]
    failures: list[str] = []
    warnings: list[str] = []
    corrections: list[str] = []
    singleton_guard = PhoenixRuntimeSingletonGuardV3.for_repo(ROOT)
    singleton_lock = singleton_guard.read_lock()
    singleton_assessment = singleton_guard.assess(singleton_lock)

    if len(listener_rows) != 1:
        failures.append(f"expected exactly one listener on {args.port}, found {len(listener_rows)}")
    api_owner_pid = int(listener_rows[0].get("OwningProcess") or 0) if listener_rows else 0
    if api_owner_pid and not any(_same_logical_process(process_id(row), api_owner_pid, processes) for row in api_processes):
        failures.append(f"listener on {args.port} is owned by PID {api_owner_pid}, not a start_phoenixguard_mobile_api.py process")
    if len(api_processes) != 1:
        failures.append(f"expected one API process, found {len(api_processes)}")
        corrections.append(
            r"Stop stale mobile API processes, then relaunch via .\.venv-live\Scripts\python.exe Backend\launch\start_phoenixguard_24_7_tracker.py --port 8793."
        )
    if fallback_rows:
        failures.append(f"fallback API port {args.fallback_port} is listening: {fallback_rows}")
    if len(tracker_processes) != 1:
        failures.append(f"expected one tracker worker process, found {len(tracker_processes)}")
        corrections.append(
            r"Start tracker with: .\.venv-live\Scripts\python.exe Backend\launch\start_phoenixguard_24_7_tracker.py --host 127.0.0.1 --port 8793 --session-id pocket-live-8788 --capture-interval 15 --no-open-dashboard"
        )
    elif args.session not in command_line(tracker_processes[0]):
        failures.append(f"tracker process does not include expected session id {args.session}")
    if not shooter_processes and not args.allow_missing_shooter:
        failures.append("shooter process is not running")
        corrections.append(
            r"Start shooter with: .\.venv-live\Scripts\python.exe Backend\launch\shooter.py signal --base-url http://127.0.0.1:8793 --session-id pocket-live-8788 --poll 15.0 --heartbeat 4.0"
        )
    if len(shooter_processes) > 1:
        failures.append(f"expected at most one shooter process, found {len(shooter_processes)}")
    if shooter_processes:
        shooter_cmd = command_line(shooter_processes[0])
        if args.base_url not in shooter_cmd:
            failures.append(f"shooter base_url mismatch: expected {args.base_url}, command={shooter_cmd}")
        if args.session not in shooter_cmd:
            failures.append(f"shooter session mismatch: expected {args.session}, command={shooter_cmd}")
    if not singleton_lock:
        failures.append(f"runtime singleton lock missing: {singleton_guard.lock_path}")
        corrections.append("Relaunch through Backend/launch/start_phoenixguard_24_7_tracker.py so PhoenixRuntimeSingletonGuardV3 owns the stack.")
    else:
        if singleton_lock.get("schema_version") != LOCK_SCHEMA_VERSION:
            failures.append(f"runtime singleton lock schema mismatch: {singleton_lock.get('schema_version')}")
        if str(singleton_lock.get("session_id") or "") != str(args.session):
            failures.append(f"runtime singleton session mismatch: {singleton_lock.get('session_id')} != {args.session}")
        if str(singleton_lock.get("base_url") or "") != str(args.base_url):
            failures.append(f"runtime singleton base_url mismatch: {singleton_lock.get('base_url')} != {args.base_url}")
        if _int(singleton_lock.get("api_port")) != int(args.port):
            failures.append(f"runtime singleton api_port mismatch: {singleton_lock.get('api_port')} != {args.port}")
        lock_api_pid = _int(singleton_lock.get("api_pid"))
        lock_tracker_pid = _int(singleton_lock.get("tracker_pid"))
        lock_shooter_pid = _int(singleton_lock.get("shooter_pid"))
        if api_owner_pid and lock_api_pid and not _same_logical_process(lock_api_pid, api_owner_pid, processes):
            failures.append(f"runtime singleton api_pid mismatch: lock={lock_api_pid} listener={api_owner_pid}")
        elif api_owner_pid and lock_api_pid and lock_api_pid != api_owner_pid:
            warnings.append(f"runtime singleton api_pid uses Windows venv redirector chain: lock={lock_api_pid} listener={api_owner_pid}")
        if tracker_processes and lock_tracker_pid and not any(
            _same_logical_process(lock_tracker_pid, process_id(row), processes) for row in tracker_processes
        ):
            failures.append(f"runtime singleton tracker_pid {lock_tracker_pid} is not the tracker process list")
        if shooter_processes and lock_shooter_pid and not any(
            _same_logical_process(lock_shooter_pid, process_id(row), processes) for row in shooter_processes
        ):
            failures.append(f"runtime singleton shooter_pid {lock_shooter_pid} is not the shooter process list")
        if singleton_assessment.stale:
            failures.append(f"runtime singleton lock is stale/unhealthy: {singleton_assessment.reason}")

    session = http_json(f"{args.base_url.rstrip('/')}/v1/mobile/window-tracker/sessions/{quote_session(args.session)}", timeout=15.0)
    live = http_json(f"{args.base_url.rstrip('/')}/v1/mobile/live/state/v3/{quote_session(args.session)}", timeout=15.0)
    expected_data = normalize_path_text(args.data_dir)
    artifact_paths: list[str] = []
    for payload in (_mapping(session.payload), _mapping(live.payload)):
        for value in payload.values():
            normalized_value = normalize_path_text(value) if isinstance(value, str) else ""
            if isinstance(value, str) and (
                "runtime/live" in normalized_value
                or "runtime\\live" in value.lower()
                or "data_live" in normalized_value
            ):
                artifact_paths.append(value)
    data_dir_ok = any(expected_data in normalize_path_text(path) for path in artifact_paths)
    if not session.ok:
        failures.append(f"session endpoint failed: {session.error or session.status}")
    elif not data_dir_ok:
        failures.append(f"session artifacts do not prove PHOENIXGUARD_DATA_DIR={args.data_dir}; artifact_paths={artifact_paths[:8]}")
    if not live.ok:
        warnings.append(f"live state endpoint unavailable during topology check: {live.error or live.status}")

    report = gate_report(
        schema_version="PG_CERTIFY_PROCESS_TOPOLOGY_V3",
        gate="Process Topology",
        failures=failures,
        warnings=warnings,
        details={
            "api_port": args.port,
            "data_dir": args.data_dir,
            "session": args.session,
            "api_pid": api_owner_pid,
            "tracker_pid": process_id(tracker_processes[0]) if tracker_processes else 0,
            "shooter_pid": process_id(shooter_processes[0]) if shooter_processes else 0,
            "singleton_lock_path": str(singleton_guard.lock_path),
            "singleton_lock": singleton_lock,
            "singleton_assessment": singleton_assessment.as_dict(),
            "processes": {
                "api": api_processes,
                "api_raw": raw_api_processes,
                "tracker": tracker_processes,
                "shooter": shooter_processes,
            },
            "listeners": listeners,
            "correction_commands": corrections,
        },
    )
    out = write_report("gate1_process_topology_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("PROCESS_TOPOLOGY: " + report["verdict"])
    if report["verdict"] == "PASS":
        print(f"api_port={args.port}")
        print(f"data_dir={args.data_dir}")
        print(f"session={args.session}")
        print(f"tracker_pid={report['tracker_pid']}")
        print(f"api_pid={report['api_pid']}")
        print(f"shooter_pid={report['shooter_pid']}")
    else:
        print_gate("PROCESS_TOPOLOGY", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
