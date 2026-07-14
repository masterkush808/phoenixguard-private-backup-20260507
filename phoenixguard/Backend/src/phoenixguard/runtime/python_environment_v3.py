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
    environment_profile: str
    environment_name: str
    expected_venv: str
    expected_venv_python: str
    process_executable: str
    sys_executable: str
    sys_prefix: str
    sys_base_prefix: str
    virtual_env: str
    phoenixguard_python_exe: str
    strict_repo_venv: bool
    site_packages: list[str]
    path_head: list[str]


def project_root_from_runtime_module() -> Path:
    return Path(__file__).resolve().parents[4]


PROFILE_ENVIRONMENTS: dict[str, str] = {
    "live": ".venv-live",
    "final_live": ".venv-live",
    "final-live": ".venv-live",
    "dev": ".venv-dev",
    "test": ".venv-dev",
    "testing": ".venv-dev",
    "training": ".venv-training",
    "train": ".venv-training",
    "business": ".venv-business",
    "share": ".venv-business",
    "docs": ".venv-docs",
    "docs-pdf": ".venv-docs",
}


LIVE_RUNTIME_COMMAND_TOKENS: tuple[str, ...] = (
    "start_phoenixguard_24_7_tracker.py",
    "start_phoenixguard_mobile_api.py",
    "shooter.py",
    "phoenixguard.runtime.model_council_daemon",
    "phoenixguard_disk_growth_guard.py",
    "uvicorn phoenixguard.mobile_api.app",
    "phoenixguard_mt4_file_bridge.py",
    "run_entry_allowance_burn.py",
    "manual_entry_alert",
)


def is_live_runtime_python_command(command_line: str) -> bool:
    """Return whether a Python command belongs to the live PhoenixGuard stack."""

    normalized = str(command_line or "").replace("/", "\\").casefold()
    return any(token in normalized for token in LIVE_RUNTIME_COMMAND_TOKENS)


def configured_python_profile() -> str:
    return str(os.getenv("PHOENIXGUARD_PYTHON_PROFILE") or "live").strip().lower() or "live"


def _safe_environment_name(value: str) -> str:
    name = value.strip()
    if not name:
        return ".venv-live"
    if "/" in name or "\\" in name or name in {".", ".."}:
        return ".venv-live"
    if not name.startswith(".venv"):
        return ".venv-live"
    return name


def configured_python_environment_name() -> str:
    explicit = str(os.getenv("PHOENIXGUARD_PYTHON_ENV_NAME") or "").strip()
    if explicit:
        return _safe_environment_name(explicit)
    return PROFILE_ENVIRONMENTS.get(configured_python_profile(), ".venv-live")


def expected_repo_venv(project_root: Path | None = None) -> Path:
    root = project_root or project_root_from_runtime_module()
    return root / configured_python_environment_name()


def expected_repo_venv_python(project_root: Path | None = None) -> Path:
    expected_venv = expected_repo_venv(project_root)
    scripts_python = expected_venv / "Scripts" / "python.exe"
    if scripts_python.exists() or os.name == "nt":
        return scripts_python
    return expected_venv / "bin" / "python"


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return str(left).lower() == str(right).lower()


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
    virtual_env_ok = not virtual_env or _same_path(virtual_env, expected_venv)
    env_python_ok = not env_python or _same_path(env_python, expected_python)
    ok = bool(prefix_ok and virtual_env_ok and env_python_ok)
    if ok:
        reason = "configured PhoenixGuard Python environment active"
    elif not prefix_ok:
        reason = f"sys.prefix is not configured PhoenixGuard environment: {sys.prefix}"
    elif not virtual_env_ok:
        reason = f"VIRTUAL_ENV is not configured PhoenixGuard environment: {virtual_env}"
    elif not env_python_ok:
        reason = f"PHOENIXGUARD_PYTHON_EXE is not configured PhoenixGuard python: {env_python}"
    else:
        reason = "configured PhoenixGuard Python environment status could not be classified"
    return {
        "schema_version": "PG_PYTHON_ENVIRONMENT_V3",
        "ok": ok,
        "reason": reason,
        "project_root": str(root),
        "environment_profile": configured_python_profile(),
        "environment_name": configured_python_environment_name(),
        "expected_venv": str(expected_venv),
        "expected_venv_python": str(expected_python),
        "process_executable": str(expected_python),
        "sys_executable": sys.executable,
        "sys_prefix": sys.prefix,
        "sys_base_prefix": sys.base_prefix,
        "virtual_env": virtual_env,
        "phoenixguard_python_exe": env_python,
        "strict_repo_venv": strict_repo_venv_enabled(),
        "site_packages": [str(path) for path in site.getsitepackages()],
        "path_head": [str(path) for path in sys.path[:8]],
    }


def assert_repo_venv_runtime(component: str, project_root: Path | None = None) -> PythonEnvironmentStatus:
    status = build_python_environment_status(project_root)
    if strict_repo_venv_enabled() and not status["ok"]:
        raise RuntimeError(f"PhoenixGuard {component} refused non-configured Python runtime: {status['reason']}")
    return status
