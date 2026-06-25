from __future__ import annotations

from typing import Any, Mapping, cast

from phoenixguard.decision.model_council_v3 import MATURITY_STAGES


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def evaluate_maturity_stage(payload: Mapping[str, Any]) -> dict[str, Any]:
    council = _mapping(payload.get("model_council"))
    execution = _mapping(payload.get("execution"))
    stage = str(
        council.get("maturity_stage")
        or payload.get("maturity_stage")
        or ("EXECUTABLE_PACKET" if execution.get("enabled") else "OBSERVATION")
    )
    try:
        ordinal = MATURITY_STAGES.index(stage)
    except ValueError:
        ordinal = -1
    final_state = str(council.get("final_state") or execution.get("state") or payload.get("final_state") or "WATCHING").upper()
    return {
        "maturity_stage": stage,
        "ordinal": ordinal,
        "max_ordinal": len(MATURITY_STAGES) - 1,
        "is_executable_packet": stage == "EXECUTABLE_PACKET" or execution.get("enabled") is True,
        "final_state": final_state,
        "permission_result": "ALLOW" if final_state.endswith("EXECUTABLE") or execution.get("enabled") is True else "BLOCK",
    }
