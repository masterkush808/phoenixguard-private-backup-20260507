from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
