from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence, cast

from PIL import Image

from phoenixguard.decision.playbook_ai_intelligence_v3 import compact_playbook_ai_intelligence_v3
from phoenixguard.mobile_api.realtime_sync_v3 import build_visual_health_v3
from phoenixguard.runtime.realtime_performance_v3 import (
    build_frame_timing_trace_v3,
    build_performance_trace_v3,
    model_warm_states_from_health,
)
from phoenixguard.tracking.market_object_tracker_v3 import (
    MarketObjectRegistryV3,
    build_market_object_registry_v3,
)
from phoenixguard.vision.broker_scene_graph_v3 import build_broker_scene_graph_v3
from phoenixguard.vision.box_refinement_v3 import OVERLAY_PRECISION_AUDIT_SCHEMA_VERSION, resolve_precision_overlays_v3
from phoenixguard.vision.overlay_layer_manager_v3 import OverlayLayerManagerV3
from phoenixguard.vision.v3_overlay_contract import (
    REQUIRED_FIELDS,
    VIEW_MODES,
    approved_overlay_display_labels,
    is_approved_overlay_display_label,
    normalize_v3_overlay_object,
    normalize_view_mode,
    overlay_is_visible,
    prediction_overlay_config,
    view_mode_profile,
)
from phoenixguard.vlm.context_skeleton_v3 import build_vlm_context_skeleton_v3


LIVE_STATE_SCHEMA_VERSION = "PG_LIVE_STATE_V3"

_SCENE_FORECAST_SCHEMA_TOKENS = (
    "SCENE_FORECAST",
    "CHRONOS_SCENE_FORECAST",
    "FORECAST_PATH_GEOMETRY",
)
_FORECAST_BELIEF_STATUSES = {
    "RESET",
    "REACQUIRING",
    "STABLE",
    "REVERSAL_PENDING",
}
_FORECAST_PUBLIC_METADATA_KEYS = (
    "forecast_engine",
    "forecast_provider",
    "forecast_provider_status",
    "forecast_id",
    "forecast_revision",
    "belief_revision",
    "belief_state",
    "committed_side",
    "candidate_side",
    "change_probability",
    "confirmation_events",
    "required_events",
    "closed_candle_key",
    "closed_candle_sequence",
    "forecast_computed_frame_id",
    "source_forecast_frame_id",
    "geometry_projected_frame_id",
    "geometry_frame_match_verified",
    "geometry_reprojected_from_cache",
    "geometry_projection_provenance",
    "detector_coverage_rebase_applied",
    "cache_replaced_for_detector_coverage_rebase",
    "pair",
    "timeframe",
    "market_identity_confirmed",
    "timeframe_identity_confirmed",
    "identity_contract_status",
    "scene_feature_audit",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return cast(Sequence[Any], value)
    return ()


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        row = _mapping(value)
        if row:
            return row
    return {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    return [dict(cast(Mapping[str, Any], item)) for item in _sequence(value) if isinstance(item, Mapping)]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _int(value: Any, default: int = 0) -> int:
    return int(_float(value, float(default)))


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on", "ok", "ready", "valid", "pass", "passed", "locked"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off", "invalid", "fail", "failed", "missing", "mismatch", "wrong_surface"}:
        return False
    return default


def _is_scene_forecast_payload(payload: Mapping[str, Any]) -> bool:
    if not payload:
        return False
    schema = _text(payload.get("schema_version")).upper()
    provider = _text(payload.get("provider")).upper()
    skill = _text(payload.get("skill")).upper()
    return bool(
        any(token in schema for token in _SCENE_FORECAST_SCHEMA_TOKENS)
        or "CHRONOS" in provider
        or "SCENE_FORECAST" in provider
        or "SCENE_FORECAST" in skill
        or _bool(payload.get("scene_forecaster"), False)
    )


def _forecast_authorization_base_is_current(
    payload: Mapping[str, Any],
    *,
    require_trade_status: bool,
) -> bool:
    if not (
        _bool(payload.get("fresh"), False)
        and _bool(payload.get("forecast_available"), False)
        and _bool(payload.get("artifact_production_gate_passed"), False)
        and _bool(payload.get("production_authorized"), False)
        and _bool(payload.get("selective_authorized"), False)
        and _bool(payload.get("market_identity_confirmed"), False)
        and _bool(payload.get("timeframe_identity_confirmed"), False)
    ):
        return False
    if any(
        _bool(payload.get(key), False)
        for key in ("stale", "expired", "diagnostic_only", "forecast_suppressed")
    ):
        return False
    stale_statuses = {"STALE", "EXPIRED", "OUTDATED", "FAIL", "FAILED"}
    if any(
        _text(payload.get(key)).upper() in stale_statuses
        for key in ("freshness_status", "stale_status")
    ):
        return False
    trade_status = _text(payload.get("trade_authorization_status")).upper()
    if require_trade_status:
        return trade_status == "AUTHORIZED"
    return not trade_status or trade_status == "AUTHORIZED"


def _forecast_public_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the small, stable belief contract safe for public overlays.

    Model internals, file paths, and free-form revision records deliberately do
    not cross this boundary.  Missing or malformed values never manufacture a
    committed direction.
    """

    belief = _first_mapping(
        payload.get("forecast_belief"),
        payload.get("belief_update"),
        payload.get("belief"),
        payload.get("belief_tracker"),
    )
    has_belief = bool(
        belief
        or any(
            key in payload
            for key in (
                "belief_state",
                "committed_side",
                "candidate_side",
                "belief_revision",
            )
        )
    )
    result: dict[str, Any] = {}
    if _is_scene_forecast_payload(payload):
        result["forecast_engine"] = "SCENE_FORECASTER_V3"
        provider = _text(payload.get("provider"))[:48].upper()
        provider_status = _text(payload.get("provider_status"))[:48].upper()
        if provider:
            result["forecast_provider"] = provider
        if provider_status:
            result["forecast_provider_status"] = provider_status
        for key in (
            "geometry_frame_match_verified",
            "geometry_reprojected_from_cache",
            "detector_coverage_rebase_applied",
            "cache_replaced_for_detector_coverage_rebase",
        ):
            if key in payload:
                result[key] = _bool(payload.get(key), False)
        for key in (
            "forecast_computed_frame_id",
            "source_forecast_frame_id",
            "geometry_projected_frame_id",
        ):
            if payload.get(key) not in (None, ""):
                result[key] = max(0, _int(payload.get(key)))
        raw_geometry_provenance = _mapping(
            payload.get("geometry_projection_provenance")
        )
        if raw_geometry_provenance:
            result["geometry_projection_provenance"] = {
                key: raw_geometry_provenance[key]
                for key in (
                    "status",
                    "method",
                    "source_forecast_frame_id",
                    "source_geometry_frame_id",
                    "projected_frame_id",
                    "verified",
                    "source_anchor",
                    "target_anchor",
                    "x_gain",
                    "y_gain",
                    "pointwise_clipping_applied",
                )
                if key in raw_geometry_provenance
            }
        raw_audit = _mapping(payload.get("scene_feature_audit"))
        if raw_audit:
            source_presence = _mapping(raw_audit.get("source_presence"))
            causal_exclusions = _mapping(raw_audit.get("causal_exclusions"))
            result["scene_feature_audit"] = {
                "consumed_field_count": _int(
                    raw_audit.get("consumed_field_count"),
                    len(_sequence(raw_audit.get("consumed_fields"))),
                ),
                "missing_field_count": _int(
                    raw_audit.get("missing_field_count"),
                    len(_sequence(raw_audit.get("missing_fields"))),
                ),
                "rejected_field_count": _int(
                    raw_audit.get("rejected_field_count"),
                    len(_sequence(raw_audit.get("rejected_fields"))),
                ),
                "source_presence": {
                    str(key): _bool(value, False)
                    for key, value in source_presence.items()
                    if str(key)
                    in {
                        "candles",
                        "projection",
                        "candle_statistics",
                        "behavior_payload",
                        "decision_kernel",
                        "smart_money_context",
                        "support_resistance_context",
                        "support_resistance_zones",
                        "trend_slopes",
                        "trend_directions",
                        "timeframe",
                        "pair",
                    }
                },
                "causal_exclusions": {
                    "forming_candles": max(
                        0,
                        _int(causal_exclusions.get("forming_candles")),
                    ),
                    "history_rows_outside_window": max(
                        0,
                        _int(
                            causal_exclusions.get("history_rows_outside_window")
                        ),
                    ),
                    "projected_geometry_is_feature": _bool(
                        causal_exclusions.get("projected_geometry_is_feature"),
                        False,
                    ),
                    "future_outcome_fields_are_feature": _bool(
                        causal_exclusions.get("future_outcome_fields_are_feature"),
                        False,
                    ),
                },
            }
    forecast_id = _text(payload.get("forecast_id"))[:96]
    if forecast_id:
        result["forecast_id"] = forecast_id
    for target, candidates in (
        ("forecast_revision", (payload.get("forecast_revision"), payload.get("revision"))),
        ("belief_revision", (payload.get("belief_revision"), belief.get("revision"))),
        (
            "closed_candle_sequence",
            (payload.get("closed_candle_sequence"), belief.get("closed_candle_sequence")),
        ),
    ):
        value = next((item for item in candidates if item not in (None, "")), None)
        if value is not None:
            result[target] = max(0, _int(value))
    closed_key = _text(
        payload.get("closed_candle_key") or belief.get("closed_candle_key")
    )[:128]
    if closed_key:
        result["closed_candle_key"] = closed_key
    if not has_belief:
        return result

    status = _text(payload.get("belief_state") or belief.get("status")).upper()
    result["belief_state"] = (
        status if status in _FORECAST_BELIEF_STATUSES else "RESET"
    )
    committed = _text(
        payload.get("committed_side")
        or belief.get("active_side")
        or belief.get("committed_side")
    ).upper()
    result["committed_side"] = (
        committed if committed in {"BUY", "SELL", "HOLD"} else "HOLD"
    )
    candidate = _text(
        payload.get("candidate_side")
        or belief.get("candidate_side")
        or belief.get("pending_side")
    ).upper()
    result["candidate_side"] = (
        candidate if candidate in {"BUY", "SELL", "HOLD"} else "HOLD"
    )
    result["confirmation_events"] = max(
        0,
        _int(
            payload.get("confirmation_events")
            if payload.get("confirmation_events") is not None
            else belief.get("pending_count")
        ),
    )
    result["required_events"] = max(
        0,
        _int(
            payload.get("required_events")
            if payload.get("required_events") is not None
            else belief.get("required_count")
        ),
    )
    probability = payload.get("change_probability")
    if probability is None:
        probability = belief.get("change_probability")
    if probability is not None:
        result["change_probability"] = round(
            max(0.0, min(1.0, _float(probability))),
            6,
        )
    return result


def _preserve_forecast_public_metadata(
    normalized: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(normalized)
    for key in _FORECAST_PUBLIC_METADATA_KEYS:
        value = raw.get(key)
        if value not in (None, ""):
            row[key] = value
    if _text(raw.get("forecast_engine")).upper() == "SCENE_FORECASTER_V3":
        scene_label = _text(
            raw.get("display_label")
            or raw.get("short_label")
            or raw.get("label"),
            "SCENE FORECASTER E1-E12",
        )
        if "SCENE" not in scene_label.upper():
            scene_label = "SCENE FORECASTER E1-E12"
        row.update(
            {
                "label": scene_label,
                "display_label": scene_label,
                "short_label": scene_label,
                "raw_display_label": scene_label,
                "display_label_status": "CANONICAL",
                "unmapped_display_label": "",
            }
        )
    return row


def _artifact_frame_id_from_path(path: Path | str | None) -> int:
    if not path:
        return 0
    try:
        prefix = Path(path).stem.split("_", 1)[0]
    except Exception:
        return 0
    try:
        frame_id = int(prefix)
    except (TypeError, ValueError):
        return 0
    return frame_id if frame_id > 0 else 0


def _image_size(path: Path | None) -> tuple[int, int]:
    if path is None or not path.exists():
        return (0, 0)
    try:
        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception:
        return (0, 0)


def _artifact_from_path(kind: str, path: Path | str | None, *, session_id: str = "") -> dict[str, Any]:
    resolved = Path(path) if path else None
    exists = bool(resolved and resolved.exists())
    width, height = _image_size(resolved)
    mtime = 0.0
    size = 0
    if exists and resolved is not None:
        try:
            stat = resolved.stat()
            mtime = float(stat.st_mtime)
            size = int(stat.st_size)
        except OSError:
            exists = False
    version = f"{int(mtime * 1000.0)}-{int(size)}" if exists else "missing"
    return {
        "kind": kind,
        "path": str(resolved) if resolved else "",
        "exists": exists,
        "width": width,
        "height": height,
        "frame_id": _artifact_frame_id_from_path(resolved),
        "mtime": mtime,
        "size": size,
        "url": f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{kind}?v={version}" if session_id else "",
    }


def _artifact_refs(
    session: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Path | str] | None = None,
    artifact_probe: Callable[[str], Path | str] | None = None,
) -> dict[str, dict[str, Any]]:
    session_id = _text(session.get("session_id"))
    keys = {
        "window": "last_window_path",
        "chart": "last_chart_path",
        "overlay": "last_overlay_path",
        "full-overlay": "last_full_overlay_path",
        "projection": "last_projection_path",
        "memory-reference": "last_memory_reference_path",
    }
    refs: dict[str, dict[str, Any]] = {}
    for kind, key in keys.items():
        value: Path | str | None = None
        if artifacts and kind in artifacts:
            value = artifacts[kind]
        elif artifact_probe is not None:
            try:
                value = artifact_probe(kind)
            except Exception:
                value = None
        if value is None and kind == "window" and _text(session.get("last_display_window_path")):
            value = _text(session.get("last_display_window_path"))
        if value is None:
            value = _text(session.get(key))
        refs[kind] = _artifact_from_path(kind, value, session_id=session_id)
    return refs


def _bounds_payload(values: Sequence[Any] | None) -> dict[str, Any]:
    if not values or len(values) < 4:
        return {"exists": False, "x": 0, "y": 0, "width": 0, "height": 0, "bbox": []}
    x0 = _float(values[0])
    y0 = _float(values[1])
    x1 = _float(values[2])
    y1 = _float(values[3])
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    return {
        "exists": right > left and bottom > top,
        "x": left,
        "y": top,
        "width": max(0.0, right - left),
        "height": max(0.0, bottom - top),
        "bbox": [left, top, right, bottom],
    }


def _focus_chart_space_bounds(
    session: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> list[float]:
    tracking = _mapping(session.get("tracking_summary"))
    focus = _mapping(tracking.get("focus_region")) or _mapping(session.get("manual_focus_region"))
    focus_box = _bounds_list(focus.get("pixel_bbox"))
    chart_width = _float(_mapping(artifacts.get("chart")).get("width"), 0.0)
    chart_height = _float(_mapping(artifacts.get("chart")).get("height"), 0.0)
    if chart_width <= 0.0 and focus_box:
        chart_width = max(1.0, focus_box[2] - focus_box[0])
    if chart_height <= 0.0 and focus_box:
        chart_height = max(1.0, focus_box[3] - focus_box[1])
    if chart_width <= 1.0 or chart_height <= 1.0:
        return []
    return [0.0, 0.0, float(chart_width), float(chart_height)]


def _chart_region_is_too_thin_for_focus(
    pixel_bbox: object,
    session: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> bool:
    bbox = _bounds_list(pixel_bbox)
    focus_bounds = _focus_chart_space_bounds(session, artifacts)
    if not bbox or not focus_bounds:
        return False
    focus_width = max(1.0, focus_bounds[2] - focus_bounds[0])
    focus_height = max(1.0, focus_bounds[3] - focus_bounds[1])
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    top_ratio = bbox[1] / focus_height if focus_height > 0.0 else 0.0
    return bool(width < focus_width * 0.70 or height < focus_height * 0.42 or top_ratio > 0.30)


def _plot_area(session: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    tracking = _mapping(session.get("tracking_summary"))
    chart_region = _mapping(tracking.get("chart_region") or tracking.get("display_region"))
    pixel_bbox: object = chart_region.get("pixel_bbox") or chart_region.get("bbox")
    if not isinstance(pixel_bbox, Sequence) or isinstance(pixel_bbox, (str, bytes, bytearray)):
        width = _float(artifacts.get("chart", {}).get("width"), 0.0)
        height = _float(artifacts.get("chart", {}).get("height"), 0.0)
        pixel_bbox = [0.0, 0.0, width, height] if width and height else []
    elif _chart_region_is_too_thin_for_focus(cast(object, pixel_bbox), session, artifacts):
        focus_bounds = _focus_chart_space_bounds(session, artifacts)
        if focus_bounds:
            pixel_bbox = focus_bounds
    payload = _bounds_payload(cast(Sequence[Any], pixel_bbox) if pixel_bbox else None)
    bounds = dict(payload)
    payload.update(
        {
            "bounds": bounds,
            "source": _text(chart_region.get("source"), "tracker_chart_region"),
            "confidence": _float(chart_region.get("confidence"), 0.0),
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "manual_focus_region": _mapping(session.get("manual_focus_region")),
        }
    )
    return payload


def _chart_transform(session: Mapping[str, Any], plot: Mapping[str, Any]) -> dict[str, Any]:
    tracking = _mapping(session.get("tracking_summary"))
    raw = _mapping(tracking.get("chart_transform"))
    frame_id = _int(session.get("frame_index"))
    transform_id = _text(raw.get("chart_transform_id") or raw.get("id"), f"ct_{_text(session.get('session_id'), 'session')}_{frame_id}")
    return {
        "chart_transform_id": transform_id,
        "frame_id": frame_id,
        "plot_area": dict(plot),
        "coordinate_space": "FULL_BROKER_SURFACE_WITH_PLOT_AREA",
    }


def _study_packet_summary(packet: Mapping[str, Any] | None, *, now_epoch: float) -> dict[str, Any]:
    payload = _mapping(packet)
    valid_until = _float(payload.get("valid_until_epoch") or payload.get("valid_until_epoch_sec"), 0.0)
    created = _float(payload.get("created_epoch") or payload.get("created_epoch_sec") or payload.get("published_epoch"), 0.0)
    age_ms = max(0.0, (float(now_epoch) - created) * 1000.0) if created > 0.0 else 0.0
    return {
        "exists": bool(payload),
        "packet_id": _text(payload.get("packet_id")),
        "schema_version": _text(payload.get("schema_version")),
        "created_epoch": created,
        "valid_until_epoch": valid_until,
        "age_ms": round(age_ms, 3),
        "fresh": bool(payload) and (valid_until <= 0.0 or valid_until >= now_epoch),
    }


def _execution_packet_valid_until(packet: Mapping[str, Any]) -> float:
    direct = _float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"), 0.0)
    if direct > 0.0:
        return direct
    created = _float(packet.get("created_epoch_sec") or packet.get("created_epoch"), 0.0)
    ttl = _float(packet.get("ttl_sec") or packet.get("time_to_live_sec") or packet.get("freshness_window_sec"), 0.0)
    return created + max(0.1, ttl) if created > 0.0 and ttl > 0.0 else 0.0


def _current_execution_packet(packet: Any, *, now_epoch: float) -> dict[str, Any]:
    payload = _mapping(packet)
    if not payload:
        return {}
    packet_type = _text(payload.get("packet_type")).upper()
    schema_version = _text(payload.get("schema_version")).upper()
    if packet_type != "PG_EXECUTION_PACKET_V3" or schema_version != "PG_EXECUTION_PACKET_V3":
        return {}
    valid_until = _execution_packet_valid_until(payload)
    if valid_until <= float(now_epoch):
        return {}
    execution = _mapping(payload.get("execution"))
    council = _mapping(payload.get("model_council"))
    execution_side = _text(execution.get("side")).upper()
    final_side = _text(council.get("final_side")).upper()
    if execution_side not in {"BUY", "SELL"} or final_side not in {"BUY", "SELL"}:
        return {}
    if execution_side != final_side:
        return {}
    if execution.get("enabled") is not True:
        return {}
    if _text(execution.get("state")).upper() != "EXECUTABLE":
        return {}
    if _text(council.get("final_state")).upper() != "EXECUTABLE":
        return {}
    if _int(execution.get("expiry_seconds"), 0) <= 0:
        return {}
    return payload


def _model_council_summary(session: Mapping[str, Any], study_packet: Mapping[str, Any] | None) -> dict[str, Any]:
    signal = _mapping(session.get("latest_signal"))
    result = _mapping(session.get("model_council_result"))
    packet = _mapping(study_packet)
    council = _mapping(result.get("model_council") or packet.get("model_council"))
    execution = _mapping(result.get("execution") or packet.get("execution"))
    promotion = _mapping(result.get("promotion_trace") or packet.get("promotion_trace"))
    return {
        "state": _text(execution.get("state") or council.get("final_state") or signal.get("entry_state"), "WATCHING").upper(),
        "side": _text(execution.get("side") or council.get("final_side") or signal.get("execution_action") or signal.get("action"), "HOLD").upper(),
        "summary": _text(signal.get("summary"), "Awaiting live chart study."),
        "next_required": _text(promotion.get("next_required") or promotion.get("denied_at") or signal.get("execution_block_reason")),
        "result_present": bool(result),
    }


def _instrument(session: Mapping[str, Any]) -> dict[str, Any]:
    tracking = _mapping(session.get("tracking_summary"))
    signal = _mapping(session.get("latest_signal"))
    market = _text(signal.get("market") or tracking.get("detected_market") or session.get("market"))
    timeframe = _text(signal.get("focus_timeframe") or tracking.get("detected_timeframe"))
    return {
        "market": market,
        "timeframe": timeframe,
        "market_confidence": _float(signal.get("market_confidence", tracking.get("market_confidence", 0.0)), 0.0),
        "timeframe_confidence": _float(signal.get("timeframe_confidence", tracking.get("timeframe_confidence", 0.0)), 0.0),
        "identity_locked": bool(market and timeframe),
        "instrument_context": _mapping(tracking.get("instrument_context") or signal.get("instrument_context")),
        "symbol_context": _mapping(tracking.get("symbol_context") or signal.get("symbol_context")),
    }


def _overlay_from_active_object(
    row: Mapping[str, Any],
    *,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    scene_graph: Mapping[str, Any],
    index: int,
) -> dict[str, Any] | None:
    overlay = _mapping(row.get("overlay"))
    if not overlay:
        overlay = dict(row)
    source_transform = _mapping(row.get("chart_transform")) or _mapping(overlay.get("chart_transform"))
    source_frame_id = _int(
        overlay.get("frame_id")
        or row.get("frame_id")
        or source_transform.get("frame_id"),
        0,
    )
    source_sequence_id = _text(overlay.get("sequence_id") or row.get("sequence_id"))
    source_chart_transform_id = _text(
        overlay.get("chart_transform_id")
        or row.get("chart_transform_id")
        or source_transform.get("chart_transform_id")
    )
    # Persisted registry rows are supplemental context, not current-frame
    # authority. Never relabel old or lineage-free geometry as current merely
    # because it was still inside the registry TTL.
    if source_frame_id <= 0 or source_frame_id != frame_id:
        return None
    # Older persisted rows may not carry the two secondary lineage tokens.
    # An exact frame match is the minimum authority; any token that is present
    # must also agree.  Missing tokens are filled only after that exact match,
    # so stale geometry can never be rebadged as the current frame.
    if source_sequence_id and source_sequence_id != sequence_id:
        return None
    if source_chart_transform_id and source_chart_transform_id != chart_transform_id:
        return None
    source_sequence_id = source_sequence_id or sequence_id
    source_chart_transform_id = source_chart_transform_id or chart_transform_id
    for source_key, target_key in (("pixel_bbox", "bounds"), ("bbox", "bounds"), ("bounds", "bounds")):
        if source_key in overlay and target_key not in overlay:
            overlay[target_key] = overlay[source_key]
    overlay.setdefault("overlay_id", row.get("overlay_id") or overlay.get("id") or f"registry_{index}")
    overlay.setdefault("object_id", row.get("object_id") or overlay.get("overlay_id"))
    overlay.setdefault("track_id", row.get("track_id") or row.get("object_id") or overlay.get("object_id"))
    overlay.setdefault("truth_score", row.get("truth_score", overlay.get("confidence", 0.0)))
    overlay.setdefault("lifecycle_state", row.get("lifecycle_state", "ACTIVE"))
    overlay["frame_id"] = source_frame_id
    overlay["sequence_id"] = source_sequence_id
    overlay["chart_transform_id"] = source_chart_transform_id
    overlay.setdefault("source_agent", row.get("source_agent", "market_registry"))
    overlay.setdefault("reason", row.get("reason", "registry active object"))
    overlay.setdefault("source_rule", row.get("source_rule", "active_object_registry"))
    overlay.setdefault("structural_anchor", row.get("structural_anchor", True))
    for key in (
        "anchor_candles",
        "anchor_candle_indices",
        "anchor_price_band",
        "anchor_time_span",
        "anchor_evidence",
        "touch_points",
        "line_points",
        "points",
        "path",
        "forecast_band_points",
        "forecast_candles",
        "forecast_scenarios",
        "forecast_anchor",
        "forecast_coordinate_space",
        "forecast_coordinate_units",
        "forecast_direction",
        "trajectory_mode",
        "trajectory_mode_probability_calibrated",
        "body_bias",
        "direction_conflict",
        "path_confidence_status",
        "forecast_engine",
        "forecast_provider",
        "forecast_provider_status",
        "forecast_id",
        "forecast_revision",
        "belief_revision",
        "belief_state",
        "committed_side",
        "candidate_side",
        "change_probability",
        "confirmation_events",
        "required_events",
        "closed_candle_key",
        "closed_candle_sequence",
        "forecast_computed_frame_id",
        "source_forecast_frame_id",
        "geometry_projected_frame_id",
        "geometry_frame_match_verified",
        "geometry_reprojected_from_cache",
        "geometry_projection_provenance",
        "detector_coverage_rebase_applied",
        "cache_replaced_for_detector_coverage_rebase",
        "scene_feature_audit",
        "interval",
    ):
        if key in row and key not in overlay:
            overlay[key] = row[key]
    _rescale_registry_overlay_to_current_chart(
        overlay,
        row,
        scene_graph=scene_graph,
    )
    visible_modes = [str(item).upper() for item in _sequence(overlay.get("visible_modes"))]
    if visible_modes and "REPLAY" in visible_modes and "FULL_HISTORY_READ" not in visible_modes:
        overlay["visible_modes"] = [*visible_modes, "FULL_HISTORY_READ"]
    try:
        return normalize_v3_overlay_object(
            overlay,
            strict=False,
            frame_id=frame_id,
            sequence_id=sequence_id,
            chart_transform_id=chart_transform_id,
            fallback_index=index,
        )
    except Exception:
        return None


def _scale_number(value: Any, *, source_min: float, source_size: float, target_min: float, target_size: float) -> float:
    return target_min + ((_float(value, source_min) - source_min) * target_size / max(1.0, source_size))


def _scale_bounds_between_chart_spaces(bounds: Sequence[Any], source: Sequence[float], target: Sequence[float]) -> list[float]:
    source_width = max(1.0, float(source[2] - source[0]))
    source_height = max(1.0, float(source[3] - source[1]))
    target_width = max(1.0, float(target[2] - target[0]))
    target_height = max(1.0, float(target[3] - target[1]))
    return [
        _scale_number(bounds[0], source_min=source[0], source_size=source_width, target_min=target[0], target_size=target_width),
        _scale_number(bounds[1], source_min=source[1], source_size=source_height, target_min=target[1], target_size=target_height),
        _scale_number(bounds[2], source_min=source[0], source_size=source_width, target_min=target[0], target_size=target_width),
        _scale_number(bounds[3], source_min=source[1], source_size=source_height, target_min=target[1], target_size=target_height),
    ]


def _scale_point_between_chart_spaces(point: Sequence[Any], source: Sequence[float], target: Sequence[float]) -> list[float]:
    source_width = max(1.0, float(source[2] - source[0]))
    source_height = max(1.0, float(source[3] - source[1]))
    target_width = max(1.0, float(target[2] - target[0]))
    target_height = max(1.0, float(target[3] - target[1]))
    return [
        _scale_number(point[0], source_min=source[0], source_size=source_width, target_min=target[0], target_size=target_width),
        _scale_number(point[1], source_min=source[1], source_size=source_height, target_min=target[1], target_size=target_height),
    ]


def _scale_points_between_chart_spaces(value: Any, source: Sequence[float], target: Sequence[float]) -> list[list[float]]:
    points: list[list[float]] = []
    for item in _sequence(value):
        point = _sequence(item)
        if len(point) >= 2:
            points.append(_scale_point_between_chart_spaces(point, source, target))
    return points


def _rescale_registry_overlay_to_current_chart(
    overlay: dict[str, Any],
    row: Mapping[str, Any],
    *,
    scene_graph: Mapping[str, Any],
) -> None:
    source_transform = _mapping(row.get("chart_transform")) or _mapping(overlay.get("chart_transform"))
    coordinate_mode = _text(
        overlay.get("coordinate_mode")
        or overlay.get("coordinate_space")
        or row.get("coordinate_mode")
        or row.get("coordinate_space")
    ).upper()
    # Normalized chart geometry is already scale-free. Treating values such as
    # ``0.62`` as source pixels and applying an origin/size transform moves a
    # perfectly anchored study into an arbitrary corner of the chart.
    if "NORMALIZED" in coordinate_mode:
        return
    source_bounds = (
        _bounds_list(source_transform.get("chart_image_bounds"))
        or _bounds_list(source_transform.get("window_bounds"))
        or _bounds_list(source_transform.get("screen_bounds"))
    )
    target_bounds = _bounds_list(scene_graph.get("chart_region_chart_bounds"))
    if not source_bounds or not target_bounds:
        return
    same_bounds = all(
        abs(float(source_bounds[index]) - float(target_bounds[index])) < 1.0
        for index in range(4)
    )
    if same_bounds:
        return
    for key in ("bbox", "bounds", "pixel_bbox"):
        raw_bounds = _sequence(overlay.get(key))
        if len(raw_bounds) >= 4:
            overlay[key] = _scale_bounds_between_chart_spaces(raw_bounds[:4], source_bounds, target_bounds)
    for key in (
        "points",
        "line_points",
        "touch_points",
        "anchor_wick_points",
        "trendline_touch_points",
        "path",
    ):
        scaled_points = _scale_points_between_chart_spaces(overlay.get(key), source_bounds, target_bounds)
        if scaled_points:
            overlay[key] = scaled_points
    # Forecast geometry has its own coordinate contract. Only pixel-based
    # flat point collections are projected here. Candle-event dictionaries
    # use explicit ``*_norm`` fields and scenario dictionaries contain nested
    # ``line_points``; neither can safely pass through the flat point scaler.
    forecast_units = _text(
        overlay.get("forecast_coordinate_units")
        or overlay.get("coordinate_units")
    ).lower()
    if forecast_units == "pixels":
        scaled_band = _scale_points_between_chart_spaces(
            overlay.get("forecast_band_points"),
            source_bounds,
            target_bounds,
        )
        if scaled_band:
            overlay["forecast_band_points"] = scaled_band
        projected_scenarios: list[dict[str, Any]] = []
        for raw_scenario in _sequence_of_mappings(overlay.get("forecast_scenarios")):
            scenario = dict(raw_scenario)
            scenario_points = _scale_points_between_chart_spaces(
                scenario.get("line_points"),
                source_bounds,
                target_bounds,
            )
            if len(scenario_points) < 2:
                continue
            scenario["line_points"] = scenario_points
            projected_scenarios.append(scenario)
        if projected_scenarios:
            overlay["forecast_scenarios"] = projected_scenarios
    anchor_evidence = _mapping(overlay.get("anchor_evidence"))
    scaled_evidence_points = _scale_points_between_chart_spaces(
        anchor_evidence.get("touch_points"),
        source_bounds,
        target_bounds,
    )
    if scaled_evidence_points:
        overlay["anchor_evidence"] = {
            **anchor_evidence,
            "touch_points": scaled_evidence_points,
        }
    overlay["coordinate_mode"] = "CHART_IMAGE_SPACE"
    overlay["registry_chart_space_scaled_v3"] = True


def _combine_overlays(
    registry: MarketObjectRegistryV3,
    *,
    active_objects: Sequence[Mapping[str, Any]] | None,
    chart_transform_id: str,
    scene_graph: Mapping[str, Any],
) -> list[dict[str, Any]]:
    overlays = [dict(overlay) for overlay in registry.overlays]
    seen = {str(overlay.get("overlay_id")) for overlay in overlays}
    seen_semantic = {_overlay_semantic_geometry_key(overlay) for overlay in overlays}
    registry_has_historical_progression = any(
        _text(overlay.get("type")).upper() == "PROGRESSION_PATH"
        and _text(overlay.get("source_path")).startswith("tracking_summary.historical_structure")
        for overlay in overlays
    )
    for index, row in enumerate(active_objects or []):
        if registry_has_historical_progression and _source_less_progression_rectangle(row):
            continue
        overlay = _overlay_from_active_object(
            row,
            frame_id=registry.frame_id,
            sequence_id=registry.sequence_context.sequence_id,
            chart_transform_id=chart_transform_id,
            scene_graph=scene_graph,
            index=index,
        )
        semantic_key = _overlay_semantic_geometry_key(overlay) if overlay is not None else ()
        if (
            overlay is not None
            and str(overlay.get("overlay_id")) not in seen
            and semantic_key not in seen_semantic
        ):
            overlays.append(overlay)
            seen.add(str(overlay.get("overlay_id")))
            seen_semantic.add(semantic_key)
    return overlays


def _overlay_semantic_geometry_key(overlay: Mapping[str, Any]) -> tuple[object, ...]:
    overlay_type = _text(overlay.get("type") or overlay.get("overlay_type")).upper()
    if overlay_type not in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}:
        return ("ID", _text(overlay.get("overlay_id") or overlay.get("id")))
    points = _scale_free_point_key(
        overlay.get("line_points") or overlay.get("points") or overlay.get("path")
    )
    role = _text(overlay.get("trendline_role") or overlay.get("role")).lower()
    scope = _text(overlay.get("trendline_scope")).upper()
    return ("TRENDLINE", role, scope, points)


def _scale_free_point_key(value: Any) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for raw_point in _sequence(value):
        point = _sequence(raw_point)
        if len(point) < 2:
            continue
        points.append((round(_float(point[0]), 2), round(_float(point[1]), 2)))
    return tuple(points)


def _source_less_progression_rectangle(row: Mapping[str, Any]) -> bool:
    overlay = _mapping(row.get("overlay")) or dict(row)
    overlay_type = _text(overlay.get("type") or row.get("type")).upper()
    layer = _text(overlay.get("layer") or row.get("layer")).lower()
    source_path = _text(overlay.get("source_path") or row.get("source_path"))
    has_path_points = bool(
        _sequence(overlay.get("line_points"))
        or _sequence(overlay.get("points"))
        or _sequence(overlay.get("path"))
        or _sequence(overlay.get("anchors"))
    )
    is_progression = overlay_type in {"PROGRESSION_PATH", "HISTORICAL_REPLAY", "HISTORICAL_PROGRESSION", "PROGRESSION"}
    return bool(is_progression and layer in {"", "historical_replay", "replay"} and not source_path and not has_path_points)


def _dashboard_overlay_object(overlay: Mapping[str, Any], *, compact: bool = False) -> dict[str, Any]:
    row = dict(overlay)
    bbox = _sequence(row.get("bbox"))
    if len(bbox) < 4:
        bounds_value = row.get("bounds")
        if _sequence(bounds_value):
            bbox = list(_sequence(bounds_value))[:4]
        elif isinstance(bounds_value, Mapping):
            maybe_bbox = _mapping(bounds_value).get("bbox")
            if _sequence(maybe_bbox):
                bbox = list(_sequence(maybe_bbox))[:4]
    if len(bbox) >= 4:
        x0 = _float(bbox[0])
        y0 = _float(bbox[1])
        x1 = _float(bbox[2])
        y1 = _float(bbox[3])
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        box = [left, top, right, bottom]
        row["bbox"] = box
        row["bounds"] = box
        row["bounds_rect"] = {
            "exists": right > left and bottom > top,
            "x": left,
            "y": top,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": max(0.0, right - left),
            "height": max(0.0, bottom - top),
            "bbox": box,
        }
    label_bounds = row.get("label_bounds")
    label_bounds_seq = _sequence(label_bounds)
    if len(label_bounds_seq) >= 4:
        lx0 = _float(label_bounds_seq[0])
        ly0 = _float(label_bounds_seq[1])
        lx1 = _float(label_bounds_seq[2])
        ly1 = _float(label_bounds_seq[3])
        left, right = sorted((lx0, lx1))
        top, bottom = sorted((ly0, ly1))
        label_box = [left, top, right, bottom]
        row["label_bounds"] = {
            "exists": right > left and bottom > top,
            "x": left,
            "y": top,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": max(0.0, right - left),
            "height": max(0.0, bottom - top),
            "bbox": label_box,
        }
    canonical_label = _text(row.get("display_label") or row.get("short_label"))
    if canonical_label:
        original_label = _text(row.get("label"))
        if original_label and original_label != canonical_label:
            row.setdefault("raw_label", original_label)
        row["label"] = canonical_label
        row["short_label"] = canonical_label
    normalized_row = normalize_v3_overlay_object(row, strict=False)
    normalized_row = _preserve_forecast_public_metadata(normalized_row, row)
    for preserved_key in ("bounds_rect", "label_bounds", "raw_label"):
        preserved_value = row.get(preserved_key)
        if preserved_value not in (None, "", [], {}):
            normalized_row[preserved_key] = preserved_value
    row = normalized_row
    if not compact:
        return row
    keep_keys = (
        "schema_version",
        "overlay_id",
        "id",
        "object_id",
        "track_id",
        "type",
        "overlay_type",
        "kind",
        "side",
        "direction",
        "action",
        "source_agent",
        "source_version",
        "source_path",
        "source_key",
        "broker_source_lock_id",
        "frame_id",
        "sequence_id",
        "chart_transform_id",
        "bbox",
        "bounds",
        "bounds_rect",
        "truth_score",
        "confidence",
        "label",
        "raw_label",
        "raw_display_label",
        "display_label",
        "display_label_status",
        "unmapped_display_label",
        "short_label",
        "layer",
        "role",
        "visible_modes",
        "visible_default",
        "label_hidden",
        "label_anchor",
        "label_bounds",
        "display_state",
        "visual_weight",
        "geometry_visible",
        "label_visible",
        "inspector_visible",
        "label_mode",
        "label_lane",
        "representation_reason",
        "style",
        "group_id",
        "group_type",
        "group_bounds",
        "summary_label",
        "expand_on_hover",
        "expand_on_click",
        "points",
        "line_points",
        "forecast_band_points",
        "forecast_candles",
        "forecast_scenarios",
        "forecast_anchor",
        "forecast_coordinate_space",
        "forecast_coordinate_units",
        "forecast_direction",
        "trajectory_mode",
        "trajectory_mode_probability_calibrated",
        "body_bias",
        "direction_conflict",
        "path_confidence_status",
        "forecast_quality_status",
        "trade_authorization_status",
        "forecast_engine",
        "forecast_provider",
        "forecast_provider_status",
        "forecast_id",
        "forecast_revision",
        "belief_revision",
        "belief_state",
        "committed_side",
        "candidate_side",
        "change_probability",
        "confirmation_events",
        "required_events",
        "closed_candle_key",
        "closed_candle_sequence",
        "forecast_computed_frame_id",
        "source_forecast_frame_id",
        "geometry_projected_frame_id",
        "geometry_frame_match_verified",
        "geometry_reprojected_from_cache",
        "geometry_projection_provenance",
        "detector_coverage_rebase_applied",
        "cache_replaced_for_detector_coverage_rebase",
        "scene_feature_audit",
        "interval",
        "touch_points",
        "touch_count",
        "trendline_role",
        "trendline_scope",
        "trendline_validation",
        "wick_probe_count",
        "line_obstruction_count",
        "body_cross_fraction",
        "close_distance_norm",
        "significant_close",
        "touch_quality",
        "breach_state",
        "validation_reason",
        "skill_gate",
        "zone_family",
        "liquidity_pool_type",
        "liquidity_source",
        "role_flip_state",
        "zone_stack_id",
        "source_rule",
        "knowledge_tags",
        "replay_sequence",
        "replay_action",
        "story",
        "parent_overlay_id",
        "parent_type",
        "nesting_depth",
        "nesting_role",
        "child_overlay_ids",
        "containment_ratio",
        "z_index",
        "coordinate_mode",
        "anchor_type",
        "anchor_candles",
        "anchor_candle_indices",
        "anchor_price_band",
        "anchor_time_span",
        "anchor_evidence",
        "anchor_evidence_status",
        "lifecycle_state",
        "ttl_ms",
        "reason",
        "precision_rejected",
        "precision_rejection_reason",
    )
    required_keep = set(REQUIRED_FIELDS)
    return {
        key: row[key]
        for key in keep_keys
        if key in row and (key in required_keep or row.get(key) not in (None, "", [], {}))
    }


def _zone_bbox(zone: Mapping[str, Any]) -> list[float]:
    raw = zone.get("bbox") or zone.get("pixel_bbox") or zone.get("bounds") or zone.get("normalized_bbox")
    if isinstance(raw, Mapping):
        raw = _mapping(raw).get("bbox")
    raw_seq = _sequence(raw)
    if len(raw_seq) < 4:
        return []
    x0, y0, x1, y1 = [_float(raw_seq[index]) for index in range(4)]
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right <= left or bottom <= top:
        return []
    return [left, top, right, bottom]


def _bounds_list(value: Any) -> list[float]:
    raw = value
    if isinstance(value, Mapping):
        value_map = _mapping(value)
        raw = value_map.get("bbox") or value_map.get("pixel_bbox") or value_map.get("bounds") or value_map.get("normalized_bbox")
        if isinstance(raw, Mapping):
            raw = _mapping(raw).get("bbox")
    raw_seq = _sequence(raw)
    if len(raw_seq) < 4:
        return []
    x0, y0, x1, y1 = [_float(raw_seq[index]) for index in range(4)]
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right <= left or bottom <= top:
        return []
    return [left, top, right, bottom]


def _broker_control_source(
    session: Mapping[str, Any],
    aliases: Sequence[str],
) -> tuple[list[float], dict[str, Any]]:
    tracking = _mapping(session.get("tracking_summary"))
    surfaces = [
        _mapping(session.get("broker_surface")),
        _mapping(tracking.get("broker_surface")),
    ]
    for surface in surfaces:
        execution_boxes = _mapping(surface.get("execution_boxes"))
        for alias in aliases:
            for source in (_mapping(execution_boxes.get(alias)), _mapping(surface.get(alias))):
                if not source:
                    continue
                if "visible" in source and not _bool(source.get("visible"), True):
                    continue
                if "locked" in source and not _bool(source.get("locked"), True):
                    continue
                bounds = _bounds_list(source)
                if bounds:
                    return bounds, source
    return [], {}


def _broker_control_overlay_objects(
    session: Mapping[str, Any],
    *,
    scene_graph: Mapping[str, Any],
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    broker_source_lock_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    surface_bounds = _bounds_list(scene_graph.get("broker_surface_bounds"))
    if not surface_bounds or surface_bounds[2] - surface_bounds[0] < 100.0 or surface_bounds[3] - surface_bounds[1] < 100.0:
        return []
    definitions: tuple[dict[str, Any], ...] = (
        {
            "source_key": "broker_screen",
            "label": "BROKER SURFACE",
            "role": "broker_screen",
            "scene_key": "broker_surface_bounds",
            "confidence": 0.92,
        },
        {
            "source_key": "right_order_panel",
            "label": "RIGHT ORDER PANEL",
            "role": "right_order_panel",
            "scene_key": "right_order_panel_bounds",
            "aliases": ("order_panel", "right_order_panel"),
            "confidence": 0.92,
        },
        {
            "source_key": "time_button",
            "label": "TIME BUTTON",
            "role": "time_button",
            "aliases": ("time_button", "time_input", "time_box", "time_field", "expiry_time_field"),
            "confidence": 0.86,
        },
        {
            "source_key": "amount_field",
            "label": "AMOUNT FIELD",
            "role": "amount_field",
            "aliases": ("amount_field", "amount_input", "amount_box", "stake_amount"),
            "confidence": 0.84,
        },
        {
            "source_key": "buy_icon",
            "label": "BUY BUTTON",
            "role": "buy_icon",
            "aliases": ("buy_icon", "buy_button"),
            "confidence": 0.90,
        },
        {
            "source_key": "sell_icon",
            "label": "SELL BUTTON",
            "role": "sell_icon",
            "aliases": ("sell_icon", "sell_button"),
            "confidence": 0.90,
        },
    )
    overlays: list[dict[str, Any]] = []
    for index, definition in enumerate(definitions):
        bounds = _bounds_list(scene_graph.get(str(definition.get("scene_key")))) if definition.get("scene_key") else []
        source_row: dict[str, Any] = {}
        if not bounds:
            bounds, source_row = _broker_control_source(
                session,
                tuple(str(item) for item in definition.get("aliases", ())),
            )
        elif definition.get("aliases"):
            source_bounds, source_row = _broker_control_source(
                session,
                tuple(str(item) for item in definition.get("aliases", ())),
            )
            if source_bounds:
                bounds = source_bounds
        if not bounds:
            continue
        confidence = max(0.01, min(1.0, _float(source_row.get("confidence"), _float(definition.get("confidence"), 0.85))))
        source_key = _text(definition.get("source_key"), f"broker_control_{index}")
        label = _text(definition.get("label"), "BROKER CONTROL")
        raw: dict[str, Any] = {
            "overlay_id": f"broker_control_{source_key}_{frame_id}",
            "object_id": f"broker_control_{source_key}",
            "track_id": f"broker_control_{source_key}",
            "type": "BROKER_CONTROL",
            "side": "HOLD",
            "source_agent": "broker_scene_graph_v3",
            "source_key": source_key,
            "broker_source_lock_id": broker_source_lock_id,
            "frame_id": frame_id,
            "sequence_id": sequence_id,
            "chart_transform_id": chart_transform_id,
            "coordinate_mode": "FULL_BROKER_SURFACE",
            "anchor_type": "BROKER_SURFACE",
            "bounds": bounds,
            "truth_score": confidence,
            "confidence": confidence,
            "lifecycle_state": "CONFIRMED",
            "visible_modes": ["BROKER", "CALIBRATION", "INSPECTOR"],
            "visible_default": False,
            "ttl_ms": 30000,
            "created_at_ms": now_ms,
            "reason": f"{label.lower()} locked on broker source",
            "label": label,
            "display_label": label,
            "short_label": label,
            "layer": "broker_controls",
            "role": _text(definition.get("role"), "broker_control"),
            "z_index": 30 + index,
        }
        try:
            overlays.append(
                normalize_v3_overlay_object(
                    raw,
                    strict=False,
                    frame_id=frame_id,
                    sequence_id=sequence_id,
                    chart_transform_id=chart_transform_id,
                    fallback_index=index,
                )
            )
        except Exception:
            continue
    return overlays


def _signal_thesis_overlay_objects(
    thesis: Mapping[str, Any],
    *,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
) -> list[dict[str, Any]]:
    if not bool(thesis.get("active")):
        return []
    side = _text(thesis.get("effective_side") or thesis.get("side")).upper()
    if side not in {"BUY", "SELL"}:
        return []
    opposite = "SELL" if side == "BUY" else "BUY"
    thesis_id = _text(thesis.get("thesis_id"), "active-thesis")
    confidence = max(0.5, min(1.0, _float(thesis.get("confidence"), 0.75)))
    visible_modes = ["CLEAN_LIVE", "COUNCIL", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"]

    def make_overlay(kind: str, overlay_type: str, zone: Mapping[str, Any], *, overlay_side: str, label: str, layer: str, role: str) -> dict[str, Any] | None:
        bbox = _zone_bbox(zone)
        if not bbox:
            return None
        raw: dict[str, Any] = {
            "overlay_id": f"thesis_{thesis_id}_{kind}",
            "object_id": f"thesis_{thesis_id}_{kind}",
            "track_id": f"thesis_{thesis_id}",
            "type": overlay_type,
            "side": overlay_side,
            "bbox": bbox,
            "truth_score": confidence,
            "confidence": confidence,
            "label": label,
            "display_label": label,
            "short_label": label,
            "layer": layer,
            "role": role,
            "visible_modes": visible_modes,
            "visible_default": True,
            "label_hidden": False,
            "label_anchor": "top" if kind == "countertrend_block" else "bottom",
            "z_index": 96 if kind == "countertrend_block" else 88,
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "BOX",
            "source_agent": "signal_thesis_tracker",
            "reason": _text(thesis.get("plain_language"), "Active thesis context"),
            "lifecycle_state": "ACTIVE",
            "frame_id": frame_id,
            "sequence_id": sequence_id,
            "chart_transform_id": chart_transform_id,
        }
        try:
            return normalize_v3_overlay_object(
                raw,
                strict=False,
                frame_id=frame_id,
                sequence_id=sequence_id,
                chart_transform_id=chart_transform_id,
                fallback_index=0,
            )
        except Exception:
            return raw

    entry_zone = _mapping(thesis.get("entry_zone"))
    target_zone = _mapping(thesis.get("target_zone"))
    invalidation_zone = _mapping(thesis.get("invalidation_zone"))
    overlays: list[dict[str, Any]] = []
    for candidate in (
        make_overlay("active_entry", "SNIPER_ENTRY_BOX", entry_zone, overlay_side=side, label=f"ACTIVE {side}", layer="trigger_zones", role="active_thesis_entry"),
        make_overlay("active_target", "TARGET_ZONE_BOX", target_zone, overlay_side=side, label=f"{side} TARGET", layer="target_zones", role="active_thesis_target"),
        make_overlay("active_invalidation", "INVALIDATION_BOX", invalidation_zone, overlay_side=side, label=f"{side} INVALID", layer="invalidation", role="active_thesis_invalidation"),
        make_overlay(
            "countertrend_block",
            "OPPOSING_FORCE",
            invalidation_zone or entry_zone,
            overlay_side=opposite,
            label="OPPOSING FORCE",
            layer="supply_demand",
            role="countertrend_block",
        ),
    ):
        if candidate is not None:
            overlays.append(candidate)
    return overlays


def _tracked_candle_rows(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    tracking = _mapping(session.get("tracking_summary"))
    signal = _mapping(session.get("latest_signal"))
    rows = _sequence_of_mappings(tracking.get("tracked_candles"))
    if rows:
        return rows
    return _sequence_of_mappings(signal.get("tracked_candles"))


def _union_box(boxes: Sequence[Sequence[Any]]) -> list[float]:
    normalized = [_bounds_list(box) for box in boxes]
    usable = [box for box in normalized if box]
    if not usable:
        return []
    return [
        min(box[0] for box in usable),
        min(box[1] for box in usable),
        max(box[2] for box in usable),
        max(box[3] for box in usable),
    ]


def _pad_box(bounds: Sequence[Any], pad_x: float = 4.0, pad_y: float = 6.0) -> list[float]:
    box = _bounds_list(bounds)
    if not box:
        return []
    return [box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y]


def _study_anchor_box(session: Mapping[str, Any], *, candle_count: int = 2) -> tuple[list[float], list[int]]:
    candles = _tracked_candle_rows(session)
    if candles:
        count = max(1, min(int(candle_count), len(candles)))
        selected = candles[-count:]
        bounds = _union_box([_bounds_list(row) for row in selected])
        if bounds:
            first_index = max(0, len(candles) - count)
            return _pad_box(bounds), list(range(first_index, len(candles)))
    thesis = _first_mapping(
        session.get("signal_thesis_v3"),
        _mapping(session.get("latest_signal")).get("signal_thesis_v3"),
        _mapping(session.get("tracking_summary")).get("signal_thesis_v3"),
    )
    for key in ("entry_zone", "target_zone", "invalidation_zone"):
        bounds = _zone_bbox(_mapping(thesis.get(key)))
        if bounds:
            return _pad_box(bounds), []
    tracking = _mapping(session.get("tracking_summary"))
    for source in (
        _mapping(tracking.get("current_box")),
        *(_sequence_of_mappings(tracking.get("structure_boxes"))[-1:]),
    ):
        bounds = _bounds_list(source)
        if bounds:
            return _pad_box(bounds), []
    return [], []


def _direct_scene_forecast_overlay(
    payload: Mapping[str, Any],
    *,
    forecast_state: str,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    now_ms: int,
) -> dict[str, Any] | None:
    """Publish only a complete, already-normalized scene-forecast bundle."""

    if forecast_state not in {"CURRENT", "STALE_DIAGNOSTIC"}:
        return None
    if payload.get("geometry_frame_match_verified") is False:
        return None
    projected_frame_id = _int(payload.get("geometry_projected_frame_id"))
    if projected_frame_id > 0 and projected_frame_id != frame_id:
        return None

    def normalized_points(value: object) -> list[list[float]] | None:
        points: list[list[float]] = []
        for raw_point in _sequence(value):
            point = _sequence(raw_point)
            if len(point) < 2:
                return None
            x_value = _float(point[0], float("nan"))
            y_value = _float(point[1], float("nan"))
            if (
                x_value != x_value
                or y_value != y_value
                or not 0.0 <= x_value <= 1.0
                or not 0.0 <= y_value <= 1.0
            ):
                return None
            points.append([round(x_value, 6), round(y_value, 6)])
        return points

    def normalized_candles(value: object) -> list[dict[str, Any]] | None:
        candles: list[dict[str, Any]] = []
        for raw_candle in sorted(
            _sequence_of_mappings(value),
            key=lambda row: _int(row.get("step")),
        ):
            step = _int(raw_candle.get("step"))
            values = {
                key: _float(raw_candle.get(key), float("nan"))
                for key in (
                    "x_norm",
                    "open_y_norm",
                    "high_y_norm",
                    "low_y_norm",
                    "close_y_norm",
                )
            }
            if any(
                item != item or not 0.0 <= item <= 1.0
                for item in values.values()
            ):
                return None
            if not (
                values["high_y_norm"]
                <= min(values["open_y_norm"], values["close_y_norm"])
                <= max(values["open_y_norm"], values["close_y_norm"])
                <= values["low_y_norm"]
            ):
                return None
            candle: dict[str, Any] = {
                "step": step,
                "label": f"E{step}",
                **{key: round(item, 6) for key, item in values.items()},
                "movement_side": _text(
                    raw_candle.get("movement_side"),
                    "HOLD",
                ).upper(),
                "body_bias": _text(raw_candle.get("body_bias"), "HOLD").upper(),
                "direction_conflict": _bool(
                    raw_candle.get("direction_conflict"),
                    False,
                ),
            }
            for key in ("interval_top_y_norm", "interval_bottom_y_norm"):
                if raw_candle.get(key) is not None:
                    item = _float(raw_candle.get(key), float("nan"))
                    if item != item or not 0.0 <= item <= 1.0:
                        return None
                    candle[key] = round(item, 6)
            candles.append(candle)
        if [row["step"] for row in candles] != list(range(1, 13)):
            return None
        return candles

    line_points = normalized_points(payload.get("line_points"))
    if line_points is None or len(line_points) != 13:
        return None

    forecast_candles = normalized_candles(payload.get("forecast_candles"))
    if forecast_candles is None:
        return None

    forecast_scenarios: list[dict[str, Any]] = []
    for raw_scenario in _sequence_of_mappings(payload.get("forecast_scenarios")):
        side = _text(raw_scenario.get("side"), "HOLD").upper()
        scenario_points = normalized_points(raw_scenario.get("line_points"))
        if (
            side not in {"BUY", "SELL", "HOLD"}
            or scenario_points is None
            or len(scenario_points) != 13
            or scenario_points[0] != line_points[0]
        ):
            return None
        scenario: dict[str, Any] = {
                "side": side,
                "role": _text(raw_scenario.get("role"))[:24],
                "label": _text(raw_scenario.get("label"), f"{side} PATH")[:40],
                "probability": round(
                    max(0.0, min(1.0, _float(raw_scenario.get("probability")))),
                    6,
                ),
                "probability_calibrated": _bool(
                    raw_scenario.get("probability_calibrated"),
                    False,
                ),
                "selected": _bool(raw_scenario.get("selected"), False),
                "raw_selected": _bool(raw_scenario.get("raw_selected"), False),
                "candidate": _bool(raw_scenario.get("candidate"), False),
                "line_points": scenario_points,
                "event_count": 12,
            }
        if "forecast_candles" in raw_scenario:
            scenario_candles = normalized_candles(
                raw_scenario.get("forecast_candles")
            )
            if scenario_candles is None:
                return None
            scenario["forecast_candles"] = scenario_candles
        forecast_scenarios.append(scenario)
    selected = [row for row in forecast_scenarios if row["selected"]]
    scenario_roles = {
        _text(row.get("role")).lower() for row in forecast_scenarios
    }
    scenario_sides = {row["side"] for row in forecast_scenarios}
    if (
        len(forecast_scenarios) != 3
        or not (
            scenario_roles == {"base", "bull", "bear"}
            or scenario_sides == {"BUY", "SELL", "HOLD"}
        )
        or len(selected) != 1
        or selected[0]["line_points"] != line_points
    ):
        return None

    anchor = _mapping(payload.get("forecast_anchor"))
    anchor_x = _float(anchor.get("x_norm"), float("nan"))
    anchor_y = _float(anchor.get("y_norm"), float("nan"))
    if (
        anchor_x != anchor_x
        or anchor_y != anchor_y
        or abs(anchor_x - line_points[0][0]) > 1e-6
        or abs(anchor_y - line_points[0][1]) > 1e-6
    ):
        return None

    metadata = _forecast_public_metadata(payload)
    belief_state = _text(metadata.get("belief_state"), "RESET").upper()
    committed_side = _text(metadata.get("committed_side"), "HOLD").upper()
    if committed_side in {"BUY", "SELL"} and selected[0]["side"] != committed_side:
        # A public belief revision and its selected path must switch atomically.
        return None

    interval_payload = _mapping(payload.get("interval"))
    interval_calibrated = bool(
        _text(interval_payload.get("status")).upper() == "READY"
        and _bool(interval_payload.get("calibrated"), False)
    )
    forecast_band_points: list[list[float]] = []
    if interval_calibrated:
        normalized_band = normalized_points(payload.get("forecast_band_points"))
        if normalized_band is None or len(normalized_band) < 5:
            return None
        forecast_band_points = normalized_band

    selective_authorized = bool(
        forecast_state == "CURRENT"
        and belief_state == "STABLE"
        and committed_side in {"BUY", "SELL"}
        and _forecast_authorization_base_is_current(
            payload,
            require_trade_status=False,
        )
    )
    quality = _text(
        payload.get("forecast_quality_status"),
        "READY" if selective_authorized else "DIAGNOSTIC",
    ).upper()
    status_token = (
        "stale_diagnostic"
        if forecast_state == "STALE_DIAGNOSTIC"
        else "authorized"
        if selective_authorized
        else "low_confidence"
        if quality == "LOW_CONFIDENCE"
        else "diagnostic"
        if quality == "DIAGNOSTIC"
        else "no_edge"
    )
    status_label = (
        "LAST VALID - DIAGNOSTIC"
        if forecast_state == "STALE_DIAGNOSTIC"
        else "AUTHORIZED"
        if selective_authorized
        else "LOW CONFIDENCE - DIAGNOSTIC"
        if quality == "LOW_CONFIDENCE"
        else "DIAGNOSTIC - NO EDGE"
    )
    path_confidence_status = _text(
        payload.get("path_confidence_status"),
        "UNAVAILABLE",
    ).upper()
    confidence = (
        max(0.0, min(1.0, _float(payload.get("path_confidence"))))
        if path_confidence_status in {"READY", "CALIBRATED"}
        and _bool(payload.get("probability_calibrated"), False)
        else 0.0
    )
    geometry_points = [*line_points, *forecast_band_points]
    for scenario in forecast_scenarios:
        geometry_points.extend(scenario["line_points"])
    for candle in forecast_candles:
        geometry_points.extend(
            [
                [candle["x_norm"], candle["high_y_norm"]],
                [candle["x_norm"], candle["low_y_norm"]],
            ]
        )
    x_values = [float(point[0]) for point in geometry_points]
    y_values = [float(point[1]) for point in geometry_points]
    forecast_direction = (
        committed_side
        if committed_side in {"BUY", "SELL"}
        else _text(payload.get("path_side"), "HOLD").upper()
    )
    return {
        "overlay_id": f"scene_forecast_composite_{frame_id}",
        "object_id": f"scene_forecast_composite_{frame_id}",
        "track_id": "scene_forecast_composite",
        # Compatibility type/family for dashboard clients; the public engine
        # and labels below are explicitly the scene forecaster.
        "type": "LSTM_STUDY",
        "side": forecast_direction if selective_authorized else "HOLD",
        "source_agent": "scene_forecaster_v3",
        "source_key": _text(payload.get("schema_version"), "PG_SCENE_FORECAST_V3"),
        "frame_id": frame_id,
        "sequence_id": sequence_id,
        "chart_transform_id": chart_transform_id,
        "coordinate_mode": "CHART_NORMALIZED",
        "anchor_type": "POLYGON",
        "bounds": [
            max(0.0, min(x_values) - 0.003),
            max(0.0, min(y_values) - 0.004),
            min(1.0, max(x_values) + 0.003),
            min(1.0, max(y_values) + 0.004),
        ],
        "line_points": line_points,
        "forecast_band_points": forecast_band_points,
        "forecast_candles": forecast_candles,
        "forecast_scenarios": forecast_scenarios,
        "forecast_anchor": {
            "x_norm": round(anchor_x, 6),
            "y_norm": round(anchor_y, 6),
            "verified_latest_close": _bool(anchor.get("verified_latest_close"), False),
            "source": _text(anchor.get("source"), "MODEL_CAUSAL_CANDLE").upper(),
        },
        "forecast_coordinate_space": "chart",
        "forecast_coordinate_units": "normalized",
        "forecast_direction": forecast_direction,
        "trajectory_mode": _text(payload.get("trajectory_mode"), forecast_direction),
        "trajectory_mode_probability_calibrated": _bool(
            payload.get("trajectory_mode_probability_calibrated"),
            False,
        ),
        "body_bias": _text(payload.get("body_bias"), "HOLD").upper(),
        "direction_conflict": _bool(payload.get("direction_conflict"), False),
        "path_confidence_status": path_confidence_status,
        "forecast_quality_status": quality,
        "trade_authorization_status": (
            "AUTHORIZED" if selective_authorized else "NO_EDGE"
        ),
        "interval": {
            "level": interval_payload.get("level")
            or interval_payload.get("nominal_coverage"),
            "method": _text(interval_payload.get("method"), "UNAVAILABLE").upper(),
            "status": "READY" if interval_calibrated else "UNAVAILABLE",
            "calibrated": interval_calibrated,
            "source_count": max(0, _int(interval_payload.get("source_count"))),
            "coverage": interval_payload.get("coverage"),
        },
        **metadata,
        "truth_score": confidence,
        "confidence": confidence,
        "lifecycle_state": "PREDICTED",
        "visible_modes": ["LSTM_STUDY", "COUNCIL", "INSPECTOR"],
        "visible_default": False,
        "ttl_ms": 12000,
        "created_at_ms": now_ms,
        "reason": (
            f"{status_label}. "
            f"{_text(payload.get('interpretation'), 'Causal scene forecast over twelve closed-candle events.')}"
        ),
        "label": f"SCENE FORECAST E1-E12 - {status_label}",
        "display_label": f"SCENE FORECAST E1-E12 - {status_label}",
        "short_label": f"SCENE FORECAST E1-E12 - {status_label}",
        "layer": "prediction_path",
        "role": f"scene_forecast_composite_{status_token}",
        "z_index": 73,
        "structural_anchor": True,
        "source_rule": (
            "causal_scene_forecaster_v3_closed_candle_events_"
            f"pathwise_interval_{status_token}_no_wall_clock"
        ),
    }


def _study_overlay_objects(
    session: Mapping[str, Any],
    two_candle: Mapping[str, Any],
    lstm: Mapping[str, Any],
    *,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []

    def payload_state(payload: Mapping[str, Any]) -> str:
        if not payload:
            return "INVALID"
        source_frame_id = _int(payload.get("_source_frame_id"))
        display_frame_id = _int(payload.get("_display_frame_id"))
        if source_frame_id <= 0 or display_frame_id <= 0:
            return "INVALID"
        if source_frame_id != display_frame_id or display_frame_id != frame_id:
            return "INVALID"
        if _bool(payload.get("_source_stale_diagnostic"), False):
            return "STALE_DIAGNOSTIC"
        if "fresh" in payload and not _bool(payload.get("fresh"), False):
            return "INVALID"
        if _bool(payload.get("stale"), False) or _bool(payload.get("expired"), False):
            return "INVALID"
        valid_until_epoch = _float(payload.get("_source_valid_until_epoch"), 0.0)
        return "CURRENT" if valid_until_epoch <= 0.0 or valid_until_epoch >= now_ms / 1000.0 else "INVALID"

    def payload_side(payload: Mapping[str, Any], *keys: str) -> str:
        side = "HOLD"
        for key in keys:
            side = _text(payload.get(key)).upper()
            if side:
                break
        return side if side in {"BUY", "SELL", "HOLD"} else "HOLD"

    def add_study(
        *,
        overlay_type: str,
        payload: Mapping[str, Any],
        label: str,
        mode: str,
        candle_count: int,
        fallback_confidence: float,
        fallback_reason: str,
    ) -> None:
        forecast_state = payload_state(payload)
        if forecast_state == "INVALID":
            return
        bounds, anchor_candles = _study_anchor_box(session, candle_count=candle_count)
        if not bounds:
            return
        confidence = max(0.05, min(1.0, _float(payload.get("confidence") or payload.get("contribution"), fallback_confidence)))
        side = payload_side(payload, "side", "direction", "direction_bias")
        raw: dict[str, Any] = {
            "overlay_id": f"{overlay_type.lower()}_{frame_id}",
            "object_id": f"{overlay_type.lower()}_{frame_id}",
            "track_id": f"{overlay_type.lower()}_study",
            "type": overlay_type,
            "side": side,
            "source_agent": "live_state_v3_study_overlay",
            "source_key": _text(payload.get("schema_version") or payload.get("skill") or mode, mode),
            "frame_id": frame_id,
            "sequence_id": sequence_id,
            "chart_transform_id": chart_transform_id,
            "coordinate_mode": "CHART_IMAGE_SPACE",
            "anchor_type": "CANDLES" if anchor_candles else "BOX",
            "anchor_candles": anchor_candles,
            "bounds": bounds,
            "truth_score": confidence,
            "confidence": confidence,
            # Core visibility treats lifecycle=STALE as hidden. Keep the
            # geometry predicted/displayable and encode the explicit stale
            # diagnostic state in its role for the public operator contract.
            "lifecycle_state": "PREDICTED" if forecast_state == "STALE_DIAGNOSTIC" else "ACTIVE",
            "visible_modes": [mode, "COUNCIL", "INSPECTOR"],
            "visible_default": False,
            "ttl_ms": 12000,
            "created_at_ms": now_ms,
            "reason": (
                f"Last valid frame-aligned forecast; diagnostic only. "
                f"{_text(payload.get('summary') or payload.get('reason'), fallback_reason)}"
                if forecast_state == "STALE_DIAGNOSTIC"
                else _text(payload.get("summary") or payload.get("reason"), fallback_reason)
            ),
            "label": f"{label} - LAST VALID" if forecast_state == "STALE_DIAGNOSTIC" else label,
            "display_label": f"{label} - LAST VALID" if forecast_state == "STALE_DIAGNOSTIC" else label,
            "short_label": f"{label} - LAST VALID" if forecast_state == "STALE_DIAGNOSTIC" else label,
            "layer": "active_council_decision",
            "role": (
                f"{mode.lower()}_stale_diagnostic"
                if forecast_state == "STALE_DIAGNOSTIC"
                else mode.lower()
            ),
            "label_anchor": "top",
            "label_hidden": False,
            "z_index": 74 if overlay_type == "TWO_CANDLE_STUDY" else 72,
            "structural_anchor": True,
            "source_rule": "study_overlay_anchored_to_visible_candles",
        }
        if overlay_type == "LSTM_STUDY":
            lstm_authorized = _forecast_authorization_base_is_current(
                payload,
                require_trade_status=True,
            )
            raw["trade_authorization_status"] = (
                "AUTHORIZED" if lstm_authorized else "NO_EDGE"
            )
        raw.update(_forecast_public_metadata(payload))
        try:
            normalized = normalize_v3_overlay_object(
                    raw,
                    strict=False,
                    frame_id=frame_id,
                    sequence_id=sequence_id,
                    chart_transform_id=chart_transform_id,
                    fallback_index=len(overlays),
                )
            overlays.append(
                _preserve_forecast_public_metadata(normalized, raw)
            )
        except Exception:
            overlays.append(raw)

    add_study(
        overlay_type="TWO_CANDLE_STUDY",
        payload=two_candle,
        label="TWO CANDLE STUDY",
        mode="TWO_CANDLE_STUDY",
        candle_count=2,
        fallback_confidence=0.58,
        fallback_reason="Two-candle study anchored to the latest visible candles.",
    )
    lstm_state = payload_state(lstm)
    scene_forecaster = _is_scene_forecast_payload(lstm)
    if scene_forecaster and lstm_state != "INVALID":
        direct_scene = _direct_scene_forecast_overlay(
            lstm,
            forecast_state=lstm_state,
            frame_id=frame_id,
            sequence_id=sequence_id,
            chart_transform_id=chart_transform_id,
            now_ms=now_ms,
        )
        if direct_scene is not None:
            try:
                normalized = normalize_v3_overlay_object(
                    direct_scene,
                    strict=False,
                    frame_id=frame_id,
                    sequence_id=sequence_id,
                    chart_transform_id=chart_transform_id,
                    fallback_index=len(overlays),
                )
                overlays.append(
                    _preserve_forecast_public_metadata(normalized, direct_scene)
                )
            except Exception:
                overlays.append(direct_scene)
            return overlays
    forecast_path = _sequence_of_mappings(lstm.get("forecast_path")) if lstm_state != "INVALID" else []
    trajectory_scenarios = (
        _sequence_of_mappings(lstm.get("trajectory_scenarios"))
        if lstm_state != "INVALID"
        else []
    )
    unqualified_lstm_path = bool(
        forecast_path
        and (
            _bool(lstm.get("legacy_restored"), False)
            or (
                bool(_text(lstm.get("path_target_semantics")))
                and _text(lstm.get("path_target_semantics")).upper()
                != "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR"
            )
        )
    )
    if unqualified_lstm_path:
        # Keep the raw contributor payload for audits, but never turn an old,
        # unvalidated, or malformed-input decoder into visible future candles.
        forecast_path = []
        trajectory_scenarios = []
    if forecast_path:
        # A future trajectory is an ordered event sequence.  Persisted public
        # payloads used to retain only the last eight generic list items, which
        # could attach C5 directly to the current candle and relabel it C1.
        # Reject any fragment instead of drawing a geometrically valid but
        # semantically false path.
        forecast_path = sorted(
            forecast_path,
            key=lambda row: _int(row.get("step"), 0),
        )
        forecast_steps = [_int(row.get("step"), 0) for row in forecast_path]
        if forecast_steps != list(range(1, 13)):
            forecast_path = []
    # A learned future path replaces the old history-window box.  The fallback
    # study box remains useful for a current LSTM payload that has no forecast
    # geometry, but it must never masquerade as predicted candles.
    if not forecast_path and not unqualified_lstm_path:
        add_study(
            overlay_type="LSTM_STUDY",
            payload=lstm,
            label="SCENE FORECASTER STUDY" if scene_forecaster else "LSTM STUDY",
            mode="LSTM_STUDY",
            candle_count=8,
            fallback_confidence=0.50,
            fallback_reason=(
                "Scene forecaster contribution anchored to the latest visible candle window."
                if scene_forecaster
                else "LSTM sequence contribution anchored to the latest visible candle window."
            ),
        )
    candles = _tracked_candle_rows(session)
    features = _sequence_of_mappings(lstm.get("features"))
    source_size = _sequence(lstm.get("source_image_size"))
    if forecast_path and len(source_size) >= 2:
        image_width = max(1.0, _float(source_size[0], 1.0))
        image_height = max(1.0, _float(source_size[1], 1.0))
        centers: list[float] = []
        # The first forecast point must be anchored to the exact causal candle
        # supplied to the model.  A separately compacted tracker list can lag
        # that candle by a frame and previously shifted the path sideways.
        for feature in features:
            center_px = _float(feature.get("center_x_px"), float("nan"))
            if center_px == center_px:
                centers.append(center_px / image_width)
        if not centers:
            for candle in candles:
                box = _bounds_list(candle)
                if not box:
                    continue
                center = 0.5 * (box[0] + box[2])
                centers.append(center if max(abs(value) for value in box) <= 1.0001 else center / image_width)
        if centers:
            latest_x = max(0.0, min(1.0, centers[-1]))
            positive_gaps = [right - left for left, right in zip(centers, centers[1:]) if right > left]
            median_gap = sorted(positive_gaps)[len(positive_gaps) // 2] if positive_gaps else 0.012
            start_close = max(
                0.0,
                min(
                    1.0,
                    _float(features[-1].get("relative_price_location"), 0.5)
                    if features
                    else 0.5,
                ),
            )
            visual_anchor_y = 1.0 - start_close
            latest_candle = candles[-1] if candles else {}
            latest_candle_bounds = _bounds_list(latest_candle)
            anchor_matches_latest_close = False
            if latest_candle_bounds:
                candle_center_x = 0.5 * (
                    latest_candle_bounds[0] + latest_candle_bounds[2]
                )
                if max(abs(value) for value in latest_candle_bounds) > 1.0001:
                    candle_center_x /= image_width
                candle_center_x = max(0.0, min(1.0, candle_center_x))
                # Snap only when the tracker candle and model feature identify
                # the same causal bar.  A compact tracker list can lag; in
                # that case the feature remains the truthful anchor.
                candle_width = abs(
                    latest_candle_bounds[2] - latest_candle_bounds[0]
                )
                if max(abs(value) for value in latest_candle_bounds) > 1.0001:
                    candle_width /= image_width
                # Stay well below one inter-candle slot. The former two-gap
                # tolerance could snap a forecast to an adjacent candle that
                # the model never ingested.
                anchor_tolerance = max(
                    0.0015,
                    min(
                        0.45 * max(0.004, median_gap),
                        0.001 + 0.75 * max(0.001, candle_width),
                    ),
                )
                if abs(candle_center_x - latest_x) <= anchor_tolerance:
                    anchor_matches_latest_close = True
                    latest_x = candle_center_x
                    close_y_px = _float(
                        latest_candle.get("close_y_px")
                        or latest_candle.get("close_y"),
                        float("nan"),
                    )
                    if close_y_px != close_y_px:
                        candle_side = payload_side(latest_candle, "direction")
                        close_y_px = (
                            latest_candle_bounds[1]
                            if candle_side == "BUY"
                            else latest_candle_bounds[3]
                            if candle_side == "SELL"
                            else 0.5
                            * (latest_candle_bounds[1] + latest_candle_bounds[3])
                        )
                    if max(abs(value) for value in latest_candle_bounds) <= 1.0001:
                        visual_anchor_y = max(0.0, min(1.0, close_y_px))
                    else:
                        visual_anchor_y = max(
                            0.0,
                            min(1.0, close_y_px / image_height),
                        )
            available_step = max(
                0.0,
                (0.985 - latest_x) / max(1, len(forecast_path)),
            )
            step_x = min(max(0.004, median_gap), available_step)
            if step_x <= 0.0005:
                # There is no usable future gutter on this chart frame.  Do
                # not stack twelve event candles on the right screen edge.
                return overlays
            vertical_anchor_offset = visual_anchor_y - (1.0 - start_close)

            def projected_price_y(location: float) -> float:
                return max(
                    0.0,
                    min(1.0, 1.0 - location + vertical_anchor_offset),
                )

            start_point = [latest_x, visual_anchor_y]
            center_points = [start_point]
            upper_points = [list(start_point)]
            lower_points = [list(start_point)]
            forecast_candles: list[dict[str, Any]] = []
            forecast_scenarios: list[dict[str, Any]] = []
            for scenario in trajectory_scenarios[:3]:
                scenario_path = sorted(
                    _sequence_of_mappings(scenario.get("forecast_path")),
                    key=lambda row: _int(row.get("step"), 0),
                )
                scenario_steps = [_int(row.get("step"), 0) for row in scenario_path]
                if scenario_steps != list(range(1, len(forecast_path) + 1)):
                    continue
                scenario_points = [list(start_point)]
                for index, scenario_row in enumerate(scenario_path, start=1):
                    scenario_close = max(
                        0.0,
                        min(
                            1.0,
                            _float(
                                scenario_row.get("expected_close_norm"),
                                start_close,
                            ),
                        ),
                    )
                    scenario_points.append(
                        [latest_x + step_x * index, projected_price_y(scenario_close)]
                    )
                scenario_side = payload_side(scenario, "side")
                forecast_scenarios.append(
                    {
                        "side": scenario_side,
                        "label": f"{scenario_side} PATH",
                        "probability": round(
                            max(0.0, min(1.0, _float(scenario.get("probability"), 0.0))),
                            6,
                        ),
                        "probability_calibrated": _bool(
                            scenario.get("probability_calibrated"),
                            False,
                        ),
                        "selected": _bool(scenario.get("selected"), False),
                        "raw_selected": _bool(
                            scenario.get("raw_selected"),
                            False,
                        ),
                        "candidate": _bool(scenario.get("candidate"), False),
                        "role": _text(scenario.get("role"))[:24],
                        "line_points": [list(point) for point in scenario_points],
                        "event_count": len(scenario_path),
                    }
                )
            forecast_scenarios.sort(
                key=lambda scenario: (
                    not bool(scenario.get("selected")),
                    -_float(scenario.get("probability"), 0.0),
                )
            )
            selected_scenarios = [
                scenario
                for scenario in forecast_scenarios
                if bool(scenario.get("selected"))
            ]
            if (
                len(forecast_scenarios) != 3
                or {str(scenario.get("side")) for scenario in forecast_scenarios}
                != {"BUY", "SELL", "HOLD"}
                or len(selected_scenarios) != 1
            ):
                # The multimodal bundle is atomic. Publishing a fragment lets
                # a downstream client silently relabel an alternative as the
                # primary path, so retain the previous complete snapshot
                # instead of emitting a partial forecast.
                return overlays
            interval_payload = _mapping(lstm.get("trajectory_interval"))
            interval_status = _text(
                lstm.get("trajectory_interval_status")
                or interval_payload.get("status"),
                "UNAVAILABLE",
            ).upper()
            interval_calibrated = bool(
                interval_status == "READY"
                and _bool(interval_payload.get("calibrated"), False)
            )
            prior_close = start_close
            for index, row in enumerate(forecast_path, start=1):
                open_location = max(
                    0.0,
                    min(1.0, _float(row.get("expected_open_norm"), prior_close)),
                )
                close_location = max(
                    0.0,
                    min(1.0, _float(row.get("expected_close_norm"), open_location)),
                )
                expected_range = max(0.0005, _float(row.get("expected_range_norm"), 0.006))
                high_location = max(
                    open_location,
                    close_location,
                    min(1.0, _float(row.get("expected_high_norm"), max(open_location, close_location))),
                )
                low_location = min(
                    open_location,
                    close_location,
                    max(0.0, _float(row.get("expected_low_norm"), min(open_location, close_location))),
                )
                lower_close = max(
                    0.0,
                    min(1.0, _float(row.get("close_lower_90_norm"), close_location)),
                )
                upper_close = max(
                    0.0,
                    min(1.0, _float(row.get("close_upper_90_norm"), close_location)),
                )
                lower_close, upper_close = sorted((lower_close, upper_close))
                row_interval_valid = bool(
                    interval_calibrated
                    and "close_lower_90_norm" in row
                    and "close_upper_90_norm" in row
                    and upper_close - lower_close < 0.35
                )
                if not row_interval_valid:
                    interval_calibrated = False
                future_x = latest_x + step_x * index
                center_points.append([future_x, projected_price_y(close_location)])
                upper_points.append([future_x, projected_price_y(upper_close)])
                lower_points.append([future_x, projected_price_y(lower_close)])
                # Body direction and event-to-event path movement are
                # intentionally separate model contracts.  A coherent direct
                # candle open is derived from its body head, so using
                # close-open here would incorrectly relabel body colour as
                # path progression.
                movement_delta = close_location - prior_close
                movement_dead_zone = max(0.0004, 0.03 * expected_range)
                derived_movement_side = (
                    "BUY"
                    if movement_delta > movement_dead_zone
                    else "SELL"
                    if movement_delta < -movement_dead_zone
                    else "HOLD"
                )
                declared_movement_side = _text(row.get("movement_direction")).upper()
                movement_side = (
                    declared_movement_side
                    if declared_movement_side in {"BUY", "SELL", "HOLD"}
                    else derived_movement_side
                )
                body_bias = payload_side(row, "candle_body_direction", "direction")
                direction_conflict = bool(
                    body_bias in {"BUY", "SELL"}
                    and movement_side in {"BUY", "SELL"}
                    and body_bias != movement_side
                )
                forecast_candles.append(
                    {
                        "step": index,
                        "label": f"E{index}",
                        "x_norm": round(future_x, 6),
                        "open_y_norm": round(projected_price_y(open_location), 6),
                        "high_y_norm": round(projected_price_y(high_location), 6),
                        "low_y_norm": round(projected_price_y(low_location), 6),
                        "close_y_norm": round(projected_price_y(close_location), 6),
                        "movement_side": movement_side,
                        "body_bias": body_bias,
                        "direction_conflict": direction_conflict,
                        "interval_top_y_norm": round(projected_price_y(upper_close), 6),
                        "interval_bottom_y_norm": round(projected_price_y(lower_close), 6),
                    }
                )
                prior_close = close_location
            selected_points = _sequence(selected_scenarios[0].get("line_points"))
            if (
                len(center_points) != 13
                or len(forecast_candles) != 12
                or len(selected_points) != 13
                or any(
                    len(_sequence(selected_point)) < 2
                    or abs(float(center_point[0]) - float(_sequence(selected_point)[0]))
                    > 1e-6
                    or abs(float(center_point[1]) - float(_sequence(selected_point)[1]))
                    > 1e-6
                    for center_point, selected_point in zip(
                        center_points,
                        selected_points,
                    )
                )
            ):
                return overlays
            band_points = (
                [*upper_points, *reversed(lower_points), list(upper_points[0])]
                if interval_calibrated
                else []
            )
            geometry_points = [*center_points]
            for scenario in forecast_scenarios:
                geometry_points.extend(_sequence(scenario.get("line_points")))
            for event in forecast_candles:
                geometry_points.extend(
                    [
                        [event["x_norm"], event["high_y_norm"]],
                        [event["x_norm"], event["low_y_norm"]],
                    ]
                )
            geometry_points.extend(band_points)
            x_values = [float(point[0]) for point in geometry_points]
            y_values = [float(point[1]) for point in geometry_points]
            bounds = [
                max(0.0, min(x_values) - 0.003),
                max(0.0, min(y_values) - 0.004),
                min(1.0, max(x_values) + 0.003),
                min(1.0, max(y_values) + 0.004),
            ]
            first_forecast = forecast_path[0]
            selective_status = _text(
                lstm.get("selective_status") or first_forecast.get("selective_status"),
                "NO_EDGE",
            ).upper()
            first_selective_status = _text(
                first_forecast.get("selective_status"),
                "NO_EDGE",
            ).upper()
            selective_authorized = bool(
                _forecast_authorization_base_is_current(
                    lstm,
                    require_trade_status=True,
                )
                and selective_status == "AUTHORIZED"
                and _bool(first_forecast.get("selective_authorized"), False)
                and first_selective_status == "AUTHORIZED"
                and lstm_state == "CURRENT"
            )
            forecast_quality_status = _text(
                lstm.get("forecast_quality_status"),
                "READY" if selective_authorized else "NO_EDGE",
            ).upper()
            status_token = (
                "stale_diagnostic"
                if lstm_state == "STALE_DIAGNOSTIC"
                else "authorized"
                if selective_authorized
                else "low_confidence"
                if forecast_quality_status == "LOW_CONFIDENCE"
                else "diagnostic"
                if forecast_quality_status == "DIAGNOSTIC"
                else "no_edge"
            )
            forecast_direction = payload_side(lstm, "path_side", "side")
            # NO_EDGE is an abstention, not a weak trade instruction.  Keep
            # the directional lean in metadata for inspection but render the
            # composite neutrally unless the path-specific gate authorizes it.
            overlay_side = forecast_direction if selective_authorized else "HOLD"
            path_confidence_status = _text(
                lstm.get("path_confidence_status"),
                "UNAVAILABLE",
            ).upper()
            confidence = (
                max(0.0, min(1.0, _float(lstm.get("path_confidence"), 0.0)))
                if path_confidence_status == "READY"
                else 0.0
            )
            status_label = (
                "LAST VALID - DIAGNOSTIC"
                if lstm_state == "STALE_DIAGNOSTIC"
                else "AUTHORIZED"
                if selective_authorized
                else "LOW CONFIDENCE - DIAGNOSTIC"
                if forecast_quality_status == "LOW_CONFIDENCE"
                else "DIAGNOSTIC - NO EDGE"
                if forecast_quality_status == "DIAGNOSTIC"
                else "NO EDGE - DIAGNOSTIC"
            )
            interpretation = _text(
                lstm.get("interpretation"),
                (
                    "Causal scene-forecaster candle-event path; a future-price band is shown only when pathwise calibrated."
                    if scene_forecaster
                    else "Causal V3 LSTM candle-event path; a future-price band is shown only when pathwise calibrated."
                ),
            )
            public_metadata = _forecast_public_metadata(lstm)
            public_prefix = "SCENE FORECAST E1-E12" if scene_forecaster else "LSTM V3 EVENTS"
            role_prefix = "scene_forecast" if scene_forecaster else "lstm_forecast"

            path_raw: dict[str, Any] = {
                    "overlay_id": f"{role_prefix}_composite_{frame_id}",
                    "object_id": f"{role_prefix}_composite_{frame_id}",
                    "track_id": f"{role_prefix}_composite",
                    "type": "LSTM_STUDY",
                    "side": overlay_side,
                    "source_agent": "lstm_candle_sequence_v3",
                    "source_key": _text(
                        lstm.get("schema_version"),
                        "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
                    ),
                    "frame_id": frame_id,
                    "sequence_id": sequence_id,
                    "chart_transform_id": chart_transform_id,
                    "coordinate_mode": "CHART_NORMALIZED",
                    "anchor_type": "POLYGON",
                    "anchor_candles": list(range(max(0, len(candles) - 8), len(candles))),
                    "bounds": bounds,
                    "line_points": [list(point) for point in center_points],
                    "forecast_band_points": [list(point) for point in band_points],
                    "forecast_candles": forecast_candles,
                    "forecast_scenarios": forecast_scenarios,
                    "forecast_anchor": {
                        "x_norm": round(float(start_point[0]), 6),
                        "y_norm": round(float(start_point[1]), 6),
                        "verified_latest_close": anchor_matches_latest_close,
                        "source": (
                            "TRACKER_LATEST_CLOSE"
                            if anchor_matches_latest_close
                            else "MODEL_CAUSAL_CANDLE"
                        ),
                    },
                    "forecast_coordinate_space": "chart",
                    "forecast_coordinate_units": "normalized",
                    "forecast_direction": forecast_direction,
                    "trajectory_mode": _text(lstm.get("trajectory_mode"), ""),
                    "trajectory_mode_probability_calibrated": _bool(
                        lstm.get("trajectory_mode_probability_calibrated"),
                        False,
                    ),
                    "body_bias": payload_side(lstm, "side"),
                    "direction_conflict": bool(
                        _bool(lstm.get("direction_conflict"), False)
                        or any(bool(row.get("direction_conflict")) for row in forecast_candles)
                    ),
                    "path_confidence_status": path_confidence_status,
                    "forecast_quality_status": forecast_quality_status,
                    "trade_authorization_status": (
                        "AUTHORIZED" if selective_authorized else "NO_EDGE"
                    ),
                    "interval": {
                        "level": 0.90,
                        "method": _text(
                            interval_payload.get("method"),
                            "PATHWISE_CONFORMAL" if interval_calibrated else "UNAVAILABLE",
                        ),
                        "status": "READY" if interval_calibrated else "UNAVAILABLE",
                        "calibrated": interval_calibrated,
                        "source_count": _int(interval_payload.get("source_count"), 0),
                        "coverage": interval_payload.get("coverage"),
                    },
                    **public_metadata,
                    "truth_score": confidence,
                    "confidence": confidence,
                    "lifecycle_state": "PREDICTED",
                    "visible_modes": ["LSTM_STUDY", "COUNCIL", "INSPECTOR"],
                    "visible_default": False,
                    "ttl_ms": 12000,
                    "created_at_ms": now_ms,
                    "reason": f"{status_label}. {interpretation}",
                    "label": f"{public_prefix} - {status_label}",
                    "display_label": f"{public_prefix} - {status_label}",
                    "short_label": f"{public_prefix} - {status_label}",
                    "layer": "prediction_path",
                    "role": f"{role_prefix}_composite_{status_token}",
                    "z_index": 73,
                    "structural_anchor": True,
                    "source_rule": (
                        (
                            "causal_scene_forecaster_v3_candle_events_"
                            if scene_forecaster
                            else "causal_direct_multi_horizon_lstm_v3_candle_events_"
                        )
                        + f"pathwise_interval_{status_token}_no_wall_clock"
                    ),
                }
            try:
                normalized = normalize_v3_overlay_object(
                        path_raw,
                        strict=False,
                        frame_id=frame_id,
                        sequence_id=sequence_id,
                        chart_transform_id=chart_transform_id,
                        fallback_index=len(overlays),
                    )
                overlays.append(
                    _preserve_forecast_public_metadata(normalized, path_raw)
                )
            except Exception:
                overlays.append(path_raw)
    return overlays


def _council_overlay_objects(
    session: Mapping[str, Any],
    study_packet: Mapping[str, Any] | None,
    *,
    frame_id: int,
    sequence_id: str,
    chart_transform_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    bounds, anchor_candles = _study_anchor_box(session, candle_count=1)
    if not bounds:
        return []
    council = _model_council_summary(session, study_packet)
    side = _text(council.get("side"), "HOLD").upper()
    if side not in {"BUY", "SELL", "HOLD"}:
        side = "HOLD"
    confidence = max(0.35, min(1.0, _float(council.get("confidence"), _float(_mapping(session.get("latest_signal")).get("effective_confidence"), 0.62))))
    raw: dict[str, Any] = {
        "overlay_id": f"model_council_marker_{frame_id}",
        "object_id": f"model_council_marker_{frame_id}",
        "track_id": "model_council_marker",
        "type": "MODEL_COUNCIL_MARKER",
        "side": side,
        "source_agent": "live_state_v3_council_overlay",
        "source_key": _text(council.get("state"), "COUNCIL"),
        "frame_id": frame_id,
        "sequence_id": sequence_id,
        "chart_transform_id": chart_transform_id,
        "coordinate_mode": "CHART_IMAGE_SPACE",
        "anchor_type": "CANDLES" if anchor_candles else "BOX",
        "anchor_candles": anchor_candles,
        "bounds": bounds,
        "truth_score": confidence,
        "confidence": confidence,
        "lifecycle_state": "ACTIVE",
        "visible_modes": ["COUNCIL", "ACTIVE_CONTEXT", "INSPECTOR"],
        "visible_default": False,
        "ttl_ms": 12000,
        "created_at_ms": now_ms,
        "reason": _text(council.get("summary"), "Model council marker anchored to the active chart read."),
        "label": "MODEL COUNCIL MARKER",
        "display_label": "MODEL COUNCIL MARKER",
        "short_label": "MODEL COUNCIL MARKER",
        "layer": "active_council_decision",
        "role": "model_council",
        "label_anchor": "right",
        "label_hidden": False,
        "z_index": 76,
        "structural_anchor": True,
        "source_rule": "council_overlay_anchored_to_current_candle",
    }
    try:
        return [
            normalize_v3_overlay_object(
                raw,
                strict=False,
                frame_id=frame_id,
                sequence_id=sequence_id,
                chart_transform_id=chart_transform_id,
                fallback_index=0,
            )
        ]
    except Exception:
        return [raw]


def _overlay_visible_for_mode(overlay: Mapping[str, Any], mode: str, *, now_ms: int | float | None = None) -> bool:
    if bool(overlay.get("precision_rejected", False)):
        return False
    normalized_mode = normalize_view_mode(mode)
    if normalized_mode == "CLEAN_LIVE" and overlay.get("visible_default") is False:
        return False
    created_at_ms = _float(overlay.get("created_at_ms"), 0.0)
    effective_now_ms = now_ms if created_at_ms > 0.0 else None
    return overlay_is_visible(overlay, normalized_mode, now_ms=effective_now_ms)


def _overlay_is_frame_aligned(overlay: Mapping[str, Any], frame_id: int) -> bool:
    if frame_id <= 0:
        return True
    layer = _text(overlay.get("layer")).lower()
    if layer in {"historical_replay", "replay"}:
        return True
    overlay_frame = _int(overlay.get("frame_id") or overlay.get("frame_index"))
    return overlay_frame <= 0 or overlay_frame == frame_id


def _overlay_artifact_alignment(
    *,
    overlay_artifact: Mapping[str, Any],
    overlay_object_frame_id: int,
) -> tuple[bool, str, int]:
    artifact_frame_id = _int(overlay_artifact.get("frame_id"))
    if artifact_frame_id <= 0 or overlay_object_frame_id <= 0:
        return True, "", artifact_frame_id
    if artifact_frame_id == overlay_object_frame_id:
        return True, "", artifact_frame_id
    return (
        False,
        f"overlay artifact frame {artifact_frame_id} does not match overlay object frame {overlay_object_frame_id}",
        artifact_frame_id,
    )


def _surface_signatures_match(session: Mapping[str, Any]) -> bool:
    display_signature = _text(
        session.get("last_display_surface_signature")
        or session.get("last_window_surface_signature")
        or session.get("display_surface_signature")
    )
    overlay_signature = _text(
        session.get("overlay_source_window_signature")
        or session.get("last_window_surface_signature")
        or session.get("last_study_surface_signature")
    )
    return bool(display_signature and overlay_signature and display_signature == overlay_signature)


def _display_only_overlay_authority_locked(
    session: Mapping[str, Any],
    *,
    overlay_object_frame_id: int = 0,
    source_block_reason: str = "",
) -> bool:
    if source_block_reason:
        return False
    display_frame = _int(session.get("frame_index") or session.get("chart_frame_id") or session.get("display_frame_id"))
    has_display_artifact = bool(session.get("last_display_window_path") or session.get("last_window_path") or session.get("last_frame_path"))
    if display_frame > 0 and overlay_object_frame_id > 0 and display_frame == overlay_object_frame_id and has_display_artifact:
        return True
    if not (bool(session.get("display_snapshot_only_v3")) or bool(session.get("display_fast_path_v3"))):
        return False
    if _surface_signatures_match(session):
        return True
    overlay_frame = max(_int(session.get("overlay_frame_id")), _int(session.get("full_overlay_frame_id")))
    has_overlay_artifact = bool(session.get("last_overlay_path") or session.get("last_full_overlay_path"))
    return bool(overlay_frame > 0 and display_frame > 0 and has_overlay_artifact and has_display_artifact)


def _mode_visible_layers(mode: str) -> list[str]:
    profile = view_mode_profile(mode)
    layer_visibility = _mapping(profile.get("layer_visibility"))
    ordered_layers = OverlayLayerManagerV3(mode).layer_order()
    return [layer for layer in ordered_layers if _bool(layer_visibility.get(layer), False)]


def _first_present(sources: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> tuple[bool, Any]:
    for source in sources:
        for key in keys:
            if key in source:
                return True, source.get(key)
    return False, None


def _source_text_status_wrong(sources: Sequence[Mapping[str, Any]]) -> bool:
    for source in sources:
        for key in ("status", "source_status", "lock_status", "validation_status", "reason"):
            value = source.get(key)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if "wrong_surface" in lowered or "wrong surface" in lowered or "surface_mismatch" in lowered:
                    return True
    return False


def _broker_source_summary(session: Mapping[str, Any]) -> dict[str, Any]:
    broker_surface = _mapping(session.get("broker_surface"))
    locked_window = _mapping(session.get("locked_window"))
    descriptor = _mapping(session.get("descriptor"))
    tracking = _mapping(session.get("tracking_summary"))
    tracking_surface = _mapping(tracking.get("broker_surface"))
    sources: list[dict[str, Any]] = [
        _mapping(session.get("broker_source")),
        _mapping(session.get("broker_source_lock")),
        _mapping(session.get("broker_source_summary")),
        _mapping(session.get("source_validation")),
        _mapping(broker_surface.get("broker_source")),
        _mapping(broker_surface.get("broker_source_lock")),
        _mapping(broker_surface.get("source")),
        _mapping(broker_surface.get("source_validation")),
        _mapping(broker_surface.get("lock_validation")),
        tracking_surface,
        _mapping(tracking_surface.get("broker_source")),
        _mapping(tracking_surface.get("broker_source_lock")),
    ]
    sources = [source for source in sources if source]

    def bool_field(keys: Sequence[str], default: bool) -> bool:
        found, value = _first_present(sources, keys)
        return _bool(value, default) if found else default

    lock_found, lock_value = _first_present(
        sources,
        ("lock_id", "source_lock_id", "surface_lock_id", "broker_lock_id", "window_lock_id", "locked_hwnd", "hwnd"),
    )
    lock_id = _text(lock_value) if lock_found else ""
    if not lock_id:
        lock_id = _text(
            session.get("lock_id")
            or session.get("source_capture_id")
            or session.get("display_capture_id")
            or locked_window.get("hwnd")
            or descriptor.get("hwnd")
            or locked_window.get("title")
            or session.get("locked_title")
            or descriptor.get("title")
        )

    wrong_surface = bool_field(
        (
            "wrong_surface",
            "surface_mismatch",
            "window_mismatch",
            "lock_mismatch",
            "wrong_window",
            "wrong_broker_surface",
        ),
        False,
    ) or _source_text_status_wrong(sources)
    url_valid = bool_field(("url_valid", "frame_url_valid", "window_url_valid", "source_url_valid"), True)
    pixel_fingerprint_valid = bool_field(
        (
            "pixel_fingerprint_valid",
            "fingerprint_valid",
            "visual_fingerprint_valid",
            "broker_surface_hash_stable",
            "surface_hash_stable",
        ),
        True,
    )

    title_found, title_value = _first_present(sources, ("title_valid", "window_title_valid", "source_title_valid"))
    if title_found:
        title_valid = _bool(title_value, True)
    else:
        title_valid = True
    if not title_valid:
        wrong_surface = True

    valid_found, valid_value = _first_present(
        sources,
        ("valid", "source_valid", "lock_valid", "surface_valid", "broker_source_valid", "is_valid"),
    )
    base_valid = _bool(valid_value, True) if valid_found else True
    valid = bool(base_valid and not wrong_surface and url_valid and title_valid and pixel_fingerprint_valid)
    status_found, status_value = _first_present(sources, ("status", "lock_status"))
    status_text = _text(status_value).upper() if status_found else ""
    if status_text and status_text not in {"VALID", "OK", "PASS", "LOCKED"}:
        valid = False
        if status_text in {"WRONG_SURFACE", "TITLE_MATCH_PIXEL_MISMATCH"}:
            wrong_surface = True
    return {
        "lock_id": lock_id,
        "valid": valid,
        "status": status_text or ("VALID" if valid else "UNKNOWN"),
        "wrong_surface": bool(wrong_surface),
        "url_valid": bool(url_valid),
        "title_valid": bool(title_valid),
        "pixel_fingerprint_valid": bool(pixel_fingerprint_valid),
    }


def _broker_source_block_reason(broker_source: Mapping[str, Any]) -> str:
    if not broker_source:
        return ""
    if _bool(broker_source.get("wrong_surface"), False):
        return "broker source rejected: wrong surface"
    if _bool(broker_source.get("valid"), True):
        return ""
    invalid_fields = [
        label
        for label, key in (
            ("url", "url_valid"),
            ("title", "title_valid"),
            ("pixel_fingerprint", "pixel_fingerprint_valid"),
        )
        if not _bool(broker_source.get(key), True)
    ]
    suffix = ", ".join(invalid_fields) if invalid_fields else "source lock"
    return f"broker source rejected: invalid {suffix}"


def _empty_overlay_reason(
    *,
    source_block_reason: str,
    total_count: int,
    renderable_count: int,
    active_mode: str,
) -> str:
    if renderable_count > 0:
        return ""
    if source_block_reason:
        return source_block_reason
    if total_count <= 0:
        return "no market overlays available for the current broker surface"
    return f"no renderable overlays for mode {active_mode}"


def _broker_surface_summary(
    session: Mapping[str, Any],
    surface_frame: Mapping[str, Any],
    frame_timing: Mapping[str, Any],
    *,
    now_epoch: float,
) -> dict[str, Any]:
    frame_url = _text(surface_frame.get("url"))
    frame_age = _float(frame_timing.get("frame_age_ms"), 0.0)
    if frame_age <= 0.0:
        published = _float(session.get("display_published_epoch") or session.get("last_capture_epoch"), 0.0)
        if published > 0.0:
            frame_age = max(0.0, (float(now_epoch) - published) * 1000.0)
    return {
        "frame_id": _int(session.get("display_frame_id") or session.get("frame_index") or session.get("capture_count")),
        "frame_url": frame_url,
        "url": frame_url,
        "image_url": frame_url,
        "latest_window_url": frame_url,
        "width": _int(surface_frame.get("width")),
        "height": _int(surface_frame.get("height")),
        "age_ms": round(frame_age, 3),
        "exists": bool(surface_frame.get("exists")),
        "frame": dict(surface_frame),
    }


def _shooter_summary(session_id: str, shooter_state: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = _mapping(shooter_state)
    explicit_available = payload.get("available")
    available = bool(payload) if explicit_available is None else _bool(explicit_available, bool(payload))
    return {
        "available": available,
        "session_id": _text(payload.get("session_id")),
        "session_match": bool(payload and _text(payload.get("session_id")) in {"", session_id}),
        "state": _text(payload.get("state") or payload.get("status")),
        "mode": _text(payload.get("mode") or payload.get("shooter_mode")),
        "side": _text(payload.get("side") or payload.get("action")),
        "raw": payload,
    }


def _two_candle_and_lstm_payloads(
    session: Mapping[str, Any],
    *,
    prefer_scene: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tracking = _mapping(session.get("tracking_summary"))
    signal = _mapping(session.get("latest_signal"))
    result = _mapping(session.get("model_council_result"))
    forecast_snapshot = _mapping(session.get("forecast_snapshot_v3"))
    display_frame_id = _int(
        session.get("display_frame_id")
        or session.get("frame_id")
        or session.get("frame_index")
    )
    study_packet = _first_mapping(
        session.get("model_council_study_packet"),
        session.get("study_packet"),
        result.get("model_council_study_packet"),
        result.get("study_packet"),
        signal.get("model_council_study_packet"),
        tracking.get("model_council_study_packet"),
    )
    kernel = _mapping(signal.get("decision_kernel") or tracking.get("decision_kernel"))
    forecast = _mapping(
        signal.get("high_frequency_forecast")
        or tracking.get("high_frequency_forecast")
        or signal.get("micro_candle_forecast")
        or tracking.get("micro_candle_forecast")
        or kernel.get("high_frequency_forecast")
        or forecast_snapshot.get("high_frequency_forecast")
    )
    two_candle = _first_mapping(
        signal.get("two_candle_study"),
        tracking.get("two_candle_study"),
        forecast.get("two_candle_study"),
        kernel.get("two_candle_study"),
        study_packet.get("two_candle_study"),
        result.get("two_candle_study"),
        forecast_snapshot.get("two_candle_study"),
    )
    scene_candidates = [
        signal.get("scene_forecast_contribution"),
        tracking.get("scene_forecast_contribution"),
        forecast.get("scene_forecast_contribution"),
        kernel.get("scene_forecast_contribution"),
        two_candle.get("scene_forecast_contribution"),
        forecast_snapshot.get("scene_forecast_contribution"),
        session.get("scene_forecast_contribution"),
        study_packet.get("scene_forecast_contribution"),
        result.get("scene_forecast_contribution"),
    ]
    scene_candidate_parents = [
        signal,
        tracking,
        forecast,
        kernel,
        two_candle,
        forecast_snapshot,
        session,
        study_packet,
        result,
    ]
    legacy_candidates = [
        signal.get("lstm_contribution"),
        tracking.get("lstm_contribution"),
        forecast.get("lstm_contribution"),
        kernel.get("lstm_contribution"),
        two_candle.get("lstm_contribution"),
        forecast_snapshot.get("lstm_contribution"),
        study_packet.get("lstm_contribution"),
        result.get("lstm_contribution"),
    ]
    legacy_candidate_parents = [
        signal,
        tracking,
        forecast,
        kernel,
        two_candle,
        forecast_snapshot,
        study_packet,
        result,
    ]
    # Once a first-class scene contribution is present, an older LSTM payload
    # cannot silently reclaim the public forecast during a hand-off.  The
    # legacy list remains the compatibility fallback for older sessions only.
    selecting_scene = bool(
        prefer_scene and any(_mapping(value) for value in scene_candidates)
    )
    if selecting_scene:
        lstm_candidates = scene_candidates
        lstm_candidate_parents = scene_candidate_parents
    else:
        lstm_candidates = legacy_candidates
        lstm_candidate_parents = legacy_candidate_parents

    current_market = _text(
        signal.get("market")
        or tracking.get("detected_market")
        or session.get("market")
    ).upper()
    chart_timeframe = _text(
        signal.get("focus_timeframe")
        or tracking.get("detected_timeframe")
    ).upper()
    high_frequency_timeframe = _text(
        signal.get("high_frequency_study_timeframe")
        or tracking.get("high_frequency_study_timeframe")
        or signal.get("configured_high_frequency_timeframe")
        or tracking.get("configured_high_frequency_timeframe")
        or chart_timeframe
    ).upper()
    # Scene geometry belongs to the detected chart timeframe.  The independent
    # LSTM lane is built on the configured high-frequency study timeframe, which
    # may legitimately differ from the chart (for example M5 study on M1).
    current_timeframe = (
        chart_timeframe if selecting_scene else high_frequency_timeframe
    )
    current_identity_pending = bool(
        signal.get("market_selector_rebind_required")
        or signal.get("market_selector_studying_new_pair")
        or tracking.get("market_selector_rebind_required")
        or tracking.get("market_selector_studying_new_pair")
        or signal.get("market_identity_confirmed") is False
        or signal.get("timeframe_identity_confirmed") is False
        or tracking.get("market_identity_confirmed") is False
        or tracking.get("timeframe_identity_confirmed") is False
    )

    def canonical_identity(value: Any) -> str:
        return "".join(character for character in _text(value).upper() if character.isalnum())

    def candidate_identity(value: Mapping[str, Any]) -> tuple[str, str]:
        state = _mapping(value.get("closed_candle_identity_state"))
        pair = _text(value.get("pair") or state.get("pair")).upper()
        timeframe = _text(value.get("timeframe") or state.get("timeframe")).upper()
        forecast_id = _text(value.get("forecast_id"))
        if forecast_id and (not pair or not timeframe):
            pieces = forecast_id.split("|", 2)
            if len(pieces) >= 2:
                pair = pair or pieces[0].upper()
                timeframe = timeframe or pieces[1].upper()
        return pair, timeframe

    def lstm_candidate_score(value: object, index: int) -> tuple[int, ...]:
        candidate = _mapping(value)
        if not candidate:
            return (0, 0, 0, 0, 0, 0, 0, 0, 0, -index)
        parent = _mapping(lstm_candidate_parents[index])
        path = _sequence_of_mappings(candidate.get("forecast_path"))
        direct_geometry = bool(
            len(_sequence(candidate.get("line_points"))) == 13
            and len(_sequence(candidate.get("forecast_candles"))) == 12
            and len(_sequence(candidate.get("forecast_scenarios"))) == 3
        )
        semantics = _text(candidate.get("path_target_semantics")).upper()
        direct_path = bool(
            direct_geometry
            or (
                path
                and not _bool(candidate.get("legacy_restored"), False)
                and semantics == "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR"
            )
        )
        explicit_candidate_frame_id = _int(
            candidate.get("frame_id") or candidate.get("model_vote_frame_id")
        )
        parent_frame_id = _int(
            parent.get("source_frame_id")
            or parent.get("frame_id")
            or parent.get("model_vote_frame_id")
            or parent.get("display_frame_id")
        )
        candidate_frame_id = explicit_candidate_frame_id or parent_frame_id
        if parent_frame_id > 0 and (
            (
                explicit_candidate_frame_id > 0
                and explicit_candidate_frame_id != parent_frame_id
            )
            or (display_frame_id > 0 and parent_frame_id != display_frame_id)
        ):
            return (0, 0, 0, 0, 0, 0, 0, 0, candidate_frame_id, -index)
        frame_match = bool(
            display_frame_id > 0
            and candidate_frame_id > 0
            and candidate_frame_id == display_frame_id
        )
        candidate_pair, candidate_timeframe = candidate_identity(candidate)
        pair_matches = bool(
            current_market
            and candidate_pair
            and canonical_identity(candidate_pair) == canonical_identity(current_market)
        )
        timeframe_matches = bool(
            current_timeframe
            and candidate_timeframe
            and candidate_timeframe == current_timeframe
        )
        candidate_pending = bool(
            candidate.get("market_identity_confirmed") is False
            or candidate.get("timeframe_identity_confirmed") is False
            or _text(candidate.get("identity_contract_status")).upper() == "PENDING"
            or _text(candidate.get("provider_status")).upper()
            == "MARKET_IDENTITY_PENDING"
        )
        if current_identity_pending:
            identity_safe = bool(candidate_pending and frame_match)
        else:
            explicit_mismatch = bool(
                (current_market and candidate_pair and not pair_matches)
                or (
                    current_timeframe
                    and candidate_timeframe
                    and not timeframe_matches
                )
            )
            identity_safe = not explicit_mismatch
        if not identity_safe:
            return (0, 0, 0, 0, 0, 0, 0, 0, candidate_frame_id, -index)
        identity_match = bool(pair_matches and timeframe_matches)
        return (
            1,
            int(identity_match or candidate_pending),
            int(frame_match),
            int(direct_path and frame_match),
            int(direct_path),
            int(bool(path) or direct_geometry),
            int(
                _bool(
                    candidate.get("forecast_available"),
                    bool(path) or direct_geometry,
                )
            ),
            int(_bool(candidate.get("fresh"), True)),
            candidate_frame_id,
            -index,
        )

    selected_lstm = max(
        enumerate(lstm_candidates),
        key=lambda item: lstm_candidate_score(item[1], item[0]),
    )
    selected_score = lstm_candidate_score(selected_lstm[1], selected_lstm[0])
    lstm = _mapping(selected_lstm[1]) if selected_score[0] else {}
    lstm_parent = _mapping(lstm_candidate_parents[selected_lstm[0]])
    model_frame_id = _int(
        session.get("model_vote_frame_id")
        or study_packet.get("frame_id")
        or two_candle.get("frame_id")
        or lstm.get("frame_id")
    )
    packet_valid_until = _float(
        study_packet.get("valid_until_epoch")
        or study_packet.get("valid_until_epoch_sec"),
        0.0,
    )
    snapshot_frame_id = _int(forecast_snapshot.get("source_frame_id"))
    snapshot_observed_epoch = _float(forecast_snapshot.get("observed_epoch"), 0.0)
    snapshot_stale = _bool(forecast_snapshot.get("stale"), False)

    def with_source_identity(
        payload: Mapping[str, Any],
        *,
        source_parent: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not payload:
            return {}
        parent = _mapping(source_parent)
        row = dict(payload)
        row["_source_frame_id"] = _int(
            payload.get("frame_id")
            or payload.get("model_vote_frame_id")
            or parent.get("frame_id")
            or parent.get("model_vote_frame_id")
            or parent.get("source_frame_id")
            or session.get("model_vote_frame_id")
            or (snapshot_frame_id if not parent else 0)
            or (model_frame_id if not parent else 0)
        )
        row["_display_frame_id"] = display_frame_id
        row["_source_valid_until_epoch"] = _float(
            payload.get("valid_until_epoch")
            or payload.get("valid_until_epoch_sec")
            or parent.get("valid_until_epoch")
            or parent.get("valid_until_epoch_sec")
            or (packet_valid_until if not parent else 0.0),
            0.0,
        )
        row["_source_observed_epoch"] = _float(
            payload.get("observed_epoch")
            or parent.get("observed_epoch")
            or (snapshot_observed_epoch if not parent else 0.0),
            0.0,
        )
        row["_source_stale_diagnostic"] = bool(
            _bool(payload.get("diagnostic_only"), False)
            or _bool(payload.get("stale"), False)
            or _bool(parent.get("diagnostic_only"), False)
            or _bool(parent.get("stale"), False)
            or (snapshot_stale if not parent else False)
        )
        return row

    return with_source_identity(two_candle), with_source_identity(
        lstm,
        source_parent=lstm_parent,
    )


def _visual_plane_state(frontend_heartbeat: Mapping[str, Any] | None) -> dict[str, Any]:
    heartbeat = _mapping(frontend_heartbeat)
    plane = _mapping(heartbeat.get("visual_plane"))
    zoom = _float(plane.get("zoom", heartbeat.get("zoom", 1.0)), 1.0)
    pan_x = _float(plane.get("pan_x", heartbeat.get("pan_x", 0.0)), 0.0)
    pan_y = _float(plane.get("pan_y", heartbeat.get("pan_y", 0.0)), 0.0)
    return {
        "schema_version": "PG_VISUAL_PLANE_STATE_V3",
        "zoom": max(0.25, min(2.5, zoom)),
        "pan_x": pan_x,
        "pan_y": pan_y,
        "locked": True,
        "last_user_change_epoch_ms": _int(plane.get("last_user_change_epoch_ms", heartbeat.get("last_user_change_epoch_ms", 0))),
        "auto_zoom_enabled": False,
        "reason": "Live mode preserves the user-selected visual plane; overlays do not auto-zoom the dashboard.",
    }


def _overlay_layout_payload(
    precision_audit: Mapping[str, Any],
    *,
    rendered_count: int,
    hidden_count: int,
    active_mode: str,
) -> dict[str, Any]:
    report = _mapping(precision_audit.get("precision_report"))
    return {
        "schema_version": "PG_OVERLAY_LAYOUT_V3",
        "mode": active_mode,
        "collision_count": _int(report.get("label_collisions")),
        "hidden_for_budget": hidden_count,
        "rendered_count": rendered_count,
        "duplicate_count": _int(report.get("duplicate_boxes")),
        "duplicate_now_hidden": _int(report.get("duplicate_now_hidden")),
        "current_candle_label_hidden": active_mode == "CLEAN_LIVE",
        "diagnostics_overlap_allowed": active_mode in {"DIAGNOSTICS", "DEBUG", "INSPECTOR"},
    }


def _overlay_vocabulary_payload(
    all_overlays: Sequence[Mapping[str, Any]],
    rendered_overlays: Sequence[Mapping[str, Any]],
    *,
    active_mode: str,
) -> dict[str, Any]:
    approved_labels = list(approved_overlay_display_labels())
    approved_tokens = {str(label).strip().upper().replace(" ", "_") for label in approved_labels}
    unknown_terms: list[str] = []
    remapped_labels: list[dict[str, str]] = []
    visible_unapproved: list[str] = []
    for row in all_overlays:
        raw_label = _text(row.get("unmapped_display_label") or row.get("raw_display_label"))
        display_label = _text(row.get("display_label") or row.get("short_label"))
        status = _text(row.get("display_label_status"))
        if status == "unmapped" and raw_label and raw_label not in unknown_terms:
            unknown_terms.append(raw_label)
        if status == "remapped" and raw_label and display_label and raw_label != display_label:
            item = {"raw": raw_label, "display": display_label}
            if item not in remapped_labels:
                remapped_labels.append(item)
    visible_label_rows = [row for row in rendered_overlays if row.get("label_hidden") is not True and row.get("label_hidden") != "true"]
    for row in visible_label_rows:
        display_label = _text(row.get("display_label") or row.get("label"))
        if display_label and not is_approved_overlay_display_label(display_label) and display_label not in visible_unapproved:
            visible_unapproved.append(display_label)
    return {
        "schema_version": "PG_OVERLAY_VOCABULARY_AUDIT_V3",
        "active_mode": active_mode,
        "approved_label_count": len(approved_labels),
        "approved_labels": approved_labels,
        "approved_label_tokens": sorted(approved_tokens),
        "visible_labels": [_text(row.get("display_label") or row.get("label")) for row in visible_label_rows if _text(row.get("display_label") or row.get("label"))],
        "visible_unapproved_labels": visible_unapproved,
        "dictionary_coverage_ok": not visible_unapproved,
        "unknown_or_unmapped_terms": unknown_terms,
        "labels_remapped": remapped_labels,
    }


def _overlay_ledger_payload(
    all_overlays: Sequence[Mapping[str, Any]],
    rendered_overlays: Sequence[Mapping[str, Any]],
    *,
    active_mode: str,
) -> dict[str, Any]:
    rendered_ids = {
        _text(row.get("overlay_id") or row.get("id") or row.get("object_id"))
        for row in rendered_overlays
        if _text(row.get("overlay_id") or row.get("id") or row.get("object_id"))
    }
    rows: list[dict[str, Any]] = []
    display_counts: dict[str, int] = {}
    rejected_count = 0
    for index, row in enumerate(all_overlays):
        overlay_id = _text(row.get("overlay_id") or row.get("id") or row.get("object_id"), f"overlay-{index}")
        if row.get("precision_rejected"):
            rejected_count += 1
            continue
        display_state = _text(row.get("display_state"), "COMPACT").upper()
        display_counts[display_state] = display_counts.get(display_state, 0) + 1
        chart_visible = overlay_id in rendered_ids
        label_visible = bool(chart_visible and row.get("label_hidden") is not True and row.get("label_visible") is not False)
        rows.append(
            {
                "overlay_id": overlay_id,
                "object_id": _text(row.get("object_id")),
                "track_id": _text(row.get("track_id")),
                "type": _text(row.get("type")),
                "layer": _text(row.get("layer")),
                "display_label": _text(row.get("display_label") or row.get("label")),
                "display_state": display_state,
                "visual_weight": round(_float(row.get("visual_weight"), 0.0), 3),
                "semantic_family": _text(_mapping(row.get("style")).get("semantic_family")),
                "source_agent": _text(row.get("source_agent")),
                "truth_score": row.get("truth_score"),
                "chart_visible": chart_visible,
                "geometry_visible": bool(chart_visible and row.get("geometry_visible") is not False),
                "label_visible": label_visible,
                "inspector_visible": bool(row.get("inspector_visible") is not False),
                "label_lane": _text(row.get("label_lane") or row.get("label_anchor")),
                "parent_overlay_id": _text(row.get("parent_overlay_id")),
                "group_id": _text(row.get("group_id")),
                "reason": _text(row.get("representation_reason") or row.get("reason")),
            }
        )
    rows.sort(key=lambda item: (bool(item["chart_visible"]), _float(item.get("visual_weight"), 0.0)), reverse=True)
    return {
        "schema_version": "PG_OVERLAY_LEDGER_V3",
        "active_mode": active_mode,
        "valid_count": len(rows),
        "ledger_count": len(rows),
        "chart_visible_geometry_count": len([row for row in rows if row["geometry_visible"]]),
        "label_visible_count": len([row for row in rows if row["label_visible"]]),
        "inspector_visible_count": len([row for row in rows if row["inspector_visible"]]),
        "rejected_count": rejected_count,
        "display_state_counts": display_counts,
        "objects": rows,
    }


def _compact_scalar_and_selected(payload: Mapping[str, Any], selected_keys: set[str]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            compact[str(key)] = value
        elif key in selected_keys:
            compact[str(key)] = value
    return compact


def _compact_scene_forecast_contribution(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not payload or not _is_scene_forecast_payload(payload):
        return dict(payload)
    compact = _compact_scalar_and_selected(
        payload,
        {
            "line_points",
            "forecast_band_points",
            "forecast_candles",
            "forecast_scenarios",
            "forecast_anchor",
            "forecast_quantiles",
            "interval",
            "raw_side_probabilities",
            "side_probabilities",
            "belief_posterior",
            "scene_feature_schema",
        },
    )
    # Replace the potentially large field-by-field audit and never expose the
    # persisted HMM checkpoint/model internals on the fast public poll path.
    compact.pop("belief_tracker_checkpoint", None)
    compact.update(_forecast_public_metadata(payload))
    return compact


def _compact_lstm_forecast_events(
    value: Any,
) -> list[dict[str, Any]]:
    event_keys = {
        "step",
        "event",
        "direction",
        "trajectory_mode",
        "trajectory_mode_probability",
        "candle_body_direction",
        "movement_direction",
        "horizon_position_direction",
        "buy_probability",
        "sell_probability",
        "path_buy_probability",
        "path_sell_probability",
        "path_probability_calibrated",
        "confidence",
        "expected_open_norm",
        "expected_high_norm",
        "expected_low_norm",
        "expected_close_norm",
        "close_lower_90_norm",
        "close_upper_90_norm",
        "expected_delta_norm",
        "expected_cumulative_delta_norm",
        "expected_body_ratio",
        "expected_upper_wick_ratio",
        "expected_lower_wick_ratio",
        "expected_range_norm",
    }
    events: list[dict[str, Any]] = []
    for raw_event in _sequence_of_mappings(value):
        event = {
            key: raw_event[key]
            for key in event_keys
            if key in raw_event
            and isinstance(raw_event[key], (str, int, float, bool))
        }
        events.append(event)
    return events


def _compact_lstm_points(value: Any) -> list[list[float]]:
    points: list[list[float]] = []
    for raw_point in _sequence(value):
        point = _sequence(raw_point)
        if len(point) < 2:
            continue
        points.append(
            [
                round(_float(point[0]), 6),
                round(_float(point[1]), 6),
            ]
        )
    return points


def _compact_lstm_candles(value: Any) -> list[dict[str, Any]]:
    candle_keys = {
        "step",
        "label",
        "x_norm",
        "open_y_norm",
        "high_y_norm",
        "low_y_norm",
        "close_y_norm",
        "interval_top_y_norm",
        "interval_bottom_y_norm",
        "movement_side",
        "body_bias",
        "direction_conflict",
    }
    return [
        {
            key: candle[key]
            for key in candle_keys
            if key in candle
            and isinstance(candle[key], (str, int, float, bool))
        }
        for candle in _sequence_of_mappings(value)
    ]


def _compact_lstm_scenarios(
    value: Any,
) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for raw_scenario in _sequence_of_mappings(value):
        scenario = {
            key: raw_scenario[key]
            for key in (
                "side",
                "role",
                "label",
                "probability",
                "probability_calibrated",
                "selected",
                "raw_selected",
                "candidate",
                "event_count",
            )
            if key in raw_scenario
            and isinstance(raw_scenario[key], (str, int, float, bool))
        }
        if "line_points" in raw_scenario:
            scenario["line_points"] = _compact_lstm_points(
                raw_scenario.get("line_points")
            )
        if "forecast_candles" in raw_scenario:
            scenario["forecast_candles"] = _compact_lstm_candles(
                raw_scenario.get("forecast_candles")
            )
        if "forecast_path" in raw_scenario:
            scenario["forecast_path"] = _compact_lstm_forecast_events(
                raw_scenario.get("forecast_path")
            )
        scenarios.append(scenario)
    return scenarios


def project_public_lstm_contribution_v3(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the LSTM lane onto its small, fail-closed public contract."""

    if not payload or _is_scene_forecast_payload(payload):
        return {}
    forecast_available = _bool(payload.get("forecast_available"), False)
    authorized = _forecast_authorization_base_is_current(
        payload,
        require_trade_status=True,
    )
    scalar_keys = {
        "schema_version",
        "blocker",
        "fresh",
        "side",
        "path_side",
        "trajectory_mode",
        "trajectory_mode_probability_calibrated",
        "path_confidence",
        "path_confidence_status",
        "forecast_quality_status",
        "direction_conflict",
        "net_expected_path_delta_norm",
        "next_1_direction",
        "next_1_probability",
        "next_2_direction",
        "next_2_probability",
        "continuation_probability",
        "reversal_probability",
        "pullback_first_probability",
        "confidence",
        "horizon_steps",
        "horizon_unit",
        "clock_time_assumption",
        "timeframe",
    }
    compact: dict[str, Any] = {
        key: payload[key]
        for key in scalar_keys
        if key in payload
        and isinstance(payload[key], (str, int, float, bool))
    }
    compact.update(
        {
            "forecast_available": forecast_available,
            "forecast_authorized": authorized,
            "trade_authorization_status": (
                "AUTHORIZED" if authorized else "NO_EDGE"
            ),
        }
    )
    if payload.get("forecast_path") is not None:
        compact["forecast_path"] = _compact_lstm_forecast_events(
            payload.get("forecast_path")
        )
    if payload.get("trajectory_scenarios") is not None:
        compact["trajectory_scenarios"] = _compact_lstm_scenarios(
            payload.get("trajectory_scenarios")
        )
    for key in ("line_points", "forecast_band_points"):
        if payload.get(key) is not None:
            compact[key] = _compact_lstm_points(payload.get(key))
    if payload.get("forecast_candles") is not None:
        compact["forecast_candles"] = _compact_lstm_candles(
            payload.get("forecast_candles")
        )
    if payload.get("forecast_scenarios") is not None:
        compact["forecast_scenarios"] = _compact_lstm_scenarios(
            payload.get("forecast_scenarios")
        )
    anchor = _mapping(payload.get("forecast_anchor"))
    if anchor:
        compact["forecast_anchor"] = {
            key: anchor[key]
            for key in (
                "x_norm",
                "y_norm",
                "verified_latest_close",
                "source",
            )
            if key in anchor
            and isinstance(anchor[key], (str, int, float, bool))
        }
    interval = _mapping(payload.get("interval"))
    if interval:
        compact["interval"] = {
            key: interval[key]
            for key in (
                "level",
                "method",
                "status",
                "calibrated",
                "source_count",
                "coverage",
            )
            if key in interval
            and isinstance(interval[key], (str, int, float, bool))
        }
    metadata = _forecast_public_metadata(payload)
    for key in (
        "belief_state",
        "committed_side",
        "candidate_side",
        "change_probability",
        "confirmation_events",
        "required_events",
    ):
        if key in metadata:
            compact[key] = metadata[key]
    interpretation = _text(payload.get("interpretation"))
    if interpretation:
        compact["interpretation"] = interpretation[:512]
    return compact


def _compact_playbook_ai_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    summary = value.get("playbook_ai_summary_v3")
    if isinstance(summary, Mapping):
        return dict(cast(Mapping[str, Any], summary))
    intelligence = value.get("playbook_ai_intelligence_v3")
    if isinstance(intelligence, Mapping):
        return compact_playbook_ai_intelligence_v3(cast(Mapping[str, Any], intelligence))
    return {}


def _compact_recent_studies(rows: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    studies = _sequence_of_mappings(rows)
    compact_rows: list[dict[str, Any]] = []
    for row in studies[-limit:]:
        compact_rows.append(
            {
                key: row.get(key)
                for key in (
                    "timestamp",
                    "created_at",
                    "side",
                    "action",
                    "confidence",
                    "summary",
                    "setup",
                    "execution_action",
                    "execution_block_reason",
                    "state",
                    "packet_id",
                )
                if row.get(key) not in (None, "", [], {})
            }
        )
    return compact_rows


def _compact_tracking_summary(tracking: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "artifact_integrity",
        "box_context",
        "broker_execution_state",
        "broker_identity",
        "broker_source",
        "broker_source_lock",
        "broker_surface",
        "candle_extraction",
        "candle_movement_context",
        "candle_movement_context_v3",
        "candle_statistics",
        "chart_region",
        "current_box",
        "decision_kernel",
        "display_region",
        "execution_timing",
        "focus_region",
        "global_local_control",
        "high_frequency_forecast",
        "instrument_context",
        "scene_forecast_contribution",
        "lstm_contribution",
        "major_trend_context",
        "map_timing",
        "micro_candle_forecast",
        "overlay_truth_audit",
        "phoenixguard_report",
        "pipeline_timing",
        "projection",
        "historical_structure",
        "angle_vectors",
        "signal_thesis_v3",
        "smart_money_context",
        "structure_boxes",
        "study_stage_timings",
        "support_resistance_context",
        "support_resistance_zones",
        "symbol_context",
        "timing_signal",
        "two_candle_study",
        "tracked_candles",
    }
    compact = _compact_scalar_and_selected(tracking, selected)
    compact["model_council_result_present"] = bool(tracking.get("model_council_result"))
    compact["model_council_packet_present"] = bool(tracking.get("model_council") or tracking.get("model_council_study_packet"))
    return compact


def _compact_latest_signal(signal: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "broker_execution_state",
        "broker_source",
        "broker_source_lock",
        "candle_extraction",
        "candle_movement_context",
        "candle_movement_context_v3",
        "countertrend_lane",
        "decision_kernel",
        "entry_distance",
        "execution_timing",
        "global_local_control",
        "high_frequency_forecast",
        "instrument_context",
        "scene_forecast_contribution",
        "lstm_contribution",
        "major_trend_context",
        "map_timing",
        "micro_candle_forecast",
        "overlay_instructions",
        "overlay_truth_audit",
        "pipeline_timing",
        "projection",
        "historical_structure",
        "angle_vectors",
        "probability",
        "signal_thesis_v3",
        "smart_money_context",
        "study_stage_timings",
        "support_resistance_context",
        "support_resistance_zones",
        "symbol_context",
        "timing_signal",
        "two_candle_study",
    }
    compact = _compact_scalar_and_selected(signal, selected)
    compact["model_council_result_present"] = bool(signal.get("model_council_result"))
    compact["model_council_packet_present"] = bool(signal.get("model_council") or signal.get("model_council_study_packet"))
    return compact


def _compact_model_council_result(result: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "angle_context",
        "candle_movement",
        "candle_movement_context_v3",
        "current_candle_contract",
        "entry_quality",
        "execution_lane",
        "final_reasoning_decision",
        "book_strategy",
        "book_strategy_state",
        "book_strategy_playbook",
        "instrument_context",
        "market_play",
        "market_reality",
        "pair_profile",
        "price_location",
        "playbook_ai_summary_v3",
        "reality_adjustments",
        "reasoning_arbitration",
        "regime",
        "promotion_trace",
        "sequence_context",
        "sequence_context_readiness",
        "signal_thesis_v3",
        "symbol_context",
        "time_to_reward_invalidation",
        "timing_decision",
        "timing_forecast",
        "trade_permission",
        "strategy_read",
        "two_candle_study",
        "scene_forecast_contribution",
        "lstm_contribution",
    }
    compact = _compact_scalar_and_selected(result, selected)
    playbook_ai_summary = _compact_playbook_ai_summary(result)
    if playbook_ai_summary:
        compact["playbook_ai_summary_v3"] = playbook_ai_summary
    council = _mapping(result.get("model_council"))
    if council:
        compact["model_council"] = _compact_scalar_and_selected(
            council,
            {
                "execution",
                "candle_movement",
                "candle_movement_context_v3",
                "trade_permission",
                "entry_quality",
                "book_strategy",
                "book_strategy_state",
                "book_strategy_playbook",
                "strategy_read",
                "playbook_ai_summary_v3",
                "market_reality",
                "promotion_trace",
                "sequence_context",
                "sequence_context_readiness",
            },
        )
        council_summary = _compact_playbook_ai_summary(council)
        if council_summary:
            compact["model_council"]["playbook_ai_summary_v3"] = council_summary
    compact["study_packet_present"] = bool(result.get("study_packet") or result.get("model_council_study_packet"))
    return compact


def _compact_performance_trace_v3(trace: Mapping[str, Any]) -> dict[str, Any]:
    timing = _mapping(trace.get("timing_trace"))
    visual_health = _mapping(trace.get("visual_health"))
    keep_timing_keys = {
        "schema_version",
        "frame_id",
        "frame_age_ms",
        "overlay_age_ms",
        "model_vote_age_ms",
        "packet_age_ms",
        "frontend_render_age_ms",
        "state_publish_age_ms",
        "overlay_state_version",
        "overlay_frame_state_version",
        "model_state_version",
        "display_frame_id",
        "overlay_frame_id",
        "overlay_frame_gap",
        "raw_overlay_frame_gap",
        "surface_signature_aligned",
        "display_only_authority_locked",
        "frame_gap_status",
        "stale_status",
        "stale_flags",
        "freshness_score",
        "source",
    }
    compact: dict[str, Any] = {
        "schema_version": trace.get("schema_version"),
        "session_id": trace.get("session_id"),
        "generated_epoch": trace.get("generated_epoch"),
        "frame_id": trace.get("frame_id"),
        "state_version": trace.get("state_version"),
        "overlay_state_version": trace.get("overlay_state_version"),
        "overlay_frame_state_version": trace.get("overlay_frame_state_version"),
        "display_frame": _compact_scalar_and_selected(
            _mapping(trace.get("display_frame")),
            {"frame_id", "age_ms", "url"},
        ),
        "overlay_state": _compact_scalar_and_selected(
            _mapping(trace.get("overlay_state")),
            {"frame_id", "age_ms", "fresh", "overlay_state_version", "overlay_frame_state_version"},
        ),
        "model_state": _compact_scalar_and_selected(
            _mapping(trace.get("model_state")),
            {"frame_id", "age_ms", "fresh", "models_awake", "models_total", "queue_depth"},
        ),
        "frontend_state": _compact_scalar_and_selected(
            _mapping(trace.get("frontend_state")),
            {"age_ms", "fresh"},
        ),
        "metrics": dict(_mapping(trace.get("metrics"))),
        "timing_trace": _compact_scalar_and_selected(timing, keep_timing_keys),
        "visual_health": _compact_scalar_and_selected(
            visual_health,
            {"status", "frame_age_ms", "overlay_age_ms", "model_vote_age_ms", "packet_age_ms", "frontend_render_age_ms", "stale_flags"},
        ),
        "adaptive_performance": _compact_scalar_and_selected(
            _mapping(trace.get("adaptive_performance")),
            {"schema_version", "profile", "reasons", "rule"},
        ),
    }
    stale_status = timing.get("stale_status") or visual_health.get("status")
    if stale_status:
        compact["stale_status"] = stale_status
    return compact


def _compact_session_payload(session: Mapping[str, Any]) -> dict[str, Any]:
    keep_keys = (
        "session_id",
        "name",
        "market",
        "window_query",
        "layout_profile",
        "effective_layout_profile",
        "capture_interval_sec",
        "rl_track_interval_sec",
        "status",
        "tracking_enabled",
        "created_at",
        "updated_at",
        "last_capture_at",
        "last_capture_started_epoch",
        "last_capture_epoch",
        "display_frame_id",
        "display_capture_epoch",
        "display_published_epoch",
        "chart_frame_id",
        "overlay_frame_id",
        "full_overlay_frame_id",
        "model_vote_frame_id",
        "model_capture_epoch",
        "model_council_update_pending",
        "model_council_pending_frame_id",
        "model_council_pending_capture_epoch",
        "source_capture_id",
        "last_window_surface_signature",
        "last_display_surface_signature",
        "last_study_surface_signature",
        "overlay_source_window_signature",
        "overlay_source_study_signature",
        "capture_count",
        "frame_index",
        "state_version",
        "decision_version",
        "decision_valid_until_epoch",
        "visual_observation_v3",
        "forecast_snapshot_v3",
        "scene_forecast_contribution",
        "lstm_contribution",
        "locked_window",
        "locked_title",
        "manual_focus_region",
        "focus_selector",
        "execution_controls",
        "broker_source",
        "broker_source_lock",
        "broker_surface",
        "broker_execution_state",
        "last_frame_path",
        "last_window_path",
        "last_display_window_path",
        "last_chart_path",
        "last_display_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
        "last_decision_path",
        "memory_projection_predict",
        "memory_projection_future",
        "memory_projection_active_mode",
        "execution_debug",
        "execution_debug_log_path",
        "signal_thesis_v3",
        "model_council_study_packet",
        "model_council_packet",
        "execution_packet",
    )
    compact = {key: session.get(key) for key in keep_keys if key in session}
    now_epoch = time.time()
    for packet_key in ("model_council_packet", "execution_packet"):
        if packet_key in compact and not _current_execution_packet(compact.get(packet_key), now_epoch=now_epoch):
            compact.pop(packet_key, None)
    if not compact.get("model_council_packet") and not compact.get("execution_packet"):
        broker_execution_state = _mapping(compact.get("broker_execution_state"))
        if _text(broker_execution_state.get("status")).lower() == "external_shooter_required":
            broker_execution_state.update(
                {
                    "status": "blocked_by_runtime",
                    "message": "Model Council V3 executable packet expired or is missing; waiting for a fresh PG_EXECUTION_PACKET_V3.",
                    "side": "HOLD",
                    "lane": "LIVE_MARKET_FLOW_WAIT",
                    "actionable": False,
                }
            )
            compact["broker_execution_state"] = broker_execution_state
    if bool(compact.get("tracking_enabled", False)):
        current_status = str(compact.get("status", "") or "").strip().lower()
        compact["status"] = current_status if current_status in {"running", "tracking"} else "running"
    compact["tracking_summary"] = _compact_tracking_summary(_mapping(session.get("tracking_summary")))
    compact["latest_signal"] = _compact_latest_signal(_mapping(session.get("latest_signal")))
    compact["model_council_result"] = _compact_model_council_result(_mapping(session.get("model_council_result")))
    compact["recent_studies"] = _compact_recent_studies(session.get("recent_studies"))
    return compact


def _compact_live_poll_session_payload(session: Mapping[str, Any]) -> dict[str, Any]:
    selected = {
        "session_id",
        "name",
        "market",
        "window_query",
        "layout_profile",
        "effective_layout_profile",
        "capture_interval_sec",
        "rl_track_interval_sec",
        "status",
        "tracking_enabled",
        "created_at",
        "updated_at",
        "last_capture_at",
        "last_capture_started_epoch",
        "last_capture_epoch",
        "display_frame_id",
        "display_capture_epoch",
        "display_published_epoch",
        "chart_frame_id",
        "overlay_frame_id",
        "full_overlay_frame_id",
        "model_vote_frame_id",
        "model_capture_epoch",
        "source_capture_id",
        "last_window_surface_signature",
        "last_display_surface_signature",
        "last_study_surface_signature",
        "overlay_source_window_signature",
        "overlay_source_study_signature",
        "capture_count",
        "frame_index",
        "state_version",
        "decision_version",
        "decision_valid_until_epoch",
        "visual_observation_v3",
        "forecast_snapshot_v3",
        "locked_window",
        "locked_title",
        "manual_focus_region",
        "focus_selector",
        "execution_controls",
        "broker_source",
        "broker_source_lock",
        "broker_surface",
        "broker_execution_state",
        "last_frame_path",
        "last_window_path",
        "last_display_window_path",
        "last_chart_path",
        "last_display_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
    }
    compact = {key: session.get(key) for key in selected if key in session}
    latest_signal = _mapping(session.get("latest_signal"))
    compact["latest_signal"] = _compact_scalar_and_selected(
        latest_signal,
        {
            "session_id",
            "signal_id",
            "side",
            "action",
            "execution_action",
            "confidence",
            "status",
            "final_state",
            "lane",
            "execution_lane",
            "symbol",
            "pair",
            "timeframe",
            "published_epoch",
            "signal_age_sec",
            "broker_source",
            "broker_source_lock",
            "promotion_failure_audit_v3",
        },
    )
    tracking = _mapping(session.get("tracking_summary"))
    compact["tracking_summary"] = _compact_scalar_and_selected(
        tracking,
        {
            "artifact_integrity",
            "session_id",
            "detected_market",
            "detected_timeframe",
            "configured_high_frequency_timeframe",
            "high_frequency_study_timeframe",
            "status",
            "frame_index",
            "capture_count",
            "display_frame_id",
            "last_capture_epoch",
            "active_track_count",
            "broker_source",
            "broker_source_lock",
            "broker_surface",
            "candle_extraction",
            "chart_region",
            "pipeline_timing",
            "tracked_candles",
            "visible_candle_count",
        },
    )
    tracked_candles = _sequence_of_mappings(tracking.get("tracked_candles"))
    if tracked_candles:
        # The operator needs only the latest causal window to corroborate the
        # public current-candle close. Keep this internal compact path bounded.
        compact["tracking_summary"]["tracked_candles"] = [
            dict(row) for row in tracked_candles[-8:]
        ]
    model_result = _mapping(session.get("model_council_result"))
    if model_result:
        compact["model_council_result"] = _compact_scalar_and_selected(
            model_result,
            {
                "packet_id",
                "state",
                "final_state",
                "side",
                "final_side",
                "lane",
                "execution_lane",
                "score",
                "confidence",
                "actionable",
                "book_strategy",
                "book_strategy_state",
                "book_strategy_playbook",
                "strategy_read",
                "playbook_ai_summary_v3",
                "denied_at",
                "next_required",
                "promotion_failure_audit_v3",
            },
        )
        playbook_ai_summary = _compact_playbook_ai_summary(model_result)
        if playbook_ai_summary:
            compact["model_council_result"]["playbook_ai_summary_v3"] = playbook_ai_summary
    return compact


def build_live_state_v3(
    session_payload: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Path | str] | None = None,
    active_objects: Sequence[Mapping[str, Any]] | None = None,
    registry_entries: Sequence[Mapping[str, Any]] | None = None,
    study_packet: Mapping[str, Any] | None = None,
    execution_packet: Mapping[str, Any] | None = None,
    model_health: Mapping[str, Any] | None = None,
    shooter_state: Mapping[str, Any] | None = None,
    frontend_heartbeat: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
    artifact_probe: Callable[[str], Path | str] | None = None,
    overlay_mode: str = "CLEAN_LIVE",
    compact_public: bool = False,
) -> dict[str, Any]:
    now_value = float(now_epoch if now_epoch is not None else time.time())
    now_ms = int(now_value * 1000.0)
    requested_overlay_mode = _text(overlay_mode, "CLEAN_LIVE")
    active_overlay_mode = normalize_view_mode(requested_overlay_mode)
    visible_layers = _mode_visible_layers(active_overlay_mode)
    overlay_mode_payload: dict[str, Any] = {
        "requested": requested_overlay_mode,
        "active": active_overlay_mode,
        "available_modes": list(VIEW_MODES),
        "visible_layers": visible_layers,
        "reason_if_empty": "",
    }
    session = dict(session_payload)
    session_id = _text(session.get("session_id"), "session")
    visual_observation = _mapping(session.get("visual_observation_v3"))
    waiting_for_new_frame = bool(
        _text(visual_observation.get("status")).upper() == "WAITING_FOR_NEW_FRAME"
        and not _bool(visual_observation.get("new_visual_evidence"), False)
    )
    # Identical pixels do not create new evidence, but they also do not make
    # geometry tied to the still-displayed frame spatially wrong. Pause the
    # wall-clock TTL while waiting; the public projection marks these objects
    # stale/diagnostic and execution remains independently revoked.
    visibility_now_ms: int | None = None if waiting_for_new_frame else now_ms
    model_health_payload = dict(model_health or {})
    broker_source = _broker_source_summary(session)
    source_block_reason = _broker_source_block_reason(broker_source)
    artifact_refs = _artifact_refs(session, artifacts=artifacts, artifact_probe=artifact_probe)
    plot = _plot_area(session, artifact_refs)
    chart_transform = _chart_transform(session, plot)
    registry = build_market_object_registry_v3(session)
    scene_graph = build_broker_scene_graph_v3(session, artifacts=artifact_refs).as_dict()["scene_graph"]
    display_overlay_artifact = artifact_refs["full-overlay"] if artifact_refs["full-overlay"]["exists"] else artifact_refs["overlay"]
    overlay_artifact_aligned, overlay_artifact_mismatch_reason, overlay_artifact_frame_id = _overlay_artifact_alignment(
        overlay_artifact=display_overlay_artifact,
        overlay_object_frame_id=registry.frame_id,
    )
    overlay_authority_locked = bool(
        not overlay_artifact_aligned
        and _display_only_overlay_authority_locked(
            session,
            overlay_object_frame_id=registry.frame_id,
            source_block_reason=source_block_reason,
        )
    )
    overlay_render_alignment_ok = bool(overlay_artifact_aligned or overlay_authority_locked)
    current_side = _text(
        _mapping(session.get("latest_signal")).get("action")
        or _mapping(session.get("latest_signal")).get("side")
        or _mapping(session.get("latest_signal")).get("execution_action")
    ).upper()
    two_candle_study, selected_forecaster = _two_candle_and_lstm_payloads(session)
    scene_forecast_contribution = (
        selected_forecaster
        if _is_scene_forecast_payload(selected_forecaster)
        else {}
    )
    _, lstm_contribution = _two_candle_and_lstm_payloads(
        session,
        prefer_scene=False,
    )
    if _is_scene_forecast_payload(lstm_contribution):
        # Older sessions may still carry the historical scene-as-LSTM alias.
        # Keep that compatibility input from becoming a second scene overlay.
        lstm_contribution = {}
    raw_overlays = _combine_overlays(
        registry,
        active_objects=active_objects,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
        scene_graph=scene_graph,
    )
    signal_thesis = _first_mapping(
        session.get("signal_thesis_v3"),
        _mapping(session.get("latest_signal")).get("signal_thesis_v3"),
        _mapping(session.get("tracking_summary")).get("signal_thesis_v3"),
        _mapping(session.get("model_council_result")).get("signal_thesis_v3"),
    )
    thesis_overlays = [] if source_block_reason else _signal_thesis_overlay_objects(
        signal_thesis,
        frame_id=registry.frame_id,
        sequence_id=registry.sequence_context.sequence_id,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
    )
    if source_block_reason:
        study_overlays = []
    else:
        primary_forecaster = scene_forecast_contribution or lstm_contribution
        study_overlays = _study_overlay_objects(
            session,
            two_candle_study,
            primary_forecaster,
            frame_id=registry.frame_id,
            sequence_id=registry.sequence_context.sequence_id,
            chart_transform_id=str(chart_transform["chart_transform_id"]),
            now_ms=now_ms,
        )
        if scene_forecast_contribution and lstm_contribution:
            study_overlays.extend(
                _study_overlay_objects(
                    session,
                    {},
                    lstm_contribution,
                    frame_id=registry.frame_id,
                    sequence_id=registry.sequence_context.sequence_id,
                    chart_transform_id=str(chart_transform["chart_transform_id"]),
                    now_ms=now_ms,
                )
            )
    council_overlays = [] if source_block_reason else _council_overlay_objects(
        session,
        study_packet or _mapping(session.get("model_council_study_packet")),
        frame_id=registry.frame_id,
        sequence_id=registry.sequence_context.sequence_id,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
        now_ms=now_ms,
    )
    broker_control_modes = {"BROKER", "CALIBRATION"}
    broker_control_overlays = [] if source_block_reason or active_overlay_mode not in broker_control_modes else _broker_control_overlay_objects(
        session,
        scene_graph=scene_graph,
        frame_id=registry.frame_id,
        sequence_id=registry.sequence_context.sequence_id,
        chart_transform_id=str(chart_transform["chart_transform_id"]),
        broker_source_lock_id=_text(broker_source.get("lock_id")),
        now_ms=now_ms,
    )
    precision_input_overlays = raw_overlays + thesis_overlays + study_overlays + council_overlays + broker_control_overlays
    if source_block_reason:
        precision_overlays = []
        precision_audit: dict[str, Any] = {
            "schema_version": OVERLAY_PRECISION_AUDIT_SCHEMA_VERSION,
            "frame_id": registry.frame_id,
            "overlay_count": len(precision_input_overlays),
            "rendered_count": 0,
            "rejected_count": len(precision_input_overlays),
            "precision_report": {
                "unanchored_boxes": 0,
                "oversized_boxes": 0,
                "duplicate_boxes": 0,
                "label_collisions": 0,
                "outside_plot_area": 0,
                "stale_frame_id": 0,
                "missing_transform": 0,
                "refined_oversized_inputs": 0,
                "outside_rejected": 0,
                "unanchored_inputs_fixed": 0,
                "broker_source_rejected": len(precision_input_overlays),
            },
            "source_block_reason": source_block_reason,
        }
    else:
        precision_overlays, precision_audit = resolve_precision_overlays_v3(
            precision_input_overlays,
            scene_graph=scene_graph,
            mode=active_overlay_mode,
            current_side=current_side,
            frame_id=registry.frame_id,
        )
    layer_manager = OverlayLayerManagerV3(active_overlay_mode, now_ms=visibility_now_ms)
    clean_overlays_only = str(os.getenv("PHOENIXGUARD_LIVE_STATE_CLEAN_OVERLAYS_ONLY", "0") or "0").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    clean_mode_prefilter = bool(clean_overlays_only and active_overlay_mode == "CLEAN_LIVE")
    if overlay_render_alignment_ok:
        overlay_source = [
            overlay
            for overlay in precision_overlays
            if (
                not clean_mode_prefilter
                or _overlay_visible_for_mode(overlay, "CLEAN_LIVE", now_ms=visibility_now_ms)
            )
            and _overlay_visible_for_mode(overlay, active_overlay_mode, now_ms=visibility_now_ms)
            and _overlay_is_frame_aligned(overlay, registry.frame_id)
        ]
    else:
        overlay_source = []
    overlay_source = sorted(overlay_source, key=layer_manager.overlay_sort_key)[: int(layer_manager.as_dict()["active_budget"])]
    overlays = [
        _dashboard_overlay_object(overlay, compact=clean_overlays_only)
        for overlay in overlay_source
    ]
    total_overlay_count = len(precision_input_overlays) if source_block_reason else len(precision_overlays)
    rejected_overlay_count = len(precision_input_overlays) if source_block_reason else len(
        [overlay for overlay in precision_overlays if overlay.get("precision_rejected")]
    )
    renderable_overlay_count = len(overlays)
    hidden_overlay_count = max(0, total_overlay_count - renderable_overlay_count - rejected_overlay_count)
    reason_if_empty = _empty_overlay_reason(
        source_block_reason=source_block_reason,
        total_count=total_overlay_count,
        renderable_count=renderable_overlay_count,
        active_mode=active_overlay_mode,
    )
    if overlay_artifact_mismatch_reason and renderable_overlay_count == 0 and not overlay_authority_locked:
        reason_if_empty = overlay_artifact_mismatch_reason
    overlay_mode_payload["reason_if_empty"] = reason_if_empty
    overlay_mode_payload["artifact_frame_id"] = overlay_artifact_frame_id
    overlay_mode_payload["overlay_object_frame_id"] = registry.frame_id
    overlay_mode_payload["artifact_frame_aligned"] = overlay_render_alignment_ok
    overlay_mode_payload["artifact_authority_locked"] = overlay_authority_locked
    overlays_payload: dict[str, Any] = {
        "count": renderable_overlay_count,
        "total_count": total_overlay_count,
        "renderable_count": renderable_overlay_count,
        "hidden_count": hidden_overlay_count,
        "rejected_count": rejected_overlay_count,
        "artifact_frame_id": overlay_artifact_frame_id,
        "overlay_object_frame_id": registry.frame_id,
        "artifact_frame_aligned": overlay_render_alignment_ok,
        "artifact_authority_locked": overlay_authority_locked,
        "artifact_mismatch_reason": overlay_artifact_mismatch_reason,
        "unknown_or_unmapped_terms": [],
        "vocabulary": {},
        "objects": overlays,
        "all_objects": [
            _dashboard_overlay_object(overlay, compact=True)
            for overlay in precision_overlays
            if not bool(overlay.get("precision_rejected", False))
        ],
    }
    sequence_context = registry.sequence_context.as_dict()
    visual_health = build_visual_health_v3(
        session_id=session_id,
        artifacts=artifact_refs,
        overlay_objects=cast(list[Mapping[str, Any]], overlays),
        sequence_context={"source_status": registry.source_status, **sequence_context},
        model_health=model_health_payload,
        frontend_heartbeat=frontend_heartbeat,
    )
    frame_timing = build_frame_timing_trace_v3(
        session,
        overlays=overlays,
        model_health=model_health_payload,
        frontend_heartbeat=frontend_heartbeat,
        now_epoch=now_value,
    )
    model_warm_state = model_warm_states_from_health(
        model_health_payload,
        frame_id=registry.frame_id,
        now_epoch=now_value,
    )
    surface_frame = artifact_refs["window"]
    chart_frame = artifact_refs["chart"]
    broker_surface_payload = _broker_surface_summary(session, surface_frame, frame_timing, now_epoch=now_value)
    execution_packet_payload = (
        _current_execution_packet(execution_packet, now_epoch=now_value)
        or _current_execution_packet(session.get("model_council_packet"), now_epoch=now_value)
        or _current_execution_packet(session.get("execution_packet"), now_epoch=now_value)
    )
    visual_plane = _visual_plane_state(frontend_heartbeat)
    instrument_payload = _instrument(session)
    overlay_layout = _overlay_layout_payload(
        precision_audit,
        rendered_count=renderable_overlay_count,
        hidden_count=hidden_overlay_count,
        active_mode=active_overlay_mode,
    )
    overlay_vocabulary = _overlay_vocabulary_payload(
        [*precision_overlays, *thesis_overlays, *study_overlays, *council_overlays],
        overlays,
        active_mode=active_overlay_mode,
    )
    overlay_ledger = _overlay_ledger_payload(
        precision_overlays,
        overlays,
        active_mode=active_overlay_mode,
    )
    overlays_payload["unknown_or_unmapped_terms"] = overlay_vocabulary["unknown_or_unmapped_terms"]
    overlays_payload["vocabulary"] = overlay_vocabulary
    overlays_payload["ledger"] = overlay_ledger
    prediction_overlay = prediction_overlay_config()
    identity_overlay: Mapping[str, Any] = next(
        (
            overlay
            for overlay in [*overlays, *precision_overlays]
            if _text(overlay.get("chart_transform_id")) or _text(overlay.get("broker_source_lock_id"))
        ),
        cast(Mapping[str, Any], {}),
    )
    tracking_summary = _mapping(session.get("tracking_summary"))
    chart_transform_id = _text(
        identity_overlay.get("chart_transform_id") or chart_transform.get("chart_transform_id"),
        f"ct_{session_id}_{registry.frame_id}",
    )
    broker_source_lock_id = _text(
        identity_overlay.get("broker_source_lock_id")
        or broker_source.get("lock_id")
        or _mapping(identity_overlay.get("broker_source")).get("lock_id")
        or f"bsl_{session_id}_{registry.frame_id}"
    )
    symbol = _text(
        instrument_payload.get("market")
        or session.get("market")
        or tracking_summary.get("detected_market")
        or tracking_summary.get("market")
    )
    timeframe = _text(
        instrument_payload.get("timeframe")
        or session.get("timeframe")
        or tracking_summary.get("detected_timeframe")
        or tracking_summary.get("timeframe")
    )
    live_visual_state: dict[str, Any] = {
        "schema_version": LIVE_STATE_SCHEMA_VERSION,
        "session_id": session_id,
        "frame_id": registry.frame_id,
        "capture_count": _int(session.get("capture_count") or session.get("frame_index")),
        "state_version": _int(session.get("state_version")),
        "chart_transform_id": chart_transform_id,
        "broker_source_lock_id": broker_source_lock_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_mode": requested_overlay_mode,
        "active_mode": active_overlay_mode,
        "visible_layers": visible_layers,
        "overlay_count": total_overlay_count,
        "renderable_count": renderable_overlay_count,
        "hidden_count": hidden_overlay_count,
        "rejected_count": rejected_overlay_count,
        "overlay_artifact_frame_id": overlay_artifact_frame_id,
        "overlay_object_frame_id": registry.frame_id,
        "overlay_artifact_frame_aligned": overlay_render_alignment_ok,
        "overlay_artifact_authority_locked": overlay_authority_locked,
        "reason_if_empty": reason_if_empty,
        "unknown_or_unmapped_terms": overlay_vocabulary["unknown_or_unmapped_terms"],
        "visible_mode": active_overlay_mode,
        "overlay_mode": dict(overlay_mode_payload),
        "overlay_mode_name": active_overlay_mode,
        "tracking_summary": _mapping(session.get("tracking_summary")),
        "latest_signal": _mapping(session.get("latest_signal")),
        "broker_source": broker_source,
        "broker_surface": broker_surface_payload,
        "manual_focus_region": _mapping(session.get("manual_focus_region")),
        "focus_selector": _mapping(session.get("focus_selector")),
        "session": _compact_live_poll_session_payload(session),
        "broker_surface_frame": {
            "artifact": surface_frame,
            "frame_id": _int(session.get("display_frame_id") or session.get("frame_index")),
            "capture_epoch": _float(session.get("display_capture_epoch") or session.get("last_capture_started_epoch")),
            "published_epoch": _float(session.get("display_published_epoch") or session.get("last_capture_epoch")),
            "stream": "display",
            "capture_plane": _mapping(_mapping(session.get("broker_surface")).get("capture_plane")),
            "locked_title": _text(session.get("locked_title") or _mapping(session.get("descriptor")).get("title")),
            "window_query": _text(session.get("window_query"), "Pocket Option"),
        },
        "surface": {
            "selected_plane": "full_broker_surface",
            "frame": surface_frame,
            "overlay_frame": artifact_refs["full-overlay"] if artifact_refs["full-overlay"]["exists"] else artifact_refs["overlay"],
            "mode": "full_broker_surface",
        },
        "chart": {
            "frame": chart_frame,
            "plot_area": plot,
            "chart_transform": chart_transform,
            "scene_graph": scene_graph,
        },
        "chart_frame": {
            "artifact": chart_frame,
            "url": chart_frame["url"],
            "image_url": chart_frame["url"],
            "frame_url": chart_frame["url"],
            "overlay_url": artifact_refs["overlay"]["url"],
            "display_artifact": artifact_refs["full-overlay"] if artifact_refs["full-overlay"]["exists"] else chart_frame,
        },
        "chart_transform": chart_transform,
        "plot_area": plot,
        "scene_graph": scene_graph,
        "broker_scene_graph_v3": scene_graph,
        "overlay_layer_manager_v3": layer_manager.as_dict(),
        "overlay_precision_audit": precision_audit,
        "overlay_layout": overlay_layout,
        "overlay_vocabulary": overlay_vocabulary,
        "overlay_ledger_v3": overlay_ledger,
        "prediction_overlay": prediction_overlay,
        "two_candle_study": two_candle_study,
        "scene_forecast_contribution": scene_forecast_contribution,
        "lstm_contribution": lstm_contribution,
        "visual_plane": visual_plane,
        "frame_timing_trace_v3": frame_timing,
        "frame_timing": frame_timing,
        "overlay_state_version": frame_timing["overlay_state_version"],
        "overlay_frame_state_version": frame_timing.get("overlay_frame_state_version", ""),
        "model_state_version": frame_timing["model_state_version"],
        "frame_age_ms": frame_timing["frame_age_ms"],
        "overlay_age_ms": frame_timing["overlay_age_ms"],
        "model_vote_age_ms": frame_timing["model_vote_age_ms"],
        "packet_age_ms": frame_timing["packet_age_ms"],
        "frontend_render_age_ms": frame_timing["frontend_render_age_ms"],
        "stale_status": frame_timing["stale_status"],
        "stale_flags": frame_timing["stale_flags"],
        "artifacts": artifact_refs,
        "overlays": overlays_payload,
        "overlay_objects": overlays,
        "market_objects": {
            "active_count": len(active_objects or registry_entries or registry.objects),
            "registry_count": len(registry_entries or registry.objects),
            "objects": [obj.as_dict() for obj in registry.objects],
            "source_status": dict(registry.source_status),
        },
        "market_object_registry": registry.as_dict(),
        "sequence_context": sequence_context,
        "sequence_context_v3": sequence_context,
        "signal_thesis_v3": signal_thesis,
        "two_candle_study_v3": two_candle_study,
        "scene_forecast_contribution_v3": scene_forecast_contribution,
        "lstm_candle_sequence_contribution_v3": lstm_contribution,
        "model_council": _model_council_summary(session, study_packet or session.get("model_council_study_packet")),
        "packets": {
            "study": _study_packet_summary(study_packet or _mapping(session.get("model_council_study_packet")), now_epoch=now_value),
            "execution": _study_packet_summary(execution_packet_payload, now_epoch=now_value),
        },
        "study_packet_status": _study_packet_summary(study_packet or _mapping(session.get("model_council_study_packet")), now_epoch=now_value),
        "execution_packet_status": _study_packet_summary(execution_packet_payload, now_epoch=now_value),
        "model_health": model_health_payload,
        "model_warm_state_v3": model_warm_state,
        "instrument": instrument_payload,
        "instrument_context": instrument_payload,
        "visual_health": {
            **visual_health,
            "full_broker_surface_visible": bool(surface_frame["exists"]),
            "overlay_contract_ok": bool(_mapping(visual_health.get("overlay")).get("contract_ok", False)),
        },
        "provider_status": dict(_mapping(session.get("live_state_provider_status"))),
        "shooter": _shooter_summary(session_id, shooter_state),
        "shooter_state": dict(_mapping(shooter_state)),
        "frontend_heartbeat": dict(_mapping(frontend_heartbeat)),
    }
    live_visual_state["performance_trace_v3"] = build_performance_trace_v3(live_visual_state, now_epoch=now_value)
    live_visual_state["vlm_context_skeleton_v3"] = build_vlm_context_skeleton_v3(live_visual_state)
    market_objects = _mapping(live_visual_state.get("market_objects"))
    compact_scene_forecast = _compact_scene_forecast_contribution(
        scene_forecast_contribution
    )
    compact_lstm_contribution = project_public_lstm_contribution_v3(
        lstm_contribution
    )
    compact_live_visual_state: dict[str, Any] = {
        "schema_version": live_visual_state["schema_version"],
        "session_id": live_visual_state["session_id"],
        "frame_id": live_visual_state["frame_id"],
        "state_version": live_visual_state["state_version"],
        "chart_transform_id": live_visual_state["chart_transform_id"],
        "broker_source_lock_id": live_visual_state["broker_source_lock_id"],
        "symbol": live_visual_state["symbol"],
        "timeframe": live_visual_state["timeframe"],
        "requested_mode": live_visual_state["requested_mode"],
        "active_mode": live_visual_state["active_mode"],
        "visible_layers": live_visual_state["visible_layers"],
        "overlay_count": live_visual_state["overlay_count"],
        "renderable_count": live_visual_state["renderable_count"],
        "reason_if_empty": live_visual_state["reason_if_empty"],
        "overlay_mode": live_visual_state["overlay_mode"],
        "broker_source": live_visual_state["broker_source"],
        "broker_surface": live_visual_state["broker_surface"],
        "manual_focus_region": live_visual_state["manual_focus_region"],
        "focus_selector": live_visual_state["focus_selector"],
        "session": live_visual_state["session"],
        "surface": live_visual_state["surface"],
        "chart_frame": live_visual_state["chart_frame"],
        "plot_area": live_visual_state["plot_area"],
        "scene_graph": live_visual_state["scene_graph"],
        "overlay_layer_manager_v3": live_visual_state["overlay_layer_manager_v3"],
        "overlay_precision_audit": live_visual_state["overlay_precision_audit"],
        "overlay_layout": live_visual_state["overlay_layout"],
        "overlay_vocabulary": live_visual_state["overlay_vocabulary"],
        "overlay_ledger_v3": live_visual_state["overlay_ledger_v3"],
        "prediction_overlay": live_visual_state["prediction_overlay"],
        "two_candle_study": live_visual_state["two_candle_study"],
        "scene_forecast_contribution": compact_scene_forecast,
        "lstm_contribution": compact_lstm_contribution,
        "visual_plane": live_visual_state["visual_plane"],
        "frame_timing_trace_v3": live_visual_state["frame_timing_trace_v3"],
        "overlay_state_version": live_visual_state["overlay_state_version"],
        "overlay_frame_state_version": live_visual_state["overlay_frame_state_version"],
        "performance_trace_v3": _compact_performance_trace_v3(_mapping(live_visual_state.get("performance_trace_v3"))),
        "vlm_context_skeleton_v3": live_visual_state["vlm_context_skeleton_v3"],
        "overlays": live_visual_state["overlays"],
        "market_objects": {
            "active_count": market_objects.get("active_count"),
            "registry_count": market_objects.get("registry_count"),
            "source_status": market_objects.get("source_status"),
        },
        "model_council": live_visual_state["model_council"],
        "signal_thesis_v3": live_visual_state["signal_thesis_v3"],
        "packets": live_visual_state["packets"],
        "visual_health": live_visual_state["visual_health"],
        "provider_status": live_visual_state["provider_status"],
        "shooter": live_visual_state["shooter"],
    }
    if compact_public:
        public_session = _compact_live_poll_session_payload(session)
        public_session.update(compact_live_visual_state)
        public_session["live_visual_state"] = compact_live_visual_state
        public_session.setdefault("tracking_summary", _mapping(public_session.get("tracking_summary")))
        public_session.setdefault("latest_signal", _mapping(public_session.get("latest_signal")))
    else:
        public_session = _compact_session_payload(session)
        public_session.update(live_visual_state)
        public_session["tracking_summary"] = _compact_tracking_summary(_mapping(live_visual_state.get("tracking_summary")))
        public_session["latest_signal"] = _compact_latest_signal(_mapping(live_visual_state.get("latest_signal")))
        public_session["model_council_result"] = _compact_model_council_result(_mapping(session.get("model_council_result")))
        public_session["live_visual_state"] = compact_live_visual_state
        public_session.setdefault("tracking_summary", _compact_tracking_summary(_mapping(session.get("tracking_summary"))))
        public_session.setdefault("latest_signal", _compact_latest_signal(_mapping(session.get("latest_signal"))))
    return public_session


def build_live_state_v3_from_tracker_service(
    tracker_service: Any,
    session_id: str,
    *,
    model_health_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    shooter_state_loader: Callable[[str], Mapping[str, Any]] | None = None,
    active_object_loader: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
    registry_loader: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
    frontend_heartbeat_loader: Callable[[str], Mapping[str, Any] | None] | None = None,
    now_epoch: float | None = None,
    overlay_mode: str = "CLEAN_LIVE",
    compact_public: bool = False,
) -> dict[str, Any]:
    degraded_sources: list[dict[str, str]] = []

    def mark_degraded(source: str, exc: Exception) -> None:
        degraded_sources.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})

    snapshot_getter = getattr(tracker_service, "get_session_snapshot", None)
    if callable(snapshot_getter):
        session = _mapping(snapshot_getter(session_id))
    else:
        session = _mapping(tracker_service.get_session(session_id))
    artifacts: dict[str, Path | str] = {}
    session_artifact_keys = {
        "window": "last_window_path",
        "chart": "last_chart_path",
        "overlay": "last_overlay_path",
        "full-overlay": "last_full_overlay_path",
        "projection": "last_projection_path",
        "memory-reference": "last_memory_reference_path",
    }
    for kind, key in session_artifact_keys.items():
        direct_path = _text(session.get(key))
        if direct_path:
            artifacts[kind] = direct_path
            continue
        try:
            artifacts[kind] = tracker_service.latest_artifact_path(session_id, kind)
        except Exception as exc:
            if kind in {"window", "chart", "overlay", "full-overlay"}:
                mark_degraded(f"artifact:{kind}", exc)
            pass
    try:
        study_packet = _mapping(tracker_service.latest_model_council_study_packet(session_id))
    except Exception as exc:
        mark_degraded("model_council_study_packet", exc)
        study_packet = _mapping(session.get("model_council_study_packet"))
    try:
        execution_packet = _mapping(tracker_service.latest_model_council_packet(session_id))
    except Exception as exc:
        mark_degraded("model_council_execution_packet", exc)
        fallback_execution_packet = _mapping(session.get("model_council_packet") or session.get("execution_packet"))
        execution_packet = _mapping(_current_execution_packet(
            fallback_execution_packet,
            now_epoch=float(now_epoch if now_epoch is not None else time.time()),
        ))
    model_health = _mapping(model_health_builder(session)) if model_health_builder else {}
    try:
        shooter_state = _mapping(shooter_state_loader(session_id)) if shooter_state_loader else {}
    except Exception as exc:
        mark_degraded("shooter_state", exc)
        shooter_state = {}
    try:
        active_objects = list(active_object_loader(session_id)) if active_object_loader else []
    except Exception as exc:
        mark_degraded("active_objects", exc)
        active_objects = []
    try:
        registry_entries = list(registry_loader(session_id)) if registry_loader else []
    except Exception as exc:
        mark_degraded("registry_entries", exc)
        registry_entries = []
    try:
        frontend_heartbeat = _mapping(frontend_heartbeat_loader(session_id)) if frontend_heartbeat_loader else {}
    except Exception as exc:
        mark_degraded("frontend_heartbeat", exc)
        frontend_heartbeat = {}
    session["live_state_provider_status"] = {
        "ok": not degraded_sources,
        "degraded": bool(degraded_sources),
        "degraded_sources": degraded_sources,
    }
    return build_live_state_v3(
        session,
        artifacts=artifacts,
        active_objects=active_objects,
        registry_entries=registry_entries,
        study_packet=study_packet,
        execution_packet=execution_packet,
        model_health=model_health,
        shooter_state=shooter_state,
        frontend_heartbeat=frontend_heartbeat,
        now_epoch=now_epoch,
        overlay_mode=overlay_mode,
        compact_public=compact_public,
    )


__all__ = [
    "LIVE_STATE_SCHEMA_VERSION",
    "build_live_state_v3",
    "build_live_state_v3_from_tracker_service",
    "compact_session_payload",
]


compact_session_payload = _compact_session_payload
