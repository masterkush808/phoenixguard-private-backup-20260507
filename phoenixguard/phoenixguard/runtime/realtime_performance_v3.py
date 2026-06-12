from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import sys
import traceback
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar, cast


FRAME_TIMING_TRACE_SCHEMA_VERSION = "PG_FRAME_TIMING_TRACE_V3"
LATEST_FRAME_BUFFER_SCHEMA_VERSION = "PG_LATEST_FRAME_BUFFER_V3"
ASYNC_ARTIFACT_WRITER_SCHEMA_VERSION = "PG_ASYNC_ARTIFACT_WRITER_V3"
BACKPRESSURE_SCHEMA_VERSION = "PG_REALTIME_BACKPRESSURE_CONTROLLER_V3"
ADAPTIVE_PERFORMANCE_SCHEMA_VERSION = "PG_ADAPTIVE_PERFORMANCE_CONTROLLER_V3"
MODEL_WARM_STATE_SCHEMA_VERSION = "PG_MODEL_WARM_STATE_V3"
PERFORMANCE_TRACE_SCHEMA_VERSION = "PG_PERFORMANCE_TRACE_V3"
SESSION_ATOMIC_WRITER_SCHEMA_VERSION = "PG_SESSION_ATOMIC_WRITER_V3"
SESSION_FRESHNESS_VALIDATOR_SCHEMA_VERSION = "PG_SESSION_FRESHNESS_VALIDATOR_V3"
CAPTURE_WORKER_HEALTH_SCHEMA_VERSION = "PG_CAPTURE_WORKER_HEALTH_V3"
CAPTURE_WATCHDOG_SCHEMA_VERSION = "PG_CAPTURE_WATCHDOG_V3"

DISPLAY_QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "FAST": {"format": "jpeg", "quality": 80, "overlay_budget_scale": 0.70, "debug_labels": False},
    "BALANCED": {"format": "jpeg", "quality": 88, "overlay_budget_scale": 1.00, "debug_labels": False},
    "HIGH_CLARITY": {"format": "png", "quality": 95, "overlay_budget_scale": 1.00, "debug_labels": True},
    "DEBUG_LOSSLESS": {"format": "png", "quality": 100, "overlay_budget_scale": 1.00, "debug_labels": True},
}

OVERLAY_RENDER_BUDGETS: dict[str, int] = {
    "CLEAN_LIVE": 10,
    "GLOBAL": 18,
    "LOCAL": 20,
    "SUPPLY_DEMAND": 18,
    "TRIGGER": 18,
    "TARGET": 16,
    "PATH": 24,
    "COUNCIL": 24,
    "TWO_CANDLE_STUDY": 12,
    "LSTM_STUDY": 8,
    "ACTIVE_CONTEXT": 30,
    "FULL_HISTORY_READ": 80,
    "REPLAY": 80,
    "PREDICTION": 30,
    "BROKER": 24,
    "CALIBRATION": 40,
    "DIAGNOSTICS": 160,
    "DEBUG": 200,
    "INSPECTOR": 200,
}

DEFAULT_SPEED_BUDGETS_MS: dict[str, float] = {
    "capture": 80.0,
    "transport": 50.0,
    "preprocess": 80.0,
    "model_inference": 700.0,
    "overlay": 100.0,
    "frontend_render": 100.0,
    "live_visual_age_target": 1200.0,
    "hard_stale": 2500.0,
    "hard_reject": 5000.0,
}

T = TypeVar("T")


def _now_ms() -> int:
    return int(time.time() * 1000.0)


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return float(number)


def _int(value: Any, default: int = 0) -> int:
    return int(_float(value, float(default)))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, Any], item)) for item in value if isinstance(item, Mapping)]


def _epoch_to_ms(value: Any) -> int:
    number = _float(value, 0.0)
    if number <= 0.0:
        return 0
    return int(number if number > 10_000_000_000 else number * 1000.0)


def _epoch_seconds(value: Any) -> float:
    number = _float(value, 0.0)
    if number <= 0.0:
        return 0.0
    return number / 1000.0 if number > 10_000_000_000 else number


def _percentile(values: Sequence[float], percentile: float) -> float:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return 0.0
    if len(clean) == 1:
        return round(clean[0], 3)
    rank = (len(clean) - 1) * max(0.0, min(100.0, float(percentile))) / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return round(clean[lower], 3)
    fraction = rank - lower
    return round(clean[lower] * (1.0 - fraction) + clean[upper] * fraction, 3)


def summarize_window(values: Sequence[float]) -> dict[str, float]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": float(len(clean)),
        "avg": round(sum(clean) / len(clean), 3) if clean else 0.0,
        "p50": _percentile(clean, 50),
        "p90": _percentile(clean, 90),
        "p95": _percentile(clean, 95),
        "max": round(max(clean), 3) if clean else 0.0,
    }


def _frame_int(payload: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = _int(payload.get(key), 0)
        if value > 0:
            return value
    return 0


def _nested_epoch(payload: Mapping[str, Any], key: str) -> float:
    row = _mapping(payload.get(key))
    return _epoch_seconds(row.get("capture_epoch") or row.get("capture_epoch_sec") or row.get("capture_started_epoch"))


def _session_frame_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    tracking = _mapping(payload.get("tracking_summary"))
    signal = _mapping(payload.get("latest_signal"))
    pipeline = _mapping(tracking.get("pipeline_timing") or signal.get("pipeline_timing"))
    display_only_publish = bool(payload.get("display_snapshot_only_v3", False)) or bool(
        payload.get("display_fast_path_v3")
    )
    frame_id = _frame_int(payload, "frame_id", "frame_index", "capture_count")
    display_frame_id = _frame_int(payload, "display_frame_id", "frame_index", "frame_id", "capture_count")
    chart_frame_id = _frame_int(payload, "chart_frame_id", "frame_index", "frame_id", "capture_count")
    overlay_frame_id = _frame_int(payload, "overlay_frame_id", "frame_index", "frame_id", "capture_count")
    model_vote_frame_id = _frame_int(payload, "model_vote_frame_id", "frame_index", "frame_id", "capture_count")
    display_capture_epoch = max(
        _epoch_seconds(payload.get("display_capture_epoch")),
        _epoch_seconds(payload.get("last_display_capture_epoch")),
        _nested_epoch(payload, "broker_surface_frame"),
        _epoch_seconds(payload.get("last_capture_started_epoch")),
    )
    model_epoch_candidates = [
        _epoch_seconds(payload.get("model_capture_epoch")),
        _epoch_seconds(pipeline.get("capture_started_epoch")),
        _epoch_seconds(signal.get("capture_started_epoch")),
    ]
    if not display_only_publish:
        model_epoch_candidates.extend(
            [
                _epoch_seconds(payload.get("last_capture_started_epoch")),
                _epoch_seconds(payload.get("last_capture_epoch")),
            ]
        )
    model_capture_epoch = max(model_epoch_candidates)
    source_capture_id = _text(payload.get("source_capture_id"))
    if not source_capture_id and frame_id > 0:
        source_capture_id = "capture:%s:%s:%s" % (
            _text(payload.get("session_id"), "session"),
            _int(payload.get("capture_count"), frame_id),
            frame_id,
        )
    return {
        "frame_id": frame_id,
        "display_frame_id": display_frame_id,
        "display_capture_epoch": display_capture_epoch,
        "chart_frame_id": chart_frame_id,
        "overlay_frame_id": overlay_frame_id,
        "model_vote_frame_id": model_vote_frame_id,
        "model_capture_epoch": model_capture_epoch,
        "state_version": _int(payload.get("state_version") or signal.get("state_version")),
        "source_capture_id": source_capture_id,
    }


@dataclass(frozen=True)
class SessionFreshnessValidatorV3:
    """Validate that a session snapshot is fresh because a frame advanced, not just because the file was touched."""

    max_visual_age_ms: float = 5000.0
    required_fields: tuple[str, ...] = (
        "frame_id",
        "display_frame_id",
        "display_capture_epoch",
        "chart_frame_id",
        "overlay_frame_id",
        "model_vote_frame_id",
        "state_version",
        "source_capture_id",
    )

    def validate(
        self,
        previous: Mapping[str, Any] | None,
        current: Mapping[str, Any],
        *,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        now = float(now_epoch if now_epoch is not None else time.time())
        prev_fields = _session_frame_fields(previous or {})
        curr_fields = _session_frame_fields(current)
        missing = [
            key
            for key in self.required_fields
            if (key not in curr_fields or curr_fields.get(key) in ("", 0, 0.0, None))
        ]
        frame_advanced = bool(curr_fields["frame_id"] > prev_fields.get("frame_id", 0))
        display_advanced = bool(curr_fields["display_frame_id"] > prev_fields.get("display_frame_id", 0))
        capture_epoch_advanced = bool(
            curr_fields["display_capture_epoch"] > float(prev_fields.get("display_capture_epoch", 0.0) or 0.0) + 0.001
        )
        model_epoch_advanced = bool(
            curr_fields["model_capture_epoch"] > float(prev_fields.get("model_capture_epoch", 0.0) or 0.0) + 0.001
        )
        version_changed = bool(
            _int(current.get("state_version")) > _int((previous or {}).get("state_version"))
            or _text(current.get("updated_at")) != _text((previous or {}).get("updated_at"))
        )
        touch_only_stale = bool(version_changed and previous and not (frame_advanced or display_advanced or capture_epoch_advanced or model_epoch_advanced))
        frame_epoch_mismatch = bool(previous and (frame_advanced or display_advanced) and not capture_epoch_advanced)
        display_age_ms = max(0.0, (now - float(curr_fields["display_capture_epoch"] or 0.0)) * 1000.0) if curr_fields["display_capture_epoch"] else 0.0
        model_age_ms = max(0.0, (now - float(curr_fields["model_capture_epoch"] or 0.0)) * 1000.0) if curr_fields["model_capture_epoch"] else 0.0
        partial = bool(missing and (curr_fields["frame_id"] > 0 or curr_fields["display_frame_id"] > 0))
        stale_visual = bool(display_age_ms > self.max_visual_age_ms and curr_fields["display_capture_epoch"] > 0.0)
        errors: list[str] = []
        if missing:
            errors.append("missing required frame fields: " + ", ".join(missing))
        if touch_only_stale:
            errors.append("session timestamp/version advanced without frame or capture epoch advancing")
        if frame_epoch_mismatch:
            errors.append("frame id advanced without display_capture_epoch advancing")
        if stale_visual:
            errors.append(f"display capture age {display_age_ms:.0f}ms exceeds {self.max_visual_age_ms:.0f}ms")
        status = "PASS"
        visual_health = "PASS"
        if partial:
            status = "PARTIAL_SESSION"
            visual_health = "FAIL"
        if touch_only_stale:
            status = "TOUCH_ONLY_STALE"
            visual_health = "FAIL"
        elif frame_epoch_mismatch:
            status = "FRAME_EPOCH_MISMATCH"
            visual_health = "FAIL"
        if stale_visual and status == "PASS":
            status = "STALE_VISUAL"
            visual_health = "FAIL"
        return {
            "schema_version": SESSION_FRESHNESS_VALIDATOR_SCHEMA_VERSION,
            "ok": bool(status == "PASS" and not missing),
            "status": status,
            "visual_health": visual_health,
            "required_fields": list(self.required_fields),
            "missing_fields": missing,
            "frame_id_advanced": frame_advanced,
            "display_frame_id_advanced": display_advanced,
            "capture_epoch_advanced": capture_epoch_advanced,
            "model_capture_epoch_advanced": model_epoch_advanced,
            "touch_only_stale": touch_only_stale,
            "frame_epoch_mismatch": frame_epoch_mismatch,
            "display_age_ms": round(display_age_ms, 3),
            "model_age_ms": round(model_age_ms, 3),
            "fields": curr_fields,
            "errors": errors,
        }


class SessionAtomicWriterV3:
    """Prepare and write session snapshots with explicit frame lineage fields."""

    schema_version = SESSION_ATOMIC_WRITER_SCHEMA_VERSION

    def __init__(self, path: Path, *, validator: SessionFreshnessValidatorV3 | None = None) -> None:
        self.path = Path(path)
        self.validator = validator or SessionFreshnessValidatorV3()

    @staticmethod
    def prepare_payload(payload: Mapping[str, Any], *, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        row = dict(payload)
        fields = _session_frame_fields(row)
        frame_id = int(fields["frame_id"] or row.get("frame_index") or row.get("capture_count") or 0)
        if frame_id > 0:
            row.setdefault("frame_id", frame_id)
        for key in ("display_frame_id", "chart_frame_id", "overlay_frame_id", "model_vote_frame_id"):
            value = int(fields.get(key) or 0)
            if value > 0:
                row[key] = value
        display_epoch = float(fields.get("display_capture_epoch") or 0.0)
        if display_epoch > 0.0:
            row["display_capture_epoch"] = display_epoch
        model_epoch = float(fields.get("model_capture_epoch") or 0.0)
        if model_epoch > 0.0:
            row["model_capture_epoch"] = model_epoch
        source_capture_id = _text(fields.get("source_capture_id"))
        if source_capture_id:
            row["source_capture_id"] = source_capture_id
        if int(row.get("state_version") or 0) <= 0:
            state_seed_epoch = _epoch_seconds(row.get("last_capture_epoch") or row.get("display_capture_epoch"))
            if frame_id > 0 and state_seed_epoch > 0.0:
                row["state_version"] = int((state_seed_epoch * 1000.0) + frame_id)
        validator = SessionFreshnessValidatorV3()
        row["session_freshness_v3"] = validator.validate(previous or {}, row)
        row["session_atomic_writer_v3"] = {
            "schema_version": SESSION_ATOMIC_WRITER_SCHEMA_VERSION,
            "prepared_epoch": time.time(),
            "required_fields_present": not bool(row["session_freshness_v3"].get("missing_fields")),
            "source": "SessionAtomicWriterV3.prepare_payload",
        }
        return row

    @staticmethod
    def write_atomic(path: Path, payload: Mapping[str, Any], *, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        prepared = SessionAtomicWriterV3.prepare_payload(payload, previous=previous)
        temp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
        temp.write_text(json.dumps(prepared, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temp.replace(target)
        try:
            target.with_suffix(target.suffix + ".last_good").write_text(
                json.dumps(prepared, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass
        return prepared

    def write(self, payload: Mapping[str, Any], *, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.write_atomic(self.path, payload, previous=previous)


@dataclass
class CaptureWorkerV3Health:
    session_id: str
    capture_count: int = 0
    frame_id: int = 0
    display_frame_id: int = 0
    display_capture_epoch: float = 0.0
    model_capture_epoch: float = 0.0
    active: bool = False
    lock_age_ms: float = 0.0
    last_error: str = ""
    last_success_epoch: float = 0.0

    @classmethod
    def from_session(
        cls,
        session: Mapping[str, Any],
        *,
        active_study_started_epoch: float = 0.0,
        now_epoch: float | None = None,
    ) -> "CaptureWorkerV3Health":
        now = float(now_epoch if now_epoch is not None else time.time())
        fields = _session_frame_fields(session)
        active = bool(active_study_started_epoch > 0.0)
        return cls(
            session_id=_text(session.get("session_id"), "session"),
            capture_count=_int(session.get("capture_count")),
            frame_id=_int(fields.get("frame_id")),
            display_frame_id=_int(fields.get("display_frame_id")),
            display_capture_epoch=float(fields.get("display_capture_epoch") or 0.0),
            model_capture_epoch=float(fields.get("model_capture_epoch") or 0.0),
            active=active,
            lock_age_ms=max(0.0, (now - active_study_started_epoch) * 1000.0) if active else 0.0,
            last_error=_text(session.get("last_error")),
            last_success_epoch=_epoch_seconds(session.get("last_capture_epoch")),
        )

    def as_dict(self) -> dict[str, Any]:
        now = time.time()
        return {
            "schema_version": CAPTURE_WORKER_HEALTH_SCHEMA_VERSION,
            "session_id": self.session_id,
            "capture_count": int(self.capture_count),
            "frame_id": int(self.frame_id),
            "display_frame_id": int(self.display_frame_id),
            "display_capture_epoch": float(self.display_capture_epoch),
            "model_capture_epoch": float(self.model_capture_epoch),
            "display_age_ms": round(max(0.0, (now - self.display_capture_epoch) * 1000.0), 3) if self.display_capture_epoch else 0.0,
            "model_age_ms": round(max(0.0, (now - self.model_capture_epoch) * 1000.0), 3) if self.model_capture_epoch else 0.0,
            "active": bool(self.active),
            "lock_age_ms": round(float(self.lock_age_ms), 3),
            "last_error": self.last_error,
            "last_success_epoch": float(self.last_success_epoch),
            "ok": bool(self.frame_id > 0 and self.display_capture_epoch > 0.0 and not self.last_error),
        }


class CaptureWatchdogV3:
    """Small watchdog helper used by the API/tools to record stuck capture evidence."""

    def __init__(
        self,
        *,
        screen_capture_timeout_ms: int = 1500,
        session_write_timeout_ms: int = 500,
        capture_once_timeout_ms: int = 3000,
    ) -> None:
        self.screen_capture_timeout_ms = int(screen_capture_timeout_ms)
        self.session_write_timeout_ms = int(session_write_timeout_ms)
        self.capture_once_timeout_ms = int(capture_once_timeout_ms)

    def snapshot(
        self,
        *,
        session_id: str,
        session: Mapping[str, Any] | None = None,
        current_window_handle: Any = None,
        active_study_started_epoch: float = 0.0,
        reason: str = "",
    ) -> dict[str, Any]:
        frames: dict[str, list[str]] = {}
        for thread_id, frame in sys._current_frames().items():
            frames[str(thread_id)] = traceback.format_stack(frame)[-16:]
        health = CaptureWorkerV3Health.from_session(
            session or {"session_id": session_id},
            active_study_started_epoch=active_study_started_epoch,
        ).as_dict()
        return {
            "schema_version": CAPTURE_WATCHDOG_SCHEMA_VERSION,
            "session_id": session_id,
            "captured_epoch": time.time(),
            "reason": reason,
            "thresholds": {
                "screen_capture_timeout_ms": self.screen_capture_timeout_ms,
                "session_write_timeout_ms": self.session_write_timeout_ms,
                "capture_once_timeout_ms": self.capture_once_timeout_ms,
            },
            "current_window_handle": current_window_handle,
            "worker_health": health,
            "thread_stacks": frames,
        }


def _stage_elapsed_ms(pipeline_timing: Mapping[str, Any], stage_name: str) -> int:
    for row in _sequence_of_mappings(pipeline_timing.get("stages")):
        if _text(row.get("stage")).lower() == stage_name.lower():
            elapsed_sec = _float(row.get("elapsed_sec"), 0.0)
            if elapsed_sec > 0.0:
                return int(elapsed_sec * 1000.0)
    return 0


def _stage_duration_ms(pipeline_timing: Mapping[str, Any], stage_names: Sequence[str]) -> float:
    wanted = {name.lower() for name in stage_names}
    total = 0.0
    for row in _sequence_of_mappings(pipeline_timing.get("stages")):
        if _text(row.get("stage")).lower() in wanted:
            total += max(0.0, _float(row.get("duration_sec"), 0.0) * 1000.0)
    return round(total, 3)


def _overlay_version(overlays: Sequence[Mapping[str, Any]], frame_id: int) -> str:
    seed = "|".join(
        f"{row.get('overlay_id') or row.get('id')}:{row.get('type')}:{row.get('bounds') or row.get('bbox')}"
        for row in overlays
    )
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"ov_{int(frame_id)}_{len(overlays)}_{digest}"


@dataclass
class FrameTimingTraceV3:
    frame_id: int
    capture_epoch_ms: int
    preprocess_start_ms: int = 0
    inference_start_ms: int = 0
    inference_done_ms: int = 0
    overlay_done_ms: int = 0
    state_published_ms: int = 0
    frontend_loaded_ms: int = 0
    frontend_overlay_drawn_ms: int = 0
    frames_dropped: int = 0
    queue_depth: int = 0
    freshness_score: float = 1.0
    source: str = "session_pipeline_timing"

    def as_dict(self, *, now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms if now_ms is not None else _now_ms())
        published = self.state_published_ms or self.overlay_done_ms or self.inference_done_ms or self.capture_epoch_ms
        overlay_done = self.overlay_done_ms or published
        model_done = self.inference_done_ms or overlay_done
        frontend_drawn = self.frontend_overlay_drawn_ms or self.frontend_loaded_ms or 0
        return {
            "schema_version": FRAME_TIMING_TRACE_SCHEMA_VERSION,
            "frame_id": int(self.frame_id),
            "capture_epoch_ms": int(self.capture_epoch_ms),
            "preprocess_start_ms": int(self.preprocess_start_ms),
            "inference_start_ms": int(self.inference_start_ms),
            "inference_done_ms": int(self.inference_done_ms),
            "overlay_done_ms": int(self.overlay_done_ms),
            "state_published_ms": int(self.state_published_ms),
            "frontend_loaded_ms": int(self.frontend_loaded_ms),
            "frontend_overlay_drawn_ms": int(self.frontend_overlay_drawn_ms),
            "frame_age_ms": max(0, now - int(self.capture_epoch_ms)) if self.capture_epoch_ms else 0,
            "overlay_age_ms": max(0, now - int(overlay_done)) if overlay_done else 0,
            "model_vote_age_ms": max(0, now - int(model_done)) if model_done else 0,
            "frontend_render_age_ms": max(0, now - int(frontend_drawn)) if frontend_drawn else 0,
            "state_publish_age_ms": max(0, now - int(published)) if published else 0,
            "frames_dropped": int(self.frames_dropped),
            "queue_depth": int(self.queue_depth),
            "freshness_score": round(max(0.0, min(1.0, float(self.freshness_score))), 4),
            "source": self.source,
        }


class LatestFrameBufferV3:
    """Small latest-frame-wins buffer. Old unprocessed frames are dropped."""

    def __init__(self, buffer_size: int = 3) -> None:
        self.buffer_size = max(2, min(3, int(buffer_size or 3)))
        self._frames: deque[dict[str, Any]] = deque(maxlen=self.buffer_size)
        self._lock = threading.Lock()
        self.frames_received = 0
        self.frames_dropped = 0
        self.frames_processed = 0

    def write(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            if len(self._frames) >= self.buffer_size:
                self.frames_dropped += max(0, len(self._frames) - 1)
                newest = self._frames[-1]
                self._frames.clear()
                self._frames.append(newest)
            row = dict(frame)
            row.setdefault("capture_epoch_ms", _now_ms())
            self._frames.append(row)
            self.frames_received += 1
            return row

    def read_latest(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._frames:
                return None
            latest = self._frames.pop()
            dropped = len(self._frames)
            self.frames_dropped += dropped
            self._frames.clear()
            self.frames_processed += 1
            return dict(latest)

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            depth = len(self._frames)
        received = max(1, self.frames_received)
        return {
            "schema_version": LATEST_FRAME_BUFFER_SCHEMA_VERSION,
            "buffer_size": self.buffer_size,
            "queue_depth": depth,
            "frames_received": int(self.frames_received),
            "frames_processed": int(self.frames_processed),
            "frames_dropped": int(self.frames_dropped),
            "freshness_score": round(max(0.0, 1.0 - (self.frames_dropped / received)), 4),
            "policy": "latest_frame_wins",
        }


class AsyncArtifactWriterV3:
    def __init__(self, *, max_workers: int = 1, max_pending: int = 3) -> None:
        self.max_pending = max(1, int(max_pending or 3))
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers or 1)), thread_name_prefix="pg-artifact-v3")
        self._futures: deque[Future[Any]] = deque()
        self.submitted = 0
        self.dropped = 0
        self.completed = 0
        self._lock = threading.Lock()

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future[T] | None:
        with self._lock:
            self._futures = deque([future for future in self._futures if not future.done()])
            if len(self._futures) >= self.max_pending:
                self.dropped += 1
                return None
            future = self._executor.submit(fn, *args, **kwargs)
            self._futures.append(future)
            self.submitted += 1
            return future

    def drain_completed(self) -> int:
        with self._lock:
            completed = [future for future in self._futures if future.done()]
            self._futures = deque([future for future in self._futures if not future.done()])
            self.completed += len(completed)
            return len(completed)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def as_dict(self) -> dict[str, Any]:
        self.drain_completed()
        with self._lock:
            pending = len(self._futures)
        return {
            "schema_version": ASYNC_ARTIFACT_WRITER_SCHEMA_VERSION,
            "pending": pending,
            "submitted": int(self.submitted),
            "completed": int(self.completed),
            "dropped": int(self.dropped),
            "max_pending": int(self.max_pending),
        }


@dataclass(frozen=True)
class RealtimeBackpressureControllerV3:
    stale_limit_ms: float = DEFAULT_SPEED_BUDGETS_MS["hard_stale"]
    reject_limit_ms: float = DEFAULT_SPEED_BUDGETS_MS["hard_reject"]
    max_queue_depth: int = 1

    def evaluate(self, *, frame_age_ms: float, overlay_age_ms: float, model_vote_age_ms: float, queue_depth: int = 0) -> dict[str, Any]:
        actions: list[str] = []
        stale: list[str] = []
        if int(queue_depth) > self.max_queue_depth:
            actions.append("drop_old_inference_jobs")
        for key, age in (
            ("frame", frame_age_ms),
            ("overlay", overlay_age_ms),
            ("model_vote", model_vote_age_ms),
        ):
            if age > self.reject_limit_ms:
                actions.append(f"reject_stale_{key}")
                stale.append(key)
            elif age > self.stale_limit_ms:
                actions.append(f"mark_stale_{key}")
                stale.append(key)
        return {
            "schema_version": BACKPRESSURE_SCHEMA_VERSION,
            "status": "REJECT" if any(action.startswith("reject") for action in actions) else "STALE" if stale else "PASS",
            "actions": actions,
            "stale_components": stale,
            "max_queue_depth": int(self.max_queue_depth),
            "stale_limit_ms": float(self.stale_limit_ms),
            "reject_limit_ms": float(self.reject_limit_ms),
        }


@dataclass(frozen=True)
class AdaptivePerformanceControllerV3:
    default_profile: str = "BALANCED"

    def choose_profile(
        self,
        *,
        p95_frame_age_ms: float = 0.0,
        clarity_score: float = 1.0,
        inference_bottleneck: bool = False,
        frontend_bottleneck: bool = False,
    ) -> dict[str, Any]:
        profile = _text(self.default_profile, "BALANCED").upper()
        reasons: list[str] = []
        if p95_frame_age_ms > DEFAULT_SPEED_BUDGETS_MS["live_visual_age_target"] or frontend_bottleneck:
            profile = "FAST"
            reasons.append("speed_budget_pressure")
        if clarity_score < 0.45 and not inference_bottleneck:
            profile = "HIGH_CLARITY"
            reasons.append("clarity_low")
        if inference_bottleneck and profile != "FAST":
            reasons.append("stagger_non_critical_models")
        details = dict(DISPLAY_QUALITY_PROFILES.get(profile, DISPLAY_QUALITY_PROFILES["BALANCED"]))
        return {
            "schema_version": ADAPTIVE_PERFORMANCE_SCHEMA_VERSION,
            "profile": profile,
            "details": details,
            "reasons": reasons,
            "rule": "reduce display quality and overlay density before reducing model crop quality",
        }


@dataclass(frozen=True)
class ModelWarmStateV3:
    model_name: str
    status: str = "AWAKE"
    last_inference_frame_id: int = 0
    last_inference_age_ms: float = 0.0
    average_inference_ms: float = 0.0
    p95_inference_ms: float = 0.0
    queue_depth: int = 0
    device: str = "unknown"
    warm: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MODEL_WARM_STATE_SCHEMA_VERSION,
            "model_name": self.model_name,
            "role_name": self.model_name,
            "status": self.status,
            "last_inference_frame_id": int(self.last_inference_frame_id),
            "last_inference_age_ms": round(float(self.last_inference_age_ms), 3),
            "average_inference_ms": round(float(self.average_inference_ms), 3),
            "p95_inference_ms": round(float(self.p95_inference_ms), 3),
            "queue_depth": int(self.queue_depth),
            "device": self.device,
            "warm": bool(self.warm),
        }


def model_warm_states_from_health(model_health: Mapping[str, Any], *, frame_id: int, now_epoch: float | None = None) -> list[dict[str, Any]]:
    now = float(now_epoch if now_epoch is not None else time.time())
    models = _sequence_of_mappings(model_health.get("models"))
    if not models and model_health.get("all_required_models_awake") is True:
        roles = [str(item) for item in cast(Sequence[Any], model_health.get("required_roles") or [])] or [
            "global_structure",
            "local_micro_structure",
            "zone_liquidity",
            "angle_dynamics",
            "historical_pattern",
            "risk_opposing_force",
            "arbitration_synthesis",
        ]
        models = [
            {
                "name": role,
                "role": role,
                "status": "AWAKE",
                "latency_ms": _float(model_health.get("max_model_latency_ms"), 0.0),
                "queue_depth": _int(model_health.get("queue_depth"), 0),
                "last_inference_epoch": now,
                "device": _text(model_health.get("device"), "unknown"),
            }
            for role in roles
        ]
    rows: list[dict[str, Any]] = []
    for row in models:
        latency = _float(row.get("latency_ms") or row.get("average_inference_ms"), 0.0)
        last_epoch = _float(row.get("last_inference_epoch") or row.get("last_heartbeat_epoch"), 0.0)
        rows.append(
            ModelWarmStateV3(
                model_name=_text(row.get("name") or row.get("role"), "model"),
                status=_text(row.get("status"), "AWAKE").upper(),
                last_inference_frame_id=_int(row.get("last_inference_frame_id"), frame_id),
                last_inference_age_ms=max(0.0, (now - last_epoch) * 1000.0) if last_epoch > 0.0 else 0.0,
                average_inference_ms=latency,
                p95_inference_ms=_float(row.get("p95_inference_ms"), latency),
                queue_depth=_int(row.get("queue_depth"), 0),
                device=_text(row.get("device"), "unknown"),
                warm=_text(row.get("status"), "AWAKE").upper() in {"AWAKE", "BUSY", "IDLE_BUT_LOADED"},
            ).as_dict()
        )
    return rows


def build_frame_timing_trace_v3(
    session: Mapping[str, Any],
    *,
    overlays: Sequence[Mapping[str, Any]] | None = None,
    model_health: Mapping[str, Any] | None = None,
    frontend_heartbeat: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now_ms = int(float(now_epoch if now_epoch is not None else time.time()) * 1000.0)
    tracking = _mapping(session.get("tracking_summary"))
    signal = _mapping(session.get("latest_signal"))
    pipeline = _mapping(tracking.get("pipeline_timing") or signal.get("pipeline_timing"))
    model_capture_ms = _epoch_to_ms(
        pipeline.get("capture_started_epoch")
        or signal.get("capture_started_epoch")
        or session.get("last_capture_started_epoch")
        or session.get("last_capture_epoch")
    )
    display_capture_ms = _epoch_to_ms(
        session.get("display_capture_epoch")
        or session.get("display_capture_started_epoch")
        or session.get("last_display_capture_epoch")
    )
    display_published_ms = _epoch_to_ms(
        session.get("display_published_epoch")
        or session.get("last_display_published_epoch")
    )
    capture_ms = display_published_ms or display_capture_ms or model_capture_ms
    published_ms = _epoch_to_ms(pipeline.get("published_epoch") or signal.get("published_epoch") or session.get("last_capture_epoch")) or now_ms
    stage_base_ms = model_capture_ms or capture_ms
    preprocess_start_ms = stage_base_ms + _stage_elapsed_ms(pipeline, "capture_window") if stage_base_ms else 0
    inference_start_ms = stage_base_ms + _stage_elapsed_ms(pipeline, "derive_study_surface") if stage_base_ms else 0
    inference_done_ms = stage_base_ms + _stage_elapsed_ms(pipeline, "tracker_study") if stage_base_ms else published_ms
    overlay_done_ms = stage_base_ms + _stage_elapsed_ms(pipeline, "artifact_write") if stage_base_ms else published_ms
    heartbeat = _mapping(frontend_heartbeat)
    display_frame_id = _int(session.get("display_frame_id") or session.get("frame_index") or session.get("frame_id") or session.get("capture_count"))
    model_frame_id = _int(session.get("model_vote_frame_id") or session.get("frame_index") or session.get("frame_id") or session.get("capture_count"))
    overlay_frame_id = _int(session.get("overlay_frame_id") or session.get("frame_index") or session.get("frame_id") or session.get("capture_count"))
    heartbeat_frame_id = _int(heartbeat.get("rendered_frame_id") or heartbeat.get("frame_id"))
    heartbeat_drawn_ms = _int(heartbeat.get("frontend_overlay_drawn_ms") or heartbeat.get("overlay_drawn_ms"))
    heartbeat_loaded_ms = _int(heartbeat.get("frontend_loaded_ms") or heartbeat.get("image_loaded_ms"))
    heartbeat_render_ms = heartbeat_drawn_ms or heartbeat_loaded_ms
    if heartbeat_frame_id and display_frame_id and heartbeat_frame_id + 1 < display_frame_id:
        heartbeat_loaded_ms = 0
        heartbeat_render_ms = 0
    if display_published_ms and display_frame_id:
        inference_done_ms = max(inference_done_ms, display_published_ms)
        overlay_done_ms = max(overlay_done_ms, display_published_ms)
    trace = FrameTimingTraceV3(
        frame_id=display_frame_id,
        capture_epoch_ms=capture_ms or published_ms,
        preprocess_start_ms=preprocess_start_ms,
        inference_start_ms=inference_start_ms,
        inference_done_ms=inference_done_ms,
        overlay_done_ms=overlay_done_ms,
        state_published_ms=published_ms,
        frontend_loaded_ms=heartbeat_loaded_ms,
        frontend_overlay_drawn_ms=heartbeat_render_ms,
        frames_dropped=_int(_mapping(model_health).get("dropped_frames") or tracking.get("frames_dropped") or signal.get("frames_dropped")),
        queue_depth=_int(_mapping(model_health).get("queue_depth")),
        freshness_score=max(0.0, min(1.0, 1.0 - max(0.0, (now_ms - (capture_ms or published_ms)) / DEFAULT_SPEED_BUDGETS_MS["hard_reject"]))),
    ).as_dict(now_ms=now_ms)
    overlay_state_version = _overlay_version(list(overlays or []), overlay_frame_id or int(trace["frame_id"]))
    backpressure = RealtimeBackpressureControllerV3().evaluate(
        frame_age_ms=float(trace["frame_age_ms"]),
        overlay_age_ms=float(trace["overlay_age_ms"]),
        model_vote_age_ms=float(trace["model_vote_age_ms"]),
        queue_depth=int(trace["queue_depth"]),
    )
    packet = _mapping(session.get("model_council_packet") or session.get("execution_packet"))
    packet_created_ms = _epoch_to_ms(packet.get("created_epoch") or packet.get("created_epoch_sec") or signal.get("published_epoch"))
    trace.update(
        {
            "overlay_state_version": overlay_state_version,
            "model_state_version": _text(_mapping(session.get("model_council_result")).get("packet_id") or packet.get("packet_id") or session.get("state_version")),
            "packet_age_ms": max(0, now_ms - packet_created_ms) if packet_created_ms else 0,
            "display_capture_epoch_ms": display_capture_ms,
            "display_published_epoch_ms": display_published_ms,
            "display_frame_id": display_frame_id,
            "overlay_frame_id": overlay_frame_id,
            "model_vote_frame_id": model_frame_id,
            "model_capture_epoch_ms": model_capture_ms,
            "stale_status": backpressure["status"],
            "stale_flags": list(backpressure["stale_components"]),
            "backpressure": backpressure,
        }
    )
    return trace


def build_performance_trace_v3(
    live_state: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now = float(now_epoch if now_epoch is not None else time.time())
    timing = _mapping(live_state.get("frame_timing_trace_v3") or live_state.get("frame_timing"))
    telemetry = _mapping(_mapping(live_state.get("model_health")).get("runtime_telemetry"))
    latency = _mapping(telemetry.get("latency"))
    queue = _mapping(telemetry.get("queue"))
    tracking = _mapping(live_state.get("tracking_summary"))
    signal = _mapping(live_state.get("latest_signal"))
    pipeline = _mapping(tracking.get("pipeline_timing") or signal.get("pipeline_timing"))
    frontend = _mapping(live_state.get("frontend_heartbeat"))
    model_health = _mapping(live_state.get("model_health"))
    model_warm_states = model_warm_states_from_health(model_health, frame_id=_int(live_state.get("frame_id")), now_epoch=now)
    frame_age = _float(timing.get("frame_age_ms"), 0.0)
    overlay_age = _float(timing.get("overlay_age_ms"), 0.0)
    model_age = _float(timing.get("model_vote_age_ms"), 0.0)
    frontend_age = _float(timing.get("frontend_render_age_ms"), 0.0)
    controller = AdaptivePerformanceControllerV3().choose_profile(
        p95_frame_age_ms=frame_age,
        clarity_score=1.0,
        inference_bottleneck=_float(latency.get("max_model_latency_ms"), 0.0) > DEFAULT_SPEED_BUDGETS_MS["model_inference"],
        frontend_bottleneck=frontend_age > DEFAULT_SPEED_BUDGETS_MS["frontend_render"] and frontend_age < DEFAULT_SPEED_BUDGETS_MS["hard_stale"],
    )
    metrics = {
        "capture_ms": _stage_duration_ms(pipeline, ("capture_window",)),
        "preprocess_ms": _stage_duration_ms(pipeline, ("derive_study_surface",)),
        "model_inference_ms": _stage_duration_ms(pipeline, ("tracker_study", "tracker_study_error")),
        "council_ms": _stage_duration_ms(pipeline, ("broker_execution", "scenario_analysis")),
        "overlay_ms": _stage_duration_ms(pipeline, ("artifact_write",)),
        "state_publish_ms": max(0.0, _float(timing.get("state_publish_age_ms"), 0.0)),
        "frontend_image_load_ms": _float(frontend.get("image_load_ms"), 0.0),
        "frontend_overlay_draw_ms": _float(frontend.get("overlay_draw_ms"), 0.0),
        "end_to_end_age_ms": frame_age,
    }
    windows = {
        "last_30s": {key: summarize_window([value]) for key, value in metrics.items()},
        "last_60s": {key: summarize_window([value]) for key, value in metrics.items()},
        "last_5m": {key: summarize_window([value]) for key, value in metrics.items()},
    }
    awake_count = sum(1 for row in model_warm_states if row.get("warm"))
    return {
        "schema_version": PERFORMANCE_TRACE_SCHEMA_VERSION,
        "session_id": _text(live_state.get("session_id")),
        "generated_epoch": now,
        "frame_id": _int(live_state.get("frame_id")),
        "state_version": _int(live_state.get("state_version")),
        "overlay_state_version": _text(timing.get("overlay_state_version")),
        "display_frame": {
            "frame_id": _int(timing.get("display_frame_id") or live_state.get("frame_id")),
            "age_ms": frame_age,
            "url": _text(_mapping(live_state.get("broker_surface")).get("url")),
        },
        "overlay_state": {
            "frame_id": _int(timing.get("overlay_frame_id") or live_state.get("frame_id")),
            "age_ms": overlay_age,
            "fresh": overlay_age <= DEFAULT_SPEED_BUDGETS_MS["hard_stale"],
            "overlay_state_version": _text(timing.get("overlay_state_version")),
        },
        "model_state": {
            "frame_id": _int(timing.get("model_vote_frame_id") or live_state.get("frame_id")),
            "age_ms": model_age,
            "fresh": model_age <= DEFAULT_SPEED_BUDGETS_MS["hard_stale"],
            "models_awake": awake_count,
            "models_total": len(model_warm_states),
            "queue_depth": _int(model_health.get("queue_depth") or queue.get("depth")),
        },
        "frontend_state": {
            "age_ms": frontend_age,
            "fresh": frontend_age <= DEFAULT_SPEED_BUDGETS_MS["hard_stale"] if frontend_age > 0.0 else False,
            "heartbeat": frontend,
        },
        "metrics": metrics,
        "percentiles": windows,
        "timing_trace": timing,
        "model_warm_state_v3": model_warm_states,
        "model_health_summary": {
            "label": f"{awake_count}/{len(model_warm_states)} awake",
            "slowest_model_ms": max((_float(row.get("p95_inference_ms"), 0.0) for row in model_warm_states), default=0.0),
            "queue_depth": _int(model_health.get("queue_depth") or queue.get("depth")),
        },
        "adaptive_performance": controller,
        "display_quality_profiles": DISPLAY_QUALITY_PROFILES,
        "overlay_render_budget": OVERLAY_RENDER_BUDGETS,
        "speed_budgets_ms": DEFAULT_SPEED_BUDGETS_MS,
        "visual_health": {
            "status": "ALIVE" if _text(timing.get("stale_status"), "PASS") == "PASS" else _text(timing.get("stale_status")),
            "frame_age_ms": frame_age,
            "overlay_age_ms": overlay_age,
            "model_vote_age_ms": model_age,
            "packet_age_ms": _float(timing.get("packet_age_ms"), 0.0),
            "frontend_render_age_ms": frontend_age,
            "stale_flags": list(cast(Sequence[Any], timing.get("stale_flags") or [])),
        },
    }


def write_json_report(path: Path | str, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return target


__all__ = [
    "ADAPTIVE_PERFORMANCE_SCHEMA_VERSION",
    "ASYNC_ARTIFACT_WRITER_SCHEMA_VERSION",
    "BACKPRESSURE_SCHEMA_VERSION",
    "DEFAULT_SPEED_BUDGETS_MS",
    "DISPLAY_QUALITY_PROFILES",
    "FRAME_TIMING_TRACE_SCHEMA_VERSION",
    "LATEST_FRAME_BUFFER_SCHEMA_VERSION",
    "MODEL_WARM_STATE_SCHEMA_VERSION",
    "OVERLAY_RENDER_BUDGETS",
    "PERFORMANCE_TRACE_SCHEMA_VERSION",
    "SESSION_ATOMIC_WRITER_SCHEMA_VERSION",
    "SESSION_FRESHNESS_VALIDATOR_SCHEMA_VERSION",
    "CAPTURE_WORKER_HEALTH_SCHEMA_VERSION",
    "CAPTURE_WATCHDOG_SCHEMA_VERSION",
    "AdaptivePerformanceControllerV3",
    "AsyncArtifactWriterV3",
    "CaptureWatchdogV3",
    "CaptureWorkerV3Health",
    "FrameTimingTraceV3",
    "LatestFrameBufferV3",
    "ModelWarmStateV3",
    "RealtimeBackpressureControllerV3",
    "SessionAtomicWriterV3",
    "SessionFreshnessValidatorV3",
    "build_frame_timing_trace_v3",
    "build_performance_trace_v3",
    "model_warm_states_from_health",
    "summarize_window",
    "write_json_report",
]
