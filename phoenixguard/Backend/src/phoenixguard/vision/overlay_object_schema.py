from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any, Literal, TypedDict, cast

from phoenixguard.vision.overlay_geometry import (
    DEFAULT_LAYER_VISIBILITY,
    OVERLAY_LAYERS,
    bbox_area,
    normalize_bbox,
)


OverlayLayer = Literal[
    "chart_bounds",
    "recent_candles",
    "major_swings",
    "local_swings",
    "supply_demand",
    "trigger_zones",
    "active_council_decision",
    "historical_replay",
    "broker_controls",
    "diagnostics",
]

OverlayBBox = tuple[float, float, float, float]


class OverlayObject(TypedDict, total=False):
    key: str
    id: str
    label: str
    layer: OverlayLayer
    bbox: list[float]
    direction: str
    role: str
    kind: str
    source: str
    confidence: float
    visible_default: bool
    geometry_kind: str
    area_ratio: float
    aspect_ratio: float
    structural_anchor: bool
    truth_score: float
    valid_for_decision: bool
    label_bbox: list[float]
    label_anchor: str
    z_index: int


class OverlayGeometryPayload(TypedDict, total=False):
    version: int
    chart_bounds: list[float]
    layers: list[str]
    layer_visibility: dict[str, bool]
    boxes: list[OverlayObject]
    layer_counts: dict[str, int]
    visible_default_count: int
    hidden_default_count: int
    debug_enabled: bool
    diagnostics_enabled: bool
    render_budget_ms: int
    static_layers: list[str]
    static_layer_hash: str
    static_layer_count: int
    dynamic_layer_count: int
    temporal_smoothing: dict[str, object]
    broker_exclusion_count: int
    truth_audit: dict[str, object]


def _as_sequence(value: object) -> list[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return []
    return list(cast(Sequence[object], value))


def _as_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (str, bytes, bytearray, Real)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _int(value: object, default: int = 0) -> int:
    number = _float(value, float(default))
    return int(number)


def _float_list(value: object) -> list[float]:
    out: list[float] = []
    for item in _as_sequence(value):
        number = _float(item, float("nan"))
        if number != number:
            return []
        out.append(number)
    return out


def overlay_layer_order() -> tuple[OverlayLayer, ...]:
    return cast(tuple[OverlayLayer, ...], OVERLAY_LAYERS)


def default_layer_visibility() -> dict[OverlayLayer, bool]:
    return cast(dict[OverlayLayer, bool], dict(DEFAULT_LAYER_VISIBILITY))


def normalize_overlay_object(raw: Mapping[str, object], *, fallback_index: int = 0) -> OverlayObject | None:
    bbox_value = raw.get("bbox")
    bbox_input = _as_sequence(bbox_value)
    if not bbox_input:
        return None
    bbox = normalize_bbox(bbox_input)
    if bbox is None:
        return None

    layer = str(raw.get("layer") or raw.get("_layer") or "diagnostics")
    if layer not in OVERLAY_LAYERS:
        layer = "diagnostics"

    key = str(raw.get("key") or raw.get("id") or raw.get("label") or f"overlay_{fallback_index}")
    label = str(raw.get("label") or key)
    row: OverlayObject = {
        "key": key,
        "label": label,
        "layer": cast(OverlayLayer, layer),
        "bbox": [round(float(value), 3) for value in bbox],
        "visible_default": bool(raw.get("visible_default", DEFAULT_LAYER_VISIBILITY.get(layer, False))),
        "area_ratio": _float(raw.get("area_ratio", 0.0) or 0.0),
        "structural_anchor": bool(raw.get("structural_anchor", False)),
    }
    for key_name in ("id", "direction", "role", "kind", "source", "geometry_kind", "label_anchor"):
        value = raw.get(key_name)
        if str(value or "").strip():
            row[cast(Any, key_name)] = str(value)
    for key_name in ("confidence", "aspect_ratio", "truth_score"):
        value = raw.get(key_name)
        if isinstance(value, (int, float)):
            row[cast(Any, key_name)] = float(value)
    if isinstance(raw.get("valid_for_decision"), bool):
        row["valid_for_decision"] = bool(raw["valid_for_decision"])
    if row["area_ratio"] <= 0.0:
        chart_bounds = _as_sequence(raw.get("chart_bounds"))
        chart_area = bbox_area(chart_bounds) if chart_bounds else 0.0
        if chart_area > 0.0:
            row["area_ratio"] = round(bbox_area(bbox) / chart_area, 6)
    return row


def normalize_overlay_geometry(raw: Mapping[str, object]) -> OverlayGeometryPayload:
    boxes = raw.get("boxes", [])
    chart_bounds = raw.get("chart_bounds", [])
    normalized_boxes: list[OverlayObject] = []
    for index, item in enumerate(_as_sequence(boxes)):
        raw_box = _as_mapping(item)
        if not raw_box:
            continue
        box = normalize_overlay_object(raw_box, fallback_index=index)
        if box is not None:
            normalized_boxes.append(box)
    layer_counts = {layer: 0 for layer in OVERLAY_LAYERS}
    for box in normalized_boxes:
        layer = str(box.get("layer") or "debug")
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
    return {
        "version": _int(raw.get("version", 4) or 4, 4),
        "chart_bounds": (
            _float_list(chart_bounds)
        ),
        "layers": list(OVERLAY_LAYERS),
        "layer_visibility": dict(DEFAULT_LAYER_VISIBILITY),
        "boxes": normalized_boxes,
        "layer_counts": layer_counts,
        "visible_default_count": sum(1 for box in normalized_boxes if box.get("visible_default")),
        "hidden_default_count": sum(1 for box in normalized_boxes if not box.get("visible_default")),
    }
