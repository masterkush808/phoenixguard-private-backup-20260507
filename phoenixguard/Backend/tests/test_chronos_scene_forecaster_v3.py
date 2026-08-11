from __future__ import annotations

import copy
import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, cast

import pytest

from phoenixguard.decision import chronos_scene_forecaster_v3 as provider
from phoenixguard.decision.scene_forecast_features_v3 import (
    extract_scene_forecast_features_v3,
)


class _FakeNumpy:
    float32 = float

    @staticmethod
    def asarray(value: Any, dtype: object = None) -> Any:
        del dtype
        return value


class _FakePipeline:
    quantiles = [0.1, 0.5, 0.9]

    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.calls = 0
        self.last_inputs: list[dict[str, Any]] = []
        self.last_kwargs: dict[str, Any] = {}

    def predict(self, inputs: list[dict[str, Any]], **kwargs: Any) -> list[Any]:
        self.calls += 1
        self.last_inputs = inputs
        self.last_kwargs = kwargs
        close_cycle = [
            0.18,
            -0.08,
            0.24,
            0.02,
            0.31,
            0.12,
            0.36,
            0.19,
            0.42,
            0.27,
            0.47,
            0.34,
        ]
        close = [
            value + 0.01 * cycle
            for cycle in range(6)
            for value in close_cycle
        ]
        open_rows = [0.0, *close[:-1]]
        high = [max(left, right) + 0.16 for left, right in zip(open_rows, close)]
        low = [min(left, right) - 0.16 for left, right in zip(open_rows, close)]
        targets = [open_rows, high, low, close]
        output: list[list[list[float]]] = []
        for target in targets:
            p10 = [value - 0.06 for value in target]
            p50 = list(target)
            p90 = [value + 0.06 for value in target]
            output.append([p10, p50, p90])
        if self.invalid:
            output[3][0][3] = output[3][2][3] + 1.0
        return [output]


def _scene_features() -> dict[str, Any]:
    candles: list[dict[str, Any]] = []
    price = 0.50
    for index in range(32):
        movement = (0.004 + 0.0002 * (index % 3)) * (1 if index % 4 != 1 else -1)
        open_price = price
        close_price = price + movement
        candles.append(
            {
                "open": open_price,
                "high": max(open_price, close_price) + 0.002,
                "low": min(open_price, close_price) - 0.002,
                "close": close_price,
                "direction": "BUY" if close_price > open_price else "SELL",
                "timestamp": 1_700_000_000 + index * 60,
                "is_closed": True,
                "parse_confidence": 0.96,
                "bbox": [20 + index * 8, 100, 24 + index * 8, 140],
            }
        )
        price = close_price
    return extract_scene_forecast_features_v3(
        candles=candles,
        projection={
            "direction": "BUY",
            "confidence": 0.71,
            "zones": [
                {"direction": "BUY", "confidence": 0.78},
                {"direction": "SELL", "confidence": 0.44},
            ],
        },
        candle_statistics={
            "sample_size": 32,
            "buy_count": 20,
            "sell_count": 12,
            "buy_ratio": 0.625,
            "sell_ratio": 0.375,
        },
        behavior_payload={
            "current_state": "PULLBACK",
            "state_confidence": 0.68,
            "trend": {
                "slope_global": 0.35,
                "slope_local": -0.12,
                "slope_current": 0.08,
                "strength": 0.64,
            },
        },
        decision_kernel={
            "dominant_side": "BUY",
            "belief_buy": 0.62,
            "belief_sell": 0.23,
            "belief_hold": 0.15,
            "belief_uncertainty": 0.31,
            "conflict_score": 0.18,
        },
        smart_money_context={
            "dominant_side": "BUY",
            "confidence": 0.66,
            "order_blocks": [{"side": "BUY", "confidence": 0.72}],
        },
        support_resistance_context={
            "dominant_side": "BUY",
            "buy_structure_score": 0.69,
            "sell_structure_score": 0.31,
        },
        trend_slopes={"global": 0.35, "local": -0.12, "current": 0.08},
        trend_directions={"global": "BUY", "local": "SELL", "current": "BUY"},
        timeframe="M1",
        pair="NZDUSD_OTC",
    )


def _anchor() -> dict[str, Any]:
    return {
        "x_norm": 0.55,
        "y_norm": 0.50,
        "price_norm": 0.50,
        "target_scale_norm": 0.01,
        "event_step_x_norm": 0.006,
        "verified_latest_close": True,
        "source": "TRACKER_LATEST_CLOSE",
    }


@pytest.fixture(autouse=True)
def reset_provider_fixture() -> Iterator[None]:
    provider.reset_provider_state_for_tests()
    yield
    provider.reset_provider_state_for_tests()


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    invalid: bool = False,
) -> tuple[_FakePipeline, dict[str, int]]:
    pipeline = _FakePipeline(invalid=invalid)
    loads = {"count": 0}

    def load() -> Any:
        loads["count"] += 1
        runtime_type = cast(Callable[..., Any], getattr(provider, "_ChronosRuntime"))
        return runtime_type(
            pipeline=pipeline,
            numpy=_FakeNumpy(),
            torch=object(),
            model_dir=provider.DEFAULT_MODEL_DIR,
            cpu_threads=2,
        )

    monkeypatch.setattr(provider, "_load_local_runtime", load)
    return pipeline, loads


def test_local_chronos_boundary_is_lazy_singleton_cached_and_past_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pipeline, loads = _install_fake_runtime(monkeypatch)
    scene = _scene_features()
    missing_metrics = tmp_path / "missing-metrics.json"

    first = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=41,
        metrics_path=missing_metrics,
    )
    second = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=41,
        metrics_path=missing_metrics,
    )

    assert loads["count"] == 1
    assert pipeline.calls == 1
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(first["line_points"]) == 73
    assert len(first["forecast_candles"]) == 72
    assert len(first["forecast_scenarios"]) == 3
    assert first["line_points"][0] == [0.55, 0.50]
    assert first["zero_shot"] is True
    assert first["shadow_mode"] is True
    assert first["production_authorized"] is False
    assert first["trade_authorized"] is False
    assert first["probability_calibrated"] is False
    assert first["forecast_band_points"] == []
    assert first["model"]["local_only"] is True
    assert first["model"]["network_allowed"] is False
    assert first["model"]["inference_mode"] == "ZERO_SHOT_SHADOW"

    model_input = pipeline.last_inputs[0]
    assert len(model_input["target"]) == 4
    assert len(model_input["target"][0]) == first["scene_feature_contract"]["history_length"]
    assert model_input["past_covariates"]
    assert "future_covariates" not in model_input
    assert first["scene_feature_contract"]["target_names"] == [
        "open_offset",
        "high_offset",
        "low_offset",
        "close_offset",
    ]
    assert first["scene_feature_contract"]["future_covariates_used"] is False
    assert pipeline.last_kwargs["prediction_length"] == 72
    assert pipeline.last_kwargs["cross_learning"] is False


def test_explicit_walk_forward_metrics_gate_enables_calibrated_envelope_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_runtime(monkeypatch)
    scene = _scene_features()
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "schema_version": provider.METRICS_SCHEMA_VERSION,
                "model_id": provider.MODEL_ID,
                "scene_schema_fingerprint": scene["schema_fingerprint"],
                "horizon_steps": 72,
                "walk_forward_validated": True,
                "leakage_audit_passed": True,
                "production_gate_passed": True,
                "path_calibration_gate_passed": True,
                "metrics_revision": "test-gate-1",
            }
        ),
        encoding="utf-8",
    )

    result = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=88,
        metrics_path=metrics_path,
    )

    assert result["provider_status"] == "AVAILABLE"
    assert result["metrics_gate"]["production_gate_passed"] is True
    assert result["metrics_gate"]["path_calibration_gate_passed"] is True
    assert result["production_authorized"] is True
    assert result["shadow_mode"] is False
    assert result["probability_calibrated"] is True
    assert result["interval"]["status"] == "READY"
    assert result["forecast_band_points"]
    # A provider-level metrics gate still cannot grant execution permission.
    assert result["trade_authorized"] is False
    assert result["selective_authorized"] is False
    assert result["contribution"] == 0.0


def test_unavailable_model_returns_complete_non_lstm_diagnostic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    loads = {"count": 0}

    def unavailable() -> Any:
        loads["count"] += 1
        raise ImportError("chronos intentionally unavailable")

    monkeypatch.setattr(provider, "_load_local_runtime", unavailable)
    scene = _scene_features()
    first = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=19,
        metrics_path=tmp_path / "missing.json",
    )
    second = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=19,
        metrics_path=tmp_path / "missing.json",
    )

    assert loads["count"] == 1
    assert first["provider"] == "SCENE_STATISTICAL_FALLBACK_V3"
    assert first["requested_provider"] == "CHRONOS_2_LOCAL"
    assert first["provider_status"] == "UNAVAILABLE_FALLBACK"
    assert first["forecast_available"] is True
    assert len(first["line_points"]) == 73
    assert len(first["forecast_candles"]) == 72
    assert len(first["forecast_scenarios"]) == 3
    assert first["fallback"]["active"] is True
    assert first["fallback"]["method"] == "RESIDUAL_LIBRARY_STATISTICAL"
    assert "chronos intentionally unavailable" in first["fallback"]["reason"]
    assert first["fallback"]["calibrated"] is False
    assert first["fallback"]["trade_authorized"] is False
    assert first["zero_shot"] is False
    assert first["probability_calibrated"] is False
    assert first["production_authorized"] is False
    assert first["forecast_band_points"] == []
    expected = copy.deepcopy(first)
    expected["cache_hit"] = True
    assert second == expected


def test_unavailable_fallback_stays_complete_at_chart_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def unavailable() -> Any:
        raise RuntimeError("offline")

    monkeypatch.setattr(provider, "_load_local_runtime", unavailable)
    anchor = _anchor()
    anchor["y_norm"] = 0.005
    anchor["price_norm"] = 0.995
    anchor["target_scale_norm"] = 1.0

    result = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=_scene_features(),
        anchor=anchor,
        deterministic_seed=93,
        metrics_path=tmp_path / "missing.json",
    )

    assert result["fallback"]["active"] is True
    assert len(result["line_points"]) == 73
    assert len(result["forecast_candles"]) == 72
    assert any(
        not 0.0 <= value <= 1.0
        for target in result["direct_quantiles"].values()
        for quantile in target.values()
        for value in quantile
    )
    assert result["viewport_fit_applied"] is True
    assert 0.0 < result["geometry_gain"] < 1.0
    assert result["geometry_transform"]["pointwise_clipping_applied"] is False
    assert result["trajectory_sampler"]["boundary_clipped_values"] == 0
    for scenario in result["forecast_scenarios"]:
        points = scenario["line_points"]
        assert points[0] == [0.55, 0.005]
        assert all(0.0 <= point[1] <= 1.0 for point in points)
        assert len({round(point[1], 12) for point in points[-8:]}) > 4
        assert all(
            0.0 <= float(candle[field]) <= 1.0
            for candle in scenario["forecast_candles"]
            for field in (
                "open_y_norm",
                "high_y_norm",
                "low_y_norm",
                "close_y_norm",
            )
        )


def test_latency_sensitive_call_skips_foundation_loader_and_keeps_seventy_two_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbidden() -> Any:
        raise AssertionError("the foundation loader must not run")

    monkeypatch.setattr(provider, "_load_local_runtime", forbidden)
    result = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=_scene_features(),
        anchor=_anchor(),
        deterministic_seed=17,
        metrics_path=tmp_path / "missing.json",
        allow_foundation_model=False,
    )

    assert result["provider"] == "SCENE_STATISTICAL_FALLBACK_V3"
    assert result["requested_provider"] == "CHRONOS_2_LOCAL"
    assert result["provider_status"] == "FOUNDATION_DISABLED_FALLBACK"
    assert result["fallback"]["active"] is True
    assert len(result["forecast_candles"]) == 72
    assert math.isclose(sum(result["raw_side_probabilities"].values()), 1.0)
    assert result["side_probabilities"] == {}


def test_invalid_foundation_quantiles_fail_over_without_promoting_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pipeline, _loads = _install_fake_runtime(monkeypatch, invalid=True)

    result = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=_scene_features(),
        anchor=_anchor(),
        deterministic_seed=7,
        metrics_path=tmp_path / "missing.json",
    )

    assert pipeline.calls == 1
    assert result["provider_status"] == "INVALID_MODEL_OUTPUT_FALLBACK"
    assert result["model"]["loaded"] is True
    assert result["model"]["used_for_forecast"] is False
    assert result["fallback"]["active"] is True
    assert "quantiles cross" in result["fallback"]["reason"]
    assert result["production_authorized"] is False
    assert result["probability_calibrated"] is False
    assert len(result["forecast_candles"]) == 72


def test_residual_sampler_is_deterministic_and_seeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def unavailable() -> Any:
        raise RuntimeError("offline")

    monkeypatch.setattr(provider, "_load_local_runtime", unavailable)
    scene = _scene_features()
    first = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=123,
        metrics_path=tmp_path / "missing.json",
    )
    provider.reset_provider_state_for_tests()
    monkeypatch.setattr(provider, "_load_local_runtime", unavailable)
    replay = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=123,
        metrics_path=tmp_path / "missing.json",
    )
    provider.reset_provider_state_for_tests()
    monkeypatch.setattr(provider, "_load_local_runtime", unavailable)
    changed = provider.build_chronos_scene_forecast_contribution_v3(
        scene_features=scene,
        anchor=_anchor(),
        deterministic_seed=124,
        metrics_path=tmp_path / "missing.json",
    )

    assert replay == first
    assert changed["trajectory_sampler"]["seed"] == 124
    assert (
        changed["trajectory_sampler"]["sample_fingerprint"]
        != first["trajectory_sampler"]["sample_fingerprint"]
    )
    assert changed["forecast_scenarios"] != first["forecast_scenarios"]


def test_provider_rejects_non_scene_contract_before_loading_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads = {"count": 0}

    def load() -> Any:
        loads["count"] += 1
        raise AssertionError("loader must not run")

    monkeypatch.setattr(provider, "_load_local_runtime", load)
    malformed = _scene_features()
    malformed["schema_version"] = "NOT_THE_SCENE_CONTRACT"

    with pytest.raises(ValueError, match="PG_SCENE_FORECAST_FEATURES_V3"):
        provider.build_chronos_scene_forecast_contribution_v3(
            scene_features=malformed,
            anchor=_anchor(),
        )
    assert loads["count"] == 0
