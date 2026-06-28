from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, cast


DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION = "pocket-live-8788"
DEFAULT_MAX_CAPTURE_SETS = 6
HEAVY_ARTIFACT_KINDS = {"chart", "overlay", "full-overlay"}
ROOT = Path(__file__).resolve().parents[2]
HEARTBEAT_DIR = Path(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or ROOT / "runtime" / "live") / "frontend_heartbeat_v3"
ROUTE_DEFAULT_MODE = {
    "live": "CLEAN_LIVE",
    "chart": "ACTIVE_CONTEXT",
    "council": "ACTIVE_CONTEXT",
    "replay": "REPLAY",
}
BACKEND_MODE_TO_SELECT = {
    "CLEAN_LIVE": "clean_live",
    "CHART_BOUNDS": "chart_bounds",
    "CANDLES": "candles",
    "GLOBAL": "global",
    "LOCAL": "local",
    "SUPPLY_DEMAND": "supply_demand",
    "TRENDLINES": "trendlines",
    "TRIGGER": "triggers",
    "TARGET": "targets",
    "INVALIDATION": "invalidation",
    "PATH": "path",
    "PREDICTION": "prediction",
    "ACTIVE_CONTEXT": "active_context",
    "COUNCIL": "council_layers",
    "FULL_HISTORY_READ": "full_history_read",
    "BROKER": "broker",
    "TWO_CANDLE_STUDY": "two_candle_study",
    "LSTM_STUDY": "lstm_study",
    "DEBUG": "debug",
    "INSPECTOR": "inspector",
    "DIAGNOSTICS": "deep_debug",
    "CALIBRATION": "calibration",
    "REPLAY": "replay",
}
SELECT_TO_BACKEND_MODE = {value: key for key, value in BACKEND_MODE_TO_SELECT.items()}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _resolve_max_capture_sets(value: int | None = None) -> int:
    if value is None:
        value = _env_int("PHOENIXGUARD_DASHBOARD_CAPTURE_MAX_SETS", DEFAULT_MAX_CAPTURE_SETS)
    return max(0, int(value))


def _http_bytes(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-V3-DashboardCapture/1.0"})
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
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "bytes": 0, "error": str(exc), "body": b""}
    except Exception as exc:
        return {"ok": False, "status": 0, "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "bytes": 0, "error": str(exc), "body": b""}


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    response = _http_bytes(url, timeout)
    body = response.pop("body", b"")
    try:
        response["payload"] = json.loads(body.decode("utf-8", errors="replace")) if body else {}
    except Exception as exc:
        response["payload"] = {}
        response["error"] = response.get("error") or str(exc)
    return response


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _normalize_route(value: str) -> str:
    route = str(value or "").strip().lower()
    return route if route in ROUTE_DEFAULT_MODE else "live"


def _normalize_backend_mode(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    upper = raw.upper().replace("-", "_")
    if upper in BACKEND_MODE_TO_SELECT:
        return upper
    select = raw.lower().replace("-", "_")
    return SELECT_TO_BACKEND_MODE.get(select, "")


def _dashboard_heartbeat_files(session_id: str, heartbeat_dir: Path | None = None) -> list[Path]:
    heartbeat_root = heartbeat_dir or HEARTBEAT_DIR
    if not heartbeat_root.exists():
        return []
    safe_session = re.sub(r"[^a-zA-Z0-9_.-]+", "-", session_id.strip())[:120] or "default"
    return sorted(
        heartbeat_root.glob(f"{safe_session}__dashboard*.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )


def _heartbeat_age_sec(heartbeat: Mapping[str, Any]) -> float:
    received_at_ms = float(heartbeat.get("received_at_ms") or 0.0)
    return max(0.0, (time.time() * 1000.0 - received_at_ms) / 1000.0) if received_at_ms > 0.0 else 999999.0


def _is_live_dashboard_heartbeat(heartbeat: Mapping[str, Any]) -> bool:
    route = str(heartbeat.get("route") or "").strip().lower()
    mode = str(heartbeat.get("overlay_mode") or "").strip().upper().replace("-", "_")
    return route in {"live", "dashboard", ""} and mode in {"CLEAN_LIVE", ""}


def _heartbeat_rank(heartbeat: Mapping[str, Any]) -> tuple[int, int, float]:
    artifact_kind = str(heartbeat.get("visible_artifact_kind") or "").strip().lower()
    artifact_rank = {"full-overlay": 3, "overlay": 2, "window-locked-overlay": 1}.get(artifact_kind, 0)
    return (
        artifact_rank,
        int(heartbeat.get("visible_overlay_count") or heartbeat.get("overlay_count") or 0),
        float(heartbeat.get("received_at_ms") or 0.0),
    )


def _latest_active_dashboard_heartbeat(session_id: str) -> dict[str, Any]:
    heartbeats: list[dict[str, Any]] = []
    for path in _dashboard_heartbeat_files(session_id):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, Mapping):
            continue
        heartbeat = dict(cast(Mapping[str, Any], payload))
        heartbeat["path"] = str(path)
        heartbeat["age_sec"] = round(_heartbeat_age_sec(heartbeat), 3)
        heartbeats.append(heartbeat)
    if not heartbeats:
        return {}
    live_heartbeats = [heartbeat for heartbeat in heartbeats if _is_live_dashboard_heartbeat(heartbeat)]
    fresh_live = [heartbeat for heartbeat in live_heartbeats if _heartbeat_age_sec(heartbeat) <= 45.0]
    if fresh_live:
        return max(fresh_live, key=_heartbeat_rank)
    if live_heartbeats:
        return max(live_heartbeats, key=_heartbeat_rank)
    return {}


def _resolve_capture_context(base_url: str, session_id: str, route: str, mode: str, timeout: float) -> dict[str, Any]:
    heartbeat = _latest_active_dashboard_heartbeat(session_id)
    route_requested = str(route or "active").strip().lower()
    mode_requested = str(mode or "").strip()
    resolved_route = "live" if route_requested in {"", "active"} else _normalize_route(route_requested)
    heartbeat_backend_mode = _normalize_backend_mode(str(heartbeat.get("overlay_mode") or ""))
    resolved_backend_mode = _normalize_backend_mode(mode_requested) or (
        heartbeat_backend_mode if route_requested in {"", "active"} and _is_live_dashboard_heartbeat(heartbeat) else ""
    ) or ROUTE_DEFAULT_MODE.get(resolved_route, "CLEAN_LIVE")
    resolved_select_value = BACKEND_MODE_TO_SELECT.get(resolved_backend_mode, "clean_live")
    base = base_url.rstrip("/")
    session_q = urllib.parse.quote(session_id, safe="")
    mode_q = urllib.parse.quote(resolved_backend_mode, safe="")
    live = _http_json(f"{base}/v1/mobile/live/state/v3/{session_q}?mode={mode_q}&compact=1", timeout)
    payload = _mapping(live.get("payload"))
    expected_renderable = int(payload.get("renderable_count") or _mapping(payload.get("overlays")).get("renderable_count") or 0)
    return {
        "requested_route": route_requested or "active",
        "requested_mode": mode_requested,
        "route": resolved_route,
        "backend_mode": resolved_backend_mode,
        "select_value": resolved_select_value,
        "expected_renderable_count": expected_renderable,
        "active_heartbeat": {
            key: heartbeat.get(key)
            for key in (
                "path",
                "route",
                "overlay_mode",
                "surface_mode",
                "visible_overlay_count",
                "overlay_count",
                "rendered_frame_id",
                "chart_frame_id",
                "overlay_render_frame_id",
                "chart_transform_id",
                "document_hidden",
                "age_sec",
            )
            if heartbeat.get(key) is not None
        },
        "live_state_probe": {
            "ok": live.get("ok"),
            "status": live.get("status"),
            "error": live.get("error"),
            "active_mode": payload.get("active_mode"),
            "requested_mode": payload.get("requested_mode"),
            "renderable_count": payload.get("renderable_count"),
            "overlay_count": payload.get("overlay_count"),
            "overlay_object_frame_id": payload.get("overlay_object_frame_id"),
            "chart_transform_id": payload.get("chart_transform_id"),
        },
    }


def _image_metrics(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageStat
    except Exception:
        return {"available": False, "reason": "PIL not installed"}
    try:
        with Image.open(path) as image:
            image_rgb = image.convert("RGB")
            stat = ImageStat.Stat(image_rgb)
            extrema = image_rgb.getextrema()
            channels_nonblank = [
                bool(isinstance(pair, tuple) and len(pair) >= 2 and pair[0] != pair[1])
                for pair in extrema
            ]
            return {
                "available": True,
                "width": int(image.width),
                "height": int(image.height),
                "mean": [round(float(value), 3) for value in stat.mean],
                "nonblank": any(channels_nonblank),
            }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _capture_with_playwright(
    url: str,
    output_png: Path,
    timeout_ms: int,
    width: int,
    height: int,
    *,
    select_value: str,
    expected_backend_mode: str,
    expected_renderable_count: int,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"ok": False, "method": "playwright", "skipped": True, "reason": f"playwright unavailable: {exc}"}
    try:
        output_png.parent.mkdir(parents=True, exist_ok=True)
        console_messages: list[dict[str, str]] = []
        page_errors: list[str] = []
        ready_state: dict[str, Any] = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text[:500]}))
            page.on("pageerror", lambda exc: page_errors.append(str(exc)[:500]))
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(".console-shell", timeout=timeout_ms)
            if select_value:
                try:
                    page.select_option("#overlay-mode-select", select_value, timeout=timeout_ms)
                except Exception:
                    pass
                page.wait_for_timeout(500)
                page.evaluate(
                    """async (selectValue) => {
                      if (typeof applyOverlayPreset === "function") applyOverlayPreset(selectValue);
                      if (typeof setMode === "function") setMode("overlay");
                      if (typeof refreshLiveVisualStateForMode === "function") {
                        await refreshLiveVisualStateForMode(selectValue);
                      }
                      if (typeof refreshSession === "function") await refreshSession();
                      if (typeof renderSurface === "function") renderSurface();
                      await new Promise((resolve) => setTimeout(resolve, 1200));
                      if (typeof commitSurfaceImage === "function") {
                        const overlay = document.querySelector("#surface-overlay");
                        const raw = document.querySelector("#surface-raw");
                        if (overlay) commitSurfaceImage(overlay);
                        if (raw) commitSurfaceImage(raw);
                      }
                      if (typeof renderHotspots === "function") renderHotspots();
                    }""",
                    select_value,
                )
            try:
                page.wait_for_function(
                    """({selectValue, expectedRenderable}) => {
                      const text = document.body ? document.body.innerText : "";
                      const legacySource = text.includes("legacy session");
                      const hotspots = document.querySelectorAll(".surface-hotspot").length;
                      const select = document.querySelector("#overlay-mode-select");
                      const selected = !selectValue || Boolean(select && select.value === selectValue);
                      const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
                      const hasImage = Boolean(image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
                      const imageSrc = image ? String(image.currentSrc || image.src || "") : "";
                      const fullOverlayImage = imageSrc.includes("latest-full-overlay") || imageSrc.includes("full-overlay") || imageSrc.includes("full_overlay");
                      const updating = text.includes("Live surface updating") || text.includes("Overlay catching up");
                      const liveSurface = text.includes("BROKER LOCKED") || text.includes("locked to saved broker surface");
                      const overlayReady = expectedRenderable > 0 ? (fullOverlayImage || hotspots === expectedRenderable) : (fullOverlayImage || hotspots === 0);
                      return selected && liveSurface && !legacySource && !updating && hasImage && overlayReady;
                    }""",
                    arg={"selectValue": select_value, "expectedRenderable": int(expected_renderable_count)},
                    timeout=max(15000, int(timeout_ms * 0.85)),
                )
            except Exception:
                pass
            page.wait_for_timeout(1000)
            ready_state = page.evaluate(
                """({expectedMode, expectedRenderable}) => {
                  const text = document.body ? document.body.innerText : "";
                  const hotspotLabels = Array.from(document.querySelectorAll(".surface-hotspot span")).map((node) => node.textContent || "");
                  const hotspotCount = document.querySelectorAll(".surface-hotspot").length;
                  const select = document.querySelector("#overlay-mode-select");
                  const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
                  const imageSrc = image ? String(image.currentSrc || image.src || "") : "";
                  const fullOverlayImage = imageSrc.includes("latest-full-overlay") || imageSrc.includes("full-overlay") || imageSrc.includes("full_overlay");
                  const updating = text.includes("Live surface updating") || text.includes("Overlay catching up");
                  const liveSurface = text.includes("BROKER LOCKED") || text.includes("locked to saved broker surface");
                  return {
                    expected_backend_mode: expectedMode,
                    expected_renderable_count: expectedRenderable,
                    selected_value: select ? String(select.value || "") : "",
                    live_state: liveSurface && !updating,
                    legacy_state: text.includes("legacy session"),
                    hotspot_count: hotspotCount,
                    hotspot_backend_match: expectedRenderable > 0 ? (fullOverlayImage || hotspotCount === expectedRenderable) : (fullOverlayImage || hotspotCount === 0),
                    visible_image: Boolean(image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0),
                    full_overlay_image: fullOverlayImage,
                    updating_state_visible: updating,
                    live_object_overlay: expectedRenderable > 0 ? (hotspotCount === expectedRenderable && !fullOverlayImage) : !fullOverlayImage,
                    overlay_rendered: expectedRenderable > 0 ? (fullOverlayImage || hotspotCount === expectedRenderable) : (fullOverlayImage || Boolean(hotspotLabels.length > 0)),
                    label_sample: hotspotLabels.slice(0, 12),
                  };
                }""",
                {"expectedMode": expected_backend_mode, "expectedRenderable": int(expected_renderable_count)},
            )
            try:
                client = page.context.new_cdp_session(page)
                send = cast(Callable[[str, Mapping[str, Any]], Any], getattr(client, "send"))
                shot = _mapping(send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True, "fromSurface": True}))
                output_png.write_bytes(base64.b64decode(str(shot.get("data") or "")))
            except Exception:
                page.screenshot(path=str(output_png), full_page=True, timeout=timeout_ms, animations="disabled")
            browser.close()
        metrics = _image_metrics(output_png)
        return {
            "ok": bool(metrics.get("nonblank")),
            "method": "playwright",
            "path": str(output_png),
            "metrics": metrics,
            "ready_state": ready_state,
            "console": console_messages[-20:],
            "page_errors": page_errors[-20:],
        }
    except Exception as exc:
        return {"ok": False, "method": "playwright", "reason": str(exc), "path": str(output_png)}


def _capture_bundle_groups(out_dir: Path, session_id: str) -> dict[str, list[Path]]:
    pattern = re.compile(
        rf"^(?:dashboard|latest_.+?)_{re.escape(session_id)}_(\d{{8}}_\d{{6}})\.(?:png|html|bin|jpg|jpeg|webp)$",
        re.IGNORECASE,
    )
    groups: dict[str, list[Path]] = {}
    if not out_dir.exists():
        return groups
    for path in out_dir.iterdir():
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if match is None:
            continue
        groups.setdefault(match.group(1), []).append(path)
    return groups


def prune_capture_evidence(out_dir: Path, session_id: str, *, max_capture_sets: int | None = None) -> dict[str, Any]:
    max_sets = _resolve_max_capture_sets(max_capture_sets)
    groups = _capture_bundle_groups(out_dir, session_id)
    stamps = sorted(groups)
    retention: dict[str, Any] = {
        "enabled": max_sets > 0,
        "max_capture_sets": max_sets,
        "existing_capture_sets": len(stamps),
        "retained_capture_sets": min(len(stamps), max_sets) if max_sets > 0 else len(stamps),
        "removed_files": 0,
        "removed_bytes": 0,
        "errors": [],
    }
    if max_sets <= 0 or len(stamps) <= max_sets:
        return retention

    remove_stamps = stamps[: max(0, len(stamps) - max_sets)]
    retained = set(stamps[len(remove_stamps):])
    retention["retained_stamps"] = sorted(retained)
    for stamp in remove_stamps:
        for path in groups.get(stamp, []):
            try:
                size = int(path.stat().st_size)
                path.unlink()
                retention["removed_files"] = int(retention["removed_files"]) + 1
                retention["removed_bytes"] = int(retention["removed_bytes"]) + size
            except Exception as exc:
                cast(list[dict[str, str]], retention["errors"]).append({"path": str(path), "error": str(exc)})
    retention["removed_mb"] = round(float(retention["removed_bytes"]) / (1024.0 * 1024.0), 3)
    return retention


def build_capture(
    base_url: str,
    session_id: str,
    timeout: float,
    out_dir: Path,
    width: int,
    height: int,
    skip_playwright: bool,
    *,
    route: str = "active",
    mode: str = "",
    max_capture_sets: int | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    session_q = urllib.parse.quote(session_id, safe="")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    capture_context = _resolve_capture_context(base, session_id, route, mode, timeout)
    route_name = str(capture_context.get("route") or "live")
    backend_mode = str(capture_context.get("backend_mode") or "CLEAN_LIVE")
    select_value = str(capture_context.get("select_value") or "clean_live")
    expected_renderable = int(capture_context.get("expected_renderable_count") or 0)
    dashboard_url = f"{base}/dashboard/{urllib.parse.quote(route_name, safe='')}/{session_q}?pg_no_heartbeat=1"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = out_dir / f"dashboard_{session_id}_{stamp}.png"
    dashboard_html_path = out_dir / f"dashboard_{session_id}_{stamp}.html"

    capture: dict[str, Any] = (
        {"ok": False, "method": "skipped", "skipped": True, "reason": "playwright disabled"}
        if skip_playwright
        else _capture_with_playwright(
            dashboard_url,
            screenshot_path,
            int(timeout * 1000.0),
            width,
            height,
            select_value=select_value,
            expected_backend_mode=backend_mode,
            expected_renderable_count=expected_renderable,
        )
    )
    dashboard = _http_bytes(dashboard_url, timeout)
    if dashboard.get("body"):
        dashboard_html_path.write_bytes(dashboard["body"])

    artifacts: dict[str, Any] = {}
    include_heavy_artifacts = _env_bool("PHOENIXGUARD_DASHBOARD_CAPTURE_HEAVY_ARTIFACTS", False)
    artifact_timeout = max(1.0, min(float(timeout), _env_float("PHOENIXGUARD_DASHBOARD_ARTIFACT_TIMEOUT_SEC", 8.0)))
    for kind in ("window", "chart", "overlay", "full-overlay"):
        if kind in HEAVY_ARTIFACT_KINDS and not include_heavy_artifacts:
            artifacts[kind] = {
                "ok": True,
                "skipped": True,
                "reason": "heavy_artifact_download_disabled",
                "bytes": 0,
            }
            continue
        url = f"{base}/v1/mobile/window-tracker/sessions/{session_q}/artifacts/latest-{kind}"
        if kind in {"overlay", "full-overlay"}:
            url = f"{url}?mode={urllib.parse.quote(backend_mode, safe='')}"
        row = _http_bytes(url, artifact_timeout)
        body = row.pop("body", b"")
        if body:
            suffix = ".png" if "image" in str(row.get("content_type") or "") else ".bin"
            path = out_dir / f"latest_{kind}_{session_id}_{stamp}{suffix}"
            path.write_bytes(body)
            row["path"] = str(path)
            row["metrics"] = _image_metrics(path) if suffix == ".png" else {}
        artifacts[kind] = row

    live = _http_json(
        f"{base}/v1/mobile/live/state/v3/{session_q}?mode={urllib.parse.quote(backend_mode, safe='')}&compact=1",
        timeout,
    )
    visual = _http_json(f"{base}/v1/mobile/visual/health/v3/{session_q}", timeout)
    if not visual.get("ok"):
        visual = _http_json(f"{base}/v1/mobile/visual/health/v3?session_id={session_q}", timeout)

    hard_mismatches: list[str] = []
    warnings: list[str] = []
    if not dashboard.get("ok") or int(dashboard.get("bytes") or 0) <= 0:
        hard_mismatches.append("dashboard HTML did not load")
    if not capture.get("ok"):
        if capture.get("skipped"):
            warnings.append(str(capture.get("reason")))
        else:
            hard_mismatches.append(f"dashboard screenshot failed or blank: {capture.get('reason') or capture.get('metrics')}")
    elif not capture.get("skipped"):
        ready = _mapping(capture.get("ready_state"))
        if not ready.get("live_state"):
            hard_mismatches.append("dashboard screenshot did not hydrate live/state/v3 before capture")
        if ready.get("legacy_state"):
            hard_mismatches.append("dashboard screenshot is still rendering legacy session state")
        if ready.get("updating_state_visible"):
            hard_mismatches.append("dashboard screenshot is still showing live surface updating")
        if not ready.get("overlay_rendered"):
            hard_mismatches.append("dashboard screenshot did not render the canonical backend overlay surface")
        if int(ready.get("expected_renderable_count") or expected_renderable) > 0 and not ready.get("hotspot_backend_match"):
            hard_mismatches.append(
                "dashboard hotspot count does not match backend renderable count: "
                f"{ready.get('hotspot_count')} != {ready.get('expected_renderable_count') or expected_renderable}"
            )
    for kind in ("window", "chart"):
        row = _mapping(artifacts.get(kind))
        if row.get("skipped"):
            warnings.append(f"latest {kind} artifact download skipped: {row.get('reason')}")
            continue
        if not row.get("ok") or int(row.get("bytes") or 0) <= 0:
            hard_mismatches.append(f"latest {kind} artifact missing")
        elif _mapping(row.get("metrics")).get("nonblank") is False:
            hard_mismatches.append(f"latest {kind} artifact is blank")
    if not live.get("ok"):
        warnings.append("live state endpoint unavailable during capture")
    if not visual.get("ok"):
        warnings.append("visual health endpoint unavailable during capture")

    retention: dict[str, Any] = prune_capture_evidence(out_dir, session_id, max_capture_sets=max_capture_sets)
    if retention.get("errors"):
        warnings.append(f"evidence retention had {len(retention['errors'])} cleanup error(s)")

    verdict = "PASS" if not hard_mismatches else "FAIL"
    return {
        "schema_version": "PG_FRONTEND_COCKPIT_CAPTURE_V3",
        "session_id": session_id,
        "base_url": base,
        "dashboard_url": dashboard_url,
        "capture_context": capture_context,
        "generated_epoch": time.time(),
        "verdict": verdict,
        "ok": verdict == "PASS",
        "capture": capture,
        "dashboard_html": {"ok": dashboard.get("ok"), "status": dashboard.get("status"), "bytes": dashboard.get("bytes"), "path": str(dashboard_html_path) if dashboard_html_path.exists() else None},
        "artifacts": artifacts,
        "live_state": {"ok": live.get("ok"), "status": live.get("status"), "payload": live.get("payload")},
        "visual_health": {"ok": visual.get("ok"), "status": visual.get("status"), "payload": visual.get("payload")},
        "evidence_retention": retention,
        "hard_mismatches": hard_mismatches,
        "warnings": warnings,
    }


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# PhoenixGuard V3 Frontend Cockpit Report",
        "",
        f"- Session: {report['session_id']}",
        f"- Dashboard URL: {report['dashboard_url']}",
        f"- Route/mode: {report.get('capture_context', {}).get('route', 'live')} / {report.get('capture_context', {}).get('backend_mode', 'CLEAN_LIVE')}",
        f"- Verdict: {report['verdict']}",
        f"- Screenshot: {report['capture'].get('path') or 'not captured'}",
        f"- Dashboard HTML: {report['dashboard_html'].get('path') or 'not captured'}",
        f"- Evidence retention: keep {report.get('evidence_retention', {}).get('max_capture_sets', 'default')} capture set(s), removed {report.get('evidence_retention', {}).get('removed_files', 0)} file(s)",
        "",
        "## Artifacts",
        "",
        "| kind | status | bytes | path | nonblank |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    artifacts = _mapping(report.get("artifacts"))
    for kind, raw_row in artifacts.items():
        row = _mapping(raw_row)
        metrics = _mapping(row.get("metrics"))
        nonblank = metrics.get("nonblank", "")
        lines.append(f"| {kind} | {row.get('status', 0)} | {row.get('bytes', 0)} | {row.get('path', '')} | {nonblank} |")
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
    parser = argparse.ArgumentParser(description="Capture the PhoenixGuard V3 dashboard and visual artifacts.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", "--session-id", dest="session_id", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--out-dir", default="reports/frontend_cockpit_capture")
    parser.add_argument("--out-json", default="reports/FINAL_FRONTEND_COCKPIT_REPORT.json")
    parser.add_argument("--out-md", default="reports/FINAL_FRONTEND_COCKPIT_REPORT.md")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--skip-playwright", action="store_true")
    parser.add_argument("--route", default="active", help="Dashboard route to capture: active, live, chart, council, or replay.")
    parser.add_argument("--mode", default="", help="Backend overlay mode to capture. Defaults to active heartbeat mode or route default.")
    parser.add_argument(
        "--max-capture-sets",
        type=int,
        default=None,
        help="Keep only the newest N timestamped dashboard evidence bundles in --out-dir. Set 0 to disable pruning.",
    )
    parser.add_argument("--soft", action="store_true", help="Always exit 0 after writing reports.")
    args = parser.parse_args(argv)

    report: dict[str, Any] = build_capture(
        args.base_url,
        args.session_id,
        args.timeout,
        Path(args.out_dir),
        args.width,
        args.height,
        args.skip_playwright,
        route=args.route,
        mode=args.mode,
        max_capture_sets=args.max_capture_sets,
    )
    _write_json(Path(args.out_json), report)
    _write_text(Path(args.out_md), _render_markdown(report))
    print(json.dumps({"verdict": report["verdict"], "hard_mismatches": report["hard_mismatches"], "out_json": args.out_json, "out_md": args.out_md, "screenshot": report["capture"].get("path")}, indent=2))
    return 0 if args.soft or report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
