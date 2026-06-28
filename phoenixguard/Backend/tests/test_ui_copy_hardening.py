from __future__ import annotations
import pytest

import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


def test_cross_checks_panel_stays_operator_safe() -> None:
    source_image = Image.new("RGB", (64, 48), color=(14, 18, 22))
    html = main.build_model_council_html(
        {
            "local_ensemble": {
                "models": {
                    "dinov2": {
                        "name": "dinov2",
                        "role": "structure_specialist",
                        "live_enabled": True,
                        "predicted_label": "BUY",
                        "confidence": 0.83,
                        "dynamic_weight": 0.91,
                        "routing_alignment": 0.77,
                    }
                },
                "ensemble": {
                    "predicted_label": "BUY",
                    "confidence": 0.83,
                    "margin": 0.19,
                    "consensus_ratio": 0.75,
                    "disagreement": 0.08,
                    "router_direction": "BUY",
                    "router_strength": 0.72,
                    "router_uncertainty": 0.24,
                    "router_regime_confidence": 0.68,
                },
                "selection": {
                    "selected_models": ["dinov2"],
                    "budget": 1,
                    "reason": "structure_route",
                },
            },
            "model_council": {"source": "inline", "status": "ready"},
        },
        source_image,
    )

    assert "Cross-Checks" in html
    assert "deeper second opinion" in html
    lowered = html.lower()
    for term in ("yolo", "grad-cam", "hidden-attention", "backend", "adapter"):
        assert term not in lowered


def test_outcome_review_panel_hides_learning_engine_terms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")
    monkeypatch.setattr(main.RUNTIME, "enable_feedback_learning_feed", True)

    html = main.build_learning_feed_html()

    assert "Outcome Review" in html
    lowered = html.lower()
    for term in ("rl", "replay", "checkpoint", "buffer"):
        assert term not in lowered


def test_setup_guide_copy_stays_manual_and_non_intrusive() -> None:
    html = main.build_setup_guide_dialog_html()

    assert "Open this any time from the workspace guides" in html
    assert "forced walkthrough" in html
    assert "opens once on first load" not in html


def test_ui_head_does_not_auto_open_setup_guide() -> None:
    shell_html = main.build_workspace_shell_bar_html().lower()
    lowered = main.UI_HEAD.lower()

    assert "show overview" in shell_html
    assert "maybeopensetupguide" not in lowered
    assert "opendialog('setup-guide')" not in lowered


def test_tracker_live_pressure_is_not_execution_hold_copy() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "function rawLivePressureRead" in dashboard
    assert "function rawLivePressureSide" not in dashboard
    assert "signal.execution_action\n        || signal.action" not in dashboard
    assert 'livePressure: "HOLD"' not in dashboard
    assert '["Live pressure", story.livePressure, story.livePressureNote]' in dashboard


def test_tracker_dashboard_exposes_next_two_candle_forecast() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "Next 2 Candles" in dashboard
    assert "function highFrequencyForecast" in dashboard
    assert "function derivedHighFrequencyForecast" in dashboard
    assert "dashboard_fallback" in dashboard
    assert "high_frequency_forecast" in dashboard
    assert "microForecastHeadline" in dashboard
    assert "Two-Candle Study" in dashboard
    assert "LSTM Study" in dashboard
    assert "overlay-lstm-study" in dashboard
    assert "TEXT_AND_BANDS_ONLY" in dashboard
    assert "do_not_render_synthetic_candles" in dashboard
    assert "/v1/mobile/live/state/v3/" in dashboard
    assert "function applyInspectorMode" in dashboard
    assert "Study Output | TEXT_AND_BANDS_ONLY" in dashboard
    assert "MEMORY MATCH" in dashboard


def test_tracker_dashboard_uses_backend_overlay_objects_for_live_overlays() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "renderableCount > 0" in dashboard
    assert "staleStatus === \"PASS\"" in dashboard
    assert "backendOverlayObjects" in dashboard
    assert "overlayPayload.objects" in dashboard
    assert "const backendOverlayObjects = Array.isArray(overlayPayload.objects) ? overlayPayload.objects : [];" in dashboard
    assert "_backendOverlay: true" in dashboard
    assert "function rememberOverlayLock" in dashboard
    assert "function lockedOverlayBoxes" in dashboard
    assert "function sessionInstrumentContext" in dashboard
    assert "function sessionSymbolContext" in dashboard
    assert "function instrumentInvalidationKey" in dashboard
    assert "instrument.instrument_id" in dashboard
    assert "INSTRUMENT_LOCKED" in dashboard
    assert "windowArtifactFrame || newestDisplayFrame(session)" in dashboard
    assert "/artifacts/files/" in dashboard
    assert "artifactFileNameFromPath" in dashboard
    assert "instrument.invalidation_reason" in dashboard
    assert "frameNumber(modePayload.artifact_frame_id)" in dashboard
    assert "frameNumber(modePayload.overlay_object_frame_id)" in dashboard
    assert "function overlayRenderableInCurrentView" in dashboard
    assert "function overlayModeAllows" in dashboard
    assert "function overlayTypeAllowedInMode" in dashboard
    assert "function applyFrontendOverlayModeBudget" in dashboard
    assert "CLEAN_LIVE: {objects: null, labels: 9}" in dashboard
    assert "DASHBOARD_REFRESH_FAST_INTERVAL_MS = 15000" in dashboard
    assert "DASHBOARD_HEARTBEAT_INTERVAL_MS = 15000" in dashboard
    assert "function frontendHeartbeatDisabled" in dashboard
    assert "pg_no_heartbeat" in dashboard
    assert "if (frontendHeartbeatDisabled())" in dashboard
    assert "function frontendOverlayLabelCandidate" in dashboard
    assert 'clean_live: "CLEAN_LIVE"' in dashboard
    assert "function backendObjectOverlayReady" in dashboard
    assert "function currentChartTransformId" in dashboard
    assert "function transformFrameFromId" in dashboard
    assert "function chartTransformCandidate" in dashboard
    assert "const authorityFrame = overlayAuthorityFrame(session);" in dashboard
    assert "candidate.frame > 0 && candidate.frame === authorityFrame" in dashboard
    assert "return `ct_${clean(session.session_id || SESSION_ID, SESSION_ID)}_${authorityFrame}`;" in dashboard
    assert "chartTransformKey || \"CHART_TRANSFORM_PENDING\"" in dashboard
    assert "chartTransformId: currentChartTransformId(session)" in dashboard
    assert "const currentChartTransform = currentChartTransformId(session);" in dashboard
    assert "chart_transform_id: chartTransformId" in dashboard
    assert "payload.chart_transform_id" in dashboard
    assert "const dynamicOverlayReady = (normalizedKind === \"overlay\" || normalizedKind === \"full-overlay\")" in dashboard
    assert "visible_artifact_kind" in dashboard
    assert "visible_image_src" in dashboard
    assert "const fullOverlayVisibleCount = Math.max(boxes.length, backendRenderableOverlayCount(session));" in dashboard
    assert "function interactiveDomOverlaySurfaceRequired" in dashboard
    assert 'useSurfaceImage(els.rawImg, "window", "window-dom-overlay", true);' in dashboard
    assert "function frontendOverlayBoundsAllowed" in dashboard
    assert '["PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT", "IMPULSE_BOX", "RETEST_BOX", "TRIGGER_BOX", "TRIGGER_ZONE"].includes(type)' in dashboard
    assert 'SUPPLY_DEMAND: new Set(["SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"])' in dashboard
    assert 'TRENDLINES: new Set(["SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"])' in dashboard
    assert "function isLineOverlay" in dashboard
    assert "function renderLineOverlay" in dashboard
    assert "surface-line-svg" in dashboard
    assert "surface-line-hotspot" in dashboard
    assert "function overlayDisplayState" in dashboard
    assert "function overlayVisualWeight" in dashboard
    assert "function overlayFillCeiling" in dashboard
    assert "function overlayRenderedAreaRatio" in dashboard
    assert "Number.isFinite(requestedFill)" in dashboard
    assert "fillScale: boundedSetting(saved.fillScale, OVERLAY_EDITOR_DEFAULTS.fillScale, 0, 1.00)" in dashboard
    assert "function applyOverlayDisplayStyle" in dashboard
    assert "function applyOverlayLabelPosition" in dashboard
    assert "display-ghosted" in dashboard
    assert "display-inspector-only-label" in dashboard
    assert "dataset.displayState" in dashboard
    assert "dataset.visualWeight" in dashboard
    assert "INSPECTOR_ONLY_LABEL" in dashboard
    assert "SUPPORT_TRENDLINE" in dashboard
    assert "RESISTANCE_TRENDLINE" in dashboard
    assert "INNER_TRENDLINE" in dashboard
    assert 'const operatorHiddenTypes = new Set(["RETEST_BOX", "TRIGGER_BOX", "TRIGGER_ZONE", "PREDICTION_PATH", "ANGLE_VECTOR"])' in dashboard
    assert 'TRIGGER: new Set(["SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX"])' in dashboard
    assert "function payloadMatchesSelectedOverlayMode" in dashboard
    assert ".surface-hotspot.label-hidden span" in dashboard
    assert "const labelHidden = box.label_hidden === true || box.label_hidden === \"true\";" in dashboard
    assert 'button.innerHTML = effectiveLabelHidden ? "" : `<span>${escapeHtml(label)}</span>`;' in dashboard
    assert 'global: "GLOBAL"' in dashboard
    assert 'local: "LOCAL"' in dashboard
    assert 'supply_demand: "SUPPLY_DEMAND"' in dashboard
    assert 'trendlines: "TRENDLINES"' in dashboard
    assert 'triggers: "TRIGGER"' in dashboard
    assert 'targets: "TARGET"' in dashboard
    assert 'full_history_read: "FULL_HISTORY_READ"' in dashboard
    assert 'broker: "BROKER"' in dashboard
    assert "target_zones: true" in dashboard
    assert "trendlines" in dashboard
    assert "layer-trendlines" in dashboard
    assert "prediction_path: false" in dashboard
    assert 'mode === "REPLAY"' in dashboard
    assert "state.layers[layer] === false" in dashboard
    assert "row.visible_default === false" in dashboard
    assert "row.precision_rejected === true" in dashboard
    assert 'const currentCandleLiveModes = new Set(["CLEAN_LIVE", "CANDLES", "LOCAL", "ACTIVE_CONTEXT"]);' in dashboard
    assert '!currentCandleLiveModes.has(activeMode)' in dashboard
    assert "rememberOverlayLock(surfaceIdentityKey(session), renderableBoxes, session);" in dashboard
    assert "bridgeSelectedModeWhileHydrating" in dashboard
    assert "ignoreVisibleModes: bridgeSelectedModeWhileHydrating" in dashboard
    assert "if (trustBackendMode) {\n            rememberOverlayLock" in dashboard
    assert "const lockedBoxes = lockedOverlayBoxes(session);" in dashboard
    assert "backendMode: backendOverlayMode(state.overlayMode)" in dashboard
    assert "payloadMatchesSelectedOverlayMode(session)" in dashboard
    assert "if (!diagnosticsViewActive()) {\n        return [];\n      }" in dashboard
    assert "clearModeScopedOverlayDom" in dashboard
    assert "updateLayerControls();\n      renderHotspots();\n      refreshLiveVisualStateForMode(state.overlayMode);" in dashboard
    assert "useLockedWindowOverlayPlane" in dashboard
    assert "window-locked-overlay" in dashboard
    assert "if (wantsOverlay && hasFullOverlay && !overlayStale)" in dashboard
    assert "} else if (useLockedWindowOverlayPlane)" in dashboard
    assert "function artifactAvailable" in dashboard
    assert "if (normalizedKind === \"window\" && fileName)" in dashboard
    assert "if (normalizedKind === \"full-overlay\" && fullOverlayUsesSavedArtifact())" in dashboard
    assert "function dynamicOverlayLayerSuffix" in dashboard
    assert "function applyLayerButtonMode" in dashboard
    assert "return `${sessionUrl()}/artifacts/files/${encodeURIComponent(fileName)}?v=${version}`;" in dashboard
    assert "artifact.exists === false" in dashboard
    assert "failedArtifactKeys" in dashboard
    assert "function handleSurfaceImageError" in dashboard
    assert "function pendingSurfaceImage" in dashboard
    assert "pendingSurfaceImageMatches" in dashboard
    assert "if (state.session && !surfaceHasImage() && pendingSurfaceImage())" in dashboard
    assert "surfaceImageLoading(targetImage)" in dashboard
    assert "liveRefreshBusy" in dashboard
    assert "streamHydrationBusy" in dashboard
    assert "(!backendObjectsAvailable && state.surface.overlayStale)" in dashboard
    assert "pendingSurfaceImage() && !surfaceHasImage()" in dashboard
    assert "surfaceCriticalLoad" in dashboard
    assert "overlayDedupKey" in dashboard
    assert "[overlayId, overlayLayer, normalizedBoxKey(overlayBounds)].join(\"|\")" in dashboard
    assert "const overlayForRender = {...overlay, bbox: overlayBounds, bounds: overlayBounds};" in dashboard
    assert "const boxes = getBoxes(session);" in dashboard
    assert "renderSession(await enrichSessionTelemetry(await mergeSelectedLiveState(payload)));" in dashboard
    assert "mergeLiveVisualState(state.session || {session_id: SESSION_ID}, livePayload)" in dashboard
    assert '["window", "overlay", "full-overlay", "chart"].includes(normalized)' in dashboard
    assert "imageSourceChanged" in dashboard
    assert "dataset.loadedSrc" in dashboard
    assert "overlayArtifactFrame(session)" in dashboard
    assert "frameNumber(session.full_overlay_frame_id)" in dashboard
    assert "frameNumber(overlaysPayload.artifact_frame_id)" in dashboard
    assert "renderSessionImmediate(payload);" in dashboard


def test_tracker_dashboard_chart_artifacts_do_not_reuse_candle_green_red_palette() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )
    overlay_editor_css = (
        _REPO / "Frontend" / "dashboard" / "static" / "floating_windows" / "overlay_editor.css"
    ).read_text(encoding="utf-8")

    assert "--overlay-demand-rgb: 78, 210, 255;" in dashboard
    assert "--overlay-supply-rgb: 248, 202, 92;" in dashboard
    assert "--overlay-trigger-rgb: 185, 154, 255;" in dashboard
    assert ".surface-trendline.trendline-support {\n      stroke: rgba(var(--overlay-demand-rgb), 0.98);" in dashboard
    assert ".surface-trendline.trendline-resistance {\n      stroke: rgba(var(--overlay-supply-rgb), 0.98);" in dashboard
    assert ".surface-hotspot.sell.layer-trigger-zones {\n      border-color: rgba(var(--overlay-trigger-rgb), 0.72);" in dashboard
    assert ".surface-hotspot.buy.layer-broker-controls {\n      border-color: rgba(var(--overlay-demand-rgb), 0.86);" in dashboard
    assert ".surface-hotspot.sell.layer-broker-controls {\n      border-color: rgba(var(--overlay-supply-rgb), 0.86);" in dashboard
    assert ".overlay-editor" in overlay_editor_css


def test_tracker_dashboard_exposes_floating_overlay_editor() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "overlay-editor" in dashboard
    assert "overlay-editor-open" in dashboard
    assert "id=\"overlay-editor-open\" type=\"button\" hidden" in dashboard
    assert "id=\"overlay-editor\" role=\"dialog\" aria-label=\"Overlay editor\" hidden" in dashboard
    assert "/v1/mobile/window-tracker/assets/floating-windows/overlay_editor.css" in dashboard
    assert "const OVERLAY_EDITOR_HARDSAVED_SETTINGS = __OVERLAY_EDITOR_SETTINGS_JSON__;" in dashboard
    assert "OVERLAY_EDITOR_SAVE_ENDPOINT" in dashboard
    assert "OVERLAY_EDITOR_SCHEMA_VERSION = 2" in dashboard
    assert "OVERLAY_EDITOR_ENABLED" in dashboard
    assert "OVERLAY_EDITOR_QUERY.get(\"overlay_editor\")" in dashboard
    assert "OVERLAY_EDITOR_MIGRATE_LOCAL" in dashboard
    assert "OVERLAY_EDITOR_STORAGE_KEY" in dashboard
    assert "OVERLAY_LAYER_KEYS" in dashboard
    assert "phoenixguard.overlay.editor.v2" in dashboard
    assert "phoenixguard.overlay.editor.v1" not in dashboard
    assert "data-overlay-setting=\"opacityScale\"" in dashboard
    assert "data-overlay-setting=\"borderScale\"" in dashboard
    assert "data-overlay-setting=\"lineScale\"" in dashboard
    assert "data-overlay-setting=\"labelScale\"" in dashboard
    assert "data-overlay-setting=\"labelOffset\"" not in dashboard
    assert "data-overlay-color=\"demand\"" in dashboard
    assert "data-overlay-layer-control=\"trendlines\"" in dashboard
    assert "function applyOverlayEditorSettings" in dashboard
    assert "function hardSaveOverlayEditorSettings" in dashboard
    assert "function migrateLocalOverlayEditorSettingsToBackend" in dashboard
    assert "if (!OVERLAY_EDITOR_ENABLED)" in dashboard
    assert "async function saveOverlayEditorSettings" in dashboard
    assert "function beginOverlayEditorDrag" in dashboard
    assert "panelLocked" in dashboard
    assert "persistedSettings.schemaVersion = OVERLAY_EDITOR_SCHEMA_VERSION" in dashboard
    assert "state.overlayEditor.layers = Object.fromEntries" not in dashboard
    assert "applySavedOverlayLayerState" not in dashboard
    assert "renderHotspots();" in dashboard


def test_compare_desk_images_default_to_uncropped_contained_views() -> None:
    source_image = Image.new("RGB", (96, 54), color=(14, 18, 22))
    overlay_image = Image.new("RGB", (96, 54), color=(28, 38, 48))
    heatmap_image = Image.new("RGB", (96, 54), color=(40, 24, 18))

    html = main.build_compare_desk_html(
        {"timestamp": "2026-04-03T00:00:00Z", "action": "BUY", "confidence": 0.72},
        source_image,
        overlay_image,
        heatmap_image,
        render_config={"overlay_mode": "history-plus-projection"},
    )

    assert "scale(1.08)" not in html
    assert "scale(1.02)" not in html
    assert "value='1.08'" not in html
    assert "scale(1)" in html
    assert "value='1'" in html
    assert "object-fit: contain !important" in main.UI_CSS


def test_decision_record_excludes_raw_backend_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def session_snapshot() -> dict[str, Any]:
        return {
            "session_id": "session-1",
            "entries": [
                {
                    "timestamp": "2026-04-03T10:00:00Z",
                    "action": "BUY",
                    "confidence": 0.81,
                    "file_name": "chart.png",
                }
            ],
        }

    monkeypatch.setattr(
        main,
        "_get_session_snapshot",
        session_snapshot,
    )
    monkeypatch.setattr(main, "_feedback_target_entries", lambda limit=240: [{"feedback_status": "pending"}])
    monkeypatch.setattr(main, "_feedback_submission_states", lambda: [{"status": "completed"}])
    monkeypatch.setattr(main, "_load_zone_memory", lambda: [{"kind": "support"}])

    payload = main.build_runtime_audit_payload(
        {
            "execution_action": "BUY",
            "confidence": 0.81,
            "decision_state": "READY",
            "execution_permission": "WAIT_FOR_CONFIRMATION",
            "expected_3min_move_pct": 0.42,
            "timing_signal": {
                "entry_state": "READY",
                "eta_minutes": {"low": 2, "high": 5},
                "timeframe": "M5",
            },
            "multi_timeframe": {"gate_state": "confirmed"},
        },
        {
            "backend": "secret-router",
            "detector": "YOLO",
            "checkpoint_path": "C:/private/model.ckpt",
        },
        render_config={
            "overlay_mode": "history-boxes",
            "council_scope": "full",
            "vision_extras": ["grounded-zones", "tta-tag"],
            "fuse_timeframe_overlays": True,
        },
    )

    assert set(payload) == {
        "generated_at",
        "decision",
        "timing",
        "review_settings",
        "session_summary",
        "outcome_review",
    }
    assert "cv_debug" not in payload
    assert "result" not in payload

    serialized = json.dumps(payload).lower()
    for term in ("yolo", "backend", "checkpoint", "secret-router", "model.ckpt"):
        assert term not in serialized
