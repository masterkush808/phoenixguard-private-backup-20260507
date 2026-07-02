from __future__ import annotations

from typing import Any

from phoenixguard.decision.candle_movement_context_v3 import build_candle_movement_context_v3


def _candle(index: int, side: str, price: float) -> dict[str, Any]:
    x0 = 20 + index * 10
    y0 = 180 - index * 4 if side == "BUY" else 90 + index * 5
    return {
        "index": index,
        "bbox": [x0, y0, x0 + 8, y0 + 28],
        "center_x": x0 + 4,
        "center_y": y0 + 14,
        "direction": side,
        "price_proxy": price,
        "body_height_pct": 0.55,
    }


def test_candle_movement_context_counts_visible_move_legs_boxes_and_duration() -> None:
    candles = [
        *[_candle(index, "SELL", 1.0 - index * 0.01) for index in range(6)],
        *[_candle(index, "BUY", 0.91 + (index - 6) * 0.012) for index in range(6, 14)],
    ]
    snapshot: dict[str, Any] = {
        "tracking_summary": {
            "detected_timeframe": "M5",
            "visible_candle_count": 14,
            "tracked_candles": candles,
            "historical_structure": [
                {
                    "label": "H1 SELL",
                    "direction": "SELL",
                    "source_indices": list(range(6)),
                    "candle_count": 6,
                    "net_move": -0.06,
                    "slope": -0.01,
                },
                {
                    "label": "H2 BUY",
                    "direction": "BUY",
                    "source_indices": list(range(6, 14)),
                    "candle_count": 8,
                    "net_move": 0.084,
                    "slope": 0.012,
                },
            ],
            "support_resistance_zones": [
                {
                    "label": "DEMAND",
                    "role": "DEMAND",
                    "bbox": [78, 118, 124, 180],
                    "anchor_candles": [6, 7, 8],
                    "confidence": 0.82,
                }
            ],
        },
        "candidate_side": "BUY",
        "risk_opposing_force": {"distance_to_opposing_force": 0.36, "distance_ok": True},
    }

    context = build_candle_movement_context_v3(snapshot)

    assert context["visible_candle_count"] == 14
    assert context["tracked_candle_count"] == 14
    assert context["current_leg"]["side"] == "BUY"
    assert context["current_leg"]["candle_count"] == 8
    assert context["current_leg"]["duration"]["minutes"] == 40.0
    assert context["move_stage"] == "MATURE"
    assert context["opposing_force_room"]["room_ok"] is True
    assert context["opposing_force_room"]["estimated_candles_to_force"] == 5
    assert context["candles_per_leg"] == [
        {"label": "H1 SELL", "side": "SELL", "candle_count": 6, "duration": {"seconds": 1800, "minutes": 30.0, "text": "30.0m"}, "move_stage": "MATURE"},
        {"label": "H2 BUY", "side": "BUY", "candle_count": 8, "duration": {"seconds": 2400, "minutes": 40.0, "text": "40.0m"}, "move_stage": "MATURE"},
    ]
    demand = next(row for row in context["box_candle_counts"] if row["label"] == "DEMAND")
    assert demand["label"] == "DEMAND"
    assert demand["anchor_candle_count"] == 3
    assert demand["contained_candle_count"] >= 1


def test_candle_movement_context_marks_reclaiming_and_late_near_opposing_force() -> None:
    candles = [
        *[_candle(index, "SELL", 1.0 - index * 0.01) for index in range(14)],
        *[_candle(index, "BUY", 0.84 + (index - 14) * 0.01) for index in range(14, 18)],
    ]
    snapshot: dict[str, Any] = {
        "timeframe": "M5",
        "tracking_summary": {
            "visible_candle_count": 18,
            "tracked_candles": candles,
            "historical_structure": [
                {"label": "H1 SELL", "direction": "SELL", "source_indices": list(range(14)), "candle_count": 14},
                {"label": "H2 BUY", "direction": "BUY", "source_indices": list(range(14, 18)), "candle_count": 4},
            ],
        },
        "candidate_side": "BUY",
        "risk_opposing_force": {"distance_to_opposing_force": 0.42, "distance_ok": True},
    }
    reclaiming = build_candle_movement_context_v3(snapshot)
    assert reclaiming["move_stage"] == "STILL_RECLAIMING"
    assert reclaiming["current_leg"]["duration"]["minutes"] == 20.0

    snapshot["risk_opposing_force"] = {"distance_to_opposing_force": 0.08, "distance_ok": False}
    late = build_candle_movement_context_v3(snapshot)
    assert late["move_stage"] == "LATE"
    assert late["opposing_force_room"]["room_ok"] is False
