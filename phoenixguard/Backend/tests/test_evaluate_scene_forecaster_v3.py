from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

from Backend.tools.evaluate_scene_forecaster_v3 import (
    PAST_COVARIATE_FEATURES,
    REQUIRED_HORIZON,
    ForecastBatch,
    ForecastWindow,
    benchmark_windows,
    build_chronos_multivariate_inputs,
    build_independent_windows,
    chronos_multivariate_forecast,
    chronos_univariate_forecast,
    last_delta_forecast,
    load_held_out_sequences,
    make_report,
    persistence_forecast,
    score_forecast,
    write_report,
)


FloatArray = NDArray[np.floating[Any]]


def _features(length: int, *, future_marker: bool = False) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for index in range(length):
        marked = future_marker and index >= length - REQUIRED_HORIZON
        marker = 10_000.0 if marked else 0.0
        rows.append(
            {
                "relative_price_location": index / 100.0,
                "range_norm": marker + 0.01 + index / 10_000.0,
                "body_norm": marker + 0.2,
                "direction": "BUY" if index % 2 == 0 else "SELL",
                "direction_value": 1.0 if index % 2 == 0 else -1.0,
                "upper_wick_norm": marker + 0.1,
                "lower_wick_norm": marker + 0.2,
                "momentum_5": marker + (1.0 if index % 2 == 0 else -1.0),
                "parse_confidence": marker + 0.9,
            }
        )
    return rows


def _row(
    *,
    split: str,
    group: str,
    source: str,
    length: int,
    future_marker: bool = False,
) -> dict[str, Any]:
    return {
        "split": split,
        "independent_group": group,
        "source": source,
        "source_path": source,
        "features": _features(length, future_marker=future_marker),
    }


def _window(source: str, truth: list[float]) -> ForecastWindow:
    context = np.linspace(-0.47, 0.0, 48, dtype=np.float32)
    zeros = np.zeros(48, dtype=np.float32)
    return ForecastWindow(
        group_id=f"group-{source}",
        source_id=source,
        source_path=source,
        origin_index=48,
        context_close=context,
        context_range=zeros.copy(),
        context_signed_body=zeros.copy(),
        past_covariates={name: zeros.copy() for name in PAST_COVARIATE_FEATURES},
        truth_close=np.asarray(truth, dtype=np.float32),
    )


def test_loads_only_test_split_and_builds_one_causal_window_per_group(tmp_path: Path) -> None:
    path = tmp_path / "sequences.jsonl"
    rows = [
        _row(split="train", group="train-group", source="train.png", length=100),
        _row(split="test", group="group-a", source="short.png", length=60),
        _row(
            split="test",
            group="group-a",
            source="long.png",
            length=72,
            future_marker=True,
        ),
        _row(
            split="test",
            group="group-b",
            source="other.png",
            length=64,
            future_marker=True,
        ),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    held_out, counts = load_held_out_sequences(path)
    windows = build_independent_windows(held_out, context_length=48, horizon=12)

    assert counts == {
        "jsonl_rows": 4,
        "held_out_test_rows": 3,
        "excluded_non_test_rows": 1,
    }
    assert len(windows) == 2
    assert {window.group_id for window in windows} == {"group-a", "group-b"}
    assert {window.source_path for window in windows} == {"long.png", "other.png"}
    assert all(len(cast(FloatArray, window.context_close)) == 48 for window in windows)
    assert all(len(cast(FloatArray, window.truth_close)) == 12 for window in windows)

    model_inputs = build_chronos_multivariate_inputs(windows)
    for model_input in model_inputs:
        assert "future_covariates" not in model_input
        assert tuple(model_input["target"].shape) == (3, 48)
        assert set(model_input["past_covariates"]) == set(PAST_COVARIATE_FEATURES)
        assert float(np.max(np.abs(model_input["target"]))) < 10_000.0
        for covariate in model_input["past_covariates"].values():
            assert float(np.max(np.abs(covariate))) < 10_000.0


def test_window_builder_fails_closed_on_weak_context_or_non_test_input() -> None:
    test_row = _row(split="test", group="group-a", source="a.png", length=80)
    with pytest.raises(ValueError, match="at least 48"):
        build_independent_windows([test_row], context_length=47, horizon=12)
    with pytest.raises(ValueError, match="requires horizon=12"):
        build_independent_windows([test_row], context_length=48, horizon=11)

    train_row = _row(split="train", group="group-a", source="a.png", length=80)
    with pytest.raises(ValueError, match="test rows only"):
        build_independent_windows([train_row], context_length=48, horizon=12)


def test_deterministic_baselines_emit_all_twelve_steps() -> None:
    window = _window("source-a", [0.01] * 12)
    context_close = cast(FloatArray, window.context_close)
    context_close[-2:] = np.asarray([0.25, 0.30], dtype=np.float32)

    persistence = persistence_forecast([window])
    last_delta = last_delta_forecast([window])

    persistence_point = cast(FloatArray, persistence.point)
    last_delta_point = cast(FloatArray, last_delta.point)
    assert persistence_point.shape == (1, 12)
    assert np.allclose(persistence_point, 0.30)
    assert last_delta_point.shape == (1, 12)
    assert np.allclose(last_delta_point[0], 0.30 + 0.05 * np.arange(1, 13))


class _FakeChronosPipeline:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def predict_quantiles(
        self,
        **kwargs: Any,
    ) -> tuple[list[FloatArray], list[FloatArray]]:
        self.calls.append(kwargs)
        outputs: list[FloatArray] = []
        medians: list[FloatArray] = []
        for model_input in cast(list[Any], kwargs["inputs"]):
            target: Any = (
                cast(dict[str, Any], model_input)["target"]
                if isinstance(model_input, dict)
                else model_input
            )
            target_array: FloatArray = np.asarray(target, dtype=np.float32)
            if target_array.ndim == 1:
                target_array = target_array[None, :]
            variates = target_array.shape[0]
            output = np.zeros((variates, 12, 3), dtype=np.float32)
            for variate in range(variates):
                median = target_array[variate, -1] + 0.01 * np.arange(1, 13)
                output[variate, :, 0] = median - 0.05
                output[variate, :, 1] = median
                output[variate, :, 2] = median + 0.05
            outputs.append(output)
            medians.append(output[:, :, 1])
        return outputs, medians


def test_chronos_adapters_use_native_univariate_and_past_only_multivariate_inputs() -> None:
    windows = [_window("source-a", [0.01] * 12), _window("source-b", [-0.01] * 12)]
    pipeline = _FakeChronosPipeline()

    univariate = chronos_univariate_forecast(
        pipeline, windows, context_length=48, batch_size=2
    )
    multivariate = chronos_multivariate_forecast(
        pipeline, windows, context_length=48, batch_size=2
    )

    univariate_point = cast(FloatArray, univariate.point)
    multivariate_point = cast(FloatArray, multivariate.point)
    univariate_p10 = cast(FloatArray, univariate.p10)
    univariate_p90 = cast(FloatArray, univariate.p90)
    assert univariate_point.shape == (2, 12)
    assert multivariate_point.shape == (2, 12)
    assert np.all(univariate_p10 <= univariate_point)
    assert np.all(univariate_point <= univariate_p90)
    assert pipeline.calls[0]["quantile_levels"] == [0.1, 0.5, 0.9]
    assert all(np.asarray(item).shape == (48,) for item in pipeline.calls[0]["inputs"])
    for item in pipeline.calls[1]["inputs"]:
        assert set(item) == {"target", "past_covariates"}
        assert np.asarray(item["target"]).shape == (3, 48)
        assert set(item["past_covariates"]) == set(PAST_COVARIATE_FEATURES)


def test_metrics_are_per_event_and_perfect_path_scores_perfectly() -> None:
    truth_a = [0.1, -0.1, 0.2, -0.2, 0.3, -0.3, 0.4, -0.4, 0.5, -0.5, 0.6, -0.6]
    truth_b = [-value for value in truth_a]
    windows = [_window("source-a", truth_a), _window("source-b", truth_b)]
    truth_rows = [cast(FloatArray, window.truth_close) for window in windows]
    point = np.asarray(truth_rows, dtype=np.float64)
    forecast = ForecastBatch(
        point=point,
        p10=point - 0.01,
        p90=point + 0.01,
        total_latency_ms=4.0,
    )

    metrics = score_forecast(windows, forecast)

    assert metrics["endpoint_balanced_accuracy"] == 1.0
    assert metrics["event_balanced_accuracy"] == 1.0
    assert metrics["path_mae"] == 0.0
    assert metrics["turning_point"]["f1"] == 1.0
    assert metrics["p10_p90_marginal_coverage"] == 1.0
    assert metrics["inference_latency_ms"]["per_sample"] == 2.0
    assert set(metrics["per_event"]) == {str(index) for index in range(1, 13)}
    assert all(item["balanced_accuracy"] == 1.0 for item in metrics["per_event"].values())


def test_failed_chronos_is_reported_honestly_and_report_is_non_production(tmp_path: Path) -> None:
    rows = [_row(split="test", group="group-a", source="a.png", length=64)]
    windows = build_independent_windows(rows, context_length=48, horizon=12)
    results = benchmark_windows(
        windows,
        pipeline=None,
        pipeline_error="ImportError: unavailable",
        context_length=48,
    )
    report = make_report(
        data_path=tmp_path / "source.jsonl",
        model_path=tmp_path / "model",
        load_counts={
            "jsonl_rows": 1,
            "held_out_test_rows": 1,
            "excluded_non_test_rows": 0,
        },
        held_out_rows=rows,
        windows=windows,
        results=results,
        model_loading={"status": "failed", "error": "ImportError: unavailable"},
        context_length=48,
        horizon=12,
        batch_size=1,
        hold_threshold=0.0,
    )
    report_path = tmp_path / "reports" / "benchmark.json"
    write_report(report, report_path)
    loaded = json.loads(report_path.read_text(encoding="utf-8"))

    assert results["persistence"]["status"] == "ok"
    assert results["last_delta"]["status"] == "ok"
    assert results["chronos2_univariate_close"]["status"] == "failed"
    assert results["chronos2_multivariate_scene"]["error"] == "ImportError: unavailable"
    assert loaded["production_ready"] is False
    assert loaded["production_authorized"] is False
    assert loaded["protocol"]["future_features_used"] is False
    assert loaded["protocol"]["future_covariates_used"] is False
    assert loaded["data"]["evaluated_independent_group_count"] == 1
