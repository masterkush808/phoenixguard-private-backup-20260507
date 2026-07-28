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
    semantic_id: NotRequired[str]
    overlay_semantic_revision: NotRequired[str]
    overlay_geometry_revision: NotRequired[str]
    anchor_id: NotRequired[str]
    positioning_status: NotRequired[str]
    positioning_basis: NotRequired[str]
    positioning_mode: NotRequired[str]
    immutable_geometry: NotRequired[bool]
    evidence_only: NotRequired[bool]
    geometry_role: NotRequired[str]
    reaction_window_anchor: NotRequired[str]
    source_bounds: NotRequired[list[float]]


class _HistoryView(TypedDict):
    observed_at: float | None
    direction: str
    state: str
    summary: str
    frame_id: _FrameId
    id: NotRequired[str]


class _OperatorWorkspaceView(TypedDict):
    schema_version: str
    session_id: str
    revision: int
    market: _MarketView
    tracking: _TrackingView
    freshness: _FreshnessView
    current_move: _MovementView
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
    for zone in zones:
        bounds = cast(list[float], zone["bounds"])
        zone.update(
            {
                "source_bounds": [0.12, bounds[1], 0.30, bounds[3]],
                "geometry_role": "FORWARD_REACTION_WINDOW",
                "reaction_window_anchor": "LATEST_COMPLETED_CANDLE",
            }
        )
    zones.append(
        {
            **zones[0],
            "zone_id": "order-zone-private-buy-limit-secondary",
            "bounds": [0.66, 0.64, 0.88, 0.70],
            "source_confidence": 0.99,
        }
    )
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
    for row in rows:
        bounds = cast(list[float], row["bounds"])
        row.update(
            {
                "source_bounds": [0.10, bounds[1], 0.28, bounds[3]],
                "geometry_role": "FORWARD_REACTION_WINDOW",
                "reaction_window_anchor": "LATEST_COMPLETED_CANDLE",
            }
        )
    rows.append(
        {
            **rows[1],
            "reference_id": "private-reference-sell-limit-secondary",
            "bounds": [0.64, 0.18, 0.84, 0.24],
        }
    )
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


def test_idle_workspace_does_not_publish_retired_order_area_previews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload(side="BUY")
    payload.update(
        {
            "symbol": "EUR/USD",
            "timeframe": "M5",
            "market_selector_visual_fingerprint": "selector_v2_eurusd",
            "instrument_identity_status": "LOCKED",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        }
    )
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
                "symbol": "EUR/USD",
                "timeframe": "M5",
                "market_selector_visual_fingerprint": "selector_v2_eurusd",
                "instrument_identity_status": "LOCKED",
            },
            {
                "overlay_id": "adaptive-target",
                "type": "TARGET_ZONE_BOX",
                "side": "BUY",
                "layer": "target_zones",
                "bounds": [0.62, 0.20, 0.72, 0.27],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "symbol": "EUR/USD",
                "timeframe": "M5",
                "market_selector_visual_fingerprint": "selector_v2_eurusd",
                "instrument_identity_status": "LOCKED",
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
        "build_current_order_positioning_candidate_v3",
        preview_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_current_order_reference_map_v3",
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

    assert preview_rows == []
    assert all(row.get("positioning_mode") == "REFERENCE" for row in reference_rows)
    assert all(row.get("immutable_geometry") is False for row in reference_rows)
    assert all(row.get("evidence_only") is True for row in reference_rows)
    assert any(row["id"] == "adaptive-trigger" for row in workspace["overlays"])
    assert any(row["id"] == "adaptive-target" for row in workspace["overlays"])
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
        "build_current_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_current_order_reference_map_v3",
        unavailable_reference_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert not any(
        row["family"] == "order_positioning" for row in workspace["overlays"]
    )
    serialized = json.dumps(workspace)
    assert "PRIVATE_SOURCE_LOCK_FAILURE" not in serialized
    assert "PG_ORDER_POSITIONING" not in serialized


def test_idle_preview_drops_unpaired_plan_failure_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload(side="SELL")
    candidate = _ready_positioning_preview_candidate()
    zones = cast(list[dict[str, object]], candidate["candidate_zones"])
    candidate["candidate_zones"] = [
        next(row for row in zones if row.get("order_kind") == "SELL_LIMIT"),
        # A SELL protective order protects a BUY thesis. There is no selected
        # BUY entry in this candidate, so the boundary must not float alone.
        next(row for row in zones if row.get("intent") == "PROTECTIVE_STOP"),
    ]

    def candidate_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return candidate

    def unavailable_reference_stub(
        _payload: Mapping[str, object],
    ) -> dict[str, object]:
        return {"status": "UNAVAILABLE"}

    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_current_order_positioning_candidate_v3",
        candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_current_order_reference_map_v3",
        unavailable_reference_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    positioning_rows = [
        row
        for row in workspace["overlays"]
        if row["family"] == "order_positioning"
    ]
    assert [row["kind"] for row in positioning_rows] == [
        "higher_price_sell_area"
    ]


def test_idle_blocked_candidate_publishes_observational_order_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload()
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
        "build_current_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_current_order_reference_map_v3",
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
    assert all(row.get("geometry_role") == "FORWARD_REACTION_WINDOW" for row in references)
    assert all(
        row.get("reaction_window_anchor") == "LATEST_COMPLETED_CANDLE"
        for row in references
    )
    assert all(row["label_hidden"] is False for row in references)
    assert all(row["lifecycle"] == "current" for row in references)
    assert any(
        row["id"] == "adaptive-target-remains-visible"
        for row in workspace["overlays"]
    )
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
                "geometry_role": "forward_reaction_window",
                "reaction_window_anchor": "latest_completed_candle",
                "source_bounds": [0.10, 0.22, 0.30, 0.28],
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
    assert safe_rows[0]["geometry_role"] == "FORWARD_REACTION_WINDOW"
    assert safe_rows[0]["reaction_window_anchor"] == "LATEST_COMPLETED_CANDLE"
    assert safe_rows[0]["source_bounds"] == [0.10, 0.22, 0.30, 0.28]
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

    stale_source_rows = mobile_app._safe_operator_overlay_rows(  # pyright: ignore[reportPrivateUsage]
        [
            {
                **safe_rows[0],
                "id": "missing-current-reaction-contract",
                "geometry_role": "",
                "reaction_window_anchor": "",
            }
        ]
    )
    assert stale_source_rows == []

    for invalid_source_bounds in (
        [0.10, 0.22, 0.30],
        [0.10, 0.22, 0.30, 0.28, 0.40],
        [float("nan"), 0.22, 0.30, 0.28],
        [-0.10, 0.22, 0.30, 0.28],
        [0.30, 0.22, 0.10, 0.28],
    ):
        malformed_origin_rows = mobile_app._safe_operator_overlay_rows(  # pyright: ignore[reportPrivateUsage]
            [
                {
                    **safe_rows[0],
                    "id": "malformed-history-origin",
                    "source_bounds": invalid_source_bounds,
                }
            ]
        )
        assert malformed_origin_rows == []


def test_order_reference_with_execution_authority_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fresh_payload()
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
        "build_current_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_current_order_reference_map_v3",
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
        "build_current_order_positioning_candidate_v3",
        blocked_candidate_stub,
    )
    monkeypatch.setattr(
        "phoenixguard.mobile_api.operator_workspace_v1."
        "build_current_order_reference_map_v3",
        legacy_reference_stub,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    assert not any(
        row["family"] == "order_positioning" for row in workspace["overlays"]
    )
    assert "legacy-plan-failure-reference" not in json.dumps(workspace)








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
        history_first = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=history"
        )
        all_second = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
    assert history_first.status_code == 200
    assert all_second.status_code == 200
    assert not any(
        row["family"] == "supply_demand"
        for row in history_first.json()["overlays"]
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
        history_second = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=history"
        )
    assert all_first.status_code == 200
    assert history_second.status_code == 200
    assert any(
        row["family"] == "supply_demand"
        for row in all_first.json()["overlays"]
    )
    assert not any(
        row["family"] == "supply_demand"
        for row in history_second.json()["overlays"]
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
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
        )
        duplicate_poll = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view=all"
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








def test_entry_permission_fails_closed_when_any_independent_authority_gate_is_missing() -> None:
    for missing_gate in (
        "direction",
        "freshness",
        "execution_packet",
        "execution_control",
        "opportunity",
        "movement_alignment",
        "pressure_alignment",
    ):
        payload = cast(
            dict[str, object],
            json.loads(json.dumps(_fresh_payload(side="BUY"))),
        )
        command = _mutable_mapping(payload["decision_command_center"])
        if missing_gate == "direction":
            command["selected_side"] = "HOLD"
        elif missing_gate == "freshness":
            command["fresh"] = False
            command["freshness_status"] = "STALE"
            command["valid_until_epoch"] = 99.0
        elif missing_gate == "execution_packet":
            command["execution_packet_present"] = False
        elif missing_gate == "execution_control":
            _mutable_mapping(payload["execution_controls"])[
                "live_execution_enabled"
            ] = False
        elif missing_gate == "opportunity":
            _mutable_mapping(command["execution_opportunity_window_v3"])[
                "state"
            ] = "CLOSED"
        elif missing_gate == "movement_alignment":
            _mutable_mapping(command["current_movement"])["side"] = "SELL"
        else:
            _mutable_mapping(command["pressure_event"])["side"] = "SELL"

        permission = _build_workspace(payload, now_epoch=100.0)["permission"]
        assert permission["action"] == "WAIT", missing_gate
        assert permission["allowed"] is False, missing_gate
        assert permission["side"] == "NEUTRAL", missing_gate


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
    assert "forecast" not in workspace
    serialized = json.dumps(workspace)
    assert "private-signature" not in serialized
    assert "duplicate_study_count" not in serialized












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


def test_ended_opposite_pressure_does_not_pollute_regression_history() -> None:
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
    assert workspace["history"] == []


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
            "market_confidence": 0.93,
            "timeframe_confidence": 0.91,
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "market_selector_visual_fingerprint": "selector_v2_eurusdotc",
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
            "market_confidence": 0.93,
            "timeframe_confidence": 0.91,
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "market_selector_visual_fingerprint": "selector_v2_eurusdotc",
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


def test_retired_fixed_forecasts_do_not_enter_public_overlay_contract() -> None:
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
                "layer": "prediction_path",
                "bounds": [0.55, 0.20, 0.90, 0.40],
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

    assert {
        "two-candle-current",
        "lstm-study-current",
        "lstm-path-current",
        "scene-study-current",
    }.isdisjoint(by_id)
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
        for key in ("current_movement", "pressure_event"):
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


def test_history_retains_continuous_chronological_regression_studies() -> None:
    payload = _fresh_payload()
    payload["recent_studies"] = [
        {
            "id": f"closed-candle-{index}",
            "created_epoch": float(index),
            "observed_at": float(index),
            "frame_id": index,
            "market_study_v3": {
                "schema_version": "PG_MARKET_STUDY_V3",
                "status": "STUDIED",
                "study_only": True,
                "execution_authority": False,
                "can_grant_entry_permission": False,
                "symbol": "CAD/JPY OTC",
                "timeframe": "M5",
                "closed_candle_key": f"closed-candle-{index}",
                "closed_candle_sequence": index,
                "regression": {
                    "major_trend": {"side": "BUY", "confidence": 0.8},
                    "inner_trend": {
                        "side": "SELL" if index % 2 else "BUY",
                        "confidence": 0.6,
                    },
                },
                "behavior": {
                    "current_state": {
                        "state": "REST" if index % 3 == 0 else "SWING",
                        "candle_count": index % 5 + 1,
                        "duration_seconds": (index % 5 + 1) * 300,
                    }
                },
                "directional_read": {
                    "side": "SELL" if index % 2 else "BUY",
                    "confidence": 0.7,
                },
            },
        }
        for index in range(40)
    ]
    tracking_summary = _mutable_mapping(payload["tracking_summary"])
    tracking_summary["market_study_v3"] = payload["recent_studies"][-1][
        "market_study_v3"
    ]

    workspace = _build_workspace(payload, now_epoch=100.0)
    history = workspace["history"]

    assert len(history) == 40
    observed = [row["observed_at"] or 0 for row in history]
    assert observed == sorted(observed)
    assert all(row["major_trend"]["side"] == "BUY" for row in history)
    assert all(row["inner_trend"]["side"] in {"BUY", "SELL"} for row in history)
    assert all(row["behavior"]["current_state"]["state"] in {"REST", "SWING"} for row in history)
    assert all(row["regression_read"]["side"] in {"BUY", "SELL"} for row in history)












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
    assert "forecast" not in workspace


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


def test_operator_route_returns_only_the_current_public_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "operator-route-contract"
    compact_state = _fresh_payload(side="BUY", now=200.0)
    compact_state.update(
        {
            "session_id": session_id,
            "state_version": 22,
            "display_frame_id": 22,
            "last_capture_epoch": 199.0,
            "tracking_enabled": True,
            "symbol": "GBP/USD",
            "timeframe": "M5",
            "tracking_summary": {
                "detected_market": "GBP/USD",
                "detected_timeframe": "M5",
                "last_capture_epoch": 199.0,
            },
            "overlays": {
                "objects": [
                    {
                        "overlay_id": "route-demand",
                        "type": "DEMAND_ZONE",
                        "side": "BUY",
                        "layer": "supply_demand",
                        "bounds": [10, 20, 40, 60],
                        "frame_id": 22,
                        "coordinate_mode": "CHART_IMAGE_SPACE",
                    },
                    {
                        "overlay_id": "route-internal-model-path",
                        "type": "LSTM_STUDY",
                        "layer": "prediction_path",
                        "bounds": [0.55, 0.30, 0.90, 0.42],
                        "frame_id": 22,
                        "coordinate_mode": "CHART_NORMALIZED",
                    },
                ]
            },
        }
    )
    command = _mutable_mapping(compact_state["decision_command_center"])
    for leg_key in ("current_movement", "pressure_event"):
        leg = _mutable_mapping(command[leg_key])
        leg["frame_id"] = 22

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
        **_kwargs: object,
    ) -> dict[str, object]:
        assert isinstance(tracker, _Tracker)
        assert requested_session_id == session_id
        return compact_state

    monkeypatch.setattr(mobile_app.RUNTIME, "data_dir", tmp_path)
    monkeypatch.setattr(
        mobile_app,
        "build_live_state_v3_from_tracker_service",
        _build_compact_state,
    )
    client = TestClient(mobile_app.create_app(window_tracker_service=_Tracker()))

    response = client.get(f"/v1/mobile/operator/state/v1/{session_id}?view=all")

    assert response.status_code == 200
    workspace = cast(_OperatorWorkspaceView, response.json())
    assert set(workspace) == TOP_LEVEL_KEYS
    assert "forecast" not in workspace
    assert workspace["market"] == {"symbol": "GBP/USD", "timeframe": "M5"}
    assert workspace["overlays"] == []
    assert workspace["permission"]["allowed"] is False
    assert _all_keys(workspace).isdisjoint(
        {"provider_status", "frame_timing_trace_v3", "source_path", "reason"}
    )

    for retired_view in ("forecast", "lstm", "two-candle", "scene-forecaster", "prediction"):
        rejected = client.get(
            f"/v1/mobile/operator/state/v1/{session_id}?view={retired_view}"
        )
        assert rejected.status_code == 400
        assert rejected.json() == {"detail": "Unsupported operator view."}




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
