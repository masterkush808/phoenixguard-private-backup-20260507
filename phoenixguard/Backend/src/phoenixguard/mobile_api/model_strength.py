from __future__ import annotations

import json
from typing import Any, Mapping, cast

from phoenixguard.decision.model_council_v3 import (
    DEFAULT_AI_CONTRIBUTION_STRENGTHS,
    DEFAULT_EXECUTION_LANE_THRESHOLDS,
)
from phoenixguard.paths import FRONTEND_ROOT


MODEL_STRENGTH_SCHEMA_VERSION = 2
MODEL_STRENGTH_SETTINGS_PATH = (
    FRONTEND_ROOT / "dashboard" / "static" / "floating_windows" / "model_strength_settings.json"
)
DEFAULT_MODEL_CONFIDENCE_FLOOR = 0.44
DEFAULT_EXECUTION_THRESHOLD = 0.70
DEFAULT_OVERLAY_CONFIDENCE_FLOOR = 0.0


def _as_mapping(value: object) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


MODEL_STRENGTH_NUMBER_GROUPS: dict[str, dict[str, tuple[float, float, float]]] = {
    "timingControls": {
        "high_frequency_entry_grace_sec": (45.0, 0.0, 180.0),
        "high_frequency_expiry_seconds": (600.0, 60.0, 3600.0),
        "high_frequency_horizon_candles": (2.0, 1.0, 12.0),
        "min_capture_interval_sec": (0.5, 0.5, 10.0),
        "max_capture_interval_sec": (30.0, 0.5, 30.0),
        "phoenix_report_interval_sec": (20.0, 0.0, 300.0),
    },
    "memoryIdentityControls": {
        "broker_surface_cache_sec": (30.0, 2.0, 300.0),
        "min_market_confidence": (0.42, 0.0, 1.0),
        "min_timeframe_confidence": (0.42, 0.0, 1.0),
    },
    "riskControls": {
        "max_executions_per_window": (1.0, 1.0, 20.0),
        "execution_window_sec": (600.0, 60.0, 3600.0),
        "cooldown_sec": (600.0, 5.0, 3600.0),
        "loss_guard_max_consecutive_losses": (2.0, 1.0, 10.0),
        "loss_guard_window_sec": (5400.0, 60.0, 86400.0),
        "loss_guard_pause_sec": (2700.0, 60.0, 86400.0),
    },
    "entryControls": {
        "min_location_sniper_target_candles": (3.0, 1.0, 36.0),
        "min_primary_target_candles": (10.0, 1.0, 72.0),
        "max_primary_target_candles": (36.0, 1.0, 120.0),
        "min_live_momentum_visible_candles": (8.0, 1.0, 64.0),
        "min_live_momentum_score": (0.54, 0.0, 1.0),
        "min_live_momentum_alignment": (3.0, 1.0, 10.0),
    },
    "opposingForceControls": {
        "min_opposing_force_reaction_score": (0.68, 0.0, 1.0),
        "min_opposing_force_reaction_alignment": (3.0, 1.0, 10.0),
        "min_opposing_force_reaction_risk": (0.72, 0.0, 1.0),
        "min_opposing_force_reaction_entry_score": (0.54, 0.0, 1.0),
        "max_opposing_force_reaction_distance": (0.10, 0.0, 1.0),
    },
    "structureControls": {
        "live_max_tracked_candles": (64.0, 8.0, 256.0),
        "support_resistance_max_zones_per_role": (4.0, 2.0, 12.0),
        "support_resistance_max_total_zones": (8.0, 4.0, 24.0),
        "support_resistance_max_significant_zones": (8.0, 4.0, 24.0),
        "smart_money_max_liquidity_pools": (8.0, 4.0, 24.0),
    },
    "councilControls": {
        "min_dominance_margin": (0.18, 0.0, 1.0),
        "flip_flop_release_stable_reads": (2.0, 1.0, 10.0),
        "flip_flop_release_candidate_flips": (2.0, 0.0, 10.0),
        "reversal_capture_min_dominance": (0.18, 0.0, 1.0),
        "opportunity_capture_stable_reads": (3.0, 1.0, 10.0),
        "opportunity_capture_min_score": (0.90, 0.0, 1.0),
        "packet_valid_for_seconds": (60.0, 1.0, 300.0),
        "study_packet_valid_for_seconds": (300.0, 5.0, 900.0),
    },
    "overlayGenerationControls": {
        "min_conf_global": (0.42, 0.0, 1.0),
        "min_conf_latest": (0.50, 0.0, 1.0),
        "history_depth": (8.0, 1.0, 24.0),
        "label_density": (10.0, 1.0, 30.0),
        "projection_focus": (0.35, 0.0, 1.0),
        "debug_depth": (6.0, 0.0, 24.0),
    },
    "observerControls": {
        "min_actionable_confidence": (0.58, 0.0, 1.0),
        "min_thesis_confidence": (0.46, 0.0, 1.0),
        "signal_cooldown_sec": (8.0, 0.0, 300.0),
        "rl_track_interval_sec": (30.0, 0.05, 300.0),
    },
    "runtimeControls": {
        "consensus_threshold": (0.82, 0.0, 1.0),
        "gates_pass_minimum": (9.0, 1.0, 20.0),
        "conformal_max_interval_pct": (0.40, 0.0, 1.0),
        "risk_min_pct": (0.5, 0.0, 10.0),
        "risk_max_pct": (2.0, 0.0, 10.0),
        "recall_boost_threshold": (0.85, 0.0, 1.0),
        "recall_veto_threshold": (0.87, 0.0, 1.0),
    },
}

MODEL_STRENGTH_BOOL_GROUPS: dict[str, dict[str, bool]] = {
    "timingControls": {
        "adaptive_timer_enabled": True,
    },
    "memoryIdentityControls": {
        "auto_memory_projection": True,
        "require_memory_projection": True,
        "live_momentum_memory_advisory": True,
        "require_market_identity": True,
        "require_timeframe_identity": False,
        "allow_locked_surface_identity_fallback": False,
    },
    "scenarioControls": {
        "scenario_generation_enabled": False,
        "continuous_model_feed_enabled": True,
        "high_frequency_enabled": True,
        "two_candle_execution_allowed": False,
        "swing_fallback_enabled": False,
        "allow_location_sniper_entries": False,
    },
    "riskControls": {
        "loss_guard_enabled": True,
    },
    "entryControls": {
        "allow_live_momentum_entries": True,
    },
    "opposingForceControls": {
        "allow_opposing_force_reactions": True,
    },
    "overlayGenerationControls": {
        "fuse_timeframe_overlays": False,
    },
    "runtimeControls": {
        "use_macro_local_alignment_gate": True,
        "use_opposition_strength_gate": True,
        "use_memory_ambiguity_penalty": True,
    },
}


def _bounded_number(raw: object, fallback: float, minimum: float, maximum: float) -> float:
    if not isinstance(raw, (int, float, str)):
        raw = fallback
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(fallback)
    if value != value:
        value = float(fallback)
    return max(float(minimum), min(float(maximum), value))


def _normalize_float_map(
    raw: object,
    defaults: Mapping[str, float],
    *,
    minimum: float,
    maximum: float,
) -> dict[str, float]:
    source = _as_mapping(raw)
    normalized: dict[str, float] = {}
    for key, fallback in defaults.items():
        normalized[key] = _bounded_number(source.get(key), float(fallback), minimum, maximum)
    return normalized


def _normalize_control_group(payload: Mapping[str, object], group: str) -> dict[str, object]:
    raw = payload.get(group)
    source = _as_mapping(raw)
    normalized: dict[str, object] = {}
    for key, (fallback, minimum, maximum) in MODEL_STRENGTH_NUMBER_GROUPS.get(group, {}).items():
        normalized[key] = _bounded_number(
            source.get(key, payload.get(key)),
            fallback,
            minimum,
            maximum,
        )
    for key, fallback in MODEL_STRENGTH_BOOL_GROUPS.get(group, {}).items():
        raw_value = source.get(key, payload.get(key, fallback))
        normalized[key] = bool(raw_value)
    return normalized


def _flatten_control_groups(settings: Mapping[str, object]) -> dict[str, object]:
    flat: dict[str, object] = {}
    for group in sorted(set(MODEL_STRENGTH_NUMBER_GROUPS) | set(MODEL_STRENGTH_BOOL_GROUPS)):
        raw = settings.get(group)
        if isinstance(raw, Mapping):
            raw_map = cast(Mapping[str, Any], raw)
            for key, value in raw_map.items():
                flat[str(key)] = value
    return flat


def sanitize_model_strength_settings(
    raw: Mapping[str, object] | None,
    *,
    profile_saved: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = dict(raw or {})
    ai_raw = payload.get("aiStrengths")
    if not isinstance(ai_raw, Mapping):
        ai_raw = payload.get("ai_contribution_strengths")
    lane_raw = payload.get("laneThresholds")
    if not isinstance(lane_raw, Mapping):
        lane_raw = payload.get("execution_lane_thresholds") or payload.get("lane_thresholds")
    ai_source: Mapping[str, object]
    if isinstance(ai_raw, Mapping):
        ai_source = cast(Mapping[str, object], ai_raw)
    else:
        ai_source = {}
    lane_source: Mapping[str, object]
    if isinstance(lane_raw, Mapping):
        lane_source = cast(Mapping[str, object], lane_raw)
    else:
        lane_source = {}
    return {
        "schemaVersion": MODEL_STRENGTH_SCHEMA_VERSION,
        "profileSaved": bool(profile_saved or payload.get("profileSaved") is True),
        "panelOpen": bool(payload.get("panelOpen", True)),
        "panelLocked": bool(payload.get("panelLocked", False)),
        "modelConfidenceFloor": _bounded_number(
            payload.get("modelConfidenceFloor", payload.get("model_confidence_floor")),
            DEFAULT_MODEL_CONFIDENCE_FLOOR,
            0.0,
            1.0,
        ),
        "executionThreshold": _bounded_number(
            payload.get("executionThreshold", payload.get("execution_threshold")),
            DEFAULT_EXECUTION_THRESHOLD,
            0.0,
            1.0,
        ),
        "overlayConfidenceFloor": _bounded_number(
            payload.get("overlayConfidenceFloor", payload.get("overlay_min_confidence")),
            DEFAULT_OVERLAY_CONFIDENCE_FLOOR,
            0.0,
            1.0,
        ),
        "aiStrengths": _normalize_float_map(
            ai_source,
            DEFAULT_AI_CONTRIBUTION_STRENGTHS,
            minimum=0.0,
            maximum=2.0,
        ),
        "laneThresholds": _normalize_float_map(
            lane_source,
            DEFAULT_EXECUTION_LANE_THRESHOLDS,
            minimum=0.0,
            maximum=1.0,
        ),
        **{
            group: _normalize_control_group(payload, group)
            for group in sorted(set(MODEL_STRENGTH_NUMBER_GROUPS) | set(MODEL_STRENGTH_BOOL_GROUPS))
        },
    }


def model_strength_settings_to_execution_controls(settings: Mapping[str, object]) -> dict[str, object]:
    normalized = sanitize_model_strength_settings(
        settings,
        profile_saved=bool(settings.get("profileSaved") is True),
    )
    ai_strengths = dict(cast(Mapping[str, float], normalized["aiStrengths"]))
    lane_thresholds = dict(cast(Mapping[str, float], normalized["laneThresholds"]))
    profile: dict[str, object] = {
        "schema_version": MODEL_STRENGTH_SCHEMA_VERSION,
        "profile_saved": bool(normalized["profileSaved"]),
        "model_confidence_floor": normalized["modelConfidenceFloor"],
        "execution_threshold": normalized["executionThreshold"],
        "overlay_min_confidence": normalized["overlayConfidenceFloor"],
        "ai_contribution_strengths": ai_strengths,
        "execution_lane_thresholds": lane_thresholds,
        **_flatten_control_groups(normalized),
    }
    return {
        "model_confidence_floor": normalized["modelConfidenceFloor"],
        "high_frequency_min_confidence": normalized["modelConfidenceFloor"],
        "execution_threshold": normalized["executionThreshold"],
        "overlay_min_confidence": normalized["overlayConfidenceFloor"],
        "ai_contribution_strengths": ai_strengths,
        "execution_lane_thresholds": lane_thresholds,
        "model_strength_profile": profile,
        **_flatten_control_groups(normalized),
    }


def read_model_strength_settings() -> dict[str, object]:
    try:
        raw = json.loads(MODEL_STRENGTH_SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return sanitize_model_strength_settings({}, profile_saved=False)
    except (json.JSONDecodeError, OSError):
        return sanitize_model_strength_settings({}, profile_saved=False)
    return sanitize_model_strength_settings(cast(Mapping[str, object], raw), profile_saved=True)


def write_model_strength_settings(raw: Mapping[str, object]) -> dict[str, object]:
    settings = sanitize_model_strength_settings(raw, profile_saved=True)
    settings["panelOpen"] = False
    MODEL_STRENGTH_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = MODEL_STRENGTH_SETTINGS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(MODEL_STRENGTH_SETTINGS_PATH)
    return settings
