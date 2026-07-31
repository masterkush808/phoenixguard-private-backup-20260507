"""Restart-safe pair partitions for the V3 Joint Path-Clock Liquidity Field.

The mathematical field deliberately has no filesystem concerns.  This module
owns the durable, pair-scoped trajectory side store around it.  Raw normalized
paths remain here and are never copied into the monolithic Pair DNA JSON.

One active anchor is opened only for a contract whose original duration is at
least fifteen minutes.  Once admitted, that anchor continues to be observed
all the way to its expiry, including its final sub-fifteen-minute interval.
Only exact, contiguous, closed-candle timestamps are accepted; gaps censor an
anchor instead of inventing intermediate prices.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, cast

from phoenixguard.study._persistence_v3 import (
    exclusive_store_lock,
    read_json_document,
    write_json_atomic,
)
from phoenixguard.study.path_clock_liquidity_v3 import (
    JointPathClockLiquidityFieldV3,
    MAX_STUDIED_DURATION_SECONDS,
    MIN_ELIGIBLE_DURATION_SECONDS,
    PathClockLiquidityValidationError,
    build_hierarchical_forward_timing_forecast_v3,
    evaluate_path_clock_promotion_gate_v3,
    score_path_clock_replays_v3,
)


PATH_CLOCK_LIQUIDITY_SIDE_STORE_SCHEMA_VERSION = (
    "PG_PATH_CLOCK_LIQUIDITY_SIDE_STORE_V3"
)
PATH_CLOCK_LIQUIDITY_PUBLIC_SCHEMA_VERSION = (
    "PG_PATH_CLOCK_LIQUIDITY_PUBLIC_STUDY_V3"
)
PROVEN_CLOSED_CANDLE_TIME_SCHEMA_VERSION = "PG_PROVEN_CLOSED_CANDLE_TIME_V3"
_COORDINATE_SPACE = "NORMALIZED_MEDIAN_RANGE"
_ORDER_DOMAIN = "CLOSED_TIMESTAMP_V1"
_IDENTITY_PROOF_SOURCE = "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
_TIMESTAMP_SEMANTIC = "BAR_CLOSE"
_TIMESTAMP_SOURCES = frozenset(
    {
        "SOURCE_CLOSE_TIME",
        "SOURCE_OPEN_PLUS_TIMEFRAME",
        "RESOLVER_BOUND_BOUNDARY_GRID",
    }
)
_CLOCK_TOLERANCE_SECONDS = 1e-6
_MAX_ACTIVE_ANCHORS = 128
_MAX_TRAJECTORIES = 128
_MAX_FREEZES = 256
_MAX_OBSERVATION_IDENTITIES = 512
_MAX_REFERENCE_CANDLES = 4
_MAX_CENSORSHIP_REASONS = 16
_MAX_REPLAYS = 256
_MINIMUM_PROMOTION_REPLAYS = 32
_MIN_SUPPORTED_SURVIVAL_PROBABILITY = 0.65
_MAX_SUPPORTED_WORST_PULLBACK_AHEAD_PROBABILITY = 0.55
_SCENARIOS: tuple[tuple[float, float], ...] = (
    (0.5, 0.5),
    (0.5, 1.0),
    (1.0, 1.0),
    (1.0, 1.5),
)


class PathClockLiquidityStoreValidationError(ValueError):
    """Raised when side-store evidence cannot satisfy the V3 contract."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        return []
    return [
        dict(cast(Mapping[str, Any], row))
        for row in cast(Sequence[object], value)
        if isinstance(row, Mapping)
    ]


def _finite(value: object, *, field: str) -> float:
    if value is None or isinstance(value, bool):
        raise PathClockLiquidityStoreValidationError(f"{field} must be finite")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise PathClockLiquidityStoreValidationError(
            f"{field} must be finite"
        ) from exc
    if not math.isfinite(parsed):
        raise PathClockLiquidityStoreValidationError(f"{field} must be finite")
    return parsed


def _integer_value(value: object, default: int = 0) -> int:
    try:
        return int(float(cast(Any, value)))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _strict_nonnegative_integer(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PathClockLiquidityStoreValidationError(
            f"{field} must be a nonnegative integer"
        )
    return value


def _timestamp_seconds(value: object) -> float | None:
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
        return _timestamp_seconds(float(text))
    except ValueError:
        pass
    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
    return parsed_datetime.timestamp()


def _whole_second_timestamp(value: object, *, field: str) -> float:
    parsed = _timestamp_seconds(value)
    if parsed is None:
        raise PathClockLiquidityStoreValidationError(
            f"{field} must be an exact timestamp"
        )
    rounded = round(parsed)
    if abs(parsed - rounded) > _CLOCK_TOLERANCE_SECONDS:
        raise PathClockLiquidityStoreValidationError(
            f"{field} must resolve to an exact whole second"
        )
    return float(rounded)


def _validated_closed_candle_time_proof(
    value: Mapping[str, Any] | None,
    *,
    symbol: str,
    timeframe: str,
    closed_candle_key: str,
    closed_candle_sequence: int,
    source_cadence_seconds: int,
) -> dict[str, Any]:
    proof = _mapping(value)
    if proof.get("schema_version") != PROVEN_CLOSED_CANDLE_TIME_SCHEMA_VERSION:
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof must use PG_PROVEN_CLOSED_CANDLE_TIME_V3"
        )
    proof_symbol = " ".join(
        str(proof.get("symbol") or "").strip().upper().split()
    )
    proof_timeframe = " ".join(
        str(proof.get("timeframe") or "").strip().upper().split()
    )
    proof_key = str(proof.get("closed_candle_key") or "").strip()
    proof_sequence = _strict_nonnegative_integer(
        proof.get("closed_candle_sequence"),
        field="closed_candle_time_proof.closed_candle_sequence",
    )
    if (
        proof_symbol != symbol
        or proof_timeframe != timeframe
        or proof_key != closed_candle_key
        or proof_sequence != closed_candle_sequence
    ):
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof does not match the requested closed-candle event"
        )
    proof_cadence = _strict_nonnegative_integer(
        proof.get("source_cadence_seconds"),
        field="closed_candle_time_proof.source_cadence_seconds",
    )
    if proof_cadence != source_cadence_seconds:
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof source cadence does not match the study cadence"
        )
    bound_row_index = _strict_nonnegative_integer(
        proof.get("bound_row_index"),
        field="closed_candle_time_proof.bound_row_index",
    )
    transition_count = _strict_nonnegative_integer(
        proof.get("transition_count"),
        field="closed_candle_time_proof.transition_count",
    )
    if proof.get("timestamp_semantic") != _TIMESTAMP_SEMANTIC:
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof timestamp_semantic must be BAR_CLOSE"
        )
    timestamp_source = str(proof.get("timestamp_source") or "").strip().upper()
    if timestamp_source not in _TIMESTAMP_SOURCES:
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof timestamp_source is not admitted"
        )
    if proof.get("proof_source") != _IDENTITY_PROOF_SOURCE:
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof must be bound by the V3 identity resolver"
        )
    contiguous = proof.get("contiguous_from_previous")
    if not isinstance(contiguous, bool):
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof.contiguous_from_previous must be boolean"
        )
    if contiguous and transition_count != 1:
        raise PathClockLiquidityStoreValidationError(
            "a contiguous closed-candle time proof must represent one transition"
        )

    close_epoch = _whole_second_timestamp(
        proof.get("close_epoch_seconds"),
        field="closed_candle_time_proof.close_epoch_seconds",
    )
    observed_epoch = _timestamp_seconds(proof.get("observed_epoch_seconds"))
    if observed_epoch is None:
        raise PathClockLiquidityStoreValidationError(
            "closed_candle_time_proof.observed_epoch_seconds must be finite"
        )
    latency = _finite(
        proof.get("observation_latency_seconds"),
        field="closed_candle_time_proof.observation_latency_seconds",
    )
    expected_latency = observed_epoch - close_epoch
    tolerance = max(
        _CLOCK_TOLERANCE_SECONDS,
        abs(expected_latency) * 1e-9,
    )
    if expected_latency < -tolerance or latency < -tolerance:
        raise PathClockLiquidityStoreValidationError(
            "closed-candle observation cannot precede its proven close"
        )
    if abs(latency - expected_latency) > tolerance:
        raise PathClockLiquidityStoreValidationError(
            "closed-candle observation latency does not match its proven clock"
        )
    normalized_latency = max(0.0, latency)
    if normalized_latency >= source_cadence_seconds:
        raise PathClockLiquidityStoreValidationError(
            "closed-candle observation latency must be less than one source cadence"
        )

    return {
        "schema_version": PROVEN_CLOSED_CANDLE_TIME_SCHEMA_VERSION,
        "symbol": symbol,
        "timeframe": timeframe,
        "closed_candle_key": closed_candle_key,
        "closed_candle_sequence": closed_candle_sequence,
        "close_epoch_seconds": close_epoch,
        "timestamp_semantic": _TIMESTAMP_SEMANTIC,
        "timestamp_source": timestamp_source,
        "proof_source": _IDENTITY_PROOF_SOURCE,
        "bound_row_index": bound_row_index,
        "transition_count": transition_count,
        "source_cadence_seconds": source_cadence_seconds,
        "observed_epoch_seconds": observed_epoch,
        "observation_latency_seconds": normalized_latency,
        "contiguous_from_previous": contiguous,
    }


def _canonical_digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PathClockLiquidityStoreValidationError(
            "path-clock state must be finite canonical JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _safety_contract() -> dict[str, object]:
    return {
        "study_only": True,
        "causal": True,
        "execution_authority": False,
        "grants_entry_permission": False,
        "may_issue_orders": False,
    }


def _direction(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "UPTREND", "UP_SWING"}:
        return "UP"
    if text in {
        "SELL",
        "BEAR",
        "BEARISH",
        "DOWN",
        "DOWNTREND",
        "DOWN_SWING",
    }:
        return "DOWN"
    return ""


def _duration_contract(
    value: object,
    *,
    source_cadence_seconds: int,
) -> dict[str, object]:
    base: dict[str, object] = {
        "minimum_eligible_duration_seconds": MIN_ELIGIBLE_DURATION_SECONDS,
        "maximum_studied_duration_seconds": MAX_STUDIED_DURATION_SECONDS,
        "requested_duration_seconds": None,
        "new_entry_eligible": False,
        "status": "MISSING_DURATION",
        "reason": (
            "A fixed contract duration is required before a new timing anchor "
            "can be admitted."
        ),
    }
    if value is None or isinstance(value, bool):
        return base
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        base["status"] = "INVALID_DURATION"
        base["reason"] = "Contract duration must be a finite number of seconds."
        return base
    if not math.isfinite(parsed) or parsed < 0.0:
        base["status"] = "INVALID_DURATION"
        base["reason"] = "Contract duration must be a finite number of seconds."
        return base
    duration = int(math.floor(parsed))
    base["requested_duration_seconds"] = duration
    if duration < MIN_ELIGIBLE_DURATION_SECONDS:
        base["status"] = "EXCLUDED_UNDER_15_MINUTES"
        base["reason"] = (
            "Moves under 900 seconds are excluded from JPCLF entry timing."
        )
        return base
    if duration > MAX_STUDIED_DURATION_SECONDS:
        base["status"] = "EXCLUDED_ABOVE_BOUNDED_HORIZON"
        base["reason"] = "Contract duration exceeds the bounded V3 study horizon."
        return base
    if duration % source_cadence_seconds:
        base["status"] = "NOT_ALIGNED_TO_CLOSED_CANDLE_GRID"
        base["reason"] = (
            "The duration has no exact closed-candle endpoint on this timeframe; "
            "JPCLF will not interpolate one."
        )
        return base
    base.update(
        {
            "status": "ELIGIBLE",
            "reason": "Duration satisfies the closed-candle 15-minute timing floor.",
            "new_entry_eligible": True,
        }
    )
    return base


def _canonical_candles(
    value: Sequence[Mapping[str, Any]],
    *,
    current_closed_candle_key: str,
    current_closed_candle_sequence: int,
    current_close_epoch_seconds: float,
    bound_row_index: int,
    source_cadence_seconds: int,
) -> list[dict[str, Any]]:
    """Validate the complete resolver-bound candle axis for one close event.

    A timestamp by itself is never identity evidence. Every accepted candle
    must carry the resolver's stable event key and sequence, and its timestamp
    must occupy the exact clock position implied by the current proven close.
    """

    parsed: list[dict[str, Any]] = []
    seen_timestamps: set[float] = set()
    seen_sequences: set[int] = set()
    for index, source in enumerate(value):
        row = dict(source)
        if row.get("closed") is not True:
            raise PathClockLiquidityStoreValidationError(
                f"candles[{index}] must be a proven closed candle"
            )
        timestamp = _whole_second_timestamp(
            row.get("timestamp"), field=f"candles[{index}].timestamp"
        )
        if row.get("identity_stable") is not True:
            raise PathClockLiquidityStoreValidationError(
                f"candles[{index}] lacks stable resolver identity"
            )
        if row.get("identity_proof_source") != _IDENTITY_PROOF_SOURCE:
            raise PathClockLiquidityStoreValidationError(
                f"candles[{index}] identity proof is not resolver-bound"
            )
        stable_identity = str(row.get("stable_candle_identity") or "").strip()
        if not stable_identity.startswith("EXPLICIT:") or len(stable_identity) <= len(
            "EXPLICIT:"
        ):
            raise PathClockLiquidityStoreValidationError(
                f"candles[{index}] stable identity must contain an EXPLICIT event key"
            )
        event_key = stable_identity[len("EXPLICIT:") :]
        event_sequence = _strict_nonnegative_integer(
            row.get("closed_candle_sequence"),
            field=f"candles[{index}].closed_candle_sequence",
        )
        sequence_delta = current_closed_candle_sequence - event_sequence
        if sequence_delta < 0:
            raise PathClockLiquidityStoreValidationError(
                f"candles[{index}] is later than the proven current event"
            )
        expected_timestamp = (
            current_close_epoch_seconds
            - sequence_delta * source_cadence_seconds
        )
        if abs(timestamp - expected_timestamp) > _CLOCK_TOLERANCE_SECONDS:
            raise PathClockLiquidityStoreValidationError(
                f"candles[{index}] timestamp is not aligned to its resolver sequence"
            )
        ohlc = _mapping(row.get("ohlc"))
        open_value = _finite(ohlc.get("open"), field=f"candles[{index}].open")
        high = _finite(ohlc.get("high"), field=f"candles[{index}].high")
        low = _finite(ohlc.get("low"), field=f"candles[{index}].low")
        close = _finite(ohlc.get("close"), field=f"candles[{index}].close")
        if high < max(open_value, close) or low > min(open_value, close) or high <= low:
            raise PathClockLiquidityStoreValidationError(
                f"candles[{index}] OHLC geometry is invalid"
            )
        if timestamp in seen_timestamps:
            raise PathClockLiquidityStoreValidationError(
                "closed candle timestamps must be unique"
            )
        if event_sequence in seen_sequences:
            raise PathClockLiquidityStoreValidationError(
                "closed candle resolver sequences must be unique"
            )
        seen_timestamps.add(timestamp)
        seen_sequences.add(event_sequence)
        parsed.append(
            {
                "source_row_index": index,
                "closed_candle_key": event_key,
                "closed_candle_sequence": event_sequence,
                "timestamp_seconds": timestamp,
                "timestamp_token": str(row.get("timestamp") or ""),
                "coordinate_space": str(row.get("coordinate_space") or ""),
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "range": high - low,
                "direction": str(row.get("direction") or ""),
            }
        )
    if not parsed:
        raise PathClockLiquidityStoreValidationError(
            "at least one resolver-bound closed candle is required"
        )
    current_matches = [
        row
        for row in parsed
        if row["source_row_index"] == bound_row_index
        and row["closed_candle_key"] == current_closed_candle_key
        and row["closed_candle_sequence"] == current_closed_candle_sequence
        and abs(
            float(row["timestamp_seconds"]) - current_close_epoch_seconds
        )
        <= _CLOCK_TOLERANCE_SECONDS
    ]
    if len(current_matches) != 1:
        raise PathClockLiquidityStoreValidationError(
            "the proven close does not bind exactly one current candle row"
        )
    parsed.sort(key=lambda row: int(row["closed_candle_sequence"]))
    return parsed


def _new_field(
    *, symbol: str, timeframe: str, source_cadence_seconds: int
) -> JointPathClockLiquidityFieldV3:
    clock_step = max(30, source_cadence_seconds)
    max_points = max(2, int(MAX_STUDIED_DURATION_SECONDS // clock_step) + 1)
    return JointPathClockLiquidityFieldV3(
        symbol=symbol,
        timeframe=timeframe,
        coordinate_space=_COORDINATE_SPACE,
        order_domain=_ORDER_DOMAIN,
        clock_step_seconds=clock_step,
        max_trajectories=_MAX_TRAJECTORIES,
        max_points_per_trajectory=max_points,
        max_freezes=_MAX_FREEZES,
        max_neighbors=32,
    )


def _safe_public_estimate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Strip raw neighbor identities from an operator-facing estimate."""

    return {
        key: deepcopy(value.get(key))
        for key in (
            "schema_version",
            "status",
            "eligible",
            "contract_admitted",
            "new_entry_eligible",
            "minimum_eligible_duration_seconds",
            "maximum_studied_duration_seconds",
            "contract_duration_seconds",
            "elapsed_seconds",
            "remaining_seconds",
            "studied_direction",
            "current_path_mru",
            "stop_distance_mru",
            "move_size_mru",
            "support_count",
            "audited_neighbor_count",
            "excluded_early_target_count",
            "survival_probability",
            "probability_worst_drawdown_still_ahead",
            "target_time_seconds",
            "stop_time_seconds",
            "future_excursion_mru",
            "reason",
            "study_only",
            "causal",
            "execution_authority",
            "grants_entry_permission",
            "may_issue_orders",
        )
        if value.get(key) is not None
    }


class PathClockLiquiditySideStoreV3:
    """Persist active clocks and normalized completed paths outside Pair DNA."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        token = hashlib.sha256(
            f"{symbol}|{timeframe}".encode("utf-8")
        ).hexdigest()[:24]
        return self.root_dir / f"{token}.json"

    @staticmethod
    def _initial_state(
        *,
        symbol: str,
        timeframe: str,
        source_cadence_seconds: int,
    ) -> tuple[dict[str, Any], JointPathClockLiquidityFieldV3]:
        field = _new_field(
            symbol=symbol,
            timeframe=timeframe,
            source_cadence_seconds=source_cadence_seconds,
        )
        return (
            {
                "schema_version": PATH_CLOCK_LIQUIDITY_SIDE_STORE_SCHEMA_VERSION,
                "symbol": symbol,
                "timeframe": timeframe,
                "coordinate_space": _COORDINATE_SPACE,
                "order_domain": _ORDER_DOMAIN,
                "source_cadence_seconds": source_cadence_seconds,
                "field_snapshot": field.snapshot(),
                "active_anchors": [],
                "freeze_log": [],
                "candidate_replays": [],
                "baseline_replays": [],
                "observation_identities": [],
                "last_observation": {},
                "audit": {
                    "admitted_anchor_count": 0,
                    "matured_trajectory_count": 0,
                    "censored_anchor_count": 0,
                    "discontinuity_count": 0,
                    "excluded_early_replay_count": 0,
                    "censorship_reasons": {},
                },
                **_safety_contract(),
            },
            field,
        )

    @staticmethod
    def _load_state(
        value: Mapping[str, Any] | None,
        *,
        symbol: str,
        timeframe: str,
        source_cadence_seconds: int,
    ) -> tuple[dict[str, Any], JointPathClockLiquidityFieldV3]:
        if value is None:
            return PathClockLiquiditySideStoreV3._initial_state(
                symbol=symbol,
                timeframe=timeframe,
                source_cadence_seconds=source_cadence_seconds,
            )
        state = dict(value)
        claimed_digest = str(state.pop("state_digest", "")).lower()
        if not claimed_digest or _canonical_digest(state) != claimed_digest:
            raise PathClockLiquidityStoreValidationError(
                "path-clock side-store digest mismatch"
            )
        if (
            state.get("schema_version")
            != PATH_CLOCK_LIQUIDITY_SIDE_STORE_SCHEMA_VERSION
            or state.get("symbol") != symbol
            or state.get("timeframe") != timeframe
            or state.get("coordinate_space") != _COORDINATE_SPACE
            or state.get("order_domain") != _ORDER_DOMAIN
            or state.get("study_only") is not True
            or state.get("causal") is not True
            or state.get("execution_authority") is not False
            or state.get("grants_entry_permission") is not False
            or state.get("may_issue_orders") is not False
        ):
            raise PathClockLiquidityStoreValidationError(
                "path-clock side-store scope or safety contract mismatch"
            )
        if int(state.get("source_cadence_seconds", 0) or 0) != source_cadence_seconds:
            raise PathClockLiquidityStoreValidationError(
                "source cadence changed inside one pair/timeframe partition"
            )
        anchors = _rows(state.get("active_anchors"))
        freezes = _rows(state.get("freeze_log"))
        candidate_replays = _rows(state.get("candidate_replays"))
        baseline_replays = _rows(state.get("baseline_replays"))
        identities = _rows(state.get("observation_identities"))
        if (
            len(anchors) > _MAX_ACTIVE_ANCHORS
            or len(freezes) > _MAX_FREEZES
            or len(candidate_replays) > _MAX_REPLAYS
            or len(baseline_replays) > _MAX_REPLAYS
            or len(candidate_replays) != len(baseline_replays)
            or len(identities) > _MAX_OBSERVATION_IDENTITIES
        ):
            raise PathClockLiquidityStoreValidationError(
                "path-clock side-store capacity was exceeded"
            )
        state["active_anchors"] = anchors
        state["freeze_log"] = freezes
        state["candidate_replays"] = candidate_replays
        state["baseline_replays"] = baseline_replays
        state["observation_identities"] = identities
        try:
            field = JointPathClockLiquidityFieldV3.from_snapshot(
                _mapping(state.get("field_snapshot"))
            )
        except PathClockLiquidityValidationError as exc:
            raise PathClockLiquidityStoreValidationError(
                f"path-clock field snapshot is invalid: {exc}"
            ) from exc
        return state, field

    @staticmethod
    def _record_censorship(
        state: dict[str, Any], *, reason: str, count: int
    ) -> None:
        if count <= 0:
            return
        audit = _mapping(state.get("audit"))
        audit["censored_anchor_count"] = int(
            audit.get("censored_anchor_count", 0) or 0
        ) + count
        reasons = _mapping(audit.get("censorship_reasons"))
        reasons[reason] = int(reasons.get(reason, 0) or 0) + count
        if len(reasons) > _MAX_CENSORSHIP_REASONS:
            reasons = dict(
                sorted(
                    reasons.items(),
                    key=lambda item: (-int(item[1] or 0), str(item[0])),
                )[:_MAX_CENSORSHIP_REASONS]
            )
        audit["censorship_reasons"] = reasons
        state["audit"] = audit

    @staticmethod
    def _anchor_point(
        anchor: Mapping[str, Any],
        *,
        by_timestamp: Mapping[float, Mapping[str, Any]],
        current: Mapping[str, Any],
    ) -> dict[str, float] | None:
        reference_timestamps = [
            float(value) for value in cast(list[Any], anchor.get("reference_timestamps", []))
        ]
        references = [by_timestamp.get(value) for value in reference_timestamps]
        if not references or any(row is None for row in references):
            return None
        anchor_timestamp = float(anchor["anchor_timestamp_seconds"])
        anchor_candle = by_timestamp.get(anchor_timestamp)
        if anchor_candle is None:
            return None
        ranges = [float(cast(Mapping[str, Any], row)["range"]) for row in references]
        baseline = float(median(ranges)) if ranges else 0.0
        if not math.isfinite(baseline) or baseline <= 1e-12:
            return None
        anchor_close = float(anchor_candle["close"])
        elapsed = float(current["timestamp_seconds"]) - anchor_timestamp
        return {
            "elapsed_seconds": elapsed,
            "path_mru": (float(current["close"]) - anchor_close) / baseline,
            "high_mru": (float(current["high"]) - anchor_close) / baseline,
            "low_mru": (float(current["low"]) - anchor_close) / baseline,
        }

    @staticmethod
    def _scenario_outcome(
        points: Sequence[Mapping[str, Any]],
        *,
        direction: str,
        stop_distance_mru: float,
        move_size_mru: float,
    ) -> tuple[int | None, int | None, bool]:
        sign = 1.0 if direction == "UP" else -1.0
        target_time: int | None = None
        target_index: int | None = None
        stop_time: int | None = None
        stop_index: int | None = None
        for index, point in enumerate(points[1:], start=1):
            directional_high = max(
                sign * float(point.get("high_mru", 0.0) or 0.0),
                sign * float(point.get("low_mru", 0.0) or 0.0),
            )
            directional_low = min(
                sign * float(point.get("high_mru", 0.0) or 0.0),
                sign * float(point.get("low_mru", 0.0) or 0.0),
            )
            elapsed = int(round(float(point.get("elapsed_seconds", 0.0) or 0.0)))
            if stop_time is None and directional_low <= -stop_distance_mru:
                stop_time = elapsed
                stop_index = index
            if target_time is None and directional_high >= move_size_mru:
                target_time = elapsed
                target_index = index
        # With candle OHLC, target and stop ordering inside the same candle is
        # unknowable.  Equal indices therefore fail closed as non-survival.
        survived = bool(
            target_time is not None
            and target_index is not None
            and (stop_index is None or target_index < stop_index)
        )
        return target_time, stop_time, survived

    @staticmethod
    def _mature_replay(
        anchor: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], bool] | None:
        prediction = _mapping(anchor.get("admission_prediction"))
        sweep_predictions = _rows(prediction.get("sweep_predictions"))
        timing_window = _mapping(prediction.get("timing_window_seconds"))
        if not sweep_predictions or not timing_window:
            return None
        points = _rows(anchor.get("points"))
        if len(points) < 2:
            return None
        direction = str(anchor.get("studied_direction") or "")
        duration = int(anchor.get("duration_seconds", 0) or 0)
        selected_stop = float(prediction.get("selected_stop_distance_mru", 0.0) or 0.0)
        selected_move = float(prediction.get("selected_move_size_mru", 0.0) or 0.0)
        selected_target_time, _selected_stop_time, _selected_survived = (
            PathClockLiquiditySideStoreV3._scenario_outcome(
                points,
                direction=direction,
                stop_distance_mru=selected_stop,
                move_size_mru=selected_move,
            )
        )
        move_occurred = selected_target_time is not None
        observed_move_time = (
            selected_target_time if selected_target_time is not None else duration
        )
        early = bool(
            move_occurred
            and observed_move_time < MIN_ELIGIBLE_DURATION_SECONDS
        )
        terminal = float(points[-1].get("path_mru", 0.0) or 0.0)
        observed_direction = (
            "UP" if terminal > 1e-12 else "DOWN" if terminal < -1e-12 else "FLAT"
        )
        candidate_sweeps: list[dict[str, Any]] = []
        baseline_sweeps: list[dict[str, Any]] = []
        for row in sweep_predictions:
            stop = float(row.get("stop_distance_mru", 0.0) or 0.0)
            move = float(row.get("move_size_mru", 0.0) or 0.0)
            _target, _stop, survived = (
                PathClockLiquiditySideStoreV3._scenario_outcome(
                    points,
                    direction=direction,
                    stop_distance_mru=stop,
                    move_size_mru=move,
                )
            )
            _baseline_target, _baseline_stop, baseline_survived = (
                PathClockLiquiditySideStoreV3._scenario_outcome(
                    points,
                    direction=direction,
                    # Baseline and candidate must be evaluated on the exact
                    # same stop/move grid. A tighter baseline stop would make
                    # stop widening look like intelligence improvement.
                    stop_distance_mru=stop,
                    move_size_mru=move,
                )
            )
            candidate_sweeps.append(
                {
                    "stop_distance_mru": stop,
                    "move_size_mru": move,
                    "predicted_survival_probability": float(
                        row.get("predicted_survival_probability", 0.0) or 0.0
                    ),
                    "survived_until_move": survived,
                }
            )
            baseline_sweeps.append(
                {
                    "stop_distance_mru": stop,
                    "move_size_mru": move,
                    "predicted_survival_probability": 0.5,
                    "survived_until_move": baseline_survived,
                }
            )
        shared = {
            "symbol": prediction.get("symbol"),
            "timeframe": prediction.get("timeframe"),
            "coordinate_space": _COORDINATE_SPACE,
            "order_domain": _ORDER_DOMAIN,
            "frozen_on_closed_candle": True,
            "future_leakage_detected": False,
            "closed_candle_key": anchor.get("anchor_closed_candle_key"),
            "horizon_seconds": duration,
            "observed_direction": observed_direction,
            "observed_move_occurred": move_occurred,
            # A no-target completion is right-censored at the exact horizon.
            # It remains a negative timing/survival observation instead of
            # disappearing from the promotion cohort.
            "observed_move_time_seconds": observed_move_time,
        }
        candidate = {
            **shared,
            "predicted_direction": direction,
            "timing_window_seconds": {
                "start": int(timing_window.get("start", 0) or 0),
                "end": int(timing_window.get("end", duration) or duration),
            },
            "sweep_outcomes": candidate_sweeps,
        }
        baseline = {
            **shared,
            "predicted_direction": prediction.get("baseline_direction"),
            "timing_window_seconds": {
                "start": 0,
                "end": min(
                    duration,
                    int(prediction.get("source_cadence_seconds", 0) or 0),
                ),
            },
            "sweep_outcomes": baseline_sweeps,
        }
        return candidate, baseline, early

    @staticmethod
    def _promotion_evidence(
        state: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        candidate_rows = _rows(state.get("candidate_replays"))
        baseline_rows = _rows(state.get("baseline_replays"))
        pending_gate = {
            "passed": False,
            "status": "INSUFFICIENT_REPLAY_CALIBRATION",
            "minimum_replays": _MINIMUM_PROMOTION_REPLAYS,
            "support": {
                "baseline": len(baseline_rows),
                "candidate": len(candidate_rows),
                "passed": False,
            },
            "all_axes_improved": False,
            "required_axes": [
                "directional_accuracy",
                "timing_accuracy",
                "sweep_survival_rate",
                "calibration_score",
            ],
            "reason": (
                "Timing support stays shadow-only until replay calibration "
                "improves all four independent axes."
            ),
            **_safety_contract(),
        }
        if not candidate_rows or len(candidate_rows) != len(baseline_rows):
            return {}, {}, pending_gate
        symbol = str(state.get("symbol") or "")
        timeframe = str(state.get("timeframe") or "")
        try:
            baseline_score = score_path_clock_replays_v3(
                baseline_rows,
                symbol=symbol,
                timeframe=timeframe,
                coordinate_space=_COORDINATE_SPACE,
                order_domain=_ORDER_DOMAIN,
            )
            candidate_score = score_path_clock_replays_v3(
                candidate_rows,
                symbol=symbol,
                timeframe=timeframe,
                coordinate_space=_COORDINATE_SPACE,
                order_domain=_ORDER_DOMAIN,
            )
            gate = evaluate_path_clock_promotion_gate_v3(
                baseline_score=baseline_score,
                candidate_score=candidate_score,
                minimum_replays=_MINIMUM_PROMOTION_REPLAYS,
                minimum_improvement=0.0,
            )
        except PathClockLiquidityValidationError:
            return {}, {}, pending_gate
        return dict(baseline_score), dict(candidate_score), dict(gate)

    @staticmethod
    def _compact_public(
        state: Mapping[str, Any],
        field: JointPathClockLiquidityFieldV3,
        *,
        duration: Mapping[str, Any],
        latest_freeze: Mapping[str, Any] | None,
        discontinuity_censored: int,
        forecast_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        anchors = _rows(state.get("active_anchors"))
        audit = _mapping(state.get("audit"))
        last_observation = _mapping(state.get("last_observation"))
        partition = field.pair_dna_partition_summary()
        public_freeze = _mapping(latest_freeze)
        estimates = [
            _safe_public_estimate(row)
            for row in _rows(public_freeze.get("scenario_estimates"))
        ]
        studied_estimates = [
            row
            for row in estimates
            if row.get("status") == "STUDIED" and row.get("eligible") is True
        ]
        selected: dict[str, Any] = {}
        if studied_estimates:
            selected = max(
                studied_estimates,
                key=lambda row: (
                    float(row.get("survival_probability", 0.0) or 0.0),
                    _integer_value(row.get("support_count")),
                    -float(row.get("stop_distance_mru", 0.0) or 0.0),
                ),
            )
        trajectory_count = _integer_value(partition.get("trajectory_count"))
        eligible = duration.get("new_entry_eligible") is True
        if discontinuity_censored:
            status = "CENSORED_DISCONTINUITY"
        elif not eligible and anchors:
            status = "ACTIVE_TRACKING_ONLY"
        elif not eligible:
            status = str(duration.get("status") or "INELIGIBLE_DURATION")
        elif selected:
            status = "STUDIED"
        elif trajectory_count:
            status = "ACCUMULATING_MATCH_SUPPORT"
        else:
            status = "BUILDING_HISTORY"
        below_floor = sum(
            1
            for row in anchors
            if 0
            <= int(row.get("duration_seconds", 0) or 0)
            - int(row.get("last_elapsed_seconds", 0) or 0)
            < MIN_ELIGIBLE_DURATION_SECONDS
        )
        baseline_score, candidate_score, promotion_gate = (
            PathClockLiquiditySideStoreV3._promotion_evidence(state)
        )
        context = _mapping(forecast_context)
        closed_candle_time_proof = _mapping(
            public_freeze.get("closed_candle_time_proof")
        )
        forecast_lineage = dict(_mapping(context.get("lineage")))
        if closed_candle_time_proof:
            forecast_lineage["anchor_close_epoch_seconds"] = (
                closed_candle_time_proof.get("close_epoch_seconds")
            )
        forward_timing_forecast = build_hierarchical_forward_timing_forecast_v3(
            candidate_direction=(
                context.get("candidate_direction")
                or public_freeze.get("studied_direction")
            ),
            duration_contract=duration,
            source_cadence_seconds=state.get("source_cadence_seconds", 300),
            directional_confidence=context.get("directional_confidence", 0.0),
            current_regime=context.get("current_regime", "UNKNOWN"),
            current_behavior=_mapping(context.get("current_behavior")),
            pair_profile=_mapping(context.get("pair_profile")),
            motif_lattice=_mapping(context.get("motif_lattice")),
            survival_network=_mapping(context.get("survival_network")),
            motif_trajectory_library=_mapping(
                context.get("motif_trajectory_library")
            ),
            exact_jpclf_estimate=selected,
            exact_time_proven=bool(
                public_freeze and closed_candle_time_proof
            ),
            exact_promotion_passed=promotion_gate.get("passed") is True,
            lineage=forecast_lineage,
        )
        forecast_available = (
            forward_timing_forecast.get("status") == "FORECAST_AVAILABLE"
        )
        hard_duration_veto = not eligible
        survival_probability = (
            float(selected.get("survival_probability", 0.0) or 0.0)
            if selected
            else 0.0
        )
        worst_pullback_ahead_probability = (
            float(
                selected.get(
                    "probability_worst_drawdown_still_ahead",
                    0.0,
                )
                or 0.0
            )
            if selected
            else 0.0
        )
        promoted_empirical_read = bool(
            eligible and selected and promotion_gate["passed"] is True
        )
        timing_supports_entry = bool(
            promoted_empirical_read
            and survival_probability
            >= _MIN_SUPPORTED_SURVIVAL_PROBABILITY
            and worst_pullback_ahead_probability
            <= _MAX_SUPPORTED_WORST_PULLBACK_AHEAD_PROBABILITY
        )
        # JPCLF is asymmetric: after promotion, anything that does not meet
        # the calibrated support policy delays/vetoes a new entry.  It still
        # cannot turn an independently closed permission into an open one.
        empirical_timing_veto = bool(
            promoted_empirical_read and not timing_supports_entry
        )
        timing_veto = bool(hard_duration_veto or empirical_timing_veto)
        field_duration = public_freeze.get("contract_duration_seconds")
        if field_duration is None:
            field_duration = duration.get("requested_duration_seconds")
        remaining_seconds = public_freeze.get("remaining_seconds")
        if remaining_seconds is None:
            remaining_seconds = field_duration
        observed_seconds = public_freeze.get("closed_at_seconds")
        valid_until: float | None = None
        if observed_seconds is not None and remaining_seconds is not None:
            valid_until = float(observed_seconds) + float(remaining_seconds)
        if hard_duration_veto:
            timing_reason = str(
                duration.get("reason")
                or "A new entry does not have the required 15-minute clock."
            )
        elif timing_supports_entry:
            timing_reason = (
                "Promoted historical path-clock evidence supports this entry "
                "window without granting entry permission."
            )
        elif empirical_timing_veto:
            timing_reason = (
                "Promoted historical path-clock evidence says the worst pullback "
                "may still be ahead."
                if worst_pullback_ahead_probability
                > _MAX_SUPPORTED_WORST_PULLBACK_AHEAD_PROBABILITY
                else (
                    "Promoted historical path-clock evidence does not show enough "
                    "stop survival for this entry window."
                )
            )
        else:
            timing_reason = (
                "A closed-candle-relative forward timing estimate is available "
                "while exact JPCLF survival calibration continues in shadow."
                if forecast_available
                else (
                    "Timing history remains shadow-only until all four replay "
                    "axes improve with sufficient support."
                )
            )
        timing_read = {
            "status": (
                "HARD_DURATION_VETO"
                if hard_duration_veto
                else "TIMING_SUPPORT"
                if timing_supports_entry
                else "TIMING_VETO"
                if empirical_timing_veto
                else "FORWARD_ESTIMATE_ONLY"
                if forecast_available
                else "BUILDING_REPLAY_CALIBRATION"
            ),
            "state": (
                "INELIGIBLE"
                if hard_duration_veto
                else "ELIGIBLE_NOW"
                if timing_supports_entry
                else "DRAWDOWN_AHEAD"
                if (
                    empirical_timing_veto
                    and worst_pullback_ahead_probability
                    > _MAX_SUPPORTED_WORST_PULLBACK_AHEAD_PROBABILITY
                )
                else "SWEEP_RISK"
                if empirical_timing_veto
                else "FORECAST_AVAILABLE"
                if forecast_available
                else "SHADOW_STUDY"
            ),
            "reason": timing_reason,
            "side": public_freeze.get("studied_direction") or "",
            "studied_direction": public_freeze.get("studied_direction") or "",
            "eligible": eligible,
            "contract_admitted": bool(public_freeze),
            "contract_duration_seconds": field_duration,
            "candidate_horizon_seconds": field_duration,
            "elapsed_seconds": public_freeze.get("elapsed_seconds"),
            "remaining_seconds": remaining_seconds,
            "support_count": int(selected.get("support_count", 0) or 0),
            "minimum_support": 3,
            "survival_probability": selected.get("survival_probability"),
            "probability_worst_drawdown_still_ahead": selected.get(
                "probability_worst_drawdown_still_ahead"
            ),
            "new_entry_eligible": eligible,
            "timing_supports_entry": timing_supports_entry,
            "timing_veto": timing_veto,
            "closed_candle_key": public_freeze.get("closed_candle_key") or "",
            "observed_at": observed_seconds,
            "valid_until": valid_until,
            "promotion_gate": promotion_gate,
            "forward_timing_forecast": deepcopy(forward_timing_forecast),
            **_safety_contract(),
        }
        latest_field_state = {
            key: deepcopy(public_freeze.get(key))
            for key in (
                "closed_candle_key",
                "order_index",
                "closed_at_seconds",
                "studied_direction",
                "contract_duration_seconds",
                "elapsed_seconds",
                "remaining_seconds",
                "new_entry_eligible",
                "current_path_mru",
            )
            if public_freeze.get(key) is not None
        }
        latest_time_proof = _mapping(
            public_freeze.get("closed_candle_time_proof")
            or last_observation.get("closed_candle_time_proof")
        )
        if latest_time_proof:
            latest_field_state["closed_candle_time_proof"] = deepcopy(
                latest_time_proof
            )
        result: dict[str, Any] = {
            "schema_version": PATH_CLOCK_LIQUIDITY_PUBLIC_SCHEMA_VERSION,
            "symbol": state.get("symbol"),
            "timeframe": state.get("timeframe"),
            "closed_candle_key": _mapping(state.get("last_observation")).get(
                "closed_candle_key"
            ),
            "closed_candle_sequence": _mapping(
                state.get("last_observation")
            ).get("closed_candle_sequence"),
            "coordinate_space": _COORDINATE_SPACE,
            "order_domain": _ORDER_DOMAIN,
            "status": status,
            "reason": (
                str(duration.get("reason") or "")
                if not eligible
                else (
                    "Historical path-clock-liquidity support is available."
                    if selected
                    else "Exact closed-candle trajectories are still accumulating."
                )
            ),
            "duration_policy": deepcopy(dict(duration)),
            "new_entry_eligible": eligible,
            "active_tracking_continues_below_floor": below_floor > 0,
            "active_anchor_count": len(anchors),
            "active_anchor_count_below_900_seconds_remaining": below_floor,
            "trajectory_count": trajectory_count,
            "freeze_count": len(_rows(state.get("freeze_log"))),
            "latest_field_state": latest_field_state,
            "scenario_estimates": estimates,
            "best_supported_scenario": deepcopy(selected),
            "timing_read": timing_read,
            "forward_timing_forecast": forward_timing_forecast,
            "promotion_gate": promotion_gate,
            "baseline_replay_score": baseline_score,
            "candidate_replay_score": candidate_score,
            "replay_support_count": len(_rows(state.get("candidate_replays"))),
            "minimum_eligible_duration_seconds": MIN_ELIGIBLE_DURATION_SECONDS,
            "maximum_studied_duration_seconds": MAX_STUDIED_DURATION_SECONDS,
            "pair_dna_partition": partition,
            "censorship_audit": {
                "censored_anchor_count": int(
                    audit.get("censored_anchor_count", 0) or 0
                ),
                "discontinuity_count": int(
                    audit.get("discontinuity_count", 0) or 0
                ),
                "excluded_early_replay_count": int(
                    audit.get("excluded_early_replay_count", 0) or 0
                ),
                "latest_discontinuity_censored_anchor_count": (
                    discontinuity_censored
                ),
                "reasons": deepcopy(_mapping(audit.get("censorship_reasons"))),
            },
            "time_proof_audit": deepcopy(
                _mapping(audit.get("latest_closed_candle_time_proof"))
            ),
            "persistence_contract": {
                "raw_trajectories_in_pair_dna_json": False,
                "raw_trajectories_in_public_study": False,
                "dedicated_pair_side_store": True,
                "restart_safe": True,
                "idempotent_closed_candle_keys": True,
                "interpolates_missing_candles": False,
                "requires_resolver_bound_time_proof": True,
                "time_proof_schema": PROVEN_CLOSED_CANDLE_TIME_SCHEMA_VERSION,
                "raw_geometry_in_time_proof": False,
            },
            **_safety_contract(),
        }
        result["public_study_digest"] = _canonical_digest(result)
        return result

    def observe_closed_candle(
        self,
        *,
        symbol: object,
        timeframe: object,
        closed_candle_key: object,
        closed_candle_sequence: object,
        closed_candle_time_proof: Mapping[str, Any] | None,
        candles: Sequence[Mapping[str, Any]],
        source_cadence_seconds: object,
        studied_direction: object,
        contract_duration_seconds: object | None,
        liquidity_state: Mapping[str, Any],
        forecast_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance every admitted anchor using one exact closed-candle event."""

        canonical_symbol = " ".join(str(symbol or "").strip().upper().split())
        canonical_timeframe = " ".join(
            str(timeframe or "").strip().upper().split()
        )
        close_key = str(closed_candle_key or "").strip()
        if not canonical_symbol or not canonical_timeframe or not close_key:
            raise PathClockLiquidityStoreValidationError(
                "symbol, timeframe, and closed_candle_key are required"
            )
        close_sequence = _strict_nonnegative_integer(
            closed_candle_sequence,
            field="closed_candle_sequence",
        )
        cadence = _strict_nonnegative_integer(
            source_cadence_seconds,
            field="source_cadence_seconds",
        )
        if cadence < 30 or cadence > MAX_STUDIED_DURATION_SECONDS:
            raise PathClockLiquidityStoreValidationError(
                "source cadence must be between 30 and 7200 seconds"
            )
        time_proof = _validated_closed_candle_time_proof(
            closed_candle_time_proof,
            symbol=canonical_symbol,
            timeframe=canonical_timeframe,
            closed_candle_key=close_key,
            closed_candle_sequence=close_sequence,
            source_cadence_seconds=cadence,
        )
        current_timestamp = float(time_proof["close_epoch_seconds"])
        canonical = _canonical_candles(
            candles,
            current_closed_candle_key=close_key,
            current_closed_candle_sequence=close_sequence,
            current_close_epoch_seconds=current_timestamp,
            bound_row_index=int(time_proof["bound_row_index"]),
            source_cadence_seconds=cadence,
        )
        current = next(
            row
            for row in canonical
            if row["source_row_index"] == time_proof["bound_row_index"]
            and row["closed_candle_key"] == close_key
            and row["closed_candle_sequence"] == close_sequence
            and row["timestamp_seconds"] == current_timestamp
        )
        current_order = int(round(current_timestamp))
        spaces = {str(row.get("coordinate_space") or "") for row in canonical}
        if len(spaces) != 1 or not next(iter(spaces)):
            raise PathClockLiquidityStoreValidationError(
                "one proven source coordinate space is required"
            )
        current_direction = _direction(studied_direction)
        duration = _duration_contract(
            contract_duration_seconds,
            source_cadence_seconds=cadence,
        )
        features = {
            "wick_entropy": _finite(
                liquidity_state.get("wick_entropy"), field="liquidity_state.wick_entropy"
            ),
            "repeated_area_touches": max(
                0, int(float(liquidity_state.get("repeated_area_touches", 0) or 0))
            ),
            "late_sweep_motif_distance": _finite(
                liquidity_state.get("late_sweep_motif_distance"),
                field="liquidity_state.late_sweep_motif_distance",
            ),
            "wick_body_asymmetry": _finite(
                liquidity_state.get("wick_body_asymmetry"),
                field="liquidity_state.wick_body_asymmetry",
            ),
            "object_copresence_density": _finite(
                liquidity_state.get("object_copresence_density"),
                field="liquidity_state.object_copresence_density",
            ),
            "as_of_order_index": current_order,
            "as_of_seconds": current_timestamp,
            "wick_body_asymmetry_source": "CLOSED_CANDLE",
            "source_candle_closed": True,
            "frozen_before_outcome": True,
        }
        observation_body = {
            "closed_candle_key": close_key,
            "closed_candle_sequence": close_sequence,
            "closed_at_seconds": current_timestamp,
            "closed_candle_time_proof": time_proof,
            "source_coordinate_space": next(iter(spaces)),
            "current_ohlc": {
                key: current[key] for key in ("open", "high", "low", "close")
            },
            "studied_direction": current_direction,
            "duration_contract": duration,
            "liquidity_state": features,
            "forecast_context_digest": _canonical_digest(
                _mapping(forecast_context)
            ),
        }
        observation_digest = _canonical_digest(observation_body)
        path = self._path(canonical_symbol, canonical_timeframe)
        with exclusive_store_lock(path, timeout_seconds=5.0):
            state, field = self._load_state(
                read_json_document(path),
                symbol=canonical_symbol,
                timeframe=canonical_timeframe,
                source_cadence_seconds=cadence,
            )
            identities = _rows(state.get("observation_identities"))
            prior_identity = next(
                (
                    row
                    for row in identities
                    if str(row.get("closed_candle_key") or "") == close_key
                ),
                None,
            )
            if prior_identity is not None:
                if prior_identity.get("observation_digest") != observation_digest:
                    raise PathClockLiquidityStoreValidationError(
                        "closed_candle_key conflicts with different JPCLF evidence"
                    )
                return deepcopy(_mapping(state.get("latest_public_study")))

            anchors = _rows(state.get("active_anchors"))
            last = _mapping(state.get("last_observation"))
            discontinuity_censored = 0
            if last:
                previous_timestamp = _finite(
                    last.get("closed_at_seconds"), field="last_observation.closed_at_seconds"
                )
                previous_sequence = _strict_nonnegative_integer(
                    last.get("closed_candle_sequence"),
                    field="last_observation.closed_candle_sequence",
                )
                delta = current_timestamp - previous_timestamp
                sequence_delta = close_sequence - previous_sequence
                if delta <= 0.0:
                    raise PathClockLiquidityStoreValidationError(
                        "closed-candle time must increase strictly"
                    )
                if sequence_delta <= 0:
                    raise PathClockLiquidityStoreValidationError(
                        "closed-candle sequence must increase strictly"
                    )
                clock_is_contiguous = bool(
                    sequence_delta == 1
                    and abs(delta - cadence)
                    <= max(_CLOCK_TOLERANCE_SECONDS, cadence * 1e-9)
                )
                if (
                    time_proof["contiguous_from_previous"] is not True
                    or not clock_is_contiguous
                ):
                    discontinuity_censored = len(anchors)
                    anchors = []
                    audit = _mapping(state.get("audit"))
                    audit["discontinuity_count"] = int(
                        audit.get("discontinuity_count", 0) or 0
                    ) + 1
                    state["audit"] = audit
                    self._record_censorship(
                        state,
                        reason="NON_CONTIGUOUS_CLOSED_CANDLE_EVENT",
                        count=discontinuity_censored,
                    )

            by_timestamp = {
                float(row["timestamp_seconds"]): row for row in canonical
            }
            retained: list[dict[str, Any]] = []
            matured_count = 0
            candidate_replays = _rows(state.get("candidate_replays"))
            baseline_replays = _rows(state.get("baseline_replays"))
            for anchor in anchors:
                point = self._anchor_point(
                    anchor,
                    by_timestamp=by_timestamp,
                    current=current,
                )
                if point is None:
                    self._record_censorship(
                        state,
                        reason="ANCHOR_NOT_REOBSERVED_ON_CURRENT_AXIS",
                        count=1,
                    )
                    continue
                elapsed = int(round(float(point["elapsed_seconds"])))
                expected_elapsed = int(anchor.get("last_elapsed_seconds", 0) or 0) + cadence
                if elapsed != expected_elapsed:
                    self._record_censorship(
                        state,
                        reason="ANCHOR_CLOCK_DISCONTINUITY",
                        count=1,
                    )
                    continue
                target_duration = int(anchor.get("duration_seconds", 0) or 0)
                if elapsed > target_duration:
                    self._record_censorship(
                        state,
                        reason="UNOBSERVED_EXACT_EXPIRY_BOUNDARY",
                        count=1,
                    )
                    continue
                points = _rows(anchor.get("points"))
                points.append(point)
                anchor["points"] = points
                anchor["last_elapsed_seconds"] = elapsed
                anchor["last_path_mru"] = float(point["path_mru"])
                if elapsed == target_duration:
                    replay = self._mature_replay(anchor)
                    if replay is not None:
                        candidate_replay, baseline_replay, early_replay = replay
                        if early_replay:
                            audit = _mapping(state.get("audit"))
                            audit["excluded_early_replay_count"] = int(
                                audit.get("excluded_early_replay_count", 0) or 0
                            ) + 1
                            state["audit"] = audit
                        else:
                            candidate_replays.append(candidate_replay)
                            baseline_replays.append(baseline_replay)
                    trajectory = {
                        "trajectory_id": (
                            f"{canonical_symbol}|{canonical_timeframe}|"
                            f"{anchor.get('anchor_closed_candle_key')}|{target_duration}"
                        ),
                        "symbol": canonical_symbol,
                        "timeframe": canonical_timeframe,
                        "coordinate_space": _COORDINATE_SPACE,
                        "order_domain": _ORDER_DOMAIN,
                        "study_only": True,
                        "completed": True,
                        "anchor": {
                            "closed_candle_key": anchor.get(
                                "anchor_closed_candle_key"
                            ),
                            "order_index": anchor.get("anchor_order_index"),
                            "closed_at_seconds": anchor.get(
                                "anchor_timestamp_seconds"
                            ),
                            "closed": True,
                        },
                        "duration_seconds": target_duration,
                        "source_cadence_seconds": cadence,
                        "exact_subcandle_timestamps_proven": False,
                        "studied_direction": anchor.get("studied_direction"),
                        "liquidity_state": anchor.get("liquidity_state"),
                        "points": points,
                    }
                    try:
                        field.add_trajectory(trajectory)
                    except PathClockLiquidityValidationError as exc:
                        if "capacity reached" not in str(exc).lower():
                            raise PathClockLiquidityStoreValidationError(
                                f"completed JPCLF trajectory is invalid: {exc}"
                            ) from exc
                        self._record_censorship(
                            state,
                            reason="BOUNDED_TRAJECTORY_CAPACITY_REACHED",
                            count=1,
                        )
                    else:
                        matured_count += 1
                    continue
                retained.append(anchor)

            state["candidate_replays"] = candidate_replays[-_MAX_REPLAYS:]
            state["baseline_replays"] = baseline_replays[-_MAX_REPLAYS:]

            admitted_anchor: dict[str, Any] | None = None
            if duration.get("new_entry_eligible") is True and current_direction:
                references = canonical[-_MAX_REFERENCE_CANDLES:]
                admitted_anchor = {
                    "anchor_closed_candle_key": close_key,
                    "anchor_order_index": current_order,
                    "anchor_timestamp_seconds": current_timestamp,
                    "duration_seconds": _integer_value(
                        duration.get("requested_duration_seconds")
                    ),
                    "studied_direction": current_direction,
                    "liquidity_state": features,
                    "reference_timestamps": [
                        float(row["timestamp_seconds"]) for row in references
                    ],
                    "last_elapsed_seconds": 0,
                    "last_path_mru": 0.0,
                    "points": [
                        {
                            "elapsed_seconds": 0.0,
                            "path_mru": 0.0,
                            "high_mru": 0.0,
                            "low_mru": 0.0,
                        }
                    ],
                }
                retained.append(admitted_anchor)
                audit = _mapping(state.get("audit"))
                audit["admitted_anchor_count"] = int(
                    audit.get("admitted_anchor_count", 0) or 0
                ) + 1
                state["audit"] = audit
            elif duration.get("new_entry_eligible") is True and not current_direction:
                duration = {
                    **duration,
                    "status": "DIRECTION_NOT_STUDIED",
                    "reason": "A directional closed-candle study is required for admission.",
                    "new_entry_eligible": False,
                }

            if len(retained) > _MAX_ACTIVE_ANCHORS:
                omitted = len(retained) - _MAX_ACTIVE_ANCHORS
                retained = retained[omitted:]
                self._record_censorship(
                    state,
                    reason="BOUNDED_ACTIVE_ANCHOR_CAPACITY_REACHED",
                    count=omitted,
                )
            state["active_anchors"] = retained
            audit = _mapping(state.get("audit"))
            audit["matured_trajectory_count"] = int(
                audit.get("matured_trajectory_count", 0) or 0
            ) + matured_count
            state["audit"] = audit

            selected_anchor = admitted_anchor
            if selected_anchor is None and retained:
                selected_anchor = min(
                    retained,
                    key=lambda row: (
                        int(row.get("duration_seconds", 0) or 0)
                        - int(row.get("last_elapsed_seconds", 0) or 0),
                        float(row.get("anchor_timestamp_seconds", 0.0) or 0.0),
                    ),
                )
            latest_freeze: dict[str, Any] = {}
            if selected_anchor is not None:
                scenario_estimates: list[dict[str, Any]] = []
                for stop, move in _SCENARIOS:
                    try:
                        estimate = field.estimate_stop_survival(
                            studied_direction=selected_anchor.get(
                                "studied_direction"
                            ),
                            contract_duration_seconds=selected_anchor.get(
                                "duration_seconds"
                            ),
                            elapsed_seconds=selected_anchor.get(
                                "last_elapsed_seconds"
                            ),
                            current_path_mru=selected_anchor.get(
                                "last_path_mru"
                            ),
                            stop_distance_mru=stop,
                            move_size_mru=move,
                            liquidity_state=features,
                            causal_order_index=current_order,
                            causal_cutoff_seconds=current_timestamp,
                            minimum_support=3,
                        )
                    except PathClockLiquidityValidationError as exc:
                        raise PathClockLiquidityStoreValidationError(
                            f"live JPCLF estimate is invalid: {exc}"
                        ) from exc
                    scenario_estimates.append(dict(estimate))
                if admitted_anchor is not None:
                    admission_candidates = [
                        row
                        for row in scenario_estimates
                        if row.get("status") == "STUDIED"
                        and row.get("eligible") is True
                        and row.get("survival_probability") is not None
                    ]
                    selected_prediction: dict[str, Any] = {}
                    if admission_candidates:
                        selected_prediction = max(
                            admission_candidates,
                            key=lambda row: (
                                _integer_value(row.get("support_count")),
                                float(
                                    row.get("survival_probability", 0.0) or 0.0
                                ),
                            ),
                        )
                    target_window = _mapping(
                        selected_prediction.get("target_time_seconds")
                    )
                    if (
                        selected_prediction
                        and target_window.get("p10") is not None
                        and target_window.get("p90") is not None
                    ):
                        target_duration = _integer_value(
                            admitted_anchor.get("duration_seconds")
                        )
                        timing_start = max(
                            0,
                            min(
                                target_duration,
                                int(round(float(target_window.get("p10", 0) or 0))),
                            ),
                        )
                        timing_end = max(
                            timing_start,
                            min(
                                target_duration,
                                int(
                                    round(
                                        float(
                                            target_window.get("p90", target_duration)
                                            or target_duration
                                        )
                                    )
                                ),
                            ),
                        )
                        admitted_anchor["admission_prediction"] = {
                            "symbol": canonical_symbol,
                            "timeframe": canonical_timeframe,
                            "baseline_direction": (
                                _direction(current.get("direction"))
                                or str(admitted_anchor.get("studied_direction") or "")
                            ),
                            "source_cadence_seconds": cadence,
                            "timing_window_seconds": {
                                "start": timing_start,
                                "end": timing_end,
                            },
                            "selected_stop_distance_mru": selected_prediction.get(
                                "stop_distance_mru"
                            ),
                            "selected_move_size_mru": selected_prediction.get(
                                "move_size_mru"
                            ),
                            "sweep_predictions": [
                                {
                                    "stop_distance_mru": row.get(
                                        "stop_distance_mru"
                                    ),
                                    "move_size_mru": row.get("move_size_mru"),
                                    "predicted_survival_probability": row.get(
                                        "survival_probability"
                                    ),
                                }
                                for row in admission_candidates
                            ],
                            "field_state_digest": field.snapshot().get(
                                "state_digest"
                            ),
                            "frozen_on_closed_candle": True,
                            "future_leakage_detected": False,
                        }
                latest_freeze = {
                    "closed_candle_key": close_key,
                    "order_index": current_order,
                    "closed_at_seconds": current_timestamp,
                    "closed_candle_time_proof": time_proof,
                    "studied_direction": selected_anchor.get(
                        "studied_direction"
                    ),
                    "contract_duration_seconds": selected_anchor.get(
                        "duration_seconds"
                    ),
                    "elapsed_seconds": selected_anchor.get(
                        "last_elapsed_seconds"
                    ),
                    "remaining_seconds": int(
                        selected_anchor.get("duration_seconds", 0) or 0
                    )
                    - int(selected_anchor.get("last_elapsed_seconds", 0) or 0),
                    "new_entry_eligible": (
                        duration.get("new_entry_eligible") is True
                        and admitted_anchor is selected_anchor
                    ),
                    "current_path_mru": selected_anchor.get("last_path_mru"),
                    "field_state_digest": field.snapshot().get("state_digest"),
                    "scenario_estimates": scenario_estimates,
                    **_safety_contract(),
                }
                latest_freeze["freeze_digest"] = _canonical_digest(latest_freeze)
                freeze_log = _rows(state.get("freeze_log"))
                freeze_log.append(latest_freeze)
                state["freeze_log"] = freeze_log[-_MAX_FREEZES:]

            identities.append(
                {
                    "closed_candle_key": close_key,
                    "closed_candle_sequence": close_sequence,
                    "closed_at_seconds": current_timestamp,
                    "closed_candle_time_proof": time_proof,
                    "observation_digest": observation_digest,
                }
            )
            state["observation_identities"] = identities[
                -_MAX_OBSERVATION_IDENTITIES:
            ]
            state["last_observation"] = {
                "closed_candle_key": close_key,
                "closed_candle_sequence": close_sequence,
                "closed_at_seconds": current_timestamp,
                "closed_candle_time_proof": time_proof,
                "observation_digest": observation_digest,
            }
            audit = _mapping(state.get("audit"))
            audit["latest_closed_candle_time_proof"] = time_proof
            state["audit"] = audit
            state["field_snapshot"] = field.snapshot()
            public = self._compact_public(
                state,
                field,
                duration=duration,
                latest_freeze=latest_freeze,
                discontinuity_censored=discontinuity_censored,
                forecast_context=forecast_context,
            )
            state["latest_public_study"] = public
            state["state_digest"] = _canonical_digest(state)
            write_json_atomic(path, state)
        return deepcopy(public)


def pending_path_clock_liquidity_v3(
    reason: object,
    *,
    contract_duration_seconds: object | None = None,
    candidate_direction: object = "",
    source_cadence_seconds: object = 300,
    forecast_context: Mapping[str, Any] | None = None,
    symbol: object = "",
    timeframe: object = "",
    closed_candle_key: object = "",
    closed_candle_sequence: object = 0,
) -> dict[str, Any]:
    cadence = _strict_nonnegative_integer(
        source_cadence_seconds,
        field="source_cadence_seconds",
    )
    if cadence < 30 or cadence > MAX_STUDIED_DURATION_SECONDS:
        raise PathClockLiquidityStoreValidationError(
            "source cadence must be between 30 and 7200 seconds"
        )
    duration = _duration_contract(
        contract_duration_seconds,
        source_cadence_seconds=cadence,
    )
    context = _mapping(forecast_context)
    lineage = {
        "symbol": symbol or _mapping(context.get("lineage")).get("symbol"),
        "timeframe": timeframe
        or _mapping(context.get("lineage")).get("timeframe"),
        "closed_candle_key": closed_candle_key
        or _mapping(context.get("lineage")).get("closed_candle_key"),
        "closed_candle_sequence": closed_candle_sequence,
    }
    forward_timing_forecast = build_hierarchical_forward_timing_forecast_v3(
        candidate_direction=(
            candidate_direction or context.get("candidate_direction")
        ),
        duration_contract=duration,
        source_cadence_seconds=cadence,
        directional_confidence=context.get("directional_confidence", 0.0),
        current_regime=context.get("current_regime", "UNKNOWN"),
        current_behavior=_mapping(context.get("current_behavior")),
        pair_profile=_mapping(context.get("pair_profile")),
        motif_lattice=_mapping(context.get("motif_lattice")),
        survival_network=_mapping(context.get("survival_network")),
        motif_trajectory_library=_mapping(
            context.get("motif_trajectory_library")
        ),
        exact_time_proven=False,
        exact_promotion_passed=False,
        lineage=lineage,
    )
    duration_eligible = duration.get("new_entry_eligible") is True
    forecast_available = (
        forward_timing_forecast.get("status") == "FORECAST_AVAILABLE"
    )
    result: dict[str, Any] = {
        "schema_version": PATH_CLOCK_LIQUIDITY_PUBLIC_SCHEMA_VERSION,
        "symbol": " ".join(str(lineage["symbol"] or "").strip().upper().split()),
        "timeframe": " ".join(
            str(lineage["timeframe"] or "").strip().upper().split()
        ),
        "closed_candle_key": str(lineage["closed_candle_key"] or "").strip(),
        "closed_candle_sequence": _integer_value(
            lineage["closed_candle_sequence"]
        ),
        "freshness_state": _mapping(
            forward_timing_forecast.get("lineage")
        ).get("freshness_state", "UNBOUND"),
        "status": "INSUFFICIENT_PROVEN_CLOSED_CANDLE_EVIDENCE",
        "reason": str(reason or "Exact closed-candle timing evidence is not ready.")[
            :320
        ],
        "duration_policy": duration,
        "new_entry_eligible": duration_eligible,
        "active_tracking_continues_below_floor": False,
        "active_anchor_count": 0,
        "active_anchor_count_below_900_seconds_remaining": 0,
        "trajectory_count": 0,
        "freeze_count": 0,
        "latest_field_state": {},
        "scenario_estimates": [],
        "best_supported_scenario": {},
        "forward_timing_forecast": forward_timing_forecast,
        "timing_read": {
            "status": (
                "FORWARD_ESTIMATE_ONLY"
                if forecast_available
                else "INSUFFICIENT_PROVEN_CLOSED_CANDLE_EVIDENCE"
            ),
            "state": (
                "FORECAST_AVAILABLE"
                if forecast_available
                else "INELIGIBLE"
                if not duration_eligible
                else "DIRECTION_UNRESOLVED"
            ),
            "reason": (
                "Exact wall-clock survival is unavailable; a separate "
                "closed-candle-relative forecast remains available."
                if forecast_available
                else str(reason or "Exact timing evidence is not ready.")[:320]
            ),
            "side": (
                forward_timing_forecast.get("candidate_direction")
                if forward_timing_forecast.get("candidate_direction")
                in {"UP", "DOWN"}
                else ""
            ),
            "studied_direction": (
                forward_timing_forecast.get("candidate_direction")
                if forward_timing_forecast.get("candidate_direction")
                in {"UP", "DOWN"}
                else ""
            ),
            "contract_duration_seconds": duration.get(
                "requested_duration_seconds"
            ),
            "candidate_horizon_seconds": duration.get(
                "requested_duration_seconds"
            ),
            "remaining_seconds": duration.get("requested_duration_seconds"),
            "support_count": 0,
            "survival_probability": None,
            "probability_worst_drawdown_still_ahead": None,
            "new_entry_eligible": duration_eligible,
            "timing_supports_entry": False,
            "timing_veto": not duration_eligible,
            "closed_candle_key": "",
            "observed_at": None,
            "valid_until": None,
            "promotion_gate": {
                "passed": False,
                "status": "INSUFFICIENT_REPLAY_CALIBRATION",
                "minimum_replays": 32,
                "support": {"baseline": 0, "candidate": 0, "passed": False},
                "all_axes_improved": False,
            },
            "forward_timing_forecast": deepcopy(forward_timing_forecast),
            **_safety_contract(),
        },
        "promotion_gate": {
            "passed": False,
            "status": "INSUFFICIENT_REPLAY_CALIBRATION",
            "minimum_replays": 32,
            "support": {"baseline": 0, "candidate": 0, "passed": False},
            "all_axes_improved": False,
            **_safety_contract(),
        },
        "pair_dna_partition": {},
        "time_proof_audit": {},
        "persistence_contract": {
            "raw_trajectories_in_pair_dna_json": False,
            "raw_trajectories_in_public_study": False,
            "dedicated_pair_side_store": True,
            "restart_safe": True,
            "idempotent_closed_candle_keys": True,
            "interpolates_missing_candles": False,
            "requires_resolver_bound_time_proof": True,
            "time_proof_schema": PROVEN_CLOSED_CANDLE_TIME_SCHEMA_VERSION,
            "raw_geometry_in_time_proof": False,
        },
        **_safety_contract(),
    }
    result["public_study_digest"] = _canonical_digest(result)
    return result


__all__ = [
    "PATH_CLOCK_LIQUIDITY_PUBLIC_SCHEMA_VERSION",
    "PATH_CLOCK_LIQUIDITY_SIDE_STORE_SCHEMA_VERSION",
    "PROVEN_CLOSED_CANDLE_TIME_SCHEMA_VERSION",
    "PathClockLiquiditySideStoreV3",
    "PathClockLiquidityStoreValidationError",
    "pending_path_clock_liquidity_v3",
]
