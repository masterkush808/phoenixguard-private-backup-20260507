from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest
from fastapi.testclient import TestClient

import phoenixguard.mobile_api.app as mobile_app
from phoenixguard.mobile_api.operator_workspace_v1 import (
    OPERATOR_WORKSPACE_SCHEMA_VERSION,
    build_operator_workspace_v1,
)


TOP_LEVEL_KEYS = {
    "schema_version",
    "session_id",
    "revision",
    "market",
    "tracking",
    "freshness",
    "current_move",
    "forecast",
    "permission",
    "pressure_event",
    "surface",
    "overlays",
    "history",
}


_FrameId = int | str | None


class _MarketView(TypedDict):
    symbol: str
    timeframe: str


class _TrackingView(TypedDict):
    active: bool
    state: str
    updated_at: float | None
    history_count: int


class _FreshnessView(TypedDict):
    state: str
    label: str
    observed_at: float | None
    valid_until: float | None
    age_seconds: float | None


class _MovementView(TypedDict):
    direction: str
    state: str
    confidence: float | None
    observed_at: float | None
    started_at: float | None
    ended_at: float | None
    frame_id: _FrameId
    summary: str


class _ForecastView(TypedDict):
    direction: str
    state: str
    confidence: float | None
    horizon_seconds: float | None
    summary: str


class _PermissionView(TypedDict):
    action: str
    allowed: bool
    side: str
    message: str
    next_condition: str
    expires_at: float | None
    window_open: bool
    valid_for_seconds: float | None
    window_label: str
    entry_location: str
    entry_guidance: str


class _SurfaceView(TypedDict):
    primary_url: str
    primary_space: str
    fallback_url: str
    fallback_space: str
    focus_url: str
    overlay_viewport: _OverlayViewportView
    frame_id: _FrameId
    updated_at: float | None


class _OverlayViewportView(TypedDict):
    source_space: str
    target_space: str
    coordinate_units: str
    bounds: list[float]


class _OverlayView(TypedDict):
    id: str
    type: str
    side: str
    group: str
    family: str
    layer: str
    label: str
    label_hidden: bool
    bounds: list[float]
    points: list[list[float]]
    line_points: list[list[float]]
    confidence: float | None
    lifecycle: str
    frame_id: _FrameId
    coordinate_space: str
    coordinate_units: str
    forecast_role: NotRequired[str]
    forecast_status: NotRequired[str]
    forecast_authorized: NotRequired[bool]
    horizon_unit: NotRequired[str]
    clock_time_assumption: NotRequired[str]
    uncertainty_level: NotRequired[float]


class _HistoryView(TypedDict):
    observed_at: float | None
    direction: str
    state: str
    summary: str
    frame_id: _FrameId


class _OperatorWorkspaceView(TypedDict):
    schema_version: str
    session_id: str
    revision: int
    market: _MarketView
    tracking: _TrackingView
    freshness: _FreshnessView
    current_move: _MovementView
    forecast: _ForecastView
    permission: _PermissionView
    pressure_event: _MovementView
    surface: _SurfaceView
    overlays: list[_OverlayView]
    history: list[_HistoryView]


def _build_workspace(
    payload: Mapping[str, object],
    *,
    now_epoch: float,
) -> _OperatorWorkspaceView:
    return cast(
        _OperatorWorkspaceView,
        build_operator_workspace_v1(payload, now_epoch=now_epoch),
    )


def _mutable_mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _fresh_payload(*, side: str = "BUY", now: float = 100.0) -> dict[str, object]:
    return {
        "session_id": "desk-live-1",
        "state_version": 14,
        "display_frame_id": 14,
        "tracking_enabled": True,
        "tracking_summary": {
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "last_capture_epoch": now - 1,
        },
        "execution_controls": {"live_execution_enabled": True},
        "decision_command_center": {
            "fresh": True,
            "freshness_status": "PASS",
            "created_epoch": now - 1,
            "valid_until_epoch": now + 20,
            "selected_side": side,
            "execution_packet_present": True,
            "current_movement": {
                "side": side,
                "state": "ACTIVE",
                "observed_at": now - 1,
                "started_at": now - 4,
                "frame_id": 14,
                "confidence": 0.84,
            },
            "pressure_event": {
                "side": side,
                "state": "ACTIVE",
                "observed_at": now - 1,
                "frame_id": 14,
                "confidence": 0.79,
            },
            "forecast": {
                "frame_id": 14,
                "side": side,
                "confidence": 0.73,
                "duration_sec": 300,
            },
            "execution_opportunity_window_v3": {
                "state": "OPEN",
                "valid_until_epoch": now + 720,
                "integrity_valid": True,
            },
        },
    }


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, nested in mapping.items():
            keys.add(str(key))
            keys.update(_all_keys(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast(Sequence[object], value)
        for nested in sequence:
            keys.update(_all_keys(nested))
    return keys


def test_operator_workspace_is_a_strict_sanitized_contract() -> None:
    payload = _fresh_payload()
    payload.update(
        {
            "provider_status": {"provider": "internal", "source_path": r"C:\secret\state.json"},
            "frame_timing_trace_v3": {"pipeline_latency_ms": 14},
            "model_council_result": {"packet_id": "exec-secret", "reason": "PRIVATE_GATE"},
            "shooter_state": {"armed": True},
            "last_chart_path": r"C:\secret\chart.png",
            "execution_packet": {
                "packet_type": "PG_EXECUTION_PACKET_V3",
                "packet_id": "exec-secret",
                "valid_until_epoch": 140.0,
            },
            "overlays": {
                "objects": [
                    {
                        "overlay_id": "zone-1",
                        "type": "DEMAND_ZONE",
                        "side": "BUY",
                        "layer": "supply_demand",
                        "bounds": [1, 2, 30, 40],
                        "frame_id": 14,
                        "coordinate_mode": "CHART_IMAGE_SPACE",
                        "source_agent": "private-agent",
                        "source_path": r"C:\secret\overlay.json",
                        "reason": "internal rationale",
                    }
                ]
            },
        }
    )

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert set(workspace) == TOP_LEVEL_KEYS
    assert workspace["schema_version"] == OPERATOR_WORKSPACE_SCHEMA_VERSION
    forbidden_keys = {
        "provider_status",
        "frame_timing_trace_v3",
        "model_council_result",
        "shooter_state",
        "packet_id",
        "source_agent",
        "source_path",
        "reason",
        "coordinate_mode",
    }
    assert _all_keys(workspace).isdisjoint(forbidden_keys)
    serialized = json.dumps(workspace)
    assert r"C:\\secret" not in serialized
    assert "exec-secret" not in serialized
    assert "private-agent" not in serialized


def test_legacy_tracker_session_route_redacts_backend_internals() -> None:
    session_id = "legacy-public-boundary"

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {
                "session_id": session_id,
                "status": "running",
                "tracking_enabled": True,
                "display_frame_id": 27,
                "last_chart_path": r"C:\private\chart.png",
                "event_log_path": r"C:\private\events.jsonl",
                "last_display_surface_signature": "surface-secret",
                "locked_window": {
                    "hwnd": 987654,
                    "title": "Broker window",
                },
                "latest_signal": {
                    "action": "HOLD",
                    "summary": "Waiting for confirmation.",
                    "source_path": r"C:\private\signal.json",
                    "normalized_features": [0.1, 0.9],
                    "study_signature": "study-secret",
                },
                "forecast_snapshot_v3": {
                    "features": [{"momentum": 0.8}],
                    "raw_model_logits": [0.2, 0.8],
                },
            }

    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}")

    assert response.status_code == 200
    payload = cast(dict[str, object], response.json())
    assert payload["session_id"] == session_id
    assert payload["display_frame_id"] == 27
    latest_signal = _mutable_mapping(payload["latest_signal"])
    assert latest_signal == {
        "action": "HOLD",
        "summary": "Waiting for confirmation.",
    }
    locked_window = _mutable_mapping(payload["locked_window"])
    assert locked_window == {"title": "Broker window"}
    private_fragments = ("feature", "hwnd", "path", "signature")
    assert not any(
        fragment in key.lower()
        for key in _all_keys(payload)
        for fragment in private_fragments
    )
    serialized = json.dumps(payload)
    assert r"C:\\private" not in serialized
    assert "surface-secret" not in serialized
    assert "study-secret" not in serialized


def test_fresh_current_authority_can_grant_buy_now() -> None:
    workspace = _build_workspace(_fresh_payload(side="BUY"), now_epoch=100.0)

    assert workspace["freshness"]["state"] == "FRESH"
    assert workspace["current_move"]["direction"] == "BUY"
    assert workspace["current_move"]["state"] == "ACTIVE"
    assert workspace["permission"] == {
        "action": "BUY_NOW",
        "allowed": True,
        "side": "BUY",
        "message": (
            "A verified buy entry window is open. Aim for a lower price inside the "
            "verified demand or retest area; do not chase highs."
        ),
        "next_condition": (
            "Use only the current verified window; stop and wait if live truth changes."
        ),
        "expires_at": 820.0,
        "window_open": True,
        "valid_for_seconds": 720.0,
        "window_label": "Open · 12m 00s remaining",
        "entry_location": "LOWER_PRICE",
        "entry_guidance": (
            "Aim for a lower price inside the verified demand or retest area; do not "
            "chase highs."
        ),
    }


def test_open_setup_window_without_current_packet_waits_for_refresh() -> None:
    payload = _fresh_payload(side="SELL")
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["permission"] == {
        "action": "WAIT",
        "allowed": False,
        "side": "NEUTRAL",
        "message": (
            "Wait. The setup window remains open, but current-frame permission is "
            "refreshing."
        ),
        "next_condition": (
            "Wait for fresh current-frame permission; the setup window alone is not "
            "permission to enter."
        ),
        "expires_at": 820.0,
        "window_open": True,
        "valid_for_seconds": 720.0,
        "window_label": "Open · 12m 00s remaining",
        "entry_location": "HIGHER_PRICE",
        "entry_guidance": (
            "Aim for a higher price inside the verified supply or retest area; do not "
            "chase lows."
        ),
    }


def test_reused_absolute_window_anchor_remains_open_without_renewing_deadline() -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity["anchor_reused"] = True

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["permission"]["allowed"] is True
    assert workspace["permission"]["window_open"] is True
    assert workspace["permission"]["expires_at"] == 820.0


def test_verified_sell_permission_requires_a_high_entry_without_chasing_lows() -> None:
    workspace = _build_workspace(_fresh_payload(side="SELL"), now_epoch=100.0)

    permission = workspace["permission"]
    assert permission["action"] == "SELL_NOW"
    assert permission["allowed"] is True
    assert permission["entry_location"] == "HIGHER_PRICE"
    assert permission["window_open"] is True
    assert permission["valid_for_seconds"] == 720.0
    assert "higher price inside the verified supply or retest area" in permission[
        "entry_guidance"
    ]
    assert "do not chase lows" in permission["message"]


def test_duplicate_visual_wait_is_publicly_waiting_and_never_fresh() -> None:
    payload = _fresh_payload(side="BUY")
    payload["visual_observation_v3"] = {
        "status": "WAITING_FOR_NEW_FRAME",
        "message": "Waiting for a new broker frame.",
        "new_visual_evidence": False,
        "last_observed_epoch": 90.0,
        "attempted_epoch": 100.0,
        "duplicate_study_count": 4,
        "surface_signature": "private-signature",
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["freshness"] == {
        "state": "WAITING",
        "label": "Waiting for a new broker frame.",
        "observed_at": 90.0,
        "valid_until": None,
        "age_seconds": 10.0,
    }
    assert workspace["tracking"]["state"] == "UPDATING"
    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["permission"]["allowed"] is False
    assert workspace["forecast"]["state"] == "STALE"
    serialized = json.dumps(workspace)
    assert "private-signature" not in serialized
    assert "duplicate_study_count" not in serialized


def test_duplicate_wait_keeps_last_forecast_nonzero_but_diagnostic_only() -> None:
    payload = _fresh_payload(side="SELL")
    command = _mutable_mapping(payload["decision_command_center"])
    command.pop("forecast")
    payload["visual_observation_v3"] = {
        "status": "WAITING_FOR_NEW_FRAME",
        "message": "Waiting for a new broker frame.",
        "new_visual_evidence": False,
        "last_observed_epoch": 99.0,
    }
    payload["forecast_snapshot_v3"] = {
        "schema_version": "PG_FORECAST_SNAPSHOT_V3",
        "source_frame_id": 14,
        "observed_epoch": 99.0,
        "stale": True,
        "diagnostic_only": True,
        "lstm_contribution": {
            "frame_id": 14,
            "stale": True,
            "diagnostic_only": True,
            "forecast_available": True,
            "path_side": "SELL",
            "confidence": 0.77,
        },
    }
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "lstm-last-valid",
                "type": "LSTM_STUDY",
                "side": "HOLD",
                "layer": "prediction_path",
                "role": "lstm_candle_event_path_stale_diagnostic",
                "bounds": [0.66, 0.32, 0.91, 0.54],
                "line_points": [[0.66, 0.38], [0.91, 0.51]],
                "confidence": 0.77,
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "lstm-last-valid-band",
                "type": "LSTM_STUDY",
                "side": "HOLD",
                "layer": "prediction_path",
                "role": "lstm_forecast_90_band_stale_diagnostic",
                "bounds": [0.66, 0.30, 0.91, 0.56],
                "points": [
                    [0.66, 0.36],
                    [0.91, 0.49],
                    [0.91, 0.53],
                    [0.66, 0.40],
                ],
                "confidence": 0.77,
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            }
        ]
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["forecast"]["direction"] == "SELL"
    assert workspace["forecast"]["confidence"] == 0.77
    assert workspace["forecast"]["state"] == "STALE"
    assert "last valid model outlook" in workspace["forecast"]["summary"].lower()
    assert "diagnostic only" in workspace["forecast"]["summary"].lower()
    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["permission"]["allowed"] is False
    assert len(workspace["overlays"]) == 2
    by_family = {row["family"]: row for row in workspace["overlays"]}
    assert set(by_family) == {"prediction", "lstm"}
    assert by_family["prediction"].get("forecast_role") == "center"
    assert by_family["lstm"].get("forecast_role") == "band_90"
    assert all(
        row["lifecycle"] == "stale_diagnostic"
        and row.get("forecast_status") == "STALE"
        and row.get("forecast_authorized") is False
        for row in workspace["overlays"]
    )


@pytest.mark.parametrize("bad_frame", [None, 13])
def test_forecast_without_exact_current_frame_fails_closed(
    bad_frame: int | None,
) -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    forecast = _mutable_mapping(command["forecast"])
    if bad_frame is None:
        forecast.pop("frame_id")
    else:
        forecast["frame_id"] = bad_frame

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["forecast"] == {
        "direction": "NEUTRAL",
        "state": "UNKNOWN",
        "confidence": None,
        "horizon_seconds": None,
        "summary": "No reliable next direction is confirmed.",
    }


def test_mismatched_explicit_forecast_does_not_mask_aligned_model_forecast() -> None:
    payload = _fresh_payload(side="SELL")
    command = _mutable_mapping(payload["decision_command_center"])
    command["forecast"] = {
        "frame_id": 13,
        "side": "SELL",
        "confidence": 0.99,
    }
    payload["forecast_snapshot_v3"] = {
        "source_frame_id": 14,
        "high_frequency_forecast": {
            "frame_id": 14,
            "direction": "BUY",
            "confidence": 0.71,
        },
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["forecast"]["direction"] == "BUY"
    assert workspace["forecast"]["state"] == "CURRENT"
    assert workspace["forecast"]["confidence"] == 0.71


@pytest.mark.parametrize("forecast_status", ["AUTHORIZED", "NO_EDGE"])
def test_current_center_path_restores_forecast_when_raw_projection_races(
    forecast_status: str,
) -> None:
    payload = _fresh_payload(side="SELL")
    command = _mutable_mapping(payload["decision_command_center"])
    command.pop("forecast")
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "current-lstm-center",
                "type": "LSTM_STUDY",
                "side": "SELL",
                "layer": "prediction_path",
                "role": f"lstm_candle_event_path_{forecast_status.lower()}",
                "bounds": [0.60, 0.30, 0.92, 0.58],
                "line_points": [[0.60, 0.32], [0.76, 0.44], [0.92, 0.54]],
                "confidence": 0.77,
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            }
        ]
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["forecast"]["direction"] == "SELL"
    assert workspace["forecast"]["state"] == "CURRENT"
    assert workspace["forecast"]["confidence"] == 0.77
    assert workspace["forecast"]["horizon_seconds"] is None
    if forecast_status == "NO_EDGE":
        assert "no reliable edge" in workspace["forecast"]["summary"].lower()
        assert "diagnostic only" in workspace["forecast"]["summary"].lower()
    else:
        assert "not entry permission" in workspace["forecast"]["summary"].lower()


def test_current_no_edge_path_completes_partial_raw_forecast_without_hiding_risk_gate() -> None:
    payload = _fresh_payload(side="SELL")
    command = _mutable_mapping(payload["decision_command_center"])
    forecast = _mutable_mapping(command["forecast"])
    forecast.pop("confidence")
    command["execution_packet_present"] = False
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity["state"] = "WAIT"
    opportunity["integrity_valid"] = False
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "current-lstm-center-no-edge",
                "type": "LSTM_STUDY",
                "side": "SELL",
                "layer": "prediction_path",
                "role": "lstm_candle_event_path_no_edge",
                "bounds": [0.60, 0.30, 0.92, 0.58],
                "line_points": [[0.60, 0.32], [0.76, 0.44], [0.92, 0.54]],
                "confidence": 0.77,
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            }
        ]
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["forecast"]["direction"] == "SELL"
    assert workspace["forecast"]["state"] == "CURRENT"
    assert workspace["forecast"]["confidence"] == 0.77
    assert "no reliable edge" in workspace["forecast"]["summary"].lower()
    assert "diagnostic only" in workspace["forecast"]["summary"].lower()
    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["permission"]["allowed"] is False


def test_lstm_path_side_drives_operator_direction_when_body_bias_disagrees() -> None:
    payload = _fresh_payload(side="SELL")
    command = _mutable_mapping(payload["decision_command_center"])
    command.pop("forecast")
    command["execution_packet_present"] = False
    payload["forecast_snapshot_v3"] = {
        "source_frame_id": 14,
        "high_frequency_forecast": {
            "frame_id": 14,
            "direction": "BUY",
            "confidence": 0.91,
            "horizon_seconds": 60,
        },
        "lstm_contribution": {
            "frame_id": 14,
            "side": "BUY",
            "path_side": "SELL",
            "confidence": 0.77,
            "selective_status": "NO_EDGE",
            "selective_authorized": False,
        },
    }
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "current-lstm-center-body-path-disagreement",
                "type": "LSTM_STUDY",
                "side": "SELL",
                "layer": "prediction_path",
                "role": "lstm_candle_event_path_no_edge",
                "bounds": [0.60, 0.30, 0.92, 0.58],
                "line_points": [[0.60, 0.32], [0.76, 0.44], [0.92, 0.54]],
                "confidence": 0.77,
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            }
        ]
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["forecast"]["direction"] == "SELL"
    assert workspace["forecast"]["state"] == "CURRENT"
    assert workspace["forecast"]["confidence"] == 0.77
    assert workspace["forecast"]["horizon_seconds"] is None
    assert "no reliable edge" in workspace["forecast"]["summary"].lower()
    assert "diagnostic only" in workspace["forecast"]["summary"].lower()
    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["permission"]["allowed"] is False


def test_center_path_with_side_geometry_conflict_cannot_restore_forecast() -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command.pop("forecast")
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "contradictory-lstm-center",
                "type": "LSTM_STUDY",
                "side": "BUY",
                "layer": "prediction_path",
                "role": "lstm_candle_event_path_authorized",
                "bounds": [0.60, 0.30, 0.92, 0.58],
                "line_points": [[0.60, 0.32], [0.76, 0.44], [0.92, 0.54]],
                "confidence": 0.91,
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            }
        ]
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["forecast"] == {
        "direction": "NEUTRAL",
        "state": "UNKNOWN",
        "confidence": None,
        "horizon_seconds": None,
        "summary": "No reliable next direction is confirmed.",
    }


def test_safe_overlay_merge_filters_to_one_frame_and_deduplicates_stable_id() -> None:
    merge_rows = cast(
        Callable[..., list[dict[str, object]]],
        getattr(mobile_app, "_merge_safe_operator_overlay_rows"),
    )
    current = [
        {"id": "trend-1", "family": "trendlines", "frame_id": 15},
        {"id": "zone-1", "family": "supply_demand", "frame_id": 15},
    ]
    saved = [
        {"id": "trend-1", "family": "trendlines", "frame_id": 14},
        {"id": "zone-2", "family": "supply_demand", "frame_id": 15},
    ]

    merged = merge_rows(current, saved, frame_id=15)

    assert [row["id"] for row in merged] == ["trend-1", "zone-1", "zone-2"]
    assert {row["frame_id"] for row in merged} == {15}


def test_mixed_frame_operator_snapshot_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "mixed-frame-snapshot"
    source: dict[str, object] = {
        "session_id": session_id,
        "state_version": 15,
        "display_frame_id": 15,
        "chart_frame_id": 15,
        "overlay_frame_id": 15,
        "full_overlay_frame_id": 15,
        "model_vote_frame_id": 15,
        "last_display_surface_signature": "window-15",
        "last_study_surface_signature": "study-15",
        "overlay_source_window_signature": "window-15",
        "overlay_source_study_signature": "study-15",
    }
    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    snapshot_path = (
        tmp_path
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / session_id
        / "operator_overlay_snapshot_v1.json"
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "PG_OPERATOR_OVERLAY_SNAPSHOT_V1",
                "session_id": session_id,
                "lineage": {
                    "frame_id": 15,
                    "chart_frame_id": 15,
                    "overlay_frame_id": 15,
                    "full_overlay_frame_id": 15,
                    "model_vote_frame_id": 15,
                    "display_surface_signature": "window-15",
                    "study_surface_signature": "study-15",
                    "overlay_source_window_signature": "window-15",
                    "overlay_source_study_signature": "study-15",
                    "state_version": 15,
                },
                "overlay_viewport": {
                    "source_space": "chart",
                    "target_space": "window",
                    "coordinate_units": "normalized",
                    "bounds": [0.1, 0.1, 0.9, 0.9],
                },
                "overlays": [
                    {"id": "current", "family": "trendlines", "frame_id": 15},
                    {"id": "old", "family": "trendlines", "frame_id": 14},
                ],
            }
        ),
        encoding="utf-8",
    )
    load_snapshot = cast(
        Callable[[str, Mapping[str, object]], dict[str, object] | None],
        getattr(mobile_app, "_load_operator_overlay_snapshot"),
    )

    assert load_snapshot(session_id, source) is None


@pytest.mark.parametrize("failure", ["stale", "contradictory"])
def test_stale_or_contradictory_pressure_fails_closed(failure: str) -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    if failure == "stale":
        command["fresh"] = False
        command["freshness_status"] = "STALE"
    else:
        command["pressure_event"] = {
            "side": "SELL",
            "state": "ACTIVE",
            "observed_at": 99.0,
            "frame_id": 14,
        }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["permission"]["allowed"] is False
    if failure == "contradictory":
        assert workspace["current_move"]["direction"] == "BUY"
        assert workspace["pressure_event"]["direction"] == "SELL"
        assert workspace["pressure_event"]["state"] == "ACTIVE"


def test_ended_opposite_pressure_is_history_not_a_current_veto() -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command["pressure_event"] = {
        "side": "SELL",
        "state": "ENDED",
        "observed_at": 96.0,
        "ended_at": 98.0,
        "frame_id": 13,
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["pressure_event"]["direction"] == "SELL"
    assert workspace["pressure_event"]["state"] == "ENDED"
    assert workspace["permission"]["action"] == "BUY_NOW"
    assert workspace["permission"]["allowed"] is True
    assert any(
        row["direction"] == "SELL" and row["state"] == "ENDED"
        for row in workspace["history"]
    )


def test_overlay_projection_filters_internal_stale_and_wrong_frame_objects() -> None:
    payload = _fresh_payload()
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "support-1",
                "type": "SUPPORT_TRENDLINE",
                "side": "BUY",
                "layer": "trendlines",
                "line_points": [[1, 2], [3, 4]],
                "frame_id": 14,
                "coordinate_mode": "CHART_IMAGE_SPACE",
                "confidence": 0.8,
            },
            {
                "overlay_id": "window-zone",
                "type": "SUPPLY_ZONE",
                "side": "SELL",
                "layer": "supply_demand",
                "bounds": [10, 12, 40, 50],
                "frame_id": 14,
                "coordinate_mode": "FULL_BROKER_SURFACE",
            },
            {
                "overlay_id": "wrong-frame",
                "type": "DEMAND_ZONE",
                "layer": "supply_demand",
                "bounds": [1, 2, 3, 4],
                "frame_id": 13,
            },
            {
                "overlay_id": "stale-current",
                "type": "TARGET_ZONE_BOX",
                "layer": "target_zones",
                "bounds": [1, 2, 3, 4],
                "frame_id": 14,
                "lifecycle_state": "STALE",
            },
            {
                "overlay_id": "past-entry",
                "type": "REPLAY_ENTRY",
                "layer": "historical_replay",
                "bounds": [2, 3, 4, 5],
                "frame_id": 14,
                "lifecycle_state": "STALE",
            },
            {
                "overlay_id": "unreprojected-past-entry",
                "type": "REPLAY_ENTRY",
                "layer": "historical_replay",
                "bounds": [2, 3, 4, 5],
                "frame_id": 9,
                "lifecycle_state": "STALE",
            },
            {
                "overlay_id": "unframed-current",
                "type": "DEMAND_ZONE",
                "layer": "supply_demand",
                "bounds": [1, 2, 3, 4],
            },
            {
                "overlay_id": "unprojected-plot-zone",
                "type": "DEMAND_ZONE",
                "layer": "supply_demand",
                "bounds": [0.1, 0.2, 0.3, 0.4],
                "frame_id": 14,
                "coordinate_mode": "PLOT_AREA_NORMALIZED",
            },
            {"overlay_id": "broker", "type": "BROKER_CONTROL", "layer": "broker_controls"},
            {"overlay_id": "debug", "type": "DEBUG_RAW_DETECTION", "layer": "diagnostics"},
        ]
    }

    workspace = _build_workspace(payload, now_epoch=100.0)
    overlays = workspace["overlays"]

    assert [overlay["id"] for overlay in overlays] == ["support-1", "window-zone", "past-entry"]
    assert overlays[0]["group"] == "structure"
    assert overlays[0]["family"] == "trendlines"
    assert overlays[0]["layer"] == "trendlines"
    assert overlays[0]["type"] == "trend"
    assert overlays[0]["coordinate_space"] == "chart"
    assert overlays[0]["coordinate_units"] == "pixels"
    assert overlays[1]["group"] == "zones"
    assert overlays[1]["family"] == "supply_demand"
    assert overlays[1]["coordinate_space"] == "window"
    assert overlays[1]["coordinate_units"] == "pixels"
    assert overlays[2]["group"] == "history"
    assert overlays[2]["family"] == "history"
    assert overlays[2]["lifecycle"] == "historical"
    assert overlays[2]["frame_id"] == 14
    assert all("coordinate_mode" not in overlay for overlay in overlays)


def test_canonical_type_controls_family_and_explicit_coordinate_units_win() -> None:
    payload = _fresh_payload()
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "typed-trend",
                "type": "SUPPORT_TRENDLINE",
                # A malformed producer layer must not turn a trendline into a
                # major-swing toggle.
                "layer": "major_swings",
                "line_points": [[0.1, 0.5], [0.8, 0.3]],
                "bounds": [0.1, 0.3, 0.8, 0.5],
                "frame_id": 14,
                "coordinate_mode": "CHART_IMAGE_SPACE",
                "coordinate_units": "normalized",
            },
            {
                "overlay_id": "typed-target",
                "type": "TARGET_ZONE_BOX",
                "bounds": [10, 20, 30, 40],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "coordinate_units": "pixels",
            },
        ]
    }

    by_id = {
        row["id"]: row
        for row in _build_workspace(payload, now_epoch=100.0)["overlays"]
    }

    assert by_id["typed-trend"]["family"] == "trendlines"
    assert by_id["typed-trend"]["coordinate_units"] == "normalized"
    assert by_id["typed-target"]["family"] == "targets"
    assert by_id["typed-target"]["layer"] == "target_zones"
    assert by_id["typed-target"]["coordinate_units"] == "pixels"


def test_only_the_latest_candle_claims_to_be_current_price() -> None:
    payload = _fresh_payload()
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "older-candle",
                "type": "CURRENT_CANDLE",
                "role": "visible_candle",
                "layer": "recent_candles",
                "bounds": [10, 20, 20, 50],
                "frame_id": 14,
            },
            {
                "overlay_id": "latest-candle",
                "type": "CURRENT_CANDLE",
                "role": "current_candle",
                "is_latest_candle": True,
                "layer": "recent_candles",
                "bounds": [25, 20, 35, 55],
                "frame_id": 14,
            },
        ]
    }

    overlays = _build_workspace(payload, now_epoch=100.0)["overlays"]
    by_id = {row["id"]: row for row in overlays}

    assert by_id["older-candle"]["label"] == "Recent candle"
    assert by_id["older-candle"]["label_hidden"] is True
    assert by_id["latest-candle"]["label"] == "Current price"
    assert by_id["latest-candle"]["label_hidden"] is False


def test_studies_and_council_keep_distinct_public_toggle_identities() -> None:
    payload = _fresh_payload()
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "two-candle-current",
                "type": "TWO_CANDLE_STUDY",
                "layer": "active_council_decision",
                "bounds": [10, 20, 30, 40],
                "frame_id": 14,
            },
            {
                "overlay_id": "lstm-study-current",
                "type": "LSTM_STUDY",
                "role": "lstm_study",
                "layer": "active_council_decision",
                "bounds": [35, 20, 55, 40],
                "frame_id": 14,
            },
            {
                "overlay_id": "lstm-path-current",
                "type": "LSTM_STUDY",
                "role": "lstm_candle_event_path_no_edge",
                "layer": "prediction_path",
                "bounds": [0.55, 0.20, 0.90, 0.40],
                "line_points": [[0.55, 0.30], [0.90, 0.24]],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "lstm-band-current",
                "type": "LSTM_STUDY",
                "role": "lstm_forecast_90_band_no_edge",
                "layer": "prediction_path",
                "bounds": [0.55, 0.16, 0.90, 0.44],
                "points": [
                    [0.55, 0.28],
                    [0.90, 0.22],
                    [0.90, 0.30],
                    [0.55, 0.36],
                ],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "lstm-upper-current",
                "type": "LSTM_STUDY",
                "role": "lstm_forecast_90_upper_boundary_no_edge",
                "layer": "prediction_path",
                "bounds": [0.55, 0.16, 0.90, 0.28],
                "line_points": [[0.55, 0.28], [0.90, 0.22]],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "lstm-lower-current",
                "type": "LSTM_STUDY",
                "role": "lstm_forecast_90_lower_boundary_no_edge",
                "layer": "prediction_path",
                "bounds": [0.55, 0.30, 0.90, 0.44],
                "line_points": [[0.55, 0.36], [0.90, 0.30]],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "council-current",
                "type": "MODEL_COUNCIL_MARKER",
                "layer": "active_council_decision",
                "bounds": [20, 50, 40, 70],
                "frame_id": 14,
            },
            {
                "overlay_id": "smc-order-block-current",
                "type": "ORDER_BLOCK",
                "layer": "smart_money",
                "side": "BUY",
                "bounds": [42, 50, 70, 76],
                "frame_id": 14,
            },
        ]
    }

    overlays = _build_workspace(payload, now_epoch=100.0)["overlays"]
    by_id = {row["id"]: row for row in overlays}

    assert by_id["two-candle-current"]["family"] == "two_candle"
    assert by_id["two-candle-current"]["label"] == "Two-candle study"
    assert by_id["lstm-study-current"]["family"] == "lstm"
    assert by_id["lstm-study-current"]["label"] == "LSTM study"
    assert by_id["lstm-path-current"]["family"] == "prediction"
    assert by_id["lstm-path-current"]["layer"] == "prediction_path"
    assert by_id["lstm-path-current"]["label"] == "LSTM V3 path - NO EDGE - diagnostic"
    assert by_id["lstm-path-current"]["coordinate_units"] == "normalized"
    assert by_id["lstm-path-current"].get("forecast_role") == "center"
    assert by_id["lstm-path-current"].get("forecast_status") == "NO_EDGE"
    assert by_id["lstm-path-current"].get("forecast_authorized") is False
    assert by_id["lstm-path-current"].get("horizon_unit") == "CANDLE_EVENTS"
    assert by_id["lstm-path-current"].get("clock_time_assumption") == "NONE"
    assert by_id["lstm-path-current"].get("uncertainty_level") == 0.90
    prediction_rows = [row for row in overlays if row["family"] == "prediction"]
    lstm_rows = [row for row in overlays if row["family"] == "lstm"]
    assert [row.get("forecast_role") for row in prediction_rows] == ["center"]
    assert {row.get("forecast_role") for row in lstm_rows} >= {
        "band_90",
        "upper_90",
        "lower_90",
    }
    assert not any(
        row.get("forecast_role") == "center" for row in lstm_rows
    )
    assert by_id["council-current"]["family"] == "council"
    assert by_id["council-current"]["label"] == "Council read"
    assert by_id["smc-order-block-current"]["family"] == "smc"
    assert by_id["smc-order-block-current"]["layer"] == "smart_money"
    assert by_id["smc-order-block-current"]["label"] == "SMC order block"


def test_surface_urls_use_encoded_session_id_and_never_copy_artifact_paths() -> None:
    payload = _fresh_payload()
    payload["session_id"] = "desk/alpha beta"
    payload["last_chart_path"] = r"C:\private\chart.png"
    payload["last_window_path"] = r"D:\private\window.png"

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["surface"]["primary_url"] == (
        "/v1/mobile/window-tracker/sessions/desk%2Falpha%20beta/artifacts/latest-window?frame_id=14"
    )
    assert workspace["surface"]["fallback_url"] == (
        "/v1/mobile/window-tracker/sessions/desk%2Falpha%20beta/artifacts/latest-chart?frame_id=14"
    )
    assert workspace["surface"]["focus_url"] == workspace["surface"]["fallback_url"]
    assert workspace["surface"]["primary_space"] == "window"
    assert workspace["surface"]["fallback_space"] == "chart"
    assert "private" not in json.dumps(workspace["surface"]).lower()


def test_surface_exposes_only_safe_normalized_chart_to_window_viewport() -> None:
    payload = _fresh_payload()
    payload["locked_window"] = {
        "hwnd": 987654,
        "title": "Private broker title",
        "width": 1942,
        "height": 1040,
    }
    tracking_summary = _mutable_mapping(payload["tracking_summary"])
    tracking_summary["focus_region"] = {
        "pixel_bbox": [58, 135, 1690, 998],
        "width": 1632,
        "height": 863,
        "source": "private_detector_name",
    }

    surface = _build_workspace(payload, now_epoch=100.0)["surface"]

    assert surface["overlay_viewport"] == {
        "source_space": "chart",
        "target_space": "window",
        "coordinate_units": "normalized",
        "bounds": [0.029866, 0.129808, 0.870237, 0.959615],
    }
    serialized = json.dumps(surface)
    assert "hwnd" not in serialized
    assert "Private broker title" not in serialized
    assert "private_detector_name" not in serialized
    assert "1942" not in serialized
    assert "1040" not in serialized


def test_surface_viewport_prefers_exact_study_focus_and_falls_back_to_enabled_manual_focus() -> None:
    payload = _fresh_payload()
    payload["manual_focus_region"] = {
        "enabled": True,
        "normalized_bbox": [0.03, 0.13, 0.87, 0.96],
        "source": "private_launcher_profile",
    }
    tracking_summary = _mutable_mapping(payload["tracking_summary"])
    tracking_summary["focus_region"] = {
        "normalized_bbox": [0.05, 0.16, 0.82, 0.91],
        "source": "private_runtime_detector",
    }

    exact = _build_workspace(payload, now_epoch=100.0)["surface"]["overlay_viewport"]
    assert exact["bounds"] == [0.05, 0.16, 0.82, 0.91]

    tracking_summary.pop("focus_region")
    fallback = _build_workspace(payload, now_epoch=100.0)["surface"]["overlay_viewport"]
    assert fallback["bounds"] == [0.03, 0.13, 0.87, 0.96]

    manual_focus = _mutable_mapping(payload["manual_focus_region"])
    manual_focus["enabled"] = False
    unavailable = _build_workspace(payload, now_epoch=100.0)["surface"]["overlay_viewport"]
    assert unavailable["bounds"] == []


def test_overlay_identifiers_never_echo_paths_or_uris() -> None:
    payload = _fresh_payload()
    unsafe_identifiers = (
        r"C:\private\zone.json",
        "/srv/private/zone.json",
        "https://internal.invalid/overlays/zone-1",
        "file:///home/phoenixguard/zone.json",
        "data:text/plain,private-overlay",
        "https%3A%2F%2Finternal.invalid%2Fzone-1",
    )
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": identifier,
                "type": "DEMAND_ZONE",
                "layer": "supply_demand",
                "bounds": [10 + index, 20, 30 + index, 40],
                "frame_id": 14,
            }
            for index, identifier in enumerate(unsafe_identifiers)
        ]
    }

    overlays = _build_workspace(payload, now_epoch=100.0)["overlays"]

    assert [row["id"] for row in overlays] == [
        f"overlay-{index}"
        for index in range(1, len(unsafe_identifiers) + 1)
    ]
    serialized = json.dumps(overlays).lower()
    assert "private" not in serialized
    assert "internal.invalid" not in serialized
    assert "file:" not in serialized
    assert "data:" not in serialized


def test_history_is_chronological_bounded_and_uses_generated_summaries() -> None:
    payload = _fresh_payload()
    payload["recent_studies"] = [
        {
            "created_epoch": float(index),
            "frame_id": index,
            "side": "SELL" if index % 2 else "BUY",
            "summary": f"internal model trace {index}",
            "reason": "private",
            "source_path": rf"C:\private\{index}.json",
        }
        for index in range(40)
    ]

    workspace = _build_workspace(payload, now_epoch=100.0)
    history = workspace["history"]

    assert len(history) == 24
    observed = [row["observed_at"] or 0 for row in history]
    assert observed == sorted(observed)
    serialized = json.dumps(history).lower()
    assert "internal model trace" not in serialized
    assert "private" not in serialized
    assert all(set(row) == {"observed_at", "direction", "state", "summary", "frame_id"} for row in history)


def test_canonical_current_leg_requires_current_frame_and_ends_previous_pressure() -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command.pop("current_movement")
    command.pop("pressure_event")
    payload.update(
        {
            "model_vote_frame_id": 14,
            "model_capture_epoch": 99.0,
            "latest_signal": {
                "published_epoch": 99.0,
                "high_frequency_forecast": {"direction": "SELL", "confidence": 0.99},
            },
        }
    )
    tracking_summary = _mutable_mapping(payload["tracking_summary"])
    tracking_summary["candle_movement_context_v3"] = {
        "schema_version": "PG_CANDLE_MOVEMENT_CONTEXT_V3",
        "current_leg": {
            "side": "BUY",
            "transition_state": "CONFIRMED",
            "confidence": 0.82,
        },
        "previous_leg": {"side": "SELL", "confidence": 0.76},
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["current_move"]["direction"] == "BUY"
    assert workspace["current_move"]["state"] == "ACTIVE"
    assert workspace["current_move"]["frame_id"] == 14
    assert workspace["pressure_event"]["direction"] == "SELL"
    assert workspace["pressure_event"]["state"] == "ENDED"
    assert workspace["permission"]["action"] == "BUY_NOW"
    assert workspace["permission"]["allowed"] is True
    assert workspace["forecast"]["direction"] == "BUY"


def test_canonical_buy_reconciles_explicit_sell_pressure_by_frame_time() -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command["current_movement"] = {
        "side": "SELL",
        "state": "ACTIVE",
        "observed_at": 98.0,
        "frame_id": 13,
    }
    command["pressure_event"] = {
        "side": "SELL",
        "state": "ACTIVE",
        "observed_at": 98.0,
        "frame_id": 14,
    }
    payload["model_vote_frame_id"] = 14
    payload["model_capture_epoch"] = 99.0
    tracking_summary = _mutable_mapping(payload["tracking_summary"])
    tracking_summary["candle_movement_context_v3"] = {
        "schema_version": "PG_CANDLE_MOVEMENT_CONTEXT_V3",
        "current_leg": {"side": "BUY", "transition_state": "CONFIRMED"},
        "previous_leg": {"side": "SELL"},
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["current_move"]["direction"] == "BUY"
    assert workspace["current_move"]["state"] == "ACTIVE"
    assert workspace["current_move"]["observed_at"] == 99.0
    assert workspace["pressure_event"]["direction"] == "SELL"
    assert workspace["pressure_event"]["state"] == "ENDED"
    assert workspace["pressure_event"]["ended_at"] == 99.0
    assert workspace["permission"]["action"] == "BUY_NOW"
    assert workspace["permission"]["allowed"] is True

    pressure_event = _mutable_mapping(command["pressure_event"])
    pressure_event["observed_at"] = 99.0
    equal_timestamp = _build_workspace(payload, now_epoch=100.0)
    assert equal_timestamp["pressure_event"]["state"] == "ENDED"
    assert equal_timestamp["permission"]["action"] == "BUY_NOW"

    pressure_event["observed_at"] = 99.5
    current_conflict = _build_workspace(payload, now_epoch=100.0)
    assert current_conflict["current_move"]["direction"] == "BUY"
    assert current_conflict["pressure_event"]["direction"] == "SELL"
    assert current_conflict["pressure_event"]["state"] == "ACTIVE"
    assert current_conflict["permission"]["action"] == "WAIT"

    pressure_event["state"] = "UNKNOWN"
    unresolved_conflict = _build_workspace(payload, now_epoch=100.0)
    assert unresolved_conflict["pressure_event"]["direction"] == "SELL"
    assert unresolved_conflict["pressure_event"]["state"] == "UNKNOWN"
    assert unresolved_conflict["permission"]["action"] == "WAIT"


@pytest.mark.parametrize(
    ("context_leg", "model_frame", "expected_state"),
    [
        (
            {
                "side": "HOLD",
                "candidate_side": "BUY",
                "move_stage": "TRANSITION",
                "transition_state": "FORMING",
                "confirmation_count": 2,
                "confirmation_required": 3,
            },
            14,
            "UNKNOWN",
        ),
        ({"side": "BUY", "transition_state": "CONFIRMED"}, 13, "STALE"),
    ],
)
def test_unconfirmed_or_wrong_frame_candle_leg_never_becomes_current(
    context_leg: dict[str, object],
    model_frame: int,
    expected_state: str,
) -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command.pop("current_movement")
    command.pop("pressure_event")
    payload["model_vote_frame_id"] = model_frame
    payload["model_capture_epoch"] = 99.0
    tracking_summary = _mutable_mapping(payload["tracking_summary"])
    tracking_summary["candle_movement_context_v3"] = {
        "schema_version": "PG_CANDLE_MOVEMENT_CONTEXT_V3",
        "current_leg": context_leg,
        "previous_leg": {"side": "SELL"},
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["current_move"]["direction"] == "NEUTRAL"
    assert workspace["current_move"]["state"] == expected_state
    assert workspace["pressure_event"]["direction"] == "SELL"
    assert workspace["pressure_event"]["state"] == "ENDED"
    assert workspace["permission"]["action"] == "WAIT"


def test_operator_route_returns_only_projection_and_merges_live_with_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "operator-route-contract"
    snapshot: dict[str, object] = {
        "session_id": session_id,
        "display_frame_id": 21,
        "tracking_enabled": True,
        "last_capture_epoch": 199.0,
        "execution_controls": {"live_execution_enabled": False},
        "tracking_summary": {
            "detected_market": "GBP/USD",
            "detected_timeframe": "M5",
            "last_capture_epoch": 199.0,
            "candle_movement_context_v3": {
                "current_leg": {"side": "BUY", "transition_state": "CONFIRMED"},
                "previous_leg": {"side": "SELL"},
            },
        },
        "recent_studies": [
            {"created_epoch": 190.0, "frame_id": 21, "side": "SELL", "reason": "private"}
        ],
        "provider_status": {"source_path": r"C:\private\session.json"},
    }
    compact_state: dict[str, object] = {
        "session_id": session_id,
        "state_version": 22,
        "display_frame_id": 22,
        "model_vote_frame_id": 22,
        "model_capture_epoch": 199.0,
        "tracking_enabled": True,
        "decision_command_center": {
            "fresh": True,
            "freshness_status": "PASS",
            "created_epoch": 199.0,
            "valid_until_epoch": 220.0,
            "selected_side": "BUY",
            "execution_packet_present": False,
        },
        "overlays": {
            "objects": [
                {
                    "overlay_id": "route-demand",
                    "object_id": "route-demand",
                    "track_id": "route-demand",
                    "type": "DEMAND_ZONE",
                    "side": "BUY",
                    "layer": "supply_demand",
                    "bounds": [10, 20, 40, 60],
                    "frame_id": 22,
                    "sequence_id": "seq-22",
                    "chart_transform_id": "ct-22",
                    "coordinate_mode": "CHART_IMAGE_SPACE",
                    "anchor_type": "CANDLES",
                    "anchor_candles": [4, 5],
                    "anchor_candle_indices": [4, 5],
                    "anchor_price_band": {"top_y": 20, "bottom_y": 60},
                    "anchor_time_span": {"left_x": 10, "right_x": 40},
                    "anchor_evidence": {"valid": True, "evidence_type": "support_reclaim"},
                    "truth_score": 0.84,
                    "confidence": 0.88,
                    "lifecycle_state": "ACTIVE",
                    "visible_modes": ["CLEAN_LIVE", "ACTIVE_CONTEXT"],
                    "ttl_ms": 9000,
                    "source_agent": "model_council_v3",
                    "source_version": "PG_V3_OVERLAY_OBJECT_V1",
                    "broker_source_lock_id": "broker-lock-22",
                    "source_path": r"C:\private\overlay.json",
                }
            ]
        },
        "frame_timing_trace_v3": {"pipeline_latency_ms": 9},
    }
    compact_overlays = _mutable_mapping(compact_state["overlays"])
    compact_objects = compact_overlays["objects"]
    assert isinstance(compact_objects, list)
    cast(list[object], compact_objects).extend(
        [
            {
                "overlay_id": overlay_id,
                "type": overlay_type,
                "layer": layer,
                "bounds": [12 + index, 22, 42 + index, 62],
                "frame_id": 22,
                "coordinate_mode": "CHART_IMAGE_SPACE",
                "lifecycle_state": lifecycle,
            }
            for index, (overlay_id, overlay_type, layer, lifecycle) in enumerate(
                (
                    ("route-bounds", "CHART_BOUNDS", "chart_bounds", "ACTIVE"),
                    ("route-current", "CURRENT_CANDLE", "recent_candles", "ACTIVE"),
                    ("route-major", "IMPULSE_BOX", "major_swings", "ACTIVE"),
                    ("route-local", "PULLBACK_BOX", "local_swings", "ACTIVE"),
                    ("route-trend", "SUPPORT_TRENDLINE", "trendlines", "ACTIVE"),
                    ("route-trigger", "SNIPER_ENTRY_BOX", "trigger_zones", "ACTIVE"),
                    ("route-target", "TARGET_ZONE_BOX", "target_zones", "ACTIVE"),
                    ("route-risk", "INVALIDATION_BOX", "invalidation", "ACTIVE"),
                    (
                        "route-council",
                        "MODEL_COUNCIL_MARKER",
                        "active_council_decision",
                        "ACTIVE",
                    ),
                    ("route-smc", "ORDER_BLOCK", "smart_money", "ACTIVE"),
                    (
                        "route-two-candle",
                        "TWO_CANDLE_STUDY",
                        "active_council_decision",
                        "ACTIVE",
                    ),
                    (
                        "route-lstm",
                        "LSTM_STUDY",
                        "active_council_decision",
                        "ACTIVE",
                    ),
                    ("route-prediction", "PREDICTION_PATH", "prediction_path", "ACTIVE"),
                    ("route-history", "REPLAY_ENTRY", "historical_replay", "STALE"),
                )
            )
        ]
    )
    compact_state["live_visual_state"] = {"overlays": compact_state["overlays"]}
    observed_call: dict[str, object] = {}
    captured_projection_input: dict[str, object] = {}

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return snapshot

        def latest_model_council_state(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {}

    def _build_compact_state(
        tracker: object,
        requested_session_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        observed_call.update(kwargs)
        return compact_state

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        _build_compact_state,
    )
    real_projection_builder = mobile_app.build_operator_workspace_v1

    def _capture_projection_input(
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        captured_projection_input.update(payload)
        return real_projection_builder(payload)

    monkeypatch.setattr(
        mobile_app,
        "build_operator_workspace_v1",
        _capture_projection_input,
    )
    client = TestClient(mobile_app.create_app(window_tracker_service=_Tracker()))

    response = client.get(
        f"/v1/mobile/operator/state/v1/{session_id}?view=zones"
    )

    assert response.status_code == 200
    workspace = cast(_OperatorWorkspaceView, response.json())
    assert set(workspace) == TOP_LEVEL_KEYS
    assert workspace["schema_version"] == OPERATOR_WORKSPACE_SCHEMA_VERSION
    assert workspace["market"] == {"symbol": "GBP/USD", "timeframe": "M5"}
    assert workspace["current_move"]["direction"] == "NEUTRAL"
    assert workspace["current_move"]["state"] == "UNKNOWN"
    assert workspace["surface"]["frame_id"] == 22
    assert workspace["permission"]["action"] == "WAIT"
    captured_overlays = captured_projection_input["overlays"]
    assert isinstance(captured_overlays, Mapping)
    captured_overlays = cast(Mapping[str, object], captured_overlays)
    assert captured_overlays.get("objects"), captured_overlays
    captured_tracking = captured_projection_input["tracking_summary"]
    assert isinstance(captured_tracking, Mapping)
    assert "candle_movement_context_v3" not in captured_tracking
    assert [row["id"] for row in workspace["overlays"]] == ["route-demand"]
    assert any(row["frame_id"] == 21 for row in workspace["history"])
    assert _all_keys(workspace).isdisjoint(
        {"provider_status", "frame_timing_trace_v3", "source_path", "reason"}
    )
    assert observed_call["overlay_mode"] == "INSPECTOR"
    assert observed_call["compact_public"] is True

    expected_views = {
        "all": (
            "INSPECTOR",
            {
                "chart_bounds",
                "current_candles",
                "major_swings",
                "local_swings",
                "supply_demand",
                "trendlines",
                "triggers",
                "targets",
                "invalidation",
                "council",
                "smc",
                "two_candle",
                "lstm",
                "prediction",
                "history",
            },
        ),
        "live": (
            "INSPECTOR",
            {
                "chart_bounds",
                "current_candles",
                "major_swings",
                "local_swings",
                "supply_demand",
                "trendlines",
                "triggers",
                "targets",
                "invalidation",
                "smc",
                "council",
            },
        ),
        "structure": (
            "INSPECTOR",
            {"current_candles", "major_swings", "local_swings", "trendlines"},
        ),
        "zones": ("INSPECTOR", {"supply_demand"}),
        "plan": ("INSPECTOR", {"council", "triggers", "targets", "invalidation"}),
        "smc": ("SMART_MONEY", {"smc"}),
        "two-candle": ("TWO_CANDLE_STUDY", {"two_candle"}),
        "lstm": ("LSTM_STUDY", {"lstm"}),
        "forecast": ("INSPECTOR", {"two_candle", "lstm", "prediction"}),
        "history": ("INSPECTOR", {"history", "major_swings", "local_swings"}),
    }
    for public_view, (expected_mode, expected_families) in expected_views.items():
        public_view_modes = cast(
            Mapping[str, str],
            getattr(mobile_app, "_OPERATOR_VIEW_TO_OVERLAY_MODE"),
        )
        assert public_view_modes[public_view] == expected_mode
        public_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view={public_view}"
        )
        assert public_response.status_code == 200
        public_workspace = cast(_OperatorWorkspaceView, public_response.json())
        assert {row["family"] for row in public_workspace["overlays"]} == expected_families
        assert observed_call["compact_public"] is True

    rejected = client.get(
        f"/v1/mobile/operator/state/v1/{session_id}?view=inspector"
    )
    assert rejected.status_code == 400
    assert rejected.json() == {"detail": "Unsupported operator view."}


def test_current_studies_survive_incomplete_same_lineage_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "operator-incomplete-snapshot"
    source: dict[str, object] = {
        "session_id": session_id,
        "state_version": 14,
        "display_frame_id": 14,
        "chart_frame_id": 14,
        "overlay_frame_id": 14,
        "full_overlay_frame_id": 14,
        "model_vote_frame_id": 14,
        "tracking_enabled": True,
        "last_capture_epoch": 99.0,
        "last_display_surface_signature": "window-sig-14",
        "last_study_surface_signature": "study-sig-14",
        "overlay_source_window_signature": "window-sig-14",
        "overlay_source_study_signature": "study-sig-14",
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.1, 0.1, 0.9, 0.9],
        },
    }

    def study_row(
        overlay_id: str,
        *,
        role: str = "",
        points: list[list[float]] | None = None,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "overlay_id": overlay_id,
            "type": "LSTM_STUDY" if role else "TWO_CANDLE_STUDY",
            "side": "BUY",
            "layer": "prediction_path" if role else "active_council_decision",
            "role": role or "two_candle_study",
            "bounds": [0.62, 0.24, 0.92, 0.56],
            "frame_id": 14,
            "coordinate_mode": "CHART_NORMALIZED",
            "lifecycle_state": "ACTIVE",
            "confidence": 0.77,
        }
        if points is not None:
            if "90_band" in role:
                row["points"] = points
            else:
                row["line_points"] = points
        return row

    current_rows = [
        study_row("current-two-candle"),
        study_row(
            "current-lstm-center",
            role="lstm_candle_event_path_authorized",
            points=[[0.62, 0.42], [0.76, 0.38], [0.92, 0.34]],
        ),
        study_row(
            "current-lstm-band",
            role="lstm_forecast_90_band_authorized",
            points=[
                [0.62, 0.36],
                [0.92, 0.28],
                [0.92, 0.44],
                [0.62, 0.48],
            ],
        ),
        study_row(
            "current-lstm-upper",
            role="lstm_forecast_90_upper_boundary_authorized",
            points=[[0.62, 0.36], [0.92, 0.28]],
        ),
        study_row(
            "current-lstm-lower",
            role="lstm_forecast_90_lower_boundary_authorized",
            points=[[0.62, 0.48], [0.92, 0.44]],
        ),
    ]
    live_state: dict[str, object] = {
        **source,
        "frame_id": 14,
        "overlays": {"objects": current_rows},
        "live_visual_state": {"overlays": {"objects": current_rows}},
    }

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return json.loads(json.dumps(source))

        def latest_model_council_state(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {}

    def _build_state(
        tracker: object,
        requested_session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        return json.loads(json.dumps(live_state))

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        _build_state,
    )
    snapshot_path = (
        tmp_path
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / session_id
        / "operator_overlay_snapshot_v1.json"
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "PG_OPERATOR_OVERLAY_SNAPSHOT_V1",
                "session_id": session_id,
                "lineage": {
                    "frame_id": 14,
                    "chart_frame_id": 14,
                    "overlay_frame_id": 14,
                    "full_overlay_frame_id": 14,
                    "model_vote_frame_id": 14,
                    "display_surface_signature": "window-sig-14",
                    "study_surface_signature": "study-sig-14",
                    "overlay_source_window_signature": "window-sig-14",
                    "overlay_source_study_signature": "study-sig-14",
                    "state_version": 14,
                },
                "overlay_viewport": {
                    "source_space": "chart",
                    "target_space": "window",
                    "coordinate_units": "normalized",
                    "bounds": [0.1, 0.1, 0.9, 0.9],
                },
                # This deliberately represents an earlier incomplete build of
                # the same atomic frame.
                "overlays": [
                    {
                        "id": "saved-demand-only",
                        "type": "zone",
                        "side": "BUY",
                        "group": "zones",
                        "family": "supply_demand",
                        "layer": "supply_demand",
                        "label": "Demand area",
                        "label_hidden": False,
                        "bounds": [10.0, 20.0, 30.0, 40.0],
                        "points": [],
                        "line_points": [],
                        "confidence": 0.7,
                        "lifecycle": "current",
                        "frame_id": 14,
                        "coordinate_space": "chart",
                        "coordinate_units": "pixels",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=forecast"
        )

    assert response.status_code == 200
    workspace = cast(_OperatorWorkspaceView, response.json())
    counts = {
        family: sum(1 for row in workspace["overlays"] if row["family"] == family)
        for family in ("two_candle", "lstm", "prediction")
    }
    assert counts == {"two_candle": 1, "lstm": 3, "prediction": 1}
    repaired_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    repaired_families = [
        str(row.get("family"))
        for row in cast(list[dict[str, object]], repaired_snapshot["overlays"])
    ]
    assert repaired_families.count("two_candle") == 1
    assert repaired_families.count("lstm") == 3
    assert repaired_families.count("prediction") == 1


def test_operator_route_persists_projection_frame_when_service_snapshot_advances(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "operator-frame-race"
    compact_source: dict[str, object] = {
        "session_id": session_id,
        "state_version": 140,
        "display_frame_id": 14,
        "chart_frame_id": 14,
        "overlay_frame_id": 14,
        "full_overlay_frame_id": 14,
        "model_vote_frame_id": 14,
        "tracking_enabled": True,
        "last_capture_epoch": 99.0,
        "last_display_surface_signature": "window-sig-14",
        "last_window_surface_signature": "window-sig-14",
        "last_study_surface_signature": "study-sig-14",
        "overlay_source_window_signature": "window-sig-14",
        "overlay_source_study_signature": "study-sig-14",
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.1, 0.1, 0.9, 0.9],
        },
        "tracking_summary": {
            "detected_market": "USD/JPY OTC",
            "detected_timeframe": "M5",
            "last_capture_epoch": 99.0,
        },
    }
    trendline = {
        "overlay_id": "inner-trendline-frame-14",
        "type": "INNER_TRENDLINE",
        "side": "BUY",
        "layer": "trendlines",
        "role": "inner_support",
        "bounds": [420.0, 310.0, 760.0, 520.0],
        "line_points": [[420.0, 520.0], [760.0, 310.0]],
        "frame_id": 14,
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "lifecycle_state": "ACTIVE",
        "confidence": 0.82,
    }
    compact_live_state: dict[str, object] = {
        **compact_source,
        "frame_id": 14,
        "overlays": {"objects": [trendline]},
        "live_visual_state": {"overlays": {"objects": [trendline]}},
    }
    service_snapshot = json.loads(json.dumps(compact_source))
    assert isinstance(service_snapshot, dict)
    for key in (
        "display_frame_id",
        "chart_frame_id",
        "overlay_frame_id",
        "full_overlay_frame_id",
        "model_vote_frame_id",
    ):
        service_snapshot[key] = 15
    service_snapshot["state_version"] = 150
    service_snapshot["last_display_surface_signature"] = "window-sig-15"
    service_snapshot["last_window_surface_signature"] = "window-sig-15"
    service_snapshot["last_study_surface_signature"] = "study-sig-15"
    service_snapshot["overlay_source_window_signature"] = "window-sig-15"
    service_snapshot["overlay_source_study_signature"] = "study-sig-15"

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return cast(dict[str, object], json.loads(json.dumps(service_snapshot)))

        def latest_model_council_state(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {}

    def _build_state(
        tracker: object,
        requested_session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        return cast(
            dict[str, object],
            json.loads(json.dumps(compact_live_state)),
        )

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        _build_state,
    )
    snapshot_path = (
        tmp_path
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / session_id
        / "operator_overlay_snapshot_v1.json"
    )

    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        first = client.get(f"/v1/mobile/operator/state/v1/{session_id}?view=structure")
        second = client.get(f"/v1/mobile/operator/state/v1/{session_id}?view=structure")

    assert first.status_code == 200
    assert second.status_code == 200
    for response in (first, second):
        workspace = cast(_OperatorWorkspaceView, response.json())
        assert workspace["surface"]["frame_id"] == 14
        assert [row["family"] for row in workspace["overlays"]] == ["trendlines"]
        assert {row["frame_id"] for row in workspace["overlays"]} == {14}
        assert all(row["lifecycle"] == "current" for row in workspace["overlays"])

    persisted = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert persisted["lineage"]["frame_id"] == 14
    assert {row["frame_id"] for row in persisted["overlays"]} == {14}


def test_operator_route_cold_wait_restores_exact_safe_overlay_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "operator-cold-wait"
    source: dict[str, object] = {
        "session_id": session_id,
        "state_version": 14,
        "display_frame_id": 14,
        "chart_frame_id": 14,
        "overlay_frame_id": 14,
        "full_overlay_frame_id": 14,
        "model_vote_frame_id": 14,
        "tracking_enabled": True,
        "last_capture_epoch": 99.0,
        "last_display_surface_signature": "window-sig-14",
        "last_study_surface_signature": "study-sig-14",
        "overlay_source_window_signature": "window-sig-14",
        "overlay_source_study_signature": "study-sig-14",
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.12, 0.08, 0.88, 0.92],
        },
        "tracking_summary": {
            "detected_market": "CAD/JPY OTC",
            "detected_timeframe": "M5",
            "last_capture_epoch": 99.0,
        },
        "forecast_snapshot_v3": {
            "schema_version": "PG_FORECAST_SNAPSHOT_V3",
            "source_frame_id": 14,
            "observed_epoch": 99.0,
            "stale": False,
            "diagnostic_only": False,
            "lstm_contribution": {
                "frame_id": 14,
                "forecast_available": True,
                "path_side": "SELL",
                "confidence": 0.77,
                "features": [{"relative_price_location": 0.51}],
                "raw_model_logits": [0.12, 0.88],
            },
            "two_candle_study": {
                "frame_id": 14,
                "primary_pressure": "SELL",
                "next_candle_forecast": {
                    "frame_id": 14,
                    "direction": "SELL",
                    "confidence": 0.64,
                },
            },
        },
    }

    def overlay(
        overlay_id: str,
        overlay_type: str,
        layer: str,
        *,
        role: str = "",
        bounds: list[float] | None = None,
        line_points: list[list[float]] | None = None,
        points: list[list[float]] | None = None,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "overlay_id": overlay_id,
            "type": overlay_type,
            "side": "SELL",
            "layer": layer,
            "bounds": bounds or [100.0, 120.0, 220.0, 240.0],
            "frame_id": 14,
            "coordinate_mode": (
                "CHART_NORMALIZED" if overlay_type == "LSTM_STUDY" else "CHART_IMAGE_SPACE"
            ),
            "lifecycle_state": "ACTIVE",
            "confidence": 0.77,
            # These raw diagnostic fields must never enter the persisted
            # operator DTO or its public response.
            "source_agent": "private_model_agent",
            "source_path": r"C:\private\raw-overlay.json",
            "reason": "private backend diagnostic",
        }
        if role:
            row["role"] = role
        if line_points is not None:
            row["line_points"] = line_points
        if points is not None:
            row["points"] = points
        return row

    full_rows = [
        overlay("cold-smc", "ORDER_BLOCK", "smart_money"),
        overlay("cold-demand", "DEMAND_ZONE", "supply_demand"),
        overlay("cold-trigger", "SNIPER_ENTRY_BOX", "trigger_zones"),
        overlay("cold-two-candle", "TWO_CANDLE_STUDY", "active_council_decision"),
        overlay(
            "cold-lstm-center",
            "LSTM_STUDY",
            "prediction_path",
            role="lstm_candle_event_path_authorized",
            bounds=[0.60, 0.28, 0.92, 0.52],
            line_points=[[0.60, 0.32], [0.76, 0.40], [0.92, 0.48]],
        ),
        overlay(
            "cold-lstm-band",
            "LSTM_STUDY",
            "prediction_path",
            role="lstm_forecast_90_band_authorized",
            bounds=[0.60, 0.24, 0.92, 0.56],
            points=[
                [0.60, 0.28],
                [0.92, 0.44],
                [0.92, 0.52],
                [0.60, 0.36],
            ],
        ),
        overlay(
            "cold-lstm-upper",
            "LSTM_STUDY",
            "prediction_path",
            role="lstm_forecast_90_upper_boundary_authorized",
            bounds=[0.60, 0.24, 0.92, 0.46],
            line_points=[[0.60, 0.28], [0.92, 0.44]],
        ),
        overlay(
            "cold-lstm-lower",
            "LSTM_STUDY",
            "prediction_path",
            role="lstm_forecast_90_lower_boundary_authorized",
            bounds=[0.60, 0.34, 0.92, 0.56],
            line_points=[[0.60, 0.36], [0.92, 0.52]],
        ),
    ]
    live_state: dict[str, object] = {
        **source,
        "frame_id": 14,
        "overlays": {"objects": full_rows},
        "live_visual_state": {"overlays": {"objects": full_rows}},
    }

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return json.loads(json.dumps(source))

        def latest_model_council_state(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {}

    def _build_state(
        tracker: object,
        requested_session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        return json.loads(json.dumps(live_state))

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        _build_state,
    )
    tracker = _Tracker()
    snapshot_path = (
        tmp_path
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / session_id
        / "operator_overlay_snapshot_v1.json"
    )
    with TestClient(mobile_app.create_app(window_tracker_service=tracker)) as client:
        fresh_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        first_snapshot_mtime_ns = snapshot_path.stat().st_mtime_ns
        repeated_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        repeated_snapshot_mtime_ns = snapshot_path.stat().st_mtime_ns
        compact_response = client.get(
            f"/v1/mobile/live/state/v3/{session_id}?mode=INSPECTOR&compact=true"
        )
    assert fresh_response.status_code == 200
    assert repeated_response.status_code == 200
    assert repeated_snapshot_mtime_ns == first_snapshot_mtime_ns
    assert compact_response.status_code == 200
    compact_serialized = json.dumps(compact_response.json())
    assert "forecast_snapshot_v3" not in compact_serialized
    assert "features" not in compact_serialized
    assert "raw_model_" not in compact_serialized
    fresh_workspace = cast(_OperatorWorkspaceView, fresh_response.json())
    fresh_counts = {
        family: sum(1 for row in fresh_workspace["overlays"] if row["family"] == family)
        for family in {"smc", "supply_demand", "triggers", "two_candle", "lstm", "prediction"}
    }
    assert fresh_counts == {
        "smc": 1,
        "supply_demand": 1,
        "triggers": 1,
        "two_candle": 1,
        "lstm": 3,
        "prediction": 1,
    }

    persisted_text = snapshot_path.read_text(encoding="utf-8")
    assert "private_model_agent" not in persisted_text
    assert "private backend diagnostic" not in persisted_text
    assert r"C:\private\raw-overlay.json" not in persisted_text

    source["state_version"] = 15
    source["visual_observation_v3"] = {
        "status": "WAITING_FOR_NEW_FRAME",
        "new_visual_evidence": False,
        "duplicate_study_count": 20,
    }
    forecast_snapshot = _mutable_mapping(source["forecast_snapshot_v3"])
    forecast_snapshot["stale"] = True
    forecast_snapshot["diagnostic_only"] = True
    live_state.clear()
    live_state.update(
        {
            **source,
            "frame_id": 14,
            "overlays": {"objects": []},
            "live_visual_state": {"overlays": {"objects": []}},
        }
    )

    # A new app instance simulates the launcher/API process restarting while
    # the broker is still displaying the exact same accepted frame.
    with TestClient(mobile_app.create_app(window_tracker_service=tracker)) as cold_client:
        all_response = cold_client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        forecast_response = cold_client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=forecast"
        )

    assert all_response.status_code == 200
    waiting_workspace = cast(_OperatorWorkspaceView, all_response.json())
    waiting_counts = {
        family: sum(1 for row in waiting_workspace["overlays"] if row["family"] == family)
        for family in {"smc", "supply_demand", "triggers", "two_candle", "lstm", "prediction"}
    }
    assert waiting_counts == fresh_counts
    assert waiting_workspace["forecast"]["direction"] == "SELL"
    assert waiting_workspace["forecast"]["confidence"] == 0.64
    assert waiting_workspace["forecast"]["state"] == "STALE"
    assert waiting_workspace["permission"] == {
        **waiting_workspace["permission"],
        "action": "WAIT",
        "allowed": False,
        "side": "NEUTRAL",
    }
    assert all(
        row["lifecycle"] == "stale_diagnostic"
        for row in waiting_workspace["overlays"]
    )
    forecast_workspace = cast(_OperatorWorkspaceView, forecast_response.json())
    assert {row["family"] for row in forecast_workspace["overlays"]} == {
        "two_candle",
        "lstm",
        "prediction",
    }
    assert sum(
        row.get("forecast_role") == "center"
        for row in forecast_workspace["overlays"]
    ) == 1
    assert all(
        row.get("forecast_authorized") is False
        for row in forecast_workspace["overlays"]
        if row.get("forecast_role")
    )
    waiting_serialized = json.dumps(waiting_workspace)
    assert all(
        forbidden not in waiting_serialized
        for forbidden in (
            "forecast_snapshot_v3",
            "features",
            "raw_model_",
            "surface_signature",
            "source_agent",
            "source_path",
            "provider_status",
            "frame_timing_trace_v3",
            "performance_trace_v3",
        )
    )
