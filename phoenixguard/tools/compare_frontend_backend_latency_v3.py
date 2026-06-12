from __future__ import annotations

import argparse
from http.client import HTTPException
import json
from pathlib import Path
import socket
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from typing import Any


def _json(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-LatencyCompareV3/1.0", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - local operator endpoint
        return json.loads(response.read().decode("utf-8", errors="replace"))


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


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare V3 backend freshness with latest frontend heartbeat.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out-json", default="reports/FINAL_FRONTEND_BACKEND_LATENCY_V3.json")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = urllib.parse.quote(args.session, safe="")
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        live = _json(f"{base}/v1/mobile/live/state/v3/{session}", args.timeout)
        perf = _json(f"{base}/v1/mobile/performance/trace/v3/{session}", args.timeout)
        heartbeat = _json(f"{base}/v1/mobile/frontend/heartbeat/v3?session_id={session}", args.timeout)
    except Exception as exc:
        report = {
            "schema_version": "PG_FRONTEND_BACKEND_LATENCY_COMPARE_V3",
            "verdict": "FAIL",
            "session_id": args.session,
            "mismatches": ["endpoint_error"],
            "error": _error(exc),
            "captured_at_ms": round(time.time() * 1000.0, 3),
        }
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"verdict": "FAIL", "mismatches": report["mismatches"], "error": report["error"], "out_json": str(out)}, indent=2))
        return 1
    timing = perf.get("timing_trace") or {}
    mismatches: list[str] = []
    if _int(live.get("frame_id")) and _int(heartbeat.get("rendered_frame_id")) and _int(live.get("frame_id")) != _int(heartbeat.get("rendered_frame_id")):
        mismatches.append("rendered_frame_id_mismatch")
    if str(live.get("overlay_state_version") or "") and str(heartbeat.get("overlay_state_version") or "") and str(live.get("overlay_state_version")) != str(heartbeat.get("overlay_state_version")):
        mismatches.append("overlay_state_version_mismatch")
    if float(timing.get("frame_age_ms") or 0) > 2500:
        mismatches.append("frame_age_hard_stale")
    if float(timing.get("overlay_age_ms") or 0) > 2500:
        mismatches.append("overlay_age_hard_stale")
    heartbeat_age_ms = max(0.0, time.time() * 1000.0 - float(heartbeat.get("received_at_ms") or 0)) if heartbeat.get("received_at_ms") else 0.0
    if heartbeat_age_ms > 2500:
        mismatches.append("frontend_heartbeat_stale")

    report = {
        "schema_version": "PG_FRONTEND_BACKEND_LATENCY_COMPARE_V3",
        "verdict": "PASS" if not mismatches else "FAIL",
        "session_id": args.session,
        "mismatches": mismatches,
        "backend": {
            "frame_id": live.get("frame_id"),
            "state_version": live.get("state_version"),
            "overlay_state_version": live.get("overlay_state_version"),
            "frame_age_ms": timing.get("frame_age_ms"),
            "overlay_age_ms": timing.get("overlay_age_ms"),
            "model_vote_age_ms": timing.get("model_vote_age_ms"),
            "packet_age_ms": timing.get("packet_age_ms"),
        },
        "frontend": {
            "rendered_frame_id": heartbeat.get("rendered_frame_id"),
            "overlay_state_version": heartbeat.get("overlay_state_version"),
            "visible_overlay_count": heartbeat.get("visible_overlay_count"),
            "heartbeat_age_ms": round(heartbeat_age_ms, 3),
        },
    }
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "mismatches": mismatches, "out_json": str(out)}, indent=2))
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
