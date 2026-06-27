from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
REPO_VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
REPO_VENV_DIR = PROJECT_ROOT / ".venv"
REPO_VENV_SCRIPTS = REPO_VENV_DIR / "Scripts"
REPO_PROCESS_PYTHON = REPO_VENV_SCRIPTS / "phoenixguard-python.exe"
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


def _pin_repo_python_environment() -> None:
    """Keep PhoenixGuard children on the repo venv instead of Windows global Python."""
    if not REPO_VENV_PYTHON.exists():
        return
    python_text = str(REPO_VENV_PYTHON)
    process_python = REPO_PROCESS_PYTHON if REPO_PROCESS_PYTHON.exists() else REPO_VENV_PYTHON
    process_text = str(process_python)
    scripts_text = str(REPO_VENV_SCRIPTS)
    os.environ["PHOENIXGUARD_PYTHON_EXE"] = python_text
    os.environ["PHOENIXGUARD_PYTHON_PROCESS_EXE"] = process_text
    os.environ["PHOENIXGUARD_PYVENV_LAUNCHER"] = python_text
    os.environ["VIRTUAL_ENV"] = str(REPO_VENV_DIR)
    if os.name == "nt" and process_python != REPO_VENV_PYTHON:
        os.environ["__PYVENV_LAUNCHER__"] = python_text
    existing_path = str(os.environ.get("PATH", "") or "")
    if scripts_text and not existing_path.lower().startswith(scripts_text.lower() + os.pathsep):
        os.environ["PATH"] = scripts_text + os.pathsep + existing_path
    try:
        multiprocessing.set_executable(process_text)
    except Exception:
        pass
    try:
        setattr(sys, "_base_executable", process_text)
    except Exception:
        pass


_pin_repo_python_environment()
