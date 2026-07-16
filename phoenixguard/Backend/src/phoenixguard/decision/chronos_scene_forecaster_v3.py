from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
import random
import re
import statistics
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from phoenixguard.decision.forecast_path_geometry_v3 import (
    FORECAST_HORIZON_STEPS,
    ForecastPathGeometryError,
    decode_forecast_path_geometry_v3,
)
from phoenixguard.decision.scene_forecast_features_v3 import (
    SCHEMA_VERSION as SCENE_FEATURE_SCHEMA_VERSION,
)


CONTRIBUTION_SCHEMA_VERSION = "PG_CHRONOS_SCENE_FORECAST_CONTRIBUTION_V3"
METRICS_SCHEMA_VERSION = "PG_CHRONOS_SCENE_METRICS_V3"
MODEL_ID = "chronos-2-small"
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MODEL_DIR = _REPO_ROOT / "models" / "foundation" / MODEL_ID
DEFAULT_METRICS_PATH = DEFAULT_MODEL_DIR / "phoenixguard_metrics_v3.json"
_TARGET_NAMES = ("open_offset", "high_offset", "low_offset", "close_offset")
_QUANTILE_LEVELS = (0.10, 0.50, 0.90)
_QUANTILE_KEYS = ("p10", "p50", "p90")
_DEFAULT_SAMPLE_COUNT = 31
_MAX_CACHE_ENTRIES = 32
_NORMAL_90_Z = 1.2815515655446004


@dataclass(frozen=True)
class _ChronosRuntime:
    pipeline: Any
    numpy: Any
    torch: Any
    model_dir: Path
    cpu_threads: int


@dataclass(frozen=True)
class _PreparedScene:
    targets: tuple[tuple[float, ...], ...]
    past_covariates: dict[str, tuple[float, ...]]
    close_history: tuple[float, ...]
    schema_fingerprint: str
    history_length: int


_RUNTIME_LOCK = threading.RLock()
_INFERENCE_LOCK = threading.Lock()
_CACHE_LOCK = threading.RLock()
_runtime: _ChronosRuntime | None = None
_runtime_attempted = False
_runtime_error = ""
_FORECAST_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _unit(value: object, *, label: str) -> float:
    number = _finite(value, label=label)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{label} must be within [0, 1]")
    return number


def _sequence(value: object, *, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be a sequence")
    return cast(Sequence[Any], value)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    mapping = cast(Mapping[Any, Any], value)
    return {str(key): item for key, item in mapping.items()}


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")[:96] or "feature"


def _category_number(value: object) -> float:
    digest = hashlib.sha256(str(value or "__MISSING__").encode("utf-8")).digest()
    integer = int.from_bytes(digest[:8], "big", signed=False)
    return 2.0 * (integer / float((1 << 64) - 1)) - 1.0


def _cpu_thread_bound() -> int:
    default = min(4, max(1, int(os.cpu_count() or 1)))
    try:
        requested = int(os.getenv("PHOENIXGUARD_CHRONOS_CPU_THREADS", str(default)))
    except ValueError:
        requested = default
    return max(1, min(8, requested))


def _load_local_runtime() -> _ChronosRuntime:
    """Load Chronos lazily from the one approved local directory.

    Optional runtime dependencies are intentionally imported only in this
    loader. ``local_files_only=True`` and a resolved filesystem path prevent a
    model-hub or network fallback.
    """

    model_dir = DEFAULT_MODEL_DIR.resolve()
    expected = (_REPO_ROOT / "models" / "foundation" / MODEL_ID).resolve()
    if model_dir != expected:
        raise RuntimeError("Chronos V3 model path must remain models/foundation/chronos-2-small")
    if not model_dir.is_dir():
        raise RuntimeError(f"local Chronos model directory is unavailable: {model_dir}")
    for required_name in ("config.json", "model.safetensors"):
        if not (model_dir / required_name).is_file():
            raise RuntimeError(f"local Chronos artifact is incomplete: {required_name}")

    numpy = importlib.import_module("numpy")
    torch = importlib.import_module("torch")
    chronos = importlib.import_module("chronos")
    pipeline_class = getattr(chronos, "Chronos2Pipeline", None)
    if pipeline_class is None:
        raise RuntimeError("installed chronos package does not provide Chronos2Pipeline")

    cpu_threads = _cpu_thread_bound()
    torch.set_num_threads(cpu_threads)
    try:
        torch.set_num_interop_threads(max(1, min(2, cpu_threads)))
    except RuntimeError:
        # PyTorch permits setting inter-op threads only before parallel work.
        # The already-established process bound remains authoritative.
        pass
    pipeline = pipeline_class.from_pretrained(
        str(model_dir),
        local_files_only=True,
        dtype=torch.float32,
    )
    inner_model = getattr(pipeline, "model", getattr(pipeline, "inner_model", None))
    if inner_model is not None:
        if hasattr(inner_model, "to"):
            inner_model.to("cpu")
        if hasattr(inner_model, "eval"):
            inner_model.eval()
    return _ChronosRuntime(
        pipeline=pipeline,
        numpy=numpy,
        torch=torch,
        model_dir=model_dir,
        cpu_threads=cpu_threads,
    )


def _get_local_runtime() -> tuple[_ChronosRuntime | None, str]:
    global _runtime, _runtime_attempted, _runtime_error
    with _RUNTIME_LOCK:
        if _runtime is not None:
            return _runtime, ""
        if _runtime_attempted:
            return None, _runtime_error
        _runtime_attempted = True
        try:
            _runtime = _load_local_runtime()
            _runtime_error = ""
        except Exception as exc:  # optional dependency/model availability boundary
            _runtime_error = f"{type(exc).__name__}: {exc}"
        return _runtime, _runtime_error


def reset_provider_state_for_tests() -> None:
    global _runtime, _runtime_attempted, _runtime_error
    with _RUNTIME_LOCK, _CACHE_LOCK:
        _runtime = None
        _runtime_attempted = False
        _runtime_error = ""
        _FORECAST_CACHE.clear()


# Backward-compatible test hook retained for existing integration fixtures.
_reset_provider_state_for_tests = reset_provider_state_for_tests


def _prepare_scene_features(scene_features: Mapping[str, Any]) -> _PreparedScene:
    if str(scene_features.get("schema_version") or "") != SCENE_FEATURE_SCHEMA_VERSION:
        raise ValueError(f"scene features must use {SCENE_FEATURE_SCHEMA_VERSION}")
    schema_fingerprint = str(scene_features.get("schema_fingerprint") or "")
    if not schema_fingerprint:
        raise ValueError("scene feature schema_fingerprint is required")

    sequence_payload = _mapping(scene_features.get("sequence"))
    numeric_schema = [
        str(value) for value in _sequence(sequence_payload.get("numeric_schema"), label="sequence.numeric_schema")
    ]
    numeric_rows_raw = _sequence(
        sequence_payload.get("numeric_rows"),
        label="sequence.numeric_rows",
    )
    if not numeric_rows_raw:
        raise ValueError("at least one closed-candle feature row is required")
    target_indices: list[int] = []
    for target in _TARGET_NAMES:
        try:
            target_indices.append(numeric_schema.index(target))
        except ValueError as exc:
            raise ValueError(f"scene sequence is missing target {target}") from exc

    rows: list[tuple[float, ...]] = []
    for row_index, raw_row in enumerate(numeric_rows_raw):
        row_values = _sequence(raw_row, label=f"sequence.numeric_rows[{row_index}]")
        if len(row_values) != len(numeric_schema):
            raise ValueError(f"sequence.numeric_rows[{row_index}] has the wrong width")
        rows.append(
            tuple(
                _finite(value, label=f"sequence.numeric_rows[{row_index}][{column_index}]")
                for column_index, value in enumerate(row_values)
            )
        )
    history_length = len(rows)
    targets = tuple(
        tuple(row[column_index] for row in rows) for column_index in target_indices
    )

    past_covariates: dict[str, tuple[float, ...]] = {}
    for column_index, name in enumerate(numeric_schema):
        if column_index in target_indices:
            continue
        past_covariates[f"candle_{_safe_name(name)}"] = tuple(
            row[column_index] for row in rows
        )

    categorical_schema = [
        str(value)
        for value in _sequence(
            sequence_payload.get("categorical_schema", []),
            label="sequence.categorical_schema",
        )
    ]
    categorical_rows = _sequence(
        sequence_payload.get("categorical_rows", []),
        label="sequence.categorical_rows",
    )
    if categorical_rows and len(categorical_rows) != history_length:
        raise ValueError("sequence categorical history length does not match numeric history")
    for column_index, name in enumerate(categorical_schema):
        encoded: list[float] = []
        for row_index, raw_row in enumerate(categorical_rows):
            row = _sequence(raw_row, label=f"sequence.categorical_rows[{row_index}]")
            if len(row) != len(categorical_schema):
                raise ValueError(f"sequence.categorical_rows[{row_index}] has the wrong width")
            encoded.append(_category_number(row[column_index]))
        if encoded:
            past_covariates[f"candle_category_{_safe_name(name)}"] = tuple(encoded)

    context_payload = _mapping(scene_features.get("context"))
    context_schema = [
        str(value)
        for value in _sequence(
            context_payload.get("numeric_schema", []),
            label="context.numeric_schema",
        )
    ]
    context_values = _sequence(
        context_payload.get("numeric_values", []),
        label="context.numeric_values",
    )
    if len(context_schema) != len(context_values):
        raise ValueError("context numeric schema and values have different widths")
    for index, (name, value) in enumerate(zip(context_schema, context_values)):
        number = _finite(value, label=f"context.numeric_values[{index}]")
        # A scene summary describes the forecast origin, not every historical
        # candle. Publish it as a final past-only impulse instead of backfilling
        # present knowledge across older rows.
        past_covariates[f"scene_{_safe_name(name)}"] = (
            *((0.0,) * max(0, history_length - 1)),
            number,
        )

    context_categories = [
        str(value)
        for value in _sequence(
            context_payload.get("categorical_schema", []),
            label="context.categorical_schema",
        )
    ]
    context_category_values = _sequence(
        context_payload.get("categorical_values", []),
        label="context.categorical_values",
    )
    if len(context_categories) != len(context_category_values):
        raise ValueError("context categorical schema and values have different widths")
    for name, value in zip(context_categories, context_category_values):
        past_covariates[f"scene_category_{_safe_name(name)}"] = (
            *((0.0,) * max(0, history_length - 1)),
            _category_number(value),
        )

    return _PreparedScene(
        targets=targets,
        past_covariates=past_covariates,
        close_history=targets[3],
        schema_fingerprint=schema_fingerprint,
        history_length=history_length,
    )


def _model_input(runtime: _ChronosRuntime, prepared: _PreparedScene) -> dict[str, Any]:
    numpy = runtime.numpy
    return {
        "target": numpy.asarray(prepared.targets, dtype=numpy.float32),
        "past_covariates": {
            name: numpy.asarray(values, dtype=numpy.float32)
            for name, values in prepared.past_covariates.items()
        },
        # Future suite values are unknowable at the causal cut. Their absence
        # is deliberate and prevents contemporaneous overlays from leaking.
    }


def _to_nested_list(value: Any) -> Any:
    current = value
    if hasattr(current, "detach"):
        current = current.detach()
    if hasattr(current, "cpu"):
        current = current.cpu()
    if hasattr(current, "tolist"):
        return current.tolist()
    return current


def _quantile_indices(pipeline: Any) -> tuple[int, int, int]:
    levels = [float(value) for value in getattr(pipeline, "quantiles", [])]
    if not levels:
        raise RuntimeError("Chronos2Pipeline did not expose training quantiles")
    indices: list[int] = []
    for required in _QUANTILE_LEVELS:
        matches = [index for index, value in enumerate(levels) if abs(value - required) <= 1e-8]
        if len(matches) != 1:
            raise RuntimeError(f"Chronos model does not expose quantile {required}")
        indices.append(matches[0])
    return indices[0], indices[1], indices[2]


def _predict_direct_quantiles(
    runtime: _ChronosRuntime,
    prepared: _PreparedScene,
) -> dict[str, dict[str, tuple[float, ...]]]:
    model_input = _model_input(runtime, prepared)
    batch_size = max(32, min(512, len(prepared.past_covariates) + len(_TARGET_NAMES)))
    with _INFERENCE_LOCK:
        predictions = runtime.pipeline.predict(
            [model_input],
            prediction_length=FORECAST_HORIZON_STEPS,
            batch_size=batch_size,
            cross_learning=False,
            limit_prediction_length=True,
        )
    prediction_rows = _sequence(predictions, label="Chronos predictions")
    if len(prediction_rows) != 1:
        raise RuntimeError("Chronos returned an unexpected task count")
    values = _to_nested_list(prediction_rows[0])
    target_rows = _sequence(values, label="Chronos target predictions")
    if len(target_rows) != len(_TARGET_NAMES):
        raise RuntimeError("Chronos did not return all four OHLC targets")
    quantile_indices = _quantile_indices(runtime.pipeline)
    result: dict[str, dict[str, tuple[float, ...]]] = {}
    for target_name, raw_target in zip(_TARGET_NAMES, target_rows):
        target_quantiles = _sequence(raw_target, label=f"Chronos {target_name} quantiles")
        field: dict[str, tuple[float, ...]] = {}
        for key, quantile_index in zip(_QUANTILE_KEYS, quantile_indices):
            if quantile_index >= len(target_quantiles):
                raise RuntimeError(f"Chronos {target_name} omitted {key}")
            horizon = _sequence(
                target_quantiles[quantile_index],
                label=f"Chronos {target_name}.{key}",
            )
            if len(horizon) != FORECAST_HORIZON_STEPS:
                raise RuntimeError(f"Chronos {target_name}.{key} is not twelve events")
            field[key] = tuple(
                _finite(value, label=f"Chronos {target_name}.{key}[{step}]")
                for step, value in enumerate(horizon)
            )
        result[target_name] = field
    return result


def _metrics_gate(
    metrics_path: Path,
    *,
    schema_fingerprint: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if metrics_path.is_file():
        try:
            loaded = json.loads(metrics_path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping):
                loaded_mapping = cast(Mapping[Any, Any], loaded)
                payload = {str(key): value for key, value in loaded_mapping.items()}
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
    checks = {
        "schema": payload.get("schema_version") == METRICS_SCHEMA_VERSION,
        "model": str(payload.get("model_id") or "") == MODEL_ID,
        "scene_schema": str(payload.get("scene_schema_fingerprint") or "")
        == schema_fingerprint,
        "horizon": payload.get("horizon_steps") == FORECAST_HORIZON_STEPS,
        "walk_forward": payload.get("walk_forward_validated") is True,
        "leakage_audit": payload.get("leakage_audit_passed") is True,
        "production_gate": payload.get("production_gate_passed") is True,
        "path_calibration_gate": payload.get("path_calibration_gate_passed") is True,
    }
    production_passed = all(checks.values())
    return {
        "artifact_present": bool(payload),
        "artifact_path": str(metrics_path),
        "checks": checks,
        "production_gate_passed": production_passed,
        "path_calibration_gate_passed": bool(
            production_passed and checks["path_calibration_gate"]
        ),
        "metrics_revision": str(payload.get("metrics_revision") or ""),
    }


def _price_scale(anchor: Mapping[str, Any]) -> tuple[float, float]:
    price_norm = _unit(anchor.get("price_norm"), label="anchor.price_norm")
    scale = _finite(
        anchor.get("target_scale_norm", anchor.get("price_scale_norm")),
        label="anchor.target_scale_norm",
    )
    if scale <= 0.0 or scale > 1.0:
        raise ValueError("anchor.target_scale_norm must be within (0, 1]")
    return price_norm, scale


def _price_quantiles(
    offsets: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    anchor_price: float,
    target_scale: float,
) -> dict[str, dict[str, tuple[float, ...]]]:
    result: dict[str, dict[str, tuple[float, ...]]] = {}
    for target in _TARGET_NAMES:
        target_rows = offsets.get(target)
        if target_rows is None:
            raise ValueError(f"direct forecast omitted {target}")
        field: dict[str, tuple[float, ...]] = {}
        for key in _QUANTILE_KEYS:
            values = target_rows.get(key)
            if values is None or len(values) != FORECAST_HORIZON_STEPS:
                raise ValueError(f"direct forecast omitted {target}.{key}")
            raw_converted = tuple(
                anchor_price + float(value) * target_scale for value in values
            )
            if any(not math.isfinite(value) for value in raw_converted):
                raise ValueError(f"direct forecast {target}.{key} is non-finite")
            # These are chart-relative price coordinates, not already-rendered
            # pixels. Keep off-plane values intact so the geometry decoder can
            # fit the complete trajectory once without destroying its turns.
            field[key] = raw_converted
        for step in range(FORECAST_HORIZON_STEPS):
            if not field["p10"][step] <= field["p50"][step] <= field["p90"][step]:
                raise ValueError(f"direct forecast {target} quantiles cross at E{step + 1}")
        result[target] = field
    for key in _QUANTILE_KEYS:
        for step in range(FORECAST_HORIZON_STEPS):
            if not (
                result["low_offset"][key][step]
                <= result["close_offset"][key][step]
                <= result["high_offset"][key][step]
            ):
                raise ValueError(f"direct OHLC quantiles cross at {key} E{step + 1}")
    return result


def _residual_library(close_history: Sequence[float]) -> tuple[float, ...]:
    deltas = [right - left for left, right in zip(close_history, close_history[1:])]
    if not deltas:
        return (-1.0, 0.0, 1.0)
    center = statistics.median(deltas)
    centered = [value - center for value in deltas]
    scale = math.sqrt(statistics.fmean(value * value for value in centered))
    if scale <= 1e-9:
        return (-1.0, 0.0, 1.0)
    return tuple(max(-3.0, min(3.0, value / scale)) for value in centered)


def _empirical_quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return ordered[lower_index]
    weight = position - lower_index
    return ordered[lower_index] * (1.0 - weight) + ordered[upper_index] * weight


def _sample_paths(
    price_quantiles: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    anchor_price: float,
    residuals: Sequence[float],
    seed: int,
    sample_count: int = _DEFAULT_SAMPLE_COUNT,
) -> tuple[list[dict[str, list[float]]], int]:
    close = price_quantiles["close_offset"]
    open_field = price_quantiles["open_offset"]["p50"]
    high_field = price_quantiles["high_offset"]["p50"]
    low_field = price_quantiles["low_offset"]["p50"]
    median_close = list(close["p50"])
    spreads = [
        max(1e-6, (close["p90"][step] - close["p10"][step]) / (2.0 * _NORMAL_90_Z))
        for step in range(FORECAST_HORIZON_STEPS)
    ]
    upper_wicks = [
        max(0.0, high_field[step] - max(open_field[step], median_close[step]))
        for step in range(FORECAST_HORIZON_STEPS)
    ]
    lower_wicks = [
        max(0.0, min(open_field[step], median_close[step]) - low_field[step])
        for step in range(FORECAST_HORIZON_STEPS)
    ]

    def coherent(close_path: Sequence[float]) -> dict[str, list[float]]:
        prior = anchor_price
        upper: list[float] = []
        lower: list[float] = []
        coherent_close: list[float] = []
        for step, raw_close in enumerate(close_path):
            value = float(raw_close)
            coherent_close.append(value)
            high = max(prior, value) + upper_wicks[step]
            low = min(prior, value) - lower_wicks[step]
            upper.append(high)
            lower.append(low)
            prior = value
        return {"close": coherent_close, "upper": upper, "lower": lower}

    samples: list[dict[str, list[float]]] = []
    samples.append(coherent(median_close))
    generator = random.Random(seed)
    pair_count = max(1, (max(3, sample_count) - 1) // 2)
    library = tuple(residuals) or (-1.0, 0.0, 1.0)
    standard_normal = statistics.NormalDist()
    persistence = 0.72
    innovation_weight = math.sqrt(1.0 - persistence**2)
    for _ in range(pair_count):
        latent = 0.0
        deviations: list[float] = []
        for step in range(FORECAST_HORIZON_STEPS):
            # Gaussian copula: correlated normal ranks preserve path continuity;
            # the empirical inverse CDF restores the scene's residual shape.
            innovation = generator.gauss(0.0, 1.0)
            latent = persistence * latent + innovation_weight * innovation
            rank = standard_normal.cdf(latent)
            empirical_residual = _empirical_quantile(library, rank)
            deviations.append(empirical_residual * spreads[step])
        positive = coherent(
            [value + deviation for value, deviation in zip(median_close, deviations)]
        )
        negative = coherent(
            [value - deviation for value, deviation in zip(median_close, deviations)]
        )
        samples.extend((positive, negative))
    return samples, 0


def _sample_fingerprint(samples: Sequence[Mapping[str, Sequence[float]]]) -> str:
    canonical = [
        {
            field: [round(float(value), 12) for value in sample[field]]
            for field in ("close", "upper", "lower")
        }
        for sample in samples
    ]
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _suite_direction_bias(prepared: _PreparedScene) -> float:
    """Blend causal suite evidence without pretending it is future truth."""

    def latest(name: str) -> float:
        values = prepared.past_covariates.get(f"scene_{name}", ())
        return float(values[-1]) if values else 0.0

    belief = latest("decision_belief_buy") - latest("decision_belief_sell")
    next_event = latest("decision_p_next_buy") - latest("decision_p_next_sell")
    evidence = latest("decision_buy_evidence") - latest("decision_sell_evidence")
    structure = (
        latest("support_resistance_buy_structure_score")
        - latest("support_resistance_sell_structure_score")
    )
    trend = math.tanh(
        0.55 * latest("trend_slope_local")
        + 0.30 * latest("trend_slope_global")
        + 0.15 * latest("trend_slope_current")
    )
    bias = (
        0.32 * belief
        + 0.22 * next_event
        + 0.18 * evidence
        + 0.16 * structure
        + 0.12 * trend
    )
    return max(-1.0, min(1.0, bias))


def _fallback_offset_quantiles(prepared: _PreparedScene) -> dict[str, dict[str, tuple[float, ...]]]:
    closes = prepared.close_history
    deltas = [right - left for left, right in zip(closes, closes[1:])]
    recent = deltas[-min(7, len(deltas)) :] if deltas else [0.0]
    momentum = statistics.median(recent)
    residual_center = statistics.median(deltas) if deltas else 0.0
    residuals = [value - residual_center for value in deltas] or [0.0]
    residual_scale = max(
        0.025,
        math.sqrt(statistics.fmean(value * value for value in residuals)),
    )
    suite_bias = _suite_direction_bias(prepared)
    suite_drift = suite_bias * max(
        residual_scale,
        statistics.median(abs(value) for value in recent),
        0.025,
    )
    blended_drift = 0.64 * momentum + 0.36 * suite_drift
    current = closes[-1] if closes else 0.0
    median_path: list[float] = []
    for step in range(FORECAST_HORIZON_STEPS):
        innovation = residuals[-1 - (step % len(residuals))] * 0.18
        current += blended_drift * (0.84**step) + innovation
        median_path.append(current)
    spread = [residual_scale * math.sqrt(step + 1) for step in range(FORECAST_HORIZON_STEPS)]
    close = {
        "p10": tuple(value - _NORMAL_90_Z * spread[index] for index, value in enumerate(median_path)),
        "p50": tuple(median_path),
        "p90": tuple(value + _NORMAL_90_Z * spread[index] for index, value in enumerate(median_path)),
    }
    typical_range = max(
        0.05,
        statistics.median(
            high - low
            for high, low in zip(prepared.targets[1], prepared.targets[2])
        ),
    )
    open_rows: dict[str, tuple[float, ...]] = {}
    high_rows: dict[str, tuple[float, ...]] = {}
    low_rows: dict[str, tuple[float, ...]] = {}
    for key in _QUANTILE_KEYS:
        prior = closes[-1] if closes else 0.0
        opens: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        for value in close[key]:
            opens.append(prior)
            highs.append(max(prior, value) + 0.20 * typical_range)
            lows.append(min(prior, value) - 0.20 * typical_range)
            prior = value
        open_rows[key] = tuple(opens)
        high_rows[key] = tuple(highs)
        low_rows[key] = tuple(lows)
    return {
        "open_offset": open_rows,
        "high_offset": high_rows,
        "low_offset": low_rows,
        "close_offset": close,
    }


def _cache_key(
    scene_features: Mapping[str, Any],
    anchor: Mapping[str, Any],
    *,
    seed: int,
    metrics_gate: Mapping[str, Any],
    allow_foundation_model: bool,
) -> str:
    payload = {
        "scene": scene_features,
        "anchor": anchor,
        "seed": seed,
        "metrics": metrics_gate,
        "allow_foundation_model": allow_foundation_model,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _cached(key: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        value = _FORECAST_CACHE.get(key)
        if value is None:
            return None
        _FORECAST_CACHE.move_to_end(key)
        return copy.deepcopy(value)


def _store_cache(key: str, value: Mapping[str, Any]) -> None:
    with _CACHE_LOCK:
        _FORECAST_CACHE[key] = copy.deepcopy(dict(value))
        _FORECAST_CACHE.move_to_end(key)
        while len(_FORECAST_CACHE) > _MAX_CACHE_ENTRIES:
            _FORECAST_CACHE.popitem(last=False)


def build_chronos_scene_forecast_contribution_v3(
    *,
    scene_features: Mapping[str, Any],
    anchor: Mapping[str, Any],
    deterministic_seed: int = 808,
    metrics_path: Path | str | None = None,
    allow_foundation_model: bool = True,
) -> dict[str, Any]:
    """Build a local-only Chronos-2 V3 shadow forecast contribution.

    The provider always uses the causal ``PG_SCENE_FORECAST_FEATURES_V3``
    contract. Model failure is isolated behind a deterministic residual-library
    fallback that still returns twelve chart events, but that fallback can
    never claim calibration, production readiness, or trade authority.
    """

    prepared = _prepare_scene_features(scene_features)
    anchor_price, target_scale = _price_scale(anchor)
    resolved_metrics_path = Path(metrics_path) if metrics_path is not None else DEFAULT_METRICS_PATH
    metrics_gate = _metrics_gate(
        resolved_metrics_path,
        schema_fingerprint=prepared.schema_fingerprint,
    )
    seed = int(deterministic_seed)
    key = _cache_key(
        scene_features,
        anchor,
        seed=seed,
        metrics_gate=metrics_gate,
        allow_foundation_model=bool(allow_foundation_model),
    )
    cached = _cached(key)
    if cached is not None:
        cached["cache_hit"] = True
        return cached

    if allow_foundation_model:
        runtime, runtime_error = _get_local_runtime()
    else:
        runtime, runtime_error = None, "foundation inference disabled for this latency-sensitive call"
    provider_status = "AVAILABLE"
    fallback_reason = ""
    direct_offsets: dict[str, dict[str, tuple[float, ...]]]
    if runtime is None:
        provider_status = (
            "UNAVAILABLE_FALLBACK"
            if allow_foundation_model
            else "FOUNDATION_DISABLED_FALLBACK"
        )
        fallback_reason = runtime_error or "local Chronos runtime unavailable"
        direct_offsets = _fallback_offset_quantiles(prepared)
    else:
        try:
            direct_offsets = _predict_direct_quantiles(runtime, prepared)
        except Exception as exc:
            provider_status = "INFERENCE_FALLBACK"
            fallback_reason = f"{type(exc).__name__}: {exc}"
            direct_offsets = _fallback_offset_quantiles(prepared)

    is_foundation_output = provider_status == "AVAILABLE"
    production_gate_passed = bool(
        is_foundation_output and metrics_gate["production_gate_passed"]
    )
    calibrated = bool(
        production_gate_passed and metrics_gate["path_calibration_gate_passed"]
    )
    try:
        direct_prices = _price_quantiles(
            direct_offsets,
            anchor_price=anchor_price,
            target_scale=target_scale,
        )
    except ValueError as exc:
        if not is_foundation_output:
            raise
        provider_status = "INVALID_MODEL_OUTPUT_FALLBACK"
        fallback_reason = f"{type(exc).__name__}: {exc}"
        direct_offsets = _fallback_offset_quantiles(prepared)
        direct_prices = _price_quantiles(
            direct_offsets,
            anchor_price=anchor_price,
            target_scale=target_scale,
        )
        is_foundation_output = False
        production_gate_passed = False
        calibrated = False

    residuals = _residual_library(prepared.close_history)
    samples, clipped_count = _sample_paths(
        direct_prices,
        anchor_price=anchor_price,
        residuals=residuals,
        seed=seed,
    )
    geometry_anchor = {
        "x_norm": anchor.get("x_norm"),
        "y_norm": anchor.get("y_norm"),
        "price_norm": anchor_price,
        "event_step_x_norm": anchor.get(
            "event_step_x_norm",
            anchor.get("step_x_norm"),
        ),
        "verified_latest_close": bool(anchor.get("verified_latest_close", False)),
        "source": str(anchor.get("source") or "MODEL_CAUSAL_CANDLE"),
    }
    try:
        geometry = decode_forecast_path_geometry_v3(
            anchor=geometry_anchor,
            sampled_trajectories=samples,
            calibrated=calibrated,
            calibration_method=(
                "WALK_FORWARD_CHRONOS_RESIDUAL_COPULA"
                if calibrated
                else ""
            ),
        )
    except ForecastPathGeometryError as exc:
        if not is_foundation_output:
            raise
        provider_status = "INVALID_MODEL_GEOMETRY_FALLBACK"
        fallback_reason = f"{type(exc).__name__}: {exc}"
        direct_offsets = _fallback_offset_quantiles(prepared)
        direct_prices = _price_quantiles(
            direct_offsets,
            anchor_price=anchor_price,
            target_scale=target_scale,
        )
        samples, clipped_count = _sample_paths(
            direct_prices,
            anchor_price=anchor_price,
            residuals=residuals,
            seed=seed,
        )
        geometry = decode_forecast_path_geometry_v3(
            anchor=geometry_anchor,
            sampled_trajectories=samples,
            calibrated=False,
        )
        is_foundation_output = False
        production_gate_passed = False
        calibrated = False

    direct_public = {
        target: {
            key: [round(value, 10) for value in values]
            for key, values in quantiles.items()
        }
        for target, quantiles in direct_prices.items()
    }
    raw_side_probabilities = {"BUY": 0.0, "HOLD": 0.0, "SELL": 0.0}
    for scenario in geometry.get("forecast_scenarios", []):
        if not isinstance(scenario, Mapping):
            continue
        scenario_payload = _mapping(cast(object, scenario))
        side = str(scenario_payload.get("side") or "HOLD").upper()
        if side not in raw_side_probabilities:
            side = "HOLD"
        raw_side_probabilities[side] += max(
            0.0,
            _finite(
                scenario_payload.get("probability") or 0.0,
                label="forecast scenario probability",
            ),
        )
    probability_total = sum(raw_side_probabilities.values())
    if probability_total > 0.0:
        raw_side_probabilities = {
            side: value / probability_total
            for side, value in raw_side_probabilities.items()
        }
    else:
        raw_side_probabilities["HOLD"] = 1.0

    result: dict[str, Any] = {
        **geometry,
        "schema_version": CONTRIBUTION_SCHEMA_VERSION,
        # Do not label a residual-library fallback as a Chronos prediction.
        # The attempted foundation provider remains explicit below, while the
        # public provider always names the engine that actually drew the path.
        "provider": (
            "CHRONOS_2_LOCAL"
            if is_foundation_output
            else "SCENE_STATISTICAL_FALLBACK_V3"
        ),
        "requested_provider": "CHRONOS_2_LOCAL",
        "provider_status": provider_status,
        "forecast_available": True,
        "forecast_quality_status": (
            "READY" if production_gate_passed else "DIAGNOSTIC"
        ),
        "zero_shot": is_foundation_output,
        "shadow_mode": not production_gate_passed,
        "production_authorized": production_gate_passed,
        "trade_authorized": False,
        "selective_authorized": False,
        "contribution": 0.0,
        "probability_calibrated": calibrated,
        "raw_side_probabilities": {
            side: round(value, 10)
            for side, value in raw_side_probabilities.items()
        },
        "side_probabilities": (
            {
                side: round(value, 10)
                for side, value in raw_side_probabilities.items()
            }
            if calibrated
            else {}
        ),
        "model": {
            "model_id": MODEL_ID,
            "artifact_path": str(DEFAULT_MODEL_DIR),
            "local_only": True,
            "network_allowed": False,
            "foundation_model": True,
            "zero_shot": True,
            "fine_tuned": False,
            "loaded": runtime is not None,
            "used_for_forecast": is_foundation_output,
            "cpu_threads": runtime.cpu_threads if runtime is not None else _cpu_thread_bound(),
            "inference_mode": (
                "PRODUCTION_GATED_ZERO_SHOT"
                if production_gate_passed
                else "ZERO_SHOT_SHADOW"
                if is_foundation_output
                else "DIAGNOSTIC_RESIDUAL_LIBRARY_FALLBACK"
            ),
        },
        "metrics_gate": metrics_gate,
        "scene_feature_contract": {
            "schema_version": SCENE_FEATURE_SCHEMA_VERSION,
            "schema_fingerprint": prepared.schema_fingerprint,
            "history_length": prepared.history_length,
            "target_names": list(_TARGET_NAMES),
            "target_mode": "MULTIVARIATE_CLOSED_CANDLES",
            "covariate_mode": "PAST_ONLY_STRUCTURED_SUITE",
            "past_covariate_count": len(prepared.past_covariates),
            "future_covariates_used": False,
        },
        "direct_quantiles": direct_public,
        "trajectory_sampler": {
            "method": "DETERMINISTIC_EMPIRICAL_GAUSSIAN_COPULA",
            "seed": seed,
            "sample_count": len(samples),
            "sample_fingerprint": _sample_fingerprint(samples),
            "boundary_clipped_values": clipped_count,
            "base_selection": "TRAJECTORY_MEDOID",
        },
        "fallback": {
            "active": not is_foundation_output,
            "method": (
                "RESIDUAL_LIBRARY_STATISTICAL_NON_LSTM"
                if not is_foundation_output
                else "NONE"
            ),
            "reason": fallback_reason,
            "calibrated": False if not is_foundation_output else calibrated,
            "trade_authorized": False,
            "suite_direction_bias": round(_suite_direction_bias(prepared), 10),
            "suite_features_used": True,
        },
        "cache_hit": False,
        "interpretation": (
            "Local Chronos-2 zero-shot shadow forecast with coherent residual-copula trajectories."
            if is_foundation_output
            else "Chronos was unavailable or invalid; a deterministic non-LSTM residual-library diagnostic path is shown."
        ),
    }
    _store_cache(key, result)
    return copy.deepcopy(result)


__all__ = [
    "CONTRIBUTION_SCHEMA_VERSION",
    "DEFAULT_METRICS_PATH",
    "DEFAULT_MODEL_DIR",
    "METRICS_SCHEMA_VERSION",
    "MODEL_ID",
    "build_chronos_scene_forecast_contribution_v3",
]
