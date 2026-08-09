from __future__ import annotations

from phoenixguard.study.directional_consensus_v3 import (
    reset_directional_consensus_v3,
    resolve_directional_consensus_v3,
)


def _study(closed_key: str, *, local_state: str, buy: float, sell: float) -> dict:
    return {
        "symbol": "NZD/USD OTC",
        "timeframe": "M5",
        "closed_candle_key": closed_key,
        "hidden_state_discovery_v3": {
            "symbol": "NZD/USD OTC",
            "timeframe": "M5",
            "hidden_state": {
                "state": local_state,
                "direction": "BUY" if local_state == "UP_SWING" else "SELL",
                "age_candles": 2,
                "segment_count": 10,
            },
            "control": {"side": "UNRESOLVED"},
            "directional_outcome_distribution": {"BUY": buy, "SELL": sell},
            "directional_components": {"BUY": {}, "SELL": {}, "REST": {}},
        },
    }


def _line(direction: str, *, touches: int, distance: float, current: bool = False) -> dict:
    return {
        "type": "SUPPORT_TRENDLINE" if direction == "BUY" else "RESISTANCE_TRENDLINE",
        "trendline_role": "support" if direction == "BUY" else "resistance",
        "trendline_scope": "MAJOR",
        "direction": direction,
        "touch_count": touches,
        "anchor_span_bars": 20,
        "confirmation_state": "CONFIRMED" if touches >= 3 else "DEVELOPING",
        "significant_close": False,
        "breach_state": "ACTIVE",
        "touch_candle_indices": [1, 4, 9] if current else [1, 4],
        "forming_touch": False,
        "close_distance_norm": distance,
        "current_projection_visible": True,
        "confidence": 0.8,
    }


def test_consensus_keeps_sell_structure_during_local_buy_pullback() -> None:
    reset_directional_consensus_v3()
    result = resolve_directional_consensus_v3(
        _study("closed-10", local_state="UP_SWING", buy=0.3, sell=0.7),
        symbol="NZD/USD OTC",
        timeframe="M5",
        major_side="SELL",
        global_side="SELL",
        local_side="SELL",
        trendlines=[
            _line("BUY", touches=4, distance=0.2),
            _line("SELL", touches=2, distance=9.0),
        ],
        candles=[{"source_index": index} for index in range(10)],
    )

    latent = result["hidden_state_discovery_v3"]
    control = latent["control"]
    assert control["side"] == "UNRESOLVED"
    assert control["directional_side"] == "SELL"
    assert control["local_leg_side"] == "BUY"
    assert control["status"] == "STABLE_DIRECTION_AT_OPPOSING_FORCE"
    assert control["structural_evidence"]["selected_line"]["direction"] == "SELL"
    assert control["structural_evidence"]["opposing_line"]["direction"] == "BUY"
    assert "not a direction flip" in control["explanation"]


def test_consensus_requires_three_distinct_closes_to_reverse() -> None:
    reset_directional_consensus_v3()
    candles = [{"source_index": index} for index in range(10)]
    initial = resolve_directional_consensus_v3(
        _study("closed-1", local_state="DOWN_SWING", buy=0.1, sell=0.9),
        symbol="TEST",
        timeframe="M5",
        major_side="SELL",
        global_side="SELL",
        local_side="SELL",
        trendlines=[_line("SELL", touches=3, distance=1.0)],
        candles=candles,
    )
    assert initial["hidden_state_discovery_v3"]["control"]["directional_side"] == "SELL"

    for index in (2, 3):
        retained = resolve_directional_consensus_v3(
            _study(f"closed-{index}", local_state="UP_SWING", buy=0.9, sell=0.1),
            symbol="TEST",
            timeframe="M5",
            major_side="BUY",
            global_side="BUY",
            local_side="BUY",
            trendlines=[_line("BUY", touches=3, distance=1.0)],
            candles=candles,
        )
        assert retained["hidden_state_discovery_v3"]["control"]["directional_side"] == "SELL"

    switched = resolve_directional_consensus_v3(
        _study("closed-4", local_state="UP_SWING", buy=0.9, sell=0.1),
        symbol="TEST",
        timeframe="M5",
        major_side="BUY",
        global_side="BUY",
        local_side="BUY",
        trendlines=[_line("BUY", touches=3, distance=1.0)],
        candles=candles,
    )
    assert switched["hidden_state_discovery_v3"]["control"]["directional_side"] == "BUY"
