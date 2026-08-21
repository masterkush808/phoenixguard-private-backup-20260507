"""Durable, exact, study-only candle ledger for PhoenixGuard V3.

The ledger stores one canonical micro-feature record for each stable
``(symbol, timeframe, candle identity)`` tuple. Re-observing an older candle in
an overlapping tracker window updates that row instead of counting a new
candle. SQLite WAL and an immediate transaction provide restart and
cross-process safety without retaining images, arbitrary source payloads, or
execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, cast

from phoenixguard.study.candle_intelligence_v3 import (
    CANDLE_INTELLIGENCE_SCHEMA_VERSION,
)


CANDLE_LEDGER_SCHEMA_VERSION = "PG_CANDLE_LEDGER_V3"
CANDLE_LEDGER_SQL_SCHEMA_VERSION = 1
DEFAULT_MAX_CANDLE_RECORDS = 1_000_000
MAX_CANDLE_LEDGER_BATCH = 4_096
# Storage keeps a million records; "recent" reads stay a bounded window so
# per-frame queries do not materialize the whole ledger.
MAX_RECENT_CANDLE_READ = 4_096


class CandleLedgerValidationError(ValueError):
    """Raised when stable candle evidence violates the ledger contract."""


class CandleLedgerCapacityError(RuntimeError):
    """Raised before mutation when an atomic batch would exceed capacity."""


class CandleLedgerPersistenceError(RuntimeError):
    """Raised when the SQLite ledger cannot be opened or transacted safely."""


_COORDINATE_SPACES = {
    "PRICE",
    "NORMALIZED_PRICE_PROXY",
    "PIXEL_PRICE_PROXY",
}
_SOURCE_KEYS = {
    "open",
    "high",
    "low",
    "close",
    "open_proxy",
    "high_proxy",
    "low_proxy",
    "close_proxy",
    "open_y_px",
    "wick_top_px",
    "wick_bottom_px",
    "close_y_px",
}
_OHLC_KEYS = ("open", "high", "low", "close")
_GEOMETRY_KEYS = (
    "range_size",
    "body_size",
    "upper_wick_size",
    "lower_wick_size",
)
_RATIO_KEYS = (
    "body_to_range",
    "upper_wick_to_range",
    "lower_wick_to_range",
    "total_wick_to_range",
    "body_to_total_wick",
    "upper_wick_to_body",
    "lower_wick_to_body",
    "close_location_in_range",
    "range_vs_sequence_median",
)
_STABLE_MARKERS = (
    "identity_stable",
    "stable_identity",
    "candle_identity_stable",
)
_IDENTITY_FIELDS = (
    "stable_candle_identity",
    "candle_identity",
    "closed_candle_identity",
    "candle_id",
)
_UNSTABLE_IDENTITY = re.compile(
    r"^(?:unknown|none|null|forming|current|latest|candle[-_:]?\d+|index[-_:]?\d+)$",
    re.IGNORECASE,
)
_CREATE_CANDLES_SQL = """
CREATE TABLE IF NOT EXISTS candle_records (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    candle_identity TEXT NOT NULL,
    candle_schema_version TEXT NOT NULL,
    timestamp_json TEXT NOT NULL,
    coordinate_space TEXT NOT NULL,
    source_values_json TEXT NOT NULL,
    ohlc_json TEXT NOT NULL,
    exact_geometry_json TEXT NOT NULL,
    ratios_json TEXT NOT NULL,
    direction TEXT NOT NULL,
    candle_type TEXT NOT NULL,
    personality TEXT NOT NULL,
    regime TEXT NOT NULL,
    relation_to_previous TEXT NOT NULL,
    interaction_json TEXT NOT NULL,
    sequence_position_json TEXT NOT NULL,
    range_size REAL NOT NULL,
    body_ratio REAL NOT NULL,
    upper_wick_ratio REAL NOT NULL,
    lower_wick_ratio REAL NOT NULL,
    payload_hash TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL CHECK (observation_count >= 1),
    first_seen_ordinal INTEGER NOT NULL CHECK (first_seen_ordinal >= 1),
    last_seen_ordinal INTEGER NOT NULL CHECK (last_seen_ordinal >= first_seen_ordinal),
    PRIMARY KEY (symbol, timeframe, candle_identity)
) WITHOUT ROWID
"""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _canonical_identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise CandleLedgerValidationError(f"{field} is required")
    if len(text) > maximum:
        raise CandleLedgerValidationError(f"{field} exceeds {maximum} characters")
    if any(ord(character) < 32 for character in text):
        raise CandleLedgerValidationError(f"{field} contains control characters")
    return text


def _pair_id(symbol: str, timeframe: str) -> str:
    digest = hashlib.sha256(f"{symbol}|{timeframe}".encode()).hexdigest()[:24]
    return f"pair-{digest}"


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise CandleLedgerValidationError(f"{field} must be a finite number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CandleLedgerValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise CandleLedgerValidationError(f"{field} must be a finite number")
    return parsed


def _non_negative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise CandleLedgerValidationError(f"{field} must be a non-negative integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CandleLedgerValidationError(f"{field} must be a non-negative integer") from exc
    if parsed < 0 or str(value).strip() not in {str(parsed), f"{parsed}.0"}:
        raise CandleLedgerValidationError(f"{field} must be a non-negative integer")
    return parsed


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise CandleLedgerValidationError(f"{field} must be boolean")
    return value


def _bounded_token(value: object, *, field: str, maximum: int = 96) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise CandleLedgerValidationError(f"{field} is required")
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise CandleLedgerValidationError(f"{field} is not a bounded public token")
    return text


def _timestamp(value: object) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise CandleLedgerValidationError("timestamp cannot be boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CandleLedgerValidationError("timestamp must be finite")
        return value
    text = str(value).strip()
    if len(text) > 96 or any(ord(character) < 32 for character in text):
        raise CandleLedgerValidationError("timestamp is not a bounded public value")
    return text or None


def _numeric_map(
    value: object,
    *,
    field: str,
    allowed: Sequence[str],
    required: Sequence[str] = (),
    nullable: Sequence[str] = (),
) -> dict[str, float | None]:
    source = _mapping(value)
    if not source:
        raise CandleLedgerValidationError(f"{field} must be a non-empty mapping")
    missing = [name for name in required if name not in source]
    if missing:
        raise CandleLedgerValidationError(
            f"{field} is missing required values: {', '.join(missing)}"
        )
    result: dict[str, float | None] = {}
    nullable_set = set(nullable)
    for name in allowed:
        if name not in source:
            continue
        raw = source.get(name)
        if raw is None and name in nullable_set:
            result[name] = None
        else:
            result[name] = _finite(raw, field=f"{field}.{name}")
    return result


def _stable_candle_identity(row: Mapping[str, Any]) -> str | None:
    marker: bool | None = None
    for name in _STABLE_MARKERS:
        if name not in row:
            continue
        value = row.get(name)
        if isinstance(value, bool):
            marker = value
            break
        return None
    if marker is not True:
        return None
    candidate = ""
    for field in _IDENTITY_FIELDS:
        raw = row.get(field)
        if raw is not None and str(raw).strip():
            candidate = str(raw).strip()
            break
    if not candidate:
        raw_timestamp = row.get("timestamp")
        if raw_timestamp is not None and str(raw_timestamp).strip():
            candidate = f"timestamp:{str(raw_timestamp).strip()}"
    if (
        not candidate
        or len(candidate) > 256
        or any(ord(character) < 32 for character in candidate)
        or _UNSTABLE_IDENTITY.fullmatch(candidate)
    ):
        return None
    return candidate


def _canonical_interaction(value: object) -> dict[str, Any]:
    source = _mapping(value)
    rejection = _mapping(source.get("rejection"))
    acceptance = _mapping(source.get("acceptance"))
    if not rejection or not acceptance:
        raise CandleLedgerValidationError(
            "interaction must include rejection and acceptance evidence"
        )
    return {
        "rejection": {
            "detected": _boolean(
                rejection.get("detected"), field="interaction.rejection.detected"
            ),
            "side": _bounded_token(
                rejection.get("side"), field="interaction.rejection.side", maximum=16
            ),
            "strength": _finite(
                rejection.get("strength"), field="interaction.rejection.strength"
            ),
            "upper_wick_swept_previous_high": _boolean(
                rejection.get("upper_wick_swept_previous_high"),
                field="interaction.rejection.upper_wick_swept_previous_high",
            ),
            "lower_wick_swept_previous_low": _boolean(
                rejection.get("lower_wick_swept_previous_low"),
                field="interaction.rejection.lower_wick_swept_previous_low",
            ),
        },
        "acceptance": {
            "detected": _boolean(
                acceptance.get("detected"), field="interaction.acceptance.detected"
            ),
            "side": _bounded_token(
                acceptance.get("side"), field="interaction.acceptance.side", maximum=16
            ),
            "body_ratio": _finite(
                acceptance.get("body_ratio"),
                field="interaction.acceptance.body_ratio",
            ),
            "closed_beyond_previous_high": _boolean(
                acceptance.get("closed_beyond_previous_high"),
                field="interaction.acceptance.closed_beyond_previous_high",
            ),
            "closed_beyond_previous_low": _boolean(
                acceptance.get("closed_beyond_previous_low"),
                field="interaction.acceptance.closed_beyond_previous_low",
            ),
        },
    }


def _canonical_position(value: object) -> dict[str, Any]:
    source = _mapping(value)
    if not source:
        raise CandleLedgerValidationError("sequence_position must be a mapping")
    return {
        "index": _non_negative_integer(source.get("index"), field="sequence_position.index"),
        "ordinal": _non_negative_integer(
            source.get("ordinal"), field="sequence_position.ordinal"
        ),
        "sequence_length": _non_negative_integer(
            source.get("sequence_length"), field="sequence_position.sequence_length"
        ),
        "from_end": _non_negative_integer(
            source.get("from_end"), field="sequence_position.from_end"
        ),
        "fraction": _finite(
            source.get("fraction"), field="sequence_position.fraction"
        ),
        "is_first": _boolean(
            source.get("is_first"), field="sequence_position.is_first"
        ),
        "is_latest": _boolean(
            source.get("is_latest"), field="sequence_position.is_latest"
        ),
        "direction_run": _non_negative_integer(
            source.get("direction_run"), field="sequence_position.direction_run"
        ),
    }


def _json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CandleLedgerValidationError(
            "candle evidence is not finite JSON data"
        ) from exc


def _canonical_candle(row: Mapping[str, Any], identity: str) -> dict[str, Any]:
    source = _mapping(row)
    if source.get("schema_version") != CANDLE_INTELLIGENCE_SCHEMA_VERSION:
        raise CandleLedgerValidationError(
            f"candle schema must be {CANDLE_INTELLIGENCE_SCHEMA_VERSION}"
        )
    if source.get("status") != "STUDIED":
        raise CandleLedgerValidationError("only studied candle evidence can be recorded")
    if source.get("study_only") is not True or source.get("execution_authority") is not False:
        raise CandleLedgerValidationError("candle evidence must be observation-only")
    if source.get("closed") is not True:
        raise CandleLedgerValidationError("only proven closed candles can be recorded")
    coordinate_space = _bounded_token(
        source.get("coordinate_space"), field="coordinate_space", maximum=40
    )
    if coordinate_space not in _COORDINATE_SPACES:
        raise CandleLedgerValidationError("coordinate_space is outside the V3 taxonomy")
    source_values = _numeric_map(
        source.get("source_values"),
        field="source_values",
        allowed=tuple(sorted(_SOURCE_KEYS)),
    )
    required_source = {
        "PRICE": {"open", "high", "low", "close"},
        "NORMALIZED_PRICE_PROXY": {
            "open_proxy",
            "high_proxy",
            "low_proxy",
            "close_proxy",
        },
        "PIXEL_PRICE_PROXY": {
            "open_y_px",
            "wick_top_px",
            "wick_bottom_px",
            "close_y_px",
        },
    }[coordinate_space]
    if not required_source.issubset(source_values):
        raise CandleLedgerValidationError(
            f"source_values are incomplete for {coordinate_space}"
        )
    ohlc = _numeric_map(
        source.get("ohlc"), field="ohlc", allowed=_OHLC_KEYS, required=_OHLC_KEYS
    )
    geometry = _numeric_map(
        source.get("exact_geometry"),
        field="exact_geometry",
        allowed=_GEOMETRY_KEYS,
        required=_GEOMETRY_KEYS,
    )
    ratios = _numeric_map(
        source.get("ratios"),
        field="ratios",
        allowed=_RATIO_KEYS,
        required=(
            "body_to_range",
            "upper_wick_to_range",
            "lower_wick_to_range",
            "total_wick_to_range",
            "close_location_in_range",
            "range_vs_sequence_median",
        ),
        nullable=(
            "body_to_total_wick",
            "upper_wick_to_body",
            "lower_wick_to_body",
        ),
    )
    interaction = _canonical_interaction(source.get("interaction"))
    sequence_position = _canonical_position(source.get("sequence_position"))
    result = {
        "candle_identity": identity,
        "candle_schema_version": CANDLE_INTELLIGENCE_SCHEMA_VERSION,
        "timestamp": _timestamp(source.get("timestamp")),
        "coordinate_space": coordinate_space,
        "source_values": source_values,
        "ohlc": ohlc,
        "exact_geometry": geometry,
        "ratios": ratios,
        "direction": _bounded_token(
            source.get("direction"), field="direction", maximum=20
        ),
        "type": _bounded_token(source.get("type"), field="type", maximum=64),
        "personality": _bounded_token(
            source.get("personality"), field="personality", maximum=64
        ),
        "regime": _bounded_token(
            source.get("regime"), field="regime", maximum=64
        ),
        "relation_to_previous": _bounded_token(
            source.get("relation_to_previous"),
            field="relation_to_previous",
            maximum=64,
        ),
        "interaction": interaction,
        "sequence_position": sequence_position,
    }
    result["payload_hash"] = hashlib.sha256(_json(result).encode()).hexdigest()
    return result


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _observed_at(value: object) -> str:
    text = str(value or _now_iso()).strip()
    if not text or len(text) > 96 or any(ord(character) < 32 for character in text):
        raise CandleLedgerValidationError("observed_at is not a bounded public value")
    return text


def _deserialize(value: object) -> Any:
    try:
        return json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise CandleLedgerPersistenceError(
            "stored candle evidence contains invalid JSON"
        ) from exc


class CandleLedgerStoreV3:
    """SQLite WAL store for exact, unique, stable V3 candle records."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = DEFAULT_MAX_CANDLE_RECORDS,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = Path(path)
        self.max_records = int(max_records)
        self.busy_timeout_ms = int(busy_timeout_ms)
        if not 1 <= self.max_records <= 10_000_000:
            raise CandleLedgerValidationError("max_records must be in [1, 10000000]")
        if not 100 <= self.busy_timeout_ms <= 60_000:
            raise CandleLedgerValidationError(
                "busy_timeout_ms must be in [100, 60000]"
            )
        if str(self.path).strip() in {"", ":memory:"}:
            raise CandleLedgerValidationError(
                "candle ledger requires a durable filesystem path"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.busy_timeout_ms / 1_000.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            journal_mode = str(
                connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            ).lower()
            if journal_mode != "wal":
                connection.close()
                raise CandleLedgerPersistenceError(
                    f"candle ledger requires WAL mode, received {journal_mode}"
                )
            return connection
        except sqlite3.Error as exc:
            raise CandleLedgerPersistenceError(
                f"cannot open candle ledger: {self.path}"
            ) from exc

    def _initialize(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'candle_records'"
                ).fetchone()
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS ledger_meta "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID"
                )
                existing_version = connection.execute(
                    "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
                ).fetchone()
                if existing_table is not None and existing_version is None:
                    raise CandleLedgerPersistenceError(
                        "existing candle ledger has no V3 schema authority"
                    )
                if (
                    existing_version is not None
                    and str(existing_version[0]) != CANDLE_LEDGER_SCHEMA_VERSION
                ):
                    raise CandleLedgerPersistenceError(
                        "candle ledger schema version does not match PhoenixGuard V3"
                    )
                connection.execute(_CREATE_CANDLES_SQL)
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS candle_records_pair_recent "
                    "ON candle_records(symbol, timeframe, last_seen_ordinal DESC)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO ledger_meta(key, value) VALUES "
                    "('schema_version', ?), ('sql_schema_version', ?), "
                    "('next_ordinal', '1')",
                    (
                        CANDLE_LEDGER_SCHEMA_VERSION,
                        str(CANDLE_LEDGER_SQL_SCHEMA_VERSION),
                    ),
                )
                sql_version = connection.execute(
                    "SELECT value FROM ledger_meta WHERE key = 'sql_schema_version'"
                ).fetchone()
                if (
                    sql_version is None
                    or int(sql_version[0]) != CANDLE_LEDGER_SQL_SCHEMA_VERSION
                ):
                    raise CandleLedgerPersistenceError(
                        "candle ledger SQL schema requires an explicit migration"
                    )
                connection.execute(
                    f"PRAGMA user_version = {CANDLE_LEDGER_SQL_SCHEMA_VERSION}"
                )
                connection.commit()
            except CandleLedgerPersistenceError:
                connection.rollback()
                raise
            except (sqlite3.Error, ValueError) as exc:
                connection.rollback()
                raise CandleLedgerPersistenceError(
                    f"cannot initialize candle ledger: {self.path}"
                ) from exc
            finally:
                connection.close()

    @staticmethod
    def _insert_values(
        *,
        symbol: str,
        timeframe: str,
        pair_id: str,
        candle: Mapping[str, Any],
        observed_at: str,
        ordinal: int,
    ) -> tuple[object, ...]:
        geometry = _mapping(candle.get("exact_geometry"))
        ratios = _mapping(candle.get("ratios"))
        return (
            symbol,
            timeframe,
            pair_id,
            candle["candle_identity"],
            candle["candle_schema_version"],
            _json(candle.get("timestamp")),
            candle["coordinate_space"],
            _json(candle.get("source_values")),
            _json(candle.get("ohlc")),
            _json(candle.get("exact_geometry")),
            _json(candle.get("ratios")),
            candle["direction"],
            candle["type"],
            candle["personality"],
            candle["regime"],
            candle["relation_to_previous"],
            _json(candle.get("interaction")),
            _json(candle.get("sequence_position")),
            float(geometry["range_size"]),
            float(ratios["body_to_range"]),
            float(ratios["upper_wick_to_range"]),
            float(ratios["lower_wick_to_range"]),
            candle["payload_hash"],
            observed_at,
            observed_at,
            1,
            ordinal,
            ordinal,
        )

    @staticmethod
    def _update_values(
        candle: Mapping[str, Any],
        *,
        observed_at: str,
        ordinal: int,
        symbol: str,
        timeframe: str,
    ) -> tuple[object, ...]:
        geometry = _mapping(candle.get("exact_geometry"))
        ratios = _mapping(candle.get("ratios"))
        return (
            candle["candle_schema_version"],
            _json(candle.get("timestamp")),
            candle["coordinate_space"],
            _json(candle.get("source_values")),
            _json(candle.get("ohlc")),
            _json(candle.get("exact_geometry")),
            _json(candle.get("ratios")),
            candle["direction"],
            candle["type"],
            candle["personality"],
            candle["regime"],
            candle["relation_to_previous"],
            _json(candle.get("interaction")),
            _json(candle.get("sequence_position")),
            float(geometry["range_size"]),
            float(ratios["body_to_range"]),
            float(ratios["upper_wick_to_range"]),
            float(ratios["lower_wick_to_range"]),
            candle["payload_hash"],
            observed_at,
            ordinal,
            symbol,
            timeframe,
            candle["candle_identity"],
        )

    def record_candles(
        self,
        candles: Sequence[Mapping[str, Any]],
        *,
        symbol: object,
        timeframe: object,
        observed_at: object = "",
    ) -> dict[str, Any]:
        """Atomically upsert stable, proven-closed candle micro records.

        Rows without an explicit ``*_identity_stable=True`` marker and a
        bounded identity are reported as skipped. A malformed row that claims
        stable identity fails the whole call before SQLite is mutated.
        """

        if isinstance(candles, (str, bytes, bytearray)):
            raise CandleLedgerValidationError(
                "candles must be a sequence of mappings"
            )
        raw_rows = [_mapping(row) for row in candles]
        if any(not row for row in raw_rows):
            raise CandleLedgerValidationError(
                "every candle must be a non-empty mapping"
            )
        if len(raw_rows) > MAX_CANDLE_LEDGER_BATCH:
            raise CandleLedgerValidationError(
                f"one candle-ledger batch cannot exceed {MAX_CANDLE_LEDGER_BATCH} rows"
            )
        canonical_symbol = _canonical_identity(symbol, field="symbol", maximum=64)
        canonical_timeframe = _canonical_identity(
            timeframe, field="timeframe", maximum=32
        )
        pair_id = _pair_id(canonical_symbol, canonical_timeframe)
        resolved_observed_at = _observed_at(observed_at)
        skipped_unstable = 0
        by_identity: dict[str, dict[str, Any]] = {}
        for row in raw_rows:
            stable_identity = _stable_candle_identity(row)
            if stable_identity is None:
                skipped_unstable += 1
                continue
            canonical = _canonical_candle(row, stable_identity)
            previous = by_identity.get(stable_identity)
            if previous is not None and previous["payload_hash"] != canonical["payload_hash"]:
                raise CandleLedgerValidationError(
                    f"batch contains conflicting evidence for candle {stable_identity}"
                )
            by_identity[stable_identity] = canonical

        if not by_identity:
            return {
                "schema_version": CANDLE_LEDGER_SCHEMA_VERSION,
                "status": "SKIPPED_UNSTABLE_IDENTITY",
                "study_only": True,
                "execution_authority": False,
                "pair_id": pair_id,
                "symbol": canonical_symbol,
                "timeframe": canonical_timeframe,
                "input_count": len(raw_rows),
                "stable_count": 0,
                "inserted_count": 0,
                "updated_count": 0,
                "changed_count": 0,
                "skipped_unstable_count": skipped_unstable,
            }

        inserted = 0
        updated = 0
        changed = 0
        unique_count = 0
        observation_count = 0
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM candle_records"
                    ).fetchone()[0]
                )
                existing: dict[str, str] = {}
                for identity in by_identity:
                    row = connection.execute(
                        "SELECT payload_hash FROM candle_records "
                        "WHERE symbol = ? AND timeframe = ? AND candle_identity = ?",
                        (canonical_symbol, canonical_timeframe, identity),
                    ).fetchone()
                    if row is not None:
                        existing[identity] = str(row[0])
                new_count = len(by_identity) - len(existing)
                if current_count + new_count > self.max_records:
                    raise CandleLedgerCapacityError(
                        "candle ledger capacity would be exceeded; no rows were changed"
                    )
                ordinal_row = connection.execute(
                    "SELECT value FROM ledger_meta WHERE key = 'next_ordinal'"
                ).fetchone()
                if ordinal_row is None:
                    raise CandleLedgerPersistenceError(
                        "candle ledger next ordinal is missing"
                    )
                next_ordinal = int(ordinal_row[0])
                for identity, candle in by_identity.items():
                    ordinal = next_ordinal
                    next_ordinal += 1
                    if identity in existing:
                        connection.execute(
                            """
                            UPDATE candle_records SET
                                candle_schema_version = ?, timestamp_json = ?,
                                coordinate_space = ?, source_values_json = ?,
                                ohlc_json = ?, exact_geometry_json = ?, ratios_json = ?,
                                direction = ?, candle_type = ?, personality = ?, regime = ?,
                                relation_to_previous = ?, interaction_json = ?,
                                sequence_position_json = ?, range_size = ?, body_ratio = ?,
                                upper_wick_ratio = ?, lower_wick_ratio = ?, payload_hash = ?,
                                last_observed_at = ?, last_seen_ordinal = ?,
                                observation_count = observation_count + 1
                            WHERE symbol = ? AND timeframe = ? AND candle_identity = ?
                            """,
                            self._update_values(
                                candle,
                                observed_at=resolved_observed_at,
                                ordinal=ordinal,
                                symbol=canonical_symbol,
                                timeframe=canonical_timeframe,
                            ),
                        )
                        updated += 1
                        changed += int(existing[identity] != candle["payload_hash"])
                    else:
                        connection.execute(
                            """
                            INSERT INTO candle_records (
                                symbol, timeframe, pair_id, candle_identity,
                                candle_schema_version, timestamp_json, coordinate_space,
                                source_values_json, ohlc_json, exact_geometry_json,
                                ratios_json, direction, candle_type, personality, regime,
                                relation_to_previous, interaction_json,
                                sequence_position_json, range_size, body_ratio,
                                upper_wick_ratio, lower_wick_ratio, payload_hash,
                                first_observed_at, last_observed_at, observation_count,
                                first_seen_ordinal, last_seen_ordinal
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                            )
                            """,
                            self._insert_values(
                                symbol=canonical_symbol,
                                timeframe=canonical_timeframe,
                                pair_id=pair_id,
                                candle=candle,
                                observed_at=resolved_observed_at,
                                ordinal=ordinal,
                            ),
                        )
                        inserted += 1
                connection.execute(
                    "UPDATE ledger_meta SET value = ? WHERE key = 'next_ordinal'",
                    (str(next_ordinal),),
                )
                pair_totals = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(observation_count), 0) "
                    "FROM candle_records WHERE symbol = ? AND timeframe = ?",
                    (canonical_symbol, canonical_timeframe),
                ).fetchone()
                unique_count = int(pair_totals[0])
                observation_count = int(pair_totals[1])
                connection.commit()
            except CandleLedgerCapacityError:
                connection.rollback()
                raise
            except CandleLedgerPersistenceError:
                connection.rollback()
                raise
            except (sqlite3.Error, ValueError) as exc:
                connection.rollback()
                raise CandleLedgerPersistenceError(
                    f"failed to transact candle ledger: {self.path}"
                ) from exc
            finally:
                connection.close()

        status = (
            "RECORDED_AND_UPDATED"
            if inserted and updated
            else "RECORDED"
            if inserted
            else "UPDATED"
        )
        return {
            "schema_version": CANDLE_LEDGER_SCHEMA_VERSION,
            "status": status,
            "study_only": True,
            "execution_authority": False,
            "pair_id": pair_id,
            "symbol": canonical_symbol,
            "timeframe": canonical_timeframe,
            "input_count": len(raw_rows),
            "stable_count": len(by_identity),
            "inserted_count": inserted,
            "updated_count": updated,
            "changed_count": changed,
            "skipped_unstable_count": skipped_unstable,
            "unique_candle_count": unique_count,
            "total_observation_count": observation_count,
        }

    @staticmethod
    def _count_map(
        connection: sqlite3.Connection,
        *,
        column: str,
        symbol: str,
        timeframe: str,
    ) -> dict[str, int]:
        allowed = {
            "coordinate_space",
            "direction",
            "candle_type",
            "personality",
            "regime",
        }
        if column not in allowed:
            raise CandleLedgerPersistenceError("unsupported candle summary column")
        rows = connection.execute(
            f"SELECT {column}, COUNT(*) FROM candle_records "  # noqa: S608 -- allowlisted column
            "WHERE symbol = ? AND timeframe = ? GROUP BY "
            f"{column} ORDER BY COUNT(*) DESC, {column} ASC",  # noqa: S608
            (symbol, timeframe),
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def pair_summary(self, symbol: object, timeframe: object) -> dict[str, Any]:
        """Return exact unique and re-observation counts for one pair scope."""

        canonical_symbol = _canonical_identity(symbol, field="symbol", maximum=64)
        canonical_timeframe = _canonical_identity(
            timeframe, field="timeframe", maximum=32
        )
        pair_id = _pair_id(canonical_symbol, canonical_timeframe)
        with self._lock:
            connection = self._connect()
            try:
                totals = connection.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(observation_count), 0),
                           AVG(range_size), AVG(body_ratio),
                           AVG(upper_wick_ratio), AVG(lower_wick_ratio)
                    FROM candle_records WHERE symbol = ? AND timeframe = ?
                    """,
                    (canonical_symbol, canonical_timeframe),
                ).fetchone()
                unique_count = int(totals[0])
                first = connection.execute(
                    "SELECT candle_identity, first_observed_at FROM candle_records "
                    "WHERE symbol = ? AND timeframe = ? "
                    "ORDER BY first_seen_ordinal ASC LIMIT 1",
                    (canonical_symbol, canonical_timeframe),
                ).fetchone()
                latest = connection.execute(
                    "SELECT candle_identity, last_observed_at FROM candle_records "
                    "WHERE symbol = ? AND timeframe = ? "
                    "ORDER BY last_seen_ordinal DESC LIMIT 1",
                    (canonical_symbol, canonical_timeframe),
                ).fetchone()
                count_maps = {
                    f"{column}_counts": self._count_map(
                        connection,
                        column=column,
                        symbol=canonical_symbol,
                        timeframe=canonical_timeframe,
                    )
                    for column in (
                        "coordinate_space",
                        "direction",
                        "candle_type",
                        "personality",
                        "regime",
                    )
                }
            except sqlite3.Error as exc:
                raise CandleLedgerPersistenceError(
                    f"failed to read candle ledger summary: {self.path}"
                ) from exc
            finally:
                connection.close()
        return {
            "schema_version": CANDLE_LEDGER_SCHEMA_VERSION,
            "status": "READY" if unique_count else "NOT_FOUND",
            "study_only": True,
            "execution_authority": False,
            "pair_id": pair_id,
            "symbol": canonical_symbol,
            "timeframe": canonical_timeframe,
            "unique_candle_count": unique_count,
            "total_observation_count": int(totals[1]),
            "first_candle_identity": str(first[0]) if first is not None else "",
            "first_observed_at": str(first[1]) if first is not None else "",
            "latest_candle_identity": str(latest[0]) if latest is not None else "",
            "last_observed_at": str(latest[1]) if latest is not None else "",
            "averages": {
                "range_size": round(float(totals[2] or 0.0), 8),
                "body_to_range": round(float(totals[3] or 0.0), 6),
                "upper_wick_to_range": round(float(totals[4] or 0.0), 6),
                "lower_wick_to_range": round(float(totals[5] or 0.0), 6),
            },
            **count_maps,
        }

    def recent_candles(
        self,
        symbol: object,
        timeframe: object,
        *,
        limit: int = 64,
    ) -> dict[str, Any]:
        """Return bounded newest-first canonical records for one pair scope."""

        count = int(limit)
        if not 1 <= count <= MAX_RECENT_CANDLE_READ:
            raise CandleLedgerValidationError(
                f"limit must be in [1, {MAX_RECENT_CANDLE_READ}]"
            )
        canonical_symbol = _canonical_identity(symbol, field="symbol", maximum=64)
        canonical_timeframe = _canonical_identity(
            timeframe, field="timeframe", maximum=32
        )
        pair_id = _pair_id(canonical_symbol, canonical_timeframe)
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT * FROM candle_records WHERE symbol = ? AND timeframe = ? "
                    "ORDER BY last_seen_ordinal DESC LIMIT ?",
                    (canonical_symbol, canonical_timeframe, count),
                ).fetchall()
            except sqlite3.Error as exc:
                raise CandleLedgerPersistenceError(
                    f"failed to read recent candle ledger: {self.path}"
                ) from exc
            finally:
                connection.close()
        records = [
            {
                "schema_version": CANDLE_LEDGER_SCHEMA_VERSION,
                "candle_schema_version": str(row["candle_schema_version"]),
                "study_only": True,
                "execution_authority": False,
                "pair_id": str(row["pair_id"]),
                "symbol": str(row["symbol"]),
                "timeframe": str(row["timeframe"]),
                "candle_identity": str(row["candle_identity"]),
                "timestamp": _deserialize(row["timestamp_json"]),
                "coordinate_space": str(row["coordinate_space"]),
                "source_values": _deserialize(row["source_values_json"]),
                "ohlc": _deserialize(row["ohlc_json"]),
                "exact_geometry": _deserialize(row["exact_geometry_json"]),
                "ratios": _deserialize(row["ratios_json"]),
                "direction": str(row["direction"]),
                "type": str(row["candle_type"]),
                "personality": str(row["personality"]),
                "regime": str(row["regime"]),
                "relation_to_previous": str(row["relation_to_previous"]),
                "interaction": _deserialize(row["interaction_json"]),
                "sequence_position": _deserialize(row["sequence_position_json"]),
                "first_observed_at": str(row["first_observed_at"]),
                "last_observed_at": str(row["last_observed_at"]),
                "observation_count": int(row["observation_count"]),
            }
            for row in rows
        ]
        return {
            "schema_version": CANDLE_LEDGER_SCHEMA_VERSION,
            "status": "READY" if records else "NOT_FOUND",
            "study_only": True,
            "execution_authority": False,
            "pair_id": pair_id,
            "symbol": canonical_symbol,
            "timeframe": canonical_timeframe,
            "newest_first": True,
            "record_count": len(records),
            "records": records,
        }


__all__ = [
    "CANDLE_LEDGER_SCHEMA_VERSION",
    "CANDLE_LEDGER_SQL_SCHEMA_VERSION",
    "DEFAULT_MAX_CANDLE_RECORDS",
    "MAX_CANDLE_LEDGER_BATCH",
    "MAX_RECENT_CANDLE_READ",
    "CandleLedgerCapacityError",
    "CandleLedgerPersistenceError",
    "CandleLedgerStoreV3",
    "CandleLedgerValidationError",
]
