from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html"


def _dashboard_text() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_dashboard_preserves_808fx_brand_identity_and_gold_visual_system() -> None:
    text = _dashboard_text()

    assert "<title>808Fx Standard Hybrid System Live Tracker</title>" in text
    assert '<h1 class="brand-title">808Fx Standard Hybrid System</h1>' in text
    assert '<p class="brand-subtitle">Powered by the Phoenix Guard Engine V3</p>' in text
    assert 'aria-label="808Fx Standard Hybrid System powered by the Phoenix Guard Engine V3"' in text
    assert "PhoenixGuard Vision" not in text
    assert "Powered by the Phoenix Guard V3 engine" not in text
    assert "--canvas: #080806;" in text
    assert "--gold: #f2c866;" in text
    assert "--gold-deep: #b87928;" in text
    assert "--accent: var(--gold);" in text
    assert "--ice: #6bc8ff;" in text
    assert "--overlay-plan: #b99afc;" in text
    assert "--overlay-history: #a7b0b8;" in text
    assert "linear-gradient(92deg, #fff7dc 0%, var(--gold) 38%, #d98a31 72%, #fff0bf 100%)" in text
    assert "-webkit-background-clip: text;" in text


def test_dashboard_defaults_to_simple_operator_workspace_without_technical_navigation() -> None:
    text = _dashboard_text()

    assert '<body class="simple-view labels-on">' in text
    assert 'id="beginner-decision-shell"' in text
    assert 'id="experience-mode-toggle" type="button" aria-pressed="false">Explore</button>' in text
    assert 'id="beginner-open-advanced" type="button">Explore the visual evidence</button>' in text
    assert 'class="workspace-nav"' not in text
    assert 'document.body.classList.contains("advanced-view")' not in text
    assert 'els.body.classList.toggle("advanced-view", next === "advanced")' in text
    assert 'els.experienceModeToggle.textContent = next === "simple" ? "Explore" : "Simple view";' in text


def test_dashboard_consumes_only_the_public_operator_workspace_contract() -> None:
    text = _dashboard_text()

    assert "PG_OPERATOR_WORKSPACE_V1" in text
    assert 'return "/v1/mobile/operator/state/v1/" + encodeURIComponent(SESSION_ID)' in text
    assert 'const PUBLIC_OVERLAY_VIEWS = new Set(["all", "live", "smc", "structure", "zones", "plan", "forecast", "history"]);' in text
    assert '"?view=" + encodeURIComponent(publicView)' in text
    assert 'const publicView = "all";' in text
    assert "ACTIVE_CONTEXT" not in text
    assert "FULL_HISTORY_READ" not in text
    assert "window.renderOperatorState = renderOperatorState;" in text
    assert "window.PhoenixGuardDashboard" in text


def test_dashboard_has_no_internal_telemetry_tuning_or_export_surfaces() -> None:
    text = _dashboard_text().lower()
    forbidden = (
        "/v1/mobile/model-council/health",
        "/v1/mobile/runtime/trace",
        "/execution/latest",
        "runtimetelemetryurl",
        "enrichsessiontelemetry",
        "runtime_telemetry",
        "frame_timing_trace",
        "model_council_result",
        "export-packet",
        "overlay-editor",
        "model-strength",
        "floating-windows",
        'id="system-time"',
        'id="system-uptime"',
        'id="system-heartbeat"',
        'id="latency-pipeline"',
        'id="latency-overlay"',
        'id="latency-budget"',
        'id="model-health-panel"',
        'class="telemetry-module"',
        'data-panel-route="bridge"',
        'data-panel-route="security"',
        'option value="debug"',
        'option value="deep_debug"',
        'option value="inspector"',
    )

    for token in forbidden:
        assert token not in text


def test_dashboard_exposes_plain_interactive_overlay_and_freshness_controls() -> None:
    text = _dashboard_text()

    assert 'id="overlay-explorer" aria-label="Overlay views"' in text
    for view in ("all", "live", "smc", "structure", "zones", "plan", "forecast", "history"):
        assert f'data-overlay-view="{view}"' in text
    for family in (
        "chart_bounds",
        "current_candles",
        "major_swings",
        "local_swings",
        "supply_demand",
        "trendlines",
        "smc",
        "council",
        "triggers",
        "targets",
        "invalidation",
        "two_candle",
        "lstm",
        "prediction",
        "history",
    ):
        assert f'data-overlay-family="{family}"' in text
    assert 'id="layers-all"' in text
    assert 'id="layers-clear"' in text
    assert 'id="run-forecast" type="button">Run forecast</button>' in text
    assert 'id="show-future-path" type="button">Show future path</button>' in text
    assert 'id="forecast-action-status" data-state="idle" role="status" aria-live="polite"' in text
    assert 'method: "POST"' in text
    assert 'mode === "future" ? "show-future" : "predict"' in text
    assert 'setOverlayView("forecast", {fetch: false});' in text
    assert "Every path shows 12 candle events" in text
    assert "NO EDGE is never entry permission." in text
    assert 'phoenixguard.overlay.layers.v1' in text
    assert 'id="visual-evidence-status" data-source="chart" data-freshness="updating" aria-live="polite"' in text
    assert 'id="overlay-inspector" aria-live="polite"' in text
    assert 'id="inspector-explanation"' in text
    assert "function updateVisualStatus(source, freshness, label)" in text
    assert "function selectOverlay(overlay)" in text
    assert 'button.setAttribute("aria-pressed", active ? "true" : "false");' in text
    assert '+ (points.length >= 2 ? " line-hit" : "")' in text
    assert '.surface-hotspot.line-hit {' in text
    assert "refreshOperatorState({force: true});" in text


def test_dashboard_explains_forecast_ranges_and_uses_a_30_second_fallback_poll() -> None:
    text = _dashboard_text()

    assert 'id="forecast-path-legend" role="list" aria-label="Forecast path legend"' in text
    assert "Selected 12-step path" in text
    assert "Bullish route" in text
    assert "Bearish route" in text
    assert "Green and red are alternative studied routes, not odds." in text
    assert "Wider route separation means less agreement" in text
    assert 'const POLL_INTERVAL_MS = 30000;' in text
    assert 'scheduleRefresh(POLL_INTERVAL_MS);' in text
    assert 'document.hidden ? POLL_INTERVAL_MS * 4 : POLL_INTERVAL_MS' in text
    assert 'const POLL_INTERVAL_MS = 3000;' not in text
    assert 'new window.EventSource(sessionStreamUrl())' in text
    assert '+ "/events";' in text
    assert 'source.addEventListener("SESSION_UPDATE"' in text
    assert "scheduleStreamRefresh(40);" in text
    assert 'window.addEventListener("pagehide", closeSessionStream);' in text


def test_dashboard_keeps_current_movement_forecast_and_permission_separate() -> None:
    text = _dashboard_text()

    assert 'id="current-move-title"' in text
    assert 'id="forecast-title"' in text
    assert 'id="permission-title"' in text
    assert 'id="pressure-event" data-state="none"' in text
    assert 'const action = normalizeAction(permission.action);' in text
    assert 'return "WAIT";' in text
    assert 'currentSide === "BUY"' in text
    assert 'pressureState === "ended"' in text
    assert "Past observations stay in history. They never overwrite the current move or regain entry permission." in text
