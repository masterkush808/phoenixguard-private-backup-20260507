from __future__ import annotations

from typing import Any

from phoenixguard.mobile_api.window_tracker import (
    PhoenixGuardWindowTrackingAdapter,
    normalize_execution_controls,
)


def _manual_candles(levels: list[float], *, image_width: int = 960) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    step = max(12.0, float(image_width - 120) / max(1, len(levels)))
    for index, center_y in enumerate(levels):
        x = 60.0 + index * step
        half_height = 18.0 + (index % 3) * 3.0
        candles.append(
            {
                "track_id": f"candle-{index}",
                "bbox": [x - 5.0, center_y - half_height, x + 5.0, center_y + half_height],
                "center_x": x,
                "center_y": center_y,
                "height": half_height * 2.0,
                "direction": "BUY" if index % 2 == 0 else "SELL",
            }
        )
    return candles


def test_support_resistance_depth_can_exceed_four_zones() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    levels = [
        330,
        292,
        248,
        306,
        360,
        276,
        228,
        318,
        382,
        262,
        216,
        340,
        404,
        286,
        238,
        356,
        424,
        302,
        252,
        372,
        444,
        318,
        268,
        388,
    ]
    levels = [float(level) for level in levels]

    zones = adapter.derive_support_resistance_zones(  # noqa: SLF001
        _manual_candles(levels),
        (960, 540),
        candidate_action="BUY",
        max_zones_per_role=4,
        max_total_zones=8,
    )

    assert len(zones) > 4
    assert len(zones) <= 8
    assert {str(zone.get("role")) for zone in zones} == {"support", "resistance"}
    assert all("zone_family" in zone for zone in zones)
    assert all("liquidity_pool_type" in zone for zone in zones)
    assert all("SUPPORT_RESISTANCE_AS_ZONE" in zone.get("knowledge_tags", []) for zone in zones)


def test_execution_controls_normalize_knowledge_depth_knobs() -> None:
    controls = normalize_execution_controls(
        {
            "live_max_tracked_candles": 512,
            "support_resistance_max_zones_per_role": 20,
            "support_resistance_max_total_zones": 40,
            "support_resistance_max_significant_zones": 40,
            "smart_money_max_liquidity_pools": 40,
        }
    )

    assert controls["live_max_tracked_candles"] == 256
    assert controls["support_resistance_max_zones_per_role"] == 12
    assert controls["support_resistance_max_total_zones"] == 24
    assert controls["support_resistance_max_significant_zones"] == 24
    assert controls["smart_money_max_liquidity_pools"] == 24
