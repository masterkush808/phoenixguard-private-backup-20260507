from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

from phoenixguard.decision.blocker_taxonomy_v3 import (
    BlockerClass,
    BlockerInputV3,
    ClassifiedBlockerV3,
    classify_blockers_v3,
)


SHADOW_ALLOWANCE_PACKAGE_SCHEMA_VERSION = "SHADOW_ALLOWANCE_PACKAGE_V1"


class BlockerAblationProfile(str, Enum):
    BASELINE_FULL_SAFETY = "BASELINE_FULL_SAFETY"
    HARD_ONLY_ABLATION = "HARD_ONLY_ABLATION"
    SOFT_BLOCKERS_AS_WARNINGS = "SOFT_BLOCKERS_AS_WARNINGS"
    WAIT_STATES_AS_PREPARE = "WAIT_STATES_AS_PREPARE"
    SHADOW_NO_BLOCKERS = "SHADOW_NO_BLOCKERS"


BlockerAblationProfileV3 = BlockerAblationProfile
DEFAULT_BLOCKER_ABLATION_PROFILE = BlockerAblationProfile.BASELINE_FULL_SAFETY


_BLOCKING_CLASSES: dict[BlockerAblationProfile, frozenset[BlockerClass]] = {
    BlockerAblationProfile.BASELINE_FULL_SAFETY: frozenset(
        {
            BlockerClass.TRUE_HARD_BLOCKER,
            BlockerClass.SOFT_WARNING,
            BlockerClass.WAIT_STATE,
            BlockerClass.STRATEGY_CAUTION,
        }
    ),
    BlockerAblationProfile.HARD_ONLY_ABLATION: frozenset(
        {BlockerClass.TRUE_HARD_BLOCKER}
    ),
    BlockerAblationProfile.SOFT_BLOCKERS_AS_WARNINGS: frozenset(
        {
            BlockerClass.TRUE_HARD_BLOCKER,
            BlockerClass.WAIT_STATE,
            BlockerClass.STRATEGY_CAUTION,
        }
    ),
    BlockerAblationProfile.WAIT_STATES_AS_PREPARE: frozenset(
        {
            BlockerClass.TRUE_HARD_BLOCKER,
            BlockerClass.SOFT_WARNING,
            BlockerClass.STRATEGY_CAUTION,
        }
    ),
    BlockerAblationProfile.SHADOW_NO_BLOCKERS: frozenset(),
}


@dataclass(frozen=True, slots=True)
class BlockerAblationDecisionV3:
    profile: BlockerAblationProfile
    shadow_state: str
    shadow_would_allow: bool
    prepare_allowed: bool
    blocked_by_baseline: tuple[ClassifiedBlockerV3, ...]
    blockers_retained_for_test: tuple[ClassifiedBlockerV3, ...]
    warnings_ignored_for_test: tuple[ClassifiedBlockerV3, ...]
    wait_states_reclassified_as_prepare: tuple[ClassifiedBlockerV3, ...]
    hard_blockers_observed: tuple[ClassifiedBlockerV3, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ablation_profile": self.profile.value,
            "shadow_state": self.shadow_state,
            "shadow_would_allow": self.shadow_would_allow,
            "prepare_allowed": self.prepare_allowed,
            "blocked_by_baseline": [row.as_dict() for row in self.blocked_by_baseline],
            "blockers_retained_for_test": [
                row.as_dict() for row in self.blockers_retained_for_test
            ],
            "warnings_ignored_for_test": [
                row.as_dict() for row in self.warnings_ignored_for_test
            ],
            "wait_states_reclassified_as_prepare": [
                row.as_dict() for row in self.wait_states_reclassified_as_prepare
            ],
            "hard_blockers_observed": [
                row.as_dict() for row in self.hard_blockers_observed
            ],
        }


def resolve_blocker_ablation_profile_v3(
    value: BlockerAblationProfile | Mapping[str, Any] | str | None,
) -> BlockerAblationProfile:
    if isinstance(value, BlockerAblationProfile):
        return value
    raw: Any = value
    if isinstance(value, Mapping):
        controls = value.get("execution_controls")
        control_mapping: Mapping[str, Any] = (
            cast("Mapping[str, Any]", controls) if isinstance(controls, Mapping) else {}
        )
        raw = (
            value.get("blocker_ablation_profile")
            or value.get("ablation_profile")
            or control_mapping.get("blocker_ablation_profile")
            or control_mapping.get("ablation_profile")
        )
    token = str(raw or DEFAULT_BLOCKER_ABLATION_PROFILE.value).strip().upper()
    try:
        return BlockerAblationProfile(token)
    except ValueError:
        return DEFAULT_BLOCKER_ABLATION_PROFILE


def evaluate_blocker_ablation_profile_v3(
    blockers: Iterable[BlockerInputV3],
    *,
    warnings: Iterable[BlockerInputV3] = (),
    profile: BlockerAblationProfile | str = DEFAULT_BLOCKER_ABLATION_PROFILE,
    side: Any = None,
) -> BlockerAblationDecisionV3:
    resolved_profile = resolve_blocker_ablation_profile_v3(profile)
    baseline_blockers = _dedupe(classify_blockers_v3(blockers))
    baseline_warnings = _dedupe(classify_blockers_v3(warnings))
    blocker_keys = {(row.code, row.source) for row in baseline_blockers}
    baseline_warnings = tuple(
        row for row in baseline_warnings if (row.code, row.source) not in blocker_keys
    )
    blocking_classes = _BLOCKING_CLASSES[resolved_profile]
    wait_as_prepare = tuple(
        row
        for row in baseline_blockers
        if resolved_profile is BlockerAblationProfile.WAIT_STATES_AS_PREPARE
        and row.blocker_class is BlockerClass.WAIT_STATE
    )
    retained = tuple(
        row
        for row in baseline_blockers
        if row.blocker_class in blocking_classes
    )
    ignored = tuple(
        row
        for row in baseline_blockers
        if row not in retained and row not in wait_as_prepare
    ) + baseline_warnings
    hard_observed = tuple(
        row
        for row in (*baseline_blockers, *baseline_warnings)
        if row.blocker_class is BlockerClass.TRUE_HARD_BLOCKER
    )
    normalized_side = str(side or "").strip().upper()
    side_valid = normalized_side in {"BUY", "SELL"}
    if retained:
        shadow_state = "BLOCKED"
    elif wait_as_prepare:
        shadow_state = "PREPARE"
    elif side_valid:
        shadow_state = "WOULD_ALLOW"
    else:
        shadow_state = "NO_DIRECTION"
    return BlockerAblationDecisionV3(
        profile=resolved_profile,
        shadow_state=shadow_state,
        shadow_would_allow=shadow_state == "WOULD_ALLOW",
        prepare_allowed=shadow_state == "PREPARE",
        blocked_by_baseline=baseline_blockers,
        blockers_retained_for_test=retained,
        warnings_ignored_for_test=_dedupe(ignored),
        wait_states_reclassified_as_prepare=wait_as_prepare,
        hard_blockers_observed=_dedupe(hard_observed),
    )


def build_shadow_allowance_package_v1(
    baseline_allowance_package: Mapping[str, Any],
    *,
    blockers: Iterable[BlockerInputV3] = (),
    warnings: Iterable[BlockerInputV3] = (),
    profile: BlockerAblationProfile | str = DEFAULT_BLOCKER_ABLATION_PROFILE,
) -> dict[str, Any]:
    baseline = dict(baseline_allowance_package)
    baseline_blockers = list(blockers)
    true_blocker = str(baseline.get("true_blocker") or "").strip().upper()
    if true_blocker not in {"", "NONE", "NULL"}:
        baseline_blockers.append(
            {
                "code": true_blocker,
                "reason": baseline.get("next_required") or "baseline allowance blocked",
                "source": "baseline_allowance_package",
            }
        )
    if baseline.get("execution_ready") is not True and not baseline_blockers:
        baseline_blockers.append(
            {
                "code": baseline.get("final_state") or "BASELINE_NOT_EXECUTION_READY",
                "reason": baseline.get("next_required") or "baseline allowance is not execution-ready",
                "source": "baseline_allowance_package",
            }
        )
    decision = evaluate_blocker_ablation_profile_v3(
        baseline_blockers,
        warnings=warnings,
        profile=profile,
        side=baseline.get("side"),
    )
    decision_payload = decision.as_dict()
    package: dict[str, Any] = {
        "schema_version": SHADOW_ALLOWANCE_PACKAGE_SCHEMA_VERSION,
        "package_type": baseline.get("package_type") or "SHADOW_ALLOWANCE",
        "allowance_family": baseline.get("allowance_family") or "SHADOW",
        "execution_authority": "SHADOW_ANALYSIS_ONLY",
        "packet_authority": None,
        "live_executable": False,
        "accepted": False,
        "decision_accepted": False,
        "execution_ready": False,
        "executable": False,
        "side": baseline.get("side"),
        "ablation_profile": decision.profile.value,
        "shadow_state": decision.shadow_state,
        "shadow_would_allow": decision.shadow_would_allow,
        "prepare_allowed": decision.prepare_allowed,
        "source_allowance_schema_version": baseline.get("schema_version"),
        "source_package_type": baseline.get("package_type"),
        "comparison_packet_id": baseline.get("packet_id"),
        "baseline_execution_ready": baseline.get("execution_ready") is True,
        "baseline_accepted": baseline.get("accepted") is True,
        "baseline_final_state": baseline.get("final_state"),
        "baseline_true_blocker": baseline.get("true_blocker"),
        "hard_runtime_truth_preserved": True,
        "score": baseline.get("score"),
        "threshold": baseline.get("threshold"),
        "selected_lane": baseline.get("selected_lane"),
        "timing_mode": baseline.get("timing_mode"),
        "entry_now_allowed": baseline.get("entry_now_allowed") is True,
        "profile_policy": {
            "blocking_classes": sorted(
                member.value for member in _BLOCKING_CLASSES[decision.profile]
            ),
            "wait_states_become_prepare": (
                decision.profile is BlockerAblationProfile.WAIT_STATES_AS_PREPARE
            ),
            "live_handoff_forbidden": True,
        },
    }
    package.update(decision_payload)
    return package


def _dedupe(
    blockers: Iterable[ClassifiedBlockerV3],
) -> tuple[ClassifiedBlockerV3, ...]:
    rows: dict[tuple[str, str], ClassifiedBlockerV3] = {}
    for blocker in blockers:
        key = (blocker.code, blocker.source)
        existing = rows.get(key)
        if existing is None or (blocker.hard and not existing.hard):
            rows[key] = blocker
    return tuple(rows.values())
