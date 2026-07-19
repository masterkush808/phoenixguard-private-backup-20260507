from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

from phoenixguard.vision.order_positioning_annotation_v3 import (
    normalize_order_positioning_annotation_v3,
    validate_order_positioning_annotation_v3,
)
from phoenixguard.vision.v3_overlay_contract import (
    normalize_v3_overlay_object,
    overlay_rejection_reasons,
    validate_v3_overlay_object,
)


_REPO = Path(__file__).resolve().parents[2]


def _evidence(*, confirmation: bool = False) -> dict[str, Any]:
    return {
        "evidence_families": ["SUPPLY_DEMAND"],
        "hard_anchor_count": 1,
        "anchor_candle_indices": [8],
        "anchor_candle_ids": ["candle-8"],
        "anchor_candle_bboxes_px": [[420, 420, 440, 475]],
        "swing_anchor_indices": [],
        "source_zone_ids": [],
        "trendline_ids": [],
        "confirmation_closed_candle_keys": ["candle-10"] if confirmation else [],
        "confirmation_events": ["BREAK_OF_STRUCTURE"] if confirmation else [],
        "confirmation_side": "BUY" if confirmation else None,
        "opposing_structure_ids": ["opposing-1"],
        "rationale": "Human-reviewed structural area visible on the anchor frame.",
    }


def _zone(
    *,
    zone_id: str,
    label: str,
    side: str,
    thesis_side: str,
    order_kind: str,
    role: str,
    bbox_px: list[float],
    bbox_normalized: list[float],
    lower: float,
    upper: float,
    relation: str,
    confirmation: bool = False,
) -> dict[str, Any]:
    return {
        "zone_id": zone_id,
        "label": label,
        "side": side,
        "thesis_side": thesis_side,
        "order_kind": order_kind,
        "order_role": role,
        "human_decision": "ACCEPTED",
        "public_label": "Plan area",
        "bbox_px": bbox_px,
        "bbox_normalized": bbox_normalized,
        "anchor_price_proxy": 100.0,
        "lower_price_proxy": lower,
        "upper_price_proxy": upper,
        "price_relation_at_anchor": relation,
        "buffer": {"value": 0.1, "unit": "PRICE_PROXY", "rationale": "Spread and chart uncertainty."},
        "evidence": _evidence(confirmation=confirmation),
        "validity": {
            "created_closed_candle_key": "candle-10",
            "prospective_at_creation": True,
            "already_crossed_at_creation": False,
            "late_after_move": False,
            "stale_after_candles": 12,
            "invalidation_rule": "Close beyond the protected structural boundary.",
            "replacement_policy": "FROZEN_UNTIL_EPISODE_END_OR_HARD_INVALIDATION",
        },
        "quality": {
            "human_confidence": 0.91,
            "geometry_confidence": 0.9,
            "causal_visibility_confirmed": True,
            "not_chasing_confirmed": True,
            "inside_plot_confirmed": True,
            "price_relationship_confirmed": True,
        },
    }


def _annotation() -> dict[str, Any]:
    return {
        "schema_version": "PHOENIXGUARD_ORDER_POSITIONING_ANNOTATION_V3",
        "annotation_id": "annotation-10",
        "annotation_phase": "PRE_OUTCOME",
        "geometry_source_annotation_id": None,
        "episode": {
            "episode_id": "episode-10",
            "episode_revision": 1,
            "pair_key": "GBP_USD_OTC",
            "timeframe": "M5",
            "started_at": "2026-07-19T10:00:00Z",
            "anchor_frame_id": "frame-10",
            "anchor_closed_candle_key": "candle-10",
            "tracking_horizon_candles": 12,
            "split_group_id": "episode-group-10",
            "baseline_frozen": True,
            "status": "TRACKING",
            "supersedes_episode_id": None,
            "supersession_reason": "NONE",
        },
        "frame": {
            "frame_id": "frame-10",
            "captured_at": "2026-07-19T10:00:00Z",
            "source_image_sha256": "a" * 64,
            "source_reference": "dataset/frame-10.png",
            "width": 1200,
            "height": 800,
            "chart_bounds_px": [100, 50, 1100, 750],
            "plot_bounds_px": [200, 100, 1000, 700],
            "chart_transform_id": "transform-10",
            "source_lock_id": "source-lock-10",
            "closed_candle_key": "candle-10",
            "closed_candle_sequence": 10,
            "complete_closed_candle_confirmed": True,
            "price_axis_direction": "HIGHER_PRICE_AT_SMALLER_Y",
        },
        "market_context": {
            "current_price_proxy": 100.0,
            "current_price_y_px": 400.0,
            "candidate_scope": "BUY",
            "global_direction": "UP",
            "local_direction": "UP",
            "range_state": "TRENDING",
            "spread_or_uncertainty_tolerance": {
                "value": 0.1,
                "unit": "PRICE_PROXY",
                "rationale": "Observed spread and transform uncertainty.",
            },
        },
        "zones": [
            _zone(
                zone_id="buy-limit-1",
                label="BUY_LIMIT_ZONE",
                side="BUY",
                thesis_side="BUY",
                order_kind="BUY_LIMIT",
                role="PASSIVE_ENTRY",
                bbox_px=[300, 450, 500, 500],
                bbox_normalized=[0.125, 0.583333, 0.375, 0.666667],
                lower=98.0,
                upper=99.0,
                relation="BELOW_CURRENT",
            ),
            _zone(
                zone_id="protect-long-1",
                label="PROTECTIVE_STOP_ZONE",
                side="SELL",
                thesis_side="BUY",
                order_kind="SELL_STOP",
                role="PROTECTIVE_INVALIDATION",
                bbox_px=[300, 550, 500, 600],
                bbox_normalized=[0.125, 0.75, 0.375, 0.833333],
                lower=95.0,
                upper=96.0,
                relation="BELOW_CURRENT",
            ),
        ],
        "negative_labels": [],
        "outcome": None,
        "review": {
            "state": "DOUBLE_REVIEWED",
            "annotator_ids": ["annotator-1"],
            "reviewer_ids": ["reviewer-1"],
            "adjudicator_id": None,
            "disagreement_present": False,
            "geometry_locked": True,
            "training_eligibility": "ELIGIBLE",
            "exclusion_reasons": [],
            "notes": "Independent geometry review complete.",
        },
        "leakage_guard": {
            "episode_group_hash": "b" * 64,
            "source_capture_group": "capture-group-10",
            "source_sequence_id": "sequence-10",
            "perceptual_group_id": "perceptual-10",
            "pair_time_bucket": "GBP_USD_OTC-M5-20260719T1000",
            "split_assignment": "TRAIN",
            "grouping_dimensions": [
                "EPISODE",
                "SOURCE_CAPTURE",
                "SOURCE_SEQUENCE",
                "PERCEPTUAL_DUPLICATE",
            ],
            "no_cross_split_related_frames": True,
        },
        "provenance": {
            "annotated_at": "2026-07-19T10:05:00Z",
            "tool_version": "annotation-tool-v3",
            "doctrine_version": "ORDER_POSITIONING_V3_2026-07-19",
            "source_kind": "HUMAN",
            "future_frames_visible_to_annotator": False,
            "visible_until_closed_candle_key": "candle-10",
            "book_evidence_refs": ["HLZ:51"],
            "weak_candidate_ids": [],
        },
    }


def _overlay(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
        "overlay_id": "overlay-1",
        "object_id": "overlay-object-1",
        "track_id": "overlay-track-1",
        "type": "SUPPLY_ZONE",
        "side": "SELL",
        "source_agent": "test",
        "source_version": "test-v1",
        "broker_source_lock_id": "broker-lock-1",
        "frame_id": "10",
        "sequence_id": "sequence-10",
        "chart_transform_id": "transform-10",
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "anchor_type": "BOX",
        "anchor_candles": [8, 9],
        "touch_points": [[300, 250], [500, 300]],
        "bounds": [300, 250, 500, 300],
        "truth_score": 0.9,
        "confidence": 0.9,
        "lifecycle_state": "ACTIVE",
        "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT", "INSPECTOR"],
        "ttl_ms": 9000,
        "reason": "test market structure",
        "label": "SUPPLY",
        "layer": "supply_demand",
    }
    payload.update(overrides)
    return payload


def _codes(result: Any) -> set[str]:
    return {issue.code for issue in result.issues}


def test_valid_annotation_is_canonical_and_safe_for_training() -> None:
    result = validate_order_positioning_annotation_v3(_annotation())

    assert result.ok is True
    assert result.safe_for_training is True
    protective = cast(list[dict[str, object]], result.normalized_annotation["zones"])[1]
    assert protective["label"] == "PROTECTIVE_STOP_ZONE"
    assert protective["side"] == "SELL"
    assert protective["thesis_side"] == "BUY"
    assert protective["order_kind"] == "SELL_STOP"


def test_legacy_protective_names_are_safely_adapted_to_actual_order_side() -> None:
    annotation = _annotation()
    legacy = annotation["zones"][1]
    legacy["label"] = "BUY_PROTECTIVE_STOP_ZONE"
    legacy["side"] = "BUY"
    legacy.pop("thesis_side")
    legacy.pop("order_kind")

    normalized = normalize_order_positioning_annotation_v3(annotation)
    protective = cast(list[dict[str, object]], normalized["zones"])[1]
    assert protective["label"] == "PROTECTIVE_STOP_ZONE"
    assert protective["side"] == "SELL"
    assert protective["thesis_side"] == "BUY"
    assert protective["order_kind"] == "SELL_STOP"
    assert validate_order_positioning_annotation_v3(annotation).safe_for_training is True

    annotation = _annotation()
    annotation["market_context"]["candidate_scope"] = "SELL"
    legacy = annotation["zones"][1]
    legacy.update(
        {
            "label": "SELL_PROTECTIVE_STOP_ZONE",
            "side": "SELL",
            "bbox_px": [300, 200, 500, 250],
            "bbox_normalized": [0.125, 0.166667, 0.375, 0.25],
            "lower_price_proxy": 104.0,
            "upper_price_proxy": 105.0,
            "price_relation_at_anchor": "ABOVE_CURRENT",
        }
    )
    legacy.pop("thesis_side")
    legacy.pop("order_kind")
    annotation["zones"] = [legacy]
    normalized = normalize_order_positioning_annotation_v3(annotation)
    protective = cast(list[dict[str, object]], normalized["zones"])[0]
    assert protective["side"] == "BUY"
    assert protective["thesis_side"] == "SELL"
    assert protective["order_kind"] == "BUY_STOP"
    assert validate_order_positioning_annotation_v3(annotation).safe_for_training is True


def test_legacy_alias_with_explicit_contradiction_fails_closed() -> None:
    annotation = _annotation()
    legacy = annotation["zones"][1]
    legacy["label"] = "BUY_PROTECTIVE_STOP_ZONE"
    legacy["thesis_side"] = "SELL"
    legacy["order_kind"] = "BUY_STOP"

    result = validate_order_positioning_annotation_v3(annotation)

    assert result.ok is False
    assert result.safe_for_training is False
    assert "LEGACY_PROTECTIVE_ALIAS_CONFLICT" in _codes(result)


def test_canonical_protective_side_means_actual_broker_order_side() -> None:
    annotation = _annotation()
    protective = annotation["zones"][1]
    protective["side"] = "BUY"
    protective["order_kind"] = "BUY_STOP"

    result = validate_order_positioning_annotation_v3(annotation)

    assert "CONTRADICTORY_PROTECTIVE_STOP_SEMANTICS" in _codes(result)
    assert result.safe_for_training is False


def test_contradictory_geometry_and_entry_protective_overlap_fail_closed() -> None:
    annotation = _annotation()
    protective = annotation["zones"][1]
    protective["bbox_px"] = [320, 460, 480, 490]
    protective["bbox_normalized"] = [0.15, 0.6, 0.35, 0.65]

    result = validate_order_positioning_annotation_v3(annotation)

    assert "ENTRY_PROTECTIVE_GEOMETRY_OVERLAP" in _codes(result)
    assert result.safe_for_training is False

    annotation = _annotation()
    protective = annotation["zones"][1]
    protective["bbox_px"] = [300, 250, 500, 300]
    protective["bbox_normalized"] = [0.125, 0.25, 0.375, 0.333333]
    result = validate_order_positioning_annotation_v3(annotation)
    assert "VERTICAL_GEOMETRY_RELATION_MISMATCH" in _codes(result)


def test_pixel_normalized_mismatch_and_unsafe_eligibility_fail_closed() -> None:
    annotation = _annotation()
    annotation["zones"][0]["bbox_normalized"] = [0.0, 0.0, 0.1, 0.1]
    annotation["review"]["geometry_locked"] = False
    annotation["leakage_guard"]["split_assignment"] = "UNASSIGNED"
    annotation["provenance"]["future_frames_visible_to_annotator"] = True

    result = validate_order_positioning_annotation_v3(annotation)

    assert "PIXEL_NORMALIZED_GEOMETRY_MISMATCH" in _codes(result)
    assert "UNSAFE_TRAINING_ELIGIBILITY" in _codes(result)
    assert result.safe_for_training is False


def test_vertical_geometry_uses_recorded_inverted_price_axis() -> None:
    annotation = _annotation()
    annotation["frame"]["price_axis_direction"] = "HIGHER_PRICE_AT_LARGER_Y"
    buy_limit = annotation["zones"][0]
    buy_limit["bbox_px"] = [300, 300, 500, 350]
    buy_limit["bbox_normalized"] = [0.125, 0.333333, 0.375, 0.416667]
    protective = annotation["zones"][1]
    protective["bbox_px"] = [300, 200, 500, 250]
    protective["bbox_normalized"] = [0.125, 0.166667, 0.375, 0.25]

    result = validate_order_positioning_annotation_v3(annotation)

    assert result.ok is True
    assert result.safe_for_training is True


def test_stop_entry_requires_named_closed_candle_confirmation() -> None:
    annotation = _annotation()
    annotation["zones"] = [
        _zone(
            zone_id="buy-stop-1",
            label="BUY_STOP_ENTRY_ZONE",
            side="BUY",
            thesis_side="BUY",
            order_kind="BUY_STOP",
            role="MOMENTUM_ENTRY",
            bbox_px=[300, 300, 500, 350],
            bbox_normalized=[0.125, 0.333333, 0.375, 0.416667],
            lower=101.0,
            upper=102.0,
            relation="ABOVE_CURRENT",
        )
    ]

    result = validate_order_positioning_annotation_v3(annotation)

    assert "MISSING_CLOSED_STOP_ENTRY_CONFIRMATION" in _codes(result)
    assert result.safe_for_training is False

    annotation["zones"][0]["evidence"]["confirmation_closed_candle_keys"] = ["candle-10"]
    annotation["zones"][0]["evidence"]["confirmation_events"] = ["BREAK_OF_STRUCTURE"]
    annotation["zones"][0]["evidence"]["confirmation_side"] = "BUY"
    result = validate_order_positioning_annotation_v3(annotation)
    assert result.safe_for_training is True

    annotation["zones"][0]["evidence"]["confirmation_side"] = "SELL"
    result = validate_order_positioning_annotation_v3(annotation)
    assert "MISSING_CLOSED_STOP_ENTRY_CONFIRMATION" in _codes(result)
    assert result.safe_for_training is False


def test_overlay_normalizer_preserves_only_explicit_closed_confirmation_proof() -> None:
    confirmed = normalize_v3_overlay_object(
        _overlay(
            confirmation_state="closed_confirmed",
            confirmation_type="BOS",
            confirmation_side="BUY",
            confirmation_closed_candle_key="candle-10",
            confirmation_closed_candle_index=10,
            knowledge_tags=["market structure", "BOS"],
            evidence_tokens=["closed candle"],
        )
    )
    raw_landscape = normalize_v3_overlay_object(_overlay(knowledge_tags=["supply", "demand"]))

    assert confirmed["confirmation_state"] == "CONFIRMED_CLOSED"
    assert confirmed["confirmation_event"] == "BREAK_OF_STRUCTURE"
    assert confirmed["confirmation_closed_candle_key"] == "candle-10"
    assert confirmed["confirmation_closed_candle_index"] == 10
    assert confirmed["stop_entry_confirmation_valid"] is True
    assert raw_landscape["stop_entry_confirmation_valid"] is False


def test_overlay_legacy_protective_alias_maps_thesis_to_opposite_actual_order() -> None:
    overlay = normalize_v3_overlay_object(
        _overlay(
            type="BUY_PROTECTIVE_STOP_ZONE",
            side="BUY",
            role="protective_stop",
            order_kind="PROTECTIVE_STOP",
        )
    )

    assert overlay["type"] == "PROTECTIVE_STOP_ZONE"
    assert overlay["side"] == "SELL"
    assert overlay["thesis_side"] == "BUY"
    assert overlay["order_kind"] == "SELL_STOP"
    assert overlay["protective_semantics_valid"] is True
    assert validate_v3_overlay_object(overlay).ok is True


def test_unconfirmed_stop_entry_overlay_is_rejected_from_live_modes() -> None:
    unconfirmed = _overlay(
        type="BUY_STOP_ENTRY_ZONE",
        side="BUY",
        role="buy_stop_entry",
        order_kind="BUY_STOP",
    )
    confirmed = copy.deepcopy(unconfirmed)
    confirmed.update(
        {
            "confirmation_state": "CONFIRMED_CLOSED",
            "confirmation_event": "MARKET_STRUCTURE_SHIFT",
            "confirmation_side": "BUY",
            "confirmation_closed_candle_key": "candle-10",
        }
    )
    wrong_side = copy.deepcopy(confirmed)
    wrong_side["confirmation_side"] = "SELL"

    assert "missing_closed_stop_entry_confirmation" in overlay_rejection_reasons(unconfirmed, "CLEAN_LIVE")
    assert "missing_closed_stop_entry_confirmation" not in overlay_rejection_reasons(confirmed, "CLEAN_LIVE")
    assert "stop_entry_confirmation_side_mismatch" in overlay_rejection_reasons(wrong_side, "CLEAN_LIVE")


def test_json_schema_uses_one_canonical_protective_label_and_explicit_sides() -> None:
    schema_path = _REPO / "docs" / "schemas" / "phoenixguard_order_positioning_annotation_v3.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    zone = schema["$defs"]["zone"]

    assert "PROTECTIVE_STOP_ZONE" in zone["properties"]["label"]["enum"]
    assert "BUY_PROTECTIVE_STOP_ZONE" not in zone["properties"]["label"]["enum"]
    assert "SELL_PROTECTIVE_STOP_ZONE" not in zone["properties"]["label"]["enum"]
    assert {"thesis_side", "order_kind"}.issubset(zone["required"])
    assert "price_axis_direction" in schema["$defs"]["frame"]["required"]
