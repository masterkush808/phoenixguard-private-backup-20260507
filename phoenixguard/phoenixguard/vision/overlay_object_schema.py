from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence, TypedDict, cast

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
    temporal_smoothing: dict[str, Any]
    broker_exclusion_count: int
    truth_audit: dict[str, Any]


def overlay_layer_order() -> tuple[OverlayLayer, ...]:
    return cast(tuple[OverlayLayer, ...], OVERLAY_LAYERS)


def default_layer_visibility() -> dict[OverlayLayer, bool]:
    return cast(dict[OverlayLayer, bool], dict(DEFAULT_LAYER_VISIBILITY))


def normalize_overlay_object(raw: Mapping[str, Any], *, fallback_index: int = 0) -> OverlayObject | None:
    bbox_value = raw.get("bbox")
    if not isinstance(bbox_value, Sequence) or isinstance(bbox_value, (str, bytes, bytearray)):
        return None
    bbox = normalize_bbox(bbox_value)
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
        "area_ratio": float(raw.get("area_ratio", 0.0) or 0.0),
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
        chart_bounds = raw.get("chart_bounds")
        chart_area = (
            bbox_area(chart_bounds)
            if isinstance(chart_bounds, Sequence) and not isinstance(chart_bounds, (str, bytes, bytearray))
            else 0.0
        )
        if chart_area > 0.0:
            row["area_ratio"] = round(bbox_area(bbox) / chart_area, 6)
    return row


def normalize_overlay_geometry(raw: Mapping[str, Any]) -> OverlayGeometryPayload:
    boxes = raw.get("boxes", [])
    chart_bounds = raw.get("chart_bounds", [])
    box_items = (
        boxes
        if isinstance(boxes, Sequence) and not isinstance(boxes, (str, bytes, bytearray))
        else []
    )
    normalized_boxes = [
        box
        for index, item in enumerate(box_items)
        if isinstance(item, Mapping)
        for box in [normalize_overlay_object(item, fallback_index=index)]
        if box is not None
    ]
    layer_counts = {layer: 0 for layer in OVERLAY_LAYERS}
    for box in normalized_boxes:
        layer_counts[str(box["layer"])] += 1
    return {
        "version": int(raw.get("version", 4) or 4),
        "chart_bounds": (
            list(chart_bounds)
            if isinstance(chart_bounds, Sequence) and not isinstance(chart_bounds, (str, bytes, bytearray))
            else []
        ),
        "layers": list(OVERLAY_LAYERS),
        "layer_visibility": dict(DEFAULT_LAYER_VISIBILITY),
        "boxes": normalized_boxes,
        "layer_counts": layer_counts,
        "visible_default_count": sum(1 for box in normalized_boxes if box.get("visible_default")),
        "hidden_default_count": sum(1 for box in normalized_boxes if not box.get("visible_default")),
    }
