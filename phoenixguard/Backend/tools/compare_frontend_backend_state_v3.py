from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence, cast


DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION = "pocket-live-8788"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def _http_json(method: str, url: str, timeout: float, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(dict(payload), default=str).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "PhoenixGuard-V3-StateCompare/1.0"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator tool
            parsed: object = json.loads(response.read().decode("utf-8", errors="replace"))
            payload_obj: object = dict(cast(Mapping[str, Any], parsed)) if isinstance(parsed, Mapping) else parsed
            return {"ok": 200 <= int(response.status) < 300, "status": int(response.status), "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "payload": payload_obj}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "error": str(exc), "payload": {}}
    except Exception as exc:
        return {"ok": False, "status": 0, "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "error": str(exc), "payload": {}}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _overlay_count(payload: Mapping[str, Any]) -> int | None:
    for value in (
        payload.get("overlay_objects"),
        payload.get("overlays"),
        _mapping(payload.get("visual")).get("overlay_objects"),
        _mapping(payload.get("visual")).get("overlays"),
        _mapping(payload.get("market_object_registry")).get("active_overlays"),
    ):
        rows = _sequence(value)
        if rows:
            return len(rows)
    for value in (
        payload.get("overlay_count"),
        _mapping(payload.get("visual")).get("overlay_count"),
        _mapping(payload.get("market_object_registry")).get("count"),
    ):
        if value not in (None, ""):
            try:
                return int(value)
            except Exception:
                pass
    return None


def _digest_live_state(payload: Mapping[str, Any], session_id: str) -> dict[str, Any]:
    chart_transform = _mapping(payload.get("chart_transform"))
    broker_surface = _mapping(payload.get("broker_surface"))
    chart_frame = _mapping(payload.get("chart_frame"))
    return {
        "session_id": str(_first(payload.get("session_id"), session_id)),
        "frame_id": _first(payload.get("frame_id"), chart_frame.get("frame_id"), broker_surface.get("frame_id"), payload.get("frame_index")),
        "state_version": _first(payload.get("state_version"), payload.get("version"), payload.get("frame_index")),
        "chart_transform_id": str(_first(payload.get("chart_transform_id"), chart_transform.get("chart_transform_id"), chart_frame.get("chart_transform_id"), "")),
        "overlay_count": _overlay_count(payload),
        "active_mode": str(_first(payload.get("active_mode"), payload.get("visible_mode"), "CLEAN_LIVE")),
        "full_broker_surface_available": bool(_first(broker_surface.get("url"), broker_surface.get("image_url"), _mapping(payload.get("window_frame")).get("url"))),
    }


def _heartbeat_payload(session_id: str, backend: Mapping[str, Any], route: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "surface_id": "compare_frontend_backend_state_v3",
        "route": route,
        "active_mode": str(backend.get("active_mode") or "DIAGNOSTICS"),
        "render_mode": "probe",
        "state_version": backend.get("state_version"),
        "frame_id": backend.get("frame_id"),
        "chart_transform_id": backend.get("chart_transform_id"),
        "overlay_count": backend.get("overlay_count"),
        "visible_overlay_count": backend.get("overlay_count"),
        "client_timestamp_ms": int(time.time() * 1000.0),
        "viewport": {"width": 1440, "height": 900},
        "render_size": {"width": 1440, "height": 900},
        "full_broker_surface_visible": bool(backend.get("full_broker_surface_available")),
    }


def build_report(base_url: str, session_id: str, timeout: float, probe_heartbeat: bool = True) -> dict[str, Any]:
    base = base_url.rstrip("/")
    session_q = urllib.parse.quote(session_id, safe="")
    live = _http_json("GET", f"{base}/v1/mobile/live/state/v3/{session_q}", timeout)
    visual_path = _http_json("GET", f"{base}/v1/mobile/visual/health/v3/{session_q}", timeout)
    visual_query = _http_json("GET", f"{base}/v1/mobile/visual/health/v3?session_id={session_q}", timeout)
    live_payload = _mapping(live.get("payload"))
    backend: dict[str, Any] = _digest_live_state(live_payload, session_id) if live.get("ok") else {"session_id": session_id}
    heartbeat: dict[str, Any] = {"ok": False, "status": 0, "payload": {}, "skipped": not probe_heartbeat}
    if probe_heartbeat:
        heartbeat = _http_json("POST", f"{base}/v1/mobile/frontend/heartbeat/v3", timeout, _heartbeat_payload(session_id, backend, f"/v3/mobile/window-tracker/dashboard/{session_id}"))

    hard_mismatches: list[str] = []
    warnings: list[str] = []
    if not live.get("ok"):
        hard_mismatches.append("canonical live state endpoint failed")
    if probe_heartbeat and not heartbeat.get("ok"):
        hard_mismatches.append("frontend heartbeat endpoint failed")
    visual = visual_path if visual_path.get("ok") else visual_query
    if not visual.get("ok"):
        hard_mismatches.append("visual health endpoint failed in path and query forms")
    elif not visual_path.get("ok"):
        warnings.append("visual health path route is missing; query fallback was used")

    hb_payload = _mapping(heartbeat.get("payload"))
    frontend = _mapping(hb_payload.get("frontend")) or _mapping(hb_payload.get("heartbeat")) or hb_payload
    if live.get("ok") and heartbeat.get("ok"):
        comparisons: dict[str, tuple[Any, Any]] = {
            "session_id": (frontend.get("session_id"), backend.get("session_id")),
            "frame_id": (frontend.get("frame_id"), backend.get("frame_id")),
            "chart_transform_id": (frontend.get("chart_transform_id"), backend.get("chart_transform_id")),
            "overlay_count": (frontend.get("overlay_count"), backend.get("overlay_count")),
        }
        for field, (frontend_value, backend_value) in comparisons.items():
            if frontend_value in (None, "") or backend_value in (None, ""):
                continue
            if str(frontend_value) != str(backend_value):
                hard_mismatches.append(f"{field} mismatch frontend={frontend_value} backend={backend_value}")
    if not backend.get("full_broker_surface_available"):
        warnings.append("backend digest did not prove full broker surface availability")

    verdict = "PASS" if not hard_mismatches else "FAIL"
    return {
        "schema_version": "PG_FRONTEND_BACKEND_STATE_COMPARE_V3",
        "session_id": session_id,
        "base_url": base,
        "generated_epoch": time.time(),
        "verdict": verdict,
        "ok": verdict == "PASS",
        "backend": backend,
        "frontend": frontend,
        "endpoints": {"live_state": live, "visual_health_path": visual_path, "visual_health_query": visual_query, "frontend_heartbeat": heartbeat},
        "hard_mismatches": hard_mismatches,
        "warnings": warnings,
    }


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# PhoenixGuard V3 Realtime Sync Report",
        "",
        f"- Session: {report['session_id']}",
        f"- Base URL: {report['base_url']}",
        f"- Verdict: {report['verdict']}",
        "",
        "## Backend Digest",
    ]
    for key, value in _mapping(report.get("backend")).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Frontend Digest"])
    for key, value in _mapping(report.get("frontend")).items():
        if key in {"viewport", "render_size", "warnings", "extra"}:
            continue
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Hard Mismatches"])
    if report.get("hard_mismatches"):
        lines.extend(f"- {item}" for item in report["hard_mismatches"])
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings"])
    if report.get("warnings"):
        lines.extend(f"- {item}" for item in report["warnings"])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare PhoenixGuard V3 canonical backend state with frontend heartbeat state.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", "--session-id", dest="session_id", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--no-probe-heartbeat", action="store_true")
    parser.add_argument("--out-json", default="reports/FINAL_REALTIME_SYNC_REPORT.json")
    parser.add_argument("--out-md", default="reports/FINAL_REALTIME_SYNC_REPORT.md")
    parser.add_argument("--soft", action="store_true", help="Always exit 0 after writing reports.")
    args = parser.parse_args(argv)

    report = build_report(args.base_url, args.session_id, args.timeout, probe_heartbeat=not args.no_probe_heartbeat)
    _write_json(Path(args.out_json), report)
    _write_text(Path(args.out_md), _render_markdown(report))
    print(json.dumps({"verdict": report["verdict"], "hard_mismatches": report["hard_mismatches"], "out_json": args.out_json, "out_md": args.out_md}, indent=2))
    return 0 if args.soft or report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
