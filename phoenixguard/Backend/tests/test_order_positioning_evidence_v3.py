from __future__ import annotations

from typing import Any

import phoenixguard.decision.order_positioning_evidence_v3 as evidence


def _streaming_session() -> dict[str, Any]:
    return {
        "tracking_summary": {
            "tracked_candles": [
                {"track_id": "candle-1", "center_x_px": 700.0},
                {"track_id": "candle-2", "center_x_px": 720.0},
                {"track_id": "candle-3", "center_x_px": 740.0},
            ]
        }
    }


def test_visual_reaction_window_excludes_the_forming_candle() -> None:
    session = _streaming_session()

    strict = evidence._order_reaction_window(session, chart_width=1000.0)
    visual = evidence._order_reaction_window(
        session,
        chart_width=1000.0,
        allow_visual_closed_fallback=True,
    )

    assert strict == {}
    assert visual["reaction_window_verified"] is True
    assert visual["reaction_window_anchor"] == "LATEST_COMPLETED_CANDLE"
    assert visual["reaction_window_anchor_id"] == "candle-2"
    assert visual["reaction_window_anchor_source"] == "PENULTIMATE_VISIBLE_CANDLE"
    assert visual["forming_candle_excluded"] is True
    assert visual["reaction_window_origin_x_norm"] == 0.72
    assert visual["reaction_window_step_x_norm"] == 0.02


def test_current_reference_map_uses_streaming_closed_candle_geometry(
    monkeypatch: Any,
) -> None:
    session = _streaming_session()
    source = {
        "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
        "track_id": "demand-current",
        "object_id": "demand-current",
        "type": "ORDER_BLOCK",
        "side": "BUY",
        "frame_id": 42,
        "sequence_id": "sequence-current",
        "chart_transform_id": "transform-current",
        "broker_source_lock_id": "lock-current",
        "coordinate_mode": "CHART_NORMALIZED",
        "bounds": [0.2, 0.6, 0.5, 0.7],
        "confidence": 0.91,
        "truth_score": 0.9,
        "lifecycle_state": "ACTIVE",
        "anchor_evidence_status": "VALID",
        "anchor_evidence": {"valid": True},
        "anchor_quality": {
            "score": 0.92,
            "has_candle_anchor": True,
            "has_sequence_anchor": True,
            "inside_plot_area": True,
            "matches_symbol_timeframe": True,
            "matches_selector_fingerprint": True,
            "chart_transform_valid": True,
        },
    }
    monkeypatch.setattr(evidence, "_order_reference_geometry", lambda _session: (1000.0, 500.0, 0.006))
    monkeypatch.setattr(evidence, "_order_reference_current_y", lambda _session, *, chart_height: (0.4, "FORMING_LIVE_CANDLE"))
    monkeypatch.setattr(evidence, "_stable_broker_source_lock_id", lambda _session: "lock-current")
    monkeypatch.setattr(evidence, "_current_positioning_frame_id", lambda _session: 42)
    monkeypatch.setattr(evidence, "_identity", lambda _session: {"pair": "USD/CAD OTC", "timeframe": "M5"})
    monkeypatch.setattr(evidence, "order_positioning_evidence_rows_v3", lambda _session: [source])

    reference_map = evidence.build_current_order_reference_map_v3(session)

    assert reference_map["status"] == "READY"
    assert reference_map["observational_only"] is True
    assert reference_map["execution_authority"] == "NONE"
    assert reference_map["reaction_window"]["reaction_window_anchor_id"] == "candle-2"
    assert reference_map["reaction_window"]["forming_candle_excluded"] is True
    assert reference_map["reference_count"] == 1
    assert reference_map["rows"][0]["order_kind"] == "BUY_LIMIT"
    assert reference_map["rows"][0]["bounds"] == [0.72, 0.6, 1.0, 0.7]
