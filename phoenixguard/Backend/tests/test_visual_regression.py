from __future__ import annotations

import io
from hashlib import sha256
from pathlib import Path

from PIL import Image

from phoenixguard.vision.contracts import normalize_overlay_object
from phoenixguard.vision.renderer import render_overlays_on_chart


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "visual_regression"

EXPECTED = {
    "v1_golden.png": ("800x600", "f36cfe66c714ce5680a2898510579be57d7beaf46c81e2c794ed82fd1bf11bc2"),
    "v2_golden.png": ("800x600", "643cbefa05457cae83b5e8dc7d53f8edeb67044d6925b6a446a238a95f5066e3"),
    "v3_restored.png": ("800x600", "f5824e5132d569eeeb70d15c5b53bcbc69fe1dd8ac708aa11c0eadbbb97ad584"),
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
