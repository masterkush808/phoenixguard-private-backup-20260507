"""Read-only live certification for the bounded PhoenixGuard V3 CPU stream.

The probe never starts, stops, captures, or otherwise mutates a tracker
session.  It samples the public session and health endpoints for a bounded
period, then verifies acquisition progress, lineage, resource bounds, the
single-slot latest-frame-wins contract, and the public three-question surface.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import time
from typing import Any, cast

try:
    from Backend.tools.certification_common_v3 import (
        DEFAULT_BASE_URL,
        DEFAULT_SESSION,
        HttpResult,
        http_json,
        quote_session,
    )
except ModuleNotFoundError:  # Direct execution from Backend/tools.
    from certification_common_v3 import (  # type: ignore[no-redef]
        DEFAULT_BASE_URL,
        DEFAULT_SESSION,
        HttpResult,
        http_json,
        quote_session,
    )


SCHEMA_VERSION = "PG_CERTIFY_CPU_STREAM_RUNTIME_V3"
EXPECTED_OPERATOR_QUESTION_KEYS = frozenset(
    {
        "market_origin_history",
        "studied_direction_current",
        "entry_now",
    }
)
_ACTIVE_STREAM_STATES = frozenset({"starting", "active"})
_API_HEALTH_STATES = frozenset({"ok", "healthy", "ready"})


@dataclass(frozen=True, slots=True)
class RuntimeCertificationConfig:
    """Bounded sampling and acceptance budgets for one live certification."""

    base_url: str = DEFAULT_BASE_URL
    session_id: str = DEFAULT_SESSION
    duration_sec: float = 12.0
    interval_sec: float = 0.5
    timeout_sec: float = 3.0
    min_fps_ratio: float = 0.30
    max_fps_ratio: float = 1.75
    max_stream_age_sec: float = 8.0
    max_session_age_sec: float = 120.0
    max_observer_memory_mib: float = 128.0
    max_full_frame_capacity: int = 4
    max_downsample_capacity: int = 256
    max_samples: int = 1_201

    def validate(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url must not be empty")
        if not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if not 2.0 <= self.duration_sec <= 300.0:
            raise ValueError("duration_sec must be between 2 and 300 seconds")
        if not 0.25 <= self.interval_sec <= 10.0:
            raise ValueError("interval_sec must be between 0.25 and 10 seconds")
        if not 0.1 <= self.timeout_sec <= 30.0:
            raise ValueError("timeout_sec must be between 0.1 and 30 seconds")
        if not 0.0 < self.min_fps_ratio <= 1.0:
            raise ValueError("min_fps_ratio must be greater than 0 and at most 1")
        if not 1.0 <= self.max_fps_ratio <= 4.0:
            raise ValueError("max_fps_ratio must be between 1 and 4")
        if self.max_fps_ratio < self.min_fps_ratio:
            raise ValueError("max_fps_ratio cannot be smaller than min_fps_ratio")
        if self.max_stream_age_sec <= 0.0 or self.max_session_age_sec <= 0.0:
            raise ValueError("freshness budgets must be greater than zero")
        if self.max_observer_memory_mib <= 0.0:
            raise ValueError("max_observer_memory_mib must be greater than zero")
        if self.max_full_frame_capacity < 1 or self.max_downsample_capacity < 2:
            raise ValueError("ring capacity ceilings are invalid")
        required_samples = int(math.ceil(self.duration_sec / self.interval_sec)) + 1
        if self.max_samples < required_samples:
            raise ValueError(
                f"max_samples={self.max_samples} cannot cover the requested "
                f"duration/interval ({required_samples} samples required)"
            )


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _epoch(value: object) -> float | None:
    numeric = _optional_float(value)
    if numeric is not None:
        return numeric if numeric > 0.0 else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.timestamp()
    return parsed.timestamp()


def _http_summary(result: HttpResult) -> dict[str, object]:
    return {
        "ok": bool(result.ok),
        "status": int(result.status),
        "latency_ms": round(float(result.latency_ms), 3),
        "error": str(result.error or ""),
    }


def _lineage_summary(value: object) -> dict[str, object]:
    lineage = _mapping(value)
    if not lineage:
        return {}
    return {
        "stream_id": str(lineage.get("stream_id") or ""),
        "stream_generation": _optional_int(lineage.get("stream_generation")),
        "frame_seq": _optional_int(lineage.get("frame_seq")),
        "captured_epoch": _optional_float(lineage.get("captured_epoch")),
        "broker_click_authority": lineage.get("broker_click_authority"),
    }


def _stream_from_session(payload: Mapping[str, Any]) -> dict[str, Any]:
    direct = _mapping(payload.get("cpu_stream_v3"))
    if direct:
        return direct
    tracking = _mapping(payload.get("tracking"))
    nested = _mapping(tracking.get("cpu_stream_v3"))
    if nested:
        return nested
    return _mapping(tracking.get("stream"))


def _stream_summary(payload: Mapping[str, Any]) -> dict[str, object]:
    stream = _stream_from_session(payload)
    observer = _mapping(stream.get("observer"))
    return {
        "present": bool(stream),
        "requested": stream.get("requested"),
        "enabled": stream.get("enabled"),
        "available": stream.get("available"),
        "status": str(stream.get("status") or "").strip().lower(),
        "mode": str(stream.get("mode") or ""),
        "target_fps": _optional_float(stream.get("target_fps")),
        "actual_fps": _optional_float(
            stream.get("actual_fps") or stream.get("acquisition_fps")
        ),
        "started_epoch": _optional_float(stream.get("started_epoch")),
        "status_updated_epoch": _optional_float(stream.get("status_updated_epoch")),
        "last_capture_epoch": _optional_float(stream.get("last_capture_epoch")),
        "last_event_epoch": _optional_float(stream.get("last_event_epoch")),
        "observed_frames": _optional_int(stream.get("observed_frames")),
        "accepted_events": _optional_int(stream.get("accepted_events")),
        "dropped_keyframes": _optional_int(stream.get("dropped_keyframes")),
        "capture_errors": _optional_int(stream.get("capture_errors")),
        "stale_generation_drops": _optional_int(stream.get("stale_generation_drops")),
        "keyframe_slot_capacity": _optional_int(stream.get("keyframe_slot_capacity")),
        "pending_keyframe": stream.get("pending_keyframe"),
        "full_model_policy": str(stream.get("full_model_policy") or ""),
        "broker_click_authority": stream.get("broker_click_authority"),
        "last_error": str(stream.get("last_error") or ""),
        "last_keyframe_lineage": _lineage_summary(stream.get("last_keyframe_lineage")),
        "last_observation_lineage": _lineage_summary(
            stream.get("last_observation_lineage")
        ),
        "observer": {
            "present": bool(observer),
            "status": str(observer.get("status") or ""),
            "cpu_only": observer.get("cpu_only"),
            "stream_id": str(observer.get("stream_id") or ""),
            "stream_generation": _optional_int(observer.get("stream_generation")),
            "frame_seq": _optional_int(observer.get("frame_seq")),
            "last_captured_epoch": _optional_float(observer.get("last_captured_epoch")),
            "rings": _mapping(observer.get("rings")),
            "memory": _mapping(observer.get("memory")),
            "counters": _mapping(observer.get("counters")),
        },
    }


def _sample(
    *,
    observed_epoch: float,
    session: HttpResult,
    health: HttpResult,
) -> dict[str, object]:
    session_payload = _mapping(session.payload)
    health_payload = _mapping(health.payload)
    stream = _stream_summary(session_payload)
    return {
        "observed_epoch": round(float(observed_epoch), 6),
        "session_http": _http_summary(session),
        "api_health_http": _http_summary(health),
        "api_health_status": str(health_payload.get("status") or "").strip().lower(),
        # The CPU stream sidecar is the bounded live heartbeat. Analytical
        # session/display clocks intentionally advance only when new evidence
        # is published and may remain old throughout an honest market rest.
        "session_updated_at": (
            stream.get("status_updated_epoch")
            or session_payload.get("updated_at")
            or session_payload.get("last_capture_epoch")
            or session_payload.get("display_published_epoch")
        ),
        "stream": stream,
    }


def collect_runtime_samples(
    config: RuntimeCertificationConfig,
    *,
    fetch_json: Callable[..., HttpResult] = http_json,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, object]], HttpResult, float]:
    """Collect bounded read-only samples and one operator contract response."""

    config.validate()
    base = config.base_url.rstrip("/")
    session_q = quote_session(config.session_id)
    session_url = f"{base}/v1/mobile/window-tracker/sessions/{session_q}"
    health_url = f"{base}/v1/mobile/health"
    operator_url = f"{base}/v1/mobile/operator/state/v1/{session_q}?view=all"
    started_epoch = clock()
    deadline = started_epoch + config.duration_sec
    samples: list[dict[str, object]] = []

    while len(samples) < config.max_samples:
        session_result = fetch_json(session_url, timeout=config.timeout_sec)
        health_result = fetch_json(health_url, timeout=config.timeout_sec)
        observed_epoch = clock()
        samples.append(
            _sample(
                observed_epoch=observed_epoch,
                session=session_result,
                health=health_result,
            )
        )
        if observed_epoch >= deadline:
            break
        sleeper(max(0.0, min(config.interval_sec, deadline - observed_epoch)))

    operator_result = fetch_json(operator_url, timeout=config.timeout_sec)
    return samples, operator_result, clock()


def _gate(
    passed: bool | None,
    message: str,
    evidence: Mapping[str, object],
) -> dict[str, object]:
    return {
        "status": "SKIP" if passed is None else ("PASS" if passed else "FAIL"),
        "passed": passed,
        "message": message,
        "evidence": dict(evidence),
    }


def _stream_samples(samples: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    return [
        _mapping(sample.get("stream"))
        for sample in samples
        if bool(_mapping(sample.get("session_http")).get("ok"))
        and bool(_mapping(sample.get("stream")).get("present"))
    ]


def _nondecreasing(values: Sequence[int | None]) -> bool:
    if not values or any(value is None for value in values):
        return False
    present = [cast(int, value) for value in values]
    return all(current >= previous for previous, current in zip(present, present[1:]))


def _resource_violations(
    stream_samples: Sequence[Mapping[str, Any]],
    config: RuntimeCertificationConfig,
) -> tuple[list[str], dict[str, object]]:
    violations: list[str] = []
    full_capacities: set[int] = set()
    downsample_capacities: set[int] = set()
    maximum_current_bytes = 0
    maximum_configured_bytes = 0
    observer_count = 0
    memory_ceiling = int(config.max_observer_memory_mib * 1024.0 * 1024.0)

    for index, stream in enumerate(stream_samples, start=1):
        observer = _mapping(stream.get("observer"))
        if not bool(observer.get("present")):
            violations.append(f"sample {index}: observer snapshot missing")
            continue
        observer_count += 1
        if observer.get("cpu_only") is not True:
            violations.append(f"sample {index}: observer cpu_only is not true")
        rings = _mapping(observer.get("rings"))
        for ring_name, capacity_ceiling, capacities in (
            ("full_frames", config.max_full_frame_capacity, full_capacities),
            ("downsamples", config.max_downsample_capacity, downsample_capacities),
        ):
            ring = _mapping(rings.get(ring_name))
            size = _optional_int(ring.get("size"))
            capacity = _optional_int(ring.get("capacity"))
            if size is None or capacity is None:
                violations.append(f"sample {index}: {ring_name} size/capacity missing")
                continue
            capacities.add(capacity)
            if capacity < 1 or capacity > capacity_ceiling:
                violations.append(
                    f"sample {index}: {ring_name} capacity {capacity} exceeds safe range"
                )
            if size < 0 or size > capacity:
                violations.append(
                    f"sample {index}: {ring_name} size {size} exceeds capacity {capacity}"
                )
        memory = _mapping(observer.get("memory"))
        current_bytes = _optional_int(memory.get("current_estimated_pixel_bytes"))
        configured_bytes = _optional_int(
            memory.get("configured_upper_bound_pixel_bytes")
        )
        if current_bytes is None or configured_bytes is None:
            violations.append(f"sample {index}: observer memory bounds missing")
            continue
        maximum_current_bytes = max(maximum_current_bytes, current_bytes)
        maximum_configured_bytes = max(maximum_configured_bytes, configured_bytes)
        if current_bytes < 0 or configured_bytes < 0 or current_bytes > configured_bytes:
            violations.append(
                f"sample {index}: current observer memory exceeds configured bound"
            )
        if configured_bytes > memory_ceiling:
            violations.append(
                f"sample {index}: configured observer bound exceeds "
                f"{config.max_observer_memory_mib:g} MiB"
            )

    if len(full_capacities) > 1 or len(downsample_capacities) > 1:
        violations.append("observer ring capacities changed during certification")
    return violations, {
        "observer_samples": observer_count,
        "full_frame_capacities": sorted(full_capacities),
        "downsample_capacities": sorted(downsample_capacities),
        "maximum_current_pixel_bytes": maximum_current_bytes,
        "maximum_configured_pixel_bytes": maximum_configured_bytes,
        "memory_ceiling_bytes": memory_ceiling,
    }


def evaluate_runtime_samples(
    config: RuntimeCertificationConfig,
    samples: Sequence[Mapping[str, object]],
    operator_result: HttpResult,
    *,
    generated_epoch: float | None = None,
) -> dict[str, object]:
    """Evaluate collected samples into explicit, machine-readable gates."""

    config.validate()
    generated = time.time() if generated_epoch is None else float(generated_epoch)
    streams = _stream_samples(samples)
    final_stream = streams[-1] if streams else {}
    gates: dict[str, dict[str, object]] = {}

    session_http = [_mapping(sample.get("session_http")) for sample in samples]
    health_http = [_mapping(sample.get("api_health_http")) for sample in samples]
    health_states = [str(sample.get("api_health_status") or "").lower() for sample in samples]
    final_sample_epoch = (
        _optional_float(samples[-1].get("observed_epoch")) if samples else None
    )
    final_capture_epoch = _optional_float(final_stream.get("last_capture_epoch"))
    stream_age = (
        final_sample_epoch - final_capture_epoch
        if final_sample_epoch is not None and final_capture_epoch is not None
        else None
    )
    session_updated_epoch = _epoch(samples[-1].get("session_updated_at")) if samples else None
    session_age = (
        final_sample_epoch - session_updated_epoch
        if final_sample_epoch is not None and session_updated_epoch is not None
        else None
    )
    reachability_ok = bool(samples) and all(
        bool(result.get("ok")) for result in [*session_http, *health_http]
    )
    health_ok = bool(health_states) and all(
        state in _API_HEALTH_STATES for state in health_states
    )
    stream_fresh = bool(
        stream_age is not None
        and -1.0 <= stream_age <= config.max_stream_age_sec
    )
    session_fresh = bool(
        session_age is not None
        and -1.0 <= session_age <= config.max_session_age_sec
    )
    freshness_ok = reachability_ok and health_ok and stream_fresh and session_fresh
    gates["session_api_freshness"] = _gate(
        freshness_ok,
        "Session/API responses are live and the stream/session timestamps are fresh."
        if freshness_ok
        else "Session/API reachability, health, or freshness budget failed.",
        {
            "sample_count": len(samples),
            "all_session_http_ok": bool(samples)
            and all(bool(result.get("ok")) for result in session_http),
            "all_api_health_http_ok": bool(samples)
            and all(bool(result.get("ok")) for result in health_http),
            "api_health_states": sorted(set(health_states)),
            "final_stream_age_sec": (
                round(stream_age, 6) if stream_age is not None else None
            ),
            "max_stream_age_sec": config.max_stream_age_sec,
            "final_session_age_sec": (
                round(session_age, 6) if session_age is not None else None
            ),
            "max_session_age_sec": config.max_session_age_sec,
        },
    )

    requested_values = [stream.get("requested") for stream in streams]
    requested_ok = bool(requested_values) and all(value is True for value in requested_values)
    gates["stream_requested"] = _gate(
        requested_ok,
        "CPU streaming was requested for every sampled session state."
        if requested_ok
        else "CPU streaming was not explicitly requested throughout the sample.",
        {"values": requested_values},
    )

    available_values = [stream.get("available") for stream in streams]
    available_ok = bool(available_values) and available_values[-1] is True and sum(
        value is True for value in available_values
    ) >= max(1, len(available_values) // 2)
    gates["stream_available"] = _gate(
        available_ok,
        "The stream worker is available and remained present during sampling."
        if available_ok
        else "The stream worker was unavailable or disappeared during sampling.",
        {
            "available_samples": sum(value is True for value in available_values),
            "total_stream_samples": len(available_values),
            "final_available": available_values[-1] if available_values else None,
        },
    )

    statuses = [str(stream.get("status") or "").lower() for stream in streams]
    status_ok = bool(statuses) and statuses[-1] == "active" and all(
        state in _ACTIVE_STREAM_STATES for state in statuses
    )
    gates["stream_status"] = _gate(
        status_ok,
        "The stream reached and retained active event-driven acquisition."
        if status_ok
        else "The stream was disabled, stopped, or entered snapshot fallback/degraded state.",
        {
            "states": statuses,
            "final_mode": final_stream.get("mode"),
            "last_error": final_stream.get("last_error"),
        },
    )

    observed_values = [_optional_int(stream.get("observed_frames")) for stream in streams]
    target_values = [_optional_float(stream.get("target_fps")) for stream in streams]
    sample_epochs = [
        _optional_float(sample.get("observed_epoch"))
        for sample in samples
        if bool(_mapping(sample.get("session_http")).get("ok"))
        and bool(_mapping(sample.get("stream")).get("present"))
    ]
    measured_fps: float | None = None
    first_observed = observed_values[0] if observed_values else None
    last_observed = observed_values[-1] if observed_values else None
    first_sample_epoch = sample_epochs[0] if sample_epochs else None
    last_sample_epoch = sample_epochs[-1] if sample_epochs else None
    if (
        len(observed_values) >= 2
        and len(sample_epochs) == len(observed_values)
        and first_observed is not None
        and last_observed is not None
        and first_sample_epoch is not None
        and last_sample_epoch is not None
        and last_sample_epoch > first_sample_epoch
    ):
        measured_fps = (last_observed - first_observed) / (
            last_sample_epoch - first_sample_epoch
        )
    target_fps = target_values[-1] if target_values else None
    target_stable = bool(target_values) and all(
        value is not None and math.isclose(value, cast(float, target_fps), abs_tol=1e-6)
        for value in target_values
    )
    min_measured = (
        target_fps * config.min_fps_ratio if target_fps is not None else None
    )
    max_measured = (
        target_fps * config.max_fps_ratio if target_fps is not None else None
    )
    rate_ok = bool(
        measured_fps is not None
        and min_measured is not None
        and max_measured is not None
        and measured_fps >= min_measured
        and measured_fps <= max_measured
        and target_stable
    )
    gates["observed_frame_advancement"] = _gate(
        rate_ok,
        "Observed frame advancement stayed near the configured CPU acquisition rate."
        if rate_ok
        else "Observed frame advancement was stalled or outside the CPU-variance band.",
        {
            "first_observed_frames": observed_values[0] if observed_values else None,
            "last_observed_frames": observed_values[-1] if observed_values else None,
            "measured_fps": round(measured_fps, 6) if measured_fps is not None else None,
            "target_fps": target_fps,
            "allowed_fps_min": round(min_measured, 6) if min_measured is not None else None,
            "allowed_fps_max": round(max_measured, 6) if max_measured is not None else None,
            "target_stable": target_stable,
            "reported_actual_fps": final_stream.get("actual_fps"),
        },
    )

    accepted_values = [_optional_int(stream.get("accepted_events")) for stream in streams]
    dropped_values = [_optional_int(stream.get("dropped_keyframes")) for stream in streams]
    observer_ring_drop_values: dict[str, list[int | None]] = {
        "full_frame_ring_drops": [],
        "downsample_ring_drops": [],
        "latest_frame_wins_drops": [],
    }
    for stream in streams:
        counters = _mapping(_mapping(stream.get("observer")).get("counters"))
        for name in observer_ring_drop_values:
            observer_ring_drop_values[name].append(_optional_int(counters.get(name)))
    counters_ok = (
        _nondecreasing(observed_values)
        and _nondecreasing(accepted_values)
        and _nondecreasing(dropped_values)
        and all(_nondecreasing(values) for values in observer_ring_drop_values.values())
    )
    gates["monotonic_stream_counters"] = _gate(
        counters_ok,
        "Observed, accepted, and drop counters were present and monotonic."
        if counters_ok
        else "A required observed/accepted/drop counter was missing or regressed.",
        {
            "observed_frames": observed_values,
            "accepted_events": accepted_values,
            "dropped_keyframes": dropped_values,
            "observer_drop_counters": observer_ring_drop_values,
        },
    )

    slot_capacities = [
        _optional_int(stream.get("keyframe_slot_capacity")) for stream in streams
    ]
    pending_values = [stream.get("pending_keyframe") for stream in streams]
    slot_ok = bool(slot_capacities) and all(value == 1 for value in slot_capacities) and all(
        isinstance(value, bool) for value in pending_values
    )
    gates["single_keyframe_slot"] = _gate(
        slot_ok,
        "The latest-keyframe handoff remained a capacity-one slot."
        if slot_ok
        else "The keyframe handoff was missing or was not capacity one.",
        {
            "capacities": slot_capacities,
            "maximum_derived_pending_depth": max(
                (1 if value is True else 0 for value in pending_values),
                default=0,
            ),
        },
    )

    resource_violations, resource_evidence = _resource_violations(streams, config)
    resources_ok = bool(streams) and not resource_violations
    gates["bounded_observer_resources"] = _gate(
        resources_ok,
        "Observer rings and estimated pixel memory stayed inside fixed capacities."
        if resources_ok
        else "Observer ring or memory bounds were absent, changed, or exceeded.",
        {**resource_evidence, "violations": resource_violations},
    )

    observer_lineages: list[tuple[str, int | None, int | None]] = []
    observation_lineages: list[tuple[str, int | None, int | None]] = []
    lineage_violations: list[str] = []
    for index, stream in enumerate(streams, start=1):
        observer = _mapping(stream.get("observer"))
        observer_row = (
            str(observer.get("stream_id") or ""),
            _optional_int(observer.get("stream_generation")),
            _optional_int(observer.get("frame_seq")),
        )
        observer_lineages.append(observer_row)
        observation = _mapping(stream.get("last_observation_lineage"))
        if observation:
            observation_row = (
                str(observation.get("stream_id") or ""),
                _optional_int(observation.get("stream_generation")),
                _optional_int(observation.get("frame_seq")),
            )
            observation_lineages.append(observation_row)
            if observation_row[0] != observer_row[0]:
                lineage_violations.append(
                    f"sample {index}: observation and observer stream_id differ"
                )
            if (
                observation_row[1] is None
                or observer_row[1] is None
                or observation_row[1] > observer_row[1]
                or observation_row[2] is None
                or observer_row[2] is None
                or observation_row[2] > observer_row[2]
            ):
                lineage_violations.append(
                    f"sample {index}: observation lineage is ahead of observer state"
                )
    stream_ids = {row[0] for row in observer_lineages if row[0]}
    generations = [row[1] for row in observer_lineages]
    frame_sequences = [row[2] for row in observer_lineages]
    observation_generations = [row[1] for row in observation_lineages]
    observation_sequences = [row[2] for row in observation_lineages]
    lineage_ok = bool(observer_lineages) and len(stream_ids) == 1 and not lineage_violations
    lineage_ok = bool(
        lineage_ok
        and _nondecreasing(generations)
        and _nondecreasing(frame_sequences)
        and bool(observation_lineages)
        and _nondecreasing(observation_generations)
        and _nondecreasing(observation_sequences)
    )
    gates["monotonic_stream_lineage"] = _gate(
        lineage_ok,
        "Stream identity, generation, and observation sequence stayed monotonic."
        if lineage_ok
        else "Stream lineage was missing, replaced, regressed, or internally inconsistent.",
        {
            "stream_ids": sorted(stream_ids),
            "observer_generations": generations,
            "observer_frame_sequences": frame_sequences,
            "observation_generations": observation_generations,
            "observation_frame_sequences": observation_sequences,
            "violations": lineage_violations,
        },
    )

    authority_values: list[object] = []
    for stream in streams:
        authority_values.append(stream.get("broker_click_authority"))
        for lineage_name in ("last_keyframe_lineage", "last_observation_lineage"):
            lineage = _mapping(stream.get(lineage_name))
            if lineage:
                authority_values.append(lineage.get("broker_click_authority"))
    authority_ok = bool(authority_values) and all(value is False for value in authority_values)
    gates["no_broker_click_authority"] = _gate(
        authority_ok,
        "The CPU stream and every published stream lineage explicitly deny broker clicks."
        if authority_ok
        else "Broker-click authority was missing or not explicitly false in the stream contract.",
        {
            "checked_values": authority_values,
            "all_explicitly_false": authority_ok,
        },
    )

    if operator_result.ok:
        operator_payload = _mapping(operator_result.payload)
        questions = _mapping(operator_payload.get("three_questions"))
        metadata_keys = {"schema_version"}
        question_keys = set(questions) - metadata_keys
        unexpected_keys = set(questions) - set(EXPECTED_OPERATOR_QUESTION_KEYS) - metadata_keys
        questions_ok = (
            len(question_keys) == 3
            and question_keys == set(EXPECTED_OPERATOR_QUESTION_KEYS)
            and not unexpected_keys
        )
        gates["exactly_three_operator_questions"] = _gate(
            questions_ok,
            "The public operator contract exposes exactly the three required questions."
            if questions_ok
            else "The available operator contract does not expose exactly the three required questions.",
            {
                "operator_http": _http_summary(operator_result),
                "question_count": len(question_keys),
                "question_keys": sorted(question_keys),
                "metadata_keys": sorted(set(questions) & metadata_keys),
                "unexpected_keys": sorted(unexpected_keys),
                "expected_question_keys": sorted(EXPECTED_OPERATOR_QUESTION_KEYS),
            },
        )
    else:
        gates["exactly_three_operator_questions"] = _gate(
            None,
            "Operator endpoint was unavailable, so the optional three-question check was skipped.",
            {"operator_http": _http_summary(operator_result)},
        )

    backlog_violations: list[str] = []
    for index, stream in enumerate(streams, start=1):
        observed = _optional_int(stream.get("observed_frames"))
        accepted = _optional_int(stream.get("accepted_events"))
        dropped = _optional_int(stream.get("dropped_keyframes"))
        if stream.get("full_model_policy") != "ACCEPTED_EVENT_OR_HEARTBEAT_ONLY":
            backlog_violations.append(f"sample {index}: full-model admission policy changed")
        if _optional_int(stream.get("keyframe_slot_capacity")) != 1:
            backlog_violations.append(f"sample {index}: keyframe slot capacity is not one")
        if not isinstance(stream.get("pending_keyframe"), bool):
            backlog_violations.append(f"sample {index}: pending keyframe depth is not bounded boolean state")
        if observed is None or accepted is None or dropped is None:
            backlog_violations.append(f"sample {index}: backlog counters missing")
            continue
        if accepted < 0 or observed < 0 or accepted > observed:
            backlog_violations.append(f"sample {index}: accepted count is outside observed count")
        if dropped < 0 or dropped > accepted:
            backlog_violations.append(f"sample {index}: dropped count is outside accepted count")
    backlog_ok = bool(streams) and not backlog_violations and resources_ok and slot_ok
    gates["no_unbounded_backlog"] = _gate(
        backlog_ok,
        "Latest-frame-wins admission, capacity-one handoff, and bounded rings prevent backlog growth."
        if backlog_ok
        else "The stream lacks one or more structural bounds that prevent backlog growth.",
        {
            "policy": final_stream.get("full_model_policy"),
            "keyframe_slot_capacity": final_stream.get("keyframe_slot_capacity"),
            "maximum_derived_pending_depth": max(
                (1 if stream.get("pending_keyframe") is True else 0 for stream in streams),
                default=0,
            ),
            "violations": backlog_violations,
        },
    )

    failures = [
        f"{name}: {gate['message']}"
        for name, gate in gates.items()
        if gate.get("status") == "FAIL"
    ]
    warnings = [
        f"{name}: {gate['message']}"
        for name, gate in gates.items()
        if gate.get("status") == "SKIP"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_epoch": generated,
        "verdict": "PASS" if not failures else "FAIL",
        "read_only": True,
        "bounded": True,
        "configuration": {
            "base_url": config.base_url.rstrip("/"),
            "session_id": config.session_id,
            "duration_sec": config.duration_sec,
            "interval_sec": config.interval_sec,
            "timeout_sec": config.timeout_sec,
            "min_fps_ratio": config.min_fps_ratio,
            "max_fps_ratio": config.max_fps_ratio,
            "max_stream_age_sec": config.max_stream_age_sec,
            "max_session_age_sec": config.max_session_age_sec,
            "max_observer_memory_mib": config.max_observer_memory_mib,
            "max_samples": config.max_samples,
        },
        "failures": failures,
        "warnings": warnings,
        "gates": gates,
        "sample_count": len(samples),
        "samples": list(samples),
    }


def certify_runtime(
    config: RuntimeCertificationConfig,
    *,
    fetch_json: Callable[..., HttpResult] = http_json,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    samples, operator_result, finished_epoch = collect_runtime_samples(
        config,
        fetch_json=fetch_json,
        clock=clock,
        sleeper=sleeper,
    )
    return evaluate_runtime_samples(
        config,
        samples,
        operator_result,
        generated_epoch=finished_epoch,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only bounded certification of the PhoenixGuard V3 CPU stream runtime."
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=12.0)
    parser.add_argument("--interval-sec", type=float, default=0.5)
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    parser.add_argument("--min-fps-ratio", type=float, default=0.30)
    parser.add_argument("--max-fps-ratio", type=float, default=1.75)
    parser.add_argument("--max-stream-age-sec", type=float, default=8.0)
    parser.add_argument("--max-session-age-sec", type=float, default=120.0)
    parser.add_argument("--max-observer-memory-mib", type=float, default=128.0)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    config = RuntimeCertificationConfig(
        base_url=str(args.base_url),
        session_id=str(args.session),
        duration_sec=float(args.duration_sec),
        interval_sec=float(args.interval_sec),
        timeout_sec=float(args.timeout_sec),
        min_fps_ratio=float(args.min_fps_ratio),
        max_fps_ratio=float(args.max_fps_ratio),
        max_stream_age_sec=float(args.max_stream_age_sec),
        max_session_age_sec=float(args.max_session_age_sec),
        max_observer_memory_mib=float(args.max_observer_memory_mib),
    )
    try:
        report = certify_runtime(config)
    except ValueError as exc:
        parser.error(str(exc))
    output = cast(Path | None, args.output)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
