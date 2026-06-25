from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence, cast

import pytest

from phoenixguard.vision.v3_overlay_contract import (
    REQUIRED_FIELDS,
    V3OverlayContractError,
    abbreviate_label,
    approved_overlay_display_labels,
    is_approved_overlay_display_label,
    layout_overlay_labels,
    normalize_bounds,
    normalize_overlay_display_label,
    normalize_v3_overlay_object,
    normalize_view_mode,
    overlay_is_visible,
    overlay_rejection_reasons,
    prediction_overlay_config,
    prediction_overlay_enabled,
    rectangles_overlap,
    reason_if_empty,
    resolve_visible_overlays,
    validate_v3_overlay_object,
    view_mode_profile,
)


_REPO = Path(__file__).resolve().parents[1]


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


def test_visible_labels_are_locked_to_approved_dictionary() -> None:
    assert "NOW" in approved_overlay_display_labels()
    assert is_approved_overlay_display_label("SNIPER SELL") is True
    assert is_approved_overlay_display_label("SUPPORT TRENDLINE") is True
    assert is_approved_overlay_display_label("RESISTANCE TRENDLINE") is True
    assert is_approved_overlay_display_label("INNER TRENDLINE") is True
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
    assert view_mode_profile("invalidation")["layer_visibility"]["invalidation"] is False
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
    assert replay_profile["layer_visibility"]["trigger_zones"] is True
    assert replay_profile["layer_visibility"]["target_zones"] is True
    assert replay_profile["layer_visibility"]["invalidation"] is False


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
    assert support["anchor_type"] == "POLYGON"
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
    assert overlay_is_visible(progression, "CLEAN_LIVE") is True


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
    assert overlay_is_visible(invalidation, "CLEAN_LIVE") is False
    assert overlay_is_visible(invalidation, "DIAGNOSTICS") is False
    assert overlay_is_visible(invalidation, "CALIBRATION") is False
    assert overlay_is_visible(invalidation, "INSPECTOR") is True


def test_mode_resolver_allows_clean_live_replay_but_hides_debug_expired_and_broker_controls() -> None:
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

    assert {"live-sniper", "replay-1"}.issubset({overlay["overlay_id"] for overlay in live})
    assert "replay-1" in {overlay["overlay_id"] for overlay in replay}
    assert "broker-1" in {overlay["overlay_id"] for overlay in calibration}
    assert "debug-1" in {overlay["overlay_id"] for overlay in inspector}
    assert all(overlay["overlay_id"] != "expired-1" for overlay in inspector)


def test_view_mode_profile_exposes_layer_policy() -> None:
    clean = view_mode_profile("CLEAN_LIVE")
    council = view_mode_profile("COUNCIL")
    inspector = view_mode_profile("INSPECTOR")
    supply = view_mode_profile("supply-demand")
    trigger = view_mode_profile("trigger")

    assert clean["layer_visibility"]["historical_replay"] is True
    assert clean["layer_visibility"]["trendlines"] is True
    assert clean["layer_visibility"]["diagnostics"] is False
    assert clean["layer_visibility"]["prediction_path"] is False
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
    trigger = _base_overlay(type="RETEST_BOX", layer="trigger_zones", visible_modes=["ACTIVE_CONTEXT"])
    supply = _base_overlay(type="SUPPLY_ZONE", layer="supply_demand", visible_modes=["ACTIVE_CONTEXT"])

    assert overlay_is_visible(replay_now, "ACTIVE_CONTEXT") is False
    assert overlay_is_visible(replay_now, "TRIGGER") is False
    assert overlay_is_visible(replay_now, "SUPPLY_DEMAND") is False
    assert overlay_is_visible(chart_bounds, "TRIGGER") is False
    assert overlay_is_visible(chart_bounds, "SUPPLY_DEMAND") is False
    assert overlay_is_visible(trigger, "TRIGGER") is True
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
