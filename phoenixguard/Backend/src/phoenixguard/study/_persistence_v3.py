"""Small cross-process persistence primitives for bounded V3 study stores."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, BinaryIO, cast


MAX_STUDY_STORE_BYTES = 16 * 1024 * 1024


class StudyPersistenceError(RuntimeError):
    """Raised when a study store cannot be locked, validated, or persisted."""


_MUTEX_GUARD = threading.Lock()
_MUTEXES: dict[str, threading.RLock] = {}


def _process_mutex(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False)).casefold()
    with _MUTEX_GUARD:
        mutex = _MUTEXES.get(key)
        if mutex is None:
            mutex = threading.RLock()
            _MUTEXES[key] = mutex
        return mutex


if os.name == "nt":
    import msvcrt

    def _try_file_lock(handle: BinaryIO) -> bool:
        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock_file(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_file_lock(handle: BinaryIO) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock_file(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_store_lock(path: Path, *, timeout_seconds: float = 5.0) -> Generator[None, None, None]:
    """Serialize threads and processes touching the same study document."""

    timeout = float(timeout_seconds)
    if not 0.0 < timeout <= 60.0:
        raise StudyPersistenceError("lock timeout must be in (0, 60]")
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    mutex = _process_mutex(lock_path)
    with mutex:
        with lock_path.open("a+b") as handle:
            deadline = time.monotonic() + timeout
            while not _try_file_lock(handle):
                if time.monotonic() >= deadline:
                    raise StudyPersistenceError(f"timed out locking study store: {path}")
                time.sleep(0.02)
            try:
                yield
            finally:
                try:
                    _unlock_file(handle)
                except OSError as exc:
                    raise StudyPersistenceError(f"failed to unlock study store: {path}") from exc


def read_json_document(
    path: Path,
    *,
    max_bytes: int = MAX_STUDY_STORE_BYTES,
) -> dict[str, Any] | None:
    """Read one bounded JSON mapping; malformed documents fail closed."""

    if not path.exists():
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StudyPersistenceError(f"cannot stat study store: {path}") from exc
    if size < 0 or size > int(max_bytes):
        raise StudyPersistenceError(f"study store exceeds {int(max_bytes)} bytes: {path}")
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudyPersistenceError(f"study store is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(parsed, Mapping):
        raise StudyPersistenceError(f"study store root must be a mapping: {path}")
    return dict(cast(Mapping[str, Any], parsed))


def write_json_atomic(
    path: Path,
    payload: Mapping[str, Any],
    *,
    max_bytes: int = MAX_STUDY_STORE_BYTES,
) -> None:
    """Durably publish one JSON mapping with an atomic same-directory replace."""

    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StudyPersistenceError("study payload is not finite JSON data") from exc
    if len(encoded) > int(max_bytes):
        raise StudyPersistenceError(f"study payload exceeds {int(max_bytes)} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise StudyPersistenceError(f"failed to atomically publish study store: {path}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


__all__ = [
    "MAX_STUDY_STORE_BYTES",
    "StudyPersistenceError",
    "exclusive_store_lock",
    "read_json_document",
    "write_json_atomic",
]
