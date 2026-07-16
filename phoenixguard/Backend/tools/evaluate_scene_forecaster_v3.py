from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_PATH = PROJECT_ROOT / "data_splits" / "lstm_raw_candle_sequences_v3.jsonl"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "foundation" / "chronos-2-small"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "reports" / "scene_forecaster_v3_chronos2_zero_shot.json"

REPORT_SCHEMA = "PHOENIXGUARD_SCENE_FORECASTER_V3_ZERO_SHOT_BENCHMARK_V1"
REQUIRED_HORIZON = 12
MINIMUM_CONTEXT_LENGTH = 48
DEFAULT_CONTEXT_LENGTH = 64
QUANTILE_LEVELS = (0.1, 0.5, 0.9)
TARGET_FEATURES = ("relative_price_location", "range_norm", "signed_body")
PAST_COVARIATE_FEATURES = (
    "upper_wick_norm",
    "lower_wick_norm",
    "momentum_5",
    "parse_confidence",
)
LABELS = ("SELL", "HOLD", "BUY")

Float32Array = NDArray[np.float32]
Float64Array = NDArray[np.float64]
StringArray = NDArray[np.str_]


class ChronosPipeline(Protocol):
    def predict_quantiles(
        self,
        *,
        inputs: Sequence[object],
        prediction_length: int,
        quantile_levels: list[float],
        batch_size: int,
        context_length: int,
        cross_learning: bool,
    ) -> tuple[Sequence[object], object]: ...


class ChronosPipelineLoader(Protocol):
    def from_pretrained(
        self,
        pretrained_model_name_or_path: str,
        **kwargs: object,
    ) -> ChronosPipeline: ...


@dataclass(frozen=True)
class ForecastWindow:
    """One statistically independent, strictly causal forecast example."""

    group_id: str
    source_id: str
    source_path: str
    origin_index: int
    context_close: Float32Array
    context_range: Float32Array
    context_signed_body: Float32Array
    past_covariates: Mapping[str, Float32Array]
    truth_close: Float32Array

    @property
    def anchor_close(self) -> float:
        return float(self.context_close[-1])


@dataclass(frozen=True)
class ForecastBatch:
    point: Float64Array
    p10: Float64Array
    p90: Float64Array
    total_latency_ms: float


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _feature_series(rows: Sequence[Mapping[str, Any]], key: str) -> Float32Array:
    return np.asarray([_finite_float(row.get(key)) for row in rows], dtype=np.float32)


def _signed_body_series(rows: Sequence[Mapping[str, Any]]) -> Float32Array:
    values: list[float] = []
    for row in rows:
        direction = _finite_float(row.get("direction_value"))
        if direction == 0.0:
            label = str(row.get("direction") or "").strip().upper()
            direction = 1.0 if label == "BUY" else -1.0 if label == "SELL" else 0.0
        values.append(_finite_float(row.get("body_norm")) * max(-1.0, min(1.0, direction)))
    return np.asarray(values, dtype=np.float32)


def load_held_out_sequences(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load only explicit test-split sequences; all other splits are excluded."""

    if not path.is_file():
        raise FileNotFoundError(f"Sequence cache does not exist: {path}")

    held_out: list[dict[str, Any]] = []
    total_rows = 0
    excluded_non_test_rows = 0
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        total_rows += 1
        payload = json.loads(raw_line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"JSONL row {line_number} must be an object")
        row: dict[str, Any] = dict(cast(Mapping[str, object], payload))
        if str(row.get("split") or "").strip().lower() != "test":
            excluded_non_test_rows += 1
            continue
        group_id = str(row.get("independent_group") or "").strip()
        features = row.get("features")
        if not group_id:
            raise ValueError(f"Held-out row {line_number} has no independent_group")
        if not isinstance(features, Sequence) or isinstance(features, (str, bytes, bytearray)):
            raise ValueError(f"Held-out row {line_number} has invalid features")
        row["independent_group"] = group_id
        row["features"] = [
            dict(cast(Mapping[str, object], item))
            for item in cast(Sequence[object], features)
            if isinstance(item, Mapping)
        ]
        held_out.append(row)

    if not held_out:
        raise ValueError("No explicit held-out test rows were found")

    return held_out, {
        "jsonl_rows": total_rows,
        "held_out_test_rows": len(held_out),
        "excluded_non_test_rows": excluded_non_test_rows,
    }


def _source_id(row: Mapping[str, Any]) -> str:
    value = str(row.get("source_path") or row.get("source") or "").strip()
    if not value:
        raise ValueError("Held-out sequence has no source identity")
    return value.casefold()


def _make_window(
    row: Mapping[str, Any],
    *,
    context_length: int,
    horizon: int,
) -> ForecastWindow:
    features = list(row["features"])
    origin = len(features) - horizon
    context_rows = features[origin - context_length : origin]
    future_rows = features[origin : origin + horizon]
    context_mappings = [dict(item) for item in context_rows]
    future_mappings = [dict(item) for item in future_rows]

    # Deliberately retain only future close truth. No future candle attributes can
    # subsequently reach either Chronos input adapter.
    truth_close = _feature_series(future_mappings, "relative_price_location")
    past_covariates = {
        name: _feature_series(context_mappings, name) for name in PAST_COVARIATE_FEATURES
    }
    source_path = str(row.get("source_path") or row.get("source") or "")
    return ForecastWindow(
        group_id=str(row["independent_group"]),
        source_id=_source_id(row),
        source_path=source_path,
        origin_index=origin,
        context_close=_feature_series(context_mappings, "relative_price_location"),
        context_range=_feature_series(context_mappings, "range_norm"),
        context_signed_body=_signed_body_series(context_mappings),
        past_covariates=past_covariates,
        truth_close=truth_close,
    )


def build_independent_windows(
    held_out_rows: Sequence[Mapping[str, Any]],
    *,
    context_length: int = DEFAULT_CONTEXT_LENGTH,
    horizon: int = REQUIRED_HORIZON,
    max_windows: int | None = None,
) -> list[ForecastWindow]:
    """Select at most one latest-origin source window per independent group."""

    if context_length < MINIMUM_CONTEXT_LENGTH:
        raise ValueError(f"context_length must be at least {MINIMUM_CONTEXT_LENGTH}")
    if horizon != REQUIRED_HORIZON:
        raise ValueError(f"This V3 benchmark requires horizon={REQUIRED_HORIZON}")
    if max_windows is not None and max_windows < 1:
        raise ValueError("max_windows must be positive when provided")

    candidates: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    required_length = context_length + horizon
    for row in held_out_rows:
        if str(row.get("split") or "").strip().lower() != "test":
            raise ValueError("build_independent_windows accepts held-out test rows only")
        group_id = str(row.get("independent_group") or "").strip()
        features = row.get("features")
        if (
            group_id
            and isinstance(features, Sequence)
            and not isinstance(features, (str, bytes, bytearray))
            and len(cast(Sequence[object], features)) >= required_length
        ):
            candidates[group_id].append(row)

    selected: list[ForecastWindow] = []
    for group_id in sorted(candidates):
        # Longest history first; source identity makes ties deterministic.
        source_row = sorted(
            candidates[group_id],
            key=lambda row: (-len(row["features"]), _source_id(row)),
        )[0]
        selected.append(
            _make_window(source_row, context_length=context_length, horizon=horizon)
        )
        if max_windows is not None and len(selected) >= max_windows:
            break

    if not selected:
        raise ValueError(
            f"No held-out group has at least {context_length + horizon} causal candles"
        )
    if len({window.group_id for window in selected}) != len(selected):
        raise AssertionError("Independent-group uniqueness invariant failed")
    if len({window.source_id for window in selected}) != len(selected):
        raise ValueError("Selected independent groups unexpectedly share a source")
    return selected


def persistence_forecast(windows: Sequence[ForecastWindow]) -> ForecastBatch:
    started = time.perf_counter()
    point = np.asarray(
        [np.repeat(window.anchor_close, REQUIRED_HORIZON) for window in windows],
        dtype=np.float64,
    )
    latency = (time.perf_counter() - started) * 1_000.0
    return ForecastBatch(point=point, p10=point.copy(), p90=point.copy(), total_latency_ms=latency)


def last_delta_forecast(windows: Sequence[ForecastWindow]) -> ForecastBatch:
    started = time.perf_counter()
    paths: list[Float64Array] = []
    steps = np.arange(1, REQUIRED_HORIZON + 1, dtype=np.float64)
    for window in windows:
        last_delta = float(window.context_close[-1] - window.context_close[-2])
        paths.append(window.anchor_close + last_delta * steps)
    point = np.asarray(paths, dtype=np.float64)
    latency = (time.perf_counter() - started) * 1_000.0
    return ForecastBatch(point=point, p10=point.copy(), p90=point.copy(), total_latency_ms=latency)


def build_chronos_univariate_inputs(
    windows: Sequence[ForecastWindow],
) -> list[Float32Array]:
    return [np.asarray(window.context_close, dtype=np.float32) for window in windows]


def build_chronos_multivariate_inputs(windows: Sequence[ForecastWindow]) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for window in windows:
        inputs.append(
            {
                "target": np.stack(
                    (
                        window.context_close,
                        window.context_range,
                        window.context_signed_body,
                    ),
                    axis=0,
                ).astype(np.float32, copy=False),
                "past_covariates": {
                    name: np.asarray(window.past_covariates[name], dtype=np.float32)
                    for name in PAST_COVARIATE_FEATURES
                },
            }
        )
    return inputs


def _as_numpy(value: Any) -> Float64Array:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value, dtype=np.float64)


def _chronos_forecast(
    pipeline: ChronosPipeline,
    inputs: Sequence[object],
    *,
    target_index: int,
    expected_variates: int,
    context_length: int,
    batch_size: int,
) -> ForecastBatch:
    started = time.perf_counter()
    quantiles, _ = pipeline.predict_quantiles(
        inputs=inputs,
        prediction_length=REQUIRED_HORIZON,
        quantile_levels=list(QUANTILE_LEVELS),
        batch_size=batch_size,
        context_length=context_length,
        cross_learning=False,
    )
    latency = (time.perf_counter() - started) * 1_000.0
    if len(quantiles) != len(inputs):
        raise ValueError(
            f"Chronos returned {len(quantiles)} predictions for {len(inputs)} inputs"
        )

    p10: list[Float64Array] = []
    point: list[Float64Array] = []
    p90: list[Float64Array] = []
    expected_shape = (expected_variates, REQUIRED_HORIZON, len(QUANTILE_LEVELS))
    for prediction in quantiles:
        array = _as_numpy(prediction)
        if array.shape != expected_shape:
            raise ValueError(
                f"Chronos prediction shape {array.shape} does not match {expected_shape}"
            )
        p10.append(array[target_index, :, 0])
        point.append(array[target_index, :, 1])
        p90.append(array[target_index, :, 2])

    return ForecastBatch(
        point=np.asarray(point, dtype=np.float64),
        p10=np.asarray(p10, dtype=np.float64),
        p90=np.asarray(p90, dtype=np.float64),
        total_latency_ms=latency,
    )


def chronos_univariate_forecast(
    pipeline: ChronosPipeline,
    windows: Sequence[ForecastWindow],
    *,
    context_length: int,
    batch_size: int,
) -> ForecastBatch:
    return _chronos_forecast(
        pipeline,
        build_chronos_univariate_inputs(windows),
        target_index=0,
        expected_variates=1,
        context_length=context_length,
        batch_size=batch_size,
    )


def chronos_multivariate_forecast(
    pipeline: ChronosPipeline,
    windows: Sequence[ForecastWindow],
    *,
    context_length: int,
    batch_size: int,
) -> ForecastBatch:
    return _chronos_forecast(
        pipeline,
        build_chronos_multivariate_inputs(windows),
        target_index=0,
        expected_variates=3,
        context_length=context_length,
        batch_size=batch_size,
    )


def _movement_labels(
    deltas: Float64Array,
    hold_threshold: float,
) -> StringArray:
    labels = np.full(deltas.shape, "HOLD", dtype="<U4")
    labels[deltas > hold_threshold] = "BUY"
    labels[deltas < -hold_threshold] = "SELL"
    return labels


def _balanced_accuracy(
    truth: StringArray,
    prediction: StringArray,
) -> tuple[float, dict[str, int]]:
    support = {label: int(np.sum(truth == label)) for label in LABELS}
    recalls = [
        float(
            cast(
                np.float64,
                np.mean(prediction[truth == label] == label),
            )
        )
        for label in LABELS
        if support[label] > 0
    ]
    return (float(np.mean(recalls)) if recalls else 0.0), support


def _turning_point_metrics(
    truth_close: Float64Array,
    point: Float64Array,
    anchors: Float64Array,
    hold_threshold: float,
) -> dict[str, float | int]:
    truth_delta = np.diff(np.concatenate((anchors[:, None], truth_close), axis=1), axis=1)
    point_delta = np.diff(np.concatenate((anchors[:, None], point), axis=1), axis=1)
    truth_sign = np.sign(truth_delta)
    point_sign = np.sign(point_delta)
    truth_sign[np.abs(truth_delta) <= hold_threshold] = 0.0
    point_sign[np.abs(point_delta) <= hold_threshold] = 0.0
    truth_turn = (truth_sign[:, 1:] * truth_sign[:, :-1]) < 0.0
    point_turn = (point_sign[:, 1:] * point_sign[:, :-1]) < 0.0
    true_positive = int(np.sum(truth_turn & point_turn))
    false_positive = int(np.sum(~truth_turn & point_turn))
    false_negative = int(np.sum(truth_turn & ~point_turn))
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "f1": round(f1, 6),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "truth_turns": int(np.sum(truth_turn)),
        "predicted_turns": int(np.sum(point_turn)),
    }


def score_forecast(
    windows: Sequence[ForecastWindow],
    forecast: ForecastBatch,
    *,
    hold_threshold: float = 0.0,
) -> dict[str, Any]:
    if hold_threshold < 0.0:
        raise ValueError("hold_threshold cannot be negative")
    truth_close = np.asarray([window.truth_close for window in windows], dtype=np.float64)
    anchors = np.asarray([window.anchor_close for window in windows], dtype=np.float64)
    expected_shape = truth_close.shape
    for name, values in (
        ("point", forecast.point),
        ("p10", forecast.p10),
        ("p90", forecast.p90),
    ):
        if values.shape != expected_shape:
            raise ValueError(f"{name} shape {values.shape} does not match truth {expected_shape}")
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{name} contains non-finite values")

    truth_event_delta = np.diff(
        np.concatenate((anchors[:, None], truth_close), axis=1), axis=1
    )
    predicted_event_delta = np.diff(
        np.concatenate((anchors[:, None], forecast.point), axis=1), axis=1
    )
    truth_event_labels = _movement_labels(truth_event_delta, hold_threshold)
    predicted_event_labels = _movement_labels(predicted_event_delta, hold_threshold)
    event_balanced, event_support = _balanced_accuracy(
        truth_event_labels.ravel(), predicted_event_labels.ravel()
    )

    per_event: dict[str, dict[str, Any]] = {}
    for event_index in range(REQUIRED_HORIZON):
        balanced, support = _balanced_accuracy(
            truth_event_labels[:, event_index], predicted_event_labels[:, event_index]
        )
        per_event[str(event_index + 1)] = {
            "balanced_accuracy": round(balanced, 6),
            "truth_support": support,
        }

    endpoint_truth = _movement_labels(truth_close[:, -1] - anchors, hold_threshold)
    endpoint_prediction = _movement_labels(forecast.point[:, -1] - anchors, hold_threshold)
    endpoint_balanced, endpoint_support = _balanced_accuracy(
        endpoint_truth, endpoint_prediction
    )
    lower = np.minimum(forecast.p10, forecast.p90)
    upper = np.maximum(forecast.p10, forecast.p90)
    coverage = float(np.mean((truth_close >= lower) & (truth_close <= upper)))
    latency_per_sample = forecast.total_latency_ms / len(windows)

    return {
        "endpoint_balanced_accuracy": round(endpoint_balanced, 6),
        "endpoint_truth_support": endpoint_support,
        "event_balanced_accuracy": round(event_balanced, 6),
        "event_truth_support": event_support,
        "per_event": per_event,
        "path_mae": round(float(np.mean(np.abs(forecast.point - truth_close))), 8),
        "turning_point": _turning_point_metrics(
            truth_close, forecast.point, anchors, hold_threshold
        ),
        "p10_p90_marginal_coverage": round(coverage, 6),
        "inference_latency_ms": {
            "total": round(forecast.total_latency_ms, 3),
            "per_sample": round(latency_per_sample, 3),
        },
        "sample_count": len(windows),
        "source_count": len({window.source_id for window in windows}),
        "independent_group_count": len({window.group_id for window in windows}),
    }


def _successful_result(
    name: str,
    windows: Sequence[ForecastWindow],
    forecast: ForecastBatch,
    *,
    hold_threshold: float,
    zero_shot: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "ok",
        "zero_shot": zero_shot,
        "production_ready": False,
        "metrics": score_forecast(windows, forecast, hold_threshold=hold_threshold),
    }


def _failed_result(name: str, error: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed",
        "zero_shot": True,
        "production_ready": False,
        "error": error,
        "metrics": None,
    }


def benchmark_windows(
    windows: Sequence[ForecastWindow],
    *,
    pipeline: ChronosPipeline | None,
    pipeline_error: str = "",
    context_length: int = DEFAULT_CONTEXT_LENGTH,
    batch_size: int = 8,
    hold_threshold: float = 0.0,
) -> dict[str, dict[str, Any]]:
    if not windows:
        raise ValueError("At least one independent window is required")
    results = {
        "persistence": _successful_result(
            "Persistence",
            windows,
            persistence_forecast(windows),
            hold_threshold=hold_threshold,
            zero_shot=False,
        ),
        "last_delta": _successful_result(
            "Last delta extrapolation",
            windows,
            last_delta_forecast(windows),
            hold_threshold=hold_threshold,
            zero_shot=False,
        ),
    }
    if pipeline is None:
        error = pipeline_error or "Chronos-2 pipeline was not loaded"
        results["chronos2_univariate_close"] = _failed_result(
            "Chronos-2 small univariate close", error
        )
        results["chronos2_multivariate_scene"] = _failed_result(
            "Chronos-2 small multivariate scene", error
        )
        return results

    chronos_jobs = (
        (
            "chronos2_univariate_close",
            "Chronos-2 small univariate close",
            chronos_univariate_forecast,
        ),
        (
            "chronos2_multivariate_scene",
            "Chronos-2 small multivariate scene",
            chronos_multivariate_forecast,
        ),
    )
    for key, name, runner in chronos_jobs:
        try:
            forecast = runner(
                pipeline,
                windows,
                context_length=context_length,
                batch_size=batch_size,
            )
            results[key] = _successful_result(
                name,
                windows,
                forecast,
                hold_threshold=hold_threshold,
                zero_shot=True,
            )
        except Exception as exc:  # Keep the other challenger and baselines auditable.
            results[key] = _failed_result(name, f"{type(exc).__name__}: {exc}")
    return results


def load_local_chronos_pipeline(
    model_path: Path,
    *,
    torch_threads: int = 2,
) -> tuple[ChronosPipeline, dict[str, Any]]:
    if not model_path.is_dir():
        raise FileNotFoundError(f"Local Chronos model directory does not exist: {model_path}")
    if torch_threads != 2:
        raise ValueError("This bounded benchmark requires torch_threads=2")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch

    chronos_module = import_module("chronos")
    pipeline_loader = cast(
        ChronosPipelineLoader,
        getattr(chronos_module, "Chronos2Pipeline"),
    )

    torch.set_num_threads(torch_threads)
    started = time.perf_counter()
    pipeline = pipeline_loader.from_pretrained(
        str(model_path),
        device_map="cpu",
        dtype=torch.float32,
        local_files_only=True,
    )
    load_latency_ms = (time.perf_counter() - started) * 1_000.0
    return pipeline, {
        "status": "ok",
        "load_latency_ms": round(load_latency_ms, 3),
        "torch_threads": int(torch.get_num_threads()),
        "local_files_only": True,
        "device": "cpu",
        "dtype": "float32",
    }


def _package_version() -> str:
    try:
        return version("chronos-forecasting")
    except PackageNotFoundError:
        return "not-installed"


def make_report(
    *,
    data_path: Path,
    model_path: Path,
    load_counts: Mapping[str, int],
    held_out_rows: Sequence[Mapping[str, Any]],
    windows: Sequence[ForecastWindow],
    results: Mapping[str, Mapping[str, Any]],
    model_loading: Mapping[str, Any],
    context_length: int,
    horizon: int,
    batch_size: int,
    hold_threshold: float,
) -> dict[str, Any]:
    required_length = context_length + horizon
    eligible_sources = sum(
        1
        for row in held_out_rows
        if isinstance(row.get("features"), Sequence)
        and len(cast(Sequence[object], row["features"])) >= required_length
    )
    test_sources = {_source_id(row) for row in held_out_rows}
    test_groups = {str(row["independent_group"]) for row in held_out_rows}
    return {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stack": "V3",
        "benchmark_type": "ZERO_SHOT_CHALLENGER",
        "production_ready": False,
        "production_authorized": False,
        "promotion_decision": "NOT_ELIGIBLE_ZERO_SHOT_EVIDENCE_ONLY",
        "warning": (
            "These bounded zero-shot results are diagnostic challenger evidence only. "
            "They must not drive live entries or replace the production forecaster."
        ),
        "model": {
            "family": "Chronos-2",
            "variant": "chronos-2-small",
            "path": str(model_path.resolve()),
            "chronos_forecasting_version": _package_version(),
            "loading": dict(model_loading),
        },
        "protocol": {
            "data_path": str(data_path.resolve()),
            "split": "test_only",
            "context_length": context_length,
            "minimum_context_length": MINIMUM_CONTEXT_LENGTH,
            "horizon": horizon,
            "batch_size": batch_size,
            "torch_threads": 2,
            "local_files_only": True,
            "hold_threshold": hold_threshold,
            "group_independence": "one_latest_origin_window_per_independent_group",
            "future_features_used": False,
            "future_covariates_used": False,
            "truth_retained_after_origin": ["relative_price_location"],
            "univariate_targets": ["relative_price_location"],
            "multivariate_targets": list(TARGET_FEATURES),
            "past_only_covariates": list(PAST_COVARIATE_FEATURES),
            "quantiles": list(QUANTILE_LEVELS),
            "turning_point_definition": (
                "a BUY-to-SELL or SELL-to-BUY reversal between consecutive forecast deltas; "
                "HOLD transitions are not turns"
            ),
            "interval_coverage_definition": "marginal coverage over every sample and horizon step",
        },
        "data": {
            **dict(load_counts),
            "held_out_source_count": len(test_sources),
            "held_out_independent_group_count": len(test_groups),
            "eligible_held_out_source_count": eligible_sources,
            "evaluated_sample_count": len(windows),
            "evaluated_source_count": len({window.source_id for window in windows}),
            "evaluated_independent_group_count": len({window.group_id for window in windows}),
            "sample_manifest": [
                {
                    "independent_group": window.group_id,
                    "source_path": window.source_path,
                    "origin_index": window.origin_index,
                    "context_length": len(window.context_close),
                    "horizon": len(window.truth_close),
                }
                for window in windows
            ],
        },
        "results": {key: dict(value) for key, value in results.items()},
        "limitations": [
            "Only one window per perceptual group is scored to prevent correlated-source inflation.",
            "The held-out group count is small, so balanced accuracy and turning-point F1 have high variance.",
            "Chronos-2 is evaluated zero-shot with no PhoenixGuard fine-tuning or calibration.",
            "The extracted relative-price scale is chart-local and is not an exchange-native price series.",
        ],
    }


def write_report(report: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the bounded Chronos-2-small zero-shot scene forecaster V3 benchmark."
    )
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--context-length", type=int, default=DEFAULT_CONTEXT_LENGTH)
    parser.add_argument("--horizon", type=int, default=REQUIRED_HORIZON)
    parser.add_argument("--max-windows", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--hold-threshold", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.batch_size < 1:
        raise ValueError("batch_size must be positive")
    if args.hold_threshold < 0.0:
        raise ValueError("hold_threshold cannot be negative")

    held_out_rows, load_counts = load_held_out_sequences(args.data_path)
    windows = build_independent_windows(
        held_out_rows,
        context_length=args.context_length,
        horizon=args.horizon,
        max_windows=args.max_windows,
    )

    pipeline: Any | None = None
    pipeline_error = ""
    try:
        pipeline, model_loading = load_local_chronos_pipeline(
            args.model_path, torch_threads=args.torch_threads
        )
    except Exception as exc:
        pipeline_error = f"{type(exc).__name__}: {exc}"
        model_loading = {
            "status": "failed",
            "error": pipeline_error,
            "torch_threads": args.torch_threads,
            "local_files_only": True,
            "device": "cpu",
        }

    results = benchmark_windows(
        windows,
        pipeline=pipeline,
        pipeline_error=pipeline_error,
        context_length=args.context_length,
        batch_size=args.batch_size,
        hold_threshold=args.hold_threshold,
    )
    report = make_report(
        data_path=args.data_path,
        model_path=args.model_path,
        load_counts=load_counts,
        held_out_rows=held_out_rows,
        windows=windows,
        results=results,
        model_loading=model_loading,
        context_length=args.context_length,
        horizon=args.horizon,
        batch_size=args.batch_size,
        hold_threshold=args.hold_threshold,
    )
    write_report(report, args.report_path)

    statuses = {key: value["status"] for key, value in results.items()}
    print(
        json.dumps(
            {
                "report": str(args.report_path.resolve()),
                "evaluated_samples": len(windows),
                "independent_groups": len({window.group_id for window in windows}),
                "statuses": statuses,
                "production_ready": False,
            },
            indent=2,
        )
    )
    chronos_ok = all(
        results[key]["status"] == "ok"
        for key in ("chronos2_univariate_close", "chronos2_multivariate_scene")
    )
    return 0 if chronos_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
