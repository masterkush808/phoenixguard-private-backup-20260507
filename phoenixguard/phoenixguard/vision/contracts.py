"""V3 overlay and chart contracts and small helpers.

These are lightweight runtime helpers used by server adapters and tests to
validate/normalize overlay objects and chart state payloads.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import cast


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (str, bytes, bytearray, Real)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return None
    return cast(Sequence[object], value)


def _float_pair(value: object) -> tuple[float, float] | None:
    seq = _as_sequence(value)
    if seq is None or len(seq) < 2:
        return None
    x = _float(seq[0], float("nan"))
    y = _float(seq[1], float("nan"))
    if x != x or y != y:
        return None
    return x, y


def _float_list(value: object) -> list[float] | None:
    seq = _as_sequence(value)
    if seq is None:
        return None
    out: list[float] = []
    for item in seq:
        number = _float(item, float("nan"))
        if number != number:
            return None
        out.append(number)
    return out


def normalize_overlay_object(value: Mapping[str, object]) -> dict[str, object]:
    out: dict[str, object] = dict(value)
    # Ensure bbox is present as list[float]
    bbox = out.get("bbox") or out.get("box") or out.get("rect")
    anchors = out.get("anchors")
    anchor_seq = _as_sequence(anchors)
    if not bbox and anchor_seq is not None:
        pts = [point for raw_point in anchor_seq if (point := _float_pair(raw_point)) is not None]
        if pts:
            xs = [point[0] for point in pts]
            ys = [point[1] for point in pts]
            bbox = [min(xs), min(ys), max(xs), max(ys)]
    bbox_values = _float_list(bbox)
    if bbox_values is not None:
        out["bbox"] = bbox_values
    elif bbox:
        out["bbox"] = bbox
    try:
        out.setdefault("truth_score", _float(out.get("truth_score") or out.get("confidence") or 0.0))
    except ValueError:
        out.setdefault("truth_score", 0.0)
    return out


def validate_chart_state(payload: object) -> bool:
    # Minimal validation for V3 chart state used by mobile API
    if not isinstance(payload, Mapping):
        return False
    chart_state = cast(Mapping[str, object], payload)
    if str(chart_state.get("schema_version", "")).upper() != "V3_CHART_STATE":
        return False
    if not chart_state.get("session_id"):
        return False
    if "frame_id" not in chart_state:
        return False
    return True


__all__ = ["normalize_overlay_object", "validate_chart_state"]
