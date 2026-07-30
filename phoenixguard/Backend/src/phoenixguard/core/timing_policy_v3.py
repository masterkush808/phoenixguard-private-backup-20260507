"""Canonical duration eligibility policy for PhoenixGuard V3.

The policy deliberately separates *duration eligibility* from direction and
entry permission.  A study, signal, or execution packet may still be rejected
for many other reasons, but no fixed-duration OTC move shorter than fifteen
minutes is admitted into timing research or executable V3 language.
"""

from __future__ import annotations

import math
from typing import Any, cast


TIMING_DURATION_POLICY_SCHEMA_VERSION = "PG_TIMING_DURATION_POLICY_V3"
MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS = 15 * 60
MAXIMUM_STUDIED_TRADE_DURATION_SECONDS = 2 * 60 * 60


def _finite_seconds(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    # Never round a sub-threshold duration upward into eligibility.  Whole
    # elapsed seconds are the conservative public unit for OTC clock policy.
    return int(math.floor(parsed))


def duration_eligibility_contract_v3(value: object) -> dict[str, Any]:
    """Return a bounded, non-authorizing duration classification."""

    duration_seconds = _finite_seconds(value)
    if duration_seconds is None:
        status = "MISSING_DURATION"
        reason = "A positive fixed duration is required before timing can be studied."
        eligible = False
    elif duration_seconds < MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS:
        status = "EXCLUDED_UNDER_15_MINUTES"
        reason = (
            "Moves shorter than 15 minutes are excluded because they do not give "
            "the studied path enough time to breathe, accumulate, and survive a sweep."
        )
        eligible = False
    elif duration_seconds > MAXIMUM_STUDIED_TRADE_DURATION_SECONDS:
        status = "EXCLUDED_ABOVE_BOUNDED_HORIZON"
        reason = "The requested duration exceeds the bounded two-hour JPCLF horizon."
        eligible = False
    else:
        status = "ELIGIBLE"
        reason = "The duration is inside the bounded 15-minute to two-hour study horizon."
        eligible = True

    return {
        "schema_version": TIMING_DURATION_POLICY_SCHEMA_VERSION,
        "status": status,
        "eligible": eligible,
        "considered": eligible,
        "duration_seconds": duration_seconds,
        "minimum_eligible_duration_seconds": (
            MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
        ),
        "maximum_studied_duration_seconds": (
            MAXIMUM_STUDIED_TRADE_DURATION_SECONDS
        ),
        "reason": reason,
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
    }


__all__ = [
    "MAXIMUM_STUDIED_TRADE_DURATION_SECONDS",
    "MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS",
    "TIMING_DURATION_POLICY_SCHEMA_VERSION",
    "duration_eligibility_contract_v3",
]
