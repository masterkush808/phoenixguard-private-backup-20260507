from __future__ import annotations

import time

from Backend.tools.run_final_10h_production_certification import timing_status_for_certification


def test_timing_status_uses_fresh_display_epoch_for_display_lag() -> None:
    now_epoch = time.time()
    timing = timing_status_for_certification(
        {
            "display_published_epoch": now_epoch - 1.0,
            "frame_timing_trace_v3": {
                "frame_age_ms": 27012,
                "overlay_age_ms": 4846,
                "model_vote_age_ms": 4846,
            },
        },
        {},
    )

    frame_age_ms = timing["frame_age_ms"]
    assert isinstance(frame_age_ms, (int, float))
    assert 0.0 < float(frame_age_ms) < 5000.0
    assert timing["display_age_ms"] != 0.0
    assert timing["overlay_age_ms"] == 4846
