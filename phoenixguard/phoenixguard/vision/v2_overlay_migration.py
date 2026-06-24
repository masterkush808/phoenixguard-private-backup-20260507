from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any, cast


def _text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (str, bytes, bytearray, Real)):
        try:
            return float(value)
        except ValueError:
            return float(default)
    return float(default)


def _as_list(value: object) -> list[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return []
    return list(cast(Sequence[object], value))


def _anchor_type(overlay: Mapping[str, object]) -> str:
    if overlay.get("anchors"):
        return "ANCHORS"
    if overlay.get("rect"):
        return "RECT"
    if overlay.get("box") or overlay.get("bbox"):
        return "BOX"
    return "POINTS"


def migrate_v2_overlay_object(
    overlay: Mapping[str, object],
    *,
    frame_id: int | None = None,
    chart_transform_id: str | None = None,
    source_agent: str = "legacy_v2_overlay_migration",
    source_version: str = "V2_MIGRATED_BEHAVIOUR",
) -> dict[str, object]:
    v3: dict[str, object] = dict(overlay)
    v3.setdefault("overlay_id", _text(v3.get("overlay_id") or v3.get("id") or v3.get("key") or v3.get("label")))
    v3.setdefault("id", v3.get("overlay_id"))
    v3.setdefault("type", _text(v3.get("type") or v3.get("layer") or "UNKNOWN"))
    v3.setdefault("side", _text(v3.get("side") or v3.get("direction") or ""))
    v3.setdefault("source_version", source_version)
    v3.setdefault("source_agent", source_agent)
    if frame_id is not None:
        v3["frame_id"] = int(frame_id)
    elif v3.get("frame_id") is None:
        v3["frame_id"] = None
    if chart_transform_id:
        v3["chart_transform_id"] = chart_transform_id
    elif v3.get("chart_transform_id") is None:
        v3["chart_transform_id"] = None
    v3.setdefault("coordinate_mode", _text(v3.get("coordinate_mode") or v3.get("space") or "CHART_IMAGE_SPACE", "CHART_IMAGE_SPACE"))
    v3.setdefault("anchor_type", _anchor_type(v3))
    v3.setdefault("truth_score", _float(v3.get("truth_score") or v3.get("confidence") or 0.0))
    v3.setdefault("confidence", _float(v3.get("confidence") or v3.get("truth_score") or 0.0))
    raw_visible_modes = v3.get("visible_modes")
    visible_modes = _as_list(raw_visible_modes) or ["CLEAN_LIVE", "ACTIVE_CONTEXT", "REPLAY", "PREDICTION", "CALIBRATION", "DEBUG", "INSPECTOR"]
    v3.setdefault("visible_modes", visible_modes)
    v3.setdefault("ttl_sec", _float(v3.get("ttl_sec") or 30.0, 30.0))
    v3.setdefault("reason", _text(v3.get("reason") or v3.get("message") or "migrated from V2 overlay behaviour"))
    raw_box = v3.get("box")
    box_values = _as_list(raw_box)
    if not v3.get("bbox") and box_values:
        v3["bbox"] = box_values
    return v3


def migrate_v2_sniper_overlay(overlay: Mapping[str, object], **kwargs: Any) -> dict[str, object]:
    v3 = migrate_v2_overlay_object(overlay, **kwargs)
    v3["type"] = "SNIPER_ENTRY"
    return v3


def migrate_v2_target_zone(overlay: Mapping[str, object], **kwargs: Any) -> dict[str, object]:
    v3 = migrate_v2_overlay_object(overlay, **kwargs)
    v3["type"] = "TARGET_ZONE"
    return v3


def migrate_v2_progression_overlay(overlay: Mapping[str, object], **kwargs: Any) -> dict[str, object]:
    v3 = migrate_v2_overlay_object(overlay, **kwargs)
    v3["type"] = "HISTORICAL_PROGRESSION"
    return v3


def migrate_v2_would_enter_marker(overlay: Mapping[str, object], **kwargs: Any) -> dict[str, object]:
    v3 = migrate_v2_overlay_object(overlay, **kwargs)
    v3["type"] = "REPLAY_ENTRY"
    return v3


def migrate_v2_would_exit_marker(overlay: Mapping[str, object], **kwargs: Any) -> dict[str, object]:
    v3 = migrate_v2_overlay_object(overlay, **kwargs)
    v3["type"] = "REPLAY_EXIT"
    return v3


def migrate_v2_angle_line(overlay: Mapping[str, object], **kwargs: Any) -> dict[str, object]:
    v3 = migrate_v2_overlay_object(overlay, **kwargs)
    v3["type"] = "ANGLE_VECTOR"
    return v3


def migrate_v2_prediction_path(overlay: Mapping[str, object], **kwargs: Any) -> dict[str, object]:
    v3 = migrate_v2_overlay_object(overlay, **kwargs)
    v3["type"] = "PREDICTION_PATH"
    return v3


__all__ = [
    "migrate_v2_overlay_object",
    "migrate_v2_sniper_overlay",
    "migrate_v2_target_zone",
    "migrate_v2_progression_overlay",
    "migrate_v2_would_enter_marker",
    "migrate_v2_would_exit_marker",
    "migrate_v2_angle_line",
    "migrate_v2_prediction_path",
]
