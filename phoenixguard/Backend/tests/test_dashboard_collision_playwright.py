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


def _surface_image_bytes(width: int, height: int) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#090b0d"/>'
        "</svg>"
    ).encode()


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
    location_guidance = (
        "Aim for a higher price inside the verified supply or retest area; do not chase lows."
        if side == "SELL"
        else "Aim for a lower price inside the verified demand or retest area; do not chase highs."
    )
    payload: dict[str, Any] = {
        "schema_version": "PG_OPERATOR_WORKSPACE_V1",
        "session_id": "operator-test",
        "revision": 42,
        "market": {"symbol": "EUR/USD", "timeframe": "M5"},
        "tracking": {
            "active": True,
            "state": "LIVE",
            "updated_at": observed_at,
            "history_count": 2,
            "stream": {
                "enabled": True,
                "state": "RUNNING",
                "acquisition_fps": 7.875,
                "observed_frames": 12_480,
                "accepted_keyframes": 318,
                "dropped_frames": 4,
                "duplicate_frames": 2_106,
                "last_frame_epoch": observed_at,
                "last_keyframe_epoch": observed_at - 1,
                "last_reason": "Visual change accepted",
                "stream_generation": 3,
                "market_read": {
                    "schema_version": "PG_CPU_STREAM_MARKET_READ_V3",
                    "state": "MOVING",
                    "summary": "Live stream sees the chart moving now.",
                    "fresh": True,
                    "observed_at": observed_at,
                    "direction": "NEUTRAL",
                    "direction_available": False,
                    "forming_candle": True,
                    "closed_candle": False,
                    "can_grant_entry_permission": False,
                },
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
                "anchor_wick_points": [[0.14, 0.36], [0.48, 0.30]],
                "chart_bounds": [0.0, 0.0, 1.0, 1.0],
                "geometry_contract_accepted": True,
                "geometry_status": "VISIBLE_TO_LATEST_X",
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
    surface_identity = "surface-operator-test-eur-usd-m5"
    cast(dict[str, Any], payload["surface"]).update(
        {
            "semantic_identity": surface_identity,
            "market_selector_visual_fingerprint": "selector_v3_eur_usd",
        }
    )
    for overlay in cast(list[dict[str, Any]], payload["overlays"]):
        overlay.update(
            {
                "symbol": "EUR/USD",
                "timeframe": "M5",
                "market_selector_visual_fingerprint": "selector_v3_eur_usd",
                "instrument_identity_status": "LOCKED",
                "surface_semantic_identity": surface_identity,
            }
        )
    return payload


def _with_timing_forecast(
    payload: dict[str, Any],
    *,
    side: str = "BUY",
    action_state: str = "PREPARE",
    enter_now: bool = False,
) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    closed_candle_key = "operator-eurusd-m5-close-42"
    result["three_questions"] = {
        "studied_direction_current": {
            "question": "Which direction was studied, and what is being studied now?",
            "headline": f"{side} remains the completed-candle study",
            "answer": f"The latest completed regression studies {side}.",
            "state": "CURRENT",
            "side": side,
            "confidence": 0.74,
            "evidence": {
                "ensemble_studied_side": side,
                "current_regression_side": side,
                "closed_candle_key": closed_candle_key,
                "summary": "Closed-candle direction and pair identity agree.",
            },
        },
        "entry_now": {
            "question": "What is the best decision to do right now?",
            "headline": (
                f"{side} leading 3–6 completed M5 candles after the anchor close"
            ),
            "answer": "The completed-candle timing study published a bounded window.",
            "state": "ENTER_NOW" if enter_now else "FORMING",
            "side": side,
            "confidence": 0.74,
            "evidence": {"summary": "Timing and permission remain separate."},
            "enter_now": enter_now,
            "action": f"{side}_NOW" if enter_now else "DO_NOT_ENTER",
            "reason": "Rest may persist 1–3 candles before continuation.",
            "next_trigger": "Invalidate after a completed candle changes direction.",
            "timing_state": "ENTER_NOW" if enter_now else "FORMING",
            "timing_forecast": {
                "schema_version": "PG_OPERATOR_TIMING_FORECAST_V3",
                "status": "FORECAST_AVAILABLE",
                "headline": (
                    f"{side} leading 3–6 completed M5 candles after the anchor close"
                ),
                "summary": (
                    f"{side} is the leading studied path 3–6 completed M5 candles "
                    "after the anchor close."
                ),
                "side": side,
                "scope": {
                    "symbol": "EUR/USD",
                    "timeframe": "M5",
                    "closed_candle_key": closed_candle_key,
                    "identity_proven": True,
                },
                "horizon_label": (
                    "3–6 completed M5 candles after the anchor close"
                ),
                "horizon_seconds_low": 900,
                "horizon_seconds_high": 1_800,
                "horizon_candles_low": 3,
                "horizon_candles_high": 6,
                "exact_wall_clock_proven": False,
                "estimated_likelihood": 0.68,
                "estimated_likelihood_label": (
                    "68% estimated likelihood · not replay-calibrated"
                ),
                "evidence_confidence": 0.41,
                "evidence_confidence_label": "41% evidence confidence",
                "directional_model_score": 0.74,
                "directional_model_score_label": (
                    "74% directional model score · not probability"
                ),
                "calibration_grade": "C_SPARSE_PAIR",
                "calibration_label": (
                    "Evidence grade C · sparse pair history · 11 cases "
                    "· not replay-calibrated"
                ),
                "source": "PAIR",
                "source_label": "Pair history",
                "timing_evidence_label": (
                    "Pair history · 11 timing observations"
                ),
                "event_likelihood_support_count": 11,
                "support_count": 11,
                "calibrated": False,
                "rest_sweep_risk": (
                    "Rest may persist 1–3 candles. Estimated medium sweep risk is 43%."
                ),
                "invalidation": (
                    "Invalidate after a completed candle changes direction."
                ),
            },
            "operator_action": {
                "schema_version": "PG_OPERATOR_ACTION_V3",
                "state": action_state,
                "label": action_state.replace("_", " "),
                "instruction": (
                    "Enter only inside the verified window."
                    if enter_now
                    else "Prepare for the studied side; entry permission is separate."
                ),
                "enter_now": enter_now,
                "entry_permission_authorized": enter_now,
            },
        },
    }
    return result


def _make_retention_timing_admissible(
    payload: dict[str, Any],
    *,
    generated_at: float,
    valid_until: float,
) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    forecast = result["three_questions"]["entry_now"]["timing_forecast"]
    forecast.update(
        {
            "source": "PAIR",
            "timing_empirical": True,
            "timing_support_count": 11,
            "forecast_lineage_matches": True,
            "generated_at": generated_at,
            "valid_until": valid_until,
        }
    )
    return result


@pytest.fixture(scope="module")
def chromium_browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        yield browser
        browser.close()


@contextmanager
def _dashboard_page(
    browser: Browser,
    payload: dict[str, Any],
    *,
    viewport: tuple[int, int] = (1440, 1000),
    delayed_artifact_frames: dict[int, float] | None = None,
    artifact_image_bytes: dict[str, bytes] | None = None,
    with_event_source: bool = False,
    session_payload: dict[str, Any] | None = None,
    frontline: dict[str, Any] | None = None,
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
            artifact_kind = (
                "window"
                if "latest-window" in url
                else "chart"
                if "latest-chart" in url
                else "default"
            )
            route.fulfill(
                status=200,
                content_type="image/svg+xml",
                body=(artifact_image_bytes or {}).get(
                    artifact_kind,
                    SURFACE_IMAGE_BYTES,
                ),
            )
        else:
            route.abort()

    page.route("http://dashboard.test/**", route_dashboard)
    payload_json = json.dumps(payload).replace("</", "<\\/")
    session_payload_json = json.dumps(session_payload).replace("</", "<\\/")
    frontline_payload_json = json.dumps(frontline).replace("</", "<\\/")
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
        window.__SESSION_PAYLOAD = {session_payload_json};
        window.__FRONTLINE_PAYLOAD = {frontline_payload_json};
        window.__FETCH_URLS = [];
        window.__FETCH_REQUESTS = [];
        window.__FRONTEND_HEARTBEAT_REQUESTS = [];
        window.__PERFORMANCE_TRACE_PAYLOAD = {{
          frame_id: Number(window.__OPERATOR_PAYLOAD?.surface?.frame_id || 0),
          display_frame: {{
            frame_id: Number(window.__OPERATOR_PAYLOAD?.surface?.frame_id || 0),
          }},
          overlay_state: {{
            frame_id: Number(window.__OPERATOR_PAYLOAD?.surface?.frame_id || 0),
            overlay_state_version: "ovlock_4_dashboardtest",
            overlay_frame_state_version: "ov_42_4_dashboardtest",
          }},
          overlay_state_version: "ovlock_4_dashboardtest",
          overlay_frame_state_version: "ov_42_4_dashboardtest",
        }};
        window.__OPERATOR_FETCH_DELAY_MS = 0;
        {event_source_bootstrap}
        Object.defineProperty(window, "Worker", {{value: undefined, configurable: true}});
        const nativeSetTimeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => {{
          // Suppress the real dashboard's recurring two-second fallback in
          // deterministic tests; individual refreshes are triggered directly.
          if (Number(delay || 0) >= 1900) return 0;
          return nativeSetTimeout(callback, delay, ...args);
        }};
        window.fetch = (input, options = {{}}) => {{
          const href = typeof input === "string" ? input : String((input && input.url) || input || "");
          const method = String(options.method || "GET").toUpperCase();
          const isFrontendHeartbeat = href === "/v1/mobile/frontend/heartbeat/v3";
          const isPerformanceTrace = href.includes("/v1/mobile/performance/trace/v3");
          if (isFrontendHeartbeat || isPerformanceTrace) {{
            window.__FRONTEND_HEARTBEAT_REQUESTS.push({{
              href,
              method,
              body: options.body || null,
            }});
          }} else {{
            window.__FETCH_URLS.push(href);
            window.__FETCH_REQUESTS.push({{href, method}});
          }}
          const isOperatorState = href.includes("/v1/mobile/operator/state/v1/");
          const isSessionState = href === "/v1/mobile/window-tracker/sessions/operator-test"
            && window.__SESSION_PAYLOAD !== null;
          const isFrontlineLatest = href.includes("/v1/mobile/frontline/latest/");
          const hasFrontline = isFrontlineLatest && window.__FRONTLINE_PAYLOAD !== null;
          const body = isFrontendHeartbeat
            ? {{schema_version: "PG_FRONTEND_HEARTBEAT_V3", status: "ALIVE"}}
            : isPerformanceTrace
            ? window.__PERFORMANCE_TRACE_PAYLOAD
            : isOperatorState
            ? window.__OPERATOR_PAYLOAD
            : isSessionState
            ? window.__SESSION_PAYLOAD
            : isFrontlineLatest
            ? window.__FRONTLINE_PAYLOAD
            : {{detail: "not found"}};
          const respond = () => new Response(JSON.stringify(body), {{
            status: isOperatorState || isSessionState || isFrontendHeartbeat || isPerformanceTrace || hasFrontline ? 200 : 404,
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
        # The clearance panel polls its own /frontline/latest endpoint in
        # parallel; it must never create a second operator/state refresh.
        operator_requests = [
            row
            for row in requests
            if "/v1/mobile/operator/state/v1/" in row["href"]
        ]
        assert operator_requests == [
            {
                "href": "/v1/mobile/operator/state/v1/operator-test?view=all",
                "method": "GET",
            }
        ]
        assert "Live stream delivered the newest decision state." in page.locator(
            "#beginner-next-read"
        ).inner_text()


def test_rendered_dashboard_posts_bounded_frame_matched_frontend_heartbeat(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["surface"].update(
        {
            "overlay_state_version": "ovlock_4_dashboardtest",
            "overlay_frame_state_version": "ov_42_4_dashboardtest",
        }
    )
    with _dashboard_page(chromium_browser, payload) as page:
        page.wait_for_function(
            """
            () => window.__FRONTEND_HEARTBEAT_REQUESTS.some(
              request => request.method === "POST"
                && request.href === "/v1/mobile/frontend/heartbeat/v3"
            )
            """,
            timeout=10_000,
        )
        heartbeat_requests = page.evaluate(
            "window.__FRONTEND_HEARTBEAT_REQUESTS.slice()"
        )
        assert not any(
            "/v1/mobile/performance/trace/v3" in request["href"]
            for request in heartbeat_requests
        )
        post = next(
            request
            for request in reversed(heartbeat_requests)
            if request["method"] == "POST"
        )
        heartbeat = json.loads(post["body"])
        actual_visible_count = page.evaluate(
            """
            () => new Set([
              ...document.querySelectorAll(
                "#surface-line-svg > [data-overlay-id], #hotspot-layer > [data-overlay-id]"
              ),
            ].map(node => node.dataset.overlayId).filter(Boolean)).size
            """
        )

        assert heartbeat["session_id"] == "operator-test"
        assert heartbeat["surface_id"] == "dashboard"
        assert heartbeat["route"] == "live"
        assert heartbeat["overlay_mode"] == "CLEAN_LIVE"
        assert heartbeat["rendered_frame_id"] == 42
        assert heartbeat["display_frame_id"] == 42
        assert heartbeat["overlay_render_frame_id"] == 42
        assert heartbeat["overlay_state_version"] == "ovlock_4_dashboardtest"
        assert heartbeat["overlay_count"] == 4
        assert heartbeat["visible_overlay_count"] == actual_visible_count
        assert heartbeat["visible_overlay_count"] == page.evaluate(
            "window.PhoenixGuardDashboard.getState().visibleOverlayCount"
        )
        assert heartbeat["visible_overlay_count"] > 0
        assert heartbeat["frontend_loaded_ms"] > 0
        assert heartbeat["frontend_overlay_drawn_ms"] > 0
        assert heartbeat["full_broker_surface_visible"] is True
        assert page.evaluate(
            "window.PhoenixGuardDashboard.getState().heartbeatTimerActive"
        ) is True

        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        assert page.evaluate(
            "window.PhoenixGuardDashboard.getState().heartbeatTimerActive"
        ) is False


def test_frontend_heartbeat_uses_frame_matched_trace_only_as_version_fallback(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.wait_for_function(
            """
            () => window.__FRONTEND_HEARTBEAT_REQUESTS.some(
              request => request.method === "POST"
                && request.href === "/v1/mobile/frontend/heartbeat/v3"
            )
            """,
            timeout=10_000,
        )
        requests = page.evaluate(
            "window.__FRONTEND_HEARTBEAT_REQUESTS.slice()"
        )
        assert any(
            request["method"] == "GET"
            and "/v1/mobile/performance/trace/v3" in request["href"]
            for request in requests
        )
        post = next(
            request for request in reversed(requests) if request["method"] == "POST"
        )
        heartbeat = json.loads(post["body"])
        assert heartbeat["rendered_frame_id"] == 42
        assert heartbeat["overlay_state_version"] == "ovlock_4_dashboardtest"


def test_selected_source_without_first_frame_fails_loud_instead_of_checking_forever(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["overlays"] = []
    selected_at = time.time() - 90
    source = {
        "schema_version": "PG_CAPTURE_SOURCE_V3",
        "state": "VALIDATING",
        "selection_state": "LOCKED",
        "source_type": "browser_tab_roi_capture",
        "transport": "EDGE_TAB_CAPTURE",
        "display_name": "Controlled chart",
        "decision_usable": False,
        "fresh": False,
        "selected_at": selected_at,
        "updated_at": selected_at,
        "last_frame_epoch": 0,
        "last_frame_id": 0,
        "stale_after_sec": 20,
        "message": "Checking the selected chart.",
        "stream": {
            "accepted_frames": 0,
            "duplicate_frames": 0,
            "last_error": "",
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload={
            "session_id": "operator-test",
            "capture_source_v3": source,
        },
    ) as page:
        page.wait_for_function(
            "() => document.querySelector('#source-state')?.textContent.trim() === 'NO FRAMES'"
        )

        assert "received no picture" in page.locator("#source-message").inner_text()
        assert page.locator("#connection-label").inner_text() == (
            "Chart source sent no frames"
        )
        assert "No source frame received" in page.locator(
            "#overlay-library-status"
        ).inner_text()
        assert page.locator(".surface-hotspot").count() == 0
        assert page.locator("#visual-evidence-label").inner_text() == (
            "No current chart frame · Current overlays unavailable"
        )


def _live_capture_source(*, last_frame_epoch: float | None = None) -> dict[str, Any]:
    captured_at = time.time() if last_frame_epoch is None else last_frame_epoch
    payload: dict[str, Any] = {
        "schema_version": "PG_CAPTURE_SOURCE_V3",
        "state_revision": 8,
        "state": "LIVE",
        "selection_state": "IDLE",
        "source_generation": 2,
        "source_type": "browser_tab_roi_capture",
        "transport": "EDGE_TAB_CAPTURE",
        "display_name": "Controlled chart",
        "decision_usable": True,
        "fresh": True,
        "reason_code": "SOURCE_LIVE",
        "selected_at": captured_at - 60,
        "updated_at": captured_at,
        "last_frame_epoch": captured_at,
        "last_frame_id": 81,
        "frame_age_sec": 0,
        "stale_after_sec": 20,
        "message": "Controlled chart is streaming in the background.",
        "stream": {
            "accepted_frames": 81,
            "duplicate_frames": 0,
            "last_frame_id": 81,
            "last_capture_epoch": captured_at,
            "last_error": "",
        },
    }
    return payload


def test_live_capture_confirming_identity_is_not_presented_as_stale_or_stay_out(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["overlays"] = []
    payload["freshness"].update(
        {
            "state": "STALE",
            "label": "Updating on the next complete frame",
            "valid_until": None,
            "age_seconds": 83,
        }
    )
    session = {
        "session_id": "operator-test",
        "capture_source_v3": _live_capture_source(),
        "tracking_summary": {
            "detected_market": "NZD/JPY OTC",
            "detected_timeframe": "M5",
            "market_identity_confirmed": False,
            "timeframe_identity_confirmed": False,
            "broker_source_lock": {"status": "VALID"},
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        assert page.locator("#source-state").inner_text() == "LIVE"
        assert page.locator("#connection-label").inner_text() == (
            "Chart live · confirming pair/timeframe"
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "CONFIRMING PAIR & TIMEFRAME"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR CURRENT READ"
        )
        assert "Identifying NZD/JPY OTC · M5" in page.locator(
            "#current-move-title"
        ).inner_text()
        assert "confirming NZD/JPY OTC · M5" in page.locator(
            "#overlay-library-status"
        ).inner_text()
        assert "STAY OUT" not in page.locator(
            "#question-entry-now"
        ).inner_text()


def test_live_capture_processing_latest_frame_is_not_called_a_stale_source(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["overlays"] = []
    payload["freshness"].update(
        {
            "state": "STALE",
            "label": "Updating on the next complete frame",
            "valid_until": None,
            "age_seconds": 47,
        }
    )
    session = {
        "session_id": "operator-test",
        "capture_source_v3": _live_capture_source(),
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "broker_source_lock": {"status": "VALID"},
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        assert page.locator("#source-state").inner_text() == "LIVE"
        assert page.locator("#connection-label").inner_text() == (
            "Chart live · analyzing latest frame"
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR CURRENT READ"
        )
        assert "current overlay set is processing" in page.locator(
            "#overlay-library-status"
        ).inner_text()
        assert page.locator("#connection-state").get_attribute("data-state") == "live"


def test_frame_processing_heartbeat_is_active_analysis_not_stale_transport(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["overlays"] = []
    payload["freshness"].update(
        {
            "state": "STALE",
            "label": "Updating on the next complete frame",
            "valid_until": None,
            "age_seconds": 61,
        }
    )
    source = _live_capture_source()
    source.update(
        {
            "state": "VALIDATING",
            "decision_usable": False,
            "reason_code": "FRAME_PROCESSING",
            "frame_age_sec": 999,
            "message": "A fresh chart frame was received and is being studied.",
        }
    )
    source["stream"].update(
        {
            "processing": True,
            "processing_frame_id": 81,
            "processing_started_epoch": time.time(),
        }
    )
    session = {
        "session_id": "operator-test",
        "capture_source_v3": source,
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        assert page.locator("#source-state").inner_text() == "ANALYZING"
        assert page.locator("#connection-label").inner_text() == (
            "Chart live · analyzing latest frame"
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR CURRENT READ"
        )
        assert page.locator("#connection-state").get_attribute("data-state") == "live"


def test_analyzing_frame_retains_latest_identity_matched_completed_study(
    chromium_browser: Browser,
) -> None:
    now_epoch = time.time()
    payload = _make_retention_timing_admissible(
        _with_timing_forecast(
            _operator_payload(action="BUY_NOW"),
            side="BUY",
            action_state="ENTER_NOW",
            enter_now=True,
        ),
        generated_at=now_epoch - 47,
        valid_until=now_epoch + 120,
    )
    payload["capture_source_v3"] = _live_capture_source()
    payload["freshness"].update(
        {
            "state": "STALE",
            "label": "Completed study updating",
            "observed_at": now_epoch - 47,
            "age_seconds": 47,
            "valid_until": None,
        }
    )
    payload["surface"]["updated_at"] = now_epoch - 47
    session = {
        "session_id": "operator-test",
        "capture_source_v3": _live_capture_source(),
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "broker_source_lock": {"status": "VALID"},
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        assert page.locator("#connection-label").inner_text() == (
            "Chart live · analyzing latest frame"
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "LATEST COMPLETED BUY STUDY"
        )
        forecast_copy = page.locator("#beginner-forecast-summary").inner_text()
        assert "Expected BUY move-start window: 3–6 completed M5 candles" in (
            forecast_copy
        )
        assert "when the studied move may start after the anchor" in forecast_copy
        assert "not a hold or expiry duration" in forecast_copy
        assert "Hold duration unavailable and not proven" in forecast_copy
        confidence_copy = page.locator("#beginner-confidence").inner_text()
        assert confidence_copy.startswith("Study age ")
        assert "completed-candle context · entry not current" in confidence_copy
        assert page.locator("#beginner-action-label").inner_text() == (
            "WATCH BUY · ENTRY NOT CURRENT"
        )
        assert page.locator("#inner-trend-title").inner_text() == (
            "Latest completed BUY study · newer frame analyzing"
        )
        assert "latest completed BUY study" in (
            page.locator("#beginner-evidence-timing").text_content() or ""
        )

        safety = page.locator("#beginner-evidence-safety").text_content() or ""
        assert "EUR/USD · M5" in safety
        assert "exact identity matched" in safety
        assert "no entry or execution authority" in safety
        assert "ENTER —" not in page.locator(
            "#question-entry-now"
        ).inner_text()

        # A newer capture response may carry only processing state while the
        # completed-study adapter catches up. Retain the exact same-chart
        # completed context rather than reverting to an empty wait card.
        processing_only = copy.deepcopy(payload)
        processing_only["revision"] = 43
        processing_only.pop("three_questions")
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            processing_only,
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "LATEST COMPLETED BUY STUDY"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WATCH BUY · ENTRY NOT CURRENT"
        )
        assert "Latest completed BUY study" in page.locator(
            "#inner-trend-title"
        ).inner_text()

        # A selector change starts a new visual namespace even when OCR still
        # reports the same pair and timeframe. Never carry the prior study
        # across that boundary.
        selector_changed = copy.deepcopy(processing_only)
        selector_changed["revision"] = 44
        selector_changed["surface"]["market_selector_visual_fingerprint"] = (
            "selector_v3_eur_usd_new_surface"
        )
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            selector_changed,
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR CURRENT READ"
        )
        assert "LATEST COMPLETED BUY STUDY" not in page.locator(
            "#question-entry-now"
        ).inner_text()


def test_analyzing_frame_does_not_retain_wrong_scope_timing_study(
    chromium_browser: Browser,
) -> None:
    payload = _with_timing_forecast(_operator_payload(), side="BUY")
    payload["three_questions"]["entry_now"]["timing_forecast"]["scope"][
        "symbol"
    ] = "GBP/USD"
    payload["freshness"].update(
        {
            "state": "STALE",
            "label": "Completed study updating",
            "observed_at": time.time() - 20,
            "age_seconds": 20,
            "valid_until": None,
        }
    )
    session = {
        "session_id": "operator-test",
        "capture_source_v3": _live_capture_source(),
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR CURRENT READ"
        )
        assert "LATEST COMPLETED BUY STUDY" not in page.locator(
            "#question-entry-now"
        ).inner_text()


def test_present_new_sell_contract_clears_retained_buy_instead_of_falling_back(
    chromium_browser: Browser,
) -> None:
    now_epoch = time.time()
    payload = _make_retention_timing_admissible(
        _with_timing_forecast(_operator_payload(), side="BUY"),
        generated_at=now_epoch - 20,
        valid_until=now_epoch + 180,
    )
    payload["capture_source_v3"] = _live_capture_source()
    payload["freshness"].update(
        {
            "state": "STALE",
            "observed_at": now_epoch - 20,
            "valid_until": None,
            "age_seconds": 20,
        }
    )
    session = {
        "session_id": "operator-test",
        "capture_source_v3": _live_capture_source(),
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        assert page.locator("#beginner-decision-title").inner_text() == (
            "LATEST COMPLETED BUY STUDY"
        )

        incoming_sell = copy.deepcopy(payload)
        incoming_sell["revision"] = 43
        sell_study = incoming_sell["three_questions"][
            "studied_direction_current"
        ]
        sell_study["side"] = "SELL"
        sell_study["evidence"].update(
            {
                "ensemble_studied_side": "SELL",
                "current_regression_side": "SELL",
                "closed_candle_key": "operator-eurusd-m5-close-B",
            }
        )
        sell_entry = incoming_sell["three_questions"]["entry_now"]
        sell_entry["side"] = "SELL"
        sell_entry.pop("timing_forecast")
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            incoming_sell,
        )

        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )
        assert "completed SELL" in page.locator(
            "#inner-trend-title"
        ).inner_text()
        assert "LATEST COMPLETED BUY STUDY" not in page.locator(
            "#question-entry-now"
        ).inner_text()

        # Once newer public Q2/Q3 truth invalidates BUY, a following
        # processing-only response cannot revive it.
        processing_only = copy.deepcopy(incoming_sell)
        processing_only["revision"] = 44
        processing_only.pop("three_questions")
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            processing_only,
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )
        assert "LATEST COMPLETED BUY STUDY" not in page.locator(
            "#question-entry-now"
        ).inner_text()


def test_retained_study_namespace_includes_selector_generation_and_geometry(
    chromium_browser: Browser,
) -> None:
    now_epoch = time.time()
    payload = _make_retention_timing_admissible(
        _with_timing_forecast(_operator_payload(), side="BUY"),
        generated_at=now_epoch - 10,
        valid_until=now_epoch + 300,
    )
    capture_source = _live_capture_source()
    capture_source.update(
        {
            "source_id": "edge-chart-region-v3",
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "selection_id": "edge-selection-17",
            "sequence_id": "edge-sequence-17",
        }
    )
    payload["capture_source_v3"] = copy.deepcopy(capture_source)
    payload["surface"]["overlay_geometry_revision"] = "geometry-a"
    payload["freshness"].update(
        {
            "state": "STALE",
            "observed_at": now_epoch - 10,
            "valid_until": None,
            "age_seconds": 10,
        }
    )
    session = {
        "session_id": "operator-test",
        "capture_source_v3": copy.deepcopy(capture_source),
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        },
    }

    boundaries: list[tuple[str, dict[str, Any]]] = []
    missing_selector = copy.deepcopy(payload)
    missing_selector["surface"].pop("market_selector_visual_fingerprint")
    boundaries.append(("missing selector", missing_selector))
    changed_generation = copy.deepcopy(payload)
    changed_generation["capture_source_v3"]["source_generation"] = 3
    boundaries.append(("source generation", changed_generation))
    changed_stream_generation = copy.deepcopy(payload)
    changed_stream_generation["tracking"]["stream"]["stream_generation"] = 4
    boundaries.append(("stream generation", changed_stream_generation))
    changed_source_type = copy.deepcopy(payload)
    changed_source_type["capture_source_v3"]["source_type"] = (
        "windows_graphics_capture_roi"
    )
    changed_source_type["capture_source_v3"]["updated_at"] += 1
    boundaries.append(("capture source type", changed_source_type))
    changed_source_coordinates = copy.deepcopy(payload)
    changed_source_coordinates["capture_source_v3"]["coordinate_space"] = (
        "edge_tab_content_v1"
    )
    changed_source_coordinates["capture_source_v3"]["updated_at"] += 1
    boundaries.append(("capture coordinate space", changed_source_coordinates))
    changed_source_id = copy.deepcopy(payload)
    changed_source_id["capture_source_v3"]["source_id"] = "edge-chart-region-v4"
    changed_source_id["capture_source_v3"]["updated_at"] += 1
    boundaries.append(("capture source id", changed_source_id))
    changed_sequence_id = copy.deepcopy(payload)
    changed_sequence_id["capture_source_v3"]["sequence_id"] = "edge-sequence-18"
    changed_sequence_id["capture_source_v3"]["updated_at"] += 1
    boundaries.append(("capture sequence id", changed_sequence_id))
    changed_selection_id = copy.deepcopy(payload)
    changed_selection_id["capture_source_v3"]["selection_id"] = (
        "edge-selection-18"
    )
    changed_selection_id["capture_source_v3"]["updated_at"] += 1
    boundaries.append(("capture selection id", changed_selection_id))
    changed_geometry = copy.deepcopy(payload)
    changed_geometry["surface"]["overlay_geometry_revision"] = "geometry-b"
    boundaries.append(("geometry revision", changed_geometry))
    changed_coordinate = copy.deepcopy(payload)
    changed_coordinate["surface"]["primary_space"] = "chart"
    boundaries.append(("coordinate space", changed_coordinate))
    tracking_stopped = copy.deepcopy(payload)
    tracking_stopped["tracking"].update({"active": False, "state": "STOPPED"})
    boundaries.append(("tracking stopped", tracking_stopped))

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        for label, boundary in boundaries:
            page.evaluate(
                "seed => window.renderOperatorState(seed)",
                payload,
            )
            assert page.locator("#beginner-decision-title").inner_text() == (
                "LATEST COMPLETED BUY STUDY"
            ), label
            processing_boundary = copy.deepcopy(boundary)
            processing_boundary.pop("three_questions")
            page.evaluate(
                "nextPayload => window.renderOperatorState(nextPayload)",
                processing_boundary,
            )
            assert page.locator("#beginner-decision-title").inner_text() != (
                "LATEST COMPLETED BUY STUDY"
            ), label
            assert page.locator("#beginner-action-label").inner_text() != (
                "WATCH BUY · ENTRY NOT CURRENT"
            ), label

        page.evaluate("seed => window.renderOperatorState(seed)", payload)
        assert page.locator("#beginner-decision-title").inner_text() == (
            "LATEST COMPLETED BUY STUDY"
        )
        page.locator("#source-select").click()
        assert page.locator("#beginner-decision-title").inner_text() == (
            "SELECTING NEW CHART"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR CURRENT READ"
        )

        page.evaluate("seed => window.renderOperatorState(seed)", payload)
        assert page.locator("#beginner-decision-title").inner_text() == (
            "LATEST COMPLETED BUY STUDY"
        )
        page.locator("#source-kill").click()
        assert page.locator("#beginner-decision-title").inner_text() == (
            "TRACKING STOPPING"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "DO NOT USE OLD SIGNAL"
        )


def test_retained_study_uses_normal_timing_gate_and_expires_its_window(
    chromium_browser: Browser,
) -> None:
    now_epoch = time.time()
    base = _with_timing_forecast(_operator_payload(), side="BUY")
    base["capture_source_v3"] = _live_capture_source()
    base["freshness"].update(
        {
            "state": "STALE",
            "observed_at": now_epoch - 10,
            "valid_until": None,
            "age_seconds": 10,
        }
    )
    # PAIR without timing_empirical/timing_support_count is not admissible.
    base_forecast = base["three_questions"]["entry_now"]["timing_forecast"]
    base_forecast.update(
        {
            "generated_at": now_epoch - 10,
            "valid_until": now_epoch + 300,
        }
    )
    session = {
        "session_id": "operator-test",
        "capture_source_v3": _live_capture_source(),
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        },
    }

    with _dashboard_page(
        chromium_browser,
        base,
        session_payload=session,
    ) as page:
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )

        below_minimum = _make_retention_timing_admissible(
            base,
            generated_at=now_epoch - 10,
            valid_until=now_epoch + 300,
        )
        below_minimum_forecast = below_minimum["three_questions"]["entry_now"][
            "timing_forecast"
        ]
        below_minimum_forecast.update(
            {
                "horizon_seconds_low": 899,
                "horizon_seconds_high": 1_800,
            }
        )
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            below_minimum,
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ANALYZING LATEST FRAME"
        )

        admissible = _make_retention_timing_admissible(
            base,
            generated_at=now_epoch - 10,
            valid_until=now_epoch + 300,
        )
        admissible_forecast = admissible["three_questions"]["entry_now"][
            "timing_forecast"
        ]
        admissible_forecast["recommended_trade_duration_seconds"] = 1_200
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            admissible,
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "LATEST COMPLETED BUY STUDY"
        )
        projection = page.locator("#beginner-forecast-summary").inner_text()
        assert "Unproven hold-duration recommendation: 20 minutes" in projection
        assert "Proven studied hold duration" not in projection

        proven = copy.deepcopy(admissible)
        proven_forecast = proven["three_questions"]["entry_now"][
            "timing_forecast"
        ]
        proven_forecast["duration_provenance"] = {
            "recommended_trade_duration_proven": True
        }
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            proven,
        )
        assert "Proven studied hold duration: 20 minutes" in page.locator(
            "#beginner-forecast-summary"
        ).inner_text()

        expires = copy.deepcopy(proven)
        expires_forecast = expires["three_questions"]["entry_now"][
            "timing_forecast"
        ]
        expires_forecast["valid_until"] = time.time() + 1.2
        expires_forecast["generated_at"] = time.time() - 10
        page.evaluate(
            "nextPayload => window.renderOperatorState(nextPayload)",
            expires,
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "LATEST COMPLETED BUY STUDY"
        )
        page.wait_for_function(
            """
            () => document.querySelector('#beginner-decision-title')?.textContent.trim()
              === 'ANALYZING LATEST FRAME'
            """,
            timeout=5_000,
        )
        assert "move-start window elapsed" in page.locator(
            "#beginner-forecast-summary"
        ).inner_text()
        assert page.locator("#inner-trend-title").inner_text() == (
            "Latest completed timing window elapsed"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR CURRENT READ"
        )


def test_capture_source_uses_server_frame_age_instead_of_dashboard_clock(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    source = _live_capture_source(last_frame_epoch=time.time() - 3_600)
    # The capture service measured this frame beside the source and explicitly
    # reports it fresh. A dashboard clock skew must not rewrite that truth.
    source["frame_age_sec"] = 0
    source["updated_at"] = time.time()
    session = {
        "session_id": "operator-test",
        "capture_source_v3": source,
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        },
    }

    with _dashboard_page(
        chromium_browser,
        payload,
        session_payload=session,
    ) as page:
        assert page.locator("#source-state").inner_text() == "LIVE"
        assert page.locator("#connection-state").get_attribute("data-state") == "live"
        assert "stale" not in page.locator("#connection-label").inner_text().lower()


def test_live_session_stream_updates_forming_read_and_completed_history_immediately(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["history"] = []
    payload["three_questions"] = {
        "studied_direction_current": {
            "side": "SELL",
            "confidence": 0,
            "answer": "The last completed study favored the upward regression.",
        }
    }


    payload["tracking"]["market_study_v3"] = {
        "symbol": "EUR/USD",
        "timeframe": "M5",
        "closed_candle_key": "closed-41",
        "closed_candle_sequence": 41,
        "observed_at": 4_102_444_500.0,
        "regression": {
            "major_trend": {"side": "BUY", "confidence": 0.81},
            "inner_trend": {"side": "SELL", "confidence": 0.67},
        },
        "directional_read": {"side": "BUY"},
        "behavior": {"market_story": "The upward market entered a pullback."},
        "candle_intelligence": {
            "latest": {
                "closed": True,
                "direction": "SELL",
                "type": "bearish rejection",
                "relation_to_previous": "inside",
            }
        },
    }

    initial_session = {
        "session_id": "operator-test",
        "cpu_stream_v3": {
            "enabled": True,
            "requested": True,
            "status": "running",
            "last_capture_epoch": 4_102_444_505.0,
            "observer": {
                "status": "healthy",
                "last_captured_epoch": 4_102_444_505.0,
                "last_decision": {"temporal_evidence": {"state": "duplicate"}},
            },
        },
        "tracking_summary": {
            "market_study_v3": copy.deepcopy(
                payload["tracking"]["market_study_v3"]
            )
        },
    }
    with _dashboard_page(
        chromium_browser,
        payload,
        with_event_source=True,
        session_payload=initial_session,
    ) as page:
        assert page.locator(".history-item").count() == 1
        assert "forming moving" in page.locator("#inner-trend-title").inner_text()
        assert "0%" not in page.locator("#direction-study-confidence").inner_text()

        next_study = copy.deepcopy(payload["tracking"]["market_study_v3"])
        next_study.update(
            {
                "closed_candle_key": "closed-42",
                "closed_candle_sequence": 42,
                "observed_at": 4_102_444_560.0,
            }
        )
        updated_operator = copy.deepcopy(payload)
        updated_operator["revision"] = 43
        updated_operator["freshness"]["observed_at"] = 4_102_444_565.0
        updated_operator["tracking"]["stream"]["last_frame_epoch"] = (
            4_102_444_565.0
        )
        updated_operator["tracking"]["stream"]["market_read"].update(
            {
                "state": "RESTING",
                "summary": "Live stream sees the chart resting now.",
                "fresh": True,
                "observed_at": 4_102_444_565.0,
            }
        )
        page.evaluate(
            """
            update => {
              window.__OPERATOR_PAYLOAD = update.operator;
              window.__EVENT_SOURCES[0].emit("SESSION_UPDATE", {
                cpu_stream_v3: {
                  enabled: true,
                  requested: true,
                  status: "running",
                  last_capture_epoch: 4102444565,
                  observer: {
                    status: "healthy",
                    last_captured_epoch: 4102444565,
                    last_decision: {
                      temporal_evidence: {state: "motion", direction: "SELL"}
                    },
                    counters: {frames_observed: 12501, keyframes_selected: 319}
                  }
                },
                tracking_summary: {market_study_v3: update.study}
              });
            }
            """,
            {"study": next_study, "operator": updated_operator},
        )

        page.wait_for_function(
            "() => window.PhoenixGuardDashboard.getState().revision === 43"
        )
        assert "forming resting" in page.locator("#inner-trend-title").inner_text()
        assert "moving down" not in page.locator("#inner-trend-title").inner_text()
        assert "Last completed candle:" in page.locator(
            "#beginner-next-read"
        ).inner_text()
        assert "Live forming stream:" in page.locator(
            "#beginner-next-read"
        ).inner_text()
        assert page.locator("#direction-study-confidence").inner_text() == (
            "Forming read · not closed"
        )
        assert page.locator(".history-item").count() == 2
        assert page.locator("#history-count").inner_text() == "2 observations"


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
            "source-select",
            "source-kill",
            "frame-window",
            "frame-chart",
            "mode-overlay",
            "mode-raw",
            "layers-all",
            "layers-clear",
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
        assert page.locator("button[data-overlay-family]").count() == 14
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
            == 14
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
            "count => window.__FETCH_REQUESTS.length === count + 3",
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


def test_labels_on_reveals_every_visible_mark_label_in_any_overlay_view(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        page.locator('button[data-overlay-view="live"]').click()
        page.locator('button[data-label-mode="on"]').click()
        result = page.evaluate(
            """
            () => {
              const root = document.querySelector('#hotspot-layer');
              root.innerHTML = '';
              function add(priority, label, policyHidden = false) {
                const button = document.createElement('button');
                button.className = 'surface-hotspot' + (policyHidden ? ' label-policy-hidden' : '');
                button.dataset.priority = String(priority);
                const span = document.createElement('span');
                span.textContent = label;
                span.getBoundingClientRect = () => ({
                  left: 20, top: 30, right: 120, bottom: 50, width: 100, height: 20,
                });
                button.appendChild(span);
                root.appendChild(button);
                return button;
              }
              const low = add(1, 'LOW');
              const high = add(100, 'HIGH');
              const policy = add(50, 'POLICY', true);
              window.resolveLabelCollisions(root);
              return {
                bodyClass: document.body.className,
                lowHidden: low.classList.contains('label-collision-hidden'),
                highHidden: high.classList.contains('label-collision-hidden'),
                policyOpacity: Number(getComputedStyle(policy.querySelector('span')).opacity),
              };
            }
            """
        )

        assert "labels-show-all" in result["bodyClass"]
        assert result["lowHidden"] is False
        assert result["highHidden"] is False
        assert result["policyOpacity"] > 0.5


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
            "market_study_v3": market_study,
            "summary": "Upward swing began.",
        },
        {
            "id": "study-e2",
            "observed_at": 4_102_444_400.0,
            "direction": "SELL",
            "state": "HISTORICAL",
            "market_study_v3": market_study,
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
        assert page.locator(".legacy-three-question-panel").is_hidden()
        assert page.locator("#frontline-qwen-panel").is_visible()
        assert page.locator("#frontline-qwen-buy").is_visible()
        assert page.locator("#frontline-qwen-sell").is_visible()
        assert page.locator("#current-move-title").inner_text() == "From an upward market"
        assert page.locator("#inner-trend-title").inner_text() == (
            "SELL studied · completed BUY · forming moving"
        )
        assert page.locator("#permission-title").inner_text() == "History leans upward"
        assert "two rests" in page.locator("#beginner-entry-read").inner_text().lower()
        assert "downward pullback" in page.locator("#beginner-next-read").inner_text().lower()

        latest = page.locator('[data-history-id="study-e3"]')
        assert latest.locator(".history-major-trend").inner_text() == "Major · uptrend"
        assert latest.locator(".history-inner-trend").inner_text() == "Inner · down"
        assert latest.locator(".history-side").inner_text() == "DOWN CONTINUE"
        assert latest.locator(".history-regression").inner_text() == "REGRESSION MATCH"


def test_three_question_contract_is_the_plain_language_source_of_truth(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="SELL_NOW")
    payload["three_questions"] = {
        "market_origin_history": {
            "question": "Where is the market from, and how did history behave?",
            "headline": "From a strong upward market",
            "answer": "Price climbed in two swings, rested twice, then rejected the upper zone.",
            "state": "UPTREND_WITH_REJECTION",
            "side": "BUY",
            "confidence": 0.91,
            "evidence": ["Two upward swings", "Two completed rests"],
            "updated_at": 4_102_444_500.0,
        },
        "studied_direction_current": {
            "question": "Which direction was studied, and what is being studied now?",
            "headline": "SELL was studied · SELL remains active",
            "answer": "The ensemble is studying a countertrend sell from the upper reaction area.",
            "state": "ACTIVE_SELL_STUDY",
            "side": "SELL",
            "confidence": 0.87,
            "evidence": "Upper rejection and downward candle pressure agree.",
            "updated_at": 4_102_444_500.0,
        },
        "entry_now": {
            "question": "Should the trade be entered now?",
            "headline": "YES — SELL NOW",
            "answer": "Enter SELL inside the verified upper reaction area; do not chase lower.",
            "state": "ENTER",
            "side": "SELL",
            "confidence": 0.84,
            "evidence": "The ensemble and current trigger agree.",
            "updated_at": 4_102_444_500.0,
            "enter_now": True,
            "action": "SELL_NOW",
            "reason": "The current rejection confirms the studied sell while location is valid.",
            "next_trigger": "Exit the idea if the upper reaction area fails.",
            "timing_state": "ENTER_NOW",
        },
    }

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator(".decision-question").count() == 3
        assert page.locator("#market-origin-question").inner_text() == (
            "Where is the market from, and how did history behave?"
        )
        assert page.locator("#direction-study-question").inner_text() == (
            "Which direction was studied, and what is being studied now?"
        )
        assert page.locator("#entry-now-question").inner_text() == (
            "What is the best decision to do right now?"
        )
        assert page.locator("#current-move-title").inner_text() == (
            "From a strong upward market"
        )
        assert "rested twice" in page.locator("#beginner-now-read").inner_text()
        assert page.locator("#market-origin-confidence").inner_text() == (
            "91% model confidence"
        )
        assert page.locator("#inner-trend-title").inner_text() == (
            "SELL studied · completed BUY · forming moving"
        )
        assert "countertrend sell" in page.locator("#beginner-next-read").inner_text()
        assert page.locator("#direction-study-confidence").inner_text() == (
            "87% model confidence"
        )
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ENTER — SELL NOW"
        )
        assert page.locator("#beginner-confidence").inner_text() == (
            "84% ensemble study score"
        )
        assert "do not chase lows" in page.locator("#beginner-instruction").inner_text()
        assert "current rejection confirms" in page.locator("#beginner-reason").inner_text()
        assert "upper reaction area fails" in page.locator(
            "#beginner-next-condition"
        ).inner_text()
        assert page.locator("#beginner-decision-shell").get_attribute(
            "data-tone"
        ) == "sell"
        assert page.locator(".evidence-details").get_attribute("open") is None


def test_passive_decision_audit_shows_measured_outcomes_without_trade_authority(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="WAIT")
    cast(dict[str, Any], payload["tracking"])["market_study_v3"] = {
        "hidden_state_discovery_v3": {
            "hidden_state": {"state": "DOWN_SWING", "age_candles": 6},
            "control": {
                "side": "SELL",
                "candidate_side": "SELL",
                "status": "STRUCTURALLY_CONFIRMED_CONTROL",
                "explanation": "SELL structural control is confirmed by primary structure and a three-touch wick line.",
                "structural_evidence": {
                    "confirmed_trendline_count": 2,
                    "selected_line": {"touch_count": 3},
                },
            },
            "next_state_distribution": {
                "support": 14,
                "normalized_entropy": 0.35,
            },
            "pair_dna": {"transition_support": 42},
        },
        "path_clock_liquidity_v3": {
            "passive_prediction_audit_v3": {
                "schema_version": "PG_PASSIVE_PREDICTION_OUTCOME_AUDIT_V3",
                "status": "AUDITED_OUTCOMES",
                "symbol": "EUR/USD",
                "timeframe": "M5",
                "frozen_forecast_count": 12,
                "pending_outcome_count": 3,
                "matured_outcome_count": 9,
                "study_only": True,
                "execution_authority": False,
                "places_trades": False,
                "can_grant_entry_permission": False,
                "candidate_metrics": {
                    "directional_accuracy": 0.78,
                    "timing_accuracy": 0.67,
                    "sweep_survival_rate": 0.56,
                    "calibration_score": 0.71,
                },
                "profitability_evidence_v3": {
                    "schema_version": "PG_FORWARD_PROFITABILITY_EVIDENCE_V3",
                    "status": "PROVEN_FORWARD_POSITIVE_EXPECTANCY",
                    "support": 240,
                    "minimum_forward_outcomes": 200,
                    "promotion_eligible": True,
                    "reference_scenario": {
                        "payout_ratio": 0.75,
                        "expected_value_per_unit_point": 0.08,
                        "expected_value_per_unit_lower_95": 0.042,
                    },
                },
                "latest_matured_outcome": {
                    "predicted_direction": "UP",
                    "direction_correct": True,
                    "observed_move_occurred": True,
                    "timing_correct": True,
                    "sweep_survival_rate": 0.5,
                },
            }
        }
    }

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#decision-audit-strip").count() == 0
        assert page.locator("#latent-control-rail").count() == 0
        assert page.locator("#latent-buy-component").count() == 0
        assert page.locator("#latent-sell-component").count() == 0
        # The clearance panel replaces the retired evidence audit.
        assert page.locator("#frontline-qwen-panel").is_visible()
        assert page.locator("#frontline-qwen-verdict").inner_text() == "PENDING"
        assert page.locator("#frontline-qwen-buy").is_visible()
        assert page.locator("#frontline-qwen-sell").is_visible()
        # Measured outcomes never grant trade authority.
        assert page.locator("#beginner-decision-title").inner_text() == "PREPARE"
        assert page.locator("#beginner-action-label").inner_text() == "PREPARE"


def test_hidden_legacy_timing_forecast_never_replaces_hidden_state_control(
    chromium_browser: Browser,
) -> None:
    payload = _with_timing_forecast(_operator_payload(action="WAIT"))

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator(".decision-question").count() == 3
        assert page.locator(".legacy-three-question-panel").is_hidden()
        # The retired hidden-state rail stays absent; the clearance panel
        # remains the visible BUY/SELL side presentation.
        assert page.locator("#latent-control-rail").count() == 0
        assert page.locator("#latent-buy-component").count() == 0
        assert page.locator("#latent-sell-component").count() == 0
        assert page.locator("#frontline-qwen-panel").is_visible()
        assert page.locator("#frontline-qwen-buy").is_visible()
        assert page.locator("#frontline-qwen-sell").is_visible()
        assert page.locator("#beginner-confidence").inner_text() == (
            "Entry closed · studied setup is being prepared"
        )
        assert page.locator("#beginner-action-label").inner_text() == "PREPARE"
        assert page.locator("#beginner-action-row").get_attribute(
            "data-action"
        ) == "prepare"
        assert "timing range withheld" in page.locator(
            "#beginner-forecast-summary"
        ).inner_text()
        assert "11 matching outcomes are recorded" in page.locator(
            "#beginner-forecast-summary"
        ).inner_text()
        assert "1–3 candles" in page.locator("#beginner-reason").inner_text()
        assert "completed candle changes direction" in page.locator(
            "#beginner-next-condition"
        ).inner_text()
        assert "NOT YET" not in page.locator("#question-entry-now").inner_text().upper()

        refreshed = copy.deepcopy(payload)
        refreshed["revision"] += 1
        refreshed_entry = refreshed["three_questions"]["entry_now"]
        refreshed_entry["operator_action"]["state"] = "WAIT_FOR_PULLBACK"
        refreshed_entry["operator_action"]["label"] = "WAIT FOR PULLBACK"
        refreshed_entry["operator_action"]["instruction"] = (
            "Wait for the pullback and sweep to complete."
        )
        page.evaluate("value => window.renderOperatorState(value)", refreshed)

        assert page.locator("#beginner-decision-title").inner_text() == (
            "WAIT FOR PULLBACK"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR PULLBACK"
        )
        assert "pullback and sweep" in page.locator(
            "#beginner-instruction"
        ).inner_text()


def _live_m5_sequence_timing_payload(
    *,
    horizon_seconds_low: int = 900,
    horizon_seconds_high: int = 1_800,
) -> dict[str, Any]:
    payload = _with_timing_forecast(
        _operator_payload(action="WAIT"),
        side="BUY",
        action_state="WAIT_FOR_PULLBACK",
        enter_now=False,
    )
    forecast = payload["three_questions"]["entry_now"]["timing_forecast"]
    forecast.update(
        {
            "status": "FORECAST_AVAILABLE",
            "source": "LIVE_M5_SEQUENCE",
            "source_label": "Current M5 closed-candle sequence",
            "timing_evidence_label": (
                "Current M5 closed-candle sequence · 54 current candles"
            ),
            "horizon_seconds_low": horizon_seconds_low,
            "horizon_seconds_high": horizon_seconds_high,
            "event_likelihood_support_count": 0,
            "support_count": 0,
            "estimated_likelihood": None,
            "estimated_likelihood_label": (
                "Event likelihood unavailable for swing completion"
            ),
            "evidence_confidence": None,
            "evidence_confidence_label": "Evidence confidence unavailable",
            "calibration_grade": "UNRATED",
            "calibration_label": (
                "Calibration UNRATED · not replay-calibrated"
            ),
            "calibrated": False,
        }
    )
    return payload


def test_live_m5_sequence_publishes_uncalibrated_closed_candle_timing_estimate(
    chromium_browser: Browser,
) -> None:
    payload = _live_m5_sequence_timing_payload()

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == (
            "WAIT FOR PULLBACK"
        )
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR PULLBACK"
        )
        assert page.locator("#beginner-action-row").get_attribute(
            "data-action"
        ) == "wait_for_pullback"

        projection = page.locator("#beginner-forecast-summary").inner_text()
        assert "BUY uncalibrated closed-candle estimate" in projection
        assert "3–6 completed M5 candles after the anchor close" in projection
        assert "Current M5 closed-candle sequence estimate" in projection
        assert "Event probability unavailable" in projection
        assert "not replay-calibrated" in projection
        assert "does not grant entry permission" in projection
        assert "timing range withheld" not in projection
        assert "68%" not in projection
        assert page.locator("#beginner-confidence").inner_text() == (
            "Entry closed · completed pullback confirmation required"
        )


def test_empirical_pair_timing_publishes_bounded_uncalibrated_estimate(
    chromium_browser: Browser,
) -> None:
    payload = _live_m5_sequence_timing_payload(
        horizon_seconds_low=900,
        horizon_seconds_high=2_700,
    )
    forecast = payload["three_questions"]["entry_now"]["timing_forecast"]
    forecast.update(
        {
            "source": "PAIR",
            "source_label": "Pair behavior timing history",
            "timing_evidence_label": "Pair behavior timing history · 3 timing observations",
            "timing_empirical": True,
            "timing_support_count": 3,
            "horizon_label": "3–9 completed M5 candles after the anchor close",
        }
    )

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == (
            "WAIT FOR PULLBACK"
        )
        projection = page.locator("#beginner-forecast-summary").inner_text()
        assert "BUY uncalibrated closed-candle estimate" in projection
        assert "3–9 completed M5 candles after the anchor close" in projection
        assert "Empirical pair-history closed-candle estimate" in projection
        assert "3 timing observations" in projection
        assert "Event probability unavailable" in projection
        assert "does not grant entry permission" in projection
        assert "timing range withheld" not in projection
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR PULLBACK"
        )


def test_live_m5_sequence_below_fifteen_minutes_keeps_timing_range_withheld(
    chromium_browser: Browser,
) -> None:
    payload = _live_m5_sequence_timing_payload(
        horizon_seconds_low=600,
        horizon_seconds_high=1_200,
    )
    forecast = payload["three_questions"]["entry_now"]["timing_forecast"]
    forecast["horizon_label"] = "2–4 completed M5 candles after the anchor close"

    with _dashboard_page(chromium_browser, payload) as page:
        projection = page.locator("#beginner-forecast-summary").inner_text()
        assert "BUY direction studied · timing range withheld" in projection
        assert "uncalibrated closed-candle estimate" not in projection
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR PULLBACK"
        )


def test_q3_explains_active_move_as_next_impulse_without_chase_language(
    chromium_browser: Browser,
) -> None:
    payload = _with_timing_forecast(
        _operator_payload(action="WAIT"),
        side="BUY",
        action_state="WAIT_FOR_PULLBACK",
        enter_now=False,
    )
    entry = payload["three_questions"]["entry_now"]
    forecast = entry["timing_forecast"]
    forecast.update(
        {
            "event_definition": (
                "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"
            ),
            "active_target_next_impulse": True,
            "target_move_already_active": True,
            "estimated_likelihood": None,
            "estimated_likelihood_label": (
                "Event likelihood unavailable for next target swing start"
            ),
        }
    )
    forecast.pop("headline")
    forecast.pop("summary")
    entry["operator_action"].pop("instruction")

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == (
            "WAIT FOR PULLBACK"
        )
        summary = page.locator("#beginner-forecast-summary").inner_text()
        assert "BUY direction studied · timing range withheld" in summary
        assert "11 matching outcomes are recorded" in summary
        assert "candle range is not published" in summary
        assert "3–6" not in summary
        assert page.locator("#beginner-action-label").inner_text() == (
            "WAIT FOR PULLBACK"
        )
        instruction = page.locator("#beginner-instruction").inner_text()
        assert "one completed rest or pullback" in instruction
        assert "Do not chase the current move" in instruction
        question = page.locator("#question-entry-now").inner_text().lower()
        assert "timing unrated" not in question
        assert "enter now" not in question

        refreshed = copy.deepcopy(payload)
        refreshed["revision"] += 1
        refreshed_action = refreshed["three_questions"]["entry_now"][
            "operator_action"
        ]
        refreshed_action["state"] = "PREPARE"
        refreshed_action["label"] = "PREPARE"
        page.evaluate("value => window.renderOperatorState(value)", refreshed)
        assert page.locator("#beginner-action-label").inner_text() == (
            "PREPARE"
        )
        assert page.locator("#beginner-decision-title").inner_text() == "PREPARE"


def test_hidden_unrated_projection_never_leads_and_atomic_permission_controls_enter(
    chromium_browser: Browser,
) -> None:
    payload = _with_timing_forecast(
        _operator_payload(action="WAIT"),
        side="BUY",
        action_state="PREPARE",
        enter_now=False,
    )
    forecast = payload["three_questions"]["entry_now"]["timing_forecast"]
    forecast.update(
        {
            "estimated_likelihood": None,
            "estimated_likelihood_label": "Event likelihood unavailable",
            "evidence_confidence": None,
            "evidence_confidence_label": "Evidence confidence unavailable",
            "event_likelihood_support_count": 0,
            "support_count": 0,
            "calibrated": False,
            "calibration_grade": "UNRATED",
"calibration_label": (
                "Calibration UNRATED · not replay-calibrated"
            ),
        }
    )

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator(".legacy-three-question-panel").is_hidden()
        assert page.locator("#latent-control-rail").count() == 0
        assert page.locator("#latent-buy-component").count() == 0
        assert page.locator("#latent-sell-component").count() == 0
        assert page.locator("#frontline-qwen-panel").is_visible()
        assert page.locator("#frontline-qwen-buy").is_visible()
        assert page.locator("#frontline-qwen-sell").is_visible()
        projection = page.locator("#beginner-forecast-summary").inner_text()
        assert "BUY direction studied · timing range withheld" in projection
        assert "3–6" not in projection
        assert "no replay-calibrated matching outcome yet" in projection
        assert page.locator("#beginner-action-label").inner_text() == "PREPARE"

    enter_payload = _with_timing_forecast(
        _operator_payload(action="BUY_NOW"),
        side="BUY",
        action_state="ENTER_NOW",
        enter_now=True,
    )
    enter_forecast = enter_payload["three_questions"]["entry_now"][
        "timing_forecast"
    ]
    enter_forecast.update(
        {
            "event_likelihood_support_count": 0,
            "support_count": 0,
            "calibrated": False,
            "calibration_grade": "UNRATED",
        }
    )
    with _dashboard_page(chromium_browser, enter_payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == (
            "ENTER — BUY NOW"
        )
        assert page.locator("#beginner-action-label").inner_text() == "ENTER NOW"
        assert "candle range is not published" in page.locator(
            "#beginner-forecast-summary"
        ).inner_text()


def test_q3_rejects_wrong_forecast_scope_without_fabricating_confidence_or_source(
    chromium_browser: Browser,
) -> None:
    payload = _with_timing_forecast(_operator_payload(action="WAIT"))
    entry = payload["three_questions"]["entry_now"]
    entry["confidence"] = 0.84
    entry["timing_forecast"]["scope"]["closed_candle_key"] = "wrong-close"

    with _dashboard_page(chromium_browser, payload) as page:
        meta = page.locator("#beginner-confidence").inner_text()
        summary = page.locator("#beginner-forecast-summary").inner_text()

        assert meta == "Entry closed · studied setup is being prepared"
        assert "84%" not in meta
        assert "current pair, timeframe, and completed candle" in summary


def test_q3_shows_clock_anchored_only_for_proven_fixed_epoch_window(
    chromium_browser: Browser,
) -> None:
    payload = _with_timing_forecast(_operator_payload(action="WAIT"))
    forecast = payload["three_questions"]["entry_now"]["timing_forecast"]
    forecast.update(
        {
            "headline": "BUY leading path · fixed window opens in 15 min",
            "horizon_label": "Fixed window opens in 15 min · closes in 30 min",
            "exact_wall_clock_proven": True,
            "anchor_close_epoch_seconds": 4_102_444_500.0,
            "target_window_start_epoch_seconds": 4_102_445_400.0,
            "target_window_end_epoch_seconds": 4_102_446_300.0,
            "event_likelihood_support_count": 32,
            "support_count": 32,
            "calibrated": True,
            "calibration_grade": "A_REPLAY_CALIBRATED",
        }
    )

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-confidence").inner_text() == (
            "Entry closed · studied setup is being prepared"
        )
        assert "fixed window opens in 15 min" in page.locator(
            "#beginner-forecast-summary"
        ).inner_text()


def test_continuous_observation_health_stays_compact_until_details_are_opened(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        assert page.locator(".decision-question").count() == 3
        status = page.locator("#stream-observation")
        assert status.is_visible()
        assert status.get_attribute("data-state") == "running"
        assert page.locator("#stream-observation-label").inner_text() == (
            "Continuous observation · live"
        )
        assert page.locator("video").count() == 0

        details = page.locator(".evidence-details")
        assert details.get_attribute("open") is None
        assert page.locator("#stream-observation-detail").is_hidden()
        details.locator("summary").click()
        assert page.locator("#stream-observation-detail").is_visible()
        detail = page.locator("#stream-observation-detail").inner_text()
        assert "7.88 FPS acquisition" in detail
        assert "12,480 frames observed" in detail
        assert "318 keyframes accepted" in detail
        assert "4 dropped" in detail
        assert "2,106 duplicates" in detail
        assert "generation 3" in detail
        assert "Visual change accepted" in detail


@pytest.mark.parametrize(
    ("permission_action", "entry_patch"),
    [
        (
            "WAIT",
            {
                "headline": "YES — SELL NOW",
                "answer": "Enter SELL now.",
                "state": "ENTER_NOW",
                "side": "SELL",
                "enter_now": True,
                "action": "SELL_NOW",
                "timing_state": "ENTER_NOW",
            },
        ),
        (
            "SELL_NOW",
            {
                "headline": "YES — BUY NOW",
                "answer": "Enter BUY now.",
                "state": "ENTER_NOW",
                "side": "BUY",
                "enter_now": True,
                "action": "BUY_NOW",
                "timing_state": "ENTER_NOW",
            },
        ),
        (
            "SELL_NOW",
            {
                "headline": "YES — SELL NOW",
                "answer": "Enter SELL now.",
                "state": "ENTER_NOW",
                "side": "SELL",
                "enter_now": False,
                "action": "SELL_NOW",
                "timing_state": "ENTER_NOW",
            },
        ),
        (
            "WAIT",
            {
                "headline": "YES — SELL NOW",
                "answer": "Enter SELL now.",
                "state": "FORMING",
                "side": "SELL",
                "enter_now": False,
                "action": "DO_NOT_ENTER",
                "timing_state": "FORMING",
            },
        ),
    ],
)
def test_three_question_entry_answer_cannot_override_or_conflict_with_permission(
    chromium_browser: Browser,
    permission_action: str,
    entry_patch: dict[str, Any],
) -> None:
    payload = _operator_payload(action=permission_action)
    payload["three_questions"] = {
        "entry_now": entry_patch
    }

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "STAY OUT"
        assert "do not enter" in page.locator("#beginner-instruction").inner_text().lower()
        assert page.locator("#beginner-decision-shell").get_attribute("data-tone") == "hold"


def test_live_yes_is_invalidated_immediately_when_refresh_loses_connection(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(action="SELL_NOW")
    ) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "ENTER — SELL NOW"
        page.evaluate(
            """
            () => {
              window.fetch = () => Promise.reject(new Error('network unavailable'));
              window.PhoenixGuardDashboard.refresh({force: true});
            }
            """
        )
        page.wait_for_function(
            "() => document.querySelector('#beginner-decision-title')?.textContent === 'STAY OUT'"
        )
        assert page.locator("#beginner-decision-shell").get_attribute("data-tone") == "blocked"
        assert "do not enter" in page.locator("#beginner-instruction").inner_text().lower()


def test_live_yes_closes_at_its_client_verified_expiry(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload(action="SELL_NOW")

    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "ENTER — SELL NOW"
        page.evaluate(
            """
            () => {
              const payload = structuredClone(window.__OPERATOR_PAYLOAD);
              payload.revision += 1;
              payload.permission.expires_at = (Date.now() / 1000) + 1.25;
              payload.permission.valid_for_seconds = 1.25;
              window.__OPERATOR_PAYLOAD = payload;
              window.renderOperatorState(payload);
            }
            """
        )
        assert page.locator("#beginner-decision-title").inner_text() == "ENTER — SELL NOW"
        page.wait_for_function(
            "() => document.querySelector('#beginner-decision-title')?.textContent === 'STAY OUT'",
            timeout=5_000,
        )
        assert page.locator("#beginner-decision-shell").get_attribute("data-tone") == "blocked"


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
















def test_current_overlay_controls_render_studies_without_retired_diagnostics(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
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
        page.locator("#layers-all").click()
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
        assert any(
            url == "/v1/mobile/window-tracker/sessions/operator-test"
            for url in fetch_urls
        )
        assert all(
            "/v1/mobile/operator/state/v1/" in url
            or url == "/v1/mobile/window-tracker/sessions/operator-test"
            or url.startswith("/v1/mobile/frontline/latest/")
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


def test_retired_model_path_controls_are_absent_from_v3_operator_surface(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload()) as page:
        assert page.locator("#forecast-action-status").count() == 0
        assert page.locator("#run-forecast").count() == 0
        assert page.locator("#show-future-path").count() == 0
        assert page.locator('[data-overlay-family="lstm"]').count() == 0
        assert page.locator('[data-overlay-family="scene_forecaster"]').count() == 0








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




def test_current_order_areas_have_independent_always_visible_controls(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
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
                "symbol": payload["market"]["symbol"],
                "timeframe": payload["market"]["timeframe"],
                "surface_semantic_identity": payload["surface"]["semantic_identity"],
                "market_selector_visual_fingerprint": payload["surface"]["market_selector_visual_fingerprint"],
                "instrument_identity_status": "LOCKED",
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
        order_status = page.locator("#order-area-control-status").inner_text().lower()
        assert "2 current reaction areas" in order_status
        assert "entry permission remains separate" in order_status
        assert "tracking" not in order_status
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
        assert float(reference_treatment["labelOpacity"]) == 0.0

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
        # Selection re-renders the overlay set and recomputes label lanes on
        # the next animation frame; measure only once the lane has settled.
        page.wait_for_function(
            "() => document.querySelector('[data-overlay-id=\"saved-buy-stop\"]')"
            "?.classList.contains('label-lane-below')"
        )
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
        inspector = inspector_copy.lower()
        assert "buy-stop area exists only because a completed candle confirmed" in inspector
        assert "only while still ahead of price" in inspector
        assert "do not chase after price has crossed it" in inspector
        assert "current reaction area" in inspector
        assert "anchored from the latest completed candle" in inspector
        assert "tracking" not in inspector
        assert "current status: under observation" in inspector
        assert "paired entry" not in inspector
        assert "reference is" not in inspector
        assert "entry permission remains separate" in inspector
        assert (
            page.locator("#inspector-confidence").inner_text()
            == "Evidence strength · 84%"
        )


def test_precision_entry_trigger_count_tracks_its_active_live_family(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["overlays"].append(
        {
            "id": "precision-entry-current",
            "type": "entry",
            "side": "SELL",
            "group": "plan",
            "family": "triggers",
            "layer": "triggers",
            "kind": "precision_entry",
            "kind_label": "Precision entry",
            "label": "Entry area",
            "bounds": [0.72, 0.21, 0.79, 0.29],
            "points": [],
            "line_points": [],
            "confidence": 0.76,
            "lifecycle": "current",
            "frame_id": 42,
            "coordinate_space": "chart",
            "coordinate_units": "normalized",
            "symbol": payload["market"]["symbol"],
            "timeframe": payload["market"]["timeframe"],
            "surface_semantic_identity": payload["surface"]["semantic_identity"],
            "market_selector_visual_fingerprint": payload["surface"]["market_selector_visual_fingerprint"],
            "instrument_identity_status": "LOCKED",
        }
    )

    with _dashboard_page(chromium_browser, payload) as page:
        control = page.locator('[data-overlay-kind-control="precision_entry"]')
        assert control.locator("[data-order-kind-count]").inner_text() == "1"
        assert control.get_attribute("data-available") == "true"
        assert control.is_disabled() is False
        assert control.get_attribute("aria-pressed") == "true"
        assert page.locator('[data-overlay-id="precision-entry-current"]').count() == 1

        control.click()

        assert control.get_attribute("aria-pressed") == "false"
        assert page.locator('[data-overlay-id="precision-entry-current"]').count() == 0



def test_order_area_controls_remain_atomic_while_the_next_image_decodes(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
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
            "symbol": initial["market"]["symbol"],
            "timeframe": initial["market"]["timeframe"],
            "surface_semantic_identity": initial["surface"]["semantic_identity"],
            "market_selector_visual_fingerprint": initial["surface"]["market_selector_visual_fingerprint"],
            "instrument_identity_status": "LOCKED",
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
        ) == "2"

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
        ) == "2"
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


@pytest.mark.parametrize("viewport", [(390, 844), (360, 800)])
def test_mobile_first_viewport_contains_hidden_state_control(
    chromium_browser: Browser,
    viewport: tuple[int, int],
) -> None:
    with _dashboard_page(
        chromium_browser, _operator_payload(), viewport=viewport
    ) as page:
        assert page.locator(".legacy-three-question-panel").is_hidden()
        assert page.locator("#latent-control-rail").count() == 0
        assert page.locator("#latent-buy-component").count() == 0
        assert page.locator("#latent-sell-component").count() == 0
        box = page.locator("#frontline-qwen-panel").bounding_box()
        assert box is not None
        assert box["y"] >= 0
        assert box["y"] + box["height"] <= viewport[1], (viewport, box)
        assert page.locator("#frontline-qwen-buy").is_visible()
        assert page.locator("#frontline-qwen-sell").is_visible()


def test_hidden_state_contract_drives_buy_sell_components(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["direct_visual_bias_v3"] = {
        "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
        "side": "BUY",
        "confidence": 0.82,
        "dominant_side": "BUY",
    }
    frontline = {
        "schema_version": "PG_FRONTLINE_QWEN_V3",
        "state": "ok",
        "verdict": "ALLOW",
        "confidence": 0.82,
        "side": "BUY",
        "reason": "The visual read supports the buy side while the sell side stays secondary.",
        "model": "qwen3-vl-32b",
    }

    with _dashboard_page(
        chromium_browser, payload, frontline=frontline
    ) as page:
        assert page.locator("#latent-control-rail").count() == 0
        assert page.locator("#latent-buy-component").count() == 0
        assert page.locator("#latent-sell-component").count() == 0
        page.wait_for_function(
            "() => document.querySelector('#frontline-qwen-verdict')?.textContent.trim() === 'ALLOW'"
        )
        assert page.locator("#frontline-qwen-panel").get_attribute(
            "data-verdict"
        ) == "ALLOW"
        assert page.locator("#frontline-qwen-buy-state").inner_text() == "PREFERRED"
        assert page.locator("#frontline-qwen-sell-state").inner_text() == "SECONDARY"
        assert page.locator("#frontline-qwen-model").inner_text() == (
            "analyst: qwen3-vl-32b"
        )


def test_ended_sell_pressure_and_current_up_move_keep_entry_closed_and_study_uptrend(
    chromium_browser: Browser,
) -> None:
    with _dashboard_page(chromium_browser, _operator_payload(action="WAIT")) as page:
        assert page.locator("#beginner-decision-title").inner_text() == "PREPARE"
        assert page.locator("#current-move-title").inner_text() == "From an upward market"
        assert "rising" in page.locator("#beginner-now-read").inner_text().lower()
        pressure = page.locator("#pressure-event")
        assert pressure.get_attribute("data-state") == "ended"
        pressure_text = (pressure.text_content() or "").lower()
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
        assert page.locator("#beginner-decision-title").inner_text() == "PREPARE"
        assert page.locator("#beginner-confidence").inner_text() == (
            "Entry closed · studied setup is being prepared"
        )

        page.evaluate("payload => window.renderOperatorState(payload)", updated)
        page.wait_for_function(
            "() => document.querySelector('#retracement-evidence')?.textContent.includes('current graph 3, full Pair DNA 15')"
        )
        assert "current graph 3, full Pair DNA 15" in evidence.inner_text()
        assert page.evaluate(
            "() => document.activeElement === document.querySelector('.evidence-details summary')"
        )
        assert page.locator("#beginner-decision-title").inner_text() == "PREPARE"

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
        assert page.locator("#beginner-decision-title").inner_text() == "PREPARE"


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
        assert page.locator("#beginner-decision-title").inner_text() == "ENTER — BUY NOW"
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
        assert page.locator("#beginner-decision-title").inner_text() == "ENTER — SELL NOW"
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
        assert page.locator("#beginner-decision-title").inner_text() == "PREPARE"
        assert page.locator("#permission-title").inner_text() == "History leans upward"
        assert (
            page.locator("#beginner-confidence").inner_text()
            == "Entry closed · studied setup is being prepared"
        )
        instruction = page.locator("#beginner-instruction").inner_text().lower()
        assert "setup window remains open" in instruction
        assert "current-frame permission is refreshing" in instruction
        reason = page.locator("#beginner-reason").inner_text().lower()
        assert "setup window remains open" in reason
        assert "current-frame permission is refreshing" in reason
        assert "refreshes current-frame permission" in page.locator(
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


def test_pixel_geometry_without_source_plane_dimensions_is_not_guessed(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    surface = cast(dict[str, Any], payload["surface"])
    for viewport in cast(
        dict[str, dict[str, Any]], surface.get("overlay_viewports", {})
    ).values():
        viewport.pop("source_bounds", None)
    cast(dict[str, Any], surface["overlay_viewport"]).pop("source_bounds", None)
    overlay = copy.deepcopy(
        next(row for row in payload["overlays"] if row["id"] == "demand-current")
    )
    overlay.update(
        {
            "coordinate_space": "chart",
            "coordinate_units": "pixels",
            "bounds": [100.0, 200.0, 300.0, 400.0],
        }
    )
    payload["overlays"] = [overlay]

    with _dashboard_page(chromium_browser, payload) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="demand-current"]').count() == 0
        assert page.locator('[data-layer-count="supply_demand"]').inner_text() == "0"


def test_exact_dual_target_contract_projects_chart_pixels_on_both_artifacts(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    surface = cast(dict[str, Any], payload["surface"])
    window_plane = [94.0, 164.0, 1069.0, 865.0]
    chart_artifact_plane = [34.0, 10.0, 1009.0, 711.0]

    def normalized_bounds(
        bounds: list[float], width: float, height: float
    ) -> list[float]:
        return [
            bounds[0] / width,
            bounds[1] / height,
            bounds[2] / width,
            bounds[3] / height,
        ]

    window_viewport = {
        "source_space": "chart",
        "target_space": "window",
        "coordinate_units": "normalized",
        "bounds": normalized_bounds(window_plane, 1859.0, 924.0),
        "source_bounds": [0.0, 0.0, 975.0, 701.0],
    }
    chart_viewport = {
        "source_space": "chart",
        "target_space": "chart_artifact",
        "coordinate_units": "normalized",
        "bounds": normalized_bounds(chart_artifact_plane, 1064.0, 721.0),
        "source_bounds": [0.0, 0.0, 975.0, 701.0],
    }
    surface["overlay_viewport"] = copy.deepcopy(window_viewport)
    surface["overlay_viewports"] = {
        "window": window_viewport,
        "chart": chart_viewport,
    }
    surface["overlay_geometry_revision"] = "exact-dual-target-geometry"

    demand = next(
        row for row in payload["overlays"] if row["id"] == "demand-current"
    )
    demand.update(
        {
            "bounds": [100.0, 200.0, 300.0, 400.0],
            "coordinate_space": "chart",
            "coordinate_units": "pixels",
        }
    )
    support = next(
        row for row in payload["overlays"] if row["id"] == "support-current"
    )
    support.update(
        {
            "bounds": [100.0, 200.0, 300.0, 400.0],
                "points": [[100.0, 200.0], [300.0, 400.0]],
                "line_points": [[100.0, 200.0], [300.0, 400.0]],
                "anchor_wick_points": [[100.0, 200.0], [300.0, 400.0]],
                "chart_bounds": [0.0, 0.0, 975.0, 701.0],
                "coordinate_space": "chart",
            "coordinate_units": "pixels",
        }
    )

    def geometry(page: Page) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            page.evaluate(
                """
                () => {
                  const image = document.querySelector('#surface-raw');
                  const imageRect = image.getBoundingClientRect();
                  const box = document.querySelector(
                    '[data-overlay-id="demand-current"]'
                  ).getBoundingClientRect();
                  const points = document.querySelector(
                    'polyline[data-overlay-id="support-current"]'
                  ).getAttribute('points').split(' ').map(pair => (
                    pair.split(',').map(Number)
                  ));
                  return {
                    imageNaturalSize: [image.naturalWidth, image.naturalHeight],
                    imageSpace: image.dataset.space,
                    points,
                    box: [
                      (box.left - imageRect.left) / imageRect.width,
                      (box.top - imageRect.top) / imageRect.height,
                      box.width / imageRect.width,
                      box.height / imageRect.height,
                    ],
                  };
                }
                """
            ),
        )

    def assert_points(
        actual: list[list[float]], expected: list[list[float]]
    ) -> None:
        assert len(actual) == len(expected)
        for actual_point, expected_point in zip(actual, expected, strict=True):
            assert actual_point == pytest.approx(expected_point, abs=1e-6)

    with _dashboard_page(
        chromium_browser,
        payload,
        viewport=(1440, 1000),
        artifact_image_bytes={
            "window": _surface_image_bytes(1859, 924),
            "chart": _surface_image_bytes(1064, 721),
        },
    ) as page:
        page.locator("#layers-all").click()
        window_geometry = geometry(page)
        assert window_geometry["imageNaturalSize"] == [1859, 924]
        assert window_geometry["imageSpace"] == "window"
        expected_window = (
            (window_plane[0] + 100.0) / 1859.0,
            (window_plane[1] + 200.0) / 924.0,
            200.0 / 1859.0,
            200.0 / 924.0,
        )
        for actual, expected in zip(
            window_geometry["box"], expected_window, strict=True
        ):
            assert abs(float(actual) - expected) <= 0.003
        assert_points(
            window_geometry["points"], [[194.0, 364.0], [394.0, 564.0]]
        )

        page.locator("#frame-chart").click()
        page.wait_for_function(
            """
            () => document.querySelector('#surface-raw').naturalWidth === 1064
              && document.querySelector('#surface-raw').dataset.space === 'chart_artifact'
            """
        )
        chart_geometry = geometry(page)
        assert chart_geometry["imageNaturalSize"] == [1064, 721]
        assert chart_geometry["imageSpace"] == "chart_artifact"
        expected_chart = (
            (chart_artifact_plane[0] + 100.0) / 1064.0,
            (chart_artifact_plane[1] + 200.0) / 721.0,
            200.0 / 1064.0,
            200.0 / 721.0,
        )
        for actual, expected in zip(
            chart_geometry["box"], expected_chart, strict=True
        ):
            assert abs(float(actual) - expected) <= 0.003
        assert_points(
            chart_geometry["points"], [[134.0, 210.0], [334.0, 410.0]]
        )


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
        overlay["surface_semantic_identity"] = "surface-eur-usd-m5-frame-43"

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


def test_continuous_regression_history_does_not_drop_rows_after_twenty_four(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["history"] = [
        {
            "id": f"regression-observation-{index:02d}",
            "observed_at": 4_102_440_000.0 + index,
            "direction": "BUY" if index % 2 == 0 else "SELL",
            "state": "HISTORICAL",
            "summary": f"Continuous regression observation {index + 1}.",
            "frame_id": 100 + index,
            "major_trend": {
                "side": "BUY",
                "confidence": 0.82,
            },
            "inner_trend": {
                "side": "SELL" if index % 3 == 0 else "BUY",
                "confidence": 0.61,
            },
            "regression_read": {
                "side": "BUY" if index % 2 == 0 else "SELL",
                "confidence": 0.73,
            },
            "behavior": {
                "current_state": {
                    "state": "REST" if index % 4 == 0 else "SWING",
                }
            },
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
            '[data-history-id="regression-observation-00"]'
        ).count() == 1
        assert page.locator(
            '[data-history-id="regression-observation-39"]'
        ).count() == 1


def test_empty_server_history_stays_empty_without_a_local_fallback(
    chromium_browser: Browser,
) -> None:
    payload = _operator_payload()
    payload["history"] = []
    with _dashboard_page(chromium_browser, payload) as page:
        assert page.locator(".history-item").count() == 0
        assert page.locator("#history-count").inner_text() == "No observations yet"
        assert page.locator(".history-empty").inner_text() == (
            "Completed candle studies will appear here in time order."
        )


def test_pair_switch_clears_server_regression_rows_and_old_geometry(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["surface"]["semantic_identity"] = "surface-eur-usd-m5"
    for overlay in initial["overlays"]:
        overlay["surface_semantic_identity"] = "surface-eur-usd-m5"
    initial["history"] = [
        {
            "id": "eur-regression-42",
            "observed_at": initial["freshness"]["observed_at"],
            "direction": "BUY",
            "state": "HISTORICAL",
            "summary": "EUR/USD major trend up; inner trend resting.",
            "frame_id": 42,
            "major_trend": {"side": "BUY", "confidence": 0.82},
            "inner_trend": {"side": "HOLD", "confidence": 0.61},
            "regression_read": {"side": "BUY", "confidence": 0.73},
            "behavior": {"current_state": {"state": "REST"}},
        }
    ]

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
    switched["history"] = []
    switched["overlays"] = []

    with _dashboard_page(chromium_browser, initial) as page:
        assert page.locator('[data-history-id="eur-regression-42"]').count() == 1
        assert page.locator('[data-overlay-id="council-current"]').count() == 1
        transition = page.evaluate(
            """
            payload => {
              window.renderOperatorState(payload);
              return {
                historyRows: document.querySelectorAll('.history-item').length,
                oldGeometry: document.querySelector(
                  '[data-overlay-id="council-current"]'
                ) !== null,
                historyCount: document.querySelector('#history-count').textContent,
              };
            }
            """,
            switched,
        )
        assert transition == {
            "historyRows": 0,
            "oldGeometry": False,
            "historyCount": "No observations yet",
        }
        assert page.locator(".history-empty").count() == 1


def test_pair_switch_rejects_old_pair_rows_even_if_server_surface_id_is_reused(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["market"]["symbol"] = "EUR/USD"
    initial["surface"]["semantic_identity"] = "incorrectly-reused-surface"
    for row in initial["overlays"]:
        row["symbol"] = "EUR/USD"
        row["timeframe"] = "M5"
        row["surface_semantic_identity"] = "incorrectly-reused-surface"

    switched = copy.deepcopy(initial)
    switched["revision"] = 43
    switched["market"]["symbol"] = "GBP/USD"
    switched["surface"].update(
        {
            # Reuse the bad server id deliberately. The market namespace and
            # row identity checks must still prevent old-pair geometry.
            "semantic_identity": "incorrectly-reused-surface",
            "frame_id": 43,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43",
        }
    )
    for row in switched["overlays"]:
        row["frame_id"] = 43

    with _dashboard_page(chromium_browser, initial) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="demand-current"]').count() == 1
        transition = page.evaluate(
            """
            payload => {
              window.renderOperatorState(payload);
              return {
                oldGeometry: document.querySelector('[data-overlay-id="demand-current"]') !== null,
                count: document.querySelector('#overlay-library-status').textContent,
              };
            }
            """,
            switched,
        )
        assert transition["oldGeometry"] is False
        page.wait_for_function(
            "() => document.querySelector('#surface-canvas').getAttribute('aria-busy') === 'false'",
            timeout=10_000,
        )
        assert page.locator('[data-overlay-id="demand-current"]').count() == 0
        assert "0 visible marks" in page.locator("#overlay-library-status").inner_text()


def test_current_surface_rejects_overlay_with_missing_identity_contract(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    malformed = copy.deepcopy(initial)
    malformed["revision"] = 43
    malformed["surface"]["frame_id"] = 43
    malformed["surface"]["primary_url"] = (
        "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=43"
    )
    malformed["surface"]["fallback_url"] = (
        "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=43"
    )
    malformed["surface"]["focus_url"] = malformed["surface"]["fallback_url"]
    for row in malformed["overlays"]:
        row["frame_id"] = 43
    demand = next(
        row for row in malformed["overlays"] if row["id"] == "demand-current"
    )
    demand.pop("surface_semantic_identity")
    demand.pop("instrument_identity_status")

    with _dashboard_page(chromium_browser, initial) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="demand-current"]').count() == 1
        page.evaluate("payload => window.renderOperatorState(payload)", malformed)
        page.wait_for_function(
            "() => document.querySelector('#surface-canvas').getAttribute('aria-busy') === 'false'",
            timeout=10_000,
        )
        assert page.locator('[data-overlay-id="demand-current"]').count() == 0
        assert page.locator('[data-overlay-id="council-current"]').count() == 1


def test_every_overlay_identity_dimension_fails_closed_independently(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    mutations = (
        ("missing symbol", "symbol", None),
        ("wrong symbol", "symbol", "GBP/USD"),
        ("missing timeframe", "timeframe", None),
        ("wrong timeframe", "timeframe", "M1"),
        ("missing frame", "frame_id", None),
        ("wrong frame", "frame_id", 99),
        (
            "missing selector",
            "market_selector_visual_fingerprint",
            None,
        ),
        (
            "wrong selector",
            "market_selector_visual_fingerprint",
            "selector_v3_gbp_usd",
        ),
        ("missing surface", "surface_semantic_identity", None),
        ("wrong surface", "surface_semantic_identity", "surface-other"),
        ("missing lock", "instrument_identity_status", None),
        ("unlocked", "instrument_identity_status", "UNPROVEN"),
    )

    with _dashboard_page(chromium_browser, initial) as page:
        page.locator("#layers-all").click()
        assert page.locator('[data-overlay-id="demand-current"]').count() == 1
        for label, key, value in mutations:
            malformed = copy.deepcopy(initial)
            demand = next(
                row
                for row in malformed["overlays"]
                if row["id"] == "demand-current"
            )
            if value is None:
                demand.pop(key)
            else:
                demand[key] = value
            page.evaluate(
                "payload => window.renderOperatorState(payload)", malformed
            )
            assert page.locator(
                '[data-overlay-id="demand-current"]'
            ).count() == 0, label
            assert page.locator(
                '[data-overlay-id="council-current"]'
            ).count() == 1, label
            page.evaluate(
                "payload => window.renderOperatorState(payload)", initial
            )
            assert page.locator(
                '[data-overlay-id="demand-current"]'
            ).count() == 1, label


def test_surface_selector_proof_missing_or_wrong_rejects_selector_bound_rows(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    malformed_cases: list[tuple[str, dict[str, Any]]] = []
    missing = copy.deepcopy(initial)
    missing["surface"].pop("market_selector_visual_fingerprint")
    malformed_cases.append(("missing surface selector", missing))
    wrong = copy.deepcopy(initial)
    wrong["surface"]["market_selector_visual_fingerprint"] = (
        "selector_v3_gbp_usd"
    )
    malformed_cases.append(("wrong surface selector", wrong))

    with _dashboard_page(chromium_browser, initial) as page:
        page.locator("#layers-all").click()
        baseline_count = page.locator(".surface-hotspot").count()
        assert baseline_count > 0
        for label, malformed in malformed_cases:
            page.evaluate(
                "payload => window.renderOperatorState(payload)", malformed
            )
            assert page.locator(".surface-hotspot").count() == 0, label
            page.evaluate(
                "payload => window.renderOperatorState(payload)", initial
            )
            assert page.locator(".surface-hotspot").count() == baseline_count, label


def test_pair_switch_supersedes_inflight_surface_and_clears_old_geometry(
    chromium_browser: Browser,
) -> None:
    initial = _operator_payload()
    initial["surface"]["semantic_identity"] = "surface-eur-usd-m5"
    for overlay in initial["overlays"]:
        overlay["surface_semantic_identity"] = "surface-eur-usd-m5"

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
            "market_selector_visual_fingerprint": "selector_v3_gbp_usd",
            "frame_id": 44,
            "primary_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-window?frame_id=44",
            "fallback_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=44",
            "focus_url": "/v1/mobile/window-tracker/sessions/operator-test/artifacts/latest-chart?frame_id=44",
        }
    )
    for overlay in switched["overlays"]:
        overlay["frame_id"] = 44
        overlay["symbol"] = "GBP/USD"
        overlay["market_selector_visual_fingerprint"] = "selector_v3_gbp_usd"
        overlay["surface_semantic_identity"] = "surface-gbp-usd-m5"
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
                decisionTitle: document.querySelector(
                  '#beginner-decision-title'
                ).textContent,
                actionLabel: document.querySelector(
                  '#beginner-action-label'
                ).textContent,
                overlayStatus: document.querySelector(
                  '#overlay-library-status'
                ).textContent,
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
        assert transition["decisionTitle"] == "STUDYING THIS CHART"
        assert transition["actionLabel"] == "WAIT FOR CURRENT READ"
        assert "Loading overlays for the selected chart" in transition[
            "overlayStatus"
        ]

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
