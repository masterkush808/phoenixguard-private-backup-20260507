from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping, Sequence, cast

from phoenixguard.vision.overlay_geometry import normalize_bbox

try:
    from phoenixguard.vision.v3_overlay_contract import (
        TYPE_LAYER_MAP as _contract_type_layer_map,
        TYPE_ROLE_MAP as _contract_type_role_map,
        V3_OVERLAY_SCHEMA_VERSION as _contract_overlay_schema_version,
        normalize_bounds,
        normalize_overlay_type,
        normalize_v3_overlay_object,
        stable_overlay_id,
        validate_overlay_payload,
    )
except Exception:
    _contract_overlay_schema_version = "PG_V3_OVERLAY_OBJECT"
    _contract_type_layer_map = {
        "CURRENT_CANDLE": "recent_candles",
        "IMPULSE_BOX": "major_swings",
        "PULLBACK_BOX": "local_swings",
        "RETEST_BOX": "trigger_zones",
        "CONTINUATION_BOX": "trigger_zones",
        "SNIPER_ENTRY_BOX": "trigger_zones",
        "TARGET_ZONE_BOX": "trigger_zones",
        "INVALIDATION_BOX": "trigger_zones",
        "SUPPLY_ZONE": "supply_demand",
        "DEMAND_ZONE": "supply_demand",
        "OPPOSING_FORCE": "supply_demand",
        "ANGLE_VECTOR": "active_council_decision",
        "PREDICTION_PATH": "active_council_decision",
        "PROGRESSION_PATH": "historical_replay",
        "BROKER_CONTROL": "broker_controls",
    }
    _contract_type_role_map = {
        "CURRENT_CANDLE": "current_candle",
        "SNIPER_ENTRY_BOX": "sniper",
        "RETEST_BOX": "trigger",
        "CONTINUATION_BOX": "continuation",
        "TARGET_ZONE_BOX": "target",
        "INVALIDATION_BOX": "invalidation",
        "SUPPLY_ZONE": "supply",
        "DEMAND_ZONE": "demand",
        "OPPOSING_FORCE": "opposing_force",
        "ANGLE_VECTOR": "angle",
        "PREDICTION_PATH": "prediction",
    }

    def normalize_bounds(value: Any) -> list[float] | None:
        bbox = normalize_bbox(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else None
        if bbox is not None:
            return [float(item) for item in bbox]
        points = []
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) and len(item) >= 2:
                    points.append((_float(item[0]), _float(item[1])))
        if not points:
            return None
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        pad = 3.0 if max(xs) <= min(xs) or max(ys) <= min(ys) else 0.0
        return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]

    def stable_overlay_id(*parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return f"v3ov_{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:16]}"

    def normalize_overlay_type(raw: Any, *, layer: Any = "", role: Any = "", side: Any = "") -> str:
        value = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
        if value in _contract_type_layer_map:
            return value
        role_value = str(role or "").strip().lower()
        if role_value in {"sniper", "aggressive_sniper"}:
            return "SNIPER_ENTRY_BOX"
        if role_value in {"target"}:
            return "TARGET_ZONE_BOX"
        if role_value in {"invalidation", "cancel"}:
            return "INVALIDATION_BOX"
        if role_value in {"trigger", "retest", "primary"}:
            return "RETEST_BOX"
        if role_value in {"pullback", "reclaim"}:
            return "PULLBACK_BOX"
        if role_value in {"support", "demand"}:
            return "DEMAND_ZONE"
        if role_value in {"resistance", "supply"}:
            return "SUPPLY_ZONE"
        layer_value = str(layer or "").strip().lower()
        if layer_value == "recent_candles":
            return "CURRENT_CANDLE"
        if layer_value == "major_swings":
            return "IMPULSE_BOX"
        if layer_value == "local_swings":
            return "PULLBACK_BOX"
        if layer_value == "supply_demand":
            return "SUPPLY_ZONE" if str(side or "").upper() == "SELL" else "DEMAND_ZONE"
        if layer_value == "historical_replay":
            return "PROGRESSION_PATH"
        if layer_value == "broker_controls":
            return "BROKER_CONTROL"
        return "CONTINUATION_BOX"

    def normalize_v3_overlay_object(
        raw: Mapping[str, Any],
        *,
        strict: bool = False,
        frame_id: int | str | None = None,
        sequence_id: str = "",
        chart_transform_id: str = "",
        source_agent: str = "market_object_tracker_v3",
        **_: Any,
    ) -> dict[str, Any]:
        bounds = normalize_bounds(raw.get("bounds", raw.get("bbox", raw.get("box", raw.get("rect")))))
        if bounds is None:
            if strict:
                raise ValueError("invalid overlay bounds")
            return {}
        overlay_type = normalize_overlay_type(raw.get("type"), layer=raw.get("layer"), role=raw.get("role"), side=raw.get("side"))
        confidence = _clip01(raw.get("confidence", raw.get("truth_score", 0.0)))
        truth_score = _clip01(raw.get("truth_score", confidence))
        side = _upper_side(raw.get("side", raw.get("direction", "HOLD")))
        label = _text(raw.get("label") or raw.get("key") or overlay_type.replace("_", " "))
        return {
            "schema_version": _contract_overlay_schema_version,
            "overlay_id": _text(raw.get("overlay_id") or raw.get("id") or raw.get("key") or stable_overlay_id(sequence_id, frame_id, overlay_type, label)),
            "object_id": _text(raw.get("object_id") or raw.get("overlay_id") or stable_overlay_id(sequence_id, overlay_type, label)),
            "track_id": _text(raw.get("track_id") or raw.get("object_id") or stable_overlay_id(sequence_id, overlay_type, label)),
            "type": overlay_type,
            "side": side,
            "source_agent": _text(raw.get("source_agent") or source_agent),
            "frame_id": frame_id if frame_id is not None else raw.get("frame_id", 0),
            "sequence_id": _text(sequence_id or raw.get("sequence_id") or "sequence_pending"),
            "chart_transform_id": _text(chart_transform_id or raw.get("chart_transform_id")),
            "coordinate_mode": _text(raw.get("coordinate_mode") or "CHART_IMAGE_SPACE"),
            "anchor_type": _text(raw.get("anchor_type") or "BOX"),
            "bounds": [round(float(value), 4) for value in bounds],
            "bbox": [round(float(value), 4) for value in bounds],
            "truth_score": truth_score,
            "confidence": confidence,
            "lifecycle_state": _text(raw.get("lifecycle_state") or "ACTIVE").upper(),
            "visible_modes": list(raw.get("visible_modes") or ["CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "PREDICTION", "DEBUG", "INSPECTOR"]),
            "ttl_ms": int(_float(raw.get("ttl_ms"), 30000.0)),
            "reason": _text(raw.get("reason") or raw.get("message") or f"{overlay_type} from tracked market object"),
            "label": label,
            "layer": _text(raw.get("layer") or _contract_type_layer_map.get(overlay_type, "diagnostics")),
            "role": _text(raw.get("role") or _contract_type_role_map.get(overlay_type, "")),
            "visible_default": bool(raw.get("visible_default", True)),
        }

    def validate_overlay_payload(overlays: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        rows = list(overlays)
        required = {"overlay_id", "object_id", "track_id", "type", "bounds", "confidence", "truth_score"}
        errors = [
            {"object_id": str(row.get("object_id") or ""), "errors": sorted(required - set(row.keys()))}
            for row in rows
            if required - set(row.keys())
        ]
        return {"schema_version": "PG_V3_OVERLAY_CONTRACT_AUDIT", "ok": not errors, "count": len(rows), "errors": errors, "fallback_contract": True}


V3_OVERLAY_SCHEMA_VERSION = _contract_overlay_schema_version
TYPE_LAYER_MAP = _contract_type_layer_map
TYPE_ROLE_MAP = _contract_type_role_map
TRACKER_SCHEMA_VERSION = "PG_MARKET_OBJECT_TRACKER_V3"
OVERLAY_SCHEMA_VERSION = V3_OVERLAY_SCHEMA_VERSION
SEQUENCE_CONTEXT_SCHEMA_VERSION = "PG_SEQUENCE_CONTEXT_V3"
MARKET_OBJECT_REGISTRY_SCHEMA_VERSION = TRACKER_SCHEMA_VERSION


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, Any], item)) for item in value if isinstance(item, Mapping)]


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


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


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _float(value, 0.0)))


def _upper_side(value: Any, default: str = "HOLD") -> str:
    side = str(value or default).strip().upper()
    return side if side in {"BUY", "SELL", "HOLD"} else default


def _frame_id(payload: Mapping[str, Any]) -> int:
    return int(_float(payload.get("frame_index", payload.get("capture_count", 0)), 0.0))


def _session_id(payload: Mapping[str, Any]) -> str:
    return _text(payload.get("session_id"), "session")


def _stable_id(session_id: str, object_type: str, source_path: str, source_key: Any = "") -> str:
    digest = hashlib.sha1(f"{session_id}|{object_type}|{source_path}|{source_key}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"mobj_{digest}"


def _sequence_id(session_id: str, frame_id: int, tracking: Mapping[str, Any], signal: Mapping[str, Any]) -> str:
    digest = hashlib.sha1(
        "|".join(
            str(value or "")
            for value in (
                session_id,
                tracking.get("global_direction"),
                tracking.get("local_direction"),
                tracking.get("impulse_direction"),
                signal.get("entry_state"),
                signal.get("setup"),
            )
        ).encode("utf-8", errors="ignore")
    ).hexdigest()[:14]
    return f"seq_{digest}"


def _chart_transform_id(payload: Mapping[str, Any], tracking: Mapping[str, Any]) -> str:
    for source in (tracking.get("chart_transform"), tracking.get("overlay_geometry"), payload.get("chart_transform")):
        item = _mapping(source)
        text = _text(item.get("chart_transform_id") or item.get("id"))
        if text:
            return text
    return f"ct_{_session_id(payload)}_{_frame_id(payload)}"


def _raw_bbox(raw: Mapping[str, Any]) -> list[float] | None:
    for key in ("bounds", "bbox", "pixel_bbox", "box", "rect"):
        bbox = normalize_bounds(raw.get(key))
        if bbox is not None:
            return bbox
    for key in ("target_bbox", "sniper_window", "trigger_window", "target_window"):
        bbox = normalize_bounds(raw.get(key))
        if bbox is not None:
            return bbox
    for key in ("points", "anchors", "path"):
        bbox = normalize_bounds(raw.get(key))
        if bbox is not None:
            return bbox
    return None


@dataclass(frozen=True)
class MarketObjectV3:
    object_id: str
    object_type: str
    source_path: str
    side: str
    bounds: tuple[float, float, float, float]
    confidence: float
    truth_score: float
    first_seen_frame: int
    last_seen_frame: int
    track_id: str
    label: str
    reason: str
    state: str = "ACTIVE"
    anchor_type: str = "BOX"
    anchor_candles: tuple[int, ...] = ()
    tight_bounds: tuple[float, float, float, float] | None = None
    expanded_bounds: tuple[float, float, float, float] | None = None

    def as_dict(self) -> dict[str, Any]:
        tight_bounds = self.tight_bounds or self.bounds
        expanded_bounds = self.expanded_bounds or self.bounds
        return {
            "object_id": self.object_id,
            "type": self.object_type,
            "object_type": self.object_type,
            "source_path": self.source_path,
            "side": self.side,
            "state": self.state,
            "lifecycle_state": self.state,
            "anchor_type": self.anchor_type,
            "anchor_candles": list(self.anchor_candles),
            "bounds": list(self.bounds),
            "bbox": list(self.bounds),
            "tight_bounds": list(tight_bounds),
            "expanded_bounds": list(expanded_bounds),
            "confidence": self.confidence,
            "truth_score": self.truth_score,
            "first_seen_frame": self.first_seen_frame,
            "last_seen_frame": self.last_seen_frame,
            "track_id": self.track_id,
            "label": self.label,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SequenceContextV3:
    sequence_id: str
    frame_start: int
    frame_end: int
    sequence_length: int
    frames_received: int
    frames_used: int
    sequence_signature: str
    confidence: float
    directions: Mapping[str, str]
    phase: str
    tracked_objects: tuple[str, ...]
    memory_matches: tuple[Mapping[str, Any], ...]
    status: str
    placeholder: bool = True
    impulse_tracks: tuple[str, ...] = ()
    pullback_tracks: tuple[str, ...] = ()
    retest_tracks: tuple[str, ...] = ()
    continuation_tracks: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()
    angle_vectors: tuple[str, ...] = ()
    sniper_entries: tuple[str, ...] = ()
    target_zones: tuple[str, ...] = ()
    invalidation_zones: tuple[str, ...] = ()
    prediction_paths: tuple[str, ...] = ()
    source_status: Mapping[str, str] = field(default_factory=dict)
    missing_sources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEQUENCE_CONTEXT_SCHEMA_VERSION,
            "sequence_id": self.sequence_id,
            "frame_start": self.frame_start,
            "frame_end": self.frame_end,
            "sequence_length": self.sequence_length,
            "frames_received": self.frames_received,
            "frames_used": self.frames_used,
            "sequence_signature": self.sequence_signature,
            "confidence": self.confidence,
            "directions": dict(self.directions),
            "phase": self.phase,
            "tracked_objects": list(self.tracked_objects),
            "impulse_tracks": list(self.impulse_tracks),
            "pullback_tracks": list(self.pullback_tracks),
            "retest_tracks": list(self.retest_tracks),
            "continuation_tracks": list(self.continuation_tracks),
            "zones": list(self.zones),
            "angle_vectors": list(self.angle_vectors),
            "sniper_entries": list(self.sniper_entries),
            "target_zones": list(self.target_zones),
            "invalidation_zones": list(self.invalidation_zones),
            "prediction_paths": list(self.prediction_paths),
            "memory_matches": [dict(item) for item in self.memory_matches],
            "source_status": dict(self.source_status),
            "missing_sources": list(self.missing_sources),
            "status": self.status,
            "sequence_status": self.status,
            "placeholder": self.placeholder,
        }


@dataclass(frozen=True)
class MarketObjectRegistryV3:
    session_id: str
    frame_id: int
    status: str
    degraded: bool
    missing_sources: tuple[str, ...]
    source_status: Mapping[str, str]
    objects: tuple[MarketObjectV3, ...]
    overlays: tuple[dict[str, Any], ...]
    sequence_context: SequenceContextV3

    def counts_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for obj in self.objects:
            counts[obj.object_type] = counts.get(obj.object_type, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        objects = [obj.as_dict() for obj in self.objects]
        overlays = [dict(overlay) for overlay in self.overlays]
        return {
            "schema_version": TRACKER_SCHEMA_VERSION,
            "registry_schema_version": MARKET_OBJECT_REGISTRY_SCHEMA_VERSION,
            "session_id": self.session_id,
            "frame_id": self.frame_id,
            "status": self.status,
            "degraded": self.degraded,
            "missing_sources": list(self.missing_sources),
            "invalid_sources": [],
            "source_status": dict(self.source_status),
            "counts_by_type": self.counts_by_type(),
            "object_count": len(self.objects),
            "overlay_count": len(self.overlays),
            "objects": objects,
            "object_registry": objects,
            "tracked_objects": objects,
            "overlays": overlays,
            "overlay_objects": overlays,
            "sequence_context": self.sequence_context.as_dict(),
            "overlay_contract": validate_overlay_payload(self.overlays),
        }


class _RegistryBuilder:
    def __init__(self, first_seen_by_id: Mapping[str, int] | None = None) -> None:
        self.first_seen_by_id = dict(first_seen_by_id or {})

    def build(self, payload: Mapping[str, Any]) -> MarketObjectRegistryV3:
        session_id = _session_id(payload)
        frame_id = _frame_id(payload)
        tracking = _mapping(payload.get("tracking_summary"))
        signal = _mapping(payload.get("latest_signal"))
        candles = _sequence_of_mappings(tracking.get("tracked_candles"))
        missing: list[str] = []
        if not tracking:
            missing.append("tracking_summary")
        if not candles:
            missing.append("tracking_summary.tracked_candles")
        source_status = {
            "tracking_summary": "READY" if tracking else "MISSING",
            "tracking_summary.tracked_candles": "READY" if candles else "MISSING",
            "tracking_summary.structure_boxes": "READY" if _sequence_of_mappings(tracking.get("structure_boxes")) else "MISSING",
            "tracking_summary.projection": "READY" if _mapping(tracking.get("projection")) else "MISSING",
            "memory_projection": "READY" if _mapping(payload.get("memory_projection_predict") or payload.get("memory_projection_current")) else "MISSING",
        }
        if missing:
            sequence = self._sequence_context(
                payload,
                tracking,
                signal,
                (),
                status="MISSING_CRITICAL_SOURCE",
                missing_sources=tuple(missing),
                source_status=source_status,
            )
            return MarketObjectRegistryV3(
                session_id=session_id,
                frame_id=frame_id,
                status="MISSING_CRITICAL_SOURCE",
                degraded=True,
                missing_sources=tuple(missing),
                source_status=source_status,
                objects=(),
                overlays=(),
                sequence_context=sequence,
            )

        objects: list[MarketObjectV3] = []
        overlays: list[dict[str, Any]] = []
        chart_transform_id = _chart_transform_id(payload, tracking)
        sequence_id = _sequence_id(session_id, frame_id, tracking, signal)

        def add_object(
            raw: Mapping[str, Any],
            *,
            object_type: str,
            source_path: str,
            source_key: Any = "",
            label: str = "",
            role: str = "",
            layer: str = "",
            side: Any = None,
            reason: str = "",
            lifecycle_state: str = "ACTIVE",
        ) -> None:
            bbox = _raw_bbox(raw)
            if bbox is None or normalize_bbox(bbox) is None:
                return
            side_value = _upper_side(side if side is not None else raw.get("side", raw.get("direction", signal.get("action"))))
            object_id = _stable_id(session_id, object_type, source_path, source_key)
            track_id = _text(raw.get("track_id") or raw.get("persistent_id"), object_id)
            first_seen = int(self.first_seen_by_id.get(object_id, frame_id))
            self.first_seen_by_id.setdefault(object_id, first_seen)
            confidence = _clip01(raw.get("confidence", raw.get("truth_score", signal.get("effective_confidence", signal.get("confidence", 0.0)))))
            truth = _clip01(raw.get("truth_score", confidence))
            label_value = _text(label or raw.get("label") or raw.get("key") or object_type.replace("_", " "))
            reason_value = _text(reason or raw.get("reason") or raw.get("story") or f"{object_type} from {source_path}")
            obj = MarketObjectV3(
                object_id=object_id,
                object_type=object_type,
                source_path=source_path,
                side=side_value,
                bounds=tuple(float(value) for value in bbox[:4]),  # type: ignore[arg-type]
                confidence=confidence,
                truth_score=truth,
                first_seen_frame=first_seen,
                last_seen_frame=frame_id,
                track_id=track_id,
                label=label_value,
                reason=reason_value,
                state=lifecycle_state,
                anchor_type=_text(raw.get("anchor_type"), "BOX").upper(),
                anchor_candles=tuple(int(_float(item, 0.0)) for item in _sequence(raw.get("anchor_candles") or raw.get("source_indices"))),
            )
            objects.append(obj)
            overlay_raw = dict(raw)
            overlay_raw.update(
                {
                    "overlay_id": stable_overlay_id(session_id, frame_id, object_id),
                    "object_id": object_id,
                    "track_id": track_id,
                    "type": object_type,
                    "side": side_value,
                    "source_agent": "market_object_tracker_v3",
                    "frame_id": frame_id,
                    "sequence_id": sequence_id,
                    "chart_transform_id": chart_transform_id,
                    "coordinate_mode": overlay_raw.get("coordinate_mode", "CHART_IMAGE_SPACE"),
                    "anchor_type": _text(raw.get("anchor_type"), "BOX").upper(),
                    "anchor_candles": list(obj.anchor_candles),
                    "bounds": bbox,
                    "truth_score": truth,
                    "confidence": confidence,
                    "lifecycle_state": lifecycle_state,
                    "reason": reason_value,
                    "label": label_value,
                    "layer": layer or TYPE_LAYER_MAP.get(object_type, "diagnostics"),
                    "role": role or TYPE_ROLE_MAP.get(object_type, ""),
                    "visible_default": object_type not in {"DEBUG_RAW_DETECTION", "PROGRESSION_PATH"},
                }
            )
            overlays.append(
                normalize_v3_overlay_object(
                    overlay_raw,
                    strict=False,
                    frame_id=frame_id,
                    sequence_id=sequence_id,
                    chart_transform_id=chart_transform_id,
                    source_agent="market_object_tracker_v3",
                )
            )

        latest_index = len(candles) - 1
        latest_candle = candles[latest_index]
        add_object(
            latest_candle,
            object_type="CURRENT_CANDLE",
            source_path=f"tracking_summary.tracked_candles[{latest_index}]",
            source_key=latest_candle.get("track_id", latest_index),
            label="CURRENT CANDLE",
            role="current_candle",
            layer="recent_candles",
            side=latest_candle.get("direction"),
        )

        for index, box in enumerate(_sequence_of_mappings(tracking.get("structure_boxes"))):
            key = str(box.get("key") or box.get("role") or "").lower()
            label_lower = str(box.get("label") or "").lower()
            if "global" in key or index == 0:
                object_type = "IMPULSE_BOX"
            elif "local" in key or "pullback" in label_lower:
                object_type = "PULLBACK_BOX"
            elif "current" in key or "continuation" in label_lower:
                object_type = "CONTINUATION_BOX"
            else:
                object_type = "CONTINUATION_BOX"
            add_object(
                box,
                object_type=object_type,
                source_path=f"tracking_summary.structure_boxes[{index}]",
                source_key=box.get("key", index),
                label=_text(box.get("label"), object_type.replace("_", " ")),
                role=str(box.get("role") or box.get("key") or ""),
                layer=TYPE_LAYER_MAP[object_type],
                side=box.get("direction"),
            )

        for index, box in enumerate(_sequence_of_mappings(tracking.get("historical_structure"))):
            label = _text(box.get("label"), f"history {index + 1}")
            lower = label.lower()
            object_type = "PULLBACK_BOX" if "pullback" in lower else "PROGRESSION_PATH"
            add_object(
                {**box, "visible_modes": ["FULL_HISTORY_READ", "REPLAY", "INSPECTOR"]},
                object_type=object_type,
                source_path=f"tracking_summary.historical_structure[{index}]",
                source_key=box.get("key", index),
                label=label,
                role="history",
                layer="historical_replay" if object_type == "PROGRESSION_PATH" else "local_swings",
                side=box.get("direction"),
                lifecycle_state="HISTORICAL",
            )

        for index, zone in enumerate(_sequence_of_mappings(tracking.get("support_resistance_zones"))):
            role = str(zone.get("role") or "").lower()
            object_type = "DEMAND_ZONE" if role == "support" else "SUPPLY_ZONE" if role == "resistance" else normalize_overlay_type(zone.get("type"), layer="supply_demand", role=role, side=zone.get("direction"))
            add_object(
                zone,
                object_type=object_type,
                source_path=f"tracking_summary.support_resistance_zones[{index}]",
                source_key=zone.get("key", index),
                label=_text(zone.get("label"), object_type.replace("_", " ")),
                role=role or TYPE_ROLE_MAP.get(object_type, ""),
                layer="supply_demand",
                side=zone.get("direction"),
            )

        projection = _mapping(tracking.get("projection"))
        for index, zone in enumerate(_sequence_of_mappings(projection.get("zones"))):
            kind = str(zone.get("kind") or zone.get("role") or "").lower()
            if "sniper" in kind:
                object_type = "SNIPER_ENTRY_BOX"
            elif "trigger" in kind or "primary" in kind or index > 0:
                object_type = "RETEST_BOX"
            else:
                object_type = "CONTINUATION_BOX"
            add_object(
                zone,
                object_type=object_type,
                source_path=f"tracking_summary.projection.zones[{index}]",
                source_key=zone.get("key", kind or index),
                label=_text(zone.get("label"), object_type.replace("_", " ")),
                role=kind or TYPE_ROLE_MAP.get(object_type, ""),
                layer="trigger_zones",
                side=zone.get("direction", projection.get("direction")),
                lifecycle_state="PREDICTED",
            )
            if normalize_bounds(zone.get("target_bbox")) is not None:
                add_object(
                    {**zone, "bbox": zone.get("target_bbox"), "label": f"{_upper_side(zone.get('direction', projection.get('direction')))} TARGET"},
                    object_type="TARGET_ZONE_BOX",
                    source_path=f"tracking_summary.projection.zones[{index}].target_bbox",
                    source_key=zone.get("key", kind or index),
                    label=f"{_upper_side(zone.get('direction', projection.get('direction')))} TARGET",
                    role="target",
                    layer="trigger_zones",
                    side=zone.get("direction", projection.get("direction")),
                    lifecycle_state="PREDICTED",
                )
            if zone.get("invalidation_y") is not None and _raw_bbox(zone) is not None:
                bbox = _raw_bbox(zone) or [0, 0, 1, 1]
                y = _float(zone.get("invalidation_y"), bbox[3])
                add_object(
                    {**zone, "bbox": [bbox[0], y - 2.0, bbox[2], y + 2.0], "label": f"{_upper_side(zone.get('direction', projection.get('direction')))} INVALIDATION"},
                    object_type="INVALIDATION_BOX",
                    source_path=f"tracking_summary.projection.zones[{index}].invalidation_y",
                    source_key=zone.get("key", kind or index),
                    label=f"{_upper_side(zone.get('direction', projection.get('direction')))} INVALIDATION",
                    role="invalidation",
                    layer="trigger_zones",
                    side=zone.get("direction", projection.get("direction")),
                    lifecycle_state="PREDICTED",
                )
            if normalize_bounds(zone.get("path")) is not None:
                add_object(
                    {**zone, "bounds": zone.get("path"), "label": f"{_upper_side(zone.get('direction', projection.get('direction')))} PREDICTION PATH"},
                    object_type="PREDICTION_PATH",
                    source_path=f"tracking_summary.projection.zones[{index}].path",
                    source_key=zone.get("key", kind or index),
                    label=f"{_upper_side(zone.get('direction', projection.get('direction')))} PREDICTION PATH",
                    role="prediction",
                    layer="active_council_decision",
                    side=zone.get("direction", projection.get("direction")),
                    lifecycle_state="PREDICTED",
                )

        for index, vector in enumerate(_sequence_of_mappings(tracking.get("angle_vectors"))):
            add_object(
                vector,
                object_type="ANGLE_VECTOR",
                source_path=f"tracking_summary.angle_vectors[{index}]",
                source_key=vector.get("id", index),
                label=_text(vector.get("label"), "ANGLE VECTOR"),
                role="angle",
                layer="active_council_decision",
                side=vector.get("direction"),
            )

        execution_timing = _mapping(tracking.get("execution_timing"))
        for key, fallback_type in (("entry_area_zone", "DEMAND_ZONE"), ("opposing_force_zone", "OPPOSING_FORCE")):
            zone = _mapping(execution_timing.get(key))
            if zone:
                role_value = str(zone.get("role") or zone.get("label") or key).lower()
                if fallback_type == "OPPOSING_FORCE":
                    object_type = "OPPOSING_FORCE"
                elif "resistance" in role_value or "supply" in role_value:
                    object_type = "SUPPLY_ZONE"
                elif "support" in role_value or "demand" in role_value:
                    object_type = "DEMAND_ZONE"
                else:
                    object_type = normalize_overlay_type(zone.get("type"), layer="supply_demand", role=zone.get("role"), side=zone.get("direction"))
                add_object(
                    zone,
                    object_type=object_type,
                    source_path=f"tracking_summary.execution_timing.{key}",
                    source_key=zone.get("label", key),
                    label=_text(zone.get("label"), object_type.replace("_", " ")),
                    role=key,
                    layer="supply_demand",
                    side=zone.get("direction"),
                )

        memory = _mapping(payload.get("memory_projection_current")) or _mapping(payload.get("memory_projection_predict")) or _mapping(payload.get("memory_projection_future"))
        forward = _mapping(memory.get("forward_projection"))
        projected = _sequence_of_mappings(forward.get("projected_candles"))
        if projected:
            points = []
            for candle in projected:
                bbox = _raw_bbox(candle)
                if bbox:
                    points.append([(bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5])
            if points:
                add_object(
                    {"path": points, "confidence": memory.get("memory_precision_score", memory.get("memory_similarity", 0.0)), "direction": memory.get("dominant_side")},
                    object_type="PREDICTION_PATH",
                    source_path="memory_projection.forward_projection.projected_candles",
                    source_key=_text(memory.get("primary_fit", {})),
                    label=f"{_upper_side(memory.get('dominant_side'))} MEMORY PATH",
                    role="prediction",
                    layer="active_council_decision",
                    side=memory.get("dominant_side"),
                    lifecycle_state="PREDICTED",
                )

        unique_objects: dict[str, MarketObjectV3] = {}
        unique_overlays: dict[str, dict[str, Any]] = {}
        for obj, overlay in zip(objects, overlays):
            unique_objects[obj.object_id] = obj
            unique_overlays[obj.object_id] = overlay
        ordered_objects = tuple(unique_objects.values())
        ordered_overlays = tuple(unique_overlays[obj.object_id] for obj in ordered_objects)
        sequence = self._sequence_context(payload, tracking, signal, ordered_objects, status="READY", missing_sources=(), source_status=source_status)
        return MarketObjectRegistryV3(
            session_id=session_id,
            frame_id=frame_id,
            status="READY",
            degraded=False,
            missing_sources=(),
            source_status=source_status,
            objects=ordered_objects,
            overlays=ordered_overlays,
            sequence_context=sequence,
        )

    def _sequence_context(
        self,
        payload: Mapping[str, Any],
        tracking: Mapping[str, Any],
        signal: Mapping[str, Any],
        objects: Sequence[MarketObjectV3],
        *,
        status: str,
        missing_sources: tuple[str, ...],
        source_status: Mapping[str, str] | None = None,
    ) -> SequenceContextV3:
        session_id = _session_id(payload)
        frame_id = _frame_id(payload)
        candles = _sequence_of_mappings(tracking.get("tracked_candles"))
        visible = int(_float(tracking.get("visible_candle_count", len(candles)), len(candles)))
        memory = _mapping(payload.get("memory_projection_predict") or payload.get("memory_projection_current") or payload.get("memory_projection_future"))
        top_matches = _sequence_of_mappings(_mapping(memory.get("primary_fit")).get("top_matches"))
        memory_matches: list[dict[str, Any]] = []
        for item in top_matches[:3]:
            memory_matches.append(
                {
                    "entry_id": _text(item.get("entry_id") or item.get("image_name") or item.get("label")),
                    "label": _text(item.get("label")),
                    "similarity": _clip01(item.get("similarity", item.get("score", 0.0))),
                    "summary": _text(item.get("summary")),
                }
            )
        signature = hashlib.sha1(
            "|".join(
                [
                    str(frame_id),
                    str([(obj.object_id, obj.last_seen_frame, list(obj.bounds)) for obj in objects]),
                    str(memory_matches),
                    str(missing_sources),
                ]
            ).encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        phase = _text(tracking.get("overlay_kind") or signal.get("setup") or signal.get("setup_type"), "UNKNOWN").upper().replace(" ", "_")
        def ids_for(*types: str) -> tuple[str, ...]:
            wanted = set(types)
            return tuple(obj.object_id for obj in objects if obj.object_type in wanted)

        return SequenceContextV3(
            sequence_id=_sequence_id(session_id, frame_id, tracking, signal),
            frame_start=max(0, frame_id - max(1, visible)),
            frame_end=frame_id,
            sequence_length=visible,
            frames_received=int(_float(payload.get("capture_count", frame_id), frame_id)),
            frames_used=visible,
            sequence_signature=signature,
            confidence=max(_clip01(signal.get("effective_confidence", signal.get("confidence", 0.0))), _clip01(memory.get("memory_precision_score", 0.0))),
            directions={
                "global": _upper_side(tracking.get("global_direction")),
                "local": _upper_side(tracking.get("local_direction")),
                "impulse": _upper_side(tracking.get("impulse_direction")),
                "council": _upper_side(signal.get("action")),
                "execution": _upper_side(signal.get("execution_action")),
            },
            phase=phase,
            tracked_objects=tuple(obj.object_id for obj in objects),
            memory_matches=tuple(memory_matches),
            status=status,
            placeholder=True,
            impulse_tracks=ids_for("IMPULSE_BOX"),
            pullback_tracks=ids_for("PULLBACK_BOX"),
            retest_tracks=ids_for("RETEST_BOX"),
            continuation_tracks=ids_for("CONTINUATION_BOX"),
            zones=ids_for("SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"),
            angle_vectors=ids_for("ANGLE_VECTOR"),
            sniper_entries=ids_for("SNIPER_ENTRY_BOX"),
            target_zones=ids_for("TARGET_ZONE_BOX"),
            invalidation_zones=ids_for("INVALIDATION_BOX"),
            prediction_paths=ids_for("PREDICTION_PATH"),
            source_status=dict(source_status or {}),
            missing_sources=missing_sources,
        )


class MarketObjectTrackerV3:
    def __init__(self) -> None:
        self._first_seen_by_id: dict[str, int] = {}

    def build_registry(self, session_payload: Mapping[str, Any]) -> MarketObjectRegistryV3:
        builder = _RegistryBuilder(self._first_seen_by_id)
        registry = builder.build(session_payload)
        self._first_seen_by_id.update(builder.first_seen_by_id)
        return registry


def build_market_object_registry_v3(session_payload: Mapping[str, Any]) -> MarketObjectRegistryV3:
    return _RegistryBuilder().build(session_payload)


def build_v3_overlays_from_session(session_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(build_market_object_registry_v3(session_payload).overlays)


def build_sequence_context_v3(session_payload: Mapping[str, Any]) -> dict[str, Any]:
    return build_market_object_registry_v3(session_payload).sequence_context.as_dict()


__all__ = [
    "MARKET_OBJECT_REGISTRY_SCHEMA_VERSION",
    "OVERLAY_SCHEMA_VERSION",
    "SEQUENCE_CONTEXT_SCHEMA_VERSION",
    "TRACKER_SCHEMA_VERSION",
    "MarketObjectRegistryV3",
    "MarketObjectTrackerV3",
    "MarketObjectV3",
    "SequenceContextV3",
    "build_market_object_registry_v3",
    "build_sequence_context_v3",
    "build_v3_overlays_from_session",
]
