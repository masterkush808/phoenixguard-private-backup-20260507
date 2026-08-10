from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from phoenixguard.decision.forecast_path_geometry_v3 import FORECAST_HORIZON_STEPS
from phoenixguard.decision.scene_forecast_contributor_v3 import (
    FORECAST_HORIZON_STEPS_V3,
    build_scene_forecast_contribution_v3,
)
from phoenixguard.study.native_horizon_replay_v3 import (
    GeometryPlan,
    NativeReplayConfig,
    draw_native_forecast,
    mask_chart_future,
)


def _candles() -> list[dict]:
    rows: list[dict] = []
    for index in range(96):
        center_x = 80.0 + index * 8.0
        close_y = 410.0 - index * 0.75 + 18.0 * ((index % 12) / 11.0 - 0.5)
        open_y = close_y + (7.0 if index % 3 else -6.0)
        rows.append(
            {
                "center_x_px": center_x,
                "center_y_px": (open_y + close_y) / 2.0,
                "center_x": center_x,
                "center_y": (open_y + close_y) / 2.0,
                "normalized_x": center_x / 1000.0,
                "normalized_y": ((open_y + close_y) / 2.0) / 700.0,
                "open_y_px": open_y,
                "close_y_px": close_y,
                "wick_top_px": min(open_y, close_y) - 5.0,
                "wick_bottom_px": max(open_y, close_y) + 5.0,
                "body_top_px": min(open_y, close_y),
                "body_bottom_px": max(open_y, close_y),
                "parse_confidence": 1.0,
                "is_closed": True,
                "closed": True,
                "forming": False,
            }
        )
    return rows


def test_native_scene_forecast_publishes_72_anchored_ohlc_candles() -> None:
    rows = _candles()[:24]
    event_key = hashlib.sha256(b"native-72-horizon-test").hexdigest()
    result = build_scene_forecast_contribution_v3(
        candles=rows,
        image_size=(1000, 700),
        timeframe="H1",
        pair="AUD/USD",
        behavior_payload={
            "current_state": "EXPANSION",
            "next_state_probs": {"BUY": 0.62, "SELL": 0.24, "PAUSE": 0.14},
        },
        smart_money_context={"dominant_side": "BUY", "buy_score": 0.7, "sell_score": 0.3},
        trend_directions={"global": "BUY", "local": "BUY", "impulse": "BUY", "major": "BUY"},
        trend_slopes={"global": -0.01, "local": -0.02, "current": -0.02},
        allow_foundation_model=False,
        event_key_override=event_key,
    )
    forecast = result["forecast_candles"]
    assert FORECAST_HORIZON_STEPS == 72
    assert FORECAST_HORIZON_STEPS_V3 == 72
    assert result["path_target_semantics"] == "DIRECT_72_EVENT_COHERENT_TRAJECTORY"
    assert len(forecast) == 72
    assert len(result["forecast_path"]) == 72
    assert forecast[0]["open_y_norm"] == rows[-1]["close_y_px"] / 700.0
    assert all(row["high_y_norm"] <= min(row["open_y_norm"], row["close_y_norm"]) for row in forecast)
    assert all(row["low_y_norm"] >= max(row["open_y_norm"], row["close_y_norm"]) for row in forecast)
    assert "LSTM" not in str(result).upper()


def test_chart_mask_preserves_ui_and_native_renderer_draws_full_horizon() -> None:
    config = NativeReplayConfig()
    image = Image.new("RGB", (1000, 700), (34, 54, 77))
    visible = tuple(_candles()[:24])
    plan = GeometryPlan(
        image_size=image.size,
        chart_bbox=(50, 100, 950, 650),
        cut_x=276,
        spacing_px=8.0,
        available_future_candles=72,
        visible_tracks=visible,
        anchor_track=visible[-1],
    )
    masked = mask_chart_future(image, plan, config.mask_rgb)
    assert masked.getpixel((500, 50)) == (34, 54, 77)
    assert masked.getpixel((500, 300)) == config.mask_rgb
    forecast = []
    anchor_y = visible[-1]["close_y_px"] / image.height
    for step in range(1, 73):
        close_y = anchor_y - 0.0015 * step + 0.012 * ((step % 9) / 8.0 - 0.5)
        open_y = anchor_y if step == 1 else forecast[-1]["close_y_norm"]
        forecast.append(
            {
                "step": step,
                "open_y_norm": open_y,
                "high_y_norm": min(open_y, close_y) - 0.006,
                "low_y_norm": max(open_y, close_y) + 0.006,
                "close_y_norm": close_y,
            }
        )
    from phoenixguard.study.native_horizon_replay_v3 import ChartIdentity, FrozenNativeForecast

    provisional = FrozenNativeForecast(
        identity=ChartIdentity("AUD/USD", "H1", 0.99, "test"),
        plan=plan,
        mask_sha256="mask",
        freeze_sha256="freeze",
        forecast_candles=tuple(forecast),
        forecast_path=tuple({"step": index} for index in range(1, 73)),
        forecast_scenarios=(),
        contribution={"provider": "PHOENIXGUARD_NATIVE_SCENE_TRAJECTORY_V3"},
        evidence={},
        prediction_canvas=masked,
    )
    rendered = draw_native_forecast(masked, provisional)
    assert rendered.width >= int(visible[-1]["center_x_px"] + 75 * 8.0)
    assert rendered.getbbox() is not None
