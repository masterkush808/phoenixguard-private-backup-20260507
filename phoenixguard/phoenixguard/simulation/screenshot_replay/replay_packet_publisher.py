from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from .replay_loader import ReplayFrame


VisionAdapter = Callable[[ReplayFrame], Mapping[str, Any]]


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _empty_mapping() -> dict[str, Any]:
    return {}


def _int_value(value: object, default: int) -> int:
    if not isinstance(value, (int, float, str)):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "CALL", "UP"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "PUT", "DOWN"}:
        return "SELL"
    return "HOLD"


def _expected_value(expected: Mapping[str, Any], *keys: str) -> Any:
    nested = _mapping(expected.get("expected"))
    for key in keys:
        if key in expected:
            return expected.get(key)
        if key in nested:
            return nested.get(key)
    return None


@dataclass(frozen=True)
class ReplayPacket:
    frame_id: int
    frame_path: Path
    frame_hash: str
    timestamp: float
    snapshot: Mapping[str, Any]
    vision_outputs: Mapping[str, Any] = field(default_factory=_empty_mapping)
    expected: Mapping[str, Any] = field(default_factory=_empty_mapping)

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "frame_path": str(self.frame_path),
            "frame_hash": self.frame_hash,
            "timestamp": self.timestamp,
            "snapshot": dict(self.snapshot),
            "vision_outputs": dict(self.vision_outputs),
            "expected": dict(self.expected),
        }


class ReplayPacketPublisher:
    def __init__(
        self,
        vision_adapter: VisionAdapter | None = None,
        *,
        session_id: str = "screenshot-replay",
        symbol: str = "EUR/GBP OTC",
        timeframe: str = "M5",
    ) -> None:
        self.vision_adapter = vision_adapter
        self.session_id = session_id
        self.symbol = symbol
        self.timeframe = timeframe
        self._previous_hash = ""

    def publish(self, frame: ReplayFrame) -> ReplayPacket:
        vision_outputs = (
            _mapping(self.vision_adapter(frame))
            if self.vision_adapter
            else self._default_vision_outputs(frame)
        )
        snapshot = self._snapshot_from_frame(frame, vision_outputs)
        packet = ReplayPacket(
            frame_id=frame.frame_id,
            frame_path=frame.path,
            frame_hash=frame.frame_hash,
            timestamp=frame.timestamp,
            snapshot=snapshot,
            vision_outputs=vision_outputs,
            expected=dict(frame.expected),
        )
        self._previous_hash = frame.frame_hash
        return packet

    def _default_vision_outputs(self, frame: ReplayFrame) -> dict[str, Any]:
        metadata = _mapping(frame.metadata)
        snapshot = _mapping(metadata.get("snapshot") or metadata.get("council_snapshot"))
        labels = _mapping(frame.labels)
        expected = _mapping(frame.expected)
        expected_trap = _expected_value(expected, "trap", "expected_trap")
        expected_quality = _expected_value(expected, "entry_quality", "expected_entry_quality")
        return {
            "chart_map": _mapping(metadata.get("chart_map") or labels.get("chart_map")),
            "zones": metadata.get("zones", labels.get("zones", _expected_value(expected, "zones") or [])),
            "angles": _mapping(metadata.get("angles") or metadata.get("angle_context") or labels.get("angles")),
            "dominance": _mapping(metadata.get("dominance") or metadata.get("market_context")),
            "entry_quality": expected_quality or metadata.get("entry_quality", {}),
            "trap_assessment": expected_trap or metadata.get("trap_assessment", {}),
            "model_council_seed": snapshot,
        }

    def _snapshot_from_frame(self, frame: ReplayFrame, vision_outputs: Mapping[str, Any]) -> dict[str, Any]:
        metadata = _mapping(frame.metadata)
        expected = _mapping(frame.expected)
        explicit = _mapping(metadata.get("snapshot") or metadata.get("council_snapshot") or vision_outputs.get("model_council_seed"))
        if explicit:
            snapshot = dict(explicit)
        else:
            dominant_side = _side(
                _expected_value(expected, "dominant_side")
                or _mapping(vision_outputs.get("dominance")).get("dominant_side")
                or metadata.get("dominant_side")
            )
            entry_quality = str(_expected_value(expected, "entry_quality") or metadata.get("entry_quality") or "").upper()
            trap_value = _expected_value(expected, "trap") or metadata.get("trap")
            trap_map = _mapping(trap_value)
            trap_text = str(trap_map.get("label", "") if trap_map else trap_value).upper()
            execution_state = str(_expected_value(expected, "execution_state") or "").upper()
            executable = execution_state in {"EXECUTABLE", "BUY_EXECUTABLE", "SELL_EXECUTABLE"}
            bad_now = "BAD" in entry_quality or bool(trap_text)
            snapshot = {
                "candidate_side": dominant_side,
                "buy_score": 0.82 if dominant_side == "BUY" else 0.12,
                "sell_score": 0.82 if dominant_side == "SELL" else 0.12,
                "context_confirmed": not bad_now,
                "execution_mature": executable and not bad_now,
                "timing": {
                    "state": "READY" if executable and not bad_now else "WAIT",
                    "expiry_seconds": int(metadata.get("expiry_seconds", 300)),
                },
                "market_context": {
                    "dominant_side": dominant_side,
                    "global_side": dominant_side,
                    "local_side": dominant_side,
                    "inside_valid_trigger_zone": not bad_now,
                    "opposing_force_distance_ok": "OPPOSING" not in trap_text,
                    "current_location": "MIDDLE_DANGER" if bad_now else "MIDDLE_SAFE",
                },
                "angle_context": {
                    "angle_class": "STEEP_IMPULSE" if "LATE_CHASE" in trap_text else "HEALTHY_TREND",
                    "late_chase_risk": "LATE_CHASE" in trap_text,
                    "post_impulse_wait_required": "LATE_CHASE" in trap_text,
                },
                "history_context": {
                    "similarity_state": "RESEMBLES_LATE_LOSS" if bad_now else "REPEATING_SUCCESSFUL_PATH",
                    "historical_late_entry_risk": "HIGH" if bad_now else "LOW",
                },
            }
        session_id = str(metadata.get("session_id") or snapshot.get("session_id") or self.session_id)
        symbol = str(metadata.get("symbol") or snapshot.get("symbol") or self.symbol)
        timeframe = str(metadata.get("timeframe") or snapshot.get("timeframe") or self.timeframe)
        snapshot.update(
            {
                "session_id": session_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "frame_id": _int_value(snapshot.get("frame_id"), frame.frame_id),
                "capture_count": _int_value(snapshot.get("capture_count"), frame.sequence_index + 1),
                "state_version": _int_value(snapshot.get("state_version"), frame.sequence_index + 1),
                "input_frame_hash": str(snapshot.get("input_frame_hash") or frame.frame_hash),
                "previous_frame_hash": str(snapshot.get("previous_frame_hash") or self._previous_hash),
            }
        )
        snapshot.setdefault(
            "live_integrity",
            {
                "is_live": True,
                "frame_advancing": True,
                "capture_advancing": True,
                "state_advancing": True,
                "cache_status": "fresh",
            },
        )
        snapshot.setdefault("runtime_model_health", {"all_required_models_awake": True, "council_status": "AWAKE"})
        return snapshot
