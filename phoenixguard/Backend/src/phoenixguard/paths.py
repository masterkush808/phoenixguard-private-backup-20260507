from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def _candidate_is_project_root(path: Path) -> bool:
    return (
        (path / "requirements.txt").exists()
        and (path / "launch_phoenixguard_live_ready.ps1").exists()
        and (path / "Backend" / "src" / "phoenixguard").exists()
    )


def _resolve_project_root() -> Path:
    env_root = str(os.getenv("PHOENIXGUARD_PROJECT_ROOT", "") or "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if _candidate_is_project_root(candidate):
            return candidate
    for candidate in (PACKAGE_ROOT, *PACKAGE_ROOT.parents):
        if _candidate_is_project_root(candidate):
            return candidate
    return PACKAGE_ROOT.parents[2]


PROJECT_ROOT = _resolve_project_root()
BACKEND_ROOT = PROJECT_ROOT / "Backend"
BACKEND_SRC_ROOT = BACKEND_ROOT / "src"
FRONTEND_ROOT = PROJECT_ROOT / "Frontend"
BUSINESS_ROOT = PROJECT_ROOT / "Business"
DEVELOPER_ROOT = PROJECT_ROOT / "Developer"


__all__ = [
    "BACKEND_ROOT",
    "BACKEND_SRC_ROOT",
    "BUSINESS_ROOT",
    "DEVELOPER_ROOT",
    "FRONTEND_ROOT",
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
]
