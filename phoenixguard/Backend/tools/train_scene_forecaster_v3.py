from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

import torch
from torch import Tensor
from torch.utils.data import DataLoader, Dataset


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SOURCE = REPO_ROOT / "Backend" / "src"
if str(BACKEND_SOURCE) not in sys.path:
    sys.path.insert(0, str(BACKEND_SOURCE))

from phoenixguard.decision.scene_forecast_features_v3 import (  # noqa: E402
    CANDLE_NUMERIC_SCHEMA,
    CONTEXT_NUMERIC_SCHEMA,
    SCHEMA_FINGERPRINT as FEATURE_SCHEMA_FINGERPRINT,
)
from phoenixguard.decision.scene_patch_forecaster_v3 import (  # noqa: E402
    DEFAULT_HORIZON,
    MOVEMENT_LABELS,
    QUANTILE_LEVELS,
    SCENE_FORECASTER_SCHEMA_VERSION,
    ScenePatchForecasterConfig,
    ScenePatchForecasterV3,
    scene_forecast_loss,
)


DEFAULT_SEQUENCE_DATA = REPO_ROOT / "data_splits" / "lstm_raw_candle_sequences_v3.jsonl"
DEFAULT_SPLIT_MANIFEST = REPO_ROOT / "data_splits" / "split_manifest.csv"
DEFAULT_OUTPUT_DIRECTORY = REPO_ROOT / "models"
ARTIFACT_FILENAME = "scene_forecaster_v3.pt"
CONFIG_FILENAME = "scene_forecaster_v3_config.json"
METRICS_FILENAME = "scene_forecaster_v3_metrics.json"
MANIFEST_FILENAME = "scene_forecaster_v3.manifest.json"
SPLIT_NAMES = ("train", "val", "test")
BASE_CANDLE_SCHEMA = tuple(
    name for name in CANDLE_NUMERIC_SCHEMA if not name.endswith("__missing")
)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _path_keys(value: object, *, relative_to: Path | None = None) -> tuple[str, ...]:
    raw = str(value or "").strip()
    if not raw:
        return ()
    normalized = raw.replace("/", "\\").casefold()
    path = Path(raw)
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    resolved = str(path.resolve(strict=False)).replace("/", "\\").casefold()
    return tuple(dict.fromkeys((normalized, resolved)))


def _manifest_source_map(manifest_path: Path) -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            split = str(row.get("split") or "").strip().casefold()
            if split not in SPLIT_NAMES:
                raise ValueError(f"unsupported manifest split: {split!r}")
            group_index = str(row.get("group_index") or "").strip()
            if not group_index:
                raise ValueError("split manifest contains an empty group_index")
            identity = (split, f"{split}:perceptual-group:{group_index}")
            for field in ("source_path", "destination_path"):
                for key in _path_keys(row.get(field), relative_to=manifest_path.parent):
                    previous = mapping.get(key)
                    if previous is not None and previous != identity:
                        raise ValueError(f"manifest path is assigned twice: {row.get(field)!r}")
                    mapping[key] = identity
    if not mapping:
        raise ValueError(f"no split rows found in {manifest_path}")
    return mapping


def _manifest_identity(
    row: Mapping[str, Any],
    mapping: Mapping[str, tuple[str, str]],
    *,
    relative_to: Path,
) -> tuple[str, str] | None:
    matches: set[tuple[str, str]] = set()
    for field in ("source", "source_path"):
        for key in _path_keys(row.get(field), relative_to=relative_to):
            if key in mapping:
                matches.add(mapping[key])
    if len(matches) > 1:
        raise ValueError(f"source resolves to conflicting split rows: {row.get('source_path')!r}")
    return next(iter(matches), None)


def load_sequence_sources(
    sequence_path: Path,
    *,
    manifest_path: Path | None = None,
) -> list[dict[str, Any]]:
    manifest = _manifest_source_map(manifest_path) if manifest_path is not None else None
    sources: list[dict[str, Any]] = []
    with sequence_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload_raw: Any = json.loads(line)
            if not isinstance(payload_raw, dict):
                raise ValueError(f"sequence line {line_number} is not an object")
            payload = cast(dict[str, Any], payload_raw)
            split = str(payload.get("split") or "").strip().casefold()
            group = str(payload.get("independent_group") or "").strip()
            features_raw = payload.get("features")
            if split not in SPLIT_NAMES or not group:
                raise ValueError(f"sequence line {line_number} lacks preserved split metadata")
            if not isinstance(features_raw, list):
                raise ValueError(f"sequence line {line_number} has invalid feature rows")
            feature_items = cast(list[object], features_raw)
            if not all(isinstance(row, dict) for row in feature_items):
                raise ValueError(f"sequence line {line_number} has invalid feature rows")
            payload["features"] = [cast(dict[str, Any], row) for row in feature_items]
            if manifest is not None:
                identity = _manifest_identity(payload, manifest, relative_to=sequence_path.parent)
                if identity is None:
                    raise ValueError(
                        f"sequence source is absent from split manifest: {payload.get('source_path')!r}"
                    )
                if identity != (split, group):
                    raise ValueError(
                        "sequence split metadata differs from the manifest for "
                        f"{payload.get('source_path')!r}: {(split, group)!r} != {identity!r}"
                    )
            sources.append(payload)
    if not sources:
        raise ValueError(f"no sequence sources found in {sequence_path}")
    validate_group_separation(sources)
    return sources


def _canonical_group(group: str) -> str:
    parts = group.split(":", 1)
    if len(parts) == 2 and parts[0].casefold() in SPLIT_NAMES:
        return parts[1].casefold()
    return group.casefold()


def validate_group_separation(sources: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    group_splits: dict[str, set[str]] = defaultdict(set)
    source_splits: dict[str, set[str]] = defaultdict(set)
    for row in sources:
        split = str(row.get("split") or "").strip().casefold()
        group = str(row.get("independent_group") or "").strip()
        if split not in SPLIT_NAMES or not group:
            raise ValueError("each source must retain a valid split and independent_group")
        group_splits[_canonical_group(group)].add(split)
        source_value = row.get("source") or row.get("source_path")
        keys = _path_keys(source_value)
        if not keys:
            raise ValueError("each source must retain a stable source identity")
        source_splits[keys[0]].add(split)

    leaked_groups = sorted(group for group, splits in group_splits.items() if len(splits) > 1)
    leaked_sources = sorted(source for source, splits in source_splits.items() if len(splits) > 1)
    if leaked_groups or leaked_sources:
        raise ValueError(
            "train/validation/test leakage detected: "
            f"groups={leaked_groups[:5]!r}, sources={leaked_sources[:5]!r}"
        )
    return {
        "sources": len(source_splits),
        "independent_groups": len(group_splits),
    }


def _evenly_spaced(values: Sequence[int], limit: int) -> list[int]:
    if limit <= 0 or len(values) <= limit:
        return list(values)
    if limit == 1:
        return [values[-1]]
    selected = {
        round(index * (len(values) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [values[index] for index in sorted(selected)]


def _source_identity(source: Mapping[str, Any]) -> str:
    return str(source.get("source") or source.get("source_path") or "").strip()


def _window_id(source: str, cut_point: int, horizon: int) -> str:
    payload = f"{source.casefold()}|{cut_point}|{horizon}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _context_candidates(source: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    history = source.get("scene_context_history")
    if isinstance(history, list):
        for candidate in cast(list[object], history):
            if isinstance(candidate, Mapping):
                yield cast(Mapping[str, Any], candidate)
    candidate = source.get("scene_context")
    if isinstance(candidate, Mapping):
        yield cast(Mapping[str, Any], candidate)


def _select_causal_context(
    source: Mapping[str, Any], history_end_index: int
) -> Mapping[str, Any] | None:
    eligible: list[tuple[int, Mapping[str, Any]]] = []
    for candidate in _context_candidates(source):
        raw_as_of = candidate.get("as_of_index")
        if raw_as_of is None:
            continue
        try:
            as_of = int(raw_as_of)
        except (TypeError, ValueError):
            continue
        if as_of <= history_end_index:
            eligible.append((as_of, candidate))
    if not eligible:
        return None
    return max(eligible, key=lambda item: item[0])[1]


def load_teacher_predictions(path: Path) -> dict[str, dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row_raw: Any = json.loads(line)
            if not isinstance(row_raw, dict):
                raise ValueError(f"teacher line {line_number} lacks window_id")
            row = cast(dict[str, Any], row_raw)
            if not row.get("window_id"):
                raise ValueError(f"teacher line {line_number} lacks window_id")
            window_id = str(row["window_id"])
            if window_id in predictions:
                raise ValueError(f"duplicate teacher window_id: {window_id}")
            predictions[window_id] = row
    return predictions


def _validated_teacher(
    window: Mapping[str, Any],
    teacher_rows: Mapping[str, Mapping[str, Any]] | None,
) -> list[list[float]] | None:
    if not teacher_rows:
        return None
    teacher = teacher_rows.get(str(window["window_id"]))
    if teacher is None:
        return None
    cut_point = int(window["cut_point"])
    history_end = int(window["history_end_index"])
    if int(teacher.get("cut_point", -1)) != cut_point:
        raise ValueError(f"teacher cut mismatch for {window['window_id']}")
    if int(teacher.get("as_of_index", -1)) != history_end:
        raise ValueError("teacher prediction is not anchored to the final history row")
    teacher_source = str(teacher.get("source") or "")
    if teacher_source and teacher_source.casefold() != str(window["source"]).casefold():
        raise ValueError(f"teacher source mismatch for {window['window_id']}")
    values = teacher.get("close_quantiles", teacher.get("quantiles"))
    horizon = int(window["horizon"])
    if not isinstance(values, list):
        raise ValueError(f"teacher horizon mismatch for {window['window_id']}")
    events = cast(list[object], values)
    if len(events) != horizon:
        raise ValueError(f"teacher horizon mismatch for {window['window_id']}")
    parsed: list[list[float]] = []
    for event_raw in events:
        if not isinstance(event_raw, list):
            raise ValueError(f"teacher quantile shape mismatch for {window['window_id']}")
        event = cast(list[Any], event_raw)
        if len(event) != len(QUANTILE_LEVELS):
            raise ValueError(f"teacher quantile shape mismatch for {window['window_id']}")
        quantiles = [_finite_float(value, float("nan")) for value in event]
        if not all(math.isfinite(value) for value in quantiles):
            raise ValueError(f"teacher contains non-finite values for {window['window_id']}")
        parsed.append(sorted(quantiles))
    return parsed


def build_causal_windows(
    sources: Sequence[Mapping[str, Any]],
    *,
    sequence_length: int = 96,
    horizon: int = DEFAULT_HORIZON,
    minimum_history: int = 24,
    max_windows_per_source: int = 0,
    teacher_rows: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if sequence_length <= 0 or minimum_history <= 0 or horizon <= 0:
        raise ValueError("sequence_length, minimum_history, and horizon must be positive")
    if minimum_history > sequence_length:
        raise ValueError("minimum_history cannot exceed sequence_length")
    validate_group_separation(sources)
    windows: list[dict[str, Any]] = []
    for source in sources:
        features_raw = source.get("features")
        if not isinstance(features_raw, list):
            continue
        feature_items = cast(list[object], features_raw)
        if not all(isinstance(row, Mapping) for row in feature_items):
            continue
        features = [cast(Mapping[str, Any], row) for row in feature_items]
        eligible_cuts = list(range(minimum_history, len(features) - horizon + 1))
        for cut_point in _evenly_spaced(eligible_cuts, max_windows_per_source):
            history_start = max(0, cut_point - sequence_length)
            history_end = cut_point - 1
            identity = _source_identity(source)
            descriptor: dict[str, Any] = {
                "window_id": _window_id(identity, cut_point, horizon),
                "source": identity,
                "source_row": source,
                "split": str(source["split"]).casefold(),
                "independent_group": str(source["independent_group"]),
                "history_start_index": history_start,
                "history_end_index": history_end,
                "target_start_index": cut_point,
                "target_end_index": cut_point + horizon - 1,
                "input_indices": tuple(range(history_start, cut_point)),
                "target_indices": tuple(range(cut_point, cut_point + horizon)),
                "cut_point": cut_point,
                "horizon": horizon,
                "sequence_length": sequence_length,
            }
            context = _select_causal_context(source, history_end)
            descriptor["suite_context"] = context
            descriptor["suite_context_as_of_index"] = (
                int(context["as_of_index"]) if context is not None else None
            )
            descriptor["teacher_quantiles"] = _validated_teacher(descriptor, teacher_rows)
            windows.append(descriptor)
    if not windows:
        raise ValueError("no causal windows can be built from the supplied sequences")
    return windows


def _bbox(row: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    value = row.get("bbox")
    if not isinstance(value, (list, tuple)):
        return None
    coordinates = cast(Sequence[Any], value)
    if len(coordinates) != 4:
        return None
    parsed = tuple(_finite_float(item, float("nan")) for item in coordinates)
    if not all(math.isfinite(item) for item in parsed):
        return None
    return parsed[0], parsed[1], parsed[2], parsed[3]


def encode_history_rows(
    history: Sequence[Mapping[str, Any]], sequence_length: int
) -> tuple[Tensor, Tensor]:
    if not history:
        raise ValueError("history cannot be empty")
    history = history[-sequence_length:]
    locations = [_finite_float(row.get("relative_price_location")) for row in history]
    ranges = [max(0.0, _finite_float(row.get("range_norm"))) for row in history]
    deltas = [abs(right - left) for left, right in zip(locations, locations[1:])]
    positive_scale = [value for value in (*ranges, *deltas) if value > 1.0e-12]
    scale = max(1.0e-6, statistics.median(positive_scale) if positive_scale else 1.0e-6)
    anchor = locations[-1]

    boxes = [_bbox(row) for row in history]
    observed_boxes = [box for box in boxes if box is not None]
    x_min = min(box[0] for box in observed_boxes) if observed_boxes else 0.0
    x_max = max(box[2] for box in observed_boxes) if observed_boxes else float(len(history))
    y_min = min(box[1] for box in observed_boxes) if observed_boxes else 0.0
    y_max = max(box[3] for box in observed_boxes) if observed_boxes else 1.0
    widths = [box[2] - box[0] for box in observed_boxes]
    heights = [box[3] - box[1] for box in observed_boxes]
    median_width = max(1.0e-9, statistics.median(widths) if widths else 1.0)
    median_height = max(1.0e-9, statistics.median(heights) if heights else 1.0)

    numeric_rows: list[list[float]] = []
    previous_close = locations[0]
    for position, (row, close, candle_range, box) in enumerate(
        zip(history, locations, ranges, boxes)
    ):
        direction = _clip(_finite_float(row.get("direction_value")), -1.0, 1.0)
        body_fraction = _clip(_finite_float(row.get("body_norm")), 0.0, 1.0)
        upper_fraction = _clip(_finite_float(row.get("upper_wick_norm")), 0.0, 1.0)
        lower_fraction = _clip(_finite_float(row.get("lower_wick_norm")), 0.0, 1.0)
        body = candle_range * body_fraction
        upper_wick = candle_range * upper_fraction
        lower_wick = candle_range * lower_fraction
        if direction > 0.0:
            open_price = close - body
            high = close + upper_wick
            low = open_price - lower_wick
        elif direction < 0.0:
            open_price = close + body
            high = open_price + upper_wick
            low = close - lower_wick
        else:
            open_price = close
            high = close + upper_wick
            low = close - lower_wick

        geometry_missing = box is None
        if box is None:
            center_x = position / max(1, len(history) - 1)
            center_y = 0.5
            width_norm = 1.0
            height_norm = 1.0
        else:
            x0, y0, x1, y1 = box
            center_x = ((x0 + x1) * 0.5 - x_min) / max(1.0e-9, x_max - x_min)
            center_y = ((y0 + y1) * 0.5 - y_min) / max(1.0e-9, y_max - y_min)
            width_norm = (x1 - x0) / median_width
            height_norm = (y1 - y0) / median_height

        base = {
            "open_offset": _clip((open_price - anchor) / scale, -32.0, 32.0),
            "high_offset": _clip((high - anchor) / scale, -32.0, 32.0),
            "low_offset": _clip((low - anchor) / scale, -32.0, 32.0),
            "close_offset": _clip((close - anchor) / scale, -32.0, 32.0),
            "close_delta": _clip(
                _finite_float(row.get("relative_price_delta_scaled"), (close - previous_close) / scale),
                -32.0,
                32.0,
            ),
            "range_scaled": _clip(
                _finite_float(row.get("range_vs_recent"), candle_range / scale), 0.0, 32.0
            ),
            "body_scaled": _clip(
                _finite_float(row.get("body_vs_recent"), body / scale), 0.0, 32.0
            ),
            "upper_wick_scaled": _clip(upper_wick / scale, 0.0, 32.0),
            "lower_wick_scaled": _clip(lower_wick / scale, 0.0, 32.0),
            "body_fraction": body_fraction,
            "upper_wick_fraction": upper_fraction,
            "lower_wick_fraction": lower_fraction,
            "direction_value": direction,
            "relative_position": position / max(1, len(history) - 1),
            "elapsed_steps": float(position - (len(history) - 1)),
            "timestamp_gap_steps": 0.0,
            "center_x_norm": _clip(center_x, 0.0, 1.0),
            "center_y_norm": _clip(center_y, 0.0, 1.0),
            "width_vs_median": _clip(width_norm, 0.0, 16.0),
            "height_vs_median": _clip(height_norm, 0.0, 16.0),
            "parse_confidence": _clip(_finite_float(row.get("parse_confidence")), 0.0, 1.0),
            "ohlc_inferred": 1.0,
        }
        missing = {name: 0.0 for name in BASE_CANDLE_SCHEMA}
        missing["timestamp_gap_steps"] = 1.0
        for name in ("center_x_norm", "center_y_norm", "width_vs_median", "height_vs_median"):
            missing[name] = float(geometry_missing)
        missing["parse_confidence"] = float(row.get("parse_confidence") is None)
        numeric_rows.append(
            [base[name] for name in BASE_CANDLE_SCHEMA]
            + [missing[name] for name in BASE_CANDLE_SCHEMA]
        )
        previous_close = close

    sequence = torch.zeros((sequence_length, len(CANDLE_NUMERIC_SCHEMA)), dtype=torch.float32)
    mask = torch.zeros(sequence_length, dtype=torch.bool)
    values = torch.tensor(numeric_rows, dtype=torch.float32)
    sequence[-len(values) :] = values
    mask[-len(values) :] = True
    return sequence, mask


def _context_tensors(context_candidate: Mapping[str, Any] | None) -> tuple[Tensor, Tensor]:
    width = len(CONTEXT_NUMERIC_SCHEMA)
    values = torch.zeros(width, dtype=torch.float32)
    missing = torch.ones(width, dtype=torch.bool)
    if context_candidate is None:
        return values, missing
    context = context_candidate.get("context", context_candidate)
    if not isinstance(context, Mapping):
        return values, missing
    context_payload = cast(Mapping[str, Any], context)
    schema = context_payload.get("numeric_schema")
    numeric_values = context_payload.get("numeric_values")
    if schema != list(CONTEXT_NUMERIC_SCHEMA) or not isinstance(numeric_values, list):
        raise ValueError("suite context must use the stable V3 numeric schema")
    numeric_items = cast(list[Any], numeric_values)
    if len(numeric_items) != width:
        raise ValueError("suite context numeric width does not match its schema")
    for index, raw_value in enumerate(numeric_items):
        parsed = _finite_float(raw_value, float("nan"))
        if math.isfinite(parsed):
            values[index] = parsed
            missing[index] = False
    return values, missing


def materialize_window(window: Mapping[str, Any]) -> dict[str, Tensor | str | int]:
    source_raw = window["source_row"]
    if not isinstance(source_raw, Mapping):
        raise ValueError("window does not reference valid source features")
    source = cast(Mapping[str, Any], source_raw)
    features_raw = source.get("features")
    if not isinstance(features_raw, list):
        raise ValueError("window does not reference valid source features")
    feature_items = cast(list[object], features_raw)
    if not all(isinstance(row, Mapping) for row in feature_items):
        raise ValueError("window does not reference valid source features")
    features = [cast(Mapping[str, Any], row) for row in feature_items]
    history_start = int(window["history_start_index"])
    cut_point = int(window["cut_point"])
    horizon = int(window["horizon"])
    sequence_length = int(window["sequence_length"])
    history = features[history_start:cut_point]
    future = features[cut_point : cut_point + horizon]
    if len(future) != horizon:
        raise ValueError("window target is shorter than its declared horizon")
    candles, candle_mask = encode_history_rows(history, sequence_length)

    future_deltas = torch.tensor(
        [_finite_float(row.get("relative_price_delta_scaled")) for row in future],
        dtype=torch.float32,
    )
    target_close_path = future_deltas.cumsum(dim=0)
    target_upper_spans = torch.tensor(
        [
            max(0.0, _finite_float(row.get("range_vs_recent")))
            * _clip(_finite_float(row.get("upper_wick_norm")), 0.0, 1.0)
            for row in future
        ],
        dtype=torch.float32,
    )
    target_lower_spans = torch.tensor(
        [
            max(0.0, _finite_float(row.get("range_vs_recent")))
            * _clip(_finite_float(row.get("lower_wick_norm")), 0.0, 1.0)
            for row in future
        ],
        dtype=torch.float32,
    )
    hold_threshold = 0.02
    target_movement = torch.where(
        future_deltas < -hold_threshold,
        torch.zeros(horizon, dtype=torch.long),
        torch.where(
            future_deltas > hold_threshold,
            torch.full((horizon,), 2, dtype=torch.long),
            torch.ones(horizon, dtype=torch.long),
        ),
    )
    static_values, static_missing_mask = _context_tensors(window.get("suite_context"))

    teacher_values = window.get("teacher_quantiles")
    if isinstance(teacher_values, list):
        teacher_quantiles = torch.tensor(teacher_values, dtype=torch.float32)
        teacher_mask = torch.ones(horizon, dtype=torch.bool)
    else:
        teacher_quantiles = torch.zeros((horizon, 3), dtype=torch.float32)
        teacher_mask = torch.zeros(horizon, dtype=torch.bool)

    last_delta = candles[candle_mask, BASE_CANDLE_SCHEMA.index("close_delta")][-1]
    steps = torch.arange(1, horizon + 1, dtype=torch.float32)
    return {
        "candles": candles,
        "candle_mask": candle_mask,
        "static_values": static_values,
        "static_missing_mask": static_missing_mask,
        "target_close_path": target_close_path,
        "target_upper_spans": target_upper_spans,
        "target_lower_spans": target_lower_spans,
        "target_movement": target_movement,
        "target_mask": torch.ones(horizon, dtype=torch.bool),
        "teacher_quantiles": teacher_quantiles,
        "teacher_mask": teacher_mask,
        "persistence_path": torch.zeros(horizon, dtype=torch.float32),
        "last_delta_path": steps * last_delta,
        "window_id": str(window["window_id"]),
        "cut_point": cut_point,
    }


class SceneWindowDataset(Dataset[dict[str, Tensor | str | int]]):
    def __init__(self, windows: Sequence[Mapping[str, Any]]) -> None:
        self.windows = list(windows)

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, index: int) -> dict[str, Tensor | str | int]:
        return materialize_window(self.windows[index])


def _to_device(batch: Mapping[str, Any], device: torch.device) -> dict[str, Any]:
    return {
        key: value.to(device) if isinstance(value, Tensor) else value
        for key, value in batch.items()
    }


def _balanced_accuracy(prediction: Tensor, target: Tensor, labels: int = 3) -> float:
    recalls: list[float] = []
    for label in range(labels):
        selected = target == label
        if bool(selected.any()):
            recalls.append(float((prediction[selected] == label).float().mean().item()))
    return sum(recalls) / len(recalls) if recalls else 0.0


def _turning_metrics(prediction: Tensor, target: Tensor) -> dict[str, float]:
    if prediction.shape[1] < 3:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    predicted_slopes = prediction[:, 1:] - prediction[:, :-1]
    target_slopes = target[:, 1:] - target[:, :-1]
    predicted_turn = predicted_slopes[:, 1:] * predicted_slopes[:, :-1] < 0.0
    target_turn = target_slopes[:, 1:] * target_slopes[:, :-1] < 0.0
    true_positive = int((predicted_turn & target_turn).sum())
    false_positive = int((predicted_turn & ~target_turn).sum())
    false_negative = int((~predicted_turn & target_turn).sum())
    accuracy = float((predicted_turn == target_turn).float().mean().item())
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2.0 * precision * recall / max(1.0e-12, precision + recall)
    return {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1}


def _path_metrics(prediction: Tensor, target: Tensor) -> dict[str, object]:
    absolute_error = (prediction - target).abs()
    endpoint_prediction = prediction[:, -1]
    endpoint_target = target[:, -1]
    endpoint_direction = torch.sign(endpoint_prediction)
    endpoint_truth = torch.sign(endpoint_target)
    return {
        "mae": float(absolute_error.mean().item()),
        "mae_by_horizon": [
            float(value.item()) for value in absolute_error.mean(dim=0)
        ],
        "endpoint_mae": float((endpoint_prediction - endpoint_target).abs().mean().item()),
        "endpoint_direction_accuracy": float(
            (endpoint_direction == endpoint_truth).float().mean().item()
        ),
        "turning": _turning_metrics(prediction, target),
    }


@torch.no_grad()
def evaluate_model(
    model: ScenePatchForecasterV3,
    windows: Sequence[Mapping[str, Any]],
    *,
    batch_size: int,
    device: torch.device,
) -> dict[str, Any]:
    if not windows:
        raise ValueError("evaluation requires at least one window")
    loader = DataLoader(SceneWindowDataset(windows), batch_size=batch_size, shuffle=False)
    model.eval()
    quantile_batches: list[Tensor] = []
    movement_batches: list[Tensor] = []
    target_path_batches: list[Tensor] = []
    target_movement_batches: list[Tensor] = []
    persistence_batches: list[Tensor] = []
    last_delta_batches: list[Tensor] = []
    for raw_batch in loader:
        batch = _to_device(raw_batch, device)
        outputs = model(
            batch["candles"],
            batch["candle_mask"],
            batch["static_values"],
            batch["static_missing_mask"],
        )
        quantile_batches.append(outputs["close_quantiles"].cpu())
        movement_batches.append(outputs["movement_logits"].argmax(dim=-1).cpu())
        target_path_batches.append(batch["target_close_path"].cpu())
        target_movement_batches.append(batch["target_movement"].cpu())
        persistence_batches.append(batch["persistence_path"].cpu())
        last_delta_batches.append(batch["last_delta_path"].cpu())

    quantiles = torch.cat(quantile_batches)
    predicted_path = quantiles[..., 1]
    target_path = torch.cat(target_path_batches)
    predicted_movement = torch.cat(movement_batches)
    target_movement = torch.cat(target_movement_batches)
    persistence = torch.cat(persistence_batches)
    last_delta = torch.cat(last_delta_batches)
    movement_accuracy = float((predicted_movement == target_movement).float().mean().item())
    coverage = (target_path >= quantiles[..., 0]) & (target_path <= quantiles[..., 2])
    return {
        "samples": len(windows),
        "movement": {
            "accuracy": movement_accuracy,
            "balanced_accuracy": _balanced_accuracy(
                predicted_movement.reshape(-1), target_movement.reshape(-1)
            ),
            "labels": list(MOVEMENT_LABELS),
        },
        "path": _path_metrics(predicted_path, target_path),
        "interval": {
            "nominal_coverage": 0.80,
            "coverage": float(coverage.float().mean().item()),
            "coverage_by_horizon": [float(value) for value in coverage.float().mean(dim=0)],
            "mean_width": float((quantiles[..., 2] - quantiles[..., 0]).mean().item()),
        },
        "baselines": {
            "persistence": _path_metrics(persistence, target_path),
            "last_delta": _path_metrics(last_delta, target_path),
        },
    }


def train_model(
    train_windows: Sequence[Mapping[str, Any]],
    validation_windows: Sequence[Mapping[str, Any]],
    *,
    config: ScenePatchForecasterConfig,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    patience: int,
    seed: int,
    device: torch.device,
) -> tuple[ScenePatchForecasterV3, list[dict[str, float]]]:
    if not train_windows or not validation_windows:
        raise ValueError("training and validation windows are both required")
    random.seed(seed)
    manual_seed = cast(Callable[[int], object], getattr(torch, "manual_seed"))
    manual_seed(seed)
    model = ScenePatchForecasterV3(config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), learning_rate, weight_decay=weight_decay
    )
    train_loader = DataLoader(
        SceneWindowDataset(train_windows),
        batch_size=batch_size,
        shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    validation_loader = DataLoader(
        SceneWindowDataset(validation_windows), batch_size=batch_size, shuffle=False
    )
    best_state: dict[str, Tensor] | None = None
    best_validation = float("inf")
    stale_epochs = 0
    history: list[dict[str, float]] = []

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss_sum = 0.0
        train_samples = 0
        for raw_batch in train_loader:
            batch = _to_device(raw_batch, device)
            optimizer.zero_grad(set_to_none=True)
            outputs = model(
                batch["candles"],
                batch["candle_mask"],
                batch["static_values"],
                batch["static_missing_mask"],
            )
            loss, _ = scene_forecast_loss(
                outputs,
                batch["target_close_path"],
                batch["target_movement"],
                target_mask=batch["target_mask"],
                target_upper_spans=batch["target_upper_spans"],
                target_lower_spans=batch["target_lower_spans"],
                teacher_quantiles=batch["teacher_quantiles"],
                teacher_mask=batch["teacher_mask"],
            )
            backward = cast(Callable[[], None], getattr(loss, "backward"))
            backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer_step = cast(Callable[[], object], getattr(optimizer, "step"))
            optimizer_step()
            sample_count = int(batch["candles"].shape[0])
            train_loss_sum += float(loss.item()) * sample_count
            train_samples += sample_count

        model.eval()
        validation_loss_sum = 0.0
        validation_samples = 0
        with torch.no_grad():
            for raw_batch in validation_loader:
                batch = _to_device(raw_batch, device)
                outputs = model(
                    batch["candles"],
                    batch["candle_mask"],
                    batch["static_values"],
                    batch["static_missing_mask"],
                )
                loss, _ = scene_forecast_loss(
                    outputs,
                    batch["target_close_path"],
                    batch["target_movement"],
                    target_mask=batch["target_mask"],
                    target_upper_spans=batch["target_upper_spans"],
                    target_lower_spans=batch["target_lower_spans"],
                    teacher_quantiles=batch["teacher_quantiles"],
                    teacher_mask=batch["teacher_mask"],
                )
                sample_count = int(batch["candles"].shape[0])
                validation_loss_sum += float(loss.item()) * sample_count
                validation_samples += sample_count

        train_loss = train_loss_sum / max(1, train_samples)
        validation_loss = validation_loss_sum / max(1, validation_samples)
        history.append(
            {"epoch": float(epoch), "train_loss": train_loss, "validation_loss": validation_loss}
        )
        if validation_loss < best_validation - 1.0e-6:
            best_validation = validation_loss
            best_state = {
                name: parameter.detach().cpu().clone()
                for name, parameter in model.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                break

    if best_state is None:
        raise RuntimeError("training did not produce a finite validation checkpoint")
    model.load_state_dict(best_state)
    return model.to(device), history


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def publish_atomic_generation(
    model: ScenePatchForecasterV3,
    *,
    output_directory: Path,
    config_payload: Mapping[str, Any],
    metrics_payload: Mapping[str, Any],
) -> Path:
    """Publish one immutable generation, then atomically swap only its manifest pointer."""

    output_directory.mkdir(parents=True, exist_ok=True)
    generations = output_directory / ".scene_forecaster_v3.generations"
    generations.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    generation_id = f"{timestamp}-{uuid.uuid4().hex[:10]}"
    staging = generations / f".{generation_id}.staging"
    final_generation = generations / generation_id
    staging.mkdir()

    model_path = staging / ARTIFACT_FILENAME
    config_path = staging / CONFIG_FILENAME
    metrics_path = staging / METRICS_FILENAME
    torch.save(
        {
            "artifact_schema": SCENE_FORECASTER_SCHEMA_VERSION,
            "feature_schema_fingerprint": FEATURE_SCHEMA_FINGERPRINT,
            "config": model.config.to_dict(),
            "state_dict": {
                name: value.detach().cpu() for name, value in model.state_dict().items()
            },
        },
        model_path,
    )
    _write_json(config_path, config_payload)
    _write_json(metrics_path, metrics_payload)
    artifacts: dict[str, dict[str, str | int]] = {}
    for path in (model_path, config_path, metrics_path):
        artifacts[path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    os.replace(staging, final_generation)

    manifest: dict[str, Any] = {
        "schema_version": SCENE_FORECASTER_SCHEMA_VERSION,
        "generation_id": generation_id,
        "generation_path": str(final_generation.relative_to(output_directory)),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts,
    }
    manifest_path = output_directory / MANIFEST_FILENAME
    temporary_manifest = output_directory / f".{MANIFEST_FILENAME}.{uuid.uuid4().hex}.tmp"
    _write_json(temporary_manifest, manifest)
    os.replace(temporary_manifest, manifest_path)
    return manifest_path


def _resolve_device(requested: str) -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but is not available")
    return device


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit or explicitly train the causal PhoenixGuard V3 scene forecaster."
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_SEQUENCE_DATA)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--teacher-predictions", type=Path)
    parser.add_argument("--sequence-length", type=int, default=96)
    parser.add_argument("--minimum-history", type=int, default=24)
    parser.add_argument("--max-windows-per-source", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--learning-rate", type=float, default=3.0e-4)
    parser.add_argument("--weight-decay", type=float, default=1.0e-4)
    parser.add_argument("--seed", type=int, default=808)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--train", action="store_true", help="Run optimization in memory.")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="After successful explicit training, atomically publish a generation.",
    )
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.publish and not args.train:
        raise SystemExit("--publish requires the explicit --train flag")
    teacher_rows = (
        load_teacher_predictions(args.teacher_predictions) if args.teacher_predictions else None
    )
    sources = load_sequence_sources(args.data, manifest_path=args.manifest)
    split_audit = validate_group_separation(sources)
    windows = build_causal_windows(
        sources,
        sequence_length=args.sequence_length,
        horizon=DEFAULT_HORIZON,
        minimum_history=args.minimum_history,
        max_windows_per_source=args.max_windows_per_source,
        teacher_rows=teacher_rows,
    )
    windows_by_split = {
        split: [window for window in windows if window["split"] == split]
        for split in SPLIT_NAMES
    }
    audit_payload = {
        "artifact_schema": SCENE_FORECASTER_SCHEMA_VERSION,
        "feature_schema_fingerprint": FEATURE_SCHEMA_FINGERPRINT,
        "source_file": str(args.data.resolve()),
        "split_manifest": str(args.manifest.resolve()),
        "sources": Counter(str(source["split"]) for source in sources),
        "windows": {split: len(rows) for split, rows in windows_by_split.items()},
        "group_audit": split_audit,
        "horizon": DEFAULT_HORIZON,
        "causal_policy": {
            "input": "closed feature rows with index strictly before cut_point",
            "target": "the next 12 feature rows, used only as labels",
            "suite_context": "latest stable-schema context with as_of_index <= history_end",
            "teacher": "optional forecast requiring exact source, cut, and history_end anchor",
            "future_images_or_suite_payloads": "never loaded",
        },
    }
    if not args.train:
        print(json.dumps(audit_payload, indent=2, sort_keys=True, default=dict))
        return 0

    device = _resolve_device(args.device)
    config = ScenePatchForecasterConfig(horizon=DEFAULT_HORIZON)
    model, training_history = train_model(
        windows_by_split["train"],
        windows_by_split["val"],
        config=config,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        patience=args.patience,
        seed=args.seed,
        device=device,
    )
    validation_metrics = evaluate_model(
        model,
        windows_by_split["val"],
        batch_size=args.batch_size,
        device=device,
    )
    test_metrics = evaluate_model(
        model,
        windows_by_split["test"],
        batch_size=args.batch_size,
        device=device,
    )
    metrics_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "training_history": training_history,
        "validation": validation_metrics,
        "test": test_metrics,
        "audit": audit_payload,
    }
    config_payload = {
        "artifact_schema": SCENE_FORECASTER_SCHEMA_VERSION,
        "feature_schema_fingerprint": FEATURE_SCHEMA_FINGERPRINT,
        "model": config.to_dict(),
        "candle_numeric_schema": list(CANDLE_NUMERIC_SCHEMA),
        "context_numeric_schema": list(CONTEXT_NUMERIC_SCHEMA),
        "quantile_levels": list(QUANTILE_LEVELS),
        "movement_labels": list(MOVEMENT_LABELS),
        "training": {
            "seed": args.seed,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
            "epochs_requested": args.epochs,
            "patience": args.patience,
        },
        "causal_policy": audit_payload["causal_policy"],
    }
    if args.publish:
        manifest_path = publish_atomic_generation(
            model,
            output_directory=args.output_directory,
            config_payload=config_payload,
            metrics_payload=metrics_payload,
        )
        metrics_payload["published_manifest"] = str(manifest_path.resolve())
    print(json.dumps(metrics_payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
