from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from phoenixguard.runtime.realtime_performance_v3 import OVERLAY_RENDER_BUDGETS
from phoenixguard.vision.v3_overlay_contract import (
    OVERLAY_LAYER_ORDER,
    VIEW_MODES,
    normalize_view_mode,
    overlay_is_visible,
    overlay_layer_name,
    overlay_type_priority,
)


OVERLAY_LAYER_MANAGER_SCHEMA_VERSION = "PG_OVERLAY_LAYER_MANAGER_V3"


@dataclass(frozen=True)
class OverlayLayerManagerV3:
    mode: str = "CLEAN_LIVE"

    def normalized_mode(self) -> str:
        value = normalize_view_mode(self.mode)
        return value if value in VIEW_MODES else "CLEAN_LIVE"

    def layer_order(self) -> tuple[str, ...]:
        return OVERLAY_LAYER_ORDER

    def overlay_sort_key(self, overlay: Mapping[str, Any]) -> tuple[int, int, float]:
        layer = overlay_layer_name(overlay.get("type"), overlay.get("layer"))
        layer_index = OVERLAY_LAYER_ORDER.index(layer) if layer in OVERLAY_LAYER_ORDER else len(OVERLAY_LAYER_ORDER)
        return (
            layer_index,
            overlay_type_priority(overlay.get("type")),
            float(overlay.get("truth_score", overlay.get("confidence", 0.0)) or 0.0),
        )

    def resolve(self, overlays: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        rows = [dict(row) for row in overlays if overlay_is_visible(row, self.normalized_mode())]
        budget = int(OVERLAY_RENDER_BUDGETS.get(self.normalized_mode(), len(rows)))
        return sorted(rows, key=self.overlay_sort_key)[:budget]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OVERLAY_LAYER_MANAGER_SCHEMA_VERSION,
            "mode": self.normalized_mode(),
            "layer_order": list(self.layer_order()),
            "overlay_render_budget": dict(OVERLAY_RENDER_BUDGETS),
            "active_budget": int(OVERLAY_RENDER_BUDGETS.get(self.normalized_mode(), 0)),
        }


__all__ = [
    "OVERLAY_LAYER_MANAGER_SCHEMA_VERSION",
    "OverlayLayerManagerV3",
]
