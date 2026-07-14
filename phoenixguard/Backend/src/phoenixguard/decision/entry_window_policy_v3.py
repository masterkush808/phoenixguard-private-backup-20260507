from __future__ import annotations

from typing import Literal, TypedDict


ENTRY_WINDOW_POLICY_SCHEMA_VERSION = "PG_ENTRY_WINDOW_POLICY_V3"
ENTRY_LOCATION_GUIDANCE_SCHEMA_VERSION = "PG_ENTRY_LOCATION_GUIDANCE_V3"
MINIMUM_ENTRY_WINDOW_SECONDS = 10 * 60
MAXIMUM_ENTRY_WINDOW_SECONDS = 15 * 60


class EntryWindowPolicyV3(TypedDict):
    schema_version: str
    duration_sec: int
    minimum_duration_sec: int
    maximum_duration_sec: int
    timeframe_seconds: int
    opening_candle_remaining_sec: int
    trade_expiry_reference_sec: int
    basis: str
    closes_early_on: list[str]


class EntryLocationGuidanceV3(TypedDict):
    schema_version: str
    side: Literal["BUY", "SELL", "HOLD"]
    rule: Literal["BUY_LOW", "SELL_HIGH", "WAIT_FOR_VERIFIED_AREA"]
    preferred_price_location: Literal["LOWER_PRICE", "HIGHER_PRICE", "NONE"]
    verified_area: Literal["DEMAND_OR_RETEST", "SUPPLY_OR_RETEST", "NONE"]
    short_label: str
    message: str


def resolve_entry_window_policy_v3(
    *,
    timeframe_seconds: int,
    opening_candle_remaining_seconds: int,
    trade_expiry_reference_seconds: int,
) -> EntryWindowPolicyV3:
    """Build a chart-aware setup window without extending execution authority.

    A short candle-close pulse may open the setup, but it must not define how
    long the chart opportunity remains visible.  Ten minutes is the floor and
    the remainder of the opening candle supplies the chart-aware fluctuation,
    capped at fifteen minutes.  Live invalidation remains authoritative.
    """

    safe_timeframe_seconds = max(0, int(timeframe_seconds))
    supplied_remaining_seconds = max(0, int(opening_candle_remaining_seconds))
    bounded_candle_remaining_seconds = min(
        supplied_remaining_seconds,
        safe_timeframe_seconds,
        MAXIMUM_ENTRY_WINDOW_SECONDS - MINIMUM_ENTRY_WINDOW_SECONDS,
    )
    duration_seconds = min(
        MAXIMUM_ENTRY_WINDOW_SECONDS,
        MINIMUM_ENTRY_WINDOW_SECONDS + bounded_candle_remaining_seconds,
    )
    if safe_timeframe_seconds >= MAXIMUM_ENTRY_WINDOW_SECONDS:
        duration_seconds = MAXIMUM_ENTRY_WINDOW_SECONDS

    return {
        "schema_version": ENTRY_WINDOW_POLICY_SCHEMA_VERSION,
        "duration_sec": duration_seconds,
        "minimum_duration_sec": MINIMUM_ENTRY_WINDOW_SECONDS,
        "maximum_duration_sec": MAXIMUM_ENTRY_WINDOW_SECONDS,
        "timeframe_seconds": safe_timeframe_seconds,
        "opening_candle_remaining_sec": bounded_candle_remaining_seconds,
        "trade_expiry_reference_sec": max(0, int(trade_expiry_reference_seconds)),
        "basis": "ten_minute_setup_plus_opening_candle_remainder_capped_at_fifteen_minutes",
        "closes_early_on": [
            "live direction or pressure contradicts the entry",
            "verified demand, supply, or retest area invalidates",
            "price becomes a late chase",
            "visual evidence becomes stale or uncertain",
            "candidate identity or side changes",
        ],
    }


def entry_location_guidance_v3(side: str) -> EntryLocationGuidanceV3:
    normalized_side = str(side or "").strip().upper()
    if normalized_side == "BUY":
        return {
            "schema_version": ENTRY_LOCATION_GUIDANCE_SCHEMA_VERSION,
            "side": "BUY",
            "rule": "BUY_LOW",
            "preferred_price_location": "LOWER_PRICE",
            "verified_area": "DEMAND_OR_RETEST",
            "short_label": "Buy lower inside the verified area",
            "message": (
                "Aim for a lower price inside the verified demand or retest area; "
                "do not chase highs."
            ),
        }
    if normalized_side == "SELL":
        return {
            "schema_version": ENTRY_LOCATION_GUIDANCE_SCHEMA_VERSION,
            "side": "SELL",
            "rule": "SELL_HIGH",
            "preferred_price_location": "HIGHER_PRICE",
            "verified_area": "SUPPLY_OR_RETEST",
            "short_label": "Sell higher inside the verified area",
            "message": (
                "Aim for a higher price inside the verified supply or retest area; "
                "do not chase lows."
            ),
        }
    return {
        "schema_version": ENTRY_LOCATION_GUIDANCE_SCHEMA_VERSION,
        "side": "HOLD",
        "rule": "WAIT_FOR_VERIFIED_AREA",
        "preferred_price_location": "NONE",
        "verified_area": "NONE",
        "short_label": "Wait for a verified entry area",
        "message": "Wait until direction and a verified entry area are both clear.",
    }
