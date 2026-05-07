from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


def _pixel_energy(image: Image.Image, x: int, y: int) -> float:
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    return float(arr[y, x].sum())


def _sample_heatmap_result() -> dict[str, object]:
    return {
        "detections": [
            {
                "bbox": [46, 42, 86, 90],
                "confidence": 0.92,
                "pattern": "latest_candle_buy",
            }
        ],
        "current_box": {
            "bbox": [46, 42, 86, 90],
            "confidence": 0.88,
            "box_type": "balance",
        },
        "next_box_hypotheses": [
            {
                "bbox": [114, 44, 154, 86],
                "confidence": 0.78,
                "box_type": "impulse",
                "trigger": "breakout",
            }
        ],
        "zone_learning": {
            "matching_zones": [
                {
                    "bbox": [42, 38, 90, 94],
                    "score": 0.74,
                    "label": "Buy reaction zone",
                    "kind": "support",
                }
            ]
        },
        "chart_state": {
            "path_clarity": 0.81,
            "entry_type": "continuation",
            "continuation_signal": "breakout",
            "reversal_signal": "none",
            "continuation_probability": 0.74,
            "reversal_probability": 0.16,
            "structure_setup": "consolidation_breakout",
        },
        "latest_parse_quality": 0.87,
        "confidence": 0.83,
        "execution_permission": "EXECUTE",
        "projection": {
            "direction": "BUY",
            "confidence": 0.79,
            "box_type": "impulse",
        },
        "action": "BUY",
    }


def _sample_reversal_heatmap_result() -> dict[str, object]:
    return {
        "detections": [
            {
                "bbox": [72, 28, 112, 88],
                "confidence": 0.90,
                "pattern": "wick_rejection_buy",
            }
        ],
        "current_box": {
            "bbox": [70, 24, 116, 92],
            "confidence": 0.86,
            "box_type": "reversal_base",
        },
        "next_box_hypotheses": [
            {
                "bbox": [126, 26, 168, 88],
                "confidence": 0.74,
                "box_type": "impulse",
                "trigger": "reversal_release",
            }
        ],
        "zone_learning": {
            "matching_zones": [
                {
                    "bbox": [66, 20, 118, 96],
                    "score": 0.78,
                    "label": "Reaction shelf",
                    "kind": "reaction",
                }
            ]
        },
        "chart_state": {
            "path_clarity": 0.72,
            "entry_type": "reversal",
            "continuation_signal": "reversal_release",
            "reversal_signal": "wick_rejection",
            "continuation_probability": 0.41,
            "reversal_probability": 0.63,
            "structure_setup": "reversal_release",
        },
        "latest_parse_quality": 0.84,
        "confidence": 0.79,
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "projection": {
            "direction": "BUY",
            "confidence": 0.73,
            "box_type": "impulse",
        },
        "action": "BUY",
    }


def test_confidence_heatmap_concentrates_on_signal_and_projection_path() -> None:
    source = Image.new("RGB", (220, 140), color=(8, 8, 8))
    result = _sample_heatmap_result()

    heatmap = main._build_confidence_heatmap_image(result, source)

    assert heatmap is not None
    assert heatmap.size == source.size

    hotspot_energy = _pixel_energy(heatmap, 66, 64)
    path_energy = _pixel_energy(heatmap, 104, 64)
    background_energy = _pixel_energy(heatmap, 16, 16)

    assert hotspot_energy > background_energy + 60.0
    assert path_energy > background_energy + 25.0
    assert hotspot_energy > path_energy


def test_confidence_heatmap_handles_large_source_image() -> None:
    source = Image.new("RGB", (1660, 859), color=(8, 8, 8))
    result = _sample_heatmap_result()

    heatmap = main._build_confidence_heatmap_image(result, source)

    assert heatmap is not None
    assert heatmap.size == source.size


def test_heatmap_summary_exposes_layer_audit_and_ranked_hotspots() -> None:
    source = Image.new("RGB", (220, 140), color=(8, 8, 8))
    result = _sample_heatmap_result()

    payload = main._build_confidence_heatmap_payload(result, source)
    summary_html = main._build_heatmap_summary_html(result, source, heatmap_payload=payload)

    assert payload is not None
    assert len(payload["hotspots"]) >= 1
    assert "opportunity" in payload["layers"]
    assert "entry" in payload["layers"]
    assert "continuation" in payload["layers"]
    assert "reversal" in payload["layers"]
    assert "window_counts" in payload
    assert payload["contour_levels"] == [0.38, 0.54, 0.70, 0.86]
    assert float(payload["hotspots"][0]["opportunity_score"]) >= 0.0
    assert payload["hotspots"][0]["window_class"] in {"entry", "continuation", "reversal"}
    assert "Final Fused Heat" in summary_html
    assert "Opportunity Windows" in summary_html
    assert "Entry Windows" in summary_html
    assert "Continuation Windows" in summary_html
    assert "Reversal Windows" in summary_html
    assert "Detections" in summary_html
    assert "Projection Corridor" in summary_html
    assert "Zones" in summary_html
    assert "Contour Rings" in summary_html
    assert "Window markers ranked by opportunity strength" in summary_html
    assert "Entry Window" in summary_html or "Continuation Window" in summary_html or "Reversal Window" in summary_html
    assert "data-layer='corridor'" in summary_html
    assert "data-layer='opportunity'" in summary_html
    assert "data-layer='entry'" in summary_html
    assert "data-layer='continuation'" in summary_html
    assert "data-layer='reversal'" in summary_html


def test_heatmap_can_classify_reversal_windows() -> None:
    source = Image.new("RGB", (220, 140), color=(8, 8, 8))
    result = _sample_reversal_heatmap_result()

    payload = main._build_confidence_heatmap_payload(result, source)

    assert payload is not None
    assert len(payload["hotspots"]) >= 1
    assert any(str(hotspot["window_class"]) == "reversal" for hotspot in payload["hotspots"])
    assert int(payload["window_counts"]["reversal"]) >= 1


def test_heatmap_feedback_calibration_leans_into_target_path_feedback(monkeypatch, tmp_path: Path) -> None:
    journal_path = tmp_path / "feedback_submissions.jsonl"
    submission = {
        "submission_id": "sub_1",
        "created_at": "2026-03-30T08:00:00+00:00",
        "updated_at": "2026-03-30T08:00:00+00:00",
        "source_path": "chart.png",
        "source_image_hash": "abc123",
        "signal_direction": "BUY",
        "actual_outcome": "BUY",
        "execution_result": "WIN",
        "market_state": "TRENDING",
        "setup_state": "CONTINUATION",
        "failure_mode": "NONE",
        "label_confidence_pct": 94,
        "feedback_image": {
            "visual_regions": [
                {"semantic_label": "target_path", "relative_bbox": [0.52, 0.24, 0.84, 0.62]},
                {"semantic_label": "target_path", "relative_bbox": [0.48, 0.32, 0.78, 0.58]},
            ]
        },
        "stage_status": {
            "personalization": "applied",
            "continual_learning": "applied",
            "rl": "applied",
            "personalization_context": "applied",
            "style_refresh": "applied",
        },
        "stage_payloads": {},
        "stage_errors": {},
    }
    journal_path.write_text(
        json.dumps(
            {
                "ts": "2026-03-30T08:00:00+00:00",
                "submission_id": "sub_1",
                "event_type": "submission_created",
                "submission": submission,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "_feedback_submission_journal_path", lambda: journal_path)
    main._heatmap_feedback_calibration_cache["journal_mtime_ns"] = None
    main._heatmap_feedback_calibration_cache["state"] = None

    calibration = main._get_heatmap_feedback_calibration()

    assert int(calibration["sample_count"]) == 1
    assert float(calibration["layer_multipliers"]["corridor"]) > 1.0
    assert float(calibration["layer_multipliers"]["opportunity"]) > 1.0
    assert float(calibration["class_multipliers"]["continuation"]) > float(calibration["class_multipliers"]["reversal"])


def test_heatmap_mask_shaping_biases_energy_toward_segmented_region() -> None:
    source = Image.new("RGB", (180, 120), color=(8, 8, 8))
    mask_grid = [
        [0, 0, 0, 64, 196, 255],
        [0, 0, 0, 84, 218, 255],
        [0, 0, 0, 96, 228, 255],
        [0, 0, 0, 84, 218, 255],
        [0, 0, 0, 64, 196, 255],
        [0, 0, 0, 48, 176, 236],
    ]
    result = {
        "detections": [],
        "next_box_hypotheses": [],
        "zone_learning": {"matching_zones": []},
        "chart_state": {
            "path_clarity": 0.62,
            "entry_type": "continuation",
            "continuation_signal": "breakout",
            "reversal_signal": "none",
            "continuation_probability": 0.58,
            "reversal_probability": 0.18,
            "structure_setup": "consolidation_breakout",
        },
        "latest_parse_quality": 0.76,
        "confidence": 0.72,
        "execution_permission": "EXECUTE",
        "projection": {"direction": "BUY", "confidence": 0.69, "box_type": "impulse"},
        "grounded_chart": {
            "backend": {
                "masks": [
                    {
                        "label": "support zone",
                        "score": 0.91,
                        "bbox": [42.0, 26.0, 126.0, 96.0],
                        "mask_area_ratio": 0.22,
                        "coverage_bbox_ratio": 0.44,
                        "grid_size": 6,
                        "mask_grid": mask_grid,
                    }
                ]
            }
        },
        "action": "BUY",
    }

    payload = main._build_confidence_heatmap_payload(result, source)
    heatmap = main._compose_confidence_heatmap_image(payload, source)

    assert payload is not None
    assert heatmap is not None
    assert int(payload["segmentation_mask_count"]) == 1
    assert "segmentation" in payload["layers"]

    left_energy = _pixel_energy(heatmap, 58, 62)
    right_energy = _pixel_energy(heatmap, 108, 62)

    assert right_energy >= left_energy + 18.0
