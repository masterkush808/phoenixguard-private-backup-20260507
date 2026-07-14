from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Sequence, cast
from urllib import request
from urllib.error import URLError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION_ID = "pocket-live-8788"
DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_PORTS = (8793, 8767, 8787, 18180, 18181, 3210, 3310, 7861)

KNOWN_STACK_TOKENS = (
    "launch_phoenixguard_live_ready.ps1",
    "start_phoenixguard_full_local.ps1",
    "start_phoenixguard_24_7_tracker.ps1",
    "start_phoenixguard_24_7_tracker.py",
    "start_phoenixguard_mobile_api.ps1",
    "start_phoenixguard_mobile_api.py",
    "start_phoenixguard_share.ps1",
    "start-phoenixguardvmmonitor.ps1",
    "start-phoenixguardvmshare.ps1",
    "shooter.py",
    "phoenixguard_disk_growth_guard.py",
    "phoenixguard_mt4_file_bridge.py",
    "phoenixguard.runtime.model_council_daemon",
    "uvicorn phoenixguard.mobile_api.app",
    "run_entry_allowance_burn.py",
    "manual_entry_alert",
    "business_mock",
    "share_phoenixguard.py",
    "next dev --hostname 127.0.0.1 --port 3210",
    "next start --hostname 127.0.0.1 --port 3310",
    "node_modules\\next\\dist\\server\\lib\\start-server.js",
    "runtime\\live\\chrome_dashboard_profile",
)

STACK_PROCESS_NAMES = {
    "chrome.exe",
    "msedge.exe",
    "node.exe",
    "powershell.exe",
    "pwsh.exe",
    "python.exe",
    "pythonw.exe",
}

NON_STACK_COMMAND_TOKENS = (
    "\\.vscode\\extensions\\",
    "\\microsoft python language server\\",
    "\\pylance\\",
    "lsp_server.py",
    "pyright-langserver",
    "ruff server",
    "isort-",
)


@dataclass(frozen=True, slots=True)
class ProcessRow:
    pid: int
    parent_pid: int
    name: str
    command_line: str
    executable_path: str


def _powershell_json(script: str) -> object:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "PowerShell command failed")
    output = proc.stdout.strip()
    if not output:
        return []
    return cast(object, json.loads(output))


def _as_list(payload: object) -> list[object]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return cast(list[object], payload)
    return [payload]


def _int_value(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _str_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def list_processes() -> list[ProcessRow]:
    payload = _powershell_json(
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine,ExecutablePath | "
        "ConvertTo-Json -Depth 5"
    )
    rows: list[ProcessRow] = []
    for item in _as_list(payload):
        if not isinstance(item, dict):
            continue
        data = cast(dict[str, object], item)
        rows.append(
            ProcessRow(
                pid=_int_value(data.get("ProcessId")),
                parent_pid=_int_value(data.get("ParentProcessId")),
                name=_str_value(data.get("Name")),
                command_line=_str_value(data.get("CommandLine")),
                executable_path=_str_value(data.get("ExecutablePath")),
            )
        )
    return [row for row in rows if row.pid > 0]


def list_port_owner_pids(ports: Sequence[int]) -> set[int]:
    if not ports:
        return set()
    joined = ",".join(str(int(port)) for port in ports)
    try:
        payload = _powershell_json(
            f"Get-NetTCPConnection -LocalPort {joined} -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object LocalAddress,LocalPort,OwningProcess,State | "
            "ConvertTo-Json -Depth 5"
        )
    except RuntimeError:
        return set()
    owners: set[int] = set()
    for item in _as_list(payload):
        if isinstance(item, dict):
            data = cast(dict[str, object], item)
            owners.add(_int_value(data.get("OwningProcess")))
    return {pid for pid in owners if pid > 0}


def _norm(text: str) -> str:
    return str(text or "").replace("/", "\\").lower()


def _current_ancestor_pids(rows_by_pid: dict[int, ProcessRow]) -> set[int]:
    ancestors: set[int] = set()
    parent = rows_by_pid.get(os.getpid())
    while parent and parent.parent_pid and parent.parent_pid not in ancestors:
        ancestors.add(parent.parent_pid)
        parent = rows_by_pid.get(parent.parent_pid)
    return ancestors


def is_stack_process(row: ProcessRow, *, repo_root: Path, ancestor_pids: set[int]) -> bool:
    if row.pid == os.getpid():
        return False
    command = _norm(row.command_line)
    if not command:
        return False
    if any(token in command for token in NON_STACK_COMMAND_TOKENS):
        return False
    token_match = any(token in command for token in KNOWN_STACK_TOKENS)
    if token_match:
        return True
    if row.pid in ancestor_pids:
        return False
    return False


def collect_stack_pids(rows: Sequence[ProcessRow], ports: Sequence[int]) -> tuple[set[int], dict[int, ProcessRow]]:
    rows_by_pid = {row.pid: row for row in rows}
    ancestor_pids = _current_ancestor_pids(rows_by_pid)
    targets = {
        row.pid
        for row in rows
        if is_stack_process(row, repo_root=PROJECT_ROOT, ancestor_pids=ancestor_pids)
    }
    for owner_pid in list_port_owner_pids(ports):
        row = rows_by_pid.get(owner_pid)
        if not row or row.pid in ancestor_pids:
            continue
        if is_stack_process(row, repo_root=PROJECT_ROOT, ancestor_pids=ancestor_pids):
            targets.add(owner_pid)
    queue = list(targets)
    while queue:
        parent_pid = queue.pop(0)
        for row in rows:
            if row.parent_pid == parent_pid and row.pid != os.getpid() and row.pid not in ancestor_pids:
                if row.pid not in targets:
                    targets.add(row.pid)
                    queue.append(row.pid)
    return targets, rows_by_pid


def root_target_pids(targets: set[int], rows_by_pid: dict[int, ProcessRow]) -> list[int]:
    roots = [pid for pid in targets if rows_by_pid.get(pid, ProcessRow(pid, 0, "", "", "")).parent_pid not in targets]
    return sorted(roots)


def post_tracker_stop(base_url: str, session_id: str) -> None:
    for suffix in ("emergency-stop", "stop"):
        url = f"{base_url.rstrip('/')}/v1/mobile/window-tracker/sessions/{session_id}/{suffix}"
        try:
            req = request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            request.urlopen(req, timeout=4).read()
            print(f"tracker_stop={suffix}: requested")
        except (OSError, URLError, TimeoutError):
            print(f"tracker_stop={suffix}: unavailable")


def kill_targets(targets: set[int], rows_by_pid: dict[int, ProcessRow], *, dry_run: bool) -> int:
    roots = root_target_pids(targets, rows_by_pid)
    if not roots:
        print("kill_switch: no PhoenixGuard stack processes found")
        return 0
    print(f"kill_switch: target_processes={len(targets)} root_processes={len(roots)}")
    for pid in roots:
        row = rows_by_pid.get(pid)
        label = f"{pid} {row.name if row else ''}".strip()
        print(f"kill_switch: {'would stop' if dry_run else 'stopping'} root {label}")
        if dry_run:
            continue
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], cwd=str(PROJECT_ROOT), check=False)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    if not dry_run:
        time.sleep(3)
    return len(targets)


def clean_runtime_state(args: argparse.Namespace) -> int:
    cleaner = PROJECT_ROOT / "Backend" / "tools" / "clean_v3_runtime_state.py"
    command = [sys.executable, str(cleaner), "--apply"]
    print("kill_switch: cleaning V3 runtime/cache state")
    print("kill_switch: " + " ".join(command))
    if args.dry_run:
        return 0
    try:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            timeout=max(5.0, float(args.clean_timeout_sec)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("kill_switch: runtime cleanup timed out")
        return 1
    return int(proc.returncode)


def relaunch(args: argparse.Namespace) -> int:
    launcher = PROJECT_ROOT / "Backend" / "launch" / "launch_phoenixguard_live_ready.ps1"
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(launcher),
        "-SessionId",
        args.session_id,
        "-CaptureIntervalSec",
        str(args.capture_interval_sec),
        "-WarmupSeconds",
        str(args.warmup_seconds),
        "-ShooterPollSec",
        str(args.shooter_poll_sec),
    ]
    if not args.open_browser:
        command.append("-NoBrowser")
    if args.disable_shooter:
        command.append("-DisableShooter")
    if args.broker_window_hwnd > 0:
        command.extend(["-BrokerWindowHwnd", str(args.broker_window_hwnd)])
    if args.broker_window_query:
        command.extend(["-BrokerWindowQuery", args.broker_window_query])
    print("kill_switch: relaunching canonical V3 live stack")
    print("kill_switch: " + " ".join(command))
    if args.dry_run:
        return 0
    return subprocess.run(command, cwd=str(PROJECT_ROOT), check=False).returncode


def verify_runtime(base_url: str, session_id: str, timeout_sec: float) -> bool:
    deadline = time.time() + max(1.0, timeout_sec)
    while time.time() < deadline:
        try:
            health = json.loads(request.urlopen(f"{base_url.rstrip('/')}/v1/mobile/health", timeout=5).read().decode("utf-8"))
            live = json.loads(
                request.urlopen(
                    f"{base_url.rstrip('/')}/v1/mobile/live/state/v3/{session_id}?mode=CLEAN_LIVE&compact=1",
                    timeout=10,
                )
                .read()
                .decode("utf-8")
            )
            if str(health.get("status") or "") == "ok" and str(live.get("status") or "") in {"running", "tracking"}:
                print(
                    "kill_switch: verified "
                    f"health={health.get('status')} session={live.get('session_id')} "
                    f"frame={live.get('frame_id')} overlays={live.get('overlay_count')}"
                )
                return True
        except (OSError, URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(2)
    print("kill_switch: verification timed out")
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kill every detected PhoenixGuard live-stack process, then relaunch FINAL_LIVE."
    )
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--capture-interval-sec", type=float, default=15.0)
    parser.add_argument("--warmup-seconds", type=int, default=20)
    parser.add_argument("--shooter-poll-sec", type=float, default=15.0)
    parser.add_argument("--broker-window-query", default="")
    parser.add_argument("--broker-window-hwnd", type=int, default=0)
    parser.add_argument("--open-browser", action="store_true", help="Allow the launcher to open the dashboard browser.")
    parser.add_argument("--disable-shooter", action="store_true", help="Relaunch without the package reporter.")
    parser.add_argument("--kill-only", action="store_true", help="Stop the stack and do not relaunch.")
    parser.add_argument("--dry-run", action="store_true", help="Print targets and relaunch command without stopping anything.")
    parser.add_argument("--skip-api-stop", action="store_true", help="Skip tracker emergency-stop/stop API calls.")
    parser.add_argument("--skip-clean", action="store_true", help="Skip V3 runtime/cache cleanup before relaunch.")
    parser.add_argument("--clean-timeout-sec", type=float, default=60.0)
    parser.add_argument("--skip-verify", action="store_true", help="Do not poll health/live-state after relaunch.")
    parser.add_argument("--verify-timeout-sec", type=float, default=90.0)
    parser.add_argument("--ports", default=",".join(str(port) for port in DEFAULT_PORTS))
    return parser


def _parse_ports(raw: str) -> tuple[int, ...]:
    ports: list[int] = []
    for item in str(raw or "").replace(";", ",").split(","):
        text = item.strip()
        if not text:
            continue
        ports.append(int(text))
    return tuple(dict.fromkeys(ports))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not PROJECT_ROOT.exists():
        raise RuntimeError(f"Project root not found: {PROJECT_ROOT}")
    ports = _parse_ports(args.ports)
    print(f"kill_switch: project_root={PROJECT_ROOT}")
    print(f"kill_switch: session_id={args.session_id} base_url={args.base_url}")
    if not args.skip_api_stop and not args.dry_run:
        post_tracker_stop(args.base_url, args.session_id)
    rows = list_processes()
    targets, rows_by_pid = collect_stack_pids(rows, ports)
    kill_targets(targets, rows_by_pid, dry_run=bool(args.dry_run))
    if args.kill_only:
        return 0
    if not args.skip_clean:
        clean_code = clean_runtime_state(args)
        if clean_code != 0:
            return clean_code
    code = relaunch(args)
    if code != 0:
        return code
    if args.skip_verify or args.dry_run:
        return 0
    return 0 if verify_runtime(args.base_url, args.session_id, args.verify_timeout_sec) else 1


if __name__ == "__main__":
    raise SystemExit(main())
