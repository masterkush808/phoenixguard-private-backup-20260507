from __future__ import annotations

import sys

from phoenixguard.decision.model_council import (
    ALLOWANCE_PACKAGE_INTRADAY_ENTER_NOW,
    ALLOWANCE_PACKAGE_SCHEMA_VERSION,
    ALLOWANCE_PACKAGE_SWING,
    COUNCIL_STATES,
    DEFAULT_AI_CONTRIBUTION_STRENGTHS,
    DEFAULT_EXECUTION_LANE_THRESHOLDS,
    MATURITY_STAGES,
    MODEL_COUNCIL_STUDY_SCHEMA_VERSION,
    OPPORTUNITY_MATURITY_SCHEMA_VERSION,
    OPPORTUNITY_MATURITY_STATES,
    PG_EXECUTION_PACKET_SCHEMA_VERSION,
    PLAYBOOK_FINAL_DECIDER,
    PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION,
    ModelCouncilV3,
    build_entry_permission_v3,
    build_promotion_failure_audit_v3,
    evaluate_model_council_v3,
    publish_model_council_packet_v3,
    validate_execution_packet_v3,
)
from phoenixguard.decision.model_council import legacy_engine as _legacy_engine

__all__ = [
    "ALLOWANCE_PACKAGE_INTRADAY_ENTER_NOW",
    "ALLOWANCE_PACKAGE_SCHEMA_VERSION",
    "ALLOWANCE_PACKAGE_SWING",
    "COUNCIL_STATES",
    "DEFAULT_AI_CONTRIBUTION_STRENGTHS",
    "DEFAULT_EXECUTION_LANE_THRESHOLDS",
    "MATURITY_STAGES",
    "MODEL_COUNCIL_STUDY_SCHEMA_VERSION",
    "OPPORTUNITY_MATURITY_SCHEMA_VERSION",
    "OPPORTUNITY_MATURITY_STATES",
    "PG_EXECUTION_PACKET_SCHEMA_VERSION",
    "PLAYBOOK_FINAL_DECIDER",
    "PROMOTION_FAILURE_AUDIT_SCHEMA_VERSION",
    "ModelCouncilV3",
    "build_entry_permission_v3",
    "build_promotion_failure_audit_v3",
    "evaluate_model_council_v3",
    "publish_model_council_packet_v3",
    "validate_execution_packet_v3",
]

sys.modules[__name__] = _legacy_engine
