from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from phoenixguard.study.v3_causal_path_replay import (
    DIRECT_PATH_SEMANTICS,
    DiskReserveError,
    ReplayConfig,
    ensure_disk_reserve,
    freeze_v3_prediction,
    mask_future_pixels,
    recompute_freeze_sha256,
    render_evidence_sheet,
    reveal_and_score_v3_prediction,
)


MASK = (7, 9, 11)


def _track(x_value: float, close_y: float, open_y: float, direction: str) -> dict:
    return {
        "center_x_px": x_value,
        "center_y_px": (close_y + open_y) / 2.0,
        "open_y_px": open_y,
        "close_y_px": close_y,
        "wick_top_px": min(close_y, open_y) - 2.0,
        "wick_bottom_px": max(close_y, open_y) + 2.0,
        "parse_confidence": 1.0,
        "direction": direction,
        "color": "intentionally-wrong-label",
    }


VISIBLE_TRACKS = [
    _track(10, 62, 60, "SELL"),
    _track(20, 58, 61, "SELL"),
    _track(30, 54, 57, "SELL"),
    _track(40, 52, 55, "SELL"),
    _track(50, 50, 52, "SELL"),
]
FUTURE_TRACKS = [
    _track(70, 45, 48, "SELL"),
    _track(80, 48, 44, "BUY"),
    _track(90, 42, 49, "SELL"),
    _track(100, 40, 43, "SELL"),
]


def _forecast_path() -> list[dict]:
    cumulative = [0.05, 0.02, 0.08, 0.10]
    movements = [0.05, -0.03, 0.06, 0.02]
    rows = []
    for step, (path_value, movement) in enumerate(zip(cumulative, movements), start=1):
        close_value = 0.50 + path_value
        open_value = close_value - movement
        rows.append(
            {
                "step": step,
                "event": f"CANDLE_EVENT_{step}",
                "direction": "BUY",
                "movement_direction": "BUY" if movement > 0 else "SELL",
                "expected_open_norm": open_value,
                "expected_high_norm": max(open_value, close_value) + 0.01,
                "expected_low_norm": min(open_value, close_value) - 0.01,
                "expected_close_norm": close_value,
                "expected_delta_norm": movement,
                "expected_cumulative_delta_norm": path_value,
                "cumulative_scale_norm": 0.02,
            }
        )
    return rows


class _FakeAdapter:
    def study(self, image: Image.Image) -> SimpleNamespace:
        is_masked = image.getpixel((image.width - 1, 0)) == MASK
        tracks = VISIBLE_TRACKS if is_masked else VISIBLE_TRACKS + FUTURE_TRACKS
        contribution = {
            "artifact_loaded": True,
            "artifact_production_gate_passed": False,
            "forecast_available": True,
            "forecast_path": _forecast_path(),
            "model_version": "lstm_candle_sequence_v3",
            "artifact_path": "models/lstm_candle_sequence_v3.pt",
            "path_target_semantics": DIRECT_PATH_SEMANTICS,
            "trajectory_decoder_status": "AVAILABLE",
            "trajectory_mode": "BUY",
            "reason": "diagnostic test fixture",
        }
        summary = {
            "tracked_candles": tracks,
            "lstm_contribution": contribution,
            "detected_market": "TEST/PAIR",
            "detected_timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "control_direction": "BUY",
            "global_direction": "BUY",
            "local_direction": "BUY",
            "impulse_direction": "BUY",
            "major_trend_direction": "BUY",
            "trendlines_v3": [{"geometry_contract_accepted": True}],
            "trendline_geometry_contract_v3": {"status": "CONFIRMED"},
            "support_resistance_zones": [{"role": "support"}],
            "smart_money_context": {"dominant_side": "BUY", "buy_score": 0.8, "sell_score": 0.2},
            "behavior": {"current_state": "EXPANSION", "next_most_likely_state": "REST"},
            "market_study_v3": {"status": "AVAILABLE", "directional_read": "BUY"},
        }
        return SimpleNamespace(
            tracking_summary=summary,
            chart_region={"pixel_bbox": [0, 0, 120, 100]},
            overlay_image=image.copy(),
        )


def _factory(_: Path) -> _FakeAdapter:
    return _FakeAdapter()


def test_prediction_is_frozen_before_reveal_and_scores_fluctuating_geometry(tmp_path: Path) -> None:
    config = ReplayConfig(
        mask_ratio=0.50,
        min_visible_candles=5,
        min_future_candles=4,
        max_horizon_candles=4,
    )
    full = Image.new("RGB", (120, 100), (35, 70, 110))
    masked = mask_future_pixels(full, cut_x=60, mask_rgb=MASK)
    frozen = freeze_v3_prediction(
        masked,
        cut_x=60,
        market_study_root=tmp_path / "prediction",
        config=config,
        adapter_factory=_factory,
    )
    freeze_hash = frozen.freeze_sha256
    reveal = reveal_and_score_v3_prediction(
        full,
        frozen,
        market_study_root=tmp_path / "reveal",
        config=config,
        adapter_factory=_factory,
    )
    assert recompute_freeze_sha256(frozen) == freeze_hash
    assert [row["expected_delta_norm"] for row in frozen.forecast_path] == [0.05, -0.03, 0.06, 0.02]
    assert reveal.metrics["prediction_frozen_before_reveal"] is True
    assert reveal.metrics["future_pixels_passed_to_predictor"] is False
    assert reveal.metrics["actual_geometry_uses_color_labels"] is False
    assert reveal.metrics["terminal_direction_hit"] is True
    assert reveal.metrics["majority_direction_hit"] is True
    assert reveal.metrics["anchor_direction_accuracy"] == pytest.approx(1.0)
    assert reveal.metrics["candle_to_candle_fluctuation_accuracy"] == pytest.approx(1.0)
    assert reveal.metrics["turning_point_f1"] == pytest.approx(1.0)


def test_visual_evidence_contains_mask_prediction_reveal_and_comparison(tmp_path: Path) -> None:
    config = ReplayConfig(
        mask_ratio=0.50,
        min_visible_candles=5,
        min_future_candles=4,
        max_horizon_candles=4,
        evidence_width=800,
        evidence_panel_height=220,
    )
    full = Image.new("RGB", (120, 100), (25, 55, 90))
    masked = mask_future_pixels(full, cut_x=60, mask_rgb=MASK)
    frozen = freeze_v3_prediction(
        masked,
        cut_x=60,
        market_study_root=tmp_path / "prediction",
        config=config,
        adapter_factory=_factory,
    )
    reveal = reveal_and_score_v3_prediction(
        full,
        frozen,
        market_study_root=tmp_path / "reveal",
        config=config,
        adapter_factory=_factory,
    )
    sheet = render_evidence_sheet(
        source_label="fixture.png",
        category="UNLABELED",
        masked_image=masked,
        full_image=full,
        frozen=frozen,
        reveal=reveal,
        status="SCORED_DIAGNOSTIC_NO_EDGE",
        reason="fixture",
        config=config,
    )
    output = tmp_path / "evidence.png"
    sheet.save(output)
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert sheet.width == 800
    assert sheet.height > 400
    assert masked.getpixel((119, 50)) == MASK


def test_disk_reserve_refuses_a_write_that_would_cross_the_floor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    usage = shutil_usage = SimpleNamespace(total=100 * 1024**3, used=54 * 1024**3, free=46 * 1024**3)
    monkeypatch.setattr(
        "phoenixguard.study.v3_causal_path_replay.shutil.disk_usage",
        lambda _: usage,
    )
    assert ensure_disk_reserve(tmp_path, min_free_gb=45.0) == pytest.approx(46.0)
    with pytest.raises(DiskReserveError):
        ensure_disk_reserve(
            tmp_path,
            min_free_gb=45.0,
            anticipated_bytes=2 * 1024**3,
        )
    assert shutil_usage.free == 46 * 1024**3
