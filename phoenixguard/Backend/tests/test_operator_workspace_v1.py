from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import phoenixguard.mobile_api.app as mobile_app
from phoenixguard.mobile_api.live_state_v3 import build_live_state_v3
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
    semantic_identity: NotRequired[str]
    overlay_semantic_revision: NotRequired[str]
    overlay_geometry_revision: NotRequired[str]


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
    semantic_id: NotRequired[str]
    overlay_semantic_revision: NotRequired[str]
    overlay_geometry_revision: NotRequired[str]
    anchor_id: NotRequired[str]
    forecast_scenarios: NotRequired[object]
    trajectory_mode: NotRequired[str]
    trajectory_mode_probability_calibrated: NotRequired[bool]
    interval: NotRequired[object]


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


def _complete_forecast_bundle(
    *,
    selected_side: str = "BUY",
    anchor_x: float = 0.55,
    anchor_y: float = 0.30,
) -> dict[str, object]:
    x_step = 0.35 / 12.0
    paths = {
        "BUY": [
            [round(anchor_x + step * x_step, 6), round(anchor_y - step * 0.005, 6)]
            for step in range(13)
        ],
        "SELL": [
            [round(anchor_x + step * x_step, 6), round(anchor_y + step * 0.006, 6)]
            for step in range(13)
        ],
        "NEUTRAL": [
            [round(anchor_x + step * x_step, 6), round(anchor_y, 6)]
            for step in range(13)
        ],
    }
    selected_path = paths[selected_side]
    candles: list[dict[str, object]] = []
    for step in range(1, 13):
        open_y = float(selected_path[step - 1][1])
        close_y = float(selected_path[step][1])
        candles.append(
            {
                "step": step,
                "x_norm": selected_path[step][0],
                "open_y_norm": open_y,
                "high_y_norm": round(min(open_y, close_y) - 0.004, 6),
                "low_y_norm": round(max(open_y, close_y) + 0.004, 6),
                "close_y_norm": close_y,
                "movement_side": selected_side,
            }
        )
    probabilities = {"BUY": 0.72, "SELL": 0.18, "NEUTRAL": 0.10}
    return {
        "line_points": selected_path,
        "forecast_candles": candles,
        "forecast_scenarios": [
            {
                "side": side,
                "label": f"{side} PATH",
                "probability": probabilities[side],
                "probability_calibrated": False,
                "selected": side == selected_side,
                "line_points": points,
                "event_count": 12,
            }
            for side, points in paths.items()
        ],
        "forecast_anchor": {
            "x_norm": anchor_x,
            "y_norm": anchor_y,
            "verified_latest_close": True,
            "source": "TRACKER_LATEST_CLOSE",
        },
    }


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


def test_operator_session_and_health_hot_paths_never_read_full_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "compact-hot-path"
    frame_id = 73
    now_epoch = 1_900_000_000.0
    session_dir = (
        tmp_path
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / session_id
    )
    artifact_dir = session_dir / "artifacts"
    artifact_dir.mkdir(parents=True)
    window_path = artifact_dir / "000073_window.png"
    chart_path = artifact_dir / "000073_chart.png"
    overlay_path = artifact_dir / "000073_full_overlay.png"
    for path in (window_path, chart_path, overlay_path):
        path.write_bytes(b"hot-path-artifact")

    frame_fields = {
        "capture_count": frame_id,
        "frame_index": frame_id,
        "display_frame_id": frame_id,
        "chart_frame_id": frame_id,
        "overlay_frame_id": frame_id,
        "full_overlay_frame_id": frame_id,
        "model_vote_frame_id": frame_id,
        "state_version": frame_id,
        "last_capture_epoch": now_epoch,
        "display_capture_epoch": now_epoch,
        "display_published_epoch": now_epoch,
        "decision_valid_until_epoch": now_epoch + 60.0,
        "last_window_path": str(window_path),
        "last_display_window_path": str(window_path),
        "last_chart_path": str(chart_path),
        "last_overlay_path": str(overlay_path),
        "last_full_overlay_path": str(overlay_path),
        "last_display_surface_signature": "surface-73",
        "last_window_surface_signature": "surface-73",
        "last_study_surface_signature": "study-73",
        "overlay_source_window_signature": "surface-73",
        "overlay_source_study_signature": "study-73",
    }
    compact_payload: dict[str, object] = {
        "session_id": session_id,
        "status": "running",
        "tracking_enabled": True,
        **frame_fields,
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.10, 0.08, 0.90, 0.92],
        },
        "tracking_summary": {
            "detected_market": "EUR/USD OTC",
            "detected_timeframe": "M5",
            "market_selector_visual_fingerprint": "selector_v2_eurusd",
            "frame_index": frame_id,
            "display_frame_id": frame_id,
            "last_capture_epoch": now_epoch,
            "market_selector_visual_changed": True,
            "market_selector_rebind_required": False,
            "market_selector_studying_new_pair": False,
            "focus_region": {
                "normalized_bbox": [0.10, 0.08, 0.90, 0.92],
            },
            "tracked_candles": [
                {
                    "frame_id": frame_id,
                    "index": index,
                    "bbox": [120 + index * 8, 200, 126 + index * 8, 250],
                    "side": "BUY" if index % 2 == 0 else "SELL",
                }
                for index in range(12)
            ],
        },
        "latest_signal": {
            "session_id": session_id,
            "status": "watching",
            "side": "HOLD",
            "published_epoch": now_epoch,
        },
        "recent_studies": [
            {
                "created_epoch": now_epoch - index,
                "frame_id": frame_id - index,
                "side": "BUY" if index % 2 == 0 else "SELL",
                "state": "ENDED",
            }
            for index in range(6)
        ],
    }
    display_payload = {
        "session_id": session_id,
        "status": "running",
        "tracking_enabled": True,
        **frame_fields,
        "frame_bundle_complete_v3": True,
    }
    (session_dir / "compact_live_state.json").write_text(
        json.dumps(compact_payload),
        encoding="utf-8",
    )
    (session_dir / "display_state.json").write_text(
        json.dumps(display_payload),
        encoding="utf-8",
    )
    full_session_path = session_dir / "session.json"
    full_session_path.write_text(
        json.dumps({"session_id": session_id, "huge_archive": "x" * 3_000_000}),
        encoding="utf-8",
    )

    class _NoFullReadTracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            raise AssertionError(f"full session snapshot read: {requested_session_id}")

        def get_session(self, requested_session_id: str) -> dict[str, object]:
            raise AssertionError(f"full session read: {requested_session_id}")

        def latest_artifact_path(self, requested_session_id: str, kind: str) -> Path:
            raise AssertionError(f"full artifact lookup: {requested_session_id}/{kind}")

        def capture_worker_health_v3(self, requested_session_id: str) -> dict[str, object]:
            raise AssertionError(f"full worker health read: {requested_session_id}")

    full_session_reads: list[Path] = []
    original_read_text = Path.read_text

    def guarded_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == full_session_path:
            full_session_reads.append(path)
            raise AssertionError("session.json must not be read by a polling endpoint")
        return original_read_text(path, encoding=encoding, errors=errors)

    operator_projection_calls = 0
    original_operator_builder = mobile_app.build_operator_workspace_v1

    def counted_operator_builder(
        source: Mapping[str, object],
    ) -> dict[str, object]:
        nonlocal operator_projection_calls
        operator_projection_calls += 1
        return original_operator_builder(source)

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(
        mobile_app,
        "build_operator_workspace_v1",
        counted_operator_builder,
    )
    with TestClient(
        mobile_app.create_app(window_tracker_service=_NoFullReadTracker())
    ) as client:
        operator_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        same_frame_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=structure"
        )

        # Persistence churn is not display authority. Replacing the compact
        # sidecar must not evict the projection while frame 73 is still shown.
        changed_compact_payload = json.loads(json.dumps(compact_payload))
        changed_tracking = cast(
            dict[str, object],
            changed_compact_payload["tracking_summary"],
        )
        changed_tracking["detected_market"] = "GBP/JPY OTC"
        changed_tracking["market_selector_visual_fingerprint"] = (
            "selector_v2_gbpjpy_confirmed"
        )
        compact_path = session_dir / "compact_live_state.json"
        replacement_path = compact_path.with_suffix(".json.replacement")
        replacement_path.write_text(
            json.dumps(changed_compact_payload),
            encoding="utf-8",
        )
        replacement_path.replace(compact_path)
        compact_churn_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )

        next_frame_id = frame_id + 1
        next_frame_fields = dict(frame_fields)
        for key in (
            "capture_count",
            "frame_index",
            "display_frame_id",
            "chart_frame_id",
            "overlay_frame_id",
            "full_overlay_frame_id",
            "model_vote_frame_id",
            "state_version",
        ):
            next_frame_fields[key] = next_frame_id
        next_frame_fields.update(
            {
                "last_display_surface_signature": "surface-74",
                "last_window_surface_signature": "surface-74",
                "last_study_surface_signature": "study-74",
                "overlay_source_window_signature": "surface-74",
                "overlay_source_study_signature": "study-74",
            }
        )
        changed_compact_payload.update(next_frame_fields)
        replacement_path.write_text(
            json.dumps(changed_compact_payload),
            encoding="utf-8",
        )
        replacement_path.replace(compact_path)
        next_display_payload = {
            **display_payload,
            **next_frame_fields,
            "frame_bundle_complete_v3": True,
        }
        display_path = session_dir / "display_state.json"
        display_replacement_path = display_path.with_suffix(".json.replacement")
        display_replacement_path.write_text(
            json.dumps(next_display_payload),
            encoding="utf-8",
        )
        display_replacement_path.replace(display_path)

        # The first poll on frame 74 returns frame 73 atomically, including its
        # own artifact URLs and WAIT permission, while one guarded refresh runs.
        stale_while_refreshing_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        changed_pair_response = stale_while_refreshing_response
        for _ in range(100):
            candidate = client.get(
                f"/v1/mobile/operator/state/v1/{session_id}?view=all"
            )
            if candidate.json()["surface"]["frame_id"] == next_frame_id:
                changed_pair_response = candidate
                break
            time.sleep(0.01)
        health_response = client.get(
            f"/v1/mobile/window-tracker/sessions/{session_id}/health"
        )
        session_response = client.get(
            f"/v1/mobile/window-tracker/sessions/{session_id}"
        )

    assert operator_response.status_code == 200
    assert same_frame_response.status_code == 200
    assert compact_churn_response.status_code == 200
    assert stale_while_refreshing_response.status_code == 200
    assert changed_pair_response.status_code == 200
    assert health_response.status_code == 200
    assert session_response.status_code == 200
    assert full_session_reads == []
    assert operator_response.json()["surface"]["frame_id"] == frame_id
    assert same_frame_response.json()["revision"] == operator_response.json()["revision"]
    assert compact_churn_response.json()["revision"] == operator_response.json()["revision"]
    assert compact_churn_response.json()["market"]["symbol"] == "EUR/USD OTC"
    assert stale_while_refreshing_response.json()["surface"]["frame_id"] == frame_id
    assert f"frame_id={frame_id}" in stale_while_refreshing_response.json()["surface"]["primary_url"]
    assert stale_while_refreshing_response.json()["permission"]["action"] == "WAIT"
    assert stale_while_refreshing_response.json()["permission"]["allowed"] is False
    assert operator_projection_calls == 2
    assert changed_pair_response.json()["surface"]["frame_id"] == next_frame_id
    assert changed_pair_response.json()["market"]["symbol"] == "GBP/JPY OTC"
    assert (
        changed_pair_response.json()["surface"]["semantic_identity"]
        != operator_response.json()["surface"]["semantic_identity"]
    )
    health = health_response.json()
    assert health["capture_worker_v3"]["frame_id"] == next_frame_id
    assert health["capture_worker_v3"]["display_frame_id"] == next_frame_id
    assert health["artifacts"]["chart"] == {
        "path": str(chart_path),
        "exists": True,
    }
    assert health["artifacts"]["overlay"] == {
        "path": str(overlay_path),
        "exists": True,
    }
    assert health["artifacts"]["window"] == {
        "path": str(window_path),
        "exists": True,
    }
    assert session_response.json()["display_frame_id"] == next_frame_id
    assert len(operator_response.content) < 300_000
    assert len(health_response.content) < 50_000
    assert len(session_response.content) < 300_000


def test_operator_same_frame_cache_is_complete_in_both_view_orders(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "operator-view-order-cache"
    frame_id = 14
    session_dir = (
        tmp_path
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / session_id
    )
    session_dir.mkdir(parents=True)
    complete_lineage = {
        "session_id": session_id,
        "capture_count": frame_id,
        "frame_index": frame_id,
        "display_frame_id": frame_id,
        "chart_frame_id": frame_id,
        "overlay_frame_id": frame_id,
        "full_overlay_frame_id": frame_id,
        "model_vote_frame_id": frame_id,
        "state_version": 140,
        "decision_version": 140,
        "last_display_surface_signature": "surface-14",
        "last_window_surface_signature": "surface-14",
        "last_study_surface_signature": "study-14",
        "overlay_source_window_signature": "surface-14",
        "overlay_source_study_signature": "study-14",
    }
    (session_dir / "compact_live_state.json").write_text(
        json.dumps(
            {
                **complete_lineage,
                "tracking_enabled": True,
                "tracking_summary": {
                    "detected_market": "EUR/USD OTC",
                    "market_selector_visual_fingerprint": "selector_v2_eurusd",
                },
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "display_state.json").write_text(
        json.dumps({**complete_lineage, "frame_bundle_complete_v3": True}),
        encoding="utf-8",
    )

    live_state = _fresh_payload()
    live_state.update(complete_lineage)
    zone = {
        "overlay_id": "view-order-demand",
        "type": "DEMAND_ZONE",
        "side": "BUY",
        "layer": "supply_demand",
        "bounds": [10, 20, 40, 60],
        "frame_id": frame_id,
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "confidence": 0.88,
        "lifecycle_state": "ACTIVE",
    }
    live_state["overlays"] = {"objects": [zone]}
    live_state["live_visual_state"] = {"overlays": {"objects": [zone]}}

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return json.loads(json.dumps(live_state))

        def latest_model_council_state(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {}

    def build_state(
        tracker: object,
        requested_session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        return json.loads(json.dumps(live_state))

    operator_projection_calls = 0
    original_operator_builder = mobile_app.build_operator_workspace_v1

    def counted_operator_builder(
        source: Mapping[str, object],
    ) -> dict[str, object]:
        nonlocal operator_projection_calls
        operator_projection_calls += 1
        return original_operator_builder(source)

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setenv("PHOENIXGUARD_LIVE_STATE_DIRECT_READ", "0")
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        build_state,
    )
    monkeypatch.setattr(
        mobile_app,
        "build_operator_workspace_v1",
        counted_operator_builder,
    )
    tracker = _Tracker()

    with TestClient(mobile_app.create_app(window_tracker_service=tracker)) as client:
        forecast_first = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=forecast"
        )
        all_second = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
    assert forecast_first.status_code == 200
    assert all_second.status_code == 200
    assert not any(
        row["family"] == "supply_demand"
        for row in forecast_first.json()["overlays"]
    )
    assert any(
        row["family"] == "supply_demand"
        for row in all_second.json()["overlays"]
    )
    assert operator_projection_calls == 1

    with TestClient(mobile_app.create_app(window_tracker_service=tracker)) as client:
        all_first = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        forecast_second = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=forecast"
        )
    assert all_first.status_code == 200
    assert forecast_second.status_code == 200
    assert any(
        row["family"] == "supply_demand"
        for row in all_first.json()["overlays"]
    )
    assert not any(
        row["family"] == "supply_demand"
        for row in forecast_second.json()["overlays"]
    )
    assert operator_projection_calls == 2


def test_operator_rollover_defers_one_refresh_until_after_stale_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "operator-background-rollover"
    now_epoch = time.time()
    active_frame = {"value": 14}
    active_revision = {
        "value": ("operator-revision-14", 14, now_epoch + 60.0),
    }

    def live_state_for_active_frame() -> dict[str, object]:
        frame_id = active_frame["value"]
        surface_signature = f"surface-{frame_id}"
        study_signature = f"study-{frame_id}"
        payload = _fresh_payload(now=now_epoch)
        payload.update(
            {
                "session_id": session_id,
                "capture_count": frame_id,
                "frame_index": frame_id,
                "display_frame_id": frame_id,
                "chart_frame_id": frame_id,
                "overlay_frame_id": frame_id,
                "full_overlay_frame_id": frame_id,
                "model_vote_frame_id": frame_id,
                "state_version": frame_id,
                "decision_version": frame_id,
                "last_display_surface_signature": surface_signature,
                "last_window_surface_signature": surface_signature,
                "last_study_surface_signature": study_signature,
                "overlay_source_window_signature": surface_signature,
                "overlay_source_study_signature": study_signature,
            }
        )
        command_center = _mutable_mapping(payload["decision_command_center"])
        command_center["current_movement"] = {
            **_mutable_mapping(command_center["current_movement"]),
            "frame_id": frame_id,
        }
        command_center["pressure_event"] = {
            **_mutable_mapping(command_center["pressure_event"]),
            "frame_id": frame_id,
        }
        command_center["forecast"] = {
            **_mutable_mapping(command_center["forecast"]),
            "frame_id": frame_id,
        }
        payload["overlays"] = {"objects": []}
        payload["live_visual_state"] = {"overlays": {"objects": []}}
        return payload

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return live_state_for_active_frame()

        def latest_model_council_state(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {}

    def build_state(
        tracker: object,
        requested_session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        return live_state_for_active_frame()

    operator_projection_calls = 0
    original_operator_builder = mobile_app.build_operator_workspace_v1

    def counted_operator_builder(
        source: Mapping[str, object],
    ) -> dict[str, object]:
        nonlocal operator_projection_calls
        operator_projection_calls += 1
        return original_operator_builder(source)

    deferred_tasks: list[
        tuple[Callable[..., object], tuple[object, ...], dict[str, object]]
    ] = []

    def defer_task(
        _background_tasks: object,
        task: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> None:
        deferred_tasks.append((task, args, kwargs))

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setenv("PHOENIXGUARD_LIVE_STATE_DIRECT_READ", "0")
    monkeypatch.setattr(mobile_app, "_LIVE_STATE_V3_CACHE_TTL_SEC", 0.0)
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        build_state,
    )
    monkeypatch.setattr(
        mobile_app,
        "build_operator_workspace_v1",
        counted_operator_builder,
    )
    def projection_source_revision(
        requested_session_id: str,
    ) -> tuple[str, int, float] | None:
        return (
            active_revision["value"]
            if requested_session_id == session_id
            else None
        )

    monkeypatch.setattr(
        mobile_app,
        "_operator_projection_source_revision",
        projection_source_revision,
    )
    monkeypatch.setattr(mobile_app.BackgroundTasks, "add_task", defer_task)

    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        warm_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        assert warm_response.status_code == 200
        assert warm_response.json()["surface"]["frame_id"] == 14
        assert operator_projection_calls == 1

        active_frame["value"] = 15
        active_revision["value"] = (
            "operator-revision-15",
            15,
            now_epoch + 60.0,
        )
        stale_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=forecast"
        )
        duplicate_poll = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=forecast"
        )

        assert stale_response.status_code == 200
        assert stale_response.json()["surface"]["frame_id"] == 14
        assert stale_response.json()["freshness"]["state"] == "STALE"
        assert stale_response.json()["permission"]["action"] == "WAIT"
        assert duplicate_poll.json()["surface"]["frame_id"] == 14
        assert operator_projection_calls == 1
        assert len(deferred_tasks) == 1

        task, args, kwargs = deferred_tasks.pop()
        task(*args, **kwargs)

        refreshed_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )

    assert operator_projection_calls == 2
    assert refreshed_response.status_code == 200
    assert refreshed_response.json()["surface"]["frame_id"] == 15
    assert deferred_tasks == []


def test_bounded_operator_context_keeps_safe_two_candle_forecast_without_raw_features() -> None:
    bounded_context = cast(
        Callable[[Mapping[str, object]], dict[str, object]],
        getattr(mobile_app, "_bounded_operator_projection_context"),
    )
    source: dict[str, object] = {
        "session_id": "bounded-forecast",
        "tracking_enabled": True,
        "display_frame_id": 14,
        "chart_frame_id": 14,
        "overlay_frame_id": 14,
        "full_overlay_frame_id": 14,
        "model_vote_frame_id": 14,
        "last_capture_epoch": 99.0,
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

    context = bounded_context(source)
    serialized_context = json.dumps(context)
    workspace = _build_workspace(context, now_epoch=100.0)

    assert "features" not in serialized_context
    assert "raw_model_logits" not in serialized_context
    assert workspace["forecast"]["direction"] == "SELL"
    assert workspace["forecast"]["confidence"] == 0.64


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
    assert {row["family"] for row in workspace["overlays"]} == {"lstm"}
    assert {row.get("forecast_role") for row in workspace["overlays"]} == {
        "center",
        "band_90",
    }
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


@pytest.mark.parametrize("forecast_status", ["AUTHORIZED", "NO_EDGE", "LOW_CONFIDENCE"])
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
    if forecast_status in {"NO_EDGE", "LOW_CONFIDENCE"}:
        assert "no reliable edge" in workspace["forecast"]["summary"].lower()
        assert "diagnostic only" in workspace["forecast"]["summary"].lower()
        if forecast_status == "LOW_CONFIDENCE":
            assert "input quality is low" in workspace["forecast"]["summary"].lower()
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


def test_safe_operator_projection_retains_bounded_forecast_scenarios() -> None:
    project_rows = cast(
        Callable[[object], list[dict[str, object]]],
        getattr(mobile_app, "_safe_operator_overlay_rows"),
    )
    scenario_points = [
        [round(0.55 + index * 0.03, 4), round(0.48 - index * 0.01, 4)]
        for index in range(14)
    ]
    raw_scenarios = [
        {
            "side": side,
            "label": f"{side} PATH",
            "probability": probability,
            "probability_calibrated": False,
            "selected": side == "BUY",
            "line_points": scenario_points,
            "event_count": 99,
            "forecast_path": [{"raw_model_value": 0.123}],
            "private_artifact_path": rf"C:\private\{side.lower()}.pt",
        }
        for side, probability in (
            ("BUY", 1.2),
            ("SELL", 0.21),
            ("NEUTRAL", -0.4),
            ("BUY", 0.01),
        )
    ]

    projected = project_rows(
        [
            {
                "id": "lstm-path-current",
                "family": "lstm",
                "frame_id": 14,
                "forecast_coordinate_units": "normalized",
                "forecast_scenarios": raw_scenarios,
                "trajectory_mode": "BUY",
                "trajectory_mode_probability_calibrated": False,
                "forecast_engine": "SCENE_FORECASTER_V3",
                "forecast_computed_frame_id": 12,
                "source_forecast_frame_id": 12,
                "geometry_projected_frame_id": 14,
                "geometry_frame_match_verified": True,
                "geometry_reprojected_from_cache": True,
                "detector_coverage_rebase_applied": True,
                "cache_replaced_for_detector_coverage_rebase": True,
                "geometry_projection_provenance": {
                    "status": "REANCHORED",
                    "source_forecast_frame_id": 12,
                    "source_geometry_frame_id": 12,
                    "projected_frame_id": 14,
                    "verified": True,
                    "pointwise_clipping_applied": False,
                },
            }
        ]
    )

    assert len(projected) == 1
    row = projected[0]
    scenarios = cast(list[dict[str, object]], row["forecast_scenarios"])
    assert len(scenarios) == 3
    assert [scenario["side"] for scenario in scenarios] == [
        "BUY",
        "SELL",
        "NEUTRAL",
    ]
    assert [scenario["probability"] for scenario in scenarios] == [1.0, 0.21, 0.0]
    assert all(len(cast(list[object], scenario["line_points"])) == 13 for scenario in scenarios)
    assert all(scenario["event_count"] == 12 for scenario in scenarios)
    assert all(scenario["probability_calibrated"] is False for scenario in scenarios)
    assert row["trajectory_mode"] == "BUY"
    assert row["trajectory_mode_probability_calibrated"] is False
    assert row["geometry_frame_match_verified"] is True
    assert row["geometry_projected_frame_id"] == 14
    assert row["forecast_computed_frame_id"] == 12
    geometry_provenance = cast(
        Mapping[str, object],
        row["geometry_projection_provenance"],
    )
    assert geometry_provenance["verified"] is True
    assert row["detector_coverage_rebase_applied"] is True
    assert row["cache_replaced_for_detector_coverage_rebase"] is True
    serialized = json.dumps(projected)
    assert "raw_model_value" not in serialized
    assert "private_artifact_path" not in serialized
    assert r"C:\private" not in serialized


def test_safe_operator_projection_accepts_role_complete_scene_scenarios() -> None:
    project_rows = cast(
        Callable[[object], list[dict[str, object]]],
        getattr(mobile_app, "_safe_operator_overlay_rows"),
    )
    bundle = _complete_forecast_bundle(selected_side="SELL")
    legacy_scenarios = cast(list[dict[str, object]], bundle["forecast_scenarios"])
    paths = {
        str(scenario["side"]): cast(list[list[float]], scenario["line_points"])
        for scenario in legacy_scenarios
    }

    def candles_for(points: list[list[float]]) -> list[dict[str, object]]:
        return [
            {
                "step": step,
                "label": f"E{step}",
                "x_norm": points[step][0],
                "open_y_norm": points[step - 1][1],
                "high_y_norm": round(
                    min(points[step - 1][1], points[step][1]) - 0.004,
                    6,
                ),
                "low_y_norm": round(
                    max(points[step - 1][1], points[step][1]) + 0.004,
                    6,
                ),
                "close_y_norm": points[step][1],
                "movement_side": "BUY"
                if points[step][1] < points[step - 1][1]
                else "SELL",
                "body_bias": "BUY"
                if points[step][1] < points[step - 1][1]
                else "SELL",
                "direction_conflict": False,
                "private_model_value": 0.123,
            }
            for step in range(1, 13)
        ]

    scene_scenarios = [
        {
            "role": role,
            "side": side,
            "label": label,
            "probability": probability,
            "probability_calibrated": False,
            "selected": role == "base",
            "raw_selected": role == "bear",
            "candidate": role == "bear",
            "line_points": paths[path_side],
            "forecast_candles": candles_for(paths[path_side]),
        }
        for role, side, path_side, label, probability in (
            ("base", "SELL", "SELL", "MEDOID PATH", 0.39),
            ("bull", "BUY", "BUY", "UPPER PATH", 0.29),
            # Endpoint-derived sides may duplicate; roles identify the scenario.
            ("bear", "SELL", "SELL", "LOWER PATH", 0.32),
        )
    ]
    bundle["forecast_scenarios"] = scene_scenarios
    anchor = cast(dict[str, object], bundle["forecast_anchor"])
    anchor["source"] = "TRACKER_LATEST_CLOSED_CANDLE"

    projected = project_rows(
        [
            {
                "id": "scene-forecast-current",
                "family": "lstm",
                "frame_id": 14,
                "forecast_role": "composite",
                "forecast_coordinate_units": "normalized",
                "coordinate_units": "normalized",
                "forecast_engine": "SCENE_FORECASTER_V3",
                "forecast_provider": "CHRONOS_2_LOCAL",
                "belief_state": "REACQUIRING",
                "committed_side": "HOLD",
                "candidate_side": "SELL",
                "forecast_revision": 11,
                "belief_revision": 4,
                **bundle,
            }
        ]
    )

    assert len(projected) == 1
    row = projected[0]
    scenarios = cast(list[dict[str, object]], row["forecast_scenarios"])
    assert {str(scenario["role"]) for scenario in scenarios} == {
        "base",
        "bull",
        "bear",
    }
    assert [str(scenario["side"]) for scenario in scenarios].count("SELL") == 2
    candidate = next(
        scenario for scenario in scenarios if scenario.get("candidate") is True
    )
    assert candidate["role"] == "bear"
    assert candidate["raw_selected"] is True
    assert all(
        len(cast(list[object], scenario["forecast_candles"])) == 12
        for scenario in scenarios
    )
    assert cast(dict[str, object], row["forecast_anchor"])["source"] == (
        "TRACKER_LATEST_CLOSED_CANDLE"
    )
    assert row["forecast_engine"] == "SCENE_FORECASTER_V3"
    assert row["forecast_provider"] == "CHRONOS_2_LOCAL"
    assert row["belief_state"] == "REACQUIRING"
    assert row["committed_side"] == "HOLD"
    assert row["candidate_side"] == "SELL"
    assert "private_model_value" not in json.dumps(projected)


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


def test_latest_candle_publishes_atomic_tracker_close_point_on_chart_pixel_plane() -> None:
    payload = _fresh_payload()
    payload.update(
        {
            "chart_frame_id": 14,
            "overlay_frame_id": 14,
        }
    )
    tracking_summary = _mutable_mapping(payload["tracking_summary"])
    tracking_summary.update(
        {
            "artifact_integrity": {
                "matches_selected_plane": True,
                "chart": {"width": 1000, "height": 700},
                "selected_plane": {"width": 1000, "height": 700},
                "chart_artifact_frame_id": 14,
                "artifact_frame_id": 14,
            },
            "chart_region": {
                "pixel_bbox": [0, 0, 1000, 700],
                "width": 1000,
                "height": 700,
            },
            "tracked_candles": [
                {
                    "bbox": [470, 210, 480, 290],
                    "center_x_px": 475.0,
                    "close_y_px": 235.0,
                },
                {
                    "bbox": [500, 200, 510, 300],
                    "center_x_px": 505.0,
                    "close_y_px": 276.25,
                },
            ],
        }
    )
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "latest-candle",
                "type": "CURRENT_CANDLE",
                "role": "current_candle",
                "is_latest_candle": True,
                "layer": "recent_candles",
                "bounds": [500, 200, 510, 300],
                # Producer points are not close evidence at the public boundary.
                "points": [[999, 699]],
                "frame_id": 14,
                "coordinate_mode": "CHART_IMAGE_SPACE",
                "coordinate_units": "pixels",
            }
        ]
    }

    current = _build_workspace(payload, now_epoch=100.0)["overlays"][0]

    assert current["points"] == [[505.0, 276.25]]

    artifact_integrity = _mutable_mapping(tracking_summary["artifact_integrity"])
    artifact_integrity["chart_artifact_frame_id"] = 13
    artifact_integrity["artifact_frame_id"] = 13
    stale_current = _build_workspace(payload, now_epoch=100.0)["overlays"][0]
    assert stale_current["points"] == []


def test_compact_live_chain_preserves_bounded_tracker_close_for_operator(
    tmp_path: Path,
) -> None:
    frame_id = 14
    window_path = tmp_path / "window.png"
    chart_path = tmp_path / "chart.png"
    overlay_path = tmp_path / "overlay.png"
    Image.new("RGB", (1200, 800), color=(20, 24, 32)).save(window_path)
    for path in (chart_path, overlay_path):
        Image.new("RGB", (1000, 700), color=(20, 24, 32)).save(path)

    tracked_candles = [
        {
            "bbox": [250 + index * 25, 200, 260 + index * 25, 300],
            "center_x_px": 255.0 + index * 25.0,
            "close_y_px": 235.0 + index,
            "direction": "BUY",
        }
        for index in range(9)
    ]
    tracked_candles.append(
        {
            "bbox": [500, 200, 510, 300],
            "center_x_px": 505.0,
            "close_y_px": 276.25,
            "direction": "BUY",
        }
    )
    session: dict[str, object] = {
        "session_id": "compact-close-chain",
        "status": "running",
        "tracking_enabled": True,
        "capture_interval_sec": 15.0,
        "last_capture_epoch": 100.0,
        "display_capture_epoch": 100.0,
        "display_published_epoch": 100.0,
        "capture_count": frame_id,
        "frame_index": frame_id,
        "display_frame_id": frame_id,
        "chart_frame_id": frame_id,
        "overlay_frame_id": frame_id,
        "full_overlay_frame_id": frame_id,
        "model_vote_frame_id": frame_id,
        "state_version": frame_id,
        "descriptor": {
            "title": "The Most Innovative Trading Platform - Microsoft Edge",
            "hwnd": 808,
        },
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
        },
        "tracking_summary": {
            "detected_market": "EUR/USD OTC",
            "detected_timeframe": "M5",
            "artifact_integrity": {
                "matches_selected_plane": True,
                "chart": {"width": 1000, "height": 700},
                "selected_plane": {"width": 1000, "height": 700},
                "chart_artifact_frame_id": frame_id,
                "artifact_frame_id": frame_id,
            },
            "chart_region": {
                "pixel_bbox": [0, 0, 1000, 700],
                "width": 1000,
                "height": 700,
            },
            "tracked_candles": tracked_candles,
        },
        "latest_signal": {
            "market": "EUR/USD OTC",
            "focus_timeframe": "M5",
            "action": "BUY",
            "side": "BUY",
            "published_epoch": 100.0,
        },
    }
    active_objects: list[dict[str, object]] = [
        {
            "overlay_id": "current-candle-chain",
            "object_id": "current-candle-chain",
            "track_id": "current-candle-chain",
            "frame_id": frame_id,
            "truth_score": 0.95,
            "lifecycle_state": "ACTIVE",
            "overlay": {
                "type": "CURRENT_CANDLE",
                "role": "current_candle",
                "is_latest_candle": True,
                "side": "BUY",
                "bounds": [500, 200, 510, 300],
                "coordinate_mode": "CHART_IMAGE_SPACE",
                "coordinate_units": "pixels",
                "anchor_type": "CANDLE",
                "anchor_candles": [9],
            },
        }
    ]

    compact_live_state = build_live_state_v3(
        session,
        artifacts={
            "window": window_path,
            "chart": chart_path,
            "overlay": overlay_path,
        },
        active_objects=active_objects,
        registry_entries=active_objects,
        overlay_mode="INSPECTOR",
        compact_public=True,
        now_epoch=100.0,
    )
    compact_tracking = _mutable_mapping(compact_live_state["tracking_summary"])
    assert len(cast(Sequence[object], compact_tracking["tracked_candles"])) == 8
    assert "artifact_integrity" in compact_tracking
    assert "chart_region" in compact_tracking

    bounded_context = mobile_app._bounded_operator_projection_context(  # pyright: ignore[reportPrivateUsage]
        compact_live_state
    )
    projection_input = mobile_app._merge_operator_projection_input(  # pyright: ignore[reportPrivateUsage]
        bounded_context,
        compact_live_state,
    )
    workspace = _build_workspace(projection_input, now_epoch=100.0)
    current = next(
        row
        for row in workspace["overlays"]
        if row["family"] == "current_candles" and row["label"] == "Current price"
    )

    assert current["points"] == [[505.0, 276.25]]


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
                "role": "lstm_forecast_composite_no_edge",
                "forecast_role": "composite",
                "forecast_status": "NO_EDGE",
                "forecast_authorized": False,
                "forecast_direction": "BUY",
                "body_bias": "SELL",
                "layer": "prediction_path",
                "bounds": [0.55, 0.20, 0.90, 0.40],
                **_complete_forecast_bundle(selected_side="SELL"),
                "trajectory_mode": "SELL",
                "trajectory_mode_probability_calibrated": False,
                "interval": {"calibrated": False, "method": "UNAVAILABLE"},
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
    assert by_id["lstm-path-current"]["family"] == "lstm"
    assert by_id["lstm-path-current"]["layer"] == "prediction_path"
    assert by_id["lstm-path-current"]["label"] == "LSTM V3 events - NO EDGE - diagnostic"
    assert by_id["lstm-path-current"]["coordinate_units"] == "normalized"
    assert by_id["lstm-path-current"].get("forecast_role") == "composite"
    assert by_id["lstm-path-current"].get("forecast_status") == "NO_EDGE"
    assert by_id["lstm-path-current"].get("forecast_authorized") is False
    assert by_id["lstm-path-current"].get("horizon_unit") == "CANDLE_EVENTS"
    assert by_id["lstm-path-current"].get("clock_time_assumption") == "NONE"
    assert by_id["lstm-path-current"].get("uncertainty_level") is None
    assert by_id["lstm-path-current"].get("forecast_direction") == "BUY"
    assert by_id["lstm-path-current"].get("body_bias") == "SELL"
    assert by_id["lstm-path-current"].get("trajectory_mode") == "SELL"
    assert (
        by_id["lstm-path-current"].get("trajectory_mode_probability_calibrated")
        is False
    )
    scenarios = cast(
        list[dict[str, object]],
        by_id["lstm-path-current"].get("forecast_scenarios"),
    )
    assert [row["side"] for row in scenarios] == ["SELL", "BUY", "NEUTRAL"]
    assert [row["selected"] for row in scenarios] == [True, False, False]
    assert [row["probability"] for row in scenarios] == [0.18, 0.72, 0.10]
    assert len(cast(list[object], scenarios[0]["line_points"])) == 13
    assert scenarios[0]["line_points"] == by_id["lstm-path-current"]["line_points"]
    assert all("forecast_path" not in row for row in scenarios)
    assert all("private_artifact_path" not in row for row in scenarios)
    interval = by_id["lstm-path-current"].get("interval")
    assert isinstance(interval, dict)
    interval = cast(dict[str, object], interval)
    assert interval["calibrated"] is False
    assert interval["method"] == "UNAVAILABLE"
    assert interval["status"] == "UNAVAILABLE"
    lstm_rows = [row for row in overlays if row["family"] == "lstm"]
    assert [row.get("forecast_role") for row in lstm_rows].count("composite") == 1
    assert by_id["council-current"]["family"] == "council"
    assert by_id["council-current"]["label"] == "Council read"
    assert by_id["smc-order-block-current"]["family"] == "smc"
    assert by_id["smc-order-block-current"]["layer"] == "smart_money"
    assert by_id["smc-order-block-current"]["label"] == "SMC order block"


def test_precision_projected_lstm_composite_survives_full_public_chain() -> None:
    payload = _fresh_payload(side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity["state"] = "WAIT"
    opportunity["integrity_valid"] = False

    chart_left = 100.0
    chart_top = 50.0
    chart_width = 1628.0
    chart_height = 861.0
    bundle = _complete_forecast_bundle(
        selected_side="BUY",
        anchor_x=0.55,
        anchor_y=0.30,
    )
    normalized_line = cast(list[list[float]], bundle["line_points"])
    pixel_line = [
        [
            round(chart_left + point[0] * chart_width, 6),
            round(chart_top + point[1] * chart_height, 6),
        ]
        for point in normalized_line
    ]
    payload.update(
        {
            "chart_frame": {
                "artifact": {"width": chart_width, "height": chart_height}
            },
            "scene_graph": {
                "chart_region_chart_bounds": [
                    chart_left,
                    chart_top,
                    chart_left + chart_width,
                    chart_top + chart_height,
                ]
            },
            "overlays": {
                "objects": [
                    {
                        "overlay_id": "precision-projected-lstm",
                        "type": "LSTM_STUDY",
                        "side": "NEUTRAL",
                        "forecast_direction": "BUY",
                        "layer": "prediction_path",
                        "role": "lstm_forecast_composite_low_confidence",
                        "bounds": [
                            min(point[0] for point in pixel_line) - 2.0,
                            min(point[1] for point in pixel_line) - 2.0,
                            max(point[0] for point in pixel_line) + 2.0,
                            max(point[1] for point in pixel_line) + 2.0,
                        ],
                        **bundle,
                        "line_points": pixel_line,
                        "forecast_band_points": [
                            [0.55, 0.29],
                            [0.90, 0.23],
                            [0.90, 0.25],
                            [0.55, 0.31],
                            [0.55, 0.29],
                        ],
                        "forecast_coordinate_space": "chart",
                        "forecast_coordinate_units": "normalized",
                        "forecast_quality_status": "LOW_CONFIDENCE",
                        "trade_authorization_status": "NO_EDGE",
                        "frame_id": 14,
                        "coordinate_mode": "CHART_IMAGE_SPACE",
                    }
                ]
            },
        }
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    lstm_rows = [row for row in workspace["overlays"] if row["family"] == "lstm"]

    assert len(lstm_rows) == 1
    lstm = lstm_rows[0]
    assert lstm["coordinate_space"] == "chart"
    assert lstm["coordinate_units"] == "normalized"
    assert lstm.get("forecast_coordinate_units") == "normalized"
    assert len(cast(list[object], lstm["line_points"])) == 13
    assert lstm.get("forecast_band_points") == [
        [0.55, 0.29],
        [0.90, 0.23],
        [0.90, 0.25],
        [0.55, 0.31],
        [0.55, 0.29],
    ]
    assert len(cast(list[object], lstm.get("forecast_candles"))) == 12
    scenarios = cast(list[dict[str, object]], lstm.get("forecast_scenarios"))
    assert len(scenarios) == 3
    assert sum(bool(scenario["selected"]) for scenario in scenarios) == 1
    selected = next(scenario for scenario in scenarios if scenario["selected"])
    assert selected["line_points"] == lstm["line_points"]
    assert lstm.get("forecast_anchor") == {
        "x_norm": 0.55,
        "y_norm": 0.30,
        "verified_latest_close": True,
        "source": "TRACKER_LATEST_CLOSE",
    }
    assert lstm.get("forecast_status") == "LOW_CONFIDENCE"
    assert lstm.get("forecast_authorized") is False
    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["permission"]["allowed"] is False

    safe_rows = mobile_app._safe_operator_overlay_rows(  # pyright: ignore[reportPrivateUsage]
        workspace["overlays"]
    )
    safe_lstm = [row for row in safe_rows if row.get("family") == "lstm"]
    assert len(safe_lstm) == 1
    assert len(cast(list[object], safe_lstm[0]["line_points"])) == 13


@pytest.mark.parametrize("failure", ["missing_transform", "space_mismatch"])
def test_precision_projected_lstm_composite_fails_closed_without_one_chart_plane(
    failure: str,
) -> None:
    payload = _fresh_payload(side="BUY")
    bundle = _complete_forecast_bundle(selected_side="BUY")
    normalized_line = cast(list[list[float]], bundle["line_points"])
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": f"invalid-projected-lstm-{failure}",
                "type": "LSTM_STUDY",
                "layer": "prediction_path",
                "role": "lstm_forecast_composite_no_edge",
                "bounds": [1100.0, 260.0, 1500.0, 520.0],
                **bundle,
                "line_points": [
                    [round(point[0] * 1628.0, 6), round(point[1] * 861.0, 6)]
                    for point in normalized_line
                ],
                "forecast_coordinate_space": (
                    "window" if failure == "space_mismatch" else "chart"
                ),
                "forecast_coordinate_units": "normalized",
                "frame_id": 14,
                "coordinate_mode": "CHART_IMAGE_SPACE",
            }
        ]
    }
    if failure == "space_mismatch":
        payload["chart_frame"] = {
            "artifact": {"width": 1628.0, "height": 861.0}
        }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert not any(row["family"] == "lstm" for row in workspace["overlays"])


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


def test_studied_history_semantics_survive_frames_but_geometry_reprojects() -> None:
    def workspace(
        *,
        frame_id: int,
        symbol: str,
        bounds: list[float],
    ) -> _OperatorWorkspaceView:
        payload = _fresh_payload(now=100.0)
        payload["display_frame_id"] = frame_id
        payload["state_version"] = frame_id
        tracking = _mutable_mapping(payload["tracking_summary"])
        tracking["detected_market"] = symbol
        # The raw selector crop changes across captures on the same pair and
        # must not flush the studied-history semantic namespace.
        tracking["market_selector_visual_fingerprint"] = f"selector-frame-{frame_id}"
        payload["broker_source_lock_id"] = "locked-broker-surface"
        command = _mutable_mapping(payload["decision_command_center"])
        for key in ("current_movement", "pressure_event", "forecast"):
            _mutable_mapping(command[key])["frame_id"] = frame_id
        payload["overlays"] = {
            "objects": [
                {
                    "overlay_id": "observed-path-frame-copy",
                    "track_id": "observed-path-stable",
                    "type": "PROGRESSION_PATH",
                    "layer": "historical_replay",
                    "bounds": bounds,
                    "line_points": [
                        [bounds[0], bounds[3]],
                        [bounds[2], bounds[1]],
                    ],
                    "frame_id": frame_id,
                    "coordinate_mode": "CHART_NORMALIZED",
                    "lifecycle_state": "HISTORICAL",
                    "anchor_candles": [3, 8],
                }
            ]
        }
        return _build_workspace(payload, now_epoch=100.0)

    first = workspace(
        frame_id=14,
        symbol="EUR/USD",
        bounds=[0.10, 0.20, 0.40, 0.60],
    )
    reprojected = workspace(
        frame_id=15,
        symbol="EUR/USD",
        bounds=[0.14, 0.18, 0.44, 0.58],
    )
    changed_pair = workspace(
        frame_id=16,
        symbol="GBP/USD",
        bounds=[0.14, 0.18, 0.44, 0.58],
    )

    first_surface = cast(Mapping[str, object], first["surface"])
    reprojected_surface = cast(Mapping[str, object], reprojected["surface"])
    changed_pair_surface = cast(Mapping[str, object], changed_pair["surface"])
    assert first_surface["semantic_identity"] == reprojected_surface["semantic_identity"]
    assert (
        first_surface["overlay_semantic_revision"]
        == reprojected_surface["overlay_semantic_revision"]
    )
    assert (
        first_surface["overlay_geometry_revision"]
        == reprojected_surface["overlay_geometry_revision"]
    )
    assert first_surface["semantic_identity"] != changed_pair_surface["semantic_identity"]
    first_row = first["overlays"][0]
    reprojected_row = reprojected["overlays"][0]
    assert first_row.get("semantic_id") == reprojected_row.get("semantic_id")
    assert (
        first_row.get("overlay_semantic_revision")
        == reprojected_row.get("overlay_semantic_revision")
    )
    assert (
        first_row.get("overlay_geometry_revision")
        != reprojected_row.get("overlay_geometry_revision")
    )


def test_stable_selector_identity_namespaces_unknown_market_history() -> None:
    def semantic_identity(fingerprint: str) -> str:
        payload = _fresh_payload(now=100.0)
        tracking = _mutable_mapping(payload["tracking_summary"])
        tracking["detected_market"] = ""
        tracking["market_selector_visual_fingerprint"] = fingerprint
        built = _build_workspace(payload, now_epoch=100.0)
        surface = cast(Mapping[str, object], built["surface"])
        return str(surface["semantic_identity"])

    first = semantic_identity("selector_v2_cad_jpy")
    assert semantic_identity("selector_v2_cad_jpy") == first
    assert semantic_identity("selector_v2_eur_usd") != first
    # Legacy raw-header hashes remain diagnostic and cannot churn history.
    assert semantic_identity("selector-frame-1") == semantic_identity("selector-frame-2")


def test_operator_projection_rejects_normalized_geometry_outside_chart_plane() -> None:
    payload = _fresh_payload()
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "floating-zone",
                "type": "DEMAND_ZONE",
                "layer": "supply_demand",
                "bounds": [1.20, 0.20, 1.40, 0.40],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "anchored-zone",
                "track_id": "demand-zone-stable",
                "type": "DEMAND_ZONE",
                "layer": "supply_demand",
                "bounds": [0.20, 0.20, 0.40, 0.40],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "anchor_candles": [4, 5],
            },
        ]
    }

    rows = _build_workspace(payload, now_epoch=100.0)["overlays"]

    assert [row["id"] for row in rows] == ["anchored-zone"]
    assert str(rows[0].get("anchor_id")).startswith("anchor_")


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
        "execution_controls": snapshot["execution_controls"],
        "tracking_summary": {
            "detected_market": "GBP/USD",
            "detected_timeframe": "M5",
            "last_capture_epoch": 199.0,
        },
        "recent_studies": snapshot["recent_studies"],
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
    route_lstm = next(
        cast(dict[str, object], item)
        for item in cast(list[object], compact_objects)
        if isinstance(item, dict)
        and cast(dict[str, object], item).get("overlay_id") == "route-lstm"
    )
    route_lstm.update(
        {
            "role": "lstm_forecast_composite_no_edge",
            **_complete_forecast_bundle(
                selected_side="BUY",
                anchor_x=0.58,
                anchor_y=0.50,
            ),
            "forecast_coordinate_space": "chart",
            "forecast_coordinate_units": "normalized",
            "coordinate_mode": "CHART_NORMALIZED",
            "trajectory_mode": "BUY",
            "trajectory_mode_probability_calibrated": False,
        }
    )
    compact_state["live_visual_state"] = {"overlays": compact_state["overlays"]}
    observed_call: dict[str, object] = {}
    captured_projection_input: dict[str, object] = {}

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            raise AssertionError(
                f"operator hot path must not read the full session: {requested_session_id}"
            )

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
    compact_lstm = next(
        cast(Mapping[str, object], row)
        for row in cast(Sequence[object], captured_overlays["objects"])
        if isinstance(row, Mapping)
        and cast(Mapping[str, object], row).get("overlay_id") == "route-lstm"
    )
    compact_scenarios = cast(
        Sequence[Mapping[str, object]],
        compact_lstm["forecast_scenarios"],
    )
    assert len(compact_scenarios) == 3
    assert all(len(cast(Sequence[object], row["line_points"])) == 13 for row in compact_scenarios)
    assert compact_lstm["trajectory_mode"] == "BUY"
    assert compact_lstm["trajectory_mode_probability_calibrated"] is False
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
        "smc": ("INSPECTOR", {"smc"}),
        "two-candle": ("INSPECTOR", {"two_candle"}),
        "lstm": ("INSPECTOR", {"lstm"}),
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
        if public_view == "lstm":
            forecast_row = next(
                row
                for row in public_workspace["overlays"]
                if row.get("forecast_role") == "composite"
            )
            endpoint_scenarios = cast(
                Sequence[Mapping[str, object]],
                forecast_row.get("forecast_scenarios"),
            )
            assert len(endpoint_scenarios) == 3
            assert all(
                len(cast(Sequence[object], row["line_points"])) == 13
                for row in endpoint_scenarios
            )
            assert forecast_row.get("trajectory_mode") == "BUY"
            assert forecast_row.get("trajectory_mode_probability_calibrated") is False
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
    assert counts == {"two_candle": 1, "lstm": 4, "prediction": 0}
    repaired_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    repaired_families = [
        str(row.get("family"))
        for row in cast(list[dict[str, object]], repaired_snapshot["overlays"])
    ]
    assert repaired_families.count("two_candle") == 1
    assert repaired_families.count("lstm") == 4
    assert repaired_families.count("prediction") == 0


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
        "lstm": 4,
        "prediction": 0,
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
    selected_lstm = next(
        row
        for row in waiting_workspace["overlays"]
        if row["family"] == "lstm" and row.get("forecast_role") == "center"
    )
    assert selected_lstm["side"] == "SELL"
    assert selected_lstm["confidence"] == 0.77
    assert waiting_workspace["forecast"]["confidence"] == selected_lstm["confidence"]
    assert waiting_workspace["forecast"]["state"] == "STALE"
    assert waiting_workspace["permission"] == {
        **waiting_workspace["permission"],
        "action": "WAIT",
        "allowed": False,
        "side": "NEUTRAL",
    }
    assert all(
        row["lifecycle"] == "current"
        for row in waiting_workspace["overlays"]
    )
    assert {row["frame_id"] for row in waiting_workspace["overlays"]} == {14}
    fresh_semantic_ids = {
        row["id"]: row.get("semantic_id")
        for row in fresh_workspace["overlays"]
    }
    waiting_semantic_ids = {
        row["id"]: row.get("semantic_id")
        for row in waiting_workspace["overlays"]
    }
    assert all(fresh_semantic_ids.values())
    assert waiting_semantic_ids == fresh_semantic_ids
    forecast_workspace = cast(_OperatorWorkspaceView, forecast_response.json())
    assert {row["family"] for row in forecast_workspace["overlays"]} == {
        "two_candle",
        "lstm",
    }
    assert sum(
        row.get("forecast_role") == "center"
        for row in forecast_workspace["overlays"]
    ) == 1
    assert all(
        row.get("forecast_authorized") is True
        and row.get("forecast_status") == "AUTHORIZED"
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
