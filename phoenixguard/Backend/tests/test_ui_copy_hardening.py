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


def test_tracker_pressure_uses_server_authoritative_temporal_event() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "function rawLivePressureRead" not in dashboard
    assert "pressure_event" in dashboard
    assert 'pressureState === "ended"' in dashboard
    assert "directionalSide(pressure.direction || pressure.side)" in dashboard
    assert "Past observations stay in history. They never overwrite the current move" in dashboard


def test_tracker_dashboard_separates_current_outlook_and_entry_permission() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'id="current-move-title"' in dashboard
    assert 'id="forecast-title"' in dashboard
    assert 'id="permission-title"' in dashboard
    assert "This is a forecast, not entry permission." in dashboard
    assert 'function entryWindowLabel(permission)' in dashboard
    assert 'function entryLocationGuidance(permission, action)' in dashboard
    assert '"Buy low · entry open"' in dashboard
    assert '"Sell high · entry open"' in dashboard
    assert '"Setup window · verifying"' in dashboard
    assert "current-frame permission is refreshing" in dashboard
    assert "lower price inside the verified demand or retest area" in dashboard
    assert "higher price inside the verified supply or retest area" in dashboard
    assert "The setup closes early if live truth changes." in dashboard
    assert "contract.valid_for_seconds" in dashboard
    assert "/v1/mobile/operator/state/v1/" in dashboard
    assert "function highFrequencyForecast" not in dashboard
    assert "function derivedHighFrequencyForecast" not in dashboard
    assert 'data-overlay-family="lstm"' in dashboard
    assert "LSTM mark shows the sequence model's current chart-anchored study or path" in dashboard
    assert "/v1/mobile/live/state/v3/" not in dashboard


def test_tracker_dashboard_uses_sanitized_operator_overlays() -> None:
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'const OPERATOR_SCHEMA_VERSION = "PG_OPERATOR_WORKSPACE_V1";' in dashboard
    assert "state.overlays = safeList(operatorState.overlays);" in dashboard
    assert "function lifecycleIsVisible(overlay)" in dashboard
    assert "function overlayMatchesSurface(overlay)" in dashboard
    assert "function overlayFamily(overlay)" in dashboard
    assert '["movement", "structure", "zones", "plan", "outlook", "history"]' in dashboard
    assert 'safeText(overlay.coordinate_space, "chart")' in dashboard
    assert "function renderOverlays()" in dashboard
    assert "backendOverlayObjects" not in dashboard
    assert "runtime_telemetry" not in dashboard
    assert "broker_controls" not in dashboard


def test_share_overlay_demo_uses_operator_contract_without_host_paths() -> None:
    demo = (_REPO / "Frontend" / "assets" / "share" / "overlay_demo.html").read_text(
        encoding="utf-8"
    )

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
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

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
    dashboard = (_REPO / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "overlay-editor" not in dashboard
    assert "OVERLAY_EDITOR" not in dashboard
    assert 'id="overlay-explorer"' in dashboard
    assert 'id="overlay-opacity" type="range"' in dashboard
    assert 'id="layers-all"' in dashboard
    assert 'id="layers-clear"' in dashboard
    assert 'data-overlay-family="smc"' in dashboard
    assert 'data-overlay-family="two_candle"' in dashboard
    assert 'data-overlay-family="lstm"' in dashboard
    assert 'data-label-mode="on"' in dashboard
    assert 'data-label-mode="hover"' in dashboard
    assert 'data-label-mode="off"' in dashboard


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
