from __future__ import annotations

from _bootstrap import ensure_backend_paths

PROJECT_ROOT = ensure_backend_paths()

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence, cast

from phoenixguard.runtime.python_environment_v3 import (
    build_python_environment_status,
    expected_repo_venv,
    expected_repo_venv_python,
)


EXTRA_ENVIRONMENT_DIR_NAMES = (
    ".venv-live",
    ".venv-dev",
    ".venv-training",
    ".venv-business",
    "venv",
    "env",
)


@dataclass(frozen=True, slots=True)
class PhoenixProcessRow:
    process_id: int
    parent_process_id: int
    command_line: str
    executable_path: str
    uses_repo_venv_python: bool


@dataclass(frozen=True, slots=True)
class SingleVenvRuntimeReport:
    schema_version: str
    ok: bool
    reason: str
    project_root: str
    expected_venv: str
    expected_python: str
    runtime_dir: str
    runtime_dir_is_environment: bool
    environment_status: dict[str, object]
    extra_environment_dirs: list[str]
    removed_extra_environment_dirs: list[str]
    process_scan_status: str
    process_scan_error: str
    phoenix_python_processes: list[dict[str, object]]
    non_repo_venv_processes: list[dict[str, object]]
    port_scan_status: str
    port_scan_error: str
    port_8793_listeners: list[dict[str, object]]


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return str(left).lower() == str(right).lower()


def _as_mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return cast(list[object], value)
    if isinstance(value, dict):
        return [value]
    return []


def _int_from_mapping(payload: dict[str, object], key: str) -> int:
    raw = payload.get(key)
    try:
        return int(str(raw or "0"))
    except ValueError:
        return 0


def _str_from_mapping(payload: dict[str, object], key: str) -> str:
    return str(payload.get(key) or "")


@dataclass(frozen=True, slots=True)
class PowerShellJsonResult:
    ok: bool
    payload: object
    error: str


def _powershell_json(command: str) -> PowerShellJsonResult:
    if os.name != "nt":
        return PowerShellJsonResult(ok=True, payload=[], error="")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return PowerShellJsonResult(ok=False, payload=[], error=proc.stderr.strip() or proc.stdout.strip())
    if not proc.stdout.strip():
        return PowerShellJsonResult(ok=True, payload=[], error="")
    parsed: object = json.loads(proc.stdout)
    return PowerShellJsonResult(ok=True, payload=parsed, error="")


def _phoenix_python_processes(expected_python: Path) -> tuple[list[PhoenixProcessRow], str]:
    command = (
        "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*phoenixguard*' } | "
        "Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath | "
        "ConvertTo-Json -Depth 5"
    )
    rows: list[PhoenixProcessRow] = []
    expected_text = str(expected_python).lower()
    result = _powershell_json(command)
    if not result.ok:
        return [], result.error
    for item in _as_list(result.payload):
        payload = _as_mapping(item)
        command_line = _str_from_mapping(payload, "CommandLine")
        executable_path = _str_from_mapping(payload, "ExecutablePath")
        uses_repo_venv_python = expected_text in command_line.lower() or _same_path(executable_path, expected_python)
        rows.append(
            PhoenixProcessRow(
                process_id=_int_from_mapping(payload, "ProcessId"),
                parent_process_id=_int_from_mapping(payload, "ParentProcessId"),
                command_line=command_line,
                executable_path=executable_path,
                uses_repo_venv_python=uses_repo_venv_python,
            )
        )
    return rows, ""


def _port_8793_listeners() -> tuple[list[dict[str, object]], str]:
    command = (
        "Get-NetTCPConnection -LocalPort 8793 -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object LocalAddress,LocalPort,OwningProcess,State | "
        "ConvertTo-Json -Depth 5"
    )
    listeners: list[dict[str, object]] = []
    result = _powershell_json(command)
    if not result.ok:
        return [], result.error
    for item in _as_list(result.payload):
        listeners.append(_as_mapping(item))
    return listeners, ""


def _extra_environment_dirs(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for name in EXTRA_ENVIRONMENT_DIR_NAMES:
        path = project_root / name
        if path.is_dir():
            paths.append(path)
    return paths


def _remove_extra_environment_dirs(project_root: Path, paths: Sequence[Path]) -> list[Path]:
    removed: list[Path] = []
    root_resolved = project_root.resolve()
    for path in paths:
        resolved = path.resolve()
        if resolved.parent != root_resolved or resolved.name not in EXTRA_ENVIRONMENT_DIR_NAMES:
            raise RuntimeError(f"Refusing to remove unexpected environment path: {resolved}")
        shutil.rmtree(resolved)
        removed.append(resolved)
    return removed


def build_single_venv_runtime_report(*, cleanup_extra_envs: bool = False) -> SingleVenvRuntimeReport:
    expected_venv = expected_repo_venv(PROJECT_ROOT)
    expected_python = expected_repo_venv_python(PROJECT_ROOT)
    runtime_dir = Path(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or PROJECT_ROOT / "runtime" / "live")
    environment_status = dict(build_python_environment_status(PROJECT_ROOT))
    extra_dirs = _extra_environment_dirs(PROJECT_ROOT)
    removed_dirs = _remove_extra_environment_dirs(PROJECT_ROOT, extra_dirs) if cleanup_extra_envs else []
    if removed_dirs:
        extra_dirs = _extra_environment_dirs(PROJECT_ROOT)
    processes, process_scan_error = _phoenix_python_processes(expected_python)
    listeners, port_scan_error = _port_8793_listeners()
    non_repo_processes = [row for row in processes if not row.uses_repo_venv_python]
    ok = bool(environment_status.get("ok") is True and not extra_dirs and not non_repo_processes)
    if ok:
        reason = "single repo .venv runtime policy is clean"
    elif environment_status.get("ok") is not True:
        reason = str(environment_status.get("reason") or "current process is not repo .venv")
    elif extra_dirs:
        reason = "extra virtual environment directories exist"
    else:
        reason = "one or more PhoenixGuard Python processes are not using repo .venv"
    return SingleVenvRuntimeReport(
        schema_version="PG_SINGLE_VENV_RUNTIME_REPORT_V1",
        ok=ok,
        reason=reason,
        project_root=str(PROJECT_ROOT),
        expected_venv=str(expected_venv),
        expected_python=str(expected_python),
        runtime_dir=str(runtime_dir),
        runtime_dir_is_environment=False,
        environment_status=environment_status,
        extra_environment_dirs=[str(path) for path in extra_dirs],
        removed_extra_environment_dirs=[str(path) for path in removed_dirs],
        process_scan_status="UNAVAILABLE" if process_scan_error else "PASS",
        process_scan_error=process_scan_error,
        phoenix_python_processes=[asdict(row) for row in processes],
        non_repo_venv_processes=[asdict(row) for row in non_repo_processes],
        port_scan_status="UNAVAILABLE" if port_scan_error else "PASS",
        port_scan_error=port_scan_error,
        port_8793_listeners=listeners,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify PhoenixGuard is using only the repo .venv runtime.")
    parser.add_argument("--cleanup-extra-envs", action="store_true", help="Delete only known extra top-level venv dirs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_single_venv_runtime_report(cleanup_extra_envs=bool(args.cleanup_extra_envs))
    payload = asdict(report)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        verdict = "PASS" if report.ok else "FAIL"
        print(f"SINGLE_REPO_VENV_RUNTIME: {verdict}")
        print(f"reason={report.reason}")
        print(f"expected_python={report.expected_python}")
        print(f"runtime_dir={report.runtime_dir}")
        print(f"runtime_dir_is_environment={str(report.runtime_dir_is_environment).lower()}")
        print(f"process_scan_status={report.process_scan_status}")
        if report.process_scan_error:
            print(f"process_scan_error={report.process_scan_error}")
        print(f"phoenix_python_processes={len(report.phoenix_python_processes)}")
        print(f"non_repo_venv_processes={len(report.non_repo_venv_processes)}")
        print(f"extra_environment_dirs={len(report.extra_environment_dirs)}")
        print(f"port_scan_status={report.port_scan_status}")
        if report.port_scan_error:
            print(f"port_scan_error={report.port_scan_error}")
        print(f"port_8793_listeners={len(report.port_8793_listeners)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
