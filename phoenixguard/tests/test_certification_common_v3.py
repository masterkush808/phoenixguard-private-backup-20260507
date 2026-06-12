from __future__ import annotations

import subprocess

from tools import certification_common_v3 as cert


def test_wmic_python_process_fallback_preserves_comma_arguments(monkeypatch) -> None:
    output = """
CommandLine="C:\\repo\\.venv\\Scripts\\python.exe" start_phoenixguard_24_7_tracker.py --focus-region 0.02,0.06,0.76,0.94 --no-open-dashboard
ParentProcessId=2596
ProcessId=26564

CommandLine="C:\\repo\\.venv\\Scripts\\python.exe" shooter.py signal --window-query "The Most Innovative Trading Platform"
ParentProcessId=1
ProcessId=10380
"""

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(cert.subprocess, "run", fake_run)

    rows = cert._python_processes_wmic("primary timeout")

    assert rows[0]["ProcessId"] == 26564
    assert rows[0]["ParentProcessId"] == 2596
    assert "0.02,0.06,0.76,0.94" in rows[0]["CommandLine"]
    assert rows[1]["ProcessId"] == 10380
