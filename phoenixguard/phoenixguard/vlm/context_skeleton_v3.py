from __future__ import annotations

from typing import Any, Mapping, Sequence, cast


VLM_CONTEXT_SKELETON_SCHEMA_VERSION = "PG_VLM_CONTEXT_SKELETON_V3"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _overlay_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "overlay_id": _text(row.get("overlay_id") or row.get("id")),
        "object_id": _text(row.get("object_id")),
        "track_id": _text(row.get("track_id")),
        "type": _text(row.get("type")),
        "layer": _text(row.get("layer")),
        "display_label": _text(row.get("display_label") or row.get("label")),
        "side": _text(row.get("side") or row.get("direction"), "HOLD").upper(),
        "reason": _text(row.get("reason")),
        "truth_score": row.get("truth_score"),
        "confidence": row.get("confidence"),
        "display_state": _text(row.get("display_state"), "COMPACT"),
        "visual_weight": row.get("visual_weight"),
        "geometry_visible": row.get("geometry_visible"),
        "label_visible": row.get("label_visible"),
        "inspector_visible": row.get("inspector_visible"),
        "label_lane": _text(row.get("label_lane") or row.get("label_anchor")),
        "bounds": list(_sequence(row.get("bounds") or row.get("bbox")))[:4],
        "visible_modes": [str(item).upper() for item in _sequence(row.get("visible_modes"))],
        "source_agent": _text(row.get("source_agent")),
    }


def _overlay_family_counts(overlays: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in overlays:
        layer = _text(row.get("layer"), "unknown")
        counts[layer] = counts.get(layer, 0) + 1
    return counts


def build_vlm_context_skeleton_v3(live_state: Mapping[str, Any]) -> dict[str, Any]:
    """Build the future VLM read contract without invoking a VLM."""

    overlays_payload = _mapping(live_state.get("overlays"))
    overlays = [
        cast(Mapping[str, Any], row)
        for row in _sequence(overlays_payload.get("objects"))
        if isinstance(row, Mapping)
    ]
    tracking = _mapping(live_state.get("tracking_summary"))
    latest_signal = _mapping(live_state.get("latest_signal"))
    market_objects = _mapping(live_state.get("market_objects"))
    sequence_context = _mapping(live_state.get("sequence_context_v3") or live_state.get("sequence_context"))
    return {
        "schema_version": VLM_CONTEXT_SKELETON_SCHEMA_VERSION,
        "purpose": "explain_phoenixguard_state_only",
        "prediction_authority": "phoenixguard_council_not_vlm",
        "session_id": _text(live_state.get("session_id")),
        "frame_id": live_state.get("frame_id"),
        "chart_transform_id": _text(live_state.get("chart_transform_id")),
        "active_mode": _text(live_state.get("active_mode"), "CLEAN_LIVE"),
        "dictionary_files": {
            "visual_dictionary": "docs/phoenixguard_v3_visual_dictionary.json",
            "market_knowledge_dictionary": "docs/phoenixguard_v3_market_knowledge_dictionary.json",
            "candlestick_glossary": "docs/phoenixguard_v3_candlestick_glossary.json",
        },
        "visual_inputs": {
            "surface": _mapping(live_state.get("surface")),
            "chart_frame": _mapping(live_state.get("chart_frame")),
            "plot_area": _mapping(live_state.get("plot_area")),
            "scene_graph": _mapping(live_state.get("scene_graph")),
            "visual_plane": _mapping(live_state.get("visual_plane")),
        },
        "overlay_story": {
            "overlay_count": len(overlays),
            "family_counts": _overlay_family_counts(overlays),
            "objects": [_overlay_summary(row) for row in overlays],
            "ledger": _mapping(overlays_payload.get("ledger") or live_state.get("overlay_ledger_v3")),
            "vocabulary": _mapping(live_state.get("overlay_vocabulary")),
            "unknown_or_unmapped_terms": list(_sequence(overlays_payload.get("unknown_or_unmapped_terms"))),
        },
        "council_story": {
            "model_council": _mapping(live_state.get("model_council")),
            "signal_thesis_v3": _mapping(live_state.get("signal_thesis_v3")),
            "two_candle_study": _mapping(live_state.get("two_candle_study")),
            "latest_signal": latest_signal,
        },
        "candlestick_story": {
            "tracked_candles": list(_sequence(tracking.get("tracked_candles") or latest_signal.get("tracked_candles"))),
            "candle_statistics": _mapping(tracking.get("candle_statistics") or latest_signal.get("candle_statistics")),
            "sequence_context": sequence_context,
        },
        "market_object_story": {
            "active_count": market_objects.get("active_count"),
            "registry_count": market_objects.get("registry_count"),
            "source_status": _mapping(market_objects.get("source_status")),
        },
        "runtime_contract": {
            "vlm_may_explain": True,
            "vlm_may_predict": False,
            "vlm_may_override_council": False,
            "requires_backend_filtered_overlays": True,
        },
    }
