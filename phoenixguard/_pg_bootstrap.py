from __future__ import annotations

import os
import sys
from pathlib import Path


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


def _pin_repo_python_environment(project_root: Path) -> None:
    environment_name = _configured_environment_name()
    repo_python = project_root / environment_name / "Scripts" / "python.exe"
    repo_venv = project_root / environment_name
    scripts_dir = repo_venv / "Scripts"
    if not repo_python.exists():
        return
    python_text = str(repo_python)
    os.environ["PHOENIXGUARD_PYTHON_ENV_NAME"] = environment_name
    os.environ["PHOENIXGUARD_PYTHON_EXE"] = python_text
    os.environ["PHOENIXGUARD_PYVENV_LAUNCHER"] = python_text
    os.environ["VIRTUAL_ENV"] = str(repo_venv)
    existing_path = str(os.environ.get("PATH", "") or "")
    scripts_text = str(scripts_dir)
    if scripts_text and not existing_path.lower().startswith(scripts_text.lower() + os.pathsep):
        os.environ["PATH"] = scripts_text + os.pathsep + existing_path


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
