from __future__ import annotations
from pathlib import Path
import json
from datetime import datetime
from typing import Sequence, Mapping, Any
from phoenixguard.core.config import RUNTIME

REGISTRY_DIR = RUNTIME.data_dir / "overlay_registry"
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)


def persist_overlay_objects(session_id: str, objects: Sequence[Mapping[str, Any]]) -> Path:
    """Append overlay objects for a session to a JSONL file and return path."""
    session_file = REGISTRY_DIR / f"{session_id}.jsonl"
    ts = datetime.utcnow().isoformat() + "Z"
    try:
        with session_file.open("a", encoding="utf-8") as fh:
            for obj in objects:
                row = {
                    "timestamp": ts,
                    "session_id": session_id,
                    "object": obj,
                }
                fh.write(json.dumps(row, default=str) + "\n")
    except Exception:
        # Best-effort; don't raise to avoid breaking caller
        pass
    return session_file
