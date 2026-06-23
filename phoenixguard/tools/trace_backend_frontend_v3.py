from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, cast


DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION = "pocket-live-8788"


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _request_json(method: str, url: str, timeout: float, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(dict(payload), default=str).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "PhoenixGuard-V3-WiringTrace/1.0"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator tool
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            payload_obj = _mapping(parsed) if isinstance(parsed, Mapping) else {"value": parsed}
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "payload": payload_obj,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        return {
            "ok": False,
            "status": int(exc.code),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "error": body or str(exc),
            "payload": {},
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "error": str(exc),
            "payload": {},
        }


def _request_bytes(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-V3-WiringTrace/1.0"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator tool
            body = response.read()
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "bytes": len(body),
                "content_type": str(response.headers.get("content-type") or ""),
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "bytes": 0, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": 0, "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "bytes": 0, "error": str(exc)}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _status_word(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# PhoenixGuard V3 Backend Frontend Wiring Report",
        "",
        f"- Session: {report['session_id']}",
        f"- Base URL: {report['base_url']}",
        f"- Verdict: {report['verdict']}",
        f"- Hard mismatches: {len(report['hard_mismatches'])}",
        "",
        "## Endpoint Matrix",
        "",
        "| endpoint | status | latency_ms | result |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, row in _mapping(report.get("endpoints")).items():
        lines.append(f"| {name} | {row.get('status', 0)} | {row.get('latency_ms', 0)} | {_status_word(bool(row.get('ok')))} |")
    lines.extend(["", "## Route Wiring"])
    for item in report.get("route_wiring_needed", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Hard Mismatches"])
    if report.get("hard_mismatches"):
        for item in report["hard_mismatches"]:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(["", "## Notes"])
    for item in report.get("warnings", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def build_report(base_url: str, session_id: str, timeout: float) -> dict[str, Any]:
    base = base_url.rstrip("/")
    session_q = urllib.parse.quote(session_id, safe="")
    heartbeat_payload = {
        "session_id": session_id,
        "surface_id": "trace_backend_frontend_v3",
        "route": f"/v1/mobile/window-tracker/dashboard/{session_id}",
        "active_mode": "DIAGNOSTICS",
        "render_mode": "probe",
        "client_timestamp_ms": int(time.time() * 1000.0),
        "viewport": {"width": 1440, "height": 900},
        "render_size": {"width": 1440, "height": 900},
        "full_broker_surface_visible": False,
    }
    endpoints = {
        "live_state_v3": _request_json("GET", f"{base}/v1/mobile/live/state/v3/{session_q}", timeout),
        "frontend_heartbeat_v3": _request_json("POST", f"{base}/v1/mobile/frontend/heartbeat/v3", timeout, heartbeat_payload),
        "visual_health_v3_path": _request_json("GET", f"{base}/v1/mobile/visual/health/v3/{session_q}", timeout),
        "visual_health_v3_query": _request_json("GET", f"{base}/v1/mobile/visual/health/v3?session_id={session_q}", timeout),
        "tracker_session": _request_json("GET", f"{base}/v1/mobile/window-tracker/sessions/{session_q}", timeout),
        "registry_active": _request_json("GET", f"{base}/v1/mobile/registry/sessions/{session_q}/active?min_truth_score=0.0", timeout),
        "dashboard": _request_bytes(f"{base}/v1/mobile/window-tracker/dashboard/{session_q}", timeout),
        "latest_window": _request_bytes(f"{base}/v1/mobile/window-tracker/sessions/{session_q}/artifacts/latest-window", timeout),
        "latest_chart": _request_bytes(f"{base}/v1/mobile/window-tracker/sessions/{session_q}/artifacts/latest-chart", timeout),
    }
    hard_mismatches: list[str] = []
    warnings: list[str] = []
    route_wiring_needed: list[str] = []

    if not endpoints["live_state_v3"]["ok"]:
        hard_mismatches.append("canonical live state route is not healthy: GET /v1/mobile/live/state/v3/{session_id}")
        route_wiring_needed.append("Wire GET /v1/mobile/live/state/v3/{session_id} to the canonical live visual state builder.")
    if not endpoints["frontend_heartbeat_v3"]["ok"]:
        hard_mismatches.append("frontend heartbeat route is not healthy: POST /v1/mobile/frontend/heartbeat/v3")
        route_wiring_needed.append("Wire POST /v1/mobile/frontend/heartbeat/v3 to realtime_sync_v3.record_frontend_heartbeat.")
    if not endpoints["visual_health_v3_path"]["ok"]:
        route_wiring_needed.append("Add path-form GET /v1/mobile/visual/health/v3/{session_id}; the existing query route can remain as a fallback.")
        if endpoints["visual_health_v3_query"]["ok"]:
            warnings.append("visual health query endpoint works, but the path endpoint required by the final plan is missing.")
        else:
            hard_mismatches.append("visual health V3 is not healthy in path or query form.")
    for name in ("dashboard", "latest_window", "latest_chart"):
        row = endpoints[name]
        if not row.get("ok") or int(row.get("bytes") or 0) <= 0:
            hard_mismatches.append(f"{name} did not return a usable payload")

    live_payload = _mapping(endpoints["live_state_v3"].get("payload"))
    if live_payload:
        if str(live_payload.get("session_id") or session_id) != session_id:
            hard_mismatches.append(f"live state session mismatch: {live_payload.get('session_id')} != {session_id}")
        if not (_mapping(live_payload.get("broker_surface")).get("url") or _mapping(live_payload.get("window_frame")).get("url")):
            warnings.append("live state did not expose an obvious full broker surface image URL.")

    verdict = "PASS" if not hard_mismatches else "FAIL"
    return {
        "schema_version": "PG_BACKEND_FRONTEND_WIRING_TRACE_V3",
        "base_url": base,
        "session_id": session_id,
        "generated_epoch": time.time(),
        "verdict": verdict,
        "ok": verdict == "PASS",
        "endpoints": endpoints,
        "hard_mismatches": hard_mismatches,
        "warnings": warnings,
        "route_wiring_needed": route_wiring_needed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trace PhoenixGuard V3 backend/frontend route wiring.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", "--session-id", dest="session_id", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out-json", default="reports/FINAL_BACKEND_FRONTEND_WIRING_REPORT.json")
    parser.add_argument("--out-md", default="reports/FINAL_BACKEND_FRONTEND_WIRING_REPORT.md")
    parser.add_argument("--soft", action="store_true", help="Always exit 0 after writing reports.")
    args = parser.parse_args(argv)

    report = build_report(args.base_url, args.session_id, args.timeout)
    _write_json(Path(args.out_json), report)
    _write_text(Path(args.out_md), _render_markdown(report))
    print(json.dumps({"verdict": report["verdict"], "hard_mismatches": report["hard_mismatches"], "out_json": args.out_json, "out_md": args.out_md}, indent=2))
    return 0 if args.soft or report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
