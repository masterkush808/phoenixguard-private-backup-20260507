from __future__ import annotations

from pathlib import Path

from tools.purge_v3_studies_and_cache import run_purge


def _write(path: Path, content: str = "runtime") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_dry_run_reports_allowlisted_runtime_paths_without_deleting(tmp_path: Path) -> None:
    safe_paths = [
        _write(tmp_path / ".codex_runtime" / "studies" / "study.json"),
        _write(tmp_path / ".codex_runtime" / "study_packets" / "packet.json"),
        _write(tmp_path / ".codex_runtime" / "replay_studies" / "replay.json"),
        _write(tmp_path / ".codex_runtime" / "old_study_cache" / "old.json"),
        _write(tmp_path / ".codex_runtime" / "latest_study.json"),
        _write(tmp_path / ".codex_runtime" / "latest_study_EURGBP.json"),
        _write(tmp_path / ".codex_runtime" / "latest_execution.json"),
        _write(tmp_path / ".codex_runtime" / "latest_execution_EURGBP.json"),
        _write(tmp_path / ".codex_runtime" / "visual_state_cache" / "state.json"),
        _write(tmp_path / ".codex_runtime" / "overlay_cache" / "overlay.json"),
        _write(tmp_path / ".codex_runtime" / "frame_cache" / "frame.cache"),
        _write(tmp_path / ".codex_runtime" / "stale_sessions" / "session.json"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "M5" / "studies" / "study.json"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "M5" / "study_packets" / "packet.json"),
    ]

    result = run_purge(tmp_path)

    assert result.confirm_delete is False
    assert result.total_file_count == len(safe_paths)
    assert all(path.exists() for path in safe_paths)
    report = (tmp_path / "reports" / "FINAL_PURGED_STUDIES_AND_CACHE_REPORT.md").read_text(encoding="utf-8")
    assert "mode: dry-run" in report
    assert ".codex_runtime/studies" in report
    assert ".codex_runtime/latest_execution_EURGBP.json" in report
    assert "safe_to_delete |" in report
    assert "true |" in report


def test_confirm_delete_removes_only_safe_generated_runtime_paths(tmp_path: Path) -> None:
    deleted_paths = [
        _write(tmp_path / ".codex_runtime" / "studies" / "study.json"),
        _write(tmp_path / ".codex_runtime" / "study_packets" / "packet.json"),
        _write(tmp_path / ".codex_runtime" / "replay_studies" / "replay.json"),
        _write(tmp_path / ".codex_runtime" / "old_study_cache" / "old.json"),
        _write(tmp_path / ".codex_runtime" / "latest_study_live.json"),
        _write(tmp_path / ".codex_runtime" / "latest_execution_live.json"),
        _write(tmp_path / ".codex_runtime" / "visual_state_cache" / "state.json"),
        _write(tmp_path / ".codex_runtime" / "overlay_cache" / "overlay.json"),
        _write(tmp_path / ".codex_runtime" / "frame_cache" / "frame.cache"),
        _write(tmp_path / ".codex_runtime" / "stale_sessions" / "session.json"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "M5" / "studies" / "study.json"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "M5" / "study_packets" / "packet.json"),
    ]
    protected_paths = [
        _write(tmp_path / ".codex_runtime" / "studies" / "model.pt"),
        _write(tmp_path / ".codex_runtime" / "frame_cache" / "808_shooter_boxes.json"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "M5" / "studies" / "user_calibration_manifest.json"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "M5" / "study_packets" / "V3_EXECUTION_CONTRACT.md"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "calibration" / "studies" / "calibration.json"),
    ]

    result = run_purge(tmp_path, confirm_delete=True)

    assert result.confirm_delete is True
    assert all(not path.exists() for path in deleted_paths)
    assert all(path.exists() for path in protected_paths)
    report = (tmp_path / "reports" / "FINAL_PURGED_STUDIES_AND_CACHE_REPORT.md").read_text(encoding="utf-8")
    assert "mode: delete" in report
    assert "Protected Paths Retained" in report
    assert ".codex_runtime/studies/model.pt" in report
    assert ".codex_runtime/frame_cache/808_shooter_boxes.json" in report


def test_unapproved_and_non_runtime_paths_are_retained(tmp_path: Path) -> None:
    retained_paths = [
        _write(tmp_path / "study_packets" / "outside-runtime.json"),
        _write(tmp_path / "latest_study.json"),
        _write(tmp_path / "808_shooter_boxes.json"),
        _write(tmp_path / "reports" / "existing_report.md"),
        _write(tmp_path / "tests" / "test_existing.py"),
        _write(tmp_path / ".codex_runtime" / "models" / "weights.pt"),
        _write(tmp_path / ".codex_runtime" / "data_live" / "EURGBP" / "M5" / "reports" / "study_packets" / "report.json"),
        _write(tmp_path / ".codex_runtime" / "latest_study_weights.pt"),
    ]
    deleted_path = _write(tmp_path / ".codex_runtime" / "latest_study_safe.json")

    run_purge(tmp_path, confirm_delete=True)

    assert not deleted_path.exists()
    assert all(path.exists() for path in retained_paths)
