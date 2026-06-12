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
    req = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-FrameTimingTraceV3/1.0", "Connection": "close"})
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump PhoenixGuard V3 frame timing trace.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default="reports/FINAL_FRAME_TIMING_TRACE_V3.json")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = urllib.parse.quote(args.session, safe="")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = _json(f"{base}/v1/mobile/performance/trace/v3/{session}", args.timeout)
    except Exception as exc:
        payload = {
            "schema_version": "PG_PERFORMANCE_TRACE_V3",
            "verdict": "FAIL",
            "session_id": args.session,
            "error": _error(exc),
            "captured_at_ms": round(time.time() * 1000.0, 3),
        }
        out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"verdict": "FAIL", "error": payload["error"], "out": str(out)}, indent=2))
        return 1
    timing = payload.get("timing_trace") or {}
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "schema_version": payload.get("schema_version"),
        "session_id": payload.get("session_id"),
        "frame_id": payload.get("frame_id"),
        "overlay_state_version": payload.get("overlay_state_version"),
        "frame_age_ms": timing.get("frame_age_ms"),
        "overlay_age_ms": timing.get("overlay_age_ms"),
        "model_vote_age_ms": timing.get("model_vote_age_ms"),
        "packet_age_ms": timing.get("packet_age_ms"),
        "frontend_render_age_ms": timing.get("frontend_render_age_ms"),
        "visual_health": payload.get("visual_health"),
        "out": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
