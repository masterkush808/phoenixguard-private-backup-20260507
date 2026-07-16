from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence, cast


FORECAST_BELIEF_TRACKER_SCHEMA_V3 = "PG_FORECAST_BELIEF_TRACKER_V3"
FORECAST_BELIEF_STATE_SCHEMA_V3 = "PG_FORECAST_BELIEF_STATE_V3"
FORECAST_BELIEF_SIDES_V3 = ("BUY", "HOLD", "SELL")
FORECAST_BELIEF_STATUSES_V3 = (
    "RESET",
    "REACQUIRING",
    "STABLE",
    "REVERSAL_PENDING",
)

_SIDE_INDEX = {side: index for index, side in enumerate(FORECAST_BELIEF_SIDES_V3)}
_OPPOSITE_SIDE = {"BUY": "SELL", "SELL": "BUY"}
_UNIFORM_BELIEF = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)


def _finite_float(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _bounded_probability(value: Any, *, name: str, inclusive_zero: bool = True) -> float:
    number = _finite_float(value, name=name)
    minimum = 0.0 if inclusive_zero else 0.0 + 1e-15
    if number < minimum or number > 1.0:
        qualifier = "[0, 1]" if inclusive_zero else "(0, 1]"
        raise ValueError(f"{name} must be within {qualifier}")
    return number


def _canonical_pair(value: Any) -> str:
    pair = str(value or "").strip().upper().replace(" ", "")
    if not pair:
        raise ValueError("pair must be non-empty")
    return pair


def _canonical_timeframe(value: Any) -> str:
    timeframe = str(value or "").strip().upper().replace(" ", "")
    if not timeframe:
        raise ValueError("timeframe must be non-empty")
    return timeframe


def _belief_tuple(values: Sequence[Any], *, name: str) -> tuple[float, float, float]:
    if len(values) != len(FORECAST_BELIEF_SIDES_V3):
        raise ValueError(f"{name} must contain BUY, HOLD, and SELL values")
    numbers = tuple(
        _finite_float(value, name=f"{name}[{side}]")
        for side, value in zip(FORECAST_BELIEF_SIDES_V3, values)
    )
    if any(value < 0.0 for value in numbers):
        raise ValueError(f"{name} values cannot be negative")
    total = sum(numbers)
    if total <= 0.0:
        raise ValueError(f"{name} must have positive total mass")
    normalized = tuple(value / total for value in numbers)
    return cast(tuple[float, float, float], normalized)


def _belief_mapping(values: Sequence[float]) -> dict[str, float]:
    return {
        side: float(values[index])
        for index, side in enumerate(FORECAST_BELIEF_SIDES_V3)
    }


@dataclass(frozen=True, slots=True)
class ForecastBeliefConfigV3:
    """All numerical policy for the V3 belief filter.

    The tracker intentionally has no hidden switching constants. A caller can
    persist this dataclass beside the promoted forecaster artifact and recreate
    the same transition and confirmation policy after a process restart.
    """

    direction_stay_probability: float = 0.90
    hold_stay_probability: float = 0.78
    minimum_stay_probability: float = 0.50
    maximum_stay_probability: float = 0.98
    adaptive_stickiness_strength: float = 0.22
    opposite_transition_share: float = 0.25
    emission_floor: float = 1e-6
    activation_posterior_threshold: float = 0.56
    activation_margin_threshold: float = 0.08
    hold_posterior_threshold: float = 0.58
    hold_margin_threshold: float = 0.08
    reversal_posterior_threshold: float = 0.62
    reversal_margin_threshold: float = 0.12
    reacquire_confirmation_events: int = 2
    reversal_confirmation_events: int = 2
    hold_confirmation_events: int = 2
    maximum_contiguous_event_gap: int = 1
    require_calibrated_emissions: bool = True

    def __post_init__(self) -> None:
        probability_fields = (
            "direction_stay_probability",
            "hold_stay_probability",
            "minimum_stay_probability",
            "maximum_stay_probability",
            "adaptive_stickiness_strength",
            "opposite_transition_share",
            "activation_posterior_threshold",
            "activation_margin_threshold",
            "hold_posterior_threshold",
            "hold_margin_threshold",
            "reversal_posterior_threshold",
            "reversal_margin_threshold",
        )
        for field_name in probability_fields:
            _bounded_probability(getattr(self, field_name), name=field_name)
        if self.minimum_stay_probability > self.maximum_stay_probability:
            raise ValueError(
                "minimum_stay_probability cannot exceed maximum_stay_probability"
            )
        for field_name in ("direction_stay_probability", "hold_stay_probability"):
            value = float(getattr(self, field_name))
            if not self.minimum_stay_probability <= value <= self.maximum_stay_probability:
                raise ValueError(
                    f"{field_name} must be within the configured stay-probability bounds"
                )
        emission_floor = _bounded_probability(
            self.emission_floor,
            name="emission_floor",
            inclusive_zero=False,
        )
        if emission_floor >= 1.0 / len(FORECAST_BELIEF_SIDES_V3):
            raise ValueError("emission_floor must be below one third")
        for field_name in (
            "reacquire_confirmation_events",
            "reversal_confirmation_events",
            "hold_confirmation_events",
            "maximum_contiguous_event_gap",
        ):
            if int(getattr(self, field_name)) < 1:
                raise ValueError(f"{field_name} must be at least one")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ForecastBeliefConfigV3:
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown forecast-belief config fields: {unknown}")
        return cls(**{key: value[key] for key in value if key in allowed})


def normalize_calibrated_emissions_v3(
    emissions: Mapping[str, Any] | Sequence[Any],
    *,
    config: ForecastBeliefConfigV3,
) -> tuple[float, float, float]:
    """Return finite BUY/HOLD/SELL probabilities with a configured floor."""

    if isinstance(emissions, Mapping):
        normalized_keys = {str(key).strip().upper(): value for key, value in emissions.items()}
        missing = [side for side in FORECAST_BELIEF_SIDES_V3 if side not in normalized_keys]
        if missing:
            raise ValueError(f"emissions are missing required sides: {missing}")
        raw = [normalized_keys[side] for side in FORECAST_BELIEF_SIDES_V3]
    elif not isinstance(emissions, (str, bytes, bytearray)):
        raw = list(emissions)
    else:
        raise ValueError("emissions must be a BUY/HOLD/SELL mapping or sequence")

    probabilities = _belief_tuple(raw, name="emissions")
    floored = tuple(max(float(config.emission_floor), value) for value in probabilities)
    total = sum(floored)
    return cast(tuple[float, float, float], tuple(value / total for value in floored))


@dataclass(frozen=True, slots=True)
class ForecastBeliefRevisionV3:
    revision: int
    event_type: str
    pair: str
    timeframe: str
    closed_candle_key: str
    closed_candle_sequence: int
    frame_id: int
    observed_at_epoch: float
    source_id: str
    calibrated: bool
    status: str
    active_side: str
    candidate_side: str
    pending_side: str
    pending_count: int
    required_count: int
    reason: str
    reset_reason: str
    emissions: tuple[float, float, float]
    posterior_before: tuple[float, float, float]
    posterior_after: tuple[float, float, float]
    transition_matrix: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "event_type": self.event_type,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "closed_candle_key": self.closed_candle_key,
            "closed_candle_sequence": self.closed_candle_sequence,
            "frame_id": self.frame_id,
            "observed_at_epoch": self.observed_at_epoch,
            "source_id": self.source_id,
            "calibrated": self.calibrated,
            "status": self.status,
            "active_side": self.active_side,
            "candidate_side": self.candidate_side,
            "pending_side": self.pending_side,
            "pending_count": self.pending_count,
            "required_count": self.required_count,
            "reason": self.reason,
            "reset_reason": self.reset_reason,
            "emissions": _belief_mapping(self.emissions),
            "posterior_before": _belief_mapping(self.posterior_before),
            "posterior_after": _belief_mapping(self.posterior_after),
            "transition_matrix": [
                [round(float(value), 12) for value in row]
                for row in self.transition_matrix
            ],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ForecastBeliefRevisionV3:
        emissions = _belief_from_serialized(value.get("emissions"), name="emissions")
        posterior_before = _belief_from_serialized(
            value.get("posterior_before"), name="posterior_before"
        )
        posterior_after = _belief_from_serialized(
            value.get("posterior_after"), name="posterior_after"
        )
        raw_matrix = value.get("transition_matrix")
        if not isinstance(raw_matrix, Sequence) or isinstance(
            raw_matrix, (str, bytes, bytearray)
        ):
            raise ValueError("revision transition_matrix must be a sequence")
        rows = tuple(
            _belief_tuple(cast(Sequence[Any], row), name="transition_matrix row")
            for row in cast(Sequence[object], raw_matrix)
            if isinstance(row, Sequence)
            and not isinstance(row, (str, bytes, bytearray))
        )
        if len(rows) != len(FORECAST_BELIEF_SIDES_V3):
            raise ValueError("revision transition_matrix must have three rows")
        status = str(value.get("status") or "").upper()
        if status not in FORECAST_BELIEF_STATUSES_V3:
            raise ValueError(f"unsupported belief status: {status!r}")
        active_side = str(value.get("active_side") or "").upper()
        candidate_side = str(value.get("candidate_side") or "").upper()
        pending_side = str(value.get("pending_side") or "").upper()
        for name, side, empty_allowed in (
            ("active_side", active_side, False),
            ("candidate_side", candidate_side, True),
            ("pending_side", pending_side, True),
        ):
            if side not in FORECAST_BELIEF_SIDES_V3 and not (empty_allowed and not side):
                raise ValueError(f"unsupported {name}: {side!r}")
        return cls(
            revision=int(value.get("revision", 0)),
            event_type=str(value.get("event_type") or "UPDATE").upper(),
            pair=_canonical_pair(value.get("pair")),
            timeframe=_canonical_timeframe(value.get("timeframe")),
            closed_candle_key=str(value.get("closed_candle_key") or ""),
            closed_candle_sequence=int(value.get("closed_candle_sequence", -1)),
            frame_id=int(value.get("frame_id", -1)),
            observed_at_epoch=_finite_float(
                value.get("observed_at_epoch", 0.0), name="observed_at_epoch"
            ),
            source_id=str(value.get("source_id") or ""),
            calibrated=bool(value.get("calibrated", False)),
            status=status,
            active_side=active_side,
            candidate_side=candidate_side,
            pending_side=pending_side,
            pending_count=max(0, int(value.get("pending_count", 0))),
            required_count=max(0, int(value.get("required_count", 0))),
            reason=str(value.get("reason") or ""),
            reset_reason=str(value.get("reset_reason") or ""),
            emissions=emissions,
            posterior_before=posterior_before,
            posterior_after=posterior_after,
            transition_matrix=cast(
                tuple[
                    tuple[float, float, float],
                    tuple[float, float, float],
                    tuple[float, float, float],
                ],
                rows,
            ),
        )


@dataclass(frozen=True, slots=True)
class ForecastBeliefUpdateV3:
    accepted: bool
    reason: str
    pair: str
    timeframe: str
    status: str
    active_side: str
    candidate_side: str
    pending_side: str
    pending_count: int
    required_count: int
    revision: int
    closed_candle_key: str
    closed_candle_sequence: int
    frame_id: int
    posterior: tuple[float, float, float]
    record: ForecastBeliefRevisionV3 | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FORECAST_BELIEF_TRACKER_SCHEMA_V3,
            "accepted": self.accepted,
            "reason": self.reason,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "status": self.status,
            "active_side": self.active_side,
            "candidate_side": self.candidate_side,
            "pending_side": self.pending_side,
            "pending_count": self.pending_count,
            "required_count": self.required_count,
            "revision": self.revision,
            "closed_candle_key": self.closed_candle_key,
            "closed_candle_sequence": self.closed_candle_sequence,
            "frame_id": self.frame_id,
            "posterior": _belief_mapping(self.posterior),
            "record": self.record.to_dict() if self.record is not None else None,
        }


@dataclass(slots=True)
class _ContextStateV3:
    pair: str
    timeframe: str
    posterior: tuple[float, float, float] = _UNIFORM_BELIEF
    active_side: str = "HOLD"
    status: str = "RESET"
    pending_side: str = ""
    pending_count: int = 0
    revision: int = 0
    last_closed_candle_key: str = ""
    last_closed_candle_sequence: int = -1
    last_frame_id: int = -1
    last_emissions: tuple[float, float, float] = _UNIFORM_BELIEF
    seen_closed_candle_keys: set[str] | None = None
    revisions: list[ForecastBeliefRevisionV3] | None = None

    def __post_init__(self) -> None:
        if self.seen_closed_candle_keys is None:
            self.seen_closed_candle_keys = set()
        if self.revisions is None:
            self.revisions = []


def _belief_from_serialized(value: Any, *, name: str) -> tuple[float, float, float]:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        rows: list[Any] = [mapping.get(side) for side in FORECAST_BELIEF_SIDES_V3]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rows = list(cast(Sequence[Any], value))
    else:
        raise ValueError(f"{name} must be a BUY/HOLD/SELL mapping or sequence")
    if len(rows) != len(FORECAST_BELIEF_SIDES_V3):
        raise ValueError(f"{name} must contain BUY, HOLD, and SELL values")
    numbers = tuple(
        _finite_float(item, name=f"{name}[{side}]")
        for side, item in zip(FORECAST_BELIEF_SIDES_V3, rows)
    )
    if any(item < 0.0 for item in numbers):
        raise ValueError(f"{name} values cannot be negative")
    if not math.isclose(sum(numbers), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"{name} must sum to one")
    return cast(tuple[float, float, float], numbers)


def _adaptive_transition_matrix(
    emissions: tuple[float, float, float],
    config: ForecastBeliefConfigV3,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    rows: list[tuple[float, float, float]] = []
    for side in FORECAST_BELIEF_SIDES_V3:
        index = _SIDE_INDEX[side]
        base_stay = (
            config.hold_stay_probability
            if side == "HOLD"
            else config.direction_stay_probability
        )
        if side == "HOLD":
            opposition = max(emissions[_SIDE_INDEX["BUY"]], emissions[_SIDE_INDEX["SELL"]])
        else:
            opposition = emissions[_SIDE_INDEX[_OPPOSITE_SIDE[side]]]
        adaptive_delta = config.adaptive_stickiness_strength * (
            emissions[index] - opposition
        )
        stay = max(
            config.minimum_stay_probability,
            min(config.maximum_stay_probability, base_stay + adaptive_delta),
        )
        remaining = 1.0 - stay
        row = [0.0, 0.0, 0.0]
        row[index] = stay
        if side == "BUY":
            row[_SIDE_INDEX["SELL"]] = remaining * config.opposite_transition_share
            row[_SIDE_INDEX["HOLD"]] = remaining - row[_SIDE_INDEX["SELL"]]
        elif side == "SELL":
            row[_SIDE_INDEX["BUY"]] = remaining * config.opposite_transition_share
            row[_SIDE_INDEX["HOLD"]] = remaining - row[_SIDE_INDEX["BUY"]]
        else:
            directional_total = emissions[_SIDE_INDEX["BUY"]] + emissions[_SIDE_INDEX["SELL"]]
            buy_share = (
                emissions[_SIDE_INDEX["BUY"]] / directional_total
                if directional_total > 0.0
                else 0.5
            )
            row[_SIDE_INDEX["BUY"]] = remaining * buy_share
            row[_SIDE_INDEX["SELL"]] = remaining - row[_SIDE_INDEX["BUY"]]
        rows.append(cast(tuple[float, float, float], tuple(row)))
    return cast(
        tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ],
        tuple(rows),
    )


def _hmm_posterior(
    prior: tuple[float, float, float],
    emissions: tuple[float, float, float],
    transition: Sequence[Sequence[float]],
) -> tuple[float, float, float]:
    predicted = [
        sum(prior[source] * transition[source][target] for source in range(3))
        for target in range(3)
    ]
    updated = [predicted[index] * emissions[index] for index in range(3)]
    total = sum(updated)
    if total <= 0.0 or not math.isfinite(total):
        return _UNIFORM_BELIEF
    return cast(tuple[float, float, float], tuple(value / total for value in updated))


def _candidate_from_posterior(
    posterior: tuple[float, float, float],
) -> tuple[str, float, float]:
    ordered = sorted(
        range(len(FORECAST_BELIEF_SIDES_V3)),
        key=lambda index: (-posterior[index], index),
    )
    candidate = FORECAST_BELIEF_SIDES_V3[ordered[0]]
    probability = posterior[ordered[0]]
    margin = probability - posterior[ordered[1]]
    return candidate, probability, margin


def _validated_config(value: object) -> ForecastBeliefConfigV3:
    if not isinstance(value, ForecastBeliefConfigV3):
        raise TypeError("config must be ForecastBeliefConfigV3")
    return value


class ForecastBeliefTrackerV3:
    """Model-agnostic, event-locked belief and side-stability tracker."""

    def __init__(self, config: ForecastBeliefConfigV3) -> None:
        self.config = _validated_config(config)
        self._contexts: dict[tuple[str, str], _ContextStateV3] = {}

    def _context_key(self, pair: Any, timeframe: Any) -> tuple[str, str]:
        return _canonical_pair(pair), _canonical_timeframe(timeframe)

    def _state(self, pair: str, timeframe: str) -> _ContextStateV3:
        key = (pair, timeframe)
        state = self._contexts.get(key)
        if state is None:
            state = _ContextStateV3(pair=pair, timeframe=timeframe)
            self._contexts[key] = state
        return state

    def _decision(
        self,
        state: _ContextStateV3,
        *,
        accepted: bool,
        reason: str,
        candidate_side: str = "",
        required_count: int = 0,
        record: ForecastBeliefRevisionV3 | None = None,
        requested_key: str | None = None,
        requested_sequence: int | None = None,
        requested_frame_id: int | None = None,
    ) -> ForecastBeliefUpdateV3:
        return ForecastBeliefUpdateV3(
            accepted=accepted,
            reason=reason,
            pair=state.pair,
            timeframe=state.timeframe,
            status=state.status,
            active_side=state.active_side,
            candidate_side=candidate_side,
            pending_side=state.pending_side,
            pending_count=state.pending_count,
            required_count=required_count,
            revision=state.revision,
            closed_candle_key=(
                state.last_closed_candle_key
                if requested_key is None
                else requested_key
            ),
            closed_candle_sequence=(
                state.last_closed_candle_sequence
                if requested_sequence is None
                else requested_sequence
            ),
            frame_id=(
                state.last_frame_id
                if requested_frame_id is None
                else requested_frame_id
            ),
            posterior=state.posterior,
            record=record,
        )

    def _append_reset_revision(
        self,
        state: _ContextStateV3,
        *,
        reason: str,
        observed_at_epoch: float,
        source_id: str,
        frame_id: int,
    ) -> ForecastBeliefRevisionV3:
        before = state.posterior
        state.posterior = _UNIFORM_BELIEF
        state.active_side = "HOLD"
        state.status = "RESET"
        state.pending_side = ""
        state.pending_count = 0
        state.last_closed_candle_key = ""
        state.last_closed_candle_sequence = -1
        state.last_frame_id = -1
        state.last_emissions = _UNIFORM_BELIEF
        cast(set[str], state.seen_closed_candle_keys).clear()
        state.revision += 1
        identity: tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ] = (
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        record = ForecastBeliefRevisionV3(
            revision=state.revision,
            event_type="RESET",
            pair=state.pair,
            timeframe=state.timeframe,
            closed_candle_key="",
            closed_candle_sequence=-1,
            frame_id=frame_id,
            observed_at_epoch=observed_at_epoch,
            source_id=source_id,
            calibrated=False,
            status="RESET",
            active_side="HOLD",
            candidate_side="",
            pending_side="",
            pending_count=0,
            required_count=0,
            reason="RESET",
            reset_reason=reason,
            emissions=_UNIFORM_BELIEF,
            posterior_before=before,
            posterior_after=_UNIFORM_BELIEF,
            transition_matrix=identity,
        )
        cast(list[ForecastBeliefRevisionV3], state.revisions).append(record)
        return record

    def reset(
        self,
        *,
        pair: Any,
        timeframe: Any,
        reason: str,
        observed_at_epoch: float | None = None,
        source_id: str = "",
        frame_id: int = -1,
    ) -> ForecastBeliefUpdateV3:
        canonical_pair, canonical_timeframe = self._context_key(pair, timeframe)
        state = self._state(canonical_pair, canonical_timeframe)
        observed = (
            time.time()
            if observed_at_epoch is None
            else _finite_float(observed_at_epoch, name="observed_at_epoch")
        )
        reset_reason = str(reason or "").strip()
        if not reset_reason:
            raise ValueError("reset reason must be non-empty")
        record = self._append_reset_revision(
            state,
            reason=reset_reason,
            observed_at_epoch=observed,
            source_id=str(source_id or ""),
            frame_id=int(frame_id),
        )
        return self._decision(
            state,
            accepted=True,
            reason="RESET",
            record=record,
            requested_frame_id=int(frame_id),
        )

    def update(
        self,
        *,
        pair: Any,
        timeframe: Any,
        closed_candle_key: Any,
        closed_candle_sequence: int,
        frame_id: int,
        emissions: Mapping[str, Any] | Sequence[Any],
        calibrated: bool,
        observed_at_epoch: float | None = None,
        source_id: str = "",
    ) -> ForecastBeliefUpdateV3:
        canonical_pair, canonical_timeframe = self._context_key(pair, timeframe)
        event_key = str(closed_candle_key or "").strip()
        if not event_key:
            raise ValueError("closed_candle_key must be non-empty")
        event_sequence = int(closed_candle_sequence)
        requested_frame = int(frame_id)
        if event_sequence < 0:
            raise ValueError("closed_candle_sequence cannot be negative")
        if requested_frame < 0:
            raise ValueError("frame_id cannot be negative")

        existing = self._contexts.get((canonical_pair, canonical_timeframe))
        if existing is not None and existing.last_closed_candle_sequence >= 0:
            if requested_frame < existing.last_frame_id:
                return self._decision(
                    existing,
                    accepted=False,
                    reason="OUT_OF_ORDER_FRAME",
                    requested_key=event_key,
                    requested_sequence=event_sequence,
                    requested_frame_id=requested_frame,
                )
            if requested_frame == existing.last_frame_id:
                return self._decision(
                    existing,
                    accepted=False,
                    reason="DUPLICATE_FRAME",
                    requested_key=event_key,
                    requested_sequence=event_sequence,
                    requested_frame_id=requested_frame,
                )
            if event_sequence < existing.last_closed_candle_sequence:
                return self._decision(
                    existing,
                    accepted=False,
                    reason="OUT_OF_ORDER_CLOSED_CANDLE",
                    requested_key=event_key,
                    requested_sequence=event_sequence,
                    requested_frame_id=requested_frame,
                )
            if event_sequence == existing.last_closed_candle_sequence:
                reason = (
                    "DUPLICATE_CLOSED_CANDLE"
                    if event_key == existing.last_closed_candle_key
                    else "CLOSED_CANDLE_SEQUENCE_COLLISION"
                )
                return self._decision(
                    existing,
                    accepted=False,
                    reason=reason,
                    requested_key=event_key,
                    requested_sequence=event_sequence,
                    requested_frame_id=requested_frame,
                )
            if event_key in cast(set[str], existing.seen_closed_candle_keys):
                return self._decision(
                    existing,
                    accepted=False,
                    reason="REPLAYED_CLOSED_CANDLE_KEY",
                    requested_key=event_key,
                    requested_sequence=event_sequence,
                    requested_frame_id=requested_frame,
                )

        if self.config.require_calibrated_emissions and calibrated is not True:
            state = existing or _ContextStateV3(
                pair=canonical_pair,
                timeframe=canonical_timeframe,
            )
            return self._decision(
                state,
                accepted=False,
                reason="UNCALIBRATED_EMISSIONS",
                requested_key=event_key,
                requested_sequence=event_sequence,
                requested_frame_id=requested_frame,
            )
        normalized_emissions = normalize_calibrated_emissions_v3(
            emissions,
            config=self.config,
        )
        observed = (
            time.time()
            if observed_at_epoch is None
            else _finite_float(observed_at_epoch, name="observed_at_epoch")
        )
        state = existing or self._state(canonical_pair, canonical_timeframe)
        gap_reset = bool(
            state.last_closed_candle_sequence >= 0
            and event_sequence - state.last_closed_candle_sequence
            > self.config.maximum_contiguous_event_gap
        )
        if gap_reset:
            self._append_reset_revision(
                state,
                reason="CLOSED_CANDLE_EVENT_GAP",
                observed_at_epoch=observed,
                source_id=str(source_id or ""),
                frame_id=requested_frame,
            )

        posterior_before = state.posterior
        transition = _adaptive_transition_matrix(normalized_emissions, self.config)
        posterior_after = _hmm_posterior(
            posterior_before,
            normalized_emissions,
            transition,
        )
        candidate_side, candidate_probability, candidate_margin = (
            _candidate_from_posterior(posterior_after)
        )
        direction_qualified = bool(
            candidate_side in _OPPOSITE_SIDE
            and candidate_probability >= self.config.activation_posterior_threshold
            and candidate_margin >= self.config.activation_margin_threshold
        )
        hold_qualified = bool(
            candidate_side == "HOLD"
            and candidate_probability >= self.config.hold_posterior_threshold
            and candidate_margin >= self.config.hold_margin_threshold
        )
        reversal_qualified = bool(
            candidate_side in _OPPOSITE_SIDE
            and candidate_probability >= self.config.reversal_posterior_threshold
            and candidate_margin >= self.config.reversal_margin_threshold
        )

        reason = "BELIEF_STABLE"
        required_count = 0
        if (
            state.active_side == "HOLD"
            and state.status == "STABLE"
            and candidate_side == "HOLD"
        ):
            state.pending_side = ""
            state.pending_count = 0
            reason = (
                "BELIEF_STABLE"
                if hold_qualified
                else "SWITCH_EVIDENCE_INSUFFICIENT"
            )
        elif (
            state.active_side == "HOLD"
            and state.status == "STABLE"
            and not direction_qualified
        ):
            state.pending_side = ""
            state.pending_count = 0
            reason = "SWITCH_EVIDENCE_INSUFFICIENT"
        elif state.active_side == "HOLD":
            required_count = (
                self.config.hold_confirmation_events
                if candidate_side == "HOLD"
                else self.config.reacquire_confirmation_events
            )
            qualified = hold_qualified if candidate_side == "HOLD" else direction_qualified
            if qualified:
                if state.pending_side == candidate_side:
                    state.pending_count += 1
                else:
                    state.pending_side = candidate_side
                    state.pending_count = 1
                if state.pending_count >= required_count:
                    state.active_side = candidate_side
                    state.status = "STABLE"
                    state.pending_side = ""
                    state.pending_count = 0
                    reason = "REACQUISITION_CONFIRMED"
                else:
                    state.status = "REACQUIRING"
                    reason = "REACQUISITION_PENDING"
            else:
                state.status = "REACQUIRING"
                state.pending_side = ""
                state.pending_count = 0
                reason = "REACQUISITION_EVIDENCE_INSUFFICIENT"
        elif candidate_side == state.active_side and direction_qualified:
            state.status = "STABLE"
            state.pending_side = ""
            state.pending_count = 0
            reason = "BELIEF_STABLE"
        elif (
            candidate_side == _OPPOSITE_SIDE.get(state.active_side)
            and reversal_qualified
        ):
            required_count = self.config.reversal_confirmation_events
            if state.pending_side == candidate_side:
                state.pending_count += 1
            else:
                state.pending_side = candidate_side
                state.pending_count = 1
            if state.pending_count >= required_count:
                state.active_side = candidate_side
                state.status = "STABLE"
                state.pending_side = ""
                state.pending_count = 0
                reason = "REVERSAL_CONFIRMED"
            else:
                state.status = "REVERSAL_PENDING"
                reason = "REVERSAL_PENDING"
        elif candidate_side == "HOLD" and hold_qualified:
            required_count = self.config.hold_confirmation_events
            if state.pending_side == "HOLD":
                state.pending_count += 1
            else:
                state.pending_side = "HOLD"
                state.pending_count = 1
            if state.pending_count >= required_count:
                state.active_side = "HOLD"
                state.status = "STABLE"
                state.pending_side = ""
                state.pending_count = 0
                reason = "HOLD_CONFIRMED"
            else:
                state.status = "REACQUIRING"
                reason = "HOLD_PENDING"
        else:
            state.status = "STABLE"
            state.pending_side = ""
            state.pending_count = 0
            reason = "SWITCH_EVIDENCE_INSUFFICIENT"

        if gap_reset and state.status != "STABLE":
            reason = "EVENT_GAP_REACQUIRING"
        state.posterior = posterior_after
        state.last_emissions = normalized_emissions
        state.last_closed_candle_key = event_key
        state.last_closed_candle_sequence = event_sequence
        state.last_frame_id = requested_frame
        cast(set[str], state.seen_closed_candle_keys).add(event_key)
        state.revision += 1
        record = ForecastBeliefRevisionV3(
            revision=state.revision,
            event_type="UPDATE",
            pair=state.pair,
            timeframe=state.timeframe,
            closed_candle_key=event_key,
            closed_candle_sequence=event_sequence,
            frame_id=requested_frame,
            observed_at_epoch=observed,
            source_id=str(source_id or ""),
            calibrated=bool(calibrated),
            status=state.status,
            active_side=state.active_side,
            candidate_side=candidate_side,
            pending_side=state.pending_side,
            pending_count=state.pending_count,
            required_count=required_count,
            reason=reason,
            reset_reason="CLOSED_CANDLE_EVENT_GAP" if gap_reset else "",
            emissions=normalized_emissions,
            posterior_before=posterior_before,
            posterior_after=posterior_after,
            transition_matrix=transition,
        )
        cast(list[ForecastBeliefRevisionV3], state.revisions).append(record)
        return self._decision(
            state,
            accepted=True,
            reason=reason,
            candidate_side=candidate_side,
            required_count=required_count,
            record=record,
        )

    def records(
        self, *, pair: Any, timeframe: Any
    ) -> tuple[ForecastBeliefRevisionV3, ...]:
        key = self._context_key(pair, timeframe)
        state = self._contexts.get(key)
        return tuple(cast(list[ForecastBeliefRevisionV3], state.revisions)) if state else ()

    def snapshot(self, *, pair: Any, timeframe: Any) -> dict[str, Any]:
        canonical_pair, canonical_timeframe = self._context_key(pair, timeframe)
        state = self._contexts.get((canonical_pair, canonical_timeframe))
        if state is None:
            return {
                "schema_version": FORECAST_BELIEF_TRACKER_SCHEMA_V3,
                "pair": canonical_pair,
                "timeframe": canonical_timeframe,
                "status": "RESET",
                "active_side": "HOLD",
                "pending_side": "",
                "pending_count": 0,
                "revision": 0,
                "last_closed_candle_key": "",
                "last_closed_candle_sequence": -1,
                "last_frame_id": -1,
                "posterior": _belief_mapping(_UNIFORM_BELIEF),
                "last_emissions": _belief_mapping(_UNIFORM_BELIEF),
            }
        return self._context_to_dict(state, include_records=False)

    def _context_to_dict(
        self, state: _ContextStateV3, *, include_records: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": FORECAST_BELIEF_TRACKER_SCHEMA_V3,
            "pair": state.pair,
            "timeframe": state.timeframe,
            "status": state.status,
            "active_side": state.active_side,
            "pending_side": state.pending_side,
            "pending_count": state.pending_count,
            "revision": state.revision,
            "last_closed_candle_key": state.last_closed_candle_key,
            "last_closed_candle_sequence": state.last_closed_candle_sequence,
            "last_frame_id": state.last_frame_id,
            "posterior": _belief_mapping(state.posterior),
            "last_emissions": _belief_mapping(state.last_emissions),
            "seen_closed_candle_keys": sorted(
                cast(set[str], state.seen_closed_candle_keys)
            ),
        }
        if include_records:
            payload["revisions"] = [
                record.to_dict()
                for record in cast(list[ForecastBeliefRevisionV3], state.revisions)
            ]
        return payload

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FORECAST_BELIEF_STATE_SCHEMA_V3,
            "tracker_schema_version": FORECAST_BELIEF_TRACKER_SCHEMA_V3,
            "config": self.config.to_dict(),
            "contexts": [
                self._context_to_dict(state, include_records=True)
                for _key, state in sorted(self._contexts.items())
            ],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_state_dict(),
            indent=indent,
            sort_keys=True,
            allow_nan=False,
        )

    @classmethod
    def from_state_dict(
        cls,
        value: Mapping[str, Any],
        *,
        config: ForecastBeliefConfigV3 | None = None,
    ) -> ForecastBeliefTrackerV3:
        if value.get("schema_version") != FORECAST_BELIEF_STATE_SCHEMA_V3:
            raise ValueError("unsupported forecast-belief state schema")
        raw_config = value.get("config")
        if not isinstance(raw_config, Mapping):
            raise ValueError("forecast-belief state config is missing")
        stored_config = ForecastBeliefConfigV3.from_mapping(
            cast(Mapping[str, Any], raw_config)
        )
        if config is not None and config != stored_config:
            raise ValueError("provided config does not match serialized belief state")
        tracker = cls(config or stored_config)
        raw_contexts = value.get("contexts", [])
        if not isinstance(raw_contexts, Sequence) or isinstance(
            raw_contexts, (str, bytes, bytearray)
        ):
            raise ValueError("forecast-belief contexts must be a sequence")
        context_items = cast(Sequence[object], raw_contexts)
        for raw_context in context_items:
            if not isinstance(raw_context, Mapping):
                raise ValueError("forecast-belief context must be an object")
            context = cast(Mapping[str, Any], raw_context)
            pair = _canonical_pair(context.get("pair"))
            timeframe = _canonical_timeframe(context.get("timeframe"))
            status = str(context.get("status") or "").upper()
            active_side = str(context.get("active_side") or "").upper()
            pending_side = str(context.get("pending_side") or "").upper()
            if status not in FORECAST_BELIEF_STATUSES_V3:
                raise ValueError(f"unsupported belief status: {status!r}")
            if active_side not in FORECAST_BELIEF_SIDES_V3:
                raise ValueError(f"unsupported active side: {active_side!r}")
            if pending_side and pending_side not in FORECAST_BELIEF_SIDES_V3:
                raise ValueError(f"unsupported pending side: {pending_side!r}")
            revisions_raw = context.get("revisions", [])
            if not isinstance(revisions_raw, Sequence) or isinstance(
                revisions_raw, (str, bytes, bytearray)
            ):
                raise ValueError("context revisions must be a sequence")
            revision_items = cast(Sequence[object], revisions_raw)
            revisions = [
                ForecastBeliefRevisionV3.from_mapping(
                    cast(Mapping[str, Any], raw_revision)
                )
                for raw_revision in revision_items
                if isinstance(raw_revision, Mapping)
            ]
            if len(revisions) != len(revision_items):
                raise ValueError("every serialized revision must be an object")
            revision_ids = [record.revision for record in revisions]
            if revision_ids != sorted(revision_ids) or len(set(revision_ids)) != len(
                revision_ids
            ):
                raise ValueError("revision records must have unique ascending IDs")
            state = _ContextStateV3(
                pair=pair,
                timeframe=timeframe,
                posterior=_belief_from_serialized(
                    context.get("posterior"), name="posterior"
                ),
                active_side=active_side,
                status=status,
                pending_side=pending_side,
                pending_count=max(0, int(context.get("pending_count", 0))),
                revision=max(0, int(context.get("revision", 0))),
                last_closed_candle_key=str(
                    context.get("last_closed_candle_key") or ""
                ),
                last_closed_candle_sequence=int(
                    context.get("last_closed_candle_sequence", -1)
                ),
                last_frame_id=int(context.get("last_frame_id", -1)),
                last_emissions=_belief_from_serialized(
                    context.get("last_emissions"), name="last_emissions"
                ),
                seen_closed_candle_keys={
                    str(item)
                    for item in cast(
                        Sequence[Any], context.get("seen_closed_candle_keys", [])
                    )
                },
                revisions=revisions,
            )
            if revisions and revisions[-1].revision != state.revision:
                raise ValueError("context revision does not match its final record")
            key = (pair, timeframe)
            if key in tracker._contexts:
                raise ValueError(f"duplicate serialized belief context: {key}")
            tracker._contexts[key] = state
        return tracker

    @classmethod
    def from_json(
        cls,
        value: str,
        *,
        config: ForecastBeliefConfigV3 | None = None,
    ) -> ForecastBeliefTrackerV3:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid forecast-belief JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("forecast-belief JSON root must be an object")
        return cls.from_state_dict(cast(Mapping[str, Any], payload), config=config)


__all__ = [
    "FORECAST_BELIEF_SIDES_V3",
    "FORECAST_BELIEF_STATE_SCHEMA_V3",
    "FORECAST_BELIEF_STATUSES_V3",
    "FORECAST_BELIEF_TRACKER_SCHEMA_V3",
    "ForecastBeliefConfigV3",
    "ForecastBeliefRevisionV3",
    "ForecastBeliefTrackerV3",
    "ForecastBeliefUpdateV3",
    "normalize_calibrated_emissions_v3",
]
