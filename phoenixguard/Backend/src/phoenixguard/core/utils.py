from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Generator


_hash_chain_locks: dict[str, threading.Lock] = {}
_hash_chain_locks_guard = threading.Lock()


def _hash_chain_mutex(path: Path) -> threading.Lock:
    key = str(path)
    with _hash_chain_locks_guard:
        lock = _hash_chain_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _hash_chain_locks[key] = lock
        return lock


if os.name == "nt":
    import msvcrt

    def _acquire_file_lock(handle: Any) -> None:
        handle.seek(0)
        handle.write(b"\0")
        handle.flush()
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.02)

    def _release_file_lock(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire_file_lock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _release_file_lock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _hash_chain_write_guard(log_path: Path) -> Generator[None, None, None]:
    lock_path = log_path.with_name(log_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _hash_chain_mutex(lock_path):
        with lock_path.open("a+b") as lock_handle:
            _acquire_file_lock(lock_handle)
            try:
                yield
            finally:
                _release_file_lock(lock_handle)


def setup_logger(log_file: Path, name: str = "phoenixguard") -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
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
    with _hash_chain_write_guard(log_path):
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
                pass

        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        current_hash = sha256_text(prev_hash + payload_json)
        line = f"{utc_now_iso()}|{payload_json}|{current_hash}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
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


def can_import_sentence_transformers_safely(timeout_sec: int | None = None) -> bool:
    if timeout_sec is None:
        raw_timeout = str(
            os.getenv("PHOENIXGUARD_SENTENCE_TRANSFORMERS_IMPORT_TIMEOUT_SEC", "120") or "120"
        ).strip()
        try:
            timeout_sec = max(1, int(float(raw_timeout)))
        except (TypeError, ValueError):
            timeout_sec = 120
    return can_import_module_safely(
        "from sentence_transformers import SentenceTransformer",
        timeout_sec=timeout_sec,
    )


def sentence_transformer_runtime_kwargs(
    *,
    allow_remote_bootstrap: bool = False,
    force_download: bool = False,
) -> dict[str, Any]:
    """Return the bounded, deterministic constructor policy for text embedders.

    PhoenixGuard's text model is intentionally CPU-owned.  Disabling the
    Transformers meta-device loader avoids the Windows native storage crash
    seen when a low-memory process materializes cached weights.  Eager
    attention is also the smallest dependable implementation for this compact
    encoder and does not start an additional optimized-attention backend.
    """

    requested_device = str(
        os.getenv("PHOENIXGUARD_TEXT_EMBEDDER_DEVICE", "cpu") or "cpu"
    ).strip().lower()
    device = requested_device if requested_device in {"cpu", "cuda", "mps"} else "cpu"
    return {
        "device": device,
        "local_files_only": bool(not allow_remote_bootstrap and not force_download),
        "model_kwargs": {
            "attn_implementation": "eager",
            "low_cpu_mem_usage": False,
        },
    }


def can_import_chronos_safely(timeout_sec: int = 20) -> bool:
    return can_import_module_safely(
        "import chronos",
        timeout_sec=timeout_sec,
    )


def can_import_torchvision_safely(timeout_sec: int | None = None) -> bool:
    if timeout_sec is None:
        raw_timeout = str(
            os.getenv("PHOENIXGUARD_TORCHVISION_IMPORT_TIMEOUT_SEC", "60") or "60"
        ).strip()
        try:
            timeout_sec = max(1, int(float(raw_timeout)))
        except (TypeError, ValueError):
            timeout_sec = 60
    return can_import_module_safely(
        "import torchvision",
        timeout_sec=timeout_sec,
    )
