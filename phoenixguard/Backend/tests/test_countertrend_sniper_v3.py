from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from phoenixguard.decision.countertrend_sniper_v3 import (
    COUNTERTREND_SNIPER_LINEAGE_KEYS,
    COUNTERTREND_SNIPER_PRELIMINARY_PHASE,
    COUNTERTREND_SNIPER_VALIDATED_PHASE,
    build_countertrend_sniper_lineage_v3,
    classify_countertrend_sniper_promotion_v3,
)


def _lineage(side: str) -> dict[str, Any]:
    return {
        "packet_id": f"pgpkt_{side.lower()}_001",
        "opportunity_id": f"pgepisode_{side.lower()}_001",
        "opportunity_key": f"pgopp_{side.lower()}_001",
        "session_id": "pocket-live-8788",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": 901,
        "capture_count": 903,
        "state_version": 1901,
        "input_frame_hash": "frame_901",
        "instrument_identity_hash": "pginst_001",
        "trigger_closed_candle_key": "EURGBP|M5|closed|901",
        "trigger_frame_id": 901,
        "valid_until_epoch": 1_800_010_300.0,
        "integrity_valid": True,
        "lineage_rejected": False,
    }


def _role_outputs(side: str, global_side: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "Global Structure Expert",
            "side_vote": global_side,
            "confidence": 0.92,
        },
        {
            "role": "Local Candle Play Expert",
            "side_vote": side,
            "confidence": 0.78,
        },
        {
            "role": "Supply/Demand and Zone Expert",
            "side_vote": side,
            "confidence": 0.74,
        },
        {
            "role": "Risk and Trap Expert",
            "side_vote": global_side,
            "confidence": 0.63,
        },
    ]


def _promotion_input(
    side: str,
    *,
    phase: str = COUNTERTREND_SNIPER_VALIDATED_PHASE,
) -> dict[str, Any]:
    global_side = "SELL" if side == "BUY" else "BUY"
    thesis_state = (
        "BUY_IN_SELL_OPPOSING_FORCE_REACTION"
        if side == "BUY"
        else "SELL_IN_BUY_OPPOSING_FORCE_REACTION"
    )
    lineage = _lineage(side)
    current_candle = {
        "entry_allowed": True,
        "current_candle_closed": True,
        "upper_shadow_range_ratio": 0.08 if side == "BUY" else 0.44,
        "lower_shadow_range_ratio": 0.44 if side == "BUY" else 0.08,
        "close_location_value": 0.76 if side == "BUY" else 0.24,
        "closed_rejection_confirmed": True,
        "trigger_closed_candle_key": lineage["trigger_closed_candle_key"],
        "trigger_frame_id": lineage["trigger_frame_id"],
        "outer_frame_id": lineage["frame_id"],
        "too_late": False,
        "wick_reversal_risk": False,
    }
    return {
        "phase": phase,
        "side": side,
        "global_side": global_side,
        "professional_thesis": {
            "thesis_state": thesis_state,
            "authority_side": side,
            "opposing_force_reaction_ready": True,
            "opposing_force_rejection_confirmed": True,
            "opposing_force_is_near": True,
            "opposing_force_is_proven": True,
            "opposing_force_zone_side": side,
            "opposing_force_zone_last_touch_age_candles": 0,
            "current_pressure_defends_against_opposing_force": False,
        },
        "current_candle": current_candle,
        "execution_lane": {
            "name": "SNIPER_ZONE_ENTRY",
            "side": side,
            "accepted": True,
            "professional_reaction_lane_authority": True,
            "wave_context": {
                "professional_reaction_path_ready": True,
                "professional_reaction_has_actionable_room": True,
            },
        },
        "timing_mode": "ENTER_NOW",
        "timing_has_explicit_expiry": True,
        "entry_now_allowed": True,
        "path_class": "DIRECT_CONTINUATION",
        "opposing_force_ok": True,
        "final_execution_score": 0.78,
        "lane_required_score": 0.70,
        "council_side_score": 0.78,
        "opposite_side_score": 0.72,
        "global_side_score": 0.72,
        "dominance_margin": 0.06,
        "model_role_outputs": _role_outputs(side, global_side),
        "required_models_ready": True,
        "live_fresh": True,
        "identity_ok": True,
        "current_frame_ok": True,
        "trap_active": False,
        "history_exit_active": False,
        "late_chase": False,
        "book_strategy_state": "ENTER_NOW",
        "execution_packet_present": True,
        "execution_packet_validated": True,
        "execution_lineage": lineage,
        "expected_lineage": deepcopy(lineage),
    }


def test_preliminary_phase_is_ready_for_book_strategy_but_never_authoritative() -> None:
    inputs = _promotion_input(
        "SELL",
        phase=COUNTERTREND_SNIPER_PRELIMINARY_PHASE,
    )
    inputs.update(
        {
            "book_strategy_state": "",
            "execution_packet_present": None,
            "execution_packet_validated": None,
            "execution_lineage": {},
            "expected_lineage": {},
        }
    )

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["phase"] == "PRELIMINARY"
    assert promotion["promotion_ready"] is True
    assert promotion["preliminary_non_authoritative"] is True
    assert promotion["authoritative"] is False
    assert promotion["classification"] == "FORMING"
    assert promotion["entry_permission_authorized"] is False
    assert promotion["movement_confirmation_bypass_allowed"] is False


def test_validated_contract_is_directionally_symmetric_and_lineage_bound() -> None:
    buy = classify_countertrend_sniper_promotion_v3(**_promotion_input("BUY"))
    sell = classify_countertrend_sniper_promotion_v3(**_promotion_input("SELL"))

    for promotion, side, global_side in (
        (buy, "BUY", "SELL"),
        (sell, "SELL", "BUY"),
    ):
        assert promotion["phase"] == "VALIDATED"
        assert promotion["classification"] == "ENTER_NOW"
        assert promotion["side"] == side
        assert promotion["against_global_side"] == global_side
        assert promotion["gates"]["closed_candle_rejection"] is True
        assert promotion["gates"]["aligned_execution_roles"] is True
        assert promotion["authorization_gates"]["execution_lineage_matches_outer_truth"] is True
        assert promotion["lineage"]["trigger_frame_id"] == promotion["lineage"]["frame_id"]
        assert set(promotion["lineage"]) == set(COUNTERTREND_SNIPER_LINEAGE_KEYS)
        assert "expected_lineage" not in promotion
        assert promotion["movement_confirmation_bypass_allowed"] is True
        assert promotion["broker_click_authority"] is False


@pytest.mark.parametrize(
    ("path", "bad_value"),
    [
        (("current_candle", "current_candle_closed"), "OPEN"),
        (("current_candle", "entry_allowed"), "READY"),
        (("professional_thesis", "opposing_force_reaction_ready"), "READY"),
        (("professional_thesis", "opposing_force_rejection_confirmed"), "READY"),
        (("professional_thesis", "opposing_force_is_near"), "OPEN"),
        (("professional_thesis", "opposing_force_is_proven"), 1),
        (("execution_lane", "accepted"), "OPEN"),
        (("execution_lane", "professional_reaction_lane_authority"), "READY"),
        (("execution_lane", "wave_context", "professional_reaction_path_ready"), "READY"),
        (("execution_lane", "wave_context", "professional_reaction_has_actionable_room"), 1),
        (("required_models_ready",), "READY"),
        (("live_fresh",), "OPEN"),
        (("identity_ok",), 1),
        (("current_frame_ok",), "READY"),
        (("timing_has_explicit_expiry",), "OPEN"),
        (("entry_now_allowed",), 1),
        (("opposing_force_ok",), "READY"),
        (("execution_packet_present",), "OPEN"),
        (("execution_packet_validated",), "READY"),
    ],
)
def test_authorization_gates_reject_non_literal_boolean_aliases(
    path: tuple[str, ...],
    bad_value: Any,
) -> None:
    inputs = _promotion_input("SELL")
    target: dict[str, Any] = inputs
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad_value

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["classification"] != "ENTER_NOW"
    assert promotion["entry_permission_authorized"] is False
    assert promotion["movement_confirmation_bypass_allowed"] is False


def test_conflicting_closed_aliases_fail_closed() -> None:
    inputs = _promotion_input("SELL")
    inputs["current_candle"]["closed"] = False

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["gates"]["closed_candle_rejection"] is False
    assert promotion["classification"] == "FORMING"


@pytest.mark.parametrize("age", [-1, -0.01, "0", None, True])
def test_zone_touch_age_requires_explicit_non_negative_numeric_age(age: Any) -> None:
    inputs = _promotion_input("SELL")
    inputs["professional_thesis"][
        "opposing_force_zone_last_touch_age_candles"
    ] = age

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["gates"]["tested_sniper_zone"] is False
    assert promotion["entry_permission_authorized"] is False


def test_candidate_side_score_not_opposite_or_final_score_funds_lane() -> None:
    inputs = _promotion_input("SELL")
    inputs["council_side_score"] = 0.10
    inputs["opposite_side_score"] = 0.99
    inputs["global_side_score"] = 0.99
    inputs["final_execution_score"] = 0.99

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["ensemble_basis"]["candidate_side_score"] == 0.10
    assert promotion["ensemble_basis"]["global_side_score"] == 0.99
    assert promotion["gates"]["candidate_directional_score"] is False
    assert promotion["classification"] == "FORMING"


@pytest.mark.parametrize(
    "roles",
    [
        [
            {"role": "Forged Role A", "side_vote": "SELL", "confidence": 1.0},
            {"role": "Forged Role B", "side_vote": "SELL", "confidence": 1.0},
        ],
        [
            {"role": "Local Candle Play Expert", "side_vote": "SELL", "confidence": 0.9},
            {"role": "Local Candle Play Expert", "side_vote": "SELL", "confidence": 0.9},
        ],
        [
            {"role": "Local Candle Play Expert", "side_vote": "SELL", "confidence": 0.55},
            {"role": "Supply/Demand and Zone Expert", "side_vote": "SELL", "confidence": 0.55},
            {"role": "Risk and Trap Expert", "side_vote": "BUY", "confidence": 1.0},
            {"role": "Timing and Path Expert", "side_vote": "BUY", "confidence": 0.2},
        ],
    ],
)
def test_unknown_duplicate_or_confidence_losing_roles_cannot_authorize(
    roles: list[dict[str, Any]],
) -> None:
    inputs = _promotion_input("SELL")
    inputs["model_role_outputs"] = roles

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["classification"] == "FORMING"
    assert promotion["entry_permission_authorized"] is False


@pytest.mark.parametrize("key", COUNTERTREND_SNIPER_LINEAGE_KEYS)
def test_every_lineage_field_is_immutable_and_must_match_outer_truth(key: str) -> None:
    inputs = _promotion_input("SELL")
    expected = inputs["expected_lineage"]
    if isinstance(expected[key], bool):
        expected[key] = not expected[key]
    elif isinstance(expected[key], (int, float)):
        expected[key] = expected[key] + 1
    else:
        expected[key] = f"{expected[key]}-tampered"

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["authorization_gates"][
        "execution_lineage_matches_outer_truth"
    ] is False
    assert promotion["classification"] == "INVALIDATED"
    assert promotion["entry_permission_authorized"] is False


def test_missing_trigger_provenance_cannot_be_imputed() -> None:
    inputs = _promotion_input("SELL")
    inputs["current_candle"]["trigger_closed_candle_key"] = ""
    inputs["current_candle"]["trigger_frame_id"] = 0

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["gates"]["trigger_candle_identity"] is False
    assert promotion["classification"] == "FORMING"


def test_identity_or_trap_failure_is_invalidated_not_missed() -> None:
    identity_inputs = _promotion_input("SELL")
    identity_inputs["identity_ok"] = False
    trap_inputs = _promotion_input("SELL")
    trap_inputs["trap_active"] = True

    for inputs in (identity_inputs, trap_inputs):
        promotion = classify_countertrend_sniper_promotion_v3(**inputs)
        assert promotion["classification"] == "INVALIDATED"
        assert promotion["entry_permission_authorized"] is False


def test_late_setup_is_missed_not_integrity_invalidated() -> None:
    inputs = _promotion_input("SELL")
    inputs["late_chase"] = True
    inputs["current_candle"]["too_late"] = True
    inputs["book_strategy_state"] = "LATE_CHASE"
    inputs["execution_packet_present"] = False
    inputs["execution_packet_validated"] = False
    inputs["execution_lineage"] = {}

    promotion = classify_countertrend_sniper_promotion_v3(**inputs)

    assert promotion["classification"] == "MISSED_DO_NOT_CHASE"
    assert promotion["entry_permission_authorized"] is False


def test_lineage_extraction_binds_packet_opportunity_identity_and_trigger() -> None:
    lineage = _lineage("SELL")
    packet = {
        "packet_id": lineage["packet_id"],
        "session_id": lineage["session_id"],
        "symbol": lineage["symbol"],
        "timeframe": lineage["timeframe"],
        "frame_id": lineage["frame_id"],
        "capture_count": lineage["capture_count"],
        "state_version": lineage["state_version"],
        "instrument_identity_hash": lineage["instrument_identity_hash"],
        "trigger_closed_candle_key": lineage["trigger_closed_candle_key"],
        "trigger_frame_id": lineage["trigger_frame_id"],
        "live_integrity": {"input_frame_hash": lineage["input_frame_hash"]},
        "execution_opportunity_window_v3": {
            "opportunity_id": lineage["opportunity_id"],
            "opportunity_key": lineage["opportunity_key"],
            "valid_until_epoch": lineage["valid_until_epoch"],
            "integrity_valid": True,
            "lineage_rejected": False,
        },
    }

    assert build_countertrend_sniper_lineage_v3(packet) == lineage
