from __future__ import annotations

import copy
from typing import Any

from phoenixguard.decision.order_positioning_v3 import (
    ORDER_POSITIONING_ACTUAL_SCHEMA_VERSION,
    advance_order_positioning_plan_v3,
    build_order_positioning_candidates_v3,
    fit_order_positioning_reprojection_v3,
    freeze_order_positioning_plan_v3,
    inverse_reproject_order_positioning_y_v3,
    reproject_order_positioning_bounds_v3,
)


def _overlay(
    overlay_type: str,
    side: str,
    bounds: list[float] | None,
    *,
    source_key: str,
    frame_id: int = 42,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
        "overlay_id": f"overlay-{source_key}-{frame_id}",
        "object_id": f"object-{source_key}",
        "track_id": source_key,
        "type": overlay_type,
        "side": side,
        "frame_id": frame_id,
        "sequence_id": "sequence-orders",
        "chart_transform_id": "transform-orders",
        "broker_source_lock_id": "source-lock-orders",
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "bounds": bounds,
        "confidence": 0.91,
        "truth_score": 0.89,
        "lifecycle_state": "ACTIVE",
        "anchor_candle_indices": [7, 9],
        "anchor_evidence_status": "VALID",
        "anchor_evidence": {"valid": True},
        "anchor_quality": {
            "score": 0.92,
            "has_candle_anchor": True,
            "has_sequence_anchor": True,
            "inside_plot_area": True,
            "matches_symbol_timeframe": True,
            "chart_transform_valid": True,
        },
    }
    payload.update(overrides)
    return payload


def _session(
    side: str = "BUY",
    *,
    overlays: list[dict[str, Any]] | None = None,
    frame_id: int = 42,
    current_price_y: float = 350.0,
    favorable_candles: int = 0,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "side": side,
        "thesis_verified": True,
        "frame_id": frame_id,
        "sequence_id": "sequence-orders",
        "chart_transform_id": "transform-orders",
        "broker_source_lock_id": "source-lock-orders",
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "market": "EURUSD_OTC",
        "timeframe": "M5",
        "price_axis_orientation": "SCREEN_Y_INCREASES_DOWN",
        "current_price_basis": "LATEST_COMPLETED_CANDLE",
        "display_band_norm": 0.006,
        "display_band_verified": True,
        "display_band_basis": "VERIFIED_MEDIAN_CANDLE_RANGE",
        "chart_bounds": [100.0, 100.0, 1100.0, 700.0],
        "current_price_y": current_price_y,
        "current_price_verified": True,
        "timing_verified": True,
        "favorable_candles_since_origin": favorable_candles,
        "reaction_window_verified": True,
        "geometry_role": "FORWARD_REACTION_WINDOW",
        "reaction_window_anchor": "LATEST_COMPLETED_CANDLE",
        "reaction_window_anchor_id": "candle-d",
        "reaction_window_origin_x_norm": 0.72,
        "reaction_window_step_x_norm": 0.01,
        "reaction_window_horizon_steps": 12,
        "overlay_objects": overlays or [],
        "reprojection_anchors": [
            {"anchor_id": "candle-a", "x_norm": 0.20, "y_norm": 0.35},
            {"anchor_id": "candle-b", "x_norm": 0.45, "y_norm": 0.48},
            {"anchor_id": "candle-c", "x_norm": 0.72, "y_norm": 0.61},
            {"anchor_id": "candle-d", "x_norm": 0.86, "y_norm": 0.42},
        ],
    }
    payload.update(overrides)
    return payload


def _demand(*, frame_id: int = 42) -> dict[str, Any]:
    return _overlay(
        "DEMAND_ZONE",
        "BUY",
        [300.0, 420.0, 650.0, 480.0],
        source_key="demand-7-9",
        frame_id=frame_id,
        confirmation_state="CONFIRMED",
        confirmation_evidence={
            "valid": True,
            "state": "CONFIRMED",
            "is_closed": True,
            "closed_candle_key": "closed-bear-break",
            "side": "SELL",
            "event_type": "BREAK_OF_STRUCTURE",
        },
    )


def _supply(*, frame_id: int = 42) -> dict[str, Any]:
    return _overlay(
        "SUPPLY_ZONE",
        "SELL",
        [300.0, 200.0, 650.0, 260.0],
        source_key="supply-7-9",
        frame_id=frame_id,
        confirmation_state="CONFIRMED",
        confirmation_evidence={
            "valid": True,
            "state": "CONFIRMED",
            "is_closed": True,
            "closed_candle_key": "closed-bull-break",
            "side": "BUY",
            "event_type": "BREAK_OF_STRUCTURE",
        },
    )


def _actual(
    step: int,
    *,
    high: float = 0.35,
    low: float = 0.42,
    close: float = 0.40,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": ORDER_POSITIONING_ACTUAL_SCHEMA_VERSION,
        "verified": True,
        "is_closed": True,
        "frame_id": 42 + step,
        "closed_candle_key": f"closed-{step}",
        "sequence_id": "sequence-orders",
        "chart_transform_id": "transform-orders",
        "broker_source_lock_id": "source-lock-orders",
        "market": "EURUSD_OTC",
        "timeframe": "M5",
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "high_y_norm": high,
        "low_y_norm": low,
        "close_y_norm": close,
    }
    payload.update(overrides)
    return payload


def _freeze(candidate: dict[str, Any]) -> dict[str, Any]:
    return freeze_order_positioning_plan_v3(
        candidate,
        "episode-orders",
        "closed-0",
        42,
        "2026-07-19T12:00:00Z",
    )


def _zones_by_intent(candidate: dict[str, Any], intent: str) -> list[dict[str, Any]]:
    return [
        zone
        for zone in candidate["candidate_zones"]
        if zone["intent"] == intent
    ]


def test_buy_builds_pullback_breakout_and_distinct_protective_stop_areas() -> None:
    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[_demand(), _supply()])
    )

    assert candidate["status"] == "READY"
    assert candidate["chart_bounds"] == [0.0, 0.0, 1.0, 1.0]
    assert candidate["coordinate_mode"] == "CHART_NORMALIZED"
    assert candidate["current_price_y_norm"] == 0.416667

    limit = _zones_by_intent(candidate, "ENTRY_LIMIT")
    stop_entry = _zones_by_intent(candidate, "ENTRY_STOP")
    protection = _zones_by_intent(candidate, "PROTECTIVE_STOP")
    assert len(limit) == 1
    assert len(stop_entry) == 1
    assert len(protection) == 2
    assert limit[0]["order_kind"] == "BUY_LIMIT"
    assert limit[0]["bounds"] == [0.72, 0.533333, 0.84, 0.633333]
    assert limit[0]["source_bounds"] == [0.2, 0.533333, 0.55, 0.633333]
    assert limit[0]["geometry_role"] == "FORWARD_REACTION_WINDOW"
    assert limit[0]["reaction_window_anchor"] == "LATEST_COMPLETED_CANDLE"
    assert stop_entry[0]["order_kind"] == "BUY_STOP"
    assert stop_entry[0]["bounds"] == [0.72, 0.160667, 0.84, 0.166667]
    assert stop_entry[0]["source_bounds"] == [0.2, 0.166667, 0.55, 0.266667]
    assert {zone["order_kind"] for zone in protection} == {"SELL_STOP"}
    assert all(zone["protected_entry_zone_id"] for zone in protection)


def test_sell_builds_sell_limit_sell_stop_and_buy_protection() -> None:
    candidate = build_order_positioning_candidates_v3(
        _session("SELL", overlays=[_supply(), _demand()])
    )

    assert candidate["status"] == "READY"
    assert _zones_by_intent(candidate, "ENTRY_LIMIT")[0]["order_kind"] == "SELL_LIMIT"
    assert _zones_by_intent(candidate, "ENTRY_STOP")[0]["order_kind"] == "SELL_STOP"
    assert {
        zone["order_kind"]
        for zone in _zones_by_intent(candidate, "PROTECTIVE_STOP")
    } == {"BUY_STOP"}


def test_trigger_target_sniper_and_prediction_geometry_cannot_create_orders() -> None:
    overlays = [
        _overlay(
            overlay_type,
            "BUY",
            [250.0, 300.0, 400.0, 340.0],
            source_key=f"ignored-{index}",
        )
        for index, overlay_type in enumerate(
            ("TRIGGER", "SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX", "PREDICTION_PATH")
        )
    ]

    candidate = build_order_positioning_candidates_v3(_session(overlays=overlays))

    assert candidate["status"] == "BLOCKED"
    assert candidate["candidate_zones"] == []
    assert candidate["blockers"] == ["NO_VERIFIED_POSITIONING_SOURCE"]


def test_unverified_price_or_anchor_fails_closed_without_any_zone() -> None:
    price_blocked = build_order_positioning_candidates_v3(
        _session(overlays=[_demand()], current_price_verified=False)
    )
    unanchored = _demand()
    unanchored["anchor_evidence_status"] = "MISSING_ANCHOR_EVIDENCE"
    anchor_blocked = build_order_positioning_candidates_v3(
        _session(overlays=[unanchored])
    )

    assert price_blocked["status"] == "BLOCKED"
    assert price_blocked["candidate_zones"] == []
    assert "CURRENT_PRICE_GEOMETRY_UNVERIFIED" in price_blocked["blockers"]
    assert anchor_blocked["status"] == "BLOCKED"
    assert anchor_blocked["candidate_zones"] == []
    assert anchor_blocked["rejected_sources"] == [
        {"source_key": "demand-7-9", "reason": "ANCHOR_EVIDENCE_INVALID"}
    ]


def test_reaction_window_rejects_a_claimed_horizon_that_would_be_truncated() -> None:
    candidate = build_order_positioning_candidates_v3(
        _session(
            overlays=[_demand()],
            reaction_window_origin_x_norm=0.90,
            reaction_window_step_x_norm=0.02,
        )
    )

    assert candidate["status"] == "BLOCKED"
    assert candidate["candidate_zones"] == []
    assert candidate["blockers"] == ["REACTION_WINDOW_HORIZON_TRUNCATED"]


def test_stale_transform_and_source_lock_are_rejected() -> None:
    stale = _demand()
    stale["frame_id"] = 41
    wrong_transform = _supply()
    wrong_transform["chart_transform_id"] = "other-transform"

    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[stale, wrong_transform])
    )

    assert candidate["status"] == "BLOCKED"
    assert {row["reason"] for row in candidate["rejected_sources"]} == {
        "STALE_SOURCE_FRAME",
        "SOURCE_TRANSFORM_MISMATCH",
    }


def test_fifth_favorable_candle_is_a_late_chase_and_never_replans() -> None:
    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[_demand()], favorable_candles=5)
    )

    assert candidate["status"] == "BLOCKED"
    assert candidate["candidate_zones"] == []
    assert candidate["blockers"] == [
        "LATE_CHASE_FIVE_OR_MORE_FAVORABLE_CANDLES"
    ]


def test_crossed_area_is_late_but_an_early_area_remains_visible() -> None:
    buy_below_demand = build_order_positioning_candidates_v3(
        _session(overlays=[_demand()], current_price_y=520.0)
    )
    early = build_order_positioning_candidates_v3(
        _session(overlays=[_demand()], current_price_y=300.0)
    )

    assert buy_below_demand["status"] == "BLOCKED"
    assert buy_below_demand["rejected_sources"] == [
        {"source_key": "demand-7-9", "reason": "LATE_CHASE_ENTRY_LIMIT"}
    ]
    assert early["status"] == "READY"
    assert _zones_by_intent(early, "ENTRY_LIMIT")[0]["timing_state"] == "WAITING_EARLY"


def test_trendline_requires_an_explicit_verified_projected_price_band() -> None:
    trendline = _overlay(
        "SUPPORT_TRENDLINE",
        "BUY",
        None,
        source_key="support-line",
    )
    blocked = build_order_positioning_candidates_v3(
        _session(overlays=[trendline])
    )
    trendline["projected_entry_band"] = {
        "verified": True,
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "bounds": [300.0, 420.0, 650.0, 440.0],
    }
    accepted = build_order_positioning_candidates_v3(
        _session(overlays=[trendline])
    )

    assert blocked["status"] == "BLOCKED"
    assert blocked["rejected_sources"] == [
        {
            "source_key": "support-line",
            "reason": "TRENDLINE_PROJECTED_BAND_UNVERIFIED",
        }
    ]
    assert accepted["status"] == "READY"
    assert _zones_by_intent(accepted, "ENTRY_LIMIT")[0]["source_type"] == (
        "SUPPORT_TRENDLINE"
    )


def test_zone_ids_are_semantic_and_stable_across_a_new_source_frame() -> None:
    first = build_order_positioning_candidates_v3(
        _session(overlays=[_demand()], frame_id=42)
    )
    second = build_order_positioning_candidates_v3(
        _session(overlays=[_demand(frame_id=43)], frame_id=43)
    )

    assert [zone["zone_id"] for zone in first["candidate_zones"]] == [
        zone["zone_id"] for zone in second["candidate_zones"]
    ]


def test_duplicate_render_objects_for_one_track_emit_one_semantic_zone_pair() -> None:
    first = _demand()
    duplicate = _demand()
    duplicate["overlay_id"] = "another-render-object"

    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[duplicate, first])
    )

    assert candidate["status"] == "READY"
    assert len(candidate["candidate_zones"]) == 2
    assert len({zone["zone_id"] for zone in candidate["candidate_zones"]}) == 2


def test_stop_entry_requires_explicit_same_side_closed_structure_confirmation() -> None:
    plain_supply = _supply()
    plain_supply.pop("confirmation_state")
    plain_supply.pop("confirmation_evidence")

    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[_demand(), plain_supply])
    )

    assert candidate["status"] == "READY"
    assert _zones_by_intent(candidate, "ENTRY_STOP") == []
    assert {
        row["reason"] for row in candidate["rejected_sources"]
    } == {"STOP_ENTRY_CLOSED_CONFIRMATION_UNPROVEN"}


def test_overlapping_order_routes_keep_only_the_strongest_verified_source() -> None:
    strongest = _demand()
    strongest["anchor_quality"]["score"] = 0.96
    weaker = _overlay(
        "DEMAND_ZONE",
        "BUY",
        [320.0, 430.0, 670.0, 490.0],
        source_key="weaker-overlapping-demand",
        anchor_quality={
            "score": 0.72,
            "has_candle_anchor": True,
            "has_sequence_anchor": True,
            "inside_plot_area": True,
            "matches_symbol_timeframe": True,
            "chart_transform_valid": True,
        },
    )

    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[weaker, strongest])
    )

    assert candidate["status"] == "READY"
    assert len(_zones_by_intent(candidate, "ENTRY_LIMIT")) == 1
    assert len(_zones_by_intent(candidate, "PROTECTIVE_STOP")) == 1
    assert {
        row["reason"] for row in candidate["rejected_sources"]
    } == {"VALID_CONTEXT_NOT_NEAREST_ENTRY_LIMIT"}


def test_non_overlapping_same_intent_selects_nearest_route_before_protection() -> None:
    nearest = _overlay(
        "DEMAND_ZONE",
        "BUY",
        [300.0, 390.0, 650.0, 430.0],
        source_key="nearest-current-demand",
        anchor_candle_indices=[65, 66],
        anchor_quality={
            "score": 0.82,
            "has_candle_anchor": True,
            "has_sequence_anchor": True,
            "inside_plot_area": True,
            "matches_symbol_timeframe": True,
            "chart_transform_valid": True,
        },
    )
    stronger_but_deeper = _overlay(
        "DEMAND_ZONE",
        "BUY",
        [300.0, 520.0, 650.0, 580.0],
        source_key="stronger-deep-context",
        anchor_candle_indices=[60, 61],
        confidence=0.99,
        truth_score=0.99,
        anchor_quality={
            "score": 0.99,
            "has_candle_anchor": True,
            "has_sequence_anchor": True,
            "inside_plot_area": True,
            "matches_symbol_timeframe": True,
            "chart_transform_valid": True,
        },
    )

    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[stronger_but_deeper, nearest])
    )
    entries = _zones_by_intent(candidate, "ENTRY_LIMIT")
    protection = _zones_by_intent(candidate, "PROTECTIVE_STOP")

    assert len(entries) == 1
    assert len(protection) == 1
    assert entries[0]["source_key"] == "nearest-current-demand"
    assert entries[0]["bounds"] == [0.72, 0.483333, 0.84, 0.55]
    assert entries[0]["source_bounds"] == [0.2, 0.483333, 0.55, 0.55]
    assert protection[0]["protected_entry_zone_id"] == entries[0]["zone_id"]
    assert {
        (row["source_key"], row["reason"])
        for row in candidate["rejected_sources"]
    } == {
        ("stronger-deep-context", "VALID_CONTEXT_NOT_NEAREST_ENTRY_LIMIT")
    }


def test_stop_entry_requires_a_named_closed_confirmation_not_only_an_index() -> None:
    supply = _supply()
    evidence = dict(supply["confirmation_evidence"])
    evidence.pop("closed_candle_key")
    evidence["closed_candle_index"] = 66
    supply["confirmation_evidence"] = evidence

    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[_demand(), supply])
    )

    assert _zones_by_intent(candidate, "ENTRY_STOP") == []
    assert {
        (row["source_key"], row["reason"])
        for row in candidate["rejected_sources"]
    } == {
        ("supply-7-9", "STOP_ENTRY_CONFIRMATION_CANDLE_ID_MISSING")
    }


def test_global_reprojection_needs_three_consistent_anchors_and_moves_one_plane() -> None:
    baseline = _session()["reprojection_anchors"]
    current = [
        {
            "anchor_id": row["anchor_id"],
            "x_norm": round(1.1 * row["x_norm"] + 0.01, 6),
            "y_norm": round(0.9 * row["y_norm"] + 0.02, 6),
        }
        for row in baseline
    ]

    transform = fit_order_positioning_reprojection_v3(baseline, current)
    projected = reproject_order_positioning_bounds_v3(
        [0.2, 0.3, 0.4, 0.5],
        transform,
    )

    assert transform["status"] == "PROVEN"
    assert transform["matched_anchor_count"] == 4
    assert projected == [0.23, 0.29, 0.45, 0.47]
    assert inverse_reproject_order_positioning_y_v3(0.47, transform) == 0.5

    current[-1]["y_norm"] = 0.50
    rejected = fit_order_positioning_reprojection_v3(baseline, current)
    assert rejected["status"] == "UNPROVEN"
    assert rejected["reason"] == "GLOBAL_TRANSFORM_RESIDUAL_TOO_HIGH"


def test_freeze_captures_baseline_geometry_without_mutating_candidate() -> None:
    candidate = build_order_positioning_candidates_v3(
        _session(overlays=[_demand()])
    )
    original = copy.deepcopy(candidate)

    plan = _freeze(candidate)

    assert candidate == original
    assert plan["status"] == "TRACKING"
    assert plan["frozen"] is True
    assert plan["baseline_closed_candle_key"] == "closed-0"
    assert plan["step"] == 0
    assert plan["horizon_steps"] == 12
    assert len(plan["geometry_fingerprint"]) == 64
    assert {zone["status"] for zone in plan["zones"]} == {
        "WAITING",
        "STANDBY",
    }


def test_advance_activates_then_marks_favorable_without_moving_any_box() -> None:
    plan = _freeze(
        build_order_positioning_candidates_v3(_session(overlays=[_demand()]))
    )
    original_bounds = {
        zone["zone_id"]: copy.deepcopy(zone["bounds"]) for zone in plan["zones"]
    }

    touched = advance_order_positioning_plan_v3(
        plan,
        _actual(1, high=0.54, low=0.60, close=0.56),
        1,
    )
    progressed = advance_order_positioning_plan_v3(
        touched,
        _actual(2, high=0.45, low=0.50, close=0.47),
        2,
    )

    entry = _zones_by_intent({"candidate_zones": touched["zones"]}, "ENTRY_LIMIT")[0]
    progressed_entry = _zones_by_intent(
        {"candidate_zones": progressed["zones"]}, "ENTRY_LIMIT"
    )[0]
    assert entry["status"] == "ACTIVATED"
    assert progressed_entry["status"] == "FAVORED"
    assert {
        zone["zone_id"]: zone["bounds"] for zone in progressed["zones"]
    } == original_bounds
    assert plan["step"] == 0


def test_protective_stop_failure_is_distinct_from_stop_entry_activation() -> None:
    plan = _freeze(
        build_order_positioning_candidates_v3(_session(overlays=[_demand()]))
    )
    touched = advance_order_positioning_plan_v3(
        plan,
        _actual(1, high=0.54, low=0.60, close=0.56),
        1,
    )
    stopped = advance_order_positioning_plan_v3(
        touched,
        _actual(2, high=0.62, low=0.65, close=0.645),
        2,
    )

    entry = _zones_by_intent(
        {"candidate_zones": stopped["zones"]}, "ENTRY_LIMIT"
    )[0]
    protection = _zones_by_intent(
        {"candidate_zones": stopped["zones"]}, "PROTECTIVE_STOP"
    )[0]
    assert entry["status"] == "FAILED"
    assert protection["status"] == "ACTIVATED"
    assert protection["order_kind"] == "SELL_STOP"

    still_stopped = advance_order_positioning_plan_v3(
        stopped,
        _actual(3, high=0.54, low=0.59, close=0.56),
        3,
    )
    later_entry = _zones_by_intent(
        {"candidate_zones": still_stopped["zones"]}, "ENTRY_LIMIT"
    )[0]
    later_protection = _zones_by_intent(
        {"candidate_zones": still_stopped["zones"]}, "PROTECTIVE_STOP"
    )[0]
    assert later_entry["status"] == "FAILED"
    assert later_protection["status"] == "ACTIVATED"


def test_advance_rejects_geometry_mutation_and_restores_frozen_bounds() -> None:
    plan = _freeze(
        build_order_positioning_candidates_v3(_session(overlays=[_demand()]))
    )
    original_bounds = copy.deepcopy(plan["zones"][0]["bounds"])
    tampered = copy.deepcopy(plan)
    tampered["zones"][0]["bounds"][1] += 0.1

    rejected = advance_order_positioning_plan_v3(tampered, _actual(1), 1)

    assert rejected["advance_status"] == "REJECTED"
    assert rejected["blockers"] == ["FROZEN_GEOMETRY_CHANGED"]
    assert rejected["zones"][0]["bounds"] == original_bounds
    assert tampered["zones"][0]["bounds"] != original_bounds


def test_advance_rejects_reprojection_anchor_mutation() -> None:
    plan = _freeze(
        build_order_positioning_candidates_v3(_session(overlays=[_demand()]))
    )
    tampered = copy.deepcopy(plan)
    tampered["reprojection_anchors"][0]["y_norm"] += 0.1

    rejected = advance_order_positioning_plan_v3(tampered, _actual(1), 1)

    assert rejected["advance_status"] == "REJECTED"
    assert rejected["blockers"] == ["FROZEN_REPROJECTION_ANCHORS_CHANGED"]


def test_new_sequence_and_transform_are_allowed_after_source_locked_reprojection() -> None:
    plan = _freeze(
        build_order_positioning_candidates_v3(_session(overlays=[_demand()]))
    )

    advanced = advance_order_positioning_plan_v3(
        plan,
        _actual(
            1,
            sequence_id="sequence-after-scroll",
            chart_transform_id="transform-after-rescale",
        ),
        1,
    )

    assert advanced["advance_status"] == "APPLIED"
    assert advanced["step"] == 1


def test_only_verified_closed_candles_can_advance_one_step_at_a_time() -> None:
    plan = _freeze(
        build_order_positioning_candidates_v3(_session(overlays=[_demand()]))
    )

    open_rejected = advance_order_positioning_plan_v3(
        plan, _actual(1, is_closed=False), 1
    )
    skipped_rejected = advance_order_positioning_plan_v3(plan, _actual(2), 2)

    assert open_rejected["advance_status"] == "REJECTED"
    assert open_rejected["blockers"] == ["ACTUAL_CANDLE_UNVERIFIED_OR_OPEN"]
    assert skipped_rejected["advance_status"] == "REJECTED"
    assert skipped_rejected["blockers"] == ["STEP_MUST_ADVANCE_BY_ONE"]
    assert plan["step"] == 0


def test_twelfth_closed_candle_completes_episode_and_enables_reset() -> None:
    plan = _freeze(
        build_order_positioning_candidates_v3(_session(overlays=[_demand()]))
    )
    for step in range(1, 13):
        plan = advance_order_positioning_plan_v3(plan, _actual(step), step)

    assert plan["status"] == "COMPLETE"
    assert plan["step"] == 12
    assert plan["reset_available"] is True
    assert {zone["status"] for zone in plan["zones"]} == {"EXPIRED"}
