from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _repo_python_process_executable(repo_python: Path, repo_venv: Path) -> Path:
    if os.name != "nt":
        return repo_python
    process_host = repo_venv / "Scripts" / "phoenixguard-python.exe"
    if process_host.exists():
        return process_host
    return repo_python


def _pin_repo_python_environment(project_root: Path) -> None:
    repo_python = project_root / ".venv" / "Scripts" / "python.exe"
    repo_venv = project_root / ".venv"
    scripts_dir = repo_venv / "Scripts"
    if not repo_python.exists():
        return
    python_text = str(repo_python)
    process_python = _repo_python_process_executable(repo_python, repo_venv)
    process_text = str(process_python)
    os.environ["PHOENIXGUARD_PYTHON_EXE"] = python_text
    os.environ["PHOENIXGUARD_PYTHON_PROCESS_EXE"] = process_text
    os.environ["PHOENIXGUARD_PYVENV_LAUNCHER"] = python_text
    if os.name == "nt" and process_python != repo_python:
        os.environ["__PYVENV_LAUNCHER__"] = python_text
    os.environ["VIRTUAL_ENV"] = str(repo_venv)
    existing_path = str(os.environ.get("PATH", "") or "")
    scripts_text = str(scripts_dir)
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


def ensure_project_paths() -> Path:
    project_root = Path(__file__).resolve().parent
    _pin_repo_python_environment(project_root)
    for path in (
        project_root / "Backend" / "src",
        project_root / "Backend",
        project_root / "Backend" / "compat",
        project_root / "Backend" / "launch",
        project_root / "Frontend" / "dashboard",
        project_root,
    ):
        text = str(path)
        if path.exists() and text not in sys.path:
            sys.path.insert(0, text)
    os.environ.setdefault("PHOENIXGUARD_PROJECT_ROOT", str(project_root))
    return project_root
