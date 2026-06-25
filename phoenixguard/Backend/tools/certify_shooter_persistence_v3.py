from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping
from typing import cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    ROOT,
    command_line,
    find_processes,
    gate_report,
    leaf_processes,
    print_gate,
    process_id,
    python_processes,
    write_report,
)


HANDSHAKE_PATHS = (
    ROOT / ".codex_runtime" / "shooter_handshake.json",
    ROOT / ".codex_runtime" / "shooter_runtime" / "shooter_handshake.json",
)


def _load_handshake() -> tuple[dict[str, object], str]:
    candidates: list[tuple[float, dict[str, object], str]] = []
    for path in HANDSHAKE_PATHS:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, Mapping):
                candidates.append((float(path.stat().st_mtime), dict(cast(Mapping[str, object], payload)), str(path)))
        except Exception:
            continue
    if not candidates:
        return {}, ""
    _, payload, path_text = max(candidates, key=lambda row: row[0])
    return payload, path_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 shooter persistence.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=1800.0)
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, object]] = []
    initial_rows = leaf_processes(find_processes(python_processes(), "shooter.py"))
    if not initial_rows:
        message = "shooter process is not running"
        if args.allow_missing:
            warnings.append(message)
        else:
            failures.append(message)
    if len(initial_rows) > 1:
        failures.append(f"expected one shooter process, found {len(initial_rows)}")
    if initial_rows:
        cmd = command_line(initial_rows[0])
        if args.base_url not in cmd:
            failures.append(f"shooter base_url mismatch: expected {args.base_url}, command={cmd}")
        if args.session not in cmd:
            failures.append(f"shooter session mismatch: expected {args.session}, command={cmd}")

    initial_pid = process_id(initial_rows[0]) if initial_rows else 0
    deadline = time.time() + max(1.0, float(args.duration_sec))
    while time.time() < deadline and not failures:
        rows = leaf_processes(find_processes(python_processes(), "shooter.py"))
        handshake, handshake_path = _load_handshake()
        alive = bool(initial_pid and any(process_id(row) == initial_pid for row in rows))
        sample: dict[str, object] = {
            "epoch": time.time(),
            "alive": alive,
            "pid": initial_pid,
            "process_count": len(rows),
            "handshake": handshake,
            "handshake_path": handshake_path,
        }
        samples.append(sample)
        if not alive and not args.allow_missing:
            failures.append(f"shooter PID {initial_pid} exited during persistence check")
            break
        if handshake:
            if str(handshake.get("base_url") or "") and str(handshake.get("base_url")) != args.base_url:
                failures.append(f"shooter handshake base_url mismatch: {handshake.get('base_url')}")
            if str(handshake.get("session_id") or "") and str(handshake.get("session_id")) != args.session:
                failures.append(f"shooter handshake session mismatch: {handshake.get('session_id')}")
            decision = dict(cast(Mapping[str, object], handshake.get("decision"))) if isinstance(handshake.get("decision"), Mapping) else {}
            reason = str(decision.get("reason") or "").upper()
            if "STALE" in reason and "WAIT" not in reason:
                failures.append(f"shooter consumed or attempted stale packet: {reason}")
        time.sleep(max(0.2, float(args.interval_sec)))

    report = gate_report(
        schema_version="PG_CERTIFY_SHOOTER_PERSISTENCE_V3",
        gate="Shooter Persistence",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "base_url": args.base_url,
            "duration_sec": float(args.duration_sec),
            "initial_pid": initial_pid,
            "initial_processes": initial_rows,
            "sample_count": len(samples),
            "samples": samples[-300:],
            "handshake_paths": [str(path) for path in HANDSHAKE_PATHS],
            "launch_command": r".\.venv\Scripts\python.exe Backend\launch\shooter.py signal --base-url http://127.0.0.1:8793 --session-id pocket-live-8788 --poll 0.20",
        },
    )
    out = write_report("gate8_shooter_persistence_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("SHOOTER_PERSISTENCE: " + report["verdict"])
    print_gate("SHOOTER_PERSISTENCE", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
