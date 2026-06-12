from __future__ import annotations

import pytest

from phoenixguard.vision.v3_overlay_contract import (
    REQUIRED_FIELDS,
    V3OverlayContractError,
    abbreviate_label,
    layout_overlay_labels,
    normalize_bounds,
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


def _base_overlay(**overrides):
    payload = {
        "overlay_id": "sniper-1",
        "object_id": "obj-1",
        "track_id": "track-1",
        "type": "SNIPER_ENTRY_BOX",
        "side": "SELL",
        "source_agent": "model_council_v3",
        "frame_id": 42,
        "sequence_id": "seq-42",
        "chart_transform_id": "ct-42",
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "anchor_type": "BOX",
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
    assert overlay["anchor_candles"] == []
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


def test_view_mode_aliases_cover_overlay_buttons_and_backend_modes() -> None:
    cases = {
        "chart-bounds": "CHART_BOUNDS",
        "candles": "CANDLES",
        "major": "GLOBAL",
        "major/global": "GLOBAL",
        "local": "LOCAL",
        "supply-demand": "SUPPLY_DEMAND",
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
    assert view_mode_profile("invalidation")["layer_visibility"]["invalidation"] is True


def test_contract_reports_missing_required_fields_and_strict_mode_raises() -> None:
    raw = {"bbox": [1, 2, 3, 4], "confidence": 0.4}
    result = validate_v3_overlay_object(raw)

    assert result.ok is False
    fields = {error.field for error in result.errors}
    assert {"type", "overlay_id", "source_agent", "frame_id", "sequence_id", "chart_transform_id", "reason"}.issubset(fields)

    with pytest.raises(V3OverlayContractError) as exc:
        normalize_v3_overlay_object(raw)
    assert "overlay_id" in str(exc.value)


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


def test_mode_resolver_hides_replay_debug_expired_and_broker_controls_from_live() -> None:
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

    assert [overlay["overlay_id"] for overlay in live] == ["live-sniper"]
    assert "replay-1" in {overlay["overlay_id"] for overlay in replay}
    assert "broker-1" in {overlay["overlay_id"] for overlay in calibration}
    assert "debug-1" in {overlay["overlay_id"] for overlay in inspector}
    assert all(overlay["overlay_id"] != "expired-1" for overlay in inspector)


def test_view_mode_profile_exposes_layer_policy() -> None:
    clean = view_mode_profile("CLEAN_LIVE")
    inspector = view_mode_profile("INSPECTOR")
    supply = view_mode_profile("supply-demand")

    assert clean["layer_visibility"]["historical_replay"] is False
    assert clean["layer_visibility"]["diagnostics"] is False
    assert clean["layer_visibility"]["prediction_path"] is False
    assert supply["mode"] == "SUPPLY_DEMAND"
    assert supply["layer_visibility"]["supply_demand"] is True
    assert supply["layer_visibility"]["trigger_zones"] is False
    assert inspector["layer_visibility"]["diagnostics"] is True
    assert inspector["allow_selection"] is True


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
    visible_labels = [overlay["label_bounds"] for overlay in laid_out if not overlay["label_hidden"]]

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
