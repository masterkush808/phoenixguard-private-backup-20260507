from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence, cast

import pytest

from phoenixguard.vision.v3_overlay_contract import (
    DIAGNOSTIC_OVERLAY_TYPES,
    HARD_ANCHOR_REQUIRED_TYPES,
    MODE_ALLOWED_TYPES,
    ORDER_POSITIONING_OVERLAY_TYPES,
    OVERLAY_LAYER_ORDER,
    OVERLAY_TYPES,
    REQUIRED_FIELDS,
    TYPE_LAYER_MAP,
    V3OverlayContractError,
    abbreviate_label,
    approved_overlay_display_labels,
    is_approved_overlay_display_label,
    layout_overlay_labels,
    normalize_bounds,
    normalize_overlay_display_label,
    normalize_overlay_type,
    normalize_v3_overlay_object,
    normalize_view_mode,
    overlay_is_visible,
    overlay_rejection_reasons,
    overlay_type_priority,
    prediction_overlay_config,
    prediction_overlay_enabled,
    rectangles_overlap,
    reason_if_empty,
    resolve_visible_overlays,
    validate_v3_overlay_object,
    view_mode_profile,
)


_REPO = Path(__file__).resolve().parents[2]


def _base_overlay(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "overlay_id": "sniper-1",
        "object_id": "obj-1",
        "track_id": "track-1",
        "type": "SNIPER_ENTRY_BOX",
        "side": "SELL",
        "source_agent": "model_council_v3",
        "layer": "trigger_zones",
        "frame_id": 42,
        "sequence_id": "seq-42",
        "chart_transform_id": "ct-42",
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "anchor_type": "BOX",
        "anchor_candles": [4, 5],
        "touch_points": [[148, 232], [208, 236]],
        "bounds": [140, 210, 220, 250],
        "truth_score": 0.83,
        "confidence": 0.91,
        "lifecycle_state": "ACTIVE",
        "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT", "PREDICTION", "INSPECTOR"],
        "ttl_ms": 9000,
        "reason": "tracked sell trigger retest",
        "label": "SELL AGGRO SNIPER",
    }
    payload.update(overrides)
    return payload


def test_contract_normalizes_complete_overlay_and_keeps_renderer_bbox_alias() -> None:
    overlay = normalize_v3_overlay_object(_base_overlay(bounds=[220, 250, 140, 210]))

    assert overlay["schema_version"] == "PG_V3_OVERLAY_OBJECT_V1"
    assert set(REQUIRED_FIELDS).issubset(overlay)
    assert overlay["type"] == "SNIPER_ENTRY_BOX"
    assert overlay["side"] == "SELL"
    assert overlay["bounds"] == [140.0, 210.0, 220.0, 250.0]
    assert overlay["bbox"] == overlay["bounds"]
    assert overlay["layer"] == "trigger_zones"
    assert overlay["truth_score"] == 0.83
    assert overlay["confidence"] == 0.91
    assert overlay["source_version"] == "PG_V3_OVERLAY_OBJECT_V1"
    assert overlay["broker_source_lock_id"]
    assert overlay["anchor_candles"] == [4, 5]
    assert overlay["anchor_candle_indices"] == [4, 5]
    assert overlay["anchor_evidence_status"] == "VALID"
    assert validate_v3_overlay_object(overlay).ok is True


def test_contract_sanitizes_historical_progression_edge_spikes() -> None:
    overlay = normalize_v3_overlay_object(
        _base_overlay(
            type="PROGRESSION_PATH",
            side="SELL",
            layer="historical_replay",
            lifecycle_state="HISTORICAL",
            label="HISTORICAL PROGRESSION",
            bounds=[221, 7, 435, 668],
            path=[
                [221, 7],
                [252, 505],
                [291, 530],
                [394, 668],
                [415, 597],
                [435, 7],
            ],
            anchor_candles=list(range(7, 18)),
            source_indices=list(range(7, 18)),
        )
    )

    points = cast(Sequence[Sequence[float]], overlay["line_points"])

    assert points[0] == [252.0, 505.0]
    assert points[-1] == [415.0, 597.0]
    bounds = cast(Sequence[float], overlay["bounds"])

    assert all(point[1] != 7.0 for point in points)
    assert bounds[1] > 400.0


def test_live_modes_reject_skinny_vertical_spike_overlays() -> None:
    overlay = _base_overlay(
        overlay_id="bad-spike-1",
        object_id="bad-spike-1",
        track_id="bad-spike-1",
        type="PROGRESSION_PATH",
        side="SELL",
        layer="historical_replay",
        label="HISTORICAL PROGRESSION",
        bounds=[320, 8, 332, 650],
        anchor_candles=list(range(10, 18)),
        source_indices=list(range(10, 18)),
        visible_modes=["REPLAY", "FULL_HISTORY_READ", "ACTIVE_CONTEXT", "INSPECTOR"],
    )

    reasons = overlay_rejection_reasons(overlay, "REPLAY")

    assert any(reason.startswith("geometry_spike_vertical") for reason in reasons)
    assert overlay_is_visible(overlay, "REPLAY") is False


def test_live_modes_reject_full_surface_lines_crossing_broker_chrome() -> None:
    overlay = _base_overlay(
        overlay_id="bad-header-line-1",
        object_id="bad-header-line-1",
        track_id="bad-header-line-1",
        type="RESISTANCE_TRENDLINE",
        side="SELL",
        layer="trendlines",
        label="RESISTANCE TRENDLINE",
        coordinate_mode="FULL_BROKER_SURFACE",
        bounds=[820, 48, 1040, 430],
        line_points=[[820, 48], [1040, 430]],
        touch_points=[[820, 48], [1040, 430]],
        anchor_candles=[2, 8],
        anchor_candle_indices=[2, 8],
        visible_modes=["CLEAN_LIVE", "TRENDLINES", "ACTIVE_CONTEXT", "INSPECTOR"],
    )

    reasons = overlay_rejection_reasons(overlay, "CLEAN_LIVE")

    assert any(reason.startswith("geometry_crosses_broker_chrome") for reason in reasons)
    assert overlay_is_visible(overlay, "CLEAN_LIVE") is False


def test_contract_normalizes_professional_required_fields_and_aliases() -> None:
    overlay = normalize_v3_overlay_object(
        _base_overlay(
            source_version="tracker-v3.2",
            broker_source_lock_id="broker-lock-42",
            anchor_type="candle_range",
            anchor_candles=["4", {"candle_index": 7}, "4", -1, "bad"],
            layer="trigger",
            visible_modes=[
                "chart-bounds",
                "candles",
                "major/global",
                "local",
                "supply",
                "invalidation",
                "full-history",
                "broker-controls",
                "deep-debug",
            ],
        )
    )

    assert set(REQUIRED_FIELDS).issubset(overlay)
    assert overlay["source_version"] == "tracker-v3.2"
    assert overlay["broker_source_lock_id"] == "broker-lock-42"
    assert overlay["anchor_type"] == "CANDLES"
    assert overlay["anchor_candles"] == [4, 7]
    assert overlay["layer"] == "trigger_zones"
    assert overlay["visible_modes"] == [
        "CHART_BOUNDS",
        "CANDLES",
        "GLOBAL",
        "LOCAL",
        "SUPPLY_DEMAND",
        "INVALIDATION",
        "FULL_HISTORY_READ",
        "BROKER",
        "DIAGNOSTICS",
    ]
    assert validate_v3_overlay_object(overlay).ok is True


def test_legacy_registry_overlay_types_stay_renderable_in_active_context() -> None:
    expected = {
        "CHART_BOUNDS": ("CHART_BOUNDS", "chart_bounds"),
        "RECENT_CANDLE": ("CURRENT_CANDLE", "recent_candles"),
        "MAJOR_SWINGS": ("IMPULSE_BOX", "major_swings"),
        "LOCAL_SWINGS": ("PULLBACK_BOX", "local_swings"),
        "SNIPER": ("SNIPER_ENTRY_BOX", "trigger_zones"),
        "PRIMARY": ("RETEST_BOX", "trigger_zones"),
        "TARGET": ("TARGET_ZONE_BOX", "target_zones"),
        "SUPPORT": ("DEMAND_ZONE", "supply_demand"),
        "RESISTANCE": ("SUPPLY_ZONE", "supply_demand"),
        "HISTORICAL_REPLAY": ("PROGRESSION_PATH", "historical_replay"),
    }

    for legacy_type, (normalized_type, layer) in expected.items():
        overlay = normalize_v3_overlay_object(
            _base_overlay(
                overlay_id=f"legacy-{legacy_type.lower()}",
                type=legacy_type,
                layer=layer,
                visible_modes=["ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
            ),
            strict=False,
        )
        assert overlay["type"] == normalized_type
        assert overlay["layer"] == layer
        assert overlay_is_visible(overlay, "ACTIVE_CONTEXT") is True


def test_full_overlay_vocabulary_aliases_normalize_to_canonical_types() -> None:
    expected = {
        # Chart and candle structure.
        "CHART_BOUNDS": "CHART_BOUNDS",
        "CURRENT_CANDLE": "CURRENT_CANDLE",
        "CANDLE_BOX": "CURRENT_CANDLE",
        "CANDLE_GROUP": "CURRENT_CANDLE",
        "TRACKED_CANDLES": "CURRENT_CANDLE",
        "CURRENT_BOX": "CURRENT_CANDLE",
        "CANDLE": "CURRENT_CANDLE",
        "RECENT_CANDLE": "CURRENT_CANDLE",
        "NOW": "CURRENT_CANDLE",
        "IMPULSE_BOX": "IMPULSE_BOX",
        "IMPULSE": "IMPULSE_BOX",
        "MAJOR_SWING": "IMPULSE_BOX",
        "GLOBAL_SWING": "IMPULSE_BOX",
        "PULLBACK_BOX": "PULLBACK_BOX",
        "PULLBACK": "PULLBACK_BOX",
        "LOCAL_SWING": "PULLBACK_BOX",
        "MINOR_SWING": "PULLBACK_BOX",
        "RETEST_BOX": "RETEST_BOX",
        "CONTINUATION_BOX": "CONTINUATION_BOX",
        "CONTINUATION": "CONTINUATION_BOX",
        "STRUCTURE_BOX": "CONTINUATION_BOX",
        "HISTORICAL_STRUCTURE": "PROGRESSION_PATH",

        # Entry, target, and invalidation aliases.
        "TRIGGER": "RETEST_BOX",
        "TRIGGER_ZONE": "RETEST_BOX",
        "CONSERVATIVE_TRIGGER": "RETEST_BOX",
        "RETEST": "RETEST_BOX",
        "RETEST_AREA": "RETEST_BOX",
        "SNIPER": "SNIPER_ENTRY_BOX",
        "SNIPER_ENTRY": "SNIPER_ENTRY_BOX",
        "SNIPER_ENTRY_BOX": "SNIPER_ENTRY_BOX",
        "SNIPER_BUY": "SNIPER_ENTRY_BOX",
        "SNIPER_SELL": "SNIPER_ENTRY_BOX",
        "ENTRY_AREA_ZONE": "SNIPER_ENTRY_BOX",
        "ENTRY_LEVEL": "SNIPER_ENTRY_BOX",
        "BUY_LIMIT_ZONE": "BUY_LIMIT_ZONE",
        "BUY_LIMIT": "BUY_LIMIT_ZONE",
        "BUY_LIMIT_AREA": "BUY_LIMIT_ZONE",
        "BUY_LIMIT_ORDER_ZONE": "BUY_LIMIT_ZONE",
        "SELL_LIMIT_ZONE": "SELL_LIMIT_ZONE",
        "SELL_LIMIT": "SELL_LIMIT_ZONE",
        "SELL_LIMIT_AREA": "SELL_LIMIT_ZONE",
        "SELL_LIMIT_ORDER_ZONE": "SELL_LIMIT_ZONE",
        "BUY_STOP_ENTRY_ZONE": "BUY_STOP_ENTRY_ZONE",
        "BUY_STOP": "BUY_STOP_ENTRY_ZONE",
        "BUY_STOP_ZONE": "BUY_STOP_ENTRY_ZONE",
        "BUY_STOP_ORDER_ZONE": "BUY_STOP_ENTRY_ZONE",
        "SELL_STOP_ENTRY_ZONE": "SELL_STOP_ENTRY_ZONE",
        "SELL_STOP": "SELL_STOP_ENTRY_ZONE",
        "SELL_STOP_ZONE": "SELL_STOP_ENTRY_ZONE",
        "SELL_STOP_ORDER_ZONE": "SELL_STOP_ENTRY_ZONE",
        "PROTECTIVE_STOP_ZONE": "PROTECTIVE_STOP_ZONE",
        "PROTECTIVE_STOP": "PROTECTIVE_STOP_ZONE",
        "PROTECTIVE_INVALIDATION_ZONE": "PROTECTIVE_STOP_ZONE",
        "BUY_PROTECTIVE_STOP_ZONE": "PROTECTIVE_STOP_ZONE",
        "SELL_PROTECTIVE_STOP_ZONE": "PROTECTIVE_STOP_ZONE",
        "STOP_LOSS_ZONE": "PROTECTIVE_STOP_ZONE",
        "TARGET_ZONE_BOX": "TARGET_ZONE_BOX",
        "TARGET": "TARGET_ZONE_BOX",
        "TARGET_ZONE": "TARGET_ZONE_BOX",
        "TARGET_LEVEL": "TARGET_ZONE_BOX",
        "INVALIDATION_BOX": "INVALIDATION_BOX",
        "INVALIDATION": "INVALIDATION_BOX",
        "INVALIDATION_ZONE": "INVALIDATION_BOX",
        "RISK_LIMIT": "INVALIDATION_BOX",

        # Landscape and reaction areas.
        "SUPPLY_ZONE": "SUPPLY_ZONE",
        "SUPPLY": "SUPPLY_ZONE",
        "SUPPLY_AREA": "SUPPLY_ZONE",
        "RESISTANCE": "SUPPLY_ZONE",
        "RESISTANCE_ZONE": "SUPPLY_ZONE",
        "LIVE_RESISTANCE": "SUPPLY_ZONE",
        "NEAREST_RESISTANCE": "SUPPLY_ZONE",
        "DEMAND_ZONE": "DEMAND_ZONE",
        "DEMAND": "DEMAND_ZONE",
        "DEMAND_AREA": "DEMAND_ZONE",
        "SUPPORT": "DEMAND_ZONE",
        "SUPPORT_ZONE": "DEMAND_ZONE",
        "LIVE_SUPPORT": "DEMAND_ZONE",
        "NEAREST_SUPPORT": "DEMAND_ZONE",
        "OPPOSING_FORCE": "OPPOSING_FORCE",
        "OPPOSING_FORCE_ZONE": "OPPOSING_FORCE",
        "SUPPORT_RESISTANCE_ZONE": "OPPOSING_FORCE",

        # Directional structure.
        "SUPPORT_TRENDLINE": "SUPPORT_TRENDLINE",
        "SUPPORT_LINE": "SUPPORT_TRENDLINE",
        "SUPPORT_TREND": "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE": "RESISTANCE_TRENDLINE",
        "RESISTANCE_LINE": "RESISTANCE_TRENDLINE",
        "RESISTANCE_TREND": "RESISTANCE_TRENDLINE",
        "INNER_TRENDLINE": "INNER_TRENDLINE",
        "INNER_LINE": "INNER_TRENDLINE",
        "INNER_TREND": "INNER_TRENDLINE",
        "TRENDLINE": "INNER_TRENDLINE",
        "ANGLE_VECTOR": "ANGLE_VECTOR",

        # Projection, replay, and memory aliases.
        "PROGRESSION_PATH": "PROGRESSION_PATH",
        "PATH": "PROGRESSION_PATH",
        "HISTORICAL_PROGRESSION": "PROGRESSION_PATH",
        "HISTORICAL_REPLAY": "PROGRESSION_PATH",
        "MEMORY_MATCH": "PROGRESSION_PATH",
        "PREDICTION_PATH": "PREDICTION_PATH",
        "PROJECTED_CANDLES": "PREDICTION_PATH",
        "FORWARD_PROJECTION": "PREDICTION_PATH",
        "REPLAY_ENTRY": "REPLAY_ENTRY",
        "WOULD_HAVE_ENTERED": "REPLAY_ENTRY",
        "REPLAY_EXIT": "REPLAY_EXIT",
        "WOULD_HAVE_EXITED": "REPLAY_EXIT",

        # Market-context concepts remain canonical internally.
        "ORDER_BLOCK": "ORDER_BLOCK",
        "ORDER_BLOCK_RETEST": "ORDER_BLOCK",
        "FAIR_VALUE_GAP": "FAIR_VALUE_GAP",
        "FVG": "FAIR_VALUE_GAP",
        "LIQUIDITY_POOL": "LIQUIDITY_POOL",
        "SMC_LIQUIDITY_POOL": "LIQUIDITY_POOL",
        "LIQUIDITY_SWEEP": "LIQUIDITY_SWEEP",
        "MARKET_STRUCTURE_SHIFT": "MARKET_STRUCTURE_SHIFT",
        "MSS": "MARKET_STRUCTURE_SHIFT",
        "BREAK_OF_STRUCTURE": "MARKET_STRUCTURE_SHIFT",
        "BOS": "MARKET_STRUCTURE_SHIFT",

        # Explanatory studies and decision markers.
        "MODEL_COUNCIL_MARKER": "MODEL_COUNCIL_MARKER",
        "SMC_COUNCIL": "MODEL_COUNCIL_MARKER",
        "REGIME_MARKER": "REGIME_MARKER",
        "MARKET_PLAY_MARKER": "MARKET_PLAY_MARKER",
        "PLAYBOOK_MARKER": "MARKET_PLAY_MARKER",
        "THESIS_MARKER": "MARKET_PLAY_MARKER",
        "OPPORTUNITY_MATURITY_MARKER": "MARKET_PLAY_MARKER",
        "SUPPORT_RECLAIM": "MARKET_PLAY_MARKER",
        "RESISTANCE_REJECTION": "MARKET_PLAY_MARKER",
        "RECLAIM_AFTER_SWEEP": "MARKET_PLAY_MARKER",
        "PRICE_LOCATION_MARKER": "PRICE_LOCATION_MARKER",
        "TWO_CANDLE_STUDY": "TWO_CANDLE_STUDY",
        "LSTM_STUDY": "LSTM_STUDY",

        # Broker and diagnostic plane.
        "BROKER_CONTROL": "BROKER_CONTROL",
        "DEBUG_RAW_DETECTION": "DEBUG_RAW_DETECTION",
        "REJECTED_OVERLAY": "REJECTED_OVERLAY",
        "STALE_OVERLAY": "STALE_OVERLAY",
        "TRANSFORM_DEBUG": "TRANSFORM_DEBUG",
        "SCENE_GRAPH_DEBUG": "SCENE_GRAPH_DEBUG",
        "LABEL_COLLISION_DEBUG": "LABEL_COLLISION_DEBUG",
        "CHART_TRANSFORM_DEBUG": "TRANSFORM_DEBUG",
        "SOURCE_LOCK_DEBUG": "DEBUG_RAW_DETECTION",
        "ANCHOR_DEBUG": "DEBUG_RAW_DETECTION",
        "CANDLE_ANCHOR_DEBUG": "DEBUG_RAW_DETECTION",
        "BOX_REFINEMENT_DEBUG": "DEBUG_RAW_DETECTION",
    }

    assert {alias: normalize_overlay_type(alias) for alias in expected} == expected
    assert set(expected.values()).issubset(set(OVERLAY_TYPES))
    assert all(TYPE_LAYER_MAP[overlay_type] for overlay_type in set(expected.values()))

    live_safe_types = set(expected.values()) - DIAGNOSTIC_OVERLAY_TYPES - {
        "BROKER_CONTROL",
        "PREDICTION_PATH",
    }
    live_modes = {
        mode: allowed
        for mode, allowed in MODE_ALLOWED_TYPES.items()
        if mode not in {"BROKER", "CALIBRATION", "DIAGNOSTICS", "DEBUG", "INSPECTOR"}
    }
    assert all(
        any(overlay_type in allowed for allowed in live_modes.values())
        for overlay_type in live_safe_types
    )


@pytest.mark.parametrize(
    (
        "overlay_type",
        "raw_side",
        "expected_side",
        "expected_thesis_side",
        "expected_kind",
        "expected_intent",
        "expected_role",
        "expected_label",
        "expected_evidence",
        "expected_color_token",
        "expected_line_style",
    ),
    [
        (
            "BUY_LIMIT_ZONE",
            "SELL",
            "BUY",
            "BUY",
            "BUY_LIMIT",
            "ENTRY_LIMIT",
            "buy_limit",
            "BUY LIMIT",
            "limit_order_area",
            "buy-limit-position",
            "solid",
        ),
        (
            "SELL_LIMIT_ZONE",
            "BUY",
            "SELL",
            "SELL",
            "SELL_LIMIT",
            "ENTRY_LIMIT",
            "sell_limit",
            "SELL LIMIT",
            "limit_order_area",
            "sell-limit-position",
            "solid",
        ),
        (
            "BUY_STOP_ENTRY_ZONE",
            "SELL",
            "BUY",
            "BUY",
            "BUY_STOP",
            "ENTRY_STOP",
            "buy_stop_entry",
            "BUY STOP ENTRY",
            "stop_entry_area",
            "buy-stop-entry-position",
            "dashed",
        ),
        (
            "SELL_STOP_ENTRY_ZONE",
            "BUY",
            "SELL",
            "SELL",
            "SELL_STOP",
            "ENTRY_STOP",
            "sell_stop_entry",
            "SELL STOP ENTRY",
            "stop_entry_area",
            "sell-stop-entry-position",
            "dashed",
        ),
        (
            "PROTECTIVE_STOP_ZONE",
            "SELL",
            "SELL",
            "BUY",
            "SELL_STOP",
            "PROTECTIVE_STOP",
            "protective_stop",
            "PROTECTIVE STOP",
            "protective_stop_band",
            "protective-stop",
            "dashed",
        ),
    ],
)
def test_order_positioning_types_are_live_safe_semantic_boxes(
    overlay_type: str,
    raw_side: str,
    expected_side: str,
    expected_thesis_side: str,
    expected_kind: str,
    expected_intent: str,
    expected_role: str,
    expected_label: str,
    expected_evidence: str,
    expected_color_token: str,
    expected_line_style: str,
) -> None:
    overlay = normalize_v3_overlay_object(
        _base_overlay(
            overlay_id=f"order-position-{overlay_type.lower()}",
            object_id=f"order-position-{overlay_type.lower()}",
            track_id=f"order-position-{overlay_type.lower()}",
            type=overlay_type,
            side=raw_side,
            layer="trigger_zones",
            role=overlay_type.lower(),
            label=overlay_type,
            visible_modes=["CLEAN_LIVE", "ORDER_POSITIONING", "ACTIVE_CONTEXT"],
            thesis_side=expected_thesis_side,
            order_kind="CONTRADICTORY_INPUT",
            confirmation_state="CONFIRMED_CLOSED",
            confirmation_event="BREAK_OF_STRUCTURE",
            confirmation_side=expected_thesis_side,
            confirmation_closed_candle_index=5,
            trade_authorization_status="AUTHORIZED",
            entry_authority_active=True,
            order_authority_active=True,
        ),
        strict=False,
    )

    assert overlay["type"] == overlay_type
    assert overlay["side"] == expected_side
    assert overlay["thesis_side"] == expected_thesis_side
    assert overlay["layer"] == "order_positioning"
    assert overlay["role"] == expected_role
    assert overlay["display_label"] == expected_label
    assert overlay["order_kind"] == expected_kind
    assert overlay["intent"] == expected_intent
    assert overlay["trade_authorization_status"] == "EVIDENCE_ONLY"
    assert overlay["entry_authority_active"] is False
    assert overlay["order_authority_active"] is False
    assert overlay["evidence_only"] is True
    assert cast(dict[str, object], overlay["anchor_evidence"])["evidence_type"] == expected_evidence
    assert cast(dict[str, object], overlay["style"])["color_token"] == expected_color_token
    assert cast(dict[str, object], overlay["style"])["line_style"] == expected_line_style
    assert overlay_type in HARD_ANCHOR_REQUIRED_TYPES
    assert overlay_type_priority(overlay_type) >= 96
    assert overlay_is_visible(overlay, "CLEAN_LIVE") is True
    assert overlay_is_visible(overlay, "ORDER_POSITIONING") is True
    assert validate_v3_overlay_object(overlay).ok is True


def test_order_positioning_mode_and_layer_are_first_class() -> None:
    profile = view_mode_profile("limits-and-stops")

    assert normalize_view_mode("order positions") == "ORDER_POSITIONING"
    assert profile["mode"] == "ORDER_POSITIONING"
    assert set(profile["allowed_types"]) == set(ORDER_POSITIONING_OVERLAY_TYPES)
    assert profile["layer_visibility"]["order_positioning"] is True
    assert all(
        not visible
        for layer, visible in profile["layer_visibility"].items()
        if layer != "order_positioning"
    )
    assert OVERLAY_LAYER_ORDER.index("trendlines") < OVERLAY_LAYER_ORDER.index(
        "order_positioning"
    ) < OVERLAY_LAYER_ORDER.index("trigger_zones")


def test_order_positioning_live_modes_require_a_wick_anchor() -> None:
    overlay = normalize_v3_overlay_object(
        _base_overlay(
            overlay_id="buy-limit-without-wick",
            object_id="buy-limit-without-wick",
            track_id="buy-limit-without-wick",
            type="BUY_LIMIT_ZONE",
            side="BUY",
            layer="order_positioning",
            label="BUY LIMIT",
            touch_points=[],
            anchor_wick_points=[],
            anchor_evidence={
                "evidence_type": "limit_order_area",
                "valid": True,
                "candle_indices": [4, 5],
                "touch_points": [],
            },
            visible_modes=["CLEAN_LIVE", "ORDER_POSITIONING"],
        ),
        strict=False,
    )

    assert "missing_order_position_wick_anchor" in overlay_rejection_reasons(
        overlay,
        "ORDER_POSITIONING",
    )
    assert overlay_is_visible(overlay, "ORDER_POSITIONING") is False


@pytest.mark.parametrize(
    "alias",
    [
        "DEBUG_RAW_DETECTION",
        "REJECTED_OVERLAY",
        "STALE_OVERLAY",
        "TRANSFORM_DEBUG",
        "SCENE_GRAPH_DEBUG",
        "LABEL_COLLISION_DEBUG",
        "CHART_TRANSFORM_DEBUG",
        "SOURCE_LOCK_DEBUG",
        "ANCHOR_DEBUG",
        "CANDLE_ANCHOR_DEBUG",
        "BOX_REFINEMENT_DEBUG",
    ],
)
def test_diagnostic_vocabulary_aliases_remain_diagnostics_only(alias: str) -> None:
    overlay = normalize_v3_overlay_object(
        _base_overlay(
            overlay_id=f"diagnostic-{alias.lower()}",
            type=alias,
            layer="diagnostics",
            label=alias,
            visible_modes=["CLEAN_LIVE", "DIAGNOSTICS", "INSPECTOR"],
        ),
        strict=False,
    )

    assert overlay["type"] in DIAGNOSTIC_OVERLAY_TYPES
    assert overlay["layer"] == "diagnostics"
    assert overlay_is_visible(overlay, "CLEAN_LIVE") is False
    assert overlay_is_visible(overlay, "DIAGNOSTICS") is True


def test_retest_invalidation_and_angle_are_visible_in_their_live_modes() -> None:
    retest = _base_overlay(
        overlay_id="retest-live",
        type="RETEST_BOX",
        layer="trigger_zones",
        visible_modes=["CLEAN_LIVE", "TRIGGER", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "PREDICTION"],
    )
    invalidation = _base_overlay(
        overlay_id="invalidation-live",
        type="INVALIDATION_BOX",
        layer="invalidation",
        visible_modes=[
            "CLEAN_LIVE",
            "INVALIDATION",
            "ACTIVE_CONTEXT",
            "FULL_HISTORY_READ",
            "REPLAY",
            "PREDICTION",
        ],
    )
    angle = _base_overlay(
        overlay_id="angle-live",
        type="ANGLE_VECTOR",
        layer="prediction_path",
        anchor_type="LINE",
        bounds=[120, 180, 420, 320],
        touch_points=[[120, 320], [420, 180]],
        visible_modes=["PATH", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "PREDICTION"],
    )

    assert all(overlay_is_visible(retest, mode) for mode in retest["visible_modes"])
    assert all(overlay_is_visible(invalidation, mode) for mode in invalidation["visible_modes"])
    assert all(overlay_is_visible(angle, mode) for mode in angle["visible_modes"])


def test_projection_aliases_remain_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", raising=False)

    for alias in ("PROJECTED_CANDLES", "FORWARD_PROJECTION"):
        projection = _base_overlay(
            overlay_id=f"projection-{alias.lower()}",
            type=alias,
            layer="prediction_path",
            anchor_type="POLYGON",
            visible_modes=["PATH", "PREDICTION", "DIAGNOSTICS", "INSPECTOR"],
        )
        assert normalize_overlay_type(alias) == "PREDICTION_PATH"
        assert overlay_is_visible(projection, "PATH") is False
        assert overlay_is_visible(projection, "PREDICTION") is False
        assert "prediction_overlay_disabled" in overlay_rejection_reasons(projection, "PREDICTION")

    monkeypatch.setenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", "1")
    diagnostic_projection = _base_overlay(
        overlay_id="projection-diagnostics-only",
        type="PREDICTION_PATH",
        layer="prediction_path",
        anchor_type="POLYGON",
        visible_modes=["PREDICTION", "DIAGNOSTICS", "INSPECTOR"],
    )
    assert overlay_is_visible(diagnostic_projection, "DIAGNOSTICS") is True
    assert overlay_is_visible(diagnostic_projection, "PREDICTION") is False


def test_visible_labels_are_locked_to_approved_dictionary() -> None:
    assert "NOW" in approved_overlay_display_labels()
    assert is_approved_overlay_display_label("SNIPER SELL") is True
    assert is_approved_overlay_display_label("SUPPORT TRENDLINE") is True
    assert is_approved_overlay_display_label("RESISTANCE TRENDLINE") is True
    assert is_approved_overlay_display_label("INNER TRENDLINE") is True
    assert is_approved_overlay_display_label("BUY LIMIT") is True
    assert is_approved_overlay_display_label("SELL LIMIT") is True
    assert is_approved_overlay_display_label("BUY STOP ENTRY") is True
    assert is_approved_overlay_display_label("SELL STOP ENTRY") is True
    assert is_approved_overlay_display_label("PROTECTIVE STOP") is True
    assert is_approved_overlay_display_label("SNIPER ENTRY BOX") is False

    sniper = normalize_v3_overlay_object(
        _base_overlay(label="SNIPER ENTRY BOX", display_label="SNIPER ENTRY BOX"),
        strict=False,
    )
    target = normalize_v3_overlay_object(
        _base_overlay(type="TARGET_ZONE_BOX", label="TARGET ZONE BOX", display_label="TARGET ZONE BOX"),
        strict=False,
    )
    continuation = normalize_v3_overlay_object(
        _base_overlay(type="CONTINUATION_BOX", label="CONT", display_label="CONT"),
        strict=False,
    )

    assert sniper["display_label"] == "SNIPER SELL"
    assert sniper["display_label_status"] == "remapped"
    assert target["display_label"] == "TARGET"
    assert continuation["display_label"] == "CONTINUATION"
    assert all(is_approved_overlay_display_label(row["display_label"]) for row in (sniper, target, continuation))


def test_visual_dictionary_artifact_covers_runtime_approved_labels() -> None:
    dictionary_path = _REPO / "docs" / "phoenixguard_v3_visual_dictionary.json"
    guide_path = _REPO / "docs" / "phoenixguard_v3_operator_view_guide.pdf"
    dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))

    assert dictionary["schema_version"] == "PG_V3_VISUAL_DICTIONARY_V1"
    assert set(approved_overlay_display_labels()).issubset(set(dictionary["approved_labels"]))
    assert guide_path.exists()


def test_market_knowledge_dictionary_is_linked_without_becoming_label_authority() -> None:
    visual_dictionary_path = _REPO / "docs" / "phoenixguard_v3_visual_dictionary.json"
    visual_dictionary = json.loads(visual_dictionary_path.read_text(encoding="utf-8"))
    knowledge_path = _REPO / visual_dictionary["knowledge_dictionary"]
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    candlestick_path = _REPO / knowledge["candlestick_glossary"]
    candlesticks = json.loads(candlestick_path.read_text(encoding="utf-8"))

    assert knowledge["schema_version"] == "PG_V3_MARKET_KNOWLEDGE_DICTIONARY_V1"
    assert knowledge["authority_rules"]["visible_labels"].startswith("Operator-visible overlay labels")
    assert knowledge["concept_aliases"]["BMS"][0] == "market_structure_shift"
    assert "zone_family" in knowledge["support_resistance"]["zone_metadata_fields"]
    assert knowledge["support_resistance"]["visual_boundary"].startswith("Horizontal areas render")
    assert "trendline_scope" in knowledge["trendlines"]["validity_fields"]
    assert "no price obstruction" in " ".join(knowledge["trendlines"]["book_rules"])
    assert "morphology_score" in knowledge["candlestick_filters"]["score_shape"]
    assert candlesticks["schema_version"] == "PG_V3_CANDLESTICK_GLOSSARY_V1"
    assert "bullish_engulfing" in candlesticks["double_candle_patterns"]["reversal"]
    assert set(knowledge["concept_aliases"]).isdisjoint(set(visual_dictionary["approved_labels"]))


def test_horizontal_zones_keep_supply_demand_labels_not_trendline_labels() -> None:
    demand = normalize_v3_overlay_object(
        _base_overlay(
            type="DEMAND_ZONE",
            layer="supply_demand",
            label="NEAREST SUPPORT 4T",
            display_label="NEAREST SUPPORT 4T",
            visible_modes=["SUPPLY_DEMAND", "ACTIVE_CONTEXT"],
        ),
        strict=False,
    )
    supply = normalize_v3_overlay_object(
        _base_overlay(
            type="SUPPLY_ZONE",
            side="SELL",
            layer="supply_demand",
            label="NEAREST RESISTANCE 5T",
            display_label="NEAREST RESISTANCE 5T",
            visible_modes=["SUPPLY_DEMAND", "ACTIVE_CONTEXT"],
        ),
        strict=False,
    )

    assert demand["display_label"] == "DEMAND"
    assert supply["display_label"] == "SUPPLY"
    assert demand["type"] == "DEMAND_ZONE"
    assert supply["type"] == "SUPPLY_ZONE"


def test_unmapped_display_terms_are_diagnostics_only() -> None:
    diagnostic = normalize_v3_overlay_object(
        _base_overlay(
            type="UNKNOWN_EXPERIMENTAL_BOX",
            label="mystery leftover label",
            display_label="mystery leftover label",
            visible_modes=["CLEAN_LIVE", "DIAGNOSTICS"],
        ),
        strict=False,
    )

    assert diagnostic["type"] == "DEBUG_RAW_DETECTION"
    assert diagnostic["display_label"] == "DEBUG RAW DETECTION"
    assert diagnostic["display_label_status"] == "unmapped"
    assert diagnostic["unmapped_display_label"] == "mystery leftover label"
    assert overlay_is_visible(diagnostic, "CLEAN_LIVE") is False
    assert overlay_is_visible(diagnostic, "DIAGNOSTICS") is True


def test_normalize_overlay_display_label_maps_leftover_short_tokens() -> None:
    assert normalize_overlay_display_label("NOW", "CURRENT_CANDLE", "HOLD") == ("NOW", "approved", "")
    assert normalize_overlay_display_label("T", "RETEST_BOX", "SELL") == ("TRIGGER", "remapped", "T")
    assert normalize_overlay_display_label("P", "PROGRESSION_PATH", "SELL") == ("PATH", "remapped", "P")


def test_view_mode_aliases_cover_overlay_buttons_and_backend_modes() -> None:
    cases = {
        "chart-bounds": "CHART_BOUNDS",
        "candles": "CANDLES",
        "major": "GLOBAL",
        "major/global": "GLOBAL",
        "local": "LOCAL",
        "supply-demand": "SUPPLY_DEMAND",
        "trendlines": "TRENDLINES",
        "trigger": "TRIGGER",
        "target": "TARGET",
        "invalidation": "INVALIDATION",
        "path": "PATH",
        "council": "COUNCIL",
        "smc": "SMART_MONEY",
        "smc-council": "SMART_MONEY",
        "smart-money-council": "SMART_MONEY",
        "two-candle-study": "TWO_CANDLE_STUDY",
        "next-two-candles": "TWO_CANDLE_STUDY",
        "lstm-study": "LSTM_STUDY",
        "full-history-read": "FULL_HISTORY_READ",
        "replay": "REPLAY",
        "broker-controls": "BROKER",
        "diagnostics": "DIAGNOSTICS",
    }

    assert {raw: normalize_view_mode(raw) for raw in cases} == cases
    assert view_mode_profile("chart-bounds")["layer_visibility"]["chart_bounds"] is True
    assert view_mode_profile("candles")["layer_visibility"]["recent_candles"] is True
    assert view_mode_profile("invalidation")["layer_visibility"]["invalidation"] is True
    trend_profile = view_mode_profile("trendlines")
    assert trend_profile["mode"] == "TRENDLINES"
    assert trend_profile["layer_visibility"]["trendlines"] is True
    assert set(trend_profile["allowed_types"]) == {"INNER_TRENDLINE", "RESISTANCE_TRENDLINE", "SUPPORT_TRENDLINE"}
    active_profile = view_mode_profile("active-context")
    assert active_profile["layer_visibility"]["historical_replay"] is True
    assert "PROGRESSION_PATH" in active_profile["allowed_types"]
    replay_profile = view_mode_profile("replay")
    assert "SNIPER_ENTRY_BOX" in replay_profile["allowed_types"]
    assert "TARGET_ZONE_BOX" in replay_profile["allowed_types"]
    assert "CURRENT_CANDLE" not in replay_profile["allowed_types"]
    assert "RETEST_BOX" in replay_profile["allowed_types"]
    assert "INVALIDATION_BOX" in replay_profile["allowed_types"]
    assert "ANGLE_VECTOR" in replay_profile["allowed_types"]
    full_history_profile = view_mode_profile("full-history-read")
    assert "CURRENT_CANDLE" not in full_history_profile["allowed_types"]
    assert "RETEST_BOX" in full_history_profile["allowed_types"]
    assert "INVALIDATION_BOX" in full_history_profile["allowed_types"]
    assert "ANGLE_VECTOR" in full_history_profile["allowed_types"]
    assert full_history_profile["layer_visibility"]["recent_candles"] is False
    assert replay_profile["layer_visibility"]["trigger_zones"] is True
    assert replay_profile["layer_visibility"]["target_zones"] is True
    assert replay_profile["layer_visibility"]["recent_candles"] is False
    assert replay_profile["layer_visibility"]["invalidation"] is True
    assert replay_profile["layer_visibility"]["prediction_path"] is True


def test_smart_money_mode_is_canonical_and_separate_from_model_council() -> None:
    profile = view_mode_profile("smc")
    smart_money_types = {
        "ORDER_BLOCK",
        "FAIR_VALUE_GAP",
        "LIQUIDITY_POOL",
        "LIQUIDITY_SWEEP",
        "MARKET_STRUCTURE_SHIFT",
    }
    order_block = _base_overlay(
        type="ORDER_BLOCK",
        layer="smart_money",
        label="ORDER BLOCK",
        display_label="ORDER BLOCK",
        visible_modes=["SMART_MONEY", "INSPECTOR"],
    )
    council = _base_overlay(
        type="MODEL_COUNCIL_MARKER",
        layer="active_council_decision",
        label="MODEL COUNCIL MARKER",
        display_label="MODEL COUNCIL MARKER",
        visible_modes=["COUNCIL", "INSPECTOR"],
    )

    assert profile["mode"] == "SMART_MONEY"
    assert set(profile["allowed_types"]) == smart_money_types
    assert profile["layer_visibility"]["smart_money"] is True
    assert profile["layer_visibility"]["active_council_decision"] is False
    assert overlay_is_visible(order_block, "SMART_MONEY") is True
    assert overlay_is_visible(order_block, "COUNCIL") is False
    assert overlay_is_visible(council, "SMART_MONEY") is False
    assert overlay_is_visible(council, "COUNCIL") is True
    assert overlay_is_visible(order_block, "INSPECTOR") is True


def test_contract_reports_missing_required_fields_and_strict_mode_raises() -> None:
    raw: dict[str, Any] = {"bbox": [1, 2, 3, 4], "confidence": 0.4}
    result = validate_v3_overlay_object(raw)

    assert result.ok is False
    fields = {error.field for error in result.errors}
    assert {"type", "overlay_id", "source_agent", "frame_id", "sequence_id", "chart_transform_id", "reason"}.issubset(fields)

    with pytest.raises(V3OverlayContractError) as exc:
        normalize_v3_overlay_object(raw)
    assert "overlay_id" in str(exc.value)


def test_live_modes_reject_unfiltered_raw_overlays_missing_renderer_contract() -> None:
    raw: dict[str, Any] = {"type": "SNIPER_ENTRY_BOX", "bbox": [1, 2, 3, 4], "confidence": 0.7}

    reasons = overlay_rejection_reasons(raw, "CLEAN_LIVE")

    assert "missing_live_render_field:layer" in reasons
    assert "missing_live_render_field:frame_id" in reasons
    assert "missing_live_render_field:chart_transform_id" in reasons
    assert "missing_live_render_field:truth_score" in reasons
    assert overlay_is_visible(raw, "CLEAN_LIVE") is False


def test_non_strict_normalization_accepts_v2_aliases_rect_and_anchors() -> None:
    sniper = normalize_v3_overlay_object(
        {
            "id": "v2-s1",
            "type": "SNIPER_ENTRY",
            "rect": [10, 20, 50, 70],
            "confidence": 1.2,
            "source": "legacy_v2_overlay_migration",
            "frame_id": 8,
            "sequence_id": "seq-8",
            "chart_transform_id": "ct-8",
            "reason": "migrated v2 sniper",
        },
        strict=False,
    )
    progression = normalize_v3_overlay_object(
        {
            "key": "hist-1",
            "type": "HISTORICAL_PROGRESSION",
            "anchors": [(5, 9), (12, 3), (20, 30)],
            "source_agent": "memory_bank",
            "frame_id": 9,
            "sequence_id": "seq-9",
            "chart_transform_id": "ct-9",
            "reason": "matched past continuation",
        },
        strict=False,
    )

    assert sniper["type"] == "SNIPER_ENTRY_BOX"
    assert sniper["confidence"] == 1.0
    assert progression["type"] == "PROGRESSION_PATH"
    assert progression["bounds"] == [5.0, 3.0, 20.0, 30.0]
    assert progression["anchor_type"] == "POLYGON"
    assert progression["line_points"] == [[5.0, 9.0], [12.0, 3.0], [20.0, 30.0]]


def test_trendline_overlays_preserve_line_geometry_and_layer_modes() -> None:
    support = normalize_v3_overlay_object(
        _base_overlay(
            type="SUPPORT_TRENDLINE",
            side="BUY",
            label="support trendline",
            display_label="support trendline",
            anchor_type="LINE",
            bounds=None,
            points=[[10, 100], [120, 100]],
            visible_modes=["TRENDLINES", "PATH", "ACTIVE_CONTEXT", "REPLAY"],
        ),
        strict=False,
    )
    inner = normalize_v3_overlay_object(
        _base_overlay(
            type="INNER_TRENDLINE",
            side="BUY",
            label="inner trendline",
            display_label="inner trendline",
            anchor_type="LINE",
            bounds=None,
            line_points=[[40, 90], [140, 72]],
            visible_modes=["TRENDLINES", "PATH", "ACTIVE_CONTEXT", "REPLAY"],
        ),
        strict=False,
    )
    progression = normalize_v3_overlay_object(
        _base_overlay(type="PROGRESSION_PATH", layer="PROGRESSION_PATH", label="history", visible_modes=["REPLAY"]),
        strict=False,
    )

    assert support["type"] == "SUPPORT_TRENDLINE"
    assert support["display_label"] == "SUPPORT TRENDLINE"
    assert support["layer"] == "trendlines"
    assert support["anchor_type"] == "LINE"
    assert support["line_points"] == [[10.0, 100.0], [120.0, 100.0]]
    assert support["bounds"] == [10.0, 97.0, 120.0, 103.0]
    assert overlay_is_visible(support, "SUPPLY_DEMAND") is False
    assert overlay_is_visible(support, "TRENDLINES") is True
    assert overlay_is_visible(support, "PATH") is False
    assert overlay_is_visible(support, "CLEAN_LIVE") is True
    assert inner["type"] == "INNER_TRENDLINE"
    assert inner["display_label"] == "INNER TRENDLINE"
    assert inner["layer"] == "trendlines"
    assert overlay_is_visible(inner, "LOCAL") is False
    assert overlay_is_visible(inner, "TRENDLINES") is True
    assert progression["layer"] == "historical_replay"


def test_progression_path_prefers_line_geometry_over_broad_context_bounds() -> None:
    progression = normalize_v3_overlay_object(
        _base_overlay(
            type="PROGRESSION_PATH",
            layer="historical_replay",
            label="historical progression",
            bounds=[20, 40, 760, 420],
            line_points=[[100, 360], [220, 320], [360, 210], [520, 180]],
            anchor_type="BOX",
            visible_modes=["CLEAN_LIVE", "REPLAY", "FULL_HISTORY_READ", "INSPECTOR"],
            lifecycle_state="HISTORICAL",
        ),
        strict=False,
    )

    assert progression["type"] == "PROGRESSION_PATH"
    assert progression["layer"] == "historical_replay"
    assert progression["anchor_type"] == "POLYGON"
    assert progression["line_points"] == [[100.0, 360.0], [220.0, 320.0], [360.0, 210.0], [520.0, 180.0]]
    assert progression["bounds"] == [100.0, 180.0, 520.0, 360.0]
    assert overlay_is_visible(progression, "CLEAN_LIVE") is False
    assert overlay_is_visible(progression, "FULL_HISTORY_READ") is True
    assert overlay_is_visible(progression, "REPLAY") is True


def test_coordinate_normalization_converts_between_chart_pixels_and_normalized() -> None:
    normalized = normalize_v3_overlay_object(
        _base_overlay(coordinate_mode="CHART_NORMALIZED", bounds=[80, 60, 400, 300]),
        image_size=[800, 600],
    )
    pixel = normalize_v3_overlay_object(
        _base_overlay(overlay_id="target-1", type="TARGET_ZONE", coordinate_mode="CHART_IMAGE_SPACE", bounds=[0.1, 0.2, 0.4, 0.5]),
        image_size=[800, 600],
        strict=False,
    )

    assert normalized["bounds"] == [0.1, 0.1, 0.5, 0.5]
    assert pixel["type"] == "TARGET_ZONE_BOX"
    assert pixel["bounds"] == [80.0, 120.0, 320.0, 300.0]
    assert normalize_bounds([4, 4, 4, 6]) is None


def test_semantic_target_invalidation_and_path_layers_override_legacy_layers() -> None:
    invalidation = normalize_v3_overlay_object(
        _base_overlay(overlay_id="invalid-1", type="INVALIDATION_BOX", layer="trigger_zones", role="invalidation"),
        strict=False,
    )
    target = normalize_v3_overlay_object(
        _base_overlay(overlay_id="target-1", type="TARGET_ZONE_BOX", layer="trigger_zones", role="target"),
        strict=False,
    )
    path = normalize_v3_overlay_object(
        _base_overlay(overlay_id="path-1", type="PREDICTION_PATH", layer="active_council_decision", role="prediction"),
        strict=False,
    )

    assert invalidation["layer"] == "invalidation"
    assert target["layer"] == "target_zones"
    assert path["layer"] == "prediction_path"
    assert overlay_is_visible(invalidation, "CLEAN_LIVE") is True
    assert overlay_is_visible(invalidation, "DIAGNOSTICS") is True
    assert overlay_is_visible(invalidation, "CALIBRATION") is False
    assert overlay_is_visible(invalidation, "INSPECTOR") is True


def test_mode_resolver_keeps_clean_live_light_but_allows_history_in_replay() -> None:
    now_ms = 10_000
    overlays = [
        _base_overlay(overlay_id="live-sniper", created_at_ms=9000, ttl_ms=5000),
        _base_overlay(
            overlay_id="replay-1",
            type="REPLAY_ENTRY",
            layer="historical_replay",
            visible_modes=["REPLAY", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="debug-1",
            type="DEBUG_RAW_DETECTION",
            layer="diagnostics",
            visible_modes=["DEBUG", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="broker-1",
            type="BROKER_CONTROL",
            layer="broker_controls",
            coordinate_mode="WINDOW_SPACE",
            visible_modes=["CALIBRATION", "INSPECTOR"],
        ),
        _base_overlay(overlay_id="expired-1", created_at_ms=0, ttl_ms=500),
    ]

    live = resolve_visible_overlays(overlays, "CLEAN_LIVE", now_ms=now_ms)
    replay = resolve_visible_overlays(overlays, "REPLAY", now_ms=now_ms)
    calibration = resolve_visible_overlays(overlays, "CALIBRATION", now_ms=now_ms)
    inspector = resolve_visible_overlays(overlays, "INSPECTOR", now_ms=now_ms)

    assert {overlay["overlay_id"] for overlay in live} == {"live-sniper"}
    assert "replay-1" in {overlay["overlay_id"] for overlay in replay}
    assert "broker-1" in {overlay["overlay_id"] for overlay in calibration}
    assert "debug-1" in {overlay["overlay_id"] for overlay in inspector}
    assert all(overlay["overlay_id"] != "expired-1" for overlay in inspector)


def test_clean_live_filters_lazy_history_payload_without_mutating_full_context() -> None:
    overlays = [
        _base_overlay(
            overlay_id="chart-bounds",
            type="CHART_BOUNDS",
            layer="chart_bounds",
            label="CHART BOUNDS",
            visible_modes=["ACTIVE_CONTEXT", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="current-candle",
            type="CURRENT_CANDLE",
            layer="recent_candles",
            label="NOW",
            visible_modes=["ACTIVE_CONTEXT", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="local-pullback",
            type="PULLBACK_BOX",
            layer="local_swings",
            label="LOCAL",
            visible_modes=["LOCAL", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="supply-zone",
            type="SUPPLY_ZONE",
            layer="supply_demand",
            label="SUPPLY",
            visible_modes=["SUPPLY_DEMAND", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="demand-zone",
            type="DEMAND_ZONE",
            layer="supply_demand",
            side="BUY",
            label="DEMAND",
            visible_modes=["SUPPLY_DEMAND", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="opposing-force",
            type="OPPOSING_FORCE",
            layer="supply_demand",
            label="OPPOSING FORCE",
            visible_modes=["TARGET", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="support-trendline",
            type="SUPPORT_TRENDLINE",
            layer="trendlines",
            label="SUPPORT TRENDLINE",
            visible_modes=["TRENDLINES", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="sniper-entry",
            type="SNIPER_ENTRY_BOX",
            layer="trigger_zones",
            label="SNIPER SELL",
            visible_modes=["TRIGGER", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="target-zone",
            type="TARGET_ZONE_BOX",
            layer="target_zones",
            label="TARGET",
            visible_modes=["TARGET", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="council-marker",
            type="MODEL_COUNCIL_MARKER",
            layer="active_council_decision",
            label="MODEL COUNCIL MARKER",
            visible_modes=["COUNCIL", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="global-history",
            type="IMPULSE_BOX",
            layer="major_swings",
            label="GLOBAL",
            visible_modes=["CLEAN_LIVE", "FULL_HISTORY_READ", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="progression-history",
            type="PROGRESSION_PATH",
            layer="historical_replay",
            label="HISTORICAL PROGRESSION",
            visible_modes=["CLEAN_LIVE", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
        ),
        _base_overlay(
            overlay_id="replay-entry",
            type="REPLAY_ENTRY",
            layer="historical_replay",
            label="REPLAY ENTRY",
            visible_modes=["CLEAN_LIVE", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"],
        ),
    ]

    clean = resolve_visible_overlays(overlays, "CLEAN_LIVE", apply_label_layout=False)
    full_history = resolve_visible_overlays(overlays, "FULL_HISTORY_READ", apply_label_layout=False)
    replay = resolve_visible_overlays(overlays, "REPLAY", apply_label_layout=False)

    assert {overlay["overlay_id"] for overlay in clean} == {
        "chart-bounds",
        "current-candle",
        "local-pullback",
        "supply-zone",
        "demand-zone",
        "opposing-force",
        "support-trendline",
        "sniper-entry",
        "target-zone",
        "council-marker",
    }
    assert {"global-history", "progression-history", "replay-entry"}.issubset(
        {overlay["overlay_id"] for overlay in full_history}
    )
    assert {"progression-history", "replay-entry"}.issubset({overlay["overlay_id"] for overlay in replay})
    assert overlays[-1]["visible_modes"] == ["CLEAN_LIVE", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"]


def test_view_mode_profile_exposes_layer_policy() -> None:
    clean = view_mode_profile("CLEAN_LIVE")
    council = view_mode_profile("COUNCIL")
    inspector = view_mode_profile("INSPECTOR")
    supply = view_mode_profile("supply-demand")
    trigger = view_mode_profile("trigger")

    assert clean["layer_visibility"]["historical_replay"] is False
    assert clean["layer_visibility"]["major_swings"] is False
    assert clean["layer_visibility"]["trendlines"] is True
    assert clean["layer_visibility"]["diagnostics"] is False
    assert clean["layer_visibility"]["prediction_path"] is False
    assert "IMPULSE_BOX" not in clean["allowed_types"]
    assert "PROGRESSION_PATH" not in clean["allowed_types"]
    assert council["layer_visibility"]["recent_candles"] is False
    assert council["layer_visibility"]["trigger_zones"] is False
    assert set(council["allowed_types"]) == {
        "MARKET_PLAY_MARKER",
        "MODEL_COUNCIL_MARKER",
        "PRICE_LOCATION_MARKER",
        "REGIME_MARKER",
    }
    assert supply["mode"] == "SUPPLY_DEMAND"
    assert supply["layer_visibility"]["chart_bounds"] is False
    assert supply["layer_visibility"]["recent_candles"] is False
    assert supply["layer_visibility"]["supply_demand"] is True
    assert supply["layer_visibility"]["trendlines"] is False
    assert supply["layer_visibility"]["trigger_zones"] is False
    assert "CURRENT_CANDLE" not in supply["allowed_types"]
    assert "CHART_BOUNDS" not in supply["allowed_types"]
    assert "SUPPORT_TRENDLINE" not in supply["allowed_types"]
    assert trigger["layer_visibility"]["recent_candles"] is False
    assert trigger["layer_visibility"]["trigger_zones"] is True
    assert "CURRENT_CANDLE" not in trigger["allowed_types"]
    assert "CHART_BOUNDS" not in trigger["allowed_types"]
    assert "RETEST_BOX" in trigger["allowed_types"]
    assert set(trigger["allowed_types"]) == {"RETEST_BOX", "SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX"}
    assert inspector["layer_visibility"]["diagnostics"] is True
    assert inspector["allow_selection"] is True


def test_story_scoped_modes_do_not_render_now_or_chart_bounds_spam() -> None:
    replay_now = _base_overlay(
        overlay_id="replay-now",
        type="CURRENT_CANDLE",
        layer="recent_candles",
        visible_modes=["REPLAY", "PREDICTION", "INSPECTOR"],
        label="NOW",
    )
    chart_bounds = _base_overlay(
        overlay_id="chart-bounds",
        type="CHART_BOUNDS",
        layer="chart_bounds",
        visible_modes=["ACTIVE_CONTEXT", "TRIGGER", "SUPPLY_DEMAND", "INSPECTOR"],
        label="CHART BOUNDS",
    )
    trigger = _base_overlay(type="RETEST_BOX", layer="trigger_zones", visible_modes=["ACTIVE_CONTEXT", "TRIGGER"])
    sniper = _base_overlay(type="SNIPER_ENTRY_BOX", layer="trigger_zones", visible_modes=["ACTIVE_CONTEXT", "TRIGGER"])
    target = _base_overlay(type="TARGET_ZONE_BOX", layer="target_zones", visible_modes=["ACTIVE_CONTEXT", "TRIGGER"])
    supply = _base_overlay(type="SUPPLY_ZONE", layer="supply_demand", visible_modes=["ACTIVE_CONTEXT"])

    assert overlay_is_visible(replay_now, "ACTIVE_CONTEXT") is False
    assert overlay_is_visible(replay_now, "TRIGGER") is False
    assert overlay_is_visible(replay_now, "SUPPLY_DEMAND") is False
    assert overlay_is_visible(chart_bounds, "TRIGGER") is False
    assert overlay_is_visible(chart_bounds, "SUPPLY_DEMAND") is False
    assert overlay_is_visible(trigger, "TRIGGER") is True
    assert overlay_is_visible(sniper, "TRIGGER") is True
    assert overlay_is_visible(target, "TRIGGER") is True
    assert overlay_is_visible(supply, "SUPPLY_DEMAND") is True


def test_council_mode_does_not_render_current_candle_or_trigger_spam() -> None:
    current = _base_overlay(type="CURRENT_CANDLE", layer="recent_candles", visible_modes=["CLEAN_LIVE", "COUNCIL"])
    trigger = _base_overlay(type="RETEST_BOX", layer="trigger_zones", visible_modes=["CLEAN_LIVE", "COUNCIL"])
    council = _base_overlay(
        type="MODEL_COUNCIL_MARKER",
        layer="active_council_decision",
        visible_modes=["COUNCIL"],
        label="MODEL COUNCIL MARKER",
    )

    assert overlay_is_visible(current, "COUNCIL") is False
    assert overlay_is_visible(trigger, "COUNCIL") is False
    assert overlay_is_visible(council, "COUNCIL") is True


def test_prediction_path_overlays_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", raising=False)
    prediction = _base_overlay(
        overlay_id="prediction-path-1",
        type="PREDICTION_PATH",
        layer="prediction_path",
        anchor_type="POLYGON",
        visible_modes=["PREDICTION", "INSPECTOR"],
    )

    assert prediction_overlay_enabled() is False
    assert prediction_overlay_config()["enabled"] is False
    assert overlay_is_visible(prediction, "CLEAN_LIVE") is False
    assert overlay_is_visible(prediction, "ACTIVE_CONTEXT") is False
    assert overlay_is_visible(prediction, "DIAGNOSTICS") is False
    assert "prediction_overlay_disabled" in overlay_rejection_reasons(prediction, "DIAGNOSTICS")

    monkeypatch.setenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", "1")

    assert prediction_overlay_enabled() is True
    assert overlay_is_visible(prediction, "DIAGNOSTICS") is True
    assert overlay_is_visible(prediction, "CLEAN_LIVE") is False


def test_prediction_label_tokens_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", raising=False)
    overlay = _base_overlay(
        overlay_id="buy-target-percent-1",
        type="TARGET_ZONE_BOX",
        layer="target_zones",
        label="BUY TARGET 46%",
        reason="legacy BUY_TARGET_PERCENT projection overlay",
        visible_modes=["CLEAN_LIVE", "ACTIVE_CONTEXT", "DIAGNOSTICS", "INSPECTOR"],
    )

    assert overlay_is_visible(overlay, "CLEAN_LIVE") is False
    assert overlay_is_visible(overlay, "ACTIVE_CONTEXT") is False
    assert overlay_is_visible(overlay, "DIAGNOSTICS") is False
    assert "prediction_overlay_disabled" in overlay_rejection_reasons(overlay, "CLEAN_LIVE")

    monkeypatch.setenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", "1")

    assert overlay_is_visible(overlay, "DIAGNOSTICS") is True
    assert overlay_is_visible(overlay, "CLEAN_LIVE") is False


def test_granular_operator_modes_accept_compatible_legacy_visible_modes() -> None:
    global_box = _base_overlay(type="IMPULSE_BOX", layer="major_swings", visible_modes=["ACTIVE_CONTEXT"])
    local_box = _base_overlay(type="PULLBACK_BOX", layer="local_swings", visible_modes=["ACTIVE_CONTEXT"])
    target_box = _base_overlay(type="TARGET_ZONE_BOX", layer="target_zones", visible_modes=["PREDICTION"])
    broker_box = _base_overlay(type="BROKER_CONTROL", layer="broker_controls", visible_modes=["CALIBRATION"])
    debug_box = _base_overlay(type="DEBUG_RAW_DETECTION", layer="diagnostics", visible_modes=["DEBUG"])

    assert overlay_is_visible(global_box, "GLOBAL") is True
    assert overlay_is_visible(local_box, "LOCAL") is True
    assert overlay_is_visible(target_box, "TARGET") is True
    assert overlay_is_visible(broker_box, "BROKER") is True
    assert overlay_is_visible(debug_box, "DIAGNOSTICS") is True
    assert overlay_is_visible(debug_box, "CLEAN_LIVE") is False


def test_reason_if_empty_reports_no_objects_and_visibility_rejections() -> None:
    broker_only = _base_overlay(
        overlay_id="broker-only",
        type="BROKER_CONTROL",
        layer="broker_controls",
        coordinate_mode="WINDOW_SPACE",
        visible_modes=["BROKER"],
    )
    expired = _base_overlay(overlay_id="expired", created_at_ms=0, ttl_ms=500)

    assert reason_if_empty([], mode="path") == "no_v3_overlay_objects:PATH"
    assert reason_if_empty([broker_only], mode="broker") == ""

    broker_reasons = overlay_rejection_reasons(broker_only, "clean_live")
    assert "type_not_allowed:BROKER_CONTROL:CLEAN_LIVE" in broker_reasons
    assert reason_if_empty([broker_only], mode="clean_live") == (
        "no_visible_v3_overlay_objects:CLEAN_LIVE:layer_hidden=1,type_not_allowed=1,visible_modes_exclude=1"
    )

    expired_reason = reason_if_empty([expired], mode="clean_live", now_ms=10_000)
    assert expired_reason == "no_visible_v3_overlay_objects:CLEAN_LIVE:expired_ttl=1"


def test_overlay_visibility_honors_lifecycle_and_layer_override() -> None:
    overlay = normalize_v3_overlay_object(_base_overlay(lifecycle_state="INVALIDATED"), strict=False)
    active = normalize_v3_overlay_object(_base_overlay(), strict=False)

    assert overlay_is_visible(overlay, "CLEAN_LIVE") is False
    assert overlay_is_visible(active, "CLEAN_LIVE", layer_overrides={"trigger_zones": False}) is False
    assert overlay_is_visible(active, "CLEAN_LIVE", layer_overrides={"trigger_zones": True}) is True


def test_label_layout_stacks_crowded_boxes_without_label_overlap() -> None:
    overlays = [
        normalize_v3_overlay_object(
            _base_overlay(
                overlay_id=f"sniper-{index}",
                bounds=[100 + index * 2, 100 + index * 2, 150 + index * 2, 140 + index * 2],
                label=f"SELL SNIPER TRIGGER {index}",
                z_index=index,
            )
        )
        for index in range(6)
    ]

    laid_out = layout_overlay_labels(overlays, chart_bounds=[0, 0, 420, 260])
    visible_labels: list[Sequence[Any]] = [
        cast(Sequence[Any], overlay["label_bounds"]) for overlay in laid_out if not overlay["label_hidden"]
    ]

    assert len(visible_labels) >= 4
    for index, first in enumerate(visible_labels):
        for second in visible_labels[index + 1 :]:
            assert rectangles_overlap(first, second, padding=2.0) is False
    assert all("label_hidden" in overlay for overlay in laid_out)


def test_label_layout_can_hide_lower_priority_labels_when_canvas_is_tight() -> None:
    overlays = [
        normalize_v3_overlay_object(
            _base_overlay(
                overlay_id=f"debug-{index}",
                type="DEBUG_RAW_DETECTION",
                layer="diagnostics",
                bounds=[0.45, 0.45, 0.5, 0.5],
                coordinate_mode="CHART_NORMALIZED",
                label=f"very long raw diagnostic overlay label {index}",
                visible_modes=["DEBUG", "INSPECTOR"],
            ),
            strict=False,
        )
        for index in range(32)
    ]

    laid_out = layout_overlay_labels(overlays, chart_bounds=[0, 0, 1, 1])

    assert any(overlay["label_hidden"] for overlay in laid_out)
    assert abbreviate_label("SELL RECLAIM TRIGGER CONTINUATION") == "SELL RECL TRIG"
