from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, cast


COUNTERTREND_SNIPER_SCHEMA_VERSION = "PG_COUNTERTREND_SNIPER_PROMOTION_V3"
COUNTERTREND_SNIPER_ENTRY_MODE = "COUNTERTREND_SNIPER"
COUNTERTREND_SNIPER_PRELIMINARY_PHASE = "PRELIMINARY"
COUNTERTREND_SNIPER_VALIDATED_PHASE = "VALIDATED"

COUNTERTREND_SNIPER_LINEAGE_KEYS = (
    "packet_id",
    "opportunity_id",
    "opportunity_key",
    "session_id",
    "symbol",
    "timeframe",
    "frame_id",
    "capture_count",
    "state_version",
    "input_frame_hash",
    "instrument_identity_hash",
    "trigger_closed_candle_key",
    "trigger_frame_id",
    "valid_until_epoch",
    "integrity_valid",
    "lineage_rejected",
)

_COUNTERTREND_THESES = {
    "SELL_IN_BUY_OPPOSING_FORCE_REACTION",
    "BUY_IN_SELL_OPPOSING_FORCE_REACTION",
    "OPPOSING_FORCE_REACTION",
}
_ENTER_NOW_PATHS = {"DIRECT_CONTINUATION", "PULLBACK_THEN_CONTINUATION"}
_MISSED_BOOK_STATES = {"LATE_CHASE", "MISSED"}
_MISSED_TIMING_MODES = {"SKIP_LATE_ENTRY", "EXPIRED", "MISSED", "LATE_CHASE"}
_GLOBAL_ROLE_NAMES = {"GLOBAL STRUCTURE EXPERT", "GLOBAL_STRUCTURE_EXPERT"}
_EXECUTION_ROLE_NAMES = {
    "LOCAL CANDLE PLAY EXPERT",
    "SUPPLY/DEMAND AND ZONE EXPERT",
    "ANGLE AND MOMENTUM EXPERT",
    "MEMORY SIMILARITY EXPERT",
    "TIMING AND PATH EXPERT",
    "RISK AND TRAP EXPERT",
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _side(value: Any) -> str:
    normalized = _upper(value)
    return normalized if normalized in {"BUY", "SELL"} else "HOLD"


def _opposite(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _strict_alias_value(
    source: Mapping[str, Any],
    keys: Sequence[str],
    *,
    expected: bool,
) -> bool:
    """Accept only explicit, mutually consistent literal booleans.

    Text aliases such as OPEN/READY and numeric truthiness are deliberately
    rejected. If more than one supported field is supplied, every supplied
    field must be a literal boolean with the same value.
    """

    values = [source[key] for key in keys if key in source]
    return bool(
        values
        and all(isinstance(value, bool) for value in values)
        and all(value is values[0] for value in values)
        and values[0] is expected
    )


def instrument_identity_hash_v3(instrument_context: Mapping[str, Any]) -> str:
    """Build a stable hash from immutable instrument-lock identity fields."""

    identity = {
        key: instrument_context.get(key)
        for key in (
            "schema_version",
            "session_id",
            "identity_state",
            "display_symbol",
            "ocr_symbol",
            "timeframe",
            "viewport_hash",
            "broker_surface_hash",
            "window_handle",
            "window_rect",
            "calibration_layout_id",
        )
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return "pginst_" + hashlib.sha256(encoded).hexdigest()[:32]


def build_countertrend_sniper_lineage_v3(
    execution_packet: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Extract the immutable promotion lineage from one execution packet."""

    packet = _mapping(execution_packet)
    opportunity = _mapping(packet.get("execution_opportunity_window_v3"))
    live_integrity = _mapping(packet.get("live_integrity"))
    instrument_context = _mapping(packet.get("instrument_context"))
    return {
        "packet_id": str(packet.get("packet_id") or ""),
        "opportunity_id": str(opportunity.get("opportunity_id") or ""),
        "opportunity_key": str(opportunity.get("opportunity_key") or ""),
        "session_id": str(packet.get("session_id") or ""),
        "symbol": str(packet.get("symbol") or ""),
        "timeframe": str(packet.get("timeframe") or "").upper(),
        "frame_id": _int(packet.get("frame_id"), 0),
        "capture_count": _int(packet.get("capture_count"), 0),
        "state_version": _int(packet.get("state_version"), 0),
        "input_frame_hash": str(
            packet.get("input_frame_hash")
            or live_integrity.get("input_frame_hash")
            or ""
        ),
        "instrument_identity_hash": str(
            packet.get("instrument_identity_hash")
            or (
                instrument_identity_hash_v3(instrument_context)
                if instrument_context
                else ""
            )
        ),
        "trigger_closed_candle_key": str(
            packet.get("trigger_closed_candle_key") or ""
        ),
        "trigger_frame_id": _int(packet.get("trigger_frame_id"), 0),
        "valid_until_epoch": _float(
            opportunity.get("valid_until_epoch")
            or opportunity.get("valid_until_epoch_sec"),
            0.0,
        ),
        "integrity_valid": opportunity.get("integrity_valid"),
        "lineage_rejected": opportunity.get("lineage_rejected"),
    }


def _lineage_complete(lineage: Mapping[str, Any]) -> bool:
    text_keys = {
        "packet_id",
        "opportunity_id",
        "opportunity_key",
        "session_id",
        "symbol",
        "timeframe",
        "input_frame_hash",
        "instrument_identity_hash",
        "trigger_closed_candle_key",
    }
    positive_integer_keys = {
        "frame_id",
        "capture_count",
        "state_version",
        "trigger_frame_id",
    }
    return bool(
        all(str(lineage.get(key) or "").strip() for key in text_keys)
        and all(_int(lineage.get(key), 0) > 0 for key in positive_integer_keys)
        and _float(lineage.get("valid_until_epoch"), 0.0) > 0.0
        and lineage.get("integrity_valid") is True
        and lineage.get("lineage_rejected") is False
    )


def _lineage_matches(
    lineage: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return bool(
        _lineage_complete(lineage)
        and _lineage_complete(expected)
        and all(lineage.get(key) == expected.get(key) for key in COUNTERTREND_SNIPER_LINEAGE_KEYS)
        and _int(lineage.get("trigger_frame_id"), 0)
        == _int(lineage.get("frame_id"), -1)
    )


def _role_ensemble_basis(
    model_role_outputs: Sequence[Mapping[str, Any]],
    *,
    side: str,
) -> dict[str, Any]:
    aligned_count = 0
    opposed_count = 0
    hold_count = 0
    aligned_confidence = 0.0
    opposed_confidence = 0.0
    global_role_count = 0
    global_role_side = "HOLD"
    global_role_confidence = 0.0
    eligible_execution_role_count = 0
    unknown_role_count = 0
    duplicate_execution_roles: set[str] = set()
    execution_rows: dict[str, Mapping[str, Any]] = {}
    for row in model_role_outputs:
        role = _upper(row.get("role"))
        confidence = max(0.0, min(1.0, _float(row.get("confidence"), 0.0)))
        if role in _GLOBAL_ROLE_NAMES:
            vote = _side(row.get("side_vote"))
            global_role_count += 1
            if confidence >= global_role_confidence:
                global_role_side = vote
                global_role_confidence = confidence
            continue
        if role not in _EXECUTION_ROLE_NAMES:
            unknown_role_count += 1
            continue
        if role in execution_rows or role in duplicate_execution_roles:
            execution_rows.pop(role, None)
            duplicate_execution_roles.add(role)
            continue
        execution_rows[role] = row

    for row in execution_rows.values():
        vote = _side(row.get("side_vote"))
        confidence = max(0.0, min(1.0, _float(row.get("confidence"), 0.0)))
        eligible_execution_role_count += 1
        if vote == side:
            aligned_count += 1
            aligned_confidence += confidence
        elif vote == _opposite(side):
            opposed_count += 1
            opposed_confidence += confidence
        else:
            hold_count += 1
    return {
        "eligible_non_global_execution_role_count": eligible_execution_role_count,
        "aligned_role_count": aligned_count,
        "opposed_role_count": opposed_count,
        "hold_role_count": hold_count,
        "aligned_role_confidence_sum": round(aligned_confidence, 4),
        "opposed_role_confidence_sum": round(opposed_confidence, 4),
        "global_role_count": global_role_count,
        "global_role_side": global_role_side,
        "global_role_confidence": round(global_role_confidence, 4),
        "unknown_role_count": unknown_role_count,
        "duplicate_execution_roles": sorted(duplicate_execution_roles),
    }


def classify_countertrend_sniper_promotion_v3(
    *,
    phase: str,
    side: str,
    global_side: str,
    professional_thesis: Mapping[str, Any],
    current_candle: Mapping[str, Any],
    execution_lane: Mapping[str, Any],
    timing_mode: str,
    timing_has_explicit_expiry: bool,
    entry_now_allowed: bool,
    path_class: str,
    opposing_force_ok: bool,
    final_execution_score: float,
    lane_required_score: float,
    council_side_score: float,
    opposite_side_score: float,
    global_side_score: float,
    dominance_margin: float,
    model_role_outputs: Sequence[Mapping[str, Any]],
    required_models_ready: bool,
    live_fresh: bool,
    identity_ok: bool,
    current_frame_ok: bool,
    trap_active: bool,
    history_exit_active: bool,
    late_chase: bool,
    book_strategy_state: str = "",
    execution_packet_present: bool | None = None,
    execution_packet_validated: bool | None = None,
    execution_lineage: Mapping[str, Any] | None = None,
    expected_lineage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a bounded V3 countertrend sniper opportunity.

    PRELIMINARY proves only that Book Strategy may consider the closed-candle
    rejection as its internal confirmation substitute. VALIDATED may authorize
    the public entry only after Book Strategy enters, the current execution
    packet validates, and immutable packet/trigger lineage matches outer truth.
    Neither phase grants broker-click authority.
    """

    resolved_phase = _upper(phase)
    resolved_side = _side(side)
    resolved_global_side = _side(global_side)
    thesis_state = _upper(professional_thesis.get("thesis_state"))
    authority_side = _side(professional_thesis.get("authority_side"))
    active = bool(
        resolved_side in {"BUY", "SELL"}
        and authority_side == resolved_side
        and thesis_state in _COUNTERTREND_THESES
        and resolved_global_side == _opposite(resolved_side)
    )

    current_candle_closed = _strict_alias_value(
        current_candle,
        (
            "current_candle_closed",
            "closed",
            "is_closed",
            "source_candle_closed",
        ),
        expected=True,
    )
    upper_wick_ratio = max(
        0.0,
        min(
            1.0,
            _float(
                current_candle.get("upper_shadow_range_ratio")
                or current_candle.get("upper_wick_ratio")
                or current_candle.get("upper_wick_range_ratio"),
                0.0,
            ),
        ),
    )
    lower_wick_ratio = max(
        0.0,
        min(
            1.0,
            _float(
                current_candle.get("lower_shadow_range_ratio")
                or current_candle.get("lower_wick_ratio")
                or current_candle.get("lower_wick_range_ratio"),
                0.0,
            ),
        ),
    )
    close_location = max(
        0.0,
        min(
            1.0,
            _float(
                current_candle.get("close_location_value")
                or current_candle.get("close_location")
                or current_candle.get("body_close_location"),
                0.5,
            ),
        ),
    )
    directional_wick_rejection = bool(
        (resolved_side == "SELL" and upper_wick_ratio >= 0.32 and close_location <= 0.45)
        or (resolved_side == "BUY" and lower_wick_ratio >= 0.32 and close_location >= 0.55)
    )
    explicit_closed_rejection = _strict_alias_value(
        current_candle,
        (
            "closed_rejection_confirmed",
            "rejection_confirmed",
            "opposing_force_rejection_confirmed",
        ),
        expected=True,
    )
    closed_candle_rejection = bool(
        current_candle_closed
        and (directional_wick_rejection or explicit_closed_rejection)
        and professional_thesis.get("opposing_force_rejection_confirmed") is True
    )
    trigger_closed_candle_key = str(
        current_candle.get("trigger_closed_candle_key") or ""
    ).strip()
    trigger_frame_id = _int(current_candle.get("trigger_frame_id"), 0)
    outer_frame_id = _int(current_candle.get("outer_frame_id"), 0)
    trigger_candle_identity = bool(
        trigger_closed_candle_key
        and trigger_frame_id > 0
        and outer_frame_id > 0
        and trigger_frame_id == outer_frame_id
    )
    zone_touch_age = professional_thesis.get(
        "opposing_force_zone_last_touch_age_candles"
    )
    zone_touch_age_valid = bool(
        isinstance(zone_touch_age, (int, float))
        and not isinstance(zone_touch_age, bool)
        and _float(zone_touch_age, -1.0) >= 0.0
        and _float(zone_touch_age, 2.0) <= 1.0
    )
    tested_sniper_zone = bool(
        professional_thesis.get("opposing_force_reaction_ready") is True
        and professional_thesis.get("opposing_force_is_near") is True
        and professional_thesis.get("opposing_force_is_proven") is True
        and _side(professional_thesis.get("opposing_force_zone_side")) == resolved_side
        and zone_touch_age_valid
    )
    current_candle_ok = bool(
        current_candle.get("entry_allowed") is True
        and _strict_alias_value(current_candle, ("too_late",), expected=False)
        and _strict_alias_value(current_candle, ("wick_reversal_risk",), expected=False)
    )
    lane_authority = bool(
        execution_lane.get("accepted") is True
        and execution_lane.get("professional_reaction_lane_authority") is True
        and _side(execution_lane.get("side")) == resolved_side
    )
    selected_wave = execution_lane.get("wave_context")
    wave_context: Mapping[str, Any] = (
        cast(Mapping[str, Any], selected_wave)
        if isinstance(selected_wave, Mapping)
        else {}
    )
    path_ready = bool(
        _upper(path_class) in _ENTER_NOW_PATHS
        and opposing_force_ok is True
        and wave_context.get("professional_reaction_path_ready") is True
        and wave_context.get("professional_reaction_has_actionable_room") is True
    )
    final_score_ready = float(final_execution_score) >= float(lane_required_score)
    candidate_score_ready = float(council_side_score) >= float(lane_required_score)
    role_basis = _role_ensemble_basis(model_role_outputs, side=resolved_side)
    aligned_execution_roles_ready = _int(role_basis.get("aligned_role_count"), 0) >= 2
    aligned_role_confidence_wins = _float(
        role_basis.get("aligned_role_confidence_sum"), 0.0
    ) > _float(role_basis.get("opposed_role_confidence_sum"), 0.0)
    ensemble_ready = bool(
        required_models_ready is True
        and final_score_ready
        and candidate_score_ready
        and aligned_execution_roles_ready
        and aligned_role_confidence_wins
        and lane_authority
    )
    no_contradictory_pressure = (
        professional_thesis.get("current_pressure_defends_against_opposing_force")
        is False
    )
    timing_ready = bool(
        _upper(timing_mode) == "ENTER_NOW"
        and timing_has_explicit_expiry is True
        and entry_now_allowed is True
    )
    not_late = bool(
        late_chase is False
        and _strict_alias_value(current_candle, ("too_late",), expected=False)
        and _upper(path_class) != "LATE_CHASE_REVERSAL_RISK"
        and _upper(timing_mode) not in _MISSED_TIMING_MODES
    )
    book_state = _upper(book_strategy_state)

    gates = {
        "countertrend_context": active,
        "tested_sniper_zone": tested_sniper_zone,
        "closed_candle_rejection": closed_candle_rejection,
        "trigger_candle_identity": trigger_candle_identity,
        "required_models_ready": required_models_ready is True,
        "candidate_directional_score": candidate_score_ready,
        "final_execution_score": final_score_ready,
        "aligned_execution_roles": aligned_execution_roles_ready,
        "aligned_role_confidence_wins": aligned_role_confidence_wins,
        "ensemble_lane_score": ensemble_ready,
        "live_fresh": live_fresh is True,
        "identity_locked": identity_ok is True,
        "current_frame": current_frame_ok is True,
        "current_candle_accepted": current_candle_ok,
        "timing_window_open": timing_ready,
        "path_and_room": path_ready,
        "no_contradictory_pressure": no_contradictory_pressure,
        "trap_clear": trap_active is False,
        "history_exit_clear": history_exit_active is False,
        "not_late": not_late,
    }
    promotion_ready = all(gates.values())
    missed = bool(
        active
        and (not not_late or book_state in _MISSED_BOOK_STATES)
    )

    resolved_lineage = _mapping(execution_lineage)
    resolved_expected_lineage = _mapping(expected_lineage)
    lineage_complete = _lineage_complete(resolved_lineage)
    lineage_matches = _lineage_matches(
        resolved_lineage,
        resolved_expected_lineage,
    )
    packet_confirmed = execution_packet_present is True
    authorization_gates = {
        "validated_phase": resolved_phase == COUNTERTREND_SNIPER_VALIDATED_PHASE,
        "book_strategy_enter_now": book_state == "ENTER_NOW",
        "execution_packet_present": packet_confirmed,
        "execution_packet_validated": execution_packet_validated is True,
        "execution_lineage_complete": lineage_complete,
        "execution_lineage_matches_outer_truth": lineage_matches,
    }
    enter_now = bool(promotion_ready and all(authorization_gates.values()))
    invalidated = bool(
        active
        and (
            gates["identity_locked"] is False
            or gates["current_frame"] is False
            or gates["trap_clear"] is False
            or gates["history_exit_clear"] is False
            or book_state == "INVALIDATED"
            or (
                resolved_phase == COUNTERTREND_SNIPER_VALIDATED_PHASE
                and packet_confirmed
                and (
                    authorization_gates["execution_packet_validated"] is False
                    or authorization_gates["execution_lineage_complete"] is False
                    or authorization_gates[
                        "execution_lineage_matches_outer_truth"
                    ]
                    is False
                )
            )
        )
    )
    classification = (
        "INVALIDATED"
        if invalidated
        else "MISSED_DO_NOT_CHASE"
        if missed
        else "ENTER_NOW"
        if enter_now
        else "FORMING"
    )
    failed_gates = [name for name, passed in gates.items() if not passed]
    failed_authorization_gates = [
        name for name, passed in authorization_gates.items() if not passed
    ]
    if classification == "ENTER_NOW":
        next_required = "Use only the current validated entry window; stop if any live gate changes."
    elif classification == "INVALIDATED":
        next_required = "This sniper proof is invalid; require a new current-frame identity, trigger, and validated packet."
    elif classification == "MISSED_DO_NOT_CHASE":
        next_required = "Do not chase this move; wait for a fresh sniper rejection and a new validated window."
    elif failed_gates:
        next_required = f"Waiting for {failed_gates[0].replace('_', ' ')}."
    elif resolved_phase == COUNTERTREND_SNIPER_PRELIMINARY_PHASE:
        next_required = "Preliminary rejection proof is ready for Book Strategy validation."
    elif failed_authorization_gates:
        next_required = (
            "Waiting for "
            f"{failed_authorization_gates[0].replace('_', ' ')}."
        )
    else:
        next_required = "Waiting for final countertrend authorization."

    ensemble_basis = {
        "candidate_side_score": round(float(council_side_score), 4),
        "council_side_score": round(float(council_side_score), 4),
        "opposite_side_score": round(float(opposite_side_score), 4),
        "global_side": resolved_global_side,
        "global_side_score": round(float(global_side_score), 4),
        "dominance_margin": round(float(dominance_margin), 4),
        "final_execution_score": round(float(final_execution_score), 4),
        "lane_required_score": round(float(lane_required_score), 4),
        "candidate_score_passed": candidate_score_ready,
        "final_score_passed": final_score_ready,
        "required_models_ready": required_models_ready is True,
        "selected_lane": str(execution_lane.get("name") or ""),
        "professional_reaction_lane_authority": lane_authority,
        "probability_claim": False,
        **role_basis,
    }
    return {
        "schema_version": COUNTERTREND_SNIPER_SCHEMA_VERSION,
        "phase": resolved_phase,
        "authoritative": enter_now,
        "preliminary_non_authoritative": (
            resolved_phase == COUNTERTREND_SNIPER_PRELIMINARY_PHASE
        ),
        "active": active,
        "classification": classification,
        "side": resolved_side,
        "against_global_side": resolved_global_side,
        "thesis_state": thesis_state,
        "promotion_ready": promotion_ready,
        "validated_entry_mode": COUNTERTREND_SNIPER_ENTRY_MODE if enter_now else "NONE",
        "entry_permission_authorized": enter_now,
        "movement_confirmation_bypass_allowed": enter_now,
        "movement_confirmation_substitute": (
            "CLOSED_CANDLE_OPPOSING_FORCE_REJECTION" if enter_now else "NONE"
        ),
        "same_side_movement_confirmation_required": not enter_now,
        "execution_packet_required": True,
        "execution_packet_present": packet_confirmed,
        "execution_packet_validated": execution_packet_validated is True,
        "book_strategy_state": book_state or "PENDING",
        "execution_authority_source": (
            "BOOK_STRATEGY_MASTER_V3_AND_VALIDATED_EXECUTION_PACKET"
            if enter_now
            else "NONE"
        ),
        "broker_click_authority": False,
        "closed_candle_evidence": {
            "closed": current_candle_closed,
            "directional_wick_rejection": directional_wick_rejection,
            "explicit_closed_rejection": explicit_closed_rejection,
            "trigger_closed_candle_key": trigger_closed_candle_key,
            "trigger_frame_id": trigger_frame_id,
            "outer_frame_id": outer_frame_id,
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "lower_wick_ratio": round(lower_wick_ratio, 4),
            "close_location": round(close_location, 4),
        },
        "lineage": resolved_lineage,
        "ensemble_basis": ensemble_basis,
        "gates": gates,
        "authorization_gates": authorization_gates,
        "blocking_gates": failed_gates,
        "authorization_blocking_gates": failed_authorization_gates,
        "next_required": next_required,
    }


__all__ = [
    "COUNTERTREND_SNIPER_ENTRY_MODE",
    "COUNTERTREND_SNIPER_LINEAGE_KEYS",
    "COUNTERTREND_SNIPER_PRELIMINARY_PHASE",
    "COUNTERTREND_SNIPER_SCHEMA_VERSION",
    "COUNTERTREND_SNIPER_VALIDATED_PHASE",
    "build_countertrend_sniper_lineage_v3",
    "classify_countertrend_sniper_promotion_v3",
    "instrument_identity_hash_v3",
]
