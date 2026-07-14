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


def _renderable_dashboard_html() -> str:
    html = DASHBOARD_PATH.read_text(encoding="utf-8")
    return (
        html.replace("__SESSION_ID_JSON__", json.dumps("operator-test"))
        .replace("__SESSION_LABEL__", "operator-test")
        .replace("__OVERLAY_EDITOR_SETTINGS_JSON__", "{}")
        .replace("__MODEL_STRENGTH_SETTINGS_JSON__", "{}")
    )


def _operator_payload(*, action: str = "WAIT", window_open: bool = False) -> dict[str, Any]:
    observed_at = 4_102_444_500.0
    allowed = action in {"BUY_NOW", "SELL_NOW"}
    side = "SELL" if action == "SELL_NOW" else "BUY"
    setup_window_open = allowed or window_open
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
            "window_label": "Open · 12m 00s remaining" if setup_window_open else "Closed",
            "entry_location": (
                "HIGHER_PRICE" if side == "SELL" else "LOWER_PRICE"
            ) if allowed else "NONE",
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
                "side": "BUY",
                "group": "outlook",
                "family": "lstm",
                "layer": "prediction_path",
                "label": "LSTM path",
                "bounds": [0.72, 0.38, 0.94, 0.55],
                "points": [],
                "line_points": [[0.72, 0.50], [0.82, 0.44], [0.94, 0.40]],
                "confidence": 0.67,
                "lifecycle": "current",
                "frame_id": 42,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
                "forecast_role": "center",
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
) -> Generator[Page, None, None]:
    html = _renderable_dashboard_html()
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
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
            route.fulfill(status=200, content_type="image/svg+xml", body=SURFACE_IMAGE_BYTES)
        else:
            route.abort()

    page.route("http://dashboard.test/**", route_dashboard)
    payload_json = json.dumps(payload).replace("</", "<\\/")
    page.add_init_script(
        f"""
        window.__OPERATOR_PAYLOAD = {payload_json};
        window.__FETCH_URLS = [];
        window.__FETCH_REQUESTS = [];
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
        Object.defineProperty(window, "EventSource", {{value: undefined, configurable: true}});
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
            const action = window.__FORECAST_ACTION_STATUS;
            return Promise.resolve(new Response(JSON.stringify(action.body), {{
              status: Number(action.status || 200),
              headers: {{"Content-Type": "application/json"}},
            }}));
          }}
          const body = href.includes("/v1/mobile/operator/state/v1/")
            ? window.__OPERATOR_PAYLOAD
            : {{detail: "not found"}};
          return Promise.resolve(new Response(JSON.stringify(body), {{
            status: href.includes("/v1/mobile/operator/state/v1/") ? 200 : 404,
            headers: {{"Content-Type": "application/json"}},
          }}));
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


def test_dashboard_label_collision_keeps_the_higher_priority_label(chromium_browser: Browser) -> None:
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
    with _dashboard_page(chromium_browser, _operator_payload(), viewport=viewport) as page:
        assert page.title() == "808Fx Standard Hybrid System Live Tracker"
        title = page.locator(".brand-title")
        assert title.is_visible()
        assert title.inner_text() == "808Fx Standard Hybrid System"
        assert page.locator(".brand-subtitle").text_content() == "Powered by the Phoenix Guard Engine V3"

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
        page.wait_for_function("() => document.body.classList.contains('advanced-view')")
        assert page.locator("#experience-mode-toggle").inner_text() == "Simple view"
        assert page.locator("#experience-mode-toggle").get_attribute("aria-pressed") == "true"
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
            assert page.evaluate("window.PhoenixGuardDashboard.getState().overlayView") == view
            assert page.evaluate("window.__FETCH_URLS.length") == request_count

        page.locator("#experience-mode-toggle").click()
        page.wait_for_function("() => document.body.classList.contains('advanced-view')")
        zones = page.locator('[data-overlay-view="zones"]')
        request_count = page.evaluate("window.__FETCH_URLS.length")
        zones.click()
        assert zones.get_attribute("aria-pressed") == "true"
        assert page.evaluate("window.__FETCH_URLS.length") == request_count
        assert page.locator("#overlay-explorer").is_visible()


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
        assert page.evaluate("window.PhoenixGuardDashboard.getState().overlayView") == "forecast"
        assert page.evaluate("window.PhoenixGuardDashboard.getState().surfaceMode") == "overlay"

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
        assert not page.locator("#surface-canvas").evaluate("node => node.classList.contains('updating')")
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


def test_run_forecast_uses_current_path_without_recomputing(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#run-forecast").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert "current forecast" in status
        assert "not entry permission" in status
        assert page.evaluate("window.PhoenixGuardDashboard.getState().overlayView") == "forecast"
        assert not page.locator("#run-forecast").is_disabled()
        assert not any(
            row["method"] == "POST" and row["href"].endswith("/predict")
            for row in page.evaluate("window.__FETCH_REQUESTS.slice()")
        )


def test_show_future_uses_current_path_without_recomputing(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#show-future-path").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'success'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert "current future path" in status
        assert "not entry permission" in status
        assert page.evaluate("window.PhoenixGuardDashboard.getState().overlayView") == "forecast"
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


def test_show_future_reports_stale_result_without_implying_permission(
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
              window.__FORECAST_ACTION_STATUS = {
                status: 200,
                body: {
                  schema_version: 'PG_FORECAST_ACTION_V1',
                  request_id: 'forecast-action-1',
                  mode: 'future',
                  status: 'stale',
                  terminal: true,
                  is_current: false,
                  poll_after_ms: 250,
                  status_url: '/v1/mobile/window-tracker/sessions/operator-test/forecast-actions/forecast-action-1',
                },
              };
            }
            """
        )
        page.locator("#show-future-path").click()
        page.wait_for_function(
            "() => document.querySelector('#forecast-action-status')?.dataset.state === 'stale'",
            timeout=10_000,
        )

        status = page.locator("#forecast-action-status").inner_text().lower()
        assert "chart changed" in status
        assert "latest frame" in status
        assert "permission" not in status
        assert any(
            row["method"] == "POST" and row["href"].endswith("/show-future")
            for row in page.evaluate("window.__FETCH_REQUESTS.slice()")
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
        assert page.evaluate("window.PhoenixGuardDashboard.getState().forecastActionBusy") is False


def test_live_read_preserves_geometry_while_backend_label_policy_declutters_text(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        assert page.evaluate("window.PhoenixGuardDashboard.getState().overlayView") == "live"
        smc_mark = page.locator('.surface-hotspot[data-overlay-id="smc-order-block"]')
        assert smc_mark.count() == 1
        assert smc_mark.evaluate("node => node.classList.contains('label-policy-hidden')")
        assert smc_mark.locator("span").evaluate("node => getComputedStyle(node).opacity") == "0"

        page.locator('[data-overlay-view="smc"]').click()
        assert smc_mark.count() == 1
        assert smc_mark.evaluate("node => node.classList.contains('label-policy-hidden')")


def test_independent_smc_lstm_and_two_candle_toggles_do_not_replace_the_pool(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator('[data-overlay-view="all"]').click()
        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().overlayView === 'all'"
        )
        expected = {"smc-order-block", "council-current", "two-candle-current", "lstm-current"}
        visible = set(page.locator("[data-overlay-id]").evaluate_all("nodes => nodes.map(node => node.dataset.overlayId)"))
        assert expected.issubset(visible)

        request_count = page.evaluate("window.__FETCH_URLS.length")
        page.locator('[data-overlay-family="smc"]').click()
        assert page.locator('.surface-hotspot[data-overlay-id="smc-order-block"]').count() == 0
        assert page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count() == 1
        assert page.locator('.surface-hotspot[data-overlay-id="two-candle-current"]').count() == 1
        assert page.evaluate("window.__FETCH_URLS.length") == request_count

        page.locator('[data-overlay-family="lstm"]').click()
        assert page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count() == 0
        assert page.locator('.surface-hotspot[data-overlay-id="two-candle-current"]').count() == 1
        assert page.locator('.surface-hotspot[data-overlay-id="council-current"]').count() == 1
        assert page.evaluate("window.__FETCH_URLS.length") == request_count

        page.locator('[data-overlay-family="smc"]').click()
        page.locator('[data-overlay-family="lstm"]').click()
        assert page.locator('.surface-hotspot[data-overlay-id="smc-order-block"]').count() == 1
        assert page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').count() == 1


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
        ids = set(page.locator("[data-overlay-id]").evaluate_all("nodes => nodes.map(node => node.dataset.overlayId)"))
        assert {"demand-current", "support-current", "past-sell", "smc-order-block", "two-candle-current", "lstm-current"}.issubset(ids)
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
        expected_families = page.evaluate("window.PhoenixGuardDashboard.getState().activeFamilies")

        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "expected => window.PhoenixGuardDashboard?.getState().revision === expected",
            arg=42,
        )
        assert page.evaluate("window.PhoenixGuardDashboard.getState().overlayView") == "custom"
        assert page.evaluate("window.PhoenixGuardDashboard.getState().activeFamilies") == expected_families
        assert page.locator('[data-overlay-family="smc"]').get_attribute("aria-pressed") == "false"
        assert page.locator('[data-overlay-family="lstm"]').get_attribute("aria-pressed") == "false"
        assert page.locator('[data-overlay-family="two_candle"]').get_attribute("aria-pressed") == "true"


@pytest.mark.parametrize("viewport", [(390, 844), (360, 800)])
def test_mobile_overlay_library_has_tappable_controls_without_page_overflow(
    chromium_browser: Browser,
    viewport: tuple[int, int],
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(), viewport=viewport) as page:
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
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


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


def test_fresh_explicit_buy_permission_renders_buy_now(chromium_browser: Browser) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(action="BUY_NOW")) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "BUY NOW"
        assert page.locator("#permission-title").inner_text() == "Buy low · entry open"
        assert page.locator("#beginner-confidence").inner_text() == "About 12 minutes remaining"
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "lower price" in instruction
        assert "verified demand or retest area" in instruction
        assert "do not chase highs" in instruction
        entry_read = page.locator("#beginner-entry-read").inner_text().lower()
        assert "about 12 minutes remaining" in entry_read
        assert "closes early if live truth changes" in entry_read
        assert page.locator("#beginner-decision-shell").get_attribute("data-tone") == "buy"


def test_fresh_explicit_sell_permission_renders_sell_high(chromium_browser: Browser) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(action="SELL_NOW")) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "SELL NOW"
        assert page.locator("#permission-title").inner_text() == "Sell high · entry open"
        assert page.locator("#beginner-confidence").inner_text() == "About 12 minutes remaining"
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "higher price" in instruction
        assert "verified supply or retest area" in instruction
        assert "do not chase lows" in instruction
        assert page.locator("#beginner-decision-shell").get_attribute("data-tone") == "sell"


def test_open_setup_wait_renders_verifying_without_false_closed_state(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT", window_open=True)
    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "WAIT"
        assert page.locator("#permission-title").inner_text() == "Setup window · verifying"
        assert page.locator("#beginner-confidence").inner_text() == "About 12 minutes remaining"
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "setup window remains open" in instruction
        assert "current-frame permission is refreshing" in instruction
        entry_read = page.locator("#beginner-entry-read").inner_text().lower()
        assert "about 12 minutes remaining" in entry_read
        assert "do not enter until buy now or sell now returns" in entry_read
        assert "entry closed" not in page.locator("#permission-title").inner_text().lower()
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


def test_overlay_selection_opens_plain_language_inspector(chromium_browser: Browser) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#experience-mode-toggle").click()
        page.wait_for_function("() => document.body.classList.contains('advanced-view')")
        # The calm Live read intentionally excludes the broader zone library.
        # Exercise a current mark that remains visible without changing views.
        page.get_by_role("button", name="Council read").click()

        content = page.locator("#inspector-content")
        assert content.is_visible()
        assert page.locator("#inspector-title").inner_text() == "Council read"
        assert page.locator("#inspector-group").inner_text().lower() == "council"
        assert "current combined plan read" in page.locator("#inspector-explanation").inner_text().lower()
        inspector_text = content.inner_text().lower()
        for forbidden in ("packet", "schema", "backend", "telemetry", "model council", "frame id"):
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
        assert "current combined plan read" in page.locator("#mobile-inspector-copy").inner_text().lower()

        page.locator("#mobile-inspector-close").click()
        assert not drawer.is_visible()


@pytest.mark.parametrize("viewport", [(1440, 1000), (390, 844)])
def test_simple_and_explore_views_do_not_overflow_the_document(
    chromium_browser: Browser,
    viewport: tuple[int, int],
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(), viewport=viewport) as page:
        for mode in ("simple", "advanced"):
            if mode == "advanced":
                page.locator("#experience-mode-toggle").click()
                page.wait_for_function("() => document.body.classList.contains('advanced-view')")
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
            assert metrics["documentWidth"] <= metrics["viewportWidth"] + 1, (viewport, mode, metrics)
            for box in metrics["boxes"]:
                assert box["width"] > 0 and box["height"] > 0, (viewport, mode, box)
                assert box["left"] >= -1, (viewport, mode, box)
                assert box["right"] <= metrics["viewportWidth"] + 1, (viewport, mode, box)


def test_line_overlay_with_bounds_has_no_visible_capsule(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-clear").click()
        page.locator('[data-overlay-family="trendlines"]').click()
        hotspot = page.locator(
            '.surface-hotspot[data-overlay-id="support-current"]'
        )
        assert hotspot.count() == 1
        assert "line-hit" in (hotspot.get_attribute("class") or "").split()
        assert page.locator(
            'polyline[data-overlay-id="support-current"]'
        ).count() == 1
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
    with _dashboard_page(chromium_browser, _operator_payload(), viewport=(1280, 900)) as page:
        page.locator("#layers-all").click()
        assert page.locator('rect.surface-chart-bounds[data-overlay-id="chart-bounds-current"]').count() == 1
        assert page.locator('.surface-hotspot[data-overlay-id="chart-bounds-current"]').count() == 0

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
        assert scroll_after["left"] > scroll_before["left"] or scroll_after["top"] > scroll_before["top"]

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
    with _dashboard_page(chromium_browser, _operator_payload(), viewport=(1280, 900)) as page:
        page.locator("#layers-all").click()
        assert page.evaluate("() => window.PhoenixGuardDashboard.getState().frameMode") == "window"
        assert "latest-window" in (page.locator("#surface-raw").get_attribute("src") or "")
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
        for actual, expected in zip(projected, (0.164, 0.264, 0.304, 0.432), strict=True):
            assert abs(float(actual) - expected) <= 0.003

        page.locator("#frame-chart").click()
        page.wait_for_function("() => document.querySelector('#surface-raw').src.includes('latest-chart')")
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
    next(row for row in payload["overlays"] if row["id"] == "past-sell")["frame_id"] = 41
    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="past-sell"]').count() == 0


def test_hiding_a_selected_family_closes_the_stale_inspector(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator("#layers-all").click()
        page.locator('.surface-hotspot[data-overlay-id="lstm-current"]').click()
        assert "has-selection" in (page.locator("body").get_attribute("class") or "")
        page.locator('[data-overlay-family="lstm"]').click()
        assert "has-selection" not in (page.locator("body").get_attribute("class") or "")
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
    same_frame_update["permission"]["message"] = "Wait. The same visual frame is still being evaluated."

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
