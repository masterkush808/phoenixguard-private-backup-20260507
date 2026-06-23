from __future__ import annotations
import pytest

from pathlib import Path
import subprocess
import sys
from typing import Any

from tools import certification_common_v3 as cert

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tools import certify_v3_full_system_burn_in as burn
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

    rows = cert._python_processes_wmic("primary timeout")

    assert rows[0]["ProcessId"] == 26564
    assert rows[0]["ParentProcessId"] == 2596
    assert "0.03,0.13,0.87,0.96" in rows[0]["CommandLine"]
    assert rows[1]["ProcessId"] == 10380


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


def test_full_activated_without_execution_packet_is_not_certified() -> None:
    verdict = burn._final_burn_verdict(
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
    verdict = burn._final_burn_verdict(
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
    verdict = burn._final_burn_verdict(
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
