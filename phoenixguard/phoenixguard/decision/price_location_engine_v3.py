from __future__ import annotations

from statistics import mean
from typing import Any, Mapping, Sequence


PRICE_LOCATION_ENGINE_VERSION = "PG_PRICE_LOCATION_ENGINE_V3"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _upper(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text or default


def _nested_rows(snapshot: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        rows = _rows(snapshot.get(key))
        if rows:
            return rows
    tracking = _mapping(snapshot.get("tracking_summary"))
    for key in keys:
        rows = _rows(tracking.get(key))
        if rows:
            return rows
    return []


def _candle_value(row: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in row:
            return _float(row.get(key), default)
    return float(default)


def _ohlc(row: Mapping[str, Any]) -> tuple[float, float, float, float]:
    fallback = _candle_value(row, "close", "c", "price_proxy", "y", default=0.0)
    open_value = _candle_value(row, "open", "o", default=fallback)
    close_value = _candle_value(row, "close", "c", "price_proxy", "y", default=fallback)
    high_value = _candle_value(row, "high", "h", default=max(open_value, close_value))
    low_value = _candle_value(row, "low", "l", default=min(open_value, close_value))
    if high_value < low_value:
        high_value, low_value = low_value, high_value
    return open_value, high_value, low_value, close_value


def _candles(snapshot: Mapping[str, Any]) -> list[tuple[float, float, float, float]]:
    rows = _nested_rows(snapshot, "candles", "tracked_candles", "candle_map", "ohlc")
    parsed = [_ohlc(row) for row in rows]
    return [row for row in parsed if any(abs(value) > 1e-12 for value in row)]


def _position(value: float, low: float, high: float, fallback: float = 0.5) -> float:
    span = max(1e-9, float(high) - float(low))
    if span <= 1e-9:
        return _clip01(fallback, 0.5)
    return _clip01((float(value) - float(low)) / span, fallback)


def _range_position_from_context(snapshot: Mapping[str, Any], key: str, default: float = 0.5) -> float:
    direct = snapshot.get(key)
    if direct is not None:
        return _clip01(direct, default)
    context = _mapping(snapshot.get("market_context"))
    return _clip01(context.get(key), default)


def _classify_position(position: float) -> str:
    if position <= 0.28:
        return "LOCAL_LOW"
    if position >= 0.72:
        return "LOCAL_HIGH"
    return "MIDDLE"


def _quality_for_side(side: str, local_position: float, global_position: float) -> tuple[str, str]:
    blended = 0.70 * local_position + 0.30 * global_position
    if side == "BUY":
        if blended <= 0.34:
            return "GOOD", "POOR"
        if blended >= 0.66:
            return "POOR", "GOOD"
        return "NEEDS_CONFIRMATION", "NEEDS_CONFIRMATION"
    if side == "SELL":
        if blended >= 0.66:
            return "POOR", "GOOD"
        if blended <= 0.34:
            return "GOOD", "POOR"
        return "NEEDS_CONFIRMATION", "NEEDS_CONFIRMATION"
    if blended <= 0.34:
        return "GOOD", "POOR"
    if blended >= 0.66:
        return "POOR", "GOOD"
    return "NEEDS_CONFIRMATION", "NEEDS_CONFIRMATION"


def _nearest_zones(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    zones = _rows(snapshot.get("zones") or snapshot.get("support_resistance_zones"))
    supply_candidates: list[tuple[float, dict[str, Any]]] = []
    demand_candidates: list[tuple[float, dict[str, Any]]] = []
    for zone in zones:
        zone_type = _upper(zone.get("zone_type") or zone.get("type") or zone.get("kind"))
        if zone.get("broken") is True or zone.get("is_broken") is True:
            continue
        distance = _clip01(zone.get("distance_from_current", zone.get("distance", 1.0)), 1.0)
        if "SUPPLY" in zone_type or "SELL" in zone_type or "RESIST" in zone_type:
            supply_candidates.append((distance, zone))
        if "DEMAND" in zone_type or "BUY" in zone_type or "SUPPORT" in zone_type:
            demand_candidates.append((distance, zone))

    supply = dict(min(supply_candidates, key=lambda item: item[0])[1]) if supply_candidates else {}
    demand = dict(min(demand_candidates, key=lambda item: item[0])[1]) if demand_candidates else {}
    return supply, demand


def analyze_price_location_v3(
    snapshot: Mapping[str, Any] | None,
    *,
    side: str | None = None,
) -> dict[str, Any]:
    source = dict(snapshot or {})
    resolved_side = _side(side or source.get("candidate_side") or source.get("side") or _mapping(source.get("market_context")).get("dominant_side"))
    candles = _candles(source)
    closes = [row[3] for row in candles]
    highs = [row[1] for row in candles]
    lows = [row[2] for row in candles]
    current = _float(source.get("current_price"), closes[-1] if closes else 0.0)

    if candles:
        global_low = min(lows)
        global_high = max(highs)
        local_slice = candles[-min(12, len(candles)) :]
        local_low = min(row[2] for row in local_slice)
        local_high = max(row[1] for row in local_slice)
        impulse_slice = candles[-min(5, len(candles)) :]
        impulse_low = min(row[2] for row in impulse_slice)
        impulse_high = max(row[1] for row in impulse_slice)
        global_position = _position(current, global_low, global_high)
        local_position = _position(current, local_low, local_high)
        impulse_position = _position(current, impulse_low, impulse_high)
    else:
        global_position = _range_position_from_context(source, "global_range_position", 0.5)
        local_position = _range_position_from_context(source, "local_range_position", global_position)
        impulse_position = _range_position_from_context(source, "impulse_range_position", local_position)
        global_low = local_low = impulse_low = 0.0
        global_high = local_high = impulse_high = 1.0

    pullback_position = _clip01(source.get("pullback_position", source.get("pullback_depth", local_position)), local_position)
    supply, demand = _nearest_zones(source)
    nearest_supply_distance = _clip01(supply.get("distance_from_current", supply.get("distance", 1.0)), 1.0) if supply else 1.0
    nearest_demand_distance = _clip01(demand.get("distance_from_current", demand.get("distance", 1.0)), 1.0) if demand else 1.0
    buy_quality, sell_quality = _quality_for_side(resolved_side, local_position, global_position)
    relative_location = _classify_position(local_position)

    current_location = _upper(source.get("current_location") or _mapping(source.get("market_context")).get("current_location"))
    if "HIGH" in current_location or "SUPPLY" in current_location or "SELL" in current_location:
        relative_location = "LOCAL_HIGH"
        local_position = max(local_position, 0.76)
        buy_quality, sell_quality = _quality_for_side(resolved_side, local_position, global_position)
    elif "LOW" in current_location or "DEMAND" in current_location or "BUY" in current_location:
        relative_location = "LOCAL_LOW"
        local_position = min(local_position, 0.24)
        buy_quality, sell_quality = _quality_for_side(resolved_side, local_position, global_position)
    elif "MIDDLE" in current_location or "MID" in current_location:
        relative_location = "MIDDLE"

    distance_to_opposing = nearest_supply_distance if resolved_side == "BUY" else nearest_demand_distance if resolved_side == "SELL" else min(nearest_supply_distance, nearest_demand_distance)
    path_room = _clip01(source.get("path_room", distance_to_opposing), distance_to_opposing)
    recent_body_sizes = [abs(row[3] - row[0]) for row in candles[-8:]]
    average_body_size = mean(recent_body_sizes) if recent_body_sizes else 0.0

    reason = (
        "Price is high inside the local structure; SELL quality is better than BUY quality."
        if relative_location == "LOCAL_HIGH"
        else "Price is low inside the local structure; BUY quality is better than SELL quality."
        if relative_location == "LOCAL_LOW"
        else "Price is in the middle of the local structure; execution needs stronger confirmation and path room."
    )

    return {
        "version": PRICE_LOCATION_ENGINE_VERSION,
        "price_location": {
            "side": resolved_side,
            "global_range_position": round(global_position, 4),
            "local_range_position": round(local_position, 4),
            "impulse_range_position": round(impulse_position, 4),
            "pullback_position": round(pullback_position, 4),
            "relative_location": relative_location,
            "price_location": relative_location,
            "global_bounds": [round(global_low, 8), round(global_high, 8)],
            "local_bounds": [round(local_low, 8), round(local_high, 8)],
            "impulse_bounds": [round(impulse_low, 8), round(impulse_high, 8)],
            "nearest_supply_zone_id": str(supply.get("zone_id") or supply.get("id") or ""),
            "nearest_demand_zone_id": str(demand.get("zone_id") or demand.get("id") or ""),
            "nearest_supply_distance": round(nearest_supply_distance, 4),
            "nearest_demand_distance": round(nearest_demand_distance, 4),
            "path_room": round(path_room, 4),
            "average_recent_body_size": round(float(average_body_size), 8),
            "buy_quality": buy_quality,
            "sell_quality": sell_quality,
            "side_quality": buy_quality if resolved_side == "BUY" else sell_quality if resolved_side == "SELL" else "NEEDS_CONFIRMATION",
            "reason": reason,
        },
    }


__all__ = [
    "PRICE_LOCATION_ENGINE_VERSION",
    "analyze_price_location_v3",
]
