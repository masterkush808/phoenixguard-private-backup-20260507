"""Profile _build_signal_payloads against the live session sidecars."""
from __future__ import annotations

import cProfile
import io
import json
import os
import pstats
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "Backend" / "src"))

os.environ.setdefault("PHOENIXGUARD_RUNTIME_SINGLETON_DISABLE", "1")

from PIL import Image  # noqa: E402

SESSION_ID = "pocket-live-8788"
LIVE_SESSIONS = Path(os.path.expandvars(
    r"%LOCALAPPDATA%\PhoenixGuard\runtime\live\data_live\mobile_api\window_tracker\sessions"
))


def main() -> int:
    from phoenixguard.mobile_api.window_tracker import PhoenixGuardWindowTrackingAdapter

    session_path = LIVE_SESSIONS / SESSION_ID
    payload = json.loads((session_path / "session.json").read_text(encoding="utf-8"))
    tracking = dict(payload.get("tracking_summary") or {})
    candles = [dict(row) for row in tracking.get("tracked_candles") or []]
    print(f"tracked_candles: {len(candles)}")

    artifacts = sorted((session_path / "artifacts").glob("*_chart.jpg"))
    if not artifacts:
        artifacts = sorted((session_path / "artifacts").glob("hot_latest_overlay.jpg"))
    chart_path = artifacts[-1]
    print(f"chart artifact: {chart_path.name}")
    chart_image = Image.open(chart_path).convert("RGB")

    timeframe_selector = {"value": "M5", "source": "broker_timeframe_selector_dom_bracket_pixel_mask", "confidence": 0.99}
    market_selector = {
        "value": "CAD/JPY OTC",
        "source": "broker_header_dom_bracket_pixel_mask",
        "confidence": 0.99,
        "market_selector_visual_fingerprint": "profiling",
    }
    chart_region = dict(payload.get("chart_region") or {})
    if not chart_region:
        chart_region = {"x": 0, "y": 0, "width": chart_image.width, "height": chart_image.height}

    tmp_root = Path(tempfile.mkdtemp(prefix="pg_profile_"))
    shutil.copytree(session_path, tmp_root / "sessions" / SESSION_ID)
    try:
        adapter = PhoenixGuardWindowTrackingAdapter()
        profiler = cProfile.Profile()
        profiler.enable()
        tracking_summary, latest_signal = adapter._build_signal_payloads(  # noqa: SLF001
            chart_image,
            chart_region,
            candles,
            timeframe_selector,
            market_selector=market_selector,
            session_payload=payload,
        )
        profiler.disable()
        action = str(latest_signal.get("action"))
        print(f"build ok: action={action} ts_keys={len(tracking_summary)}")
        buffer = io.StringIO()
        stats = pstats.Stats(profiler, stream=buffer).sort_stats("cumulative")
        stats.print_stats(28)
        text = buffer.getvalue()
        for line in text.splitlines():
            print(line[:200])
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
