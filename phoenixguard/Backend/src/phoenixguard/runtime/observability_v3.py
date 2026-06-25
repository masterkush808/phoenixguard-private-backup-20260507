from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from phoenixguard.runtime.cache_v3 import (
    CACHE_SCHEMA_VERSION,
    EXECUTION_PACKET_SCHEMA_VERSION,
    validate_execution_packet_for_live_execution,
)
from phoenixguard.decision.candle_outcome_tracker import CANDLE_OUTCOME_TRACKER_V1


MODEL_STATUS_VALUES: tuple[str, ...] = (
    "AWAKE",
    "WARMING",
    "IDLE_BUT_LOADED",
    "BUSY",
    "STALE",
    "FAILED",
    "RESTARTING",
    "DISABLED",
)

EXECUTABLE_MODEL_STATUSES = {"AWAKE", "IDLE_BUT_LOADED", "BUSY"}

DEFAULT_REQUIRED_MODEL_ROLES: tuple[str, ...] = (
    "global_structure",
    "local_micro_structure",
    "zone_liquidity",
    "angle_dynamics",
    "historical_pattern",
    "risk_opposing_force",
    "arbitration_synthesis",
)

BAD_ENTRY_CLASS_001 = "LATE_CHASE_STEEP_IMPULSE"


def _now_epoch() -> float:
    return float(time.time())


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _upper(value: Any, default: str = "") -> str:
    return _text(value, default).upper()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _sequence_of_text(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in cast(Sequence[Any], value) if str(item or "").strip()]


def _finite_float(value: Any, default: float = 0.0) -> float:
    number = _float(value, default)
    return number if math.isfinite(number) else float(default)


def _nonnegative_float(value: Any, default: float = 0.0) -> float:
    return max(0.0, _finite_float(value, default))


def _nonnegative_int(value: Any, default: int = 0) -> int:
    return max(0, _int(value, default))


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _finite_float(value, default)))


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _sum_int_at_keys(payloads: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> int:
    total = 0
    for payload in payloads:
        for key in keys:
            if key in payload:
                total += _nonnegative_int(payload.get(key), 0)
                break
    return total


def _status(value: Any, fallback: str = "FAILED") -> str:
    status = _upper(value, fallback)
    return status if status in MODEL_STATUS_VALUES else fallback


def _latest_signal_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    latest = _mapping(payload.get("latest_signal", {}))
    return latest if latest else dict(payload)


def _execution_packet_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    latest = _latest_signal_from_payload(payload)
    for candidate in (
        payload,
        latest,
        _mapping(payload.get("execution_packet")),
        _mapping(payload.get("model_council_packet")),
        _mapping(latest.get("execution_packet")),
        _mapping(latest.get("model_council_packet")),
    ):
        if _mapping(candidate).get("schema_version") == EXECUTION_PACKET_SCHEMA_VERSION:
            return dict(candidate)
    return latest if latest else dict(payload)


def _first_mapping_at_paths(payload: Mapping[str, Any], paths: Sequence[Sequence[str]]) -> dict[str, Any]:
    for path in paths:
        current: Any = payload
        for key in path:
            current = _mapping(current).get(key)
        candidate = _mapping(current)
        if candidate:
            return candidate
    return {}


def _health_blob_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    latest = _latest_signal_from_payload(payload)
    return _first_mapping_at_paths(
        {"root": dict(payload), "latest": latest},
        (
            ("root", "runtime_model_health"),
            ("root", "model_council_health"),
            ("root", "model_health"),
            ("root", "model_council_result", "runtime_model_health"),
            ("root", "execution_packet", "runtime_model_health"),
            ("root", "model_council_packet", "runtime_model_health"),
            ("latest", "runtime_model_health"),
            ("latest", "model_council_health"),
            ("latest", "model_health"),
            ("latest", "model_council_result", "runtime_model_health"),
            ("latest", "model_council", "runtime_model_health"),
            ("latest", "execution_packet", "runtime_model_health"),
            ("latest", "model_council_packet", "runtime_model_health"),
        ),
    )


def _packet_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    packet = _execution_packet_from_payload(payload)
    return packet if packet.get("schema_version") == EXECUTION_PACKET_SCHEMA_VERSION else {}


def _candidate_payloads(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    latest = _latest_signal_from_payload(payload)
    tracking = _mapping(payload.get("tracking_summary"))
    packet = _packet_from_payload(payload)
    council_result = _mapping(payload.get("model_council_result") or latest.get("model_council_result"))
    broker_state = _mapping(payload.get("broker_execution_state"))
    return [
        dict(payload),
        latest,
        tracking,
        packet,
        council_result,
        _mapping(packet.get("runtime_model_health")),
        _mapping(council_result.get("runtime_model_health")),
        _mapping(latest.get("runtime_model_health")),
        broker_state,
    ]


def _process_usage_from_psutil(pid: int) -> dict[str, Any]:
    try:
        import psutil  # type: ignore[import-untyped]
    except Exception:
        return {
            "available": False,
            "pid": int(pid),
            "cpu_percent": 0.0,
            "memory_rss_mb": 0.0,
            "memory_percent": 0.0,
            "status": "psutil_unavailable",
        }

    try:
        process = psutil.Process(int(pid))
        memory = process.memory_info()
        return {
            "available": True,
            "pid": int(pid),
            "alive": bool(process.is_running()),
            "status": _text(process.status(), "unknown"),
            "cpu_percent": _nonnegative_float(process.cpu_percent(interval=None), 0.0),
            "memory_rss_mb": round(_nonnegative_float(memory.rss, 0.0) / (1024 * 1024), 3),
            "memory_vms_mb": round(_nonnegative_float(memory.vms, 0.0) / (1024 * 1024), 3),
            "memory_percent": round(_nonnegative_float(process.memory_percent(), 0.0), 3),
            "threads": _nonnegative_int(process.num_threads(), 0),
        }
    except Exception as exc:
        return {
            "available": False,
            "pid": int(pid),
            "cpu_percent": 0.0,
            "memory_rss_mb": 0.0,
            "memory_percent": 0.0,
            "status": "process_unavailable",
            "error": str(exc),
        }


def collect_compute_usage(
    *,
    pid: int | None = None,
    include_gpu: bool = True,
) -> dict[str, Any]:
    """Return best-effort process/system compute telemetry without requiring psutil or CUDA."""

    resolved_pid = int(pid or os.getpid())
    process = _process_usage_from_psutil(resolved_pid)
    system: dict[str, Any] = {
        "available": False,
        "cpu_percent": 0.0,
        "memory_used_mb": 0.0,
        "memory_total_mb": 0.0,
        "memory_percent": 0.0,
    }
    try:
        import psutil  # type: ignore[import-untyped]

        virtual = psutil.virtual_memory()
        system = {
            "available": True,
            "cpu_percent": _nonnegative_float(psutil.cpu_percent(interval=None), 0.0),
            "cpu_count": _nonnegative_int(psutil.cpu_count(logical=True), 0),
            "memory_used_mb": round(_nonnegative_float(virtual.used, 0.0) / (1024 * 1024), 3),
            "memory_total_mb": round(_nonnegative_float(virtual.total, 0.0) / (1024 * 1024), 3),
            "memory_percent": round(_nonnegative_float(virtual.percent, 0.0), 3),
        }
    except Exception:
        pass

    gpu: dict[str, Any] = {"available": False, "devices": []}
    if include_gpu:
        try:
            import torch  # type: ignore[import-untyped]

            torch_module = cast(Any, torch)
            cuda_available = bool(torch_module.cuda.is_available())
            devices: list[dict[str, Any]] = []
            if cuda_available:
                for index in range(int(torch_module.cuda.device_count())):
                    props: Any = torch_module.cuda.get_device_properties(index)
                    allocated = _nonnegative_float(torch_module.cuda.memory_allocated(index), 0.0)
                    reserved = _nonnegative_float(torch_module.cuda.memory_reserved(index), 0.0)
                    total = _nonnegative_float(getattr(props, "total_memory", 0), 0.0)
                    devices.append(
                        {
                            "index": index,
                            "name": _text(getattr(props, "name", ""), f"cuda:{index}"),
                            "memory_allocated_mb": round(allocated / (1024 * 1024), 3),
                            "memory_reserved_mb": round(reserved / (1024 * 1024), 3),
                            "memory_total_mb": round(total / (1024 * 1024), 3),
                            "memory_percent": round((reserved / total) * 100.0, 3) if total > 0.0 else 0.0,
                        }
                    )
            gpu = {"available": cuda_available, "devices": devices}
        except Exception as exc:
            gpu = {"available": False, "devices": [], "error": str(exc)}

    return {
        "available": bool(process.get("available") or system.get("available")),
        "pid": resolved_pid,
        "process": process,
        "system": system,
        "gpu": gpu,
        "cpu_percent": _nonnegative_float(process.get("cpu_percent"), 0.0),
        "ram_mb": _nonnegative_float(process.get("memory_rss_mb"), 0.0),
    }


def _model_process_telemetry(row: Mapping[str, Any], *, include_process_snapshot: bool) -> dict[str, Any]:
    process_payload = _mapping(row.get("process"))
    pid = _nonnegative_int(row.get("pid") or process_payload.get("pid"), 0)
    if process_payload:
        process_payload.setdefault("pid", pid)
        process_payload.setdefault("available", bool(process_payload))
        return process_payload
    explicit: dict[str, Any] = {
        "available": any(
            key in row
            for key in (
                "process_cpu_percent",
                "process_memory_mb",
                "memory_rss_mb",
                "gpu_memory_mb",
            )
        ),
        "pid": pid,
        "cpu_percent": _nonnegative_float(row.get("process_cpu_percent"), 0.0),
        "memory_rss_mb": _nonnegative_float(row.get("process_memory_mb", row.get("memory_rss_mb")), 0.0),
        "gpu_memory_mb": _nonnegative_float(row.get("gpu_memory_mb"), 0.0),
    }
    if explicit["available"] or not include_process_snapshot or pid <= 0:
        return explicit
    return _process_usage_from_psutil(pid)


def _model_row(
    row: Mapping[str, Any],
    *,
    role: str,
    now_epoch: float,
    stale_after_sec: float,
    default_name: str,
    missing_error: str | None = None,
    include_process_snapshot: bool = False,
) -> dict[str, Any]:
    heartbeat_epoch = _float(row.get("last_heartbeat_epoch"), 0.0)
    resolved_status = _status(row.get("status"), "AWAKE" if heartbeat_epoch > 0.0 else "STALE")
    heartbeat_age_sec = max(0.0, now_epoch - heartbeat_epoch) if heartbeat_epoch > 0.0 else None
    if heartbeat_epoch <= 0.0 or (now_epoch - heartbeat_epoch) > float(stale_after_sec):
        resolved_status = "STALE" if resolved_status not in {"FAILED", "DISABLED"} else resolved_status
    return {
        "name": _text(row.get("name"), default_name),
        "role": role,
        "status": resolved_status,
        "device": _text(row.get("device"), "unknown"),
        "pid": _nonnegative_int(row.get("pid"), 0),
        "last_heartbeat_epoch": heartbeat_epoch,
        "heartbeat_age_sec": None if heartbeat_age_sec is None else round(float(heartbeat_age_sec), 3),
        "last_inference_epoch": _float(row.get("last_inference_epoch"), 0.0),
        "latency_ms": _nonnegative_float(row.get("latency_ms"), 0.0),
        "frames_processed": _nonnegative_int(row.get("frames_processed"), 0),
        "queue_depth": _nonnegative_int(row.get("queue_depth"), 0),
        "last_error": row.get("last_error", missing_error),
        "required": row.get("required", True) is not False,
        "process": _model_process_telemetry(row, include_process_snapshot=include_process_snapshot),
    }


def _latency_summary(models: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latencies = [_nonnegative_float(item.get("latency_ms"), 0.0) for item in models]
    nonzero = [value for value in latencies if value > 0.0]
    avg = sum(nonzero) / len(nonzero) if nonzero else 0.0
    return {
        "max_model_latency_ms": max(latencies, default=0.0),
        "avg_model_latency_ms": round(avg, 3),
        "per_model_ms": {
            _text(item.get("role") or item.get("name"), f"model_{index}"): _nonnegative_float(item.get("latency_ms"), 0.0)
            for index, item in enumerate(models)
        },
    }


def _queue_summary(models: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    depths = [_nonnegative_int(item.get("queue_depth"), 0) for item in models]
    return {
        "depth": max(depths, default=0),
        "total_depth": sum(depths),
        "per_model_depth": {
            _text(item.get("role") or item.get("name"), f"model_{index}"): _nonnegative_int(item.get("queue_depth"), 0)
            for index, item in enumerate(models)
        },
    }


def _cache_summary(
    payload: Mapping[str, Any],
    *,
    daemon_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = _candidate_payloads(payload)
    daemon = dict(daemon_status or {})
    cache_objs = [
        _mapping(daemon.get("cache")),
        _mapping(payload.get("cache")),
        _mapping(payload.get("cache_metrics")),
        _mapping(payload.get("cache_stats")),
    ]
    hits = _sum_int_at_keys(candidates, ("cache_hits", "cache_hit_count", "hits"))
    misses = _sum_int_at_keys(candidates, ("cache_misses", "cache_miss_count", "misses"))
    rejects = _sum_int_at_keys(candidates, ("cache_rejects", "cache_reject_count", "rejects", "rejected"))
    for cache_obj in cache_objs:
        hits = max(hits, _nonnegative_int(cache_obj.get("hits", cache_obj.get("cache_hits")), 0))
        misses = max(misses, _nonnegative_int(cache_obj.get("misses", cache_obj.get("cache_misses")), 0))
        rejects = max(rejects, _nonnegative_int(cache_obj.get("rejects", cache_obj.get("cache_rejects")), 0))
    entries = _nonnegative_int(
        _first_present(
            *(value for cache_obj in cache_objs for value in (cache_obj.get("entries"), cache_obj.get("cache_entries"))),
            daemon.get("cache_entries"),
            payload.get("cache_entries"),
        ),
        0,
    )
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "rejects": rejects,
        "entries": entries,
        "hit_rate": round(float(hits) / float(total), 4) if total > 0 else 0.0,
        "last_reject_reason": _text(
            _first_present(
                *(cache_obj.get("last_reject_reason") for cache_obj in cache_objs),
                payload.get("cache_reject_reason"),
                daemon.get("last_cache_reject_reason"),
            )
        ),
    }


def _frame_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidates = _candidate_payloads(payload)
    dropped = _sum_int_at_keys(
        candidates,
        ("dropped_frames", "dropped_frame_count", "frames_dropped", "drop_count"),
    )
    stale = _sum_int_at_keys(
        candidates,
        ("stale_frames", "stale_frame_count", "frames_stale", "stale_count"),
    )
    processed = _sum_int_at_keys(candidates, ("frames_processed", "processed_frames"))
    latest = _latest_signal_from_payload(payload)
    return {
        "dropped": dropped,
        "stale": stale,
        "processed": processed,
        "frame_id": _nonnegative_int(_first_present(payload.get("frame_id"), latest.get("frame_id")), 0),
        "capture_count": _nonnegative_int(payload.get("capture_count"), 0),
        "stale_signal": bool(latest.get("stale", False)),
    }


def _packet_summary(payload: Mapping[str, Any], *, now_epoch: float) -> dict[str, Any]:
    packet = _packet_from_payload(payload)
    latest = _latest_signal_from_payload(payload)
    live_integrity = _mapping(packet.get("live_integrity") or latest.get("live_integrity"))
    created_epoch = _float(_first_present(packet.get("created_epoch"), latest.get("published_epoch"), payload.get("last_capture_epoch")), 0.0)
    valid_until_epoch = _float(_first_present(packet.get("valid_until_epoch"), latest.get("valid_until_epoch"), payload.get("decision_valid_until_epoch")), 0.0)
    embedded_age_ms = _nonnegative_float(live_integrity.get("packet_age_ms"), 0.0)
    if created_epoch > 0.0:
        computed_age_ms = max(0.0, (now_epoch - created_epoch) * 1000.0)
        age_ms = embedded_age_ms if embedded_age_ms > 0.0 and computed_age_ms > 86_400_000.0 else computed_age_ms
    else:
        age_ms = embedded_age_ms
    age_sec = age_ms / 1000.0 if age_ms > 0.0 else 0.0
    return {
        "packet_id": _text(packet.get("packet_id")),
        "schema_version": _text(packet.get("schema_version")),
        "age_ms": round(age_ms, 3),
        "age_sec": round(age_sec, 3),
        "created_epoch": created_epoch,
        "valid_until_epoch": valid_until_epoch,
        "stale": bool((valid_until_epoch > 0.0 and valid_until_epoch <= now_epoch) or latest.get("stale", False)),
        "cache_status": _text(live_integrity.get("cache_status"), "unknown"),
        "source": _text(live_integrity.get("source"), "unknown"),
    }


def _shooter_handshake_summary(payload: Mapping[str, Any], *, packet: Mapping[str, Any], now_epoch: float) -> dict[str, Any]:
    latest = _latest_signal_from_payload(payload)
    execution_state = _mapping(payload.get("broker_execution_state"))
    explicit = (
        _mapping(payload.get("shooter_handshake"))
        or _mapping(payload.get("shooter_status"))
        or _mapping(latest.get("shooter_handshake"))
        or _mapping(execution_state.get("shooter_handshake"))
    )
    status = _text(
        explicit.get("status")
        or execution_state.get("external_shooter_status")
        or execution_state.get("shooter_status")
        or ("waiting_for_shooter" if packet else "not_ready"),
        "not_ready",
    )
    last_seen_epoch = _float(
        _first_present(
            explicit.get("last_seen_epoch"),
            explicit.get("updated_epoch"),
            explicit.get("heartbeat_epoch"),
            execution_state.get("shooter_last_seen_epoch"),
        ),
        0.0,
    )
    age_sec = max(0.0, now_epoch - last_seen_epoch) if last_seen_epoch > 0.0 else 0.0
    ready_states = {"ready", "connected", "acknowledged", "handshake_ok", "alive", "online"}
    return {
        "status": status,
        "ready": _text(status).lower() in ready_states or explicit.get("ready") is True,
        "required": bool(packet),
        "last_seen_epoch": last_seen_epoch,
        "age_sec": round(age_sec, 3),
        "message": _text(explicit.get("message") or execution_state.get("message")),
    }


def _paper_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    explicit = (
        _mapping(payload.get("paper"))
        or _mapping(payload.get("paper_metrics"))
        or _mapping(payload.get("paper_outcome_metrics"))
    )
    execution_state = _mapping(payload.get("broker_execution_state"))
    last_result = _mapping(execution_state.get("last_result") or payload.get("last_result"))
    status_text = _text(last_result.get("status") or last_result.get("outcome")).lower()
    wins = _nonnegative_int(explicit.get("wins", explicit.get("won")), 0)
    losses = _nonnegative_int(explicit.get("losses", explicit.get("lost")), 0)
    flats = _nonnegative_int(explicit.get("flats", explicit.get("flat")), 0)
    expired = _nonnegative_int(explicit.get("expired_unverified"), 0)
    if status_text in {"won", "win"}:
        wins = max(1, wins)
    elif status_text in {"lost", "loss"}:
        losses = max(1, losses)
    elif status_text == "flat":
        flats = max(1, flats)
    elif status_text == "expired_unverified":
        expired = max(1, expired)
    total = _nonnegative_int(explicit.get("total"), 0)
    total = max(total, wins + losses + flats + expired)
    return {
        "total": total,
        "would_click": _nonnegative_int(explicit.get("would_click", explicit.get("would_click_count")), 0),
        "actual_clicked": _nonnegative_int(explicit.get("actual_clicked", explicit.get("actual_click_count")), 0),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "expired_unverified": expired,
        "last_status": _text(last_result.get("status") or explicit.get("last_status"), "none"),
        "last_outcome": _text(last_result.get("outcome") or explicit.get("last_outcome"), "none"),
        "last_timing_grade": _text(last_result.get("timing_grade") or explicit.get("last_timing_grade"), "none"),
    }


def _path_quality_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    latest = _latest_signal_from_payload(payload)
    tracking = _mapping(payload.get("tracking_summary"))
    kernel = _mapping(latest.get("decision_kernel") or tracking.get("decision_kernel"))
    report = _mapping(tracking.get("phoenixguard_report") or latest.get("phoenixguard_report"))
    forward = _mapping(report.get("forward_projection") or latest.get("forward_projection"))
    timing = _mapping(report.get("timing_judgment") or latest.get("timing_judgment"))
    memory_projection = (
        _mapping(payload.get("memory_projection_current"))
        or _mapping(payload.get("memory_projection_future"))
        or _mapping(payload.get("memory_projection_predict"))
    )
    score = _clip01(
        _first_present(
            payload.get("path_quality_score"),
            latest.get("path_quality_score"),
            kernel.get("path_quality_score"),
            kernel.get("p_target_before_invalidation"),
            memory_projection.get("memory_precision_score"),
            latest.get("effective_confidence"),
        ),
        0.0,
    )
    if score >= 0.75:
        label = "HIGH"
    elif score >= 0.5:
        label = "MEDIUM"
    elif score > 0.0:
        label = "LOW"
    else:
        label = "UNKNOWN"
    return {
        "score": round(score, 4),
        "label": _text(_first_present(payload.get("path_quality_label"), latest.get("path_quality_label")), label).upper(),
        "next_event": _text(kernel.get("next_most_likely_event"), "unknown"),
        "trigger_eta_candles": _nonnegative_float(kernel.get("eta_trigger_candles"), 0.0),
        "target_eta_candles": _nonnegative_float(kernel.get("eta_target_after_trigger_candles"), 0.0),
        "stale_after_candles": _nonnegative_float(kernel.get("stale_after_candles"), 0.0),
        "likely_path": _text(forward.get("likely_path") or report.get("headline") or latest.get("summary")),
        "trigger_area": _text(forward.get("likely_trigger_area")),
        "invalidation_area": _text(forward.get("likely_invalidation_area")),
        "entry_quality": _text(
            _first_present(
                memory_projection.get("entry_quality"),
                latest.get("entry_quality"),
                timing.get("entry_quality"),
            ),
            "UNKNOWN",
        ).upper(),
    }


def _no_trade_value_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    signal = _latest_signal_from_payload(payload)
    counters = _mapping(payload.get("no_trade_metrics") or signal.get("no_trade_metrics") or signal.get("avoided_loss_metrics"))
    block_reason = _upper(signal.get("block_reason") or payload.get("block_reason"))
    market_context = _mapping(signal.get("market_context") or payload.get("market_context"))
    angle_context = _mapping(signal.get("angle_context") or payload.get("angle_context"))
    late_chase = _nonnegative_int(counters.get("late_chase_avoided"), 0)
    opposing = _nonnegative_int(counters.get("opposing_force_avoided"), 0)
    conflict = _nonnegative_int(counters.get("flip_flop_conflict_avoided"), 0)
    stale = _nonnegative_int(counters.get("stale_packet_avoided"), 0)
    time_mismatch = _nonnegative_int(counters.get("time_mismatch_avoided"), 0)
    if block_reason in {"LATE_CHASE_STEEP_IMPULSE", "LATE_CHASE_AFTER_IMPULSE"} or bool(angle_context.get("late_chase_risk")):
        late_chase += 1
    if market_context.get("opposing_force_distance_ok") is False or block_reason == "OPPOSING_FORCE_TOO_CLOSE":
        opposing += 1
    if block_reason in {"FLIP_FLOP_CONTAINED", "BUY_AND_SELL_EXECUTABLE_CONFLICT", "CONFLICT_MARKET"}:
        conflict += 1
    if "STALE" in block_reason or "EXPIRED" in block_reason:
        stale += 1
    if "TIME" in block_reason and "MISMATCH" in block_reason:
        time_mismatch += 1
    total = late_chase + opposing + conflict + stale + time_mismatch
    return {
        "bad_trades_avoided": total,
        "late_chase_avoided": late_chase,
        "opposing_force_avoided": opposing,
        "flip_flop_conflict_avoided": conflict,
        "stale_packet_avoided": stale,
        "time_mismatch_avoided": time_mismatch,
    }


def _confidence_calibration_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    signal = _latest_signal_from_payload(payload)
    calibration = _mapping(signal.get("confidence_calibration") or payload.get("confidence_calibration"))
    raw = _clip01(_first_present(calibration.get("raw_confidence"), signal.get("confidence"), signal.get("effective_confidence")), 0.0)
    angle_context = _mapping(signal.get("angle_context") or payload.get("angle_context"))
    regime = _upper(signal.get("regime") or _mapping(signal.get("market_reality")).get("market_phase") or "UNKNOWN")
    penalty = 0.0
    reason = _text(calibration.get("reason"))
    if bool(angle_context.get("late_chase_risk")) or _upper(angle_context.get("angle_class")) in {"STEEP_IMPULSE", "PARABOLIC_RISK"}:
        penalty = max(penalty, 0.28)
        reason = reason or "Confidence reduced because this regime often overreacts to steep vertical candles."
    calibrated = _clip01(calibration.get("calibrated_confidence"), max(0.0, raw - penalty))
    bucket = _text(calibration.get("reliability_bucket"))
    if not bucket:
        bucket = "LOW_FOR_THIS_REGIME" if calibrated < raw - 0.15 else "NORMAL_FOR_THIS_REGIME"
    return {
        "raw_confidence": round(raw, 4),
        "calibrated_confidence": round(calibrated, 4),
        "reason": reason or "Confidence calibration used current runtime confidence.",
        "reliability_bucket": bucket,
        "regime": regime,
    }


def _model_role_reliability_summary(payload: Mapping[str, Any], models: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    signal = _latest_signal_from_payload(payload)
    supplied = _mapping(signal.get("model_role_reliability") or payload.get("model_role_reliability"))
    reliability: dict[str, Any] = {}
    for row in models:
        role = _text(row.get("role") or row.get("name"))
        if not role:
            continue
        existing = _mapping(supplied.get(role))
        latency = _nonnegative_float(row.get("latency_ms"), 0.0)
        status = _status(row.get("status"), "FAILED")
        current = _clip01(existing.get("current_regime_reliability"), 0.72 if status in EXECUTABLE_MODEL_STATUSES else 0.2)
        if latency > 500:
            current = min(current, 0.55)
        reliability[role] = {
            "overall_accuracy": _clip01(existing.get("overall_accuracy"), current),
            "current_regime_reliability": round(current, 4),
            "status": status,
        }
    return reliability


def build_runtime_telemetry(
    packet_or_session: Mapping[str, Any] | None,
    *,
    health: Mapping[str, Any] | None = None,
    daemon_status: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
    compute_usage: Mapping[str, Any] | None = None,
    include_process_snapshot: bool = True,
) -> dict[str, Any]:
    payload = _mapping(packet_or_session)
    now = _now_epoch() if now_epoch is None else float(now_epoch)
    model_health = _mapping(health or _health_blob_from_payload(payload))
    models = _sequence_of_mappings(model_health.get("models"))
    daemon = dict(daemon_status or model_health.get("daemon_status") or {})
    packet = _packet_from_payload(payload)
    compute = dict(compute_usage) if isinstance(compute_usage, Mapping) else (
        collect_compute_usage(include_gpu=True) if include_process_snapshot else {"available": False}
    )
    latency = _latency_summary(models)
    queue = _queue_summary(models)
    cache = _cache_summary(payload, daemon_status=daemon)
    frames = _frame_summary(payload)
    packet_metrics = _packet_summary(payload, now_epoch=now)
    paper = _paper_summary(payload)
    path_quality = _path_quality_summary(payload)
    no_trade_value = _no_trade_value_summary(payload)
    confidence_calibration = _confidence_calibration_summary(payload)
    model_role_reliability = _model_role_reliability_summary(payload, models)
    return {
        "generated_epoch": now,
        "generated_at": _iso_from_epoch(now),
        "compute": compute,
        "models": {
            "count": len(models),
            "required_awake": bool(model_health.get("all_required_models_awake", False)),
            "processes": [dict(item.get("process", {})) for item in models],
        },
        "latency": latency,
        "queue": queue,
        "frames": frames,
        "cache": cache,
        "packet": packet_metrics,
        "shooter_handshake": _shooter_handshake_summary(payload, packet=packet, now_epoch=now),
        "paper": paper,
        "path_quality": path_quality,
        "no_trade_value": no_trade_value,
        "confidence_calibration": confidence_calibration,
        "model_role_reliability": model_role_reliability,
    }


def _synthesize_awake_heartbeats(
    required_roles: Sequence[str],
    *,
    now_epoch: float,
    health_blob: Mapping[str, Any],
) -> list[dict[str, Any]]:
    max_latency = _float(health_blob.get("max_model_latency_ms"), 0.0)
    queue_depth = _int(health_blob.get("queue_depth"), 0)
    return [
        {
            "name": f"{role}_model",
            "role": role,
            "status": "AWAKE",
            "device": _text(health_blob.get("device"), "unknown"),
            "pid": _int(health_blob.get("pid"), 0),
            "last_heartbeat_epoch": now_epoch,
            "last_inference_epoch": now_epoch,
            "latency_ms": max_latency,
            "frames_processed": _int(health_blob.get("frames_processed"), 0),
            "queue_depth": queue_depth,
            "last_error": None,
        }
        for role in required_roles
    ]


def build_model_council_health(
    *,
    session_id: str = "",
    heartbeats: Sequence[Mapping[str, Any]] | None = None,
    required_roles: Sequence[str] = DEFAULT_REQUIRED_MODEL_ROLES,
    now_epoch: float | None = None,
    stale_after_sec: float = 15.0,
    daemon_status: Mapping[str, Any] | None = None,
    compute_usage: Mapping[str, Any] | None = None,
    include_process_snapshot: bool = False,
) -> dict[str, Any]:
    now = _now_epoch() if now_epoch is None else float(now_epoch)
    required = tuple(str(role) for role in required_roles)
    heartbeat_rows = [dict(item) for item in (heartbeats or [])]
    by_role: dict[str, dict[str, Any]] = {}
    for row in heartbeat_rows:
        role = _text(row.get("role") or row.get("name"))
        if role and role not in by_role:
            by_role[role] = row

    models: list[dict[str, Any]] = []
    all_required_awake = True
    for role in required:
        row = by_role.pop(role, None)
        if row is None:
            all_required_awake = False
            models.append(
                _model_row(
                    {},
                    role=role,
                    now_epoch=now,
                    stale_after_sec=stale_after_sec,
                    default_name=f"{role}_model",
                    missing_error="missing heartbeat",
                    include_process_snapshot=include_process_snapshot,
                )
            )
            continue

        model = _model_row(
            row,
            role=role,
            now_epoch=now,
            stale_after_sec=stale_after_sec,
            default_name=f"{role}_model",
            include_process_snapshot=include_process_snapshot,
        )
        if model["status"] not in EXECUTABLE_MODEL_STATUSES:
            all_required_awake = False
        models.append(model)

    for role, row in sorted(by_role.items()):
        models.append(
            _model_row(
                row,
                role=role,
                now_epoch=now,
                stale_after_sec=stale_after_sec,
                default_name=f"{role}_model",
                include_process_snapshot=include_process_snapshot,
            )
        )

    latency = _latency_summary(models)
    queue = _queue_summary(models)
    daemon = dict(daemon_status or {})
    cv_loaded_models = _sequence_of_text(daemon.get("loaded_models"))
    cv_failed_models = _mapping(daemon.get("failed_models"))
    health: dict[str, Any] = {
        "ok": bool(all_required_awake),
        "session_id": str(session_id or ""),
        "council_status": "AWAKE" if all_required_awake else "STALE",
        "all_required_models_awake": bool(all_required_awake),
        "cv_models_loaded": cv_loaded_models,
        "cv_models_failed": cv_failed_models,
        "all_cv_models_loaded": bool(cv_loaded_models) and not bool(cv_failed_models),
        "required_roles": list(required),
        "models": models,
        "max_model_latency_ms": latency["max_model_latency_ms"],
        "avg_model_latency_ms": latency["avg_model_latency_ms"],
        "queue_depth": queue["depth"],
        "generated_epoch": now,
        "generated_at": _iso_from_epoch(now),
        "daemon_status": daemon,
    }
    telemetry = build_runtime_telemetry(
        {"session_id": session_id, "runtime_model_health": health},
        health=health,
        daemon_status=daemon,
        now_epoch=now,
        compute_usage=compute_usage,
        include_process_snapshot=include_process_snapshot,
    )
    health["runtime_telemetry"] = telemetry
    health["telemetry"] = telemetry
    return health


def build_model_council_health_from_session(
    session_payload: Mapping[str, Any],
    *,
    required_roles: Sequence[str] = DEFAULT_REQUIRED_MODEL_ROLES,
    now_epoch: float | None = None,
    stale_after_sec: float = 15.0,
    daemon_status: Mapping[str, Any] | None = None,
    compute_usage: Mapping[str, Any] | None = None,
    include_process_snapshot: bool = True,
) -> dict[str, Any]:
    now = _now_epoch() if now_epoch is None else float(now_epoch)
    health_blob: dict[str, Any] = _health_blob_from_payload(session_payload)
    if not health_blob and any(
        _mapping(session_payload.get(key))
        for key in ("model_council_result", "model_council_study_packet", "study_packet", "execution_packet")
    ):
        health_blob = {
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "queue_depth": 0,
            "max_model_latency_ms": 0.0,
            "models": [],
            "required_roles": list(required_roles),
        }
    council_status = _upper(health_blob.get("council_status"), "AWAKE")
    compact_health_is_awake = (
        health_blob.get("all_required_models_awake") is True
        and council_status not in {"STALE", "FAILED", "DISABLED"}
    )
    heartbeats = _sequence_of_mappings(
        health_blob.get("models")
        or health_blob.get("heartbeats")
        or health_blob.get("required_models")
        or []
    )
    if compact_health_is_awake:
        existing_roles = {_text(row.get("role") or row.get("name")) for row in heartbeats}
        heartbeats.extend(
            row
            for row in _synthesize_awake_heartbeats(required_roles, now_epoch=now, health_blob=health_blob)
            if _text(row.get("role")) not in existing_roles
    )
    session_id = _text(session_payload.get("session_id"))
    resolved_daemon_status = _mapping(daemon_status or health_blob.get("daemon_status"))
    health = build_model_council_health(
        session_id=session_id,
        heartbeats=heartbeats,
        required_roles=required_roles,
        now_epoch=now,
        stale_after_sec=stale_after_sec,
        daemon_status=resolved_daemon_status,
        compute_usage=compute_usage,
        include_process_snapshot=include_process_snapshot,
    )
    telemetry = build_runtime_telemetry(
        session_payload,
        health=health,
        daemon_status=resolved_daemon_status,
        now_epoch=now,
        compute_usage=compute_usage,
        include_process_snapshot=include_process_snapshot,
    )
    health["runtime_telemetry"] = telemetry
    health["telemetry"] = telemetry
    health["dropped_frames"] = telemetry["frames"]["dropped"]
    health["stale_frames"] = telemetry["frames"]["stale"]
    health["packet_age_sec"] = telemetry["packet"]["age_sec"]
    health["cache_reject_count"] = telemetry["cache"]["rejects"]
    health["paper_outcome"] = telemetry["paper"]
    health["path_quality"] = telemetry["path_quality"]
    return health


def model_health_allows_executable(health: Mapping[str, Any]) -> bool:
    all_required = health.get("all_required_models_awake") is True
    council_status = _upper(health.get("council_status"), "AWAKE" if all_required else "STALE")
    if not bool(all_required and council_status == "AWAKE"):
        return False
    for row in _sequence_of_mappings(health.get("models")):
        if row.get("required", True) is False:
            continue
        if _status(row.get("status"), "FAILED") not in EXECUTABLE_MODEL_STATUSES:
            return False
    return True


def packet_health_allows_executable(
    packet: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
) -> bool:
    if not validate_execution_packet_for_live_execution(packet, now_epoch=now_epoch).ok:
        return False
    return model_health_allows_executable(_mapping(packet.get("runtime_model_health", {})))


def build_intelligence_health(packet_or_session: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = _mapping(packet_or_session)
    signal = _latest_signal_from_payload(payload)
    packet = _execution_packet_from_payload(payload)
    council_result = _mapping(payload.get("model_council_result") or signal.get("model_council_result"))
    model_council = _mapping(packet.get("model_council") or council_result.get("model_council") or signal.get("model_council"))
    market_context = _mapping(packet.get("market_context") or council_result.get("market_context") or signal.get("market_context"))
    angle_context = _mapping(packet.get("angle_context") or council_result.get("angle_context") or signal.get("angle_context"))
    history_context = _mapping(packet.get("history_context") or council_result.get("history_context") or signal.get("history_context"))
    risk_context = _mapping(packet.get("risk_context") or council_result.get("risk_context") or signal.get("risk_context"))
    market_reality = _mapping(packet.get("market_reality") or council_result.get("market_reality") or signal.get("market_reality"))
    entry_quality = _mapping(packet.get("entry_quality") or council_result.get("entry_quality") or signal.get("entry_quality"))
    trade_permission = _mapping(packet.get("trade_permission") or council_result.get("trade_permission") or signal.get("trade_permission"))
    market_trap = _mapping(packet.get("market_trap") or council_result.get("market_trap") or signal.get("market_trap"))
    if not entry_quality:
        entry_quality = _mapping(market_reality.get("entry_quality"))
    if not trade_permission:
        trade_permission = _mapping(market_reality.get("trade_permission") or market_reality.get("permission"))
    if not market_trap:
        market_trap = _mapping(market_reality.get("market_trap"))
    candidate_queue = _mapping(packet.get("trade_candidate_queue") or council_result.get("trade_candidate_queue") or signal.get("trade_candidate_queue"))
    listening_stream = _mapping(packet.get("market_listening_stream") or council_result.get("market_listening_stream") or signal.get("market_listening_stream"))
    execution = _mapping(packet.get("execution") or council_result.get("execution") or signal.get("execution"))
    runtime_health = _mapping(
        packet.get("runtime_model_health")
        or council_result.get("runtime_model_health")
        or signal.get("runtime_model_health")
    )

    final_state = _text(
        model_council.get("final_state")
        or execution.get("state")
        or signal.get("status")
        or "WATCHING",
        "WATCHING",
    ).upper()
    final_side = _upper(model_council.get("final_side") or execution.get("side") or signal.get("side"), "HOLD")
    angle_label = _text(angle_context.get("angle_class"), "UNKNOWN").upper()
    if bool(angle_context.get("late_chase_risk")):
        angle_label = "LATE_CHASE"
    elif bool(angle_context.get("parabolic_risk")):
        angle_label = "PARABOLIC_RISK"

    risk_label = "DISTANCE_OK" if market_context.get("opposing_force_distance_ok") is True else "UNKNOWN"
    if market_context.get("opposing_force_distance_ok") is False:
        risk_label = "OPPOSING_FORCE_CLOSE"
    if risk_context:
        risk_label = _text(risk_context.get("risk_state") or risk_label, risk_label).upper()

    return {
        "ok": True,
        "session_id": _text(payload.get("session_id") or packet.get("session_id") or signal.get("session_id")),
        "all_models_awake": bool(runtime_health.get("all_required_models_awake", False)),
        "global_agent": _upper(market_context.get("global_side"), "UNKNOWN"),
        "local_agent": _upper(market_context.get("local_side"), "UNKNOWN"),
        "zone_agent": _text(market_context.get("current_location"), "UNKNOWN").upper(),
        "angle_agent": angle_label,
        "history_agent": _text(history_context.get("similarity_state"), "UNKNOWN").upper(),
        "risk_agent": risk_label,
        "council_final_state": final_state,
        "council_final_side": final_side,
        "council_disagreement_score": _float(model_council.get("disagreement_score"), 0.0),
        "dominance_margin": _float(model_council.get("dominance_margin"), 0.0),
        "flip_flop_state": _text(model_council.get("flip_flop_state"), "UNKNOWN").upper(),
        "executable_stability_score": _float(
            model_council.get("executable_stability_score"),
            1.0 if final_state == "EXECUTABLE" else 0.0,
        ),
        "market_reality": market_reality,
        "entry_quality": entry_quality,
        "trade_permission": trade_permission,
        "market_trap": market_trap,
        "trade_candidate_queue": candidate_queue,
        "market_listening_stream": listening_stream,
        "council_debate": _mapping(packet.get("council_debate") or council_result.get("council_debate") or signal.get("council_debate")),
        "reason": _text(
            model_council.get("arbitration_reason")
            or model_council.get("reason")
            or packet.get("block_reason")
            or signal.get("summary")
            or "No council reason published."
        ),
    }


def append_forensic_decision_log(
    log_path: Path,
    decision: Mapping[str, Any],
    *,
    packet: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now = _now_epoch() if now_epoch is None else float(now_epoch)
    packet_payload = _mapping(packet)
    row: dict[str, Any] = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "event": "decision_cycle",
        "timestamp_epoch": now,
        "timestamp": _iso_from_epoch(now),
        "packet_id": _text(packet_payload.get("packet_id") or decision.get("packet_id")),
        "session_id": _text(packet_payload.get("session_id") or decision.get("session_id")),
        "decision": dict(decision),
    }
    if packet_payload:
        row["packet_schema_version"] = _text(packet_payload.get("schema_version"))
        row["packet_valid_until_epoch"] = _float(packet_payload.get("valid_until_epoch"), 0.0)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, default=str) + "\n")
    return row


def evaluate_bad_entry_replay(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = _mapping(record)
    signal = _latest_signal_from_payload(payload)
    market_context = _mapping(signal.get("market_context") or payload.get("market_context"))
    angle_context = _mapping(signal.get("angle_context") or payload.get("angle_context"))
    history_context = _mapping(signal.get("history_context") or payload.get("history_context"))
    block_reason = _upper(signal.get("block_reason") or payload.get("block_reason"))
    bad_entry_class = _upper(signal.get("bad_entry_class") or payload.get("bad_entry_class"))

    late_chase = bool(market_context.get("is_late_chase")) or bool(angle_context.get("late_chase_risk"))
    wait_required = bool(angle_context.get("post_impulse_wait_required"))
    history_exit_here = _upper(history_context.get("historical_late_entry_risk")) in {"HIGH", "SEVERE"}
    history_exit_here = history_exit_here or _upper(history_context.get("similarity_state")) in {
        "RESEMBLES_LATE_LOSS",
        "WOULD_EXIT_HERE",
    }
    explicit_bad_entry = BAD_ENTRY_CLASS_001 in {block_reason, bad_entry_class}
    blocked = bool(explicit_bad_entry or (late_chase and (wait_required or history_exit_here)))
    return {
        "execution_allowed": not blocked,
        "blocked": blocked,
        "block_reason": BAD_ENTRY_CLASS_001 if blocked else "",
        "instruction": (
            "Wait for pullback/retest into conservative trigger zone."
            if blocked
            else "Replay does not match bad-entry class 001."
        ),
    }


def record_paper_mode_decision(
    log_path: Path,
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
    click_callback: Any | None = None,
) -> dict[str, Any]:
    del click_callback
    now = _now_epoch() if now_epoch is None else float(now_epoch)
    execution = _mapping(packet.get("execution", {}))
    would_click = bool(decision.get("will_click", False))
    if "will_click" not in decision:
        would_click = bool(
            execution.get("enabled") is True
            and _upper(execution.get("state")) == "EXECUTABLE"
            and _upper(execution.get("side")) in {"BUY", "SELL"}
        )
    row: dict[str, Any] = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "event": "paper_mode_decision",
        "timestamp_epoch": now,
        "timestamp": _iso_from_epoch(now),
        "paper_mode": True,
        "would_click": would_click,
        "actual_clicked": False,
        "packet_id": _text(packet.get("packet_id")),
        "session_id": _text(packet.get("session_id")),
        "side": _upper(execution.get("side"), "HOLD"),
        "decision": dict(decision),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, default=str) + "\n")
    return row


def record_candle_outcome_metrics(
    log_path: Path,
    metrics: Mapping[str, Any],
    *,
    packet: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now = _now_epoch() if now_epoch is None else float(now_epoch)
    packet_payload = _mapping(packet)
    metric_payload = dict(metrics)
    metric_payload.setdefault("version", CANDLE_OUTCOME_TRACKER_V1)
    row: dict[str, Any] = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "event": "candle_outcome_metrics",
        "timestamp_epoch": now,
        "timestamp": _iso_from_epoch(now),
        "tracker_version": CANDLE_OUTCOME_TRACKER_V1,
        "packet_id": _text(packet_payload.get("packet_id") or metric_payload.get("packet_id")),
        "session_id": _text(packet_payload.get("session_id") or metric_payload.get("session_id")),
        "side": _upper(
            metric_payload.get("side")
            or _mapping(packet_payload.get("execution")).get("side")
            or _mapping(packet_payload.get("model_council")).get("final_side"),
            "HOLD",
        ),
        "metrics": metric_payload,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, default=str) + "\n")
    return row


append_candle_outcome_metrics = record_candle_outcome_metrics
