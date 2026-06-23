from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any


def _default_common_files_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot resolve MetaQuotes common Files directory.")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json_file(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"read_error:{exc}"
    try:
        value = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        return None, f"json_error:{exc}"
    if not isinstance(value, dict):
        return None, "json_error:not_object"
    return value, ""


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n")


def _file_state(path: Path, stale_sec: float, status_stale_sec: float) -> dict[str, Any]:
    state: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        state.update({"parse_ok": False, "error": "missing", "stale": True})
        return state
    try:
        stat = path.stat()
    except OSError as exc:
        state.update({"parse_ok": False, "error": f"stat_error:{exc}", "stale": True})
        return state
    age_sec = max(0.0, time.time() - float(stat.st_mtime))
    payload, error = _read_json_file(path)
    effective_stale_sec = stale_sec
    state.update(
        {
            "size_bytes": int(stat.st_size),
            "mtime_epoch": float(stat.st_mtime),
            "age_sec": round(age_sec, 3),
            "stale": age_sec > effective_stale_sec,
            "stale_sec": effective_stale_sec,
            "parse_ok": payload is not None,
            "error": error,
        }
    )
    if payload is not None:
        schema_version = str(payload.get("schema_version") or "")
        bridge_status = str(payload.get("bridge_status") or "")
        if schema_version != "PG_MT4_EXECUTION_COMMAND_V1" and bridge_status != "EXECUTION_PACKET":
            effective_stale_sec = status_stale_sec
            state["stale"] = age_sec > effective_stale_sec
            state["stale_sec"] = effective_stale_sec
        state.update(
            {
                "schema_version": schema_version,
                "bridge_status": bridge_status,
                "packet_id": str(payload.get("packet_id") or payload.get("detail") or ""),
                "symbol": str(payload.get("symbol") or ""),
                "bridge_sequence": int(float(payload.get("bridge_sequence") or 0)),
                "written_epoch": float(payload.get("bridge_written_epoch") or payload.get("written_epoch") or 0.0),
            }
        )
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor PhoenixGuard MT4 JSON bridge file health.")
    parser.add_argument("--common-files-dir", default="")
    parser.add_argument("--signal-file", default=r"PhoenixGuard\mt4_execution_command.json")
    parser.add_argument("--status-file", default=r"PhoenixGuard\mt4_bridge_status.json")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--poll-sec", type=float, default=0.5)
    parser.add_argument("--stale-sec", type=float, default=2.5)
    parser.add_argument("--status-stale-sec", type=float, default=8.0)
    parser.add_argument("--duration-sec", type=float, default=0.0)
    parser.add_argument("--print-every", type=float, default=30.0)
    args = parser.parse_args()

    common_root = Path(args.common_files_dir) if args.common_files_dir else _default_common_files_dir()
    signal_path = common_root / args.signal_file
    status_path = common_root / args.status_file
    out_dir = Path(args.out_dir) if args.out_dir else Path.cwd() / "reports" / "mt4_bridge_health"
    log_path = out_dir / "mt4_bridge_health.jsonl"
    start = time.time()
    last_print = 0.0
    last_sequence = 0
    parse_errors = 0
    stale_count = 0
    sequence_regressions = 0
    samples = 0

    print(f"MT4 bridge health monitor signal={signal_path}", flush=True)
    print(f"MT4 bridge health monitor status={status_path}", flush=True)
    print(f"MT4 bridge health monitor log={log_path}", flush=True)

    while True:
        now = time.time()
        signal = _file_state(signal_path, args.stale_sec, args.status_stale_sec)
        status = _file_state(status_path, args.stale_sec, args.status_stale_sec)
        sequence = int(signal.get("bridge_sequence") or status.get("bridge_sequence") or 0)
        if sequence and last_sequence and sequence < last_sequence:
            sequence_regressions += 1
        if sequence:
            last_sequence = sequence
        if not signal.get("parse_ok"):
            parse_errors += 1
        if signal.get("stale"):
            stale_count += 1
        samples += 1

        record = {
            "at_epoch": now,
            "at_utc": _utc_now(),
            "sample": samples,
            "signal": signal,
            "status": status,
            "summary": {
                "parse_errors": parse_errors,
                "stale_count": stale_count,
                "last_sequence": last_sequence,
                "sequence_regressions": sequence_regressions,
            },
        }
        _append_jsonl(log_path, record)

        if now - last_print >= args.print_every:
            print(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} mt4_signal "
                f"parse_ok={signal.get('parse_ok')} stale={signal.get('stale')} "
                f"seq={sequence} status={signal.get('bridge_status') or signal.get('schema_version')} "
                f"errors={parse_errors}",
                flush=True,
            )
            last_print = now

        if args.duration_sec > 0 and now - start >= args.duration_sec:
            break
        time.sleep(max(0.1, args.poll_sec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
