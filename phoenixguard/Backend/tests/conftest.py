from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ.setdefault("PHOENIXGUARD_TRACING_DISABLED", "1")

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
