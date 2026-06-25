from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, gate_report, print_gate, quote_session, write_report

from phoenixguard.vision.v3_overlay_contract import VIEW_MODES, normalize_view_mode


REQUIRED_OPERATOR_MODES: tuple[str, ...] = (
    "CLEAN_LIVE",
    "ACTIVE_CONTEXT",
    "GLOBAL",
    "LOCAL",
    "SUPPLY_DEMAND",
    "TRENDLINES",
    "TRIGGER",
    "TARGET",
    "PATH",
    "COUNCIL",
    "FULL_HISTORY_READ",
    "REPLAY",
    "BROKER",
    "DIAGNOSTICS",
    "TWO_CANDLE_STUDY",
)

MODE_TO_SELECT_VALUE: dict[str, str] = {
    "CLEAN_LIVE": "clean_live",
    "ACTIVE_CONTEXT": "active_context",
    "GLOBAL": "global",
    "LOCAL": "local",
    "SUPPLY_DEMAND": "supply_demand",
    "TRENDLINES": "trendlines",
    "TRIGGER": "triggers",
    "TARGET": "targets",
    "PATH": "path",
    "COUNCIL": "council_layers",
    "FULL_HISTORY_READ": "full_history_read",
    "REPLAY": "replay",
    "BROKER": "broker",
    "DIAGNOSTICS": "deep_debug",
    "TWO_CANDLE_STUDY": "two_candle_study",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _mode_list(raw: str, *, all_modes: bool) -> list[str]:
    if all_modes:
        return [mode for mode in VIEW_MODES if mode in MODE_TO_SELECT_VALUE]
    if not raw.strip():
        return list(REQUIRED_OPERATOR_MODES)
    return [normalize_view_mode(part) for part in raw.replace(";", ",").split(",") if part.strip()]


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-V3-OverlayModeScreenshots/1.0"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator tool
            body = response.read()
            payload = _mapping(json.loads(body.decode("utf-8", errors="replace"))) if body else {}
            return {"ok": 200 <= int(response.status) < 300, "status": int(response.status), "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "payload": payload}
    except Exception as exc:
        return {"ok": False, "status": 0, "latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "payload": {}, "error": str(exc)}


def _http_json_retry(url: str, timeout: float, *, attempts: int = 3) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(max(1, int(attempts))):
        row = _http_json(url, timeout)
        row["attempt"] = attempt + 1
        last = row
        if row.get("ok"):
            return row
        time.sleep(0.35 * (attempt + 1))
    return last


def _image_metrics(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageStat
    except Exception as exc:
        return {"available": False, "reason": f"PIL unavailable: {exc}"}
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            stat = ImageStat.Stat(rgb)
            extrema = rgb.getextrema()
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture PhoenixGuard V3 dashboard screenshots for each overlay mode.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--modes", default="")
    parser.add_argument("--all-modes", action="store_true")
    parser.add_argument("--out", default=".codex_runtime/visual_evidence/overlay_modes")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--width", type=int, default=1365)
    parser.add_argument("--height", type=int, default=768)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        report = gate_report(
            schema_version="PG_CAPTURE_OVERLAY_MODE_SCREENSHOTS_V3",
            gate="Overlay Mode Screenshots",
            failures=[f"playwright unavailable: {exc}"],
            warnings=[],
            details={"session_id": args.session, "base_url": args.base_url},
        )
        out = write_report("gate_overlay_mode_screenshots_v3.json", report)
        report["out_json"] = str(out)
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print_gate("OVERLAY_MODE_SCREENSHOTS", report)
        return 1

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    dashboard_url = f"{base}/v1/mobile/window-tracker/dashboard/{session_q}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    modes = _mode_list(args.modes, all_modes=args.all_modes)
    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": args.width, "height": args.height}, device_scale_factor=1)
        try:
            try:
                page.goto(dashboard_url, wait_until="domcontentloaded", timeout=int(args.timeout * 1000.0))
                page.wait_for_selector(".console-shell", timeout=int(args.timeout * 1000.0))
                page.wait_for_timeout(1500)
            except Exception as exc:
                failures.append(f"dashboard initial hydration failed before mode capture: {exc}")
                modes = []
            for mode in modes:
                page.evaluate("window.scrollTo(0, 0)")
                select_value = MODE_TO_SELECT_VALUE.get(mode)
                if not select_value:
                    failures.append(f"{mode}: no dashboard select value")
                    continue
                backend = _http_json_retry(
                    f"{base}/v1/mobile/live/state/v3/{session_q}?mode={urllib.parse.quote(mode, safe='')}&compact=1",
                    timeout=args.timeout,
                )
                payload = _mapping(backend.get("payload"))
                expected_renderable = int(payload.get("renderable_count") or 0)
                mode_query = f"mode={urllib.parse.quote(mode, safe='')}"
                try:
                    with page.expect_response(
                        lambda response, expected=mode_query: "/v1/mobile/live/state/v3/" in response.url
                        and expected in response.url
                        and 200 <= response.status < 300,
                        timeout=int(args.timeout * 1000.0),
                    ):
                        page.select_option("#overlay-mode-select", select_value, timeout=int(args.timeout * 1000.0))
                except Exception:
                    page.select_option("#overlay-mode-select", select_value, timeout=int(args.timeout * 1000.0))
                page.wait_for_timeout(2000)
                page.evaluate(
                    """async (selectValue) => {
                      if (typeof refreshSession === "function") await refreshSession();
                      if (typeof setMode === "function") setMode("overlay");
                      if (typeof applyOverlayPreset === "function") applyOverlayPreset(selectValue);
                      if (typeof refreshLiveVisualStateForMode === "function") {
                        await refreshLiveVisualStateForMode(selectValue);
                      }
                      const select = document.querySelector("#overlay-mode-select");
                      if (select) {
                        select.value = selectValue;
                        select.dispatchEvent(new Event("change", {bubbles: true}));
                      }
                      if (typeof renderSurface === "function") renderSurface();
                      await new Promise((resolve) => setTimeout(resolve, 1500));
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
                          const select = document.querySelector("#overlay-mode-select");
                          const selected = Boolean(select && select.value === selectValue);
                          const legacy = Boolean(document.body && document.body.innerText.includes("legacy session"));
                          const updating = Boolean(document.body && (
                            document.body.innerText.includes("Live surface updating")
                            || document.body.innerText.includes("Overlay catching up")
                          ));
                          const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
                          const hasImage = Boolean(image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
                          const hotspots = document.querySelectorAll(".surface-hotspot").length;
                          return selected && !legacy && !updating && hasImage && (expectedRenderable <= 0 || hotspots > 0);
                        }""",
                        arg={"selectValue": select_value, "expectedRenderable": expected_renderable},
                        timeout=int(args.timeout * 1000.0),
                    )
                except Exception:
                    pass
                page.wait_for_timeout(350)
                ready = page.evaluate(
                    """(expectedMode) => {
                      const select = document.querySelector("#overlay-mode-select");
                      const labels = Array.from(document.querySelectorAll(".surface-hotspot span")).map((node) => node.textContent || "");
                      const image = document.querySelector("#surface-overlay.visible, #surface-raw.visible");
                      return {
                        selected_value: select ? select.value : "",
                        hotspot_count: document.querySelectorAll(".surface-hotspot").length,
                        labels,
                        expected_mode: expectedMode,
                        visible_image: Boolean(image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0),
                        body_has_legacy: Boolean(document.body && document.body.innerText.includes("legacy session")),
                      };
                    }""",
                    mode,
                )
                screenshot_path = out_dir / f"{mode.lower()}_{args.session}.png"
                try:
                    client = page.context.new_cdp_session(page)
                    send = cast(Callable[[str, Mapping[str, Any]], Any], getattr(client, "send"))
                    shot = _mapping(send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True, "fromSurface": True}))
                    screenshot_path.write_bytes(base64.b64decode(str(shot.get("data") or "")))
                except Exception:
                    page.screenshot(path=str(screenshot_path), full_page=True, timeout=int(args.timeout * 1000.0), animations="disabled")
                metrics = _image_metrics(screenshot_path)
                sample: dict[str, Any] = {
                    "mode": mode,
                    "select_value": select_value,
                    "backend_ok": backend.get("ok"),
                    "backend_status": backend.get("status"),
                    "backend_error": backend.get("error"),
                    "backend_attempt": backend.get("attempt"),
                    "backend_latency_ms": backend.get("latency_ms"),
                    "backend_active_mode": payload.get("active_mode"),
                    "backend_requested_mode": payload.get("requested_mode"),
                    "backend_renderable_count": payload.get("renderable_count"),
                    "backend_unknown_or_unmapped_terms": payload.get("unknown_or_unmapped_terms"),
                    "dashboard_ready": ready,
                    "screenshot": str(screenshot_path),
                    "metrics": metrics,
                }
                samples.append(sample)
                if not backend.get("ok"):
                    failures.append(f"{mode}: backend live-state request failed")
                if payload.get("active_mode") != mode:
                    failures.append(f"{mode}: backend active_mode={payload.get('active_mode')}")
                if ready.get("selected_value") != select_value:
                    failures.append(f"{mode}: dashboard select did not stay on {select_value}")
                if ready.get("body_has_legacy"):
                    failures.append(f"{mode}: dashboard rendered legacy session text")
                if ready.get("visible_image") is not True:
                    failures.append(f"{mode}: dashboard surface image was not visible")
                if expected_renderable > 0 and int(ready.get("hotspot_count") or 0) <= 0:
                    failures.append(f"{mode}: dashboard rendered no hotspots for {expected_renderable} backend overlays")
                if metrics.get("nonblank") is not True:
                    failures.append(f"{mode}: screenshot blank or unreadable")
        finally:
            page.close()
            browser.close()

    report = gate_report(
        schema_version="PG_CAPTURE_OVERLAY_MODE_SCREENSHOTS_V3",
        gate="Overlay Mode Screenshots",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "base_url": args.base_url,
            "dashboard_url": dashboard_url,
            "out_dir": str(out_dir),
            "modes": modes,
            "samples": samples,
        },
    )
    out = write_report("gate_overlay_mode_screenshots_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("OVERLAY_MODE_SCREENSHOTS: " + report["verdict"])
    print_gate("OVERLAY_MODE_SCREENSHOTS", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
