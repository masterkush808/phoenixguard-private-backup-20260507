from __future__ import annotations

import copy
import json
import math
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright


DASHBOARD_PATH = Path("Frontend/dashboard/static/window_tracker_dashboard.html")
SURFACE_IMAGE_BYTES = b"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="700" viewBox="0 0 1200 700">
<rect width="1200" height="700" fill="#090b0d"/>
<path d="M0 140H1200M0 280H1200M0 420H1200M0 560H1200" stroke="#1b2329"/>
</svg>"""


def _composite_forecast_geometry() -> tuple[
    list[list[float]], list[dict[str, Any]], list[list[float]]
]:
    candles: list[dict[str, Any]] = []
    previous_close = 0.50
    sell_steps = {3, 7, 10}
    for step in range(1, 13):
        movement_side = "SELL" if step in sell_steps else "BUY"
        close = (
            previous_close + 0.006
            if movement_side == "SELL"
            else previous_close - 0.008
        )
        x_norm = 0.73 + (step - 1) * 0.018
        candles.append(
            {
                "step": step,
                "label": f"E{step}",
                "x_norm": round(x_norm, 4),
                "open_y_norm": round(previous_close, 4),
                "high_y_norm": round(min(previous_close, close) - 0.006, 4),
                "low_y_norm": round(max(previous_close, close) + 0.006, 4),
                "close_y_norm": round(close, 4),
                "movement_side": movement_side,
                "position_side": "BUY",
                "body_bias": "BUY",
                "direction_conflict": movement_side == "SELL",
            }
        )
        previous_close = close

    line_points = [[0.712, 0.50]] + [
        [float(row["x_norm"]), float(row["close_y_norm"])] for row in candles
    ]
    upper = [
        [float(row["x_norm"]), round(float(row["high_y_norm"]) - 0.012, 4)]
        for row in candles
    ]
    lower = [
        [float(row["x_norm"]), round(float(row["low_y_norm"]) + 0.012, 4)]
        for row in reversed(candles)
    ]
    return line_points, candles, upper + lower


def _multimodal_forecast_scenarios(primary: list[list[float]]) -> list[dict[str, Any]]:
    anchor_x, anchor_y = primary[0]
    sell_path = [[anchor_x, anchor_y]]
    hold_path = [[anchor_x, anchor_y]]
    for index, (x_norm, _) in enumerate(primary[1:], start=1):
        sell_path.append([x_norm, round(anchor_y + index * 0.006, 4)])
        hold_path.append(
            [x_norm, round(anchor_y + (0.002 if index % 2 else -0.002), 4)]
        )
    return [
        {
            "side": "BUY",
            "label": "BUY scenario",
            "probability": 0.46,
            "probability_calibrated": False,
            "selected": True,
            "line_points": primary,
            "event_count": 12,
        },
        {
            "side": "SELL",
            "label": "SELL scenario",
            "probability": 0.34,
            "probability_calibrated": False,
            "selected": False,
            "line_points": sell_path,
            "event_count": 12,
        },
        {
            "side": "HOLD",
            "label": "HOLD scenario",
            "probability": 0.20,
            "probability_calibrated": False,
            "selected": False,
            "line_points": hold_path,
            "event_count": 12,
        },
    ]


def _renderable_dashboard_html() -> str:
    html = DASHBOARD_PATH.read_text(encoding="utf-8")
    return (
        html.replace("__SESSION_ID_JSON__", json.dumps("operator-test"))
        .replace("__SESSION_LABEL__", "operator-test")
        .replace("__OVERLAY_EDITOR_SETTINGS_JSON__", "{}")
        .replace("__MODEL_STRENGTH_SETTINGS_JSON__", "{}")
    )


def _operator_payload(
    *, action: str = "WAIT", window_open: bool = False
) -> dict[str, Any]:
    observed_at = 4_102_444_500.0
    allowed = action in {"BUY_NOW", "SELL_NOW"}
    side = "SELL" if action == "SELL_NOW" else "BUY"
    setup_window_open = allowed or window_open
    forecast_line, forecast_candles, forecast_band = _composite_forecast_geometry()
    location_guidance = (
        "Aim for a higher price inside the verified supply or retest area; do not chase lows."
        if side == "SELL"
        else "Aim for a lower price inside the verified demand or retest area; do not chase highs."
    )
    return {
        "schema_version": "PG_OPERATOR_WORKSPACE_V1",
        "session_id": "operator-test",
        "revision": 42,
        "market": {"symbol": "EUR/USD", "timeframe": "M5"},
        "tracking": {
            "active": True,
            "state": "LIVE",
            "updated_at": observed_at,
            "history_count": 2,
        },
        "freshness": {
            "state": "FRESH",
            "observed_at": observed_at,
            "valid_until": observed_at + 300,
            "age_seconds": 1,
        },
        "current_move": {
            "direction": side,
            "state": "ACTIVE",
            "confidence": 0.84,
            "observed_at": observed_at,
            "started_at": observed_at - 120,
            "ended_at": None,
            "frame_id": 42,
            "summary": (
                "Price is moving down in the newest valid observation."
                if side == "SELL"
                else "Price is moving up in the newest valid observation."
            ),
        },
        "forecast": {
            "direction": side,
            "confidence": 0.73,
            "horizon_seconds": 300,
            "summary": (
                "Price may continue downward, but this forecast is not entry permission."
                if side == "SELL"
                else "Price may continue upward, but this forecast is not entry permission."
            ),
        },
        "permission": {
            "action": action,
            "allowed": allowed,
            "side": side if allowed else "NEUTRAL",
            "message": (
                f"A verified {side.lower()} entry window is open."
                if allowed
                else "The setup window remains open, but current-frame permission is refreshing."
                if setup_window_open
                else "Wait. Earlier sell pressure ended and the new upward move still needs confirmation."
            ),
            "next_condition": (
                "Use the verified price area; stop if live truth changes."
                if allowed
                else "Wait while Phoenix Guard refreshes current-frame permission."
                if setup_window_open
                else "Wait for a fresh entry window that agrees with the current upward move."
            ),
            "window_open": setup_window_open,
            "valid_for_seconds": 720 if setup_window_open else None,
            "window_label": "Open · 12m 00s remaining"
            if setup_window_open
            else "Closed",
            "entry_location": ("HIGHER_PRICE" if side == "SELL" else "LOWER_PRICE")
            if allowed
            else "NONE",
            "entry_guidance": location_guidance if allowed else "",
            "expires_at": observed_at + 720 if setup_window_open else None,
        },
        "pressure_event": {
            "direction": "SELL",
            "state": "ENDED",
            "confidence": 0.79,
            "observed_at": observed_at - 180,
            "started_at": observed_at - 300,
            "ended_at": observed_at - 60,
            "frame_id": 41,
            "summary": "The previous downward pressure has ended.",
        },
        "surface": {
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=42",
            "primary_space": "window",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=42",
            "fallback_space": "chart",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=42",
            "overlay_viewport": {
                "source_space": "chart",
                "target_space": "window",
                "coordinate_units": "normalized",
                "bounds": [0.10, 0.12, 0.90, 0.92],
            },
            "frame_id": 42,
            "updated_at": observed_at,
        },
        "overlays": [
            {
                "id": "chart-bounds-current",
                "type": "bounds",
                "side": "HOLD",
                "group": "structure",
                "family": "chart_bounds",
                "layer": "chart_bounds",
                "label": "Chart bounds",
                "bounds": [0.01, 0.01, 0.99, 0.99],
                "points": [],
                "line_points": [],
                "confidence": 1.0,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
            },
            {
                "id": "demand-current",
                "type": "zone",
                "side": "BUY",
                "group": "zones",
                "family": "supply_demand",
                "layer": "supply_demand",
                "label": "Demand area",
                "bounds": [0.08, 0.18, 0.46, 0.72],
                "points": [],
                "line_points": [],
                "confidence": 0.82,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
            },
            {
                "id": "support-current",
                "type": "trend",
                "side": "BUY",
                "group": "structure",
                "family": "trendlines",
                "layer": "trendlines",
                "label": "Support trend",
                "bounds": [0.14, 0.24, 0.86, 0.36],
                "points": [[0.14, 0.36], [0.48, 0.30], [0.86, 0.24]],
                "line_points": [[0.14, 0.36], [0.48, 0.30], [0.86, 0.24]],
                "confidence": 0.74,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
            },
            {
                "id": "past-sell",
                "type": "movement",
                "side": "SELL",
                "group": "history",
                "family": "history",
                "layer": "historical_replay",
                "label": "Earlier down move",
                "bounds": [0.48, 0.24, 0.88, 0.68],
                "points": [],
                "line_points": [],
                "confidence": 0.68,
                "lifecycle": "historical",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
            },
            {
                "id": "smc-order-block",
                "type": "zone",
                "side": "BUY",
                "group": "plan",
                "family": "smc",
                "layer": "smart_money",
                "label": "SMC order block",
                "label_hidden": True,
                "bounds": [0.52, 0.18, 0.68, 0.32],
                "points": [],
                "line_points": [],
                "confidence": 0.78,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
            },
            {
                "id": "council-current",
                "type": "plan",
                "side": "BUY",
                "group": "plan",
                "family": "council",
                "layer": "active_council_decision",
                "label": "Council read",
                "bounds": [0.70, 0.18, 0.78, 0.30],
                "points": [],
                "line_points": [],
                "confidence": 0.71,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
            },
            {
                "id": "two-candle-current",
                "type": "outlook",
                "side": "BUY",
                "group": "outlook",
                "family": "two_candle",
                "layer": "active_council_decision",
                "label": "Two-candle study",
                "bounds": [0.60, 0.44, 0.72, 0.62],
                "points": [],
                "line_points": [],
                "confidence": 0.69,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
            },
            {
                "id": "lstm-current",
                "type": "outlook",
                "side": "HOLD",
                "group": "outlook",
                "family": "lstm",
                "layer": "prediction_path",
                "label": "LSTM 12-event path · no edge",
                "bounds": [0.70, 0.38, 0.95, 0.53],
                "points": [],
                "line_points": forecast_line,
                "confidence": 0.0,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
                "forecast_coordinate_space": "chart",
                "forecast_coordinate_units": "normalized",
                "forecast_role": "composite",
                "forecast_status": "NO_EDGE",
                "forecast_authorized": False,
                "forecast_direction": "BUY",
                "trajectory_mode": "BUY",
                "trajectory_mode_probability_calibrated": False,
                "path_confidence_status": "UNAVAILABLE",
                "body_bias": "BUY",
                "direction_conflict": True,
                "interval": {
                    "calibrated": False,
                    "status": "UNAVAILABLE",
                    "coverage": None,
                },
                "forecast_band_points": forecast_band,
                "forecast_candles": forecast_candles,
                "forecast_scenarios": _multimodal_forecast_scenarios(forecast_line),
                "forecast_anchor": {
                    "x_norm": forecast_line[0][0],
                    "y_norm": forecast_line[0][1],
                    "verified_latest_close": True,
                    "source": "TRACKER_LATEST_CLOSE",
                },
            },
        ],
        "history": [
            {
                "observed_at": observed_at - 180,
                "direction": "SELL",
                "state": "ENDED",
                "summary": "Downward movement ended.",
                "frame_id": 41,
            },
            {
                "observed_at": observed_at,
                "direction": "BUY",
                "state": "CURRENT",
                "summary": "Upward movement is current.",
                "frame_id": 42,
            },
        ],
    }


@pytest.fixture(scope="module")
def chromium_browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@contextmanager
def _dashboard_page(
    browser: Browser,
    payload: dict[str, Any],
    *,
    viewport: tuple[int, int] = (1440, 1000),
    delayed_artifact_frames: dict[int, float] | None = None,
    with_event_source: bool = False,
) -> Generator[Page, None, None]:
    html = _renderable_dashboard_html()
    context = browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]}
    )
    page = context.new_page()

    def route_dashboard(route: Route) -> None:
        url = route.request.url
        if url == "http://dashboard.test/":
            route.fulfill(status=200, content_type="text/html", body=html)
        elif "/artifacts/latest-" in url:
            for frame_id, delay_seconds in (delayed_artifact_frames or {}).items():
                if f"frame_id={frame_id}" in url:
                    time.sleep(delay_seconds)
                    break
            route.fulfill(
                status=200, content_type="image/svg+xml", body=SURFACE_IMAGE_BYTES
            )
        else:
            route.abort()

    page.route("http://dashboard.test/**", route_dashboard)
    payload_json = json.dumps(payload).replace("</", "<\\/")
    event_source_bootstrap = (
        """
        window.__EVENT_SOURCES = [];
        class DashboardTestEventSource {
          constructor(url) {
            this.url = String(url);
            this.readyState = 0;
            this.listeners = new Map();
            window.__EVENT_SOURCES.push(this);
            queueMicrotask(() => {
              if (this.readyState !== 2) {
                this.readyState = 1;
                if (typeof this.onopen === "function") this.onopen({type: "open"});
              }
            });
          }
          addEventListener(type, callback) {
            const callbacks = this.listeners.get(type) || [];
            callbacks.push(callback);
            this.listeners.set(type, callbacks);
          }
          emit(type, body = {}) {
            const event = {type, data: JSON.stringify(body)};
            (this.listeners.get(type) || []).forEach(callback => callback(event));
          }
          close() {
            this.readyState = 2;
          }
        }
        Object.defineProperty(window, "EventSource", {
          value: DashboardTestEventSource, configurable: true
        });
        """
        if with_event_source
        else 'Object.defineProperty(window, "EventSource", {value: undefined, configurable: true});'
    )
    page.add_init_script(
        f"""
        window.__OPERATOR_PAYLOAD = {payload_json};
        window.__FETCH_URLS = [];
        window.__FETCH_REQUESTS = [];
        window.__OPERATOR_FETCH_DELAY_MS = 0;
        window.__FORECAST_ACTION_DELAY_MS = 0;
        window.__FORECAST_ACTION_RESPONSES = {{
          predict: {{status: 202, body: {{
            schema_version: "PG_FORECAST_ACTION_V1", request_id: "forecast-action-1",
            mode: "predict", status: "queued", terminal: false, is_current: true,
            poll_after_ms: 250,
            status_url: "/v1/mobile/window-tracker/sessions/operator-test/forecast-actions/forecast-action-1",
          }}}},
          future: {{status: 202, body: {{
            schema_version: "PG_FORECAST_ACTION_V1", request_id: "forecast-action-1",
            mode: "future", status: "queued", terminal: false, is_current: true,
            poll_after_ms: 250,
            status_url: "/v1/mobile/window-tracker/sessions/operator-test/forecast-actions/forecast-action-1",
          }}}},
        }};
        window.__FORECAST_ACTION_STATUS = {{status: 200, body: {{
          schema_version: "PG_FORECAST_ACTION_V1", request_id: "forecast-action-1",
          mode: "predict", status: "ready", terminal: true, is_current: true,
          poll_after_ms: 250,
          status_url: "/v1/mobile/window-tracker/sessions/operator-test/forecast-actions/forecast-action-1",
        }}}};
        window.__FORECAST_ACTION_STATUS_QUEUE = [];
        {event_source_bootstrap}
        Object.defineProperty(window, "Worker", {{value: undefined, configurable: true}});
        const nativeSetTimeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => {{
          if (Number(delay || 0) >= 2500) return 0;
          return nativeSetTimeout(callback, delay, ...args);
        }};
        window.fetch = (input, options = {{}}) => {{
          const href = typeof input === "string" ? input : String((input && input.url) || input || "");
          const method = String(options.method || "GET").toUpperCase();
          window.__FETCH_URLS.push(href);
          window.__FETCH_REQUESTS.push({{href, method}});
          const actionKey = href.endsWith("/show-future") ? "future" : href.endsWith("/predict") ? "predict" : "";
          if (actionKey) {{
            const action = window.__FORECAST_ACTION_RESPONSES[actionKey];
            return new Promise(resolve => nativeSetTimeout(() => resolve(new Response(JSON.stringify(action.body), {{
              status: Number(action.status || 200),
              headers: {{"Content-Type": "application/json"}},
            }})), Number(window.__FORECAST_ACTION_DELAY_MS || 0)));
          }}
          if (href.includes("/forecast-actions/")) {{
            const action = window.__FORECAST_ACTION_STATUS_QUEUE.length
              ? window.__FORECAST_ACTION_STATUS_QUEUE.shift()
              : window.__FORECAST_ACTION_STATUS;
            return Promise.resolve(new Response(JSON.stringify(action.body), {{
              status: Number(action.status || 200),
              headers: {{"Content-Type": "application/json"}},
            }}));
          }}
          const isOperatorState = href.includes("/v1/mobile/operator/state/v1/");
          const body = isOperatorState
            ? window.__OPERATOR_PAYLOAD
            : {{detail: "not found"}};
          const respond = () => new Response(JSON.stringify(body), {{
            status: isOperatorState ? 200 : 404,
            headers: {{"Content-Type": "application/json"}},
          }});
          const delay = isOperatorState
            ? Number(window.__OPERATOR_FETCH_DELAY_MS || 0)
            : 0;
          return delay > 0
            ? new Promise(resolve => nativeSetTimeout(() => resolve(respond()), delay))
            : Promise.resolve(respond());
        }};
        """
    )
    page.goto("http://dashboard.test/", wait_until="domcontentloaded")
    page.wait_for_function(
        "expected => window.PhoenixGuardDashboard?.getState().revision === expected",
        arg=payload["revision"],
        timeout=10_000,
    )
    page.wait_for_function(
        "() => document.querySelector('#surface-canvas')?.classList.contains('ready')",
        timeout=10_000,
    )
    try:
        yield page
    finally:
        context.close()


def test_live_session_stream_coalesces_updates_into_atomic_operator_refreshes(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    updated = copy.deepcopy(initial)
    updated["revision"] = 43
    updated["freshness"]["observed_at"] += 1
    updated["current_move"]["summary"] = (
        "Live stream delivered the newest decision state."
    )

    with _dashboard_page(
        chromium_browser, initial, with_event_source=True
    ) as page:
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().streamConnected === true"
        )
        assert page.evaluate("window.__EVENT_SOURCES.length") == 1
        assert page.evaluate("window.__EVENT_SOURCES[0].url") == (
            "/v1/mobile/window-tracker/sessions/operator-test/events"
        )
        request_count = page.evaluate("window.__FETCH_REQUESTS.length")

        page.evaluate(
            """
            payload => {
              window.__OPERATOR_PAYLOAD = payload;
              const stream = window.__EVENT_SOURCES[0];
              stream.emit("SESSION_UPDATE", {capture_count: 43});
              stream.emit("SESSION_UPDATE", {capture_count: 43});
              stream.emit("SESSION_UPDATE", {capture_count: 43});
            }
            """,
            updated,
        )
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().revision === 43"
        )

        requests = page.evaluate(
            "start => window.__FETCH_REQUESTS.slice(start)", request_count
        )
        assert requests == [
            {
                "href": "/v1/mobile/operator/state/v1/operator-test?view=all",
                "method": "GET",
            }
        ]
        assert page.locator("#beginner-reason").inner_text() == (
            "Live stream delivered the newest decision state."
        )


def test_every_dashboard_control_is_wired_and_safe_under_real_clicks(
    chromium_browser: Browser,
) -> None:
    page_errors: list[str] = []
    console_errors: list[str] = []
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )

        static_button_ids = {
            "help-open",
            "experience-mode-toggle",
            "beginner-open-advanced",
            "frame-window",
            "frame-chart",
            "mode-overlay",
            "mode-raw",
            "layers-all",
            "layers-clear",
            "run-forecast",
            "show-future-path",
            "zoom-out",
            "zoom-fit",
            "zoom-actual",
            "zoom-in",
            "refresh-view",
            "mobile-inspector-close",
            "help-close",
        }
        assert set(
            page.locator("button[id]").evaluate_all(
                "nodes => nodes.map(node => node.id)"
            )
        ) == static_button_ids
        assert page.locator("button[data-overlay-view]").count() == 8
        assert page.locator("button[data-overlay-family]").count() == 16
        assert page.locator("button[data-label-mode]").count() == 3
        assert page.locator("input[type=range]").count() == 2

        page.locator("#help-open").click()
        assert page.locator("#help-dialog").get_attribute("open") is not None
        page.locator("#help-close").click()
        assert page.locator("#help-dialog").get_attribute("open") is None

        # This secondary link is intentionally hidden at desktop widths; its
        # handler still has to remain wired for layouts that expose it.
        page.locator("#beginner-open-advanced").evaluate("node => node.click()")
        assert "advanced-view" in (
            page.locator("body").get_attribute("class") or ""
        )
        page.locator("#experience-mode-toggle").click()
        assert "simple-view" in (page.locator("body").get_attribute("class") or "")
        page.locator("#experience-mode-toggle").click()
        assert "advanced-view" in (
            page.locator("body").get_attribute("class") or ""
        )

        for selector in ("#frame-chart", "#frame-window"):
            page.locator(selector).click()
            assert page.locator(selector).get_attribute("aria-pressed") == "true"
        for selector in ("#mode-raw", "#mode-overlay"):
            page.locator(selector).click()
            assert page.locator(selector).get_attribute("aria-pressed") == "true"

        operator_requests = page.evaluate(
            "() => window.__FETCH_REQUESTS.filter(row => row.href.includes('/operator/state/')).length"
        )
        for view in (
            "all",
            "live",
            "smc",
            "structure",
            "zones",
            "plan",
            "forecast",
            "history",
        ):
            control = page.locator(f'button[data-overlay-view="{view}"]')
            control.click()
            assert control.get_attribute("aria-pressed") == "true"
        assert page.evaluate(
            "() => window.__FETCH_REQUESTS.filter(row => row.href.includes('/operator/state/')).length"
        ) == operator_requests

        page.locator("#layers-clear").click()
        assert (
            page.locator("button[data-overlay-family][aria-pressed=true]").count()
            == 0
        )
        page.locator("#layers-all").click()
        assert (
            page.locator("button[data-overlay-family][aria-pressed=true]").count()
            == 16
        )
        families = page.locator("button[data-overlay-family]").evaluate_all(
            "nodes => nodes.map(node => node.dataset.overlayFamily)"
        )
        for family in families:
            control = page.locator(f'button[data-overlay-family="{family}"]')
            control.click()
            assert control.get_attribute("aria-pressed") == "false"
            control.click()
            assert control.get_attribute("aria-pressed") == "true"

        for selector in ("#run-forecast", "#show-future-path"):
            page.locator(selector).click()
            page.wait_for_function(
                "() => window.PhoenixGuardDashboard.getState().forecastActionBusy === false"
            )
            assert (
                page.locator("#forecast-action-status").get_attribute("data-state")
                == "success"
            )

        for mode in ("hover", "off", "on"):
            control = page.locator(f'button[data-label-mode="{mode}"]')
            control.click()
            assert control.get_attribute("aria-pressed") == "true"
        page.locator("#overlay-opacity").fill("73")
        assert page.locator("#overlay-opacity").input_value() == "73"

        for selector in ("#zoom-actual", "#zoom-out", "#zoom-in", "#zoom-fit"):
            page.locator(selector).click()
        assert page.locator("#zoom-fit").get_attribute("aria-pressed") == "true"

        request_count = page.evaluate("window.__FETCH_REQUESTS.length")
        page.locator("#refresh-view").click()
        page.wait_for_function(
            "count => window.__FETCH_REQUESTS.length === count + 1",
            arg=request_count,
        )

        page.locator("#history-scrubber").fill("0")
        page.locator('button[data-overlay-view="live"]').click()
        page.locator("#experience-mode-toggle").click()
        page.get_by_role("button", name="Council read").click()
        assert page.locator("#mobile-inspector").is_visible()
        page.locator("#mobile-inspector-close").click()
        assert page.locator("#mobile-inspector").is_hidden()

        assert page_errors == []
        assert console_errors == []


def test_dashboard_label_collision_keeps_the_higher_priority_label(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        result = page.evaluate(
            """
            () => {
              const root = document.querySelector('#hotspot-layer');
              root.innerHTML = '';
              function add(priority, label) {
                const button = document.createElement('button');
                button.className = 'surface-hotspot';
                button.dataset.priority = String(priority);
                const span = document.createElement('span');
                span.textContent = label;
                span.getBoundingClientRect = () => ({left: 10, top: 10, right: 90, bottom: 30, width: 80, height: 20});
                button.appendChild(span);
                root.appendChild(button);
                return button;
              }
              const low = add(10, 'LOW');
              const high = add(200, 'HIGH');
              window.resolveLabelCollisions(root);
              return {
                lowHidden: low.classList.contains('label-collision-hidden'),
                highHidden: high.classList.contains('label-collision-hidden'),
              };
            }
            """
        )

    assert result == {"lowHidden": True, "highHidden": False}


@pytest.mark.parametrize("viewport", [(1440, 1000), (390, 844)])
def test_808fx_branding_stays_premium_readable_and_inside_the_header(
    chromium_browser: Browser,
    viewport: tuple[int, int],
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(), viewport=viewport
    ) as page:
        assert page.title() == "808Fx Standard Hybrid System Live Tracker"
        title = page.locator(".brand-title")
        assert title.is_visible()
        assert title.inner_text() == "808Fx Standard Hybrid System"
        assert (
            page.locator(".brand-subtitle").text_content()
            == "Powered by the Phoenix Guard Engine V3"
        )

        branding = page.evaluate(
            """
            () => {
              const title = document.querySelector('.brand-title');
              const mark = document.querySelector('.brand-mark');
              const header = document.querySelector('.app-header');
              const titleStyle = getComputedStyle(title);
              const markStyle = getComputedStyle(mark);
              const titleRect = title.getBoundingClientRect();
              const headerRect = header.getBoundingClientRect();
              return {
                titleBackground: titleStyle.backgroundImage,
                titleClip: titleStyle.webkitBackgroundClip || titleStyle.backgroundClip,
                markColor: markStyle.color,
                titleFullyRendered:
                  title.scrollWidth <= title.clientWidth + 1 &&
                  title.scrollHeight <= parseFloat(titleStyle.lineHeight) * 2.25,
                titleLeft: titleRect.left,
                titleRight: titleRect.right,
                headerLeft: headerRect.left,
                headerRight: headerRect.right,
              };
            }
            """
        )
        assert "linear-gradient" in branding["titleBackground"]
        assert branding["titleClip"] == "text"
        assert branding["markColor"] == "rgb(242, 200, 102)"
        assert branding["titleFullyRendered"], (viewport, branding)
        assert branding["titleLeft"] >= branding["headerLeft"]
        assert branding["titleRight"] <= branding["headerRight"]


def test_simple_is_default_and_explore_does_not_restore_workspace_navigation(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        assert "simple-view" in (page.locator("body").get_attribute("class") or "")
        assert page.locator(".workspace-nav").count() == 0
        assert page.locator("#beginner-decision-shell").is_visible()
        assert page.locator("#overlay-explorer").is_visible()
        assert page.locator("#experience-mode-toggle").inner_text() == "Explore"

        page.locator("#experience-mode-toggle").click()
        page.wait_for_function(
            "() => document.body.classList.contains('advanced-view')"
        )
        assert page.locator("#experience-mode-toggle").inner_text() == "Simple view"
        assert (
            page.locator("#experience-mode-toggle").get_attribute("aria-pressed")
            == "true"
        )
        assert page.locator("#overlay-explorer").is_visible()
        assert page.locator("#overlay-inspector").is_visible()
        assert page.locator(".workspace-nav").count() == 0

        fetch_urls = page.evaluate("window.__FETCH_URLS.slice()")
        assert fetch_urls
        assert all("/v1/mobile/operator/state/v1/" in url for url in fetch_urls)


def test_overlay_explorer_updates_aria_state_locally_from_the_atomic_all_pool(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        for view in ("structure", "history"):
            button = page.locator(f'[data-overlay-view="{view}"]')
            assert button.is_visible()
            request_count = page.evaluate("window.__FETCH_URLS.length")
            button.click()
            assert button.get_attribute("aria-pressed") == "true"
            assert (
                page.evaluate("window.PhoenixGuardDashboard.getState().overlayView")
                == view
            )
            assert page.evaluate("window.__FETCH_URLS.length") == request_count

        page.locator("#experience-mode-toggle").click()
        page.wait_for_function(
            "() => document.body.classList.contains('advanced-view')"
        )
        zones = page.locator('[data-overlay-view="zones"]')
        request_count = page.evaluate("window.__FETCH_URLS.length")
        zones.click()
        assert zones.get_attribute("aria-pressed") == "true"
        assert page.evaluate("window.__FETCH_URLS.length") == request_count
        assert page.locator("#overlay-explorer").is_visible()


def test_inflight_atomic_all_refresh_survives_local_forecast_toggle(
    chromium_browser: Browser,
) -> None:
    updated = copy.deepcopy(_operator_payload())
    updated["revision"] = 43
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        source_before = page.locator("#surface-raw").get_attribute("src")
        request_count = page.evaluate("window.__FETCH_REQUESTS.length")
        page.evaluate(
            """
            payload => {
              window.__OPERATOR_PAYLOAD = payload;
              window.__OPERATOR_FETCH_DELAY_MS = 350;
              void window.PhoenixGuardDashboard.refresh({force: true});
            }
            """,
            updated,
        )
        page.wait_for_function(
            "count => window.__FETCH_REQUESTS.length === count + 1",
            arg=request_count,
        )

        page.locator('[data-overlay-view="forecast"]').click()
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().overlayView")
            == "forecast"
        )
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().revision === 43",
            timeout=5_000,
        )

        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert composite.count() == 1
        assert composite.get_attribute("data-event-count") == "12"
        assert composite.get_attribute("data-scenario-count") == "3"
        assert page.locator("#surface-raw").get_attribute("src") == source_before
        assert not page.locator("#surface-canvas").evaluate(
            "node => node.classList.contains('updating')"
        )

        requests = page.evaluate(
            "start => window.__FETCH_REQUESTS.slice(start)", request_count
        )
        assert requests == [
            {
                "href": "/v1/mobile/operator/state/v1/operator-test?view=all",
                "method": "GET",
            }
        ]


def test_same_visual_poll_updates_permission_without_rerendering_overlays(
    chromium_browser: Browser,
) -> None:
    updated = copy.deepcopy(_operator_payload())
    refreshed_message = "Wait: permission refreshed without changing chart geometry."
    cast(dict[str, Any], updated["permission"])["message"] = refreshed_message
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        page.evaluate(
            """
            () => {
              window.__POLL_LSTM_NODE = document.querySelector(
                '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
              );
            }
            """
        )
        stats_before = page.evaluate(
            "window.PhoenixGuardDashboard.getState().overlayRenderStats"
        )
        source_before = page.locator("#surface-raw").get_attribute("src")
        request_count = page.evaluate("window.__FETCH_REQUESTS.length")

        page.evaluate(
            """
            payload => {
              window.__OPERATOR_PAYLOAD = payload;
              return window.PhoenixGuardDashboard.refresh({force: true});
            }
            """,
            updated,
        )

        assert page.locator("#beginner-instruction").inner_text() == refreshed_message
        result = page.evaluate(
            """
            () => ({
              stats: window.PhoenixGuardDashboard.getState().overlayRenderStats,
              sameNode: window.__POLL_LSTM_NODE === document.querySelector(
                '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
              ),
            })
            """
        )
        assert result == {"stats": stats_before, "sameNode": True}
        assert page.locator("#surface-raw").get_attribute("src") == source_before
        assert not page.locator("#surface-canvas").evaluate(
            "node => node.classList.contains('updating')"
        )
        requests = page.evaluate(
            "start => window.__FETCH_REQUESTS.slice(start)", request_count
        )
        assert requests == [
            {
                "href": "/v1/mobile/operator/state/v1/operator-test?view=all",
                "method": "GET",
            }
        ]


def _assert_clean_multimodal_forecast(
    page: Page,
    source_line: list[list[float]],
    *,
    status: str,
) -> None:
    composite = page.locator(
        '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
    )
    assert composite.count() == 1
    assert composite.get_attribute("data-display-mode") == "multi-scenario-paths"
    assert composite.get_attribute("data-scenario-count") == "3"
    assert composite.get_attribute("data-forecast-status") == status
    assert composite.get_attribute("data-event-count") == "12"
    assert composite.get_attribute("data-probability-calibration") == "UNCALIBRATED"

    alternatives = composite.locator(":scope > .surface-forecast-scenario")
    assert alternatives.count() == 2
    assert alternatives.count() <= 2
    alternative_opacities = cast(list[float], alternatives.evaluate_all(
        "nodes => nodes.map(node => Number(node.style.opacity))"
    ))
    assert len(alternative_opacities) == 2
    assert all(
        math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6)
        for actual, expected in zip(
            alternative_opacities,
            (0.6152, 0.576),
            strict=True,
        )
    )

    steps = composite.locator(":scope > .surface-forecast-step-node")
    assert steps.count() == 12
    assert steps.evaluate_all(
        "nodes => nodes.map(node => node.dataset.eventLabel)"
    ) == [f"E{event_index}" for event_index in range(1, 13)]
    step_labels = composite.locator(":scope > .surface-forecast-step-label")
    assert step_labels.count() == 4
    assert step_labels.evaluate_all(
        "nodes => nodes.map(node => node.textContent)"
    ) == ["E1", "E4", "E8", "E12"]
    assert composite.locator(
        ":scope > .surface-forecast-step-label.milestone"
    ).count() == 4

    milestones = composite.locator(
        ":scope > .surface-forecast-step-node.surface-forecast-milestone-node"
    )
    assert milestones.count() == 4
    assert milestones.locator("title").evaluate_all(
        "nodes => nodes.map(node => node.textContent)"
    ) == [
        "E1 expected close · true chart scale",
        "E4 expected close · true chart scale",
        "E8 expected close · true chart scale",
        "E12 expected close · true chart scale",
    ]
    assert composite.locator(".surface-forecast-milestone-node.endpoint").count() == 1
    assert composite.locator(".surface-forecast-endpoint-label").text_content() == (
        "UP COMMITTED · E1–E12 · NO EDGE"
    )

    assert composite.locator(":scope > .surface-forecast-candle").count() == 12
    assert composite.locator(".surface-forecast-candle-body").count() == 12
    assert composite.locator(".surface-forecast-candle-wick").count() == 12

    for selector in (
        ".surface-forecast-event",
        ".surface-forecast-event-body",
        ".surface-forecast-close-node",
        ".surface-forecast-origin-rail",
        ".surface-forecast-origin",
    ):
        assert composite.locator(selector).count() == 0

    path = page.locator('polyline[data-overlay-id="lstm-current"]')
    assert path.count() == 1
    rendered_points = [
        tuple(float(value) for value in pair.split(","))
        for pair in (path.get_attribute("points") or "").split()
    ]
    expected_points = [
        ((0.10 + x_norm * 0.80) * 1200, (0.12 + y_norm * 0.80) * 700)
        for x_norm, y_norm in source_line
    ]
    assert len(rendered_points) == len(expected_points) == 13
    for rendered, expected in zip(rendered_points, expected_points, strict=True):
        assert all(
            math.isclose(actual, wanted, rel_tol=1e-6, abs_tol=1e-6)
            for actual, wanted in zip(rendered, expected, strict=True)
        )


def test_no_edge_composite_forecast_renders_clean_multimodal_paths(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    source_line = next(
        row["line_points"] for row in payload["overlays"] if row.get("family") == "lstm"
    )
    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        _assert_clean_multimodal_forecast(page, source_line, status="NO_EDGE")

        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert "forecast-no-edge" in (composite.get_attribute("class") or "").split()
        assert composite.locator(".surface-forecast-band").count() == 0

        hotspot = page.locator('.surface-hotspot[data-overlay-id="lstm-current"]')
        path = page.locator('polyline[data-overlay-id="lstm-current"]')
        assert hotspot.count() == 1
        assert hotspot.evaluate(
            "node => node.classList.contains('label-policy-hidden')"
        )
        assert hotspot.locator("span").evaluate(
            "node => getComputedStyle(node).opacity"
        ) == "0"
        for class_names in (
            (hotspot.get_attribute("class") or "").split(),
            (path.get_attribute("class") or "").split(),
        ):
            assert "forecast-no-edge" in class_names
            assert "buy" not in class_names
            assert "sell" not in class_names

        hotspot.click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        assert "The complete 12-step up / buy-side forecast is shown" in inspector_copy
        assert "Trade status remains NO EDGE." in inspector_copy
        assert "no reliable" not in inspector_copy.lower()


def test_low_confidence_composite_stays_visible_and_never_looks_authorized(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    for row in payload["overlays"]:
        if row.get("family") != "lstm":
            continue
        row["forecast_status"] = "LOW_CONFIDENCE"
        row["forecast_quality_status"] = "LOW_CONFIDENCE"
        row["trade_authorization_status"] = "NO_EDGE"
        row["forecast_authorized"] = False

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        path = page.locator('polyline[data-overlay-id="lstm-current"]')

        source_line = next(
            row["line_points"]
            for row in payload["overlays"]
            if row.get("family") == "lstm"
        )
        _assert_clean_multimodal_forecast(page, source_line, status="LOW_CONFIDENCE")
        assert (
            "forecast-low-confidence"
            in (composite.get_attribute("class") or "").split()
        )
        assert path.get_attribute("data-forecast-status") == "LOW_CONFIDENCE"
        assert "forecast-authorized" not in (path.get_attribute("class") or "").split()


def test_authorized_composite_is_the_only_state_that_renders_predicted_ranges(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="BUY_NOW")
    for row in payload["overlays"]:
        if row.get("family") != "lstm":
            continue
        row["side"] = "BUY"
        row["forecast_status"] = "AUTHORIZED"
        row["forecast_quality_status"] = "CALIBRATED"
        row["trade_authorization_status"] = "AUTHORIZED"
        row["forecast_authorized"] = True
        row["interval"]["calibrated"] = True

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )

        source_line = next(
            row["line_points"]
            for row in payload["overlays"]
            if row.get("family") == "lstm"
        )
        _assert_clean_multimodal_forecast(page, source_line, status="AUTHORIZED")
        assert composite.locator(".surface-forecast-band").count() == 1


def test_authorized_but_uncalibrated_forecast_hides_the_range_band(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="BUY_NOW")
    for row in payload["overlays"]:
        if row.get("family") != "lstm":
            continue
        row["side"] = "BUY"
        row["forecast_status"] = "AUTHORIZED"
        row["forecast_authorized"] = True
        row["interval"]["calibrated"] = False

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )

        source_line = next(
            row["line_points"]
            for row in payload["overlays"]
            if row.get("family") == "lstm"
        )
        _assert_clean_multimodal_forecast(page, source_line, status="AUTHORIZED")
        assert composite.locator(".surface-forecast-band").count() == 0


@pytest.mark.parametrize("neutral_side", ["HOLD", "NEUTRAL"])
def test_neutral_primary_suppresses_compact_path_and_keeps_directional_alternatives(
    chromium_browser: Browser,
    neutral_side: str,
) -> None:
    payload = _operator_payload()
    for row in payload["overlays"]:
        if row.get("family") != "lstm":
            continue
        scenarios = row["forecast_scenarios"]
        neutral_scenario = next(
            scenario for scenario in scenarios if scenario.get("side") == "HOLD"
        )
        buy_scenario = next(
            scenario for scenario in scenarios if scenario.get("side") == "BUY"
        )
        sell_scenario = next(
            scenario for scenario in scenarios if scenario.get("side") == "SELL"
        )
        for scenario in scenarios:
            scenario["selected"] = scenario is neutral_scenario
        neutral_scenario["probability"] = 0.5803
        buy_scenario["probability"] = 0.4100
        # Live V3 can assign a very small uncalibrated mode probability. RANGE
        # still promises both complete directional branches, so SELL must stay.
        sell_scenario["probability"] = 0.0097
        neutral_scenario["side"] = neutral_side
        row["side"] = neutral_side
        row["forecast_direction"] = neutral_side
        row["trajectory_mode"] = neutral_side
        row["line_points"] = copy.deepcopy(neutral_scenario["line_points"])
        # A calibrated interval must not resurrect geometry around a neutral path.
        row["interval"]["calibrated"] = True

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )

        assert composite.count() == 1
        assert composite.get_attribute("data-display-mode") == "range-scenario-paths"
        assert composite.get_attribute("data-primary-side") == "HOLD"
        assert composite.get_attribute("data-scenario-count") == "3"
        assert composite.get_attribute("data-event-count") == "12"
        assert (
            composite.get_attribute("data-probability-calibration")
            == "UNCALIBRATED"
        )
        assert composite.get_attribute("aria-label") == (
            "Range leads; complete 12-step buy and sell paths shown; "
            "no edge and not entry permission"
        )

        alternatives = composite.locator(
            ":scope > .surface-forecast-scenario.range-branch"
        )
        assert alternatives.count() == 2
        assert alternatives.evaluate_all(
            "nodes => nodes.map(node => node.dataset.eventCount)"
        ) == ["12", "12"]
        assert alternatives.evaluate_all(
            "nodes => nodes.map(node => "
            "node.classList.contains('buy') ? 'BUY' : "
            "node.classList.contains('sell') ? 'SELL' : 'HOLD')"
        ) == ["BUY", "SELL"]
        assert alternatives.evaluate_all(
            "nodes => nodes.map(node => node.getAttribute('points').trim().split(/\\s+/).length)"
        ) == [13, 13]
        assert alternatives.locator("title").evaluate_all(
            "nodes => nodes.map(node => node.textContent)"
        ) == [
            "Bullish route · 12 events · relative route weight uncalibrated · "
            "spread from the selected path shows route uncertainty",
            "Bearish route · 12 events · relative route weight uncalibrated · "
            "spread from the selected path shows route uncertainty",
        ]
        assert composite.locator(".surface-forecast-direction-label").text_content() == (
            "RANGE LEADS · 12-STEP BUY/SELL PATHS · NO EDGE"
        )

        assert composite.locator(".surface-forecast-band").count() == 0
        branch_nodes = composite.locator(":scope > .surface-forecast-branch-node")
        assert branch_nodes.count() == 24
        assert branch_nodes.evaluate_all(
            "nodes => nodes.map(node => node.dataset.eventLabel)"
        ) == [
            *[f"E{event_index}" for event_index in range(1, 13)],
            *[f"E{event_index}" for event_index in range(1, 13)],
        ]
        assert composite.locator(
            ":scope > .surface-forecast-branch-node.surface-forecast-milestone-node"
        ).count() == 8
        assert composite.locator(
            ":scope > .surface-forecast-branch-node.endpoint"
        ).count() == 2
        branch_labels = composite.locator(":scope > .surface-forecast-step-label")
        assert branch_labels.count() == 8
        assert branch_labels.evaluate_all(
            "nodes => nodes.map(node => node.textContent)"
        ) == [
            "E1",
            "E4",
            "E8",
            "E12",
            "E1",
            "E4",
            "E8",
            "E12",
        ]
        assert composite.locator(
            ":scope > .surface-forecast-step-label.milestone"
        ).count() == 8
        assert composite.locator(".surface-forecast-empty-label").count() == 0
        assert page.locator('polyline[data-overlay-id="lstm-current"]').count() == 0
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 0
        )


@pytest.mark.parametrize("candidate_side", ["BUY", "SELL"])
def test_reacquiring_hold_renders_verified_closed_candle_candidate_path(
    chromium_browser: Browser,
    candidate_side: str,
) -> None:
    payload = _operator_payload()
    forecast = next(
        row for row in payload["overlays"] if row.get("family") == "lstm"
    )
    original_scenarios = copy.deepcopy(forecast["forecast_scenarios"])
    buy_path = copy.deepcopy(
        next(
            scenario["line_points"]
            for scenario in original_scenarios
            if scenario.get("side") == "BUY"
        )
    )
    sell_path = copy.deepcopy(
        next(
            scenario["line_points"]
            for scenario in original_scenarios
            if scenario.get("side") == "SELL"
        )
    )
    selected_path = buy_path if candidate_side == "BUY" else sell_path

    forecast.update(
        {
            "side": "NEUTRAL",
            "label": "Scene forecaster events - diagnostic",
            "forecast_status": "DIAGNOSTIC",
            "forecast_direction": candidate_side,
            "trajectory_mode": candidate_side,
            "belief_state": "REACQUIRING",
            "committed_side": "HOLD",
            "candidate_side": candidate_side,
            "line_points": copy.deepcopy(selected_path),
            "bounds": [
                min(point[0] for point in selected_path),
                min(point[1] for point in selected_path),
                max(point[0] for point in selected_path),
                max(point[1] for point in selected_path),
            ],
            "forecast_anchor": {
                "x_norm": selected_path[0][0],
                "y_norm": selected_path[0][1],
                "verified_latest_close": True,
                "source": "TRACKER_LATEST_CLOSED_CANDLE",
            },
            "forecast_scenarios": [
                {
                    "role": "base",
                    "side": candidate_side,
                    "probability": 0.36,
                    "probability_calibrated": False,
                    "selected": True,
                    "candidate": True,
                    "line_points": copy.deepcopy(selected_path),
                    "event_count": 12,
                },
                {
                    "role": "bull",
                    "side": "BUY",
                    "probability": 0.33,
                    "probability_calibrated": False,
                    "selected": False,
                    "line_points": buy_path,
                    "event_count": 12,
                },
                {
                    "role": "bear",
                    "side": "SELL",
                    "probability": 0.31,
                    "probability_calibrated": False,
                    "selected": False,
                    "line_points": sell_path,
                    "event_count": 12,
                },
            ],
        }
    )
    previous_close = float(selected_path[0][1])
    forecast["forecast_candles"] = []
    for step, (x_norm, close_y_norm) in enumerate(selected_path[1:], start=1):
        close = float(close_y_norm)
        movement_side = "BUY" if close < previous_close else "SELL"
        forecast["forecast_candles"].append(
            {
                "step": step,
                "label": f"E{step}",
                "x_norm": float(x_norm),
                "open_y_norm": previous_close,
                "high_y_norm": min(previous_close, close) - 0.004,
                "low_y_norm": max(previous_close, close) + 0.004,
                "close_y_norm": close,
                "movement_side": movement_side,
                "body_bias": movement_side,
                "direction_conflict": False,
            }
        )
        previous_close = close

    # The visible current-price candle is forming and is intentionally away
    # from the last closed candle. It cannot invalidate the backend-verified
    # closed-candle origin of this already completed scene study.
    payload["overlays"].append(
        {
            "id": "forming-current-price",
            "type": "candle",
            "side": candidate_side,
            "group": "movement",
            "family": "current_candles",
            "layer": "current_candle",
            "label": "Current price",
            "bounds": [
                selected_path[0][0] - 0.006,
                selected_path[0][1] + 0.12,
                selected_path[0][0] + 0.006,
                selected_path[0][1] + 0.18,
            ],
            "points": [
                [selected_path[0][0], selected_path[0][1] + 0.15]
            ],
            "line_points": [],
            "confidence": 0.91,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
            "candle_state": "FORMING",
        }
    )

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert composite.count() == 1
        assert composite.get_attribute("data-anchor-state") == "CANDLE_LOCKED"
        assert composite.get_attribute("data-event-count") == "12"
        assert composite.get_attribute("data-primary-side") == "HOLD"
        assert composite.get_attribute("data-candidate-side") == candidate_side
        assert composite.get_attribute("data-belief-state") == "REACQUIRING"
        assert (
            composite.get_attribute("data-scenario-presentation")
            == "SELECTED_WITH_BULL_BEAR_RANGE"
        )
        assert composite.locator(":scope > .surface-forecast-candle").count() == 12
        assert composite.locator(".surface-forecast-candle-body").count() == 12
        assert composite.locator(".surface-forecast-candle-wick").count() == 12
        assert composite.locator(".surface-forecast-step-label").evaluate_all(
            "nodes => nodes.map(node => node.textContent)"
        ) == ["E1", "E4", "E8", "E12"]

        path = page.locator('polyline[data-overlay-id="lstm-current"]')
        assert path.count() == 1
        assert "forecast-under-review" in (path.get_attribute("class") or "").split()
        assert "forecast-selected-path" in (path.get_attribute("class") or "").split()
        assert path.get_attribute("data-path-presentation") == (
            "SELECTED_12_STEP_PATH"
        )
        assert "buy" not in (path.get_attribute("class") or "").split()
        assert "sell" not in (path.get_attribute("class") or "").split()
        alternatives = composite.locator(":scope > .surface-forecast-scenario")
        assert alternatives.count() == 2
        assert alternatives.evaluate_all(
            "nodes => nodes.map(node => node.dataset.scenarioRole)"
        ) == ["BULL", "BEAR"]
        assert alternatives.evaluate_all(
            "nodes => nodes.map(node => node.dataset.uncertaintyMeaning)"
        ) == ["ROUTE_SPREAD", "ROUTE_SPREAD"]
        assert alternatives.evaluate_all(
            "nodes => nodes.map(node => node.classList.contains('bullish-range') "
            "? 'BULL' : node.classList.contains('bearish-range') ? 'BEAR' : '')"
        ) == ["BULL", "BEAR"]
        assert composite.locator(":scope > .surface-forecast-range-label").evaluate_all(
            "nodes => nodes.map(node => node.textContent)"
        ) == ["BULLISH ROUTE", "BEARISH ROUTE"]

        paint_order = composite.locator(":scope > *").evaluate_all(
            "nodes => nodes.map(node => node.getAttribute('class') || '')"
        )
        first_candle = next(
            index
            for index, class_name in enumerate(paint_order)
            if "surface-forecast-candle" in class_name
        )
        scenario_indices = [
            index
            for index, class_name in enumerate(paint_order)
            if "surface-forecast-scenario" in class_name
        ]
        assert scenario_indices and max(scenario_indices) < first_candle
        assert composite.locator(".surface-forecast-endpoint-label").text_content() == (
            f"{'UP' if candidate_side == 'BUY' else 'DOWN'} STUDY · "
            "CALIBRATION PENDING · NO EDGE"
        )


def test_forecast_legend_and_foreground_hierarchy_explain_uncertainty_locally(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator('[data-overlay-view="all"]').click()

        legend = page.locator("#forecast-path-legend")
        assert legend.is_visible()
        assert legend.get_attribute("aria-label") == "Forecast path legend"
        assert legend.locator('[role="listitem"]').all_inner_texts() == [
            "Selected 12-step path",
            "Bullish route",
            "Bearish route",
        ]
        legend_copy = legend.inner_text().lower()
        assert "alternative studied routes, not odds" in legend_copy
        assert "wider route separation means less agreement" in legend_copy

        svg_order = page.locator("#surface-line-svg > *").evaluate_all(
            "nodes => nodes.map(node => ({"
            "family: node.dataset.overlayFamilyId || '', "
            "role: node.dataset.forecastRole || '', "
            "presentation: node.dataset.pathPresentation || ''"
            "}))"
        )
        scene_indices = [
            index for index, row in enumerate(svg_order) if row["family"] == "lstm"
        ]
        other_indices = [
            index for index, row in enumerate(svg_order) if row["family"] != "lstm"
        ]
        assert len(scene_indices) == 2
        assert other_indices and min(scene_indices) > max(other_indices)
        assert svg_order[-1] == {
            "family": "lstm",
            "role": "composite",
            "presentation": "SELECTED_12_STEP_PATH",
        }
        assert page.evaluate("window.__FETCH_URLS.length") == request_count


def test_composite_without_scenarios_suppresses_the_flat_line_and_hotspot(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    for row in payload["overlays"]:
        if row.get("family") != "lstm":
            continue
        row.pop("forecast_scenarios")
        row["line_points"] = [[x, 0.50] for x, _ in row["line_points"]]

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )

        assert composite.count() == 0
        assert page.locator('polyline[data-overlay-id="lstm-current"]').count() == 0
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 0
        )


def test_forecast_studies_copy_promises_complete_events_without_calibrated_odds(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        helper_copy = page.locator("#forecast-action-status").text_content()
        assert helper_copy == (
            "Every path shows 12 candle events · solid = committed scene forecast · "
            "dashed = outcome under review · NO EDGE is never entry permission."
        )


def test_run_forecast_posts_action_keeps_forecast_view_and_refreshes_atomically(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    for row in payload["overlays"]:
        if row.get("family") == "lstm":
            row["forecast_role"] = "band_90"
    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#mode-raw").click()
        source_before = page.locator("#surface-raw").get_attribute("src")
        page.evaluate("() => { window.__FORECAST_ACTION_DELAY_MS = 800; }")

        page.locator("#run-forecast").click()
        assert page.locator("#forecast-actions").get_attribute("aria-busy") == "true"
        assert page.locator("#run-forecast").is_disabled()
        assert page.locator("#show-future-path").is_disabled()
        assert page.locator("#run-forecast").inner_text() == "Forecasting…"
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().overlayView")
            == "forecast"
        )
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().surfaceMode")
            == "overlay"
        )

        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )
        status = page.locator("#forecast-action-status").inner_text().lower()
        assert "forecast ready" in status
        assert "not entry permission" in status
        assert not page.locator("#run-forecast").is_disabled()
        assert not page.locator("#show-future-path").is_disabled()
        assert page.locator("#surface-raw").get_attribute("src") == source_before
        assert not page.locator("#surface-canvas").evaluate(
            "node => node.classList.contains('updating')"
        )
        assert page.locator(".family-lstm").count() >= 1
        requests = page.evaluate("window.__FETCH_REQUESTS.slice()")
        post_requests = [row for row in requests if row["method"] == "POST"]
        assert post_requests == [
            {
                "href": "/v1/mobile/window-tracker/sessions/operator-test/predict",
                "method": "POST",
            }
        ]
        assert requests[-1]["method"] == "GET"
        assert "/v1/mobile/operator/state/v1/" in requests[-1]["href"]


def test_run_forecast_uses_current_composite_without_recomputing(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        assert (
            page.locator(
                '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
            ).count()
            == 1
        )
        page.locator("#run-forecast").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert "current forecast" in status
        assert "not entry permission" in status
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().overlayView")
            == "forecast"
        )
        assert (
            page.locator(
                '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
            ).count()
            == 1
        )
        assert not page.locator("#run-forecast").is_disabled()
        assert not any(
            row["method"] == "POST" and row["href"].endswith("/predict")
            for row in page.evaluate("window.__FETCH_REQUESTS.slice()")
        )


def test_show_future_uses_current_composite_without_recomputing(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        assert (
            page.locator(
                '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
            ).count()
            == 1
        )
        page.locator("#show-future-path").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert "current future path" in status
        assert "not entry permission" in status
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().overlayView")
            == "forecast"
        )
        assert (
            page.locator(
                '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
            ).count()
            == 1
        )
        assert not page.locator("#show-future-path").is_disabled()
        assert not any(
            row["method"] == "POST" and row["href"].endswith("/show-future")
            for row in page.evaluate("window.__FETCH_REQUESTS.slice()")
        )


def test_show_future_does_not_treat_uncertainty_band_as_center_path(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    for row in payload["overlays"]:
        if row.get("family") != "lstm":
            continue
        row["forecast_role"] = "band_90"
        row["line_points"] = [[0.72, 0.38], [0.94, 0.55]]

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#show-future-path").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )

        assert any(
            row["method"] == "POST" and row["href"].endswith("/show-future")
            for row in page.evaluate("window.__FETCH_REQUESTS.slice()")
        )


def test_show_future_keeps_completed_snapshot_when_live_chart_advances(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["overlays"] = [
        row
        for row in payload["overlays"]
        if row.get("family") not in {"lstm", "prediction"}
    ]
    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate(
            """
            () => {
              window.__FORECAST_ACTION_STATUS_QUEUE = [
                {
                  status: 200,
                  body: {
                    schema_version: 'PG_FORECAST_ACTION_V1',
                    request_id: 'forecast-action-1',
                    mode: 'future',
                    status: 'ready',
                    terminal: true,
                    is_current: false,
                    snapshot_ready: true,
                    snapshot_status: 'READY',
                    source_frame_index: 41,
                    current_frame_index: 42,
                    source_frame_age: 1,
                    trade_authorized: false,
                    poll_after_ms: 250,
                    status_url: '/v1/mobile/window-tracker/sessions/operator-test/forecast-actions/forecast-action-1',
                  },
                },
              ];
            }
            """
        )
        page.locator("#show-future-path").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert "future path ready from its captured frame" in status
        assert "1 frame ago" in status
        assert "never grants entry permission" in status
        future_posts = [
            row
            for row in page.evaluate("window.__FETCH_REQUESTS.slice()")
            if row["method"] == "POST" and row["href"].endswith("/show-future")
        ]
        assert len(future_posts) == 1
        assert all(
            row["method"] == "POST" and row["href"].endswith("/show-future")
            for row in future_posts
        )


def test_forecast_action_maps_backend_failure_to_safe_beginner_copy(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["overlays"] = [
        row
        for row in payload["overlays"]
        if row.get("family") not in {"lstm", "prediction"}
    ]
    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate(
            """
            () => {
              window.__FORECAST_ACTION_RESPONSES.predict = {
                status: 400,
                body: {detail: 'Lock broker focus before running memory projection.'},
              };
            }
            """
        )
        page.locator("#run-forecast").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'error'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert status == "lock the broker chart first, then run the forecast again."
        for forbidden in ("backend", "schema", "telemetry", "memory projection"):
            assert forbidden not in status
        assert not page.locator("#run-forecast").is_disabled()
        assert page.locator("#forecast-actions").get_attribute("aria-busy") == "false"
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().forecastActionBusy")
            is False
        )


def test_live_read_preserves_geometry_while_backend_label_policy_declutters_text(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().overlayView")
            == "live"
        )
        smc_mark = page.locator('.surface-hotspot[data-overlay-id="smc-order-block"]')
        assert smc_mark.count() == 1
        assert smc_mark.evaluate(
            "node => node.classList.contains('label-policy-hidden')"
        )
        assert (
            smc_mark.locator("span").evaluate("node => getComputedStyle(node).opacity")
            == "0"
        )

        page.locator('[data-overlay-view="smc"]').click()
        assert smc_mark.count() == 1
        assert smc_mark.evaluate(
            "node => node.classList.contains('label-policy-hidden')"
        )


def test_independent_smc_lstm_and_two_candle_toggles_do_not_replace_the_pool(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator('[data-overlay-view="all"]').click()
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().overlayView === 'all'"
        )
        expected = {
            "smc-order-block",
            "council-current",
            "two-candle-current",
            "lstm-current",
        }
        visible = set(
            page.locator("[data-overlay-id]").evaluate_all(
                "nodes => nodes.map(node => node.dataset.overlayId)"
            )
        )
        assert expected.issubset(visible)

        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator('[data-overlay-family="smc"]').click()
        assert (
            page.locator('.surface-hotspot[data-overlay-id="smc-order-block"]').count()
            == 0
        )
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 1
        )
        assert (
            page.locator(
                '.surface-hotspot[data-overlay-id="two-candle-current"]'
            ).count()
            == 1
        )
        assert page.evaluate("window.__FETCH_URLS.length") == request_count

        page.locator('[data-overlay-family="lstm"]').click()
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 0
        )
        assert (
            page.locator(
                '.surface-hotspot[data-overlay-id="two-candle-current"]'
            ).count()
            == 1
        )
        assert (
            page.locator('.surface-hotspot[data-overlay-id="council-current"]').count()
            == 1
        )
        assert page.evaluate("window.__FETCH_URLS.length") == request_count

        page.locator('[data-overlay-family="smc"]').click()
        page.locator('[data-overlay-family="lstm"]').click()
        assert (
            page.locator('.surface-hotspot[data-overlay-id="smc-order-block"]').count()
            == 1
        )
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 1
        )


def test_show_all_and_clear_switch_every_public_family_atomically(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-clear").click()
        assert page.locator("[data-overlay-id]").count() == 0
        assert page.locator("#surface-line-svg polyline").count() == 0
        assert all(
            value == "false"
            for value in page.locator("[data-overlay-family]").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('aria-pressed'))"
            )
        )

        page.locator("#layers-all").click()
        assert all(
            value == "true"
            for value in page.locator("[data-overlay-family]").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('aria-pressed'))"
            )
        )
        ids = set(
            page.locator("[data-overlay-id]").evaluate_all(
                "nodes => nodes.map(node => node.dataset.overlayId)"
            )
        )
        assert {
            "demand-current",
            "support-current",
            "past-sell",
            "smc-order-block",
            "two-candle-current",
            "lstm-current",
        }.issubset(ids)
        assert "broker" not in " ".join(ids).lower()
        assert "diagnostic" not in " ".join(ids).lower()


def test_custom_overlay_mix_survives_reload_without_network_refetch_per_toggle(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator('[data-overlay-family="smc"]').click()
        page.locator('[data-overlay-family="lstm"]').click()
        assert page.evaluate("window.__FETCH_URLS.length") == request_count
        expected_families = page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        )

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "expected => window.PhoenixGuardDashboard?.getState().revision === expected",
            arg=42,
        )
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().overlayView")
            == "custom"
        )
        assert (
            page.evaluate("window.PhoenixGuardDashboard.getState().activeFamilies")
            == expected_families
        )
        assert (
            page.locator('[data-overlay-family="smc"]').get_attribute("aria-pressed")
            == "false"
        )
        assert (
            page.locator('[data-overlay-family="lstm"]').get_attribute("aria-pressed")
            == "false"
        )
        assert (
            page.locator('[data-overlay-family="two_candle"]').get_attribute(
                "aria-pressed"
            )
            == "true"
        )


def test_all_overlay_toggles_are_local_and_reuse_detached_semantic_nodes(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        page.evaluate(
            """
            () => {
              window.__PAST_SELL_NODE = document.querySelector(
                '.surface-hotspot[data-overlay-id="past-sell"]'
              );
            }
            """
        )
        request_count = page.evaluate("window.__FETCH_URLS.length")

        page.locator('[data-overlay-family="history"]').click()
        assert page.locator('[data-overlay-id="past-sell"]').count() == 0
        page.locator('[data-overlay-family="history"]').click()
        page.locator('[data-overlay-view="forecast"]').click()
        page.locator('[data-overlay-view="all"]').click()
        page.locator("#mode-raw").click()
        page.locator("#mode-overlay").click()

        result = page.evaluate(
            """
            () => ({
              requests: window.__FETCH_URLS.length,
              sameHistoryNode:
                window.__PAST_SELL_NODE === document.querySelector(
                  '.surface-hotspot[data-overlay-id="past-sell"]'
                ),
            })
            """
        )
        assert result == {
            "requests": request_count,
            "sameHistoryNode": True,
        }


@pytest.mark.parametrize("viewport", [(390, 844), (360, 800)])
def test_mobile_overlay_library_has_tappable_controls_without_page_overflow(
    chromium_browser: Browser,
    viewport: tuple[int, int],
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(), viewport=viewport
    ) as page:
        for selector in (
            "#layers-all",
            "#layers-clear",
            '[data-overlay-family="smc"]',
            '[data-overlay-family="lstm"]',
            "#run-forecast",
            "#show-future-path",
        ):
            control = page.locator(selector)
            control.scroll_into_view_if_needed()
            assert control.is_visible()
            box = control.bounding_box()
            assert box is not None and box["height"] >= 40, (viewport, selector, box)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )


def test_ended_sell_pressure_and_current_up_move_render_wait_not_current_sell(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(action="WAIT")) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "WAIT"
        assert page.locator("#current-move-title").inner_text() == "Moving up"
        assert "moving up" in page.locator("#beginner-now-read").inner_text().lower()
        pressure = page.locator("#pressure-event")
        assert pressure.get_attribute("data-state") == "ended"
        pressure_text = pressure.inner_text().lower()
        assert "ended" in pressure_text
        assert "current sell pressure" not in pressure_text
        assert "sell" not in page.locator("#current-move-title").inner_text().lower()
        assert "sell" not in page.locator("#beginner-now-read").inner_text().lower()
        assert page.locator(".history-item").count() == 2


def test_fresh_explicit_buy_permission_renders_buy_now(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(action="BUY_NOW")) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "BUY NOW"
        assert page.locator("#permission-title").inner_text() == "Buy low · entry open"
        assert (
            page.locator("#beginner-confidence").inner_text()
            == "About 12 minutes remaining"
        )
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "lower price" in instruction
        assert "verified demand or retest area" in instruction
        assert "do not chase highs" in instruction
        entry_read = page.locator("#beginner-entry-read").inner_text().lower()
        assert "about 12 minutes remaining" in entry_read
        assert "closes early if live truth changes" in entry_read
        assert (
            page.locator("#beginner-decision-shell").get_attribute("data-tone") == "buy"
        )


def test_fresh_explicit_sell_permission_renders_sell_high(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(action="SELL_NOW")
    ) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "SELL NOW"
        assert (
            page.locator("#permission-title").inner_text() == "Sell high · entry open"
        )
        assert (
            page.locator("#beginner-confidence").inner_text()
            == "About 12 minutes remaining"
        )
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "higher price" in instruction
        assert "verified supply or retest area" in instruction
        assert "do not chase lows" in instruction
        assert (
            page.locator("#beginner-decision-shell").get_attribute("data-tone")
            == "sell"
        )


def test_open_setup_wait_renders_verifying_without_false_closed_state(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT", window_open=True)
    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "WAIT"
        assert (
            page.locator("#permission-title").inner_text() == "Setup window · verifying"
        )
        assert (
            page.locator("#beginner-confidence").inner_text()
            == "About 12 minutes remaining"
        )
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "setup window remains open" in instruction
        assert "current-frame permission is refreshing" in instruction
        entry_read = page.locator("#beginner-entry-read").inner_text().lower()
        assert "about 12 minutes remaining" in entry_read
        assert "do not enter until buy now or sell now returns" in entry_read
        assert (
            "entry closed" not in page.locator("#permission-title").inner_text().lower()
        )
        assert "no verified entry window" not in instruction


def test_visual_badge_discloses_interactive_source_and_live_freshness(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        badge = page.locator("#visual-evidence-status")
        page.wait_for_function(
            "() => document.querySelector('#visual-evidence-status')?.dataset.source === 'interactive'"
        )
        assert badge.is_visible()
        assert badge.get_attribute("data-source") == "interactive"
        assert badge.get_attribute("data-freshness") == "live"
        assert badge.inner_text() == "Interactive overlays · Live"

        page.locator("#mode-raw").click()
        assert badge.get_attribute("data-source") == "chart"
        assert badge.get_attribute("data-freshness") == "live"
        assert "Original broker view" in badge.inner_text()
        assert page.locator("#mode-raw").get_attribute("aria-pressed") == "true"


def test_overlay_selection_opens_plain_language_inspector(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#experience-mode-toggle").click()
        page.wait_for_function(
            "() => document.body.classList.contains('advanced-view')"
        )
        # The calm Live read intentionally excludes the broader zone library.
        # Exercise a current mark that remains visible without changing views.
        page.get_by_role("button", name="Council read").click()

        content = page.locator("#inspector-content")
        assert content.is_visible()
        assert page.locator("#inspector-title").inner_text() == "Council read"
        assert page.locator("#inspector-group").inner_text().lower() == "council"
        assert (
            "current combined plan read"
            in page.locator("#inspector-explanation").inner_text().lower()
        )
        inspector_text = content.inner_text().lower()
        for forbidden in (
            "packet",
            "schema",
            "backend",
            "telemetry",
            "model council",
            "frame id",
        ):
            assert forbidden not in inspector_text


def test_simple_view_overlay_selection_opens_plain_language_drawer(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        assert "simple-view" in (page.locator("body").get_attribute("class") or "")
        page.get_by_role("button", name="Council read").click()

        drawer = page.locator("#mobile-inspector")
        assert drawer.is_visible()
        assert page.locator("#mobile-inspector-title").inner_text() == "Council read"
        assert (
            "current combined plan read"
            in page.locator("#mobile-inspector-copy").inner_text().lower()
        )

        page.locator("#mobile-inspector-close").click()
        assert not drawer.is_visible()


@pytest.mark.parametrize("viewport", [(1440, 1000), (390, 844)])
def test_simple_and_explore_views_do_not_overflow_the_document(
    chromium_browser: Browser,
    viewport: tuple[int, int],
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(), viewport=viewport
    ) as page:
        for mode in ("simple", "advanced"):
            if mode == "advanced":
                page.locator("#experience-mode-toggle").click()
                page.wait_for_function(
                    "() => document.body.classList.contains('advanced-view')"
                )
            metrics = page.evaluate(
                """
                () => ({
                  documentWidth: document.documentElement.scrollWidth,
                  viewportWidth: window.innerWidth,
                  boxes: ['.app-header', '.brand', '#experience-mode-toggle', '#overlay-explorer', '.vision-workspace', '#surface-stage']
                    .map(selector => {
                      const node = document.querySelector(selector);
                      const rect = node.getBoundingClientRect();
                      return {selector, left: rect.left, right: rect.right, width: rect.width, height: rect.height};
                    }),
                })
                """
            )
            assert metrics["documentWidth"] <= metrics["viewportWidth"] + 1, (
                viewport,
                mode,
                metrics,
            )
            for box in metrics["boxes"]:
                assert box["width"] > 0 and box["height"] > 0, (viewport, mode, box)
                assert box["left"] >= -1, (viewport, mode, box)
                assert box["right"] <= metrics["viewportWidth"] + 1, (
                    viewport,
                    mode,
                    box,
                )


def test_line_overlay_with_bounds_has_no_visible_capsule(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-clear").click()
        page.locator('[data-overlay-family="trendlines"]').click()
        hotspot = page.locator('.surface-hotspot[data-overlay-id="support-current"]')
        assert hotspot.count() == 1
        assert "line-hit" in (hotspot.get_attribute("class") or "").split()
        assert page.locator('polyline[data-overlay-id="support-current"]').count() == 1

        def rendered_style() -> dict[str, str]:
            return cast(
                dict[str, str],
                hotspot.evaluate(
                    """node => ({
                border: getComputedStyle(node).borderTopColor,
                background: getComputedStyle(node).backgroundColor,
                    })"""
                ),
            )

        for activate in (hotspot.hover, hotspot.focus):
            activate()
            style = rendered_style()
            assert style["border"] == "rgba(0, 0, 0, 0)"
            assert style["background"] == "rgba(0, 0, 0, 0)"


def test_overlay_geometry_stays_attached_through_zoom_pan_and_resize(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(), viewport=(1280, 900)
    ) as page:
        page.locator("#layers-all").click()
        assert (
            page.locator(
                'rect.surface-chart-bounds[data-overlay-id="chart-bounds-current"]'
            ).count()
            == 1
        )
        assert (
            page.locator(
                '.surface-hotspot[data-overlay-id="chart-bounds-current"]'
            ).count()
            == 0
        )

        def geometry() -> dict[str, Any]:
            return page.evaluate(
                """
                () => {
                  const image = document.querySelector('#surface-raw').getBoundingClientRect();
                  const box = document.querySelector('[data-overlay-id="demand-current"]').getBoundingClientRect();
                  const line = document.querySelector('polyline[data-overlay-id="lstm-current"]');
                  return {
                    box: [
                      (box.left - image.left) / image.width,
                      (box.top - image.top) / image.height,
                      box.width / image.width,
                      box.height / image.height,
                    ],
                    linePoints: line ? line.getAttribute('points') : '',
                    viewBox: document.querySelector('#surface-line-svg').getAttribute('viewBox'),
                  };
                }
                """
            )

        baseline = geometry()
        assert baseline["linePoints"]
        page.locator("#zoom-in").click()
        page.locator("#zoom-in").click()
        page.wait_for_timeout(250)

        stage = page.locator("#surface-stage").bounding_box()
        assert stage is not None
        start_x = stage["x"] + stage["width"] * 0.95
        start_y = stage["y"] + stage["height"] * 0.90
        scroll_before = page.evaluate(
            "() => ({left: document.querySelector('#surface-stage').scrollLeft, top: document.querySelector('#surface-stage').scrollTop})"
        )
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x - 90, start_y - 45, steps=4)
        page.mouse.up()
        scroll_after = page.evaluate(
            "() => ({left: document.querySelector('#surface-stage').scrollLeft, top: document.querySelector('#surface-stage').scrollTop})"
        )
        assert (
            scroll_after["left"] > scroll_before["left"]
            or scroll_after["top"] > scroll_before["top"]
        )

        page.set_viewport_size({"width": 1040, "height": 760})
        page.wait_for_timeout(50)
        changed = geometry()
        for actual, expected in zip(changed["box"], baseline["box"], strict=True):
            assert abs(float(actual) - float(expected)) <= 0.002
        assert changed["linePoints"] == baseline["linePoints"]
        assert changed["viewBox"] == baseline["viewBox"]

        page.locator("#zoom-actual").click()
        actual_size = geometry()
        page.locator("#zoom-fit").click()
        fit_again = geometry()
        for observed in (actual_size, fit_again):
            for actual, expected in zip(observed["box"], baseline["box"], strict=True):
                assert abs(float(actual) - float(expected)) <= 0.002
            assert observed["linePoints"] == baseline["linePoints"]
            assert observed["viewBox"] == baseline["viewBox"]


def test_full_broker_is_default_and_chart_overlays_project_into_its_viewport(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(), viewport=(1280, 900)
    ) as page:
        page.locator("#layers-all").click()
        assert (
            page.evaluate("() => window.PhoenixGuardDashboard.getState().frameMode")
            == "window"
        )
        assert "latest-window" in (
            page.locator("#surface-raw").get_attribute("src") or ""
        )
        projected = page.evaluate(
            """
            () => {
              const image = document.querySelector('#surface-raw').getBoundingClientRect();
              const box = document.querySelector('[data-overlay-id="demand-current"]').getBoundingClientRect();
              return [
                (box.left - image.left) / image.width,
                (box.top - image.top) / image.height,
                box.width / image.width,
                box.height / image.height,
              ];
            }
            """
        )
        for actual, expected in zip(
            projected, (0.164, 0.264, 0.304, 0.432), strict=True
        ):
            assert abs(float(actual) - expected) <= 0.003

        page.locator("#frame-chart").click()
        page.wait_for_function(
            "() => document.querySelector('#surface-raw').src.includes('latest-chart')"
        )
        focused = page.evaluate(
            """
            () => {
              const image = document.querySelector('#surface-raw').getBoundingClientRect();
              const box = document.querySelector('[data-overlay-id="demand-current"]').getBoundingClientRect();
              return [
                (box.left - image.left) / image.width,
                (box.top - image.top) / image.height,
                box.width / image.width,
                box.height / image.height,
              ];
            }
            """
        )
        for actual, expected in zip(focused, (0.08, 0.18, 0.38, 0.54), strict=True):
            assert abs(float(actual) - expected) <= 0.003


def test_mismatched_historical_overlay_is_not_drawn_on_the_current_frame(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    next(row for row in payload["overlays"] if row["id"] == "past-sell")["frame_id"] = (
        41
    )
    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="past-sell"]').count() == 0


def test_new_live_revision_reuses_studied_history_and_updates_only_live_edge_geometry(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["overlays"].append(
        {
            "id": "latest-candle",
            "type": "candle",
            "side": "BUY",
            "group": "movement",
            "family": "current_candles",
            "layer": "current_candle",
            "label": "Current price",
            "bounds": [0.70, 0.50, 0.724, 0.56],
            "points": [],
            "line_points": [],
            "confidence": 0.91,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
        }
    )
    updated = copy.deepcopy(initial)
    updated["revision"] = 43
    latest = next(row for row in updated["overlays"] if row["id"] == "latest-candle")
    latest["bounds"] = [0.70, 0.512, 0.724, 0.572]
    forecast = next(row for row in updated["overlays"] if row["id"] == "lstm-current")
    forecast["line_points"] = [
        [point[0], round(point[1] + 0.012, 4)]
        for point in forecast["line_points"]
    ]
    forecast["forecast_anchor"] = {
        **forecast["forecast_anchor"],
        "y_norm": round(forecast["forecast_anchor"]["y_norm"] + 0.012, 4),
    }
    forecast["forecast_band_points"] = [
        [point[0], round(point[1] + 0.012, 4)]
        for point in forecast["forecast_band_points"]
    ]
    forecast["forecast_candles"] = [
        {
            **candle,
            **{
                field: round(candle[field] + 0.012, 4)
                for field in (
                    "open_y_norm",
                    "high_y_norm",
                    "low_y_norm",
                    "close_y_norm",
                )
            },
        }
        for candle in forecast["forecast_candles"]
    ]
    forecast["forecast_scenarios"] = [
        {
            **scenario,
            "line_points": [
                [point[0], round(point[1] + 0.012, 4)]
                for point in scenario["line_points"]
            ],
        }
        for scenario in forecast["forecast_scenarios"]
    ]

    with _dashboard_page(chromium_browser, initial) as page:
        page.locator("#layers-all").click()
        before = page.evaluate(
            """
            () => {
              const history = document.querySelector(
                '.surface-hotspot[data-overlay-id="past-sell"]'
              );
              const current = document.querySelector(
                '.surface-hotspot[data-overlay-id="latest-candle"]'
              );
              const forecast = document.querySelector(
                'g.surface-forecast-composite[data-overlay-id="lstm-current"]'
              );
              const path = document.querySelector(
                'polyline[data-overlay-id="lstm-current"]'
              );
              window.__STUDIED_HISTORY_NODE = history;
              window.__CURRENT_CANDLE_NODE = current;
              window.__FORECAST_GROUP_NODE = forecast;
              window.__HISTORY_LIST_NODE = document.querySelector(
                '.history-item[data-history-id^="history-0-"]'
              );
              return {
                historySignature: history.dataset.renderSignature,
                historyStyle: history.getAttribute('style'),
                currentStyle: current.getAttribute('style'),
                forecastSignature: forecast.dataset.renderSignature,
                forecastPoints: path.getAttribute('points'),
                cacheHits:
                  window.PhoenixGuardDashboard.getState().overlayRenderStats
                    .projectionCacheHits,
              };
            }
            """
        )
        page.evaluate("payload => window.renderOperatorState(payload)", updated)
        after = page.evaluate(
            """
            () => {
              const history = document.querySelector(
                '.surface-hotspot[data-overlay-id="past-sell"]'
              );
              const current = document.querySelector(
                '.surface-hotspot[data-overlay-id="latest-candle"]'
              );
              const forecast = document.querySelector(
                'g.surface-forecast-composite[data-overlay-id="lstm-current"]'
              );
              const path = document.querySelector(
                'polyline[data-overlay-id="lstm-current"]'
              );
              return {
                historySame: window.__STUDIED_HISTORY_NODE === history,
                historyListSame:
                  window.__HISTORY_LIST_NODE === document.querySelector(
                    '.history-item[data-history-id^="history-0-"]'
                  ),
                currentSame: window.__CURRENT_CANDLE_NODE === current,
                forecastSame: window.__FORECAST_GROUP_NODE === forecast,
                historySignature: history.dataset.renderSignature,
                historyStyle: history.getAttribute('style'),
                currentStyle: current.getAttribute('style'),
                forecastSignature: forecast.dataset.renderSignature,
                forecastPoints: path.getAttribute('points'),
                cacheHits:
                  window.PhoenixGuardDashboard.getState().overlayRenderStats
                    .projectionCacheHits,
              };
            }
            """
        )

        assert after["historySame"] is True
        assert after["historyListSame"] is True
        assert after["currentSame"] is True
        assert after["forecastSame"] is True
        assert after["historySignature"] == before["historySignature"]
        assert after["historyStyle"] == before["historyStyle"]
        assert after["currentStyle"] != before["currentStyle"]
        assert after["forecastSignature"] != before["forecastSignature"]
        assert after["forecastPoints"] != before["forecastPoints"]
        assert after["cacheHits"] > before["cacheHits"]


def test_studied_history_reprojects_in_place_when_viewport_geometry_changes(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    remapped = copy.deepcopy(initial)
    remapped["revision"] = 43
    remapped["surface"]["overlay_geometry_revision"] = "geometry-2"
    remapped["surface"]["overlay_viewport"]["bounds"] = [0.16, 0.12, 0.96, 0.92]

    with _dashboard_page(chromium_browser, initial) as page:
        page.locator("#layers-all").click()
        before = page.evaluate(
            """
            () => {
              const node = document.querySelector(
                '.surface-hotspot[data-overlay-id="past-sell"]'
              );
              window.__REPROJECTED_HISTORY_NODE = node;
              return node.getAttribute('style');
            }
            """
        )
        page.evaluate("payload => window.renderOperatorState(payload)", remapped)
        after = page.evaluate(
            """
            () => {
              const node = document.querySelector(
                '.surface-hotspot[data-overlay-id="past-sell"]'
              );
              return {
                same: window.__REPROJECTED_HISTORY_NODE === node,
                style: node.getAttribute('style'),
              };
            }
            """
        )
        assert after["same"] is True
        assert after["style"] != before


def test_off_surface_and_anchor_mismatched_geometry_is_suppressed_not_clamped(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    invalid_history = copy.deepcopy(
        next(row for row in payload["overlays"] if row["id"] == "past-sell")
    )
    invalid_history["id"] = "off-surface-history"
    invalid_history["bounds"] = [-0.25, 0.20, 0.08, 0.45]
    invalid_forecast = copy.deepcopy(
        next(row for row in payload["overlays"] if row["id"] == "lstm-current")
    )
    invalid_forecast["id"] = "detached-forecast"
    invalid_forecast["forecast_scenarios"][0]["line_points"][0] = [0.25, 0.20]
    payload["overlays"].extend([invalid_history, invalid_forecast])

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="off-surface-history"]').count() == 0
        assert page.locator('[data-overlay-id="detached-forecast"]').count() == 0
        valid = page.locator(
            'g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert valid.count() == 1
        assert valid.get_attribute("data-anchor-state") == "CANDLE_LOCKED"


def test_verified_closed_forecast_must_stay_adjacent_to_live_forming_candle(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    forecast = next(row for row in payload["overlays"] if row["id"] == "lstm-current")
    anchor_x, anchor_y = forecast["line_points"][0]
    forecast["forecast_anchor"]["source"] = "TRACKER_LATEST_CLOSED_CANDLE"
    for index, center_x in enumerate(
        (anchor_x - 0.16, anchor_x - 0.08, anchor_x),
        start=1,
    ):
        payload["overlays"].append(
            {
                "id": f"sparse-recent-candle-{index}",
                "type": "candle",
                "side": "BUY",
                "group": "movement",
                "family": "current_candles",
                "layer": "recent_candles",
                "label": "Recent candle",
                "bounds": [
                    center_x - 0.006,
                    anchor_y - 0.04,
                    center_x + 0.006,
                    anchor_y + 0.04,
                ],
                "points": [],
                "line_points": [],
                "confidence": 0.90,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
                "candle_state": "CLOSED",
            }
        )
    payload["overlays"].append(
        {
            "id": "forming-current-price",
            "type": "candle",
            "side": "BUY",
            "group": "movement",
            "family": "current_candles",
            "layer": "current_candle",
            "label": "Current price",
            "bounds": [
                anchor_x + 0.074,
                anchor_y + 0.10,
                anchor_x + 0.086,
                anchor_y + 0.18,
            ],
            "points": [[anchor_x + 0.08, anchor_y + 0.14]],
            "line_points": [],
            "confidence": 0.94,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
            "candle_state": "FORMING",
        }
    )

    stale = copy.deepcopy(forecast)
    stale["id"] = "stale-verified-closed-forecast"
    shift_x = -0.24
    stale["line_points"] = [
        [round(point[0] + shift_x, 6), point[1]]
        for point in stale["line_points"]
    ]
    stale["bounds"] = [
        stale["bounds"][0] + shift_x,
        stale["bounds"][1],
        stale["bounds"][2] + shift_x,
        stale["bounds"][3],
    ]
    stale["forecast_anchor"]["x_norm"] = round(
        stale["forecast_anchor"]["x_norm"] + shift_x,
        6,
    )
    stale["forecast_band_points"] = [
        [round(point[0] + shift_x, 6), point[1]]
        for point in stale["forecast_band_points"]
    ]
    stale["forecast_candles"] = [
        {
            **candle,
            "x_norm": round(candle["x_norm"] + shift_x, 6),
        }
        for candle in stale["forecast_candles"]
    ]
    stale["forecast_scenarios"] = [
        {
            **scenario,
            "line_points": [
                [round(point[0] + shift_x, 6), point[1]]
                for point in scenario["line_points"]
            ],
        }
        for scenario in stale["forecast_scenarios"]
    ]
    payload["overlays"].append(stale)

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="forecast"]').click()
        current = page.locator(
            'g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert current.count() == 1
        assert current.get_attribute("data-anchor-state") == "CANDLE_LOCKED"
        assert current.get_attribute("data-event-count") == "12"
        assert page.locator(
            '[data-overlay-id="stale-verified-closed-forecast"]'
        ).count() == 0


def test_whole_forecast_displaced_from_latest_candle_is_not_candle_locked(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    forecast = next(row for row in payload["overlays"] if row["id"] == "lstm-current")
    anchor_x, anchor_y = forecast["line_points"][0]
    payload["overlays"].append(
        {
            "id": "latest-candle-anchor",
            "type": "candle",
            "side": "BUY",
            "group": "movement",
            "family": "current_candles",
            "layer": "current_candle",
            "label": "Current price",
            "bounds": [anchor_x - 0.006, anchor_y, anchor_x + 0.006, anchor_y + 0.06],
            "points": [],
            "line_points": [],
            "confidence": 0.93,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
        }
    )
    displaced = copy.deepcopy(forecast)
    displaced["id"] = "whole-composite-displaced"
    displaced["line_points"] = [
        [point[0], round(point[1] + 0.18, 4)]
        for point in displaced["line_points"]
    ]
    displaced["forecast_scenarios"] = [
        {
            **scenario,
            "line_points": [
                [point[0], round(point[1] + 0.18, 4)]
                for point in scenario["line_points"]
            ],
        }
        for scenario in displaced["forecast_scenarios"]
    ]
    displaced["forecast_anchor"]["y_norm"] = round(
        displaced["forecast_anchor"]["y_norm"] + 0.18,
        4,
    )
    displaced["bounds"] = [
        displaced["bounds"][0],
        displaced["bounds"][1] + 0.18,
        displaced["bounds"][2],
        displaced["bounds"][3] + 0.18,
    ]
    payload["overlays"].append(displaced)

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#layers-all").click()
        assert page.locator(
            'g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        ).count() == 1
        assert page.locator(
            '[data-overlay-id="whole-composite-displaced"]'
        ).count() == 0


def test_explicit_candle_close_overrides_wick_bounds_and_rejects_displaced_forecast(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    forecast = next(row for row in payload["overlays"] if row["id"] == "lstm-current")
    anchor_x, anchor_y = forecast["line_points"][0]
    payload["overlays"].append(
        {
            "id": "latest-candle-exact-close",
            "type": "candle",
            "side": "SELL",
            "group": "movement",
            "family": "current_candles",
            "layer": "current_candle",
            "label": "Current price",
            # SELL used to infer the wick bottom as the close, which is far
            # from the model's verified tracker close in this regression.
            "bounds": [
                anchor_x - 0.006,
                anchor_y - 0.12,
                anchor_x + 0.006,
                anchor_y + 0.12,
            ],
            "points": [[anchor_x, anchor_y]],
            "line_points": [],
            "confidence": 0.93,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
        }
    )
    displaced = copy.deepcopy(forecast)
    displaced["id"] = "explicit-close-displaced"
    displaced["line_points"] = [
        [point[0], round(point[1] + 0.18, 4)]
        for point in displaced["line_points"]
    ]
    displaced["forecast_scenarios"] = [
        {
            **scenario,
            "line_points": [
                [point[0], round(point[1] + 0.18, 4)]
                for point in scenario["line_points"]
            ],
        }
        for scenario in displaced["forecast_scenarios"]
    ]
    displaced["forecast_anchor"]["y_norm"] = round(
        displaced["forecast_anchor"]["y_norm"] + 0.18,
        4,
    )
    displaced["bounds"] = [
        displaced["bounds"][0],
        displaced["bounds"][1] + 0.18,
        displaced["bounds"][2],
        displaced["bounds"][3] + 0.18,
    ]
    payload["overlays"].append(displaced)

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#layers-all").click()
        valid = page.locator(
            'g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert valid.count() == 1
        assert valid.get_attribute("data-anchor-state") == "CANDLE_LOCKED"
        assert page.locator(
            '[data-overlay-id="explicit-close-displaced"]'
        ).count() == 0


def test_hiding_a_selected_family_closes_the_stale_inspector(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').click()
        assert "has-selection" in (page.locator("body").get_attribute("class") or "")
        page.locator('[data-overlay-family="lstm"]').click()
        assert "has-selection" not in (
            page.locator("body").get_attribute("class") or ""
        )
        assert page.locator("#inspector-content").is_hidden()


def test_overlay_keyboard_focus_survives_an_unrelated_family_toggle(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        target = page.locator('.surface-hotspot[data-overlay-id="two-candle-current"]')
        target.focus()
        page.locator('[data-overlay-family="smc"]').click()
        page.wait_for_function(
            "() => document.activeElement?.dataset?.overlayId === 'two-candle-current'"
        )


def test_newer_observation_is_accepted_after_backend_revision_reset(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    restarted = copy.deepcopy(initial)
    restarted["revision"] = 1
    restarted["freshness"]["observed_at"] += 60
    restarted["surface"].update(
        {
            "frame_id": 43,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
        }
    )
    for overlay in restarted["overlays"]:
        overlay["frame_id"] = 43
    with _dashboard_page(chromium_browser, initial) as page:
        page.evaluate("payload => { window.__OPERATOR_PAYLOAD = payload; }", restarted)
        page.evaluate("() => window.PhoenixGuardDashboard.refresh({force: true})")
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().revision === 1"
        )
        page.wait_for_function(
            "() => !document.querySelector('#surface-canvas').classList.contains('updating')"
        )


def test_chart_and_new_frame_overlays_swap_only_after_the_image_is_ready(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    next_frame = copy.deepcopy(initial)
    next_frame["revision"] = 43
    next_frame["surface"].update(
        {
            "frame_id": 43,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
        }
    )
    for overlay in next_frame["overlays"]:
        overlay["frame_id"] = 43
        if overlay["id"] == "demand-current":
            overlay["id"] = "new-demand"
    queued_frame = copy.deepcopy(next_frame)
    queued_frame["revision"] = 44
    queued_frame["surface"].update(
        {
            "frame_id": 44,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=44",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=44",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=44",
        }
    )
    for overlay in queued_frame["overlays"]:
        overlay["frame_id"] = 44
        if overlay["id"] == "new-demand":
            overlay["id"] = "queued-demand"
    with _dashboard_page(
        chromium_browser,
        initial,
        delayed_artifact_frames={43: 0.25},
    ) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="demand-current"]').count() == 1
        transition = page.evaluate(
            """
            payloads => {
              window.renderOperatorState(payloads[0]);
              const firstSource = document.querySelector('#surface-raw').src;
              window.renderOperatorState(payloads[1]);
              return {
                busy: document.querySelector('#surface-canvas').getAttribute('aria-busy'),
                oldGeometryVisible: document.querySelector('[data-overlay-id="demand-current"]') !== null,
                newGeometryVisible: document.querySelector('[data-overlay-id="new-demand"]') !== null,
                sourceStayedOnFirstLoad:
                  firstSource.includes('frame_id=43') &&
                  document.querySelector('#surface-raw').src === firstSource,
              };
            }
            """,
            [next_frame, queued_frame],
        )
        assert transition == {
            "busy": "true",
            "oldGeometryVisible": True,
            "newGeometryVisible": False,
            "sourceStayedOnFirstLoad": True,
        }
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-id=\"queued-demand\"]') !== null",
            timeout=10_000,
        )
        assert page.locator('[data-overlay-id="demand-current"]').count() == 0
        assert page.locator('[data-overlay-id="queued-demand"]').count() == 1
        assert page.locator('[data-overlay-id="new-demand"]').count() == 0


def test_nonvisual_operator_revision_does_not_reload_the_same_broker_frame(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    same_frame_update = copy.deepcopy(initial)
    same_frame_update["revision"] = 43
    same_frame_update["freshness"]["age_seconds"] = 2
    same_frame_update["permission"]["message"] = (
        "Wait. The same visual frame is still being evaluated."
    )

    with _dashboard_page(chromium_browser, initial) as page:
        result = page.evaluate(
            """
            payload => {
              const image = document.querySelector('#surface-raw');
              const sourceBefore = image.src;
              window.renderOperatorState(payload);
              return {
                sourceBefore,
                sourceAfter: image.src,
                updating: document.querySelector('#surface-canvas').classList.contains('updating'),
                geometryVisible: document.querySelector('[data-overlay-id="smc-order-block"]') !== null,
              };
            }
            """,
            same_frame_update,
        )

        assert result["sourceBefore"] == result["sourceAfter"]
        assert result["updating"] is False
        assert result["geometryVisible"] is True


def test_latest_history_row_keeps_its_real_ended_lifecycle(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["current_move"]["direction"] = "HOLD"
    payload["history"][-1]["state"] = "ENDED"
    payload["history"][-1]["summary"] = "The latest recorded movement has ended."
    with _dashboard_page(chromium_browser, payload) as page:
        latest = page.locator(".history-item").first
        assert latest.get_attribute("aria-current") == "true"
        assert latest.locator(".history-state").inner_text().lower() == "ended"


def test_empty_session_history_records_completed_sideways_frames(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["history"] = []
    payload["current_move"].update(
        {
            "direction": "HOLD",
            "state": "UNKNOWN",
            "summary": "Current movement is not confirmed.",
        }
    )
    with _dashboard_page(chromium_browser, payload) as page:
        page.wait_for_function(
            "() => document.querySelectorAll('.history-item').length === 1",
            timeout=10_000,
        )
        assert page.locator("#history-count").inner_text() == "1 observation"
        assert page.locator(".history-side").first.inner_text() == "WAIT"
        assert (
            page.locator(".history-copy").first.inner_text()
            == "Current movement is not confirmed."
        )


def test_pair_switch_resets_local_history_namespace(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["surface"]["semantic_identity"] = "surface-eur-usd-m5"
    initial["history"] = []
    initial["current_move"]["summary"] = "EUR/USD local upward move."

    switched = copy.deepcopy(initial)
    switched["revision"] = 43
    switched["market"]["symbol"] = "GBP/USD"
    switched["surface"].update(
        {
            "semantic_identity": "surface-gbp-usd-m5",
            "frame_id": 43,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
        }
    )
    switched["freshness"]["observed_at"] += 60
    switched["current_move"].update(
        {
            "observed_at": switched["freshness"]["observed_at"],
            "frame_id": 43,
            "summary": "GBP/USD first local upward move.",
        }
    )
    for overlay in switched["overlays"]:
        overlay["frame_id"] = 43

    with _dashboard_page(chromium_browser, initial) as page:
        assert page.locator(".history-copy").all_inner_texts() == [
            "EUR/USD local upward move."
        ]
        page.evaluate("payload => window.renderOperatorState(payload)", switched)
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('.history-copy')).some(node => node.textContent.includes('GBP/USD'))",
            timeout=10_000,
        )
        assert page.locator(".history-copy").all_inner_texts() == [
            "GBP/USD first local upward move."
        ]
