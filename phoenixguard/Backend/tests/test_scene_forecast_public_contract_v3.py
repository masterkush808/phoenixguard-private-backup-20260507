from __future__ import annotations

import json
from typing import Any, cast

from phoenixguard.mobile_api.operator_workspace_v1 import build_operator_workspace_v1


RETIRED_PUBLIC_FAMILIES = {
    "two_candle",
    "scene_forecaster",
    "lstm",
    "prediction",
}


def _session_with_internal_model_rows() -> dict[str, Any]:
    model_rows = [
        {
            "overlay_id": "two-candle-internal",
            "type": "TWO_CANDLE_STUDY",
            "layer": "active_council_decision",
            "bounds": [10, 20, 30, 40],
            "frame_id": 14,
        },
        {
            "overlay_id": "lstm-internal",
            "type": "LSTM_STUDY",
            "role": "lstm_forecast_composite_authorized",
            "layer": "prediction_path",
            "bounds": [35, 20, 55, 40],
            "frame_id": 14,
            "forecast_status": "AUTHORIZED",
            "forecast_authorized": True,
            "selective_authorized": True,
            "line_points": [[0.55, 0.50], [0.72, 0.41]],
        },
        {
            "overlay_id": "scene-internal",
            "type": "SCENE_FORECAST_STUDY",
            "role": "scene_forecast_composite_authorized",
            "layer": "prediction_path",
            "bounds": [58, 20, 82, 40],
            "frame_id": 14,
            "forecast_status": "AUTHORIZED",
            "forecast_authorized": True,
            "selective_authorized": True,
            "line_points": [[0.58, 0.42], [0.82, 0.33]],
        },
        {
            "overlay_id": "prediction-internal",
            "type": "PREDICTION_PATH",
            "layer": "prediction_path",
            "bounds": [58, 42, 82, 58],
            "frame_id": 14,
            "line_points": [[0.58, 0.42], [0.82, 0.58]],
        },
    ]
    return {
        "session_id": "retired-model-boundary",
        "state_version": 14,
        "display_frame_id": 14,
        "tracking_enabled": True,
        "last_capture_epoch": 99.0,
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "last_capture_epoch": 99.0,
        },
        "overlays": {
            "objects": model_rows
            + [
                {
                    "overlay_id": "council-current",
                    "type": "MODEL_COUNCIL_MARKER",
                    "layer": "active_council_decision",
                    "bounds": [20, 50, 40, 70],
                    "frame_id": 14,
                }
            ]
        },
    }


def test_internal_model_paths_never_enter_the_public_overlay_contract() -> None:
    workspace = build_operator_workspace_v1(
        _session_with_internal_model_rows(),
        now_epoch=100.0,
    )
    public_overlays = cast(list[dict[str, Any]], workspace["overlays"])

    assert "forecast" not in workspace
    assert RETIRED_PUBLIC_FAMILIES.isdisjoint(
        {str(row.get("family")) for row in public_overlays}
    )
    assert {str(row.get("id")) for row in public_overlays} == {"council-current"}
    assert public_overlays[0]["family"] == "council"
    assert cast(dict[str, Any], workspace["permission"])["allowed"] is False


def test_authorization_flags_cannot_resurrect_a_retired_model_path() -> None:
    session = _session_with_internal_model_rows()
    rows = cast(list[dict[str, Any]], cast(dict[str, Any], session["overlays"])["objects"])
    for row in rows:
        if row["overlay_id"] == "council-current":
            continue
        row.update(
            {
                "role": "authorized_public_path",
                "trade_authorization_status": "AUTHORIZED",
                "forecast_status": "AUTHORIZED",
                "forecast_authorized": True,
                "production_authorized": True,
                "selective_authorized": True,
            }
        )

    workspace = build_operator_workspace_v1(session, now_epoch=100.0)
    public_overlays = cast(list[dict[str, Any]], workspace["overlays"])

    assert {str(row.get("id")) for row in public_overlays} == {"council-current"}
    assert RETIRED_PUBLIC_FAMILIES.isdisjoint(
        {str(row.get("family")) for row in public_overlays}
    )


def test_public_workspace_contains_no_fixed_model_path_payload() -> None:
    workspace = build_operator_workspace_v1(
        _session_with_internal_model_rows(),
        now_epoch=100.0,
    )
    serialized = json.dumps(workspace).lower()

    assert "forecast" not in workspace
    for private_path_field in (
        "forecast_scenarios",
        "forecast_candles",
        "forecast_band_points",
        "future_blocks",
        "trajectory_scenarios",
    ):
        assert private_path_field not in serialized
