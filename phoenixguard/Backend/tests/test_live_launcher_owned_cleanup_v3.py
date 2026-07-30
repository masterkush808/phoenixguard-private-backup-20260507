from __future__ import annotations

from pathlib import Path

import pytest

from Developer.developer_tools import phoenixguard_kill_switch as kill_switch


ROOT = Path(__file__).resolve().parents[2]


def test_stack_process_requires_this_repository_and_known_runtime_token() -> None:
    repo = ROOT.resolve()
    owned = kill_switch.ProcessRow(
        pid=101,
        parent_pid=1,
        name="python.exe",
        command_line=(
            f'"{repo}\\.venv-live\\Scripts\\python.exe" '
            f'"{repo}\\Backend\\tools\\phoenixguard_disk_growth_guard.py" --apply'
        ),
        executable_path=f"{repo}\\.venv-live\\Scripts\\python.exe",
    )
    other_repo = kill_switch.ProcessRow(
        pid=102,
        parent_pid=1,
        name="python.exe",
        command_line=(
            r'"C:\other\phoenixguard\.venv-live\Scripts\python.exe" '
            r'"C:\other\phoenixguard\Backend\tools\phoenixguard_disk_growth_guard.py" --apply'
        ),
        executable_path=r"C:\other\phoenixguard\.venv-live\Scripts\python.exe",
    )
    unrelated = kill_switch.ProcessRow(
        pid=103,
        parent_pid=1,
        name="python.exe",
        command_line=f'"{repo}\\.venv-live\\Scripts\\python.exe" -m pytest',
        executable_path=f"{repo}\\.venv-live\\Scripts\\python.exe",
    )

    assert kill_switch.is_stack_process(owned, repo_root=repo, ancestor_pids=set()) is True
    assert kill_switch.is_stack_process(owned, repo_root=repo, ancestor_pids={101}) is False
    assert kill_switch.is_stack_process(other_repo, repo_root=repo, ancestor_pids=set()) is False
    assert kill_switch.is_stack_process(unrelated, repo_root=repo, ancestor_pids=set()) is False


def test_process_discovery_falls_back_to_psutil_when_cim_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = [kill_switch.ProcessRow(201, 1, "python.exe", "owned", "python.exe")]

    def deny_cim(_script: str) -> object:
        raise RuntimeError("Access denied")

    monkeypatch.setattr(kill_switch, "_powershell_json", deny_cim)
    monkeypatch.setattr(kill_switch, "_list_processes_psutil", lambda: expected)

    assert kill_switch.list_processes() == expected


def test_launcher_blocks_cleanup_until_lock_and_port_ownership_are_proven() -> None:
    launcher = (
        ROOT / "Backend" / "launch" / "launch_phoenixguard_live_ready.ps1"
    ).read_text(encoding="utf-8")

    required_contracts = (
        "PG_RUNTIME_SINGLETON_GUARD_V3",
        "state_version_owner",
        "runtime_owner_id",
        "candidateProcess.StartTime",
        "IPGlobalProperties",
        "phoenixguard_kill_switch.py",
        "Repository-scoped PhoenixGuard cleanup failed",
        "Port 8793 is still owned after scoped PhoenixGuard cleanup",
    )
    for contract in required_contracts:
        assert contract in launcher


def test_pocket_live_launcher_disables_irrelevant_mt4_bridge_by_default() -> None:
    launcher = (
        ROOT / "Backend" / "launch" / "launch_phoenixguard_live_ready.ps1"
    ).read_text(encoding="utf-8")

    assert "innovative trading platform" in launcher
    assert "$env:PHOENIXGUARD_MT4_BRIDGE_ENABLED = if ($pocketOptionOnly) { '0' } else { '1' }" in launcher
    assert "if ($launchMt4Bridge)" in launcher
    assert "mt4_bridge_enabled = [bool]$launchMt4Bridge" in launcher
