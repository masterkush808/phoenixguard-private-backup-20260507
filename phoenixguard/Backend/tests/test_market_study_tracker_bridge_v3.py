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
    )


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
