from __future__ import annotations

from typing import Any, Mapping, Sequence


def render_candle_frame_stub(frame: Mapping[str, Any]) -> dict[str, Any]:
    """Return renderer-ready candle geometry metadata without requiring image libs."""

    ohlc = dict(frame.get("ohlc", {})) if isinstance(frame.get("ohlc"), Mapping) else {}
    return {
        "frame_id": frame.get("frame_id"),
        "index": frame.get("index"),
        "ohlc": ohlc,
        "render_backend": "metadata_stub",
    }


def render_candle_sequence_stub(frames: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [render_candle_frame_stub(frame) for frame in frames]


__all__ = ["render_candle_frame_stub", "render_candle_sequence_stub"]
