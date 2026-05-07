from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image


_REPO = Path(__file__).resolve().parent.parent
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


def test_outcome_review_panel_hides_learning_engine_terms(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")
    monkeypatch.setattr(main.RUNTIME, "models_dir", tmp_path / "models")
    monkeypatch.setattr(main.RUNTIME, "enable_feedback_learning_feed", True)

    html = main._build_learning_feed_html()

    assert "Outcome Review" in html
    lowered = html.lower()
    for term in ("rl", "replay", "checkpoint", "buffer"):
        assert term not in lowered


def test_setup_guide_copy_stays_manual_and_non_intrusive() -> None:
    html = main._build_setup_guide_dialog_html()

    assert "Open this any time from the workspace guides" in html
    assert "forced walkthrough" in html
    assert "opens once on first load" not in html


def test_ui_head_does_not_auto_open_setup_guide() -> None:
    shell_html = main._build_workspace_shell_bar_html().lower()
    lowered = main.UI_HEAD.lower()

    assert "show overview" in shell_html
    assert "maybeopensetupguide" not in lowered
    assert "opendialog('setup-guide')" not in lowered


def test_compare_desk_images_default_to_uncropped_contained_views() -> None:
    source_image = Image.new("RGB", (96, 54), color=(14, 18, 22))
    overlay_image = Image.new("RGB", (96, 54), color=(28, 38, 48))
    heatmap_image = Image.new("RGB", (96, 54), color=(40, 24, 18))

    html = main._build_compare_desk_html(
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


def test_decision_record_excludes_raw_backend_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "_get_session_snapshot",
        lambda: {
            "session_id": "session-1",
            "entries": [
                {
                    "timestamp": "2026-04-03T10:00:00Z",
                    "action": "BUY",
                    "confidence": 0.81,
                    "file_name": "chart.png",
                }
            ],
        },
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
