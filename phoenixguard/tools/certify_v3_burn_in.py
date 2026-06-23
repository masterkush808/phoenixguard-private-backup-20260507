from __future__ import annotations

import argparse
import json
import time
from typing import Mapping

from certification_common_v3 import (
    CERT_DIR,
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    find_processes,
    gate_report,
    http_json,
    leaf_processes,
    percentile,
    print_gate,
    process_id,
    python_processes,
    quote_session,
    tcp_listeners,
    write_final_certification_report,
    write_report,
)


def _load_gate_reports() -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    for path in sorted(CERT_DIR.glob("gate*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("gate"):
                reports.append(payload)
        except Exception:
            pass
    return reports


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _float_value(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PhoenixGuard V3 burn-in certification monitor.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=7200.0)
    parser.add_argument("--interval-sec", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--allow-missing-shooter", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, object]] = []
    frame_ages: list[float] = []
    overlay_ages: list[float] = []
    model_ages: list[float] = []
    endpoint_failures = 0
    stale_execution_packets = 0
    initial_api = {process_id(row) for row in find_processes(python_processes(), "start_phoenixguard_mobile_api.py") if process_id(row)}
    initial_tracker = {process_id(row) for row in leaf_processes(find_processes(python_processes(), "start_phoenixguard_24_7_tracker.py")) if process_id(row)}
    initial_shooter = {process_id(row) for row in leaf_processes(find_processes(python_processes(), "shooter.py")) if process_id(row)}
    if not initial_api:
        failures.append("API process not running at burn-in start")
    if not initial_tracker:
        failures.append("tracker process not running at burn-in start")
    if not initial_shooter and not args.allow_missing_shooter:
        failures.append("shooter process not running at burn-in start")

    deadline = time.time() + max(1.0, float(args.duration_sec))
    while time.time() < deadline and not failures:
        processes = python_processes()
        api = {process_id(row) for row in find_processes(processes, "start_phoenixguard_mobile_api.py") if process_id(row)}
        tracker = {process_id(row) for row in leaf_processes(find_processes(processes, "start_phoenixguard_24_7_tracker.py")) if process_id(row)}
        shooter = {process_id(row) for row in leaf_processes(find_processes(processes, "shooter.py")) if process_id(row)}
        listeners = tcp_listeners([8793, 8787])
        live = http_json(f"{base}/v1/mobile/live/state/v3/{session_q}", timeout=args.timeout)
        perf = http_json(f"{base}/v1/mobile/performance/trace/v3/{session_q}", timeout=args.timeout)
        heartbeat = http_json(f"{base}/v1/mobile/frontend/heartbeat/v3?session_id={session_q}", timeout=min(args.timeout, 3.0))
        sample = {
            "epoch": time.time(),
            "api_pids": sorted(api),
            "tracker_pids": sorted(tracker),
            "shooter_pids": sorted(shooter),
            "listeners": listeners,
            "live_ok": live.ok,
            "perf_ok": perf.ok,
            "heartbeat_ok": heartbeat.ok,
        }
        if not live.ok or not perf.ok:
            endpoint_failures += 1
            sample["error"] = live.error or perf.error
        else:
            live_payload = live.payload if isinstance(live.payload, dict) else {}
            perf_payload = perf.payload if isinstance(perf.payload, dict) else {}
            timing = _mapping(perf_payload.get("timing_trace"))
            frame_age = _float_value(timing.get("frame_age_ms"), 0.0)
            overlay_age = _float_value(timing.get("overlay_age_ms"), 0.0)
            model_age = _float_value(timing.get("model_vote_age_ms"), 0.0)
            frame_ages.append(frame_age)
            overlay_ages.append(overlay_age)
            model_ages.append(model_age)
            execution_status = _mapping(live_payload.get("execution_packet_status"))
            if execution_status.get("exists") and execution_status.get("fresh") is False:
                stale_execution_packets += 1
            sample.update(
                {
                    "frame_id": live_payload.get("frame_id"),
                    "frame_age_ms": frame_age,
                    "overlay_age_ms": overlay_age,
                    "model_vote_age_ms": model_age,
                    "execution_packet_status": execution_status,
                }
            )
        samples.append(sample)
        if initial_api and not (initial_api & api):
            failures.append(f"API process changed/exited: initial={sorted(initial_api)} current={sorted(api)}")
        if initial_tracker and not (initial_tracker & tracker):
            failures.append(f"tracker process changed/exited: initial={sorted(initial_tracker)} current={sorted(tracker)}")
        if initial_shooter and not (initial_shooter & shooter):
            failures.append(f"shooter process changed/exited: initial={sorted(initial_shooter)} current={sorted(shooter)}")
        if not args.allow_missing_shooter and not shooter:
            failures.append("shooter process missing during burn-in")
        if endpoint_failures:
            failures.append(f"endpoint_failures={endpoint_failures}")
        if stale_execution_packets:
            failures.append(f"stale_execution_packets={stale_execution_packets}")
        time.sleep(max(0.2, float(args.interval_sec)))

    p95_frame = percentile(frame_ages, 95)
    p95_overlay = percentile(overlay_ages, 95)
    p95_model = percentile(model_ages, 95)
    if p95_frame >= 1200.0:
        failures.append(f"p95 frame_age_ms {p95_frame:.0f} >= 1200")
    if p95_overlay >= 1800.0:
        failures.append(f"p95 overlay_age_ms {p95_overlay:.0f} >= 1800")
    if p95_model >= 1800.0:
        failures.append(f"p95 model_vote_age_ms {p95_model:.0f} >= 1800")

    report = gate_report(
        schema_version="PG_CERTIFY_V3_BURN_IN",
        gate="Burn-In",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "duration_sec": float(args.duration_sec),
            "sample_count": len(samples),
            "initial_api_pids": sorted(initial_api),
            "initial_tracker_pids": sorted(initial_tracker),
            "initial_shooter_pids": sorted(initial_shooter),
            "p95_frame_age_ms": p95_frame,
            "p95_overlay_age_ms": p95_overlay,
            "p95_model_vote_age_ms": p95_model,
            "endpoint_failures": endpoint_failures,
            "stale_execution_packets": stale_execution_packets,
            "samples": samples[-500:],
        },
    )
    out = write_report("gate10_burn_in_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    all_reports = [row for row in _load_gate_reports() if row.get("gate") != "Burn-In"] + [report]
    final_path = write_final_certification_report(all_reports)
    report["final_report"] = str(final_path)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("BURN_IN: " + report["verdict"])
    print(f"FINAL_REPORT={final_path}")
    print_gate("BURN_IN", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
