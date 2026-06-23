"""V3 overlay and chart contracts and small helpers.

These are lightweight runtime helpers used by server adapters and tests to
validate/normalize overlay objects and chart state payloads.
"""
from typing import Mapping, Any, Optional


def normalize_overlay_object(value: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(value)
    # Ensure bbox is present as list[float]
    bbox = out.get("bbox") or out.get("box") or out.get("rect")
    anchors = out.get("anchors")
    if not bbox and isinstance(anchors, (list, tuple)):
        try:
            pts = [tuple(map(float, p)) for p in anchors if isinstance(p, (list, tuple)) and len(p) >= 2]
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
        except Exception:
            bbox = None
    if bbox:
        try:
            out["bbox"] = [float(v) for v in bbox]
        except Exception:
            out["bbox"] = bbox
    out.setdefault("truth_score", float(out.get("truth_score") or out.get("confidence") or 0.0))
    return out


def validate_chart_state(payload: Mapping[str, Any]) -> bool:
    # Minimal validation for V3 chart state used by mobile API
    if not isinstance(payload, Mapping):
        return False
    if str(payload.get("schema_version", "")).upper() != "V3_CHART_STATE":
        return False
    if not payload.get("session_id"):
        return False
    if "frame_id" not in payload:
        return False
    return True


__all__ = ["normalize_overlay_object", "validate_chart_state"]
