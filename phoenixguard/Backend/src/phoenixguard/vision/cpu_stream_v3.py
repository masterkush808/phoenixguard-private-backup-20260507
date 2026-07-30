"""Bounded CPU-only temporal observation for PhoenixGuard V3 screen streams.

The observer deliberately does not perform trade inference.  It converts a
potentially high-rate sequence of screen frames into immutable, lineage-bound
keyframe decisions that the existing closed-candle study pipeline may choose
to consume.  All retained state is bounded by :class:`CPUStreamConfig`.

Only Pillow and NumPy are used.  In particular, this module does not require a
GPU, a video codec, or OpenCV.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import threading
from typing import cast
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from PIL import Image


CPU_STREAM_DECISION_SCHEMA_VERSION = "PG_CPU_STREAM_DECISION_V3"
CPU_STREAM_TEMPORAL_EVIDENCE_SCHEMA_VERSION = "PG_CPU_STREAM_TEMPORAL_EVIDENCE_V3"
CPU_STREAM_HEALTH_SCHEMA_VERSION = "PG_CPU_STREAM_HEALTH_V3"


@dataclass(frozen=True, slots=True)
class CPUStreamConfig:
    """Resource and event-selection limits for :class:`CPUStreamObserver`."""

    full_frame_capacity: int = 2
    downsample_ring_capacity: int = 48
    downsample_size: tuple[int, int] = (128, 72)
    max_frame_pixels: int = 16_777_216
    pixel_change_threshold: float = 8.0 / 255.0
    duplicate_mean_change_threshold: float = 0.0015
    duplicate_changed_pixel_ratio_threshold: float = 0.003
    rest_motion_score_threshold: float = 0.018
    material_motion_score_threshold: float = 0.075
    material_changed_pixel_ratio_threshold: float = 0.10
    keyframe_min_interval_sec: float = 0.25
    heartbeat_interval_sec: float = 5.0

    def __post_init__(self) -> None:
        if self.full_frame_capacity < 1:
            raise ValueError("full_frame_capacity must be at least 1.")
        if self.downsample_ring_capacity < 2:
            raise ValueError("downsample_ring_capacity must be at least 2.")
        if len(self.downsample_size) != 2 or min(self.downsample_size) < 8:
            raise ValueError("downsample_size must contain width and height of at least 8 pixels.")
        if self.max_frame_pixels < 64 * 64:
            raise ValueError("max_frame_pixels must allow at least a 64x64 frame.")
        for name in (
            "pixel_change_threshold",
            "duplicate_mean_change_threshold",
            "duplicate_changed_pixel_ratio_threshold",
            "rest_motion_score_threshold",
            "material_motion_score_threshold",
            "material_changed_pixel_ratio_threshold",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a finite value between 0 and 1.")
        if self.duplicate_mean_change_threshold > self.rest_motion_score_threshold:
            raise ValueError("duplicate_mean_change_threshold cannot exceed rest_motion_score_threshold.")
        if self.rest_motion_score_threshold >= self.material_motion_score_threshold:
            raise ValueError("rest_motion_score_threshold must be below material_motion_score_threshold.")
        if not math.isfinite(self.keyframe_min_interval_sec) or self.keyframe_min_interval_sec < 0.0:
            raise ValueError("keyframe_min_interval_sec must be finite and non-negative.")
        if not math.isfinite(self.heartbeat_interval_sec) or self.heartbeat_interval_sec <= 0.0:
            raise ValueError("heartbeat_interval_sec must be finite and greater than zero.")
        if self.heartbeat_interval_sec < self.keyframe_min_interval_sec:
            raise ValueError("heartbeat_interval_sec cannot be shorter than keyframe_min_interval_sec.")


@dataclass(frozen=True, slots=True)
class CPUStreamDecision:
    """One frame's study-selection result and its temporal evidence."""

    accepted_for_study: bool
    reason: str
    frame_seq: int
    stream_id: str
    stream_generation: int
    input_frame_hash: str
    temporal_evidence: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": CPU_STREAM_DECISION_SCHEMA_VERSION,
            "accepted_for_study": self.accepted_for_study,
            "reason": self.reason,
            "frame_seq": self.frame_seq,
            "stream_id": self.stream_id,
            "stream_generation": self.stream_generation,
            "input_frame_hash": self.input_frame_hash,
            "temporal_evidence": deepcopy(self.temporal_evidence),
        }


@dataclass(slots=True)
class _FullFrameRecord:
    frame_seq: int
    captured_epoch: float
    sha256: str
    image: Image.Image


@dataclass(slots=True)
class _DownsampleRecord:
    frame_seq: int
    captured_epoch: float
    sha256: str
    grayscale: NDArray[np.uint8]
    motion_score: float
    state: str


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            str(key): _json_safe(item)
            for key, item in sorted(mapping.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast(Sequence[object], value)
        return [_json_safe(item) for item in sequence]
    return str(value)


def _stable_mapping_hash(value: object) -> str:
    encoded = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frame_hashes(image: Image.Image) -> tuple[str, str, bytes]:
    raw = image.tobytes()
    header = f"RGB\0{image.width}\0{image.height}\0".encode("ascii")
    sha256 = hashlib.sha256()
    sha256.update(header)
    sha256.update(raw)
    blake2b = hashlib.blake2b(digest_size=32)
    blake2b.update(header)
    blake2b.update(raw)
    return sha256.hexdigest(), blake2b.hexdigest(), raw


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


class CPUStreamObserver:
    """Observe screen frames with bounded memory and event-driven keyframes.

    ``push`` is serialized by an internal lock.  The sequence number therefore
    remains strictly increasing even when producers call it concurrently.
    Identity or geometry changes start a new generation and clear temporal
    history before the new frame is admitted.
    """

    def __init__(
        self,
        config: CPUStreamConfig | None = None,
        *,
        stream_id: str = "",
    ) -> None:
        self.config = config or CPUStreamConfig()
        self._stream_id = str(stream_id or "").strip() or f"cpu-stream-{uuid4().hex}"
        self._lock = threading.RLock()
        self._stream_generation = 1
        self._frame_seq = 0
        self._full_frames: deque[_FullFrameRecord] = deque()
        self._downsample_frames: deque[_DownsampleRecord] = deque()
        self._identity_hash = ""
        self._identity: object = {}
        self._geometry: dict[str, object] = {}
        self._geometry_hash = ""
        self._last_captured_epoch = 0.0
        self._last_frame_hash = ""
        self._last_frame_blake2b = ""
        self._last_keyframe_epoch = 0.0
        self._last_keyframe_seq = 0
        self._last_keyframe_hash = ""
        self._last_motion_score = 0.0
        self._last_decision: CPUStreamDecision | None = None
        self._pending_generation_reason = "stream_start"
        self._state = "idle"
        self._state_started_epoch = 0.0
        self._duplicate_streak = 0
        self._rest_streak = 0
        self._motion_streak = 0
        self._counters = self._new_counters()

    @staticmethod
    def _new_counters() -> dict[str, int]:
        return {
            "frames_received": 0,
            "frames_observed": 0,
            "keyframes_selected": 0,
            "duplicate_frames": 0,
            "rest_frames": 0,
            "motion_frames": 0,
            "material_change_frames": 0,
            "material_keyframes": 0,
            "heartbeat_keyframes": 0,
            "visible_recovery_keyframes": 0,
            "identity_resets": 0,
            "geometry_resets": 0,
            "manual_resets": 0,
            "non_monotonic_rejections": 0,
            "full_frame_ring_drops": 0,
            "downsample_ring_drops": 0,
            "latest_frame_wins_drops": 0,
            "throttled_material_frames": 0,
            "coalesced_material_frames": 0,
        }

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def stream_generation(self) -> int:
        with self._lock:
            return self._stream_generation

    def reset(self) -> None:
        """Clear temporal history while preserving monotonic stream lineage."""

        with self._lock:
            self._stream_generation += 1
            self._counters["manual_resets"] += 1
            self._clear_generation_state(reason="manual_reset")

    def push(
        self,
        image: object,
        *,
        captured_epoch: float,
        identity: Mapping[str, object] | str,
        allow_heartbeat: bool = True,
        allow_study_keyframe: bool = True,
        force_study_keyframe: bool = False,
    ) -> CPUStreamDecision:
        """Observe one frame and decide whether it is a V3 study keyframe."""

        if not isinstance(image, Image.Image):
            raise TypeError("image must be a Pillow Image.")
        epoch = float(captured_epoch)
        if not math.isfinite(epoch) or epoch < 0.0:
            raise ValueError("captured_epoch must be finite and non-negative.")
        if image.width < 1 or image.height < 1:
            raise ValueError("image must have positive geometry.")
        pixel_count = int(image.width) * int(image.height)
        if pixel_count > self.config.max_frame_pixels:
            raise ValueError(
                f"image contains {pixel_count} pixels, above max_frame_pixels={self.config.max_frame_pixels}."
            )

        with self._lock:
            self._frame_seq += 1
            frame_seq = self._frame_seq
            self._counters["frames_received"] += 1
            rgb = image.convert("RGB")
            sha256, blake2b, _raw = _frame_hashes(rgb)
            normalized_identity = _json_safe(identity)
            identity_hash = _stable_mapping_hash(normalized_identity)
            geometry: dict[str, object] = {"width": rgb.width, "height": rgb.height, "mode": "RGB"}
            geometry_hash = _stable_mapping_hash(geometry)

            if self._last_captured_epoch > 0.0 and epoch <= self._last_captured_epoch:
                self._counters["non_monotonic_rejections"] += 1
                decision = self._rejected_timestamp_decision(
                    frame_seq=frame_seq,
                    captured_epoch=epoch,
                    sha256=sha256,
                    blake2b=blake2b,
                    identity_hash=identity_hash,
                    geometry=geometry,
                )
                self._last_decision = decision
                return decision

            reset_reason = self._pending_generation_reason
            identity_changed = bool(self._identity_hash and identity_hash != self._identity_hash)
            geometry_changed = bool(self._geometry_hash and geometry_hash != self._geometry_hash)
            if identity_changed or geometry_changed:
                self._stream_generation += 1
                if identity_changed:
                    self._counters["identity_resets"] += 1
                if geometry_changed:
                    self._counters["geometry_resets"] += 1
                if identity_changed and geometry_changed:
                    reset_reason = "identity_geometry_reset"
                elif identity_changed:
                    reset_reason = "identity_reset"
                else:
                    reset_reason = "geometry_reset"
                self._clear_generation_state(reason=reset_reason)

            self._identity = normalized_identity
            self._identity_hash = identity_hash
            self._geometry = geometry
            self._geometry_hash = geometry_hash
            previous = self._downsample_frames[-1] if self._downsample_frames else None
            grayscale = self._downsample_grayscale(rgb)

            if previous is None:
                state = "keyframe"
                metrics = self._neutral_metrics()
                accepted = True
                reason = reset_reason or "stream_start"
            else:
                metrics = self._change_metrics(previous.grayscale, grayscale)
                state = self._classify_state(
                    sha256=sha256,
                    previous_sha256=previous.sha256,
                    metrics=metrics,
                )
                accepted, reason = self._select_keyframe(
                    state=state,
                    captured_epoch=epoch,
                    allow_heartbeat=allow_heartbeat,
                    allow_study_keyframe=allow_study_keyframe,
                    force_study_keyframe=force_study_keyframe,
                )

            self._append_full_frame(frame_seq, epoch, sha256, rgb)
            self._append_downsample(frame_seq, epoch, sha256, grayscale, metrics, state)
            self._counters["frames_observed"] += 1
            self._update_state_tracking(state, epoch)
            if accepted:
                self._record_keyframe(reason, frame_seq, epoch, sha256)

            previous_epoch = previous.captured_epoch if previous is not None else 0.0
            previous_hash = previous.sha256 if previous is not None else ""
            evidence = self._temporal_evidence(
                frame_seq=frame_seq,
                captured_epoch=epoch,
                previous_captured_epoch=previous_epoch,
                sha256=sha256,
                blake2b=blake2b,
                previous_hash=previous_hash,
                identity_hash=identity_hash,
                geometry=geometry,
                state=state,
                accepted=accepted,
                reason=reason,
                metrics=metrics,
            )
            decision = CPUStreamDecision(
                accepted_for_study=accepted,
                reason=reason,
                frame_seq=frame_seq,
                stream_id=self._stream_id,
                stream_generation=self._stream_generation,
                input_frame_hash=sha256,
                temporal_evidence=evidence,
            )
            self._last_captured_epoch = epoch
            self._last_frame_hash = sha256
            self._last_frame_blake2b = blake2b
            self._last_motion_score = float(cast(float, metrics["motion_score"]))
            self._pending_generation_reason = ""
            self._last_decision = decision
            return decision

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-safe public health and resource snapshot."""

        with self._lock:
            downsample_width, downsample_height = self.config.downsample_size
            full_bytes = sum(record.image.width * record.image.height * 3 for record in self._full_frames)
            sample_bytes = sum(int(record.grayscale.nbytes) for record in self._downsample_frames)
            configured_upper_bound = (
                self.config.full_frame_capacity * self.config.max_frame_pixels * 3
                + self.config.downsample_ring_capacity * downsample_width * downsample_height
            )
            return {
                "schema_version": CPU_STREAM_HEALTH_SCHEMA_VERSION,
                "status": "healthy" if self._downsample_frames else "idle",
                "cpu_only": True,
                "stream_id": self._stream_id,
                "stream_generation": self._stream_generation,
                "frame_seq": self._frame_seq,
                "last_captured_epoch": self._last_captured_epoch,
                "last_frame_hash": self._last_frame_hash,
                "last_frame_blake2b": self._last_frame_blake2b,
                "last_keyframe_seq": self._last_keyframe_seq,
                "last_keyframe_epoch": self._last_keyframe_epoch,
                "last_keyframe_hash": self._last_keyframe_hash,
                "identity": deepcopy(self._identity),
                "identity_hash": self._identity_hash,
                "geometry": dict(self._geometry),
                "geometry_hash": self._geometry_hash,
                "state": self._state,
                "streaks": {
                    "duplicate_frames": self._duplicate_streak,
                    "rest_frames": self._rest_streak,
                    "motion_frames": self._motion_streak,
                    "state_duration_sec": self._state_duration(self._last_captured_epoch),
                },
                "rings": {
                    "full_frames": {
                        "size": len(self._full_frames),
                        "capacity": self.config.full_frame_capacity,
                        "dropped": self._counters["full_frame_ring_drops"],
                    },
                    "downsamples": {
                        "size": len(self._downsample_frames),
                        "capacity": self.config.downsample_ring_capacity,
                        "dropped": self._counters["downsample_ring_drops"],
                    },
                },
                "memory": {
                    "current_full_frame_bytes": full_bytes,
                    "current_downsample_bytes": sample_bytes,
                    "current_estimated_pixel_bytes": full_bytes + sample_bytes,
                    "configured_upper_bound_pixel_bytes": configured_upper_bound,
                    "max_frame_pixels": self.config.max_frame_pixels,
                },
                "counters": dict(self._counters),
                "last_decision": self._last_decision.as_dict() if self._last_decision is not None else {},
            }

    def _clear_generation_state(self, *, reason: str) -> None:
        self._full_frames.clear()
        self._downsample_frames.clear()
        self._identity_hash = ""
        self._identity = {}
        self._geometry = {}
        self._geometry_hash = ""
        self._last_captured_epoch = 0.0
        self._last_frame_hash = ""
        self._last_frame_blake2b = ""
        self._last_keyframe_epoch = 0.0
        self._last_keyframe_seq = 0
        self._last_keyframe_hash = ""
        self._last_motion_score = 0.0
        self._last_decision = None
        self._pending_generation_reason = reason
        self._state = "idle"
        self._state_started_epoch = 0.0
        self._duplicate_streak = 0
        self._rest_streak = 0
        self._motion_streak = 0

    def _downsample_grayscale(self, image: Image.Image) -> NDArray[np.uint8]:
        sample = image.convert("L").resize(self.config.downsample_size, Image.Resampling.BILINEAR)
        grayscale = np.asarray(sample, dtype=np.uint8).copy()
        grayscale.setflags(write=False)
        return grayscale

    @staticmethod
    def _neutral_metrics() -> dict[str, object]:
        return {
            "mean_abs_change": 0.0,
            "rms_change": 0.0,
            "p95_abs_change": 0.0,
            "max_abs_change": 0.0,
            "changed_pixel_ratio": 0.0,
            "motion_score": 0.0,
            "apparent_shift_x": 0.0,
            "apparent_shift_y": 0.0,
            "motion_bbox_normalized": [],
            "motion_centroid_normalized": [],
            "wick_motion": {
                "upper_activity": 0.0,
                "body_activity": 0.0,
                "lower_activity": 0.0,
                "upper_rejection_pressure": 0.0,
                "lower_rejection_pressure": 0.0,
                "dominant_extreme": "NONE",
                "vertical_span_ratio": 0.0,
                "wick_like_column_ratio": 0.0,
                "top_changed_y_normalized": None,
                "bottom_changed_y_normalized": None,
            },
        }

    def _change_metrics(
        self,
        previous: NDArray[np.uint8],
        current: NDArray[np.uint8],
    ) -> dict[str, object]:
        previous_i16 = previous.astype(np.int16, copy=False)
        current_i16 = current.astype(np.int16, copy=False)
        signed = current_i16 - previous_i16
        diff = np.abs(signed).astype(np.float32) / 255.0
        changed = diff >= float(self.config.pixel_change_threshold)
        mean_abs = float(np.mean(diff))
        rms = float(np.sqrt(np.mean(np.square(diff, dtype=np.float32))))
        p95 = float(np.percentile(diff, 95.0))
        max_abs = float(np.max(diff))
        changed_ratio = float(np.mean(changed))
        motion_score = _clip01(max(mean_abs * 2.0, rms, p95 * 0.5, changed_ratio * 0.5))

        motion_bbox: list[float] = []
        motion_centroid: list[float] = []
        vertical_span_ratio = 0.0
        top_changed: float | None = None
        bottom_changed: float | None = None
        rows, columns = np.nonzero(changed)
        height, width = diff.shape
        if rows.size:
            y0 = int(np.min(rows))
            y1 = int(np.max(rows))
            x0 = int(np.min(columns))
            x1 = int(np.max(columns))
            motion_bbox = [
                round(x0 / max(1, width - 1), 6),
                round(y0 / max(1, height - 1), 6),
                round(x1 / max(1, width - 1), 6),
                round(y1 / max(1, height - 1), 6),
            ]
            motion_centroid = [
                round(float(np.mean(columns)) / max(1, width - 1), 6),
                round(float(np.mean(rows)) / max(1, height - 1), 6),
            ]
            vertical_span_ratio = float((y1 - y0 + 1) / max(1, height))
            top_changed = float(y0 / max(1, height - 1))
            bottom_changed = float(y1 / max(1, height - 1))

        upper_end = max(1, height // 3)
        lower_start = min(height - 1, (height * 2) // 3)
        upper_activity = float(np.mean(diff[:upper_end]))
        body_activity = float(np.mean(diff[upper_end:lower_start])) if lower_start > upper_end else mean_abs
        lower_activity = float(np.mean(diff[lower_start:]))
        upper_pressure = max(0.0, upper_activity - body_activity)
        lower_pressure = max(0.0, lower_activity - body_activity)
        pressure_margin = max(0.001, mean_abs * 0.08)
        if upper_pressure > lower_pressure + pressure_margin:
            dominant_extreme = "UPPER"
        elif lower_pressure > upper_pressure + pressure_margin:
            dominant_extreme = "LOWER"
        elif rows.size:
            dominant_extreme = "BALANCED"
        else:
            dominant_extreme = "NONE"

        column_spans: list[float] = []
        for column in range(width):
            column_rows = np.flatnonzero(changed[:, column])
            if column_rows.size >= 2:
                column_spans.append(float(column_rows[-1] - column_rows[0] + 1) / max(1, height))
        wick_like_column_ratio = (
            float(np.mean(np.asarray(column_spans, dtype=np.float32) >= 0.16))
            if column_spans
            else 0.0
        )

        positive = np.clip(signed, 0, None).astype(np.float32)
        negative = np.clip(-signed, 0, None).astype(np.float32)
        apparent_shift_x, apparent_shift_y = self._apparent_shift(positive, negative)
        return {
            "mean_abs_change": round(mean_abs, 8),
            "rms_change": round(rms, 8),
            "p95_abs_change": round(p95, 8),
            "max_abs_change": round(max_abs, 8),
            "changed_pixel_ratio": round(changed_ratio, 8),
            "motion_score": round(motion_score, 8),
            "apparent_shift_x": round(apparent_shift_x, 8),
            "apparent_shift_y": round(apparent_shift_y, 8),
            "motion_bbox_normalized": motion_bbox,
            "motion_centroid_normalized": motion_centroid,
            "wick_motion": {
                "upper_activity": round(upper_activity, 8),
                "body_activity": round(body_activity, 8),
                "lower_activity": round(lower_activity, 8),
                "upper_rejection_pressure": round(upper_pressure, 8),
                "lower_rejection_pressure": round(lower_pressure, 8),
                "dominant_extreme": dominant_extreme,
                "vertical_span_ratio": round(vertical_span_ratio, 8),
                "wick_like_column_ratio": round(wick_like_column_ratio, 8),
                "top_changed_y_normalized": round(top_changed, 8) if top_changed is not None else None,
                "bottom_changed_y_normalized": round(bottom_changed, 8) if bottom_changed is not None else None,
            },
        }

    @staticmethod
    def _apparent_shift(
        positive: NDArray[np.float32],
        negative: NDArray[np.float32],
    ) -> tuple[float, float]:
        def centroid(weights: NDArray[np.float32]) -> tuple[float, float] | None:
            total = float(np.sum(weights))
            if total <= 1e-6:
                return None
            height, width = weights.shape
            xs = np.arange(width, dtype=np.float32)[None, :]
            ys = np.arange(height, dtype=np.float32)[:, None]
            return (
                float(np.sum(weights * xs) / total) / max(1, width - 1),
                float(np.sum(weights * ys) / total) / max(1, height - 1),
            )

        positive_center = centroid(positive)
        negative_center = centroid(negative)
        if positive_center is None or negative_center is None:
            return 0.0, 0.0
        return (
            max(-1.0, min(1.0, positive_center[0] - negative_center[0])),
            max(-1.0, min(1.0, positive_center[1] - negative_center[1])),
        )

    def _classify_state(
        self,
        *,
        sha256: str,
        previous_sha256: str,
        metrics: Mapping[str, object],
    ) -> str:
        mean_abs = float(cast(float, metrics["mean_abs_change"]))
        changed_ratio = float(cast(float, metrics["changed_pixel_ratio"]))
        motion_score = float(cast(float, metrics["motion_score"]))
        if sha256 == previous_sha256 or (
            mean_abs <= self.config.duplicate_mean_change_threshold
            and changed_ratio <= self.config.duplicate_changed_pixel_ratio_threshold
        ):
            return "duplicate"
        if (
            motion_score >= self.config.material_motion_score_threshold
            or changed_ratio >= self.config.material_changed_pixel_ratio_threshold
        ):
            return "material_change"
        if motion_score <= self.config.rest_motion_score_threshold:
            return "rest"
        return "motion"

    def _select_keyframe(
        self,
        *,
        state: str,
        captured_epoch: float,
        allow_heartbeat: bool,
        allow_study_keyframe: bool,
        force_study_keyframe: bool,
    ) -> tuple[bool, str]:
        elapsed = (
            captured_epoch - self._last_keyframe_epoch
            if self._last_keyframe_epoch > 0.0
            else math.inf
        )
        if (
            force_study_keyframe
            and allow_study_keyframe
            and state in {"rest", "motion", "material_change"}
        ):
            # The tracker may use this only after its bounded visible recovery
            # capture has already classified as fresh. Duplicate frames and
            # rejected timestamps never reach this admission branch, and the
            # observer remains direction/execution agnostic.
            return True, "visible_duplicate_recovery"
        if state == "material_change":
            if not allow_study_keyframe:
                self._counters["coalesced_material_frames"] += 1
                return False, "material_change_coalesced_during_study"
            if elapsed + 1e-12 >= self.config.keyframe_min_interval_sec:
                return True, "material_change"
            self._counters["throttled_material_frames"] += 1
            return False, "material_change_throttled"
        if (
            allow_study_keyframe
            and allow_heartbeat
            and elapsed + 1e-12 >= self.config.heartbeat_interval_sec
        ):
            return True, "heartbeat"
        if state == "duplicate":
            return False, "duplicate"
        if state == "rest":
            return False, "rest"
        return False, "motion_below_material_threshold"

    def _append_full_frame(
        self,
        frame_seq: int,
        captured_epoch: float,
        sha256: str,
        image: Image.Image,
    ) -> None:
        if len(self._full_frames) >= self.config.full_frame_capacity:
            self._full_frames.popleft()
            self._counters["full_frame_ring_drops"] += 1
            self._counters["latest_frame_wins_drops"] += 1
        self._full_frames.append(
            _FullFrameRecord(
                frame_seq=frame_seq,
                captured_epoch=captured_epoch,
                sha256=sha256,
                image=image.copy(),
            )
        )

    def _append_downsample(
        self,
        frame_seq: int,
        captured_epoch: float,
        sha256: str,
        grayscale: NDArray[np.uint8],
        metrics: Mapping[str, object],
        state: str,
    ) -> None:
        if len(self._downsample_frames) >= self.config.downsample_ring_capacity:
            self._downsample_frames.popleft()
            self._counters["downsample_ring_drops"] += 1
        self._downsample_frames.append(
            _DownsampleRecord(
                frame_seq=frame_seq,
                captured_epoch=captured_epoch,
                sha256=sha256,
                grayscale=grayscale,
                motion_score=float(cast(float, metrics["motion_score"])),
                state=state,
            )
        )

    def _update_state_tracking(self, state: str, captured_epoch: float) -> None:
        if state != self._state:
            self._state = state
            self._state_started_epoch = captured_epoch
        if state == "duplicate":
            self._duplicate_streak += 1
            self._rest_streak = 0
            self._motion_streak = 0
            self._counters["duplicate_frames"] += 1
        elif state == "rest":
            self._duplicate_streak = 0
            self._rest_streak += 1
            self._motion_streak = 0
            self._counters["rest_frames"] += 1
        elif state in {"motion", "material_change"}:
            self._duplicate_streak = 0
            self._rest_streak = 0
            self._motion_streak += 1
            self._counters["motion_frames"] += 1
            if state == "material_change":
                self._counters["material_change_frames"] += 1
        else:
            self._duplicate_streak = 0
            self._rest_streak = 0
            self._motion_streak = 0

    def _record_keyframe(
        self,
        reason: str,
        frame_seq: int,
        captured_epoch: float,
        sha256: str,
    ) -> None:
        self._last_keyframe_seq = frame_seq
        self._last_keyframe_epoch = captured_epoch
        self._last_keyframe_hash = sha256
        self._counters["keyframes_selected"] += 1
        if reason == "material_change":
            self._counters["material_keyframes"] += 1
        elif reason == "heartbeat":
            self._counters["heartbeat_keyframes"] += 1
        elif reason == "visible_duplicate_recovery":
            self._counters["visible_recovery_keyframes"] += 1

    def _state_duration(self, captured_epoch: float) -> float:
        if self._state_started_epoch <= 0.0 or captured_epoch < self._state_started_epoch:
            return 0.0
        return round(captured_epoch - self._state_started_epoch, 6)

    def _temporal_evidence(
        self,
        *,
        frame_seq: int,
        captured_epoch: float,
        previous_captured_epoch: float,
        sha256: str,
        blake2b: str,
        previous_hash: str,
        identity_hash: str,
        geometry: Mapping[str, object],
        state: str,
        accepted: bool,
        reason: str,
        metrics: Mapping[str, object],
    ) -> dict[str, object]:
        delta_sec = (
            max(0.0, captured_epoch - previous_captured_epoch)
            if previous_captured_epoch > 0.0
            else 0.0
        )
        motion_score = float(cast(float, metrics["motion_score"]))
        return {
            "schema_version": CPU_STREAM_TEMPORAL_EVIDENCE_SCHEMA_VERSION,
            "stream_id": self._stream_id,
            "stream_generation": self._stream_generation,
            "frame_seq": frame_seq,
            "captured_epoch": captured_epoch,
            "previous_captured_epoch": previous_captured_epoch,
            "frame_delta_sec": round(delta_sec, 6),
            "input_frame_hash": sha256,
            "input_frame_hash_algorithm": "sha256",
            "input_frame_blake2b": blake2b,
            "input_frame_blake2b_algorithm": "blake2b-256",
            "previous_frame_hash": previous_hash,
            "identity_hash": identity_hash,
            "geometry": dict(geometry),
            "state": state,
            "accepted_for_study": accepted,
            "selection_reason": reason,
            "change": {
                "mean_abs_change": metrics["mean_abs_change"],
                "rms_change": metrics["rms_change"],
                "p95_abs_change": metrics["p95_abs_change"],
                "max_abs_change": metrics["max_abs_change"],
                "changed_pixel_ratio": metrics["changed_pixel_ratio"],
            },
            "motion": {
                "state": state,
                "motion_score": metrics["motion_score"],
                "motion_acceleration": round(motion_score - self._last_motion_score, 8),
                "apparent_shift_x": metrics["apparent_shift_x"],
                "apparent_shift_y": metrics["apparent_shift_y"],
                "bbox_normalized": deepcopy(metrics["motion_bbox_normalized"]),
                "centroid_normalized": deepcopy(metrics["motion_centroid_normalized"]),
                "streak_frames": self._motion_streak,
            },
            "rest": {
                "active": state in {"duplicate", "rest"},
                "state": state if state in {"duplicate", "rest"} else "motion",
                "duplicate_streak_frames": self._duplicate_streak,
                "rest_streak_frames": self._rest_streak,
                "duration_sec": self._state_duration(captured_epoch),
            },
            "wick_motion": deepcopy(metrics["wick_motion"]),
            "keyframe": {
                "selected": accepted,
                "reason": reason,
                "forced_visible_recovery": (
                    reason == "visible_duplicate_recovery"
                ),
                "previous_keyframe_seq": self._last_keyframe_seq if not accepted else 0,
                "min_interval_sec": self.config.keyframe_min_interval_sec,
                "heartbeat_interval_sec": self.config.heartbeat_interval_sec,
            },
            "drop_counters": {
                "full_frame_ring_drops": self._counters["full_frame_ring_drops"],
                "downsample_ring_drops": self._counters["downsample_ring_drops"],
                "latest_frame_wins_drops": self._counters["latest_frame_wins_drops"],
            },
        }

    def _rejected_timestamp_decision(
        self,
        *,
        frame_seq: int,
        captured_epoch: float,
        sha256: str,
        blake2b: str,
        identity_hash: str,
        geometry: Mapping[str, object],
    ) -> CPUStreamDecision:
        evidence: dict[str, object] = {
            "schema_version": CPU_STREAM_TEMPORAL_EVIDENCE_SCHEMA_VERSION,
            "stream_id": self._stream_id,
            "stream_generation": self._stream_generation,
            "frame_seq": frame_seq,
            "captured_epoch": captured_epoch,
            "previous_captured_epoch": self._last_captured_epoch,
            "frame_delta_sec": round(captured_epoch - self._last_captured_epoch, 6),
            "input_frame_hash": sha256,
            "input_frame_hash_algorithm": "sha256",
            "input_frame_blake2b": blake2b,
            "input_frame_blake2b_algorithm": "blake2b-256",
            "previous_frame_hash": self._last_frame_hash,
            "identity_hash": identity_hash,
            "geometry": dict(geometry),
            "state": "rejected",
            "accepted_for_study": False,
            "selection_reason": "non_monotonic_capture_epoch",
            "change": {},
            "motion": {},
            "rest": {},
            "wick_motion": {},
            "keyframe": {
                "selected": False,
                "reason": "non_monotonic_capture_epoch",
                "previous_keyframe_seq": self._last_keyframe_seq,
                "min_interval_sec": self.config.keyframe_min_interval_sec,
                "heartbeat_interval_sec": self.config.heartbeat_interval_sec,
            },
            "drop_counters": {
                "full_frame_ring_drops": self._counters["full_frame_ring_drops"],
                "downsample_ring_drops": self._counters["downsample_ring_drops"],
                "latest_frame_wins_drops": self._counters["latest_frame_wins_drops"],
            },
        }
        return CPUStreamDecision(
            accepted_for_study=False,
            reason="non_monotonic_capture_epoch",
            frame_seq=frame_seq,
            stream_id=self._stream_id,
            stream_generation=self._stream_generation,
            input_frame_hash=sha256,
            temporal_evidence=evidence,
        )


__all__ = [
    "CPU_STREAM_DECISION_SCHEMA_VERSION",
    "CPU_STREAM_HEALTH_SCHEMA_VERSION",
    "CPU_STREAM_TEMPORAL_EVIDENCE_SCHEMA_VERSION",
    "CPUStreamConfig",
    "CPUStreamDecision",
    "CPUStreamObserver",
]
