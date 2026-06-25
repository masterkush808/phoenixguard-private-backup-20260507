from __future__ import annotations
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import cast

from phoenixguard.vision.v3_chart_transform import V3ChartTransform


def _as_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _as_sequence(value: object) -> list[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return []
    return list(cast(Sequence[object], value))


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (str, bytes, bytearray, Real)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _float_list(value: object) -> list[float]:
    out: list[float] = []
    for item in _as_sequence(value):
        if not isinstance(item, (str, bytes, bytearray, Real)):
            return []
        try:
            out.append(float(item))
        except ValueError:
            return []
    return out


def memory_episode_match_to_historical_study(match: Mapping[str, object]) -> dict[str, object]:
    # Minimal conversion: map fields to V3HistoricalStudy structure
    study: dict[str, object] = {
        "study_id": str(match.get("episode_id") or match.get("id") or "hist_0"),
        "side": str(match.get("side") or match.get("direction") or ""),
        "setup_type": str(match.get("setup_type") or "UNKNOWN"),
        "would_enter_at": match.get("would_enter_at") or {},
        "would_exit_at": match.get("would_exit_at") or {},
        "target_zone": match.get("target_zone") or {},
        "path_points": match.get("path_points") or [],
        "outcome": match.get("outcome") or "UNKNOWN",
        "visible_modes": match.get("visible_modes") or ["REPLAY", "INSPECTOR"],
    }
    return study


def a_star_scenario_to_prediction_scenario(scenario: Mapping[str, object]) -> dict[str, object]:
    return {
        "scenario_id": str(scenario.get("id") or "pred_0"),
        "side": str(scenario.get("side") or scenario.get("direction") or ""),
        "timing_forecast": scenario.get("timing") or {},
        "path": scenario.get("path") or [],
        "confidence": _float(scenario.get("confidence")),
        "visible_modes": scenario.get("visible_modes") or ["PREDICTION"],
    }


def scenario_paint_output_to_overlay_objects(output: Mapping[str, object]) -> list[dict[str, object]]:
    objs: list[dict[str, object]] = []
    for item in _as_sequence(output.get("overlays")):
        o = _as_mapping(item)
        if not o:
            continue
        o.setdefault("id", o.get("key") or o.get("label") or f"overlay_{len(objs)}")
        o.setdefault("layer", o.get("layer") or o.get("role") or "trigger_zones")
        o.setdefault("bbox", o.get("bbox") or [0, 0, 0, 0])
        o.setdefault("truth_score", _float(o.get("truth_score") or o.get("confidence")))
        # enrichment: attach placeholders for transform/frame and visibility
        o.setdefault("chart_transform_id", o.get("chart_transform_id") or None)
        o.setdefault("frame_id", o.get("frame_id") or None)
        o.setdefault("visible_modes", o.get("visible_modes") or ["LIVE", "REPLAY", "PREDICTION"])
        # normalized bbox helper: ensure bbox is [x1,y1,x2,y2]
        bbox = _float_list(o.get("bbox") or [0, 0, 0, 0])
        if len(bbox) == 4:
            o["bbox"] = bbox
        else:
            o["bbox"] = [0.0, 0.0, 0.0, 0.0]
        objs.append(o)
    return objs


def enrich_overlay_with_transform(
    overlay: Mapping[str, object],
    chart_transform: Mapping[str, object] | V3ChartTransform | None,
    frame_id: int | None,
) -> dict[str, object]:
    o = dict(overlay)
    if chart_transform is not None:
        if isinstance(chart_transform, V3ChartTransform):
            ct: V3ChartTransform | None = chart_transform
        else:
            try:
                chart_size = _float_list(chart_transform.get("chart_image_bounds") or chart_transform.get("size") or [800, 600])
                ct = V3ChartTransform.create(chart_size or [800, 600], frame_id=frame_id)
            except Exception:
                ct = None
        if ct is not None:
            o["chart_transform_id"] = str(ct.chart_transform_id)
            # convert normalized bbox to chart pixels if values appear normalized
            bbox = _float_list(o.get("bbox") or [0, 0, 0, 0])
            if len(bbox) == 4 and max(bbox) <= 1.01:
                o["bbox"] = ct.normalized_to_chart_image(bbox)
    if frame_id is not None:
        o["frame_id"] = int(frame_id)
    o.setdefault("visible_modes", o.get("visible_modes") or ["LIVE", "REPLAY", "PREDICTION"])
    return o
