from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_SRC = PROJECT_ROOT / "Backend" / "src"
BACKEND_ROOT = PROJECT_ROOT / "Backend"
BACKEND_COMPAT = PROJECT_ROOT / "Backend" / "compat"
BACKEND_LAUNCH = PROJECT_ROOT / "Backend" / "launch"
FRONTEND_DASHBOARD = PROJECT_ROOT / "Frontend" / "dashboard"

for path in (BACKEND_SRC, BACKEND_ROOT, BACKEND_COMPAT, BACKEND_LAUNCH, FRONTEND_DASHBOARD, PROJECT_ROOT):
    text = str(path)
    if path.exists() and text not in sys.path:
        sys.path.insert(0, text)

os.environ.setdefault("PHOENIXGUARD_PROJECT_ROOT", str(PROJECT_ROOT))


def _configured_environment_name() -> str:
    explicit = str(os.environ.get("PHOENIXGUARD_PYTHON_ENV_NAME") or "").strip()
    if explicit:
        return explicit if explicit.startswith(".venv") and "/" not in explicit and "\\" not in explicit else ".venv-live"
    profile = str(os.environ.get("PHOENIXGUARD_PYTHON_PROFILE") or "live").strip().lower()
    return {
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
    }.get(profile, ".venv-live")


def _pin_repo_python_environment() -> None:
    """Keep PhoenixGuard children on the configured repo profile env instead of Windows global Python."""
    environment_name = _configured_environment_name()
    repo_venv_dir = PROJECT_ROOT / environment_name
    repo_venv_python = repo_venv_dir / "Scripts" / "python.exe"
    repo_venv_scripts = repo_venv_dir / "Scripts"
    if not repo_venv_python.exists():
        return
    python_text = str(repo_venv_python)
    scripts_text = str(repo_venv_scripts)
    os.environ["PHOENIXGUARD_PYTHON_ENV_NAME"] = environment_name
    os.environ["PHOENIXGUARD_PYTHON_EXE"] = python_text
    os.environ["PHOENIXGUARD_PYVENV_LAUNCHER"] = python_text
    os.environ["VIRTUAL_ENV"] = str(repo_venv_dir)
    existing_path = str(os.environ.get("PATH", "") or "")
    if scripts_text and not existing_path.lower().startswith(scripts_text.lower() + os.pathsep):
        os.environ["PATH"] = scripts_text + os.pathsep + existing_path


_pin_repo_python_environment()
