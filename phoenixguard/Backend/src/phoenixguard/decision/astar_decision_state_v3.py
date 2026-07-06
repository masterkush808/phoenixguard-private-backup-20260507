from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import cast


YES_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "y", "on", "allowed", "ready", "pass", "passed"})
NO_VALUES: frozenset[str] = frozenset({"0", "false", "no", "n", "off", "blocked", "fail", "failed", "none", "null"})
VALID_DECISION_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})
NESTED_CONTEXT_KEYS: tuple[str, ...] = (
    "market_context",
    "timing_decision",
    "entry_timing",
    "play_evidence",
    "professional_trade_plan",
    "support_resistance_context",
    "promotion_trace",
    "execution_lane",
)


class PullbackPhaseV3(str, Enum):
    PULLBACK_NOT_STARTED = "PULLBACK_NOT_STARTED"
    PULLBACK_IN_PROGRESS = "PULLBACK_IN_PROGRESS"
    PULLBACK_HELD = "PULLBACK_HELD"
    PULLBACK_RECLAIMED = "PULLBACK_RECLAIMED"
    PULLBACK_FAILED = "PULLBACK_FAILED"
    LATE_CHASE = "LATE_CHASE"
    NOT_REQUIRED = "PULLBACK_NOT_STARTED"
    WAITING_FOR_PULLBACK = "PULLBACK_IN_PROGRESS"


class LiveThesisStateV3(str, Enum):
    BUY_INACTIVE = "BUY_INACTIVE"
    BUY_FORMING = "BUY_FORMING"
    BUY_CONFIRMED = "BUY_CONFIRMED"
    BUY_ENTER_NOW = "BUY_ENTER_NOW"
    BUY_LATE = "BUY_LATE"
    BUY_INVALIDATED = "BUY_INVALIDATED"
    SELL_INACTIVE = "SELL_INACTIVE"
    SELL_FORMING = "SELL_FORMING"
    SELL_CONFIRMED = "SELL_CONFIRMED"
    SELL_ENTER_NOW = "SELL_ENTER_NOW"
    SELL_LATE = "SELL_LATE"
    SELL_INVALIDATED = "SELL_INVALIDATED"
    WATCHING = "WATCHING"
    PREPARING = "PREPARING"
    WAIT_FOR_PULLBACK = "WAIT_FOR_PULLBACK"
    ENTER_NOW = "ENTER_NOW"
    BLOCKED_BY_DISCIPLINE = "BLOCKED_BY_DISCIPLINE"
    BLOCKED_BY_RUNTIME = "BLOCKED_BY_RUNTIME"
    INVALIDATED = "INVALIDATED"


class InteractionStateV3(str, Enum):
    NONE = "NONE"
    REJECTED = "REJECTED"
    TESTING = "TESTING"
    ACCEPTED_ABOVE = "ACCEPTED_ABOVE"
    BROKEN_AND_RETESTING = "BROKEN_AND_RETESTING"
    BROKEN_AND_HOLDING = "BROKEN_AND_HOLDING"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    FAILED_RETEST = "FAILED_RETEST"
    SUPPORT_HELD = "SUPPORT_HELD"
    SUPPORT_REJECTED = "SUPPORT_REJECTED"
    RESISTANCE_REJECTED = "RESISTANCE_REJECTED"
    RESISTANCE_ACCEPTED_ABOVE = "RESISTANCE_ACCEPTED_ABOVE"
    ROLE_FLIP_RESISTANCE_TO_SUPPORT = "ROLE_FLIP_RESISTANCE_TO_SUPPORT"
    ROLE_FLIP_SUPPORT_TO_RESISTANCE = "ROLE_FLIP_SUPPORT_TO_RESISTANCE"


class MarketLocationV3(str, Enum):
    UNKNOWN = "UNKNOWN"
    LOW_VALUE_AREA = "LOW_VALUE_AREA"
    SUPPORT_REACTION_AREA = "SUPPORT_REACTION_AREA"
    AT_SUPPORT = "AT_SUPPORT"
    AT_RESISTANCE = "AT_RESISTANCE"
    PREMIUM_RANGE = "PREMIUM_RANGE"
    RESISTANCE_REACTION_AREA = "RESISTANCE_REACTION_AREA"
    ABOVE_RESISTANCE = "ABOVE_RESISTANCE"
    BELOW_SUPPORT = "BELOW_SUPPORT"
    MID_RANGE = "MID_RANGE"
    EXTREME_LOW = "EXTREME_LOW"
    EXTREME_HIGH = "EXTREME_HIGH"


class MidRangeDecisionDisciplineV3(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    STRONG_CONFIRMATION_REQUIRED = "STRONG_CONFIRMATION_REQUIRED"
    CONFIRMED = "CONFIRMED"
    BLOCKED_WEAK_CONFIRMATION = "BLOCKED_WEAK_CONFIRMATION"


class ConfirmationEventV3(str, Enum):
    NONE = "NONE"
    BODY_ACCEPTANCE = "BODY_ACCEPTANCE"
    WICK_REJECTION = "WICK_REJECTION"
    RETEST_HOLD = "RETEST_HOLD"
    RECLAIM_AFTER_SWEEP = "RECLAIM_AFTER_SWEEP"
    BREAK_AND_HOLD = "BREAK_AND_HOLD"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    FAILED_RETEST = "FAILED_RETEST"
    CONTINUATION_CANDLE = "CONTINUATION_CANDLE"
    OPPOSING_FORCE_REACTION = "OPPOSING_FORCE_REACTION"
    PULLBACK_HELD = "PULLBACK_HELD"
    PULLBACK_RECLAIMED = "PULLBACK_RECLAIMED"
    RETEST_HELD = "RETEST_HELD"
    RESISTANCE_ACCEPTED_ABOVE = "RESISTANCE_ACCEPTED_ABOVE"
    SUPPORT_REJECTED = "SUPPORT_REJECTED"
    RESISTANCE_REJECTION = "RESISTANCE_REJECTION"
    SUPPORT_ABSORPTION = "SUPPORT_ABSORPTION"
    BREAKOUT_CONFIRMATION = "BREAKOUT_CONFIRMATION"
    BREAKDOWN_CONFIRMATION = "BREAKDOWN_CONFIRMATION"
    STRONG_FLOW_CONFIRMATION = "STRONG_FLOW_CONFIRMATION"
    CURRENT_CANDLE_ACCEPTED = "CURRENT_CANDLE_ACCEPTED"


class BlockerTaxonomyV3(str, Enum):
    NONE = "NONE"
    TRUE_HARD_BLOCKER = "TRUE_HARD_BLOCKER"
    SOFT_WARNING = "SOFT_WARNING"
    WAIT_STATE = "WAIT_STATE"
    STRATEGY_CAUTION = "STRATEGY_CAUTION"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    HARD_RUNTIME_FAILURE = "HARD_RUNTIME_FAILURE"
    HARD_PACKET_FAILURE = "HARD_PACKET_FAILURE"
    HARD_PERMISSION_FAILURE = "HARD_PERMISSION_FAILURE"
    HARD_TIMING_FAILURE = "HARD_TIMING_FAILURE"
    HARD_STRUCTURE_FAILURE = "HARD_STRUCTURE_FAILURE"
    HARD_CONFIRMATION_FAILURE = "HARD_CONFIRMATION_FAILURE"
    HARD_DECISION_FAILURE = "HARD_DECISION_FAILURE"


@dataclass(frozen=True)
class ClassifiedBlockerV3:
    code: str
    field: str
    reason: str
    taxonomy: BlockerTaxonomyV3
    hard: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "field": self.field,
            "reason": self.reason,
            "taxonomy": self.taxonomy.value,
            "hard": self.hard,
        }


@dataclass(frozen=True)
class AuthorizationSurvivalTraceV3:
    requested_state: LiveThesisStateV3
    final_state: LiveThesisStateV3
    survived_enter_now: bool
    downgrade_layer: str
    downgrade_reason: str
    hard_blockers: tuple[str, ...]
    soft_warnings: tuple[str, ...]
    trace_steps: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "requested_state": self.requested_state.value,
            "final_state": self.final_state.value,
            "survived_enter_now": self.survived_enter_now,
            "downgrade_layer": self.downgrade_layer,
            "downgrade_reason": self.downgrade_reason,
            "hard_blockers": list(self.hard_blockers),
            "soft_warnings": list(self.soft_warnings),
            "trace_steps": list(self.trace_steps),
        }


@dataclass(frozen=True)
class CandidateDecisionLedgerV3:
    candidate_side: str
    evidence_side: str
    final_state: LiveThesisStateV3
    decision_allowed: bool
    pullback_phase: PullbackPhaseV3
    interaction_state: InteractionStateV3
    market_location: MarketLocationV3
    mid_range_discipline: MidRangeDecisionDisciplineV3
    confirmation_events: tuple[ConfirmationEventV3, ...]
    hard_blockers: tuple[ClassifiedBlockerV3, ...]
    soft_warnings: tuple[ClassifiedBlockerV3, ...]
    authorization_trace: AuthorizationSurvivalTraceV3

    def as_dict(self) -> dict[str, object]:
        blocker_codes = tuple(row.code for row in self.hard_blockers)
        return {
            "schema_version": "PG_ASTAR_DECISION_STATE_V3",
            "candidate_side": self.candidate_side,
            "evidence_side": self.evidence_side,
            "final_state": self.final_state.value,
            "decision_allowed": self.decision_allowed,
            "pullback_phase": self.pullback_phase.value,
            "interaction_state": self.interaction_state.value,
            "market_location": self.market_location.value,
            "mid_range_discipline": self.mid_range_discipline.value,
            "confirmation_events": [event.value for event in self.confirmation_events],
            "hard_blockers": [row.as_dict() for row in self.hard_blockers],
            "soft_warnings": [row.as_dict() for row in self.soft_warnings],
            "blocker_codes": list(blocker_codes),
            "authorization_trace": self.authorization_trace.as_dict(),
        }


@dataclass(frozen=True)
class _AuthorizationDecisionV3:
    requested_state: LiveThesisStateV3
    final_state: LiveThesisStateV3
    decision_allowed: bool
    downgrade_layer: str
    downgrade_reason: str
    hard_blockers: tuple[ClassifiedBlockerV3, ...]
    soft_warnings: tuple[ClassifiedBlockerV3, ...]


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return cast(Sequence[object], value)
    return ()


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "n/a"}:
        return default
    return text


def _upper(value: object) -> str:
    return _text(value).upper().replace("-", "_").replace(" ", "_")


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, float):
        return value == value and value != 0.0
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in YES_VALUES:
            return True
        if normalized in NO_VALUES:
            return False
    return bool(value)


def _float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return default
    return parsed


def _get_value(data: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in data:
            value = data[key]
            if value is not None and not (isinstance(value, str) and not _text(value)):
                return value
    for nested_key in NESTED_CONTEXT_KEYS:
        nested = _mapping(data.get(nested_key))
        if not nested:
            continue
        for key in keys:
            if key in nested:
                value = nested[key]
                if value is not None and not (isinstance(value, str) and not _text(value)):
                    return value
    return None


def _has_explicit_false(data: Mapping[str, object], *keys: str) -> bool:
    value = _get_value(data, *keys)
    return value is not None and not _bool(value)


def _any_true(data: Mapping[str, object], *keys: str) -> bool:
    return any(_bool(value) for value in (_get_value(data, key) for key in keys) if value is not None)


def _normalize_side(value: object) -> str:
    side = _upper(value)
    return side if side in VALID_DECISION_SIDES else ""


def _normalize_state(value: object | None) -> LiveThesisStateV3 | None:
    if value is None:
        return None
    normalized = _upper(value)
    if normalized in {"EXECUTABLE", "AUTHORIZED", "ENTRY_NOW"}:
        normalized = "ENTER_NOW"
    if normalized in {"BLOCKED", "DISCIPLINE_BLOCKED"}:
        normalized = "BLOCKED_BY_DISCIPLINE"
    for state in LiveThesisStateV3:
        if normalized in {state.name, state.value}:
            return state
    return None


def _requested_state(snapshot: Mapping[str, object]) -> LiveThesisStateV3:
    explicit = _normalize_state(
        _get_value(snapshot, "requested_state", "candidate_state", "opportunity_state", "book_strategy_state", "state")
    )
    if explicit is not None:
        return explicit
    timing_mode = _upper(_get_value(snapshot, "timing_mode", "mode"))
    if timing_mode == "ENTER_NOW" or _any_true(snapshot, "entry_now_requested", "entry_now_allowed", "decision_accepted"):
        return LiveThesisStateV3.ENTER_NOW
    if timing_mode == "WAIT_FOR_PULLBACK":
        return LiveThesisStateV3.WAIT_FOR_PULLBACK
    return LiveThesisStateV3.PREPARING


def _entry_now_intent(snapshot: Mapping[str, object], phase: PullbackPhaseV3) -> bool:
    timing_mode = _upper(_get_value(snapshot, "timing_mode", "mode"))
    requested = _requested_state(snapshot)
    return bool(
        requested == LiveThesisStateV3.ENTER_NOW
        or timing_mode == "ENTER_NOW"
        or _any_true(snapshot, "entry_now_requested", "entry_now_allowed", "decision_accepted")
        or phase in {PullbackPhaseV3.PULLBACK_HELD, PullbackPhaseV3.PULLBACK_RECLAIMED}
    )


def derive_pullback_phase_v3(snapshot: Mapping[str, object]) -> PullbackPhaseV3:
    if _any_true(snapshot, "pullback_failed", "pullback_invalidated", "retest_failed", "candidate_invalidated"):
        return PullbackPhaseV3.PULLBACK_FAILED
    if _any_true(
        snapshot,
        "pullback_reclaimed",
        "reclaim_confirmed",
        "pullback_reclaim_ready",
        "live_reclaim_breakout_ready",
        "role_flip_confirmed",
    ):
        return PullbackPhaseV3.PULLBACK_RECLAIMED
    if _any_true(snapshot, "pullback_held", "pullback_confirmed", "retest_held", "retest_confirmed"):
        return PullbackPhaseV3.PULLBACK_HELD
    if _upper(_get_value(snapshot, "timing_mode", "mode")) == "WAIT_FOR_PULLBACK" or _any_true(
        snapshot,
        "wait_for_pullback",
        "pullback_required",
        "pullback_not_confirmed",
        "retest_required",
    ):
        return PullbackPhaseV3.WAITING_FOR_PULLBACK
    return PullbackPhaseV3.NOT_REQUIRED


def evaluate_interaction_state_v3(snapshot: Mapping[str, object]) -> dict[str, object]:
    role = _upper(_get_value(snapshot, "zone_role", "role", "sr_role", "support_resistance_role")).lower()
    relation = _upper(_get_value(snapshot, "interaction", "relation", "relation_to_zone", "state", "reaction"))
    accepted_above = _any_true(
        snapshot,
        "accepted_above",
        "resistance_accepted_above",
        "breakout_accepted",
        "role_flip_to_support",
    ) or relation in {"ACCEPTED_ABOVE", "ABOVE_ACCEPTED", "BREAKOUT_ACCEPTED"}
    rejected = _any_true(
        snapshot,
        "rejected",
        "support_rejected",
        "breakdown_accepted",
        "accepted_below",
        "role_flip_to_resistance",
    ) or relation in {"REJECTED", "SUPPORT_REJECTED", "ACCEPTED_BELOW", "BREAKDOWN_ACCEPTED"}
    held = _any_true(snapshot, "held", "support_held", "retest_held", "wick_rejection", "absorption")

    state = InteractionStateV3.NONE
    event = ConfirmationEventV3.NONE
    evidence_side = ""
    role_flip = False
    reason = "No support/resistance interaction was confirmed."

    if role == "resistance" and accepted_above:
        state = InteractionStateV3.RESISTANCE_ACCEPTED_ABOVE
        event = ConfirmationEventV3.RESISTANCE_ACCEPTED_ABOVE
        evidence_side = "BUY"
        role_flip = True
        reason = "Resistance was accepted above; treat the level as buy-side role-flip evidence."
    elif role == "support" and rejected:
        state = InteractionStateV3.SUPPORT_REJECTED
        event = ConfirmationEventV3.SUPPORT_REJECTED
        evidence_side = "SELL"
        role_flip = True
        reason = "Support failed or rejected; treat the level as sell-side breakdown evidence."
    elif role == "resistance" and rejected:
        state = InteractionStateV3.RESISTANCE_REJECTED
        event = ConfirmationEventV3.RESISTANCE_REJECTION
        evidence_side = "SELL"
        reason = "Resistance rejected price; treat the level as sell-side reaction evidence."
    elif role == "support" and held:
        state = InteractionStateV3.SUPPORT_HELD
        event = ConfirmationEventV3.SUPPORT_ABSORPTION
        evidence_side = "BUY"
        reason = "Support held; treat the level as buy-side absorption evidence."

    return {
        "interaction_state": state.value,
        "zone_role": role,
        "evidence_side": evidence_side,
        "role_flip": role_flip,
        "confirmation_event": event.value,
        "reason": reason,
    }


def evaluate_support_resistance_interaction_v3(snapshot: Mapping[str, object]) -> dict[str, object]:
    return evaluate_interaction_state_v3(snapshot)


def derive_market_location_v3(snapshot: Mapping[str, object]) -> MarketLocationV3:
    label = _upper(_get_value(snapshot, "market_location", "location", "price_location", "price_location_label"))
    if "MID" in label or "MIDDLE" in label or "NO_EDGE" in label:
        return MarketLocationV3.MID_RANGE
    if "ABOVE_RESISTANCE" in label or "BREAKOUT" in label:
        return MarketLocationV3.ABOVE_RESISTANCE
    if "BELOW_SUPPORT" in label or "BREAKDOWN" in label:
        return MarketLocationV3.BELOW_SUPPORT
    if "EXTREME_HIGH" in label or "UPPER_EXTREME" in label:
        return MarketLocationV3.EXTREME_HIGH
    if "EXTREME_LOW" in label or "LOWER_EXTREME" in label:
        return MarketLocationV3.EXTREME_LOW
    if "SUPPORT" in label or "DEMAND" in label:
        return MarketLocationV3.AT_SUPPORT
    if "RESISTANCE" in label or "SUPPLY" in label:
        return MarketLocationV3.AT_RESISTANCE
    if _any_true(snapshot, "at_support", "near_support", "entry_support"):
        return MarketLocationV3.AT_SUPPORT
    if _any_true(snapshot, "at_resistance", "near_resistance", "entry_resistance"):
        return MarketLocationV3.AT_RESISTANCE
    range_position = _get_value(snapshot, "range_position", "price_range_position", "location_percentile")
    if range_position is not None:
        position = _float(range_position, 0.5)
        if 0.35 <= position <= 0.65:
            return MarketLocationV3.MID_RANGE
        if position <= 0.20:
            return MarketLocationV3.EXTREME_LOW
        if position >= 0.80:
            return MarketLocationV3.EXTREME_HIGH
        if position < 0.35:
            return MarketLocationV3.AT_SUPPORT
        if position > 0.65:
            return MarketLocationV3.AT_RESISTANCE
    return MarketLocationV3.UNKNOWN


def _confirmation_score(snapshot: Mapping[str, object], events: Sequence[ConfirmationEventV3]) -> float:
    explicit = _get_value(snapshot, "confirmation_score", "confirmation_strength", "flow_confirmation_score")
    score = _float(explicit, 0.0) if explicit is not None else 0.0
    if _any_true(snapshot, "strong_confirmation", "breakout_confirmation", "current_flow_continuation_ready"):
        score = max(score, 0.76)
    strong_events = {
        ConfirmationEventV3.PULLBACK_RECLAIMED,
        ConfirmationEventV3.RESISTANCE_ACCEPTED_ABOVE,
        ConfirmationEventV3.SUPPORT_REJECTED,
        ConfirmationEventV3.BREAKOUT_CONFIRMATION,
        ConfirmationEventV3.BREAKDOWN_CONFIRMATION,
        ConfirmationEventV3.STRONG_FLOW_CONFIRMATION,
    }
    if any(event in strong_events for event in events):
        score = max(score, 0.74)
    return max(0.0, min(1.0, score))


def evaluate_mid_range_decision_discipline_v3(snapshot: Mapping[str, object]) -> dict[str, object]:
    location = derive_market_location_v3(snapshot)
    events = derive_confirmation_events_v3(snapshot)
    required = max(0.0, min(1.0, _float(_get_value(snapshot, "mid_range_required_confirmation"), 0.72)))
    score = _confirmation_score(snapshot, events)
    if location != MarketLocationV3.MID_RANGE:
        return {
            "market_location": location.value,
            "discipline": MidRangeDecisionDisciplineV3.NOT_APPLICABLE.value,
            "blocked": False,
            "required_confirmation_score": required,
            "confirmation_score": score,
            "blocker": "",
            "reason": "Market is not in the middle of the range.",
        }
    if score >= required:
        return {
            "market_location": location.value,
            "discipline": MidRangeDecisionDisciplineV3.CONFIRMED.value,
            "blocked": False,
            "required_confirmation_score": required,
            "confirmation_score": score,
            "blocker": "",
            "reason": "Mid-range location has strong enough confirmation to avoid a weak-edge entry.",
        }
    return {
        "market_location": location.value,
        "discipline": MidRangeDecisionDisciplineV3.BLOCKED_WEAK_CONFIRMATION.value,
        "blocked": True,
        "required_confirmation_score": required,
        "confirmation_score": score,
        "blocker": "MID_RANGE_NEEDS_STRONG_CONFIRMATION",
        "reason": "Middle-of-range entries need stronger confirmation than edge entries.",
    }


def derive_confirmation_events_v3(snapshot: Mapping[str, object]) -> tuple[ConfirmationEventV3, ...]:
    events: list[ConfirmationEventV3] = []
    phase = derive_pullback_phase_v3(snapshot)
    if phase == PullbackPhaseV3.PULLBACK_HELD:
        events.append(ConfirmationEventV3.PULLBACK_HELD)
    elif phase == PullbackPhaseV3.PULLBACK_RECLAIMED:
        events.append(ConfirmationEventV3.PULLBACK_RECLAIMED)
    if _any_true(snapshot, "retest_held", "retest_confirmed"):
        events.append(ConfirmationEventV3.RETEST_HELD)

    interaction = evaluate_interaction_state_v3(snapshot)
    interaction_event = _upper(interaction.get("confirmation_event"))
    for event in ConfirmationEventV3:
        if event.value == interaction_event and event != ConfirmationEventV3.NONE:
            events.append(event)
            break

    if _any_true(snapshot, "breakout_confirmation", "breakout_confirmed", "bos_confirmed", "bms_confirmed"):
        events.append(ConfirmationEventV3.BREAKOUT_CONFIRMATION)
    if _any_true(snapshot, "breakdown_confirmation", "breakdown_confirmed"):
        events.append(ConfirmationEventV3.BREAKDOWN_CONFIRMATION)
    if _any_true(snapshot, "strong_confirmation", "current_flow_continuation_ready", "impulse_confirmation"):
        events.append(ConfirmationEventV3.STRONG_FLOW_CONFIRMATION)
    if _any_true(snapshot, "current_candle_accepted", "current_candle_entry_allowed", "current_candle_ok"):
        events.append(ConfirmationEventV3.CURRENT_CANDLE_ACCEPTED)

    deduped: list[ConfirmationEventV3] = []
    for event in events:
        if event not in deduped:
            deduped.append(event)
    return tuple(deduped) if deduped else (ConfirmationEventV3.NONE,)


def _taxonomy_for_blocker(code: str, field: str, reason: str, *, soft: bool) -> BlockerTaxonomyV3:
    if soft:
        return BlockerTaxonomyV3.SOFT_WARNING
    combined = f"{code} {field} {reason}".upper()
    if any(token in combined for token in ("RUNTIME", "INSTRUMENT_CONTEXT", "BROKER", "FRESHNESS")):
        return BlockerTaxonomyV3.HARD_RUNTIME_FAILURE
    if any(token in combined for token in ("PACKET", "EXPIRED", "SCHEMA", "LANGUAGE")):
        return BlockerTaxonomyV3.HARD_PACKET_FAILURE
    if "PERMISSION" in combined or "LICENSE" in combined:
        return BlockerTaxonomyV3.HARD_PERMISSION_FAILURE
    if any(token in combined for token in ("TIMING", "PULLBACK", "RETEST", "CURRENT_CANDLE", "LATE_CHASE")):
        return BlockerTaxonomyV3.HARD_TIMING_FAILURE
    if any(token in combined for token in ("STRUCTURE", "SUPPORT", "RESISTANCE", "TRAP", "INVALIDATED", "OPPOSING_FORCE")):
        return BlockerTaxonomyV3.HARD_STRUCTURE_FAILURE
    if any(token in combined for token in ("CONFIRM", "SCORE", "MID_RANGE", "LANE")):
        return BlockerTaxonomyV3.HARD_CONFIRMATION_FAILURE
    return BlockerTaxonomyV3.HARD_DECISION_FAILURE


def _classify_blocker_record(
    blocker: Mapping[str, object],
    *,
    force_hard: bool | None = None,
    force_soft: bool = False,
) -> ClassifiedBlockerV3:
    field = _upper(blocker.get("field") or blocker.get("layer") or "blocker")
    code = _upper(blocker.get("code") or blocker.get("blocker") or blocker.get("true_blocker") or field)
    reason = _text(blocker.get("reason") or blocker.get("message") or blocker.get("effect"))
    severity = _upper(blocker.get("severity") or blocker.get("kind") or blocker.get("effect"))
    hard_flag_present = "hard" in blocker
    explicit_hard = _bool(blocker.get("hard")) if "hard" in blocker else False
    explicit_soft = force_soft or severity in {"WARNING", "WARN", "SOFT", "NOTICE", "OBSERVE"}
    hard = force_hard if force_hard is not None else bool((explicit_hard or not hard_flag_present) and not explicit_soft)
    taxonomy = _taxonomy_for_blocker(code, field, reason, soft=not hard)
    if taxonomy != BlockerTaxonomyV3.SOFT_WARNING and not explicit_soft:
        hard = True
    if not code:
        code = field or "BLOCKER"
    return ClassifiedBlockerV3(code=code, field=field or "BLOCKER", reason=reason, taxonomy=taxonomy, hard=hard)


def classify_blocker_v3(blocker: Mapping[str, object]) -> dict[str, object]:
    return _classify_blocker_record(blocker).as_dict()


def _blocker_from_object(
    value: object,
    *,
    source: str,
    force_hard: bool | None = None,
    force_soft: bool = False,
) -> ClassifiedBlockerV3 | None:
    if isinstance(value, Mapping):
        row = dict(cast(Mapping[str, object], value))
        row.setdefault("field", source)
        return _classify_blocker_record(row, force_hard=force_hard, force_soft=force_soft)
    code = _upper(value)
    if not code:
        return None
    row = {"field": source, "code": code, "reason": code, "hard": bool(force_hard)}
    return _classify_blocker_record(row, force_hard=force_hard, force_soft=force_soft)


def _dedupe_blockers(blockers: Sequence[ClassifiedBlockerV3]) -> tuple[ClassifiedBlockerV3, ...]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ClassifiedBlockerV3] = []
    for blocker in blockers:
        key = (blocker.code, blocker.field, blocker.taxonomy.value)
        if key not in seen:
            seen.add(key)
            deduped.append(blocker)
    return tuple(deduped)


def _runtime_failure_blocker(snapshot: Mapping[str, object]) -> ClassifiedBlockerV3 | None:
    runtime_pass = _get_value(snapshot, "runtime_pass", "runtime_ok", "hard_runtime_pass", "runtime_integrity_pass")
    if runtime_pass is not None and not _bool(runtime_pass):
        return ClassifiedBlockerV3(
            code="RUNTIME_INTEGRITY_FAILED",
            field="RUNTIME",
            reason="Hard runtime integrity is not clear.",
            taxonomy=BlockerTaxonomyV3.HARD_RUNTIME_FAILURE,
            hard=True,
        )
    runtime_status = _upper(_get_value(snapshot, "runtime_status", "broker_status", "execution_state", "final_state"))
    if runtime_status in {"BLOCKED_BY_RUNTIME", "RUNTIME_FAILED", "RUNTIME_BLOCKED", "BROKER_BLOCKED"}:
        return ClassifiedBlockerV3(
            code=runtime_status,
            field="RUNTIME",
            reason="Runtime state blocks authorization.",
            taxonomy=BlockerTaxonomyV3.HARD_RUNTIME_FAILURE,
            hard=True,
        )
    return None


def _classified_blockers(snapshot: Mapping[str, object]) -> tuple[ClassifiedBlockerV3, ...]:
    records: list[ClassifiedBlockerV3] = []
    for value in _sequence(_get_value(snapshot, "hard_blockers")):
        record = _blocker_from_object(value, source="hard_blockers", force_hard=True)
        if record is not None:
            records.append(record)
    for value in _sequence(_get_value(snapshot, "blockers")):
        record = _blocker_from_object(value, source="blockers", force_hard=None)
        if record is not None:
            records.append(record)
    for value in _sequence(_get_value(snapshot, "soft_warnings", "warnings")):
        record = _blocker_from_object(value, source="soft_warnings", force_hard=False, force_soft=True)
        if record is not None:
            records.append(record)
    runtime_blocker = _runtime_failure_blocker(snapshot)
    if runtime_blocker is not None:
        records.append(runtime_blocker)
    return _dedupe_blockers(records)


def classify_blockers_v3(snapshot: Mapping[str, object]) -> dict[str, object]:
    records = _classified_blockers(snapshot)
    hard_blockers = tuple(row for row in records if row.hard)
    soft_warnings = tuple(row for row in records if not row.hard)
    return {
        "hard_blockers": [row.as_dict() for row in hard_blockers],
        "soft_warnings": [row.as_dict() for row in soft_warnings],
        "blocker_codes": [row.code for row in hard_blockers],
        "soft_warning_codes": [row.code for row in soft_warnings],
    }


def _virtual_blocker(code: str, field: str, reason: str, taxonomy: BlockerTaxonomyV3) -> ClassifiedBlockerV3:
    return ClassifiedBlockerV3(code=code, field=field, reason=reason, taxonomy=taxonomy, hard=True)


def _layer_for_blocker(blocker: ClassifiedBlockerV3) -> str:
    if blocker.taxonomy in {BlockerTaxonomyV3.HARD_RUNTIME_FAILURE, BlockerTaxonomyV3.HARD_PACKET_FAILURE}:
        return "RUNTIME_INTEGRITY"
    if blocker.taxonomy == BlockerTaxonomyV3.HARD_PERMISSION_FAILURE:
        return "TRADE_PERMISSION"
    if blocker.code == "MID_RANGE_NEEDS_STRONG_CONFIRMATION":
        return "MID_RANGE_DECISION_DISCIPLINE"
    if blocker.taxonomy == BlockerTaxonomyV3.HARD_TIMING_FAILURE:
        return "TIMING_MODE"
    if blocker.taxonomy == BlockerTaxonomyV3.HARD_STRUCTURE_FAILURE:
        return "STRUCTURE_VALIDATION"
    if blocker.taxonomy == BlockerTaxonomyV3.HARD_CONFIRMATION_FAILURE:
        return "CONFIRMATION"
    return "DECISION_DISCIPLINE"


def _compute_authorization(snapshot: Mapping[str, object]) -> _AuthorizationDecisionV3:
    requested = _requested_state(snapshot)
    phase = derive_pullback_phase_v3(snapshot)
    records = _classified_blockers(snapshot)
    hard_blockers = list(row for row in records if row.hard)
    soft_warnings = tuple(row for row in records if not row.hard)
    mid_range = evaluate_mid_range_decision_discipline_v3(snapshot)
    side = _normalize_side(_get_value(snapshot, "candidate_side", "side", "action", "execution_action"))

    if _bool(mid_range.get("blocked")):
        hard_blockers.append(
            _virtual_blocker(
                "MID_RANGE_NEEDS_STRONG_CONFIRMATION",
                "MARKET_LOCATION",
                _text(mid_range.get("reason")),
                BlockerTaxonomyV3.HARD_CONFIRMATION_FAILURE,
            )
        )
    if phase == PullbackPhaseV3.PULLBACK_FAILED:
        hard_blockers.append(
            _virtual_blocker(
                "PULLBACK_FAILED",
                "PULLBACK_PHASE",
                "Pullback failed or invalidated the candidate.",
                BlockerTaxonomyV3.HARD_TIMING_FAILURE,
            )
        )
    elif phase == PullbackPhaseV3.WAITING_FOR_PULLBACK and _entry_now_intent(snapshot, phase):
        hard_blockers.append(
            _virtual_blocker(
                "WAIT_FOR_PULLBACK",
                "PULLBACK_PHASE",
                "Pullback is still pending.",
                BlockerTaxonomyV3.HARD_TIMING_FAILURE,
            )
        )
    if _has_explicit_false(snapshot, "current_candle_accepted", "current_candle_entry_allowed", "current_candle_ok"):
        hard_blockers.append(
            _virtual_blocker(
                "CURRENT_CANDLE_NOT_ACCEPTED",
                "CURRENT_CANDLE",
                "Current candle is not accepted for entry.",
                BlockerTaxonomyV3.HARD_CONFIRMATION_FAILURE,
            )
        )

    hard_tuple = _dedupe_blockers(hard_blockers)
    runtime_blocker = next(
        (
            row
            for row in hard_tuple
            if row.taxonomy in {BlockerTaxonomyV3.HARD_RUNTIME_FAILURE, BlockerTaxonomyV3.HARD_PACKET_FAILURE}
        ),
        None,
    )
    if runtime_blocker is not None:
        return _AuthorizationDecisionV3(
            requested_state=requested,
            final_state=LiveThesisStateV3.BLOCKED_BY_RUNTIME,
            decision_allowed=False,
            downgrade_layer=_layer_for_blocker(runtime_blocker),
            downgrade_reason=runtime_blocker.reason,
            hard_blockers=hard_tuple,
            soft_warnings=soft_warnings,
        )
    if hard_tuple:
        first = hard_tuple[0]
        final_state = LiveThesisStateV3.INVALIDATED if first.code == "PULLBACK_FAILED" else LiveThesisStateV3.PREPARING
        return _AuthorizationDecisionV3(
            requested_state=requested,
            final_state=final_state,
            decision_allowed=False,
            downgrade_layer=_layer_for_blocker(first),
            downgrade_reason=first.reason,
            hard_blockers=hard_tuple,
            soft_warnings=soft_warnings,
        )
    if side not in VALID_DECISION_SIDES:
        return _AuthorizationDecisionV3(
            requested_state=requested,
            final_state=LiveThesisStateV3.WATCHING,
            decision_allowed=False,
            downgrade_layer="CANDIDATE_SIDE",
            downgrade_reason="No BUY or SELL candidate side was supplied.",
            hard_blockers=hard_tuple,
            soft_warnings=soft_warnings,
        )
    if _entry_now_intent(snapshot, phase):
        return _AuthorizationDecisionV3(
            requested_state=requested,
            final_state=LiveThesisStateV3.ENTER_NOW,
            decision_allowed=True,
            downgrade_layer="NONE",
            downgrade_reason="",
            hard_blockers=hard_tuple,
            soft_warnings=soft_warnings,
        )
    return _AuthorizationDecisionV3(
        requested_state=requested,
        final_state=requested if requested != LiveThesisStateV3.ENTER_NOW else LiveThesisStateV3.PREPARING,
        decision_allowed=False,
        downgrade_layer="TIMING_MODE",
        downgrade_reason="Candidate has not reached ENTER_NOW timing.",
        hard_blockers=hard_tuple,
        soft_warnings=soft_warnings,
    )


def derive_live_thesis_state_v3(snapshot: Mapping[str, object]) -> LiveThesisStateV3:
    return _compute_authorization(snapshot).final_state


def build_authorization_survival_trace_v3(snapshot: Mapping[str, object]) -> dict[str, object]:
    decision = _compute_authorization(snapshot)
    runtime_step = (
        "hard_runtime_gate:fail"
        if decision.final_state == LiveThesisStateV3.BLOCKED_BY_RUNTIME
        else "hard_runtime_gate:pass"
    )
    trace_steps = [
        f"requested:{decision.requested_state.value}",
        runtime_step,
        "soft_warnings:preserved" if decision.soft_warnings else "soft_warnings:none",
    ]
    if decision.downgrade_layer != "NONE":
        trace_steps.append(f"downgrade:{decision.downgrade_layer}")
    trace_steps.append(f"final:{decision.final_state.value}")
    trace = AuthorizationSurvivalTraceV3(
        requested_state=decision.requested_state,
        final_state=decision.final_state,
        survived_enter_now=decision.decision_allowed and decision.final_state == LiveThesisStateV3.ENTER_NOW,
        downgrade_layer=decision.downgrade_layer,
        downgrade_reason=decision.downgrade_reason,
        hard_blockers=tuple(row.code for row in decision.hard_blockers),
        soft_warnings=tuple(row.code for row in decision.soft_warnings),
        trace_steps=tuple(trace_steps),
    )
    return trace.as_dict()


def build_candidate_decision_ledger_v3(snapshot: Mapping[str, object]) -> dict[str, object]:
    decision = _compute_authorization(snapshot)
    phase = derive_pullback_phase_v3(snapshot)
    interaction = evaluate_interaction_state_v3(snapshot)
    interaction_state = InteractionStateV3(_text(interaction.get("interaction_state"), InteractionStateV3.NONE.value))
    market_location = derive_market_location_v3(snapshot)
    mid_range = evaluate_mid_range_decision_discipline_v3(snapshot)
    mid_range_discipline = MidRangeDecisionDisciplineV3(
        _text(mid_range.get("discipline"), MidRangeDecisionDisciplineV3.NOT_APPLICABLE.value)
    )
    confirmation_events = derive_confirmation_events_v3(snapshot)
    candidate_side = _normalize_side(_get_value(snapshot, "candidate_side", "side", "action", "execution_action"))
    evidence_side = _normalize_side(interaction.get("evidence_side")) or candidate_side
    trace_dict = build_authorization_survival_trace_v3(snapshot)
    trace = AuthorizationSurvivalTraceV3(
        requested_state=LiveThesisStateV3(_text(trace_dict.get("requested_state"), LiveThesisStateV3.PREPARING.value)),
        final_state=LiveThesisStateV3(_text(trace_dict.get("final_state"), decision.final_state.value)),
        survived_enter_now=_bool(trace_dict.get("survived_enter_now")),
        downgrade_layer=_text(trace_dict.get("downgrade_layer"), "NONE"),
        downgrade_reason=_text(trace_dict.get("downgrade_reason")),
        hard_blockers=tuple(_text(value) for value in _sequence(trace_dict.get("hard_blockers")) if _text(value)),
        soft_warnings=tuple(_text(value) for value in _sequence(trace_dict.get("soft_warnings")) if _text(value)),
        trace_steps=tuple(_text(value) for value in _sequence(trace_dict.get("trace_steps")) if _text(value)),
    )
    ledger = CandidateDecisionLedgerV3(
        candidate_side=candidate_side,
        evidence_side=evidence_side,
        final_state=decision.final_state,
        decision_allowed=decision.decision_allowed,
        pullback_phase=phase,
        interaction_state=interaction_state,
        market_location=market_location,
        mid_range_discipline=mid_range_discipline,
        confirmation_events=confirmation_events,
        hard_blockers=decision.hard_blockers,
        soft_warnings=decision.soft_warnings,
        authorization_trace=trace,
    )
    return ledger.as_dict()


__all__ = [
    "AuthorizationSurvivalTraceV3",
    "BlockerTaxonomyV3",
    "CandidateDecisionLedgerV3",
    "ConfirmationEventV3",
    "InteractionStateV3",
    "LiveThesisStateV3",
    "MarketLocationV3",
    "MidRangeDecisionDisciplineV3",
    "PullbackPhaseV3",
    "build_authorization_survival_trace_v3",
    "build_candidate_decision_ledger_v3",
    "classify_blocker_v3",
    "classify_blockers_v3",
    "derive_confirmation_events_v3",
    "derive_live_thesis_state_v3",
    "derive_market_location_v3",
    "derive_pullback_phase_v3",
    "evaluate_interaction_state_v3",
    "evaluate_mid_range_decision_discipline_v3",
    "evaluate_support_resistance_interaction_v3",
]
