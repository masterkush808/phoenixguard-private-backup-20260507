from __future__ import annotations

import argparse
from http.client import HTTPException
import json
from pathlib import Path
import statistics
import socket
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from typing import Any, Mapping, cast


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _json(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-LiveSpeedTestV3/1.0", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - local operator endpoint
        return _mapping(json.loads(response.read().decode("utf-8", errors="replace")))


def _error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    if isinstance(exc, URLError):
        return f"URL error: {exc.reason}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"timeout: {exc}"
    if isinstance(exc, HTTPException):
        return f"HTTP error: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _p(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    rows = sorted(values)
    index = min(len(rows) - 1, max(0, int(round((len(rows) - 1) * pct / 100.0))))
    return round(float(rows[index]), 3)


def _avg(values: list[float]) -> float:
    return round(float(statistics.mean(values)), 3) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PhoenixGuard V3 live speed/freshness sampling.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--duration-sec", type=float, default=120.0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default=".codex_runtime/speed_test")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = urllib.parse.quote(args.session, safe="")
    deadline = time.time() + max(1.0, float(args.duration_sec))
    frame_ids: list[int] = []
    rendered_ids: list[int] = []
    frame_ages: list[float] = []
    overlay_ages: list[float] = []
    model_ages: list[float] = []
    frontend_ages: list[float] = []
    end_to_end: list[float] = []
    queue_depths: list[float] = []
    stale_events = 0
    stale_execution_packets = 0
    endpoint_failures = 0
    samples: list[dict[str, Any]] = []

    while time.time() < deadline:
        try:
            live = _json(f"{base}/v1/mobile/live/state/v3/{session}", args.timeout)
            perf = _json(f"{base}/v1/mobile/performance/trace/v3/{session}", args.timeout)
        except Exception as exc:
            endpoint_failures += 1
            samples.append({
                "error": _error(exc),
                "sample_epoch_ms": round(time.time() * 1000.0, 3),
            })
            time.sleep(max(0.1, float(args.interval_sec)))
            continue
        try:
            heartbeat = _json(f"{base}/v1/mobile/frontend/heartbeat/v3?session_id={session}", min(args.timeout, 5.0))
        except Exception as exc:
            endpoint_failures += 1
            heartbeat = {"error": _error(exc)}
        timing = _mapping(perf.get("timing_trace"))
        model_state = _mapping(perf.get("model_state"))
        frame_id = int(float(live.get("frame_id") or 0))
        rendered_frame = int(float(heartbeat.get("rendered_frame_id") or 0))
        frame_ids.append(frame_id)
        if rendered_frame:
            rendered_ids.append(rendered_frame)
        frame_ages.append(float(timing.get("frame_age_ms") or 0))
        overlay_ages.append(float(timing.get("overlay_age_ms") or 0))
        model_ages.append(float(timing.get("model_vote_age_ms") or 0))
        frontend_ages.append(float(timing.get("frontend_render_age_ms") or 0))
        end_to_end.append(float(timing.get("frame_age_ms") or 0))
        queue_depths.append(float(model_state.get("queue_depth") or 0))
        visual_health = _mapping(perf.get("visual_health"))
        stale_events += 1 if str(visual_health.get("status") or "").upper() not in {"ALIVE", "PASS"} else 0
        execution_status = _mapping(live.get("execution_packet_status") or _mapping(_mapping(live.get("packets")).get("execution")))
        if execution_status.get("exists") and execution_status.get("fresh") is False:
            stale_execution_packets += 1
        samples.append({
            "frame_id": frame_id,
            "rendered_frame_id": rendered_frame,
            "frame_age_ms": timing.get("frame_age_ms"),
            "overlay_age_ms": timing.get("overlay_age_ms"),
            "model_vote_age_ms": timing.get("model_vote_age_ms"),
            "frontend_render_age_ms": timing.get("frontend_render_age_ms"),
            "queue_depth": model_state.get("queue_depth"),
            "visual_health": perf.get("visual_health"),
            "heartbeat_error": heartbeat.get("error"),
        })
        time.sleep(max(0.1, float(args.interval_sec)))

    frames_captured = len(set(frame_ids))
    frames_displayed = len(set(rendered_ids))
    manual_refresh_required = frames_displayed <= 1 and frames_captured > 1
    model_summary = _mapping((samples[-1] if samples else {}).get("visual_health"))
    report: dict[str, Any] = {
        "schema_version": "PG_LIVE_SPEED_TEST_V3",
        "session_id": args.session,
        "duration_sec": float(args.duration_sec),
        "sample_count": len(samples),
        "frames_captured": frames_captured,
        "frames_displayed": frames_displayed,
        "frames_dropped": max(0, frames_captured - frames_displayed),
        "average_frame_age_ms": _avg(frame_ages),
        "p95_frame_age_ms": _p(frame_ages, 95),
        "average_overlay_age_ms": _avg(overlay_ages),
        "p95_overlay_age_ms": _p(overlay_ages, 95),
        "average_model_vote_age_ms": _avg(model_ages),
        "p95_model_vote_age_ms": _p(model_ages, 95),
        "p95_frontend_render_age_ms": _p(frontend_ages, 95),
        "p95_end_to_end_visual_age_ms": _p(end_to_end, 95),
        "queue_depth_p95": _p(queue_depths, 95),
        "stale_events": stale_events,
        "stale_execution_packets": stale_execution_packets,
        "frontend_manual_refresh_required": manual_refresh_required,
        "model_health": model_summary,
        "pass_criteria": {
            "capture_alive": frames_captured > 1,
            "frontend_auto_updates": not manual_refresh_required,
            "p95_frame_age_under_1200": _p(frame_ages, 95) < 1200,
            "p95_overlay_age_under_1800": _p(overlay_ages, 95) < 1800,
            "p95_visual_age_under_2500": _p(end_to_end, 95) < 2500,
            "queue_depth_p95_lte_1": _p(queue_depths, 95) <= 1,
            "stale_execution_packets_zero": stale_execution_packets == 0,
            "endpoint_failures_zero": endpoint_failures == 0,
        },
        "endpoint_failures": endpoint_failures,
        "samples": samples,
    }
    pass_criteria = _mapping(report.get("pass_criteria"))
    report["verdict"] = "PASS" if all(bool(value) for value in pass_criteria.values()) else "FAIL"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "live_speed_test_v3.json"
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("verdict", "frames_captured", "frames_displayed", "p95_frame_age_ms", "p95_overlay_age_ms", "p95_end_to_end_visual_age_ms", "queue_depth_p95", "stale_execution_packets", "endpoint_failures")}, indent=2))
    print(str(out_json))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
