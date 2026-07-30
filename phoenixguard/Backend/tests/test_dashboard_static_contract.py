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
    assert "RETIRED_FORECAST_FAMILIES" not in text
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
    assert 'const POLL_INTERVAL_MS = 2000;' in text
    assert 'ACTIVE_TRACKING_POLL_INTERVAL_MS' not in text
    assert 'scheduleRefresh(POLL_INTERVAL_MS);' in text
    assert 'document.hidden ? POLL_INTERVAL_MS * 4 : POLL_INTERVAL_MS' in text
    assert 'const POLL_INTERVAL_MS = 30000;' not in text
    assert "await refreshOperatorState();" in text
    assert "download the much larger raw session snapshot every two seconds" in text
    assert 'new window.EventSource(sessionStreamUrl())' in text
    assert '+ "/events";' in text
    assert 'source.addEventListener("SESSION_UPDATE"' in text
    assert "scheduleStreamRefresh(40);" in text
    assert 'window.addEventListener("pagehide", function () {' in text
    assert "closeSessionStream();" in text


def test_dashboard_commits_live_answers_before_the_broker_bitmap_finishes() -> None:
    text = _dashboard_text()

    assert "function commitLiveOperatorCopy(payload)" in text
    copy_start = text.index("function commitLiveOperatorCopy(payload)")
    surface_start = text.index("function loadSurface(payload, options)")
    image_request = text.index("els.surfaceImage.src = primaryUrl;", surface_start)
    live_copy = text.index("commitLiveOperatorCopy(payload);", surface_start)
    full_commit = text.index("function commitOperatorState(payload)")
    copy_contract = text[copy_start:surface_start]

    assert live_copy < image_request
    assert "state.payload = operatorState;" in copy_contract
    assert "renderTrackingStatus(operatorState);" in copy_contract
    assert "renderDecision(operatorState);" in copy_contract
    assert "renderHistory(operatorState);" in copy_contract
    assert "state.overlays =" not in copy_contract
    assert "state.overlays = operatorOverlays;" in text[full_commit:]


def test_dashboard_first_viewport_answers_exactly_three_plain_language_questions() -> None:
    text = _dashboard_text()

    assert 'class="decision-questions" aria-label="The three live trading questions"' in text
    assert text.count('class="decision-question" data-question=') == 3
    assert 'id="market-origin-question">Where is the market from, and how did history behave?</h3>' in text
    assert 'id="direction-study-question">Which direction was studied, and what is being studied now?</h3>' in text
    assert (
        'id="entry-now-question">What is the best decision to do right now?</h3>'
        in text
    )
    assert 'id="story-step-one-label">Question 1</span>' in text
    assert 'id="story-step-two-label">Question 2</span>' in text
    assert 'id="story-step-three-label">Question 3</span>' in text
    assert 'id="beginner-decision-title" aria-live="assertive">NO — NOT YET</h2>' in text
    assert '<summary>Technical contracts and evidence</summary>' in text

    assert 'id="current-move-title"' in text
    assert 'id="inner-trend-title"' in text
    assert 'id="permission-title"' in text
    assert 'id="pressure-event" data-state="none"' in text
    assert 'safeObject(safeObject(payload).three_questions)' in text
    assert '"market_origin_history"' in text
    assert '"studied_direction_current"' in text
    assert '"entry_now"' in text
    assert 'const answers = threeQuestionAnswers(payload, study, permission);' in text
    assert 'setText(els.beginnerDecisionTitle, answers.entry.headline);' in text
    assert "function marketRegressionStudy(payload)" in text
    assert "tracking.market_study_v3" in text
    assert "regressionContract.major_trend" in text
    assert "regressionContract.inner_trend" in text
    assert "behaviorContract.market_story" in text
    assert "function questionConfidence(value)" in text
    assert "numeric <= 0" in text
    assert "function completedCandleHistoryRows(payload)" in text
    assert "closed_candle_key" in text
    assert "function rememberCompletedStudyHistory(payload)" in text


def test_dashboard_keeps_continuous_observation_restrained_and_non_authoritative() -> None:
    text = _dashboard_text()
    lowered = text.lower()

    assert 'id="stream-observation" data-state="unknown" role="status" aria-live="polite"' in text
    assert 'id="stream-observation-label">Observation health unavailable</span>' in text
    assert 'id="stream-observation-detail">Stream health unavailable</strong>' in text
    assert "function renderStreamObservation(payload)" in text
    assert "function streamContract(payload)" in text
    assert "function liveFormingRead(payload)" in text
    assert "const stream = streamContract(payload);" in text
    assert "function applySessionStreamEvent(event)" in text
    assert "rememberCompletedStudyHistory(state.liveSession);" in text
    assert "function sessionSnapshotUrl()" in text
    assert "function refreshSessionSnapshot(options)" in text
    assert "function refreshPublicState(options)" in text
    assert '"DEGRADED_SNAPSHOT_FALLBACK"' in text
    assert "Never let lower-level pixel telemetry overrule its market_read." in text
    assert 'direction: "NEUTRAL"' in text
    assert "direction_available: false" in text
    assert 'label = "moving";' in text
    assert 'label = explicitSide === "BUY"' not in text
    assert 'running: "Continuous observation · live"' in text
    assert 'degraded: "Continuous observation · limited"' in text
    assert 'detail.push(formatStreamCount(stream.observed_frames) + " frames observed")' in text
    assert 'detail.push(formatStreamCount(stream.accepted_keyframes) + " keyframes accepted")' in text
    assert '.stream-observation[data-state="running"] .stream-observation-dot' in text
    assert "animation: status-pulse 2.4s ease-in-out infinite;" in text
    assert "@media (prefers-reduced-motion: reduce)" in text
    assert lowered.count('class="decision-question" data-question=') == 3
    assert "<video" not in lowered

    compact_status = text.index('id="stream-observation"')
    technical_details = text.index('<details class="evidence-details">')
    detail_metrics = text.index('id="stream-observation-detail"')
    details_end = text.index("</details>", technical_details)
    assert compact_status < technical_details < detail_metrics < details_end


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


def test_dashboard_permanently_retires_manual_sequence_tracking() -> None:
    text = _dashboard_text()
    lowered = text.lower()

    for retired_token in (
        'id="tracking-start"',
        'id="tracking-stop"',
        'id="tracking-reset"',
        'id="tracking-path-comparison"',
        'id="tracking-event-tape"',
        "/tracking-" + "episodes/",
        "pg_" + "tracking_" + "episode",
        "tracking" + "episode",
        "tracking" + " episode",
        "start " + "tracking",
        "stop &amp; save",
        "pending close",
        "frozen " + "route",
    ):
        assert retired_token not in lowered

    assert 'id="history-title">Regression study · candle by candle</h2>' in text
    assert "function marketRegressionStudy(payload)" in text
    assert "function decorateHistoryItems(items)" in text


def test_dashboard_individual_overlay_controls_are_real_filters() -> None:
    text = _dashboard_text()

    assert 'id="tracking-plan-toggle"' not in text
    assert 'id="tracking-plan-panel"' not in text
    assert 'data-overlay-family="playbook"' not in text
    assert 'id="detailed-overlay-controls"' in text
    assert 'id="detailed-overlay-list" aria-label="Individual overlay types"' in text
    assert "function overlayKind(overlay)" in text
    assert "function toggleOverlayKind(kind)" in text
    assert "&& overlayKindIsEnabled(overlay)" in text
    assert "function overlayIsDiagnostic(overlay)" in text
    assert "!overlayIsDiagnostic(overlay)" in text
    assert "function quickViewShowsOverlay(overlay)" in text
    assert "&& quickViewShowsOverlay(overlay)" in text
    assert 'data-overlay-family="market_context"' in text
    assert ">Reaction map <" in text


def test_dashboard_has_permanent_independent_order_area_controls() -> None:
    text = _dashboard_text()

    assert 'data-overlay-family="order_positioning"' in text
    assert ">Order areas <" in text
    for kind in (
        "precision_entry",
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
    assert "function overlayIsAvailableForControl(overlay)" in text
    assert "overlayKind(overlay) === token && overlayIsAvailableForControl(overlay)" in text
    assert 'live: ["current_candles", "market_context", "council", "order_positioning"]' in text
    assert 'history: ["history", "major_swings", "local_swings", "order_positioning"]' in text
    assert "function orderPositioningContext(overlay)" in text
    assert "function orderOriginStudyOverlay(overlay)" in text
    assert '"FORWARD_REACTION_WINDOW"' in text
    assert '"LATEST_COMPLETED_CANDLE"' in text
    assert 'syncOverlayButtons(state.overlays, {loading: true});' in text
    assert "Current reaction area · " in text
    assert "Earlier source area · " in text
    assert "update only with verified chart geometry" in text
    assert "Wait for price to reach a limit area" in text
    assert "Evidence only; entry permission remains separate." in text


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
