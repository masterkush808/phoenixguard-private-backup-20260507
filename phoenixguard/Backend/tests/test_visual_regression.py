from __future__ import annotations

import io
from hashlib import sha256
from pathlib import Path

from PIL import Image

from phoenixguard.vision.contracts import normalize_overlay_object
from phoenixguard.vision.renderer import render_overlays_on_chart


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "visual_regression"

EXPECTED = {
    "v1_golden.png": ("800x600", "bd5065d0518ac08ef08da04a2b6e9f32f8e0f9a0909d958127fa22457aa02899"),
    "v2_golden.png": ("800x600", "893505e774d02f973c56c26cb6c98c3196f5e8a31a847675b7c2ee29721cda43"),
    "v3_restored.png": ("800x600", "2cf81ed1ed7c764e32dc9195617036a84e36e8cb02d7e8ba33fb8831d7127719"),
}


def _image_signature(path: Path) -> tuple[str, str]:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        signature = sha256(rgba.tobytes()).hexdigest()
        return f"{rgba.width}x{rgba.height}", signature


def test_visual_regression_fixtures_match_expected_signatures() -> None:
    for name, expected in EXPECTED.items():
        path = FIXTURE_DIR / name
        assert path.exists(), f"missing visual regression fixture: {path}"
        assert _image_signature(path) == expected


def test_renderer_matches_collected_golden_images() -> None:
    cases = {
        "v1_golden.png": [
            normalize_overlay_object({"id": "v1-sniper", "box": [40, 50, 180, 160], "confidence": 0.93}),
        ],
        "v2_golden.png": [
            normalize_overlay_object({"id": "v2-trend", "anchors": [(80, 120), (180, 120), (180, 240), (80, 240)], "confidence": 0.88}),
            normalize_overlay_object({"id": "v2-entry", "rect": [260, 180, 360, 260], "confidence": 0.74}),
        ],
        "v3_restored.png": [
            normalize_overlay_object({"id": "v3-sniper", "box": [100, 80, 240, 190], "confidence": 0.95}),
            normalize_overlay_object({"id": "v3-target", "box": [300, 150, 420, 280], "confidence": 0.67}),
        ],
    }

    for fixture_name, overlays in cases.items():
        rendered = render_overlays_on_chart(None, overlays)
        with Image.open(io.BytesIO(rendered)) as rendered_image:
            rendered_rgba = rendered_image.convert("RGBA")
            rendered_signature = (f"{rendered_rgba.width}x{rendered_rgba.height}", sha256(rendered_rgba.tobytes()).hexdigest())
        assert rendered_signature == EXPECTED[fixture_name]
        assert _image_signature(FIXTURE_DIR / fixture_name) == EXPECTED[fixture_name]
