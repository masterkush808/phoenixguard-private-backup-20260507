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
    cpu_stream_tracking_contract_v3,
    refresh_operator_streaming_read_v3,
)


TOP_LEVEL_KEYS = {
    "schema_version",
    "session_id",
    "revision",
    "market",
    "three_questions",
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


class _StreamView(TypedDict):
    enabled: bool
    state: str
    acquisition_fps: float | None
    observed_frames: int
    accepted_keyframes: int
    dropped_frames: int
    duplicate_frames: int
    last_frame_epoch: float | None
    last_keyframe_epoch: float | None
    heartbeat_epoch: float | None
    fresh: bool
    last_reason: str
    stream_generation: int
    market_read: dict[str, object]


class _TrackingView(TypedDict):
    active: bool
    state: str
    updated_at: float | None
    history_count: int
    market_study_v3: dict[str, object]
    stream: _StreamView


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


class _QuestionAnswerView(TypedDict):
    question: str
    headline: str
    answer: str
    state: str
    side: str
    confidence: float
    evidence: dict[str, object]
    updated_at: float | None


class _EntryQuestionAnswerView(_QuestionAnswerView):
    enter_now: bool
    action: str
    reason: str
    next_trigger: str
    timing_state: str
    decision: str
    decision_state: str
    permission_allowed: bool
    entry_permission_authorized: bool
    timing_supports_entry: bool
    timing_veto: bool
    timing_forecast: NotRequired[dict[str, object]]
    study_projection: NotRequired[dict[str, object]]
    operator_action: NotRequired[dict[str, object]]
    identity_rebind_pending: NotRequired[bool]
    broker_expiry_v3: NotRequired[dict[str, object]]
    broker_expiry_proven: NotRequired[bool]
    broker_expiry_eligible: NotRequired[bool]


class _ThreeQuestionView(TypedDict):
    schema_version: str
    market_origin_history: _QuestionAnswerView
    studied_direction_current: _QuestionAnswerView
    entry_now: _EntryQuestionAnswerView


class _OperatorWorkspaceView(TypedDict):
    schema_version: str
    session_id: str
    revision: int
    market: _MarketView
    three_questions: _ThreeQuestionView
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
        "capture_count": 14,
        "input_frame_hash": "frame-eurusd-m5-14",
        "instrument_identity_hash": "pginst-eurusd-m5",
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
            "broker_expiry_contract_v3": {
                "schema_version": "PG_BROKER_EXPIRY_PROOF_V3",
                "proven": True,
                "expiry_seconds": 1_800,
                "symbol": "EUR/USD",
                "timeframe": "M5",
                "closed_candle_key": "3d40b65dac4324cb7bb8e288",
                "frame_id": 14,
                "input_frame_hash": "frame-eurusd-m5-14",
                "valid_until_epoch": now + 20,
                "source": "BROKER_UI_BOUND",
            },
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


def _wgc_identity_overlay_payload() -> dict[str, object]:
    payload = _fresh_payload(side="BUY")
    payload.update(
        {
            "symbol": "CAD/CHF OTC",
            "timeframe": "M5",
            "instrument_identity_status": "LOCKED",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
        }
    )
    tracking = _mutable_mapping(payload["tracking_summary"])
    tracking.update(
        {
            "detected_market": "CAD/CHF OTC",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "market_selector_rebind_required": False,
            "market_selector_studying_new_pair": False,
            "broker_source": {
                "valid": True,
                "status": "VALID",
                "wrong_surface": False,
                "title_valid": False,
                "pixel_fingerprint_valid": True,
                "study_source_only": True,
                "broker_click_safe": False,
            },
            "broker_source_lock": {
                "schema_version": "BROKER_SOURCE_LOCK_V3",
                "status": "VALID",
                "valid": True,
                "broker_source_locked": True,
                "reason_codes": [
                    "EXTERNAL_FRAME_FEED_LOCKED",
                    "CHART_STUDY_SOURCE_LOCKED",
                ],
                "selected_target": {
                    "title": "windows-region-capture-v3",
                    "target_id": "wgc-target-14",
                },
                "surface_guard": {
                    "surface_class": "BROKER_SURFACE",
                    "wrong_surface": False,
                    "capture_safe": True,
                    "broker_like_pixels": True,
                    "evidence": {
                        "source_id": "windows-region-capture-v3",
                        "sequence_id": "wgc-sequence-14",
                        "source_type": "windows_graphics_capture_roi",
                        "coordinate_space": "wgc_hwnd_roi_v1",
                    },
                },
                "broker_pixel_fingerprint": "wgc-frame-fingerprint-14",
                "evidence": {
                    "source_id": "windows-region-capture-v3",
                    "sequence_id": "wgc-sequence-14",
                    "source_type": "windows_graphics_capture_roi",
                    "coordinate_space": "wgc_hwnd_roi_v1",
                    "study_source_expected": True,
                    "chart_source_like": True,
                    "study_source_only": True,
                    "broker_click_safe": False,
                },
            },
        }
    )
    payload["overlays"] = {
        "objects": [
            {
                "overlay_id": "wgc-demand-zone-14",
                "type": "DEMAND_ZONE",
                "side": "BUY",
                "layer": "supply_demand",
                "bounds": [0.22, 0.55, 0.48, 0.68],
                "frame_id": 14,
                "coordinate_mode": "CHART_NORMALIZED",
                "symbol": "CAD/CHF OTC",
                "timeframe": "M5",
                "instrument_identity_status": "LOCKED",
            }
        ]
    }
    return payload


def test_wgc_study_source_projects_identity_locked_overlays_without_selector_fingerprint() -> None:
    workspace = _build_workspace(
        _wgc_identity_overlay_payload(),
        now_epoch=100.0,
    )

    assert len(workspace["overlays"]) == 1
    overlay = workspace["overlays"][0]
    assert overlay["id"] == "wgc-demand-zone-14"
    assert overlay["symbol"] == "CAD/CHF OTC"
    assert overlay["timeframe"] == "M5"
    assert overlay["market_selector_visual_fingerprint"] == ""
    assert overlay["instrument_identity_status"] == "LOCKED"


@pytest.mark.parametrize(
    "failure_case",
    (
        "wrong_source_id",
        "wrong_source_type",
        "wrong_coordinate_space",
        "invalid_lock",
        "stale_lock",
        "wrong_surface",
        "unsafe_capture",
        "identity_pending",
        "identity_disagreement",
        "stale_source_claim",
        "unexpected_overlay_fingerprint",
    ),
)
def test_wgc_no_selector_identity_proof_fails_closed(
    failure_case: str,
) -> None:
    payload = _wgc_identity_overlay_payload()
    tracking = _mutable_mapping(payload["tracking_summary"])
    source_lock = _mutable_mapping(tracking["broker_source_lock"])
    lock_evidence = _mutable_mapping(source_lock["evidence"])
    surface_guard = _mutable_mapping(source_lock["surface_guard"])
    guard_evidence = _mutable_mapping(surface_guard["evidence"])

    if failure_case == "wrong_source_id":
        lock_evidence["source_id"] = "other-capture-source"
    elif failure_case == "wrong_source_type":
        lock_evidence["source_type"] = "browser_tab_roi_capture"
    elif failure_case == "wrong_coordinate_space":
        guard_evidence["coordinate_space"] = "edge_tab_roi_v1"
    elif failure_case == "invalid_lock":
        source_lock["valid"] = False
    elif failure_case == "stale_lock":
        source_lock["status"] = "STALE"
    elif failure_case == "wrong_surface":
        surface_guard["wrong_surface"] = True
    elif failure_case == "unsafe_capture":
        surface_guard["capture_safe"] = False
    elif failure_case == "identity_pending":
        tracking["market_selector_rebind_required"] = True
    elif failure_case == "identity_disagreement":
        payload["identity_disagreement"] = True
    elif failure_case == "stale_source_claim":
        _mutable_mapping(tracking["broker_source"])["status"] = "STALE"
    elif failure_case == "unexpected_overlay_fingerprint":
        overlays = _mutable_mapping(payload["overlays"])
        objects = cast(list[dict[str, object]], overlays["objects"])
        objects[0]["market_selector_visual_fingerprint"] = "selector_v2_other"
    else:
        raise AssertionError(f"Unhandled WGC failure case: {failure_case}")

    assert _build_workspace(payload, now_epoch=100.0)["overlays"] == []


def _cpu_stream_runtime_payload(
    *,
    now: float,
    frame_seq: int,
    state: str,
) -> dict[str, object]:
    return {
        "session_id": "desk-live-1",
        "cpu_stream_v3": {
            "requested": True,
            "enabled": True,
            "available": True,
            "status": "active",
            "actual_fps": 2.0,
            "observed_frames": frame_seq,
            "last_capture_epoch": now - 0.1,
            "status_updated_epoch": now - 0.05,
            "broker_click_authority": True,
            "private_frame_path": r"C:\secret\frame.png",
            "observer": {
                "frame_seq": frame_seq,
                "stream_generation": 2,
                "last_captured_epoch": now - 0.1,
                "last_frame_hash": "private-frame-hash",
                "last_decision": {
                    "frame_seq": frame_seq,
                    "stream_generation": 2,
                    "input_frame_hash": "private-frame-hash",
                    "temporal_evidence": {
                        "frame_seq": frame_seq,
                        "stream_generation": 2,
                        "state": state,
                        "direction": "BUY",
                        "motion": {
                            "state": state,
                            "motion_score": 0.24 if state == "motion" else 0.01,
                            "motion_acceleration": 0.03,
                        },
                        "change": {"changed_pixel_ratio": 0.16},
                        "rest": {
                            "active": state in {"rest", "duplicate"},
                            "duration_sec": 2.5,
                        },
                        "wick_motion": {"dominant_extreme": "LOWER"},
                    },
                },
            },
        },
    }


def _countertrend_study_payload(*, now: float = 100.0) -> dict[str, object]:
    payload = _fresh_payload(side="SELL", now=now)
    tracking = _mutable_mapping(payload["tracking_summary"])
    study: dict[str, object] = {
        "schema_version": "PG_MARKET_STUDY_V3",
        "status": "STUDIED",
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
        "symbol": "EUR/USD",
        "timeframe": "M5",
        "closed_candle_key": "3d40b65dac4324cb7bb8e288",
        "closed_candle_sequence": 88,
        "regression": {
            "major_trend": {
                "side": "BUY",
                "confidence": 0.86,
                "window_candles": 34,
            },
            "inner_trend": {
                "side": "SELL",
                "confidence": 0.78,
                "window_candles": 9,
            },
            "current_pressure": {"side": "SELL", "confidence": 0.81},
        },
        "behavior": {
            "current_state": {
                "state": "SWING",
                "direction": "SELL",
                "candle_count": 3,
                "duration_seconds": 900,
            }
        },
        "directional_read": {
            "side": "SELL",
            "confidence": 0.82,
            "status": "SUPPORTED",
        },
        "pair_dna": {"observation_count": 419},
    }
    tracking["market_study_v3"] = study
    payload["recent_studies"] = [
        {
            "observed_at": now - 301,
            "frame_id": 12,
            "market_study_v3": {
                **study,
                "closed_candle_key": "4f50c76ebd5435dc8cc9f399",
                "closed_candle_sequence": 86,
                "directional_read": {
                    "side": "BUY",
                    "confidence": 0.72,
                    "status": "SUPPORTED",
                },
            },
        },
        {
            "observed_at": now - 1,
            "frame_id": 14,
            "market_study_v3": study,
        },
    ]
    command = _mutable_mapping(payload["decision_command_center"])
    command["sides"] = {
        "BUY": {"score": 0.46, "selected": False},
        "SELL": {"score": 0.84, "selected": True},
    }
    command["buy_score"] = 0.46
    command["sell_score"] = 0.84
    return payload


def _attach_mature_path_clock_timing(
    payload: dict[str, object],
    *,
    supports_entry: bool,
    timing_veto: bool,
    contract_duration_seconds: int = 1_800,
    remaining_seconds: int = 1_800,
    closed_candle_key: str | None = None,
    valid_until: float | None = None,
) -> None:
    tracking = _mutable_mapping(payload["tracking_summary"])
    study = _mutable_mapping(tracking["market_study_v3"])
    study_side = str(_mutable_mapping(study["directional_read"])["side"])
    effective_closed_candle_key = closed_candle_key or str(
        study["closed_candle_key"]
    )
    timing_read: dict[str, object] = {
        "status": "TIMING_SUPPORT",
        "side": study_side,
        "eligible": True,
        "contract_admitted": True,
        "new_entry_eligible": remaining_seconds >= 900,
        "contract_duration_seconds": contract_duration_seconds,
        "elapsed_seconds": contract_duration_seconds - remaining_seconds,
        "remaining_seconds": remaining_seconds,
        "support_count": 48,
        "minimum_support": 32,
        "survival_probability": 0.74,
        "probability_worst_drawdown_still_ahead": 0.31,
        "timing_supports_entry": supports_entry,
        "timing_veto": timing_veto,
        "neighbor_evidence": [{"trajectory_id": "private-neighbour"}],
    }
    if valid_until is not None:
        timing_read["valid_until"] = valid_until
    study["path_clock_liquidity_v3"] = {
        "schema_version": "PG_PATH_CLOCK_LIQUIDITY_FIELD_V3",
        "status": "STUDIED",
        "reason": "The four-axis replay gate passed.",
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
        "symbol": study["symbol"],
        "timeframe": study["timeframe"],
        "closed_candle_key": effective_closed_candle_key,
        "closed_candle_sequence": study["closed_candle_sequence"],
        "minimum_eligible_duration_seconds": 900,
        "maximum_studied_duration_seconds": 7_200,
        "timing_read": timing_read,
        "promotion_gate": {
            "status": "PROMOTION_ELIGIBLE",
            "passed": True,
            "all_axes_improved": True,
            "minimum_replays": 32,
            "support": {"baseline": 48, "candidate": 48, "passed": True},
            "axes": {"private": "must-not-cross"},
        },
        "replay_score": {
            "audited_replay_count": 52,
            "eligible_replay_count": 48,
            "excluded_early_move_count": 4,
            "metrics": {
                "directional_accuracy": 0.73,
                "timing_accuracy": 0.69,
                "sweep_survival_rate": 0.71,
                "calibration_score": 0.81,
            },
        },
        "trajectories": [{"trajectory_id": "private-trajectory"}],
        "freezes": [{"closed_candle_key": "private-freeze"}],
        "liquidity_state": {"wick_entropy": 0.42},
    }


def _attach_forward_timing_forecast(
    payload: dict[str, object],
    *,
    side: str | None = None,
    probability: float | None = 0.68,
    evidence_confidence: float = 0.41,
    calibration_grade: str = "C_SPARSE_PAIR",
    calibrated: bool = False,
    source_tier: str = "PAIR",
    support_count: int = 11,
    sweep_support_count: int = 11,
    exact_anchor_epoch: float | None = None,
) -> None:
    _attach_mature_path_clock_timing(
        payload,
        supports_entry=False,
        timing_veto=False,
    )
    tracking = _mutable_mapping(payload["tracking_summary"])
    study = _mutable_mapping(tracking["market_study_v3"])
    forecast_side = side or str(
        _mutable_mapping(study["directional_read"])["side"]
    )
    timing_contract = _mutable_mapping(study["path_clock_liquidity_v3"])
    timing_read = _mutable_mapping(timing_contract["timing_read"])
    forecast: dict[str, object] = {
        "schema_version": "PG_JPCLF_FORWARD_TIMING_FORECAST_V3",
        "probability_semantics_version": (
            "PG_JPCLF_FORWARD_PROBABILITY_SEMANTICS_V3"
        ),
        "status": "FORECAST_AVAILABLE",
        "candidate_direction": "UP" if forecast_side == "BUY" else "DOWN",
        "current_regime": "TREND",
        "forecast_horizon_seconds": 1_800,
        "lineage": {
            "symbol": study["symbol"],
            "timeframe": study["timeframe"],
            "closed_candle_key": study["closed_candle_key"],
            "closed_candle_sequence": study["closed_candle_sequence"],
            "source_cadence_seconds": 300,
            "lineage_bound": True,
            "freshness_state": "CURRENT_CLOSED_CANDLE",
            "lineage_digest": "forecast-lineage-test",
            **(
                {"anchor_close_epoch_seconds": exact_anchor_epoch}
                if exact_anchor_epoch is not None
                else {}
            ),
        },
        "move_window": {
            "earliest": {"seconds": 900, "minutes": 15, "candles": 3},
            "central": {"seconds": 1_200, "minutes": 20, "candles": 4},
            "latest": {"seconds": 1_800, "minutes": 30, "candles": 6},
            "basis": "CLOSED_CANDLE_RELATIVE_DECLARED_CADENCE",
            "relative_to": "CLOSED_CANDLE_ANCHOR",
            "rolling_wall_clock": False,
            "exact_wall_clock_proven": exact_anchor_epoch is not None,
            "anchor_time_proven": exact_anchor_epoch is not None,
            "estimate_calibrated": calibrated,
            **(
                {
                    "anchor_close_epoch_seconds": exact_anchor_epoch,
                    "target_window_start_epoch_seconds": exact_anchor_epoch
                    + 900.0,
                    "target_window_central_epoch_seconds": exact_anchor_epoch
                    + 1_200.0,
                    "target_window_end_epoch_seconds": exact_anchor_epoch
                    + 1_800.0,
                }
                if exact_anchor_epoch is not None
                else {}
            ),
        },
        "probability": {
            "value": probability,
            "confidence": evidence_confidence if support_count > 0 else None,
            "metric": "MOTIF_TARGET_FOLLOW_THROUGH_WITHIN_FORECAST_HORIZON",
            "source_tier": source_tier,
            "calibration_grade": calibration_grade,
            "calibrated": calibrated,
            "support_count": support_count,
            "shrinkage_weight": 0.22,
            "compatibility_alias_for": "event_likelihood",
        },
        "directional_model": {
            "candidate_direction": "UP" if forecast_side == "BUY" else "DOWN",
            "score": 0.84,
            "source": "CURRENT_DIRECTIONAL_ENSEMBLE",
            "is_event_likelihood": False,
        },
        "timing_estimate": {
            "source_tier": source_tier,
            "basis": (
                "CURRENT_CLOSED_CANDLE_SEQUENCE_AND_DECLARED_CADENCE"
                if source_tier == "LIVE_M5_SEQUENCE"
                else "PAIR_DNA_BEHAVIOR_DURATION"
            ),
            "empirical_timing_evidence": source_tier != "LIVE_M5_SEQUENCE",
            "support_count": support_count if source_tier != "LIVE_M5_SEQUENCE" else 0,
            "current_sequence_candle_count": 3,
            "window_blend_weight": 0.22,
        },
        "event_likelihood": {
            "value": probability if support_count > 0 else None,
            "event": "MOTIF_TARGET_FOLLOW_THROUGH_WITHIN_FORECAST_HORIZON",
            "source_tier": source_tier if support_count > 0 else "NONE",
            "support_count": support_count,
            "calibrated": calibrated,
        },
        "evidence_confidence": {
            "value": evidence_confidence if support_count > 0 else None,
            "basis": (
                "EMPIRICAL_OUTCOME_SUPPORT_SATURATION"
                if support_count > 0
                else "NO_EMPIRICAL_OUTCOME_SUPPORT"
            ),
            "support_count": support_count,
        },
        "state_transition_estimate": {
            "value": 0.61 if support_count > 0 else None,
            "transition": "REST->DOWN_SWING",
            "target_count": support_count,
            "support_count": support_count,
            "source_tier": "PAIR_STATE_TRANSITIONS",
            "is_directional_likelihood": False,
        },
        "stop_survival": {
            "value": None,
            "source_tier": "NONE",
            "support_count": 0,
            "exact_wall_clock_proven": False,
            "calibrated": False,
        },
        "adverse_excursion_risk": {
            "worst_drawdown_still_ahead_probability": None,
            "source_tier": "NONE",
            "support_count": 0,
        },
        "expected_pre_move": {
            "state": "REST_THEN_MOVE",
            "rest_window_candles": {
                "earliest": 1,
                "central": 2,
                "latest": 3,
            },
            "rest_window_minutes": {
                "earliest": 5,
                "central": 10,
                "latest": 15,
            },
            "sweep_probability": 0.43,
            "sweep_risk": "MEDIUM",
            "sweep_source_tier": (
                "PAIR_REGIME_MOTIF" if sweep_support_count > 0 else "NONE"
            ),
            "sweep_support_count": sweep_support_count,
        },
        "invalidation": {
            "direction": "UP" if forecast_side == "SELL" else "DOWN",
            "condition": "Invalidate after a completed candle changes direction.",
            "adverse_distance_mru": 1.25,
            "expires_after_seconds": 1_800,
            "expires_after_candles": 6,
            "closed_candles_only": True,
        },
        "enter_now": {
            "permission": False,
            "duration_eligible": True,
            "timing_advisory": "FORWARD_WINDOW_AVAILABLE",
            "reason": "Timing only.",
            "permission_source": "INDEPENDENT_ENTRY_CONTRACT_REQUIRED",
        },
        "evidence_hierarchy": {
            "selected_tier": source_tier,
            "support_count": support_count,
        },
        "study_only": True,
        "execution_authority": False,
        "broker_click_authority": False,
        "can_grant_entry_permission": False,
    }
    timing_contract["forward_timing_forecast"] = forecast
    timing_read["forward_timing_forecast"] = forecast


def _mark_active_target_next_impulse(
    payload: dict[str, object],
) -> None:
    event_definition = "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"
    tracking = _mutable_mapping(payload["tracking_summary"])
    study = _mutable_mapping(tracking["market_study_v3"])
    timing_contract = _mutable_mapping(study["path_clock_liquidity_v3"])
    forward = _mutable_mapping(timing_contract["forward_timing_forecast"])
    forward["status"] = "FORECAST_AVAILABLE"
    _mutable_mapping(forward["move_window"])[
        "event_definition"
    ] = event_definition
    _mutable_mapping(forward["timing_estimate"]).update(
        {
            "event_definition": event_definition,
            "current_target_state": "ALREADY_ACTIVE_AT_ANCHOR",
        }
    )
    _mutable_mapping(forward["event_likelihood"]).update(
        {
            "value": None,
            "event": event_definition,
            "source_tier": "NONE",
            "support_count": 0,
            "calibrated": False,
        }
    )
    _mutable_mapping(forward["probability"]).update(
        {
            "value": None,
            "confidence": None,
            "metric": event_definition,
            "support_count": 0,
            "calibrated": False,
        }
    )


def _retarget_m5_study_payload(
    payload: dict[str, object],
    *,
    symbol: str,
    closed_candle_key: str,
    side: str,
) -> None:
    tracking = _mutable_mapping(payload["tracking_summary"])
    tracking["detected_market"] = symbol
    tracking["detected_timeframe"] = "M5"
    study = _mutable_mapping(tracking["market_study_v3"])
    study["symbol"] = symbol
    study["timeframe"] = "M5"
    study["closed_candle_key"] = closed_candle_key
    regression = _mutable_mapping(study["regression"])
    for key in ("major_trend", "inner_trend", "current_pressure"):
        _mutable_mapping(regression[key])["side"] = side
    current_state = _mutable_mapping(_mutable_mapping(study["behavior"])["current_state"])
    current_state["direction"] = side
    _mutable_mapping(study["directional_read"])["side"] = side
    command = _mutable_mapping(payload["decision_command_center"])
    command["selected_side"] = side
    command["execution_packet_present"] = False
    command["sides"] = {
        "BUY": {"score": 0.84 if side == "BUY" else 0.42, "selected": side == "BUY"},
        "SELL": {"score": 0.84 if side == "SELL" else 0.42, "selected": side == "SELL"},
    }
    command["buy_score"] = 0.84 if side == "BUY" else 0.42
    command["sell_score"] = 0.84 if side == "SELL" else 0.42
    for key in ("current_movement", "pressure_event"):
        _mutable_mapping(command[key])["side"] = side
    payload["recent_studies"] = []


def _set_current_regression_side(
    payload: dict[str, object],
    *,
    side: str,
) -> None:
    tracking = _mutable_mapping(payload["tracking_summary"])
    market_study = _mutable_mapping(tracking["market_study_v3"])
    directional_read = _mutable_mapping(market_study["directional_read"])
    directional_read["side"] = side


def _countertrend_execution_lineage(*, now: float = 100.0) -> dict[str, object]:
    return {
        "packet_id": "pgpkt-countertrend-eurusd-m5-14",
        "opportunity_id": "pgepisode-countertrend-eurusd-m5-14",
        "session_id": "desk-live-1",
        "symbol": "EUR/USD",
        "timeframe": "M5",
        "frame_id": 14,
        "capture_count": 14,
        "state_version": 14,
        "input_frame_hash": "frame-eurusd-m5-14",
        "instrument_identity_hash": "pginst-eurusd-m5",
        "trigger_closed_candle_key": "3d40b65dac4324cb7bb8e288",
        "opportunity_key": "pgopp-countertrend-eurusd-m5",
        "trigger_frame_id": 14,
        "valid_until_epoch": now + 20.0,
        "integrity_valid": True,
        "lineage_rejected": False,
    }


def _countertrend_enter_now_promotion(*, now: float = 100.0) -> dict[str, object]:
    return {
        "schema_version": "PG_COUNTERTREND_SNIPER_PROMOTION_V3",
        "phase": "VALIDATED",
        "active": True,
        "classification": "ENTER_NOW",
        "side": "SELL",
        "against_global_side": "BUY",
        "entry_permission_authorized": True,
        "movement_confirmation_bypass_allowed": True,
        "execution_packet_present": True,
        "validated_entry_mode": "COUNTERTREND_SNIPER",
        "broker_click_authority": False,
        "lineage": _countertrend_execution_lineage(now=now),
        "ensemble_basis": {"council_side_score": 0.89},
    }


def _bind_countertrend_command(
    payload: dict[str, object],
    promotion: Mapping[str, object],
) -> None:
    command = _mutable_mapping(payload["decision_command_center"])
    lineage = _mutable_mapping(promotion["lineage"])
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity.update(
        {
            "opportunity_id": lineage["opportunity_id"],
            "opportunity_key": lineage["opportunity_key"],
            "trigger_frame_id": lineage["trigger_frame_id"],
        }
    )
    command["execution_packet_id"] = lineage["packet_id"]
    command["execution_lineage"] = dict(lineage)
    command["countertrend_sniper_promotion_v3"] = dict(promotion)


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
        "coordinate_mode",
    }
    assert _all_keys(workspace).isdisjoint(forbidden_keys)
    serialized = json.dumps(workspace)
    assert r"C:\\secret" not in serialized
    assert "exec-secret" not in serialized
    assert "private-agent" not in serialized


def test_operator_workspace_sanitizes_cpu_stream_health_without_authority() -> None:
    payload = _fresh_payload()
    payload["cpu_stream_v3"] = {
        "enabled": "yes",
        "state": "streaming",
        "acquisition_fps": 31.98765,
        "observed_frames": 123,
        "accepted_keyframes": 44,
        "dropped_frames": -5,
        "duplicate_frames": "7",
        "last_frame_epoch": 99.75,
        "last_keyframe_epoch": 99.5,
        "status_updated_epoch": 99.9,
        "last_reason": "  Pixel change\naccepted  ",
        "stream_generation": 9,
        "observer": {
            "frame_seq": 123,
            "last_captured_epoch": 99.75,
            "last_decision": {
                "frame_seq": 123,
                "stream_generation": 9,
                "temporal_evidence": {
                    "frame_seq": 123,
                    "stream_generation": 9,
                    "state": "motion",
                    "motion": {
                        "state": "motion",
                        "motion_score": 0.21,
                        "motion_acceleration": 0.04,
                    },
                    "change": {"changed_pixel_ratio": 0.18},
                    "rest": {"active": False, "duration_sec": 0.0},
                    "wick_motion": {"dominant_extreme": "UPPER"},
                },
            },
        },
        "execution_authority": True,
        "broker_click_authority": True,
        "private_capture_policy": "FULL_MODEL_ALWAYS",
    }

    stream = _build_workspace(payload, now_epoch=100.0)["tracking"]["stream"]

    assert cpu_stream_tracking_contract_v3(payload, now_epoch=100.0) == stream
    assert set(stream) == {
        "enabled",
        "state",
        "acquisition_fps",
        "observed_frames",
        "accepted_keyframes",
        "dropped_frames",
        "duplicate_frames",
        "last_frame_epoch",
        "last_keyframe_epoch",
        "heartbeat_epoch",
        "fresh",
        "last_reason",
        "stream_generation",
        "market_read",
    }
    assert stream["enabled"] is True
    assert stream["state"] == "RUNNING"
    assert stream["acquisition_fps"] == 31.988
    assert stream["observed_frames"] == 123
    assert stream["accepted_keyframes"] == 44
    assert stream["dropped_frames"] == 0
    assert stream["duplicate_frames"] == 7
    assert stream["last_frame_epoch"] == 99.75
    assert stream["last_keyframe_epoch"] == 99.5
    assert stream["heartbeat_epoch"] == 99.9
    assert stream["fresh"] is True
    assert stream["last_reason"] == "Pixel change accepted"
    assert stream["stream_generation"] == 9
    market_read = cast(dict[str, object], stream["market_read"])
    assert market_read["state"] == "MOVING"
    assert market_read["fresh"] is True
    assert market_read["freshness_budget_seconds"] == 8.0
    assert market_read["frame_seq"] == 123
    assert market_read["direction"] == "NEUTRAL"
    assert market_read["direction_available"] is False
    assert market_read["forming_candle"] is True
    assert market_read["closed_candle"] is False
    assert market_read["can_grant_entry_permission"] is False
    assert market_read["execution_authority"] is False
    assert market_read["broker_click_authority"] is False
    serialized = json.dumps(stream)
    assert '"broker_click_authority": true' not in serialized
    assert '"execution_authority": true' not in serialized
    assert "FULL_MODEL_ALWAYS" not in serialized


def test_slow_advancing_cpu_stream_uses_bounded_adaptive_freshness() -> None:
    payload = _cpu_stream_runtime_payload(now=100.0, frame_seq=124, state="motion")
    stream = cast(dict[str, object], payload["cpu_stream_v3"])
    stream["target_fps"] = 0.5
    stream["actual_fps"] = 0.07
    stream["last_capture_epoch"] = 76.0
    stream["status_updated_epoch"] = 80.0
    observer = cast(dict[str, object], stream["observer"])
    observer["last_captured_epoch"] = 76.0
    last_decision = cast(dict[str, object], observer["last_decision"])
    temporal = cast(dict[str, object], last_decision["temporal_evidence"])
    temporal["frame_delta_sec"] = 3.5

    public_stream = cpu_stream_tracking_contract_v3(payload, now_epoch=100.0)
    market_read = cast(dict[str, object], public_stream["market_read"])

    assert public_stream["fresh"] is True
    assert market_read["fresh"] is True
    assert market_read["state"] == "MOVING"
    assert market_read["frame_age_seconds"] == 24.0
    assert market_read["heartbeat_age_seconds"] == 20.0
    assert market_read["freshness_budget_seconds"] == 42.857
    assert market_read["can_grant_entry_permission"] is False


def test_cpu_stream_becomes_stale_beyond_adaptive_freshness_ceiling() -> None:
    payload = _cpu_stream_runtime_payload(now=100.0, frame_seq=125, state="motion")
    stream = cast(dict[str, object], payload["cpu_stream_v3"])
    stream["target_fps"] = 0.5
    stream["actual_fps"] = 0.01
    stream["last_capture_epoch"] = 54.0
    stream["status_updated_epoch"] = 54.0

    public_stream = cpu_stream_tracking_contract_v3(payload, now_epoch=100.0)
    market_read = cast(dict[str, object], public_stream["market_read"])

    assert public_stream["fresh"] is False
    assert market_read["fresh"] is False
    assert market_read["state"] == "STALE"
    assert market_read["freshness_budget_seconds"] == 45.0


def test_unavailable_cpu_stream_is_stale_even_with_current_timestamps() -> None:
    payload = _cpu_stream_runtime_payload(now=100.0, frame_seq=126, state="motion")
    stream = cast(dict[str, object], payload["cpu_stream_v3"])
    stream["available"] = False

    public_stream = cpu_stream_tracking_contract_v3(payload, now_epoch=100.0)
    market_read = cast(dict[str, object], public_stream["market_read"])

    assert public_stream["state"] == "RUNNING"
    assert public_stream["fresh"] is False
    assert market_read["state"] == "STALE"
    assert market_read["fresh"] is False


def test_normal_cpu_stream_keeps_short_freshness_budget() -> None:
    payload = _cpu_stream_runtime_payload(now=100.0, frame_seq=127, state="rest")

    public_stream = cpu_stream_tracking_contract_v3(payload, now_epoch=100.0)
    market_read = cast(dict[str, object], public_stream["market_read"])

    assert public_stream["fresh"] is True
    assert market_read["state"] == "RESTING"
    assert market_read["freshness_budget_seconds"] == 8.0


def test_operator_workspace_adapts_nested_runtime_stream_health_aliases() -> None:
    payload = _fresh_payload()
    tracking = _mutable_mapping(payload["tracking_summary"])
    tracking["cpu_stream_v3"] = {
        "requested": True,
        "status": "degraded_snapshot_fallback",
        "actual_fps": 7.25,
        "observed_frames": 87,
        "accepted_events": 12,
        "dropped_keyframes": 3,
        "last_capture_epoch": 98.75,
        "last_event_epoch": 98.25,
        "last_error": r"C:\secret\stream.log",
        "observer": {
            "counters": {"duplicate_frames": 19},
            "internal_threshold": 0.17,
        },
        "last_keyframe_lineage": {
            "stream_generation": 4,
            "input_frame_hash": "private-hash",
        },
    }

    stream = _build_workspace(payload, now_epoch=100.0)["tracking"]["stream"]

    assert stream["enabled"] is True
    assert stream["state"] == "DEGRADED"
    assert stream["acquisition_fps"] == 7.25
    assert stream["accepted_keyframes"] == 12
    assert stream["dropped_frames"] == 3
    assert stream["duplicate_frames"] == 19
    assert stream["last_frame_epoch"] == 98.75
    assert stream["last_keyframe_epoch"] == 98.25
    assert stream["last_reason"] == ""
    assert stream["stream_generation"] == 4
    serialized = json.dumps(stream)
    assert "private-hash" not in serialized
    assert r"C:\\secret" not in serialized


def test_fresh_stream_heartbeat_updates_current_read_and_best_action_without_permission() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    payload.update(_cpu_stream_runtime_payload(now=100.0, frame_seq=41, state="motion"))

    workspace = _build_workspace(payload, now_epoch=100.0)
    questions = workspace["three_questions"]
    studied = questions["studied_direction_current"]
    decision = questions["entry_now"]

    assert studied["side"] == "SELL"
    assert studied["updated_at"] == 99.95
    assert "live stream is moving" in studied["headline"]
    assert "intrabar observation" in studied["answer"]
    studied_read = cast(
        dict[str, object],
        cast(dict[str, object], studied["evidence"])["streaming_market_read"],
    )
    assert studied_read["state"] == "MOVING"
    assert studied_read["direction"] == "NEUTRAL"
    assert studied_read["direction_available"] is False

    assert decision["question"] == "What is the best decision to do right now?"
    assert decision["decision"] == "TRACK_SELL_CONTINUATION"
    assert decision["decision_state"] == "TRACKING"
    assert decision["action"] == "DO_NOT_ENTER"
    assert decision["enter_now"] is False
    assert decision["updated_at"] == 99.95
    decision_evidence = cast(dict[str, object], decision["evidence"])
    assert decision_evidence["entry_permission_authorized"] is False
    assert decision_evidence["execution_authority"] is False
    assert decision_evidence["broker_click_authority"] is False
    serialized = json.dumps(workspace)
    assert "private-frame-hash" not in serialized
    assert r"C:\\secret" not in serialized
    assert '"direction": "BUY"' not in json.dumps(workspace["tracking"]["stream"])


def test_resting_stream_watches_directional_retrace_and_names_current_reference() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    base = _build_workspace(payload, now_epoch=100.0)
    public_workspace = cast(dict[str, object], base)
    public_workspace["overlays"] = [
        *base["overlays"],
        {
            "layer": "order_positioning",
            "side": "SELL",
            "label": "Higher price sell area",
            "lifecycle": "current",
            "positioning_mode": "REFERENCE",
        },
    ]

    refreshed = refresh_operator_streaming_read_v3(
        public_workspace,
        _cpu_stream_runtime_payload(now=100.0, frame_seq=42, state="rest"),
        now_epoch=100.0,
    )
    entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )

    assert entry["decision"] == "WATCH_SELL_RALLY"
    assert entry["decision_state"] == "WATCHING_RETRACE"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["enter_now"] is False
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert operator_action["state"] == "WAIT_FOR_PULLBACK"
    assert "Higher price sell area" in str(operator_action["instruction"])
    assert "fresh SELL pullback" in str(operator_action["instruction"])
    assert "fresh verified entry window" in str(operator_action["instruction"])
    evidence = cast(dict[str, object], entry["evidence"])
    assert evidence["execution_authority"] is False
    assert evidence["broker_click_authority"] is False


def test_duplicate_stream_pixels_are_not_reported_as_market_rest() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    base = _build_workspace(payload, now_epoch=100.0)

    refreshed = refresh_operator_streaming_read_v3(
        base,
        _cpu_stream_runtime_payload(now=100.0, frame_seq=43, state="duplicate"),
        now_epoch=100.0,
    )
    entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )

    assert entry["decision"] == "TRACK_SELL"
    assert entry["decision_state"] == "OBSERVING_CAPTURE"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["enter_now"] is False
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert entry["headline"] == "PREPARE"
    assert operator_action["state"] == "PREPARE"
    assert "unchanged pixels" in str(operator_action["instruction"]).lower()
    assert "do not prove a market rest" in str(
        operator_action["instruction"]
    ).lower()


def test_missed_stream_decision_waits_for_fresh_directional_pullback() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity["state"] = "EXPIRED"
    opportunity["valid_until_epoch"] = 99.0
    payload.update(_cpu_stream_runtime_payload(now=100.0, frame_seq=43, state="motion"))

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"]["entry_now"]

    assert entry["decision"] == "WAIT_FOR_FRESH_SELL_PULLBACK"
    assert entry["decision_state"] == "MISSED"
    assert entry["timing_state"] == "MISSED"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["enter_now"] is False
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert entry["headline"] == "WAIT FOR PULLBACK"
    assert operator_action["state"] == "WAIT_FOR_PULLBACK"
    assert "fresh SELL pullback" in str(operator_action["instruction"])
    assert "fresh verified entry window" in str(operator_action["instruction"])


def test_missed_prior_thesis_yields_to_aligned_current_regression_and_major() -> None:
    payload = _countertrend_study_payload()
    _set_current_regression_side(payload, side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity["state"] = "EXPIRED"
    opportunity["valid_until_epoch"] = 99.0
    payload.update(_cpu_stream_runtime_payload(now=100.0, frame_seq=44, state="motion"))

    workspace = _build_workspace(payload, now_epoch=100.0)
    studied = workspace["three_questions"]["studied_direction_current"]
    entry = workspace["three_questions"]["entry_now"]

    assert studied["side"] == "SELL"
    assert studied["evidence"]["ensemble_studied_side"] == "SELL"
    assert studied["evidence"]["current_regression_side"] == "BUY"
    assert studied["evidence"]["major_trend_side"] == "BUY"
    assert entry["decision"] == "WAIT_FOR_FRESH_BUY_PULLBACK"
    assert entry["side"] == "BUY"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["evidence"]["prior_studied_side"] == "SELL"
    assert entry["evidence"]["current_actionable_study_side"] == "BUY"
    assert entry["evidence"]["prior_thesis_superseded"] is True
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert entry["headline"] == "WAIT FOR PULLBACK"
    assert "prior SELL" in str(operator_action["instruction"])
    assert "current closed-candle study now tracks BUY" in str(
        operator_action["instruction"]
    )
    assert "fresh BUY pullback" in str(operator_action["instruction"])


def test_current_lineage_buy_forecast_is_not_hidden_by_older_sell_command() -> None:
    payload = _countertrend_study_payload()
    _set_current_regression_side(payload, side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    _attach_forward_timing_forecast(
        payload,
        side="BUY",
        probability=None,
        evidence_confidence=0.0,
        calibration_grade="D_CURRENT_SEQUENCE",
        source_tier="LIVE_M5_SEQUENCE",
        support_count=0,
        sweep_support_count=0,
    )
    payload.update(
        _cpu_stream_runtime_payload(now=100.0, frame_seq=46, state="rest")
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    studied = workspace["three_questions"]["studied_direction_current"]
    entry = workspace["three_questions"]["entry_now"]
    forecast = cast(dict[str, object], entry["timing_forecast"])
    evidence = cast(dict[str, object], entry["evidence"])

    assert studied["side"] == "SELL"
    assert studied["evidence"]["current_regression_side"] == "BUY"
    assert entry["side"] == "BUY"
    assert evidence["historical_studied_side"] == "SELL"
    assert evidence["current_forecast_side"] == "BUY"
    assert evidence["forecast_uses_current_regression"] is True
    assert evidence["direction_source"] == "CURRENT_CLOSED_CANDLE_FORECAST"
    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["side"] == "BUY"
    assert forecast["horizon_label"] == (
        "3–6 completed M5 candles after the anchor close"
    )
    assert "timing will publish" not in str(entry["headline"]).lower()
    assert entry["decision"] == "WATCH_BUY_PULLBACK"
    assert entry["operator_action"]["state"] == "WAIT_FOR_PULLBACK"


def test_resting_stream_watches_current_buy_after_prior_sell_expires() -> None:
    payload = _countertrend_study_payload()
    _set_current_regression_side(payload, side="BUY")
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    payload.update(_cpu_stream_runtime_payload(now=100.0, frame_seq=45, state="rest"))

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"]["entry_now"]

    assert entry["timing_state"] == "FORMING"
    assert entry["decision"] == "WATCH_BUY_PULLBACK"
    assert entry["decision_state"] == "WATCHING_RETRACE"
    assert entry["side"] == "BUY"
    assert entry["enter_now"] is False
    assert entry["action"] == "DO_NOT_ENTER"
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert operator_action["state"] == "WAIT_FOR_PULLBACK"
    assert "prior SELL thesis remains history" in str(
        operator_action["instruction"]
    )
    assert "current closed-candle study now tracks BUY" in str(
        operator_action["instruction"]
    )


def test_stream_refresh_updates_cached_questions_without_rebuilding_closed_candle_study() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    base = _build_workspace(payload, now_epoch=100.0)
    original_study_key = base["tracking"]["market_study_v3"]["closed_candle_key"]

    moving = refresh_operator_streaming_read_v3(
        base,
        _cpu_stream_runtime_payload(now=101.0, frame_seq=51, state="motion"),
        now_epoch=101.0,
    )
    resting = refresh_operator_streaming_read_v3(
        moving,
        _cpu_stream_runtime_payload(now=102.0, frame_seq=52, state="rest"),
        now_epoch=102.0,
    )

    assert moving["revision"] == resting["revision"] == base["revision"]
    assert moving["tracking"]["stream"]["market_read"]["state"] == "MOVING"
    assert resting["tracking"]["stream"]["market_read"]["state"] == "RESTING"
    assert (
        moving["three_questions"]["entry_now"]["decision"]
        == "TRACK_SELL_CONTINUATION"
    )
    assert resting["three_questions"]["entry_now"]["decision"] == "WATCH_SELL_RALLY"
    assert resting["three_questions"]["entry_now"]["action"] == "DO_NOT_ENTER"
    assert resting["tracking"]["market_study_v3"]["closed_candle_key"] == original_study_key


def test_expired_cached_enter_now_is_cleared_by_public_stream_synthesis() -> None:
    base = _build_workspace(_countertrend_study_payload(), now_epoch=100.0)
    assert base["three_questions"]["entry_now"]["enter_now"] is True
    expired = dict(base)
    expired["permission"] = {
        **cast(dict[str, object], base["permission"]),
        "allowed": False,
        "action": "WAIT",
        "side": "NEUTRAL",
        "window_open": False,
    }

    refreshed = refresh_operator_streaming_read_v3(
        expired,
        _cpu_stream_runtime_payload(now=101.0, frame_seq=61, state="motion"),
        now_epoch=101.0,
    )
    decision = refreshed["three_questions"]["entry_now"]

    assert decision["enter_now"] is False
    assert decision["action"] == "DO_NOT_ENTER"
    assert decision["timing_state"] != "ENTER_NOW"
    assert decision["state"] != "ENTER_NOW"
    assert decision["decision"] == "TRACK_SELL_CONTINUATION"
    assert refreshed["permission"]["allowed"] is False


def test_three_questions_explain_countertrend_sell_without_turning_study_into_permission() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False

    brief = _build_workspace(payload, now_epoch=100.0)["three_questions"]

    assert brief["schema_version"] == "PG_THREE_QUESTION_OPERATOR_BRIEF_V3"
    history = brief["market_origin_history"]
    assert history["state"] == "CURRENT"
    assert history["side"] == "BUY"
    assert "upward major structure" in history["answer"]
    assert history["evidence"]["behavior_state"] == "SWING"
    assert history["evidence"]["history_observation_count"] == 2

    study = brief["studied_direction_current"]
    assert study["state"] == "CURRENT"
    assert study["side"] == "SELL"
    assert study["confidence"] == 0.84
    assert study["evidence"]["ensemble_studied_side"] == "SELL"
    assert study["evidence"]["current_regression_side"] == "SELL"
    assert study["evidence"]["countertrend"] is True
    assert "countertrend SELL study inside a BUY major trend" in study["answer"]

    entry = brief["entry_now"]
    assert entry["enter_now"] is False
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["timing_state"] == "FORMING"
    assert entry["state"] == "FORMING"
    assert entry["side"] == "SELL"
    assert entry["headline"] == "PREPARE"
    assert cast(dict[str, object], entry["operator_action"])["state"] == "PREPARE"
    assert not entry["reason"].lower().startswith("wait")


def test_three_questions_do_not_call_missing_entry_evidence_stale_history() -> None:
    workspace = _build_workspace(
        {
            "session_id": "desk-live-1",
            "tracking_enabled": False,
            "tracking_summary": {},
            "decision_command_center": {},
        },
        now_epoch=100.0,
    )

    entry = workspace["three_questions"]["entry_now"]

    assert entry["timing_state"] == "FORMING"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["headline"] == "STAY OUT"
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert operator_action["state"] == "AVOID"
    assert "No identity-proven completed study" in str(
        operator_action["instruction"]
    )
    assert "last trade study" not in str(operator_action["instruction"]).lower()


def test_three_questions_publish_enter_now_only_from_existing_permission_contract() -> None:
    payload = _countertrend_study_payload()

    workspace = _build_workspace(payload, now_epoch=100.0)
    entry = workspace["three_questions"]["entry_now"]

    assert workspace["permission"]["allowed"] is True
    assert entry["enter_now"] is True
    assert entry["action"] == "SELL_NOW"
    assert entry["timing_state"] == "ENTER_NOW"
    assert entry["side"] == "SELL"
    assert entry["evidence"]["permission_allowed"] is True


@pytest.mark.parametrize(
    ("symbol", "closed_candle_key", "side"),
    [
        ("EUR/USD", "key-eurusd-m5-101", "BUY"),
        ("GBP/JPY OTC", "key-gbpjpy-m5-202", "SELL"),
        ("AUD/CAD OTC", "key-audcad-m5-303", "BUY"),
        ("NZD/USD OTC", "key-nzdusd-m5-404", "SELL"),
        ("CHF/JPY OTC", "key-chfjpy-m5-505", "BUY"),
    ],
)
def test_five_pair_m5_forecasts_are_concrete_bounded_and_stream_stable(
    symbol: str,
    closed_candle_key: str,
    side: str,
) -> None:
    payload = _countertrend_study_payload()
    _retarget_m5_study_payload(
        payload,
        symbol=symbol,
        closed_candle_key=closed_candle_key,
        side=side,
    )
    _attach_forward_timing_forecast(
        payload,
        probability=None,
        evidence_confidence=0.58,
        calibration_grade="D_CURRENT_SEQUENCE",
        source_tier="LIVE_M5_SEQUENCE",
        support_count=0,
        sweep_support_count=0,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    entry = workspace["three_questions"]["entry_now"]
    forecast = cast(dict[str, object], entry["timing_forecast"])
    action = cast(dict[str, object], entry["operator_action"])

    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["side"] == side
    assert forecast["horizon_seconds_low"] >= 900
    assert forecast["horizon_candles_low"] >= 3
    assert forecast["horizon_candles_high"] >= forecast["horizon_candles_low"]
    assert forecast["horizon_label"] == (
        "3–6 completed M5 candles after the anchor close"
    )
    assert "next" not in str(forecast["headline"]).lower()
    assert " min" not in str(forecast["headline"]).lower()
    assert forecast["source"] == "LIVE_M5_SEQUENCE"
    assert forecast["calibration_grade"] == "D_CURRENT_SEQUENCE"
    assert forecast["estimated_likelihood"] is None
    assert forecast["evidence_confidence"] is None
    assert forecast["directional_model_score"] == 0.84
    assert forecast["directional_model_score_label"] == (
        "84% directional model score · not probability"
    )
    assert forecast["timing_evidence_label"] == (
        "Current M5 closed-candle sequence · 3 current candles"
    )
    assert "Evidence grade D \u00b7 current sequence" in str(
        forecast["calibration_label"]
    )
    scope = cast(dict[str, object], forecast["scope"])
    assert scope == {
        "symbol": symbol,
        "timeframe": "M5",
        "closed_candle_key": closed_candle_key,
        "identity_proven": True,
    }
    assert side in str(forecast["headline"])
    assert "NOT YET" not in str(entry["headline"]).upper()
    assert "NOT YET" not in str(entry["answer"]).upper()
    assert action["state"] == "PREPARE"
    projection = cast(dict[str, object], entry["study_projection"])
    assert projection["headline"] == (
        f"{side} direction studied · timing range withheld"
    )
    assert projection["timing_range_publishable"] is False
    assert "not replay-calibrated" in str(projection["summary"])
    assert projection["study_only"] is True
    assert projection["can_grant_entry_permission"] is False

    runtime = _cpu_stream_runtime_payload(
        now=101.0,
        frame_seq=81,
        state="motion",
    )
    runtime["tracking_summary"] = {
        "detected_market": symbol,
        "detected_timeframe": "M5",
    }
    refreshed = refresh_operator_streaming_read_v3(
        workspace,
        runtime,
        now_epoch=101.0,
    )
    refreshed_entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )
    refreshed_forecast = cast(
        dict[str, object], refreshed_entry["timing_forecast"]
    )
    refreshed_action = cast(dict[str, object], refreshed_entry["operator_action"])
    assert refreshed_forecast["headline"] == forecast["headline"]
    assert cast(dict[str, object], refreshed_forecast["scope"])["symbol"] == symbol
    assert cast(dict[str, object], refreshed_forecast["scope"])[
        "closed_candle_key"
    ] == closed_candle_key
    assert refreshed_action["state"] == "PREPARE"

    later_runtime = _cpu_stream_runtime_payload(
        now=401.0,
        frame_seq=82,
        state="rest",
    )
    later_runtime["tracking_summary"] = {
        "detected_market": symbol,
        "detected_timeframe": "M5",
    }
    later = refresh_operator_streaming_read_v3(
        refreshed,
        later_runtime,
        now_epoch=401.0,
    )
    later_entry = cast(
        dict[str, object],
        cast(dict[str, object], later["three_questions"])["entry_now"],
    )
    later_forecast = cast(
        dict[str, object], later_entry["timing_forecast"]
    )
    assert later_forecast["headline"] == forecast["headline"]
    assert later_forecast["horizon_label"] == forecast["horizon_label"]


def test_active_target_forecasts_next_impulse_without_inviting_a_chase() -> None:
    payload = _countertrend_study_payload()
    _retarget_m5_study_payload(
        payload,
        symbol="EUR/USD",
        closed_candle_key="active-buy-close",
        side="BUY",
    )
    _attach_forward_timing_forecast(
        payload,
        probability=None,
        evidence_confidence=0.58,
        calibration_grade="D_CURRENT_SEQUENCE",
        source_tier="LIVE_M5_SEQUENCE",
        support_count=0,
        sweep_support_count=0,
    )
    _mark_active_target_next_impulse(payload)

    workspace = _build_workspace(payload, now_epoch=100.0)
    entry = cast(
        dict[str, object],
        cast(dict[str, object], workspace["three_questions"])["entry_now"],
    )
    forecast = cast(dict[str, object], entry["timing_forecast"])
    action = cast(dict[str, object], entry["operator_action"])

    assert forecast["headline"] == (
        "BUY is active · next BUY impulse estimated "
        "3–6 completed M5 candles after the anchor close"
    )
    assert forecast["event_definition"] == (
        "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"
    )
    assert forecast["active_target_next_impulse"] is True
    assert forecast["target_move_already_active"] is True
    assert forecast["estimated_likelihood"] is None
    assert "current BUY move is mature and already active" in str(
        forecast["summary"]
    )
    assert "next BUY impulse" in str(forecast["summary"])
    assert "one rest or pullback" in str(forecast["summary"])
    assert "not permission to chase or enter" in str(forecast["summary"])
    assert entry["enter_now"] is False
    assert entry["headline"] == "WAIT FOR PULLBACK"
    projection = cast(dict[str, object], entry["study_projection"])
    assert projection["headline"] == (
        "BUY direction studied · timing range withheld"
    )
    assert projection["support_count"] == 0
    assert projection["calibrated"] is False
    assert projection["timing_range_publishable"] is False
    assert action["state"] == "WAIT_FOR_PULLBACK"
    assert "Do not chase" in str(action["instruction"])
    assert "one completed rest or pullback" in str(action["instruction"])

    runtime = _cpu_stream_runtime_payload(
        now=101.0,
        frame_seq=83,
        state="motion",
    )
    runtime["tracking_summary"] = {
        "detected_market": "EUR/USD",
        "detected_timeframe": "M5",
    }
    refreshed = refresh_operator_streaming_read_v3(
        workspace,
        runtime,
        now_epoch=101.0,
    )
    refreshed_entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )
    refreshed_forecast = cast(
        dict[str, object], refreshed_entry["timing_forecast"]
    )
    refreshed_action = cast(
        dict[str, object], refreshed_entry["operator_action"]
    )
    assert refreshed_forecast["headline"] == forecast["headline"]
    assert refreshed_action["state"] == "WAIT_FOR_PULLBACK"
    assert refreshed_entry["enter_now"] is False

    enter_payload = _countertrend_study_payload()
    _attach_forward_timing_forecast(
        enter_payload,
        source_tier="LIVE_M5_SEQUENCE",
    )
    _mark_active_target_next_impulse(enter_payload)
    enter_entry = cast(
        dict[str, object],
        cast(
            dict[str, object],
            _build_workspace(enter_payload, now_epoch=100.0)[
                "three_questions"
            ],
        )["entry_now"],
    )
    enter_action = cast(dict[str, object], enter_entry["operator_action"])
    assert enter_entry["enter_now"] is True
    assert enter_action["state"] == "ENTER_NOW"


def test_forward_forecast_separates_estimated_likelihood_from_evidence_confidence() -> None:
    payload = _countertrend_study_payload()
    _attach_forward_timing_forecast(payload)

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]
    forecast = cast(dict[str, object], entry["timing_forecast"])
    action = cast(dict[str, object], entry["operator_action"])

    assert forecast["headline"] == (
        "SELL leading 3–6 completed M5 candles after the anchor close"
    )
    assert forecast["estimated_likelihood"] == 0.68
    assert forecast["evidence_confidence"] == 0.41
    assert forecast["confidence"] == 0.41
    assert forecast["estimated_likelihood_label"] == (
        "68% estimated chance of motif target follow-through within the "
        "forecast horizon · not replay-calibrated"
    )
    assert forecast["evidence_confidence_label"] == "41% evidence confidence"
    assert forecast["directional_model_score"] == 0.84
    assert forecast["directional_model_score_label"] == (
        "84% directional model score · not probability"
    )
    assert forecast["event_likelihood_event_label"] == (
        "motif target follow-through within the forecast horizon"
    )
    assert forecast["timing_evidence_label"] == (
        "Pair behavior timing history · empirical · 11 timing observations"
    )
    assert forecast["calibration_grade"] == "C_SPARSE_PAIR"
    assert "Evidence grade C · sparse pair history" in str(
        forecast["calibration_label"]
    )
    assert "C_SPARSE_PAIR" not in str(forecast["calibration_label"])
    assert "Rest may persist 1–3 candles" in str(forecast["rest_sweep_risk"])
    assert "43%" in str(forecast["rest_sweep_risk"])
    assert forecast["invalidation"] == (
        "Invalidate after a completed candle changes direction."
    )
    assert entry["enter_now"] is True
    assert action["state"] == "ENTER_NOW"


def test_exact_window_uses_fixed_epochs_counts_down_and_expires_without_reset() -> None:
    payload = _countertrend_study_payload()
    _attach_forward_timing_forecast(
        payload,
        calibrated=True,
        exact_anchor_epoch=100.0,
    )
    study = _mutable_mapping(
        _mutable_mapping(payload["tracking_summary"])["market_study_v3"]
    )
    timing = _mutable_mapping(study["path_clock_liquidity_v3"])
    forward = _mutable_mapping(timing["forward_timing_forecast"])
    _mutable_mapping(forward["stop_survival"]).update(
        {
            "value": 0.72,
            "source_tier": "EXACT_JPCLF",
            "support_count": 40,
            "exact_wall_clock_proven": True,
            "calibrated": True,
        }
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    initial_entry = cast(
        dict[str, object], workspace["three_questions"]["entry_now"]
    )
    initial = cast(dict[str, object], initial_entry["timing_forecast"])
    initial_projection = cast(
        dict[str, object], initial_entry["study_projection"]
    )
    assert initial["exact_wall_clock_proven"] is True
    assert initial["target_window_start_epoch_seconds"] == 1_000.0
    assert initial["target_window_end_epoch_seconds"] == 1_900.0
    assert initial["seconds_until_window_start"] == 900
    assert initial["seconds_until_window_end"] == 1_800
    assert "fixed window opens in 15 min" in str(initial["headline"])
    assert initial_projection["timing_range_publishable"] is True
    assert initial_projection["headline"] == initial["headline"]

    at_400 = refresh_operator_streaming_read_v3(
        workspace,
        _cpu_stream_runtime_payload(now=400.0, frame_seq=91, state="motion"),
        now_epoch=400.0,
    )
    at_400_forecast = cast(
        dict[str, object],
        cast(dict[str, object], at_400["three_questions"])["entry_now"][
            "timing_forecast"
        ],
    )
    assert at_400_forecast["target_window_end_epoch_seconds"] == 1_900.0
    assert at_400_forecast["seconds_until_window_start"] == 600
    assert at_400_forecast["seconds_until_window_end"] == 1_500
    assert "opens in 10 min" in str(at_400_forecast["headline"])

    at_700 = refresh_operator_streaming_read_v3(
        at_400,
        _cpu_stream_runtime_payload(now=700.0, frame_seq=92, state="rest"),
        now_epoch=700.0,
    )
    at_700_forecast = cast(
        dict[str, object],
        cast(dict[str, object], at_700["three_questions"])["entry_now"][
            "timing_forecast"
        ],
    )
    assert at_700_forecast["target_window_end_epoch_seconds"] == 1_900.0
    assert at_700_forecast["seconds_until_window_start"] == 300
    assert at_700_forecast["seconds_until_window_end"] == 1_200
    assert "opens in 5 min" in str(at_700_forecast["headline"])

    expired = refresh_operator_streaming_read_v3(
        at_700,
        _cpu_stream_runtime_payload(now=1_901.0, frame_seq=93, state="motion"),
        now_epoch=1_901.0,
    )
    expired_forecast = cast(
        dict[str, object],
        cast(dict[str, object], expired["three_questions"])["entry_now"][
            "timing_forecast"
        ],
    )
    assert expired_forecast["exact_wall_clock_proven"] is False
    assert expired_forecast["target_window_start_epoch_seconds"] is None
    assert expired_forecast["target_window_end_epoch_seconds"] is None
    assert expired_forecast["estimated_likelihood"] is None
    assert "exact timing expired" in str(expired_forecast["headline"])
    expired_technical = cast(
        dict[str, object], expired_forecast["technical_estimates"]
    )
    assert expired_technical["stop_survival"] == {}
    assert expired_technical["adverse_excursion_risk"] == {}


def test_stream_pair_switch_discards_prior_forecast_and_permission() -> None:
    base_payload = _countertrend_study_payload()
    _attach_forward_timing_forecast(base_payload)
    base = _build_workspace(base_payload, now_epoch=100.0)
    prior_forecast = cast(
        dict[str, object], base["three_questions"]["entry_now"]["timing_forecast"]
    )
    assert cast(dict[str, object], prior_forecast["scope"])["symbol"] == "EUR/USD"

    same_frame_runtime = _cpu_stream_runtime_payload(
        now=101.0,
        frame_seq=82,
        state="motion",
    )
    same_frame_runtime["display_frame_id"] = 14
    same_frame_runtime["tracking_summary"] = {
        "detected_market": "AUD/CAD OTC",
        "detected_timeframe": "M5",
    }
    pending = refresh_operator_streaming_read_v3(
        base,
        same_frame_runtime,
        now_epoch=101.0,
    )
    pending_entry = cast(
        dict[str, object],
        cast(dict[str, object], pending["three_questions"])["entry_now"],
    )
    pending_forecast = cast(
        dict[str, object], pending_entry["timing_forecast"]
    )
    pending_action = cast(dict[str, object], pending_entry["operator_action"])

    assert pending["market"] == {"symbol": "EUR/USD", "timeframe": "M5"}
    assert pending["permission"]["allowed"] is False
    assert pending_entry["identity_rebind_pending"] is True
    assert cast(dict[str, object], pending_forecast["scope"])["symbol"] == (
        "EUR/USD"
    )
    assert pending_forecast["headline"] == prior_forecast["headline"]
    assert pending_action["state"] == "AVOID"

    next_pair_payload = _countertrend_study_payload()
    _retarget_m5_study_payload(
        next_pair_payload,
        symbol="AUD/CAD OTC",
        closed_candle_key="key-audcad-m5-next-frame",
        side="BUY",
    )
    next_pair_tracking = _mutable_mapping(next_pair_payload["tracking_summary"])
    next_frame_runtime = _cpu_stream_runtime_payload(
        now=102.0,
        frame_seq=83,
        state="motion",
    )
    next_frame_runtime["display_frame_id"] = 15
    next_frame_runtime["tracking_summary"] = {
        "detected_market": "AUD/CAD OTC",
        "detected_timeframe": "M5",
        "market_study_v3": next_pair_tracking["market_study_v3"],
    }
    refreshed = refresh_operator_streaming_read_v3(
        pending,
        next_frame_runtime,
        now_epoch=102.0,
    )
    entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )
    forecast = cast(dict[str, object], entry["timing_forecast"])
    action = cast(dict[str, object], entry["operator_action"])

    assert refreshed["market"] == {"symbol": "AUD/CAD OTC", "timeframe": "M5"}
    assert refreshed["permission"]["allowed"] is False
    assert forecast["side"] == "NEUTRAL"
    assert forecast["status"] == "DIRECTION_UNRESOLVED"
    assert "EUR/USD" not in json.dumps(forecast)
    assert action["state"] == "AVOID"
    assert entry["enter_now"] is False
    assert entry["identity_rebind_pending"] is False


def test_exact_stop_survival_calibration_never_bleeds_into_event_likelihood() -> None:
    payload = _countertrend_study_payload()
    _attach_forward_timing_forecast(
        payload,
        probability=None,
        evidence_confidence=0.0,
        calibration_grade="UNRATED",
        calibrated=False,
        source_tier="EXACT_JPCLF",
        support_count=0,
        sweep_support_count=0,
    )
    study = _mutable_mapping(
        _mutable_mapping(payload["tracking_summary"])["market_study_v3"]
    )
    timing = _mutable_mapping(study["path_clock_liquidity_v3"])
    forward = _mutable_mapping(timing["forward_timing_forecast"])
    stop_survival = _mutable_mapping(forward["stop_survival"])
    stop_survival.update(
        {
            "value": 0.72,
            "source_tier": "EXACT_JPCLF",
            "support_count": 40,
            "exact_wall_clock_proven": True,
            "calibrated": True,
            "stop_distance_mru": 1.1,
            "move_size_mru": 1.8,
        }
    )

    forecast = cast(
        dict[str, object],
        _build_workspace(payload, now_epoch=100.0)["three_questions"][
            "entry_now"
        ]["timing_forecast"],
    )
    technical = cast(dict[str, object], forecast["technical_estimates"])
    public_stop = cast(dict[str, object], technical["stop_survival"])

    assert forecast["estimated_likelihood"] is None
    assert forecast["evidence_confidence"] is None
    assert forecast["calibration_grade"] == "UNRATED"
    assert forecast["calibrated"] is False
    assert forecast["event_likelihood_support_count"] == 0
    assert "UNRATED" in str(forecast["calibration_label"])
    assert public_stop["value"] == 0.72
    assert public_stop["calibrated"] is True


def test_model_horizon_never_overrides_proven_sub_15_minute_broker_expiry() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    broker_expiry = _mutable_mapping(command["broker_expiry_contract_v3"])
    broker_expiry["expiry_seconds"] = 600
    _attach_forward_timing_forecast(payload)
    study = _mutable_mapping(
        _mutable_mapping(payload["tracking_summary"])["market_study_v3"]
    )
    timing = _mutable_mapping(study["path_clock_liquidity_v3"])
    forward = _mutable_mapping(timing["forward_timing_forecast"])
    forward["forecast_horizon_seconds"] = 3_000

    workspace = _build_workspace(payload, now_epoch=100.0)
    entry = workspace["three_questions"]["entry_now"]
    forecast = cast(dict[str, object], entry["timing_forecast"])
    action = cast(dict[str, object], entry["operator_action"])
    proof = cast(dict[str, object], entry["broker_expiry_v3"])

    assert workspace["permission"]["allowed"] is True
    assert forecast["forecast_horizon_seconds"] == 3_000
    assert forecast["forecast_horizon_source"] == "MODEL_STUDY_HORIZON"
    assert forecast["broker_expiry_seconds"] is None
    assert proof["status"] == "VERIFIED_INELIGIBLE"
    assert proof["expiry_seconds"] == 600
    assert proof["model_horizon_is_broker_expiry"] is False
    assert entry["entry_permission_authorized"] is False
    assert entry["enter_now"] is False
    assert entry["timing_state"] == "DURATION_INELIGIBLE"
    assert action["state"] == "AVOID"
    assert "requires at least 15 minutes" in str(action["instruction"])


def test_unknown_broker_expiry_keeps_forecast_but_action_requires_verification() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command.pop("broker_expiry_contract_v3")
    _attach_forward_timing_forecast(payload)
    study = _mutable_mapping(
        _mutable_mapping(payload["tracking_summary"])["market_study_v3"]
    )
    timing = _mutable_mapping(study["path_clock_liquidity_v3"])
    _mutable_mapping(timing["forward_timing_forecast"])[
        "forecast_horizon_seconds"
    ] = 3_000

    workspace = _build_workspace(payload, now_epoch=100.0)
    entry = workspace["three_questions"]["entry_now"]
    forecast = cast(dict[str, object], entry["timing_forecast"])
    action = cast(dict[str, object], entry["operator_action"])
    proof = cast(dict[str, object], entry["broker_expiry_v3"])

    assert workspace["permission"]["allowed"] is True
    assert forecast["status"] == "FORECAST_AVAILABLE"
    assert forecast["forecast_horizon_seconds"] == 3_000
    assert proof["status"] == "UNVERIFIED"
    assert proof["expiry_seconds"] is None
    assert entry["entry_permission_authorized"] is False
    assert entry["enter_now"] is False
    assert entry["timing_state"] == "EXPIRY_UNVERIFIED"
    assert action["state"] == "PREPARE"
    assert "SET/VERIFY EXPIRY ≥15 MIN" in str(action["instruction"])
    assert "Broker expiry unverified" in str(action["instruction"])

    refreshed = refresh_operator_streaming_read_v3(
        workspace,
        _cpu_stream_runtime_payload(now=101.0, frame_seq=94, state="motion"),
        now_epoch=101.0,
    )
    refreshed_entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )
    refreshed_action = cast(
        dict[str, object], refreshed_entry["operator_action"]
    )
    assert refreshed_entry["enter_now"] is False
    assert refreshed_action["state"] == "PREPARE"
    assert "SET/VERIFY EXPIRY ≥15 MIN" in str(
        refreshed_action["instruction"]
    )


def test_mature_path_clock_timing_supports_but_never_grants_entry_permission() -> None:
    payload = _countertrend_study_payload()
    _attach_mature_path_clock_timing(
        payload,
        supports_entry=True,
        timing_veto=False,
    )

    workspace = _build_workspace(payload, now_epoch=100.0)
    studied = workspace["three_questions"]["studied_direction_current"]
    entry = workspace["three_questions"]["entry_now"]
    public_study = workspace["tracking"]["market_study_v3"]

    assert "Mature timing history supports studying this SELL" in studied["answer"]
    assert "anything under 15 minutes is excluded" in studied["answer"]
    assert workspace["permission"]["allowed"] is True
    assert entry["permission_allowed"] is True
    assert entry["entry_permission_authorized"] is True
    assert entry["timing_supports_entry"] is True
    assert entry["timing_veto"] is False
    assert entry["enter_now"] is True
    serialized = json.dumps(public_study)
    assert "private-trajectory" not in serialized
    assert "private-neighbour" not in serialized
    assert "private-freeze" not in serialized
    assert "wick_entropy" not in serialized

    no_permission_payload = _countertrend_study_payload()
    no_permission_command = _mutable_mapping(
        no_permission_payload["decision_command_center"]
    )
    no_permission_command["execution_packet_present"] = False
    _attach_mature_path_clock_timing(
        no_permission_payload,
        supports_entry=True,
        timing_veto=False,
    )
    no_permission = _build_workspace(
        no_permission_payload,
        now_epoch=100.0,
    )["three_questions"]["entry_now"]
    assert no_permission["entry_permission_authorized"] is False
    assert no_permission["timing_supports_entry"] is True
    assert no_permission["enter_now"] is False
    assert no_permission["action"] == "DO_NOT_ENTER"


def test_mature_path_clock_veto_delays_permission_and_survives_stream_refresh() -> None:
    payload = _countertrend_study_payload()
    _attach_mature_path_clock_timing(
        payload,
        supports_entry=False,
        timing_veto=True,
    )
    workspace = _build_workspace(payload, now_epoch=100.0)
    entry = workspace["three_questions"]["entry_now"]

    assert workspace["permission"]["allowed"] is True
    assert entry["permission_allowed"] is True
    assert entry["entry_permission_authorized"] is True
    assert entry["timing_supports_entry"] is False
    assert entry["timing_veto"] is True
    assert entry["enter_now"] is False
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["decision"] == "DELAY_FOR_TIMING"
    assert entry["timing_state"] == "TIMING_DELAY"

    refreshed = refresh_operator_streaming_read_v3(
        workspace,
        _cpu_stream_runtime_payload(now=101.0, frame_seq=71, state="motion"),
        now_epoch=101.0,
    )
    refreshed_entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )
    assert refreshed_entry["entry_permission_authorized"] is True
    assert refreshed_entry["timing_veto"] is True
    assert refreshed_entry["enter_now"] is False
    assert refreshed_entry["decision"] == "DELAY_FOR_TIMING"
    refreshed_action = cast(
        dict[str, object], refreshed_entry["operator_action"]
    )
    assert refreshed_action["state"] == "WAIT_FOR_PULLBACK"
    assert "At least 15 minutes" in str(refreshed_action["instruction"])


def test_stream_refresh_expires_mature_path_clock_support_fail_closed() -> None:
    payload = _countertrend_study_payload()
    _attach_mature_path_clock_timing(
        payload,
        supports_entry=True,
        timing_veto=False,
        valid_until=100.5,
    )
    workspace = _build_workspace(payload, now_epoch=100.0)
    assert workspace["three_questions"]["entry_now"]["enter_now"] is True

    refreshed = refresh_operator_streaming_read_v3(
        workspace,
        _cpu_stream_runtime_payload(now=101.0, frame_seq=72, state="motion"),
        now_epoch=101.0,
    )
    entry = cast(
        dict[str, object],
        cast(dict[str, object], refreshed["three_questions"])["entry_now"],
    )
    timing = cast(
        dict[str, object],
        cast(dict[str, object], entry["evidence"])["path_clock_liquidity_v3"],
    )

    assert entry["entry_permission_authorized"] is True
    assert entry["timing_supports_entry"] is False
    assert entry["timing_veto"] is False
    assert entry["enter_now"] is True
    assert entry["action"] == "SELL_NOW"
    assert entry["decision"] == "ENTER_SELL"
    assert timing["state"] == "PROVISIONAL"


@pytest.mark.parametrize(
    (
        "contract_duration_seconds",
        "remaining_seconds",
        "closed_candle_key",
        "expected_state",
        "expected_veto",
    ),
    [
        (
            899,
            899,
            "3d40b65dac4324cb7bb8e288",
            "UNDER_15_MINUTES",
            True,
        ),
        (1_800, 1_800, "wrong-closed-candle", "PROVISIONAL", False),
    ],
)
def test_path_clock_timing_only_vetoes_known_under_15_minute_duration(
    contract_duration_seconds: int,
    remaining_seconds: int,
    closed_candle_key: str,
    expected_state: str,
    expected_veto: bool,
) -> None:
    payload = _countertrend_study_payload()
    _attach_mature_path_clock_timing(
        payload,
        supports_entry=True,
        timing_veto=False,
        contract_duration_seconds=contract_duration_seconds,
        remaining_seconds=remaining_seconds,
        closed_candle_key=closed_candle_key,
    )

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]
    timing = cast(
        dict[str, object],
        cast(dict[str, object], entry["evidence"])["path_clock_liquidity_v3"],
    )

    assert entry["entry_permission_authorized"] is True
    assert entry["timing_veto"] is expected_veto
    assert entry["enter_now"] is (not expected_veto)
    assert entry["action"] == (
        "DO_NOT_ENTER" if expected_veto else "SELL_NOW"
    )
    assert timing["state"] == expected_state


def test_unproven_exact_candle_time_keeps_eligible_duration_separate() -> None:
    payload = _countertrend_study_payload()
    tracking = _mutable_mapping(payload["tracking_summary"])
    study = _mutable_mapping(tracking["market_study_v3"])
    study["path_clock_liquidity_v3"] = {
        "schema_version": "PG_PATH_CLOCK_LIQUIDITY_PUBLIC_STUDY_V3",
        "status": "CENSORED_INVALID_TIMING_EVIDENCE",
        "reason": "Exact contiguous timing evidence was not proven.",
        "duration_policy": {
            "minimum_eligible_duration_seconds": 900,
            "maximum_studied_duration_seconds": 7_200,
            "requested_duration_seconds": 3_000,
            "new_entry_eligible": True,
            "status": "ELIGIBLE",
        },
        "timing_read": {
            "status": "INSUFFICIENT_PROVEN_CLOSED_CANDLE_EVIDENCE",
            "state": "INELIGIBLE",
            "contract_duration_seconds": 3_000,
            "remaining_seconds": 3_000,
            "new_entry_eligible": False,
            "timing_supports_entry": False,
            "timing_veto": False,
        },
        "promotion_gate": {
            "status": "INSUFFICIENT_REPLAY_CALIBRATION",
            "passed": False,
            "all_axes_improved": False,
        },
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
    }

    bounded_once = mobile_app._bounded_operator_projection_context(payload)  # pyright: ignore[reportPrivateUsage]
    bounded_twice = mobile_app._bounded_operator_projection_context(bounded_once)  # pyright: ignore[reportPrivateUsage]
    projected_tracking = _mutable_mapping(bounded_twice["tracking_summary"])
    projected_study = _mutable_mapping(projected_tracking["market_study_v3"])
    projected_timing = _mutable_mapping(
        projected_study["path_clock_liquidity_v3"]
    )
    assert projected_timing["duration_policy_status"] == "ELIGIBLE"
    assert projected_timing["duration_policy_eligible"] is True

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]
    timing = cast(
        dict[str, object],
        cast(dict[str, object], entry["evidence"])["path_clock_liquidity_v3"],
    )

    assert timing["minimum_duration_seconds"] == 900
    assert timing["contract_duration_seconds"] == 3_000
    assert timing["duration_policy_valid"] is True
    assert timing["remaining_window_eligible"] is True
    assert timing["timing_evidence_proven"] is False
    assert timing["state"] == "PROVISIONAL"
    assert timing["source_status"] == "CENSORED_INVALID_TIMING_EVIDENCE"
    assert timing["source_timing_status"] == (
        "INSUFFICIENT_PROVEN_CLOSED_CANDLE_EVIDENCE"
    )
    assert timing["timing_veto"] is False
    assert entry["entry_permission_authorized"] is True
    assert entry["enter_now"] is True
    assert entry["action"] == "SELL_NOW"
    assert entry["timing_state"] == "ENTER_NOW"
    assert entry["decision"] == "ENTER_SELL"
    assert "survival probability is invented" in str(timing["reason"])


def test_unaligned_duration_is_not_mislabeled_as_under_15_minutes() -> None:
    payload = _countertrend_study_payload()
    tracking = _mutable_mapping(payload["tracking_summary"])
    study = _mutable_mapping(tracking["market_study_v3"])
    study["path_clock_liquidity_v3"] = {
        "schema_version": "PG_PATH_CLOCK_LIQUIDITY_PUBLIC_STUDY_V3",
        "status": "PENDING",
        "reason": "The duration has no exact closed-candle endpoint.",
        "symbol": "EUR/USD",
        "timeframe": "M5",
        "closed_candle_key": "3d40b65dac4324cb7bb8e288",
        "duration_policy": {
            "minimum_eligible_duration_seconds": 900,
            "maximum_studied_duration_seconds": 7_200,
            "requested_duration_seconds": 1_000,
            "new_entry_eligible": False,
            "status": "NOT_ALIGNED_TO_CLOSED_CANDLE_GRID",
        },
        "timing_read": {
            "status": "PENDING",
            "state": "INELIGIBLE",
            "side": "SELL",
            "eligible": False,
            "contract_admitted": False,
            "contract_duration_seconds": 1_000,
            "remaining_seconds": 1_000,
            "new_entry_eligible": False,
            "timing_supports_entry": False,
            "timing_veto": False,
        },
        "promotion_gate": {
            "status": "INSUFFICIENT_REPLAY_CALIBRATION",
            "passed": False,
            "all_axes_improved": False,
        },
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
    }

    bounded_once = mobile_app._bounded_operator_projection_context(payload)  # pyright: ignore[reportPrivateUsage]
    bounded_twice = mobile_app._bounded_operator_projection_context(bounded_once)  # pyright: ignore[reportPrivateUsage]
    entry = _build_workspace(bounded_twice, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]
    timing = _mutable_mapping(
        _mutable_mapping(entry["evidence"])["path_clock_liquidity_v3"]
    )

    assert timing["contract_duration_seconds"] == 1_000
    assert timing["duration_policy_valid"] is True
    assert timing["remaining_window_eligible"] is True
    assert timing["timing_evidence_proven"] is False
    assert timing["state"] == "PROVISIONAL"
    assert timing["timing_veto"] is False
    assert entry["entry_permission_authorized"] is False
    assert entry["enter_now"] is False
    assert entry["action"] == "DO_NOT_ENTER"


def test_bounded_projection_keeps_only_public_path_clock_summary() -> None:
    payload = _countertrend_study_payload()
    _attach_mature_path_clock_timing(
        payload,
        supports_entry=True,
        timing_veto=False,
    )

    bounded = mobile_app._bounded_operator_projection_context(payload)  # pyright: ignore[reportPrivateUsage]
    tracking = cast(dict[str, object], bounded["tracking_summary"])
    study = cast(dict[str, object], tracking["market_study_v3"])
    timing = cast(dict[str, object], study["path_clock_liquidity_v3"])

    assert timing["minimum_eligible_duration_seconds"] == 900
    assert timing["execution_authority"] is False
    assert timing["can_grant_entry_permission"] is False
    assert cast(dict[str, object], timing["timing_read"])[
        "contract_duration_seconds"
    ] == 1_800
    serialized = json.dumps(bounded)
    assert "private-trajectory" not in serialized
    assert "private-neighbour" not in serialized
    assert "private-freeze" not in serialized
    assert "wick_entropy" not in serialized


def test_countertrend_sniper_can_enter_sell_while_current_leg_remains_buy() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["current_movement"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
        "confidence": 0.83,
    }
    command["pressure_event"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
        "confidence": 0.80,
    }
    promotion = _countertrend_enter_now_promotion()
    _bind_countertrend_command(payload, promotion)
    payload["model_council_result"] = {
        "book_strategy": {
            "countertrend_sniper_promotion_v3": promotion
        }
    }

    workspace = _build_workspace(payload, now_epoch=100.0)
    permission = workspace["permission"]
    entry = workspace["three_questions"]["entry_now"]

    assert workspace["current_move"]["direction"] == "BUY"
    assert permission["allowed"] is True
    assert permission["action"] == "SELL_NOW"
    assert "countertrend sniper sell entry window" in permission["message"]
    assert entry["enter_now"] is True
    assert entry["action"] == "SELL_NOW"
    assert entry["timing_state"] == "ENTER_NOW"
    assert entry["side"] == "SELL"
    assert entry["evidence"]["countertrend_classification"] == "ENTER_NOW"


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("schema_version", "UNKNOWN"),
        ("classification", "FORMING"),
        ("entry_permission_authorized", False),
        ("movement_confirmation_bypass_allowed", False),
        ("execution_packet_present", False),
        ("validated_entry_mode", "NONE"),
        ("active", False),
        ("side", "BUY"),
    ],
)
def test_countertrend_sniper_bypass_fails_closed_when_any_proof_is_missing(
    field: str,
    unsafe_value: object,
) -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["current_movement"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
    }
    command["pressure_event"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
    }
    promotion = _countertrend_enter_now_promotion()
    promotion[field] = unsafe_value
    _bind_countertrend_command(payload, promotion)
    payload["model_council_result"] = {
        "book_strategy": {"countertrend_sniper_promotion_v3": promotion}
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["permission"]["allowed"] is False
    assert workspace["permission"]["action"] == "WAIT"
    assert workspace["three_questions"]["entry_now"]["enter_now"] is False
    assert workspace["three_questions"]["entry_now"]["action"] == "DO_NOT_ENTER"


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("packet_id", "pgpkt-other"),
        ("opportunity_id", "pgepisode-other"),
        ("opportunity_key", "pgopp-other"),
        ("session_id", "other-session"),
        ("symbol", "AUD/JPY"),
        ("timeframe", "M15"),
        ("frame_id", 13),
        ("capture_count", 13),
        ("state_version", 13),
        ("input_frame_hash", "other-frame"),
        ("instrument_identity_hash", "pginst-other"),
        ("trigger_closed_candle_key", "other-candle"),
        ("trigger_frame_id", 13),
        ("integrity_valid", False),
        ("lineage_rejected", True),
    ],
)
def test_countertrend_sniper_promotion_lineage_mismatch_matrix_fails_closed(
    field: str,
    unsafe_value: object,
) -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["current_movement"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
    }
    command["pressure_event"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
    }
    promotion = _countertrend_enter_now_promotion()
    _bind_countertrend_command(payload, promotion)
    projected = _mutable_mapping(command["countertrend_sniper_promotion_v3"])
    projected_lineage = _mutable_mapping(projected["lineage"])
    projected_lineage[field] = unsafe_value

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["permission"]["allowed"] is False
    assert workspace["three_questions"]["entry_now"]["timing_state"] == "INVALIDATED"


def test_old_eurusd_m5_study_and_promotion_cannot_cross_into_audjpy_m15() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["current_movement"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
    }
    promotion = _countertrend_enter_now_promotion()
    _bind_countertrend_command(payload, promotion)
    tracking = _mutable_mapping(payload["tracking_summary"])
    tracking["detected_market"] = "AUD/JPY"
    tracking["detected_timeframe"] = "M15"

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["market"] == {"symbol": "AUD/JPY", "timeframe": "M15"}
    history = workspace["three_questions"]["market_origin_history"]
    assert history["state"] == "MISMATCHED_EVIDENCE"
    assert history["evidence"]["identity_proven"] is False
    assert history["evidence"]["identity_mismatch"] is True
    assert history["evidence"]["history_observation_count"] == 0
    assert workspace["three_questions"]["studied_direction_current"]["state"] == "MISMATCHED_EVIDENCE"
    assert workspace["permission"]["allowed"] is False
    assert workspace["three_questions"]["entry_now"]["timing_state"] == "INVALIDATED"


def test_countertrend_sniper_stale_frame_lineage_is_invalidated() -> None:
    payload = _countertrend_study_payload()
    promotion = _countertrend_enter_now_promotion()
    _bind_countertrend_command(payload, promotion)
    command = _mutable_mapping(payload["decision_command_center"])
    command["current_movement"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 100.0,
        "frame_id": 15,
    }
    payload.update(
        {
            "display_frame_id": 15,
            "capture_count": 15,
            "state_version": 15,
            "input_frame_hash": "frame-eurusd-m5-15",
        }
    )

    workspace = _build_workspace(payload, now_epoch=101.0)

    assert workspace["permission"]["allowed"] is False
    assert workspace["three_questions"]["entry_now"]["timing_state"] == "INVALIDATED"


def test_countertrend_sniper_expired_lineage_is_stale_not_enter_now() -> None:
    payload = _countertrend_study_payload()
    promotion = _countertrend_enter_now_promotion()
    _bind_countertrend_command(payload, promotion)
    command = _mutable_mapping(payload["decision_command_center"])
    command_lineage = _mutable_mapping(command["execution_lineage"])
    projected = _mutable_mapping(command["countertrend_sniper_promotion_v3"])
    projected_lineage = _mutable_mapping(projected["lineage"])
    command_lineage["valid_until_epoch"] = 99.0
    projected_lineage["valid_until_epoch"] = 99.0
    command["current_movement"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
    }

    workspace = _build_workspace(payload, now_epoch=100.0)

    assert workspace["permission"]["allowed"] is False
    assert workspace["three_questions"]["entry_now"]["timing_state"] == "STALE"


def test_three_questions_call_an_expired_studied_move_missed_instead_of_wait() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity["state"] = "EXPIRED"
    opportunity["valid_until_epoch"] = 99.0

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]

    assert entry["enter_now"] is False
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["timing_state"] == "MISSED"
    assert entry["headline"] == "WAIT FOR PULLBACK"
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert operator_action["state"] == "WAIT_FOR_PULLBACK"
    assert "chasing it now is not authorized" in str(
        operator_action["instruction"]
    )
    assert "fresh SELL pullback" in str(operator_action["instruction"])


def test_three_questions_do_not_claim_diagnostic_probe_was_definitively_missed() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    payload["promotion_trace"] = {
        "missed_opportunity": {
            "side": "SELL",
            "setup": "SNIPER_ZONE_ENTRY",
            "future_move_confirmed": None,
        }
    }

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]

    assert entry["enter_now"] is False
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["timing_state"] == "FORMING"
    assert "missed" not in entry["headline"].lower()


def test_three_questions_classify_late_chase_trap_as_missed() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["execution_packet_present"] = False
    command["blocker"] = "LATE_CHASE_TRAP"
    opportunity = _mutable_mapping(command["execution_opportunity_window_v3"])
    opportunity["state"] = "CLOSED"
    opportunity["valid_until_epoch"] = 140.0

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]

    assert entry["timing_state"] == "MISSED"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["headline"] == "WAIT FOR PULLBACK"
    operator_action = cast(dict[str, object], entry["operator_action"])
    assert operator_action["state"] == "WAIT_FOR_PULLBACK"
    assert "chasing" in str(operator_action["instruction"]).lower()


def test_three_questions_recover_countertrend_study_without_command_center() -> None:
    payload = _countertrend_study_payload()
    payload.pop("decision_command_center")
    payload["model_council_result"] = {
        "final_side": "HOLD",
        "book_strategy": {
            "dual_thesis_report_v3": {
                "selected_authority_side": "SELL",
            },
            "countertrend_sniper_promotion_v3": {
                "schema_version": "PG_COUNTERTREND_SNIPER_PROMOTION_V3",
                "active": True,
                "classification": "MISSED_DO_NOT_CHASE",
                "side": "SELL",
                "against_global_side": "BUY",
                "ensemble_basis": {"council_side_score": 0.88},
                "entry_permission_authorized": False,
                "execution_packet_present": False,
            },
        },
    }

    brief = _build_workspace(payload, now_epoch=100.0)["three_questions"]
    study = brief["studied_direction_current"]
    entry = brief["entry_now"]

    assert study["side"] == "SELL"
    assert study["confidence"] == 0.88
    assert study["evidence"]["direction_source"] == "COUNTERTREND_SNIPER"
    assert study["evidence"]["countertrend_classification"] == "MISSED_DO_NOT_CHASE"
    assert "ensemble was studying SELL" in study["answer"]
    assert entry["enter_now"] is False
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["timing_state"] == "MISSED"
    assert entry["side"] == "SELL"


def test_three_questions_distinguish_live_conflict_from_a_forming_entry() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["current_movement"] = {
        "side": "BUY",
        "state": "ACTIVE",
        "observed_at": 99.0,
        "frame_id": 14,
        "confidence": 0.79,
    }

    entry = _build_workspace(payload, now_epoch=100.0)["three_questions"][
        "entry_now"
    ]

    assert entry["timing_state"] == "CONFLICT"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["side"] == "SELL"
    assert entry["evidence"]["current_move_side"] == "BUY"


def test_three_questions_keep_last_completed_study_when_entry_read_is_stale() -> None:
    payload = _countertrend_study_payload()
    command = _mutable_mapping(payload["decision_command_center"])
    command["valid_until_epoch"] = 99.0
    command["fresh"] = False
    command["freshness_status"] = "STALE"

    brief = _build_workspace(payload, now_epoch=100.0)["three_questions"]

    history = brief["market_origin_history"]
    assert history["state"] == "LAST_COMPLETED"
    assert history["side"] == "BUY"
    assert history["headline"].startswith("Last completed study:")
    assert history["evidence"]["identity_proven"] is True
    assert history["evidence"]["symbol"] == "EUR/USD"
    assert history["evidence"]["timeframe"] == "M5"

    study = brief["studied_direction_current"]
    assert study["state"] == "STALE"
    assert study["side"] == "SELL"
    assert study["headline"].startswith("Last completed read:")
    assert "last completed closed-candle regression reads SELL" in study["answer"]

    entry = brief["entry_now"]
    assert entry["timing_state"] == "STALE"
    assert entry["action"] == "DO_NOT_ENTER"
    assert entry["side"] == "SELL"


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
    assert '"execution_authority": true' not in serialized
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
        def cpu_stream_health_v3(
            self,
            requested_session_id: str,
            persisted: Mapping[str, object],
        ) -> dict[str, object]:
            assert requested_session_id == session_id
            assert persisted["session_id"] == session_id
            return {
                "schema_version": "PG_CPU_STREAM_RUNTIME_V3",
                "requested": True,
                "enabled": True,
                "available": True,
                "status": "degraded_snapshot_fallback",
                "actual_fps": 0.0,
                "observed_frames": 0,
                "accepted_events": 0,
                "last_error": "Waiting for the exact broker window.",
                "keyframe_slot_capacity": 1,
                "broker_click_authority": False,
            }

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
    initial_stream = operator_response.json()["tracking"]["stream"]
    assert initial_stream["enabled"] is True
    assert initial_stream["state"] == "DEGRADED"
    assert initial_stream["acquisition_fps"] == 0.0
    assert initial_stream["observed_frames"] == 0
    assert initial_stream["accepted_keyframes"] == 0
    assert initial_stream["dropped_frames"] == 0
    assert initial_stream["duplicate_frames"] == 0
    assert initial_stream["last_frame_epoch"] is None
    assert initial_stream["last_keyframe_epoch"] is None
    assert initial_stream["heartbeat_epoch"] is None
    assert initial_stream["fresh"] is False
    assert initial_stream["last_reason"] == "Waiting for the exact broker window."
    assert initial_stream["stream_generation"] == 0
    assert initial_stream["market_read"]["state"] == "STARTING"
    assert initial_stream["market_read"]["execution_authority"] is False
    assert same_frame_response.json()["tracking"]["stream"]["state"] == "DEGRADED"
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
    tracking_summary["detected_market"] = "CAD/JPY OTC"
    tracking_summary["detected_timeframe"] = "M5"
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
        {"provider_status", "frame_timing_trace_v3", "source_path"}
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
