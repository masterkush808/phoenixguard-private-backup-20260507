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


def _backend_rendered_frame_id(live: dict[str, Any], timing: dict[str, Any]) -> int:
    for value in (
        live.get("display_frame_id"),
        timing.get("display_frame_id"),
        (live.get("broker_surface_frame") or {}).get("frame_id") if isinstance(live.get("broker_surface_frame"), dict) else 0,
        live.get("frame_id"),
    ):
        parsed = _int(value)
        if parsed > 0:
            return parsed
    return 0


def _start_dashboard_client(
    *,
    base_url: str,
    session: str,
    timeout: float,
    width: int,
    height: int,
) -> tuple[tuple[Any, Any, Any] | None, str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment dependent
        return None, f"dashboard_client_unavailable:{exc}"
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": int(width), "height": int(height)})
        url = f"{base_url.rstrip('/')}/v1/mobile/window-tracker/dashboard/{urllib.parse.quote(session, safe='')}"
        page.goto(url, wait_until="domcontentloaded", timeout=int(max(1.0, timeout) * 1000.0))
        page.wait_for_selector(".console-shell", timeout=int(max(1.0, timeout) * 1000.0))
        page.wait_for_function(
            """() => {
              const text = document.body ? document.body.innerText : "";
              const legacySource = text.includes("legacy session");
              const hotspots = document.querySelectorAll(".surface-hotspot").length;
              const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
              const hasImage = Boolean(image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
              const updating = text.includes("Live surface updating") || text.includes("Overlay catching up");
              const liveSurface = text.includes("BROKER LOCKED") || text.includes("locked to saved broker surface");
              const sessionReady = Boolean(typeof state !== "undefined" && state.session && Number(state.session.frame_id || 0) > 0);
              return sessionReady && liveSurface && !legacySource && !updating && hasImage && hotspots > 0;
            }""",
            timeout=max(15000, int(max(1.0, timeout) * 1000.0)),
        )
        page.wait_for_timeout(1000)
        return (playwright, browser, page), ""
    except Exception as exc:  # pragma: no cover - environment dependent
        try:
            playwright.stop()  # type: ignore[name-defined]
        except Exception:
            pass
        return None, f"dashboard_client_start_failed:{exc}"


def _close_dashboard_client(client: tuple[Any, Any, Any] | None) -> None:
    if client is None:
        return
    playwright, browser, _page = client
    try:
        browser.close()
    except Exception:
        pass
    try:
        playwright.stop()
    except Exception:
        pass


def _post_heartbeat_payload(base_url: str, payload: dict[str, Any], timeout: float) -> tuple[str, dict[str, Any] | None]:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/v1/mobile/frontend/heartbeat/v3",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "PhoenixGuard-LatencyCompareV3/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - local operator endpoint
            body = json.loads(response.read().decode("utf-8", errors="replace"))
        if isinstance(body, dict) and body.get("status") != "ignored":
            return "", body
        return f"dashboard_heartbeat_python_post_ignored:{body}", None
    except Exception as exc:
        return f"dashboard_heartbeat_python_post_failed:{_error(exc)}", None


def _flush_dashboard_heartbeat(client: tuple[Any, Any, Any] | None, *, base_url: str, timeout: float) -> tuple[str, dict[str, Any] | None]:
    if client is None:
        return "", None
    _playwright, _browser, page = client
    try:
        result = page.evaluate(
            """async () => {
              if (!state || !state.session) {
                return {ok: false, reason: "dashboard_state_missing"};
              }
              const liveState = Object(state.session.live_visual_state || state.session.live_state_v3 || {});
              const timing = Object(state.session.frame_timing_trace_v3 || liveState.frame_timing_trace_v3 || {});
              const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
              const nowMs = Date.now();
              const overlayMode = typeof backendOverlayMode === "function"
                ? backendOverlayMode(state.overlayMode)
                : String(state.overlayMode || "CLEAN_LIVE").trim().toUpperCase();
              const frameId = Math.max(
                Number(liveState.frame_id || 0),
                Number(state.session.frame_id || 0),
                Number(state.session.display_frame_id || 0),
                Number(state.session.frame_index || 0),
                Number(state.session.chart_frame_id || 0)
              );
              const payload = {
                session_id: state.session.session_id || location.pathname.split("/").pop(),
                surface_id: "dashboard",
                route: state.route,
                overlay_mode: overlayMode,
                surface_mode: state.mode,
                frame_id: frameId,
                rendered_frame_id: frameId,
                overlay_state_version: state.session.overlay_state_version || liveState.overlay_state_version || timing.overlay_state_version || "",
                overlay_frame_state_version: state.session.overlay_frame_state_version || liveState.overlay_frame_state_version || timing.overlay_frame_state_version || "",
                state_version: String(liveState.state_version || state.session.state_version || state.session.decision_version || ""),
                overlay_count: Number(state.session.renderable_count || liveState.renderable_count || 0),
                visible_overlay_count: document.querySelectorAll(".surface-hotspot").length,
                frontend_loaded_ms: nowMs,
                frontend_overlay_drawn_ms: nowMs,
                overlay_draw_ms: Number((state.surface || {}).lastOverlayRenderMs || 0),
                viewport: {
                  width: Number(document.querySelector("#surface-stage")?.clientWidth || 0),
                  height: Number(document.querySelector("#surface-stage")?.clientHeight || 0),
                  scroll_left: Number(document.querySelector("#surface-stage")?.scrollLeft || 0),
                  scroll_top: Number(document.querySelector("#surface-stage")?.scrollTop || 0),
                  zoom: Number((state.surface || {}).zoom || 1),
                  zoom_mode: String((state.surface || {}).zoomMode || "manual"),
                },
                render_size: {
                  width: image ? Number(image.naturalWidth || 0) : 0,
                  height: image ? Number(image.naturalHeight || 0) : 0,
                  canvas_width: image ? Number(image.clientWidth || 0) : 0,
                  canvas_height: image ? Number(image.clientHeight || 0) : 0,
                },
                full_broker_surface_visible: Boolean(image),
                frontend_state_version: String((state.surface || {}).visibleFrameKey || (state.surface || {}).viewportSignature || ""),
                sent_at_ms: nowMs,
              };
              try {
                const response = await fetch("/v1/mobile/frontend/heartbeat/v3", {
                  method: "POST",
                  cache: "no-store",
                  headers: {"Content-Type": "application/json"},
                  body: JSON.stringify(payload),
                });
                const body = await response.json().catch(() => ({}));
                return {ok: response.ok && body.status !== "ignored", status: response.status, reason: body.reason || body.status || "", body, payload};
              } catch (error) {
                return {ok: false, status: 0, reason: String(error && error.message || error), payload};
              }
            }"""
        )
        if isinstance(result, dict) and result.get("ok") and isinstance(result.get("body"), dict):
            return "", dict(result["body"])
        if isinstance(result, dict) and isinstance(result.get("payload"), dict):
            return _post_heartbeat_payload(base_url, dict(result["payload"]), timeout)
        return f"dashboard_heartbeat_flush_unavailable:{result}", None
    except Exception as exc:  # pragma: no cover - environment dependent
        return f"dashboard_heartbeat_flush_failed:{exc}", None


def _build_report(
    *,
    args: argparse.Namespace,
    live: dict[str, Any],
    heartbeat: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    timing = live.get("frame_timing_trace_v3") or (
        (live.get("performance_trace_v3") or {}).get("timing_trace")
        if isinstance(live.get("performance_trace_v3"), dict)
        else {}
    ) or {}
    mismatches: list[str] = []
    report_warnings: list[str] = list(warnings or [])
    heartbeat_status = str(heartbeat.get("status") or "").strip().lower()
    if heartbeat_status in {"missing", "stale", "fail", "error"}:
        mismatches.append(f"frontend_heartbeat_{heartbeat_status}")
    if not heartbeat.get("received_at_ms"):
        mismatches.append("frontend_heartbeat_missing_received_at_ms")
    if _int(live.get("frame_id")) and not _int(heartbeat.get("rendered_frame_id")):
        mismatches.append("frontend_heartbeat_missing_rendered_frame_id")
    if str(live.get("overlay_state_version") or "") and not str(heartbeat.get("overlay_state_version") or ""):
        mismatches.append("frontend_heartbeat_missing_overlay_state_version")
    backend_rendered_frame_id = _backend_rendered_frame_id(live, timing)
    frame_gap = 0
    if backend_rendered_frame_id and _int(heartbeat.get("rendered_frame_id")):
        frame_gap = abs(backend_rendered_frame_id - _int(heartbeat.get("rendered_frame_id")))
        if frame_gap > int(args.max_frame_gap):
            mismatches.append("rendered_frame_id_outside_backend_window")
    backend_overlay_version = str(live.get("overlay_state_version") or "")
    frontend_overlay_version = str(heartbeat.get("overlay_state_version") or "")
    backend_overlay_frame_version = str(live.get("overlay_frame_state_version") or timing.get("overlay_frame_state_version") or "")
    frontend_overlay_frame_version = str(heartbeat.get("overlay_frame_state_version") or "")
    if backend_overlay_version and frontend_overlay_version and backend_overlay_version != frontend_overlay_version:
        if frame_gap == 0:
            mismatches.append("overlay_state_version_mismatch")
        else:
            report_warnings.append("overlay_state_version_near_frame_drift")
    if backend_overlay_frame_version and frontend_overlay_frame_version and backend_overlay_frame_version != frontend_overlay_frame_version:
        if frame_gap == 0:
            mismatches.append("overlay_frame_state_version_mismatch")
        else:
            report_warnings.append("overlay_frame_state_version_near_frame_drift")
    if int(heartbeat.get("visible_overlay_count") or 0) <= 0 and int(live.get("renderable_count") or 0) > 0:
        mismatches.append("frontend_overlay_not_visible")
    if float(timing.get("frame_age_ms") or 0) > float(args.max_frame_age_ms):
        mismatches.append("frame_age_hard_stale")
    if float(timing.get("overlay_age_ms") or 0) > float(args.max_overlay_age_ms):
        mismatches.append("overlay_age_hard_stale")
    heartbeat_age_ms = (
        max(0.0, time.time() * 1000.0 - float(heartbeat.get("received_at_ms") or 0))
        if heartbeat.get("received_at_ms")
        else 0.0
    )
    if heartbeat.get("received_at_ms") and heartbeat_age_ms > float(args.max_heartbeat_age_ms):
        mismatches.append("frontend_heartbeat_stale")

    return {
        "schema_version": "PG_FRONTEND_BACKEND_LATENCY_COMPARE_V3",
        "verdict": "PASS" if not mismatches else "FAIL",
        "session_id": args.session,
        "mismatches": mismatches,
        "warnings": sorted(set(report_warnings)),
        "backend": {
            "overlay_mode": live.get("active_mode"),
            "frame_id": live.get("frame_id"),
            "display_frame_id": live.get("display_frame_id") or timing.get("display_frame_id"),
            "rendered_frame_id": backend_rendered_frame_id,
            "state_version": live.get("state_version"),
            "overlay_state_version": backend_overlay_version,
            "overlay_frame_state_version": backend_overlay_frame_version,
            "frame_age_ms": timing.get("frame_age_ms"),
            "overlay_age_ms": timing.get("overlay_age_ms"),
            "model_vote_age_ms": timing.get("model_vote_age_ms"),
            "packet_age_ms": timing.get("packet_age_ms"),
        },
        "frontend": {
            "overlay_mode": heartbeat.get("overlay_mode"),
            "rendered_frame_id": heartbeat.get("rendered_frame_id"),
            "frame_gap": frame_gap,
            "overlay_state_version": frontend_overlay_version,
            "overlay_frame_state_version": frontend_overlay_frame_version,
            "visible_overlay_count": heartbeat.get("visible_overlay_count"),
            "heartbeat_age_ms": round(heartbeat_age_ms, 3),
            "status": heartbeat.get("status"),
            "received_at_ms": heartbeat.get("received_at_ms"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare V3 backend freshness with latest frontend heartbeat.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-heartbeat-age-ms", type=float, default=2500.0)
    parser.add_argument("--max-frame-age-ms", type=float, default=2500.0)
    parser.add_argument("--max-overlay-age-ms", type=float, default=2500.0)
    parser.add_argument("--max-frame-gap", type=int, default=3)
    parser.add_argument("--mode", default="CLEAN_LIVE")
    parser.add_argument("--sample-window-sec", type=float, default=8.0)
    parser.add_argument("--sample-interval-sec", type=float, default=0.5)
    parser.add_argument("--no-dashboard-client", action="store_true")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--out-json", default="reports/FINAL_FRONTEND_BACKEND_LATENCY_V3.json")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = urllib.parse.quote(args.session, safe="")
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    dashboard_client: tuple[Any, Any, Any] | None = None
    pre_warnings: list[str] = []
    if not args.no_dashboard_client:
        dashboard_client, warning = _start_dashboard_client(
            base_url=base,
            session=args.session,
            timeout=args.timeout,
            width=args.width,
            height=args.height,
        )
        if warning:
            pre_warnings.append(warning)
    report: dict[str, Any] | None = None
    try:
        deadline = time.time() + max(0.1, float(args.sample_window_sec))
        while True:
            try:
                sample_warnings = list(pre_warnings)
                requested_mode = str(args.mode or "CLEAN_LIVE").strip().upper() or "CLEAN_LIVE"
                mode_q = urllib.parse.quote(requested_mode, safe="")
                live = _json(f"{base}/v1/mobile/live/state/v3/{session}?mode={mode_q}&compact=1", args.timeout)
                flush_warning, flushed_heartbeat = _flush_dashboard_heartbeat(dashboard_client, base_url=base, timeout=args.timeout)
                if flush_warning:
                    sample_warnings.append(flush_warning)
                heartbeat = flushed_heartbeat or _json(f"{base}/v1/mobile/frontend/heartbeat/v3?session_id={session}", args.timeout)
                heartbeat_mode = str(heartbeat.get("overlay_mode") or requested_mode).strip().upper() or requested_mode
                if heartbeat_mode != str(live.get("active_mode") or requested_mode).strip().upper():
                    mode_q = urllib.parse.quote(heartbeat_mode, safe="")
                    live = _json(f"{base}/v1/mobile/live/state/v3/{session}?mode={mode_q}&compact=1", args.timeout)
                report = _build_report(args=args, live=live, heartbeat=heartbeat, warnings=sample_warnings)
            except Exception as exc:
                report = {
                    "schema_version": "PG_FRONTEND_BACKEND_LATENCY_COMPARE_V3",
                    "verdict": "FAIL",
                    "session_id": args.session,
                    "mismatches": ["endpoint_error"],
                    "warnings": pre_warnings,
                    "error": _error(exc),
                    "captured_at_ms": round(time.time() * 1000.0, 3),
                }
            if report.get("verdict") == "PASS" or time.time() >= deadline:
                break
            time.sleep(max(0.1, float(args.sample_interval_sec)))
    finally:
        _close_dashboard_client(dashboard_client)
    assert report is not None
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    mismatches = list(report.get("mismatches") or [])
    print(json.dumps({"verdict": report["verdict"], "mismatches": mismatches, "out_json": str(out)}, indent=2))
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
