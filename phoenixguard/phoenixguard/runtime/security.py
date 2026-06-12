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
import tempfile
from contextlib import closing
from threading import RLock


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
        self.available = False

    def insert_preference(self, row: dict[str, str]) -> None:
        _ = row
        return None

    def fetch_recent(self, limit: int = 200) -> list[dict[str, str]]:
        _ = limit
        return []

    def export_json(self) -> str:
        return "[]"

    def insert_contact_brief(self, row: dict[str, Any]) -> None:
        _ = row
        return None

    def fetch_recent_contact_briefs(self, limit: int = 200) -> list[dict[str, Any]]:
        _ = limit
        return []

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
        self.available = True
        self._lock = RLock()
        self._initialize()

    def _initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._with_plaintext_conn(write=True, fn=lambda conn, plain_path: None)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                session_id TEXT,
                alias TEXT,
                creator TEXT,
                full_name TEXT NOT NULL,
                contact_channel TEXT NOT NULL,
                organization TEXT,
                purpose TEXT NOT NULL,
                consent_ack INTEGER NOT NULL DEFAULT 0,
                meta_json TEXT
            )
            """
        )

    def _open_plaintext_conn(self, plain_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(plain_path))
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _sync_encrypted_from_plaintext(self, plain_path: Path) -> None:
        cipher = self.fernet.encrypt(plain_path.read_bytes())
        cipher_tmp_path = self.db_path.with_name(self.db_path.name + ".tmp")
        try:
            cipher_tmp_path.write_bytes(cipher)
            os.replace(cipher_tmp_path, self.db_path)
        finally:
            try:
                cipher_tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _with_plaintext_conn(
        self,
        *,
        write: bool,
        fn: Any,
    ) -> Any:
        with self._lock:
            with tempfile.TemporaryDirectory(prefix=f"{self.db_path.stem}_prefs_") as tmp_dir_raw:
                tmp_dir = Path(tmp_dir_raw)
                plain_path = tmp_dir / "store.sqlite"
                if self.db_path.exists():
                    plain_path.write_bytes(self.fernet.decrypt(self.db_path.read_bytes()))
                conn = self._open_plaintext_conn(plain_path)
                try:
                    self._ensure_schema(conn)
                    result = fn(conn, plain_path)
                    if write:
                        conn.commit()
                        self._sync_encrypted_from_plaintext(plain_path)
                    return result
                finally:
                    conn.close()

    def insert_preference(self, row: dict[str, str]) -> None:
        def _insert(conn: sqlite3.Connection, _plain_path: Path) -> None:
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
        self._with_plaintext_conn(write=True, fn=_insert)

    def fetch_recent(self, limit: int = 200) -> list[dict[str, str]]:
        def _fetch(conn: sqlite3.Connection, _plain_path: Path) -> list[dict[str, str]]:
            out: list[dict[str, str]] = []
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
            return out
        return self._with_plaintext_conn(write=False, fn=_fetch)

    def insert_contact_brief(self, row: dict[str, Any]) -> None:
        meta_json = json.dumps(dict(row.get("meta", {})), ensure_ascii=False, sort_keys=True)

        def _insert(conn: sqlite3.Connection, _plain_path: Path) -> None:
            conn.execute(
                """
                INSERT INTO contact_briefs (
                    ts, session_id, alias, creator, full_name, contact_channel,
                    organization, purpose, consent_ack, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("ts", "")),
                    str(row.get("session_id", "")),
                    str(row.get("alias", "")),
                    str(row.get("creator", "")),
                    str(row.get("full_name", "")),
                    str(row.get("contact_channel", "")),
                    str(row.get("organization", "")),
                    str(row.get("purpose", "")),
                    int(bool(row.get("consent_ack", False))),
                    meta_json,
                ),
            )

        self._with_plaintext_conn(write=True, fn=_insert)

    def fetch_recent_contact_briefs(self, limit: int = 200) -> list[dict[str, Any]]:
        def _fetch(conn: sqlite3.Connection, _plain_path: Path) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            with closing(conn.cursor()) as cur:
                cur.execute(
                    """
                    SELECT
                        ts, session_id, alias, creator, full_name, contact_channel,
                        organization, purpose, consent_ack, meta_json
                    FROM contact_briefs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                for ts, session_id, alias, creator, full_name, contact_channel, organization, purpose, consent_ack, meta_json in cur.fetchall():
                    try:
                        meta = json.loads(str(meta_json or ""))
                    except Exception:
                        meta = {}
                    out.append(
                        {
                            "ts": ts,
                            "session_id": session_id,
                            "alias": alias,
                            "creator": creator,
                            "full_name": full_name,
                            "contact_channel": contact_channel,
                            "organization": organization,
                            "purpose": purpose,
                            "consent_ack": bool(consent_ack),
                            "meta": meta if isinstance(meta, dict) else {},
                        }
                    )
            return out

        return self._with_plaintext_conn(write=False, fn=_fetch)

    def export_json(self) -> str:
        return json.dumps(self.fetch_recent(10_000), ensure_ascii=False, indent=2)

    def close(self) -> None:
        return None

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
