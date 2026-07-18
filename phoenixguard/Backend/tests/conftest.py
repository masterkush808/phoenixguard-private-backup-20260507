from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ.setdefault("PHOENIXGUARD_TRACING_DISABLED", "1")
os.environ.setdefault("PHOENIXGUARD_PYTHON_PROFILE", "test")
os.environ.setdefault("PHOENIXGUARD_STRICT_REPO_VENV", "0")
os.environ.setdefault("PHOENIXGUARD_DISABLE_TRACKER_STOP_HOTKEY", "1")
os.environ.setdefault("PHOENIXGUARD_BACKGROUND_WARMUP_ON_LAUNCH", "0")

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
