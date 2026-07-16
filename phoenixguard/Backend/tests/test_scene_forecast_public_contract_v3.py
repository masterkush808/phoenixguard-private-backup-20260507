from __future__ import annotations

from typing import Any, Callable, cast

from phoenixguard.mobile_api import live_state_v3
from phoenixguard.mobile_api.live_state_v3 import compact_session_payload
from phoenixguard.mobile_api.operator_workspace_v1 import build_operator_workspace_v1


_compact_scene_forecast_contribution = cast(
    Callable[[dict[str, Any]], dict[str, Any]],
    getattr(live_state_v3, "_compact_scene_forecast_contribution"),
)
_dashboard_overlay_object = cast(
    Callable[..., dict[str, Any]],
    getattr(live_state_v3, "_dashboard_overlay_object"),
)
_study_overlay_objects = cast(
    Callable[..., list[dict[str, Any]]],
    getattr(live_state_v3, "_study_overlay_objects"),
)
_two_candle_and_lstm_payloads = cast(
    Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]],
    getattr(live_state_v3, "_two_candle_and_lstm_payloads"),
)


def _scene_contribution(*, frame_id: int = 14) -> dict[str, Any]:
    anchor_x = 0.52
    anchor_y = 0.50
    step_x = 0.025
    buy_path = [
        [round(anchor_x + step * step_x, 6), round(anchor_y - step * 0.006, 6)]
        for step in range(13)
    ]
    sell_path = [
        [round(anchor_x + step * step_x, 6), round(anchor_y + step * 0.005, 6)]
        for step in range(13)
    ]
    bull_path = [
        [round(anchor_x + step * step_x, 6), round(anchor_y - step * 0.008, 6)]
        for step in range(13)
    ]

    def candles_for(path: list[list[float]], side: str) -> list[dict[str, Any]]:
        candles: list[dict[str, Any]] = []
        for step in range(1, 13):
            open_y = path[step - 1][1]
            close_y = path[step][1]
            candles.append(
                {
                    "step": step,
                    "x_norm": path[step][0],
                    "open_y_norm": open_y,
                    "high_y_norm": round(min(open_y, close_y) - 0.003, 6),
                    "low_y_norm": round(max(open_y, close_y) + 0.003, 6),
                    "close_y_norm": close_y,
                    "movement_side": side,
                    "body_bias": side,
                    "direction_conflict": False,
                }
            )
        return candles

    candles = candles_for(buy_path, "BUY")
    return {
        "schema_version": "PG_CHRONOS_SCENE_FORECAST_CONTRIBUTION_V3",
        "provider": "CHRONOS_2_LOCAL",
        "provider_status": "AVAILABLE",
        "scene_feature_audit": {
            "consumed_field_count": 162,
            "missing_field_count": 11,
            "rejected_field_count": 317,
            "consumed_fields": ["candles", "decision_kernel.side"],
            "missing_fields": ["projection.slope"],
            "rejected_fields": [
                {"path": "decision_kernel.future_outcome", "reason": "future_outcome"}
            ],
            "source_presence": {
                "candles": True,
                "decision_kernel": True,
                "projection": False,
            },
            "causal_exclusions": {
                "forming_candles": 1,
                "history_rows_outside_window": 0,
                "projected_geometry_is_feature": False,
                "future_outcome_fields_are_feature": False,
            },
        },
        "frame_id": frame_id,
        "display_frame_id": frame_id,
        "forecast_computed_frame_id": frame_id,
        "source_forecast_frame_id": frame_id,
        "geometry_projected_frame_id": frame_id,
        "geometry_frame_match_verified": True,
        "geometry_reprojected_from_cache": False,
        "detector_coverage_rebase_applied": False,
        "cache_replaced_for_detector_coverage_rebase": False,
        "geometry_projection_provenance": {
            "status": "COMPUTED_CURRENT_FRAME",
            "source_forecast_frame_id": frame_id,
            "source_geometry_frame_id": frame_id,
            "projected_frame_id": frame_id,
            "verified": True,
            "pointwise_clipping_applied": False,
        },
        "fresh": True,
        "forecast_available": True,
        "forecast_quality_status": "READY",
        "path_side": "BUY",
        "path_confidence_status": "READY",
        "path_confidence": 0.81,
        "production_authorized": True,
        "artifact_production_gate_passed": True,
        "selective_authorized": True,
        "forecast_id": "eurusd-m5-close-447",
        "forecast_revision": 19,
        "closed_candle_key": "eurusd-m5-447",
        "closed_candle_sequence": 447,
        "belief_update": {
            "status": "REVERSAL_PENDING",
            "active_side": "BUY",
            "candidate_side": "SELL",
            "pending_side": "SELL",
            "pending_count": 1,
            "required_count": 2,
            "revision": 8,
            "closed_candle_key": "eurusd-m5-447",
            "closed_candle_sequence": 447,
        },
        "change_probability": 0.68,
        "line_points": buy_path,
        "forecast_candles": candles,
        "forecast_scenarios": [
            {
                "side": "BUY",
                "role": "base",
                "label": "BUY COMMITTED",
                "probability": 0.58,
                "probability_calibrated": True,
                "selected": True,
                "raw_selected": False,
                "candidate": False,
                "line_points": buy_path,
                "forecast_candles": candles,
            },
            {
                "side": "SELL",
                "role": "bear",
                "label": "SELL UNDER REVIEW",
                "probability": 0.30,
                "probability_calibrated": True,
                "selected": False,
                "raw_selected": True,
                "candidate": True,
                "line_points": sell_path,
                "forecast_candles": candles_for(sell_path, "SELL"),
            },
            {
                "side": "BUY",
                "role": "bull",
                "label": "BULL PATH",
                "probability": 0.12,
                "probability_calibrated": True,
                "selected": False,
                "raw_selected": False,
                "candidate": False,
                "line_points": bull_path,
                "forecast_candles": candles_for(bull_path, "BUY"),
            },
        ],
        "forecast_anchor": {
            "x_norm": anchor_x,
            "y_norm": anchor_y,
            "verified_latest_close": True,
            "source": "TRACKER_LATEST_CLOSED_CANDLE",
        },
        "forecast_band_points": [],
        "interval": {
            "status": "UNAVAILABLE",
            "calibrated": False,
            "method": "UNAVAILABLE",
        },
        "interpretation": "Twelve-event causal scene forecast.",
    }


def _session_with_scene() -> dict[str, Any]:
    scene = _scene_contribution()
    return {
        "session_id": "scene-public-contract",
        "display_frame_id": 14,
        "frame_id": 14,
        "model_vote_frame_id": 14,
        "scene_forecast_contribution": scene,
        "latest_signal": {
            "lstm_contribution": {
                "schema_version": "PG_LSTM_CANDLE_SEQUENCE_CONTRIBUTION_V3",
                "frame_id": 14,
                "forecast_available": True,
                "path_side": "SELL",
                "confidence": 0.99,
            }
        },
    }


def test_scene_contribution_precedes_legacy_alias_and_publishes_atomic_bundle() -> None:
    session = _session_with_scene()

    _two_candle, selected = _two_candle_and_lstm_payloads(session)
    overlays = _study_overlay_objects(
        session,
        {},
        selected,
        frame_id=14,
        sequence_id="scene-sequence-14",
        chart_transform_id="chart-transform-14",
        now_ms=100_000,
    )

    assert selected["schema_version"] == "PG_CHRONOS_SCENE_FORECAST_CONTRIBUTION_V3"
    assert selected["_source_frame_id"] == selected["_display_frame_id"] == 14
    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay["forecast_engine"] == "SCENE_FORECASTER_V3"
    assert overlay["forecast_provider"] == "CHRONOS_2_LOCAL"
    assert overlay["forecast_provider_status"] == "AVAILABLE"
    assert overlay["scene_feature_audit"]["consumed_field_count"] == 162
    assert overlay["scene_feature_audit"]["rejected_field_count"] == 317
    assert overlay["geometry_frame_match_verified"] is True
    assert overlay["geometry_projected_frame_id"] == 14
    assert overlay["geometry_projection_provenance"]["verified"] is True
    assert overlay["label"].startswith("SCENE FORECAST E1-E12")
    assert overlay["role"] == "scene_forecast_composite_no_edge"
    assert overlay["trade_authorization_status"] == "NO_EDGE"
    assert overlay["belief_state"] == "REVERSAL_PENDING"
    assert overlay["committed_side"] == "BUY"
    assert overlay["candidate_side"] == "SELL"
    assert overlay["confirmation_events"] == 1
    assert overlay["required_events"] == 2
    assert len(overlay["line_points"]) == 13
    assert len(overlay["forecast_candles"]) == 12
    assert len(overlay["forecast_scenarios"]) == 3

    compact = compact_session_payload(session)
    assert compact["scene_forecast_contribution"]["forecast_id"] == "eurusd-m5-close-447"
    assert compact["scene_forecast_contribution"]["geometry_frame_match_verified"] is True
    assert compact["scene_forecast_contribution"]["geometry_projected_frame_id"] == 14
    selected["belief_tracker_checkpoint"] = {"private_revisions": list(range(100))}
    public_boundary = _compact_scene_forecast_contribution(selected)
    assert "belief_tracker_checkpoint" not in public_boundary
    assert public_boundary["scene_feature_audit"]["consumed_field_count"] == 162

    dashboard_row = _dashboard_overlay_object(overlay, compact=True)
    assert dashboard_row["forecast_engine"] == "SCENE_FORECASTER_V3"
    assert dashboard_row["belief_state"] == "REVERSAL_PENDING"
    assert dashboard_row["committed_side"] == "BUY"
    assert dashboard_row["candidate_side"] == "SELL"
    assert dashboard_row["geometry_frame_match_verified"] is True
    assert dashboard_row["geometry_projected_frame_id"] == 14


def test_operator_exposes_committed_scene_revision_without_flip_or_authority() -> None:
    session = _session_with_scene()
    _two_candle, selected = _two_candle_and_lstm_payloads(session)
    overlay = _study_overlay_objects(
        session,
        {},
        selected,
        frame_id=14,
        sequence_id="scene-sequence-14",
        chart_transform_id="chart-transform-14",
        now_ms=100_000,
    )[0]
    session.update(
        {
            "state_version": 14,
            "tracking_enabled": True,
            "tracking_summary": {
                "detected_market": "EUR/USD",
                "detected_timeframe": "M5",
                "last_capture_epoch": 99.0,
            },
            "decision_command_center": {
                "fresh": True,
                "freshness_status": "PASS",
                "created_epoch": 99.0,
                "valid_until_epoch": 120.0,
                "selected_side": "BUY",
                "execution_packet_present": False,
                "current_movement": {
                    "side": "BUY",
                    "state": "ACTIVE",
                    "observed_at": 99.0,
                    "frame_id": 14,
                    "confidence": 0.80,
                },
                "pressure_event": {
                    "side": "BUY",
                    "state": "ACTIVE",
                    "observed_at": 99.0,
                    "frame_id": 14,
                    "confidence": 0.76,
                },
                "execution_opportunity_window_v3": {
                    "state": "WAIT",
                    "integrity_valid": False,
                },
            },
            "overlays": {"objects": [overlay]},
        }
    )

    workspace = build_operator_workspace_v1(session, now_epoch=100.0)
    forecast = cast(dict[str, Any], workspace["forecast"])
    public_overlay = cast(list[dict[str, Any]], workspace["overlays"])[0]
    permission = cast(dict[str, Any], workspace["permission"])

    assert forecast["forecast_engine"] == "SCENE_FORECASTER_V3"
    assert forecast["forecast_provider"] == "CHRONOS_2_LOCAL"
    assert forecast["forecast_provider_status"] == "AVAILABLE"
    assert forecast["scene_feature_audit"]["missing_field_count"] == 11
    assert forecast["direction"] == "BUY"
    assert forecast["belief_state"] == "REVERSAL_PENDING"
    assert forecast["committed_side"] == "BUY"
    assert forecast["candidate_side"] == "SELL"
    assert forecast["confirmation_events"] == 1
    assert forecast["required_events"] == 2
    assert "BUY remains the committed forecast" in forecast["summary"]
    assert "SELL is under review" in forecast["summary"]
    assert public_overlay["label"].startswith("Scene forecaster events")
    assert public_overlay["forecast_status"] == "NO_EDGE"
    assert public_overlay["forecast_authorized"] is False
    assert public_overlay["forecast_engine"] == "SCENE_FORECASTER_V3"
    assert public_overlay["geometry_frame_match_verified"] is True
    assert public_overlay["geometry_projected_frame_id"] == 14
    assert public_overlay["scene_feature_audit"]["causal_exclusions"] == {
        "forming_candles": 1,
        "history_rows_outside_window": 0,
        "projected_geometry_is_feature": False,
        "future_outcome_fields_are_feature": False,
    }
    assert public_overlay["belief_revision"] == 8
    assert public_overlay["forecast_anchor"]["source"] == "TRACKER_LATEST_CLOSED_CANDLE"
    scenarios = cast(list[dict[str, Any]], public_overlay["forecast_scenarios"])
    candidate = next(row for row in scenarios if row["candidate"])
    assert candidate["side"] == "SELL"
    assert candidate["raw_selected"] is True
    assert permission["action"] == "WAIT"
    assert permission["allowed"] is False


def test_current_scene_path_is_not_stale_when_execution_freshness_is_unknown() -> None:
    """Forecast recency follows its exact frame, not execution authorization."""

    session = _session_with_scene()
    _two_candle, selected = _two_candle_and_lstm_payloads(session)
    overlay = _study_overlay_objects(
        session,
        {},
        selected,
        frame_id=14,
        sequence_id="scene-sequence-14",
        chart_transform_id="chart-transform-14",
        now_ms=100_000,
    )[0]
    session.update(
        {
            "state_version": 14,
            "tracking_enabled": True,
            "last_capture_epoch": 99.0,
            "tracking_summary": {
                "detected_market": "EUR/USD",
                "detected_timeframe": "M5",
                "last_capture_epoch": 99.0,
            },
            # No execution command is available because this diagnostic
            # forecast has NO EDGE.  That must close permission without
            # relabeling exact-frame model geometry as an older forecast.
            "overlays": {"objects": [overlay]},
        }
    )

    workspace = build_operator_workspace_v1(session, now_epoch=100.0)
    freshness = cast(dict[str, Any], workspace["freshness"])
    forecast = cast(dict[str, Any], workspace["forecast"])
    public_overlay = cast(list[dict[str, Any]], workspace["overlays"])[0]
    permission = cast(dict[str, Any], workspace["permission"])

    assert freshness["state"] == "UNKNOWN"
    assert forecast["state"] == "CURRENT"
    assert forecast["forecast_status"] == "NO_EDGE"
    assert public_overlay["lifecycle"] == "current"
    assert public_overlay["forecast_status"] == "NO_EDGE"
    assert permission["action"] == "WAIT"
    assert permission["allowed"] is False
