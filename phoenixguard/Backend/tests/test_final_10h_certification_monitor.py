from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from Backend.tools.run_final_10h_production_certification import (
    dashboard_capture_command_for_certification,
    fetch_endpoint_results_for_certification,
    overlay_modes_capture_command_for_certification,
    poll_capture_jobs_for_certification,
    start_capture_job_for_certification,
    timing_status_for_certification,
)


def _float_value(value: object) -> float:
    assert isinstance(value, (int, float))
    return float(value)


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


def test_timing_status_uses_direct_display_state_without_live_state_rebuild() -> None:
    now_epoch = time.time()
    timing = timing_status_for_certification(
        {
            "frame_timing_trace_v3": {
                "frame_age_ms": 27012,
                "overlay_age_ms": 27012,
                "model_vote_age_ms": 27012,
            },
        },
        {},
        {
            "display_published_epoch": now_epoch - 1.0,
            "frame_index": 42,
            "overlay_frame_id": 42,
            "model_vote_frame_id": 42,
        },
    )

    assert 0.0 < _float_value(timing["frame_age_ms"]) < 5000.0
    assert 0.0 < _float_value(timing["overlay_age_ms"]) < 5000.0
    assert 0.0 < _float_value(timing["model_vote_age_ms"]) < 5000.0
    assert 0.0 < _float_value(timing["direct_display_age_ms"]) < 5000.0


def test_timing_status_does_not_refresh_overlay_age_on_frame_mismatch() -> None:
    now_epoch = time.time()
    timing = timing_status_for_certification(
        {
            "frame_timing_trace_v3": {
                "frame_age_ms": 27012,
                "overlay_age_ms": 27012,
                "model_vote_age_ms": 27012,
            },
        },
        {},
        {
            "display_published_epoch": now_epoch - 1.0,
            "frame_index": 42,
            "overlay_frame_id": 41,
            "model_vote_frame_id": 41,
        },
    )

    assert 0.0 < _float_value(timing["frame_age_ms"]) < 5000.0
    assert timing["overlay_age_ms"] == 27012
    assert timing["model_vote_age_ms"] == 27012


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

    time.sleep(0.03)
    active = poll_capture_jobs_for_certification([job], log_path)

    assert active == []
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "capture_finished"
    assert events[-1]["timed_out"] is True


def test_dashboard_capture_command_passes_child_timeout(tmp_path: Path) -> None:
    command = dashboard_capture_command_for_certification(
        "http://127.0.0.1:8793",
        "pocket-live-8788",
        tmp_path / "dashboard",
        timeout_sec=45.0,
    )

    timeout_index = command.index("--timeout")
    assert command[timeout_index + 1] == "45.000"


def test_overlay_mode_capture_command_passes_child_timeout(tmp_path: Path) -> None:
    command = overlay_modes_capture_command_for_certification(
        "http://127.0.0.1:8793",
        "pocket-live-8788",
        tmp_path / "overlay_modes",
        timeout_sec=30.0,
    )

    timeout_index = command.index("--timeout")
    assert command[timeout_index + 1] == "30.000"


class _SlowJsonHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        time.sleep(0.4)
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        _ = (format, args)
        return


def test_endpoint_fetch_runs_in_parallel() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowJsonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        urls = {f"endpoint_{index}": f"http://127.0.0.1:{port}/endpoint_{index}" for index in range(4)}
        started = time.perf_counter()
        results = fetch_endpoint_results_for_certification(urls, list(urls), timeout_sec=5.0)
        elapsed = time.perf_counter() - started
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)

    assert set(results) == set(urls)
    assert all(status == 200 for status, _payload, _error, _latency in results.values())
    assert elapsed < 1.2
