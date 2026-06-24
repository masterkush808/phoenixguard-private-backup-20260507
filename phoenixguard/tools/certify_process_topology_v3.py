from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from typing import Any, cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_DATA_DIR,
    DEFAULT_SESSION,
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


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


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
    api_processes = find_processes(processes, "start_phoenixguard_mobile_api.py")
    tracker_processes = leaf_processes(find_processes(processes, "start_phoenixguard_24_7_tracker.py"))
    shooter_processes = leaf_processes(find_processes(processes, "shooter.py"))
    listener_rows = [row for row in listeners if int(row.get("LocalPort") or 0) == int(args.port)]
    fallback_rows = [row for row in listeners if int(row.get("LocalPort") or 0) == int(args.fallback_port)]
    failures: list[str] = []
    warnings: list[str] = []
    corrections: list[str] = []

    if len(listener_rows) != 1:
        failures.append(f"expected exactly one listener on {args.port}, found {len(listener_rows)}")
    api_owner_pid = int(listener_rows[0].get("OwningProcess") or 0) if listener_rows else 0
    if api_owner_pid and not any(process_id(row) == api_owner_pid for row in api_processes):
        failures.append(f"listener on {args.port} is owned by PID {api_owner_pid}, not a start_phoenixguard_mobile_api.py process")
    if len(api_processes) != 1:
        failures.append(f"expected one API process, found {len(api_processes)}")
        corrections.append("Stop stale mobile API processes, then relaunch via start_phoenixguard_24_7_tracker.py --port 8793.")
    if fallback_rows:
        failures.append(f"fallback API port {args.fallback_port} is listening: {fallback_rows}")
    if len(tracker_processes) != 1:
        failures.append(f"expected one tracker worker process, found {len(tracker_processes)}")
        corrections.append("Start tracker with: python start_phoenixguard_24_7_tracker.py --host 127.0.0.1 --port 8793 --session-id pocket-live-8788 --capture-interval 1 --no-open-dashboard")
    elif args.session not in command_line(tracker_processes[0]):
        failures.append(f"tracker process does not include expected session id {args.session}")
    if not shooter_processes and not args.allow_missing_shooter:
        failures.append("shooter process is not running")
        corrections.append('Start shooter with: python shooter.py signal --base-url http://127.0.0.1:8793 --session-id pocket-live-8788 --poll 0.20 --max-signal-age 8 --preferred-source tracker --require-preferred-source --window-query "The Most Innovative Trading Platform" --shooter-mode LIVE_READY --no-auto-open --record-action-evidence')
    if len(shooter_processes) > 1:
        failures.append(f"expected at most one shooter process, found {len(shooter_processes)}")
    if shooter_processes:
        shooter_cmd = command_line(shooter_processes[0])
        if args.base_url not in shooter_cmd:
            failures.append(f"shooter base_url mismatch: expected {args.base_url}, command={shooter_cmd}")
        if args.session not in shooter_cmd:
            failures.append(f"shooter session mismatch: expected {args.session}, command={shooter_cmd}")

    session = http_json(f"{args.base_url.rstrip('/')}/v1/mobile/window-tracker/sessions/{quote_session(args.session)}", timeout=15.0)
    live = http_json(f"{args.base_url.rstrip('/')}/v1/mobile/live/state/v3/{quote_session(args.session)}", timeout=15.0)
    expected_data = normalize_path_text(args.data_dir)
    artifact_paths: list[str] = []
    for payload in (_mapping(session.payload), _mapping(live.payload)):
        for value in payload.values():
            if isinstance(value, str) and (".codex_runtime" in value or "data_live" in value):
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
            "processes": {
                "api": api_processes,
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
