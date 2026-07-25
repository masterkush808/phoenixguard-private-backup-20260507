from __future__ import annotations

import copy
import json
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
            "episode": {
                "state": "ACTIVE",
                "event_horizon": 12,
                "event_cursor": 0,
            },
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
                "kind": "chart_area",
                "kind_label": "Chart area",
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
                "kind": "lower_reaction_area",
                "kind_label": "Lower reaction area",
                "label": "Lower reaction area",
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
                "kind": "rising_support_line",
                "kind_label": "Rising support line",
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
                "kind": "past_movement",
                "kind_label": "Past movement",
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
                "family": "market_context",
                "layer": "market_context",
                "kind": "reaction_zone",
                "kind_label": "Reaction zone",
                "label": "Reaction zone",
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
                "kind": "combined_analysis",
                "kind_label": "Combined analysis",
                "label": "Combined analysis",
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
                "kind": "near_term_read",
                "kind_label": "Near-term candle read",
                "label": "Near-term candle read",
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
                "kind": "sequence_outlook",
                "kind_label": "12-step sequence outlook",
                "label": "12-step sequence outlook · uncertain",
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
        window.__TRACKING_EPISODE_RESPONSES = {{
          start: {{status: 202, body: {{state: "STARTING"}}}},
          stop: {{status: 202, body: {{state: "STOPPING"}}}},
          reset: {{status: 202, body: {{state: "RESETTING"}}}},
        }};
        window.__TRACKING_READINESS_RESPONSE = null;
        window.__TRACKING_EPISODE_DELAY_MS = 0;
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
          if (href.endsWith("/tracking-episodes/readiness")) {{
            const readiness = window.__TRACKING_READINESS_RESPONSE;
            const status = readiness ? Number(readiness.status || 200) : 404;
            const body = readiness ? readiness.body : {{detail: "not found"}};
            return Promise.resolve(new Response(JSON.stringify(body), {{
              status,
              headers: {{"Content-Type": "application/json"}},
            }}));
          }}
          const episodeAction = href.endsWith("/tracking-episodes/start")
            ? "start"
            : href.endsWith("/tracking-episodes/stop")
              ? "stop"
              : href.endsWith("/tracking-episodes/reset")
                ? "reset"
                : "";
          if (episodeAction) {{
            const action = window.__TRACKING_EPISODE_RESPONSES[episodeAction];
            const respond = () => new Response(JSON.stringify(action.body), {{
                status: Number(action.status || 200),
                headers: {{"Content-Type": "application/json"}},
              }});
            const delay = Number(window.__TRACKING_EPISODE_DELAY_MS || 0);
            return delay > 0
              ? new Promise(resolve => nativeSetTimeout(() => resolve(respond()), delay))
              : Promise.resolve(respond());
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
                "href": "/v1/mobile/window-tracker/sessions/operator-test/tracking-episodes/readiness",
                "method": "GET",
            },
            {
                "href": "/v1/mobile/operator/state/v1/operator-test?view=all",
                "method": "GET",
            }
        ]
        assert page.locator("#beginner-next-read").inner_text() == (
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
            "tracking-start",
            "tracking-stop",
            "tracking-reset",
            "tracking-plan-toggle",
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
        assert page.locator("button[data-overlay-view]").count() == 7
        assert page.locator("button[data-overlay-family]").count() == 13
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
            "market_context",
            "structure",
            "zones",
            "plan",
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
            == 13
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
            "count => window.__FETCH_REQUESTS.length === count + 2",
            arg=request_count,
        )

        page.locator("#history-scrubber").fill("0")
        page.locator('button[data-overlay-view="live"]').click()
        page.locator("#experience-mode-toggle").click()
        page.get_by_role("button", name="Combined analysis", exact=True).click()
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


def test_show_all_and_labels_on_reveals_collision_and_policy_hidden_labels(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        page.locator('button[data-label-mode="on"]').click()
        exhaustive = page.evaluate(
            """
            () => {
              const root = document.querySelector('#hotspot-layer');
              root.innerHTML = '';
              function add(priority, label, left, policyHidden = false) {
                const button = document.createElement('button');
                button.className = 'surface-hotspot' + (policyHidden ? ' label-policy-hidden' : '');
                button.dataset.priority = String(priority);
                const span = document.createElement('span');
                span.textContent = label;
                span.getBoundingClientRect = () => ({
                  left, top: 30, right: left + 90, bottom: 50, width: 90, height: 20,
                });
                button.appendChild(span);
                root.appendChild(button);
                return button;
              }
              const low = add(10, 'LOW', 20);
              const high = add(200, 'HIGH', 20);
              const policy = add(100, 'POLICY', 220, true);
              window.resolveLabelCollisions(root);
              return {
                bodyClass: document.body.className,
                lowHidden: low.classList.contains('label-collision-hidden'),
                highHidden: high.classList.contains('label-collision-hidden'),
                lowOpacity: Number(getComputedStyle(low.querySelector('span')).opacity),
                highOpacity: Number(getComputedStyle(high.querySelector('span')).opacity),
                policyOpacity: Number(getComputedStyle(policy.querySelector('span')).opacity),
              };
            }
            """
        )
        assert "labels-show-all" in exhaustive["bodyClass"]
        assert exhaustive["lowHidden"] is False
        assert exhaustive["highHidden"] is False
        assert exhaustive["lowOpacity"] > 0.5
        assert exhaustive["highOpacity"] > 0.5
        assert exhaustive["policyOpacity"] > 0.5

        page.locator('button[data-label-mode="hover"]').click()
        decluttered = page.evaluate(
            """
            () => {
              const root = document.querySelector('#hotspot-layer');
              window.resolveLabelCollisions(root);
              const nodes = Array.from(root.querySelectorAll('.surface-hotspot'));
              return {
                bodyClass: document.body.className,
                lowHidden: nodes[0].classList.contains('label-collision-hidden'),
                highHidden: nodes[1].classList.contains('label-collision-hidden'),
                policyOpacity: Number(getComputedStyle(nodes[2].querySelector('span')).opacity),
              };
            }
            """
        )
        assert "labels-show-all" not in decluttered["bodyClass"]
        assert decluttered["lowHidden"] is True
        assert decluttered["highHidden"] is False
        assert decluttered["policyOpacity"] == 0


def test_market_story_and_history_prefer_v3_regression_study(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT")
    market_study = {
        "regression": {
            "major_trend": {"side": "BUY", "slope": 0.18, "confidence": 0.88},
            "inner_trend": {"side": "SELL", "slope": -0.09, "confidence": 0.79},
        },
        "behavior": {
            "current_state": {
                "state": "PULLBACK",
                "direction": "SELL",
                "candle_count": 2,
            },
            "current_segment": {
                "state": "DOWN_SWING",
                "next_state": "REST",
                "candle_count": 2,
            },
            "market_story": (
                "Major trend is up while the inner trend pulls back down; "
                "two rests preceded the current continuation."
            ),
        },
        "historical_similarity": {
            "historical_continuation": {
                "side": "BUY",
                "confidence": 0.81,
                "support": 14,
                "status": "SUPPORTED",
            }
        },
    }
    payload["tracking"]["market_study_v3"] = market_study
    payload["history"] = [
        {
            "id": "study-e1",
            "observed_at": 4_102_444_300.0,
            "direction": "BUY",
            "state": "HISTORICAL",
            "summary": "Upward swing began.",
        },
        {
            "id": "study-e2",
            "observed_at": 4_102_444_400.0,
            "direction": "SELL",
            "state": "HISTORICAL",
            "summary": "Inner pullback moved down.",
        },
        {
            "id": "study-e3",
            "observed_at": 4_102_444_500.0,
            "direction": "SELL",
            "state": "CURRENT",
            "agreement": True,
            "market_study_v3": market_study,
            "summary": "The downward inner move continued.",
        },
    ]

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "CLOSED"
        assert page.locator("#story-step-one-label").inner_text() == "MAJOR TREND"
        assert page.locator("#story-step-two-label").inner_text() == "INNER TREND"
        assert page.locator("#story-step-three-label").inner_text() == "REGRESSION STUDY"
        assert page.locator("#current-move-title").inner_text() == "Uptrend"
        assert page.locator("#forecast-title").inner_text() == "Downward pullback"
        assert page.locator("#permission-title").inner_text() == "History leans upward"
        assert "two rests" in page.locator("#beginner-entry-read").inner_text().lower()
        assert "downward pullback" in page.locator("#beginner-story-summary").inner_text().lower()

        latest = page.locator('[data-history-id="study-e3"]')
        assert latest.locator(".history-major-trend").inner_text() == "Major · uptrend"
        assert latest.locator(".history-inner-trend").inner_text() == "Inner · down"
        assert latest.locator(".history-side").inner_text() == "DOWN CONTINUE"
        assert latest.locator(".history-regression").inner_text() == "REGRESSION MATCH"


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


def test_tracking_episode_start_stop_keeps_the_anchored_story_and_server_history(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["history"] = []
    initial["tracking"]["episode"] = {
        "state": "IDLE",
        "ready": False,
        "event_horizon": 12,
        "event_cursor": 0,
    }
    ready = copy.deepcopy(initial)
    ready["revision"] = 42.5
    ready["tracking"]["episode"]["ready"] = True
    active = copy.deepcopy(initial)
    active["revision"] = 43
    active["tracking"]["episode"] = {
        "episode_id": "episode-live-presentation",
        "state": "ACTIVE",
        "event_horizon": 12,
        "event_cursor": 4,
        "started_at": 4_102_444_400.0,
        "baseline": {
            "title": "Starting climb",
            "summary": "The episode began while price was climbing.",
            "direction": "BUY",
        },
        "current": {
            "title": "Current pause",
            "summary": "The move is pausing while the original plan remains anchored.",
        },
        "plan": {
            "title": "Hold the original plan",
            "summary": "Wait for the saved lower entry area; do not chase.",
            "evidence_families": ["trendlines", "market_context", "triggers"],
        },
        "events": [
            {
                "event_id": "episode-event-1",
                "event_index": 1,
                "observed_at": 4_102_444_510.0,
                "direction": "BUY",
                "predicted_direction": "BUY",
                "agreement": True,
                "state": "historical",
                "title": "Move strengthened",
                "summary": "Price extended from the starting chart.",
            },
            {
                "event_id": "episode-event-2",
                "event_index": 2,
                "observed_at": 4_102_444_520.0,
                "direction": "SELL",
                "predicted_direction": "BUY",
                "agreement": False,
                "state": "historical",
                "summary": "Price moved down and differed from the saved block.",
            },
            {
                "event_id": "episode-event-3",
                "event_index": 3,
                "observed_at": 4_102_444_530.0,
                "direction": "HOLD",
                "predicted_direction": "BUY",
                "agreement": None,
                "state": "historical",
                "summary": "The candle was recorded without a directional comparison.",
            },
            {
                "event_id": "episode-event-4",
                "event_index": 4,
                "observed_at": 4_102_444_540.0,
                "direction": "BUY",
                "predicted_direction": "BUY",
                "agreement": True,
                "state": "current",
                "title": "Current pause",
                "summary": "The move is pausing while the original plan remains anchored.",
            },
        ],
        "future_blocks": copy.deepcopy(_composite_forecast_geometry()[1]),
    }
    completed = copy.deepcopy(active)
    completed["revision"] = 44
    completed["tracking"]["episode"]["state"] = "COMPLETED"

    with _dashboard_page(chromium_browser, initial) as page:
        assert page.locator("#tracking-start").is_visible()
        assert page.locator("#tracking-start").is_disabled()
        assert page.locator("#tracking-episode-state").inner_text() == "PREPARING"
        assert page.locator("#run-forecast").count() == 0

        page.evaluate("payload => window.renderOperatorState(payload)", ready)
        page.wait_for_function(
            "() => document.querySelector('#tracking-start')?.disabled === false"
        )

        page.evaluate(
            "payload => window.__TRACKING_EPISODE_RESPONSES.start.body = payload",
            active,
        )
        page.locator("#tracking-start").click()
        page.wait_for_function(
            "() => document.querySelector('#tracking-ribbon')?.dataset.state === 'active'"
        )
        assert page.locator("#tracking-episode-progress").inner_text() == "4 of 12"
        assert page.locator("#tracking-anchor-title").inner_text() == "Starting climb"
        assert page.locator("#tracking-forecast-title").inner_text() == "Regression leans upward"
        assert page.locator("#tracking-event-tape .tracking-event").count() == 12
        assert page.locator("#tracking-event-tape .tracking-event").nth(0).get_attribute("data-state") == "aligned"
        assert page.locator("#tracking-event-tape .tracking-event").nth(0).locator("span").inner_text() == "Up movement"
        assert page.locator("#tracking-event-tape .tracking-event").nth(1).get_attribute("data-state") == "opposed"
        assert page.locator("#tracking-event-tape .tracking-event").nth(1).locator("span").inner_text() == "Down movement"
        assert page.locator("#tracking-event-tape .tracking-event").nth(2).get_attribute("data-state") == "recorded"
        assert page.locator("#tracking-event-tape .tracking-event").nth(2).locator("span").inner_text() == "Rest / range"
        assert page.locator("#tracking-event-tape .tracking-event").nth(4).get_attribute("data-state") == "forming"
        assert page.locator("#tracking-event-tape .tracking-event").nth(5).locator("span").inner_text() == "Pending close"
        assert page.locator("#story-step-one-label").inner_text() == "MAJOR TREND"
        assert page.locator("#story-step-two-label").inner_text() == "INNER TREND"
        assert page.locator("#story-step-three-label").inner_text() == "REGRESSION STUDY"
        assert page.locator("#current-move-title").inner_text() == "Uptrend"
        assert page.locator("#forecast-title").inner_text() == "Upward inner move"
        assert page.locator("#permission-title").inner_text() == "History leans upward"
        assert page.locator("#history-count").inner_text() == "4 observations"
        assert page.locator("#tracking-stop").is_visible()
        assert page.locator("#tracking-reset").is_hidden()

        page.evaluate(
            "payload => window.__TRACKING_EPISODE_RESPONSES.stop.body = payload",
            completed,
        )
        page.locator("#tracking-stop").click()
        page.wait_for_function(
            "() => document.querySelector('#tracking-ribbon')?.dataset.state === 'complete'"
        )
        assert page.locator("#tracking-start").is_hidden()
        assert page.locator("#tracking-reset").is_visible()
        assert not page.locator("#tracking-reset").is_disabled()
        assert page.locator("#run-forecast").count() == 0
        assert page.locator("#history-count").inner_text() == "4 observations"
        requests = page.evaluate("window.__FETCH_REQUESTS.slice()")
        assert any(
            row["method"] == "POST"
            and row["href"].endswith("/tracking-episodes/start")
            for row in requests
        )
        assert any(
            row["method"] == "POST"
            and row["href"].endswith("/tracking-episodes/stop")
            for row in requests
        )


def test_tracking_start_uses_dedicated_readiness_and_applies_action_response(
    chromium_browser: Browser,
) -> None:
    stale_operator = _operator_payload()
    stale_operator["tracking"]["episode"] = {
        "schema_version": "PG_TRACKING_EPISODE_PUBLIC_V1",
        "episode_id": "",
        "state": "IDLE",
        "revision": 0,
        "ready": False,
        "event_horizon": 12,
        "event_cursor": 0,
    }
    active_episode = {
        "schema_version": "PG_TRACKING_EPISODE_PUBLIC_V1",
        "episode_id": "episode-readiness-authority",
        "state": "ACTIVE",
        "revision": 1,
        "ready": True,
        "event_horizon": 12,
        "event_cursor": 0,
        "started_at": 4_102_444_400.0,
        "updated_at": 4_102_444_400.0,
        "summary": "The baseline is frozen and E1 is now being observed.",
    }

    with _dashboard_page(chromium_browser, stale_operator) as page:
        start = page.locator("#tracking-start")
        assert start.is_visible()
        assert start.is_disabled()

        page.evaluate(
            """
            () => {
              window.__TRACKING_READINESS_RESPONSE = {
                status: 200,
                body: {
                  schema_version: "PG_TRACKING_EPISODE_READINESS_PUBLIC_V1",
                  ready: true,
                  message: "The chart is ready. Start Tracking when you want to anchor the 12-event study.",
                  reasons: [],
                  event_horizon: 12,
                  current: {state: "IDLE", event_cursor: 0, event_horizon: 12},
                },
              };
              return window.PhoenixGuardDashboard.refresh();
            }
            """
        )
        page.wait_for_function(
            "() => document.querySelector('#tracking-start')?.disabled === false"
        )
        page.evaluate(
            "payload => window.__TRACKING_EPISODE_RESPONSES.start.body = payload",
            active_episode,
        )
        start.click()
        page.wait_for_function(
            "() => document.querySelector('#tracking-stop')?.hidden === false"
        )

        assert start.is_hidden()
        assert page.locator("#tracking-stop").is_visible()
        assert page.locator("#tracking-reset").is_hidden()
        assert page.locator("#tracking-episode-progress").inner_text() == "0 of 12"
        assert page.locator("#tracking-episode-state").inner_text() == "TRACKING"
        requests = page.evaluate("window.__FETCH_REQUESTS.slice()")
        assert any(
            row["method"] == "GET"
            and row["href"].endswith("/tracking-episodes/readiness")
            for row in requests
        )
        assert any(
            row["method"] == "POST"
            and row["href"].endswith("/tracking-episodes/start")
            for row in requests
        )


def test_completed_tracking_episode_resets_once_before_a_new_start(
    chromium_browser: Browser,
) -> None:
    completed = _operator_payload()
    events = [
        {
            "event_id": f"completed-reset-e{step}",
            "event_index": step,
            "observed_at": 4_102_444_500.0 + step,
            "direction": "BUY" if step % 2 else "SELL",
            "predicted_direction": "BUY",
            "agreement": bool(step % 2),
            "state": "historical",
            "summary": f"E{step} remained saved in the completed study.",
        }
        for step in range(1, 13)
    ]
    completed["history"] = copy.deepcopy(events)
    completed["tracking"]["episode"] = {
        "episode_id": "episode-completed-reset",
        "state": "COMPLETED",
        "ready": False,
        "event_horizon": 12,
        "event_cursor": 12,
        "progress": {"completed": 12, "total": 12},
        "events": copy.deepcopy(events),
        "future_blocks": copy.deepcopy(_composite_forecast_geometry()[1]),
        "summary": "All 12 events are complete and the study is saved.",
    }

    reset = copy.deepcopy(completed)
    reset["revision"] = 43
    reset["freshness"]["observed_at"] += 1
    reset["tracking"]["episode"] = {
        "episode_id": "",
        "state": "IDLE",
        "ready": False,
        "event_horizon": 12,
        "event_cursor": 0,
        "progress": {"completed": 0, "total": 12},
        "events": [],
        "future_blocks": [],
        "summary": "Preparing the latest completed candle for a new study.",
    }
    ready = copy.deepcopy(reset)
    ready["revision"] = 44
    ready["freshness"]["observed_at"] += 1
    ready["tracking"]["episode"]["ready"] = True

    with _dashboard_page(chromium_browser, completed) as page:
        reset_button = page.locator("#tracking-reset")
        start_button = page.locator("#tracking-start")
        assert reset_button.is_visible()
        assert not reset_button.is_disabled()
        assert start_button.is_hidden()
        assert page.locator("#tracking-stop").is_hidden()
        assert page.locator("#tracking-episode-progress").inner_text() == "12 of 12"
        history_before = page.locator("#history-count").inner_text()
        assert history_before == "12 observations"

        page.evaluate(
            """
            payload => {
              window.__TRACKING_EPISODE_RESPONSES.reset.body = payload;
              window.__TRACKING_EPISODE_DELAY_MS = 150;
              const button = document.querySelector('#tracking-reset');
              button.click();
              button.click();
            }
            """,
            reset,
        )
        assert reset_button.is_visible()
        assert reset_button.is_disabled()
        assert page.locator("#tracking-episode-progress").inner_text() == "12 of 12"
        assert page.locator("#history-count").inner_text() == history_before

        page.wait_for_function(
            "() => document.querySelector('#tracking-episode-progress')?.textContent === '0 of 12'"
        )
        assert reset_button.is_hidden()
        assert start_button.is_visible()
        assert start_button.is_disabled()
        assert page.locator("#tracking-episode-state").inner_text() == "PREPARING"
        assert page.locator("#history-count").inner_text() == history_before

        reset_requests = [
            row
            for row in page.evaluate("window.__FETCH_REQUESTS.slice()")
            if row["method"] == "POST"
            and row["href"].endswith("/tracking-episodes/reset")
        ]
        assert len(reset_requests) == 1

        page.evaluate("payload => window.renderOperatorState(payload)", ready)
        page.wait_for_function(
            "() => document.querySelector('#tracking-start')?.disabled === false"
        )
        assert start_button.is_visible()
        assert reset_button.is_hidden()
        assert page.locator("#history-count").inner_text() == history_before

        for offset, terminal_state in enumerate(
            ("STOPPED", "INVALIDATED", "FAILED"), start=3
        ):
            retained = copy.deepcopy(completed)
            retained["revision"] = 42 + offset
            retained["freshness"]["observed_at"] += offset
            retained["tracking"]["episode"]["state"] = terminal_state
            retained["tracking"]["episode"]["event_cursor"] = 4
            retained["tracking"]["episode"]["progress"] = {
                "completed": 4,
                "total": 12,
            }
            retained["tracking"]["episode"]["events"] = copy.deepcopy(events[:4])
            page.evaluate("payload => window.renderOperatorState(payload)", retained)
            assert reset_button.is_visible(), terminal_state
            assert not reset_button.is_disabled(), terminal_state
            assert start_button.is_hidden(), terminal_state


def test_tracking_episode_chrome_updates_before_the_next_broker_image_decodes(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["tracking"]["episode"] = {
        "state": "IDLE",
        "ready": True,
        "event_horizon": 12,
        "event_cursor": 0,
    }
    active = copy.deepcopy(initial)
    active["revision"] = 43
    active["freshness"]["observed_at"] += 1
    active["surface"].update(
        {
            "frame_id": 43,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
        }
    )
    active["tracking"]["episode"] = {
        "episode_id": "episode-image-race",
        "state": "ACTIVE",
        "ready": True,
        "event_horizon": 12,
        "event_cursor": 1,
        "started_at": 4_102_444_500.0,
        "updated_at": 4_102_444_501.0,
        "baseline": {"title": "Chosen buy entry", "direction": "BUY"},
        "future_blocks": copy.deepcopy(_composite_forecast_geometry()[1]),
        "events": [
            {
                "id": "episode-image-race-e1",
                "event_index": 1,
                "observed_at": 4_102_444_501.0,
                "direction": "BUY",
                "predicted_direction": "BUY",
                "agreement": True,
                "summary": "E1 moved up and matched the saved future block.",
            }
        ],
    }

    with _dashboard_page(
        chromium_browser,
        initial,
        delayed_artifact_frames={43: 0.25},
    ) as page:
        page.evaluate(
            "payload => window.__TRACKING_EPISODE_RESPONSES.start.body = payload",
            active,
        )
        page.locator("#tracking-start").click()
        page.wait_for_function(
            "() => document.querySelector('#tracking-episode-progress')?.textContent === '1 of 12'"
        )
        assert page.locator("#tracking-anchor-title").inner_text() == "Chosen buy entry"
        assert page.locator("#tracking-forecast-title").inner_text() == (
            "Regression leans upward"
        )
        assert page.locator("#tracking-event-tape .tracking-event").nth(0).get_attribute(
            "data-state"
        ) == "aligned"


@pytest.mark.parametrize("viewport", [(1440, 1000), (390, 844)])
@pytest.mark.skip(reason="V3 retired the dual forecast-route presentation in favor of regression tracking.")
def test_tracking_episode_compares_two_frozen_paths_without_hiding_the_alternate(
    chromium_browser: Browser,
    viewport: tuple[int, int],
) -> None:
    payload = _operator_payload()
    path_x = [round(0.20 + step * 0.052, 4) for step in range(13)]
    path_a_points = [
        [x_value, round(0.58 - step * 0.008, 4)]
        for step, x_value in enumerate(path_x)
    ]
    path_b_points = [
        [
            x_value,
            round(0.58 + (0.012 * step if step <= 3 else 0.036 - 0.010 * (step - 3)), 4),
        ]
        for step, x_value in enumerate(path_x)
    ]
    payload["tracking"]["episode"] = {
        "episode_id": "episode-two-path-live",
        "state": "ACTIVE",
        "ready": True,
        "event_horizon": 12,
        "event_cursor": 3,
        "started_at": 4_102_444_400.0,
        "baseline": {"title": "Latest completed candle", "direction": "BUY"},
        "path_comparison": {
            "schema_version": "PG_TRACKING_PATH_COMPARISON_PUBLIC_V1",
            "paths": [
                {
                    "id": "PATH_A",
                    "label": "Main forecast",
                    "direction": "BUY",
                    "summary": "A steady climb with a shallow middle pause.",
                    "points": path_a_points,
                    "steps": [
                        {"step": step, "direction": "BUY"}
                        for step in range(1, 13)
                    ],
                },
                {
                    "id": "PATH_B",
                    "label": "Alternative forecast",
                    "direction": "BUY",
                    "summary": "A deeper pullback before the climb resumes.",
                    "points": path_b_points,
                    "steps": [
                        {"step": step, "direction": "BUY"}
                        for step in range(1, 13)
                    ],
                },
            ],
            "verdict": "PATH_A",
            "favored_path_id": "PATH_A",
            "verdict_summary": "The completed candles currently fit the main route more closely.",
            "anchor": {
                "status": "CONFIRMED",
                "label": "Latest completed candle",
                "direction": "BUY",
            },
            "forming_at_start": {
                "status": "OBSERVED",
                "label": "Candle forming when tracking started",
                "direction": "BUY",
            },
            "forecast_bias": {
                "status": "DIRECTIONAL",
                "label": "Forecast bias at start",
                "summary": "The saved scene outlook leaned upward.",
                "direction": "BUY",
            },
            "entry_thesis": {
                "status": "DIRECTIONAL",
                "label": "Entry idea at start",
                "summary": "The saved idea was to wait for a lower buy entry.",
                "direction": "BUY",
            },
            "entry_location": {
                "status": "TRACKING",
                "label": "Saved entry area",
                "summary": "The saved buy area remains below the starting close.",
                "direction": "BUY",
                "preferred_location": "lower price",
                "top_level": 0.548,
                "bottom_level": 0.566,
                "progress": {"status": "INSIDE", "distance": 0.0},
            },
            "trade_permission": {
                "status": "WAIT",
                "label": "Entry permission at start",
                "summary": "The starting chart did not permit a trade.",
            },
            "continuity": {
                "state": "LIVE",
                "summary": "The live candle sequence remains continuous.",
            },
        },
        "events": [
            {
                "event_id": "two-path-e1",
                "event_index": 1,
                "direction": "BUY",
                "observed_at": 4_102_444_510.0,
                "observed_close_level": 0.566,
                "favored_path_id": "PATH_A",
                "entry_location_progress": {"status": "INSIDE", "distance": 0.0},
                "path_fit_by_id": {
                    "PATH_A": {"status": "MEASURED", "direction_agreement": True},
                    "PATH_B": {"status": "MEASURED", "direction_agreement": True},
                },
                "summary": "E1 closed upward and currently favors Path A.",
            },
            {
                "event_id": "two-path-e2",
                "event_index": 2,
                "direction": "BUY",
                "observed_at": 4_102_444_520.0,
                "observed_close_level": 0.558,
                "favored_path_id": "PATH_A",
                "entry_location_progress": {"status": "INSIDE", "distance": 0.0},
                "path_fit_by_id": {
                    "PATH_A": {"status": "MEASURED", "direction_agreement": True},
                    "PATH_B": {"status": "MEASURED", "direction_agreement": False},
                },
                "summary": "E2 closed upward and currently favors Path A.",
            },
            {
                "event_id": "two-path-e3",
                "event_index": 3,
                "direction": "SELL",
                "observed_at": 4_102_444_530.0,
                "observed_close_level": 0.562,
                "favored_path_id": "",
                "entry_location_progress": {"status": "INSIDE", "distance": 0.0},
                "path_fit_by_id": {
                    "PATH_A": {"status": "MEASURED", "direction_agreement": False},
                    "PATH_B": {"status": "MEASURED", "direction_agreement": True},
                },
                "summary": "E3 closed downward; the full route verdict remains Path A.",
            },
        ],
    }

    with _dashboard_page(chromium_browser, payload, viewport=viewport) as page:
        path_a = page.locator("#tracking-path-a")
        path_b = page.locator("#tracking-path-b")
        assert path_a.is_visible()
        assert path_b.is_visible()
        assert page.locator("#tracking-forecast-title").inner_text() == "Path A favored"
        assert page.locator("#tracking-path-a-title").inner_text() == "Main forecast"
        assert page.locator("#tracking-path-b-title").inner_text() == "Alternative forecast"
        rendered_path_a = page.locator("#tracking-route-path-a").get_attribute("points") or ""
        rendered_path_b = page.locator("#tracking-route-path-b").get_attribute("points") or ""
        assert len(rendered_path_a.split()) == 13
        assert len(rendered_path_b.split()) == 13
        assert rendered_path_a != rendered_path_b
        assert rendered_path_a.split()[0] == rendered_path_b.split()[0]
        anchor_point = rendered_path_a.split()[0].split(",")
        assert page.locator("#tracking-route-anchor").get_attribute("cx") == anchor_point[0]
        assert page.locator("#tracking-route-anchor").get_attribute("cy") == anchor_point[1]
        assert page.locator("#tracking-route-axis span").count() == 12
        assert all(
            page.locator("#tracking-route-axis span").nth(index).get_attribute("style")
            for index in range(12)
        )
        assert page.locator('#tracking-route-nodes [data-path-id="PATH_A"]').count() == 12
        assert page.locator('#tracking-route-nodes [data-path-id="PATH_B"]').count() == 12
        observed_before = page.locator("#tracking-route-observed").get_attribute("points") or ""
        assert len(observed_before.split()) == 4
        assert page.locator("#tracking-observed-nodes .tracking-observed-node").count() == 3
        assert page.locator("#tracking-entry-band").is_visible()
        assert page.locator("#tracking-path-a-status").inner_text() == "Favored now"
        assert page.locator("#tracking-path-b-status").inner_text() == "Still tracked"
        assert page.locator("#tracking-entry-title").inner_text() == "Buy idea frozen at start"
        assert "E1 is the candle that was live when Start Tracking was pressed" in page.locator(
            "#tracking-anchor-meta"
        ).inner_text()
        assert "Entry permission · Closed at start" in page.locator(
            "#tracking-entry-permission"
        ).inner_text()
        assert "latest confirmed candle is inside it" in page.locator(
            "#tracking-entry-progress"
        ).inner_text()
        assert page.locator("#tracking-event-tape .tracking-event").nth(0).locator("span").inner_text() == "Path A · up"
        assert page.locator("#tracking-event-tape .tracking-event").nth(2).locator("span").inner_text() == "Compared · down"
        assert page.locator(
            '#tracking-route-nodes [data-path-id="PATH_A"][data-step="2"]'
        ).get_attribute("data-fit") == "aligned"
        assert page.locator(
            '#tracking-route-nodes [data-path-id="PATH_B"][data-step="2"]'
        ).get_attribute("data-fit") == "opposed"

        path_b.click()
        assert path_a.is_visible()
        assert path_b.is_visible()
        assert path_a.get_attribute("aria-pressed") == "false"
        assert path_b.get_attribute("aria-pressed") == "true"
        assert page.locator("#tracking-path-comparison").get_attribute("data-focused-path") == "PATH_B"
        page.wait_for_function(
            """
            () => Number(getComputedStyle(document.querySelector('#tracking-route-path-b')).opacity)
              > Number(getComputedStyle(document.querySelector('#tracking-route-path-a')).opacity)
            """
        )
        route_opacity = page.evaluate(
            """
            () => ({
              pathA: getComputedStyle(document.querySelector('#tracking-route-path-a')).opacity,
              pathB: getComputedStyle(document.querySelector('#tracking-route-path-b')).opacity,
            })
            """
        )
        assert float(route_opacity["pathA"]) > 0
        assert float(route_opacity["pathB"]) > 0
        assert float(route_opacity["pathB"]) > float(route_opacity["pathA"])

        advanced = copy.deepcopy(payload)
        advanced["revision"] = 43
        advanced["tracking"]["episode"]["event_cursor"] = 4
        advanced["tracking"]["episode"]["events"].append(
            {
                "event_id": "two-path-e4",
                "event_index": 4,
                "direction": "BUY",
                "observed_at": 4_102_444_540.0,
                "observed_close_level": 0.551,
                "favored_path_id": "PATH_A",
                "entry_location_progress": {"status": "INSIDE", "distance": 0.0},
                "path_fit_by_id": {
                    "PATH_A": {"status": "MEASURED", "direction_agreement": True},
                    "PATH_B": {"status": "MEASURED", "direction_agreement": False},
                },
                "summary": "E4 added one confirmed point to the observed progression.",
            }
        )
        page.evaluate("value => window.renderOperatorState(value)", advanced)
        observed_after = page.locator("#tracking-route-observed").get_attribute("points") or ""
        assert len(observed_after.split()) == 5
        assert len(observed_after.split()) > len(observed_before.split())

        untrusted = copy.deepcopy(advanced)
        untrusted["revision"] = 44
        untrusted["tracking"]["episode"]["event_cursor"] = 5
        untrusted["tracking"]["episode"]["events"].append(
            {
                "event_id": "two-path-e5-untrusted",
                "event_index": 5,
                "direction": "BUY",
                "observed_at": 4_102_444_550.0,
                "observed_close_level": None,
                "favored_path_id": "",
                "entry_location_progress": {"status": "UNKNOWN", "distance": None},
                "path_fit_by_id": {
                    "PATH_A": {"status": "UNKNOWN", "direction_agreement": None},
                    "PATH_B": {"status": "UNKNOWN", "direction_agreement": None},
                },
                "summary": "E5 has no trusted transform, so no observed point is published.",
            }
        )
        page.evaluate("value => window.renderOperatorState(value)", untrusted)
        observed_untrusted = page.locator("#tracking-route-observed").get_attribute("points") or ""
        assert len(observed_untrusted.split()) == 5
        assert page.locator('#tracking-observed-nodes [data-step="5"]').count() == 0
        layout = page.evaluate(
            """
            () => ({
              documentWidth: document.documentElement.scrollWidth,
              viewportWidth: window.innerWidth,
              pathARight: document.querySelector('#tracking-path-a').getBoundingClientRect().right,
              pathBRight: document.querySelector('#tracking-path-b').getBoundingClientRect().right,
            })
            """
        )
        assert layout["documentWidth"] <= layout["viewportWidth"] + 1, (viewport, layout)
        assert layout["pathARight"] <= layout["viewportWidth"] + 1, (viewport, layout)
        assert layout["pathBRight"] <= layout["viewportWidth"] + 1, (viewport, layout)


@pytest.mark.skip(reason="V3 no longer presents route-lane verdict controls.")
def test_tracking_episode_shows_server_too_close_and_clean_restart_states(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["tracking"]["episode"] = {
        "episode_id": "episode-two-path-reacquiring",
        "state": "ACTIVE",
        "ready": True,
        "event_horizon": 12,
        "event_cursor": 1,
        "started_at": time.time() - 300,
        "baseline": {"title": "Latest completed candle", "direction": "SELL"},
        "path_comparison": {
            "schema_version": "PG_TRACKING_PATH_COMPARISON_PUBLIC_V1",
            "paths": [
                {
                    "id": path_id,
                    "label": label,
                    "direction": "SELL",
                    "summary": summary,
                    "steps": [
                        {"step": step, "direction": "SELL" if step < 7 else "BUY"}
                        for step in range(1, 13)
                    ],
                }
                for path_id, label, summary in (
                    ("PATH_A", "Main forecast", "A direct downward route."),
                    ("PATH_B", "Alternative forecast", "A pullback before moving down."),
                )
            ],
            "verdict": "TOO_CLOSE",
            "favored_path_id": "",
            "verdict_summary": "Neither frozen route has a clear fit advantage yet.",
            "entry_thesis": {
                "status": "NEUTRAL",
                "label": "Entry idea at start",
                "summary": "The starting candle did not permit a directional entry.",
                "direction": "NEUTRAL",
            },
            "continuity": {
                "state": "REACQUIRING",
                "summary": "The completed-candle sequence cannot be verified continuously.",
            },
        },
        "events": [
            {
                "event_id": "two-path-reacquiring-e1",
                "event_index": 1,
                "direction": "SELL",
                "observed_at": time.time() - 280,
                "favored_path_id": "",
                "path_fit_by_id": {
                    "PATH_A": {"status": "MEASURED", "direction_agreement": True},
                    "PATH_B": {"status": "MEASURED", "direction_agreement": True},
                },
                "summary": "E1 was measured without a favored route.",
            }
        ],
    }

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#tracking-forecast-title").inner_text() == "Too close"
        assert page.locator("#tracking-path-a-status").inner_text() == "Too close"
        assert page.locator("#tracking-path-b-status").inner_text() == "Too close"
        assert page.locator("#tracking-entry-title").inner_text() == "No directional entry idea"
        guidance = page.locator("#tracking-continuity-guidance")
        assert guidance.is_visible()
        assert "Start tracking from the latest completed candle" in guidance.inner_text()
        assert page.locator("#tracking-path-a").is_visible()
        assert page.locator("#tracking-path-b").is_visible()

        for revision, verdict, expected_title in (
            (43, "PATHS_OVERLAP", "Paths overlap"),
            (44, "NEITHER_PATH_FITS", "Neither path fits"),
            (45, "GEOMETRY_UNAVAILABLE", "Forecast routes unavailable"),
        ):
            updated = copy.deepcopy(payload)
            updated["revision"] = revision
            updated["tracking"]["episode"]["path_comparison"].update(
                {
                    "verdict": verdict,
                    "favored_path_id": "",
                    "verdict_summary": "The server published an explicit non-favored route state.",
                    "continuity": {
                        "state": "LIVE",
                        "summary": "The live sequence remains continuous.",
                    },
                }
            )
            page.evaluate("value => window.renderOperatorState(value)", updated)
            assert page.locator("#tracking-forecast-title").inner_text() == expected_title
            assert page.locator("#tracking-path-a").is_visible()
            assert page.locator("#tracking-path-b").is_visible()

        unknown = copy.deepcopy(payload)
        unknown["revision"] = 46
        unknown["tracking"]["episode"]["path_comparison"].update(
            {
                "verdict": "INTERNAL_EXPERIMENT",
                "favored_path_id": "PATH_A",
                "verdict_summary": "This value is not part of the public verdict vocabulary.",
            }
        )
        page.evaluate("value => window.renderOperatorState(value)", unknown)
        assert page.locator("#tracking-forecast-title").inner_text() == "Waiting for evidence"
        assert page.locator("#tracking-path-a").get_attribute("data-state") != "favored"
        assert page.locator("#tracking-path-b").get_attribute("data-state") != "favored"


def test_tracking_episode_exposes_reacquiring_without_inventing_completed_events(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["freshness"]["observed_at"] = time.time()
    payload["tracking"]["episode"] = {
        "episode_id": "episode-reacquiring",
        "state": "ACTIVE",
        "ready": True,
        "event_horizon": 12,
        "event_cursor": 0,
        "started_at": time.time() - 900,
        "updated_at": time.time() - 900,
        "baseline": {"title": "Chosen buy entry", "direction": "BUY"},
        "future_blocks": copy.deepcopy(_composite_forecast_geometry()[1]),
        "events": [],
    }

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#tracking-watch-title").inner_text() == "Reacquiring E1"
        assert page.locator("#tracking-watch-read").get_attribute("data-state") == (
            "reacquiring"
        )
        slots = page.locator("#tracking-event-tape .tracking-event")
        assert slots.count() == 12
        assert slots.nth(0).get_attribute("data-state") == "reacquiring"
        assert slots.nth(0).locator("span").inner_text() == "Finding close"
        assert slots.nth(1).get_attribute("data-state") == "pending"
        assert page.locator("#tracking-episode-progress").inner_text() == "0 of 12"


def test_tracking_plan_controls_render_studies_without_retired_forecast_diagnostics(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    public_sequence = cast(
        dict[str, Any],
        next(
            overlay
            for overlay in payload["overlays"]
            if overlay["family"] == "lstm"
        ),
    )
    public_sequence["line_points"] = []
    public_sequence["points"] = []
    public_sequence["forecast_scenarios"] = []
    public_sequence["forecast_band_points"] = []
    public_sequence["geometry_kind"] = "future_blocks"
    payload["overlays"].append(
        {
            "id": "internal-transform-debug",
            "type": "debug",
            "side": "HOLD",
            "group": "structure",
            "family": "major_swings",
            "layer": "diagnostics",
            "kind": "transform_debug",
            "kind_label": "Internal transform debug",
            "label": "Internal transform debug",
            "bounds": [0.2, 0.2, 0.4, 0.4],
            "points": [],
            "line_points": [],
            "confidence": 1.0,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
        }
    )

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#mode-raw").click()
        page.locator("#tracking-plan-toggle").click()
        assert (
            page.locator("#tracking-plan-toggle").get_attribute("aria-expanded")
            == "true"
        )
        assert page.locator("#tracking-plan-panel").is_visible()
        assert page.locator("#mode-overlay").get_attribute("aria-pressed") == "true"
        assert int(page.locator("#tracking-plan-count").inner_text()) > 0
        for family in ("trendlines", "market_context", "history"):
            assert (
                page.locator(
                    f'[data-overlay-family="{family}"]'
                ).get_attribute("aria-pressed")
                == "true"
            )
        active_families = page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        )
        assert not {
            "two_candle",
            "scene_forecaster",
            "lstm",
            "prediction",
        }.intersection(active_families)
        assert page.locator('[data-overlay-family="lstm"]').count() == 0
        assert page.locator('polyline[data-overlay-id="support-current"]').count() == 1

        page.locator("#detailed-overlay-controls").evaluate("node => node.open = true")
        support_toggle = page.locator(
            '#detailed-overlay-list .kind-filter[data-overlay-kind="rising_support_line"]'
        )
        assert support_toggle.count() == 1
        support_toggle.click()
        assert page.locator('polyline[data-overlay-id="support-current"]').count() == 0
        assert support_toggle.get_attribute("aria-pressed") == "false"
        support_toggle.click()
        assert page.locator('polyline[data-overlay-id="support-current"]').count() == 1
        assert page.locator('[data-overlay-kind="transform_debug"]').count() == 0
        assert page.locator('[data-overlay-id="internal-transform-debug"]').count() == 0

        page.locator("#layers-all").click()
        assert page.locator(".surface-forecast-composite").count() == 0
        assert page.locator(".surface-forecast-scenario").count() == 0
        assert page.locator('[data-overlay-id="lstm-current"]').count() == 0
        assert page.locator('[data-overlay-id="two-candle-current"]').count() == 0
        assert page.locator("polyline.family-lstm").count() == 0
        public_copy = page.locator("body").inner_text().lower()
        for proprietary_term in ("smc", "liquidity", "order block", "fair value gap", "lstm"):
            assert proprietary_term not in public_copy


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_episode_locked_future_blocks_remain_at_the_original_anchor_as_price_advances(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["tracking"]["episode"].update(
        {
            "episode_id": "episode-public-1",
            "state": "ACTIVE",
            "event_cursor": 7,
        }
    )
    payload["overlays"].append(
        {
            "id": "current-candle-far-ahead",
            "type": "movement",
            "side": "BUY",
            "group": "movement",
            "family": "current_candles",
            "layer": "current_candles",
            "kind": "current_price",
            "kind_label": "Current price",
            "label": "Current price",
            "bounds": [0.14, 0.62, 0.16, 0.74],
            "points": [[0.15, 0.70]],
            "line_points": [],
            "confidence": 0.9,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
        }
    )
    sequence = next(
        overlay for overlay in payload["overlays"] if overlay["family"] == "lstm"
    )
    sequence["baseline_locked"] = True

    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        composite = page.locator(
            'g.surface-forecast-composite.block-only[data-overlay-id="lstm-current"]'
        )
        assert composite.count() == 1
        assert composite.get_attribute("data-tracking-episode") == "ANCHORED"
        assert composite.locator(".surface-forecast-event-block").count() == 12
        assert page.locator('polyline[data-overlay-id="lstm-current"]').count() == 0


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_episode_owned_sequence_renders_all_blocks_without_a_live_sequence_contributor(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    live_sequence = cast(
        dict[str, Any],
        next(
            overlay
            for overlay in payload["overlays"]
            if overlay["family"] == "lstm"
        ),
    )
    payload["overlays"] = [
        overlay for overlay in payload["overlays"] if overlay["family"] != "lstm"
    ]
    episode_blocks = copy.deepcopy(live_sequence["forecast_candles"])
    payload["tracking"]["episode"] = {
        "episode_id": "episode-owned-sequence-1",
        "state": "ACTIVE",
        "event_horizon": 12,
        "event_cursor": 0,
        "future_blocks": copy.deepcopy(episode_blocks),
    }
    episode_sequence: dict[str, Any] = {
        **copy.deepcopy(live_sequence),
        "id": "episode-owned-sequence",
        "kind": "future_blocks",
        "kind_label": "12-step future blocks",
        "layer": "future_blocks",
        "label": "Saved future blocks",
        "label_hidden": True,
        "line_points": [],
        "points": [],
        "forecast_scenarios": [],
        "forecast_band_points": [],
        "forecast_candles": episode_blocks,
        "geometry_kind": "future_blocks",
        "baseline_locked": True,
    }
    payload["overlays"].append(episode_sequence)

    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        composite = page.locator(
            'g.surface-forecast-composite.block-only[data-overlay-id="episode-owned-sequence"]'
        )

        assert composite.count() == 1
        assert composite.get_attribute("data-display-mode") == "event-blocks"
        assert composite.get_attribute("data-scenario-count") == "0"
        assert composite.get_attribute("data-tracking-episode") == "ANCHORED"
        assert composite.locator(".surface-forecast-event-block").count() == 12
        assert composite.locator(".surface-forecast-candle-body").count() == 12
        assert composite.locator(".surface-forecast-candle-wick").count() == 12
        assert page.locator('[data-overlay-family-id="lstm"]').count() == 1
        assert page.locator('polyline[data-overlay-id="episode-owned-sequence"]').count() == 0
        assert page.locator("polyline.family-lstm").count() == 0


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_retained_episode_blocks_survive_a_poll_that_omits_the_live_sequence(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    live_sequence = cast(
        dict[str, Any],
        next(
            overlay
            for overlay in payload["overlays"]
            if overlay["family"] == "lstm"
        ),
    )
    episode_blocks = copy.deepcopy(live_sequence["forecast_candles"])
    payload["overlays"] = [
        overlay for overlay in payload["overlays"] if overlay["family"] != "lstm"
    ]
    payload["tracking"]["episode"] = {
        "episode_id": "episode-retained-blocks-1",
        "state": "STOPPED",
        "event_horizon": 12,
        "event_cursor": 0,
        "future_blocks": episode_blocks,
        "baseline": {"direction": "BUY"},
    }

    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        composite = page.locator(
            'g.surface-forecast-composite.block-only'
            '[data-overlay-id="episode-outlook-episode-retained-blocks-1"]'
        )

        assert composite.count() == 1
        assert composite.get_attribute("data-display-mode") == "event-blocks"
        assert composite.get_attribute("data-tracking-episode") == "ANCHORED"
        assert composite.locator(".surface-forecast-event-block").count() == 12
        assert composite.locator(".surface-forecast-candle-body").count() == 12
        assert composite.locator(".surface-forecast-candle-wick").count() == 12
        assert page.locator("polyline.family-lstm").count() == 0


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_retained_episode_fallback_rejects_an_incomplete_block_sequence(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    live_sequence = cast(
        dict[str, Any],
        next(
            overlay
            for overlay in payload["overlays"]
            if overlay["family"] == "lstm"
        ),
    )
    payload["overlays"] = [
        overlay for overlay in payload["overlays"] if overlay["family"] != "lstm"
    ]
    payload["tracking"]["episode"] = {
        "episode_id": "episode-incomplete-blocks-1",
        "state": "STOPPED",
        "event_horizon": 12,
        "event_cursor": 0,
        "future_blocks": copy.deepcopy(live_sequence["forecast_candles"][:11]),
    }

    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        assert page.locator("g.surface-forecast-composite.block-only").count() == 0
        assert page.locator(".surface-forecast-event-block").count() == 0


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
        assert any("/v1/mobile/operator/state/v1/" in url for url in fetch_urls)
        assert any(url.endswith("/tracking-episodes/readiness") for url in fetch_urls)
        assert all(
            "/v1/mobile/operator/state/v1/" in url
            or url.endswith("/tracking-episodes/readiness")
            for url in fetch_urls
        )


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


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
            "count => window.__FETCH_REQUESTS.length === count + 2",
            arg=request_count,
        )

        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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
                "href": "/v1/mobile/window-tracker/sessions/operator-test/tracking-episodes/readiness",
                "method": "GET",
            },
            {
                "href": "/v1/mobile/operator/state/v1/operator-test?view=all",
                "method": "GET",
            }
        ]


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_same_visual_poll_updates_permission_without_rerendering_overlays(
    chromium_browser: Browser,
) -> None:
    updated = copy.deepcopy(_operator_payload())
    refreshed_message = "Wait: permission refreshed without changing chart geometry."
    cast(dict[str, Any], updated["permission"])["message"] = refreshed_message
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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

        assert page.locator("#beginner-reason").inner_text() == refreshed_message
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
                "href": "/v1/mobile/window-tracker/sessions/operator-test/tracking-episodes/readiness",
                "method": "GET",
            },
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
    del source_line
    composite = page.locator(
        '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
    )
    assert composite.count() == 1
    assert composite.get_attribute("data-display-mode") == "event-blocks"
    assert composite.get_attribute("data-scenario-count") == "3"
    assert composite.get_attribute("data-forecast-status") == status
    assert composite.get_attribute("data-event-count") == "12"
    assert composite.get_attribute("data-probability-calibration") == "UNCALIBRATED"
    assert composite.locator(":scope > .surface-forecast-scenario").count() == 0
    assert composite.locator(":scope > .surface-forecast-step-node").count() == 0
    assert composite.locator(":scope > .surface-forecast-step-label").count() == 0
    assert composite.locator(".surface-forecast-endpoint-label").count() == 0
    assert composite.locator(":scope > .surface-forecast-candle").count() == 12
    assert composite.locator(".surface-forecast-event-block").count() == 12
    assert composite.locator(".surface-forecast-candle-body").count() == 12
    assert composite.locator(".surface-forecast-candle-wick").count() == 12
    assert page.locator('polyline[data-overlay-id="lstm-current"]').count() == 0


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_no_edge_composite_forecast_renders_clean_multimodal_paths(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    source_line = next(
        row["line_points"] for row in payload["overlays"] if row.get("family") == "lstm"
    )
    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        _assert_clean_multimodal_forecast(page, source_line, status="NO_EDGE")

        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert "forecast-no-edge" in (composite.get_attribute("class") or "").split()
        assert composite.locator(".surface-forecast-band").count() == 0

        hotspot = page.locator('.surface-hotspot[data-overlay-id="lstm-current"]')
        path = page.locator('polyline[data-overlay-id="lstm-current"]')
        assert hotspot.count() == 1
        assert path.count() == 0
        assert hotspot.evaluate(
            "node => node.classList.contains('label-policy-hidden')"
        )
        assert hotspot.locator("span").evaluate(
            "node => getComputedStyle(node).opacity"
        ) == "0"
        for class_names in ((hotspot.get_attribute("class") or "").split(),):
            assert "forecast-no-edge" in class_names
            assert "buy" not in class_names
            assert "sell" not in class_names

        hotspot.click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        assert "Twelve future event blocks are anchored" in inspector_copy
        assert "not a guaranteed path or entry permission" in inspector_copy


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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
        assert path.count() == 0
        assert "forecast-authorized" not in (composite.get_attribute("class") or "").split()


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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
        page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        assert "Twelve future event blocks are anchored" in inspector_copy
        assert "not a guaranteed path or entry permission" in inspector_copy


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_missing_trade_status_never_reads_as_authorized(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="BUY_NOW")
    for row in payload["overlays"]:
        if row.get("family") != "lstm":
            continue
        row["forecast_status"] = "AUTHORIZED"
        row["forecast_authorized"] = True
        row["interval"]["calibrated"] = True
        row.pop("trade_authorization_status", None)

    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert "forecast-no-edge" in (composite.get_attribute("class") or "")
        assert composite.locator(".surface-forecast-band").count() == 0
        assert "not a guaranteed path or entry permission" in inspector_copy
        assert "AUTHORIZED" not in inspector_copy


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        row["trade_authorization_status"] = "AUTHORIZED"
        row["interval"]["calibrated"] = False

    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )

        assert composite.count() == 1
        assert composite.get_attribute("data-display-mode") == "event-blocks"
        assert composite.get_attribute("data-primary-side") == "HOLD"
        assert composite.get_attribute("data-scenario-count") == "3"
        assert composite.get_attribute("data-event-count") == "12"
        assert (
            composite.get_attribute("data-probability-calibration")
            == "UNCALIBRATED"
        )
        assert "Twelve future event blocks" in (composite.get_attribute("aria-label") or "")
        assert composite.locator(":scope > .surface-forecast-scenario").count() == 0
        assert composite.locator(".surface-forecast-band").count() == 0
        assert composite.locator(".surface-forecast-event-block").count() == 12
        assert page.locator('polyline[data-overlay-id="lstm-current"]').count() == 0
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 0
        )


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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
        assert composite.locator(".surface-forecast-event-block").count() == 12
        assert composite.locator(".surface-forecast-candle-body").count() == 12
        assert composite.locator(".surface-forecast-candle-wick").count() == 12
        assert composite.locator(".surface-forecast-step-label").count() == 0

        path = page.locator('polyline[data-overlay-id="lstm-current"]')
        assert path.count() == 0
        alternatives = composite.locator(":scope > .surface-forecast-scenario")
        assert alternatives.count() == 0
        assert composite.locator(":scope > .surface-forecast-range-label").count() == 0
        assert composite.locator(".surface-forecast-endpoint-label").count() == 0


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_forecast_legend_is_removed_while_internal_geometry_keeps_stable_hierarchy(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator('[data-overlay-view="all"]').click()

        assert page.locator("#forecast-path-legend").count() == 0

        svg_order = page.locator("#surface-line-svg > *").evaluate_all(
            "nodes => nodes.map(node => ({"
            "family: node.dataset.overlayFamilyId || '', "
            "role: node.dataset.forecastRole || '', "
            "presentation: node.dataset.pathPresentation || '', "
            "display: node.dataset.displayMode || ''"
            "}))"
        )
        scene_indices = [
            index for index, row in enumerate(svg_order) if row["family"] == "lstm"
        ]
        other_indices = [
            index for index, row in enumerate(svg_order) if row["family"] != "lstm"
        ]
        assert len(scene_indices) == 1
        assert other_indices and min(scene_indices) > max(other_indices)
        assert svg_order[-1] == {
            "family": "lstm",
            "role": "",
            "presentation": "",
            "display": "event-blocks",
        }
        assert page.evaluate("window.__FETCH_URLS.length") == request_count


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        composite = page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )

        assert composite.count() == 1
        assert composite.get_attribute("data-display-mode") == "event-blocks"
        assert composite.locator(".surface-forecast-event-block").count() == 12
        assert page.locator('polyline[data-overlay-id="lstm-current"]').count() == 0
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 0
        )


def test_forecast_studies_controls_are_absent_from_v3_operator_surface(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        assert page.locator("#forecast-action-status").count() == 0
        assert page.locator("#run-forecast").count() == 0
        assert page.locator("#show-future-path").count() == 0
        page.evaluate(
            "localStorage.setItem('phoenixguard.overlay.preset.v1', 'forecast')"
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard?.getState().revision === 42"
        )
        assert page.evaluate(
            "window.PhoenixGuardDashboard.getState().overlayView"
        ) == "live"
        assert page.evaluate(
            "localStorage.getItem('phoenixguard.overlay.preset.v1')"
        ) == "live"


@pytest.mark.skip(reason="Manual forecast controls were removed from the V3 operator surface.")
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
        # Forecast refresh must preserve the block-only LSTM contract.  This
        # fixture deliberately exposes only an uncertainty-band row, so no
        # LSTM trajectory is a valid visual result.
        assert page.locator("polyline.family-lstm").count() == 0
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


@pytest.mark.skip(reason="Manual forecast controls were removed from the V3 operator surface.")
def test_run_forecast_uses_current_composite_without_recomputing(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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


@pytest.mark.skip(reason="Manual forecast controls were removed from the V3 operator surface.")
def test_show_future_uses_current_composite_without_recomputing(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
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


@pytest.mark.parametrize(
    ("action_selector", "status_fragment"),
    [
        ("#run-forecast", "current forecast"),
        ("#show-future-path", "current future path"),
    ],
)
@pytest.mark.skip(reason="Manual forecast controls were removed from the V3 operator surface.")
def test_forecast_actions_reuse_current_scene_only_geometry_without_network(
    chromium_browser: Browser,
    action_selector: str,
    status_fragment: str,
) -> None:
    payload = _operator_payload()
    lstm = next(row for row in payload["overlays"] if row.get("id") == "lstm-current")
    scene = copy.deepcopy(lstm)
    scene.update(
        {
            "id": "scene-current",
            "family": "scene_forecaster",
            "label": "Scene forecaster events · current",
        }
    )
    payload["overlays"] = [
        row
        for row in payload["overlays"]
        if row.get("family") not in {"lstm", "prediction"}
    ]
    payload["overlays"].append(scene)

    with _dashboard_page(chromium_browser, payload) as page:
        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator(action_selector).click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert status_fragment in status
        assert "not entry permission" in status
        assert page.evaluate("window.__FETCH_URLS.length") == request_count
        assert page.locator(
            '#surface-line-svg > g.surface-forecast-composite[data-overlay-id="scene-current"]'
        ).count() == 1


@pytest.mark.skip(reason="Manual scene-forecast controls were removed from the V3 operator surface.")
def test_scene_only_geometry_has_truthful_count_and_independent_toggle(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    lstm = next(row for row in payload["overlays"] if row.get("id") == "lstm-current")
    scene = copy.deepcopy(lstm)
    scene.update(
        {
            "id": "scene-current",
            "family": "scene_forecaster",
            "label": "Scene forecaster events · current",
        }
    )
    payload["overlays"] = [
        row
        for row in payload["overlays"]
        if row.get("family") not in {"lstm", "prediction"}
    ]
    payload["overlays"].append(scene)

    with _dashboard_page(chromium_browser, payload) as page:
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        request_count = page.evaluate("window.__FETCH_URLS.length")
        assert page.locator('[data-layer-count="scene_forecaster"]').inner_text() == "1"
        assert page.locator('[data-layer-count="prediction"]').inner_text() == "0"
        assert page.locator('[data-overlay-id="scene-current"]').count() >= 1

        page.locator('[data-overlay-family="prediction"]').click()
        assert page.locator('[data-overlay-id="scene-current"]').count() >= 1
        page.evaluate(
            "() => window.PhoenixGuardDashboard.toggleFamily('scene_forecaster')"
        )
        assert page.locator('[data-overlay-id="scene-current"]').count() == 0
        assert page.evaluate("window.__FETCH_URLS.length") == request_count


@pytest.mark.skip(reason="Manual forecast controls were removed from the V3 operator surface.")
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


@pytest.mark.skip(reason="Manual forecast controls were removed from the V3 operator surface.")
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


@pytest.mark.skip(reason="Manual forecast controls were removed from the V3 operator surface.")
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

        page.locator('[data-overlay-view="market_context"]').click()
        assert smc_mark.count() == 1
        assert smc_mark.evaluate(
            "node => node.classList.contains('label-policy-hidden')"
        )


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
def test_independent_smc_scene_lstm_and_two_candle_toggles_do_not_replace_the_pool(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    lstm = next(row for row in payload["overlays"] if row.get("id") == "lstm-current")
    scene = copy.deepcopy(lstm)
    scene.update(
        {
            "id": "scene-current",
            "family": "scene_forecaster",
            "label": "Scene forecaster events · no edge",
        }
    )
    payload["overlays"].append(scene)
    with _dashboard_page(chromium_browser, payload) as page:
        page.locator('[data-overlay-view="all"]').click()
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().overlayView === 'all'"
        )
        expected = {
            "smc-order-block",
            "council-current",
            "two-candle-current",
            "lstm-current",
            "scene-current",
        }
        visible = set(
            page.locator("[data-overlay-id]").evaluate_all(
                "nodes => nodes.map(node => node.dataset.overlayId)"
            )
        )
        assert expected.issubset(visible)
        assert page.locator('[data-overlay-family="scene_forecaster"]').count() == 0
        assert page.locator('[data-overlay-family="lstm"]').count() == 0

        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator('[data-overlay-family="market_context"]').click()
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

        page.evaluate(
            "() => window.PhoenixGuardDashboard.toggleFamily('scene_forecaster')"
        )
        assert page.locator('[data-overlay-id="scene-current"]').count() == 0
        assert page.locator('[data-overlay-id="lstm-current"]').count() >= 1
        assert page.evaluate("window.__FETCH_URLS.length") == request_count

        page.evaluate("() => window.PhoenixGuardDashboard.toggleFamily('lstm')")
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

        page.locator('[data-overlay-family="market_context"]').click()
        page.evaluate(
            "() => window.PhoenixGuardDashboard.toggleFamily('scene_forecaster')"
        )
        page.evaluate("() => window.PhoenixGuardDashboard.toggleFamily('lstm')")
        assert (
            page.locator('.surface-hotspot[data-overlay-id="smc-order-block"]').count()
            == 1
        )
        assert (
            page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count()
            == 1
        )
        assert page.locator('[data-overlay-id="scene-current"]').count() >= 1


def test_order_area_modes_have_independent_always_visible_controls(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["tracking"]["episode"].update(
        {
            "state": "IDLE",
            "order_areas": {
                "status": "REFERENCE",
                "count": 2,
                "message": "Current chart reference locations are ready for study.",
            },
        }
    )
    specs = (
        ("saved-buy-limit", "lower_price_buy_area", "Lower-price buy area", "BUY", [0.48, 0.58, 0.68, 0.63]),
        # A six-thousandth chart-height stop band is deliberately thinner than
        # the old ten-pixel hotspot minimum. Its exact visible child must keep
        # the server boundary while its transparent parent supplies hit area.
        ("saved-buy-stop", "upside_break_area", "Upside break area", "BUY", [0.94, 0.005, 0.99, 0.011]),
    )
    payload["overlays"].extend(
        {
            "id": overlay_id,
            "type": "entry" if kind != "plan_failure_area" else "risk",
            "side": side,
            "group": "plan",
            "family": "order_positioning",
            "layer": "order_positioning",
            "kind": kind,
            "kind_label": label,
            "label": label,
            "label_hidden": True,
            "bounds": bounds,
            "points": [],
            "line_points": [],
            "confidence": 0.84,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
            "positioning_status": "WAITING",
            "positioning_mode": "REFERENCE",
            "positioning_basis": "Current chart structure",
            "immutable_geometry": False,
            "evidence_only": True,
        }
        for overlay_id, kind, label, side, bounds in specs
    )
    next(
        overlay
        for overlay in payload["overlays"]
        if overlay.get("id") == "saved-buy-limit"
    ).update(
        {
            "geometry_role": "FORWARD_REACTION_WINDOW",
            "reaction_window_anchor": "LATEST_COMPLETED_CANDLE",
            "source_bounds": [0.22, 0.58, 0.36, 0.63],
        }
    )

    def payload_for_mode(
        mode: str,
        revision: int,
        episode_state: str,
    ) -> dict[str, Any]:
        updated = copy.deepcopy(payload)
        updated["revision"] = revision
        updated["tracking"]["episode"]["state"] = episode_state
        updated["tracking"]["episode"]["order_areas"].update(
            {
                "status": mode,
                "count": 2 if mode == "REFERENCE" else 3,
                "message": f"{mode.title()} order locations are available.",
            }
        )
        for overlay in updated["overlays"]:
            if overlay.get("family") != "order_positioning":
                continue
            overlay["positioning_mode"] = mode
            overlay["immutable_geometry"] = mode == "FROZEN"
        if mode in {"PREVIEW", "FROZEN"}:
            plan_failure = copy.deepcopy(
                next(
                    overlay
                    for overlay in updated["overlays"]
                    if overlay.get("id") == "saved-buy-limit"
                )
            )
            plan_failure.update(
                {
                    "id": "saved-plan-failure",
                    "type": "risk",
                    "kind": "plan_failure_area",
                    "kind_label": "Plan failure area",
                    "label": "Plan failure area",
                    "bounds": [0.48, 0.70, 0.68, 0.73],
                    "positioning_status": "WAITING",
                    "positioning_basis": (
                        "Saved chart structure"
                        if mode == "FROZEN"
                        else "Verified current-chart evidence"
                    ),
                }
            )
            updated["overlays"].append(plan_failure)
        return updated

    def assert_plain_operator_copy(text: str) -> None:
        assert all(token not in text for token in ("REFERENCE", "PREVIEW", "FROZEN"))

    with _dashboard_page(chromium_browser, payload) as page:
        assert (
            page.locator('[data-layer-count="order_positioning"]').inner_text() == "2"
        )
        assert {row[0] for row in specs}.issubset(
            set(
                page.locator("[data-overlay-id]").evaluate_all(
                    "nodes => nodes.map(node => node.dataset.overlayId)"
                )
            )
        )

        expected_counts = {
            "lower_price_buy_area": "1",
            "higher_price_sell_area": "0",
            "upside_break_area": "1",
            "downside_break_area": "0",
            "plan_failure_area": "0",
        }
        for kind, count in expected_counts.items():
            control = page.locator(f'[data-overlay-kind-control="{kind}"]')
            assert control.locator("[data-order-kind-count]").inner_text() == count
            available = count != "0"
            assert control.get_attribute("data-available") == str(available).lower()
            assert control.is_disabled() is (not available)
            assert control.get_attribute("aria-disabled") == str(not available).lower()
            assert control.get_attribute("aria-pressed") == str(available).lower()
        assert page.locator("#order-area-control-status").inner_text() == (
            "2 current reaction areas are marked. Wait for price to reach a limit "
            "area; a stop area matters only while it remains ahead of price. Start "
            "Tracking saves only a verified plan, and entry permission remains separate."
        )
        assert page.locator('[data-geometry-role="SOURCE_ORIGIN"]').count() == 0
        reference_treatment = page.locator(
            '[data-overlay-id="saved-buy-limit"]'
        ).evaluate(
            """
            node => {
              const visual = node.querySelector('.order-area-visual');
              const style = getComputedStyle(visual);
              return {
                mode: node.dataset.positioningMode,
                disabled: node.disabled,
                display: style.display,
                visibility: style.visibility,
                opacity: style.opacity,
                borderStyle: style.borderStyle,
                backgroundImage: style.backgroundImage,
                labelOpacity: getComputedStyle(node.querySelector('span')).opacity,
                context: node.dataset.orderContext,
                geometryRole: node.dataset.geometryRole,
                reactionAnchor: node.dataset.reactionWindowAnchor,
                label: node.querySelector('span').textContent,
              };
            }
            """
        )
        assert reference_treatment["mode"] == "REFERENCE"
        assert reference_treatment["disabled"] is False
        assert reference_treatment["display"] != "none"
        assert reference_treatment["visibility"] == "visible"
        assert float(reference_treatment["opacity"]) >= 0.95
        assert reference_treatment["borderStyle"] == "solid"
        assert reference_treatment["backgroundImage"] != "none"
        assert reference_treatment["context"] == "current"
        assert reference_treatment["geometryRole"] == "FORWARD_REACTION_WINDOW"
        assert reference_treatment["reactionAnchor"] == "LATEST_COMPLETED_CANDLE"
        assert reference_treatment["label"] == "Buy lower · limit · current"
        assert float(reference_treatment["labelOpacity"]) >= 0.9

        def assert_pixel_geometry(
            overlay_id: str,
            expected: tuple[float, float, float, float],
        ) -> None:
            metrics = page.evaluate(
                """
                overlayId => {
                  const image = document.querySelector('#surface-raw').getBoundingClientRect();
                  const hotspot = document.querySelector(
                    `[data-overlay-id="${overlayId}"]`
                  );
                  const box = (
                    hotspot.querySelector('.order-area-visual') || hotspot
                  ).getBoundingClientRect();
                  return {
                    image: {left: image.left, top: image.top, width: image.width, height: image.height},
                    box: {left: box.left, top: box.top, width: box.width, height: box.height},
                  };
                }
                """,
                overlay_id,
            )
            image = metrics["image"]
            box = metrics["box"]
            expected_pixels = (
                image["left"] + image["width"] * expected[0],
                image["top"] + image["height"] * expected[1],
                image["width"] * expected[2],
                image["height"] * expected[3],
            )
            for actual, wanted in zip(
                (box["left"], box["top"], box["width"], box["height"]),
                expected_pixels,
                strict=True,
            ):
                assert abs(float(actual) - float(wanted)) <= 1.0

        # Full broker mode maps chart coordinates through [0.10, 0.12, 0.90, 0.92].
        assert_pixel_geometry("saved-buy-limit", (0.484, 0.584, 0.16, 0.04))
        assert_pixel_geometry("saved-buy-stop", (0.852, 0.124, 0.04, 0.0048))

        page.locator("#frame-chart").click()
        page.wait_for_function(
            "() => document.querySelector('#surface-raw').src.includes('latest-chart')"
        )
        assert_pixel_geometry("saved-buy-limit", (0.48, 0.58, 0.20, 0.05))
        assert_pixel_geometry("saved-buy-stop", (0.94, 0.005, 0.05, 0.006))
        page.evaluate(
            "() => window.resolveLabelCollisions(document.querySelector('#hotspot-layer'))"
        )
        classes = (
            page.locator('[data-overlay-id="saved-buy-stop"]').get_attribute("class")
            or ""
        ).split()
        assert "label-lane-below" in classes
        assert "label-lane-end" in classes
        page.locator('[data-overlay-id="saved-buy-stop"]').scroll_into_view_if_needed()
        hit_target = page.evaluate(
            """
            () => {
              const node = document.querySelector('[data-overlay-id="saved-buy-stop"]');
              const hitBox = node.getBoundingClientRect();
              const visual = node.querySelector('.order-area-visual').getBoundingClientRect();
              return {
                clickX: visual.left + visual.width / 2,
                clickY: visual.bottom + 6,
                visibleBottom: visual.bottom,
                hitExpansionBottom: hitBox.bottom - visual.bottom,
                hitElement: document.elementFromPoint(
                  visual.left + visual.width / 2,
                  visual.bottom + 6
                )?.closest?.('[data-overlay-id]')?.dataset?.overlayId || '',
              };
            }
            """
        )
        assert hit_target["clickY"] > hit_target["visibleBottom"]
        assert hit_target["hitExpansionBottom"] >= 9
        assert hit_target["hitElement"] == "saved-buy-stop", hit_target
        page.mouse.click(hit_target["clickX"], hit_target["clickY"])
        assert "selected" in (
            page.locator('[data-overlay-id="saved-buy-stop"]').get_attribute("class")
            or ""
        ).split()
        label_bounds = page.evaluate(
            """
            () => {
              const root = document.querySelector('#hotspot-layer').getBoundingClientRect();
              const label = document.querySelector(
                '[data-overlay-id="saved-buy-stop"] span'
              ).getBoundingClientRect();
              return {root: {top: root.top, right: root.right}, label: {
                top: label.top, right: label.right
              }};
            }
            """
        )
        assert label_bounds["label"]["top"] >= label_bounds["root"]["top"] - 1
        assert label_bounds["label"]["right"] <= label_bounds["root"]["right"] + 1

        # Returning to Full broker keeps the toggle checks below on the same
        # projection used when this operator panel first opened.
        page.locator("#frame-window").click()
        page.wait_for_function(
            "() => document.querySelector('#surface-raw').src.includes('latest-window')"
        )

        buy_limit_control = page.locator(
            '[data-overlay-kind-control="lower_price_buy_area"]'
        )
        buy_limit_control.click()
        assert buy_limit_control.get_attribute("aria-pressed") == "false"
        assert page.locator('[data-overlay-id="saved-buy-limit"]').count() == 0
        assert page.locator('[data-overlay-id="saved-buy-stop"]').count() == 1
        assert page.locator('[data-overlay-id="saved-plan-failure"]').count() == 0

        buy_limit_control.click()
        assert buy_limit_control.get_attribute("aria-pressed") == "true"
        assert page.locator('[data-overlay-id="saved-buy-limit"]').count() == 1

        page.locator('[data-overlay-family="order_positioning"]').click()
        assert buy_limit_control.get_attribute("aria-pressed") == "false"
        assert page.locator('[data-overlay-id="saved-buy-limit"]').count() == 0
        buy_limit_control.click()
        assert (
            page.locator('[data-overlay-family="order_positioning"]').get_attribute(
                "aria-pressed"
            )
            == "true"
        )
        assert buy_limit_control.get_attribute("aria-pressed") == "true"
        assert page.locator('[data-overlay-id="saved-buy-limit"]').count() == 1

        page.locator('[data-overlay-id="saved-buy-stop"]').click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        assert_plain_operator_copy(inspector_copy)
        inspector = inspector_copy.lower()
        assert "buy-stop area exists only because a completed candle confirmed" in inspector
        assert "only while still ahead of price" in inspector
        assert "do not chase after price has crossed it" in inspector
        assert "current reaction area" in inspector
        assert "anchored from the latest completed candle" in inspector
        assert "may update as the chart changes" in inspector
        assert "start tracking saves only a verified plan" in inspector
        assert "current status: under observation" in inspector
        assert "paired entry" not in inspector
        assert "reference is" not in inspector
        assert "entry permission remains separate" in inspector
        assert (
            page.locator("#inspector-confidence").inner_text()
            == "Evidence strength · 84%"
        )

        preview = payload_for_mode("PREVIEW", 43, "IDLE")
        page.evaluate("value => window.renderOperatorState(value)", preview)
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-id=\"saved-buy-limit\"]')"
            "?.dataset.positioningMode === 'PREVIEW'"
        )
        assert page.locator("#order-area-control-status").inner_text() == (
            "3 current plan areas are aligned to this chart. Wait for the marked "
            "price instead of chasing; Start Tracking freezes them; entry "
            "permission remains separate."
        )
        page.locator('[data-overlay-id="saved-plan-failure"]').click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        assert_plain_operator_copy(inspector_copy)
        inspector = inspector_copy.lower()
        assert "current plan area is aligned to present chart evidence" in inspector
        assert "remains mutable until start tracking saves its geometry" in inspector
        assert "preview is" not in inspector
        assert "entry permission remains separate" in inspector
        assert page.locator("#inspector-side").inner_text() == "Risk boundary"
        assert (
            page.locator("#inspector-confidence").inner_text()
            == "Evidence strength · 84%"
        )

        # A validated preview can coexist with different current chart
        # references before tracking. Counts and copy must preserve that
        # boundary: only preview geometry can become the saved plan.
        mixed_preview = copy.deepcopy(preview)
        mixed_preview["revision"] = 44
        reference_rows: list[dict[str, Any]] = []
        for source_id, changes in (
            (
                "saved-buy-limit",
                {
                    "id": "current-reference-sell-limit",
                    "side": "SELL",
                    "kind": "higher_price_sell_area",
                    "kind_label": "Higher-price sell area",
                    "label": "Higher-price sell area",
                    "bounds": [0.28, 0.22, 0.46, 0.28],
                },
            ),
            (
                "saved-buy-stop",
                {
                    "id": "current-reference-sell-stop",
                    "side": "SELL",
                    "kind": "downside_break_area",
                    "kind_label": "Downside break area",
                    "label": "Downside break area",
                    "bounds": [0.56, 0.78, 0.72, 0.82],
                },
            ),
        ):
            reference = copy.deepcopy(
                next(
                    overlay
                    for overlay in mixed_preview["overlays"]
                    if overlay.get("id") == source_id
                )
            )
            reference.update(changes)
            reference.update(
                {
                    "positioning_mode": "REFERENCE",
                    "positioning_status": "WAITING",
                    "positioning_basis": "Current chart structure",
                    "immutable_geometry": False,
                }
            )
            reference_rows.append(reference)
        mixed_preview["overlays"].extend(reference_rows)
        mixed_preview["tracking"]["episode"]["order_areas"]["count"] = 5
        page.evaluate("value => window.renderOperatorState(value)", mixed_preview)
        page.wait_for_function(
            "() => document.querySelectorAll('[data-positioning-mode=\"REFERENCE\"]')"
            ".length === 2"
        )
        assert page.locator('[data-layer-count="order_positioning"]').inner_text() == "5"
        for kind in (
            "lower_price_buy_area",
            "higher_price_sell_area",
            "upside_break_area",
            "downside_break_area",
            "plan_failure_area",
        ):
            assert page.locator(
                f'[data-overlay-kind-control="{kind}"] [data-order-kind-count]'
            ).inner_text() == "1"
        assert page.locator("#order-area-control-status").inner_text() == (
            "5 order locations are visible: 2 current reaction areas and 3 current "
            "plan areas. Wait for the marked price instead of chasing; Start Tracking "
            "saves only verified plan geometry, and entry permission remains separate."
        )
        assert page.locator(
            '[data-overlay-id="saved-plan-failure"]'
        ).get_attribute("data-positioning-mode") == "PREVIEW"
        assert not page.locator(
            '[data-overlay-kind="plan_failure_area"]'
        ).evaluate_all(
            "nodes => nodes.some(node => node.dataset.positioningMode === 'REFERENCE')"
        )

        page.locator('[data-overlay-view="plan"]').click()
        frozen = payload_for_mode("FROZEN", 45, "ACTIVE")
        page.evaluate("value => window.renderOperatorState(value)", frozen)
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-id=\"saved-buy-limit\"]')"
            "?.dataset.positioningMode === 'FROZEN'"
        )
        assert page.locator("#order-area-control-status").inner_text() == (
            "3 saved-start areas are shown as muted earlier context. Use Live read "
            "for the current reaction area; entry permission remains separate."
        )
        saved_treatment = page.locator(
            '[data-overlay-id="saved-buy-limit"]'
        ).evaluate(
            """
            node => ({
              context: node.dataset.orderContext,
              opacity: getComputedStyle(node.querySelector('.order-area-visual')).opacity,
              borderStyle: getComputedStyle(node.querySelector('.order-area-visual')).borderStyle,
              label: node.querySelector('span').textContent,
            })
            """
        )
        assert saved_treatment["context"] == "saved"
        assert float(saved_treatment["opacity"]) <= 0.5
        assert saved_treatment["borderStyle"] == "dashed"
        assert saved_treatment["label"] == "Buy lower · limit · saved start"
        page.locator('[data-overlay-id="saved-plan-failure"]').click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        assert_plain_operator_copy(inspector_copy)
        inspector = inspector_copy.lower()
        assert "muted area is the saved starting plan" in inspector
        assert "fixed for before-and-after study" in inspector
        assert "not the current reaction area" in inspector
        assert "frozen is" not in inspector
        assert "entry permission remains separate" in inspector
        assert page.locator("#inspector-side").inner_text() == "Risk boundary"
        assert (
            page.locator("#inspector-confidence").inner_text()
            == "Evidence strength · 84%"
        )

        # REFERENCE remains a current observational layer even while an
        # episode is active and no frozen order geometry is available.
        page.locator('[data-overlay-view="live"]').click()
        active_reference = payload_for_mode("REFERENCE", 46, "ACTIVE")
        page.evaluate("value => window.renderOperatorState(value)", active_reference)
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-id=\"saved-buy-limit\"]')"
            "?.dataset.positioningMode === 'REFERENCE'"
        )
        assert page.locator(
            '[data-overlay-id="saved-buy-limit"]'
        ).get_attribute("data-positioning-mode") == "REFERENCE"
        assert page.locator("#order-area-control-status").inner_text() == (
            "2 current reaction areas are marked. Wait for price to reach a limit "
            "area; a stop area matters only while it remains ahead of price. The saved "
            "tracking plan is unchanged, and entry permission remains separate."
        )
        assert page.locator(
            '[data-overlay-kind-control="lower_price_buy_area"]'
        ).get_attribute("aria-pressed") == "true"
        plan_failure_control = page.locator(
            '[data-overlay-kind-control="plan_failure_area"]'
        )
        assert plan_failure_control.locator(
            "[data-order-kind-count]"
        ).inner_text() == "0"
        assert plan_failure_control.is_disabled()

        # Current references can coexist with frozen plan geometry. They are
        # counted and toggled, but the copy makes the persistence boundary
        # explicit instead of implying that the saved plan moved.
        page.locator('[data-overlay-view="plan"]').click()
        mixed = payload_for_mode("FROZEN", 47, "ACTIVE")
        current_reference = copy.deepcopy(
            next(
                overlay
                for overlay in mixed["overlays"]
                if overlay.get("id") == "saved-buy-limit"
            )
        )
        current_reference.update(
            {
                "id": "current-reference-buy-limit",
                "bounds": [0.25, 0.42, 0.36, 0.47],
                "positioning_mode": "REFERENCE",
                "positioning_basis": "Current live structure",
                "immutable_geometry": False,
            }
        )
        mixed["overlays"].append(current_reference)
        mixed["tracking"]["episode"]["order_areas"]["count"] = 4
        page.evaluate("value => window.renderOperatorState(value)", mixed)
        page.wait_for_function(
            "() => document.querySelectorAll('[data-overlay-id=\"current-reference-buy-limit\"]')"
            ".length === 1"
        )
        assert page.locator('[data-overlay-id="current-reference-buy-limit"]').count() == 1
        assert page.locator(
            '[data-overlay-kind-control="lower_price_buy_area"] [data-order-kind-count]'
        ).inner_text() == "2"
        assert page.locator("#order-area-control-status").inner_text() == (
            "4 order locations are visible: 1 current reaction area and 3 muted "
            "saved-start areas. React only at a current area; saved-start context "
            "and entry permission remain separate."
        )
        page.locator('[data-overlay-id="current-reference-buy-limit"]').click()
        inspector_copy = page.locator("#inspector-explanation").inner_text()
        assert_plain_operator_copy(inspector_copy)
        inspector = inspector_copy.lower()
        assert "current reaction area" in inspector
        assert "anchored from the latest completed candle" in inspector
        assert "may update with current evidence during active tracking" in inspector
        assert "saved starting plan remains unchanged" in inspector
        assert "entry permission remains separate" in inspector

        page.locator('[data-overlay-view="history"]').click()
        assert page.locator('[data-overlay-id="current-reference-buy-limit"]').count() == 0
        assert page.locator(
            '.surface-hotspot.family-order-positioning.order-context-saved'
        ).count() == 3
        origin = page.locator('[data-geometry-role="SOURCE_ORIGIN"]')
        assert origin.count() == 1
        origin_treatment = origin.evaluate(
            """
            node => ({
              context: node.dataset.orderContext,
              borderStyle: getComputedStyle(node.querySelector('.order-area-visual')).borderStyle,
              opacity: getComputedStyle(node.querySelector('.order-area-visual')).opacity,
              label: node.querySelector('span').textContent,
            })
            """
        )
        assert origin_treatment["context"] == "history"
        assert origin_treatment["borderStyle"] == "dotted"
        assert float(origin_treatment["opacity"]) <= 0.3
        assert origin_treatment["label"] == "Buy lower · limit · earlier"
        page.locator('[data-overlay-view="live"]').click()
        assert page.locator('[data-overlay-id="current-reference-buy-limit"]').count() == 1
        assert page.locator(
            '.surface-hotspot.family-order-positioning.order-context-saved'
        ).count() == 0
        assert page.locator('[data-geometry-role="SOURCE_ORIGIN"]').count() == 0


def test_order_area_controls_remain_atomic_while_the_next_image_decodes(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["tracking"]["episode"].update(
        {
            "state": "IDLE",
            "order_areas": {
                "status": "REFERENCE",
                "count": 1,
                "message": "One current reaction area is available.",
            },
        }
    )
    initial["overlays"].append(
        {
            "id": "current-buy-limit-frame-42",
            "type": "entry",
            "side": "BUY",
            "group": "plan",
            "family": "order_positioning",
            "layer": "order_positioning",
            "kind": "lower_price_buy_area",
            "kind_label": "Lower-price buy area",
            "label": "Lower-price buy area",
            "label_hidden": True,
            "bounds": [0.68, 0.56, 0.82, 0.60],
            "points": [],
            "line_points": [],
            "confidence": 0.88,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
            "positioning_status": "WAITING",
            "positioning_mode": "REFERENCE",
            "positioning_basis": "Current chart structure",
            "immutable_geometry": False,
            "evidence_only": True,
        }
    )
    next_frame = copy.deepcopy(initial)
    next_frame["revision"] = 43
    next_frame["freshness"]["observed_at"] += 1
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
        if overlay.get("id") == "current-buy-limit-frame-42":
            overlay.update(
                {
                    "id": "current-sell-limit-frame-43",
                    "side": "SELL",
                    "kind": "higher_price_sell_area",
                    "kind_label": "Higher-price sell area",
                    "label": "Higher-price sell area",
                    "bounds": [0.70, 0.31, 0.86, 0.35],
                }
            )

    with _dashboard_page(
        chromium_browser,
        initial,
        delayed_artifact_frames={43: 0.4},
    ) as page:
        assert page.locator(
            '[data-overlay-kind-control="lower_price_buy_area"] [data-order-kind-count]'
        ).inner_text() == "1"
        transition = page.evaluate(
            """
            payload => {
              window.renderOperatorState(payload);
              return {
                busy: document.querySelector('#surface-canvas').getAttribute('aria-busy'),
                buyCount: document.querySelector(
                  '[data-overlay-kind-control="lower_price_buy_area"] [data-order-kind-count]'
                ).textContent,
                buyDisabled: document.querySelector(
                  '[data-overlay-kind-control="lower_price_buy_area"]'
                ).disabled,
                sellCount: document.querySelector(
                  '[data-overlay-kind-control="higher_price_sell_area"] [data-order-kind-count]'
                ).textContent,
                oldVisible: document.querySelector(
                  '[data-overlay-id="current-buy-limit-frame-42"]'
                ) !== null,
                newVisible: document.querySelector(
                  '[data-overlay-id="current-sell-limit-frame-43"]'
                ) !== null,
              };
            }
            """,
            next_frame,
        )
        assert transition == {
            "busy": "true",
            "buyCount": "1",
            "buyDisabled": False,
            "sellCount": "0",
            "oldVisible": True,
            "newVisible": False,
        }
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-id=\"current-sell-limit-frame-43\"]') !== null",
            timeout=10_000,
        )
        assert page.locator(
            '[data-overlay-kind-control="lower_price_buy_area"] [data-order-kind-count]'
        ).inner_text() == "0"
        assert page.locator(
            '[data-overlay-kind-control="higher_price_sell_area"] [data-order-kind-count]'
        ).inner_text() == "1"
        assert page.locator('[data-overlay-id="current-buy-limit-frame-42"]').count() == 0


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
        }.issubset(ids)
        assert "two-candle-current" not in ids
        assert "lstm-current" not in ids
        assert page.locator(".surface-forecast-composite").count() == 0
        assert page.locator(".surface-forecast-scenario").count() == 0
        assert "broker" not in " ".join(ids).lower()
        assert "diagnostic" not in " ".join(ids).lower()


def test_custom_overlay_mix_survives_reload_without_network_refetch_per_toggle(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator('[data-overlay-family="market_context"]').click()
        page.locator('[data-overlay-family="history"]').click()
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
            page.locator('[data-overlay-family="market_context"]').get_attribute("aria-pressed")
            == "false"
        )
        restored_families = page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        )
        assert "lstm" not in restored_families
        assert "two_candle" not in restored_families
        assert "history" not in restored_families


def test_scene_split_migration_runs_once_and_preserves_independent_reload_choice(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.evaluate(
            """
            () => {
              localStorage.setItem('phoenixguard.overlay.layers.v1', JSON.stringify(['lstm']));
              localStorage.setItem('phoenixguard.overlay.preset.v1', 'custom');
              localStorage.removeItem('phoenixguard.overlay.layers.scene-split-migration.v1');
            }
            """
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "expected => window.PhoenixGuardDashboard?.getState().revision === expected",
            arg=42,
        )
        migrated_families = page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        )
        assert not {
            "two_candle",
            "scene_forecaster",
            "lstm",
            "prediction",
        }.intersection(migrated_families)
        assert page.evaluate(
            "localStorage.getItem('phoenixguard.overlay.layers.scene-split-migration.v1')"
        ) == "1"

        stored_families = page.evaluate(
            "JSON.parse(localStorage.getItem('phoenixguard.overlay.layers.v1') || '[]')"
        )
        assert not {
            "two_candle",
            "scene_forecaster",
            "lstm",
            "prediction",
        }.intersection(stored_families)


def test_existing_live_preset_migrates_order_positioning_without_touching_custom_choice(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.evaluate(
            """
            () => {
              localStorage.setItem(
                'phoenixguard.overlay.layers.v1',
                JSON.stringify(['current_candles', 'market_context', 'council'])
              );
              localStorage.setItem('phoenixguard.overlay.preset.v1', 'live');
              localStorage.removeItem('phoenixguard.overlay.layers.order-positioning-migration.v1');
            }
            """
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "expected => window.PhoenixGuardDashboard?.getState().revision === expected",
            arg=42,
        )

        assert "order_positioning" in page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        )
        assert page.evaluate(
            "localStorage.getItem('phoenixguard.overlay.layers.order-positioning-migration.v1')"
        ) == "1"

        page.evaluate(
            """
            () => {
              localStorage.setItem('phoenixguard.overlay.layers.v1', JSON.stringify(['trendlines']));
              localStorage.setItem('phoenixguard.overlay.preset.v1', 'custom');
              localStorage.removeItem('phoenixguard.overlay.layers.order-positioning-migration.v1');
            }
            """
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "expected => window.PhoenixGuardDashboard?.getState().revision === expected",
            arg=42,
        )
        assert page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        ) == ["trendlines"]
        assert page.evaluate(
            "localStorage.getItem('phoenixguard.overlay.layers.order-positioning-migration.v1')"
        ) == "1"
        page.evaluate(
            "() => window.PhoenixGuardDashboard.toggleFamily('scene_forecaster')"
        )
        active_families = page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        )
        assert active_families == ["trendlines"]
        assert "lstm" not in active_families

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "expected => window.PhoenixGuardDashboard?.getState().revision === expected",
            arg=42,
        )
        assert page.evaluate(
            "window.PhoenixGuardDashboard.getState().activeFamilies"
        ) == ["trendlines"]


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
        page.evaluate("() => window.PhoenixGuardDashboard.setView('live')")
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


def test_reused_render_key_replaces_a_detached_node_when_svg_type_changes(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial_support = next(
        row for row in initial["overlays"] if row["id"] == "support-current"
    )
    initial_support["forecast_role"] = "band_90"

    updated = copy.deepcopy(initial)
    updated["revision"] = 43
    updated["freshness"]["observed_at"] += 1
    for overlay in updated["overlays"]:
        if overlay["id"] == "support-current":
            overlay["forecast_role"] = "upper_90"

    with _dashboard_page(chromium_browser, initial) as page:
        page.locator("#layers-all").click()
        selector = '#surface-line-svg [data-overlay-id="support-current"]'
        page.wait_for_function(
            "selector => document.querySelector(selector)?.localName === 'polygon'",
            arg=selector,
        )
        page.evaluate(
            """
            () => {
              window.__oldTypedLineNode = document.querySelector(
                '#surface-line-svg [data-overlay-id="support-current"]'
              );
              window.PhoenixGuardDashboard.toggleFamily('trendlines');
            }
            """
        )
        assert page.locator(selector).count() == 0

        page.evaluate("payload => window.renderOperatorState(payload)", updated)
        page.evaluate("() => window.PhoenixGuardDashboard.toggleFamily('trendlines')")
        page.wait_for_function(
            "selector => document.querySelector(selector)?.localName === 'polyline'",
            arg=selector,
        )

        assert page.locator(selector).count() == 1
        assert page.locator(selector).evaluate("node => node.localName") == "polyline"
        assert page.evaluate("() => window.__oldTypedLineNode.isConnected") is False
        page.evaluate("() => window.PhoenixGuardDashboard.toggleFamily('trendlines')")
        page.evaluate("() => window.PhoenixGuardDashboard.toggleFamily('trendlines')")
        assert page.locator(selector).count() == 1
        assert page.locator(selector).evaluate("node => node.localName") == "polyline"


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
            '[data-overlay-family="market_context"]',
            '[data-overlay-family="major_swings"]',
            '[data-overlay-family="local_swings"]',
            '[data-overlay-family="history"]',
        ):
            control = page.locator(selector)
            control.scroll_into_view_if_needed()
            assert control.is_visible()
            box = control.bounding_box()
            assert box is not None and box["height"] >= 40, (viewport, selector, box)
        assert page.locator('[data-overlay-family="lstm"]').count() == 0
        assert page.locator('[data-overlay-family="scene_forecaster"]').count() == 0
        assert page.locator("#run-forecast").count() == 0
        assert page.locator("#show-future-path").count() == 0
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth + 1"
        )


def test_ended_sell_pressure_and_current_up_move_keep_entry_closed_and_study_uptrend(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(action="WAIT")) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "CLOSED"
        assert page.locator("#current-move-title").inner_text() == "Uptrend"
        assert "rising" in page.locator("#beginner-now-read").inner_text().lower()
        pressure = page.locator("#pressure-event")
        assert pressure.get_attribute("data-state") == "ended"
        pressure_text = pressure.inner_text().lower()
        assert "ended" in pressure_text
        assert "current sell pressure" not in pressure_text
        assert "sell" not in page.locator("#current-move-title").inner_text().lower()
        assert "sell" not in page.locator("#beginner-now-read").inner_text().lower()
        assert page.locator(".history-item").count() == 2


def test_retracement_evidence_shows_current_and_full_pair_support_without_permission(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT")
    payload["tracking"]["market_study_v3"] = {
        "retracement_study": {
            "schema_version": "PG_MARKET_RETRACEMENT_STUDY_V3",
            "status": "STUDIED",
            "study_only": True,
            "observation_only": True,
            "execution_authority": False,
            "can_grant_entry_permission": False,
            "levels": [
                {
                    "level_id": "OTE_70_5",
                    "graph_support": 2,
                    "pair_dna_support": 14,
                },
                {
                    "level_id": "CUSTOM_71_8",
                    "graph_support": 1,
                    "pair_dna_support": 9,
                },
            ],
        }
    }
    updated = copy.deepcopy(payload)
    updated["revision"] = 43
    updated["freshness"]["observed_at"] += 1
    updated_levels = updated["tracking"]["market_study_v3"][
        "retracement_study"
    ]["levels"]
    updated_levels[0]["graph_support"] = 3
    updated_levels[0]["pair_dna_support"] = 15

    with _dashboard_page(chromium_browser, payload) as page:
        summary = page.locator(".evidence-details summary")
        summary.click()
        evidence = page.locator("#retracement-evidence")
        assert evidence.is_visible()
        assert evidence.inner_text() == (
            "Retracement evidence: 70.5% OTE reference — current graph 2, "
            "full Pair DNA 14; 71.8% experimental/nonstandard — current graph 1, "
            "full Pair DNA 9. Observation only; never entry permission."
        )
        assert page.locator("#beginner-decision-title").inner_text() == "CLOSED"
        assert page.locator("#beginner-confidence").inner_text() == (
            "No entry permission"
        )

        page.evaluate("payload => window.renderOperatorState(payload)", updated)
        page.wait_for_function(
            "() => document.querySelector('#retracement-evidence')?.textContent.includes('current graph 3, full Pair DNA 15')"
        )
        assert "current graph 3, full Pair DNA 15" in evidence.inner_text()
        assert page.evaluate(
            "() => document.activeElement === document.querySelector('.evidence-details summary')"
        )
        assert page.locator("#beginner-decision-title").inner_text() == "CLOSED"

        page.evaluate(
            "() => window.renderUnavailableState(new Error('Workspace offline'))"
        )
        assert evidence.inner_text() == (
            "Retracement evidence: unavailable while the live workspace is offline. "
            "Observation only; never entry permission."
        )


def test_zero_retracement_support_waits_for_history_and_stays_observation_only(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT")
    payload["tracking"]["market_study_v3"] = {
        "retracement_study": {
            "schema_version": "PG_MARKET_RETRACEMENT_STUDY_V3",
            "status": "NO_PROVEN_COMPLETED_SWINGS",
            "study_only": True,
            "observation_only": True,
            "execution_authority": False,
            "can_grant_entry_permission": False,
            "levels": [
                {
                    "level_id": "OTE_70_5",
                    "graph_support": 0,
                    "pair_dna_support": 0,
                },
                {
                    "level_id": "CUSTOM_71_8",
                    "graph_support": 0,
                    "pair_dna_support": 0,
                },
            ],
        }
    }

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator(".evidence-details summary").click()
        assert page.locator("#retracement-evidence").inner_text() == (
            "Retracement evidence: awaiting completed graph and Pair DNA history "
            "for the 70.5% OTE reference and the 71.8% experimental, nonstandard "
            "level. Observation only; never entry permission."
        )
        assert page.locator("#beginner-decision-title").inner_text() == "CLOSED"


def test_partial_retracement_dto_does_not_claim_unknown_full_support_is_zero(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT")
    payload["tracking"]["market_study_v3"] = {
        "retracement_study": {
            "schema_version": "PG_MARKET_RETRACEMENT_STUDY_V3",
            "status": "STUDIED_TRUNCATED",
            "study_only": True,
            "observation_only": True,
            "execution_authority": False,
            "can_grant_entry_permission": False,
            "levels": [
                {
                    "level_id": "OTE_70_5",
                    "graph_support": 4,
                },
                {
                    "level_id": "CUSTOM_71_8",
                    "graph_support": 0,
                    "visible_partition_support": 8,
                },
            ],
        }
    }

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator(".evidence-details summary").click()
        assert page.locator("#retracement-evidence").inner_text() == (
            "Retracement evidence: 70.5% OTE reference — current graph 4, "
            "full Pair DNA unavailable; 71.8% experimental/nonstandard — "
            "current graph 0, full Pair DNA unavailable. Observation only; "
            "never entry permission."
        )


def test_fresh_explicit_buy_permission_renders_buy_now(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(action="BUY_NOW")) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "BUY NOW"
        assert page.locator("#permission-title").inner_text() == "History leans upward"
        assert "Buy low · entry open" in (
            page.locator("#beginner-evidence-safety").text_content() or ""
        )
        assert (
            page.locator("#beginner-confidence").inner_text()
            == "About 12 minutes remaining"
        )
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "lower price" in instruction
        assert "verified demand or retest area" in instruction
        assert "do not chase highs" in instruction
        assert "latest sequence" in page.locator("#beginner-entry-read").inner_text().lower()
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
        assert page.locator("#permission-title").inner_text() == "History leans upward"
        assert "Sell high · entry open" in (
            page.locator("#beginner-evidence-safety").text_content() or ""
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


def test_open_setup_wait_keeps_entry_closed_while_permission_refreshes(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT", window_open=True)
    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "CLOSED"
        assert page.locator("#permission-title").inner_text() == "History leans upward"
        assert (
            page.locator("#beginner-confidence").inner_text()
            == "Permission refreshing"
        )
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert instruction == "no trade entry is open."
        reason = page.locator("#beginner-reason").inner_text().lower()
        assert "setup window remains open" in reason
        assert "current-frame permission is refreshing" in reason
        assert "wait for current-frame permission" in page.locator(
            "#beginner-next-condition"
        ).inner_text().lower()
        assert "Setup window · verifying" in (
            page.locator("#beginner-evidence-safety").text_content() or ""
        )


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
        page.get_by_role("button", name="Combined analysis", exact=True).click()

        content = page.locator("#inspector-content")
        assert content.is_visible()
        assert page.locator("#inspector-title").inner_text() == "Combined analysis"
        assert page.locator("#inspector-group").inner_text() == "COMBINED ANALYSIS"
        assert (
            "current combined analysis"
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
        page.get_by_role("button", name="Combined analysis", exact=True).click()

        drawer = page.locator("#mobile-inspector")
        assert drawer.is_visible()
        assert page.locator("#mobile-inspector-title").inner_text() == "Combined analysis"
        assert (
            "current combined analysis"
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
                  return {
                    box: [
                      (box.left - image.left) / image.width,
                      (box.top - image.top) / image.height,
                      box.width / image.width,
                      box.height / image.height,
                    ],
                    forecastCount: document.querySelectorAll(
                      '.surface-forecast-composite, .surface-forecast-scenario'
                    ).length,
                    viewBox: document.querySelector('#surface-line-svg').getAttribute('viewBox'),
                  };
                }
                """
            )

        baseline = geometry()
        assert baseline["forecastCount"] == 0
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
        assert changed["forecastCount"] == 0
        assert changed["viewBox"] == baseline["viewBox"]

        page.locator("#zoom-actual").click()
        actual_size = geometry()
        page.locator("#zoom-fit").click()
        fit_again = geometry()
        for observed in (actual_size, fit_again):
            for actual, expected in zip(observed["box"], baseline["box"], strict=True):
                assert abs(float(actual) - float(expected)) <= 0.002
                assert observed["forecastCount"] == 0
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


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
              const firstBlock = document.querySelector(
                'g[data-overlay-id="lstm-current"] .surface-forecast-event-block'
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
                forecastPoints: firstBlock.getAttribute('y'),
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
              const firstBlock = document.querySelector(
                'g[data-overlay-id="lstm-current"] .surface-forecast-event-block'
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
                forecastPoints: firstBlock.getAttribute('y'),
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


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
    invalid_forecast["line_points"] = []
    invalid_forecast["forecast_scenarios"] = []
    invalid_forecast["forecast_anchor"]["y_norm"] = 0.20
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


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        page.evaluate("() => window.PhoenixGuardDashboard.setView('forecast')")
        current = page.locator(
            'g.surface-forecast-composite[data-overlay-id="lstm-current"]'
        )
        assert current.count() == 1
        assert current.get_attribute("data-anchor-state") == "CANDLE_LOCKED"
        assert current.get_attribute("data-event-count") == "12"
        assert page.locator(
            '[data-overlay-id="stale-verified-closed-forecast"]'
        ).count() == 0


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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


@pytest.mark.skip(reason="V3 retired visual forecast routes; regression study replaces them.")
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
        page.locator('.surface-hotspot[data-overlay-id="past-sell"]').click()
        assert "has-selection" in (page.locator("body").get_attribute("class") or "")
        page.evaluate("() => window.PhoenixGuardDashboard.toggleFamily('history')")
        assert "has-selection" not in (
            page.locator("body").get_attribute("class") or ""
        )
        assert page.locator("#inspector-content").is_hidden()


def test_overlay_keyboard_focus_survives_an_unrelated_family_toggle(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        target = page.locator('.surface-hotspot[data-overlay-id="demand-current"]')
        target.focus()
        page.locator('[data-overlay-family="market_context"]').click()
        page.wait_for_function(
            "() => document.activeElement?.dataset?.overlayId === 'demand-current'"
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


def test_queued_same_image_viewport_refinement_projects_the_queued_geometry(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    pending = copy.deepcopy(initial)
    pending["revision"] = 43
    pending["surface"].update(
        {
            "semantic_identity": "surface-eur-usd-m5-frame-43",
            "frame_id": 43,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "overlay_viewport": {
                "source_space": "chart",
                "target_space": "window",
                "coordinate_units": "normalized",
                "bounds": [0.10, 0.12, 0.90, 0.92],
            },
        }
    )
    for overlay in pending["overlays"]:
        overlay["frame_id"] = 43

    queued = copy.deepcopy(pending)
    queued["revision"] = 44
    queued["surface"]["overlay_viewport"]["bounds"] = [0.20, 0.22, 0.80, 0.82]

    with _dashboard_page(
        chromium_browser,
        initial,
        delayed_artifact_frames={43: 0.25},
    ) as page:
        page.locator("#layers-all").click()
        page.evaluate(
            "payloads => { window.renderOperatorState(payloads[0]); window.renderOperatorState(payloads[1]); }",
            [pending, queued],
        )
        page.wait_for_function(
            """
            () => document.querySelector('#surface-canvas').getAttribute('aria-busy') === 'false'
              && document.querySelector('#surface-raw').src.includes('frame_id=43')
            """,
            timeout=10_000,
        )
        projected = page.evaluate(
            """
            () => {
              const image = document.querySelector('#surface-raw').getBoundingClientRect();
              const box = document.querySelector(
                '[data-overlay-id="demand-current"]'
              ).getBoundingClientRect();
              return [
                (box.left - image.left) / image.width,
                (box.top - image.top) / image.height,
                box.width / image.width,
                box.height / image.height,
              ];
            }
            """
        )
        # Queued viewport [0.20, 0.22, 0.80, 0.82] applied to the demand
        # bounds [0.08, 0.18, 0.46, 0.72]. The pending viewport would yield
        # [0.164, 0.264, 0.304, 0.432] and must never be used here.
        for actual, expected in zip(
            projected,
            (0.248, 0.328, 0.228, 0.324),
            strict=True,
        ):
            assert abs(float(actual) - expected) <= 0.003


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


def test_session_history_does_not_drop_episode_rows_after_twenty_four(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["history"] = [
        {
            "id": f"episode-retained-event-{index:02d}",
            "episode_id": f"episode-retained-{index // 12:02d}",
            "event_index": index % 12 + 1,
            "observed_at": 4_102_440_000.0 + index,
            "direction": "BUY" if index % 2 == 0 else "SELL",
            "state": "HISTORICAL",
            "summary": f"Retained episode event {index + 1}.",
            "frame_id": 100 + index,
        }
        for index in range(40)
    ]

    with _dashboard_page(chromium_browser, payload) as page:
        page.wait_for_function(
            "() => document.querySelectorAll('.history-item').length === 40",
            timeout=10_000,
        )
        assert page.locator("#history-count").inner_text() == "40 observations"
        assert page.locator(
            '[data-history-id="episode-retained-event-00"]'
        ).count() == 1
        assert page.locator(
            '[data-history-id="episode-retained-event-39"]'
        ).count() == 1


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
        assert page.locator(".history-side").first.inner_text() == "REST"
        assert (
            page.locator(".history-copy").first.inner_text()
            == "Rest / range behavior was recorded while direction remained unconfirmed."
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


def test_pair_switch_supersedes_inflight_surface_and_clears_old_geometry(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["surface"]["semantic_identity"] = "surface-eur-usd-m5"

    next_eur_frame = copy.deepcopy(initial)
    next_eur_frame["revision"] = 43
    next_eur_frame["freshness"]["observed_at"] += 1
    next_eur_frame["surface"].update(
        {
            "frame_id": 43,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
        }
    )
    for overlay in next_eur_frame["overlays"]:
        overlay["frame_id"] = 43
        if overlay["id"] == "demand-current":
            overlay["id"] = "eur-demand-frame-43"

    switched = copy.deepcopy(next_eur_frame)
    switched["revision"] = 44
    switched["market"]["symbol"] = "GBP/USD"
    switched["freshness"]["observed_at"] += 1
    switched["surface"].update(
        {
            "semantic_identity": "surface-gbp-usd-m5",
            "frame_id": 44,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=44",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=44",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=44",
        }
    )
    for overlay in switched["overlays"]:
        overlay["frame_id"] = 44
        if overlay["id"] == "eur-demand-frame-43":
            overlay.update(
                {
                    # Reuse the committed old-pair semantic id deliberately.
                    # The pair namespace boundary must still create a fresh
                    # node rather than reviving its detached predecessor.
                    "id": "demand-current",
                    "bounds": [0.55, 0.20, 0.85, 0.35],
                }
            )

    with _dashboard_page(
        chromium_browser,
        initial,
        delayed_artifact_frames={43: 0.35},
    ) as page:
        page.locator("#layers-all").click()
        transition = page.evaluate(
            """
            payloads => {
              window.__oldPairOverlayNode = document.querySelector(
                '[data-overlay-id="demand-current"]'
              );
              window.renderOperatorState(payloads[0]);
              window.renderOperatorState(payloads[1]);
              const image = document.querySelector('#surface-raw');
              return {
                busy: document.querySelector('#surface-canvas').getAttribute('aria-busy'),
                source: image.src,
                oldCommittedGeometry: document.querySelector(
                  '[data-overlay-id="demand-current"]'
                ) !== null,
                supersededGeometry: document.querySelector(
                  '[data-overlay-id="eur-demand-frame-43"]'
                ) !== null,
                nextPairGeometryBeforeDecode: document.querySelector(
                  '[data-overlay-id="demand-current"]'
                ) !== null,
              };
            }
            """,
            [next_eur_frame, switched],
        )
        assert transition["busy"] == "true"
        assert "frame_id=44" in transition["source"]
        assert "surface_identity=surface-gbp-usd-m5" in transition["source"]
        assert transition["oldCommittedGeometry"] is False
        assert transition["supersededGeometry"] is False
        assert transition["nextPairGeometryBeforeDecode"] is False

        page.wait_for_function(
            "() => document.querySelector('[data-overlay-id=\"demand-current\"]') !== null",
            timeout=10_000,
        )
        assert page.locator('[data-overlay-id="eur-demand-frame-43"]').count() == 0
        assert page.evaluate(
            """
            () => document.querySelector('[data-overlay-id="demand-current"]')
              !== window.__oldPairOverlayNode
            """
        ) is True
        projected = page.evaluate(
            """
            () => {
              const image = document.querySelector('#surface-raw').getBoundingClientRect();
              const box = document.querySelector(
                '[data-overlay-id="demand-current"]'
              ).getBoundingClientRect();
              return [
                (box.left - image.left) / image.width,
                (box.top - image.top) / image.height,
                box.width / image.width,
                box.height / image.height,
              ];
            }
            """
        )
        for actual, expected in zip(projected, (0.54, 0.28, 0.24, 0.12), strict=True):
            assert abs(float(actual) - expected) <= 0.003
