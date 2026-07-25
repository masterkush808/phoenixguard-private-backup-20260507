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
    assert 'const PUBLIC_OVERLAY_VIEWS = new Set(["all", "live", "market_context", "structure", "zones", "plan", "history"]);' in text
    assert 'const RETIRED_FORECAST_FAMILIES = new Set(["two_candle", "scene_forecaster", "lstm", "prediction"]);' in text
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
    for view in ("all", "live", "market_context", "structure", "zones", "plan", "history"):
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
        "order_positioning",
        "triggers",
        "targets",
        "invalidation",
        "history",
    ):
        assert f'data-overlay-family="{family}"' in text
    assert 'id="layers-all"' in text
    assert 'id="layers-clear"' in text
    assert '>Forecasts</button>' not in text
    assert 'data-retired-surface="forecast-studies"' not in text
    assert 'id="run-forecast"' not in text
    assert 'id="show-future-path"' not in text
    assert 'if (stored === "forecast") {' in text
    assert 'writeStoredValue("phoenixguard.overlay.preset.v1", "live");' in text
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


def test_dashboard_removes_forecast_route_controls_and_uses_adaptive_fallback_polling() -> None:
    text = _dashboard_text()

    assert 'id="forecast-path-legend"' not in text
    assert 'id="tracking-route-svg"' not in text
    assert 'id="tracking-path-a"' not in text
    assert 'id="tracking-path-b"' not in text
    assert 'const POLL_INTERVAL_MS = 30000;' in text
    assert 'const ACTIVE_TRACKING_POLL_INTERVAL_MS = 5000;' in text
    assert 'scheduleRefresh(POLL_INTERVAL_MS);' in text
    assert 'document.hidden ? activeDelay * 4 : activeDelay' in text
    assert 'const POLL_INTERVAL_MS = 3000;' not in text
    assert 'new window.EventSource(sessionStreamUrl())' in text
    assert '+ "/events";' in text
    assert 'source.addEventListener("SESSION_UPDATE"' in text
    assert "scheduleStreamRefresh(40);" in text
    assert 'window.addEventListener("pagehide", function () {' in text
    assert "closeSessionStream();" in text


def test_dashboard_keeps_trend_regression_and_entry_permission_separate() -> None:
    text = _dashboard_text()

    assert 'id="current-move-title"' in text
    assert 'id="forecast-title"' in text
    assert 'id="permission-title"' in text
    assert 'id="story-step-one-label">Major trend</span>' in text
    assert 'id="story-step-two-label">Inner trend</span>' in text
    assert 'id="story-step-three-label">Regression study</span>' in text
    assert 'id="pressure-event" data-state="none"' in text
    assert 'const action = normalizeAction(permission.action);' in text
    assert 'setText(els.beginnerDecisionTitle, action === "WAIT" ? "CLOSED" : action);' in text
    assert "function marketRegressionStudy(payload)" in text
    assert "tracking.market_study_v3" in text
    assert "regressionContract.major_trend" in text
    assert "regressionContract.inner_trend" in text
    assert "behaviorContract.market_story" in text
    assert "Entry permission remains" in text


def test_dashboard_renders_retracement_support_as_observation_only_evidence() -> None:
    text = _dashboard_text()

    assert 'id="retracement-evidence" aria-live="polite"' in text
    assert "function retracementEvidenceSummary(studyContract)" in text
    assert "safeObject(studyContract).retracement_study" in text
    assert 'level.level_id, "").toUpperCase()' in text
    assert '"OTE_70_5", "CUSTOM_71_8"' in text
    assert "level.graph_support" in text
    assert "level.pair_dna_support" in text
    assert 'Object.prototype.hasOwnProperty.call(level, "pair_dna_support")' in text
    assert "level.visible_partition_support" not in text
    assert "70.5% OTE reference" in text
    assert "71.8% experimental/nonstandard" in text
    assert "awaiting completed graph and Pair DNA history" in text
    assert "full Pair DNA" in text
    assert 'full Pair DNA " + (ote.pairDna === null ? "unavailable"' in text
    assert "unavailable while the live workspace is offline" in text
    assert "Observation only; never entry permission." in text
    assert "retracement.can_grant_entry_permission === false" in text
    assert "setText(els.retracementEvidence, study.retracementEvidence);" in text


def test_dashboard_tracking_episode_controls_use_the_public_episode_contract() -> None:
    text = _dashboard_text()

    assert 'id="tracking-start" type="button" disabled>Start Tracking</button>' in text
    assert 'id="tracking-stop" type="button" hidden disabled>Stop &amp; save</button>' in text
    assert 'id="tracking-reset" type="button" hidden disabled' in text
    assert '+ "/tracking-episodes/"' in text
    assert 'const TRACKING_READINESS_SCHEMA_VERSION = "PG_TRACKING_EPISODE_READINESS_PUBLIC_V1";' in text
    assert '+ "/tracking-episodes/readiness";' in text
    assert "requestTrackingEpisodeReadiness()" in text
    assert "state.trackingReadiness = readiness;" in text
    assert 'reset: "reset"' in text
    assert 'runTrackingEpisodeAction("reset")' in text
    assert "row.event_horizon" in text
    assert "row.event_cursor" in text
    assert "safeObject(episode.baseline)" in text
    assert "safeObject(episode.current)" in text
    assert "safeObject(episode.plan)" in text
    assert "safeList(trackingEpisode(payload).events)" in text
    assert 'id="tracking-anchor-title"' in text
    assert 'id="tracking-forecast-title"' in text
    assert 'id="tracking-watch-title"' in text
    assert 'id="tracking-live-updated"' in text
    assert 'id="tracking-event-tape"' in text
    assert "safeList(safeObject(episode).events)" in text
    assert "safeList(episode.future_blocks)" in text
    assert '"Rest / range"' in text
    assert '"Up continuation"' in text
    assert 'detail.textContent = "Pending close";' in text
    assert 'title: "Reacquiring E" + next' in text
    assert "commitRealtimeTracking(operatorState);" in text
    assert text.index("commitRealtimeTracking(operatorState);") < text.index(
        "loadSurface(operatorState);"
    )
    assert "const ACTIVE_TRACKING_POLL_INTERVAL_MS = 5000;" in text
    assert "? ACTIVE_TRACKING_POLL_INTERVAL_MS" in text
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


def test_dashboard_retires_the_dual_route_surface_but_keeps_contract_compatibility() -> None:
    text = _dashboard_text()

    assert 'const TRACKING_PATH_COMPARISON_SCHEMA = "PG_TRACKING_PATH_COMPARISON_PUBLIC_V1";' in text
    assert 'id="tracking-path-comparison"' in text
    assert ">Candle-by-candle regression study<" in text
    assert 'id="tracking-route-plot"' not in text
    assert 'id="tracking-path-rows"' not in text
    assert ">Two frozen forecast routes<" not in text
    assert "safeObject(safeObject(episode).path_comparison)" in text
    assert 'safeObject(event.path_fit_by_id)' in text
    assert "event.observed_close_level" in text
    assert "safeList(path.points)" in text
    assert "function trackingRoutePlotGeometry(comparison, events)" in text
    assert "function renderTrackingRoutePlot(comparison, events)" in text
    assert "svgPointList(geometry.pathA)" in text
    assert "svgPointList(geometry.pathB)" in text
    assert "[geometry.anchor].concat" in text
    assert 'safeText(event.favored_path_id, "").toUpperCase()' in text
    assert 'PATH_A: {state: "path-a", title: "Path A favored"}' in text
    assert 'PATH_B: {state: "path-b", title: "Path B favored"}' in text
    assert 'TOO_CLOSE: {state: "too-close", title: "Too close"}' in text
    assert 'PATHS_OVERLAP: {state: "too-close", title: "Paths overlap"}' in text
    assert 'NEITHER_PATH_FITS: {state: "no-fit", title: "Neither path fits"}' in text
    assert 'GEOMETRY_UNAVAILABLE: {state: "unavailable", title: "Forecast routes unavailable"}' in text
    assert 'WAITING: {state: "unknown", title: "Waiting for evidence"}' in text
    assert 'id="tracking-entry-title"' in text
    assert 'id="tracking-entry-permission"' in text
    assert 'id="tracking-continuity-guidance"' in text
    assert "safeObject(rawComparison.anchor)" in text
    assert "safeObject(rawComparison.forming_at_start)" in text
    assert 'rawAnchorStatus === "CONFIRMED"' in text
    assert 'rawFormingStatus === "OBSERVED"' in text
    assert '"E1 is the candle that was live when Start Tracking was pressed.' in text
    assert "safeObject(rawComparison.trade_permission)" in text
    assert '["PERMITTED", "WAIT"].includes(rawTradePermissionStatus)' in text
    assert "safeObject(rawComparison.entry_location)" in text
    assert 'id="tracking-entry-progress"' in text
    assert "event.entry_location_progress" in text
    for progress_state in (
        "INSIDE",
        "APPROACHING",
        "MOVED_AWAY",
        "OUTSIDE",
        "CONFIRMED",
        "INVALIDATED",
        "UNKNOWN",
    ):
        assert f'"{progress_state}"' in text
    assert "setTrackingPathFocus(button.dataset.pathId);" in text
    assert '.tracking-path-comparison[data-has-focus="true"] .tracking-path-row[aria-pressed="false"]' in text
    assert "fit.number" not in text
    assert "fit.error" not in text
    assert "tracking-path-step" not in text


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


def test_dashboard_has_permanent_independent_order_area_controls() -> None:
    text = _dashboard_text()

    assert 'data-overlay-family="order_positioning"' in text
    assert ">Order areas <" in text
    for kind in (
        "lower_price_buy_area",
        "higher_price_sell_area",
        "upside_break_area",
        "downside_break_area",
        "plan_failure_area",
    ):
        assert f'data-overlay-kind-control="{kind}"' in text
        assert f'"{kind}"' in text
    assert 'document.querySelectorAll("[data-overlay-kind-control]")' in text
    assert "toggleOverlayKind(button.dataset.overlayKindControl);" in text
    assert 'live: ["current_candles", "market_context", "council", "order_positioning"]' in text
    assert 'history: ["history", "major_swings", "local_swings", "order_positioning"]' in text
    assert "function orderPositioningContext(overlay)" in text
    assert "function orderOriginStudyOverlay(overlay)" in text
    assert '"FORWARD_REACTION_WINDOW"' in text
    assert '"LATEST_COMPLETED_CANDLE"' in text
    assert 'syncOverlayButtons(state.overlays, {loading: true});' in text
    assert "Current reaction area · " in text
    assert "Earlier source area · " in text
    assert "does not slide to chase price" in text
    assert "Evidence only; entry permission remains separate." in text


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


def test_show_all_with_labels_on_is_the_explicit_exhaustive_label_mode() -> None:
    text = _dashboard_text()

    assert "function exhaustiveLabelModeActive()" in text
    assert 'state.labelMode === "on" && state.overlayView === "all"' in text
    assert 'els.body.classList.toggle("labels-show-all", exhaustiveLabelModeActive());' in text
    assert "body.labels-on.labels-show-all .surface-hotspot.label-policy-hidden span" in text
    assert "body.labels-on.labels-show-all .surface-hotspot.label-collision-hidden span" in text
    assert "const showEveryLabel = exhaustiveLabelModeActive();" in text
    assert "if (!showEveryLabel && accepted.some" in text


def test_session_history_renders_regression_major_inner_and_behavior_fields() -> None:
    text = _dashboard_text()

    assert "function decorateHistoryItems(items)" in text
    assert "rowRegression.major_trend" in text
    assert "rowRegression.inner_trend" in text
    assert "rowBehavior.current_state" in text
    assert 'major.className = "history-major-trend";' in text
    assert 'inner.className = "history-inner-trend";' in text
    assert 'regression.className = "history-regression";' in text
    assert 'side.textContent = item.behavior === "CONTINUATION"' in text
    assert '"Regression match"' in text
    assert '"REST"' in text


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
