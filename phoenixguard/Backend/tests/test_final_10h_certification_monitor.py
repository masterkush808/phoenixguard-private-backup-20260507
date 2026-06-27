from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from Backend.tools.run_final_10h_production_certification import (
    poll_capture_jobs_for_certification,
    start_capture_job_for_certification,
    timing_status_for_certification,
)


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


def test_capture_job_times_out_and_logs_without_blocking_sampler(tmp_path: Path) -> None:
    log_path = tmp_path / "capture_log.jsonl"
    out_dir = tmp_path / "capture"
    started = time.time()
    job = start_capture_job_for_certification(
        "test_sleep_capture",
        [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ],
        out_dir,
        log_path,
        timeout_sec=5.0,
    )

    active = poll_capture_jobs_for_certification([job], log_path)
    elapsed = time.time() - started

    assert elapsed < 2.0
    assert len(active) == 1
    assert job.process.poll() is None
    job.process.kill()
    job.process.wait(timeout=5.0)


def test_capture_job_timeout_records_finished_event(tmp_path: Path) -> None:
    log_path = tmp_path / "capture_log.jsonl"
    out_dir = tmp_path / "capture"
    job = start_capture_job_for_certification(
        "test_timeout_capture",
        [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ],
        out_dir,
        log_path,
        timeout_sec=0.01,
    )

    active = poll_capture_jobs_for_certification([job], log_path)

    assert active == []
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "capture_finished"
    assert events[-1]["timed_out"] is True
