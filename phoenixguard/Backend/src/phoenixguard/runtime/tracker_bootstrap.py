from __future__ import annotations

from typing import Any, Mapping, Sequence, cast

from phoenixguard.core.timing_policy_v3 import (
    MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS,
)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def build_locked_tracker_controls(
    capture_interval_sec: float = 1.0,
    *,
    live_execution_enabled: bool = True,
    execution_mode: str = "live",
    require_market_identity: bool = True,
    require_timeframe_identity: bool = True,
    allow_locked_surface_identity_fallback: bool = False,
    swing_fallback_enabled: bool = False,
    broker_surface_cache_sec: float = 30.0,
    adaptive_timer_enabled: bool = True,
    min_capture_interval_sec: float = 0.5,
    max_capture_interval_sec: float = 1.0,
    max_executions_per_window: int = 1,
    execution_window_sec: float = 10.0 * 60.0,
    cooldown_sec: float = float(MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS),
    loss_guard_enabled: bool = True,
    loss_guard_max_consecutive_losses: int = 2,
    loss_guard_window_sec: float = 90.0 * 60.0,
    loss_guard_pause_sec: float = 45.0 * 60.0,
    phoenix_report_interval_sec: float = 10.0,
) -> dict[str, Any]:
    return {
        "capture_interval_sec": float(capture_interval_sec),
        "live_execution_enabled": bool(live_execution_enabled),
        "execution_mode": str(execution_mode),
        "allow_countertrend_scalp": False,
        "allow_location_sniper_entries": True,
        "trade_profile": "HIGH_FREQUENCY",
        "high_frequency_enabled": True,
        "swing_fallback_enabled": bool(swing_fallback_enabled),
        "continuous_model_feed_enabled": True,
        "high_frequency_timeframe": "M5",
        "high_frequency_horizon_candles": 2,
        "high_frequency_expiry_seconds": MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS,
        "high_frequency_entry_grace_sec": 45.0,
        "high_frequency_min_confidence": 0.44,
        "scenario_generation_enabled": False,
        "require_market_identity": bool(require_market_identity),
        "require_timeframe_identity": bool(require_timeframe_identity),
        "allow_locked_surface_identity_fallback": bool(allow_locked_surface_identity_fallback),
        "broker_surface_cache_sec": max(2.0, float(broker_surface_cache_sec)),
        "adaptive_timer_enabled": bool(adaptive_timer_enabled),
        "min_capture_interval_sec": float(min_capture_interval_sec),
        "max_capture_interval_sec": float(max_capture_interval_sec),
        "max_executions_per_window": int(max_executions_per_window),
        "execution_window_sec": float(execution_window_sec),
        "cooldown_sec": float(cooldown_sec),
        "loss_guard_enabled": bool(loss_guard_enabled),
        "loss_guard_max_consecutive_losses": int(loss_guard_max_consecutive_losses),
        "loss_guard_window_sec": float(loss_guard_window_sec),
        "loss_guard_pause_sec": float(loss_guard_pause_sec),
        "min_location_sniper_target_candles": 3,
        "phoenix_report_interval_sec": float(phoenix_report_interval_sec),
    }


def tracker_focus_is_locked(session_payload: Mapping[str, Any]) -> bool:
    manual_focus = _mapping(session_payload.get("manual_focus_region", {}))
    if bool(manual_focus.get("enabled", False)):
        return len(_sequence(manual_focus.get("normalized_bbox", []))) == 4
    focus_selector = _mapping(session_payload.get("focus_selector", {}))
    if focus_selector:
        return str(focus_selector.get("status", "")).lower() in {"selected", "locked", "ready"}
    return False


def tracker_session_is_running(session_payload: Mapping[str, Any]) -> bool:
    return bool(session_payload.get("tracking_enabled", False)) and str(session_payload.get("status", "")).lower() in {
        "tracking",
        "running",
    }


def tracker_session_runtime_state(
    session_payload: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
    max_capture_staleness_sec: float = 60.0,
    decision_stale_grace_sec: float = 10.0,
) -> dict[str, Any]:
    import time

    now = time.time() if now_epoch is None else float(now_epoch)
    running = tracker_session_is_running(session_payload)
    capture_interval = max(0.1, _float(session_payload.get("capture_interval_sec"), 1.0))
    latest_signal_payload = _mapping(session_payload.get("latest_signal", {}))
    tracking_summary_payload = _mapping(session_payload.get("tracking_summary", {}))
    pipeline_timing_payload = _mapping(
        latest_signal_payload.get("pipeline_timing") or tracking_summary_payload.get("pipeline_timing") or {}
    )
    observed_pipeline_latency = max(
        _float(session_payload.get("pipeline_latency_sec"), 0.0),
        _float(latest_signal_payload.get("pipeline_latency_sec"), 0.0),
        _float(pipeline_timing_payload.get("pipeline_latency_sec"), 0.0),
    )
    observed_freshness_window = max(
        _float(session_payload.get("freshness_window_sec"), 0.0),
        _float(latest_signal_payload.get("freshness_window_sec"), 0.0),
        _float(pipeline_timing_payload.get("freshness_window_sec"), 0.0),
    )
    capture_staleness_limit = min(
        600.0,
        max(
            float(max_capture_staleness_sec),
            capture_interval * 20.0,
            observed_pipeline_latency * 3.0,
            observed_freshness_window * 1.5,
        ),
    )
    decision_grace = min(
        600.0,
        max(float(decision_stale_grace_sec), capture_interval * 4.0, observed_pipeline_latency, observed_freshness_window * 0.25),
    )
    last_capture_epoch = _float(session_payload.get("last_capture_epoch") or session_payload.get("last_capture_started_epoch"), 0.0)
    display_published_epoch = _float(
        session_payload.get("display_published_epoch") or session_payload.get("last_display_published_epoch"),
        0.0,
    )
    display_only_authority = bool(session_payload.get("display_snapshot_only_v3") or session_payload.get("display_fast_path_v3"))
    if display_only_authority and display_published_epoch > last_capture_epoch:
        last_capture_epoch = display_published_epoch
    decision_valid_until_epoch = _float(session_payload.get("decision_valid_until_epoch"), 0.0)
    capture_age_sec = max(0.0, now - last_capture_epoch) if last_capture_epoch > 0.0 else 0.0
    decision_age_sec = max(0.0, now - decision_valid_until_epoch) if decision_valid_until_epoch > 0.0 else 0.0

    if not running:
        return {
            "status": "STOPPED",
            "fresh": False,
            "stale": False,
            "reason": "tracker session is not running",
            "release_condition": "tracking_enabled=true and status=running",
            "last_capture_age_sec": round(capture_age_sec, 3),
            "decision_age_sec": round(decision_age_sec, 3),
        }
    if last_capture_epoch <= 0.0 and decision_valid_until_epoch <= 0.0:
        return {
            "status": "WARMING",
            "fresh": False,
            "stale": False,
            "reason": "tracker session is running but first capture has not published yet",
            "release_condition": "first capture publishes last_capture_epoch and decision_valid_until_epoch",
            "last_capture_age_sec": 0.0,
            "decision_age_sec": 0.0,
        }
    if last_capture_epoch > 0.0 and capture_age_sec > capture_staleness_limit:
        return {
            "status": "STALE",
            "fresh": False,
            "stale": True,
            "reason": "last_capture_epoch is older than the runtime freshness limit",
            "release_condition": "restart tracker worker or publish a fresh capture",
            "last_capture_age_sec": round(capture_age_sec, 3),
            "decision_age_sec": round(decision_age_sec, 3),
        }
    if decision_valid_until_epoch > 0.0 and decision_age_sec > decision_grace:
        return {
            "status": "STALE",
            "fresh": False,
            "stale": True,
            "reason": "decision_valid_until_epoch expired beyond grace",
            "release_condition": "publish a fresh decision before reading packets",
            "last_capture_age_sec": round(capture_age_sec, 3),
            "decision_age_sec": round(decision_age_sec, 3),
        }
    return {
        "status": "FRESH",
        "fresh": True,
        "stale": False,
        "reason": "tracker session is running with fresh capture/decision state",
        "release_condition": "",
        "last_capture_age_sec": round(capture_age_sec, 3),
        "decision_age_sec": round(decision_age_sec, 3),
    }


def tracker_session_is_stale(
    session_payload: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
    max_capture_staleness_sec: float = 60.0,
    decision_stale_grace_sec: float = 10.0,
) -> bool:
    return bool(
        tracker_session_runtime_state(
            session_payload,
            now_epoch=now_epoch,
            max_capture_staleness_sec=max_capture_staleness_sec,
            decision_stale_grace_sec=decision_stale_grace_sec,
        ).get("stale", False)
    )
