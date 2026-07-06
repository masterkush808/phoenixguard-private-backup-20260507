from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import cast

from phoenixguard.decision.book_strategy.blocker_taxonomy import (
    BlockerClass,
    BlockerSplit,
    classify_blocker_code,
    split_blockers,
)


BOOK_STRATEGY_CONTRACT_SCHEMA_VERSION = "PG_BOOK_STRATEGY_CONTRACTS_V1"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MaturityState(str, Enum):
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    EARLY_FORMING = "EARLY_FORMING"
    VALID_WATCH = "VALID_WATCH"
    PREPARE = "PREPARE"
    ENTER_NOW = "ENTER_NOW"
    LATE_CHASE = "LATE_CHASE"
    INVALIDATED = "INVALIDATED"
    MISSED = "MISSED"


_MATURITY_ALIASES: Mapping[str, MaturityState] = {
    "WATCHING": MaturityState.VALID_WATCH,
    "WATCH": MaturityState.VALID_WATCH,
    "PREPARING": MaturityState.PREPARE,
    "READY": MaturityState.PREPARE,
    "EXECUTABLE": MaturityState.ENTER_NOW,
    "EXECUTE": MaturityState.ENTER_NOW,
    "ENTRY_NOW": MaturityState.ENTER_NOW,
    "BLOCKED": MaturityState.PREPARE,
    "BLOCKED_BY_RUNTIME": MaturityState.PREPARE,
    "CONFLICT": MaturityState.INVALIDATED,
    "SKIP_LATE_ENTRY": MaturityState.LATE_CHASE,
    "NO_TRADE": MaturityState.NO_OPPORTUNITY,
    "HOLD": MaturityState.NO_OPPORTUNITY,
}


def _empty_mapping() -> dict[str, object]:
    return {}


@dataclass(frozen=True, slots=True)
class StrategyBlocker:
    code: str
    field: str
    reason: str
    hard: bool = False
    blocker_class: BlockerClass = BlockerClass.DIAGNOSTIC_ONLY
    received: object | None = None
    required: object | None = None

    @property
    def can_block_package(self) -> bool:
        return self.blocker_class is BlockerClass.TRUE_HARD_BLOCKER

    def to_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "field": self.field,
            "reason": self.reason,
            "hard": self.hard,
            "class": self.blocker_class.value,
            "can_block_package": self.can_block_package,
            "received": self.received,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class BookStrategyEvidence:
    playbook: str = ""
    entry_profile: str = ""
    strategy_combo: tuple[str, ...] = field(default_factory=tuple)
    market_phase: str = ""
    reaction_type: str = ""
    raw: Mapping[str, object] = field(default_factory=_empty_mapping)

    def to_payload(self) -> dict[str, object]:
        return {
            "playbook": self.playbook,
            "entry_profile": self.entry_profile,
            "strategy_combo": list(self.strategy_combo),
            "market_phase": self.market_phase,
            "reaction_type": self.reaction_type,
            "raw": dict(self.raw),
        }

    @classmethod
    def from_payload(cls, value: object) -> BookStrategyEvidence:
        payload = _string_key_mapping(value)
        return cls(
            playbook=_string_value(payload.get("playbook")),
            entry_profile=_string_value(payload.get("entry_profile")),
            strategy_combo=_string_tuple(payload.get("strategy_combo")),
            market_phase=_string_value(payload.get("market_phase_v3") or payload.get("market_phase")),
            reaction_type=_string_value(payload.get("reaction_type")),
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class BookStrategyDecision:
    maturity: MaturityState
    side: Side
    playbook: str
    confidence: float
    next_required: str
    blockers: tuple[StrategyBlocker, ...] = field(default_factory=tuple)
    hard_blockers: tuple[StrategyBlocker, ...] = field(default_factory=tuple)
    evidence: BookStrategyEvidence = field(default_factory=BookStrategyEvidence)
    schema_version: str = BOOK_STRATEGY_CONTRACT_SCHEMA_VERSION
    headline: str = ""
    playbook_signal: Side = Side.HOLD

    @property
    def blocked(self) -> bool:
        return any(blocker.can_block_package for blocker in (*self.hard_blockers, *self.blockers))

    @property
    def blocker_split(self) -> BlockerSplit:
        return split_blockers(self.blockers)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "maturity": self.maturity.value,
            "maturity_state": self.maturity.value,
            "side": self.side.value,
            "playbook_signal": self.playbook_signal.value,
            "playbook": self.playbook,
            "headline": self.headline,
            "confidence": self.confidence,
            "next_required": self.next_required,
            "hard_blockers": [blocker.to_payload() for blocker in self.hard_blockers],
            "blockers": [blocker.to_payload() for blocker in self.blockers],
            "evidence": self.evidence.to_payload(),
        }

    @classmethod
    def from_payload(cls, value: object) -> BookStrategyDecision:
        payload = _string_key_mapping(value)
        blockers = blockers_from_payload(payload.get("blockers"))
        hard_blockers_payload = payload.get("hard_blockers")
        hard_blockers = blockers_from_payload(hard_blockers_payload)
        if not hard_blockers:
            hard_blockers = tuple(blocker for blocker in blockers if blocker.can_block_package)
        evidence = BookStrategyEvidence.from_payload(payload.get("evidence"))
        return cls(
            maturity=normalize_maturity(payload.get("maturity_state") or payload.get("maturity")),
            side=normalize_side(payload.get("side")),
            playbook=_string_value(payload.get("playbook")),
            confidence=_float_value(payload.get("confidence")),
            next_required=_string_value(payload.get("next_required")),
            blockers=blockers,
            hard_blockers=hard_blockers,
            evidence=evidence,
            schema_version=_string_value(payload.get("schema_version"), BOOK_STRATEGY_CONTRACT_SCHEMA_VERSION),
            headline=_string_value(payload.get("headline")),
            playbook_signal=normalize_side(payload.get("playbook_signal")),
        )


def normalize_side(value: object) -> Side:
    if isinstance(value, Side):
        return value
    token = _token(value)
    if token in {"BUY", "BULL", "BULLISH", "UP", "UPTREND", "CALL", "DEMAND", "LONG"}:
        return Side.BUY
    if token in {"SELL", "BEAR", "BEARISH", "DOWN", "DOWNTREND", "PUT", "SUPPLY", "SHORT"}:
        return Side.SELL
    return Side.HOLD


def normalize_maturity(value: object) -> MaturityState:
    if isinstance(value, MaturityState):
        return value
    token = _token(value)
    if token in MaturityState.__members__:
        return MaturityState[token]
    for state in MaturityState:
        if token == state.value:
            return state
    return _MATURITY_ALIASES.get(token, MaturityState.NO_OPPORTUNITY)


def blocker_from_payload(value: object) -> StrategyBlocker:
    if isinstance(value, StrategyBlocker):
        return value
    if isinstance(value, str):
        blocker_class = classify_blocker_code(value)
        return StrategyBlocker(
            code=_code_value(value),
            field=_code_value(value).lower(),
            reason="",
            blocker_class=blocker_class,
        )
    payload = _string_key_mapping(value)
    field = _string_value(payload.get("field") or payload.get("code")).strip()
    code = _code_value(payload.get("code") or field)
    hard = _bool_value(payload.get("hard"))
    blocker_class = _blocker_class_from_payload(payload.get("class") or payload.get("blocker_class"), code, hard)
    return StrategyBlocker(
        code=code,
        field=field,
        reason=_string_value(payload.get("reason") or payload.get("message")),
        hard=hard,
        blocker_class=blocker_class,
        received=payload.get("received"),
        required=payload.get("required"),
    )


def blockers_from_payload(value: object) -> tuple[StrategyBlocker, ...]:
    if isinstance(value, StrategyBlocker):
        return (value,)
    if isinstance(value, str):
        return (blocker_from_payload(value),)
    if not isinstance(value, Sequence):
        return ()
    if isinstance(value, (bytes, bytearray)):
        return ()
    rows = cast(Sequence[object], value)
    return tuple(blocker_from_payload(row) for row in rows)


def evidence_from_payload(value: object) -> BookStrategyEvidence:
    return BookStrategyEvidence.from_payload(value)


def decision_from_payload(value: object) -> BookStrategyDecision:
    return BookStrategyDecision.from_payload(value)


def _blocker_class_from_payload(value: object, code: str, hard: bool) -> BlockerClass:
    token = _token(value)
    if token in BlockerClass.__members__:
        return BlockerClass[token]
    for blocker_class in BlockerClass:
        if token == blocker_class.value:
            return blocker_class
    return classify_blocker_code(code, hard=hard)


def _string_key_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    raw_mapping = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw_mapping.items() if isinstance(key, str)}


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Sequence):
        return ()
    if isinstance(value, (bytes, bytearray)):
        return ()
    items = cast(Sequence[object], value)
    return tuple(_string_value(item) for item in items if _string_value(item))


def _token(value: object) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _code_value(value: object) -> str:
    return _token(value).replace(".", "_")


def _string_value(value: object, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _float_value(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(1.0, float(value.strip())))
        except ValueError:
            return default
    return default


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    token = _token(value)
    return token in {"1", "TRUE", "YES", "Y", "ON", "HARD", "BLOCKED", "FAIL", "FAILED"}
