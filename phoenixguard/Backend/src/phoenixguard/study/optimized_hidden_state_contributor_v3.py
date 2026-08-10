from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, cast


OPTIMIZED_CONTRIBUTOR_SCHEMA_VERSION = "PG_OPTIMIZED_HIDDEN_STATE_CONTRIBUTOR_V3"


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    row = cast(Mapping[object, Any], value)
    return {str(key): item for key, item in row.items()}


def _candidate_from_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    direct = _mapping(value.get("masked_future_optimized_v3"))
    if direct:
        return direct
    direct = _mapping(value.get("optimized_hidden_state_evidence_v3"))
    if direct:
        return direct
    masked = _mapping(value.get("masked_future_behavior_v3"))
    nested = _mapping(masked.get("optimized_hidden_state"))
    if nested:
        return nested
    hidden = _mapping(value.get("hidden_state_discovery_v3"))
    nested = _mapping(hidden.get("optimized_hidden_state_evidence_v3"))
    if nested:
        return nested
    evidence = _mapping(value.get("evidence"))
    nested = _mapping(evidence.get("masked_future_optimized_v3"))
    if nested:
        return nested
    market = _mapping(value.get("market_context"))
    nested = _mapping(market.get("masked_future_optimized_v3"))
    return nested


def find_optimized_hidden_state_evidence_v3(
    *sources: Mapping[str, Any] | None,
) -> dict[str, Any]:
    queue: list[Mapping[str, Any]] = [source for source in sources if source is not None]
    visited = 0
    while queue and visited < 32:
        source = queue.pop(0)
        visited += 1
        candidate = _candidate_from_mapping(source)
        if candidate:
            return deepcopy(candidate)
        for key in ("snapshot", "market", "book_strategy", "intelligence", "model_council"):
            nested = _mapping(source.get(key))
            if nested:
                queue.append(nested)
    return {}


def contributor_summary_v3(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    row = _mapping(evidence)
    if not row:
        return {
            "schema_version": OPTIMIZED_CONTRIBUTOR_SCHEMA_VERSION,
            "status": "UNAVAILABLE",
            "study_only": True,
            "execution_authority": "NONE",
            "grants_entry_permission": False,
        }
    return {
        "schema_version": OPTIMIZED_CONTRIBUTOR_SCHEMA_VERSION,
        "status": str(row.get("status") or "OBSERVING"),
        "side": str(row.get("side") or row.get("side_candidate") or "HOLD"),
        "event_type": str(row.get("event_type") or "NO_OPPORTUNITY"),
        "opportunity_maturity": str(row.get("opportunity_maturity") or "NO_OPPORTUNITY"),
        "target_before_invalidation_probability": float(
            row.get("target_before_invalidation_probability") or 0.0
        ),
        "selected_high_confidence": bool(row.get("selected_high_confidence", False)),
        "calibrated": bool(row.get("calibrated", False)),
        "promotion_eligible": bool(row.get("promotion_eligible", False)),
        "study_only": True,
        "execution_authority": "NONE",
        "grants_entry_permission": False,
    }


def attach_optimized_hidden_state_evidence_v3(
    payload: Mapping[str, Any],
    *sources: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = deepcopy(dict(payload))
    evidence = find_optimized_hidden_state_evidence_v3(result, *sources)
    result["optimized_hidden_state_contributor_v3"] = contributor_summary_v3(evidence)
    if evidence:
        result["masked_future_optimized_v3"] = evidence
    return result
