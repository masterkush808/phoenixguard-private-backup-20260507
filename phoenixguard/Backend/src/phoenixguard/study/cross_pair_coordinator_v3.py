"""Restart-safe synchronization coordinator for V3 cross-pair studies.

The pure cross-pair association engine requires two complete synchronized
series at once, while live tracker updates arrive one pair at a time.  This
bounded coordinator stores dimensionless closed-candle returns per pair scope,
aligns only exact shared timestamps, and invokes the research engine only when
support is sufficient.  It never manufactures a peer series or an edge.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from typing import Any, cast

from phoenixguard.study._persistence_v3 import (
    exclusive_store_lock,
    read_json_document,
    write_json_atomic,
)
from phoenixguard.study.cross_pair_association_v3 import (
    CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
    CrossPairAssociationValidationError,
    analyze_cross_pair_lead_lag_v3,
)


CROSS_PAIR_COORDINATOR_SCHEMA_VERSION = "PG_CROSS_PAIR_COORDINATOR_STATE_V3"
DEFAULT_COORDINATOR_MAX_PAIRS = 64
DEFAULT_COORDINATOR_MAX_SAMPLES = 1_024
DEFAULT_COORDINATOR_MAX_EDGES = 128
DEFAULT_COORDINATOR_MAX_NULL_SHIFTS = 63
_NORMALIZED_SPACE = "NORMALIZED_RETURN"
_SYNCHRONIZED_ORDER = "SYNCHRONIZED_CLOSED_TIMESTAMP_V1"


class CrossPairCoordinatorValidationError(ValueError):
    """Raised when synchronized pair state is malformed or exceeds bounds."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text or len(text) > maximum:
        raise CrossPairCoordinatorValidationError(
            f"{field} must contain 1 to {maximum} characters"
        )
    return text


def _integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        raise CrossPairCoordinatorValidationError(f"{field} must be an integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CrossPairCoordinatorValidationError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise CrossPairCoordinatorValidationError(
            f"{field} must be in [{minimum}, {maximum}]"
        )
    return parsed


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise CrossPairCoordinatorValidationError(f"{field} must be finite")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CrossPairCoordinatorValidationError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise CrossPairCoordinatorValidationError(f"{field} must be finite")
    return parsed


def _safety() -> dict[str, Any]:
    return {
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


def _canonical_rows(
    value: Sequence[Mapping[str, Any]],
    *,
    pair_scope_id: str,
    maximum: int,
) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)):
        raise CrossPairCoordinatorValidationError("series must be a sequence")
    raw_rows = list(value)
    if not raw_rows or len(raw_rows) > maximum:
        raise CrossPairCoordinatorValidationError(
            f"series must contain between 1 and {maximum} rows"
        )
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows):
        row = _mapping(raw)
        if row.get("is_closed") is not True:
            raise CrossPairCoordinatorValidationError(
                f"series[{index}] is not a proven closed candle"
            )
        pair_id = _identity(row.get("pair_id"), field="pair_id", maximum=96)
        if pair_id != pair_scope_id:
            raise CrossPairCoordinatorValidationError("series pair_id does not match scope")
        coordinate = _identity(
            row.get("coordinate_space"),
            field="coordinate_space",
            maximum=64,
        )
        order_domain = _identity(
            row.get("order_domain"),
            field="order_domain",
            maximum=64,
        )
        if coordinate != _NORMALIZED_SPACE or order_domain != _SYNCHRONIZED_ORDER:
            raise CrossPairCoordinatorValidationError(
                "cross-pair coordinator requires normalized synchronized evidence"
            )
        rows.append(
            {
                "pair_id": pair_id,
                "candle_id": _identity(
                    row.get("candle_id"),
                    field="candle_id",
                    maximum=256,
                ),
                "closed_timestamp": _finite(
                    row.get("closed_timestamp"),
                    field="closed_timestamp",
                ),
                "is_closed": True,
                "coordinate_space": _NORMALIZED_SPACE,
                "order_domain": _SYNCHRONIZED_ORDER,
                "value": _finite(row.get("value"), field="value"),
            }
        )
    timestamps = [float(row["closed_timestamp"]) for row in rows]
    if len(set(timestamps)) != len(timestamps):
        raise CrossPairCoordinatorValidationError("series contains duplicate timestamps")
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:], strict=False)):
        raise CrossPairCoordinatorValidationError("series timestamps must increase strictly")
    return rows


def _contiguous_tail(
    rows: Sequence[Mapping[str, Any]],
    *,
    timeframe_seconds: int,
    maximum: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    previous: float | None = None
    tolerance = max(1e-9, timeframe_seconds * 1e-9)
    for raw in sorted(rows, key=lambda row: float(row["closed_timestamp"])):
        timestamp = float(raw["closed_timestamp"])
        if previous is not None and abs((timestamp - previous) - timeframe_seconds) > tolerance:
            selected = []
        selected.append(dict(raw))
        previous = timestamp
    return selected[-maximum:]


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": CROSS_PAIR_COORDINATOR_SCHEMA_VERSION,
        "study_only": True,
        "execution_authority": False,
        "next_ordinal": 1,
        "pairs": {},
    }


class CrossPairStudyCoordinatorV3:
    """Atomically retain and compare bounded normalized pair histories."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_pairs: int = DEFAULT_COORDINATOR_MAX_PAIRS,
        max_samples: int = DEFAULT_COORDINATOR_MAX_SAMPLES,
        max_edges: int = DEFAULT_COORDINATOR_MAX_EDGES,
        max_lag: int = 6,
        minimum_support: int = 32,
        max_null_shifts: int = DEFAULT_COORDINATOR_MAX_NULL_SHIFTS,
    ) -> None:
        self.path = Path(path)
        self.max_pairs = _integer(
            max_pairs,
            field="max_pairs",
            minimum=1,
            maximum=1_000_000,
        )
        self.max_samples = _integer(
            max_samples,
            field="max_samples",
            minimum=1,
            maximum=1_000_000,
        )
        self.max_edges = _integer(
            max_edges,
            field="max_edges",
            minimum=1,
            maximum=1_000_000,
        )
        self.max_lag = _integer(
            max_lag,
            field="max_lag",
            minimum=1,
            maximum=1_000_000,
        )
        self.minimum_support = _integer(
            minimum_support,
            field="minimum_support",
            minimum=1,
            maximum=self.max_samples,
        )
        self.max_null_shifts = _integer(
            max_null_shifts,
            field="max_null_shifts",
            minimum=1,
            maximum=1_000_000,
        )

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{symbol}|{timeframe}"

    def _load(self) -> dict[str, Any]:
        raw = read_json_document(self.path)
        if raw is None:
            return _empty_state()
        if (
            raw.get("schema_version") != CROSS_PAIR_COORDINATOR_SCHEMA_VERSION
            or raw.get("study_only") is not True
            or raw.get("execution_authority") is not False
        ):
            raise CrossPairCoordinatorValidationError(
                "cross-pair coordinator state is not a V3 study document"
            )
        pairs = _mapping(raw.get("pairs"))
        if len(pairs) > self.max_pairs:
            raise CrossPairCoordinatorValidationError("stored pair capacity is exceeded")
        canonical_pairs: dict[str, Any] = {}
        for key, value in pairs.items():
            entry = _mapping(value)
            symbol = _identity(entry.get("symbol"), field="symbol", maximum=64)
            timeframe = _identity(entry.get("timeframe"), field="timeframe", maximum=32)
            timeframe_seconds = _integer(
                entry.get("timeframe_seconds"),
                field="timeframe_seconds",
                minimum=1,
                maximum=2_592_000,
            )
            pair_scope_id = self._key(symbol, timeframe)
            rows = _canonical_rows(
                cast(Sequence[Mapping[str, Any]], entry.get("rows", [])),
                pair_scope_id=pair_scope_id,
                maximum=self.max_samples,
            )
            if str(key) != pair_scope_id:
                raise CrossPairCoordinatorValidationError("stored pair key is inconsistent")
            canonical_pairs[pair_scope_id] = {
                "symbol": symbol,
                "timeframe": timeframe,
                "timeframe_seconds": timeframe_seconds,
                "updated_ordinal": int(entry.get("updated_ordinal", 0) or 0),
                "rows": rows,
            }
        return {
            "schema_version": CROSS_PAIR_COORDINATOR_SCHEMA_VERSION,
            "study_only": True,
            "execution_authority": False,
            "next_ordinal": max(1, int(raw.get("next_ordinal", 1) or 1)),
            "pairs": canonical_pairs,
        }

    def _pending(
        self,
        *,
        status: str,
        symbol: str,
        timeframe: str,
        stored_pair_count: int,
        compatible_pair_count: int = 0,
        tested_pair_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "schema_version": CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
            "coordinator_schema_version": CROSS_PAIR_COORDINATOR_SCHEMA_VERSION,
            "status": status,
            "current_pair_id": self._key(symbol, timeframe),
            "stored_pair_count": stored_pair_count,
            "compatible_pair_count": compatible_pair_count,
            "tested_pair_count": tested_pair_count,
            "nodes": [],
            "edges": [],
            "published_edge_count": 0,
            "contract": {
                "requires_distinct_pair": True,
                "requires_exact_shared_closed_timestamps": True,
                "requires_normalized_coordinate_space": True,
                "fabricates_missing_pair_evidence": False,
                "maximum_pairs": self.max_pairs,
                "maximum_samples_per_pair": self.max_samples,
                **_safety(),
            },
            **_safety(),
        }

    def update_pair(
        self,
        *,
        symbol: object,
        timeframe: object,
        timeframe_seconds: int,
        series: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        canonical_symbol = _identity(symbol, field="symbol", maximum=64)
        canonical_timeframe = _identity(timeframe, field="timeframe", maximum=32)
        duration = _integer(
            timeframe_seconds,
            field="timeframe_seconds",
            minimum=1,
            maximum=2_592_000,
        )
        pair_scope_id = self._key(canonical_symbol, canonical_timeframe)
        incoming = _canonical_rows(
            series,
            pair_scope_id=pair_scope_id,
            maximum=self.max_samples,
        )
        with exclusive_store_lock(self.path, timeout_seconds=5.0):
            state = self._load()
            pairs = _mapping(state.get("pairs"))
            if pair_scope_id not in pairs and len(pairs) >= self.max_pairs:
                return self._pending(
                    status="PAIR_CAPACITY_REACHED",
                    symbol=canonical_symbol,
                    timeframe=canonical_timeframe,
                    stored_pair_count=len(pairs),
                )
            existing = _mapping(pairs.get(pair_scope_id))
            by_timestamp = {
                float(row["closed_timestamp"]): dict(row)
                for row in cast(list[dict[str, Any]], existing.get("rows", []))
            }
            for row in incoming:
                by_timestamp[float(row["closed_timestamp"])] = row
            merged = _contiguous_tail(
                list(by_timestamp.values()),
                timeframe_seconds=duration,
                maximum=self.max_samples,
            )
            ordinal = int(state.get("next_ordinal", 1) or 1)
            pairs[pair_scope_id] = {
                "symbol": canonical_symbol,
                "timeframe": canonical_timeframe,
                "timeframe_seconds": duration,
                "updated_ordinal": ordinal,
                "rows": merged,
            }
            state["pairs"] = pairs
            state["next_ordinal"] = ordinal + 1
            write_json_atomic(self.path, state)
            snapshot = deepcopy(pairs)

        if len(snapshot) < 2:
            return self._pending(
                status="INSUFFICIENT_SYNCHRONIZED_PAIR",
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                stored_pair_count=len(snapshot),
            )
        current_rows = cast(list[dict[str, Any]], _mapping(snapshot[pair_scope_id]).get("rows", []))
        compatible = 0
        tested = 0
        studies: list[dict[str, Any]] = []
        proof_by_digest: dict[str, list[dict[str, Any]]] = {}
        required_rows = self.minimum_support + self.max_lag
        for other_key in sorted(snapshot):
            if other_key == pair_scope_id:
                continue
            other = _mapping(snapshot[other_key])
            if other.get("timeframe") != canonical_timeframe or int(
                other.get("timeframe_seconds", 0) or 0
            ) != duration:
                continue
            other_rows = cast(list[dict[str, Any]], other.get("rows", []))
            left_by_time = {float(row["closed_timestamp"]): row for row in current_rows}
            right_by_time = {float(row["closed_timestamp"]): row for row in other_rows}
            shared = sorted(set(left_by_time) & set(right_by_time))
            left_aligned = _contiguous_tail(
                [left_by_time[timestamp] for timestamp in shared],
                timeframe_seconds=duration,
                maximum=self.max_samples,
            )
            aligned_timestamps = [float(row["closed_timestamp"]) for row in left_aligned]
            right_aligned = [right_by_time[timestamp] for timestamp in aligned_timestamps]
            if len(left_aligned) < 8:
                continue
            compatible += 1
            if len(left_aligned) < required_rows:
                continue
            tested += 1
            try:
                study = analyze_cross_pair_lead_lag_v3(
                    left_aligned,
                    right_aligned,
                    max_lag=self.max_lag,
                    minimum_support=self.minimum_support,
                    max_samples=self.max_samples,
                    max_null_shifts=self.max_null_shifts,
                )
            except CrossPairAssociationValidationError as exc:
                raise CrossPairCoordinatorValidationError(str(exc)) from exc
            studies.append(study)
            digest = str(study.get("evidence_digest") or "")
            peer_scope_binding_digest = hashlib.sha256(
                (
                    "|".join(sorted((pair_scope_id, str(other_key))))
                    + "|"
                    + digest
                ).encode("utf-8")
            ).hexdigest()
            study["peer_scope_binding_digest"] = peer_scope_binding_digest
            proof_by_digest[peer_scope_binding_digest] = [
                {
                    "candle_id": hashlib.sha256(
                        (
                            str(left_row["candle_id"])
                            + "|"
                            + str(right_row["candle_id"])
                        ).encode("utf-8")
                    ).hexdigest()[:32],
                    "order_index": int(
                        round(float(left_row["closed_timestamp"]) * 1_000_000)
                    ),
                    "closed_timestamp": float(left_row["closed_timestamp"]),
                    "coordinate_space": _NORMALIZED_SPACE,
                    "order_domain": _SYNCHRONIZED_ORDER,
                    "is_closed": True,
                }
                for left_row, right_row in zip(left_aligned, right_aligned, strict=True)
            ]

        if compatible == 0:
            return self._pending(
                status="INSUFFICIENT_SYNCHRONIZED_PAIR",
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                stored_pair_count=len(snapshot),
            )
        if tested == 0:
            return self._pending(
                status="INSUFFICIENT_SYNCHRONIZED_SUPPORT",
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                stored_pair_count=len(snapshot),
                compatible_pair_count=compatible,
            )
        edges = [
            deepcopy(edge)
            for study in studies
            for edge in cast(list[dict[str, Any]], study.get("significant_associations", []))
        ]
        edges.sort(
            key=lambda row: (
                float(row.get("bonferroni_adjusted_p_value", 1.0)),
                -float(row.get("association_score", 0.0)),
                str(row.get("source_pair_id") or ""),
            )
        )
        published = edges[: self.max_edges]
        node_ids = sorted(
            {
                str(edge.get(field) or "")
                for edge in published
                for field in ("source_pair_id", "target_pair_id")
            }
        )
        graph_digest = hashlib.sha256(
            "|".join(
                sorted(
                    str(study.get("peer_scope_binding_digest") or "")
                    for study in studies
                )
            ).encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
            "coordinator_schema_version": CROSS_PAIR_COORDINATOR_SCHEMA_VERSION,
            "status": "SUPPORTED" if published else "NO_SIGNIFICANT_ASSOCIATION",
            "current_pair_id": pair_scope_id,
            "stored_pair_count": len(snapshot),
            "compatible_pair_count": compatible,
            "tested_pair_count": tested,
            "nodes": [
                {"pair_id": pair_id, "study_only": True, "execution_authority": False}
                for pair_id in node_ids
            ],
            "edges": published,
            "published_edge_count": len(published),
            "significant_edge_count_before_bound": len(edges),
            "edges_truncated_by_bound": len(edges) > self.max_edges,
            "graph_digest": graph_digest,
            "pair_studies": [
                {
                    "status": study.get("status"),
                    "aligned_closed_candle_count": study.get(
                        "aligned_closed_candle_count"
                    ),
                    "evidence_digest": study.get("evidence_digest"),
                    "peer_scope_binding_digest": study.get(
                        "peer_scope_binding_digest"
                    ),
                    "published_association_count": len(
                        cast(list[object], study.get("significant_associations", []))
                    ),
                }
                for study in studies
            ],
            "contract": {
                "requires_distinct_pair": True,
                "requires_exact_shared_closed_timestamps": True,
                "requires_normalized_coordinate_space": True,
                "publishes_only_significant_associations": True,
                "fabricates_missing_pair_evidence": False,
                "maximum_pairs": self.max_pairs,
                "maximum_samples_per_pair": self.max_samples,
                "maximum_edges": self.max_edges,
                **_safety(),
            },
            "_proof_evidence_by_digest": proof_by_digest,
            **_safety(),
        }


__all__ = [
    "CROSS_PAIR_COORDINATOR_SCHEMA_VERSION",
    "DEFAULT_COORDINATOR_MAX_EDGES",
    "DEFAULT_COORDINATOR_MAX_NULL_SHIFTS",
    "DEFAULT_COORDINATOR_MAX_PAIRS",
    "DEFAULT_COORDINATOR_MAX_SAMPLES",
    "CrossPairCoordinatorValidationError",
    "CrossPairStudyCoordinatorV3",
]
