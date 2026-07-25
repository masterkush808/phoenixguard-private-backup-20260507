"""Live orchestration for PhoenixGuard V3's observation-only market study.

The service deliberately sits beside the execution stack.  It turns proven
closed-candle geometry into explainable candle, behaviour, Pair DNA, and
historical-similarity evidence, but it never grants trade permission.
"""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from statistics import median
import threading
from typing import Any, Mapping, Sequence, cast

from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.candle_ledger_v3 import CandleLedgerStoreV3
from phoenixguard.study.historical_similarity_v3 import (
    HistoricalSequenceStoreV3,
    build_sequence_fingerprint_v3,
)
from phoenixguard.study.pair_dna_v3 import PairDNAStoreV3
from phoenixguard.study.object_relationship_graph_v3 import (
    build_object_relationship_graph_v3,
)
from phoenixguard.study._persistence_v3 import (
    exclusive_store_lock,
    read_json_document,
    write_json_atomic,
)


MARKET_STUDY_SCHEMA_VERSION = "PG_MARKET_STUDY_V3"
_MAX_LIVE_CANDLES = 128
_PENDING_OUTCOME_SCHEMA_VERSION = "PG_PENDING_MARKET_OUTCOMES_V3"
_MAX_PENDING_PAIRS = 64
_MAX_PENDING_CANDLES = 16
_MAX_PENDING_OBJECTS = 16


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[object], value) if _mapping(item)]


def _clip01(value: object) -> float:
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed if math.isfinite(parsed) else 0.0))


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(float(cast(Any, value)))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _side(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "UPTREND", "UP_SWING"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "DOWNTREND", "DOWN_SWING"}:
        return "SELL"
    return "HOLD"


def _identity_token(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".15g") if math.isfinite(value) else ""
    return str(value).strip()


def _current_axis_prior_candle(
    previous: Mapping[str, Any],
    studied_candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Find the prior close re-observed on the current frame's coordinate axis.

    Pixel and normalized proxy values cannot be compared across separately
    scaled chart frames.  A previous candle is therefore usable only when its
    stable timestamp or explicitly proven immutable identity is present among
    the current frame's earlier candles.  The newest candle is deliberately
    excluded.  Positional tracker ids and inferred sequence ids never qualify.
    """

    if len(studied_candles) < 2:
        return {}
    prior_timestamp = _identity_token(previous.get("latest_candle_timestamp"))
    prior_stable_identity = ""
    if previous.get("latest_candle_identity_stable") is True:
        prior_stable_identity = _identity_token(
            previous.get("latest_stable_candle_identity")
        )
    if not prior_timestamp and not prior_stable_identity:
        return {}
    for row in reversed(studied_candles[:-1]):
        timestamp_matches = bool(
            prior_timestamp
            and _identity_token(row.get("timestamp")) == prior_timestamp
        )
        identity_matches = bool(
            prior_stable_identity
            and row.get("identity_stable") is True
            and _identity_token(row.get("stable_candle_identity"))
            == prior_stable_identity
        )
        if timestamp_matches or identity_matches:
            return dict(row)
    return {}


def _prior_only_baseline_range(
    studied_candles: Sequence[Mapping[str, Any]],
) -> float:
    """Measure the current-frame scale without using the outcome candle."""

    ranges: list[float] = []
    for row in studied_candles[:-1]:
        ohlc = _mapping(row.get("ohlc"))
        try:
            high = float(cast(Any, ohlc.get("high")))
            low = float(cast(Any, ohlc.get("low")))
        except (TypeError, ValueError):
            continue
        range_size = high - low
        if math.isfinite(range_size) and range_size > 0.0:
            ranges.append(range_size)
    return float(median(ranges)) if ranges else 0.0


def _base_contract(*, symbol: str = "", timeframe: str = "", status: str) -> dict[str, Any]:
    return {
        "schema_version": MARKET_STUDY_SCHEMA_VERSION,
        "status": status,
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
        "symbol": symbol,
        "timeframe": timeframe,
    }


def pending_market_study_v3(
    reason: object,
    *,
    symbol: object = "",
    timeframe: object = "",
    status: str = "PENDING",
) -> dict[str, Any]:
    result = _base_contract(
        symbol=str(symbol or "").strip().upper(),
        timeframe=str(timeframe or "").strip().upper(),
        status=str(status or "PENDING").strip().upper(),
    )
    result.update(
        {
            "reason": str(reason or "Market study evidence is not ready.")[:320],
            "directional_read": {
                "side": "HOLD",
                "confidence": 0.0,
                "status": "INSUFFICIENT_EVIDENCE",
                "reasons": [],
                "study_only": True,
                "execution_authority": False,
            },
        }
    )
    return result


def _compact_candle(candle: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(candle.get(key))
        for key in (
            "candle_id",
            "timestamp",
            "identity_stable",
            "stable_candle_identity",
            "identity_source",
            "identity_proof_source",
            "closed_candle_sequence",
            "closed",
            "coordinate_space",
            "source_values",
            "ohlc",
            "exact_geometry",
            "ratios",
            "direction",
            "type",
            "personality",
            "regime",
            "relation_to_previous",
            "interaction",
            "sequence_position",
            "fingerprint_token",
        )
        if candle.get(key) not in (None, "", [], {})
    }


def _compact_candle_study(study: Mapping[str, Any]) -> dict[str, Any]:
    candles = _rows(study.get("candles"))
    return {
        "schema_version": study.get("schema_version"),
        "status": study.get("status"),
        "study_only": True,
        "execution_authority": False,
        "studied_count": int(study.get("studied_count", 0) or 0),
        "truncated_count": int(study.get("truncated_count", 0) or 0),
        "sequence_signature": str(study.get("sequence_signature") or ""),
        "baseline_range": study.get("baseline_range"),
        "summary": deepcopy(_mapping(study.get("summary"))),
        "latest": _compact_candle(candles[-1]) if candles else {},
        "recent_candles": [_compact_candle(row) for row in candles[-12:]],
    }


def _compact_behavior(study: Mapping[str, Any]) -> dict[str, Any]:
    segments = _rows(study.get("segments"))
    return {
        "schema_version": study.get("schema_version"),
        "status": study.get("status"),
        "study_only": True,
        "execution_authority": False,
        "candle_count": int(study.get("candle_count", 0) or 0),
        "timeframe_seconds": int(study.get("timeframe_seconds", 0) or 0),
        "major_trend": deepcopy(_mapping(study.get("major_trend"))),
        "inner_trend": deepcopy(_mapping(study.get("inner_trend"))),
        "current_state": deepcopy(_mapping(study.get("current_state"))),
        "current_segment": deepcopy(segments[-1]) if segments else {},
        "swing_summary": deepcopy(_mapping(study.get("swing_summary"))),
        "rest_summary": deepcopy(_mapping(study.get("rest_summary"))),
        "state_counts": deepcopy(_mapping(study.get("state_counts"))),
        "transition_summary": deepcopy(_mapping(study.get("transition_summary"))),
        "market_story": str(study.get("market_story") or ""),
    }


def _pending_candle_study(study: Mapping[str, Any]) -> dict[str, Any]:
    candles = _rows(study.get("candles"))[-_MAX_PENDING_CANDLES:]
    return {
        "schema_version": study.get("schema_version"),
        "status": study.get("status"),
        "study_only": True,
        "execution_authority": False,
        "sequence_signature": str(study.get("sequence_signature") or ""),
        "candles": [
            {
                key: deepcopy(row.get(key))
                for key in (
                    "candle_id",
                    "timestamp",
                    "identity_stable",
                    "stable_candle_identity",
                    "identity_source",
                    "identity_proof_source",
                    "closed_candle_sequence",
                    "closed",
                    "coordinate_space",
                    "ratios",
                    "direction",
                    "type",
                    "personality",
                    "regime",
                    "interaction",
                )
                if row.get(key) not in (None, "", [], {})
            }
            for row in candles
        ],
    }


def _pending_behavior_study(study: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": study.get("schema_version"),
        "status": study.get("status"),
        "study_only": True,
        "execution_authority": False,
        "states": deepcopy(_rows(study.get("states"))[-_MAX_PENDING_CANDLES:]),
        "segments": deepcopy(_rows(study.get("segments"))[-_MAX_PENDING_CANDLES:]),
        "major_trend": deepcopy(_mapping(study.get("major_trend"))),
        "inner_trend": deepcopy(_mapping(study.get("inner_trend"))),
        "current_state": deepcopy(_mapping(study.get("current_state"))),
    }


def _pending_retracement_study(study: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the completed graph evidence needed for delayed Pair DNA."""

    if not study:
        return {}
    observations = _rows(study.get("observations"))[:128]
    return {
        "schema_version": study.get("schema_version"),
        "status": study.get("status"),
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "observations": deepcopy(observations),
    }


def _top_counts(value: object, *, limit: int = 8) -> dict[str, int]:
    source = _mapping(value)
    ranked = sorted(
        ((str(key), int(count or 0)) for key, count in source.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return dict(ranked[:limit])


def _compact_pair_profile(value: object) -> dict[str, Any]:
    profile = _mapping(value)
    if not profile:
        return {}
    candle = _mapping(profile.get("candle"))
    behavior = _mapping(profile.get("behavior"))
    correlations = _rows(
        profile.get("marginal_and_pairwise_outcome_associations")
        or profile.get("correlation_summary")
    )
    correlations.sort(key=lambda row: (-int(row.get("support", 0) or 0), str(row.get("feature") or "")))
    retracement = _mapping(profile.get("retracement_confluence"))
    retracement_partitions = _rows(retracement.get("empirical_partitions"))
    retracement_level_support = {"OTE_70_5": 0, "CUSTOM_71_8": 0}
    for row in retracement_partitions:
        level_id = str(_mapping(row.get("partition")).get("level_id") or "")
        if level_id not in retracement_level_support:
            continue
        retracement_level_support[level_id] += int(
            _mapping(row.get("support")).get("completed_studies", 0) or 0
        )
    retracement_partitions.sort(
        key=lambda row: (
            -int(_mapping(row.get("support")).get("completed_studies", 0) or 0),
            str(_mapping(row.get("partition")).get("level_id") or ""),
            str(row.get("bucket_id") or ""),
        )
    )
    retracement_catalog = _mapping(retracement.get("level_catalog"))
    compact_retracement = {
        "schema_version": retracement.get("schema_version"),
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "completed_study_count": int(
            retracement.get("completed_study_count", 0) or 0
        ),
        "interpretation_contract": deepcopy(
            _mapping(retracement.get("interpretation_contract"))
        ),
        "level_catalog": {
            level_id: deepcopy(_mapping(retracement_catalog.get(level_id)))
            for level_id in ("OTE_70_5", "CUSTOM_71_8")
            if _mapping(retracement_catalog.get(level_id))
        },
        "level_support": [
            {
                "level_id": level_id,
                "completed_study_count": retracement_level_support[level_id],
            }
            for level_id in ("OTE_70_5", "CUSTOM_71_8")
        ],
        "empirical_partitions": deepcopy(retracement_partitions[:16]),
        "partitions_truncated_count": max(0, len(retracement_partitions) - 16),
    }
    return {
        "schema_version": profile.get("schema_version"),
        "pair_id": profile.get("pair_id"),
        "symbol": profile.get("symbol"),
        "timeframe": profile.get("timeframe"),
        "observation_count": int(profile.get("observation_count", 0) or 0),
        "candle_count": int(profile.get("candle_count", 0) or 0),
        "first_observed_at": profile.get("first_observed_at"),
        "last_observed_at": profile.get("last_observed_at"),
        "candle": {
            "direction_counts": _top_counts(candle.get("direction_counts")),
            "type_counts": _top_counts(candle.get("type_counts")),
            "personality_counts": _top_counts(candle.get("personality_counts")),
            "averages": deepcopy(_mapping(candle.get("averages"))),
            "rates": deepcopy(_mapping(candle.get("rates"))),
        },
        "behavior": {
            "state_candle_counts": _top_counts(behavior.get("state_candle_counts")),
            "major_trend_counts": _top_counts(behavior.get("major_trend_counts")),
            "inner_trend_counts": _top_counts(behavior.get("inner_trend_counts")),
            "transition_probabilities": deepcopy(_mapping(behavior.get("transition_probabilities"))),
            "segment_averages": deepcopy(_mapping(behavior.get("segment_averages"))),
        },
        "regime_counts": _top_counts(profile.get("regime_counts")),
        "object_type_counts": _top_counts(profile.get("object_type_counts")),
        "outcome_association_contract": deepcopy(
            _mapping(profile.get("outcome_association_contract"))
        ),
        "outcome_associations": deepcopy(correlations[:12]),
        "retracement_confluence": compact_retracement,
        "study_only": True,
        "execution_authority": False,
    }


def _compact_similarity(value: Mapping[str, Any]) -> dict[str, Any]:
    matches = _rows(value.get("matches"))
    compact_matches = [
        {
            "sequence_id": row.get("sequence_id"),
            "similarity": row.get("similarity"),
            "regime": row.get("regime"),
            "outcome": deepcopy(_mapping(row.get("outcome"))),
            "latest": deepcopy(_mapping(row.get("latest"))),
            "shared_object_types": deepcopy(row.get("shared_object_types", [])),
            "explanations": list(cast(Sequence[Any], row.get("explanations", [])))[:4]
            if isinstance(row.get("explanations"), Sequence)
            and not isinstance(row.get("explanations"), (str, bytes, bytearray))
            else [],
        }
        for row in matches[:8]
    ]
    return {
        "schema_version": value.get("schema_version"),
        "status": value.get("status"),
        "study_only": True,
        "execution_authority": False,
        "query_fingerprint_id": value.get("query_fingerprint_id"),
        "filters": deepcopy(_mapping(value.get("filters"))),
        "match_count": int(value.get("match_count", 0) or 0),
        "historical_continuation": deepcopy(_mapping(value.get("historical_continuation"))),
        "matches": compact_matches,
    }


def _compact_candle_ledger(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value.get(key))
        for key in (
            "schema_version",
            "status",
            "study_only",
            "execution_authority",
            "pair_id",
            "symbol",
            "timeframe",
            "inserted_count",
            "updated_count",
            "changed_count",
            "skipped_unstable_count",
            "unique_candle_count",
            "total_observation_count",
        )
        if value.get(key) is not None
    }


def _compact_similarity_graph(
    value: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
) -> dict[str, Any]:
    nodes = [
        row
        for row in _rows(value.get("nodes"))
        if str(row.get("symbol") or "").upper() == symbol
        and str(row.get("timeframe") or "").upper() == timeframe
    ][-64:]
    identifiers = {str(row.get("fingerprint_id") or "") for row in nodes}
    edges = [
        row
        for row in _rows(value.get("edges"))
        if str(row.get("source") or "") in identifiers
        and str(row.get("target") or "") in identifiers
    ]
    edges.sort(
        key=lambda row: (
            -_clip01(row.get("similarity")),
            str(row.get("source") or ""),
        )
    )
    return {
        "schema_version": value.get("schema_version"),
        "status": value.get("status"),
        "graph_kind": value.get("graph_kind"),
        "directed": False,
        "study_only": True,
        "execution_authority": False,
        "node_count": len(nodes),
        "edge_count": len(edges[:128]),
        "nodes": [
            {
                "fingerprint_id": row.get("fingerprint_id"),
                "sequence_id": row.get("sequence_id"),
                "regime": row.get("regime"),
                "latest": deepcopy(_mapping(row.get("latest"))),
                "object_types": list(cast(Sequence[Any], row.get("object_types", [])))[:12]
                if isinstance(row.get("object_types"), Sequence)
                and not isinstance(row.get("object_types"), (str, bytes, bytearray))
                else [],
                "outcome": deepcopy(_mapping(row.get("outcome"))),
            }
            for row in nodes
        ],
        "edges": [
            {
                "source": row.get("source"),
                "target": row.get("target"),
                "similarity": row.get("similarity"),
                "shared_object_types": list(
                    cast(Sequence[Any], row.get("shared_object_types", []))
                )[:12]
                if isinstance(row.get("shared_object_types"), Sequence)
                and not isinstance(
                    row.get("shared_object_types"), (str, bytes, bytearray)
                )
                else [],
            }
            for row in edges[:128]
        ],
    }


def _trend_vote(row: Mapping[str, Any]) -> tuple[str, float]:
    return _side(row.get("side", row.get("direction", row.get("label")))), _clip01(
        row.get("confidence", row.get("strength", 0.0))
    )


def _directional_read(
    regression: Mapping[str, Any],
    behavior: Mapping[str, Any],
    similarity: Mapping[str, Any],
    latest_candle: Mapping[str, Any],
) -> dict[str, Any]:
    major = _mapping(regression.get("major_trend")) or _mapping(behavior.get("major_trend"))
    inner = _mapping(regression.get("inner_trend")) or _mapping(behavior.get("inner_trend"))
    continuation = _mapping(similarity.get("historical_continuation"))
    votes: list[tuple[str, float, float, str]] = []
    major_side, major_confidence = _trend_vote(major)
    inner_side, inner_confidence = _trend_vote(inner)
    if major_side in {"BUY", "SELL"}:
        votes.append((major_side, max(0.12, major_confidence), 0.52, "major regression"))
    if inner_side in {"BUY", "SELL"}:
        votes.append((inner_side, max(0.10, inner_confidence), 0.30, "inner regression"))
    continuation_side = _side(continuation.get("direction"))
    if continuation.get("status") == "SUPPORTED" and continuation_side in {"BUY", "SELL"}:
        votes.append(
            (
                continuation_side,
                _clip01(continuation.get("confidence")),
                0.18,
                f"{int(continuation.get('support', 0) or 0)} similar outcomes",
            )
        )
    if not votes:
        latest_side = _side(latest_candle.get("direction"))
        if latest_side in {"BUY", "SELL"}:
            votes.append((latest_side, 0.08, 0.12, "latest completed candle"))

    score = sum((1.0 if side == "BUY" else -1.0) * confidence * weight for side, confidence, weight, _ in votes)
    evidence_weight = sum(confidence * weight for _, confidence, weight, _ in votes)
    selected = "BUY" if score >= 0.0 else "SELL"
    if not votes:
        selected = "HOLD"
    agreement = sum(
        confidence * weight
        for side, confidence, weight, _ in votes
        if side == selected
    )
    opposition = sum(
        confidence * weight
        for side, confidence, weight, _ in votes
        if side != selected
    )
    confidence = _clip01((agreement - 0.45 * opposition) / max(0.20, evidence_weight)) if votes else 0.0
    reasons = [
        f"{label}: {side.lower()} ({confidence_value:.0%})"
        for side, confidence_value, _weight, label in votes
    ]
    return {
        "side": selected,
        "confidence": round(confidence, 6),
        "status": "DIRECTIONAL_STUDY" if selected in {"BUY", "SELL"} else "INSUFFICIENT_EVIDENCE",
        "agreement_score": round(max(0.0, agreement), 6),
        "opposition_score": round(max(0.0, opposition), 6),
        "reasons": reasons,
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
    }


class MarketStudyServiceV3:
    """Coordinate bounded durable V3 study memory on each proven candle close."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.pair_dna = PairDNAStoreV3(self.root_dir / "pair_dna_v3.json")
        self.candle_ledger = CandleLedgerStoreV3(
            self.root_dir / "candle_ledger_v3.sqlite3"
        )
        self.historical = HistoricalSequenceStoreV3(
            self.root_dir / "historical_sequences_v3.json"
        )
        self._pending_path = self.root_dir / "pending_outcomes_v3.json"
        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._result_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._graph_cache: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _pending_key(pair_key: tuple[str, str]) -> str:
        return f"{pair_key[0]}|{pair_key[1]}"

    def _load_pending_pair(
        self,
        pair_key: tuple[str, str],
    ) -> dict[str, Any] | None:
        with exclusive_store_lock(self._pending_path, timeout_seconds=5.0):
            state = read_json_document(self._pending_path)
        if state is None:
            return self._pending.get(pair_key)
        if (
            state.get("schema_version") != _PENDING_OUTCOME_SCHEMA_VERSION
            or state.get("study_only") is not True
            or state.get("execution_authority") is not False
        ):
            raise ValueError("pending outcome store is not a PhoenixGuard V3 study document")
        entries = _mapping(state.get("entries"))
        pending = _mapping(entries.get(self._pending_key(pair_key)))
        if not pending:
            self._pending.pop(pair_key, None)
            return None
        self._pending[pair_key] = pending
        return pending

    def _persist_pending_pair(
        self,
        pair_key: tuple[str, str],
        pending: Mapping[str, Any],
    ) -> None:
        with exclusive_store_lock(self._pending_path, timeout_seconds=5.0):
            stored_state = read_json_document(self._pending_path)
            state: dict[str, Any]
            if stored_state is None:
                state = {
                    "schema_version": _PENDING_OUTCOME_SCHEMA_VERSION,
                    "study_only": True,
                    "execution_authority": False,
                    "next_ordinal": 1,
                    "entries": dict[str, Any](),
                }
            else:
                state = stored_state
            if (
                state.get("schema_version") != _PENDING_OUTCOME_SCHEMA_VERSION
                or state.get("study_only") is not True
                or state.get("execution_authority") is not False
            ):
                raise ValueError(
                    "pending outcome store is not a PhoenixGuard V3 study document"
                )
            entries = _mapping(state.get("entries"))
            ordinal = max(1, _integer(state.get("next_ordinal"), 1))
            row = deepcopy(dict(pending))
            row["stored_ordinal"] = ordinal
            entries[self._pending_key(pair_key)] = row
            if len(entries) > _MAX_PENDING_PAIRS:
                eviction_key = min(
                    entries,
                    key=lambda key: (
                        _integer(_mapping(entries[key]).get("stored_ordinal")),
                        str(key),
                    ),
                )
                del entries[eviction_key]
            state["entries"] = entries
            state["next_ordinal"] = ordinal + 1
            write_json_atomic(self._pending_path, state)
        self._pending[pair_key] = deepcopy(dict(pending))

    @staticmethod
    def _outcome(
        previous: Mapping[str, Any],
        *,
        previous_close: float,
        current_close: float,
        baseline_range: float,
    ) -> dict[str, Any]:
        baseline = max(1e-12, abs(float(baseline_range)))
        realized = (current_close - previous_close) / baseline
        if realized > 0.04:
            direction = "UP"
        elif realized < -0.04:
            direction = "DOWN"
        else:
            direction = "REST"
        expected = _side(previous.get("directional_side"))
        actual = "BUY" if direction == "UP" else "SELL" if direction == "DOWN" else "HOLD"
        return {
            "direction": direction,
            "realized_return": round(realized, 8),
            "success": expected in {"BUY", "SELL"} and actual == expected,
            "horizon_candles": 1,
            "coordinate_continuity": "CURRENT_FRAME_REOBSERVATION",
        }

    def study(
        self,
        candles: Sequence[Mapping[str, Any]],
        *,
        symbol: object,
        timeframe: object,
        closed_candle_key: object,
        closed_candle_sequence: object = 0,
        regime: object = "UNKNOWN",
        regression: Mapping[str, Any] | None = None,
        objects: Sequence[Mapping[str, Any]] = (),
        observed_at: object = "",
    ) -> dict[str, Any]:
        canonical_symbol = str(symbol or "").strip().upper()
        canonical_timeframe = str(timeframe or "").strip().upper()
        close_key = str(closed_candle_key or "").strip()
        if not canonical_symbol or not canonical_timeframe or not close_key:
            return pending_market_study_v3(
                "Pair, timeframe, and closed-candle identity must all be confirmed.",
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
            )
        cache_key = (canonical_symbol, canonical_timeframe, close_key)
        with self._lock:
            cached = self._result_cache.get(cache_key)
            if cached is not None:
                return deepcopy(cached)

            candle_study = analyze_candle_sequence_v3(
                list(candles)[-_MAX_LIVE_CANDLES:],
                regime=str(regime or "UNKNOWN"),
                require_closed=True,
                max_candles=_MAX_LIVE_CANDLES,
            )
            if candle_study.get("status") != "STUDIED" or int(candle_study.get("studied_count", 0) or 0) < 4:
                result = pending_market_study_v3(
                    "At least four proven closed candles are required for a sequence fingerprint.",
                    symbol=canonical_symbol,
                    timeframe=canonical_timeframe,
                    status="INSUFFICIENT_HISTORY",
                )
                result["candle_intelligence"] = _compact_candle_study(candle_study)
                self._result_cache[cache_key] = result
                return deepcopy(result)

            behavior_study = measure_market_behavior_v3(
                candle_study,
                timeframe_seconds=max(1, int(_mapping(regression).get("timeframe_seconds", 300) or 300)),
                max_candles=_MAX_LIVE_CANDLES,
                inner_window=min(8, int(candle_study.get("studied_count", 0) or 0)),
            )
            sequence_id = f"{canonical_symbol}|{canonical_timeframe}|{close_key}"
            object_rows = [_mapping(row) for row in objects if _mapping(row)][:64]
            fingerprint = build_sequence_fingerprint_v3(
                candle_study,
                behavior_study,
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                sequence_id=sequence_id,
                objects=object_rows,
            )
            studied_candles = _rows(candle_study.get("candles"))
            object_relationship_graph = build_object_relationship_graph_v3(
                studied_candles,
                object_rows,
                max_object_nodes=32,
                max_candle_nodes=8,
                max_edges=128,
                max_points_per_object=8,
            )
            retracement_study = _mapping(
                object_relationship_graph.get("retracement_study")
            )
            latest_candle = studied_candles[-1]
            ledger_candle = deepcopy(latest_candle)
            ledger_candle["identity_stable"] = True
            ledger_candle["stable_candle_identity"] = close_key
            ledger_result = self.candle_ledger.record_candles(
                [ledger_candle],
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                observed_at=observed_at,
            )
            current_close = float(_mapping(latest_candle.get("ohlc")).get("close", 0.0) or 0.0)
            pair_key = (canonical_symbol, canonical_timeframe)
            prior = self._load_pending_pair(pair_key)
            current_sequence = max(0, _integer(closed_candle_sequence))
            maturation: dict[str, Any] = {
                "status": "NO_PREVIOUS_SEQUENCE",
                "study_only": True,
                "execution_authority": False,
            }
            if prior and str(prior.get("sequence_id")) != sequence_id:
                latest_space = str(latest_candle.get("coordinate_space") or "")
                reobserved: dict[str, Any] = {}
                reobserved_space = ""
                prior_sequence_value = prior.get("closed_candle_sequence")
                one_step_horizon_proven = bool(
                    isinstance(prior_sequence_value, int)
                    and not isinstance(prior_sequence_value, bool)
                    and prior_sequence_value >= 0
                    and current_sequence == prior_sequence_value + 1
                )
                if not one_step_horizon_proven:
                    maturation = {
                        "status": "SKIPPED_UNPROVEN_ONE_STEP_HORIZON",
                        "previous_sequence_id": str(
                            prior.get("sequence_id") or ""
                        ),
                        "previous_closed_candle_sequence": prior_sequence_value,
                        "current_closed_candle_sequence": current_sequence,
                        "required_horizon_candles": 1,
                        "study_only": True,
                        "execution_authority": False,
                    }
                else:
                    reobserved = _current_axis_prior_candle(
                        prior,
                        studied_candles,
                    )
                    reobserved_space = str(
                        reobserved.get("coordinate_space") or ""
                    )
                if (
                    one_step_horizon_proven
                    and reobserved
                    and reobserved_space == latest_space
                ):
                    previous_close = float(
                        _mapping(reobserved.get("ohlc")).get("close", 0.0) or 0.0
                    )
                    outcome = self._outcome(
                        prior,
                        previous_close=previous_close,
                        current_close=current_close,
                        baseline_range=_prior_only_baseline_range(
                            studied_candles
                        ),
                    )
                    enriched = deepcopy(_mapping(prior.get("fingerprint")))
                    enriched["outcome"] = outcome
                    self.historical.add(enriched)
                    self.pair_dna.record_study(
                        symbol=canonical_symbol,
                        timeframe=canonical_timeframe,
                        candle_study=_mapping(prior.get("candle_study")),
                        behavior_study=_mapping(prior.get("behavior_study")),
                        sequence_id=str(prior.get("sequence_id") or ""),
                        observed_at=prior.get("observed_at"),
                        objects=_rows(prior.get("objects")),
                        outcome=outcome,
                        retracement_study=(
                            _mapping(prior.get("retracement_study")) or None
                        ),
                    )
                    maturation = {
                        "status": "MATURED",
                        "previous_sequence_id": str(prior.get("sequence_id") or ""),
                        "matched_candle_id": str(reobserved.get("candle_id") or ""),
                        "matched_timestamp": reobserved.get("timestamp"),
                        "coordinate_space": latest_space,
                        "study_only": True,
                        "execution_authority": False,
                    }
                elif one_step_horizon_proven:
                    maturation = {
                        "status": "SKIPPED_UNPROVEN_COORDINATE_CONTINUITY",
                        "previous_sequence_id": str(prior.get("sequence_id") or ""),
                        "previous_coordinate_space": str(
                            prior.get("latest_coordinate_space") or ""
                        ),
                        "current_coordinate_space": latest_space,
                        "study_only": True,
                        "execution_authority": False,
                    }

            similarity = self.historical.search(
                fingerprint,
                top_k=8,
                minimum_similarity=0.55,
                same_pair=True,
                same_timeframe=True,
                min_outcome_support=3,
            )
            self.historical.add(fingerprint)
            sequence_number = current_sequence
            graph = self._graph_cache.get(pair_key)
            if graph is None or (sequence_number > 0 and sequence_number % 12 == 0):
                graph = _compact_similarity_graph(
                    self.historical.similarity_graph(
                        minimum_similarity=0.65,
                        max_edges_per_node=6,
                        same_pair=True,
                        same_timeframe=True,
                    ),
                    symbol=canonical_symbol,
                    timeframe=canonical_timeframe,
                )
                self._graph_cache[pair_key] = graph
            profile_result = self.pair_dna.get_profile(canonical_symbol, canonical_timeframe)
            compact_behavior = _compact_behavior(behavior_study)
            compact_similarity = _compact_similarity(similarity)
            compact_similarity["similarity_graph"] = deepcopy(graph)
            regression_row = deepcopy(_mapping(regression))
            regression_row.update(
                {
                    "study_only": True,
                    "execution_authority": False,
                    "major_trend": regression_row.get("major_trend")
                    or deepcopy(_mapping(behavior_study.get("major_trend"))),
                    "inner_trend": regression_row.get("inner_trend")
                    or deepcopy(_mapping(behavior_study.get("inner_trend"))),
                }
            )
            directional = _directional_read(
                regression_row,
                compact_behavior,
                compact_similarity,
                latest_candle,
            )
            result = _base_contract(
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                status="STUDIED",
            )
            result.update(
                {
                    "closed_candle_key": close_key,
                    "closed_candle_sequence": max(
                        0, _integer(closed_candle_sequence)
                    ),
                    "sequence_id": sequence_id,
                    "observed_at": str(observed_at or ""),
                    "regression": regression_row,
                    "candle_intelligence": _compact_candle_study(candle_study),
                    "candle_ledger": _compact_candle_ledger(ledger_result),
                    "behavior": compact_behavior,
                    "pair_dna": _compact_pair_profile(profile_result.get("profile")),
                    "object_relationship_graph": object_relationship_graph,
                    "historical_similarity": compact_similarity,
                    "outcome_maturation": maturation,
                    "directional_read": directional,
                }
            )
            pending = {
                "sequence_id": sequence_id,
                "closed_candle_sequence": current_sequence,
                "fingerprint": fingerprint,
                "candle_study": _pending_candle_study(candle_study),
                "behavior_study": _pending_behavior_study(behavior_study),
                "objects": object_rows[:_MAX_PENDING_OBJECTS],
                "retracement_study": _pending_retracement_study(
                    retracement_study
                ),
                "observed_at": str(observed_at or ""),
                "directional_side": directional.get("side"),
                "latest_candle_id": str(latest_candle.get("candle_id") or ""),
                "latest_candle_timestamp": latest_candle.get("timestamp"),
                "latest_candle_identity_stable": (
                    latest_candle.get("identity_stable") is True
                ),
                "latest_stable_candle_identity": str(
                    latest_candle.get("stable_candle_identity") or ""
                ),
                "latest_coordinate_space": str(
                    latest_candle.get("coordinate_space") or ""
                ),
            }
            self._persist_pending_pair(pair_key, pending)
            self._result_cache[cache_key] = deepcopy(result)
            if len(self._result_cache) > 64:
                oldest = next(iter(self._result_cache))
                del self._result_cache[oldest]
            return deepcopy(result)


__all__ = [
    "MARKET_STUDY_SCHEMA_VERSION",
    "MarketStudyServiceV3",
    "pending_market_study_v3",
]
