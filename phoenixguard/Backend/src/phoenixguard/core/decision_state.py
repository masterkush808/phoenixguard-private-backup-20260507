from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


def derive_state_version(*, capture_count: int = 0, frame_index: int = 0, published_epoch: float = 0.0) -> int:
    """Build a monotonic state version from the latest capture metadata."""
    epoch_component = int(round(max(0.0, float(published_epoch)) * 1000.0))
    return int(max(0, int(capture_count or 0), int(frame_index or 0), epoch_component))


def derive_valid_until_epoch(
    *,
    published_epoch: float,
    freshness_window_sec: float,
    expiry_seconds: int | None = None,
) -> float:
    """Return the latest epoch at which an intent should still be considered fresh."""
    published = max(0.0, float(published_epoch or 0.0))
    freshness_window = max(1.0, float(freshness_window_sec or 0.0))
    if expiry_seconds is not None and int(expiry_seconds) > 0:
        freshness_window = min(freshness_window, float(int(expiry_seconds)))
    return round(published + freshness_window, 3)


@dataclass(frozen=True)
class TradeIntent:
    """Frozen decision object handed from the tracker to the local shooter."""

    signal_id: str
    side: str
    expiry_seconds: int
    state_version: int
    published_epoch: float
    valid_until_epoch: float
    freshness_score: float
    summary: str = ""
    source: str = "tracker"
    status: str = "armed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_trade_intent(
    latest_signal: Mapping[str, Any],
    *,
    session_payload: Mapping[str, Any] | None = None,
) -> TradeIntent | None:
    """Build a frozen trade intent when the latest signal is actionable."""
    payload = dict(session_payload or {})
    signal = dict(latest_signal or {})
    side = str(
        signal.get("execution_action")
        or signal.get("action")
        or signal.get("side")
        or "HOLD"
    ).strip().upper()
    if side not in {"BUY", "SELL"}:
        return None
    signal_id = str(signal.get("signal_id", "") or "").strip()
    if not signal_id:
        return None

    published_epoch = float(signal.get("published_epoch", payload.get("last_capture_epoch", 0.0)) or 0.0)
    freshness_window_sec = float(signal.get("freshness_window_sec", payload.get("capture_interval_sec", 8.0)) or 8.0)
    expiry_seconds = int(signal.get("expiry_seconds", signal.get("required_seconds", 0)) or 0)
    state_version = derive_state_version(
        capture_count=int(payload.get("capture_count", 0) or 0),
        frame_index=int(payload.get("frame_index", 0) or 0),
        published_epoch=published_epoch,
    )
    valid_until_epoch = derive_valid_until_epoch(
        published_epoch=published_epoch,
        freshness_window_sec=freshness_window_sec,
        expiry_seconds=expiry_seconds or None,
    )
    freshness_score = float(signal.get("freshness_score", payload.get("freshness_score", 0.0)) or 0.0)
    summary = str(signal.get("summary", "") or "")
    source = str(signal.get("source", payload.get("window_query", "tracker")) or "tracker").strip() or "tracker"
    status = str(signal.get("status", payload.get("status", "armed")) or "armed").strip() or "armed"
    return TradeIntent(  # type: ignore[call-arg]
        signal_id=signal_id,
        side=side,
        expiry_seconds=max(1, expiry_seconds or int(payload.get("expiry_seconds", 0) or 0) or 1),
        state_version=state_version,
        published_epoch=published_epoch,
        valid_until_epoch=valid_until_epoch,
        freshness_score=freshness_score,
        summary=summary,
        source=source,
        status=status,
    )