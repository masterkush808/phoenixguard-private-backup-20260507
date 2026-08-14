import argparse
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "Developer" / "developer_tools" / "phoenixguard_kill_switch.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("phoenixguard_kill_switch_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_kill_switch_defaults_to_one_second_capture_interval():
    module = _load_module()

    args = module.build_parser().parse_args([])

    assert args.capture_interval_sec == 1.0


def test_kill_switch_relaunch_passes_one_second_capture_interval(monkeypatch):
    module = _load_module()
    captured: list[str] = []

    def run(command, **_kwargs):
        captured.extend(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    args = argparse.Namespace(
        session_id="pocket-live-8788",
        capture_interval_sec=module.DEFAULT_CAPTURE_INTERVAL_SEC,
        warmup_seconds=20,
        shooter_poll_sec=30.0,
        open_browser=False,
        disable_shooter=False,
        broker_window_hwnd=0,
        broker_window_query="",
        dry_run=False,
    )

    assert module.relaunch(args) == 0
    interval_index = captured.index("-CaptureIntervalSec") + 1
    assert captured[interval_index] == "1.0"


def test_kill_switch_scopes_the_direct_trade_bridge_as_part_of_the_stack():
    module = _load_module()
    bridge_path = PROJECT_ROOT / "Backend" / "launch" / "phoenixguard_direct_trade_bridge.py"
    row = module.ProcessRow(
        pid=8080,
        parent_pid=1,
        name="python.exe",
        command_line=f'python.exe "{bridge_path}" --session-id pocket-live-8788',
        executable_path=str(PROJECT_ROOT / ".venv-live" / "Scripts" / "python.exe"),
    )

    assert module.is_stack_process(row, repo_root=PROJECT_ROOT, ancestor_pids=set()) is True
