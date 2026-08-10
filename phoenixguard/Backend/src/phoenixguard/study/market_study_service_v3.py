"""Live orchestration for PhoenixGuard V3's observation-only market study.

The service deliberately sits beside the execution stack.  It turns proven
closed-candle geometry into explainable candle, behaviour, Pair DNA, and
historical-similarity evidence, but it never grants trade permission.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import threading
from typing import Any, Mapping, Sequence, cast

from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.latent_state_discovery_v3 import build_latent_state_discovery_v3
from phoenixguard.study.adaptive_feature_ontology_v3 import (
    ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
    AdaptiveFeatureOntologyV3,
    AdaptiveFeatureOntologyValidationError,
)
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.candle_ledger_v3 import CandleLedgerStoreV3
from phoenixguard.study.concept_drift_v3 import (
    CONCEPT_DRIFT_STUDY_SCHEMA_VERSION,
    ConceptDriftValidationError,
    OnlineConceptDriftDetectorV3,
)
from phoenixguard.study.cross_pair_association_v3 import (
    CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
)
from phoenixguard.study.cross_pair_coordinator_v3 import (
    CrossPairCoordinatorValidationError,
    CrossPairStudyCoordinatorV3,
)
from phoenixguard.study.historical_similarity_v3 import (
    HistoricalSequenceStoreV3,
    build_sequence_fingerprint_v3,
)
from phoenixguard.study.pair_dna_v3 import PairDNAStoreV3, PairDNAValidationError
from phoenixguard.study.path_clock_liquidity_store_v3 import (
    PathClockLiquiditySideStoreV3,
    PathClockLiquidityStoreValidationError,
    pending_path_clock_liquidity_v3,
)
from phoenixguard.study.object_relationship_graph_v3 import (
    build_object_relationship_graph_v3,
)
from phoenixguard.study.motif_lattice_v3 import (
    HISTORICAL_PATH_SCHEMA_VERSION,
    MAX_CLOSED_HISTORY_CANDLES,
    MAX_PATH_CANDLES,
    MAX_SURVIVAL_HORIZON,
    MOTIF_LATTICE_SCHEMA_VERSION,
    SURVIVAL_EVIDENCE_SCHEMA_VERSION,
    MotifLatticeValidationError,
    build_hierarchical_motif_lattice_v3,
    build_time_to_event_survival_evidence_v3,
    reconstruct_normalized_historical_path_v3,
)
from phoenixguard.study.study_claim_proof_v3 import (
    PUBLIC_STUDY_CANONICAL_PROJECTION_VERSION,
    STUDY_CLAIM_PROOF_SCHEMA_VERSION,
    StudyClaimProofValidationError,
    canonical_public_study_hash_v3,
    issue_study_claim_certificate_v3,
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
_MAX_CONTINUOUS_MOTIF_NODES_PER_LEVEL = 512
_MAX_PUBLIC_RECENT_CANDLES = 32

_CLOSED_CANDLE_TIME_PROOF_CACHE_FIELDS = (
    "schema_version",
    "symbol",
    "timeframe",
    "closed_candle_key",
    "closed_candle_sequence",
    "close_epoch_seconds",
    "timestamp_semantic",
    "timestamp_source",
    "proof_source",
    "bound_row_index",
    "transition_count",
    "source_cadence_seconds",
    "observed_epoch_seconds",
    "observation_latency_seconds",
    "contiguous_from_previous",
)
_ONTOLOGY_STORE_SCHEMA_VERSION = "PG_PAIR_SCOPED_ADAPTIVE_ONTOLOGY_STORE_V3"
_MAX_ONTOLOGY_PAIRS = 64
_CONCEPT_DRIFT_FIXED_WINDOW = 24


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


def _timestamp_seconds(value: object) -> float | None:
    """Normalize common epoch units and ISO text for strict event ordering."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        magnitude = abs(parsed)
        if magnitude >= 1e18:
            return parsed / 1e9
        if magnitude >= 1e15:
            return parsed / 1e6
        if magnitude >= 1e12:
            return parsed / 1e3
        return parsed
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None and math.isfinite(numeric):
        return _timestamp_seconds(numeric)
    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
    return parsed_datetime.astimezone(timezone.utc).timestamp()


def _closed_candle_time_proof_cache_token(
    value: Mapping[str, Any] | None,
) -> str:
    """Return a stable cache discriminator without validating or publishing proof.

    The JPCLF side store remains the sole proof validator and sanitizer.  This
    token only prevents an earlier fail-closed result for one candle key from
    hiding later evidence, while preserving the existing same-event freeze for
    every input other than the timing proof.
    """

    if value is None:
        return "CLOSED_CANDLE_TIME_PROOF_ABSENT"
    proof = dict(value)
    normalized: dict[str, object] = {}
    for field in _CLOSED_CANDLE_TIME_PROOF_CACHE_FIELDS:
        item = proof.get(field)
        if isinstance(item, float) and not math.isfinite(item):
            normalized[field] = {
                "invalid_non_finite_float": (
                    "NAN" if math.isnan(item) else "POSITIVE_INFINITY" if item > 0 else "NEGATIVE_INFINITY"
                )
            }
        elif item is None or isinstance(item, (bool, int, float, str)):
            normalized[field] = item
        else:
            normalized[field] = {
                "invalid_value_type": (
                    f"{type(item).__module__}.{type(item).__qualname__}"
                )
            }
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"CLOSED_CANDLE_TIME_PROOF_SHA256:{hashlib.sha256(encoded).hexdigest()}"


def _price_history_row(
    row: Mapping[str, Any],
    *,
    identity_field: str,
) -> tuple[float, dict[str, Any]] | None:
    if str(row.get("coordinate_space") or "").strip().upper() != "PRICE":
        return None
    timestamp = row.get("timestamp")
    order = _timestamp_seconds(timestamp)
    if order is None:
        return None
    ohlc = _mapping(row.get("ohlc"))
    try:
        open_value = float(cast(Any, ohlc.get("open")))
        high = float(cast(Any, ohlc.get("high")))
        low = float(cast(Any, ohlc.get("low")))
        close = float(cast(Any, ohlc.get("close")))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (open_value, high, low, close)):
        return None
    identity = str(row.get(identity_field) or "").strip()
    if not identity:
        identity = hashlib.sha256(
            f"{timestamp}|{open_value:.17g}|{high:.17g}|{low:.17g}|{close:.17g}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
    return order, {
        "candle_id": f"continuous-{identity}",
        "timestamp": deepcopy(timestamp),
        "open": open_value,
        "high": high,
        "low": low,
        "close": close,
        "closed": True,
    }


def _continuous_price_rows(
    candle_study: Mapping[str, Any],
    ledger_records: Sequence[Mapping[str, Any]],
    *,
    timeframe_seconds: int,
) -> list[dict[str, Any]]:
    """Merge current price truth with restart-safe ledger rows by timestamp.

    Current-frame rows win duplicate timestamps.  A detected gap resets the
    chain so only the latest exact contiguous tail can reach advanced studies.
    Proxy coordinates never enter this merge because their frame axes can
    change between captures.
    """

    by_order: dict[float, dict[str, Any]] = {}
    for row in reversed(list(ledger_records)):
        candidate = _price_history_row(row, identity_field="candle_identity")
        if candidate is not None:
            order, raw = candidate
            by_order[order] = raw
    for row in _rows(candle_study.get("candles")):
        candidate = _price_history_row(row, identity_field="stable_candle_identity")
        if candidate is not None:
            order, raw = candidate
            by_order[order] = raw
    tolerance = max(1e-6, timeframe_seconds * 1e-6)
    contiguous: list[dict[str, Any]] = []
    previous_order: float | None = None
    for order in sorted(by_order):
        if (
            previous_order is not None
            and abs((order - previous_order) - timeframe_seconds) > tolerance
        ):
            contiguous = []
        contiguous.append(by_order[order])
        previous_order = order
    return contiguous[-MAX_CLOSED_HISTORY_CANDLES:]


def _advanced_order_domain(candles: Sequence[Mapping[str, Any]]) -> str:
    if candles and all(_timestamp_seconds(row.get("timestamp")) is not None for row in candles):
        return "CLOSED_TIMESTAMP_V1"
    if candles and all(
        row.get("identity_stable") is True
        and row.get("identity_proof_source") == "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
        and isinstance(row.get("closed_candle_sequence"), int)
        and not isinstance(row.get("closed_candle_sequence"), bool)
        for row in candles
    ):
        return "TRACKER_EVENT_SEQUENCE_V3"
    return ""


def _jpclf_liquidity_state(
    candles: Sequence[Mapping[str, Any]],
    object_relationship_graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the closed-candle-only liquidity vector used by JPCLF.

    The vector intentionally uses dimensionless candle ratios and bounded graph
    counts.  It therefore remains comparable when a locked chart is rendered
    on a different pixel scale, while the side store separately proves path
    geometry by re-observing each anchor on the current coordinate axis.
    """

    if not candles:
        return {
            "wick_entropy": 0.0,
            "repeated_area_touches": 0,
            "late_sweep_motif_distance": 1.0,
            "wick_body_asymmetry": 0.0,
            "object_copresence_density": 0.0,
        }
    latest = candles[-1]
    ratios = _mapping(latest.get("ratios"))
    body = _clip01(ratios.get("body_to_range"))
    upper = _clip01(ratios.get("upper_wick_to_range"))
    lower = _clip01(ratios.get("lower_wick_to_range"))
    total = body + upper + lower
    entropy = 0.0
    if total > 1e-12:
        for value in (body / total, upper / total, lower / total):
            if value > 1e-12:
                entropy -= value * math.log(value)
        entropy /= math.log(3.0)

    recent = list(candles[-8:])
    sweep_count = 0
    for candle in recent:
        rejection = _mapping(_mapping(candle.get("interaction")).get("rejection"))
        if (
            rejection.get("upper_wick_swept_previous_high") is True
            or rejection.get("lower_wick_swept_previous_low") is True
        ):
            sweep_count += 1
    late_sweep_distance = 1.0 - (sweep_count / max(1, len(recent)))

    relation_counts = _mapping(object_relationship_graph.get("relation_counts"))
    repeated_touches = sum(
        max(0, _integer(count))
        for relation, count in relation_counts.items()
        if "TOUCH" in str(relation).upper()
        or "OVERLAP" in str(relation).upper()
        or "RETEST" in str(relation).upper()
    )
    nodes = _rows(object_relationship_graph.get("nodes"))
    edges = _rows(object_relationship_graph.get("edges"))
    object_nodes = [
        row
        for row in nodes
        if "OBJECT" in str(row.get("node_type") or "").upper()
    ]
    if not object_nodes:
        # Some graph revisions publish object nodes without a node_type label.
        object_nodes = [
            row
            for row in nodes
            if row.get("object_type") not in (None, "")
        ]
    object_density = min(
        1.0,
        (len(object_nodes) / 32.0) + (min(128, len(edges)) / 256.0),
    )
    body_denominator = max(body, 1.0 / 64.0)
    asymmetry = max(-64.0, min(64.0, (lower - upper) / body_denominator))
    return {
        "wick_entropy": round(_clip01(entropy), 8),
        "repeated_area_touches": min(64, repeated_touches),
        "late_sweep_motif_distance": round(max(0.0, late_sweep_distance), 8),
        "wick_body_asymmetry": round(asymmetry, 8),
        "object_copresence_density": round(object_density, 8),
    }


def _jpclf_resolver_bound_rows_v3(
    candles: Sequence[Mapping[str, Any]],
    closed_candle_time_proof: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], Mapping[str, Any] | None]:
    """Select the proven JPCLF axis and verify its supplied proof index.

    Candle intelligence may contain older screenshot rows that are useful to
    the general study but lack lifelong resolver identity or exact time.  They
    must not enter JPCLF.  The tracker expresses ``bound_row_index`` in this
    exact ordered subset, so the service verifies key, sequence, and close time
    at that index and then forwards the original proof unchanged.
    """

    trusted: list[dict[str, Any]] = []
    for source in candles:
        row = dict(source)
        sequence = row.get("closed_candle_sequence")
        if (
            row.get("closed") is not True
            or row.get("identity_stable") is not True
            or row.get("identity_proof_source")
            != "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
            or not str(row.get("stable_candle_identity") or "").startswith(
                "EXPLICIT:"
            )
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
            or _timestamp_seconds(row.get("timestamp")) is None
        ):
            continue
        trusted.append(row)

    if closed_candle_time_proof is None:
        return trusted, None

    proof = closed_candle_time_proof
    proof_key = str(proof.get("closed_candle_key") or "").strip()
    proof_sequence = proof.get("closed_candle_sequence")
    proof_resolver_index = proof.get("bound_row_index")
    proof_close_seconds = _timestamp_seconds(proof.get("close_epoch_seconds"))
    if (
        not proof_key
        or not isinstance(proof_sequence, int)
        or isinstance(proof_sequence, bool)
        or proof_sequence < 0
        or not isinstance(proof_resolver_index, int)
        or isinstance(proof_resolver_index, bool)
        or proof_resolver_index < 0
        or proof_close_seconds is None
    ):
        return [], proof

    if proof_resolver_index >= len(trusted):
        return [], proof
    expected_identity = f"EXPLICIT:{proof_key}"
    current = trusted[proof_resolver_index]
    current_seconds = _timestamp_seconds(current.get("timestamp"))
    if (
        current.get("stable_candle_identity") != expected_identity
        or current.get("closed_candle_sequence") != proof_sequence
        or current_seconds is None
        or abs(current_seconds - proof_close_seconds) > 1e-6
    ):
        return [], proof
    return trusted, proof


def _advanced_pending_contract(
    schema_version: str,
    *,
    symbol: str,
    timeframe: str,
    reason: object,
    closed_candle_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "status": "INSUFFICIENT_PROVEN_HISTORY",
        "reason": str(reason or "Advanced closed-candle history is not ready.")[:320],
        "study_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "symbol": symbol,
        "timeframe": timeframe,
        "continuous_window": {
            "fixed_sequence_horizon": False,
            "observed_closed_candle_count": max(0, int(closed_candle_count)),
            "retained_closed_candle_limit": MAX_CLOSED_HISTORY_CANDLES,
        },
    }


def _pending_advanced_studies(
    *,
    symbol: str,
    timeframe: str,
    reason: object,
    closed_candle_count: int,
) -> dict[str, dict[str, Any]]:
    fields = (
        ("motif_lattice", MOTIF_LATTICE_SCHEMA_VERSION),
        ("survival_network", SURVIVAL_EVIDENCE_SCHEMA_VERSION),
        ("path_reconstruction", HISTORICAL_PATH_SCHEMA_VERSION),
    )
    return {
        name: _advanced_pending_contract(
            schema,
            symbol=symbol,
            timeframe=timeframe,
            reason=reason,
            closed_candle_count=closed_candle_count,
        )
        for name, schema in fields
    }


def _path_anchor(behavior_study: Mapping[str, Any], candle_count: int) -> tuple[int, str]:
    completed = [
        row
        for row in _rows(behavior_study.get("segments"))
        if 0 <= _integer(row.get("end_index"), -1) < candle_count - 1
    ]
    if not completed:
        return 0, "RETAINED_HISTORY_START"
    selected = max(
        completed,
        key=lambda row: (
            _integer(row.get("end_index"), -1),
            _integer(row.get("start_index"), -1),
        ),
    )
    return _integer(selected.get("end_index")), "LATEST_COMPLETED_BEHAVIOR_SEGMENT_END"


def _realized_reference_direction(
    candles: Sequence[Mapping[str, Any]],
    *,
    anchor_index: int,
) -> str:
    anchor_close = float(_mapping(candles[anchor_index].get("ohlc")).get("close", 0.0) or 0.0)
    latest_close = float(_mapping(candles[-1].get("ohlc")).get("close", 0.0) or 0.0)
    if latest_close > anchor_close:
        return "UP"
    if latest_close < anchor_close:
        return "DOWN"
    latest_direction = str(candles[-1].get("direction") or "").upper()
    return "DOWN" if latest_direction == "BEARISH" else "UP"


def _survival_topology(
    curves: Sequence[Mapping[str, Any]],
    *,
    object_conditioned: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state_names = sorted({str(row.get("origin_state") or "UNKNOWN") for row in curves})
    event_names = sorted({str(row.get("event_type") or "UNKNOWN") for row in curves})
    nodes = [
        {"node_id": f"STATE:{name}", "node_type": "CANDLE_BEHAVIOR_STATE", "label": name}
        for name in state_names
    ]
    nodes.extend(
        {"node_id": f"EVENT:{name}", "node_type": "TIME_TO_EVENT", "label": name}
        for name in event_names
    )
    edges = [
        {
            "from_node_id": f"STATE:{row.get('origin_state')}",
            "to_node_id": f"EVENT:{row.get('event_type')}",
            "curve_index": index,
            "support": int(row.get("support", 0) or 0),
            "status": str(row.get("status") or "INSUFFICIENT_SUPPORT"),
            "causal": False,
        }
        for index, row in enumerate(curves)
    ]
    object_network = _mapping(_mapping(object_conditioned).get("network"))
    object_nodes = _rows(object_network.get("nodes"))
    object_edges = _rows(object_network.get("edges"))
    nodes.extend(object_nodes)
    edges.extend(object_edges)
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "edge_semantics": "NON_CAUSAL_HISTORICAL_TIME_TO_EVENT_ASSOCIATION",
        "object_conditioning": {
            "status": _mapping(object_conditioned).get("status"),
            "curve_count": len(_rows(_mapping(object_conditioned).get("curves"))),
            "uses_durable_matured_pair_history": True,
            "uses_durable_resolver_stable_observation_history": True,
            "requires_outcome_labels": False,
            "current_frame_objects_used_as_historical_support": False,
        },
    }


def _object_conditioned_time_to_event(
    pair_profile: Mapping[str, Any] | None,
    *,
    min_support: int = 3,
    max_recent_sequences: int = 256,
    max_object_types: int = 16,
    max_horizon: int = 64,
) -> dict[str, Any]:
    """Estimate bounded survival from resolver-stable Pair DNA observations."""

    rows = _rows(_mapping(pair_profile).get("recent_sequences"))[
        -max_recent_sequences:
    ]
    normalized: list[dict[str, Any]] = []
    frequencies: dict[str, int] = {}
    for row in rows:
        state = str(row.get("current_state") or "UNKNOWN").strip().upper()
        object_types = sorted(
            {
                str(value).strip().upper()
                for value in cast(Sequence[object], row.get("object_types", []))
                if str(value).strip()
            }
        )[:32]
        normalized.append({"state": state, "object_types": object_types})
        for object_type in object_types:
            frequencies[object_type] = frequencies.get(object_type, 0) + 1
    selected_types = [
        name
        for name, _ in sorted(
            frequencies.items(),
            key=lambda item: (-item[1], item[0]),
        )[:max_object_types]
    ]
    selected = set(selected_types)
    observations: dict[tuple[str, str, str], list[tuple[int, bool]]] = {}
    swing_states = {"UP_SWING", "DOWN_SWING"}
    for origin, row in enumerate(normalized[:-1]):
        state = str(row["state"])
        available = min(max_horizon, len(normalized) - origin - 1)
        if available <= 0:
            continue
        event_types = ["NEXT_SWING"]
        if state in swing_states:
            event_types.append("DIRECTION_CHANGE")
        if state == "REST":
            event_types.append("REST_END")
        for object_type in cast(list[str], row["object_types"]):
            if object_type not in selected:
                continue
            for event_type in event_types:
                duration = available
                observed = False
                for distance in range(1, available + 1):
                    current = str(normalized[origin + distance]["state"])
                    previous = str(normalized[origin + distance - 1]["state"])
                    if event_type == "NEXT_SWING":
                        occurred = current in swing_states and current != previous
                    elif event_type == "DIRECTION_CHANGE":
                        occurred = (
                            state == "UP_SWING" and current == "DOWN_SWING"
                        ) or (
                            state == "DOWN_SWING" and current == "UP_SWING"
                        )
                    else:
                        occurred = current != "REST"
                    if occurred:
                        duration = distance
                        observed = True
                        break
                observations.setdefault(
                    (object_type, state, event_type), []
                ).append((duration, observed))
    curves: list[dict[str, Any]] = []
    for (object_type, state, event_type), values in sorted(
        observations.items()
    ):
        maximum_duration = max(duration for duration, _ in values)
        survival = 1.0
        restricted_mean = 0.0
        points: list[dict[str, Any]] = []
        for duration in range(1, maximum_duration + 1):
            restricted_mean += survival
            at_risk = sum(value >= duration for value, _ in values)
            events = sum(
                value == duration and observed
                for value, observed in values
            )
            censored = sum(
                value == duration and not observed
                for value, observed in values
            )
            if events:
                survival *= 1.0 - events / at_risk
            points.append(
                {
                    "closed_candles": duration,
                    "at_risk": at_risk,
                    "events": events,
                    "censored": censored,
                    "survival_probability": round(survival, 6),
                    "cumulative_event_probability": round(1.0 - survival, 6),
                }
            )
        median = next(
            (
                int(point["closed_candles"])
                for point in points
                if float(point["survival_probability"]) <= 0.5
            ),
            None,
        )
        curves.append(
            {
                "object_type": object_type,
                "origin_state": state,
                "event_type": event_type,
                "status": (
                    "SUPPORTED" if len(values) >= min_support
                    else "INSUFFICIENT_SUPPORT"
                ),
                "support": len(values),
                "minimum_support": min_support,
                "event_count": sum(observed for _, observed in values),
                "right_censored_count": sum(
                    not observed for _, observed in values
                ),
                "median_event_time_closed_candles": median,
                "restricted_mean_event_free_closed_candles": round(
                    restricted_mean,
                    6,
                ),
                "curve": points,
            }
        )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for object_type in selected_types:
        token = hashlib.sha256(object_type.encode("utf-8")).hexdigest()[:16]
        nodes.append(
            {
                "node_id": f"OBJECT_TYPE:{token}",
                "node_type": "MARKET_OBJECT_TYPE",
                "label": object_type,
            }
        )
    confluence_ids: set[str] = set()
    event_ids: set[str] = set()
    for index, curve in enumerate(curves):
        object_type = str(curve["object_type"])
        state = str(curve["origin_state"])
        event_type = str(curve["event_type"])
        object_token = hashlib.sha256(object_type.encode("utf-8")).hexdigest()[:16]
        confluence_token = hashlib.sha256(
            f"{object_type}|{state}".encode("utf-8")
        ).hexdigest()[:16]
        confluence_id = f"OBJECT_STATE:{confluence_token}"
        event_id = f"OBJECT_EVENT:{event_type}"
        if confluence_id not in confluence_ids:
            nodes.append(
                {
                    "node_id": confluence_id,
                    "node_type": "OBJECT_CANDLE_STATE_CONFLUENCE",
                    "object_type": object_type,
                    "candle_state": state,
                }
            )
            confluence_ids.add(confluence_id)
            edges.append(
                {
                    "from_node_id": f"OBJECT_TYPE:{object_token}",
                    "to_node_id": confluence_id,
                    "relation": "OBSERVED_IN_CANDLE_STATE",
                    "causal": False,
                }
            )
        if event_id not in event_ids:
            nodes.append(
                {
                    "node_id": event_id,
                    "node_type": "OBJECT_CONDITIONED_TIME_TO_EVENT",
                    "label": event_type,
                }
            )
            event_ids.add(event_id)
        edges.append(
            {
                "from_node_id": confluence_id,
                "to_node_id": event_id,
                "relation": "HISTORICAL_TIME_TO_EVENT_ASSOCIATION",
                "curve_index": index,
                "support": int(curve["support"]),
                "status": str(curve["status"]),
                "causal": False,
            }
        )
    return {
        "schema_version": "PG_OBJECT_CONDITIONED_TIME_TO_EVENT_V3",
        "status": "STUDIED" if curves else "INSUFFICIENT_MATURED_OBJECT_HISTORY",
        "matured_sequence_count": len(normalized),
        "selected_object_type_count": len(selected_types),
        "curve_count": len(curves),
        "curves": curves,
        "network": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        },
        "bounds": {
            "max_recent_sequences": max_recent_sequences,
            "max_object_types": max_object_types,
            "max_horizon_closed_candles": max_horizon,
        },
        "history_contract": {
            "source": "PAIR_DNA_MATURED_COMPLETED_STUDIES",
            "closed_history_only": True,
            "current_frame_objects_are_not_historical_support": True,
        },
        "study_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
    }


def _bounded_trajectory_library(
    history: Mapping[str, Any],
    motif: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    max_entries: int = 16,
    max_follow_through_candles: int = 32,
) -> dict[str, Any]:
    """Reconstruct bounded historical follow-through paths keyed by motifs."""

    candidates: list[dict[str, Any]] = []
    for level in _rows(motif.get("levels")):
        level_index = _integer(level.get("level"), -1)
        if level_index < 1:
            continue
        for node in _rows(level.get("nodes")):
            span = _mapping(node.get("span"))
            end_index = _integer(span.get("end_index"), -1)
            if 0 <= end_index < len(candles) - 1:
                candidates.append(
                    {
                        "level": level_index,
                        "kind": str(node.get("kind") or "UNKNOWN"),
                        "motif_token": str(node.get("motif_token") or ""),
                        "start_index": _integer(span.get("start_index"), -1),
                        "end_index": end_index,
                    }
                )
    candidates.sort(
        key=lambda row: (
            -int(row["end_index"]),
            -int(row["level"]),
            str(row["motif_token"]),
        )
    )
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        if len(entries) >= max_entries:
            break
        token = str(candidate["motif_token"])
        anchor = int(candidate["end_index"])
        key = (token, anchor)
        if not token or key in seen:
            continue
        seen.add(key)
        selected_end = min(
            len(candles) - 1,
            anchor + max(2, max_follow_through_candles) - 1,
        )
        reference_direction = _realized_reference_direction(
            candles[: selected_end + 1],
            anchor_index=anchor,
        )
        try:
            trajectory = reconstruct_normalized_historical_path_v3(
                history,
                anchor_index=anchor,
                end_index=selected_end,
                reference_direction=reference_direction,
                max_path_candles=min(
                    MAX_PATH_CANDLES,
                    selected_end - anchor + 1,
                ),
                normalization_lookback=min(64, anchor + 1),
            )
        except MotifLatticeValidationError:
            continue
        entries.append(
            {
                "trajectory_id": trajectory.get("path_id"),
                "match_key": f"MOTIF:{token}",
                "motif_token": token,
                "motif_level": candidate["level"],
                "motif_kind": candidate["kind"],
                "motif_span": {
                    "start_index": candidate["start_index"],
                    "end_index": anchor,
                },
                "follow_through_end_index": selected_end,
                "point_count": trajectory.get("point_count"),
                "reference_direction": reference_direction,
                "path_summary": deepcopy(trajectory.get("path_summary")),
                "points": deepcopy(_rows(trajectory.get("points"))),
                "historical_only": True,
                "study_only": True,
                "causal": False,
                "execution_authority": False,
            }
        )
    by_motif: dict[str, list[str]] = {}
    for entry in entries:
        by_motif.setdefault(str(entry["motif_token"]), []).append(
            str(entry["trajectory_id"] or "")
        )
    return {
        "schema_version": "PG_HISTORICAL_TRAJECTORY_LIBRARY_V3",
        "status": "READY" if entries else "INSUFFICIENT_COMPLETED_FOLLOW_THROUGH",
        "entry_count": len(entries),
        "max_entries": max_entries,
        "max_follow_through_candles": max_follow_through_candles,
        "entries": entries,
        "index_by_motif_token": by_motif,
        "history_id": motif.get("history_id"),
        "restart_safe_derivation": "PAIR_LEDGER_RECONSTRUCTION",
        "study_only": True,
        "causal": False,
        "historical_only": True,
        "execution_authority": False,
        "grants_entry_permission": False,
    }


def _continuous_advanced_studies(
    candle_study: Mapping[str, Any],
    behavior_study: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    history_source: str,
    pair_profile: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    candles = _rows(candle_study.get("candles"))
    order_domain = _advanced_order_domain(candles)
    if not order_domain:
        return _pending_advanced_studies(
            symbol=symbol,
            timeframe=timeframe,
            reason="Stable contiguous timestamp or tracker-event order is unproven.",
            closed_candle_count=len(candles),
        )
    history = {
        "symbol": symbol,
        "timeframe": timeframe,
        "order_domain": order_domain,
        "candle_study": candle_study,
        "behavior_study": behavior_study,
    }
    continuous_window = {
        "fixed_sequence_horizon": False,
        "observed_closed_candle_count": len(candles),
        "retained_closed_candle_limit": MAX_CLOSED_HISTORY_CANDLES,
        "history_source": history_source,
    }
    result: dict[str, dict[str, Any]] = {}
    motif: dict[str, Any] = {}
    try:
        motif = build_hierarchical_motif_lattice_v3(
            history,
            max_nodes_per_level=_MAX_CONTINUOUS_MOTIF_NODES_PER_LEVEL,
        )
        motif["continuous_window"] = deepcopy(continuous_window)
        result["motif_lattice"] = motif
    except MotifLatticeValidationError as exc:
        result["motif_lattice"] = _advanced_pending_contract(
            MOTIF_LATTICE_SCHEMA_VERSION,
            symbol=symbol,
            timeframe=timeframe,
            reason=str(exc),
            closed_candle_count=len(candles),
        )

    try:
        survival = build_time_to_event_survival_evidence_v3(
            [history],
            max_horizon=min(MAX_SURVIVAL_HORIZON, len(candles) - 1),
            min_support=3,
            max_histories=1,
        )
        survival["continuous_window"] = deepcopy(continuous_window)
        object_conditioned = _object_conditioned_time_to_event(pair_profile)
        survival["object_conditioned_time_to_event"] = object_conditioned
        survival["network"] = _survival_topology(
            _rows(survival.get("curves")),
            object_conditioned=object_conditioned,
        )
        result["survival_network"] = survival
    except MotifLatticeValidationError as exc:
        result["survival_network"] = _advanced_pending_contract(
            SURVIVAL_EVIDENCE_SCHEMA_VERSION,
            symbol=symbol,
            timeframe=timeframe,
            reason=str(exc),
            closed_candle_count=len(candles),
        )

    try:
        anchor_index, anchor_method = _path_anchor(behavior_study, len(candles))
        minimum_anchor = max(0, len(candles) - MAX_PATH_CANDLES)
        if anchor_index < minimum_anchor:
            anchor_index = minimum_anchor
            anchor_method = f"{anchor_method}|BOUNDED_TAIL_CLAMP"
        reference_direction = _realized_reference_direction(
            candles,
            anchor_index=anchor_index,
        )
        path = reconstruct_normalized_historical_path_v3(
            history,
            anchor_index=anchor_index,
            end_index=len(candles) - 1,
            reference_direction=reference_direction,
            max_path_candles=min(MAX_PATH_CANDLES, len(candles) - anchor_index),
            normalization_lookback=min(64, anchor_index + 1),
        )
        path["continuous_window"] = deepcopy(continuous_window)
        path["anchor_selection"] = {
            "method": anchor_method,
            "fixed_sequence_horizon": False,
            "reference_direction_basis": "HISTORICAL_REALIZED_PATH_LABEL",
            "reference_direction_is_trade_instruction": False,
        }
        if motif.get("status") == "STUDIED":
            path["trajectory_library"] = _bounded_trajectory_library(
                history,
                motif,
                candles,
            )
        result["path_reconstruction"] = path
    except MotifLatticeValidationError as exc:
        result["path_reconstruction"] = _advanced_pending_contract(
            HISTORICAL_PATH_SCHEMA_VERSION,
            symbol=symbol,
            timeframe=timeframe,
            reason=str(exc),
            closed_candle_count=len(candles),
        )
    return result


def _evidence_identity(row: Mapping[str, Any], index: int) -> str:
    stable = str(row.get("stable_candle_identity") or "").strip()
    timestamp = _identity_token(row.get("timestamp"))
    source = stable or timestamp or f"UNPROVEN-{index}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def _pending_research_contract(
    schema_version: str,
    *,
    symbol: str,
    timeframe: str,
    reason: object,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "status": "INSUFFICIENT_PROVEN_HISTORY",
        "reason": str(reason or "Research evidence is not ready.")[:320],
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
        "symbol": symbol,
        "timeframe": timeframe,
    }


def _pending_research_studies(
    *,
    symbol: str,
    timeframe: str,
    reason: object,
) -> dict[str, dict[str, Any]]:
    concept = _pending_research_contract(
        CONCEPT_DRIFT_STUDY_SCHEMA_VERSION,
        symbol=symbol,
        timeframe=timeframe,
        reason=reason,
    )
    regime = deepcopy(concept)
    regime["status"] = "REGIME_PARTITION_UNPROVEN"
    return {
        "adaptive_feature_ontology": _pending_research_contract(
            ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
            symbol=symbol,
            timeframe=timeframe,
            reason=reason,
        ),
        "concept_drift": concept,
        "regime_partition": regime,
        "cross_pair_association": _pending_research_contract(
            CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
            symbol=symbol,
            timeframe=timeframe,
            reason=reason,
        ),
        "claim_proofs": {
            "schema_version": STUDY_CLAIM_PROOF_SCHEMA_VERSION,
            "status": "INSUFFICIENT_PROVEN_HISTORY",
            "reason": str(reason or "Proof evidence is not ready.")[:320],
            "certificate_count": 0,
            "certificates": [],
            "coverage": [],
            "study_only": True,
            "causal": False,
            "execution_authority": False,
            "grants_entry_permission": False,
        },
    }


def _pearson_correlation(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 3:
        return 0.0
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left, right, strict=True)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_scale <= 1e-12 or right_scale <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, numerator / (left_scale * right_scale)))


def _derived_feature_evaluation(
    candles: Sequence[Mapping[str, Any]],
    *,
    feature_id: str,
) -> dict[str, Any] | None:
    values: list[float] = []
    outcomes: list[float] = []
    for index in range(len(candles) - 1):
        current = candles[index]
        following = candles[index + 1]
        current_ohlc = _mapping(current.get("ohlc"))
        following_ohlc = _mapping(following.get("ohlc"))
        try:
            current_close = float(cast(Any, current_ohlc.get("close")))
            current_high = float(cast(Any, current_ohlc.get("high")))
            current_low = float(cast(Any, current_ohlc.get("low")))
            following_close = float(cast(Any, following_ohlc.get("close")))
        except (TypeError, ValueError):
            continue
        scale = current_high - current_low
        if not all(
            math.isfinite(value)
            for value in (current_close, current_high, current_low, following_close)
        ) or scale <= 1e-12:
            continue
        if feature_id == "WICK_ASYMMETRY":
            feature_value = _ratio_value(current, "upper_wick_to_range") - _ratio_value(
                current,
                "lower_wick_to_range",
            )
            outcome = (following_close - current_close) / scale
        else:
            if index == 0:
                continue
            previous_interaction = _mapping(candles[index - 1].get("interaction"))
            previous_rejection = _mapping(
                previous_interaction.get("rejection")
            )
            if previous_rejection.get("detected") is not True:
                continue
            feature_value = -_ratio_value(current, "range_vs_sequence_median")
            outcome = abs(following_close - current_close) / scale
        if math.isfinite(feature_value) and math.isfinite(outcome):
            values.append(feature_value)
            outcomes.append(outcome)
    support = len(values)
    if support < 8:
        return None
    holdout_support = max(1, support // 4)
    training_end = support - holdout_support
    training_values = values[:training_end]
    training_outcomes = outcomes[:training_end]
    global_effect = _pearson_correlation(training_values, training_outcomes)
    holdout_effect = _pearson_correlation(
        values[training_end:],
        outcomes[training_end:],
    )
    partition_count = min(4, max(1, len(training_values) // 8))
    partition_effects: list[float] = []
    for partition in range(partition_count):
        start = round(partition * len(training_values) / partition_count)
        end = round((partition + 1) * len(training_values) / partition_count)
        if end - start >= 3:
            partition_effects.append(
                _pearson_correlation(
                    training_values[start:end],
                    training_outcomes[start:end],
                )
            )
    sign = 1 if global_effect >= 0.0 else -1
    stability_effects = [*partition_effects, holdout_effect]
    stable = sum(
        effect == 0.0 or (1 if effect > 0.0 else -1) == sign
        for effect in stability_effects
    )
    stability = stable / max(1, len(stability_effects))
    observed = abs(global_effect)
    offsets = range(1, min(64, len(training_outcomes)))
    null_effects = [
        abs(
            _pearson_correlation(
                training_values,
                training_outcomes[offset:] + training_outcomes[:offset],
            )
        )
        for offset in offsets
    ]
    exceedances = sum(effect >= observed - 1e-12 for effect in null_effects)
    empirical_p = (1 + exceedances) / (1 + len(null_effects))
    return {
        "support_count": support,
        "holdout_support_count": holdout_support,
        "independent_partition_count": len(stability_effects),
        "closed_candle_only": True,
        "temporal_precedence_verified": True,
        "future_leakage_detected": False,
        "deterministic_derivation": True,
        "coordinate_space_preserved": True,
        "order_domain_preserved": True,
        "stability_score": round(stability, 8),
        "effect_size": round(min(observed, abs(holdout_effect)), 8),
        "training_effect_size": round(observed, 8),
        "holdout_effect_size": round(abs(holdout_effect), 8),
        "adjusted_p_value": round(min(1.0, 2.0 * empirical_p), 8),
        "evaluation_method": "NEXT_CLOSED_CANDLE_CIRCULAR_SHIFT_ASSOCIATION_V3",
        "null_shift_count": len(null_effects),
    }


def _adaptive_feature_ontology_study(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    coordinate_space: str,
    order_domain: str,
    ontology: AdaptiveFeatureOntologyV3 | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    evidence_ids = [_evidence_identity(row, index) for index, row in enumerate(candles)]
    try:
        registry = ontology or AdaptiveFeatureOntologyV3(
            symbol=symbol,
            timeframe=timeframe,
            max_features=8,
            max_revisions_per_feature=32,
            minimum_support=32,
            minimum_holdout_support=8,
            minimum_independent_partitions=3,
            minimum_stability_score=0.75,
            minimum_effect_size=0.15,
            maximum_adjusted_p_value=0.05,
        )
        candidates = (
            (
                "WICK_ASYMMETRY",
                {
                    "description": "Upper minus lower wick share on one closed candle",
                    "inputs": ["upper_wick_to_range", "lower_wick_to_range"],
                },
                {
                    "algorithm_id": "PG_WICK_ASYMMETRY_V3",
                    "algorithm_version": "3.0.0",
                    "expression": "upper_wick_to_range-lower_wick_to_range",
                },
            ),
            (
                "RANGE_COMPRESSION_AFTER_REJECTION",
                {
                    "description": (
                        "Range compression ratio after a closed-candle rejection event"
                    ),
                    "inputs": ["range_vs_sequence_median", "rejection_detected"],
                },
                {
                    "algorithm_id": "PG_COMPRESSION_AFTER_REJECTION_V3",
                    "algorithm_version": "3.0.0",
                    "expression": "range_multiple when prior rejection is true",
                },
            ),
        )
        for feature_id, definition, derivation in candidates:
            try:
                feature_result = registry.get_feature(feature_id)
            except AdaptiveFeatureOntologyValidationError:
                feature_result = registry.propose_shadow_feature(
                    feature_id=feature_id,
                    definition=definition,
                    derivation=derivation,
                    closed_candle_ids=evidence_ids,
                    coordinate_space=coordinate_space,
                    order_domain=order_domain,
                )
            feature = _mapping(feature_result.get("feature"))
            if feature.get("status") != "SHADOW":
                continue
            evaluation = _derived_feature_evaluation(
                candles,
                feature_id=feature_id,
            )
            if evaluation is None:
                continue
            revisions = _rows(feature.get("revisions"))
            latest_gate = _mapping(revisions[-1].get("promotion_gate")) if revisions else {}
            last_support = _integer(
                _mapping(latest_gate.get("measurements")).get("support_count"),
                0,
            )
            support = _integer(evaluation.get("support_count"), 0)
            if latest_gate and support < last_support + 16:
                continue
            evaluated = registry.evaluate_promotion_gate(
                feature_id,
                evaluation=evaluation,
                closed_candle_ids=evidence_ids,
                coordinate_space=coordinate_space,
                order_domain=order_domain,
            )
            evaluated_feature = _mapping(evaluated.get("feature"))
            evaluated_revisions = _rows(evaluated_feature.get("revisions"))
            gate = _mapping(evaluated_revisions[-1].get("promotion_gate"))
            if gate.get("passed") is True:
                registry.promote(feature_id)
        audit = registry.snapshot()
        public = registry.public_study_snapshot()
    except AdaptiveFeatureOntologyValidationError as exc:
        return (
            _pending_research_contract(
                ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
                symbol=symbol,
                timeframe=timeframe,
                reason=str(exc),
            ),
            None,
        )
    features = _rows(audit.get("features"))
    public_features = _rows(public.get("features"))
    evaluated = sum(
        any(_mapping(revision).get("promotion_gate") for revision in _rows(feature.get("revisions")))
        for feature in features
    )
    return ({
        "schema_version": ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
        "status": (
            "READY" if public_features else "SHADOW_EVIDENCE_ACCUMULATING"
        ),
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
        "symbol": symbol,
        "timeframe": timeframe,
        "coordinate_space": coordinate_space,
        "order_domain": order_domain,
        "ontology_version": int(public.get("ontology_version", 0) or 0),
        "namespace": "PUBLIC_STUDY",
        "public_features": public_features,
        "promoted_feature_count": len(public_features),
        "shadow_features_excluded": True,
        "shadow_audit": {
            "shadow_feature_count": sum(
                str(feature.get("status") or "") == "SHADOW"
                for feature in features
            ),
            "evaluated_shadow_feature_count": evaluated,
            "evidence_closed_candle_count": len(candles),
            "definitions_published": False,
            "promotion_requires_real_holdout_gate": True,
        },
        "interpretation": (
            "Shadow candidates accumulate audit evidence only. No feature is public "
            "until its explicit temporal-safety and holdout gate passes."
        ),
    }, audit)


def _ratio_value(row: Mapping[str, Any], name: str) -> float:
    value = _mapping(row.get("ratios")).get(name, 0.0)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _drift_order_index(
    row: Mapping[str, Any],
    *,
    order_domain: str,
) -> int | None:
    if order_domain == "TRACKER_EVENT_SEQUENCE_V3":
        value = row.get("closed_candle_sequence")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None
    timestamp = _timestamp_seconds(row.get("timestamp"))
    return None if timestamp is None else int(round(timestamp * 1_000_000))


def _concept_drift_study(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    coordinate_space: str,
    order_domain: str,
    pair_dna: PairDNAStoreV3,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(candles) < 8:
        pending = _pending_research_contract(
            CONCEPT_DRIFT_STUDY_SCHEMA_VERSION,
            symbol=symbol,
            timeframe=timeframe,
            reason="At least eight proven closed candles are required for drift comparison.",
        )
        regime = deepcopy(pending)
        regime["status"] = "REGIME_PARTITION_WARMING"
        return pending, regime
    feature_names = (
        "BODY_RATIO",
        "WICK_ASYMMETRY",
        "RANGE_MULTIPLE",
        "CLOSE_LOCATION",
    )
    try:
        saved = pair_dna.get_concept_drift_state(symbol, timeframe)
        saved_state = _mapping(saved.get("detector_state"))
        if saved.get("status") == "READY" and saved_state:
            detector = OnlineConceptDriftDetectorV3.from_snapshot(
                saved_state,
                symbol=symbol,
                timeframe=timeframe,
            )
        else:
            detector = OnlineConceptDriftDetectorV3(
                symbol=symbol,
                timeframe=timeframe,
                coordinate_space=coordinate_space,
                order_domain=order_domain,
                feature_names=feature_names,
                window_size=_CONCEPT_DRIFT_FIXED_WINDOW,
                max_regime_partitions=64,
            )
        observations: list[dict[str, Any]] = []
        for index, candle in enumerate(candles):
            order_index = _drift_order_index(candle, order_domain=order_domain)
            if order_index is None:
                raise ConceptDriftValidationError("closed-candle order is unproven")
            observations.append(
                {
                    "candle_id": _evidence_identity(candle, index),
                    "order_index": order_index,
                    "is_closed": True,
                    "coordinate_space": coordinate_space,
                    "order_domain": order_domain,
                    "features": {
                        "BODY_RATIO": _ratio_value(candle, "body_to_range"),
                        "WICK_ASYMMETRY": (
                            _ratio_value(candle, "upper_wick_to_range")
                            - _ratio_value(candle, "lower_wick_to_range")
                        ),
                        "RANGE_MULTIPLE": _ratio_value(
                            candle,
                            "range_vs_sequence_median",
                        ),
                        "CLOSE_LOCATION": _ratio_value(
                            candle,
                            "close_location_in_range",
                        ),
                    },
                }
            )
        result = detector.replay_retained_history(observations)
        pair_dna.record_concept_drift_state(
            symbol=symbol,
            timeframe=timeframe,
            detector_state=detector.persistence_snapshot(),
        )
    except (ConceptDriftValidationError, PairDNAValidationError) as exc:
        pending = _pending_research_contract(
            CONCEPT_DRIFT_STUDY_SCHEMA_VERSION,
            symbol=symbol,
            timeframe=timeframe,
            reason=str(exc),
        )
        regime = deepcopy(pending)
        regime["status"] = "REGIME_PARTITION_UNPROVEN"
        return pending, regime
    metrics = deepcopy(_mapping(result.get("metrics")))
    baseline_ids = metrics.pop("baseline_closed_candle_ids", [])
    recent_ids = metrics.pop("recent_closed_candle_ids", [])
    partitions = [
        {
            key: deepcopy(row.get(key))
            for key in (
                "regime_partition_id",
                "ordinal",
                "status",
                "start_order_index",
                "end_order_index",
                "created_by",
                "drift_evidence_digest",
            )
        }
        for row in _rows(result.get("partitions"))
    ]
    compact = {
        key: deepcopy(result.get(key))
        for key in (
            "schema_version",
            "status",
            "stream",
            "current_regime_partition_id",
            "partition_count",
            "buffered_closed_candles",
            "required_for_comparison",
            "study_only",
            "observation_only",
            "causal",
            "predicts_direction",
            "execution_authority",
            "grants_entry_permission",
            "grants_execution_permission",
        )
    }
    compact.update(
        {
            "partitions": partitions,
            "metrics": metrics,
            "private_identity_audit": {
                "baseline_identity_count": len(cast(list[object], baseline_ids)),
                "recent_identity_count": len(cast(list[object], recent_ids)),
                "raw_candle_identities_published": False,
            },
            "window_policy": {
                "adaptive": False,
                "window_size": detector.window_size,
                "fixed_sequence_horizon": False,
                "configuration_persisted_per_pair": True,
                "retained_history_replay_idempotent": True,
            },
        }
    )
    current_partition = partitions[-1] if partitions else {}
    regime = {
        "schema_version": CONCEPT_DRIFT_STUDY_SCHEMA_VERSION,
        "status": (
            "ACTIVE" if current_partition else "REGIME_PARTITION_UNPROVEN"
        ),
        "symbol": symbol,
        "timeframe": timeframe,
        "coordinate_space": coordinate_space,
        "order_domain": order_domain,
        "current_partition": deepcopy(current_partition),
        "partition_count": len(partitions),
        "created_from_concept_drift": True,
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "predicts_direction": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }
    compact["regime_partition"] = deepcopy(regime)
    return compact, regime


def _normalized_cross_pair_series(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    order_domain: str,
) -> list[dict[str, Any]]:
    if order_domain != "CLOSED_TIMESTAMP_V1":
        return []
    pair_id = f"{symbol}|{timeframe}"
    rows: list[dict[str, Any]] = []
    for index in range(1, len(candles)):
        previous_ohlc = _mapping(candles[index - 1].get("ohlc"))
        current_ohlc = _mapping(candles[index].get("ohlc"))
        timestamp = _timestamp_seconds(candles[index].get("timestamp"))
        if timestamp is None:
            return []
        try:
            previous_high = float(cast(Any, previous_ohlc.get("high")))
            previous_low = float(cast(Any, previous_ohlc.get("low")))
            previous_close = float(cast(Any, previous_ohlc.get("close")))
            current_close = float(cast(Any, current_ohlc.get("close")))
        except (TypeError, ValueError):
            return []
        scale = previous_high - previous_low
        if not all(
            math.isfinite(value)
            for value in (
                previous_high,
                previous_low,
                previous_close,
                current_close,
            )
        ) or scale <= 1e-12:
            return []
        rows.append(
            {
                "pair_id": pair_id,
                "candle_id": _evidence_identity(candles[index], index),
                "closed_timestamp": timestamp,
                "is_closed": True,
                "coordinate_space": "NORMALIZED_RETURN",
                "order_domain": "SYNCHRONIZED_CLOSED_TIMESTAMP_V1",
                "value": round((current_close - previous_close) / scale, 10),
            }
        )
    return rows[-256:]


def _claim_proof_rows(
    candles: Sequence[Mapping[str, Any]],
    *,
    coordinate_space: str,
    order_domain: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        timestamp = _timestamp_seconds(candle.get("timestamp"))
        if timestamp is None:
            return []
        rows.append(
            {
                "candle_id": _evidence_identity(candle, index),
                "order_index": int(round(timestamp * 1_000_000)),
                "closed_timestamp": timestamp,
                "coordinate_space": coordinate_space,
                "order_domain": order_domain,
                "is_closed": True,
            }
        )
    return rows


def _merge_cross_pair_proof_evidence(
    proof_by_digest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Bind every tested peer study into one strict ordered proof stream.

    Each peer study can cover the same closed timestamp with a different
    pair-composite identity.  A proof certificate requires one strictly
    increasing row per order index, so identities sharing an index are folded
    into a deterministic digest rather than dropping all but the first peer.
    """

    digests = sorted(
        str(raw_digest)
        for raw_digest, value in proof_by_digest.items()
        if str(raw_digest) and _rows(value)
    )
    by_order: dict[int, dict[str, Any]] = {}
    identities_by_order: dict[int, list[str]] = {}
    for digest in digests:
        for row in _rows(proof_by_digest.get(digest)):
            order_index = _integer(row.get("order_index"), -1)
            if order_index < 0:
                continue
            canonical = {
                "order_index": order_index,
                "closed_timestamp": deepcopy(row.get("closed_timestamp")),
                "coordinate_space": str(row.get("coordinate_space") or ""),
                "order_domain": str(row.get("order_domain") or ""),
                "is_closed": row.get("is_closed") is True,
            }
            previous = by_order.get(order_index)
            if previous is not None and previous != canonical:
                raise CrossPairCoordinatorValidationError(
                    "cross-pair proof rows disagree at a shared order index"
                )
            by_order[order_index] = canonical
            identities_by_order.setdefault(order_index, []).append(
                f"{digest}:{str(row.get('candle_id') or '')}"
            )
    merged: list[dict[str, Any]] = []
    for order_index in sorted(by_order):
        row = deepcopy(by_order[order_index])
        identity_material = "|".join(sorted(set(identities_by_order[order_index])))
        row["candle_id"] = hashlib.sha256(
            identity_material.encode("utf-8")
        ).hexdigest()[:32]
        merged.append(row)
    return merged, digests


def _study_claim_proofs(
    *,
    advanced: Mapping[str, Mapping[str, Any]],
    ontology: Mapping[str, Any],
    concept_drift: Mapping[str, Any],
    regime_partition: Mapping[str, Any],
    cross_pair: Mapping[str, Any],
    proof_rows: Sequence[Mapping[str, Any]],
    coordinate_space: str,
    order_domain: str,
    cross_pair_proof: Sequence[Mapping[str, Any]],
    cross_pair_evidence_digests: Sequence[str] = (),
    material_studies: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    certificates: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []

    def issue(
        claim_key: str,
        claim_type: str,
        study: Mapping[str, Any],
        payload: Mapping[str, Any],
        algorithm_id: str,
        *,
        evidence: Sequence[Mapping[str, Any]] = proof_rows,
        evidence_coordinate: str = coordinate_space,
        evidence_order: str = order_domain,
        additional_inputs: Mapping[str, Any] | None = None,
    ) -> None:
        if not evidence:
            coverage.append(
                {
                    "claim_key": claim_key,
                    "status": "INSUFFICIENT_CLOSED_TIMESTAMP_EVIDENCE",
                    "certificate_id": "",
                }
            )
            return
        published_study_hash = canonical_public_study_hash_v3(study)
        inputs = {"published_study_hash": published_study_hash}
        if additional_inputs:
            inputs.update(deepcopy(dict(additional_inputs)))
        try:
            certificate = issue_study_claim_certificate_v3(
                claim_type=claim_type,
                claim_payload=dict(payload),
                closed_candles=evidence,
                coordinate_space=evidence_coordinate,
                order_domain=evidence_order,
                inputs=inputs,
                derivation={
                    "algorithm_id": algorithm_id,
                    "algorithm_version": "3.0.0",
                    "published_contract_hash_binding": True,
                },
            )
        except StudyClaimProofValidationError as exc:
            coverage.append(
                {
                    "claim_key": claim_key,
                    "status": "PROOF_REJECTED",
                    "reason": str(exc)[:240],
                    "certificate_id": "",
                }
            )
            return
        certificates.append(certificate)
        coverage.append(
            {
                "claim_key": claim_key,
                "status": "COVERED",
                "certificate_id": certificate["certificate_id"],
                "published_study_hash": published_study_hash,
            }
        )

    motif = _mapping(advanced.get("motif_lattice"))
    if motif.get("status") == "STUDIED":
        issue(
            "motif_lattice",
            "MOTIF_COMPOSITION",
            motif,
            {
                "history_id": motif.get("history_id"),
                "depth": motif.get("depth"),
                "summary": motif.get("summary"),
            },
            "PG_HIERARCHICAL_MOTIF_LATTICE_V3",
        )
    survival = _mapping(advanced.get("survival_network"))
    if survival.get("status") == "STUDIED":
        issue(
            "survival_network",
            "TIME_TO_EVENT",
            survival,
            {
                "history_count": survival.get("history_count"),
                "derived_observation_count": survival.get(
                    "derived_observation_count"
                ),
                "curve_count": len(_rows(survival.get("curves"))),
                "max_horizon_closed_candles": survival.get(
                    "max_horizon_closed_candles"
                ),
            },
            "PG_KAPLAN_MEIER_TIME_TO_EVENT_V3",
        )
    path = _mapping(advanced.get("path_reconstruction"))
    if path.get("status") == "RECONSTRUCTED":
        issue(
            "path_reconstruction",
            "PATH_RECONSTRUCTION",
            path,
            {
                "path_id": path.get("path_id"),
                "anchor_index": path.get("anchor_index"),
                "end_index": path.get("end_index"),
                "path_summary": path.get("path_summary"),
            },
            "PG_NORMALIZED_HISTORICAL_PATH_V3",
        )
    if concept_drift.get("status") not in {
        "INSUFFICIENT_PROVEN_HISTORY",
        None,
    }:
        issue(
            "concept_drift",
            "CONCEPT_DRIFT",
            concept_drift,
            {
                "status": concept_drift.get("status"),
                "current_regime_partition_id": concept_drift.get(
                    "current_regime_partition_id"
                ),
                "partition_count": concept_drift.get("partition_count"),
                "metrics_digest": _mapping(concept_drift.get("metrics")).get(
                    "evidence_digest"
                ),
            },
            "PG_ONLINE_CONCEPT_DRIFT_V3",
        )
    if regime_partition.get("status") == "ACTIVE":
        issue(
            "regime_partition",
            "REGIME_PARTITION",
            regime_partition,
            {
                "current_partition": regime_partition.get("current_partition"),
                "partition_count": regime_partition.get("partition_count"),
            },
            "PG_CONCEPT_DRIFT_REGIME_PARTITION_V3",
        )
    public_features = _rows(ontology.get("public_features"))
    if ontology.get("status") not in {"INSUFFICIENT_PROVEN_HISTORY", None}:
        issue(
            "adaptive_feature_ontology",
            "STUDY_ASSOCIATION",
            ontology,
            {
                "status": ontology.get("status"),
                "ontology_version": ontology.get("ontology_version"),
                "promoted_feature_count": ontology.get(
                    "promoted_feature_count"
                ),
                "shadow_audit": ontology.get("shadow_audit"),
            },
            "PG_ADAPTIVE_FEATURE_ONTOLOGY_PUBLICATION_V3",
        )
    for feature in public_features:
        feature_id = str(feature.get("feature_id") or "UNKNOWN")
        issue(
            f"feature_promotion:{feature_id}",
            "FEATURE_PROMOTION",
            ontology,
            {
                "feature_id": feature_id,
                "revision": feature.get("revision"),
                "ontology_version": feature.get("ontology_version"),
            },
            "PG_ADAPTIVE_FEATURE_PROMOTION_GATE_V3",
        )
    if int(cross_pair.get("tested_pair_count", 0) or 0) > 0:
        issue(
            "cross_pair_association",
            "CROSS_PAIR_ASSOCIATION",
            cross_pair,
            {
                "status": cross_pair.get("status"),
                "graph_digest": cross_pair.get("graph_digest"),
                "published_edge_count": cross_pair.get("published_edge_count"),
                "edges": cross_pair.get("edges"),
            },
            "PG_CROSS_PAIR_ASSOCIATION_V3",
            evidence=cross_pair_proof,
            evidence_coordinate="NORMALIZED_RETURN",
            evidence_order="SYNCHRONIZED_CLOSED_TIMESTAMP_V1",
            additional_inputs={
                "tested_pair_evidence_digests": list(
                    cross_pair_evidence_digests
                ),
                "all_tested_peers_bound": True,
            },
        )
    else:
        coverage.append(
            {
                "claim_key": "cross_pair_association",
                "status": "NOT_PUBLISHED_INSUFFICIENT_SYNCHRONIZED_EVIDENCE",
                "certificate_id": "",
            }
        )
    material_algorithms = {
        "regression": (
            "BEHAVIORAL_SUMMARY",
            "PG_REGRESSION_STUDY_PUBLICATION_V3",
        ),
        "candle_intelligence": (
            "BEHAVIORAL_SUMMARY",
            "PG_CANDLE_INTELLIGENCE_PUBLICATION_V3",
        ),
        "behavior": (
            "BEHAVIORAL_SUMMARY",
            "PG_BEHAVIORAL_SEQUENCE_PUBLICATION_V3",
        ),
        "pair_dna": (
            "STUDY_ASSOCIATION",
            "PG_PAIR_DNA_PUBLICATION_V3",
        ),
        "object_relationship_graph": (
            "STUDY_ASSOCIATION",
            "PG_OBJECT_RELATIONSHIP_GRAPH_PUBLICATION_V3",
        ),
        "historical_similarity": (
            "HISTORICAL_SIMILARITY",
            "PG_HISTORICAL_SIMILARITY_PUBLICATION_V3",
        ),
        "outcome_maturation": (
            "STUDY_ASSOCIATION",
            "PG_OUTCOME_MATURATION_PUBLICATION_V3",
        ),
        "directional_read": (
            "BEHAVIORAL_SUMMARY",
            "PG_DIRECTIONAL_READ_PUBLICATION_V3",
        ),
    }
    for claim_key, (claim_type, algorithm_id) in material_algorithms.items():
        study = _mapping(_mapping(material_studies).get(claim_key))
        if not study:
            coverage.append(
                {
                    "claim_key": claim_key,
                    "status": "NOT_PUBLISHED",
                    "certificate_id": "",
                }
            )
            continue
        issue(
            claim_key,
            claim_type,
            study,
            {
                "status": study.get("status"),
                "schema_version": study.get("schema_version"),
                "published_study_hash": canonical_public_study_hash_v3(study),
            },
            algorithm_id,
            additional_inputs={
                "source_authentication_status": "NOT_PROVIDED",
                "certificate_scope": "PUBLIC_DERIVATION_INTEGRITY",
            },
        )
    covered = sum(row.get("status") == "COVERED" for row in coverage)
    return {
        "schema_version": STUDY_CLAIM_PROOF_SCHEMA_VERSION,
        "status": "COMPLETE" if covered == len(coverage) else "PARTIAL",
        "certificate_count": len(certificates),
        "required_claim_count": len(coverage),
        "covered_claim_count": covered,
        "certificates": certificates,
        "coverage": coverage,
        "contract": {
            "binds_published_study_hash": True,
            "binds_ordered_closed_candles": True,
            "proves_integrity_not_causation": True,
            "unproven_claims_are_disclosed": True,
        },
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


def _attach_claim_proof_references(
    studies: Mapping[str, dict[str, Any]],
    claim_proofs: Mapping[str, Any],
) -> None:
    coverage = {
        str(row.get("claim_key") or ""): row
        for row in _rows(claim_proofs.get("coverage"))
        if row.get("status") == "COVERED"
    }
    for name, study in studies.items():
        row = _mapping(coverage.get(name))
        certificate_id = str(row.get("certificate_id") or "")
        if certificate_id:
            study["claim_proof_id"] = certificate_id
            study["claim_bound_study_hash"] = str(
                row.get("published_study_hash") or ""
            )
            study["claim_bound_projection"] = (
                PUBLIC_STUDY_CANONICAL_PROJECTION_VERSION
            )


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
        "recent_candles": [
            _compact_candle(row) for row in candles[-_MAX_PUBLIC_RECENT_CANDLES:]
        ],
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
    segment_counts = _top_counts(behavior.get("segment_counts"), limit=16)
    transition_counts = _top_counts(
        behavior.get("transition_counts"), limit=16
    )
    all_segment_counts = _mapping(behavior.get("segment_counts"))

    def state_segment_support(state: str) -> int:
        return sum(
            int(count or 0)
            for key, count in all_segment_counts.items()
            if str(key).upper().rsplit("|", maxsplit=1)[-1] == state
        )

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
        "outcome_label_count": int(
            profile.get("outcome_label_count", 0) or 0
        ),
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
            "segment_counts": segment_counts,
            "transition_counts": transition_counts,
            "major_trend_counts": _top_counts(behavior.get("major_trend_counts")),
            "inner_trend_counts": _top_counts(behavior.get("inner_trend_counts")),
            "transition_probabilities": deepcopy(_mapping(behavior.get("transition_probabilities"))),
            "segment_averages": deepcopy(_mapping(behavior.get("segment_averages"))),
            "timing_support": {
                "completed_segments": sum(
                    int(count or 0)
                    for count in all_segment_counts.values()
                ),
                "up_swing_segments": state_segment_support("UP_SWING"),
                "down_swing_segments": state_segment_support("DOWN_SWING"),
                "rest_segments": state_segment_support("REST"),
                "transition_boundaries": sum(
                    int(count or 0)
                    for count in _mapping(
                        behavior.get("transition_counts")
                    ).values()
                ),
                "duration_basis": "DECLARED_TIMEFRAME_CANDLE_COUNTS",
                "exact_wall_clock_proven": False,
            },
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
        self.cross_pair = CrossPairStudyCoordinatorV3(
            self.root_dir / "cross_pair_coordinator_v3.json"
        )
        self.path_clock_liquidity = PathClockLiquiditySideStoreV3(
            self.root_dir / "path_clock_liquidity_v3"
        )
        self._pending_path = self.root_dir / "pending_outcomes_v3.json"
        self._ontology_path = self.root_dir / "adaptive_feature_ontology_v3.json"
        self._lock = threading.RLock()
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._result_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._pair_observation_seen: set[tuple[str, str]] = set()

    def resolver_order_state(
        self,
        symbol: object,
        timeframe: object,
    ) -> dict[str, Any]:
        """Expose Pair DNA's read-only resolver restart floor."""

        return self.pair_dna.get_resolver_order_state(symbol, timeframe)

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

    def _load_pair_ontology(
        self,
        *,
        symbol: str,
        timeframe: str,
    ) -> AdaptiveFeatureOntologyV3 | None:
        with exclusive_store_lock(self._ontology_path, timeout_seconds=5.0):
            state = read_json_document(self._ontology_path)
        if state is None:
            return None
        if (
            state.get("schema_version") != _ONTOLOGY_STORE_SCHEMA_VERSION
            or state.get("study_only") is not True
            or state.get("execution_authority") is not False
        ):
            raise AdaptiveFeatureOntologyValidationError(
                "adaptive ontology store is not a V3 study document"
            )
        entries = _mapping(state.get("entries"))
        if len(entries) > _MAX_ONTOLOGY_PAIRS:
            raise AdaptiveFeatureOntologyValidationError(
                "adaptive ontology pair capacity is exceeded"
            )
        snapshot = _mapping(entries.get(self._pending_key((symbol, timeframe))))
        if not snapshot:
            return None
        snapshot.pop("updated_ordinal", None)
        return AdaptiveFeatureOntologyV3.from_snapshot(
            snapshot,
            symbol=symbol,
            timeframe=timeframe,
        )

    def _persist_pair_ontology(
        self,
        snapshot: Mapping[str, Any],
        *,
        symbol: str,
        timeframe: str,
    ) -> None:
        with exclusive_store_lock(self._ontology_path, timeout_seconds=5.0):
            stored_state = read_json_document(self._ontology_path)
            state: dict[str, Any]
            if stored_state is None:
                state = {
                    "schema_version": _ONTOLOGY_STORE_SCHEMA_VERSION,
                    "study_only": True,
                    "execution_authority": False,
                    "next_ordinal": 1,
                    "entries": {},
                }
            else:
                state = stored_state
            if (
                state.get("schema_version") != _ONTOLOGY_STORE_SCHEMA_VERSION
                or state.get("study_only") is not True
                or state.get("execution_authority") is not False
            ):
                raise AdaptiveFeatureOntologyValidationError(
                    "adaptive ontology store is not a V3 study document"
                )
            entries = _mapping(state.get("entries"))
            key = self._pending_key((symbol, timeframe))
            if key not in entries and len(entries) >= _MAX_ONTOLOGY_PAIRS:
                raise AdaptiveFeatureOntologyValidationError(
                    "adaptive ontology pair capacity reached"
                )
            ordinal = max(1, _integer(state.get("next_ordinal"), 1))
            row = deepcopy(dict(snapshot))
            row["updated_ordinal"] = ordinal
            entries[key] = row
            state["entries"] = entries
            state["next_ordinal"] = ordinal + 1
            write_json_atomic(self._ontology_path, state)

    def _continuous_history_studies(
        self,
        candle_study: Mapping[str, Any],
        behavior_study: Mapping[str, Any],
        *,
        symbol: str,
        timeframe: str,
        regime: object,
        timeframe_seconds: int,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Extend broker-price history from the durable pair ledger when safe."""

        current_candles = _rows(candle_study.get("candles"))
        coordinate_spaces = {
            str(row.get("coordinate_space") or "").strip().upper()
            for row in current_candles
        }
        if coordinate_spaces != {"PRICE"}:
            return (
                dict(candle_study),
                dict(behavior_study),
                "CURRENT_EXACT_COORDINATE_FRAME",
            )
        ledger = self.candle_ledger.recent_candles(
            symbol,
            timeframe,
            limit=MAX_CLOSED_HISTORY_CANDLES,
        )
        merged_rows = _continuous_price_rows(
            candle_study,
            _rows(ledger.get("records")),
            timeframe_seconds=timeframe_seconds,
        )
        if len(merged_rows) <= len(current_candles):
            return (
                dict(candle_study),
                dict(behavior_study),
                "CURRENT_RETAINED_CLOSED_HISTORY",
            )
        extended_candles = analyze_candle_sequence_v3(
            merged_rows,
            regime=str(regime or "UNKNOWN"),
            require_closed=True,
            max_candles=MAX_CLOSED_HISTORY_CANDLES,
        )
        count = int(extended_candles.get("studied_count", 0) or 0)
        adaptive_inner_window = min(128, max(2, int(round(math.sqrt(count)))))
        extended_behavior = measure_market_behavior_v3(
            extended_candles,
            timeframe_seconds=timeframe_seconds,
            max_candles=MAX_CLOSED_HISTORY_CANDLES,
            inner_window=adaptive_inner_window,
        )
        return (
            extended_candles,
            extended_behavior,
            "CURRENT_HISTORY_PLUS_RESTART_SAFE_PAIR_LEDGER",
        )

    def _continuous_research_studies(
        self,
        candle_study: Mapping[str, Any],
        advanced_studies: dict[str, dict[str, Any]],
        *,
        symbol: str,
        timeframe: str,
        timeframe_seconds: int,
        material_studies: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        candles = _rows(candle_study.get("candles"))
        order_domain = _advanced_order_domain(candles)
        coordinate_spaces = {
            str(row.get("coordinate_space") or "").strip().upper()
            for row in candles
        }
        if not order_domain or len(coordinate_spaces) != 1:
            pending = _pending_research_studies(
                symbol=symbol,
                timeframe=timeframe,
                reason="Exact stable order and one coordinate space are required.",
            )
            return pending
        coordinate_space = next(iter(coordinate_spaces))
        try:
            registry = self._load_pair_ontology(
                symbol=symbol,
                timeframe=timeframe,
            )
            ontology, ontology_snapshot = _adaptive_feature_ontology_study(
                candles,
                symbol=symbol,
                timeframe=timeframe,
                coordinate_space=coordinate_space,
                order_domain=order_domain,
                ontology=registry,
            )
            if ontology_snapshot is not None:
                self._persist_pair_ontology(
                    ontology_snapshot,
                    symbol=symbol,
                    timeframe=timeframe,
                )
        except AdaptiveFeatureOntologyValidationError as exc:
            ontology = _pending_research_contract(
                ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
                symbol=symbol,
                timeframe=timeframe,
                reason=str(exc),
            )
        concept_drift, regime_partition = _concept_drift_study(
            candles,
            symbol=symbol,
            timeframe=timeframe,
            coordinate_space=coordinate_space,
            order_domain=order_domain,
            pair_dna=self.pair_dna,
        )
        normalized_series = _normalized_cross_pair_series(
            candles,
            symbol=symbol,
            timeframe=timeframe,
            order_domain=order_domain,
        )
        cross_pair_proof: list[dict[str, Any]] = []
        cross_pair_evidence_digests: list[str] = []
        if normalized_series:
            try:
                cross_pair = self.cross_pair.update_pair(
                    symbol=symbol,
                    timeframe=timeframe,
                    timeframe_seconds=timeframe_seconds,
                    series=normalized_series,
                )
            except CrossPairCoordinatorValidationError as exc:
                cross_pair = _pending_research_contract(
                    CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
                    symbol=symbol,
                    timeframe=timeframe,
                    reason=str(exc),
                )
            proof_by_digest = _mapping(
                cross_pair.pop("_proof_evidence_by_digest", {})
            )
            if proof_by_digest:
                try:
                    (
                        cross_pair_proof,
                        cross_pair_evidence_digests,
                    ) = _merge_cross_pair_proof_evidence(proof_by_digest)
                except CrossPairCoordinatorValidationError as exc:
                    cross_pair = _pending_research_contract(
                        CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
                        symbol=symbol,
                        timeframe=timeframe,
                        reason=str(exc),
                    )
                    cross_pair_proof = []
                    cross_pair_evidence_digests = []
            cross_pair["proof_evidence_digests"] = list(
                cross_pair_evidence_digests
            )
            cross_pair["all_tested_peer_evidence_bound"] = bool(
                cross_pair_evidence_digests
            )
        else:
            cross_pair = _pending_research_contract(
                CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
                symbol=symbol,
                timeframe=timeframe,
                reason=(
                    "Cross-pair study requires exact source closed timestamps and "
                    "a distinct synchronized compatible pair."
                ),
            )
            cross_pair["status"] = "INSUFFICIENT_SYNCHRONIZED_PAIR"
            cross_pair["tested_pair_count"] = 0
            cross_pair["edges"] = []
            cross_pair["contract"] = {
                "requires_exact_shared_closed_timestamps": True,
                "fabricates_missing_pair_evidence": False,
            }
        proof_rows = _claim_proof_rows(
            candles,
            coordinate_space=coordinate_space,
            order_domain=order_domain,
        )
        claim_proofs = _study_claim_proofs(
            advanced=advanced_studies,
            ontology=ontology,
            concept_drift=concept_drift,
            regime_partition=regime_partition,
            cross_pair=cross_pair,
            proof_rows=proof_rows,
            coordinate_space=coordinate_space,
            order_domain=order_domain,
            cross_pair_proof=cross_pair_proof,
            cross_pair_evidence_digests=cross_pair_evidence_digests,
            material_studies=material_studies,
        )
        research = {
            "adaptive_feature_ontology": ontology,
            "concept_drift": concept_drift,
            "regime_partition": regime_partition,
            "cross_pair_association": cross_pair,
            "claim_proofs": claim_proofs,
        }
        proof_bound_studies: dict[str, dict[str, Any]] = {
            **advanced_studies,
            "adaptive_feature_ontology": ontology,
            "concept_drift": concept_drift,
            "regime_partition": regime_partition,
            "cross_pair_association": cross_pair,
        }
        proof_bound_studies.update(
            {
                key: cast(dict[str, Any], value)
                for key, value in _mapping(material_studies).items()
                if isinstance(value, dict)
            }
        )
        _attach_claim_proof_references(proof_bound_studies, claim_proofs)
        return research

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

    def _path_clock_study(
        self,
        *,
        symbol: str,
        timeframe: str,
        closed_candle_key: str,
        closed_candle_sequence: object,
        candles: Sequence[Mapping[str, Any]],
        timeframe_seconds: int,
        studied_direction: object,
        contract_duration_seconds: object | None,
        object_relationship_graph: Mapping[str, Any],
        closed_candle_time_proof: Mapping[str, Any] | None = None,
        regime: object = "UNKNOWN",
        directional_confidence: object = 0.0,
        current_behavior: Mapping[str, Any] | None = None,
        pair_profile: Mapping[str, Any] | None = None,
        advanced_studies: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance the pair timing field without affecting trade permission."""

        jpclf_candles, jpclf_time_proof = _jpclf_resolver_bound_rows_v3(
            candles,
            closed_candle_time_proof,
        )
        advanced = _mapping(advanced_studies)
        path_reconstruction = _mapping(advanced.get("path_reconstruction"))
        forecast_context = {
            "candidate_direction": studied_direction,
            "directional_confidence": directional_confidence,
            "current_regime": str(regime or "UNKNOWN"),
            "current_behavior": deepcopy(_mapping(current_behavior)),
            "pair_profile": deepcopy(_mapping(pair_profile)),
            "motif_lattice": deepcopy(_mapping(advanced.get("motif_lattice"))),
            "survival_network": deepcopy(
                _mapping(advanced.get("survival_network"))
            ),
            "motif_trajectory_library": deepcopy(
                _mapping(path_reconstruction.get("trajectory_library"))
            ),
            "lineage": {
                "symbol": symbol,
                "timeframe": timeframe,
                "closed_candle_key": closed_candle_key,
                "closed_candle_sequence": closed_candle_sequence,
            },
        }
        try:
            return self.path_clock_liquidity.observe_closed_candle(
                symbol=symbol,
                timeframe=timeframe,
                closed_candle_key=closed_candle_key,
                closed_candle_sequence=closed_candle_sequence,
                closed_candle_time_proof=jpclf_time_proof,
                candles=jpclf_candles,
                source_cadence_seconds=timeframe_seconds,
                studied_direction=studied_direction,
                contract_duration_seconds=contract_duration_seconds,
                liquidity_state=_jpclf_liquidity_state(
                    jpclf_candles,
                    object_relationship_graph,
                ),
                forecast_context=forecast_context,
            )
        except PathClockLiquidityStoreValidationError as exc:
            pending = pending_path_clock_liquidity_v3(
                "JPCLF failed closed because exact contiguous timing evidence "
                f"could not be proven: {exc}",
                contract_duration_seconds=contract_duration_seconds,
                candidate_direction=studied_direction,
                source_cadence_seconds=timeframe_seconds,
                forecast_context=forecast_context,
                symbol=symbol,
                timeframe=timeframe,
                closed_candle_key=closed_candle_key,
                closed_candle_sequence=closed_candle_sequence,
            )
            pending["status"] = "CENSORED_INVALID_TIMING_EVIDENCE"
            return pending

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
        contract_duration_seconds: object | None = None,
        closed_candle_time_proof: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical_symbol = str(symbol or "").strip().upper()
        canonical_timeframe = str(timeframe or "").strip().upper()
        close_key = str(closed_candle_key or "").strip()
        if not canonical_symbol or not canonical_timeframe or not close_key:
            result = pending_market_study_v3(
                "Pair, timeframe, and closed-candle identity must all be confirmed.",
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
            )
            result.update(
                _pending_advanced_studies(
                    symbol=canonical_symbol,
                    timeframe=canonical_timeframe,
                    reason="Pair, timeframe, and closed-candle identity are unproven.",
                    closed_candle_count=0,
                )
            )
            result.update(
                _pending_research_studies(
                    symbol=canonical_symbol,
                    timeframe=canonical_timeframe,
                    reason="Pair, timeframe, and closed-candle identity are unproven.",
                )
            )
            path_clock_pending = pending_path_clock_liquidity_v3(
                "Pair, timeframe, and closed-candle identity are unproven.",
                contract_duration_seconds=contract_duration_seconds,
            )
            result["path_clock_liquidity_v3"] = path_clock_pending
            result["path_clock_liquidity"] = deepcopy(path_clock_pending)
            return result
        timing_proof_cache_token = _closed_candle_time_proof_cache_token(
            closed_candle_time_proof
        )
        cache_key = (
            canonical_symbol,
            canonical_timeframe,
            close_key,
            timing_proof_cache_token,
        )
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
                result.update(
                    _pending_advanced_studies(
                        symbol=canonical_symbol,
                        timeframe=canonical_timeframe,
                        reason=(
                            "At least four proven closed candles are required for "
                            "continuous advanced studies."
                        ),
                        closed_candle_count=int(
                            candle_study.get("studied_count", 0) or 0
                        ),
                    )
                )
                result.update(
                    _pending_research_studies(
                        symbol=canonical_symbol,
                        timeframe=canonical_timeframe,
                        reason=(
                            "At least four proven closed candles are required for "
                            "continuous research studies."
                        ),
                    )
                )
                timeframe_seconds = max(
                    1,
                    int(_mapping(regression).get("timeframe_seconds", 300) or 300),
                )
                studied_rows = _rows(candle_study.get("candles"))
                path_clock_pending = self._path_clock_study(
                    symbol=canonical_symbol,
                    timeframe=canonical_timeframe,
                    closed_candle_key=close_key,
                    closed_candle_sequence=closed_candle_sequence,
                    candles=studied_rows,
                    timeframe_seconds=timeframe_seconds,
                    studied_direction="HOLD",
                    contract_duration_seconds=contract_duration_seconds,
                    closed_candle_time_proof=closed_candle_time_proof,
                    object_relationship_graph={},
                    regime=regime,
                )
                result["path_clock_liquidity_v3"] = path_clock_pending
                result["path_clock_liquidity"] = deepcopy(path_clock_pending)
                self._result_cache[cache_key] = result
                return deepcopy(result)

            timeframe_seconds = max(
                1,
                int(_mapping(regression).get("timeframe_seconds", 300) or 300),
            )
            behavior_study = measure_market_behavior_v3(
                candle_study,
                timeframe_seconds=timeframe_seconds,
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
            advanced_candles, advanced_behavior, advanced_history_source = (
                self._continuous_history_studies(
                    candle_study,
                    behavior_study,
                    symbol=canonical_symbol,
                    timeframe=canonical_timeframe,
                    regime=regime,
                    timeframe_seconds=timeframe_seconds,
                )
            )
            current_close = float(_mapping(latest_candle.get("ohlc")).get("close", 0.0) or 0.0)
            pair_key = (canonical_symbol, canonical_timeframe)
            allow_tracker_sequence_rebase = (
                pair_key not in self._pair_observation_seen
            )
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
                    self.pair_dna.record_outcome(
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
                        allow_tracker_sequence_rebase=(
                            allow_tracker_sequence_rebase
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

            # Advanced object-conditioned survival must see only evidence that
            # predates the current frame.  The current observation is written
            # immediately afterwards and is reloaded separately for JPCLF and
            # the public Pair DNA support contract.
            advanced_pair_profile = _mapping(
                self.pair_dna.get_profile(
                    canonical_symbol,
                    canonical_timeframe,
                ).get("profile")
            )
            advanced_studies = _continuous_advanced_studies(
                advanced_candles,
                advanced_behavior,
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                history_source=advanced_history_source,
                pair_profile=advanced_pair_profile,
            )
            self.pair_dna.record_observation(
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                candle_study=candle_study,
                behavior_study=behavior_study,
                sequence_id=sequence_id,
                observed_at=observed_at,
                objects=object_rows,
                allow_tracker_sequence_rebase=(
                    allow_tracker_sequence_rebase
                ),
            )
            self._pair_observation_seen.add(pair_key)
            profile_result = self.pair_dna.get_profile(
                canonical_symbol,
                canonical_timeframe,
            )
            historical_pair_profile = _mapping(profile_result.get("profile"))
            similarity = self.historical.search(
                fingerprint,
                top_k=8,
                minimum_similarity=0.55,
                same_pair=True,
                same_timeframe=True,
                min_outcome_support=3,
            )
            self.historical.add(fingerprint)
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
            path_clock_liquidity = self._path_clock_study(
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                closed_candle_key=close_key,
                closed_candle_sequence=closed_candle_sequence,
                candles=studied_candles,
                timeframe_seconds=timeframe_seconds,
                studied_direction=directional.get("side"),
                contract_duration_seconds=contract_duration_seconds,
                closed_candle_time_proof=closed_candle_time_proof,
                object_relationship_graph=object_relationship_graph,
                regime=regime,
                directional_confidence=directional.get("confidence", 0.0),
                current_behavior=advanced_behavior,
                pair_profile=historical_pair_profile,
                advanced_studies=advanced_studies,
            )
            material_studies = {
                "regression": regression_row,
                "candle_intelligence": _compact_candle_study(candle_study),
                "behavior": compact_behavior,
                "pair_dna": _compact_pair_profile(profile_result.get("profile")),
                "object_relationship_graph": object_relationship_graph,
                "historical_similarity": compact_similarity,
                "outcome_maturation": maturation,
                "directional_read": directional,
            }
            research_studies = self._continuous_research_studies(
                advanced_candles,
                advanced_studies,
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                timeframe_seconds=timeframe_seconds,
                material_studies=material_studies,
            )
            hidden_state_discovery = build_latent_state_discovery_v3(
                candles=_rows(advanced_candles.get("candles")),
                behavior=advanced_behavior,
                pair_profile=historical_pair_profile,
                advanced_studies=advanced_studies,
                research_studies=research_studies,
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                timeframe_seconds=timeframe_seconds,
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
                    "candle_intelligence": material_studies[
                        "candle_intelligence"
                    ],
                    "candle_ledger": _compact_candle_ledger(ledger_result),
                    "behavior": compact_behavior,
                    "pair_dna": material_studies["pair_dna"],
                    "object_relationship_graph": object_relationship_graph,
                    "historical_similarity": compact_similarity,
                    "outcome_maturation": maturation,
                    "directional_read": directional,
                    "path_clock_liquidity_v3": path_clock_liquidity,
                    "path_clock_liquidity": deepcopy(path_clock_liquidity),
                    "hidden_state_discovery_v3": hidden_state_discovery,
                    "intelligence_authority": "HIDDEN_STATE_DISCOVERY_V3",
                    **advanced_studies,
                    **research_studies,
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
