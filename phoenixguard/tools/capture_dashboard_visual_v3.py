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
from typing import Any, Mapping


DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION = "pocket-live-8788"
DEFAULT_MAX_CAPTURE_SETS = 6
HEAVY_ARTIFACT_KINDS = {"chart", "overlay", "full-overlay"}


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


def _capture_with_playwright(url: str, output_png: Path, timeout_ms: int, width: int, height: int) -> dict[str, Any]:
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
            try:
                page.wait_for_function(
                    """() => {
                      const text = document.body ? document.body.innerText : "";
                      const legacySource = text.includes("legacy session");
                      const hotspots = document.querySelectorAll(".surface-hotspot").length;
                      const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
                      const hasImage = Boolean(image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
                      const imageSrc = image ? String(image.currentSrc || image.src || "") : "";
                      const fullOverlayImage = imageSrc.includes("latest-full-overlay") || imageSrc.includes("full-overlay");
                      const updating = text.includes("Live surface updating") || text.includes("Overlay catching up");
                      const liveSurface = text.includes("BROKER LOCKED") || text.includes("locked to saved broker surface");
                      return liveSurface && !legacySource && !updating && hasImage && (hotspots > 0 || fullOverlayImage);
                    }""",
                    timeout=max(15000, int(timeout_ms * 0.85)),
                )
            except Exception:
                pass
            page.wait_for_timeout(1000)
            ready_state = page.evaluate(
                """() => {
                  const text = document.body ? document.body.innerText : "";
                  const hotspotLabels = Array.from(document.querySelectorAll(".surface-hotspot span")).map((node) => node.textContent || "");
                  const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
                  const imageSrc = image ? String(image.currentSrc || image.src || "") : "";
                  const fullOverlayImage = imageSrc.includes("latest-full-overlay") || imageSrc.includes("full-overlay");
                  const updating = text.includes("Live surface updating") || text.includes("Overlay catching up");
                  const liveSurface = text.includes("BROKER LOCKED") || text.includes("locked to saved broker surface");
                  return {
                    live_state: liveSurface && !updating,
                    legacy_state: text.includes("legacy session"),
                    hotspot_count: document.querySelectorAll(".surface-hotspot").length,
                    visible_image: Boolean(image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0),
                    full_overlay_image: fullOverlayImage,
                    updating_state_visible: updating,
                    overlay_rendered: Boolean((hotspotLabels.length > 0) || fullOverlayImage),
                    label_sample: hotspotLabels.slice(0, 12),
                  };
                }"""
            )
            try:
                client = page.context.new_cdp_session(page)
                shot = client.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True, "fromSurface": True})
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
    retention = {
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
                retention["errors"].append({"path": str(path), "error": str(exc)})
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
    max_capture_sets: int | None = None,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    session_q = urllib.parse.quote(session_id, safe="")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dashboard_url = f"{base}/v1/mobile/window-tracker/dashboard/{session_q}"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = out_dir / f"dashboard_{session_id}_{stamp}.png"
    dashboard_html_path = out_dir / f"dashboard_{session_id}_{stamp}.html"

    capture = {"ok": False, "method": "skipped", "skipped": True, "reason": "playwright disabled"} if skip_playwright else _capture_with_playwright(dashboard_url, screenshot_path, int(timeout * 1000.0), width, height)
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
        row = _http_bytes(url, artifact_timeout)
        body = row.pop("body", b"")
        if body:
            suffix = ".png" if "image" in str(row.get("content_type") or "") or kind != "html" else ".bin"
            path = out_dir / f"latest_{kind}_{session_id}_{stamp}{suffix}"
            path.write_bytes(body)
            row["path"] = str(path)
            row["metrics"] = _image_metrics(path) if suffix == ".png" else {}
        artifacts[kind] = row

    live = _http_json(f"{base}/v1/mobile/live/state/v3/{session_q}", timeout)
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
        ready = capture.get("ready_state") or {}
        if not ready.get("live_state"):
            hard_mismatches.append("dashboard screenshot did not hydrate live/state/v3 before capture")
        if ready.get("legacy_state"):
            hard_mismatches.append("dashboard screenshot is still rendering legacy session state")
        if ready.get("updating_state_visible"):
            hard_mismatches.append("dashboard screenshot is still showing live surface updating")
        if not ready.get("overlay_rendered"):
            hard_mismatches.append("dashboard screenshot did not render DOM hotspots or the full-overlay artifact")
    for kind in ("window", "chart"):
        row = artifacts.get(kind, {})
        if row.get("skipped"):
            warnings.append(f"latest {kind} artifact download skipped: {row.get('reason')}")
            continue
        if not row.get("ok") or int(row.get("bytes") or 0) <= 0:
            hard_mismatches.append(f"latest {kind} artifact missing")
        elif row.get("metrics") and row["metrics"].get("nonblank") is False:
            hard_mismatches.append(f"latest {kind} artifact is blank")
    if not live.get("ok"):
        warnings.append("live state endpoint unavailable during capture")
    if not visual.get("ok"):
        warnings.append("visual health endpoint unavailable during capture")

    retention = prune_capture_evidence(out_dir, session_id, max_capture_sets=max_capture_sets)
    if retention.get("errors"):
        warnings.append(f"evidence retention had {len(retention['errors'])} cleanup error(s)")

    verdict = "PASS" if not hard_mismatches else "FAIL"
    return {
        "schema_version": "PG_FRONTEND_COCKPIT_CAPTURE_V3",
        "session_id": session_id,
        "base_url": base,
        "dashboard_url": dashboard_url,
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
    for kind, row in report.get("artifacts", {}).items():
        metrics = row.get("metrics") if isinstance(row, Mapping) else {}
        nonblank = metrics.get("nonblank") if isinstance(metrics, Mapping) else ""
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
    parser.add_argument(
        "--max-capture-sets",
        type=int,
        default=None,
        help="Keep only the newest N timestamped dashboard evidence bundles in --out-dir. Set 0 to disable pruning.",
    )
    parser.add_argument("--soft", action="store_true", help="Always exit 0 after writing reports.")
    args = parser.parse_args(argv)

    report = build_capture(
        args.base_url,
        args.session_id,
        args.timeout,
        Path(args.out_dir),
        args.width,
        args.height,
        args.skip_playwright,
        max_capture_sets=args.max_capture_sets,
    )
    _write_json(Path(args.out_json), report)
    _write_text(Path(args.out_md), _render_markdown(report))
    print(json.dumps({"verdict": report["verdict"], "hard_mismatches": report["hard_mismatches"], "out_json": args.out_json, "out_md": args.out_md, "screenshot": report["capture"].get("path")}, indent=2))
    return 0 if args.soft or report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
