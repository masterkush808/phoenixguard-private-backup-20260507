from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from phoenixguard.decision.book_strategy.blocker_taxonomy import (
    BlockerClass,
    classify_blocker_code,
)


BLOCKER_TAXONOMY_SCHEMA_VERSION = "PG_BLOCKER_TAXONOMY_V3"
BlockerInputV3: TypeAlias = "Mapping[str, Any] | str"


@dataclass(frozen=True, slots=True)
class ClassifiedBlockerV3:
    code: str
    blocker_class: BlockerClass
    source: str = "model_council"
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def hard(self) -> bool:
        return self.blocker_class is BlockerClass.TRUE_HARD_BLOCKER

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "blocker_class": self.blocker_class.value,
            "hard": self.hard,
            "source": self.source,
            "reason": self.reason,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def normalize_blocker_code_v3(value: Any) -> str:
    text = str(value or "").strip().upper()
    normalized = text.replace(".", "_").replace("-", "_").replace(" ", "_")
    return normalized or "UNSPECIFIED_BLOCKER"


def classify_blocker_v3(
    blocker: BlockerInputV3,
    *,
    default_source: str = "model_council",
) -> ClassifiedBlockerV3:
    payload = dict(blocker) if isinstance(blocker, Mapping) else {"code": blocker}
    code = normalize_blocker_code_v3(
        payload.get("code")
        or payload.get("blocker_code")
        or payload.get("field")
        or payload.get("name")
        or payload.get("denied_at")
        or blocker
    )
    explicit_class = _blocker_class(payload.get("blocker_class"))
    explicit_hard = _truthy(payload.get("hard"))
    blocker_class = (
        BlockerClass.TRUE_HARD_BLOCKER
        if explicit_hard
        else explicit_class
        or classify_blocker_code(code)
    )
    source = str(payload.get("source") or default_source or "model_council").strip()
    reason = str(
        payload.get("reason")
        or payload.get("message")
        or payload.get("effect")
        or ""
    ).strip()
    details = {
        key: payload[key]
        for key in ("field", "received", "required", "name", "value", "effect")
        if key in payload
    }
    return ClassifiedBlockerV3(
        code=code,
        blocker_class=blocker_class,
        source=source,
        reason=reason,
        details=details,
    )


def classify_blockers_v3(
    blockers: Iterable[BlockerInputV3],
    *,
    default_source: str = "model_council",
) -> tuple[ClassifiedBlockerV3, ...]:
    return tuple(
        classify_blocker_v3(blocker, default_source=default_source)
        for blocker in blockers
    )


def partition_blockers_v3(
    blockers: Iterable[BlockerInputV3],
    *,
    default_source: str = "model_council",
) -> dict[str, list[dict[str, Any]]]:
    partition = {member.value: [] for member in BlockerClass}
    for blocker in classify_blockers_v3(blockers, default_source=default_source):
        partition[blocker.blocker_class.value].append(blocker.as_dict())
    return partition


def _blocker_class(value: Any) -> BlockerClass | None:
    if isinstance(value, BlockerClass):
        return value
    token = normalize_blocker_code_v3(value)
    if token == "UNSPECIFIED_BLOCKER":
        return None
    try:
        return BlockerClass(token)
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "hard"}
