from __future__ import annotations

import os
from pathlib import Path
import site
import sys
from typing import TypedDict


class PythonEnvironmentStatus(TypedDict):
    schema_version: str
    ok: bool
    reason: str
    project_root: str
    expected_venv: str
    expected_venv_python: str
    process_executable: str
    sys_executable: str
    sys_prefix: str
    sys_base_prefix: str
    virtual_env: str
    phoenixguard_python_exe: str
    phoenixguard_python_process_exe: str
    strict_repo_venv: bool
    site_packages: list[str]
    path_head: list[str]


def project_root_from_runtime_module() -> Path:
    return Path(__file__).resolve().parents[4]


def expected_repo_venv(project_root: Path | None = None) -> Path:
    root = project_root or project_root_from_runtime_module()
    return root / ".venv"


def expected_repo_venv_python(project_root: Path | None = None) -> Path:
    root = project_root or project_root_from_runtime_module()
    scripts_python = root / ".venv" / "Scripts" / "python.exe"
    if scripts_python.exists() or os.name == "nt":
        return scripts_python
    return root / ".venv" / "bin" / "python"


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return str(left).lower() == str(right).lower()


def repo_venv_process_executable(project_root: Path | None = None) -> Path:
    """Return the repo venv executable used by PhoenixGuard launchers."""
    root = project_root or project_root_from_runtime_module()
    if os.name == "nt":
        process_host = root / ".venv" / "Scripts" / "phoenixguard-python.exe"
        if process_host.exists():
            return process_host
    return expected_repo_venv_python(root)


def strict_repo_venv_enabled() -> bool:
    return str(os.getenv("PHOENIXGUARD_STRICT_REPO_VENV", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def build_python_environment_status(project_root: Path | None = None) -> PythonEnvironmentStatus:
    root = project_root or project_root_from_runtime_module()
    expected_venv = expected_repo_venv(root)
    expected_python = expected_repo_venv_python(root)
    prefix_ok = _same_path(sys.prefix, expected_venv)
    virtual_env = os.getenv("VIRTUAL_ENV", "")
    env_python = os.getenv("PHOENIXGUARD_PYTHON_EXE", "")
    process_python = os.getenv("PHOENIXGUARD_PYTHON_PROCESS_EXE", "")
    virtual_env_ok = not virtual_env or _same_path(virtual_env, expected_venv)
    env_python_ok = not env_python or _same_path(env_python, expected_python)
    ok = bool(prefix_ok and virtual_env_ok and env_python_ok)
    if ok:
        reason = "repo .venv runtime active"
    elif not prefix_ok:
        reason = f"sys.prefix is not repo .venv: {sys.prefix}"
    elif not virtual_env_ok:
        reason = f"VIRTUAL_ENV is not repo .venv: {virtual_env}"
    else:
        reason = f"PHOENIXGUARD_PYTHON_EXE is not repo .venv python: {env_python}"
    return {
        "schema_version": "PG_PYTHON_ENVIRONMENT_V3",
        "ok": ok,
        "reason": reason,
        "project_root": str(root),
        "expected_venv": str(expected_venv),
        "expected_venv_python": str(expected_python),
        "process_executable": str(repo_venv_process_executable(root)),
        "sys_executable": sys.executable,
        "sys_prefix": sys.prefix,
        "sys_base_prefix": sys.base_prefix,
        "virtual_env": virtual_env,
        "phoenixguard_python_exe": env_python,
        "phoenixguard_python_process_exe": process_python,
        "strict_repo_venv": strict_repo_venv_enabled(),
        "site_packages": [str(path) for path in site.getsitepackages()],
        "path_head": [str(path) for path in sys.path[:8]],
    }


def assert_repo_venv_runtime(component: str, project_root: Path | None = None) -> PythonEnvironmentStatus:
    status = build_python_environment_status(project_root)
    if strict_repo_venv_enabled() and not status["ok"]:
        raise RuntimeError(f"PhoenixGuard {component} refused non-repo-venv Python runtime: {status['reason']}")
    return status
