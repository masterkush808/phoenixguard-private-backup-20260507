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

    assert '<body class="simple-view labels-hover">' in text
    assert 'id="beginner-decision-shell"' in text
    assert 'id="experience-mode-toggle" type="button" aria-pressed="false">Explore</button>' in text
    assert 'id="beginner-open-advanced" type="button">Explore the visual evidence</button>' in text
    assert 'class="workspace-nav"' not in text
    assert 'document.body.classList.contains("advanced-view")' not in text
    assert 'els.body.classList.toggle("advanced-view", next === "advanced")' in text
    assert 'els.experienceModeToggle.textContent = next === "simple" ? "Explore" : "Simple view";' in text


def test_dashboard_exposes_truthful_universal_chart_source_controls_before_decisions() -> None:
    text = _dashboard_text()

    source_index = text.index('id="source-control"')
    questions_index = text.index('id="beginner-decision-shell"')
    assert source_index < questions_index
    for element_id in (
        "source-control",
        "source-state",
        "source-label",
        "source-age",
        "source-message",
        "source-select",
        "source-kill",
    ):
        assert f'id="{element_id}"' in text
    assert "Ctrl+Shift+B" in text
    assert "Ctrl+Shift+K" in text
    assert 'function captureSourceContract(payload)' in text
    assert 'source.fresh === true' in text
    assert 'sourceState === "LIVE" && !fresh' in text
    assert 'Historical frame retained · stream interrupted' in text
    assert '+ "/source-control/kill";' in text
    assert 'method: "POST"' in text
    assert 'function enforceCaptureSourceDecision(payload)' in text
    assert 'sourceGuideUntilEpoch: 0' in text
    assert 'const guideActive = state.sourceGuideUntilEpoch > Date.now() / 1000;' in text


def test_dashboard_fails_loud_when_selected_source_never_sends_a_first_frame() -> None:
    text = _dashboard_text()

    assert "CAPTURE_SOURCE_FIRST_FRAME_TIMEOUT_FALLBACK_SECONDS" in text
    assert 'sourceState === "VALIDATING"' in text
    assert "lastFrameEpoch <= 0" in text
    assert "acceptedFrames <= 0" in text
    assert 'sourceState = "NO_FRAMES";' in text
    assert 'NO_FRAMES: "NO FRAMES"' in text
    assert "Phoenix Guard received no picture from it" in text
    assert "No source frame received · Current overlays unavailable" in text


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


def test_dashboard_posts_bounded_frame_matched_frontend_heartbeat() -> None:
    text = _dashboard_text()

    assert "const FRONTEND_HEARTBEAT_INTERVAL_MS = 5000;" in text
    assert 'return "/v1/mobile/frontend/heartbeat/v3";' in text
    assert 'return "/v1/mobile/performance/trace/v3?session_id="' in text
    assert "async function resolveHeartbeatOverlayVersion" in text
    assert "traceFrameId !== renderedFrameId" in text
    assert "async function sendFrontendHeartbeat" in text
    assert 'surface_id: "dashboard"' in text
    assert 'route: "live"' in text
    assert 'overlay_mode: "CLEAN_LIVE"' in text
    assert "overlay_state_version: version.version" in text
    assert "visible_overlay_count: current.visibleOverlayCount" in text
    assert "state.lastRenderedOverlayCount = renderedOverlayCount;" in text
    assert "if (state.heartbeatIntervalTimer)" in text
    assert "window.setInterval(function ()" in text
    assert "}, FRONTEND_HEARTBEAT_INTERVAL_MS);" in text
    assert "queueFrontendHeartbeat(0);" in text
    assert "stopFrontendHeartbeat();" in text
    assert "sendFrontendHeartbeat();" in text
    assert "await sendFrontendHeartbeat" not in text


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
    assert "function visualObservationView(payload)" in text
    assert 'status === "LIVE_FRAME_UNCHANGED"' in text
    assert '"Chart stream live · picture unchanged"' in text
    assert '"Interactive overlays · Live picture unchanged"' in text
    assert "function selectOverlay(overlay)" in text
    assert "function strictTrendlineOverlayAccepted(overlay)" in text
    assert "overlay.geometry_contract_accepted !== true" in text
    assert "if (!strictTrendlineOverlayAccepted(overlay))" in text
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


def test_dashboard_first_viewport_is_market_clearance_panel() -> None:
    text = _dashboard_text()

    assert 'class="decision-questions legacy-three-question-panel"' in text
    assert 'aria-hidden="true" hidden' in text
    assert '.legacy-three-question-panel {' in text
    assert 'display: none !important;' in text
    assert 'function renderLatentStateControl(payload)' in text
    assert 'renderLatentStateControl(payload);' in text
    assert 'class="frontline-qwen-panel" id="frontline-qwen-panel"' in text
    assert 'id="frontline-qwen-panel" data-state="pending" data-verdict="pending"' in text
    assert '>Market analysis clearance</h3>' in text
    assert 'id="frontline-qwen-verdict">PENDING</strong>' in text
    assert 'id="frontline-qwen-buy"' in text
    assert 'id="frontline-qwen-sell"' in text
    assert "function renderFrontlineQwen(payload, frontline)" in text
    assert "function renderFrontlineQwenMeta(qwen, payload)" in text
    assert "refreshFrontlineQwen(operatorState, {silent: true});" in text
    assert "async function refreshFrontlineQwen(payload, options)" in text
    assert '"/v1/mobile/frontline/latest/" + encodeURIComponent(SESSION_ID)' in text
    assert "function frontlineUrl() {" in text
    assert "analyst: " in text
    assert "frame: " in text
    assert "Trade clearance" in text
    assert 'safeObject(safeObject(payload).three_questions)' in text
    assert '"No lineage-matched timing source"' in text
    assert (
        '"The timing study is paused until the current pair, timeframe, and '
        'completed candle share one verified lineage.' in text
    )
    assert '"Clock anchored · " + horizonLabel' in text
    assert '"Timing basis: " + timingBasisLabel' in text
    assert 'const actionContract = safeObject(entryContract.operator_action);' in text
    assert "const projectionTimingUnproven = forecastIdentityMatches" in text
    assert (
        "const uncalibratedClosedCandleEstimateAvailable = forecastIdentityMatches"
        in text
    )
    assert "function timingForecastAdmissibility(" in text
    assert "timingAdmissibility.rangeAdmissible" in text
    assert 'sourceTier === "LIVE_M5_SEQUENCE"' in text
    assert 'sourceTier === "PAIR" || sourceTier.startsWith("PAIR_")' in text
    assert "forecast.timing_empirical === true" in text
    assert "timingSupportCount > 0" in text
    assert 'timeframe === "M5"' in text
    assert "horizonSecondsLow >= 900" in text
    assert "horizonSecondsHigh >= horizonSecondsLow" in text
    assert (
        'forecastSide + " uncalibrated closed-candle estimate · " + horizonLabel'
        in text
    )
    assert "Current M5 closed-candle sequence estimate." in text
    assert "Empirical pair-history closed-candle estimate" in text
    assert "Event probability unavailable;" in text
    assert "does not grant entry permission." in text
    assert 'forecastSide + " direction studied · timing range withheld"' in text
    assert "The candle range is not published and is not an entry signal." in text
    assert 'const actionHeadline = enterNow' in text
    assert 'headline: actionHeadline' in text
    assert 'forecastSummary: studyProjection' in text
    assert (
        'timingEventDefinition === "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"'
        in text
    )
    assert 'else if (!Object.keys(actionContract).length)' in text
    assert 'operatorActionState = activeTargetNextImpulse' in text
    assert "Do not chase the current move." in text
    assert 'function normalizeOperatorAction(value)' in text
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
    assert 'id="latent-buy-component"' not in text
    assert 'id="latent-sell-component"' not in text
    assert 'class="latent-component-grid"' not in text
    assert 'id="latent-control-side"' not in text


def test_dashboard_hides_live_hidden_state_evidence_audit() -> None:
    text = _dashboard_text()

    assert 'id="decision-audit-strip"' not in text
    assert "Hidden-state evidence" not in text
    assert "function passiveDecisionAudit(payload)" not in text
    assert "function renderPassiveDecisionAudit(payload)" not in text
    assert "renderPassiveDecisionAudit(payload);" not in text
    assert "pairDna.transition_support" not in text
    assert "distribution.normalized_entropy" not in text
    assert "audit.profitability_evidence_v3" not in text
    assert '"POSITIVE EV PROVEN"' not in text
    assert 'status === "STRUCTURALLY_CONFIRMED_CONTROL"' not in text
    assert 'candidateSide + " LOCAL LEG ONLY"' not in text
    assert 'directionalSide + " DOMINANT STRUCTURE"' not in text
    assert "Descriptive evidence only · never an entry instruction, trade, or permission." not in text
    assert 'id="frontline-qwen-panel" data-state="pending" data-verdict="pending"' in text
    assert "Trade clearance" in text
    assert "Analyst assessment" in text
    assert "BUY SIDE" in text
    assert "SELL SIDE" in text


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
    live_preset = text[text.index("live: [") : text.index("market_context: [")]
    for family in (
        "chart_bounds",
        "current_candles",
        "major_swings",
        "local_swings",
        "supply_demand",
        "trendlines",
        "market_context",
        "book_rules",
        "council",
        "order_positioning",
        "triggers",
        "targets",
        "invalidation",
    ):
        assert f'"{family}"' in live_preset
    assert '"history"' not in live_preset
    assert 'structure: ["current_candles", "major_swings", "local_swings", "trendlines", "book_rules"]' in text
    assert 'zones: ["supply_demand", "book_rules"]' in text
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


def test_labels_on_is_the_explicit_exhaustive_label_mode() -> None:
    text = _dashboard_text()

    assert 'labelMode: readStoredValue("phoenixguard.labels", "hover")' in text
    assert 'data-label-mode="hover" aria-pressed="true">On hover</button>' in text
    assert "function exhaustiveLabelModeActive()" in text
    assert 'return state.labelMode === "on";' in text
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
    lowered = _dashboard_text().split("<script", 1)[0].lower()

    for private_term in (
        "liquidity",
        "order block",
        "order_block",
        "fair value gap",
        "fair_value_gap",
        "fvg",
    ):
        assert private_term not in lowered


def test_dashboard_uses_backend_capture_source_stale_threshold() -> None:
    text = _dashboard_text()

    assert "source.stale_after_sec" in text
    assert "CAPTURE_SOURCE_STALE_FALLBACK_SECONDS" in text
    assert "frameAge <= staleAfterSeconds" in text
    assert "frameAge <= CAPTURE_SOURCE_STALE_AFTER_SECONDS" not in text


def test_dashboard_separates_live_capture_identity_and_analysis_readiness() -> None:
    text = _dashboard_text()

    assert "function chartIdentityView(payload)" in text
    assert "function operatorReadinessView(payload, sourceOverride)" in text
    assert 'state: "IDENTIFYING"' in text
    assert 'state: "ANALYZING"' in text
    assert '"CONFIRMING PAIR & TIMEFRAME"' in text
    assert '"ANALYZING LATEST FRAME"' in text
    assert '"STREAM INTERRUPTED"' in text
    assert '"WAIT FOR CURRENT READ"' in text
    assert '"DO NOT USE OLD SIGNAL"' in text
    assert "captureSourceTruthEpoch" in text
    assert 'reasonCode === "FRAME_PROCESSING"' in text
    assert "stream.processing === true" in text
    assert 'frameProcessing ? "ANALYZING"' in text
    assert "sourceView.transportActive" in text
    assert "Math.max(declaredAge" not in text
    assert "? declaredAge\n        : measuredAge;" in text
