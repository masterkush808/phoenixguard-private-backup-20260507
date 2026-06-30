from __future__ import annotations
import json
import pytest

from pathlib import Path
import subprocess
import sys
from typing import Any

from tools import certification_common_v3 as cert

TOOLS_DIR = Path(__file__).resolve().parents[2] / "Backend" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import certify_v3_full_system_burn_in as burn
from tools import capture_dashboard_visual_v3 as dashboard_capture
from tools.capture_dashboard_visual_v3 import prune_capture_evidence


def test_wmic_python_process_fallback_preserves_comma_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    output = """
CommandLine="C:\\repo\\.venv\\Scripts\\python.exe" start_phoenixguard_24_7_tracker.py --focus-region 0.03,0.13,0.87,0.96 --no-open-dashboard
ParentProcessId=2596
ProcessId=26564

CommandLine="C:\\repo\\.venv\\Scripts\\python.exe" shooter.py signal --window-query "The Most Innovative Trading Platform"
ParentProcessId=1
ProcessId=10380
"""

    def fake_run(*args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(cert.subprocess, "run", fake_run)

    rows = cert.python_processes_wmic("primary timeout")

    assert rows[0]["ProcessId"] == 26564
    assert rows[0]["ParentProcessId"] == 2596
    assert "0.03,0.13,0.87,0.96" in rows[0]["CommandLine"]
    assert rows[1]["ProcessId"] == 10380


def test_leaf_processes_returns_deepest_matching_children() -> None:
    rows: list[dict[str, object]] = [
        {"ProcessId": 100, "ParentProcessId": 1, "CommandLine": "python start_phoenixguard_24_7_tracker.py"},
        {"ProcessId": 200, "ParentProcessId": 100, "CommandLine": "python start_phoenixguard_24_7_tracker.py"},
        {"ProcessId": 300, "ParentProcessId": 200, "CommandLine": "python start_phoenixguard_mobile_api.py"},
    ]

    leaves = cert.leaf_processes(rows)

    assert [row["ProcessId"] for row in leaves] == [300]


def test_dashboard_capture_retention_prunes_old_timestamp_bundles(tmp_path: Path) -> None:
    session = "pocket-live-8788"
    stamps = ["20260616_010000", "20260616_010100", "20260616_010200"]
    for stamp in stamps:
        for name in (
            f"dashboard_{session}_{stamp}.png",
            f"dashboard_{session}_{stamp}.html",
            f"latest_full-overlay_{session}_{stamp}.png",
        ):
            (tmp_path / name).write_bytes(b"evidence")
    unrelated = tmp_path / "dashboard_other-session_20260616_010000.png"
    unrelated.write_bytes(b"keep")

    result = prune_capture_evidence(tmp_path, session, max_capture_sets=1)

    assert result["removed_files"] == 6
    assert (tmp_path / f"dashboard_{session}_20260616_010200.png").exists()
    assert (tmp_path / f"latest_full-overlay_{session}_20260616_010200.png").exists()
    assert unrelated.exists()


def test_dashboard_capture_probe_does_not_publish_frontend_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_resolve_capture_context(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "route": "live",
            "backend_mode": "CLEAN_LIVE",
            "select_value": "clean_live",
            "expected_renderable_count": 0,
        }

    def fake_http_bytes(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "status": 200,
            "bytes": 13,
            "content_type": "text/html",
            "body": b"<html></html>",
        }

    def fake_http_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"ok": True, "status": 200, "payload": {"renderable_count": 0}}

    monkeypatch.setattr(
        dashboard_capture,
        "_resolve_capture_context",
        fake_resolve_capture_context,
    )
    monkeypatch.setattr(
        dashboard_capture,
        "_http_bytes",
        fake_http_bytes,
    )
    monkeypatch.setattr(
        dashboard_capture,
        "_http_json",
        fake_http_json,
    )

    report = dashboard_capture.build_capture(
        "http://127.0.0.1:8793",
        "pocket-live-8788",
        timeout=1.0,
        out_dir=tmp_path,
        width=800,
        height=600,
        skip_playwright=True,
    )

    assert "pg_no_heartbeat=1" in report["dashboard_url"]


def test_dashboard_capture_active_heartbeat_prefers_live_truth_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_capture, "HEARTBEAT_DIR", tmp_path)
    now_ms = 10_000_000.0
    monkeypatch.setattr(dashboard_capture.time, "time", lambda: now_ms / 1000.0)
    session = "pocket-live-8788"
    live = {
        "session_id": session,
        "surface_id": "dashboard_live",
        "route": "live",
        "overlay_mode": "CLEAN_LIVE",
        "visible_artifact_kind": "window-locked-overlay",
        "visible_overlay_count": 12,
        "received_at_ms": now_ms - 20_000.0,
        "chart_transform_id": "ct_live",
    }
    replay = {
        "session_id": session,
        "surface_id": "dashboard_replay_replay",
        "route": "replay",
        "overlay_mode": "REPLAY",
        "visible_artifact_kind": "window-locked-overlay",
        "visible_overlay_count": 36,
        "received_at_ms": now_ms - 1_000.0,
        "chart_transform_id": "ct_replay",
    }
    (tmp_path / f"{session}__dashboard_live.json").write_text(json.dumps(live), encoding="utf-8")
    (tmp_path / f"{session}__dashboard_replay_replay.json").write_text(json.dumps(replay), encoding="utf-8")

    selected = dashboard_capture.latest_active_dashboard_heartbeat(session)

    assert selected["route"] == "live"
    assert selected["overlay_mode"] == "CLEAN_LIVE"
    assert selected["chart_transform_id"] == "ct_live"


def test_dashboard_capture_active_heartbeat_does_not_promote_replay_without_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_capture, "HEARTBEAT_DIR", tmp_path)
    now_ms = 10_000_000.0
    monkeypatch.setattr(dashboard_capture.time, "time", lambda: now_ms / 1000.0)
    session = "pocket-live-8788"
    replay = {
        "session_id": session,
        "surface_id": "dashboard_replay_replay",
        "route": "replay",
        "overlay_mode": "REPLAY",
        "visible_artifact_kind": "window-locked-overlay",
        "visible_overlay_count": 36,
        "received_at_ms": now_ms - 1_000.0,
        "chart_transform_id": "ct_replay",
    }
    (tmp_path / f"{session}__dashboard_replay_replay.json").write_text(json.dumps(replay), encoding="utf-8")

    selected = dashboard_capture.latest_active_dashboard_heartbeat(session)

    assert selected == {}


def test_full_activated_without_execution_packet_is_not_certified() -> None:
    verdict = burn.final_burn_verdict(
        mode="FULL_ACTIVATED",
        stop_reason="",
        executable_packets=[],
        trade_outcomes=[],
        promotion_failures=[],
        profitability={"profitability": "INSUFFICIENT_SAMPLE"},
        safe_paper={},
        require_live_clicks_armed=True,
        min_sample_trades=3,
    )

    assert verdict == "FAIL_NO_EXECUTION_PACKET"


def test_full_activated_with_promotion_failures_reports_promotion_failure() -> None:
    verdict = burn.final_burn_verdict(
        mode="FULL_ACTIVATED",
        stop_reason="",
        executable_packets=[],
        trade_outcomes=[],
        promotion_failures=[{"denied_at": "NO_EXECUTION_LANE_ACCEPTED"}],
        profitability={"profitability": "INSUFFICIENT_SAMPLE"},
        safe_paper={},
        require_live_clicks_armed=True,
        min_sample_trades=3,
    )

    assert verdict == "FAIL_PROMOTION"


def test_technical_runtime_only_verdict_remains_runtime_only() -> None:
    verdict = burn.final_burn_verdict(
        mode="TECHNICAL",
        stop_reason="",
        executable_packets=[],
        trade_outcomes=[],
        promotion_failures=[],
        profitability={"profitability": "INSUFFICIENT_SAMPLE"},
        safe_paper={},
        require_live_clicks_armed=False,
        min_sample_trades=3,
    )

    assert verdict == "PASS_RUNTIME_ONLY_NO_TRADES"


def test_burn_reset_files_clear_stale_final_summaries() -> None:
    required = {
        "burn_in_summary.json",
        "profitability_summary.json",
        "safe_paper_summary.json",
        "precision_summary.json",
        "promotion_blocker_ranking.json",
    }

    assert required.issubset(set(burn.BURN_RESET_FILES))
