from __future__ import annotations

from typing import Any

from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from phoenixguard.runtime.observability_v3 import (
    DEFAULT_REQUIRED_MODEL_ROLES,
    build_model_council_health,
    build_model_council_health_from_session,
    build_runtime_telemetry,
    model_health_allows_executable,
    packet_health_allows_executable,
)


NOW = 2_000.0


def _compute_usage() -> dict[str, Any]:
    return {
        "available": True,
        "pid": 1234,
        "process": {
            "available": True,
            "pid": 1234,
            "cpu_percent": 12.5,
            "memory_rss_mb": 256.0,
        },
        "system": {
            "available": True,
            "cpu_percent": 37.0,
            "memory_used_mb": 4096.0,
            "memory_total_mb": 8192.0,
            "memory_percent": 50.0,
        },
        "gpu": {"available": False, "devices": []},
        "cpu_percent": 12.5,
        "ram_mb": 256.0,
    }


def _packet(runtime_model_health: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_execution_packet_v3(
        packet_id="pgpkt-telemetry",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=20,
        capture_count=21,
        state_version=120,
        side="BUY",
        expiry_seconds=300,
        input_frame_hash="frame-telemetry",
        created_epoch=NOW - 0.8,
        valid_until_epoch=NOW + 1.2,
        live_integrity={"packet_age_ms": 800, "cache_status": "fresh", "source": "model_council"},
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health=runtime_model_health
        or {"all_required_models_awake": True, "council_status": "AWAKE"},
    )


def test_runtime_telemetry_reports_compute_packet_cache_paper_and_path_quality() -> None:
    health = {
        "all_required_models_awake": True,
        "council_status": "AWAKE",
        "models": [
            {
                "role": "global_structure",
                "status": "AWAKE",
                "last_heartbeat_epoch": NOW - 1.0,
                "latency_ms": 44.0,
                "queue_depth": 3,
                "pid": 1234,
            }
        ],
        "max_model_latency_ms": 44.0,
        "queue_depth": 3,
    }
    session = {
        "session_id": "pocket-live-8788",
        "capture_count": 21,
        "dropped_frames": 2,
        "stale_frames": 1,
        "cache_metrics": {"hits": 7, "misses": 2, "rejects": 4, "entries": 5},
        "paper_metrics": {"total": 3, "would_click": 2, "actual_clicked": 0, "wins": 1},
        "broker_execution_state": {"last_result": {"status": "lost", "outcome": "loss", "timing_grade": "held_too_long"}},
        "model_council_packet": _packet(health),
        "latest_signal": {
            "runtime_model_health": health,
            "block_reason": "LATE_CHASE_STEEP_IMPULSE",
            "confidence": 0.89,
            "angle_context": {"angle_class": "STEEP_IMPULSE", "late_chase_risk": True},
            "decision_kernel": {
                "p_target_before_invalidation": 0.82,
                "next_most_likely_event": "trigger",
                "eta_trigger_candles": 2,
            },
        },
    }

    telemetry = build_runtime_telemetry(
        session,
        health=health,
        now_epoch=NOW,
        compute_usage=_compute_usage(),
        include_process_snapshot=False,
    )

    assert telemetry["compute"]["process"]["cpu_percent"] == 12.5
    assert telemetry["queue"]["depth"] == 3
    assert telemetry["packet"]["age_sec"] == 0.8
    assert telemetry["frames"]["dropped"] == 2
    assert telemetry["frames"]["stale"] == 1
    assert telemetry["cache"]["rejects"] == 4
    assert telemetry["paper"]["losses"] == 1
    assert telemetry["path_quality"]["label"] == "HIGH"
    assert telemetry["no_trade_value"]["late_chase_avoided"] == 1
    assert telemetry["confidence_calibration"]["calibrated_confidence"] < telemetry["confidence_calibration"]["raw_confidence"]
    assert telemetry["model_role_reliability"]["global_structure"]["current_regime_reliability"] > 0.0


def test_health_from_session_returns_all_roles_and_telemetry_aliases() -> None:
    session = {
        "session_id": "pocket-live-8788",
        "cache_metrics": {"rejects": 1},
        "latest_signal": {
            "runtime_model_health": {
                "all_required_models_awake": True,
                "council_status": "AWAKE",
                "max_model_latency_ms": 18.0,
                "queue_depth": 2,
            },
            "execution_packet": _packet(),
        },
    }

    health = build_model_council_health_from_session(
        session,
        now_epoch=NOW,
        compute_usage=_compute_usage(),
        include_process_snapshot=False,
    )

    assert [model["role"] for model in health["models"]] == list(DEFAULT_REQUIRED_MODEL_ROLES)
    assert health["all_required_models_awake"] is True
    assert health["queue_depth"] == 2
    assert health["packet_age_sec"] == 0.8
    assert health["cache_reject_count"] == 1
    assert health["runtime_telemetry"]["compute"]["available"] is True


def test_stale_heartbeat_marks_model_stale_and_blocks_health() -> None:
    health = build_model_council_health(
        session_id="pocket-live-8788",
        heartbeats=[
            {
                "role": "global_structure",
                "status": "AWAKE",
                "last_heartbeat_epoch": NOW - 60.0,
            }
        ],
        required_roles=("global_structure",),
        now_epoch=NOW,
        stale_after_sec=15.0,
        compute_usage=_compute_usage(),
    )

    assert health["models"][0]["status"] == "STALE"
    assert health["all_required_models_awake"] is False
    assert model_health_allows_executable(health) is False


def test_stale_model_row_prevents_executable_packet_even_with_compact_awake_flag() -> None:
    runtime_health = {
        "all_required_models_awake": True,
        "council_status": "AWAKE",
        "models": [
            {
                "role": "global_structure",
                "status": "STALE",
                "last_heartbeat_epoch": NOW - 60.0,
                "required": True,
            }
        ],
    }

    assert packet_health_allows_executable(_packet(runtime_health), now_epoch=NOW) is False
