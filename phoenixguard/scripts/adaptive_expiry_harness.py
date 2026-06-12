"""Simple harness to exercise _choose_adaptive_expiry diagnostics.

Run from repo root:
    python scripts/adaptive_expiry_harness.py

This will load the `shooter.py` module by path and call the adaptive
selection helper with several synthetic payloads, printing results.
"""
from __future__ import annotations
import json
import logging
import importlib.util
import sys
from types import SimpleNamespace
from pathlib import Path
from importlib.abc import Loader
from typing import Any, Dict, List, Tuple

LOG = logging.getLogger("adaptive_harness")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOOTER_PATH = ROOT / "shooter.py"
if not SHOOTER_PATH.exists():
    print("Cannot locate 'shooter.py' at expected path:", SHOOTER_PATH)
    sys.exit(2)

spec = importlib.util.spec_from_file_location("shooter_mod", str(SHOOTER_PATH))
if spec is None:
    print("Failed to create import spec for:", SHOOTER_PATH)
    sys.exit(2)
shooter = importlib.util.module_from_spec(spec)
loader = spec.loader
assert loader is not None
cast_loader: Loader = loader
cast_loader.exec_module(shooter)

_choose = getattr(shooter, "_choose_adaptive_expiry")

samples: List[Tuple[str, Dict[str, Any]]] = [
    ("explicit_expiry", {"expiry_seconds": 90, "signal_id": "s-explicit"}),
    ("candle_notation", {"candle_notation": "2c", "focus_timeframe": "M1", "signal_id": "s-candle"}),
    ("countdown", {"countdown_seconds": 12, "signal_id": "s-countdown"}),
    ("kernel_p1", {"decision_kernel": {"p_trigger_next_1": 0.5, "state": "armed"}, "focus_timeframe": "M1", "signal_id": "s-kernel1"}),
    ("kernel_p3", {"decision_kernel": {"p_trigger_next_3": 0.6, "state": "armed"}, "focus_timeframe": "M1", "signal_id": "s-kernel3"}),
    ("scenario_confident", {"scenario_analysis": {"top_scenario": {"probability": 0.85}}, "focus_timeframe": "M5", "signal_id": "s-scenario"}),
    ("fallback", {},),
]

args = SimpleNamespace(adaptive_verbose=True)

for name, payload in samples:
    chosen = _choose(payload, 59, args)
    print(f"{name}: chosen={chosen}s payload_sample={json.dumps({'signal_id': payload.get('signal_id')}, ensure_ascii=False)}")

print("Harness complete.")
