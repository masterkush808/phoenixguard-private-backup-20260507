from pathlib import Path
from phoenixguard.vision.renderer import render_overlays_on_chart


def test_render_overlays_on_chart_smoke(tmp_path: Path):
    png = render_overlays_on_chart(None, [])
    assert isinstance(png, (bytes, bytearray)) and len(png) > 100
    out_dir = Path("Backend/tests/fixtures/visual_regression")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "v3_current.png"
    out_path.write_bytes(png)
    # simple file sanity
    assert out_path.exists() and out_path.stat().st_size > 0
