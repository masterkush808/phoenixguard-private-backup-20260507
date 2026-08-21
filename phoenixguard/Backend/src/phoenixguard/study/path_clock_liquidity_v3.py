"""Bounded Joint Path-Clock Liquidity Field studies for PhoenixGuard V3.

The field joins three pieces of historical evidence that must not be separated
when studying fixed-duration markets:

* price displacement in normalized median-range units;
* elapsed and remaining wall-clock time; and
* a five-dimensional, causally frozen liquidity state.

Only completed historical trajectories may enter the library.  A live field
state may be frozen only at a proven closed-candle key.  Forming-candle
wick/body asymmetry is accepted solely when its value was frozen no later than
that key's causal cutoff.  Outcomes following a freeze are replay evidence,
never inputs to the freeze itself.

All storage and queries are explicitly bounded and deterministic.  Outputs are
study evidence only: they cannot grant entry, order, broker-click, or execution
authority.  In particular, a duration or remaining horizon below fifteen
minutes is ineligible rather than rounded up or silently extrapolated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
from statistics import median
from threading import RLock
from typing import Any, cast

from phoenixguard.core.timing_policy_v3 import (
    MAXIMUM_STUDIED_TRADE_DURATION_SECONDS,
    MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS,
)


PATH_CLOCK_LIQUIDITY_SCHEMA_VERSION = "PG_PATH_CLOCK_LIQUIDITY_FIELD_V3"
PATH_CLOCK_TRAJECTORY_SCHEMA_VERSION = "PG_PATH_CLOCK_TRAJECTORY_V3"
PATH_CLOCK_FREEZE_SCHEMA_VERSION = "PG_PATH_CLOCK_LIQUIDITY_FREEZE_V3"
PATH_CLOCK_REPLAY_SCORE_SCHEMA_VERSION = "PG_PATH_CLOCK_REPLAY_SCORE_V3"
PATH_CLOCK_PROMOTION_GATE_SCHEMA_VERSION = "PG_PATH_CLOCK_PROMOTION_GATE_V3"
PATH_CLOCK_FORWARD_TIMING_FORECAST_SCHEMA_VERSION = (
    "PG_JPCLF_FORWARD_TIMING_FORECAST_V3"
)
PATH_CLOCK_FORWARD_PROBABILITY_SEMANTICS_VERSION = (
    "PG_JPCLF_FORWARD_PROBABILITY_SEMANTICS_V3"
)
PATH_CLOCK_PAIR_DNA_PARTITION_SCHEMA_VERSION = (
    "PG_PATH_CLOCK_LIQUIDITY_PAIR_DNA_PARTITION_V3"
)

MIN_ELIGIBLE_DURATION_SECONDS = MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
MAX_STUDIED_DURATION_SECONDS = MAXIMUM_STUDIED_TRADE_DURATION_SECONDS
DEFAULT_CLOCK_STEP_SECONDS = 30
DEFAULT_MAX_TRAJECTORIES = 1_000_000
DEFAULT_MAX_POINTS_PER_TRAJECTORY = 1_000_000
DEFAULT_MAX_FREEZES = 1_000_000
DEFAULT_MAX_NEIGHBORS = 1_000_000
DEFAULT_MAX_FIELD_ROWS = 1_000_000
MAX_RAW_POINTS_PER_TRAJECTORY = 1_000_000
MAX_REPLAY_RECORDS = 1_000_000
MAX_SWEEP_OUTCOMES_PER_REPLAY = 1_000_000
_LIQUIDITY_BIN_COUNT = 10

_COORDINATE_SPACE = "NORMALIZED_MEDIAN_RANGE"
_ORDER_DOMAINS = {
    "CLOSED_TIMESTAMP_V1",
    "SOURCE_CANDLE_CLOSE_ORDER",
    "TRACKER_EVENT_SEQUENCE_V3",
}
_DIRECTIONS = {"UP", "DOWN"}
_ASYMMETRY_SOURCES = {"CLOSED_CANDLE", "FORMING_CANDLE_AS_OF_CUTOFF"}
_LIQUIDITY_FIELDS = (
    "wick_entropy",
    "repeated_area_touches",
    "late_sweep_motif_distance",
    "wick_body_asymmetry",
    "object_copresence_density",
)
_PROMOTION_AXES = (
    "directional_accuracy",
    "timing_accuracy",
    "sweep_survival_rate",
    "calibration_score",
)
_FORECAST_TARGET_MRU = 0.5
_FORECAST_INVALIDATION_MRU = 0.5
_FORECAST_SWEEP_MRU = 0.25
_MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT = 3
_MIN_FORWARD_PAIR_TIMING_SUPPORT = 3
_MIN_FORWARD_LIVE_HISTORY_CANDLES = 8
_MIN_FORWARD_LIVE_COMPLETED_SEGMENTS = 2
_UNCONDITIONAL_SURVIVAL_OBJECT_TYPES = {"", "NONE", "ALL", "PAIR_STATE"}


class PathClockLiquidityValidationError(ValueError):
    """Raised when a JPCLF input violates a V3 causal or bounded contract."""


def _identity(value: object, *, field: str, maximum: int = 128) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise PathClockLiquidityValidationError(f"{field} is required")
    if len(text) > maximum:
        raise PathClockLiquidityValidationError(
            f"{field} exceeds {maximum} characters"
        )
    return text


def _finite(
    value: object,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool):
        raise PathClockLiquidityValidationError(f"{field} must be finite")
    try:
        result = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise PathClockLiquidityValidationError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise PathClockLiquidityValidationError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise PathClockLiquidityValidationError(
            f"{field} must be at least {minimum}"
        )
    if maximum is not None and result > maximum:
        raise PathClockLiquidityValidationError(
            f"{field} must be at most {maximum}"
        )
    return result


def _integer(
    value: object,
    *,
    field: str,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise PathClockLiquidityValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise PathClockLiquidityValidationError(
            f"{field} must be an integer >= {minimum}"
        ) from exc
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric < minimum:
        raise PathClockLiquidityValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    result = int(numeric)
    if maximum is not None and result > maximum:
        raise PathClockLiquidityValidationError(
            f"{field} cannot exceed {maximum}"
        )
    return result


def _required_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PathClockLiquidityValidationError(f"{field} must be a mapping")
    return dict(cast(Mapping[str, Any], value))


def _required_rows(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise PathClockLiquidityValidationError(
            f"{field} must be a sequence of mappings"
        )
    result: list[dict[str, Any]] = []
    for index, item in enumerate(cast(Sequence[object], value)):
        result.append(_required_mapping(item, field=f"{field}[{index}]"))
    return result


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PathClockLiquidityValidationError(
            "JPCLF evidence must contain finite JSON values"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _rounded(value: float, places: int = 8) -> float:
    return round(float(value), places)


def _quantile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _safety_contract() -> dict[str, object]:
    return {
        "study_only": True,
        "observation_only": True,
        "closed_candle_causal": True,
        "establishes_causation": False,
        "execution_authority": False,
        "broker_click_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


def _optional_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _optional_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        return []
    return [
        dict(cast(Mapping[str, Any], row))
        for row in cast(Sequence[object], value)
        if isinstance(row, Mapping)
    ]


def _safe_number(value: object, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return float(default)
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _safe_count(value: object, default: int = 0) -> int:
    parsed = _safe_number(value, float(default))
    return max(0, int(math.floor(parsed)))


def _optional_unit_interval(value: object) -> float | None:
    """Return a declared unit-interval value without inventing a zero."""

    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):
        return None
    return max(0.0, min(1.0, parsed))


def _optional_nonnegative_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0.0:
        return None
    return parsed


def _forecast_direction(value: object) -> str:
    token = " ".join(str(value or "").strip().upper().split())
    if token in {"BUY", "BULL", "BULLISH", "UP", "UPTREND", "UP_SWING"}:
        return "UP"
    if token in {
        "SELL",
        "BEAR",
        "BEARISH",
        "DOWN",
        "DOWNTREND",
        "DOWN_SWING",
    }:
        return "DOWN"
    return ""


def _aligned_forecast_candles(
    value: float,
    *,
    minimum: int,
    maximum: int,
) -> int:
    return max(minimum, min(maximum, int(math.ceil(max(0.0, value)))))


def _window_point(candles: int, cadence_seconds: int) -> dict[str, object]:
    seconds = max(0, int(candles)) * cadence_seconds
    return {
        "seconds": seconds,
        "minutes": _rounded(seconds / 60.0, 2),
        "candles": max(0, int(candles)),
    }


def _current_motif_token(
    motif_lattice: Mapping[str, Any],
) -> tuple[str, int]:
    candle_count = _safe_count(motif_lattice.get("closed_candle_count"))
    latest_index = candle_count - 1
    if latest_index < 0:
        return "", -1
    candidates: list[tuple[int, int, str]] = []
    for level in _optional_rows(motif_lattice.get("levels")):
        level_index = _safe_count(level.get("level"))
        for node in _optional_rows(level.get("nodes")):
            span = _optional_mapping(node.get("span"))
            if _safe_count(span.get("end_index"), -1) != latest_index:
                continue
            token = str(node.get("motif_token") or "").strip().upper()
            if token:
                candidates.append(
                    (
                        level_index,
                        _safe_count(span.get("candle_count")),
                        token,
                    )
                )
    if not candidates:
        return "", -1
    selected = max(candidates, key=lambda row: (row[0], row[1], row[2]))
    return selected[2], selected[0]


def _pair_behavior_without_coordinate_prefix(
    value: Mapping[str, Any],
) -> dict[str, object]:
    """Aggregate Pair DNA behavior keys by the suffix after the last ``|``."""

    raw_counts = _optional_mapping(value.get("segment_counts"))
    raw_candle_sums = _optional_mapping(value.get("segment_candle_sum"))
    raw_duration_sums = _optional_mapping(value.get("segment_duration_sum"))
    raw_averages = _optional_mapping(value.get("segment_averages"))
    counts: dict[str, int] = {}
    for token, count in raw_counts.items():
        state = str(token).rsplit("|", maxsplit=1)[-1].upper()
        counts[state] = counts.get(state, 0) + _safe_count(count)

    weighted_averages: dict[str, dict[str, float]] = {}
    average_tokens = set(raw_averages) | set(raw_counts)
    for token in sorted(average_tokens):
        state = str(token).rsplit("|", maxsplit=1)[-1].upper()
        row = _optional_mapping(raw_averages.get(token))
        weight = max(1, _safe_count(raw_counts.get(token), 1))
        candles = (
            _safe_number(row.get("candles"))
            if row
            else _safe_number(raw_candle_sums.get(token)) / weight
        )
        duration_seconds = (
            _safe_number(row.get("duration_seconds"))
            if row
            else _safe_number(raw_duration_sums.get(token)) / weight
        )
        aggregate = weighted_averages.setdefault(
            state,
            {"weight": 0.0, "candles": 0.0, "duration_seconds": 0.0},
        )
        aggregate["weight"] += weight
        aggregate["candles"] += candles * weight
        aggregate["duration_seconds"] += duration_seconds * weight
    averages = {
        state: {
            "candles": _rounded(row["candles"] / row["weight"], 4),
            "duration_seconds": _rounded(
                row["duration_seconds"] / row["weight"], 2
            ),
        }
        for state, row in weighted_averages.items()
        if row["weight"] > 0.0
    }

    raw_transition_counts = _optional_mapping(value.get("transition_counts"))
    transition_counts: dict[str, int] = {}
    for token, count in raw_transition_counts.items():
        transition = str(token).rsplit("|", maxsplit=1)[-1].upper()
        transition_counts[transition] = (
            transition_counts.get(transition, 0) + _safe_count(count)
        )
    outgoing: dict[str, int] = {}
    for token, count in transition_counts.items():
        source = token.split("->", maxsplit=1)[0]
        outgoing[source] = outgoing.get(source, 0) + count
    transitions = {
        token: count / outgoing[token.split("->", maxsplit=1)[0]]
        for token, count in transition_counts.items()
        if outgoing.get(token.split("->", maxsplit=1)[0], 0) > 0
    }
    if not transitions:
        for token, probability in _optional_mapping(
            value.get("transition_probabilities")
        ).items():
            transition = str(token).rsplit("|", maxsplit=1)[-1].upper()
            transitions[transition] = max(
                0.0, min(1.0, _safe_number(probability))
            )
    return {
        "segment_counts": counts,
        "segment_averages": averages,
        "transition_counts": transition_counts,
        "transition_probabilities": transitions,
    }


def _curve_window(
    curve: Mapping[str, Any],
    *,
    minimum_candles: int,
    horizon_candles: int,
) -> tuple[tuple[int, int, int], float] | None:
    points = _optional_rows(curve.get("curve"))
    if not points:
        return None

    def threshold(value: float, fallback: int) -> int:
        return next(
            (
                _safe_count(point.get("closed_candles"), fallback)
                for point in points
                if _safe_number(point.get("cumulative_event_probability"))
                >= value
            ),
            fallback,
        )

    earliest = _aligned_forecast_candles(
        threshold(0.20, horizon_candles),
        minimum=minimum_candles,
        maximum=horizon_candles,
    )
    central = _aligned_forecast_candles(
        threshold(0.50, horizon_candles),
        minimum=earliest,
        maximum=horizon_candles,
    )
    latest = _aligned_forecast_candles(
        threshold(0.80, horizon_candles),
        minimum=central,
        maximum=horizon_candles,
    )
    available = [
        point
        for point in points
        if _safe_count(point.get("closed_candles")) <= horizon_candles
    ]
    event_probability = (
        _safe_number(available[-1].get("cumulative_event_probability"))
        if available
        else 0.0
    )
    return (earliest, central, latest), max(0.0, min(1.0, event_probability))


def _motif_observations(
    trajectory_library: Mapping[str, Any],
    *,
    motif_token: str,
    direction: str,
    minimum_candles: int,
    horizon_candles: int,
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for entry in _optional_rows(trajectory_library.get("entries")):
        if str(entry.get("motif_token") or "").strip().upper() != motif_token:
            continue
        if _forecast_direction(entry.get("reference_direction")) != direction:
            continue
        target_candles: int | None = None
        sweep_before_move = False
        invalidated = False
        rest_candles = 0
        for point in _optional_rows(entry.get("points")):
            offset = _safe_count(point.get("offset_closed_candles"))
            if offset <= 0 or offset > horizon_candles:
                continue
            favorable = _safe_number(
                point.get("cumulative_favorable_excursion_in_median_ranges")
            )
            adverse = _safe_number(
                point.get("cumulative_adverse_excursion_in_median_ranges")
            )
            if target_candles is None:
                sweep_before_move = bool(
                    sweep_before_move or adverse >= _FORECAST_SWEEP_MRU
                )
                invalidated = bool(
                    invalidated or adverse >= _FORECAST_INVALIDATION_MRU
                )
                if str(point.get("state") or "").upper() == "REST":
                    rest_candles += 1
                if favorable >= _FORECAST_TARGET_MRU:
                    target_candles = offset
        considered_target = bool(
            target_candles is not None and target_candles >= minimum_candles
        )
        observations.append(
            {
                "success": considered_target and not invalidated,
                "target_candles": target_candles if considered_target else None,
                "sweep_before_move": sweep_before_move,
                "rest_candles": rest_candles,
            }
        )
    return observations


def build_hierarchical_forward_timing_forecast_v3(
    *,
    candidate_direction: object,
    duration_contract: Mapping[str, Any],
    source_cadence_seconds: object,
    directional_confidence: object = 0.0,
    current_regime: object = "UNKNOWN",
    current_behavior: Mapping[str, Any] | None = None,
    pair_profile: Mapping[str, Any] | None = None,
    motif_lattice: Mapping[str, Any] | None = None,
    survival_network: Mapping[str, Any] | None = None,
    motif_trajectory_library: Mapping[str, Any] | None = None,
    exact_jpclf_estimate: Mapping[str, Any] | None = None,
    exact_time_proven: bool = False,
    exact_promotion_passed: bool = False,
    lineage: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Publish a bounded forecast even while exact pair JPCLF matures.

    Evidence is selected through the declared hierarchy and shrunk toward its
    broader parent.  The final pooled value is an explicitly uncalibrated
    neutral policy prior, never disguised as observed pair evidence.
    """

    cadence = _integer(
        source_cadence_seconds,
        field="source_cadence_seconds",
        minimum=30,
        maximum=MAX_STUDIED_DURATION_SECONDS,
    )
    duration_row = dict(duration_contract)
    duration = _safe_count(
        duration_row.get(
            "requested_duration_seconds",
            duration_row.get("duration_seconds"),
        )
    )
    duration_eligible = bool(
        duration_row.get("new_entry_eligible") is True
        or duration_row.get("eligible") is True
    )
    direction = _forecast_direction(candidate_direction)
    regime = " ".join(
        str(current_regime or "UNKNOWN").strip().upper().split()
    )[:64]
    if not regime:
        regime = "UNKNOWN"
    raw_lineage = _optional_mapping(lineage)
    anchor_close_epoch_seconds = (
        _optional_nonnegative_number(
            raw_lineage.get("anchor_close_epoch_seconds")
        )
        if exact_time_proven
        else None
    )
    source_lineage: dict[str, object] = {
        "symbol": " ".join(
            str(raw_lineage.get("symbol") or "").strip().upper().split()
        )[:64],
        "timeframe": " ".join(
            str(raw_lineage.get("timeframe") or "").strip().upper().split()
        )[:32],
        "closed_candle_key": str(
            raw_lineage.get("closed_candle_key") or ""
        ).strip()[:256],
        "closed_candle_sequence": _safe_count(
            raw_lineage.get("closed_candle_sequence")
        ),
        "source_cadence_seconds": cadence,
    }
    if anchor_close_epoch_seconds is not None:
        source_lineage["anchor_close_epoch_seconds"] = _rounded(
            anchor_close_epoch_seconds,
            6,
        )
    lineage_bound = bool(
        source_lineage["symbol"]
        and source_lineage["timeframe"]
        and source_lineage["closed_candle_key"]
    )
    source_lineage["lineage_bound"] = lineage_bound
    source_lineage["freshness_state"] = (
        "CURRENT_CLOSED_CANDLE" if lineage_bound else "UNBOUND"
    )
    source_lineage["lineage_digest"] = _digest(source_lineage)
    horizon_candles = max(1, duration // cadence) if duration else 0
    minimum_candles = int(math.ceil(MIN_ELIGIBLE_DURATION_SECONDS / cadence))
    forecast_available = bool(
        duration_eligible
        and duration >= MIN_ELIGIBLE_DURATION_SECONDS
        and duration <= MAX_STUDIED_DURATION_SECONDS
        and horizon_candles >= minimum_candles
        and direction
    )
    status = (
        "FORECAST_AVAILABLE"
        if forecast_available
        else "DIRECTION_UNRESOLVED"
        if duration_eligible and not direction
        else "INELIGIBLE_DURATION"
    )
    if not forecast_available:
        result: dict[str, object] = {
            "schema_version": PATH_CLOCK_FORWARD_TIMING_FORECAST_SCHEMA_VERSION,
            "probability_semantics_version": (
                PATH_CLOCK_FORWARD_PROBABILITY_SEMANTICS_VERSION
            ),
            "status": status,
            "candidate_direction": direction or "UNRESOLVED",
            "current_regime": regime,
            "forecast_horizon_seconds": duration or None,
            "lineage": source_lineage,
            "move_window": {
                "earliest": None,
                "central": None,
                "latest": None,
                "basis": "CLOSED_CANDLE_RELATIVE",
                "exact_wall_clock_proven": False,
                "anchor_time_proven": False,
                "estimate_calibrated": False,
                "event_definition": "UNAVAILABLE",
                "relative_to": "CLOSED_CANDLE_ANCHOR",
                "rolling_wall_clock": False,
            },
            "probability": {
                "value": None,
                "confidence": None,
                "metric": "UNAVAILABLE",
                "source_tier": "NONE",
                "calibration_grade": "UNRATED",
                "calibrated": False,
                "support_count": 0,
                "shrinkage_weight": None,
                "compatibility_alias_for": "event_likelihood",
            },
            "directional_model": {
                "candidate_direction": direction or "UNRESOLVED",
                "score": None,
                "source": "CURRENT_DIRECTIONAL_ENSEMBLE",
                "is_event_likelihood": False,
            },
            "timing_estimate": {
                "source_tier": "NONE",
                "basis": "NO_ELIGIBLE_DURATION_WINDOW",
                "event_definition": "UNAVAILABLE",
                "empirical_timing_evidence": False,
                "support_count": 0,
                "current_sequence_candle_count": 0,
                "current_target_state": "UNRESOLVED",
                "components": {},
            },
            "event_likelihood": {
                "value": None,
                "event": "UNAVAILABLE",
                "source_tier": "NONE",
                "support_count": 0,
                "calibrated": False,
            },
            "evidence_confidence": {
                "value": None,
                "basis": "NO_EMPIRICAL_OUTCOME_SUPPORT",
                "support_count": 0,
            },
            "state_transition_estimate": {
                "value": None,
                "transition": "UNAVAILABLE",
                "target_count": 0,
                "support_count": 0,
                "minimum_support": _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT,
                "eligible": False,
            },
            "stop_survival": {
                "value": None,
                "source_tier": "NONE",
                "support_count": 0,
                "exact_wall_clock_proven": False,
                "calibrated": False,
            },
            "adverse_excursion_risk": {
                "worst_drawdown_still_ahead_probability": None,
                "source_tier": "NONE",
                "support_count": 0,
            },
            "expected_pre_move": {
                "state": "NOT_STUDIED",
                "rest_window_candles": None,
                "rest_window_minutes": None,
                "rest_source_tier": "NONE",
                "rest_support_count": 0,
                "rest_zero_observed": False,
                "sweep_probability": None,
                "sweep_risk": "UNKNOWN",
            },
            "invalidation": {
                "direction": "",
                "condition": "No timing forecast is active.",
                "adverse_distance_mru": _FORECAST_INVALIDATION_MRU,
                "expires_after_seconds": duration or None,
                "expires_after_candles": horizon_candles or None,
                "closed_candles_only": True,
            },
            "enter_now": {
                "permission": False,
                "permission_determined": False,
                "forecast_horizon_eligible": duration_eligible,
                "timing_advisory": status,
                "reason": (
                    "Entry permission remains an independent contract; this "
                    "forecast cannot grant it."
                ),
                "permission_source": "INDEPENDENT_ENTRY_CONTRACT_REQUIRED",
            },
            "evidence_hierarchy": {
                "selected_tier": "NONE",
                "attempted_tiers": [],
                "pooled_prior_is_empirical_pair_evidence": False,
                "pooled_prior_published_as_likelihood": False,
            },
            **_safety_contract(),
        }
        result["forecast_digest"] = _digest(result)
        return result

    prior_window = (
        minimum_candles,
        _aligned_forecast_candles(
            (minimum_candles + horizon_candles) / 2.0,
            minimum=minimum_candles,
            maximum=horizon_candles,
        ),
        horizon_candles,
    )
    selected_window = prior_window
    timing_source_tier = "POLICY_WINDOW"
    timing_basis = "CANONICAL_DURATION_POLICY_WINDOW"
    timing_support_count = 0
    timing_empirical = False
    timing_weight = 0.0
    event_likelihood: float | None = None
    event_kind = "UNAVAILABLE"
    event_source_tier = "NONE"
    event_support_count = 0
    event_calibrated = False
    evidence_confidence: float | None = None
    sweep_probability: float | None = None
    sweep_source_tier = "NONE"
    sweep_support_count = 0
    stop_survival_probability: float | None = None
    stop_survival_support_count = 0
    adverse_excursion_probability: float | None = None
    rest_center: float | None = None
    rest_source_tier = "NONE"
    rest_support_count = 0
    rest_zero_observed = False
    timing_event_definition = "UNAVAILABLE"
    timing_components: dict[str, object] = {}
    attempted: list[dict[str, object]] = []

    raw_behavior = _optional_mapping(
        _optional_mapping(pair_profile).get("behavior")
    )
    behavior = _pair_behavior_without_coordinate_prefix(raw_behavior)
    segment_averages = _optional_mapping(behavior.get("segment_averages"))
    segment_counts = _optional_mapping(behavior.get("segment_counts"))
    transition_counts = _optional_mapping(behavior.get("transition_counts"))
    current = _optional_mapping(_optional_mapping(current_behavior).get("current_state"))
    current_state = str(current.get("state") or "REST").strip().upper()
    target_state = "UP_SWING" if direction == "UP" else "DOWN_SWING"
    opposite_state = "DOWN_SWING" if target_state == "UP_SWING" else "UP_SWING"
    target_move_already_active = current_state == target_state
    target_state_support = _safe_count(segment_counts.get(target_state))

    sequence_count = _safe_count(
        _optional_mapping(current_behavior).get("candle_count")
    )
    swing_summary = _optional_mapping(
        _optional_mapping(current_behavior).get("swing_summary")
    )
    target_summary = _optional_mapping(
        swing_summary.get("up" if direction == "UP" else "down")
    )
    target_sequence_segment_count = _safe_count(
        target_summary.get("segment_count")
    )
    target_sequence_average_candles = _safe_number(
        target_summary.get("average_candles")
    )
    target_sequence_maximum_candles = max(
        target_sequence_average_candles,
        _safe_number(target_summary.get("maximum_candles")),
    )
    opposite_summary = _optional_mapping(
        swing_summary.get("down" if direction == "UP" else "up")
    )
    opposite_sequence_segment_count = _safe_count(
        opposite_summary.get("segment_count")
    )
    opposite_sequence_average_candles = _safe_number(
        opposite_summary.get("average_candles")
    )
    rest_summary = _optional_mapping(
        _optional_mapping(current_behavior).get("rest_summary")
    )
    rest_sequence_segment_count = _safe_count(rest_summary.get("segment_count"))
    rest_sequence_average_candles = _safe_number(
        rest_summary.get("average_candles")
    )
    rest_sequence_maximum_candles = max(
        rest_sequence_average_candles,
        _safe_number(rest_summary.get("maximum_candles")),
    )
    live_completed_segment_count = (
        target_sequence_segment_count
        + opposite_sequence_segment_count
        + rest_sequence_segment_count
    )
    current_sequence_state_supported = bool(
        (
            current_state == "REST"
            and rest_sequence_segment_count > 0
        )
        or (
            current_state == opposite_state
            and opposite_sequence_segment_count > 0
            and opposite_sequence_average_candles > 0.0
            and rest_sequence_segment_count > 0
        )
        or (
            current_state == target_state
            and target_sequence_segment_count > 0
            and target_sequence_average_candles > 0.0
            and rest_sequence_segment_count > 0
        )
    )
    directional_model_score = _optional_unit_interval(directional_confidence)
    sequence_available = bool(
        sequence_count >= _MIN_FORWARD_LIVE_HISTORY_CANDLES
        and live_completed_segment_count
        >= _MIN_FORWARD_LIVE_COMPLETED_SEGMENTS
        and target_sequence_segment_count > 0
        and target_sequence_average_candles > 0.0
        and current_sequence_state_supported
        and directional_model_score is not None
        and directional_model_score > 0.0
    )
    attempted.append(
        {
            "tier": "LIVE_M5_SEQUENCE",
            "available": sequence_available,
            "support_count": 0,
            "current_sequence_candle_count": sequence_count,
            "minimum_history_candles": _MIN_FORWARD_LIVE_HISTORY_CANDLES,
            "completed_segment_count": live_completed_segment_count,
            "minimum_completed_segments": (
                _MIN_FORWARD_LIVE_COMPLETED_SEGMENTS
            ),
            "target_segment_count": target_sequence_segment_count,
            "rest_segment_count": rest_sequence_segment_count,
            "current_state_supported": current_sequence_state_supported,
            "target_move_already_active": target_move_already_active,
            "basis": "CURRENT_CLOSED_CANDLE_BEHAVIOR_AND_DIRECTIONAL_ENSEMBLE",
        }
    )
    if sequence_available:
        current_count = _safe_number(current.get("candle_count"), 0.0)
        if current_state == target_state:
            current_remaining = max(
                0.0,
                target_sequence_average_candles - current_count,
            )
            expected_intermediate_rest = rest_sequence_average_candles
            local_center = current_remaining + expected_intermediate_rest
            target_distribution_spread = max(
                0.0,
                target_sequence_maximum_candles
                - target_sequence_average_candles,
            )
            rest_distribution_spread = max(
                0.0,
                rest_sequence_maximum_candles
                - rest_sequence_average_candles,
            )
            local_uncertainty = max(
                1.0,
                target_distribution_spread + rest_distribution_spread,
            )
            rest_center = expected_intermediate_rest
        elif current_state == "REST":
            current_remaining = max(
                0.0,
                rest_sequence_average_candles - current_count,
            )
            expected_intermediate_rest = 0.0
            local_center = current_remaining
            local_uncertainty = max(
                1.0,
                rest_sequence_average_candles / 2.0,
            )
            rest_center = current_remaining
        else:
            current_remaining = max(
                0.0,
                opposite_sequence_average_candles - current_count,
            )
            expected_intermediate_rest = rest_sequence_average_candles
            local_center = current_remaining + expected_intermediate_rest
            local_uncertainty = max(
                1.0,
                (
                    opposite_sequence_average_candles
                    + rest_sequence_average_candles
                )
                / 2.0,
            )
            rest_center = expected_intermediate_rest
        live_window = (
            _aligned_forecast_candles(
                local_center - local_uncertainty,
                minimum=minimum_candles,
                maximum=horizon_candles,
            ),
            _aligned_forecast_candles(
                local_center,
                minimum=minimum_candles,
                maximum=horizon_candles,
            ),
            _aligned_forecast_candles(
                local_center + local_uncertainty,
                minimum=minimum_candles,
                maximum=horizon_candles,
            ),
        )
        live_weight = min(0.75, sequence_count / (sequence_count + 8.0))
        selected_window = tuple(
            _aligned_forecast_candles(
                (1.0 - live_weight) * prior + live_weight * empirical,
                minimum=minimum_candles,
                maximum=horizon_candles,
            )
            for prior, empirical in zip(
                selected_window, live_window, strict=True
            )
        )
        timing_source_tier = "LIVE_M5_SEQUENCE"
        timing_basis = "CURRENT_CLOSED_CANDLE_SEQUENCE_AND_DECLARED_CADENCE"
        timing_support_count = 0
        timing_empirical = False
        timing_weight = live_weight
        timing_event_definition = (
            "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"
            if target_move_already_active
            else "TARGET_MOVE_START_AFTER_ANCHOR"
        )
        timing_basis = (
            "CURRENT_ACTIVE_TARGET_COMPLETION_AND_OBSERVED_REST"
            if target_move_already_active
            else timing_basis
        )
        timing_components = {
            "current_state": current_state,
            "current_state_candle_count": _rounded(current_count, 4),
            "current_state_remaining_candles": _rounded(
                current_remaining,
                4,
            ),
            "expected_intermediate_rest_candles": _rounded(
                expected_intermediate_rest,
                4,
            ),
            "wait_to_target_start_candles": _rounded(local_center, 4),
            "target_duration_included": False,
            "active_target_remaining_included": target_move_already_active,
        }
        if target_move_already_active:
            timing_components.update(
                {
                    "target_distribution": {
                        "segment_count": target_sequence_segment_count,
                        "average_candles": _rounded(
                            target_sequence_average_candles,
                            4,
                        ),
                        "maximum_candles": _rounded(
                            target_sequence_maximum_candles,
                            4,
                        ),
                    },
                    "rest_distribution": {
                        "segment_count": rest_sequence_segment_count,
                        "average_candles": _rounded(
                            rest_sequence_average_candles,
                            4,
                        ),
                        "maximum_candles": _rounded(
                            rest_sequence_maximum_candles,
                            4,
                        ),
                    },
                    "distribution_uncertainty_candles": _rounded(
                        local_uncertainty,
                        4,
                    ),
                }
            )
        rest_source_tier = "LIVE_BEHAVIOR"
        rest_support_count = rest_sequence_segment_count
        rest_zero_observed = rest_center == 0.0
    target_state_average_candles = _safe_number(
        _optional_mapping(segment_averages.get(target_state)).get("candles")
    )
    pair_rest_support = _safe_count(segment_counts.get("REST"))
    pair_rest_average = _safe_number(
        _optional_mapping(segment_averages.get("REST")).get("candles")
    )
    pair_opposite_support = _safe_count(segment_counts.get(opposite_state))
    pair_opposite_average = _safe_number(
        _optional_mapping(segment_averages.get(opposite_state)).get("candles")
    )
    pair_prerequisite_support = (
        pair_rest_support
        if current_state == "REST"
        else min(pair_opposite_support, pair_rest_support)
        if current_state == opposite_state
        else 0
    )
    pair_effective_support = min(
        target_state_support,
        pair_prerequisite_support,
    )
    pair_prerequisite_duration_available = bool(
        (
            current_state == "REST"
            and pair_rest_support > 0
            and pair_rest_average >= 0.0
        )
        or (
            current_state == opposite_state
            and pair_opposite_support > 0
            and pair_opposite_average > 0.0
            and pair_rest_support > 0
            and pair_rest_average >= 0.0
        )
    )
    pair_available = bool(
        not target_move_already_active
        and target_state in segment_averages
        and pair_effective_support >= _MIN_FORWARD_PAIR_TIMING_SUPPORT
        and target_state_average_candles > 0.0
        and pair_prerequisite_duration_available
    )
    transition_key = f"{current_state}->{target_state}"
    transition_target_count = _safe_count(transition_counts.get(transition_key))
    transition_support_count = sum(
        _safe_count(count)
        for token, count in transition_counts.items()
        if str(token).split("->", maxsplit=1)[0].upper() == current_state
    )
    transition_estimate = (
        transition_target_count / transition_support_count
        if transition_support_count > 0
        else None
    )
    attempted.append(
        {
            "tier": "PAIR",
            "available": pair_available,
            "support_count": pair_effective_support,
            "minimum_support": _MIN_FORWARD_PAIR_TIMING_SUPPORT,
            "basis": "PAIR_DNA_TARGET_START_WAIT",
            "target_state": target_state,
            "target_state_support": target_state_support,
            "prerequisite_state_support": pair_prerequisite_support,
            "target_move_already_active": target_move_already_active,
        }
    )
    if pair_available:
        current_count = _safe_number(current.get("candle_count"), 0.0)
        if current_state == "REST":
            current_remaining = max(
                0.0,
                pair_rest_average - current_count,
            )
            expected_intermediate_rest = 0.0
            central_raw = current_remaining
            pair_uncertainty = max(1.0, pair_rest_average / 2.0)
            rest_center = current_remaining
        else:
            current_remaining = max(
                0.0,
                pair_opposite_average - current_count,
            )
            expected_intermediate_rest = pair_rest_average
            central_raw = current_remaining + expected_intermediate_rest
            pair_uncertainty = max(
                1.0,
                (pair_opposite_average + pair_rest_average) / 2.0,
            )
            rest_center = expected_intermediate_rest
        pair_window = (
            _aligned_forecast_candles(
                central_raw - pair_uncertainty,
                minimum=minimum_candles,
                maximum=horizon_candles,
            ),
            _aligned_forecast_candles(
                central_raw,
                minimum=minimum_candles,
                maximum=horizon_candles,
            ),
            _aligned_forecast_candles(
                central_raw + pair_uncertainty,
                minimum=minimum_candles,
                maximum=horizon_candles,
            ),
        )
        pair_window = (
            pair_window[0],
            max(pair_window[0], pair_window[1]),
            max(pair_window[1], pair_window[2]),
        )
        pair_weight = pair_effective_support / (pair_effective_support + 16.0)
        selected_window = tuple(
            _aligned_forecast_candles(
                (1.0 - pair_weight) * prior + pair_weight * empirical,
                minimum=minimum_candles,
                maximum=horizon_candles,
            )
            for prior, empirical in zip(prior_window, pair_window, strict=True)
        )
        timing_source_tier = "PAIR"
        timing_basis = "PAIR_DNA_TARGET_START_WAIT"
        timing_support_count = pair_effective_support
        timing_empirical = True
        timing_weight = pair_weight
        timing_event_definition = "TARGET_MOVE_START_AFTER_ANCHOR"
        timing_components = {
            "current_state": current_state,
            "current_state_candle_count": _rounded(current_count, 4),
            "current_state_remaining_candles": _rounded(
                current_remaining,
                4,
            ),
            "expected_intermediate_rest_candles": _rounded(
                expected_intermediate_rest,
                4,
            ),
            "wait_to_target_start_candles": _rounded(central_raw, 4),
            "target_duration_included": False,
        }
        rest_source_tier = "PAIR_DNA_REST_DURATION"
        rest_support_count = pair_rest_support
        rest_zero_observed = rest_center == 0.0

    survival = _optional_mapping(survival_network)
    event_type = (
        "REST_END"
        if current_state == "REST"
        else "DIRECTION_CHANGE"
        if current_state != target_state
        else "NEXT_SWING"
    )
    scoped_curves = [
        curve
        for curve in _optional_rows(survival.get("curves"))
        if str(curve.get("origin_state") or "").upper() == current_state
        and str(curve.get("event_type") or "").upper() == event_type
    ]
    unconditional_curves = [
        curve
        for curve in scoped_curves
        if " ".join(str(curve.get("object_type") or "").upper().split())
        in _UNCONDITIONAL_SURVIVAL_OBJECT_TYPES
    ]
    supported_curves = [
        curve
        for curve in unconditional_curves
        if str(curve.get("status") or "").upper() == "SUPPORTED"
        and _safe_count(curve.get("minimum_support")) > 0
        and _safe_count(curve.get("support"))
        >= max(
            _safe_count(curve.get("minimum_support")),
            _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT,
        )
    ]
    matching_curve: dict[str, Any] = (
        max(
            supported_curves,
            key=lambda curve: (
                _safe_count(curve.get("support")),
                str(curve.get("object_type") or ""),
            ),
        )
        if supported_curves
        else {}
    )
    curve_support = _safe_count(matching_curve.get("support"))
    curve_minimum_support = _safe_count(matching_curve.get("minimum_support"))
    curve_evidence = _curve_window(
        matching_curve,
        minimum_candles=minimum_candles,
        horizon_candles=horizon_candles,
    )
    regime_available = bool(curve_evidence and curve_support > 0)
    attempted.insert(
        0,
        {
            "tier": "PAIR_STATE_SURVIVAL",
            "available": regime_available,
            "support_count": curve_support,
            "minimum_support": curve_minimum_support,
            "basis": "PAIR_CLOSED_CANDLE_STATE_SURVIVAL_CURVE",
            "unconditional_only": True,
            "rejected_object_conditioned_count": (
                len(scoped_curves) - len(unconditional_curves)
            ),
            "rejected_unsupported_count": (
                len(unconditional_curves) - len(supported_curves)
            ),
        },
    )
    if curve_evidence is not None and curve_support > 0:
        curve_window, event_probability = curve_evidence
        regime_weight = curve_support / (curve_support + 8.0)
        selected_window = tuple(
            _aligned_forecast_candles(
                (1.0 - regime_weight) * parent + regime_weight * empirical,
                minimum=minimum_candles,
                maximum=horizon_candles,
            )
            for parent, empirical in zip(
                selected_window, curve_window, strict=True
            )
        )
        timing_source_tier = "PAIR_STATE_SURVIVAL"
        timing_basis = "PAIR_CLOSED_CANDLE_STATE_SURVIVAL_CURVE"
        timing_support_count = curve_support
        timing_empirical = True
        timing_weight = regime_weight
        timing_event_definition = f"{event_type}_AFTER_ANCHOR"
        timing_components = {
            "origin_state": current_state,
            "survival_event_type": event_type,
            "target_duration_included": False,
        }
        event_likelihood = event_probability
        event_kind = f"{event_type}_BY_FORECAST_HORIZON"
        event_source_tier = "PAIR_STATE_SURVIVAL"
        event_support_count = curve_support
        evidence_confidence = regime_weight

    motif = _optional_mapping(motif_lattice)
    motif_token, motif_level = _current_motif_token(motif)
    motif_observations = _motif_observations(
        _optional_mapping(motif_trajectory_library),
        motif_token=motif_token,
        direction=direction,
        minimum_candles=minimum_candles,
        horizon_candles=horizon_candles,
    ) if motif_token else []
    motif_support = len(motif_observations)
    motif_timing_eligible = (
        motif_support >= _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT
    )
    attempted.insert(
        0,
        {
            "tier": "PAIR_MOTIF",
            "available": motif_timing_eligible,
            "raw_match_available": motif_support > 0,
            "support_count": motif_support,
            "minimum_outcome_support": _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT,
            "timing_window_eligible": motif_timing_eligible,
            "outcome_probability_eligible": motif_timing_eligible,
            "sparse_diagnostic_only": bool(
                motif_support > 0 and not motif_timing_eligible
            ),
            "basis": "MATCHED_HISTORICAL_MOTIF_FOLLOW_THROUGH",
            "motif_token": motif_token,
            "motif_level": motif_level,
        },
    )
    if motif_observations:
        motif_weight = motif_support / (motif_support + 4.0)
        motif_probability = sum(
            row["success"] is True for row in motif_observations
        ) / motif_support
        target_values = [
            float(cast(Any, row["target_candles"]))
            for row in motif_observations
            if row.get("target_candles") is not None
        ]
        if target_values and motif_timing_eligible:
            motif_window = (
                _quantile(target_values, 0.10),
                _quantile(target_values, 0.50),
                _quantile(target_values, 0.90),
            )
            selected_window = tuple(
                _aligned_forecast_candles(
                    (1.0 - motif_weight) * parent
                    + motif_weight * empirical,
                    minimum=minimum_candles,
                    maximum=horizon_candles,
                )
                for parent, empirical in zip(
                    selected_window, motif_window, strict=True
                )
            )
            timing_source_tier = "PAIR_MOTIF"
            timing_basis = "MATCHED_HISTORICAL_MOTIF_FOLLOW_THROUGH"
            timing_support_count = motif_support
            timing_empirical = True
            timing_weight = motif_weight
            timing_event_definition = "TARGET_MOVE_THRESHOLD_AFTER_ANCHOR"
            timing_components = {
                "target_threshold_mru": _FORECAST_TARGET_MRU,
                "matched_motif_support": motif_support,
                "target_duration_included": False,
            }
        if motif_timing_eligible:
            rest_center = median(
                float(cast(Any, row["rest_candles"]))
                for row in motif_observations
            )
            rest_source_tier = "PAIR_MOTIF"
            rest_support_count = motif_support
            rest_zero_observed = rest_center == 0.0
            sweep_probability = (
                sum(
                    row["sweep_before_move"] is True
                    for row in motif_observations
                )
                / motif_support
            )
            sweep_source_tier = "PAIR_MOTIF"
            sweep_support_count = motif_support
            event_likelihood = motif_probability
            event_kind = "MOTIF_TARGET_FOLLOW_THROUGH_WITHIN_FORECAST_HORIZON"
            event_source_tier = "PAIR_MOTIF"
            event_support_count = motif_support
            evidence_confidence = motif_weight

    exact = _optional_mapping(exact_jpclf_estimate)
    exact_support = _safe_count(exact.get("support_count"))
    exact_minimum_support = max(
        _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT,
        _safe_count(exact.get("minimum_support")),
    )
    exact_probability_value = _optional_unit_interval(
        exact.get("survival_probability")
    )
    exact_available = bool(
        exact_probability_value is not None
        and exact_support >= exact_minimum_support
        and exact_time_proven
        and anchor_close_epoch_seconds is not None
    )
    exact_window_used = False
    exact_raw_window_seconds: tuple[float, float, float] | None = None
    if exact_available:
        exact_weight = 1.0 if exact_promotion_passed else exact_support / (
            exact_support + 8.0
        )
        stop_survival_probability = exact_probability_value
        stop_survival_support_count = exact_support
        target_times = _optional_mapping(exact.get("target_time_seconds"))
        exact_window_seconds = (
            _safe_number(target_times.get("p10")),
            _safe_number(target_times.get("median")),
            _safe_number(target_times.get("p90")),
        )
        if all(value > 0.0 for value in exact_window_seconds):
            exact_window_used = True
            exact_raw_window_seconds = exact_window_seconds
            exact_window = tuple(
                _aligned_forecast_candles(
                    value / cadence,
                    minimum=minimum_candles,
                    maximum=horizon_candles,
                )
                for value in exact_window_seconds
            )
            selected_window = tuple(
                _aligned_forecast_candles(
                    (1.0 - exact_weight) * parent
                    + exact_weight * empirical,
                    minimum=minimum_candles,
                    maximum=horizon_candles,
                )
                for parent, empirical in zip(
                    selected_window, exact_window, strict=True
                )
            )
            timing_source_tier = "EXACT_JPCLF"
            timing_basis = "EXACT_PATH_CLOCK_LIQUIDITY_TARGET_TIME"
            timing_support_count = exact_support
            timing_empirical = True
            timing_weight = exact_weight
            timing_event_definition = (
                "EXACT_TARGET_MOVE_THRESHOLD_AFTER_ANCHOR"
            )
            timing_components = {
                "target_threshold_mru": _optional_nonnegative_number(
                    exact.get("move_size_mru")
                ),
                "raw_target_time_seconds": {
                    "p10": _rounded(exact_window_seconds[0], 6),
                    "median": _rounded(exact_window_seconds[1], 6),
                    "p90": _rounded(exact_window_seconds[2], 6),
                },
                "target_duration_included": False,
            }
        adverse_excursion_probability = _optional_unit_interval(
            exact.get("probability_worst_drawdown_still_ahead")
        )
        attempted.insert(
            0,
            {
                "tier": "EXACT_JPCLF",
                "available": True,
                "support_count": exact_support,
                "minimum_support": exact_minimum_support,
                "basis": (
                    "EXACT_PATH_CLOCK_LIQUIDITY_TARGET_TIME_AND_STOP_SURVIVAL"
                    if exact_window_used
                    else "EXACT_PATH_CLOCK_LIQUIDITY_STOP_SURVIVAL_ONLY"
                ),
            },
        )
    elif exact_support > 0:
        attempted.insert(
            0,
            {
                "tier": "EXACT_JPCLF",
                "available": False,
                "support_count": exact_support,
                "minimum_support": exact_minimum_support,
                "basis": (
                    "EXACT_SUPPORT_FLOOR_NOT_MET"
                    if exact_support < exact_minimum_support
                    else "EXACT_WALL_CLOCK_ANCHOR_REQUIRED"
                ),
            },
        )

    earliest, central, latest = selected_window
    central = max(earliest, central)
    latest = max(central, latest)
    if sweep_probability is not None:
        sweep_probability = max(0.0, min(1.0, sweep_probability))
    rest_min: int | None = None
    rest_central: int | None = None
    rest_latest: int | None = None
    if rest_center is not None:
        rest_min = max(0, int(math.floor(rest_center - 1.0)))
        rest_central = max(rest_min, int(round(rest_center)))
        rest_latest = max(
            rest_central,
            int(math.ceil(rest_center + 1.0)),
        )
    event_calibration_grade = (
        "EMPIRICAL_UNCALIBRATED" if event_support_count > 0 else "UNRATED"
    )
    invalidation_direction = "DOWN" if direction == "UP" else "UP"
    sweep_risk = (
        "UNRATED"
        if sweep_probability is None
        else "HIGH"
        if sweep_probability >= 0.65
        else "MEDIUM"
        if sweep_probability >= 0.35
        else "LOW"
    )
    attempted.append(
        {
            "tier": "POLICY_WINDOW",
            "available": True,
            "support_count": 0,
            "basis": "CANONICAL_DURATION_POLICY_WINDOW_ONLY",
        }
    )
    tier_order = {
        "EXACT_JPCLF": 0,
        "PAIR_MOTIF_JPCLF": 0,
        "PAIR_JPCLF": 0,
        "PAIR_MOTIF": 1,
        "PAIR_STATE_SURVIVAL": 2,
        "PAIR": 3,
        "LIVE_M5_SEQUENCE": 4,
        "POLICY_WINDOW": 5,
    }
    attempted.sort(key=lambda row: tier_order.get(str(row["tier"]), 99))
    timing_window_available = timing_source_tier != "POLICY_WINDOW"
    public_status = (
        "FORECAST_AVAILABLE"
        if timing_window_available
        else "TARGET_MOVE_ALREADY_ACTIVE"
        if target_move_already_active
        else "TIMING_UNRATED"
    )
    if timing_window_available:
        expected_state = (
            "SWEEP_THEN_MOVE"
            if sweep_probability is not None and sweep_risk == "HIGH"
            else "PRE_MOVE_STATE_UNRATED"
            if rest_center is None
            else "DIRECT_MOVE_POSSIBLE"
            if rest_zero_observed
            else "REST_THEN_MOVE"
            if rest_central is not None and rest_central > 0
            else "PRE_MOVE_STATE_UNRATED"
        )
    else:
        expected_state = (
            "TARGET_MOVE_ALREADY_ACTIVE"
            if target_move_already_active
            else "TIMING_UNRATED"
        )
    move_window: dict[str, object] = {
        "earliest": (
            _window_point(earliest, cadence)
            if timing_window_available
            else None
        ),
        "central": (
            _window_point(central, cadence)
            if timing_window_available
            else None
        ),
        "latest": (
            _window_point(latest, cadence)
            if timing_window_available
            else None
        ),
        "basis": (
            "EXACT_EMPIRICAL_CLOCK_ANCHORED_WINDOW"
            if exact_window_used and exact_promotion_passed
            else "CLOCK_ANCHORED_SHRUNK_ESTIMATE"
            if exact_window_used
            else "CLOSED_CANDLE_RELATIVE_DECLARED_CADENCE"
            if timing_window_available
            else "TARGET_MOVE_ALREADY_ACTIVE_AT_ANCHOR"
            if target_move_already_active
            else "POLICY_HORIZON_ONLY_NO_MOVE_WINDOW"
        ),
        "exact_wall_clock_proven": exact_window_used,
        "anchor_time_proven": exact_window_used,
        "estimate_calibrated": bool(
            exact_window_used and exact_promotion_passed
        ),
        "event_definition": (
            timing_event_definition
            if timing_window_available
            else "TARGET_MOVE_ALREADY_ACTIVE_AT_ANCHOR"
            if target_move_already_active
            else "UNAVAILABLE"
        ),
        "relative_to": "CLOSED_CANDLE_ANCHOR",
        "rolling_wall_clock": False,
    }
    if exact_window_used and anchor_close_epoch_seconds is not None:
        move_window.update(
            {
                "anchor_close_epoch_seconds": _rounded(
                    anchor_close_epoch_seconds,
                    6,
                ),
                "target_window_start_epoch_seconds": _rounded(
                    anchor_close_epoch_seconds + earliest * cadence,
                    6,
                ),
                "target_window_central_epoch_seconds": _rounded(
                    anchor_close_epoch_seconds + central * cadence,
                    6,
                ),
                "target_window_end_epoch_seconds": _rounded(
                    anchor_close_epoch_seconds + latest * cadence,
                    6,
                ),
                "raw_empirical_target_time_seconds": (
                    {
                        "p10": _rounded(exact_raw_window_seconds[0], 6),
                        "median": _rounded(exact_raw_window_seconds[1], 6),
                        "p90": _rounded(exact_raw_window_seconds[2], 6),
                    }
                    if exact_raw_window_seconds is not None
                    else None
                ),
            }
        )
    rest_window_candles: dict[str, int] | None = None
    rest_window_minutes: dict[str, float] | None = None
    if (
        timing_window_available
        and rest_min is not None
        and rest_central is not None
        and rest_latest is not None
    ):
        rest_window_candles = {
            "earliest": rest_min,
            "central": rest_central,
            "latest": rest_latest,
        }
        rest_window_minutes = {
            "earliest": _rounded(rest_min * cadence / 60.0, 2),
            "central": _rounded(rest_central * cadence / 60.0, 2),
            "latest": _rounded(rest_latest * cadence / 60.0, 2),
        }
    result = {
        "schema_version": PATH_CLOCK_FORWARD_TIMING_FORECAST_SCHEMA_VERSION,
        "probability_semantics_version": (
            PATH_CLOCK_FORWARD_PROBABILITY_SEMANTICS_VERSION
        ),
        "status": public_status,
        "candidate_direction": direction,
        "current_regime": regime,
        "forecast_horizon_seconds": duration,
        "lineage": source_lineage,
        "move_window": move_window,
        "directional_model": {
            "candidate_direction": direction,
            "score": (
                _rounded(directional_model_score, 6)
                if directional_model_score is not None
                else None
            ),
            "source": "CURRENT_DIRECTIONAL_ENSEMBLE",
            "is_event_likelihood": False,
        },
        "timing_estimate": {
            "source_tier": timing_source_tier,
            "basis": timing_basis,
            "event_definition": (
                timing_event_definition
                if timing_window_available
                else "TARGET_MOVE_ALREADY_ACTIVE_AT_ANCHOR"
                if target_move_already_active
                else "UNAVAILABLE"
            ),
            "empirical_timing_evidence": timing_empirical,
            "support_count": timing_support_count,
            "current_sequence_candle_count": sequence_count,
            "current_target_state": (
                "ALREADY_ACTIVE_AT_ANCHOR"
                if target_move_already_active
                else "NOT_ACTIVE_AT_ANCHOR"
            ),
            "components": timing_components if timing_window_available else {},
            "window_blend_weight": _rounded(timing_weight, 6),
        },
        "event_likelihood": {
            "value": (
                _rounded(event_likelihood, 6)
                if event_likelihood is not None and event_support_count > 0
                else None
            ),
            "event": event_kind,
            "source_tier": event_source_tier,
            "support_count": event_support_count,
            "calibrated": event_calibrated,
        },
        "evidence_confidence": {
            "value": (
                _rounded(evidence_confidence, 6)
                if evidence_confidence is not None and event_support_count > 0
                else None
            ),
            "basis": (
                "EMPIRICAL_OUTCOME_SUPPORT_SATURATION"
                if event_support_count > 0
                else "NO_EMPIRICAL_OUTCOME_SUPPORT"
            ),
            "support_count": event_support_count,
        },
        "probability": {
            "value": (
                _rounded(event_likelihood, 6)
                if event_likelihood is not None and event_support_count > 0
                else None
            ),
            "confidence": (
                _rounded(evidence_confidence, 6)
                if evidence_confidence is not None and event_support_count > 0
                else None
            ),
            "metric": event_kind,
            "source_tier": event_source_tier,
            "calibration_grade": event_calibration_grade,
            "calibrated": event_calibrated,
            "support_count": event_support_count,
            "shrinkage_weight": None,
            "compatibility_alias_for": "event_likelihood",
        },
        "state_transition_estimate": {
            "value": (
                _rounded(transition_estimate, 6)
                if transition_estimate is not None
                and transition_support_count
                >= _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT
                else None
            ),
            "transition": transition_key,
            "target_count": transition_target_count,
            "support_count": transition_support_count,
            "minimum_support": _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT,
            "eligible": (
                transition_support_count
                >= _MIN_FORWARD_EMPIRICAL_OUTCOME_SUPPORT
            ),
            "source_tier": "PAIR_STATE_TRANSITIONS",
            "is_directional_likelihood": False,
        },
        "stop_survival": {
            "value": (
                _rounded(stop_survival_probability, 6)
                if stop_survival_probability is not None
                and stop_survival_support_count > 0
                else None
            ),
            "source_tier": (
                "EXACT_JPCLF" if stop_survival_support_count > 0 else "NONE"
            ),
            "support_count": stop_survival_support_count,
            "exact_wall_clock_proven": bool(
                stop_survival_support_count >= exact_minimum_support
                and exact_time_proven
                and anchor_close_epoch_seconds is not None
            ),
            "calibrated": bool(
                stop_survival_support_count > 0 and exact_promotion_passed
            ),
            "stop_distance_mru": (
                _rounded(value, 6)
                if (
                    value := _optional_nonnegative_number(
                        exact.get("stop_distance_mru")
                    )
                )
                is not None
                else None
            ),
            "move_size_mru": (
                _rounded(value, 6)
                if (
                    value := _optional_nonnegative_number(
                        exact.get("move_size_mru")
                    )
                )
                is not None
                else None
            ),
        },
        "adverse_excursion_risk": {
            "worst_drawdown_still_ahead_probability": (
                _rounded(adverse_excursion_probability, 6)
                if adverse_excursion_probability is not None
                and stop_survival_support_count > 0
                else None
            ),
            "source_tier": (
                "EXACT_JPCLF" if stop_survival_support_count > 0 else "NONE"
            ),
            "support_count": stop_survival_support_count,
        },
        "expected_pre_move": {
            "state": expected_state,
            "rest_window_candles": rest_window_candles,
            "rest_window_minutes": rest_window_minutes,
            "rest_source_tier": rest_source_tier,
            "rest_support_count": rest_support_count,
            "rest_zero_observed": rest_zero_observed,
            "sweep_probability": (
                _rounded(sweep_probability, 6)
                if sweep_probability is not None and sweep_support_count > 0
                else None
            ),
            "sweep_risk": sweep_risk,
            "sweep_source_tier": sweep_source_tier,
            "sweep_support_count": sweep_support_count,
        },
        "invalidation": {
            "direction": invalidation_direction,
            "condition": (
                f"Invalidate after a proven closed candle changes the studied "
                f"direction to {invalidation_direction} or moves at least "
                f"{_FORECAST_INVALIDATION_MRU:.2f} MRU against the anchor."
            ),
            "adverse_distance_mru": _FORECAST_INVALIDATION_MRU,
            "expires_after_seconds": duration if timing_window_available else None,
            "expires_after_candles": (
                horizon_candles if timing_window_available else None
            ),
            "closed_candles_only": True,
        },
        "enter_now": {
            "permission": False,
            "permission_determined": False,
            "forecast_horizon_eligible": True,
            "timing_advisory": (
                "FORWARD_WINDOW_AVAILABLE"
                if timing_window_available
                else public_status
            ),
            "reason": (
                "This forecast describes timing only. Entry permission must be "
                "open in the independent entry contract."
            ),
            "permission_source": "INDEPENDENT_ENTRY_CONTRACT_REQUIRED",
        },
        "evidence_hierarchy": {
            "selected_tier": timing_source_tier,
            "attempted_tiers": attempted,
            "pooled_prior": {
                "version": "PG_JPCLF_POLICY_WINDOW_V3",
                "direction_probability": None,
                "calibrated": False,
                "empirical_pair_evidence": False,
                "published_as_likelihood": False,
                "used_for": "FORECAST_HORIZON_BOUNDS_ONLY",
            },
            "closed_candle_relative_evidence_allowed_without_exact_time_proof": True,
            "exact_survival_claim_requires_exact_time_proof": True,
        },
        **_safety_contract(),
    }
    result["forecast_digest"] = _digest(result)
    return result


def _validate_scope(
    row: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    coordinate_space: str,
    order_domain: str,
    field: str,
) -> None:
    if _identity(row.get("symbol"), field=f"{field}.symbol", maximum=64) != symbol:
        raise PathClockLiquidityValidationError(f"{field}.symbol scope mismatch")
    if (
        _identity(row.get("timeframe"), field=f"{field}.timeframe", maximum=32)
        != timeframe
    ):
        raise PathClockLiquidityValidationError(f"{field}.timeframe scope mismatch")
    if (
        _identity(
            row.get("coordinate_space"),
            field=f"{field}.coordinate_space",
            maximum=64,
        )
        != coordinate_space
    ):
        raise PathClockLiquidityValidationError(
            f"{field}.coordinate_space scope mismatch"
        )
    if (
        _identity(
            row.get("order_domain"),
            field=f"{field}.order_domain",
            maximum=64,
        )
        != order_domain
    ):
        raise PathClockLiquidityValidationError(
            f"{field}.order_domain scope mismatch"
        )


def _liquidity_state(
    value: object,
    *,
    field: str,
    causal_order_index: int,
    causal_cutoff_seconds: float,
) -> dict[str, object]:
    row = _required_mapping(value, field=field)
    result: dict[str, object] = {
        "wick_entropy": _finite(
            row.get("wick_entropy"),
            field=f"{field}.wick_entropy",
            minimum=0.0,
            maximum=1.0,
        ),
        "repeated_area_touches": _integer(
            row.get("repeated_area_touches"),
            field=f"{field}.repeated_area_touches",
            maximum=64,
        ),
        "late_sweep_motif_distance": _finite(
            row.get("late_sweep_motif_distance"),
            field=f"{field}.late_sweep_motif_distance",
            minimum=0.0,
            maximum=1_000.0,
        ),
        "wick_body_asymmetry": _finite(
            row.get("wick_body_asymmetry"),
            field=f"{field}.wick_body_asymmetry",
            minimum=-64.0,
            maximum=64.0,
        ),
        "object_copresence_density": _finite(
            row.get("object_copresence_density"),
            field=f"{field}.object_copresence_density",
            minimum=0.0,
            maximum=1.0,
        ),
    }
    as_of_order = _integer(
        row.get("as_of_order_index"),
        field=f"{field}.as_of_order_index",
    )
    as_of_seconds = _finite(
        row.get("as_of_seconds"),
        field=f"{field}.as_of_seconds",
    )
    if as_of_order > causal_order_index or as_of_seconds > causal_cutoff_seconds:
        raise PathClockLiquidityValidationError(
            f"{field} contains evidence after the causal cutoff"
        )
    source = _identity(
        row.get("wick_body_asymmetry_source"),
        field=f"{field}.wick_body_asymmetry_source",
        maximum=48,
    )
    if source not in _ASYMMETRY_SOURCES:
        raise PathClockLiquidityValidationError(
            f"{field}.wick_body_asymmetry_source is unsupported"
        )
    if source == "CLOSED_CANDLE":
        if row.get("source_candle_closed") is not True:
            raise PathClockLiquidityValidationError(
                f"{field} closed-candle asymmetry lacks close proof"
            )
    elif row.get("frozen_before_outcome") is not True:
        raise PathClockLiquidityValidationError(
            f"{field} forming-candle asymmetry lacks a causal freeze proof"
        )
    result.update(
        {
            "as_of_order_index": as_of_order,
            "as_of_seconds": _rounded(as_of_seconds, 6),
            "wick_body_asymmetry_source": source,
            "source_candle_closed": source == "CLOSED_CANDLE",
            "frozen_before_outcome": True,
        }
    )
    return result


def _liquidity_vector(row: Mapping[str, Any]) -> tuple[float, ...]:
    """Return bounded dimensionless coordinates for deterministic distances."""

    return (
        float(row["wick_entropy"]),
        min(1.0, float(row["repeated_area_touches"]) / 8.0),
        float(row["late_sweep_motif_distance"])
        / (1.0 + float(row["late_sweep_motif_distance"])),
        (math.tanh(float(row["wick_body_asymmetry"]) / 4.0) + 1.0) / 2.0,
        float(row["object_copresence_density"]),
    )


def _liquidity_bin(row: Mapping[str, Any]) -> tuple[int, ...]:
    """Quantize every normalized liquidity coordinate without collapsing it."""

    return tuple(
        min(
            _LIQUIDITY_BIN_COUNT - 1,
            max(0, int(value * _LIQUIDITY_BIN_COUNT)),
        )
        for value in _liquidity_vector(row)
    )


def _liquidity_distance(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> float:
    left_vector = _liquidity_vector(left)
    right_vector = _liquidity_vector(right)
    return math.sqrt(
        sum((left_value - right_value) ** 2 for left_value, right_value in zip(left_vector, right_vector, strict=True))
        / len(_LIQUIDITY_FIELDS)
    )


def _interpolated_path(points: Sequence[Mapping[str, Any]], elapsed: float) -> float:
    if elapsed <= float(points[0]["elapsed_seconds"]):
        return float(points[0]["path_mru"])
    for left, right in zip(points, points[1:], strict=False):
        left_time = float(left["elapsed_seconds"])
        right_time = float(right["elapsed_seconds"])
        if elapsed <= right_time:
            fraction = (elapsed - left_time) / (right_time - left_time)
            return float(left["path_mru"]) + fraction * (
                float(right["path_mru"]) - float(left["path_mru"])
            )
    return float(points[-1]["path_mru"])


def _resample_points(
    raw_points: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: int,
    clock_step_seconds: int,
) -> list[dict[str, object]]:
    grid_times = list(range(0, duration_seconds + 1, clock_step_seconds))
    if grid_times[-1] != duration_seconds:
        grid_times.append(duration_seconds)
    result: list[dict[str, object]] = []
    previous_time = -1.0
    cumulative_high = 0.0
    cumulative_low = 0.0
    for elapsed in grid_times:
        path = _interpolated_path(raw_points, float(elapsed))
        interval_rows = [
            row
            for row in raw_points
            if previous_time < float(row["elapsed_seconds"]) <= elapsed
        ]
        interval_high = max(
            [path, *(float(row["high_mru"]) for row in interval_rows)]
        )
        interval_low = min(
            [path, *(float(row["low_mru"]) for row in interval_rows)]
        )
        cumulative_high = max(cumulative_high, interval_high)
        cumulative_low = min(cumulative_low, interval_low)
        result.append(
            {
                "elapsed_seconds": elapsed,
                "remaining_seconds": duration_seconds - elapsed,
                "path_mru": _rounded(path),
                "interval_high_mru": _rounded(interval_high),
                "interval_low_mru": _rounded(interval_low),
                "cumulative_high_mru": _rounded(cumulative_high),
                "cumulative_low_mru": _rounded(cumulative_low),
            }
        )
        previous_time = float(elapsed)
    return result


def _directional_extremes(
    points: Sequence[Mapping[str, Any]], *, direction: str, start_index: int = 0
) -> tuple[float, float, int | None, int | None]:
    sign = 1.0 if direction == "UP" else -1.0
    baseline = float(points[start_index]["path_mru"])
    maximum_favorable = 0.0
    maximum_adverse = 0.0
    favorable_index: int | None = None
    adverse_index: int | None = None
    for index in range(start_index + 1, len(points)):
        point = points[index]
        high = float(point["interval_high_mru"])
        low = float(point["interval_low_mru"])
        directional_high = max(sign * (high - baseline), sign * (low - baseline))
        directional_low = min(sign * (high - baseline), sign * (low - baseline))
        if directional_high > maximum_favorable:
            maximum_favorable = directional_high
            favorable_index = index
        if -directional_low > maximum_adverse:
            maximum_adverse = -directional_low
            adverse_index = index
    return maximum_adverse, maximum_favorable, adverse_index, favorable_index


class JointPathClockLiquidityFieldV3:
    """Pair-scoped empirical path-clock-liquidity study with hard bounds."""

    def __init__(
        self,
        *,
        symbol: object,
        timeframe: object,
        coordinate_space: object,
        order_domain: object,
        clock_step_seconds: int = DEFAULT_CLOCK_STEP_SECONDS,
        max_trajectories: int = DEFAULT_MAX_TRAJECTORIES,
        max_points_per_trajectory: int = DEFAULT_MAX_POINTS_PER_TRAJECTORY,
        max_freezes: int = DEFAULT_MAX_FREEZES,
        max_neighbors: int = DEFAULT_MAX_NEIGHBORS,
    ) -> None:
        self.symbol = _identity(symbol, field="symbol", maximum=64)
        self.timeframe = _identity(timeframe, field="timeframe", maximum=32)
        self.coordinate_space = _identity(
            coordinate_space, field="coordinate_space", maximum=64
        )
        if self.coordinate_space != _COORDINATE_SPACE:
            raise PathClockLiquidityValidationError(
                "coordinate_space must be NORMALIZED_MEDIAN_RANGE"
            )
        self.order_domain = _identity(
            order_domain, field="order_domain", maximum=64
        )
        if self.order_domain not in _ORDER_DOMAINS:
            raise PathClockLiquidityValidationError(
                "order_domain must prove stable closed-candle order"
            )
        self.clock_step_seconds = _integer(
            clock_step_seconds,
            field="clock_step_seconds",
            minimum=1,
            maximum=MIN_ELIGIBLE_DURATION_SECONDS,
        )
        self.max_trajectories = _integer(
            max_trajectories,
            field="max_trajectories",
            minimum=1,
            maximum=DEFAULT_MAX_TRAJECTORIES,
        )
        self.max_points_per_trajectory = _integer(
            max_points_per_trajectory,
            field="max_points_per_trajectory",
            minimum=2,
            maximum=DEFAULT_MAX_POINTS_PER_TRAJECTORY,
        )
        self.max_freezes = _integer(
            max_freezes,
            field="max_freezes",
            minimum=1,
            maximum=DEFAULT_MAX_FREEZES,
        )
        self.max_neighbors = _integer(
            max_neighbors,
            field="max_neighbors",
            minimum=1,
            maximum=DEFAULT_MAX_NEIGHBORS,
        )
        if (
            self.clock_step_seconds * (self.max_points_per_trajectory - 1)
            < MIN_ELIGIBLE_DURATION_SECONDS
        ):
            raise PathClockLiquidityValidationError(
                "point capacity cannot represent the 900-second minimum horizon"
            )
        self._trajectories: list[dict[str, Any]] = []
        self._trajectory_ids: set[str] = set()
        self._freezes: list[dict[str, Any]] = []
        self._freeze_keys: set[str] = set()
        self._last_freeze_order_index: int | None = None
        self._lock = RLock()

    def _scope(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "coordinate_space": self.coordinate_space,
            "order_domain": self.order_domain,
        }

    def add_trajectory(self, value: Mapping[str, Any]) -> dict[str, object]:
        """Validate, clock-align, and retain one completed historical path."""

        row = dict(value)
        _validate_scope(
            row,
            symbol=self.symbol,
            timeframe=self.timeframe,
            coordinate_space=self.coordinate_space,
            order_domain=self.order_domain,
            field="trajectory",
        )
        if row.get("study_only") is not True or row.get("completed") is not True:
            raise PathClockLiquidityValidationError(
                "trajectory must be completed study-only history"
            )
        trajectory_id = _identity(
            row.get("trajectory_id"), field="trajectory.trajectory_id", maximum=256
        )
        anchor = _required_mapping(row.get("anchor"), field="trajectory.anchor")
        if anchor.get("closed") is not True:
            raise PathClockLiquidityValidationError(
                "trajectory.anchor must be a proven closed candle"
            )
        anchor_key = _identity(
            anchor.get("closed_candle_key"),
            field="trajectory.anchor.closed_candle_key",
            maximum=256,
        )
        anchor_order = _integer(
            anchor.get("order_index"), field="trajectory.anchor.order_index"
        )
        anchor_seconds = _finite(
            anchor.get("closed_at_seconds"),
            field="trajectory.anchor.closed_at_seconds",
        )
        duration = _integer(
            row.get("duration_seconds"),
            field="trajectory.duration_seconds",
            minimum=MIN_ELIGIBLE_DURATION_SECONDS,
            maximum=MAX_STUDIED_DURATION_SECONDS,
        )
        if duration > self.clock_step_seconds * (
            self.max_points_per_trajectory - 1
        ):
            raise PathClockLiquidityValidationError(
                "trajectory exceeds bounded common-clock point capacity"
            )
        source_cadence = _integer(
            row.get("source_cadence_seconds"),
            field="trajectory.source_cadence_seconds",
            minimum=1,
            maximum=MAX_STUDIED_DURATION_SECONDS,
        )
        exact_subcandle_timestamps = row.get(
            "exact_subcandle_timestamps_proven"
        )
        if not isinstance(exact_subcandle_timestamps, bool):
            raise PathClockLiquidityValidationError(
                "trajectory.exact_subcandle_timestamps_proven must be boolean"
            )
        if self.clock_step_seconds < source_cadence and not exact_subcandle_timestamps:
            raise PathClockLiquidityValidationError(
                "common clock cannot be finer than source cadence without exact "
                "sub-candle timestamp proof"
            )
        direction = _identity(
            row.get("studied_direction"),
            field="trajectory.studied_direction",
            maximum=8,
        )
        if direction not in _DIRECTIONS:
            raise PathClockLiquidityValidationError(
                "trajectory.studied_direction must be UP or DOWN"
            )
        liquidity = _liquidity_state(
            row.get("liquidity_state"),
            field="trajectory.liquidity_state",
            causal_order_index=anchor_order,
            causal_cutoff_seconds=anchor_seconds,
        )
        raw = _required_rows(row.get("points"), field="trajectory.points")
        if len(raw) < 2 or len(raw) > MAX_RAW_POINTS_PER_TRAJECTORY:
            raise PathClockLiquidityValidationError(
                "trajectory.points must contain between 2 and "
                f"{MAX_RAW_POINTS_PER_TRAJECTORY} samples"
            )
        parsed: list[dict[str, float]] = []
        previous_elapsed = -1.0
        for index, point in enumerate(raw):
            elapsed = _finite(
                point.get("elapsed_seconds"),
                field=f"trajectory.points[{index}].elapsed_seconds",
                minimum=0.0,
                maximum=float(duration),
            )
            if elapsed <= previous_elapsed:
                raise PathClockLiquidityValidationError(
                    "trajectory point elapsed_seconds must increase strictly"
                )
            path = _finite(
                point.get("path_mru"),
                field=f"trajectory.points[{index}].path_mru",
                minimum=-10_000.0,
                maximum=10_000.0,
            )
            high = _finite(
                point.get("high_mru", path),
                field=f"trajectory.points[{index}].high_mru",
                minimum=-10_000.0,
                maximum=10_000.0,
            )
            low = _finite(
                point.get("low_mru", path),
                field=f"trajectory.points[{index}].low_mru",
                minimum=-10_000.0,
                maximum=10_000.0,
            )
            if low > min(path, high) or high < max(path, low):
                raise PathClockLiquidityValidationError(
                    f"trajectory.points[{index}] high/low contradict path_mru"
                )
            parsed.append(
                {
                    "elapsed_seconds": elapsed,
                    "path_mru": path,
                    "high_mru": high,
                    "low_mru": low,
                }
            )
            previous_elapsed = elapsed
        if parsed[0]["elapsed_seconds"] != 0.0 or abs(parsed[0]["path_mru"]) > 1e-9:
            raise PathClockLiquidityValidationError(
                "trajectory must begin at elapsed_seconds=0 and path_mru=0"
            )
        if abs(parsed[-1]["elapsed_seconds"] - duration) > 1e-6:
            raise PathClockLiquidityValidationError(
                "trajectory must end at duration_seconds"
            )
        maximum_observed_gap = max(
            current["elapsed_seconds"] - previous["elapsed_seconds"]
            for previous, current in zip(parsed, parsed[1:], strict=False)
        )
        allowed_gap = (
            self.clock_step_seconds
            if self.clock_step_seconds < source_cadence
            else max(self.clock_step_seconds, source_cadence)
        )
        if maximum_observed_gap > allowed_gap:
            raise PathClockLiquidityValidationError(
                "trajectory samples are too sparse for the declared common clock"
            )
        points = _resample_points(
            parsed,
            duration_seconds=duration,
            clock_step_seconds=self.clock_step_seconds,
        )
        if len(points) > self.max_points_per_trajectory:
            raise PathClockLiquidityValidationError(
                "resampled trajectory exceeds bounded point capacity"
            )
        terminal = float(cast(Any, points[-1]["path_mru"]))
        final_direction = (
            "UP" if terminal > 1e-9 else "DOWN" if terminal < -1e-9 else "FLAT"
        )
        mae, mfe, _, _ = _directional_extremes(points, direction=direction)
        stored: dict[str, Any] = {
            "schema_version": PATH_CLOCK_TRAJECTORY_SCHEMA_VERSION,
            "trajectory_id": trajectory_id,
            **self._scope(),
            "anchor": {
                "closed_candle_key": anchor_key,
                "order_index": anchor_order,
                "closed_at_seconds": _rounded(anchor_seconds, 6),
                "closed": True,
            },
            "duration_seconds": duration,
            "clock_step_seconds": self.clock_step_seconds,
            "source_cadence_seconds": source_cadence,
            "exact_subcandle_timestamps_proven": exact_subcandle_timestamps,
            "intrabar_event_order": "UNKNOWN_FAIL_CLOSED",
            "studied_direction": direction,
            "final_direction": final_direction,
            "liquidity_state": liquidity,
            "maximum_adverse_excursion_mru": _rounded(mae),
            "maximum_favorable_excursion_mru": _rounded(mfe),
            "points": points,
            **_safety_contract(),
        }
        stored["trajectory_digest"] = _digest(stored)
        with self._lock:
            if trajectory_id in self._trajectory_ids:
                existing = next(
                    trajectory
                    for trajectory in self._trajectories
                    if trajectory["trajectory_id"] == trajectory_id
                )
                if existing.get("trajectory_digest") == stored["trajectory_digest"]:
                    return deepcopy(existing)
                raise PathClockLiquidityValidationError(
                    "trajectory_id conflicts with different historical evidence"
                )
            if len(self._trajectories) >= self.max_trajectories:
                raise PathClockLiquidityValidationError(
                    "trajectory library capacity reached"
                )
            self._trajectories.append(stored)
            self._trajectory_ids.add(trajectory_id)
        return deepcopy(stored)

    def _eligible_neighbors(
        self,
        *,
        direction: str,
        contract_duration_seconds: int,
        elapsed_seconds: int,
        current_path_mru: float,
        liquidity_state: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        remaining = contract_duration_seconds - elapsed_seconds
        candidates: list[dict[str, Any]] = []
        for trajectory in self._trajectories:
            if trajectory["studied_direction"] != direction:
                continue
            if int(trajectory["duration_seconds"]) < remaining:
                continue
            points = cast(list[dict[str, Any]], trajectory["points"])
            aligned_elapsed = int(trajectory["duration_seconds"]) - remaining
            start_index = min(
                range(len(points)),
                key=lambda index: (
                    abs(int(points[index]["elapsed_seconds"]) - aligned_elapsed),
                    index,
                ),
            )
            aligned_path = float(points[start_index]["path_mru"])
            path_distance = abs(aligned_path - current_path_mru)
            liquidity_distance = _liquidity_distance(
                liquidity_state,
                cast(Mapping[str, Any], trajectory["liquidity_state"]),
            )
            clock_error = abs(
                int(points[start_index]["remaining_seconds"]) - remaining
            ) / max(1, self.clock_step_seconds)
            joint_distance = math.sqrt(
                path_distance * path_distance
                + liquidity_distance * liquidity_distance
                + clock_error * clock_error
            )
            candidates.append(
                {
                    "trajectory": trajectory,
                    "start_index": start_index,
                    "joint_distance": joint_distance,
                }
            )
        candidates.sort(
            key=lambda candidate: (
                float(candidate["joint_distance"]),
                str(cast(Mapping[str, Any], candidate["trajectory"])["trajectory_id"]),
            )
        )
        return candidates[: self.max_neighbors]

    def estimate_stop_survival(
        self,
        *,
        studied_direction: object,
        contract_duration_seconds: object,
        elapsed_seconds: object,
        current_path_mru: object,
        stop_distance_mru: object,
        move_size_mru: object,
        liquidity_state: Mapping[str, Any],
        causal_order_index: object,
        causal_cutoff_seconds: object,
        minimum_support: int = 1,
    ) -> dict[str, object]:
        """Estimate whether target Y historically arrived before stop X.

        The returned probability is an empirical, similarity-weighted study
        statistic.  It is not calibrated execution authority.  A remaining
        horizon below 900 seconds is returned as explicitly ineligible.
        """

        direction = _identity(
            studied_direction, field="studied_direction", maximum=8
        )
        if direction not in _DIRECTIONS:
            raise PathClockLiquidityValidationError(
                "studied_direction must be UP or DOWN"
            )
        contract_duration = _integer(
            contract_duration_seconds,
            field="contract_duration_seconds",
            minimum=MIN_ELIGIBLE_DURATION_SECONDS,
            maximum=MAX_STUDIED_DURATION_SECONDS,
        )
        elapsed = _integer(
            elapsed_seconds,
            field="elapsed_seconds",
            maximum=contract_duration,
        )
        remaining = contract_duration - elapsed
        path = _finite(
            current_path_mru,
            field="current_path_mru",
            minimum=-10_000.0,
            maximum=10_000.0,
        )
        stop = _finite(
            stop_distance_mru,
            field="stop_distance_mru",
            minimum=1e-9,
            maximum=10_000.0,
        )
        move = _finite(
            move_size_mru,
            field="move_size_mru",
            minimum=1e-9,
            maximum=10_000.0,
        )
        support_floor = _integer(
            minimum_support,
            field="minimum_support",
            minimum=1,
            maximum=self.max_neighbors,
        )
        order_index = _integer(
            causal_order_index, field="causal_order_index"
        )
        cutoff = _finite(
            causal_cutoff_seconds, field="causal_cutoff_seconds"
        )
        live_liquidity = _liquidity_state(
            liquidity_state,
            field="liquidity_state",
            causal_order_index=order_index,
            causal_cutoff_seconds=cutoff,
        )
        base: dict[str, object] = {
            "schema_version": PATH_CLOCK_LIQUIDITY_SCHEMA_VERSION,
            "status": "INSUFFICIENT_HISTORY",
            "eligible": False,
            "historical_estimate_available": False,
            "contract_admitted": True,
            "new_entry_eligible": remaining >= MIN_ELIGIBLE_DURATION_SECONDS,
            "late_clock_observation": remaining < MIN_ELIGIBLE_DURATION_SECONDS,
            "minimum_eligible_duration_seconds": MIN_ELIGIBLE_DURATION_SECONDS,
            "maximum_studied_duration_seconds": MAX_STUDIED_DURATION_SECONDS,
            "contract_duration_seconds": contract_duration,
            "elapsed_seconds": elapsed,
            "remaining_seconds": remaining,
            "studied_direction": direction,
            "current_path_mru": _rounded(path),
            "stop_distance_mru": _rounded(stop),
            "move_size_mru": _rounded(move),
            "support_count": 0,
            **_safety_contract(),
        }
        with self._lock:
            neighbors = self._eligible_neighbors(
                direction=direction,
                contract_duration_seconds=contract_duration,
                elapsed_seconds=elapsed,
                current_path_mru=path,
                liquidity_state=live_liquidity,
            )
        if len(neighbors) < support_floor:
            base["support_count"] = len(neighbors)
            base["reason"] = "Eligible closed-history support is below minimum_support."
            return base

        weighted_survival = 0.0
        weight_total = 0.0
        target_times: list[float] = []
        stop_times: list[float] = []
        future_mae: list[float] = []
        future_mfe: list[float] = []
        worst_drawdown_ahead = 0
        early_target_count = 0
        evidence: list[dict[str, object]] = []
        sign = 1.0 if direction == "UP" else -1.0
        for neighbor in neighbors:
            trajectory = cast(dict[str, Any], neighbor["trajectory"])
            points = cast(list[dict[str, Any]], trajectory["points"])
            start_index = int(neighbor["start_index"])
            baseline = float(points[start_index]["path_mru"])
            target_time: int | None = None
            historical_target_elapsed: int | None = None
            stop_time: int | None = None
            for point in points[start_index + 1 :]:
                directional_high = max(
                    sign * (float(point["interval_high_mru"]) - baseline),
                    sign * (float(point["interval_low_mru"]) - baseline),
                )
                directional_low = min(
                    sign * (float(point["interval_high_mru"]) - baseline),
                    sign * (float(point["interval_low_mru"]) - baseline),
                )
                seconds_after_start = (
                    int(point["elapsed_seconds"])
                    - int(points[start_index]["elapsed_seconds"])
                )
                if stop_time is None and directional_low <= -stop:
                    stop_time = seconds_after_start
                if target_time is None and directional_high >= move:
                    target_time = seconds_after_start
                    historical_target_elapsed = int(point["elapsed_seconds"])
                if stop_time is not None and target_time is not None:
                    break
            projected_target_elapsed = (
                elapsed + target_time if target_time is not None else None
            )
            early_target = (
                projected_target_elapsed is not None
                and projected_target_elapsed < MIN_ELIGIBLE_DURATION_SECONDS
            )
            if early_target:
                early_target_count += 1
            survived = target_time is not None and (
                stop_time is None or target_time < stop_time
            )
            mae, mfe, _, _ = _directional_extremes(
                points, direction=direction, start_index=start_index
            )
            _, _, global_adverse_index, _ = _directional_extremes(
                points, direction=direction
            )
            drawdown_ahead = bool(
                global_adverse_index is not None
                and global_adverse_index > start_index
            )
            if not early_target:
                if drawdown_ahead:
                    worst_drawdown_ahead += 1
                weight = 1.0 / (1.0 + float(neighbor["joint_distance"]))
                weighted_survival += weight * float(survived)
                weight_total += weight
                if target_time is not None:
                    target_times.append(float(target_time))
                if stop_time is not None:
                    stop_times.append(float(stop_time))
                future_mae.append(mae)
                future_mfe.append(mfe)
            evidence.append(
                {
                    "trajectory_id": trajectory["trajectory_id"],
                    "joint_distance": _rounded(float(neighbor["joint_distance"])),
                    "target_before_stop": survived,
                    "target_time_seconds": target_time,
                    "projected_contract_target_elapsed_seconds": (
                        projected_target_elapsed
                    ),
                    "historical_target_elapsed_seconds": historical_target_elapsed,
                    "stop_time_seconds": stop_time,
                    "excluded_early_target": early_target,
                    "global_worst_drawdown_index": global_adverse_index,
                    "worst_drawdown_still_ahead": drawdown_ahead,
                    "intrabar_event_order": "UNKNOWN_FAIL_CLOSED",
                }
            )
        eligible_support = len(neighbors) - early_target_count
        if eligible_support < support_floor or weight_total <= 0.0:
            base.update(
                {
                    "support_count": eligible_support,
                    "audited_neighbor_count": len(neighbors),
                    "excluded_early_target_count": early_target_count,
                    "neighbor_evidence": evidence,
                    "reason": (
                        "Eligible timing support is below minimum_support after "
                        "excluding moves completed before 900 seconds."
                    ),
                }
            )
            return base
        probability = weighted_survival / max(weight_total, 1e-12)
        entry_window_eligible = remaining >= MIN_ELIGIBLE_DURATION_SECONDS
        base.update(
            {
                "status": (
                    "STUDIED" if entry_window_eligible else "ACTIVE_TRACKING_ONLY"
                ),
                "eligible": entry_window_eligible,
                "historical_estimate_available": True,
                "support_count": eligible_support,
                "audited_neighbor_count": len(neighbors),
                "excluded_early_target_count": early_target_count,
                "survival_probability": _rounded(probability, 6),
                "probability_worst_drawdown_still_ahead": _rounded(
                    worst_drawdown_ahead / eligible_support, 6
                ),
                "target_time_seconds": {
                    "observed_count": len(target_times),
                    "p10": _rounded(_quantile(target_times, 0.10), 3),
                    "median": _rounded(_quantile(target_times, 0.50), 3),
                    "p90": _rounded(_quantile(target_times, 0.90), 3),
                },
                "stop_time_seconds": {
                    "observed_count": len(stop_times),
                    "median": _rounded(_quantile(stop_times, 0.50), 3),
                },
                "future_excursion_mru": {
                    "mae_median": _rounded(median(future_mae)),
                    "mae_p90": _rounded(_quantile(future_mae, 0.90)),
                    "mfe_median": _rounded(median(future_mfe)),
                    "mfe_p90": _rounded(_quantile(future_mfe, 0.90)),
                },
                "neighbor_evidence": evidence,
                "reason": (
                    "Empirical closed-history path-clock-liquidity study only; "
                    "entry permission remains independent."
                ),
            }
        )
        return base

    def freeze_closed_candle_state(
        self,
        *,
        closed_candle_key: object,
        order_index: object,
        closed_at_seconds: object,
        studied_direction: object,
        contract_duration_seconds: object,
        elapsed_seconds: object,
        current_path_mru: object,
        liquidity_state: Mapping[str, Any],
        scenarios: Sequence[Mapping[str, Any]] = (),
        minimum_support: int = 1,
    ) -> dict[str, object]:
        """Freeze a bounded, replayable field state at one closed-candle key."""

        key = _identity(closed_candle_key, field="closed_candle_key", maximum=256)
        order = _integer(order_index, field="order_index")
        cutoff = _finite(closed_at_seconds, field="closed_at_seconds")
        scenario_rows = _required_rows(scenarios, field="scenarios")
        if len(scenario_rows) > MAX_SWEEP_OUTCOMES_PER_REPLAY:
            raise PathClockLiquidityValidationError(
                f"scenarios cannot exceed {MAX_SWEEP_OUTCOMES_PER_REPLAY}"
            )
        validated_liquidity = _liquidity_state(
            liquidity_state,
            field="liquidity_state",
            causal_order_index=order,
            causal_cutoff_seconds=cutoff,
        )
        duration = _integer(
            contract_duration_seconds,
            field="contract_duration_seconds",
            minimum=MIN_ELIGIBLE_DURATION_SECONDS,
            maximum=MAX_STUDIED_DURATION_SECONDS,
        )
        elapsed = _integer(
            elapsed_seconds, field="elapsed_seconds", maximum=duration
        )
        direction = _identity(
            studied_direction, field="studied_direction", maximum=8
        )
        if direction not in _DIRECTIONS:
            raise PathClockLiquidityValidationError(
                "studied_direction must be UP or DOWN"
            )
        path = _finite(
            current_path_mru,
            field="current_path_mru",
            minimum=-10_000.0,
            maximum=10_000.0,
        )
        support_floor = _integer(
            minimum_support,
            field="minimum_support",
            minimum=1,
            maximum=self.max_neighbors,
        )
        canonical_scenarios = [
            {
                "stop_distance_mru": _rounded(
                    _finite(
                        row.get("stop_distance_mru"),
                        field=f"scenarios[{index}].stop_distance_mru",
                        minimum=1e-9,
                        maximum=10_000.0,
                    )
                ),
                "move_size_mru": _rounded(
                    _finite(
                        row.get("move_size_mru"),
                        field=f"scenarios[{index}].move_size_mru",
                        minimum=1e-9,
                        maximum=10_000.0,
                    )
                ),
            }
            for index, row in enumerate(scenario_rows)
        ]
        request_body = {
            "closed_candle_key": key,
            "order_index": order,
            "closed_at_seconds": _rounded(cutoff, 6),
            **self._scope(),
            "studied_direction": direction,
            "contract_duration_seconds": duration,
            "elapsed_seconds": elapsed,
            "current_path_mru": _rounded(path),
            "liquidity_state": validated_liquidity,
            "scenarios": canonical_scenarios,
            "minimum_support": support_floor,
        }
        request_digest = _digest(request_body)
        with self._lock:
            if key in self._freeze_keys:
                existing = next(
                    item
                    for item in self._freezes
                    if item["closed_candle_key"] == key
                )
                if existing.get("request_digest") == request_digest:
                    return deepcopy(existing)
                raise PathClockLiquidityValidationError(
                    "closed_candle_key conflicts with a different frozen state"
                )
            library_revision = len(self._trajectories)
            library_binding = _digest(
                [row["trajectory_digest"] for row in self._trajectories]
            )
        estimates = [
            self.estimate_stop_survival(
                studied_direction=direction,
                contract_duration_seconds=duration,
                elapsed_seconds=elapsed,
                current_path_mru=path,
                stop_distance_mru=row["stop_distance_mru"],
                move_size_mru=row["move_size_mru"],
                liquidity_state=validated_liquidity,
                causal_order_index=order,
                causal_cutoff_seconds=cutoff,
                minimum_support=support_floor,
            )
            for row in canonical_scenarios
        ]
        freeze: dict[str, Any] = {
            "schema_version": PATH_CLOCK_FREEZE_SCHEMA_VERSION,
            **request_body,
            "request_digest": request_digest,
            "remaining_seconds": duration - elapsed,
            "eligible": duration - elapsed >= MIN_ELIGIBLE_DURATION_SECONDS,
            "contract_admitted": True,
            "historical_tracking_active": True,
            "new_entry_eligible": (
                duration - elapsed >= MIN_ELIGIBLE_DURATION_SECONDS
            ),
            "trajectory_library_revision": library_revision,
            "trajectory_library_binding_digest": library_binding,
            "scenario_estimates": estimates,
            **_safety_contract(),
        }
        freeze["freeze_digest"] = _digest(freeze)
        with self._lock:
            current_library_binding = _digest(
                [row["trajectory_digest"] for row in self._trajectories]
            )
            if (
                len(self._trajectories) != library_revision
                or current_library_binding != library_binding
            ):
                raise PathClockLiquidityValidationError(
                    "trajectory library changed during freeze; retry"
                )
            if key in self._freeze_keys:
                existing = next(
                    item
                    for item in self._freezes
                    if item["closed_candle_key"] == key
                )
                if existing.get("request_digest") == request_digest:
                    return deepcopy(existing)
                raise PathClockLiquidityValidationError(
                    "closed_candle_key conflicts with a different frozen state"
                )
            if (
                self._last_freeze_order_index is not None
                and order <= self._last_freeze_order_index
            ):
                raise PathClockLiquidityValidationError(
                    "closed-candle freeze order must increase strictly"
                )
            if len(self._freezes) >= self.max_freezes:
                raise PathClockLiquidityValidationError("freeze capacity reached")
            self._freezes.append(freeze)
            self._freeze_keys.add(key)
            self._last_freeze_order_index = order
        return deepcopy(freeze)

    def joint_clock_distribution(
        self, *, max_rows: int = DEFAULT_MAX_FIELD_ROWS
    ) -> dict[str, object]:
        """Summarize joint MAE/MFE/path/final direction on the common grid."""

        row_limit = _integer(
            max_rows,
            field="max_rows",
            minimum=1,
            maximum=DEFAULT_MAX_FIELD_ROWS,
        )
        buckets: dict[tuple[int, int, tuple[int, ...]], dict[str, Any]] = {}
        with self._lock:
            trajectories = tuple(self._trajectories)
        for trajectory in trajectories:
            direction = str(trajectory["studied_direction"])
            sign = 1.0 if direction == "UP" else -1.0
            direction_label = str(trajectory["final_direction"])
            duration = int(trajectory["duration_seconds"])
            liquidity_state = cast(
                Mapping[str, Any], trajectory["liquidity_state"]
            )
            liquidity_bin = _liquidity_bin(liquidity_state)
            for point in cast(list[dict[str, Any]], trajectory["points"]):
                elapsed = int(point["elapsed_seconds"])
                remaining = int(point["remaining_seconds"])
                bucket_key = (duration, remaining, liquidity_bin)
                bucket = buckets.setdefault(
                    bucket_key,
                    {
                        "elapsed_seconds": elapsed,
                        "paths": [],
                        "mae": [],
                        "mfe": [],
                        "directions": {"UP": 0, "DOWN": 0, "FLAT": 0},
                        "liquidity": [],
                    },
                )
                path = float(point["path_mru"])
                directional_high = max(
                    sign * float(point["cumulative_high_mru"]),
                    sign * float(point["cumulative_low_mru"]),
                )
                directional_low = min(
                    sign * float(point["cumulative_high_mru"]),
                    sign * float(point["cumulative_low_mru"]),
                )
                cast(list[float], bucket["paths"]).append(path)
                cast(list[float], bucket["mae"]).append(max(0.0, -directional_low))
                cast(list[float], bucket["mfe"]).append(max(0.0, directional_high))
                cast(dict[str, int], bucket["directions"])[direction_label] += 1
                cast(list[tuple[float, ...]], bucket["liquidity"]).append(
                    _liquidity_vector(liquidity_state)
                )
        rows: list[dict[str, object]] = []
        for bucket_key in sorted(buckets)[:row_limit]:
            duration, remaining, liquidity_bin = bucket_key
            bucket = buckets[bucket_key]
            paths = cast(list[float], bucket["paths"])
            mae = cast(list[float], bucket["mae"])
            mfe = cast(list[float], bucket["mfe"])
            vectors = cast(list[tuple[float, ...]], bucket["liquidity"])
            rows.append(
                {
                    "contract_duration_seconds": duration,
                    "elapsed_seconds": int(bucket["elapsed_seconds"]),
                    "remaining_seconds": remaining,
                    "liquidity_bin": {
                        field: liquidity_bin[index]
                        for index, field in enumerate(_LIQUIDITY_FIELDS)
                    },
                    "liquidity_bin_count_per_axis": _LIQUIDITY_BIN_COUNT,
                    "support_count": len(paths),
                    "path_mru": {
                        "p10": _rounded(_quantile(paths, 0.10)),
                        "median": _rounded(_quantile(paths, 0.50)),
                        "p90": _rounded(_quantile(paths, 0.90)),
                    },
                    "mae_mru": {
                        "median": _rounded(_quantile(mae, 0.50)),
                        "p90": _rounded(_quantile(mae, 0.90)),
                    },
                    "mfe_mru": {
                        "median": _rounded(_quantile(mfe, 0.50)),
                        "p90": _rounded(_quantile(mfe, 0.90)),
                    },
                    "final_direction_counts": deepcopy(bucket["directions"]),
                    "liquidity_centroid": {
                        field: _rounded(
                            sum(vector[index] for vector in vectors) / len(vectors)
                        )
                        for index, field in enumerate(_LIQUIDITY_FIELDS)
                    },
                }
            )
        return {
            "schema_version": PATH_CLOCK_LIQUIDITY_SCHEMA_VERSION,
            **self._scope(),
            "clock_step_seconds": self.clock_step_seconds,
            "minimum_eligible_duration_seconds": MIN_ELIGIBLE_DURATION_SECONDS,
            "trajectory_count": len(trajectories),
            "row_count": len(rows),
            "truncated": len(buckets) > row_limit,
            "rows": rows,
            **_safety_contract(),
        }

    def snapshot(self) -> dict[str, object]:
        """Return dedicated bounded side-store state without raw prices.

        This document intentionally is not a Pair DNA JSON payload.  Call
        :meth:`pair_dna_partition_summary` for the compact aggregate that may be
        embedded in Pair DNA.
        """

        with self._lock:
            body: dict[str, object] = {
                "schema_version": PATH_CLOCK_LIQUIDITY_SCHEMA_VERSION,
                **self._scope(),
                "config": {
                    "clock_step_seconds": self.clock_step_seconds,
                    "max_trajectories": self.max_trajectories,
                    "max_points_per_trajectory": self.max_points_per_trajectory,
                    "max_freezes": self.max_freezes,
                    "max_neighbors": self.max_neighbors,
                    "minimum_eligible_duration_seconds": MIN_ELIGIBLE_DURATION_SECONDS,
                    "maximum_studied_duration_seconds": MAX_STUDIED_DURATION_SECONDS,
                },
                "persistence_contract": {
                    "storage_role": "DEDICATED_BOUNDED_TRAJECTORY_SIDE_STORE",
                    "pair_dna_embeddable": False,
                    "contains_raw_prices": False,
                    "contains_normalized_trajectory_points": True,
                },
                "trajectories": deepcopy(self._trajectories),
                "freezes": deepcopy(self._freezes),
                **_safety_contract(),
            }
        body["state_digest"] = _digest(body)
        return body

    def pair_dna_partition_summary(self) -> dict[str, object]:
        """Return a compact aggregate; never copy raw paths into Pair DNA."""

        with self._lock:
            trajectories = tuple(self._trajectories)
            freezes = tuple(self._freezes)
        directions = {"UP": 0, "DOWN": 0, "FLAT": 0}
        durations: list[float] = []
        mae: list[float] = []
        mfe: list[float] = []
        for trajectory in trajectories:
            directions[str(trajectory["final_direction"])] += 1
            durations.append(float(trajectory["duration_seconds"]))
            mae.append(float(trajectory["maximum_adverse_excursion_mru"]))
            mfe.append(float(trajectory["maximum_favorable_excursion_mru"]))
        side_store_binding = _digest(
            {
                "trajectory_digests": [
                    row["trajectory_digest"] for row in trajectories
                ],
                "freeze_digests": [row["freeze_digest"] for row in freezes],
            }
        )
        result: dict[str, object] = {
            "schema_version": PATH_CLOCK_PAIR_DNA_PARTITION_SCHEMA_VERSION,
            **self._scope(),
            "trajectory_count": len(trajectories),
            "freeze_count": len(freezes),
            "final_direction_counts": directions,
            "duration_seconds": {
                "minimum": _rounded(min(durations), 3) if durations else None,
                "median": _rounded(median(durations), 3) if durations else None,
                "maximum": _rounded(max(durations), 3) if durations else None,
            },
            "excursion_mru": {
                "mae_median": _rounded(median(mae)) if mae else None,
                "mfe_median": _rounded(median(mfe)) if mfe else None,
            },
            "side_store_binding_digest": side_store_binding,
            "contains_trajectory_points": False,
            "contains_freeze_records": False,
            "side_store_required_for_replay": True,
            **_safety_contract(),
        }
        result["partition_digest"] = _digest(result)
        return result

    @classmethod
    def from_snapshot(
        cls, value: Mapping[str, Any]
    ) -> JointPathClockLiquidityFieldV3:
        """Restore state by replaying every invariant-bearing record."""

        row = dict(value)
        if row.get("schema_version") != PATH_CLOCK_LIQUIDITY_SCHEMA_VERSION:
            raise PathClockLiquidityValidationError("snapshot schema mismatch")
        claimed_digest = _identity(
            row.get("state_digest"), field="snapshot.state_digest", maximum=64
        ).lower()
        digest_body = deepcopy(row)
        digest_body.pop("state_digest", None)
        if _digest(digest_body) != claimed_digest:
            raise PathClockLiquidityValidationError("snapshot digest mismatch")
        for safety_key, expected in _safety_contract().items():
            if row.get(safety_key) is not expected:
                raise PathClockLiquidityValidationError(
                    f"snapshot violates {safety_key} safety invariant"
                )
        persistence = _required_mapping(
            row.get("persistence_contract"), field="snapshot.persistence_contract"
        )
        if (
            persistence.get("storage_role")
            != "DEDICATED_BOUNDED_TRAJECTORY_SIDE_STORE"
            or persistence.get("pair_dna_embeddable") is not False
            or persistence.get("contains_raw_prices") is not False
            or persistence.get("contains_normalized_trajectory_points") is not True
        ):
            raise PathClockLiquidityValidationError(
                "snapshot persistence contract is unsafe"
            )
        config = _required_mapping(row.get("config"), field="snapshot.config")
        if (
            _integer(
                config.get("minimum_eligible_duration_seconds"),
                field="config.minimum_eligible_duration_seconds",
            )
            != MIN_ELIGIBLE_DURATION_SECONDS
            or _integer(
                config.get("maximum_studied_duration_seconds"),
                field="config.maximum_studied_duration_seconds",
            )
            != MAX_STUDIED_DURATION_SECONDS
        ):
            raise PathClockLiquidityValidationError(
                "snapshot timing policy does not match canonical V3 policy"
            )
        instance = cls(
            symbol=row.get("symbol"),
            timeframe=row.get("timeframe"),
            coordinate_space=row.get("coordinate_space"),
            order_domain=row.get("order_domain"),
            clock_step_seconds=_integer(
                config.get("clock_step_seconds"), field="config.clock_step_seconds"
            ),
            max_trajectories=_integer(
                config.get("max_trajectories"), field="config.max_trajectories"
            ),
            max_points_per_trajectory=_integer(
                config.get("max_points_per_trajectory"),
                field="config.max_points_per_trajectory",
            ),
            max_freezes=_integer(
                config.get("max_freezes"), field="config.max_freezes"
            ),
            max_neighbors=_integer(
                config.get("max_neighbors"), field="config.max_neighbors"
            ),
        )
        trajectories = _required_rows(
            row.get("trajectories"), field="snapshot.trajectories"
        )
        freezes = _required_rows(row.get("freezes"), field="snapshot.freezes")
        if len(trajectories) > instance.max_trajectories:
            raise PathClockLiquidityValidationError(
                "snapshot exceeds trajectory capacity"
            )
        if len(freezes) > instance.max_freezes:
            raise PathClockLiquidityValidationError("snapshot exceeds freeze capacity")
        for index, trajectory in enumerate(trajectories):
            _validate_scope(
                trajectory,
                symbol=instance.symbol,
                timeframe=instance.timeframe,
                coordinate_space=instance.coordinate_space,
                order_domain=instance.order_domain,
                field=f"snapshot.trajectories[{index}]",
            )
            trajectory_digest = str(trajectory.get("trajectory_digest") or "")
            trajectory_body = deepcopy(trajectory)
            trajectory_body.pop("trajectory_digest", None)
            if not trajectory_digest or _digest(trajectory_body) != trajectory_digest:
                raise PathClockLiquidityValidationError(
                    "snapshot trajectory digest mismatch"
                )
            normalized_points = _required_rows(
                trajectory.get("points"),
                field=f"snapshot.trajectories[{index}].points",
            )
            canonical = instance.add_trajectory(
                {
                    **instance._scope(),
                    "trajectory_id": trajectory.get("trajectory_id"),
                    "study_only": True,
                    "completed": True,
                    "anchor": trajectory.get("anchor"),
                    "duration_seconds": trajectory.get("duration_seconds"),
                    "source_cadence_seconds": trajectory.get(
                        "source_cadence_seconds"
                    ),
                    "exact_subcandle_timestamps_proven": trajectory.get(
                        "exact_subcandle_timestamps_proven"
                    ),
                    "studied_direction": trajectory.get("studied_direction"),
                    "liquidity_state": trajectory.get("liquidity_state"),
                    "points": [
                        {
                            "elapsed_seconds": point.get("elapsed_seconds"),
                            "path_mru": point.get("path_mru"),
                            "high_mru": point.get("interval_high_mru"),
                            "low_mru": point.get("interval_low_mru"),
                        }
                        for point in normalized_points
                    ],
                }
            )
            if canonical != trajectory:
                raise PathClockLiquidityValidationError(
                    "snapshot trajectory is not canonical"
                )
        for index, freeze in enumerate(freezes):
            _validate_scope(
                freeze,
                symbol=instance.symbol,
                timeframe=instance.timeframe,
                coordinate_space=instance.coordinate_space,
                order_domain=instance.order_domain,
                field=f"snapshot.freezes[{index}]",
            )
            freeze_digest = str(freeze.get("freeze_digest") or "")
            freeze_body = deepcopy(freeze)
            freeze_body.pop("freeze_digest", None)
            if not freeze_digest or _digest(freeze_body) != freeze_digest:
                raise PathClockLiquidityValidationError(
                    "snapshot freeze digest mismatch"
                )
            _required_rows(
                freeze.get("scenario_estimates"),
                field=f"snapshot.freezes[{index}].scenario_estimates",
            )
            scenarios = _required_rows(
                freeze.get("scenarios"),
                field=f"snapshot.freezes[{index}].scenarios",
            )
            library_revision = _integer(
                freeze.get("trajectory_library_revision"),
                field=f"snapshot.freezes[{index}].trajectory_library_revision",
                maximum=len(instance._trajectories),
            )
            expected_binding = _digest(
                [
                    trajectory["trajectory_digest"]
                    for trajectory in instance._trajectories[:library_revision]
                ]
            )
            if freeze.get("trajectory_library_binding_digest") != expected_binding:
                raise PathClockLiquidityValidationError(
                    "snapshot freeze trajectory-library binding mismatch"
                )
            complete_library = instance._trajectories
            instance._trajectories = complete_library[:library_revision]
            try:
                canonical = instance.freeze_closed_candle_state(
                    closed_candle_key=freeze.get("closed_candle_key"),
                    order_index=freeze.get("order_index"),
                    closed_at_seconds=freeze.get("closed_at_seconds"),
                    studied_direction=freeze.get("studied_direction"),
                    contract_duration_seconds=freeze.get(
                        "contract_duration_seconds"
                    ),
                    elapsed_seconds=freeze.get("elapsed_seconds"),
                    current_path_mru=freeze.get("current_path_mru"),
                    liquidity_state=_required_mapping(
                        freeze.get("liquidity_state"),
                        field=f"snapshot.freezes[{index}].liquidity_state",
                    ),
                    scenarios=scenarios,
                    minimum_support=_integer(
                        freeze.get("minimum_support"),
                        field=f"snapshot.freezes[{index}].minimum_support",
                        minimum=1,
                        maximum=instance.max_neighbors,
                    ),
                )
            finally:
                instance._trajectories = complete_library
            if canonical != freeze:
                raise PathClockLiquidityValidationError(
                    "snapshot freeze is not canonical"
                )
        return instance


def score_path_clock_replays_v3(
    records: Sequence[Mapping[str, Any]],
    *,
    symbol: object,
    timeframe: object,
    coordinate_space: object,
    order_domain: object,
) -> dict[str, object]:
    """Score direction, timing, sweep survival, and probability calibration."""

    scope_symbol = _identity(symbol, field="symbol", maximum=64)
    scope_timeframe = _identity(timeframe, field="timeframe", maximum=32)
    scope_coordinate = _identity(
        coordinate_space, field="coordinate_space", maximum=64
    )
    if scope_coordinate != _COORDINATE_SPACE:
        raise PathClockLiquidityValidationError(
            "coordinate_space must be NORMALIZED_MEDIAN_RANGE"
        )
    scope_order = _identity(order_domain, field="order_domain", maximum=64)
    if scope_order not in _ORDER_DOMAINS:
        raise PathClockLiquidityValidationError(
            "order_domain must prove stable closed-candle order"
        )
    rows = _required_rows(records, field="records")
    if not rows or len(rows) > MAX_REPLAY_RECORDS:
        raise PathClockLiquidityValidationError(
            f"records must contain between 1 and {MAX_REPLAY_RECORDS} replays"
        )
    seen_keys: set[str] = set()
    directional_hits = 0
    timing_hits = 0
    survival_hits = 0
    outcomes = 0
    brier_sum = 0.0
    eligible_replays = 0
    excluded_early_moves = 0
    calibration_bins: dict[int, list[tuple[float, float]]] = {}
    audited_replay_keys: list[str] = []
    scenario_grid_rows: list[dict[str, object]] = []
    evaluation_cohort_rows: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        _validate_scope(
            row,
            symbol=scope_symbol,
            timeframe=scope_timeframe,
            coordinate_space=scope_coordinate,
            order_domain=scope_order,
            field=f"records[{index}]",
        )
        if (
            row.get("frozen_on_closed_candle") is not True
            or row.get("future_leakage_detected") is not False
        ):
            raise PathClockLiquidityValidationError(
                f"records[{index}] lacks closed-candle causal replay proof"
            )
        key = _identity(
            row.get("closed_candle_key"),
            field=f"records[{index}].closed_candle_key",
            maximum=256,
        )
        if key in seen_keys:
            raise PathClockLiquidityValidationError(
                "replay closed_candle_key values must be unique"
            )
        seen_keys.add(key)
        audited_replay_keys.append(key)
        horizon = _integer(
            row.get("horizon_seconds"),
            field=f"records[{index}].horizon_seconds",
            minimum=MIN_ELIGIBLE_DURATION_SECONDS,
            maximum=MAX_STUDIED_DURATION_SECONDS,
        )
        predicted_direction = _identity(
            row.get("predicted_direction"),
            field=f"records[{index}].predicted_direction",
            maximum=8,
        )
        observed_direction = _identity(
            row.get("observed_direction"),
            field=f"records[{index}].observed_direction",
            maximum=8,
        )
        if predicted_direction not in _DIRECTIONS or observed_direction not in {
            *_DIRECTIONS,
            "FLAT",
        }:
            raise PathClockLiquidityValidationError(
                "predicted replay direction must be UP or DOWN and observed "
                "direction must be UP, DOWN, or FLAT"
            )
        timing_window = _required_mapping(
            row.get("timing_window_seconds"),
            field=f"records[{index}].timing_window_seconds",
        )
        timing_start = _integer(
            timing_window.get("start"),
            field=f"records[{index}].timing_window_seconds.start",
        )
        timing_end = _integer(
            timing_window.get("end"),
            field=f"records[{index}].timing_window_seconds.end",
            minimum=timing_start,
            maximum=horizon,
        )
        observed_move_time = _integer(
            row.get("observed_move_time_seconds"),
            field=f"records[{index}].observed_move_time_seconds",
            maximum=horizon,
        )
        move_occurred_value = row.get("observed_move_occurred")
        if move_occurred_value is None:
            # Restart compatibility: every V3 replay persisted before this
            # explicit censor flag was necessarily a target-hit replay.
            move_occurred = True
        elif isinstance(move_occurred_value, bool):
            move_occurred = move_occurred_value
        else:
            raise PathClockLiquidityValidationError(
                "replay observed_move_occurred must be boolean"
            )
        if not move_occurred and observed_move_time != horizon:
            raise PathClockLiquidityValidationError(
                "a no-target replay must be right-censored at horizon_seconds"
            )
        sweep_rows = _required_rows(
            row.get("sweep_outcomes"), field=f"records[{index}].sweep_outcomes"
        )
        if not sweep_rows or len(sweep_rows) > MAX_SWEEP_OUTCOMES_PER_REPLAY:
            raise PathClockLiquidityValidationError(
                "each replay needs bounded sweep outcomes"
            )
        validated_sweeps: list[dict[str, object]] = []
        seen_scenarios: set[tuple[float, float]] = set()
        for sweep_index, sweep in enumerate(sweep_rows):
            stop_distance = _rounded(
                _finite(
                    sweep.get("stop_distance_mru"),
                    field=(
                        f"records[{index}].sweep_outcomes[{sweep_index}]"
                        ".stop_distance_mru"
                    ),
                    minimum=1e-9,
                    maximum=10_000.0,
                )
            )
            move_size = _rounded(
                _finite(
                    sweep.get("move_size_mru"),
                    field=(
                        f"records[{index}].sweep_outcomes[{sweep_index}]"
                        ".move_size_mru"
                    ),
                    minimum=1e-9,
                    maximum=10_000.0,
                )
            )
            scenario_identity = (stop_distance, move_size)
            if scenario_identity in seen_scenarios:
                raise PathClockLiquidityValidationError(
                    "sweep stop/move identities must be unique within a replay"
                )
            seen_scenarios.add(scenario_identity)
            probability = _finite(
                sweep.get("predicted_survival_probability"),
                field=(
                    f"records[{index}].sweep_outcomes[{sweep_index}]"
                    ".predicted_survival_probability"
                ),
                minimum=0.0,
                maximum=1.0,
            )
            survived = sweep.get("survived_until_move")
            if not isinstance(survived, bool):
                raise PathClockLiquidityValidationError(
                    "sweep survived_until_move must be boolean"
                )
            validated_sweeps.append(
                {
                    "stop_distance_mru": stop_distance,
                    "move_size_mru": move_size,
                    "predicted_survival_probability": probability,
                    "survived_until_move": survived,
                }
            )
        canonical_scenario_grid = [
            {
                "stop_distance_mru": sweep["stop_distance_mru"],
                "move_size_mru": sweep["move_size_mru"],
            }
            for sweep in sorted(
                validated_sweeps,
                key=lambda sweep: (
                    float(cast(Any, sweep["stop_distance_mru"])),
                    float(cast(Any, sweep["move_size_mru"])),
                ),
            )
        ]
        excluded_early = bool(
            move_occurred
            and observed_move_time < MIN_ELIGIBLE_DURATION_SECONDS
        )
        scenario_grid_rows.append(
            {
                "closed_candle_key": key,
                "scenarios": canonical_scenario_grid,
            }
        )
        evaluation_cohort_rows.append(
            {
                "closed_candle_key": key,
                "horizon_seconds": horizon,
                "observed_direction": observed_direction,
                "observed_move_occurred": move_occurred,
                "observed_move_time_seconds": observed_move_time,
                "excluded_early_move": excluded_early,
                "sweep_outcomes": [
                    {
                        "stop_distance_mru": sweep["stop_distance_mru"],
                        "move_size_mru": sweep["move_size_mru"],
                        "survived_until_move": sweep["survived_until_move"],
                    }
                    for sweep in sorted(
                        validated_sweeps,
                        key=lambda sweep: (
                            float(cast(Any, sweep["stop_distance_mru"])),
                            float(cast(Any, sweep["move_size_mru"])),
                        ),
                    )
                ],
            }
        )
        if excluded_early:
            excluded_early_moves += 1
            continue
        eligible_replays += 1
        directional_hits += int(predicted_direction == observed_direction)
        timing_hits += int(
            move_occurred and timing_start <= observed_move_time <= timing_end
        )
        for sweep in validated_sweeps:
            probability = float(
                cast(Any, sweep["predicted_survival_probability"])
            )
            survived = bool(sweep["survived_until_move"])
            actual = float(survived)
            survival_hits += int(survived)
            outcomes += 1
            brier_sum += (probability - actual) ** 2
            bin_index = min(9, int(probability * 10.0))
            calibration_bins.setdefault(bin_index, []).append((probability, actual))
    if eligible_replays == 0 or outcomes == 0:
        raise PathClockLiquidityValidationError(
            "no eligible replays remain after excluding moves under 900 seconds"
        )
    expected_calibration_error = 0.0
    calibration_rows: list[dict[str, object]] = []
    for bin_index in sorted(calibration_bins):
        values = calibration_bins[bin_index]
        predicted = sum(value[0] for value in values) / len(values)
        observed = sum(value[1] for value in values) / len(values)
        expected_calibration_error += len(values) / outcomes * abs(predicted - observed)
        calibration_rows.append(
            {
                "bin": bin_index,
                "support_count": len(values),
                "mean_predicted_probability": _rounded(predicted, 6),
                "observed_survival_rate": _rounded(observed, 6),
            }
        )
    metrics = {
        "directional_accuracy": _rounded(directional_hits / eligible_replays, 6),
        "timing_accuracy": _rounded(timing_hits / eligible_replays, 6),
        "sweep_survival_rate": _rounded(survival_hits / outcomes, 6),
        "calibration_score": _rounded(1.0 - expected_calibration_error, 6),
        "expected_calibration_error": _rounded(expected_calibration_error, 6),
        "brier_score": _rounded(brier_sum / outcomes, 6),
    }
    return {
        "schema_version": PATH_CLOCK_REPLAY_SCORE_SCHEMA_VERSION,
        "scope": {
            "symbol": scope_symbol,
            "timeframe": scope_timeframe,
            "coordinate_space": scope_coordinate,
            "order_domain": scope_order,
        },
        "audited_replay_count": len(rows),
        "eligible_replay_count": eligible_replays,
        "excluded_early_move_count": excluded_early_moves,
        "sweep_outcome_count": outcomes,
        "replay_key_digest": _digest(sorted(audited_replay_keys)),
        "scenario_grid_digest": _digest(
            sorted(scenario_grid_rows, key=lambda row: str(row["closed_candle_key"]))
        ),
        "evaluation_cohort_digest": _digest(
            sorted(
                evaluation_cohort_rows,
                key=lambda row: str(row["closed_candle_key"]),
            )
        ),
        "metrics": metrics,
        "calibration_bins": calibration_rows,
        "minimum_eligible_duration_seconds": MIN_ELIGIBLE_DURATION_SECONDS,
        **_safety_contract(),
    }


def evaluate_path_clock_promotion_gate_v3(
    *,
    baseline_score: Mapping[str, Any],
    candidate_score: Mapping[str, Any],
    minimum_replays: int = 32,
    minimum_improvement: float = 0.0,
) -> dict[str, object]:
    """Pass only when all four independent replay axes strictly improve."""

    support_floor = _integer(
        minimum_replays,
        field="minimum_replays",
        minimum=1,
        maximum=MAX_REPLAY_RECORDS,
    )
    delta_floor = _finite(
        minimum_improvement,
        field="minimum_improvement",
        minimum=0.0,
        maximum=1.0,
    )
    for label, score in (
        ("baseline_score", baseline_score),
        ("candidate_score", candidate_score),
    ):
        if score.get("schema_version") != PATH_CLOCK_REPLAY_SCORE_SCHEMA_VERSION:
            raise PathClockLiquidityValidationError(
                f"{label} must be a canonical JPCLF replay score"
            )
        for safety_key, expected in _safety_contract().items():
            if score.get(safety_key) is not expected:
                raise PathClockLiquidityValidationError(
                    f"{label} violates {safety_key} safety invariant"
                )
    baseline_scope = _required_mapping(
        baseline_score.get("scope"), field="baseline_score.scope"
    )
    candidate_scope = _required_mapping(
        candidate_score.get("scope"), field="candidate_score.scope"
    )
    if baseline_scope != candidate_scope:
        raise PathClockLiquidityValidationError(
            "promotion scores must have identical pair and coordinate scope"
        )
    baseline_metrics = _required_mapping(
        baseline_score.get("metrics"), field="baseline_score.metrics"
    )
    candidate_metrics = _required_mapping(
        candidate_score.get("metrics"), field="candidate_score.metrics"
    )
    baseline_support = _integer(
        baseline_score.get("eligible_replay_count"),
        field="baseline_score.eligible_replay_count",
    )
    candidate_support = _integer(
        candidate_score.get("eligible_replay_count"),
        field="candidate_score.eligible_replay_count",
    )
    baseline_audited = _integer(
        baseline_score.get("audited_replay_count"),
        field="baseline_score.audited_replay_count",
    )
    candidate_audited = _integer(
        candidate_score.get("audited_replay_count"),
        field="candidate_score.audited_replay_count",
    )
    baseline_outcomes = _integer(
        baseline_score.get("sweep_outcome_count"),
        field="baseline_score.sweep_outcome_count",
    )
    candidate_outcomes = _integer(
        candidate_score.get("sweep_outcome_count"),
        field="candidate_score.sweep_outcome_count",
    )
    pairing_fields = (
        "replay_key_digest",
        "scenario_grid_digest",
        "evaluation_cohort_digest",
    )
    paired_digests: dict[str, dict[str, object]] = {}
    for field in pairing_fields:
        baseline_digest = _identity(
            baseline_score.get(field), field=f"baseline_score.{field}"
        )
        candidate_digest = _identity(
            candidate_score.get(field), field=f"candidate_score.{field}"
        )
        paired_digests[field] = {
            "baseline": baseline_digest,
            "candidate": candidate_digest,
            "matches": baseline_digest == candidate_digest,
        }
    comparisons: dict[str, dict[str, object]] = {}
    for axis in _PROMOTION_AXES:
        baseline = _finite(
            baseline_metrics.get(axis),
            field=f"baseline_score.metrics.{axis}",
            minimum=0.0,
            maximum=1.0,
        )
        candidate = _finite(
            candidate_metrics.get(axis),
            field=f"candidate_score.metrics.{axis}",
            minimum=0.0,
            maximum=1.0,
        )
        improvement = candidate - baseline
        comparisons[axis] = {
            "baseline": _rounded(baseline, 6),
            "candidate": _rounded(candidate, 6),
            "improvement": _rounded(improvement, 6),
            "improved": improvement > delta_floor,
        }
    support_passed = (
        baseline_support >= support_floor and candidate_support >= support_floor
    )
    paired_evaluation_passed = bool(
        baseline_support == candidate_support
        and baseline_audited == candidate_audited
        and baseline_outcomes == candidate_outcomes
        and all(bool(row["matches"]) for row in paired_digests.values())
    )
    all_axes_improved = all(
        bool(comparison["improved"]) for comparison in comparisons.values()
    )
    passed = support_passed and paired_evaluation_passed and all_axes_improved
    return {
        "schema_version": PATH_CLOCK_PROMOTION_GATE_SCHEMA_VERSION,
        "passed": passed,
        "status": "PROMOTION_ELIGIBLE" if passed else "RETAIN_BASELINE",
        "minimum_replays": support_floor,
        "minimum_improvement": _rounded(delta_floor, 6),
        "support": {
            "baseline": baseline_support,
            "candidate": candidate_support,
            "passed": support_passed,
        },
        "paired_evaluation": {
            "passed": paired_evaluation_passed,
            "audited_replay_count": {
                "baseline": baseline_audited,
                "candidate": candidate_audited,
            },
            "sweep_outcome_count": {
                "baseline": baseline_outcomes,
                "candidate": candidate_outcomes,
            },
            "digests": paired_digests,
        },
        "axes": comparisons,
        "all_axes_improved": all_axes_improved,
        "reason": (
            "The paired replay cohort matched and all four independent "
            "timing-study axes improved."
            if passed
            else (
                "Promotion requires an identical replay cohort and stop/move "
                "scenario grid, sufficient support, and strict improvement on "
                "every axis."
            )
        ),
        **_safety_contract(),
    }


__all__ = [
    "DEFAULT_CLOCK_STEP_SECONDS",
    "JointPathClockLiquidityFieldV3",
    "MAX_STUDIED_DURATION_SECONDS",
    "MIN_ELIGIBLE_DURATION_SECONDS",
    "PATH_CLOCK_FREEZE_SCHEMA_VERSION",
    "PATH_CLOCK_FORWARD_TIMING_FORECAST_SCHEMA_VERSION",
    "PATH_CLOCK_LIQUIDITY_SCHEMA_VERSION",
    "PATH_CLOCK_PAIR_DNA_PARTITION_SCHEMA_VERSION",
    "PATH_CLOCK_PROMOTION_GATE_SCHEMA_VERSION",
    "PATH_CLOCK_REPLAY_SCORE_SCHEMA_VERSION",
    "PATH_CLOCK_TRAJECTORY_SCHEMA_VERSION",
    "PathClockLiquidityValidationError",
    "build_hierarchical_forward_timing_forecast_v3",
    "evaluate_path_clock_promotion_gate_v3",
    "score_path_clock_replays_v3",
]
