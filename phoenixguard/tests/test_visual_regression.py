from __future__ import annotations

import io
from hashlib import sha256
from pathlib import Path

from PIL import Image

from phoenixguard.vision.contracts import normalize_overlay_object
from phoenixguard.vision.renderer import render_overlays_on_chart


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "visual_regression"

EXPECTED = {
    "v1_golden.png": ("800x600", "846f00ab9107be433bf5b30a5498b9bf2b1112859ce98b3850cf01d37c40468d"),
    "v2_golden.png": ("800x600", "5134620b5bf84d3ef3212e7d75db32a7c7494d0ac8c2926d13b113b798828b33"),
    "v3_restored.png": ("800x600", "502c31edfdf182ea6c6406e99757f27056fb8dc07d1355421c68dbc6a0d75438"),
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
