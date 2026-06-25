from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_project_paths() -> Path:
    project_root = Path(__file__).resolve().parent
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
