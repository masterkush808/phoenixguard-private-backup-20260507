from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


def _configure_runtime_dirs(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(main.RUNTIME, "logs_dir", logs_dir)
    return data_dir, logs_dir


def test_restore_manual_inference_jobs_requeues_recoverable_rows(monkeypatch, tmp_path: Path) -> None:
    data_dir, _logs_dir = _configure_runtime_dirs(monkeypatch, tmp_path)
    higher_path = tmp_path / "higher.png"
    lower_path = tmp_path / "lower.png"
    higher_path.write_bytes(b"higher")
    lower_path.write_bytes(b"lower")

    queue_path = data_dir / "manual_inference_jobs.json"
    queue_path.write_text(
        json.dumps(
            [
                {
                    "job_id": "recover-me",
                    "status": "running",
                    "upload_paths": [str(higher_path), str(lower_path)],
                    "render_config": {"overlay_mode": "history-plus-projection"},
                },
                {
                    "job_id": "missing-input",
                    "status": "queued",
                    "upload_paths": [str(higher_path), str(tmp_path / "missing.png")],
                    "render_config": {"overlay_mode": "history-plus-projection"},
                },
            ]
        ),
        encoding="utf-8",
    )

    restored = main._restore_manual_inference_jobs()
    rows = main._read_json_file(queue_path, [])

    assert restored == {"restored_jobs": 1}
    assert rows[0]["job_id"] == "recover-me"
    assert rows[0]["status"] == "queued"
    assert rows[0]["recovered"] is True
    assert rows[1]["job_id"] == "missing-input"
    assert rows[1]["status"] == "failed"
    assert "unavailable" in rows[1]["last_error"].lower()


def test_resume_pending_manual_inference_jobs_submits_background_recovery(monkeypatch, tmp_path: Path) -> None:
    data_dir, _logs_dir = _configure_runtime_dirs(monkeypatch, tmp_path)
    original_resume_state = main._manual_inference_resume_started
    queue_path = data_dir / "manual_inference_jobs.json"
    queue_path.write_text(
        json.dumps(
            [
                {
                    "job_id": "queued-job",
                    "status": "queued",
                    "upload_paths": [str(tmp_path / "higher.png"), str(tmp_path / "lower.png")],
                    "render_config": {"overlay_mode": "history-plus-projection"},
                }
            ]
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    class _ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            fn(*args, **kwargs)
            return None

    def _fake_process(job_id: str) -> bool:
        calls.append(job_id)
        main._update_manual_inference_job(job_id, status="completed", result_path="done.json")
        return True

    try:
        main._manual_inference_resume_started = False
        monkeypatch.setattr(main, "_get_background_executor", lambda: _ImmediateExecutor())
        monkeypatch.setattr(main, "_process_recovered_manual_inference_job", _fake_process)

        main._resume_pending_manual_inference_jobs()

        rows = main._read_json_file(queue_path, [])
        assert calls == ["queued-job"]
        assert rows[0]["status"] == "completed"
        assert main._manual_inference_resume_started is False
    finally:
        main._manual_inference_resume_started = original_resume_state


def test_complete_manual_inference_job_writes_result_summary(monkeypatch, tmp_path: Path) -> None:
    _configure_runtime_dirs(monkeypatch, tmp_path)
    job = main._enqueue_manual_inference_job(
        [str(tmp_path / "higher.png"), str(tmp_path / "lower.png")],
        main._build_render_config(
            overlay_mode="history-plus-projection",
            min_conf_global=0.42,
            min_conf_latest=0.50,
            history_depth=8,
            label_density=10,
            projection_focus=0.35,
            debug_depth=6,
        ),
        audit_tab_loaded=False,
        heatmap_tab_loaded=False,
        compare_tab_loaded=True,
        source="manual-sync",
    )

    updated = main._complete_manual_inference_job(
        str(job["job_id"]),
        {
            "action": "SELL",
            "confidence": 0.71,
            "decision_state": "confirmed",
            "execution_permission": "allowed",
            "authority_contract": {
                "runtime_path": "OFFLINE_MANUAL_ANALYSIS",
                "execution_authority": "NONE",
                "can_publish_execution_packet": False,
                "can_trigger_shooter": False,
                "canonical_live_authority": "tracker_model_council_packet_shooter",
                "required_live_packet": "PG_EXECUTION_PACKET_V3",
            },
            "packet_authority": "OFFLINE_ANALYSIS_ONLY",
            "memory_similarity": 0.44,
            "projection": {"direction": "SELL"},
            "multi_timeframe": {"aligned": False},
            "timestamp": "2026-03-28T00:00:00+00:00",
        },
        file_path="lower.png",
    )

    result_path = Path(str(updated["result_path"]))
    payload = json.loads(result_path.read_text(encoding="utf-8"))

    assert updated["status"] == "completed"
    assert result_path.exists()
    assert payload["job_id"] == str(job["job_id"])
    assert payload["action"] == "SELL"
    assert payload["authority_contract"]["can_publish_execution_packet"] is False
    assert payload["packet_authority"] == "OFFLINE_ANALYSIS_ONLY"
    assert payload["file_path"] == "lower.png"
    assert payload["render_config"]["overlay_mode"] == "history-plus-projection"
