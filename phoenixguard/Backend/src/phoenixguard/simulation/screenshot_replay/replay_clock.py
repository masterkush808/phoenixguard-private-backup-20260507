from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Mapping, cast


class ReplayMode(str, Enum):
    REAL_TIME_REPLAY = "REAL_TIME_REPLAY"
    FAST_REPLAY = "FAST_REPLAY"
    STEP_FRAME_BY_FRAME = "STEP_FRAME_BY_FRAME"
    BAD_ENTRY_REPLAY = "BAD_ENTRY_REPLAY"
    PAPER_EXECUTION_REPLAY = "PAPER_EXECUTION_REPLAY"
    OVERLAY_DEBUG_REPLAY = "OVERLAY_DEBUG_REPLAY"


_NO_WAIT_MODES = {ReplayMode.FAST_REPLAY, ReplayMode.STEP_FRAME_BY_FRAME}


def resolve_replay_mode(value: ReplayMode | str) -> ReplayMode:
    if isinstance(value, ReplayMode):
        return value
    return ReplayMode(str(value).strip().upper())


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


@dataclass(frozen=True)
class ReplaySpeedController:
    mode: ReplayMode = ReplayMode.FAST_REPLAY
    speed_multiplier: float = 1.0
    default_frame_interval_seconds: float = 1.0

    @classmethod
    def from_value(
        cls,
        mode: ReplayMode | str,
        *,
        speed_multiplier: float = 1.0,
        default_frame_interval_seconds: float = 1.0,
    ) -> "ReplaySpeedController":
        return cls(
            mode=resolve_replay_mode(mode),
            speed_multiplier=speed_multiplier,
            default_frame_interval_seconds=default_frame_interval_seconds,
        )

    def delay_seconds(
        self,
        *,
        previous_timestamp: float | None,
        current_timestamp: float | None,
        frame_interval_seconds: float | None = None,
    ) -> float:
        if self.mode in _NO_WAIT_MODES:
            return 0.0
        base = frame_interval_seconds
        if base is None and previous_timestamp is not None and current_timestamp is not None:
            base = max(0.0, float(current_timestamp) - float(previous_timestamp))
        if base is None or base <= 0.0:
            base = self.default_frame_interval_seconds
        multiplier = max(0.01, _float(self.speed_multiplier, 1.0))
        return max(0.0, float(base) / multiplier)


@dataclass
class ReplayClock:
    speed: ReplaySpeedController = ReplaySpeedController()
    sleep_enabled: bool = False
    frame_index: int = 0
    replay_time_seconds: float = 0.0
    previous_timestamp: float | None = None

    def advance(self, frame: object) -> dict[str, Any]:
        if isinstance(frame, Mapping):
            typed_frame = cast(Mapping[str, Any], frame)
            timestamp_value = typed_frame.get("timestamp", 0.0)
            interval_value = typed_frame.get("frame_interval_seconds", 0.0)
        else:
            timestamp_value = getattr(frame, "timestamp", 0.0)
            interval_value = getattr(frame, "frame_interval_seconds", 0.0)
        timestamp = _float(timestamp_value, 0.0)
        interval = _float(interval_value, 0.0)
        delay = self.speed.delay_seconds(
            previous_timestamp=self.previous_timestamp,
            current_timestamp=timestamp,
            frame_interval_seconds=interval or None,
        )
        started = time.perf_counter()
        if self.sleep_enabled and delay > 0.0:
            time.sleep(delay)
        elapsed = time.perf_counter() - started
        self.frame_index += 1
        self.replay_time_seconds += delay
        self.previous_timestamp = timestamp
        return {
            "frame_index": self.frame_index,
            "mode": self.speed.mode.value,
            "scheduled_delay_seconds": round(float(delay), 6),
            "actual_wait_seconds": round(float(elapsed), 6),
            "replay_time_seconds": round(float(self.replay_time_seconds), 6),
        }
