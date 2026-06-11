from __future__ import annotations

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
        },
        "next_box_hypotheses": [
            {
                "bbox": [114, 44, 154, 86],
                "confidence": 0.78,
            }
        ],
        "zone_learning": {
            "matching_zones": [
                {
                    "bbox": [42, 38, 90, 94],
                    "score": 0.74,
                    "label": "Buy reaction zone",
                }
            ]
        },
        "chart_state": {
            "path_clarity": 0.81,
        },
        "latest_parse_quality": 0.87,
        "projection": {
            "direction": "BUY",
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


def test_heatmap_summary_exposes_layer_audit_and_ranked_hotspots() -> None:
    source = Image.new("RGB", (220, 140), color=(8, 8, 8))
    result = _sample_heatmap_result()

    payload = main._build_confidence_heatmap_payload(result, source)
    summary_html = main._build_heatmap_summary_html(result, source, heatmap_payload=payload)

    assert payload is not None
    assert len(payload["hotspots"]) >= 1
    assert payload["contour_levels"] == [0.38, 0.54, 0.70, 0.86]
    assert "Final Fused Heat" in summary_html
    assert "Detections" in summary_html
    assert "Projection Corridor" in summary_html
    assert "Zones" in summary_html
    assert "Contour Rings" in summary_html
    assert "Hotspot Markers" in summary_html
    assert "Top Hotspots" in summary_html
    assert "Hotspot 1" in summary_html
    assert "data-layer='corridor'" in summary_html
