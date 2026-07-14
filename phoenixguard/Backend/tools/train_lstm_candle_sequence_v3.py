from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import csv
import hashlib
import json
import math
import random
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_BOOTSTRAP))

from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_HORIZON_STEPS,
    DEFAULT_METRICS_PATH,
    DEFAULT_MODEL_PATH,
    DEFAULT_SEQUENCE_LENGTH,
    FEATURE_SCHEMA,
    LSTM_CANDLE_SEQUENCE_VERSION,
    MAX_PRICE_DELTA,
    PLAY_LABELS,
    PREDICTION_FEATURE_INDICES,
    PREDICTION_SCHEMA,
    causal_chart_context_tensor,
    candle_sequence_features,
    create_lstm_candle_sequence_model,
    feature_vector,
)
from phoenixguard.decision.retrieval_forecast_v3 import (
    build_retrieval_bank_v3,
    retrieve_forecast_v3,
)
from phoenixguard.decision.selective_risk_v3 import (
    calibration_metrics,
    choose_class_conditional_thresholds,
    evaluate_class_conditional_selection,
    fit_temperature,
    source_cluster_accuracy_interval,
    temperature_softmax,
)
from phoenixguard.paths import PROJECT_ROOT
from phoenixguard.vision.candle_palette_v3 import extract_candle_tracks_adaptive_v3


DEFAULT_RAW_MEMORY_ROOT = PROJECT_ROOT / "808 Memory"
DEFAULT_SPLIT_MANIFEST = PROJECT_ROOT / "data_splits" / "split_manifest.csv"
DEFAULT_SEQUENCE_CACHE = PROJECT_ROOT / "data_splits" / "lstm_raw_candle_sequences_v3.jsonl"
DEFAULT_TRAINING_CHECKPOINT = PROJECT_ROOT / "models" / "lstm_candle_sequence_v3_training.pt"
EXTRACTOR_SCHEMA_VERSION = "PG_ADAPTIVE_PALETTE_OHLC_EXTRACTOR_V3_20260713"
CHART_CONTEXT_SIZE = (96, 192)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
SIDE_TO_INDEX = {"BUY": 0, "SELL": 1}
INDEX_TO_SIDE = {0: "BUY", 1: "SELL"}
PLAY_TO_INDEX = {label: index for index, label in enumerate(PLAY_LABELS)}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _production_readiness_evidence(
    *,
    source_counts: Mapping[str, Any],
    test_metrics: Mapping[str, Any],
    risk_control: Mapping[str, Any],
    target_precision: float,
    minimum_predictions: int,
) -> dict[str, Any]:
    """Build a fail-closed release decision from the untouched test split.

    Validation chooses retrieval and abstention settings. This function never
    retunes them: it only asks whether their locked-test performance provides
    enough class-balanced and source-clustered evidence for production use.
    Point estimates alone are deliberately insufficient for an 85% claim.
    """

    required_precision = max(0.85, min(1.0, _finite_float(target_precision, 0.85)))
    required_per_class = max(1, int(minimum_predictions))
    test_selection = _mapping(risk_control.get("test_selection"))
    per_class = _mapping(test_selection.get("per_class"))
    buy = _mapping(per_class.get("BUY"))
    sell = _mapping(per_class.get("SELL"))
    selected_cluster = _mapping(risk_control.get("test_selected_source_cluster_accuracy_95"))
    direction_cluster = _mapping(test_metrics.get("source_cluster_accuracy_95"))

    confusion = test_metrics.get("confusion_matrix")
    recalls: dict[str, float] = {"BUY": 0.0, "SELL": 0.0}
    if isinstance(confusion, Sequence) and not isinstance(confusion, (str, bytes, bytearray)):
        matrix = cast(Sequence[Any], confusion)
        for index, side in INDEX_TO_SIDE.items():
            if (
                index >= len(matrix)
                or not isinstance(matrix[index], Sequence)
                or isinstance(matrix[index], (str, bytes, bytearray))
            ):
                continue
            row = cast(Sequence[Any], matrix[index])
            support = sum(max(0.0, _finite_float(value)) for value in row)
            correct = max(0.0, _finite_float(row[index])) if index < len(row) else 0.0
            recalls[side] = correct / support if support > 0.0 else 0.0

    balanced = _finite_float(test_metrics.get("balanced_accuracy"))
    persistence_balanced = _finite_float(test_metrics.get("persistence_baseline_balanced_accuracy"))
    minimum_selected_sources = 10
    checks = {
        "independent_split_support": (
            int(source_counts.get("train", 0) or 0) >= 100
            and int(source_counts.get("validation", 0) or 0) >= 20
            and int(source_counts.get("test", 0) or 0) >= 20
        ),
        "direction_balanced_accuracy_at_least_52": balanced >= 0.52,
        "direction_beats_persistence_by_one_point": balanced >= persistence_balanced + 0.01,
        "direction_source_cluster_lower_95_above_chance": _finite_float(direction_cluster.get("lower_95")) > 0.50,
        "both_direction_class_recalls_at_least_chance": all(value >= 0.50 for value in recalls.values()),
        "endpoint_path_direction_accuracy_at_least_55": _finite_float(
            test_metrics.get("endpoint_path_direction_accuracy")
        )
        >= 0.55,
        "interval_90_coverage_at_least_70": _finite_float(test_metrics.get("interval_90_coverage")) >= 0.70,
        "locked_selective_accuracy_at_target": _finite_float(test_selection.get("accuracy"))
        >= required_precision,
        "locked_selective_macro_precision_at_target": _finite_float(
            test_selection.get("macro_predicted_class_precision")
        )
        >= required_precision,
        "locked_selective_overall_wilson_lower_at_target": _finite_float(test_selection.get("wilson_lower_95"))
        >= required_precision,
        "locked_selective_each_class_has_minimum_support": all(
            int(row.get("selected", 0) or 0) >= required_per_class for row in (buy, sell)
        ),
        "locked_selective_each_class_precision_at_target": all(
            _finite_float(row.get("precision")) >= required_precision for row in (buy, sell)
        ),
        "locked_selective_each_class_wilson_lower_at_target": all(
            _finite_float(row.get("wilson_lower_95")) >= required_precision for row in (buy, sell)
        ),
        "locked_selective_spans_ten_source_clusters": int(selected_cluster.get("sources", 0) or 0)
        >= minimum_selected_sources,
        "locked_selective_source_cluster_lower_95_at_target": _finite_float(selected_cluster.get("lower_95"))
        >= required_precision,
    }
    failed = [name for name, passed in checks.items() if not passed]
    point_selective_pass = bool(
        checks["locked_selective_accuracy_at_target"]
        and checks["locked_selective_macro_precision_at_target"]
        and checks["locked_selective_each_class_has_minimum_support"]
        and checks["locked_selective_each_class_precision_at_target"]
    )
    robust_selective_pass = bool(
        point_selective_pass
        and checks["locked_selective_overall_wilson_lower_at_target"]
        and checks["locked_selective_each_class_wilson_lower_at_target"]
        and checks["locked_selective_spans_ten_source_clusters"]
        and checks["locked_selective_source_cluster_lower_95_at_target"]
    )
    return {
        "production_ready": not failed,
        "checks": checks,
        "failed_checks": failed,
        "locked_test_selective_point_pass": point_selective_pass,
        "locked_test_selective_robust_pass": robust_selective_pass,
        "required_selective_precision": required_precision,
        "minimum_predictions_per_class": required_per_class,
        "minimum_selected_source_clusters": minimum_selected_sources,
        "test_class_recalls": {side: round(value, 6) for side, value in recalls.items()},
        "balanced_accuracy_margin_over_persistence": round(balanced - persistence_balanced, 6),
    }


def _tensor_list(value: torch.Tensor) -> Any:
    return cast(Callable[[], Any], getattr(value, "tolist"))()


def _resolved_key(path: Path | str) -> str:
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _split_manifest_source_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    output: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            split = str(row.get("split") or "train").strip().lower()
            for key in ("source_path", "destination_path"):
                value = row.get(key)
                if value:
                    output[_resolved_key(value)] = split
    return output


def extract_raw_candles(image_path: Path) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    """Extract ordered candle events from a raw suite screenshot.

    This is intentionally candle-centric. Unlike the former implementation, it
    never resizes the entire screenshot into arbitrary vertical columns.
    """
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    image.thumbnail((1920, 1200), Image.Resampling.BILINEAR)
    array = np.asarray(image, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] < 3:
        return [], image.size
    height, width = int(array.shape[0]), int(array.shape[1])
    candles = extract_candle_tracks_adaptive_v3(array, minimum_track_length=6)
    return candles, (width, height)


def image_to_sequence_features(image_path: Path, *, phase: str = "") -> list[dict[str, Any]]:
    candles, image_size = extract_raw_candles(image_path)
    # Training input must come from pixels, not hindsight encoded in a filename.
    return candle_sequence_features(candles, image_size=image_size, sequence_phase=phase)


def _load_sequence_cache(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, Mapping):
                    rows.append(dict(cast(Mapping[str, Any], value)))
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def _write_sequence_cache(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(dict(row), separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def _raw_suite_sequences(
    root: Path,
    *,
    split_manifest_path: Path,
    cache_path: Path,
    rebuild_cache: bool,
) -> list[dict[str, Any]]:
    split_map = _split_manifest_source_map(split_manifest_path)
    images = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    cached_rows = [] if rebuild_cache else _load_sequence_cache(cache_path)
    cached_by_source = {str(row.get("source") or "").casefold(): row for row in cached_rows}
    output: list[dict[str, Any]] = []
    for index, image_path in enumerate(images, start=1):
        key = _resolved_key(image_path)
        stat = image_path.stat()
        cached = _mapping(cached_by_source.get(key))
        if (
            cached
            and int(cached.get("source_size", -1)) == int(stat.st_size)
            and int(cached.get("source_mtime_ns", -1)) == int(stat.st_mtime_ns)
            and str(cached.get("extractor_schema_version") or "") == EXTRACTOR_SCHEMA_VERSION
            and cached.get("features")
        ):
            row = cached
        else:
            features = image_to_sequence_features(image_path)
            row = {
                "source": key,
                "source_path": str(image_path),
                "source_size": int(stat.st_size),
                "source_mtime_ns": int(stat.st_mtime_ns),
                "extractor_schema_version": EXTRACTOR_SCHEMA_VERSION,
                "features": features,
            }
        row["split"] = split_map.get(key, "unassigned").lower()
        output.append(row)
        if index % 50 == 0:
            print(json.dumps({"stage": "raw_candle_extraction", "processed": index, "total": len(images)}), flush=True)
    _write_sequence_cache(cache_path, output)
    return output


def _play_target(history: Sequence[Mapping[str, Any]], future: Sequence[Mapping[str, Any]]) -> int:
    context_rows = history[-min(8, len(history)) :]
    context_move = sum(float(row.get("relative_price_delta_scaled", 0.0)) for row in context_rows)
    if abs(context_move) < 0.05 and history:
        context_move = float(history[-1].get("direction_value", 0.0))
    future_moves = [float(row.get("relative_price_delta_scaled", 0.0)) for row in future]
    net = sum(future_moves) / max(1, len(future_moves))
    if context_move and net * context_move >= 0.04:
        return PLAY_TO_INDEX["CONTINUATION"]
    if context_move and net * context_move <= -0.04:
        return PLAY_TO_INDEX["REVERSAL"]
    return PLAY_TO_INDEX["PULLBACK"]


def _evenly_spaced(values: Sequence[int], limit: int) -> list[int]:
    unique = sorted(set(int(value) for value in values))
    if limit <= 0 or len(unique) <= limit:
        return unique
    if limit == 1:
        return [unique[-1]]
    indices = [round(index * (len(unique) - 1) / (limit - 1)) for index in range(limit)]
    return [unique[index] for index in sorted(set(indices))]


def _causal_windows(
    sequences: Sequence[Mapping[str, Any]],
    *,
    sequence_length: int,
    horizon_steps: int,
    minimum_history: int,
    windows_per_source: int,
    minimum_source_confidence: float,
    maximum_clipped_delta_rate: float,
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for sequence_row in sequences:
        features = [dict(cast(Mapping[str, Any], row)) for row in cast(Sequence[Any], sequence_row.get("features", [])) if isinstance(row, Mapping)]
        if len(features) < minimum_history + horizon_steps:
            continue
        if str(sequence_row.get("split") or "") not in {"train", "val", "valid", "validation", "test"}:
            continue
        parse_values = [float(row.get("parse_confidence", 0.0) or 0.0) for row in features]
        median_confidence = float(np.median(np.asarray(parse_values, dtype=np.float32))) if parse_values else 0.0
        clipped_rate = sum(
            abs(float(row.get("relative_price_delta_scaled", 0.0) or 0.0)) >= 0.999
            for row in features[1:]
        ) / max(1, len(features) - 1)
        if median_confidence < float(minimum_source_confidence) or clipped_rate > float(maximum_clipped_delta_rate):
            continue
        cut_points = _evenly_spaced(
            list(range(int(minimum_history), len(features) - int(horizon_steps) + 1)),
            int(windows_per_source),
        )
        for cut in cut_points:
            history = features[max(0, cut - sequence_length) : cut]
            future = features[cut : cut + horizon_steps]
            matrix = [feature_vector(row) for row in history[-sequence_length:]]
            matrix.extend([[0.0] * len(FEATURE_SCHEMA) for _ in range(max(0, sequence_length - len(matrix)))])
            future_matrix = [feature_vector(row) for row in future]
            history_bbox: Sequence[Any] = (
                cast(Sequence[Any], history[-1].get("bbox", ()))
                if history
                else ()
            )
            chart_cut_x = (
                float(history_bbox[2]) + 1.0
                if len(history_bbox) >= 4
                else 0.0
            )
            source_path = str(sequence_row.get("source_path") or sequence_row.get("source") or "")
            windows.append(
                {
                    "sequence": matrix,
                    "length": min(len(history), sequence_length),
                    "targets": future_matrix,
                    "directions": [SIDE_TO_INDEX["BUY"] if float(row.get("direction_value", 0.0)) >= 0.0 else SIDE_TO_INDEX["SELL"] for row in future],
                    "target_quality": [max(0.15, float(row.get("parse_confidence", 0.0))) for row in future],
                    "play": _play_target(history, future),
                    "split": str(sequence_row.get("split") or "train").lower(),
                    "source": source_path,
                    "cut_point": cut,
                    "chart_cut_x": chart_cut_x,
                    "window_id": hashlib.sha256(f"{source_path}|{cut}".encode("utf-8")).hexdigest()[:20],
                    "source_median_parse_confidence": median_confidence,
                    "source_clipped_delta_rate": clipped_rate,
                }
            )
    return windows


_CHART_CONTEXT_CACHE: dict[tuple[str, int, int, int], torch.Tensor] = {}


def _chart_context_for_row(row: Mapping[str, Any]) -> torch.Tensor:
    source = Path(str(row.get("source") or ""))
    cut_x = int(round(float(row.get("chart_cut_x", 0.0) or 0.0)))
    try:
        mtime_ns = int(source.stat().st_mtime_ns)
    except OSError:
        return torch.zeros((3, *CHART_CONTEXT_SIZE), dtype=torch.float32)
    cache_key = (_resolved_key(source), mtime_ns, cut_x, CHART_CONTEXT_SIZE[1])
    cached = _CHART_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached.clone()
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        image.thumbnail((1920, 1200), Image.Resampling.BILINEAR)
        width, height = image.size
        x0, x1 = int(width * 0.06), int(width * 0.92)
        y0, y1 = int(height * 0.05), int(height * 0.96)
        chart = image.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)))
        local_cut = max(0, min(chart.width, cut_x - x0))
        context = causal_chart_context_tensor(
            chart,
            cut_x=int(local_cut),
            output_size=CHART_CONTEXT_SIZE,
        ).to(dtype=torch.float32)
    except (OSError, ValueError, TypeError):
        context = torch.zeros((3, *CHART_CONTEXT_SIZE), dtype=torch.float32)
    if len(_CHART_CONTEXT_CACHE) < 4096:
        _CHART_CONTEXT_CACHE[cache_key] = context.detach().clone()
    return context


def _augment_chart_context(context: torch.Tensor) -> torch.Tensor:
    values = context.clone()
    if random.random() < 0.80:
        gamma = random.uniform(0.82, 1.20)
        gain = random.uniform(0.82, 1.18)
        values = torch.clamp(values.pow(gamma) * gain, 0.0, 1.0)
    if random.random() < 0.35:
        values = torch.clamp(values + torch.randn_like(values) * random.uniform(0.002, 0.018), 0.0, 1.0)
    if random.random() < 0.30:
        for _ in range(random.randint(1, 3)):
            y = random.randrange(values.shape[1])
            color = torch.rand((3, 1), dtype=values.dtype) * 0.85
            values[:, y : y + 1, :] = color.unsqueeze(-1)
    return values


class CandlePathDataset(
    Dataset[
        tuple[
            torch.Tensor,
            torch.Tensor,
            torch.Tensor,
            torch.Tensor,
            torch.Tensor,
            torch.Tensor,
            torch.Tensor,
            torch.Tensor,
        ]
    ]
):
    def __init__(self, rows: Sequence[Mapping[str, Any]], *, augment: bool = False) -> None:
        self.rows = list(rows)
        self.augment = bool(augment)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(
        self,
        index: int,
    ) -> tuple[
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
    ]:
        row = self.rows[index]
        sequence = torch.tensor(row["sequence"], dtype=torch.float32)
        context = _chart_context_for_row(row)
        if self.augment:
            context = _augment_chart_context(context)
            if random.random() < 0.45:
                noise = torch.randn_like(sequence) * 0.008
                # Never corrupt the discrete candle direction channel or the
                # padded suffix; perturb only real continuous observations.
                noise[:, FEATURE_SCHEMA.index("direction_value")] = 0.0
                length = int(row.get("length", sequence.shape[0]) or sequence.shape[0])
                noise[length:, :] = 0.0
                sequence = sequence + noise
        return (
            sequence,
            torch.tensor(row["targets"], dtype=torch.float32),
            torch.tensor(row["directions"], dtype=torch.long),
            torch.tensor(int(row["play"]), dtype=torch.long),
            torch.tensor(int(row.get("length", len(row["sequence"]))), dtype=torch.long),
            context,
            torch.tensor(row.get("target_quality", [1.0] * len(row["directions"])), dtype=torch.float32),
            torch.tensor(index, dtype=torch.long),
        )


def _split_rows(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    train = [row for row in rows if str(row.get("split")) == "train"]
    validation = [row for row in rows if str(row.get("split")) in {"val", "valid", "validation"}]
    test = [row for row in rows if str(row.get("split")) == "test"]
    return train, validation, test


def _balanced_accuracy(y_true: Sequence[int], y_pred: Sequence[int]) -> float:
    recalls: list[float] = []
    for label in (0, 1):
        indices = [index for index, value in enumerate(y_true) if value == label]
        if indices:
            recalls.append(sum(int(y_pred[index] == label) for index in indices) / len(indices))
    return sum(recalls) / len(recalls) if recalls else 0.0


def _multiclass_balanced_accuracy(y_true: Sequence[int], y_pred: Sequence[int], labels: int) -> float:
    recalls: list[float] = []
    for label in range(labels):
        indices = [index for index, value in enumerate(y_true) if value == label]
        if indices:
            recalls.append(sum(int(y_pred[index] == label) for index in indices) / len(indices))
    return sum(recalls) / len(recalls) if recalls else 0.0


def _continuous_targets(targets: torch.Tensor) -> torch.Tensor:
    return torch.stack([targets[:, :, index] for index in PREDICTION_FEATURE_INDICES], dim=-1)


def _class_weights(rows: Sequence[Mapping[str, Any]]) -> torch.Tensor:
    counts = [1, 1]
    for row in rows:
        for label in cast(Sequence[int], row.get("directions", [])):
            counts[int(label)] += 1
    total = float(sum(counts))
    return torch.tensor([total / (2.0 * count) for count in counts], dtype=torch.float32)


def _play_class_weights(rows: Sequence[Mapping[str, Any]]) -> torch.Tensor:
    counts = [1 for _ in PLAY_LABELS]
    for row in rows:
        counts[int(row.get("play", PLAY_TO_INDEX["PULLBACK"]))] += 1
    total = float(sum(counts))
    raw = [math.sqrt(total / (len(PLAY_LABELS) * count)) for count in counts]
    mean_weight = sum(raw) / len(raw)
    return torch.tensor([weight / mean_weight for weight in raw], dtype=torch.float32)


def _loss(
    outputs: Mapping[str, torch.Tensor],
    targets: torch.Tensor,
    directions: torch.Tensor,
    plays: torch.Tensor,
    *,
    class_weights: torch.Tensor,
    play_class_weights: torch.Tensor,
    target_quality: torch.Tensor,
) -> torch.Tensor:
    direction_logits = outputs["direction_logits"]
    decision_logits = outputs.get("decision_logits", direction_logits)
    feature_mean = outputs["feature_mean"]
    feature_scale = outputs["feature_scale"]
    quality = torch.clamp(target_quality.to(direction_logits.device), 0.15, 1.0)

    # Natural-prior CE produces calibratable probabilities. The independent
    # decision head receives balancing, so minority recall cannot distort the
    # probability head used for abstention and risk reporting.
    natural_ce = F.cross_entropy(
        direction_logits.reshape(-1, 2),
        directions.reshape(-1),
        reduction="none",
    ).reshape(directions.shape)
    balanced_ce = F.cross_entropy(
        decision_logits.reshape(-1, 2),
        directions.reshape(-1),
        weight=class_weights.to(direction_logits.device),
        reduction="none",
    ).reshape(directions.shape)
    probabilities = torch.softmax(direction_logits, dim=-1)
    one_hot = F.one_hot(directions, num_classes=2).to(dtype=probabilities.dtype)
    brier = torch.mean(torch.sum((probabilities - one_hot) ** 2, dim=-1) * quality)

    continuous = _continuous_targets(targets)
    residual = (continuous - feature_mean) / feature_scale
    # Heavy-tailed Student-t objective is robust to screenshot compression and
    # imperfect wick segmentation; unlike Gaussian NLL it does not let a few
    # parser outliers dominate all twelve horizons.
    student_nll = torch.log(feature_scale) + 2.0 * torch.log1p(residual.square() / 3.0)
    student_nll = torch.mean(student_nll * quality.unsqueeze(-1))
    point_huber = F.smooth_l1_loss(feature_mean, continuous, reduction="none")
    point_huber = torch.mean(point_huber * quality.unsqueeze(-1))

    predicted_deltas = feature_mean[:, :, 4]
    target_deltas = continuous[:, :, 4]
    cumulative_huber = F.smooth_l1_loss(
        torch.cumsum(predicted_deltas, dim=1),
        torch.cumsum(target_deltas, dim=1),
    )
    endpoint_target = (torch.sum(target_deltas, dim=1) >= 0.0).to(dtype=predicted_deltas.dtype)
    endpoint_loss = F.binary_cross_entropy_with_logits(
        4.0 * torch.sum(predicted_deltas, dim=1),
        endpoint_target,
    )
    probability_direction = probabilities[:, :, 0] - probabilities[:, :, 1]
    direction_delta_consistency = F.smooth_l1_loss(predicted_deltas, probability_direction)
    play_loss = F.cross_entropy(outputs["play_logits"], plays, weight=play_class_weights.to(plays.device))
    return (
        torch.mean(natural_ce * quality)
        + 0.55 * torch.mean(balanced_ce * quality)
        + 0.12 * brier
        + 0.34 * student_nll
        + 0.28 * point_huber
        + 0.32 * cumulative_huber
        + 0.16 * endpoint_loss
        + 0.05 * direction_delta_consistency
        + 0.03 * play_loss
        + 0.004 * torch.mean(feature_scale)
    )


def evaluate(
    model: Any,
    rows: Sequence[Mapping[str, Any]],
    batch_size: int,
    *,
    return_details: bool = False,
) -> dict[str, Any]:
    if not rows:
        return {
            "direction_accuracy": 0.0,
            "balanced_accuracy": 0.0,
            "path_delta_mae": 1.0,
            "interval_90_coverage": 0.0,
            "play_accuracy": 0.0,
            "play_balanced_accuracy": 0.0,
            "play_majority_baseline_accuracy": 0.0,
            "calibration_error": 1.0,
            "persistence_baseline_accuracy": 0.0,
            "persistence_baseline_balanced_accuracy": 0.0,
            "horizon_direction_accuracy": {},
            "confusion_matrix": [[0, 0], [0, 0]],
        }
    true_all: list[int] = []
    pred_all: list[int] = []
    natural_pred_all: list[int] = []
    natural_logits_all: list[list[float]] = []
    natural_probabilities_all: list[list[float]] = []
    decision_probabilities_all: list[list[float]] = []
    source_ids_all: list[str] = []
    per_step_true: list[list[int]] = []
    per_step_pred: list[list[int]] = []
    play_true: list[int] = []
    play_pred: list[int] = []
    persistence_correct = 0
    persistence_total = 0
    persistence_predictions: list[int] = []
    delta_error = 0.0
    delta_count = 0
    covered = 0
    endpoint_correct = 0
    endpoint_total = 0
    confusion = [[0, 0], [0, 0]]
    natural_confusion = [[0, 0], [0, 0]]
    window_embeddings: list[list[float]] = []
    window_sources: list[str] = []
    window_directions: list[list[int]] = []
    window_deltas: list[list[float]] = []
    window_indices: list[int] = []
    model.eval()
    dataset = CandlePathDataset(rows)
    with torch.inference_mode():
        for sequence, targets, directions, plays, lengths, chart_context, _target_quality, row_indices in DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=False,
        ):
            outputs = cast(
                Mapping[str, torch.Tensor],
                model(
                    sequence,
                    horizon_steps=directions.shape[1],
                    lengths=lengths,
                    chart_context=chart_context,
                ),
            )
            natural_probabilities = torch.softmax(outputs["direction_logits"], dim=-1)
            decision_probabilities = torch.softmax(
                outputs.get("decision_logits", outputs["direction_logits"]),
                dim=-1,
            )
            natural_predictions = torch.argmax(natural_probabilities, dim=-1)
            predictions = torch.argmax(decision_probabilities, dim=-1)
            play_predictions = torch.argmax(outputs["play_logits"], dim=-1)
            if not per_step_true:
                per_step_true = [[] for _ in range(directions.shape[1])]
                per_step_pred = [[] for _ in range(directions.shape[1])]
            for step in range(directions.shape[1]):
                per_step_true[step].extend(int(value) for value in cast(list[int], _tensor_list(directions[:, step])))
                per_step_pred[step].extend(int(value) for value in cast(list[int], _tensor_list(predictions[:, step])))
            direction_values = cast(list[list[int]], _tensor_list(directions))
            prediction_values = cast(list[list[int]], _tensor_list(predictions))
            natural_prediction_values = cast(list[list[int]], _tensor_list(natural_predictions))
            natural_probability_values = cast(list[list[list[float]]], _tensor_list(natural_probabilities))
            decision_probability_values = cast(list[list[list[float]]], _tensor_list(decision_probabilities))
            logits_values = cast(list[list[list[float]]], _tensor_list(outputs["direction_logits"]))
            batch_row_indices = cast(list[int], _tensor_list(row_indices))
            length_values = cast(list[int], _tensor_list(lengths))
            for batch_offset, (
                true_row,
                pred_row,
                natural_pred_row,
                natural_probability_row,
                decision_probability_row,
                logits_row,
                sequence_row,
            ) in enumerate(
                zip(
                    direction_values,
                    prediction_values,
                    natural_prediction_values,
                    natural_probability_values,
                    decision_probability_values,
                    logits_values,
                    sequence,
                )
            ):
                row_index = int(batch_row_indices[batch_offset])
                source_id = str(rows[row_index].get("source") or "")
                final_observation = max(0, int(length_values[batch_offset]) - 1)
                persistence_side = (
                    0
                    if float(sequence_row[final_observation, FEATURE_SCHEMA.index("direction_value")].item()) >= 0.0
                    else 1
                )
                for true, pred, natural_pred, natural_probability, decision_probability, logit in zip(
                    true_row,
                    pred_row,
                    natural_pred_row,
                    natural_probability_row,
                    decision_probability_row,
                    logits_row,
                ):
                    true_all.append(int(true))
                    pred_all.append(int(pred))
                    natural_pred_all.append(int(natural_pred))
                    confusion[int(true)][int(pred)] += 1
                    natural_confusion[int(true)][int(natural_pred)] += 1
                    natural_logits_all.append([float(value) for value in logit])
                    natural_probabilities_all.append([float(value) for value in natural_probability])
                    decision_probabilities_all.append([float(value) for value in decision_probability])
                    source_ids_all.append(source_id)
                    persistence_correct += int(true == persistence_side)
                    persistence_total += 1
                    persistence_predictions.append(persistence_side)
            continuous = _continuous_targets(targets)
            means = outputs["feature_mean"]
            scales = outputs["feature_scale"]
            delta_error += MAX_PRICE_DELTA * float(torch.sum(torch.abs(means[:, :, 4] - continuous[:, :, 4])).item())
            delta_count += int(continuous[:, :, 4].numel())
            covered += int(torch.sum(torch.abs(continuous - means) <= 1.645 * scales).item())
            predicted_endpoint = torch.sum(means[:, :, 4], dim=1) >= 0.0
            actual_endpoint = torch.sum(continuous[:, :, 4], dim=1) >= 0.0
            endpoint_correct += int(torch.sum(predicted_endpoint == actual_endpoint).item())
            endpoint_total += int(predicted_endpoint.numel())
            play_true.extend(int(value) for value in cast(list[int], _tensor_list(plays)))
            play_pred.extend(int(value) for value in cast(list[int], _tensor_list(play_predictions)))
            window_embeddings.extend(
                [list(map(float, row)) for row in cast(list[list[float]], _tensor_list(outputs["context_embedding"]))]
            )
            window_sources.extend(str(rows[int(index)].get("source") or "") for index in batch_row_indices)
            window_directions.extend(direction_values)
            window_deltas.extend(
                [list(map(float, row)) for row in cast(list[list[float]], _tensor_list(continuous[:, :, 4]))]
            )
            window_indices.extend(int(index) for index in batch_row_indices)
    horizon_accuracy = {
        str(step + 1): round(sum(int(a == b) for a, b in zip(truth, predicted)) / max(1, len(truth)), 4)
        for step, (truth, predicted) in enumerate(zip(per_step_true, per_step_pred))
    }
    majority_play = max(set(play_true), key=play_true.count) if play_true else PLAY_TO_INDEX["PULLBACK"]
    class_precision: dict[str, float] = {}
    for label, side in INDEX_TO_SIDE.items():
        predicted_count = sum(confusion[truth][label] for truth in (0, 1))
        class_precision[side] = round(confusion[label][label] / predicted_count, 4) if predicted_count else 0.0
    metrics: dict[str, Any] = {
        "direction_accuracy": round(sum(int(a == b) for a, b in zip(true_all, pred_all)) / max(1, len(true_all)), 4),
        "balanced_accuracy": round(_balanced_accuracy(true_all, pred_all), 4),
        "natural_direction_accuracy": round(sum(int(a == b) for a, b in zip(true_all, natural_pred_all)) / max(1, len(true_all)), 4),
        "natural_balanced_accuracy": round(_balanced_accuracy(true_all, natural_pred_all), 4),
        "predicted_class_precision": class_precision,
        "path_delta_mae": round(delta_error / max(1, delta_count), 6),
        "endpoint_path_direction_accuracy": round(endpoint_correct / max(1, endpoint_total), 4),
        "interval_90_coverage": round(covered / max(1, delta_count * len(PREDICTION_SCHEMA)), 4),
        "play_accuracy": round(sum(int(a == b) for a, b in zip(play_true, play_pred)) / max(1, len(play_true)), 4),
        "play_balanced_accuracy": round(_multiclass_balanced_accuracy(play_true, play_pred, len(PLAY_LABELS)), 4),
        "play_majority_baseline_accuracy": round(sum(int(value == majority_play) for value in play_true) / max(1, len(play_true)), 4),
        "calibration": calibration_metrics(natural_probabilities_all, true_all),
        "calibration_error": round(
            sum(calibration_metrics(natural_probabilities_all, true_all)["classwise_ece"].values()) / 2.0,
            4,
        ),
        "persistence_baseline_accuracy": round(persistence_correct / max(1, persistence_total), 4),
        "persistence_baseline_balanced_accuracy": round(_balanced_accuracy(true_all, persistence_predictions), 4),
        "horizon_direction_accuracy": horizon_accuracy,
        "confusion_matrix": confusion,
        "natural_confusion_matrix": natural_confusion,
        "source_cluster_accuracy_95": source_cluster_accuracy_interval(
            true_all,
            pred_all,
            source_ids_all,
            samples=500,
            seed=42,
        ),
    }
    if return_details:
        metrics["_details"] = {
            "labels": true_all,
            "decisions": pred_all,
            "natural_logits": natural_logits_all,
            "natural_probabilities": natural_probabilities_all,
            "decision_probabilities": decision_probabilities_all,
            "source_ids": source_ids_all,
            "window_embeddings": window_embeddings,
            "window_sources": window_sources,
            "window_directions": window_directions,
            "window_deltas": window_deltas,
            "window_indices": window_indices,
        }
    return metrics


def _without_details(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "_details"}


def _build_train_retrieval_bank(
    train_rows: Sequence[Mapping[str, Any]],
    details: Mapping[str, Any],
) -> dict[str, Any]:
    indices = [int(value) for value in cast(Sequence[Any], details.get("window_indices", []))]
    directions = [
        [INDEX_TO_SIDE[int(label)] for label in row]
        for row in cast(Sequence[Sequence[int]], details.get("window_directions", []))
    ]
    return build_retrieval_bank_v3(
        details.get("window_embeddings", []),
        details.get("window_sources", []),
        directions,
        details.get("window_deltas", []),
        split_labels=["train"] * len(indices),
        entry_ids=[str(train_rows[index].get("window_id") or f"train-{index}") for index in indices],
    )


def _blend_with_retrieval(
    details: Mapping[str, Any],
    bank: Mapping[str, Any],
    *,
    temperature: float,
    top_k: int,
    alpha: float,
) -> dict[str, Any]:
    labels = [int(value) for value in cast(Sequence[Any], details.get("labels", []))]
    model_probabilities = temperature_softmax(
        cast(Sequence[Sequence[float]], details.get("natural_logits", [])),
        temperature,
    )
    decision_probabilities = [
        [float(value) for value in row]
        for row in cast(Sequence[Sequence[float]], details.get("decision_probabilities", []))
    ]
    embeddings = details.get("window_embeddings", [])
    retrieved = retrieve_forecast_v3(
        bank,
        embeddings,
        top_k=max(1, int(top_k)),
        minimum_similarity=0.05,
        similarity_power=2.0,
    )
    retrieval_probabilities: list[list[float]] = []
    retrieval_confidences: list[float] = []
    for forecast in retrieved:
        for horizon in cast(Sequence[Mapping[str, Any]], forecast.get("horizons", [])):
            probability_map = cast(Mapping[str, Any], horizon.get("probabilities", {}))
            retrieval_probabilities.append(
                [float(probability_map.get("BUY", 0.5)), float(probability_map.get("SELL", 0.5))]
            )
            retrieval_confidences.append(float(horizon.get("effective_confidence", 0.0) or 0.0))
    blend = max(0.0, min(0.75, float(alpha)))
    probabilities: list[list[float]] = []
    decisions: list[int] = []
    for index, model_row in enumerate(model_probabilities):
        retrieval_row = retrieval_probabilities[index] if index < len(retrieval_probabilities) else [0.5, 0.5]
        support = retrieval_confidences[index] if index < len(retrieval_confidences) else 0.0
        effective_alpha = blend * max(0.0, min(1.0, support))
        probability = [
            (1.0 - effective_alpha) * model_row[class_index]
            + effective_alpha * retrieval_row[class_index]
            for class_index in range(2)
        ]
        probabilities.append(probability)
        decision_row = decision_probabilities[index] if index < len(decision_probabilities) else model_row
        combined_decision = [
            (1.0 - effective_alpha) * decision_row[class_index]
            + effective_alpha * retrieval_row[class_index]
            for class_index in range(2)
        ]
        decisions.append(0 if combined_decision[0] >= combined_decision[1] else 1)
    return {
        "labels": labels,
        "probabilities": probabilities,
        "decisions": decisions,
        "source_ids": [str(value) for value in cast(Sequence[Any], details.get("source_ids", []))],
        "retrieval_forecasts": retrieved,
    }


def _choose_retrieval_settings(
    validation_details: Mapping[str, Any],
    bank: Mapping[str, Any],
    *,
    temperature: float,
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for top_k in (3, 5, 8, 12):
        for alpha in (0.0, 0.15, 0.30, 0.45, 0.60):
            blended = _blend_with_retrieval(
                validation_details,
                bank,
                temperature=temperature,
                top_k=top_k,
                alpha=alpha,
            )
            labels = cast(Sequence[int], blended["labels"])
            decisions = cast(Sequence[int], blended["decisions"])
            accuracy = sum(int(left == right) for left, right in zip(labels, decisions)) / max(1, len(labels))
            balanced = _balanced_accuracy(labels, decisions)
            candidate = {
                "top_k": top_k,
                "alpha": alpha,
                "validation_accuracy": round(accuracy, 6),
                "validation_balanced_accuracy": round(balanced, 6),
                "score": balanced + 0.05 * accuracy,
            }
            if best is None or (candidate["score"], -alpha, -top_k) > (
                best["score"],
                -float(best["alpha"]),
                -int(best["top_k"]),
            ):
                best = candidate
    return best or {"top_k": 1, "alpha": 0.0, "validation_accuracy": 0.0, "validation_balanced_accuracy": 0.0, "score": 0.0}


def _calibrate_and_select(
    validation_details: Mapping[str, Any],
    test_details: Mapping[str, Any],
    bank: Mapping[str, Any],
    *,
    target_precision: float,
    minimum_predictions: int,
) -> dict[str, Any]:
    temperature = fit_temperature(
        cast(Sequence[Sequence[float]], validation_details.get("natural_logits", [])),
        cast(Sequence[int], validation_details.get("labels", [])),
    )
    retrieval = _choose_retrieval_settings(validation_details, bank, temperature=temperature)
    validation = _blend_with_retrieval(
        validation_details,
        bank,
        temperature=temperature,
        top_k=int(retrieval["top_k"]),
        alpha=float(retrieval["alpha"]),
    )
    test = _blend_with_retrieval(
        test_details,
        bank,
        temperature=temperature,
        top_k=int(retrieval["top_k"]),
        alpha=float(retrieval["alpha"]),
    )
    selection = choose_class_conditional_thresholds(
        cast(Sequence[Sequence[float]], validation["probabilities"]),
        cast(Sequence[int], validation["labels"]),
        cast(Sequence[int], validation["decisions"]),
        target_precision=target_precision,
        minimum_predictions=minimum_predictions,
    )
    thresholds = cast(Mapping[str, Any], selection["thresholds"])
    test_selection = evaluate_class_conditional_selection(
        cast(Sequence[Sequence[float]], test["probabilities"]),
        cast(Sequence[int], test["labels"]),
        cast(Sequence[int], test["decisions"]),
        thresholds,
    )
    selected_mask: list[bool] = []
    for probabilities, decision in zip(
        cast(Sequence[Sequence[float]], test["probabilities"]),
        cast(Sequence[int], test["decisions"]),
    ):
        side = INDEX_TO_SIDE[int(decision)]
        selected_mask.append(float(probabilities[int(decision)]) >= float(thresholds.get(side, 1.01)))
    cluster_interval = source_cluster_accuracy_interval(
        cast(Sequence[int], test["labels"]),
        cast(Sequence[int], test["decisions"]),
        cast(Sequence[str], test["source_ids"]),
        selected=selected_mask,
        samples=1000,
        seed=1729,
    )
    return {
        "temperature": round(float(temperature), 6),
        "retrieval": {key: value for key, value in retrieval.items() if key != "score"},
        "thresholds": dict(thresholds),
        "validation_selection": selection,
        "test_selection": test_selection,
        "test_selected_source_cluster_accuracy_95": cluster_interval,
        "validation_calibration": calibration_metrics(
            cast(Sequence[Sequence[float]], validation["probabilities"]),
            cast(Sequence[int], validation["labels"]),
        ),
        "test_calibration": calibration_metrics(
            cast(Sequence[Sequence[float]], test["probabilities"]),
            cast(Sequence[int], test["labels"]),
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upgrade the PhoenixGuard V3 computer-vision LSTM path model from raw 808 Memory suites.")
    parser.add_argument("--raw-memory-root", type=Path, default=DEFAULT_RAW_MEMORY_ROOT)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--sequence-cache", type=Path, default=DEFAULT_SEQUENCE_CACHE)
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--sequence-length", type=int, default=DEFAULT_SEQUENCE_LENGTH)
    parser.add_argument("--horizon-steps", type=int, default=DEFAULT_HORIZON_STEPS)
    parser.add_argument("--minimum-history", type=int, default=16)
    parser.add_argument("--windows-per-source", type=int, default=8)
    parser.add_argument("--minimum-source-confidence", type=float, default=0.45)
    parser.add_argument("--maximum-clipped-delta-rate", type=float, default=0.10)
    parser.add_argument("--hidden-dim", type=int, default=96)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--epochs", type=int, default=28)
    parser.add_argument("--patience", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=8e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--target-selective-precision", type=float, default=0.85)
    parser.add_argument("--minimum-selective-predictions", type=int, default=20)
    parser.add_argument("--checkpoint-path", type=Path, default=DEFAULT_TRAINING_CHECKPOINT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--metrics-path", type=Path, default=DEFAULT_METRICS_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    random.seed(int(args.seed))
    np.random.seed(int(args.seed))
    cast(Callable[[int], Any], getattr(torch, "manual_seed"))(int(args.seed))
    torch.set_num_threads(max(1, int(args.torch_threads)))
    started = time.time()
    sequences = _raw_suite_sequences(
        args.raw_memory_root,
        split_manifest_path=args.split_manifest,
        cache_path=args.sequence_cache,
        rebuild_cache=bool(args.rebuild_cache),
    )
    print(
        json.dumps(
            {
                "stage": "raw_sequences_ready",
                "source_images": len(sequences),
                "feature_events": sum(len(cast(Sequence[Any], row.get("features", []))) for row in sequences),
            }
        ),
        flush=True,
    )
    windows = _causal_windows(
        sequences,
        sequence_length=int(args.sequence_length),
        horizon_steps=int(args.horizon_steps),
        minimum_history=int(args.minimum_history),
        windows_per_source=int(args.windows_per_source),
        minimum_source_confidence=float(args.minimum_source_confidence),
        maximum_clipped_delta_rate=float(args.maximum_clipped_delta_rate),
    )
    print(json.dumps({"stage": "causal_windows_ready", "windows": len(windows)}), flush=True)
    train_rows, validation_rows, test_rows = _split_rows(windows)
    usable_source_paths = {str(row.get("source") or "") for row in windows}
    usable_sequences = [
        row
        for row in sequences
        if str(row.get("source_path") or row.get("source") or "") in usable_source_paths
    ]
    source_counts = {
        split: len({str(row.get("source")) for row in subset})
        for split, subset in (("train", train_rows), ("validation", validation_rows), ("test", test_rows))
    }
    if not train_rows or not validation_rows or not test_rows:
        print(json.dumps({"ok": False, "error": "source_grouped_train_val_test_rows_required", "windows": len(windows), "sources": source_counts}, indent=2))
        return 2

    print(
        json.dumps(
            {
                "stage": "source_splits_ready",
                "sources": source_counts,
                "training_windows": len(train_rows),
                "validation_windows": len(validation_rows),
                "test_windows": len(test_rows),
            }
        ),
        flush=True,
    )
    model = create_lstm_candle_sequence_model(
        input_dim=len(FEATURE_SCHEMA),
        hidden_dim=int(args.hidden_dim),
        num_layers=int(args.num_layers),
        dropout=float(args.dropout),
        horizon_steps=int(args.horizon_steps),
    )
    print(json.dumps({"stage": "model_ready", "parameters": sum(parameter.numel() for parameter in model.parameters())}), flush=True)
    optimizer: torch.optim.Optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.lr), weight_decay=1e-3)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.55, patience=3, min_lr=1e-5)
    train_loader = DataLoader(
        CandlePathDataset(train_rows, augment=True),
        batch_size=int(args.batch_size),
        shuffle=True,
    )
    class_weights = _class_weights(train_rows)
    play_class_weights = _play_class_weights(train_rows)
    history: list[dict[str, Any]] = []
    best_state: dict[str, Any] | None = None
    best_score = -1e9
    stale_epochs = 0
    start_epoch = 1
    if bool(args.resume) and args.checkpoint_path.exists():
        checkpoint = cast(Mapping[str, Any], torch.load(args.checkpoint_path, map_location="cpu", weights_only=False))
        model.load_state_dict(cast(Mapping[str, Any], checkpoint["model_state"]))
        optimizer.load_state_dict(cast(dict[str, Any], checkpoint["optimizer_state"]))
        scheduler.load_state_dict(cast(dict[str, Any], checkpoint["scheduler_state"]))
        stored_best = checkpoint.get("best_state")
        best_state = dict(cast(Mapping[str, Any], stored_best)) if isinstance(stored_best, Mapping) else None
        best_score = float(checkpoint.get("best_score", best_score))
        stale_epochs = int(checkpoint.get("stale_epochs", 0))
        history = [dict(cast(Mapping[str, Any], row)) for row in cast(Sequence[Any], checkpoint.get("history", [])) if isinstance(row, Mapping)]
        start_epoch = int(checkpoint.get("epoch", 0)) + 1
        print(json.dumps({"stage": "training_resumed", "start_epoch": start_epoch, "best_score": best_score}), flush=True)
    for epoch in range(start_epoch, int(args.epochs) + 1):
        model.train()
        total_loss = 0.0
        batches = 0
        for sequence, targets, directions, plays, lengths, chart_context, target_quality, _row_indices in train_loader:
            optimizer.zero_grad(set_to_none=True)
            outputs = cast(
                Mapping[str, torch.Tensor],
                model(
                    sequence,
                    horizon_steps=int(args.horizon_steps),
                    lengths=lengths,
                    chart_context=chart_context,
                ),
            )
            loss = _loss(
                outputs,
                targets,
                directions,
                plays,
                class_weights=class_weights,
                play_class_weights=play_class_weights,
                target_quality=target_quality,
            )
            cast(Callable[[], Any], getattr(loss, "backward"))()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            cast(Callable[[], Any], getattr(optimizer, "step"))()
            total_loss += float(loss.item())
            batches += 1
        validation = evaluate(model, validation_rows, int(args.batch_size))
        far_accuracy = float(_mapping(validation.get("horizon_direction_accuracy")).get(str(args.horizon_steps), 0.0))
        score = (
            float(validation["balanced_accuracy"])
            + 0.25 * far_accuracy
            + 0.30 * float(validation["endpoint_path_direction_accuracy"])
            - 0.20 * float(validation["path_delta_mae"])
        )
        cast(Callable[[float], None], getattr(scheduler, "step"))(score)
        improved = score > best_score + 1e-5
        if improved:
            best_score = score
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            stale_epochs = 0
        else:
            stale_epochs += 1
        epoch_row = {
            "epoch": epoch,
            "loss": round(total_loss / max(1, batches), 6),
            "decoder_mode": "DIRECT_HORIZON_QUERIES",
            "learning_rate": round(float(optimizer.param_groups[0]["lr"]), 8),
            **validation,
        }
        history.append(epoch_row)
        args.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "schema_version": "PG_LSTM_CANDLE_PATH_TRAINING_CHECKPOINT_V3",
                "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
                "epoch": epoch,
                "model_state": model.state_dict(),
                "optimizer_state": optimizer.state_dict(),
                "scheduler_state": scheduler.state_dict(),
                "best_state": best_state,
                "best_score": best_score,
                "stale_epochs": stale_epochs,
                "history": history,
            },
            args.checkpoint_path,
        )
        print(json.dumps({"stage": "training", **epoch_row}), flush=True)
        if stale_epochs >= int(args.patience):
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    train_evaluation = evaluate(model, train_rows, int(args.batch_size), return_details=True)
    validation_evaluation = evaluate(model, validation_rows, int(args.batch_size), return_details=True)
    test_evaluation = evaluate(model, test_rows, int(args.batch_size), return_details=True)
    train_details = _mapping(train_evaluation.get("_details"))
    validation_details = _mapping(validation_evaluation.get("_details"))
    test_details = _mapping(test_evaluation.get("_details"))
    retrieval_bank = _build_train_retrieval_bank(train_rows, train_details)
    risk_control = _calibrate_and_select(
        validation_details,
        test_details,
        retrieval_bank,
        target_precision=float(args.target_selective_precision),
        minimum_predictions=int(args.minimum_selective_predictions),
    )
    validation_metrics = _without_details(validation_evaluation)
    test_metrics = _without_details(test_evaluation)
    test_selection = _mapping(risk_control.get("test_selection"))
    per_class_selection = _mapping(test_selection.get("per_class"))
    buy_selection = _mapping(per_class_selection.get("BUY"))
    sell_selection = _mapping(per_class_selection.get("SELL"))
    readiness = _production_readiness_evidence(
        source_counts=source_counts,
        test_metrics=test_metrics,
        risk_control=risk_control,
        target_precision=float(args.target_selective_precision),
        minimum_predictions=int(args.minimum_selective_predictions),
    )
    point_estimate_selective_85 = bool(readiness["locked_test_selective_point_pass"])
    a_grade_selective_85 = bool(readiness["locked_test_selective_robust_pass"])
    production_ready = bool(readiness["production_ready"])
    for path in (args.model_path, args.config_path, args.metrics_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "schema_version": "PG_LSTM_CANDLE_PATH_ARTIFACT_V3",
            "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
            "state_dict": model.state_dict(),
            "feature_schema": list(FEATURE_SCHEMA),
            "prediction_schema": list(PREDICTION_SCHEMA),
            "index_to_side": INDEX_TO_SIDE,
            "play_to_index": PLAY_TO_INDEX,
            "retrieval_bank": retrieval_bank,
            "risk_control": risk_control,
        },
        args.model_path,
    )
    config: dict[str, Any] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONFIG_V3",
        "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
        "stack_version": "PHOENIXGUARD_V3",
        "modality": "COMPUTER_VISION",
        "training_source": "RAW_SCREENSHOT_SUITES",
        "architecture": "CAUSAL_PIXEL_CNN_MASKED_LSTM_DIRECT_HORIZON_ATTENTION",
        "visual_frontend": "SHARED_ADAPTIVE_PALETTE_OHLC_PLUS_CAUSAL_RAW_CHART_PIXELS",
        "extractor_schema_version": EXTRACTOR_SCHEMA_VERSION,
        "feature_schema": list(FEATURE_SCHEMA),
        "prediction_schema": list(PREDICTION_SCHEMA),
        "input_dim": len(FEATURE_SCHEMA),
        "sequence_length": int(args.sequence_length),
        "horizon_steps": int(args.horizon_steps),
        "horizon_unit": "CANDLE_EVENTS",
        "clock_time_assumption": "NONE",
        "hidden_dim": int(args.hidden_dim),
        "num_layers": int(args.num_layers),
        "dropout": float(args.dropout),
        "chart_context_size": list(CHART_CONTEXT_SIZE),
        "raw_memory_root": str(args.raw_memory_root),
        "split_manifest_path": str(args.split_manifest),
        "sequence_cache_path": str(args.sequence_cache),
        "training_checkpoint_path": str(args.checkpoint_path),
        "minimum_history": int(args.minimum_history),
        "windows_per_source": int(args.windows_per_source),
        "minimum_source_confidence": float(args.minimum_source_confidence),
        "maximum_clipped_delta_rate": float(args.maximum_clipped_delta_rate),
        "source_images": len(sequences),
        "usable_source_images": len(usable_sequences),
        "training_windows": len(train_rows),
        "validation_windows": len(validation_rows),
        "test_windows": len(test_rows),
        "source_counts": source_counts,
        "probability_temperature": risk_control.get("temperature"),
        "selective_direction_thresholds": risk_control.get("thresholds"),
        "retrieval": risk_control.get("retrieval"),
        "target_selective_precision": float(args.target_selective_precision),
        "point_estimate_selective_85": point_estimate_selective_85,
        "a_grade_selective_85": a_grade_selective_85,
        "production_readiness_checks": readiness["checks"],
        "production_readiness_failures": readiness["failed_checks"],
        "high_frequency_enabled": True,
        "normal_analysis_enabled": True,
        "default_usage": "ALL_ANALYSIS",
        "production_ready": production_ready,
        "artifact_path": str(args.model_path),
    }
    metrics: dict[str, Any] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_METRICS_V3",
        "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
        "stack_version": "PHOENIXGUARD_V3",
        "modality": "COMPUTER_VISION",
        "production_ready": production_ready,
        "point_estimate_selective_85": point_estimate_selective_85,
        "a_grade_selective_85": a_grade_selective_85,
        "production_readiness": readiness,
        "source_images": len(sequences),
        "usable_source_images": len(usable_sequences),
        "source_counts": source_counts,
        "training_windows": len(train_rows),
        "validation_windows": len(validation_rows),
        "test_windows": len(test_rows),
        "validation": validation_metrics,
        "test": test_metrics,
        "risk_control": risk_control,
        "selective_target_precision": float(args.target_selective_precision),
        "test_selective_accuracy": test_selection.get("accuracy"),
        "test_selective_coverage": test_selection.get("coverage"),
        "test_selective_macro_precision": test_selection.get("macro_predicted_class_precision"),
        "test_selective_buy_precision": buy_selection.get("precision"),
        "test_selective_sell_precision": sell_selection.get("precision"),
        "test_selective_source_cluster_accuracy_95": risk_control.get("test_selected_source_cluster_accuracy_95"),
        **{f"test_{key}": value for key, value in test_metrics.items()},
        "training_seconds": round(time.time() - started, 3),
        "history": history,
    }
    args.config_path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    args.metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "production_ready": production_ready,
                "point_estimate_selective_85": point_estimate_selective_85,
                "a_grade_selective_85": a_grade_selective_85,
                "production_readiness": readiness,
                "model_path": str(args.model_path),
                "config_path": str(args.config_path),
                "metrics_path": str(args.metrics_path),
                "source_counts": source_counts,
                "training_windows": len(train_rows),
                "validation_windows": len(validation_rows),
                "test_windows": len(test_rows),
                "validation": validation_metrics,
                "test": test_metrics,
                "risk_control": risk_control,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
