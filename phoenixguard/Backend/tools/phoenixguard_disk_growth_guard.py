from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "Backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from phoenixguard.runtime.disk_growth_guard_v3 import (  # noqa: E402
    DEFAULT_MAX_BYTES,
    build_default_targets,
    parse_size_bytes,
    run_disk_growth_guard,
    write_guard_report,
)


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PhoenixGuard disk growth guard. Caps generated runtime/session memory artifacts.",
    )
    parser.add_argument("--limit", default="", help="Per-target cap, for example 2GB, 512MB, or bytes. Default: 2GB.")
    parser.add_argument("--low-water", default="", help="Target size after pruning. Default: 75% of --limit.")
    parser.add_argument("--apply", action="store_true", help="Delete/prune generated artifacts. Without this it is a dry run.")
    parser.add_argument("--once", action="store_true", help="Run one sweep and exit.")
    parser.add_argument("--interval-sec", type=float, default=300.0, help="Watch-loop interval. Default: 300 seconds.")
    parser.add_argument(
        "--include-codex-sessions",
        action="store_true",
        help="Also cap operator ~/.codex/sessions history. Old children only; current active files are age-protected.",
    )
    parser.add_argument(
        "--report-path",
        default="runtime/live/disk_growth_guard_report.json",
        help="Latest JSON report path.",
    )
    parser.add_argument(
        "--jsonl-log",
        default="runtime/live/logs_live/disk_growth_guard.jsonl",
        help="Append-only JSONL guard activity log.",
    )
    args = parser.parse_args()

    max_bytes = parse_size_bytes(args.limit, default=DEFAULT_MAX_BYTES)
    low_water = parse_size_bytes(args.low_water, default=int(max_bytes * 0.75))
    if low_water <= 0 or low_water > max_bytes:
        low_water = int(max_bytes * 0.75)

    report_path = Path(args.report_path)
    jsonl_path = Path(args.jsonl_log)

    while True:
        targets = build_default_targets(
            max_bytes=max_bytes,
            low_water_bytes=low_water,
            include_codex_sessions=bool(args.include_codex_sessions),
        )
        report = run_disk_growth_guard(targets, apply=bool(args.apply))
        write_guard_report(report_path, report)
        row = report.to_dict()
        _append_jsonl(jsonl_path, row)
        print(
            json.dumps(
                {
                    "schema_version": report.schema_version,
                    "applied": report.applied,
                    "total_mb_before": round(report.total_bytes_before / 1024 / 1024, 2),
                    "total_mb_after": round(report.total_bytes_after / 1024 / 1024, 2),
                    "total_mb_removed": round(report.total_bytes_removed / 1024 / 1024, 2),
                    "triggered": [target.name for target in report.targets if target.triggered],
                },
                sort_keys=True,
            )
        )
        if bool(args.once):
            return 0
        time.sleep(max(30.0, float(args.interval_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
