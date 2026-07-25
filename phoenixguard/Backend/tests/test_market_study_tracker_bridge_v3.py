from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenixguard.decision.scene_forecast_contributor_v3 import (
    resolve_closed_candle_identity_v3,
)
from phoenixguard.mobile_api.window_tracker import PhoenixGuardWindowTrackingAdapter


def _tracker_window(closed_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    close_y = 220.0
    for index in range(closed_count + 1):
        direction = "BUY" if index % 4 != 1 else "SELL"
        open_y = close_y
        move = -(2.0 + index % 3) if direction == "BUY" else 1.5 + index % 2
        close_y += move
        rows.append(
            {
                "track_id": index,
                "direction": direction,
                "center_x": 30.0 + index * 10.0,
                "open_y_px": open_y,
                "close_y_px": close_y,
                "wick_top_px": min(open_y, close_y) - 1.0 - index % 2,
                "wick_bottom_px": max(open_y, close_y) + 1.5 + index % 3,
                "is_closed": index < closed_count,
            }
        )
    return rows


def _scene(resolution: dict[str, Any]) -> dict[str, Any]:
    state = dict(resolution["state"])
    return {
        "closed_candle_key": resolution["closed_candle_key"],
        "closed_candle_sequence": resolution["closed_candle_sequence"],
        "closed_candle_identity_state": state,
        "prior_close_reobservation": resolution["prior_close_reobservation"],
        "confirmed_closed_candle_batch": list(
            state.get("confirmed_event_batch", [])
        ),
    }


def _study(
    adapter: PhoenixGuardWindowTrackingAdapter,
    candles: list[dict[str, Any]],
    scene: dict[str, Any],
    *,
    smart_money_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return adapter._build_market_study_v3(  # pyright: ignore[reportPrivateUsage]
        candles=candles,
        market="CAD/JPY OTC",
        timeframe="M5",
        market_identity_confirmed=True,
        timeframe_identity_confirmed=True,
        scene_forecast=scene,
        global_direction="BUY",
        local_direction="BUY",
        impulse_direction="BUY",
        global_slope=0.12,
        local_slope=0.09,
        current_slope=0.07,
        global_window=8,
        recent_window=4,
        current_window=3,
        major_trend_context={"side": "BUY", "confidence": 0.82},
        consolidation_score=0.18,
        image_size=(900, 500),
        structure_boxes=[],
        historical_structure=[],
        support_resistance_zones=[],
        smart_money_context=smart_money_context,
    )


def _source_price_window(closed_count: int) -> list[dict[str, Any]]:
    geometries = (
        (102.0, 103.0, 101.0, 102.5),
        (101.5, 102.0, 100.0, 101.0),
        (102.5, 106.0, 102.0, 105.0),
        (105.5, 110.0, 105.0, 109.0),
        (108.5, 109.0, 104.0, 105.0),
        (105.0, 107.0, 103.0, 106.0),
    )
    return [
        {
            "track_id": index,
            "source_bar_id": f"source-bar-{index}",
            "direction": "BUY" if close >= open_value else "SELL",
            "center_x": 100.0 + index * 12.0,
            "open_y_px": 300.0 - open_value,
            "close_y_px": 300.0 - close,
            "wick_top_px": 300.0 - high,
            "wick_bottom_px": 300.0 - low,
            "is_closed": index < closed_count,
        }
        for index, (open_value, high, low, close) in enumerate(geometries)
        if index <= closed_count
    ]


def test_market_study_objects_publish_an_explicit_candle_compatible_value_axis() -> None:
    objects = PhoenixGuardWindowTrackingAdapter._market_study_objects_v3(  # pyright: ignore[reportPrivateUsage]
        [
            {
                "zone_id": "order-block-1",
                "kind": "ORDER_BLOCK",
                "bbox": [120.0, 80.0, 240.0, 110.0],
            }
        ],
        image_size=(900, 500),
    )

    assert objects == [
        {
            "object_type": "ORDER_BLOCK",
            "object_id": "order-block-1",
            "identity_scope": "OBSERVATION_ONLY",
            "identity_stable": False,
            "direction": "HOLD",
            "confidence": 0.0,
            "bounds": [0.13333333, 0.16, 0.26666667, 0.22],
            "coordinate_space": "NORMALIZED",
            "value_bounds": [-110.0, -80.0],
            "value_coordinate_space": "PIXEL_PRICE_PROXY",
            "value_axis_source": "PIXEL_BBOX",
        }
    ]


def test_market_study_objects_preserve_a_valid_explicit_value_axis() -> None:
    objects = PhoenixGuardWindowTrackingAdapter._market_study_objects_v3(  # pyright: ignore[reportPrivateUsage]
        [
            {
                "object_id": "fvg-price-1",
                "object_type": "PRICE_IMBALANCE",
                "bounds": [0.1, 0.2, 0.4, 0.3],
                "value_bounds": [1.2715, 1.273],
                "value_coordinate_space": "PRICE",
            }
        ],
        image_size=(900, 500),
    )

    assert objects[0]["value_bounds"] == [1.2715, 1.273]
    assert objects[0]["value_coordinate_space"] == "PRICE"
    assert objects[0]["value_axis_source"] == "EXPLICIT"


def test_production_object_families_require_and_preserve_stable_candle_anchors() -> None:
    objects = PhoenixGuardWindowTrackingAdapter._market_study_objects_v3(  # pyright: ignore[reportPrivateUsage]
        [
            {
                "type": "bullish_order_block",
                "source_index": 1,
                "bbox": [100.0, 180.0, 180.0, 190.0],
            }
        ],
        [
            {
                "type": "bearish_fvg",
                "source_index": 2,
                "bbox": [190.0, 170.0, 240.0, 178.0],
            }
        ],
        [
            {
                "kind": "support",
                "key": "support_1",
                "source_indices": [1, 2],
                "knowledge_tags": ["LIQUIDITY_POOL"],
                "touch_count": 3,
                "bbox": [90.0, 195.0, 250.0, 205.0],
            },
            {
                "kind": "support",
                "key": "malformed_touch_count",
                "source_index": 9,
                "knowledge_tags": ["LIQUIDITY_POOL"],
                "touch_count": "not-a-number",
                "bbox": [90.0, 210.0, 250.0, 220.0],
            },
        ],
        image_size=(900, 500),
        stable_candle_identities_by_index={
            1: "EXPLICIT:close-1",
            2: "EXPLICIT:close-2",
        },
    )

    assert [row["object_type"] for row in objects] == [
        "BULLISH_ORDER_BLOCK",
        "BEARISH_FVG",
        "CROWDED_PRICE_AREA",
        "SUPPORT",
    ]
    assert all(
        row["identity_scope"] == "STABLE_CANDLE_ANCHOR"
        and row["identity_stable"] is True
        for row in objects[:3]
    )
    assert objects[2]["associated_candle_ids"] == [
        "EXPLICIT:close-1",
        "EXPLICIT:close-2",
    ]
    assert objects[3]["identity_scope"] == "OBSERVATION_ONLY"
    assert objects[3]["identity_stable"] is False


def test_live_screenshot_rollover_bridges_one_authoritative_close_to_pair_dna(
    tmp_path: Path,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter(
        market_study_root=tmp_path / "tracker-study"
    )
    first_rows = _tracker_window(8)
    first_resolution = resolve_closed_candle_identity_v3(
        first_rows,
        pair="CAD/JPY OTC",
        timeframe="M5",
    )
    first = _study(adapter, first_rows, _scene(first_resolution))
    assert first["outcome_maturation"]["status"] == "NO_PREVIOUS_SEQUENCE"

    second_rows = _tracker_window(9)
    second_resolution = resolve_closed_candle_identity_v3(
        second_rows,
        pair="CAD/JPY OTC",
        timeframe="M5",
        previous_state=first_resolution["state"],
    )
    assert second_resolution["transition_observed"] is True
    assert second_resolution["prior_close_reobservation"]["status"] == "CONFIRMED"

    second = _study(adapter, second_rows, _scene(second_resolution))

    assert second["outcome_maturation"]["status"] == "MATURED"
    assert second["pair_dna"]["candle_count"] == 1
    profile = adapter._market_study_service.pair_dna.get_profile(  # pyright: ignore[reportOptionalMemberAccess,reportPrivateUsage]
        "CAD/JPY OTC",
        "M5",
    )["profile"]
    assert profile["identity_ledger"]["candle_order_domain"] == (
        "TRACKER_EVENT_SEQUENCE_V3"
    )
    assert second["candle_ledger"]["unique_candle_count"] == 2
    latest = second["candle_intelligence"]["latest"]
    assert latest["identity_stable"] is True
    assert latest["identity_proof_source"] == (
        "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
    )
    assert latest["closed_candle_sequence"] == 1


def test_multi_interval_source_gap_cannot_mature_one_candle_pair_dna(
    tmp_path: Path,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter(
        market_study_root=tmp_path / "source-gap-study"
    )
    first_rows = _tracker_window(8)
    first_rows[-2]["bar_open_time"] = 1_783_755_200
    first_resolution = resolve_closed_candle_identity_v3(
        first_rows,
        pair="CAD/JPY OTC",
        timeframe="M5",
    )
    first = _study(adapter, first_rows, _scene(first_resolution))

    gap_rows = _tracker_window(8)
    gap_rows[-2]["bar_open_time"] = 1_783_756_100
    gap_rows[-2]["close_y_px"] = float(gap_rows[-2]["close_y_px"]) - 20.0
    gap_rows[-2]["wick_top_px"] = min(
        float(gap_rows[-2]["open_y_px"]),
        float(gap_rows[-2]["close_y_px"]),
    ) - 1.0
    gap_resolution = resolve_closed_candle_identity_v3(
        gap_rows,
        pair="CAD/JPY OTC",
        timeframe="M5",
        previous_state=first_resolution["state"],
    )

    assert gap_resolution["transition_reason"] == "SOURCE_BAR_GAP_UNPROVEN"
    assert gap_resolution["closed_candle_sequence"] == 0
    assert gap_resolution["closed_candle_key"] == first_resolution["closed_candle_key"]
    repeated = _study(adapter, gap_rows, _scene(gap_resolution))
    assert repeated == first
    assert repeated["outcome_maturation"]["status"] == "NO_PREVIOUS_SEQUENCE"
    assert repeated["pair_dna"].get("observation_count", 0) == 0


def test_source_rollovers_build_stable_history_and_production_object_confluence(
    tmp_path: Path,
) -> None:
    previous_state: dict[str, Any] | None = None
    resolution: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for closed_count in range(1, 6):
        rows = _source_price_window(closed_count)
        resolution = resolve_closed_candle_identity_v3(
            rows,
            pair="CAD/JPY OTC",
            timeframe="M5",
            previous_state=previous_state,
        )
        previous_state = resolution["state"]

    bindings = resolution["stable_visible_candle_bindings"]
    assert [row["closed_candle_sequence"] for row in bindings] == [0, 1, 2, 3, 4]
    assert [row["current_row_index"] for row in bindings] == [0, 1, 2, 3, 4]
    assert bindings[-1]["proof_source"] == "SOURCE_FORMING_BAR_BECAME_CLOSED"

    adapter = PhoenixGuardWindowTrackingAdapter(
        market_study_root=tmp_path / "source-rollover-study"
    )
    result = _study(
        adapter,
        rows,
        _scene(resolution),
        smart_money_context={
            "order_blocks": [
                {
                    "type": "bullish_order_block",
                    "source_index": 1,
                    "bbox": [90.0, 196.8, 190.0, 197.2],
                    "confidence": 0.9,
                }
            ],
            "fair_value_gaps": [],
        },
    )

    graph = result["object_relationship_graph"]
    retracement = graph["retracement_study"]
    assert retracement["status"] == "STUDIED"
    assert {row["level_id"] for row in retracement["observations"]} == {
        "OTE_70_5",
        "CUSTOM_71_8",
    }
    order_block = next(
        node
        for node in graph["nodes"]
        if node.get("node_type") == "MARKET_OBJECT"
        and node.get("object_type") == "BULLISH_ORDER_BLOCK"
    )
    assert order_block["identity_scope"] == "STABLE_CANDLE_ANCHOR"
    assert order_block["identity_stable"] is True


def test_caller_stability_flags_cannot_promote_a_positional_object(
    tmp_path: Path,
) -> None:
    rows = _source_price_window(5)
    rows[0].update(
        {
            "identity_stable": True,
            "stable_candle_identity": "spoofed-positional-identity",
            "identity_proof_source": "CALLER_CLAIM",
        }
    )
    resolution = resolve_closed_candle_identity_v3(
        rows,
        pair="CAD/JPY OTC",
        timeframe="M5",
    )
    adapter = PhoenixGuardWindowTrackingAdapter(
        market_study_root=tmp_path / "spoof-resistant-study"
    )
    result = _study(
        adapter,
        rows,
        _scene(resolution),
        smart_money_context={
            "order_blocks": [
                {
                    "type": "bullish_order_block",
                    "source_index": 0,
                    "zone_id": "positional-zone-1",
                    "bbox": [90.0, 196.8, 190.0, 197.2],
                }
            ]
        },
    )

    graph = result["object_relationship_graph"]
    order_block = next(
        node
        for node in graph["nodes"]
        if node.get("node_type") == "MARKET_OBJECT"
        and node.get("object_type") == "BULLISH_ORDER_BLOCK"
    )
    assert order_block["object_id"] == "positional-zone-1"
    assert order_block["identity_scope"] == "OBSERVATION_ONLY"
    assert order_block["identity_stable"] is False
    assert graph["retracement_study"]["status"] == "NO_COMPARABLE_OBJECTS"
