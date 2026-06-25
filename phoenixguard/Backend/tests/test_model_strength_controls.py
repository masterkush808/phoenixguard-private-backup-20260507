from __future__ import annotations
from pathlib import Path

from typing import Mapping, cast

from phoenixguard.mobile_api.model_strength import (
    model_strength_settings_to_execution_controls,
    sanitize_model_strength_settings,
)
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService


def test_model_strength_settings_to_execution_controls_clamps_and_maps() -> None:
    settings = sanitize_model_strength_settings(
        {
            "modelConfidenceFloor": 1.4,
            "executionThreshold": 0.63,
            "overlayConfidenceFloor": -0.2,
            "aiStrengths": {"lstm_sequence": 1.7, "scenario_engine": 3.0},
            "laneThresholds": {"SNIPER_ZONE_ENTRY": 0.58, "FAILED_RETEST_ENTRY": -1},
            "timingControls": {"high_frequency_expiry_seconds": 180, "adaptive_timer_enabled": False},
            "councilControls": {"min_dominance_margin": 0.24, "packet_valid_for_seconds": 75},
            "opposingForceControls": {"max_opposing_force_reaction_distance": 0.18},
            "runtimeControls": {"risk_min_pct": 1.1, "risk_max_pct": 2.8},
        },
        profile_saved=True,
    )

    controls = model_strength_settings_to_execution_controls(settings)
    ai_strengths = cast(Mapping[str, object], controls["ai_contribution_strengths"])
    lane_thresholds = cast(Mapping[str, object], controls["execution_lane_thresholds"])
    profile = cast(Mapping[str, object], controls["model_strength_profile"])

    assert controls["model_confidence_floor"] == 1.0
    assert controls["high_frequency_min_confidence"] == 1.0
    assert controls["execution_threshold"] == 0.63
    assert controls["overlay_min_confidence"] == 0.0
    assert ai_strengths["lstm_sequence"] == 1.7
    assert ai_strengths["scenario_engine"] == 2.0
    assert lane_thresholds["SNIPER_ZONE_ENTRY"] == 0.58
    assert lane_thresholds["FAILED_RETEST_ENTRY"] == 0.0
    assert controls["high_frequency_expiry_seconds"] == 180.0
    assert controls["adaptive_timer_enabled"] is False
    assert controls["min_dominance_margin"] == 0.24
    assert controls["packet_valid_for_seconds"] == 75.0
    assert controls["max_opposing_force_reaction_distance"] == 0.18
    assert controls["risk_min_pct"] == 1.1
    assert controls["risk_max_pct"] == 2.8
    assert profile["profile_saved"] is True


def test_window_tracker_persists_model_strength_controls(tmp_path: Path) -> None:
    service = ContinuousWindowTrackerService(root_dir=tmp_path)
    try:
        service.create_session(session_id="model-strength-test")
        updated = service.update_session_controls(
            "model-strength-test",
            model_confidence_floor=0.31,
            high_frequency_min_confidence=0.31,
            execution_threshold=0.63,
            overlay_min_confidence=0.27,
            ai_contribution_strengths={"lstm_sequence": 1.7},
            execution_lane_thresholds={"SNIPER_ZONE_ENTRY": 0.58},
            model_strength_profile={"schema_version": 1, "execution_threshold": 0.63},
            high_frequency_expiry_seconds=180,
            high_frequency_horizon_candles=4,
            allow_live_momentum_entries=False,
            min_live_momentum_score=0.66,
            min_dominance_margin=0.22,
            packet_valid_for_seconds=90,
            risk_min_pct=1.2,
            risk_max_pct=3.0,
        )

        controls = updated["execution_controls"]
        assert controls["model_confidence_floor"] == 0.31
        assert controls["high_frequency_min_confidence"] == 0.31
        assert controls["execution_threshold"] == 0.63
        assert controls["overlay_min_confidence"] == 0.27
        assert controls["ai_contribution_strengths"]["lstm_sequence"] == 1.7
        assert controls["execution_lane_thresholds"]["SNIPER_ZONE_ENTRY"] == 0.58
        assert controls["model_strength_profile"]["execution_threshold"] == 0.63
        assert controls["high_frequency_expiry_seconds"] == 180
        assert controls["high_frequency_horizon_candles"] == 4
        assert controls["allow_live_momentum_entries"] is False
        assert controls["min_live_momentum_score"] == 0.66
        assert controls["min_dominance_margin"] == 0.22
        assert controls["packet_valid_for_seconds"] == 90.0
        assert controls["risk_min_pct"] == 1.2
        assert controls["risk_max_pct"] == 3.0
    finally:
        service.shutdown()
