from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from phoenixguard.decision.book_strategy.contracts import StrategyBlocker


class BlockerClass(str, Enum):
    TRUE_HARD_BLOCKER = "TRUE_HARD_BLOCKER"
    SOFT_WARNING = "SOFT_WARNING"
    WAIT_STATE = "WAIT_STATE"
    STRATEGY_CAUTION = "STRATEGY_CAUTION"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"


_TRUE_HARD_TOKENS: tuple[str, ...] = (
    "API_UNHEALTHY",
    "CACHE_STALE",
    "CHART_TRANSFORM",
    "DUPLICATE_PROCESS",
    "EXPLICIT_INVALIDATION",
    "FRAME_NOT_ADVANCING",
    "FRESHNESS",
    "INSTRUMENT_CONTEXT",
    "INVALIDATED",
    "LIVE_INTEGRITY",
    "MISSING_CHART_TRANSFORM",
    "MISSING_SEQUENCE_CONTEXT",
    "MODEL_HEALTH",
    "MODELS_NOT_AWAKE",
    "NO_PATH_ROOM",
    "PACKET_SCHEMA",
    "RUNTIME",
    "SEQUENCE_CONTEXT",
    "SOURCE_LOCK",
    "STALE",
    "SYMBOL_TIMEFRAME_MISMATCH",
    "WRONG_SOURCE",
    "WRONG_SURFACE",
)
_WAIT_TOKENS: tuple[str, ...] = (
    "CONFIRM",
    "PULLBACK",
    "RETEST",
    "TIMING_MODE",
    "WAIT",
    "WATCH",
)
_STRATEGY_CAUTION_TOKENS: tuple[str, ...] = (
    "BAD_ENTRY",
    "CURRENT_CANDLE",
    "FALSE_BREAKOUT",
    "LATE_CHASE",
    "OPPOSING_FORCE",
    "PROFESSIONAL_GRADE",
    "REPLAY_WAVE_TEMPLATE",
    "RISK",
    "TARGET_BEFORE_INVALIDATION",
)
_DIAGNOSTIC_TOKENS: tuple[str, ...] = (
    "DIAGNOSTIC",
    "LSTM",
    "MEMORY",
    "MODEL_COUNCIL_CONTRIBUTOR",
    "REGRESSION",
    "SCENARIO",
    "SKILL",
    "STUDY",
    "TWO_CANDLE",
)


@dataclass(frozen=True, slots=True)
class BlockerSplit:
    hard: tuple[StrategyBlocker, ...]
    soft: tuple[StrategyBlocker, ...]
    wait: tuple[StrategyBlocker, ...]
    caution: tuple[StrategyBlocker, ...]
    diagnostic: tuple[StrategyBlocker, ...]

    @property
    def all(self) -> tuple[StrategyBlocker, ...]:
        return (*self.hard, *self.soft, *self.wait, *self.caution, *self.diagnostic)

    @property
    def can_block_package(self) -> bool:
        return bool(self.hard)


BlockerInput: TypeAlias = "StrategyBlocker | Mapping[str, object] | str"


def classify_blocker_code(code: object, *, hard: bool | None = None) -> BlockerClass:
    token = _normalize_code(code)
    if hard is True:
        return BlockerClass.TRUE_HARD_BLOCKER
    if not token:
        return BlockerClass.DIAGNOSTIC_ONLY
    if _contains_any(token, _TRUE_HARD_TOKENS):
        return BlockerClass.TRUE_HARD_BLOCKER
    if _contains_any(token, _DIAGNOSTIC_TOKENS):
        return BlockerClass.DIAGNOSTIC_ONLY
    if _contains_any(token, _WAIT_TOKENS):
        return BlockerClass.WAIT_STATE
    if _contains_any(token, _STRATEGY_CAUTION_TOKENS):
        return BlockerClass.STRATEGY_CAUTION
    return BlockerClass.SOFT_WARNING


def split_blockers(blockers: Iterable[BlockerInput]) -> BlockerSplit:
    from phoenixguard.decision.book_strategy.contracts import blocker_from_payload

    hard_blockers: list[StrategyBlocker] = []
    soft_warnings: list[StrategyBlocker] = []
    wait_states: list[StrategyBlocker] = []
    cautions: list[StrategyBlocker] = []
    diagnostics: list[StrategyBlocker] = []
    for blocker_input in blockers:
        blocker = blocker_from_payload(blocker_input)
        if blocker.blocker_class is BlockerClass.TRUE_HARD_BLOCKER:
            hard_blockers.append(blocker)
        elif blocker.blocker_class is BlockerClass.WAIT_STATE:
            wait_states.append(blocker)
        elif blocker.blocker_class is BlockerClass.STRATEGY_CAUTION:
            cautions.append(blocker)
        elif blocker.blocker_class is BlockerClass.DIAGNOSTIC_ONLY:
            diagnostics.append(blocker)
        else:
            soft_warnings.append(blocker)
    return BlockerSplit(
        hard=tuple(hard_blockers),
        soft=tuple(soft_warnings),
        wait=tuple(wait_states),
        caution=tuple(cautions),
        diagnostic=tuple(diagnostics),
    )


def _contains_any(token: str, needles: tuple[str, ...]) -> bool:
    return any(needle in token for needle in needles)


def _normalize_code(value: object) -> str:
    text = str(value or "").strip().upper()
    return text.replace(".", "_").replace("-", "_").replace(" ", "_")
