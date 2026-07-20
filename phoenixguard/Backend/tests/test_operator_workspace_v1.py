from __future__ import annotations

import hashlib
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
    kind: str
    kind_label: str
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
    geometry_kind: NotRequired[str]
    horizon_unit: NotRequired[str]
    clock_time_assumption: NotRequired[str]
    uncertainty_level: NotRequired[float]
    semantic_id: NotRequired[str]
    overlay_semantic_revision: NotRequired[str]
    overlay_geometry_revision: NotRequired[str]
    anchor_id: NotRequired[str]
    forecast_scenarios: NotRequired[object]
    forecast_candles: NotRequired[list[dict[str, object]]]
    forecast_anchor: NotRequired[object]
    baseline_locked: NotRequired[bool]
    trajectory_mode: NotRequired[str]
    trajectory_mode_probability_calibrated: NotRequired[bool]
    interval: NotRequired[object]
    positioning_status: NotRequired[str]
    positioning_basis: NotRequired[str]
    positioning_mode: NotRequired[str]
    immutable_geometry: NotRequired[bool]
    evidence_only: NotRequired[bool]


class _HistoryView(TypedDict):
    observed_at: float | None
    direction: str
    state: str
    summary: str
    frame_id: _FrameId
    id: NotRequired[str]
    episode_id: NotRequired[str]
    event_index: NotRequired[int]
    predicted_direction: NotRequired[str]
    agreement: NotRequired[bool | None]


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
        "tracking_episode": {
            "schema_version": "PG_TRACKING_EPISODE_V1",
            "episode_id": "episode-test-active",
            "state": "ACTIVE",
            "revision": 1,
            "event_horizon": 12,
            "event_cursor": 0,
            "pair": "EUR/USD",
            "timeframe": "M5",
            "committed_plan": {"decision": {"action": side}},
            "events": [],
        },
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


def _positioning_anchor_rows(
    *,
    scale_x: float = 1.0,
    offset_x: float = 0.0,
    scale_y: float = 1.0,
    offset_y: float = 0.0,
) -> list[dict[str, object]]:
    baseline = (
        ("stable-a", 0.18, 0.34),
        ("stable-b", 0.38, 0.47),
        ("stable-c", 0.61, 0.59),
        ("stable-d", 0.79, 0.41),
    )
    return [
        {
            "track_id": anchor_id,
            "is_closed": True,
            "x_norm": round(scale_x * x_norm + offset_x, 6),
            "close_y_norm": round(scale_y * y_norm + offset_y, 6),
        }
        for anchor_id, x_norm, y_norm in baseline
    ]


def _ready_positioning_preview_candidate(
    *,
    frame_id: int = 14,
) -> dict[str, object]:
    zones = [
        {
            "zone_id": "order-zone-private-buy-limit",
            "intent": "ENTRY_LIMIT",
            "order_kind": "BUY_LIMIT",
            "side": "BUY",
            "bounds": [0.56, 0.62, 0.78, 0.68],
            "source_confidence": 0.91,
            "public_basis": "PRIVATE_BUY_LIMIT_BASIS",
        },
        {
            "zone_id": "order-zone-private-sell-limit",
            "intent": "ENTRY_LIMIT",
            "order_kind": "SELL_LIMIT",
            "side": "SELL",
            "bounds": [0.54, 0.24, 0.76, 0.30],
            "source_confidence": 0.89,
        },
        {
            "zone_id": "order-zone-private-buy-stop",
            "intent": "ENTRY_STOP",
            "order_kind": "BUY_STOP",
            "side": "BUY",
            "bounds": [0.60, 0.18, 0.80, 0.22],
            "source_confidence": 0.87,
        },
        {
            "zone_id": "order-zone-private-sell-stop",
            "intent": "ENTRY_STOP",
            "order_kind": "SELL_STOP",
            "side": "SELL",
            "bounds": [0.58, 0.72, 0.79, 0.76],
            "source_confidence": 0.86,
        },
        {
            "zone_id": "order-zone-private-plan-failure",
            "intent": "PROTECTIVE_STOP",
            "order_kind": "SELL_STOP",
            "side": "SELL",
            "bounds": [0.56, 0.68, 0.78, 0.70],
            "source_confidence": 0.91,
        },
    ]
    return {
        "schema_version": "PG_ORDER_POSITIONING_CANDIDATES_V3",
        "status": "READY",
        "frame_id": frame_id,
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "candidate_zones": zones,
        "blockers": ["PRIVATE_READY_TELEMETRY"],
    }


def _ready_order_reference_map(
    *,
    frame_id: int = 14,
) -> dict[str, object]:
    rows = [
        {
            "reference_id": "private-reference-buy-limit",
            "order_kind": "BUY_LIMIT",
            "intent": "ENTRY_LIMIT",
            "side": "BUY",
            "bounds": [0.50, 0.64, 0.74, 0.70],
            "boundary_y_norm": 0.64,
            "location_role": "LOWER_ENTRY",
            "source_reference_id": "private-demand-source",
            "observational_only": True,
            "execution_authority": "NONE",
        },
        {
            "reference_id": "private-reference-sell-limit",
            "order_kind": "SELL_LIMIT",
            "intent": "ENTRY_LIMIT",
            "side": "SELL",
            "bounds": [0.52, 0.22, 0.76, 0.28],
            "boundary_y_norm": 0.28,
            "location_role": "UPPER_ENTRY",
            "source_reference_id": "private-supply-source",
            "observational_only": True,
            "execution_authority": "NONE",
        },
        {
            "reference_id": "private-reference-buy-stop",
            "order_kind": "BUY_STOP",
            "intent": "ENTRY_STOP",
            "side": "BUY",
            "bounds": [0.61, 0.16, 0.79, 0.20],
            "boundary_y_norm": 0.20,
            "location_role": "UPPER_CONFIRMATION",
            "source_reference_id": "private-resistance-source",
            "observational_only": True,
            "execution_authority": "NONE",
        },
        {
            "reference_id": "private-reference-sell-stop",
            "order_kind": "SELL_STOP",
            "intent": "ENTRY_STOP",
            "side": "SELL",
            "bounds": [0.59, 0.74, 0.80, 0.78],
            "boundary_y_norm": 0.74,
            "location_role": "LOWER_CONFIRMATION",
            "source_reference_id": "private-support-source",
            "observational_only": True,
            "execution_authority": "NONE",
        },
    ]
    return {
        "schema_version": "PG_ORDER_REFERENCE_MAP_V1",
        "status": "READY",
        "frame_id": frame_id,
        "sequence_id": "private-current-sequence",
        "chart_transform_id": "private-current-transform",
        "broker_source_lock_id": "private-current-source-lock",
        "market": "EUR/USD",
        "timeframe": "M5",
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "current_price_y_norm": 0.48,
        "rows": rows,
    }


def _seal_positioning_plan(plan: dict[str, object]) -> None:
    zones = cast(list[dict[str, object]], plan["zones"])
    static = [
        {
            key: value
            for key, value in zone.items()
            if key not in {"status", "status_reason", "last_updated_step"}
        }
        for zone in zones
    ]
    geometry_snapshot = json.dumps(
        static,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    anchors = cast(list[dict[str, object]], plan["reprojection_anchors"])
    anchor_snapshot = json.dumps(
        anchors,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    plan["geometry_snapshot"] = geometry_snapshot
    plan["geometry_fingerprint"] = hashlib.sha256(
        geometry_snapshot.encode("utf-8")
    ).hexdigest()
    plan["reprojection_anchor_snapshot"] = anchor_snapshot
    plan["reprojection_anchor_fingerprint"] = hashlib.sha256(
        anchor_snapshot.encode("utf-8")
    ).hexdigest()


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


def test_idle_workspace_publishes_exact_validated_order_area_previews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload(side="BUY")
    payload["tracking_episode"] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "state": "IDLE",
        "revision": 0,
        "event_horizon": 12,
        "event_cursor": 0,
    }
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "adaptive-trigger",
                "type": "TRIGGER_ZONE",
                "side": "BUY",
                "layer": "trigger_zones",
                "bounds": [0.61, 0.46, 0.66, 0.50],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "adaptive-target",
                "type": "TARGET_ZONE_BOX",
                "side": "BUY",
                "layer": "target_zones",
                "bounds": [0.62, 0.20, 0.72, 0.27],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
        ]
    }

    def preview_candidate_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return _ready_positioning_preview_candidate()

    def reference_map_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        reference_map = _ready_order_reference_map()
        rows = cast(list[dict[str, object]], reference_map["rows"])
        rows.extend(
            [
                {
                    **rows[0],
                    "reference_id": "near-preview-same-kind",
                    "bounds": [0.5605, 0.6205, 0.7795, 0.6805],
                },
                {
                    **rows[1],
                    "reference_id": "exact-preview-shared-bounds",
                    "bounds": [0.56, 0.62, 0.78, 0.68],
                },
            ]
        )
        return reference_map

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_positioning_candidate_v3",
        preview_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_reference_map_v3",
        reference_map_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    positioning_rows = [
        row
        for row in workspace["overlays"]
        if row["family"] == "order_positioning"
    ]
    preview_rows = [
        row for row in positioning_rows if row.get("positioning_mode") == "PREVIEW"
    ]
    reference_rows = [
        row
        for row in positioning_rows
        if row.get("positioning_mode") == "REFERENCE"
    ]

    assert {row["kind"]: row["bounds"] for row in preview_rows} == {
        "lower_price_buy_area": [0.56, 0.62, 0.78, 0.68],
        "higher_price_sell_area": [0.54, 0.24, 0.76, 0.30],
        "upside_break_area": [0.60, 0.18, 0.80, 0.22],
        "downside_break_area": [0.58, 0.72, 0.79, 0.76],
        "plan_failure_area": [0.56, 0.68, 0.78, 0.70],
    }
    assert {row["kind"]: row["bounds"] for row in reference_rows} == {
        "lower_price_buy_area": [0.50, 0.64, 0.74, 0.70],
        "higher_price_sell_area": [0.52, 0.22, 0.76, 0.28],
        "upside_break_area": [0.61, 0.16, 0.79, 0.20],
        "downside_break_area": [0.59, 0.74, 0.80, 0.78],
    }
    # Same-kind references survive when their chart location is materially
    # different. Near-identical and exact shared bounds are collapsed.
    assert len(positioning_rows) == 9
    assert len(reference_rows) == 4
    assert all(row.get("positioning_status") == "WAITING" for row in reference_rows)
    assert all(row.get("immutable_geometry") is False for row in positioning_rows)
    assert all(row.get("evidence_only") is True for row in positioning_rows)
    assert all(row["coordinate_units"] == "normalized" for row in positioning_rows)
    assert {row["id"] for row in positioning_rows}.isdisjoint(
        {
            "order-zone-private-buy-limit",
            "order-zone-private-sell-limit",
            "order-zone-private-buy-stop",
            "order-zone-private-sell-stop",
            "order-zone-private-plan-failure",
        }
    )
    # Preview evidence must not hide the current adaptive plan; only a frozen
    # active episode owns that precedence.
    assert any(row["id"] == "adaptive-trigger" for row in workspace["overlays"])
    assert any(row["id"] == "adaptive-target" for row in workspace["overlays"])
    episode = _mutable_mapping(
        _mutable_mapping(workspace["tracking"])["episode"]
    )
    order_areas = _mutable_mapping(episode["order_areas"])
    assert order_areas["status"] == "PREVIEW"
    assert order_areas["count"] == 9
    assert order_areas["kind_counts"] == {
        "lower_price_buy_area": 2,
        "higher_price_sell_area": 2,
        "upside_break_area": 2,
        "downside_break_area": 2,
        "plan_failure_area": 1,
    }
    assert "4 distinct chart location references" in str(order_areas["message"])
    assert "entry permission remains separate" in str(order_areas["message"])
    serialized = json.dumps(workspace)
    for private_token in (
        "PG_ORDER_POSITIONING",
        "ENTRY_LIMIT",
        "ENTRY_STOP",
        "PROTECTIVE_STOP",
        "BUY_LIMIT",
        "SELL_LIMIT",
        "PRIVATE_BUY_LIMIT_BASIS",
        "PRIVATE_READY_TELEMETRY",
        "order-zone-private",
    ):
        assert private_token not in serialized


def test_idle_workspace_hides_blocked_order_candidates_without_private_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload()
    payload["tracking_episode"] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "state": "IDLE",
        "revision": 0,
    }

    def blocked_candidate_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": "PG_ORDER_POSITIONING_CANDIDATES_V3",
            "status": "BLOCKED",
            "frame_id": 14,
            "coordinate_mode": "CHART_NORMALIZED",
            "chart_bounds": [0.0, 0.0, 1.0, 1.0],
            "candidate_zones": _ready_positioning_preview_candidate()[
                "candidate_zones"
            ],
            "blockers": ["PRIVATE_SOURCE_LOCK_FAILURE"],
        }

    def unavailable_reference_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": "PG_ORDER_REFERENCE_MAP_V1",
            "status": "UNAVAILABLE",
        }

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_reference_map_v3",
        unavailable_reference_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert not any(
        row["family"] == "order_positioning" for row in workspace["overlays"]
    )
    episode = _mutable_mapping(
        _mutable_mapping(workspace["tracking"])["episode"]
    )
    assert episode["order_areas"] == {
        "status": "UNAVAILABLE",
        "count": 0,
        "message": "No chart-verified order area is available on this current frame.",
        "kind_counts": {
            "lower_price_buy_area": 0,
            "higher_price_sell_area": 0,
            "upside_break_area": 0,
            "downside_break_area": 0,
            "plan_failure_area": 0,
        },
    }
    serialized = json.dumps(workspace)
    assert "PRIVATE_SOURCE_LOCK_FAILURE" not in serialized
    assert "PG_ORDER_POSITIONING" not in serialized


def test_idle_blocked_candidate_publishes_observational_order_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload()
    payload["tracking_episode"] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "state": "IDLE",
        "revision": 0,
    }
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "adaptive-target-remains-visible",
                "type": "TARGET_ZONE_BOX",
                "side": "BUY",
                "layer": "target_zones",
                "bounds": [0.62, 0.18, 0.75, 0.24],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            }
        ]
    }

    def blocked_candidate_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": "PG_ORDER_POSITIONING_CANDIDATES_V3",
            "status": "BLOCKED",
            "frame_id": 14,
            "blockers": ["PRIVATE_EXECUTION_BLOCKER"],
        }

    def reference_map_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return _ready_order_reference_map()

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_reference_map_v3",
        reference_map_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    references = [
        row
        for row in workspace["overlays"]
        if row["family"] == "order_positioning"
    ]

    assert {row["kind"]: row["bounds"] for row in references} == {
        "lower_price_buy_area": [0.50, 0.64, 0.74, 0.70],
        "higher_price_sell_area": [0.52, 0.22, 0.76, 0.28],
        "upside_break_area": [0.61, 0.16, 0.79, 0.20],
        "downside_break_area": [0.59, 0.74, 0.80, 0.78],
    }
    assert all(row.get("positioning_mode") == "REFERENCE" for row in references)
    assert all(row.get("positioning_status") == "WAITING" for row in references)
    assert all(row.get("immutable_geometry") is False for row in references)
    assert all(row.get("evidence_only") is True for row in references)
    assert any(
        row["id"] == "adaptive-target-remains-visible"
        for row in workspace["overlays"]
    )
    order_areas = _mutable_mapping(
        _mutable_mapping(_mutable_mapping(workspace["tracking"])["episode"])[
            "order_areas"
        ]
    )
    assert order_areas["status"] == "REFERENCE"
    assert order_areas["count"] == 4
    assert order_areas["kind_counts"] == {
        "lower_price_buy_area": 1,
        "higher_price_sell_area": 1,
        "upside_break_area": 1,
        "downside_break_area": 1,
        "plan_failure_area": 0,
    }
    assert "Entry permission remains separate" in str(order_areas["message"])
    serialized = json.dumps(workspace)
    for private_token in (
        "PG_ORDER_REFERENCE_MAP",
        "ENTRY_LIMIT",
        "ENTRY_STOP",
        "PROTECTIVE_INVALIDATION",
        "BUY_LIMIT",
        "SELL_LIMIT",
        "BUY_STOP",
        "SELL_STOP",
        "execution_authority",
        "observational_only",
        "source_reference_id",
        "location_role",
        "protected_side",
        "protected_reference_id",
        "private-reference",
        "private-current",
        "PRIVATE_EXECUTION_BLOCKER",
    ):
        assert private_token not in serialized
    assert "Original plan boundary" not in serialized


def test_final_public_overlay_boundary_preserves_only_safe_order_positioning_semantics() -> None:
    safe_rows = mobile_app._safe_operator_overlay_rows(  # pyright: ignore[reportPrivateUsage]
        [
            {
                "id": "current-higher-price-reference",
                "type": "entry",
                "kind": "higher_price_sell_area",
                "kind_label": "Higher-price sell area",
                "side": "SELL",
                "group": "plan",
                "family": "order_positioning",
                "layer": "order_positioning",
                "label": "Higher-price sell area",
                "label_hidden": True,
                "bounds": [0.52, 0.22, 0.76, 0.28],
                "points": [],
                "line_points": [],
                "confidence": 0.82,
                "lifecycle": "current",
                "frame_id": 14,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
                "positioning_mode": "reference",
                "positioning_status": "waiting",
                "positioning_basis": "Possible higher-price\nreaction area",
                "immutable_geometry": False,
                "evidence_only": True,
                "execution_authority": "NONE",
                "source_lineage": "private-lineage",
            }
        ]
    )

    assert len(safe_rows) == 1
    assert safe_rows[0]["positioning_mode"] == "REFERENCE"
    assert safe_rows[0]["positioning_status"] == "WAITING"
    assert safe_rows[0]["positioning_basis"] == "Possible higher-price reaction area"
    assert safe_rows[0]["immutable_geometry"] is False
    assert safe_rows[0]["evidence_only"] is True
    assert "execution_authority" not in safe_rows[0]
    assert "source_lineage" not in safe_rows[0]

    unsafe_rows = mobile_app._safe_operator_overlay_rows(  # pyright: ignore[reportPrivateUsage]
        [
            {
                **safe_rows[0],
                "id": "ambiguous-order-area",
                "positioning_mode": "",
            }
        ]
    )
    assert unsafe_rows == []


def test_order_reference_with_execution_authority_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload()
    payload["tracking_episode"] = {"state": "IDLE", "revision": 0}

    def blocked_candidate_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {"status": "BLOCKED"}

    def unauthorized_reference_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        reference_map = _ready_order_reference_map()
        rows = cast(list[dict[str, object]], reference_map["rows"])
        reference_map["rows"] = [{**rows[0], "execution_authority": "EXECUTE"}]
        return reference_map

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_reference_map_v3",
        unauthorized_reference_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert not any(
        row["family"] == "order_positioning" for row in workspace["overlays"]
    )
    assert "EXECUTE" not in json.dumps(workspace)


def test_observational_plan_failure_reference_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload()
    payload["tracking_episode"] = {"state": "IDLE", "revision": 0}

    def blocked_candidate_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {"status": "BLOCKED"}

    def legacy_reference_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        reference_map = _ready_order_reference_map()
        rows = cast(list[dict[str, object]], reference_map["rows"])
        reference_map["rows"] = [
            {
                **rows[0],
                "reference_id": "legacy-plan-failure-reference",
                "intent": "PROTECTIVE_INVALIDATION",
                "order_kind": "SELL_STOP",
                "location_role": "PLAN_INVALIDATION",
            }
        ]
        return reference_map

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_reference_map_v3",
        legacy_reference_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    episode = _mutable_mapping(
        _mutable_mapping(workspace["tracking"])["episode"]
    )

    assert not any(
        row["family"] == "order_positioning" for row in workspace["overlays"]
    )
    assert _mutable_mapping(episode["order_areas"])["status"] == "UNAVAILABLE"
    assert "legacy-plan-failure-reference" not in json.dumps(workspace)


def test_active_episode_without_frozen_areas_shows_current_references_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload()
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "raw-active-preview",
                "type": "SELL_LIMIT_ZONE",
                "side": "SELL",
                "layer": "order_positioning",
                "bounds": [0.20, 0.20, 0.40, 0.26],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "positioning_mode": "PREVIEW",
                "immutable_geometry": False,
                "evidence_only": True,
            }
        ]
    }
    episode_before = json.dumps(payload["tracking_episode"], sort_keys=True)

    def reference_map_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return _ready_order_reference_map()

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_reference_map_v3",
        reference_map_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    references = [
        row
        for row in workspace["overlays"]
        if row["family"] == "order_positioning"
    ]
    order_areas = _mutable_mapping(
        _mutable_mapping(_mutable_mapping(workspace["tracking"])["episode"])[
            "order_areas"
        ]
    )

    assert len(references) == 4
    assert all(row.get("positioning_mode") == "REFERENCE" for row in references)
    assert all(row.get("positioning_status") == "WAITING" for row in references)
    assert not any(row["kind"] == "plan_failure_area" for row in references)
    assert not any(row["id"] == "raw-active-preview" for row in workspace["overlays"])
    assert order_areas["status"] == "REFERENCE"
    assert order_areas["count"] == 4
    assert _mutable_mapping(order_areas["kind_counts"])["plan_failure_area"] == 0
    assert "original tracking plan remains unchanged" in str(
        order_areas["message"]
    ).lower()
    assert "entry permission remains separate" in str(
        order_areas["message"]
    ).lower()
    assert json.dumps(payload["tracking_episode"], sort_keys=True) == episode_before


def test_episode_order_areas_are_frozen_public_overlays_and_replace_moving_plan_boxes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload(side="BUY")
    episode = _mutable_mapping(payload["tracking_episode"])
    episode["anchor"] = {"frame_id": 9, "closed_candle_key": "closed-9"}
    positioning_plan: dict[str, object] = {
        "schema_version": "PG_ORDER_POSITIONING_PLAN_V3",
        "frozen": True,
        "sequence_id": "episode-positioning-sequence",
        "chart_transform_id": "episode-positioning-transform",
        "broker_source_lock_id": "episode-positioning-source",
        "market": "EUR/USD",
        "timeframe": "M5",
        "reprojection_anchors": [
            {
                "anchor_id": row["track_id"],
                "x_norm": row["x_norm"],
                "y_norm": row["close_y_norm"],
            }
            for row in _positioning_anchor_rows()
        ],
        "zones": [
            {
                "zone_id": "episode-test-active:buy-limit:zone-1",
                "overlay_type": "BUY_LIMIT_ZONE",
                "side": "BUY",
                "normalized_bounds": [0.56, 0.52, 0.78, 0.58],
                "source_bounds": [0.56, 0.52, 0.78, 0.58],
                "source_type": "DEMAND_ZONE",
                "source_track_id": "demand-source",
                "status": "APPROACHING",
                "confidence": 0.84,
                "public_basis": "Lower-price reaction area",
                "origin_frame_id": 9,
            },
            {
                "zone_id": "episode-test-active:buy-stop:zone-2",
                "overlay_type": "BUY_STOP_ENTRY_ZONE",
                "side": "BUY",
                "normalized_bounds": [0.64, 0.36, 0.82, 0.38],
                "source_bounds": [0.64, 0.38, 0.82, 0.44],
                "source_type": "SUPPLY_ZONE",
                "source_track_id": "supply-source",
                "status": "WAITING",
                "confidence": 0.79,
                "public_basis": "Completed-candle confirmation",
                "origin_frame_id": 9,
            },
            {
                "zone_id": "episode-test-active:protective-stop:zone-3",
                "overlay_type": "PROTECTIVE_STOP_ZONE",
                "side": "SELL",
                "normalized_bounds": [0.56, 0.58, 0.78, 0.60],
                "source_bounds": [0.56, 0.52, 0.78, 0.58],
                "source_type": "DEMAND_ZONE",
                "source_track_id": "demand-source",
                "status": "WAITING",
                "confidence": 0.84,
                "public_basis": "Original idea boundary",
                "origin_frame_id": 9,
            },
        ],
    }
    _seal_positioning_plan(positioning_plan)
    episode["positioning_plan"] = positioning_plan
    tracking = _mutable_mapping(payload["tracking_summary"])
    tracking["tracked_candles"] = _positioning_anchor_rows()
    payload["overlays"] = {
        "objects": [
            {
                "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
                "overlay_id": "demand-frame-14",
                "object_id": "demand-object",
                "track_id": "demand-source",
                "type": "DEMAND_ZONE",
                "side": "BUY",
                "layer": "supply_demand",
                "bounds": [0.56, 0.52, 0.78, 0.58],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "sequence_id": "current-positioning-sequence-14",
                "chart_transform_id": "current-positioning-transform-14",
                "broker_source_lock_id": "episode-positioning-source",
            },
            {
                "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
                "overlay_id": "supply-frame-14",
                "object_id": "supply-object",
                "track_id": "supply-source",
                "type": "SUPPLY_ZONE",
                "side": "SELL",
                "layer": "supply_demand",
                "bounds": [0.64, 0.38, 0.82, 0.44],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "sequence_id": "current-positioning-sequence-14",
                "chart_transform_id": "current-positioning-transform-14",
                "broker_source_lock_id": "episode-positioning-source",
            },
            {
                "overlay_id": "moving-target-frame-14",
                "type": "TARGET_ZONE_BOX",
                "side": "BUY",
                "layer": "target_zones",
                "bounds": [0.55, 0.20, 0.78, 0.27],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
            },
            {
                "overlay_id": "adaptive-preview-must-not-win",
                "type": "SELL_LIMIT_ZONE",
                "side": "SELL",
                "layer": "order_positioning",
                "bounds": [0.20, 0.20, 0.40, 0.26],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "positioning_status": "WAITING",
                "positioning_basis": "PRIVATE_MOVING_PREVIEW",
                "positioning_mode": "PREVIEW",
                "immutable_geometry": False,
                "evidence_only": True,
            },
        ]
    }

    def reference_map_stub(
        source: Mapping[str, object],
    ) -> dict[str, object]:
        frame_id = cast(int, source.get("display_frame_id", 14))
        reference_map = _ready_order_reference_map(frame_id=frame_id)
        if frame_id == 14:
            rows = cast(list[dict[str, object]], reference_map["rows"])
            rows.extend(
                [
                    {
                        **rows[0],
                        "reference_id": "near-frozen-same-kind",
                        "bounds": [0.5605, 0.5205, 0.7795, 0.5805],
                    },
                    {
                        **rows[1],
                        "reference_id": "exact-frozen-shared-bounds",
                        "bounds": [0.56, 0.52, 0.78, 0.58],
                    },
                ]
            )
        return reference_map

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_tracking_order_reference_map_v3",
        reference_map_stub,
    )

    first = _build_workspace(payload, now_epoch=100.0)
    first_areas = [
        row for row in first["overlays"] if row["family"] == "order_positioning"
    ]

    assert {row["kind"] for row in first_areas} == {
        "lower_price_buy_area",
        "higher_price_sell_area",
        "upside_break_area",
        "downside_break_area",
        "plan_failure_area",
    }
    assert all(row["frame_id"] == 14 for row in first_areas)
    assert all(row["coordinate_units"] == "normalized" for row in first_areas)
    assert sum(row.get("positioning_mode") == "FROZEN" for row in first_areas) == 3
    assert sum(row.get("positioning_mode") == "REFERENCE" for row in first_areas) == 4
    assert all(
        row.get("positioning_status") == "WAITING"
        for row in first_areas
        if row.get("positioning_mode") == "REFERENCE"
    )
    assert all(
        cast(dict[str, object], row)["immutable_geometry"]
        is (row.get("positioning_mode") == "FROZEN")
        for row in first_areas
    )
    assert not any(
        row["id"] == "adaptive-preview-must-not-win"
        for row in first["overlays"]
    )
    assert not any(row["kind"] == "target_area" for row in first["overlays"])
    public_episode = _mutable_mapping(
        cast(dict[str, object], first["tracking"])["episode"]
    )
    public_order_areas = _mutable_mapping(public_episode["order_areas"])
    assert public_order_areas["status"] == "TRACKING"
    assert public_order_areas["count"] == 7
    assert public_order_areas["kind_counts"] == {
        "lower_price_buy_area": 2,
        "higher_price_sell_area": 1,
        "upside_break_area": 2,
        "downside_break_area": 1,
        "plan_failure_area": 1,
    }
    assert "saved fixed order areas" in str(public_order_areas["message"])
    assert "current chart location references" in str(public_order_areas["message"])
    first_geometry = {row["id"]: row["bounds"] for row in first_areas}

    payload["display_frame_id"] = 15
    command = _mutable_mapping(payload["decision_command_center"])
    _mutable_mapping(command["current_movement"])["frame_id"] = 15
    _mutable_mapping(command["pressure_event"])["frame_id"] = 15
    tracking["tracked_candles"] = _positioning_anchor_rows(
        scale_x=1.02,
        offset_x=0.01,
        scale_y=0.96,
        offset_y=0.02,
    )
    payload["overlays"] = {
        "objects": [
            {
                "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
                "overlay_id": "demand-frame-15",
                "object_id": "demand-object",
                "track_id": "demand-source",
                "type": "DEMAND_ZONE",
                "side": "BUY",
                "layer": "supply_demand",
                "bounds": [0.57, 0.54, 0.79, 0.60],
                "frame_id": 15,
                "coordinate_mode": "CHART_NORMALIZED",
                "sequence_id": "current-positioning-sequence-15",
                "chart_transform_id": "current-positioning-transform-15",
                "broker_source_lock_id": "episode-positioning-source",
            },
            {
                "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
                "overlay_id": "supply-frame-15",
                "object_id": "supply-object",
                "track_id": "supply-source",
                "type": "SUPPLY_ZONE",
                "side": "SELL",
                "layer": "supply_demand",
                "bounds": [0.65, 0.40, 0.83, 0.46],
                "frame_id": 15,
                "coordinate_mode": "CHART_NORMALIZED",
                "sequence_id": "current-positioning-sequence-15",
                "chart_transform_id": "current-positioning-transform-15",
                "broker_source_lock_id": "episode-positioning-source",
            },
        ]
    }
    second = _build_workspace(payload, now_epoch=101.0)
    second_areas = [
        row for row in second["overlays"] if row["family"] == "order_positioning"
    ]

    assert {row["id"]: row["bounds"] for row in second_areas} != first_geometry
    assert all(row["frame_id"] == 15 for row in second_areas)
    serialized = json.dumps(second_areas)
    assert "BUY_LIMIT_ZONE" not in serialized
    assert "closed-9" not in serialized

    payload["display_frame_id"] = 16
    _mutable_mapping(command["current_movement"])["frame_id"] = 16
    _mutable_mapping(command["pressure_event"])["frame_id"] = 16
    bad_anchors = _positioning_anchor_rows(
        scale_x=1.02,
        offset_x=0.01,
        scale_y=0.96,
        offset_y=0.02,
    )
    bad_anchors[-1]["close_y_norm"] = 0.75
    tracking["tracked_candles"] = bad_anchors
    current_objects = cast(
        list[dict[str, object]],
        _mutable_mapping(payload["overlays"])["objects"],
    )
    for row in current_objects:
        row["frame_id"] = 16
        row["sequence_id"] = "current-positioning-sequence-16"
        row["chart_transform_id"] = "current-positioning-transform-16"
    unproven = _build_workspace(payload, now_epoch=102.0)
    unproven_areas = [
        row
        for row in unproven["overlays"]
        if row["family"] == "order_positioning"
    ]
    assert len(unproven_areas) == 4
    assert all(
        row.get("positioning_mode") == "REFERENCE" for row in unproven_areas
    )
    unproven_episode = _mutable_mapping(
        _mutable_mapping(unproven["tracking"])["episode"]
    )
    assert _mutable_mapping(unproven_episode["order_areas"])["status"] == "REFERENCE"


def test_operator_forecast_keeps_user_truth_and_strips_runtime_telemetry() -> None:
    payload = _fresh_payload()
    private_forecast_fields: dict[str, object] = {
        "forecast_engine": "SCENE_FORECASTER_V3",
        "forecast_provider": "SCENE_STATISTICAL_FALLBACK_V3",
        "forecast_provider_status": "FOUNDATION_DISABLED_FALLBACK",
        "forecast_id": "private-forecast-id",
        "forecast_revision": 23,
        "belief_revision": 11,
        "closed_candle_key": "private-candle-key",
        "closed_candle_sequence": 91,
        "forecast_computed_frame_id": 12,
        "source_forecast_frame_id": 12,
        "geometry_projected_frame_id": 14,
        "geometry_frame_match_verified": True,
        "geometry_reprojected_from_cache": True,
        "detector_coverage_rebase_applied": True,
        "cache_replaced_for_detector_coverage_rebase": True,
        "geometry_projection_provenance": {
            "method": "SHARED_ANCHOR_AFFINE_FIT",
            "source_geometry_frame_id": 12,
            "projected_frame_id": 14,
        },
        "scene_feature_audit": {
            "consumed_field_count": 41,
            "source_presence": {"decision_kernel": True},
        },
    }
    public_belief_fields: dict[str, object] = {
        "belief_state": "STABLE",
        "committed_side": "BUY",
        "candidate_side": "HOLD",
        "confirmation_events": 2,
        "required_events": 2,
        "change_probability": 0.08,
    }
    payload["scene_forecast_contribution"] = {
        "frame_id": 14,
        "direction": "BUY",
        "confidence": 0.71,
        "fresh": True,
        **public_belief_fields,
        **private_forecast_fields,
    }
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "scene-current",
                "type": "SCENE_FORECAST_STUDY",
                "layer": "prediction_path",
                "role": "scene_forecast_candle_event_path_no_edge",
                "frame_id": 14,
                "coordinate_space": "chart",
                "coordinate_units": "normalized",
                "line_points": [[0.42, 0.58], [0.66, 0.44]],
                "side": "BUY",
                "confidence": 0.71,
                **public_belief_fields,
                **private_forecast_fields,
            }
        ]
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    forecast = workspace["forecast"]
    forecast_mapping = cast(Mapping[str, object], forecast)
    assert forecast["direction"] == "BUY"
    assert forecast["confidence"] == 0.71
    for key, value in public_belief_fields.items():
        assert forecast_mapping[key] == value
    assert len(workspace["overlays"]) == 1
    overlay = workspace["overlays"][0]
    overlay_mapping = cast(Mapping[str, object], overlay)
    assert overlay["family"] == "scene_forecaster"
    assert overlay["line_points"] == [[0.42, 0.58], [0.66, 0.44]]
    for key, value in public_belief_fields.items():
        assert overlay_mapping[key] == value
    forbidden = set(private_forecast_fields)
    assert _all_keys(forecast).isdisjoint(forbidden)
    assert _all_keys(overlay).isdisjoint(forbidden)
    serialized = json.dumps(workspace)
    assert "FOUNDATION_DISABLED_FALLBACK" not in serialized
    assert "SHARED_ANCHOR_AFFINE_FIT" not in serialized
    assert "private-candle-key" not in serialized


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


def test_tracking_controls_invalidate_operator_projection_on_the_same_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "episode-control-cache"
    now_epoch = time.time()
    episode: dict[str, object] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "episode_id": "",
        "state": "IDLE",
        "revision": 0,
        "event_horizon": 12,
        "event_cursor": 0,
        "events": [],
    }

    def live_state() -> dict[str, object]:
        payload = _fresh_payload(now=now_epoch)
        payload["session_id"] = session_id
        payload["tracking_episode"] = json.loads(json.dumps(episode))
        payload["overlays"] = {"objects": []}
        payload["live_visual_state"] = {"overlays": {"objects": []}}
        return payload

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return live_state()

        def latest_model_council_state(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return {}

        def start_tracking_episode(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            episode.update(
                {
                    "episode_id": "episode-control-1",
                    "state": "ACTIVE",
                    "revision": 1,
                    "committed_plan": {"decision": {"action": "BUY"}},
                }
            )
            return json.loads(json.dumps(episode))

        def stop_tracking_episode(
            self,
            requested_session_id: str,
            *,
            reason: str,
        ) -> dict[str, object]:
            assert requested_session_id == session_id
            assert reason == "operator_stop"
            episode.update(
                {
                    "state": "STOPPED",
                    "revision": 2,
                    "terminal_reason": "MANUAL_STOP",
                }
            )
            return json.loads(json.dumps(episode))

        def get_tracking_episode_readiness(
            self,
            requested_session_id: str,
        ) -> dict[str, object]:
            assert requested_session_id == session_id
            return {"ready": True, "reasons": []}

    def build_state(
        tracker: object,
        requested_session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        return live_state()

    def source_revision(
        requested_session_id: str,
    ) -> tuple[str, int, float] | None:
        if requested_session_id != session_id:
            return None
        return (
            f"episode:{episode['episode_id']}:{episode['state']}:{episode['revision']}",
            14,
            now_epoch + 60.0,
        )

    projection_calls = 0
    original_builder = mobile_app.build_operator_workspace_v1

    def counted_builder(source: Mapping[str, object]) -> dict[str, object]:
        nonlocal projection_calls
        projection_calls += 1
        return original_builder(source)

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setenv("PHOENIXGUARD_LIVE_STATE_DIRECT_READ", "0")
    monkeypatch.setattr(mobile_app, "_LIVE_STATE_V3_CACHE_TTL_SEC", 0.0)
    monkeypatch.setattr(
        mobile_app,
        "_COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC",
        0.0,
    )
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        build_state,
    )
    monkeypatch.setattr(
        mobile_app,
        "_operator_projection_source_revision",
        source_revision,
    )
    monkeypatch.setattr(mobile_app, "build_operator_workspace_v1", counted_builder)

    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        idle = client.get(f"/v1/mobile/operator/state/v1/{session_id}?view=all")
        started = client.post(
            f"/v1/mobile/window-tracker/sessions/{session_id}/tracking-episodes/start"
        )
        active = client.get(f"/v1/mobile/operator/state/v1/{session_id}?view=all")
        stopped = client.post(
            f"/v1/mobile/window-tracker/sessions/{session_id}/tracking-episodes/stop",
            json={"reason": "operator_stop"},
        )
        retained = client.get(f"/v1/mobile/operator/state/v1/{session_id}?view=all")

    assert idle.json()["tracking"]["episode"]["state"] == "IDLE"
    assert started.status_code == 200
    assert active.json()["tracking"]["episode"]["state"] == "ACTIVE"
    assert active.json()["freshness"]["state"] != "STALE"
    assert stopped.status_code == 200
    assert retained.json()["tracking"]["episode"]["state"] == "STOPPED"
    assert retained.json()["freshness"]["state"] != "STALE"
    assert projection_calls == 3


def test_inflight_old_episode_projection_cannot_repopulate_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "episode-cache-race"
    now_epoch = time.time()
    episode: dict[str, object] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "episode_id": "episode-race-1",
        "state": "ACTIVE",
        "revision": 1,
        "event_horizon": 12,
        "event_cursor": 0,
        "committed_plan": {"decision": {"action": "BUY"}},
        "events": [],
    }

    def live_state() -> dict[str, object]:
        payload = _fresh_payload(now=now_epoch)
        payload["session_id"] = session_id
        payload["tracking_episode"] = json.loads(json.dumps(episode))
        payload["overlays"] = {"objects": []}
        payload["live_visual_state"] = {"overlays": {"objects": []}}
        return payload

    class _Tracker:
        def get_session_snapshot(self, _session_id: str) -> dict[str, object]:
            return live_state()

        def latest_model_council_state(self, _session_id: str) -> dict[str, object]:
            return {}

    def build_state(
        _tracker: object,
        _session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        return live_state()

    def source_revision(
        requested_session_id: str,
    ) -> tuple[str, int, float] | None:
        if requested_session_id != session_id:
            return None
        return (
            f"episode:{episode['state']}:{episode['revision']}",
            14,
            now_epoch + 60.0,
        )

    projection_calls = 0
    transition_during_first_build = True
    original_builder = mobile_app.build_operator_workspace_v1

    def racing_builder(source: Mapping[str, object]) -> dict[str, object]:
        nonlocal projection_calls, transition_during_first_build
        projection_calls += 1
        projected = original_builder(source)
        if transition_during_first_build:
            transition_during_first_build = False
            episode.update(
                {
                    "state": "STOPPED",
                    "revision": 2,
                    "terminal_reason": "MANUAL_STOP",
                }
            )
        return projected

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setenv("PHOENIXGUARD_LIVE_STATE_DIRECT_READ", "0")
    monkeypatch.setattr(mobile_app, "_LIVE_STATE_V3_CACHE_TTL_SEC", 0.0)
    monkeypatch.setattr(
        mobile_app,
        "_COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC",
        0.0,
    )
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        build_state,
    )
    monkeypatch.setattr(
        mobile_app,
        "_operator_projection_source_revision",
        source_revision,
    )
    monkeypatch.setattr(mobile_app, "build_operator_workspace_v1", racing_builder)

    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        old_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        current_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )

    assert old_response.json()["tracking"]["episode"]["state"] == "ACTIVE"
    assert current_response.json()["tracking"]["episode"]["state"] == "STOPPED"
    assert current_response.json()["freshness"]["state"] != "STALE"
    assert projection_calls == 2


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


def test_public_episode_and_legacy_session_routes_never_expose_frozen_internals() -> None:
    visual_blocks = cast(list[dict[str, object]], _complete_forecast_bundle()["forecast_candles"])
    raw_episode: dict[str, object] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "episode_id": "episode-public-route-1",
        "state": "ACTIVE",
        "revision": 3,
        "event_horizon": 12,
        "event_cursor": 0,
        "pair": "EUR/USD",
        "timeframe": "M5",
        "committed_plan": {
            "decision": {"action": "BUY", "summary": "PRIVATE_PLAN_TRACE"},
            "model_council": {"provider": "PRIVATE_COUNCIL_PROVIDER"},
        },
        "baseline_forecasts": {
            "scene": {
                "forecast_candles": visual_blocks,
                "provider": "PRIVATE_SCENE_PROVIDER",
                "source_path": r"C:\private\scene.json",
            },
            "lstm": {
                "forecast_path": [{"step": step, "raw_model_score": 0.9} for step in range(1, 13)],
                "model_version": "PRIVATE_MODEL_VERSION",
            },
        },
        "candidate_revision": {"model_vote": "PRIVATE_CANDIDATE_MODEL"},
        "events": [],
    }

    class _Tracker:
        def get_session_snapshot(self, _session_id: str) -> dict[str, object]:
            return {
                "session_id": "episode-route-test",
                "tracking_episode": json.loads(json.dumps(raw_episode)),
                "tracking_episode_history": [
                    {"provider": "PRIVATE_ARCHIVE_PROVIDER"}
                ],
            }

        def get_tracking_episode(self, _session_id: str) -> dict[str, object]:
            return json.loads(json.dumps(raw_episode))

        def get_tracking_episode_readiness(self, _session_id: str) -> dict[str, object]:
            return {
                "ready": False,
                "reasons": [
                    "Wait for a complete 12-event Scene or LSTM forecast baseline."
                ],
                "identity": {"closed_candle_key": "PRIVATE_EVENT_KEY"},
                "scene_horizon": 12,
                "lstm_horizon": 12,
                "current": json.loads(json.dumps(raw_episode)),
            }

        def start_tracking_episode(self, _session_id: str) -> dict[str, object]:
            return json.loads(json.dumps(raw_episode))

        def stop_tracking_episode(
            self,
            _session_id: str,
            *,
            reason: str,
        ) -> dict[str, object]:
            stopped = json.loads(json.dumps(raw_episode))
            stopped["state"] = "STOPPED"
            stopped["terminal_reason"] = reason
            return stopped

    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        responses = [
            client.get(
                "/v1/mobile/window-tracker/sessions/episode-route-test/tracking-episodes/current"
            ),
            client.get(
                "/v1/mobile/window-tracker/sessions/episode-route-test/tracking-episodes/readiness"
            ),
            client.post(
                "/v1/mobile/window-tracker/sessions/episode-route-test/tracking-episodes/start"
            ),
            client.post(
                "/v1/mobile/window-tracker/sessions/episode-route-test/tracking-episodes/stop",
                json={"reason": "operator_stop"},
            ),
        ]
        legacy = client.get(
            "/v1/mobile/window-tracker/sessions/episode-route-test"
        )

    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["schema_version"] == "PG_TRACKING_EPISODE_PUBLIC_V1"
    assert responses[1].json()["schema_version"] == (
        "PG_TRACKING_EPISODE_READINESS_PUBLIC_V1"
    )
    assert responses[-1].json()["terminal_reason"] == "STOPPED"
    assert legacy.status_code == 200
    assert "tracking_episode" not in legacy.json()
    assert "tracking_episode_history" not in legacy.json()
    serialized = json.dumps([response.json() for response in responses]).lower()
    for secret in (
        "baseline_forecasts",
        "committed_plan",
        "candidate_revision",
        "private_plan_trace",
        "private_scene_provider",
        "private_council_provider",
        "private_model_version",
        "private_candidate_model",
        "private_event_key",
        "private_",
        "provider",
        "source_path",
        "model_version",
        "raw_model",
        "raw_model_score",
        "scene_horizon",
        "lstm_horizon",
    ):
        assert secret not in serialized


def test_operator_route_preserves_twelve_frozen_blocks_through_bounded_merge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "episode-bounded-route"
    bundle = _complete_forecast_bundle(
        selected_side="SELL",
        anchor_x=0.55,
        anchor_y=0.38,
    )
    visual_blocks = cast(list[dict[str, object]], bundle["forecast_candles"])
    sequence_path = [
        {
            "step": block["step"],
            "expected_open_norm": 1.0 - float(cast(float, block["open_y_norm"])),
            "expected_high_norm": 1.0 - float(cast(float, block["high_y_norm"])),
            "expected_low_norm": 1.0 - float(cast(float, block["low_y_norm"])),
            "expected_close_norm": 1.0 - float(cast(float, block["close_y_norm"])),
            "movement_direction": "SELL",
            "candle_body_direction": "SELL",
            "raw_model_score": 0.99,
            "source_path": r"C:\private\future.json",
        }
        for block in visual_blocks
    ]
    episode: dict[str, object] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "session_id": session_id,
        "episode_id": "episode-bounded-1",
        "state": "ACTIVE",
        "revision": 5,
        "event_horizon": 12,
        "event_cursor": 0,
        "started_at": "2026-07-18T08:00:00Z",
        "updated_at": "2026-07-18T08:00:00Z",
        "pair": "EUR/USD",
        "timeframe": "M5",
        "committed_plan": {
            "decision": {"action": "SELL", "summary": "PRIVATE_PLAN_TRACE"},
        },
        "baseline_forecasts": {
            "scene": {
                "provider": "PRIVATE_SCENE_PROVIDER",
            },
            "lstm": {
                "forecast_path": sequence_path,
                "model_version": "PRIVATE_MODEL_VERSION",
            },
        },
        "events": [],
    }
    live_state: dict[str, object] = {
        "session_id": session_id,
        "state_version": 14,
        "decision_version": 14,
        "display_frame_id": 14,
        "chart_frame_id": 14,
        "overlay_frame_id": 14,
        "full_overlay_frame_id": 14,
        "model_vote_frame_id": 14,
        "frame_bundle_complete_v3": True,
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
            "detected_market": "EUR/USD",
            "detected_timeframe": "M5",
            "last_capture_epoch": 99.0,
        },
        "tracking_episode": episode,
        "tracking_episode_history": [
            {
                "schema_version": "PG_TRACKING_EPISODE_HISTORY_ENTRY_V1",
                "episode_id": "episode-bounded-previous",
                "state": "STOPPED",
                "revision": 3,
                "event_cursor": 1,
                "event_horizon": 12,
                "direction_agreement_count": 1,
                "direction_observation_count": 1,
                "ended_at": 98.5,
                "anchor_frame_id": 13,
                "events": [
                    {
                        "event_id": "episode-bounded-previous:E1",
                        "step": 1,
                        "observed_at": 98.0,
                        "predicted_side": "SELL",
                        "actual_side": "SELL",
                        "direction_agreement": True,
                        "frame_id": 13,
                        "raw_block": {"provider": "PRIVATE_HISTORY_PROVIDER"},
                    }
                ],
            }
        ],
        "tracking_episode_readiness": {"ready": True, "reasons": []},
        # The contributor is deliberately absent.  The frozen episode itself
        # must continue publishing the twelve block-only objects.
        "overlays": {"objects": []},
        "live_visual_state": {"overlays": {"objects": []}},
    }

    class _Tracker:
        def get_session_snapshot(self, requested_session_id: str) -> dict[str, object]:
            assert requested_session_id == session_id
            return cast(dict[str, object], json.loads(json.dumps(live_state)))

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
        return cast(dict[str, object], json.loads(json.dumps(live_state)))

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        _build_state,
    )
    with TestClient(mobile_app.create_app(window_tracker_service=_Tracker())) as client:
        operator_response = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        compact_response = client.get(
            f"/v1/mobile/live/state/v3/{session_id}?mode=INSPECTOR&compact=true"
        )

    assert operator_response.status_code == 200
    workspace = cast(_OperatorWorkspaceView, operator_response.json())
    public_episode = cast(
        Mapping[str, object],
        cast(Mapping[str, object], workspace["tracking"])["episode"],
    )
    future_blocks = cast(list[dict[str, object]], public_episode["future_blocks"])
    assert len(future_blocks) == 12
    assert [block["step"] for block in future_blocks] == list(range(1, 13))
    public_composite = next(
        row
        for row in workspace["overlays"]
        if row.get("family") == "lstm"
        and row.get("forecast_role") == "composite"
    )
    assert public_composite.get("baseline_locked") is True
    assert public_composite.get("forecast_candles") == future_blocks
    assert public_composite["line_points"] == []
    assert public_composite.get("geometry_kind") == "future_blocks"
    assert public_composite["kind"] == "future_blocks"
    assert public_composite["label"] == "Saved future blocks"
    archived_event = next(
        row
        for row in workspace["history"]
        if row.get("id") == "episode-bounded-previous-e1"
    )
    assert archived_event.get("agreement") is True
    assert archived_event.get("predicted_direction") == "SELL"

    assert compact_response.status_code == 200
    compact_serialized = json.dumps(compact_response.json()).lower()
    assert "tracking_episode" not in compact_serialized
    assert "baseline_forecasts" not in compact_serialized
    assert "private_scene_provider" not in compact_serialized
    assert "private_model_version" not in compact_serialized
    assert "private_plan_trace" not in compact_serialized
    assert "raw_model_score" not in compact_serialized
    assert "source_path" not in compact_serialized
    assert "private_history_provider" not in json.dumps(workspace).lower()


def test_projector_synthesizes_episode_owned_future_blocks_without_live_lstm() -> None:
    payload = _fresh_payload(side="SELL")
    bundle = _complete_forecast_bundle(
        selected_side="SELL",
        anchor_x=0.56,
        anchor_y=0.37,
    )
    episode = _mutable_mapping(payload["tracking_episode"])
    episode["baseline_forecasts"] = {
        "scene": {
            "forecast_candles": bundle["forecast_candles"],
            "provider": "PRIVATE_PROVIDER",
        }
    }
    payload["overlays"] = {"objects": []}
    payload["live_visual_state"] = {"overlays": {"objects": []}}

    workspace = _build_workspace(payload, now_epoch=100.0)
    rows = [
        row
        for row in workspace["overlays"]
        if row.get("family") == "lstm"
        and row.get("forecast_role") == "composite"
    ]

    assert len(rows) == 1
    row = rows[0]
    assert row.get("baseline_locked") is True
    assert row["frame_id"] == 14
    assert row["coordinate_space"] == "chart"
    assert row["coordinate_units"] == "normalized"
    assert len(cast(list[object], row.get("forecast_candles"))) == 12
    assert row["line_points"] == []
    assert row.get("geometry_kind") == "future_blocks"
    anchor = cast(Mapping[str, object], row.get("forecast_anchor"))
    first_block = cast(Sequence[Mapping[str, object]], row.get("forecast_candles", []))[0]
    assert float(cast(float, anchor["x_norm"])) < float(cast(float, first_block["x_norm"]))
    assert anchor["y_norm"] == first_block["open_y_norm"]
    serialized = json.dumps(row).lower()
    assert "private" not in serialized
    assert "provider" not in serialized
    assert "model" not in serialized
    assert "source" not in serialized


def test_projector_builds_twelve_safe_blocks_from_lstm_only_episode() -> None:
    payload = _fresh_payload(side="BUY")
    episode = _mutable_mapping(payload["tracking_episode"])
    episode["baseline_forecasts"] = {
        "scene": {},
        "lstm": {
            "forecast_path": [
                {
                    "step": step,
                    "expected_open_norm": 0.45 + step * 0.004,
                    "expected_high_norm": 0.47 + step * 0.004,
                    "expected_low_norm": 0.43 + step * 0.004,
                    "expected_close_norm": 0.46 + step * 0.004,
                    "movement_direction": "BUY",
                    "candle_body_direction": "BUY",
                    "raw_model_score": 0.99,
                }
                for step in range(1, 13)
            ],
            "model_version": "PRIVATE_LSTM_VERSION",
        },
    }
    payload["overlays"] = {"objects": []}
    payload["live_visual_state"] = {"overlays": {"objects": []}}

    workspace = _build_workspace(payload, now_epoch=100.0)
    public_episode = cast(
        Mapping[str, object],
        cast(Mapping[str, object], workspace["tracking"])["episode"],
    )
    blocks = cast(list[dict[str, object]], public_episode["future_blocks"])
    composite = next(
        row
        for row in workspace["overlays"]
        if row.get("family") == "lstm"
        and row.get("forecast_role") == "composite"
    )

    assert len(blocks) == 12
    assert [block["step"] for block in blocks] == list(range(1, 13))
    assert all(
        float(cast(float, right["x_norm"]))
        > float(cast(float, left["x_norm"]))
        for left, right in zip(blocks, blocks[1:])
    )
    assert composite.get("forecast_candles") == blocks
    assert composite.get("baseline_locked") is True
    serialized = json.dumps(workspace).lower()
    assert "private_lstm_version" not in serialized
    assert "raw_model_score" not in serialized


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


def test_entry_permission_fails_closed_without_an_active_tracking_episode() -> None:
    missing = _fresh_payload(side="BUY")
    missing.pop("tracking_episode")
    idle = _fresh_payload(side="BUY")
    idle["tracking_episode"] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "episode_id": "",
        "state": "IDLE",
        "revision": 0,
    }

    for payload in (missing, idle):
        permission = _build_workspace(payload, now_epoch=100.0)["permission"]
        assert permission["action"] == "WAIT"
        assert permission["allowed"] is False
        assert permission["window_open"] is False
        assert permission["expires_at"] is None
        assert "Start Tracking" in permission["message"]


def test_active_episode_never_authorizes_a_direction_that_differs_from_saved_plan() -> None:
    payload = _fresh_payload(side="SELL")
    episode = _mutable_mapping(payload["tracking_episode"])
    episode["committed_plan"] = {"decision": {"action": "BUY"}}

    workspace = _build_workspace(payload, now_epoch=100.0)
    permission = workspace["permission"]
    public_episode = cast(
        Mapping[str, object],
        cast(Mapping[str, object], workspace["tracking"])["episode"],
    )

    assert cast(Mapping[str, object], public_episode["baseline"])["direction"] == "BUY"
    assert permission["action"] == "WAIT"
    assert permission["allowed"] is False
    assert permission["window_open"] is False
    assert permission["expires_at"] is None
    assert permission["valid_for_seconds"] is None
    assert permission["window_label"] == "Closed"
    assert permission["entry_location"] == "LOWER_PRICE"
    assert permission["entry_guidance"] == (
        "Aim for a lower price inside the verified demand or retest area; do not "
        "chase highs."
    )
    assert permission["message"] == (
        "Wait. The current proposal differs from the saved tracking plan."
    )


def test_active_episode_without_directional_saved_plan_remains_wait() -> None:
    payload = _fresh_payload(side="BUY")
    episode = _mutable_mapping(payload["tracking_episode"])
    episode["committed_plan"] = {"decision": {"action": "HOLD"}}

    permission = _build_workspace(payload, now_epoch=100.0)["permission"]

    assert permission["action"] == "WAIT"
    assert permission["allowed"] is False
    assert permission["window_open"] is False
    assert permission["expires_at"] is None
    assert permission["valid_for_seconds"] is None
    assert permission["window_label"] == "Closed"
    assert permission["message"] == (
        "Wait. The saved tracking plan does not permit a directional entry."
    )


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
    assert "last valid outlook" in workspace["forecast"]["summary"].lower()
    assert "observation only" in workspace["forecast"]["summary"].lower()
    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["permission"]["allowed"] is False
    # The last textual read remains available, but obsolete LSTM path/band
    # drawings are not public now that the operator contract is block-only.
    assert workspace["overlays"] == []


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
def test_line_only_lstm_path_cannot_restore_forecast_when_raw_projection_races(
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

    assert workspace["forecast"] == {
        "direction": "NEUTRAL",
        "state": "UNKNOWN",
        "confidence": None,
        "horizon_seconds": None,
        "summary": "No reliable next direction is confirmed.",
    }
    assert workspace["overlays"] == []


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
    assert workspace["forecast"]["confidence"] is None
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

    assert workspace["forecast"]["direction"] == "BUY"
    assert workspace["forecast"]["state"] == "CURRENT"
    assert workspace["forecast"]["confidence"] == 0.91
    assert workspace["forecast"]["horizon_seconds"] == 60
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
                "overlay_id": "scene-study-current",
                "type": "SCENE_FORECAST_STUDY",
                "role": "scene_forecast_study",
                "layer": "prediction_path",
                "bounds": [0.58, 0.42, 0.82, 0.58],
                "frame_id": 14,
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
    assert by_id["two-candle-current"]["label"] == "Near-term candle read"
    assert by_id["lstm-study-current"]["family"] == "lstm"
    assert by_id["lstm-study-current"]["label"] == "12-step future blocks"
    assert by_id["lstm-path-current"]["family"] == "lstm"
    assert by_id["lstm-path-current"]["layer"] == "prediction_path"
    assert by_id["lstm-path-current"]["label"] == "Future blocks · no reliable edge"
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
    assert all("line_points" not in scenario for scenario in scenarios)
    assert by_id["lstm-path-current"]["line_points"] == []
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
    assert by_id["scene-study-current"]["family"] == "scene_forecaster"
    assert by_id["scene-study-current"]["label"] == "Visual outlook"
    assert by_id["council-current"]["family"] == "council"
    assert by_id["council-current"]["label"] == "Combined analysis"
    context_overlay = next(row for row in overlays if row["kind"] == "reaction_zone")
    assert context_overlay["family"] == "market_context"
    assert context_overlay["layer"] == "market_context"
    assert context_overlay["label"] == "Reaction zone"
    assert "smc" not in str(context_overlay["id"]).lower()
    assert "order" not in str(context_overlay["id"]).lower()
    serialized_context = json.dumps(context_overlay).lower()
    assert "smart_money" not in serialized_context
    assert "order_block" not in serialized_context
    assert "liquidity" not in serialized_context


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
    assert lstm["line_points"] == []
    assert lstm.get("geometry_kind") == "future_blocks"
    assert lstm.get("forecast_band_points") == []
    assert len(cast(list[object], lstm.get("forecast_candles"))) == 12
    scenarios = cast(list[dict[str, object]], lstm.get("forecast_scenarios"))
    assert len(scenarios) == 3
    assert sum(bool(scenario["selected"]) for scenario in scenarios) == 1
    selected = next(scenario for scenario in scenarios if scenario["selected"])
    assert "line_points" not in selected
    assert lstm.get("forecast_band_points") == []
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
    assert safe_lstm[0]["line_points"] == []


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


def test_episode_history_accepts_action_and_iso_timestamp_without_exposing_internals() -> None:
    payload = _fresh_payload()
    payload["recent_studies"] = [
        {
            "timestamp": "2026-07-18T08:30:00Z",
            "frame_id": 13,
            "action": "SELL",
            "state": "ENDED",
            "summary": "Event 3 closed below the starting block.",
            "episode_id": "episode-safe-1",
            "event_id": "episode-safe-1-e3",
            "event_index": 3,
            "source_path": r"C:\private\episode.json",
        }
    ]

    history = _build_workspace(payload, now_epoch=1_768_000_000.0)["history"]
    event = next(row for row in history if row.get("episode_id") == "episode-safe-1")

    assert event.get("id") == "episode-safe-1-e3"
    assert event["direction"] == "SELL"
    assert event.get("event_index") == 3
    assert event["observed_at"] == 1_784_363_400.0
    assert event["summary"] == "Event 3 closed below the starting block."
    assert "source_path" not in event


def test_tracking_episode_projects_frozen_plan_blocks_and_before_after_story() -> None:
    payload = _fresh_payload(side="SELL")
    payload["tracking_episode_readiness"] = {"ready": True, "reasons": []}
    visual_blocks = [
        {
            "step": step,
            "x_norm": 0.55 + step * 0.02,
            "open_y_norm": 0.45 + step * 0.002,
            "high_y_norm": 0.42 + step * 0.002,
            "low_y_norm": 0.49 + step * 0.002,
            "close_y_norm": 0.47 + step * 0.002,
            "movement_side": "SELL",
            "body_bias": "SELL",
        }
        for step in range(1, 13)
    ]
    sequence_path = [
        {
            "step": step,
            "expected_open_norm": 0.55 - step * 0.002,
            "expected_high_norm": 0.57 - step * 0.002,
            "expected_low_norm": 0.51 - step * 0.002,
            "expected_close_norm": 0.53 - step * 0.002,
            "movement_direction": "SELL",
            "candle_body_direction": "SELL",
            "raw_model_buy_probability": 0.01,
            "source_path": r"C:\private\forecast.json",
        }
        for step in range(1, 13)
    ]
    payload["tracking_episode"] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "episode_id": "episode-public-1",
        "state": "ACTIVE",
        "revision": 4,
        "event_horizon": 12,
        "event_cursor": 1,
        "started_at": "2026-07-18T08:00:00Z",
        "updated_at": "2026-07-18T08:05:00Z",
        "pair": "EUR/USD OTC",
        "timeframe": "M5",
        "committed_plan": {"decision": {"action": "SELL", "summary": "private trace"}},
        "baseline_forecasts": {
            "scene": {"forecast_candles": visual_blocks, "provider": "private"},
            "lstm": {"forecast_path": sequence_path, "model_version": "private"},
        },
        "events": [
            {
                "event_id": "episode-public-1:E1",
                "episode_id": "episode-public-1",
                "step": 1,
                "observed_at": "2026-07-18T08:05:00Z",
                "predicted_block": {"side": "SELL"},
                "actual_block": {"side": "SELL"},
                "direction_agreement": True,
                "after_reference": {"frame_id": 14, "source_path": r"C:\private\frame.png"},
            }
        ],
    }

    workspace = _build_workspace(payload, now_epoch=1_784_362_000.0)
    episode = cast(dict[str, object], cast(dict[str, object], workspace["tracking"])["episode"])

    assert episode["state"] == "ACTIVE"
    assert episode["progress"] == {"completed": 1, "total": 12}
    assert len(cast(list[object], episode["future_blocks"])) == 12
    assert cast(dict[str, object], episode["baseline"])["direction"] == "SELL"
    assert "matched" in str(cast(dict[str, object], episode["current"])["summary"])
    event = next(row for row in workspace["history"] if row.get("episode_id") == "episode-public-1")
    assert event.get("event_index") == 1
    assert event["direction"] == "SELL"
    serialized = json.dumps(episode).lower()
    assert "raw_model" not in serialized
    assert "provider" not in serialized
    assert "source_path" not in serialized
    assert "private trace" not in serialized


def test_tracking_episode_archive_is_projected_as_persistent_session_history() -> None:
    payload = _fresh_payload()
    payload["tracking_episode_history"] = [
        {
            "schema_version": "PG_TRACKING_EPISODE_HISTORY_ENTRY_V1",
            "episode_id": "episode-archived-1",
            "state": "STOPPED",
            "event_cursor": 7,
            "event_horizon": 12,
            "direction_agreement_count": 5,
            "direction_observation_count": 7,
            "ended_at": "2026-07-18T09:00:00Z",
            "anchor_frame_id": 40,
            "events": [
                {
                    "event_id": "episode-archived-1:E1",
                    "step": 1,
                    "observed_at": "2026-07-18T08:05:00Z",
                    "predicted_side": "SELL",
                    "actual_side": "SELL",
                    "direction_agreement": True,
                    "frame_id": 34,
                    "raw_block": {"provider": "private"},
                },
                {
                    "event_id": "episode-archived-1:E2",
                    "step": 2,
                    "observed_at": "2026-07-18T08:10:00Z",
                    "predicted_side": "SELL",
                    "actual_side": "BUY",
                    "direction_agreement": False,
                    "frame_id": 35,
                },
            ],
            "source_path": r"C:\private\episode.json",
            "provider": "private-provider",
        },
        {
            "schema_version": "PG_TRACKING_EPISODE_HISTORY_ENTRY_V1",
            "episode_id": "episode-archived-2",
            "state": "COMPLETED",
            "event_cursor": 1,
            "event_horizon": 12,
            "direction_agreement_count": 1,
            "direction_observation_count": 1,
            "ended_at": "2026-07-18T10:00:00Z",
            "anchor_frame_id": 52,
            "events": [
                {
                    "event_id": "episode-archived-2:E1",
                    "step": 1,
                    "observed_at": "2026-07-18T09:05:00Z",
                    "predicted_side": "BUY",
                    "actual_side": "BUY",
                    "direction_agreement": True,
                    "frame_id": 45,
                }
            ],
        },
    ]

    history = _build_workspace(payload, now_epoch=1_784_400_000.0)["history"]
    archived = next(
        row
        for row in history
        if row.get("id") == "episode-archived-1-summary"
    )

    assert archived.get("id") == "episode-archived-1-summary"
    assert archived.get("event_index") == 7
    assert archived["frame_id"] == 40
    assert archived["summary"] == (
        "Saved tracking study: 7 of 12 events recorded; "
        "5 of 7 directional blocks matched."
    )
    by_id = {
        str(row.get("id")): row
        for row in history
        if row.get("episode_id") in {
            "episode-archived-1",
            "episode-archived-2",
        }
    }
    assert {
        "episode-archived-1-e1",
        "episode-archived-1-e2",
        "episode-archived-1-summary",
        "episode-archived-2-e1",
        "episode-archived-2-summary",
    }.issubset(by_id)
    assert by_id["episode-archived-1-e1"].get("agreement") is True
    assert by_id["episode-archived-1-e2"].get("agreement") is False
    assert by_id["episode-archived-2-e1"].get("predicted_direction") == "BUY"
    serialized = json.dumps(list(by_id.values())).lower()
    assert "provider" not in serialized
    assert "source_path" not in serialized
    assert "raw_block" not in serialized
    assert "private" not in serialized


def test_session_history_preserves_every_retained_episode_event_and_summary() -> None:
    payload = _fresh_payload()
    payload["tracking_episode_history"] = [
        {
            "schema_version": "PG_TRACKING_EPISODE_HISTORY_ENTRY_V1",
            "episode_id": f"episode-retained-{episode_index:02d}",
            "state": "COMPLETED",
            "event_cursor": 12,
            "event_horizon": 12,
            "direction_agreement_count": 12,
            "direction_observation_count": 12,
            "ended_at": float(10_000 + episode_index * 100 + 99),
            "anchor_frame_id": episode_index * 100,
            "events": [
                {
                    "event_id": f"episode-retained-{episode_index:02d}-e{step}",
                    "step": step,
                    "observed_at": float(10_000 + episode_index * 100 + step),
                    "predicted_side": "BUY",
                    "actual_side": "BUY",
                    "direction_agreement": True,
                    "frame_id": episode_index * 100 + step,
                }
                for step in range(1, 13)
            ],
        }
        for episode_index in range(24)
    ]

    history = _build_workspace(payload, now_epoch=20_000.0)["history"]
    episode_rows = [row for row in history if row.get("episode_id")]
    row_ids = {str(row.get("id")) for row in episode_rows}

    assert len(episode_rows) == 24 * 13
    assert "episode-retained-00-e1" in row_ids
    assert "episode-retained-00-summary" in row_ids
    assert "episode-retained-23-e12" in row_ids
    assert "episode-retained-23-summary" in row_ids
    observed = [float(row.get("observed_at") or 0.0) for row in episode_rows]
    assert observed == sorted(observed)


def test_tracking_readiness_message_uses_plain_public_language() -> None:
    payload = _fresh_payload()
    payload["tracking_episode"] = {
        "schema_version": "PG_TRACKING_EPISODE_V1",
        "episode_id": "",
        "state": "IDLE",
        "revision": 0,
    }
    payload["tracking_episode_readiness"] = {
        "ready": False,
        "reasons": ["Wait for a complete 12-event Scene or LSTM forecast baseline."],
    }

    episode = cast(
        Mapping[str, object],
        cast(Mapping[str, object], _build_workspace(payload, now_epoch=100.0)["tracking"])[
            "episode"
        ],
    )

    assert episode["readiness_message"] == "Wait until all 12 future blocks are ready."
    serialized = json.dumps(episode).lower()
    assert "scene" not in serialized
    assert "lstm" not in serialized
    assert "forecast baseline" not in serialized


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

    expected_views: dict[str, tuple[str, set[str]]] = {
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
                "market_context",
                "two_candle",
                "lstm",
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
                "market_context",
                "council",
            },
        ),
        "structure": (
            "INSPECTOR",
            {"current_candles", "major_swings", "local_swings", "trendlines"},
        ),
        "zones": ("INSPECTOR", {"supply_demand"}),
        "plan": ("INSPECTOR", {"council", "triggers", "targets", "invalidation"}),
        "market_context": ("INSPECTOR", {"market_context"}),
        "two-candle": ("INSPECTOR", {"two_candle"}),
        "scene-forecaster": ("INSPECTOR", set()),
        "lstm": ("INSPECTOR", {"lstm"}),
        "forecast": ("INSPECTOR", {"two_candle", "lstm"}),
        "history": ("INSPECTOR", {"history", "major_swings", "local_swings"}),
    }
    public_family_views = cast(
        Mapping[str, frozenset[str] | None],
        getattr(mobile_app, "_OPERATOR_VIEW_TO_PUBLIC_FAMILIES"),
    )
    assert public_family_views["scene-forecaster"] == frozenset(
        {"scene_forecaster"}
    )
    assert public_family_views["forecast"] == frozenset(
        {"two_candle", "scene_forecaster", "lstm", "prediction"}
    )
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
            assert endpoint_scenarios == []
            assert forecast_row.get("line_points") == []
            assert forecast_row.get("geometry_kind") == "future_blocks"
            assert len(
                cast(Sequence[object], forecast_row.get("forecast_candles"))
            ) == 12
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
    assert counts == {"two_candle": 1, "lstm": 0, "prediction": 0}
    repaired_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    repaired_families = [
        str(row.get("family"))
        for row in cast(list[dict[str, object]], repaired_snapshot["overlays"])
    ]
    assert repaired_families.count("two_candle") == 1
    assert repaired_families.count("lstm") == 0
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
        "tracking_episode": {
            "schema_version": "PG_TRACKING_EPISODE_V1",
            "episode_id": "episode-cold-wait",
            "state": "ACTIVE",
            "revision": 2,
            "event_cursor": 0,
            "baseline_forecasts": {
                "scene": {"provider": "PRIVATE_EPISODE_PROVIDER"},
                "lstm": {"model_version": "PRIVATE_EPISODE_MODEL"},
            },
            "committed_plan": {"summary": "PRIVATE_EPISODE_PLAN"},
            "candidate_revision": {"source_path": r"C:\private\candidate.json"},
        },
        "tracking_episode_history": [
            {"episode_id": "archived-private", "provider": "PRIVATE_ARCHIVE_PROVIDER"}
        ],
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
            "high_frequency_study_timeframe": "M5",
            "last_capture_epoch": 99.0,
        },
        "forecast_snapshot_v3": {
            "schema_version": "PG_FORECAST_SNAPSHOT_V3",
            "source_frame_id": 14,
            "observed_epoch": 99.0,
            "stale": False,
            "diagnostic_only": False,
            "lstm_contribution": {
                "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
                "frame_id": 14,
                "pair": "CAD/JPY OTC",
                "timeframe": "M5",
                "fresh": True,
                "market_identity_confirmed": True,
                "timeframe_identity_confirmed": True,
                "forecast_available": True,
                "artifact_production_gate_passed": True,
                "production_authorized": True,
                "selective_authorized": True,
                "trade_authorization_status": "AUTHORIZED",
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
            if "authorized" in role:
                row["trade_authorization_status"] = "AUTHORIZED"
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
    assert "tracking_episode" not in compact_serialized
    assert "tracking_episode_history" not in compact_serialized
    assert "PRIVATE_EPISODE_PROVIDER" not in compact_serialized
    assert "PRIVATE_EPISODE_MODEL" not in compact_serialized
    assert "PRIVATE_EPISODE_PLAN" not in compact_serialized
    assert "PRIVATE_ARCHIVE_PROVIDER" not in compact_serialized
    assert "features" not in compact_serialized
    assert "raw_model_" not in compact_serialized
    fresh_workspace = cast(_OperatorWorkspaceView, fresh_response.json())
    fresh_counts = {
        family: sum(1 for row in fresh_workspace["overlays"] if row["family"] == family)
        for family in {"market_context", "supply_demand", "triggers", "two_candle", "lstm", "prediction"}
    }
    assert fresh_counts == {
        "market_context": 1,
        "supply_demand": 1,
        "triggers": 1,
        "two_candle": 1,
        "lstm": 0,
        "prediction": 0,
    }
    assert all(
        row.get("forecast_authorized") is True
        and row.get("forecast_status") == "AUTHORIZED"
        for row in fresh_workspace["overlays"]
        if row.get("forecast_role")
    )

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
        for family in {"market_context", "supply_demand", "triggers", "two_candle", "lstm", "prediction"}
    }
    assert waiting_counts == fresh_counts
    assert waiting_workspace["forecast"]["direction"] == "SELL"
    # The line-only LSTM fixture is intentionally excluded by the public
    # block-only contract, so the aligned near-term candle read owns the
    # restored forecast confidence.
    assert waiting_workspace["forecast"]["confidence"] == 0.64
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
    }
    assert sum(
        row.get("forecast_role") == "center"
        for row in forecast_workspace["overlays"]
    ) == 0
    assert all(
        row.get("forecast_authorized") is False
        and row.get("forecast_status") == "STALE"
        and row.get("trade_authorization_status") == "NO_EDGE"
        for row in forecast_workspace["overlays"]
        if row.get("forecast_role")
    ), [
        (
            row.get("forecast_role"),
            row.get("forecast_status"),
            row.get("forecast_authorized"),
            row.get("trade_authorization_status"),
        )
        for row in forecast_workspace["overlays"]
        if row.get("forecast_role")
    ]
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
