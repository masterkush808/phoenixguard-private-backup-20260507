from __future__ import annotations

import ctypes
import gc
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import json
import sqlite3
from contextlib import closing


class SecurityManager:
    def __init__(self, data_dir: Path, logs_dir: Path, kdf_iterations: int = 600_000) -> None:
        self.data_dir = data_dir
        self.logs_dir = logs_dir
        self.kdf_iterations = kdf_iterations
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _salt_path(self) -> Path:
        return self.data_dir / "kdf_salt.bin"

    def derive_fernet(self, passphrase: str) -> Fernet:
        salt_path = self._salt_path()
        if not salt_path.exists():
            salt_path.write_bytes(os.urandom(16))
        salt = salt_path.read_bytes()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.kdf_iterations,
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        return Fernet(key)

    def encrypt_bytes(self, plaintext: bytes, fernet: Fernet) -> bytes:
        return fernet.encrypt(plaintext)

    def decrypt_bytes(self, token: bytes, fernet: Fernet) -> bytes:
        return fernet.decrypt(token)

    def encrypt_file(self, in_path: Path, out_path: Path, fernet: Fernet) -> None:
        out_path.write_bytes(self.encrypt_bytes(in_path.read_bytes(), fernet))

    def decrypt_file(self, in_path: Path, out_path: Path, fernet: Fernet) -> None:
        out_path.write_bytes(self.decrypt_bytes(in_path.read_bytes(), fernet))

    def secure_delete_file(self, path: Path) -> None:
        if not path.exists():
            return
        size = path.stat().st_size
        with path.open("r+b") as f:
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
        path.unlink(missing_ok=True)

    def secure_wipe_tensor_like(self, buffer: Any) -> None:
        try:
            if hasattr(buffer, "detach"):
                arr = buffer.detach().cpu().contiguous()
                data_ptr = arr.data_ptr()
                n_bytes = arr.numel() * arr.element_size()
                ctypes.memset(data_ptr, 0, n_bytes)
        except Exception:
            pass

    def memory_cleanup(self) -> None:
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        gc.collect()


class UnavailablePreferenceStore:
    def __init__(self, reason: str, logger: Any | None = None) -> None:
        self.reason = str(reason)
        self.logger = logger

    def insert_preference(self, row: dict[str, str]) -> None:
        _ = row
        return None

    def fetch_recent(self, limit: int = 200) -> list[dict[str, str]]:
        _ = limit
        return []

    def export_json(self) -> str:
        return "[]"

    def close(self) -> None:
        return None

    def __enter__(self) -> "UnavailablePreferenceStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


class EncryptedPreferenceStore:
    def __init__(self, db_path: Path, fernet: Fernet) -> None:
        self.db_path = db_path
        self.fernet = fernet
        self.tmp_path = db_path.with_suffix(".tmp.sqlite")
        self._initialize()

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._open_plaintext_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    image_hash TEXT,
                    chosen TEXT NOT NULL,
                    rejected TEXT,
                    reason TEXT,
                    annotation_text TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        self._sync_encrypted()
        self._cleanup_plaintext_files()

    def _plaintext_sidecars(self) -> tuple[Path, ...]:
        return (
            self.tmp_path,
            self.tmp_path.with_name(self.tmp_path.name + "-journal"),
            self.tmp_path.with_name(self.tmp_path.name + "-wal"),
            self.tmp_path.with_name(self.tmp_path.name + "-shm"),
        )

    def _cleanup_plaintext_files(self) -> None:
        for path in self._plaintext_sidecars():
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    def _ensure_plaintext_db(self) -> None:
        if self.tmp_path.exists():
            return
        if self.db_path.exists():
            plain = self.fernet.decrypt(self.db_path.read_bytes())
            self.tmp_path.write_bytes(plain)

    def _open_plaintext_conn(self) -> sqlite3.Connection:
        self._ensure_plaintext_db()
        conn = sqlite3.connect(str(self.tmp_path))
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _sync_encrypted(self) -> None:
        if self.tmp_path.exists():
            cipher = self.fernet.encrypt(self.tmp_path.read_bytes())
            cipher_tmp_path = self.db_path.with_name(self.db_path.name + ".tmp")
            try:
                cipher_tmp_path.write_bytes(cipher)
                os.replace(cipher_tmp_path, self.db_path)
            finally:
                try:
                    cipher_tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def insert_preference(self, row: dict[str, str]) -> None:
        conn = self._open_plaintext_conn()
        try:
            conn.execute(
                """
                INSERT INTO preferences (ts, image_hash, chosen, rejected, reason, annotation_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("ts", ""),
                    row.get("image_hash", ""),
                    row.get("chosen", ""),
                    row.get("rejected", ""),
                    row.get("reason", ""),
                    row.get("annotation_text", ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self._sync_encrypted()
        self._cleanup_plaintext_files()

    def fetch_recent(self, limit: int = 200) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        conn = self._open_plaintext_conn()
        try:
            with closing(conn.cursor()) as cur:
                cur.execute(
                "SELECT ts, image_hash, chosen, rejected, reason, annotation_text FROM preferences ORDER BY id DESC LIMIT ?",
                (limit,),
                )
                for ts, image_hash, chosen, rejected, reason, annotation_text in cur.fetchall():
                    out.append(
                        {
                            "ts": ts,
                            "image_hash": image_hash,
                            "chosen": chosen,
                            "rejected": rejected,
                            "reason": reason,
                            "annotation_text": annotation_text,
                        }
                    )
        finally:
            conn.close()
            self._cleanup_plaintext_files()
        return out

    def export_json(self) -> str:
        return json.dumps(self.fetch_recent(10_000), ensure_ascii=False, indent=2)

    def close(self) -> None:
        self._cleanup_plaintext_files()

    def __enter__(self) -> "EncryptedPreferenceStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def open_preference_store(
    db_path: Path,
    fernet: Fernet | None,
    *,
    logger: Any | None = None,
) -> EncryptedPreferenceStore | UnavailablePreferenceStore:
    if fernet is None:
        reason = "PHOENIXGUARD_PASSPHRASE is not set; encrypted preference storage is disabled."
        if logger is not None:
            logger.warning(reason)
        return UnavailablePreferenceStore(reason, logger=logger)
    try:
        return EncryptedPreferenceStore(db_path, fernet)
    except InvalidToken:
        reason = (
            "Encrypted preferences could not be decrypted with the configured "
            "PHOENIXGUARD_PASSPHRASE; feedback history is disabled until the passphrase "
            "or encrypted database is corrected."
        )
        if logger is not None:
            logger.warning(reason)
        return UnavailablePreferenceStore(reason, logger=logger)
    except Exception as exc:
        reason = f"Encrypted preference storage failed to initialize: {exc}"
        if logger is not None:
            logger.warning(reason)
        return UnavailablePreferenceStore(reason, logger=logger)
