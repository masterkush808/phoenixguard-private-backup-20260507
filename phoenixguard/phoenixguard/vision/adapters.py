from __future__ import annotations
from typing import Mapping, Any, Sequence, List
from phoenixguard.vision.v3_chart_transform import V3ChartTransform


def memory_episode_match_to_historical_study(match: Mapping[str, Any]) -> dict:
    # Minimal conversion: map fields to V3HistoricalStudy structure
    study = {
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


def a_star_scenario_to_prediction_scenario(scenario: Mapping[str, Any]) -> dict:
    return {
        "scenario_id": str(scenario.get("id") or "pred_0"),
        "side": str(scenario.get("side") or scenario.get("direction") or ""),
        "timing_forecast": scenario.get("timing") or {},
        "path": scenario.get("path") or [],
        "confidence": float(scenario.get("confidence") or 0.0),
        "visible_modes": scenario.get("visible_modes") or ["PREDICTION"],
    }


def scenario_paint_output_to_overlay_objects(output: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    objs: List[Mapping[str, Any]] = []
    for item in output.get("overlays", []) if isinstance(output.get("overlays"), list) else []:
        o = dict(item)
        o.setdefault("id", o.get("key") or o.get("label") or f"overlay_{len(objs)}")
        o.setdefault("layer", o.get("layer") or o.get("role") or "trigger_zones")
        o.setdefault("bbox", o.get("bbox") or [0, 0, 0, 0])
        o.setdefault("truth_score", float(o.get("truth_score") or o.get("confidence") or 0.0))
        # enrichment: attach placeholders for transform/frame and visibility
        o.setdefault("chart_transform_id", o.get("chart_transform_id") or None)
        o.setdefault("frame_id", o.get("frame_id") or None)
        o.setdefault("visible_modes", o.get("visible_modes") or ["LIVE", "REPLAY", "PREDICTION"])
        # normalized bbox helper: ensure bbox is [x1,y1,x2,y2]
        try:
            bbox = list(map(float, o.get("bbox") or [0, 0, 0, 0]))
            if len(bbox) == 4:
                o["bbox"] = bbox
        except Exception:
            o["bbox"] = [0.0, 0.0, 0.0, 0.0]
        objs.append(o)
    return objs


def enrich_overlay_with_transform(overlay: Mapping[str, Any], chart_transform: Mapping[str, Any] | None, frame_id: int | None) -> dict:
    o = dict(overlay)
    if chart_transform and isinstance(chart_transform, Mapping):
        # accept either a V3ChartTransform instance or dict
        if hasattr(chart_transform, "normalized_to_chart_image"):
            ct = chart_transform
        else:
            try:
                ct = V3ChartTransform.create(chart_transform.get("chart_image_bounds") or chart_transform.get("size") or [800, 600], frame_id=frame_id)
            except Exception:
                ct = None
        if ct is not None:
            o["chart_transform_id"] = str(ct.chart_transform_id)
            # convert normalized bbox to chart pixels if values appear normalized
            try:
                bbox = list(o.get("bbox") or [0, 0, 0, 0])
                if all(isinstance(v, (float, int)) for v in bbox) and max(bbox) <= 1.01:
                    px = ct.normalized_to_chart_image(bbox)
                    o["bbox"] = px
            except Exception:
                pass
    if frame_id is not None:
        o["frame_id"] = int(frame_id)
    o.setdefault("visible_modes", o.get("visible_modes") or ["LIVE", "REPLAY", "PREDICTION"])
    return o
