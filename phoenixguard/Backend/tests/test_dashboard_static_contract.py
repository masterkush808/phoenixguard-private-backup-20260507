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
    assert 'const PUBLIC_OVERLAY_VIEWS = new Set(["all", "live", "market_context", "structure", "zones", "plan", "forecast", "history"]);' in text
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
    for view in ("all", "live", "market_context", "structure", "zones", "plan", "forecast", "history"):
        assert f'data-overlay-view="{view}"' in text
    for family in (
        "chart_bounds",
        "current_candles",
        "major_swings",
        "local_swings",
        "supply_demand",
        "trendlines",
        "market_context",
        "council",
        "triggers",
        "targets",
        "invalidation",
        "two_candle",
        "scene_forecaster",
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
    assert "Every outlook studies 12 candle events" in text
    assert "A model outlook is evidence, never entry permission." in text
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
    assert "Selected visual route" in text
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


def test_dashboard_tracking_episode_controls_use_the_public_episode_contract() -> None:
    text = _dashboard_text()

    assert 'id="tracking-start" type="button" disabled>Start Tracking</button>' in text
    assert 'id="tracking-stop" type="button" hidden disabled>Stop &amp; save</button>' in text
    assert '+ "/tracking-episodes/"' in text
    assert 'const endpoint = action === "stop" ? "stop" : "start";' in text
    assert "row.event_horizon" in text
    assert "row.event_cursor" in text
    assert "safeObject(episode.baseline)" in text
    assert "safeObject(episode.current)" in text
    assert "safeObject(episode.plan)" in text
    assert "safeList(trackingEpisode(payload).events)" in text
    assert "Start Tracking to publish a 12-step outlook" in text
    assert "capture-worker" not in text
    for private_fallback in (
        "baseline_forecasts",
        "candidate_revision",
        "committed_plan",
        "committed_forecast",
        "baseline_snapshot",
        "current_snapshot",
    ):
        assert private_fallback not in text


def test_dashboard_tracking_plan_and_individual_overlay_controls_are_real_filters() -> None:
    text = _dashboard_text()

    assert 'id="tracking-plan-toggle"' in text
    assert ">Playbook overlays <" in text
    assert 'id="tracking-plan-panel"' in text
    assert 'data-overlay-family="playbook"' not in text
    assert 'id="detailed-overlay-controls"' in text
    assert 'id="detailed-overlay-list" aria-label="Individual overlay types"' in text
    assert "function overlayKind(overlay)" in text
    assert "function toggleOverlayKind(kind)" in text
    assert "&& overlayKindIsEnabled(overlay)" in text
    assert "function overlayIsDiagnostic(overlay)" in text
    assert "!overlayIsDiagnostic(overlay)" in text
    assert "function episodeAllowsOverlay(overlay, payload)" in text
    assert 'data-overlay-family="market_context"' in text
    assert ">Reaction map <" in text


def test_dashboard_sequence_outlook_renders_blocks_without_a_selected_path() -> None:
    text = _dashboard_text()

    assert 'const blockOnly = overlayFamily(overlay) === "lstm";' in text
    assert "const rawForecastCandles = safeList(overlay.forecast_candles);" in text
    assert "const blockAnchorMatches = Boolean(" in text
    assert "const explicitAnchorMatches = blockOnly" in text
    assert '"surface-forecast-composite forecast-" + visualStatus + (blockOnly ? " block-only" : "")' in text
    assert 'eventBlock.setAttribute("class", "surface-forecast-event-block");' in text
    assert 'group.dataset.displayMode = blockOnly' in text
    assert 'if (overlayFamily(overlay) === "lstm" && forecastRole === "center")' in text
    assert 'const line = overlayFamily(overlay) === "lstm" && forecastRole === "composite"' in text
    assert "Twelve future event blocks are anchored to the latest completed candle." in text


def test_dashboard_retains_episode_future_blocks_across_stale_poll_refreshes() -> None:
    text = _dashboard_text()

    assert "function episodeOutlookOverlays(operatorState)" in text
    assert "const futureBlocks = safeList(episode.future_blocks)" in text
    assert 'geometry_kind: "future_blocks"' in text
    assert "const operatorOverlays = episodeOutlookOverlays(operatorState);" in text


def test_dashboard_prefers_server_episode_history_over_local_fallback() -> None:
    text = _dashboard_text()

    assert "const episodeEvents = safeList(trackingEpisode(payload).events);" in text
    assert "const durableRows = serverRows.concat(episodeEvents);" in text
    assert "const sourceRows = durableRows.length ? durableRows : state.localHistory;" in text
    assert "row.event_id || row.id || row.episode_id" in text


def test_dashboard_source_contains_no_private_strategy_vocabulary() -> None:
    lowered = _dashboard_text().lower()

    for private_term in (
        "smc",
        "liquidity",
        "order block",
        "order_block",
        "fair value gap",
        "fair_value_gap",
        "fvg",
    ):
        assert private_term not in lowered
