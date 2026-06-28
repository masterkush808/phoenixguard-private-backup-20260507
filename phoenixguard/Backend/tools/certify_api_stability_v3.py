from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Mapping, cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_RUNTIME_DIR,
    DEFAULT_SESSION,
    command_line,
    find_processes,
    gate_report,
    http_json,
    print_gate,
    process_id,
    python_processes,
    quote_session,
    summarize_numbers,
    write_report,
)


def _api_pids() -> set[int]:
    return {process_id(row) for row in find_processes(python_processes(), "start_phoenixguard_mobile_api.py") if process_id(row)}


def _write_crash_evidence(last_request: dict[str, object], process_rows: list[dict[str, object]]) -> None:
    out_dir = Path("reports/certification/api_crash")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "last_request_before_crash.json").write_text(json.dumps(last_request, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (out_dir / "api_process_tree.json").write_text(json.dumps(process_rows, indent=2, sort_keys=True, default=str), encoding="utf-8")
    logs = sorted(DEFAULT_RUNTIME_DIR.glob("**/*.log"), key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)
    tail_lines: list[str] = []
    for path in logs[:5]:
        try:
            tail_lines.append(f"## {path}\n" + "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]))
        except Exception:
            pass
    (out_dir / "api_stderr_tail.txt").write_text("\n\n".join(tail_lines), encoding="utf-8")
    (out_dir / "api_crash_report.md").write_text(
        "# API Crash Evidence\n\n"
        f"- Last request: `{last_request.get('method')} {last_request.get('url')}`\n"
        f"- Error: `{last_request.get('error')}`\n"
        f"- Process rows captured: {len(process_rows)}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 API stability under live probes.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=300.0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    endpoints = [
        ("GET", f"{base}/v1/mobile/health"),
        ("GET", f"{base}/v1/mobile/live/state/v3/{session_q}?mode=CLEAN_LIVE&compact=1"),
        ("GET", f"{base}/v1/mobile/performance/trace/v3/{session_q}"),
        ("GET", f"{base}/v1/mobile/visual/health/v3/{session_q}"),
        ("POST", f"{base}/v1/mobile/window-tracker/sessions/{session_q}/capture-once?display_only=true"),
    ]
    initial_pids = _api_pids()
    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, object]] = []
    latencies: list[float] = []
    control_latencies: list[float] = []
    connection_resets = 0
    timeouts = 0
    last_request: dict[str, object] = {}
    deadline = time.time() + max(1.0, float(args.duration_sec))

    while time.time() < deadline:
        for method, url in endpoints:
            last_request = {"method": method, "url": url, "epoch": time.time()}
            result = http_json(url, method=method, timeout=args.timeout)
            is_control_probe = "/capture-once" in url
            if is_control_probe:
                control_latencies.append(result.latency_ms)
            else:
                latencies.append(result.latency_ms)
            error = str(result.error or "")
            if "reset" in error.lower() or "forcibly closed" in error.lower():
                connection_resets += 1
            if "timeout" in error.lower() or result.latency_ms > args.timeout * 1000.0:
                timeouts += 1
            result_fields = dict(cast(Mapping[str, object], result.as_dict()))
            row: dict[str, object] = {"method": method, "url": url, **result_fields}
            samples.append(row)
            if not result.ok:
                failures.append(f"{method} {url} failed: {result.error or result.status}")
                last_request = row
                break
        current_pids = _api_pids()
        if initial_pids and not (initial_pids & current_pids):
            failures.append(f"API process exited or was replaced: initial={sorted(initial_pids)} current={sorted(current_pids)}")
            _write_crash_evidence(last_request, [{**row, "CommandLine": command_line(row)} for row in python_processes()])
            break
        if failures:
            break
        time.sleep(max(0.1, float(args.interval_sec)))

    summary = summarize_numbers(latencies)
    control_summary = summarize_numbers(control_latencies)
    if connection_resets:
        failures.append(f"{connection_resets} connection reset(s) observed")
    if timeouts:
        failures.append(f"{timeouts} timeout/over-budget request(s) observed")
    if summary["p95"] > 1500.0:
        failures.append(f"p95 response time {summary['p95']:.0f}ms exceeded 1500ms")
    if summary["p99"] > 3000.0:
        failures.append(f"p99 response time {summary['p99']:.0f}ms exceeded 3000ms")
    if control_summary["p99"] > args.timeout * 1000.0:
        failures.append(f"capture-once control p99 {control_summary['p99']:.0f}ms exceeded timeout budget {args.timeout * 1000.0:.0f}ms")

    report = gate_report(
        schema_version="PG_CERTIFY_API_STABILITY_V3",
        gate="API Stability",
        failures=failures,
        warnings=warnings,
        details={
            "base_url": base,
            "session_id": args.session,
            "duration_sec": float(args.duration_sec),
            "initial_api_pids": sorted(initial_pids),
            "final_api_pids": sorted(_api_pids()),
            "connection_resets": connection_resets,
            "timeouts": timeouts,
            "latency_ms": summary,
            "capture_once_control_latency_ms": control_summary,
            "sample_count": len(samples),
            "samples": samples[-200:],
        },
    )
    out = write_report("gate4_api_stability_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("API_STABILITY: " + report["verdict"])
    print_gate("API_STABILITY", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
