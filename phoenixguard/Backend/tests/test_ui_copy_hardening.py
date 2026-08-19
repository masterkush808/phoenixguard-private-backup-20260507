from __future__ import annotations
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DASHBOARD = _REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html"


def test_tracker_pressure_uses_server_authoritative_temporal_event() -> None:
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    assert "function rawLivePressureRead" not in dashboard
    assert "pressure_event" in dashboard
    assert 'pressureState === "ended"' in dashboard
    assert "studyDirection(pressure.direction || pressure.side)" in dashboard
    assert "function liveFormingRead(payload)" in dashboard
    assert "Live forming evidence remains provisional until candle close." in dashboard
    assert "Regression study · candle by candle" in dashboard
    assert "History never grants entry permission." in dashboard


def test_tracker_dashboard_exposes_only_market_clearance_panel() -> None:
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    assert 'id="current-move-title"' in dashboard
    assert 'id="permission-title"' in dashboard
    assert 'id="latent-control-rail"' not in dashboard
    assert 'aria-label="BUY and SELL hidden-state components"' not in dashboard
    assert "BUY COMPONENT" not in dashboard
    assert "SELL COMPONENT" not in dashboard
    assert 'id="frontline-qwen-panel" data-state="pending" data-verdict="pending"' in dashboard
    assert "Market analysis clearance" in dashboard
    assert "Trade clearance" in dashboard
    assert "BUY SIDE" in dashboard
    assert "SELL SIDE" in dashboard
    assert 'class="decision-questions legacy-three-question-panel"' in dashboard
    assert 'aria-label="Legacy decision questions" aria-hidden="true" hidden' in dashboard
    assert 'aria-label="The three live trading questions"' not in dashboard
    assert 'id="market-origin-question">Where is the market from, and how did history behave?' in dashboard
    assert 'id="direction-study-question">Which direction was studied, and what is being studied now?' in dashboard
    assert 'id="entry-now-question">What is the best decision to do right now?' in dashboard
    assert "function threeQuestionAnswers(payload, study, permission)" in dashboard
    assert "safeObject(safeObject(payload).three_questions)" in dashboard
    assert 'function entryWindowLabel(permission)' in dashboard
    assert 'function entryLocationGuidance(permission, action)' in dashboard
    assert '"Buy low · entry open"' in dashboard
    assert '"Sell high · entry open"' in dashboard
    assert '"Setup window · verifying"' in dashboard
    assert "The model has not confirmed a current entry." in dashboard
    assert "lower price inside the verified demand or retest area" in dashboard
    assert "higher price inside the verified supply or retest area" in dashboard
    assert "scheduleEntryExpiry(permission, freshness, action);" in dashboard
    assert "The verified entry window has expired." in dashboard
    assert "contract.valid_for_seconds" in dashboard
    assert "/v1/mobile/operator/state/v1/" in dashboard
    assert "function highFrequencyForecast" not in dashboard
    assert "function derivedHighFrequencyForecast" not in dashboard
    assert "<summary>Technical contracts and evidence</summary>" in dashboard
    assert "<span>Major and inner trend</span>" in dashboard
    assert "No current chart-verified order area is drawable yet; entry permission remains separate." in dashboard
    assert 'data-overlay-family="lstm"' not in dashboard
    assert 'data-overlay-family="scene_forecaster"' not in dashboard
    assert 'id="forecast-title"' not in dashboard
    assert "This is a forecast, not entry permission." not in dashboard
    assert "episodeOutlookOverlays" not in dashboard
    assert "/v1/mobile/live/state/v3/" not in dashboard


def test_tracker_dashboard_uses_sanitized_operator_overlays() -> None:
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    assert 'const OPERATOR_SCHEMA_VERSION = "PG_OPERATOR_WORKSPACE_V1";' in dashboard
    assert "const operatorOverlays = safeList(committedOperatorState.overlays)" in dashboard
    assert "overlayIdentityMatchesPayload(overlay, committedOperatorState)" in dashboard
    assert "state.overlays = operatorOverlays;" in dashboard
    assert "function lifecycleIsVisible(overlay)" in dashboard
    assert "function overlayMatchesSurface(overlay)" in dashboard
    assert "function overlayFamily(overlay)" in dashboard
    assert '["movement", "structure", "zones", "plan", "history"]' in dashboard
    assert '["movement", "structure", "zones", "plan", "outlook", "history"]' not in dashboard
    assert 'safeText(overlay.coordinate_space, "chart")' in dashboard
    assert "function renderOverlays()" in dashboard
    assert "episodeOutlookOverlays" not in dashboard
    assert "backendOverlayObjects" not in dashboard
    assert "runtime_telemetry" not in dashboard
    assert "broker_controls" not in dashboard


def test_share_overlay_demo_uses_operator_contract_without_host_paths() -> None:
    demo = (_REPO / "Frontend" / "assets" / "share" / "overlay_demo.html").read_text(encoding="utf-8")

    assert "/v1/mobile/operator/state/v1/" in demo
    assert "latestOperatorState" in demo
    assert "last_window_path" not in demo
    assert "last_frame_path" not in demo
    assert "last_chart_path" not in demo
    assert "last_overlay_path" not in demo
    assert "last_full_overlay_path" not in demo
    assert "file:///" not in demo


def test_backend_overlay_renderers_do_not_fill_chart_covering_boxes() -> None:
    root = Path(__file__).resolve().parents[2]
    tracker_source = (root / "Backend" / "src" / "phoenixguard" / "mobile_api" / "window_tracker.py").read_text(
        encoding="utf-8"
    )
    renderer_source = (root / "Backend" / "src" / "phoenixguard" / "vision" / "renderer.py").read_text(encoding="utf-8")

    assert "fill_alpha = 0" in tracker_source
    assert "draw.rounded_rectangle(clipped, radius=10, fill=None" in tracker_source
    assert "draw.rounded_rectangle(bbox, radius=radius, fill=None" in tracker_source
    assert "draw.rounded_rectangle(clipped, radius=radius, fill=None" in tracker_source
    assert "fill=_rgba(role_color, 30 if emphasized else 18)" not in tracker_source
    assert "draw.rectangle([x1, y1, x2, y2], outline=color, fill=None, width=2)" in renderer_source


def test_tracker_dashboard_uses_a_restrained_semantic_palette() -> None:
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    assert "--gold: #f2c866;" in dashboard
    assert "--accent: var(--gold);" in dashboard
    assert "--up: #63e09a;" in dashboard
    assert "--down: #ff654f;" in dashboard
    assert "--warn: #e8c878;" in dashboard
    assert "--ice: #6bc8ff;" in dashboard
    assert "--overlay-plan: #b99afc;" in dashboard
    assert "--overlay-history: #a7b0b8;" in dashboard
    assert ".surface-hotspot.buy {" in dashboard
    assert ".surface-hotspot.sell {" in dashboard
    assert "layer-broker-controls" not in dashboard
    assert "--overlay-demand-rgb" not in dashboard
    assert "--overlay-supply-rgb" not in dashboard


def test_tracker_dashboard_does_not_expose_overlay_tuning_editor() -> None:
    dashboard = DASHBOARD.read_text(encoding="utf-8")

    assert "overlay-editor" not in dashboard
    assert "OVERLAY_EDITOR" not in dashboard
    assert 'id="overlay-explorer"' in dashboard
    assert 'id="overlay-opacity" type="range"' in dashboard
    assert 'id="layers-all"' in dashboard
    assert 'id="layers-clear"' in dashboard
    assert 'data-overlay-family="market_context"' in dashboard
    assert '>Reaction map <' in dashboard
    assert 'data-overlay-family="playbook"' not in dashboard
    assert 'data-overlay-family="two_candle"' not in dashboard
    assert 'data-overlay-family="lstm"' not in dashboard
    assert 'data-label-mode="on"' in dashboard
    assert 'data-label-mode="hover"' in dashboard
    assert 'data-label-mode="off"' in dashboard
