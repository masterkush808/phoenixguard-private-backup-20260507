from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any


def setup_logger(log_file: Path, name: str = "phoenixguard") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.addHandler(fh)
    return logger


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def append_hash_chain(log_path: Path, payload: dict[str, Any]) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = "0" * 64
    if log_path.exists():
        try:
            with log_path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
                line = b""
                while pos > 0:
                    pos -= 1
                    f.seek(pos, os.SEEK_SET)
                    char = f.read(1)
                    if char == b"\n" and line:
                        break
                    line = char + line
                last_line = line.decode("utf-8").strip()
                if last_line:
                    prev_hash = last_line.split("|")[-1]
        except Exception:
            pass  # fallback to default prev_hash if any error

    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    current_hash = sha256_text(prev_hash + payload_json)
    line = f"{utc_now_iso()}|{payload_json}|{current_hash}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)
    return current_hash


def safe_json_loads(raw: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if fallback is None:
        fallback = {}
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except Exception:
                return fallback
    return fallback


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@lru_cache(maxsize=None)
def can_import_module_safely(import_stmt: str, timeout_sec: int = 20) -> bool:
    env = dict(os.environ)
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        result = subprocess.run(
            [sys.executable, "-c", import_stmt],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1, int(timeout_sec)),
            check=False,
            env=env,
        )
        return result.returncode == 0
    except Exception:
        return False


def can_import_sentence_transformers_safely(timeout_sec: int = 20) -> bool:
    return can_import_module_safely(
        "from sentence_transformers import SentenceTransformer",
        timeout_sec=timeout_sec,
    )


def can_import_chronos_safely(timeout_sec: int = 20) -> bool:
    return can_import_module_safely(
        "import chronos",
        timeout_sec=timeout_sec,
    )


def can_import_torchvision_safely(timeout_sec: int = 20) -> bool:
    return can_import_module_safely(
        "import torchvision",
        timeout_sec=timeout_sec,
    )
