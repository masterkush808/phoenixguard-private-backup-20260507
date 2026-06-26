from __future__ import annotations

import argparse
import csv
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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n")


def _read_csv_rows(path: Path) -> list[list[str]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[list[str]] = []
    for row in csv.reader(raw.splitlines()):
        if row:
            rows.append(row)
    return rows


def _read_state(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"exists": False, "error": str(exc)}
    return {
        "exists": True,
        "last_accepted_packet_id": lines[0].strip() if len(lines) >= 1 else "",
        "last_accepted_frame": lines[1].strip() if len(lines) >= 2 else "",
        "last_accepted_capture": lines[2].strip() if len(lines) >= 3 else "",
        "last_accepted_state": lines[3].strip() if len(lines) >= 4 else "",
    }


def _event_from_row(index: int, row: list[str]) -> dict[str, Any]:
    padded = row + [""] * max(0, 7 - len(row))
    action = padded[1].strip()
    return {
        "source": "mt4_executioner_audit",
        "row_index": index,
        "mt4_time": padded[0].strip(),
        "action": action,
        "packet_id": padded[2].strip(),
        "side": padded[3].strip(),
        "lots": padded[4].strip(),
        "symbol": padded[5].strip(),
        "detail": padded[6].strip(),
        "entry_taken_verified": action == "ACCEPT_TRADE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor MT4 EA audit rows for verified PhoenixGuard execution events.")
    parser.add_argument("--common-files-dir", default="")
    parser.add_argument("--audit-file", default=r"PhoenixGuard\mt4_executioner_audit.csv")
    parser.add_argument("--state-file", default=r"PhoenixGuard\mt4_executioner_state.txt")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--poll-sec", type=float, default=15.0)
    parser.add_argument("--duration-sec", type=float, default=0.0)
    parser.add_argument("--print-every", type=float, default=30.0)
    args = parser.parse_args()

    common_root = Path(args.common_files_dir) if args.common_files_dir else _default_common_files_dir()
    audit_path = common_root / args.audit_file
    state_path = common_root / args.state_file
    out_dir = Path(args.out_dir) if args.out_dir else Path.cwd() / "reports" / "mt4_execution_confirmation"
    log_path = out_dir / "mt4_execution_confirmation.jsonl"
    start = time.time()
    last_print = 0.0
    seen_rows = len(_read_csv_rows(audit_path))
    action_counts: dict[str, int] = {}
    verified_entries = 0

    print(f"MT4 execution confirmation monitor audit={audit_path}", flush=True)
    print(f"MT4 execution confirmation monitor state={state_path}", flush=True)
    print(f"MT4 execution confirmation monitor log={log_path}", flush=True)

    while True:
        now = time.time()
        rows = _read_csv_rows(audit_path)
        new_rows = rows[seen_rows:]
        for offset, row in enumerate(new_rows, start=seen_rows):
            event = _event_from_row(offset, row)
            action = str(event.get("action") or "")
            action_counts[action] = action_counts.get(action, 0) + 1
            if event["entry_taken_verified"]:
                verified_entries += 1
            event.update(
                {
                    "at_epoch": now,
                    "at_utc": _utc_now(),
                    "state": _read_state(state_path),
                    "summary": {
                        "verified_entry_count": verified_entries,
                        "action_counts": dict(action_counts),
                    },
                }
            )
            _append_jsonl(log_path, event)
        seen_rows = len(rows)

        if now - last_print >= args.print_every:
            _append_jsonl(
                log_path,
                {
                    "at_epoch": now,
                    "at_utc": _utc_now(),
                    "source": "mt4_execution_confirmation_heartbeat",
                    "audit_exists": audit_path.exists(),
                    "state": _read_state(state_path),
                    "summary": {
                        "observed_rows": seen_rows,
                        "verified_entry_count": verified_entries,
                        "action_counts": dict(action_counts),
                    },
                },
            )
            print(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} mt4_execution "
                f"rows={seen_rows} verified_entries={verified_entries}",
                flush=True,
            )
            last_print = now

        if args.duration_sec > 0 and now - start >= args.duration_sec:
            break
        time.sleep(max(0.2, args.poll_sec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
